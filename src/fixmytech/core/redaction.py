"""Redaction of potentially personal data from free text before it is logged, sent or stored.

Applied to every free-text field that can contain device-derived content (status details, error
messages). Structured observation values are produced by parsers that never copy log text, so
redaction here is a second line of defence, not the primary control.
"""

from __future__ import annotations

import re

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    (re.compile(r"\bhttps?://\S+", re.IGNORECASE), "[url]"),
    (re.compile(r"\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", re.IGNORECASE), "[mac]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[ip]"),
    (re.compile(r"(?:/sdcard|/storage|/data/(?:user|data|media))/\S*"), "[path]"),
    (re.compile(r"\+?\d[\d\s().-]{8,}\d"), "[number]"),  # phone numbers, IMEIs, long ids
    (re.compile(r"\b[0-9a-f]{12,}\b", re.IGNORECASE), "[id]"),  # tokens, hashes
    (re.compile(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{10,}\b"), "[id]"),  # device serials
    (re.compile(r"(?i)\b(token|password|passwd|secret|api[_-]?key|authorization)\b\s*[:=]\s*\S+"), r"\1=[redacted]"),
)


def redact(text: str | None, max_len: int = 300) -> str | None:
    if text is None:
        return None
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    out = " ".join(out.split())
    return out[:max_len]
