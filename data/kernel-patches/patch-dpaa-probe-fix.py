#!/usr/bin/env python3
"""
Patch SDK DPAA drivers to fix soft lockup during probe and add diagnostics.

Modifies three files:
  1. sdk_dpaa/dpaa_eth.h — reduce DPAA_ETH_RX_QUEUES from 128 to 16
  2. sdk_dpaa/dpaa_eth_common.c — cond_resched, debug printks, while-loop safety
  3. sdk_dpaa/dpaa_eth.c — probe progress tracing

Usage: patch-dpaa-probe-fix.py <kernel-source-dir>
  e.g.: patch-dpaa-probe-fix.py .
"""

import sys
import os
import re


def patch_dpaa_eth_h(path):
    """Reduce DPAA_ETH_RX_QUEUES from 128 to 16."""
    with open(path, 'r') as f:
        content = f.read()

    if 'DPAA_ETH_RX_QUEUES\t16' in content or 'DPAA_ETH_RX_QUEUES 16' in content:
        print(f"  {path}: RX_QUEUES already patched, skipping")
        return

    new = content.replace(
        '#define DPAA_ETH_RX_QUEUES\t128',
        '#define DPAA_ETH_RX_QUEUES\t16'
    )
    if new == content:
        # Try with spaces
        new = re.sub(
            r'#define\s+DPAA_ETH_RX_QUEUES\s+128',
            '#define DPAA_ETH_RX_QUEUES\t16',
            content
        )
    if new == content:
        print(f"  WARNING: Could not find DPAA_ETH_RX_QUEUES 128 in {path}")
        return

    with open(path, 'w') as f:
        f.write(new)
    print(f"  {path}: DPAA_ETH_RX_QUEUES reduced to 16")


