"""F-241 (T-M6-8 VLAN-v6 dig, 2026-09-03): atomic RICP-widen capture --
`probe3`. Fixes the exposure-window safety problem F-240's own follow-up
test exposed: a multi-command interactive sequence over the slow
serial-console relay (widen, arm, several probe2 reads, restore) stayed
open long enough (~a minute) to measurably corrupt real concurrent eth1
traffic (~1500 RX errors, confirmed causally -- see
decomp/fe-action-interpreter.md "2026-09-03 (same day, second
follow-up)"). This fixup moves the whole widen -> arm -> wait -> snapshot
-> restore -> detach sequence into ONE kernel-side debugfs write, so the
RICP-widened window is bounded by a single bounded msleep(), not by
however many seconds a human-driven console round-trip takes.

New cc_test verb:
  probe3 <mode:0|1> <port_hex> <proto> <src-ip6> <dst-ip6> <sport> <dport> <fqid-hex>
    mode 0 -> plain all-GEC (cc_test_install_v6, EKFC=0)
    mode 1 -> hybrid EKFC+GEC (cc_test_install_v6pid, EKFC=PORT_ID)
  Reuses the existing install_v6/install_v6pid parsers verbatim (builds a
  reprefixed argument string and calls them directly -- no duplicated
  setup logic) so this stays a thin wrapper, not a second implementation.
  Sequence: fman_port_widen_ricp() -> clear fman_pcd_probe2_valid so a
  stale pre-existing capture can't be mistaken for a fresh one -> arm the
  requested scheme -> msleep(300) for real background traffic to be
  captured by F-239's existing eth1 hook -> snapshot fman_pcd_probe2_buf
  into a separate cc_test_probe3_buf (frozen, immune to later traffic
  overwriting the live probe2 buffer while the result is read afterward)
  -> detach the scheme (same teardown order as the `clear` verb) ->
  fman_port_restore_ricp(). All under ~300-500ms plus register I/O, not
  multiple slow interactive round-trips.

New read node: /sys/kernel/debug/fman_pcd/<N>/probe3 -- dumps the frozen
snapshot, same hex-dump format as probe2 (fman_pcd_probe2_show), so it's
immune to timing races on the READ side too (unlike reading the live
probe2 node, which a later unrelated frame can overwrite before the
human gets to `cat` it).

Must run after F-240 (fman_port_widen_ricp/fman_port_restore_ricp,
cc_test_saved_ricp/cc_test_ricp_widened anchors) and after the 0188 patch
(cc_test_install_v6/cc_test_install_v6pid must already exist) and after
F-239 (fman_pcd_probe2_buf/fman_pcd_probe2_valid must already exist in
fman_pcd.c). Idempotent.
"""

import os
import sys

cc_test_c = "drivers/net/ethernet/freescale/fman/fman_pcd_cc_test.c"
fman_pcd_c = "drivers/net/ethernet/freescale/fman/fman_pcd.c"

for p in (cc_test_c, fman_pcd_c):
    if not os.path.exists(p):
        print(f"### F-241: {p} not found")
        sys.exit(0)

marker = "F-241(probe3-atomic)"

with open(fman_pcd_c) as f:
    pcd_src = f.read()
if "FMAN_PCD_PROBE2_LEN" not in pcd_src or "fman_pcd_probe2_buf" not in pcd_src:
    print("### F-241: FATAL: fman_pcd_probe2_buf/FMAN_PCD_PROBE2_LEN not found -- F-239 must run first")
    sys.exit(1)

with open(cc_test_c) as f:
    src = f.read()

if marker in src:
    print("### F-241: already applied")
    sys.exit(0)

for needed in ("cc_test_saved_ricp", "cc_test_ricp_widened",
               "fman_port_widen_ricp", "fman_port_restore_ricp"):
    if needed not in src:
        print(f"### F-241: FATAL: {needed} not found -- F-240 must run first")
        sys.exit(1)

# ---------------------------------------------------------------------
# 1. Add extern probe2 decls + probe3 snapshot buffer, right after
#    F-240's cc_test_saved_ricp/cc_test_ricp_widened arrays.
# ---------------------------------------------------------------------
anchor1 = (
    "static u32 cc_test_saved_ricp[32];\n"
    "static bool cc_test_ricp_widened[32];\n"
)
if anchor1 not in src:
    print("### F-241: FATAL: cc_test_saved_ricp/cc_test_ricp_widened block not found")
    sys.exit(1)
if src.count(anchor1) != 1:
    print(f"### F-241: FATAL: anchor1 not unique ({src.count(anchor1)})")
    sys.exit(1)

new1 = anchor1 + (
    f"\n/* {marker}: the F-239 live capture buffer this atomic verb\n"
    " * snapshots from -- same module (fsl_dpaa_fman), plain extern. */\n"
    "#define CC_TEST_PROBE3_LEN 176\n"
    "extern u8 fman_pcd_probe2_buf[CC_TEST_PROBE3_LEN];\n"
    "extern bool fman_pcd_probe2_valid;\n"
    "static u8 cc_test_probe3_buf[CC_TEST_PROBE3_LEN];\n"
    "static bool cc_test_probe3_valid;\n"
)
src = src.replace(anchor1, new1, 1)

