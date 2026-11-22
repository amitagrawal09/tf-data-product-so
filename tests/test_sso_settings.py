"""Single sign-on configured from Settings (docs/09): draft → check → test sign-in → enable → reset."""
from __future__ import annotations

import time
import types

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import tfso.experience.sso_admin as sso_admin

TENANT = "11111111-2222-3333-4444-555555555555"
API = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SPA = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
ADMIN = {"x-dev-persona": "admin"}
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

DRAFT = {"mode": "entra", "tenantId": TENANT, "apiClientId": API, "spaClientId": SPA}


def token(roles, **over):
    claims = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": API, "oid": "o-1", "name": "Avery",
              "preferred_username": "avery@contoso.example", "roles": roles,
              "iat": int(time.time()), "exp": int(time.time()) + 600, **over}
    return jwt.encode(claims, KEY, algorithm="RS256")


@pytest.fixture
def sso(h):
    for app in (h.exp_app, h.process_app, h.system_app):
        app.state.auth.refresh_seconds = 0
        app.state.auth._jwks_client = lambda url: types.SimpleNamespace(
            get_signing_key_from_jwt=lambda t: types.SimpleNamespace(key=KEY.public_key()))
    return h


async def test_public_config_and_admin_only(sso):
    h = sso
    import httpx
    async with httpx.AsyncClient(transport=h.exp._transport, base_url="http://exp.test") as anon:
        cfg = (await anon.get("/auth/v1/config")).json()
    assert cfg["mode"] == "dev" and cfg["source"] == "environment"
    assert (await h.exp.get("/admin/v1/sso", headers={"x-dev-persona": "finance.controller"})).status_code == 403
    got = (await h.exp.get("/admin/v1/sso", headers=ADMIN)).json()
    assert got["draft"] is None and len(got["appRoles"]) == 15
    assert next(r for r in got["appRoles"] if r["value"] == "External.Vendor")["allowedMemberTypes"] == ["User"]


async def test_validation_errors(sso):
    r = await sso.exp.put("/admin/v1/sso", headers=ADMIN, json={"mode": "entra", "tenantId": "not-a-guid"})
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["errors"]}
    assert {"tenantId", "apiClientId", "spaClientId"} <= fields
    r = await sso.exp.put("/admin/v1/sso", headers=ADMIN, json={**DRAFT, "spaClientId": API})
    assert r.status_code == 422 and "two different" in r.json()["detail"]


async def test_enable_requires_admin_test_sign_in_then_enforces_everywhere(sso, monkeypatch):
    h = sso
    saved = (await h.exp.put("/admin/v1/sso", headers=ADMIN, json=DRAFT)).json()
    assert saved["draftStatus"]["state"] == "UNVERIFIED" and not saved["draftStatus"]["canActivate"]
    assert (await h.exp.post("/admin/v1/sso/activate", headers=ADMIN)).status_code == 409

    async def meta(url):
        return {"issuer": f"https://login.microsoftonline.com/{TENANT}/v2.0"}
    monkeypatch.setattr(sso_admin, "fetch_metadata", meta)
    chk = (await h.exp.post("/admin/v1/sso/check", headers=ADMIN)).json()
    assert chk["ok"] and len(chk["checks"]) == 2

    # a valid token without DataMart.Admin would lock the admin out → refused
    v = (await h.exp.post("/admin/v1/sso/verify", headers=ADMIN, json={"accessToken": token(["Orders.Read"])})).json()
    assert not v["verification"]["ok"] and "DataMart.Admin" in v["verification"]["message"]
    bad = (await h.exp.post("/admin/v1/sso/verify", headers=ADMIN, json={"accessToken": token(["DataMart.Admin"], aud="other")})).json()
    assert not bad["verification"]["ok"]
    v = (await h.exp.post("/admin/v1/sso/verify", headers=ADMIN, json={"accessToken": token(["DataMart.Admin"])})).json()
    assert v["verification"]["ok"] and v["draftStatus"]["state"] == "VERIFIED" and v["draftStatus"]["canActivate"]

    # any change to the draft invalidates the test
    changed = (await h.exp.put("/admin/v1/sso", headers=ADMIN, json={**DRAFT, "claimRegions": "extension_region"})).json()
    assert changed["draftStatus"]["state"] == "UNVERIFIED"
    await h.exp.put("/admin/v1/sso", headers=ADMIN, json=DRAFT)
    await h.exp.post("/admin/v1/sso/verify", headers=ADMIN, json={"accessToken": token(["DataMart.Admin"])})

    act = await h.exp.post("/admin/v1/sso/activate", headers=ADMIN)
    assert act.status_code == 200 and act.json()["public"]["clientId"] == SPA

    cfg = (await h.exp.get("/auth/v1/config")).json()
    assert cfg["mode"] == "entra" and cfg["scopes"][0] == f"api://{API}/access_as_user" and cfg["source"] == "settings"
    # personas stop working on every service; bearer tokens work
    for client, path in ((h.exp, "/auth/v1/me"), (h.process, "/v1/orders"), (h.system, "/v1/masterdata/items")):
        assert (await client.get(path, headers={"x-dev-persona": "admin", "x-api-key": ""})).status_code == 401, path
        ok = await client.get(path, headers={"authorization": f"Bearer {token(['DataMart.Admin'])}", "x-api-key": ""})
        assert ok.status_code == 200, (path, ok.text)
    me = (await h.exp.get("/auth/v1/me", headers={"authorization": f"Bearer {token(['Domain.Finance.Read'])}", "x-api-key": ""})).json()
    assert me["authMode"] == "entra" and me["domains"] == ["finance"]

    # break-glass: the service key resets to the deployment settings
    assert (await h.process.delete("/v1/sso/active")).status_code == 204
    assert (await h.exp.get("/auth/v1/config")).json()["mode"] == "dev"
    assert (await h.process.get("/v1/orders", headers={"x-dev-persona": "admin", "x-api-key": ""})).status_code == 200


async def test_admin_reset_and_users_cannot_touch_store(sso):
    h = sso
    assert (await h.process.get("/v1/sso", headers={"x-dev-persona": "admin", "x-api-key": ""})).status_code == 403
    r = await h.exp.post("/admin/v1/sso/reset", headers=ADMIN)
    assert r.status_code == 200 and r.json()["public"]["mode"] == "dev"


async def test_production_never_allows_personas(sso):
    import dataclasses
    h = sso
    h.exp_app.state.settings = dataclasses.replace(h.exp_app.state.settings, env="prod")
    from tfso.common.sso import SsoConfig, validate
    assert any(e["field"] == "mode" for e in validate(SsoConfig(mode="dev"), is_prod=True))
