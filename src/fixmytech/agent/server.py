"""Local agent HTTP API (127.0.0.1 only).

Trust model (see docs/SECURITY.md):
* Bound to the loopback interface; never reachable from the network.
* ``Host`` header must be a loopback name -> defeats DNS-rebinding attacks.
* Requests carrying an ``Origin`` must come from an allowlisted FixMyTech web origin.
* Every endpoint except /health requires the per-run pairing token (``X-Agent-Token``). A custom header
  also forces a CORS preflight, so other websites cannot even trigger a scan blindly.
* There is no endpoint that accepts a command. Scans run a fixed, allowlisted set of read-only checks.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from fixmytech.agent import AGENT_VERSION, actions
from fixmytech.agent.adb import (
    SERIAL_RE,
    AdbClient,
    AdbError,
    AdbNotFoundError,
    AdbTimeoutError,
    DeviceDisconnectedError,
    run_action,
)
from fixmytech.agent.collectors import ScanContext, make_observation, run_check, run_scan
from fixmytech.core.capabilities import CHECKS, CHECKS_BY_ID, NOT_ASSESSABLE
from fixmytech.core.observations import CheckStatus, ScanResult
from fixmytech.core.redaction import redact

log = logging.getLogger("fixmytech.agent")

STATE_HELP = {
    "device": "Connected and authorized.",
    "unauthorized": "Unlock the phone and tap 'Allow' on the 'Allow USB debugging?' prompt. "
    "If no prompt appears, unplug and reconnect the cable, or revoke USB debugging authorizations "
    "in Developer options and reconnect.",
    "offline": "The phone is connected but not responding. Unplug and reconnect it, then try a different "
    "cable or USB port.",
    "no_permissions": "The operating system denied access to the USB device. Reconnect the phone, "
    "and check the USB driver installation.",
    "recovery": "The phone is in recovery mode. Restart it normally first.",
    "bootloader": "The phone is in bootloader/fastboot mode. Restart it normally first.",
    "sideload": "The phone is in sideload mode. Restart it normally first.",
}


@dataclass
class ScanJob:
    scan_id: str
    serial: str
    state: Literal["running", "done", "failed"] = "running"
    current_check: str | None = None
    done: int = 0
    total: int = 0
    result: ScanResult | None = None
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class TaskJob:
    job_id: str
    kind: str
    serial: str
    state: Literal["running", "done", "failed"] = "running"
    stage: str | None = None
    done: int = 0
    total: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    live: dict[str, Any] | None = None  # latest reading, for tests that show progress as they run
    lock: threading.Lock = field(default_factory=threading.Lock)


# Capabilities the web app checks before offering a feature, so an outdated agent is detected clearly.
FEATURES = ["scan", "connector_test", "bugreport", "phone_tests", "live_readings"]


class ConsentRequest(BaseModel):
    consent: bool
    duration_s: int | None = Field(default=None, ge=10, le=120)


class PhoneTestsRequest(BaseModel):
    consent: bool
    web_port: int | None = Field(default=None, ge=1024, le=65535)  # local site: forwarded to the phone
    site_origin: str | None = Field(default=None, max_length=300)  # hosted site: opened by its public https address
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{20,100}$")


class ScanRequest(BaseModel):
    serial: str = Field(pattern=SERIAL_RE.pattern)
    consent: bool = Field(description="Customer has agreed to the read-only scan")
    checks: list[str] | None = Field(default=None, max_length=len(CHECKS))


def create_agent_app(
    token: str,
    allowed_origins: list[str],
    adb: AdbClient | None = None,
    salt: bytes | None = None,
    port: int = 8765,
    run_in_thread: bool = True,
    sleeper: Callable[[float], None] = time.sleep,
) -> FastAPI:
    adb_client = adb or AdbClient()
    device_salt = salt or secrets.token_bytes(16)
    jobs: dict[str, ScanJob] = {}
    tasks: dict[str, TaskJob] = {}
    scan_lock = threading.Lock()
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost", "testserver"}

    app = FastAPI(title="FixMyTech local diagnostic agent", version=AGENT_VERSION, docs_url=None, redoc_url=None)

    # Registered first so the loopback guard (added after) is the outermost layer and also covers preflights.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["X-Agent-Token", "Content-Type"],
        allow_credentials=False,
        # Chrome Private Network Access: lets an https FixMyTech page reach this loopback agent.
        allow_private_network=True,
        max_age=600,
    )

    @app.middleware("http")
    async def loopback_guard(request: Request, call_next: Any) -> Any:
        host = (request.headers.get("host") or "").lower()
        if host not in allowed_hosts:
            return JSONResponse({"error": {"code": "forbidden_host", "message": "Invalid Host header"}}, 403)
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            return JSONResponse({"error": {"code": "forbidden_origin", "message": "Origin not allowed"}}, 403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    def require_token(x_agent_token: str | None = Header(default=None)) -> None:
        if not x_agent_token or not secrets.compare_digest(x_agent_token, token):
            raise HTTPException(401, detail={"code": "invalid_agent_token",
                                             "message": "Missing or invalid agent pairing token"})

    def _adb_error(exc: AdbError) -> HTTPException:
        if isinstance(exc, AdbNotFoundError):
            return HTTPException(503, detail={"code": "adb_not_installed",
                                              "message": "ADB (Android platform-tools) is not installed or not found."})
        if isinstance(exc, AdbTimeoutError):
            return HTTPException(504, detail={"code": "adb_timeout", "message": "ADB did not respond in time."})
        if isinstance(exc, DeviceDisconnectedError):
            return HTTPException(409, detail={"code": "device_disconnected", "message": "The device disconnected."})
        return HTTPException(503, detail={"code": "adb_error", "message": redact(str(exc))})

    def _require_ready_device(serial: str) -> None:
        if not SERIAL_RE.match(serial):
            raise HTTPException(422, detail={"code": "invalid_serial", "message": "Invalid device serial"})
        try:
            devices = {d.serial: d for d in adb_client.devices()}
        except AdbError as exc:
            raise _adb_error(exc) from exc
        dev = devices.get(serial)
        if dev is None:
            raise HTTPException(404, detail={"code": "device_not_found", "message": "Device is not connected"})
        if dev.state != "device":
            raise HTTPException(409, detail={"code": f"device_{dev.state}",
                                             "message": STATE_HELP.get(dev.state, f"Device state: {dev.state}")})

    @app.get("/agent/v1/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "agent_version": AGENT_VERSION, "token_required": True, "features": FEATURES}

    @app.get("/agent/v1/session", dependencies=[Depends(require_token)])
    def session_check() -> dict[str, Any]:
        return {"paired": True, "agent_version": AGENT_VERSION}

    @app.get("/agent/v1/adb", dependencies=[Depends(require_token)])
    def adb_status() -> dict[str, Any]:
        try:
            version = adb_client.version()
        except AdbError as exc:
            err = _adb_error(exc)
            return {"installed": False, "server_running": False, "version": None, "error": err.detail}
        try:
            adb_client.start_server()
            server_ok, error = True, None
        except AdbError as exc:
            server_ok, error = False, _adb_error(exc).detail
        return {"installed": True, "server_running": server_ok, "version": version, "error": error}

    @app.get("/agent/v1/devices", dependencies=[Depends(require_token)])
    def devices() -> dict[str, Any]:
        try:
            entries = adb_client.devices()
        except AdbError as exc:
            raise _adb_error(exc) from exc
        return {
            "devices": [
                {
                    "serial": d.serial,
                    "state": d.state,
                    "model": d.attributes.get("model", "").replace("_", " ") or None,
                    "ready": d.state == "device",
                    "help": STATE_HELP.get(d.state, f"Unrecognized state '{d.state}'."),
                }
                for d in entries
            ]
        }

    @app.get("/agent/v1/devices/{serial}/summary", dependencies=[Depends(require_token)])
    def device_summary(serial: str) -> dict[str, Any]:
        _require_ready_device(serial)
        ctx = ScanContext(adb=adb_client, serial=serial)
        try:
            ident = run_check(ctx, "device.identity")
        except DeviceDisconnectedError as exc:
            raise _adb_error(exc) from exc
        sdk = ident.value.get("sdk_int") if ident.usable else None
        caps = []
        for spec in CHECKS:
            supported: bool | None = True
            if spec.min_sdk and isinstance(sdk, int):
                supported = sdk >= spec.min_sdk
            elif spec.min_sdk:
                supported = None
            caps.append({"check_id": spec.check_id, "title": spec.title, "quality": spec.quality,
                         "expected_supported": supported, "limitations": list(spec.limitations),
                         "in_default_scan": spec.default_in_scan})
        return {"identity": ident.model_dump(mode="json"), "capabilities": caps,
                "not_assessable": list(NOT_ASSESSABLE)}

    @app.get("/agent/v1/capabilities", dependencies=[Depends(require_token)])
    def capabilities() -> dict[str, Any]:
        return {"checks": [c.model_dump(mode="json") for c in CHECKS], "not_assessable": list(NOT_ASSESSABLE)}

    def _execute(job: ScanJob, checks: list[str] | None) -> None:
        def progress(check_id: str, done: int, total: int) -> None:
            with job.lock:
                job.current_check, job.done, job.total = check_id, done, total

        try:
            result = run_scan(adb_client, job.serial, device_salt, checks, progress, scan_id=job.scan_id)
            with job.lock:
                job.result, job.state = result, "done"
        except AdbError as exc:
            with job.lock:
                job.state, job.error = "failed", _adb_error(exc).detail["message"]  # type: ignore[index]
        except Exception:  # noqa: BLE001 - report failure to the UI instead of a hung job
            log.exception("scan failed")
            with job.lock:
                job.state, job.error = "failed", "Unexpected agent error during scan"
        finally:
            scan_lock.release()

    @app.post("/agent/v1/scans", status_code=202, dependencies=[Depends(require_token)])
    def start_scan(req: ScanRequest) -> dict[str, Any]:
        if not req.consent:
            raise HTTPException(422, detail={"code": "consent_required",
                                             "message": "The customer must consent before a scan runs."})
        if req.checks:
            unknown = [c for c in req.checks if c not in CHECKS_BY_ID or not CHECKS_BY_ID[c].default_in_scan]
            if unknown:
                raise HTTPException(422, detail={"code": "unknown_check", "message": f"Unknown checks: {unknown}"})
        _require_ready_device(req.serial)
        if not scan_lock.acquire(blocking=False):
            raise HTTPException(409, detail={"code": "scan_in_progress", "message": "A scan is already running."})
        job = ScanJob(scan_id=secrets.token_urlsafe(12), serial=req.serial,
                      total=len(req.checks or [c for c in CHECKS if c.default_in_scan]))
        jobs[job.scan_id] = job
        if run_in_thread:
            threading.Thread(target=_execute, args=(job, req.checks), daemon=True).start()
        else:
            _execute(job, req.checks)
        return {"scan_id": job.scan_id}

    @app.get("/agent/v1/scans/{scan_id}", dependencies=[Depends(require_token)])
    def scan_status(scan_id: str) -> dict[str, Any]:
        job = jobs.get(scan_id)
        if job is None:
            raise HTTPException(404, detail={"code": "scan_not_found", "message": "Unknown scan"})
        with job.lock:
            return {
                "scan_id": job.scan_id,
                "state": job.state,
                "current_check": job.current_check,
                "current_title": CHECKS_BY_ID[job.current_check].title
                if job.current_check in CHECKS_BY_ID else None,
                "done": job.done,
                "total": job.total,
                "error": job.error,
                "result": job.result.model_dump(mode="json") if job.result else None,
            }

    # ------------------------------------------------------------ consented actions and agent-assisted tests

    def _run_job(job: TaskJob, fn: Callable[[Callable[[str, int, int], None], TaskJob], dict[str, Any]]) -> None:
        def progress(stage: str, done: int, total: int) -> None:
            with job.lock:
                job.stage, job.done, job.total = stage, done, total

        try:
            result = fn(progress, job)
            with job.lock:
                job.result, job.state = result, "done"
        except AdbError as exc:
            with job.lock:
                job.state, job.error = "failed", _adb_error(exc).detail["message"]  # type: ignore[index]
        except (ValueError, OSError) as exc:
            with job.lock:
                job.state, job.error = "failed", redact(str(exc))
        except Exception:  # noqa: BLE001 - report failure to the UI instead of a hung job
            log.exception("job %s failed", job.kind)
            with job.lock:
                job.state, job.error = "failed", "Unexpected agent error"
        finally:
            scan_lock.release()

    def _start(kind: str, serial: str,
               fn: Callable[[Callable[[str, int, int], None], TaskJob], dict[str, Any]]) -> dict[str, Any]:
        _require_ready_device(serial)
        if not scan_lock.acquire(blocking=False):
            raise HTTPException(409, detail={"code": "busy", "message": "Another scan or test is already running."})
        job = TaskJob(job_id=secrets.token_urlsafe(12), kind=kind, serial=serial)
        tasks[job.job_id] = job
        if run_in_thread:
            threading.Thread(target=_run_job, args=(job, fn), daemon=True).start()
        else:
            _run_job(job, fn)
        return {"job_id": job.job_id}

    def _set_live(job: TaskJob, sample: dict[str, Any]) -> None:
        with job.lock:
            job.live = sample

    def _require_consent(consent: bool) -> None:
        if not consent:
            raise HTTPException(422, detail={"code": "consent_required",
                                             "message": "The customer must agree before this runs."})

    @app.post("/agent/v1/devices/{serial}/connector-test", status_code=202, dependencies=[Depends(require_token)])
    def start_connector_test(serial: str, req: ConsentRequest) -> dict[str, Any]:
        _require_consent(req.consent)
        return _start("connector_test", serial,
                      lambda progress, job: actions.connector_test(
                          adb_client, serial, req.duration_s or 45, progress=progress, sleep=sleeper,
                          on_sample=lambda sample: _set_live(job, sample)))

    @app.post("/agent/v1/devices/{serial}/bugreport", status_code=202, dependencies=[Depends(require_token)])
    def start_bugreport(serial: str, req: ConsentRequest) -> dict[str, Any]:
        _require_consent(req.consent)

        def run(progress: Callable[[str, int, int], None], job: TaskJob) -> dict[str, Any]:
            progress("collecting", 0, 1)
            counts = actions.collect_bugreport(adb_client, serial)
            progress("done", 1, 1)
            return make_observation("bugreport.summary", CheckStatus.OK, dict(counts)).model_dump(mode="json")

        return _start("bugreport", serial, run)

    @app.get("/agent/v1/jobs/{job_id}", dependencies=[Depends(require_token)])
    def job_status(job_id: str) -> dict[str, Any]:
        job = tasks.get(job_id)
        if job is None:
            raise HTTPException(404, detail={"code": "job_not_found", "message": "Unknown job"})
        with job.lock:
            return {"job_id": job.job_id, "kind": job.kind, "state": job.state, "stage": job.stage,
                    "done": job.done, "total": job.total, "error": job.error, "result": job.result,
                    "live": job.live}

    @app.post("/agent/v1/devices/{serial}/phone-tests", dependencies=[Depends(require_token)])
    def open_phone_tests(serial: str, req: PhoneTestsRequest) -> dict[str, Any]:
        """Forward the phone's localhost:<port> to this computer and open the test page in the phone's browser.

        ``adb reverse`` makes the page a secure context on the phone (localhost), which browsers require for
        camera, microphone and motion-sensor access. The forward is removed with DELETE or when the phone
        disconnects.
        """
        _require_consent(req.consent)
        _require_ready_device(serial)
        if req.site_origin is not None:
            # Hosted site: the phone can reach it directly over https (a secure context), so no forwarding is needed.
            # Only the site this agent was started for may be opened.
            if req.site_origin not in allowed_origins or not req.site_origin.startswith("https://"):
                raise HTTPException(422, detail={"code": "site_not_allowed",
                                                 "message": "That site is not the one this agent was started for."})
            try:
                opened = run_action(adb_client, serial, "open_site_tests", site=req.site_origin, token=req.token)
            except ValueError as exc:
                raise HTTPException(422, detail={"code": "invalid_request", "message": str(exc)}) from exc
            except AdbError as exc:
                raise _adb_error(exc) from exc
            return {"forwarded": False, "opened": opened.returncode == 0 and "Error" not in opened.stdout,
                    "url_on_phone": f"{req.site_origin}/p/…"}
        if req.web_port is None:
            raise HTTPException(422, detail={"code": "invalid_request", "message": "web_port or site_origin required"})
        try:
            run_action(adb_client, serial, "reverse_add", port=req.web_port)
            opened = run_action(adb_client, serial, "open_phone_tests", port=req.web_port, token=req.token)
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "invalid_request", "message": str(exc)}) from exc
        except AdbError as exc:
            raise _adb_error(exc) from exc
        return {"forwarded": True, "opened": opened.returncode == 0 and "Error" not in opened.stdout,
                "url_on_phone": f"http://localhost:{req.web_port}/p/…"}

    @app.delete("/agent/v1/devices/{serial}/phone-tests", dependencies=[Depends(require_token)])
    def close_phone_tests(serial: str, web_port: int) -> dict[str, Any]:
        _require_ready_device(serial)
        try:
            run_action(adb_client, serial, "reverse_remove", port=web_port)
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "invalid_request", "message": str(exc)}) from exc
        except AdbError as exc:
            raise _adb_error(exc) from exc
        return {"removed": True}

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse({"error": detail}, status_code=exc.status_code)

    return app
