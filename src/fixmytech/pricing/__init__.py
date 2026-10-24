"""Repair-price intelligence: source-attributed price records, ingestion, and evidence-backed estimates.

Design rules (see docs/PRICING_SOURCES.md):
* Every number shown to a customer traces back to a source URL and a retrieval timestamp.
* Prices of different scope (parts only, labour only, parts + labour, all-inclusive) are never mixed.
* Missing evidence produces ``insufficient_pricing_data``, never a generic or invented price.
* Demo fixtures exist only for development, are labelled as such, and are refused in production.
"""
