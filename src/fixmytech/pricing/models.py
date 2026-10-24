"""Pricing data model.

Two layers, kept separate so every normalized value can be traced to its evidence:

* ``SourceEvidence`` - what the source literally said (raw label and raw price text, URL, retrieval time,
  content hash of the retrieved document).
* ``PriceRecord`` - the normalized interpretation (manufacturer, model, component, numeric amount, scope,
  tax treatment), which embeds its ``SourceEvidence``.

Unknown attributes are ``None`` / ``"unknown"``; nothing is filled in by assumption.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SourceType(StrEnum):
    OFFICIAL_PARTS_LIST = "official_parts_list"  # manufacturer spare-part prices
    OFFICIAL_LABOUR_SCHEDULE = "official_labour_schedule"  # manufacturer service/labour charges
    OFFICIAL_SERVICE_ESTIMATE = "official_service_estimate"  # manufacturer all-in repair estimate
    AUTHORIZED_PROVIDER = "authorized_service_provider"
    INDEPENDENT_SHOP = "independent_shop"
    CUSTOMER_QUOTE = "customer_quote"
    COMPLETED_REPAIR = "completed_repair"  # verified transaction
    DEMO_FIXTURE = "demo_fixture"  # development only, never real
    MARKET_BENCHMARK = "market_benchmark"  # industry benchmark reference range


class PriceScope(StrEnum):
    PARTS_ONLY = "parts_only"
    LABOUR_ONLY = "labour_only"
    INSPECTION_FEE = "inspection_fee"
    PARTS_AND_LABOUR = "parts_and_labour"
    ALL_INCLUSIVE = "all_inclusive"  # parts, labour and taxes
    UNKNOWN = "unknown"


class TaxTreatment(StrEnum):
    EXCLUDED = "excluded"
    INCLUDED = "included"
    UNKNOWN = "unknown"


class PricePoint(StrEnum):
    EXACT = "exact"
    STARTING_FROM = "starting_from"
    RANGE = "range"


class UsagePolicy(StrEnum):
    """Whether automated retrieval and display of a source is acceptable."""

    AUTOMATED_PERMITTED = "automated_permitted"  # robots and terms allow it
    LEGAL_REVIEW_REQUIRED = "legal_review_required"  # robots allow, terms restrict redistribution
    MANUAL_ONLY = "manual_only"  # robots disallow automation; human curation with attribution only
    PROHIBITED = "prohibited"  # terms prohibit automated and equivalent manual copying


class SourceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_url: str
    organization: str
    source_type: SourceType
    retrieved_at: datetime
    document_sha256: str | None = Field(default=None, description="Hash of the retrieved document")
    published_at: str | None = Field(default=None, description="Publication/effective date stated by the source")
    extraction_method: str
    raw_label: str = Field(description="Item label as written by the source")
    raw_value: str = Field(description="Price text as written by the source")
    raw_context: dict[str, str] = Field(default_factory=dict, description="Other source fields kept verbatim")


class PriceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    manufacturer: str
    model_name: str | None = None
    model_code: str | None = None
    applies_to_group: str | None = Field(default=None, description="For tiered prices, e.g. 'Galaxy S series'")
    region: str = Field(pattern=r"^[A-Z]{2}$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    component: str = Field(description="Normalized component/service id, e.g. display_assembly, main_battery")
    component_label: str
    amount: Decimal = Field(ge=0)
    amount_high: Decimal | None = Field(default=None, ge=0)
    price_point: PricePoint = PricePoint.EXACT
    scope: PriceScope
    tax: TaxTreatment
    includes: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    evidence: SourceEvidence
    first_seen_at: datetime
    last_verified_at: datetime
    last_changed_at: datetime

    def content_hash(self) -> str:
        key = f"{self.amount}|{self.amount_high}|{self.scope}|{self.tax}|{self.price_point}|{self.evidence.raw_value}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]


class SourceStatus(BaseModel):
    source_id: str
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    records: int = 0


class PricingDataset(BaseModel):
    schema_version: str = "1"
    generated_at: datetime | None = None
    sources: dict[str, SourceStatus] = Field(default_factory=dict)
    records: list[PriceRecord] = Field(default_factory=list)


def record_id(source_id: str, *parts: str | None) -> str:
    key = "|".join([source_id, *[(p or "").strip().lower() for p in parts]])
    return hashlib.sha256(key.encode()).hexdigest()[:20]
