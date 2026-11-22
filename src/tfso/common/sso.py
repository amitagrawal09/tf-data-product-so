"""Single sign-on configuration that an administrator can change at runtime from the console's Settings page.

Effective configuration = deployment defaults (TFSO_AUTH_MODE / TFSO_ENTRA_* environment variables) overlaid with
the *active* record saved through Settings. The Process API owns the record (table `sso_settings`); the Experience
and System APIs read it through `GET /v1/sso/active` and refresh it every few seconds, so a change takes effect
everywhere without a redeploy.

Safety rules (docs/09-connect-entra-id.md):
  * A configuration is edited as a *draft*. Switching to `entra` requires a successful test sign-in against the
    draft by a user whose token carries `DataMart.Admin`, so an administrator cannot lock everyone out.
  * `dev` personas are never allowed when TFSO_ENV=prod, whatever is saved.
  * TFSO_SSO_SETTINGS=locked makes the environment variables authoritative (Settings becomes read-only).
  * Break-glass: `./deploy.sh sso-reset` (service key) deletes the active record and falls back to the deployment.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import Settings

GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
MODES = ("dev", "entra", "apikey")
EDITABLE = ("mode", "tenantId", "apiClientId", "spaClientId", "externalIssuer", "externalAuthority",
            "externalClientId", "externalApiClientId", "claimCustomer", "claimVendor", "claimRegions", "groupRoleMap")

# Same namespace as scripts/entra-setup.sh so manually created roles get identical ids.
ID_NAMESPACE = uuid.UUID("6f1c3b8e-2d47-4c1a-9e55-7a0b6c1d2e3f")
ROLE_DISPLAY = {
    "DataMart.Admin": "Data Mart administrator", "DataMart.Steward": "Data steward", "DataMart.Export": "Data export",
    "PII.Read": "Personal data reader", "Orders.Read": "Order status reader", "Orders.Operate": "Order operator",
    "Domain.SalesOrder.Read": "Sales Order domain reader", "Domain.Customer.Read": "Customer domain reader",
    "Domain.Product.Read": "Product domain reader", "Domain.Logistics.Read": "Logistics domain reader",
    "Domain.Vendor.Read": "Vendor domain reader", "Domain.Procurement.Read": "Procurement domain reader",
    "Domain.Finance.Read": "Finance domain reader", "External.Customer": "External customer",
    "External.Vendor": "External vendor",
}


def _clean(v: Any) -> str | None:
    v = (str(v).strip() if v is not None else "")
    return v or None


@dataclass(frozen=True)
class SsoConfig:
    mode: str = "apikey"
    tenantId: str | None = None
    apiClientId: str | None = None
    spaClientId: str | None = None
    externalIssuer: str | None = None
    externalAuthority: str | None = None
    externalClientId: str | None = None       # console (SPA) app in the External ID tenant, if different
    externalApiClientId: str | None = None    # API app in the External ID tenant (token audience)
    externalJwks: str | None = None
    claimCustomer: str = "extension_customerId"
    claimVendor: str = "extension_vendorId"
    claimRegions: str = "extension_regions"
    groupRoleMap: dict[str, str] | None = None
    extraAudiences: tuple[str, ...] = ()
    source: str = "environment"
    updatedAt: str | None = None
    updatedBy: str | None = None

    # ------------------------------------------------------------------ derived values
    @property
    def authority(self) -> str | None:
        return f"https://login.microsoftonline.com/{self.tenantId}" if self.tenantId else None

    @property
    def issuer(self) -> str | None:
        return f"{self.authority}/v2.0" if self.tenantId else None

    @property
    def scope(self) -> str | None:
        return f"api://{self.apiClientId}/access_as_user" if self.apiClientId else None

    @property
    def audiences(self) -> list[str]:
        out: set[str] = set()
        for a in (self.apiClientId, self.externalApiClientId, *self.extraAudiences):
            if a:
                out |= {a, a.replace("api://", ""), a if a.startswith("api://") else f"api://{a}"}
        return sorted(out)

    def editable(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in EDITABLE}

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.editable(), sort_keys=True, default=str).encode()).hexdigest()[:16]

    def public(self) -> dict[str, Any]:
        """What the console needs to sign users in. Identifiers only - nothing here is secret."""
        out: dict[str, Any] = {"mode": self.mode, "source": self.source}
        if self.mode == "entra":
            out.update({
                "tenantId": self.tenantId, "clientId": self.spaClientId, "authority": self.authority,
                "scopes": [s for s in (self.scope, "openid", "profile", "offline_access") if s],
                "externalAuthority": self.externalAuthority or "",
                "externalClientId": self.externalClientId or "",
                "externalScopes": ([f"api://{self.externalApiClientId}/access_as_user", "openid", "profile", "offline_access"]
                                   if self.externalApiClientId else []),
            })
        return out

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["extraAudiences"] = list(self.extraAudiences)
        d.update({"authority": self.authority, "issuer": self.issuer, "scope": self.scope, "audiences": self.audiences,
                  "fingerprint": self.fingerprint()})
        return d


def from_env(settings: Settings) -> SsoConfig:
    """Deployment defaults from the environment (deploy.sh / Bicep)."""
    listed = [a.strip() for a in (settings.entra_audience or "").split(",") if a.strip()]
    api_id = _clean(os.getenv("TFSO_ENTRA_API_CLIENT_ID")) or next(
        (a.replace("api://", "") for a in listed if GUID.match(a.replace("api://", ""))), None)
    group_map = None
    if settings.group_role_map:
        try:
            group_map = json.loads(settings.group_role_map)
        except ValueError:
            group_map = None
    return SsoConfig(
        mode=settings.auth_mode, tenantId=settings.entra_tenant_id, apiClientId=api_id,
        spaClientId=_clean(os.getenv("TFSO_ENTRA_SPA_CLIENT_ID")), externalIssuer=settings.entra_external_issuer,
        externalAuthority=_clean(os.getenv("TFSO_ENTRA_EXTERNAL_AUTHORITY")), externalJwks=settings.entra_external_jwks,
        claimCustomer=settings.claim_customer, claimVendor=settings.claim_vendor, claimRegions=settings.claim_regions,
        groupRoleMap=group_map, extraAudiences=tuple(listed), source="environment")


def settings_locked() -> bool:
    return (os.getenv("TFSO_SSO_SETTINGS") or "").strip().lower() in ("locked", "disabled", "off", "false")


def merge(env: SsoConfig, record: dict[str, Any] | None) -> SsoConfig:
    """Overlay a saved record on the deployment defaults."""
    if not record or settings_locked():
        return env
    vals = {k: record.get(k) for k in EDITABLE if k in record}
    for k in list(vals):
        if k != "groupRoleMap":
            vals[k] = _clean(vals[k])
    for k in ("claimCustomer", "claimVendor", "claimRegions"):
        if not vals.get(k):
            vals.pop(k, None)
    return SsoConfig(**{**asdict(env), "extraAudiences": (), **vals, "source": "settings",
                        "updatedAt": record.get("updatedAt"), "updatedBy": record.get("updatedBy")})


def validate(cfg: SsoConfig, *, is_prod: bool) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []

    def err(fld: str, msg: str):
        errors.append({"field": fld, "message": msg})

    if cfg.mode not in ("dev", "entra"):
        err("mode", "Mode must be dev (test personas) or entra (Microsoft Entra ID)")
    if is_prod and cfg.mode != "entra":
        err("mode", "Production always uses Entra ID single sign-on")
    if cfg.mode == "entra":
        for fld, label in (("tenantId", "Directory (tenant) ID"), ("apiClientId", "API application (client) ID"),
                           ("spaClientId", "Console application (client) ID")):
            v = getattr(cfg, fld)
            if not v:
                err(fld, f"{label} is required for Entra ID sign-in")
            elif not GUID.match(v):
                err(fld, f"{label} must be a GUID such as 00000000-0000-0000-0000-000000000000")
        if cfg.apiClientId and cfg.spaClientId and cfg.apiClientId == cfg.spaClientId:
            err("spaClientId", "The console and the API must be two different app registrations")
    for fld in ("externalClientId", "externalApiClientId"):
        v = getattr(cfg, fld)
        if v and not GUID.match(v):
            err(fld, "Must be a GUID")
    if cfg.externalIssuer and not cfg.externalIssuer.startswith("https://"):
        err("externalIssuer", "Issuer must be an https:// URL ending in /v2.0")
    if cfg.externalAuthority and not cfg.externalAuthority.startswith("https://"):
        err("externalAuthority", "Authority must be an https:// URL")
    if bool(cfg.externalIssuer) != bool(cfg.externalAuthority):
        err("externalAuthority" if cfg.externalIssuer else "externalIssuer",
            "Customer/vendor sign-in needs both the External ID issuer and authority")
    if cfg.groupRoleMap is not None:
        if not isinstance(cfg.groupRoleMap, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                             for k, v in cfg.groupRoleMap.items()):
            err("groupRoleMap", 'Group mapping must be a JSON object: {"<group object id>": "<app role>"}')
        elif any(v not in ROLE_DISPLAY for v in cfg.groupRoleMap.values()):
            err("groupRoleMap", "Group mapping values must be app role names such as Domain.Finance.Read")
    return errors


def app_roles_manifest() -> list[dict[str, Any]]:
    """The `appRoles` array for the API app registration manifest (same ids as scripts/entra-setup.sh)."""
    from .identity import ROLES
    out = []
    for r in ROLES:
        value = r["role"]
        external = value.startswith("External.")
        out.append({"allowedMemberTypes": ["User"] if external else ["User", "Application"],
                    "description": r["description"], "displayName": ROLE_DISPLAY.get(value, value),
                    "id": str(uuid.uuid5(ID_NAMESPACE, f"approle:{value}")), "isEnabled": True, "value": value})
    return out


@dataclass
class VerifyResult:
    ok: bool
    subject: str | None = None
    name: str | None = None
    roles: list[str] = field(default_factory=list)
    userType: str | None = None
    message: str = ""