# ---------------------------------------------------------------------
# 2. Add cc_test_probe3() just before cc_test_write().
# ---------------------------------------------------------------------
anchor2 = "static ssize_t cc_test_write(struct file *file, const char __user *buf,\n"
if anchor2 not in src:
    print("### F-241: FATAL: cc_test_write anchor not found")
    sys.exit(1)
if src.count(anchor2) != 1:
    print(f"### F-241: FATAL: anchor2 not unique ({src.count(anchor2)})")
    sys.exit(1)

probe3_fn = (
    f"/* {marker}: atomic widen -> arm -> wait -> snapshot -> restore ->\n"
    " * detach, all inside one kernel call. See file-header comment for\n"
    " * the exposure-window rationale. mode 0 = plain all-GEC\n"
    " * (cc_test_install_v6), mode 1 = hybrid EKFC+GEC\n"
    " * (cc_test_install_v6pid). Returns 0 on success (regardless of\n"
    " * whether a frame was actually captured in the wait window -- check\n"
    " * cc_test_probe3_valid/the probe3 read node for that), negative\n"
    " * errno on setup failure. */\n"
    "static int cc_test_probe3(struct fman_pcd *pcd, const char *kbuf)\n"
    "{\n"
    "\tchar tail[160];\n"
    "\tchar reprefixed[192];\n"
    "\tstruct fman_port *rxport;\n"
    "\tu32 saved_ricp;\n"
    "\tint mode, n, err, werr;\n"
    "\tu8 port_id;\n"
    "\n"
    "\tn = sscanf(kbuf, \"probe3 %d %159[^\\n]\", &mode, tail);\n"
    "\tif (n != 2 || (mode != 0 && mode != 1))\n"
    "\t\treturn -EINVAL;\n"
    "\tif (sscanf(tail, \"%hhi\", &port_id) != 1 ||\n"
    "\t    port_id >= ARRAY_SIZE(cc_test_saved_ricp))\n"
    "\t\treturn -EINVAL;\n"
    "\n"
    "\trxport = fman_port_lookup_rx(fman_pcd_get_fman(pcd), port_id);\n"
    "\tif (!rxport)\n"
    "\t\treturn -ENODEV;\n"
    "\n"
    "\tif (cc_test_ricp_widened[port_id])\n"
    "\t\treturn -EBUSY;\n"
    "\n"
    "\tif (mode == 1)\n"
    "\t\tn = snprintf(reprefixed, sizeof(reprefixed), \"install_v6pid %s\", tail);\n"
    "\telse\n"
    "\t\tn = snprintf(reprefixed, sizeof(reprefixed), \"install_v6 %s\", tail);\n"
    "\tif (n >= sizeof(reprefixed))\n"
    "\t\treturn -EINVAL;\n"
    "\n"
    "\twerr = fman_port_widen_ricp(rxport, &saved_ricp);\n"
    "\tif (werr)\n"
    "\t\treturn werr;\n"
    "\tcc_test_saved_ricp[port_id] = saved_ricp;\n"
    "\tcc_test_ricp_widened[port_id] = true;\n"
    "\n"
    "\tfman_pcd_probe2_valid = false;\n"
    "\n"
    "\terr = (mode == 1) ? cc_test_install_v6pid(pcd, reprefixed)\n"
    "\t\t\t   : cc_test_install_v6(pcd, reprefixed);\n"
    "\tif (err) {\n"
    "\t\t(void)fman_port_restore_ricp(rxport, saved_ricp);\n"
    "\t\tcc_test_ricp_widened[port_id] = false;\n"
    "\t\treturn err;\n"
    "\t}\n"
    "\n"
    "\tmsleep(300);\n"
    "\n"
    "\tif (fman_pcd_probe2_valid) {\n"
    "\t\tmemcpy(cc_test_probe3_buf, fman_pcd_probe2_buf, CC_TEST_PROBE3_LEN);\n"
    "\t\tcc_test_probe3_valid = true;\n"
    "\t} else {\n"
    "\t\tcc_test_probe3_valid = false;\n"
    "\t}\n"
    "\n"
    "\t/* Same teardown order as the `clear` verb. */\n"
    "\t(void)fman_pcd_kg_port_detach_cc(pcd, port_id);\n"
    "\t(void)fman_port_set_cc_base(rxport, 0);\n"
    "\tusleep_range(5000, 6000);\n"
    "\tfman_pcd_cc_static_destroy(pcd, port_id);\n"
    "\n"
    "\t(void)fman_port_restore_ricp(rxport, saved_ricp);\n"
    "\tcc_test_ricp_widened[port_id] = false;\n"
    "\n"
    "\tpr_info(\"fman_pcd cc_test: probe3 mode=%d port 0x%02x done, %s\\n\",\n"
    "\t\tmode, port_id, cc_test_probe3_valid ? \"captured\" : \"no frame in window\");\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
)
src = src.replace(anchor2, probe3_fn + anchor2, 1)

