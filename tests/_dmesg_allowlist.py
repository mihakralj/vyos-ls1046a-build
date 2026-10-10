"""Load and apply the checked-in dmesg splat allowlist.

The allowlist (golden/dmesg_allowlist.yaml) holds substrings/regexes for
splat-shaped kernel-log lines that are known-benign — historical
fixtures, boot-time announcements, etc. The splat_window fixture runs
each candidate splat through filter_splats(); anything matching an
applicable allowlist entry is dropped.

Entries carry a mandatory `expires` date. load_allowlist() raises on
expiry — that converts "I'll look at it later" into a CI failure that
forces a fresh review.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

import yaml

ALLOWLIST_PATH = Path(__file__).parent / "golden" / "dmesg_allowlist.yaml"


class AllowlistError(AssertionError):
    pass


def _compile_entry(entry: dict, today: datetime.date) -> dict:
    pattern = entry.get("pattern")
    reason = entry.get("reason")
    expires = entry.get("expires")
    if not pattern or not reason or not expires:
        raise AllowlistError(
            f"allowlist entry missing pattern/reason/expires: {entry!r}"
        )
    if isinstance(expires, str):
        expires = datetime.date.fromisoformat(expires)
    if not isinstance(expires, datetime.date):
        raise AllowlistError(
            f"allowlist entry has non-date expires: {entry!r}"
        )
    if expires < today:
        raise AllowlistError(
            f"allowlist entry expired ({expires.isoformat()}): {pattern!r}; "
            f"re-review the underlying behaviour and bump expires or remove."
        )
    applies_to = entry.get("applies_to")
    if applies_to is None:
        applies_to = ["*"]
    elif isinstance(applies_to, str):
        applies_to = [applies_to]
    return {
        "regex": re.compile(pattern),
        "reason": reason,
        "expires": expires,
        "applies_to": list(applies_to),
    }


def load_allowlist(path: Path | None = None) -> list[dict]:
    p = path or ALLOWLIST_PATH
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text()) or {}
    entries = raw.get("allowlist") or []
    today = datetime.date.today()
    return [_compile_entry(e, today) for e in entries]


def _entry_applies_to(entry: dict, test_name: str) -> bool:
    for pat in entry["applies_to"]:
        if pat == "*" or re.search(pat, test_name):
            return True
    return False


def filter_splats(
    splats: list[str], test_name: str, allowlist: list[dict]
) -> list[str]:
    out: list[str] = []
    for line in splats:
        suppressed = False
        for entry in allowlist:
            if not _entry_applies_to(entry, test_name):
                continue
            if entry["regex"].search(line):
                suppressed = True
                break
        if not suppressed:
            out.append(line)
    return out
