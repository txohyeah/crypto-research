from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .crypto import (
    CryptoRepository,
    normalize_crypto_symbol,
    normalize_exchange,
    normalize_market_type,
    normalize_timeframe,
)
from .crypto_signals import GOLDEN_BULL_SIGNAL_NAME, GOLDEN_BULL_SIGNAL_VERSION
from .exceptions import DataInsufficientError, ReportWriteError, UserInputError
from .golden_bull_trading import BEAR_TRIAL_TARGET_POSITION_PCT
from tech_indicators.indicators import compute_golden_bull_lines
from .kline_decision import build_unified_kline_trade_plan
from .reburn import (
    REBURN_MA_SLOPE_LOOKBACK_BARS,
    REBURN_RISK_CAP_POSITION_PCT,
    REBURN_STRONG_TARGET_POSITION_PCT,
    REBURN_WEAK_TARGET_POSITION_PCT,
)


DEFAULT_INITIAL_CAPITAL = 5000.0
DEFAULT_PROFIT_RUNNER_TRIGGER_PCT = 0.30
DEFAULT_PROFIT_DRAWDOWN_STOP_PCT = 0.30
DEFAULT_TRADING_STRATEGY = "golden_bull"
MA20_FULL_TRADING_STRATEGY = "ma20_full"
TRADING_STRATEGIES = {DEFAULT_TRADING_STRATEGY, MA20_FULL_TRADING_STRATEGY}
BEAR_MARKET_MAX_POSITION_PCT = BEAR_TRIAL_TARGET_POSITION_PCT
REDUCE_TARGET_POSITION_PCT = 0.50
MIN_REBALANCE_POSITION_DELTA_PCT = 0.05
BULL_REGIME_MATURE_MIN_BARS = 42
BULL_REGIME_BEAR_INTERRUPT_TOLERANCE_BARS = 6
MA20_FULL_WINDOW = 20


@dataclass(frozen=True)
class CryptoTradingConfig:
    exchange: str = "binance"
    market_type: str = "spot"
    symbol: str = "BTCUSDT"
    timeframe: str = "4h"
    initial_capital: float = DEFAULT_INITIAL_CAPITAL
    profit_runner_trigger_pct: float = DEFAULT_PROFIT_RUNNER_TRIGGER_PCT
    profit_drawdown_stop_pct: float = DEFAULT_PROFIT_DRAWDOWN_STOP_PCT
    fee_rate: float = 0.001
    slippage_bps: float = 5.0
    min_history_bars: int = 120
    strategy: str = DEFAULT_TRADING_STRATEGY

    @classmethod
    def build(
        cls,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        timeframe: str,
        initial_capital: float,
        profit_runner_trigger_pct: float,
        profit_drawdown_stop_pct: float,
        fee_rate: float,
        slippage_bps: float,
        min_history_bars: int,
        strategy: str = DEFAULT_TRADING_STRATEGY,
    ) -> "CryptoTradingConfig":
        exchange = normalize_exchange(exchange)
        market_type = normalize_market_type(market_type)
        symbol = normalize_crypto_symbol(symbol)
        timeframe = normalize_timeframe(timeframe)
        if initial_capital <= 0:
            raise UserInputError("--initial-capital must be greater than 0")
        for name, value in {
            "--profit-runner-trigger-pct": profit_runner_trigger_pct,
            "--profit-drawdown-stop-pct": profit_drawdown_stop_pct,
            "--fee-rate": fee_rate,
            "--slippage-bps": slippage_bps,
        }.items():
            if value < 0:
                raise UserInputError(f"{name} must be greater than or equal to 0")
        if min_history_bars <= 0:
            raise UserInputError("--min-history-bars must be greater than 0")
        strategy = str(strategy or DEFAULT_TRADING_STRATEGY).strip()
        if strategy not in TRADING_STRATEGIES:
            choices = ", ".join(sorted(TRADING_STRATEGIES))
            raise UserInputError(f"--strategy must be one of: {choices}")
        return cls(
            exchange=exchange,
            market_type=market_type,
            symbol=symbol,
            timeframe=timeframe,
            initial_capital=float(initial_capital),
            profit_runner_trigger_pct=float(profit_runner_trigger_pct),
            profit_drawdown_stop_pct=float(profit_drawdown_stop_pct),
            fee_rate=float(fee_rate),
            slippage_bps=float(slippage_bps),
            min_history_bars=int(min_history_bars),
            strategy=strategy,
        )


@dataclass
class PositionLot:
    open_time_ms: int
    qty: float
    entry_price: float
    entry_low_price: float | None
    entry_high_price: float | None
    position_pct_before: float
    signal_type: str | None = None

    @classmethod
    def from_dict(cls, item: dict[str, Any]) -> "PositionLot":
        return cls(
            open_time_ms=int(item.get("open_time_ms") or 0),
            qty=float(item.get("qty") or 0.0),
            entry_price=float(item.get("entry_price") or 0.0),
            entry_low_price=_optional_float(item.get("entry_low_price")),
            entry_high_price=_optional_float(item.get("entry_high_price")),
            position_pct_before=float(item.get("position_pct_before") or 0.0),
            signal_type=str(item.get("signal_type")) if item.get("signal_type") else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "open_time_ms": self.open_time_ms,
            "qty": _round(self.qty, 10),
            "entry_price": _round(self.entry_price),
            "entry_low_price": _round(self.entry_low_price),
            "entry_high_price": _round(self.entry_high_price),
            "position_pct_before": _round(self.position_pct_before, 6),
            "signal_type": self.signal_type,
        }


@dataclass
class AccountState:
    cash: float
    base_qty: float
    phase: str = "cash"
    realized_pnl: float = 0.0
    equity_high_watermark: float | None = None
    profit_high_watermark: float | None = None
    last_open_time_ms: int | None = None
    stop_line_name: str | None = None
    stop_line_price: float | None = None
    entry_signal_type: str | None = None
    entry_price: float | None = None
    entry_high_price: float | None = None
    entry_candle_low_price: float | None = None
    position_lots: list[PositionLot] = field(default_factory=list)
    take_profit_reduced: bool = False
    take_profit_reduce_bars_since: int | None = None
    last_take_profit_reduce_open_time_ms: int | None = None

    @classmethod
    def fresh(cls, initial_capital: float) -> "AccountState":
        return cls(cash=float(initial_capital), base_qty=0.0)

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> "AccountState":
        return cls(
            cash=float(snapshot.get("cash") or 0.0),
            base_qty=float(snapshot.get("base_qty") or 0.0),
            phase=str(snapshot.get("phase") or "cash"),
            realized_pnl=float(snapshot.get("realized_pnl") or 0.0),
            equity_high_watermark=_optional_float(snapshot.get("equity_high_watermark")),
            profit_high_watermark=_optional_float(snapshot.get("profit_high_watermark")),
            last_open_time_ms=int(snapshot["open_time_ms"]) if snapshot.get("open_time_ms") is not None else None,
            stop_line_name=str(snapshot.get("stop_line_name")) if snapshot.get("stop_line_name") else None,
            stop_line_price=_optional_float(snapshot.get("stop_line_price")),
            entry_signal_type=str(snapshot.get("entry_signal_type")) if snapshot.get("entry_signal_type") else None,
            entry_price=_optional_float(snapshot.get("entry_price")),
            entry_high_price=_optional_float(snapshot.get("entry_high_price")),
            entry_candle_low_price=_optional_float(snapshot.get("entry_candle_low_price")),
            position_lots=_position_lots_from_snapshot(snapshot),
            take_profit_reduced=bool(snapshot.get("take_profit_reduced")),
            take_profit_reduce_bars_since=_optional_int(snapshot.get("take_profit_reduce_bars_since")),
            last_take_profit_reduce_open_time_ms=(
                int(snapshot["last_take_profit_reduce_open_time_ms"])
                if snapshot.get("last_take_profit_reduce_open_time_ms") is not None
                else None
            ),
        )


def run_crypto_backtest(
    *,
    repository: CryptoRepository,
    config: CryptoTradingConfig,
    lookback_bars: int,
    output_dir: str | None = None,
) -> dict[str, object]:
    frame = _load_trading_frame(repository, config, lookback_bars)
    state = AccountState.fresh(config.initial_capital)
    if config.strategy == MA20_FULL_TRADING_STRATEGY:
        signals = _load_available_signal_map(repository, config, lookback_bars, frame=frame)
        decisions, trades, snapshots = _run_ma20_full_engine(
            frame,
            config,
            state,
            start_index=config.min_history_bars - 1,
            signals=signals,
        )
    else:
        signals = _load_signal_map(repository, config, lookback_bars, frame=frame)
        decisions, trades, snapshots = _run_engine(
            frame,
            config,
            state,
            start_index=config.min_history_bars - 1,
            signals=signals,
        )
    output = _resolve_output_dir(output_dir, config, "backtest")
    _write_run_artifacts(output, config, frame, decisions, trades, snapshots, mode="backtest")
    summary = _summary(config, frame, decisions, trades, snapshots)
    summary["output_dir"] = str(output.resolve())
    summary["command"] = "crypto backtest"
    return summary


def run_crypto_paper_trade(
    *,
    repository: CryptoRepository,
    config: CryptoTradingConfig,
    lookback_bars: int,
    output_dir: str | None = None,
) -> dict[str, object]:
    frame = _load_trading_frame(repository, config, lookback_bars)
    output = _resolve_output_dir(output_dir, config, "paper")
    previous_snapshot = _load_latest_snapshot(output / "account_snapshots.jsonl")
    if previous_snapshot:
        state = AccountState.from_snapshot(previous_snapshot)
        start_index = _next_unprocessed_index(frame, state.last_open_time_ms)
    else:
        state = AccountState.fresh(config.initial_capital)
        start_index = len(frame) - 1
    if start_index is None:
        return {
            "ok": True,
            "command": "crypto paper-trade",
            "exchange": config.exchange,
            "market_type": config.market_type,
            "symbol": config.symbol,
            "timeframe": config.timeframe,
            "processed_bars": 0,
            "latest_snapshot": previous_snapshot,
            "output_dir": str(output.resolve()),
        }

    if config.strategy == MA20_FULL_TRADING_STRATEGY:
        signals = _load_available_signal_map(repository, config, lookback_bars, frame=frame)
        decisions, trades, snapshots = _run_ma20_full_engine(
            frame,
            config,
            state,
            start_index=start_index,
            signals=signals,
        )
    else:
        signals = _load_signal_map(repository, config, lookback_bars, frame=frame)
        decisions, trades, snapshots = _run_engine(frame, config, state, start_index=start_index, signals=signals)
    _append_run_artifacts(output, config, frame, decisions, trades, snapshots, mode="paper")
    latest = snapshots[-1] if snapshots else previous_snapshot
    return {
        "ok": True,
        "command": "crypto paper-trade",
        "exchange": config.exchange,
        "market_type": config.market_type,
        "symbol": config.symbol,
        "timeframe": config.timeframe,
        "processed_bars": len(snapshots),
        "latest_snapshot": latest,
        "output_dir": str(output.resolve()),
    }


