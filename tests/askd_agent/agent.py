"""Device operations shared by the UART agent and WAN HTTP service."""

from __future__ import annotations

import asyncio
import errno
import os
import platform
import re
import secrets
import select
import signal
import shutil
import socket
import struct
import subprocess
import time
from pathlib import Path


from . import __version__, counters, dmesg

class AgentError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(str(detail))


def _error(status, detail):
    raise AgentError(status, detail)


KMEMLEAK_PATH = Path("/sys/kernel/debug/kmemleak")

# struct nlmsghdr is 16 bytes on all 64-bit Linux: u32 len, u16 type,
# u16 flags, u32 seq, u32 pid.
_NLMSGHDR_SIZE = 16


def _new_capture_id() -> str:
    return secrets.token_hex(8)


async def health(body: dict, state: dict) -> dict:
    return {
        "ok": True,
        "version": __version__,
        "host": platform.node(),
        "uptime_s": _read_uptime(),
        "kernel": platform.release(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "capture_protocol": 2,
        "binaries": [name for name in ("ip", "nft", "conntrack", "iperf3", "pppd", "smcrouted")
                     if shutil.which(name)],
    }


def _read_uptime() -> float:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        return 0.0


async def counters_get(body: dict, state: dict) -> dict:
    ifaces = body.get("ifaces", []) or ["eth3", "eth4"]
    return await asyncio.to_thread(counters.snapshot, ifaces)


# A window whose stop never arrives would otherwise hold its descriptor for
# the life of the agent. Tests open one per test, so a small bound is plenty
# and keeps a crashed run from exhausting the descriptor table.
MAX_OPEN_CAPTURES = 64


async def capture_start(body: dict, state: dict) -> dict:
    ifaces = body.get("ifaces") or ["eth3", "eth4"]
    # Opt-in, because it is not free: the snapshot walks every file under
    # /proc/fqid_stats -- 779 of them on this image -- and each read is a live
    # QMan frame-queue query, about a second per snapshot and two snapshots per
    # window. Callers that only want the splat window should not pay it. Ask
    # for it with {"counters": true} when the deltas are actually read.
    want_counters = bool(body.get("counters"))
    captures = state["captures"]
    if len(captures) >= MAX_OPEN_CAPTURES:
        return _error(503, {"complete": False, "error": "too many open captures"})
    cap_id = _new_capture_id()
    try:
        fd = dmesg.open_at_tail()
    except OSError as error:
        return _error(503, {"complete": False, "error": str(error)})
    try:
        capture = {
            "counters": await asyncio.to_thread(counters.snapshot, ifaces) if want_counters else None,
            "ifaces": ifaces,
        }
        capture["window"] = dmesg.Window(fd)
        captures[cap_id] = capture
    except BaseException:
        dmesg.close(fd)
        raise
    return {"capture_id": cap_id, "complete": True}


async def capture_stop(body: dict, state: dict) -> dict:
    cap_id = body["capture_id"]
    cap = state["captures"].pop(cap_id, None)
    if cap is None:
        return _error(404, {"error": "unknown capture_id"})
    window = await cap["window"].finish()
    new_lines = window["lines"]
    splats = dmesg.has_splat(new_lines)
    before = cap["counters"]
    delta = (counters.diff_numeric(before, await asyncio.to_thread(counters.snapshot, cap["ifaces"]))
             if before is not None else None)
    return {
        "complete": window["complete"],
        "error": window["error"],
        "dmesg": new_lines,
        "splats": splats,
        "counters_delta": delta,
    }


async def dmesg_delta(body: dict, state: dict) -> dict:
    cursor = body.get("cursor")
    window = await asyncio.to_thread(dmesg.retained_boot if cursor is None else dmesg.read_since,
                                     *(() if cursor is None else (cursor,)))
    return {
        **window,
        "splats": dmesg.has_splat(window["lines"]),
    }


def _netlink_send_sync(
    protocol: int,
    body: bytes,
    nlmsg_len_override: int | None,
    nlmsg_type: int,
    nlmsg_flags: int,
    timeout_s: float,
) -> dict:
    """Send a raw netlink message, return the raw reply + parse hints.

    `body` is everything after the nlmsghdr — the message payload the
    kernel's input handler sees. By default the nlmsghdr's nlmsg_len
    matches the actual on-wire bytes (16 + len(body)); tests can lie
    via `nlmsg_len_override` to probe handlers' own length validation
    (skb->len vs nlmsg_len).
    """
    sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, protocol)
    try:
        sock.bind((0, 0))
        sock.settimeout(timeout_s)

        real_len   = _NLMSGHDR_SIZE + len(body)
        header_len = real_len if nlmsg_len_override is None else nlmsg_len_override
        # Header fields: u32 len, u16 type, u16 flags, u32 seq, u32 pid
        nlh = struct.pack(
            "=IHHII", header_len & 0xFFFFFFFF,
            nlmsg_type & 0xFFFF, nlmsg_flags & 0xFFFF,
            1, 0,
        )
        sock.send(nlh + body)

        try:
            reply = sock.recv(8192)
        except socket.timeout:
            reply = b""

        out: dict = {"sent_bytes": real_len, "reply_hex": reply.hex()}
        if len(reply) >= _NLMSGHDR_SIZE:
            reply_body = reply[_NLMSGHDR_SIZE:]
            out["body_hex"] = reply_body.hex()
        return out
    finally:
        sock.close()


