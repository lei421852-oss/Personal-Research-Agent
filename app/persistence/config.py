"""独立读取H2配置；禁用时不读取连接串，也不导入SQLAlchemy。"""

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.persistence.errors import DatabaseConfigurationError, StorageError

ENV_FIELDS = {
    "pool_size": "H2_DATABASE_POOL_SIZE",
    "max_overflow": "H2_DATABASE_MAX_OVERFLOW",
    "pool_timeout": "H2_DATABASE_POOL_TIMEOUT",
    "connect_timeout": "H2_DATABASE_CONNECT_TIMEOUT",
    "command_timeout": "H2_DATABASE_COMMAND_TIMEOUT",
    "health_timeout": "H2_DATABASE_HEALTH_TIMEOUT",
}


class DatabaseSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    enabled: bool = False
    url: SecretStr | None = Field(default=None, repr=False)
    pool_size: int = Field(default=2, ge=1, le=10)
    max_overflow: int = Field(default=0, ge=0, le=10)
    pool_timeout: float = Field(default=5, gt=0, le=60, allow_inf_nan=False)
    connect_timeout: float = Field(default=5, gt=0, le=60, allow_inf_nan=False)
    command_timeout: float = Field(default=5, gt=0, le=60, allow_inf_nan=False)
    health_timeout: float = Field(default=5, gt=0, le=60, allow_inf_nan=False)

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None):
        source = os.environ if environment is None else environment
        raw = source.get("H2_DATABASE_ENABLED", "false").strip().lower()
        if raw not in {"true", "false", "1", "0"}:
            raise DatabaseConfigurationError()
        if raw in {"false", "0"}:
            return cls()
        values = {"enabled": True, "url": source.get("H2_DATABASE_URL")}
        for field, key in ENV_FIELDS.items():
            if key in source:
                values[field] = source[key]
        try:
            result = cls(**values)
        except (ValidationError, TypeError, ValueError):
            raise DatabaseConfigurationError() from None
        if result.url is None or not result.url.get_secret_value().strip():
            raise DatabaseConfigurationError()
        return result

    def connection_options(self):
        if not self.enabled:
            raise StorageError("storage_disabled")
        try:
            from sqlalchemy.engine import make_url
        except ImportError:
            raise StorageError("storage_dependency_missing") from None
        try:
            if self.url is None:
                raise ValueError
            parsed = make_url(self.url.get_secret_value())
            if parsed.drivername != "postgresql+asyncpg":
                raise ValueError
            if not all(
                (parsed.host, parsed.database, parsed.username, parsed.password)
            ):
                raise ValueError
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError
            if set(parsed.query) - {"ssl"}:
                raise ValueError
            ssl = parsed.query.get("ssl", "require")
            if ssl not in {"disable", "require", "verify-full"}:
                raise ValueError
            # 由受控参数传给驱动，不允许URL覆盖池和超时设置。
            return parsed.set(port=parsed.port or 5432, query={}), {
                "ssl": ssl,
                "timeout": self.connect_timeout,
                "command_timeout": self.command_timeout,
            }
        except Exception:  # noqa: BLE001 - 连接串异常仅保留安全配置错误
            raise DatabaseConfigurationError() from None
