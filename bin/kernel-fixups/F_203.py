"""F-203: order-1 (8 KiB) DPAA RX buffers to allow larger MTU.

WHY: mainline dpaa_eth sizes each RX buffer as one order-0 page
(DPAA_BP_RAW_SIZE=4096 -> usable ~3712, MTU ceiling ~3600). dpaa_change_mtu()
rejects any MTU that does not fit ONE contiguous RX buffer, and the LS1046A
mEMAC (fsl_fm_max_frm=9600) will still ACCEPT a larger wire frame that then
has no buffer big enough -> FM_FD_ERR_PHYSICAL -> RX-deaf wedge (cold-boot
recovery only). To carry jumbo-ish MTU while keeping frames CONTIGUOUS (so the
ASK/FMan FE hardware-offload path, which is validated only on single-buffer
frames, still works), enlarge the RX buffer to an order-1 (8 KiB) page.

The alternative (enable FMan multi-buffer RX scatter/gather) keeps 4 KiB pages
but delivers jumbo frames as SG lists: sg_fd_to_skb() is the slow non-linear
path AND the ASK FE rewrite/enqueue is not silicon-verified on SG frames, so
jumbo would drop to software. Order-1 keeps jumbo offloadable — the correct
choice for an offload-first product.

WHAT (RX-buffer sites only; TX SGT page and XDP-TX copy page stay order-0):
  * DPAA_BP_RAW_SIZE 4096 -> 8192 via a named DPAA_BP_ORDER=1.
  * dpaa_bp_add_8_bufs(): dev_alloc_pages(0) -> dev_alloc_pages(DPAA_BP_ORDER)
    (the RX pool seed/refill; the DMA map already uses DPAA_BP_RAW_SIZE so it
    scales automatically).
  * All RX-buffer frees free_pages(vaddr,0) -> free_pages(vaddr,DPAA_BP_ORDER):
    contig_fd_to_skb free_buffer, sg_fd_to_skb (SGT + members + error), and the
    XDP DROP/ABORTED/REDIRECT/xmit-fail buffer frees in dpaa_rx.
  * dpaa_bp_free_pf(): skb_free_frag() already frees via compound_head, correct
    for an order-1 compound page — left unchanged.

NOT CHANGED (must stay order-0):
  * TX SGT page in dpaa_start_xmit's sg builder (dev_alloc_pages(0) @ the SGT
    page + its free_pages(buff_start,0)) — a 256B SGT scratch page, not an RX
    buffer.
  * dpaa_xdp_realign()/A050385 copy page + MEM_TYPE_PAGE_ORDER0 — the erratum
    path is runtime-INACTIVE on LS1046A (no fsl,erratum-a050385 DT prop), so it
    never runs here; leaving it order-0 is safe and avoids touching XDP frame
    accounting.

MEMORY: RX pool doubles (128 buf/CPU x 4 CPU x 2 ports x 8K ~= 8 MiB, was
~4 MiB) — negligible on 2 GiB. Watch for `dev_alloc_pages() failed` / RX
depletion under memory fragmentation; that is the only real order-1 risk and
it manifests as drops, not per-frame slowdown (the datapath code is unchanged).

MTU: usable = SKB_WITH_OVERHEAD(8192) ~= 7808; dpaa_change_mtu allows
mtu+VLAN_ETH_HLEN+ETH_FCS_LEN <= size-rx_headroom -> max MTU ~7530. The VyOS
clamps are raised to a conservative 7000 (vyos-1x-036/037).

S0 QDRANT GATE: cross-checked DPAA_BP_RAW_SIZE/dpaa_bp_size, the RX SG
consumer (sg_fd_to_skb) vs no-SG-produced reality, A050385 runtime-inactive on
LS1046A, and BMan pool sizing. No conflict; qdrant's "oversized frame wedges"
root cause is exactly what this addresses.
"""

import re
import sys

SRC = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"

with open(SRC) as f:
    src = f.read()

if "DPAA_BP_ORDER" in src:
    print("### F-203 already applied")
    sys.exit(0)

edits = []

# 1) Define DPAA_BP_ORDER and derive DPAA_BP_RAW_SIZE from it.
old_def = "#define DPAA_BP_RAW_SIZE 4096\n"
new_def = (
    "/* F-203: order-1 (8 KiB) RX buffers so jumbo-ish MTU frames arrive in a\n"
    " * single contiguous buffer (keeps them eligible for ASK FE HW offload).\n"
    " */\n"
    "#define DPAA_BP_ORDER 1\n"
    "#define DPAA_BP_RAW_SIZE (4096 << DPAA_BP_ORDER)\n"
)
edits.append(("bp-raw-size define", old_def, new_def, 1))