_FAILSLAB_DIR = Path("/sys/kernel/debug/failslab")


def _arm_failslab(n: int) -> None:
    """Configure failslab to fault exactly the Nth kmalloc made by the
    current task.

    Mechanism: per-task `/proc/self/fail-nth` is the only correct primitive
    for surgical fault injection here. Unlike `times`/`probability` (global
    counters that drain on incidental allocations), fail_nth is decremented
    only by the current task's kmallocs and bypasses all other failslab
    gates (probability, task-filter, times, interval) — see
    lib/fault-inject.c:should_fail_ex.

    The wrapper `should_failslab` still filters on `ignore-gfp-wait` BEFORE
    reaching should_fail_ex, so GFP_KERNEL allocations would be exempt
    under the kernel default (Y). The knob is set to N once at agent
    startup (main()) and left there: with no task's fail-nth armed the
    setting alone faults nothing, and the old per-request write/restore
    cycle was a global flip-flop where one silently swallowed write
    failure made an entire sweep test nothing (the fault never reached
    any GFP_KERNEL allocator and every register just succeeded). Verify
    the knob here and fail LOUDLY instead of sweeping in the dark.
    """
    knob = (_FAILSLAB_DIR / "ignore-gfp-wait").read_text().strip()
    if knob != "N":
        raise RuntimeError(
            f"failslab ignore-gfp-wait is {knob!r}, expected 'N' "
            "(startup arming missing?) - GFP_KERNEL faults would be exempt"
        )
    Path("/proc/self/fail-nth").write_text(f"{n}\n")


def _disarm_failslab() -> None:
    """Clear the per-task counter before the child exits. The global
    `ignore-gfp-wait` knob deliberately stays at N (see _arm_failslab);
    fail-nth is per-task and self-clears when it fires, so nothing else
    on the system can see a fault from it."""
    try:
        Path("/proc/self/fail-nth").write_text("0\n")
    except OSError:
        pass


