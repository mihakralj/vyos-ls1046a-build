"""F-287: keep debugfs and keyless flushes from stealing FE owners."""

import sys

PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
HEADER = "include/linux/fsl/fman_pcd.h"

DEBUG_CLEAR = """\
/* Caller holds fe_lock. Never drain live or quarantined non-debug owners. */
static int fman_pcd_ehash_debug_clear(struct fman_pcd *pcd)
{
\tstruct fman_pcd_ehash_table *t;
\tstruct fman_pcd_ehash_flow *flow;

\tlist_for_each_entry(t, &pcd->fe_ehash_tables, node) {
\t\tif (t->draining || t->poisoned ||
\t\t    !list_empty(&t->quarantined_flows) ||
\t\t    !list_empty(&t->quarantined_cu))
\t\t\treturn -EBUSY;
\t\tlist_for_each_entry(flow, &t->flows, node)
\t\t\tif (flow->owner != FMAN_PCD_FE_OWNER_DEBUGFS)
\t\t\t\treturn -EBUSY;
\t}
\treturn fman_pcd_ehash_flow_clear_all(pcd);
}

"""

EDITS = [
    (HEADER,
     "#define FMAN_PCD_FE_OWNER_ASK\t\t0x00U\t/* ask.ko, debugfs, legacy */\n",
     "#define FMAN_PCD_FE_OWNER_ASK\t\t0x00U\t/* ask.ko, legacy */\n"),
    (HEADER,
     "#define FMAN_PCD_FE_OWNER_WGFP\t\t0x03U\t/* dpaa_wgfp */\n",
     "#define FMAN_PCD_FE_OWNER_WGFP\t\t0x03U\t/* dpaa_wgfp */\n"
     "#define FMAN_PCD_FE_OWNER_DEBUGFS\t0x04U\t/* reserved for debugfs */\n"),
    (PCD,
     "/* F-230: debugfs fe_flow add path never performs NAT; T-M6-8d adds an\n",
     "/* F-287: shared owner check, also used by the debugfs flow handler. */\n"
     "static bool fman_pcd_ehash_foreign(struct fman_pcd_ehash_table *t,\n"
     "\t\t\t\t   const u8 *key, u8 key_size, u32 owner);\n\n"
     "/* F-230: debugfs fe_flow add path never performs NAT; T-M6-8d adds an\n"),
    (PCD,
     "\t\t\t\t      FMAN_PCD_FE_OWNER_ASK, 0);\n",
     "\t\t\t\t      FMAN_PCD_FE_OWNER_DEBUGFS, 0);\n"),
    (PCD,
     "static int fman_pcd_fe_flow_show(struct seq_file *s, void *unused)\n",
     DEBUG_CLEAR +
     "static int fman_pcd_fe_flow_show(struct seq_file *s, void *unused)\n"),
    (PCD,
     '\tif (!strncmp(buf, "clear", 5)) {\n'
     "\t\terr = fman_pcd_ehash_flow_clear_all(pcd);\n",
     '\tif (!strncmp(buf, "clear", 5)) {\n'
     "\t\terr = fman_pcd_ehash_debug_clear(pcd);\n"),
    (PCD,
     "\t\tderr = fman_pcd_ehash_del_key(dt, dkey, dks);\n",
     "\t\tif (fman_pcd_ehash_foreign(dt, dkey, dks,\n"
     "\t\t\t\t\t   FMAN_PCD_FE_OWNER_DEBUGFS)) {\n"
     "\t\t\tmutex_unlock(&pcd->fe_lock);\n"
     "\t\t\treturn -EBUSY;\n"
     "\t\t}\n"
     "\t\tderr = fman_pcd_ehash_del_key(dt, dkey, dks);\n"),
    (PCD,
     "\t/* F-248: the drop records point at the EXIT singleton instead. */\n"
     "\terr = fman_pcd_ehash_add_key_natwrap_dbgfs(t, key, key_size,\n",
     "\tif (fman_pcd_ehash_foreign(t, key, key_size,\n"
     "\t\t\t\t   FMAN_PCD_FE_OWNER_DEBUGFS)) {\n"
     "\t\tmutex_unlock(&pcd->fe_lock);\n"
     "\t\treturn -EEXIST;\n"
     "\t}\n\n"
     "\t/* F-248: the drop records point at the EXIT singleton instead. */\n"
     "\terr = fman_pcd_ehash_add_key_natwrap_dbgfs(t, key, key_size,\n"),
    (PCD,
     "\tif (!fm || !action || action->key_size == 0)\n"
     "\t\treturn -EINVAL;\n"
     "\tpcd = fman_get_pcd(fm);\n",
     "\tif (!fm || !action || action->key_size == 0)\n"
     "\t\treturn -EINVAL;\n"
     "\tif ((action->flags & FMAN_PCD_FE_OWNER_MASK) ==\n"
     "\t    FMAN_PCD_FE_OWNER_DEBUGFS)\n"
     "\t\treturn -EINVAL;\n"
     "\tpcd = fman_get_pcd(fm);\n"),
    (PCD,
     "\t/* No key => legacy clear-all (admin flush / disengage). */\n"
     "\tif (!key || key_size == 0) {\n"
     "\t\tint err;\n\n"
     "\t\t/* F-280: only the ASK owner may clear every owner's records. */\n"
     "\t\tif (owner & FMAN_PCD_FE_OWNER_MASK) {\n"
     "\t\t\tmutex_unlock(&pcd->fe_lock);\n"
     "\t\t\treturn -EINVAL;\n"
     "\t\t}\n"
     "\t\terr = fman_pcd_ehash_flow_clear_all(pcd);\n\n"
     "\t\tmutex_unlock(&pcd->fe_lock);\n"
     "\t\treturn err;\n"
     "\t}\n",
     "\t/* F-287: a keyless caller cannot identify the owners to retire. */\n"
     "\tif (!key || key_size == 0) {\n"
     "\t\tmutex_unlock(&pcd->fe_lock);\n"
     "\t\treturn -EOPNOTSUPP;\n"
     "\t}\n"),
    (PCD,
     '\telse if (!strncmp(buf, "clear", 5)) {\n'
     "\t\t/* F-269 clear error propagation */\n"
     "\t\terr = fman_pcd_ehash_drain(pcd);\n"
     "\t} else {\n",
     '\telse if (!strncmp(buf, "clear", 5)) {\n'
     "\t\t/* F-287: port/chain users are not serialized by fe_lock. */\n"
     "\t\terr = -EOPNOTSUPP;\n"
     "\t} else {\n"),
]


def main():
    files = {}
    for path, _old, _new in EDITS:
        if path not in files:
            try:
                with open(path) as stream:
                    files[path] = stream.read()
            except FileNotFoundError:
                print(f"### F-287: FATAL: {path} not found")
                return 1
    done = [new in files[path] for path, _old, new in EDITS]
    if all(done):
        print("### F-287: already applied")
        return 0
    if any(done):
        print("### F-287: FATAL: partially applied tree")
        return 1
    for i, (path, old, new) in enumerate(EDITS):
        count = files[path].count(old)
        if count != 1:
            print(f"### F-287: FATAL: anchor {i} in {path} found {count} times (expected 1)")
            return 1
        files[path] = files[path].replace(old, new, 1)
    for path, src in files.items():
        with open(path, "w") as stream:
            stream.write(src)
    print("### F-287: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
