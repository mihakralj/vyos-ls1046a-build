// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - flow_offload subsystem (PR8 / M1.4 + PR14j ingress-only bind).
 *
 * Implements the `flow_block_cb` dispatcher that the in-tree dpaa
 * patch (PR11/M2.2 - `0002-dpaa-eth-flow-block.patch`) plugs into the
 * `dpaa_setup_tc()` path.
 *
 * PR14j architectural change vs. PR14g:
 *
 *   PR14g bound the KG scheme to whichever dpaa netdev received the
 *   FIRST FLOW_BLOCK_BIND.  On the M2 test rig that turned out to be
 *   egress eth4 - so the ingress eth3 bind returned -EBUSY and the
 *   classifier never saw RX traffic.  Result: 6.9 Gbps / 54% CPU, well
 *   below the 18 Gbps M2 gate.
 *
 *   PR14j defers ask_hw_port_bind() from FLOW_BLOCK_BIND to
 *   FLOW_CLS_REPLACE.  At REPLACE time the rule carries a REDIRECT /
 *   MIRRED action with the egress target, so the netdev whose block
 *   received the REPLACE is unambiguously the INGRESS netdev (the
 *   one that matched the 5-tuple).  We bind KG to THAT netdev's FMan
 *   port id.  Egress-only netdevs never receive a REPLACE for which
 *   they are the source, so they never consume the single-port-per-
 *   scheme KGSE_MV slot.
 *
 *   FLOW_BLOCK_BIND still installs the block_cb (so we'll see the
 *   REPLACE events) - it just does not touch silicon.
 *
 * Translation shape (FLOW_CLS_* -> ask_flow_*):
 *
 *   FLOW_CLS_REPLACE: parse match into ask_flow_key, parse action into
 *     action_flags + oif, resolve neighbour MAC for the egress netdev
 *     (so the OH-port INSRT_GENERIC can push a real L2 header), bind
 *     KG to this netdev's FMan port id (idempotent), then call
 *     ask_flow_insert().
 *
 *   FLOW_CLS_DESTROY: ask_flow_remove(table, cookie).
 *
 *   FLOW_CLS_STATS: ask_flow_get_stats() into flow_stats_update().
 *
 * Spec ref: §4.3 (flow_block_cb integration), §11.1 (M2 perf gate),
 * plans/PR14j-DESIGN.md (the two-stage OH-port architecture).
 */

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/in.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/list.h>
#include <linux/spinlock.h>
#include <linux/netdevice.h>
#include <linux/inetdevice.h>
#include <linux/of.h>
#include <linux/string.h>
#include <linux/if_ether.h>
#include <net/flow_offload.h>
#include <net/pkt_cls.h>
#include <net/arp.h>
#include <net/neighbour.h>
#include <net/net_namespace.h>
#include <linux/fsl/dpaa_flow_offload.h>

#include "include/ask_internal.h"

/* ------------------------------------------------------------------------- */
/* PR14j: direction classification helper                                     */
/*                                                                            */
/* Used by ask_flow_offload_setup_tc(FLOW_BLOCK_BIND) for logging only;       */
/* the actual ingress-vs-egress decision in PR14j is made at FLOW_CLS_REPLACE */
/* time (see ask_flow_offload_replace() below) when the REDIRECT target is    */
/* visible.  At FLOW_BLOCK_BIND we don't have enough context to know which    */
/* end of the flow this netdev is going to be.                                */
/*                                                                            */
/* The walk: from dev->dev.parent->of_node (the platform device's DT node)    */
/* walk upward via of_get_next_parent(), looking for a node with the          */
/* "fsl,fman-memac" compatible or "fsl,fman-mac-rx"/"fsl,fman-mac-tx"         */
/* phandle properties.  DPAA1 MACs always have BOTH rx and tx phandles, so    */
/* the helper returns ASK_DIR_INGRESS for any MAC that has an rx phandle      */
/* pointing at a "*-rx" port (= all DPAA1 MACs) and falls back to             */
/* ASK_DIR_EGRESS / ASK_DIR_UNKNOWN otherwise.                                */
/*                                                                            */
/* This is intentionally weak; the real binding decision is in the REPLACE    */
/* path.  The helper exists so kunit can exercise the of_node parsing logic   */
/* and so future PR14k/l revisions can elevate it.                            */
/* ------------------------------------------------------------------------- */

static bool ask_node_is_rx_port(const struct device_node *np)
{
        if (!np)
                return false;
        return of_device_is_compatible(np, "fsl,fman-port-1g-rx")  ||
               of_device_is_compatible(np, "fsl,fman-port-10g-rx") ||
               of_device_is_compatible(np, "fsl,fman-v3-port-rx");
}

