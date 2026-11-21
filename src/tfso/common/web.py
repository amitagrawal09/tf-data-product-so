"""FastAPI app factory: correlation ids, RFC 7807 problems, auth, structured logging, telemetry."""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .identity import Principal

correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")
PUBLIC_PATHS = ("/health", "/docs", "/redoc", "/openapi.json")


def correlation_id() -> str:
    return correlation_id_var.get() or str(uuid.uuid4())


class ApiError(Exception):
    def __init__(self, status: int, title: str, detail: str | None = None, errors: list | None = None,
                 type_: str | None = None, headers: dict | None = None):
        super().__init__(detail or title)
        self.status, self.title, self.detail, self.errors = status, title, detail, errors or []
        self.type = type_ or f"https://tfso.taylorfarms.example/problems/{title.lower().replace(' ', '-')}"
        self.headers = headers or {}


def problem(status: int, title: str, detail: str | None, instance: str, errors: list | None = None,
            type_: str | None = None, headers: dict | None = None) -> JSONResponse:
    body = {"type": type_ or "about:blank", "title": title, "status": status, "detail": detail,
            "instance": instance, "correlationId": correlation_id_var.get(), "errors": errors or []}
    h = {"x-correlation-id": correlation_id_var.get()}
    h.update(headers or {})
    return JSONResponse(body, status_code=status, media_type="application/problem+json", headers=h)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname,
                   "logger": record.name, "msg": record.getMessage(), "correlationId": correlation_id_var.get()}
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(service: str) -> None:
    root = logging.getLogger()
    if not any(getattr(h, "_tfso", False) for h in root.handlers):
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(JsonFormatter())
        h._tfso = True  # type: ignore[attr-defined]
        root.addHandler(h)
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def configure_telemetry(settings: Settings) -> None:
    if not settings.appinsights_connection_string:
        return
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor(connection_string=settings.appinsights_connection_string,
                                logger_name="tfso")
    except Exception:  # noqa: BLE001
        logging.getLogger("tfso").exception("Azure Monitor telemetry could not be configured")


# ------------------------------------------------------------------------------------------- auth
class Authenticator:
    """Resolves every request to one Principal (docs/07 §1). A user identity always wins over the service key."""

    PUBLIC_DEV_PATHS = ("/auth/v1/personas",)

    def __init__(self, settings: Settings):
        self.settings = settings
        self._jwks: dict[str, Any] = {}
        if settings.auth_mode == "dev" and settings.is_prod:
            raise RuntimeError("TFSO_AUTH_MODE=dev is not allowed when TFSO_ENV=prod - configure Entra ID (entra)")

    def resolve(self, request: Request) -> Principal:
        from .identity import SERVICE, from_persona
        mode = self.settings.auth_mode
        if mode == "none":
            return SERVICE
        auth = request.headers.get("authorization", "")
        persona = request.headers.get("x-dev-persona")
        if mode == "entra" and auth.lower().startswith("bearer "):
            return self._validate_jwt(auth.split(" ", 1)[1])
        if mode == "dev" and persona:
            p = from_persona(persona.strip())
            if p is None:
                raise ApiError(401, "Unauthorized", f"Unknown dev persona '{persona}'")
            return p
        key = request.headers.get("x-api-key")
        if key and key in self.settings.api_keys:
            return SERVICE
        if mode == "dev" and request.url.path.endswith(self.PUBLIC_DEV_PATHS):
            return SERVICE  # persona list for the sign-in page
        detail = {"apikey": "Missing or invalid x-api-key header",
                  "dev": "Sign in: send x-dev-persona (dev) or x-api-key (service)",
                  "entra": "Sign in with Microsoft Entra ID: Bearer token required"}.get(mode, "Unauthorized")
        if mode not in ("apikey", "dev", "entra"):
            raise ApiError(500, "Misconfigured auth", f"Unknown TFSO_AUTH_MODE {mode}")
        raise ApiError(401, "Unauthorized", detail)

    def check(self, request: Request) -> dict[str, Any]:
        """Backward-compatible: sets request.state.user and returns the legacy principal dict."""
        user = self.resolve(request)
        request.state.user = user
        return user.legacy()

    def _jwks_client(self, url: str):
        import jwt
        if url not in self._jwks:
            self._jwks[url] = jwt.PyJWKClient(url)
        return self._jwks[url]

    def _validate_jwt(self, token: str) -> Principal:
        import json as _json

        import jwt
        from .identity import from_claims
        s = self.settings
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
        except Exception as exc:  # noqa: BLE001
            raise ApiError(401, "Unauthorized", f"Malformed token: {exc}") from exc
        iss = unverified.get("iss", "")
        workforce = f"https://login.microsoftonline.com/{s.entra_tenant_id}/v2.0"
        if iss == workforce:
            jwks = f"https://login.microsoftonline.com/{s.entra_tenant_id}/discovery/v2.0/keys"
        elif s.entra_external_issuer and iss == s.entra_external_issuer:
            jwks = s.entra_external_jwks or iss.replace("/v2.0", "/discovery/v2.0/keys")
        else:
            raise ApiError(401, "Unauthorized", f"Untrusted token issuer {iss}")
        # Comma-separated: workforce API and (optionally) the External ID API; v2 tokens carry the bare GUID.
        listed = [a.strip() for a in (s.entra_audience or "").split(",") if a.strip()]
        audiences = sorted({x for a in listed for x in (a, a.replace("api://", ""))})
        try:
            key = self._jwks_client(jwks).get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=audiences, issuer=iss)
        except Exception as exc:  # noqa: BLE001
            raise ApiError(401, "Unauthorized", f"Invalid token: {exc}") from exc
        group_map = _json.loads(s.group_role_map) if s.group_role_map else None
        return from_claims(claims, external_issuer=s.entra_external_issuer, customer_claim=s.claim_customer,
                           vendor_claim=s.claim_vendor, regions_claim=s.claim_regions, group_role_map=group_map)


