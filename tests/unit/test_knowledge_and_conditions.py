import json
import shutil

import pytest

from fixmytech.domain import conditions
from fixmytech.domain.knowledge import DATA_DIR, load_knowledge_base


def test_knowledge_base_loads_and_cross_references():
    kb = load_knowledge_base()
    assert len(kb.faults) >= 8 and len(kb.rules) >= 50 and len(kb.questions) >= 20
    assert "primary_symptoms" in kb.questions


def test_no_rule_asserts_definite_fault_from_single_customer_symptom():
    """A bare symptom-area selection must stay weak evidence (weight <= 1)."""
    kb = load_knowledge_base()
    for r in kb.rules:
        facts = list(conditions.referenced_facts(r.when))
        if facts == ["ans.primary_symptoms"]:
            assert all(e.weight <= 1.0 for e in r.effects), r.id


def _broken_kb(tmp_path, mutate):
    for name in ("taxonomy.json", "rules.json", "questions.json"):
        shutil.copy(DATA_DIR / name, tmp_path / name)
    doc = json.loads((tmp_path / "rules.json").read_text())
    mutate(doc)
    (tmp_path / "rules.json").write_text(json.dumps(doc))
    return tmp_path


def test_unknown_fault_in_rule_is_rejected(tmp_path):
    d = _broken_kb(tmp_path, lambda doc: doc["rules"][0]["effects"].append({"fault": "nope", "weight": 1}))
    with pytest.raises(ValueError, match="unknown fault"):
        load_knowledge_base(d)


def test_unknown_question_in_rule_is_rejected(tmp_path):
    d = _broken_kb(tmp_path, lambda doc: doc["rules"][0].update(when=["ans.not_a_question", "eq", "x"]))
    with pytest.raises(ValueError, match="unknown question"):
        load_knowledge_base(d)


def test_unknown_check_in_rule_is_rejected(tmp_path):
    d = _broken_kb(tmp_path, lambda doc: doc["rules"][0].update(when=["obs.fake.check.x", "eq", 1]))
    with pytest.raises(ValueError, match="unknown check"):
        load_knowledge_base(d)


@pytest.mark.parametrize("cond,facts,expected", [
    (["a", "eq", 1], {"a": 1}, True),
    (["a", "eq", 1], {}, False),
    (["a", "ne", 1], {}, True),
    (["a", "lt", 5], {"a": True}, False),  # booleans are not numbers
    (["a", "gte", 5], {"a": 5}, True),
    (["a", "in", [1, 2]], {"a": 2}, True),
    (["a", "contains", "x"], {"a": ["x", "y"]}, True),
    (["a", "contains_any", ["z", "y"]], {"a": ["x", "y"]}, True),
    (["a", "contains", "x"], {"a": "x"}, False),
    (["a", "missing"], {}, True),
    (["a", "exists"], {"a": 0}, True),
    ({"all": [["a", "eq", 1], ["b", "eq", 2]]}, {"a": 1, "b": 2}, True),
    ({"any": [["a", "eq", 9], ["b", "eq", 2]]}, {"a": 1, "b": 2}, True),
    ({"not": ["a", "eq", 1]}, {"a": 1}, False),
])
def test_condition_semantics(cond, facts, expected):
    assert conditions.evaluate(cond, facts) is expected


def test_condition_validation():
    with pytest.raises(ValueError):
        conditions.validate(["a", "bogus", 1])
    with pytest.raises(ValueError):
        conditions.validate(["a", "in", "notalist"])
    with pytest.raises(ValueError):
        conditions.validate({"all": []})
