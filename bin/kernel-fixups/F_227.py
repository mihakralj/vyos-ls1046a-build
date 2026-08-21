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

FIX (defense-in-depth): before taking the !skb -> xdp_return_frame branch,
verify the FD is kernel-owned via fd->bpid == FSL_DPAA_BPID_INV. If the bpid
is a live BMan pool id, this is an FMan-forwarded HIT frame whose buffer was
already deallocated by FMan (confirmed FQ context_a has EBD=1), so we must
NOT dereference swbp AND must NOT dpaa_fd_release() it (double-free). Just
return NULL: the confirm is a no-op for a buffer FMan already recycled.

Legitimate skb (bpid 0xff, skb!=NULL) and XDP (bpid 0xff, skb==NULL) paths are
byte-for-byte unchanged. Cost: one already-present FD field read per confirm.

This is the crash-safety net paired with the ask_flow_offload.c fail-closed
change (a HIT should never reach a confirmed FQ; if a regression ever routes
one there again, this guard prevents the panic instead of crashing).

Count-gated, idempotent (marker "F-227"); hard-fail on any source drift.
"""

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

NEW = (
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

with open(ETH_C) as f:
    src = f.read()

if "F-227" in src:
    print("### F-227 already applied")
    sys.exit(0)

n = src.count(OLD)
if n != 1:
    print(f"### F-227: FATAL: swbp !skb branch expected 1 match in {ETH_C}, got {n}")
    sys.exit(1)

with open(ETH_C, "w") as f:
    f.write(src.replace(OLD, NEW, 1))

print(f"### dpaa_eth.c: F-227 crash-safe TX-confirm guard applied (1 block)")
