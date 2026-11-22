"""Host regressions for the WireGuard parity matrix wrapper."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]

FAKE_SSH = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

command = " ".join(sys.argv[1:])
with open(os.environ["SSH_LOG"], "a") as log:
    log.write(json.dumps(command) + "\\n")
if "parameters/wgfp_rx_fast" in command:
    print("2")
elif "parameters/wgfp_rx_tail" in command:
    print("N")
elif "parameters/wgfp_rx" in command:
    print("N" if Path(os.environ["DRIFT_FILE"]).exists() or os.environ.get("WGFP_RX") == "0" else "Y")
"""

FAKE_RUNNER = """#!/usr/bin/env python3
import csv
import json
import os
from pathlib import Path
import sys

keys = ("WG_PORT", "WG_NOTRACK", "WGFP_COUNTER_LOG", "OUTER", "OUT", "J", "WG_STATE_PATH", "WG_DUT_STAT")
with open(os.environ["RUNNER_LOG"], "a") as log:
    log.write(json.dumps({key: os.environ.get(key, "") for key in keys}) + "\\n")
if os.environ.get("DRIFT_AFTER_RUN") == "1":
    Path(os.environ["DRIFT_FILE"]).write_text("changed")
if os.environ.get("FAIL_PORT") == os.environ.get("WG_PORT"):
    print("runner failed", file=sys.stderr)
    sys.exit(7)
suffix = "-eth3" if os.environ["WG_PORT"] == "eth3" else ""
rows = [
    (cell + suffix, direction, rep)
    for cell in sys.argv[2:]
    for direction in os.environ["DIRS"].split()
    for rep in range(1, int(os.environ["REPS"]) + 1)
]
if os.environ.get("DROP_LAST_ROW") == "1":
    rows.pop()
directory = Path(os.environ["J"])
directory.mkdir(parents=True, exist_ok=True)
duration = int(os.environ["DUR"])
for cell, direction, rep in rows:
    rate = 0 if os.environ.get("ZERO_RATE") == "1" else 0.75e9 if direction == "bidir" else 1.5e9
    intervals = []
    for second in range(duration):
        sample = {"start": second, "end": second + 1, "seconds": 1,
                  "bits_per_second": rate, "omitted": False}
        interval = {"sum": sample}
        if direction == "bidir":
            interval["sum_bidir_reverse"] = dict(sample)
        intervals.append(interval)
    stats = {"seconds": duration, "bits_per_second": rate}
    end = {"sum_sent": stats, "sum_received": stats}
    if direction == "bidir":
        end.update(sum_sent_bidir_reverse=stats, sum_received_bidir_reverse=stats)
    start = {"tcp_mss_default": 1368, "test_start": {
        "protocol": "TCP", "duration": duration, "num_streams": int(os.environ.get("STREAMS", "4")),
        "reverse": int(direction == "down"), "bidir": int(direction == "bidir")}}
    (directory / f"{cell}-{direction}-r{rep}.json").write_text(json.dumps({"start": start, "intervals": intervals, "end": end}))
if os.environ.get("WGFP") == "1" and os.environ.get("COUNTER_FAILURE") != "missing_log":
    logpath = Path(os.environ["WGFP_COUNTER_LOG"])
    if os.environ.get("COUNTER_FAILURE") == "rewrite_history" and logpath.exists():
        logpath.write_text(logpath.read_text().replace("sealed=0", "sealed=9", 1))
    counter_rows = list(rows)
    if os.environ.get("COUNTER_FAILURE") == "missing_cell":
        counter_rows.pop()
    if os.environ.get("COUNTER_FAILURE") == "duplicate_cell":
        counter_rows.append(counter_rows[-1])
    with logpath.open("a") as log:
        for cell, direction, rep in counter_rows:
            for phase in ("before", "after", "settle"):
                failure = os.environ.get("COUNTER_FAILURE")
                if phase == "settle" and failure == "missing_settle":
                    continue
                active = phase != "before"
                sealed = 10 if active and failure != "no_sealed" else 0
                rx = 10 if active and os.environ.get("WGFP_RX", "1") != "0" and failure not in ("no_rx", "p6_only") else 0
                ok = 10 if active and failure != "no_xmit" else 0
                frames = 20 if active and failure != "no_sink_frames" else 10
                peers = 0 if phase == "before" and failure == "peer_pending" else 1
                if phase == "after" and failure == "peer_missing":
                    peers = 0
                errs = 1 if phase == "settle" and failure == "late_sink_error" else 0
                fail = 1 if phase == "settle" and failure == "late_qman_error" else 0
                boot = "01234567-89ab-cdef-0123-456789abcdef"
                if phase == "settle" and failure == "boot_change":
                    boot = "abcdef01-2345-6789-abcd-ef0123456789"
                state = f"on sealed={sealed} rx={rx} peers={peers} install_fail=0 boot_id={boot}"
                if failure == "p6_only":
                    state += f" p6_fwd={10 if active else 0} p6_stack=0"
                elif failure == "invalid_p6":
                    state += " p6_fwd=bad"
                qman = f"sinks=1 xmit_ok={ok} xmit_fail={fail} xmit_ern=0"
                sink = f"sink0 fqid=0x2be frames={frames} errs={errs} sg=0"
                log.write(f"{cell}\\t{direction}\\t{rep}\\t{phase}\\t1\\t{state}\\t{qman}\\t{sink}\\n")
if os.environ.get("FOREIGN_OUTPUT") == "1":
    target = Path(os.environ["OUTDIR"]) / f'{os.environ["TAG"]}-185-{os.environ["WG_PORT"]}.csv'
    target.write_text("foreign\\n")
with open(os.environ["OUT"], "w", newline="") as output:
    writer = csv.writer(output)
    writer.writerow(["cell", "dir", "rep", "gbps"])
    writer.writerows((cell, direction, rep, "0" if os.environ.get("ZERO_RATE") == "1" else "1.5")
                    for cell, direction, rep in rows)
"""


