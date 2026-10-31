"""HTTP routes: /api/v1/..."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from fixmytech import __version__
from fixmytech.core.capabilities import CHECKS, CHECKS_BY_ID, NOT_ASSESSABLE
from fixmytech.core.observations import Observation, ScanResult
from fixmytech.domain.guided import InvalidTestResultError, TestResultInput, recommended_tests, validate_result
from fixmytech.domain.questionnaire import InvalidAnswerError, NextQuestion, next_question, validate_answer
from fixmytech.domain.repair import QuoteInput
from fixmytech.integrations.flipvalue.client import (
    FlipValueAuthError,
    FlipValueConfigError,
    FlipValueDisabledError,
    FlipValueError,
    FlipValueRateLimitedError,
    FlipValueRejectedRequestError,
    FlipValueTimeoutError,
    FlipValueUnsupportedDeviceError,
)
from fixmytech.integrations.flipvalue.market import FlipValueNoDataError, MarketClient
from fixmytech.integrations.flipvalue.schemas import ConditionGrade
from fixmytech.pricing.sources import SOURCES
from fixmytech.server import partner as partner_svc
from fixmytech.server import services as svc
from fixmytech.server.db import SCHEMA_VERSION, DiagnosticSession
from fixmytech.server.errors import ApiError
from fixmytech.server.security import hash_token, new_token, token_matches

log = logging.getLogger("fixmytech.api")
router = APIRouter(prefix="/api/v1")

DISCLAIMER = ("FixMyTech provides decision support based on limited evidence. It does not prove that any component "
              "is defective. Confirm hardware faults with a qualified technician before paying for repairs.")


def state(request: Request) -> svc.AppState:
    return request.app.state.fmt


def db_session(request: Request) -> Iterator[Session]:
    yield from state(request).db.session()


StateDep = Annotated[svc.AppState, Depends(state)]
DbDep = Annotated[Session, Depends(db_session)]


def authorized_session(session_id: str, db: DbDep,
                       authorization: Annotated[str | None, Header()] = None) -> DiagnosticSession:
    """Capability-token access control: the token returned at creation is required for every access.

    Unknown ids and wrong tokens get the same 404 so session ids cannot be probed.
    """
    try:
        uuid.UUID(session_id)
    except ValueError:
        raise ApiError(404, "session_not_found", "Session not found or access denied.") from None
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "missing_token", "This session requires its access token.")
    rec = db.get(DiagnosticSession, session_id)
    if rec is None or not token_matches(authorization.removeprefix("Bearer ").strip(), rec.token_hash):
        raise ApiError(404, "session_not_found", "Session not found or access denied.")
    if rec.expires_at.replace(tzinfo=rec.expires_at.tzinfo or UTC) < svc.utc_now():
        raise ApiError(404, "session_expired", "This session has expired and its data was scheduled for deletion.")
    return rec


SessionDep = Annotated[DiagnosticSession, Depends(authorized_session)]


def _save(db: Session, rec: DiagnosticSession) -> None:
    rec.updated_at = svc.utc_now()
    db.add(rec)
    db.commit()


# ---------------------------------------------------------------------------- system & catalog


@router.get("/health")
def health(st: StateDep) -> dict[str, Any]:
    try:
        db_ok = st.db.ping()
    except Exception:  # noqa: BLE001 - health must report, not raise
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "version": __version__, "database": db_ok,
            "knowledge_version": st.kb.version, "flipvalue": st.flipvalue.status(),
            "ml_enabled": st.settings.ml_enabled}


@router.get("/ready")
def ready(st: StateDep, response: Response) -> dict[str, Any]:
    """Readiness: can this instance serve customers correctly? (Liveness is /health.)"""
    checks: dict[str, Any] = {}
    try:
        checks["database"] = st.db.ping()
        checks["schema_current"] = st.db.schema_version() == SCHEMA_VERSION
    except Exception:  # noqa: BLE001 - readiness reports, never raises
        checks["database"], checks["schema_current"] = False, False
    pricing = st.pricing.status()
    checks["pricing_records"] = pricing["records"]
    checks["pricing_sources_failed"] = sorted(sid for sid, src in pricing["sources"].items() if src.get("last_error"))
    fv = st.flipvalue.status()
    checks["flipvalue"] = {"mode": fv["mode"], "configured": fv["configured"]}
    ok = checks["database"] and checks["schema_current"] and (fv["configured"] or fv["mode"] == "disabled")
    warnings = []
    if pricing["records"] == 0:
        warnings.append("No repair price data loaded; estimates will show 'insufficient pricing data'.")
    if checks["pricing_sources_failed"]:
        warnings.append("Some price sources failed their last refresh; older data is still served with its date.")
    response.status_code = 200 if ok else 503
    return {"ready": ok, "checks": checks, "warnings": warnings}


@router.get("/config")
def public_config(st: StateDep) -> dict[str, Any]:
    pricing = st.pricing.status()
    return {
        "agent_url": st.settings.agent_url,
        "default_region": st.settings.default_region,
        "regions": [{"region": r, "currency": c, "has_price_data": r in pricing["regions"]}
                    for r, c in sorted(st.settings.market_currencies.items())],
        "valuation_configured": bool(partner_svc.public_status(st)["configured"]),
        "retention_days": st.settings.retention_days,
        "unsaved_retention_hours": st.settings.unsaved_retention_hours,
        "pricing_demo": st.settings.pricing_demo,
        "env": st.settings.env,
        "disclaimer": DISCLAIMER,
    }


@router.get("/pricing/status")
def pricing_status(st: StateDep) -> dict[str, Any]:
    """Which price sources are loaded, when they were last retrieved, and whether any failed."""
    status = st.pricing.status()
    allowed = st.pricing.policy.allowed_sources
    return {
        **status,
        "catalog": [{"source_id": s.source_id, "organization": s.organization, "url": s.url, "region": s.region,
                     "source_type": s.source_type.value, "usage_policy": s.usage_policy.value,
                     "automated": s.collector is not None,
                     "enabled_for_display": (allowed is None or s.source_id in allowed)
                     and s.usage_policy.value != "prohibited"} for s in SOURCES],
    }


@router.get("/catalog/checks")
def catalog_checks() -> dict[str, Any]:
    return {"checks": [c.model_dump(mode="json") for c in CHECKS], "not_assessable": list(NOT_ASSESSABLE)}


@router.get("/catalog/faults")
def catalog_faults(st: StateDep) -> dict[str, Any]:
    return {"version": st.kb.taxonomy_version, "faults": [f.model_dump() for f in st.kb.faults.values()],
            "repair_types": [r.model_dump() for r in st.kb.repair_types.values()]}


# ---------------------------------------------------------------------------- sessions


@router.post("/sessions", status_code=201)
def create_session(req: svc.CreateSessionRequest, st: StateDep, db: DbDep) -> dict[str, Any]:
    if not req.consent.diagnostic_processing:
        raise ApiError(422, "consent_required", "Consent to process diagnostic data is required to continue.")
    now = svc.utc_now()
    st.db.purge_expired(now)
    token = new_token()
    rec = DiagnosticSession(
        id=str(uuid.uuid4()), token_hash=hash_token(token), created_at=now, updated_at=now,
        expires_at=svc.new_expiry(req.consent, st.settings, now), mode=req.mode,
        device=req.device.model_dump(), consent={**req.consent.model_dump(), "given_at": now.isoformat()},
        scan=None, answers={}, quotes=[], valuation=None, decision=None,
    )
    db.add(rec)
    db.commit()
    return {"session_id": rec.id, "access_token": token, "expires_at": rec.expires_at.isoformat()}


def _summary(rec: DiagnosticSession) -> dict[str, Any]:
    return {"session_id": rec.id, "mode": rec.mode, "device": rec.device, "consent": rec.consent,
            "created_at": rec.created_at.isoformat(), "expires_at": rec.expires_at.isoformat(),
            "has_scan": rec.scan is not None, "answers": len(rec.answers or {}),
            "tests_done": len(rec.guided_tests or {}),
            "quotes": len(rec.quotes or []), "has_valuation": bool(rec.valuation),
            "has_decision": bool(rec.decision)}


def _progress(st: svc.AppState, rec: DiagnosticSession) -> dict[str, Any]:
    """Step completion for the progress bar: hands-on tests done/passed, and whether questions are finished."""
    results = svc.tests_of(rec)
    available = [t.id for t in st.kb.tests.values() if not (t.method == "agent_assisted" and rec.mode != "usb")]
    done = [tid for tid in available if tid in results]
    passed = [tid for tid in available if results.get(tid, {}).get("outcome") == "pass"]
    nq = _next(st, rec)
    return {"tests_available": len(available), "tests_done": len(done), "tests_passed": len(passed),
            "tests_complete": bool(available) and len(done) == len(available),
            "questions_complete": nq.question is None, "questions_stop_reason": nq.stop_reason,
            "questions_skipped_by_tests": len(nq.skipped_by_tests)}


@router.get("/sessions/{session_id}")
def get_session(rec: SessionDep, st: StateDep) -> dict[str, Any]:
    return {**_summary(rec), "progress": _progress(st, rec)}


@router.patch("/sessions/{session_id}/device")
def update_device(rec: SessionDep, device: svc.DeviceProfile, db: DbDep) -> dict[str, Any]:
    rec.device = device.model_dump()
    rec.valuation = None  # a valuation for different device details is no longer valid
    _save(db, rec)
    return _summary(rec)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(rec: SessionDep, db: DbDep) -> Response:
    db.delete(rec)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------- scan & observations


@router.put("/sessions/{session_id}/scan")
def put_scan(rec: SessionDep, scan: ScanResult, db: DbDep) -> dict[str, Any]:
    if rec.mode != "usb":
        raise ApiError(409, "manual_session", "This session was started without a USB scan.")
    if rec.scan and rec.scan.get("scan_id") == scan.scan_id:
        return {"stored": True, "duplicate": True}
    unknown = [o.check_id for o in scan.observations if o.check_id not in CHECKS_BY_ID]
    if unknown:
        raise ApiError(422, "unknown_checks", "The scan contains unknown checks.", unknown)
    rec.scan = scan.model_dump(mode="json")
    # The phone's own identity is more reliable than what was typed (e.g. "Pixel" / "6A" vs "Google" / "Pixel 6a").
    device = dict(rec.device)
    typed = {k: device.get(k) for k in ("manufacturer", "model")}
    if scan.identity.manufacturer:
        device["manufacturer"] = scan.identity.manufacturer
    if scan.identity.model:
        device["model"] = scan.identity.model
    if any(typed.values()) and typed != {k: device.get(k) for k in typed}:
        device["customer_entered"] = typed
        rec.valuation = None  # a valuation for different device details is no longer valid
    rec.device = device
    _save(db, rec)
    return {"stored": True, "duplicate": False}


@router.put("/sessions/{session_id}/observations/{check_id}")
def put_optin_observation(check_id: str, obs: Observation, rec: SessionDep, db: DbDep) -> dict[str, Any]:
    """Add/replace an opt-in observation (e.g. the consented bug-report summary) in the stored scan."""
    spec = CHECKS_BY_ID.get(check_id)
    if spec is None or spec.default_in_scan or obs.check_id != check_id:
        raise ApiError(422, "not_optin_check", "Only opt-in checks can be added after the scan.")
    if not rec.scan:
        raise ApiError(409, "scan_required", "Run the device scan first.")
    scan = svc.scan_of(rec)
    assert scan is not None
    observations = [o for o in scan.observations if o.check_id != check_id] + [obs]
    rec.scan = scan.model_copy(update={"observations": observations}).model_dump(mode="json")
    _save(db, rec)
    return {"stored": True}


@router.get("/sessions/{session_id}/observations")
def get_observations(rec: SessionDep) -> dict[str, Any]:
    scan = svc.scan_of(rec)
    items = []
    if scan:
        for o in scan.observations:
            spec = CHECKS_BY_ID[o.check_id]
            items.append({**o.model_dump(mode="json"), "title": spec.title, "category": spec.category,
                          "quality": spec.quality.value, "privacy_note": spec.privacy_note})
    return {"has_scan": scan is not None, "complete": scan.complete if scan else None,
            "identity": scan.identity.model_dump() if scan else None,
            "scanned_at": scan.finished_at.isoformat() if scan else None,
            "observations": items, "not_assessable": list(NOT_ASSESSABLE)}


# ---------------------------------------------------------------------------- questionnaire


def _next(st: svc.AppState, rec: DiagnosticSession, more: bool = False) -> NextQuestion:
    return next_question(st.kb, svc.device_of(rec), svc.scan_of(rec), rec.answers or {}, svc.tests_of(rec),
                         more=more)


@router.get("/sessions/{session_id}/questions/next")
def get_next_question(rec: SessionDep, st: StateDep, more: bool = False) -> NextQuestion:
    """``more=true``: the customer wants to describe other problems although the tests already gave an answer."""
    return _next(st, rec, more)


@router.get("/sessions/{session_id}/answers")
def list_answers(rec: SessionDep, st: StateDep) -> dict[str, Any]:
    out = []
    for qid, value in (rec.answers or {}).items():
        q = st.kb.questions.get(qid)
        if q is None:
            continue
        labels = {o.value: o.label for o in q.options}
        vals = value if isinstance(value, list) else [value]
        out.append({"question_id": qid, "question": q.text, "value": value,
                    "labels": [labels.get(v, v) for v in vals]})
    return {"answers": out}


@router.put("/sessions/{session_id}/answers/{question_id}")
def put_answer(question_id: str, body: svc.AnswerRequest, rec: SessionDep, st: StateDep, db: DbDep) -> NextQuestion:
    q = st.kb.questions.get(question_id)
    if q is None:
        raise ApiError(404, "question_not_found", "Unknown question.")
    try:
        value = validate_answer(q, body.value)
    except InvalidAnswerError as exc:
        raise ApiError(422, "invalid_answer", str(exc)) from exc
    rec.answers = {**(rec.answers or {}), question_id: value}
    _save(db, rec)
    return _next(st, rec)


@router.delete("/sessions/{session_id}/answers/{question_id}")
def delete_answer(question_id: str, rec: SessionDep, st: StateDep, db: DbDep) -> NextQuestion:
    answers = dict(rec.answers or {})
    answers.pop(question_id, None)
    rec.answers = answers
    _save(db, rec)
    return _next(st, rec)


# ---------------------------------------------------------------------------- diagnosis & repair


@router.get("/sessions/{session_id}/diagnosis")
def get_diagnosis(rec: SessionDep, st: StateDep) -> dict[str, Any]:
    diagnosis = svc.run_diagnosis(st, rec)
    result = diagnosis.model_dump(mode="json")
    result["repair_costs"] = svc.candidate_repair_costs(st, rec, diagnosis)
    return result


@router.get("/sessions/{session_id}/repair-estimate")
def get_repair_estimate(rec: SessionDep, st: StateDep) -> dict[str, Any]:
    return svc.repair_report(st, rec).model_dump(mode="json")


@router.post("/sessions/{session_id}/quotes", status_code=201)
def add_quote(quote: QuoteInput, rec: SessionDep, st: StateDep, db: DbDep) -> dict[str, Any]:
    if quote.repair_type not in st.kb.repair_types:
        raise ApiError(422, "unknown_repair_type", "Unknown repair type.")
    quotes = list(rec.quotes or [])
    if len(quotes) >= 10:
        raise ApiError(409, "too_many_quotes", "A session can hold at most 10 quotes.")
    quotes.append(quote.model_dump(mode="json"))
    rec.quotes = quotes
    _save(db, rec)
    return svc.repair_report(st, rec).model_dump(mode="json")


@router.delete("/sessions/{session_id}/quotes/{index}")
def delete_quote(index: int, rec: SessionDep, st: StateDep, db: DbDep) -> dict[str, Any]:
    quotes = list(rec.quotes or [])
    if not 0 <= index < len(quotes):
        raise ApiError(404, "quote_not_found", "That quote does not exist.")
    quotes.pop(index)
    rec.quotes = quotes
    _save(db, rec)
    return svc.repair_report(st, rec).model_dump(mode="json")


# ---------------------------------------------------------------------------- FlipValue


_FV_STATUS = {
    FlipValueDisabledError: 503, FlipValueConfigError: 503, FlipValueAuthError: 502,
    FlipValueRateLimitedError: 503, FlipValueTimeoutError: 504, FlipValueUnsupportedDeviceError: 422,
    FlipValueRejectedRequestError: 422,
}

_FV_MESSAGES = {
    "flipvalue_disabled": "Resale valuation is not enabled on this server.",
    "flipvalue_misconfigured": "Resale valuation is not configured correctly on this server.",
    "flipvalue_auth_failed": "The valuation service rejected our credentials. This is our problem, not yours.",
    "flipvalue_rate_limited": "The valuation service is busy. Please try again in a minute.",
    "flipvalue_timeout": "The valuation service did not respond in time. Please try again.",
    "flipvalue_unavailable": "The valuation service is currently unavailable.",
    "flipvalue_unsupported_device": "The valuation service cannot value this device model.",
    "flipvalue_rejected_request": "The valuation service could not process these device details.",
    "flipvalue_invalid_response": "The valuation service returned data we could not verify, so it was not used.",
}


@router.get("/sessions/{session_id}/valuation")
def get_valuation(rec: SessionDep, st: StateDep) -> dict[str, Any]:
    diagnosis = svc.run_diagnosis(st, rec)
    status = partner_svc.public_status(st)
    return {"configured": status["configured"], "origin": status["origin"],
            "development_mock": status["development_mock"], "stored": rec.valuation,
            "suggested_condition": svc.suggested_condition(diagnosis)}


@router.post("/sessions/{session_id}/valuation")
def request_valuation(inp: svc.ValuationInput, rec: SessionDep, st: StateDep, db: DbDep) -> dict[str, Any]:
    resolved = partner_svc.resolve_client(st)
    if resolved is None:
        raise ApiError(409, "valuation_not_configured",
                       "Resale values need a FlipValue API key. Add it in Settings; nothing is estimated without it.")
    ok, retry = st.valuation_limiter.allow(rec.id)
    if not ok:
        raise ApiError(429, "valuation_rate_limited", "Too many valuation requests for this session.",
                       headers={"Retry-After": str(int(retry) + 1)})
    diagnosis = svc.run_diagnosis(st, rec)
    currency = svc.region_currency(st, svc.device_of(rec).region) or "USD"
    try:
        vreq = svc.build_valuation_request(st, rec, inp, diagnosis, currency)
    except ValidationError as exc:
        raise ApiError(422, "invalid_valuation_request", "Device details are not valid for valuation.") from exc
    now = svc.utc_now().isoformat()
    try:
        outcome = resolved.client.get_valuation(vreq)
    except FlipValueError as exc:
        status = _FV_STATUS.get(type(exc), 502)
        rec.valuation = {"ok": False, "error_code": exc.code, "requested_at": now, "source": resolved.client.source,
                         "request": vreq.model_dump(mode="json")}
        _save(db, rec)
        log.warning("valuation failed code=%s request_id=%s", exc.code, exc.request_id)
        raise ApiError(status, exc.code, _FV_MESSAGES.get(exc.code, "Valuation failed."),
                       {"diagnosis_still_available": True}) from exc
    rec.valuation = {
        "ok": True, "source": outcome.source, "requested_at": now, "request_id": outcome.request_id,
        "attempts": outcome.attempts, "latency_ms": outcome.latency_ms,
        "request": vreq.model_dump(mode="json"), "response": outcome.response.model_dump(mode="json"),
    }
    _save(db, rec)
    return rec.valuation


_MARKET_MESSAGES = {
    **_FV_MESSAGES,
    "flipvalue_no_data": "FlipValue has no data for this phone.",
    "flipvalue_unavailable": "This FlipValue service does not provide this data right now.",
}


def _market_client(st: svc.AppState, rec: DiagnosticSession) -> MarketClient:
    resolved = partner_svc.resolve_client(st)
    if resolved is None:
        raise ApiError(409, "valuation_not_configured",
                       "Market data needs a FlipValue API key. Add it in Settings; nothing is estimated without it.")
    if st.market_limiter:
        ok, retry = st.market_limiter.allow(rec.id)
        if not ok:
            raise ApiError(429, "market_rate_limited", "Too many FlipValue requests for this session.",
                           headers={"Retry-After": str(int(retry) + 1)})
    return MarketClient(resolved.client)


def _market_part(fetch: Any) -> dict[str, Any]:
    """One section of the market view. A failure becomes 'no data' with its reason; nothing is filled in."""
    try:
        return fetch()
    except FlipValueError as exc:
        log.info("flipvalue market section failed code=%s", exc.code)
        return {"available": False, "error_code": exc.code,
                "reason": exc.message if isinstance(exc, FlipValueNoDataError)
                else _MARKET_MESSAGES.get(exc.code, "FlipValue could not provide this data.")}


@router.get("/sessions/{session_id}/market")
def get_market(rec: SessionDep, st: StateDep, grade: ConditionGrade = ConditionGrade.GOOD,
               storage_gb: int | None = None) -> dict[str, Any]:
    """FlipValue's trends and store prices for this phone, as shown on FlipValue's own site."""
    market = _market_client(st, rec)
    device = svc.device_of(rec)
    base: dict[str, Any] = {"region": device.region, "grade": grade.value, "product": None, "storage_gb": None,
                            "trend": None, "offers": None}
    if not device.manufacturer or not device.model:
        return {**base, "status": "device_details_required",
                "message": "FlipValue needs the phone's manufacturer and model."}
    try:
        product = market.find_product(device.manufacturer, device.model)
    except FlipValueError as exc:
        no_data = isinstance(exc, FlipValueNoDataError)
        return {**base, "status": "no_product" if no_data else "error", "error_code": exc.code,
                "message": f"FlipValue has no data for {device.manufacturer} {device.model}." if no_data
                else _MARKET_MESSAGES.get(exc.code, "FlipValue could not be reached.")}
    sizes = product["storage_gb"]
    wanted = storage_gb or device.storage_gb
    storage = wanted if not sizes or wanted in sizes else sizes[0]
    if storage is None:
        return {**base, "status": "ok", "product": product, "message": "Choose a storage size."}
    return {**base, "status": "ok", "product": product, "storage_gb": storage,
            "storage_matched": wanted == storage,
            "trend": _market_part(lambda: market.trend(product["id"], storage, device.region, grade.value)),
            "offers": _market_part(lambda: market.offers(product["id"], storage, device.region))}


@router.post("/sessions/{session_id}/market/compare")
def post_market_compare(body: svc.MarketCompareInput, rec: SessionDep, st: StateDep) -> dict[str, Any]:
    """FlipValue's repair-or-sell comparison for the values the customer is using."""
    market = _market_client(st, rec)
    payload = {**body.model_dump(exclude_none=True), "repair_cost_basis": "user_supplied"}
    return _market_part(lambda: {"available": True, **market.compare(payload)})


