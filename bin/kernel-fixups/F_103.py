"""F_103: Skip FMan RX port BPID reprogram during ZC attach.

The reprogram-WRITE in af_xdp_pool_xsk_pool_attach() changes the FMan
RX port's primary BMan pool from the kernel page-pool to the XSK pool.
After this, all ingress frames are DMA'd into XSK chunks and enqueued
to the default RX FQ with invalid context_b, causing a NULL deref in
__poll_portal_fast (qman_p_poll_dqrr).

The comment at lines 730-738 says this write "stays out of this patch"
but the code at lines 810-839 executes it anyway. Skip it until the
ZC RX datapath is fully ready to handle XSK-pool frames.

Temporary — re-enable once the rx_hook properly handles all frames
from the XSK pool without crashing the default RX path.
"""

import os

changes = 0
AFXDP = "drivers/net/ethernet/freescale/dpaa/af_xdp_pool/af_xdp_pool_main.c"

if os.path.exists(AFXDP):
    with open(AFXDP) as f:
        s = f.read()

    # Wrap the reprogram-WRITE block in if (0) to disable it
    old = ('\tif (rxp) {\n'
           '\t\tint wret;\n'
           '\t\tstruct fman *_fm2 = fman_bind(priv->mac_dev->fman_dev);')
    new = ('\t/* F_103: reprogram-WRITE disabled — causes QMan context_b corruption */\n'
           '\tif (0 && rxp) {\n'
           '\t\tint wret;\n'
           '\t\tstruct fman *_fm2 = fman_bind(priv->mac_dev->fman_dev);')

    if old in s:
        s = s.replace(old, new, 1)
        changes += 1
        print("### F_103: FMan RX port BPID reprogram disabled")
    else:
        print("### F_103: WARNING anchor not found in af_xdp_pool_main.c")

    with open(AFXDP, 'w') as f:
        f.write(s)

if changes:
    print("### F_103: %d change(s) applied" % changes)
else:
    print("### F_103: WARNING no changes applied")
