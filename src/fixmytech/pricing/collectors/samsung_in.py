"""Samsung India collectors.

Parts page: the server-rendered HTML embeds ``var myProducts=[...]`` (a JavaScript array literal) with one
object per model: Series, Product, ModelNumber and part prices as Indian-formatted strings ("1,11,960").
The page states: prices exclude labour, consumables and GST; prices are for the premium colour and maximum
storage; the service centre confirms the final price.

Labour page: embeds ``const data = {"Mobile Devices": [{name, charges: [{s, p}]}]}`` with three tiers.
The page states taxes are charged extra.

Both parsers fail loudly (``CollectorError``) when the expected structure is missing, so a site redesign is
reported as an ingestion failure instead of silently producing no or wrong prices.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
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

PARTS_URL = "https://www.samsung.com/in/support/spare-part-pricing-list-for-repair/"
LABOUR_URL = "https://www.samsung.com/in/support/out-of-warranty-repair-charges/"

PARTS_LIMITATIONS = [
    "Official spare-part price only: excludes labour, consumables and GST (stated by Samsung India).",
    "Listed for the premium colour and maximum storage variant; the service centre confirms the price for "
    "your exact unit.",
    "Samsung states prices are subject to change; final cost is confirmed after diagnosis at the service centre.",
]
LABOUR_LIMITATIONS = [
    "Official labour charge tier only; taxes are charged extra (stated by Samsung India).",
    "Applies to products sold by Samsung authorised dealers in India; pickup/drop is charged separately.",
]

MIN_INR, MAX_INR = Decimal(10), Decimal(500000)

# Source field -> (normalized component id, human label)
TOP_LEVEL_FIELDS = {
    "Display": ("display_assembly", "Display (service assembly)"),
    "LCD": ("display_assembly", "Display (LCD service assembly)"),
    "SubDisplay": ("cover_display", "Cover / sub display"),
    "DisplayRepair": ("display_repair", "Display repair"),
    "BackGlass": ("back_glass", "Back glass"),
    "BackGlassCeramic": ("back_glass_ceramic", "Back glass (ceramic)"),
    "MainBattery": ("main_battery", "Main battery"),
    "SubBattery": ("sub_battery", "Sub battery"),
    "SPenKit": ("s_pen_kit", "S Pen kit"),
}
NESTED_FIELDS = {
    "screencomponent": {
        "componentscreenrepair": ("screen_component_repair", "Screen component repair"),
        "oled": ("oled_panel", "OLED panel (component)"),
        "frame": ("frame", "Frame"),
    },
    "MotherBoard": {
        "onesixGB": ("mainboard_16gb", "Main board (16 GB)"),
        "threetwoGB": ("mainboard_32gb", "Main board (32 GB)"),
        "sixfourGB": ("mainboard_64gb", "Main board (64 GB)"),
        "onetwoeightGB": ("mainboard_128gb", "Main board (128 GB)"),
        "twofivesixGB": ("mainboard_256gb", "Main board (256 GB)"),
        "fiveonetwoGB": ("mainboard_512gb", "Main board (512 GB)"),
        "oneTB": ("mainboard_1tb", "Main board (1 TB)"),
        "Subboard": ("sub_board", "Sub board"),
    },
    "Camera": {
        "Front": ("camera_front", "Front camera"),
        "FrontSub": ("camera_front_sub", "Front sub camera"),
        "Depth": ("camera_depth", "Depth camera"),
        "Back": ("camera_rear", "Rear camera"),
        "Iris": ("camera_iris", "Iris camera"),
        "TOF": ("camera_tof", "ToF camera"),
        "Macro": ("camera_macro", "Macro camera"),
        "UltraWide": ("camera_ultrawide", "Ultra-wide camera"),
        "AutoFocus": ("camera_autofocus", "Autofocus camera"),
        "Wide": ("camera_wide", "Wide (main) camera"),
        "Tele": ("camera_tele", "Telephoto camera"),
    },
}
LABOUR_COMPONENTS = {
    "AE (Accessory Exchange)": ("labour_accessory_exchange", PriceScope.LABOUR_ONLY),
    "Inspection Charges": ("inspection_fee", PriceScope.INSPECTION_FEE),
    "L1 No Parts used": ("labour_l1_no_parts", PriceScope.LABOUR_ONLY),
    "L2 Parts used": ("labour_l2_parts_used", PriceScope.LABOUR_ONLY),
    "L3 Component Level Repair": ("labour_l3_component", PriceScope.LABOUR_ONLY),
}
# Labour tier names on the labour page.
TIER_FOLDABLE = "Galaxy Z series (Fold & Flip)"
TIER_S = "Galaxy S series / Note series / Tab S series"
TIER_OTHER = "Other Galaxy series"


def _extract_js_literal(text: str, start_pattern: str, opener: str, closer: str) -> str:
    m = re.search(start_pattern, text)
    if not m:
        raise CollectorError(f"marker not found: {start_pattern}")
    i = text.index(opener, m.end() - 1)
    depth, in_str, quote, esc = 0, False, "", False
    for j in range(i, len(text)):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in "\"'":
            in_str, quote = True, ch
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    raise CollectorError("unterminated literal")


def _js_to_json(literal: str) -> Any:
    cleaned = re.sub(r",(\s*[}\]])", r"\1", literal)  # trailing commas
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise CollectorError(f"embedded data is not parseable: {exc.msg}") from exc


def parse_inr(raw: str) -> Decimal | None:
    """'1,11,960' -> 111960. Returns None for empty; raises ValueError for malformed values."""
    s = raw.strip()
    if not s or s.upper() in ("N/A", "NA", "-", "--"):
        return None  # the source marks the part as not offered
    if not re.fullmatch(r"\d{1,3}(,\d{2,3})*(\.\d{1,2})?|\d+(\.\d{1,2})?", s):
        raise ValueError(f"not a price: {raw!r}")
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"not a price: {raw!r}") from exc


def labour_tier_for(series: str | None, product: str | None) -> str:
    s, p = (series or "").lower(), (product or "").lower()
    if "galaxy z" in s or "fold" in p or "flip" in p:
        return TIER_FOLDABLE
    if s.strip() in ("galaxy s", "galaxy note") or p.startswith("galaxy s") or "note" in p or "tab s" in p:
        return TIER_S
    return TIER_OTHER


def parse_parts_page(html: str, retrieved_at: datetime, sha256: str | None, url: str = PARTS_URL) -> ParseResult:
    products = _js_to_json(_extract_js_literal(html, r"var\s+myProducts\s*=\s*\[", "[", "]"))
    if not isinstance(products, list) or not products:
        raise CollectorError("myProducts is empty or not a list")
    out = ParseResult()
    for prod in products:
        if not isinstance(prod, dict) or not prod.get("ModelNumber") or not prod.get("Product"):
            out.rejected.append({"item": str(prod)[:80], "reason": "missing model number or product name"})
            continue
        out.items_seen += 1
        model_code = str(prod["ModelNumber"]).strip().upper()
        if not re.fullmatch(r"SM-[A-Z0-9]{3,8}(/[A-Z0-9]+)?", model_code):
            out.rejected.append({"item": model_code, "reason": "unexpected model number format"})
            continue
        fields: list[tuple[str, str, str, str]] = []  # (source_field, component, label, raw)
        for f, (comp, label) in TOP_LEVEL_FIELDS.items():
            if isinstance(prod.get(f), str):
                fields.append((f, comp, label, prod[f]))
        for group, mapping in NESTED_FIELDS.items():
            blocks = prod.get(group) or []
            if isinstance(blocks, list) and blocks and isinstance(blocks[0], dict):
                for f, (comp, label) in mapping.items():
                    if isinstance(blocks[0].get(f), str):
                        fields.append((f"{group}.{f}", comp, label, blocks[0][f]))
        context = {"Series": str(prod.get("Series", "")), "Product": str(prod["Product"]),
                   "ModelNumber": model_code, "labour_tier": labour_tier_for(prod.get("Series"), prod["Product"])}
        for src_field, comp, label, raw in fields:
            try:
                amount = parse_inr(raw)
            except ValueError as exc:
                out.rejected.append({"item": f"{model_code} {src_field}", "reason": str(exc)})
                continue
            if amount is None:
                continue
            if not MIN_INR <= amount <= MAX_INR:
                out.rejected.append({"item": f"{model_code} {src_field}", "reason": f"amount out of range: {amount}"})
                continue
            out.records.append(PriceRecord(
                record_id=record_id("samsung_in_parts", model_code, comp),
                manufacturer="Samsung", model_name=str(prod["Product"]).strip(), model_code=model_code,
                region="IN", currency="INR", component=comp, component_label=label,
                amount=amount, price_point=PricePoint.EXACT, scope=PriceScope.PARTS_ONLY, tax=TaxTreatment.EXCLUDED,
                includes=["part"], excludes=["labour", "consumables", "GST"], limitations=PARTS_LIMITATIONS,
                evidence=SourceEvidence(
                    source_id="samsung_in_parts", source_url=url, organization="Samsung India",
                    source_type=SourceType.OFFICIAL_PARTS_LIST, retrieved_at=retrieved_at, document_sha256=sha256,
                    extraction_method="html: embedded JS array 'myProducts' parsed as JSON",
                    raw_label=src_field, raw_value=raw, raw_context=context),
                first_seen_at=retrieved_at, last_verified_at=retrieved_at, last_changed_at=retrieved_at,
            ))
    if not out.records:
        raise CollectorError("no prices extracted from myProducts")
    return out


_LABOUR_TIER_RE = re.compile(r"name:\s*\"(?P<name>[^\"]+)\"[^}]*?charges:\s*\[(?P<charges>[^\]]*)\]", re.S)
_CHARGE_RE = re.compile(r"\{\s*s:\s*\"(?P<s>[^\"]+)\"\s*,\s*p:\s*\"(?P<p>[^\"]*)\"\s*\}")


def parse_labour_page(html: str, retrieved_at: datetime, sha256: str | None, url: str = LABOUR_URL) -> ParseResult:
    block = _extract_js_literal(html, r"\"Mobile Devices\"\s*:\s*\[", "[", "]")
    published = None
    m = re.search(r'<meta name="date" content="([0-9-]{8,10})"', html)
    if m:
        published = m.group(1)
    out = ParseResult()
    for tier in _LABOUR_TIER_RE.finditer(block):
        name = tier.group("name").strip()
        out.items_seen += 1
        for charge in _CHARGE_RE.finditer(tier.group("charges")):
            label, raw = charge.group("s").strip(), charge.group("p")
            mapped = LABOUR_COMPONENTS.get(label)
            if mapped is None:
                out.rejected.append({"item": f"{name} / {label}", "reason": "unknown charge type"})
                continue
            try:
                amount = parse_inr(raw)
            except ValueError as exc:
                out.rejected.append({"item": f"{name} / {label}", "reason": str(exc)})
                continue
            if amount is None or not Decimal(10) <= amount <= Decimal(20000):
                out.rejected.append({"item": f"{name} / {label}", "reason": f"amount missing or out of range: {raw!r}"})
                continue
            comp, scope = mapped
            out.records.append(PriceRecord(
                record_id=record_id("samsung_in_labour", name, comp),
                manufacturer="Samsung", applies_to_group=name, region="IN", currency="INR",
                component=comp, component_label=label, amount=amount, price_point=PricePoint.EXACT,
                scope=scope, tax=TaxTreatment.EXCLUDED, includes=["labour"] if scope == PriceScope.LABOUR_ONLY
                else ["inspection"], excludes=["parts", "GST", "pickup/drop"], limitations=LABOUR_LIMITATIONS,
                evidence=SourceEvidence(
                    source_id="samsung_in_labour", source_url=url, organization="Samsung India",
                    source_type=SourceType.OFFICIAL_LABOUR_SCHEDULE, retrieved_at=retrieved_at, document_sha256=sha256,
                    published_at=published, extraction_method="html: embedded JS object 'data[\"Mobile Devices\"]'",
                    raw_label=label, raw_value=raw, raw_context={"tier": name}),
                first_seen_at=retrieved_at, last_verified_at=retrieved_at, last_changed_at=retrieved_at,
            ))
    if out.items_seen < 3 or not out.records:
        raise CollectorError(f"expected 3 mobile labour tiers, found {out.items_seen}")
    return out
