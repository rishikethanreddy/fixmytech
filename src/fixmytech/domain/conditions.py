"""A tiny, declarative condition language used by rules and question visibility.

A condition is either
* a triple ``[fact, op, value]``, e.g. ``["obs.storage.data.free_pct", "lt", 10]``
* ``{"all": [cond, ...]}``, ``{"any": [cond, ...]}`` or ``{"not": cond}``.

Semantics for missing facts: every operator is False when the fact is missing, except ``missing``
(True) and ``ne`` (True: a missing value is "not equal" to anything). This means an unavailable
check never accidentally satisfies a rule.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

OPS = {"eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "exists", "missing", "contains", "contains_any"}

_MISSING = object()


def _cmp(op: str, actual: Any, expected: Any) -> bool:
    if op == "eq":
        return bool(actual == expected)
    if op == "ne":
        return bool(actual != expected)
    if op == "in":
        return actual in expected
    if op == "not_in":
        return actual not in expected
    if op in ("gt", "gte", "lt", "lte"):
        if isinstance(actual, bool) or not isinstance(actual, int | float):
            return False
        return {"gt": actual > expected, "gte": actual >= expected, "lt": actual < expected,
                "lte": actual <= expected}[op]
    if op == "contains":
        return isinstance(actual, list | tuple | set) and expected in actual
    if op == "contains_any":
        return isinstance(actual, list | tuple | set) and any(e in actual for e in expected)
    if op == "exists":
        return True
    raise ValueError(f"unknown operator {op!r}")


def evaluate(cond: Any, facts: Mapping[str, Any]) -> bool:
    if isinstance(cond, list):
        fact, op, *rest = cond
        expected = rest[0] if rest else None
        actual = facts.get(fact, _MISSING)
        if actual is _MISSING or actual is None:
            return op in ("missing", "ne")
        if op == "missing":
            return False
        return _cmp(op, actual, expected)
    if isinstance(cond, dict):
        if "all" in cond:
            return all(evaluate(c, facts) for c in cond["all"])
        if "any" in cond:
            return any(evaluate(c, facts) for c in cond["any"])
        if "not" in cond:
            return not evaluate(cond["not"], facts)
    raise ValueError(f"malformed condition: {cond!r}")


def referenced_facts(cond: Any) -> Iterator[str]:
    if isinstance(cond, list):
        yield cond[0]
    elif isinstance(cond, dict):
        for key in ("all", "any"):
            for c in cond.get(key, []):
                yield from referenced_facts(c)
        if "not" in cond:
            yield from referenced_facts(cond["not"])


def validate(cond: Any) -> None:
    if isinstance(cond, list):
        if len(cond) not in (2, 3) or not isinstance(cond[0], str) or cond[1] not in OPS:
            raise ValueError(f"malformed condition triple: {cond!r}")
        if cond[1] in ("in", "not_in", "contains_any") and not isinstance(cond[2], list):
            raise ValueError(f"operator {cond[1]} needs a list: {cond!r}")
        return
    if isinstance(cond, dict) and len(cond) == 1:
        key = next(iter(cond))
        if key in ("all", "any") and isinstance(cond[key], list) and cond[key]:
            for c in cond[key]:
                validate(c)
            return
        if key == "not":
            validate(cond["not"])
            return
    raise ValueError(f"malformed condition: {cond!r}")
