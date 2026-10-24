"""Pure parsers for ADB command output.

All functions take raw text and return normalized scalars. They never return free text copied from
device logs (apart from allowlisted property values), so nothing personal leaks through them.
Raise ``ParseError`` when output is not in a recognized format; callers turn that into an
``error`` observation rather than guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta


class ParseError(ValueError):
    pass


# ---------------------------------------------------------------------------- devices


@dataclass(frozen=True)
class DeviceEntry:
    serial: str
    state: str  # device | unauthorized | offline | recovery | sideload | bootloader | no permissions ...
    attributes: dict[str, str] = field(default_factory=dict)


def parse_devices_list(output: str) -> list[DeviceEntry]:
    """Parse ``adb devices -l``."""
    devices: list[DeviceEntry] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        # "no permissions" state contains a space; handle it before generic split.
        m = re.match(r"^(\S+)\s+(no permissions)(.*)$", line)
        if m:
            devices.append(DeviceEntry(serial=m.group(1), state="no_permissions", attributes={}))
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state, *rest = parts
        attrs = {}
        for token in rest:
            if ":" in token:
                k, v = token.split(":", 1)
                if k in {"product", "model", "device", "transport_id", "usb"}:
                    attrs[k] = v
        devices.append(DeviceEntry(serial=serial, state=state, attributes=attrs))
    return devices


# ---------------------------------------------------------------------------- getprop

_GETPROP_RE = re.compile(r"^\[([^\]]+)\]:\s*\[(.*)\]\s*$", re.DOTALL)

# Only these properties are ever retained. Everything else in getprop output (including the serial
# number and IMEI-bearing vendor properties) is dropped.
GETPROP_ALLOWLIST = frozenset(
    {
        "ro.product.manufacturer",
        "ro.product.brand",
        "ro.product.model",
        "ro.product.name",
        "ro.build.version.release",
        "ro.build.version.sdk",
        "ro.build.version.security_patch",
        "ro.build.type",
        "sys.boot.reason",
        "sys.boot.reason.last",
        "persist.sys.boot.reason",
        "persist.sys.boot.reason.history",
        "ro.boot.bootreason",
        "gsm.sim.state",
        "gsm.network.type",
        "ro.boot.verifiedbootstate",
        "ro.boot.flash.locked",
        "ro.build.tags",
        "ro.debuggable",
    }
)


def parse_getprop(output: str) -> dict[str, str]:
    """Parse ``getprop`` output. Values may span several lines (e.g. boot reason history)."""
    props: dict[str, str] = {}
    entries: list[str] = []
    for line in output.splitlines():
        if line.startswith("[") and "]: [" in line or not entries:
            entries.append(line)
        else:
            entries[-1] += "\n" + line
    for entry in entries:
        m = _GETPROP_RE.match(entry.strip())
        if m and m.group(1) in GETPROP_ALLOWLIST:
            props[m.group(1)] = m.group(2)[:200]
    if not props and output.strip():
        raise ParseError("unrecognized getprop output")
    return props


# ---------------------------------------------------------------------------- battery

BATTERY_HEALTH = {
    1: "unknown",
    2: "good",
    3: "overheat",
    4: "dead",
    5: "over_voltage",
    6: "unspecified_failure",
    7: "cold",
}
BATTERY_STATUS = {1: "unknown", 2: "charging", 3: "discharging", 4: "not_charging", 5: "full"}


def _to_bool(s: str) -> bool | None:
    s = s.strip().lower()
    if s == "true":
        return True
    if s == "false":
        return False
    return None


def _to_int(s: str) -> int | None:
    try:
        return int(s.strip())
    except ValueError:
        return None


def parse_dumpsys_battery(output: str) -> dict[str, str | int | float | bool | None]:
    if "Current Battery Service state" not in output:
        raise ParseError("unrecognized dumpsys battery output")
    fields: dict[str, str] = {}
    for line in output.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip().lower()] = v.strip()

    level = _to_int(fields.get("level", ""))
    scale = _to_int(fields.get("scale", "")) or 100
    temp_tenths = _to_int(fields.get("temperature", ""))
    health_code = _to_int(fields.get("health", ""))
    status_code = _to_int(fields.get("status", ""))
    plugged = [
        name
        for name, key in (("ac", "ac powered"), ("usb", "usb powered"), ("wireless", "wireless powered"),
                          ("dock", "dock powered"))
        if _to_bool(fields.get(key, "false"))
    ]
    max_current = _to_int(fields.get("max charging current", ""))
    result: dict[str, str | int | float | bool | None] = {
        "level_pct": round(level * 100 / scale) if level is not None and scale else None,
        "temperature_c": temp_tenths / 10 if temp_tenths is not None else None,
        "voltage_mv": _to_int(fields.get("voltage", "")),
        "health": BATTERY_HEALTH.get(health_code, "unknown") if health_code is not None else None,
        "status": BATTERY_STATUS.get(status_code, "unknown") if status_code is not None else None,
        "present": _to_bool(fields.get("present", "")),
        "plugged": ",".join(plugged) if plugged else "none",
        "max_charging_current_ma": max_current // 1000 if max_current else None,
        "technology": fields.get("technology") or None,
        "cycle_count": _to_int(fields.get("cycle count", "")) if "cycle count" in fields else None,
        # dumpsys battery can be overridden with "dumpsys battery set ..."; values are then fake.
        "values_overridden": "UPDATES STOPPED" in output,
    }
    return result


def parse_sysfs_int(output: str) -> int:
    value = _to_int(output.strip().splitlines()[0] if output.strip() else "")
    if value is None:
        raise ParseError("not an integer")
    return value


_CAPACITY_LINES = {
    "estimated battery capacity": "design_capacity_mah",
    "last learned battery capacity": "learned_capacity_mah",
    "min learned battery capacity": "min_learned_capacity_mah",
    "max learned battery capacity": "max_learned_capacity_mah",
}


def parse_batterystats_capacity(output: str) -> dict[str, int]:
    """Extract only the capacity lines from ``dumpsys batterystats``; the rest is discarded."""
    found: dict[str, int] = {}
    for line in output.splitlines():
        stripped = line.strip().lower()
        for prefix, key in _CAPACITY_LINES.items():
            if stripped.startswith(prefix) and key not in found:
                m = re.search(r"(\d+)\s*mah", stripped)
                if m:
                    found[key] = int(m.group(1))
    return found


# ---------------------------------------------------------------------------- storage / memory


def _storage_value(total_kb: int, avail_kb: int, used_kb: int | None = None) -> dict[str, int | float]:
    if total_kb <= 0:
        raise ParseError("storage total is zero")
    used = used_kb if used_kb is not None else total_kb - avail_kb
    return {
        "total_gb": round(total_kb / 1024 / 1024, 2),
        "used_gb": round(used / 1024 / 1024, 2),
        "free_gb": round(avail_kb / 1024 / 1024, 2),
        "free_pct": round(avail_kb * 100 / total_kb, 1),
    }


def parse_df_data(output: str) -> dict[str, int | float]:
    """Parse ``df -k /data``.

    Tolerates the formats seen across Android versions: the mount point may be reported as ``/data`` or as the
    underlying mount (e.g. ``/data/user/0`` or ``/``), and long filesystem names may wrap onto their own line.
    Because the command asks about ``/data`` only, a single data row is accepted whatever its mount label.
    """
    lines = [ln.split() for ln in output.splitlines() if ln.strip()]
    rows: list[list[str]] = []
    pending: list[str] = []
    for parts in lines:
        if parts[0].lower().startswith("filesystem"):
            continue
        if len(parts) == 1 and not parts[0].isdigit():
            pending = parts  # wrapped filesystem name
            continue
        if pending and parts[0].isdigit():
            parts = pending + parts
        pending = []
        if len(parts) >= 4 and all(p.isdigit() for p in parts[1:4]):
            rows.append(parts)
    if not rows:
        raise ParseError("no data row in df output")
    row = next((r for r in rows if r[-1] == "/data"), None) or (rows[0] if len(rows) == 1 else None)
    if row is None:
        raise ParseError("ambiguous df output")
    total_kb, used_kb, avail_kb = int(row[1]), int(row[2]), int(row[3])
    return _storage_value(total_kb, avail_kb, used_kb)


def parse_diskstats_free(output: str) -> dict[str, int | float]:
    """``dumpsys diskstats``: ``Data-Free: 4770100K / 115404100K total = 4% free``."""
    m = re.search(r"Data-Free:\s*(\d+)K\s*/\s*(\d+)K", output)
    if not m:
        raise ParseError("no Data-Free line in diskstats")
    return _storage_value(int(m.group(2)), int(m.group(1)))


def parse_meminfo(output: str) -> dict[str, int | float | None]:
    values: dict[str, int] = {}
    for line in output.splitlines():
        m = re.match(r"^(\w+):\s+(\d+)\s*kB", line.strip())
        if m:
            values[m.group(1)] = int(m.group(2))
    if "MemTotal" not in values:
        raise ParseError("MemTotal missing")
    total = values["MemTotal"]
    avail = values.get("MemAvailable")
    return {
        "total_mb": total // 1024,
        "available_mb": avail // 1024 if avail is not None else None,
        "available_pct": round(avail * 100 / total, 1) if avail is not None else None,
    }


def parse_uptime(output: str) -> float:
    try:
        return float(output.split()[0])
    except (IndexError, ValueError) as exc:
        raise ParseError("bad /proc/uptime") from exc


# ---------------------------------------------------------------------------- thermal

THERMAL_STATUS = {
    0: "none",
    1: "light",
    2: "moderate",
    3: "severe",
    4: "critical",
    5: "emergency",
    6: "shutdown",
}
_TEMP_TYPES = {0: "cpu", 1: "gpu", 2: "battery", 3: "skin", 4: "usb_port"}
_TEMP_RE = re.compile(r"Temperature\{mValue=(-?[\d.]+|NaN), mType=(-?\d+), mName=[^,]*, mStatus=(\d+)\}")


def parse_thermalservice(output: str) -> dict[str, str | int | float | None]:
    m = re.search(r"Thermal Status:\s*(\d+)", output)
    if not m:
        raise ParseError("no Thermal Status line")
    code = int(m.group(1))
    result: dict[str, str | int | float | None] = {
        "throttling_status": THERMAL_STATUS.get(code, "unknown"),
        "throttling_code": code,
    }
    section = output
    idx = output.find("Current temperatures from HAL")
    if idx >= 0:
        section = output[idx:]
        nxt = section.find("Current cooling devices")
        if nxt > 0:
            section = section[:nxt]
    maxima: dict[str, float] = {}
    for tm in _TEMP_RE.finditer(section):
        if tm.group(1) == "NaN":
            continue
        value = float(tm.group(1))
        kind = _TEMP_TYPES.get(int(tm.group(2)))
        if kind is None or not (-40 <= value <= 150):
            continue
        maxima[kind] = max(value, maxima.get(kind, value))
    for kind in _TEMP_TYPES.values():
        result[f"max_{kind}_c"] = maxima.get(kind)
    return result


# ---------------------------------------------------------------------------- crash history

CRASH_TAG_GROUPS: dict[str, tuple[str, ...]] = {
    "app_crashes": ("data_app_crash",),
    "app_native_crashes": ("data_app_native_crash",),
    "app_anrs": ("data_app_anr",),
    "system_app_crashes": ("system_app_crash", "system_app_native_crash"),
    "system_app_anrs": ("system_app_anr",),
    "system_server_crashes": ("system_server_crash", "system_server_native_crash", "system_server_watchdog",
                              "system_server_anr"),
    "tombstones": ("SYSTEM_TOMBSTONE",),
    "kernel_panics": ("SYSTEM_LAST_KMSG", "SYSTEM_RECOVERY_KMSG"),
}
_DROPBOX_ENTRY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(\S+)\s")


def parse_dropbox_counts(output: str, device_now: datetime, window_days: int = 7) -> dict[str, int]:
    if "Drop box contents" not in output and not _DROPBOX_ENTRY_RE.search(output):
        raise ParseError("unrecognized dropbox output")
    cutoff = device_now - timedelta(days=window_days)
    counts = dict.fromkeys(CRASH_TAG_GROUPS, 0)
    tag_to_group = {tag: g for g, tags in CRASH_TAG_GROUPS.items() for tag in tags}
    oldest: datetime | None = None
    for line in output.splitlines():
        m = _DROPBOX_ENTRY_RE.match(line)
        if not m:
            continue  # preview lines (app names, stack snippets) are deliberately ignored
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        oldest = ts if oldest is None or ts < oldest else oldest
        if ts < cutoff:
            continue
        group = tag_to_group.get(m.group(2))
        if group:
            counts[group] += 1
    counts["history_days_available"] = (device_now - oldest).days if oldest else 0
    return counts


def parse_device_date(output: str) -> datetime:
    try:
        return datetime.strptime(output.strip(), "%Y-%m-%dT%H:%M:%S")
    except ValueError as exc:
        raise ParseError("bad date output") from exc


# ---------------------------------------------------------------------------- boot reason

ABNORMAL_BOOT_MARKERS = ("panic", "watchdog", "thermal", "battery", "undervoltage", "brownout", "ocp",
                         "hw_reset", "kernel", "crash", "abnormal", "pmic", "wdog")


def classify_boot_reason(reason: str | None) -> str | None:
    if not reason:
        return None
    r = reason.lower()
    if "thermal" in r:
        return "thermal_shutdown"
    if "battery" in r or "undervoltage" in r or "brownout" in r or "ocp" in r or "pmic" in r:
        return "power_fault"
    if any(m in r for m in ("panic", "watchdog", "wdog", "kernel", "crash", "hw_reset", "abnormal")):
        return "crash_or_watchdog"
    if "userrequested" in r or "shell" in r or "recovery" in r or "ota" in r or r.startswith("reboot") \
            or r.startswith("shutdown,userrequested") or "cold" in r:
        return "normal"
    return "other"


def parse_boot_history(history: str | None, now_epoch: float | None = None, window_days: int = 7) -> dict[str, int]:
    """``persist.sys.boot.reason.history`` is newline/comma separated ``reason,...,epoch`` records."""
    counts = {"abnormal_restarts_recent": 0, "restarts_recorded": 0}
    if not history:
        return counts
    for record in re.split(r"[\n]+|\\n", history):
        record = record.strip()
        if not record:
            continue
        parts = record.split(",")
        epoch = _to_int(parts[-1]) if parts else None
        reason = ",".join(parts[:-1]) if epoch is not None else record
        if now_epoch is not None and epoch is not None and now_epoch - epoch > window_days * 86400:
            continue
        counts["restarts_recorded"] += 1
        if classify_boot_reason(reason) in {"thermal_shutdown", "power_fault", "crash_or_watchdog"}:
            counts["abnormal_restarts_recent"] += 1
    return counts


# ---------------------------------------------------------------------------- camera / input / settings


def parse_camera_count(output: str) -> int:
    m = re.search(r"Number of camera devices:\s*(\d+)", output)
    if not m:
        raise ParseError("camera count not found")
    return int(m.group(1))


def parse_touchscreen(output: str) -> bool:
    if "Input Reader State" not in output and "Event Hub State" not in output:
        raise ParseError("unrecognized dumpsys input output")
    markers = ("touchscreen", "deviceType: touchScreen".lower(), "sources: 0x00001002", "0x00005002")
    lower = output.lower()
    return any(m in lower for m in markers)


def parse_settings_int(output: str) -> int | None:
    s = output.strip()
    if s in ("", "null"):
        return None
    return _to_int(s)


# ---------------------------------------------------------------------------- sensors / disk

SENSOR_TYPES_OF_INTEREST = ("accelerometer", "gyroscope", "magnetic_field", "proximity", "light", "pressure",
                            "step_counter", "gravity")
_SENSOR_TYPE_RE = re.compile(r"type:\s*android\.sensor\.([a-z_]+)\(\d+\)")


def parse_sensor_list(output: str) -> dict[str, int | bool]:
    """Parse the 'Sensor List:' section of ``dumpsys sensorservice`` (sensor types only, no vendor names kept)."""
    idx = output.find("Sensor List:")
    if idx < 0:
        raise ParseError("no 'Sensor List:' section")
    section = output[idx:]
    end = re.search(r"\n\S[^\n]*:\s*\n", section[len("Sensor List:"):])
    if end:
        section = section[: len("Sensor List:") + end.start()]
    types = _SENSOR_TYPE_RE.findall(section)
    if not types:
        raise ParseError("no sensor entries found")
    result: dict[str, int | bool] = {"sensor_count": len(types)}
    for t in SENSOR_TYPES_OF_INTEREST:
        result[f"has_{t}"] = t in types
    return result


def parse_diskstats(output: str) -> dict[str, int | float | None]:
    m = re.search(r"Latency:\s*(\d+)ms\s*\[512B Data Write\]", output)
    if not m:
        raise ParseError("no write latency line")
    speed = re.search(r"Recent Disk Write Speed \(kB/s\)\s*=\s*(\d+)", output)
    return {"write_latency_ms": int(m.group(1)),
            "recent_write_speed_kbps": int(speed.group(1)) if speed else None}


def classify_software_integrity(props: dict[str, str]) -> dict[str, str | bool | None]:
    state = props.get("ro.boot.verifiedbootstate") or None
    locked = props.get("ro.boot.flash.locked")
    tags = props.get("ro.build.tags") or None
    return {
        "verified_boot_state": state,  # green: locked + verified; yellow: custom key; orange: unlocked
        "bootloader_locked": None if locked is None else locked == "1",
        "release_keys": None if tags is None else "release-keys" in tags,
        "debuggable_build": None if "ro.debuggable" not in props else props.get("ro.debuggable") == "1",
    }
