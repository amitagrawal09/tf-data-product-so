"""Enterprise data mart access control (docs/07-data-mart-access-contract.md).

Runs in `dev` sign-in mode: requests carry `x-dev-persona` and are resolved exactly like an Entra ID token with
the same app roles and scope claims would be. The harness' x-api-key requests remain the service identity.
"""
from __future__ import annotations

import dataclasses

import pytest

from tfso.common.config import get_settings
from tfso.common.identity import from_claims
from tfso.common.web import Authenticator


def P(persona: str) -> dict:
    return {"x-dev-persona": persona}


async def seed(h) -> dict:
    west = await h.portal(customerPo="A-W")                                       # FreshMart, Stockton → WEST
    central = await h.portal(customerPo="A-C", shipToId="ST-1001-03")             # FreshMart, Dallas → CENTRAL
    other = await h.portal(customerId="C-1006", shipToId="ST-1006-01", customerPo="A-NE")  # another customer
    await h.advance(west["orderId"], "INVOICED")
    return {"west": west["orderId"], "central": central["orderId"], "other": other["orderId"]}


async def test_identity_endpoints(h):
    personas = (await h.exp.get("/auth/v1/personas", headers={})).json()["items"]
    assert {p["id"] for p in personas} >= {"admin", "ext.customer.freshmart", "ext.vendor.salinas"}

    me = (await h.exp.get("/auth/v1/me", headers=P("regional.gm.west"))).json()
    assert me["userType"] == "internal" and me["scope"]["regions"] == ["WEST"] and me["canReadOrders"]
    assert not me["canExport"] and "sales-order" in me["domains"] and "finance" not in me["domains"]

    ext = (await h.exp.get("/auth/v1/me", headers=P("ext.customer.freshmart"))).json()
    assert ext["userType"] == "external" and ext["scope"]["customerIds"] == ["C-1001"] and not ext["canReadOrders"]
    assert set(ext["domains"]) == {"sales-order", "logistics", "finance", "product"}

    vend = (await h.exp.get("/auth/v1/me", headers=P("ext.vendor.salinas"))).json()
    assert set(vend["domains"]) == {"vendor", "procurement", "finance"}

    assert (await h.exp.get("/auth/v1/me", headers=P("nobody"))).status_code == 401


async def test_no_identity_is_rejected(h):
    import httpx
    async with httpx.AsyncClient(transport=h.exp._transport, base_url="http://exp.test") as anon:
        assert (await anon.get("/marketplace/v1/datasets")).status_code == 401
        assert (await anon.get("/health")).status_code == 200
        assert (await anon.get("/auth/v1/personas")).status_code == 200


def test_dev_mode_refused_in_production():
    s = dataclasses.replace(get_settings("experience"), auth_mode="dev", env="prod")
    with pytest.raises(RuntimeError):
        Authenticator(s)


async def test_admin_endpoints_need_steward(h):
    assert (await h.exp.get("/admin/v1/role-matrix", headers=P("sales.analyst"))).status_code == 403
    m = (await h.exp.get("/admin/v1/role-matrix", headers=P("finance.controller"))).json()
    fin = next(r for r in m["roles"] if r["role"] == "Domain.Finance.Read")
    assert fin["domains"]["finance"] and not fin["domains"]["vendor"] and len(m["domains"]) == 7
    assert len((await h.exp.get("/admin/v1/roles", headers=P("admin"))).json()["items"]) >= 15


