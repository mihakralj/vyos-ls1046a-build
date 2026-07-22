"""F-115 v2: Fix DMA-index headroom mismatch (recover=0 bug) + diagnostic.

M4 ZC blocker: xsk_zc_eligible climbs but xsk_zc_rx_recovered stays 0.

Root cause: dpaa_xsk_build_dma_index() stores pool->heads[i].dma (the
chunk BASE dma) in xsk_chunk_dma[band][i].  But the seed/refill loops
store xsk_buff_xdp_get_dma(handle) — which is base + XDP_PACKET_HEADROOM
— into the BMan buffer.  FMan reports that same headroom-adjusted address
as qm_fd_addr(fd).  So dpaa_xsk_chunk_head_from_dma() bsearches for
(base+256) in an array of [base...] values and MISSES every frame →
recover never increments → redirect never fires.

v2 (2026-07-22): HW-validated on board .185.  The v1 fix added
pool->headroom to the index key, but the F-115 diagnostic showed the
delta was exactly pool->headroom (256 bytes) too large.  xsk_buff_xdp_get_dma()
returns base + XDP_PACKET_HEADROOM only — pool->headroom is an internal
XSK offset NOT reflected in the DMA address.  v2 removes pool->headroom
from the index key.

FIX: build the DMA index from base + XDP_PACKET_HEADROOM (256 bytes),
matching what xsk_buff_xdp_get_dma() stores in BMan and what FMan
reports as qm_fd_addr(fd).

DIAGNOSTIC: on a bsearch miss, rate-limited-log the fd DMA, the index
range [0]..[cnt-1], and the delta from base[0], so if the headroom
arithmetic is still off we can read the exact offset.

Disposition: fold-into 0103b once validated on silicon.
Upstream-Status: Inappropriate [LS1046A DPAA1 AF_XDP ZC]
Risk-Tier: C (edits af_xdp_pool_main.c hot-path lookup)
"""

import sys, os, re

path = "drivers/net/ethernet/freescale/dpaa/af_xdp_pool/af_xdp_pool_main.c"
if not os.path.exists(path):
    print("### F-115: af_xdp_pool_main.c not found (M4 ZC path absent)")
    sys.exit(0)

with open(path) as f:
    src = f.read()

changes = 0

# ── 1. DIAGNOSTIC ONLY: NO index modification needed ──
# The original code `pool->heads[i].dma` is CORRECT.
# xp_init_xskb_dma() sets xskb->dma = frame_dma + pool->headroom + XDP_PACKET_HEADROOM.
# The FMan reports this exact value as qm_fd_addr(fd).
# The bsearch key matches the index key — no headroom adjustment needed.
#
# v1 (reverted): added + XDP_PACKET_HEADROOM + pool->headroom, which
# DOUBLE-ADDED the headroom (pool->heads[i].dma already includes both).
# HW-validated 2026-07-22: the F-115 diagnostic showed delta = pool->headroom
# (256 bytes) too large, confirming the double-add.
#
# v2: diagnostic-only.  The recover=0 bug observed in T-M4-4b was caused by
# a STALE DMA index (from a previous attach), not a headroom mismatch.
# The stale-index root cause is still under investigation.

# ── 2. DIAGNOSTIC: log the delta on a bsearch miss ──
# This is the PRIMARY purpose of F-115 v2.  The diagnostic helped identify
# that the v1 headroom fix was wrong (delta = pool->headroom too large) and
# that the real issue is a stale DMA index from a previous attach.
# Anchor on the bsearch call, insert a rate-limited log after the miss check.
anchor = "hit = bsearch(&dma, base, cnt, sizeof(*base), dpaa_xsk_dma_cmp);"
if anchor in src and "F-115 recover-miss" not in src:
    diag = (anchor + "\n"
            "\tif (unlikely(!hit)) {\n"
            "\t\tstatic int _f115_n;\n"
            "\t\tif (_f115_n < 8) {\n"
            "\t\t\t_f115_n++;\n"
            "\t\t\tpr_err(\"F-115 recover-miss: fd_dma=0x%llx band=%u cnt=%u base[0]=0x%llx base[cnt-1]=0x%llx delta0=%lld\\n\",\n"
            "\t\t\t       (unsigned long long)dma, band, cnt,\n"
            "\t\t\t       (unsigned long long)base[0],\n"
            "\t\t\t       (unsigned long long)base[cnt-1],\n"
            "\t\t\t       (long long)((s64)dma - (s64)base[0]));\n"
            "\t\t}\n"
            "\t}")
    src = src.replace(anchor, diag, 1)
    changes += 1
    print("### F-115: recover-miss diagnostic injected (rate-limited x8)")
elif "F-115 recover-miss" in src:
    print("### F-115: diagnostic already present")
else:
    print("### F-115: WARNING — bsearch anchor not found for diagnostic")

# ── 3. Ensure XDP_PACKET_HEADROOM is available ──
if "XDP_PACKET_HEADROOM" in src and "#include <net/xdp.h>" not in src and "#include <linux/bpf.h>" not in src:
    # Insert include after the first #include line
    m = re.search(r'#include\s+<[^>]+>\n', src)
    if m:
        src = src[:m.end()] + "#include <net/xdp.h>\t/* F-115: XDP_PACKET_HEADROOM */\n" + src[m.end():]
        changes += 1
        print("### F-115: added #include <net/xdp.h>")

if changes:
    with open(path, "w") as f:
        f.write(src)
    print(f"### F-115: {changes} change(s) applied")
else:
    print("### F-115: no changes applied")
