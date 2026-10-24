"""Price ingestion job (run manually or from a scheduler; never per customer request).

    python -m fixmytech.pricing.ingest                              # all automated sources
    python -m fixmytech.pricing.ingest --source samsung_in_parts
    python -m fixmytech.pricing.ingest --from-file samsung_in_parts=saved.html   # parse a saved copy
    python -m fixmytech.pricing.ingest --import-manual curated.json             # human-curated records
    python -m fixmytech.pricing.ingest --dry-run

Writes <pricing_dir>/dataset.json and a report in <pricing_dir>/reports/. Exit code 1 if any source failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from fixmytech import __version__
from fixmytech.pricing.collectors import google_store, samsung_in, samsung_us
from fixmytech.pricing.collectors.base import CollectorError, ParseResult
from fixmytech.pricing.fetch import FetchError, PoliteFetcher
from fixmytech.pricing.models import PriceRecord, PricingDataset, UsagePolicy
from fixmytech.pricing.sources import SOURCES_BY_ID
from fixmytech.pricing.store import PricingStore, merge, record_failure

log = logging.getLogger("fixmytech.pricing.ingest")

Parser = Callable[[str, datetime, str | None], ParseResult]
# Single-document sources: the page itself contains the data.
PAGE_JOBS: dict[str, Parser] = {
    "samsung_in_parts": samsung_in.parse_parts_page,
    "samsung_in_labour": samsung_in.parse_labour_page,
    "samsung_us_screen": samsung_us.parse_page,
}
# Multi-request sources: the page lists models, a data endpoint returns each model's prices.
MULTI_JOBS = set(google_store.MARKETS)
JOBS = {*PAGE_JOBS, *MULTI_JOBS}


def _run_google(sid: str, fetcher: PoliteFetcher, from_file: Path | None, at: datetime) -> tuple[ParseResult, str, str]:
    country, hl, _cur, url = google_store.MARKETS[sid]
    if from_file is not None:
        bundle = json.loads(from_file.read_text(encoding="utf-8"))  # {"page": html, "rpc": {sku: response}}
        page = bundle["page"]
        sha = hashlib.sha256(page.encode()).hexdigest()
        return (google_store.collect(sid, page, lambda sku: bundle["rpc"].get(sku, ""), at, sha), sha,
                f"file:{from_file.name}")
    doc = fetcher.get(url)

    def fetch_rpc(sku: str) -> str:
        return fetcher.post_form(google_store.RPC_URL, google_store.rpc_params(country, hl),
                                 google_store.rpc_form(country, hl, sku)).text

    return google_store.collect(sid, doc.text, fetch_rpc, at, doc.sha256), doc.sha256, "http"


def default_pricing_dir() -> Path:
    return Path(os.environ.get("FIXMYTECH_PRICING_DIR", "var/pricing"))


def user_agent() -> str:
    contact = os.environ.get("FIXMYTECH_PRICING_CONTACT", "").strip()
    return f"FixMyTech-PriceBot/{__version__} (+repair price reference; contact: {contact or 'not configured'})"


def run(store: PricingStore, sources: list[str], from_files: dict[str, Path], dry_run: bool = False,
        fetcher: PoliteFetcher | None = None, now: datetime | None = None) -> dict[str, Any]:
    at = now or datetime.now(UTC)
    dataset = store.load()
    report: dict[str, Any] = {"started_at": at.isoformat(), "dry_run": dry_run, "sources": {}}
    fetcher = fetcher or PoliteFetcher(user_agent=user_agent())
    for sid in sources:
        definition = SOURCES_BY_ID[sid]
        entry: dict[str, Any] = {"url": definition.url, "usage_policy": definition.usage_policy.value}
        report["sources"][sid] = entry
        if definition.usage_policy in (UsagePolicy.MANUAL_ONLY, UsagePolicy.PROHIBITED) or sid not in JOBS:
            entry.update(status="skipped", reason=f"no automated collector permitted ({definition.usage_policy.value})")
            continue
        try:
            if sid in MULTI_JOBS:
                parsed, sha, origin = _run_google(sid, fetcher, from_files.get(sid), at)
            else:
                if sid in from_files:
                    raw = from_files[sid].read_bytes()
                    text, sha, origin = (raw.decode("utf-8"), hashlib.sha256(raw).hexdigest(),
                                         f"file:{from_files[sid].name}")
                else:
                    doc = fetcher.get(definition.url)
                    text, sha, origin = doc.text, doc.sha256, "http"
                parsed = PAGE_JOBS[sid](text, at, sha)
        except (FetchError, CollectorError, OSError, UnicodeDecodeError, ValueError, KeyError) as exc:
            log.error("source %s failed: %s", sid, exc)
            record_failure(dataset, sid, at, f"{type(exc).__name__}: {exc}")
            entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            continue
        previous_hash = next((r.evidence.document_sha256 for r in dataset.records if r.evidence.source_id == sid), None)
        counts = merge(dataset, sid, parsed.records, at)
        entry.update(status="ok", origin=origin, document_sha256=sha, document_changed=previous_hash not in (None, sha),
                     items_seen=parsed.items_seen, records_extracted=len(parsed.records),
                     rejected=len(parsed.rejected), rejected_samples=parsed.rejected[:20], **counts)
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["total_records"] = len(dataset.records)
    if not dry_run:
        store.save(dataset)
        report["report_path"] = str(store.save_report(report, at))
    return report


def import_manual(store: PricingStore, path: Path, now: datetime | None = None) -> dict[str, Any]:
    """Import human-curated, source-attributed records (e.g. for MANUAL_ONLY sources)."""
    at = now or datetime.now(UTC)
    try:
        records = TypeAdapter(list[PriceRecord]).validate_json(path.read_bytes())
    except ValidationError as exc:
        return {"status": "failed", "error": f"{exc.error_count()} validation error(s)", "details": exc.errors()[:10]}
    bad = [r.record_id for r in records if not r.evidence.extraction_method.startswith("manual")
           or SOURCES_BY_ID.get(r.evidence.source_id) is None
           or SOURCES_BY_ID[r.evidence.source_id].usage_policy == UsagePolicy.PROHIBITED]
    if bad:
        return {"status": "failed", "error": "records must be manual, from a known and non-prohibited source",
                "records": bad[:20]}
    dataset: PricingDataset = store.load()
    by_source: dict[str, list[PriceRecord]] = {}
    for r in records:
        by_source.setdefault(r.evidence.source_id, []).append(r)
    out = {sid: merge(dataset, sid, recs, at) for sid, recs in by_source.items()}
    store.save(dataset)
    return {"status": "ok", "sources": out}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=default_pricing_dir())
    ap.add_argument("--source", action="append", choices=sorted(SOURCES_BY_ID))
    ap.add_argument("--from-file", action="append", default=[], metavar="SOURCE=PATH")
    ap.add_argument("--import-manual", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    store = PricingStore(args.dir)
    if args.import_manual:
        result = import_manual(store, args.import_manual)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["status"] == "ok" else 1
    files = {}
    for spec in args.from_file:
        sid, _, p = spec.partition("=")
        files[sid] = Path(p)
    sources = args.source or [s for s in JOBS if s in SOURCES_BY_ID]
    report = run(store, sources, files, dry_run=args.dry_run)
    summary = {sid: {k: v for k, v in e.items() if k != "rejected_samples"} for sid, e in report["sources"].items()}
    print(json.dumps({"total_records": report["total_records"], "sources": summary}, indent=2, default=str))
    return 1 if any(e.get("status") == "failed" for e in report["sources"].values()) else 0


if __name__ == "__main__":
    sys.exit(main())
