"""Rule-based adaptive question selection.

Utility of a candidate question (transparent, no learned parameters):

    utility = priority / 100 + sum over target faults of activity(fault)

    activity(fault) = 1.0   if the fault is among the top 3 by score and score > 0  (discriminate leaders)
                      0.5   if score > 0                                           (still in play)
                      0.15  otherwise                                              (screening)

The highest-utility visible, unanswered question is asked next. Stopping rules, checked in order:
budget reached; no visible questions left; leader is 'strong' and leads the runner-up by at least
``confident_margin`` after ``min_questions``; best utility below ``min_utility``.

This method has NOT been evaluated against a fixed-order questionnaire on real cases, so no claim is
made that it is more accurate; it is designed to ask fewer irrelevant questions.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel

from fixmytech.core.observations import ScanResult
from fixmytech.domain import conditions
from fixmytech.domain.diagnosis import SupportLevel, deduplicate, fault_scores, fire_rules, fired_rules, support_level
from fixmytech.domain.facts import Answers, DeviceProfile, TestResults, build_facts, fact_values
from fixmytech.domain.knowledge import KnowledgeBase, Question


class InvalidAnswerError(ValueError):
    pass


class QuestionView(BaseModel):
    id: str
    text: str
    help: str | None
    type: str
    options: list[dict[str, Any]]


class NextQuestion(BaseModel):
    question: QuestionView | None
    stop_reason: str | None
    asked: int
    budget: int
    utility: float | None = None
    why: str | None = None
    strategy: str | None = None
    skipped_by_tests: list[dict[str, str]] = []


def validate_answer(q: Question, value: Any) -> str | list[str]:
    allowed = q.option_values()
    if q.type == "single_choice":
        if not isinstance(value, str) or value not in allowed:
            raise InvalidAnswerError(f"answer must be one of {sorted(allowed)}")
        return value
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        raise InvalidAnswerError("answer must be a non-empty list of options")
    if len(set(value)) != len(value):
        raise InvalidAnswerError("duplicate options in answer")
    unknown = set(value) - allowed
    if unknown:
        raise InvalidAnswerError(f"unknown options: {sorted(unknown)}")
    exclusive = {o.value for o in q.options if o.exclusive}
    if len(value) > 1 and exclusive & set(value):
        raise InvalidAnswerError("an exclusive option cannot be combined with others")
    return list(value)


def _activity(scores: dict[str, float]) -> dict[str, float]:
    ranked = sorted((f for f in scores if scores[f] > 0), key=lambda f: -scores[f])
    top = set(ranked[:3])
    return {f: (1.0 if f in top else 0.5 if scores[f] > 0 else 0.15) for f in scores}


Strategy = Literal["rule_utility", "expected_entropy", "fixed_order"]
TEMPERATURE = 2.0
UNKNOWN_BASELINE = 1.0  # pseudo-score of "none of these / insufficient evidence"
MIN_ENTROPY_GAIN = 0.01


def _entropy(scores: dict[str, float]) -> float:
    """Entropy (nats) of softmax(score/T) over faults plus an 'unknown' outcome.

    A heuristic measure of how spread out the ranking is; NOT a calibrated probability distribution.
    """
    vals = [*scores.values(), UNKNOWN_BASELINE]
    m = max(vals)
    exps = [math.exp((v - m) / TEMPERATURE) for v in vals]
    z = sum(exps)
    return -sum((e / z) * math.log(e / z) for e in exps if e > 0)


def _expected_entropy_gain(kb: KnowledgeBase, device: DeviceProfile, scan: ScanResult | None, answers: Answers,
                           tests: TestResults | None, q: Question, current: float) -> float:
    """Average entropy reduction over the question's options, each option weighted equally.

    Equal weights are an explicit assumption: without validated case data FixMyTech does not know how likely
    each answer is.
    """
    outcomes = [o.value for o in q.options if not (q.type == "multi_choice" and o.exclusive)]
    if not outcomes:
        return 0.0
    total = 0.0
    for value in outcomes:
        sim = {**answers, q.id: [value] if q.type == "multi_choice" else value}
        total += _entropy(fault_scores(kb, build_facts(kb, device, scan, sim, tests)))
    return current - total / len(outcomes)


def _visible_unanswered(kb: KnowledgeBase, values: dict[str, object], answered: set[str], answers: Answers
                        ) -> list[Question]:
    return [q for q in kb.questions.values() if q.id not in answered and q.id not in answers
            and q.is_available(values)]


def next_question(kb: KnowledgeBase, device: DeviceProfile, scan: ScanResult | None, answers: Answers,
                  tests: TestResults | None = None, strategy: Strategy | None = None,
                  more: bool = False) -> NextQuestion:
    """The next most useful question, or why none is needed. ``more`` asks anyway after "no_questions_needed"."""
    settings = kb.question_settings
    strategy = strategy or settings.strategy
    records = build_facts(kb, device, scan, answers, tests)
    values = fact_values(records)
    answered = {k[4:] for k in records if k.startswith("ans.")}
    asked = len(answered)
    budget = settings.max_questions

    skipped = [{"id": q.id, "text": q.text, "reason": q.answered_by_tests_reason or "a hands-on test answered it"}
               for q in kb.questions.values() if q.id not in answered and q.answered_by(values)
               and (q.visible_if is None or conditions.evaluate(q.visible_if, values))]

    def stop(reason: str) -> NextQuestion:
        return NextQuestion(question=None, stop_reason=reason, asked=asked, budget=budget, strategy=strategy,
                            skipped_by_tests=skipped)

    if asked >= budget:
        return stop("budget_reached")
    if any(n.severity == "safety" for r in fired_rules(kb, values) for n in r.notes):
        return stop("safety_concern")

    scores = fault_scores(kb, records)
    if scores.get("liquid_damage", 0) >= 3.0:
        return stop("professional_inspection")
    ranked = sorted(scores, key=lambda f: -scores[f])
    if asked == 0 and not more and strategy != "fixed_order" and len(ranked) >= 2 and tests:
        # Device readings and hands-on tests may already give a strong, clear answer: then no questions are needed.
        leader, runner = ranked[0], ranked[1]
        items = [e for e in deduplicate(fire_rules(kb, records)) if e.fault == leader and e.counted and e.weight > 0]
        objective = all(e.device_based or e.test_based for e in items)
        if (items and objective and scores[leader] - scores[runner] >= settings.confident_margin
                and support_level(scores[leader], len({e.group for e in items})) == SupportLevel.STRONG):
            return stop("no_questions_needed")
    if strategy != "fixed_order" and asked >= settings.min_questions and len(ranked) >= 2:
        leader, runner = ranked[0], ranked[1]
        if scores[leader] - scores[runner] >= settings.confident_margin:
            groups = {e.group for e in deduplicate(fire_rules(kb, records))
                      if e.fault == leader and e.counted and e.weight > 0}
            if support_level(scores[leader], len(groups)) == SupportLevel.STRONG:
                return stop("confident_enough")

    candidates = _visible_unanswered(kb, values, answered, answers)
    if not candidates:
        return stop("no_more_questions")
    activity = _activity(scores)

    if strategy == "fixed_order":
        q = candidates[0]  # definition order: the baseline asks every visible question in a fixed order
        utility = 1.0
    elif strategy == "expected_entropy":
        current = _entropy(scores)
        gains = [(_expected_entropy_gain(kb, device, scan, answers, tests, q, current), q) for q in candidates]
        utility, q = max(gains, key=lambda g: (g[0], g[1].priority))
        if utility < MIN_ENTROPY_GAIN and asked >= settings.min_questions:
            return stop("no_informative_questions")
    else:
        utility, q = max(((q.priority / 100 + sum(activity[t] for t in q.targets), q) for q in candidates),
                         key=lambda g: g[0])
        if utility < settings.min_utility:
            return stop("no_informative_questions")

    leaders = [kb.faults[f].name for f in q.targets if activity[f] == 1.0]
    why = (f"Helps distinguish between: {', '.join(leaders)}." if len(leaders) >= 2
           else f"Checks for: {leaders[0]}." if leaders else "Screens for problems not yet covered.")
    return NextQuestion(
        question=QuestionView(id=q.id, text=q.text, help=q.help, type=q.type,
                              options=[o.model_dump() for o in q.options]),
        stop_reason=None, asked=asked, budget=budget, utility=round(utility, 3), why=why, strategy=strategy,
        skipped_by_tests=skipped,
    )
