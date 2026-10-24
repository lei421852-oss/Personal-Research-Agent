"""Alembic 环境：连接串只来自 H2_MIGRATION_DATABASE_URL，应用启动不调用本文件。

由独立命令行进程执行，使用 AsyncEngine + asyncpg，并以 run_sync 桥接同步 API。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.persistence.migrations_url import migration_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def target_metadata():
    # 只在迁移进程内导入模型；应用运行时不受影响。
    from app.persistence.models import Base

    return Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=migration_url(),
        target_metadata=target_metadata(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata(),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(migration_url(), pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
