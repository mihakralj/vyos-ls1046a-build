"""A discard gives its group id up to a stream somebody wants.

A bridged stream nobody wants is dropped by a classifier entry of its own,
which holds a group id -- a family has 512, and both learners draw on them --
for as long as the stream keeps arriving. An upstream that never stops would
hold them for good, and every stream a listener joins afterwards would wait in
software, refused-failed. So an add that replicates and finds no id takes one
from the discard that counted the fewest frames over the last refresh, and is
made again at once.

The case fills a family from one port, in two waves: one port's senders may
hold half the family's places, so the second wave proves that discards hold
none of them (A314). Each wave is one stream per fill group with a static
membership on the LAN port, carried in hardware; its memberships then go and its
streams do not, so every entry becomes a discard and keeps its id. A host behind
the LAN port then joins one more group arriving on that same port, whose stream
has to be carried in hardware straight away -- one discard gone for it, nothing
counted as failing -- and stay carried while the discards go on counting, with
nothing given up again.

The fill streams come from a thread on this host, one prebuilt frame per group
each period over a raw socket on the DUT-facing port: often while the learner
learns them, and once a second after, which keeps every discard counting at
every refresh. Only the case's own groups and link-local control reach the
bridge meanwhile; the segment's own multicast would discard, and take ids, of
its own.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import AsyncExitStack, asynccontextmanager
import ipaddress
import socket
import threading
import time

import pytest

from _mcast_windows import (COUNT, PORT, bridge_settings, delivered, frames, host, in_hardware, learn, mcast_rows, members, moved, quiet, stream, streamed)
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF
from _flowtable_rig import (command)
from _flowtable_service_multicast_leave import (FILTER_TIMERS, FILTER_VERSION)

# At least the ids a family has.
FILL = 512
FILL_PREFIX = {4: "239.77.0.0/16", 6: "ff1e::77:0/112"}
# Not counted by the windows' CPU counter, which counts PORT and the one after.
FILL_PORT = PORT + 20
WATCHED = {4: "239.78.0.1", 6: "ff1e::78:1"}
# Seconds between one frame of every fill stream and the next: often enough
# while the learner learns them, and a frame a second after -- five at every
# refresh, which never finds a discard idle.
LEARNING, HOLDING = 0.2, 1.0
# Past two refreshes, which is when an eviction that thrashed would show.
HOLD = 11
BATCH = "/tmp/ask_mcast_fill_memberships"


def fill_groups(family: int) -> list[str]:
    """Distinct groups with distinct mapped MACs."""
    if family == 4:
        return [f"239.77.{i // 250}.{i % 250 + 1}" for i in range(FILL)]
    return [f"ff1e::77:{i + 1:x}" for i in range(FILL)]


class Fill:
    """One frame of every fill stream, sent from this host every `period`."""

    def __init__(self, family: int, groups: list[str], iface: str):
        self.iface = iface
        self.frames = [bytes(frames({**stream(family, g, hops=64, port=FILL_PORT), "count": 1})[0])
                       for g in groups]
        self.period = LEARNING
        self.sent = 0
        self.error: BaseException | None = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name="mcast-fill", daemon=True)

    def run(self) -> None:
        try:
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
            sock.bind((self.iface, 0))
            try:
                while not self.stop.is_set():
                    began = time.monotonic()
                    for frame in self.frames:
                        for _ in range(1000):
                            try:
                                sock.send(frame)
                                break
                            except OSError:
                                # A full queue clears; a port gone down
                                # does not.
                                time.sleep(0.001)
                        else:
                            raise OSError(f"{self.iface} takes no more frames")
                        self.sent += 1
                    self.stop.wait(max(0.0, self.period - (time.monotonic() - began)))
            finally:
                sock.close()
        except BaseException as e:  # reported by __exit__, on the test's own thread
            self.error = e

    def __enter__(self) -> Fill:
        self.thread.start()
        return self

    def __exit__(self, kind, value, traceback) -> None:
        self.stop.set()
        self.thread.join(timeout=10)
        assert not self.thread.is_alive(), "the fill sender did not stop"
        if self.error is not None and kind is None:
            raise self.error


async def fill_memberships(r, bridge: str, groups: list[str], *, add: bool) -> None:
    """A static membership on the LAN port for every fill group, or none: one
    `bridge -batch` rather than a command per group. Taking them away goes on
    past one that is already gone."""
    verb = "add" if add else "del"
    lines = "".join(f"mdb {verb} dev {bridge} port {TARGET_LAN_IF} grp {g}"
                    f"{' permanent' if add else ''}\n" for g in groups)
    result = await r.target.fs_write(r.session, BATCH, lines, timeout_ms=5000)
    assert result.get("errno", 0) == 0, result
    await command(r.target, r.session, "bridge", *([] if add else ["-force"]), "-batch", BATCH,
                  check=add, timeout_ms=60000)


@asynccontextmanager
async def filled(r, bridge: str, groups: list[str]):
    await fill_memberships(r, bridge, groups, add=True)
    try:
        yield
    finally:
        await fill_memberships(r, bridge, groups, add=False)


@pytest.mark.parametrize("family", [4, 6])
async def test_gives_its_id_to_a_listener(multicast_rig,
                                                                             mcast_bridge, family):
    r = multicast_rig
    groups, watched = fill_groups(family), WATCHED[family]
    fill_net = ipaddress.ip_network(FILL_PREFIX[family])
    ids = f"mcast_group_ids{family}"
    port = f"{TARGET_LAN_IF}/0"

    def fill_rows(state):
        return [row for row in state["mcast"] if ipaddress.ip_address(row["group"]) in fill_net]

    def brief(state):
        """The counters, and the fill rows by state: the rows themselves are
        too many to read in a failure message."""
        return {**{k: v for k, v in state.items() if k.startswith(("mcast_", "mroute_"))},
                "quarantine": state["quarantine"], "fatal": state["fatal"],
                "fill": dict(Counter(row["state"] for row in fill_rows(state))),
                "watched": mcast_rows(state, watched)}

    async def settle(predicate, what, timeout):
        deadline = time.monotonic() + timeout
        while True:
            state = await r.proc()
            if predicate(state):
                return state
            if time.monotonic() > deadline:
                pytest.fail(f"v{family} {what}: not reached in {timeout}s: {brief(state)}")
            await asyncio.sleep(0.5)

    def carried(state):
        rows = mcast_rows(state, watched)
        return (len(rows) == 1 and rows[0]["state"] == "installed" and rows[0]["in"] == TARGET_WAN_IF
                and members(rows[0], "ports") == {port})

    async with AsyncExitStack() as kept_quiet:
        await kept_quiet.enter_async_context(bridge_settings(r, mcast_bridge, **FILTER_TIMERS))
        await kept_quiet.enter_async_context(quiet(r, [FILL_PREFIX[family], watched]))
        # Whatever the segment's own multicast left in hardware ages out
        # once its streams are kept off the ports: every id is the case's.
        empty = await settle(lambda s: s[ids] == 0, "no group id held before the fill", 30)
        slots = empty["mcast_group_id_slots"]
        assert 0 < slots <= FILL and slots % 2 == 0, brief(empty)
        # One port's senders may hold half the family's places
        # (FT_MC_MAX_PORT_FLOWS), and every fill stream arrives on one port.
        half = slots // 2
        waves = [groups[:half], groups[half:slots]]

        def wave_rows(state, wave):
            names = {ipaddress.ip_address(g) for g in wave}
            return [row for row in fill_rows(state) if ipaddress.ip_address(row["group"]) in names]

        async with AsyncExitStack() as stack:
            evicted = None
            fills = []
            for n, wave in enumerate(waves):
                # Every stream of the wave wanted and carried: the first wave
                # fills its port's share, and the second has to find room
                # beside the first's discards, which hold ids and no place.
                await stack.enter_async_context(filled(r, mcast_bridge, wave))
                fill = stack.enter_context(Fill(family, wave, r.wire))
                fills.append(fill)
                carried_wave = await settle(
                    lambda s: s["mcast_installed"] == half * (n + 1) and s[ids] == half * (n + 1) and
                    len(wave_rows(s, wave)) == half and
                    all(row["state"] == "installed" for row in wave_rows(s, wave)),
                    f"fill wave {n + 1} carried beside {n * half} discards", 90)
                assert carried_wave["mcast_discarding"] == n * half, brief(carried_wave)
                if evicted is None:
                    evicted = carried_wave["mcast_discards_evicted"]
                assert carried_wave["mcast_discards_evicted"] == evicted, brief(carried_wave)

                # Nobody wants them any more, and upstream goes on sending:
                # every entry drops its stream instead, and keeps its id.
                fill.period = HOLDING
                await fill_memberships(r, mcast_bridge, wave, add=False)
                discarding = await settle(
                    lambda s: s["mcast_discarding"] == half * (n + 1) and s[ids] == half * (n + 1) and
                    len(fill_rows(s)) == half * (n + 1) and
                    all(row["state"] == "discarding" for row in fill_rows(s)),
                    f"fill wave {n + 1} held by discards", 60)
            assert discarding["mcast_installed"] == slots, brief(discarding)
            assert discarding["mcast_discards_evicted"] == evicted, brief(discarding)

            # A viewer joins one more channel. Its add finds no id, takes the
            # one a discard holds, and is carried in the same pass.
            await stack.enter_async_context(host(r.lan, family=family, group=watched, iface=LAN_NIC,
                                                 mode="asm", version=FILTER_VERSION[family]))
            configs = [stream(family, watched, hops=64)]
            await learn(r, configs, carried, f"v{family}: {watched} carried")
            # The discard that gave way was named by nothing, and goes with
            # the next pass.
            joined = await settle(lambda s: carried(s) and len(fill_rows(s)) == slots - 1,
                                  "the evicted discard retired", 15)
            assert joined["mcast_discards_evicted"] == evicted + 1, brief(joined)
            assert joined["mcast_discarding"] == slots - 1, brief(joined)
            assert joined["mcast_installed"] == slots and joined[ids] == slots, brief(joined)
            assert joined["mcast_install_errors"] == discarding["mcast_install_errors"], brief(joined)
            assert all(row["state"] == "discarding" for row in fill_rows(joined)), brief(joined)

            window = await r.window(configs, [(r.lan, {LAN_NIC: None})], ingress=TARGET_WAN_IF,
                                    label=f"discard-capacity-v{family}")
            assert delivered(window, streamed(window, watched), LAN_NIC)
            assert moved(window, lambda s: (mcast_rows(s, watched) or [None])[0]) == COUNT, \
                brief(window["after"])
            in_hardware(window)

            # Two refreshes on, with every discard still counting: nothing
            # more given up, and nothing failing to go back in.
            await asyncio.sleep(HOLD)
            held = await r.proc()
            assert carried(held), brief(held)
            for key in ("mcast_discards_evicted", "mcast_discarding", "mcast_installed",
                        "mcast_install_errors", ids):
                assert held[key] == joined[key], (key, brief(joined), brief(held))
            r.record(f"mcast-discard-capacity-v{family}", {
                "slots": slots, "sent": sum(f.sent for f in fills), "carried": brief(carried_wave),
                "discarding": brief(discarding), "joined": brief(joined), "held": brief(held)})

        # The fill stopped and the viewer left: every discard ages out at its
        # first refresh that counts nothing, and every id comes back -- read
        # while the segment's own multicast is still kept off the ports.
        final = await settle(lambda s: s[ids] == 0 and s["mcast_installed"] == 0 and
                             not fill_rows(s) and not mcast_rows(s, watched),
                             "every id given back", 60)
        assert final["quarantine"] == 0, brief(final)
