"""G1 extension: a process inside a non-init user namespace cannot
reconfigure cdx.

What this actually exercises: the device-node mode (0600 on /dev/cdx_ctrl)
combined with the unmapped userns. With no uid_map, every uid in the new
userns translates to /proc/sys/kernel/overflowuid (typically 65534), so
`os.open()` trips EACCES at the FS-permission check BEFORE the
dispatcher's `capable(CAP_NET_ADMIN)` gate runs. Both gates enforce the
same security property — non-init userns can't reconfigure cdx — so the
test validates that property even though it doesn't directly hit the
dispatcher gate. (To exercise the dispatcher gate specifically, the
agent would need an "open as root, then unshare, then ioctl on the
existing fd" mode; not in scope here.)

Skipped when the kernel doesn't support unprivileged userns: agent's
`os.unshare(CLONE_NEWUSER)` raises OSError, which `_run_isolated`
records under `error` as a `[Errno N] ...` string. The skip predicate
matches that shape; the cdx-rejection paths produce different error
strings (`open: ...`, plain strerror), so the predicate is unambiguous.
"""

from __future__ import annotations

import errno

import pytest

from _ioctl import CDX_CTRL_DPA_SET_PARAMS, SIZEOF_CDX_CTRL_SET_DPA_PARAMS


DEVICE = "/dev/cdx_ctrl"


def _looks_like_unshare_failure(result: dict) -> bool:
    """Heuristic: `_run_isolated`'s OSError handler stores `str(e)` for
    raised OSErrors, which has the `[Errno N] message` shape. cdx FS
    open / ioctl rejections come back with different error shapes
    (`open: <strerror>` / bare strerror) and a populated `rc` field."""
    err = result.get("error", "")
    return err.startswith("[Errno") and result.get("rc") is None


async def test_g1_non_init_userns_cannot_reconfigure_dpa(
    aiohttp_session, target_agent, splat_window,
):
    data = b"\x00" * SIZEOF_CDX_CTRL_SET_DPA_PARAMS
    r = await target_agent.ioctl_send(
        aiohttp_session,
        device=DEVICE, cmd=CDX_CTRL_DPA_SET_PARAMS, data=data,
        userns=True,
    )
    if _looks_like_unshare_failure(r):
        pytest.skip(
            f"kernel rejected CLONE_NEWUSER (CONFIG_USER_NS=n or "
            f"unprivileged userns disabled): {r.get('error')!r}"
        )
    # EACCES from the FS gate (open of /dev/cdx_ctrl, mode 0600, fails
    # for the userns-mapped overflowuid) or EPERM from the dispatcher
    # gate would both be valid rejections of the security property.
    assert r.get("errno") in (errno.EACCES, errno.EPERM), (
        f"non-init userns must be rejected (EACCES from FS-mode gate "
        f"or EPERM from dispatcher gate); got {r}"
    )
