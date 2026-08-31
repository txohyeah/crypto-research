from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any, Callable

import pandas as pd

from .crypto import CryptoRepository, normalize_crypto_symbol, normalize_exchange, normalize_market_type, normalize_timeframe
from .exceptions import DataInsufficientError, UserInputError
from tech_indicators.indicators import compute_indicators


GOLDEN_BULL_SIGNAL_NAME = "golden_bull_position_rating"
GOLDEN_BULL_SIGNAL_VERSION = "golden_bull_position_rating:v5"


@dataclass(frozen=True)
class CryptoSignalConfig:
    exchange: str = "binance"
    market_type: str = "spot"
    symbol: str = "BTCUSDT"
    timeframe: str = "4h"
    signal_name: str = GOLDEN_BULL_SIGNAL_NAME
    signal_version: str = GOLDEN_BULL_SIGNAL_VERSION
    min_history_bars: int = 120

    @classmethod
    def build(
        cls,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        timeframe: str,
        min_history_bars: int,
    ) -> "CryptoSignalConfig":
        if min_history_bars <= 0:
            raise UserInputError("--min-history-bars must be greater than 0")
        return cls(
            exchange=normalize_exchange(exchange),
            market_type=normalize_market_type(market_type),
            symbol=normalize_crypto_symbol(symbol),
            timeframe=normalize_timeframe(timeframe),
            min_history_bars=int(min_history_bars),
        )


def run_crypto_signal_sync(
    *,
    repository: CryptoRepository,
    config: CryptoSignalConfig,
    lookback_bars: int,
    refresh_all: bool = False,
    progress: Callable[[str], None] | None = None,
    progress_every: int = 50,
) -> dict[str, object]:
    if lookback_bars < config.min_history_bars:
        raise UserInputError("--lookback-bars must be greater than or equal to --min-history-bars")
    if progress_every <= 0:
        raise UserInputError("--progress-every must be greater than 0")
    repository.ensure_schema()
    frame = repository.fetch_ohlcv(
        exchange=config.exchange,
        market_type=config.market_type,
        symbol=config.symbol,
        timeframe=config.timeframe,
        limit=lookback_bars,
    )
    if frame.empty:
        raise DataInsufficientError(
            f"No local crypto OHLCV data for {config.symbol} {config.timeframe}",
            hint="Run crypto sync before crypto signal-sync",
        )
    if len(frame) < config.min_history_bars:
        raise DataInsufficientError(
            f"Not enough local crypto OHLCV data for {config.symbol} {config.timeframe}: "
            f"{len(frame)}/{config.min_history_bars}",
            hint="Run crypto sync with a larger --lookback-bars value",
        )

    existing_rows: list[dict[str, Any]] = []
    existing_open_times: set[int] = set()
    if not refresh_all:
        existing_rows = repository.fetch_signal_snapshots(
            exchange=config.exchange,
            market_type=config.market_type,
            symbol=config.symbol,
            timeframe=config.timeframe,
            signal_name=config.signal_name,
            signal_version=config.signal_version,
            limit=lookback_bars,
        )
        existing_open_times = {int(row["open_time_ms"]) for row in existing_rows}

    rows = []
    target_indices = range(config.min_history_bars - 1, len(frame))
    target_count = len(target_indices)
    skipped_count = 0
    started_at = time.monotonic()
    if progress:
        progress(
            f"signal-sync start {config.symbol} {config.timeframe}: "
            f"bars={len(frame)} targets={target_count} refresh_all={refresh_all}"
        )
    for processed_count, idx in enumerate(target_indices, start=1):
        row = frame.iloc[idx]
        open_time_ms = int(row["open_time_ms"])
        if open_time_ms in existing_open_times:
            skipped_count += 1
            if progress and (processed_count == 1 or processed_count % progress_every == 0 or processed_count == target_count):
                progress(
                    _progress_message(
                        config=config,
                        processed_count=processed_count,
                        target_count=target_count,
                        computed_count=len(rows),
                        skipped_count=skipped_count,
                        started_at=started_at,
                    )
                )
            continue
        rows.append(_build_signal_row(frame.iloc[: idx + 1].copy(), config))
        if progress and (processed_count == 1 or processed_count % progress_every == 0 or processed_count == target_count):
            progress(
                _progress_message(
                    config=config,
                    processed_count=processed_count,
                    target_count=target_count,
                    computed_count=len(rows),
                    skipped_count=skipped_count,
                    started_at=started_at,
                )
            )

    stored_count = repository.upsert_signal_snapshots(rows) if rows else 0
    latest = rows[-1] if rows else (existing_rows[-1] if existing_rows else None)
    if progress:
        progress(
            f"signal-sync done {config.symbol} {config.timeframe}: "
            f"computed={len(rows)} skipped={skipped_count} stored={stored_count} "
            f"elapsed={time.monotonic() - started_at:.1f}s"
        )
    return {
        "ok": True,
        "command": "crypto signal-sync",
        "exchange": config.exchange,
        "market_type": config.market_type,
        "symbol": config.symbol,
        "timeframe": config.timeframe,
        "signal_name": config.signal_name,
        "signal_version": config.signal_version,
        "bar_count": len(frame),
        "computed_count": len(rows),
        "skipped_count": skipped_count,
        "stored_count": stored_count,
        "latest_open_time_utc": _iso_value(latest.get("open_time_utc")) if latest else None,
        "database": repository.settings.safe_database_label,
    }


def _iso_value(value: Any) -> str | None:
    """兼容 sqlite(str 时间) 与 datetime 的 ISO 序列化。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    return str(value) + "Z"


def _progress_message(
    *,
    config: CryptoSignalConfig,
    processed_count: int,
    target_count: int,
    computed_count: int,
    skipped_count: int,
    started_at: float,
) -> str:
    elapsed = time.monotonic() - started_at
    rate = processed_count / elapsed if elapsed > 0 else 0.0
    remaining = target_count - processed_count
    eta = remaining / rate if rate > 0 else 0.0
    return (
        f"signal-sync {config.symbol} {config.timeframe}: "
        f"{processed_count}/{target_count} computed={computed_count} skipped={skipped_count} "
        f"elapsed={elapsed:.1f}s eta={eta:.1f}s"
    )


def _build_signal_row(history: pd.DataFrame, config: CryptoSignalConfig) -> dict[str, Any]:
    row = history.iloc[-1]
    indicators = compute_indicators(history)
    rating = indicators.get(config.signal_name)
    rating_payload = rating if isinstance(rating, dict) else {}
    action, action_strength = _primary_action(rating_payload)
    return {
        "exchange": config.exchange,
        "market_type": config.market_type,
        "symbol": config.symbol,
        "timeframe": config.timeframe,
        "signal_name": config.signal_name,
        "signal_version": config.signal_version,
        "open_time_ms": int(row["open_time_ms"]),
        "open_time_utc": row["open_time_utc"],
        "close_time_utc": row["close_time_utc"],
        "channel_regime": rating_payload.get("channel_regime"),
        "channel_strength": _optional_int(rating_payload.get("channel_strength")),
        "action": action,
        "action_strength": action_strength,
        "rating_payload": rating_payload,
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }


def _primary_action(rating: dict[str, Any]) -> tuple[str | None, int | None]:
    ratings = [item for item in rating.get("ratings", []) if isinstance(item, dict)]
    if not ratings:
        return None, None
    best = max(ratings, key=lambda item: int(item.get("strength") or 0))
    return str(best.get("action") or ""), int(best.get("strength") or 0)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
