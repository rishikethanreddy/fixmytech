from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from fixmytech.pricing import ingest
from fixmytech.pricing.collectors.samsung_in import (
    CollectorError,
    labour_tier_for,
    parse_inr,
    parse_labour_page,
    parse_parts_page,
)
from fixmytech.pricing.estimate import ModelMatch, PricingPolicy, estimate, match_model
from fixmytech.pricing.fetch import PoliteFetcher, RobotsDisallowedError
from fixmytech.pricing.models import PriceScope, PricingDataset, SourceType
from fixmytech.pricing.store import PricingStore, merge

FX = Path(__file__).parents[1] / "fixtures" / "pricing"
PARTS = (FX / "samsung_in_parts_excerpt.html").read_text(encoding="utf-8")
LABOUR = (FX / "samsung_in_labour_excerpt.html").read_text(encoding="utf-8")
T0 = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)


def dataset(at=T0) -> PricingDataset:
    ds = PricingDataset()
    merge(ds, "samsung_in_parts", parse_parts_page(PARTS, at, "sha-p").records, at)
    merge(ds, "samsung_in_labour", parse_labour_page(LABOUR, at, "sha-l").records, at)
    return ds


@pytest.mark.parametrize("raw,expected", [("1,11,960", Decimal(111960)), ("2,400", Decimal(2400)),
                                          ("425", Decimal(425)), ("", None), ("N/A", None),
                                          ("99.50", Decimal("99.50"))])
def test_parse_inr(raw, expected):
    assert parse_inr(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "12,34,5", "₹500", "1.2.3"])
def test_parse_inr_rejects_malformed(raw):
    with pytest.raises(ValueError):
        parse_inr(raw)


def test_parts_parser_on_real_excerpt():
    res = parse_parts_page(PARTS, T0, "sha")
    assert res.items_seen == 4 and not res.rejected
    s24u = {r.component: r for r in res.records if r.model_code == "SM-S928B"}
    assert s24u["main_battery"].amount == Decimal(2400)
    assert s24u["oled_panel"].amount == Decimal(16160)
    assert "display_assembly" not in s24u  # Samsung lists no full assembly for this model
    rec = s24u["main_battery"]
    assert rec.scope == PriceScope.PARTS_ONLY and rec.tax.value == "excluded"
    assert {"labour", "GST"} <= set(rec.excludes)
    ev = rec.evidence
    assert ev.source_url.startswith("https://www.samsung.com/in/support/") and ev.raw_value == "2,400"
    assert ev.raw_label == "MainBattery" and ev.retrieved_at == T0 and ev.source_type == SourceType.OFFICIAL_PARTS_LIST
    assert ev.raw_context["labour_tier"] == "Galaxy S series / Note series / Tab S series"


def test_labour_parser_on_real_excerpt():
    res = parse_labour_page(LABOUR, T0, "sha")
    by = {(r.applies_to_group, r.component): r for r in res.records}
    assert len(res.records) == 15
    assert by[("Other Galaxy series", "labour_l2_parts_used")].amount == Decimal(350)
    assert by[("Galaxy Z series (Fold & Flip)", "inspection_fee")].scope == PriceScope.INSPECTION_FEE
    assert res.records[0].evidence.published_at == "2026-09-25"


def test_site_change_is_reported_not_silent():
    with pytest.raises(CollectorError):
        parse_parts_page("<html>redesigned page without data</html>", T0, None)
    with pytest.raises(CollectorError):
        parse_labour_page('<script>const data = {"Mobile Devices": []}</script>', T0, None)
    with pytest.raises(CollectorError):
        parse_parts_page("<script>var myProducts=[ {broken json </script>", T0, None)


def test_invalid_values_rejected_with_reason():
    bad = PARTS.replace('"MainBattery": "2,400"', '"MainBattery": "two thousand"')
    res = parse_parts_page(bad, T0, None)
    assert any("SM-S928B MainBattery" in r["item"] for r in res.rejected)
    huge = PARTS.replace('"MainBattery": "2,400"', '"MainBattery": "99,99,999"')
    assert any("out of range" in r["reason"] for r in parse_parts_page(huge, T0, None).rejected)


