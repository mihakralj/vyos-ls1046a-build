"""Shared device API over session-owned DUT and dell2 SSH stdio channels."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
import aiohttp


# Canonical filter for "leaks originating from ASK-maintained code".
# Pass as `filter_substrs=ASK_KMEMLEAK_FILTER` when calling Agent.kmemleak().
#
# Two signal classes combined:
#
#   1. Module-name annotations. kmemleak writes backtrace frames via %pS,
#      which for symbols in loadable modules appends "[modname]". Our two
#      out-of-tree kmods show up as [cdx] and [ask_flowtable] in any frame
#      that sits in that module — regardless of what the function is
#      called. This is the strongest signal: a frame either is in a given
#      .ko or it isn't.
#
#   2. Function-name prefixes. Backup signal for cases where module
#      annotation might be stripped (e.g. certain aggressive link-time
#      optimisations or symbol-table truncation). cdx consistently
#      prefixes its exported + file-scope functions with cdx_. Redundant
#      with (1) on a healthy kallsyms setup; cheap insurance when it isn't.
#      The adapter's ft_ prefix is too generic to use the same way.
#
# Built-in kernel code (including NXP's sdk_dpaa / sdk_fman / fsl_qbman,
# which link into vmlinux in our config) shows no bracket annotation, so
# (1) automatically excludes those. That's why DPAA's ~16k baseline
# false-positives don't trip this filter even though some of their
# symbol names happen to contain "dpa_" — those frames lack [cdx] and
# their names start with "dpa_" / "dpaa_" / "qman_" / "bman_" / "fm_",
# none of which appear in the needles below.
ASK_KMEMLEAK_FILTER = [
    "[cdx]", "[ask_flowtable]",
    "cdx_",
]

# Seconds a kmemleak scan or clear may take on the DUT; see Agent.kmemleak().
KMEMLEAK_TIMEOUT = 600
_WAN_SESSION = None


def set_wan_session(session):
    global _WAN_SESSION
    if session is not None and _WAN_SESSION is not None:
        raise RuntimeError("dell2 agent already has an owner")
    _WAN_SESSION = session


@dataclass
class Agent:
    name: str           # human label, e.g. "target", "lan", "wan"
    base_url: str = ""  # WAN HTTP endpoint; empty selects the shared DUT UART.

    async def request(self, session, operation, body=None, *, timeout=30, check=True):
        body = body or {}
        if os.environ.get("ASK_TRANSPORT") == "ssh" and self.name == "wan":
            if _WAN_SESSION is None:
                raise RuntimeError("dell2 stdio agent session is not open")
            return await asyncio.to_thread(_WAN_SESSION.request, operation, body, timeout, check)
        if not self.base_url:
            from .uart import target_session
            return await asyncio.to_thread(target_session().request, operation, body, timeout, check)
        path = operation
        if operation == "capture-stop":
            path += "/" + body["capture_id"]
        method = "GET" if operation in {"health", "counters", "kmemleak-scan"} else "POST"
        kwargs = {"params" if method == "GET" else "json": body}
        async with session.request(method, f"{self.base_url}/{path}",
                                   timeout=aiohttp.ClientTimeout(total=timeout), **kwargs) as response:
            if check:
                response.raise_for_status()
            return await response.json()

    async def observe(self, session, operation, **body):
        return await self.request(session, "observe/" + operation, body,
                                  timeout=float(body.get("timeout", 30)) + 10)

    async def artifact(self, session, ident):
        import base64
        data = bytearray()
        offset = 0
        while True:
            part = await self.request(session, "artifact/read", {"id": ident, "offset": offset})
            data.extend(base64.b64decode(part["data"]))
            offset = len(data)
            if offset == part["size"]:
                import hashlib
                assert hashlib.sha256(data).hexdigest() == ident, "artifact digest mismatch"
                return bytes(data)
            assert offset < part["size"], part


    async def health(self, session: aiohttp.ClientSession) -> dict:
        return await self.request(session, "health", {}, timeout=5)

    async def capture_start(self, session: aiohttp.ClientSession, ifaces: list[str] | None = None,
                            *, counters: bool = False) -> str:
        """Open a capture window. `counters` additionally snapshots the
        firmware counters, which costs about a second at each end of the
        window because it reads every /proc/fqid_stats entry and each read is
        a live frame-queue query. Ask for it only where the deltas are read;
        the splat window itself does not need them."""
        body = {"ifaces": ifaces or [], "counters": counters}
        result = await self.request(session, "capture-start", body, timeout=30)
        assert result.get("complete") is True, result
        return result["capture_id"]

    async def capture_stop(self, session: aiohttp.ClientSession, cap_id: str) -> dict:
        return await self.request(session, "capture-stop", {"capture_id": cap_id}, timeout=30)

    async def boot_log(self, session):
        return await self.request(session, "dmesg-delta", {}, timeout=30)

    async def kmemleak(
        self,
        session: aiohttp.ClientSession,
        filter_substrs: list[str] | None = None,
    ) -> dict:
        # If no filter: return everything. For "ASK-code only" callers,
        # pass ASK_KMEMLEAK_FILTER (defined at module top).
        # The agent writes "scan" to /sys/kernel/debug/kmemleak, which walks
        # the heap synchronously, then reads and filters the whole report.
        # The first scan after heavy traffic on a boot reports the DPAA
        # buffer pools' ~12k false positives, and on a KASAN kernel reading
        # that report has outrun 120 s. A scan that fails or times out is an
        # error, never an empty report: callers assert on leak_count.
        result = await self.request(session, "kmemleak-scan", {"filter": ",".join(filter_substrs or [])},
                                    timeout=KMEMLEAK_TIMEOUT)
        assert "leak_count" in result, f"kmemleak scan returned no report: {result}"
        return result

    async def kmemleak_clear(self, session: aiohttp.ClientSession) -> dict:
        # Clear internally does `scan` + `clear` on the agent side so
        # unclassified boot-time baseline objects don't slip past the
        # cursor. The `scan` write is synchronous in the kernel, so it
        # shares the scan's budget.
        return await self.request(session, "kmemleak-clear", {}, timeout=KMEMLEAK_TIMEOUT)

    async def netlink_send(
        self,
        session: aiohttp.ClientSession,
        protocol: int,
        msg: bytes,
        *,
        nlmsg_type: int = 0,
        nlmsg_flags: int = 0,
        nlmsg_len_override: int | None = None,
        timeout_ms: int = 500,
        failslab_times: int | None = None,
        uid: int | None = None,
        userns: bool = False,
    ) -> dict:
        """Send a raw netlink message and return the raw reply. When
        `failslab_times=N` is set, the agent forks, arms per-task fail-nth so
        that exactly the Nth kmalloc made by that child in the send path
        returns NULL, and sends; `ignore-gfp-wait` is off so GFP_KERNEL
        allocations are eligible. It works for any protocol, which is what
        lets an XFRM NEWSA drive the IPsec SA allocator's unwind. Pair with a
        kmemleak cursor around the sweep to catch leaks on unwind."""
        body: dict = {
            "protocol":    protocol,
            "body_hex":    msg.hex(),
            "nlmsg_type":  nlmsg_type,
            "nlmsg_flags": nlmsg_flags,
            "timeout_ms":  timeout_ms,
        }
        if nlmsg_len_override is not None:
            body["nlmsg_len_override"] = nlmsg_len_override
        if failslab_times is not None:
            body["failslab_times"] = int(failslab_times)
        if uid is not None:
            body["uid"] = int(uid)
        if userns:
            body["userns"] = True
        return await self.request(session, "netlink/send", body, timeout=timeout_ms / 1000 + 5)

    async def ioctl_send(
        self,
        session: aiohttp.ClientSession,
        device: str,
        cmd: int,
        data: bytes = b"",
        *,
        uid: int | None = None,
        userns: bool = False,
        drop_cap_net_admin: bool = False,
        timeout_ms: int = 1000,
    ) -> dict:
        """Issue an ioctl on the agent. `uid` drops to an unprivileged
        UID before open. `userns=True` wraps the call in an unmapped
        new user namespace (unmapped-userns capability-gate case).
        `drop_cap_net_admin=True` runs capset() between open and ioctl
        so the dispatcher sees a CAP_NET_ADMIN-less effective set on a
        privileged-opened fd (mid-flight cap-drop case)."""
        body: dict = {
            "device":     device,
            "cmd":        int(cmd),
            "data_hex":   data.hex(),
            "timeout_ms": timeout_ms,
        }
        if uid is not None:
            body["uid"] = int(uid)
        if userns:
            body["userns"] = True
        if drop_cap_net_admin:
            body["drop_cap_net_admin"] = True
        return await self.request(session, "ioctl/send", body, timeout=timeout_ms / 1000 + 5)

    async def exec_cmd(
        self,
        session: aiohttp.ClientSession,
        argv: list[str],
        *,
        timeout_ms: int = 5000,
        quiet: bool = False,
    ) -> dict:
        return await self.request(session, "exec", {"argv": argv, "timeout_ms": timeout_ms, "quiet": quiet},
                                  timeout=timeout_ms / 1000 + 5)

    async def fs_read(
        self,
        session: aiohttp.ClientSession,
        path: str,
        *,
        max_bytes: int = 1 << 20,
    ) -> dict:
        """Read a file on the agent and return {content_hex, size, errno}.

        Used for /proc, /sys, and other binary-safe surfaces where exec_cmd
        with `cat` is unavailable (cat is not in the exec allowlist). The
        kernel-side H2 key-zeroing probe at /proc/cdx/last_freed_key is the
        primary consumer.
        """
        body = {"path": path, "max_bytes": int(max_bytes)}
        return await self.request(session, "fs/read", body, timeout=30)

    async def fs_write(
        self,
        session: aiohttp.ClientSession,
        path: str,
        content: str | bytes,
        *,
        uid: int | None = None,
        timeout_ms: int = 1000,
    ) -> dict:
        body: dict = {"path": path, "timeout_ms": timeout_ms}
        # Bytes travel hex-encoded, as fs_read returns them: as text they would
        # be re-encoded on the way and arrive altered.
        if isinstance(content, str):
            body["content"] = content
        else:
            body["content_hex"] = content.hex()
        if uid is not None:
            body["uid"] = int(uid)
        return await self.request(session, "fs/write", body, timeout=timeout_ms / 1000 + 5)

# DUT management has no network endpoint. WAN agents retain their explicit URL.
TARGET = Agent("target")