static bool ask_node_is_tx_port(const struct device_node *np)
{
        if (!np)
                return false;
        return of_device_is_compatible(np, "fsl,fman-port-1g-tx")  ||
               of_device_is_compatible(np, "fsl,fman-port-10g-tx") ||
               of_device_is_compatible(np, "fsl,fman-v3-port-tx");
}

int ask_flow_offload_classify_dir(const struct net_device *dev)
{
        struct device_node *mac_np;
        struct device_node *port_np;
        struct device *parent;
        int dir = ASK_DIR_UNKNOWN;

        if (!dev)
                return ASK_DIR_UNKNOWN;

        parent = dev->dev.parent;
        if (!parent || !parent->of_node)
                return ASK_DIR_UNKNOWN;

        /* Walk up to the MAC node carrying the rx/tx phandle properties. */
        mac_np = of_node_get(parent->of_node);
        while (mac_np) {
                struct device_node *tmp;

                if (of_device_is_compatible(mac_np, "fsl,fman-memac") ||
                    of_get_property(mac_np, "fsl,fman-mac-rx", NULL) ||
                    of_get_property(mac_np, "fsl,fman-mac-tx", NULL))
                        break;

                tmp = of_get_next_parent(mac_np);
                mac_np = tmp;
        }

        if (!mac_np)
                return ASK_DIR_UNKNOWN;

        port_np = of_parse_phandle(mac_np, "fsl,fman-mac-rx", 0);
        if (port_np) {
                if (ask_node_is_rx_port(port_np))
                        dir = ASK_DIR_INGRESS;
                of_node_put(port_np);
        }

        if (dir == ASK_DIR_UNKNOWN) {
                port_np = of_parse_phandle(mac_np, "fsl,fman-mac-tx", 0);
                if (port_np) {
                        if (ask_node_is_tx_port(port_np))
                                dir = ASK_DIR_EGRESS;
                        of_node_put(port_np);
                }
        }

        of_node_put(mac_np);
        return dir;
}
EXPORT_SYMBOL_GPL(ask_flow_offload_classify_dir);

/* ------------------------------------------------------------------------- */
/* PR14j: neighbour resolution for OH-port MANIP_INSRT_GENERIC                */
/*                                                                            */
/* The OH-port chain pushes a fresh 14-byte L2 header onto the rewritten      */
/* IPv4 packet (next_hop_mac, egress_mac, ETH_P_IP).  We need both MACs at    */
/* FLOW_CLS_REPLACE time so the per-flow fman_pcd_manip can be created in    */
/* ask_hw_flow_insert_v4_tcp().                                               */
/*                                                                            */
/*   egress_mac    = the egress netdev's own MAC (dev_addr)                   */
/*   next_hop_mac  = neigh_lookup(arp_tbl, dst_ip, egress_dev) and check      */
/*                   NUD_CONNECTED|NUD_REACHABLE|NUD_PERMANENT.  If the       */
/*                   neighbour is not yet resolved we leave next_hop_mac      */
/*                   zero; ask_hw_flow_insert_v4_tcp() returns -EAGAIN and    */
/*                   the SW path handles the flow until the neighbour         */
/*                   resolves and the next retry succeeds.                    */
/* ------------------------------------------------------------------------- */

static void ask_resolve_neigh_v4(struct net_device *egress_dev,
                                 __be32 dst_ip,
                                 u8 *out_next_hop_mac,
                                 u8 *out_egress_mac)
{
        struct neighbour *n;
        u32 dst_key = (__force u32)dst_ip;

        memset(out_next_hop_mac, 0, ETH_ALEN);
        memset(out_egress_mac,   0, ETH_ALEN);

        if (!egress_dev)
                return;

        memcpy(out_egress_mac, egress_dev->dev_addr, ETH_ALEN);

        n = neigh_lookup(&arp_tbl, &dst_key, egress_dev);
        if (!n)
                return;

        /*
         * PR14y: accept any NUD_VALID state (which includes STALE,
         * DELAY, PROBE in addition to REACHABLE/PERMANENT/NOARP).
         * A STALE neighbour still has a valid n->ha — STALE only means
         * "no recent confirmation traffic", not "MAC unknown".  The
         * earlier PR14j mask of CONNECTED|REACHABLE|PERMANENT rejected
         * every STALE entry, which is the dominant state for ARP
         * entries refreshed >30 s ago.  Result was every nft-flowtable
         * REPLACE returning -EAGAIN ("neigh unresolved") even though
         * the kernel had a perfectly good lladdr cached.  The full
         * M2 gate (16 parallel iperf3 streams over a single 5-tuple
         * pair) measured 6.853 Gbps at 63 % CPU with the narrow mask
         * because all flows fell back to the SW fastpath.
         *
         * Reference: include/net/neighbour.h
         *   #define NUD_VALID  (NUD_PERMANENT|NUD_NOARP|NUD_REACHABLE| \
         *                       NUD_PROBE|NUD_STALE|NUD_DELAY)
         * NUD_VALID is exactly "n->ha is meaningful".  Anything not
         * in NUD_VALID (NONE/INCOMPLETE/FAILED) has no MAC and must
         * still bounce to SW.
         */
        read_lock_bh(&n->lock);
        if (n->nud_state & NUD_VALID)
                memcpy(out_next_hop_mac, n->ha, ETH_ALEN);
        read_unlock_bh(&n->lock);

        neigh_release(n);
}

