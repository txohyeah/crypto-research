from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from tech_indicators.chart import build_chart_frame, build_reburn_markers, parse_chart_indicators, render_chart_html
from .config import Settings, load_settings
from .exceptions import DataInsufficientError, DatabaseConnectionError, ReportWriteError, UserInputError
from tech_indicators.indicators import compute_indicators
from .models import StrategyEvaluation
from .report import render_markdown_report
from tech_indicators.strategies import RuleEvaluator, get_strategy


BINANCE_SPOT_BASE_URL = "https://api.binance.com"
SUPPORTED_EXCHANGES = {"binance"}
SUPPORTED_MARKET_TYPES = {"spot"}
SUPPORTED_TIMEFRAMES = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
}


@dataclass(frozen=True)
class BinanceKline:
    open_time_ms: int
    open_: float
    high: float
    low: float
    close: float
    volume_base: float
    close_time_ms: int
    volume_quote: float
    trade_count: int
    taker_buy_base: float
    taker_buy_quote: float

    @classmethod
    def from_payload(cls, row: list[Any]) -> "BinanceKline":
        if len(row) < 11:
            raise UserInputError("Binance kline payload has fewer than 11 fields")
        return cls(
            open_time_ms=int(row[0]),
            open_=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume_base=float(row[5]),
            close_time_ms=int(row[6]),
            volume_quote=float(row[7]),
            trade_count=int(row[8]),
            taker_buy_base=float(row[9]),
            taker_buy_quote=float(row[10]),
        )


