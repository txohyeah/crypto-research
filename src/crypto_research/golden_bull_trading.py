from __future__ import annotations

from typing import Any


BULL_MAX_POSITION_PCT = 1.0
BULL_WEAK_MAX_POSITION_PCT = 0.5
BEAR_TRIAL_TARGET_POSITION_PCT = 0.3
BEAR_REBALANCE_TRIGGER_POSITION_PCT = 0.3
BEAR_MAX_POSITION_PCT = BEAR_REBALANCE_TRIGGER_POSITION_PCT
BEAR_SELL_TARGET_POSITION_PCT = 0.2
BULL_MIN_REDUCE_TARGET_POSITION_PCT = 0.2
BULL_TREND_REDUCE_FRACTION = 0.3
BULL_TAKE_PROFIT_REDUCE_FRACTION = 1.0 / 3.0
BEAR_TAKE_PROFIT_REDUCE_FRACTION = 0.5
MAX_STRONG_RECLAIM_GAIN_PCT = 10.0
MAX_BUY_CANDLE_GAIN_PCT = 8.0
MAX_STRONG_RECLAIM_DISTANCE_PCT = 5.0
STRONG_VOLUME_RATIO_MIN = 1.8
STRONG_VOLUME_RATIO_MAX = 2.2
BULL_MATURE_MIN_BARS = 42
MAX_WEAK_BREAKOUT_GAIN_PCT = 5.0
TAKE_PROFIT_DROP_THRESHOLD_FRACTION = 0.5
TAKE_PROFIT_MIN_ENTRY_GAIN_FLOOR_PCT = 1.2
TAKE_PROFIT_MIN_ENTRY_GAIN_VOLATILITY_MULTIPLIER = 1.2


