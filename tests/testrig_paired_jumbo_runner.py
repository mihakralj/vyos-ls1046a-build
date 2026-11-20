"""Host-only checks for the paired jumbo coordinator."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess

import pytest

from testrig_paired_jumbo import snapshots, write_trial

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
BOOT1 = "01010101-0101-4101-8101-010101010101"
BOOT2 = "02020202-0202-4202-8202-020202020202"


@pytest.fixture
def bench(tmp_path):
    bindir = tmp_path / "bin"
    fakebin = tmp_path / "fakebin"
    state = tmp_path / "state"
    reference = tmp_path / "reference"
    templates = tmp_path / "templates"
    snaps = tmp_path / "snapshots"
    for path in (bindir, fakebin, state, reference, templates, snaps):
        path.mkdir()
    files = (
        "testrig-paired-jumbo.sh", "testrig-env.sh", "testrig-parity-profile.sh",
        "testrig_jumbo_parity.py", "testrig_parity.py", "testrig_iperf.py",
        "testrig_wg_validation.py", "testrig-wg-server.py", "testrig-wgfp-stop.py",
        "testrig-vendor-mtu.sh", "testrig-vendor-jumbo-nft.sh",
    )
    for name in files:
        source = ROOT / "bin" / name
        if source.exists():
            shutil.copy(source, bindir / name)
    runner = bindir / "testrig-paired-jumbo.sh"
    if runner.exists():
        source = runner.read_text().replace(
            "/tmp/testrig-dut2-battery.lock", str(tmp_path / "dut2.lock"))
        gate = (
            'if [[ $MODE = run && ${RIG_JUMBO_LIVE_APPROVED:-0} != 1 ]]; then\n'
            '  echo "ERROR: paired jumbo run disabled until FE disengagement and drain are proven safe" >&2\n'
            '  exit 2\n'
            'fi\n'
        )
        assert source.count(gate) == 1
        # Exercise candidate coordinator code only with fake SSH; production stays gated.
        runner.write_text(source.replace(gate, "", 1))

    def executable(path, text):
        path.write_text(text)
        path.chmod(0o755)

    executable(fakebin / "pgrep", "#!/bin/sh\nexit 1\n")
    executable(fakebin / "ssh", """#!/usr/bin/env bash
set -eu
host= cmd=
for arg in "$@"; do
  case $arg in test@*) host=$arg ;; esac
  cmd=$arg
done
case "$cmd" in
  'cat /proc/sys/kernel/random/boot_id')
    if [ "$host" = test@ask2 ]; then echo "$BOOT1"; else echo "$BOOT2"; fi ;;
  'cat /etc/os-release')
    if [ "$host" = test@ask2 ]; then echo ID=vyos; else echo ID=openwrt; fi ;;
  *'wgfp_peer'*) echo off ;;
  *'show configuration commands'*) echo 'set system host-name test' ;;
  *'systemctl is-active dhclient'*) : ;;
  *'uname -r'*) echo "controls $host" ;;
  *'nft list tables'*) echo 'table inet filter' ;;
  'sudo -n sh -s -- '*)
    cat >/dev/null
    [ "${FAIL_SOCKET_QUERY:-0}" = 0 ] || exit 255
    if [ "${BUSY_SOCKET:-0}" = 1 ] || [ -e "$STATE/foreign-socket" ]; then
      echo 'foreign test socket'
    fi ;;
  *'python3 - start '*)
    set -- $cmd
    directory=$6
    echo "start $host" >> "$CALLS"
    touch "$STATE/server-$host-${directory##*-}" ;;
  *'python3 - identity '*) echo '123:456:5278' ;;
  *'python3 - 123 456 server '*)
    echo "stop $host" >> "$CALLS"
    [ "${FAIL_STOP:-0}" = 0 ] ;;
  *'python3 - remove '*)
    set -- $cmd
    directory=$6
    rm "$STATE/server-$host-${directory##*-}" ;;
  *) echo "unexpected ssh: $host $cmd" >&2; exit 1 ;;
esac
""")
    executable(bindir / "testrig-ask2-9k.sh", """#!/usr/bin/env bash
set -eu
lease="$XDG_STATE_HOME/testrig-ask2-jumbo/lease"
case $1 in
  preflight)
    echo 'preflight ask' >> "$CALLS"
    [ "${FAIL_ASK_PREFLIGHT:-0}" = 0 ] ;;
  up)
    echo 'ask up' >> "$CALLS"
    mkdir -p "$(dirname "$lease")"
    printf '%s\\n' "$RIG_ASK2_JUMBO_TOKEN" > "$lease" ;;
  down)
    [ "$(cat "$lease")" = "$RIG_ASK2_JUMBO_TOKEN" ] || exit 1
    echo 'ask down' >> "$CALLS"
    rm "$lease" ;;