class BinanceSpotClient:
    def __init__(self, base_url: str = BINANCE_SPOT_BASE_URL, timeout: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def fetch_klines(
        self,
        *,
        symbol: str,
        interval: str,
        limit: int = 1000,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[BinanceKline]:
        if limit <= 0 or limit > 1000:
            raise UserInputError("Binance kline limit must be between 1 and 1000")
        params: dict[str, object] = {
            "symbol": normalize_crypto_symbol(symbol),
            "interval": normalize_timeframe(interval),
            "limit": limit,
        }
        if start_time_ms is not None:
            params["startTime"] = int(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = int(end_time_ms)
        url = f"{self.base_url}/api/v3/klines?{urlencode(params)}"
        request = Request(url, headers={"User-Agent": "stock-research/0.1"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise DatabaseConnectionError(
                f"Binance kline request failed: {exc}",
                hint="Check network access or configure a reachable Binance base URL",
            ) from exc
        if not isinstance(payload, list):
            raise DatabaseConnectionError(f"Unexpected Binance kline response: {payload!r}")
        return [BinanceKline.from_payload(row) for row in payload if isinstance(row, list)]


class CryptoRepository:
    """sqlite 存储后端（替代原 MySQL/SQLAlchemy 实现，接口语义保持一致）。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._path = settings.sqlite_path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self):
        import sqlite3

        conn = sqlite3.connect(str(self._path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_schema(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS crypto_asset (
              exchange TEXT NOT NULL,
              market_type TEXT NOT NULL,
              symbol TEXT NOT NULL,
              base_asset TEXT NOT NULL,
              quote_asset TEXT NOT NULL,
              display_name TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 1,
              updated_at TEXT NOT NULL DEFAULT (datetime('now')),
              PRIMARY KEY (exchange, market_type, symbol)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS crypto_ohlcv (
              exchange TEXT NOT NULL,
              market_type TEXT NOT NULL,
              symbol TEXT NOT NULL,
              timeframe TEXT NOT NULL,
              open_time_ms INTEGER NOT NULL,
              open_time_utc TEXT NOT NULL,
              close_time_utc TEXT NOT NULL,
              open REAL NOT NULL,
              high REAL NOT NULL,
              low REAL NOT NULL,
              close REAL NOT NULL,
              volume_base REAL NOT NULL,
              volume_quote REAL NOT NULL,
              trade_count INTEGER NULL,
              taker_buy_base REAL NULL,
              taker_buy_quote REAL NULL,
              is_closed INTEGER NOT NULL DEFAULT 1,
              fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
              PRIMARY KEY (exchange, market_type, symbol, timeframe, open_time_ms)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS crypto_signal_snapshot (
              exchange TEXT NOT NULL,
              market_type TEXT NOT NULL,
              symbol TEXT NOT NULL,
              timeframe TEXT NOT NULL,
              signal_name TEXT NOT NULL,
              signal_version TEXT NOT NULL,
              open_time_ms INTEGER NOT NULL,
              open_time_utc TEXT NOT NULL,
              close_time_utc TEXT NOT NULL,
              channel_regime TEXT NULL,
              channel_strength INTEGER NULL,
              action TEXT NULL,
              action_strength INTEGER NULL,
              rating_payload TEXT NOT NULL,
              generated_at TEXT NOT NULL,
              updated_at TEXT NOT NULL DEFAULT (datetime('now')),
              PRIMARY KEY (exchange, market_type, symbol, timeframe, signal_name, signal_version, open_time_ms)
            )
            """,
        ]
        try:
            with self._connect() as conn:
                for statement in statements:
                    conn.execute(statement)
        except Exception as exc:
            raise DatabaseConnectionError(str(exc)) from exc

    def upsert_asset(self, *, exchange: str, market_type: str, symbol: str) -> None:
        base_asset, quote_asset = split_symbol(symbol)
        params = {
            "exchange": exchange,
            "market_type": market_type,
            "symbol": symbol,
            "base_asset": base_asset,
            "quote_asset": quote_asset,
            "display_name": f"{base_asset}/{quote_asset}",
        }
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO crypto_asset
                      (exchange, market_type, symbol, base_asset, quote_asset, display_name, active)
                    VALUES
                      (:exchange, :market_type, :symbol, :base_asset, :quote_asset, :display_name, 1)
                    ON CONFLICT(exchange, market_type, symbol) DO UPDATE SET
                      base_asset = excluded.base_asset,
                      quote_asset = excluded.quote_asset,
                      display_name = excluded.display_name,
                      active = 1
                    """,
                    params,
                )
        except Exception as exc:
            raise DatabaseConnectionError(str(exc)) from exc

    def latest_open_time_ms(self, *, exchange: str, market_type: str, symbol: str, timeframe: str) -> int | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT MAX(open_time_ms) AS value
                    FROM crypto_ohlcv
                    WHERE exchange = ?
                      AND market_type = ?
                      AND symbol = ?
                      AND timeframe = ?
                    """,
                    (exchange, market_type, symbol, timeframe),
                ).fetchone()
        except Exception as exc:
            raise DatabaseConnectionError(str(exc)) from exc
        value = row["value"] if row is not None else None
        return int(value) if value is not None else None

    def upsert_klines(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        timeframe: str,
        klines: list[BinanceKline],
        safety_delay_ms: int,
    ) -> int:
        now_ms = utc_now_ms()
        rows = []
        for kline in klines:
            is_closed = kline.close_time_ms <= now_ms - safety_delay_ms
            if not is_closed:
                continue
            rows.append(
                (
                    exchange,
                    market_type,
                    symbol,
                    timeframe,
                    kline.open_time_ms,
                    str(utc_datetime(kline.open_time_ms)),
                    str(utc_datetime(kline.close_time_ms)),
                    kline.open_,
                    kline.high,
                    kline.low,
                    kline.close,
                    kline.volume_base,
                    kline.volume_quote,
                    kline.trade_count,
                    kline.taker_buy_base,
                    kline.taker_buy_quote,
                    1,
                )
            )
        if not rows:
            return 0
        try:
            with self._connect() as conn:
                conn.executemany(
                    """
                    INSERT INTO crypto_ohlcv
                      (exchange, market_type, symbol, timeframe, open_time_ms, open_time_utc, close_time_utc,
                       open, high, low, close, volume_base, volume_quote, trade_count,
                       taker_buy_base, taker_buy_quote, is_closed)
                    VALUES
                      (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(exchange, market_type, symbol, timeframe, open_time_ms) DO UPDATE SET
                      open_time_utc = excluded.open_time_utc,
                      close_time_utc = excluded.close_time_utc,
                      open = excluded.open,
                      high = excluded.high,
                      low = excluded.low,
                      close = excluded.close,
                      volume_base = excluded.volume_base,
                      volume_quote = excluded.volume_quote,
                      trade_count = excluded.trade_count,
                      taker_buy_base = excluded.taker_buy_base,
                      taker_buy_quote = excluded.taker_buy_quote,
                      is_closed = excluded.is_closed
                    """,
                    rows,
                )
        except Exception as exc:
            raise DatabaseConnectionError(str(exc)) from exc
        return len(rows)

    def fetch_ohlcv(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        timeframe: str,
        limit: int,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> pd.DataFrame:
        if limit <= 0:
            raise UserInputError("--lookback-bars must be greater than 0")
        window_clauses = []
        params: list[object] = [exchange, market_type, symbol, timeframe]
        if start_time_ms is not None:
            window_clauses.append("AND open_time_ms >= ?")
            params.append(int(start_time_ms))
        if end_time_ms is not None:
            window_clauses.append("AND open_time_ms <= ?")
            params.append(int(end_time_ms))
        window_sql = "\n                            ".join(window_clauses)
        limit_sql = str(int(limit))
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM (
                      SELECT
                        symbol,
                        timeframe,
                        open_time_ms,
                        open_time_utc,
                        close_time_utc,
                        open,
                        high,
                        low,
                        close,
                        volume_base,
                        volume_quote,
                        trade_count
                      FROM crypto_ohlcv
                      WHERE exchange = ?
                        AND market_type = ?
                        AND symbol = ?
                        AND timeframe = ?
                        AND is_closed = 1
                        {window_sql}
                      ORDER BY open_time_ms DESC
                      LIMIT {limit_sql}
                    ) x
                    ORDER BY open_time_ms ASC
                    """,
                    params,
                ).fetchall()
        except Exception as exc:
            raise DatabaseConnectionError(str(exc)) from exc
        return crypto_rows_to_frame([dict(row) for row in rows])

    def latest_signal_open_time_ms(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        timeframe: str,
        signal_name: str,
        signal_version: str,
    ) -> int | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT MAX(open_time_ms) AS value
                    FROM crypto_signal_snapshot
                    WHERE exchange = ?
                      AND market_type = ?
                      AND symbol = ?
                      AND timeframe = ?
                      AND signal_name = ?
                      AND signal_version = ?
                    """,
                    (exchange, market_type, symbol, timeframe, signal_name, signal_version),
                ).fetchone()
        except Exception as exc:
            raise DatabaseConnectionError(str(exc)) from exc
        value = row["value"] if row is not None else None
        return int(value) if value is not None else None

    def upsert_signal_snapshots(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        params = []
        for row in rows:
            payload = row.get("rating_payload") or {}
            params.append(
                (
                    row["exchange"],
                    row["market_type"],
                    row["symbol"],
                    row["timeframe"],
                    row["signal_name"],
                    row["signal_version"],
                    row["open_time_ms"],
                    str(row["open_time_utc"]),
                    str(row["close_time_utc"]),
                    row.get("channel_regime"),
                    row.get("channel_strength"),
                    row.get("action"),
                    row.get("action_strength"),
                    json.dumps(payload, ensure_ascii=False, default=str),
                    str(row["generated_at"]),
                )
            )
        try:
            with self._connect() as conn:
                conn.executemany(
                    """
                    INSERT INTO crypto_signal_snapshot
                      (exchange, market_type, symbol, timeframe, signal_name, signal_version,
                       open_time_ms, open_time_utc, close_time_utc, channel_regime, channel_strength,
                       action, action_strength, rating_payload, generated_at)
                    VALUES
                      (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(exchange, market_type, symbol, timeframe, signal_name, signal_version, open_time_ms)
                    DO UPDATE SET
                      open_time_utc = excluded.open_time_utc,
                      close_time_utc = excluded.close_time_utc,
                      channel_regime = excluded.channel_regime,
                      channel_strength = excluded.channel_strength,
                      action = excluded.action,
                      action_strength = excluded.action_strength,
                      rating_payload = excluded.rating_payload,
                      generated_at = excluded.generated_at
                    """,
                    params,
                )
        except Exception as exc:
            raise DatabaseConnectionError(str(exc)) from exc
        return len(rows)

    def fetch_signal_snapshots(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        timeframe: str,
        signal_name: str,
        signal_version: str,
        limit: int,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            raise UserInputError("--lookback-bars must be greater than 0")
        window_clauses = []
        params: list[object] = [
            exchange,
            market_type,
            symbol,
            timeframe,
            signal_name,
            signal_version,
        ]
        if start_time_ms is not None:
            window_clauses.append("AND open_time_ms >= ?")
            params.append(int(start_time_ms))
        if end_time_ms is not None:
            window_clauses.append("AND open_time_ms <= ?")
            params.append(int(end_time_ms))
        window_sql = "\n                            ".join(window_clauses)
        limit_sql = str(int(limit))
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM (
                      SELECT
                        exchange,
                        market_type,
                        symbol,
                        timeframe,
                        signal_name,
                        signal_version,
                        open_time_ms,
                        open_time_utc,
                        close_time_utc,
                        channel_regime,
                        channel_strength,
                        action,
                        action_strength,
                        rating_payload,
                        generated_at
                      FROM crypto_signal_snapshot
                      WHERE exchange = ?
                        AND market_type = ?
                        AND symbol = ?
                        AND timeframe = ?
                        AND signal_name = ?
                        AND signal_version = ?
                        {window_sql}
                      ORDER BY open_time_ms DESC
                      LIMIT {limit_sql}
                    ) x
                    ORDER BY open_time_ms ASC
                    """,
                    params,
                ).fetchall()
        except Exception as exc:
            raise DatabaseConnectionError(str(exc)) from exc
        return [_signal_row_to_dict(dict(row)) for row in rows]


def run_crypto_sync(
    *,
    symbol: str,
    timeframe: str,
    exchange: str = "binance",
    market_type: str = "spot",
    lookback_bars: int = 600,
    env_path: str | None = None,
    database: str | None = None,
    base_url: str = BINANCE_SPOT_BASE_URL,
    repository: CryptoRepository | None = None,
    client: BinanceSpotClient | None = None,
) -> dict[str, object]:
    exchange = normalize_exchange(exchange)
    market_type = normalize_market_type(market_type)
    symbol = normalize_crypto_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    if lookback_bars <= 0:
        raise UserInputError("--lookback-bars must be greater than 0")
    if exchange != "binance" or market_type != "spot":
        raise UserInputError("Only Binance spot crypto sync is currently supported")

    repository = repository or CryptoRepository(load_settings(env_path, database))
    client = client or BinanceSpotClient(base_url=base_url)
    repository.ensure_schema()
    repository.upsert_asset(exchange=exchange, market_type=market_type, symbol=symbol)

    interval_ms = SUPPORTED_TIMEFRAMES[timeframe]
    latest = repository.latest_open_time_ms(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
    )
    if latest is None:
        remaining = lookback_bars
        start_time_ms = max(0, utc_now_ms() - remaining * interval_ms)
    else:
        remaining = max(1, lookback_bars)
        start_time_ms = latest + interval_ms

    fetched_count = 0
    stored_count = 0
    cursor = start_time_ms
    while remaining > 0:
        batch_limit = min(1000, remaining)
        klines = client.fetch_klines(
            symbol=symbol,
            interval=timeframe,
            limit=batch_limit,
            start_time_ms=cursor,
        )
        if not klines:
            break
        fetched_count += len(klines)
        stored_count += repository.upsert_klines(
            exchange=exchange,
            market_type=market_type,
            symbol=symbol,
            timeframe=timeframe,
            klines=klines,
            safety_delay_ms=60_000,
        )
        next_cursor = klines[-1].open_time_ms + interval_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        remaining -= len(klines)
        if len(klines) < batch_limit:
            break

    return {
        "ok": True,
        "exchange": exchange,
        "market_type": market_type,
        "symbol": symbol,
        "timeframe": timeframe,
        "requested_bars": lookback_bars,
        "fetched_count": fetched_count,
        "stored_count": stored_count,
        "database": repository.settings.safe_database_label,
    }


def run_crypto_rate(
    *,
    symbol: str,
    timeframe: str,
    exchange: str = "binance",
    market_type: str = "spot",
    lookback_bars: int = 600,
    confirm_timeframe: str | None = None,
    confirm_lookback_bars: int = 600,
    output_path: str | None = None,
    env_path: str | None = None,
    database: str | None = None,
    repository: CryptoRepository | None = None,
) -> dict[str, object]:
    exchange = normalize_exchange(exchange)
    market_type = normalize_market_type(market_type)
    symbol = normalize_crypto_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    confirm_timeframe = normalize_timeframe(confirm_timeframe) if confirm_timeframe else None
    repository = repository or CryptoRepository(load_settings(env_path, database))
    frame = repository.fetch_ohlcv(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        limit=lookback_bars,
    )
    if frame.empty:
        raise DataInsufficientError(
            f"No local crypto OHLCV data for {symbol} {timeframe}",
            hint="Run crypto sync before crypto rate",
        )
    strategy = get_strategy("golden_bull_position_rating")
    min_history = int((strategy.executable_rules or {}).get("min_history_days") or 1)
    if len(frame) < min_history:
        raise DataInsufficientError(
            f"Not enough local crypto OHLCV data for {symbol} {timeframe}: {len(frame)}/{min_history}",
            hint="Run crypto sync with a larger --lookback-bars value before crypto rate",
        )

    indicators = compute_indicators(frame)
    confirmation = None
    if confirm_timeframe:
        confirmation = _crypto_confirmation(
            repository,
            exchange=exchange,
            market_type=market_type,
            symbol=symbol,
            timeframe=confirm_timeframe,
            lookback_bars=confirm_lookback_bars,
        )
        indicators["crypto_confirmation"] = confirmation

    meta = {
        "code": symbol,
        "ts_code": f"{exchange.upper()}:{symbol}:{timeframe}",
        "name": f"{symbol} {timeframe}",
    }
    evaluation = RuleEvaluator().evaluate(strategy, meta, indicators)
    trade_date = str(frame.iloc[-1]["trade_date"])

    if output_path:
        _write_crypto_report(
            output_path,
            strategy_display_name=strategy.display_name,
            trade_date=trade_date,
            evaluation=evaluation,
            confirmation=confirmation,
        )

    return {
        "ok": True,
        "exchange": exchange,
        "market_type": market_type,
        "symbol": symbol,
        "timeframe": timeframe,
        "bar_count": len(frame),
        "latest_open_time_utc": _iso_value(frame.iloc[-1].get("open_time_utc")),
        "latest_close_time_utc": _iso_value(frame.iloc[-1].get("close_time_utc")),
        "rating": _evaluation_payload(evaluation),
        "confirmation": confirmation,
        "output": str(Path(output_path).resolve()) if output_path else None,
    }


def run_crypto_chart(
    *,
    symbol: str,
    timeframe: str,
    exchange: str = "binance",
    market_type: str = "spot",
    lookback_bars: int = 300,
    indicators: str = "ma,golden_bull",
    output_path: str | None = None,
    env_path: str | None = None,
    database: str | None = None,
    repository: CryptoRepository | None = None,
) -> dict[str, object]:
    exchange = normalize_exchange(exchange)
    market_type = normalize_market_type(market_type)
    symbol = normalize_crypto_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    if lookback_bars <= 0:
        raise UserInputError("--lookback-bars must be greater than 0")

    indicator_config = parse_chart_indicators(indicators)
    repository = repository or CryptoRepository(load_settings(env_path, database))
    frame = repository.fetch_ohlcv(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        limit=lookback_bars,
    )
    if frame.empty:
        raise DataInsufficientError(
            f"No local crypto OHLCV data for {symbol} {timeframe}",
            hint="Run crypto sync before crypto chart",
        )

    chart_frame = build_chart_frame(frame, indicator_config)
    latest = chart_frame.iloc[-1]
    start_label = _iso_value(chart_frame.iloc[0].get("open_time_utc")) or str(chart_frame.iloc[0]["trade_date"])
    end_label = _iso_value(latest.get("close_time_utc")) or str(latest["trade_date"])
    meta = {
        "code": f"{symbol} {timeframe}",
        "symbol": symbol,
        "name": f"{exchange} {market_type} UTC",
        "trade_date": end_label,
        "start_date": start_label,
    }
    markers = build_reburn_markers(chart_frame, timeframe_label=timeframe)
    output = Path(output_path) if output_path else _default_crypto_chart_output(symbol, timeframe)
    html = render_chart_html(chart_frame, meta, indicator_config, markers=markers)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(html, encoding="utf-8")
    except OSError as exc:
        raise ReportWriteError(str(exc), payload={"output": str(output)}) from exc

    return {
        "ok": True,
        "command": "crypto chart",
        "exchange": exchange,
        "market_type": market_type,
        "symbol": symbol,
        "timeframe": timeframe,
        "bar_count": int(len(chart_frame)),
        "marker_count": len(markers),
        "indicators": list(indicator_config.requested),
        "latest_open_time_utc": _iso_value(latest.get("open_time_utc")),
        "latest_close_time_utc": _iso_value(latest.get("close_time_utc")),
        "output": str(output.resolve()),
    }


def run_crypto_trade_chart(
    *,
    symbol: str,
    timeframe: str,
    trades_path: str,
    exchange: str = "binance",
    market_type: str = "spot",
    lookback_bars: int = 300,
    start: str | None = None,
    end: str | None = None,
    output_path: str | None = None,
    env_path: str | None = None,
    database: str | None = None,
    repository: CryptoRepository | None = None,
) -> dict[str, object]:
    exchange = normalize_exchange(exchange)
    market_type = normalize_market_type(market_type)
    symbol = normalize_crypto_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    if lookback_bars <= 0:
        raise UserInputError("--lookback-bars must be greater than 0")
    start_time_ms = _parse_utc_ms(start, field_name="--start") if start else None
    end_time_ms = _parse_utc_ms(end, field_name="--end") if end else None
    if start_time_ms is not None and end_time_ms is not None and start_time_ms > end_time_ms:
        raise UserInputError("--start must not be later than --end")

    trades_file = Path(trades_path)
    if not trades_file.is_file():
        raise UserInputError(f"Trade file not found: {trades_file}")

    indicator_config = parse_chart_indicators("ma20,ma60,golden_bull")
    repository = repository or CryptoRepository(load_settings(env_path, database))
    frame = repository.fetch_ohlcv(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        limit=lookback_bars,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
    )
    if frame.empty:
        raise DataInsufficientError(
            f"No local crypto OHLCV data for {symbol} {timeframe}",
            hint="Run crypto sync before crypto trade-chart",
        )

    chart_frame = build_chart_frame(frame, indicator_config)
    markers = _trade_markers_from_jsonl(trades_file, chart_frame)
    markers.extend(_reburn_markers_from_decisions(trades_file, chart_frame))
    kline_details = _kline_details_from_decisions(trades_file, chart_frame)
    _apply_decision_golden_bull_lines(chart_frame, kline_details)
    latest = chart_frame.iloc[-1]
    start_label = _iso_value(chart_frame.iloc[0].get("open_time_utc")) or str(chart_frame.iloc[0]["trade_date"])
    end_label = _iso_value(latest.get("close_time_utc")) or str(latest["trade_date"])
    meta = {
        "code": f"{symbol} {timeframe}",
        "symbol": symbol,
        "name": f"{exchange} {market_type} UTC trades",
        "trade_date": end_label,
        "start_date": start_label,
    }
    output = Path(output_path) if output_path else _default_crypto_trade_chart_output(symbol, timeframe)
    html = render_chart_html(chart_frame, meta, indicator_config, markers=markers, kline_details=kline_details)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(html, encoding="utf-8")
    except OSError as exc:
        raise ReportWriteError(str(exc), payload={"output": str(output)}) from exc

    return {
        "ok": True,
        "command": "crypto trade-chart",
        "exchange": exchange,
        "market_type": market_type,
        "symbol": symbol,
        "timeframe": timeframe,
        "bar_count": int(len(chart_frame)),
        "trade_count": int(len(_read_jsonl_objects(trades_file))),
        "marker_count": int(len(markers)),
        "trades": str(trades_file.resolve()),
        "start": start,
        "end": end,
        "latest_open_time_utc": _iso_value(latest.get("open_time_utc")),
        "latest_close_time_utc": _iso_value(latest.get("close_time_utc")),
        "output": str(output.resolve()),
    }


def crypto_rows_to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    for column in ["open", "high", "low", "close", "volume_base", "volume_quote"]:
        frame[column] = frame[column].map(_float_value)
    frame["trade_date"] = frame["open_time_utc"].map(_bar_label)
    frame["vol"] = frame["volume_base"]
    frame["amount"] = frame["volume_quote"]
    frame["pre_close"] = frame["close"].shift(1)
    frame["pct_chg"] = ((frame["close"] / frame["pre_close"]) - 1.0) * 100.0
    frame.loc[frame["pre_close"].isna(), "pct_chg"] = None
    return frame.reset_index(drop=True)


def _signal_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("rating_payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    elif payload is None:
        payload = {}
    row["rating_payload"] = payload
    return row


def normalize_exchange(value: str) -> str:
    exchange = str(value or "").strip().lower()
    if exchange not in SUPPORTED_EXCHANGES:
        raise UserInputError(f"Unsupported crypto exchange: {value}")
    return exchange


def normalize_market_type(value: str) -> str:
    market_type = str(value or "").strip().lower()
    if market_type not in SUPPORTED_MARKET_TYPES:
        raise UserInputError(f"Unsupported crypto market type: {value}")
    return market_type


def normalize_timeframe(value: str | None) -> str:
    timeframe = str(value or "").strip()
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise UserInputError(f"Unsupported crypto timeframe: {value}")
    return timeframe


def normalize_crypto_symbol(value: str) -> str:
    symbol = str(value or "").strip().replace("/", "").replace("-", "").upper()
    if not symbol or not symbol.isalnum():
        raise UserInputError("--symbol must be a non-empty Binance symbol such as BTCUSDT")
    return symbol


def split_symbol(symbol: str) -> tuple[str, str]:
    symbol = normalize_crypto_symbol(symbol)
    for quote in ("USDT", "USDC", "FDUSD", "BTC", "ETH", "BNB", "TRY", "EUR", "BRL"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)], quote
    return symbol, ""


def utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def utc_datetime(value_ms: int) -> datetime:
    return datetime.fromtimestamp(value_ms / 1000, tz=timezone.utc).replace(tzinfo=None)


def _parse_utc_ms(value: str, *, field_name: str) -> int:
    text = value.strip()
    if not text:
        raise UserInputError(f"{field_name} must not be empty")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise UserInputError(f"{field_name} must be an ISO datetime, e.g. 2025-01-27T00:00:00Z") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def _crypto_confirmation(
    repository: CryptoRepository,
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    timeframe: str,
    lookback_bars: int,
) -> dict[str, object]:
    frame = repository.fetch_ohlcv(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        limit=lookback_bars,
    )
    if frame.empty:
        return {"available": False, "timeframe": timeframe, "reason": "missing_local_ohlcv"}
    indicators = compute_indicators(frame)
    rating = indicators.get("golden_bull_position_rating") if isinstance(indicators, dict) else None
    if not isinstance(rating, dict):
        return {"available": False, "timeframe": timeframe, "reason": "rating_unavailable"}
    return {
        "available": True,
        "timeframe": timeframe,
        "bar_count": len(frame),
        "latest_open_time_utc": _iso_value(frame.iloc[-1].get("open_time_utc")),
        "latest_close_time_utc": _iso_value(frame.iloc[-1].get("close_time_utc")),
        "channel_regime": rating.get("channel_regime"),
        "channel_strength": rating.get("channel_strength"),
        "scenes": rating.get("scenes", []),
        "ratings": rating.get("ratings", []),
    }


def _write_crypto_report(
    output_path: str,
    *,
    strategy_display_name: str,
    trade_date: str,
    evaluation: StrategyEvaluation,
    confirmation: dict[str, object] | None,
) -> None:
    strategy = get_strategy("golden_bull_position_rating")
    content = render_markdown_report(
        strategy=strategy,
        trade_date=trade_date,
        input_count=1,
        invalid_codes=[],
        evaluations=[evaluation],
        missing_data=[],
    )
    lines = [
        content,
        "",
        "## Crypto Context",
        "",
        f"- Asset: {evaluation.code}",
        "- Data source: Binance spot public klines",
        "- Rating uses closed local OHLCV bars only.",
    ]
    if confirmation:
        lines.extend(
            [
                f"- Confirmation timeframe: {confirmation.get('timeframe')}",
                f"- Confirmation available: {confirmation.get('available')}",
                f"- Confirmation regime: {confirmation.get('channel_regime', 'N/A')}",
            ]
        )
    path = Path(output_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError as exc:
        raise ReportWriteError(str(exc)) from exc


def _default_crypto_chart_output(symbol: str, timeframe: str) -> Path:
    return Path("reports") / "crypto_charts" / f"{symbol}_{timeframe}_latest.html"


def _default_crypto_trade_chart_output(symbol: str, timeframe: str) -> Path:
    return Path("reports") / "crypto_charts" / f"{symbol}_{timeframe}_trades.html"


def _trade_markers_from_jsonl(path: Path, frame: pd.DataFrame) -> list[dict[str, Any]]:
    open_times = set(int(value) for value in frame["open_time_ms"].dropna()) if "open_time_ms" in frame else set()
    decision_by_open_time = _decision_context_by_open_time(path)
    markers: list[dict[str, Any]] = []
    for item in _read_jsonl_objects(path):
        side = str(item.get("side") or "").lower()
        if side not in {"buy", "sell"}:
            continue
        open_time_ms = item.get("open_time_ms")
        if open_times and open_time_ms is not None and int(open_time_ms) not in open_times:
            continue
        rating = item.get("rating") if isinstance(item.get("rating"), dict) else {}
        rating_metrics = rating.get("metrics") if isinstance(rating.get("metrics"), dict) else {}
        price = item.get("price", rating_metrics.get("close"))
        if price is None:
            continue
        signal_context = _trade_signal_context(item)
        if not signal_context and open_time_ms is not None:
            signal_context = decision_by_open_time.get(int(open_time_ms), {})
        markers.append(
            {
                "marker_kind": "trade",
                "label": "B" if side == "buy" else "S",
                "time": item.get("time"),
                "open_time_ms": int(open_time_ms) if open_time_ms is not None else None,
                "side": side,
                "action": item.get("action"),
                "price": float(price),
                "qty": item.get("qty"),
                "quote_value": item.get("quote_value"),
                "fee": item.get("fee"),
                "target_position_pct": item.get("target_position_pct"),
                "return_pct_before": item.get("return_pct_before"),
                "equity_before": item.get("equity_before"),
                "cash_before": item.get("cash_before"),
                "base_qty_before": item.get("base_qty_before"),
                "position_pct_before": item.get("position_pct_before"),
                "return_pct_after": item.get("return_pct_after"),
                "equity_after": item.get("equity_after"),
                "cash_after": item.get("cash_after"),
                "base_qty_after": item.get("base_qty_after"),
                "position_pct_after": item.get("position_pct_after"),
                "stop_line_name": item.get("stop_line_name"),
                "stop_line_price": item.get("stop_line_price"),
                "entry_signal_type": item.get("entry_signal_type"),
                "entry_price": item.get("entry_price"),
                "entry_price_before": item.get("entry_price_before"),
                "entry_high_price": item.get("entry_high_price"),
                "entry_high_price_before": item.get("entry_high_price_before"),
                "entry_peak_gain_pct": item.get("entry_peak_gain_pct"),
                "entry_candle_low_price": item.get("entry_candle_low_price"),
                "entry_candle_low_price_before": item.get("entry_candle_low_price_before"),
                "trade_plan": item.get("trade_plan", {}),
                **signal_context,
            }
        )
    return markers


def _reburn_markers_from_decisions(trades_path: Path, frame: pd.DataFrame) -> list[dict[str, Any]]:
    decisions_path = trades_path.with_name("decisions.jsonl")
    if not decisions_path.is_file():
        return []
    open_times = set(int(value) for value in frame["open_time_ms"].dropna()) if "open_time_ms" in frame else set()
    markers: list[dict[str, Any]] = []
    for item in _read_jsonl_objects(decisions_path):
        trade_plan = item.get("trade_plan") if isinstance(item.get("trade_plan"), dict) else {}
        metrics = trade_plan.get("metrics") if isinstance(trade_plan.get("metrics"), dict) else {}
        if trade_plan.get("reburn_signal") is not True and metrics.get("reburn") is not True:
            continue
        open_time_ms = item.get("open_time_ms")
        if open_time_ms is None:
            continue
        key = int(open_time_ms)
        if open_times and key not in open_times:
            continue
        rating = item.get("rating") if isinstance(item.get("rating"), dict) else {}
        rating_metrics = rating.get("metrics") if isinstance(rating.get("metrics"), dict) else {}
        price = item.get("price", rating_metrics.get("close"))
        if price is None:
            continue
        signal_context = _trade_signal_context(item)
        markers.append(
            {
                "marker_kind": "reburn",
                "label": "R",
                "marker_name": "Reburn low point",
                "time": item.get("time"),
                "open_time_ms": key,
                "side": "reburn",
                "action": item.get("action"),
                "price": float(price),
                "target_position_pct": item.get("target_position_pct"),
                "return_pct_after": item.get("return_pct"),
                "equity_after": item.get("equity"),
                "cash_after": item.get("cash"),
                "base_qty_after": item.get("base_qty"),
                "position_pct_after": item.get("current_position_pct"),
                "trade_plan": trade_plan,
                **signal_context,
            }
        )
    return markers


def _decision_context_by_open_time(trades_path: Path) -> dict[int, dict[str, Any]]:
    decisions_path = trades_path.with_name("decisions.jsonl")
    if not decisions_path.is_file():
        return {}
    result: dict[int, dict[str, Any]] = {}
    for item in _read_jsonl_objects(decisions_path):
        open_time_ms = item.get("open_time_ms")
        if open_time_ms is None:
            continue
        result[int(open_time_ms)] = _trade_signal_context(item)
    return result


def _kline_details_from_decisions(trades_path: Path, frame: pd.DataFrame) -> dict[int, dict[str, Any]]:
    decisions_path = trades_path.with_name("decisions.jsonl")
    if not decisions_path.is_file():
        return {}
    open_times = set(int(value) for value in frame["open_time_ms"].dropna()) if "open_time_ms" in frame else set()
    result: dict[int, dict[str, Any]] = {}
    for item in _read_jsonl_objects(decisions_path):
        open_time_ms = item.get("open_time_ms")
        if open_time_ms is None:
            continue
        key = int(open_time_ms)
        if open_times and key not in open_times:
            continue
        context = _trade_signal_context(item)
        context.update(
            {
                "time": item.get("time"),
                "open_time_ms": key,
                "action": item.get("action"),
                "target_position_pct": item.get("target_position_pct"),
                "current_position_pct": item.get("current_position_pct"),
                "cash": item.get("cash"),
                "base_qty": item.get("base_qty"),
                "position_value": item.get("position_value"),
                "equity": item.get("equity"),
                "return_pct": item.get("return_pct"),
                "phase": item.get("phase"),
                "entry_price": (item.get("risk") or {}).get("entry_price"),
                "entry_high_price": (item.get("risk") or {}).get("entry_high_price"),
                "entry_peak_gain_pct": (item.get("risk") or {}).get("entry_peak_gain_pct"),
                "entry_candle_low_price": (item.get("risk") or {}).get("entry_candle_low_price"),
            }
        )
        result[key] = context
    return result


def _apply_decision_golden_bull_lines(frame: pd.DataFrame, kline_details: dict[int, dict[str, Any]]) -> None:
    if not kline_details or "open_time_ms" not in frame:
        return
    line_columns = ["golden_bull", "golden_bull_trend", "golden_bull_2", "channel_upper", "channel_lower"]
    if not all(column in frame for column in line_columns):
        return
    for idx, row in frame.iterrows():
        open_time_ms = row.get("open_time_ms")
        if pd.isna(open_time_ms):
            continue
        context = kline_details.get(int(open_time_ms))
        if not context:
            continue
        golden_bull = _optional_float(context.get("decision_golden_bull"))
        golden_bull_trend = _optional_float(context.get("decision_golden_bull_trend"))
        golden_bull_2 = _optional_float(context.get("decision_golden_bull_2"))
        if None in (golden_bull, golden_bull_trend, golden_bull_2):
            continue
        upper = _optional_float(context.get("decision_channel_upper"))
        lower = _optional_float(context.get("decision_channel_lower"))
        if upper is None:
            upper = max(golden_bull, golden_bull_trend, golden_bull_2)
        if lower is None:
            lower = min(golden_bull, golden_bull_trend, golden_bull_2)
        frame.loc[idx, "golden_bull"] = golden_bull
        frame.loc[idx, "golden_bull_trend"] = golden_bull_trend
        frame.loc[idx, "golden_bull_2"] = golden_bull_2
        frame.loc[idx, "channel_upper"] = upper
        frame.loc[idx, "channel_lower"] = lower


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _trade_signal_context(item: dict[str, Any]) -> dict[str, Any]:
    rating = item.get("rating") if isinstance(item.get("rating"), dict) else {}
    trade_plan = item.get("trade_plan") if isinstance(item.get("trade_plan"), dict) else {}
    if not rating and "reason" not in item and not trade_plan:
        return {}
    ratings = [entry for entry in rating.get("ratings", []) if isinstance(entry, dict)]
    strongest = max(ratings, key=lambda entry: int(entry.get("strength") or 0), default={})
    metrics = rating.get("metrics", {}) if isinstance(rating.get("metrics"), dict) else {}
    trade_metrics = trade_plan.get("metrics", {}) if isinstance(trade_plan.get("metrics"), dict) else {}
    metrics = {**trade_metrics, **metrics}
    return {
        "signal_action": trade_plan.get("signal_type") or strongest.get("action"),
        "signal_strength": strongest.get("strength"),
        "final_trade_signal": trade_plan.get("signal_type"),
        "final_trade_action": trade_plan.get("action"),
        "final_trade_side": trade_plan.get("side"),
        "final_trade_reason": trade_plan.get("reason", []),
        "trade_candidates": trade_plan.get("candidates", []),
        "golden_bull_ratings": ratings,
        "golden_bull_metrics": metrics,
        "decision_golden_bull": metrics.get("golden_bull"),
        "decision_golden_bull_trend": metrics.get("golden_bull_trend"),
        "decision_golden_bull_2": metrics.get("golden_bull_2"),
        "decision_ma20": metrics.get("ma20"),
        "decision_ma60": metrics.get("ma60"),
        "ma60_slope_pct": metrics.get("ma60_slope_pct"),
        "ma20_ma60_spread_pct": metrics.get("ma20_ma60_spread_pct"),
        "prev_ma20_ma60_spread_pct": metrics.get("prev_ma20_ma60_spread_pct"),
        "ma20_ma60_spread_widening": metrics.get("ma20_ma60_spread_widening"),
        "decision_channel_upper": metrics.get("upper_line"),
        "decision_channel_lower": metrics.get("lower_line"),
        "channel_regime": rating.get("channel_regime"),
        "channel_strength": rating.get("channel_strength"),
        "scenes": rating.get("scenes", []),
        "risk_flags": rating.get("risk_flags", []),
        "invalidations": rating.get("invalidations", []),
        "risk": item.get("risk", {}),
        "reason": item.get("reason", []),
    }


def _read_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise UserInputError(f"Unable to read trade file: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UserInputError(f"Invalid JSONL in trade file at line {line_number}: {exc}") from exc
        if not isinstance(payload, dict):
            raise UserInputError(f"Trade file line {line_number} must be a JSON object")
        rows.append(payload)
    return rows


def _evaluation_payload(item: StrategyEvaluation) -> dict[str, object]:
    return {
        "code": item.code,
        "ts_code": item.ts_code,
        "name": item.name,
        "close": item.close,
        "pct_chg": item.pct_chg,
        "score": item.score,
        "bucket": item.bucket,
        "warnings": item.warnings,
        "golden_bull_position_rating": item.indicators.get("golden_bull_position_rating"),
    }


def _float_value(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    if value is None:
        return math.nan
    return float(value)


def _bar_label(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d%H%M")
    return str(value)


def _iso_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)
