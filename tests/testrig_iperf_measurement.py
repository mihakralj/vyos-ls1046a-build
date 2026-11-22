"""Host regressions for the fixed, shared TCP measurement window."""

import copy
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.host
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))


def tcp_result(*, bidir=False, reverse=False, duration=8, streams=2, rate=8e9, reverse_rate=2e9):
    def sample(start, stop, rate):
        return {"start": start, "end": stop, "seconds": stop - start,
                "bits_per_second": rate, "bytes": rate * (stop - start) / 8,
                "omitted": False, "sender": not reverse}

    intervals = []
    for second in range(duration):
        interval = {"sum": sample(second, second + 1, rate)}
        if bidir:
            interval["sum_bidir_reverse"] = sample(second, second + 1, reverse_rate)
            interval["sum_bidir_reverse"]["sender"] = False
        intervals.append(interval)
    stats = {"seconds": duration, "bits_per_second": rate, "retransmits": 0}
    end = {"sum_sent": dict(stats), "sum_received": dict(stats)}
    if bidir:
        end.update(sum_sent_bidir_reverse=dict(stats), sum_received_bidir_reverse=dict(stats))
    return {
        "start": {"tcp_mss_default": 1448, "test_start": {
            "protocol": "TCP", "duration": duration, "num_streams": streams,
            "bidir": int(bidir), "reverse": int(reverse)}},
        "intervals": intervals, "end": end,
    }


def measure(data, **kwargs):
    return importlib.import_module("testrig_iperf").measure_tcp(
        data, duration=8, steady_from=2, **kwargs)


def test_terminal_overrun_is_excluded_and_reported():
    data = tcp_result(bidir=True)
    for key in ("sum", "sum_bidir_reverse"):
        data["intervals"][-1][key].update(end=17, seconds=10, bits_per_second=0, bytes=0)
    report = measure(data, direction="bidir", streams=2)
    assert report["gbps"] == 10
    assert report["measure_to"] == 7
    assert report["directions"]["forward"]["measured_seconds"] == 5
    assert report["directions"]["forward"]["terminal_overrun_seconds"] == 9
    assert report["directions"]["forward"]["role"] == "sender"
    assert report["directions"]["reverse"]["role"] == "receiver"


@pytest.mark.parametrize("with_bytes", [True, False])
def test_nonuniform_intervals_are_duration_weighted(with_bytes):
    data = tcp_result()
    data["intervals"][2:4] = [
        {"sum": {"start": 2, "end": 2.5, "seconds": .5, "bits_per_second": 0, "bytes": 0}},
        {"sum": {"start": 2.5, "end": 4, "seconds": 1.5, "bits_per_second": 4e9,
                 "bytes": 750_000_000}},
    ]
    if not with_bytes:
        for interval in data["intervals"]:
            interval["sum"].pop("bytes")
    report = measure(data)
    assert report["gbps"] == 6
    assert report["directions"]["forward"]["bytes"] == 3_750_000_000
    assert report["directions"]["forward"]["zero_intervals"] == 1
    assert report["directions"]["forward"]["zero_seconds"] == .5


@pytest.mark.parametrize("defect", [
    "gap", "overlap", "missing", "reverse", "reverse_time", "nan", "omitted",
    "cross_start", "cross_end", "duration", "streams", "mss", "error", "truncated",
    "reverse_nan", "reverse_omitted", "missing_seconds", "direction",
])
def test_malformed_evidence_is_not_a_slow_trial(defect):
    data = tcp_result(bidir=True)
    sample = data["intervals"][3]["sum"]
    if defect == "gap":
        sample.update(start=3.1, seconds=.9)
    elif defect == "overlap":
        sample.update(start=2.9, seconds=1.1)
    elif defect == "missing":
        data["intervals"].pop(3)
    elif defect == "reverse":
        data["intervals"][3].pop("sum_bidir_reverse")
    elif defect == "reverse_time":
        data["intervals"][3]["sum_bidir_reverse"].update(start=3.1, seconds=.9)
    elif defect == "nan":
        sample["bits_per_second"] = float("nan")
    elif defect == "omitted":
        sample["omitted"] = True
    elif defect == "reverse_nan":
        data["intervals"][3]["sum_bidir_reverse"]["bits_per_second"] = float("nan")
    elif defect == "reverse_omitted":
        data["intervals"][3]["sum_bidir_reverse"]["omitted"] = True
    elif defect == "missing_seconds":
        sample.pop("seconds")
    elif defect == "direction":
        data["start"]["test_start"]["bidir"] = 0
    elif defect.startswith("cross_"):
        index = 1 if defect == "cross_start" else 6
        for key in ("sum", "sum_bidir_reverse"):
            data["intervals"][index][key].update(end=index + 1.3, seconds=1.3)
            data["intervals"][index + 1][key].update(start=index + 1.3, seconds=.7)
    elif defect == "duration":
        data["start"]["test_start"]["duration"] = 9
    elif defect == "streams":
        data["start"]["test_start"]["num_streams"] = 4
    elif defect == "mss":
        data["start"]["tcp_mss_default"] = 0
    elif defect == "error":
        data["error"] = "connection failed"
    else:
        data["intervals"].pop()
    with pytest.raises(ValueError):
        measure(data, direction="bidir", streams=2)


