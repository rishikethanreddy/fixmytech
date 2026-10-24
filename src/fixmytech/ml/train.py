"""Train and evaluate a fault classifier from a labelled CSV.

    python -m fixmytech.ml.train --data cases.csv --out models/fault_model

Evaluation protocol:
* Grouped K-fold cross-validation on ``device_group`` so the same device/customer never appears in
  both train and test folds (leakage control).
* Baseline: most-frequent-class classifier, evaluated identically. The model is only worth enabling if
  it clearly beats the baseline.
* Metrics: accuracy, macro-F1, balanced accuracy, multi-class Brier score and a top-class reliability
  table (calibration).

If any row has ``label_source == synthetic`` the metadata records ``validated: false`` and
``data_provenance: synthetic``; FixMyTech then never lets the model influence diagnoses.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from fixmytech.ml.features import LABEL_SOURCES, METADATA_COLUMNS, feature_names

MODEL_VERSION_PREFIX = "fault-logreg"
MIN_CASES_PER_CLASS = 5


def build_pipeline() -> Pipeline:
    return Pipeline([
        ("impute", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(max_iter=2000, C=1.0, class_weight="balanced")),
    ])


def _brier(y_true: np.ndarray, proba: np.ndarray, classes: np.ndarray) -> float:
    onehot = (y_true[:, None] == classes[None, :]).astype(float)
    return float(np.mean(np.sum((proba - onehot) ** 2, axis=1)))


def _reliability(y_true: np.ndarray, proba: np.ndarray, classes: np.ndarray, bins: int = 5) -> list[dict[str, Any]]:
    conf = proba.max(axis=1)
    correct = classes[proba.argmax(axis=1)] == y_true
    table = []
    edges = np.linspace(0, 1, bins + 1)
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        mask = (conf >= lo) & (conf < hi if hi < 1 else conf <= hi)
        if mask.any():
            table.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(mask.sum()),
                          "mean_confidence": round(float(conf[mask].mean()), 3),
                          "observed_accuracy": round(float(correct[mask].mean()), 3)})
    return table


def load_dataset(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [c for c in (*METADATA_COLUMNS, *feature_names()) if c not in df.columns]
    if missing:
        raise ValueError(f"dataset is missing columns: {missing}")
    bad = set(df["label_source"]) - set(LABEL_SOURCES)
    if bad:
        raise ValueError(f"unknown label_source values: {bad}")
    if df["case_id"].duplicated().any():
        raise ValueError("duplicate case_id values")
    return df


def evaluate(df: pd.DataFrame, folds: int = 5) -> dict[str, Any]:
    X = df[feature_names()].to_numpy(dtype=float)
    y = df["label_fault"].to_numpy()
    groups = df["device_group"].to_numpy()
    classes = np.unique(y)
    n_splits = min(folds, len(np.unique(groups)))
    if n_splits < 2:
        raise ValueError("need at least 2 distinct device_group values for grouped cross-validation")

    preds = {"model": np.empty_like(y), "baseline": np.empty_like(y)}
    proba = np.zeros((len(y), len(classes)))
    for train_idx, test_idx in GroupKFold(n_splits=n_splits).split(X, y, groups):
        model = build_pipeline().fit(X[train_idx], y[train_idx])
        base = DummyClassifier(strategy="most_frequent").fit(X[train_idx], y[train_idx])
        preds["model"][test_idx] = model.predict(X[test_idx])
        preds["baseline"][test_idx] = base.predict(X[test_idx])
        p = model.predict_proba(X[test_idx])
        for j, c in enumerate(model.classes_):
            proba[test_idx, int(np.where(classes == c)[0][0])] = p[:, j]

    def metrics(pred: np.ndarray) -> dict[str, float]:
        return {"accuracy": round(float(accuracy_score(y, pred)), 4),
                "macro_f1": round(float(f1_score(y, pred, average="macro", zero_division=0)), 4),
                "balanced_accuracy": round(float(balanced_accuracy_score(y, pred)), 4)}

    return {
        "protocol": f"GroupKFold(n_splits={n_splits}) on device_group",
        "n_cases": int(len(y)),
        "n_groups": int(len(np.unique(groups))),
        "class_counts": {str(k): int(v) for k, v in zip(*np.unique(y, return_counts=True), strict=True)},
        "baseline_most_frequent": metrics(preds["baseline"]),
        "model": metrics(preds["model"]),
        "model_brier": round(_brier(y, proba, classes), 4),
        "reliability": _reliability(y, proba, classes),
    }


def train(data: Path, out_dir: Path, folds: int = 5) -> dict[str, Any]:
    df = load_dataset(data)
    counts = df["label_fault"].value_counts()
    if (counts < MIN_CASES_PER_CLASS).any():
        raise ValueError(f"every class needs >= {MIN_CASES_PER_CLASS} cases: {counts.to_dict()}")
    synthetic = bool((df["label_source"] == "synthetic").any())
    report = evaluate(df, folds)
    model = build_pipeline().fit(df[feature_names()].to_numpy(dtype=float), df["label_fault"].to_numpy())
    digest = hashlib.sha256(data.read_bytes()).hexdigest()[:12]
    meta = {
        "model_version": f"{MODEL_VERSION_PREFIX}-{datetime.now(UTC):%Y%m%d}-{digest}",
        "trained_at": datetime.now(UTC).isoformat(),
        "dataset_sha256_12": digest,
        "data_provenance": "synthetic" if synthetic else "technician_verified",
        "validated": (not synthetic) and report["model"]["macro_f1"] > report["baseline_most_frequent"]["macro_f1"],
        "classes": [str(c) for c in model.classes_],
        "features": feature_names(),
        "calibrated": False,
        "evaluation": report,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out_dir / "model.joblib")
    (out_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--folds", type=int, default=5)
    args = ap.parse_args()
    meta = train(args.data, args.out, args.folds)
    print(json.dumps({k: meta[k] for k in ("model_version", "data_provenance", "validated")}, indent=2))
    print(json.dumps(meta["evaluation"], indent=2))


if __name__ == "__main__":
    main()
