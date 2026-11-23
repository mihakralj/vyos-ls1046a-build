"""Host regressions for the WAN runner's resource ownership (bin/testrig-ask2-wan.sh, bin/testrig-wan-own.sh)."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
OWN = ROOT / "bin/testrig-wan-own.sh"
OWNER = "ask2wan-0123456789abcdef-v7"


def runner(tmp_path, *args, ssh_body="exit 1"):
    """Run the WAN runner with every ssh call logged and answered by ssh_body."""
    ssh = tmp_path / "ssh"
    ssh.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALLS"\n{ssh_body}\n')
    ssh.chmod(0o755)
    pgrep = tmp_path / "pgrep"
    pgrep.write_text("#!/bin/sh\nexit 1\n")
    pgrep.chmod(0o755)
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "TESTRIG_ENV": str(tmp_path / "no-rig-config"),
        "ASK2_DUT": "test@dut", "GEN1": "test@gen1", "GEN2": "test@gen2", "RIG_KEY": "",
        "OUTDIR": str(tmp_path / "results"),
        "WAN_LEASE_ROOT": str(tmp_path / "leases"),
        "CALLS": str(tmp_path / "calls"),
    }
    result = subprocess.run(["bash", str(ROOT / "bin/testrig-ask2-wan.sh"), *args],
                            env=env, capture_output=True, text=True, timeout=30)
    calls = (tmp_path / "calls").read_text() if (tmp_path / "calls").exists() else ""
    return result, calls


def test_preflight_conflict_changes_nothing(tmp_path):
    # the DUT already has a pppoe7 config node: refuse before any mutation
    result, calls = runner(tmp_path, "preflight", ssh_body=(
        'case "$*" in *"existsActive interfaces pppoe pppoe7"*) exit 0;; esac; exit 1'))
    assert result.returncode == 1, result.stderr
    assert "CONFLICT: DUT config node exists: interfaces pppoe pppoe7" in result.stderr
    assert "sudo sh -s" not in calls and "vbash" not in calls
    assert not list((tmp_path / "leases").glob("lease-*"))
    assert not (tmp_path / "results").exists()


@pytest.mark.parametrize("mode", ["run", "setup"])
def test_gated_run_does_not_touch_an_unreleased_lease(tmp_path, mode):
    stale = tmp_path / "leases/lease-0123456789abcdef"
    stale.mkdir(parents=True)
    (stale / "journal").write_text("g1 link enp1s0f0np0.7 ask2wan-0123456789abcdef-v7\n")
    result, calls = runner(tmp_path, mode)
    assert result.returncode == 2
    assert "WAN setup remains gated" in result.stderr
    assert calls == ""
    assert stale.exists()


def test_teardown_keeps_the_lease_when_ownership_is_unproven(tmp_path):
    lease = tmp_path / "leases/lease-0123456789abcdef"
    lease.mkdir(parents=True)
    (lease / "journal").write_text("g1 link enp1s0f0np0.7 ask2wan-0123456789abcdef-v7\n")
    result, calls = runner(tmp_path, "teardown", str(lease),
                           ssh_body='echo "ERROR: refusing" >&2; exit 1')
    assert result.returncode == 1
    assert "LEFT IN PLACE: g1 link" in result.stderr
    assert "link-delete enp1s0f0np0.7 ask2wan-0123456789abcdef-v7" in calls
    assert (lease / "journal").exists()


def test_teardown_reconciles_owned_ppp_service(tmp_path):
    lease = tmp_path / "leases/lease-0123456789abcdef"
    lease.mkdir(parents=True)
    run_dir = "/var/lib/ask2-wan-0123456789abcdef"
    (lease / "journal").write_text(
        f"g1 rundir {run_dir} ask2wan-0123456789abcdef-run\n"
        "g1 link enp1s0f0np0.7 ask2wan-0123456789abcdef-v7\n"
        "g1 procs ask2wan-0123456789abcdef.service "
        "11111111-1111-1111-1111-111111111111\n"
    )
    result, calls = runner(
        tmp_path, "teardown", str(lease),
        ssh_body="exit 0",
    )
    assert result.returncode == 0, result.stderr
    assert "service-stop ask2wan-0123456789abcdef.service" in calls
    assert not lease.exists()


def test_teardown_rejects_legacy_unidentified_ppp_process_entry(tmp_path):
    lease = tmp_path / "leases/lease-0123456789abcdef"
    lease.mkdir(parents=True)
    (lease / "journal").write_text(
        "g1 procs /var/lib/ask2-wan-0123456789abcdef/\n"
    )
    result, calls = runner(tmp_path, "teardown", str(lease), ssh_body="exit 0")
    assert result.returncode == 1
    assert "invalid WAN journal entry" in result.stderr
    assert calls == ""
    assert lease.exists()


def test_unreadable_teardown_journal_keeps_lease_without_ssh(tmp_path):
    lease = tmp_path / "leases/lease-0123456789abcdef"
    lease.mkdir(parents=True)
    (lease / "journal").write_text("g1 link enp1s0f0np0.7 ask2wan-0123456789abcdef-v7\n")
    tac = tmp_path / "tac"
    tac.write_text("#!/bin/sh\nexit 1\n")
    tac.chmod(0o755)
    result, calls = runner(tmp_path, "teardown", str(lease), ssh_body="exit 0")
    assert result.returncode == 1
    assert "cannot read WAN journal" in result.stderr
    assert calls == ""
    assert (lease / "journal").exists()


@pytest.mark.parametrize("invalid", [
    "g1 corrupted-process-entry /var/lib/ask2-wan-0123456789abcdef/",
    "unknown link enp1s0f0np0.7 ask2wan-0123456789abcdef-v7",
    "g1 link enp1s0f0np0.7 ask2wan-fedcba9876543210-v7",
    "g1 rule6 10957 wrong 157 fd99:57::1 fd99:2::/64",
    "g1 wgroute far 1 1 ::1 ::/0 wgw0 1420 158",
    "g2 route 10.0.0.0/24 10.1.1.1",
    "dut vyos ask2-wan-fedcba9876543210",
    "g1 link enp1s0f0np0.7 ask2wan-0123456789abcdef-v7 extra",
])
def test_invalid_journal_entry_prevents_any_cleanup(tmp_path, invalid):
    lease = tmp_path / "leases/lease-0123456789abcdef"
    lease.mkdir(parents=True)
    (lease / "journal").write_text(
        f"{invalid}\ng1 link enp1s0f0np0.7 ask2wan-0123456789abcdef-v7\n"
    )
    result, calls = runner(tmp_path, "teardown", str(lease), ssh_body="exit 0")
    assert result.returncode == 1
    assert "invalid WAN journal" in result.stderr
    assert calls == ""
    assert (lease / "journal").exists()


@pytest.mark.parametrize("entry", [
    "g1 rundir /var/lib/ask2-wan-0123456789abcdef ask2wan-0123456789abcdef-run",
    "g1 link enp1s0f0np0.7 ask2wan-0123456789abcdef-v7",
    "dut link wgw0 ask2wan-0123456789abcdef-wg",
    "dut nat 0123456789abcdef0123456789abcdef",
    "dut wgfp_lease 0123456789abcdef0123456789abcdef",
    "g2 route fd99:57::/64 fd99:2::185",
    "g1 rule6 10957 157 157 fd99:57::1 fd99:2::/64",
    "g1 wgroute far 10958 158 fd99:230::112 fd99:2::/64 wgw0 1420 158",
    "g1 procs ask2wan-0123456789abcdef.service "
    "11111111-1111-1111-1111-111111111111",
    "dut vyos ask2-wan-0123456789abcdef",
])
def test_valid_non_ppp_journal_entries_remain_recoverable(tmp_path, entry):
    lease = tmp_path / "leases/lease-0123456789abcdef"
    lease.mkdir(parents=True)
    (lease / "journal").write_text(f"{entry}\n")
    result, _ = runner(
        tmp_path, "teardown", str(lease),
        ssh_body='case "$*" in *"existsActive"*) exit 1;; esac; exit 0',
    )
    assert result.returncode == 0, result.stderr
    assert not lease.exists()


def test_unreachable_dut_does_not_authorize_dependent_cleanup(tmp_path):
    lease = tmp_path / "leases/lease-0123456789abcdef"
    lease.mkdir(parents=True)
    (lease / "journal").write_text(
        "g1 link enp1s0f0np0.7 ask2wan-0123456789abcdef-v7\n"
        "dut vyos ask2-wan-0123456789abcdef\n"
    )
    result, calls = runner(
        tmp_path, "teardown", str(lease),
        ssh_body='case "$*" in *"existsActive"*) exit 255;; esac; exit 0',
    )
    assert result.returncode == 1
    assert "LEFT IN PLACE: dut vyos" in result.stderr
    assert "link-delete" not in calls
    assert (lease / "journal").exists()


def test_failed_vyos_commit_does_not_authorize_dependent_cleanup(tmp_path):
    lease = tmp_path / "leases/lease-0123456789abcdef"
    lease.mkdir(parents=True)
    (lease / "journal").write_text(
        "g1 link enp1s0f0np0.7 ask2wan-0123456789abcdef-v7\n"
        "dut vyos ask2-wan-0123456789abcdef\n"
    )
    result, calls = runner(
        tmp_path, "teardown", str(lease),
        ssh_body=(
            'case "$*" in '
            '*"existsActive"*) exit 0;; '
            '*"returnActiveValue"*) '
            'if grep -q vbash "$CALLS"; then exit 1; fi; '
            'echo ask2-wan-0123456789abcdef; exit 0;; '
            '*"vbash"*) echo "connection refused" >&2; exit 255;; '
            'esac; exit 0'
        ),
    )
    assert result.returncode == 1
    assert "LEFT IN PLACE: dut vyos" in result.stderr
    assert "link-delete" not in calls
    assert (lease / "journal").exists()


def test_preflight_refuses_unreachable_dut_config(tmp_path):
    result, _ = runner(
        tmp_path, "preflight",
        ssh_body='case "$*" in *"existsActive"*) exit 255;; *"pgrep"*) exit 1;; esac; exit 0',
    )
    assert result.returncode == 1
    assert "cannot query DUT config" in result.stderr


def test_preflight_refuses_unreachable_generator(tmp_path):
    result, _ = runner(
        tmp_path, "preflight",
        ssh_body=(
            'case "$*" in *"existsActive"*) exit 1;; *"test@gen2"*) exit 255;; '
            '*"pgrep"*) exit 1;; esac; exit 0'
        ),
    )
    assert result.returncode == 1
    assert "cannot query dell2 routes" in result.stderr


def test_preflight_refuses_failed_generator_link_query(tmp_path):
    result, _ = runner(
        tmp_path, "preflight",
        ssh_body=(
            'case "$*" in *"existsActive"*) exit 1;; '
            '*"test@gen1"*"ip -o link show"*) exit 255;; '
            '*"pgrep"*) exit 1;; esac; exit 0'
        ),
    )
    assert result.returncode == 1
    assert "cannot query dell1 links" in result.stderr


def test_preflight_refuses_an_existing_dut_vlan_link(tmp_path):
    result, _ = runner(
        tmp_path, "preflight",
        ssh_body=(
            'case "$*" in *"existsActive"*) exit 1;; '
            '*"test@dut"*"ip -o link show"*) echo "8: eth3.7@eth3: <UP>"; exit 0;; '
            '*"pgrep"*) exit 1;; esac; exit 0'
        ),
    )
    assert result.returncode == 1
    assert "DUT link pppoe7, eth3.7 or wgw0 exists" in result.stderr


def test_preflight_refuses_an_occupied_ipv6_route_table(tmp_path):
    result, _ = runner(
        tmp_path, "preflight",
        ssh_body=(
            'case "$*" in *"existsActive"*) exit 1;; '
            '*"ip -N -6 route show table all"*) '
            'echo "fd99:57::/64 dev ppp0 table 157 metric 157"; exit 0;; '
            '*"pgrep"*) exit 1;; esac; exit 0'
        ),
    )
    assert result.returncode == 1
    assert "dell1 route table 157/158 in use" in result.stderr


@pytest.fixture
def netns():
    if not shutil.which("unshare") or not shutil.which("ip"):
        pytest.skip("unshare and ip are required")
    if subprocess.run(["unshare", "-Urn", "ip", "link", "add", "p0", "type", "dummy"],
                      capture_output=True, check=False).returncode:
        pytest.skip("disposable network namespace unavailable")

    def run(script):
        return subprocess.run(["unshare", "-Urn", "sh", "-eu", "-c", script, "_", str(OWN), OWNER],
                              text=True, capture_output=True, check=False)
    return run


def test_owned_link_round_trip_and_foreign_link_refused(netns):
    r = netns('ip link add base0 type dummy; ip link set base0 up; '
              'sh "$1" link-create base0.7 "$2" link base0 type vlan id 7; '
              'ip link show "$2" >/dev/null; sh "$1" link-delete base0.7 "$2"; '
              '! ip link show base0.7 2>/dev/null; '
              'ip link add link base0 name base0.7 type vlan id 7; '
              '! sh "$1" link-create base0.7 "$2" link base0 type vlan id 8; '
              '! sh "$1" link-delete base0.7 "$2"; ip link show base0.7 >/dev/null')
    assert r.returncode == 0, r.stderr


def test_tagged_route_delete_leaves_foreign_routes(netns):
    r = netns('ip link add d0 type dummy; ip link set d0 up; ip addr add 10.1.0.1/24 dev d0; '
              'sh "$1" route-add 10.9.0.0/24 10.1.0.2 159 4159; '
              '! sh "$1" route-add 10.9.0.0/24 10.1.0.2 159 4159; '
              'ip route add 10.9.0.0/24 via 10.1.0.3 metric 50; '
              'sh "$1" route-delete 10.9.0.0/24 10.1.0.2 159 4159; '
              '[ -z "$(ip route show exact 10.9.0.0/24 proto 159)" ]; '
              'ip route show exact 10.9.0.0/24 | grep -q "via 10.1.0.3"')
    assert r.returncode == 0, r.stderr


def test_rule6_delete_refuses_an_untagged_rule(netns):
    r = netns('ip -6 rule add pref 10957 from fd99:57::1 to fd99:2::/64 lookup 157; '
              '! sh "$1" rule6-delete 10957 157 157 fd99:57::1 fd99:2::/64; '
              '[ -n "$(ip -6 rule show pref 10957)" ]; ip -6 rule del pref 10957; '
              'ip -6 rule add pref 10957 from fd99:57::1 to fd99:2::/64 lookup 157 protocol 157; '
              'sh "$1" rule6-delete 10957 157 157 fd99:57::1 fd99:2::/64; '
              '[ -z "$(ip -6 rule show pref 10957)" ]')
    assert r.returncode == 0, r.stderr


def test_owned_service_stop_requires_inactive_empty_cgroup(tmp_path):
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    systemctl = tmp_path / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *--property=LoadState*) echo loaded;;\n"
        "  *--property=ControlGroup*) echo /ask2wan-test;;\n"
        "  \"is-active\"*) echo inactive; exit 3;;\n"
        "  \"stop \"*|\"reset-failed \"*) exit 0;;\n"
        "  *) exit 1;;\n"
        "esac\n"
    )
    systemctl.chmod(0o755)
    result = subprocess.run(
        ["sh", str(OWN), "service-stop",
         "ask2wan-0123456789abcdef.service", boot],
        env=os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
