"""Types shared by all collectors."""

from __future__ import annotations

from dataclasses import dataclass, field

from fixmytech.pricing.models import PriceRecord


class CollectorError(Exception):
    """The source structure was not recognized (likely a site change)."""


@dataclass
class ParseResult:
    records: list[PriceRecord] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    items_seen: int = 0
