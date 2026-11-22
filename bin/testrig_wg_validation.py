"""Shared validation for WireGuard benchmark artifacts."""

import hashlib
import json
import os
import re
from pathlib import Path

from testrig_iperf import measure_tcp


def wgfp_counter_snapshot(path):
    log = Path(path)
    if log.is_symlink():
        raise ValueError("WGFP counter log must not be a symlink")
    try:
        content = log.read_bytes()
    except FileNotFoundError:
        content = b""
    if content and not content.endswith(b"\n"):
        raise ValueError("WGFP counter history has a truncated sample")
    return len(content), hashlib.sha256(content).hexdigest()


def _wgfp_values(text, required, optional=()):
    values = {}
    for field in text.split():
        key, sep, value = field.partition("=")
        if key not in required and key not in optional:
            continue
        if key in values or not sep or not value.isascii() or not value.isdecimal():
            raise ValueError(f"invalid or duplicate WGFP counter {key}")
        values[key] = int(value)
    if required - values.keys():
        raise ValueError(f"missing WGFP counters: {sorted(required - values.keys())}")
    return values


def _wgfp_sinks(text, expected):
    sinks = {}
    for line in (text.split("|") if text else []):
        fields = line.split()
        if (not fields or not fields[0].startswith("sink")
                or not fields[0][4:].isascii() or not fields[0][4:].isdecimal()
                or fields[0] in sinks):
            raise ValueError("invalid or duplicate WGFP sink")
        fqids = [field[5:] for field in fields[1:] if field.startswith("fqid=")]
        if len(fqids) != 1 or not re.fullmatch(r"0x[0-9a-fA-F]+", fqids[0]):
            raise ValueError(f"{fields[0]}: invalid or duplicate FQID")
        counters = _wgfp_values(" ".join(fields[1:]), {"frames", "errs", "sg"})
        sinks[fields[0]] = (int(fqids[0], 16), counters)
    if len(sinks) != expected or len({value[0] for value in sinks.values()}) != expected:
        raise ValueError("incomplete or duplicate WGFP sink FQIDs")
    return sinks


def _wgfp_sample(state, qman, sink_data):
    if not state.startswith("on "):
        raise ValueError("WGFP is not bound in counter sample")
    values = _wgfp_values(state, {"sealed", "rx", "peers", "install_fail"},
                          {"p6_fwd", "p6_stack"})
    boots = [field[8:] for field in state.split() if field.startswith("boot_id=")]
    if len(boots) != 1 or not re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boots[0]):
        raise ValueError("invalid WGFP boot ID")
    values["boot_id"] = boots[0]
    values["rx"] += values.get("p6_fwd", 0) + values.get("p6_stack", 0)
    values.update(_wgfp_values(qman, {"sinks", "xmit_ok", "xmit_fail", "xmit_ern"}))
    values["sink_data"] = _wgfp_sinks(sink_data, values["sinks"])
    return values


def _wgfp_sink_transition(label, old, new):
    old_sinks, new_sinks = old["sink_data"], new["sink_data"]
    if (not old_sinks.keys() <= new_sinks.keys()
            or any(old_sinks[name][0] != new_sinks[name][0] for name in old_sinks)):
        raise ValueError(f"{label}: sink FQ identities changed")
    deltas = []
    zero = {"frames": 0, "errs": 0, "sg": 0}
    for name in new_sinks:
        before, after = old_sinks.get(name, (0, zero))[1], new_sinks[name][1]
        if after["frames"] < before["frames"]:
            raise ValueError(f"{label}: sink frame counter decreased")
        if any(after[field] != before[field] for field in ("errs", "sg")):
            raise ValueError(f"{label}: sink errors or SG drops changed")
        deltas.append(after["frames"] - before["frames"])
    return deltas


def _validate_wgfp_group(key, samples, peers, rx_enabled):
    cell, direction, rep = key
    label = f"{cell} {direction} r{rep}"
    before, after, settle = (samples[phase] for phase in ("before", "after", "settle"))
    # A fresh tunnel can have no active WGFP peer until the measured traffic starts.
    if any(samples[phase]["peers"] < peers for phase in ("after", "settle")):
        raise ValueError(f"{label}: peer count failed")
    for left, right in ((before, after), (after, settle)):
        if right["install_fail"] != left["install_fail"]:
            raise ValueError(f"{label}: installation failed")
        if any(right[name] < left[name] for name in ("sealed", "rx", "xmit_ok")):
            raise ValueError(f"{label}: fast-path counter decreased")
        if not rx_enabled and right["rx"] != left["rx"]:
            raise ValueError(f"{label}: P3 receive counter advanced")
    if direction != "down" and after["sealed"] <= before["sealed"]:
        raise ValueError(f"{label}: fast-path counters did not advance")
    if rx_enabled and direction != "up" and after["rx"] <= before["rx"]:
        raise ValueError(f"{label}: P3 receive counter did not advance")
    if direction != "down" and after["xmit_ok"] <= before["xmit_ok"]:
        raise ValueError(f"{label}: QMan submissions did not advance")
    if not any(_wgfp_sink_transition(label, before, after)):
        raise ValueError(f"{label}: sink frame counters did not advance")
    _wgfp_sink_transition(label, after, settle)


