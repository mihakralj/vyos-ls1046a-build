"""F-268 (T-M6-2 B7b bridge VLAN pop/push records, 2026-10-19): a bridge flag
for the inline ehash record emitter.

A bridged frame between an access port and a trunk changes its 802.1Q tag
(pop or push) but is otherwise forwarded untouched. The routed VLAN chain is
silicon-proven (2026-10-05, .185): pop 04 11 12 21 41 01, push
04 11 12 21 42 41 01 (push needs patch 0217's RX headroom). It always puts a
TTL/hop-limit decrement (0x21/0x29) in front of the L2 rebuild, which a
bridge must never do, and caps the ENQUEUE mtu at 1500.

New FMAN_PCD_VLANF_BRIDGE (bit 4) on an L2/TX record:
  - no UPDATE_TTL 0x21 / UPDATE_HOPLIMIT 0x29: pop 04 11 12 41 01,
    push 04 11 12 42 41 01;
  - ENQUEUE mtu 0xffff (EN_EHASH_DISABLE_FRAG), like the vendor
    cdx_ehash.c fill_bridge_actions() (EHASH_BRIDGE_FLOW, mtu 0xffff,
    STRIP_ETH -> STRIP_ALL_VLAN -> optional INSERT_VLAN -> ethernet_hm ->
    enqueue, no TTL). A push grows the frame by 4; the bridge has no MTU
    check on that path either.
INSERT_L2_HDR rewrites DA/SA with the frame's own addresses (ask.ko passes
the record key's DA/SA). STRIP_ALL_VLAN keeps the routed encoding: real
ingress VID + VALIDATE for a pop, VID 0 for a push (untagged ingress, proven
by the routed push-only record). The vendor's OP_SKIP_VLAN_VALIDATE (bit 0)
is not used.

The emitter refuses (-EINVAL) a bridge record without the L2/TX terminal,
without POP or PUSH (keeps every bridge record on a proven chain), or with
NAT or an egress MTU (both are L3 edits). Records without the flag are
byte-identical. Dormant until ask.ko sets it (B7b, ask.bridge_offload).
Must run after F-267. Idempotent; exits non-zero unless every anchor matches
the expected number of times.
"""

import sys

MARK = "F-268"

EDITS = [
    ("include/linux/fsl/fman_pcd.h",
     "#define FMAN_PCD_VLANF_PPPOE_INSERT\t(1u << 3)\n",
     "#define FMAN_PCD_VLANF_PPPOE_INSERT\t(1u << 3)\n"
     "/* F-268: bridge record (B7b): with POP or PUSH, no TTL/hop-limit\n"
     " * decrement and ENQUEUE mtu 0xffff (vendor fill_bridge_actions). */\n"
     "#define FMAN_PCD_VLANF_BRIDGE\t(1u << 4)\n", 1),
    # Refuse anything but a POP/PUSH L2 record without L3 edits.
    ("drivers/net/ethernet/freescale/fman/fman_pcd.c",
     "\t/* F-262: an MTU check needs the L2/TX terminal, the frag-info block\n",
     "\t/* F-268: a bridge record is a VLAN pop or push on the L2/TX\n"
     "\t * terminal and nothing else (no L3 edit, no MTU check). */\n"
     "\tif (vlan && (vlan->flags & FMAN_PCD_VLANF_BRIDGE) &&\n"
     "\t    (!(l2_dst && l2_src && eth_type) || (nat && nat->flags) ||\n"
     "\t     vlan->egress_mtu ||\n"
     "\t     !(vlan->flags & (FMAN_PCD_VLANF_POP | FMAN_PCD_VLANF_PUSH)) ||\n"
     "\t     (vlan->flags & (FMAN_PCD_VLANF_PPPOE_STRIP |\n"
     "\t\t\t     FMAN_PCD_VLANF_PPPOE_INSERT))))\n"
     "\t\treturn -EINVAL;\n"
     "\t/* F-262: an MTU check needs the L2/TX terminal, the frag-info block\n", 1),
    ("drivers/net/ethernet/freescale/fman/fman_pcd.c",
     "\t\t\tbool vlan_pop = vlan && (vlan->flags & FMAN_PCD_VLANF_POP);\n",
     "\t\t\tbool vlan_pop = vlan && (vlan->flags & FMAN_PCD_VLANF_POP);\n"
     "\t\t\tbool bridge = vlan && (vlan->flags & FMAN_PCD_VLANF_BRIDGE);\n", 1),
    ("drivers/net/ethernet/freescale/fman/fman_pcd.c",
     "\t\t\tif (eth_type == 0x0800) {\n"
     "\t\t\t\t/* UPDATE_TTL opcode + 4B zero DSCP param first. */\n",
     "\t\t\tif (bridge) {\n"
     "\t\t\t\t/* F-268: a bridge never touches TTL/hop limit. */\n"
     "\t\t\t} else if (eth_type == 0x0800) {\n"
     "\t\t\t\t/* UPDATE_TTL opcode + 4B zero DSCP param first. */\n", 1),
    ("drivers/net/ethernet/freescale/fman/fman_pcd.c",
     "\t\t\tcpu_to_be16(vlan && (vlan->flags & FMAN_PCD_VLANF_PPPOE_INSERT) ?\n"
     "\t\t\t\t    0xffff : 1500);\n",
     "\t\t\tcpu_to_be16(vlan && (vlan->flags &\n"
     "\t\t\t\t\t    (FMAN_PCD_VLANF_PPPOE_INSERT |\n"
     "\t\t\t\t\t     FMAN_PCD_VLANF_BRIDGE)) ?\n"
     "\t\t\t\t    0xffff : 1500);\t/* F-268: bridge */\n", 1),
]

srcs = {}
for path, _, _, _ in EDITS:
    if path not in srcs:
        try:
            with open(path) as f:
                srcs[path] = f.read()
        except FileNotFoundError:
            print(f"### F-268: FATAL: {path} not found")
            sys.exit(1)

if all(MARK in s for s in srcs.values()):
    print("### F-268: already applied")
    sys.exit(0)

for path, old, new, want in EDITS:
    n = srcs[path].count(old)
    if n != want:
        print(f"### F-268: FATAL: anchor in {path} found {n} times (expected {want})")
        sys.exit(1)
    srcs[path] = srcs[path].replace(old, new)

for path, s in srcs.items():
    with open(path, "w") as f:
        f.write(s)
print("### F-268: ehash emitter bridge VLAN pop/push records (no TTL, mtu 0xffff)")
