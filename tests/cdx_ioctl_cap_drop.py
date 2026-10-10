"""G1 extension: caller drops CAP_NET_ADMIN between open() and ioctl().

The cdx dispatcher checks `capable(CAP_NET_ADMIN)` per-ioctl, not per-fd-
open. So a process that opened /dev/cdx_ctrl with full caps but then
dropped CAP_NET_ADMIN (e.g. setcap-style binaries that shed caps before
running untrusted code on a privileged-opened fd) must still be rejected.

Mechanism (agent-side): _ioctl_work opens the device as root, then
capset()s CAP_NET_ADMIN out of effective+permitted+inheritable, then
issues the ioctl. The ioctl path's capable() check sees the stripped
set and returns -EPERM.
"""

from __future__ import annotations

import errno

import pytest

from _ioctl import CDX_CTRL_DPA_SET_PARAMS, CDX_CTRL_DPA_INIT_CHECK, SIZEOF_CDX_CTRL_SET_DPA_PARAMS


DEVICE = "/dev/cdx_ctrl"


@pytest.mark.parametrize("command", [CDX_CTRL_DPA_SET_PARAMS, CDX_CTRL_DPA_INIT_CHECK])
async def test_g1_capability_drop_between_open_and_ioctl(
    aiohttp_session, target_agent, splat_window, command,
):
    data = b"\x00" * SIZEOF_CDX_CTRL_SET_DPA_PARAMS
    r = await target_agent.ioctl_send(
        aiohttp_session,
        device=DEVICE, cmd=command, data=data,
        drop_cap_net_admin=True,
    )
    assert r.get("errno") == errno.EPERM, (
        f"ioctl after capset(drop CAP_NET_ADMIN) must return EPERM; got {r}"
    )
