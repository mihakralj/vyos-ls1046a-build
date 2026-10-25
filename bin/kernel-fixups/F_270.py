"""F-270 (engaged-port oversize wedge, 2026-10-22): ENQUEUE mtu 0xffff on every
record that carries no hardware MTU check.

The inline ehash emitter wrote ENQUEUE mtu 1500, bpid 0, word2 0 on plain
routed records. With the F-262 advanced-offload RX-port triple applied the
microcode honours that threshold: the first IP packet longer than 1500 bytes
takes the fragmentation path with no frag pool and no frag-info block, and the
ingress port goes RX-deaf until a cold power-cycle (.185, 2026-10-22, UDP
size sweep at MTU 2500: payload 1472 forwarded, payload 1473 wedged eth3 on
the first frame; eth3 disengaged forwards the same traffic at 8.6 Gbit/s).
The 2026-08-17 MTU battery passed 1280-2500 before the triple existed.

EN_EHASH_DISABLE_FRAG = 0xffff is what the vendor writes where it does no
fragmentation (bridge and PPPoE-relay records), and F-261/F-268 already use it.
Records that need a hardware MTU check (F-262: 05 PREEMPTIVE_CHECKS, a real
frag pool) overwrite the field with the egress MTU afterwards, unchanged.

Must run after F-268. Idempotent; exits non-zero unless the anchor matches
exactly once.
"""

import sys

MARK = "F-270"
PATH = "drivers/net/ethernet/freescale/fman/fman_pcd.c"

OLD = (
    "\t\t*(__be16 *)(r + enqueue_off + 0) =\n"
    "\t\t\tcpu_to_be16(vlan && (vlan->flags &\n"
    "\t\t\t\t\t    (FMAN_PCD_VLANF_PPPOE_INSERT |\n"
    "\t\t\t\t\t     FMAN_PCD_VLANF_BRIDGE)) ?\n"
    "\t\t\t\t    0xffff : 1500);\t/* F-268: bridge */\n"
)
NEW = (
    "\t\t/* F-270: EN_EHASH_DISABLE_FRAG. No frag pool/info exists unless\n"
    "\t\t * F-262 seals a real MTU check below; 1500 here wedged the port\n"
    "\t\t * on the first IP packet above it. */\n"
    "\t\t*(__be16 *)(r + enqueue_off + 0) = cpu_to_be16(0xffff);\n"
)

try:
    with open(PATH) as f:
        s = f.read()
except FileNotFoundError:
    print(f"### F-270: FATAL: {PATH} not found")
    sys.exit(1)

if MARK in s:
    print("### F-270: already applied")
    sys.exit(0)

n = s.count(OLD)
if n != 1:
    print(f"### F-270: FATAL: anchor in {PATH} found {n} times (expected 1)")
    sys.exit(1)

with open(PATH, "w") as f:
    f.write(s.replace(OLD, NEW))
print("### F-270: ehash ENQUEUE mtu 0xffff on records without a hardware MTU check")
