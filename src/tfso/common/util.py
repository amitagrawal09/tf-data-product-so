from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if len(s) == 8 and s.isdigit():  # CCYYMMDD (X12)
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    return date.fromisoformat(s[:10])


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4()}"


def order_id(now: datetime | None = None) -> str:
    now = now or utcnow()
    return f"SO-{now:%Y%m%d}-{secrets.token_hex(4).upper()}"


def group_id(now: datetime | None = None) -> str:
    now = now or utcnow()
    return f"OG-{now:%Y%m%d}-{secrets.token_hex(4).upper()}"


def dec(value: Any, default: Decimal | None = None) -> Decimal | None:
    if value in (None, ""):
        return default
    return value if isinstance(value, Decimal) else Decimal(str(value))


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def qty_str(value: Any) -> str | None:
    """Canonical decimal string for quantities: 30.0 → '30', 2.50 → '2.5'."""
    if value in (None, ""):
        return None
    try:
        d = Decimal(str(value))
    except Exception:  # noqa: BLE001 - leave unparseable input for DQ-008 to report
        return str(value)
    s = format(d.normalize(), "f")
    return s if s not in ("-0",) else "0"
