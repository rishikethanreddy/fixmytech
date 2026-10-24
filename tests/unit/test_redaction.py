from fixmytech.core.redaction import redact


def test_redacts_personal_data():
    text = ("user jane.doe@mail.com called +1 (555) 123-4567 from 10.0.0.12 mac aa:bb:cc:dd:ee:ff "
            "imei 356938035643809 serial R5CR20ABCDE1234 token=abc123 path /sdcard/DCIM/me.jpg https://x.y/z")
    out = redact(text)
    assert out is not None
    for secret in ("jane.doe", "555", "10.0.0.12", "aa:bb", "356938035643809", "abc123", "/sdcard", "https://"):
        assert secret not in out


def test_truncates_and_handles_none():
    assert redact(None) is None
    assert len(redact("x" * 1000) or "") == 300
