"""Transactional outbox + idempotent inbox, shared by the Process and System APIs."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .bus import Broker, EventStream
from .db import Database, JsonDoc
from .util import utcnow

log = logging.getLogger("tfso.outbox")


def make_models(Base):  # noqa: N803 - factory so each service keeps its own metadata
    class OutboxMessage(Base):
        __tablename__ = "outbox"
        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        message_id: Mapped[str] = mapped_column(String(64), unique=True)
        topic: Mapped[str] = mapped_column(String(80), index=True)
        envelope: Mapped[dict] = mapped_column(JsonDoc)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
        dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
        attempts: Mapped[int] = mapped_column(Integer, default=0)
        next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
        last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    class InboxMessage(Base):
        __tablename__ = "inbox"
        message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
        topic: Mapped[str] = mapped_column(String(80))
        processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    return OutboxMessage, InboxMessage


class Outbox:
    def __init__(self, db: Database, model, inbox_model, broker: Broker, stream: EventStream | None = None,
                 stream_topics: tuple[str, ...] = ()):
        self.db, self.model, self.inbox_model, self.broker = db, model, inbox_model, broker
        self.stream, self.stream_topics = stream, stream_topics
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def add(self, session: Session, topic: str, envelope: dict[str, Any]) -> None:
        session.add(self.model(message_id=envelope["id"], topic=topic, envelope=envelope))

    def kick(self) -> None:
        self._wake.set()

    def processed(self, message_id: str) -> bool:
        """Idempotent consumer check (read side)."""
        if not message_id:
            return False
        with self.db.session() as s:
            return s.get(self.inbox_model, message_id) is not None

    def mark_processed(self, message_id: str, topic: str, session: Session | None = None) -> None:
        """Record a message as handled. Call only after the handler's effects are committed (or pass the
        session that commits them) so a failed handler is retried on redelivery."""
        if not message_id:
            return
        if session is not None:
            if session.get(self.inbox_model, message_id) is None:
                session.add(self.inbox_model(message_id=message_id, topic=topic))
            return
        with self.db.session() as s:
            if s.get(self.inbox_model, message_id) is None:
                s.add(self.inbox_model(message_id=message_id, topic=topic))

    async def dispatch_once(self, limit: int = 100) -> int:
        async with self._lock:
            now = utcnow()
            with self.db.session() as s:
                rows = s.execute(select(self.model).where(self.model.dispatched_at.is_(None))
                                 .order_by(self.model.id).limit(limit)).scalars().all()
                pending = [(r.id, r.topic, r.envelope, r.next_attempt_at) for r in rows]
            sent = 0
            blocked_subjects: set[str] = set()
            for row_id, topic, env, next_at in pending:
                subject = env.get("subject") or ""
                if subject in blocked_subjects:
                    continue  # preserve per-order ordering behind a failed message
                if next_at and _aware(next_at) > now:
                    blocked_subjects.add(subject)
                    continue
                try:
                    await self.broker.publish(topic, env)
                    if self.stream and topic in self.stream_topics and self.stream.enabled:
                        await self.stream.send([env])
                    with self.db.session() as s:
                        r = s.get(self.model, row_id)
                        r.dispatched_at, r.attempts = utcnow(), r.attempts + 1
                    sent += 1
                except Exception as exc:  # noqa: BLE001
                    blocked_subjects.add(subject)
                    with self.db.session() as s:
                        r = s.get(self.model, row_id)
                        r.attempts += 1
                        r.last_error = str(exc)[:2000]
                        r.next_attempt_at = utcnow() + timedelta(seconds=min(300, 2 ** min(r.attempts, 8)))
                    log.warning("outbox publish failed id=%s topic=%s attempt=%s: %s", row_id, topic, r.attempts, exc)
            return sent

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            try:
                await self.dispatch_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("outbox dispatcher error")

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    def stats(self) -> dict[str, int]:
        from sqlalchemy import func
        with self.db.session() as s:
            pending = s.scalar(select(func.count()).select_from(self.model).where(self.model.dispatched_at.is_(None)))
            failed = s.scalar(select(func.count()).select_from(self.model)
                              .where(self.model.dispatched_at.is_(None), self.model.attempts > 0))
        return {"pending": pending or 0, "retrying": failed or 0}


def _aware(dt: datetime) -> datetime:
    from datetime import timezone
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


Callback = Callable[..., Any]
