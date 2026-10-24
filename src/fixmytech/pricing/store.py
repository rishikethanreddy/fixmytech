"""File-based pricing store.

Price data is reference data refreshed by an operator-run ingestion job, so it lives in a JSON dataset file
(``<pricing_dir>/dataset.json``) rather than in the per-customer session database. Each ingestion run also
writes ``<pricing_dir>/reports/<timestamp>.json``. Writes are atomic (temp file + rename).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fixmytech.pricing.models import PriceRecord, PricingDataset, SourceStatus


class PricingStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "dataset.json"

    def load(self) -> PricingDataset:
        if not self.path.is_file():
            return PricingDataset()
        return PricingDataset.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, dataset: PricingDataset) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(dataset.model_dump_json(indent=1))
        os.replace(tmp, self.path)

    def save_report(self, report: dict[str, Any], at: datetime) -> Path:
        reports = self.directory / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        path = reports / f"{at:%Y%m%dT%H%M%SZ}.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return path


def merge(dataset: PricingDataset, source_id: str, fresh: list[PriceRecord], at: datetime) -> dict[str, int]:
    """Merge one source's fresh records. Duplicates (same record id) within the batch are dropped.

    added: new record id · updated: value changed (last_changed_at moves) · unchanged: same value
    (last_verified_at moves) · not_seen: existing records the source no longer lists (kept, but no longer
    verified, so they age out of freshness).
    """
    existing = {r.record_id: r for r in dataset.records}
    counts = {"added": 0, "updated": 0, "unchanged": 0, "duplicates": 0, "not_seen": 0}
    seen: set[str] = set()
    for rec in fresh:
        if rec.record_id in seen:
            counts["duplicates"] += 1
            continue
        seen.add(rec.record_id)
        old = existing.get(rec.record_id)
        if old is None:
            existing[rec.record_id] = rec
            counts["added"] += 1
        elif old.content_hash() == rec.content_hash():
            # Same source value: keep its history, but take the current normalization (exclusions, limitations).
            existing[rec.record_id] = rec.model_copy(update={"first_seen_at": old.first_seen_at,
                                                             "last_changed_at": old.last_changed_at,
                                                             "last_verified_at": at})
            counts["unchanged"] += 1
        else:
            existing[rec.record_id] = rec.model_copy(update={"first_seen_at": old.first_seen_at,
                                                             "last_changed_at": at, "last_verified_at": at})
            counts["updated"] += 1
    counts["not_seen"] = sum(1 for r in existing.values()
                             if r.evidence.source_id == source_id and r.record_id not in seen)
    dataset.records = list(existing.values())
    status = dataset.sources.get(source_id) or SourceStatus(source_id=source_id)
    status.last_attempt_at = at
    status.last_success_at = at
    status.last_error = None
    status.records = sum(1 for r in dataset.records if r.evidence.source_id == source_id)
    dataset.sources[source_id] = status
    dataset.generated_at = at
    return counts


def record_failure(dataset: PricingDataset, source_id: str, at: datetime, error: str) -> None:
    status = dataset.sources.get(source_id) or SourceStatus(source_id=source_id)
    status.last_attempt_at = at
    status.last_error = error[:300]
    dataset.sources[source_id] = status