def validate_wgfp_counters(path, expected, rx_enabled, start=0, prefix_digest=None):
    """Validate a complete counter history and the requested append-only groups."""
    budget_text = os.environ.get("WGFP_XMIT_FAIL_BUDGET", "0")
    if not budget_text.isascii() or not budget_text.isdecimal():
        raise ValueError("WGFP_XMIT_FAIL_BUDGET must be a non-negative integer")
    fail_budget = int(budget_text, 10)
    log = Path(path)
    if log.is_symlink():
        raise ValueError("WGFP counter log must not be a symlink")
    content = log.read_bytes()
    if not content:
        raise ValueError("missing WGFP counters")
    if (start < 0 or start > len(content) or (start and content[start - 1:start] != b"\n")
            or (prefix_digest is not None
                and hashlib.sha256(content[:start]).hexdigest() != prefix_digest)):
        raise ValueError("WGFP counter history changed during the runner")
    if not content.endswith(b"\n"):
        raise ValueError("invalid WGFP counter sequence: truncated sample")
    previous = None
    expected_phase = "before"
    active_key = None
    active_samples = {}
    seen = set()
    offset = 0
    for raw in content.splitlines(keepends=True):
        if offset < start < offset + len(raw) or (offset == start and expected_phase != "before"):
            raise ValueError("WGFP counter history ends inside a sample sequence")
        current = offset >= start
        offset += len(raw)
        cell, direction, rep_text, phase, attempt, state, qman, sink_data = raw.decode("utf-8").rstrip("\n").split("\t")
        if (not cell or direction not in ("up", "down", "bidir") or not rep_text.isascii()
                or not rep_text.isdecimal() or int(rep_text) < 1):
            raise ValueError("invalid WGFP counter sequence")
        key = (cell, direction, int(rep_text))
        if attempt != "1":
            raise ValueError(f"{cell} {direction} r{rep_text}: WGFP attempt was retried")
        if phase != expected_phase or (phase != "before" and key != active_key):
            raise ValueError(f"{cell} {direction} r{rep_text}: invalid WGFP counter sequence")
        if phase == "before":
            active_key, expected_phase = key, "after"
            active_samples = {}
        elif phase == "after":
            expected_phase = "settle"
        else:
            active_key, expected_phase = None, "before"
        sample = _wgfp_sample(state, qman, sink_data)
        if previous:
            if sample["boot_id"] != previous["boot_id"]:
                raise ValueError("DUT boot changed between WGFP samples")
            # Inside a cell xmit_fail may rise by the opt-in budget; between cells nothing may change.
            base = previous if phase == "before" else active_samples["before"]
            fail_delta = sample["xmit_fail"] - base["xmit_fail"]
            if (sample["xmit_ern"] != base["xmit_ern"]
                    or not 0 <= fail_delta <= (0 if phase == "before" else fail_budget)):
                raise ValueError(f"{cell} {direction} r{rep_text}: QMan failures or rejections changed")
        previous = sample
        active_samples[phase] = sample
        if current and phase == "settle":
            if key in seen:
                raise ValueError(f"{key}: duplicate WGFP counter group")
            if key not in expected:
                raise ValueError(f"{key}: unexpected WGFP counter group")
            _validate_wgfp_group(key, active_samples, expected[key], rx_enabled)
            seen.add(key)
    if expected_phase != "before":
        raise ValueError("invalid WGFP counter sequence: final sample is not settled")
    if seen != expected.keys():
        raise ValueError(f"missing settled WGFP counters: {sorted(expected.keys() - seen)}")


def iperf_rate(path, direction, duration, steady_from, measure_to=None, streams=None):
    data = json.loads(Path(path).read_text())
    if measure_to is None and os.environ.get("MEASURE_TO"):
        measure_to = float(os.environ["MEASURE_TO"])
    try:
        return measure_tcp(data, duration, steady_from, measure_to,
                           direction=direction, streams=streams)["gbps"]
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc
