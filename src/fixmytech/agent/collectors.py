"""Diagnostic collectors: one function per check in the capability registry.

Each collector runs allowlisted read-only commands, parses the output and returns an Observation.
Raw command output is never placed in an Observation.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from fixmytech.agent import parsers
from fixmytech.agent.adb import AdbClient, AdbTimeoutError, CommandResult, DeviceDisconnectedError
from fixmytech.core.capabilities import CHECKS, CHECKS_BY_ID
from fixmytech.core.observations import (
    CheckStatus,
    DeviceIdentity,
    EvidenceKind,
    Observation,
    ObservationValue,
    ScanResult,
    utcnow,
)
from fixmytech.core.redaction import redact


@dataclass
class ScanContext:
    adb: AdbClient
    serial: str
    _cache: dict[tuple[str, ...], CommandResult] = field(default_factory=dict)

    def run(self, command: tuple[str, ...], timeout: float | None = None) -> CommandResult:
        """Run (and memoize) an allowlisted command. Disconnects and timeouts propagate."""
        if command not in self._cache:
            self._cache[command] = self.adb.shell(self.serial, command, timeout=timeout)
        return self._cache[command]

    def props(self) -> dict[str, str]:
        return parsers.parse_getprop(_require_ok(self.run(("getprop",))))


class CheckFailedError(Exception):
    def __init__(self, status: CheckStatus, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _require_ok(res: CommandResult) -> str:
    if res.permission_denied:
        raise CheckFailedError(CheckStatus.PERMISSION_DENIED, "Access denied by the device's security policy")
    if res.missing:
        raise CheckFailedError(CheckStatus.UNSUPPORTED, "This device does not expose this information")
    if res.returncode != 0 and not res.stdout.strip():
        raise CheckFailedError(CheckStatus.ERROR, redact(res.stderr) or "command failed")
    return res.stdout


Collector = Callable[[ScanContext], tuple[ObservationValue, list[str]]]


# ----------------------------------------------------------------------------- collectors
# Each returns (value, extra_limitations).


def collect_identity(ctx: ScanContext) -> tuple[ObservationValue, list[str]]:
    p = ctx.props()
    sdk = p.get("ro.build.version.sdk")
    return {
        "manufacturer": p.get("ro.product.manufacturer"),
        "brand": p.get("ro.product.brand"),
        "model": p.get("ro.product.model"),
        "android_release": p.get("ro.build.version.release"),
        "sdk_int": int(sdk) if sdk and sdk.isdigit() else None,
        "security_patch": p.get("ro.build.version.security_patch"),
        "build_type": p.get("ro.build.type"),
    }, []


def _battery(ctx: ScanContext) -> dict[str, str | int | float | bool | None]:
    return parsers.parse_dumpsys_battery(_require_ok(ctx.run(("dumpsys", "battery"))))


def collect_battery_status(ctx: ScanContext) -> tuple[ObservationValue, list[str]]:
    b = _battery(ctx)
    extra = []
    if b.get("values_overridden"):
        extra.append("Battery values have been overridden with 'dumpsys battery set'; readings are not real.")
    return b, extra


def collect_battery_capacity(ctx: ScanContext) -> tuple[ObservationValue, list[str]]:
    value: ObservationValue = {}
    sysfs = {
        "charge_full_uah": "/sys/class/power_supply/battery/charge_full",
        "charge_full_design_uah": "/sys/class/power_supply/battery/charge_full_design",
        "cycle_count": "/sys/class/power_supply/battery/cycle_count",
    }
    denied = False
    for key, path in sysfs.items():
        res = ctx.run(("cat", path))
        if res.permission_denied:
            denied = True
            continue
        try:
            value[key] = parsers.parse_sysfs_int(_require_ok(res))
        except (CheckFailedError, parsers.ParseError):
            continue
    full, design = value.get("charge_full_uah"), value.get("charge_full_design_uah")
    source = None
    if isinstance(full, int) and isinstance(design, int) and design > 0 and full > 0:
        value["capacity_ratio_pct"] = round(full * 100 / design, 1)
        source = "sysfs"
    else:
        res = ctx.run(("dumpsys", "batterystats"), timeout=45)
        if res.returncode == 0:
            caps = parsers.parse_batterystats_capacity(res.stdout)
            value.update(caps)
            learned, est = caps.get("learned_capacity_mah"), caps.get("design_capacity_mah")
            if learned and est and learned > 0:
                value["capacity_ratio_pct"] = round(learned * 100 / est, 1)
                source = "batterystats"
    value["capacity_source"] = source
    if "capacity_ratio_pct" not in value and "cycle_count" not in value:
        status = CheckStatus.PERMISSION_DENIED if denied else CheckStatus.UNSUPPORTED
        raise CheckFailedError(status, "Battery capacity is not exposed to ADB on this device")
    extra = []
    if source == "batterystats":
        extra.append("Design capacity comes from the OEM power profile and may be approximate.")
    return value, extra


def collect_charging_state(ctx: ScanContext) -> tuple[ObservationValue, list[str]]:
    b = _battery(ctx)
    plugged = b.get("plugged")
    status = b.get("status")
    level = b.get("level_pct")
    if plugged == "none":
        assessment = "no_charger_detected"
    elif status in ("charging", "full"):
        assessment = "charging"
    elif status in ("not_charging", "discharging") and isinstance(level, int) and level >= 80:
        assessment = "paused_possibly_by_protection"
    elif status in ("not_charging", "discharging"):
        assessment = "connected_not_charging"
    else:
        assessment = "unknown"
    return {
        "plugged": plugged,
        "battery_status": status,
        "level_pct": level,
        "assessment": assessment,
        "max_charging_current_ma": b.get("max_charging_current_ma"),
    }, []


def collect_storage(ctx: ScanContext) -> tuple[ObservationValue, list[str]]:
    try:
        value: ObservationValue = dict(parsers.parse_df_data(_require_ok(ctx.run(("df", "-k", "/data")))))
        value["method"] = "df"
        return value, []
    except (parsers.ParseError, CheckFailedError):
        # Fallback found necessary on a Pixel 6a running Android 17, whose df output did not match.
        value = dict(parsers.parse_diskstats_free(_require_ok(ctx.run(("dumpsys", "diskstats"), timeout=30))))
        value["method"] = "diskstats"
        return value, ["Measured with Android's disk statistics (df output was not recognised on this device)."]


def collect_memory(ctx: ScanContext) -> tuple[ObservationValue, list[str]]:
    return dict(parsers.parse_meminfo(_require_ok(ctx.run(("cat", "/proc/meminfo"))))), []


def collect_thermal(ctx: ScanContext) -> tuple[ObservationValue, list[str]]:
    return dict(parsers.parse_thermalservice(_require_ok(ctx.run(("dumpsys", "thermalservice"))))), []


def collect_crashes(ctx: ScanContext) -> tuple[ObservationValue, list[str]]:
    now = parsers.parse_device_date(_require_ok(ctx.run(("date", "+%Y-%m-%dT%H:%M:%S"))))
    counts = parsers.parse_dropbox_counts(_require_ok(ctx.run(("dumpsys", "dropbox"), timeout=30)), now)
    extra = []
    if counts.get("history_days_available", 0) < 7:
        extra.append(f"The device only retained about {counts['history_days_available']} day(s) of history.")
    return dict(counts), extra


def collect_boot(ctx: ScanContext) -> tuple[ObservationValue, list[str]]:
    uptime_s = parsers.parse_uptime(_require_ok(ctx.run(("cat", "/proc/uptime"))))
    p = ctx.props()
    reason = p.get("sys.boot.reason") or p.get("persist.sys.boot.reason") or p.get("ro.boot.bootreason")
    now_epoch = None
    try:
        now_epoch = parsers.parse_device_date(ctx.run(("date", "+%Y-%m-%dT%H:%M:%S")).stdout).timestamp()
    except parsers.ParseError:
        pass
    hist = parsers.parse_boot_history(p.get("persist.sys.boot.reason.history"), now_epoch)
    extra = []
    if not reason:
        extra.append("This device does not record a boot reason.")
    return {
        "uptime_hours": round(uptime_s / 3600, 1),
        "last_boot_reason_class": parsers.classify_boot_reason(reason),
        **hist,
    }, extra


def collect_camera(ctx: ScanContext) -> tuple[ObservationValue, list[str]]:
    count = parsers.parse_camera_count(_require_ok(ctx.run(("dumpsys", "media.camera"), timeout=20)))
    return {"camera_devices": count}, []


def collect_touchscreen(ctx: ScanContext) -> tuple[ObservationValue, list[str]]:
    present = parsers.parse_touchscreen(_require_ok(ctx.run(("dumpsys", "input"))))
    return {"touchscreen_registered": present}, []


def collect_connectivity(ctx: ScanContext) -> tuple[ObservationValue, list[str]]:
    airplane = parsers.parse_settings_int(ctx.run(("settings", "get", "global", "airplane_mode_on")).stdout)
    wifi = parsers.parse_settings_int(ctx.run(("settings", "get", "global", "wifi_on")).stdout)
    p = ctx.props()
    sim_states = [s.strip().upper() for s in p.get("gsm.sim.state", "").split(",") if s.strip()]
    net_types = [s.strip() for s in p.get("gsm.network.type", "").split(",") if s.strip()]
    return {
        "airplane_mode": bool(airplane) if airplane is not None else None,
        "wifi_enabled": bool(wifi) if wifi is not None else None,
        "sim_ready": "READY" in sim_states or "LOADED" in sim_states if sim_states else None,
        "sim_absent": all(s == "ABSENT" for s in sim_states) if sim_states else None,
        "sim_slots_reported": len(sim_states),
        "mobile_network_type": next((t for t in net_types if t.upper() != "UNKNOWN"), None),
    }, []


def collect_sensors(ctx: ScanContext) -> tuple[ObservationValue, list[str]]:
    return dict(parsers.parse_sensor_list(_require_ok(ctx.run(("dumpsys", "sensorservice"), timeout=20)))), []


def collect_storage_io(ctx: ScanContext) -> tuple[ObservationValue, list[str]]:
    return dict(parsers.parse_diskstats(_require_ok(ctx.run(("dumpsys", "diskstats"), timeout=30)))), []


def collect_integrity(ctx: ScanContext) -> tuple[ObservationValue, list[str]]:
    value = parsers.classify_software_integrity(ctx.props())
    if all(v is None for v in value.values()):
        raise CheckFailedError(CheckStatus.UNSUPPORTED, "This device does not report its verified-boot state")
    return dict(value), []


COLLECTORS: dict[str, Collector] = {
    "device.identity": collect_identity,
    "battery.status": collect_battery_status,
    "battery.capacity": collect_battery_capacity,
    "charging.state": collect_charging_state,
    "storage.data": collect_storage,
    "memory.ram": collect_memory,
    "thermal.status": collect_thermal,
    "stability.crashes": collect_crashes,
    "stability.boot": collect_boot,
    "camera.enumeration": collect_camera,
    "input.touchscreen": collect_touchscreen,
    "connectivity.basic": collect_connectivity,
    "sensors.inventory": collect_sensors,
    "storage.io": collect_storage_io,
    "device.integrity": collect_integrity,
}

assert set(COLLECTORS) == {c.check_id for c in CHECKS if c.default_in_scan}, "every scan check needs a collector"


def make_observation(check_id: str, status: CheckStatus, value: ObservationValue | None = None,
                     extra: list[str] | None = None, detail: str | None = None) -> Observation:
    return _observation(check_id, status, value, extra, detail)


def _observation(check_id: str, status: CheckStatus, value: ObservationValue | None = None,
                 extra: list[str] | None = None, detail: str | None = None) -> Observation:
    spec = CHECKS_BY_ID[check_id]
    kind = spec.evidence_kind if status == CheckStatus.OK else EvidenceKind.UNAVAILABLE
    return Observation(
        check_id=check_id,
        timestamp=utcnow(),
        status=status,
        value=value or {},
        source=spec.sources[0],
        evidence_kind=kind,
        limitations=[*spec.limitations, *(extra or [])][:10],
        detail=redact(detail),
    )


def run_check(ctx: ScanContext, check_id: str) -> Observation:
    """Run one check. Never raises except DeviceDisconnectedError."""
    spec = CHECKS_BY_ID[check_id]
    try:
        sdk = None
        if spec.min_sdk is not None:
            raw = ctx.props().get("ro.build.version.sdk", "")
            sdk = int(raw) if raw.isdigit() else None
            if sdk is not None and sdk < spec.min_sdk:
                return _observation(check_id, CheckStatus.UNSUPPORTED,
                                    detail=f"Requires Android API {spec.min_sdk}+; device reports {sdk}")
        value, extra = COLLECTORS[check_id](ctx)
        return _observation(check_id, CheckStatus.OK, value, extra)
    except DeviceDisconnectedError:
        raise
    except AdbTimeoutError:
        return _observation(check_id, CheckStatus.TIMEOUT, detail="The device did not respond in time")
    except CheckFailedError as exc:
        return _observation(check_id, exc.status, detail=exc.detail)
    except parsers.ParseError as exc:
        return _observation(check_id, CheckStatus.ERROR, detail=f"Unrecognized output format ({exc})")


def device_ref(serial: str, salt: bytes) -> str:
    return hashlib.sha256(salt + serial.encode()).hexdigest()[:16]


ProgressCallback = Callable[[str, int, int], None]


def run_scan(adb: AdbClient, serial: str, salt: bytes, check_ids: list[str] | None = None,
             progress: ProgressCallback | None = None, scan_id: str | None = None) -> ScanResult:
    """Run the requested checks (default: all). Handles disconnection mid-scan."""
    ids = check_ids or [c.check_id for c in CHECKS if c.default_in_scan]
    ctx = ScanContext(adb=adb, serial=serial)
    started: datetime = utcnow()
    observations: list[Observation] = []
    complete = True
    for i, check_id in enumerate(ids):
        if progress:
            progress(check_id, i, len(ids))
        if not complete:
            observations.append(_observation(check_id, CheckStatus.SKIPPED, detail="Scan aborted: device disconnected"))
            continue
        try:
            observations.append(run_check(ctx, check_id))
        except DeviceDisconnectedError:
            complete = False
            observations.append(_observation(check_id, CheckStatus.DEVICE_DISCONNECTED,
                                             detail="The device was disconnected during this check"))
    if progress:
        progress("done", len(ids), len(ids))

    ident_obs = next((o for o in observations if o.check_id == "device.identity" and o.usable), None)
    iv = ident_obs.value if ident_obs else {}
    identity = DeviceIdentity(
        manufacturer=_s(iv.get("manufacturer")),
        model=_s(iv.get("model")),
        android_release=_s(iv.get("android_release")),
        sdk_int=iv.get("sdk_int") if isinstance(iv.get("sdk_int"), int) else None,  # type: ignore[arg-type]
    )
    from fixmytech.agent import AGENT_VERSION

    return ScanResult(
        scan_id=scan_id or secrets.token_urlsafe(12),
        agent_version=AGENT_VERSION,
        device_ref=device_ref(serial, salt),
        identity=identity,
        started_at=started,
        finished_at=utcnow(),
        complete=complete,
        observations=observations,
    )


def _s(v: object) -> str | None:
    return str(v)[:80] if v not in (None, "") else None