/* ------------------------------------------------------------------------- */
/* Per-block state                                                            */
/* ------------------------------------------------------------------------- */

struct ask_flow_block_priv {
        struct net_device *dev;
};

static LIST_HEAD(ask_flow_block_priv_list);
static DEFINE_SPINLOCK(ask_flow_block_priv_lock);

struct ask_flow_block_priv_entry {
        struct list_head node;
        struct ask_flow_block_priv priv;
};

static struct ask_flow_block_priv_entry *
ask_flow_block_priv_alloc(struct net_device *dev)
{
        struct ask_flow_block_priv_entry *e;

        e = kzalloc(sizeof(*e), GFP_KERNEL);
        if (!e)
                return NULL;

        e->priv.dev = dev;
        spin_lock(&ask_flow_block_priv_lock);
        list_add(&e->node, &ask_flow_block_priv_list);
        spin_unlock(&ask_flow_block_priv_lock);
        return e;
}

static void ask_flow_block_priv_free(void *cb_priv)
{
        struct ask_flow_block_priv *p = cb_priv;
        struct ask_flow_block_priv_entry *e, *tmp;

        if (!p)
                return;

        spin_lock(&ask_flow_block_priv_lock);
        list_for_each_entry_safe(e, tmp, &ask_flow_block_priv_list, node) {
                if (&e->priv == p) {
                        list_del(&e->node);
                        spin_unlock(&ask_flow_block_priv_lock);
                        kfree(e);
                        return;
                }
        }
        spin_unlock(&ask_flow_block_priv_lock);
}

static struct net_device *
ask_flow_block_priv_dev(void *cb_priv)
{
        struct ask_flow_block_priv *p = cb_priv;

        return p ? p->dev : NULL;
}

/* ------------------------------------------------------------------------- */
/* Match parsing                                                              */
/* ------------------------------------------------------------------------- */

static int ask_parse_match_v4(struct flow_cls_offload *f,
                              struct ask_flow_key *key)
{
        struct flow_rule *rule = flow_cls_offload_flow_rule(f);
        struct flow_dissector *d = rule->match.dissector;

        memset(key, 0, sizeof(*key));
        key->l3_proto = ASK_FLOW_L3_IPV4;

        if (flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_BASIC)) {
                struct flow_match_basic m;

                flow_rule_match_basic(rule, &m);
                if (m.key->n_proto != htons(ETH_P_IP))
                        return -EOPNOTSUPP;
                key->l4_proto = m.key->ip_proto;
        } else {
                return -EOPNOTSUPP;
        }

        if (flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_IPV4_ADDRS)) {
                struct flow_match_ipv4_addrs m;

                flow_rule_match_ipv4_addrs(rule, &m);
                memcpy(&key->src_ip[0], &m.key->src, 4);
                memcpy(&key->dst_ip[0], &m.key->dst, 4);
        } else {
                return -EOPNOTSUPP;
        }

        if (key->l4_proto == IPPROTO_TCP || key->l4_proto == IPPROTO_UDP) {
                struct flow_match_ports m;

                if (!flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_PORTS))
                        return -EOPNOTSUPP;
                flow_rule_match_ports(rule, &m);
                key->sport = m.key->src;
                key->dport = m.key->dst;
        }

        if (flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_VLAN)) {
                struct flow_match_vlan m;

                flow_rule_match_vlan(rule, &m);
                key->vlan_id = m.key->vlan_id;
        }

        (void)d;
        return 0;
}

