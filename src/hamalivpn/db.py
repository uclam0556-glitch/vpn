from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import Settings, get_settings


class Base(DeclarativeBase):
    pass


def make_engine(settings: Settings | None = None) -> AsyncEngine:
    config = settings or get_settings()
    return create_async_engine(
        config.database_url,
        pool_pre_ping=True,
        echo=config.debug,
    )


engine = make_engine()
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


async def create_schema(target_engine: AsyncEngine | None = None) -> None:
    selected_engine = target_engine or engine
    async with selected_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