def _netlink_send_failslab(
    protocol: int,
    body: bytes,
    nlmsg_len_override: int | None,
    nlmsg_type: int,
    nlmsg_flags: int,
    timeout_s: float,
    failslab_times: int,
) -> dict:
    """Fork a child, open the netlink socket there, arm failslab scoped to
    the child only, send the message, then disarm. The fork isolates
    make-it-fail from the parent agent — otherwise arming would fault the
    agent's own kmallocs (aiohttp handlers, JSON serialization) and wedge
    the service.

    Arming *after* socket creation means the `times=N` counter is spent on
    kmallocs during the send/recv syscall path and whatever they call into
    (e.g. an XFRM NEWSA's xdo_dev_state_add() into the cdx SA allocator) —
    not on the bookkeeping overhead of opening the socket itself.
    """
    import pickle

    r_fd, w_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r_fd)
        result: dict = {}
        armed = False
        try:
            sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, protocol)
            sock.bind((0, 0))
            sock.settimeout(timeout_s)

            real_len   = _NLMSGHDR_SIZE + len(body)
            header_len = real_len if nlmsg_len_override is None else nlmsg_len_override
            nlh = struct.pack(
                "=IHHII", header_len & 0xFFFFFFFF,
                nlmsg_type & 0xFFFF, nlmsg_flags & 0xFFFF,
                1, 0,
            )
            msg = nlh + body

            # fail-nth counts EVERY should_fail() call this task makes, not
            # just failslab: with the debug build's full fault-injection
            # family enabled, the child's own page faults between arming
            # and the syscall consume counts via fail_page_alloc, and the
            # burn varies with the (COW-inherited) memory layout — which is
            # what made whole sweeps land short of the target allocator
            # (ISSUES.md A70). Pin everything now so the interpreter takes
            # no page faults after arming, and pre-resolve the bound
            # methods so the armed window is exactly send+recv.
            import ctypes
            MCL_CURRENT, MCL_FUTURE = 1, 2
            mlockall_rc = ctypes.CDLL(None, use_errno=True).mlockall(
                MCL_CURRENT | MCL_FUTURE)
            do_send = sock.send
            do_recv = sock.recv

            _arm_failslab(failslab_times)
            armed = True

            send_err = None
            try:
                do_send(msg)
            except OSError as e:
                send_err = f"send: errno={e.errno} {e.strerror}"

            reply = b""
            if send_err is None:
                try:
                    reply = do_recv(8192)
                except socket.timeout:
                    reply = b""
                except OSError as e:
                    send_err = f"recv: errno={e.errno} {e.strerror}"

            # Read the residue BEFORE disarming: fail-nth decrements once
            # per eligible allocation by this task, so residue == armed
            # value means the syscall path made zero eligible allocations
            # (the injection tested nothing), residue == 0 means the fault
            # fired, anything between counts the eligible allocations seen.
            # This is the discriminator for the "sweep never reached the
            # allocator" flake (ISSUES.md A70).
            try:
                fail_nth_residue = int(
                    Path("/proc/self/fail-nth").read_text().strip())
            except (OSError, ValueError):
                fail_nth_residue = -1

            # Disarm ASAP so the subsequent pickle/pipe write doesn't
            # also see faults.
            _disarm_failslab()
            armed = False

            result = {
                "sent_bytes":     real_len,
                "reply_hex":      reply.hex(),
                "failslab_times": failslab_times,
                "fail_nth_residue": fail_nth_residue,
                "mlockall_rc":    mlockall_rc,
            }
            if send_err:
                result["send_error"] = send_err
            if len(reply) >= _NLMSGHDR_SIZE:
                result["body_hex"] = reply[_NLMSGHDR_SIZE:].hex()
            sock.close()
        except OSError as e:
            result = {"error": f"setup failed: errno={e.errno} {e.strerror}"}
        except Exception as e:
            result = {"error": f"{type(e).__name__}: {e}"}
        finally:
            if armed:
                # Exception between arm and explicit disarm — best effort.
                _disarm_failslab()
        try:
            os.write(w_fd, pickle.dumps(result))
        finally:
            os.close(w_fd)
            os._exit(0)

    os.close(w_fd)
    buf = b""
    try:
        while True:
            chunk = os.read(r_fd, 65536)
            if not chunk:
                break
            buf += chunk
    finally:
        os.close(r_fd)
    os.waitpid(pid, 0)
    try:
        return pickle.loads(buf)
    except Exception as e:
        return {"error": f"child produced no result: {e}"}


_CLONE_NEWUSER = 0x10000000


def _enter_unmapped_userns() -> None:
    """Create a new user namespace with no uid/gid mappings.

    With no mapping, all uids in the namespace map to /proc/sys/kernel/
    overflowuid (typically 65534) and capable() against init_user_ns
    returns false — exactly what the unmapped-userns capability-gate
    test wants to assert on a CAP_NET_ADMIN-gated ioctl.
    """
    os.unshare(_CLONE_NEWUSER)