# ---------------------------------------------------------------------
# 3. Dispatch "probe3 " in cc_test_write, right before the final else.
# ---------------------------------------------------------------------
anchor3 = (
    "\t} else {\n"
    "\t\tret = -EINVAL;\n"
    "\t}\n"
    "\n"
    "\tkfree(kbuf);\n"
    "\treturn ret;\n"
    "}\n"
)
if anchor3 not in src:
    print("### F-241: FATAL: cc_test_write final-else anchor not found")
    sys.exit(1)
if src.count(anchor3) != 1:
    print(f"### F-241: FATAL: anchor3 not unique ({src.count(anchor3)})")
    sys.exit(1)
new3 = (
    "\t} else if (strncmp(kbuf, \"probe3 \", 7) == 0) {\n"
    f"\t\t/* {marker}: atomic widen/arm/wait/snapshot/restore/detach. */\n"
    "\t\tret = cc_test_probe3(pcd, kbuf);\n"
    "\t\tif (ret == 0)\n"
    "\t\t\tret = count;\n"
    "\t} else {\n"
    "\t\tret = -EINVAL;\n"
    "\t}\n"
    "\n"
    "\tkfree(kbuf);\n"
    "\treturn ret;\n"
    "}\n"
)
src = src.replace(anchor3, new3, 1)

# ---------------------------------------------------------------------
# 4. probe3 read node: show/open/fops + debugfs_create_file, mirroring
#    fman_pcd_probe2_show's format.
# ---------------------------------------------------------------------
anchor4 = (
    "static const struct file_operations fman_pcd_cc_test_fops = {\n"
)
if anchor4 not in src:
    print("### F-241: FATAL: fman_pcd_cc_test_fops anchor not found")
    sys.exit(1)
if src.count(anchor4) != 1:
    print(f"### F-241: FATAL: anchor4 not unique ({src.count(anchor4)})")
    sys.exit(1)
probe3_show = (
    f"/* {marker}: frozen probe3 snapshot, same format as fman_pcd_probe2_show\n"
    " * (fman_pcd.c) -- immune to later traffic overwriting the live probe2\n"
    " * buffer between the atomic capture and the human reading this node. */\n"
    "static int cc_test_probe3_show(struct seq_file *s, void *unused)\n"
    "{\n"
    "\tint i;\n"
    "\n"
    "\tif (!cc_test_probe3_valid) {\n"
    "\t\tseq_puts(s, \"idle (no probe3 capture yet, or none in the last capture window)\\n\");\n"
    "\t\treturn 0;\n"
    "\t}\n"
    "\tseq_puts(s, \"parse-result-base window, offsets relative to +0 (frozen probe3 snapshot):\\n\");\n"
    "\tfor (i = 0; i < CC_TEST_PROBE3_LEN; i += 16) {\n"
    "\t\tint j, n = min(16, CC_TEST_PROBE3_LEN - i);\n"
    "\n"
    "\t\tseq_printf(s, \"%+04d:\", i);\n"
    "\t\tfor (j = 0; j < n; j++)\n"
    "\t\t\tseq_printf(s, \" %02x\", cc_test_probe3_buf[i + j]);\n"
    "\t\tseq_puts(s, \"\\n\");\n"
    "\t}\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "static int cc_test_probe3_open(struct inode *inode, struct file *file)\n"
    "{\n"
    "\treturn single_open(file, cc_test_probe3_show, inode->i_private);\n"
    "}\n"
    "\n"
    "static const struct file_operations cc_test_probe3_fops = {\n"
    "\t.owner\t\t= THIS_MODULE,\n"
    "\t.open\t\t= cc_test_probe3_open,\n"
    "\t.read\t\t= seq_read,\n"
    "\t.llseek\t\t= seq_lseek,\n"
    "\t.release\t= single_release,\n"
    "};\n"
    "\n"
)
src = src.replace(anchor4, probe3_show + anchor4, 1)

anchor5 = (
    "\tdebugfs_create_file(\"cc_test\", 0600, parent, pcd,\n"
    "\t\t\t    &fman_pcd_cc_test_fops);\n"
)
if anchor5 not in src:
    print("### F-241: FATAL: cc_test debugfs_create_file anchor not found")
    sys.exit(1)
if src.count(anchor5) != 1:
    print(f"### F-241: FATAL: anchor5 not unique ({src.count(anchor5)})")
    sys.exit(1)
new5 = anchor5 + (
    f"\t/* {marker}: frozen atomic-capture read node. */\n"
    "\tdebugfs_create_file(\"probe3\", 0444, parent, pcd,\n"
    "\t\t\t    &cc_test_probe3_fops);\n"
)
src = src.replace(anchor5, new5, 1)

with open(cc_test_c, "w") as f:
    f.write(src)
print("### fman_pcd_cc_test.c: F-241 atomic probe3 (widen/arm/wait/snapshot/restore/detach) added")
