"""F-279: keep tc-flower/ntuple ehash owners when a delete fails.

F-269 del_key may fail with the record still linked (-ESHUTDOWN, -ETIMEDOUT
before unlink). 0198 tc-flower DESTROY freed its rule regardless and a
same-cookie REPLACE overwrote the old key without deleting it; 0194 ntuple
kept adding after a failed replace-delete (two rules at one location) and
treated -ENOENT as a hard error. Flower ops now serialize on flower_mutex,
failed deletes stay on cls_flower_orphans (retried on the next flower op,
key refused with -EBUSY until settled), duplicate keys across flower
cookies or ntuple locations are refused (F-219 eviction would alias them),
and ntuple fails the replace instead of orphaning. dpaa_remove()
(which frees the netdev and so cannot retain owners) is reachable only by
sysfs unbind of the built-in dpaa/mac drivers, so suppress both.
"""

import sys

EDITS = [
    ('drivers/net/ethernet/freescale/dpaa/dpaa_eth.h',
     '\tspinlock_t flower_lock;\n\tstruct list_head cls_flowers;\n',
     '\tspinlock_t flower_lock;\n\tstruct list_head cls_flowers;\n\t/* F-279: serializes flower REPLACE/DESTROY; cls_flower_orphans holds\n\t * records whose ehash delete failed and may still be linked. */\n\tstruct mutex flower_mutex;\n\tstruct list_head cls_flower_orphans;\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_eth.c',
     '\tspin_lock_init(&priv->flower_lock);\n\tINIT_LIST_HEAD(&priv->cls_flowers);\n',
     '\tspin_lock_init(&priv->flower_lock);\n\tINIT_LIST_HEAD(&priv->cls_flowers);\n\tmutex_init(&priv->flower_mutex);\t/* F-279 */\n\tINIT_LIST_HEAD(&priv->cls_flower_orphans);\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_eth.c',
     '#define DPAA_CLS_FLOWER_KEY_MAX\tFMAN_PCD_FE_ROUTED_KEY_SIZE /* F-259 */\n',
     '#define DPAA_CLS_FLOWER_KEY_MAX\tFMAN_PCD_FE_ROUTED_KEY_SIZE /* F-259 */\n\n/* F-279: an ehash delete is settled when the record is gone (0) or was\n * already unlinked/quarantined by the PCD (-ENOENT). Anything else may\n * leave the record linked, so the driver keeps owning it. */\nstatic bool dpaa_cls_flower_del_settled(int err)\n{\n\treturn !err || err == -ENOENT;\n}\n\nstatic bool dpaa_cls_flower_same_key(const struct dpaa_cls_flower *fl,\n\t\t\t\t     const u8 *key, u8 key_size)\n{\n\treturn fl->key_size == key_size && !memcmp(fl->key, key, key_size);\n}\n\n/* Caller holds flower_mutex and has unlinked @fl from cls_flowers. */\nstatic int dpaa_cls_flower_release(struct dpaa_priv *priv, struct fman *fm,\n\t\t\t\t   u8 port_id, struct dpaa_cls_flower *fl)\n{\n\tint err = fman_pcd_fe_flow_del(fm, port_id, fl->key, fl->key_size);\n\n\tif (dpaa_cls_flower_del_settled(err)) {\n\t\tkfree(fl);\n\t\treturn 0;\n\t}\n\tnetdev_err(priv->net_dev,\n\t\t   "F-279: flower record delete failed (%d); retained\\n", err);\n\tspin_lock(&priv->flower_lock);\n\tlist_add_tail(&fl->node, &priv->cls_flower_orphans);\n\tspin_unlock(&priv->flower_lock);\n\treturn err;\n}\n\n/* Caller holds flower_mutex. */\nstatic void dpaa_cls_flower_orphan_retry(struct dpaa_priv *priv,\n\t\t\t\t\t struct fman *fm, u8 port_id)\n{\n\tstruct dpaa_cls_flower *pos, *tmp;\n\n\tlist_for_each_entry_safe(pos, tmp, &priv->cls_flower_orphans, node) {\n\t\tif (!dpaa_cls_flower_del_settled(\n\t\t\tfman_pcd_fe_flow_del(fm, port_id, pos->key,\n\t\t\t\t\t     pos->key_size)))\n\t\t\tcontinue;\n\t\tspin_lock(&priv->flower_lock);\n\t\tlist_del(&pos->node);\n\t\tspin_unlock(&priv->flower_lock);\n\t\tkfree(pos);\n\t}\n}\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_eth.c',
     '\tstruct dpaa_cls_flower *fl, *pos;\n',
     '\tstruct dpaa_cls_flower *fl, *pos, *old;\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_eth.c',
     '\tspin_lock(&priv->flower_lock);\n\tlist_for_each_entry(pos, &priv->cls_flowers, node) {\n\t\tif (pos->cookie_lo == (u32)f->cookie) {\n\t\t\tfl = pos;\n\t\t\tgoto have_slot;\n\t\t}\n\t}\n\tfl = NULL;\nhave_slot:\n\tspin_unlock(&priv->flower_lock);\n\n\tif (!fl) {\n\t\tfl = kzalloc(sizeof(*fl), GFP_KERNEL);\n\t\tif (!fl)\n\t\t\treturn -ENOMEM;\n\t}\n\n\tmemset(&action, 0, sizeof(action));\n\tmemcpy(action.key, key, key_size);\n\taction.key_size = key_size;\n\taction.table_idx = 0;\n\taction.drop_action = 1;\t/* F-247: the EXIT FE terminal */\n\n\terr = fman_pcd_fe_flow_add(fm, port_id, &action);\n\tif (err) {\n\t\tif (!fl || list_empty(&fl->node))\n\t\t\tkfree(fl);\n\t\treturn err;\n\t}\n\n\tif (!fl->cookie_lo) {\n\t\tfl->cookie_lo = (u32)f->cookie;\n\t\tmemcpy(fl->key, key, key_size);\n\t\tfl->key_size = key_size;\n\t\tspin_lock(&priv->flower_lock);\n\t\tlist_add_tail(&fl->node, &priv->cls_flowers);\n\t\tspin_unlock(&priv->flower_lock);\n\t} else {\n\t\tmemcpy(fl->key, key, key_size);\n\t\tfl->key_size = key_size;\n\t}\n\n\treturn 0;\n}\n\nstatic int dpaa_setup_tc_flower_destroy(struct net_device *net_dev,\n\t\t\t\t\tstruct flow_cls_offload *f)\n{\n\tstruct dpaa_priv *priv = netdev_priv(net_dev);\n\tstruct dpaa_cls_flower *pos, *tmp;\n\tstruct fman *fm;\n\tu8 port_id;\n\tint err = 0;\n\n\tif (!priv->mac_dev)\n\t\treturn -ENODEV;\n\tfm = fman_bind(priv->mac_dev->fman_dev);\n\tif (!fm)\n\t\treturn -ENODEV;\n\tport_id = fman_port_get_id(priv->mac_dev->port[0]);\n\n\tspin_lock(&priv->flower_lock);\n\tlist_for_each_entry_safe(pos, tmp, &priv->cls_flowers, node) {\n\t\tif (pos->cookie_lo != (u32)f->cookie)\n\t\t\tcontinue;\n\t\tlist_del(&pos->node);\n\t\tspin_unlock(&priv->flower_lock);\n\t\terr = fman_pcd_fe_flow_del(fm, port_id, pos->key,\n\t\t\t\t\t   pos->key_size);\n\t\tkfree(pos);\n\t\treturn err;\n\t}\n\tspin_unlock(&priv->flower_lock);\n\treturn 0;\n}\n\n',
     '\t/* F-279: serialize flower ownership. Retry retained deletes first,\n\t * refuse a key whose old record is not yet settled, and refuse a key\n\t * another rule already owns (a duplicate insert would evict it). */\n\tmutex_lock(&priv->flower_mutex);\n\tdpaa_cls_flower_orphan_retry(priv, fm, port_id);\n\terr = -EBUSY;\n\tlist_for_each_entry(pos, &priv->cls_flower_orphans, node)\n\t\tif (dpaa_cls_flower_same_key(pos, key, key_size))\n\t\t\tgoto out_unlock;\n\told = NULL;\n\terr = -EEXIST;\n\tlist_for_each_entry(pos, &priv->cls_flowers, node) {\n\t\tif (pos->cookie_lo == (u32)f->cookie)\n\t\t\told = pos;\n\t\telse if (dpaa_cls_flower_same_key(pos, key, key_size))\n\t\t\tgoto out_unlock;\n\t}\n\terr = 0;\n\tif (old && dpaa_cls_flower_same_key(old, key, key_size))\n\t\tgoto out_unlock;\n\n\tfl = kzalloc(sizeof(*fl), GFP_KERNEL);\n\tif (!fl) {\n\t\terr = -ENOMEM;\n\t\tgoto out_unlock;\n\t}\n\n\tmemset(&action, 0, sizeof(action));\n\tmemcpy(action.key, key, key_size);\n\taction.key_size = key_size;\n\taction.table_idx = 0;\n\taction.drop_action = 1;\t/* F-247: the EXIT FE terminal */\n\n\terr = fman_pcd_fe_flow_add(fm, port_id, &action);\n\tif (err) {\n\t\tkfree(fl);\n\t\tgoto out_unlock;\n\t}\n\n\tfl->cookie_lo = (u32)f->cookie;\n\tmemcpy(fl->key, key, key_size);\n\tfl->key_size = key_size;\n\tspin_lock(&priv->flower_lock);\n\tlist_add_tail(&fl->node, &priv->cls_flowers);\n\tif (old)\n\t\tlist_del(&old->node);\n\tspin_unlock(&priv->flower_lock);\n\t/* The new key is live; the old one is now released or retained. */\n\tif (old)\n\t\tdpaa_cls_flower_release(priv, fm, port_id, old);\n\nout_unlock:\n\tmutex_unlock(&priv->flower_mutex);\n\treturn err;\n}\n\nstatic int dpaa_setup_tc_flower_destroy(struct net_device *net_dev,\n\t\t\t\t\tstruct flow_cls_offload *f)\n{\n\tstruct dpaa_priv *priv = netdev_priv(net_dev);\n\tstruct dpaa_cls_flower *pos, *fl = NULL;\n\tstruct fman *fm;\n\tu8 port_id;\n\tint err = 0;\n\n\tif (!priv->mac_dev)\n\t\treturn -ENODEV;\n\tfm = fman_bind(priv->mac_dev->fman_dev);\n\tif (!fm)\n\t\treturn -ENODEV;\n\tport_id = fman_port_get_id(priv->mac_dev->port[0]);\n\n\tmutex_lock(&priv->flower_mutex);\n\tdpaa_cls_flower_orphan_retry(priv, fm, port_id);\n\tspin_lock(&priv->flower_lock);\n\tlist_for_each_entry(pos, &priv->cls_flowers, node) {\n\t\tif (pos->cookie_lo == (u32)f->cookie) {\n\t\t\tfl = pos;\n\t\t\tlist_del(&fl->node);\n\t\t\tbreak;\n\t\t}\n\t}\n\tspin_unlock(&priv->flower_lock);\n\t/* F-279: tc forgets the filter even on error, so a failed delete\n\t * keeps the record owned on cls_flower_orphans instead of freeing. */\n\tif (fl)\n\t\terr = dpaa_cls_flower_release(priv, fm, port_id, fl);\n\tmutex_unlock(&priv->flower_mutex);\n\treturn err;\n}\n\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_eth.c',
     'static struct platform_driver dpaa_driver = {\n\t.driver = {\n\t\t.name = KBUILD_MODNAME,\n\t},\n',
     'static struct platform_driver dpaa_driver = {\n\t.driver = {\n\t\t.name = KBUILD_MODNAME,\n\t\t/* F-279: no sysfs unbind; dpaa_remove() cannot keep ehash\n\t\t * owners whose delete failed once the netdev is freed. */\n\t\t.suppress_bind_attrs = true,\n\t},\n'),
    ('drivers/net/ethernet/freescale/fman/mac.c',
     '\t\t.name\t\t= KBUILD_MODNAME,\n\t\t.of_match_table\t= mac_match,\n\t},\n',
     '\t\t.name\t\t= KBUILD_MODNAME,\n\t\t.of_match_table\t= mac_match,\n\t\t/* F-279: mac_remove() unregisters the dpaa-ethernet child. */\n\t\t.suppress_bind_attrs\t= true,\n\t},\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c',
     '\terr = dpaa_cls_build_key(r, r->key, key_size);\n\tif (err) {\n\t\tkfree(r);\n\t\treturn err;\n\t}\n',
     '\terr = dpaa_cls_build_key(r, r->key, key_size);\n\tif (err) {\n\t\tkfree(r);\n\t\treturn err;\n\t}\n\t/* F-279: one owner per key. F-219 evicts a duplicate on insert, so a\n\t * second location with the same key would alias the first record. */\n\tlist_for_each_entry(pos, &priv->cls_rules, node) {\n\t\tif (pos != replaced && pos->key_size == key_size &&\n\t\t    !memcmp(pos->key, r->key, key_size)) {\n\t\t\tkfree(r);\n\t\t\treturn -EEXIST;\n\t\t}\n\t}\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c',
     '\t/* Replace-in-place: drop the old record first, add the new one, and\n\t * re-add the old on failure so list state matches silicon. */\n\tif (replaced) {\n\t\terr = fman_pcd_fe_flow_del(fm, port_id, replaced->key,\n\t\t\t\t     replaced->key_size);\n\t\tif (!err) {\n\t\t\tlist_del(&replaced->node);\n\t\t\tpriv->cls_rule_cnt--;\n\t\t\treplaced_removed = true;\n\t\t}\n\t}\n\n',
     '\t/* Replace-in-place: drop the old record first, add the new one, and\n\t * re-add the old on failure so list state matches silicon.\n\t * F-279: -ENOENT means the old record is already gone; any other\n\t * delete error may leave it linked, so fail the insert and keep it. */\n\tif (replaced) {\n\t\terr = fman_pcd_fe_flow_del(fm, port_id, replaced->key,\n\t\t\t\t     replaced->key_size);\n\t\tif (err && err != -ENOENT) {\n\t\t\tkfree(r);\n\t\t\treturn err;\n\t\t}\n\t\tlist_del(&replaced->node);\n\t\tpriv->cls_rule_cnt--;\n\t\treplaced_removed = true;\n\t}\n\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c',
     '\terr = fman_pcd_fe_flow_del(fm, port_id, r->key, r->key_size);\n\tif (err)\n\t\treturn err;\n\n\tlist_del(&r->node);\n',
     '\terr = fman_pcd_fe_flow_del(fm, port_id, r->key, r->key_size);\n\tif (err && err != -ENOENT)\t/* F-279: -ENOENT = already gone */\n\t\treturn err;\n\n\tlist_del(&r->node);\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c',
     '\tstruct dpaa_cls_rule *r, *tmp;\n\n\tstruct fman *fm;\n\tu8 port_id;\n',
     '\tstruct dpaa_cls_rule *r, *tmp;\n\n\tstruct fman *fm;\n\tu8 port_id;\n\tint err;\n'),
    ('drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c',
     '\t\t\tfman_pcd_fe_flow_del(fm, port_id, r->key, r->key_size);\n\t\t\tlist_del(&r->node);\n',
     '\t\t\terr = fman_pcd_fe_flow_del(fm, port_id, r->key,\n\t\t\t\t\t\t   r->key_size);\n\t\t\t/* F-279: unreachable after probe (unbind suppressed);\n\t\t\t * the netdev is going away, so report what stays linked. */\n\t\t\tif (err && err != -ENOENT)\n\t\t\t\tnetdev_err(net_dev,\n\t\t\t\t\t   "F-279: ntuple loc %u delete failed (%d) at remove\\n",\n\t\t\t\t\t   r->fs.location, err);\n\t\t\tlist_del(&r->node);\n'),
]


def main():
    files = {}
    for path, _old, _new in EDITS:
        if path not in files:
            try:
                with open(path) as stream:
                    files[path] = stream.read()
            except FileNotFoundError:
                print(f"### F-279: FATAL: {path} not found")
                return 1
    done = [new in files[path] for path, _old, new in EDITS]
    if all(done):
        print("### F-279: already applied")
        return 0
    if any(done):
        print("### F-279: FATAL: partially applied tree")
        return 1
    for i, (path, old, new) in enumerate(EDITS):
        count = files[path].count(old)
        if count != 1:
            print(f"### F-279: FATAL: anchor {i} in {path} found {count} times (expected 1)")
            return 1
        files[path] = files[path].replace(old, new, 1)
    for path, src in files.items():
        with open(path, "w") as stream:
            stream.write(src)
    print("### F-279: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
