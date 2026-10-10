"""Retired classifier ioctls reject calls without reading their payloads."""

from __future__ import annotations

import errno
import struct

import pytest

from _ioctl import CDX_CTRL_DPA_SET_PARAMS, CDX_CTRL_DPA_CONNADD_LEGACY, CDX_CTRL_DPA_GET_MURAM_DATA, CDX_CTRL_UNKNOWN_NR, SIZEOF_CDX_CTRL_GET_MURAM_DATA


DEVICE = "/dev/cdx_ctrl"

# struct cdx_ctrl_set_dpa_params: pointer at 0, count at 8, tail pad at 12.
def _set_params_struct(num_fmans: int, fman_ptr: int = 0) -> bytes:
    return struct.pack("<QI4x", fman_ptr, num_fmans)


CDX_MAX_FMANS = 16


@pytest.mark.parametrize("num_fmans", [
    0,                 # would leave fman_info unset
    1,                 # well-formed count, still a reconfiguration
    CDX_MAX_FMANS + 1, # just past the cap
    10_000,            # 625× the cap
    0xFFFFFFFF,        # wraps most signed comparisons
])
async def test_set_params_ioctl_removed(
    aiohttp_session, target_agent, splat_window, num_fmans,
):
    """The in-kernel builder removed userspace classifier installation."""
    data = _set_params_struct(num_fmans=num_fmans)
    r = await target_agent.ioctl_send(
        aiohttp_session,
        device=DEVICE, cmd=CDX_CTRL_DPA_SET_PARAMS, data=data,
    )
    assert r.get("errno") == errno.ENOTTY, (
        f"num_fmans={num_fmans}: expected the retired ioctl to refuse, got {r}"
    )


# Linux convention is ENOTTY for "this fd doesn't recognize this ioctl";
# cdx_dev.c's dispatcher now returns ENOTTY on the default arm. A
# re-added handler would return 0 (success), which is the regression.
_NO_SUCH_IOCTL = {errno.ENOTTY}


async def test_c9b_connadd_ioctl_removed(
    aiohttp_session, target_agent, splat_window,
):
    """CDX_CTRL_DPA_CONNADD (nr=3) was deleted by C9b. Dispatcher must
    not dispatch it anywhere."""
    r = await target_agent.ioctl_send(
        aiohttp_session,
        device=DEVICE, cmd=CDX_CTRL_DPA_CONNADD_LEGACY, data=b"",
    )
    assert r.get("errno") in _NO_SUCH_IOCTL, (
        f"removed-ioctl nr=3 should be rejected (ENOTTY), got {r}"
    )


async def test_unknown_ioctl_nr_rejected(
    aiohttp_session, target_agent, splat_window,
):
    """Any ioctl nr the dispatcher has no entry for is rejected, not
    routed through a handler."""
    r = await target_agent.ioctl_send(
        aiohttp_session,
        device=DEVICE, cmd=CDX_CTRL_UNKNOWN_NR, data=b"",
    )
    assert r.get("errno") in _NO_SUCH_IOCTL, (
        f"unregistered ioctl (nr=99) should be rejected, got {r}"
    )


async def test_get_muram_data_nr_rejected_when_debug_disabled(
    aiohttp_session, target_agent, splat_window,
):
    """CDX_CTRL_DPA_GET_MURAM_DATA (nr=4) is gated on DPAA_DEBUG_ENABLE.
    Production images don't define that, so the dispatcher has no entry
    and the ioctl must come back ENOTTY. Tripwire: if a build flips
    DPAA_DEBUG_ENABLE on, this test starts seeing 0/EFAULT/EINVAL — at
    which point the test should be extended to bound the size field of
    struct muram_data, where there's an unchecked memcpy in the handler
    (cdx_dev.c:104)."""
    data = b"\x00" * SIZEOF_CDX_CTRL_GET_MURAM_DATA
    r = await target_agent.ioctl_send(
        aiohttp_session,
        device=DEVICE, cmd=CDX_CTRL_DPA_GET_MURAM_DATA, data=data,
    )
    assert r.get("errno") in _NO_SUCH_IOCTL, (
        f"GET_MURAM_DATA on a non-debug build should be ENOTTY; got {r}. "
        f"If DPAA_DEBUG_ENABLE was just turned on, extend this test "
        f"with a bounds sweep on struct muram_data.size."
    )
