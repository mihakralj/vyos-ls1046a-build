#!/usr/bin/env python3
#
# Copyright (C) VyOS Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# op-mode: show offload config
#          show offload flow
#
# LS1046A / ASK2 (Accelerated Switching Kernel) hardware routing offload.
#
# `show offload config` lists every DPAA ethernet port and which address
# families are hardware-offloaded on it (IPv4 / IPv6), from the running
# configuration, cross-referenced with the live FMan FE-VM armed state in
# debugfs so the operator can see both intent and what silicon is actually
# running.
#
# `show offload flow` renders the flows the FMan FE-VM has learned, wrapping
# the standard YNL client talking straight to ask.ko's generic-netlink
# family (there is no askd daemon):
#
#     ynl --family ask --dump dump-flows --output-json

import argparse
import json
import socket
import time
from ipaddress import ip_address

from tabulate import tabulate

from vyos.configquery import ConfigTreeQuery
from vyos.utils.process import rc_cmd

YNL = '/usr/local/bin/ynl'
SPEC_FAMILY = 'ask'
FE_PORT = '/sys/kernel/debug/fman_pcd/0/fe_port'

# Board-fixed physical map for the LS1046A Mono Gateway DK (AGENTS.md port
# order rule): interface name -> FMan hardware port id. This is the same
# static mapping ask.ko engages against; there is no per-port family read-back
# op in the genl family, so configured families come from the running config
# and the live/armed indicator comes from the fe_port debugfs node.
HW_PORT = {
    'eth0': 0x0c,   # left RJ45  (MAC5, mgmt)
    'eth1': 0x0d,   # center RJ45 (MAC6)
    'eth2': 0x09,   # right RJ45 (MAC2)
    'eth3': 0x10,   # left SFP+  (MAC9)
    'eth4': 0x11,   # right SFP+ (MAC10)
}
# Reverse map (hw port id -> interface name) for ordered per-port listing.
_HW_PORT_NAME = {v: k for k, v in HW_PORT.items()}

# IANA L4 protocol number -> display name.
_L4 = {socket.IPPROTO_TCP: 'tcp', socket.IPPROTO_UDP: 'udp',
       socket.IPPROTO_ICMP: 'icmp', socket.IPPROTO_ESP: 'esp',
       socket.IPPROTO_SCTP: 'sctp'}


def _get(flow: dict, *names, default=None):
    """Fetch an attr tolerating dash/underscore spelling drift in ynl output."""
    for n in names:
        for key in (n, n.replace('-', '_'), n.replace('_', '-')):
            if key in flow:
                return flow[key]
    return default


def _fmt_ip(val):
    """ynl emits `binary` attrs as a hex string; render as an IP address."""
    if val is None:
        return '-'
    if isinstance(val, str):
        try:
            raw = bytes.fromhex(val)
        except ValueError:
            return val
    elif isinstance(val, (bytes, bytearray)):
        raw = bytes(val)
    else:
        return str(val)
    if len(raw) == 16:
        return str(ip_address(raw))
    if len(raw) == 4:
        return str(ip_address(raw))
    return raw.hex()


# NAT/PAT rewrite bitmask matching include/uapi/linux/ask/ask.h.
_ASK_NATF_SNAT = 1 << 0
_ASK_NATF_DNAT = 1 << 1
_ASK_NATF_SPAT = 1 << 2
_ASK_NATF_DPAT = 1 << 3

_FALLBACK_LABELS = (
    ('admission-gate', 'admission-gate'),
    ('policy-fenced', 'policy-fenced'),
    ('unsupported-proto', 'unsupported-proto'),
    ('unsupported-action', 'unsupported-action'),
    ('neigh-unresolved', 'neigh-unresolved'),
    ('port-unengaged', 'port-unengaged'),
    ('max-flows', 'max-flows'),
    ('hw-error', 'hw-error'),
)


def _fmt_endpoint(val, port):
    """Format an ip:port endpoint. IPv6 is bracketed ([addr]:port) so the
    address/port boundary is unambiguous; IPv4 uses plain addr:port."""
    ip = _fmt_ip(val)
    if port is None:
        port = '-'
    return f'[{ip}]:{port}' if ':' in ip else f'{ip}:{port}'


def _fmt_nat(flow: dict) -> str:
    """Render translated NAT/PAT endpoints from ASK_FLOW_ATTR_NAT_*."""
    flags = _get(flow, 'nat-flags', default=0) or 0
    if not flags:
        return '-'
    parts = []
    if flags & (_ASK_NATF_SNAT | _ASK_NATF_SPAT):
        nsip = _get(flow, 'nat-src-ip') if (flags & _ASK_NATF_SNAT) else None
        nspt = _get(flow, 'nat-sport') if (flags & _ASK_NATF_SPAT) else None
        if nsip is not None and nspt is not None:
            parts.append(f'snat {_fmt_endpoint(nsip, nspt)}')
        elif nsip is not None:
            parts.append(f'snat {_fmt_ip(nsip)}')
        elif nspt is not None:
            parts.append(f'spat :{nspt}')
    if flags & (_ASK_NATF_DNAT | _ASK_NATF_DPAT):
        ndip = _get(flow, 'nat-dst-ip') if (flags & _ASK_NATF_DNAT) else None
        ndpt = _get(flow, 'nat-dport') if (flags & _ASK_NATF_DPAT) else None
        if ndip is not None and ndpt is not None:
            parts.append(f'dnat {_fmt_endpoint(ndip, ndpt)}')
        elif ndip is not None:
            parts.append(f'dnat {_fmt_ip(ndip)}')
        elif ndpt is not None:
            parts.append(f'dpat :{ndpt}')
    return ', '.join(parts) if parts else f'0x{int(flags):x}'


