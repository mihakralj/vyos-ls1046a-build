#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
ASK2 RFC 7011 IPFIX v10 exporter (Phase 1 Option B MVP).

Polls ask.ko's generic-netlink family (dump-flows + get-info) via the vendored
YNL client library (/usr/share/ynl/pyynl) and exports standard RFC 7011 IPFIX
UDP datagrams containing:
  - Template 256: IPv4 flows (original 5-tuple, post-NAT44/PAT fields, IIF/OIF,
    host-observed start/end timestamps, absolute + delta packets/bytes, end
    reason, flow cookie ID).
  - Template 257: IPv6 flows (same, with 16-byte IPv6 + post-NAT66 addresses).
  - Options Template 258: observation domain coverage options (sampling interval
    = 0, active/max flows, and the 8 per-cause SW-fallback counters).

Read-only with respect to FMan silicon. Requires CAP_NET_ADMIN in init_net.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address
import logging
import signal
import socket
import struct
import sys
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

IPFIX_VERSION = 10
SET_ID_TEMPLATE = 2
SET_ID_OPTIONS_TEMPLATE = 3
TMPL_ID_IPV4 = 256
TMPL_ID_IPV6 = 257
TMPL_ID_OPTIONS = 258

# RFC 5612 documentation Private Enterprise Number for ASK2-specific options IEs.
ASK_ENTERPRISE_NUM = 32473
ENTERPRISE_BIT = 0x8000

# RFC 7011 flowEndReason values
FLOW_END_ACTIVE_TIMEOUT = 0x02
FLOW_END_EOF = 0x03
FLOW_END_FORCED = 0x04

# ASK_NATF_* bits (include/uapi/linux/ask/ask.h)
ASK_NATF_SNAT = 1 << 0
ASK_NATF_DNAT = 1 << 1
ASK_NATF_SPAT = 1 << 2
ASK_NATF_DPAT = 1 << 3

ETH_P_IP = 0x0800
ETH_P_IPV6 = 0x86DD

# Max UDP payload per IPFIX Message (safe below 1500 B MTU).
MAX_IPFIX_PAYLOAD = 1400

# Template field definitions: (ie_id, length, enterprise_num_or_None)
IPV4_FIELDS: Tuple[Tuple[int, int, Optional[int]], ...] = (
    (8, 4, None),     # sourceIPv4Address
    (12, 4, None),    # destinationIPv4Address
    (7, 2, None),     # sourceTransportPort
    (11, 2, None),    # destinationTransportPort
    (4, 1, None),     # protocolIdentifier
    (10, 4, None),    # ingressInterface
    (14, 4, None),    # egressInterface
    (225, 4, None),   # postNATSourceIPv4Address
    (226, 4, None),   # postNATDestinationIPv4Address
    (227, 2, None),   # postNAPTSourceTransportPort
    (228, 2, None),   # postNAPTDestinationTransportPort
    (152, 8, None),   # flowStartMilliseconds
    (153, 8, None),   # flowEndMilliseconds
    (86, 8, None),    # packetTotalCount
    (85, 8, None),    # octetTotalCount
    (2, 8, None),     # packetDeltaCount
    (1, 8, None),     # octetDeltaCount
    (136, 1, None),   # flowEndReason
    (148, 8, None),   # flowId
)

IPV6_FIELDS: Tuple[Tuple[int, int, Optional[int]], ...] = (
    (27, 16, None),   # sourceIPv6Address
    (28, 16, None),   # destinationIPv6Address
    (7, 2, None),     # sourceTransportPort
    (11, 2, None),    # destinationTransportPort
    (4, 1, None),     # protocolIdentifier
    (10, 4, None),    # ingressInterface
    (14, 4, None),    # egressInterface
    (281, 16, None),  # postNATSourceIPv6Address
    (282, 16, None),  # postNATDestinationIPv6Address
    (227, 2, None),   # postNAPTSourceTransportPort
    (228, 2, None),   # postNAPTDestinationTransportPort
    (152, 8, None),   # flowStartMilliseconds
    (153, 8, None),   # flowEndMilliseconds
    (86, 8, None),    # packetTotalCount
    (85, 8, None),    # octetTotalCount
    (2, 8, None),     # packetDeltaCount
    (1, 8, None),     # octetDeltaCount
    (136, 1, None),   # flowEndReason
    (148, 8, None),   # flowId
)

