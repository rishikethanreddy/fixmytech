import pytest

from fixmytech.agent.collectors import run_scan
from fixmytech.domain.facts import DeviceProfile
from fixmytech.domain.knowledge import default_knowledge_base
from fixmytech.domain.questionnaire import InvalidAnswerError, next_question, validate_answer
from tests.conftest import SERIAL, FakeAdb

KB = default_knowledge_base()
DEV = DeviceProfile(manufacturer="Google", model="Pixel 7", age_months=20)


def test_first_question_is_symptom_screen():
    nq = next_question(KB, DEV, None, {})
    assert nq.question and nq.question.id == "primary_symptoms"
    assert nq.budget == KB.question_settings.max_questions


def test_follow_ups_are_conditional_on_symptoms():
    nq = next_question(KB, DEV, None, {"primary_symptoms": ["not_charging"], "physical_event": "none"})
    assert nq.question and nq.question.id in {"charging_behavior", "charging_other_cable", "battery_swelling",
                                              "port_debris", "battery_history"}
    asked = set()
    answers = {"primary_symptoms": ["camera"], "physical_event": "none"}
    for _ in range(10):
        nq = next_question(KB, DEV, None, answers)
        if not nq.question:
            break
        asked.add(nq.question.id)
        answers[nq.question.id] = nq.question.options[0]["value"]
    assert not asked & {"charging_behavior", "battery_drain_pattern", "touch_symptom", "network_symptom"}
    assert "camera_symptom" in asked


def test_questions_stop_and_never_repeat():
    answers: dict = {}
    seen = []
    for _ in range(40):
        nq = next_question(KB, DEV, None, answers)
        if nq.question is None:
            assert nq.stop_reason in {"budget_reached", "no_more_questions", "confident_enough",
                                      "no_informative_questions"}
            break
        assert nq.question.id not in seen
        seen.append(nq.question.id)
        q = KB.questions[nq.question.id]
        if q.type == "multi_choice":
            answers[q.id] = [o.value for o in q.options if not o.exclusive]
        else:
            answers[q.id] = q.options[0].value
    assert len(seen) <= KB.question_settings.max_questions


def test_budget_stop():
    s = KB.question_settings
    answers = {"primary_symptoms": ["battery_drain", "shutdowns", "not_charging", "overheating", "screen",
                                    "app_crashes", "camera", "audio", "network"]}
    for q in list(KB.questions.values())[1:]:
        if len(answers) >= s.max_questions:
            break
        answers[q.id] = q.options[0].value if q.type == "single_choice" else [q.options[0].value]
    nq = next_question(KB, DEV, None, answers)
    if len(answers) >= s.max_questions:
        assert nq.stop_reason == "budget_reached"


def test_scan_makes_questions_visible():
    # storage check succeeded, so the 'storage_warning' question (asked only without scan data) is hidden
    s = run_scan(FakeAdb(), SERIAL, b"0123456789abcdef")
    answers = {"primary_symptoms": ["slow"], "physical_event": "none"}
    ids = []
    for _ in range(12):
        nq = next_question(KB, DEV, s, answers)
        if not nq.question:
            break
        ids.append(nq.question.id)
        answers[nq.question.id] = nq.question.options[0]["value"]
    assert "storage_warning" not in ids


@pytest.mark.parametrize("qid,value", [
    ("physical_event", "teleported"),
    ("physical_event", ["none"]),
    ("primary_symptoms", "battery_drain"),
    ("primary_symptoms", []),
    ("primary_symptoms", ["none", "battery_drain"]),
    ("primary_symptoms", ["slow", "slow"]),
])
def test_invalid_answers_rejected(qid, value):
    with pytest.raises(InvalidAnswerError):
        validate_answer(KB.questions[qid], value)


def test_valid_answers():
    assert validate_answer(KB.questions["physical_event"], "dropped") == "dropped"
    assert validate_answer(KB.questions["primary_symptoms"], ["slow", "camera"]) == ["slow", "camera"]


def _passed(*test_ids: str, **measures: dict) -> dict:
    return {tid: {"outcome": "pass", "outcome_kind": "phone_measured", "measures": measures.get(tid, {}),
                  "measures_kind": "phone_measured"} for tid in test_ids}


def test_questions_answered_by_tests_are_skipped():
    answers = {"primary_symptoms": ["camera", "audio"], "physical_event": "none"}
    tests = _passed("camera_rear", "camera_front", "speaker", "microphone", "earpiece_call")
    asked = set()
    for _ in range(12):
        nq = next_question(KB, DEV, None, answers, tests)
        if not nq.question:
            break
        asked.add(nq.question.id)
        q = KB.questions[nq.question.id]
        answers[q.id] = [q.options[0].value] if q.type == "multi_choice" else q.options[0].value
    assert not asked & {"camera_symptom", "camera_other_app", "audio_symptom", "audio_headphones"}
    skipped = {s["id"]: s["reason"] for s in nq.skipped_by_tests}
    assert skipped["camera_symptom"] == "both camera tests passed"
    assert "audio_symptom" in skipped


def test_partial_test_results_do_not_skip_questions():
    answers = {"primary_symptoms": ["camera"], "physical_event": "none"}
    tests = _passed("camera_rear")  # the front camera was not tested
    nq = next_question(KB, DEV, None, answers, tests)
    assert all(s["id"] != "camera_symptom" for s in nq.skipped_by_tests)


def test_touch_question_needs_full_coverage_to_be_skipped():
    answers = {"primary_symptoms": ["touch"], "physical_event": "none"}
    partial = _passed("touch_grid", touch_grid={"coverage_pct": 80})
    full = _passed("touch_grid", touch_grid={"coverage_pct": 100})
    assert "touch_symptom" not in {s["id"] for s in next_question(KB, DEV, None, answers, partial).skipped_by_tests}
    assert "touch_symptom" in {s["id"] for s in next_question(KB, DEV, None, answers, full).skipped_by_tests}


def test_no_skips_without_tests():
    nq = next_question(KB, DEV, None, {"primary_symptoms": ["camera"], "physical_event": "none"})
    assert nq.skipped_by_tests == []


def _failed(*test_ids: str) -> dict:
    return {tid: {"outcome": "fail", "outcome_kind": "phone_measured", "measures": {},
                  "measures_kind": "phone_measured"} for tid in test_ids}


def test_no_questions_needed_when_tests_give_a_strong_answer():
    nq = next_question(KB, DEV, None, {}, _failed("speaker", "microphone"))
    assert nq.question is None and nq.stop_reason == "no_questions_needed"


def test_single_failed_test_still_asks():
    nq = next_question(KB, DEV, None, {}, _failed("speaker"))
    assert nq.question and nq.question.id == "primary_symptoms"


def test_fixed_order_baseline_ignores_the_shortcut():
    nq = next_question(KB, DEV, None, {}, _failed("speaker", "microphone"), strategy="fixed_order")
    assert nq.question is not None


def test_customer_can_still_describe_other_problems():
    nq = next_question(KB, DEV, None, {}, _failed("speaker", "microphone"), more=True)
    assert nq.question and nq.question.id == "primary_symptoms"
