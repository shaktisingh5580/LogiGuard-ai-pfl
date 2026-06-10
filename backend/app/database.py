"""Async SQLAlchemy engine, session factory, and declarative base.

Usage in request handlers::

    from app.database import get_session

    async def my_endpoint(session: AsyncSession = Depends(get_session)):
        ...
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

# ── Naming conventions for Alembic auto-generation ────────────
_NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Application-wide declarative base with consistent naming."""

    metadata = MetaData(naming_convention=_NAMING_CONVENTION)


def _build_engine():  # noqa: ANN202
    settings = get_settings()
    return create_async_engine(
        settings.DATABASE_URL,
        echo=settings.APP_DEBUG,
        future=True,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
    )


engine = _build_engine()

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a transactional async session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
