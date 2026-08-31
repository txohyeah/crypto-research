from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StockCode:
    raw: str
    code: str
    ts_code: str


@dataclass(frozen=True)
class PositionInput:
    code: StockCode
    cost_price: float | None = None
    position_size: str | None = None
    buy_date: str | None = None
    position_type: str | None = None
    thesis: str | None = None
    notes: str | None = None

    def to_context(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "cost_price": self.cost_price,
                "position_size": self.position_size,
                "buy_date": self.buy_date,
                "position_type": self.position_type,
                "thesis": self.thesis,
                "notes": self.notes,
            }.items()
            if value not in (None, "")
        }


@dataclass(frozen=True)
class MissingDataItem:
    dataset: str
    ts_code: str | None
    start_date: str
    end_date: str
    reason: str
    actual_count: int | None = None
    required_count: int | None = None


@dataclass(frozen=True)
class MissingDataContract:
    status: str
    analysis_type: str
    required: dict[str, Any]
    missing: list[MissingDataItem]
    reason: str
    retryable: bool
    suggested_command: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "analysis_type": self.analysis_type,
            "required": self.required,
            "missing": [
                {
                    key: value
                    for key, value in {
                        "dataset": item.dataset,
                        "ts_code": item.ts_code,
                        "start_date": item.start_date,
                        "end_date": item.end_date,
                        "reason": item.reason,
                        "actual_count": item.actual_count,
                        "required_count": item.required_count,
                    }.items()
                    if value is not None
                }
                for item in self.missing
            ],
            "reason": self.reason,
            "retryable": self.retryable,
            "suggested_command": self.suggested_command,
        }


@dataclass
class RuleResult:
    name: str
    passed: bool
    reason: str
    weight: int = 0
    value: Any = None
    status: str = "passed"


@dataclass
class StrategyEvaluation:
    code: str
    ts_code: str
    name: str
    score: int
    grade: str
    bucket: str
    close: float | None
    pct_chg: float | None
    indicators: dict[str, Any]
    rule_results: list[RuleResult] = field(default_factory=list)
    core_rule_results: list[RuleResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    exclude_reasons: list[str] = field(default_factory=list)

    @property
    def hit_reasons(self) -> list[str]:
        return [r.reason for r in self.rule_results if r.passed and r.weight > 0]

    @property
    def penalty_reasons(self) -> list[str]:
        return [r.reason for r in self.rule_results if r.passed and r.weight < 0]
