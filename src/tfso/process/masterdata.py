"""Cached client for the master data System API (customers, ship-tos, items, agreements, partners)."""
from __future__ import annotations

import time
from typing import Any

from ..common.config import Settings
from ..common.web import ServiceClient


class MasterDataClient:
    def __init__(self, settings: Settings, ttl: float = 300):
        self.settings = settings
        self.ttl = ttl
        self._cache: dict[str, tuple[float, Any]] = {}
        self._client: ServiceClient | None = None

    @property
    def client(self) -> ServiceClient:
        if self._client is None:
            self._client = ServiceClient(self.settings.system_url, self.settings)
        return self._client

    async def _get(self, path: str, params: dict | None = None, *, not_found_ok: bool = True,
                   ttl: float | None = None) -> Any:
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()) if v is not None)
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < (self.ttl if ttl is None else ttl):
            return hit[1]
        value = await self.client.get_json(path, {k: v for k, v in (params or {}).items() if v is not None},
                                           not_found_ok=not_found_ok)
        self._cache[key] = (time.time(), value)
        return value

    async def customer(self, customer_id: str | None) -> dict | None:
        return await self._get(f"/v1/masterdata/customers/{customer_id}") if customer_id else None

    async def customers(self) -> list[dict]:
        return (await self._get("/v1/masterdata/customers"))["items"]

    async def ship_to(self, ship_to_id: str | None) -> dict | None:
        return await self._get(f"/v1/masterdata/ship-tos/{ship_to_id}") if ship_to_id else None

    async def find_ship_to(self, customer_id: str | None, *, edi_code=None, gln=None, name=None) -> dict | None:
        items = (await self._get("/v1/masterdata/ship-tos", {"customerId": customer_id, "ediLocationCode": edi_code,
                                                               "gln": gln, "name": name}))["items"]
        return items[0] if items else None

    async def ship_tos(self, customer_id: str) -> list[dict]:
        return (await self._get(f"/v1/masterdata/customers/{customer_id}/ship-tos"))["items"]

    async def item(self, item_number: str | None) -> dict | None:
        return await self._get(f"/v1/masterdata/items/{item_number}") if item_number else None

    async def items(self) -> list[dict]:
        return (await self._get("/v1/masterdata/items"))["items"]

    async def resolve_item(self, customer_id: str | None, ref: dict[str, Any]) -> dict | None:
        if ref.get("itemNumber"):
            found = await self.item(ref["itemNumber"])
            if found:
                return found
        params = {"customerId": customer_id, "buyerItem": ref.get("buyerItemNumber"), "gtin": ref.get("gtin"),
                  "upc": ref.get("upc"), "vendorItem": ref.get("vendorItemNumber") or ref.get("itemNumber")}
        if not any(params[k] for k in ("buyerItem", "gtin", "upc", "vendorItem")):
            return None
        items = (await self._get("/v1/masterdata/item-xref", params))["items"]
        return items[0] if items else None

    async def agreement(self, customer_id: str, item_number: str, on: str | None = None) -> dict | None:
        items = (await self._get("/v1/masterdata/price-agreements",
                                 {"customerId": customer_id, "itemNumber": item_number, "on": on}))["items"]
        return items[0] if items else None

    async def partner(self, isa_id: str) -> dict | None:
        return await self._get(f"/v1/masterdata/trading-partners/{isa_id}")

    async def regions(self) -> list[dict]:
        return (await self._get("/v1/masterdata/regions"))["items"]

    async def inventory(self) -> list[dict]:
        """On-hand by warehouse and item - never cached for more than a few seconds (sourcing decisions)."""
        return (await self._get("/v1/masterdata/inventory", ttl=2))["items"]
