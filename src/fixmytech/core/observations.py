"""Observation schema: the unit of evidence produced by a diagnostic check.

An observation records *what a check saw*, where it came from, and how much it can be trusted.
A failed or unsupported check is still an observation (status != "ok"), and must never be
interpreted as evidence that the device is healthy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCAN_SCHEMA_VERSION = "1.0"

# Scalar values only: observations are normalized key/value facts, never raw log text.
ObservationValue = dict[str, str | int | float | bool | None]


class CheckStatus(StrEnum):
    OK = "ok"
    UNSUPPORTED = "unsupported"  # the device/OS does not expose this signal
    PERMISSION_DENIED = "permission_denied"  # exposed but not readable by the adb shell user
    TIMEOUT = "timeout"
    ERROR = "error"  # command failed or output could not be parsed
    DEVICE_DISCONNECTED = "device_disconnected"
    SKIPPED = "skipped"  # not run (e.g. prerequisite failed or scan aborted)


class EvidenceKind(StrEnum):
    MEASURED = "measured"  # a sensor/kernel reading (e.g. battery temperature)
    SYSTEM_REPORTED = "system_reported"  # an OS-level status or counter (e.g. crash counts)
    INFERRED = "inferred"  # derived by FixMyTech from other values
    UNAVAILABLE = "unavailable"  # nothing could be observed


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,63}$")
    timestamp: datetime
    status: CheckStatus
    value: ObservationValue = Field(default_factory=dict)
    source: str = Field(max_length=200, description="Command or data source that produced the value")
    evidence_kind: EvidenceKind
    limitations: list[str] = Field(default_factory=list, max_length=10)
    detail: str | None = Field(default=None, max_length=300, description="Human-readable status detail")

    @field_validator("value")
    @classmethod
    def _bounded_value(cls, v: ObservationValue) -> ObservationValue:
        if len(v) > 40:
            raise ValueError("too many fields in observation value")
        for key, item in v.items():
            if len(key) > 64:
                raise ValueError("observation field name too long")
            if isinstance(item, str) and len(item) > 200:
                raise ValueError(f"observation field {key!r} too long")
        return v

    @property
    def usable(self) -> bool:
        return self.status == CheckStatus.OK


class DeviceIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manufacturer: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    android_release: str | None = Field(default=None, max_length=20)
    sdk_int: int | None = Field(default=None, ge=1, le=100)


class ScanResult(BaseModel):
    """Payload sent from the agent (via the browser) to the backend."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCAN_SCHEMA_VERSION
    scan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    agent_version: str = Field(max_length=20)
    device_ref: str = Field(
        pattern=r"^[a-f0-9]{16}$",
        description="Salted hash of the device serial; the serial itself never leaves the agent",
    )
    identity: DeviceIdentity
    started_at: datetime
    finished_at: datetime
    complete: bool = Field(description="False if the scan was aborted (e.g. device disconnected)")
    observations: list[Observation] = Field(max_length=100)

    def by_check(self) -> dict[str, Observation]:
        return {o.check_id: o for o in self.observations}


def utcnow() -> datetime:
    return datetime.now(UTC)


def flatten_value(prefix: str, value: dict[str, Any]) -> dict[str, Any]:
    return {f"{prefix}.{k}": v for k, v in value.items()}