/* ------------------------------------------------------------------------- */
/* Action parsing                                                             */
/*                                                                            */
/* PR14j extension: also returns the egress net_device * (act->dev) so the    */
/* caller can run neigh_lookup() and fill key->next_hop_mac / egress_mac.     */
/* The pointer is borrowed from the rule and is RCU-protected; caller must    */
/* use it before returning from the FLOW_CLS_REPLACE handler.                 */
/* ------------------------------------------------------------------------- */

static int ask_parse_action(struct flow_cls_offload *f,
                            u32 *out_action_flags, u32 *out_oif,
                            struct net_device **out_egress_dev)
{
        struct flow_rule *rule = flow_cls_offload_flow_rule(f);
        struct flow_action_entry *act;
        struct net_device *egress = NULL;
        u32 flags = 0;
        u32 oif = 0;
        int i;

        flow_action_for_each(i, act, &rule->action) {
                switch (act->id) {
                case FLOW_ACTION_REDIRECT:
                case FLOW_ACTION_MIRRED:
                        if (!act->dev)
                                return -EOPNOTSUPP;
                        oif = act->dev->ifindex;
                        egress = act->dev;
                        break;
                case FLOW_ACTION_VLAN_PUSH:
                        flags |= ASK_ACT_VLAN_PUSH;
                        break;
                case FLOW_ACTION_VLAN_POP:
                        flags |= ASK_ACT_VLAN_POP;
                        break;
                case FLOW_ACTION_CSUM:
                        break;
                case FLOW_ACTION_PTYPE:
                case FLOW_ACTION_ACCEPT:
                        break;
                /*
                 * PR14q: kernel 6.18 nft flowtable offload emits
                 * FLOW_ACTION_MANGLE (L2 dst-MAC + IP TTL decrement at
                 * minimum, optionally NAT src/dst rewrite) BEFORE the
                 * FLOW_ACTION_REDIRECT that names the egress netdev.
                 * PR14p instrumentation on 2026-05-17 caught every
                 * REPLACE returning -EOPNOTSUPP (-95) from this switch
                 * because MANGLE was unhandled. The HW path does not
                 * yet apply MANGLE rewrites (OH-port chain only pushes
                 * the next-hop L2 header from neigh_lookup); we accept
                 * the action as a no-op here so that the REDIRECT that
                 * follows actually gets to set oif. Header rewrite
                 * fidelity is deferred to PR14r/PR14s.
                 *
                 * FLOW_ACTION_TUNNEL_ENCAP / FLOW_ACTION_TUNNEL_DECAP
                 * are similarly accepted as no-ops so a future kernel
                 * that emits them on the flowtable path does not
                 * regress us back to silent SW fallback.
                 */
                case FLOW_ACTION_MANGLE:
                case FLOW_ACTION_ADD:
                        break;
                default:
                        pr_info_ratelimited("ask: flow_offload: parse_action: unhandled act->id=%u (treating as -EOPNOTSUPP)\n",
                                            act->id);
                        return -EOPNOTSUPP;
                }
        }

        if (oif == 0) {
                pr_info_ratelimited("ask: flow_offload: parse_action: no REDIRECT/MIRRED found (oif still 0)\n");
                return -EOPNOTSUPP;
        }

        *out_action_flags = flags;
        *out_oif = oif;
        if (out_egress_dev)
                *out_egress_dev = egress;
        return 0;
}

/* ------------------------------------------------------------------------- */
/* FLOW_CLS_* dispatch                                                        */
/* ------------------------------------------------------------------------- */

static int ask_flow_offload_replace(struct net_device *ingress_dev,
                                    struct flow_cls_offload *f)
{
        struct ask_flow_table *t = ask_flow_default_table();
        struct net_device *egress_dev = NULL;
        struct ask_flow_key key;
        __be32 dst_ip;
        u32 hw_id = 0;
        u32 action_flags = 0;
        u32 oif = 0;
        int rc;

        if (!t) {
                pr_info_ratelimited("ask: flow_offload: REPLACE early-return (no default table) cookie=0x%lx\n",
                                    f->cookie);
                return -EOPNOTSUPP;
        }

        rc = ask_parse_match_v4(f, &key);
        if (rc) {
                pr_info_ratelimited("ask: flow_offload: REPLACE early-return (parse_match_v4=%d) cookie=0x%lx\n",
                                    rc, f->cookie);
                return rc;
        }

        rc = ask_parse_action(f, &action_flags, &oif, &egress_dev);
        if (rc) {
                pr_info_ratelimited("ask: flow_offload: REPLACE early-return (parse_action=%d) cookie=0x%lx\n",
                                    rc, f->cookie);
                return rc;
        }

