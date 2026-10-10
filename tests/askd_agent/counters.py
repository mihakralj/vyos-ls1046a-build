"""Snapshot /proc/ASK, /proc/fqid_stats, and ethtool counters.

Diagnostic snapshots accompany each test. Counter meanings depend on their
source: FQ frame counts are queue occupancy, SDK DPAA ethtool RX packets are
software counts, and netdev totals include hardware counts. A generic
positive delta does not prove offload.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

FQID_STATS_ROOT = Path("/proc/fqid_stats")
ASK_PROC_ROOT = Path("/proc/ASK")


def _read_file(p: Path) -> str | None:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return None


def _walk_procdir(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        if p.is_file():
            content = _read_file(p)
            if content is not None:
                out[str(p.relative_to(root))] = content
    return out


def _ethtool_stats(iface: str) -> dict[str, int]:
    try:
        r = subprocess.run(
            ["ethtool", "-S", iface],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    stats: dict[str, int] = {}
    for line in r.stdout.splitlines():
        m = re.fullmatch(r"\s*([^:]+?):\s*(-?\d+)\s*", line)
        if m:
            stats[m.group(1).strip()] = int(m.group(2))
    return stats


def snapshot(interfaces: list[str] | None = None) -> dict:
    """Return diagnostic counters without inferring where packets ran."""
    return {
        "fqid_stats": _walk_procdir(FQID_STATS_ROOT),
        "ask_proc":   _walk_procdir(ASK_PROC_ROOT),
        "ethtool":    {i: _ethtool_stats(i) for i in (interfaces or [])},
    }


def diff_numeric(before: dict, after: dict) -> dict:
    """Pairwise int-delta of matching leaf keys. Non-numeric leaves dropped."""
    out: dict = {}
    for top_key, before_sub in before.items():
        after_sub = after.get(top_key, {})
        if not isinstance(before_sub, dict) or not isinstance(after_sub, dict):
            continue
        sub_out: dict = {}
        for k, v_before in before_sub.items():
            v_after = after_sub.get(k)
            if isinstance(v_before, int) and isinstance(v_after, int):
                sub_out[k] = v_after - v_before
            elif isinstance(v_before, dict) and isinstance(v_after, dict):
                nested: dict = {}
                for kk, vb in v_before.items():
                    va = v_after.get(kk)
                    if isinstance(vb, int) and isinstance(va, int):
                        nested[kk] = va - vb
                if nested:
                    sub_out[k] = nested
        if sub_out:
            out[top_key] = sub_out
    return out
