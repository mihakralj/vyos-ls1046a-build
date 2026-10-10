"""Run the real moal receive-drop path under the DUT's KASAN kernel.

This optional case is outside the wired Dell rig. Supply a prebuilt signed
probe via ASK_MWIFIEX_RX_MODULE and its matching moal.ko via
ASK_MWIFIEX_DRIVER_MODULE. The test never builds or strips the probe.
"""
import asyncio
import os
from pathlib import Path
import subprocess

import pytest

from ask_orch.uart import Console
from _flowtable_rig import (artifact_dir, command, console_command)

DRIVER_MODULE = os.environ.get("ASK_MWIFIEX_DRIVER_MODULE", "")
OBJCOPY = os.environ.get("ASK_MWIFIEX_OBJCOPY", "aarch64-linux-gnu-objcopy")


@pytest.fixture(scope="module")
def probe_module():
    """Require operator-provided matching binaries; preserve the module signature."""
    given = os.environ.get("ASK_MWIFIEX_RX_MODULE")
    if not given or not DRIVER_MODULE:
        pytest.skip("Wi-Fi requires a prebuilt signed probe and matching ASK_MWIFIEX_DRIVER_MODULE")
    built = Path(given)
    assert built.is_file() and Path(DRIVER_MODULE).is_file(), (given, DRIVER_MODULE)
    return built


async def test_mwifiex_receive_drop(aiohttp_session, target_agent, splat_window, probe_module):
    module = probe_module
    # Refuse to test a different driver from the one whose headers built the
    # probe. Compare the ELF note directly, without depending on DUT readelf.
    destination = artifact_dir("mwifiex-rx-probe")
    note = destination / "moal-build-id.note"
    subprocess.run([str(OBJCOPY), "--dump-section", f".note.gnu.build-id={note}",
                    DRIVER_MODULE, str(destination / "moal-copy.ko")], check=True)
    actual = await target_agent.fs_read(aiohttp_session, "/sys/module/moal/notes/.note.gnu.build-id")
    assert actual["errno"] == 0 and bytes.fromhex(actual["content_hex"]) == note.read_bytes(), actual
    path = "/tmp/ask_mwifiex_rx_test.ko"
    data = module.read_bytes()
    # Allow the line about 2 KB/s, well under what it carries, plus margin.
    result = await target_agent.fs_write(aiohttp_session, path, data,
                                         timeout_ms=30000 + len(data) // 2)
    assert result["errno"] == 0 and result["rc"] == len(data), result
    with Console.target(log_path=str(artifact_dir() / "mwifiex-rx-uart.log")) as con:
        await asyncio.to_thread(con.login, "root", None)
        try:
            await command(target_agent, aiohttp_session, "insmod", path)
            await command(target_agent, aiohttp_session, "rmmod", "ask_mwifiex_rx_test")
            log = await command(target_agent, aiohttp_session, "dmesg")
            assert "mwifiex RX cleanup: 64 detached EasyMesh drops passed" in log["stdout"]
        finally:
            await command(target_agent, aiohttp_session, "rmmod", "ask_mwifiex_rx_test", check=False)
            await console_command(con, "rm", "-f", path)