async def test_domain_roles_lock_datasets(h):
    cat = (await h.exp.get("/marketplace/v1/datasets", headers=P("sales.analyst"))).json()["items"]
    by = {d["id"]: d for d in cat}
    assert len(by) == 25  # internal users see the whole catalogue …
    assert by["sales-orders"]["accessible"] and not by["ap-invoices"]["accessible"]  # … but locked outside their domains
    assert "Domain.Finance.Read" in by["ap-invoices"]["lockedReason"]
    assert (await h.exp.get("/marketplace/v1/datasets/ap-invoices/preview", headers=P("sales.analyst"))).status_code == 403
    pv = (await h.exp.get("/marketplace/v1/datasets/purchase-orders/preview", headers=P("procurement.buyer")))
    assert pv.status_code == 200

    doms = {d["domainId"]: d for d in (await h.exp.get("/marketplace/v1/domains", headers=P("procurement.buyer"))).json()["items"]}
    assert len(doms) == 7 and doms["procurement"]["accessible"] and not doms["sales-order"]["accessible"]
    assert not doms["finance"]["accessible"]


async def test_confidential_needs_approval_unless_steward(h):
    # buyer: Procurement domain role, but purchase-orders is Confidential → sensitive fields hidden, export blocked
    assert (await h.exp.get("/marketplace/v1/datasets/purchase-order-lines/export", headers=P("procurement.buyer"))).status_code == 403
    pv = (await h.exp.get("/marketplace/v1/datasets/purchase-order-lines/preview", headers=P("procurement.buyer"))).json()
    assert "unitCost" in pv["maskedFields"] and pv["rows"][0]["unitCost"].startswith("••••")

    req = (await h.exp.post("/marketplace/v1/datasets/purchase-order-lines/access-requests", headers=P("procurement.buyer"),
                            json={"purpose": "Commodity cost analysis"})).json()
    assert req["status"] == "PENDING" and req["requester"] == "pat.buyer@taylorfarms.example"
    # only stewards decide, and non-stewards only see their own requests
    assert (await h.exp.post(f"/marketplace/v1/access-requests/{req['requestId']}/decision", headers=P("sales.analyst"),
                             json={"decision": "APPROVED"})).status_code == 403
    assert (await h.exp.get("/marketplace/v1/access-requests", headers=P("sales.analyst"))).json()["items"] == []
    dec = (await h.exp.post(f"/marketplace/v1/access-requests/{req['requestId']}/decision", headers=P("finance.controller"),
                            json={"decision": "APPROVED", "note": "OK"})).json()
    assert dec["status"] == "APPROVED" and dec["decidedBy"] == "frankie.finance@taylorfarms.example"
    r = await h.exp.get("/marketplace/v1/datasets/purchase-order-lines/export", params={"format": "json"}, headers=P("procurement.buyer"))
    assert r.status_code == 200 and isinstance(r.json()["rows"][0]["unitCost"], float)
    # stewards read confidential data without a request
    assert (await h.exp.get("/marketplace/v1/datasets/ap-invoices/export", headers=P("finance.controller"))).status_code == 200


async def test_export_role_and_pii_masking(h):
    await seed(h)
    # cs.agent can read the Customer domain but has no DataMart.Export
    r = await h.exp.get("/marketplace/v1/datasets/sales-orders/export", headers=P("cs.agent"))
    assert r.status_code == 403 and "DataMart.Export" in r.json()["detail"]
    # PII: finance controller (PII.Read, steward) sees contacts; the analyst (approved? no - Confidential) is masked
    pv = (await h.exp.get("/marketplace/v1/datasets/customers/preview", headers=P("finance.controller"))).json()
    assert not pv["maskedFields"] and "@" in pv["rows"][0]["contactEmail"]
    pv = (await h.exp.get("/marketplace/v1/datasets/customers/preview", headers=P("sales.analyst"))).json()
    assert "contactEmail" in pv["maskedFields"]