        /*
         * PR14r (2026-05-17): dedupe duplicate REPLACE for the same
         * cookie BEFORE touching silicon.
         *
         * nft flowtable offload registers our block_cb on every netdev
         * in the flowtable's `devices = { ... }` list.  For a typical
         * router flowtable that contains BOTH the ingress and the
         * egress port — so when a flow add fires, the same cookie is
         * delivered to us TWICE: once via the ingress netdev's block,
         * once via the egress netdev's block.
         *
         * The SW path in ask_flow_insert() correctly dedupes via the
         * rhashtable insert (-EEXIST → wrapped to 0 below).  But it
         * does so AFTER calling ask_hw_flow_insert(), which means the
         * second call burns a fresh CC node slot, then rolls it back
         * by re-programming the slot to DROP via
         * ask_hw_flow_remove() — but the FMan PCD CC API has NO key-
         * removal primitive (see ask_hw.c PR14r comment + fman_pcd_cc.c
         * line 882: keys are append-only with DROP tombstones).  So
         * every duplicate burns ONE permanent slot, doubling slot
         * consumption per flow.
         *
         * Skip the wasted hw_insert by checking the SW table first.
         * If the cookie already lives there, this is the second-
         * direction (egress block) replay — treat it as success and
         * return.  Only the first arrival (ingress) actually drives
         * hardware.
         *
         * PR14p instrumentation on 2026-05-17 caught the smoking-gun
         * dmesg pattern: every flow shows two consecutive `REPLACE
         * installed` lines, dev=eth3 then dev=eth4, identical cookie,
         * consecutive hw_id slots.  After this dedupe only the eth3
         * line should appear; the eth4 second-replay logs a single
         * `REPLACE dedup` line instead.
         */
        {
                struct ask_flow *existing;

                rcu_read_lock();
                existing = ask_flow_lookup(t, (u64)f->cookie);
                rcu_read_unlock();
                if (existing) {
                        pr_info_ratelimited("ask: flow_offload: REPLACE dedup cookie=0x%lx ingress=%s (already installed via first-arrival block)\n",
                                            f->cookie,
                                            ingress_dev ? netdev_name(ingress_dev) : "?");
                        return 0;
                }
        }

        /*
         * PR14j: resolve the OH-chain L2 header.  We need
         *   egress_mac   = egress netdev's own MAC
         *   next_hop_mac = neigh ARP entry for dst_ip on egress_dev
         * before handing the key to ask_flow_insert() -> ask_hw_flow_insert().
         * If the neighbour is not yet resolved, the HW path returns -EAGAIN
         * and the SW path takes the flow until the neighbour completes.
         */
        memcpy(&dst_ip, &key.dst_ip[0], 4);
        ask_resolve_neigh_v4(egress_dev, dst_ip,
                             key.next_hop_mac, key.egress_mac);

        /*
         * PR14j: ingress-only KG port-bind.
         *
         * The REPLACE arrived on @ingress_dev's block (the dpaa netdev
         * that matched the 5-tuple).  Bind KG to THAT netdev's FMan
         * port id, not the egress device.  Idempotent re-bind for the
         * same port is a no-op.  -EBUSY (different port already bound)
         * is logged and ignored - the SW path picks up the flow.
         */
        if (ingress_dev) {
                u8 pid;
                int prc = dpaa_get_fman_port_id(ingress_dev, &pid);

                if (prc == 0) {
                        prc = ask_hw_port_bind(pid);
                        if (prc == -EBUSY)
                                ask_pr_dbg("flow_offload: REPLACE %s: KG already bound to other port, SW fallback\n",
                                           netdev_name(ingress_dev));
                        else if (prc && prc != -ENODEV)
                                ask_pr_warn("flow_offload: REPLACE %s (pid %u) port-bind failed: %d\n",
                                            netdev_name(ingress_dev), pid, prc);
                } else if (prc != -ENODEV && prc != -ERANGE) {
                        ask_pr_dbg("flow_offload: REPLACE dpaa_get_fman_port_id(%s) failed: %d\n",
                                   netdev_name(ingress_dev), prc);
                }
        }

        rc = ask_flow_insert(t, (u64)f->cookie, &key, oif,
                             action_flags, &hw_id);
        if (rc == -EEXIST)
                return 0;
        if (rc) {
                pr_info_ratelimited("ask: flow_offload: REPLACE flow_insert=%d cookie=0x%lx oif=%u nh=%pM em=%pM\n",
                                    rc, f->cookie, oif,
                                    key.next_hop_mac, key.egress_mac);
                return rc;
        }

