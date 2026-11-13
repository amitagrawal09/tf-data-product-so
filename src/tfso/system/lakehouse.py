"""Lakehouse system API: lands order status events (CloudEvents) in the Bronze zone.

file mode : ./data/lakehouse/bronze/sales-order-events/date=YYYY-MM-DD/part-HH.jsonl
adls mode : abfss://bronze@<account>/sales-order-events/date=YYYY-MM-DD/<eventId>.json  (Auto Loader input)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..common.config import Settings
from ..common.util import utcnow


class LakehouseWriter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.mode = settings.lakehouse_mode
        self._fs = None

    def _adls(self):
        if self._fs is None:
            from azure.identity import DefaultAzureCredential
            from azure.storage.filedatalake import DataLakeServiceClient
            svc = DataLakeServiceClient(self.settings.lakehouse_account_url, credential=DefaultAzureCredential())
            self._fs = svc.get_file_system_client(self.settings.lakehouse_container)
        return self._fs

    def write(self, dataset: str, envelope: dict[str, Any]) -> str:
        now = utcnow()
        partition = f"{dataset}/date={now:%Y-%m-%d}"
        line = json.dumps(envelope, separators=(",", ":"))
        if self.mode == "adls":
            path = f"{partition}/{envelope['id']}.json"
            self._adls().get_file_client(path).upload_data(line, overwrite=True)
            return f"abfss://{self.settings.lakehouse_container}/{path}"
        base = Path(self.settings.lakehouse_local_path) / "bronze" / partition
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"part-{now:%H}.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return str(path)

    def list_files(self) -> list[dict[str, Any]]:
        if self.mode == "adls":
            return [{"path": p.name, "size": p.content_length} for p in self._adls().get_paths(max_results=200)]
        root = Path(self.settings.lakehouse_local_path) / "bronze"
        if not root.exists():
            return []
        out = []
        for p in sorted(root.rglob("*.jsonl")):
            with open(p, encoding="utf-8") as fh:
                rows = sum(1 for _ in fh)
            out.append({"path": os.path.relpath(p, root), "rows": rows, "bytes": p.stat().st_size})
        return out