@pytest.mark.parametrize("series,product,tier", [
    ("Galaxy Z", "Galaxy Z Fold7", "Galaxy Z series (Fold & Flip)"),
    ("Galaxy S", "Galaxy S24 FE", "Galaxy S series / Note series / Tab S series"),
    ("Galaxy Tab", "Galaxy Tab S9", "Galaxy S series / Note series / Tab S series"),
    ("Galaxy Tab", "Galaxy Tab A9", "Other Galaxy series"),
    ("Galaxy A", "Galaxy A55 5G", "Other Galaxy series"),
])
def test_labour_tiers(series, product, tier):
    assert labour_tier_for(series, product) == tier


def test_merge_added_unchanged_updated_not_seen():
    ds = PricingDataset()
    first = parse_parts_page(PARTS, T0, "a").records
    assert merge(ds, "samsung_in_parts", first, T0)["added"] == len(first)
    t1 = T0 + timedelta(days=3)
    counts = merge(ds, "samsung_in_parts", parse_parts_page(PARTS, t1, "a").records, t1)
    assert counts["unchanged"] == len(first) and counts["added"] == 0
    rec = next(r for r in ds.records if r.model_code == "SM-S928B" and r.component == "main_battery")
    assert rec.last_verified_at == t1 and rec.first_seen_at == T0 and rec.last_changed_at == T0
    t2 = T0 + timedelta(days=6)
    changed = PARTS.replace('"MainBattery": "2,400"', '"MainBattery": "2,500"')
    fewer = [r for r in parse_parts_page(changed, t2, "b").records if r.model_code != "SM-F966B"]
    counts = merge(ds, "samsung_in_parts", fewer, t2)
    assert counts["updated"] == 1 and counts["not_seen"] > 0
    rec = next(r for r in ds.records if r.model_code == "SM-S928B" and r.component == "main_battery")
    assert rec.amount == Decimal(2500) and rec.last_changed_at == t2 and rec.first_seen_at == T0
    dup = merge(ds, "samsung_in_parts", [first[0], first[0]], t2)
    assert dup["duplicates"] == 1


def test_estimate_exact_model_parts_plus_labour():
    e = estimate(dataset(), "battery_replacement", "Battery replacement", "samsung", "SM-S928B", "IN", PricingPolicy(),
                 now=T0)
    assert e.status == "estimated" and e.usable_for_decision
    assert e.low == e.high == Decimal(2400 + 425)
    assert e.scope == PriceScope.PARTS_AND_LABOUR and e.tax == "excluded"
    assert e.model_match == ModelMatch.EXACT_CODE and e.freshness == "fresh"
    assert [x.scope for x in e.evidence] == [PriceScope.PARTS_ONLY, PriceScope.LABOUR_ONLY]
    assert all(x.source_url and x.retrieved_at for x in e.evidence)
    assert "GST" in e.exclusions and "labour" not in e.exclusions


def test_screen_falls_back_to_component_repair_with_note():
    e = estimate(dataset(), "screen_replacement", "Screen", "samsung", "SM-S928B", "IN", PricingPolicy(), now=T0)
    assert e.low == Decimal(16160 + 425)
    assert any("screen component repair" in n for n in e.notes)


def test_model_matching_levels():
    recs = [r for r in dataset().records if r.scope == PriceScope.PARTS_ONLY]
    assert match_model(recs, "samsung", "SM-S928B").kind == ModelMatch.EXACT_CODE
    assert match_model(recs, "Samsung", "Galaxy S24 Ultra").kind == ModelMatch.EXACT_NAME
    assert match_model(recs, "samsung", "SM-S928U").kind == ModelMatch.VARIANT
    assert match_model(recs, "samsung", "SM-S921B").kind == ModelMatch.NONE  # related model is never used
    assert match_model(recs, "google", "SM-S928B").kind == ModelMatch.NONE  # wrong manufacturer


def test_variant_match_is_labelled_approximation():
    e = estimate(dataset(), "battery_replacement", "Battery", "samsung", "SM-S928U", "IN", PricingPolicy(), now=T0)
    assert e.model_match == ModelMatch.VARIANT and any("Approximation" in n for n in e.notes)