        /*
         * PR14r (2026-05-17): the previous packed-id tier check
         * (hw_id & 0xffff0000) was a holdover from the pre-PR14j packed
         * (token<<16 | slot) encoding.  Since PR14j the hw_id is an
         * xarray cookie that starts at 1, so this check ALWAYS reported
         * "SW-fallback" — masking the fact that HW offload was actually
         * working.  The authoritative HW-vs-SW signal is the
         * "ask: flow: hw_insert OK ..." vs "ask: flow: hw_insert=-NN
         * (SW-fallback)" pr_info emitted by ask_flow.c at insert time
         * (one log line per cookie, immediately precedes this one).
         * Drop the misleading "tier" string here; keep cookie + hw_id
         * for cross-referencing.
         */
        pr_info_ratelimited("ask: flow_offload: REPLACE installed cookie=0x%lx hw_id=0x%08x ingress=%s oif=%u nh=%pM em=%pM\n",
                            f->cookie, hw_id,
                            ingress_dev ? netdev_name(ingress_dev) : "?", oif,
                            key.next_hop_mac, key.egress_mac);
        return 0;
}

static int ask_flow_offload_destroy(struct flow_cls_offload *f)
{
        struct ask_flow_table *t = ask_flow_default_table();
        int rc;

        if (!t)
                return -EOPNOTSUPP;

        rc = ask_flow_remove(t, (u64)f->cookie);
        if (rc == -ENOENT)
                return 0;
        if (rc)
                return rc;

        ask_pr_dbg("flow_offload: DESTROY cookie=0x%lx\n", f->cookie);
        return 0;
}

static int ask_flow_offload_stats(struct flow_cls_offload *f)
{
        struct ask_flow_table *t = ask_flow_default_table();
        u64 packets = 0, bytes = 0, last_seen_ns = 0;
        int rc;

        if (!t)
                return -EOPNOTSUPP;

        rc = ask_flow_get_stats(t, (u64)f->cookie,
                                &packets, &bytes, &last_seen_ns);
        if (rc)
                return rc;

        flow_stats_update(&f->stats, bytes, packets, 0, last_seen_ns,
                          FLOW_ACTION_HW_STATS_DELAYED);
        return 0;
}

/*
 * The single flow_block_cb consumed by both nf_flow_table and tc-flower.
 *
 * PR14o instrumentation: emit a ratelimited pr_info on first entry per
 * netdev so a production build with no dyndbg still proves whether our
 * cb is being invoked at all. The M2 verification on 2026-05-17 showed
 * BIND events firing but no REPLACE events — this trace is what we need
 * to confirm whether nf_flow_table_offload reaches us or silently aborts
 * in nf_flow_offload_alloc().
 */
int ask_flow_offload_setup_tc_block_cb(enum tc_setup_type type, void *type_data,
                                       void *cb_priv)
{
        struct flow_cls_offload *f = type_data;
        struct net_device *dev = ask_flow_block_priv_dev(cb_priv);

        if (type != TC_SETUP_CLSFLOWER) {
                pr_warn_ratelimited("ask: flow_offload: unexpected tc_setup_type=%u (expected CLSFLOWER)\n",
                                    type);
                return -EOPNOTSUPP;
        }

        switch (f->command) {
        case FLOW_CLS_REPLACE:
                pr_info_ratelimited("ask: flow_offload: cb invoked REPLACE cookie=0x%lx dev=%s\n",
                                    f->cookie,
                                    dev ? netdev_name(dev) : "?");
                return ask_flow_offload_replace(dev, f);
        case FLOW_CLS_DESTROY:
                pr_info_ratelimited("ask: flow_offload: cb invoked DESTROY cookie=0x%lx\n",
                                    f->cookie);
                return ask_flow_offload_destroy(f);
        case FLOW_CLS_STATS:
                /* STATS fires once per refresh interval per flow; keep at
                 * dbg level to avoid spam. */
                return ask_flow_offload_stats(f);
        default:
                pr_warn_ratelimited("ask: flow_offload: unexpected flow_cls command=%u\n",
                                    f->command);
                return -EOPNOTSUPP;
        }
}
EXPORT_SYMBOL_GPL(ask_flow_offload_setup_tc_block_cb);

