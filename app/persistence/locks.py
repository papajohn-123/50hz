from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


SessionFactory = Callable[[], AsyncSession]


def advisory_lock_key(lock_name: str) -> int:
    """Map a human-readable lock name to PostgreSQL's signed bigint space."""

    if not lock_name:
        raise ValueError("lock name cannot be empty")
    digest = hashlib.blake2b(
        lock_name.encode("utf-8"), digest_size=8, person=b"50hzlock"
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


class PostgresAdvisoryLockProvider:
    """Own a session-level advisory lock for the lifetime of the context."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def acquire(self, lock_name: str) -> AsyncIterator[bool]:
        lock_key = advisory_lock_key(lock_name)
        async with self._session_factory() as session:
            result = await session.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": lock_key},
            )
            acquired = bool(result.scalar_one())
            try:
                yield acquired
            finally:
                if acquired:
                    await session.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"),
                        {"lock_key": lock_key},
                    )