OPTIONS_SCOPE_FIELDS: Tuple[Tuple[int, int, Optional[int]], ...] = (
    (149, 4, None),   # observationDomainId
)

OPTIONS_DATA_FIELDS: Tuple[Tuple[int, int, Optional[int]], ...] = (
    (305, 4, None),                       # samplingPacketInterval (0 = unsampled)
    (1, 4, ASK_ENTERPRISE_NUM),           # askActiveFlows
    (2, 4, ASK_ENTERPRISE_NUM),           # askMaxFlows
    (10, 8, ASK_ENTERPRISE_NUM),          # askFallbackAdmissionGate
    (11, 8, ASK_ENTERPRISE_NUM),          # askFallbackPolicyFenced
    (12, 8, ASK_ENTERPRISE_NUM),          # askFallbackUnsupportedProto
    (13, 8, ASK_ENTERPRISE_NUM),          # askFallbackUnsupportedAction
    (14, 8, ASK_ENTERPRISE_NUM),          # askFallbackNeighUnresolved
    (15, 8, ASK_ENTERPRISE_NUM),          # askFallbackPortUnengaged
    (16, 8, ASK_ENTERPRISE_NUM),          # askFallbackMaxFlows
    (17, 8, ASK_ENTERPRISE_NUM),          # askFallbackHwError
)

FALLBACK_KEYS: Tuple[str, ...] = (
    "admission-gate",
    "policy-fenced",
    "unsupported-proto",
    "unsupported-action",
    "neigh-unresolved",
    "port-unengaged",
    "max-flows",
    "hw-error",
)


def _get(d: dict, *names: str, default: Any = None) -> Any:
    for n in names:
        for k in (n, n.replace("-", "_"), n.replace("_", "-")):
            if k in d:
                return d[k]
    return default


def _parse_ip_bytes(val: Any, expected_len: int) -> bytes:
    if isinstance(val, (bytes, bytearray)):
        raw = bytes(val)
    elif isinstance(val, str):
        try:
            raw = bytes.fromhex(val)
        except ValueError:
            raw = (
                IPv6Address(val).packed
                if expected_len == 16
                else IPv4Address(val).packed
            )
    else:
        raw = b"\x00" * expected_len
    if len(raw) == expected_len:
        return raw
    if len(raw) > expected_len:
        return raw[:expected_len]
    return raw.ljust(expected_len, b"\x00")


def _encode_field_specs(
    fields: Iterable[Tuple[int, int, Optional[int]]]
) -> bytes:
    out = bytearray()
    for ie_id, length, enterprise in fields:
        if enterprise is not None:
            out += struct.pack("!HHI", ie_id | ENTERPRISE_BIT, length, enterprise)
        else:
            out += struct.pack("!HH", ie_id, length)
    return bytes(out)


def _wrap_set(set_id: int, payload: bytes) -> bytes:
    pad = (-len(payload)) % 4
    total_len = 4 + len(payload) + pad
    return struct.pack("!HH", set_id, total_len) + payload + (b"\x00" * pad)


def build_template_set() -> bytes:
    body = bytearray()
    for tmpl_id, fields in ((TMPL_ID_IPV4, IPV4_FIELDS), (TMPL_ID_IPV6, IPV6_FIELDS)):
        body += struct.pack("!HH", tmpl_id, len(fields))
        body += _encode_field_specs(fields)
    return _wrap_set(SET_ID_TEMPLATE, bytes(body))


def build_options_template_set() -> bytes:
    total_fields = len(OPTIONS_SCOPE_FIELDS) + len(OPTIONS_DATA_FIELDS)
    body = bytearray()
    body += struct.pack("!HHH", TMPL_ID_OPTIONS, total_fields, len(OPTIONS_SCOPE_FIELDS))
    body += _encode_field_specs(OPTIONS_SCOPE_FIELDS)
    body += _encode_field_specs(OPTIONS_DATA_FIELDS)
    return _wrap_set(SET_ID_OPTIONS_TEMPLATE, bytes(body))


