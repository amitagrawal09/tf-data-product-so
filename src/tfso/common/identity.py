"""Identity and authorisation model for the Enterprise Data Mart (contract: docs/07-data-mart-access-contract.md).

A request is authorised as exactly one Principal:
  * a **user** - from an Entra ID access token (workforce or External ID) or, in dev mode, a test persona;
  * the **service** principal - service-to-service calls with the internal x-api-key.
App roles drive domain access; scope (customerIds / vendorIds / regions) drives row-level security.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DOMAINS: list[dict[str, str]] = [
    {"domainId": "sales-order", "name": "Sales Order", "role": "Domain.SalesOrder.Read",
     "description": "Orders from every channel, lines, lifecycle history, exceptions, risk, service KPIs and pricing"},
    {"domainId": "customer", "name": "Customer", "role": "Domain.Customer.Read",
     "description": "Customer accounts, delivery locations and EDI trading partners"},
    {"domainId": "product", "name": "Product", "role": "Domain.Product.Read",
     "description": "Items, standard costs and margins, and available inventory by site"},
    {"domainId": "logistics", "name": "Logistics", "role": "Domain.Logistics.Read",
     "description": "Shipments and traceability, carriers and outbound loads"},
    {"domainId": "vendor", "name": "Vendor", "role": "Domain.Vendor.Read",
     "description": "Grower and supplier master data and monthly vendor scorecards"},
    {"domainId": "procurement", "name": "Procurement", "role": "Domain.Procurement.Read",
     "description": "Purchase orders for raw produce and packaging, PO lines and goods receipts with inspection"},
    {"domainId": "finance", "name": "Finance", "role": "Domain.Finance.Read",
     "description": "Accounts receivable invoices, accounts payable invoices with 3-way match, and spend by category"},
]
DOMAIN_BY_ID = {d["domainId"]: d for d in DOMAINS}

ROLES: list[dict[str, str]] = [
    {"role": "DataMart.Admin", "kind": "platform", "description": "Full access to every domain and dataset, plus the Access & roles administration"},
    {"role": "DataMart.Steward", "kind": "platform", "description": "Approves or rejects access requests and sees all dataset metadata"},
    {"role": "DataMart.Export", "kind": "platform", "description": "Can export datasets (CSV, JSON, NDJSON, Excel); without it users can preview only"},
    {"role": "PII.Read", "kind": "platform", "description": "Sees personal data (contact names, e-mails, phones) unmasked"},
    {"role": "Orders.Read", "kind": "platform", "description": "Uses the Order Status API and the Monitor pages"},
    {"role": "Orders.Operate", "kind": "platform", "description": "Creates, corrects, holds, cancels and resubmits orders; runs the fulfilment simulator"},
    *[{"role": d["role"], "kind": "domain", "description": f"Reads datasets in the {d['name']} domain"} for d in DOMAINS],
    {"role": "External.Customer", "kind": "external", "description": "External customer: sees only its own orders, shipments, invoices and the public item catalogue"},
    {"role": "External.Vendor", "kind": "external", "description": "External vendor: sees only its own purchase orders, receipts, payables and scorecard"},
]
ROLE_NAMES = {r["role"] for r in ROLES}

PERSONAS: list[dict[str, Any]] = [
    {"id": "admin", "name": "Avery Admin", "email": "avery.admin@taylorfarms.example", "userType": "internal",
     "title": "Data platform administrator", "roles": ["DataMart.Admin"], "scope": {}},
    {"id": "sales.analyst", "name": "Sam Analyst", "email": "sam.analyst@taylorfarms.example", "userType": "internal",
     "title": "Commercial analyst", "roles": ["Orders.Read", "Domain.SalesOrder.Read", "Domain.Customer.Read",
                                            "Domain.Product.Read", "Domain.Logistics.Read", "DataMart.Export"], "scope": {}},
    {"id": "cs.agent", "name": "Casey Service", "email": "casey.service@taylorfarms.example", "userType": "internal",
     "title": "Customer service representative", "roles": ["Orders.Read", "Orders.Operate", "Domain.SalesOrder.Read",
                                                          "Domain.Customer.Read", "PII.Read"], "scope": {}},
    {"id": "regional.gm.west", "name": "Morgan West", "email": "morgan.west@taylorfarms.example", "userType": "internal",
     "title": "Regional GM, West", "roles": ["Orders.Read", "Domain.SalesOrder.Read", "Domain.Logistics.Read",
                                            "Domain.Product.Read"], "scope": {"regions": ["WEST"]}},
    {"id": "procurement.buyer", "name": "Pat Buyer", "email": "pat.buyer@taylorfarms.example", "userType": "internal",
     "title": "Produce and packaging buyer", "roles": ["Domain.Procurement.Read", "Domain.Vendor.Read",
                                                      "Domain.Product.Read", "DataMart.Export"], "scope": {}},
    {"id": "finance.controller", "name": "Frankie Finance", "email": "frankie.finance@taylorfarms.example",
     "userType": "internal", "title": "Financial controller",
     "roles": ["Domain.Finance.Read", "Domain.Vendor.Read", "Domain.Procurement.Read", "Domain.SalesOrder.Read",
               "Domain.Customer.Read", "DataMart.Export", "PII.Read", "DataMart.Steward"], "scope": {}},
    {"id": "ext.customer.freshmart", "name": "Pat Rivera (FreshMart)", "email": "orders@freshmart.example",
     "userType": "external", "title": "Buyer, FreshMart Grocery", "roles": ["External.Customer"],
     "scope": {"customerIds": ["C-1001"]}},
    {"id": "ext.vendor.salinas", "name": "Jordan Greens (Salinas Valley Greens)", "email": "sales@svgreens.example",
     "userType": "external", "title": "Account manager, Salinas Valley Greens", "roles": ["External.Vendor"],
     "scope": {"vendorIds": ["V-1001"]}},
]
PERSONA_BY_ID = {p["id"]: p for p in PERSONAS}


@dataclass
class Principal:
    id: str
    name: str
    email: str | None
    user_type: str                      # internal | external | service
    roles: list[str] = field(default_factory=list)
    auth_mode: str = "apikey"
    persona: str | None = None
    customer_ids: list[str] = field(default_factory=list)
    vendor_ids: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    claims: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ role helpers
    @property
    def is_service(self) -> bool:
        return self.user_type == "service"

    @property
    def is_external(self) -> bool:
        return self.user_type == "external"

    def has(self, role: str) -> bool:
        return self.is_service or "DataMart.Admin" in self.roles or role in self.roles

    @property
    def is_admin(self) -> bool:
        return self.is_service or "DataMart.Admin" in self.roles

    @property
    def is_steward(self) -> bool:
        return self.is_admin or "DataMart.Steward" in self.roles

    @property
    def can_read_orders(self) -> bool:
        return not self.is_external and self.has("Orders.Read")

    @property
    def can_operate_orders(self) -> bool:
        return not self.is_external and self.has("Orders.Operate")

    @property
    def can_export(self) -> bool:
        return self.is_external or self.has("DataMart.Export")

    @property
    def pii_access(self) -> bool:
        return not self.is_external and self.has("PII.Read")

    @property
    def is_external_customer(self) -> bool:
        return self.is_external and "External.Customer" in self.roles

    @property
    def is_external_vendor(self) -> bool:
        return self.is_external and "External.Vendor" in self.roles

    def can_read_domain(self, domain_id: str) -> bool:
        d = DOMAIN_BY_ID.get(domain_id)
        return bool(d) and not self.is_external and self.has(d["role"])

    def to_dict(self, external_domains: list[str] | None = None) -> dict[str, Any]:
        if self.is_external:
            domains = sorted(external_domains or [])
        else:
            domains = [d["domainId"] for d in DOMAINS if self.can_read_domain(d["domainId"])]
        return {"id": self.id, "name": self.name, "email": self.email, "userType": self.user_type,
                "roles": sorted(self.roles) if not self.is_service else ["*"], "authMode": self.auth_mode,
                "persona": self.persona, "domains": domains,
                "canExport": self.can_export, "canOperateOrders": self.can_operate_orders,
                "canReadOrders": self.can_read_orders, "isAdmin": self.is_admin, "isSteward": self.is_steward,
                "piiAccess": self.pii_access,
                "scope": {"customerIds": self.customer_ids, "vendorIds": self.vendor_ids, "regions": self.regions}}

    def legacy(self) -> dict[str, Any]:
        """Dict kept on request.state.principal for older call sites."""
        d = {"sub": self.email or self.id, "mode": self.auth_mode, "userType": self.user_type}
        if self.customer_ids:
            d["extension_customerId"] = self.customer_ids[0]
        return d


SERVICE = Principal(id="service", name="Service", email=None, user_type="service", roles=["*"], auth_mode="apikey")


def from_persona(persona_id: str) -> Principal | None:
    p = PERSONA_BY_ID.get(persona_id)
    if not p:
        return None
    sc = p.get("scope") or {}
    return Principal(id=p["id"], name=p["name"], email=p["email"], user_type=p["userType"], roles=list(p["roles"]),
                     auth_mode="dev", persona=p["id"], customer_ids=list(sc.get("customerIds", [])),
                     vendor_ids=list(sc.get("vendorIds", [])), regions=list(sc.get("regions", [])))


def _list_claim(v: Any) -> list[str]:
    if v in (None, ""):
        return []
    if isinstance(v, list):
        return [str(x) for x in v if str(x)]
    return [x.strip() for x in str(v).split(",") if x.strip()]


def _claim(claims: dict[str, Any], name: str) -> Any:
    """Exact claim, or an Entra External ID extension attribute (extension_<appId>_<name>)."""
    if name in claims:
        return claims[name]
    short = name.split("extension_", 1)[-1]
    for k, v in claims.items():
        if k.startswith("extension_") and k.endswith("_" + short):
            return v
    return None


def from_claims(claims: dict[str, Any], *, external_issuer: str | None = None, customer_claim: str = "extension_customerId",
                vendor_claim: str = "extension_vendorId", regions_claim: str = "extension_regions",
                group_role_map: dict[str, str] | None = None) -> Principal:
    roles = [r for r in _list_claim(claims.get("roles")) if r in ROLE_NAMES]
    for g in _list_claim(claims.get("groups")):
        mapped = (group_role_map or {}).get(g)
        if mapped and mapped not in roles:
            roles.append(mapped)
    customer_ids = _list_claim(_claim(claims, customer_claim))
    vendor_ids = _list_claim(_claim(claims, vendor_claim))
    external = (bool(external_issuer) and claims.get("iss") == external_issuer) \
        or "External.Customer" in roles or "External.Vendor" in roles or str(claims.get("acct")) == "1"
    if external:
        # External users never hold internal roles, whatever the token says.
        roles = [r for r in roles if r.startswith("External.")]
        if customer_ids and "External.Customer" not in roles:
            roles.append("External.Customer")
        if vendor_ids and "External.Vendor" not in roles:
            roles.append("External.Vendor")
    email = claims.get("email") or claims.get("preferred_username") or claims.get("upn")
    return Principal(id=str(claims.get("oid") or claims.get("sub") or email or "user"),
                     name=claims.get("name") or email or "User", email=email,
                     user_type="external" if external else "internal", roles=roles, auth_mode="entra",
                     customer_ids=customer_ids, vendor_ids=vendor_ids,
                     regions=[r.upper() for r in _list_claim(_claim(claims, regions_claim))], claims=claims)
