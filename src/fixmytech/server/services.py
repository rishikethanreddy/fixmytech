"""Application services: glue between HTTP, persistence and the framework-independent domain."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from fixmytech.core.observations import ScanResult
from fixmytech.domain.decision import DecisionInputs, DecisionResult, Objective, Range, ValueSource, decide
from fixmytech.domain.diagnosis import DiagnosisResult, SupportLevel, diagnose
from fixmytech.domain.facts import DeviceProfile, build_facts, fact_values
from fixmytech.domain.knowledge import KnowledgeBase
from fixmytech.domain.repair import QuoteInput, RepairEstimateReport, RepairPricingService, estimate_for_diagnosis
from fixmytech.integrations.flipvalue.client import FlipValueClient
from fixmytech.integrations.flipvalue.schemas import (
    REPAIR_TYPES,
    ConditionGrade,
    ConditionSpec,
    DeviceSpec,
    RepairScenario,
    ValuationRequest,
)
from fixmytech.ml.inference import ml_opinion
from fixmytech.server.config import Settings
from fixmytech.server.db import Database, DiagnosticSession
from fixmytech.server.errors import ApiError
from fixmytech.server.partner import PartnerStore
from fixmytech.server.security import SlidingWindowLimiter


@dataclass
class AppState:
    settings: Settings
    db: Database
    kb: KnowledgeBase
    pricing: RepairPricingService
    flipvalue: FlipValueClient
    valuation_limiter: SlidingWindowLimiter
    partner_store: PartnerStore | None = None
    partner_limiter: SlidingWindowLimiter | None = None
    market_limiter: SlidingWindowLimiter | None = None
    partner_clients: dict[tuple[str, str], FlipValueClient] = field(default_factory=dict)


# ---------------------------------------------------------------------------- request models


class Consent(BaseModel):
    diagnostic_processing: bool = Field(description="Agree to processing of diagnostic data for this report")
    store_report: bool = Field(default=False, description="Keep the report for the retention period")


class CreateSessionRequest(BaseModel):
    mode: Literal["usb", "manual"]
    device: DeviceProfile
    consent: Consent


class AnswerRequest(BaseModel):
    value: str | list[str]


class ValuationInput(BaseModel):
    condition_grade: ConditionGrade
    repair_type: str | None = Field(default=None, description="Repair to value; defaults to the top candidate's")
    storage_gb: int | None = Field(default=None, ge=1, le=4096)
    age_months: int | None = Field(default=None, ge=0, le=240)


class MarketCompareInput(BaseModel):
    """FlipValue's repair-or-sell comparison. Values in major units of ``currency``; unknown values are omitted."""

    currency: str = Field(pattern=r"^[A-Z]{3}$")
    as_is_value: float | None = Field(default=None, ge=0, le=10_000_000)
    after_repair_value: float | None = Field(default=None, ge=0, le=10_000_000)
    repair_cost: float | None = Field(default=None, ge=0, le=10_000_000)
    replacement_price: float | None = Field(default=None, ge=0, le=10_000_000)
    selling_fee_pct: float = Field(default=0.0, ge=0, le=50)
    selling_fixed_cost: float = Field(default=0.0, ge=0, le=1_000_000)


class ManualValues(BaseModel):
    as_is: Range | None = None
    repaired: Range | None = None


class DecisionRequest(BaseModel):
    objective: Objective = "lowest_cost"
    replacement_price: float | None = Field(default=None, ge=0, le=1_000_000)
    horizon_months: int = Field(default=24, ge=1, le=72)
    transaction_fee_pct: float = Field(default=0.0, ge=0, le=50)
    old_device_annual_depreciation_pct: float = Field(default=20.0, ge=0, le=90)
    new_device_annual_depreciation_pct: float = Field(default=30.0, ge=0, le=90)
    expected_extra_repairs: float = Field(default=0.0, ge=0, le=1_000_000)
    repair_source: str | None = Field(default=None, pattern=r"^(estimate|quote):[a-z_0-9]+$",
                                      description="'estimate:<repair_type>' or 'quote:<index>'")
    value_source: Literal["flipvalue", "manual"] = "flipvalue"
    manual_values: ManualValues | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


# ---------------------------------------------------------------------------- helpers


def device_of(rec: DiagnosticSession) -> DeviceProfile:
    return DeviceProfile(**rec.device)


def scan_of(rec: DiagnosticSession) -> ScanResult | None:
    return ScanResult.model_validate(rec.scan) if rec.scan else None


def new_expiry(consent: Consent, settings: Settings, now: datetime) -> datetime:
    if consent.store_report:
        return now + timedelta(days=settings.retention_days)
    return now + timedelta(hours=settings.unsaved_retention_hours)


