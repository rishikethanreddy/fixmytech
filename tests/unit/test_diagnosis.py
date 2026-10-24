from fixmytech.agent.collectors import run_scan
from fixmytech.core.observations import CheckStatus
from fixmytech.domain.diagnosis import SupportLevel, deduplicate, diagnose, fire_rules
from fixmytech.domain.facts import DeviceProfile, active_answers, build_facts
from fixmytech.domain.knowledge import default_knowledge_base
from tests.conftest import SERIAL, FakeAdb

KB = default_knowledge_base()
DEV = DeviceProfile(manufacturer="samsung", model="SM-G991B", age_months=36, region="US")


def scan():
    return run_scan(FakeAdb(), SERIAL, b"0123456789abcdef")


def cand(result, fault):
    return next((c for c in result.candidates if c.fault_id == fault), None)


def test_manual_no_answers_is_insufficient_evidence():
    r = diagnose(KB, DEV, None, {})
    assert r.outcome == "insufficient_evidence"
    assert r.candidates == [] or all(c.support_level != SupportLevel.STRONG for c in r.candidates)
    assert "not probabilities" in r.score_interpretation


def test_scan_evidence_drives_storage_and_software():
    r = diagnose(KB, DEV, scan(), {})
    storage = cand(r, "storage_pressure")
    assert storage and storage.support_level in (SupportLevel.MODERATE, SupportLevel.STRONG)
    assert storage.has_device_evidence
    sw = cand(r, "software_instability")
    assert sw and sw.score >= 2.5
    # every supporting item cites the facts it used, with provenance
    for item in storage.supporting:
        assert item.facts and item.facts[0].source.value.startswith("device_")


def test_battery_strong_from_device_and_customer_evidence():
    answers = {"primary_symptoms": ["battery_drain", "shutdowns"], "battery_drain_pattern": "sudden_drops",
               "shutdown_at_charge": "yes_above_20"}
    r = diagnose(KB, DEV, scan(), answers)
    b = cand(r, "battery_wear")
    assert b.support_level == SupportLevel.STRONG
    assert {"BATT-CAPACITY-LOW", "BATT-DRAIN-SUDDEN"} <= {e.rule_id for e in b.supporting}


def test_group_deduplication_prevents_double_counting():
    answers = {"primary_symptoms": ["storage_full", "slow"], "storage_warning": "yes"}
    records = build_facts(KB, DEV, scan(), answers)
    items = deduplicate(fire_rules(KB, records))
    storage_obs = [i for i in items if i.fault == "storage_pressure" and i.group == "storage_obs" and i.counted]
    assert len(storage_obs) == 1


def test_contradictory_evidence_is_kept_and_flagged():
    answers = {"primary_symptoms": ["not_charging"], "charging_behavior": "intermittent",
               "charging_other_cable": "works_fine"}
    r = diagnose(KB, DEV, None, answers)
    c = cand(r, "charging_path")
    assert c.contradicting and c.conflicting
    assert any(n.text.startswith("Your original cable") for n in r.notes)


def test_safety_notes_first_and_next_action():
    answers = {"primary_symptoms": ["battery_drain"], "battery_swelling": "yes"}
    r = diagnose(KB, DEV, None, answers)
    assert r.notes[0].severity == "safety"
    assert r.next_actions[0].startswith("Follow the safety advice")


def test_failed_checks_are_missing_evidence_not_health():
    s = scan()
    for o in s.observations:
        if o.check_id == "battery.capacity":
            o.status, o.value = CheckStatus.PERMISSION_DENIED, {}
    r = diagnose(KB, DEV, s, {"primary_symptoms": ["battery_drain"], "battery_drain_pattern": "sudden_drops"})
    b = cand(r, "battery_wear")
    assert not any(e.rule_id == "BATT-CAPACITY-GOOD" for e in b.contradicting)
    assert any(m.ref == "battery.capacity" for m in b.missing_evidence)
    assert any(u["check_id"] == "battery.capacity" for u in r.unavailable_checks)


def test_stale_answers_drop_out_when_question_becomes_invisible():
    answers = {"primary_symptoms": ["camera"], "battery_drain_pattern": "sudden_drops"}
    active = active_answers(KB, {}, answers)
    assert "battery_drain_pattern" not in active
    r = diagnose(KB, DEV, None, answers)
    assert "battery_drain_pattern" in r.inactive_answers
    assert cand(r, "battery_wear") is None


def test_general_checkup_with_nothing_found():
    r = diagnose(KB, DEV, None, {"primary_symptoms": ["none"], "physical_event": "none"})
    assert r.outcome == "no_fault_indicated"
    assert "not proof" in r.outcome_summary


def test_close_call_detected():
    answers = {"primary_symptoms": ["touch"], "touch_symptom": "ghost_touches", "touch_after_restart": "goes_away",
               "safe_mode": "continues"}
    r = diagnose(KB, DEV, None, answers)
    top = [c for c in r.candidates if c.support_level in (SupportLevel.MODERATE, SupportLevel.STRONG)]
    if len(top) >= 2 and top[0].score - top[1].score < 1.5:
        assert r.close_call is not None


def test_no_percentages_in_output():
    r = diagnose(KB, DEV, scan(), {"primary_symptoms": ["slow"]})
    text = r.model_dump_json()
    assert "probability" not in text.lower().replace("not probabilities", "")
