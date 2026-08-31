from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import os

from .exceptions import UserInputError


ROOT_DIR = Path(__file__).resolve().parents[2]


def read_env_file(path: str | Path | None) -> dict[str, str]:
    if path is None:
        return {}
    env_path = Path(path)
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _int_value(values: dict[str, str], name: str, default: int) -> int:
    raw = values.get(name) or os.getenv(name)
    return default if raw in (None, "") else int(raw)


@dataclass(frozen=True)
class Settings:
    """crypto-research 运行配置：sqlite 单库（data/crypto.db）。

    存储统一为本地 sqlite（原 MySQL 已废弃）。可通过 env CRYPTO_DB 或
    load_settings 的 database 参数覆盖路径。
    """

    database: str = "data/crypto.db"

    @property
    def sqlite_path(self) -> Path:
        p = Path(self.database)
        return p if p.is_absolute() else (ROOT_DIR / p)

    @property
    def safe_database_label(self) -> str:
        return self.database

    def with_database(self, database: str | None) -> "Settings":
        if not database:
            return self
        return replace(self, database=database)


def load_settings(env_path: str | Path | None = None, database: str | None = None) -> Settings:
    default_env = ROOT_DIR / ".env"
    values = read_env_file(env_path if env_path is not None else default_env)
    db = database or values.get("CRYPTO_DB") or os.getenv("CRYPTO_DB", "data/crypto.db")
    if not db:
        raise UserInputError("CRYPTO_DB must not be empty")
    return Settings(database=db)