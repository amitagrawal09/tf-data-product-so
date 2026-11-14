"""Process API persistence: the Sales Order data product store (PostgreSQL JSONB / SQLite JSON).

`sales_orders` holds the canonical document plus projected, indexed columns for the status API.
`order_history` is the append-only lifecycle (event store). `order_documents` holds inbound/outbound
documents (X12, email, JSON). `outbox`/`inbox` give exactly-once effects over at-least-once messaging.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..common.db import JsonDoc
from ..common.outbox import make_models
from ..common.util import utcnow


class ProcessBase(DeclarativeBase):
    pass


class OrderRow(ProcessBase):
    __tablename__ = "sales_orders"
    order_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    order_group_id: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    history_seq: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), index=True)
    status_category: Mapped[str] = mapped_column(String(30), index=True)
    source_channel: Mapped[str] = mapped_column(String(20), index=True)
    customer_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_po: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    ship_to_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    erp_sales_order_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    requested_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    total_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    open_exception_count: Mapped[int] = mapped_column(Integer, default=0)
    touchless: Mapped[bool] = mapped_column(Boolean, default=True)
    first_pass: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    erp_created: Mapped[bool] = mapped_column(Boolean, default=False)
    receipt_to_erp_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    receipt_to_ship_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    doc: Mapped[dict] = mapped_column(JsonDoc)

    __table_args__ = (Index("ix_orders_customer_po", "customer_id", "customer_po"),)


class HistoryRow(ProcessBase):
    __tablename__ = "order_history"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(40), ForeignKey("sales_orders.order_id"), index=True)
    order_group_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_system: Mapped[str] = mapped_column(String(40))
    external_event_id: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True)
    entry: Mapped[dict] = mapped_column(JsonDoc)

    __table_args__ = (UniqueConstraint("order_id", "sequence", name="uq_history_order_seq"),)


class DocumentRow(ProcessBase):
    __tablename__ = "order_documents"
    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    order_group_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    document_type: Mapped[str] = mapped_column(String(40))
    direction: Mapped[str] = mapped_column(String(10))
    control_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    content_type: Mapped[str] = mapped_column(String(80), default="text/plain")
    partner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    content: Mapped[str] = mapped_column(Text)


class SubscriptionRow(ProcessBase):
    __tablename__ = "webhook_subscriptions"
    subscription_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    callback_url: Mapped[str] = mapped_column(String(500))
    event_types: Mapped[list] = mapped_column(JsonDoc)
    customer_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    secret: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deliveries: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    last_status: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdempotencyRow(ProcessBase):
    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    response: Mapped[dict] = mapped_column(JsonDoc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CounterRow(ProcessBase):
    __tablename__ = "counters"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class DataAccessRequestRow(ProcessBase):
    """Data marketplace: a consumer's request to use a dataset (governance record)."""
    __tablename__ = "data_access_requests"
    request_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(80), index=True)
    requester: Mapped[str] = mapped_column(String(200), index=True)
    team: Mapped[str | None] = mapped_column(String(200), nullable=True)
    purpose: Mapped[str] = mapped_column(Text)
    consumer_system: Mapped[str | None] = mapped_column(String(200), nullable=True)
    access_level: Mapped[str] = mapped_column(String(20), default="READ")
    status: Mapped[str] = mapped_column(String(20), index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DataExportRow(ProcessBase):
    """Data marketplace: audit of every data retrieval / export (who, what, how, which APIs fulfilled it)."""
    __tablename__ = "data_exports"
    export_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(80), index=True)
    requester: Mapped[str] = mapped_column(String(200), index=True)
    format: Mapped[str] = mapped_column(String(10))
    filters: Mapped[dict] = mapped_column(JsonDoc)
    fields: Mapped[list] = mapped_column(JsonDoc)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    fulfilled_by: Mapped[list] = mapped_column(JsonDoc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


OutboxMessage, InboxMessage = make_models(ProcessBase)
