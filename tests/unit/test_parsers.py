from datetime import datetime

import pytest

from fixmytech.agent import parsers
from tests.conftest import fx


def test_devices_list_states():
    devs = {d.serial: d for d in parsers.parse_devices_list(fx("devices_l.txt"))}
    assert devs["R5CR20ABCDE"].state == "device"
    assert devs["R5CR20ABCDE"].attributes["model"] == "SM_G991B"
    assert devs["emulator-5554"].state == "unauthorized"
    assert devs["0123456789ABCDEF"].state == "offline"
    assert devs["HT7A1B234567"].state == "no_permissions"


def test_devices_list_empty():
    assert parsers.parse_devices_list("List of devices attached\n\n") == []


def test_getprop_allowlist_drops_serials_and_handles_multiline():
    props = parsers.parse_getprop(fx("getprop.txt"))
    assert props["ro.product.model"] == "SM-G991B"
    assert props["ro.build.version.sdk"] == "34"
    assert "ro.boot.serialno" not in props and "ril.serialnumber" not in props
    assert "kernel_panic,1792300000" in props["persist.sys.boot.reason.history"]
    assert "dalvik.vm.heapsize" not in props


def test_battery_parsing():
    b = parsers.parse_dumpsys_battery(fx("dumpsys_battery.txt"))
    assert b["level_pct"] == 62
    assert b["temperature_c"] == 31.2
    assert b["health"] == "good"
    assert b["status"] == "not_charging"
    assert b["plugged"] == "usb"
    assert b["max_charging_current_ma"] == 500
    assert b["values_overridden"] is False


def test_battery_overridden_and_dead():
    b = parsers.parse_dumpsys_battery(fx("dumpsys_battery_dead.txt"))
    assert b["health"] == "dead" and b["temperature_c"] == 45.5
    assert b["values_overridden"] is True
    assert b["plugged"] == "none"


def test_battery_unrecognized():
    with pytest.raises(parsers.ParseError):
        parsers.parse_dumpsys_battery("Can't find service: battery")


def test_batterystats_only_capacity_lines():
    caps = parsers.parse_batterystats_capacity(fx("batterystats_excerpt.txt"))
    assert caps == {"design_capacity_mah": 4000, "learned_capacity_mah": 3120,
                    "min_learned_capacity_mah": 3080, "max_learned_capacity_mah": 3990}


def test_df():
    d = parsers.parse_df_data(fx("df.txt"))
    assert d["free_pct"] == pytest.approx(4.1, abs=0.1)
    assert d["total_gb"] == pytest.approx(110.06, abs=0.05)


def test_df_bad():
    with pytest.raises(parsers.ParseError):
        parsers.parse_df_data("Filesystem Size Used\n/data 25G 20G 5G 80% /data\n")


def test_meminfo():
    m = parsers.parse_meminfo(fx("meminfo.txt"))
    assert m["total_mb"] == 7583 and m["available_pct"] == pytest.approx(30.2, abs=0.1)


def test_thermal_uses_hal_section_and_skips_nan():
    t = parsers.parse_thermalservice(fx("thermalservice.txt"))
    assert t["throttling_status"] == "light"
    assert t["max_cpu_c"] == 44.0
    assert t["max_battery_c"] == 31.2
    assert t["max_usb_port_c"] is None


def test_dropbox_counts_window_and_privacy():
    now = datetime(2026, 10, 19, 9, 0, 0)
    c = parsers.parse_dropbox_counts(fx("dropbox.txt"), now)
    assert c["app_crashes"] == 2  # the September entry is outside the 7-day window
    assert c["app_anrs"] == 1
    assert c["system_server_crashes"] == 1
    assert c["tombstones"] == 1 and c["kernel_panics"] == 1
    assert c["system_app_crashes"] == 1
    assert c["history_days_available"] >= 40
    assert all(isinstance(v, int) for v in c.values())  # counts only, never app names


@pytest.mark.parametrize("reason,expected", [
    ("reboot,userrequested", "normal"),
    ("kernel_panic", "crash_or_watchdog"),
    ("shutdown,battery", "power_fault"),
    ("shutdown,thermal", "thermal_shutdown"),
    ("reboot,ota", "normal"),
    ("something_else", "other"),
    (None, None),
])
def test_boot_reason_classification(reason, expected):
    assert parsers.classify_boot_reason(reason) == expected


def test_boot_history_window():
    hist = "reboot,userrequested,1760800000\nkernel_panic,1760700000\nshutdown,battery,1700000000"
    c = parsers.parse_boot_history(hist, now_epoch=1760860000)
    assert c == {"abnormal_restarts_recent": 1, "restarts_recorded": 2}


def test_camera_and_touch():
    assert parsers.parse_camera_count(fx("media_camera.txt")) == 4
    assert parsers.parse_touchscreen(fx("input.txt")) is True
    assert parsers.parse_touchscreen(fx("input_no_touch.txt")) is False
    with pytest.raises(parsers.ParseError):
        parsers.parse_touchscreen("garbage")


def test_settings_int():
    assert parsers.parse_settings_int("1\n") == 1
    assert parsers.parse_settings_int("null") is None


def test_sensor_inventory_types_only():
    s = parsers.parse_sensor_list(fx("sensorservice.txt"))
    assert s["sensor_count"] == 4 and s["has_accelerometer"] and s["has_proximity"]
    assert s["has_magnetic_field"] is False
    assert not any("STMicro" in str(v) for v in s.values())
    with pytest.raises(parsers.ParseError):
        parsers.parse_sensor_list("Sensor Device: nothing")


def test_diskstats():
    d = parsers.parse_diskstats(fx("diskstats.txt"))
    assert d == {"write_latency_ms": 3, "recent_write_speed_kbps": 248331}
    with pytest.raises(parsers.ParseError):
        parsers.parse_diskstats("Data-Free: 1K / 2K total = 50% free")


def test_software_integrity():
    props = parsers.parse_getprop(fx("getprop.txt"))
    assert parsers.classify_software_integrity(props) == {
        "verified_boot_state": "green", "bootloader_locked": True, "release_keys": True, "debuggable_build": False}
    assert parsers.classify_software_integrity({})["bootloader_locked"] is None


@pytest.mark.parametrize("text,free_pct", [
    ("Filesystem 1K-blocks Used Available Use% Mounted on\n"
     "/dev/block/dm-61 113115312 73697916 39417396 66% /data/user/0\n", 34.8),
    ("Filesystem      1K-blocks      Used Available Use% Mounted on\n/dev/block/bootdevice/by-name/userdata\n"
     "                115404100 110634000   4770100  96% /data\n", 4.1),
])
def test_df_variants(text, free_pct):
    assert parsers.parse_df_data(text)["free_pct"] == pytest.approx(free_pct, abs=0.1)


def test_diskstats_free_fallback():
    v = parsers.parse_diskstats_free(fx("diskstats.txt"))
    assert v["free_pct"] == pytest.approx(4.1, abs=0.1) and v["total_gb"] > 100