def build_golden_bull_trade_plan(
    rating: dict[str, Any],
    *,
    current_position_pct: float = 0.0,
    stop_line_name: str | None = None,
    stop_line_price: float | None = None,
    take_profit_reduced: bool = False,
    entry_price: float | None = None,
    entry_high_price: float | None = None,
    take_profit_entry_price: float | None = None,
    take_profit_entry_high_price: float | None = None,
    entry_candle_low_price: float | None = None,
    add_on_entry_low_price: float | None = None,
    add_on_stop_target_position_pct: float | None = None,
    low_reburn_point: bool = False,
) -> dict[str, Any]:
    """Resolve Golden Bull analysis into a deterministic trading plan.

    The input remains the generic Golden Bull position-rating payload used by
    stock and crypto. This layer applies stricter trade rules: buys require a
    bullish candle, sells require a bearish candle, and position caps are
    derived from channel regime and signal type.
    """

    metrics = rating.get("metrics") if isinstance(rating.get("metrics"), dict) else {}
    raw_channel_regime = str(rating.get("channel_regime") or "unknown")
    channel_strength = _optional_int(rating.get("channel_strength"))
    candles = _candle_context(metrics)
    line_prices = _line_prices(metrics)
    candles["low_reburn_point"] = bool(low_reburn_point)
    channel_regime = _effective_channel_regime(raw_channel_regime, candles)
    candidates = _signal_candidates(channel_regime, channel_strength, candles, line_prices)

    current_position_pct = max(0.0, float(current_position_pct or 0.0))
    decision = _hold_plan(
        channel_regime=channel_regime,
        channel_strength=channel_strength,
        current_position_pct=current_position_pct,
        candidates=candidates,
        candles=candles,
    )

    take_profit_entry_price = take_profit_entry_price if take_profit_entry_price is not None else entry_price
    take_profit_entry_high_price = (
        take_profit_entry_high_price if take_profit_entry_high_price is not None else entry_high_price
    )
    entry_peak_gain_pct = _entry_peak_gain_pct(entry_high_price, entry_price)
    take_profit_entry_peak_gain_pct = _entry_peak_gain_pct(take_profit_entry_high_price, take_profit_entry_price)
    take_profit_entry_current_high_gain_pct = _entry_peak_gain_pct(candles["high"], take_profit_entry_price)
    take_profit_entry_gain_confirmed = _take_profit_current_high_gain_confirmed(
        candles["high"],
        take_profit_entry_price,
        candles["take_profit_min_entry_gain_pct"],
    )
    take_profit_exit_confirmed = _take_profit_exit_confirmed(
        take_profit_entry_price=take_profit_entry_price,
        take_profit_entry_gain_confirmed=take_profit_entry_gain_confirmed,
        take_profit_drop_confirmed=candles["take_profit_drop_confirmed"],
    )

    if (
        current_position_pct > 0
        and entry_candle_low_price is not None
        and candles["close"] is not None
        and candles["close"] < entry_candle_low_price
    ):
        decision = {
            "action": "sell_clear",
            "side": "sell",
            "signal_type": "entry_candle_low_stop",
            "target_position_pct": 0.0,
            "position_cap_pct": 0.0,
            "stop_line_name": "entry_candle_low",
            "stop_line_price": entry_candle_low_price,
            "reason": ["close below the low of the entry candle"],
        }
    elif (
        current_position_pct > 0
        and add_on_entry_low_price is not None
        and candles["close"] is not None
        and candles["close"] < add_on_entry_low_price
    ):
        decision = {
            "action": "sell_clear",
            "side": "sell",
            "signal_type": "add_on_entry_low_stop",
            "target_position_pct": 0.0,
            "position_cap_pct": 0.0,
            "stop_line_name": "add_on_entry_low",
            "stop_line_price": add_on_entry_low_price,
            "reason": ["close below the low of the latest add-on entry candle; clear position"],
        }
    elif (
        current_position_pct > 0
        and candles["is_bearish"]
        and stop_line_price is not None
        and candles["close"] is not None
        and candles["close"] < stop_line_price
    ):
        decision = {
            "action": "sell_clear",
            "side": "sell",
            "signal_type": "stop_loss_exit",
            "target_position_pct": 0.0,
            "position_cap_pct": 0.0,
            "stop_line_name": stop_line_name,
            "stop_line_price": stop_line_price,
            "reason": [f"bearish close below active stop line {stop_line_name}"],
        }
    elif current_position_pct > 0 and candidates["bear_upper_bearish_sell"]["triggered"]:
        decision = {
            "action": "bear_trend_line_sell",
            "side": "sell",
            "signal_type": "bear_upper_bearish_sell",
            "target_position_pct": min(current_position_pct, BEAR_SELL_TARGET_POSITION_PCT),
            "position_cap_pct": BEAR_SELL_TARGET_POSITION_PCT,
            "stop_line_name": None,
            "stop_line_price": None,
            "reason": ["bear channel candle crosses down through the Golden Bull line; target 20% position"],
        }
    elif channel_regime == "bull" and current_position_pct > 0 and candidates["bull_upper_bearish_reduce"]["triggered"]:
        target = max(BULL_MIN_REDUCE_TARGET_POSITION_PCT, current_position_pct * (1.0 - BULL_TREND_REDUCE_FRACTION))
        target = min(current_position_pct, target)
        decision = {
            "action": "reduce",
            "side": "sell",
            "signal_type": "bull_golden_line_cross_down_reduce",
            "target_position_pct": target,
            "position_cap_pct": BULL_MAX_POSITION_PCT,
            "stop_line_name": stop_line_name,
            "stop_line_price": stop_line_price,
            "reason": ["bull channel candle crosses down through the Golden Bull line; reduce current position by 30%, keeping at least 20%"],
        }
    elif channel_regime == "bull" and candidates["bull_strong_reclaim"]["triggered"]:
        target = BULL_MAX_POSITION_PCT if low_reburn_point else BULL_WEAK_MAX_POSITION_PCT
        decision = {
            "action": "buy_or_hold_full" if target >= BULL_MAX_POSITION_PCT else "buy_or_hold_half",
            "side": "buy",
            "signal_type": "bull_strong_reclaim",
            "target_position_pct": target,
            "position_cap_pct": target,
            "stop_line_name": "golden_bull_2",
            "stop_line_price": line_prices.get("golden_bull_2"),
            "reason": [
                (
                    "bull trend-line reclaim with low Reburn point; target 100% position"
                    if low_reburn_point
                    else "bull trend-line reclaim without low Reburn point; target 50% position"
                )
            ],
        }
    elif candidates["ma20_reburn_reclaim"]["triggered"]:
        decision = {
            "action": "buy_or_hold_light",
            "side": "buy",
            "signal_type": "ma20_reburn_reclaim",
            "target_position_pct": BEAR_TRIAL_TARGET_POSITION_PCT,
            "position_cap_pct": BEAR_TRIAL_TARGET_POSITION_PCT,
            "stop_line_name": "entry_candle_low",
            "stop_line_price": None,
            "reason": ["MA20 reclaim with low Reburn point, close above Golden Bull trend line, and at least one moving average not falling; target 30% position"],
        }
    elif channel_regime == "bear" and candidates["bear_trial_buy"]["triggered"]:
        decision = {
            "action": "bear_trial",
            "side": "buy",
            "signal_type": "bear_trial_buy",
            "target_position_pct": BEAR_TRIAL_TARGET_POSITION_PCT,
            "position_cap_pct": BEAR_TRIAL_TARGET_POSITION_PCT,
            "stop_line_name": "golden_bull_trend",
            "stop_line_price": line_prices.get("life_line"),
            "reason": ["bear strength 1/2 candle reclaims Golden Bull trend line: trial position targets 30%"],
        }

    decision["target_position_pct"] = _cap_target(
        decision.get("target_position_pct"),
        decision.get("position_cap_pct"),
        channel_regime,
    )
    if decision.get("side") == "buy" and current_position_pct >= float(decision["target_position_pct"]) - 0.001:
        decision["action"] = "hold"
        decision["side"] = "hold"
        decision["target_position_pct"] = current_position_pct
        decision["reason"] = list(decision.get("reason", [])) + ["target position already reached"]
    decision["channel_regime"] = channel_regime
    decision["raw_channel_regime"] = raw_channel_regime
    decision["channel_strength"] = channel_strength
    decision["candidates"] = list(candidates.values())
    decision["metrics"] = {
        "open": candles["open"],
        "high": candles["high"],
        "low": candles["low"],
        "close": candles["close"],
        "daily_return_pct": candles["daily_return_pct"],
        "volume_vs_prev_ratio": candles["volume_vs_prev_ratio"],
        "volume_vs_prev_pct": candles["volume_vs_prev_pct"],
        "bull_regime_age": candles["bull_regime_age"],
        "ma60_slope_pct": candles["ma60_slope_pct"],
        "ma20_slope_pct": candles["ma20_slope_pct"],
        "ma60_rising": candles["ma60_rising"],
        "ma20_not_falling": candles["ma20_not_falling"],
        "ma60_not_falling": candles["ma60_not_falling"],
        "buy_upper_room_allowed": _buy_upper_room_allowed(candles, line_prices),
        "open_close_gain_pct": candles["open_close_gain_pct"],
        "ma20_ma60_spread_pct": candles["ma20_ma60_spread_pct"],
        "prev_ma20_ma60_spread_pct": candles["prev_ma20_ma60_spread_pct"],
        "ma20_ma60_spread_widening": candles["ma20_ma60_spread_widening"],
        "avg_abs_return_10_pct": candles["avg_abs_return_10_pct"],
        "take_profit_drop_threshold_pct": candles["take_profit_drop_threshold_pct"],
        "take_profit_drop_confirmed": candles["take_profit_drop_confirmed"],
        "take_profit_min_entry_gain_pct": candles["take_profit_min_entry_gain_pct"],
        "entry_price": entry_price,
        "entry_high_price": entry_high_price,
        "entry_peak_gain_pct": entry_peak_gain_pct,
        "take_profit_entry_price": take_profit_entry_price,
        "take_profit_entry_high_price": take_profit_entry_high_price,
        "take_profit_entry_peak_gain_pct": take_profit_entry_peak_gain_pct,
        "take_profit_entry_current_high_gain_pct": take_profit_entry_current_high_gain_pct,
        "entry_peak_gain_confirmed": take_profit_entry_gain_confirmed,
        "take_profit_exit_confirmed": take_profit_exit_confirmed,
        "entry_candle_low_price": entry_candle_low_price,
        "add_on_entry_low_price": add_on_entry_low_price,
        "add_on_stop_target_position_pct": add_on_stop_target_position_pct,
        "low_reburn_point": bool(low_reburn_point),
        "is_bullish": candles["is_bullish"],
        "is_bearish": candles["is_bearish"],
        "distance_to_upper_pct": candles["distance_to_upper_pct"],
        **line_prices,
    }
    return decision


