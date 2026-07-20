"""F_101: Lower DPAA1_MIN_UMEM_CHUNK 3840→2048 for M4 ZC testing.

Temporary workaround: VPP's af_xdp plugin creates UMEM with 2048-byte
chunks (frame_size=1792), but the DPAA1 driver requires >=3840.
Lower to 2048 so ZC mode can be tested. Remove once VPP is fixed to
use 4096-byte chunks (PAGE_SIZE).
"""

import os

changes = 0
HEADER = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.h"

if os.path.exists(HEADER):
    with open(HEADER) as f:
        s = f.read()

    old = '#define DPAA1_MIN_UMEM_CHUNK        3840'
    new = '#define DPAA1_MIN_UMEM_CHUNK        2048'
    if old in s:
        s = s.replace(old, new, 1)
        changes += 1
        print("### F_101: DPAA1_MIN_UMEM_CHUNK 3840→2048")

    with open(HEADER, 'w') as f:
        f.write(s)

if changes:
    print("### F_101: %d change(s) applied" % changes)
else:
    print("### F_101: WARNING anchor not found")