def _load_trading_frame(repository: CryptoRepository, config: CryptoTradingConfig, lookback_bars: int) -> pd.DataFrame:
    if lookback_bars < config.min_history_bars:
        raise UserInputError("--lookback-bars must be greater than or equal to --min-history-bars")
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
            hint="Run crypto sync before crypto backtest or paper-trade",
        )
    if len(frame) < config.min_history_bars:
        raise DataInsufficientError(
            f"Not enough local crypto OHLCV data for {config.symbol} {config.timeframe}: "
            f"{len(frame)}/{config.min_history_bars}",
            hint="Run crypto sync with a larger --lookback-bars value",
        )
    return frame.reset_index(drop=True)


def _load_signal_map(
    repository: CryptoRepository,
    config: CryptoTradingConfig,
    lookback_bars: int,
    *,
    frame: pd.DataFrame | None = None,
) -> dict[int, dict[str, Any]]:
    start_time_ms = None
    end_time_ms = None
    if frame is not None and not frame.empty:
        start_time_ms = int(frame.iloc[0]["open_time_ms"])
        end_time_ms = int(frame.iloc[-1]["open_time_ms"])
    rows = repository.fetch_signal_snapshots(
        exchange=config.exchange,
        market_type=config.market_type,
        symbol=config.symbol,
        timeframe=config.timeframe,
        signal_name=GOLDEN_BULL_SIGNAL_NAME,
        signal_version=GOLDEN_BULL_SIGNAL_VERSION,
        limit=lookback_bars,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
    )
    if not rows:
        raise DataInsufficientError(
            f"No local crypto signal snapshots for {config.symbol} {config.timeframe}",
            hint="Run crypto signal-sync before crypto backtest or paper-trade",
        )
    signals = {int(row["open_time_ms"]): row for row in rows}
    if frame is not None and not frame.empty:
        required_open_times = [int(row["open_time_ms"]) for _, row in frame.iloc[config.min_history_bars - 1 :].iterrows()]
        missing = [open_time_ms for open_time_ms in required_open_times if open_time_ms not in signals]
        if missing:
            raise DataInsufficientError(
                f"Missing {len(missing)} crypto signal snapshots for {config.symbol} {config.timeframe}",
                hint=(
                    "Run crypto signal-sync with a lookback window covering the backtest or paper-trade range; "
                    f"first missing open_time_ms={missing[0]}"
                ),
            )
    return signals


def _load_available_signal_map(
    repository: CryptoRepository,
    config: CryptoTradingConfig,
    lookback_bars: int,
    *,
    frame: pd.DataFrame | None = None,
) -> dict[int, dict[str, Any]]:
    start_time_ms = None
    end_time_ms = None
    if frame is not None and not frame.empty:
        start_time_ms = int(frame.iloc[0]["open_time_ms"])
        end_time_ms = int(frame.iloc[-1]["open_time_ms"])
    rows = repository.fetch_signal_snapshots(
        exchange=config.exchange,
        market_type=config.market_type,
        symbol=config.symbol,
        timeframe=config.timeframe,
        signal_name=GOLDEN_BULL_SIGNAL_NAME,
        signal_version=GOLDEN_BULL_SIGNAL_VERSION,
        limit=lookback_bars,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
    )
    return {int(row["open_time_ms"]): row for row in rows}