def _signal_candidates(
    channel_regime: str,
    channel_strength: int | None,
    candles: dict[str, Any],
    lines: dict[str, float | None],
) -> dict[str, dict[str, Any]]:
    upper = lines.get("upper_line")
    life_line = lines.get("life_line")
    close = candles["close"]
    low = candles["low"]
    high = candles["high"]
    near_upper = _close_is_near_upper(channel_regime, candles, lines)
    trend_reclaim = (
        candles["is_bullish"]
        and low is not None
        and close is not None
        and life_line is not None
        and low < life_line
        and close > life_line
        and _buy_candle_gain_allowed(candles)
        and _buy_upper_room_allowed(candles, lines)
    )
    bear_trial_reclaim = (
        candles["is_bullish"]
        and low is not None
        and close is not None
        and life_line is not None
        and low < life_line
        and close > life_line
        and _buy_candle_gain_allowed(candles)
        and _buy_upper_room_allowed(candles, lines)
    )
    ma20_reclaim = _ma20_reburn_reclaim_confirmed(candles, lines)

    return {
        "bull_strong_reclaim": {
            "type": "bull_strong_reclaim",
            "triggered": channel_regime == "bull" and trend_reclaim,
            "reason": "bull channel candle crosses the Golden Bull trend line from below; target 50%, or 100% when low Reburn point is present",
        },
        "bull_weak_reclaim": {
            "type": "bull_weak_reclaim",
            "triggered": False,
            "reason": "disabled: bull trend-line reclaim now maps to one target rule",
        },
        "bull_ma20_pullback_strong": {
            "type": "bull_ma20_pullback_strong",
            "triggered": False,
            "reason": "disabled: MA20 pullback is replaced by MA20 Reburn reclaim",
        },
        "bull_ma20_pullback_weak": {
            "type": "bull_ma20_pullback_weak",
            "triggered": False,
            "reason": "disabled: MA20 pullback is replaced by MA20 Reburn reclaim",
        },
        "ma20_reburn_reclaim": {
            "type": "ma20_reburn_reclaim",
            "triggered": ma20_reclaim,
            "reason": "low Reburn point crosses MA20 from below, closes above the Golden Bull trend line, has upper-line room, and at least one of MA20/MA60 has not fallen for three bars",
        },
        "bull_upper_chase_weak": {
            "type": "bull_upper_chase_weak",
            "triggered": False,
            "reason": "disabled: upper-line chase buy is not used",
        },
        "bull_weak_buy": {
            "type": "bull_weak_buy",
            "triggered": False,
            "reason": "disabled: only strict bull_strong_reclaim can buy",
        },
        "bull_upper_bearish_reduce": {
            "type": "bull_upper_bearish_reduce",
            "triggered": (
                channel_regime == "bull"
                and _cross_down(candles, upper)
            ),
            "reason": "bull channel candle opens above and closes below the Golden Bull line",
        },
        "bear_trial_buy": {
            "type": "bear_trial_buy",
            "triggered": channel_regime == "bear" and channel_strength in {1, 2} and bear_trial_reclaim,
            "reason": "bear strength 1/2 candle crosses the Golden Bull trend line from below; target position 30%",
        },
        "bear_upper_bearish_sell": {
            "type": "bear_upper_bearish_sell",
            "triggered": (
                channel_regime == "bear"
                and _cross_down(candles, upper)
            ),
            "reason": "bear channel candle opens above and closes below the Golden Bull line; target position 20%",
        },
    }


