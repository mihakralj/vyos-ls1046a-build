"""F-115: Fix DMA-index headroom mismatch (recover=0 bug) + diagnostic.

M4 ZC blocker: xsk_zc_eligible climbs but xsk_zc_rx_recovered stays 0.

Root cause: dpaa_xsk_build_dma_index() stores pool->heads[i].dma (the
chunk BASE dma) in xsk_chunk_dma[band][i].  But the seed/refill loops
store xsk_buff_xdp_get_dma(handle) — which is base + XDP_PACKET_HEADROOM
+ pool->headroom — into the BMan buffer.  FMan reports that same
headroom-adjusted address as qm_fd_addr(fd).  So dpaa_xsk_chunk_head_from_dma()
bsearches for (base+headroom) in an array of [base...] values and MISSES
every frame → recover never increments → redirect never fires.

FIX: build the DMA index from the same headroom-adjusted address the
seed loop uses, so the bsearch key matches qm_fd_addr(fd).

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

# ── 1. FIX: index build must store headroom-adjusted DMA ──
# Match:  priv->xsk_chunk_dma[band][i] = pool->heads[i].dma;
# (whitespace between tokens is flexible)
pat = re.compile(
    r'(priv->xsk_chunk_dma\[band\]\[i\]\s*=\s*)pool->heads\[i\]\.dma;')
matches = pat.findall(src)
if len(matches) == 1:
    src = pat.sub(
        r'\1pool->heads[i].dma + XDP_PACKET_HEADROOM + pool->headroom; '
        r'/* F-115: match qm_fd_addr(fd)=base+headroom */',
        src)
    changes += 1
    print("### F-115: index build now headroom-adjusted (base+XDP_PACKET_HEADROOM+pool->headroom)")
elif len(matches) == 0:
    print("### F-115: WARNING — index build assignment not found (already fixed?)")
else:
    print(f"### F-115: WARNING — expected 1 index assignment, found {len(matches)} — skipping fix")

# ── 2. DIAGNOSTIC: log the delta on a bsearch miss ──
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
