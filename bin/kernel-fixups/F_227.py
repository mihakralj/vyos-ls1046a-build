"""F-227 (2026-08-21): crash-safe TX-confirm guard for FMan-forwarded HIT FDs.

PANIC (image 2047, board .185, sustained mixed v4+v6 offload soak, ~t=184s):
  pc  xdp_return_frame+0x1c
  lr  dpaa_cleanup_tx_fd+0x238
  conf_dflt_dqrr -> dpaa_tx_conf -> dpaa_cleanup_tx_fd
  "NULL pointer dereference" + "deadbeefdeadbfcb" (poisoned pointer)

ROOT CAUSE: dpaa_cleanup_tx_fd() treats the first bytes of every TX-confirm
frame buffer as a `struct dpaa_eth_swbp` and, when swbp->skb == NULL, assumes
an XDP TX frame and calls xdp_return_frame(swbp->xdpf). That is valid ONLY for
kernel-originated TX buffers (skb TX and XDP TX), which always carry
fd->bpid == FSL_DPAA_BPID_INV (0xff) — set in skb_to_contig_fd /
skb_to_sg_fd / dpaa_xdp_xmit_frame.

An FMan-HIT-forwarded frame that lands a TX-confirm FD on a CONFIRMED egress
FQ (B0V=1) carries a buffer from an RX BMan pool (fd->bpid = a valid RX pool
id, NOT 0xff) and has NO dpaa_eth_swbp written. swbp->skb reads garbage
(NULL), so the code dereferences a garbage swbp->xdpf -> panic.

FIX (defense-in-depth): before the diagnostic pointer capture, TX DMA unmap
or backpointer access, reject real-BPID FDs, which may refer to RX-pool rather
than kernel TX mappings. A confirmation may describe a buffer already
recycled by FMan, while an error may still own it. Do not publish, unmap,
dereference or release it here; log the unknown ownership. An invalid BPID is
necessary but not sufficient for normal skb/XDP cleanup: XSK zero-copy FDs
also use it and must be claimed by their owner before reaching this function.
This guard does not resolve error routing or recover retained buffers. F-317
subsequently retires the diagnostic capture of ordinary TX buffers.

Legitimate skb (bpid 0xff, skb!=NULL) and XDP (bpid 0xff, skb==NULL) cleanup
behaviour is otherwise unchanged. Cost: one already-present FD field read per
confirm.

This is the crash-safety net paired with the ask_flow_offload.c fail-closed
change (a HIT should never reach a confirmed FQ; if a regression ever routes
one there again, this guard prevents the panic instead of crashing).

Count-gated, idempotent, and upgrades the original guard after the unmaps.
Hard-fail on unknown or ambiguous source drift.
"""

import re
import sys

ETH_C = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"

OLD = (
    "\tswbp = (struct dpaa_eth_swbp *)vaddr;\n"
    "\tskb = swbp->skb;\n"
    "\n"
    "\t/* No skb backpointer is set when running XDP. An xdp_frame\n"
    "\t * backpointer is saved instead.\n"
    "\t */\n"
    "\tif (!skb) {\n"
    "\t\txdp_return_frame(swbp->xdpf);\n"
    "\t\treturn NULL;\n"
    "\t}\n"
)

LEGACY_NEW = (
    "\t/* F-227 (2026-08-21): reject FMan-forwarded HIT FDs before touching\n"
    "\t * swbp. Kernel skb/XDP TX confirms always carry FSL_DPAA_BPID_INV\n"
    "\t * (set in skb_to_contig_fd/skb_to_sg_fd/dpaa_xdp_xmit_frame). A HIT\n"
    "\t * frame that landed a confirm FD on a confirmed egress FQ carries a\n"
    "\t * live RX BMan pool id and has NO dpaa_eth_swbp; its buffer was\n"
    "\t * already deallocated by FMan (EBD=1). Dereferencing swbp->xdpf here\n"
    "\t * panics (NULL/garbage), and dpaa_fd_release() would double-free.\n"
    "\t * Ignore the confirm: nothing to free on the host side. This guard\n"
    "\t * should never fire in a correct config (ask.ko fails closed to SW\n"
    "\t * unless it has a no-confirm FQ); a firing means a HIT reached a\n"
    "\t * confirmed FQ, so log it ratelimited for diagnosis. */\n"
    "\tif (unlikely(fd->bpid != FSL_DPAA_BPID_INV)) {\n"
    "\t\tnet_warn_ratelimited(\"%s: F-227 dropped confirm for FMan HIT FD bpid=%u (HIT on confirmed FQ)\\n\",\n"
    "\t\t\t\t     priv->net_dev->name, fd->bpid);\n"
    "\t\treturn NULL;\n"
    "\t}\n"
    "\n"
    "\tswbp = (struct dpaa_eth_swbp *)vaddr;\n"
    "\tskb = swbp->skb;\n"
    "\n"
    "\t/* No skb backpointer is set when running XDP. An xdp_frame\n"
    "\t * backpointer is saved instead.\n"
    "\t */\n"
    "\tif (!skb) {\n"
    "\t\txdp_return_frame(swbp->xdpf);\n"
    "\t\treturn NULL;\n"
    "\t}\n"
)

