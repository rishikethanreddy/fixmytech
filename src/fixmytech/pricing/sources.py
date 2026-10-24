"""Registry of pricing sources and their usage policy.

The policy reflects what was found when each source was reviewed (robots.txt and website terms, 2026-10-19).
It must be re-checked periodically; see docs/PRICING_SOURCES.md.
"""

from __future__ import annotations

from pydantic import BaseModel

from fixmytech.pricing.models import SourceType, UsagePolicy


class SourceDefinition(BaseModel):
    source_id: str
    organization: str
    url: str
    region: str
    currency: str
    manufacturers: tuple[str, ...]
    source_type: SourceType
    usage_policy: UsagePolicy
    policy_notes: str
    collector: str | None  # module name under fixmytech.pricing.collectors, or None
    customer_link_label: str | None = None  # shown to customers as "check the official source"


SOURCES: tuple[SourceDefinition, ...] = (
    SourceDefinition(
        source_id="samsung_in_parts",
        organization="Samsung India",
        url="https://www.samsung.com/in/support/spare-part-pricing-list-for-repair/",
        region="IN",
        currency="INR",
        manufacturers=("samsung",),
        source_type=SourceType.OFFICIAL_PARTS_LIST,
        usage_policy=UsagePolicy.LEGAL_REVIEW_REQUIRED,
        policy_notes=("robots.txt (User-agent: *) does not disallow /in/support/. Samsung's legal page says users may "
                      "save content on their equipment but may not transfer it to third parties; displaying prices "
                      "to FixMyTech customers needs legal review or Samsung's permission before production use."),
        collector="samsung_in",
        customer_link_label="Samsung India spare part prices",
    ),
    SourceDefinition(
        source_id="samsung_in_labour",
        organization="Samsung India",
        url="https://www.samsung.com/in/support/out-of-warranty-repair-charges/",
        region="IN",
        currency="INR",
        manufacturers=("samsung",),
        source_type=SourceType.OFFICIAL_LABOUR_SCHEDULE,
        usage_policy=UsagePolicy.LEGAL_REVIEW_REQUIRED,
        policy_notes="Same site and terms as samsung_in_parts.",
        collector="samsung_in",
        customer_link_label="Samsung India out-of-warranty repair charges",
    ),
    SourceDefinition(
        source_id="google_in_estimator",
        organization="Google Store India",
        url="https://store.google.com/in/repair-cost-estimator?hl=en-IN",
        region="IN",
        currency="INR",
        manufacturers=("google",),
        source_type=SourceType.OFFICIAL_SERVICE_ESTIMATE,
        usage_policy=UsagePolicy.LEGAL_REVIEW_REQUIRED,
        policy_notes=("robots.txt allows /in/repair-cost-estimator and the /_/ data endpoint; Google's Terms of "
                      "Service prohibit automated access only where it violates such machine-readable instructions. "
                      "Google keeps rights in its content, so displaying the estimates in production needs review."),
        collector="google_store",
        customer_link_label="Google Store repair pricing guide (India)",
    ),
    SourceDefinition(
        source_id="google_us_estimator",
        organization="Google Store US",
        url="https://store.google.com/us/repair-cost-estimator?hl=en-US",
        region="US",
        currency="USD",
        manufacturers=("google",),
        source_type=SourceType.OFFICIAL_SERVICE_ESTIMATE,
        usage_policy=UsagePolicy.LEGAL_REVIEW_REQUIRED,
        policy_notes="Same site and terms as google_in_estimator.",
        collector="google_store",
        customer_link_label="Google Store repair pricing guide (US)",
    ),
    SourceDefinition(
        source_id="realme_in_parts",
        organization="realme India",
        url="https://www.realme.com/in/support/spare-parts-price",
        region="IN",
        currency="INR",
        manufacturers=("realme",),
        source_type=SourceType.OFFICIAL_PARTS_LIST,
        usage_policy=UsagePolicy.PROHIBITED,
        policy_notes=("realme's User Agreement: 'You must also not scrape or systematically extract Website Content "
                      "without our written permission'. Not collected; customers are linked to the official page."),
        collector=None,
        customer_link_label="realme India spare parts prices",
    ),
    SourceDefinition(
        source_id="motorola_in_parts",
        organization="Motorola India",
        url="https://en-in.support.motorola.com/app/answers/detail/a_id/133816/~/india-out-of-warranty-parts-price-list",
        region="IN",
        currency="INR",
        manufacturers=("motorola",),
        source_type=SourceType.OFFICIAL_PARTS_LIST,
        usage_policy=UsagePolicy.MANUAL_ONLY,
        policy_notes=("robots.txt has 'User-agent: * Disallow: /'. No automated collector. Prices may only enter "
                      "FixMyTech through manual, source-attributed curation (data/pricing manual records)."),
        collector=None,
        customer_link_label="Motorola India out-of-warranty parts price list",
    ),
    SourceDefinition(
        source_id="apple_in_service",
        organization="Apple India",
        url="https://support.apple.com/en-in/iphone/repair",
        region="IN",
        currency="INR",
        manufacturers=("apple",),
        source_type=SourceType.OFFICIAL_SERVICE_ESTIMATE,
        usage_policy=UsagePolicy.PROHIBITED,
        policy_notes=("Apple's website terms prohibit robots, spiders, page-scrapes 'or any similar or equivalent "
                      "manual process' to acquire or copy site content. FixMyTech does not store Apple prices; it "
                      "links customers to Apple's own estimate tool."),
        collector=None,
        customer_link_label="Apple's official iPhone repair cost estimator",
    ),
    SourceDefinition(
        source_id="oneplus_in_pricing",
        organization="OnePlus India",
        url="https://www.oneplus.in/support/repair-pricing",
        region="IN",
        currency="INR",
        manufacturers=("oneplus",),
        source_type=SourceType.OFFICIAL_PARTS_LIST,
        usage_policy=UsagePolicy.MANUAL_ONLY,
        policy_notes=("Reviewed 2026-10-21. No robots.txt (HTTP 404). The page is a client-rendered app: the HTML "
                      "holds only template placeholders and prices come from OnePlus's private application API "
                      "(supportapi.oneplus.in router). That API is undocumented and no terms permitting its use were "
                      "found, so it is not called. Prices may enter only through manual, attributed curation."),
        collector=None,
        customer_link_label="OnePlus India repair pricing",
    ),
    SourceDefinition(
        source_id="oneplus_us_pricing",
        organization="OnePlus United States",
        url="https://www.oneplus.com/us/support/repair-pricing",
        region="US",
        currency="USD",
        manufacturers=("oneplus",),
        source_type=SourceType.OFFICIAL_PARTS_LIST,
        usage_policy=UsagePolicy.MANUAL_ONLY,
        policy_notes="Same client-rendered application and private API as oneplus_in_pricing (reviewed 2026-10-21).",
        collector=None,
        customer_link_label="OnePlus US repair pricing",
    ),
    SourceDefinition(
        source_id="samsung_us_screen",
        organization="Samsung United States",
        url="https://www.samsung.com/us/support/cracked-screen-repair/",
        region="US",
        currency="USD",
        manufacturers=("samsung",),
        source_type=SourceType.OFFICIAL_SERVICE_ESTIMATE,
        usage_policy=UsagePolicy.LEGAL_REVIEW_REQUIRED,
        policy_notes=("robots.txt (User-agent: *) allows /us/support/. Server-rendered tables of suggested screen "
                      "repair prices (excluding taxes and fees). Same Samsung terms as the India pages: displaying "
                      "the prices to customers in production needs legal review or Samsung's permission."),
        collector="samsung_us",
        customer_link_label="Samsung US screen repair pricing",
    ),
)

SOURCES_BY_ID = {s.source_id: s for s in SOURCES}


def official_links(manufacturer: str | None, region: str) -> list[dict[str, str]]:
    """Official pages a customer can check themselves, for their brand and region."""
    man = (manufacturer or "").strip().lower()
    return [{"label": s.customer_link_label or s.organization, "url": s.url, "organization": s.organization}
            for s in SOURCES if s.region == region and man in s.manufacturers and s.customer_link_label]
