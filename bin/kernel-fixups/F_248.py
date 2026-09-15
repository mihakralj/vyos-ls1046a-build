"""F-248 (T-M6-2 B2, 2026-09-15): add cc_test_install_l2() -- the bridge
FDB L2 composite install_* variant probe3 mode 2 (F-247) needs.

Mirrors cc_test_install_v6pid() exactly (same heap-allocated spec
kzalloc/kfree, same fman_pcd_cc_static_install -> get_base ->
ensure_params_page -> set_cc_base -> kg_port_attach_cc_* -> goto
out_destroy_cc error path), just targeting the bridge L2 composite
(FMAN_PCD_CC_HW_F_MAC_DST, cc_pack_key_l2()/CC_KEY_SIZE_L2 -- patch
0205/0206) and fman_pcd_kg_port_attach_cc_l2() (patch 0207) instead of
the IPv6 dual-lane path.

`install_l2 <port> <dst_mac> <target-fqid-hex>` installs a DA-only leaf,
arms the port's KeyGen scheme for L2 extraction, and points FMBM_RCCB at
the tree -- the full real silicon-arming sequence, not just the MURAM
tree build.

Anchored on cc_test_install_v6pid()'s exact closing text (a real board
patch, 0185 -- stable, not itself fixup-added) and the write-dispatch
chain's "install_vlan "/"install " boundary. Must run after 0185 (for
the anchor) and after 0206/0207 (fman_pcd_cc_hw_spec.bridge_l2 and
fman_pcd_kg_port_attach_cc_l2() must already exist) -- and before F-247
(which reuses cc_test_install_l2()). Idempotent.
"""

import os
import sys

cc_test_c = "drivers/net/ethernet/freescale/fman/fman_pcd_cc_test.c"

if not os.path.exists(cc_test_c):
    print(f"### F-248: {cc_test_c} not found")
    sys.exit(0)

marker = "F-248(cc-test-install-l2)"

with open(cc_test_c) as f:
    src = f.read()

if marker in src:
    print("### F-248: already applied")
    sys.exit(0)

if "cc_test_install_v6pid" not in src:
    print("### F-248: FATAL: cc_test_install_v6pid not found -- patch 0185 must run first")
    sys.exit(1)

# ---------------------------------------------------------------------
# 1. Insert cc_test_install_l2() right after cc_test_install_v6pid()'s
#    closing brace (its exact out_destroy_cc tail is a stable anchor,
#    unique in the file).
# ---------------------------------------------------------------------
anchor1 = (
    '\tpr_info("fman_pcd cc_test: IPv6 dual-lane+PID (hybrid EKFC+GEC) port 0x%02x CC base 0x%x -> fqid 0x%x %pI6c:%u -> %pI6c:%u proto %u\\n",\n'
    "\t\tport_id, cc_base, fqid, src6, sport, dst6, dport, proto);\n"
    "\treturn 0;\n"
    "\n"
    "out_destroy_cc:\n"
    "\t(void)fman_pcd_kg_port_detach_cc(pcd, port_id);\n"
    "\tfman_pcd_cc_static_destroy(pcd, port_id);\n"
    "\treturn err;\n"
    "}\n"
)
if anchor1 not in src:
    print("### F-248: FATAL: cc_test_install_v6pid closing-tail anchor not found")
    sys.exit(1)
if src.count(anchor1) != 1:
    print(f"### F-248: FATAL: anchor1 not unique ({src.count(anchor1)})")
    sys.exit(1)

