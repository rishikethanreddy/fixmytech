"""Agent-assisted tests and the consented bug-report workflow.

Both run only on an explicit, separate customer request (never as part of the read-only scan).

Bug report privacy: Android bug reports contain logs that can include personal data (account names, app
activity, network names). FixMyTech writes the report to a private temporary folder on the customer's own
computer, reads it in memory to count a fixed list of crash/stability markers, and deletes the file
immediately. Only the counts leave the agent; no log line is ever copied.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fixmytech.agent import parsers
from fixmytech.agent.adb import AdbClient, AdbTimeoutError, DeviceDisconnectedError, run_action

Progress = Callable[[str, int, int], None]


def connector_test(adb: AdbClient, serial: str, duration_s: int = 45, interval_s: float = 1.0,
                   progress: Progress | None = None, sleep: Callable[[float], None] = time.sleep,
                   on_sample: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Sample the phone's own battery/charger status while the customer gently moves the plug."""
    samples: list[dict[str, Any]] = []
    disconnects = 0
    steps = max(1, int(duration_s / interval_s))
    for i in range(steps):
        if progress:
            progress("sampling", i, steps)
        try:
            res = adb.shell(serial, ("dumpsys", "battery"), timeout=5)
            b = parsers.parse_dumpsys_battery(res.stdout)
            samples.append({"plugged": b["plugged"], "status": b["status"], "level": b["level_pct"],
                            "temp": b["temperature_c"]})
        except (DeviceDisconnectedError, AdbTimeoutError):
            disconnects += 1
            samples.append({"plugged": "lost", "status": None, "level": None, "temp": None})
        except parsers.ParseError:
            samples.append({"plugged": "unknown", "status": None, "level": None, "temp": None})
        if on_sample:
            on_sample({**samples[-1], "second": i + 1, "of": steps,
                       "drops_so_far": sum(1 for a, b in zip(samples, samples[1:], strict=False)
                                           if a["plugged"] not in ("none", "lost", "unknown")
                                           and b["plugged"] in ("none", "lost", "unknown"))})
        sleep(interval_s)
    if progress:
        progress("done", steps, steps)
    return summarize_connector_samples(samples, disconnects)


def summarize_connector_samples(samples: list[dict[str, Any]], disconnects: int) -> dict[str, Any]:
    plugged = [s["plugged"] not in ("none", "lost", "unknown") for s in samples]
    plug_events = sum(1 for a, b in zip(plugged, plugged[1:], strict=False) if a and not b)
    charging = any(s["status"] in ("charging", "full") for s in samples)
    levels = [s["level"] for s in samples if isinstance(s["level"], int)]
    temps = [s["temp"] for s in samples if isinstance(s["temp"], int | float)]
    start_level = levels[0] if levels else None
    measures: dict[str, Any] = {
        "samples": len(samples), "charging_observed": charging, "plug_events": plug_events,
        "adb_disconnects": disconnects, "level_delta": (levels[-1] - levels[0]) if len(levels) >= 2 else None,
        "max_temperature_c": max(temps) if temps else None, "start_level_pct": start_level,
    }
    if not any(plugged) and disconnects == 0:
        return {"outcome": "not_run", "not_run_reason": "inconclusive", "measures": measures,
                "detail": "The phone never reported a power connection. Check the cable is a data/charging cable."}
    if plug_events >= 2 or disconnects >= 1:
        return {"outcome": "partial", "measures": measures,
                "detail": "The connection dropped while the plug was being moved."}
    if charging:
        return {"outcome": "pass", "measures": measures, "detail": "Charging stayed connected."}
    if start_level is not None and start_level >= 80:
        return {"outcome": "not_run", "not_run_reason": "inconclusive", "measures": measures,
                "detail": "Charging was paused at a high battery level (battery protection is common above 80%). "
                          "Repeat the test when the battery is lower."}
    return {"outcome": "fail", "measures": measures,
            "detail": "Power was connected but the phone did not charge during the test."}


# ------------------------------------------------------------------------------------- bug report

BUGREPORT_MARKERS: dict[str, re.Pattern[str]] = {
    "fatal_exceptions": re.compile(r"FATAL EXCEPTION"),
    "anr_events": re.compile(r"\bANR in\b"),
    "native_crashes": re.compile(r"^\*\*\*( \*\*\*){15}\s*$", re.MULTILINE),  # tombstone header line
    "low_memory_kills": re.compile(r"lowmemorykiller|lmkd: Kill"),
    "watchdog_events": re.compile(r"WATCHDOG KILLING SYSTEM PROCESS"),
    "thermal_shutdown_events": re.compile(r"(?i)thermal shutdown|THERMAL_SHUTDOWN"),
    "kernel_panic_events": re.compile(r"Kernel panic"),
}
MAX_TEXT_BYTES = 200 * 1024 * 1024


def summarize_bugreport_text(text: str) -> dict[str, int]:
    return {name: len(pattern.findall(text)) for name, pattern in BUGREPORT_MARKERS.items()}


def summarize_bugreport_zip(path: Path) -> dict[str, int]:
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if re.fullmatch(r"bugreport-[^/]*\.txt", n)]
        if not names:
            raise ValueError("bug report archive has no main text file")
        info = zf.getinfo(names[0])
        if info.file_size > MAX_TEXT_BYTES:
            raise ValueError("bug report text is unexpectedly large")
        text = zf.read(names[0]).decode("utf-8", errors="replace")
    counts = summarize_bugreport_text(text)
    counts["text_megabytes"] = round(len(text) / 1_000_000)
    return counts


def collect_bugreport(adb: AdbClient, serial: str, timeout_s: float = 600) -> dict[str, int]:
    """Run ``adb bugreport`` into a private temp dir, summarize, and always delete the file."""
    workdir = Path(tempfile.mkdtemp(prefix="fixmytech-br-"))
    try:
        target = workdir / "bugreport.zip"
        res = run_action(adb, serial, "bugreport", timeout=timeout_s, path=str(target))
        if not target.is_file():
            raise ValueError("adb did not produce a bug report" + (" (device refused)" if res.returncode else ""))
        return summarize_bugreport_zip(target)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
