"""F_099: Instrument AF_XDP ZC bind path with pr_err diagnostics.

Temporary fixup for M4 ZC debugging.  Inject pr_err() at every error
return in xp_assign_dev(), xsk_bind(), and dpaa_xdp().  Once the
failing check is identified from dmesg, remove this fixup and fix
the root cause.

Disposition: temporary — remove after M4 ZC root cause found
"""

import os, sys, re

changes = 0


def insert_before(src, anchor, insertion):
    """Insert `insertion` before the first occurrence of `anchor` in `src`."""
    if src.count(anchor) != 1:
        return src, False
    return src.replace(anchor, insertion + anchor, 1), True


# ── File 1: net/xdp/xsk_buff_pool.c — xp_assign_dev() ───────────

XBP = "net/xdp/xsk_buff_pool.c"
if os.path.exists(XBP):
    with open(XBP) as f:
        s = f.read()

    # Entry diagnostic after dev_hold
    s, ok = insert_before(s,
        '\tif (force_copy)',
        '\tpr_err("ZCBIND: xp_assign_dev ENTER force_zc=%d features=%#x mtu=%u\\n",\n'
        '\t       force_zc, netdev->xdp_features, (unsigned int)netdev->mtu);\n\n')
    if ok: print("### F_099: xp_assign_dev entry instrumented"); changes += 1

    # NETDEV_XDP_ACT_ZC check failure
    s, ok = insert_before(s,
        '\t\terr = -EOPNOTSUPP;\n\t\tgoto err_unreg_pool;\n\t}\n\n\tif (mbuf)',
        '\t\tpr_err("ZCBIND: xp_assign_dev FAIL: NETDEV_XDP_ACT_ZC incomplete features=%#x need=%#x\\n",\n'
        '\t\t       netdev->xdp_features, (unsigned int)NETDEV_XDP_ACT_ZC);\n')
    if ok: print("### F_099: ZC features check instrumented"); changes += 1

    # ndo_bpf failure
    if '\t\terr = netdev->netdev_ops->ndo_bpf(netdev, &bpf);' in s:
        old = ('\terr = netdev->netdev_ops->ndo_bpf(netdev, &bpf);\n'
               '\tif (err)\n\t\tgoto err_unreg_pool;')
        new = ('\terr = netdev->netdev_ops->ndo_bpf(netdev, &bpf);\n'
               '\tif (err) {\n'
               '\t\tpr_err("ZCBIND: xp_assign_dev FAIL: ndo_bpf returned %d on %s\\n", err, netdev->name);\n'
               '\t\tgoto err_unreg_pool;\n\t}')
        if old in s:
            s = s.replace(old, new, 1)
            print("### F_099: ndo_bpf failure instrumented"); changes += 1

    # err_unreg_pool label
    if '\terr_unreg_pool:\n' in s:
        s = s.replace('\terr_unreg_pool:\n',
                      '\terr_unreg_pool:\n'
                      '\tpr_err("ZCBIND: xp_assign_dev EXIT err=%d dev=%s\\n", err, netdev->name);\n')
        print("### F_099: err_unreg_pool label instrumented"); changes += 1

    with open(XBP, 'w') as f:
        f.write(s)

# ── File 2: net/xdp/xsk.c — xsk_bind() ──────────────────────────

