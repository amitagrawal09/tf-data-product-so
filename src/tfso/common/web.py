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
    def __init__(self, settings: Settings):
        self.settings = settings
        self._jwks = None

    def check(self, request: Request) -> dict[str, Any]:
        mode = self.settings.auth_mode
        if mode == "none":
            return {"sub": "anonymous", "mode": "none"}
        if mode == "apikey":
            key = request.headers.get("x-api-key")
            if key and key in self.settings.api_keys:
                return {"sub": f"apikey:{key[:4]}***", "mode": "apikey"}
            raise ApiError(401, "Unauthorized", "Missing or invalid x-api-key header")
        if mode == "entra":
            # APIM forwards the backend key in x-api-key for service-to-service calls behind the gateway.
            key = request.headers.get("x-api-key")
            if key and key in self.settings.api_keys:
                return {"sub": "apim", "mode": "apikey"}
            auth = request.headers.get("authorization", "")
            if not auth.lower().startswith("bearer "):
                raise ApiError(401, "Unauthorized", "Bearer token required")
            return self._validate_jwt(auth.split(" ", 1)[1])
        raise ApiError(500, "Misconfigured auth", f"Unknown TFSO_AUTH_MODE {mode}")

    def _validate_jwt(self, token: str) -> dict[str, Any]:
        import jwt
        tenant = self.settings.entra_tenant_id
        if self._jwks is None:
            self._jwks = jwt.PyJWKClient(f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys")
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            return jwt.decode(token, key, algorithms=["RS256"], audience=self.settings.entra_audience,
                              issuer=f"https://login.microsoftonline.com/{tenant}/v2.0")
        except Exception as exc:  # noqa: BLE001
            raise ApiError(401, "Unauthorized", f"Invalid token: {exc}") from exc


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
                       expose_headers=["x-correlation-id", "etag", "location"])

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
