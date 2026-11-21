"""Runtime configuration, read once from environment variables (see docs/00-solution-blueprint.md §3)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    service: str
    env: str
    db_url: str
    bus_mode: str
    servicebus_fqdn: str | None
    servicebus_connection: str | None
    eventhub_fqdn: str | None
    eventhub_name: str
    process_url: str
    system_url: str
    experience_url: str
    auth_mode: str
    api_keys: tuple[str, ...]
    entra_tenant_id: str | None
    entra_audience: str | None
    entra_external_issuer: str | None
    entra_external_jwks: str | None
    claim_customer: str
    claim_vendor: str
    claim_regions: str
    group_role_map: str | None
    d365_mode: str
    d365_url: str | None
    d365_tenant_id: str | None
    d365_client_id: str | None
    d365_client_secret: str | None
    d365_company: str
    sim_autoprogress: bool
    sim_step_seconds: float
    lakehouse_mode: str
    lakehouse_account_url: str | None
    lakehouse_container: str
    lakehouse_local_path: str
    docintel_endpoint: str | None
    docintel_model_id: str
    appinsights_connection_string: str | None
    isa_sender_id: str
    bus_http_routes: dict[str, list[str]] = field(default_factory=dict)

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"


def default_http_routes(process_url: str, system_url: str) -> dict[str, list[str]]:
    """Local HTTP bus fan-out that mirrors the Service Bus topic/subscription topology."""
    return {
        "sales-order-commands": [f"{system_url}/v1/bus/sales-order-commands"],
        "system-status-events": [f"{process_url}/v1/bus/system-status-events"],
        "sales-order-events": [
            f"{system_url}/v1/bus/sales-order-events",
            f"{process_url}/v1/bus/sales-order-events",
        ],
    }


@lru_cache(maxsize=None)
def get_settings(service: str = "app") -> Settings:
    process_url = _env("TFSO_PROCESS_URL", "http://localhost:8002").rstrip("/")
    system_url = _env("TFSO_SYSTEM_URL", "http://localhost:8003").rstrip("/")
    routes_raw = _env("TFSO_BUS_HTTP_ROUTES")
    routes = json.loads(routes_raw) if routes_raw else default_http_routes(process_url, system_url)
    return Settings(
        service=service,
        env=_env("TFSO_ENV", "local"),
        db_url=_env("TFSO_DB_URL", f"sqlite:///./data/{service}.db"),
        bus_mode=_env("TFSO_BUS_MODE", "http"),
        servicebus_fqdn=_env("TFSO_SERVICEBUS_FQDN"),
        servicebus_connection=_env("TFSO_SERVICEBUS_CONNECTION"),
        eventhub_fqdn=_env("TFSO_EVENTHUB_FQDN"),
        eventhub_name=_env("TFSO_EVENTHUB_NAME", "order-status-stream"),
        process_url=process_url,
        system_url=system_url,
        experience_url=_env("TFSO_EXPERIENCE_URL", "http://localhost:8001").rstrip("/"),
        auth_mode=_env("TFSO_AUTH_MODE", "apikey"),
        api_keys=tuple(k.strip() for k in _env("TFSO_API_KEYS", "local-dev-key").split(",") if k.strip()),
        entra_tenant_id=_env("TFSO_ENTRA_TENANT_ID"),
        entra_audience=_env("TFSO_ENTRA_AUDIENCE"),
        entra_external_issuer=_env("TFSO_ENTRA_EXTERNAL_ISSUER"),
        entra_external_jwks=_env("TFSO_ENTRA_EXTERNAL_JWKS"),
        claim_customer=_env("TFSO_CLAIM_CUSTOMER", "extension_customerId"),
        claim_vendor=_env("TFSO_CLAIM_VENDOR", "extension_vendorId"),
        claim_regions=_env("TFSO_CLAIM_REGIONS", "extension_regions"),
        group_role_map=_env("TFSO_GROUP_ROLE_MAP"),
        d365_mode=_env("TFSO_D365_MODE", "simulator"),
        d365_url=_env("TFSO_D365_URL"),
        d365_tenant_id=_env("TFSO_D365_TENANT_ID"),
        d365_client_id=_env("TFSO_D365_CLIENT_ID"),
        d365_client_secret=_env("TFSO_D365_CLIENT_SECRET"),
        d365_company=_env("TFSO_D365_COMPANY", "usmf"),
        sim_autoprogress=_bool("TFSO_SIM_AUTOPROGRESS", False),
        sim_step_seconds=float(_env("TFSO_SIM_STEP_SECONDS", "5")),
        lakehouse_mode=_env("TFSO_LAKEHOUSE_MODE", "file"),
        lakehouse_account_url=_env("TFSO_LAKEHOUSE_ACCOUNT_URL"),
        lakehouse_container=_env("TFSO_LAKEHOUSE_CONTAINER", "bronze"),
        lakehouse_local_path=_env("TFSO_LAKEHOUSE_LOCAL_PATH", "./data/lakehouse"),
        docintel_endpoint=_env("TFSO_DOCINTEL_ENDPOINT"),
        docintel_model_id=_env("TFSO_DOCINTEL_MODEL_ID", "prebuilt-layout"),
        appinsights_connection_string=_env("APPLICATIONINSIGHTS_CONNECTION_STRING"),
        isa_sender_id=_env("TFSO_ISA_SENDER_ID", "TAYLORFARMS"),
        bus_http_routes=routes,
    )