def run_diagnosis(state: AppState, rec: DiagnosticSession) -> DiagnosisResult:
    device, scan = device_of(rec), scan_of(rec)
    ml = None
    if state.settings.ml_enabled:
        facts = fact_values(build_facts(state.kb, device, scan, rec.answers, tests_of(rec)))
        ml = ml_opinion(state.settings.ml_model_dir, True, facts)
    return diagnose(state.kb, device, scan, rec.answers, ml, tests_of(rec))


def tests_of(rec: DiagnosticSession) -> dict[str, Any]:
    return dict(rec.guided_tests or {})


def quotes_of(rec: DiagnosticSession) -> list[QuoteInput]:
    return [QuoteInput.model_validate(q) for q in rec.quotes or []]


def repair_report(state: AppState, rec: DiagnosticSession, diagnosis: DiagnosisResult | None = None
                  ) -> RepairEstimateReport:
    diagnosis = diagnosis or run_diagnosis(state, rec)
    return estimate_for_diagnosis(state.pricing, state.kb, diagnosis, device_of(rec), quotes_of(rec))


def candidate_repair_costs(state: AppState, rec: DiagnosticSession,
                           diagnosis: DiagnosisResult) -> dict[str, Any]:
    """Per-candidate repair cost blocks for the diagnosis step.

    Returns a dict keyed by fault_id, each value is a serializable repair_cost block.
    Only candidates with weak-or-better support and at least one repair type get a block.
    """
    device = device_of(rec)
    costs: dict[str, Any] = {}
    for cand in diagnosis.candidates:
        if cand.support_level not in (SupportLevel.STRONG, SupportLevel.MODERATE, SupportLevel.WEAK):
            continue
        if not cand.repair_types:
            continue
        # Use the first repair type (the primary one for this fault)
        rt = cand.repair_types[0]
        est = state.pricing.estimate(state.kb, rt, device)
        block: dict[str, Any] = {
            "repair_type": rt,
            "repair_name": state.kb.repair_types[rt].name if rt in state.kb.repair_types else rt,
            "status": est.status,
            "conditional_label": "if this component is the cause",
        }
        if cand.requires_confirmation or cand.support_level != SupportLevel.STRONG:
            block["confirmation_note"] = "Needs technician confirmation before replacing parts."
        if est.status in ("estimated", "parts_only"):
            block["currency"] = est.currency
            block["low"] = float(est.low) if est.low is not None else None
            block["high"] = float(est.high) if est.high is not None else None
            block["scope"] = est.scope.value if est.scope else None
            block["tax"] = est.tax
            block["model_match"] = est.model_match.value
            block["freshness"] = est.freshness.value if est.freshness else None
            block["last_verified_at"] = est.last_verified_at.isoformat() if est.last_verified_at else None
            block["evidence"] = [e.model_dump(mode="json") for e in est.evidence]
            block["notes"] = est.notes
            block["official_links"] = est.official_links
            if est.status == "parts_only":
                block["scope_warning"] = ("This is the part price only, not a full repair cost. "
                                          "Labour and tax are extra.")
        else:
            # insufficient_pricing_data
            block["official_links"] = est.official_links
            block["notes"] = est.notes
        costs[cand.fault_id] = block
    return costs


def suggested_condition(diagnosis: DiagnosisResult) -> dict[str, Any]:
    hardware = [c for c in diagnosis.candidates
                if c.support_level in (SupportLevel.STRONG, SupportLevel.MODERATE)
                and c.fault_id not in ("software_instability", "storage_pressure")]
    top_repair = next((rt for c in hardware for rt in c.repair_types if rt in REPAIR_TYPES), None)
    return {
        "grade": ConditionGrade.FAULTY.value if hardware else None,
        "faults": [c.fault_id for c in diagnosis.candidates
                   if c.support_level in (SupportLevel.STRONG, SupportLevel.MODERATE)],
        "repair_type": top_repair,
        "explanation": ("A hardware fault is moderately or strongly supported, so 'faulty' is suggested."
                        if hardware else "No hardware fault is well supported; choose the grade that matches the "
                                         "phone's cosmetic condition."),
    }


def build_valuation_request(state: AppState, rec: DiagnosticSession, inp: ValuationInput,
                            diagnosis: DiagnosisResult, currency: str) -> ValuationRequest:
    device = device_of(rec)
    if not device.manufacturer or not device.model:
        raise ApiError(422, "device_details_required", "Manufacturer and model are needed for a resale valuation.")
    suggestion = suggested_condition(diagnosis)
    repair_type = inp.repair_type or suggestion["repair_type"]
    if repair_type is not None and repair_type not in REPAIR_TYPES:
        raise ApiError(422, "unknown_repair_type", f"Unknown repair type '{repair_type}'.")
    return ValuationRequest(
        device=DeviceSpec(manufacturer=device.manufacturer, model=device.model,
                          storage_gb=inp.storage_gb or device.storage_gb,
                          age_months=inp.age_months if inp.age_months is not None else device.age_months),
        region=device.region,
        currency=currency,
        condition=ConditionSpec(grade=inp.condition_grade, faults=suggestion["faults"]),
        repair_scenario=RepairScenario(repair_type=repair_type) if repair_type else None,
    )