/* ------------------------------------------------------------------------- */
/* Public block-bind helper                                                   */
/*                                                                            */
/* PR14j change: no longer calls ask_hw_port_bind() here.  We register the    */
/* block_cb for every netdev (so we'll see REPLACE / DESTROY / STATS) but     */
/* defer the silicon port-bind to ask_flow_offload_replace(), which knows    */
/* the actual ingress direction.                                             */
/*                                                                            */
/* PR14n note: kernel 6.18 nft flowtable offload (nf_flow_table_offload.c)   */
/* sets bo->binder_type = FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS — there is   */
/* no FLOW_BLOCK_BINDER_TYPE_FT enumerator in this kernel (the value was     */
/* never landed upstream by the time of 6.18).  Both tc-flower and nft       */
/* flowtable therefore arrive with binder_type==CLSACT_INGRESS, so a single  */
/* equality check suffices.  The real PR14n fix is the flow_indr_dev         */
/* registration below — that's what unblocks the nft-flowtable bind path,   */
/* not a binder-type widening.                                                */
/* ------------------------------------------------------------------------- */

static LIST_HEAD(ask_flow_block_cb_list);

int ask_flow_offload_setup_tc(struct net_device *dev,
                              struct flow_block_offload *fbo)
{
        struct ask_flow_block_priv_entry *e;
        struct flow_block_cb *block_cb;

        if (fbo->binder_type != FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS)
                return -EOPNOTSUPP;

        fbo->driver_block_list = &ask_flow_block_cb_list;

        switch (fbo->command) {
        case FLOW_BLOCK_BIND:
                e = ask_flow_block_priv_alloc(dev);
                if (!e)
                        return -ENOMEM;

                block_cb = flow_block_cb_alloc(
                        ask_flow_offload_setup_tc_block_cb,
                        &e->priv, &e->priv,
                        ask_flow_block_priv_free);
                if (IS_ERR(block_cb)) {
                        ask_flow_block_priv_free(&e->priv);
                        return PTR_ERR(block_cb);
                }
                flow_block_cb_add(block_cb, fbo);
                list_add_tail(&block_cb->driver_list,
                              &ask_flow_block_cb_list);

                pr_info_ratelimited("ask: flow_offload: BIND %s (dir=%d; PR14j defers KG bind to REPLACE)\n",
                                    netdev_name(dev),
                                    ask_flow_offload_classify_dir(dev));
                return 0;

        case FLOW_BLOCK_UNBIND:
                block_cb = flow_block_cb_lookup(
                        fbo->block,
                        ask_flow_offload_setup_tc_block_cb,
                        NULL);
                if (!block_cb)
                        return -ENOENT;
                flow_block_cb_remove(block_cb, fbo);
                list_del(&block_cb->driver_list);
                ask_pr_dbg("flow_offload: UNBIND %s\n", netdev_name(dev));
                return 0;

        default:
                return -EOPNOTSUPP;
        }
}
EXPORT_SYMBOL_GPL(ask_flow_offload_setup_tc);

/* ------------------------------------------------------------------------- */
/* dpaa_flow_offload_ops backend registration                                 */
/* ------------------------------------------------------------------------- */

static int ask_dpaa_setup_tc_block(struct net_device *dev,
                                   struct flow_block_offload *fbo)
{
        return ask_flow_offload_setup_tc(dev, fbo);
}

static const struct dpaa_flow_offload_ops ask_dpaa_fo_ops = {
        .owner          = THIS_MODULE,
        .setup_tc_block = ask_dpaa_setup_tc_block,
};

/* ------------------------------------------------------------------------- */
/* PR14n: flow_indr_dev callback for nft-flowtable bind                       */
/*                                                                            */
/* nft flowtables with `flags offload` deliver their FLOW_BLOCK_BIND through  */
/* the flow_indr_dev_register() path (see net/core/flow_offload.c             */
/* flow_indr_dev_setup_offload()), NOT through ndo_setup_tc().  Without a    */
/* registered indr callback ask.ko never sees the bind and the kernel falls  */
/* back to the SW flowtable fast path (~6 Gbps / 60% CPU on LS1046A).        */
/*                                                                            */
/* The indr core invokes our callback once per (netdev, flowtable) pair when */
/* the userspace `nft flow add` -> nf_flow_table_offload_setup() machinery   */
/* arrives.  We accept any dpaa netdev (i.e. one whose ->dev.parent->driver  */
/* matches our existing dpaa_flow_offload_ops registration target) and       */
/* dispatch TC_SETUP_BLOCK + TC_SETUP_FT to the same                          */
/* ask_flow_offload_setup_tc() helper used by the dpaa_eth ndo path.  The    */
/* binder-type widening for FT was done above (PR14n change to               */
/* ask_flow_offload_setup_tc).                                                */
/*                                                                            */
/* Filtering by netdev: rather than maintaining a separate "is this our      */
/* netdev" predicate (the bnxt / mlx5 / nfp drivers each carry a private one */
/* keyed on their tunnel-device type), we rely on the dpaa backend rejecting */
/* unknown netdevs via dpaa_register_flow_offload_handler — the same        */
/* indirection already used by the ndo path.  In practice the indr core      */
/* only reaches us when nft has matched a device whose flowtable is bound,   */
/* so the check is rarely exercised; when it is, ask_flow_offload_setup_tc  */
/* will silently no-op on a non-dpaa netdev because the per-block priv       */
/* allocation succeeds but the block_cb's REPLACE handler then fails to     */
/* resolve dpaa_get_fman_port_id() and the flow stays SW-only (same          */
/* graceful degradation as PR14j ingress-only bind).                          */
/* ------------------------------------------------------------------------- */