def _run_isolated(
    work, uid: int | None, timeout_s: float, *, userns: bool = False,
) -> dict:
    """Fork a subprocess, optionally drop to `uid` and/or enter an
    unmapped new userns, call `work()`, pipe back a result dict.
    """
    import pickle

    r_fd, w_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r_fd)
        result: dict = {}
        try:
            if userns:
                _enter_unmapped_userns()
            if uid is not None:
                # setresgid before setresuid (reverse is forbidden when
                # dropping root — gid needs the privilege to change).
                os.setresgid(uid, uid, uid)
                os.setresuid(uid, uid, uid)
            result = work()
        except OSError as e:
            result = {"error": str(e), "errno": e.errno}
        except Exception as e:
            result = {"error": f"{type(e).__name__}: {e}"}
        try:
            os.write(w_fd, pickle.dumps(result))
        finally:
            os.close(w_fd)
            os._exit(0)
    os.close(w_fd)
    buf = b""
    deadline = time.monotonic() + timeout_s if timeout_s and timeout_s > 0 else None
    timed_out = False
    try:
        while True:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([r_fd], [], [], remaining)[0]:
                    timed_out = True
                    break
            chunk = os.read(r_fd, 65536)
            if not chunk:
                break
            buf += chunk
    finally:
        os.close(r_fd)
    if timed_out:
        # Child wedged (e.g. stuck in a kernel call) — kill so we neither
        # block the agent forever nor leak a zombie past the waitpid.
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    os.waitpid(pid, 0)
    if timed_out:
        return {"error": f"timed out after {timeout_s}s", "errno": errno.ETIMEDOUT}
    try:
        return pickle.loads(buf)
    except Exception as e:
        return {"error": f"child produced no result: {e}; raw={buf!r}"}


# capset() ABI — mirror of <linux/capability.h>. Lets us drop a single
# capability from the effective+permitted+inheritable sets between the
# privileged open of /dev/cdx_ctrl and the ioctl, so the dispatcher's
# capable(CAP_NET_ADMIN) check sees a stripped credential set.
_LINUX_CAPABILITY_VERSION_3 = 0x20080522
_CAP_NET_ADMIN = 12


class _CapHeader(__import__("ctypes").Structure):
    import ctypes as _ct
    _fields_ = [("version", _ct.c_uint32), ("pid", _ct.c_int)]


class _CapData(__import__("ctypes").Structure):
    import ctypes as _ct
    _fields_ = [
        ("effective",   _ct.c_uint32),
        ("permitted",   _ct.c_uint32),
        ("inheritable", _ct.c_uint32),
    ]


def _drop_cap_net_admin() -> None:
    import ctypes
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.capget.argtypes = [ctypes.POINTER(_CapHeader), ctypes.POINTER(_CapData)]
    libc.capget.restype  = ctypes.c_int
    libc.capset.argtypes = [ctypes.POINTER(_CapHeader), ctypes.POINTER(_CapData)]
    libc.capset.restype  = ctypes.c_int

    hdr = _CapHeader(version=_LINUX_CAPABILITY_VERSION_3, pid=0)
    data = (_CapData * 2)()
    if libc.capget(ctypes.byref(hdr), data) != 0:
        e = ctypes.get_errno()
        raise OSError(e, f"capget: {os.strerror(e)}")
    mask = ~(1 << _CAP_NET_ADMIN) & 0xFFFFFFFF
    data[0].effective   &= mask
    data[0].permitted   &= mask
    data[0].inheritable &= mask
    if libc.capset(ctypes.byref(hdr), data) != 0:
        e = ctypes.get_errno()
        raise OSError(e, f"capset: {os.strerror(e)}")


def _ioctl_work(
    device: str, cmd: int, data_in: bytes,
    *, drop_cap_net_admin: bool = False,
) -> dict:
    import fcntl
    try:
        fd = os.open(device, os.O_RDWR)
    except OSError as e:
        return {"rc": -1, "errno": e.errno, "error": f"open: {e.strerror}"}
    try:
        if drop_cap_net_admin:
            try:
                _drop_cap_net_admin()
            except OSError as e:
                return {"rc": -1, "errno": e.errno,
                        "error": f"capset: {e.strerror}"}
        buf = bytearray(data_in) if data_in else bytearray(0)
        try:
            rc = fcntl.ioctl(fd, cmd, buf, True) if data_in else fcntl.ioctl(fd, cmd, 0)
            return {"rc": int(rc), "errno": 0, "data_hex": bytes(buf).hex()}
        except OSError as e:
            return {"rc": -1, "errno": e.errno, "error": e.strerror}
    finally:
        os.close(fd)


