"""Polite, robots-aware HTTP retrieval for price ingestion.

* Checks robots.txt for our user agent before every fetch (cached per host per run).
* Identifies itself with a descriptive User-Agent including a contact address.
* At most one request per host every ``min_interval_s`` seconds; bounded retries on transient errors.
* Never sends cookies or credentials, never solves CAPTCHAs, never retries 401/403/429-with-block.
"""

from __future__ import annotations

import hashlib
import logging
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("fixmytech.pricing.fetch")


class FetchError(Exception):
    pass


class RobotsDisallowedError(FetchError):
    pass


@dataclass
class FetchedDocument:
    url: str
    status: int
    text: str
    sha256: str
    fetched_at_monotonic: float


@dataclass
class PoliteFetcher:
    user_agent: str
    timeout_s: float = 30.0
    max_retries: int = 2
    min_interval_s: float = 5.0
    transport: httpx.BaseTransport | None = None
    sleep: object = time.sleep
    _robots: dict[str, urllib.robotparser.RobotFileParser] = field(default_factory=dict)
    _last_hit: dict[str, float] = field(default_factory=dict)

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout_s, transport=self.transport, follow_redirects=True,
                            headers={"User-Agent": self.user_agent, "Accept-Language": "en-IN,en;q=0.8"})

    def _wait_turn(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            delay = self.min_interval_s - (time.monotonic() - last)
            if delay > 0:
                self.sleep(delay)  # type: ignore[operator]
        self._last_hit[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        rp = self._robots.get(base)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            with self._client() as c:
                self._wait_turn(parts.netloc)
                r = c.get(f"{base}/robots.txt")
            if r.status_code in (401, 403):
                rp.parse(["User-agent: *", "Disallow: /"])  # access to robots.txt denied: assume no permission
            elif r.status_code >= 400:
                rp.parse([])  # no robots.txt: no restrictions declared
            else:
                rp.parse(r.text.splitlines())
            self._robots[base] = rp
        return rp.can_fetch(self.user_agent, url)

    def get(self, url: str) -> FetchedDocument:
        return self._request("GET", url)

    def post_form(self, url: str, params: dict[str, str], data: dict[str, str]) -> FetchedDocument:
        """POST a form (used for a site's own public data endpoint, e.g. a price estimator), robots-checked."""
        return self._request("POST", url, params=params, data=data)

    def _request(self, method: str, url: str, params: dict[str, str] | None = None,
                 data: dict[str, str] | None = None) -> FetchedDocument:
        if not self.allowed(url):
            raise RobotsDisallowedError(f"robots.txt disallows {url}")
        host = urlsplit(url).netloc
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._wait_turn(host)
            try:
                with self._client() as c:
                    r = c.request(method, url, params=params, data=data)
            except httpx.TransportError as exc:
                last_exc = exc
                log.warning("fetch attempt %d failed for %s: %s", attempt + 1, url, type(exc).__name__)
                continue
            if r.status_code == 200:
                return FetchedDocument(url=str(r.url), status=200, text=r.text,
                                       sha256=hashlib.sha256(r.content).hexdigest(),
                                       fetched_at_monotonic=time.monotonic())
            if r.status_code in (500, 502, 503, 504):
                last_exc = FetchError(f"HTTP {r.status_code}")
                continue
            raise FetchError(f"HTTP {r.status_code} for {url}")
        raise FetchError(f"giving up on {url}: {last_exc}")
