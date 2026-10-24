"""Repair-cost estimation for a diagnosis.

Kept separate from diagnosis: this module prices *repair types*; the caller supplies the plausible faults and
their support levels, which are carried through to every estimate.

Prices come from ``fixmytech.pricing`` (source-attributed records). Development demo fixtures are used only
when explicitly enabled and are labelled ``demo_fixture`` everywhere. There is no generic fallback: without
evidence the result is ``insufficient_pricing_data``.
"""

from __future__ import annotations

import csv
import threading
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from fixmytech.domain.diagnosis import DiagnosisResult, SupportLevel
from fixmytech.domain.facts import DeviceProfile
from fixmytech.domain.knowledge import KnowledgeBase
from fixmytech.pricing.estimate import PricingEstimate, PricingPolicy, estimate
from fixmytech.pricing.models import PriceScope, PricingDataset
from fixmytech.pricing.store import PricingStore

DEMO_PRICE_FILE = Path(__file__).resolve().parent.parent / "data" / "demo_repair_prices.csv"


class DemoPriceRow(BaseModel):
    """Development-only generic ranges. Never market data."""

    repair_type: str
    region: str
    currency: str
    low: Decimal
    high: Decimal
    notes: str = ""


def load_demo_rows(path: Path = DEMO_PRICE_FILE) -> list[DemoPriceRow]:
    with path.open(encoding="utf-8", newline="") as fh:
        return [DemoPriceRow(repair_type=r["repair_type"], region=r["region"], currency=r["currency"],
                             low=Decimal(r["parts_low"]) + Decimal(r["labor_low"]),
                             high=Decimal(r["parts_high"]) + Decimal(r["labor_high"]), notes=r.get("notes", ""))
                for r in csv.DictReader(fh)]


class RepairPricingService:
    """Loads the pricing dataset (reloading when the ingestion job replaces the file) and produces estimates."""

    def __init__(self, store: PricingStore | None, policy: PricingPolicy, demo_rows: list[DemoPriceRow] | None = None,
                 dataset: PricingDataset | None = None, benchmark_rows: list[DemoPriceRow] | None = None) -> None:
        self.store = store
        self.policy = policy
        self.demo_rows = demo_rows or []
        self.benchmark_rows = benchmark_rows
        self._dataset = dataset
        self._mtime: float | None = None
        self._lock = threading.Lock()

    def dataset(self) -> PricingDataset:
        if self.store is None:
            return self._dataset or PricingDataset()
        with self._lock:
            try:
                mtime = self.store.path.stat().st_mtime
            except FileNotFoundError:
                self._dataset, self._mtime = PricingDataset(), None
                return self._dataset
            if self._dataset is None or mtime != self._mtime:
                self._dataset, self._mtime = self.store.load(), mtime
            return self._dataset

    def estimate(self, kb: KnowledgeBase, repair_type: str, device: DeviceProfile) -> PricingEstimate:
        name = kb.repair_types[repair_type].name if repair_type in kb.repair_types else repair_type
        result = estimate(self.dataset(), repair_type, name, device.manufacturer, device.model, device.region,
                          self.policy)
        if result.status == "insufficient_pricing_data":
            target_region = device.region or "IN"
            if self.policy.demo_enabled:
                demo = next(
                    (r for r in self.demo_rows if r.repair_type == repair_type and r.region == target_region),
                    None,
                )
                if demo:
                    return result.model_copy(update={
                        "status": "estimated", "data_kind": "demo_fixture", "currency": demo.currency,
                        "low": demo.low, "high": demo.high, "scope": PriceScope.PARTS_AND_LABOUR, "tax": "unknown",
                        "usable_for_decision": True,
                        "notes": ["DEMO DATA: a made-up generic range for development. Not a market price."],
                        "limitations": [demo.notes] if demo.notes else []})
            elif self.benchmark_rows:
                bench = next(
                    (r for r in self.benchmark_rows if r.repair_type == repair_type and r.region == target_region),
                    None,
                )
                if bench:
                    man_label = (device.manufacturer or "this brand").title()
                    return result.model_copy(update={
                        "status": "estimated", "data_kind": "benchmark", "currency": bench.currency,
                        "low": bench.low, "high": bench.high, "scope": PriceScope.PARTS_AND_LABOUR, "tax": "unknown",
                        "usable_for_decision": True,
                        "notes": [
                            f"Market benchmark reference: typical {name.lower()} cost range across repair services.",
                            f"Official published parts pricing was not available for {man_label}.",
                        ],
                        "limitations": [bench.notes] if bench.notes else []})
        return result

    def status(self) -> dict[str, Any]:
        ds = self.dataset()
        return {
            "records": len(ds.records),
            "generated_at": ds.generated_at,
            "regions": sorted({r.region for r in ds.records if self.policy.allows(r)}),
            "sources": {sid: s.model_dump(mode="json") for sid, s in ds.sources.items()},
            "allowed_sources": (sorted(self.policy.allowed_sources) if self.policy.allowed_sources is not None
                                else "all"),
            "demo_enabled": self.policy.demo_enabled,
        }


class QuoteInput(BaseModel):
    repair_type: str = Field(pattern=r"^[a-z_]{3,50}$")
    amount: Decimal = Field(ge=0, le=Decimal("1000000"))
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    includes_tax: bool | None = None
    note: str | None = Field(default=None, max_length=200)
    received_on: date | None = None


QuotePosition = Literal["below_range", "within_range", "above_range", "below_part_price", "not_comparable"]


