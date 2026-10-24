from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from fixmytech.domain.diagnosis import diagnose
from fixmytech.domain.facts import DeviceProfile
from fixmytech.domain.knowledge import default_knowledge_base
from fixmytech.domain.repair import (
    QuoteInput,
    RepairPricingService,
    compare_quote,
    estimate_for_diagnosis,
    load_demo_rows,
)
from fixmytech.pricing.collectors.samsung_in import parse_labour_page, parse_parts_page
from fixmytech.pricing.estimate import PricingPolicy
from fixmytech.pricing.models import PricingDataset
from fixmytech.pricing.store import PricingStore, merge

KB = default_knowledge_base()
FX = Path(__file__).parents[1] / "fixtures" / "pricing"
T0 = datetime.now(UTC)
S24U = DeviceProfile(manufacturer="samsung", model="SM-S928B", region="IN")


def service(demo=False, allowed=None) -> RepairPricingService:
    ds = PricingDataset()
    merge(ds, "samsung_in_parts", parse_parts_page((FX / "samsung_in_parts_excerpt.html").read_text("utf-8"), T0,
                                                   None).records, T0)
    merge(ds, "samsung_in_labour", parse_labour_page((FX / "samsung_in_labour_excerpt.html").read_text("utf-8"), T0,
                                                     None).records, T0)
    return RepairPricingService(None, PricingPolicy(allowed_sources=allowed, demo_enabled=demo),
                                demo_rows=load_demo_rows() if demo else None, dataset=ds)


def q(amount, cur="INR", rt="battery_replacement"):
    return QuoteInput(repair_type=rt, amount=Decimal(amount), currency=cur)


def test_quote_comparison_against_parts_and_labour():
    est = service().estimate(KB, "battery_replacement", S24U)  # 2400 + 425 before tax
    assert compare_quote(q(2000), est).position == "below_range"
    assert compare_quote(q(2825), est).position == "within_range"
    above = compare_quote(q(4000), est)
    assert above.position == "above_range" and "excludes tax" in above.explanation
    assert compare_quote(q(2825, "USD"), est).position == "not_comparable"


def test_quote_comparison_parts_only_reference():
    est = service(allowed={"samsung_in_parts"}).estimate(KB, "battery_replacement", S24U)
    assert est.status == "parts_only"
    assert compare_quote(q(1500), est).position == "below_part_price"
    assert compare_quote(q(3000), est).position == "not_comparable"


def test_no_generic_fallback_without_demo():
    est = service().estimate(KB, "battery_replacement", DeviceProfile(manufacturer="Nokia", model="X", region="US"))
    assert est.status == "insufficient_pricing_data" and est.data_kind == "none"


def test_demo_fixture_only_when_enabled_and_labelled():
    est = service(demo=True).estimate(KB, "battery_replacement", DeviceProfile(manufacturer="Nokia", model="X",
                                                                             region="US"))
    assert est.data_kind == "demo_fixture" and any("DEMO" in n for n in est.notes)
    real = service(demo=True).estimate(KB, "battery_replacement", S24U)
    assert real.data_kind == "source_attributed"  # real evidence always wins over demo
    assert "DEMO" in compare_quote(q(60, "USD"), est).explanation


def test_estimates_follow_diagnostic_uncertainty():
    svc = service()
    d = diagnose(KB, S24U, None, {})
    rep = estimate_for_diagnosis(svc, KB, d, S24U, [])
    assert [e.repair_type for e in rep.estimates] == ["professional_diagnosis"]
    assert rep.estimates[0].scope == "inspection_fee" and not rep.estimates[0].usable_for_decision
    assert "No fault is well supported" in rep.diagnostic_uncertainty

    d = diagnose(KB, S24U, None, {"primary_symptoms": ["screen", "battery_drain"], "screen_symptom": ["cracked_glass"],
                                  "battery_drain_pattern": "sudden_drops", "physical_event": "dropped"})
    rep = estimate_for_diagnosis(svc, KB, d, S24U, [q(3000)])
    by = {e.repair_type: e for e in rep.estimates}
    assert by["screen_replacement"].usable_for_decision and by["battery_replacement"].usable_for_decision
    assert rep.overall_currency == "INR" and rep.overall_low == Decimal(2825) and rep.overall_high == Decimal(16585)
    assert all(e.fault_support for e in rep.estimates)
    assert rep.quotes[0].position == "above_range" and rep.demo_data_in_use is False


def test_service_reloads_when_dataset_file_changes(tmp_path):
    store = PricingStore(tmp_path)
    svc = RepairPricingService(store, PricingPolicy())
    assert svc.estimate(KB, "battery_replacement", S24U).status == "insufficient_pricing_data"
    ds = PricingDataset()
    merge(ds, "samsung_in_parts", parse_parts_page((FX / "samsung_in_parts_excerpt.html").read_text("utf-8"), T0,
                                                   None).records, T0)
    store.save(ds)
    assert svc.estimate(KB, "battery_replacement", S24U).status == "parts_only"


@pytest.mark.parametrize("env_kw,msg", [
    ({"FIXMYTECH_PRICING_DEMO": True, "FIXMYTECH_PRICING_ALLOWED_SOURCES": ""}, "PRICING_DEMO"),
    ({}, "ALLOWED_SOURCES"),
])
def test_production_pricing_rules(env_kw, msg):
    from fixmytech.server.config import Settings

    with pytest.raises(ValueError, match=msg):
        Settings(FIXMYTECH_ENV="production", **env_kw)
