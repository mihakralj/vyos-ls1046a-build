"""Shared fixtures: DUT SSH stdio, dell1 SSH commands, and dell2 stdio.

Function-scoped aiohttp sessions follow pytest-asyncio's event loops. Kernel
capture windows include fixture setup and teardown, retaining bulk evidence
only on failures. Bench settings come from bench.json and the selected DUT.
"""

from __future__ import annotations

import gzip
import asyncio
import json
import os
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio

pytest_plugins = ["ask_orch.pytest_plugin", "pytester"]
pytest.register_assert_rewrite("ask_orch", *(
    path.stem for path in Path(__file__).parent.glob("_*.py")
    if path.stem != "__init__"
))

from ask_orch.artifacts import artifact_dir, record
from ask_orch.capture import capture_window, verify_capture
from ask_orch import client
from ask_orch.uart import Console, set_target_session
from ask_orch.serial import SSHSession
from ask_orch.lab import Endpoint
from askd_agent.wire import VERSION as SERIAL_PROTOCOL
from ask_orch.guest import Guest
from ask_orch.provenance import agent_sources, firmware_script

from _dmesg_allowlist import load_allowlist


# ---- per-test aiohttp ---------------------------------------------------

# Function-scoped: pytest-asyncio's default test loop is function-scoped,
# and aiohttp.ClientSession is bound to the loop it was created under.
# Session-scoping the ClientSession would span multiple loops and raise
# "Timeout context manager should be used inside a task". ClientSession
# creation cost is ~1ms, negligible compared to the HTTP round-trips
# each test does.
@pytest_asyncio.fixture
async def aiohttp_session():
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        yield s


@pytest.fixture(scope="session")
def target_agent(bench_health):
    return client.TARGET


@pytest.fixture(scope="session")
def dut_uart(hardware_bench):
    """Retain the upstream fixture name while sharing one SSH agent per node."""
    with SSHSession(Endpoint.from_env("dut"),
                    log_path=artifact_dir("session") / "dut-ssh.log") as session:
        set_target_session(session)
        try:
            with SSHSession(Endpoint.from_env("wan"), local=True,
                            log_path=artifact_dir("session") / "dell2-agent.log") as wan_session:
                client.set_wan_session(wan_session)
                try:
                    yield session
                finally:
                    client.set_wan_session(None)
        finally:
            set_target_session(None)


# Loaded once per session. An expired entry raises here, failing the suite
# before hardware setup rather than letting a stale suppressor mask a regression.
@pytest.fixture(scope="session")
def dmesg_allowlist():
    return load_allowlist()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def bench_health(request, hardware_bench, dmesg_allowlist, dut_uart):
    """Check firmware, logging and selected release capabilities before mutations."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        health = await client.TARGET.health(session)
        record("dut", health, nodeid="session")
        assert health.get("ok") and health.get("capture_protocol") == 2 and health.get("serial_protocol") == SERIAL_PROTOCOL, (
            "DUT needs the current test agent with reliable kernel capture", health)
        required = {"ip", "nft", "conntrack", "python3"}
        for item in request.session.items:
            for marker in item.iter_markers("requires"):
                required.update(marker.args)
        result = await asyncio.to_thread(dut_uart.python, firmware_script(required))
        assert result["rc"] == 0, result
        firmware = json.loads(result["stdout"])
        expected = agent_sources(Path(__file__).resolve().parents[1] / "askd_agent")
        firmware["agent_matches_checkout"] = firmware["agent_sources"] == expected
        record("firmware", firmware, nodeid="session")
        config = await client.TARGET.fs_read(session, "/proc/config.gz")
        assert config["errno"] == 0, config
        kernel_config = gzip.decompress(bytes.fromhex(config["content_hex"])).decode()
        assert "CONFIG_KASAN=y" in kernel_config.splitlines(), (
            "upstream regression tests require a compatible KASAN image; use the repository build path")
        boot = await client.TARGET.boot_log(session)
        record("boot-kernel", boot, nodeid="session")
        try:
            verify_capture(boot, "boot", dmesg_allowlist)
        except BaseException:
            if boot.get("artifact"):
                data = await client.TARGET.artifact(session, boot["artifact"]["id"])
                (artifact_dir("session") / "boot-kernel-log.json").write_bytes(data)
            raise
        finally:
            if boot.get("artifact"):
                await client.TARGET.request(session, "artifact/release", {"id": boot["artifact"]["id"]})
        if request.config.getoption("--release"):
            assert not firmware["missing_binaries"], (
                "release firmware lacks required tools", firmware["missing_binaries"])
            assert firmware["agent_matches_checkout"], (
                "DUT agent differs from this checkout; rebuild, stage and boot the current image")
        return {"health": health, "boot": boot}


@pytest_asyncio.fixture
async def _target_reachable(aiohttp_session, target_agent):
    """Fail-fast per-test if the target agent is unreachable. Cheaper
    than repeating commands against a lost UART session."""
    try:
        h = await target_agent.health(aiohttp_session)
    except Exception as e:
        pytest.exit(f"target agent unreachable: {e}", returncode=2)
    if not h.get("ok"):
        pytest.exit(f"target /health returned ok=False: {h!r}", returncode=2)


@pytest.fixture(scope="session")
def lan(hardware_bench):
    """Out-of-band LAN control on dell1, inside the selected data namespace."""
    guest = Guest("dell1")
    record("lan-agent", {"version": guest.check()}, nodeid="session")
    return guest


# ---- per-test ------------------------------------------------------------

@pytest_asyncio.fixture
async def splat_window(request, aiohttp_session, target_agent, dmesg_allowlist):
    """Wrap a test in a capture window; fail if new kernel splats appear.

    Even if the test's main assertion failed, run the splat check — a
    sanitizer report is independently important and surfacing both
    signals beats hiding one.

    Splat filtering is two-stage: the agent emits everything matching
    SPLAT_RE; this fixture then drops anything in the checked-in
    allowlist (golden/dmesg_allowlist.yaml). Test authors should *not*
    add inline filters here — extend the YAML.
    """
    async with capture_window(target_agent, aiohttp_session, request.node.nodeid, dmesg_allowlist,
                              failed_check=lambda: getattr(request.node, "_ask_failed", False)) as cap_id:
        yield cap_id


# Shared fixtures are registered here; test modules declare dependencies by name.
from _flowtable_connections import connections  # noqa: F401
from _flowtable_ipv6 import hairpin6, ipv6_rig  # noqa: F401
from _flowtable_pppoe import pppoe_rig  # noqa: F401
from _flowtable_qos import qos  # noqa: F401
from _flowtable_rig import rig  # noqa: F401
from _flowtable_selective_neighbour import selective  # noqa: F401
from _flowtable_service import service  # noqa: F401
from _flowtable_service_bridge import bridge_service, bridge_software  # noqa: F401
from _flowtable_service_ipsec import ipsec_service  # noqa: F401
from _flowtable_service_multicast import multicast_service  # noqa: F401
from _flowtable_service_multicast_leave import listener_bridge  # noqa: F401
from _flowtable_service_vlan import vlan_service  # noqa: F401
from _flowtable_tcp_snat import tcp_snat  # noqa: F401
from _flowtable_tunnel import tunnel_rig  # noqa: F401
from _flowtable_vlan import vlan_rig  # noqa: F401
from _mcast_e2e import mcast_bridge, mroute_lan_bridge, smcrouted, stream_cpu  # noqa: F401
from _mcast_helpers import pcap_cleanup_lan  # noqa: F401
from _mcast_windows import multicast_rig  # noqa: F401
from _topology import ipv6_topology  # noqa: F401
