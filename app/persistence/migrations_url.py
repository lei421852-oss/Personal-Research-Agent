"""迁移连接串的唯一来源；与运行期配置完全分离。

迁移使用独立账号与独立变量，绝不回退到 H2_DATABASE_URL。
"""

from __future__ import annotations

import os
from collections.abc import Mapping

ENV_VAR = "H2_MIGRATION_DATABASE_URL"
REQUIRED_SCHEME = "postgresql+asyncpg://"


def migration_url(environment: Mapping[str, str] | None = None) -> str:
    source = os.environ if environment is None else environment
    url = (source.get(ENV_VAR) or "").strip()
    if not url:
        raise RuntimeError(
            f"{ENV_VAR} is required; migrations never fall back to the "
            "application connection string"
        )
    if not url.startswith(REQUIRED_SCHEME):
        raise RuntimeError(f"migrations require a {REQUIRED_SCHEME} URL")
    return url
