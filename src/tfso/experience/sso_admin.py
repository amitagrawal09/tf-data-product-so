"""Settings → Single sign-on (Experience API, DataMart.Admin only) and the public sign-in configuration.

Flow (docs/09-connect-entra-id.md):
  1. PUT  /admin/v1/sso           save a draft (validated; any earlier test sign-in is discarded)
  2. POST /admin/v1/sso/check     the server checks the tenant / External ID metadata endpoints
  3. POST /admin/v1/sso/verify    the console signs the admin in against the *draft* and posts the access token;
                                  it must validate and carry DataMart.Admin
  4. POST /admin/v1/sso/activate  copies the verified draft to `active`; every service enforces it within seconds
     POST /admin/v1/sso/reset     back to the deployment settings (also: ./deploy.sh sso-reset, service key)
GET /auth/v1/config (public) tells the console how to sign users in.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Body, Request

from ..common.identity import ROLES
from ..common.sso import EDITABLE, app_roles_manifest, merge, settings_locked, validate
from ..common.util import iso, parse_dt, utcnow
from ..common.web import ApiError, ServiceClient, current_user, forbid

VERIFY_VALID_FOR = timedelta(hours=24)


async def fetch_metadata(url: str) -> dict[str, Any]:
    """OpenID Connect discovery document (separate function so tests can replace it)."""
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()


def build_sso_router(app, process: ServiceClient) -> APIRouter:
    r = APIRouter()
    settings = app.state.settings
    auth = app.state.auth

    async def active_sso():
        return (await process.get_json("/v1/sso/active"))["active"]

    auth.source = active_sso

    def admin(request: Request):
        user = current_user(request)
        if not user.is_admin:
            raise forbid("Single sign-on settings require the DataMart.Admin role")
        return user

    def actor(request: Request) -> str:
        u = current_user(request)
        return u.email or u.id

    async def records() -> dict:
        return await process.get_json("/v1/sso")

    async def put(slot: str, doc: dict) -> dict:
        resp = await process.request("PUT", f"/v1/sso/{slot}", json_body=doc)
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, "SSO settings could not be saved", resp.text[:300])
        return resp.json()

    def status_of(draft: dict | None) -> dict:
        if not draft:
            return {"state": "NONE"}
        cfg = merge(auth.env_sso, draft)
        v = draft.get("verification") or {}
        fresh = bool(v.get("at")) and utcnow() - parse_dt(v["at"]) < VERIFY_VALID_FOR
        verified = bool(v.get("ok")) and v.get("fingerprint") == cfg.fingerprint() and fresh
        errors = validate(cfg, is_prod=settings.is_prod)
        can = not errors and not settings_locked() and (cfg.mode != "entra" or verified)
        return {"state": "VERIFIED" if verified else ("INVALID" if errors else "UNVERIFIED"), "errors": errors,
                "verification": v or None, "canActivate": can, "config": cfg.to_dict(), "public": cfg.public()}

    # ------------------------------------------------------------------ public
    @r.get("/auth/v1/config", tags=["identity"], summary="How the console signs users in (public; identifiers only)")
    async def public_config():
        await auth.refresh()
        return {**auth.sso.public(), "environment": settings.env}

    # ------------------------------------------------------------------ admin
    @r.get("/admin/v1/sso", tags=["administration"], summary="Single sign-on settings (DataMart.Admin)")
    async def get_sso(request: Request):
        admin(request)
        await auth.refresh(force=True)
        rec = await records()
        return {"effective": auth.sso.to_dict(), "environment": auth.env_sso.to_dict(), "active": rec["active"],
                "draft": rec["draft"], "draftStatus": status_of(rec["draft"]), "locked": settings_locked(),
                "isProd": settings.is_prod, "env": settings.env,
                "appRoles": app_roles_manifest(), "roles": ROLES, "editableFields": list(EDITABLE)}

    @r.put("/admin/v1/sso", tags=["administration"], summary="Save the SSO draft (validated, not yet enforced)")
    async def save_draft(request: Request, body: dict = Body(...)):
        admin(request)
        if settings_locked():
            raise ApiError(409, "Settings locked", "TFSO_SSO_SETTINGS=locked: single sign-on is managed by the deployment")
        doc = {k: body.get(k) for k in EDITABLE if k in body}
        if isinstance(doc.get("groupRoleMap"), dict) and not doc["groupRoleMap"]:
            doc["groupRoleMap"] = None
        cfg = merge(auth.env_sso, doc)
        errors = validate(cfg, is_prod=settings.is_prod)
        if errors:
            raise ApiError(422, "Invalid single sign-on settings", "; ".join(e["message"] for e in errors), errors=errors)
        saved = await put("draft", {**doc, "updatedBy": actor(request)})
        return {"draft": saved, "draftStatus": status_of(saved)}

    @r.post("/admin/v1/sso/check", tags=["administration"], summary="Check the draft against Entra ID metadata")
    async def check(request: Request):
        admin(request)
        draft = (await records())["draft"]
        if not draft:
            raise ApiError(409, "No draft", "Save the settings first")
        cfg = merge(auth.env_sso, draft)
        checks = [{"name": "Settings are complete", "ok": not validate(cfg, is_prod=settings.is_prod),
                   "detail": "; ".join(e["message"] for e in validate(cfg, is_prod=settings.is_prod)) or "All required values present"}]
        if cfg.mode == "entra" and cfg.tenantId:
            url = f"{cfg.authority}/v2.0/.well-known/openid-configuration"
            try:
                meta = await fetch_metadata(url)
                ok = meta.get("issuer") == cfg.issuer
                checks.append({"name": "Workforce tenant reachable", "ok": ok,
                               "detail": f"Issuer {meta.get('issuer')}" if ok else f"Unexpected issuer {meta.get('issuer')}"})
            except httpx.HTTPStatusError as exc:
                checks.append({"name": "Workforce tenant reachable", "ok": False,
                               "detail": (f"Microsoft does not recognise tenant {cfg.tenantId} (HTTP {exc.response.status_code}). "
                                          "Copy the Directory (tenant) ID from Entra admin center → Overview.")})
            except Exception as exc:  # noqa: BLE001
                checks.append({"name": "Workforce tenant reachable", "ok": False,
                               "detail": f"Could not reach {url} ({exc}). Allow outbound HTTPS to login.microsoftonline.com."})
        if cfg.mode == "entra" and cfg.externalAuthority:
            url = f"{cfg.externalAuthority.rstrip('/')}/v2.0/.well-known/openid-configuration"
            try:
                meta = await fetch_metadata(url)
                ok = meta.get("issuer") == cfg.externalIssuer
                checks.append({"name": "External ID tenant reachable", "ok": ok,
                               "detail": f"Issuer {meta.get('issuer')}" + ("" if ok else f" does not match {cfg.externalIssuer}")})
            except Exception as exc:  # noqa: BLE001
                checks.append({"name": "External ID tenant reachable", "ok": False, "detail": f"{url}: {exc}"})
        return {"ok": all(c["ok"] for c in checks), "checks": checks}

    @r.post("/admin/v1/sso/verify", tags=["administration"],
            summary="Validate a test sign-in against the draft (token must carry DataMart.Admin)")
    async def verify(request: Request, body: dict = Body(...)):
        admin(request)
        rec = await records()
        draft = rec["draft"]
        if not draft:
            raise ApiError(409, "No draft", "Save the settings first")
        cfg = merge(auth.env_sso, draft)
        if cfg.mode != "entra":
            raise ApiError(409, "Nothing to test", "Test sign-in applies to Entra ID mode")
        token = (body.get("accessToken") or "").strip()
        if not token:
            raise ApiError(400, "Missing token", "accessToken is required")
        result: dict[str, Any] = {"at": iso(utcnow()), "fingerprint": cfg.fingerprint(), "by": actor(request)}
        try:
            p = auth._validate_jwt(token, cfg)
            result.update({"subject": p.email or p.id, "name": p.name, "roles": sorted(p.roles), "userType": p.user_type})
            if p.is_external:
                result.update(ok=False, message="Signed in as an external user. Test with an administrator from your own tenant.")
            elif "DataMart.Admin" not in p.roles:
                result.update(ok=False, message=("The token is valid but does not carry DataMart.Admin. In Entra ID, open "
                                                 "Enterprise applications → the API app → Users and groups and assign "
                                                 "yourself DataMart.Admin, then test again - otherwise enabling SSO "
                                                 "would lock you out."))
            else:
                result.update(ok=True, message=f"Signed in as {result['subject']} with {len(p.roles)} app role(s).")
        except ApiError as exc:
            result.update(ok=False, message=exc.detail or exc.title)
        saved = await put("draft", {**{k: v for k, v in draft.items() if k != "updatedAt"}, "verification": result})
        return {"verification": result, "draftStatus": status_of(saved)}

    @r.post("/admin/v1/sso/activate", tags=["administration"], summary="Enforce the verified draft everywhere")
    async def activate(request: Request):
        admin(request)
        draft = (await records())["draft"]
        st = status_of(draft)
        if not draft or not st["canActivate"]:
            reason = ("; ".join(e["message"] for e in st.get("errors") or []) or
                      ("Single sign-on settings are locked by the deployment" if settings_locked() else
                       "Run Test sign-in with an account that has DataMart.Admin first"))
            raise ApiError(409, "Cannot enable yet", reason)
        doc = {k: draft.get(k) for k in EDITABLE if k in draft}
        await put("active", {**doc, "updatedBy": actor(request), "verification": draft.get("verification")})
        await auth.refresh(force=True)
        return {"effective": auth.sso.to_dict(), "public": auth.sso.public()}

    @r.post("/admin/v1/sso/reset", tags=["administration"], summary="Return to the deployment's sign-in settings")
    async def reset(request: Request):
        admin(request)
        resp = await process.request("DELETE", "/v1/sso/active")
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, "Reset failed", resp.text[:300])
        await auth.refresh(force=True)
        return {"effective": auth.sso.to_dict(), "public": auth.sso.public()}

    return r