@pytest.fixture
def parity(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("wg-parity.sh", "testrig-env.sh", "testrig-wgfp-modes.sh",
                 "testrig_wg_validation.py", "testrig_iperf.py", "testrig-parity-profile.sh"):
        source = ROOT / "bin" / name
        if source.exists():
            shutil.copy(source, bindir / name)
    runner = bindir / "testrig-wireguard.sh"
    runner.write_text(FAKE_RUNNER)
    runner.chmod(0o755)
    ssh = tmp_path / "ssh"
    ssh.write_text(FAKE_SSH)
    ssh.chmod(0o755)
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "TESTRIG_ENV": str(tmp_path / "no-rig-config"),
        "GEN1": "test@gen1",
        "GEN2": "test@gen2",
        "ASK2_DUT": "test@dut",
        "VENDOR_DUT": "test@dut",
        "DUT": "",
        "RIG_KEY": "",
        "OUTDIR": str(tmp_path / "results"),
        "TAG": "test",
        "PORTS": "eth3",
        "WGFP": "1",
        "WGFP_RX": "",
        "WGFP_RX_FAST": "2",
        "WGFP_RX_TAIL": "0",
        "WGFP_COUNTER_LOG": "",
        "WG_NOTRACK": "0",
        "OUTER": "4",
        "SSH_LOG": str(tmp_path / "ssh.log"),
        "RUNNER_LOG": str(tmp_path / "runner.log"),
        "DRIFT_FILE": str(tmp_path / "drift"),
    }

    def run(**extra):
        who = extra.pop("WHO", "ask2")
        result = subprocess.run(
            ["bash", str(bindir / "wg-parity.sh"), who],
            env=env | extra,
            capture_output=True,
            text=True,
            timeout=10,
        )
        def records(name):
            path = tmp_path / name
            return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return result, records("ssh.log"), records("runner.log")

    return run


def test_parity_uses_scoped_notrack_and_counter_gate_without_global_writes(parity):
    result, commands, runs = parity()
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(runs) == 1
    assert runs[0]["WG_PORT"] == "eth3"
    assert runs[0]["WG_NOTRACK"] == "3"
    assert runs[0]["WGFP_COUNTER_LOG"]
    assert commands and all("cat /sys/module/wireguard/parameters/" in c for c in commands)


def test_parity_stops_after_runner_failure(parity):
    result, _, runs = parity(PORTS="eth3 eth4", FAIL_PORT="eth3")
    assert result.returncode != 0
    assert [run["WG_PORT"] for run in runs] == ["eth3"]


def test_parity_retains_complete_zero_rate_trials(parity):
    result, _, _ = parity(ZERO_RATE="1")
    assert result.returncode == 0, result.stdout + result.stderr


def test_parity_stops_on_global_mode_drift(parity):
    result, _, runs = parity(PORTS="eth3 eth4", DRIFT_AFTER_RUN="1")
    assert result.returncode != 0
    assert [run["WG_PORT"] for run in runs] == ["eth3"]
    assert "wgfp_rx" in result.stderr


def test_parity_rejects_broad_notrack_before_remote_commands(parity):
    result, commands, runs = parity(WG_NOTRACK="2")
    assert result.returncode != 0
    assert not commands and not runs


def test_parity_fails_when_a_requested_cell_is_missing(parity):
    result, _, _ = parity(DROP_LAST_ROW="1")
    assert result.returncode != 0
    assert "missing" in result.stderr.lower()