def _fmt_age(first_seen_ns, now_ns: int) -> str:
    """Format host-observed flow age from CLOCK_MONOTONIC nanoseconds."""
    if not first_seen_ns:
        return '-'
    sec = max(0, (now_ns - int(first_seen_ns)) // 1_000_000_000)
    if sec < 60:
        return f'{sec}s'
    if sec < 3600:
        return f'{sec // 60}m{sec % 60:02d}s'
    return f'{sec // 3600}h{(sec % 3600) // 60:02d}m'


def _fetch_info() -> dict:
    """Run ynl get-info and return the unwrapped info dict (or {} if absent)."""
    rc, out = rc_cmd(f'{YNL} --family {SPEC_FAMILY} --do get-info --output-json')
    if rc != 0 or not out.strip():
        return {}
    try:
        data = json.loads(out)
    except ValueError:
        return {}
    if isinstance(data, dict) and isinstance(data.get('info'), dict):
        return data['info']
    return data if isinstance(data, dict) else {}


def _fmt_fallbacks(info: dict):
    """Return formatted non-zero software fallback breakdown, or None."""
    fb = _get(info, 'fallback')
    if not isinstance(fb, dict):
        return None
    parts = []
    for key, label in _FALLBACK_LABELS:
        val = _get(fb, key, default=0) or 0
        if val:
            parts.append(f'{label}={val}')
    return ', '.join(parts) if parts else 'none'


def _armed_hw_ports():
    """Return the set of FMan hardware port ids currently FE-armed.

    The fe_port debugfs node lists one line per armed RX port, e.g.
        port 0x10 pool 0x57000/8448 B mgmt 0x59100/21 B v4_table ffff...
    Absent (module not loaded / nothing engaged) -> empty set.
    """
    armed = set()
    try:
        with open(FE_PORT) as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == 'port':
                    try:
                        armed.add(int(parts[1], 16))
                    except ValueError:
                        pass
    except OSError:
        pass
    return armed


def _fetch_flows():
    """Run the YNL dump and return a flat list of per-flow dicts."""
    rc, out = rc_cmd(f'{YNL} --family {SPEC_FAMILY} --dump dump-flows --output-json')
    if rc != 0:
        raise RuntimeError(out.strip() or 'ynl dump-flows failed '
                           '(is ask.ko loaded and offload engaged?)')
    if not out.strip():
        return []
    data = json.loads(out)
    if isinstance(data, dict):
        data = [data]
    flows = []
    for item in data:
        if isinstance(item, dict) and 'flow' in item and isinstance(item['flow'], dict):
            flows.append(item['flow'])
        elif isinstance(item, dict):
            flows.append(item)
    return flows


def _nat66_offload_enabled():
    """Return the live ask.ko IPv6 NAT66 hardware-offload gate."""
    try:
        with open('/sys/module/ask/parameters/nat66_offload') as f:
            return f.read().strip().lower() in ('1', 'y', 'yes', 'true')
    except OSError:
        return False


def _nat44_offload_enabled():
    """Return the live ask.ko NAT44/PAT hardware-offload gate."""
    try:
        with open('/sys/module/ask/parameters/nat44_offload') as f:
            return f.read().strip().lower() in ('1', 'y', 'yes', 'true')
    except OSError:
        return False


def show_config():
    """Per-port hardware offload configuration and live armed state."""
    conf = ConfigTreeQuery()
    armed = _armed_hw_ports()

    rows = []
    for hw_port in sorted(HW_PORT.values()):
        ifname = _HW_PORT_NAME[hw_port]
        base = ['interfaces', 'ethernet', ifname]

        # Build the list of active offload types for this port. A port is
        # either handed to the VPP (AF_XDP) dataplane, or offloaded per family
        # by the FMan FE-VM; the two are mutually exclusive (per-interface
        # ASK<->VPP mutex). Extensible: add future categories (nat, ipsec,
        # vlan, ...) as they become hardware-offloaded.
        offloads = []
        if conf.exists(['vpp', 'settings', 'interface', ifname]):
            offloads.append('vpp')
        else:
            v4 = conf.exists(base + ['offload', 'ipv4'])
            v6 = conf.exists(base + ['offload', 'ipv6'])
            if v4:
                offloads.append('ipv4')
            if v6:
                offloads.append('ipv6')
            # NAT is a hardware capability layered on each family's routed
            # path: nat44 when IPv4 offload + the nat44 gate are on, nat66 when
            # IPv6 offload + the nat66 gate are on (both default on). NAT46/
            # NAT64 are not hardware-offloadable.
            if v4 and _nat44_offload_enabled():
                offloads.append('nat44')
            if v6 and _nat66_offload_enabled():
                offloads.append('nat66')
            if conf.exists(base + ['ingress-policer']):
                offloads.append('policer')
            # Distinguish configured-but-not-armed from live in silicon:
            # append a marker rather than hiding the inconsistency.
            if offloads and hw_port not in armed:
                offloads = [f'{o}*' for o in offloads]

        rows.append([
            f'0x{hw_port:02x}',
            ifname,
            ', '.join(offloads) if offloads else 'none',
        ])

    headers = ['HW-Port', 'Interface', 'Offloads']
    print('ASK2 hardware routing offload (NXP LS1046A FMan FE-VM)\n')
    print(tabulate(rows, headers=headers, tablefmt='simple'))
    if any('*' in r[2] for r in rows):
        print('\n* configured but not currently engaged in silicon '
              '(e.g. link down or interface assigned to VPP).')
    info = _fetch_info()
    if info:
        n_flows = _get(info, 'num-flows')
        m_flows = _get(info, 'max-flows')
        if n_flows is not None and m_flows is not None:
            limit = 'unlimited' if m_flows == 0 else str(m_flows)
            print(f'\nActive offloaded flow directions: {n_flows} / {limit}')
        fb_str = _fmt_fallbacks(info)
        if fb_str is not None:
            print(f'Software fallbacks (since load): {fb_str}')
    return 0


_ETH_P_IP = 2048       # 0x0800
_ETH_P_IPV6 = 34525    # 0x86DD


def show_flow():
    """All flows the FMan FE-VM has learned, across every port.

    Flows are grouped by address family (IPv4 first, then IPv6) and sorted
    within each family so the table is stable and readable rather than
    following the kernel's hash-random rhashtable iteration order.
    """
    flows = _fetch_flows()
    now_ns = time.monotonic_ns()
    rows = []
    port_agg = {}
    for f in flows:
        l3 = _get(f, 'l3-proto')
        family = 'ipv6' if l3 == _ETH_P_IPV6 else 'ipv4'
        l4 = _get(f, 'l4-proto')
        proto = _L4.get(l4, str(l4) if l4 is not None else '-')
        # Sort key: IPv4 (0) before IPv6 (1), then proto, source, dest.
        sort_key = (0 if family == 'ipv4' else 1,
                    proto,
                    _fmt_ip(_get(f, 'src-ip')),
                    _get(f, 'sport') or 0,
                    _fmt_ip(_get(f, 'dst-ip')),
                    _get(f, 'dport') or 0)
        source = _fmt_endpoint(_get(f, 'src-ip'), _get(f, 'sport'))
        destination = _fmt_endpoint(_get(f, 'dst-ip'), _get(f, 'dport'))
        rows.append((sort_key, [
            f'{family}/{proto}',
            f'{source} → {destination}',
            _fmt_nat(f),
            _fmt_age(_get(f, 'first-seen-ns'), now_ns),
            _get(f, 'packets', default=0),
            _get(f, 'bytes', default=0),
        ]))
        # VLAN CC-path flows carry a per-port HW-forwarded aggregate
        # (no per-key counters exist on silicon); collect once per port.
        pid = _get(f, 'port-id')
        apkts = _get(f, 'agg-packets')
        abytes = _get(f, 'agg-bytes')
        if pid is not None and apkts is not None and pid not in port_agg:
            port_agg[pid] = (apkts, abytes)

    if not rows:
        print('No flows tracked by ASK offload')
        fb_str = _fmt_fallbacks(_fetch_info())
        if fb_str and fb_str != 'none':
            print(f'Software fallbacks (since load): {fb_str}')
        return 0

    rows.sort(key=lambda r: r[0])
    data = [r[1] for r in rows]
    headers = ['Protocol', 'Flow', 'NAT', 'Age', 'Packets', 'Bytes']
    print(f'ASK offload flows ({len(data)} total):\n')
    print(tabulate(data, headers=headers, tablefmt='simple'))
    for pid in sorted(port_agg):
        apkts, abytes = port_agg[pid]
        print(f'\nVLAN port {pid:#04x} hardware aggregate '
              f'(since first flow on port): {apkts} packets / {abytes} bytes')
    fb_str = _fmt_fallbacks(_fetch_info())
    if fb_str and fb_str != 'none':
        print(f'\nSoftware fallbacks (since load): {fb_str}')
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--action', required=True, choices=['config', 'flow'],
                        help='config: per-port offload state; flow: offloaded flows')
    args = parser.parse_args()
    try:
        if args.action == 'config':
            raise SystemExit(show_config())
        raise SystemExit(show_flow())
    except RuntimeError as e:
        print(e)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
