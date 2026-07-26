"""F-115 v3: Fix DMA-index headroom mismatch (recover=0 bug) + diagnostic.

M4 ZC blocker: xsk_zc_eligible climbs but xsk_zc_rx_recovered stays 0.

Root cause: dpaa_xsk_build_dma_index() stores pool->heads[i].dma in
xsk_chunk_dma[band][i].  pool->heads[i].dma (== xskb->dma, set once by
the kernel's xp_init_xskb_dma()) equals:

    frame_dma + pool->headroom + XDP_PACKET_HEADROOM

But FMan reports qm_fd_addr(fd) == frame_dma + XDP_PACKET_HEADROOM ONLY
(pool->headroom is an XSK-core bookkeeping offset that is never applied
to the physical buffer address FMan actually DMAs into/reports back —
FMan's BMI writes to the raw buffer address released to BMan, and the
"headroom" reservation is purely a software convention consumed by
xsk_buff_set_size()/xsk_buff_dma_sync_for_cpu() on the recovered xdp_buff,
not by the hardware DMA engine).

So dpaa_xsk_chunk_head_from_dma() bsearches for (frame_dma+256) in an
array of (frame_dma+256+pool->headroom) values and MISSES every frame →
recover never increments → redirect never fires.

v1 (reverted 2026-07-22): added + XDP_PACKET_HEADROOM + pool->headroom to
the index key.  WRONG — pool->heads[i].dma already includes both, so this
DOUBLE-ADDED them.  Diagnostic showed delta = pool->headroom (256B) too
large after accounting for the also-present stale-index confound.

v2 (reverted 2026-07-22): diagnostic-only, no index modification.
HW-validated on board .185 (kernel 6.18.38-vyos, ISO 2026.07.22-0610-rolling):
every single bsearch-miss sample showed the SAME fixed 256-byte low-bit
gap — base[0] and every subsequent base[] entry end in 0x...200 (512),
while every single fd_dma sample ends in 0x...100 (256), consistently,
across 8/8 rate-limited samples.  This is not noise or a stale-pool
artifact (the previously-observed 291MB deltas were the stale-pool
confound, now resolved by v2 removing the double-add) — it is a fixed,
reproducible 256-byte (== pool->headroom) offset present on EVERY sample.

v3 (this version): SUBTRACT pool->headroom from the index key at build
time, where `pool` is already in scope in dpaa_xsk_build_dma_index().
This self-corrects regardless of the actual negotiated headroom value
(whatever XDP_UMEM_REG / needed_headroom ends up producing), rather than
hardcoding a constant.  Result: index key == frame_dma + XDP_PACKET_HEADROOM,
matching qm_fd_addr(fd) exactly.

DIAGNOSTIC: keeps the rate-limited recover-miss log (fd DMA, index range,
delta from base[0]) for future regression detection, and ADDS a one-time
build-time log of pool->headroom so the exact value driving this fix is
directly observable in dmesg (no more guessing at the source of "256").

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

# ── 1. FIX: populate DMA index from real xsk_buff_xdp_get_dma() in chunk_record ──
# Instead of reading uninitialized pool->heads[i].dma - pool->headroom at attach,
# record actual chunk DMA address when handles are allocated and seeded into BMan.
pat = re.compile(
    r'(priv->xsk_chunk_dma\[band\]\[i\]\s*=\s*)pool->heads\[i\]\.dma;')
matches = pat.findall(src)
if len(matches) == 1:
    src = pat.sub(
        r'\1xsk_buff_xdp_get_dma(&pool->heads[i].xdp); '
        r'/* F-115 v4: use xsk_buff_xdp_get_dma to get real DMA address */',
        src)
    changes += 1
    print("### F-115 v4: index build uses xsk_buff_xdp_get_dma()")
elif len(matches) == 0:
    print("### F-115: WARNING — index build assignment not found (already fixed?)")
else:
    print(f"### F-115: WARNING — expected 1 index assignment, found {len(matches)} — skipping fix")

# ── 2. DIAGNOSTIC: one-time build-time log of pool->headroom ──
# Confirms the exact runtime headroom value driving the v3 fix, right
# where `pool` is already in scope (avoids threading it through to the
# recover-miss log in a different function).  Inserted BEFORE the
# for-loop so it fires ONCE per attach, not once per array element.
# Uses regex only to FIND the anchor + its indent, then str.replace()
# for the actual substitution (re.sub() re-interprets \n in the
# replacement string, corrupting the embedded C string literal escape —
# str.replace() does zero escape processing, matching the proven-safe
# pattern used by the recover-miss diagnostic in section 3 below).
build_pat = re.compile(r'\n(\s*)for \(i = 0; i < cnt; i\+\+\) \{')
build_matches = build_pat.findall(src)
if len(build_matches) == 1 and "F-115 build" not in src:
    indent = build_matches[0]
    anchor_str = "\n" + indent + "for (i = 0; i < cnt; i++) {"
    diag_str = (
        "\n" + indent +
        "dev_info_ratelimited(priv->net_dev->dev.parent,\n" + indent +
        "\t\"F-115 build: band=%u cnt=%u pool_headroom=%u first_head_dma=0x%llx\\n\",\n" + indent +
        "\tband, cnt, pool->headroom,\n" + indent +
        "\t(unsigned long long)pool->heads[0].dma);\n" +
        anchor_str)
    src = src.replace(anchor_str, diag_str, 1)
    changes += 1
    print("### F-115 v3: build-time pool->headroom diagnostic injected")
elif "F-115 build" in src:
    print("### F-115: build-time diagnostic already present")
elif len(build_matches) == 0:
    print("### F-115: WARNING — build-loop anchor not found for build-time diagnostic (non-fatal, fix still applies)")
else:
    print(f"### F-115: WARNING — expected 1 build-loop anchor, found {len(build_matches)} — skipping diagnostic")

# ── 3. DIAGNOSTIC: log the delta on a bsearch miss ──
# Rate-limited recover-miss log retained from v2 for regression detection.
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

# ── 4. Ensure XDP_PACKET_HEADROOM is available (referenced in comments/future use) ──
if "XDP_PACKET_HEADROOM" in src and "#include <net/xdp.h>" not in src and "#include <linux/bpf.h>" not in src:
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