def _close_is_near_upper(
    channel_regime: str,
    candles: dict[str, Any],
    lines: dict[str, float | None],
) -> bool:
    close = candles["close"]
    upper = lines.get("upper_line")
    if close is None or upper is None:
        return False
    if close >= upper:
        return True

    if channel_regime == "bull":
        compare_line = lines.get("life_line")
    elif channel_regime == "bear":
        compare_line = lines.get("golden_bull_2")
    else:
        compare_line = None

    if compare_line is not None and upper != compare_line:
        return abs(upper - close) <= abs(close - compare_line)

    distance_to_upper_pct = candles["distance_to_upper_pct"]
    return distance_to_upper_pct is not None and -2.0 <= distance_to_upper_pct <= 3.0


def _bull_ma20_pullback_confirmed(candles: dict[str, Any], lines: dict[str, float | None]) -> bool:
    close = candles["close"]
    low = candles["low"]
    ma20 = lines.get("ma20")
    ma60 = lines.get("ma60")
    upper = lines.get("upper_line")
    trend_line = lines.get("life_line")
    if trend_line is None:
        trend_line = lines.get("golden_bull_trend")
    if not candles["is_bullish"] or None in (close, low, ma20, ma60, trend_line):
        return False
    if ma20 <= ma60:
        return False
    if upper is not None and close > upper:
        return False
    support_floor = min(ma20, trend_line)
    if close < support_floor:
        return False
    closest_line = _closest_line_name(
        low,
        {
            "ma20": ma20,
            "ma60": ma60,
            "upper_line": upper,
            "golden_bull_trend": trend_line,
            "golden_bull_2": lines.get("golden_bull_2"),
            "golden_bull": lines.get("golden_bull"),
        },
    )
    if closest_line == "upper_line":
        return False
    return True