static int ask_flow_indr_setup_block_cb(struct net_device *dev,
                                        struct flow_block_offload *fbo,
                                        void (*cleanup)(struct flow_block_cb *block_cb))
{
        /*
         * The indr path supplies its own cleanup() that the flow_block
         * core expects us to wire into the allocated block_cb.  Our
         * existing ask_flow_offload_setup_tc() path uses
         * flow_block_cb_alloc() (not flow_indr_block_cb_alloc()) and
         * does not pre-register a cleanup hook, so we mirror that here:
         * the per-flow allocation lifetime is owned by the indr core's
         * block-release path, which calls cleanup() against our block_cb
         * once the upper layer unbinds.  ask_flow_block_priv_free()
         * (registered as the cb_priv release in flow_block_cb_alloc)
         * takes care of the per-block priv struct.
         */
        (void)cleanup;
        return ask_flow_offload_setup_tc(dev, fbo);
}

static int ask_flow_indr_setup_cb(struct net_device *dev, struct Qdisc *sch,
                                  void *cb_priv, enum tc_setup_type type,
                                  void *type_data, void *data,
                                  void (*cleanup)(struct flow_block_cb *block_cb))
{
        if (!dev)
                return -EOPNOTSUPP;

        switch (type) {
        case TC_SETUP_BLOCK:
        case TC_SETUP_FT:
                return ask_flow_indr_setup_block_cb(dev, type_data, cleanup);
        default:
                return -EOPNOTSUPP;
        }
}

/* ------------------------------------------------------------------------- */
/* Lifecycle                                                                  */
/* ------------------------------------------------------------------------- */

int ask_flow_offload_init(void)
{
        int rc;

        rc = dpaa_register_flow_offload_handler(&ask_dpaa_fo_ops);
        if (rc == -ENODEV) {
                ask_pr_info("flow_offload: dpaa backend unavailable (-ENODEV); running standalone\n");
                /*
                 * Still register the indr callback so a non-DPAA host with
                 * a kunit synthetic netdev (or a future DPAA2 host where
                 * the ndo path lives elsewhere) can still drive
                 * FLOW_CLS_REPLACE through the indr path.
                 */
        } else if (rc) {
                ask_pr_err("flow_offload: dpaa_register_flow_offload_handler failed: %d\n",
                           rc);
                return rc;
        }

        rc = flow_indr_dev_register(ask_flow_indr_setup_cb, NULL);
        if (rc) {
                ask_pr_err("flow_offload: flow_indr_dev_register failed: %d\n", rc);
                dpaa_unregister_flow_offload_handler(&ask_dpaa_fo_ops);
                return rc;
        }

        ask_pr_info("flow_offload: ready (PR14o: dpaa_eth ndo + indr nft-flowtable bind, cb-trace pr_info)\n");
        return 0;
}

static void ask_flow_indr_release(void *cb_priv)
{
        /* No per-cb state; the cb_priv passed at register-time was NULL. */
        (void)cb_priv;
}

void ask_flow_offload_exit(void)
{
        struct ask_flow_block_priv_entry *e, *tmp;
        int rc;

        flow_indr_dev_unregister(ask_flow_indr_setup_cb, NULL,
                                 ask_flow_indr_release);

        rc = dpaa_unregister_flow_offload_handler(&ask_dpaa_fo_ops);
        if (rc && rc != -ENODEV && rc != -EINVAL)
                ask_pr_warn("flow_offload: dpaa_unregister_flow_offload_handler failed: %d\n",
                            rc);

        spin_lock(&ask_flow_block_priv_lock);
        list_for_each_entry_safe(e, tmp, &ask_flow_block_priv_list, node) {
                list_del(&e->node);
                kfree(e);
        }
        spin_unlock(&ask_flow_block_priv_lock);

        ask_pr_dbg("flow_offload: exit\n");
}
