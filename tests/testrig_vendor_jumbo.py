"""Host checks for owned DUT2 jumbo MTU transitions."""

import os
import csv
import json
from pathlib import Path
import subprocess

import pytest

from testrig_iperf_measurement import tcp_result

pytestmark = pytest.mark.host
HELPER = Path(__file__).resolve().parents[1] / "bin/testrig-vendor-mtu.sh"
NFT = Path(__file__).resolve().parents[1] / "bin/testrig-vendor-jumbo-nft.sh"
RUNNER = Path(__file__).resolve().parents[1] / "bin/testrig-vendor-jumbo.sh"
OWNER = "t2mtu-0123456789abcdef-d3"
MAC = "02:00:00:00:00:03"
TOKEN = "0123456789abcdef0123456789abcdef"


def run_lock_prefix(tmp_path, inherit, parent_lock=False):
    script = RUNNER.read_text().split("\nLEASE_ROOT=", 1)[0].replace(
        "/tmp/testrig-dut2-battery.lock", str(tmp_path / "dut2-battery.lock"),
    )
    env = os.environ | {
        "TESTRIG_ENV": str(tmp_path / "missing"),
        "VENDOR_DUT": "vendor@example", "GEN1": "g1@example", "GEN2": "g2@example",
        "GEN_IF_NS": "testnic", "VENDOR_NS": "testns", "JUMBO_BPID": "32",
        "BR_G1_IPV4": "192.0.2.1", "BR_G2_IPV4": "198.51.100.2",
        "BR_G1_IPV6": "2001:db8:1::1", "BR_G2_IPV6": "2001:db8:2::2",
        "JUMBO_G1_VLAN4": "192.0.2.10", "JUMBO_G2_VLAN4": "198.51.100.20",
        "JUMBO_G1_VLAN6": "2001:db8:10::1", "JUMBO_G2_VLAN6": "2001:db8:20::2",
        "OUTDIR": str(tmp_path), "RIG_INHERIT_LOCK": str(int(inherit)),
        "SCRIPT": script, "TEST_LOCK": str(tmp_path / "dut2-battery.lock"),
    }
    if parent_lock:
        command = [
            "bash", "-c",
            'exec 9>>"$TEST_LOCK"; flock -n 9 || exit; '
            'bash -c "$SCRIPT" testrig-vendor-jumbo.sh run-routed; status=$?; exit "$status"',
        ]
    else:
        command = ["bash", "-c", script, "testrig-vendor-jumbo.sh", "run-routed"]
    return subprocess.run(command, cwd=RUNNER.parent, env=env,
                          capture_output=True, text=True, timeout=30)


def test_jumbo_accepts_inherited_dut2_battery_lock(tmp_path):
    result = run_lock_prefix(tmp_path, inherit=True, parent_lock=True)
    assert result.returncode == 0, result.stderr


def test_jumbo_rejects_missing_inherited_dut2_lock(tmp_path):
    result = run_lock_prefix(tmp_path, inherit=True)
    assert result.returncode != 0
    assert "lock" in result.stderr.lower()


def test_jumbo_refuses_unrelated_dut2_battery_lock(tmp_path):
    result = run_lock_prefix(tmp_path, inherit=False, parent_lock=True)
    assert result.returncode != 0
    assert "lock" in result.stderr.lower()


def test_paired_snapshots_bracket_vendor_jumbo_trials(tmp_path):
    script = RUNNER.read_text()
    before = script[script.index("\nstart_server 0\nstart_server 1\n"):
                    script.index("\nexport QUICK_G1_IPV4=")]
    after = script[script.index("\nstop_server 0\nstop_server 1\n"):
                   script.index('\nif [ "$MODE" != run-routed ]; then\n  record "bridge')]
    snapshot = tmp_path / "testrig-jumbo-snapshot.sh"
    snapshot.write_text('#!/bin/sh\nprintf "snapshot %s\\n" "$2" >> "$CALLS"\n')
    snapshot.chmod(0o755)
    stubs = """
start_server() { printf 'start %s\\n' "$1" >> "$CALLS"; }
stop_server() { printf 'stop %s\\n' "$1" >> "$CALLS"; }
no_test_sockets() { :; }
check_links() { :; }
check_health() { :; }
"""
    env = os.environ | {"HERE": str(tmp_path), "RIG_JUMBO_SNAPSHOT_DIR": str(tmp_path),
                        "CALLS": str(tmp_path / "calls")}
    result = subprocess.run(["bash", "-eu", "-c", stubs + before + after],
                            env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "calls").read_text().splitlines() == [
        "start 0", "start 1", "snapshot before", "stop 0", "stop 1", "snapshot after"]


def test_paired_snapshot_drift_stops_vendor_before_traffic(tmp_path):
    script = RUNNER.read_text()
    before = script[script.index("\nstart_server 0\nstart_server 1\n"):
                    script.index("\nexport QUICK_G1_IPV4=")]
    snapshot = tmp_path / "testrig-jumbo-snapshot.sh"
    snapshot.write_text('#!/bin/sh\nexit 0\n')
    snapshot.chmod(0o755)
    stubs = """
start_server() { :; }
python3() { return 1; }
"""
    env = os.environ | {
        "HERE": str(tmp_path), "RIG_JUMBO_SNAPSHOT_DIR": str(tmp_path),
        "RIG_JUMBO_PAIR_DIR": str(tmp_path), "RIG_JUMBO_PAIR_REP": "1",
        "RIG_JUMBO_ROW": "unicast-9k",
    }
    result = subprocess.run(["bash", "-eu", "-c", stubs + before + "\necho traffic"],
                            env=env, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "traffic" not in result.stdout


@pytest.mark.parametrize(("mode", "bridge_mode"), [
    ("run", "run"), ("smoke", "smoke"), ("run-routed", None),
])
def test_routed_only_mode_does_not_run_bridge(tmp_path, mode, bridge_mode):
    script = RUNNER.read_text()
    tail = script[script.index("\nstop_server 0\nstop_server 1\n"):]
    bridge = tmp_path / "testrig-vendor-bridge.sh"
    bridge.write_text('#!/bin/sh\nprintf "%s\\n" "$1" > "$CALLS"\n')
    bridge.chmod(0o755)
    env = os.environ | {
        "MODE": mode, "HERE": str(tmp_path), "RUN": str(tmp_path),
        "TOKEN": TOKEN, "POOL_MIN": "1000", "REPS": "3",
        "DUR": "32", "STEADY_FROM": "10", "STREAMS": "8",
        "CALLS": str(tmp_path / "bridge-calls"),
    }
    stubs = "\n".join(f"{name}() {{ :; }}" for name in
                      ("stop_server", "no_test_sockets", "check_links",
                       "check_health", "record"))
    result = subprocess.run(["bash", "-eu", "-c", stubs + tail], env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    calls = tmp_path / "bridge-calls"
    if bridge_mode is None:
        assert not calls.exists()
    else:
        assert calls.read_text().strip() == bridge_mode


def test_routed_only_mode_is_accepted():
    script = RUNNER.read_text()
    mode_check = script[script.index("MODE=${1:-run}"):script.index("OUTDIR=${OUTDIR:")]
    result = subprocess.run(["bash", "-c", mode_check, "--", "run-routed"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(("row", "expected"), [
    ("unicast-9k", ["unicast-v4", "unicast-v6", "unicast-v4-rev", "unicast-v6-rev",
                    "unicast-v4-bidir", "unicast-v6-bidir"]),
    ("nat-9k", ["nat-v4", "nat-v6", "nat-v4-rev", "nat-v6-rev",
                "nat-v4-bidir", "nat-v6-bidir"]),
    ("vlan-9k", ["vlan-v4", "vlan-v6", "vlan-v4-rev", "vlan-v6-rev",
                 "vlan-v4-bidir", "vlan-v6-bidir"]),
    ("vlan-port-9k", ["vlanport-v4", "vlanport-v6", "portvlan-v4", "portvlan-v6",
                      "vlanport-v4-bidir", "vlanport-v6-bidir"]),
])
def test_routed_row_selects_only_its_six_cells(row, expected):
    script = RUNNER.read_text()
    snippet = script[script.index("\nuni=(unicast-v4"):script.index("\nstop_server 0\nstop_server 1\n")]
    stubs = """
check_links() { :; }
start_nat() { printf 'nat:%s\\n' "$1"; }
clear_nat() { :; }
run_quick() { printf 'cell:%s%s\\n' "$1" "$([ "$2" = 1 ] && echo -bidir)"; }
"""
    env = os.environ | {"MODE": "run-routed", "RIG_JUMBO_ROW": row, "REPS": "1"}
    result = subprocess.run(["bash", "-eu", "-c", stubs + snippet], env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    cells = [line.removeprefix("cell:") for line in result.stdout.splitlines()
             if line.startswith("cell:")]
    assert cells == expected


@pytest.mark.parametrize(("mode", "row"), [
    ("run", "unicast-9k"), ("smoke", "nat-9k"),
    ("run-routed", "bridge-9k"), ("run-routed", "bogus"),
])
def test_row_selector_rejects_unsupported_mode_or_row_before_bench_access(mode, row):
    script = RUNNER.read_text()
    snippet = script[script.index("MODE=${1:-run}"):script.index("OUTDIR=${OUTDIR:")]
    result = subprocess.run(["bash", "-eu", "-c", snippet, "--", mode],
                            env=os.environ | {"RIG_JUMBO_ROW": row},
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "RIG_JUMBO_ROW" in result.stderr


@pytest.mark.parametrize("mode", ["run-routed", "preflight-routed"])
def test_routed_only_mode_does_not_require_bridge_probes(tmp_path, mode):
    env = os.environ | {
        "TESTRIG_ENV": str(tmp_path / "missing"),
        "VENDOR_DUT": "vendor@example", "GEN1": "g1@example", "GEN2": "g2@example",
        "GEN_IF_NS": "testnic", "VENDOR_NS": "testns", "JUMBO_BPID": "32",
        "BR_G1_IPV4": "192.0.2.1", "BR_G2_IPV4": "198.51.100.2",
        "BR_G1_IPV6": "2001:db8:1::1", "BR_G2_IPV6": "2001:db8:2::2",
        "JUMBO_G1_VLAN4": "192.0.2.10", "JUMBO_G2_VLAN4": "198.51.100.20",
        "JUMBO_G1_VLAN6": "2001:db8:10::1", "JUMBO_G2_VLAN6": "2001:db8:20::2",
        "OUTDIR": "relative",
    }
    env.pop("BR_PROBE1", None)
    env.pop("BR_PROBE2", None)
    result = subprocess.run(["bash", str(RUNNER), mode], env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "OUTDIR must be absolute" in result.stderr
    result = subprocess.run(["bash", str(RUNNER), "run"], env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "BR_PROBE1 BR_PROBE2" in result.stderr


@pytest.mark.parametrize(("eth3", "eth4", "ok"), [
    ("32", "32", True),
    ("32", "33", False),
    ("32 33", "32", False),
    ("unreadable", "32", False),
])
def test_pool_count_requires_live_bpid_on_both_ports(eth3, eth4, ok):
    script = RUNNER.read_text()
    functions = script[script.index("\npool_count(){"):script.index("\n# Parent links")]
    stubs = """
d() {
  case "$1" in
    *eth3/bpids*) value=$BP3 ;;
    *eth4/bpids*) value=$BP4 ;;
    *pool_count*) value=12000 ;;
    *) return 1 ;;
  esac
  [ "$value" != unreadable ] || return 1
  printf '%s\\n' "$value"
}
fail() { echo "$*" >&2; return 1; }
"""
    env = os.environ | {"BP3": eth3, "BP4": eth4, "JUMBO_BPID": "32"}
    result = subprocess.run(["bash", "-eu", "-c", stubs + functions + "\npool_count"],
                            env=env, capture_output=True, text=True, check=False)
    assert (result.returncode == 0) is ok
    if not ok:
        assert "BPID" in result.stderr


def test_jumbo_rejects_scoped_ipv6_endpoint_before_bench_access():
    script = RUNNER.read_text()
    snippet = script[script.index('if ! python3 - "$BR_G1_IPV4"'):script.index("\nREPS=")]
    env = os.environ | {
        "BR_G1_IPV4": "192.0.2.1", "BR_G2_IPV4": "198.51.100.2",
        "BR_G1_IPV6": "fe80::1%zone", "BR_G2_IPV6": "2001:db8:2::2",
        "JUMBO_G1_VLAN4": "192.0.2.10", "JUMBO_G2_VLAN4": "198.51.100.20",
        "JUMBO_G1_VLAN6": "2001:db8:10::1", "JUMBO_G2_VLAN6": "2001:db8:20::2",
        "BR_PROBE1": "192.0.2.200", "BR_PROBE2": "198.51.100.200",
    }
    result = subprocess.run(
        ["bash", "-c", snippet], env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0


def fixture(tmp_path):
    state = tmp_path / "state"
    dev = state / "eth3"
    dev.mkdir(parents=True)
    for name, value in {"ifindex": "5", "iflink": "5", "address": MAC, "mtu": "1500"}.items():
        (dev / name).write_text(value + "\n")
    (dev / "altnames").write_text("")
    cat = tmp_path / "cat"
    cat.write_text(
        '#!/bin/sh\n'
        'case "$1" in /sys/class/net/*) p=${1#/sys/class/net/}; exec /bin/cat "$STATE/$p";; '
        '*) exec /bin/cat "$@";; esac\n'
    )
    cat.chmod(0o755)
    ip = tmp_path / "ip"
    ip.write_text(
        '#!/bin/sh\n'
        'case "$*" in\n'
        '  "-d -o link show dev "*)\n'
        '    dev=${6}; printf "5: %s: mtu %s" "$dev" "$(/bin/cat "$STATE/$dev/mtu")"\n'
        '    while read -r n; do [ -z "$n" ] || printf " altname %s" "$n"; done '
        '< "$STATE/$dev/altnames"; printf "\\n";;\n'
        '  "link property add dev "*)\n'
        '    printf "%s\\n" "$*" >> "$CALLS"; echo "$7" >> "$STATE/$5/altnames";;\n'
        '  "link property del dev "*)\n'
        '    printf "%s\\n" "$*" >> "$CALLS"; '
        '    grep -Fxv "$7" "$STATE/$5/altnames" > "$STATE/$5/alt.tmp" || true; '
        '    mv "$STATE/$5/alt.tmp" "$STATE/$5/altnames";;\n'
        '  "link set dev "*)\n'
        '    printf "%s\\n" "$*" >> "$CALLS"; echo "$6" > "$STATE/$4/mtu"; '
        '    [ "${FAIL_AFTER_WRITE:-0}" = 0 ] || exit 1;;\n'
        '  *) echo "unexpected ip call: $*" >&2; exit 1;;\n'
        'esac\n'
    )
    ip.chmod(0o755)
    return {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "STATE": str(state),
        "CALLS": str(tmp_path / "calls"),
    }


def run(env, operation, *, ifindex="5", mac=MAC, before="1500", after="9000", dev="eth3"):
    return subprocess.run(
        ["sh", str(HELPER), operation, dev, ifindex, "5", mac, OWNER, before, after],
        env=env, capture_output=True, text=True, check=False,
    )


def test_owned_mtu_round_trip(tmp_path):
    env = fixture(tmp_path)
    assert run(env, "claim").returncode == 0
    assert run(env, "set").returncode == 0
    assert (tmp_path / "state/eth3/mtu").read_text().strip() == "9000"
    assert run(env, "set", before="9000", after="1500").returncode == 0
    assert run(env, "release", before="9000", after="1500").returncode == 0
    assert (tmp_path / "state/eth3/mtu").read_text().strip() == "1500"
    assert (tmp_path / "state/eth3/altnames").read_text() == ""


@pytest.mark.parametrize("change", ["index", "mac", "mtu", "owner"])
def test_mtu_set_refuses_changed_identity_or_foreign_state(tmp_path, change):
    env = fixture(tmp_path)
    assert run(env, "claim").returncode == 0
    dev = tmp_path / "state/eth3"
    if change == "index":
        (dev / "ifindex").write_text("6\n")
    elif change == "mac":
        (dev / "address").write_text("02:00:00:00:00:04\n")
    elif change == "mtu":
        (dev / "mtu").write_text("1400\n")
    else:
        (dev / "altnames").write_text("foreign-owner\n")
    result = run(env, "set")
    assert result.returncode != 0
    assert "link set" not in (tmp_path / "calls").read_text()


def test_partial_mtu_write_can_be_restored_by_owner(tmp_path):
    env = fixture(tmp_path)
    assert run(env, "claim").returncode == 0
    result = run(env | {"FAIL_AFTER_WRITE": "1"}, "set")
    assert result.returncode != 0
    assert (tmp_path / "state/eth3/mtu").read_text().strip() == "9000"
    assert run(env, "set", before="9000", after="1500").returncode == 0
    assert run(env, "release", before="9000", after="1500").returncode == 0


def test_owned_mtu_check_rejects_missing_marker_and_wrong_mtu(tmp_path):
    env = fixture(tmp_path)
    assert run(env, "check", before="9000", after="1500").returncode != 0
    assert run(env, "claim").returncode == 0
    assert run(env, "check", before="9000", after="1500").returncode != 0
    assert run(env, "set").returncode == 0
    assert run(env, "check", before="9000", after="1500").returncode == 0
    (tmp_path / "state/eth3/altnames").write_text("")
    assert run(env, "check", before="9000", after="1500").returncode != 0


def test_unclaimed_port_and_eth0_are_never_changed(tmp_path):
    env = fixture(tmp_path)
    assert run(env, "set").returncode != 0
    assert run(env, "claim", dev="eth0").returncode != 0
    assert not (tmp_path / "calls").exists()


def nft_fixture(tmp_path, second_owner=None):
    nft = tmp_path / "nft"
    nft.write_text(
        '#!/bin/sh\n'
        'printf "%s\\n" "$*" >> "$CALLS"\n'
        'case "$*" in\n'
        f'  "list tables") printf "table ip vwn{TOKEN}\\ntable ip6 vwn{TOKEN}\\n";;\n'
        f'  "-a list table ip vwn{TOKEN}") '
        f'printf \'table ip vwn{TOKEN} # handle 42 {{\\n comment "dut2-wan-{TOKEN}"\\n}}\\n\';;\n'
        f'  "-a list table ip6 vwn{TOKEN}") '
        f'printf \'table ip6 vwn{TOKEN} # handle 43 {{\\n comment "%s"\\n}}\\n\' "$OWNER6";;\n'
        '  "-f -") /bin/cat > "$RULES";;\n'
        '  *) exit 1;;\n'
        'esac\n'
    )
    nft.chmod(0o755)
    return {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "CALLS": str(tmp_path / "nft-calls"),
        "RULES": str(tmp_path / "rules"),
        "OWNER6": second_owner or f"dut2-wan-{TOKEN}",
    }


def test_owned_nat_tables_use_exact_test_endpoints(tmp_path):
    env = nft_fixture(tmp_path)
    result = subprocess.run(
        ["sh", str(NFT), "create", TOKEN, "nat", "192.0.2.1", "198.51.100.2",
         "2001:db8:1::1", "2001:db8:2::2"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    content = (tmp_path / "rules").read_text()
    assert f'create table ip vwn{TOKEN}' in content
    assert f'create table ip6 vwn{TOKEN}' in content
    assert "ip saddr 192.0.2.1 ip daddr 198.51.100.2" in content
    assert "ip saddr 198.51.100.2 ip daddr 192.0.2.1" in content
    assert "ip6 saddr 2001:db8:1::1 ip6 daddr 2001:db8:2::2" in content
    assert "ip6 saddr 2001:db8:2::2 ip6 daddr 2001:db8:1::1" in content


def test_nat_delete_checks_both_owners_before_deleting(tmp_path):
    env = nft_fixture(tmp_path, second_owner="foreign")
    result = subprocess.run(["sh", str(NFT), "delete", TOKEN], env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert not (tmp_path / "rules").exists()


def test_nat_delete_uses_verified_handles_in_one_transaction(tmp_path):
    env = nft_fixture(tmp_path)
    result = subprocess.run(["sh", str(NFT), "delete", TOKEN], env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "rules").read_text().splitlines() == [
        "delete table ip handle 42", "delete table ip6 handle 43"
    ]


@pytest.mark.parametrize(
    ("source4", "source6"),
    [("192.0.2.999", "2001:db8::1"), ("192.0.2.1", "2001:db8::1;delete")],
)
def test_invalid_nat_endpoints_are_rejected_before_nft(tmp_path, source4, source6):
    env = nft_fixture(tmp_path)
    result = subprocess.run(
        ["sh", str(NFT), "create", TOKEN, "nat", source4, "198.51.100.2",
         source6, "2001:db8::2"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert not (tmp_path / "nft-calls").exists()


def run_restore(tmp_path, *, claimed=1, count=8, unsafe=0, failed="", foreign=""):
    script = RUNNER.read_text()
    restore = script[script.index("restore_mtu(){"):script.index("\ncleanup(){")]
    calls = tmp_path / "restore-calls"
    result = subprocess.run(
        ["bash", "-c",
         'safe_to_restore(){ [ "$UNSAFE" = 0 ]; }\n'
         'check_health(){ :; }\n'
         'no_foreign_children(){ [ "$1" != "$FOREIGN" ]; }\n'
         'link_op(){ printf "%s %s\\n" "$1" "$2" >> "$CALLS"; '
         '[ "$1:$2" != "$FAILED" ]; }\n'
         f'{restore}\nrestore_mtu\n'],
        env=os.environ | {
            "claims_done": str(claimed), "claim_count": str(count),
            "UNSAFE": str(unsafe), "FAILED": failed, "FOREIGN": foreign,
            "CALLS": str(calls),
        },
        capture_output=True, text=True, check=False,
    )
    return result, calls.read_text().splitlines() if calls.exists() else []


def test_restore_refuses_unsafe_traffic_or_bridge_state(tmp_path):
    result, calls = run_restore(tmp_path, unsafe=1)
    assert result.returncode != 0
    assert calls == []


@pytest.mark.parametrize("failed", ["set:5", "set:7", "set:4", "set:6"])
def test_restore_never_lowers_dut_when_generator_restore_fails(tmp_path, failed):
    result, calls = run_restore(tmp_path, failed=failed)
    assert result.returncode != 0
    assert not any(call in ("set 0", "set 1", "set 2", "set 3") for call in calls)
    assert not any(call.startswith("release ") for call in calls)


def test_restore_refuses_to_lower_a_parent_with_foreign_children(tmp_path):
    result, calls = run_restore(tmp_path, foreign="4")
    assert result.returncode != 0
    assert "set 4" not in calls
    assert not any(call in ("set 0", "set 1", "set 2", "set 3") for call in calls)


def test_restore_lowers_children_then_parents_and_releases_claims(tmp_path):
    result, calls = run_restore(tmp_path)
    assert result.returncode == 0, result.stderr
    assert calls[:8] == [f"set {i}" for i in (5, 7, 4, 6, 2, 3, 0, 1)]
    assert calls[8:] == [f"release {i}" for i in reversed(range(8))]


def test_partial_claim_cleanup_never_changes_mtu(tmp_path):
    result, calls = run_restore(tmp_path, claimed=0, count=3)
    assert result.returncode == 0, result.stderr
    assert calls == ["release 2", "release 1", "release 0"]


@pytest.mark.parametrize(
    ("mss", "dest", "repetitions", "csv_rate", "expected"),
    [(8948, "198.51.100.2", 1, "9.0", True), (1448, "198.51.100.2", 1, "9.0", False),
     (8948, "198.51.100.3", 1, "9.0", False), (8948, "198.51.100.2", 2, "9.0", False),
     (8948, "198.51.100.2", 1, "8.0", False)],
)
def test_jumbo_result_requires_one_valid_sample_with_expected_endpoints(
    tmp_path, mss, dest, repetitions, csv_rate, expected
):
    script = RUNNER.read_text()
    validate = script[script.index("validate_result(){"):script.index("\nrun_quick(){")]
    csv_path = tmp_path / "result.csv"
    json_path = tmp_path / "result.json"
    with csv_path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=("cell", "gbps", "verdict"))
        writer.writeheader()
        for _ in range(repetitions):
            writer.writerow({"cell": "unicast-v4", "gbps": csv_rate, "verdict": "HW"})
    data = tcp_result(rate=9e9)
    data["start"].update(tcp_mss_default=mss, connected=[
        {"local_host": "192.0.2.1", "remote_host": dest, "remote_port": 5278}
        for _ in range(2)])
    json_path.write_text(json.dumps(data))
    env = os.environ | {
        "HERE": str(RUNNER.parent), "STREAMS": "2", "PORT": "5278", "DUR": "8", "STEADY_FROM": "2",
        "BR_G1_IPV4": "192.0.2.1", "BR_G2_IPV4": "198.51.100.2",
        "BR_G1_IPV6": "2001:db8:1::1", "BR_G2_IPV6": "2001:db8:2::2",
        "JUMBO_G1_VLAN4": "192.0.2.10", "JUMBO_G2_VLAN4": "198.51.100.20",
        "JUMBO_G1_VLAN6": "2001:db8:10::1", "JUMBO_G2_VLAN6": "2001:db8:20::2",
    }
    result = subprocess.run(
        ["bash", "-c", f'{validate}\nvalidate_result "$1" "$2" unicast-v4\n',
         "test", str(csv_path), str(json_path)],
        env=env, capture_output=True, text=True, check=False,
    )
    assert (result.returncode == 0) == expected, result.stderr


@pytest.mark.parametrize(
    ("connections", "reverse_interval", "forward_rate", "reverse_rate", "expected"),
    [(4, True, 5e9, 4e9, True), (2, True, 5e9, 4e9, False),
     (4, False, 5e9, 4e9, False), (4, True, 5e9, 0, True),
     (4, True, 0, 4e9, True), (4, True, 0, 0, True)],
)
def test_jumbo_bidir_requires_both_stream_sets_and_reverse_intervals(
    tmp_path, connections, reverse_interval, forward_rate, reverse_rate, expected
):
    script = RUNNER.read_text()
    validate = script[script.index("validate_result(){"):script.index("\nrun_quick(){")]
    csv_path = tmp_path / "result.csv"
    json_path = tmp_path / "result.json"
    total_rate = (forward_rate + (reverse_rate if reverse_interval else 0)) / 1e9
    verdict = "FAIL(no traffic)" if total_rate < .1 else "HW"
    csv_path.write_text(f"cell,gbps,verdict\nunicast-v4-bidir,{total_rate},{verdict}\n")
    data = tcp_result(bidir=True, rate=forward_rate, reverse_rate=reverse_rate)
    if not reverse_interval:
        for interval in data["intervals"]:
            interval.pop("sum_bidir_reverse")
    data["start"].update(tcp_mss_default=8948, connected=[
        {"local_host": "192.0.2.1", "remote_host": "198.51.100.2", "remote_port": 5278}
        for _ in range(connections)])
    json_path.write_text(json.dumps(data))
    env = os.environ | {
        "HERE": str(RUNNER.parent), "STREAMS": "2", "PORT": "5278", "DUR": "8", "STEADY_FROM": "2",
        "BR_G1_IPV4": "192.0.2.1", "BR_G2_IPV4": "198.51.100.2",
        "BR_G1_IPV6": "2001:db8:1::1", "BR_G2_IPV6": "2001:db8:2::2",
        "JUMBO_G1_VLAN4": "192.0.2.10", "JUMBO_G2_VLAN4": "198.51.100.20",
        "JUMBO_G1_VLAN6": "2001:db8:10::1", "JUMBO_G2_VLAN6": "2001:db8:20::2",
    }
    result = subprocess.run(
        ["bash", "-c", f'{validate}\nvalidate_result "$1" "$2" unicast-v4-bidir\n',
         "test", str(csv_path), str(json_path)],
        env=env, capture_output=True, text=True, check=False,
    )
    assert (result.returncode == 0) == expected, result.stderr
