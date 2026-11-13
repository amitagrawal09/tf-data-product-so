"""Master data system API: customers, ship-tos, items, trade agreements, cross references, trading partners.

`SeedMasterData` serves the reference data set shipped with the product (local / test / simulator).
`D365MasterData` reads the same shapes from Dynamics 365 F&O OData data entities and caches them.
"""
from __future__ import annotations

import json
import time
from datetime import date
from importlib import resources
from typing import Any

from .d365_client import D365Client


class SeedMasterData:
    mode = "seed"

    def __init__(self, data: dict[str, Any] | None = None):
        if data is None:
            data = json.loads(resources.files("tfso.system").joinpath("seed/masterdata.json").read_text())
        self.data = data
        self.customers = {c["customerId"]: c for c in data["customers"]}
        self.ship_tos = {s["shipToId"]: s for s in data["shipTos"]}
        self.items = {i["itemNumber"]: i for i in data["items"]}
        self.partners = {p["isaId"]: p for p in data["tradingPartners"]}
        self.inventory = dict(data.get("inventory", {}))

    async def list_customers(self, active: bool | None = None) -> list[dict]:
        return [c for c in self.customers.values() if active is None or c["active"] == active]

    async def get_customer(self, customer_id: str) -> dict | None:
        c = self.customers.get(customer_id)
        if c is None:  # allow lookup by ERP account or partner client id
            c = next((x for x in self.customers.values()
                      if customer_id in (x.get("erpAccount"), x.get("partnerClientId"))), None)
        return c

    async def ship_tos_for(self, customer_id: str) -> list[dict]:
        return [s for s in self.ship_tos.values() if s["customerId"] == customer_id]

    async def get_ship_to(self, ship_to_id: str) -> dict | None:
        return self.ship_tos.get(ship_to_id)

    async def find_ship_to(self, customer_id: str | None, *, edi_code: str | None = None, gln: str | None = None,
                           name: str | None = None) -> dict | None:
        for s in self.ship_tos.values():
            if customer_id and s["customerId"] != customer_id:
                continue
            if edi_code and s.get("ediLocationCode") == edi_code:
                return s
            if gln and s.get("gln") == gln:
                return s
            if name and s["name"].lower() == name.lower():
                return s
        return None

    async def list_items(self, active: bool | None = None) -> list[dict]:
        return [i for i in self.items.values() if active is None or i["active"] == active]

    async def get_item(self, item_number: str) -> dict | None:
        return self.items.get(item_number)

    async def resolve_item(self, customer_id: str | None, *, buyer_item: str | None = None, gtin: str | None = None,
                           upc: str | None = None, vendor_item: str | None = None) -> dict | None:
        if vendor_item and vendor_item in self.items:
            return self.items[vendor_item]
        if buyer_item:
            x = next((x for x in self.data["itemXref"] if x["buyerItemNumber"] == buyer_item
                      and (customer_id is None or x["customerId"] == customer_id)), None)
            if x:
                return self.items.get(x["itemNumber"])
            if buyer_item in self.items:
                return self.items[buyer_item]
        for i in self.items.values():
            if (gtin and i.get("gtin") == gtin) or (upc and i.get("upc") == upc):
                return i
        return None

    async def price_agreements(self, customer_id: str | None = None, item_number: str | None = None,
                               on: date | None = None) -> list[dict]:
        on = on or date.today()
        out = []
        for a in self.data["priceAgreements"]:
            if customer_id and a["customerId"] != customer_id:
                continue
            if item_number and a["itemNumber"] != item_number:
                continue
            if not (date.fromisoformat(a["validFrom"]) <= on <= date.fromisoformat(a["validTo"])):
                continue
            out.append(a)
        return out

    async def get_partner(self, isa_id: str) -> dict | None:
        return self.partners.get(isa_id.strip())

    async def item_xref(self, customer_id: str | None) -> list[dict]:
        return [x for x in self.data["itemXref"] if customer_id is None or x["customerId"] == customer_id]


class D365MasterData(SeedMasterData):
    """Reads master data from D365 F&O; trading partner / cross-reference config stays in the seed set
    (in production those live in the Logic Apps Integration Account and the MDM hub)."""
    mode = "d365"

    def __init__(self, client: D365Client, ttl_seconds: int = 900):
        super().__init__()
        self.client, self.ttl = client, ttl_seconds
        self._loaded_at = 0.0

    async def _refresh(self) -> None:
        if time.time() - self._loaded_at < self.ttl:
            return
        cust = await self.client.get_all("CustomersV3", select="CustomerAccount,OrganizationName,CustomerGroupId,"
                                         "PaymentTerms,CreditLimit,SalesCurrencyCode,InvoiceAccount,OnHoldStatus")
        self.customers = {c["CustomerAccount"]: {
            "customerId": c["CustomerAccount"], "name": c.get("OrganizationName"), "active": c.get("OnHoldStatus") in (None, "No"),
            "erpAccount": c["CustomerAccount"], "invoiceAccount": c.get("InvoiceAccount") or c["CustomerAccount"],
            "currency": c.get("SalesCurrencyCode", "USD"), "paymentTerms": {"code": c.get("PaymentTerms")},
            "creditLimit": c.get("CreditLimit", 0), "openAr": 0, "priceGroup": c.get("CustomerGroupId"), "channels": []}
            for c in cust}
        items = await self.client.get_all("ReleasedProductsV2", select="ItemNumber,SearchName,SalesUnitSymbol,"
                                          "SalesPrice,ProductLifecycleStateId")
        for p in items:
            existing = self.items.get(p["ItemNumber"], {})
            self.items[p["ItemNumber"]] = {**existing, "itemNumber": p["ItemNumber"],
                                           "description": p.get("SearchName") or existing.get("description"),
                                           "salesUoms": [p.get("SalesUnitSymbol") or "CA"],
                                           "listPrice": p.get("SalesPrice", existing.get("listPrice")),
                                           "active": p.get("ProductLifecycleStateId") in (None, "", "Active")}
        self._loaded_at = time.time()

    async def list_customers(self, active=None):
        await self._refresh()
        return await super().list_customers(active)

    async def get_customer(self, customer_id):
        await self._refresh()
        return await super().get_customer(customer_id)

    async def list_items(self, active=None):
        await self._refresh()
        return await super().list_items(active)

    async def get_item(self, item_number):
        await self._refresh()
        return await super().get_item(item_number)

    async def price_agreements(self, customer_id=None, item_number=None, on=None):
        flt = []
        if customer_id:
            flt.append(f"AccountRelation eq '{customer_id}'")
        if item_number:
            flt.append(f"ItemNumber eq '{item_number}'")
        rows = await self.client.get_all("SalesPriceAgreements", filter_=" and ".join(flt) or None)
        return [{"agreementId": r.get("TradeAgreementJournalNumber"), "customerId": r.get("AccountRelation"),
                 "itemNumber": r.get("ItemNumber"), "price": r.get("Price"), "uom": r.get("QuantityUnitySymbol"),
                 "validFrom": (r.get("PriceApplicableFromDate") or "1900-01-01")[:10],
                 "validTo": (r.get("PriceApplicableToDate") or "2154-12-31")[:10]} for r in rows]
