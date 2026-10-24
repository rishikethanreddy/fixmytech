"""Shared test fixtures: a fake ADB that replays recorded command output, and app factories."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from fixmytech.agent.adb import AdbClient, AdbTimeoutError, CommandResult

warnings.filterwarnings("ignore", message=".*httpx2.*")

FIXTURES = Path(__file__).parent / "fixtures" / "adb"
SERIAL = "R5CR20ABCDE"


def fx(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def default_shell_outputs() -> dict[tuple[str, ...], CommandResult | Exception]:
    ok = lambda name: CommandResult(0, fx(name), "")  # noqa: E731
    denied = CommandResult(1, "", "cat: /sys/class/power_supply/battery/charge_full: Permission denied")
    return {
        ("getprop",): ok("getprop.txt"),
        ("dumpsys", "battery"): ok("dumpsys_battery.txt"),
        ("dumpsys", "batterystats"): ok("batterystats_excerpt.txt"),
        ("dumpsys", "thermalservice"): ok("thermalservice.txt"),
        ("dumpsys", "dropbox"): ok("dropbox.txt"),
        ("dumpsys", "media.camera"): ok("media_camera.txt"),
        ("dumpsys", "input"): ok("input.txt"),
        ("dumpsys", "sensorservice"): ok("sensorservice.txt"),
        ("dumpsys", "diskstats"): ok("diskstats.txt"),
        ("df", "-k", "/data"): ok("df.txt"),
        ("cat", "/proc/meminfo"): ok("meminfo.txt"),
        ("cat", "/proc/uptime"): CommandResult(0, "86400.50 12345.00\n", ""),
        ("cat", "/sys/class/power_supply/battery/charge_full"): denied,
        ("cat", "/sys/class/power_supply/battery/charge_full_design"): denied,
        ("cat", "/sys/class/power_supply/battery/cycle_count"): CommandResult(0, "612\n", ""),
        ("date", "+%Y-%m-%dT%H:%M:%S"): CommandResult(0, "2026-10-19T09:00:00\n", ""),
        ("settings", "get", "global", "airplane_mode_on"): CommandResult(0, "0\n", ""),
        ("settings", "get", "global", "wifi_on"): CommandResult(0, "1\n", ""),
    }


class FakeAdb(AdbClient):
    """Replays recorded output. ``disconnect_after`` simulates unplugging after N shell commands."""

    def __init__(self, shell: dict[tuple[str, ...], Any] | None = None,
                 devices_output: str | None = None, disconnect_after: int | None = None,
                 installed: bool = True) -> None:
        super().__init__("fake-adb")
        self.shell_outputs = shell if shell is not None else default_shell_outputs()
        self.devices_output = devices_output if devices_output is not None else fx("devices_l.txt")
        self.disconnect_after = disconnect_after
        self.installed = installed
        self.calls: list[list[str]] = []

    @property
    def adb_path(self) -> str:
        from fixmytech.agent.adb import AdbNotFoundError

        if not self.installed:
            raise AdbNotFoundError("adb executable not found")
        return "fake-adb"

    def _run(self, args: list[str], timeout: float | None = None) -> CommandResult:
        _ = self.adb_path
        self.calls.append(args)
        if args[0] == "version":
            return CommandResult(0, "Android Debug Bridge version 1.0.41\nVersion 35.0.2-fake\n", "")
        if args[0] == "start-server":
            return CommandResult(0, "", "")
        if args[:2] == ["devices", "-l"]:
            return CommandResult(0, self.devices_output, "")
        assert args[0] == "-s"
        if args[2] == "reverse":
            return CommandResult(0, "", "")
        if args[2] == "bugreport":
            import zipfile

            with zipfile.ZipFile(args[3], "w") as zf:
                zf.writestr("bugreport-o1s-2026-10-19.txt", fx("bugreport_excerpt.txt"))
                zf.writestr("FS/data/anr/anr_1", "not read")
            return CommandResult(0, "Bug report copied to " + args[3], "")
        assert args[2] == "shell"
        if args[3:5] == ["am", "start"]:
            return CommandResult(0, "Starting: Intent { act=android.intent.action.VIEW }", "")
        shell_calls = sum(1 for c in self.calls if len(c) > 2 and c[2] == "shell")
        if self.disconnect_after is not None and shell_calls > self.disconnect_after:
            return CommandResult(1, "", f"error: device '{args[1]}' not found")
        out = self.shell_outputs.get(tuple(args[3:]))
        if isinstance(out, list):  # a sequence of successive outputs (e.g. battery samples over time)
            out = out.pop(0) if len(out) > 1 else out[0]
        if out is None:
            return CommandResult(127, "", f"/system/bin/sh: {args[3]}: not found")
        if isinstance(out, AdbTimeoutError):
            raise out
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def fake_adb() -> FakeAdb:
    return FakeAdb()


def asgi_transport_for(app: object) -> httpx.MockTransport:
    """An httpx transport that routes synchronous client calls into an ASGI app (e.g. the mock FlipValue)."""
    from fastapi.testclient import TestClient

    tc = TestClient(app)  # type: ignore[arg-type]

    def handler(request: httpx.Request) -> httpx.Response:
        r = tc.request(request.method, request.url.path, content=request.content,
                       headers={k: v for k, v in request.headers.items() if k.lower() != "host"})
        return httpx.Response(r.status_code, headers=r.headers, content=r.content)

    return httpx.MockTransport(handler)


@pytest.fixture(scope="session")
def pricing_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A pricing dataset built by the real ingestion code from the saved Samsung India excerpts."""
    from fixmytech.pricing import ingest
    from fixmytech.pricing.store import PricingStore

    d = tmp_path_factory.mktemp("pricing")
    fx = Path(__file__).parent / "fixtures" / "pricing"
    report = ingest.run(PricingStore(d), ["samsung_in_parts", "samsung_in_labour"],
                        {"samsung_in_parts": fx / "samsung_in_parts_excerpt.html",
                         "samsung_in_labour": fx / "samsung_in_labour_excerpt.html"})
    assert all(s["status"] == "ok" for s in report["sources"].values())
    return d


