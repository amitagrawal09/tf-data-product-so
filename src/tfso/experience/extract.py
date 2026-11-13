"""Order extraction for the e-mail channel: message text, CSV, Excel, plain text and PDF attachments.

PDFs are read with Azure AI Document Intelligence when TFSO_DOCINTEL_ENDPOINT is configured
(prebuilt-layout or a custom purchase-order model), otherwise with a local text extractor.
Every extraction returns a confidence score; DQ-011 holds low-confidence orders for review.
"""
from __future__ import annotations

import asyncio
import base64
import csv
import io
import re
from typing import Any

import httpx

from ..common.config import Settings

HEADER_PATTERNS = {
    "poNumber": re.compile(r"(?:P\.?O\.?|purchase\s*order)\s*(?:number|no\.?|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-_/]{2,})", re.I),
    "customerId": re.compile(r"customer(?:\s*(?:id|account|acct|no\.?|#))?\s*[:#]\s*([A-Z]-?\d{3,}|[A-Z0-9\-]{3,})", re.I),
    "shipTo": re.compile(r"ship[\s\-]*to(?:\s*(?:id|code|location))?\s*[:#]\s*([A-Z0-9][A-Z0-9\-]{2,})", re.I),
    "deliveryDate": re.compile(r"(?:deliver(?:y)?(?:\s*date)?|need(?:ed)?\s*(?:by|on)|due)\s*[:#]?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})", re.I),
}
LINE_PATTERN = re.compile(
    r"^\s*(?:\d+[.)]\s+)?(?P<item>[A-Z]{1,4}-\d{3,6})\s*[,;\t ]\s*(?:qty[:\s]*)?(?P<qty>\d+(?:\.\d+)?)\s*"
    r"(?P<uom>CA|CS|EA|LB|BX|PL|CASES?|EACH)?\b(?:\s*(?:@|x|at)?\s*\$?(?P<price>\d+\.\d{2}))?", re.I | re.M)
UOM_MAP = {"CS": "CA", "CASE": "CA", "CASES": "CA", "EACH": "EA"}
COLUMN_ALIASES = {
    "item": {"item", "itemnumber", "item number", "item #", "sku", "product", "product code", "vendor item"},
    "buyer": {"buyer item", "customer item", "your item"},
    "qty": {"qty", "quantity", "cases", "order qty"},
    "uom": {"uom", "unit", "unit of measure"},
    "price": {"price", "unit price", "cost", "unit cost"},
}


def _norm_date(s: str | None) -> str | None:
    if not s:
        return None
    if "/" in s:
        m, d, y = s.split("/")
        y = ("20" + y) if len(y) == 2 else y
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    return s


def parse_text(text: str) -> dict[str, Any]:
    header = {}
    for key, pat in HEADER_PATTERNS.items():
        m = pat.search(text)
        if m:
            header[key] = m.group(1).strip()
    lines = []
    for m in LINE_PATTERN.finditer(text):
        uom = (m.group("uom") or "CA").upper()
        lines.append({"itemNumber": m.group("item").upper(), "quantity": m.group("qty"),
                      "uom": UOM_MAP.get(uom, uom), "unitPrice": m.group("price")})
    return {"header": header, "lines": lines}


def _col(headers: list[str], kind: str) -> int | None:
    for i, h in enumerate(headers):
        if (h or "").strip().lower() in COLUMN_ALIASES[kind]:
            return i
    return None


def parse_table(rows: list[list[Any]]) -> list[dict[str, Any]]:
    rows = [r for r in rows if any(c not in (None, "") for c in r)]
    if not rows:
        return []
    headers = [str(c or "") for c in rows[0]]
    ci, cb, cq, cu, cp = (_col(headers, k) for k in ("item", "buyer", "qty", "uom", "price"))
    if cq is None or (ci is None and cb is None):
        return []
    out = []
    for r in rows[1:]:
        def get(i):
            return None if i is None or i >= len(r) or r[i] in (None, "") else str(r[i]).strip()
        if not get(cq):
            continue
        uom = (get(cu) or "CA").upper()
        out.append({"itemNumber": get(ci), "buyerItemNumber": get(cb), "quantity": get(cq),
                    "uom": UOM_MAP.get(uom, uom), "unitPrice": get(cp)})
    return out