async def test_regional_user_only_sees_their_region(h):
    ids = await seed(h)
    orders = (await h.process.get("/v1/orders", headers=P("regional.gm.west"))).json()["items"]
    assert {o["region"] for o in orders} == {"WEST"} and ids["west"] in {o["orderId"] for o in orders}
    assert (await h.process.get(f"/v1/orders/{ids['central']}/status", headers=P("regional.gm.west"))).status_code == 404
    assert (await h.process.get(f"/v1/orders/{ids['west']}/status", headers=P("regional.gm.west"))).status_code == 200
    # asking for another region yields nothing rather than leaking it
    assert (await h.process.get("/v1/orders", params={"region": "CENTRAL"}, headers=P("regional.gm.west"))).json()["total"] == 0
    pv = (await h.exp.get("/marketplace/v1/datasets/sales-orders/preview", headers=P("regional.gm.west"))).json()
    assert pv["rows"] and {x["region"] for x in pv["rows"]} == {"WEST"} and "WEST" in pv["rowScopeNote"]
    loads = (await h.exp.get("/marketplace/v1/datasets/carrier-loads/preview", params={"limit": 200},
                             headers=P("regional.gm.west"))).json()["rows"]
    assert loads and {x["originRegion"] for x in loads} == {"WEST"}
    # read-only role cannot operate orders
    assert (await h.process.post(f"/v1/orders/{ids['west']}/cancel", json={"reason": "x"},
                                 headers=P("regional.gm.west"))).status_code == 403


async def test_external_customer_row_security(h):
    ids = await seed(h)
    cust = P("ext.customer.freshmart")
    cat = {d["id"] for d in (await h.exp.get("/marketplace/v1/datasets", headers=cust)).json()["items"]}
    assert cat == {"sales-orders", "sales-order-lines", "shipments", "invoices", "items"}
    assert (await h.exp.get("/marketplace/v1/datasets/vendors", headers=cust)).status_code == 404
    assert (await h.exp.get("/marketplace/v1/datasets/sales-orders/lineage", headers=cust)).status_code == 404

    r = await h.exp.get("/marketplace/v1/datasets/sales-orders/export", params={"format": "json"}, headers=cust)
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert {x["customerId"] for x in rows} == {"C-1001"} and ids["other"] not in {x["orderId"] for x in rows}
    # asking for someone else's rows returns none
    r = await h.exp.get("/marketplace/v1/datasets/sales-orders/preview", params={"customerId": "C-1006"}, headers=cust)
    assert r.json()["rows"] == []

    mine = (await h.exp.get("/portal/v1/my/orders", headers=cust)).json()["items"]
    assert {o["orderId"] for o in mine} == {ids["west"], ids["central"]}
    assert (await h.exp.get(f"/portal/v1/orders/{ids['other']}", headers=cust)).status_code == 403
    assert (await h.exp.get(f"/portal/v1/orders/{ids['west']}", headers=cust)).status_code == 200
    custs = (await h.exp.get("/portal/v1/catalog/customers", headers=cust)).json()["items"]
    assert [c["customerId"] for c in custs] == ["C-1001"]
    # the internal Order Status API is not for external users
    assert (await h.process.get("/v1/orders", headers=cust)).status_code == 403
    assert (await h.system.get("/v1/masterdata/customers", headers=cust)).status_code == 403


async def test_external_vendor_row_security(h):
    v = P("ext.vendor.salinas")
    cat = {d["id"] for d in (await h.exp.get("/marketplace/v1/datasets", headers=v)).json()["items"]}
    assert cat == {"vendors", "vendor-scorecard", "purchase-orders", "purchase-order-lines", "goods-receipts", "ap-invoices"}
    for ds in cat:
        r = await h.exp.get(f"/marketplace/v1/datasets/{ds}/export", params={"format": "json", "limit": 50000}, headers=v)
        assert r.status_code == 200, (ds, r.text)
        rows = r.json()["rows"]
        assert rows and {x["vendorId"] for x in rows} == {"V-1001"}, ds
    assert (await h.exp.get("/marketplace/v1/datasets/sales-orders", headers=v)).status_code == 404
    assert (await h.exp.get("/portal/v1/my/orders", headers=v)).status_code == 403
    card = (await h.exp.get("/marketplace/v1/datasets/purchase-orders", headers=v)).json()
    assert card["recordCount"] is None and "V-1001" in card["rowScopeNote"] and "lineage" not in card


