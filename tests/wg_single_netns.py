"""Exercise the single-tunnel runner against an isolated three-host rig."""

import csv
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT / "bin/testrig-wireguard.sh"


def test_single_runner_with_real_isolated_wireguard_and_routes(tmp_path):
    wg = os.environ.get("WG_TEST_BINARY") or shutil.which("wg")
    required = ("unshare", "nsenter", "ip", "ping", "iperf3")
    if not wg or not Path(wg).is_file() or any(not shutil.which(item) for item in required):
        pytest.skip("WireGuard tools, iperf3, ip, ping, unshare and nsenter are required")
    nft = shutil.which("nft") or ("/usr/sbin/nft" if Path("/usr/sbin/nft").exists() else None)
    if not nft:
        pytest.skip("nft is required")
    probe = subprocess.run(
        ["unshare", "-Urn", "sh", "-c",
         "ip link add probe0 type wireguard && ip link property add probe0 altname wgo-probe"],
        text=True, capture_output=True, check=False,
    )
    if probe.returncode:
        pytest.skip(f"disposable WireGuard namespace unavailable: {probe.stderr.strip()}")
    result = subprocess.run(
        ["unshare", "-Urn", sys.executable, __file__, "--worker", str(tmp_path), wg],
        text=True, capture_output=True, check=False, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def worker(work, wg):
    work = Path(work)
    tools = work / "bin"
    tools.mkdir()
    (tools / "wg").symlink_to(wg)
    scripts = {
        "sudo": '#!/bin/sh\nexec "$@"\n',
        "modprobe": "#!/bin/sh\nexit 0\n",
        "sleep": "#!/bin/sh\nexit 0\n",
        "ssh": """#!/usr/bin/env python3
import os, re, sys
from pathlib import Path
args = sys.argv[1:]
index = next(i for i, value in enumerate(args) if '@' in value)
host = args[index].split('@', 1)[1]
remote = args[index + 1:]
if '-n' in args[:index]:
    fd = os.open('/dev/null', os.O_RDONLY)
    os.dup2(fd, 0)
    os.close(fd)
if len(remote) == 1 and 'wgfp_peer_lease' in remote[0]:
    state = Path(os.environ['FAKE_LEASE_OWNER'])
    owner = state.read_text() if state.exists() else ''
    if 'cat /sys/module/wireguard/parameters/wgfp_peer_lease' in remote[0]:
        print(f'owned={owner} dev=off' if owner else 'unowned')
        sys.exit(0)
    match = re.search(r"'(reserve|release) ([0-9a-f]{32})'", remote[0])
    if not match:
        sys.exit(1)
    action, token = match.groups()
    if action == 'reserve' and (not owner or owner == token):
        state.write_text(token)
    elif action == 'release' and owner == token:
        state.unlink()
    else:
        sys.exit(1)
    sys.exit(0)
if len(remote) == 1 and 'cat /sys/module/wireguard/parameters/wgfp_peer' in remote[0]:
    print('off')
    sys.exit(0)
cmd = ['/bin/sh', '-c', remote[0]] if len(remote) == 1 else remote
os.execvp('nsenter', ['nsenter', '-t', os.environ['NS_' + host.upper()], '-n'] + cmd)
""",
    }
    for name, text in scripts.items():
        path = tools / name
        path.write_text(text)
        path.chmod(0o755)
    env = os.environ | {
        "PATH": f"{tools}:/usr/sbin:{os.environ['PATH']}",
        "TESTRIG_ENV": str(work / "no-testrig-env"),
        "GEN1": "gen@gen1",
        "GEN2": "gen@gen2",
        "DUT": "dut@dut",
        "DUT_KIND": "ask2",
        "RIG_KEY": "",
        "RIG_NS": "",
        "WG_STATE_PATH": str(work / "state"),
        "FAKE_LEASE_OWNER": str(work / "wgfp-owner"),
        "WG_DUT_STAT": str(work / "dut.stat"),
        "J": str(work / "json"),
        "OUT": str(work / "results.csv"),
        "DIRS": "up down",
        "DUR": "3",
        "STEADY_FROM": "0",
        "STREAMS": "1",
        "REPS": "1",
        "WGFP": "0",
    }
    children = {}

    def command(*args, check=True, **kwargs):
        result = subprocess.run(args, env=env, text=True, capture_output=True, check=False, **kwargs)
        if check and result.returncode:
            raise RuntimeError(f"{args} failed:\n{result.stdout}\n{result.stderr}")
        return result

    def net(host, *args, check=True):
        return command("nsenter", "-t", str(children[host].pid), "-n", *args, check=check)

    def rig(*args, check=True, **options):
        result = subprocess.run(
            ["bash", str(SINGLE), *args], env=env | options, text=True,
            capture_output=True, check=False, timeout=60,
        )
        if check and result.returncode:
            raise AssertionError(f"rig {args} failed:\n{result.stdout}\n{result.stderr}")
        return result

    try:
        parent_ns = os.stat("/proc/self/ns/net").st_ino
        for host in ("dut", "gen1", "gen2"):
            child = subprocess.Popen(
                ["unshare", "-n", "/usr/bin/sleep", "120"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env,
            )
            children[host] = child
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError(f"{host} namespace failed: {child.stderr.read().decode()}")
                if os.stat(f"/proc/{child.pid}/ns/net").st_ino != parent_ns:
                    break
                time.sleep(0.01)
            else:
                raise RuntimeError(f"{host} namespace did not start")
            env[f"NS_{host.upper()}"] = str(child.pid)
            net(host, "ip", "link", "set", "lo", "up")

        for left, host1, right, host2 in (
            ("eth3", "dut", "g1", "gen1"),
            ("eth4", "dut", "g2", "gen2"),
            ("direct1", "gen1", "direct2", "gen2"),
        ):
            command("ip", "link", "add", left, "type", "veth", "peer", "name", right)
            command("ip", "link", "set", left, "netns", str(children[host1].pid))
            command("ip", "link", "set", right, "netns", str(children[host2].pid))
            net(host1, "ip", "link", "set", left, "up")
            net(host2, "ip", "link", "set", right, "up")
        for host, dev, address in (
            ("dut", "eth3", "10.99.1.185/24"),
            ("dut", "eth4", "10.99.2.185/24"),
            ("gen1", "g1", "10.99.1.112/24"),
            ("gen2", "g2", "10.99.2.113/24"),
            ("gen1", "direct1", "10.99.99.112/24"),
            ("gen2", "direct2", "10.99.99.113/24"),
        ):
            net(host, "ip", "addr", "add", address, "dev", dev)
        for host, dev, address in (
            ("dut", "eth3", "fd99:1::185/64"),
            ("dut", "eth4", "fd99:2::185/64"),
            ("gen1", "g1", "fd99:1::112/64"),
            ("gen2", "g2", "fd99:2::113/64"),
        ):
            net(host, "ip", "-6", "addr", "add", address, "dev", dev, "nodad")
        net("dut", "sh", "-c",
            "printf 1 > /proc/sys/net/ipv4/ip_forward; "
            "printf 1 > /proc/sys/net/ipv6/conf/all/forwarding")
        net("dut", "nft", "add", "table", "inet", "foreign")
        net("gen1", "ip", "route", "add", "10.99.200.0/24", "via", "10.99.1.186")
        net("gen1", "ip", "-6", "route", "add", "fd99:200::/64", "via", "fd99:1::186")
        baseline = {
            (host, family): net(host, "ip", family, "rule", "show").stdout
            for host in ("gen1", "gen2") for family in ("-4", "-6")
        }
        baseline_routes = (
            net("gen1", "ip", "route", "show", "exact", "10.99.200.0/24").stdout,
            net("gen1", "ip", "-6", "route", "show", "exact", "fd99:200::/64").stdout,
        )
        for outer in ("4", "6"):
            rig("setup", WG_SKIP_SERVERS="1", WG_NOTRACK="1", WGMTU="1300", OUTER=outer)
            net("gen1", "ping", "-c", "1", "-W", "5", "-I", "10.99.1.112", "10.99.200.113")
            net("gen1", "ping", "-6", "-c", "1", "-W", "5", "-I", "fd99:1::112", "fd99:200::113")
            occupied = rig("run", "wg-v4", check=False, WG_SKIP_SERVERS="1",
                           WG_EXTERNAL_IPERF="1", OUTER=outer)
            assert occupied.returncode and "already reserved" in occupied.stderr
            net("dut", "ip", "link", "show", "wg0")
            rig("teardown", WG_SKIP_SERVERS="0", WGMTU="1420", OUTER=outer)
            assert not (work / "state.single-lease").exists()
            assert net("dut", "ip", "link", "show", "wg0", check=False).returncode
            net("dut", "nft", "list", "table", "inet", "foreign")
            assert "wgn" not in net("dut", "nft", "list", "tables").stdout
            for key, before in baseline.items():
                assert net(key[0], "ip", key[1], "rule", "show").stdout == before
            assert (
                net("gen1", "ip", "route", "show", "exact", "10.99.200.0/24").stdout,
                net("gen1", "ip", "-6", "route", "show", "exact", "fd99:200::/64").stdout,
            ) == baseline_routes

        rig("run", "wg-v4", WG_SKIP_SERVERS="0", WG_NOTRACK="1", RECREATE="1")
        with (work / "results.csv").open(newline="") as result_file:
            rows = list(csv.DictReader(result_file))
        assert [row["dir"] for row in rows] == ["up", "down"]
        assert all(float(row["gbps"]) > 0 for row in rows)
        assert not (work / "state.single-lease").exists()
        rig("run", "ceiling-v4", WG_SKIP_SERVERS="0", DIRS="up")
        assert not (work / "state.single-lease").exists()
        assert net("gen1", "ip", "link", "show", "wgd", check=False).returncode
        assert net("gen2", "ip", "link", "show", "wgd", check=False).returncode
    finally:
        for child in children.values():
            if child.poll() is None:
                child.terminate()
        for child in children.values():
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


if __name__ == "__main__" and sys.argv[1:2] == ["--worker"]:
    worker(sys.argv[2], sys.argv[3])
