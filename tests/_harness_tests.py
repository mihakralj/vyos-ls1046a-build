"""Host-only regression checks for the imported harness's lab adapter."""

import json
import io
import re
import shlex
import subprocess
import zipfile
from urllib.parse import unquote
from pathlib import Path
from types import SimpleNamespace

import pytest

from ask_orch.lab import Bench, Endpoint, RunResult, SUITE_ROOT, source_archive, temporary_wan_vlan
from ask_orch.serial import SSHSession
from ask_orch.pytest_plugin import backend_requirements, pytest_runtest_setup
from run_test import command, parse_arguments, remote_command, trusted_host_keys

pytestmark = pytest.mark.host


@pytest.fixture(autouse=True)
def _target_reachable():
    yield


@pytest.fixture(autouse=True)
def splat_window():
    yield


@pytest.mark.parametrize("target,host,namespace,nic", [
    ("dut1", "192.168.1.185", "", "enp1s0f0np0"),
    ("dut2", "192.168.1.106", "n106", "enp1s0f1np1"),
])
def test_profiles(target, host, namespace, nic):
    bench = Bench.load(target)
    env = bench.environment({})
    assert env["ASK_LAB_DUT"] == target
    assert env["ASK_LAN_NETNS"] == env["ASK_WAN_NETNS"] == namespace
    assert env["ASK_LAN_NIC"] == env["ASK_WAN_INJECT_IF"] == nic
    assert env["ASK_DUT_SSH_HOST"] == host
    assert env["ASK_LAN_SSH_HOST"] == "192.168.1.112"
    assert env["ASK_WAN_SSH_HOST"] == "192.168.1.113"
    assert env["ASK_WAN_IPERF_IP"] == "10.99.2.113"
    assert env["ASK_TARGET_IP"] == "10.99.1.185"
    assert env["ASK_PPPOE_SERVER_IF"] == env["ASK_MROUTE_WAN_IF"] == env["ASK_DHCP_WAN_IF"] == "askwan3900"


@pytest.mark.parametrize("alias,name", [("185", "dut1"), ("106", "dut2")])
def test_numeric_profiles_remain_aliases(alias, name):
    assert Bench.load(alias) == Bench.load(name)


def test_preflight_payloads_compile_for_both_duts(monkeypatch):
    sources = []

    def python(endpoint, source, timeout=30, **kwargs):
        compile(source, "<lab-preflight>", "exec")
        sources.append(source)
        return {"rc": 0, "stdout": json.dumps({"ok": True})}

    monkeypatch.setattr(Endpoint, "python", python)
    for target in ("dut1", "dut2"):
        assert Bench.load(target).preflight()["ok"]
    assert len(sources) == 6


def test_ssh_quotes_remote_arguments():
    endpoint = Endpoint("lan", "192.168.1.112", "admin", namespace="n106")
    argv = endpoint.command(["printf", "%s", "value; not a command"])
    assert "BatchMode=yes" in argv
    assert shlex.split(argv[-1]) == ["sudo", "-n", "ip", "netns", "exec", "n106",
                                      "printf", "%s", "value; not a command"]


def test_management_bypasses_data_namespace():
    endpoint = Endpoint("lan", "192.168.1.112", "admin", namespace="n106")
    assert shlex.split(endpoint.command(["true"], management=True)[-1]) == ["sudo", "-n", "true"]


def test_worker_uses_explicit_trusted_host_file(monkeypatch, tmp_path):
    path = tmp_path / "known_hosts"
    monkeypatch.setenv("ASK_SSH_KNOWN_HOSTS", str(path))
    argv = Endpoint("lan", "192.168.1.112", "admin").ssh()
    assert "UserKnownHostsFile=" + str(path) in argv
    assert "StrictHostKeyChecking=yes" in argv


def test_stage_copies_only_trusted_public_host_entries(monkeypatch):
    calls = []

    def lookup(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=argv[-1] + " ssh-ed25519 public-key\n")

    monkeypatch.setattr(subprocess, "run", lookup)
    entries = trusted_host_keys(Bench.load("dut1"))
    assert calls == [["ssh-keygen", "-F", host] for host in
                     ("192.168.1.185", "192.168.1.112", "192.168.1.113")]
    assert entries.count("public-key") == 3


def test_rejects_management_port_in_test_profile(tmp_path):
    config = json.loads((SUITE_ROOT / "bench.json").read_text())
    config["duts"]["dut1"]["lan_if"] = "eth0"
    path = tmp_path / "bench.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="management eth0"):
        Bench.load("185", path)