# ---------------------------------------------------------------------------- partner service settings


class PartnerSettingsInput(BaseModel):
    base_url: str = Field(min_length=8, max_length=300)
    api_key: str = Field(min_length=8, max_length=300)


def _admin(request: Request, st: svc.AppState, token: str | None) -> None:
    host = request.client.host if request.client else None
    partner_svc.require_admin(host, token, st.settings.admin_token.get_secret_value() if st.settings.admin_token
                              else None, st.settings.env)
    ok, retry = st.partner_limiter.allow(host or "unknown") if st.partner_limiter else (True, 0.0)
    if not ok:
        raise ApiError(429, "settings_rate_limited", "Too many settings requests.",
                       headers={"Retry-After": str(int(retry) + 1)})


AdminToken = Annotated[str | None, Header(alias="X-Admin-Token")]


@router.get("/settings/partner")
def get_partner_settings(request: Request, st: StateDep, token: AdminToken = None) -> dict[str, Any]:
    _admin(request, st, token)
    return partner_svc.public_status(st)


@router.put("/settings/partner")
def put_partner_settings(body: PartnerSettingsInput, request: Request, st: StateDep,
                         token: AdminToken = None) -> dict[str, Any]:
    _admin(request, st, token)
    url = partner_svc.validate_base_url(body.base_url, st.settings.env)
    assert st.partner_store is not None
    st.partner_store.save(partner_svc.PartnerConfig(base_url=url, api_key=body.api_key.strip()))
    log.info("partner service configuration saved")  # never log the key or the URL's credentials
    return partner_svc.public_status(st)


