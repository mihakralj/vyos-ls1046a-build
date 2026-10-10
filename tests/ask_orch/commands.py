"""Checked agent commands and UART framing shared by the test harness."""

import asyncio
import base64
import hashlib
import json
import re
import shlex
import time

import pytest


async def read(agent, session, path):
    limit = 16 << 20 if path == "/proc/cdx_flowtable" else 1 << 20
    result = await agent.fs_read(session, path, max_bytes=limit)
    assert result.get("size", 0) < limit, (path, "truncated diagnostics")
    assert result["errno"] == 0, (path, result)
    return bytes.fromhex(result["content_hex"]).decode()


async def command(agent, session, *argv, check=True, timeout_ms=15000, quiet=False):
    result = await agent.exec_cmd(session, list(argv), timeout_ms=timeout_ms,
                                  **({"quiet": True} if quiet else {}))
    if check:
        assert result["rc"] == 0, result
    return result


async def console_command(console, *argv, check=True, timeout=20, resync=False):
    if hasattr(console, "session"):
        result = await asyncio.to_thread(console.run, shlex.join(argv), timeout)
        if check:
            assert result.rc == 0, result.stdout
        return {"rc": result.rc, "stdout": result.stdout}
    # BusyBox line editing can wrap and redraw the echoed command. Frame
    # actual output rather than relying on the console's echo stripping.
    #
    # `resync` is for a caller whose command is idempotent and who would rather
    # be told the framing was lost than have the test fail on line noise. The
    # UART has no flow control and the DUT logs `ttyS0: input overrun(s)` under
    # a fast writer: characters vanish mid-word and the marker comes back
    # misspelled, which is indistinguishable from the command never running.
    # Such a caller gets rc None, resynchronises the prompt and decides for
    # itself; everyone else still fails loudly, because for a mutating command
    # "it is unknown whether this ran" is not a result worth continuing on.
    assert all("\n" not in arg for arg in argv), "use console_python for scripts"
    marker = f"__ASK_OUTPUT_{time.monotonic_ns()}__"
    cmd = f"printf '\\n%s\\n' {shlex.quote(marker)}; {shlex.join(argv)}"
    try:
        result = await asyncio.to_thread(console.run, cmd, timeout)
    except TimeoutError as error:
        if not resync:
            raise
        await asyncio.to_thread(console.sync_prompt)
        return {"rc": None, "stdout": str(error)}
    match = re.search(r"(?:^|\n)" + marker + r"\r?\n", result.stdout)
    if not match and resync:
        await asyncio.to_thread(console.sync_prompt)
        return {"rc": None, "stdout": result.stdout}
    assert match, ("missing console output boundary", result.stdout)
    stdout = result.stdout[match.end():]
    if check:
        assert result.rc == 0, stdout
    return {"rc": result.rc, "stdout": stdout}


CONSOLE_NOISE = re.compile(
    r"(?:(?:ask-flowtable\[\d+\]: |\[\s*\d+\.\d+\] )[^\n]*|\*\* \d+ printk messages dropped \*\*)\r?\n?")


def console_json(text):
    """Decode controller JSON from UART output, minus interleaved console lines.

    The raw UART transcript still records those lines for diagnosis.
    """
    return json.loads(CONSOLE_NOISE.sub("", text))


async def flowtable_json(console, *args):
    """Run the controller over the UART and decode its JSON."""
    result = await console_command(console, "/usr/sbin/ask-flowtable", *args)
    return console_json(result["stdout"])


async def remove_qdisc(console, device, attachment, kind):
    """Deleting an absent qdisc is fine; leaving the test qdisc is not."""
    await console_command(console, "tc", "qdisc", "del", "dev", device, attachment,
                          check=False, timeout=30)
    result = await console_command(console, "tc", "-j", "qdisc", "show", "dev", device,
                                   timeout=30)
    assert not any(qdisc.get("kind") == kind for qdisc in console_json(result["stdout"])), result


async def console_python(console, script, *, timeout=20, attempts=3):
    if hasattr(console, "python"):
        result = await asyncio.to_thread(console.python, script, timeout)
        assert result["rc"] == 0, result
        return result
    # The physical UART can lose characters in long input lines, so stage short
    # chunks and verify the exact script before executing any test operation.
    # A dropped character corrupts the staged text, not the console, so retry
    # the staging rather than failing the test on the line noise: decoding each
    # chunk as it arrived turned one lost character into "base64: invalid
    # input" and lost the whole test. Accumulate the encoded text, decode once,
    # and let the digest decide whether it survived.
    encoded = base64.b64encode(script.encode()).decode()
    wanted = hashlib.sha256(script.encode()).hexdigest()
    path = f"/tmp/ask_ft_{time.monotonic_ns()}.py"
    staged = f"{path}.b64"
    last_failure = None
    try:
        for attempt in range(attempts):
            # Tolerant, and it has to be: this is the first command of each
            # attempt, so a boundary lost to an overrun here used to raise
            # before the retry loop it sits inside could do anything. `rm -f`
            # is idempotent, and a delete that silently did not happen leaves
            # stale text the digest below catches on this same pass.
            try:
                await console_command(console, "rm", "-f", path, staged, resync=True)
                for offset in range(0, len(encoded), 144):
                    await console_command(console, "sh", "-c",
                                          f"printf %s {shlex.quote(encoded[offset:offset + 144])} "
                                          f">> {shlex.quote(staged)}")
                decoded = await console_command(console, "sh", "-c",
                                                f"base64 -d {shlex.quote(staged)} > {shlex.quote(path)}",
                                                check=False)
                digest = await console_command(console, "sha256sum", path, check=False)
                if decoded["rc"] == digest["rc"] == 0 and digest["stdout"].split()[:1] == [wanted]:
                    break
                last_failure = (decoded, digest)
            except (AssertionError, TimeoutError) as error:
                # Kernel messages can split either output marker. Only the
                # private staging files have changed: discard and restage.
                # Execution stays outside this retry boundary because an
                # unacknowledged test operation may already have happened.
                last_failure = repr(error)
                await asyncio.to_thread(console.sync_prompt)
        else:
            pytest.fail(f"UART staging failed {attempts} times: {last_failure}")
        return await console_command(console, "python3", path, timeout=timeout)
    finally:
        # Tolerant for the same reason, and with less at stake: this runs on
        # the way out, the rootfs is an initramfs that forgets /tmp at the next
        # boot, and a cleanup that lost its framing must not turn a passing
        # test into an error.
        await console_command(console, "rm", "-f", path, staged, resync=True)
