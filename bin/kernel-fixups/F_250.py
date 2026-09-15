"""F-250 (T-M6-2 B2, 2026-09-15/16): add cc_test_install_l2fwd() -- hypothesis
test for the still-open §8.2b mystery (CC-hit -> CPU-bypassed hardware
forward not observed despite FMan's own QMI counters proving the AD's
enqueue action genuinely succeeds: fmqm_etfc tracks sent-frame count
almost exactly, fmqm_dtfc barely moves -- enqueued but never dequeued,
no hardware error flagged anywhere).

Root-cause candidate found by re-reading this project's own prior silicon
proofs closely: EVERY previously-proven cross-port hardware forward in
this codebase (the "24M+ frames silicon-forwarded" precedent cited for
the bare enqueue-AD path, and R3b/R4b's ~55k pps VLAN proofs) went
through NADEN + an HMTD chain (plans/ASK2-VLAN-REARCH.md: "ask20 silicon
captured 24M+ frames through exactly this RESULT_CF|NADEN + HMTD
encoding"). The "24M+ frames" precedent was re-traced to ethtool ntuple
RX-QUEUE steering (dpaa_ethtool.c, ETHTOOL_SRXCLSRLINS) -- a CPU-consumed
RX FQ target, software-polled, structurally different from a genuinely
cross-port no-confirm EGRESS TX FQ serviced by FMan's own hardware TX
block. The bare (no NADEN, no HMTD) enqueue-only AD form that
cc_test_install_l2() (F-248) uses for target_fqid may have NEVER actually
been silicon-proven for a genuine cross-port EGRESS forward -- only for
same-port RX steering and, separately, for KeyGen extraction correctness
(this session's own probe3 proofs, which never exercise the AD/enqueue
path at all).

cc_test_install_l2fwd() tests this directly: same bridge_l2 DA-match key
as install_l2, but chained via NADEN through a minimal, single-op
IPV4_FORWARD HMTD (semantically valid for real IPv4/UDP test frames --
this session's test frames all are -- and gives a bonus verification
signal: a successfully-forwarded frame's TTL will read one less than
sent, proving it genuinely walked the HMTD, not just some unrelated
path). This is explicitly a HYPOTHESIS TEST, not the final production
bridge design -- a real untagged L2 bridge forward should need no header
edit at all (plan §1's whole design point); if this test confirms HMTD
chaining is what makes dequeue work, that is itself an important new
finding requiring its own follow-up (either bridging genuinely needs a
trivial/no-op HMTD op that doesn't exist yet in fman_pcd_manip.c's
op-type table, or there is a narrower missing NIA/scheduling field this
test would help isolate without one).

Reuses cc_test_vlan_hm[] for teardown tracking (the existing "clear
<port>" command already destroys any handle found there, no new teardown
code needed).

Must run after F-248 (cc_test_install_l2, anchor + bridge_l2 dispatch
must already exist) and after patch 0207 (fman_pcd_kg_port_attach_cc_l2).
Idempotent.
"""

import os
import sys

cc_test_c = "drivers/net/ethernet/freescale/fman/fman_pcd_cc_test.c"

if not os.path.exists(cc_test_c):
    print(f"### F-250: {cc_test_c} not found")
    sys.exit(0)

marker = "F-250(cc-test-install-l2fwd)"

with open(cc_test_c) as f:
    src = f.read()

if marker in src:
    print("### F-250: already applied")
    sys.exit(0)

for needed in ("cc_test_install_l2", "cc_test_vlan_hm", "FMAN_PCD_HM_HW_OP_IPV4_FORWARD"):
    if needed not in src:
        print(f"### F-250: FATAL: {needed} not found -- F-248/0207 must run first")
        sys.exit(1)

