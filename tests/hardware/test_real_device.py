"""Hardware validation against a REAL, authorized Android phone.

    pytest -m hardware -s tests/hardware

Requires: adb installed, exactly one phone connected with USB debugging authorized.
Optional: FIXMYTECH_HW_SERIAL to choose a device. Records what was observed; it cannot judge whether the
values are physically correct, so print them and compare with the phone's own Settings screens.
"""

from __future__ import annotations

import json
import os

import pytest

from fixmytech.agent.adb import AdbClient, AdbError, locate_adb
from fixmytech.agent.collectors import run_scan
from fixmytech.core.observations import CheckStatus

pytestmark = pytest.mark.hardware


@pytest.fixture(scope="module")
def device():
    if locate_adb(os.environ.get("FIXMYTECH_ADB_PATH")) is None:
        pytest.skip("adb not installed")
    adb = AdbClient(os.environ.get("FIXMYTECH_ADB_PATH"))
    try:
        adb.start_server()
        devices = adb.devices()
    except AdbError as exc:
        pytest.skip(f"adb not usable: {exc}")
    wanted = os.environ.get("FIXMYTECH_HW_SERIAL")
    ready = [d for d in devices if d.state == "device" and (not wanted or d.serial == wanted)]
    if not ready:
        states = {d.serial[-4:]: d.state for d in devices}
        pytest.skip(f"no authorized device connected (seen: {states or 'none'})")
    return adb, ready[0].serial


def test_real_scan(device):
    adb, serial = device
    result = run_scan(adb, serial, b"hardware-test-sa")
    print("\n" + json.dumps({o.check_id: {"status": o.status.value, "value": o.value, "detail": o.detail}
                             for o in result.observations}, indent=2, default=str))
    obs = result.by_check()
    assert result.complete, "device disconnected during scan"
    assert obs["device.identity"].status == CheckStatus.OK
    assert obs["battery.status"].status == CheckStatus.OK
    assert obs["storage.data"].status == CheckStatus.OK
    assert serial not in result.model_dump_json()
    for o in result.observations:
        assert o.status != CheckStatus.ERROR or o.detail, f"{o.check_id} errored without explanation"
