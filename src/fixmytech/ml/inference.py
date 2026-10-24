"""Load an optional trained model and produce a clearly-labelled, separate model opinion.

The output is attached to a diagnosis under ``ml`` and is never merged into the rule-based ranking.
Probabilities are reported as uncalibrated model scores unless calibration was evaluated.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from fixmytech.ml.features import facts_to_row, feature_names

log = logging.getLogger("fixmytech.ml")


class FaultModel:
    def __init__(self, model: Any, meta: dict[str, Any]) -> None:
        self.model = model
        self.meta = meta

    @classmethod
    def load(cls, model_dir: Path) -> FaultModel:
        import joblib  # optional dependency

        meta = json.loads((model_dir / "metadata.json").read_text(encoding="utf-8"))
        if meta.get("features") != feature_names():
            raise ValueError("model feature schema does not match this version of FixMyTech")
        return cls(joblib.load(model_dir / "model.joblib"), meta)

    def predict(self, facts: dict[str, Any], top_k: int = 3) -> dict[str, Any]:
        import numpy as np

        row = facts_to_row(facts)
        x = np.array([[row[f] for f in feature_names()]], dtype=float)
        proba = self.model.predict_proba(x)[0]
        order = proba.argsort()[::-1][:top_k]
        validated = bool(self.meta.get("validated")) and self.meta.get("data_provenance") == "technician_verified"
        return {
            "status": "validated" if validated else "experimental_unvalidated",
            "model_version": self.meta.get("model_version"),
            "data_provenance": self.meta.get("data_provenance"),
            "calibrated": bool(self.meta.get("calibrated")),
            "score_label": "model probability" if self.meta.get("calibrated") else "uncalibrated model score",
            "top": [{"fault_id": str(self.model.classes_[i]), "score": round(float(proba[i]), 3)} for i in order],
            "used_in_ranking": False,
            "note": "Shown separately from the rule-based ranking. "
                    + ("" if validated else "This model has not been validated on real repair data."),
        }


@lru_cache(maxsize=4)
def load_model(model_dir: str | None) -> FaultModel | None:
    if not model_dir:
        return None
    try:
        return FaultModel.load(Path(model_dir))
    except Exception:  # noqa: BLE001 - a broken model must never take down diagnosis
        log.exception("ML model could not be loaded from %s", model_dir)
        return None


def ml_opinion(model_dir: str | None, enabled: bool, facts: dict[str, Any]) -> dict[str, Any] | None:
    if not enabled:
        return None
    model = load_model(model_dir)
    if model is None:
        return {"status": "unavailable", "note": "ML is enabled but no usable model was found.",
                "used_in_ranking": False}
    return model.predict(facts)
