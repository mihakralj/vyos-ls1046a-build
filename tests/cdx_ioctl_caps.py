"""G1 + G2: /dev/cdx_ctrl capability gate + single-open race fix.

G1 (ISSUES.md): cdx_ctrl_ioctl lacked a CAP_NET_ADMIN check; any open
with mode 0660 could reconfigure the DPAA datapath. Fix added
`if (!capable(CAP_NET_ADMIN)) return -EPERM` at dispatch.

G2: cdx_dev.c open path used `atomic_dec_and_test` then `atomic_inc` on
failure — racy check-then-act. Fix switched to `atomic_cmpxchg(1→0)`.
The behaviour we care about: exactly one opener gets the fd, the rest
get EBUSY.
"""

from __future__ import annotations

import errno


from _ioctl import (
    CDX_CTRL_DPA_SET_PARAMS,
    SIZEOF_CDX_CTRL_SET_DPA_PARAMS,
)


DEVICE       = "/dev/cdx_ctrl"
NON_ROOT_UID = 1000


async def test_g1_unprivileged_cannot_reconfigure_dpa(
    aiohttp_session, target_agent, splat_window,
):
    """Unprivileged user cannot drive /dev/cdx_ctrl at all.

    Two layers gate this: the device node is mode 0600 (root-only open)
    AND the ioctl dispatcher calls `capable(CAP_NET_ADMIN)` before
    handling (the G1 fix). A non-root open hits EACCES from the FS
    layer before reaching the in-kernel capable check; either gate is
    acceptable, what matters is the user can't get through.
    """
    data = b"\x00" * SIZEOF_CDX_CTRL_SET_DPA_PARAMS
    r = await target_agent.ioctl_send(
        aiohttp_session,
        device=DEVICE,
        cmd=CDX_CTRL_DPA_SET_PARAMS,
        data=data,
        uid=NON_ROOT_UID,
    )
    # EACCES = device-node mode says "nope", EPERM = capable() says "nope".
    assert r.get("errno") in (errno.EACCES, errno.EPERM), (
        f"unprivileged reconfigure should be rejected (EACCES/EPERM), got {r}"
    )


async def test_g1_root_gets_past_capability_gate(
    aiohttp_session, target_agent, splat_window,
):
    """Root passes the capability gate and receives ENOTTY for the retired command."""
    data = b"\x00" * SIZEOF_CDX_CTRL_SET_DPA_PARAMS
    r = await target_agent.ioctl_send(
        aiohttp_session,
        device=DEVICE,
        cmd=CDX_CTRL_DPA_SET_PARAMS,
        data=data,
        # uid=None → agent stays as root
    )
    assert r.get("errno") == errno.ENOTTY, (
        f"root hit the capability gate (regression!): {r}"
    )


async def test_g2_single_open_gate(
    aiohttp_session, target_agent, splat_window,
):
    """Two concurrent opens: exactly one succeeds, the other → EBUSY.

    The agent's /ioctl/send opens+ioctls+closes atomically, so we can't
    easily hold one open from here. Instead: do two fast-back-to-back
    ioctls; they don't race (the first closes before the second opens)
    but after enough iterations we'd catch a gate regression. Simpler
    assertion: a single successful open succeeds, and if we WERE able
    to open twice without closing the first, we'd see EBUSY.

    For a real concurrent test we'd need a holding-endpoint. For now
    assert the gate still exists by having the agent's /ioctl/send path
    succeed on its own full open→ioctl→close cycle — a regression that
    broke the gate would leave the device open across calls and the
    second call would fail. No direct race coverage yet.
    """
    # Two sequential ioctls — both should succeed the open→close cycle.
    # If the gate were broken in a way that leaks open refs, the second
    # call here would return EBUSY.
    data = b"\x00" * SIZEOF_CDX_CTRL_SET_DPA_PARAMS
    for i in range(3):
        r = await target_agent.ioctl_send(
            aiohttp_session,
            device=DEVICE,
            cmd=CDX_CTRL_DPA_SET_PARAMS,
            data=data,
        )
        # Any result other than EBUSY means open+close pairing is intact.
        # errno=0 + rc=0 would mean the ioctl fully succeeded (won't here
        # because the struct is all zeros and downstream code rejects);
        # some other errno is fine, EBUSY is not.
        assert r.get("errno") != errno.EBUSY, (
            f"iteration {i}: ioctl got EBUSY — open refcount isn't being "
            f"released between ioctls; {r}"
        )
