"""F-286: identify FE records by a boot-unique software owner token."""

import sys

PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
HEADER = "include/linux/fsl/fman_pcd.h"

OLD_SIG = (
    "\t\t\t\t  u16 eth_type,\n"
    "\t\t\t\t  const struct fman_pcd_nat_params *nat,\n"
    "\t\t\t\t  const struct fman_pcd_vlan_params *vlan)"
)
NEW_SIG = (
    "\t\t\t\t  u16 eth_type,\n"
    "\t\t\t\t  const struct fman_pcd_nat_params *nat,\n"
    "\t\t\t\t  const struct fman_pcd_vlan_params *vlan,\n"
    "\t\t\t\t  u8 owner, u64 owner_token)"
)

OLD_TAG = (
    "\t\t/* F-280: tag the key's record (kzalloc'd = OWNER_ASK). Set it\n"
    "\t\t * even on error: a timed-out insert may leave it linked. */\n"
    "\t\t{\n"
    "\t\t\tstruct fman_pcd_ehash_flow *of =\n"
    "\t\t\t\tfman_pcd_ehash_lookup(t, action->key,\n"
    "\t\t\t\t\t\t      action->key_size);\n"
    "\n"
    "\t\t\tif (of)\n"
    "\t\t\t\tof->owner = action->flags &\n"
    "\t\t\t\t\t    FMAN_PCD_FE_OWNER_MASK;\n"
    "\t\t}\n"
)

TOKEN_ALLOC = """\
/* F-286: tokens never repeat during the lifetime of the built-in FMan. */
static atomic64_t fman_pcd_fe_token_seq = ATOMIC64_INIT(0);

u64 fman_pcd_fe_alloc_token(void)
{
\ts64 old = atomic64_read(&fman_pcd_fe_token_seq);

\twhile (old < S64_MAX) {
\t\ts64 observed = atomic64_cmpxchg(&fman_pcd_fe_token_seq,
\t\t\t\t\t\t old, old + 1);

\t\tif (observed == old)
\t\t\treturn (u64)(old + 1);
\t\told = observed;
\t}
\treturn 0;
}
EXPORT_SYMBOL_GPL(fman_pcd_fe_alloc_token);

"""

CHECK_IDENTITY = """\
\t/* A poisoned generation must be settled before a different live
\t * owner can be interpreted as proof that this owner is gone. */
\tif (check_token) {
\t\tstruct fman_pcd_ehash_flow *f;
\t\tint err;

\t\tif (t->draining) {
\t\t\tmutex_unlock(&pcd->fe_lock);
\t\t\treturn -ESHUTDOWN;
\t\t}
\t\terr = fman_pcd_ehash_recover(t);
\t\tif (err) {
\t\t\tmutex_unlock(&pcd->fe_lock);
\t\t\treturn err;
\t\t}
\t\tf = fman_pcd_ehash_lookup(t, key, key_size);
\t\tif (f && (f->owner != (owner & FMAN_PCD_FE_OWNER_MASK) ||
\t\t\t  f->owner_token != owner_token)) {
\t\t\tmutex_unlock(&pcd->fe_lock);
\t\t\treturn -ESTALE;
\t\t}
\t} else {
\t\tstruct fman_pcd_ehash_flow *f =
\t\t\tfman_pcd_ehash_lookup(t, key, key_size);

\t\tif (f && f->owner == (owner & FMAN_PCD_FE_OWNER_MASK) &&
\t\t    f->owner_token) {
\t\t\tmutex_unlock(&pcd->fe_lock);
\t\t\treturn -EBUSY;
\t\t}
\t}
"""

ADD_CHECK = """\
static int fman_pcd_ehash_token_add_check(struct fman_pcd_ehash_table *t,
\t\t\t\t\t  const u8 *key, u8 key_size,
\t\t\t\t\t  u32 owner, u64 owner_token)
{
\tstruct fman_pcd_ehash_flow *f = fman_pcd_ehash_lookup(t, key, key_size);

\tif (!f)
\t\treturn 0;
\tif (f->owner != (owner & FMAN_PCD_FE_OWNER_MASK))
\t\treturn -EEXIST;
\tif (f->owner_token && !owner_token)
\t\treturn -EEXIST;
\tif (f->owner_token && owner_token < f->owner_token)
\t\treturn -ESTALE;
\treturn 0;
}

"""

OLD_ADD_CHECK = """\
\t/* F-280: another owner's key is refused, never evicted. */
\tif (fman_pcd_ehash_foreign(t, action->key, action->key_size,
\t\t\t\t   action->flags)) {
\t\tmutex_unlock(&pcd->fe_lock);
\t\treturn -EEXIST;
\t}
"""

NEW_ADD_CHECK = """\
\t{
\t\tint err = fman_pcd_ehash_token_add_check(t, action->key,
\t\t\t\t\t\t     action->key_size,
\t\t\t\t\t\t     action->flags,
\t\t\t\t\t\t     action->owner_token);

\t\tif (err) {
\t\t\tmutex_unlock(&pcd->fe_lock);
\t\t\treturn err;
\t\t}
\t}
"""