async def ioctl_send(body: dict, state: dict) -> dict:
    """POST {device, cmd, data_hex, [uid], [userns], [drop_cap_net_admin],
    [timeout_ms]} -> ioctl result.

    `uid` drops to an unprivileged UID before open (G1-style). `userns`
    runs the call inside an unmapped CLONE_NEWUSER namespace (the
    unmapped-userns capability-gate case). `drop_cap_net_admin` does capset() between
    open and ioctl so a privileged-opened fd hits the dispatcher with
    a stripped effective set (the mid-flight cap-drop case).
    """
    try:
        device = body["device"]
        cmd    = int(body["cmd"])
        data   = bytes.fromhex(body.get("data_hex", ""))
    except (KeyError, ValueError, TypeError) as e:
        return _error(400, {"error": f"bad request: {e}"})
    uid = body.get("uid")
    if uid is not None:
        uid = int(uid)
    userns = bool(body.get("userns", False))
    drop_cap_net_admin = bool(body.get("drop_cap_net_admin", False))
    timeout_s = float(body.get("timeout_ms", 1000)) / 1000.0

    def _work():
        return _ioctl_work(device, cmd, data,
                           drop_cap_net_admin=drop_cap_net_admin)

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _run_isolated(_work, uid, timeout_s, userns=userns),
    )
    return result


_EXEC_ARGV0_ALLOWED = {
    "ip", "ethtool", "iptables", "modprobe", "rmmod", "insmod",
    "sysctl", "conntrack", "bridge", "tcpdump", "nft",
    # The IPv6 half of iptables, with the same surface: rules, nothing that
    # starts a program. tc is not listed, and stays on the console: `tc exec
    # bpf import ... run` starts whatever it is given.
    "ip6tables",
    # Reading the kernel log. Several subsystems say what they did only
    # there -- VWD logs the classifier hooks appearing and going, and those
    # hooks have no sysfs or procfs face at all -- so without this the only
    # way to see a transition is a UART session, which cannot be scripted
    # alongside the HTTP fixtures the rest of a test uses.
    "dmesg", "lsmod",
    # Wi-Fi. A VAP's lifecycle is driven by these and by nothing else on
    # this image: hostapd owns the AP interface, so it is what has to stop
    # before the interface can unregister, and `iw` is the only way to
    # change an interface's type or take one down to a station. Without
    # them the registration half of the VAP work is testable and the
    # retirement half is not.
    "iw", "hostapd", "hostapd_cli", "wpa_supplicant", "wpa_cli",
    # Routed multicast. ipmr's MFC has no /proc or netlink write surface a
    # test could use: an entry is installed by a process holding an MRT_INIT
    # socket and by nothing else, so the consumer is the control plane and
    # has to be driven directly.
    "smcrouted", "smcroutectl",
    # Stopping the above. argv[0] stays the gate, so this buys the ability
    # to signal a named process and nothing more.
    "kill", "killall",
}


async def exec_cmd(body: dict, state: dict) -> dict:
    """POST {argv[], [timeout_ms]} -> {rc, stdout, stderr}.

    Used by test fixtures for net-config (ip link add, routes, iptables
    rules, etc.) that the DUT needs before and after a scenario. argv[0]
    is whitelisted to keep the surface from becoming arbitrary RCE — the
    agent still runs as root so the shell would inherit that.
    """
    argv = body.get("argv")
    if not isinstance(argv, list) or not argv:
        return _error(400, {"error": "argv must be non-empty list"})
    if argv[0] not in _EXEC_ARGV0_ALLOWED:
        return _error(400, {"error": f"argv[0]={argv[0]!r} not allowed; "
                     f"allowed: {sorted(_EXEC_ARGV0_ALLOWED)}"})
    timeout_s = float(body.get("timeout_ms", 5000)) / 1000.0
    def run():
        started = time.monotonic()
        result = subprocess.run(argv,
                                stdout=subprocess.DEVNULL if body.get("quiet") else subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout_s, check=False)
        return result, started, time.monotonic()
    try:
        # bytes + errors='replace' decode: any allowlisted command can emit
        # non-UTF-8 bytes (tcpdump -X output, iptables names with high-byte
        # chars, etc.) and the default text=True path raises
        # UnicodeDecodeError → 500.
        r, started, ended = await asyncio.get_event_loop().run_in_executor(None, run)
    except FileNotFoundError:
        return _error(501, {"error": f"{argv[0]} not installed"})
    except subprocess.TimeoutExpired:
        return _error(504, {"error": "exec timed out"})
    return {
        "argv":   argv,
        "rc":     r.returncode,
        "stdout": (r.stdout or b"").decode("utf-8", errors="replace"),
        "stderr": r.stderr.decode("utf-8", errors="replace"),
        "at": (started + ended) / 2,
        "span": ended - started,
    }


