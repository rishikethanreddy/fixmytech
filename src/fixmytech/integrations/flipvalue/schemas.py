"""FlipValue API contract v1 (PROPOSED - pending partner agreement).

Endpoint: ``POST {FLIPVALUE_BASE_URL}/v1/valuations``
Auth:     ``X-API-Key: <key>``
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "1.0-proposed"


class ConditionGrade(StrEnum):
    """Cosmetic + functional condition. Definitions are in INTEGRATION_CONTRACT.md."""

    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    FAULTY = "faulty"  # has at least one functional fault


# FixMyTech fault ids sent to FlipValue. Mirrors fixmytech/data/taxonomy.json.
FAULT_CATEGORIES = (
    "software_instability",
    "storage_pressure",
    "battery_wear",
    "charging_path",
    "display_damage",
    "camera_fault",
    "audio_fault",
    "connectivity_issue",
    "liquid_damage",
)

REPAIR_TYPES = (
    "battery_replacement",
    "charging_port_replacement",
    "screen_replacement",
    "camera_module_replacement",
    "audio_component_replacement",
    "liquid_damage_service",
    "software_reset",
)


class DeviceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manufacturer: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=80)
    storage_gb: int | None = Field(default=None, ge=1, le=4096)
    age_months: int | None = Field(default=None, ge=0, le=240)


class ConditionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grade: ConditionGrade
    faults: list[str] = Field(default_factory=list, max_length=len(FAULT_CATEGORIES))

    @model_validator(mode="after")
    def _known_faults(self) -> ConditionSpec:
        unknown = set(self.faults) - set(FAULT_CATEGORIES)
        if unknown:
            raise ValueError(f"unknown fault categories: {sorted(unknown)}")
        return self


class RepairScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repair_type: str

    @model_validator(mode="after")
    def _known(self) -> RepairScenario:
        if self.repair_type not in REPAIR_TYPES:
            raise ValueError(f"unknown repair type {self.repair_type}")
        return self


class ValuationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: DeviceSpec
    region: str = Field(pattern=r"^[A-Z]{2}$", description="ISO 3166-1 alpha-2 market")
    currency: str = Field(pattern=r"^[A-Z]{3}$", description="ISO 4217 currency requested")
    condition: ConditionSpec
    repair_scenario: RepairScenario | None = None


class MoneyEstimate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    value: Decimal = Field(ge=0)
    low: Decimal | None = Field(default=None, ge=0)
    high: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _range(self) -> MoneyEstimate:
        if self.low is not None and self.low > self.value:
            raise ValueError("low > value")
        if self.high is not None and self.high < self.value:
            raise ValueError("high < value")
        return self


class ValuationResponse(BaseModel):
    """Responses are validated strictly on the fields FixMyTech relies on; extra fields are ignored."""

    model_config = ConfigDict(extra="ignore")
    api_version: str
    valuation_id: str = Field(max_length=100)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    as_is: MoneyEstimate
    after_repair: MoneyEstimate | None = None
    data_timestamp: datetime
    method: str = Field(max_length=200)
    status: str = Field(pattern=r"^(ok|partial)$")
    warnings: list[str] = Field(default_factory=list, max_length=20)
    mock: bool = False


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: str
    message: str
    request_id: str | None = None


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")
    error: ErrorBody
