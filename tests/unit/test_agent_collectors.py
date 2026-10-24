import pytest

from fixmytech.agent.adb import (
    ALLOWED_SHELL_COMMANDS,
    AdbTimeoutError,
    CommandNotAllowedError,
    CommandResult,
    is_adb_transport_error,
)
from fixmytech.agent.collectors import COLLECTORS, run_scan
from fixmytech.core.capabilities import CHECKS_BY_ID
from fixmytech.core.observations import CheckStatus, EvidenceKind
from tests.conftest import SERIAL, FakeAdb, default_shell_outputs, fx

SALT = b"0123456789abcdef"


def test_allowlist_blocks_anything_else():
    adb = FakeAdb()
    for cmd in [("reboot",), ("pm", "uninstall", "x"), ("rm", "-rf", "/sdcard"), ("getprop", ";", "reboot"),
                ("settings", "put", "global", "airplane_mode_on", "1"), ("cat", "/sdcard/DCIM/x.jpg")]:
        with pytest.raises(CommandNotAllowedError):
            adb.shell(SERIAL, cmd)
    assert adb.calls == []


def test_allowlist_is_read_only_commands():
    verbs = {c[0] for c in ALLOWED_SHELL_COMMANDS}
    assert verbs <= {"getprop", "dumpsys", "df", "cat", "date", "settings"}
    assert all(c[1] == "get" for c in ALLOWED_SHELL_COMMANDS if c[0] == "settings")


def test_invalid_serial_rejected():
    with pytest.raises(ValueError):
        FakeAdb().shell("abc;reboot", ("getprop",))


def test_transport_error_detection():
    assert is_adb_transport_error("error: device 'X' not found")
    assert is_adb_transport_error("adb: device offline")
    assert not is_adb_transport_error("cat: /sys/x: Permission denied")
    assert not is_adb_transport_error("/system/bin/sh: df: not found")


def test_every_registered_check_has_a_collector():
    assert set(COLLECTORS) == {c for c, s in CHECKS_BY_ID.items() if s.default_in_scan}


def test_full_scan_with_recorded_outputs():
    result = run_scan(FakeAdb(), SERIAL, SALT)
    obs = result.by_check()
    assert result.complete
    assert len(result.device_ref) == 16 and SERIAL not in result.model_dump_json()
    assert result.identity.model == "SM-G991B" and result.identity.sdk_int == 34
    assert obs["battery.status"].status == CheckStatus.OK
    assert obs["battery.status"].evidence_kind == EvidenceKind.MEASURED
    assert obs["storage.data"].value["free_pct"] < 5
    assert obs["stability.crashes"].value["system_server_crashes"] == 1
    assert obs["stability.boot"].value["abnormal_restarts_recent"] >= 1
    assert obs["charging.state"].value["assessment"] == "connected_not_charging"
    # sysfs capacity is denied, so the collector falls back to batterystats capacity lines.
    cap = obs["battery.capacity"].value
    assert cap["capacity_source"] == "batterystats" and cap["capacity_ratio_pct"] == 78.0
    assert cap["cycle_count"] == 612
    assert obs["connectivity.basic"].value["sim_ready"] is True
    assert obs["input.touchscreen"].value["touchscreen_registered"] is True
    for o in result.observations:  # every observation carries provenance and limitations
        assert o.source and o.timestamp and isinstance(o.limitations, list)


def test_unavailable_checks_are_not_reported_as_healthy():
    outputs = default_shell_outputs()
    for k in list(outputs):
        if k[:2] == ("dumpsys", "batterystats") or k[0] == "cat" and "power_supply" in k[1]:
            outputs[k] = CommandResult(1, "", "Permission denied")
    outputs[("dumpsys", "thermalservice")] = AdbTimeoutError("timeout")
    outputs[("dumpsys", "media.camera")] = CommandResult(0, "Can't find service: media.camera\n", "")
    result = run_scan(FakeAdb(shell=outputs), SERIAL, SALT)
    obs = result.by_check()
    assert obs["battery.capacity"].status == CheckStatus.PERMISSION_DENIED
    assert obs["thermal.status"].status == CheckStatus.TIMEOUT
    assert obs["camera.enumeration"].status == CheckStatus.UNSUPPORTED
    for cid in ("battery.capacity", "thermal.status", "camera.enumeration"):
        assert obs[cid].value == {} and obs[cid].evidence_kind == EvidenceKind.UNAVAILABLE


def test_disconnect_mid_scan_marks_remaining_checks():
    result = run_scan(FakeAdb(disconnect_after=3), SERIAL, SALT)
    statuses = [o.status for o in result.observations]
    assert not result.complete
    assert CheckStatus.DEVICE_DISCONNECTED in statuses
    first_bad = statuses.index(CheckStatus.DEVICE_DISCONNECTED)
    assert all(s == CheckStatus.SKIPPED for s in statuses[first_bad + 1:])


def test_min_sdk_unsupported():
    outputs = default_shell_outputs()
    outputs[("getprop",)] = CommandResult(0, fx("getprop.txt").replace("[34]", "[28]"), "")
    obs = run_scan(FakeAdb(shell=outputs), SERIAL, SALT, ["thermal.status"]).by_check()
    assert obs["thermal.status"].status == CheckStatus.UNSUPPORTED


def test_error_details_are_redacted():
    outputs = default_shell_outputs()
    outputs[("df", "-k", "/data")] = CommandResult(1, "", "df: error for user bob@example.com at 192.168.1.20")
    obs = run_scan(FakeAdb(shell=outputs), SERIAL, SALT, ["storage.data"]).by_check()
    assert "bob@example.com" not in (obs["storage.data"].detail or "")
    assert "192.168" not in (obs["storage.data"].detail or "")


def test_storage_falls_back_to_diskstats_when_df_unrecognised():
    outputs = default_shell_outputs()
    outputs[("df", "-k", "/data")] = CommandResult(0, "something unexpected\n", "")
    obs = run_scan(FakeAdb(shell=outputs), SERIAL, SALT, ["storage.data"]).by_check()["storage.data"]
    assert obs.status == CheckStatus.OK and obs.value["method"] == "diskstats"
    assert any("disk statistics" in lim for lim in obs.limitations)
