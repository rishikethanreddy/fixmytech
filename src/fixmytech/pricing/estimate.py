"""Evidence-backed repair estimates from source-attributed price records.

Rules:
* Model matching: exact model code > exact model name > same model, different regional variant (labelled as an
  approximation). Related-but-different models are never used.
* Scopes are never mixed: an official parts price is combined only with that manufacturer's official labour
  charge for the matching tier, and the result is labelled "parts + labour, before tax". A parts price with no
  labour data stays "parts only" and is not usable as a total repair cost.
* No evidence -> ``insufficient_pricing_data`` with links to official sources the customer can check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from fixmytech.pricing.models import PriceRecord, PriceScope, PricingDataset, SourceType
from fixmytech.pricing.sources import SOURCES_BY_ID, official_links


class ModelMatch(StrEnum):
    EXACT_CODE = "exact_model_code"
    EXACT_NAME = "exact_model_name"
    VARIANT = "regional_variant"  # same model, different regional suffix
    NONE = "none"


class Freshness(StrEnum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"


# repair type -> ordered alternatives; each alternative is (component ids, how to combine, note)
# "single": use the first available component in the list; "range": min..max across available components.
# Components named service_* are complete official repair estimates (e.g. Google Store); the others are parts.
COMPONENT_PLANS: dict[str, list[tuple[list[str], Literal["single", "range"], str | None]]] = {
    "screen_replacement": [
        (["display_assembly"], "single", None),
        (["service_screen"], "single", None),
        (["service_screen_module"], "single", "Samsung lists only a full screen module replacement for this model."),
        (["oled_panel"], "single", "Samsung lists only a screen component repair (OLED panel) for this model, "
                                   "not a full display assembly."),
    ],
    "battery_replacement": [(["main_battery"], "single", None), (["service_battery"], "single", None)],
    "camera_module_replacement": [
        (["camera_wide", "camera_rear", "camera_ultrawide", "camera_tele", "camera_macro", "camera_depth"], "range",
         "The range covers the rear cameras listed for this model; the price depends on which camera has failed."),
        (["service_camera_rear", "service_camera_front"], "range",
         "The range covers the rear and front camera repairs; the price depends on which camera has failed."),
    ],
    "charging_port_replacement": [(["service_charging_port"], "single", None)],
    "audio_component_replacement": [(["service_audio"], "single", None)],
    "liquid_damage_service": [(["service_liquid"], "single", None)],
}
# Brand names customers type instead of the manufacturer (e.g. "Pixel" for Google).
MANUFACTURER_ALIASES = {"pixel": "google", "galaxy": "samsung", "iphone": "apple", "redmi": "xiaomi", "poco": "xiaomi",
                        "mi": "xiaomi", "moto": "motorola", "nord": "oneplus"}
LABOUR_COMPONENT = "labour_l2_parts_used"


@dataclass
class PricingPolicy:
    allowed_sources: set[str] | None = None  # None: all sources except prohibited ones
    demo_enabled: bool = False
    fresh_days: int = 30
    stale_days: int = 90
    show_policy_notes: bool = False  # development: tell the operator a source needs legal review

    def allows(self, record: PriceRecord) -> bool:
        if record.evidence.source_type == SourceType.DEMO_FIXTURE:
            return self.demo_enabled
        if self.allowed_sources is not None:
            return record.evidence.source_id in self.allowed_sources
        return True


class EvidenceLine(BaseModel):
    label: str
    amount: Decimal
    amount_high: Decimal | None = None
    scope: PriceScope
    organization: str
    source_type: SourceType
    source_url: str
    retrieved_at: datetime
    last_verified_at: datetime
    published_at: str | None
    raw_label: str
    raw_value: str
    model_name: str | None
    model_code: str | None
    applies_to_group: str | None


class PricingEstimate(BaseModel):
    repair_type: str
    repair_name: str
    status: Literal["estimated", "parts_only", "insufficient_pricing_data"]
    data_kind: Literal["source_attributed", "demo_fixture", "benchmark", "none"]
    currency: str | None = None
    low: Decimal | None = None
    high: Decimal | None = None
    scope: PriceScope | None = None
    tax: Literal["excluded", "included", "unknown"] | None = None
    usable_for_decision: bool = False
    model_match: ModelMatch = ModelMatch.NONE
    matched_model: str | None = None
    freshness: Freshness | None = None
    last_verified_at: datetime | None = None
    evidence: list[EvidenceLine] = []
    exclusions: list[str] = []
    limitations: list[str] = []
    notes: list[str] = []
    why_quotes_differ: str | None = None
    official_links: list[dict[str, str]] = []
    policy_notes: list[str] = []
    for_faults: list[str] = []
    fault_support: str | None = None


WHY_DIFFERS = ("A repair shop's quote can differ because of taxes, the exact variant and colour, extra parts found "
               "during inspection, consumables, and whether genuine or third-party parts are used. Independent shops "
               "set their own prices.")


def _norm_code(code: str | None) -> str:
    return re.sub(r"[^A-Z0-9-]", "", (code or "").upper().split("/")[0])


def _code_family(code: str) -> str:
    """SM-S928B -> SM-S928 (strip the regional letter suffix)."""
    m = re.match(r"^(SM-[A-Z]\d{3})[A-Z0-9]*$", code)
    return m.group(1) if m else code


def _norm_name(name: str | None) -> str:
    s = (name or "").lower()
    s = re.sub(r"\b(samsung|galaxy|google|5g|4g)\b", " ", s)
    s = s.replace("plus", "+")
    return re.sub(r"[^a-z0-9+]", "", s)


@dataclass
class _Match:
    kind: ModelMatch
    records: list[PriceRecord] = field(default_factory=list)


def normalize_manufacturer(manufacturer: str | None) -> str:
    m = (manufacturer or "").strip().lower()
    return MANUFACTURER_ALIASES.get(m, m)


def match_model(records: list[PriceRecord], manufacturer: str | None, model: str | None) -> _Match:
    man = normalize_manufacturer(manufacturer)
    candidates = [r for r in records if (r.model_code or r.model_name)
                  and (not man or r.manufacturer.lower() == man)]
    names_only = False
    if not candidates and man:
        # The typed "manufacturer" may really be part of the model name ("Pixel" + "6A"); try a name match
        # without it. A model code is never matched across manufacturers.
        candidates = [r for r in records if r.model_code or r.model_name]
        names_only = True
    if not model or not candidates:
        return _Match(ModelMatch.NONE)
    code = _norm_code(model)
    exact = [r for r in candidates if r.model_code and _norm_code(r.model_code) == code]
    if exact and not names_only:
        return _Match(ModelMatch.EXACT_CODE, exact)
    names = {_norm_name(model), _norm_name(f"{manufacturer or ''} {model}")} - {""}
    if names:
        by_name = [r for r in candidates if _norm_name(r.model_name) in names]
        if by_name and len({r.manufacturer.lower() for r in by_name}) == 1:
            codes = {r.model_code for r in by_name}
            if len(codes) == 1:
                return _Match(ModelMatch.EXACT_NAME, by_name)
            # same marketing name listed under several codes: take one, deterministically
            first = sorted(c for c in codes if c)[0]
            return _Match(ModelMatch.EXACT_NAME, [r for r in by_name if r.model_code == first])

    if code.startswith("SM-") and not names_only:
        fam = _code_family(code)
        variant = [r for r in candidates if _code_family(_norm_code(r.model_code)) == fam]
        if variant:
            first = sorted({r.model_code for r in variant if r.model_code})[0]
            return _Match(ModelMatch.VARIANT, [r for r in variant if r.model_code == first])
    return _Match(ModelMatch.NONE)


def _freshness(oldest: datetime, now: datetime, policy: PricingPolicy) -> Freshness:
    age = (now - oldest).days
    if age <= policy.fresh_days:
        return Freshness.FRESH
    if age <= policy.stale_days:
        return Freshness.AGING
    return Freshness.STALE


def _line(r: PriceRecord, label: str | None = None) -> EvidenceLine:
    return EvidenceLine(label=label or r.component_label, amount=r.amount, amount_high=r.amount_high, scope=r.scope,
                        organization=r.evidence.organization, source_type=r.evidence.source_type,
                        source_url=r.evidence.source_url, retrieved_at=r.evidence.retrieved_at,
                        last_verified_at=r.last_verified_at, published_at=r.evidence.published_at,
                        raw_label=r.evidence.raw_label, raw_value=r.evidence.raw_value, model_name=r.model_name,
                        model_code=r.model_code, applies_to_group=r.applies_to_group)


def estimate(dataset: PricingDataset, repair_type: str, repair_name: str, manufacturer: str | None,
             model: str | None, region: str, policy: PricingPolicy, now: datetime | None = None) -> PricingEstimate:
    now = now or datetime.now(UTC)
    base = PricingEstimate(repair_type=repair_type, repair_name=repair_name, status="insufficient_pricing_data",
                           data_kind="none", official_links=official_links(manufacturer, region),
                           why_quotes_differ=WHY_DIFFERS)
    records = [r for r in dataset.records if r.region == region and policy.allows(r)]
    model_records = [r for r in records if r.scope in (PriceScope.PARTS_ONLY, PriceScope.PARTS_AND_LABOUR)
                     and (r.model_code or r.model_name)]
    match = match_model(model_records, manufacturer, model)
    plans = COMPONENT_PLANS.get(repair_type)
    if repair_type == "professional_diagnosis" and match.kind != ModelMatch.NONE:
        return _inspection_estimate(base, match, records, policy, now)
    if match.kind == ModelMatch.NONE or not plans:
        reason = ("No source-attributed price was found for this phone model." if plans
                  else "None of the price sources lists this repair type.")
        return base.model_copy(update={"notes": [reason, "Use the official links below or enter a repair quote."]})

    by_comp = {r.component: r for r in match.records}
    chosen: list[PriceRecord] = []
    note: str | None = None
    combine: str = "single"
    for comps, how, plan_note in plans:
        available = [by_comp[c] for c in comps if c in by_comp]
        if available:
            chosen = available if how == "range" else [available[0]]
            note, combine = plan_note, how
            break
    sample = match.records[0]
    matched = _model_label(sample)
    if not chosen:
        return base.model_copy(update={
            "model_match": match.kind, "matched_model": matched,
            "notes": [f"{sample.evidence.organization} lists {matched} but no price for this repair."]})

    parts_low = min(r.amount for r in chosen)
    parts_high = max(r.amount_high or r.amount for r in chosen)
    lines = [_line(r) for r in chosen]
    notes = [n for n in [note] if n]
    if match.kind == ModelMatch.VARIANT:
        notes.append("Approximation: your phone's model code differs from the listed regional variant "
                     f"{sample.model_code}.")
    if match.kind == ModelMatch.EXACT_NAME:
        notes.append("Matched by model name; check that your phone's model number matches the one shown.")

    if all(r.scope == PriceScope.PARTS_AND_LABOUR for r in chosen):
        return _service_estimate(base, match.kind, matched, chosen, notes, combine, policy, now)

    tier = sample.evidence.raw_context.get("labour_tier")
    labour = next((r for r in records if r.component == LABOUR_COMPONENT and r.applies_to_group == tier
                   and r.manufacturer.lower() == sample.manufacturer.lower()), None)
    used = [*chosen, *([labour] if labour else [])]
    oldest = min(r.last_verified_at for r in used)
    limitations = list(dict.fromkeys(lim for r in used for lim in r.limitations))
    covered = {inc for r in used for inc in r.includes} | ({"parts"} if chosen else set())
    exclusions = list(dict.fromkeys(x for r in used for x in r.excludes
                                    if x not in covered and not (labour and x == "labour")))
    is_demo = any(r.evidence.source_type == SourceType.DEMO_FIXTURE for r in used)
    policy_notes = []
    if policy.show_policy_notes:
        for sid in dict.fromkeys(r.evidence.source_id for r in used):
            d = SOURCES_BY_ID.get(sid)
            if d and d.usage_policy.value == "legal_review_required":
                policy_notes.append(f"{d.organization}: {d.policy_notes}")
    common = {
        "currency": sample.currency, "tax": "excluded" if all(r.tax.value == "excluded" for r in used) else "unknown",
        "model_match": match.kind, "matched_model": matched, "freshness": _freshness(oldest, now, policy),
        "last_verified_at": oldest, "limitations": limitations, "notes": notes, "policy_notes": policy_notes,
        "data_kind": "demo_fixture" if is_demo else "source_attributed",
    }
    if labour is None:
        return base.model_copy(update={
            **common, "status": "parts_only", "low": parts_low, "high": parts_high, "scope": PriceScope.PARTS_ONLY,
            "evidence": lines, "exclusions": exclusions,
            "notes": [*notes, "Official labour charges for this model were not found, so this is the part "
                              "price only."]})
    lines.append(_line(labour, f"Labour ({labour.component_label}, {labour.applies_to_group})"))
    return base.model_copy(update={
        **common, "status": "estimated", "low": parts_low + labour.amount, "high": parts_high + labour.amount,
        "scope": PriceScope.PARTS_AND_LABOUR, "usable_for_decision": True, "evidence": lines,
        "exclusions": exclusions,
        "notes": [*notes, f"Official part price plus {labour.evidence.organization}'s standard labour charge, "
                          "before tax."
                  + (" Range shown because the price depends on which part fails." if combine == "range" else "")]})


def _inspection_estimate(base: PricingEstimate, match: _Match, records: list[PriceRecord], policy: PricingPolicy,
                         now: datetime) -> PricingEstimate:
    """The manufacturer's published inspection fee for the model's service tier (not a repair price)."""
    sample = match.records[0]
    tier = sample.evidence.raw_context.get("labour_tier")
    fee = next((r for r in records if r.component == "inspection_fee" and r.applies_to_group == tier
                and r.manufacturer.lower() == sample.manufacturer.lower()), None)
    matched = _model_label(sample)
    if fee is None:
        return base.model_copy(update={"model_match": match.kind, "matched_model": matched,
                                       "notes": ["No published inspection fee was found for this model."]})
    return base.model_copy(update={
        "status": "estimated", "data_kind": "source_attributed", "currency": fee.currency, "low": fee.amount,
        "high": fee.amount, "scope": PriceScope.INSPECTION_FEE, "tax": fee.tax.value, "usable_for_decision": False,
        "model_match": match.kind, "matched_model": matched, "freshness": _freshness(fee.last_verified_at, now, policy),
        "last_verified_at": fee.last_verified_at, "evidence": [_line(fee, f"Inspection fee ({fee.applies_to_group})")],
        "exclusions": [x for x in fee.excludes if x != "parts"], "limitations": fee.limitations,
        "notes": [f"{fee.evidence.organization}'s published inspection charge for this model's service tier. "
                  "It is charged when you decide not to go ahead with a repair after inspection."]})


def _model_label(r: PriceRecord) -> str:
    return f"{r.model_name} ({r.model_code})" if r.model_code else str(r.model_name)


def _policy_notes(used: list[PriceRecord], policy: PricingPolicy) -> list[str]:
    notes = []
    if policy.show_policy_notes:
        for sid in dict.fromkeys(r.evidence.source_id for r in used):
            d = SOURCES_BY_ID.get(sid)
            if d and d.usage_policy.value == "legal_review_required":
                notes.append(f"{d.organization}: {d.policy_notes}")
    return notes


def _service_estimate(base: PricingEstimate, kind: ModelMatch, matched: str, chosen: list[PriceRecord],
                      notes: list[str], combine: str, policy: PricingPolicy, now: datetime) -> PricingEstimate:
    """A manufacturer's complete repair estimate (parts + labour); used as-is, never combined with labour."""
    org = chosen[0].evidence.organization
    tax = "excluded" if all(r.tax.value == "excluded" for r in chosen) else (
        "included" if all(r.tax.value == "included" for r in chosen) else "unknown")
    oldest = min(r.last_verified_at for r in chosen)
    is_demo = any(r.evidence.source_type == SourceType.DEMO_FIXTURE for r in chosen)
    return base.model_copy(update={
        "status": "estimated", "data_kind": "demo_fixture" if is_demo else "source_attributed",
        "currency": chosen[0].currency, "low": min(r.amount for r in chosen),
        "high": max(r.amount_high or r.amount for r in chosen), "scope": PriceScope.PARTS_AND_LABOUR, "tax": tax,
        "usable_for_decision": True, "model_match": kind, "matched_model": matched,
        "freshness": _freshness(oldest, now, policy), "last_verified_at": oldest,
        "evidence": [_line(r) for r in chosen],
        "exclusions": list(dict.fromkeys(x for r in chosen for x in r.excludes)),
        "limitations": list(dict.fromkeys(lim for r in chosen for lim in r.limitations)),
        "policy_notes": _policy_notes(chosen, policy),
        "notes": [*notes, f"{org}'s official repair estimate for this model (parts and labour)."
                  + (" Range shown because the price depends on which part fails." if combine == "range" else "")],
    })
