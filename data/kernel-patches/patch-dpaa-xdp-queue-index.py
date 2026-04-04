#!/usr/bin/env python3
"""
Patch drivers/net/ethernet/freescale/dpaa/dpaa_eth.c to fix AF_XDP.

Problem: The DPAA driver passes dpaa_fq->fqid (QMan Frame Queue ID) as the
queue_index parameter to xdp_rxq_info_reg(). FQIDs are dynamically allocated
from QMan pools and are typically 256+ or 32768+, far exceeding the XSKMAP
max_entries (1024). When the XDP program xsk_def_prog calls
bpf_redirect_map(&xsks_map, ctx->rx_queue_index, XDP_PASS), the
XSKMAP lookup for key=FQID always fails because key >= max_entries.
Result: AF_XDP RX is completely non-functional on DPAA interfaces.

Fix: Replace dpaa_fq->fqid with 0 as the queue_index. This maps all RX
frame queues to queue 0, matching the XSK socket that VPP creates at
queue_id=0. All packets on all CPUs are redirected to the single AF_XDP
socket, which is correct for the DPAA driver that reports 1 combined channel.

Confirmed on NXP LS1046A Mono Gateway with kernel 6.6.130-vyos.
"""

import sys
import re


def patch_dpaa_eth(content):
    """Fix xdp_rxq_info_reg queue_index in dpaa_eth.c."""
    # Pattern: xdp_rxq_info_reg(&dpaa_fq->xdp_rxq, dpaa_fq->net_dev,
    #                            dpaa_fq->fqid, 0);
    # Replace dpaa_fq->fqid with 0

    old_pattern = r'(xdp_rxq_info_reg\s*\(\s*&dpaa_fq->xdp_rxq\s*,\s*dpaa_fq->net_dev\s*,\s*)dpaa_fq->fqid(\s*,\s*0\s*\))'
    new_text = r'\g<1>0 /* was dpaa_fq->fqid; fixed for AF_XDP: FQID exceeds XSKMAP max_entries */\2'

    result, count = re.subn(old_pattern, new_text, content)
    if count == 0:
        # Try a more relaxed pattern
        old_pattern2 = r'(xdp_rxq_info_reg\(&dpaa_fq->xdp_rxq,\s*dpaa_fq->net_dev,\s*)\n\s*(dpaa_fq->fqid,\s*0\))'
        new_text2 = r'\g<1>\n\t\t\t\t       0 /* was dpaa_fq->fqid; fixed for AF_XDP */,'
        # Actually this multi-line pattern is tricky. Let's try line-by-line.
        lines = content.split('\n')
        patched = False
        for i, line in enumerate(lines):
            if 'dpaa_fq->fqid' in line and 'xdp_rxq_info_reg' in lines[max(0, i-1):i+1]:
                # This line or the previous has xdp_rxq_info_reg
                lines[i] = line.replace('dpaa_fq->fqid',
                    '0 /* was dpaa_fq->fqid; fixed for AF_XDP */')
                patched = True
                break
            # Check if xdp_rxq_info_reg call spans lines
            if 'dpaa_fq->fqid' in line and i > 0:
                # Look back up to 3 lines for xdp_rxq_info_reg
                for j in range(max(0, i-3), i):
                    if 'xdp_rxq_info_reg' in lines[j]:
                        lines[i] = line.replace('dpaa_fq->fqid',
                            '0 /* was dpaa_fq->fqid; fixed for AF_XDP */')
                        patched = True
                        break
                if patched:
                    break

        if patched:
            result = '\n'.join(lines)
            count = 1
        else:
            print("WARNING: Could not find dpaa_fq->fqid in xdp_rxq_info_reg call")
            return content

    print(f"  Patched {count} occurrence(s) of xdp_rxq_info_reg queue_index")
    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: patch-dpaa-xdp-queue-index.py <path/to/dpaa_eth.c>")
        sys.exit(1)

    path = sys.argv[1]
    try:
        with open(path, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"ERROR: {path} not found")
        sys.exit(1)

    if '0 /* was dpaa_fq->fqid; fixed for AF_XDP */' in content:
        print("  Already patched, skipping")
        return

    result = patch_dpaa_eth(content)
    if result != content:
        with open(path, 'w') as f:
            f.write(result)
        print(f"OK: Patched {path}")
    else:
        print(f"WARNING: No changes made to {path}")


if __name__ == '__main__':
    main()