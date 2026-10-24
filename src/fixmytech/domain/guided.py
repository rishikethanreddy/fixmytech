"""Guided hardware tests: result validation, provenance, and recommendations.

Provenance rules:
* ``outcome`` of a phone-browser or manual test is the customer's judgement -> ``user_observed``.
* Numeric ``measures`` recorded by the phone's browser -> ``phone_measured``; by the local agent ->
  ``agent_measured`` (the agent-assisted test's outcome is also computed from its measurements).
* ``not_run`` (unsupported browser, permission denied, skipped, no such hardware) is recorded but never
  becomes a pass/fail fact.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from fixmytech.core.observations import utcnow
from fixmytech.domain.knowledge import GuidedTest, KnowledgeBase

Outcome = Literal["pass", "partial", "fail", "not_run"]
NotRunReason = Literal["unsupported", "permission_denied", "skipped", "hardware_absent", "inconclusive", "error"]
RecordedVia = Literal["phone", "main", "agent"]


class InvalidTestResultError(ValueError):
    pass


class TestResultInput(BaseModel):
    outcome: Outcome
    not_run_reason: NotRunReason | None = None
    measures: dict[str, float | int | bool | str | None] = Field(default_factory=dict)
    detail: str | None = Field(default=None, max_length=300)

    @field_validator("measures")
    @classmethod
    def _bounded(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > 20:
            raise ValueError("too many measures")
        for k, x in v.items():
            if isinstance(x, str) and len(x) > 100:
                raise ValueError(f"measure {k} too long")
            if isinstance(x, float) and (x != x or abs(x) > 1e9):
                raise ValueError(f"measure {k} out of range")
        return v


class TestResult(TestResultInput):
    test_id: str
    recorded_at: datetime
    recorded_via: RecordedVia
    outcome_kind: Literal["user_observed", "agent_measured"]
    measures_kind: Literal["phone_measured", "agent_measured", "user_observed"]


def validate_result(test: GuidedTest, data: TestResultInput, via: RecordedVia) -> TestResult:
    if via == "phone" and test.method != "phone_browser":
        raise InvalidTestResultError("this test cannot be submitted from the phone page")
    if via == "agent" and test.method != "agent_assisted":
        raise InvalidTestResultError("this test is not agent-assisted")
    if via == "main" and test.method == "agent_assisted" and data.outcome != "not_run":
        raise InvalidTestResultError("agent-assisted results must come from the agent measurement")
    if data.outcome == "not_run":
        if data.not_run_reason is None:
            raise InvalidTestResultError("not_run needs a reason")
    else:
        allowed = {o.value for o in test.outcomes}
        if data.outcome not in allowed:
            raise InvalidTestResultError(f"outcome must be one of {sorted(allowed)} or not_run")
        if data.not_run_reason is not None:
            raise InvalidTestResultError("not_run_reason only applies to not_run")
    unknown = set(data.measures) - set(test.measures)
    if unknown:
        raise InvalidTestResultError(f"unknown measures: {sorted(unknown)}")
    agent = via == "agent"
    return TestResult(**data.model_dump(), test_id=test.id, recorded_at=utcnow(), recorded_via=via,
                      outcome_kind="agent_measured" if agent else "user_observed",
                      measures_kind="agent_measured" if agent else ("phone_measured" if via == "phone"
                                                                    else "user_observed"))


def recommended_tests(kb: KnowledgeBase, fault_ids: list[str], done: set[str]) -> list[str]:
    """Tests that target the given faults and have not produced a pass/fail/partial result yet."""
    out: list[str] = []
    for fid in fault_ids:
        for t in kb.tests.values():
            if fid in t.targets and t.id not in done and t.id not in out:
                out.append(t.id)
    return out