def test_valid_stalls_remain_in_both_direction_scores():
    data = tcp_result(bidir=True)
    for interval in data["intervals"]:
        interval["sum_bidir_reverse"].update(bits_per_second=0, bytes=0)
    report = measure(data, direction="bidir")
    assert report["gbps"] == 8
    assert report["directions"]["reverse"]["gbps"] == 0
    assert report["directions"]["reverse"]["zero_seconds"] == 5


def test_timestamp_jitter_and_explicit_window():
    data = tcp_result()
    for interval in data["intervals"]:
        interval["sum"]["start"] += .001
        interval["sum"]["end"] += .001
    report = measure(data, measure_to=6)
    assert report["directions"]["forward"]["measured_seconds"] == 4
    assert report["gbps"] == 8


def test_reverse_client_is_receiver_and_rate_fallback_is_weighted():
    data = tcp_result(reverse=True)
    for interval in data["intervals"]:
        interval["sum"].pop("bytes")
    report = measure(data, direction="down")
    assert report["directions"]["forward"]["role"] == "receiver"
    assert report["gbps"] == 8


def test_byte_count_and_rate_must_agree():
    data = copy.deepcopy(tcp_result())
    data["intervals"][3]["sum"]["bytes"] = 0
    with pytest.raises(ValueError):
        measure(data)


def test_fully_stalled_trial_is_valid():
    report = measure(tcp_result(bidir=True, rate=0, reverse_rate=0))
    assert report["gbps"] == 0
    assert all(value["zero_seconds"] == 5 for value in report["directions"].values())


def test_terminal_directions_may_finish_at_different_times():
    data = tcp_result(bidir=True)
    data["intervals"][-1]["sum"].update(end=17, seconds=10, bits_per_second=0, bytes=0)
    report = measure(data)
    assert report["gbps"] == 10
    assert report["directions"]["forward"]["terminal_overrun_seconds"] == 9
    assert report["directions"]["reverse"]["terminal_overrun_seconds"] == 0


def test_wg_validator_honors_explicit_measure_to_environment(tmp_path, monkeypatch):
    from testrig_wg_validation import iperf_rate

    data = tcp_result()
    for interval in data["intervals"][5:]:
        interval["sum"].update(bits_per_second=0, bytes=0)
    path = tmp_path / "iperf.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("MEASURE_TO", "5")
    assert iperf_rate(path, "up", 8, 2) == 8
    assert iperf_rate(path, "up", 8, 2, measure_to=7) == 4.8


@pytest.mark.parametrize("direction", ["up", "down", "bidir"])
def test_wg_tcp_parser_uses_the_same_guarded_window(tmp_path, direction):
    root = Path(__file__).resolve().parents[1]
    script = (root / "bin/testrig-wireguard.sh").read_text()
    parser = script[script.index("parse(){"):script.index("\ncpu_stats(){")]
    data = tcp_result(bidir=direction == "bidir", reverse=direction == "down")
    for sample in data["intervals"][-1].values():
        sample.update(end=17, seconds=10, bits_per_second=0, bytes=0)
    path = tmp_path / "iperf.json"
    path.write_text(json.dumps(data))
    result = subprocess.run(
        ["bash", "-c",
         f'HERE="{root / "bin"}"\nSTEADY_FROM=2 DUR=8 STREAMS=2 MODE=tcp\n'
         f'{parser}\nparse "$1" "$2"\n', "test", str(path), direction],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert float(result.stdout.split()[0]) == (10 if direction == "bidir" else 8)
