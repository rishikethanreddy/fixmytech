"""Loading and validation of the diagnostic knowledge base (taxonomy, rules, questions).

The knowledge base is data, not code: new rules and questions are added in ``fixmytech/data/*.json``.
``load_knowledge_base`` validates cross-references so a typo in a fault id or question id fails at
startup and in tests rather than silently never firing.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fixmytech.core.capabilities import CHECKS_BY_ID
from fixmytech.domain import conditions

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Facts computed in fixmytech.domain.facts (not directly observed).
DERIVED_FACTS = {"derived.app_instability_7d"}
DEVICE_FACTS = {"device.age_months", "device.manufacturer", "device.model", "device.storage_gb"}


class Fault(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    description: str
    repair_types: list[str]
    components: list[str] = []
    self_help: list[str]
    related_checks: list[str]


class RepairType(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    professional: bool


class Effect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fault: str
    weight: float = Field(ge=-5, le=5)


class Note(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Literal["info", "safety"]
    text: str


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    group: str
    when: Any
    effects: list[Effect] = Field(default_factory=list)
    rationale: str
    limitations: list[str] = []
    requires_confirmation: bool = False
    notes: list[Note] = []

    @model_validator(mode="after")
    def _check_condition(self) -> Rule:
        conditions.validate(self.when)
        if not self.effects and not self.notes:
            raise ValueError(f"rule {self.id} has neither effects nor notes")
        return self


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str
    exclusive: bool = False


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    text: str
    help: str | None = None
    type: Literal["single_choice", "multi_choice"]
    priority: int = Field(ge=0, le=100)
    targets: list[str] = Field(min_length=1)
    options: list[Option] = Field(min_length=2)
    visible_if: Any = None
    # Skip the question when hands-on test results already answer it (condition over test.* facts).
    answered_by_tests: Any = None
    answered_by_tests_reason: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Question:
        if self.visible_if is not None:
            conditions.validate(self.visible_if)
        if self.answered_by_tests is not None:
            conditions.validate(self.answered_by_tests)
        if len({o.value for o in self.options}) != len(self.options):
            raise ValueError(f"duplicate option values in {self.id}")
        return self

    def option_values(self) -> set[str]:
        return {o.value for o in self.options}

    def answered_by(self, values: dict[str, Any]) -> bool:
        """True when hands-on test results already answer this question."""
        return self.answered_by_tests is not None and conditions.evaluate(self.answered_by_tests, values)

    def is_available(self, values: dict[str, Any]) -> bool:
        """Visible given the current facts, and not already answered by a hands-on test."""
        visible = self.visible_if is None or conditions.evaluate(self.visible_if, values)
        return visible and not self.answered_by(values)


class TestOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Literal["pass", "partial", "fail"]
    label: str


class GuidedTest(BaseModel):
    """A hands-on test. Results are evidence of type phone_measured / agent_measured / user_observed."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    title: str
    category: str
    method: Literal["phone_browser", "agent_assisted", "manual"]
    summary: str
    instructions: list[str]
    requires: list[Literal["touch", "webaudio", "microphone", "camera", "vibrate", "motion", "secure_context",
                           "agent"]]
    measures: list[str]
    outcomes: list[TestOutcome] = Field(min_length=2)
    targets: list[str]


class QuestionSettings(BaseModel):
    strategy: Literal["rule_utility", "expected_entropy", "fixed_order"] = "rule_utility"
    max_questions: int = Field(ge=1, le=50)
    min_questions: int = Field(ge=0, le=50)
    confident_margin: float = Field(gt=0)
    min_utility: float = Field(ge=0)


class KnowledgeBase(BaseModel):
    taxonomy_version: str
    rules_version: str
    questions_version: str
    faults: dict[str, Fault]
    repair_types: dict[str, RepairType]
    rules: list[Rule]
    questions: dict[str, Question]
    question_settings: QuestionSettings
    tests: dict[str, GuidedTest] = {}
    tests_version: str = ""

    @property
    def version(self) -> str:
        return (f"taxonomy={self.taxonomy_version};rules={self.rules_version};questions={self.questions_version};"
                f"tests={self.tests_version}")