DMA_START = (
    "\tif (unlikely(qm_fd_get_format(fd) == qm_fd_sg)) {\n"
    "\t\tdma_unmap_page(priv->tx_dma_dev, addr,\n"
)

IC_COMMENT = "\t/* F-069a v9: capture frame-data vaddr (no bp var found) */\n"
IC_MANGLED = (
    IC_COMMENT +
    "\tfman_pcd_ic_vaddr = vaddr;\tconst struct qm_sg_entry *sgt;\n"
)
IC_CAPTURE = IC_COMMENT + "\tfman_pcd_ic_vaddr = vaddr;\n\n"

GUARD = (
    "\t/* F-227: a real-BPID FD is not a kernel skb/XDP TX mapping. Its\n"
    "\t * ownership is unknown here: FMan may have recycled a successful\n"
    "\t * frame or returned a still-owned error. Do not unmap or release it.\n"
    "\t */\n"
    "\tif (unlikely(fd->bpid != FSL_DPAA_BPID_INV)) {\n"
    "\t\tnet_warn_ratelimited(\"%s: F-227 skipped TX cleanup for bpid=%u; buffer ownership unknown\\n\",\n"
    "\t\t\t\t     priv->net_dev->name, fd->bpid);\n"
    "\t\treturn NULL;\n"
    "\t}\n"
    "\n"
)

with open(ETH_C) as f:
    src = f.read()

matches = list(re.finditer(
    r"(?m)^(?:static )?struct sk_buff \*dpaa_cleanup_tx_fd\([^;]*?\n\{\n",
    src,
))
if len(matches) != 1:
    print(f"### F-227: FATAL: expected one dpaa_cleanup_tx_fd in {ETH_C}, "
          f"got {len(matches)}")
    sys.exit(1)
match = matches[0]
end = src.find("\n}\n", match.end())
if end < 0:
    print(f"### F-227: FATAL: dpaa_cleanup_tx_fd end not found in {ETH_C}")
    sys.exit(1)
end += 3
body = src[match.start():end]
original = body

if "F-227" in body:
    if (body.count(GUARD) == 1 and body.count(DMA_START) == 1 and
            body.count(OLD) == 1 and
            body.index(GUARD) < body.index(DMA_START) < body.index(OLD)):
        body = body.replace(GUARD, "", 1)
    elif body.count(LEGACY_NEW) == 1:
        body = body.replace(LEGACY_NEW, OLD, 1)
    else:
        print(f"### F-227: FATAL: unrecognized existing guard in {ETH_C}")
        sys.exit(1)
    if "F-227" in body:
        print(f"### F-227: FATAL: unexpected extra guard marker in {ETH_C}")
        sys.exit(1)

capture_count = body.count("fman_pcd_ic_vaddr")
if capture_count == 0:
    capture = ""
elif capture_count == 1 and body.count(IC_MANGLED) == 1:
    body = body.replace(IC_MANGLED, "\tconst struct qm_sg_entry *sgt;\n", 1)
    capture = IC_CAPTURE
elif capture_count == 1 and body.count(IC_CAPTURE) == 1:
    body = body.replace(IC_CAPTURE, "", 1)
    capture = IC_CAPTURE
else:
    print(f"### F-227: FATAL: unrecognized diagnostic capture in {ETH_C}")
    sys.exit(1)

n_swbp = body.count(OLD)
n_dma = body.count(DMA_START)
if n_swbp != 1 or n_dma != 1 or body.index(DMA_START) > body.index(OLD):
    print(f"### F-227: FATAL: expected one ordered DMA/swbp anchor in {ETH_C}, "
          f"got DMA={n_dma}, swbp={n_swbp}")
    sys.exit(1)

body = body.replace(DMA_START, GUARD + capture + DMA_START, 1)
if body == original:
    print("### F-227 already applied before diagnostic capture and TX DMA unmaps")
    sys.exit(0)

with open(ETH_C, "w") as f:
    f.write(src[:match.start()] + body + src[end:])

print("### dpaa_eth.c: F-227 guard applied before capture and TX DMA unmaps (1 block)")