WRAPPERS = """\
int fman_pcd_fe_flow_del_owner(struct fman *fm, u8 hw_port_id,
\t\t\t       const u8 *key, u8 key_size, u32 owner)
{
\treturn fman_pcd_fe_flow_del_checked(fm, hw_port_id, key, key_size,
\t\t\t\t\t    owner, 0, false);
}
EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_del_owner);

int fman_pcd_fe_flow_del_identified(struct fman *fm, u8 hw_port_id,
\t\t\t\t    const u8 *key, u8 key_size, u32 owner,
\t\t\t\t    u64 owner_token)
{
\tif (!key || !key_size || !owner_token)
\t\treturn -EINVAL;
\treturn fman_pcd_fe_flow_del_checked(fm, hw_port_id, key, key_size,
\t\t\t\t\t    owner, owner_token, true);
}
EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_del_identified);
"""

STATS_WRAPPERS = """\
int fman_pcd_fe_flow_get_stats(struct fman *fm, u8 hw_port_id,
\t\t\t       const u8 *key, u8 key_size,
\t\t\t       u64 *packets, u64 *bytes, u32 *timestamp)
{
\treturn fman_pcd_fe_flow_get_stats_checked(fm, hw_port_id, key, key_size,
\t\t\t\t\t\t packets, bytes, timestamp,
\t\t\t\t\t\t 0, 0, false);
}
EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_get_stats);

int fman_pcd_fe_flow_get_stats_identified(struct fman *fm, u8 hw_port_id,
\t\t\t\t\t  const u8 *key, u8 key_size,
\t\t\t\t\t  u32 owner, u64 owner_token,
\t\t\t\t\t  u64 *packets, u64 *bytes,
\t\t\t\t\t  u32 *timestamp)
{
\tif (!owner_token)
\t\treturn -EINVAL;
\treturn fman_pcd_fe_flow_get_stats_checked(fm, hw_port_id, key, key_size,
\t\t\t\t\t\t packets, bytes, timestamp,
\t\t\t\t\t\t owner, owner_token, true);
}
EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_get_stats_identified);
"""