esac
""")
    executable(bindir / "testrig-jumbo-snapshot.sh", """#!/usr/bin/env bash
set -eu
case $1 in
  */baseline)
    kind=baseline
    if [ "${TRUNCATE_SCHEDULE:-0}" = 1 ]; then
      head -n 2 "$1/../schedule.tsv" > "$1/../short-schedule"
      mv "$1/../short-schedule" "$1/../schedule.tsv"
    fi ;;
  */ask2-*-snapshot) kind=ask ;;
  */vendor-*-snapshot) kind=vendor ;;
  *) exit 1 ;;
esac
for gen in 1 2; do cp "$SNAPS/$kind-$2.json" "$1/gen$gen-$2.json"; done
echo tuning > "$1/generator-tuning-$2.txt"
""")
    executable(bindir / "testrig-gen-tune.sh", "#!/bin/sh\nexit 0\n")
    executable(bindir / "testrig-offload-quick.sh", """#!/usr/bin/env bash
set -eu
tag=$1
[ "$BIDIR" = 0 ] || tag=$tag-bidir
echo "quick $tag" >> "$CALLS"
if [ "${BUSY_AFTER_QUICK:-0}" = 1 ]; then touch "$STATE/foreign-socket"; fi
[ "${FAIL_QUICK:-}" != "$tag" ] || exit 1
mkdir -p "$J"
cp "$TEMPLATES/$tag.csv" "$OUT"
cp "$TEMPLATES/$tag.json" "$J/$tag.json"
""")
    executable(bindir / "testrig-vendor-jumbo.sh", """#!/usr/bin/env bash
set -eu
    if [ "$1" = preflight-routed ]; then
      echo 'preflight vendor' >> "$CALLS"
      [ "${FAIL_VENDOR_PREFLIGHT:-0}" = 0 ]
      exit
    fi
[ "$1" = run-routed ] && [ "$REPS" = 1 ] && [ "$WG_REPS" = 1 ]
[ "$RIG_INHERIT_LOCK" = 1 ]
if compgen -G "$STATE/server-*" >/dev/null; then exit 1; fi
echo "vendor $RIG_JUMBO_ROW" >> "$CALLS"
run="$OUTDIR/dut2-jumbo-test"
mkdir -p "$run/json"
"$HERE/testrig-jumbo-snapshot.sh" "$RIG_JUMBO_SNAPSHOT_DIR" before
python3 "$HERE/testrig_jumbo_parity.py" check-snapshot \
  "$RIG_JUMBO_PAIR_DIR" "$RIG_JUMBO_ROW" "$RIG_JUMBO_PAIR_REP" vendor before
while IFS=$'\\t' read -r column cell bidir; do
  tag=$cell
  [ "$bidir" = 0 ] || tag=$tag-bidir
  mkdir "$run/json/$tag-r1"
  cp "$TEMPLATES/$tag.csv" "$run/quick9k-$tag-r1.csv"
  cp "$TEMPLATES/$tag.json" "$run/json/$tag-r1/$tag.json"