def _ma20_reburn_reclaim_confirmed(candles: dict[str, Any], lines: dict[str, float | None]) -> bool:
    close = candles["close"]
    low = candles["low"]
    ma20 = lines.get("ma20")
    trend_line = lines.get("life_line") or lines.get("golden_bull_trend")
    if not candles.get("low_reburn_point"):
        return False
    if None in (close, low, ma20, trend_line):
        return False
    if not _buy_candle_gain_allowed(candles):
        return False
    if not _buy_upper_room_allowed(candles, lines):
        return False
    if not bool(candles["ma20_not_falling"] or candles["ma60_not_falling"]):
        return False
    return bool(low < ma20 and close > ma20 and close > trend_line)


def _cross_down(candles: dict[str, Any], line_price: float | None) -> bool:
    open_ = candles["open"]
    close = candles["close"]
    if None in (open_, close, line_price):
        return False
    return bool(open_ > line_price and close < line_price)


def _buy_candle_gain_allowed(candles: dict[str, Any]) -> bool:
    gain = candles.get("open_close_gain_pct")
    return gain is None or gain <= MAX_BUY_CANDLE_GAIN_PCT


def _buy_upper_room_allowed(candles: dict[str, Any], lines: dict[str, float | None]) -> bool:
    high = candles.get("high")
    upper = lines.get("upper_line")
    if high is None or upper is None:
        return True
    return bool(high <= upper)


def _closest_line_name(price: float | None, lines: dict[str, float | None]) -> str | None:
    if price is None:
        return None
    distances = {
        name: abs(price - value)
        for name, value in lines.items()
        if value is not None
    }
    if not distances:
        return None
    return min(distances, key=distances.get)


def _strong_volume_confirmed(volume_vs_prev_ratio: float | None) -> bool:
    return (
        volume_vs_prev_ratio is not None
        and STRONG_VOLUME_RATIO_MIN <= volume_vs_prev_ratio <= STRONG_VOLUME_RATIO_MAX
    )