def _check_fact_reference(fact: str, questions: dict[str, Question], where: str,
                          tests: dict[str, GuidedTest] | None = None) -> None:
    if fact.startswith("test."):
        _, tid, field = (fact.split(".", 2) + [""])[:3]
        test = (tests or {}).get(tid)
        if test is None or field not in ("outcome", *test.measures):
            raise ValueError(f"{where}: unknown test or test field in fact {fact!r}")
    elif fact.startswith("ans."):
        if fact[4:] not in questions:
            raise ValueError(f"{where}: unknown question in fact {fact!r}")
    elif fact.startswith("test_status."):
        if fact[len("test_status."):] not in (tests or {}):
            raise ValueError(f"{where}: unknown test in fact {fact!r}")
    elif fact.startswith("obs_status."):
        if fact[len("obs_status."):] not in CHECKS_BY_ID:
            raise ValueError(f"{where}: unknown check in fact {fact!r}")
    elif fact.startswith("obs."):
        if not any(fact[4:].startswith(cid + ".") for cid in CHECKS_BY_ID):
            raise ValueError(f"{where}: unknown check in fact {fact!r}")
    elif fact not in DERIVED_FACTS | DEVICE_FACTS:
        raise ValueError(f"{where}: unknown fact namespace {fact!r}")


def load_knowledge_base(data_dir: Path = DATA_DIR) -> KnowledgeBase:
    tax: dict[str, Any] = json.loads((data_dir / "taxonomy.json").read_text(encoding="utf-8"))
    rules_doc: dict[str, Any] = json.loads((data_dir / "rules.json").read_text(encoding="utf-8"))
    q_doc: dict[str, Any] = json.loads((data_dir / "questions.json").read_text(encoding="utf-8"))
    t_path = data_dir / "guided_tests.json"
    t_doc: dict[str, Any] = (json.loads(t_path.read_text(encoding="utf-8")) if t_path.is_file()
                             else {"version": "", "tests": []})
    tests = {t["id"]: GuidedTest(**t) for t in t_doc["tests"]}

    faults = {f["id"]: Fault(**f) for f in tax["faults"]}
    repair_types = {r["id"]: RepairType(**r) for r in tax["repair_types"]}
    rules = [Rule(**r) for r in rules_doc["rules"]]
    questions = {q["id"]: Question(**q) for q in q_doc["questions"]}

    for f in faults.values():
        for rt in f.repair_types:
            if rt not in repair_types:
                raise ValueError(f"fault {f.id}: unknown repair type {rt}")
        for c in f.related_checks:
            if c not in CHECKS_BY_ID:
                raise ValueError(f"fault {f.id}: unknown check {c}")
    if len({r.id for r in rules}) != len(rules):
        raise ValueError("duplicate rule ids")
    for r in rules:
        for e in r.effects:
            if e.fault not in faults:
                raise ValueError(f"rule {r.id}: unknown fault {e.fault}")
        for fact in conditions.referenced_facts(r.when):
            _check_fact_reference(fact, questions, f"rule {r.id}", tests)
    for gt in tests.values():
        for t in gt.targets:
            if t not in faults:
                raise ValueError(f"test {gt.id}: unknown target fault {t}")
    for q in questions.values():
        for t in q.targets:
            if t not in faults:
                raise ValueError(f"question {q.id}: unknown target fault {t}")
        for cond in (q.visible_if, q.answered_by_tests):
            if cond is not None:
                for fact in conditions.referenced_facts(cond):
                    _check_fact_reference(fact, questions, f"question {q.id}", tests)

    return KnowledgeBase(
        taxonomy_version=tax["version"],
        rules_version=rules_doc["version"],
        questions_version=q_doc["version"],
        faults=faults,
        repair_types=repair_types,
        rules=rules,
        questions=questions,
        question_settings=QuestionSettings(**q_doc["settings"]),
        tests=tests,
        tests_version=t_doc["version"],
    )


@lru_cache(maxsize=1)
def default_knowledge_base() -> KnowledgeBase:
    return load_knowledge_base()