def test_parity_isolates_raw_results_between_outer_families(parity):
    first, _, _ = parity(TAG="wg", OUTER="4")
    second, _, runs = parity(TAG="wg6", OUTER="6")
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert len(runs) == 2
    for key in ("J", "WG_STATE_PATH", "WG_DUT_STAT", "WGFP_COUNTER_LOG"):
        assert runs[0][key] and runs[1][key] and runs[0][key] != runs[1][key]


@pytest.mark.parametrize(("failure", "message"), [
    ("missing_log", "counter"),
    ("missing_cell", "missing"),
    ("missing_settle", "sequence"),
    ("duplicate_cell", "duplicate"),
    ("no_sealed", "fast-path"),
    ("no_rx", "receive"),
    ("no_xmit", "submissions"),
    ("no_sink_frames", "sink frame"),
    ("late_sink_error", "sink"),
    ("late_qman_error", "QMan"),
    ("boot_change", "boot"),
    ("invalid_p6", "counter"),
])
def test_parity_rejects_incomplete_or_failed_wgfp_evidence(parity, failure, message):
    result, _, _ = parity(COUNTER_FAILURE=failure)
    assert result.returncode != 0
    assert message.lower() in result.stderr.lower()


def test_parity_accepts_shared_history_for_repeated_cell_names(parity, tmp_path):
    log = str(tmp_path / "shared-counters.tsv")
    first, _, _ = parity(TAG="wg", OUTER="4", WGFP_COUNTER_LOG=log)
    second, _, _ = parity(TAG="wg6", OUTER="6", WGFP_COUNTER_LOG=log)
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert len(Path(log).read_text().splitlines()) == 2 * 2 * 3 * 3 * 3


def test_parity_counts_p6_activity_when_p3_counter_stays_flat(parity):
    result, _, _ = parity(COUNTER_FAILURE="p6_only")
    assert result.returncode == 0, result.stdout + result.stderr


def test_parity_allows_peer_to_appear_after_baseline(parity):
    result, _, _ = parity(COUNTER_FAILURE="peer_pending")
    assert result.returncode == 0, result.stdout + result.stderr


def test_parity_requires_peer_during_and_after_traffic(parity):
    result, _, _ = parity(COUNTER_FAILURE="peer_missing")
    assert result.returncode != 0
    assert "peer count failed" in result.stderr


def test_parity_validates_both_port_runs_with_one_counter_history(parity, tmp_path):
    log = str(tmp_path / "shared-counters.tsv")
    result, _, runs = parity(PORTS="eth3 eth4", WGFP_COUNTER_LOG=log)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [run["WG_PORT"] for run in runs] == ["eth3", "eth4"]
    assert (tmp_path / "results/test-185-eth3.csv").is_file()
    assert (tmp_path / "results/test-185-eth4.csv").is_file()


def test_parity_does_not_publish_failed_measurements(parity, tmp_path):
    result, _, _ = parity(COUNTER_FAILURE="no_sealed")
    assert result.returncode != 0
    assert not (tmp_path / "results/test-185-eth3.csv").exists()
    assert list((tmp_path / "results").glob("test-185-run-*/eth3.csv"))


def test_parity_rejects_rewritten_shared_counter_history(parity, tmp_path):
    log = str(tmp_path / "shared-counters.tsv")
    first, _, _ = parity(TAG="wg", WGFP_COUNTER_LOG=log)
    second, _, _ = parity(TAG="wg6", WGFP_COUNTER_LOG=log, COUNTER_FAILURE="rewrite_history")
    assert first.returncode == 0, first.stderr
    assert second.returncode != 0
    assert "history" in second.stderr.lower()


def test_parity_preserves_output_created_by_another_actor(parity, tmp_path):
    result, _, _ = parity(FOREIGN_OUTPUT="1")
    target = tmp_path / "results/test-185-eth3.csv"
    assert result.returncode != 0
    assert target.read_text() == "foreign\n"


def test_vendor_parity_rejects_broad_notrack_before_remote_access(parity):
    result, commands, runs = parity(
        WHO="vendor", WGFP="0", WGFP_RX="0", WGFP_RX_FAST="", WGFP_RX_TAIL="", WG_NOTRACK="2",
    )
    assert result.returncode != 0
    assert "peer-scoped NOTRACK" in result.stderr
    assert not commands and not runs


@pytest.mark.parametrize(("variable", "value"), [
    ("CELLS", "wg-v4 plain-v4"),
    ("CELLS", "wg-v4 wg-v4"),
    ("DIRS", "up up"),
    ("MODE", "udp"),
    ("WGFP", "2"),
])
def test_parity_rejects_invalid_or_duplicate_requests_before_remote_access(parity, variable, value):
    result, commands, runs = parity(**{variable: value})
    assert result.returncode != 0
    assert not commands and not runs
