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
# op-mode: show interfaces ethernet eth<n> offload ask flows
#
# Renders the flows the NXP LS1046A FMan FE-VM has offloaded to silicon on
# a given interface. There is no askd daemon: this wraps the standard YNL
# client talking straight to ask.ko's generic-netlink family, per
# kernel/ask/uapi/ask.yaml (§3.5 "Operator UX"):
#
#     ynl --family ask --dump dump-flows --output-json
#
# ask_genl_fill_one_flow() (T-M7-2) supplies the 5-tuple + iif/oif + stats;
# we filter to the flows whose ingress or egress ifindex is this interface.

import argparse
import json
import socket
import time
from ipaddress import ip_address

from tabulate import tabulate

from vyos.utils.process import rc_cmd

YNL = '/usr/local/bin/ynl'
SPEC_FAMILY = 'ask'

# IANA L4 protocol number -> display name.
_L4 = {socket.IPPROTO_TCP: 'tcp', socket.IPPROTO_UDP: 'udp',
       socket.IPPROTO_ICMP: 'icmp', socket.IPPROTO_ESP: 'esp',
       socket.IPPROTO_SCTP: 'sctp'}


def _ifindex(intf: str):
    """Resolve an interface name to its kernel ifindex, or None."""
    try:
        with open(f'/sys/class/net/{intf}/ifindex') as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


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


def _fmt_endpoint(val, port):
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


def _fetch_flows():
    """Run the YNL dump and return a flat list of per-flow dicts.

    ask_genl wraps each flow in an ASK_ATTR_FLOW nest, so a message may
    arrive either flat ({src-ip: ...}) or wrapped ({flow: {src-ip: ...}});
    accept both so the display survives either decode shape.
    """
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


def show_flows(intf: str):
    idx = _ifindex(intf)
    if idx is None:
        print(f'Interface {intf} does not exist')
        return 1

    flows = _fetch_flows()
    now_ns = time.monotonic_ns()
    rows = []
    for f in flows:
        iif = _get(f, 'iif')
        oif = _get(f, 'oif')
        if idx not in (iif, oif):
            continue
        l4 = _get(f, 'l4-proto')
        proto = _L4.get(l4, str(l4) if l4 is not None else '-')
        sport = _get(f, 'sport', default='-')
        dport = _get(f, 'dport', default='-')
        # `offloaded` (u8) distinguishes hardware-backed from software-fallback
        # flows; absent on older images.
        off = _get(f, 'offloaded')
        if off is None:
            offload = '?'
        else:
            offload = 'hw' if int(off) else 'sw'
        rows.append([
            proto,
            _fmt_endpoint(_get(f, 'src-ip'), sport),
            _fmt_endpoint(_get(f, 'dst-ip'), dport),
            _fmt_nat(f),
            _get(f, 'iif', default='-'),
            _get(f, 'oif', default='-'),
            offload,
            _fmt_age(_get(f, 'first-seen-ns'), now_ns),
            _get(f, 'packets', default=0),
            _get(f, 'bytes', default=0),
        ])

    if not rows:
        print(f'No flows tracked by ASK on {intf}')
        return 0

    n_hw = sum(1 for r in rows if r[6] == 'hw')
    headers = ['Proto', 'Source', 'Destination', 'NAT', 'IIF', 'OIF',
               'Offload', 'Age', 'Packets', 'Bytes']
    print(f'ASK flows on {intf} ({len(rows)} tracked, {n_hw} in hardware):\n')
    print(tabulate(rows, headers=headers, tablefmt='simple'))
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--intf-name', required=True,
                        help='Ethernet interface to show ASK-offloaded flows for')
    args = parser.parse_args()
    try:
        raise SystemExit(show_flows(args.intf_name))
    except RuntimeError as e:
        print(e)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
