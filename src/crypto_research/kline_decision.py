from __future__ import annotations

from typing import Any

import pandas as pd

from .golden_bull_trading import build_golden_bull_trade_plan
from .reburn import reburn_signal


def build_unified_kline_trade_plan(
    rating: dict[str, Any],
    history: pd.DataFrame,
    *,
    current_position_pct: float = 0.0,
    timeframe_label: str = "1d",
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
) -> dict[str, Any]:
    """Build the shared stock/crypto K-line decision plan.

    Golden Bull still supplies sell/stop context and candidate diagnostics.
    Buy-side decisions are gated by the low Reburn point so every K-line
    consumer interprets entries the same way.
    """

    trade_plan = build_golden_bull_trade_plan(
        rating,
        current_position_pct=current_position_pct,
        stop_line_name=stop_line_name,
        stop_line_price=stop_line_price,
        take_profit_reduced=take_profit_reduced,
        entry_price=entry_price,
        entry_high_price=entry_high_price,
        take_profit_entry_price=take_profit_entry_price,
        take_profit_entry_high_price=take_profit_entry_high_price,
        entry_candle_low_price=entry_candle_low_price,
        add_on_entry_low_price=add_on_entry_low_price,
        add_on_stop_target_position_pct=add_on_stop_target_position_pct,
        low_reburn_point=reburn_signal(history),
    )
    return trade_plan