def patch_dpaa_eth_common(path):
    """Add cond_resched, debug printks, and while-loop safety break."""
    with open(path, 'r') as f:
        lines = f.readlines()

    if any('DPAA_FQ_SETUP: enter' in l for l in lines):
        print(f"  {path}: already patched, skipping")
        return

    out = []
    in_fq_setup = False
    in_fqs_init = False
    fq_setup_main_loop_seen = False
    while_loop_started = False
    while_loop_inner_foreach = False
    added_fq_count_var = False
    fqs_init_foreach_seen = False

    i = 0
    while i < len(lines):
        line = lines[i]

        # Track function boundaries
        if re.match(r'^void dpa_fq_setup\(', line):
            in_fq_setup = True
            fq_setup_main_loop_seen = False
            while_loop_started = False

        if re.match(r'^EXPORT_SYMBOL\(dpa_fq_setup\)', line):
            in_fq_setup = False

        if re.match(r'^int dpa_fqs_init\(', line):
            in_fqs_init = True
            added_fq_count_var = False
            fqs_init_foreach_seen = False

        if in_fqs_init and re.match(r'^EXPORT_SYMBOL\(dpa_fqs_init\)', line):
            in_fqs_init = False

        # === dpa_fq_setup modifications ===
        if in_fq_setup:
            # Add debug counter and entry printk after variable declarations
            if 'int egress_cnt = 0' in line and 'conf_cnt' in line:
                out.append(line)
                out.append('\tint __fq_dbg_cnt = 0;\n')
                out.append('\tprintk("DPAA_FQ_SETUP: enter\\n");\n')
                i += 1
                continue

            # Add num_portals printk after the for_each_cpu loop
            if 'portals[num_portals++]' in line:
                out.append(line)
                # Find the next line and add printk after it
                i += 1
                if i < len(lines):
                    out.append(lines[i])  # This should be a blank or if line
                    # Insert printk
                    out.append('\tprintk("DPAA_FQ_SETUP: num_portals=%d\\n", num_portals);\n')
                    i += 1
                continue

            # Main loop: add cond_resched + debug printk
            if not fq_setup_main_loop_seen and 'list_for_each_entry(fq, &priv->dpa_fq_list, list)' in line:
                fq_setup_main_loop_seen = True
                out.append(line)  # the list_for_each_entry line
                i += 1
                # Add instrumentation at start of loop body
                out.append('\t\tcond_resched();\n')
                out.append('\t\t__fq_dbg_cnt++;\n')
                out.append('\t\tprintk("DPAA_FQ_SETUP: fq #%d type=%d\\n", __fq_dbg_cnt, fq->fq_type);\n')
                continue

            # After main loop closing: add summary printk
            # Detect the comment that precedes the while-loop
            if fq_setup_main_loop_seen and not while_loop_started and \
               'The number of Tx queues may be smaller' in line:
                out.append('\tprintk("DPAA_FQ_SETUP: main loop done, %d FQs, egress_cnt=%d/%d\\n",\n')
                out.append('\t       __fq_dbg_cnt, egress_cnt, DPAA_ETH_TX_QUEUES);\n')
                out.append('\n')
                out.append(line)
                i += 1
                continue

            # While-loop: add safety break + cond_resched
            if 'while (egress_cnt < DPAA_ETH_TX_QUEUES)' in line:
                while_loop_started = True
                out.append(line)  # while (...) {
                out.append('\t\tint __prev_egress = egress_cnt;\n')
                i += 1
                continue

            # Inner list_for_each_entry inside while-loop: add cond_resched
            if while_loop_started and 'list_for_each_entry(fq, &priv->dpa_fq_list, list)' in line:
                while_loop_inner_foreach = True
                out.append(line)
                i += 1
                # Add cond_resched at start of inner loop body
                out.append('\t\t\tcond_resched();\n')
                continue

            # After inner foreach closing brace, add safety break check
            # The inner foreach is closed by a line with just "}" at 2-tab indent
            # followed by the while-loop's closing "}" at 1-tab indent
            if while_loop_started and while_loop_inner_foreach:
                stripped = line.rstrip('\n')
                # Check for the closing brace of the inner list_for_each_entry
                # It's the "}" that's at the same level as the while body
                if stripped.strip() == '}' and not stripped.startswith('\t\t\t'):
                    # Count tabs to determine which brace this is
                    tabs = len(stripped) - len(stripped.lstrip('\t'))
                    if tabs == 2:
                        # This closes the inner list_for_each_entry
                        out.append(line)
                        # Add safety break after the inner foreach
                        out.append('\t\tif (egress_cnt == __prev_egress) {\n')
                        out.append('\t\t\tprintk("DPAA_FQ_SETUP: WARN no TX FQs, breaking egress fill (egress_cnt=%d/%d)\\n",\n')
                        out.append('\t\t\t       egress_cnt, DPAA_ETH_TX_QUEUES);\n')
                        out.append('\t\t\tbreak;\n')
                        out.append('\t\t}\n')
                        while_loop_inner_foreach = False
                        i += 1
                        continue
                    elif tabs == 1:
                        # This closes the while-loop
                        out.append(line)
                        out.append('\n')
                        out.append('\tprintk("DPAA_FQ_SETUP: done\\n");\n')
                        while_loop_started = False
                        i += 1
                        continue

        # === dpa_fqs_init modifications ===
        if in_fqs_init:
            # Add fq_count variable after the dpa_fq declaration
            if not added_fq_count_var and 'struct dpa_fq *dpa_fq;' in line:
                out.append(line)
                out.append('\tint fq_count = 0;\n')
                added_fq_count_var = True
                i += 1
                continue

            # Add cond_resched in the main loop
            if not fqs_init_foreach_seen and 'list_for_each_entry(dpa_fq, list, list)' in line:
                fqs_init_foreach_seen = True
                out.append(line)
                i += 1
                # Add cond_resched every 32 FQs
                out.append('\t\tif (++fq_count % 32 == 0)\n')
                out.append('\t\t\tcond_resched();\n')
                out.append('\n')
                continue

        out.append(line)
        i += 1

    with open(path, 'w') as f:
        f.writelines(out)
    print(f"  {path}: cond_resched + debug + safety break added")