def test_principal_from_entra_claims():
    internal = from_claims({"oid": "o1", "name": "N", "preferred_username": "n@taylorfarms.example",
                                      "roles": ["Domain.Finance.Read", "Bogus.Role"], "extension_regions": "WEST,CENTRAL"})
    assert internal.roles == ["Domain.Finance.Read"] and internal.regions == ["WEST", "CENTRAL"] and not internal.is_external
    ext = from_claims({"oid": "o2", "roles": ["External.Vendor", "DataMart.Admin"],
                                 "extension_vendorId": "V-1001", "iss": "https://ext.example/v2.0"},
                                external_issuer="https://ext.example/v2.0")
    assert ext.is_external_vendor and ext.vendor_ids == ["V-1001"] and not ext.is_admin  # externals cannot hold internal roles


async def test_entra_bearer_tokens_are_validated(tmp_path):
    """Entra mode: RS256 signature, issuer (workforce or External ID) and audience are checked; roles become rights."""
    import time
    import types

    import httpx
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    from tfso.common.identity import PERSONAS  # noqa: F401  (ensures module import)
    from tfso.system.app import create_system_app

    tenant, aud = "11111111-2222-3333-4444-555555555555", "api://aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ext_iss = "https://contoso.ciamlogin.com/99999999-0000-0000-0000-000000000000/v2.0"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    s = dataclasses.replace(get_settings("system"), auth_mode="entra", api_keys=("svc",), env="test", bus_mode="inproc",
                            d365_mode="simulator", entra_tenant_id=tenant, entra_audience=f"{aud},api://ext-api",
                            entra_external_issuer=ext_iss, db_url=f"sqlite:///{tmp_path}/s.db",
                            lakehouse_mode="file", lakehouse_local_path=str(tmp_path / "lake"), eventhub_fqdn=None)
    app = create_system_app(s)
    app.state.auth._jwks_client = lambda url: types.SimpleNamespace(
        get_signing_key_from_jwt=lambda t: types.SimpleNamespace(key=key.public_key()))

    def tok(claims, signer=key, **over):
        base = {"iss": f"https://login.microsoftonline.com/{tenant}/v2.0", "aud": aud.replace("api://", ""),
                "oid": "u1", "name": "Pat", "preferred_username": "pat@taylorfarms.example",
                "exp": int(time.time()) + 600, "iat": int(time.time()), **claims, **over}
        return {"authorization": "Bearer " + jwt.encode(base, signer, algorithm="RS256")}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://system.test") as c:
        ok = tok({"roles": ["Domain.Product.Read", "Orders.Read"]})
        assert (await c.get("/v1/masterdata/items", headers=ok)).status_code == 200
        assert (await c.get("/v1/masterdata/items", headers=tok({}, signer=other))).status_code == 401      # bad signature
        assert (await c.get("/v1/masterdata/items", headers=tok({}, aud="someone-else"))).status_code == 401  # wrong audience
        assert (await c.get("/v1/masterdata/items", headers=tok({}, iss="https://evil.example/v2.0"))).status_code == 401
        assert (await c.get("/v1/masterdata/items", headers=tok({}, exp=int(time.time()) - 10))).status_code == 401
        ext = tok({"roles": ["External.Customer"], "extension_customerId": "C-1001"}, iss=ext_iss, aud="api://ext-api")
        assert (await c.get("/v1/masterdata/items", headers=ext)).status_code == 403  # valid, but external
        assert (await c.get("/v1/masterdata/items")).status_code == 401              # no token
        assert (await c.get("/v1/masterdata/items", headers={"x-dev-persona": "admin"})).status_code == 401  # no dev bypass
        assert (await c.get("/v1/masterdata/items", headers={"x-api-key": "svc"})).status_code == 200      # service identity