@pytest.fixture
def make_backend(pricing_dir: Path, tmp_path: Path) -> Callable[..., object]:
    from fastapi.testclient import TestClient

    from fixmytech.integrations.flipvalue.client import FlipValueClient, FlipValueSettings
    from fixmytech.integrations.flipvalue.mock_server import create_mock_app
    from fixmytech.server.app import create_app
    from fixmytech.server.config import Settings

    def factory(flipvalue_mode: str = "mock", transport: httpx.BaseTransport | None = None,
                **overrides: object) -> TestClient:
        overrides.setdefault("FIXMYTECH_PRICING_DIR", str(pricing_dir))
        overrides.setdefault("FIXMYTECH_PARTNER_CONFIG_PATH", str(tmp_path / "partner_service.json"))
        overrides.setdefault("FIXMYTECH_ALLOW_MOCK_VALUATION", flipvalue_mode == "mock")
        settings = Settings(FIXMYTECH_DATABASE_URL="sqlite:///:memory:", FIXMYTECH_ENV="test",
                            FLIPVALUE_MODE=flipvalue_mode, **overrides)  # type: ignore[arg-type]
        fv_transport = transport or asgi_transport_for(create_mock_app(api_key="test-key", timeout_sleep_s=0.3))
        client = FlipValueClient(FlipValueSettings(mode=flipvalue_mode,  # type: ignore[arg-type]
                                                   base_url="https://flipvalue.test", api_key="test-key",
                                                   timeout_s=2.0, max_retries=1, backoff_s=0.0),
                                 transport=fv_transport, sleep=lambda _: None)
        return TestClient(create_app(settings, flipvalue=client, serve_frontend=False))

    return factory