async def fs_read(body: dict, state: dict) -> dict:
    """POST {path, [max_bytes]} -> {content_hex, size, errno}.

    Returns file contents hex-encoded so binary payloads (e.g.
    /proc/cdx/last_freed_key under CDX_DEBUG_KEY_ZEROING — see the H2
    regression test) survive transport without UnicodeDecodeError.
    Mirrors fs_write's bytes-discipline. max_bytes caps the response.
    """
    try:
        path = body["path"]
    except (KeyError, TypeError) as e:
        return _error(400, {"error": f"bad request: {e}"})
    max_bytes = int(body.get("max_bytes", 1 << 20))

    def _work():
        try:
            with open(path, "rb") as f:
                data = f.read(max_bytes)
            return {"content_hex": data.hex(), "size": len(data), "errno": 0}
        except OSError as e:
            return {"content_hex": "", "size": 0,
                    "errno": e.errno, "error": e.strerror}

    result = await asyncio.get_event_loop().run_in_executor(None, _work)
    return result


async def fs_write(body: dict, state: dict) -> dict:
    """POST {path, content | content_hex, [uid], [timeout_ms]} -> write attempt result.

    Used for sysctl / /proc / /sys writes, including tests that care about
    capability enforcement (`uid` drops privilege before the open), and for
    staging binaries such as test modules: `content_hex` carries bytes
    exactly, where `content` is text.
    """
    try:
        path = body["path"]
        if "content_hex" in body:
            content = bytes.fromhex(body["content_hex"])
        else:
            content = body.get("content", "")
    except (KeyError, TypeError, ValueError) as e:
        return _error(400, {"error": f"bad request: {e}"})
    uid = body.get("uid")
    if uid is not None:
        uid = int(uid)
    timeout_s = float(body.get("timeout_ms", 1000)) / 1000.0

    data = content.encode() if isinstance(content, str) else bytes(content)

    def _work():
        try:
            with open(path, "wb") as f:
                n = f.write(data)
            return {"rc": int(n), "errno": 0}
        except OSError as e:
            return {"rc": -1, "errno": e.errno, "error": e.strerror}

    result = await asyncio.get_event_loop().run_in_executor(
        None, _run_isolated, _work, uid, timeout_s,
    )
    return result


async def netlink_send(body: dict, state: dict) -> dict:
    """POST {protocol, body_hex, [nlmsg_type, nlmsg_flags,
                                  nlmsg_len_override, timeout_ms,
                                  failslab_times, uid, userns]}
    -> raw kernel reply.

    The agent prepends a netlink header; everything else is the caller's
    bytes verbatim, so any netlink protocol can be targeted (XFRM,
    rtnetlink, ...). `nlmsg_len_override` lies about the header's length
    field independent of the body bytes sent, to probe a handler's own
    length validation.

    `uid` / `userns` fork a child that drops privilege before opening
    the netlink socket (the in-kernel netlink_capable gate checks the
    socket opener's credentials), for capability-gate tests.

    `failslab_times` forks a child and arms per-task fail-nth around exactly
    one send (see _netlink_send_failslab): an armed window that covers one
    send is the only way to drive a specific allocation on the handler's
    side to NULL. An XFRM NEWSA, for one, reaches the cdx IPsec SA
    allocator through xdo_dev_state_add().
    """
    try:
        protocol = int(body["protocol"])
        msg = bytes.fromhex(body.get("body_hex", ""))
    except (KeyError, ValueError, TypeError) as e:
        return _error(400, {"error": f"bad request: {e}"})
    nlmsg_type  = int(body.get("nlmsg_type", 0))
    nlmsg_flags = int(body.get("nlmsg_flags", 0))
    nlmsg_len_override = body.get("nlmsg_len_override")
    timeout_s = float(body.get("timeout_ms", 500)) / 1000.0
    failslab_times = body.get("failslab_times")
    uid = body.get("uid")
    if uid is not None:
        uid = int(uid)
    userns = bool(body.get("userns", False))
    try:
        if uid is not None or userns:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _run_isolated(
                    lambda: _netlink_send_sync(
                        protocol, msg, nlmsg_len_override,
                        nlmsg_type, nlmsg_flags, timeout_s,
                    ),
                    uid, timeout_s, userns=userns,
                ),
            )
        elif failslab_times is not None:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                _netlink_send_failslab,
                protocol, msg, nlmsg_len_override,
                nlmsg_type, nlmsg_flags, timeout_s, int(failslab_times),
            )
        else:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                _netlink_send_sync,
                protocol, msg, nlmsg_len_override,
                nlmsg_type, nlmsg_flags, timeout_s,
            )
    except OSError as e:
        return _error(500, {"error": f"socket error: {e}"})
    return result