# ---------------------------------------------------------------------
# 1. Insert cc_test_install_l2fwd() right after cc_test_install_l2()'s
#    closing brace (F-248's own anchor pattern, its exact closing tail).
# ---------------------------------------------------------------------
anchor1 = (
    '\tpr_info("fman_pcd cc_test: bridge L2 DA-match port 0x%02x CC base 0x%x -> fqid 0x%x DA %pM\\n",\n'
    "\t\tport_id, cc_base, fqid, dst_mac);\n"
    "\treturn 0;\n"
    "\n"
    "out_destroy_cc:\n"
    "\t(void)fman_pcd_kg_port_detach_cc(pcd, port_id);\n"
    "\tfman_pcd_cc_static_destroy(pcd, port_id);\n"
    "\treturn err;\n"
    "}\n"
)
if anchor1 not in src:
    print("### F-250: FATAL: cc_test_install_l2 closing-tail anchor not found")
    sys.exit(1)
if src.count(anchor1) != 1:
    print(f"### F-250: FATAL: anchor1 not unique ({src.count(anchor1)})")
    sys.exit(1)

new_fn = (
    f"\n/* {marker}: install_l2fwd <port> <dst_mac> <target-fqid-hex>\n"
    " *\n"
    " * Plan §8.2b hypothesis test (see this fixup's own module docstring\n"
    " * for the full reasoning): identical to install_l2 except the CC\n"
    " * leaf's AD chains via NADEN through a minimal single-op\n"
    " * IPV4_FORWARD HMTD instead of a bare fqid-only enqueue. Semantically\n"
    " * valid for real IPv4/UDP frames (dec_ttl+l4_csum, exactly like the\n"
    " * routed-forward op install_vlan's own \"pop\" path already uses) --\n"
    " * a successfully-forwarded frame's TTL reads one less than sent,\n"
    " * independently confirming it walked the HMTD. NOT the final bridge\n"
    " * design (plan §1: untagged bridging should need no header edit) --\n"
    " * purely to test whether bare (non-NADEN) cross-port enqueue is the\n"
    " * gap, or whether the problem lies elsewhere.\n"
    " */\n"
    "static int cc_test_install_l2fwd(struct fman_pcd *pcd, const char *args)\n"
    "{\n"
    "\tstruct fman_pcd_cc_hw_spec *spec = NULL;\n"
    "\tstruct fman_pcd_hm_hw_spec hm;\n"
    "\tstruct fman_port *rxport;\n"
    "\tstruct fman *fm;\n"
    "\tchar mac_str[32];\n"
    "\tu8 dst_mac[ETH_ALEN];\n"
    "\tu32 fqid, cc_base, hm_handle = 0;\n"
    "\tu8 port_id;\n"
    "\tint n, err;\n"
    "\n"
    '\tn = sscanf(args, "install_l2fwd %hhi %31s %x", &port_id, mac_str, &fqid);\n'
    "\tif (n != 3 || port_id >= ARRAY_SIZE(cc_test_vlan_hm))\n"
    "\t\treturn -EINVAL;\n"
    "\tif (!mac_pton(mac_str, dst_mac))\n"
    "\t\treturn -EINVAL;\n"
    "\n"
    "\tfm = fman_pcd_get_fman(pcd);\n"
    "\tif (!fm)\n"
    "\t\treturn -ENODEV;\n"
    "\tif (cc_test_vlan_hm[port_id])\n"
    "\t\treturn -EBUSY;\n"
    "\n"
    "\tmemset(&hm, 0, sizeof(hm));\n"
    "\thm.num_ops = 1;\n"
    "\thm.ops[0].type = FMAN_PCD_HM_HW_OP_IPV4_FORWARD;\n"
    "\thm.ops[0].ipv4_forward.dec_ttl = 1;\n"
    "\thm.ops[0].ipv4_forward.l4_csum = 1;\n"
    "\terr = fman_pcd_hm_install(pcd, port_id, &hm, &hm_handle);\n"
    "\tif (err)\n"
    "\t\treturn err;\n"
    "\n"
    "\tspec = kzalloc(sizeof(*spec), GFP_KERNEL);\n"
    "\tif (!spec) {\n"
    "\t\terr = -ENOMEM;\n"
    "\t\tgoto out_put_hm;\n"
    "\t}\n"
    "\tspec->bridge_l2 = true;\n"
    "\tspec->num_keys = 1;\n"
    "\tspec->miss_qband = 0;\n"
    "\tspec->miss_fqid = 0;\n"
    "\tspec->miss_fe_off = fman_pcd_fe_root_get_offset(fm);\n"
    "\tspec->keys[0].present = FMAN_PCD_CC_HW_F_MAC_DST;\n"
    "\tether_addr_copy(spec->keys[0].dst_mac, dst_mac);\n"
    "\tspec->keys[0].target_qband = 0;\n"
    "\tspec->keys[0].target_fqid = fqid;\n"
    "\tspec->keys[0].hm_handle = hm_handle;\n"
    "\n"
    "\terr = fman_pcd_cc_static_install(pcd, port_id, spec);\n"
    "\tkfree(spec);\n"
    "\tspec = NULL;\n"
    "\tif (err)\n"
    "\t\tgoto out_put_hm;\n"
    "\n"
    "\terr = fman_pcd_cc_static_get_base(pcd, port_id, &cc_base);\n"
    "\tif (err)\n"
    "\t\tgoto out_destroy_cc;\n"
    "\trxport = fman_port_lookup_rx(fm, port_id);\n"
    "\tif (!rxport) {\n"
    "\t\terr = -ENODEV;\n"
    "\t\tgoto out_destroy_cc;\n"
    "\t}\n"
    "\terr = fman_pcd_port_ensure_params_page(pcd, rxport);\n"
    "\tif (err)\n"
    "\t\tgoto out_destroy_cc;\n"
    "\terr = fman_port_set_cc_base(rxport, cc_base);\n"
    "\tif (err)\n"
    "\t\tgoto out_destroy_cc;\n"
    "\terr = fman_pcd_kg_port_attach_cc_l2(pcd, port_id, cc_base);\n"
    "\tif (err) {\n"
    "\t\t(void)fman_port_set_cc_base(rxport, 0);\n"
    "\t\tgoto out_destroy_cc;\n"
    "\t}\n"
    "\n"
    "\tcc_test_vlan_hm[port_id] = hm_handle;\n"
    '\tpr_info("fman_pcd cc_test: bridge L2+HMTD DA-match port 0x%02x CC base 0x%x HMTD 0x%x -> fqid 0x%x DA %pM\\n",\n'
    "\t\tport_id, cc_base, hm_handle, fqid, dst_mac);\n"
    "\treturn 0;\n"
    "\n"
    "out_destroy_cc:\n"
    "\t(void)fman_pcd_kg_port_detach_cc(pcd, port_id);\n"
    "\tfman_pcd_cc_static_destroy(pcd, port_id);\n"
    "out_put_hm:\n"
    "\t(void)fman_pcd_hm_destroy(pcd, port_id, hm_handle);\n"
    "\treturn err;\n"
    "}\n"
)
src = src.replace(anchor1, anchor1 + new_fn, 1)

# ---------------------------------------------------------------------
# 2. Dispatch "install_l2fwd " right before the "install_l2 " check
#    (longer-prefix verb must be checked first, same rule as every other
#    verb pair in this dispatch chain).
# ---------------------------------------------------------------------
anchor2 = '\t} else if (strncmp(kbuf, "install_l2 ", 11) == 0) {\n'
if anchor2 not in src:
    print("### F-250: FATAL: install_l2 dispatch anchor not found")
    sys.exit(1)
if src.count(anchor2) != 1:
    print(f"### F-250: FATAL: anchor2 not unique ({src.count(anchor2)})")
    sys.exit(1)
new2 = (
    '\t} else if (strncmp(kbuf, "install_l2fwd ", 14) == 0) {\n'
    "\t\tret = cc_test_install_l2fwd(pcd, kbuf);\n"
    "\t\tif (ret == 0)\n"
    "\t\t\tret = count;\n"
    + anchor2
)
src = src.replace(anchor2, new2, 1)

with open(cc_test_c, "w") as f:
    f.write(src)
print("### fman_pcd_cc_test.c: F-250 cc_test_install_l2fwd (bridge L2 + HMTD hypothesis test) added")