def _run_engine(
    frame: pd.DataFrame,
    config: CryptoTradingConfig,
    state: AccountState,
    *,
    start_index: int,
    signals: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    decisions: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    start_index = max(config.min_history_bars - 1, start_index)
    bull_regime_age_by_open_time = _bull_regime_age_by_open_time(
        frame,
        signals,
        bear_interrupt_tolerance_bars=BULL_REGIME_BEAR_INTERRUPT_TOLERANCE_BARS,
    )
    avg_abs_return_10_by_open_time = _avg_abs_return_10_by_open_time(frame)
    for idx in range(start_index, len(frame)):
        row = frame.iloc[idx]
        _advance_take_profit_reduce_clock(state)
        price = float(row["close"])
        _update_entry_high_price(state, row)
        rating = _current_rating(
            row,
            config,
            signals,
            bull_regime_age=bull_regime_age_by_open_time.get(int(row["open_time_ms"])),
            avg_abs_return_10_pct=avg_abs_return_10_by_open_time.get(int(row["open_time_ms"])),
        )
        action, target_position_pct, reasons, trade_plan = _target_from_rating(
            state,
            config,
            frame.iloc[: idx + 1],
            price,
            rating,
        )
        ma20_cross_down = _ma20_cross_down(frame.iloc[: idx + 1])
        equity_before_trade = _equity(state, price)
        current_position_pct = _current_position_pct(state, price, equity_before_trade)
        if current_position_pct > 0 and ma20_cross_down:
            action = "sell_clear"
            target_position_pct = 0.0
            reasons = reasons + ["close crossed down through MA20; clear position"]
            trade_plan = {
                **(trade_plan or {}),
                "action": "sell_clear",
                "side": "sell",
                "signal_type": "ma20_cross_down_clear",
                "target_position_pct": 0.0,
                "position_cap_pct": 0.0,
                "stop_line_name": "ma20",
                "stop_line_price": ma20_cross_down["ma20"],
                "reason": list((trade_plan or {}).get("reason", []))
                + ["close crossed down through MA20; clear position"],
                "metrics": {
                    **((trade_plan or {}).get("metrics") if isinstance((trade_plan or {}).get("metrics"), dict) else {}),
                    "ma20_cross_down": True,
                    "ma20_cross_down_open": ma20_cross_down["open"],
                    "ma20_cross_down_close": ma20_cross_down["close"],
                    "ma20_cross_down_ma20": ma20_cross_down["ma20"],
                },
            }
        trade = _rebalance(row, state, config, price, target_position_pct, action, trade_plan=trade_plan)
        if trade:
            _attach_trade_signal(trade, rating, reasons, trade_plan)
        equity = _equity(state, price)
        stop_event = _apply_profit_runner_state(state, config, equity)
        if stop_event:
            stop_trade = _rebalance(row, state, config, price, 0.0, "profit_trailing_stop", trade_plan=trade_plan)
            if stop_trade:
                _attach_trade_signal(stop_trade, rating, reasons + [stop_event], trade_plan)
                trades.append(stop_trade)
            action = "profit_trailing_stop"
            target_position_pct = 0.0
            reasons.append(stop_event)
        if trade:
            trades.append(trade)
        snapshot = _snapshot(row, state, config, price)
        decision = _decision(row, config, rating, action, target_position_pct, reasons, snapshot, trade_plan)
        decisions.append(decision)
        snapshots.append(snapshot)
        state.last_open_time_ms = int(row["open_time_ms"])
    return decisions, trades, snapshots


def _run_ma20_full_engine(
    frame: pd.DataFrame,
    config: CryptoTradingConfig,
    state: AccountState,
    *,
    start_index: int,
    signals: dict[int, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    decisions: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    if len(frame) < MA20_FULL_WINDOW:
        raise DataInsufficientError(
            f"Not enough local crypto OHLCV data for {config.symbol} {config.timeframe}: "
            f"{len(frame)}/{MA20_FULL_WINDOW}",
            hint="Run crypto sync with at least 20 --lookback-bars for ma20_full backtest",
        )
    start_index = max(MA20_FULL_WINDOW - 1, start_index)
    ma20_values = frame["close"].astype(float).rolling(MA20_FULL_WINDOW, min_periods=MA20_FULL_WINDOW).mean()
    if signals:
        signal_indices = [
            idx for idx, row in frame.iterrows() if int(row["open_time_ms"]) in signals
        ]
        if signal_indices:
            start_index = max(start_index, int(min(signal_indices)))
            missing = [
                int(row["open_time_ms"])
                for _, row in frame.iloc[start_index:].iterrows()
                if int(row["open_time_ms"]) not in signals
            ]
            if missing:
                raise DataInsufficientError(
                    f"Missing {len(missing)} crypto signal snapshots for {config.symbol} {config.timeframe}",
                    hint=(
                        "Run crypto signal-sync with a lookback window covering the ma20_full backtest range; "
                        f"first missing open_time_ms={missing[0]}"
                    ),
                )
    for idx in range(start_index, len(frame)):
        row = frame.iloc[idx]
        history = frame.iloc[: idx + 1]
        price = float(row["close"])
        ma20 = float(ma20_values.iloc[idx])
        if not math.isfinite(ma20):
            continue
        current_position_pct = _current_position_pct(state, price, _equity(state, price))
        signal = (signals or {}).get(int(row["open_time_ms"]))
        rating = signal.get("rating_payload") if isinstance(signal, dict) else None
        risk = _ma20_full_risk_context(history, rating if isinstance(rating, dict) else None)
        if price > ma20:
            if current_position_pct < 0.999 and risk["risk_count"] >= 2:
                action = "ma20_full_buy_blocked"
                target_position_pct = current_position_pct
                side = "hold"
                signal_type = "ma20_full_buy_blocked_by_risk"
                reasons = [
                    "close is above MA20, but two or more risk filters are active; buy ignored",
                    *_reburn_risk_reasons(risk),
                ]
            else:
                action = "ma20_full_buy_or_hold"
                target_position_pct = 1.0
                side = "buy"
                signal_type = "ma20_full_above"
                reasons = ["close is above MA20; target full position", *_reburn_risk_reasons(risk)]
        elif price < ma20:
            action = "ma20_full_sell_or_cash"
            target_position_pct = 0.0
            side = "sell"
            signal_type = "ma20_full_below"
            reasons = ["close is below MA20; target cash"]
        else:
            action = "ma20_full_hold"
            target_position_pct = current_position_pct
            side = "hold"
            signal_type = "ma20_full_equal"
            reasons = ["close equals MA20; hold current position"]
        trade_plan = {
            "action": action,
            "side": side,
            "signal_type": signal_type,
            "target_position_pct": target_position_pct,
            "position_cap_pct": target_position_pct,
            "stop_line_name": "ma20" if target_position_pct == 0.0 else None,
            "stop_line_price": ma20,
            "reason": reasons,
            "metrics": {
                "close": price,
                "ma20": ma20,
                "close_above_ma20": price > ma20,
                "close_below_ma20": price < ma20,
                **risk.get("golden_bull_lines", {}),
            },
            "reburn_risk": risk,
        }
        trade = _rebalance(row, state, config, price, target_position_pct, action, trade_plan=trade_plan)
        if trade:
            _attach_trade_signal(trade, {}, reasons, trade_plan)
            trades.append(trade)
        equity = _equity(state, price)
        stop_event = _apply_profit_runner_state(state, config, equity)
        if stop_event:
            stop_trade = _rebalance(row, state, config, price, 0.0, "profit_trailing_stop", trade_plan=trade_plan)
            if stop_trade:
                _attach_trade_signal(stop_trade, {}, reasons + [stop_event], trade_plan)
                trades.append(stop_trade)
            action = "profit_trailing_stop"
            target_position_pct = 0.0
            reasons.append(stop_event)
        snapshot = _snapshot(row, state, config, price)
        decision = _decision(row, config, {}, action, target_position_pct, reasons, snapshot, trade_plan)
        decisions.append(decision)
        snapshots.append(snapshot)
        state.last_open_time_ms = int(row["open_time_ms"])
    return decisions, trades, snapshots


def _current_rating(
    row: pd.Series,
    config: CryptoTradingConfig,
    signals: dict[int, dict[str, Any]],
    *,
    bull_regime_age: int | None = None,
    avg_abs_return_10_pct: float | None = None,
) -> dict[str, Any]:
    open_time_ms = int(row["open_time_ms"])
    signal = signals.get(open_time_ms)
    if not signal:
        raise DataInsufficientError(
            f"Missing crypto signal snapshot for {config.symbol} {config.timeframe} open_time_ms={open_time_ms}",
            hint="Run crypto signal-sync with a lookback window covering the backtest or paper-trade range",
        )
    rating = signal.get("rating_payload")
    if not isinstance(rating, dict):
        return {}
    enriched = dict(rating)
    metrics = rating.get("metrics") if isinstance(rating.get("metrics"), dict) else {}
    enriched_metrics = dict(metrics)
    existing_bull_regime_age = _optional_int(metrics.get("bull_regime_age"))
    if existing_bull_regime_age is None and bull_regime_age is not None:
        enriched_metrics["bull_regime_age"] = int(bull_regime_age)
    if _optional_float(metrics.get("avg_abs_return_10_pct")) is None and avg_abs_return_10_pct is not None:
        enriched_metrics["avg_abs_return_10_pct"] = _round(avg_abs_return_10_pct, 6)
    enriched["metrics"] = enriched_metrics
    return enriched


def _avg_abs_return_10_by_open_time(frame: pd.DataFrame) -> dict[int, float]:
    pct_change = frame["close"].astype(float).pct_change() * 100.0
    avg_abs_return_10 = pct_change.abs().shift(1).rolling(10, min_periods=10).mean()
    result: dict[int, float] = {}
    for idx, value in avg_abs_return_10.items():
        if pd.notna(value):
            result[int(frame.iloc[int(idx)]["open_time_ms"])] = float(value)
    return result


def _bull_regime_age_by_open_time(
    frame: pd.DataFrame,
    signals: dict[int, dict[str, Any]],
    *,
    bear_interrupt_tolerance_bars: int,
) -> dict[int, int]:
    age_by_open_time: dict[int, int] = {}
    bull_age = 0
    interrupted_bear_bars = 0
    for _, row in frame.iterrows():
        open_time_ms = int(row["open_time_ms"])
        signal = signals.get(open_time_ms)
        rating = signal.get("rating_payload") if isinstance(signal, dict) else None
        channel_regime = _effective_channel_regime_for_rating(rating)
        if channel_regime == "bull":
            bull_age += 1
            interrupted_bear_bars = 0
        elif channel_regime == "bear":
            interrupted_bear_bars += 1
            if interrupted_bear_bars > bear_interrupt_tolerance_bars:
                bull_age = 0
        else:
            bull_age = 0
            interrupted_bear_bars = 0
        age_by_open_time[open_time_ms] = bull_age
    return age_by_open_time


def _effective_channel_regime_for_rating(rating: Any) -> str:
    if not isinstance(rating, dict):
        return "unknown"
    channel_regime = str(rating.get("channel_regime") or "unknown")
    metrics = rating.get("metrics") if isinstance(rating.get("metrics"), dict) else {}
    ma60_slope_pct = _optional_float(metrics.get("ma60_slope_pct"))
    if channel_regime == "bull" and ma60_slope_pct is not None and ma60_slope_pct <= 0:
        return "bear"
    return channel_regime


def _legacy_target_from_rating(
    state: AccountState,
    config: CryptoTradingConfig,
    price: float,
    rating: dict[str, Any],
) -> tuple[str, float, list[str]]:
    equity = _equity(state, price)
    if state.phase == "profit_runner":
        return "hold_profit_runner", _current_position_pct(state, price, equity), [
            "profit_runner ignores direct 4h Golden Bull sell signal",
            f"channel_regime={rating.get('channel_regime', 'unknown')}",
        ]

    ratings = [item for item in rating.get("ratings", []) if isinstance(item, dict)]
    actions = {str(item.get("action") or ""): int(item.get("strength") or 0) for item in ratings}
    channel_regime = str(rating.get("channel_regime") or "unknown")
    reasons = [
        f"channel_regime={channel_regime}",
        "golden_bull_position_rating drives trend_trade target",
    ]

    current_position_pct = _current_position_pct(state, price, equity)
    if actions.get("清仓", 0) >= 3 and current_position_pct > 0:
        if _bull_upper_failure_reduce_required(rating) and current_position_pct > 0:
            reduce_target = min(current_position_pct, REDUCE_TARGET_POSITION_PCT)
            return "reduce", reduce_target, reasons + [
                "bull-channel upper failure converts clear pressure to reduce instead of clear",
            ]
        if _bull_support_exit_protected(rating) and current_position_pct > 0:
            return "hold_bull_support", current_position_pct, reasons + [
                "clear signal ignored in bull-channel support zone",
            ]
        return "sell_clear", 0.0, reasons + ["clear signal strength >= 3"]
    if actions.get("减仓", 0) >= 4 and current_position_pct > 0:
        if _bull_upper_failure_reduce_required(rating) and current_position_pct > 0:
            reduce_target = min(current_position_pct, REDUCE_TARGET_POSITION_PCT)
            return "reduce", reduce_target, reasons + [
                "bull-channel upper failure confirms reduce pressure",
            ]
        if _bull_support_exit_protected(rating) and current_position_pct > 0:
            return "hold_bull_support", current_position_pct, reasons + [
                "reduce signal ignored in bull-channel support zone",
            ]
        reduce_target = min(current_position_pct, REDUCE_TARGET_POSITION_PCT)
        if channel_regime == "bear":
            reduce_target = min(reduce_target, BEAR_MARKET_MAX_POSITION_PCT)
        return "reduce", reduce_target, reasons + ["reduce signal strength >= 4; reduce never increases exposure"]
    hold_strength = actions.get("持有", 0)
    entry_strength = max(
        [actions.get(name, 0) for name in ("建仓", "加仓", "轻仓加仓", "试仓")],
        default=0,
    )
    if channel_regime == "bear":
        if current_position_pct > BEAR_MARKET_MAX_POSITION_PCT:
            return "bear_cap", BEAR_MARKET_MAX_POSITION_PCT, reasons + [
                f"bear channel caps spot-long exposure at {BEAR_MARKET_MAX_POSITION_PCT:.0%}"
            ]
        if entry_strength >= 3 and actions.get("观察", 0) < 4:
            return "bear_trial", BEAR_MARKET_MAX_POSITION_PCT, reasons + [
                f"bear trial strength={entry_strength}; max position {BEAR_MARKET_MAX_POSITION_PCT:.0%}"
            ]
        if state.base_qty > 0:
            return "hold_bear", current_position_pct, reasons + ["bear hold does not add exposure"]
        return "wait", 0.0, reasons + ["bear channel without explicit trial entry"]
    if channel_regime == "bull":
        if _bull_fresh_breakout_wait_required(rating):
            if state.base_qty > 0:
                return "hold_breakout_watch", current_position_pct, reasons + [
                    "fresh upper breakout waits for 3-bar retest before adding"
                ]
            return "wait_breakout_watch", 0.0, reasons + [
                "fresh upper breakout is hold-only and does not open a new spot-long"
            ]
        reclaim_strength = max(entry_strength, hold_strength)
        if reclaim_strength >= 3 and _bull_life_line_reclaim_confirmed(rating):
            return "buy_or_hold_full", 1.0, reasons + [
                f"bull life-line reclaim strength={reclaim_strength}"
            ]
        if entry_strength >= 4 and _bull_add_position_allowed(rating):
            return "buy_or_hold_full", 1.0, reasons + [f"confirmed bull entry strength={entry_strength}"]
        if entry_strength >= 3 and actions.get("观察", 0) < 4 and _bull_support_retest_confirmed(rating):
            return "buy_or_hold_light", 0.3, reasons + [f"confirmed light bull entry strength={entry_strength}"]
        if state.base_qty > 0:
            return "hold", current_position_pct, reasons + [
                f"hold strength={hold_strength}; no confirmed add-back setup"
            ]
        return "wait", 0.0, reasons + ["bull channel without confirmed entry setup"]
    bullish_strength = max(entry_strength, hold_strength)
    if bullish_strength >= 3 and actions.get("观察", 0) < 4:
        return "buy_or_hold_light", 0.3, reasons + [f"light bullish strength={bullish_strength}"]
    if state.base_qty > 0:
        return "hold", _current_position_pct(state, price, equity), reasons + ["no explicit exit signal"]
    return "wait", 0.0, reasons + ["no entry signal"]


def _bull_support_exit_protected(rating: dict[str, Any]) -> bool:
    if str(rating.get("channel_regime") or "") != "bull":
        return False
    scenes = {str(scene) for scene in rating.get("scenes", [])}
    if "volume_break" in scenes:
        return False
    metrics = rating.get("metrics") if isinstance(rating.get("metrics"), dict) else {}
    close_below_support_days = _optional_int(metrics.get("close_below_support_days"))
    if close_below_support_days is not None and close_below_support_days >= 2:
        return False

    if _bull_support_retest_confirmed(rating):
        return True
    return False


def _bull_upper_failure_reduce_required(rating: dict[str, Any]) -> bool:
    if str(rating.get("channel_regime") or "") != "bull":
        return False
    scenes = {str(scene) for scene in rating.get("scenes", [])}
    if _bull_support_retest_confirmed(rating):
        return False
    metrics = rating.get("metrics") if isinstance(rating.get("metrics"), dict) else {}
    close_below_upper_days = _optional_int(metrics.get("close_below_upper_days"))
    distance_to_upper_pct = _optional_float(metrics.get("distance_to_upper_pct"))
    if "upper_shadow_failed" in scenes or metrics.get("upper_shadow_state") == "failed":
        return True
    if not scenes.intersection({"upper_lost", "upper_exhaustion", "upper_pressure_bearish"}):
        return False
    return (
        close_below_upper_days is not None
        and close_below_upper_days >= 2
        and distance_to_upper_pct is not None
        and -1.0 <= distance_to_upper_pct <= 8.0
    )


def _bull_fresh_breakout_wait_required(rating: dict[str, Any]) -> bool:
    if str(rating.get("channel_regime") or "") != "bull":
        return False
    scenes = {str(scene) for scene in rating.get("scenes", [])}
    return "fresh_upper_breakout" in scenes


def _bull_add_position_allowed(rating: dict[str, Any]) -> bool:
    if str(rating.get("channel_regime") or "") != "bull":
        return False
    return _bull_support_retest_confirmed(rating) or _bull_life_line_reclaim_confirmed(rating)


def _bull_support_retest_confirmed(rating: dict[str, Any]) -> bool:
    scenes = {str(scene) for scene in rating.get("scenes", [])}
    support_scenes = {
        "lower_support",
        "bull_channel_pullback",
        "lower_panic",
        "below_support_volume_recovery",
        "reclaim_channel",
    }
    if not scenes.intersection(support_scenes):
        return False
    metrics = rating.get("metrics") if isinstance(rating.get("metrics"), dict) else {}
    close_below_support_days = _optional_int(metrics.get("close_below_support_days"))
    if close_below_support_days is not None and close_below_support_days >= 2:
        return False

    open_ = _optional_float(metrics.get("open"))
    close = _optional_float(metrics.get("close"))
    low = _optional_float(metrics.get("low"))
    life_line = _optional_float(metrics.get("bull_bear_boundary"))
    if life_line is None:
        life_line = _optional_float(metrics.get("golden_bull_trend"))
    if None in (open_, close, low, life_line):
        return False
    if close <= open_:
        return False
    return bool(low <= life_line and close <= life_line * 1.08)


def _bull_life_line_reclaim_confirmed(rating: dict[str, Any]) -> bool:
    if str(rating.get("channel_regime") or "") != "bull":
        return False
    metrics = rating.get("metrics") if isinstance(rating.get("metrics"), dict) else {}
    open_ = _optional_float(metrics.get("open"))
    close = _optional_float(metrics.get("close"))
    low = _optional_float(metrics.get("low"))
    life_line = _optional_float(metrics.get("bull_bear_boundary"))
    if life_line is None:
        life_line = _optional_float(metrics.get("golden_bull_trend"))
    daily_return_pct = _optional_float(metrics.get("daily_return_pct"))
    close_below_support_days = _optional_int(metrics.get("close_below_support_days"))
    if None in (open_, close, low, life_line):
        return False
    if close_below_support_days is not None and close_below_support_days >= 2:
        return False
    if daily_return_pct is not None and daily_return_pct > 10.0:
        return False
    return bool(open_ < close and low <= life_line * 1.03 and close > life_line and close <= life_line * 1.08)


def _bull_upper_retest_confirmed(rating: dict[str, Any]) -> bool:
    scenes = {str(scene) for scene in rating.get("scenes", [])}
    if "upper_support" not in scenes:
        return False
    metrics = rating.get("metrics") if isinstance(rating.get("metrics"), dict) else {}
    close_above_upper_days = _optional_int(metrics.get("close_above_upper_days"))
    if close_above_upper_days is None or close_above_upper_days < 3:
        return False

    open_ = _optional_float(metrics.get("open"))
    close = _optional_float(metrics.get("close"))
    low = _optional_float(metrics.get("low"))
    upper = _optional_float(metrics.get("upper_line"))
    if None in (open_, close, low, upper):
        return False
    if close <= open_:
        return False
    return bool(low <= upper * 1.02 and close >= upper * 0.99 and close <= upper * 1.03)


def _target_from_rating(
    state: AccountState,
    config: CryptoTradingConfig,
    history: pd.DataFrame,
    price: float,
    rating: dict[str, Any],
) -> tuple[str, float, list[str], dict[str, Any]]:
    equity = _equity(state, price)
    current_position_pct = _current_position_pct(state, price, equity)
    trade_plan = build_unified_kline_trade_plan(
        rating,
        history,
        current_position_pct=current_position_pct,
        timeframe_label="4h",
        stop_line_name=state.stop_line_name,
        stop_line_price=state.stop_line_price,
        take_profit_reduced=_take_profit_reduce_blocked(state),
        entry_price=state.entry_price,
        entry_high_price=state.entry_high_price,
        take_profit_entry_price=_latest_take_profit_entry_price(state),
        take_profit_entry_high_price=_latest_take_profit_entry_high_price(state),
        entry_candle_low_price=state.entry_candle_low_price,
        add_on_entry_low_price=_latest_add_on_entry_low_price(state),
        add_on_stop_target_position_pct=_latest_add_on_stop_target_position_pct(state),
    )
    if state.phase == "profit_runner":
        return "hold_profit_runner", current_position_pct, [
            "profit_runner blocks new 4h Reburn buys and ignores direct 4h Golden Bull sell signal",
            f"channel_regime={rating.get('channel_regime', 'unknown')}",
        ], trade_plan

    channel_regime = str(trade_plan.get("channel_regime") or rating.get("channel_regime") or "unknown")
    reasons = [
        f"channel_regime={trade_plan.get('channel_regime') or channel_regime}",
        "Golden Bull trend/MA20 crosses drive target-position trading",
        "golden_bull_trade_plan drives trend_trade target",
        f"trade_signal={trade_plan.get('signal_type')}",
    ]
    reasons.extend(str(item) for item in trade_plan.get("reason", []) if item)
    return (
        str(trade_plan.get("action") or "wait"),
        float(trade_plan.get("target_position_pct") or 0.0),
        reasons,
        trade_plan,
    )


def _reburn_buy_trade_plan(
    history: pd.DataFrame,
    rating: dict[str, Any],
    *,
    current_position_pct: float,
    fallback_trade_plan: dict[str, Any],
) -> dict[str, Any]:
    if len(history) < 62:
        return {
            **fallback_trade_plan,
            "reburn_signal": None,
            "reburn_risk": {"insufficient_history": True},
            "reason": list(fallback_trade_plan.get("reason", []))
            + ["insufficient 4h history for Reburn point; legacy plan kept"],
        }
    raw_channel_regime = str(rating.get("channel_regime") or "unknown")
    risk = _reburn_risk_context(history, raw_channel_regime)
    signal = _reburn_signal(history)
    strong_volume = _reburn_strong_volume(history)
    base_plan = {
        "action": "hold" if current_position_pct > 0 else "wait",
        "side": "hold",
        "signal_type": "no_trade",
        "target_position_pct": current_position_pct,
        "position_cap_pct": REBURN_STRONG_TARGET_POSITION_PCT,
        "stop_line_name": None,
        "stop_line_price": None,
        "channel_regime": raw_channel_regime,
        "raw_channel_regime": raw_channel_regime,
        "reburn_signal": signal,
        "reburn_risk": risk,
        "metrics": {
            **(fallback_trade_plan.get("metrics") if isinstance(fallback_trade_plan.get("metrics"), dict) else {}),
            "reburn": signal,
            "reburn_risk": risk,
            "volume_vs_prev_ratio": _reburn_volume_vs_prev_ratio(history),
        },
        "candidates": list(fallback_trade_plan.get("candidates", [])),
    }
    if not signal:
        return {
            **base_plan,
            "reason": ["no 4h Reburn point; legacy Golden Bull buy signal ignored"],
        }
    if risk["risk_count"] >= 2:
        return {
            **base_plan,
            "reason": [
                "4h Reburn point appeared, but two or more risk filters are active; buy ignored",
                *_reburn_risk_reasons(risk),
            ],
        }

    target = REBURN_STRONG_TARGET_POSITION_PCT if strong_volume else REBURN_WEAK_TARGET_POSITION_PCT
    signal_type = "reburn_4h_strong_buy" if strong_volume else "reburn_4h_weak_buy"
    if risk["risk_count"] == 1:
        target = min(target, REBURN_RISK_CAP_POSITION_PCT)
    if current_position_pct >= target - 0.001:
        return {
            **base_plan,
            "target_position_pct": current_position_pct,
            "position_cap_pct": target,
            "signal_type": signal_type,
            "reason": [
                "4h Reburn point confirmed, but target position already reached",
                *_reburn_risk_reasons(risk),
            ],
        }
    return {
        **base_plan,
        "action": "buy_or_hold_full" if target >= REBURN_STRONG_TARGET_POSITION_PCT else "buy_or_hold_half",
        "side": "buy",
        "signal_type": signal_type,
        "target_position_pct": target,
        "position_cap_pct": target,
        "stop_line_name": "entry_candle_low",
        "stop_line_price": None,
        "reason": [
            "4h Reburn point confirmed",
            (
                "strong volume: latest volume is 1.8~2.2x previous bar"
                if strong_volume
                else "weak volume: latest volume is outside 1.8~2.2x previous bar"
            ),
            *_reburn_risk_reasons(risk),
        ],
    }


def _reburn_risk_context(history: pd.DataFrame, raw_channel_regime: str) -> dict[str, Any]:
    ma20_down = _ma_down(history, 20, REBURN_MA_SLOPE_LOOKBACK_BARS)
    ma60_down = _ma_down(history, 60, REBURN_MA_SLOPE_LOOKBACK_BARS)
    bear = raw_channel_regime == "bear"
    return {
        "bear": bear,
        "ma20_down": ma20_down,
        "ma60_down": ma60_down,
        "risk_count": int(bear) + int(ma20_down) + int(ma60_down),
    }


def _ma20_full_risk_context(
    history: pd.DataFrame,
    rating: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_channel_regime = "unknown"
    line_metrics: dict[str, Any] = {}
    rating_metrics = rating.get("metrics") if isinstance(rating, dict) and isinstance(rating.get("metrics"), dict) else {}
    if isinstance(rating, dict):
        raw_channel_regime = str(rating.get("channel_regime") or "unknown")
        life_line = _optional_float(
            rating_metrics.get("golden_bull_trend")
            if rating_metrics.get("golden_bull_trend") is not None
            else rating_metrics.get("life_line")
        )
        trend_confirmation_line = _optional_float(
            rating_metrics.get("golden_bull_2")
            if rating_metrics.get("golden_bull_2") is not None
            else rating_metrics.get("trend_confirmation_line")
        )
        golden_bull = _optional_float(rating_metrics.get("golden_bull") or rating_metrics.get("upper_line"))
        upper = _optional_float(rating_metrics.get("upper_line") or rating_metrics.get("channel_upper") or golden_bull)
        lower = _optional_float(rating_metrics.get("lower_line") or rating_metrics.get("channel_lower") or life_line)
        line_metrics = {
            "golden_bull": _round(golden_bull),
            "golden_bull_trend": _round(life_line),
            "golden_bull_2": _round(trend_confirmation_line),
            "upper_line": _round(upper),
            "lower_line": _round(lower),
        }
    elif len(history) >= 120:
        lines = compute_golden_bull_lines(history)
        line = lines.iloc[-1]
        life_line = _optional_float(line.get("golden_bull_trend"))
        trend_confirmation_line = _optional_float(line.get("golden_bull_2"))
        golden_bull = _optional_float(line.get("golden_bull"))
        upper = _optional_float(line.get("channel_upper"))
        lower = _optional_float(line.get("channel_lower"))
        line_metrics = {
            "golden_bull": _round(golden_bull),
            "golden_bull_trend": _round(life_line),
            "golden_bull_2": _round(trend_confirmation_line),
            "upper_line": _round(upper),
            "lower_line": _round(lower),
        }
        if life_line is not None and trend_confirmation_line is not None:
            if abs(life_line - trend_confirmation_line) <= 1e-8:
                raw_channel_regime = "transition"
            else:
                raw_channel_regime = "bull" if trend_confirmation_line < life_line else "bear"
    risk = _reburn_risk_context(history, raw_channel_regime)
    risk["channel_regime"] = raw_channel_regime
    risk["golden_bull_lines"] = line_metrics
    return risk


def _reburn_risk_reasons(risk: dict[str, Any]) -> list[str]:
    reasons = []
    if risk.get("bear"):
        reasons.append("risk filter active: Golden Bull raw channel is bear")
    if risk.get("ma20_down"):
        reasons.append("risk filter active: MA20 is below its value 3 bars ago")
    if risk.get("ma60_down"):
        reasons.append("risk filter active: MA60 is below its value 3 bars ago")
    if not reasons:
        reasons.append("no bear/MA20-down/MA60-down risk filter active")
    return reasons


def _ma_down(history: pd.DataFrame, period: int, lookback: int) -> bool:
    if len(history) < period + lookback:
        return False
    ma = history["close"].astype(float).rolling(period, min_periods=period).mean()
    latest = ma.iloc[-1]
    previous = ma.iloc[-1 - lookback]
    if pd.isna(latest) or pd.isna(previous):
        return False
    return bool(latest < previous)


def _reburn_strong_volume(history: pd.DataFrame) -> bool:
    ratio = _reburn_volume_vs_prev_ratio(history)
    return ratio is not None and 1.8 <= ratio <= 2.2


def _reburn_volume_vs_prev_ratio(history: pd.DataFrame) -> float | None:
    if len(history) < 2 or "vol" not in history:
        return None
    latest = _optional_float(history.iloc[-1].get("vol"))
    previous = _optional_float(history.iloc[-2].get("vol"))
    if latest is None or previous in (None, 0):
        return None
    return latest / previous


def _reburn_signal(history: pd.DataFrame) -> bool:
    closes = [float(item) for item in history["close"].dropna().tolist()]
    if len(closes) < 62:
        return False
    current = closes[-1]
    previous = closes[-2]
    if current <= previous:
        return False

    r1 = _reburn_window(closes, 10)
    r2 = _reburn_window(closes, 13)
    r3 = _reburn_window(closes, 20)
    r4 = _reburn_window(closes, 30)
    r5 = _reburn_window(closes, 60)
    if None in (r1, r2, r3, r4, r5):
        return False
    assert r1 and r2 and r3 and r4 and r5

    gain = (current / previous - 1.0) * 100.0
    range8 = _range_pct(closes[-8:])
    slope5 = (current / closes[-6] - 1.0) * 100.0 if len(closes) >= 6 and closes[-6] else 0.0

    low_rebound = r1["low_dist"] == 0 and gain >= 4 and r1["rebound"] <= 4.5 and r1["prev_rebound"] <= 0.2
    mid_low_rebound = (
        r1["low_dist"] == 0 and gain >= 5 and r1["rebound"] <= 6.5 and r1["prev_rebound"] <= 0.2 and r1["drop"] < 15
    )
    deep_low_rebound = (
        r1["low_dist"] == 0 and r1["drop"] >= 20 and gain >= 3.4 and r1["rebound"] <= 4 and r1["prev_rebound"] <= 0.2
    )
    strong_low_rebound = r1["low_dist"] == 0 and gain >= 6.5
    near_low_allowed = r1["low_dist"] > 0 or low_rebound or mid_low_rebound or deep_low_rebound or strong_low_rebound
    short_noise_filter = not (r1["low_dist"] <= 1 and r1["prev_rebound"] >= 3)
    early_noise_filter = not (r3["low_dist"] <= 1 and r3["prev_rebound"] >= 3)
    weak_rebound_allowed = r1["rebound"] >= 4 or gain >= 2 or r1["low_dist"] <= 2
    chase_filter = r3["rebound"] <= 25 and r4["rebound"] <= 35 and r5["rebound"] <= 60
    fake_low_filter = not (r1["low_dist"] == 2 and r1["drop"] >= 15 and r1["rebound"] < 4 and gain < 2.5)
    crash_first_bull = (
        r1["drop"] >= 12 and r1["low_dist"] <= 1 and gain < 5 and r1["rebound"] < 5 and range8 >= 12 and slope5 < -5
    )
    crash_filter = not crash_first_bull

    cond0 = r3["drop"] >= 25 and 18 <= r3["rebound"] <= 25 and gain >= 0.9 and r5["rebound"] <= 60
    cond1 = (
        r1["drop"] >= 8
        and _reburn_cross(closes, 10, 3)
        and near_low_allowed
        and short_noise_filter
        and early_noise_filter
        and weak_rebound_allowed
        and (r1["rebound"] < 4 or r1["rebound"] >= 5 or low_rebound or deep_low_rebound)
        and chase_filter
        and fake_low_filter
    )
    cond1b = r1["drop"] >= 5 and _reburn_cross(closes, 10, 3) and r1["low_dist"] >= 5 and r1["rebound"] < 4 and gain >= 2 and chase_filter
    cond2 = (
        r2["drop"] >= 10
        and _reburn_cross(closes, 13, 5)
        and (r2["rebound"] >= 6 or r2["low_dist"] == 1)
        and (r2["prev_rebound"] < 4.5 or r3["drop"] >= 25)
        and early_noise_filter
        and chase_filter
    )
    cond3 = (
        r3["drop"] >= 15
        and _reburn_cross(closes, 20, 5)
        and (r2["rebound"] >= 6 or r2["low_dist"] == 1)
        and (r3["prev_rebound"] < 4.5 or r3["drop"] >= 25)
        and (r3["prev_rebound"] < 4 or r3["rebound"] <= 7)
        and early_noise_filter
        and chase_filter
    )
    cond4 = r4["drop"] >= 18 and _reburn_cross(closes, 30, 6) and r3["rebound"] <= 20 and gain >= 3 and r5["rebound"] <= 60
    cond5 = r5["drop"] >= 30 and r3["drop"] < 12 and _reburn_cross(closes, 60, 3) and r5["rebound"] < 5 and gain >= 2.5
    cond6 = r5["drop"] >= 30 and r3["drop"] < 12 and r5["low_dist"] == 0 and 1.5 <= r5["rebound"] <= 2.2 and gain >= 1.5
    patch1 = r1["drop"] >= 5 and r1["drop"] < 8 and _reburn_cross(closes, 10, 3) and 1 <= r1["low_dist"] <= 3 and r1["rebound"] <= 4 and r1["prev_rebound"] <= 1.5 and gain >= 1.5 and r3["rebound"] <= 12
    patch2 = r1["drop"] >= 8 and _reburn_cross(closes, 10, 3) and r1["prev_rebound"] <= 1 and 4 <= r1["rebound"] <= 5 and gain >= 3 and r5["rebound"] <= 60
    patch3 = r1["drop"] >= 10 and _reburn_cross(closes, 10, 2) and r1["low_dist"] >= 5 and r1["rebound"] <= 2.5 and gain >= 1.8 and r5["rebound"] <= 60
    patch4 = r3["drop"] >= 10 and r4["drop"] >= 20 and r1["low_dist"] == 0 and 1.8 <= r1["rebound"] <= 2.4 and gain >= 1.8 and r1["prev_rebound"] <= 0.2 and 5 <= r4["rebound"] <= 8
    patch5 = r3["drop"] >= 19 and _reburn_cross(closes, 20, 4.5) and 4 <= r3["prev_rebound"] <= 5 and 4.5 <= r3["rebound"] <= 7 and gain >= 0.3 and r1["low_dist"] >= 3 and r5["rebound"] <= 60
    patch6 = r4["drop"] >= 17 and _reburn_cross(closes, 30, 6) and r4["rebound"] <= 7 and 4 <= r4["prev_rebound"] < 6 and gain >= 1.5 and r5["rebound"] <= 60
    patch7 = r3["drop"] >= 15 and r4["drop"] >= 18 and r1["low_dist"] >= 4 and 1 <= r1["rebound"] <= 1.8 and 0.9 <= r1["prev_rebound"] <= 1.4 and gain > 0
    patch8 = r3["drop"] >= 15 and r1["low_dist"] == 0 and 2 <= r1["rebound"] <= 2.5 and gain >= 2 and r3["rebound"] >= 10 and r5["rebound"] <= 60
    stable = range8 <= 8.8 and sum(1 for idx in range(-8, 0) if closes[idx] >= closes[idx - 1]) >= 3
    stable_bull = (
        stable
        and 0.5 <= gain <= 4.8
        and r1["rebound"] >= 1.5
        and r5["rebound"] <= 60
        and (r1["low_dist"] >= 1 or r1["drop"] <= 5)
        and (_reburn_cross(closes, 10, 1.5) or _reburn_cross(closes, 10, 3) or gain >= 2.5)
    )

    base = any(
        [
            cond0,
            cond1,
            cond1b,
            cond2,
            cond3,
            cond4,
            cond5,
            cond6,
            patch1,
            patch2,
            patch3,
            patch4,
            patch5,
            patch6,
            patch7,
            patch8,
        ]
    )
    return (base and crash_filter) or (stable_bull and not crash_first_bull)


def _reburn_window(closes: list[float], period: int) -> dict[str, float | int] | None:
    if len(closes) < period + 1:
        return None
    prior = closes[-period - 1 : -1]
    low = min(prior)
    high = max(prior)
    if low <= 0 or high <= 0:
        return None
    low_last_index = max(idx for idx, value in enumerate(prior) if value == low)
    return {
        "low": low,
        "high": high,
        "drop": (high - low) / high * 100.0,
        "rebound": (closes[-1] - low) / low * 100.0,
        "prev_rebound": (closes[-2] - low) / low * 100.0,
        "low_dist": len(prior) - 1 - low_last_index,
    }


def _reburn_cross(closes: list[float], period: int, threshold: float) -> bool:
    if len(closes) < period + 2:
        return False
    current = _reburn_window(closes, period)
    previous_prior = closes[-period - 2 : -2]
    if current is None or not previous_prior:
        return False
    previous_low = min(previous_prior)
    if previous_low <= 0:
        return False
    previous_rebound = (closes[-2] - previous_low) / previous_low * 100.0
    return bool(previous_rebound <= threshold < current["rebound"])


def _range_pct(values: list[float]) -> float:
    if not values:
        return 0.0
    low = min(values)
    if low <= 0:
        return 0.0
    return (max(values) - low) / low * 100.0


def _ma20_cross_down(history: pd.DataFrame) -> dict[str, float] | None:
    if len(history) < 20:
        return None
    opens = history["open"].astype(float)
    closes = history["close"].astype(float)
    ma20 = closes.rolling(20, min_periods=20).mean()
    open_ = float(opens.iloc[-1])
    close = float(closes.iloc[-1])
    current_ma20 = float(ma20.iloc[-1])
    if not all(math.isfinite(value) for value in [open_, close, current_ma20]):
        return None
    if open_ > current_ma20 and close < current_ma20:
        return {
            "open": open_,
            "close": close,
            "ma20": current_ma20,
        }
    return None


def _rebalance(
    row: pd.Series,
    state: AccountState,
    config: CryptoTradingConfig,
    price: float,
    target_position_pct: float,
    action: str,
    *,
    trade_plan: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    equity = _equity(state, price)
    deployable_capital = _deployable_capital(config, equity)
    target_position_pct = min(1.0, max(0.0, target_position_pct))
    current_value = state.base_qty * price
    raw_target_value = equity * target_position_pct
    capped_target_value = deployable_capital * target_position_pct
    if raw_target_value > current_value:
        target_value = max(current_value, min(raw_target_value, capped_target_value))
    else:
        target_value = raw_target_value
    cash_before = state.cash
    base_qty_before = state.base_qty
    position_pct_before = current_value / equity if equity > 0 else 0.0
    equity_before = equity
    if target_position_pct > 1e-12 and abs(target_position_pct - position_pct_before) < MIN_REBALANCE_POSITION_DELTA_PCT:
        return None
    stop_line_name_before = state.stop_line_name
    stop_line_price_before = state.stop_line_price
    entry_signal_type_before = state.entry_signal_type
    entry_price_before = state.entry_price
    entry_high_price_before = state.entry_high_price
    entry_candle_low_price_before = state.entry_candle_low_price
    position_lots_before = [lot.to_dict() for lot in state.position_lots]
    delta_value = target_value - current_value
    if abs(delta_value) < max(1.0, equity * 0.001):
        return None

    slippage = config.slippage_bps / 10_000.0
    if delta_value > 0:
        fill_price = price * (1.0 + slippage)
        gross = min(delta_value, state.cash)
        fee = gross * config.fee_rate
        spend = max(0.0, gross - fee)
        qty = spend / fill_price if fill_price > 0 else 0.0
        if qty <= 0:
            return None
        state.cash -= gross
        state.base_qty += qty
        side = "buy"
        quote_value = gross
    else:
        fill_price = price * (1.0 - slippage)
        qty = min(state.base_qty, abs(delta_value) / fill_price if fill_price > 0 else 0.0)
        if qty <= 0:
            return None
        gross = qty * fill_price
        fee = gross * config.fee_rate
        proceeds = max(0.0, gross - fee)
        state.cash += proceeds
        state.base_qty -= qty
        state.realized_pnl = _equity(state, price) - config.initial_capital
        side = "sell"
        quote_value = gross

    if side == "buy" and trade_plan:
        previous_position_cost = base_qty_before * fill_price if base_qty_before > 0 else 0.0
        added_position_cost = qty * fill_price
        total_qty = base_qty_before + qty
        if total_qty > 0:
            existing_entry = state.entry_price or fill_price
            previous_position_cost = base_qty_before * existing_entry
            state.entry_price = (previous_position_cost + added_position_cost) / total_qty
        row_high = _optional_float(row.get("high"))
        row_low = _optional_float(row.get("low"))
        if row_high is not None:
            state.entry_high_price = max(
                value for value in [state.entry_high_price, row_high] if value is not None
            )
        if base_qty_before <= 1e-12 or state.entry_candle_low_price is None:
            state.entry_candle_low_price = row_low
        state.position_lots.append(
            PositionLot(
                open_time_ms=int(row["open_time_ms"]),
                qty=qty,
                entry_price=fill_price,
                entry_low_price=row_low,
                entry_high_price=row_high,
                position_pct_before=position_pct_before,
                signal_type=str(trade_plan.get("signal_type")) if trade_plan.get("signal_type") else None,
            )
        )
        state.stop_line_name = trade_plan.get("stop_line_name") or state.stop_line_name
        state.stop_line_price = _optional_float(trade_plan.get("stop_line_price")) or state.stop_line_price
        state.entry_signal_type = trade_plan.get("signal_type") or state.entry_signal_type
        state.take_profit_reduced = False
        state.take_profit_reduce_bars_since = None
        state.last_take_profit_reduce_open_time_ms = None
    elif side == "sell":
        _consume_position_lots(state, qty)
        _refresh_entry_price_from_lots(state)
    if side == "sell" and trade_plan and trade_plan.get("signal_type") in {
        "bull_take_profit_reduce",
        "bear_upper_bearish_sell",
    }:
        state.take_profit_reduced = True
        state.take_profit_reduce_bars_since = 0
        state.last_take_profit_reduce_open_time_ms = int(row["open_time_ms"])
    if state.base_qty <= 1e-12:
        state.base_qty = 0.0
        state.stop_line_name = None
        state.stop_line_price = None
        state.entry_signal_type = None
        state.entry_price = None
        state.entry_high_price = None
        state.entry_candle_low_price = None
        state.position_lots = []
        state.take_profit_reduced = False
        state.take_profit_reduce_bars_since = None
        state.last_take_profit_reduce_open_time_ms = None
        if action in {"sell_clear", "profit_trailing_stop"}:
            state.phase = "exit_locked"
    equity_after = _equity(state, price)
    trade_plan_stop_price = _optional_float((trade_plan or {}).get("stop_line_price"))
    trade_stop_line_price = (
        trade_plan_stop_price
        if trade_plan_stop_price is not None
        else state.stop_line_price if state.stop_line_price is not None else stop_line_price_before
    )
    trade_entry_price = state.entry_price if state.entry_price is not None else entry_price_before
    trade_entry_high_price = state.entry_high_price if state.entry_high_price is not None else entry_high_price_before
    trade_entry_candle_low_price = (
        state.entry_candle_low_price
        if state.entry_candle_low_price is not None
        else entry_candle_low_price_before
    )
    trade_add_on_entry_low_price = _latest_add_on_entry_low_price(state)
    trade_add_on_stop_target_position_pct = _latest_add_on_stop_target_position_pct(state)
    if (trade_plan or {}).get("signal_type") == "add_on_entry_low_stop":
        trade_add_on_entry_low_price = trade_plan_stop_price
        trade_add_on_stop_target_position_pct = _optional_float((trade_plan or {}).get("target_position_pct"))

    return {
        "time": _row_time(row),
        "open_time_ms": int(row["open_time_ms"]),
        "symbol": config.symbol,
        "timeframe": config.timeframe,
        "side": side,
        "action": action,
        "price": _round(fill_price),
        "qty": _round(qty, 10),
        "quote_value": _round(quote_value),
        "fee": _round(fee),
        "target_position_pct": _round(target_position_pct, 6),
        "deployable_capital_before": _round(deployable_capital),
        "return_pct_before": _round((equity_before / config.initial_capital) - 1.0, 6),
        "equity_before": _round(equity_before),
        "cash_before": _round(cash_before),
        "base_qty_before": _round(base_qty_before, 10),
        "position_pct_before": _round(position_pct_before, 6),
        "return_pct_after": _round((equity_after / config.initial_capital) - 1.0, 6),
        "equity_after": _round(equity_after),
        "cash_after": _round(state.cash),
        "base_qty_after": _round(state.base_qty, 10),
        "position_pct_after": _round(_current_position_pct(state, price, equity_after), 6),
        "stop_line_name": (trade_plan or {}).get("stop_line_name") or state.stop_line_name or stop_line_name_before,
        "stop_line_price": _round(trade_stop_line_price),
        "entry_signal_type": state.entry_signal_type or entry_signal_type_before,
        "entry_price": _round(trade_entry_price),
        "entry_price_before": _round(entry_price_before),
        "entry_high_price": _round(trade_entry_high_price),
        "entry_high_price_before": _round(entry_high_price_before),
        "entry_peak_gain_pct": _round(_entry_peak_gain_pct(trade_entry_high_price, trade_entry_price), 6),
        "entry_candle_low_price": _round(trade_entry_candle_low_price),
        "entry_candle_low_price_before": _round(entry_candle_low_price_before),
        "add_on_entry_low_price": _round(trade_add_on_entry_low_price),
        "add_on_stop_target_position_pct": _round(trade_add_on_stop_target_position_pct, 6),
        "position_lots": [lot.to_dict() for lot in state.position_lots],
        "position_lots_before": position_lots_before,
        "take_profit_reduced": state.take_profit_reduced,
        "take_profit_reduce_bars_since": state.take_profit_reduce_bars_since,
        "last_take_profit_reduce_open_time_ms": state.last_take_profit_reduce_open_time_ms,
    }


def _attach_trade_signal(
    trade: dict[str, Any],
    rating: dict[str, Any],
    reasons: list[str],
    trade_plan: dict[str, Any] | None,
) -> None:
    trade["reason"] = list(reasons)
    trade["trade_plan"] = trade_plan or {}
    trade["rating"] = {
        "channel_regime": rating.get("channel_regime"),
        "channel_strength": rating.get("channel_strength"),
        "scenes": rating.get("scenes", []),
        "ratings": rating.get("ratings", []),
        "risk_flags": rating.get("risk_flags", []),
        "invalidations": rating.get("invalidations", []),
        "metrics": rating.get("metrics", {}),
    }


def _apply_profit_runner_state(state: AccountState, config: CryptoTradingConfig, equity: float) -> str | None:
    profit = equity - config.initial_capital
    return_pct = profit / config.initial_capital
    if state.phase in {"cash", "exit_locked"} and state.base_qty > 0:
        state.phase = "trend_trade"
    if state.phase == "trend_trade" and return_pct >= config.profit_runner_trigger_pct:
        state.phase = "profit_runner"
        state.equity_high_watermark = equity
        state.profit_high_watermark = max(0.0, profit)
        return None
    if state.phase != "profit_runner":
        return None

    if state.equity_high_watermark is None or equity > state.equity_high_watermark:
        state.equity_high_watermark = equity
    state.profit_high_watermark = max(0.0, (state.equity_high_watermark or equity) - config.initial_capital)
    stop_equity = (state.equity_high_watermark or equity) - (state.profit_high_watermark or 0.0) * config.profit_drawdown_stop_pct
    if (state.profit_high_watermark or 0.0) > 0 and equity <= stop_equity:
        state.phase = "exit_locked"
        return f"profit drawdown stop: equity <= {_round(stop_equity)}"
    return None


def _snapshot(row: pd.Series, state: AccountState, config: CryptoTradingConfig, price: float) -> dict[str, Any]:
    equity = _equity(state, price)
    deployable_capital = _deployable_capital(config, equity)
    profit_hwm = state.profit_high_watermark or max(0.0, equity - config.initial_capital)
    equity_hwm = state.equity_high_watermark or equity
    stop_equity = None
    profit_drawdown = None
    profit_drawdown_pct = None
    if profit_hwm > 0:
        stop_equity = equity_hwm - profit_hwm * config.profit_drawdown_stop_pct
        profit_drawdown = max(0.0, equity_hwm - equity)
        profit_drawdown_pct = profit_drawdown / profit_hwm if profit_hwm else None
    return {
        "time": _row_time(row),
        "open_time_ms": int(row["open_time_ms"]),
        "symbol": config.symbol,
        "timeframe": config.timeframe,
        "phase": state.phase,
        "price": _round(price),
        "cash": _round(state.cash),
        "base_qty": _round(state.base_qty, 10),
        "position_value": _round(state.base_qty * price),
        "equity": _round(equity),
        "deployable_capital": _round(deployable_capital),
        "realized_pnl": _round(state.realized_pnl),
        "unrealized_pnl": _round(equity - config.initial_capital - state.realized_pnl),
        "return_pct": _round((equity / config.initial_capital) - 1.0, 6),
        "position_pct": _round(_current_position_pct(state, price, equity), 6),
        "equity_high_watermark": _round(equity_hwm),
        "profit_high_watermark": _round(profit_hwm),
        "profit_drawdown": _round(profit_drawdown),
        "profit_drawdown_pct": _round(profit_drawdown_pct, 6),
        "profit_trailing_stop_equity": _round(stop_equity),
        "stop_line_name": state.stop_line_name,
        "stop_line_price": _round(state.stop_line_price),
        "entry_signal_type": state.entry_signal_type,
        "entry_price": _round(state.entry_price),
        "entry_high_price": _round(state.entry_high_price),
        "entry_peak_gain_pct": _round(_entry_peak_gain_pct(state.entry_high_price, state.entry_price), 6),
        "entry_candle_low_price": _round(state.entry_candle_low_price),
        "add_on_entry_low_price": _round(_latest_add_on_entry_low_price(state)),
        "add_on_stop_target_position_pct": _round(_latest_add_on_stop_target_position_pct(state), 6),
        "position_lots": [lot.to_dict() for lot in state.position_lots],
        "take_profit_reduced": state.take_profit_reduced,
        "take_profit_reduce_bars_since": state.take_profit_reduce_bars_since,
        "last_take_profit_reduce_open_time_ms": state.last_take_profit_reduce_open_time_ms,
    }


def _decision(
    row: pd.Series,
    config: CryptoTradingConfig,
    rating: dict[str, Any],
    action: str,
    target_position_pct: float,
    reasons: list[str],
    snapshot: dict[str, Any],
    trade_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "time": _row_time(row),
        "open_time_ms": int(row["open_time_ms"]),
        "symbol": config.symbol,
        "timeframe": config.timeframe,
        "phase": snapshot["phase"],
        "price": snapshot["price"],
        "action": action,
        "target_position_pct": _round(target_position_pct, 6),
        "current_position_pct": snapshot["position_pct"],
        "cash": snapshot["cash"],
        "base_qty": snapshot["base_qty"],
        "position_value": snapshot["position_value"],
        "equity": snapshot["equity"],
        "deployable_capital": snapshot["deployable_capital"],
        "return_pct": snapshot["return_pct"],
        "reason": reasons,
        "trade_plan": trade_plan or {},
        "rating": {
            "channel_regime": rating.get("channel_regime"),
            "channel_strength": rating.get("channel_strength"),
            "scenes": rating.get("scenes", []),
            "ratings": rating.get("ratings", []),
            "risk_flags": rating.get("risk_flags", []),
            "invalidations": rating.get("invalidations", []),
            "metrics": rating.get("metrics", {}),
        },
        "risk": {
            "profit_runner_trigger_pct": config.profit_runner_trigger_pct,
            "profit_drawdown_stop_pct": config.profit_drawdown_stop_pct,
            "equity_high_watermark": snapshot["equity_high_watermark"],
            "profit_high_watermark": snapshot["profit_high_watermark"],
            "profit_trailing_stop_equity": snapshot["profit_trailing_stop_equity"],
            "take_profit_reduced": snapshot["take_profit_reduced"],
            "take_profit_reduce_bars_since": snapshot["take_profit_reduce_bars_since"],
            "last_take_profit_reduce_open_time_ms": snapshot["last_take_profit_reduce_open_time_ms"],
            "entry_price": snapshot["entry_price"],
            "entry_high_price": snapshot["entry_high_price"],
            "entry_peak_gain_pct": snapshot["entry_peak_gain_pct"],
            "entry_candle_low_price": snapshot["entry_candle_low_price"],
            "add_on_entry_low_price": snapshot["add_on_entry_low_price"],
            "add_on_stop_target_position_pct": snapshot["add_on_stop_target_position_pct"],
            "position_lots": snapshot["position_lots"],
        },
    }


def _position_lots_from_snapshot(snapshot: dict[str, Any]) -> list[PositionLot]:
    raw_lots = snapshot.get("position_lots")
    if not isinstance(raw_lots, list):
        return []
    lots: list[PositionLot] = []
    for item in raw_lots:
        if isinstance(item, dict):
            lot = PositionLot.from_dict(item)
            if lot.qty > 0:
                lots.append(lot)
    return lots


def _latest_add_on_lot(state: AccountState) -> PositionLot | None:
    active_lots = [lot for lot in state.position_lots if lot.qty > 1e-12]
    if len(active_lots) <= 1:
        return None
    return active_lots[-1]


def _latest_active_lot(state: AccountState) -> PositionLot | None:
    active_lots = [lot for lot in state.position_lots if lot.qty > 1e-12]
    return active_lots[-1] if active_lots else None


def _latest_take_profit_entry_price(state: AccountState) -> float | None:
    lot = _latest_active_lot(state)
    return lot.entry_price if lot else state.entry_price


def _latest_take_profit_entry_high_price(state: AccountState) -> float | None:
    lot = _latest_active_lot(state)
    return lot.entry_high_price if lot else state.entry_high_price


def _latest_add_on_entry_low_price(state: AccountState) -> float | None:
    lot = _latest_add_on_lot(state)
    return lot.entry_low_price if lot else None


def _latest_add_on_stop_target_position_pct(state: AccountState) -> float | None:
    lot = _latest_add_on_lot(state)
    return lot.position_pct_before if lot else None


def _consume_position_lots(state: AccountState, qty: float) -> None:
    remaining = max(0.0, qty)
    while remaining > 1e-12 and state.position_lots:
        lot = state.position_lots[-1]
        consumed = min(lot.qty, remaining)
        lot.qty -= consumed
        remaining -= consumed
        if lot.qty <= 1e-12:
            state.position_lots.pop()
    if state.base_qty <= 1e-12:
        state.position_lots = []


def _refresh_entry_price_from_lots(state: AccountState) -> None:
    total_qty = sum(lot.qty for lot in state.position_lots if lot.qty > 1e-12)
    if total_qty <= 1e-12:
        state.entry_price = None
        return
    total_cost = sum(lot.qty * lot.entry_price for lot in state.position_lots if lot.qty > 1e-12)
    state.entry_price = total_cost / total_qty


def _advance_take_profit_reduce_clock(state: AccountState) -> None:
    if not state.take_profit_reduced:
        return
    if state.take_profit_reduce_bars_since is None:
        state.take_profit_reduce_bars_since = 0
        return
    state.take_profit_reduce_bars_since += 1


def _take_profit_reduce_blocked(state: AccountState) -> bool:
    return bool(state.take_profit_reduced)


def _update_entry_high_price(state: AccountState, row: pd.Series) -> None:
    if state.base_qty <= 1e-12:
        return
    high = _optional_float(row.get("high"))
    if high is None:
        return
    state.entry_high_price = max(value for value in [state.entry_high_price, high] if value is not None)
    for lot in state.position_lots:
        if lot.qty > 1e-12:
            lot.entry_high_price = max(value for value in [lot.entry_high_price, high] if value is not None)


def _entry_peak_gain_pct(entry_high_price: float | None, entry_price: float | None) -> float | None:
    if entry_price is None or entry_price <= 0 or entry_high_price is None:
        return None
    return (entry_high_price / entry_price) - 1.0


def _write_run_artifacts(
    output_dir: Path,
    config: CryptoTradingConfig,
    frame: pd.DataFrame,
    decisions: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    *,
    mode: str,
) -> None:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(output_dir / "decisions.jsonl", decisions)
        _write_jsonl(output_dir / "trades.jsonl", trades)
        _write_jsonl(output_dir / "account_snapshots.jsonl", snapshots)
        (output_dir / "report.md").write_text(
            _render_report(config, frame, decisions, trades, snapshots, mode),
            encoding="utf-8",
        )
    except OSError as exc:
        raise ReportWriteError(str(exc), payload={"output_dir": str(output_dir)}) from exc


def _append_run_artifacts(
    output_dir: Path,
    config: CryptoTradingConfig,
    frame: pd.DataFrame,
    decisions: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    *,
    mode: str,
) -> None:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        _append_jsonl(output_dir / "decisions.jsonl", decisions)
        _append_jsonl(output_dir / "trades.jsonl", trades)
        _append_jsonl(output_dir / "account_snapshots.jsonl", snapshots)
        (output_dir / "report.md").write_text(
            _render_report(config, frame, decisions, trades, snapshots, mode),
            encoding="utf-8",
        )
    except OSError as exc:
        raise ReportWriteError(str(exc), payload={"output_dir": str(output_dir)}) from exc


def _render_report(
    config: CryptoTradingConfig,
    frame: pd.DataFrame,
    decisions: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    mode: str,
) -> str:
    latest = snapshots[-1] if snapshots else {}
    trade_count = len(trades)
    lines = [
        f"# {config.symbol} {config.timeframe} {config.strategy} {mode} Report",
        "",
        "## Summary",
        "",
        f"- Exchange: {config.exchange} {config.market_type}",
        f"- Strategy: {config.strategy}",
        f"- Initial capital: {config.initial_capital:.2f}",
        f"- Latest equity: {_fmt(latest.get('equity'))}",
        f"- Latest phase: {latest.get('phase', 'N/A')}",
        f"- Return: {_fmt_pct(latest.get('return_pct'))}",
        f"- Underlying return: {_fmt_pct(_underlying_return_pct(frame, snapshots))}",
        f"- Trades: {trade_count}",
        f"- Profit runner trigger: {config.profit_runner_trigger_pct:.2%}",
        f"- Profit drawdown stop: {config.profit_drawdown_stop_pct:.2%}",
        "",
        "## Latest Decision",
        "",
    ]
    if decisions:
        decision = decisions[-1]
        lines.extend(
            [
                f"- Time: {decision.get('time')}",
                f"- Action: {decision.get('action')}",
                f"- Phase: {decision.get('phase')}",
                f"- Target position: {_fmt_pct(decision.get('target_position_pct'))}",
                f"- Current position: {_fmt_pct(decision.get('current_position_pct'))}",
                f"- Channel regime: {(decision.get('rating') or {}).get('channel_regime', 'N/A')}",
                f"- Reason: {'; '.join(str(item) for item in decision.get('reason', []))}",
            ]
        )
    else:
        lines.append("- No new closed bar was processed.")
    lines.extend(
        [
            "",
            "## Agent Files",
            "",
            "- decisions.jsonl: per-bar signal, phase, target position, reasons, and compact strategy context.",
            "- trades.jsonl: simulated fills with fee and slippage assumptions.",
            "- account_snapshots.jsonl: account state and high-watermark fields after every processed closed bar.",
            "",
            "This report is a paper/backtest simulation on historical OHLCV data, not trading advice.",
        ]
    )
    return "\n".join(lines)


def _summary(
    config: CryptoTradingConfig,
    frame: pd.DataFrame,
    decisions: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> dict[str, object]:
    latest = snapshots[-1] if snapshots else {}
    equities = [float(item["equity"]) for item in snapshots if item.get("equity") is not None]
    max_drawdown_pct = _max_drawdown_pct(equities)
    underlying_return_pct = _underlying_return_pct(frame, snapshots)
    return {
        "ok": True,
        "exchange": config.exchange,
        "market_type": config.market_type,
        "symbol": config.symbol,
        "timeframe": config.timeframe,
        "strategy": config.strategy,
        "bar_count": len(frame),
        "processed_bars": len(snapshots),
        "trade_count": len(trades),
        "initial_capital": config.initial_capital,
        "final_equity": latest.get("equity"),
        "return_pct": latest.get("return_pct"),
        "underlying_return_pct": _round(underlying_return_pct, 6),
        "max_drawdown_pct": _round(max_drawdown_pct, 6),
        "latest_phase": latest.get("phase"),
        "latest_open_time_utc": latest.get("time"),
    }


def _underlying_return_pct(frame: pd.DataFrame, snapshots: list[dict[str, Any]]) -> float | None:
    if frame.empty:
        return None
    scope = frame
    if snapshots:
        start_ms = _optional_int(snapshots[0].get("open_time_ms"))
        end_ms = _optional_int(snapshots[-1].get("open_time_ms"))
        if start_ms is not None and end_ms is not None and "open_time_ms" in frame.columns:
            scoped = frame[(frame["open_time_ms"] >= start_ms) & (frame["open_time_ms"] <= end_ms)]
            if not scoped.empty:
                scope = scoped
    first_close = _optional_float(scope.iloc[0].get("close"))
    latest_close = _optional_float(scope.iloc[-1].get("close"))
    if first_close is None or latest_close is None or first_close <= 0:
        return None
    return (latest_close - first_close) / first_close


def _resolve_output_dir(output_dir: str | None, config: CryptoTradingConfig, mode: str) -> Path:
    if output_dir:
        return Path(output_dir)
    return Path("reports") / "crypto_trading" / config.symbol / config.timeframe / mode


def _load_latest_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    latest = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            latest = json.loads(line)
    return latest if isinstance(latest, dict) else None


def _next_unprocessed_index(frame: pd.DataFrame, last_open_time_ms: int | None) -> int | None:
    if last_open_time_ms is None:
        return len(frame) - 1
    for idx, row in frame.iterrows():
        if int(row["open_time_ms"]) > int(last_open_time_ms):
            return int(idx)
    return None


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    content = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    path.write_text(content, encoding="utf-8")


def _append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _equity(state: AccountState, price: float) -> float:
    return state.cash + state.base_qty * price


def _current_position_pct(state: AccountState, price: float, equity: float) -> float:
    if equity <= 0:
        return 0.0
    return (state.base_qty * price) / equity


def _deployable_capital(config: CryptoTradingConfig, equity: float) -> float:
    if equity <= 0:
        return 0.0
    return min(equity, config.initial_capital)


def _max_drawdown_pct(equities: list[float]) -> float:
    peak = 0.0
    max_drawdown = 0.0
    for equity in equities:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    return max_drawdown


def _row_time(row: pd.Series) -> str:
    value = row.get("open_time_utc")
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result):
        return None
    return result


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _round(value: Any, ndigits: int = 2) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return round(number, ndigits)


def _fmt(value: Any) -> str:
    rounded = _round(value)
    return "N/A" if rounded is None else f"{rounded:.2f}"


def _fmt_pct(value: Any) -> str:
    rounded = _round(value, 4)
    return "N/A" if rounded is None else f"{rounded:.2%}"
