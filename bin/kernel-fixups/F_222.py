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
purely to carry jumbo-ish MTU (3600-7000) in a single contiguous buffer so
oversized frames stay eligible for the ASK FE hardware-offload path. Its own
docstring flagged this exact risk: "Watch for `dev_alloc_pages() failed` / RX
depletion under memory fragmentation; that is the only real order-1 risk."

The order-1 buffer is a LATENT WEDGE for EVERY port under sustained load (eth2
merely hit it first, being the first high-pps test of the session; eth3/eth4
would wedge identically at the same rate). The MTU 3600-7000 jumbo capability it
enabled was never silicon-validated ("pending cold-boot validation" per the
master plan) and is a niche relative to robust standard-MTU forwarding on all
five ports, which is the actual product requirement.

FIX: flip the single define DPAA_BP_ORDER 1 -> 0. Every F-203 site references
DPAA_BP_ORDER (the RX seed/refill dev_alloc_pages(DPAA_BP_ORDER), all RX-buffer
free_pages(..., DPAA_BP_ORDER), and DPAA_BP_RAW_SIZE = 4096 << DPAA_BP_ORDER),
so this one change reverts the whole RX-buffer path to the mainline-proven
order-0 single-4 KiB-page model atomically and consistently:
  * DPAA_BP_RAW_SIZE      -> 4096
  * dev_alloc_pages(0), free_pages(..., 0)   (order-0; ~never fails at GBs free)
  * usable dpaa_bp->size  -> SKB_WITH_OVERHEAD(4096) ~= 3712, MTU ceiling ~3600

ASK FE offload at standard MTU is UNAFFECTED: the E25/E26 silicon HITs and the
per-port-table (F-220/F-221) work all ran on order-0 4 KiB buffers before F-203.
The per-port five-port v4 engage/table/FQ results from this session are
independent of buffer order.

CONSEQUENCE: ASK MTU must be clamped back to the mainline single-4K-buffer limit
(~3600). The companion change lowers the vyos-1x-036/037 ASK MTU clamp from 7000
to 3600 so an oversized-frame configuration cannot wedge RX the other way
(fsl_fm_max_frm=9600 still lets the MAC accept a >4 KiB wire frame that no
order-0 buffer can hold). Restoring true jumbo (>3600) later needs a proper
page_pool / multi-size BMan pool or RX scatter-gather reassembly, not order-1.

S0 QDRANT GATE: cross-checked DPAA_BP_RAW_SIZE / dpaa_bp_size / dpaa_change_mtu
ceiling, the order-1 fragmentation risk (F-203 docstring + this board result),
and that ASK offload validated on order-0. No conflict.

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
    " * Revert to the mainline-proven order-0 single-4 KiB-page RX buffer. All\n"
    " * F-203 sites derive from this define, so this one line reverts them all;\n"
    " * DPAA_BP_RAW_SIZE becomes 4096 and the ASK MTU clamp is lowered to ~3600\n"
    " * (vyos-1x-036/037). ASK FE offload at standard MTU is unaffected (E25/E26\n"
    " * HITs and F-220/F-221 per-port tables ran on order-0). */\n"
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