def sample_valuation_request(state: AppState) -> ValuationRequest:
    """A fixed, non-personal request used only to check that a saved API key is accepted."""
    region = state.settings.default_region
    return ValuationRequest(
        device=DeviceSpec(manufacturer="Samsung", model="SM-S928B", storage_gb=256, age_months=24),
        region=region, currency=state.settings.market_currencies[region],
        condition=ConditionSpec(grade=ConditionGrade.GOOD, faults=[]))


def region_currency(state: AppState, region: str) -> str | None:
    return state.settings.market_currencies.get(region)


def _range_from_money(m: dict[str, Any] | None) -> Range | None:
    if not m:
        return None
    v = float(m["value"])
    return Range(low=float(m.get("low") or v), high=float(m.get("high") or v))


def run_decision(state: AppState, rec: DiagnosticSession, req: DecisionRequest) -> DecisionResult:
    diagnosis = run_diagnosis(state, rec)
    report = repair_report(state, rec, diagnosis)
    device = device_of(rec)
    currency = req.currency or region_currency(state, device.region)

    repair_cost: Range | None = None
    repair_cost_source: Literal["reference_estimate", "customer_quote"] | None = None
    extra_caveats: list[str] = []
    if req.repair_source:
        kind, ref = req.repair_source.split(":", 1)
        if kind == "estimate":
            est = next((e for e in report.estimates if e.repair_type == ref and e.usable_for_decision), None)
            if est is None or est.low is None or est.high is None:
                raise ApiError(422, "estimate_unavailable",
                               f"There is no complete repair-cost estimate for '{ref}'. Enter a repair quote instead.")
            if est.tax == "excluded":
                extra_caveats.append("The repair cost is an official reference before tax; the amount you pay will be "
                                     "higher by any applicable GST/VAT.")
            if est.data_kind == "demo_fixture":
                extra_caveats.append("The repair cost uses DEMO pricing data, not a market price.")
            elif est.data_kind == "benchmark":
                extra_caveats.append("The repair cost uses an industry market benchmark reference range.")
            repair_cost, repair_cost_source, currency = Range(low=float(est.low), high=float(est.high)), \
                "reference_estimate", currency or est.currency
            if est.currency != currency:
                raise ApiError(422, "currency_mismatch", "Repair estimate currency differs from the decision currency.")
        else:
            quotes = quotes_of(rec)
            idx = int(ref) if ref.isdigit() else -1
            if not 0 <= idx < len(quotes):
                raise ApiError(422, "quote_not_found", "That quote does not exist.")
            q = quotes[idx]
            currency = currency or q.currency
            if q.currency != currency:
                raise ApiError(422, "currency_mismatch", "Quote currency differs from the decision currency.")
            repair_cost, repair_cost_source = Range.point(float(q.amount)), "customer_quote"

    as_is = repaired = None
    value_source: ValueSource | None = None
    if req.value_source == "flipvalue":
        v = rec.valuation
        if v and v.get("ok"):
            resp = v["response"]
            if currency and resp["currency"] != currency:
                raise ApiError(422, "currency_mismatch",
                               f"Valuation is in {resp['currency']}, but costs are in {currency}.")
            currency = currency or resp["currency"]
            as_is, repaired = _range_from_money(resp["as_is"]), _range_from_money(resp.get("after_repair"))
            value_source = v["source"]
    elif req.manual_values:
        as_is, repaired = req.manual_values.as_is, req.manual_values.repaired
        value_source = "customer_provided"

    if currency is None:
        raise ApiError(422, "currency_unknown", "Specify a currency for the comparison.")
    inputs = DecisionInputs(
        currency=currency, objective=req.objective, repair_cost=repair_cost, repair_cost_source=repair_cost_source,
        as_is_value=as_is, repaired_value=repaired, value_source=value_source,
        replacement_price=req.replacement_price, horizon_months=req.horizon_months,
        transaction_fee_pct=req.transaction_fee_pct,
        old_device_annual_depreciation_pct=req.old_device_annual_depreciation_pct,
        new_device_annual_depreciation_pct=req.new_device_annual_depreciation_pct,
        expected_extra_repairs=req.expected_extra_repairs,
    )
    result = decide(inputs)
    result.caveats[:0] = extra_caveats
    if value_source is None:
        result.caveats.insert(0, "Resale value was not considered (that step was skipped or no value was entered), so "
                                 "selling cannot be compared. Only repair cost and replacement price are used.")
    if diagnosis.outcome != "candidates_found" and repair_cost is not None:
        result.caveats.insert(0, "The diagnosis is uncertain, so the repair being priced may not be the one needed.")
    return result


def money(v: Decimal | float | None) -> float | None:
    return float(v) if v is not None else None


def utc_now() -> datetime:
    return datetime.now(UTC)