def _hold_plan(
    *,
    channel_regime: str,
    channel_strength: int | None,
    current_position_pct: float,
    candidates: dict[str, dict[str, Any]],
    candles: dict[str, Any],
) -> dict[str, Any]:
    if channel_regime == "bear" and current_position_pct > BEAR_REBALANCE_TRIGGER_POSITION_PCT and candles["is_bearish"]:
        return {
            "action": "bear_cap",
            "side": "sell",
            "signal_type": "bear_position_cap",
            "target_position_pct": BEAR_SELL_TARGET_POSITION_PCT,
            "position_cap_pct": BEAR_REBALANCE_TRIGGER_POSITION_PCT,
            "stop_line_name": None,
            "stop_line_price": None,
            "reason": [
                f"bear channel position above {BEAR_REBALANCE_TRIGGER_POSITION_PCT:.0%}; rebalance to {BEAR_SELL_TARGET_POSITION_PCT:.0%}"
            ],
        }
    return {
        "action": "hold" if current_position_pct > 0 else "wait",
        "side": "hold",
        "signal_type": "no_trade",
        "target_position_pct": current_position_pct,
        "position_cap_pct": BULL_MAX_POSITION_PCT if channel_regime == "bull" else BEAR_REBALANCE_TRIGGER_POSITION_PCT,
        "stop_line_name": None,
        "stop_line_price": None,
        "reason": ["no confirmed Golden Bull trade setup"],
    }


def _candle_context(metrics: dict[str, Any]) -> dict[str, Any]:
    open_ = _optional_float(metrics.get("open"))
    high = _optional_float(metrics.get("high"))
    low = _optional_float(metrics.get("low"))
    close = _optional_float(metrics.get("close"))
    daily_return_pct = _optional_float(metrics.get("daily_return_pct"))
    avg_abs_return_10_pct = _optional_float(metrics.get("avg_abs_return_10_pct"))
    take_profit_drop_threshold_pct = (
        avg_abs_return_10_pct * TAKE_PROFIT_DROP_THRESHOLD_FRACTION
        if avg_abs_return_10_pct is not None
        else None
    )
    take_profit_min_entry_gain_pct = _take_profit_min_entry_gain_pct(avg_abs_return_10_pct)
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "daily_return_pct": daily_return_pct,
        "distance_to_upper_pct": _optional_float(metrics.get("distance_to_upper_pct")),
        "volume_vs_prev_ratio": _optional_float(metrics.get("volume_vs_prev_ratio")),
        "volume_vs_prev_pct": _optional_float(metrics.get("volume_vs_prev_pct")),
        "close_above_upper_days": _optional_int(metrics.get("close_above_upper_days")),
        "bull_regime_age": _optional_int(metrics.get("bull_regime_age")),
        "ma60_slope_pct": _optional_float(metrics.get("ma60_slope_pct")),
        "ma20_slope_pct": _optional_float(metrics.get("ma20_slope_pct")),
        "ma60_rising": _optional_float(metrics.get("ma60_slope_pct")) is not None
        and _optional_float(metrics.get("ma60_slope_pct")) > 0,
        "ma20_not_falling": _ma_not_falling_three_bars(metrics, "ma20"),
        "ma60_not_falling": _ma_not_falling_three_bars(metrics, "ma60"),
        "open_close_gain_pct": _open_close_gain_pct(open_, close),
        "ma20_ma60_spread_pct": _optional_float(metrics.get("ma20_ma60_spread_pct")),
        "prev_ma20_ma60_spread_pct": _optional_float(metrics.get("prev_ma20_ma60_spread_pct")),
        "ma20_ma60_spread_widening": bool(metrics.get("ma20_ma60_spread_widening")),
        "avg_abs_return_10_pct": avg_abs_return_10_pct,
        "take_profit_drop_threshold_pct": take_profit_drop_threshold_pct,
        "take_profit_drop_confirmed": _take_profit_drop_confirmed(daily_return_pct, take_profit_drop_threshold_pct),
        "take_profit_min_entry_gain_pct": take_profit_min_entry_gain_pct,
        "is_bullish": open_ is not None and close is not None and close > open_,
        "is_bearish": open_ is not None and close is not None and close < open_,
    }


