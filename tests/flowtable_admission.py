"""Retry transient partial admission without replacing the table or sockets."""
from __future__ import annotations

import pytest

from _flowtable_connections import (SPORT, peer)
from _flowtable_mtu import (table_identity)
from _flowtable_rig import (read)
from _flowtable_selective_neighbour import (hardware, warm)


@pytest.mark.parametrize("protocol", ["udp", "tcp"])
async def test_flowtable_transient_admission(connections, protocol):
    r = connections
    flows = [{"id": 0, "proto": protocol, "sport": SPORT + (protocol == "tcp"), "lan": r.lan_ip}]
    before, identity = await r.state(), await table_identity(r)
    assert before["entries"] == 0, before
    knob = "/sys/module/ask_flowtable/parameters/flowtable_fail_stage"
    # This fault is consumed only by a matching second directional request
    # after its peer already owns a hardware entry. It uses the same path as
    # failure to acquire RTNL, without actually blocking a kernel lock. With
    # no IPsec policy configured, that path declines the offer and retires
    # nothing: the first direction stays installed, and the software path
    # forwarding the second offers the flow again about a second later, which
    # admits it. So one busy, and exactly the two installs the connection
    # needs -- no retirement and no reinstall of the direction that held.
    assert (await r.target.fs_write(r.session, knob, "4"))["errno"] == 0
    try:
        async with peer(r, flows) as p:
            admitted = await warm(r, p, [0], protocol + "-busy-readmitted", flows)
            assert (await read(r.target, r.session, knob)).strip() == "0"
            assert admitted["busy"] >= before["busy"] + 1, (before, admitted)
            assert admitted["admission_invalidations"] == before["admission_invalidations"], (before, admitted)
            assert admitted["deletes"] == before["deletes"], (before, admitted)
            assert admitted["installs"] == before["installs"] + 2, (before, admitted)
            assert admitted["rearms"] == before["rearms"] and not admitted["invalidation_done"]
            assert admitted["handle_refs"] == admitted["neighbour_refs"] == 2
            assert await table_identity(r) == identity
            after = await hardware(r, p, protocol + "-busy-hardware", flows)
            assert after["admission_invalidations"] == admitted["admission_invalidations"]
            r.record(protocol + "-busy-complete", {"before": before, "admitted": admitted,
                                                   "after": after, "table": identity})
    finally:
        assert (await r.target.fs_write(r.session, knob, "0"))["errno"] == 0