def current_user(request: Request) -> Principal:
    from .identity import SERVICE
    return getattr(request.state, "user", None) or SERVICE


def forbid(detail: str) -> ApiError:
    return ApiError(403, "Forbidden", detail)


def create_app(settings: Settings, title: str, description: str, version: str = "1.0.0") -> FastAPI:
    configure_logging(settings.service)
    configure_telemetry(settings)
    app = FastAPI(title=title, description=description, version=version,
                  contact={"name": "Sales Order data product team"},
                  license_info={"name": "Proprietary"})
    app.state.settings = settings
    app.state.auth = Authenticator(settings)
    log = logging.getLogger(f"tfso.{settings.service}")

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["x-correlation-id", "etag", "location", "content-disposition", "x-tfso-row-count", "x-tfso-export-id", "x-tfso-fulfilled-by"])

    @app.middleware("http")
    async def _context(request: Request, call_next):
        cid = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        token = correlation_id_var.set(cid)
        start = time.perf_counter()
        try:
            if request.method != "OPTIONS" and not request.url.path.startswith(PUBLIC_PATHS):
                try:
                    request.state.principal = app.state.auth.check(request)
                except ApiError as e:
                    return problem(e.status, e.title, e.detail, request.url.path, type_=e.type)
            response = await call_next(request)
            response.headers["x-correlation-id"] = cid
            log.info("%s %s -> %s (%.1f ms)", request.method, request.url.path, response.status_code,
                     (time.perf_counter() - start) * 1000)
            return response
        finally:
            correlation_id_var.reset(token)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return problem(exc.status, exc.title, exc.detail, request.url.path, exc.errors, exc.type, exc.headers)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        return problem(exc.status_code, str(exc.detail) if exc.status_code != 404 else "Not Found",
                       str(exc.detail), request.url.path)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        errors = [{"field": ".".join(str(p) for p in e.get("loc", [])), "message": e.get("msg"), "type": e.get("type")}
                  for e in exc.errors()]
        return problem(422, "Request validation failed", "The request body or parameters are invalid",
                       request.url.path, errors)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error")
        return problem(500, "Internal Server Error", "An unexpected error occurred", request.url.path)

    return app


# ------------------------------------------------------------------------------ inter-service client
_TRANSPORTS: dict[str, httpx.AsyncBaseTransport] = {}


def register_transport(base_url: str, transport: httpx.AsyncBaseTransport) -> None:
    """Tests route service-to-service calls to in-process ASGI apps."""
    _TRANSPORTS[base_url.rstrip("/")] = transport


class ServiceClient:
    def __init__(self, base_url: str, settings: Settings, timeout: float = 20):
        self.base_url = base_url.rstrip("/")
        self.settings = settings
        transport = _TRANSPORTS.get(self.base_url)
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport)

    def _headers(self, extra: dict | None = None) -> dict[str, str]:
        h = {"x-correlation-id": correlation_id_var.get() or str(uuid.uuid4())}
        if self.settings.api_keys:
            h["x-api-key"] = self.settings.api_keys[0]
        h.update(extra or {})
        return h

    async def request(self, method: str, path: str, *, json_body: Any = None, params: dict | None = None,
                      headers: dict | None = None, content: bytes | str | None = None) -> httpx.Response:
        return await self.client.request(method, path, json=json_body, params=params, content=content,
                                         headers=self._headers(headers))

    async def get_json(self, path: str, params: dict | None = None, *, not_found_ok: bool = False) -> Any:
        resp = await self.request("GET", path, params=params)
        if not_found_ok and resp.status_code == 404:
            return None
        relay(resp)
        return resp.json()

    async def post_json(self, path: str, body: Any, headers: dict | None = None) -> Any:
        resp = await self.request("POST", path, json_body=body, headers=headers)
        relay(resp)
        return resp.json()

    async def aclose(self) -> None:
        await self.client.aclose()


def relay(resp: httpx.Response) -> None:
    """Re-raise a downstream problem+json as our own ApiError (preserving status and detail)."""
    if resp.status_code < 400:
        return
    try:
        body = resp.json()
    except ValueError:
        body = {"title": resp.reason_phrase, "detail": resp.text[:500]}
    raise ApiError(resp.status_code, body.get("title") or "Downstream error", body.get("detail"),
                   body.get("errors"), body.get("type"))