def _take_profit_drop_confirmed(daily_return_pct: float | None, threshold_pct: float | None) -> bool:
    if threshold_pct is None:
        return True
    if daily_return_pct is None:
        return False
    return daily_return_pct < 0 and abs(daily_return_pct) > threshold_pct


def _open_close_gain_pct(open_: float | None, close: float | None) -> float | None:
    if open_ is None or open_ <= 0 or close is None:
        return None
    return (close / open_ - 1.0) * 100.0


def _ma_not_falling_three_bars(metrics: dict[str, Any], name: str) -> bool:
    current = _optional_float(metrics.get(name))
    previous = _optional_float(metrics.get(f"prev_{name}"))
    previous_2 = _optional_float(metrics.get(f"prev2_{name}"))
    if current is not None and previous is not None and previous_2 is not None:
        return current >= previous and previous >= previous_2

    slope = _optional_float(metrics.get(f"{name}_slope_pct"))
    if slope is not None:
        return slope >= 0
    if current is None or previous is None:
        return False
    return current >= previous


def _entry_peak_gain_pct(entry_high_price: float | None, entry_price: float | None) -> float | None:
    if entry_price is None or entry_price <= 0:
        return None
    if entry_high_price is None:
        return None
    return (entry_high_price / entry_price - 1.0) * 100.0


def _take_profit_min_entry_gain_pct(avg_abs_return_10_pct: float | None) -> float:
    if avg_abs_return_10_pct is None:
        return TAKE_PROFIT_MIN_ENTRY_GAIN_FLOOR_PCT
    return max(
        TAKE_PROFIT_MIN_ENTRY_GAIN_FLOOR_PCT,
        avg_abs_return_10_pct * TAKE_PROFIT_MIN_ENTRY_GAIN_VOLATILITY_MULTIPLIER,
    )


def _take_profit_current_high_gain_confirmed(
    current_high_price: float | None,
    entry_price: float | None,
    threshold_pct: float | None,
) -> bool:
    if entry_price is None or entry_price <= 0:
        return True
    current_high_gain_pct = _entry_peak_gain_pct(current_high_price, entry_price)
    if current_high_gain_pct is None:
        return False
    return current_high_gain_pct > (threshold_pct or TAKE_PROFIT_MIN_ENTRY_GAIN_FLOOR_PCT)


def _take_profit_exit_confirmed(
    *,
    take_profit_entry_price: float | None,
    take_profit_entry_gain_confirmed: bool,
    take_profit_drop_confirmed: bool,
) -> bool:
    if take_profit_entry_price is not None and take_profit_entry_price > 0:
        return take_profit_entry_gain_confirmed
    return take_profit_drop_confirmed


def _effective_channel_regime(raw_channel_regime: str, candles: dict[str, Any]) -> str:
    if raw_channel_regime == "bull" and candles["ma60_slope_pct"] is not None and not candles["ma60_rising"]:
        return "bear"
    return raw_channel_regime


def _line_prices(metrics: dict[str, Any]) -> dict[str, float | None]:
    life_line = _optional_float(metrics.get("bull_bear_boundary"))
    if life_line is None:
        life_line = _optional_float(metrics.get("golden_bull_trend"))
    return {
        "upper_line": _optional_float(metrics.get("upper_line") or metrics.get("golden_bull")),
        "golden_bull": _optional_float(metrics.get("golden_bull")),
        "life_line": life_line,
        "golden_bull_trend": _optional_float(metrics.get("golden_bull_trend")),
        "golden_bull_2": _optional_float(metrics.get("golden_bull_2")),
        "lower_line": _optional_float(metrics.get("lower_line")),
        "ma20": _optional_float(metrics.get("ma20")),
        "ma60": _optional_float(metrics.get("ma60")),
    }


def _cap_target(target: Any, cap: Any, channel_regime: str) -> float:
    target_value = max(0.0, float(target or 0.0))
    cap_value = float(cap if cap is not None else BULL_MAX_POSITION_PCT)
    if channel_regime == "bear":
        cap_value = min(cap_value, BEAR_REBALANCE_TRIGGER_POSITION_PCT)
    return min(target_value, cap_value)


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _optional_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
