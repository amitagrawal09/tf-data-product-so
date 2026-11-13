"""Render the canonical data model to JSON Schema files under cdm/.

    python -m tfso.tools.export_schema [--out cdm]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..common.cdm import CDM_VERSION, OrderGroup, OrderHistoryEntry, OrderStatusChanged, SalesOrder, StatusEvent

BASE = "https://schemas.taylorfarms.example/sales-order"


def schema_for(model, name: str, title: str) -> dict:
    s = model.model_json_schema(by_alias=True, mode="serialization")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": f"{BASE}/{name}.v1.json",
            "title": title, "x-cdm-version": CDM_VERSION, **{k: v for k, v in s.items() if k != "title"}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="cdm")
    out = Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    targets = [(SalesOrder, "sales-order", "Canonical Sales Order"),
               (OrderHistoryEntry, "order-history-entry", "Order lifecycle history entry"),
               (OrderGroup, "order-group", "Order group lifecycle"),
               (OrderStatusChanged, "order-status-changed", "OrderStatusChanged event data (sales-order-events)"),
               (StatusEvent, "status-event", "Inbound status event from Tier-3 systems")]
    for model, name, title in targets:
        path = out / f"{name}.v1.schema.json"
        path.write_text(json.dumps(schema_for(model, name, title), indent=2) + "\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
