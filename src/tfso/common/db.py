"""SQLAlchemy engine/session helpers. SQLite locally, Azure Database for PostgreSQL in the cloud."""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import JSON, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# JSONB on PostgreSQL (indexable), JSON elsewhere.
JsonDoc = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        path = url.split("///", 1)[-1]
        if path and path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if path == ":memory:" or url == "sqlite://":
            from sqlalchemy.pool import StaticPool
            kwargs["poolclass"] = StaticPool
    else:
        kwargs.update(pool_size=10, max_overflow=20, pool_recycle=1800)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragma(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    return engine


class Database:
    def __init__(self, url: str, metadata=None):
        self.engine = make_engine(url)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        (metadata or Base.metadata).create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self.SessionLocal()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def ping(self) -> bool:
        from sqlalchemy import text
        with self.engine.connect() as c:
            c.execute(text("select 1"))
        return True
