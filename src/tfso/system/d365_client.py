"""Dynamics 365 Finance & Operations OData client (client-credentials flow via Microsoft Entra ID)."""
from __future__ import annotations

import time
from typing import Any

import httpx


class D365Client:
    def __init__(self, base_url: str, tenant_id: str, client_id: str, client_secret: str, company: str):
        self.base_url = base_url.rstrip("/")
        self.tenant_id, self.client_id, self.client_secret = tenant_id, client_id, client_secret
        self.company = company
        self._token: str | None = None
        self._expires = 0.0
        self.http = httpx.AsyncClient(timeout=60)

    async def _auth(self) -> dict[str, str]:
        if not self._token or time.time() > self._expires - 120:
            resp = await self.http.post(
                f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                data={"grant_type": "client_credentials", "client_id": self.client_id,
                      "client_secret": self.client_secret, "scope": f"{self.base_url}/.default"})
            resp.raise_for_status()
            body = resp.json()
            self._token, self._expires = body["access_token"], time.time() + int(body.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json",
                "OData-Version": "4.0", "OData-MaxVersion": "4.0"}

    async def get_all(self, entity: str, *, select: str | None = None, filter_: str | None = None) -> list[dict[str, Any]]:
        params = {"cross-company": "true"} if not self.company else {}
        q = [f"$filter={filter_}"] if filter_ else []
        if select:
            q.append(f"$select={select}")
        url = f"{self.base_url}/data/{entity}" + ("?" + "&".join(q) if q else "")
        out: list[dict] = []
        while url:
            resp = await self.http.get(url, headers=await self._auth(), params=params)
            resp.raise_for_status()
            body = resp.json()
            out.extend(body.get("value", []))
            url, params = body.get("@odata.nextLink"), None
        return out

    async def get(self, entity_path: str) -> dict[str, Any]:
        resp = await self.http.get(f"{self.base_url}/data/{entity_path}", headers=await self._auth())
        resp.raise_for_status()
        return resp.json()

    async def create(self, entity: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = await self.http.post(f"{self.base_url}/data/{entity}", json=body,
                                    headers={**await self._auth(), "Content-Type": "application/json"})
        if resp.status_code >= 400:
            raise D365Error(resp.status_code, resp.text[:2000])
        return resp.json()

    async def action(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = await self.http.post(f"{self.base_url}/data/{path}", json=body,
                                    headers={**await self._auth(), "Content-Type": "application/json"})
        if resp.status_code >= 400:
            raise D365Error(resp.status_code, resp.text[:2000])
        return resp.json() if resp.content else {}


class D365Error(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"D365 returned {status}: {body}")
        self.status, self.body = status, body
