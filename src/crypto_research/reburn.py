from __future__ import annotations

from typing import Any

import pandas as pd


REBURN_WEAK_TARGET_POSITION_PCT = 0.50
REBURN_STRONG_TARGET_POSITION_PCT = 1.0
REBURN_RISK_CAP_POSITION_PCT = 0.20
REBURN_MA_SLOPE_LOOKBACK_BARS = 3


def build_reburn_buy_trade_plan(
    history: pd.DataFrame,
    rating: dict[str, Any],
    *,
    current_position_pct: float,
    fallback_trade_plan: dict[str, Any],
    timeframe_label: str = "4h",
) -> dict[str, Any]:
    label = timeframe_label or "K-line"
    if len(history) < 62:
        return {
            **fallback_trade_plan,
            "reburn_signal": None,
            "reburn_risk": {"insufficient_history": True},
            "reason": list(fallback_trade_plan.get("reason", []))
            + [f"insufficient {label} history for Reburn point; legacy plan kept"],
        }
    raw_channel_regime = str(rating.get("channel_regime") or "unknown")
    risk = reburn_risk_context(history, raw_channel_regime)
    signal = reburn_signal(history)
    strong_volume = reburn_strong_volume(history)
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
            "volume_vs_prev_ratio": reburn_volume_vs_prev_ratio(history),
        },
        "candidates": list(fallback_trade_plan.get("candidates", [])),
    }
    if not signal:
        return {
            **base_plan,
            "reason": [f"no {label} Reburn point; legacy Golden Bull buy signal ignored"],
        }
    if risk["risk_count"] >= 2:
        return {
            **base_plan,
            "reason": [
                f"{label} Reburn point appeared, but two or more risk filters are active; buy ignored",
                *reburn_risk_reasons(risk),
            ],
        }

    target = REBURN_STRONG_TARGET_POSITION_PCT if strong_volume else REBURN_WEAK_TARGET_POSITION_PCT
    signal_type = f"reburn_{label}_strong_buy" if strong_volume else f"reburn_{label}_weak_buy"
    if risk["risk_count"] == 1:
        target = min(target, REBURN_RISK_CAP_POSITION_PCT)
    if current_position_pct >= target - 0.001:
        return {
            **base_plan,
            "target_position_pct": current_position_pct,
            "position_cap_pct": target,
            "signal_type": signal_type,
            "reason": [
                f"{label} Reburn point confirmed, but target position already reached",
                *reburn_risk_reasons(risk),
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
            f"{label} Reburn point confirmed",
            (
                "strong volume: latest volume is 1.8~2.2x previous bar"
                if strong_volume
                else "weak volume: latest volume is outside 1.8~2.2x previous bar"
            ),
            *reburn_risk_reasons(risk),
        ],
    }


def reburn_risk_context(history: pd.DataFrame, raw_channel_regime: str) -> dict[str, Any]:
    ma20_down = _ma_down(history, 20, REBURN_MA_SLOPE_LOOKBACK_BARS)
    ma60_down = _ma_down(history, 60, REBURN_MA_SLOPE_LOOKBACK_BARS)
    bear = raw_channel_regime == "bear"
    return {
        "bear": bear,
        "ma20_down": ma20_down,
        "ma60_down": ma60_down,
        "risk_count": int(bear) + int(ma20_down) + int(ma60_down),
    }


def reburn_risk_reasons(risk: dict[str, Any]) -> list[str]:
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


def reburn_strong_volume(history: pd.DataFrame) -> bool:
    ratio = reburn_volume_vs_prev_ratio(history)
    return ratio is not None and 1.8 <= ratio <= 2.2


def reburn_volume_vs_prev_ratio(history: pd.DataFrame) -> float | None:
    if len(history) < 2 or "vol" not in history:
        return None
    latest = _optional_float(history.iloc[-1].get("vol"))
    previous = _optional_float(history.iloc[-2].get("vol"))
    if latest is None or previous in (None, 0):
        return None
    return latest / previous


def reburn_signal(history: pd.DataFrame) -> bool:
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


def _ma_down(history: pd.DataFrame, period: int, lookback: int) -> bool:
    if len(history) < period + lookback:
        return False
    ma = history["close"].astype(float).rolling(period, min_periods=period).mean()
    latest = ma.iloc[-1]
    previous = ma.iloc[-1 - lookback]
    if pd.isna(latest) or pd.isna(previous):
        return False
    return bool(latest < previous)


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


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None
