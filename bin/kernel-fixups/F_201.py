"""F-201: stop F-051 clobbering RSS hash distribution on non-ASK schemes.

ROOT CAUSE (board-confirmed 2026-08-16/17 on .185, all five RSS schemes 0-4):
keygen_scheme_setup() computes the correct RSS FQID-distribution word for a
hashing scheme at lines 647-661:

    kgse_hc = (hash_fqid_count - 1) | (hashShift << 24) | (sym ? ...)

For the mainline RSS schemes (eth0-4, hash_fqid_count = pcd_fqs_count = 128 via
keygen_port_hashing_init()) that yields kgse_hc = 127, spreading the CRC-64 hash
across 128 RX PCD FQs -> all four QMan portals/CPUs.

F-051 (2026-07-10) then UNCONDITIONALLY overwrites `scheme_regs.kgse_hc = 0;`
right before the hardware write, to isolate the exact-match ehash path. But the
clear fires for EVERY scheme, RSS or exact-match. Result on silicon: every RSS
scheme reads back range=0 / HMASK=0 -> 1 FQ -> a single portal -> CPU0.

SYMPTOM: software forwarding (nft flowtable, ASK disengaged) pins CPU0 at 100%
softirq and caps ~2.5 Gbps because every hashed flow lands on the base FQ only
(eth3 0x200, eth4 0x300); eth3/eth4 rx_dropped climbs into the millions on CPU0
while CPU1-3 stay idle. This is a latent RSS regression across the whole
software datapath, not an ASK-only defect.

FIX: guard the F-051 `kgse_hc = 0;` clear on the scheme's active engine role.
Only CC/FE exact-match modes (`next_engine == 2 || next_engine == 3`) need the
clear. RSS/direct-enqueue mode (`next_engine == 0`) keeps its computed kgse_hc
(128-way spread), including eth3/eth4 after ASK disengage: their persistent EKFC
override remains set, so `scheme->ekfc` is NOT a valid RSS-vs-ehash discriminator.
The ASK/exact-match arm path still gets kgse_hc = 0, so FE/ehash dispatch is
unchanged -- FQID distribution is bypassed there by design (spec
fman-keygen-flow-key-spec.md S5.4: hash_fqid_count irrelevant for ehash).
Policer mode (`next_engine == 1`) also retains its computed hash distribution;
F-051 was never intended to alter the policer path.

bmch/bmcl/ekdv zeroing is left unconditional (mainline RSS never sets bmch/bmcl;
ekdv is re-established by the use_hashing block and re-zeroed by F-179 only under
the ekfc override), so only the hash-distribution word is preserved.

Runs AFTER F-051 (mutate.py) and F-183 (which both emit the identical
`scheme_regs.kgse_hc   = 0;` line); anchors on the final derived text.
Count-gated, idempotent marker F-201; hard-fail on drift.

S0 QDRANT GATE: cross-checked against arch/fman-microcode-210-programming-
reference.md (kgse_fqb[27:24] range 0->1FQ / 7->128FQs; kgse_hc[31:16] HMASK),
spec fman-keygen-flow-key-spec.md S5.4, RM Ch.5. Qdrant confirms hash_fqid_count
drives KeyGen FQID spread BEFORE AC_CC/FE lookup, is irrelevant to the ehash
terminal, and 128 hashing PCD FQs is the DPAA-Ethernet default (BSP Ch.5:
"many DPAA Ethernet examples default to 128 hashing PCD queues"). No conflict.
"""

import sys

SRC = "drivers/net/ethernet/freescale/fman/fman_keygen.c"

with open(SRC) as f:
    src = f.read()

if "F-201" in src:
    print("### F-201 already applied")
    sys.exit(0)

# Exact F-051 + F-183-derived text (fman_keygen.c ~lines 700-714).
old = (
    "\t/* F-051: force-clear RSS mask/hash config for exact-match ehash */\n"
    "\tscheme_regs.kgse_bmch = 0;\n"
    "\tscheme_regs.kgse_bmcl = 0;\n"
    "\tscheme_regs.kgse_hc   = 0;\n"
    "\tscheme_regs.kgse_ekdv = 0;\n"
)

new = (
    "\t/* F-051: force-clear RSS mask/hash config for exact-match ehash */\n"
    "\tscheme_regs.kgse_bmch = 0;\n"
    "\tscheme_regs.kgse_bmcl = 0;\n"
    "\t/* F-201: only clear the hash-distribution word for CC/FE exact-match\n"
    "\t * schemes (next_engine 2/3). RSS/direct-enqueue (next_engine 0) and\n"
    "\t * policer (1) keep their computed kgse_hc so the CRC-64 hash spreads\n"
    "\t * across all 128 RX PCD FQs / 4 portals; F-051's unconditional clear\n"
    "\t * collapsed every RSS scheme to a single FQ (CPU0), capping software\n"
    "\t * forwarding at ~2.5 Gbps. eth3/eth4 keep a persistent EKFC override\n"
    "\t * after ASK disengage, so scheme->ekfc is NOT a valid discriminator --\n"
    "\t * the engine role is. The ehash terminal ignores FQID distribution. */\n"
    "\tif (enable && (scheme->next_engine == 2 || scheme->next_engine == 3))\n"
    "\t\tscheme_regs.kgse_hc   = 0;\n"
    "\tscheme_regs.kgse_ekdv = 0;\n"
)

if old not in src:
    print("### F-201: FATAL: F-051/F-183 kgse_hc-clear anchor not found -- "
          "F-051 or F-183 not applied or drifted. Refusing to guess.")
    sys.exit(1)

if src.count(old) != 1:
    print(f"### F-201: FATAL: expected 1 anchor match, got {src.count(old)}")
    sys.exit(1)

src = src.replace(old, new, 1)
with open(SRC, "w") as f:
    f.write(src)
print("### fman_keygen.c: F-201 preserve RSS kgse_hc (guard F-051 clear on "
      "CC/FE engine roles) applied (1 block)")