XSKC = "net/xdp/xsk.c"
if os.path.exists(XSKC):
    with open(XSKC) as f:
        s = f.read()

    # No rx/tx rings
    s, ok = insert_before(s,
        '\t\terr = -EINVAL;\n\t\tgoto out_unlock;\n\t}\n\n\tqid',
        '\t\tpr_err("ZCBIND: xsk_bind FAIL: no rx or tx rings (rx=%px tx=%px)\\n", xs->rx, xs->tx);\n')
    if ok: print("### F_099: xsk_bind no-rings check instrumented"); changes += 1

    # UMEM or validate_queues failure
    if '\t} else if (!xs->umem || !xsk_validate_queues(xs)) {\n\t\terr = -EINVAL;\n\t\tgoto out_unlock;' in s:
        old = ('\t} else if (!xs->umem || !xsk_validate_queues(xs)) {\n'
               '\t\terr = -EINVAL;\n'
               '\t\tgoto out_unlock;')
        new = ('\t} else if (!xs->umem || !xsk_validate_queues(xs)) {\n'
               '\t\tpr_err("ZCBIND: xsk_bind FAIL: umem=%px valid_queues=%d rx=%px tx=%px\\n",\n'
               '\t\t       xs->umem, xsk_validate_queues(xs), xs->rx, xs->tx);\n'
               '\t\terr = -EINVAL;\n'
               '\t\tgoto out_unlock;')
        s = s.replace(old, new, 1)
        print("### F_099: xsk_bind umem/queues check instrumented"); changes += 1

    # xp_create_and_assign_umem failure
    old2 = ('\t\txs->pool = xp_create_and_assign_umem(xs, xs->umem);\n'
            '\t\tif (!xs->pool) {\n'
            '\t\t\terr = -ENOMEM;')
    if old2 in s:
        s = s.replace(old2,
            '\t\txs->pool = xp_create_and_assign_umem(xs, xs->umem);\n'
            '\t\tif (!xs->pool) {\n'
            '\t\t\tpr_err("ZCBIND: xsk_bind FAIL: xp_create_and_assign_umem ENOMEM\\n");\n'
            '\t\t\terr = -ENOMEM;',
            1)
        print("### F_099: xsk_bind pool alloc failure instrumented"); changes += 1

    with open(XSKC, 'w') as f:
        f.write(s)

# ── File 3: dpaa_eth.c — dpaa_xdp() ─────────────────────────────

DPAA_ETH = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
if os.path.exists(DPAA_ETH):
    with open(DPAA_ETH) as f:
        s = f.read()

    if '\tcase XDP_SETUP_XSK_POOL:' in s:
        s, ok = insert_before(s,
            '\tcase XDP_SETUP_XSK_POOL:',
            '\t\tpr_err("ZCBIND: dpaa_xdp XDP_SETUP_XSK_POOL on %s\\n", net_dev->name);\n')
        if ok: print("### F_099: dpaa_xdp XDP_SETUP_XSK_POOL instrumented"); changes += 1

    with open(DPAA_ETH, 'w') as f:
        f.write(s)

# ── File 4: dpaa_eth_afxdp.c — xsk_pool_attach ───────────────────

AFXDP = "drivers/net/ethernet/freescale/dpaa/dpaa_eth_afxdp.c"
if os.path.exists(AFXDP):
    with open(AFXDP) as f:
        s = f.read()

    # Instrument xsk_pool_attach entry + error returns
    if '\treturn 0;\n}\n\nstatic void af_xdp_pool_xsk_pool_detach' in s:
        s = s.replace(
            '\treturn 0;\n}\n\nstatic void af_xdp_pool_xsk_pool_detach',
            '\tpr_info("ZCBIND: af_xdp_pool_xsk_pool_attach OK on %s\\n", netdev->name);\n'
            '\treturn 0;\n}\n\nstatic void af_xdp_pool_xsk_pool_detach',
            1)
        print("### F_099: af_xdp_pool_xsk_pool_attach success instrumented"); changes += 1

    # Find any "return -E" in attach function and instrument
    # Look for the attach function boundaries
    attach_start = s.find('int af_xdp_pool_xsk_pool_attach')
    if attach_start > 0:
        attach_end = s.find('\n}\n', attach_start)
        if attach_end > 0:
            # Add pr_err before any "return -E" in the attach function
            import re
            def repl(m):
                return '\t\tpr_err("ZCBIND: af_xdp_pool_attach FAIL at %s line: ret=%s\\n", netdev->name, (int)(%s));\n%s' % (
                    m.group(0).strip(), m.group(1).strip(), m.group(1).strip(), m.group(0))
            # Only target lines in the attach function
            attach_body = s[attach_start:attach_end]
            modified = re.sub(r'(return\s+-E\w+;)', repl, attach_body)
            if modified != attach_body:
                s = s[:attach_start] + modified + s[attach_end:]
                print("### F_099: af_xdp_pool_attach error returns instrumented"); changes += 1

    with open(AFXDP, 'w') as f:
        f.write(s)

if changes:
    print("### F_099: %d instrumentation point(s) added (rebuild to trace ZC EINVAL)" % changes)
else:
    print("### F_099: WARNING no instrumentation points applied (anchors may have changed)")
