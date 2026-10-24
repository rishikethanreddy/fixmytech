"""FlipValue HTTP client.

* All FlipValue HTTP traffic goes through this module.
* Secrets come from configuration (environment), never from the browser.
* Bounded retries: only for connection errors, timeouts, 429 and 502/503/504, at most
  ``max_retries`` times, with capped backoff. Every attempt sends the same ``Idempotency-Key`` so the
  partner can de-duplicate; valuation is specified as side-effect free.
* Every failure is mapped to a typed ``FlipValueError`` with a stable ``code``. No fallback values are
  ever invented when FlipValue fails.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import ValidationError

from fixmytech.integrations.flipvalue.schemas import ErrorEnvelope, ValuationRequest, ValuationResponse

log = logging.getLogger("fixmytech.flipvalue")

Mode = Literal["live", "mock", "disabled"]
RETRY_STATUS = {429, 502, 503, 504}
MAX_RETRY_AFTER_S = 5.0


class FlipValueError(Exception):
    code = "flipvalue_error"
    retryable = False

    def __init__(self, message: str, status_code: int | None = None, request_id: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.request_id = request_id
        self.retry_after: float | None = None


class FlipValueDisabledError(FlipValueError):
    code = "flipvalue_disabled"


class FlipValueConfigError(FlipValueError):
    code = "flipvalue_misconfigured"


class FlipValueAuthError(FlipValueError):
    code = "flipvalue_auth_failed"


class FlipValueRateLimitedError(FlipValueError):
    code = "flipvalue_rate_limited"
    retryable = True


class FlipValueTimeoutError(FlipValueError):
    code = "flipvalue_timeout"
    retryable = True


class FlipValueUnavailableError(FlipValueError):
    code = "flipvalue_unavailable"
    retryable = True


class FlipValueUnsupportedDeviceError(FlipValueError):
    code = "flipvalue_unsupported_device"


class FlipValueRejectedRequestError(FlipValueError):
    code = "flipvalue_rejected_request"


class FlipValueInvalidResponseError(FlipValueError):
    code = "flipvalue_invalid_response"


@dataclass(frozen=True)
class FlipValueSettings:
    mode: Mode
    base_url: str | None
    api_key: str | None
    timeout_s: float = 8.0
    max_retries: int = 2
    backoff_s: float = 0.5
    api_version: str = "1"


@dataclass(frozen=True)
class ValuationOutcome:
    response: ValuationResponse
    source: Literal["flipvalue_live", "flipvalue_mock"]
    request_id: str
    attempts: int
    latency_ms: int


class FlipValueClient:
    def __init__(self, settings: FlipValueSettings, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.settings = settings
        self._transport = transport
        self._sleep = sleep

    @property
    def source(self) -> Literal["flipvalue_live", "flipvalue_mock"]:
        return "flipvalue_mock" if self.settings.mode == "mock" else "flipvalue_live"

    def status(self) -> dict[str, object]:
        s = self.settings
        return {"mode": s.mode, "configured": bool(s.base_url and s.api_key) or s.mode == "disabled",
                "base_url_host": httpx.URL(s.base_url).host if s.base_url else None,
                "api_version": s.api_version, "timeout_s": s.timeout_s, "max_retries": s.max_retries}

    def _check_config(self) -> None:
        s = self.settings
        if s.mode == "disabled":
            raise FlipValueDisabledError("FlipValue integration is disabled in configuration")
        if not s.base_url or not s.api_key:
            raise FlipValueConfigError("FLIPVALUE_BASE_URL and FLIPVALUE_API_KEY must be set")
        url = httpx.URL(s.base_url)
        if s.mode == "live" and url.scheme != "https" and url.host not in ("localhost", "127.0.0.1"):
            raise FlipValueConfigError("FlipValue live mode requires an https:// base URL")

    def get_valuation(self, req: ValuationRequest) -> ValuationOutcome:
        self._check_config()
        s = self.settings
        request_id = str(uuid.uuid4())
        headers = {
            "X-API-Key": s.api_key or "",
            "Accept": "application/json",
            "X-Request-ID": request_id,
            "Idempotency-Key": request_id,
            "FlipValue-API-Version": s.api_version,
            "User-Agent": "FixMyTech/0.2",
        }
        body = req.model_dump(mode="json", exclude_none=True)
        attempts = 0
        started = time.monotonic()
        last_error: FlipValueError | None = None
        with httpx.Client(base_url=s.base_url or "", timeout=s.timeout_s, transport=self._transport) as http:
            while attempts <= s.max_retries:
                attempts += 1
                try:
                    resp = http.post("/v1/valuations", json=body, headers=headers)
                    outcome = self._handle(resp, request_id)
                    latency = int((time.monotonic() - started) * 1000)
                    log.info("flipvalue ok request_id=%s attempts=%d latency_ms=%d", request_id, attempts, latency)
                    if outcome.mock and self.settings.mode == "live":
                        raise FlipValueInvalidResponseError(
                            "Configured as live, but the service identified itself as a mock", request_id=request_id)
                    return ValuationOutcome(outcome, self.source, request_id, attempts, latency)
                except httpx.TimeoutException:
                    last_error = FlipValueTimeoutError("FlipValue did not respond in time", request_id=request_id)
                except httpx.TransportError:
                    last_error = FlipValueUnavailableError("Could not connect to FlipValue", request_id=request_id)
                except FlipValueError as exc:
                    last_error = exc
                log.warning("flipvalue attempt=%d failed code=%s request_id=%s", attempts, last_error.code, request_id)
                if not last_error.retryable or attempts > s.max_retries:
                    break
                delay = min(s.backoff_s * (2 ** (attempts - 1)), MAX_RETRY_AFTER_S)
                if last_error.retry_after is not None:
                    delay = min(max(delay, last_error.retry_after), MAX_RETRY_AFTER_S)
                self._sleep(delay)
        assert last_error is not None
        raise last_error

    def _handle(self, resp: httpx.Response, request_id: str) -> ValuationResponse:
        code = resp.status_code
        if code == 200:
            try:
                return ValuationResponse.model_validate(resp.json())
            except (ValueError, ValidationError) as exc:
                raise FlipValueInvalidResponseError("FlipValue returned a response that does not match the contract",
                                                    code, request_id) from exc
        message = self._error_message(resp)
        if code in (401, 403):
            raise FlipValueAuthError("FlipValue rejected the API key", code, request_id)
        if code == 404:
            raise FlipValueUnsupportedDeviceError(message or "FlipValue cannot value this device", code, request_id)
        if code in (400, 422):
            raise FlipValueRejectedRequestError(message or "FlipValue rejected the request", code, request_id)
        if code == 429:
            err = FlipValueRateLimitedError("FlipValue rate limit reached", code, request_id)
            try:
                err.retry_after = float(resp.headers.get("Retry-After", ""))
            except ValueError:
                pass
            raise err
        if code in RETRY_STATUS or code >= 500:
            err5 = FlipValueUnavailableError(f"FlipValue is unavailable (HTTP {code})", code, request_id)
            err5.retryable = code in RETRY_STATUS
            raise err5
        raise FlipValueInvalidResponseError(f"Unexpected HTTP {code} from FlipValue", code, request_id)

    @staticmethod
    def _error_message(resp: httpx.Response) -> str | None:
        try:
            return ErrorEnvelope.model_validate(resp.json()).error.message[:200]
        except (ValueError, ValidationError):
            return None