done < <(python3 "$HERE/testrig_jumbo_parity.py" cells "$RIG_JUMBO_ROW")
"$HERE/testrig-jumbo-snapshot.sh" "$RIG_JUMBO_SNAPSHOT_DIR" after
printf 'mode=run-routed\\nboot=%s\\n' "$BOOT2" > "$run/COMPLETE"
""")
    for cell in ("unicast-v4", "unicast-v6", "unicast-v4-rev", "unicast-v6-rev"):
        for bidir in (0, 1) if not cell.endswith("-rev") else (0,):
            tag = cell + ("-bidir" if bidir else "")
            write_trial(templates / f"{tag}.csv", templates / f"{tag}.json",
                        cell, bidir, 6 if "-v6" in cell else 4)
    baseline, ask_before, ask_after, vendor_before, vendor_after = snapshots()
    for name, value in (("baseline-before", baseline), ("ask-before", ask_before),
                        ("ask-after", ask_after), ("vendor-before", vendor_before),
                        ("vendor-after", vendor_after)):
        (snaps / f"{name}.json").write_text(json.dumps(value))
    for gen in (1, 2):
        (reference / f"gen{gen}-before.json").write_text(json.dumps(baseline))
    profile = {
        "protocol": "matched-iperf-v1", "duration": 32, "steady_from": 10,
        "measure_to": 30, "streams": 8, "repetitions": 3, "congestion": "bbr",
        "mtu": 1500, "interval": 1, "zerocopy": True, "extra_sampling": False,
        "rate_semantics": "client sender normally, client receiver with -R; bidir adds reverse receiver",
    }
    (reference / "protocol.json").write_text(json.dumps(profile))
    (reference / "PARTIAL").write_text("successfully completed runnable rows\n")
    for board, boot, platform in (("ask2", BOOT1, "vyos"), ("vendor", BOOT2, "openwrt")):
        (reference / f"boot-{board}.txt").write_text(boot + "\n")
        (reference / f"platform-{board}.txt").write_text(f"ID={platform}\n")
        (reference / f"controls-{board}.txt").write_text(f"controls test@{board}\n")
    source = reference / "source"
    source.write_text("unchanged\n")
    (reference / "harness.sha256").write_text(
        f"{hashlib.sha256(source.read_bytes()).hexdigest()}  {source}\n")

    env = os.environ | {
        "PATH": f"{fakebin}:{os.environ['PATH']}", "TESTRIG_ENV": str(tmp_path / "none"),
        "RIG_OUT": str(tmp_path / "output"), "RUN_DIR": str(tmp_path / "run"),
        "PARITY_REFERENCE_DIR": str(reference), "PARITY_ASK2_COLD_BOOT_ID": BOOT1,
        "PARITY_VENDOR_COLD_BOOT_ID": BOOT2, "ASK2_DUT": "test@ask2",
        "VENDOR_DUT": "test@vendor", "GEN1": "test@gen1", "GEN2": "test@gen2",
        "GEN_IF": "testnic0", "GEN_IF_NS": "testnic1", "VENDOR_NS": "testns",
        "BR_G1_IPV4": "192.0.2.1", "BR_G2_IPV4": "192.0.2.2",
        "BR_G1_IPV6": "2001:db8::1", "BR_G2_IPV6": "2001:db8::2",
        "JUMBO_G1_VLAN4": "192.0.2.10", "JUMBO_G2_VLAN4": "192.0.2.20",
        "JUMBO_G1_VLAN6": "2001:db8::10", "JUMBO_G2_VLAN6": "2001:db8::20",
        "JUMBO_BPID": "32",
        "RIG_KEY": "", "XDG_STATE_HOME": str(state), "STATE": str(state),
        "BOOT1": BOOT1, "BOOT2": BOOT2, "CALLS": str(tmp_path / "calls"),
        "TEMPLATES": str(templates), "SNAPS": str(snaps), "HERE": str(bindir),
        "DUR": "32", "STEADY_FROM": "10", "MEASURE_TO": "30",
        "STREAMS": "8", "REPS": "3", "WG_REPS": "3", "PARITY_CC": "bbr",
    }
    for name in ("RIG_JUMBO_ROW", "BIDIR", "PARITY_MTU", "RUN_ON", "SAMPLE",
                 "DUT_TRACE", "BIDIR_DUR", "BIDIR_STEADY_FROM", "IPERF_ARGS"):
        env.pop(name, None)

    def run(*args, **extra):
        return subprocess.run(["bash", str(runner), *(args or ("unicast-9k",))], env=env | extra,
                              capture_output=True, text=True, timeout=60)

    def calls():
        path = tmp_path / "calls"
        return path.read_text().splitlines() if path.exists() else []

    return run, calls, state, reference, tmp_path / "run"


def test_coordinator_runs_paired_best_of_three_and_records_six_cells(bench):
    run, calls, state, _, result_dir = bench
    result = run()
    assert result.returncode == 0, result.stderr
    operations = [line for line in calls() if line.startswith(("ask ", "quick ", "vendor "))]
    assert operations == ([
        "ask up", "quick unicast-v4", "quick unicast-v6",
        "quick unicast-v4-rev", "quick unicast-v6-rev",
        "quick unicast-v4-bidir", "quick unicast-v6-bidir",
        "ask down", "vendor unicast-9k",
    ] * 3)
    assert len(list(result_dir.glob("pair-*.json"))) == 18
    assert (result_dir / "COMPLETE").exists()
    assert not list(state.glob("server-*"))
    assert not (state / "testrig-ask2-jumbo/lease").exists()


def test_coordinator_cleans_owned_resources_on_trial_failure(bench):
    run, calls, state, _, result_dir = bench
    result = run(FAIL_QUICK="unicast-v4-rev")
    assert result.returncode != 0
    assert "ask down" in calls()
    assert not any(line.startswith("vendor ") for line in calls())
    assert not list(state.glob("server-*"))
    assert not (state / "testrig-ask2-jumbo/lease").exists()
    assert not (result_dir / "COMPLETE").exists()


def test_coordinator_retains_lease_if_server_cleanup_is_uncertain(bench):
    run, calls, state, _, result_dir = bench
    result = run(FAIL_QUICK="unicast-v4-rev", FAIL_STOP="1")
    assert result.returncode != 0
    assert "ask down" not in calls()
    assert (state / "testrig-ask2-jumbo/lease").exists()
    assert not any(line.startswith("vendor ") for line in calls())
    assert not (result_dir / "COMPLETE").exists()


def test_coordinator_does_not_touch_foreign_jumbo_lease(bench):
    run, calls, state, _, _ = bench
    lease = state / "testrig-ask2-jumbo/lease"
    lease.parent.mkdir()
    lease.write_text("foreign\n")
    result = run()
    assert result.returncode != 0
    assert lease.read_text() == "foreign\n"
    assert not any(line.startswith(("ask ", "quick ", "vendor ")) for line in calls())


def test_coordinator_rejects_changed_reference_harness(bench):
    run, calls, _, reference, _ = bench
    (reference / "source").write_text("changed\n")
    result = run()
    assert result.returncode != 0
    assert not any(line.startswith(("ask ", "quick ", "vendor ")) for line in calls())


def test_preflight_validates_without_changing_or_completing_a_battery(bench):
    run, calls, state, _, result_dir = bench
    result = run("--preflight", "unicast-9k")
    assert result.returncode == 0, result.stderr
    assert calls() == ["preflight ask", "preflight vendor"]
    assert not any(line.startswith(("ask ", "quick ", "vendor ")) for line in calls())
    assert not (state / "testrig-ask2-jumbo/lease").exists()
    assert not (result_dir / "COMPLETE").exists()


@pytest.mark.parametrize("failure", ["FAIL_ASK_PREFLIGHT", "FAIL_VENDOR_PREFLIGHT"])
def test_adapter_preflight_failure_stops_before_any_changes(bench, failure):
    run, calls, state, _, result_dir = bench
    result = run(**{failure: "1"})
    assert result.returncode != 0
    assert not any(line.startswith(("ask ", "quick ", "vendor ")) for line in calls())
    assert not (state / "testrig-ask2-jumbo/lease").exists()
    assert not (result_dir / "COMPLETE").exists()


def test_coordinator_rejects_unfinished_reference(bench):
    run, calls, _, reference, _ = bench
    (reference / "PARTIAL").unlink()
    result = run()
    assert result.returncode != 0
    assert "reference battery" in result.stderr
    assert calls() == []


def test_coordinator_rejects_a_different_cold_boot(bench):
    run, calls, state, _, _ = bench
    result = run(PARITY_ASK2_COLD_BOOT_ID=BOOT2)
    assert result.returncode != 0
    assert "cold boot" in result.stderr
    assert not any(line.startswith(("ask ", "quick ", "vendor ")) for line in calls())
    assert not (state / "testrig-ask2-jumbo/lease").exists()


def test_coordinator_never_completes_a_truncated_schedule(bench):
    run, _, _, _, result_dir = bench
    result = run(TRUNCATE_SCHEDULE="1")
    assert result.returncode != 0
    assert not list(result_dir.glob("pair-*.json"))
    assert not (result_dir / "COMPLETE").exists()


def test_coordinator_rejects_different_board_endpoints_before_changes(bench):
    run, calls, _, _, _ = bench
    result = run(QUICK_G1_IPV4="192.0.2.99")
    assert result.returncode != 0
    assert not any(line.startswith(("ask ", "quick ", "vendor ")) for line in calls())


def test_coordinator_rejects_unsafe_endpoint_before_remote_execution(bench):
    run, calls, _, _, _ = bench
    result = run(BR_G1_IPV4="192.0.2.1;exit")
    assert result.returncode != 0
    assert "invalid generator endpoint" in result.stderr
    assert calls() == []


@pytest.mark.parametrize("failure", [{"BUSY_SOCKET": "1"}, {"FAIL_SOCKET_QUERY": "1"}])
def test_coordinator_refuses_active_or_uninspectable_test_sockets(bench, failure):
    run, calls, state, _, _ = bench
    result = run(**failure)
    assert result.returncode != 0
    assert not any(line.startswith(("ask ", "quick ", "vendor ")) for line in calls())
    assert not (state / "testrig-ask2-jumbo/lease").exists()


def test_coordinator_retains_lease_if_foreign_socket_appears_before_restore(bench):
    run, calls, state, _, _ = bench
    result = run(BUSY_AFTER_QUICK="1", FAIL_QUICK="unicast-v4-rev")
    assert result.returncode != 0
    assert "ask down" not in calls()
    assert (state / "testrig-ask2-jumbo/lease").exists()


@pytest.mark.parametrize(("namespace", "protocol", "address"), [
    (namespace, protocol, address)
    for namespace in ("root", "vendor")
    for protocol in ("tcp", "udp")
    for address in ("192.0.2.1", "192.0.2.10", "[2001:db8::1]", "[2001:db8::10]")
])
def test_socket_gate_finds_foreign_flows_on_other_ports(tmp_path, namespace, protocol, address):
    script = (ROOT / "bin/testrig-paired-jumbo.sh").read_text()
    remote = script.split("<<'REMOTE'\n", 1)[1].split("\nREMOTE", 1)[0]
    ss = tmp_path / "ss"
    ss.write_text("""#!/bin/sh