@pytest.mark.parametrize("host", ["-oProxyCommand=bad", "host; command", ""])
def test_rejects_unsafe_ssh_destination(host):
    with pytest.raises(ValueError):
        Endpoint("dut", host, "vyos")


def test_timeout_is_never_replayed(monkeypatch):
    calls = []

    def timed_out(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timed_out)
    with pytest.raises(TimeoutError, match="not replayed"):
        Endpoint("dut", "192.168.1.185", "vyos").execute(["true"], timeout=1)
    assert len(calls) == 1


def test_runner_uses_flattened_paths_and_collects_by_default():
    argv, env = command("dut", {}, executable="python")
    assert argv == ["python", "-m", "pytest", "-c", str(SUITE_ROOT / "pyproject.toml"),
                    str(SUITE_ROOT), "--collect-only"]
    assert env["PYTHONPATH"] == str(SUITE_ROOT)


def test_runner_requires_explicit_execution():
    argv, _ = command("dut", {"ASK_LAB_EXECUTE": "1"}, executable="python")
    assert "--lab-run" in argv and "--collect-only" not in argv


@pytest.mark.parametrize("argv,selection", [
    (["--dut", "dut1", "--collect-only", "-m", "portable"], ["-m", "portable"]),
    (["dut", "--dut", "106", "--run", "-k", "ipsec or mcast"], ["-k", "ipsec or mcast"]),
])
def test_pytest_filter_values_are_not_runner_positionals(argv, selection):
    args, extra = parse_arguments(argv)
    assert args.suite == "dut" and extra == selection


@pytest.mark.parametrize("filename,capabilities", [
    ("flowtable_capacity.py", ("native_flowtable", "kasan")),
    ("mcast_e2e.py", ("native_flowtable", "kasan")),
    ("ipsec_xfrm_offload.py", ("legacy_cdx", "kasan")),
    ("smoke.py", ("kasan",)),
])
def test_imported_backend_guards(filename, capabilities):
    assert backend_requirements(filename) == capabilities


def test_missing_backend_stops_before_agent_setup(monkeypatch):
    monkeypatch.setenv("ASK_RUNNER_ROLE", "wan")
    config = SimpleNamespace(
        _ask_broken=None, getoption=lambda name: name == "--lab-run",
        _ask_lab_preflight={"ok": True, "dut": "dut1",
                            "nodes": {"dut": {"capabilities": {"ask2": True}}}})
    item = SimpleNamespace(config=config, get_closest_marker=lambda name: name == "hardware",
                           iter_markers=lambda name: [pytest.mark.backend("native_flowtable", "kasan").mark])
    with pytest.raises(pytest.skip.Exception, match="kasan, native_flowtable"):
        pytest_runtest_setup(item)


def test_disruption_gate_stops_before_preflight(monkeypatch):
    monkeypatch.setenv("ASK_RUNNER_ROLE", "wan")
    config = SimpleNamespace(_ask_broken=None, getoption=lambda name: name == "--lab-run")
    item = SimpleNamespace(config=config,
                           get_closest_marker=lambda name: name in {"hardware", "destructive"})
    with pytest.raises(pytest.skip.Exception, match="allow-disruptive"):
        pytest_runtest_setup(item)


def test_dhcp_options_preserve_authoritative_isolated_server():
    from flowtable_dhcp import GATEWAY_OPTIONS

    assert GATEWAY_OPTIONS == ("--interface=eth3", "--bind-dynamic", "--dhcp-authoritative", "--port=0")