@router.delete("/settings/partner")
def delete_partner_settings(request: Request, st: StateDep, token: AdminToken = None) -> dict[str, Any]:
    _admin(request, st, token)
    assert st.partner_store is not None
    st.partner_store.clear()
    st.partner_clients.clear()
    log.info("partner service configuration removed")
    return partner_svc.public_status(st)


@router.post("/settings/partner/test")
def test_partner_settings(request: Request, st: StateDep, token: AdminToken = None) -> dict[str, Any]:
    """Check the saved key with a sample valuation request (no customer data is sent)."""
    _admin(request, st, token)
    resolved = partner_svc.resolve_client(st)
    if resolved is None or resolved.origin == "development_mock":
        return {"ok": False, "code": "valuation_not_configured", "message": "No FlipValue API key is configured."}
    sample = svc.sample_valuation_request(st)
    try:
        resolved.client.get_valuation(sample)
    except FlipValueUnsupportedDeviceError:
        return {"ok": True, "message": "Connected: the key was accepted (the sample device is not in the catalogue)."}
    except FlipValueError as exc:
        return {"ok": False, "code": exc.code, "message": _FV_MESSAGES.get(exc.code, "The test failed.")}
    return {"ok": True, "message": "Connected: the service accepted the key and returned a valuation."}


