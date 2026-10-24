# Data and model card

## Summary

| | |
|---|---|
| Production diagnostic method | Rule-based evidence fusion (`data/rules.json`), expert-set weights |
| ML model in production | **None.** `FIXMYTECH_ML_ENABLED=false` by default |
| Real labelled repair data available | **No** (ML pipeline unchanged in v0.2; rule engine and guided tests strengthened instead) |
| Synthetic data | Generator in `ml/synthetic.py`, for software tests and demos only |
| Reported accuracy | **None.** No metric on real cases exists, so none is claimed |

## Knowledge base

* Taxonomy, rules and questions versioned `2026.10.1`; the version is returned with every diagnosis.
* Rules were written from general smartphone repair knowledge and Android platform behaviour; they have **not** been
  validated against technician-confirmed outcomes.

## Repair price data

* **Real, source-attributed:** Samsung India official spare-part prices (179 models, 1,075 records) and official labour
  charges (3 tiers), retrieved by `fixmytech.pricing.ingest`; last successful retrieval 2026-10-19 07:07 UTC. Stored in
  `var/pricing/` (not committed). Scope: parts and labour **before GST**; "premium colour, maximum storage". Display in
  production requires source approval (`FIXMYTECH_PRICING_ALLOWED_SOURCES`) after legal review. See PRICING_SOURCES.md.
* **Demo fixtures:** `data/demo_repair_prices.csv` (generic ranges, `DEMO_FIXTURE_NOT_MARKET_DATA`), used only with
  `FIXMYTECH_PRICING_DEMO=true`, refused in production and labelled DEMO everywhere.
* **Not available:** independent-shop prices, completed-repair transactions, other brands and markets.

## Question-strategy simulation

`fixmytech.domain.question_eval` compares fixed-order, rule-utility and expected-entropy selection on 11 developer-written
personas: 11/11, 11/11 and 10/11 intended faults ranked first with 6.0, 4.9 and 4.4 mean questions. The personas
share the rule authors' assumptions, so this is a behavioural check of the rule base, **not** an accuracy estimate.

## Training dataset schema (for future real data)

CSV, one row per technician-verified case.

| Column | Type | Meaning |
|---|---|---|
| `case_id` | string, unique | Case identifier |
| `device_group` | string | Grouping key for leakage control (same device/customer must share a value) |
| `collected_at` | ISO date | When the case was recorded |
| `label_fault` | taxonomy fault id | Fault **confirmed by a technician** |
| `label_source` | `technician_verified` \| `synthetic` | Provenance |
| feature columns | float, blank = missing | Exactly `ml.features.feature_names()`: numeric observation features (`battery_capacity_pct`, `storage_free_pct`, `app_instability_7d`, ...), boolean observation features, `ans__<question>__<option>` 0/1 indicators |

Features are produced by `ml.features.facts_to_row`, the same function used at inference, so data exported from real
FixMyTech sessions (once outcomes are recorded) has identical features.

## Training and evaluation protocol (`python -m fixmytech.ml.train --data cases.csv --out models/x`)

* Pipeline: median imputation with missing-indicators → standardisation → multinomial logistic regression
  (class-balanced). Chosen as an interpretable baseline appropriate for small tabular data.
* Validation: `GroupKFold` on `device_group` (no device in both train and test).
* Baseline: most-frequent class, evaluated identically.
* Metrics: accuracy, macro-F1, balanced accuracy, multi-class Brier score, top-class reliability table.
* Requirements: ≥ 5 cases per class, ≥ 2 groups. Metadata records dataset hash, provenance, metrics, `calibrated=false`.
* `validated=true` only if **all rows are technician-verified** and the model beats the baseline on macro-F1.

## Inference policy

* The model output is attached separately as `diagnosis.ml` and labelled; `used_in_ranking` is always `false` in this
  version. Scores are labelled "uncalibrated model score" unless calibration was evaluated.
* A model trained on any synthetic rows is `experimental_unvalidated` and must never be presented as diagnostic evidence.

## What is needed before a production model

1. A few hundred technician-confirmed cases per target fault, collected with FixMyTech's scan + questionnaire,
   with consent for this use.
2. Recording of the confirmed outcome per session (not yet implemented: a technician-feedback endpoint).
3. Evaluation on a held-out time period as well as grouped CV; calibration (e.g. isotonic) and reliability analysis.
4. A documented decision on how (if at all) model output is fused with rules, evaluated against rules-only.
5. Monitoring for drift across Android versions and manufacturers.
