"""An SA whose HMAC split key SEC fails to derive is refused.

An HMAC SA's split key is a SEC job, run when the SA is installed. When that
job fails -- a full job ring, a descriptor that cannot be mapped, SEC ending it
with an error -- the SA used to be installed anyway, carrying a key SEC never
wrote, and every frame it authenticated failed. The test image's
/proc/cdx_split_key_fail halts the next N such jobs on SEC, through the same
completion a real failure takes.
"""
from __future__ import annotations

import asyncio
import re

import pytest

from _ipsec_helpers import endpoints_down, endpoints_up
from _topology import TARGET_WAN_IF
from _flowtable_rig import (command, read, status_text)
from _flowtable_service_ipsec import (Transform)

KNOB = "/proc/cdx_split_key_fail"
# A pair, an SPI and a reqid of its own. Nothing is sent: the peer does not
# exist.
LOCAL, PEER = "198.18.110.1", "198.18.110.2"
REQID = "49309"
IDENTITY = ["src", LOCAL, "dst", PEER, "proto", "esp", "spi", hex(0x534B0001)]
# What a refused install must leave where it was: the SAs the adapter
# installed and the SAs cdx's cache holds. The flow side is not compared: the
# service admits and ages unrelated flows while this runs.
UNMOVED = ("ipsec_sas", "ipsec_sa_cache", "fatal")


async def backend(agent, session):
    state = status_text(await read(agent, session, "/proc/cdx_flowtable"))
    return {key: state[key] for key in UNMOVED}


async def settled(agent, session):
    """The backend's figures once they stop moving. An SA deleted by a test
    before this one leaves the adapter asynchronously -- its retirement is
    queued work, and cdx releases its context a period later -- so the counts
    are read until two readings half a second apart agree."""
    previous = await backend(agent, session)
    for _ in range(40):
        await asyncio.sleep(0.5)
        current = await backend(agent, session)
        if current == previous:
            return current
        previous = current
    pytest.fail(f"the backend's SA counts kept moving: {previous}")


async def add(agent, session):
    """The outbound SA, CBC with HMAC-SHA-256, offloaded: its install runs
    one split-key job."""
    return await command(agent, session, "ip", "xfrm", "state", "add", *IDENTITY,
                         "mode", "tunnel", "reqid", REQID, *Transform().algorithms,
                         "offload", "packet", "dev", TARGET_WAN_IF, "dir", "out", check=False)


async def get(agent, session):
    return await command(agent, session, "ip", "xfrm", "state", "get", *IDENTITY, check=False)


async def test_failure(aiohttp_session, target_agent, splat_window):
    """A failed split-key job refuses the SA with the reason and leaves
    nothing behind -- no state, no SA in cdx's cache -- and once disarmed, the
    same SA installs in hardware."""
    await endpoints_up(target_agent, aiohttp_session, iface=TARGET_WAN_IF, local=LOCAL,
                       peer=PEER, lladdr="02:00:00:00:0a:02")
    try:
        before = await settled(target_agent, aiohttp_session)
        armed = await target_agent.fs_write(aiohttp_session, KNOB, "1")
        assert armed["errno"] == 0, armed
        refused = await add(target_agent, aiohttp_session)
        shown = await get(target_agent, aiohttp_session)
        after = await backend(target_agent, aiohttp_session)
        knob = (await read(target_agent, aiohttp_session, KNOB)).strip()
        result = {"add": refused, "get": shown, "before": before, "after": after, "knob": knob}
        assert refused["rc"] != 0, result
        assert "cdx: SEC could not derive the HMAC split key" in refused["stderr"], result
        # The one armed job was this install's.
        assert knob == "armed=0", result
        # Packet offload has no software fallback: nothing was installed,
        # in xfrm or in cdx.
        assert shown["rc"] != 0, result
        assert after == before, result

        disarmed = await target_agent.fs_write(aiohttp_session, KNOB, "0")
        assert disarmed["errno"] == 0, disarmed
        added = await add(target_agent, aiohttp_session)
        shown = await get(target_agent, aiohttp_session)
        installed = await backend(target_agent, aiohttp_session)
        result = {"add": added, "get": shown, "before": before, "installed": installed}
        assert added["rc"] == 0, result
        assert re.search(rf"crypto offload parameters: dev {TARGET_WAN_IF} dir out mode packet",
                         shown["stdout"]), result
        assert installed["ipsec_sas"] == before["ipsec_sas"] + 1, result
        assert installed["ipsec_sa_cache"] == before["ipsec_sa_cache"] + 1, result
    finally:
        await target_agent.fs_write(aiohttp_session, KNOB, "0")
        await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "delete", *IDENTITY,
                      check=False)
        await endpoints_down(target_agent, aiohttp_session, iface=TARGET_WAN_IF, local=LOCAL,
                             peer=PEER)
