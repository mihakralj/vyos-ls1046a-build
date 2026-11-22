"""Fixed-window TCP scoring shared by both DUT adapters.

Only whole intervals inside [STEADY_FROM, MEASURE_TO] count. The default upper
boundary is DUR-1: the final reporting interval can overrun when a client stalls.
Rates are client-observed sender (forward) and receiver (reverse), not two
receiver goodputs. Zero-byte intervals are valid evidence and stay in the score.
"""

import json
import math
from pathlib import Path

JITTER = 0.02


def _number(value, label, *, positive=False):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or value < 0 or (positive and value == 0)):
        raise ValueError(f"invalid {label}")
    return float(value)


def _sample(sample, label):
    if not isinstance(sample, dict):
        raise ValueError(f"missing {label} interval")
    try:
        start = _number(sample["start"], f"{label} start")
        stop = _number(sample["end"], f"{label} end")
        seconds = _number(sample["seconds"], f"{label} seconds", positive=True)
        rate = _number(sample["bits_per_second"], f"{label} rate")
    except KeyError as exc:
        raise ValueError(f"missing {label} interval {exc}") from exc
    if stop <= start or abs(stop - start - seconds) > JITTER or sample.get("omitted"):
        raise ValueError(f"invalid or omitted {label} interval")
    if "bytes" in sample:
        size = _number(sample["bytes"], f"{label} bytes")
        if not math.isclose(size * 8 / seconds, rate, rel_tol=1e-4, abs_tol=8):
            raise ValueError(f"{label} bytes and rate disagree")
    else:
        size = _number(rate * seconds / 8, f"{label} derived bytes")
    return start, stop, seconds, rate, size


