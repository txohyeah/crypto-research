class StockResearchError(Exception):
    """Base exception with an agent-readable error code and exit status."""

    error_code = "INTERNAL_ERROR"
    exit_code = 5
    hint = ""

    def __init__(self, message: str, *, hint: str | None = None, payload: dict[str, object] | None = None) -> None:
        super().__init__(message)
        if hint is not None:
            self.hint = hint
        self.payload = payload or {}


class UserInputError(StockResearchError):
    error_code = "INVALID_ARGUMENT"
    exit_code = 1


class DatabaseConnectionError(StockResearchError):
    error_code = "DATABASE_CONNECTION_FAILED"
    exit_code = 2
    hint = "Check sqlite path/database or Binance network access"


class DataInsufficientError(StockResearchError):
    error_code = "DATA_INSUFFICIENT"
    exit_code = 3


class ReportWriteError(StockResearchError):
    error_code = "REPORT_WRITE_FAILED"
    exit_code = 4
