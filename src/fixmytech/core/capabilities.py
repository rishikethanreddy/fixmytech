"""Diagnostic capability registry.

Declares every check the agent can attempt, what it needs, how good the resulting evidence is,
and what it can NOT tell you. The agent maps each ``check_id`` to a collector; the backend uses
the same registry to explain unsupported checks and to label evidence quality in reports.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from fixmytech.core.observations import EvidenceKind


class EvidenceQuality(StrEnum):
    HIGH = "high"  # direct reading, stable across devices
    MEDIUM = "medium"  # OS-reported, generally reliable but vendor-dependent
    LOW = "low"  # indirect or heuristic; only meaningful alongside other evidence


class CheckSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    check_id: str
    title: str
    category: str
    description: str
    sources: tuple[str, ...]
    evidence_kind: EvidenceKind
    quality: EvidenceQuality
    min_sdk: int | None = None
    limitations: tuple[str, ...] = ()
    privacy_note: str | None = None
    default_in_scan: bool = True  # False: needs its own explicit consent (e.g. bug report)


CHECKS: tuple[CheckSpec, ...] = (
    CheckSpec(
        check_id="device.identity",
        title="Device identity",
        category="device",
        description="Manufacturer, model, Android version and build type.",
        sources=("adb shell getprop",),
        evidence_kind=EvidenceKind.SYSTEM_REPORTED,
        quality=EvidenceQuality.HIGH,
        limitations=("Model names are marketing/OEM codes and may not match retail names.",),
        privacy_note="Only an allowlist of non-identifying properties is retained; the serial is never sent.",
    ),
    CheckSpec(
        check_id="battery.status",
        title="Battery status",
        category="battery",
        description="Charge level, temperature, voltage, OS-reported health flag and charger state.",
        sources=("adb shell dumpsys battery",),
        evidence_kind=EvidenceKind.MEASURED,
        quality=EvidenceQuality.HIGH,
        limitations=(
            "The 'health' flag only reports gross faults (dead, overheat, over-voltage); "
            "'good' does NOT mean the battery has not degraded.",
            "Temperature is a single point-in-time reading.",
        ),
    ),
    CheckSpec(
        check_id="battery.capacity",
        title="Battery capacity estimate",
        category="battery",
        description="Fuel-gauge learned capacity compared with design capacity, and cycle count where exposed.",
        sources=(
            "adb shell cat /sys/class/power_supply/battery/{charge_full,charge_full_design,cycle_count}",
            "adb shell dumpsys batterystats (capacity lines only)",
        ),
        evidence_kind=EvidenceKind.SYSTEM_REPORTED,
        quality=EvidenceQuality.MEDIUM,
        limitations=(
            "Many devices block these files (SELinux) or omit learned capacity; then degradation is unknown.",
            "Fuel-gauge 'learned capacity' is an estimate that can drift; it is not a lab capacity test.",
        ),
        privacy_note="batterystats output is parsed in memory; only capacity numbers are retained.",
    ),
    CheckSpec(
        check_id="charging.state",
        title="Charging state",
        category="charging",
        description="Whether a connected charger is actually charging the battery at scan time.",
        sources=("adb shell dumpsys battery",),
        evidence_kind=EvidenceKind.INFERRED,
        quality=EvidenceQuality.MEDIUM,
        limitations=(
            "Only meaningful while a charger is connected; the USB debugging cable itself counts as one.",
            "Battery-protection and adaptive-charging features intentionally pause charging (e.g. at 80-85%).",
        ),
    ),
    CheckSpec(
        check_id="storage.data",
        title="Internal storage",
        category="storage",
        description="Free and used space on the user data partition.",
        sources=("adb shell df -k /data",),
        evidence_kind=EvidenceKind.MEASURED,
        quality=EvidenceQuality.HIGH,
    ),
    CheckSpec(
        check_id="memory.ram",
        title="Memory",
        category="performance",
        description="Total and currently available RAM.",
        sources=("adb shell cat /proc/meminfo",),
        evidence_kind=EvidenceKind.MEASURED,
        quality=EvidenceQuality.LOW,
        limitations=("Available RAM fluctuates constantly; Android deliberately keeps RAM full of caches.",),
    ),
    CheckSpec(
        check_id="thermal.status",
        title="Thermal state",
        category="thermal",
        description="OS thermal throttling status and current sensor temperatures.",
        sources=("adb shell dumpsys thermalservice",),
        evidence_kind=EvidenceKind.MEASURED,
        quality=EvidenceQuality.MEDIUM,
        min_sdk=29,
        limitations=(
            "Point-in-time reading; the phone may be idle and cool while connected to the computer.",
            "Sensor names and coverage vary by manufacturer.",
        ),
    ),
    CheckSpec(
        check_id="stability.crashes",
        title="Crash and freeze history",
        category="software",
        description="Counts of app crashes, app freezes (ANRs), native crashes and system crashes in the last 7 days.",
        sources=("adb shell dumpsys dropbox (entry headers only)", "adb shell date"),
        evidence_kind=EvidenceKind.SYSTEM_REPORTED,
        quality=EvidenceQuality.MEDIUM,
        limitations=(
            "The OS keeps a limited history; older or rotated entries are not counted.",
            "Crash counts show software instability, not its root cause.",
        ),
        privacy_note="Only entry tags and timestamps are counted; crash contents and app names are discarded.",
    ),
    CheckSpec(
        check_id="stability.boot",
        title="Uptime and last restart reason",
        category="software",
        description="Time since last boot and the OS-recorded reason for recent restarts.",
        sources=("adb shell cat /proc/uptime", "adb shell getprop"),
        evidence_kind=EvidenceKind.SYSTEM_REPORTED,
        quality=EvidenceQuality.MEDIUM,
        limitations=("Boot-reason properties are vendor-specific and absent on many devices.",),
    ),
    CheckSpec(
        check_id="camera.enumeration",
        title="Camera enumeration",
        category="camera",
        description="Number of camera devices the camera service reports.",
        sources=("adb shell dumpsys media.camera",),
        evidence_kind=EvidenceKind.SYSTEM_REPORTED,
        quality=EvidenceQuality.LOW,
        limitations=(
            "A camera being listed does not mean it produces a good image; image quality is not tested.",
            "Expected camera count for the model is not known to FixMyTech.",
        ),
    ),
    CheckSpec(
        check_id="input.touchscreen",
        title="Touchscreen registration",
        category="display",
        description="Whether the OS has registered a touchscreen input device.",
        sources=("adb shell dumpsys input",),
        evidence_kind=EvidenceKind.SYSTEM_REPORTED,
        quality=EvidenceQuality.LOW,
        limitations=(
            "A registered touchscreen can still have dead zones or ghost touches; touch accuracy is not tested.",
        ),
    ),
    CheckSpec(
        check_id="sensors.inventory",
        title="Sensor inventory",
        category="sensors",
        description="Which motion and environment sensors the OS reports (accelerometer, gyroscope, proximity...).",
        sources=("adb shell dumpsys sensorservice",),
        evidence_kind=EvidenceKind.SYSTEM_REPORTED,
        quality=EvidenceQuality.MEDIUM,
        limitations=("A listed sensor may still give wrong readings; use the guided sensor test to check it responds.",
                     "Expected sensors for the model are not known to FixMyTech."),
        privacy_note="Only sensor types are kept; vendor and part names are discarded.",
    ),
    CheckSpec(
        check_id="storage.io",
        title="Storage write speed",
        category="storage",
        description="Write latency measured by Android's own disk benchmark (dumpsys diskstats).",
        sources=("adb shell dumpsys diskstats",),
        evidence_kind=EvidenceKind.MEASURED,
        quality=EvidenceQuality.LOW,
        limitations=("A single short benchmark; background activity during the scan affects it.",
                     "Slow writes suggest a busy or worn storage chip but do not prove a fault."),
    ),
    CheckSpec(
        check_id="device.integrity",
        title="Software integrity",
        category="software",
        description="Verified-boot state, bootloader lock and build signing (detects unlocked or modified software).",
        sources=("adb shell getprop",),
        evidence_kind=EvidenceKind.SYSTEM_REPORTED,
        quality=EvidenceQuality.MEDIUM,
        limitations=("Some manufacturers do not expose these properties.",),
    ),
    CheckSpec(
        check_id="bugreport.summary",
        title="Bug report crash summary",
        category="software",
        description="Counts of crashes, freezes, low-memory kills and thermal/watchdog events found in an Android "
                    "bug report generated with your consent.",
        sources=("adb bugreport (parsed on this computer, then deleted)",),
        evidence_kind=EvidenceKind.SYSTEM_REPORTED,
        quality=EvidenceQuality.MEDIUM,
        limitations=("Covers only what the phone's logs still hold (often hours to a few days).",
                     "Counts show instability, not its root cause."),
        privacy_note="The report is processed on your computer and deleted; only counts are sent.",
        default_in_scan=False,
    ),
    CheckSpec(
        check_id="connectivity.basic",
        title="Connectivity settings",
        category="connectivity",
        description="Airplane mode, Wi-Fi enabled flag, SIM state and mobile network type.",
        sources=("adb shell settings get global ...", "adb shell getprop"),
        evidence_kind=EvidenceKind.SYSTEM_REPORTED,
        quality=EvidenceQuality.LOW,
        limitations=("Signal quality, Wi-Fi networks and call quality are not measured.",),
        privacy_note="No SSIDs, phone numbers, IMEI or cell identifiers are collected.",
    ),
)

CHECKS_BY_ID: dict[str, CheckSpec] = {c.check_id: c for c in CHECKS}

# Things FixMyTech explicitly cannot assess over ADB. Surfaced in the UI and reports.
NOT_ASSESSABLE: tuple[str, ...] = (
    "Physical screen cracks, dead pixels or display colour defects",
    "Touch accuracy, dead zones and ghost touches",
    "Speaker, earpiece and microphone audio quality",
    "Camera image quality, focus and lens damage",
    "Liquid damage indicators and internal corrosion",
    "Charging port wear or debris (only charging behaviour is observed)",
    "Water resistance / seal integrity",
    "Cellular signal quality and antenna faults",
)