def measure_tcp(data, duration, steady_from, measure_to=None, direction=None, streams=None):
    """Validate a full requested trial and return bounded per-direction evidence.

    A 20 ms timestamp tolerance accommodates iperf reporting jitter, never
    prorates a boundary-crossing interval or substitutes a shorter window.
    """
    duration = _number(duration, "duration", positive=True)
    steady_from = _number(steady_from, "STEADY_FROM")
    measure_to = duration - 1 if measure_to is None else _number(measure_to, "MEASURE_TO")
    if not 0 <= steady_from < measure_to <= duration:
        raise ValueError("invalid scoring window: require STEADY_FROM < MEASURE_TO <= DUR")
    if not isinstance(data, dict) or data.get("error"):
        raise ValueError(f"iperf3 failed: {data.get('error') if isinstance(data, dict) else 'invalid JSON'}")
    start = data.get("start", {})
    if not isinstance(start, dict):
        raise ValueError("invalid iperf start metadata")
    mss = start.get("tcp_mss_default")
    if type(mss) is not int or not 0 < mss <= 65535:
        raise ValueError("iperf3 result is missing a valid TCP MSS")
    test = start.get("test_start", {})
    if not isinstance(test, dict):
        raise ValueError("invalid iperf trial metadata")
    count = test.get("num_streams")
    if (test.get("protocol") != "TCP" or test.get("duration") != duration
            or type(count) is not int or count < 1
            or (streams is not None and count != streams)
            or test.get("reverse", 0) not in (0, 1)
            or test.get("bidir", 0) not in (0, 1)):
        raise ValueError("iperf parameters do not match requested duration/streams/protocol")
    bidir, reverse = bool(test.get("bidir", 0)), bool(test.get("reverse", 0))
    actual_direction = "bidir" if bidir else "down" if reverse else "up"
    if (bidir and reverse) or (direction is not None and direction != actual_direction):
        raise ValueError("iperf direction does not match requested trial")
    connections = start.get("connected")
    if connections is not None and (
            not isinstance(connections, list) or len(connections) != count * (2 if bidir else 1)):
        raise ValueError("iperf connection count does not match requested streams/directions")
    intervals = data.get("intervals")
    if not isinstance(intervals, list) or not intervals:
        raise ValueError("missing iperf intervals")
    directions = {}
    timing = None
    latest = 0.0
    for label, key, role in (
            ("forward", "sum", "receiver" if reverse else "sender"),
            *([("reverse", "sum_bidir_reverse", "receiver")] if bidir else [])):
        previous = 0.0
        selected = []
        current_timing = []
        for index, interval in enumerate(intervals):
            if not isinstance(interval, dict):
                raise ValueError("invalid iperf interval")
            sample = interval.get(key)
            left, right, seconds, rate, size = _sample(sample, label)
            if abs(left - previous) > JITTER:
                raise ValueError(f"{label}: gapped or overlapping intervals")
            if right > duration + JITTER and index != len(intervals) - 1:
                raise ValueError(f"{label}: nonterminal interval exceeds requested duration")
            if ("sender" in sample and
                    (sample["sender"] not in (True, False) or bool(sample["sender"]) != (role == "sender"))):
                raise ValueError(f"{label}: unexpected sender/receiver direction")
            if timing is not None and left < measure_to - JITTER and right > steady_from + JITTER and any(
                    abs(a - b) > JITTER for a, b in zip((left, right), timing[index])):
                raise ValueError("reverse interval timing differs from forward")
            current_timing.append((left, right))
            for boundary in (steady_from, measure_to):
                if left < boundary - JITTER and right > boundary + JITTER:
                    raise ValueError(f"{label}: interval crosses scoring boundary {boundary}")
            if left >= steady_from - JITTER and right <= measure_to + JITTER:
                selected.append((left, right, seconds, rate, size))
            previous = right
        if (previous < duration - JITTER or not selected
                or abs(selected[0][0] - steady_from) > JITTER
                or abs(selected[-1][1] - measure_to) > JITTER):
            raise ValueError(f"{label}: incomplete steady window or truncated requested trial")
        seconds = _number(sum(item[2] for item in selected), f"{label} measured seconds", positive=True)
        if abs(seconds - (measure_to - steady_from)) > 2 * JITTER:
            raise ValueError(f"{label}: incomplete steady window duration")
        size = _number(sum(item[4] for item in selected), f"{label} measured bytes")
        directions[label] = {
            "role": role, "gbps": _number(size * 8 / seconds / 1e9, f"{label} measured rate"), "bytes": size,
            "measured_seconds": seconds, "intervals": len(selected),
            "zero_intervals": sum(item[3] == 0 for item in selected),
            "zero_seconds": sum(item[2] for item in selected if item[3] == 0),
            "terminal_overrun_seconds": max(0.0, previous - duration),
            "trial_end": previous,
            "excluded_intervals": len(intervals) - len(selected),
        }
        timing = current_timing
        latest = max(latest, previous)
    if not bidir and any("sum_bidir_reverse" in interval for interval in intervals):
        raise ValueError("unexpected reverse intervals in unidirectional trial")
    fields = ["sum_sent", "sum_received"]
    if bidir:
        fields += ["sum_sent_bidir_reverse", "sum_received_bidir_reverse"]
    end = data.get("end", {})
    if not isinstance(end, dict):
        raise ValueError("invalid iperf end summaries")
    for field in fields:
        summary = end.get(field, {})
        if not isinstance(summary, dict):
            raise ValueError(f"invalid {field} summary")
        seconds = _number(summary.get("seconds"), f"{field} seconds", positive=True)
        _number(summary.get("bits_per_second"), f"{field} rate")
        if not duration - JITTER <= seconds <= latest + JITTER:
            raise ValueError(f"incomplete or inconsistent {field} duration")
    return {
        "valid": True, "duration": duration, "steady_from": steady_from,
        "measure_to": measure_to, "timestamp_tolerance_seconds": JITTER,
        "streams": count, "direction": actual_direction, "tcp_mss": mss,
        "rate_basis": "client sender plus client receiver" if bidir else f"client {directions['forward']['role']}",
        "gbps": sum(value["gbps"] for value in directions.values()),
        "bytes": sum(value["bytes"] for value in directions.values()),
        "directions": directions,
    }


def report_json(path, report):
    """Keep the original iperf JSON and an adjacent machine-readable score."""
    Path(path).with_suffix(".measurement.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n")