@pytest.mark.parametrize("man,model,rt,region", [
    ("google", "Pixel 7", "battery_replacement", "IN"),  # no source for brand
    ("samsung", "SM-S928B", "charging_port_replacement", "IN"),  # repair type not in source
    ("samsung", "SM-S928B", "battery_replacement", "US"),  # no data for region
    ("samsung", None, "battery_replacement", "IN"),  # model unknown
])
def test_insufficient_pricing_data(man, model, rt, region):
    e = estimate(dataset(), rt, rt, man, model, region, PricingPolicy(), now=T0)
    assert e.status == "insufficient_pricing_data" and e.low is None and not e.usable_for_decision


def test_parts_only_when_labour_missing():
    ds = PricingDataset()
    merge(ds, "samsung_in_parts", parse_parts_page(PARTS, T0, "p").records, T0)
    e = estimate(ds, "battery_replacement", "Battery", "samsung", "SM-S928B", "IN", PricingPolicy(), now=T0)
    assert e.status == "parts_only" and e.scope == PriceScope.PARTS_ONLY and not e.usable_for_decision


def test_policy_blocks_unapproved_sources():
    e = estimate(dataset(), "battery_replacement", "Battery", "samsung", "SM-S928B", "IN",
                 PricingPolicy(allowed_sources=set()), now=T0)
    assert e.status == "insufficient_pricing_data"
    e = estimate(dataset(), "battery_replacement", "Battery", "samsung", "SM-S928B", "IN",
                 PricingPolicy(allowed_sources={"samsung_in_parts"}), now=T0)
    assert e.status == "parts_only"


def test_freshness_ages():
    pol = PricingPolicy(fresh_days=30, stale_days=90)
    ds = dataset()
    assert estimate(ds, "battery_replacement", "B", "samsung", "SM-S928B", "IN", pol,
                    now=T0 + timedelta(days=45)).freshness == "aging"
    assert estimate(ds, "battery_replacement", "B", "samsung", "SM-S928B", "IN", pol,
                    now=T0 + timedelta(days=120)).freshness == "stale"


def test_official_links_for_brands_without_data():
    e = estimate(dataset(), "screen_replacement", "Screen", "apple", "iPhone 15", "IN", PricingPolicy(), now=T0)
    assert e.status == "insufficient_pricing_data"
    assert any("apple.com" in link["url"] for link in e.official_links)


def test_ingest_from_saved_files_and_report(tmp_path):
    store = PricingStore(tmp_path)
    (tmp_path / "p.html").write_text(PARTS, encoding="utf-8")
    (tmp_path / "l.html").write_text(LABOUR, encoding="utf-8")
    report = ingest.run(store, ["samsung_in_parts", "samsung_in_labour", "motorola_in_parts"],
                        {"samsung_in_parts": tmp_path / "p.html", "samsung_in_labour": tmp_path / "l.html"}, now=T0)
    assert report["sources"]["samsung_in_parts"]["status"] == "ok"
    assert report["sources"]["samsung_in_parts"]["added"] > 0
    assert report["sources"]["motorola_in_parts"]["status"] == "skipped"  # robots disallow: no collector
    assert Path(report["report_path"]).is_file()
    loaded = store.load()
    assert loaded.sources["samsung_in_parts"].last_success_at == T0 and len(loaded.records) == report["total_records"]


def test_ingest_failure_is_recorded_and_keeps_old_data(tmp_path):
    store = PricingStore(tmp_path)
    (tmp_path / "p.html").write_text(PARTS, encoding="utf-8")
    ingest.run(store, ["samsung_in_parts"], {"samsung_in_parts": tmp_path / "p.html"}, now=T0)
    (tmp_path / "p.html").write_text("<html>new design</html>", encoding="utf-8")
    report = ingest.run(store, ["samsung_in_parts"], {"samsung_in_parts": tmp_path / "p.html"},
                        now=T0 + timedelta(days=1))
    assert report["sources"]["samsung_in_parts"]["status"] == "failed"
    ds = store.load()
    assert ds.sources["samsung_in_parts"].last_error and ds.sources["samsung_in_parts"].last_success_at == T0
    assert len(ds.records) > 0


def _fetcher(handler):
    return PoliteFetcher(user_agent="FixMyTech-test", transport=httpx.MockTransport(handler), min_interval_s=0,
                         sleep=lambda _: None)


