"""Feature schema shared by training data and inference.

A training CSV row has the metadata columns below plus one column per feature. Numeric features
may be blank (missing). Answer features are 0/1 indicators. Facts are mapped with ``facts_to_row``,
so training data exported from real FixMyTech sessions has exactly the inference-time features.
"""

from __future__ import annotations

import math
from typing import Any

METADATA_COLUMNS = ("case_id", "device_group", "collected_at", "label_fault", "label_source")
LABEL_SOURCES = ("technician_verified", "synthetic")

NUMERIC_FEATURES: dict[str, str] = {
    "battery_temp_c": "obs.battery.status.temperature_c",
    "battery_capacity_pct": "obs.battery.capacity.capacity_ratio_pct",
    "battery_cycles": "obs.battery.capacity.cycle_count",
    "storage_free_pct": "obs.storage.data.free_pct",
    "app_instability_7d": "derived.app_instability_7d",
    "system_server_crashes": "obs.stability.crashes.system_server_crashes",
    "kernel_panics": "obs.stability.crashes.kernel_panics",
    "abnormal_restarts": "obs.stability.boot.abnormal_restarts_recent",
    "camera_devices": "obs.camera.enumeration.camera_devices",
    "thermal_code": "obs.thermal.status.throttling_code",
    "device_age_months": "device.age_months",
}

BOOL_FEATURES: dict[str, tuple[str, Any]] = {
    "touch_not_registered": ("obs.input.touchscreen.touchscreen_registered", False),
    "connected_not_charging": ("obs.charging.state.assessment", "connected_not_charging"),
    "battery_health_bad": ("obs.battery.status.health", "dead"),
}

# (question_id, option) indicator features
ANSWER_FEATURES: tuple[tuple[str, str], ...] = tuple(
    [("primary_symptoms", v) for v in ("battery_drain", "not_charging", "overheating", "shutdowns", "slow",
                                       "app_crashes", "screen", "touch", "camera", "audio", "network",
                                       "storage_full")]
    + [("physical_event", v) for v in ("dropped", "liquid", "both")]
    + [("safe_mode", "stops"), ("safe_mode", "continues"), ("charging_behavior", "intermittent"),
       ("charging_other_cable", "works_fine"), ("battery_drain_pattern", "sudden_drops"),
       ("shutdown_at_charge", "yes_above_20"), ("touch_symptom", "dead_areas"), ("battery_swelling", "yes")]
)


def feature_names() -> list[str]:
    return [*NUMERIC_FEATURES, *BOOL_FEATURES, *(f"ans__{q}__{o}" for q, o in ANSWER_FEATURES)]


def facts_to_row(facts: dict[str, Any]) -> dict[str, float]:
    row: dict[str, float] = {}
    for name, fact in NUMERIC_FEATURES.items():
        v = facts.get(fact)
        row[name] = float(v) if isinstance(v, int | float) and not isinstance(v, bool) else math.nan
    for name, (fact, positive) in BOOL_FEATURES.items():
        v = facts.get(fact)
        row[name] = math.nan if v is None else float(v == positive)
    for q, o in ANSWER_FEATURES:
        v = facts.get(f"ans.{q}")
        hit = (o in v) if isinstance(v, list) else (v == o)
        row[f"ans__{q}__{o}"] = float(hit) if v is not None else 0.0
    return row
