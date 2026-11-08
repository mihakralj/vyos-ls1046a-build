"""F-280: one software owner per shared ehash key.

ASK, 0198 tc-flower, 0194 ntuple and dpaa_wgfp insert into the same per-port
ehash table. F-219 evicts any same-key record before an insert and
fman_pcd_fe_flow_del() matches by key alone, so one subsystem could silently
replace or remove a record another still tracks (a flower drop rule lost to
an ASK route, or an ASK delete removing a WGFP steering record). Each record
now carries an owner (fman_pcd_fe_flow_action.flags low byte, previously
reserved and zero, so ask.ko stays FMAN_PCD_FE_OWNER_ASK unchanged). Insert
of another owner's key returns -EEXIST, delete of another owner's key is a
no-op success, and a same-owner re-insert keeps the F-219 eviction.
dpaa_wgfp.c (kernel/common/files) passes FMAN_PCD_FE_OWNER_WGFP itself.
"""

import sys

EDITS = [
    ('include/linux/fsl/fman_pcd.h',
     '\tu32  flags;\t\t\t/* reserved for future use */\n',
     '\tu32  flags;\t\t\t/* F-280: low byte = FMAN_PCD_FE_OWNER_* */\n'),
    ('include/linux/fsl/fman_pcd.h',
     'int fman_pcd_fe_flow_del(struct fman *fm, u8 hw_port_id,\n\t\t\t const u8 *key, u8 key_size);\n',
     'int fman_pcd_fe_flow_del(struct fman *fm, u8 hw_port_id,\n\t\t\t const u8 *key, u8 key_size);\n/* F-280: ehash record owner, carried in fman_pcd_fe_flow_action.flags.\n * A key held by one owner is refused (-EEXIST) to the others and only its\n * owner can delete it; a same-owner re-insert keeps the F-219 eviction.\n * fman_pcd_fe_flow_del() deletes as FMAN_PCD_FE_OWNER_ASK. */\n#define FMAN_PCD_FE_OWNER_MASK\t\t0xffU\n#define FMAN_PCD_FE_OWNER_ASK\t\t0x00U\t/* ask.ko, debugfs, legacy */\n#define FMAN_PCD_FE_OWNER_FLOWER\t0x01U\t/* 0198 tc-flower */\n#define FMAN_PCD_FE_OWNER_NTUPLE\t0x02U\t/* 0194 ethtool ntuple */\n#define FMAN_PCD_FE_OWNER_WGFP\t\t0x03U\t/* dpaa_wgfp */\nint fman_pcd_fe_flow_del_owner(struct fman *fm, u8 hw_port_id,\n\t\t\t       const u8 *key, u8 key_size, u32 owner);\n'),
    ('drivers/net/ethernet/freescale/fman/fman_pcd.c',
     '\tu16 index;\t\t\t/* bucket index (for the show node) */\n\tu8 key_size;\n};\n',
     '\tu16 index;\t\t\t/* bucket index (for the show node) */\n\tu8 key_size;\n\tu8 owner;\t\t\t/* F-280: FMAN_PCD_FE_OWNER_* */\n};\n'),
    ('drivers/net/ethernet/freescale/fman/fman_pcd.c',
     'int fman_pcd_fe_flow_add(struct fman *fm, u8 hw_port_id,\n',
     '/* F-280: the live flow for @key in @t, or NULL. Caller holds fe_lock. */\nstatic struct fman_pcd_ehash_flow *\nfman_pcd_ehash_lookup(struct fman_pcd_ehash_table *t, const u8 *key,\n\t\t      u8 key_size)\n{\n\tstruct fman_pcd_ehash_flow *f;\n\n\tif (!key || key_size == 0 || key_size != t->key_size)\n\t\treturn NULL;\n\thlist_for_each_entry(f, &t->bidx[fman_pcd_ehash_bucket_index(key,\n\t\t\t\t\tkey_size, t->hash_shift, t->hash_mask)],\n\t\t\t     bnode)\n\t\tif (f->key_size == key_size &&\n\t\t    !memcmp((u8 *)f->record + FMAN_EHASH_FLOW_KEY_OFF, key,\n\t\t\t    key_size))\n\t\t\treturn f;\n\treturn NULL;\n}\n\n/* F-280: true when another owner holds @key. F-219 evicts a same-key\n * record on insert and delete matches by key alone, so without this one\n * subsystem (ASK, tc-flower, ntuple, WGFP) could replace or remove a\n * record that another subsystem still tracks as its own. */\nstatic bool fman_pcd_ehash_foreign(struct fman_pcd_ehash_table *t,\n\t\t\t\t   const u8 *key, u8 key_size, u32 owner)\n{\n\tstruct fman_pcd_ehash_flow *f = fman_pcd_ehash_lookup(t, key, key_size);\n\n\treturn f && f->owner != (owner & FMAN_PCD_FE_OWNER_MASK);\n}\n\nint fman_pcd_fe_flow_add(struct fman *fm, u8 hw_port_id,\n'),
    ('drivers/net/ethernet/freescale/fman/fman_pcd.c',
     '\tif (action->table_idx == 0)\n\t\tt = fman_pcd_ehash_table_for_port(pcd, hw_port_id);\n\telse\n\t\tt = fman_pcd_ehash_table_by_index(pcd, action->table_idx);\n\tif (!t) {\n\t\tmutex_unlock(&pcd->fe_lock);\n\t\treturn -ENODEV;\n\t}\n',
     "\tif (action->table_idx == 0)\n\t\tt = fman_pcd_ehash_table_for_port(pcd, hw_port_id);\n\telse\n\t\tt = fman_pcd_ehash_table_by_index(pcd, action->table_idx);\n\tif (!t) {\n\t\tmutex_unlock(&pcd->fe_lock);\n\t\treturn -ENODEV;\n\t}\n\t/* F-280: another owner's key is refused, never evicted. */\n\tif (fman_pcd_ehash_foreign(t, action->key, action->key_size,\n\t\t\t\t   action->flags)) {\n\t\tmutex_unlock(&pcd->fe_lock);\n\t\treturn -EEXIST;\n\t}\n"),
    ('drivers/net/ethernet/freescale/fman/fman_pcd.c',
     '&_vlan : NULL);\n',
     "&_vlan : NULL);\n\t\t/* F-280: tag the key's record (kzalloc'd = OWNER_ASK). Set it\n\t\t * even on error: a timed-out insert may leave it linked. */\n\t\t{\n\t\t\tstruct fman_pcd_ehash_flow *of =\n\t\t\t\tfman_pcd_ehash_lookup(t, action->key,\n\t\t\t\t\t\t      action->key_size);\n\n\t\t\tif (of)\n\t\t\t\tof->owner = action->flags &\n\t\t\t\t\t    FMAN_PCD_FE_OWNER_MASK;\n\t\t}\n"),
    ('drivers/net/ethernet/freescale/fman/fman_pcd.c',
     'int fman_pcd_fe_flow_del(struct fman *fm, u8 hw_port_id,\n\t\t\t const u8 *key, u8 key_size)\n{\n',
     'int fman_pcd_fe_flow_del_owner(struct fman *fm, u8 hw_port_id,\n\t\t\t       const u8 *key, u8 key_size, u32 owner)\n{\n'),
    ('drivers/net/ethernet/freescale/fman/fman_pcd.c',
     '\t/* No key => legacy clear-all (admin flush / disengage). */\n\tif (!key || key_size == 0) {\n\t\tint err = fman_pcd_ehash_flow_clear_all(pcd);\n',
     "\t/* No key => legacy clear-all (admin flush / disengage). */\n\tif (!key || key_size == 0) {\n\t\tint err;\n\n\t\t/* F-280: only the ASK owner may clear every owner's records. */\n\t\tif (owner & FMAN_PCD_FE_OWNER_MASK) {\n\t\t\tmutex_unlock(&pcd->fe_lock);\n\t\t\treturn -EINVAL;\n\t\t}\n\t\terr = fman_pcd_ehash_flow_clear_all(pcd);\n"),
    ('drivers/net/ethernet/freescale/fman/fman_pcd.c',
     '\t{\n\t\tint err = fman_pcd_ehash_del_key(t, key, key_size);\n',
     "\t/* F-280: another owner's record is not ours to delete; for this\n\t * caller the key is already gone. */\n\tif (fman_pcd_ehash_foreign(t, key, key_size, owner)) {\n\t\tmutex_unlock(&pcd->fe_lock);\n\t\treturn 0;\n\t}\n\t{\n\t\tint err = fman_pcd_ehash_del_key(t, key, key_size);\n"),
    ('drivers/net/ethernet/freescale/fman/fman_pcd.c',
     'EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_del);\n',
     'EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_del_owner);\n\nint fman_pcd_fe_flow_del(struct fman *fm, u8 hw_port_id,\n\t\t\t const u8 *key, u8 key_size)\n{\n\treturn fman_pcd_fe_flow_del_owner(fm, hw_port_id, key, key_size,\n\t\t\t\t\t  FMAN_PCD_FE_OWNER_ASK);\n}\nEXPORT_SYMBOL_GPL(fman_pcd_fe_flow_del);\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_eth.c',
     '\taction.drop_action = 1;\t/* F-247: the EXIT FE terminal */\n\n\terr = fman_pcd_fe_flow_add(fm, port_id, &action);\n',
     '\taction.drop_action = 1;\t/* F-247: the EXIT FE terminal */\n\taction.flags = FMAN_PCD_FE_OWNER_FLOWER;\t/* F-280 */\n\n\terr = fman_pcd_fe_flow_add(fm, port_id, &action);\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_eth.c',
     '\tint err = fman_pcd_fe_flow_del(fm, port_id, fl->key, fl->key_size);\n',
     '\tint err = fman_pcd_fe_flow_del_owner(fm, port_id, fl->key,\n\t\t\t\t\t     fl->key_size,\n\t\t\t\t\t     FMAN_PCD_FE_OWNER_FLOWER);\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_eth.c',
     '\t\t\tfman_pcd_fe_flow_del(fm, port_id, pos->key,\n\t\t\t\t\t     pos->key_size)))\n',
     '\t\t\tfman_pcd_fe_flow_del_owner(fm, port_id, pos->key,\n\t\t\t\t\t\t   pos->key_size,\n\t\t\t\t\t\t   FMAN_PCD_FE_OWNER_FLOWER)))\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c',
     '\taction.table_idx = 0;\t\t/* per-port IPv4 ehash table */\n',
     '\taction.table_idx = 0;\t\t/* per-port IPv4 ehash table */\n\taction.flags = FMAN_PCD_FE_OWNER_NTUPLE;\t/* F-280 */\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c',
     '\t\t\t\tra.key_size = replaced->key_size;\n',
     '\t\t\t\tra.key_size = replaced->key_size;\n\t\t\t\tra.flags = FMAN_PCD_FE_OWNER_NTUPLE;\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c',
     '\t\terr = fman_pcd_fe_flow_del(fm, port_id, replaced->key,\n\t\t\t\t     replaced->key_size);\n',
     '\t\terr = fman_pcd_fe_flow_del_owner(fm, port_id, replaced->key,\n\t\t\t\t\t\t replaced->key_size,\n\t\t\t\t\t\t FMAN_PCD_FE_OWNER_NTUPLE);\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c',
     '\terr = fman_pcd_fe_flow_del(fm, port_id, r->key, r->key_size);\n\tif (err && err != -ENOENT)\t/* F-279',
     '\terr = fman_pcd_fe_flow_del_owner(fm, port_id, r->key, r->key_size,\n\t\t\t\t\t FMAN_PCD_FE_OWNER_NTUPLE);\n\tif (err && err != -ENOENT)\t/* F-279'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c',
     '\t\t\terr = fman_pcd_fe_flow_del(fm, port_id, r->key,\n\t\t\t\t\t\t   r->key_size);\n',
     '\t\t\terr = fman_pcd_fe_flow_del_owner(fm, port_id, r->key,\n\t\t\t\t\t\t\t r->key_size,\n\t\t\t\t\t\t\t FMAN_PCD_FE_OWNER_NTUPLE);\n'),
]


def main():
    files = {}
    for path, _old, _new in EDITS:
        if path not in files:
            try:
                with open(path) as stream:
                    files[path] = stream.read()
            except FileNotFoundError:
                print(f"### F-280: FATAL: {path} not found")
                return 1
    done = [new in files[path] for path, _old, new in EDITS]
    if all(done):
        print("### F-280: already applied")
        return 0
    if any(done):
        print("### F-280: FATAL: partially applied tree")
        return 1
    for i, (path, old, new) in enumerate(EDITS):
        count = files[path].count(old)
        if count != 1:
            print(f"### F-280: FATAL: anchor {i} in {path} found {count} times (expected 1)")
            return 1
        files[path] = files[path].replace(old, new, 1)
    for path, src in files.items():
        with open(path, "w") as stream:
            stream.write(src)
    print("### F-280: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
