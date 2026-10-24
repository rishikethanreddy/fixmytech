from fixmytech.domain.facts import DeviceProfile
from fixmytech.domain.knowledge import default_knowledge_base
from fixmytech.domain.question_eval import CAVEAT, evaluate
from fixmytech.domain.questionnaire import _entropy, next_question

KB = default_knowledge_base()
DEV = DeviceProfile(age_months=20)


def test_evaluation_harness_runs_and_carries_caveat():
    res = evaluate(KB)
    assert res["caveat"] == CAVEAT and "not diagnostic accuracy" in CAVEAT
    fixed, rule = res["strategies"]["fixed_order"], res["strategies"]["rule_utility"]
    # Regression guard on the simulation (not an accuracy claim): adaptive selection must not get worse than
    # the fixed baseline at finding the intended fault, and must not ask more questions on average.
    assert rule["intended_fault_on_top"] >= fixed["intended_fault_on_top"]
    assert rule["mean_questions"] <= fixed["mean_questions"]
    for strat in res["strategies"].values():
        assert strat["max_questions"] <= KB.question_settings.max_questions


def test_all_strategies_return_valid_visible_questions():
    for strat in ("rule_utility", "expected_entropy", "fixed_order"):
        nq = next_question(KB, DEV, None, {"primary_symptoms": ["camera"]}, strategy=strat)
        assert nq.question is not None and nq.strategy == strat
        assert nq.question.id not in {"charging_behavior", "battery_drain_pattern"}


def test_safety_concern_stops_questions():
    nq = next_question(KB, DEV, None, {"primary_symptoms": ["battery_drain"], "battery_swelling": "yes"})
    assert nq.question is None and nq.stop_reason == "safety_concern"


def test_liquid_exposure_stops_for_professional_inspection():
    nq = next_question(KB, DEV, None, {"primary_symptoms": ["audio"], "physical_event": "liquid",
                                       "liquid_timing": "days_ago"})
    assert nq.question is None and nq.stop_reason == "professional_inspection"


def test_entropy_is_highest_when_scores_are_flat():
    flat = _entropy(dict.fromkeys(KB.faults, 0.0))
    peaked = _entropy({**dict.fromkeys(KB.faults, 0.0), "battery_wear": 8.0})
    assert flat > peaked > 0
