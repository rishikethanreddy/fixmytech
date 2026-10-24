"""Samsung United States collector.

The cracked-screen-repair page is server-rendered HTML with tables titled "Screen Repair Pricing" (front glass /
display only) and "Screen Module Replacement Pricing" (the whole screen module). Each row is a model name and
a price such as "$289"; "-" means the repair is not offered for that model. The first row of each table has an
extra leading cell (the model family) which is ignored.

Samsung states the prices are "suggested repair prices ... that may vary by region or authorized service store
location and exclude applicable taxes and fees". Rows have no model number, only a marketing name, so they are
matched by name.

The parser fails loudly (``CollectorError``) when the expected tables are missing, so a site redesign is reported
as an ingestion failure instead of silently producing no or wrong prices.
"""

from __future__ import annotations

import html as html_lib
import re
from datetime import datetime
from decimal import Decimal

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

SOURCE_ID = "samsung_us_screen"
URL = "https://www.samsung.com/us/support/cracked-screen-repair/"

# Table title -> (component id, label)
TABLE_COMPONENTS = {
    "screen repair pricing": ("service_screen", "Screen repair (display)"),
    "screen module replacement pricing": ("service_screen_module", "Screen module replacement"),
}
LIMITATIONS = [
    "Samsung's suggested repair price; it may vary by region or authorised service location and excludes "
    "applicable taxes and fees (stated by Samsung).",
    "Screen repairs only: the device must not have a bent or broken frame, a swollen battery or other hardware "
    "failures found at inspection.",
]
MIN_USD, MAX_USD = Decimal(20), Decimal(2000)

_TABLE_RE = re.compile(r"<table.*?</table>", re.S)
_ROW_RE = re.compile(r"<tr.*?</tr>", re.S)
_CELL_RE = re.compile(r"<t[dh].*?</t[dh]>", re.S)
_PRICE_RE = re.compile(r"\$\s?(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_page(page_html: str, retrieved_at: datetime, sha256: str | None, url: str = URL) -> ParseResult:
    cleaned = re.sub(r"<(script|style).*?</\1>", "", page_html, flags=re.S)
    out = ParseResult()
    seen_tables = 0
    for table in _TABLE_RE.findall(cleaned):
        rows = [[_text(c) for c in _CELL_RE.findall(r)] for r in _ROW_RE.findall(table)]
        title = rows[0][0].lower() if rows and rows[0] else ""
        if title not in TABLE_COMPONENTS:
            continue
        seen_tables += 1
        component, label = TABLE_COMPONENTS[title]
        family = ""
        for cells in rows[1:]:
            if len(cells) == 3:  # first row of a table carries the family name in front
                family, cells = cells[0], cells[1:]
            if len(cells) != 2:
                out.rejected.append({"item": " | ".join(cells)[:80], "reason": "unexpected row shape"})
                continue
            name, raw = cells
            out.items_seen += 1
            if raw in ("-", "", "--"):
                continue  # not offered for this model
            m = _PRICE_RE.fullmatch(raw)
            if not name or not m:
                out.rejected.append({"item": f"{name} = {raw}"[:80], "reason": "not a model name and a USD price"})
                continue
            amount = Decimal(m.group(1).replace(",", ""))
            if not MIN_USD <= amount <= MAX_USD:
                out.rejected.append({"item": f"{name} = {raw}", "reason": "price outside plausible range"})
                continue
            out.records.append(PriceRecord(
                record_id=record_id(SOURCE_ID, name, component),
                manufacturer="Samsung", model_name=name, model_code=None, region="US", currency="USD",
                component=component, component_label=label, amount=amount, price_point=PricePoint.EXACT,
                scope=PriceScope.PARTS_AND_LABOUR, tax=TaxTreatment.EXCLUDED,
                includes=["screen repair"], excludes=["applicable taxes and fees"], limitations=LIMITATIONS,
                evidence=SourceEvidence(
                    source_id=SOURCE_ID, source_url=url, organization="Samsung United States",
                    source_type=SourceType.OFFICIAL_SERVICE_ESTIMATE, retrieved_at=retrieved_at,
                    document_sha256=sha256, extraction_method="html: pricing tables parsed",
                    raw_label=title, raw_value=raw, raw_context={"family": family} if family else {}),
                first_seen_at=retrieved_at, last_verified_at=retrieved_at, last_changed_at=retrieved_at,
            ))
    if not seen_tables:
        raise CollectorError("no 'Screen Repair Pricing' / 'Screen Module Replacement Pricing' tables found")
    if not out.records:
        raise CollectorError("tables found but no prices extracted")
    return out
