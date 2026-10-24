# Repair price sources

FixMyTech shows repair prices only when they trace back to a named source, URL and retrieval date. When no such
evidence exists for the customer's exact model and repair, it says `insufficient_pricing_data` and links to official
sources instead of guessing. The generic placeholder ranges used in v0.1 are no longer shown in normal results.

## Source review (2026-10-19)

| Source id | Organization / URL | robots.txt (`User-agent: *`) | Terms | Data access found | Decision |
|---|---|---|---|---|---|
| `samsung_in_parts` | Samsung India, [spare part pricing](https://www.samsung.com/in/support/spare-part-pricing-list-for-repair/) | `/in/support/` not disallowed | [Legal page](https://www.samsung.com/in/info/legal/): users may save content on their equipment but "not transfer the aforesaid ... to third parties" | Server HTML embeds `var myProducts=[...]` (179 models, part prices as "1,11,960") | **Collector built.** Usage policy `legal_review_required` |
| `samsung_in_labour` | Samsung India, [out-of-warranty repair charges](https://www.samsung.com/in/support/out-of-warranty-repair-charges/) | allowed | same as above | Server HTML embeds `const data = {"Mobile Devices": [...]}` (3 tiers × 5 charges); page dated 2026-09-25 | **Collector built.** `legal_review_required` |
| `motorola_in_parts` | Motorola India, [out-of-warranty parts price list](https://en-in.support.motorola.com/app/answers/detail/a_id/133816/~/india-out-of-warranty-parts-price-list) | **`Disallow: /`** | not reviewed | - | **No automation.** `manual_only`: human-curated records may be imported with attribution |
| `apple_in_service` | Apple India, [iPhone repair](https://support.apple.com/en-in/iphone/repair) | repair pages allowed | [Website terms](https://www.apple.com/legal/internet-services/terms/site.html) prohibit robots, spiders, page-scrapes "or any similar or equivalent manual process" | JSON pricing API used by the page | **Not collected, not transcribed.** `prohibited`. Customers get a link to Apple's own estimator |
| `oneplus_in_pricing` | OnePlus India, [repair pricing](https://www.oneplus.in/support/repair-pricing) | no robots.txt (404) | terms page not found | per-model detail pages | Candidate only; not implemented |
| `google_in_estimator`, `google_us_estimator` | Google Store, [repair cost estimator](https://store.google.com/in/repair-cost-estimator?hl=en-IN) (IN and US) | estimator page and its data endpoint allowed | Page states prices are out-of-warranty estimates, "may exclude tax", final cost set after inspection, delivery fees may apply, authorised providers set their own fees | Model list in the server HTML; per-model prices from the page's own public `batchexecute` call (`QQr9wd`), no login | **Collector built** (`pricing/collectors/google_store.py`). `legal_review_required`. Records are `parts_and_labour`, tax `unknown` |
| `realme_in_parts` | realme India | - | User agreement forbids scraping | - | **`prohibited`**, not collected |
| OPPO, Xiaomi (India) | official price pages | - | - | Return HTTP 403 to an identified bot | **Not bypassed.** Official links only |

The registry lives in `src/fixmytech/pricing/sources.py`. Re-check robots.txt and terms before enabling any source
in production, and at least every six months.

### Production gate

`FIXMYTECH_PRICING_ALLOWED_SOURCES` lists the source ids an operator has approved for display. It is **required in
production** (the server refuses to start without it). **No source is approved by default.** Samsung's terms restrict
redistribution, so enabling `samsung_in_*` in production needs Samsung's permission or legal advice. In development the
variable may be unset; the UI then shows a collapsed "Operator note (development only)" about the pending review.

### Coverage by brand (reviewed 2026-10-21)

Every brand was checked with the identified bot user agent (robots.txt first, one request per page, 5 s apart).
"Collected" means a live ingestion ran; nothing is collected where robots.txt, terms or access controls say no, and
403 responses were **not** bypassed.

| Brand | Status | Evidence |
|---|---|---|
| Samsung | **Collected**: India (parts + labour), US (screen repair, excl. taxes: 98 records, 2026-10-21; 5 ambiguous promotional foldable rows rejected) | robots allow `/in/support/`, `/us/support/`; terms restrict redistribution, so `legal_review_required` |
| Google (Pixel) | **Collected**: India and US estimator (988 records, 2026-10-19) | robots allow the estimator and its data call; `legal_review_required` |
| OnePlus | **Manual only**: pages exist (India, US) but are client-rendered; prices come from a private app API | HTML contains only template placeholders; no robots.txt (404) and no terms found permitting the private API, so it is not called |
| Apple | **Prohibited** | Website terms ban robots/page-scrapes; official estimator link only |
| realme | **Prohibited** | User agreement: "must not scrape or systematically extract Website Content without our written permission" |
| Motorola | **Manual only** | robots `Disallow: /` on the support portal |
| OPPO, Xiaomi / Redmi / POCO, Sony | **Blocked** | `robots.txt` itself returns HTTP 403 to the identified bot; no access-control bypass attempted. No customer link added because the exact price pages could not be verified |
| vivo, iQOO | **No public price list found** | robots allow `/in/support/`; guessed repair-charge URLs return 404, and only warranty terms are published; no prices exist to collect |
| Nothing | **No public price list found** | robots allow; `repair`/`repair-pricing` pages return 404 (India and global) |
| Huawei, Honor | **No public price list found** | robots allow `/support/`; no repair-price page (404 / support landing only) |
| ASUS, HMD (Nokia), Infinix, Tecno, Lava, Micromax | **No public price list found** | robots allow; support pages contain no repair prices or return 404 |

For brands without a collector, estimates show `insufficient_pricing_data`, official links where verified, and the
customer can enter a shop quote. A human may curate prices from an official page with attribution through
`--import-manual` (see Ingestion). New sources must be reviewed the same way before a collector is written; do not
add a collector for a source whose robots.txt or terms forbid it.

## Data model (`src/fixmytech/pricing/models.py`)

Source evidence and normalized values are stored separately but together:

* **`SourceEvidence`**: `source_id`, `source_url`, `organization`, `source_type` (official parts list, official labour
  schedule, official service estimate, authorized provider, independent shop, customer quote, completed repair, demo
  fixture), `retrieved_at`, `document_sha256`, `published_at` (when the source states it), `extraction_method`,
  `raw_label` and `raw_value` **exactly as written by the source**, `raw_context` (e.g. series, model number).
* **`PriceRecord`**: manufacturer, model name and code (or `applies_to_group` for tiered charges), region, currency,
  normalized `component`, `amount`/`amount_high`, `price_point` (exact / starting-from / range), `scope` (parts only,
  labour only, inspection fee, parts + labour, all-inclusive, unknown), `tax` (excluded / included / unknown),
  `includes`, `excludes`, `limitations`, plus `first_seen_at`, `last_verified_at`, `last_changed_at`.

Unknown attributes stay unknown (`None`/`"unknown"`); nothing is filled in by assumption.

## Ingestion (`python -m fixmytech.pricing.ingest`)

Run manually or from a scheduler (e.g. weekly Windows Task Scheduler / cron). It is **never** run per customer request.

* Checks robots.txt for its own user agent before each fetch; identifies itself
  (`FixMyTech-PriceBot/<version> (contact: $FIXMYTECH_PRICING_CONTACT)`); waits ≥5 s between requests to a host;
  30 s timeouts; at most 2 retries, only on 5xx/connection errors. No cookies, logins, CAPTCHA solving or browser
  automation (the Samsung data is in the server-rendered HTML).
* Source-specific parsing is isolated (`pricing/collectors/samsung_in.py`). If the expected embedded data is missing,
  the run reports `failed` (a site redesign is detected, not silently ignored) and **keeps the previous data with its
  dates**.
* Validation: model-number format, Indian-format numbers only, plausible ranges (₹10 to ₹5,00,000 for parts), known
  labour charge names; `N/A` means "not offered". Every rejection is listed with a reason.
* De-duplication by deterministic record id; per source: `added`, `updated` (value changed, `last_changed_at` moves),
  `unchanged` (`last_verified_at` moves), `not_seen` (no longer listed: kept but ages out), `duplicates`, `rejected`.
* Output: `<pricing_dir>/dataset.json` (atomic write) and `<pricing_dir>/reports/<timestamp>.json`.
* `--from-file SOURCE=PATH` parses a saved copy; `--import-manual FILE` imports human-curated records (must be marked
  `extraction_method: manual...` and come from a known, non-prohibited source).

### Retrieval log (this environment)

| Date (UTC) | Run | Result |
|---|---|---|
| 2026-10-19 06:26:15 | first live run | samsung_in_parts: 179 models, 1,075 records added, 0 rejected; samsung_in_labour: 15 added |
| 2026-10-19 07:06:39 | second live run | 1,075 + 15 `unchanged` (de-duplication and verification timestamps confirmed) |
| 2026-10-19 07:07:43 | third live run | 1,075 + 15 `unchanged`; normalization refresh confirmed |
| 2026-10-19 10:28:54 | Google Store live run | google_in_estimator: 27 Pixel models, 481 records; google_us_estimator: 28 models, 507 records; 0 rejected. Dataset total 2,078 records |
| 2026-10-21 | Samsung US live run | samsung_us_screen: 159 rows seen, 98 records added, 5 rejected (ambiguous promotional footnotes). Dataset total 2,176 records |

The dataset is written to `var/pricing/` (git-ignored). It is **not committed**, to avoid redistributing Samsung's
list; every deployment must run the ingestion itself. Test fixtures under `tests/fixtures/pricing/` are small excerpts
(4 of 179 models plus the labour block) kept only to test the parsers against the real page structure.

## Estimates (`src/fixmytech/pricing/estimate.py`)

1. **Model matching:** exact model code, then exact model name (with a "check your model number" note), then the same
   model's regional variant (e.g. SM-S928U vs SM-S928B, labelled *Approximation*). Related models (S24 vs S24 Ultra)
   are never used.
   Typed brand names are normalized ("Pixel" → Google, "Galaxy" → Samsung, ...), and when the typed manufacturer
   matches no source, a name-only match is tried ("Pixel" + "6A" → "Pixel 6a"); a model *code* is never matched across
   manufacturers. The scanned device identity overrides typed values.
2. **Repair to component:** screen → display assembly, else Google's screen service, else OLED panel ("screen
   component repair", noted); battery → main battery or battery service; camera → range across the listed rear cameras,
   or Google's rear/front camera service range; charging port, audio and liquid damage → Google's matching service
   estimates. Samsung has no component for those three, so they return `insufficient_pricing_data` there.
   Google's estimates are already parts + labour, so they are shown as "the manufacturer's own repair estimate" with
   tax marked as unknown ("may not include tax").
3. **Scopes are never mixed:** the official part price plus the official labour charge ("L2 Parts used") for the same
   manufacturer and service tier gives **"official part + labour, before tax"**. Without labour data the result is
   `parts_only`, which cannot be used as a repair cost in the Decision step. The bench-diagnosis option uses the
   official inspection fee (`inspection_fee` scope, also not a repair cost).
4. **Freshness:** `fresh` ≤ 30 days since last verification, `aging` ≤ 90, then `stale` (configurable). The date is
   always shown; stale data is labelled, never presented as current.
5. **Output:** amount or range, currency, scope, tax treatment, model-match quality, freshness, every evidence line
   (source text, URL, retrieval date, page date), exclusions (e.g. GST, consumables, pickup/drop), limitations, a
   "why a shop's quote may differ" explanation, and official links.

## Demo fixtures

`src/fixmytech/data/demo_repair_prices.csv` (generic ranges, `source=DEMO_FIXTURE_NOT_MARKET_DATA`) is used only when
`FIXMYTECH_PRICING_DEMO=true`. It is refused in production, labelled `data_kind: demo_fixture`, shown under a red
"DEMO price - not real" banner, and real evidence always takes precedence.

## Customer quotes

Quotes record amount, currency and whether tax is included. They are compared only with estimates of the same currency
and comparable scope: within / below / above the official reference (noting the reference is before tax), "below the
part price alone" for parts-only references, otherwise "not comparable".

## Known gaps

* Automated data covers Samsung (India, US) and Google Pixel (India and US). Samsung US rows have no model number, so they match by marketing name (a scan that reports only a model code such as SM-S928U will not match them). Apple, Motorola, OnePlus, OPPO, realme,
  Xiaomi and other markets show `insufficient_pricing_data` with official links where known.
* No independent-shop prices and no verified completed-repair transactions yet.
* Samsung prices are for "premium colour and maximum storage"; the exact unit may differ.
* Legal review for displaying Samsung data in production is outstanding.
