"""Host unit & RFC 7011 round-trip tests for board/scripts/ask-ipfix-exporter.py."""

from __future__ import annotations

import importlib.util
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path
import struct

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
EXPORTER_PATH = ROOT / "board/scripts/ask-ipfix-exporter.py"


def _load_exporter():
    spec = importlib.util.spec_from_file_location("ask_ipfix_exporter", EXPORTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def decode_ipfix_messages(packets: list[bytes]):
    """Strict RFC 7011 IPFIX v10 decoder supporting Templates, Options Templates, and Data Sets."""
    templates: dict[int, list[tuple[int, int, int | None]]] = {}
    records: list[tuple[int, dict]] = []

    for pkt in packets:
        assert len(pkt) >= 16
        ver, msg_len, _export_t, _seq, _domain = struct.unpack("!HHIII", pkt[:16])
        assert ver == 10
        assert msg_len == len(pkt)
        off = 16
        while off < msg_len:
            set_id, set_len = struct.unpack("!HH", pkt[off : off + 4])
            assert set_len >= 4 and off + set_len <= msg_len
            body = pkt[off + 4 : off + set_len]
            off += set_len
            pos = 0
            if set_id == 2:  # Template Set
                while pos + 4 <= len(body):
                    tid, fcnt = struct.unpack("!HH", body[pos : pos + 4])
                    if tid == 0:
                        break
                    pos += 4
                    fspecs = []
                    for _ in range(fcnt):
                        ie, flen = struct.unpack("!HH", body[pos : pos + 4])
                        pos += 4
                        pen = None
                        if ie & 0x8000:
                            ie &= 0x7FFF
                            (pen,) = struct.unpack("!I", body[pos : pos + 4])
                            pos += 4
                        fspecs.append((ie, flen, pen))
                    templates[tid] = fspecs
            elif set_id == 3:  # Options Template Set
                while pos + 6 <= len(body):
                    tid, fcnt, scnt = struct.unpack("!HHH", body[pos : pos + 6])
                    if tid == 0:
                        break
                    assert scnt >= 1 and fcnt >= scnt
                    pos += 6
                    fspecs = []
                    for _ in range(fcnt):
                        ie, flen = struct.unpack("!HH", body[pos : pos + 4])
                        pos += 4
                        pen = None
                        if ie & 0x8000:
                            ie &= 0x7FFF
                            (pen,) = struct.unpack("!I", body[pos : pos + 4])
                            pos += 4
                        fspecs.append((ie, flen, pen))
                    templates[tid] = fspecs
            elif set_id >= 256:
                assert set_id in templates, f"unknown template {set_id}"
                fspecs = templates[set_id]
                rec_len = sum(flen for _, flen, _ in fspecs)
                while pos + rec_len <= len(body):
                    rec: dict = {}
                    for ie, flen, pen in fspecs:
                        raw = body[pos : pos + flen]
                        pos += flen
                        key = (pen, ie) if pen is not None else ie
                        if flen == 1:
                            val = struct.unpack("!B", raw)[0]
                        elif flen == 2:
                            val = struct.unpack("!H", raw)[0]
                        elif flen == 4 and ie in (8, 12, 225, 226) and pen is None:
                            val = str(IPv4Address(raw))
                        elif flen == 4:
                            val = struct.unpack("!I", raw)[0]
                        elif flen == 8:
                            val = struct.unpack("!Q", raw)[0]
                        elif flen == 16:
                            val = str(IPv6Address(raw))
                        else:
                            val = raw
                        rec[key] = val
                    records.append((set_id, rec))
    return templates, records


def test_ipfix_v4_nat44_v6_nat66_options_and_eof():
    m = _load_exporter()
    exp = m.AskIpfixExporter(domain_id=42, template_refresh_s=60.0)

    wall0 = 1_764_000_000_000_000_000
    mono0 = 200_000_000_000

    # Poll 1: one NAT44 flow + one NAT66 flow + fallback options.
    f4 = {
        "id": 0x1001,
        "offloaded": 1,
        "l3-proto": 0x0800,
        "l4-proto": 6,
        "src-ip": "0a630170",
        "dst-ip": "0a630271",
        "sport": 55012,
        "dport": 5201,
        "iif": 5,
        "oif": 6,
        "nat-flags": m.ASK_NATF_SNAT | m.ASK_NATF_SPAT,
        "nat-src-ip": "0a6302b9",
        "nat-sport": 41234,
        "first-seen-ns": mono0 - 5_000_000_000,
        "last-seen-ns": mono0 - 1_000_000_000,
        "packets": 1000,
        "bytes": 1_500_000,
    }
    f6 = {
        "id": 0x2002,
        "offloaded": 1,
        "l3-proto": 0x86DD,
        "l4-proto": 17,
        "src-ip": "fd990001000000000000000000000112",
        "dst-ip": "fd990002000000000000000000000113",
        "sport": 50900,
        "dport": 5201,
        "iif": 5,
        "oif": 6,
        "nat-flags": m.ASK_NATF_SNAT,
        "nat-src-ip": "fd990002000000000000000000000185",
        "first-seen-ns": mono0 - 3_000_000_000,
        "last-seen-ns": mono0 - 500_000_000,
        "packets": 500,
        "bytes": 750_000,
    }
    info = {
        "num-flows": 2,
        "max-flows": 262144,
        "fallback": {
            "admission-gate": 18,
            "policy-fenced": 1,
            "unsupported-proto": 2,
            "unsupported-action": 8,
            "neigh-unresolved": 3,
            "port-unengaged": 4,
            "max-flows": 5,
            "hw-error": 0,
        },
    }

    pkts1 = exp.step([f4, f6], info, now_wall_ns=wall0, now_mono_ns=mono0)

    # Poll 2: f4 grows; f6 disappears (EOF); non-offloaded flow is ignored.
    f4_b = dict(f4, packets=2500, bytes=3_750_000, **{"last-seen-ns": mono0 + 9_000_000_000})
    sw_only = dict(f4, id=0x9999, offloaded=0)
    pkts2 = exp.step(
        [f4_b, sw_only],
        info,
        now_wall_ns=wall0 + 10_000_000_000,
        now_mono_ns=mono0 + 10_000_000_000,
    )

    templates, recs = decode_ipfix_messages(pkts1 + pkts2)
    assert set(templates.keys()) == {256, 257, 258}

    v4_recs = [r for tid, r in recs if tid == 256]
    v6_recs = [r for tid, r in recs if tid == 257]
    opt_recs = [r for tid, r in recs if tid == 258]

    assert len(v4_recs) == 2
    # Check NAT44 fields on poll 1.
    r4_1 = v4_recs[0]
    assert r4_1[8] == "10.99.1.112"
    assert r4_1[12] == "10.99.2.113"
    assert r4_1[225] == "10.99.2.185"
    assert r4_1[226] == "10.99.2.113"
    assert r4_1[7] == 55012 and r4_1[227] == 41234
    assert r4_1[11] == 5201 and r4_1[228] == 5201
    assert r4_1[86] == 1000 and r4_1[85] == 1_500_000
    assert r4_1[2] == 1000 and r4_1[1] == 1_500_000
    assert r4_1[136] == m.FLOW_END_ACTIVE_TIMEOUT

    # Check delta & monotonic total on poll 2.
    r4_2 = v4_recs[1]
    assert r4_2[86] == 2500 and r4_2[85] == 3_750_000
    assert r4_2[2] == 1500 and r4_2[1] == 2_250_000

    # Check NAT66 + EOF on poll 2.
    assert len(v6_recs) == 2
    assert v6_recs[0][27] == "fd99:1::112"
    assert v6_recs[0][281] == "fd99:2::185"
    assert v6_recs[0][136] == m.FLOW_END_ACTIVE_TIMEOUT
    assert v6_recs[1][148] == 0x2002
    assert v6_recs[1][136] == m.FLOW_END_EOF

    # Check Options record carries sampling=0 and all 8 fallback counters.
    assert len(opt_recs) == 2
    opt = opt_recs[0]
    assert opt[149] == 42
    assert opt[305] == 0
    assert opt[(m.ASK_ENTERPRISE_NUM, 1)] == 2
    assert opt[(m.ASK_ENTERPRISE_NUM, 2)] == 262144
    assert opt[(m.ASK_ENTERPRISE_NUM, 10)] == 18
    assert opt[(m.ASK_ENTERPRISE_NUM, 13)] == 8


def test_ipfix_counter_decrease_segments_without_negative_deltas():
    m = _load_exporter()
    exp = m.AskIpfixExporter(domain_id=1, template_refresh_s=60.0)
    wall0 = 1_764_000_000_000_000_000
    mono0 = 100_000_000_000
    base = {
        "id": 0x42,
        "offloaded": 1,
        "l3-proto": 0x0800,
        "l4-proto": 6,
        "src-ip": "0a630170",
        "dst-ip": "0a630271",
        "sport": 12345,
        "dport": 443,
        "iif": 5,
        "oif": 6,
        "first-seen-ns": mono0,
        "last-seen-ns": mono0,
        "packets": 5000,
        "bytes": 7_500_000,
    }
    p1 = exp.step([base], None, now_wall_ns=wall0, now_mono_ns=mono0)
    # Simulated silicon/boot reset: counters drop below previous snapshot.
    reset_flow = dict(base, packets=120, bytes=180_000)
    p2 = exp.step([reset_flow], None, now_wall_ns=wall0 + 5_000_000_000, now_mono_ns=mono0 + 5_000_000_000)

    _, recs = decode_ipfix_messages(p1 + p2)
    v4 = [r for tid, r in recs if tid == 256]
    # Poll 1: active(5000); Poll 2: forced-end(5000, delta 0) + fresh active(120, delta 120)
    assert len(v4) == 3
    assert v4[1][136] == m.FLOW_END_FORCED
    assert v4[1][86] == 5000 and v4[1][2] == 0
    assert v4[2][136] == m.FLOW_END_ACTIVE_TIMEOUT
    assert v4[2][86] == 120 and v4[2][85] == 180_000
    assert v4[2][2] == 120 and v4[2][1] == 180_000