case "$*" in *"src $EXPECT_ADDRESS"*) ;;
  *) exit 0 ;;
esac
case "$*" in *-tn*) protocol=tcp ;; *-uan*) protocol=udp ;; *) exit 0 ;; esac
[ "${SOCKET_NAMESPACE:-root}" = "$EXPECT_NAMESPACE" ] &&
  [ "$protocol" = "$EXPECT_PROTOCOL" ] && echo 'foreign test flow'
exit 0
""")
    ss.chmod(0o755)
    ip = tmp_path / "ip"
    ip.write_text("""#!/bin/sh
[ "$1 $2 $3" = "netns exec testns" ] || exit 1
shift 3
SOCKET_NAMESPACE=vendor exec "$@"
""")
    ip.chmod(0o755)
    env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}",
                        "EXPECT_ADDRESS": address, "EXPECT_NAMESPACE": namespace,
                        "EXPECT_PROTOCOL": protocol}
    result = subprocess.run(
        ["sh", "-s", "--", "testns", "192.0.2.1", "192.0.2.10",
         "2001:db8::1", "2001:db8::10"],
        input=remote, text=True, capture_output=True, env=env, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "foreign test flow" in result.stdout


@pytest.mark.parametrize(("family", "protocol"), [
    (socket.AF_INET, "tcp"), (socket.AF_INET, "udp"),
    (socket.AF_INET6, "tcp"), (socket.AF_INET6, "udp"),
])
def test_socket_gate_detects_real_loopback_flows(tmp_path, family, protocol):
    if not shutil.which("ss"):
        pytest.skip("ss unavailable")
    script = (ROOT / "bin/testrig-paired-jumbo.sh").read_text()
    remote = script.split("<<'REMOTE'\n", 1)[1].split("\nREMOTE", 1)[0]
    ip = tmp_path / "ip"
    ip.write_text('#!/bin/sh\nshift 3\nexec "$@"\n')
    ip.chmod(0o755)
    address = "127.0.0.42" if family == socket.AF_INET else "::1"
    sock = socket.socket(family, socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM)
    peer = listener = None
    try:
        sock.bind((address, 0))
        port = sock.getsockname()[1]
        if protocol == "tcp":
            listener = socket.socket(family, socket.SOCK_STREAM)
            listener.bind((address, 0))
            listener.listen()
            sock.connect(listener.getsockname())
            peer, _ = listener.accept()
        env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}"}
        result = subprocess.run(
            ["sh", "-s", "--", "testns", "127.0.0.42", "127.0.0.43",
             "::1", "2001:db8::10"],
            input=remote, text=True, capture_output=True, env=env, check=False,
        )
        assert result.returncode == 0, result.stderr
        assert f":{port}" in result.stdout
    finally:
        sock.close()
        if peer is not None:
            peer.close()
        if listener is not None:
            listener.close()


@pytest.mark.parametrize("family", [socket.AF_INET, socket.AF_INET6])
def test_socket_gate_detects_foreign_tcp_listeners(tmp_path, family):
    if not shutil.which("ss"):
        pytest.skip("ss unavailable")
    script = (ROOT / "bin/testrig-paired-jumbo.sh").read_text()
    remote = script.split("<<'REMOTE'\n", 1)[1].split("\nREMOTE", 1)[0]
    ip = tmp_path / "ip"
    ip.write_text('#!/bin/sh\nshift 3\nexec "$@"\n')
    ip.chmod(0o755)
    address = "127.0.0.42" if family == socket.AF_INET else "::1"
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        listener.bind((address, 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = subprocess.run(
            ["sh", "-s", "--", "testns", "127.0.0.42", "127.0.0.43",
             "::1", "2001:db8::10"],
            input=remote, text=True, capture_output=True,
            env=os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}"}, check=False,
        )
    assert result.returncode == 0, result.stderr
    assert f":{port}" in result.stdout
