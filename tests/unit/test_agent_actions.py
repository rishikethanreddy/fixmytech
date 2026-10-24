import zipfile

import pytest

from fixmytech.agent import actions
from fixmytech.agent.adb import CommandNotAllowedError, CommandResult, action_argv
from tests.conftest import SERIAL, FakeAdb, default_shell_outputs, fx

TOKEN = "t" * 32


def battery(plugged_usb: bool, status: int, level: int = 50) -> CommandResult:
    text = (f"Current Battery Service state:\n  AC powered: false\n  USB powered: {str(plugged_usb).lower()}\n"
            f"  Wireless powered: false\n  status: {status}\n  health: 2\n  present: true\n  level: {level}\n"
            "  scale: 100\n  voltage: 3900\n  temperature: 300\n")
    return CommandResult(0, text, "")


def adb_with_battery(seq: list[CommandResult]) -> FakeAdb:
    out = default_shell_outputs()
    out[("dumpsys", "battery")] = list(seq)
    return FakeAdb(shell=out)


@pytest.mark.parametrize("action,kwargs", [
    ("reverse_add", {"port": 80}), ("reverse_add", {"port": 70000}),
    ("open_phone_tests", {"port": 8000, "token": "bad token; reboot"}),
    ("open_phone_tests", {"port": 8000, "token": "short"}),
    ("bugreport", {"path": "/tmp/x.txt"}),
])
def test_action_arguments_are_validated(action, kwargs):
    with pytest.raises(ValueError):
        action_argv(action, **kwargs)


def test_unknown_action_refused():
    with pytest.raises(CommandNotAllowedError):
        action_argv("install_apk", path="x.apk")


def test_action_argv_shapes():
    assert action_argv("reverse_add", port=8010) == ["reverse", "tcp:8010", "tcp:8010"]
    argv = action_argv("open_phone_tests", port=8010, token=TOKEN)
    assert argv[:2] == ["shell", "am"] and argv[-1] == f"http://localhost:8010/p/{TOKEN}"


def test_connector_test_pass():
    res = actions.connector_test(adb_with_battery([battery(True, 2)] * 5), SERIAL, duration_s=5, sleep=lambda _: None)
    assert res["outcome"] == "pass" and res["measures"]["charging_observed"] is True
    assert res["measures"]["samples"] == 5 and res["measures"]["plug_events"] == 0


def test_connector_test_detects_dropouts():
    seq = [battery(True, 2), battery(False, 3), battery(True, 2), battery(False, 3), battery(True, 2)]
    res = actions.connector_test(adb_with_battery(seq), SERIAL, duration_s=5, sleep=lambda _: None)
    assert res["outcome"] == "partial" and res["measures"]["plug_events"] == 2


def test_connector_test_counts_adb_disconnects():
    out = default_shell_outputs()
    fake = FakeAdb(shell=out, disconnect_after=2)
    res = actions.connector_test(fake, SERIAL, duration_s=4, sleep=lambda _: None)
    assert res["measures"]["adb_disconnects"] >= 1 and res["outcome"] == "partial"


def test_connector_test_not_charging_low_battery_is_fail():
    res = actions.connector_test(adb_with_battery([battery(True, 4, level=40)] * 3), SERIAL, duration_s=3,
                                 sleep=lambda _: None)
    assert res["outcome"] == "fail"


def test_connector_test_high_battery_is_inconclusive():
    res = actions.connector_test(adb_with_battery([battery(True, 4, level=90)] * 3), SERIAL, duration_s=3,
                                 sleep=lambda _: None)
    assert res["outcome"] == "not_run" and res["not_run_reason"] == "inconclusive"


def test_bugreport_summary_counts_only():
    counts = actions.summarize_bugreport_text(fx("bugreport_excerpt.txt"))
    assert counts["fatal_exceptions"] == 2 and counts["anr_events"] == 1
    assert counts["low_memory_kills"] == 2 and counts["native_crashes"] == 1
    assert all(isinstance(v, int) for v in counts.values())


def test_bugreport_file_is_deleted_and_nothing_personal_returned(tmp_path, monkeypatch):
    made = []
    real = actions.tempfile.mkdtemp

    def tracking_mkdtemp(**kw):
        d = real(dir=tmp_path, **kw)
        made.append(d)
        return d

    monkeypatch.setattr(actions.tempfile, "mkdtemp", tracking_mkdtemp)
    counts = actions.collect_bugreport(FakeAdb(), SERIAL)
    assert counts["fatal_exceptions"] == 2
    assert made and not any(p.exists() for p in map(__import__("pathlib").Path, made))
    assert "jane.doe" not in str(counts) and "192.168" not in str(counts)


def test_bugreport_zip_without_main_file(tmp_path):
    z = tmp_path / "b.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("other.txt", "x")
    with pytest.raises(ValueError):
        actions.summarize_bugreport_zip(z)