new_fn = (
    f"\n/* {marker}: install_l2 <port> <dst_mac> <target-fqid-hex>\n"
    " *\n"
    " * T-M6-2 B2 (plans/ASK2-BRIDGE-OFFLOAD-PLAN.md): install ONE CC leaf\n"
    " * matching only the bridge FDB L2 composite's destination-MAC field\n"
    " * (FMAN_PCD_CC_HW_F_MAC_DST, cc_pack_key_l2()/CC_KEY_SIZE_L2=15 --\n"
    " * patch 0205/0206), arm the port's KeyGen scheme for the L2 extraction\n"
    " * (fman_pcd_kg_port_attach_cc_l2(), EKFC 0xe4000000 -- patch 0207), and\n"
    " * point the port's FMBM_RCCB at the tree, exactly like every other\n"
    " * install_* variant above (fman_pcd_cc_static_get_base ->\n"
    " * fman_pcd_port_ensure_params_page -> fman_port_set_cc_base ->\n"
    " * kg_port_attach_cc_*). miss_fe_off is left 0 (miss stays on the\n"
    " * existing RSS/SW path) -- the coexistence proof's non-zero-miss case\n"
    " * is a separate, later install once the read-only oracle question this\n"
    " * command exists to answer (plan §8.1, probe3 mode 2 -- F-247) is\n"
    " * settled; this is deliberately the simplest possible arm.\n"
    " *\n"
    " * spec is heap-allocated (kzalloc/kfree), not stack: struct\n"
    " * fman_pcd_cc_hw_spec carries a 64-entry key array and does not belong\n"
    " * on a kernel stack frame, matching every other install_* variant in\n"
    " * this file.\n"
    " */\n"
    "static int cc_test_install_l2(struct fman_pcd *pcd, const char *args)\n"
    "{\n"
    "\tstruct fman_pcd_cc_hw_spec *spec = NULL;\n"
    "\tstruct fman_port *rxport;\n"
    "\tstruct fman *fm;\n"
    "\tchar mac_str[32];\n"
    "\tu8 dst_mac[ETH_ALEN];\n"
    "\tu32 fqid, cc_base;\n"
    "\tu8 port_id;\n"
    "\tint n, err;\n"
    "\n"
    '\tn = sscanf(args, "install_l2 %hhi %31s %x", &port_id, mac_str, &fqid);\n'
    "\tif (n != 3 || port_id >= ARRAY_SIZE(cc_test_vlan_hm))\n"
    "\t\treturn -EINVAL;\n"
    "\tif (!mac_pton(mac_str, dst_mac))\n"
    "\t\treturn -EINVAL;\n"
    "\n"
    "\tfm = fman_pcd_get_fman(pcd);\n"
    "\tif (!fm)\n"
    "\t\treturn -ENODEV;\n"
    "\n"
    "\tspec = kzalloc(sizeof(*spec), GFP_KERNEL);\n"
    "\tif (!spec)\n"
    "\t\treturn -ENOMEM;\n"
    "\tspec->bridge_l2 = true;\n"
    "\tspec->num_keys = 1;\n"
    "\tspec->miss_qband = 0;\n"
    "\tspec->miss_fqid = 0;\t/* miss stays on the existing RSS/SW path */\n"
    "\tspec->keys[0].present = FMAN_PCD_CC_HW_F_MAC_DST;\n"
    "\tether_addr_copy(spec->keys[0].dst_mac, dst_mac);\n"
    "\tspec->keys[0].target_qband = 0;\n"
    "\tspec->keys[0].target_fqid = fqid;\n"
    "\n"
    "\terr = fman_pcd_cc_static_install(pcd, port_id, spec);\n"
    "\tkfree(spec);\n"
    "\tif (err)\n"
    "\t\treturn err;\n"
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
src = src.replace(anchor1, anchor1 + new_fn, 1)

# ---------------------------------------------------------------------
# 2. Dispatch "install_l2 " right before the bare "install " check.
# ---------------------------------------------------------------------
anchor2 = '\t} else if (strncmp(kbuf, "install ", 8) == 0) {\n'
if anchor2 not in src:
    print("### F-248: FATAL: bare-install dispatch anchor not found")
    sys.exit(1)
if src.count(anchor2) != 1:
    print(f"### F-248: FATAL: anchor2 not unique ({src.count(anchor2)})")
    sys.exit(1)
new2 = (
    f'\t}} else if (strncmp(kbuf, "install_l2 ", 11) == 0) {{\n'
    "\t\tret = cc_test_install_l2(pcd, kbuf);\n"
    "\t\tif (ret == 0)\n"
    "\t\t\tret = count;\n"
    + anchor2
)
src = src.replace(anchor2, new2, 1)

with open(cc_test_c, "w") as f:
    f.write(src)
print("### fman_pcd_cc_test.c: F-248 cc_test_install_l2 (bridge L2) added")
