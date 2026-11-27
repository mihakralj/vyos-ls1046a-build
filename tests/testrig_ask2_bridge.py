"""Host regression tests for the hardened ASK2 bridge runner (bin/testrig-ask2-bridge.sh)."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
GEN_IF = "enp1s0f0np0"


def _load_matrix_table():
    spec = importlib.util.spec_from_file_location("ask2_matrix_table", ROOT / "bin/ask2-matrix-table.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FAKE_SSH_PY = r'''
import json, os, re, sys
from pathlib import Path

args = sys.argv[1:]
# Strip ssh flags (-n, -o ..., -i ...)
i = 0
while i < len(args):
    if args[i] == "-n":
        i += 1
    elif args[i] in ("-o", "-i"):
        i += 2
    else:
        break
host = args[i]
cmd = " ".join(args[i + 1 :])

state_path = Path(os.environ["FAKE_STATE"])
calls_path = Path(os.environ["CALLS"])
state = json.loads(state_path.read_text())

with calls_path.open("a") as fh:
    fh.write(f"{host}: {cmd}\n")

def save():
    state_path.write_text(json.dumps(state))

if host == "test@dut":
    if "ip addr flush" in cmd or "ip link del eth3.10" in cmd or "ip link del eth4.20" in cmd:
        sys.stderr.write("FORBIDDEN destructive DUT command\n")
        sys.exit(99)
    if "cat /proc/sys/kernel/random/boot_id" in cmd:
        print(state["boot"])
        sys.exit(0)
    if "test -e /sys/module/ask/parameters/bridge_offload" in cmd:
        sys.exit(0 if state.get("has_bridge_offload", True) else 1)
    if "cat /sys/module/ask/parameters/bridge_offload" in cmd:
        print("Y" if state.get("bridge_offload", 1) else "N")
        sys.exit(0)
    if "tee /sys/module/ask/parameters/bridge_offload" in cmd:
        m = re.search(r"echo ([01])", cmd)
        if m:
            state["bridge_offload"] = int(m.group(1))
            save()
        sys.exit(0)
    if "cat /sys/kernel/debug/fman_pcd/0/fe_arm" in cmd:
        faulted = state.get("faulted", "(none)")
        staging = state.get("staging", "no")
        armed = state.get("armed", "0x09 0x0d 0x10 0x11")
        print("fe_pool engaged:  YES")
        print(f"Armed ports: {armed}")
        print(f"Faulted ports: {faulted}")
        print(f"FE fault/staging: {staging}")
        sys.exit(0)
    if "grep -c 'keysz=16 '" in cmd:
        print(state.get("bridge_records", 0))
        sys.exit(0)
    if "awk '/keysz=16 /" in cmd:
        print(state.get("bridge_hits", 0))
        sys.exit(0)
    if "grep '^cpu ' /proc/stat" in cmd:
        tick = state.get("cpu_tick", 0)
        state["cpu_tick"] = tick + 1
        save()
        # ~2% busy between consecutive calls
        print(f"cpu  {tick * 2} 0 0 {1000 + tick * 98} 0 0 0 0 0 0")
        sys.exit(0)
    if "journalctl -k -b" in cmd:
        print(state.get("kernel_errors", 0))
        sys.exit(0)
    if "pcd-snapshot capture" in cmd:
        state["pcd_captured"] = True
        save()
        sys.exit(0)
    if "pcd-snapshot diff" in cmd:
        if state.get("fail_pcd_diff"):
            print("MISMATCH in MURAM", file=sys.stderr)
            sys.exit(1)
        print("pcd-snapshot diff: OK (identical)")
        sys.exit(0)
    if "test -x /usr/local/bin/pcd-snapshot" in cmd:
        sys.exit(0)
    if "cat /sys/class/net/eth3/mtu /sys/class/net/eth4/mtu" in cmd:
        print(state["mtu"]["eth3"])
        print(state["mtu"]["eth4"])
        sys.exit(0)
    if '/sys/class/net/$i/master' in cmd:
        m3 = state["masters"].get("eth3")
        m4 = state["masters"].get("eth4")
        print(m3 or "-")
        print(m4 or "-")
        sys.exit(0)
    if "/sys/class/net/eth3/master" in cmd or "/sys/class/net/eth4/master" in cmd:
        if "readlink" in cmd:
            m = re.search(r"/sys/class/net/(eth[34])/master", cmd)
            master = state["masters"].get(m.group(1)) if m else None
            if master:
                print(master)
                sys.exit(0)
            sys.exit(1)
        m3 = state["masters"].get("eth3")
        m4 = state["masters"].get("eth4")
        print(f"{m3 or '-'} {m4 or '-'}")
        sys.exit(0)
    if "ip link add " in cmd and " type bridge" in cmd:
        m = re.search(r"ip link add (\S+) type bridge", cmd)
        br = m.group(1)
        state["bridges"].append(br)
        if "eth3 master" in cmd:
            state["masters"]["eth3"] = br
        if "eth4 master" in cmd:
            state["masters"]["eth4"] = br
        save()
        sys.exit(0)
    if "nomaster" in cmd:
        if state.get("fail_nomaster"):
            sys.exit(1)
        if "for p in eth3 eth4" in cmd or "eth3 nomaster" in cmd:
            state["masters"]["eth3"] = None
        if "for p in eth3 eth4" in cmd or "eth4 nomaster" in cmd:
            state["masters"]["eth4"] = None
        m = re.search(r'ip link del "([^"]+)"', cmd) or re.search(r"ip link del (\S+)", cmd)
        if m and m.group(1) in state["bridges"]:
            state["bridges"].remove(m.group(1))
        state["bridge_records"] = 0
        save()
        sys.exit(0)
    if "ip link show dev " in cmd:
        m = re.search(r"ip link show dev (\S+)", cmd)
        sys.exit(0 if m and m.group(1) in state["bridges"] else 1)
    sys.exit(0)

if host in ("test@gen1", "test@gen2"):
    g = "g1" if host == "test@gen1" else "g2"
    if f"cat /sys/class/net/{os.environ['GEN_IF']}/mtu" in cmd:
        print(state["gen_mtu"][g])
        sys.exit(0)
    if "ip -o addr show dev" in cmd:
        for addr in state["gen_addrs"][g]:
            print(addr)
        sys.exit(0)
    if "ip addr add " in cmd or "ip -6 addr add " in cmd:
        for tok in re.findall(r"addr add (\S+)", cmd):
            if tok not in state["gen_addrs"][g]:
                state["gen_addrs"][g].append(tok)
        save()
        sys.exit(0)
    if "ip addr del " in cmd or "ip -6 addr del " in cmd:
        for tok in re.findall(r"addr del (\S+)", cmd):
            if tok in state["gen_addrs"][g]:
                state["gen_addrs"][g].remove(tok)
        save()
        sys.exit(0)
    if "ss -H -ltn" in cmd:
        if state.get("port_busy"):
            print("LISTEN 0 128 0.0.0.0:5277 0.0.0.0:*")
        sys.exit(0)
    if "pkill -f" in cmd:
        state["iperf_srv"] = False
        save()
        sys.exit(0)
    if "iperf3 -s -p 5277" in cmd:
        state["iperf_srv"] = True
        save()
        sys.exit(0)
    if "ping " in cmd:
        sys.exit(0)
    if "iperf3 -c " in cmd:
        mtu = state["gen_mtu"][g]
        bidir = "--bidir" in cmd
        bps = (9.89e9 if mtu == 9000 else 9.35e9)
        intervals = []
        for s in range(0, 6):
            iv = {"sum": {"start": float(s), "end": float(s + 1), "bits_per_second": bps}}
            if bidir:
                iv["sum_bidir_reverse"] = {"start": float(s), "end": float(s + 1), "bits_per_second": bps * 0.94}
            intervals.append(iv)
        state["bridge_hits"] = state.get("bridge_hits", 0) + 100000
        save()
        print(json.dumps({"intervals": intervals}))
        sys.exit(0)
    sys.exit(0)

sys.stderr.write(f"unexpected host {host}\n")
sys.exit(1)
'''


def _baseline():
    return {
        "boot": "020b5ec3-7705-49a0-859a-db4d0fac67af",
        "has_bridge_offload": True,
        "bridge_offload": 1,
        "armed": "0x09 0x0d 0x10 0x11",
        "faulted": "(none)",
        "staging": "no",
        "mtu": {"eth3": 1500, "eth4": 1500},
        "masters": {"eth3": None, "eth4": None},
        "bridges": [],
        "bridge_records": 0,
        "bridge_hits": 0,
        "kernel_errors": 0,
        "gen_mtu": {"g1": 1500, "g2": 1500},
        "gen_addrs": {"g1": [], "g2": []},
        "iperf_srv": False,
    }


@pytest.fixture
def rig(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shutil.copy(ROOT / "bin/testrig-env.sh", bindir / "testrig-env.sh")
    source = (ROOT / "bin/testrig-ask2-bridge.sh").read_text().replace(
        "/tmp/testrig-dut2-battery.lock", str(tmp_path / "dut2-battery.lock"),
    )
    runner = bindir / "testrig-ask2-bridge.sh"
    runner.write_text(source)
    runner.chmod(0o755)

    # Fake testrig-ask2-9k.sh to verify jumbo fencing order & state transitions
    fake9k = bindir / "testrig-ask2-9k.sh"
    fake9k.write_text(
        f'#!{sys.executable}\n'
        'import json, os, sys\n'
        'from pathlib import Path\n'
        'mode = sys.argv[1]\n'
        'st_p = Path(os.environ["FAKE_STATE"])\n'
        'st = json.loads(st_p.read_text())\n'
        'with Path(os.environ["CALLS"]).open("a") as fh:\n'
        '    fh.write(f"9k:{mode} masters={st[\'masters\']} bridges={st[\'bridges\']}\\n")\n'
        'if mode in ("up", "down"):\n'
        '    assert st["masters"]["eth3"] is None and st["masters"]["eth4"] is None, "bridge still active during 9k transition!"\n'
        '    m = 9000 if mode == "up" else 1500\n'
        '    st["mtu"] = {"eth3": m, "eth4": m}\n'
        '    st["gen_mtu"] = {"g1": m, "g2": m}\n'
        '    st_p.write_text(json.dumps(st))\n'
    )
    fake9k.chmod(0o755)

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    fake_ssh_script = fakebin / "fake_ssh.py"
    fake_ssh_script.write_text(FAKE_SSH_PY)
    ssh = fakebin / "ssh"
    ssh.write_text(f'#!/bin/sh\nexec {sys.executable} {fake_ssh_script} "$@"\n')
    ssh.chmod(0o755)
    sleep = fakebin / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    pgrep = fakebin / "pgrep"
    pgrep.write_text("#!/bin/sh\nexit 1\n")
    pgrep.chmod(0o755)

    state = tmp_path / "state.json"
    state.write_text(json.dumps(_baseline()))
    calls = tmp_path / "calls"
    rig_out = tmp_path / "rig-out"
    rig_out.mkdir()
    outdir = tmp_path / "results"

    env = os.environ | {
        "PATH": f"{fakebin}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "XDG_STATE_HOME": str(tmp_path / "xdg"),
        "TESTRIG_ENV": str(tmp_path / "no-rig-config"),
        "RIG_OUT": str(rig_out),
        "ASK2_DUT": "test@dut",
        "GEN1": "test@gen1",
        "GEN2": "test@gen2",
        "GEN_IF": GEN_IF,
        "RIG_KEY": "",
        "RIG_PARITY": "0",
        "OUTDIR": str(outdir),
        "REPS": "1",
        "DUR": "5",
        "STEADY_FROM": "1",
        "BIDIR_DUR": "5",
        "BIDIR_STEADY_FROM": "1",
        "FAKE_STATE": str(state),
        "CALLS": str(calls),
    }

    def run(*args, **extra):
        return subprocess.run(
            ["bash", str(runner), *args],
            env=env | extra,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def current():
        return json.loads(state.read_text())

    def log():
        return calls.read_text().splitlines() if calls.exists() else []

    def lease():
        return tmp_path / "xdg" / "testrig-ask2-bridge" / "lease"

    return run, log, current, state, lease, outdir


@pytest.mark.parametrize("args", [(), ("bogus",), ("run", "extra")])
def test_missing_or_invalid_mode_refuses_before_remote_access_or_output(rig, args):
    run, log, _, _, lease, outdir = rig
    res = run(*args)
    assert res.returncode == 2
    assert "usage:" in res.stderr.lower()
    assert not log()
    assert not outdir.exists()
    assert not lease().exists()


@pytest.mark.parametrize("extra", [
    {"MTUS": "1500 4000"},
    {"BRIDGE_OFFLOAD": "2"},
    {"DUR": "3", "STEADY_FROM": "3"},
    {"OUTDIR": ""},
])
def test_invalid_parameters_refuse_before_ssh(rig, extra):
    run, log, _, _, lease, _ = rig
    res = run("run", **extra)
    assert res.returncode == 2
    assert not log()
    assert not lease().exists()


@pytest.mark.parametrize("mode", ["preflight", "status"])
def test_inspection_modes_are_read_only(rig, mode):
    run, _, current, _, lease, outdir = rig
    before = current()
    res = run(mode, MTUS="1500")
    assert res.returncode == 0, res.stderr
    assert current() == before
    assert not lease().exists()
    assert not outdir.exists()


def test_preflight_refuses_existing_lease_and_preserves_it(rig):
    run, _, _, _, lease, _ = rig
    lease().parent.mkdir(parents=True)
    lease().write_text("TOKEN=deadbeef\n")
    res = run("preflight", MTUS="1500")
    assert res.returncode != 0
    assert "lease" in res.stderr.lower()
    assert lease().read_text() == "TOKEN=deadbeef\n"


def test_preflight_refuses_premastered_port(rig):
    run, _, current, state, _, _ = rig
    st = current()
    st["masters"]["eth3"] = "br0"
    state.write_text(json.dumps(st))
    res = run("preflight", MTUS="1500")
    assert res.returncode != 0
    assert "master" in res.stderr.lower()


def test_run_1500_only_emits_matrix_csv_and_restores_without_destructive_dut_ops(rig):
    run, log, current, _, lease, outdir = rig
    res = run("run", MTUS="1500")
    assert res.returncode == 0, res.stdout + res.stderr
    assert not lease().exists()
    st = current()
    assert st["masters"] == {"eth3": None, "eth4": None}
    assert st["bridges"] == []
    assert st["gen_addrs"] == {"g1": [], "g2": []}
    assert st["iperf_srv"] is False
    assert st["pcd_captured"] is True
    assert any("pcd-snapshot diff" in line for line in log())
    assert not any("9k:" in line for line in log())

    csv_file = outdir / "bridge-1500.csv"
    assert csv_file.exists()
    table = _load_matrix_table().collect(outdir)
    for fam in ("4", "6"):
        for d in ("up", "down", "bidir"):
            val, count, verdict, _ = table[("bridge", fam, d)]
            assert count == 1 and val is not None and val > 9.0
            assert verdict == "HW"


def test_run_1500_and_9000_fences_jumbo_via_ask2_9k_and_emits_both_rows(rig):
    run, log, current, _, lease, outdir = rig
    res = run("run", MTUS="1500 9000")
    assert res.returncode == 0, res.stdout + res.stderr
    assert not lease().exists()
    st = current()
    assert st["mtu"] == {"eth3": 1500, "eth4": 1500}
    assert st["gen_mtu"] == {"g1": 1500, "g2": 1500}
    assert st["masters"] == {"eth3": None, "eth4": None}
    assert st["bridges"] == []

    lines = log()
    assert any(l.startswith("9k:preflight") for l in lines)
    assert any(l.startswith("9k:up") for l in lines)
    assert any(l.startswith("9k:down") for l in lines)

    table = _load_matrix_table().collect(outdir)
    for row in ("bridge", "bridge-9k"):
        for fam in ("4", "6"):
            for d in ("up", "down", "bidir"):
                val, count, verdict, _ = table[(row, fam, d)]
                assert count == 1 and val is not None and val > 9.0
                assert verdict == "HW"


def test_cleanup_retains_lease_when_pcd_snapshot_diff_fails(rig):
    run, _, current, state, lease, _ = rig
    st = current()
    st["fail_pcd_diff"] = True
    state.write_text(json.dumps(st))
    res = run("run", MTUS="1500")
    assert res.returncode != 0
    assert lease().exists(), "lease must be retained when teardown verification fails"
