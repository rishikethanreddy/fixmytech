import json

import pytest

from fixmytech.domain.diagnosis import diagnose
from fixmytech.domain.facts import DeviceProfile
from fixmytech.domain.knowledge import default_knowledge_base
from fixmytech.ml import synthetic, train
from fixmytech.ml.features import facts_to_row, feature_names
from fixmytech.ml.inference import FaultModel, load_model, ml_opinion


@pytest.fixture(scope="module")
def synthetic_model(tmp_path_factory):
    d = tmp_path_factory.mktemp("ml")
    csv_path = d / "syn.csv"
    synthetic.write_csv(synthetic.generate(300), csv_path)
    meta = train.train(csv_path, d / "model", folds=3)
    return d / "model", meta


def test_synthetic_training_is_never_marked_validated(synthetic_model):
    _, meta = synthetic_model
    assert meta["data_provenance"] == "synthetic"
    assert meta["validated"] is False
    ev = meta["evaluation"]
    assert ev["protocol"].startswith("GroupKFold")
    assert "baseline_most_frequent" in ev and ev["reliability"]


def test_model_output_is_separate_and_labelled(synthetic_model):
    model_dir, _ = synthetic_model
    out = ml_opinion(str(model_dir), True, {"ans.primary_symptoms": ["battery_drain"]})
    assert out["status"] == "experimental_unvalidated"
    assert out["used_in_ranking"] is False
    assert out["score_label"] == "uncalibrated model score"
    kb = default_knowledge_base()
    d_with = diagnose(kb, DeviceProfile(), None, {"primary_symptoms": ["battery_drain"]}, out)
    d_without = diagnose(kb, DeviceProfile(), None, {"primary_symptoms": ["battery_drain"]})
    assert [c.score for c in d_with.candidates] == [c.score for c in d_without.candidates]


def test_disabled_and_missing_models():
    assert ml_opinion(None, False, {}) is None
    assert ml_opinion("does/not/exist", True, {})["status"] == "unavailable"


def test_feature_schema_mismatch_rejected(synthetic_model, tmp_path):
    model_dir, _ = synthetic_model
    meta = json.loads((model_dir / "metadata.json").read_text())
    meta["features"] = meta["features"][:-1]
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "metadata.json").write_text(json.dumps(meta))
    (bad / "model.joblib").write_bytes((model_dir / "model.joblib").read_bytes())
    with pytest.raises(ValueError):
        FaultModel.load(bad)
    load_model.cache_clear()


def test_dataset_validation(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("case_id,label_fault\n1,battery_wear\n")
    with pytest.raises(ValueError, match="missing columns"):
        train.load_dataset(p)


def test_facts_to_row_handles_missing():
    row = facts_to_row({})
    assert set(row) == set(feature_names())
    assert row["battery_capacity_pct"] != row["battery_capacity_pct"]  # NaN