EDITS = (
    (HEADER, "\tu32  flags;\t\t\t/* F-280: low byte = FMAN_PCD_FE_OWNER_* */\n",
     "\tu32  flags;\t\t\t/* F-280: low byte = FMAN_PCD_FE_OWNER_* */\n"
     "\tu64  owner_token;\t\t/* F-286: 0 for legacy callers */\n", 1),
    (HEADER,
     "int fman_pcd_fe_flow_del_owner(struct fman *fm, u8 hw_port_id,\n"
     "\t\t\t       const u8 *key, u8 key_size, u32 owner);\n",
     "int fman_pcd_fe_flow_del_owner(struct fman *fm, u8 hw_port_id,\n"
     "\t\t\t       const u8 *key, u8 key_size, u32 owner);\n"
     "u64 fman_pcd_fe_alloc_token(void);\n"
     "int fman_pcd_fe_flow_del_identified(struct fman *fm, u8 hw_port_id,\n"
     "\t\t\t\t    const u8 *key, u8 key_size, u32 owner,\n"
     "\t\t\t\t    u64 owner_token);\n", 1),
    (HEADER,
     "int fman_pcd_fe_flow_get_stats(struct fman *fm, u8 hw_port_id,\n"
     "\t\t\t       const u8 *key, u8 key_size,\n"
     "\t\t\t       u64 *packets, u64 *bytes, u32 *timestamp);\n",
     "int fman_pcd_fe_flow_get_stats(struct fman *fm, u8 hw_port_id,\n"
     "\t\t\t       const u8 *key, u8 key_size,\n"
     "\t\t\t       u64 *packets, u64 *bytes, u32 *timestamp);\n"
     "int fman_pcd_fe_flow_get_stats_identified(struct fman *fm, u8 hw_port_id,\n"
     "\t\t\t\t\t  const u8 *key, u8 key_size,\n"
     "\t\t\t\t\t  u32 owner, u64 owner_token,\n"
     "\t\t\t\t\t  u64 *packets, u64 *bytes,\n"
     "\t\t\t\t\t  u32 *timestamp);\n", 1),
    (PCD, "#include <linux/kernel.h>\n",
     "#include <linux/kernel.h>\n#include <linux/atomic.h>\n", 1),
    (PCD, "\tu8 owner;\t\t\t/* F-280: FMAN_PCD_FE_OWNER_* */\n",
     "\tu8 owner;\t\t\t/* F-280: FMAN_PCD_FE_OWNER_* */\n"
     "\tu64 owner_token;\t\t/* F-286: software owner identity */\n", 1),
    (PCD, OLD_SIG, NEW_SIG, 2),
    (PCD, "\t\t\t\t      l2_dst, l2_src, eth_type, NULL, vlan);\n",
     "\t\t\t\t      l2_dst, l2_src, eth_type, NULL, vlan,\n"
     "\t\t\t\t      FMAN_PCD_FE_OWNER_ASK, 0);\n", 1),
    (PCD, "\tflow = kzalloc(sizeof(*flow), GFP_KERNEL);\n"
          "\tif (!flow)\n\t\treturn -ENOMEM;\n",
     "\tflow = kzalloc(sizeof(*flow), GFP_KERNEL);\n"
     "\tif (!flow)\n\t\treturn -ENOMEM;\n"
     "\tflow->owner = owner;\n"
     "\tflow->owner_token = owner_token;\n", 1),
    (PCD, "/* F-280: the live flow for @key in @t, or NULL. Caller holds fe_lock. */\n",
     TOKEN_ALLOC + "/* F-280: the live flow for @key in @t, or NULL. Caller holds fe_lock. */\n", 1),
    (PCD, "int fman_pcd_fe_flow_add(struct fman *fm, u8 hw_port_id,\n",
     ADD_CHECK + "int fman_pcd_fe_flow_add(struct fman *fm, u8 hw_port_id,\n", 1),
    (PCD, OLD_ADD_CHECK, NEW_ADD_CHECK, 1),
    (PCD, "\t\t\t\t\t\t  action->egress_mtu) ? &_vlan : NULL);\n",
     "\t\t\t\t\t\t  action->egress_mtu) ? &_vlan : NULL,\n"
     "\t\t\t\t\t\t action->flags & FMAN_PCD_FE_OWNER_MASK,\n"
     "\t\t\t\t\t\t action->owner_token);\n", 1),
    (PCD, OLD_TAG, "", 1),
    (PCD,
     "int fman_pcd_fe_flow_del_owner(struct fman *fm, u8 hw_port_id,\n"
     "\t\t\t       const u8 *key, u8 key_size, u32 owner)\n",
     "static int fman_pcd_fe_flow_del_checked(struct fman *fm, u8 hw_port_id,\n"
     "\t\t\t\t\tconst u8 *key, u8 key_size, u32 owner,\n"
     "\t\t\t\t\tu64 owner_token, bool check_token)\n", 1),
    (PCD, "\t/* F-280: another owner's record is not ours to delete; for this\n",
     CHECK_IDENTITY + "\t/* F-280: another owner's record is not ours to delete; for this\n", 1),
    (PCD, "EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_del_owner);\n", WRAPPERS, 1),
    (PCD,
     "int fman_pcd_fe_flow_get_stats(struct fman *fm, u8 hw_port_id,\n"
     "\t\t\t       const u8 *key, u8 key_size,\n"
     "\t\t\t       u64 *packets, u64 *bytes, u32 *timestamp)\n",
     "static int fman_pcd_fe_flow_get_stats_checked(struct fman *fm, u8 hw_port_id,\n"
     "\t\t\t\t\t      const u8 *key, u8 key_size,\n"
     "\t\t\t\t\t      u64 *packets, u64 *bytes,\n"
     "\t\t\t\t\t      u32 *timestamp, u32 owner,\n"
     "\t\t\t\t\t      u64 owner_token, bool check_token)\n", 1),
    (PCD, "\tif (hit) {\n\t\tconst u8 *r = hit->record;\n",
     "\tif (hit && check_token &&\n"
     "\t    (hit->owner != (owner & FMAN_PCD_FE_OWNER_MASK) ||\n"
     "\t     hit->owner_token != owner_token)) {\n"
     "\t\tmutex_unlock(&pcd->fe_lock);\n"
     "\t\treturn -ESTALE;\n"
     "\t}\n"
     "\tif (hit) {\n\t\tconst u8 *r = hit->record;\n", 1),
    (PCD, "EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_get_stats);\n", STATS_WRAPPERS, 1),
)


def main():
    sources = {}
    for path, _old, _new, _count in EDITS:
        if path not in sources:
            try:
                with open(path) as stream:
                    sources[path] = stream.read()
            except FileNotFoundError:
                print(f"### F-286: FATAL: {path} not found")
                return 1
    if "F-286" in sources[HEADER] or "F-286" in sources[PCD]:
        for path, _old, new, count in EDITS:
            if new and sources[path].count(new) != count:
                print(f"### F-286: FATAL: incomplete application in {path}")
                return 1
        if OLD_TAG in sources[PCD]:
            print("### F-286: FATAL: old owner tagging remains")
            return 1
        print("### F-286: already applied")
        return 0
    for path, old, _new, count in EDITS:
        actual = sources[path].count(old)
        if actual != count:
            print(f"### F-286: FATAL: anchor in {path} found {actual} times (expected {count})")
            return 1
    for path, old, new, count in EDITS:
        sources[path] = sources[path].replace(old, new, count)
    for path, source in sources.items():
        with open(path, "w") as stream:
            stream.write(source)
    print("### F-286: identified FE ownership")
    return 0


if __name__ == "__main__":
    sys.exit(main())
