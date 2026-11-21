"""Host regressions for the owned ASK2 MTU-9000 runner (bin/testrig-ask2-9k.sh)."""

import fcntl
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


@pytest.mark.parametrize("mode", ["up", "down", "run"])
@pytest.mark.parametrize("parity", ["0", "1"])
def test_jumbo_transition_refuses_before_remote_access_or_lease_changes(tmp_path, mode, parity):
    calls = tmp_path / "ssh-calls"
    ssh = tmp_path / "ssh"
    ssh.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\nexit 1\n')
    ssh.chmod(0o755)
    runner_dir = tmp_path / "runner"
    runner_dir.mkdir()
    shutil.copy(ROOT / "bin/testrig-env.sh", runner_dir)
    runner = runner_dir / "testrig-ask2-9k.sh"
    runner.write_text((ROOT / "bin" / runner.name).read_text().replace(
        "/tmp/testrig-dut2-battery.lock", str(tmp_path / "dut2-battery.lock"),
    ))
    lease = tmp_path / "state" / "testrig-ask2-jumbo" / "lease"
    lease_content = (
        "TOKEN=0123456789abcdef\n"
        "BOOT=020b5ec3-7705-49a0-859a-db4d0fac67af\n"
        "GID0='4 4 3c:fd:fe:00:00:01'\n"
        "GID1='9 4 3c:fd:fe:00:00:01'\n"
        "GID2='4 4 3c:fd:fe:00:00:02'\n"
        "GID3='9 4 3c:fd:fe:00:00:02'\n"
    )
    if mode == "down":
        lease.parent.mkdir(parents=True)
        lease.write_text(lease_content)
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "TESTRIG_ENV": str(tmp_path / "no-rig-config"),
        "ASK2_DUT": "test@dut",
        "GEN1": "test@gen1",
        "GEN2": "test@gen2",
        "GEN_IF": GEN_IF,
        "RIG_KEY": "",
        "RIG_PARITY": parity,
        "RIG_OUT": str(tmp_path / "rig"),
        "OUTDIR": str(tmp_path / "results"),
        "CALLS": str(calls),
    }
    result = subprocess.run(
        ["bash", str(runner), mode],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2, result.stderr
    assert "disabled" in result.stderr.lower()
    assert not calls.exists(), calls.read_text()
    assert not (tmp_path / "results").exists()
    if mode == "down":
        assert lease.read_text() == lease_content
    else:
        assert not lease.exists()


@pytest.mark.parametrize("args", [(), ("unicast-9k",)])
def test_paired_jumbo_run_refuses_before_remote_access_or_output(tmp_path, args):
    calls = tmp_path / "ssh-calls"
    ssh = tmp_path / "ssh"
    ssh.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\nexit 1\n')
    ssh.chmod(0o755)
    lease = tmp_path / "state" / "testrig-ask2-jumbo" / "lease"
    lease.parent.mkdir(parents=True)
    lease.write_text("foreign lease\n")
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "TESTRIG_ENV": str(tmp_path / "no-rig-config"),
        "ASK2_DUT": "",
        "RIG_OUT": str(tmp_path / "rig"),
        "RUN_DIR": str(tmp_path / "results"),
        "CALLS": str(calls),
    }
    result = subprocess.run(
        ["bash", str(ROOT / "bin/testrig-paired-jumbo.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2, result.stderr
    assert "disabled" in result.stderr.lower()
    assert not calls.exists(), calls.read_text()
    assert not (tmp_path / "results").exists()
    assert lease.read_text() == "foreign lease\n"


def test_paired_jumbo_preflight_reaches_configuration_validation(tmp_path):
    env = os.environ | {
        "HOME": str(tmp_path),
        "TESTRIG_ENV": str(tmp_path / "no-rig-config"),
        "ASK2_DUT": "",
        "DUR": "32",
        "STEADY_FROM": "10",
        "MEASURE_TO": "30",
        "STREAMS": "8",
        "REPS": "3",
        "PARITY_CC": "bbr",
        "SAMPLE": "0",
        "DUT_TRACE": "0",
        "RUN_ON": "agent",
        "BIDIR_DUR": "32",
        "BIDIR_STEADY_FROM": "10",
        "WG_REPS": "3",
        "IPERF_ARGS": "-C bbr",
    }
    result = subprocess.run(
        ["bash", str(ROOT / "bin/testrig-paired-jumbo.sh"), "--preflight"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "ASK2_DUT" in result.stderr
    assert "disabled" not in result.stderr.lower()


def baseline():
    def link(index, parent, mac):
        return {"index": index, "link": parent, "mac": mac, "mtu": 1500, "owner": None}

    gen = {}
    for host, vid, last in (("g1", 10, "01"), ("g2", 20, "02")):
        gen[host] = {GEN_IF: link(4, 4, f"3c:fd:fe:00:00:{last}"),
                     f"{GEN_IF}.{vid}": link(9, 4, f"3c:fd:fe:00:00:{last}")}
    return {
        "boot": "020b5ec3-7705-49a0-859a-db4d0fac67af",
        "offload": {f"{e} {f}": True for e in ("eth3", "eth4") for f in ("ipv4", "ipv6")},
        "offload_active": True,
        "cfg": {"eth3": None, "eth4": None, "eth3.10": None, "eth4.20": None},
        "mtu": {"eth3": 1500, "eth4": 1500, "eth3.10": 1500, "eth4.20": 1500},
        "gen": gen,
    }


@pytest.fixture
def rig(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("testrig-env.sh", "testrig-vendor-mtu.sh"):
        shutil.copy(ROOT / "bin" / name, bindir / name)
    source = (ROOT / "bin/testrig-ask2-9k.sh").read_text().replace(
        "/tmp/testrig-dut2-battery.lock", str(tmp_path / "dut2-battery.lock"),
    )
    guarded = bindir / "testrig-ask2-9k-guarded.sh"
    guarded.write_text(source)
    runner = bindir / "testrig-ask2-9k.sh"
    # Exercise candidate lifecycle code only against fake SSH; the shipped guard stays enabled.
    gate = 'lifecycle_gate "$MODE"'
    assert source.count(gate) == 1
    runner.write_text(source.replace(gate, ":", 1))
    runner.chmod(0o755)
    guarded.chmod(0o755)
    quick = bindir / "testrig-offload-quick.sh"
    quick.write_text(
        '#!/bin/sh\nprintf "quick %s\\n" "$BIDIR" >> "$CALLS"\n'
        'if [ "${CAPTURE_CELLS:-0}" = 1 ]; then printf "cells %s %s\\n" "$BIDIR" "$*" >> "$CALLS"; fi\n'
    )
    quick.chmod(0o755)
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    ssh = fakebin / "ssh"
    ssh.write_text(f'#!/bin/sh\nexec {sys.executable} {ROOT / "tests" / "_testrig_ask2_9k_fake_ssh.py"} "$@"\n')
    ssh.chmod(0o755)
    sleep = fakebin / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    pgrep = fakebin / "pgrep"
    pgrep.write_text("#!/bin/sh\nexit 1\n")
    pgrep.chmod(0o755)
    state = tmp_path / "state.json"
    state.write_text(json.dumps(baseline()))
    calls = tmp_path / "calls"
    rig_out = tmp_path / "rig-out"
    rig_out.mkdir()
    env = os.environ | {
        "PATH": f"{fakebin}:{os.environ['PATH']}",
        "TESTRIG_ENV": str(tmp_path / "no-rig-config"),
        "XDG_STATE_HOME": str(tmp_path / "xdg"),
        "RIG_OUT": str(rig_out),
        "TEST_DUT2_LOCK": str(tmp_path / "dut2-battery.lock"),
        "ASK2_DUT": "test@dut", "GEN1": "test@gen1", "GEN2": "test@gen2",
        "GEN_IF": GEN_IF, "RIG_KEY": "", "RIG_PARITY": "0",
        "FAKE_STATE": str(state), "CALLS": str(calls), "REQUIRE_DISARMED": "1",
    }

    def run(mode, *, inherited_locks=False, guarded_mode=False, **extra):
        command = ["bash", str(guarded if guarded_mode else runner), mode]
        if inherited_locks:
            command = [
                "bash", "-c",
                'exec 8>>"$RIG_OUT/parity-bidir.lock"; flock -n 8; '
                'exec 9>>"$TEST_DUT2_LOCK"; flock -n 9; '
                '"$@"; status=$?; exit "$status"',
                "--", *command,
            ]
        return subprocess.run(command, env=env | extra, capture_output=True,
                              text=True, timeout=60)

    def log():
        return calls.read_text().splitlines() if calls.exists() else []

    def current():
        return json.loads(state.read_text())

    def lease():
        return tmp_path / "xdg" / "testrig-ask2-jumbo" / "lease"

    return run, log, current, state, lease


def assert_restored(current):
    assert current["mtu"] == {"eth3": 1500, "eth4": 1500, "eth3.10": 1500, "eth4.20": 1500}
    assert set(current["cfg"].values()) == {None}
    assert current["offload_active"]
    for links in current["gen"].values():
        for link in links.values():
            assert link["mtu"] == 1500 and link["owner"] is None


@pytest.mark.parametrize("mode", ["preflight", "status"])
def test_inspection_modes_remain_read_only(rig, mode):
    run, _, current, _, lease = rig
    before = current()
    result = run(mode, guarded_mode=True)
    assert result.returncode == 0, result.stderr
    assert current() == before
    assert not lease().exists()


def test_preflight_preserves_lease_and_reports_manual_recovery(rig):
    run, _, current, _, lease = rig
    before = current()
    lease().parent.mkdir(parents=True)
    lease().write_text("# existing owner\n")
    result = run("preflight", guarded_mode=True)
    assert result.returncode != 0
    assert "manual recovery" in result.stderr
    assert lease().read_text() == "# existing owner\n"
    assert current() == before


def test_up_down_round_trip_orders_disengage_and_restores(rig):
    run, log, current, _, lease = rig
    up = run("up")
    assert up.returncode == 0, up.stderr
    state = current()
    assert state["mtu"] == {"eth3": 9000, "eth4": 9000, "eth3.10": 9000, "eth4.20": 9000}
    assert state["offload_active"] and lease().exists()
    calls = log()
    assert not any("VIOLATION" in c for c in calls)
    first_off = calls.index("dut delete eth3 offload ipv4")
    first_mtu = calls.index("dut set eth3 mtu 9000")
    first_gen = next(i for i, c in enumerate(calls) if c.endswith("set enp1s0f0np0 1500->9000"))
    rearm = calls.index("dut set eth3 offload ipv4")
    assert first_off < first_mtu < first_gen < rearm < calls.index("g1 ping")

    down = run("down")
    assert down.returncode == 0, down.stderr
    assert_restored(current())
    assert not lease().exists()
    assert not any("VIOLATION" in c for c in log())


@pytest.mark.parametrize("conflict", ["gen_owned", "dut_mtu"])
def test_preflight_conflict_changes_nothing(rig, conflict):
    run, log, current, state_path, lease = rig
    state = current()
    if conflict == "gen_owned":
        state["gen"]["g2"][f"{GEN_IF}.20"]["owner"] = "t2mtu-0123456789abcdef-x"
    else:
        state["cfg"]["eth4"] = "9000"
        state["mtu"]["eth4"] = 9000
    state_path.write_text(json.dumps(state))
    result = run("up")
    assert result.returncode != 0
    assert "CONFLICT" in result.stderr
    assert log() == []
    assert current() == state and not lease().exists()


def test_run_restores_after_failed_df_probe(rig, tmp_path):
    run, log, current, _, lease = rig
    result = run("run", OUTDIR=str(tmp_path / "out"), FAIL_PING="1", REPS="1")
    assert result.returncode != 0
    assert "MTU 9000 frames" in result.stderr
    assert not any(c.startswith("quick") for c in log())
    assert_restored(current())
    assert not lease().exists()


def test_df_probes_use_the_requested_paired_endpoints():
    script = (ROOT / "bin/testrig-ask2-9k.sh").read_text()
    probe = script[script.index("\ndf_probe(){"):script.index("\nup(){")]
    stubs = 'on(){ printf "%s\\n" "$*"; }\nMTU=9000\n'
    env = os.environ | {
        "QUICK_G1_IPV4": "192.0.2.1", "QUICK_G2_IPV4": "192.0.2.2",
        "QUICK_G1_IPV6": "2001:db8:1::1", "QUICK_G2_IPV6": "2001:db8:2::2",
        "QUICK_G1_VLAN4": "192.0.2.10", "QUICK_G2_VLAN4": "192.0.2.20",
        "QUICK_G1_VLAN6": "2001:db8:10::1", "QUICK_G2_VLAN6": "2001:db8:20::2",
    }
    result = subprocess.run(["bash", "-eu", "-c", stubs + probe + "\ndf_probe"],
                            env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    for pair in (
        "-I 192.0.2.1 192.0.2.2", "-I 192.0.2.2 192.0.2.1",
        "-I 192.0.2.10 192.0.2.20", "-I 192.0.2.10 192.0.2.2",
        "-I 2001:db8:1::1 2001:db8:2::2",
        "-I 2001:db8:10::1 2001:db8:20::2",
        "-I 2001:db8:10::1 2001:db8:2::2",
    ):
        assert pair in result.stdout


def test_run_executes_cells_between_up_and_down(rig, tmp_path):
    run, log, current, _, lease = rig
    result = run("run", OUTDIR=str(tmp_path / "out"), REPS="1")
    assert result.returncode == 0, result.stderr
    calls = log()
    assert [c for c in calls if c.startswith("quick")] == ["quick 0", "quick 1"]
    assert calls.index("quick 0") > calls.index("g1 ping")
    assert calls.index("quick 1") < calls.index("dut delete eth3 mtu")
    assert_restored(current())
    assert not lease().exists()


@pytest.mark.parametrize(("row", "unidir", "bidir"), [
    ("unicast-9k", "unicast-v4 unicast-v6 unicast-v4-rev unicast-v6-rev",
     "unicast-v4 unicast-v6"),
    ("nat-9k", "nat-v4 nat-v6 nat-v4-rev nat-v6-rev", "nat-v4 nat-v6"),
    ("vlan-9k", "vlan-v4 vlan-v6 vlan-v4-rev vlan-v6-rev", "vlan-v4 vlan-v6"),
    ("vlan-port-9k", "vlanport-v4 vlanport-v6 portvlan-v4 portvlan-v6",
     "vlanport-v4 vlanport-v6"),
])
def test_run_selects_only_requested_jumbo_row(rig, tmp_path, row, unidir, bidir):
    run, log, current, _, lease = rig
    result = run("run", OUTDIR=str(tmp_path / "out"), REPS="1",
                 RIG_JUMBO_ROW=row, CAPTURE_CELLS="1")
    assert result.returncode == 0, result.stderr
    assert [line for line in log() if line.startswith("cells ")] == [
        f"cells 0 {unidir}", f"cells 1 {bidir}"]
    assert_restored(current())
    assert not lease().exists()


@pytest.mark.parametrize("extra", [
    {"RIG_JUMBO_ROW": "bogus"},
    {"RIG_JUMBO_ROW": "unicast-9k", "CELLS_UNI": "nat-v4"},
    {"RIG_JUMBO_ROW": "unicast-9k", "CELLS_BI": "nat-v4"},
])
def test_invalid_row_selector_fails_before_changes(rig, tmp_path, extra):
    run, log, current, _, lease = rig
    before = current()
    result = run("run", OUTDIR=str(tmp_path / "out"), **extra)
    assert result.returncode != 0
    assert "RIG_JUMBO_ROW" in result.stderr
    assert log() == [] and current() == before and not lease().exists()


def test_down_refuses_foreign_dut_value_and_keeps_lease(rig):
    run, log, current, state_path, lease = rig
    assert run("up").returncode == 0
    state = current()
    state["cfg"]["eth4"] = "1600"
    state_path.write_text(json.dumps(state))
    before = len(log())
    result = run("down")
    assert result.returncode != 0
    assert "foreign" in result.stderr
    assert lease().exists()
    assert log()[before:] == []


def test_down_refuses_reboot_and_preserves_foreign_configuration(rig):
    run, log, current, state_path, lease = rig
    assert run("up").returncode == 0
    state = current()
    state["boot"] = "120b5ec3-7705-49a0-859a-db4d0fac67af"
    state["cfg"]["eth4"] = "1600"
    state["mtu"]["eth4"] = 1600
    state_path.write_text(json.dumps(state))
    before = len(log())
    result = run("down")
    assert result.returncode != 0
    assert "reboot" in result.stderr.lower()
    assert current() == state and lease().exists()
    assert log()[before:] == []


def test_down_refuses_failed_config_query_without_mutating(rig):
    run, log, current, _, lease = rig
    assert run("up").returncode == 0
    state = current()
    before = len(log())
    result = run("down", FAIL_CONFIG_QUERY="1")
    assert result.returncode != 0
    assert lease().exists()
    assert current() == state and log()[before:] == []


def test_down_refuses_foreign_offload_change(rig):
    run, log, current, state_path, lease = rig
    assert run("up").returncode == 0
    state = current()
    state["offload"]["eth3 ipv4"] = False
    state["offload_active"] = False
    state_path.write_text(json.dumps(state))
    before = len(log())
    result = run("down")
    assert result.returncode != 0
    assert "offload" in result.stderr.lower()
    assert current() == state and lease().exists()
    assert log()[before:] == []


@pytest.mark.parametrize("lockname", ["parity", "dut2"])
def test_up_refuses_a_competing_battery_lock(rig, tmp_path, lockname):
    run, log, current, _, lease = rig
    before = current()
    lockpath = (tmp_path / "rig-out/parity-bidir.lock" if lockname == "parity"
                else tmp_path / "dut2-battery.lock")
    with lockpath.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run("up")
    assert result.returncode != 0
    assert "lock" in result.stderr.lower() or "battery" in result.stderr.lower()
    assert current() == before and not lease().exists() and log() == []


def test_up_accepts_inherited_paired_battery_locks(rig):
    run, _, current, _, lease = rig
    result = run("up", inherited_locks=True)
    assert result.returncode == 0, result.stderr
    assert lease().exists()
    result = run("down")
    assert result.returncode == 0, result.stderr
    assert_restored(current())


def test_explicit_owner_token_guards_jumbo_cleanup(rig):
    run, log, current, _, lease = rig
    token = "0123456789abcdef"
    result = run("up", RIG_ASK2_JUMBO_TOKEN=token)
    assert result.returncode == 0, result.stderr
    assert f"TOKEN={token}\n" in lease().read_text()
    before = len(log())
    state = current()
    result = run("down", RIG_ASK2_JUMBO_TOKEN="fedcba9876543210")
    assert result.returncode != 0 and lease().exists()
    assert log()[before:] == [] and current() == state
    result = run("down", RIG_ASK2_JUMBO_TOKEN=token)
    assert result.returncode == 0, result.stderr
    assert_restored(current())


def test_invalid_explicit_owner_token_fails_before_changes(rig):
    run, log, current, _, lease = rig
    before = current()
    result = run("up", RIG_ASK2_JUMBO_TOKEN="not-a-token")
    assert result.returncode != 0
    assert log() == [] and current() == before and not lease().exists()


def test_down_refuses_a_symlinked_lease(rig, tmp_path):
    run, log, current, _, lease = rig
    assert run("up").returncode == 0
    target = tmp_path / "foreign-lease"
    lease().rename(target)
    lease().symlink_to(target)
    before = len(log())
    state = current()
    result = run("down")
    assert result.returncode != 0
    assert lease().is_symlink() and target.exists()
    assert current() == state and log()[before:] == []


def test_up_refuses_dangling_lease_symlink(rig, tmp_path):
    run, log, current, _, lease = rig
    lease().parent.mkdir(parents=True)
    lease().symlink_to(tmp_path / "missing-owner")
    before = current()
    result = run("up")
    assert result.returncode != 0
    assert lease().is_symlink() and current() == before and log() == []


def test_up_refuses_config_changed_during_generator_claims(rig):
    run, log, current, _, lease = rig
    result = run("up", CHANGE_CONFIG_ON_CLAIM="1")
    assert result.returncode != 0
    assert lease().exists()
    assert current()["cfg"]["eth4"] == "1600"
    assert not any(line.startswith("dut ") for line in log())


def test_down_refuses_foreign_generator_before_changing_dut(rig):
    run, log, current, state_path, lease = rig
    assert run("up").returncode == 0
    state = current()
    state["gen"]["g2"][GEN_IF]["owner"] = "t2mtu-0123456789abcdef-x"
    state_path.write_text(json.dumps(state))
    before = len(log())
    result = run("down")
    assert result.returncode != 0
    assert lease().exists() and current() == state
    assert log()[before:] == []


def test_down_releases_partial_claim_without_touching_dut(rig):
    run, log, current, _, lease = rig
    result = run("up", FAIL_CLAIM_ON_VLAN="1")
    assert result.returncode != 0 and lease().exists()
    before = len(log())
    result = run("down")
    assert result.returncode == 0, result.stderr
    assert not lease().exists()
    assert_restored(current())
    assert not any(line.startswith("dut ") for line in log()[before:])


def test_down_preserves_new_config_changed_during_restoration(rig):
    run, log, current, _, lease = rig
    assert run("up").returncode == 0
    before = len(log())
    result = run("down", CHANGE_CONFIG_ON_RESTORE="1")
    assert result.returncode != 0
    assert lease().exists() and current()["cfg"]["eth4"] == "1600"
    assert not any(line.startswith("dut delete eth4 mtu") for line in log()[before:])