def test_fetcher_respects_robots():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, text="ok")

    f = _fetcher(handler)
    assert f.get("https://example.test/public/page").text == "ok"
    with pytest.raises(RobotsDisallowedError):
        f.get("https://example.test/private/prices")


def test_fetcher_bounded_retries_and_no_robots_file():
    calls = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(1)
        return httpx.Response(503)

    from fixmytech.pricing.fetch import FetchError

    with pytest.raises(FetchError):
        _fetcher(handler).get("https://example.test/x")
    assert len(calls) == 3


def test_ingest_over_http_with_robots_block(tmp_path):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return httpx.Response(200, text=PARTS)

    report = ingest.run(PricingStore(tmp_path), ["samsung_in_parts"], {}, fetcher=_fetcher(handler), now=T0)
    assert report["sources"]["samsung_in_parts"]["status"] == "failed"
    assert "RobotsDisallowedError" in report["sources"]["samsung_in_parts"]["error"]


def test_manual_import_rules(tmp_path):
    store = PricingStore(tmp_path)
    rec = parse_parts_page(PARTS, T0, None).records[0]
    manual = rec.model_copy(update={"evidence": rec.evidence.model_copy(update={
        "source_id": "motorola_in_parts", "extraction_method": "manual transcription by operator"})})
    path = tmp_path / "m.json"
    path.write_text("[" + manual.model_dump_json() + "]", encoding="utf-8")
    assert ingest.import_manual(store, path)["status"] == "ok"
    apple = rec.model_copy(update={"evidence": rec.evidence.model_copy(update={
        "source_id": "apple_in_service", "extraction_method": "manual"})})
    path.write_text("[" + apple.model_dump_json() + "]", encoding="utf-8")
    assert ingest.import_manual(store, path)["status"] == "failed"  # prohibited source
    path.write_text("[" + rec.model_dump_json() + "]", encoding="utf-8")
    assert ingest.import_manual(store, path)["status"] == "failed"  # not marked manual


def test_exclusions_do_not_list_included_items():
    e = estimate(dataset(), "battery_replacement", "Battery", "samsung", "SM-S928B", "IN", PricingPolicy(), now=T0)
    assert "parts" not in e.exclusions and "labour" not in e.exclusions and "GST" in e.exclusions


def test_inspection_fee_for_bench_diagnosis():
    e = estimate(dataset(), "professional_diagnosis", "Bench diagnosis", "samsung", "SM-S928B", "IN", PricingPolicy(),
                 now=T0)
    assert e.status == "estimated" and e.scope == PriceScope.INSPECTION_FEE and e.low == Decimal(200)
    assert e.usable_for_decision is False  # an inspection fee is not a repair cost


def test_samsung_us_parser_and_estimate():
    from datetime import UTC, datetime
    from pathlib import Path

    from fixmytech.pricing.collectors import samsung_us
    from fixmytech.pricing.estimate import PricingPolicy, estimate
    from fixmytech.pricing.models import PricingDataset

    html = (Path(__file__).parents[1] / "fixtures/pricing/samsung_us_excerpt.html").read_text(encoding="utf-8")
    at = datetime(2026, 10, 21, tzinfo=UTC)
    res = samsung_us.parse_page(html, at, "sha")
    by = {(r.model_name, r.component): r for r in res.records}
    assert by[("Galaxy S26 Ultra", "service_screen")].amount == 289
    assert by[("Galaxy S26+", "service_screen")].tax.value == "excluded"
    assert ("Galaxy S26 Ultra", "service_screen_module") not in by  # "-" means not offered
    assert not res.rejected
    est = estimate(PricingDataset(records=res.records), "screen_replacement", "Screen", "Samsung",
                   "Galaxy S26 Ultra", "US", PricingPolicy(), now=at)
    assert est.status == "estimated" and est.low == 289 and est.currency == "USD"
    assert est.evidence[0].source_url.startswith("https://www.samsung.com/us/")


def test_samsung_us_parser_fails_loudly_on_redesign():
    from datetime import UTC, datetime

    import pytest as _pytest

    from fixmytech.pricing.collectors import samsung_us
    from fixmytech.pricing.collectors.base import CollectorError

    with _pytest.raises(CollectorError):
        samsung_us.parse_page("<html><table><tr><td>nothing</td></tr></table></html>", datetime.now(UTC), None)