def build_ipfix_message(
    sets: List[bytes],
    export_time_s: int,
    seq_num: int,
    domain_id: int,
) -> bytes:
    payload = b"".join(sets)
    total_len = 16 + len(payload)
    hdr = struct.pack(
        "!HHIII",
        IPFIX_VERSION,
        total_len,
        export_time_s & 0xFFFFFFFF,
        seq_num & 0xFFFFFFFF,
        domain_id & 0xFFFFFFFF,
    )
    return hdr + payload


@dataclass
class TrackedFlow:
    flow_id: int
    is_v6: bool
    src_ip: bytes
    dst_ip: bytes
    sport: int
    dport: int
    proto: int
    iif: int
    oif: int
    nat_src_ip: bytes
    nat_dst_ip: bytes
    nat_sport: int
    nat_dport: int
    first_seen_ns: int
    last_seen_ns: int
    packets: int
    bytes_total: int


def _normalize_flow(raw: dict) -> Optional[TrackedFlow]:
    if not _get(raw, "offloaded", default=1):
        return None
    flow_id = _get(raw, "id")
    if flow_id is None:
        return None
    l3 = _get(raw, "l3-proto", default=ETH_P_IP)
    is_v6 = int(l3) == ETH_P_IPV6
    iplen = 16 if is_v6 else 4

    src_ip = _parse_ip_bytes(_get(raw, "src-ip"), iplen)
    dst_ip = _parse_ip_bytes(_get(raw, "dst-ip"), iplen)
    sport = int(_get(raw, "sport", default=0) or 0) & 0xFFFF
    dport = int(_get(raw, "dport", default=0) or 0) & 0xFFFF
    proto = int(_get(raw, "l4-proto", default=0) or 0) & 0xFF
    iif = int(_get(raw, "iif", default=0) or 0) & 0xFFFFFFFF
    oif = int(_get(raw, "oif", default=0) or 0) & 0xFFFFFFFF

    nat_flags = int(_get(raw, "nat-flags", default=0) or 0)
    nat_src_ip = (
        _parse_ip_bytes(_get(raw, "nat-src-ip"), iplen)
        if (nat_flags & ASK_NATF_SNAT) and _get(raw, "nat-src-ip") is not None
        else src_ip
    )
    nat_dst_ip = (
        _parse_ip_bytes(_get(raw, "nat-dst-ip"), iplen)
        if (nat_flags & ASK_NATF_DNAT) and _get(raw, "nat-dst-ip") is not None
        else dst_ip
    )
    nat_sport = (
        int(_get(raw, "nat-sport", default=sport) or 0) & 0xFFFF
        if (nat_flags & ASK_NATF_SPAT)
        else sport
    )
    nat_dport = (
        int(_get(raw, "nat-dport", default=dport) or 0) & 0xFFFF
        if (nat_flags & ASK_NATF_DPAT)
        else dport
    )

    first_seen_ns = int(_get(raw, "first-seen-ns", default=0) or 0)
    last_seen_ns = int(_get(raw, "last-seen-ns", default=0) or 0)
    packets = int(_get(raw, "packets", default=0) or 0)
    bytes_total = int(_get(raw, "bytes", default=0) or 0)

    return TrackedFlow(
        flow_id=int(flow_id) & 0xFFFFFFFFFFFFFFFF,
        is_v6=is_v6,
        src_ip=src_ip,
        dst_ip=dst_ip,
        sport=sport,
        dport=dport,
        proto=proto,
        iif=iif,
        oif=oif,
        nat_src_ip=nat_src_ip,
        nat_dst_ip=nat_dst_ip,
        nat_sport=nat_sport,
        nat_dport=nat_dport,
        first_seen_ns=first_seen_ns,
        last_seen_ns=last_seen_ns,
        packets=packets,
        bytes_total=bytes_total,
    )