# ---------------------------------------------------------------------------- decision & report


@router.post("/sessions/{session_id}/decision")
def post_decision(req: svc.DecisionRequest, rec: SessionDep, st: StateDep, db: DbDep) -> dict[str, Any]:
    result = svc.run_decision(st, rec, req)
    rec.decision = {"request": req.model_dump(mode="json"), "result": result.model_dump(mode="json"),
                    "computed_at": svc.utc_now().isoformat()}
    _save(db, rec)
    return rec.decision


@router.get("/sessions/{session_id}/report")
def get_report(rec: SessionDep, st: StateDep) -> dict[str, Any]:
    diagnosis = svc.run_diagnosis(st, rec)
    repair = svc.repair_report(st, rec, diagnosis)
    return {
        "report_version": "1",
        "generated_at": svc.utc_now().isoformat(),
        "app_version": __version__,
        "session": _summary(rec),
        "observations": get_observations(rec),
        "answers": list_answers(rec, st)["answers"],
        "diagnosis": diagnosis.model_dump(mode="json"),
        "repair": repair.model_dump(mode="json"),
        "guided_tests": svc.tests_of(rec),
        "valuation": rec.valuation,
        "decision": rec.decision,
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------- guided hardware tests

PHONE_LINK_MINUTES = 30


def _tests_view(st: svc.AppState, rec: DiagnosticSession,
                methods: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    results = svc.tests_of(rec)
    diagnosis = svc.run_diagnosis(st, rec)
    in_play = [c.fault_id for c in diagnosis.candidates if c.score > 0]
    done = {tid for tid, r in results.items() if r.get("outcome") != "not_run"}
    recommended = set(recommended_tests(st.kb, in_play, done))
    out = []
    for t in st.kb.tests.values():
        if methods and t.method not in methods:
            continue
        available = not (t.method == "agent_assisted" and rec.mode != "usb")
        out.append({**t.model_dump(), "result": results.get(t.id), "recommended": t.id in recommended,
                    "available": available,
                    "unavailable_reason": None if available else "Needs the USB connection and local agent."})
    out.sort(key=lambda x: (not x["recommended"], x["result"] is not None, x["category"]))
    return out


def _store_result(rec: DiagnosticSession, db: Session, test_id: str, result: dict[str, Any]) -> None:
    tests = svc.tests_of(rec)
    tests[test_id] = result
    rec.guided_tests = tests
    _save(db, rec)


@router.get("/sessions/{session_id}/tests")
def list_tests(rec: SessionDep, st: StateDep) -> dict[str, Any]:
    expires = rec.phone_token_expires_at
    active = bool(rec.phone_token_hash and expires and expires.replace(tzinfo=expires.tzinfo or UTC) > svc.utc_now())
    return {"tests": _tests_view(st, rec), "phone_link_active": active,
            "phone_link_expires_at": rec.phone_token_expires_at.isoformat() if active and rec.phone_token_expires_at
            else None}


def _test_or_404(st: svc.AppState, test_id: str) -> Any:
    test = st.kb.tests.get(test_id)
    if test is None:
        raise ApiError(404, "test_not_found", "Unknown test.")
    return test


@router.put("/sessions/{session_id}/tests/{test_id}")
def put_test_result(test_id: str, body: TestResultInput, rec: SessionDep, st: StateDep, db: DbDep) -> dict[str, Any]:
    """Record a manual test outcome, or mark any test as not run. Phone-browser results come from the phone."""
    test = _test_or_404(st, test_id)
    if test.method == "phone_browser" and body.outcome != "not_run":
        raise ApiError(422, "use_phone_page", "Run this test on the phone so its measurements are recorded there.")
    try:
        result = validate_result(test, body, "main")
    except InvalidTestResultError as exc:
        raise ApiError(422, "invalid_test_result", str(exc)) from exc
    _store_result(rec, db, test_id, result.model_dump(mode="json"))
    return {"stored": result.model_dump(mode="json"), "next_question": _next(st, rec).model_dump()}


@router.post("/sessions/{session_id}/tests/{test_id}/agent-result")
def put_agent_test_result(test_id: str, body: TestResultInput, rec: SessionDep, st: StateDep,
                          db: DbDep) -> dict[str, Any]:
    """Result of an agent-assisted test, relayed by the browser from the local agent (same trust as the scan)."""
    test = _test_or_404(st, test_id)
    if rec.mode != "usb":
        raise ApiError(409, "manual_session", "Agent-assisted tests need a USB session.")
    try:
        result = validate_result(test, body, "agent")
    except InvalidTestResultError as exc:
        raise ApiError(422, "invalid_test_result", str(exc)) from exc
    _store_result(rec, db, test_id, result.model_dump(mode="json"))
    return {"stored": result.model_dump(mode="json")}


@router.delete("/sessions/{session_id}/tests/{test_id}")
def delete_test_result(test_id: str, rec: SessionDep, st: StateDep, db: DbDep) -> dict[str, Any]:
    _test_or_404(st, test_id)
    tests = svc.tests_of(rec)
    tests.pop(test_id, None)
    rec.guided_tests = tests
    _save(db, rec)
    return {"deleted": True}


@router.post("/sessions/{session_id}/phone-link", status_code=201)
def create_phone_link(rec: SessionDep, db: DbDep) -> dict[str, Any]:
    """A short-lived token that only lets the phone list and submit its browser tests for this session."""
    token = new_token()
    rec.phone_token_hash = hash_token(token)
    rec.phone_token_expires_at = svc.utc_now() + timedelta(minutes=PHONE_LINK_MINUTES)
    _save(db, rec)
    return {"token": token, "path": f"/p/{token}", "expires_at": rec.phone_token_expires_at.isoformat()}


@router.delete("/sessions/{session_id}/phone-link", status_code=204)
def revoke_phone_link(rec: SessionDep, db: DbDep) -> Response:
    rec.phone_token_hash = None
    rec.phone_token_expires_at = None
    _save(db, rec)
    return Response(status_code=204)


def phone_session(token: str, db: DbDep) -> DiagnosticSession:
    if not 20 <= len(token) <= 100:
        raise ApiError(404, "phone_link_invalid", "This test link is invalid or has expired.")
    rec = db.query(DiagnosticSession).filter(DiagnosticSession.phone_token_hash == hash_token(token)).one_or_none()
    expires = rec.phone_token_expires_at if rec else None
    if rec is None or expires is None or expires.replace(tzinfo=expires.tzinfo or UTC) < svc.utc_now():
        raise ApiError(404, "phone_link_invalid", "This test link is invalid or has expired. Create a new one on "
                                                  "your computer.")
    return rec


PhoneDep = Annotated[DiagnosticSession, Depends(phone_session)]


@router.get("/phone/{token}")
def phone_overview(rec: PhoneDep, st: StateDep) -> dict[str, Any]:
    dev = rec.device or {}
    return {"device_label": " ".join(x for x in (dev.get("manufacturer"), dev.get("model")) if x) or "your phone",
            "expires_at": rec.phone_token_expires_at.isoformat() if rec.phone_token_expires_at else None,
            "tests": _tests_view(st, rec, ("phone_browser",))}


@router.put("/phone/{token}/tests/{test_id}")
def phone_put_result(test_id: str, body: TestResultInput, rec: PhoneDep, st: StateDep, db: DbDep) -> dict[str, Any]:
    test = _test_or_404(st, test_id)
    try:
        result = validate_result(test, body, "phone")
    except InvalidTestResultError as exc:
        raise ApiError(422, "invalid_test_result", str(exc)) from exc
    _store_result(rec, db, test_id, result.model_dump(mode="json"))
    return {"stored": result.model_dump(mode="json")}