class QuoteComparison(BaseModel):
    repair_type: str
    amount: Decimal
    currency: str
    kind: Literal["customer_quote"] = "customer_quote"
    position: QuotePosition
    explanation: str


class RepairEstimateReport(BaseModel):
    region: str
    estimates: list[PricingEstimate]
    overall_low: Decimal | None
    overall_high: Decimal | None
    overall_currency: str | None
    diagnostic_uncertainty: str
    quotes: list[QuoteComparison]
    no_paid_repair_suggested: list[str]
    pricing_generated_at: datetime | None
    demo_data_in_use: bool


def compare_quote(quote: QuoteInput, est: PricingEstimate | None) -> QuoteComparison:
    base: dict[str, Any] = {"repair_type": quote.repair_type, "amount": quote.amount, "currency": quote.currency}

    def out(pos: QuotePosition, text: str) -> QuoteComparison:
        return QuoteComparison.model_validate({**base, "position": pos, "explanation": text})

    if est is None or est.low is None or est.high is None or est.status == "insufficient_pricing_data":
        return out("not_comparable", "There is no reference price for this repair to compare against.")
    if est.currency != quote.currency:
        return out("not_comparable", f"Your quote is in {quote.currency} but the reference is in {est.currency}; "
                                     "FixMyTech does not convert currencies.")
    demo = " (the reference is DEMO data, not a market price)" if est.data_kind == "demo_fixture" else ""
    bench = " (compared against market benchmark reference)" if est.data_kind == "benchmark" else ""
    ref_label = "the market benchmark reference" if est.data_kind == "benchmark" else "the official reference"
    if est.status == "parts_only":
        if quote.amount < est.low:
            return out("below_part_price", "Your quote is lower than the official price of the part alone. Ask whether "
                                           f"it uses a genuine part{demo}.")
        return out("not_comparable", "The reference is the official part price only (no labour or tax), so it cannot "
                                     f"be compared with a full repair quote{demo}.")
    tax_note = (" The reference excludes tax; a quote that includes GST/VAT will be higher."
                if est.tax == "excluded" else "")
    if quote.amount < est.low:
        return out("below_range", f"Your quote is below {ref_label}. Check which part quality it uses."
                   + tax_note + demo + bench)
    if quote.amount > est.high:
        return out("above_range", f"Your quote is above {ref_label}. Ask for an itemised quote or a "
                                  f"second opinion.{tax_note}{demo}{bench}")
    return out("within_range", f"Your quote is within {ref_label} range.{tax_note}{demo}{bench}")


PRICED_LEVELS = (SupportLevel.STRONG, SupportLevel.MODERATE, SupportLevel.WEAK)


def estimate_for_diagnosis(service: RepairPricingService, kb: KnowledgeBase, diagnosis: DiagnosisResult,
                           device: DeviceProfile, quotes: list[QuoteInput]) -> RepairEstimateReport:
    estimates: dict[str, PricingEstimate] = {}
    self_help_only: list[str] = []
    for cand in diagnosis.candidates:
        if cand.support_level not in PRICED_LEVELS:
            continue
        if not cand.repair_types or cand.fault_id == "software_instability":
            self_help_only.append(cand.name)
            if not cand.repair_types:
                continue
        for rt in cand.repair_types:
            if rt in estimates:
                estimates[rt].for_faults.append(cand.fault_id)
                continue
            est = service.estimate(kb, rt, device)
            estimates[rt] = est.model_copy(update={"for_faults": [cand.fault_id],
                                                   "fault_support": cand.support_level.value})
    if diagnosis.outcome == "insufficient_evidence" and "professional_diagnosis" not in estimates:
        estimates["professional_diagnosis"] = service.estimate(kb, "professional_diagnosis", device)

    usable = [e for e in estimates.values() if e.usable_for_decision and e.repair_type != "professional_diagnosis"]
    currencies = {e.currency for e in usable}
    overall_low = overall_high = None
    overall_currency = None
    if usable and len(currencies) == 1:
        overall_currency = currencies.pop()
        overall_low = min(e.low for e in usable if e.low is not None)
        overall_high = max(e.high for e in usable if e.high is not None)

    strong = [c for c in diagnosis.candidates if c.support_level in (SupportLevel.STRONG, SupportLevel.MODERATE)]
    if diagnosis.close_call:
        uncertainty = ("Two faults are ranked closely, so the repair actually needed is uncertain; the overall range "
                       "spans both.")
    elif len(strong) == 1 and strong[0].support_level == SupportLevel.STRONG:
        uncertainty = "One fault is strongly supported; its repair estimate is the most relevant."
    elif strong:
        uncertainty = "The leading fault is only moderately supported; a technician should confirm it before you pay."
    else:
        uncertainty = "No fault is well supported yet, so repair prices are shown for reference only."

    comparisons = [compare_quote(q, estimates.get(q.repair_type) or service.estimate(kb, q.repair_type, device))
                   for q in quotes]
    ds = service.dataset()
    return RepairEstimateReport(
        region=device.region, estimates=list(estimates.values()), overall_low=overall_low, overall_high=overall_high,
        overall_currency=overall_currency, diagnostic_uncertainty=uncertainty, quotes=comparisons,
        no_paid_repair_suggested=self_help_only, pricing_generated_at=ds.generated_at,
        demo_data_in_use=any(e.data_kind == "demo_fixture" for e in estimates.values()),
    )
