"""SYNTHETIC case generator - for software tests and demos ONLY.

Cases are generated from hand-written fault profiles, so a model trained on them only learns those
profiles back. Any metric computed on this data says nothing about real diagnostic accuracy. Every
row is labelled ``label_source=synthetic``.

    python -m fixmytech.ml.synthetic --out synthetic_cases.csv --n 600
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from pathlib import Path

from fixmytech.ml.features import METADATA_COLUMNS, feature_names

PROFILES: dict[str, dict[str, object]] = {
    "battery_wear": {"battery_capacity_pct": (72, 6), "battery_cycles": (750, 150),
                     "ans": ["primary_symptoms:battery_drain", "battery_drain_pattern:sudden_drops"]},
    "storage_pressure": {"storage_free_pct": (3, 2), "ans": ["primary_symptoms:slow", "primary_symptoms:storage_full"]},
    "software_instability": {"app_instability_7d": (25, 8), "system_server_crashes": (1, 1),
                             "ans": ["primary_symptoms:app_crashes", "safe_mode:stops"]},
    "charging_path": {"connected_not_charging": 1.0,
                      "ans": ["primary_symptoms:not_charging", "charging_behavior:intermittent"]},
    "display_damage": {"touch_not_registered": 0.3, "ans": ["primary_symptoms:screen", "physical_event:dropped",
                                                            "touch_symptom:dead_areas"]},
}


def generate(n: int, seed: int = 7) -> list[dict[str, object]]:
    rng = random.Random(seed)
    names = feature_names()
    rows = []
    labels = list(PROFILES)
    for i in range(n):
        label = labels[i % len(labels)]
        prof = PROFILES[label]
        row: dict[str, object] = dict.fromkeys(names, 0.0)
        for f in ("battery_temp_c", "battery_capacity_pct", "battery_cycles", "storage_free_pct",
                  "app_instability_7d", "system_server_crashes", "kernel_panics", "abnormal_restarts",
                  "camera_devices", "thermal_code", "device_age_months"):
            row[f] = math.nan if rng.random() < 0.3 else {
                "battery_temp_c": rng.gauss(32, 4), "battery_capacity_pct": rng.gauss(90, 5),
                "battery_cycles": max(0, rng.gauss(350, 150)), "storage_free_pct": max(0, rng.gauss(35, 15)),
                "app_instability_7d": max(0, round(rng.gauss(3, 3))), "system_server_crashes": 0,
                "kernel_panics": 0, "abnormal_restarts": 0, "camera_devices": rng.choice([2, 3, 4]),
                "thermal_code": 0, "device_age_months": rng.randint(3, 60)}[f]
        for key, spec in prof.items():
            if key == "ans":
                for a in spec:  # type: ignore[attr-defined]
                    q, o = str(a).split(":")
                    col = f"ans__{q}__{o}"
                    if col in row and rng.random() < 0.8:
                        row[col] = 1.0
            elif isinstance(spec, tuple):
                mu, sigma = spec
                row[key] = max(0.0, rng.gauss(float(mu), float(sigma)))
            else:
                row[key] = 1.0 if rng.random() < float(spec) else 0.0  # type: ignore[arg-type]
        # Label noise so the toy problem is not trivially separable.
        if rng.random() < 0.1:
            label = rng.choice(labels)
        row.update({"case_id": f"syn-{i:05d}", "device_group": f"syn-dev-{i // 3}",
                    "collected_at": "synthetic", "label_fault": label, "label_source": "synthetic"})
        rows.append(row)
    return rows


def write_csv(rows: list[dict[str, object]], path: Path) -> None:
    cols = [*METADATA_COLUMNS, *feature_names()]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if isinstance(r[c], float) and math.isnan(r[c]) else r[c]) for c in cols})  # type: ignore[arg-type]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--n", type=int, default=600)
    args = ap.parse_args()
    write_csv(generate(args.n), args.out)
    print(f"wrote {args.n} SYNTHETIC rows to {args.out}")


if __name__ == "__main__":
    main()
