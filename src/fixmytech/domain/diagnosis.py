"""Rule inference and evidence fusion.

Pipeline: facts -> fire rules -> de-duplicate correlated evidence per (fault, group) -> rank faults ->
assign a support level -> list contradictions, missing evidence and next steps.

Scores are additive *ranking scores* built from documented rule weights. They are NOT probabilities
and must never be displayed as percentages. Support levels have fixed, documented thresholds
(see ``SUPPORT_THRESHOLDS`` and docs/DIAGNOSTICS.md).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from fixmytech.core.capabilities import CHECKS_BY_ID
from fixmytech.core.observations import ScanResult, utcnow
from fixmytech.domain import conditions
from fixmytech.domain.facts import (
    Answers,
    DeviceProfile,
    FactRecord,
    FactSource,
    TestResults,
    build_facts,
    fact_values,
)
from fixmytech.domain.guided import recommended_tests
from fixmytech.domain.knowledge import KnowledgeBase, Rule

SCORE_INTERPRETATION = (
    "Ranking scores add up documented rule weights for and against each fault. They order the "
    "candidates and are not probabilities or percentages. Support levels: strong = score >= 5 from at "
    "least two independent lines of evidence; moderate = score >= 3; weak = score >= 1."
)


class SupportLevel(StrEnum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    MINIMAL = "minimal"


STRONG_MIN_SCORE = 5.0
STRONG_MIN_GROUPS = 2
MODERATE_MIN_SCORE = 3.0
WEAK_MIN_SCORE = 1.0
CLOSE_CALL_MARGIN = 1.5


class FactRef(BaseModel):
    fact: str
    value: Any
    source: FactSource
    origin: str


class EvidenceItem(BaseModel):
    rule_id: str
    group: str
    fault: str
    weight: float
    rationale: str
    limitations: list[str]
    requires_confirmation: bool
    facts: list[FactRef]
    counted: bool = True

    @property
    def device_based(self) -> bool:
        return any(f.source in (FactSource.DEVICE_MEASURED, FactSource.DEVICE_REPORTED, FactSource.DERIVED)
                   for f in self.facts)

    @property
    def test_based(self) -> bool:
        return any(f.source in (FactSource.USER_OBSERVED, FactSource.PHONE_MEASURED, FactSource.AGENT_MEASURED)
                   for f in self.facts)


class MissingEvidence(BaseModel):
    kind: str  # "check" | "question" | "test"
    ref: str
    title: str
    reason: str


class FaultAssessment(BaseModel):
    fault_id: str
    name: str
    description: str
    score: float
    support_level: SupportLevel
    supporting: list[EvidenceItem]
    contradicting: list[EvidenceItem]
    superseded: list[EvidenceItem]
    has_device_evidence: bool
    has_test_evidence: bool = False
    conflicting: bool
    requires_confirmation: bool
    missing_evidence: list[MissingEvidence]
    repair_types: list[str]
    self_help: list[str]
    suggested_tests: list[str] = []
    ranking_basis: str = ""


class NextStep(BaseModel):
    kind: str  # safety | question | test | self_help | quote | inspection | none
    text: str
    ref: str | None = None


class Note(BaseModel):
    severity: str
    text: str
    rule_id: str


class DiagnosisResult(BaseModel):
    generated_at: datetime
    knowledge_version: str
    outcome: str  # candidates_found | insufficient_evidence | no_fault_indicated
    outcome_summary: str
    candidates: list[FaultAssessment]
    close_call: list[str] | None
    notes: list[Note]
    next_actions: list[str]
    score_interpretation: str
    evidence_counts: dict[str, int]
    unavailable_checks: list[dict[str, str]]
    inactive_answers: list[str]
    remaining_uncertainty: str = ""
    recommended_next_step: NextStep | None = None
    tests_considered: dict[str, str] = {}
    ml: dict[str, Any] | None = None


def fire_rules(kb: KnowledgeBase, records: dict[str, FactRecord]) -> list[EvidenceItem]:
    values = fact_values(records)
    items: list[EvidenceItem] = []
    for rule in kb.rules:
        if not conditions.evaluate(rule.when, values):
            continue
        refs = [FactRef(fact=f, value=records[f].value, source=records[f].source, origin=records[f].origin)
                for f in dict.fromkeys(conditions.referenced_facts(rule.when)) if f in records]
        for eff in rule.effects:
            items.append(_item(rule, eff.fault, eff.weight, refs))
    return items


def _item(rule: Rule, fault: str, weight: float, refs: list[FactRef]) -> EvidenceItem:
    return EvidenceItem(rule_id=rule.id, group=rule.group, fault=fault, weight=weight, rationale=rule.rationale,
                        limitations=rule.limitations, requires_confirmation=rule.requires_confirmation, facts=refs)


def deduplicate(items: list[EvidenceItem]) -> list[EvidenceItem]:
    """Within each (fault, group) keep only the strongest supporting and strongest contradicting item.

    Rules in one group read the same underlying evidence (e.g. three storage thresholds), so letting all
    of them add up would double count.
    """
    best_pos: dict[tuple[str, str], EvidenceItem] = {}
    best_neg: dict[tuple[str, str], EvidenceItem] = {}
    for it in items:
        key = (it.fault, it.group)
        table = best_pos if it.weight > 0 else best_neg
        cur = table.get(key)
        if cur is None or abs(it.weight) > abs(cur.weight):
            table[key] = it
    keep = {id(v) for v in (*best_pos.values(), *best_neg.values())}
    return [it.model_copy(update={"counted": id(it) in keep}) for it in items]


def support_level(score: float, supporting_groups: int) -> SupportLevel:
    if score >= STRONG_MIN_SCORE and supporting_groups >= STRONG_MIN_GROUPS:
        return SupportLevel.STRONG
    if score >= MODERATE_MIN_SCORE:
        return SupportLevel.MODERATE
    if score >= WEAK_MIN_SCORE:
        return SupportLevel.WEAK
    return SupportLevel.MINIMAL


def fault_scores(kb: KnowledgeBase, records: dict[str, FactRecord]) -> dict[str, float]:
    scores: dict[str, float] = dict.fromkeys(kb.faults, 0.0)
    for it in deduplicate(fire_rules(kb, records)):
        if it.counted:
            scores[it.fault] += it.weight
    return scores


def fired_rules(kb: KnowledgeBase, values: dict[str, Any]) -> list[Rule]:
    return [r for r in kb.rules if conditions.evaluate(r.when, values)]


def _missing_for_fault(kb: KnowledgeBase, fault_id: str, scan: ScanResult | None, values: dict[str, Any],
                       answered: set[str], tests_done: set[str] | None = None) -> list[MissingEvidence]:
    missing: list[MissingEvidence] = []
    obs = scan.by_check() if scan else {}
    for check_id in kb.faults[fault_id].related_checks:
        o = obs.get(check_id)
        title = CHECKS_BY_ID[check_id].title
        if o is None:
            missing.append(MissingEvidence(kind="check", ref=check_id, title=title,
                                           reason="No USB scan data" if scan is None else "Check not run"))
        elif not o.usable:
            missing.append(MissingEvidence(kind="check", ref=check_id, title=title,
                                           reason=f"Check {o.status.value.replace('_', ' ')}"))
    for q in kb.questions.values():
        if fault_id in q.targets and q.id not in answered and q.is_available(values):
            missing.append(MissingEvidence(kind="question", ref=q.id, title=q.text, reason="Not answered yet"))
    for tid in recommended_tests(kb, [fault_id], tests_done or set()):
        missing.append(MissingEvidence(kind="test", ref=tid, title=kb.tests[tid].title, reason="Test not done yet"))
    return missing


def _ranking_basis(c_supporting: list[EvidenceItem], c_contra: list[EvidenceItem], score: float,
                   level: SupportLevel) -> str:
    kinds = []
    if any(e.device_based for e in c_supporting):
        kinds.append("phone readings")
    if any(e.test_based for e in c_supporting):
        kinds.append("hands-on tests")
    if any(any(f.source == FactSource.CUSTOMER_REPORTED for f in e.facts) for e in c_supporting):
        kinds.append("your answers")
    groups = len({e.group for e in c_supporting})
    text = (f"Ranked {level.value} from {groups} independent line(s) of evidence"
            + (f" ({', '.join(kinds)})" if kinds else "") + f"; ranking score {score}.")
    if c_contra:
        text += f" {len(c_contra)} item(s) point the other way."
    return text


def diagnose(kb: KnowledgeBase, device: DeviceProfile, scan: ScanResult | None, answers: Answers,
             ml_output: dict[str, Any] | None = None, tests: TestResults | None = None) -> DiagnosisResult:
    records = build_facts(kb, device, scan, answers, tests)
    values = fact_values(records)
    answered = {k[4:] for k in records if k.startswith("ans.")}
    tests_done = {tid for tid, r in (tests or {}).items() if r.get("outcome") != "not_run"}
    items = deduplicate(fire_rules(kb, records))

    by_fault: dict[str, list[EvidenceItem]] = defaultdict(list)
    for it in items:
        by_fault[it.fault].append(it)

    candidates: list[FaultAssessment] = []
    for fault_id, fault in kb.faults.items():
        evid = by_fault.get(fault_id, [])
        counted = [e for e in evid if e.counted]
        if not counted:
            continue
        supporting = sorted((e for e in counted if e.weight > 0), key=lambda e: -e.weight)
        contradicting = sorted((e for e in counted if e.weight < 0), key=lambda e: e.weight)
        score = round(sum(e.weight for e in counted), 2)
        level = support_level(score, len({e.group for e in supporting}))
        candidates.append(FaultAssessment(
            fault_id=fault_id,
            name=fault.name,
            description=fault.description,
            score=score,
            support_level=level,
            supporting=supporting,
            contradicting=contradicting,
            superseded=[e for e in evid if not e.counted],
            has_device_evidence=any(e.device_based for e in supporting),
            has_test_evidence=any(e.test_based for e in supporting),
            conflicting=bool(supporting) and any(e.weight <= -1.0 for e in contradicting),
            requires_confirmation=any(e.requires_confirmation for e in supporting),
            missing_evidence=_missing_for_fault(kb, fault_id, scan, values, answered, tests_done),
            repair_types=fault.repair_types,
            self_help=fault.self_help,
            suggested_tests=recommended_tests(kb, [fault_id], tests_done) if score > 0 else [],
            ranking_basis=_ranking_basis(supporting, contradicting, score, level),
        ))
    candidates.sort(key=lambda c: (-c.score, c.fault_id))

    notes: list[Note] = []
    seen_notes: set[str] = set()
    fired = fired_rules(kb, values)
    fired_rule_ids = [r.id for r in fired]
    for rule in fired:
        for n in rule.notes:
            if n.text not in seen_notes:
                seen_notes.add(n.text)
                notes.append(Note(severity=n.severity, text=n.text, rule_id=rule.id))
    notes.sort(key=lambda n: n.severity != "safety")

    significant = [c for c in candidates if c.support_level in (SupportLevel.STRONG, SupportLevel.MODERATE)]
    close_call = None
    if len(significant) >= 2 and significant[0].score - significant[1].score < CLOSE_CALL_MARGIN:
        close_call = [significant[0].fault_id, significant[1].fault_id]

    general_checkup = values.get("ans.primary_symptoms") == ["none"]
    if significant:
        outcome = "candidates_found"
        summary = f"The evidence most supports: {significant[0].name} ({significant[0].support_level.value} support)."
        if close_call:
            summary = (f"The evidence does not clearly separate {kb.faults[close_call[0]].name} and "
                       f"{kb.faults[close_call[1]].name}.")
    elif general_checkup and not any(c.support_level == SupportLevel.WEAK for c in candidates):
        outcome = "no_fault_indicated"
        summary = ("No fault was indicated by the available checks and answers. This is not proof that the phone "
                   "is free of faults: several components cannot be tested over USB.")
    else:
        outcome = "insufficient_evidence"
        summary = ("There is not enough evidence to identify a likely fault. More answers, additional checks, or a "
                   "professional inspection are needed.")

    unavailable = []
    if scan:
        for o in scan.observations:
            if not o.usable:
                unavailable.append({"check_id": o.check_id, "title": CHECKS_BY_ID[o.check_id].title,
                                    "status": o.status.value, "detail": o.detail or ""})

    counts = {"device_facts": 0, "customer_answers": 0, "test_results": len(tests_done),
              "rules_fired": len(fired_rule_ids)}
    for r in records.values():
        if r.source in (FactSource.DEVICE_MEASURED, FactSource.DEVICE_REPORTED):
            counts["device_facts"] += 1
        elif r.source == FactSource.CUSTOMER_REPORTED:
            counts["customer_answers"] += 1
    next_step = _next_step(kb, outcome, significant, candidates, notes, values, answered, tests_done)

    return DiagnosisResult(
        generated_at=utcnow(),
        knowledge_version=kb.version,
        outcome=outcome,
        outcome_summary=summary,
        candidates=candidates,
        close_call=close_call,
        notes=notes,
        next_actions=_next_actions(kb, outcome, significant, notes, close_call),
        score_interpretation=SCORE_INTERPRETATION,
        evidence_counts=counts,
        unavailable_checks=unavailable,
        inactive_answers=sorted(set(answers) - answered),
        remaining_uncertainty=_uncertainty_text(outcome, significant, close_call, candidates),
        recommended_next_step=next_step,
        tests_considered={tid: (tests or {})[tid]["outcome"] for tid in (tests or {})},
        ml=ml_output,
    )


def _uncertainty_text(outcome: str, significant: list[FaultAssessment], close_call: list[str] | None,
                      candidates: list[FaultAssessment]) -> str:
    if outcome == "insufficient_evidence":
        return "High: no explanation is well supported yet."
    if outcome == "no_fault_indicated":
        return "Unknown for parts that cannot be tested remotely; nothing points to a fault in what was checked."
    top = significant[0]
    parts = []
    if close_call:
        parts.append("two explanations are almost equally supported")
    if top.support_level != SupportLevel.STRONG:
        parts.append("the leading explanation is only moderately supported")
    if top.conflicting:
        parts.append("some evidence contradicts it")
    if not top.has_device_evidence and not top.has_test_evidence:
        parts.append("it rests on your answers alone")
    if top.requires_confirmation:
        parts.append("a technician should confirm it before parts are replaced")
    others = [c for c in candidates[1:] if c.score > 0]
    if others and not close_call:
        parts.append(f"{len(others)} other explanation(s) have some support")
    if not parts:
        return "Lower: one explanation is strongly and consistently supported."
    return "Moderate to high: " + "; ".join(parts) + "."


def _next_step(kb: KnowledgeBase, outcome: str, significant: list[FaultAssessment], candidates: list[FaultAssessment],
               notes: list[Note], values: dict[str, Any], answered: set[str], tests_done: set[str]) -> NextStep:
    """One recommendation: safety > discriminating test > unanswered question > self-help > quote > inspection."""
    safety = next((n for n in notes if n.severity == "safety"), None)
    if safety:
        return NextStep(kind="safety", text=safety.text)
    in_play = [c for c in candidates if c.score > 0][:3]
    for c in in_play:
        if c.suggested_tests:
            t = kb.tests[c.suggested_tests[0]]
            return NextStep(kind="test", ref=t.id,
                            text=f"Run the '{t.title}' test to confirm or rule out {c.name.lower()}.")
    for q in sorted(kb.questions.values(), key=lambda q: -q.priority):
        if q.id not in answered and any(c.fault_id in q.targets for c in in_play) and q.is_available(values):
            return NextStep(kind="question", ref=q.id, text=f"Answer: {q.text}")
    if outcome == "insufficient_evidence":
        return NextStep(kind="inspection", text="Ask a repair technician for a bench diagnosis; remote checks have "
                                                "not found a clear cause.")
    if outcome == "no_fault_indicated":
        return NextStep(kind="none", text="No action needed now.")
    top = significant[0]
    if top.fault_id in ("software_instability", "storage_pressure"):
        return NextStep(kind="self_help", text=f"Try the free self-help steps for {top.name.lower()} first.")
    if top.requires_confirmation or top.support_level != SupportLevel.STRONG:
        return NextStep(kind="inspection",
                        text=f"Have a technician confirm the {top.name.lower()} and quote the repair.")
    return NextStep(kind="quote", text="Get a repair quote and compare it with the reference price.")


def _next_actions(kb: KnowledgeBase, outcome: str, significant: list[FaultAssessment], notes: list[Note],
                  close_call: list[str] | None) -> list[str]:
    actions: list[str] = []
    if any(n.severity == "safety" for n in notes):
        actions.append("Follow the safety advice above before anything else.")
    if outcome == "insufficient_evidence":
        actions.append("Answer the remaining questions, or run a USB scan if you have not.")
        actions.append("If the problem continues, ask a repair technician for a bench diagnosis.")
        return actions
    if outcome == "no_fault_indicated":
        actions.append("No action is needed based on this check-up. Re-run the diagnosis if symptoms appear.")
        return actions
    top = significant[0]
    if close_call:
        actions.append("Ask a technician to confirm which of the closely-ranked faults is present "
                       "before paying for parts.")
    if top.fault_id in ("software_instability", "storage_pressure"):
        actions.append(f"Try the self-help steps for '{top.name}' first; they are free and often resolve the issue.")
    elif top.repair_types:
        repair = kb.repair_types[top.repair_types[0]].name.lower()
        if top.requires_confirmation or top.support_level != SupportLevel.STRONG:
            actions.append(f"Get a quote for a {repair}, and ask the technician to confirm the fault "
                           "before replacing parts.")
        else:
            actions.append(f"Get a quote for a {repair}.")
    actions.append("Compare the repair cost against selling or replacing the phone in the Decision step.")
    return actions