async def pdf_text(data: bytes, settings: Settings) -> tuple[str, str, str | None]:
    """Returns (text, method, model_id)."""
    if settings.docintel_endpoint:
        from azure.identity.aio import DefaultAzureCredential
        cred = DefaultAzureCredential()
        token = (await cred.get_token("https://cognitiveservices.azure.com/.default")).token
        await cred.close()
        url = (f"{settings.docintel_endpoint.rstrip('/')}/documentintelligence/documentModels/"
               f"{settings.docintel_model_id}:analyze?api-version=2024-11-30")
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(url, json={"base64Source": base64.b64encode(data).decode()},
                             headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
            op = r.headers["operation-location"]
            for _ in range(60):
                await asyncio.sleep(1)
                res = (await c.get(op, headers={"Authorization": f"Bearer {token}"})).json()
                if res.get("status") == "succeeded":
                    return res["analyzeResult"].get("content", ""), "OCR", settings.docintel_model_id
                if res.get("status") == "failed":
                    raise RuntimeError(f"Document Intelligence failed: {res.get('error')}")
        raise TimeoutError("Document Intelligence did not finish in 60 s")
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages), "TEMPLATE", "pypdf"


async def extract_email(message: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Returns {header, lines, method, modelId, confidence, lowConfidenceFields, sources}."""
    body = message.get("body") or ""
    parsed = parse_text(body + "\n" + (message.get("subject") or ""))
    header, lines = dict(parsed["header"]), list(parsed["lines"])
    method, model_id, sources = "TEMPLATE", "regex-v1", ["body"]
    for att in message.get("attachments") or []:
        name = (att.get("fileName") or "").lower()
        data = base64.b64decode(att.get("contentBase64") or "")
        ctype = (att.get("contentType") or "").lower()
        att_lines: list[dict] = []
        if name.endswith(".csv") or "csv" in ctype:
            att_lines = parse_table(list(csv.reader(io.StringIO(data.decode("utf-8-sig")))))
            method = "STRUCTURED"
        elif name.endswith((".xlsx", ".xlsm")) or "spreadsheet" in ctype:
            from openpyxl import load_workbook
            ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
            att_lines = parse_table([list(r) for r in ws.iter_rows(values_only=True)])
            method = "STRUCTURED"
        elif name.endswith(".pdf") or "pdf" in ctype:
            text, method, model_id = await pdf_text(data, settings)
            p = parse_text(text)
            header = {**p["header"], **header}
            att_lines = p["lines"]
        elif name.endswith(".txt") or ctype.startswith("text/"):
            p = parse_text(data.decode("utf-8", errors="replace"))
            header = {**p["header"], **header}
            att_lines = p["lines"]
        if att_lines:
            lines = att_lines  # an attached order document wins over loose body text
            sources.append(att.get("fileName") or "attachment")
    required = ["poNumber", "shipTo", "deliveryDate"]
    missing = [f for f in required if not header.get(f)]
    if not lines:
        missing.append("lines")
    bad_lines = [i + 1 for i, l in enumerate(lines) if not (l.get("itemNumber") or l.get("buyerItemNumber")) or not l.get("quantity")]
    score = 1.0 - 0.2 * len(missing) - (0.1 * len(bad_lines))
    if method == "OCR":
        score -= 0.05
    if not header.get("customerId"):
        missing.append("customerId")
        score -= 0.05
    return {"header": header, "lines": lines, "method": method, "modelId": model_id,
            "confidence": round(max(0.0, min(1.0, score)), 2), "lowConfidenceFields": missing, "sources": sources}