def test_upstream_windows_run_for_markers_added_after_collection(pytester):
    pytester.makeini('''
[pytest]
asyncio_default_fixture_loop_scope = function
markers = upstream: imported backend coverage
''')
    pytester.makeconftest('''
import pytest
import pytest_asyncio
from ask_orch.pytest_plugin import upstream_fixture_setup

@pytest.fixture
def order():
    return []

@pytest_asyncio.fixture
async def _target_reachable(order):
    order.append("reachable")

@pytest_asyncio.fixture
async def splat_window(order):
    order.append("capture")
    yield
''')
    pytester.makepyfile('''
def test_windows(order):
    assert order == ["reachable", "capture"]
''')

    class LateMarker:
        def pytest_collection_modifyitems(self, items):
            for item in items:
                item.add_marker("upstream")

    result = pytester.runpytest_inprocess("-q", plugins=[LateMarker()])
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize("existing", [False, True])
def test_wan_vlan_only_removes_owned_interfaces(monkeypatch, existing):
    calls = []

    def execute(endpoint, argv, **kwargs):
        calls.append(argv)
        if argv[:4] == ["ip", "-j", "-d", "link"]:
            row = {"link_index": 3, "flags": ["UP"],
                   "linkinfo": {"info_kind": "vlan", "info_data": {"id": 3900}}}
            return RunResult("", json.dumps([row]), 0) if existing else RunResult("", "absent", 1)
        if argv[:3] == ["ip", "-j", "link"]:
            return RunResult("", json.dumps([{"ifindex": 3}]), 0)
        return RunResult("", "", 0)

    monkeypatch.setattr(Endpoint, "execute", execute)
    with temporary_wan_vlan(Bench.load("dut2"), "askwan3900", 3900):
        pass
    created = any(argv[:3] == ["ip", "link", "add"] for argv in calls)
    removed = any(argv[:3] == ["ip", "link", "del"] for argv in calls)
    assert created == removed == (not existing)


def test_local_documentation_links_and_rig_names():
    for name in ("README.md", "testing.md"):
        document = SUITE_ROOT / name
        text = document.read_text()
        for target in re.findall(r"\[[^\]\n]+\]\(([^)]+)\)", text):
            if target.startswith(("https://", "http://", "#")):
                continue
            assert (document.parent / unquote(target.split("#")[0])).exists(), (name, target)
        assert all(role in text for role in ("dell1", "dell2", "dut1", "dut2"))


def test_source_archive_excludes_environments_and_caches(tmp_path):
    (tmp_path / "script.py").write_text("print('included')")
    for name in (".venv", "__pycache__", ".pytest_cache"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "private.py").write_text("excluded")
    with zipfile.ZipFile(io.BytesIO(source_archive(tmp_path))) as archive:
        assert archive.namelist() == ["script.py"]


@pytest.mark.parametrize("target,namespace", [("dut1", ""), ("dut2", "n106")])
def test_hardware_worker_is_on_dell2(target, namespace):
    argv = remote_command(Bench.load(target), Path("/tmp/runner"), Path("/tmp/suite"),
                          ["-k", "portable"])
    remote = shlex.split(argv[-1])
    assert "-A" in argv and argv[-2] == "admin@192.168.1.113"
    assert remote[:3] == ["sudo", "-n", "--preserve-env=SSH_AUTH_SOCK"]
    assert ("ip" in remote) == bool(namespace)
    assert f"ASK_RUNNER_NETNS={namespace}" in remote
    assert remote[-8:] == ["/tmp/runner/.venv/bin/python", "/tmp/suite/run_test.py",
                          "--dut", target, "--run", "--on-runner", "-k", "portable"]


def test_remote_settings_preserve_test_overrides_not_control_identity(monkeypatch):
    monkeypatch.setenv("ASK_MROUTE_WAN_VID", "3901")
    monkeypatch.setenv("ASK_PPPOE_INNER_LOCAL", "10.98.0.9")
    monkeypatch.setenv("ASK_RUNNER_NETNS", "wrong")
    monkeypatch.setenv("ASK_DUT_SSH_KEY", "/private/key")
    monkeypatch.setenv("ASK_API_TOKEN", "not-forwarded")
    argv = remote_command(Bench.load("dut1"), Path("/tmp/runner"), Path("/tmp/suite"), [])
    remote = shlex.split(argv[-1])
    assert "ASK_MROUTE_WAN_VID=3901" in remote and "ASK_PPPOE_INNER_LOCAL=10.98.0.9" in remote
    assert "ASK_RUNNER_NETNS=" in remote and "ASK_RUNNER_NETNS=wrong" not in remote
    assert not any("PRIVATE" in value or "SSH_KEY=" in value or "API_TOKEN=" in value for value in remote)


def test_framed_agent_can_run_over_stdio():
    endpoint = Endpoint("wan", "127.0.0.1", "root")
    with SSHSession(endpoint, local=True) as session:
        for _ in range(2):
            result = session.python("print('stdio protocol')", timeout=10)
            assert result["rc"] == 0 and result["stdout"].strip() == "stdio protocol"
        assert len(session.scripts) == 1
    assert session.process.poll() == 0