def _mono_to_epoch_ms(mono_ns: int, offset_ns: int, fallback_ms: int) -> int:
    if not mono_ns:
        return fallback_ms
    val = (mono_ns + offset_ns) // 1_000_000
    return max(0, val)


def encode_flow_record(
    f: TrackedFlow,
    d_packets: int,
    d_bytes: int,
    end_reason: int,
    mono_to_wall_offset_ns: int,
    now_ms: int,
) -> bytes:
    start_ms = _mono_to_epoch_ms(f.first_seen_ns, mono_to_wall_offset_ns, now_ms)
    end_ms = _mono_to_epoch_ms(f.last_seen_ns, mono_to_wall_offset_ns, start_ms)
    if end_ms < start_ms:
        end_ms = start_ms
    if f.is_v6:
        return struct.pack(
            "!16s16sHHBII16s16sHHQQQQQQBQ",
            f.src_ip,
            f.dst_ip,
            f.sport,
            f.dport,
            f.proto,
            f.iif,
            f.oif,
            f.nat_src_ip,
            f.nat_dst_ip,
            f.nat_sport,
            f.nat_dport,
            start_ms,
            end_ms,
            f.packets,
            f.bytes_total,
            d_packets,
            d_bytes,
            end_reason,
            f.flow_id,
        )
    return struct.pack(
        "!4s4sHHBII4s4sHHQQQQQQBQ",
        f.src_ip,
        f.dst_ip,
        f.sport,
        f.dport,
        f.proto,
        f.iif,
        f.oif,
        f.nat_src_ip,
        f.nat_dst_ip,
        f.nat_sport,
        f.nat_dport,
        start_ms,
        end_ms,
        f.packets,
        f.bytes_total,
        d_packets,
        d_bytes,
        end_reason,
        f.flow_id,
    )


def encode_options_record(domain_id: int, info: dict) -> bytes:
    num_flows = int(_get(info, "num-flows", default=0) or 0) & 0xFFFFFFFF
    max_flows = int(_get(info, "max-flows", default=0) or 0) & 0xFFFFFFFF
    fb = _get(info, "fallback", default={}) or {}
    fb_vals = [int(_get(fb, k, default=0) or 0) & 0xFFFFFFFFFFFFFFFF for k in FALLBACK_KEYS]
    return struct.pack(
        "!IIII8Q",
        domain_id & 0xFFFFFFFF,
        0,  # samplingPacketInterval = 0 (unsampled full hardware flow accounting)
        num_flows,
        max_flows,
        *fb_vals,
    )


