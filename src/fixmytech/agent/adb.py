"""Safe, read-only ADB command runner.

Safety properties:
* Commands are fixed argv lists, never shell strings; ``shell=False`` always.
* Every remote shell command must appear in ``ALLOWED_SHELL_COMMANDS``. Anything else is refused,
  even if a future code change tries to run it. There is no way for a customer or browser to supply
  a command.
* The device serial must come from ``adb devices`` output and match a strict pattern.
* Every call has a timeout.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from fixmytech.agent import parsers

SERIAL_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")

# Read-only commands only. Adding to this list requires review: nothing here may write to the device,
# change settings, install packages, reboot, or read user content.
ALLOWED_SHELL_COMMANDS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("getprop",),
        ("dumpsys", "battery"),
        ("dumpsys", "batterystats"),
        ("dumpsys", "thermalservice"),
        ("dumpsys", "dropbox"),
        ("dumpsys", "media.camera"),
        ("dumpsys", "input"),
        ("dumpsys", "sensorservice"),
        ("dumpsys", "diskstats"),
        ("df", "-k", "/data"),
        ("cat", "/proc/meminfo"),
        ("cat", "/proc/uptime"),
        ("cat", "/sys/class/power_supply/battery/charge_full"),
        ("cat", "/sys/class/power_supply/battery/charge_full_design"),
        ("cat", "/sys/class/power_supply/battery/cycle_count"),
        ("date", "+%Y-%m-%dT%H:%M:%S"),
        ("settings", "get", "global", "airplane_mode_on"),
        ("settings", "get", "global", "wifi_on"),
    }
)

_DISCONNECT_MARKERS = (
    "device offline",
    "no devices/emulators found",
    "device not found",
    "not found",
    "device unauthorized",
    "closed",
    "protocol fault",
    "connection reset",
)


TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,100}$")


class AdbError(Exception):
    """Base class for ADB failures."""


class AdbNotFoundError(AdbError):
    pass


class AdbServerError(AdbError):
    pass


class AdbTimeoutError(AdbError):
    pass


class DeviceDisconnectedError(AdbError):
    pass


class CommandNotAllowedError(AdbError):
    pass


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def permission_denied(self) -> bool:
        text = (self.stdout + self.stderr).lower()
        return "permission denied" in text

    @property
    def missing(self) -> bool:
        text = (self.stdout + self.stderr).lower()
        return "no such file" in text or "can't find service" in text or "unknown command" in text


def locate_adb(configured: str | None = None) -> str | None:
    """Find adb: explicit config, PATH, then the default Android SDK location on Windows."""
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    found = shutil.which("adb")
    if found:
        candidates.append(found)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(str(Path(local) / "Android" / "Sdk" / "platform-tools" / "adb.exe"))
    android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if android_home:
        candidates.append(str(Path(android_home) / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")))
    for c in candidates:
        if Path(c).is_file():
            return c
    return None


class AdbClient:
    def __init__(self, adb_path: str | None = None, default_timeout: float = 15.0) -> None:
        self._configured = adb_path
        self.default_timeout = default_timeout

    @property
    def adb_path(self) -> str:
        path = locate_adb(self._configured)
        if path is None:
            raise AdbNotFoundError("adb executable not found")
        return path

    def _run(self, args: list[str], timeout: float | None = None) -> CommandResult:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(
                [self.adb_path, *args],
                capture_output=True,
                timeout=timeout or self.default_timeout,
                shell=False,
                check=False,
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            raise AdbNotFoundError("adb executable not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbTimeoutError(f"adb {args[0]} timed out") from exc
        return CommandResult(
            returncode=proc.returncode,
            stdout=proc.stdout.decode("utf-8", errors="replace"),
            stderr=proc.stderr.decode("utf-8", errors="replace"),
        )

    def version(self) -> str:
        res = self._run(["version"], timeout=10)
        first = res.stdout.strip().splitlines()[:2]
        return " / ".join(line.strip() for line in first)

    def start_server(self) -> None:
        res = self._run(["start-server"], timeout=30)
        if res.returncode != 0:
            raise AdbServerError("ADB server failed to start")

    def devices(self) -> list[parsers.DeviceEntry]:
        res = self._run(["devices", "-l"], timeout=20)
        if res.returncode != 0:
            raise AdbServerError("adb devices failed")
        return parsers.parse_devices_list(res.stdout)

    def shell(self, serial: str, command: tuple[str, ...], timeout: float | None = None) -> CommandResult:
        if command not in ALLOWED_SHELL_COMMANDS:
            raise CommandNotAllowedError(f"command not in allowlist: {command[0]!r}")
        if not SERIAL_RE.match(serial):
            raise ValueError("invalid device serial")
        res = self._run(["-s", serial, "shell", *command], timeout=timeout)
        if res.returncode != 0 and is_adb_transport_error(res.stderr):
            raise DeviceDisconnectedError("device disconnected or unavailable")
        return res


def is_adb_transport_error(stderr: str) -> bool:
    """True if stderr is adb itself complaining about the device (not the remote command failing).

    adb's own errors are prefixed ``error:`` or ``adb:``; remote failures such as
    ``cat: /sys/...: Permission denied`` are not.
    """
    for line in stderr.lower().splitlines():
        line = line.strip()
        if (line.startswith("error:") or line.startswith("adb:")) and any(m in line for m in _DISCONNECT_MARKERS):
            return True
    return False


# ----------------------------------------------------------------------------- explicit, consented actions
# These are NOT part of the read-only scan. Each needs its own explicit customer request in the UI and is
# built only from validated integers/tokens, never from free text.


def _port(port: int) -> int:
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("invalid port")
    return port


# A public https origin only (no path, query or credentials); the agent additionally requires it to be its paired site.
SITE_RE = re.compile(r"^https://[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(:\d{2,5})?$")


def action_argv(action: str, *, port: int | None = None, token: str | None = None,
                path: str | None = None, site: str | None = None) -> list[str]:
    if action == "reverse_add":
        p = _port(port or 0)
        return ["reverse", f"tcp:{p}", f"tcp:{p}"]
    if action == "reverse_remove":
        return ["reverse", "--remove", f"tcp:{_port(port or 0)}"]
    if action == "open_phone_tests":
        if not token or not TOKEN_RE.match(token):
            raise ValueError("invalid token")
        url = f"http://localhost:{_port(port or 0)}/p/{token}"
        return ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url]
    if action == "open_site_tests":
        if not token or not TOKEN_RE.match(token):
            raise ValueError("invalid token")
        if not site or not SITE_RE.match(site):
            raise ValueError("invalid site address")
        return ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", f"{site}/p/{token}"]
    if action == "bugreport":
        if not path or not path.endswith(".zip"):
            raise ValueError("invalid bugreport path")
        return ["bugreport", path]
    raise CommandNotAllowedError(f"unknown action {action!r}")


def run_action(client: AdbClient, serial: str, action: str, timeout: float = 30.0, **kwargs: object) -> CommandResult:
    if not SERIAL_RE.match(serial):
        raise ValueError("invalid device serial")
    argv = action_argv(action, **kwargs)  # type: ignore[arg-type]
    res = client._run(["-s", serial, *argv], timeout=timeout)
    if res.returncode != 0 and is_adb_transport_error(res.stderr):
        raise DeviceDisconnectedError("device disconnected or unavailable")
    return res
