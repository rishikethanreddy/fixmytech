"""Google Store "Repair pricing guide" collector (official mail-in repair estimates for Pixel phones).

The estimator page (``/in/repair-cost-estimator``, ``/us/repair-cost-estimator``) embeds the list of Pixel models
as ``["ISE-002329","Pixel 6a"]`` pairs. When a model is chosen, the page asks Google Store's own data endpoint
(``/_/Gstore/data/batchexecute``, rpc ``QQr9wd``) for that model's estimates. The collector makes the same requests,
one per model, through the polite fetcher (robots.txt allows both paths; Google's Terms of Service prohibit
automated access only where it violates such machine-readable instructions).

What the page states, and is therefore recorded with every price: estimates are for **out-of-warranty service**,
**may exclude tax**, final costs are confirmed after inspection, authorised providers set their own fees, and
delivery fees may apply. These are complete repair estimates (not part prices).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from fixmytech.pricing.collectors.base import CollectorError, ParseResult
from fixmytech.pricing.models import (
    PricePoint,
    PriceRecord,
    PriceScope,
    SourceEvidence,
    SourceType,
    TaxTreatment,
    record_id,
)

RPC_ID = "QQr9wd"
RPC_URL = "https://store.google.com/_/Gstore/data/batchexecute"
MARKETS = {
    # source_id: (country, hl, currency, page url)
    "google_in_estimator": ("IN", "en-IN", "INR", "https://store.google.com/in/repair-cost-estimator?hl=en-IN"),
    "google_us_estimator": ("US", "en-US", "USD", "https://store.google.com/us/repair-cost-estimator?hl=en-US"),
}
LIMITATIONS = [
    "Google's official estimate for out-of-warranty mail-in service; Google states it may exclude tax.",
    "Final cost is confirmed after Google inspects the device; delivery fees may apply.",
    "Authorised service providers set their own fees, which can differ.",
]
GROUP_LABEL = {1: "Accidental damage", 2: "Performance issues"}

_MODEL_RE = re.compile(r'\["(ISE-\d{3,8})","(Pixel[^"]{0,40})"\]')


def parse_models(page_html: str) -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for sku, name in _MODEL_RE.findall(page_html):
        seen.setdefault(sku, name.strip())
    if not seen:
        raise CollectorError("no Pixel model list found on the estimator page")
    return list(seen.items())


def rpc_form(country: str, hl: str, sku: str) -> dict[str, str]:
    inner = [[[[hl]], None, [country, None, None], 69, 1, None, None, None,
              ["android_group:eng.finsky.merchandising.prod"], None, None, None, None, None, None, None, 2], sku]
    freq = [[[RPC_ID, json.dumps(inner, separators=(",", ":")), None, "generic"]]]
    return {"f.req": json.dumps(freq, separators=(",", ":"))}


def rpc_params(country: str, hl: str) -> dict[str, str]:
    return {"rpcids": RPC_ID, "source-path": f"/{country.lower()}/repair-cost-estimator", "hl": hl, "rt": "c"}


def parse_rpc_response(text: str) -> list[tuple[int, str, str, Decimal]]:
    """Return (group, issue label, currency, amount) tuples from a batchexecute response."""
    payload = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('[["wrb.fr"'):
            payload = json.loads(line)
            break
    if payload is None:
        raise CollectorError("no wrb.fr payload in estimator response")
    entry = next((e for e in payload if len(e) > 2 and e[0] == "wrb.fr" and e[1] == RPC_ID), None)
    if entry is None or not isinstance(entry[2], str):
        raise CollectorError("estimator response has no data for the rpc")
    data: Any = json.loads(entry[2])
    out: list[tuple[int, str, str, Decimal]] = []
    try:
        for group_id, items in data[0]:
            for label, prices in items:
                currency, units, *nanos = prices[0]
                amount = Decimal(int(units)) + (Decimal(int(nanos[0])) / Decimal(10**9) if nanos else Decimal(0))
                out.append((int(group_id), str(label), str(currency), amount.quantize(Decimal("0.01"))))
    except (TypeError, ValueError, IndexError) as exc:
        raise CollectorError(f"unexpected estimator data shape: {exc}") from exc
    return out


def component_for(label: str) -> str:
    known = {
        "cracked screen": "service_screen",
        "screen issue (other)": "service_screen_other",
        "battery issue": "service_battery",
        "charging port damage": "service_charging_port",
        "camera issue (back)": "service_camera_rear",
        "camera issue (front)": "service_camera_front",
        "audio issue": "service_audio",
        "liquid damage": "service_liquid",
        "frame or back cover damage": "service_frame_back",
        "button issue": "service_buttons",
    }
    key = label.strip().lower()
    return known.get(key) or "service_" + re.sub(r"[^a-z0-9]+", "_", key).strip("_")


def collect(source_id: str, page_html: str, fetch_rpc: Callable[[str], str], retrieved_at: datetime,
            page_sha: str | None) -> ParseResult:
    country, _hl, currency, url = MARKETS[source_id]
    out = ParseResult()
    for sku, name in parse_models(page_html):
        out.items_seen += 1
        try:
            rows = parse_rpc_response(fetch_rpc(sku))
        except CollectorError as exc:
            out.rejected.append({"item": f"{name} ({sku})", "reason": str(exc)})
            continue
        if not rows:
            out.rejected.append({"item": f"{name} ({sku})", "reason": "no estimates listed"})
            continue
        for group, label, cur, amount in rows:
            if cur != currency or not Decimal(1) <= amount <= Decimal(1_000_000):
                out.rejected.append({"item": f"{name} {label}", "reason": f"unexpected value {cur} {amount}"})
                continue
            comp = component_for(label)
            out.records.append(PriceRecord(
                record_id=record_id(source_id, sku, comp), manufacturer="Google", model_name=name, model_code=None,
                region=country, currency=currency, component=comp, component_label=label, amount=amount,
                price_point=PricePoint.EXACT, scope=PriceScope.PARTS_AND_LABOUR, tax=TaxTreatment.UNKNOWN,
                includes=["parts", "labour"], excludes=["delivery fees (may apply)"], limitations=LIMITATIONS,
                evidence=SourceEvidence(
                    source_id=source_id, source_url=url, organization=f"Google Store ({country})",
                    source_type=SourceType.OFFICIAL_SERVICE_ESTIMATE, retrieved_at=retrieved_at,
                    document_sha256=page_sha, extraction_method=f"Google Store estimator data endpoint (rpc {RPC_ID})",
                    raw_label=label, raw_value=f"{cur} {amount}",
                    raw_context={"sku": sku, "model": name, "group": GROUP_LABEL.get(group, str(group))}),
                first_seen_at=retrieved_at, last_verified_at=retrieved_at, last_changed_at=retrieved_at,
            ))
    if not out.records:
        raise CollectorError("no estimates extracted")
    return out
