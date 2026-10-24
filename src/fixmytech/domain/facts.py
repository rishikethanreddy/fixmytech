"""Turn observations, answers and device details into a flat fact map with provenance.

Fact namespaces:
* ``obs.<check_id>.<field>``  value from a usable (status=ok) observation
* ``obs_status.<check_id>``   collection status of a check (present for every check that was attempted)
* ``ans.<question_id>``       customer answer (only answers to currently-visible questions)
* ``device.<field>``          customer-provided device details
* ``derived.<name>``          computed by FixMyTech from other facts
* ``test.<test_id>.outcome``  guided-test outcome (pass/partial/fail); ``not_run`` produces no outcome fact
* ``test.<test_id>.<measure>`` numeric result measured by the phone browser or the agent
* ``test_status.<test_id>``   pass/partial/fail/not_run
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from fixmytech.core.observations import ScanResult
from fixmytech.domain import conditions
from fixmytech.domain.knowledge import KnowledgeBase


class FactSource(StrEnum):
    DEVICE_MEASURED = "device_measured"
    DEVICE_REPORTED = "device_reported"
    CUSTOMER_REPORTED = "customer_reported"
    CUSTOMER_PROVIDED = "customer_provided"  # device details typed by the customer
    DERIVED = "derived"
    USER_OBSERVED = "user_observed"  # customer's judgement during a guided test
    PHONE_MEASURED = "phone_measured"  # measured by the phone's browser during a guided test
    AGENT_MEASURED = "agent_measured"  # measured by the local agent during a guided test


class DeviceProfile(BaseModel):
    manufacturer: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    storage_gb: int | None = Field(default=None, ge=1, le=4096)
    age_months: int | None = Field(default=None, ge=0, le=240)
    region: str = Field(default="US", pattern=r"^[A-Z]{2}$")


@dataclass(frozen=True)
class FactRecord:
    value: Any
    source: FactSource
    origin: str  # check id, question id or field name
    evidence_kind: str | None = None


Answers = dict[str, str | list[str]]
TestResults = dict[str, dict[str, Any]]  # stored TestResult dumps keyed by test id


def _device_facts(device: DeviceProfile) -> dict[str, FactRecord]:
    out: dict[str, FactRecord] = {}
    for key in ("manufacturer", "model", "storage_gb", "age_months"):
        v = getattr(device, key)
        if v is not None:
            out[f"device.{key}"] = FactRecord(v, FactSource.CUSTOMER_PROVIDED, key)
    return out


def _scan_facts(scan: ScanResult | None) -> dict[str, FactRecord]:
    out: dict[str, FactRecord] = {}
    if scan is None:
        return out
    for o in scan.observations:
        out[f"obs_status.{o.check_id}"] = FactRecord(o.status.value, FactSource.DEVICE_REPORTED, o.check_id)
        if not o.usable:
            continue
        src = FactSource.DEVICE_MEASURED if o.evidence_kind.value == "measured" else FactSource.DEVICE_REPORTED
        for k, v in o.value.items():
            if v is not None:
                out[f"obs.{o.check_id}.{k}"] = FactRecord(v, src, o.check_id, o.evidence_kind.value)
    crashes = scan.by_check().get("stability.crashes")
    if crashes and crashes.usable:
        total = sum(int(crashes.value.get(k) or 0) for k in ("app_crashes", "app_anrs", "app_native_crashes"))
        out["derived.app_instability_7d"] = FactRecord(total, FactSource.DERIVED, "stability.crashes", "inferred")
    return out


def active_answers(kb: KnowledgeBase, base: dict[str, Any], answers: Answers) -> Answers:
    """Answers whose questions are visible given everything else; stale answers drop out.

    Visibility can depend on other answers, so iterate to a fixed point.
    """
    active: Answers = {}
    for _ in range(len(answers) + 1):
        facts = {**base, **{f"ans.{k}": v for k, v in active.items()}}
        nxt: Answers = {}
        for qid, value in answers.items():
            q = kb.questions.get(qid)
            if q is None:
                continue
            if q.visible_if is None or conditions.evaluate(q.visible_if, facts):
                nxt[qid] = value
        if nxt == active:
            break
        active = nxt
    return active


def _test_facts(tests: TestResults | None) -> dict[str, FactRecord]:
    out: dict[str, FactRecord] = {}
    for tid, r in (tests or {}).items():
        out[f"test_status.{tid}"] = FactRecord(r["outcome"], FactSource(r["outcome_kind"]), tid)
        if r["outcome"] == "not_run":
            continue
        out[f"test.{tid}.outcome"] = FactRecord(r["outcome"], FactSource(r["outcome_kind"]), tid)
        for k, v in (r.get("measures") or {}).items():
            if v is not None:
                out[f"test.{tid}.{k}"] = FactRecord(v, FactSource(r["measures_kind"]), tid)
    return out


def build_facts(kb: KnowledgeBase, device: DeviceProfile, scan: ScanResult | None,
                answers: Answers, tests: TestResults | None = None) -> dict[str, FactRecord]:
    records = {**_device_facts(device), **_scan_facts(scan), **_test_facts(tests)}
    base = {k: r.value for k, r in records.items()}
    for qid, value in active_answers(kb, base, answers).items():
        records[f"ans.{qid}"] = FactRecord(value, FactSource.CUSTOMER_REPORTED, qid)
    return records


def fact_values(records: dict[str, FactRecord]) -> dict[str, Any]:
    return {k: r.value for k, r in records.items()}
