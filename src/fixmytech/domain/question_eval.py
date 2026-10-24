"""Compare question-selection strategies on hand-written personas (SIMULATION, not real cases).

    python -m fixmytech.domain.question_eval

Each persona has a known intended fault and scripted answers; unscripted questions get a neutral answer
("not sure", "not tried", "none"...). The personas were written by the FixMyTech developers from the same
knowledge that produced the rules, so results only show how the strategies behave on the rule base:
how many questions they ask and whether the intended fault ends up on top. They are NOT evidence of
real-world diagnostic accuracy.
"""

from __future__ import annotations

import json
import statistics
import sys
from dataclasses import dataclass, field
from typing import Any

from fixmytech.domain.diagnosis import SupportLevel, diagnose
from fixmytech.domain.facts import Answers, DeviceProfile
from fixmytech.domain.knowledge import KnowledgeBase, Question, default_knowledge_base
from fixmytech.domain.questionnaire import Strategy, next_question

CAVEAT = ("Simulation on developer-written personas; measures questioning efficiency on the rule base only, "
          "not diagnostic accuracy on real phones.")
NEUTRAL = ("unsure", "not_tried", "none", "no", "not_available", "clean", "works_fine", "original")


@dataclass
class Persona:
    name: str
    fault: str | None  # None: no phone fault (e.g. bad charger)
    answers: Answers
    device: DeviceProfile = field(default_factory=lambda: DeviceProfile(age_months=24))


PERSONAS: list[Persona] = [
    Persona("worn battery", "battery_wear", {
        "primary_symptoms": ["battery_drain", "shutdowns"], "battery_drain_pattern": "sudden_drops",
        "shutdown_at_charge": "yes_above_20", "battery_history": "original", "safe_mode": "continues"},
        DeviceProfile(age_months=40)),
    Persona("app conflict", "software_instability", {
        "primary_symptoms": ["app_crashes", "slow"], "crash_scope": "many_apps", "recent_update": "yes",
        "safe_mode": "stops"}),
    Persona("full storage", "storage_pressure", {
        "primary_symptoms": ["slow", "storage_full"], "storage_warning": "yes", "crash_scope": "many_apps"}),
    Persona("worn port", "charging_path", {
        "primary_symptoms": ["not_charging"], "charging_behavior": "intermittent",
        "charging_other_cable": "same_problem", "port_debris": "damage", "wireless_charging": "works"}),
    Persona("dropped phone", "display_damage", {
        "primary_symptoms": ["screen", "touch"], "physical_event": "dropped",
        "screen_symptom": ["cracked_glass", "lines_or_spots"], "touch_symptom": "dead_areas",
        "touch_after_restart": "persists"}),
    Persona("dead camera", "camera_fault", {
        "primary_symptoms": ["camera"], "physical_event": "dropped", "camera_symptom": "black_image",
        "camera_other_app": "fails"}),
    Persona("silent speaker", "audio_fault", {
        "primary_symptoms": ["audio"], "audio_symptom": "no_speaker", "audio_headphones": "works"}),
    Persona("no service", "connectivity_issue", {
        "primary_symptoms": ["network"], "network_symptom": "no_service", "network_scope": "everywhere"}),
    Persona("water exposure", "liquid_damage", {
        "primary_symptoms": ["audio", "not_charging"], "physical_event": "liquid", "liquid_timing": "days_ago",
        "port_debris": "corrosion"}),
    Persona("ambiguous drain", "battery_wear", {
        "primary_symptoms": ["battery_drain"], "battery_drain_pattern": "fast_always"},
        DeviceProfile(age_months=36)),
    Persona("faulty charger, phone fine", None, {
        "primary_symptoms": ["not_charging"], "charging_behavior": "no_response",
        "charging_other_cable": "works_fine"}),
]


def _neutral(q: Question) -> str | list[str]:
    values = [o.value for o in q.options]
    if q.type == "multi_choice":
        return ["none"] if "none" in values else [values[-1]]
    return next((v for v in NEUTRAL if v in values), values[-1])


def run_persona(kb: KnowledgeBase, p: Persona, strategy: Strategy) -> dict[str, Any]:
    answers: Answers = {}
    asked: list[str] = []
    stop = None
    for _ in range(60):
        nq = next_question(kb, p.device, None, answers, strategy=strategy)
        if nq.question is None:
            stop = nq.stop_reason
            break
        q = kb.questions[nq.question.id]
        answers[q.id] = p.answers.get(q.id, _neutral(q))
        asked.append(q.id)
    d = diagnose(kb, p.device, None, answers)
    top = d.candidates[0] if d.candidates and d.candidates[0].score > 0 else None
    meaningful = top is not None and top.support_level in (SupportLevel.STRONG, SupportLevel.MODERATE)
    if p.fault is None:
        correct = not meaningful or top is None or top.fault_id != "charging_path"
    else:
        correct = meaningful and top is not None and top.fault_id == p.fault
    return {"persona": p.name, "intended": p.fault, "top": top.fault_id if top else None,
            "top_support": top.support_level.value if top else None, "correct": correct,
            "questions_asked": len(asked), "stop_reason": stop}


def evaluate(kb: KnowledgeBase | None = None,
             strategies: tuple[Strategy, ...] = ("fixed_order", "rule_utility", "expected_entropy")) -> dict[str, Any]:
    kb = kb or default_knowledge_base()
    out: dict[str, Any] = {"caveat": CAVEAT, "personas": len(PERSONAS), "strategies": {}}
    for strat in strategies:
        rows = [run_persona(kb, p, strat) for p in PERSONAS]
        out["strategies"][strat] = {
            "intended_fault_on_top": sum(r["correct"] for r in rows),
            "mean_questions": round(statistics.mean(r["questions_asked"] for r in rows), 2),
            "max_questions": max(r["questions_asked"] for r in rows),
            "rows": rows,
        }
    return out


def main() -> None:
    res = evaluate()
    sys.stdout.write(res["caveat"] + "\n\n")
    sys.stdout.write(f"{'strategy':18} {'intended on top':>16} {'mean questions':>15} {'max':>4}\n")
    for name, r in res["strategies"].items():
        sys.stdout.write(f"{name:18} {r['intended_fault_on_top']:>10}/{res['personas']:<5} "
                         f"{r['mean_questions']:>15} {r['max_questions']:>4}\n")
    if "--json" in sys.argv:
        sys.stdout.write(json.dumps(res, indent=2) + "\n")


if __name__ == "__main__":
    main()