class AskIpfixExporter:
    """Stateful RFC 7011 IPFIX exporter over ASK2 YNL snapshots."""

    def __init__(
        self,
        domain_id: int = 1,
        template_refresh_s: float = 60.0,
    ) -> None:
        self.domain_id = domain_id
        self.template_refresh_s = template_refresh_s
        self.seq_num = 0
        self.last_template_s = -1e9
        self.active: Dict[int, TrackedFlow] = {}

    def _pack_packets(
        self,
        v4_recs: List[bytes],
        v6_recs: List[bytes],
        opt_recs: List[bytes],
        include_templates: bool,
        export_time_s: int,
    ) -> List[bytes]:
        packets: List[bytes] = []
        if include_templates:
            t_sets = [build_template_set(), build_options_template_set()]
            packets.append(
                build_ipfix_message(t_sets, export_time_s, self.seq_num, self.domain_id)
            )

        def _flush_typed(set_id: int, recs: List[bytes]) -> None:
            if not recs:
                return
            chunk: List[bytes] = []
            chunk_bytes = 0
            max_set_body = MAX_IPFIX_PAYLOAD - 24
            for rec in recs:
                if chunk and chunk_bytes + len(rec) > max_set_body:
                    data_set = _wrap_set(set_id, b"".join(chunk))
                    packets.append(
                        build_ipfix_message(
                            [data_set], export_time_s, self.seq_num, self.domain_id
                        )
                    )
                    self.seq_num = (self.seq_num + len(chunk)) & 0xFFFFFFFF
                    chunk = []
                    chunk_bytes = 0
                chunk.append(rec)
                chunk_bytes += len(rec)
            if chunk:
                data_set = _wrap_set(set_id, b"".join(chunk))
                packets.append(
                    build_ipfix_message(
                        [data_set], export_time_s, self.seq_num, self.domain_id
                    )
                )
                self.seq_num = (self.seq_num + len(chunk)) & 0xFFFFFFFF

        _flush_typed(TMPL_ID_IPV4, v4_recs)
        _flush_typed(TMPL_ID_IPV6, v6_recs)
        _flush_typed(TMPL_ID_OPTIONS, opt_recs)
        return packets

    def step(
        self,
        raw_flows: Iterable[dict],
        info: Optional[dict] = None,
        now_wall_ns: Optional[int] = None,
        now_mono_ns: Optional[int] = None,
    ) -> List[bytes]:
        """Process one dump-flows + get-info snapshot and return IPFIX UDP payloads."""
        if now_wall_ns is None:
            now_wall_ns = time.time_ns()
        if now_mono_ns is None:
            now_mono_ns = time.monotonic_ns()
        now_s = now_wall_ns / 1e9
        export_time_s = int(now_wall_ns // 1_000_000_000)
        now_ms = int(now_wall_ns // 1_000_000)
        offset_ns = now_wall_ns - now_mono_ns

        include_templates = (now_s - self.last_template_s) >= self.template_refresh_s
        if include_templates:
            self.last_template_s = now_s

        current: Dict[int, TrackedFlow] = {}
        for item in raw_flows:
            if isinstance(item, dict) and isinstance(item.get("flow"), dict):
                item = item["flow"]
            tf = _normalize_flow(item)
            if tf is not None:
                current[tf.flow_id] = tf

        v4_recs: List[bytes] = []
        v6_recs: List[bytes] = []

        # 1. Emit end-of-flow records for flows that disappeared since last poll.
        gone_ids = [fid for fid in self.active if fid not in current]
        for fid in gone_ids:
            prev = self.active.pop(fid)
            rec = encode_flow_record(
                prev, 0, 0, FLOW_END_EOF, offset_ns, now_ms
            )
            (v6_recs if prev.is_v6 else v4_recs).append(rec)

        # 2. Emit active records (handling counter resets via segmentation).
        for fid, cur in current.items():
            prev = self.active.get(fid)
            if prev is None:
                d_pkts = cur.packets
                d_bytes = cur.bytes_total
            elif cur.packets < prev.packets or cur.bytes_total < prev.bytes_total:
                # Counter decrease (silicon reset / re-insert / HA failover):
                # close the old segment and start a fresh one; never emit negative deltas.
                close_rec = encode_flow_record(
                    prev, 0, 0, FLOW_END_FORCED, offset_ns, now_ms
                )
                (v6_recs if prev.is_v6 else v4_recs).append(close_rec)
                d_pkts = cur.packets
                d_bytes = cur.bytes_total
            else:
                d_pkts = cur.packets - prev.packets
                d_bytes = cur.bytes_total - prev.bytes_total

            rec = encode_flow_record(
                cur, d_pkts, d_bytes, FLOW_END_ACTIVE_TIMEOUT, offset_ns, now_ms
            )
            (v6_recs if cur.is_v6 else v4_recs).append(rec)
            self.active[fid] = cur

        opt_recs: List[bytes] = []
        if info is not None:
            opt_recs.append(encode_options_record(self.domain_id, info))

        return self._pack_packets(
            v4_recs, v6_recs, opt_recs, include_templates, export_time_s
        )

    def flush_all(
        self,
        now_wall_ns: Optional[int] = None,
        now_mono_ns: Optional[int] = None,
    ) -> List[bytes]:
        """Emit forced-end records for all remaining tracked flows on shutdown."""
        if not self.active:
            return []
        if now_wall_ns is None:
            now_wall_ns = time.time_ns()
        if now_mono_ns is None:
            now_mono_ns = time.monotonic_ns()
        export_time_s = int(now_wall_ns // 1_000_000_000)
        now_ms = int(now_wall_ns // 1_000_000)
        offset_ns = now_wall_ns - now_mono_ns
        v4_recs: List[bytes] = []
        v6_recs: List[bytes] = []
        for prev in self.active.values():
            rec = encode_flow_record(
                prev, 0, 0, FLOW_END_FORCED, offset_ns, now_ms
            )
            (v6_recs if prev.is_v6 else v4_recs).append(rec)
        self.active.clear()
        return self._pack_packets(v4_recs, v6_recs, [], False, export_time_s)


class YnlAskSource:
    """In-process YNL source reading ask.ko without per-poll subprocess forks."""

    def __init__(
        self,
        spec_path: str = "/usr/share/ynl/specs/ask.yaml",
        pyynl_dir: str = "/usr/share/ynl/pyynl",
    ) -> None:
        if pyynl_dir not in sys.path:
            sys.path.insert(0, pyynl_dir)
        from lib import YnlFamily  # type: ignore

        self._ynl = YnlFamily(spec_path, "")

    def fetch(self) -> Tuple[List[dict], dict]:
        flows = self._ynl.dump("dump-flows", {}) or []
        info_raw = self._ynl.do("get-info", {}) or {}
        if isinstance(info_raw, dict) and isinstance(info_raw.get("info"), dict):
            info = info_raw["info"]
        else:
            info = info_raw if isinstance(info_raw, dict) else {}
        return list(flows), info


def run_exporter(
    collector_host: str,
    collector_port: int,
    interval_s: float,
    template_refresh_s: float,
    domain_id: int,
    count: int,
    fetch_fn: Callable[[], Tuple[List[dict], dict]],
) -> int:
    af = socket.AF_INET6 if ":" in collector_host else socket.AF_INET
    sock = socket.socket(af, socket.SOCK_DGRAM)
    exporter = AskIpfixExporter(
        domain_id=domain_id, template_refresh_s=template_refresh_s
    )
    stop = False

    def _handle_sig(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    sent_msgs = 0
    iters = 0
    try:
        while not stop:
            flows, info = fetch_fn()
            pkts = exporter.step(flows, info)
            for pkt in pkts:
                sock.sendto(pkt, (collector_host, collector_port))
                sent_msgs += 1
            iters += 1
            if count > 0 and iters >= count:
                break
            deadline = time.monotonic() + interval_s
            while not stop and time.monotonic() < deadline:
                time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    finally:
        for pkt in exporter.flush_all():
            sock.sendto(pkt, (collector_host, collector_port))
            sent_msgs += 1
        sock.close()
    logging.info("exported %d IPFIX message(s) in %d poll(s)", sent_msgs, iters)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ASK2 RFC 7011 IPFIX v10 exporter polling ask.ko via YNL"
    )
    parser.add_argument("--collector", required=True, help="IPFIX collector IP")
    parser.add_argument("--port", type=int, default=4739, help="UDP port (default 4739)")
    parser.add_argument("--interval", type=float, default=10.0, help="Poll interval in seconds (default 10)")
    parser.add_argument("--template-refresh", type=float, default=60.0, help="Template refresh seconds (default 60)")
    parser.add_argument("--domain-id", type=int, default=1, help="Observation Domain ID (default 1)")
    parser.add_argument("--count", type=int, default=0, help="Stop after N polls (0 = run forever)")
    parser.add_argument("--spec", default="/usr/share/ynl/specs/ask.yaml", help="Path to ask.yaml")
    parser.add_argument("--pyynl-dir", default="/usr/share/ynl/pyynl", help="Path to vendored pyynl")
    parser.add_argument("--verbose", action="store_true", help="Enable INFO logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="ask-ipfix-exporter: %(levelname)s: %(message)s",
    )
    src = YnlAskSource(spec_path=args.spec, pyynl_dir=args.pyynl_dir)
    return run_exporter(
        collector_host=args.collector,
        collector_port=args.port,
        interval_s=args.interval,
        template_refresh_s=args.template_refresh,
        domain_id=args.domain_id,
        count=args.count,
        fetch_fn=src.fetch,
    )


if __name__ == "__main__":
    raise SystemExit(main())
