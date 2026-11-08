"""F-276: retain the DPAA XSK pool owner across BMan drain retries."""

import sys

HEADER = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.h"
MARKER = "F-276: retain the XSK pool owner across BMan drain retries"


def fail(message):
    print(f"### F-276: FATAL: {message}")
    sys.exit(1)


try:
    with open(HEADER) as stream:
        header = stream.read()
except FileNotFoundError:
    fail(f"{HEADER} not found")

field = "xsk_pool_detach_pending[DPAA1_XSK_MAX_QBANDS]"
if MARKER in header:
    if field not in header:
        fail("marker present but pending-pool field is missing")
    print("### F-276: already applied")
    sys.exit(0)
if field in header:
    fail("pending-pool field exists without the F-276 marker")

anchor = "\tstruct bman_pool           *xsk_bpool[DPAA1_XSK_MAX_QBANDS];\n"
if header.count(anchor) != 1:
    fail(f"expected one xsk_bpool field anchor, found {header.count(anchor)}")

header = header.replace(
    anchor,
    anchor
    + f"\t/* {MARKER}. */\n"
    + "\tstruct xsk_buff_pool       *xsk_pool_detach_pending"
    "[DPAA1_XSK_MAX_QBANDS];\n",
    1,
)

with open(HEADER, "w") as stream:
    stream.write(header)

print("### F-276: DPAA retains XSK owner across BMan drain retries")
