"""Shared support for flowtable policy."""

from __future__ import annotations

import json

from _flowtable_connections import SPORT
from _flowtable_rig import (
    DPORT,
    WAN_IP,
    command,
    console_command,
    console_json,
    console_python,
)
from _topology import TARGET_LAN_IF, TARGET_WAN_IF

CONFIG = "/tmp/ask-flowtable-test.conf"

# The offload engine is the C ask-flowtable daemon; the hardware-drain fields it
# reports match the adapter's /proc header (see flowtable/src, cdx_flowtable).
DRAIN_FIELDS = ("bindings", "entries", "handle_refs", "neighbour_refs", "quarantine")


def candidate(r):
    """A policy dict in the historical schema. policy_to_conf() renders it to the
    daemon's native /etc/ask/offload.conf format; the dict form keeps the tests
    readable and lets a case mutate one field."""
    return {"version": 1, "enabled": True, "devices": [TARGET_LAN_IF, TARGET_WAN_IF],
            "scope": [{"source": r.lan_ip, "destination": WAN_IP,
                       "source_port": {"min": SPORT, "max": SPORT + 15}, "destination_port": DPORT}],
            "exclude": []}


def _portspec(value):
    return f"{value['min']}-{value['max']}" if isinstance(value, dict) else str(value)


def _match_to_tokens(m):
    """A scope/exclude dict -> a conf match line's tokens. Empty dict -> 'any'."""
    fields = {"protocol": "proto", "source": "saddr", "destination": "daddr",
              "reply_source": "reply-saddr", "reply_destination": "reply-daddr"}
    ports = {"source_port": "sport", "destination_port": "dport",
             "reply_source_port": "reply-sport", "reply_destination_port": "reply-dport"}
    parts = []
    for k, tok in fields.items():
        if k in m:
            parts.append(f"{tok} {m[k]}")
    for k, tok in ports.items():
        if k in m:
            parts.append(f"{tok} {_portspec(m[k])}")
    if "port" in m:
        parts.append(f"port {_portspec(m['port'])}")
    if "mark" in m:
        parts.append(f"mark {m['mark']['value']:#x}/{m['mark']['mask']:#x}")
    if "name" in m:
        parts.append(f"name {m['name']}")
    return " ".join(parts) if parts else "any"


def policy_to_conf(policy):
    """Render the dict policy to the daemon's line-based conf. Non-schema values
    (e.g. enabled as a string) pass through verbatim so a case can still probe a
    rejection; the daemon validates."""
    lines = [f"version {policy.get('version', 1)}"]
    enabled = policy.get("enabled", True)
    lines.append("enabled " + ("yes" if enabled is True else "no" if enabled is False else str(enabled)))
    lines.append("devices " + " ".join(policy["devices"]))
    for m in policy.get("scope", []):
        lines.append("scope " + _match_to_tokens(m))
    for m in policy.get("exclude", []):
        lines.append("exclude " + _match_to_tokens(m))
    return "\n".join(lines) + "\n"


async def expected_hash(con, policy):
    """The daemon's own fingerprint for a policy, from `check` — the tests no
    longer import a Python hash implementation to mirror. Written to a scratch
    path so it does not disturb whatever CONFIG currently holds."""
    scratch = "/tmp/ask-flowtable-hash.conf"
    await console_python(con, f"from pathlib import Path\nPath({scratch!r}).write_text({policy_to_conf(policy)!r})\n")
    result = await console_command(con, "/usr/sbin/ask-flowtable", "check", "--config", scratch)
    return console_json(result["stdout"])["policy_hash"]


async def apply(con, policy, *, check=True, r=None):
    """Write and apply a policy through the shared DUT UART session."""
    text = policy_to_conf(policy)
    if r is not None:
        written = await r.target.fs_write(r.session, CONFIG, text)
        assert written["errno"] == 0, written
    else:
        await console_python(con, f"from pathlib import Path\nPath({CONFIG!r}).write_text({text!r})\n")
    result = await console_command(con, "/usr/sbin/ask-flowtable", "apply", "--config", CONFIG,
                                   check=check, timeout=40)
    if check:
        result = console_json(result["stdout"])
        assert all(result["drained"][k] == 0 for k in DRAIN_FIELDS), result
    return result


async def stop(con):
    result = await console_command(con, "/usr/sbin/ask-flowtable", "stop", timeout=40)
    state = console_json(result["stdout"])["drained"]
    assert all(state[k] == 0 for k in DRAIN_FIELDS), state
    # A stop is global: multicast is switched off and drained with the rest.
    assert state["mcast_enabled"] == state["mcast_installed"] == state["mroute_installed"] == 0, state


async def installed(con):
    result = await console_command(con, "/usr/sbin/ask-flowtable", "status")
    return console_json(result["stdout"])


async def policy_table_handle(r):
    """The kernel's handle for the controller's table. Replacing the installed
    generation creates a new table, so a changed handle means it was replaced."""
    listing = json.loads((await command(r.target, r.session, "nft", "-j", "list", "tables"))["stdout"])
    return next((item["table"]["handle"] for item in listing["nftables"]
                 if item.get("table", {}).get("family") == "inet"
                 and item["table"].get("name") == "ask_flowtable"), None)