def patch_dpaa_eth_c(path):
    """Add probe progress tracing to dpaa_eth_priv_probe."""
    with open(path, 'r') as f:
        content = f.read()

    if 'DPAA_PROBE: bp_create done' in content:
        print(f"  {path}: already patched, skipping")
        return

    # Add printk after dpa_priv_bp_create success
    content = content.replace(
        'err = dpa_priv_bp_create(net_dev, dpa_bp, count);\n'
        '\n'
        '\tif (err < 0)\n'
        '\t\tgoto bp_create_failed;\n',
        'err = dpa_priv_bp_create(net_dev, dpa_bp, count);\n'
        '\n'
        '\tif (err < 0)\n'
        '\t\tgoto bp_create_failed;\n'
        '\tprintk("DPAA_PROBE: bp_create done\\n");\n'
    )

    # Add printk after dpa_get_channel
    content = content.replace(
        'channel = dpa_get_channel();\n'
        '\n'
        '\tif (channel < 0) {\n'
        '\t\terr = channel;\n'
        '\t\tgoto get_channel_failed;\n'
        '\t}\n',
        'channel = dpa_get_channel();\n'
        '\n'
        '\tif (channel < 0) {\n'
        '\t\terr = channel;\n'
        '\t\tgoto get_channel_failed;\n'
        '\t}\n'
        '\tprintk("DPAA_PROBE: get_channel=%d\\n", channel);\n'
    )

    # Add printk after dpaa_eth_add_channel
    content = content.replace(
        'dpaa_eth_add_channel(priv->channel);\n'
        '\n'
        '\tdpa_fq_setup(priv',
        'dpaa_eth_add_channel(priv->channel);\n'
        '\tprintk("DPAA_PROBE: add_channel done\\n");\n'
        '\n'
        '\tdpa_fq_setup(priv'
    )

    # Add printk after dpa_fq_setup
    content = content.replace(
        'dpa_fq_setup(priv, &private_fq_cbs, priv->mac_dev->port_dev[TX]);\n'
        '\n',
        'dpa_fq_setup(priv, &private_fq_cbs, priv->mac_dev->port_dev[TX]);\n'
        '\tprintk("DPAA_PROBE: fq_setup done\\n");\n'
        '\n'
    )

    # Add printk after dpaa_eth_cgr_init
    content = content.replace(
        'err = dpaa_eth_cgr_init(priv);\n'
        '\tif (err < 0) {\n'
        '\t\tdev_err(dev, "Error initializing CGR\\n");\n'
        '\t\tgoto tx_cgr_init_failed;\n'
        '\t}\n',
        'err = dpaa_eth_cgr_init(priv);\n'
        '\tif (err < 0) {\n'
        '\t\tdev_err(dev, "Error initializing CGR\\n");\n'
        '\t\tgoto tx_cgr_init_failed;\n'
        '\t}\n'
        '\tprintk("DPAA_PROBE: cgr_init done\\n");\n'
    )

    # Add printk after dpa_fqs_init
    content = content.replace(
        'err = dpa_fqs_init(dev,  &priv->dpa_fq_list, false);\n'
        '\tif (err < 0)\n'
        '\t\tgoto fq_alloc_failed;\n',
        'err = dpa_fqs_init(dev,  &priv->dpa_fq_list, false);\n'
        '\tif (err < 0)\n'
        '\t\tgoto fq_alloc_failed;\n'
        '\tprintk("DPAA_PROBE: fqs_init done\\n");\n'
    )

    with open(path, 'w') as f:
        f.write(content)
    print(f"  {path}: probe progress tracing added")


def main():
    if len(sys.argv) < 2:
        print("Usage: patch-dpaa-probe-fix.py <kernel-source-dir>")
        sys.exit(1)

    kdir = sys.argv[1]
    sdk_dir = os.path.join(kdir, 'drivers/net/ethernet/freescale/sdk_dpaa')

    eth_h = os.path.join(sdk_dir, 'dpaa_eth.h')
    eth_common = os.path.join(sdk_dir, 'dpaa_eth_common.c')
    eth_c = os.path.join(sdk_dir, 'dpaa_eth.c')

    for f in [eth_h, eth_common, eth_c]:
        if not os.path.exists(f):
            print(f"ERROR: {f} not found")
            sys.exit(1)

    print("I: Patching SDK DPAA drivers for probe fix + diagnostics...")
    patch_dpaa_eth_h(eth_h)
    patch_dpaa_eth_common(eth_common)
    patch_dpaa_eth_c(eth_c)
    print("I: SDK DPAA probe fix complete")


if __name__ == '__main__':
    main()