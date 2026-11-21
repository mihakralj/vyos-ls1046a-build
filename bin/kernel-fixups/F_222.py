"""F-222: revert DPAA RX buffers from order-1 back to order-0 (robust under load).

ROOT CAUSE (board .185, 2026-08-21, image 0236): under sustained ~800 Mbit/s
routed transit through a newly-engaged 1G port (eth2), the DPAA RX refill path
flooded the log with `fsl_dpaa_mac ...: dev_alloc_pages() failed` and the port
went RX-deaf, wedging the board (watchdog reset, ~50 s recovery). This was NOT
global memory exhaustion (MemFree ~6.8 GB at the time); it is order-1 (8 KiB
CONTIGUOUS) page-allocation failure under fragmentation in the atomic softirq
refill context. buddyinfo at idle showed only ~1600 order-1 blocks free in the
Normal zone, which a sustained high-pps RX burst drains to zero.

F-203 introduced the order-1 buffer (DPAA_BP_ORDER=1, DPAA_BP_RAW_SIZE=8192)
to carry jumbo-ish frames in one contiguous buffer. Short hardware tests at MTU
7000 passed, but did not establish sustained-load reliability; the subsequent
order-1 refill failure prompted this revert. The failure was observed on eth2,
not independently demonstrated on every port.

FIX: flip the single define DPAA_BP_ORDER 1 -> 0. Every F-203 site references
DPAA_BP_ORDER (the RX seed/refill dev_alloc_pages(DPAA_BP_ORDER), all RX-buffer
free_pages(..., DPAA_BP_ORDER), and DPAA_BP_RAW_SIZE = 4096 << DPAA_BP_ORDER),
so this one change reverts the whole RX-buffer path to the order-0 4 KiB-page
model consistently. DPAA_BP_RAW_SIZE becomes 4096; larger frames may span
multiple buffers rather than fitting in one.

The companion ASK MTU cap of 3600 was historical, not an inherent order-0
ceiling. Patch 0177 formerly limited mEMAC MAXFRM to dpaa_bp->size; that clamp
was removed and the current VyOS ASK guards permit 1280-9000. This permitted
range is not a claim that the entire range or every traffic class is validated.

CPU RX has an SG-to-skb path (sg_fd_to_skb()), distinct from ASK FE forwarding.
The FE hit path has separately passed an MTU-9000 hardware test using multi-buffer
frames. An intermittent 9k bidirectional hardware collapse remains open, and
the WireGuard fast path drops SG frames; neither is resolved by that FE test.

Must run AFTER F-203 (which creates the DPAA_BP_ORDER define). Idempotent.
"""

import os
import sys

path = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
if not os.path.exists(path):
    print("### F-222: dpaa_eth.c not found")
    sys.exit(0)

with open(path) as f:
    src = f.read()

marker = "F-222(order0-revert)"
if marker in src:
    print("### F-222: already applied")
    sys.exit(0)

old = "#define DPAA_BP_ORDER 1\n"
new = (
    "/* F-222(order0-revert): order-1 (8 KiB) RX buffers wedge the port under\n"
    " * sustained load via dev_alloc_pages() failure on a fragmented Normal zone\n"
    " * (board .185 2026-08-21, ~800 Mbit/s routed transit, MemFree ~6.8 GB).\n"
    " * Restore order-0 4 KiB RX buffers; all F-203 sites derive from this\n"
    " * define. The companion 3600 ASK MTU cap was historical: current guards\n"
    " * permit 1280-9000, not proof of validation across the entire range.\n"
    " * CPU RX has sg_fd_to_skb(); FE forwarding of SG frames was separately\n"
    " * tested at MTU 9000. Intermittent 9k bidirectional HW collapse remains\n"
    " * open, and the WireGuard fast path drops SG frames. */\n"
    "#define DPAA_BP_ORDER 0\n"
)

if old not in src:
    print("### F-222: FATAL: DPAA_BP_ORDER define not found — F-203 must run first / source drifted")
    sys.exit(1)
if src.count(old) != 1:
    print(f"### F-222: FATAL: DPAA_BP_ORDER define not unique ({src.count(old)} matches)")
    sys.exit(1)

src = src.replace(old, new, 1)

with open(path, "w") as f:
    f.write(src)

print("### dpaa_eth.c: F-222 reverted DPAA_BP_ORDER 1 -> 0 (robust order-0 RX buffers)")
