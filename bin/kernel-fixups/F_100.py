"""F_100: Instrument dpaa_eth_afxdp.c attach path for ZC debugging.

Temporary fixup for M4 ZC debugging. Adds pr_err() at every error
return in af_xdp_pool_xsk_pool_attach(). Remove after root cause found.
"""

import os

changes = 0
AFXDP = "drivers/net/ethernet/freescale/dpaa/af_xdp_pool/af_xdp_pool_main.c"

if os.path.exists(AFXDP):
    with open(AFXDP) as f:
        s = f.read()

    # Entry
    if 'int af_xdp_pool_xsk_pool_attach(' in s:
        s = s.replace(
            'int af_xdp_pool_xsk_pool_attach(',
            'int af_xdp_pool_xsk_pool_attach(\n\tpr_err("ZCBIND: af_xdp_pool_attach ENTER pool=%px qid=%u\\n", pool, queue_id);\n\t',
            1)
        changes += 1
        print("### F_100: af_xdp_pool_attach ENTER instrumented")

    # xsk_pool_dma_map failure
    if 'xsk_pool_dma_map(pool,' in s:
        s = s.replace(
            'if (ret) {\n\t\tpriv->xsk_dma_map_fail++;',
            'if (ret) {\n\t\tpr_err("ZCBIND: af_xdp_pool_attach FAIL: xsk_pool_dma_map ret=%d\\n", ret);\n\t\tpriv->xsk_dma_map_fail++;',
            1)
        changes += 1
        print("### F_100: xsk_pool_dma_map failure instrumented")

    # bman_new_pool failure
    if 'bpool = bman_new_pool();' in s:
        s = s.replace(
            'if (!bpool) {',
            'if (!bpool) {\n\t\tpr_err("ZCBIND: af_xdp_pool_attach FAIL: bman_new_pool ENOMEM\\n");',
            1)
        changes += 1
        print("### F_100: bman_new_pool failure instrumented")

    # Success
    if 'xsk_pool_attach_ok++;' in s:
        s = s.replace(
            'xsk_pool_attach_ok++;',
            'pr_err("ZCBIND: af_xdp_pool_attach OK pool=%px bpid=%u\\n", pool, priv->xsk_bpid[queue_id]);\n\tpriv->xsk_pool_attach_ok++;',
            1)
        changes += 1
        print("### F_100: af_xdp_pool_attach success instrumented")

    with open(AFXDP, 'w') as f:
        f.write(s)

if changes:
    print("### F_100: %d instrumentation point(s) added" % changes)
else:
    print("### F_100: WARNING no instrumentation applied (file missing or anchors changed)")
