"""Local stand-in for Azure API Management + static host for the Order Operations Console.

Routes mirror the APIM APIs: /api/exp/* → Experience, /api/process/* → Process, /api/system/* → System.
Like the APIM policy it stamps x-correlation-id. It injects the backend x-api-key only in legacy apikey mode;
with SSO (dev personas / Entra ID) the user's identity headers are forwarded instead.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..common.config import get_settings

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "host", "content-length", "content-encoding"}


def create_gateway_app() -> FastAPI:
    settings = get_settings("gateway")
    backends = {"exp": settings.experience_url, "process": settings.process_url, "system": settings.system_url}
    frontend = Path(os.getenv("TFSO_FRONTEND_DIR", Path(__file__).resolve().parents[3] / "frontend"))
    client = httpx.AsyncClient(timeout=60)
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await client.aclose()

    app = FastAPI(title="Sales Order - local API gateway", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["x-correlation-id", "etag", "location"])

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "gateway", "backends": backends, "frontend": str(frontend)}

    @app.api_route("/api/{api}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(api: str, path: str, request: Request):
        base = backends.get(api)
        if not base:
            return JSONResponse({"title": "Unknown API", "status": 404, "detail": api}, status_code=404,
                                media_type="application/problem+json")
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
        headers.setdefault("x-correlation-id", str(uuid.uuid4()))
        lower = {k.lower() for k in headers}
        has_identity = "authorization" in lower or "x-dev-persona" in lower
        # Like APIM: inject the backend key only in legacy apikey mode and never for signed-in users,
        # otherwise every user request would run with service-level rights.
        if settings.auth_mode == "apikey" and not has_identity and settings.api_keys and "x-api-key" not in lower:
            headers["x-api-key"] = settings.api_keys[0]
        try:
            resp = await client.request(request.method, f"{base}/{path}", params=request.query_params,
                                        content=await request.body(), headers=headers)
        except httpx.HTTPError as exc:
            return JSONResponse({"title": "Bad Gateway", "status": 502, "detail": str(exc)}, status_code=502,
                                media_type="application/problem+json")
        out = {k: v for k, v in resp.headers.items() if k.lower() not in HOP_BY_HOP}
        return Response(content=resp.content, status_code=resp.status_code, headers=out)

    if frontend.exists():
        app.mount("/assets", StaticFiles(directory=frontend), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            target = (frontend / path).resolve()
            if path and target.is_file() and frontend.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(frontend / "index.html")

    return app


def __getattr__(name: str):
    if name == "app":
        globals()["app"] = create_gateway_app()
        return globals()["app"]
    raise AttributeError(name)
