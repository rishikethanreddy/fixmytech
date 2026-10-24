"""Tests for component-level repair cost in the diagnosis step (Task 1).

Unit tests verify candidate_repair_costs logic; integration tests verify
the GET /diagnosis endpoint includes the repair_costs dict.
"""
from datetime import UTC, datetime
from pathlib import Path

from fixmytech.domain.diagnosis import SupportLevel, diagnose
from fixmytech.domain.facts import DeviceProfile
from fixmytech.domain.knowledge import default_knowledge_base
from fixmytech.domain.repair import RepairPricingService, load_demo_rows
from fixmytech.pricing.collectors.samsung_in import parse_labour_page, parse_parts_page
from fixmytech.pricing.estimate import PricingPolicy
from fixmytech.pricing.models import PricingDataset
from fixmytech.pricing.store import merge

KB = default_knowledge_base()
FX = Path(__file__).parents[1] / "fixtures" / "pricing"
T0 = datetime.now(UTC)
S24U = DeviceProfile(manufacturer="samsung", model="SM-S928B", region="IN")


def _service(demo=False) -> RepairPricingService:
    ds = PricingDataset()
    merge(ds, "samsung_in_parts", parse_parts_page((FX / "samsung_in_parts_excerpt.html").read_text("utf-8"), T0,
                                                   None).records, T0)
    merge(ds, "samsung_in_labour", parse_labour_page((FX / "samsung_in_labour_excerpt.html").read_text("utf-8"), T0,
                                                     None).records, T0)
    return RepairPricingService(None, PricingPolicy(demo_enabled=demo),
                                demo_rows=load_demo_rows() if demo else None, dataset=ds)


# -------------------------------------------------------------------------- taxonomy checks


def test_taxonomy_components_field():
    """Every fault has a (possibly empty) components list."""
    for fault in KB.faults.values():
        assert isinstance(fault.components, list), f"fault {fault.id} missing components list"


def test_hardware_faults_have_components():
    """Battery, screen, camera, charging port faults should have at least one component."""
    hw = ["battery_wear", "display_damage", "camera_fault", "charging_path"]
    for fid in hw:
        assert KB.faults[fid].components, f"fault {fid} should list suspected components"


def test_software_faults_have_no_components():
    """Software-only faults do not map to physical components."""
    assert KB.faults["software_instability"].components == []
    assert KB.faults["storage_pressure"].components == []


# -------------------------------------------------------------------------- candidate_repair_costs unit tests


class _FakeAppState:
    """Minimal stand-in for services.AppState to test candidate_repair_costs."""

    def __init__(self, svc, kb):
        self.pricing = svc
        self.kb = kb
        self.settings = type("S", (), {"ml_enabled": False, "ml_model_dir": None})()


class _FakeRec:
    """Minimal stand-in for a DiagnosticSession."""

    def __init__(self, device=S24U):
        self.device = {"manufacturer": device.manufacturer, "model": device.model,
                       "storage_gb": device.storage_gb, "age_months": device.age_months,
                       "region": device.region}
        self.scan = None
        self.answers = {}
        self.guided_tests = {}
        self.quotes = []


def test_candidate_repair_costs_returns_costs_for_hardware_faults():
    """Candidates with repair types get a repair_cost block."""
    from fixmytech.server.services import candidate_repair_costs

    svc = _service()
    answers = {"primary_symptoms": ["battery_drain", "shutdowns"],
               "battery_drain_pattern": "sudden_drops",
               "shutdown_at_charge": "yes_above_20",
               "physical_event": "none"}
    diag = diagnose(KB, S24U, None, answers)
    state = _FakeAppState(svc, KB)
    rec = _FakeRec(S24U)
    rec.answers = answers

    costs = candidate_repair_costs(state, rec, diag)

    # battery_wear is the top candidate with strong/moderate support; it should have cost data
    assert "battery_wear" in costs
    bc = costs["battery_wear"]
    assert bc["repair_type"] == "battery_replacement"
    assert bc["repair_name"] == "Battery replacement"
    assert bc["status"] in ("estimated", "parts_only")
    assert bc["conditional_label"] == "if this component is the cause"
    if bc["status"] in ("estimated", "parts_only"):
        assert bc["currency"] == "INR"
        assert bc["low"] is not None and bc["high"] is not None
        assert bc["low"] > 0


def test_candidate_repair_costs_empty_for_software_only():
    """Software-only candidates do not get a cost block."""
    from fixmytech.server.services import candidate_repair_costs

    svc = _service()
    diag = diagnose(KB, S24U, None, {})  # no answers → no strong candidates
    state = _FakeAppState(svc, KB)
    rec = _FakeRec(S24U)

    costs = candidate_repair_costs(state, rec, diag)
    # software_instability and storage_pressure have no repair types or are minimal; should be absent
    assert "storage_pressure" not in costs


def test_candidate_repair_costs_insufficient_data():
    """If pricing data is missing for a device, status is insufficient_pricing_data."""
    from fixmytech.server.services import candidate_repair_costs

    svc = _service()
    # Unknown model with no pricing data
    device = DeviceProfile(manufacturer="nokia", model="X", region="US")
    answers = {"primary_symptoms": ["battery_drain"], "battery_drain_pattern": "sudden_drops",
               "physical_event": "none"}
    diag = diagnose(KB, device, None, answers)
    state = _FakeAppState(svc, KB)
    rec = _FakeRec(device)
    rec.answers = answers

    costs = candidate_repair_costs(state, rec, diag)
    # battery_wear may still appear with insufficient_pricing_data
    if "battery_wear" in costs:
        assert costs["battery_wear"]["status"] == "insufficient_pricing_data"


def test_confirmation_note_on_non_strong_candidates():
    """Candidates that aren't strongly supported get a confirmation_note."""
    from fixmytech.server.services import candidate_repair_costs

    svc = _service()
    # Ask about screen + battery: screen may be moderate/weak
    answers = {"primary_symptoms": ["screen", "battery_drain"],
               "screen_symptom": ["cracked_glass"],
               "battery_drain_pattern": "sudden_drops",
               "physical_event": "dropped"}
    diag = diagnose(KB, S24U, None, answers)
    state = _FakeAppState(svc, KB)
    rec = _FakeRec(S24U)
    rec.answers = answers

    costs = candidate_repair_costs(state, rec, diag)
    for fid, block in costs.items():
        cand = next(c for c in diag.candidates if c.fault_id == fid)
        if cand.support_level != SupportLevel.STRONG or cand.requires_confirmation:
            assert "confirmation_note" in block, f"missing confirmation_note for {fid} (support={cand.support_level})"