def _kmemleak_split(report: str) -> list[str]:
    """Split a kmemleak report into per-leak blocks.

    Each leak block starts with "unreferenced object" and runs until
    the next "unreferenced object" or end of text. Empty strings
    between boundaries are dropped.
    """
    blocks: list[str] = []
    current: list[str] = []
    for line in report.splitlines(keepends=True):
        if line.startswith("unreferenced object"):
            if current:
                blocks.append("".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("".join(current))
    return blocks


# Frames that never own an allocation: the kmemleak/slab machinery and
# the skb construction helpers sitting between the slab and the caller.
_NONOWNER_FRAME = re.compile(
    r"^(kmemleak_|kmem_cache_|__kmalloc|kmalloc|krealloc|slab_"
    r"|__build_skb|build_skb|__alloc_skb|__napi_build_skb)"
)


def _owner_frames(block: str, n: int = 2) -> list[str]:
    """First `n` plausible owner frames of a leak block's backtrace."""
    frames: list[str] = []
    in_bt = False
    for line in block.splitlines():
        if "backtrace" in line:
            in_bt = True
            continue
        if not in_bt:
            continue
        frame = line.strip()
        if not frame:
            continue
        if _NONOWNER_FRAME.match(frame):
            continue
        frames.append(frame)
        if len(frames) >= n:
            break
    return frames


def _kmemleak_filter(blocks: list[str], needles: list[str]) -> list[str]:
    """Keep leak blocks whose ALLOCATION OWNER matches any of `needles`.

    Substring-anywhere matching is unsound: the arm64 frame-pointer
    unwinder occasionally emits a stale frame from a previous stack
    user (observed: every DPAA bpool-seed record from cdx module init
    carrying a bogus `abm_build_l2flow` frame below the cdx frames —
    an impossible call chain that made 4600 X3-class false positives
    match an `abm_` needle). The allocation's owner is within the
    first couple of real frames above the allocator, so only those are
    matched.
    """
    if not needles:
        return blocks
    return [
        b for b in blocks
        if any(n in f for n in needles for f in _owner_frames(b))
    ]


async def kmemleak_scan(body: dict, state: dict) -> dict:
    """GET ?filter=[cdx],cdx_ -> filtered kmemleak report.

    Without `filter`, returns the full report (all ~16k baseline DPAA
    false-positives included) — backward-compatible with callers that
    don't care about noise. With `filter`, returns only the leak blocks
    whose trace text contains at least one of the comma-separated
    substrings. Pair with POST /kmemleak-clear to get a true since-
    cursor delta: clear at test start, scan with filter at test end,
    assert blocks == [].
    """
    if not KMEMLEAK_PATH.exists():
        return _error(501, {"error": "kmemleak not available"})
    try:
        await asyncio.to_thread(KMEMLEAK_PATH.write_text, "scan\n")
    except OSError as e:
        return _error(500, {"error": f"scan write failed: {e}"})
    # kmemleak's scanner is async in kernel; wait a bit for results to settle.
    await asyncio.sleep(2.0)
    try:
        report = await asyncio.to_thread(KMEMLEAK_PATH.read_text)
    except OSError as e:
        return _error(500, {"error": f"read failed: {e}"})
    filter_raw = body.get("filter", "")
    needles = [s for s in filter_raw.split(",") if s] if filter_raw else []
    if needles:
        blocks = _kmemleak_filter(_kmemleak_split(report), needles)
        return {
            "report": "".join(blocks),
            "leak_count": len(blocks),
            "filter": needles,
        }
    return {
        "report": report,
        "leak_count": report.count("unreferenced object"),
    }


async def kmemleak_clear(body: dict, state: dict) -> dict:
    """POST -> mark all currently-reported kmemleak leaks as seen.

    Writes "scan" first, waits a beat for the scanner to classify all
    currently-unreferenced objects (`clear` only marks the
    already-classified ones; boot-time allocations that the kernel's
    own background scanner hasn't swept yet would otherwise slip past
    the cursor and appear as "new" leaks on the next scan), then
    writes "clear" to /sys/kernel/debug/kmemleak. Subsequent scans
    (and reads of the file) only show leaks detected after this call.
    This is the cursor primitive for per-test leak deltas: the 16k
    DPAA false-positive baseline is erased once and the test window
    starts clean.
    """
    if not KMEMLEAK_PATH.exists():
        return _error(501, {"error": "kmemleak not available"})
    # The write to "scan" is synchronous — the kernel walks the heap
    # before returning. Necessary to run this BEFORE "clear": `clear`
    # only sets OBJECT_REPORTED on allocations that are already marked
    # UNREFERENCED, and the in-kernel background scanner runs on a
    # 10-minute cadence. Without a forced scan first, fresh boot-time
    # allocations that haven't been classified yet slip past the
    # cursor and reappear as "new" leaks on the very next scan.
    # Offload to a thread so we don't block the event loop during the
    # 30-60s first-boot heap walk.
    # TWO scans, not one: kmemleak only classifies a white object as
    # reportable once its content checksum is STABLE across consecutive
    # scans (mm/kmemleak.c update_checksum — the first scan just
    # records the CRC and returns false). On a freshly booted DUT the
    # background scanner (10-min cadence) has never run, so a single
    # scan leaves every boot-time allocation unclassified, `clear`
    # misses it, and it surfaces as a "new" leak inside the very next
    # test window.
    def _scan_then_clear() -> None:
        KMEMLEAK_PATH.write_text("scan\n")
        KMEMLEAK_PATH.write_text("scan\n")
        KMEMLEAK_PATH.write_text("clear\n")
    try:
        await asyncio.get_event_loop().run_in_executor(None, _scan_then_clear)
    except OSError as e:
        return _error(500, {"error": f"scan/clear failed: {e}"})
    return {"ok": True}


OPERATIONS = {
    "health": health, "counters": counters_get,
    "capture-start": capture_start, "capture-stop": capture_stop,
    "dmesg-delta": dmesg_delta, "kmemleak-scan": kmemleak_scan,
    "kmemleak-clear": kmemleak_clear, "netlink/send": netlink_send,
    "ioctl/send": ioctl_send, "fs/read": fs_read, "fs/write": fs_write,
    "exec": exec_cmd,
}


async def close_captures(state):
    while state["captures"]:
        _, capture = state["captures"].popitem()
        await capture["window"].finish()


def initialize():
    # GPIO-independent eligibility setup shared by both transports.
    try:
        (_FAILSLAB_DIR / "ignore-gfp-wait").write_text("N\n")
    except OSError:
        pass  # _arm_failslab checks the setting before every use.


def build_app():
    from aiohttp import web

    app = web.Application()
    # The same plain state the serial transport keeps (stdio.py), rather than
    # the application's own mapping, which aiohttp wants typed keys for.
    state = {"captures": {}}

    async def cleanup(_app):
        await close_captures(state)

    app.on_cleanup.append(cleanup)

    async def handle(request):
        body = await request.json() if request.can_read_body else {}
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="request must be an object")
        body.update(request.query)
        if "iface" in request.query:
            body["ifaces"] = request.query.getall("iface")
        if "cap_id" in request.match_info:
            body["capture_id"] = request.match_info["cap_id"]
        operation = next(op for op in OPERATIONS if op.replace("/", "-") == request.match_info.route.name)
        try:
            return web.json_response(await OPERATIONS[operation](body, state))
        except AgentError as error:
            return web.json_response(error.detail, status=error.status)

    for operation in OPERATIONS:
        method = "GET" if operation in {"health", "counters", "kmemleak-scan"} else "POST"
        path = "/capture-stop/{cap_id}" if operation == "capture-stop" else "/" + operation
        app.router.add_route(method, path, handle, name=operation.replace("/", "-"))
    return app


def main():
    import sys

    initialize()
    if "--record-kernel" in sys.argv:
        dmesg.record_boot()
    elif "--stdio" in sys.argv:
        from .stdio import main as stdio_main
        stdio_main()
    else:
        from aiohttp import web
        web.run_app(build_app(), host=os.environ.get("ASKD_HOST", "127.0.0.1"),
                    port=int(os.environ.get("ASKD_PORT", "9110")), access_log=None)


if __name__ == "__main__":
    main()