# 2) RX pool seed/refill allocation -> order-1.
old_alloc = "\t\tp = dev_alloc_pages(0);\n"
new_alloc = "\t\tp = dev_alloc_pages(DPAA_BP_ORDER);\n"
# There are two dev_alloc_pages(0) with this exact indentation? The RX one is
# inside the 8-buf loop (\t\t). The XDP-TX copy uses a different indent (\t).
# Anchor on the RX one via surrounding context to stay unique.
old_alloc_ctx = (
    "\tfor (i = 0; i < 8; i++) {\n"
    "\t\tp = dev_alloc_pages(0);\n"
)
new_alloc_ctx = (
    "\tfor (i = 0; i < 8; i++) {\n"
    "\t\tp = dev_alloc_pages(DPAA_BP_ORDER);\n"
)
edits.append(("rx pool alloc order", old_alloc_ctx, new_alloc_ctx, 1))

# 3) contig_fd_to_skb free_buffer path.
edits.append((
    "contig free_buffer",
    "free_buffer:\n\tfree_pages((unsigned long)vaddr, 0);\n\treturn NULL;\n}\n",
    "free_buffer:\n\tfree_pages((unsigned long)vaddr, DPAA_BP_ORDER);\n\treturn NULL;\n}\n",
    1,
))

# 4) sg_fd_to_skb: free the SGT buffer (success tail).
edits.append((
    "sg success SGT free",
    "\t/* free the SG table buffer */\n\tfree_pages((unsigned long)vaddr, 0);\n\n\treturn skb;\n",
    "\t/* free the SG table buffer */\n\tfree_pages((unsigned long)vaddr, DPAA_BP_ORDER);\n\n\treturn skb;\n",
    1,
))

# 5) sg_fd_to_skb: error path member free.
edits.append((
    "sg error member free",
    "\t\tfree_pages((unsigned long)sg_vaddr, 0);\n",
    "\t\tfree_pages((unsigned long)sg_vaddr, DPAA_BP_ORDER);\n",
    1,
))

# 6) sg_fd_to_skb: error path final SGT fragment free.
edits.append((
    "sg error SGT free",
    "\t/* free the SGT fragment */\n\tfree_pages((unsigned long)vaddr, 0);\n\n\treturn NULL;\n",
    "\t/* free the SGT fragment */\n\tfree_pages((unsigned long)vaddr, DPAA_BP_ORDER);\n\n\treturn NULL;\n",
    1,
))

# 7) dpaa_rx XDP S/G-not-supported free.
edits.append((
    "xdp sg-unsupported free",
    "\t\t\tdpaa_release_sgt_members(sgt);\n\t\t\tfree_pages((unsigned long)vaddr, 0);\n",
    "\t\t\tdpaa_release_sgt_members(sgt);\n\t\t\tfree_pages((unsigned long)vaddr, DPAA_BP_ORDER);\n",
    1,
))

# 8) XDP_TX convert-fail free.
edits.append((
    "xdp tx convert-fail free",
    "\t\tif (unlikely(!xdpf)) {\n\t\t\tfree_pages((unsigned long)vaddr, 0);\n",
    "\t\tif (unlikely(!xdpf)) {\n\t\t\tfree_pages((unsigned long)vaddr, DPAA_BP_ORDER);\n",
    1,
))

# 9) XDP_REDIRECT error free.
edits.append((
    "xdp redirect-fail free",
    "\t\t\ttrace_xdp_exception(priv->net_dev, xdp_prog, xdp_act);\n\t\t\tfree_pages((unsigned long)vaddr, 0);\n\t\t}\n\t\tbreak;\n\tdefault:\n",
    "\t\t\ttrace_xdp_exception(priv->net_dev, xdp_prog, xdp_act);\n\t\t\tfree_pages((unsigned long)vaddr, DPAA_BP_ORDER);\n\t\t}\n\t\tbreak;\n\tdefault:\n",
    1,
))

# 10) XDP_DROP free.
edits.append((
    "xdp drop free",
    "\t\t/* Free the buffer */\n\t\tfree_pages((unsigned long)vaddr, 0);\n\t\tbreak;\n",
    "\t\t/* Free the buffer */\n\t\tfree_pages((unsigned long)vaddr, DPAA_BP_ORDER);\n\t\tbreak;\n",
    1,
))

for name, old, new, want in edits:
    n = src.count(old)
    if n != want:
        print(f"### F-203: FATAL: '{name}': expected {want} match, got {n}")
        sys.exit(1)
    src = src.replace(old, new, 1)

with open(SRC, "w") as f:
    f.write(src)

print(f"### dpaa_eth.c: F-203 order-1 RX buffers applied ({len(edits)} sites)")
