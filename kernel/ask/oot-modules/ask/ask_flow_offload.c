// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - flow_offload subsystem (PR8 / M1.4 + PR14j ingress-only bind).
 *
 * Implements the `flow_block_cb` dispatcher that the in-tree dpaa
 * patch (PR11/M2.2 - `0002-dpaa-eth-flow-block.patch`) plugs into the
 * `dpaa_setup_tc()` path.
 *
 * Ingress-vs-egress binding: the port-bind decision is deferred from
 * FLOW_BLOCK_BIND to FLOW_CLS_REPLACE.  At REPLACE time the rule carries
 * a REDIRECT/MIRRED action with the egress target, so the netdev whose
 * block received the REPLACE is unambiguously the INGRESS netdev (the one
 * that matched the 5-tuple); egress-only netdevs never receive a REPLACE
 * for which they are the source, so they never consume the single-port-
 * per-scheme KGSE_MV slot.  FLOW_BLOCK_BIND only installs the block_cb
 * (so we see the REPLACE events); it does not touch silicon.
 * (Previously a FIRST-BIND scheme bound the wrong netdev - see git log.)
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
 * Spec ref: ask2-rewrite-spec.md §4.3 (flow_block_cb integration),
 * §11.1 (M2 perf gate).
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
#include <linux/if_vlan.h>
#include <linux/etherdevice.h>
#include <linux/workqueue.h>
#include <linux/jiffies.h>
#include <net/flow_offload.h>
#include <net/pkt_cls.h>
#include <net/arp.h>
#include <net/ndisc.h>
#include <net/neighbour.h>
#include <net/netevent.h>
#include <net/net_namespace.h>
#include <net/netfilter/nf_flow_table.h>
#include "include/ask_internal.h"
#include <linux/fs.h>
#include <linux/file.h>
#include <linux/fsl/dpaa_flow_offload.h>
#include <linux/fsl/fman_pcd.h>       /* F-109: fman_pcd_fe_flow_add/del, fman_pcd_fe_flow_action */

/*
 * Single-image OOT re-declares (board patches 0121 + 0104).
 *
 * The retired ask-flavor entry mechanism (<linux/fsl/dpaa_flow_offload.h> +
 * struct dpaa_flow_offload_ops + dpaa_register/unregister_flow_offload_handler)
 * does not exist on the common board substrate.  ask.ko now enters solely via
 * the mainline flow_indr_dev_register() path and derives a netdev's ingress
 * BMI port id from the board-exported resolvers instead of the retired
 * dpaa_get_fman_port_id():
 *   dpaa_get_rx_fman_port(dev) -> ingress RX struct fman_port   (board 0121)
 *   fman_port_get_id(port)     -> BMI hard port id              (board 0104)
 * ask_dpaa_get_fman_port_id() keeps the old (dev, *pid) -> 0/err call shape
 * the REPLACE/UNBIND handlers below were written against (NULL/non-dpaa dev
 * -> -ENODEV, which the handlers treat as the graceful SW-only fallback).
 */
struct fman_port;
struct fman_port *dpaa_get_rx_fman_port(struct net_device *dev);
u8 fman_port_get_id(struct fman_port *port);

static inline int ask_dpaa_get_fman_port_id(struct net_device *dev, u8 *pid)
{
        struct fman_port *port;

        /*
         * T-M6-8: a routed flow whose ingress/egress is an 802.1Q VLAN
         * sub-interface (eth3.100) resolves against the VIF netdev, but the
         * FMan classifier and BMI port live on the PHYSICAL lower device. The
         * VID and the pop/push intent come from the flow's VLAN match/actions
         * (F-233), NOT from the netdev identity, so it is always correct to
         * resolve the physical FMan port from the vif's real device here.
         * dpaa_get_rx_fman_port() returns NULL for a vif, which previously left
         * VLAN-routed flows unresolved (stuck pending -> SW fallback).
         */
        if (is_vlan_dev(dev))
                dev = vlan_dev_real_dev(dev);

        port = dpaa_get_rx_fman_port(dev);
        if (!port)
                return -ENODEV;
        *pid = fman_port_get_id(port);
        return 0;
}

/*
 * PR14z17: file-scope first-arrival latch (hoisted from function-scope
 * static in PR14z5).  The FLOW_BLOCK_UNBIND handler resets this back
 * to 0xff between nft flowtable load cycles so a second `nft -f` run
 * does not see the previous run's pipeline assignment.  cmpxchg on a
 * `u8` is supported on arm64 via __cmpxchg_small (kernel 6.18).
 */
static u8 ask_flow_first_pid = 0xff;

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

/*
 * T-M6-1: IPv6 counterpart of ask_resolve_neigh_v4().  nd_tbl keys are full
 * struct in6_addr values; accept the same NUD_VALID states because n->ha is
 * meaningful for REACHABLE/STALE/DELAY/PROBE/PERMANENT/NOARP.  The egress MAC
 * is the netdev's own source MAC; next-hop MAC comes from IPv6 NDISC.
 *
 * First IPv6 HW-HIT iteration intentionally does synchronous lookup only. If
 * unresolved, the existing preflight returns -EAGAIN and the kernel software
 * path carries the flow; ask_neigh.c already observes nd_tbl events, but the
 * legacy pending queue is v4/__be32-specific and will be widened only after
 * the direct stable-neighbour path is silicon-proven.
 */
static void ask_resolve_neigh_v6(struct net_device *egress_dev,
                                 const struct in6_addr *dst_ip,
                                 u8 *out_next_hop_mac,
                                 u8 *out_egress_mac)
{
        struct neighbour *n;

        memset(out_next_hop_mac, 0, ETH_ALEN);
        memset(out_egress_mac,   0, ETH_ALEN);

        if (!egress_dev || !dst_ip)
                return;

        memcpy(out_egress_mac, egress_dev->dev_addr, ETH_ALEN);

        n = neigh_lookup(&nd_tbl, dst_ip, egress_dev);
        if (!n)
                return;

        read_lock_bh(&n->lock);
        if (n->nud_state & NUD_VALID)
                memcpy(out_next_hop_mac, n->ha, ETH_ALEN);
        read_unlock_bh(&n->lock);

        neigh_release(n);
}

/* ------------------------------------------------------------------------- */
/* PR14y: deferred-insert pending queue + NETEVENT_NEIGH_UPDATE notifier.    */
/*                                                                            */
/* The kernel delivers FLOW_CLS_REPLACE for new flows BEFORE the next-hop    */
/* ARP/ND has resolved.  PR14y catches the unresolved REPLACE, parks the    */
/* cookie in a pending list, and replays the HW insert from the netevent     */
/* notifier the moment the neigh transitions to a NUD_VALID state.  Until   */
/* the retry succeeds the kernel SW flowtable carries the flow (the         */
/* per-flow forwarding decision still works in software, just at higher     */
/* CPU cost).                                                                */
/*                                                                            */
/* Bounded by ASK_FLOW_PENDING_MAX (see the note on that define for the      */
/* current value and how it was sized).  Beyond the cap we drop new          */
/* deferrals on the floor (counter pr_info'd, ratelimited) so a pathological */
/* burst can't pin unbounded memory.                                         */
/*                                                                            */
/* Lifetime: entries live until (a) neigh resolves and HW insert is replayed,*/
/* (b) FLOW_CLS_DESTROY arrives for the same cookie before resolution, or   */
/* (c) module unload.                                                        */
/* ------------------------------------------------------------------------- */

/*
 * PR14z2 (2026-05-18): raised from 256 → 4096 after M2 measurement on
 * 2026-05-18 captured `PR14y queue full (-28)` overflow within the
 * first 100 ms of iperf3 `-P 8` cold-start. With 8 parallel TCP
 * streams the kernel's nft flowtable emits ~16 FLOW_CLS_REPLACE
 * within the first millisecond (forward + reverse direction per
 * stream), and each one whose neigh is still NUD_INCOMPLETE lands
 * here. 256 was sized for steady-state churn, not for a thundering-
 * herd ARP-warmup. 4096 covers a /24 ARP table worst-case, costs
 * ~256 KB peak when fully populated (struct ask_flow_pending is ~64 B
 * with cacheline padding), and is drained the moment each neigh
 * resolves — so the steady-state count under normal traffic remains
 * effectively zero.
 *
 * If even 4096 overflows, that is a legitimate "router is being DoS'd
 * by SYN flood at unresolvable next-hops" condition and the SW path
 * (which still carries the flow) is the correct backstop.
 */
#define ASK_FLOW_PENDING_MAX 4096

struct ask_flow_pending {
        struct list_head        node;
        u64                     cookie;
        struct ask_flow_key     key;
        u32                     oif;
        u32                     action_flags;
        int                     egress_ifindex;
        /*
         * PR14z10 (2026-05-19): ingress-side ifindex captured at
         * defer time. M2 telemetry (2026-05-19, PR14z9 build) showed
         * PR14z9 poll ticking at 100 ms for the full 30 s test with
         * scanned=227 resolved=0 pending=227 — i.e. the pending list
         * never drained even though the next-hop ARP had long since
         * resolved. Root cause: when nft flowtable emits the REPLACE
         * for the REV direction (eth4 -> eth3 return traffic) the
         * REDIRECT action's act->dev is eth3 (the egress for the
         * reply path), so egress_ifindex captured eth3's ifindex.
         * But the kernel's neigh lookup for the dst_ip in the rule's
         * key happens against `egress_dev` (eth3) and the dst_ip
         * itself is 10.99.1.2 (the lxc201 side that lives on eth3),
         * so neigh resolution should work — yet poll's repeated
         * dev_get_by_index_rcu(egress_ifindex=5) + neigh_lookup
         * never returned NUD_VALID, while NETEVENT fired with
         * dev->ifindex=6 (eth4) for the FORWARD direction's
         * 10.11.1.2 -> eth4 ARP that resolved fine on its own.
         *
         * The asymmetry: nft flowtable delivers the SAME REPLACE
         * cookie to BOTH block_cbs (the dedup at PR14r line 687
         * suppresses the second silicon insert, but the FIRST-
         * arrival can be EITHER block depending on registration
         * order). For an eth3->eth4 forward stream the first-
         * arrival block can be eth4 (block-cb registered on eth4
         * sees the REPLACE first), in which case ingress_dev=eth4
         * but act->dev=eth4 too (PR14z6 echo filter triggers and
         * we return early) -- OR ingress_dev=eth3, act->dev=eth4,
         * defer with egress_ifindex=6. The REV direction symmetric
         * case: ingress_dev=eth4, act->dev=eth3, defer with
         * egress_ifindex=5. But the dst_ip stored in the defer
         * entry comes from the rule's key, which the kernel
         * computed from the conntrack tuple — and for some REV
         * cookies that key's dst_ip is the ORIGINAL direction's
         * dst_ip (the L3 destination of the FORWARD packet that
         * created the conntrack entry, 10.11.1.2), not the reply's
         * dst_ip (10.99.1.2). That dst_ip is only resolvable on
         * eth4, not on the stored egress_ifindex=eth3.
         *
         * Mitigation: also store the ingress_dev's ifindex at defer
         * time, and in BOTH the poll and the netevent fast-path
         * match against EITHER ifindex. Whichever device the kernel
         * actually resolves the neighbour on is the one we use to
         * pick up n->ha. The "wrong" pipeline tagging (FWD vs REV)
         * is harmless because the PR14z5 first_pid latch already
         * decided that at synchronous REPLACE time -- by the time
         * the deferred replay lands, the silicon classifier is
         * armed and the 5-tuple match still wins regardless of
         * which cc_tree the cookie's CC slot lives in.
         */
        int                     ingress_ifindex;
        __be32                  dst_ip;
        unsigned long           jiffies_inserted;
        u32                     generation; /* A3: owner stamp for replay guard */
};

static LIST_HEAD(ask_flow_pending_list);
static DEFINE_SPINLOCK(ask_flow_pending_lock);
static unsigned int ask_flow_pending_count;
static atomic_t ask_flow_pending_deferred = ATOMIC_INIT(0);
static atomic_t ask_flow_pending_resolved = ATOMIC_INIT(0);
static atomic_t ask_flow_pending_overflow = ATOMIC_INIT(0);

/*
 * PR14z10: match on EITHER egress_ifindex OR ingress_ifindex. The
 * netevent fires with dev = the actual device the neigh resolved on,
 * which can be either end of the flow depending on which direction's
 * dst_ip we stored in the defer entry.
 */
static struct ask_flow_pending *
ask_flow_pending_take_one(int ifindex, int alt_ifindex, __be32 dst_ip)
{
        struct ask_flow_pending *p, *tmp;
        struct ask_flow_pending *ret = NULL;

        spin_lock_bh(&ask_flow_pending_lock);
        list_for_each_entry_safe(p, tmp, &ask_flow_pending_list, node) {
                /* T-M6-8: VLAN neighbour events arrive on the vif while
                 * pending entries may be keyed on the physical lower device.
                 * Match either form (alt_ifindex==ifindex for non-VLAN). */
                if ((p->egress_ifindex == ifindex ||
                     p->ingress_ifindex == ifindex ||
                     p->egress_ifindex == alt_ifindex ||
                     p->ingress_ifindex == alt_ifindex) &&
                    p->dst_ip == dst_ip) {
                        list_del(&p->node);
                        ask_flow_pending_count--;
                        ret = p;
                        break;
                }
        }
        spin_unlock_bh(&ask_flow_pending_lock);
        return ret;
}

static bool ask_flow_pending_drop_cookie(u64 cookie)
{
        struct ask_flow_pending *p, *tmp;
        bool found = false;

        spin_lock_bh(&ask_flow_pending_lock);
        list_for_each_entry_safe(p, tmp, &ask_flow_pending_list, node) {
                if (p->cookie == cookie) {
                        list_del(&p->node);
                        ask_flow_pending_count--;
                        found = true;
                        kfree(p);
                        /* A3: do not break — drain every legacy duplicate for
                         * this cookie so none can replay after DESTROY. */
                }
        }
        spin_unlock_bh(&ask_flow_pending_lock);
        return found;
}

static int ask_flow_pending_enqueue(u64 cookie,
                                    const struct ask_flow_key *key,
                                    u32 oif, u32 action_flags,
                                    int egress_ifindex,
                                    int ingress_ifindex,
                                    __be32 dst_ip,
                                    u32 generation)
{
        struct ask_flow_pending *p, *iter;

        p = kzalloc(sizeof(*p), GFP_ATOMIC);
        if (!p)
                return -ENOMEM;

        p->cookie           = cookie;
        p->key              = *key;
        p->oif              = oif;
        p->action_flags     = action_flags;
        p->egress_ifindex   = egress_ifindex;
        p->ingress_ifindex  = ingress_ifindex;
        p->dst_ip           = dst_ip;
        p->jiffies_inserted = jiffies;
        p->generation       = generation;

        spin_lock_bh(&ask_flow_pending_lock);
        /*
         * T-M6-A3 (R3): coalesce by cookie. A flapping neighbour or repeated
         * REPLACE retries for the same cookie must not accumulate multiple
         * pending entries — otherwise a DESTROY that drops the first leaves a
         * second that later replays and resurrects the flow. If an entry for
         * this cookie already exists, overwrite it in place with the newest
         * key/generation and keep a single entry per cookie.
         */
        list_for_each_entry(iter, &ask_flow_pending_list, node) {
                if (iter->cookie == cookie) {
                        iter->key              = *key;
                        iter->oif              = oif;
                        iter->action_flags     = action_flags;
                        iter->egress_ifindex   = egress_ifindex;
                        iter->ingress_ifindex  = ingress_ifindex;
                        iter->dst_ip           = dst_ip;
                        iter->jiffies_inserted = jiffies;
                        iter->generation       = generation;
                        spin_unlock_bh(&ask_flow_pending_lock);
                        kfree(p);
                        return 0;
                }
        }
        if (ask_flow_pending_count >= ASK_FLOW_PENDING_MAX) {
                spin_unlock_bh(&ask_flow_pending_lock);
                kfree(p);
                atomic_inc(&ask_flow_pending_overflow);
                return -ENOSPC;
        }
        list_add_tail(&p->node, &ask_flow_pending_list);
        ask_flow_pending_count++;
        spin_unlock_bh(&ask_flow_pending_lock);
        atomic_inc(&ask_flow_pending_deferred);
        return 0;
}

/*
 * ask_flow_neigh_resolved - deferred-insert drain for a resolved next-hop.
 *
 * Called in PROCESS context from ask_neigh.c's workqueue when an ARP neighbour
 * toward @dst_ip on @dev transitions to NUD_VALID.  Drains every pending cookie
 * waiting on (dev->ifindex, dst_ip) and replays its HW insert.
 *
 * MUST run in process context: ask_flow_insert() below allocates GFP_KERNEL.
 * The netevent chain is ATOMIC (net/core/netevent.c ATOMIC_NOTIFIER_HEAD), so
 * ask_neigh defers here via a workqueue rather than calling inline — this also
 * closes the historical PR14z8 "deferred-insert OK=0" gap (the old inline call
 * from the atomic notifier could not complete a GFP_KERNEL insert).
 */
 void ask_flow_neigh_resolved(struct net_device *dev, __be32 dst_ip)
 {
        struct ask_flow_pending *p;
        struct ask_flow_table *t;
        u32 hw_id = 0;
        int rc;
        unsigned int drained = 0;
        int drain_ifindex;

        if (!dev)
                return;

        /*
         * T-M6-8: the ARP neighbour for a VLAN-egress next-hop lives on the
         * 802.1Q vif (e.g. eth3.100), so this callback fires with the vif dev.
         * But pending entries are keyed on the PHYSICAL egress/ingress ifindex
         * (META-corrected at REPLACE), so a vif ifindex would never match and
         * the reverse (PUSH) direction would stay pending forever -> data
         * stall. Drain against the physical lower-device ifindex. Neighbour
         * re-resolution below stays on @dev (the vif) where the ARP entry is.
         */
        drain_ifindex = is_vlan_dev(dev) ? vlan_dev_real_dev(dev)->ifindex
                                         : dev->ifindex;

        pr_info_ratelimited("ask: neigh: resolved dev=%s ifindex=%d (drain_ifindex=%d) dst_ip=%pI4 pending_count=%u\n",
                            netdev_name(dev), dev->ifindex, drain_ifindex, &dst_ip,
                            READ_ONCE(ask_flow_pending_count));

        t = ask_flow_default_table();
        if (!t)
                return;

        /*
         * Drain ALL pending entries waiting on (drain_ifindex, dst_ip) — a
         * single ARP resolution can unblock multiple cookies if several
         * flows are heading at the same next-hop.
         */
        while ((p = ask_flow_pending_take_one(drain_ifindex, dev->ifindex,
                                              dst_ip))) {
                drained++;
                /* Re-resolve so we always pick up the fresh n->ha. */
                ask_resolve_neigh_v4(dev, dst_ip,
                                     p->key.next_hop_mac, p->key.egress_mac);
                /* F-111: Drop pending entries with multicast/broadcast
                 * next-hop — these can never be HW-offloaded. */
                if (is_multicast_ether_addr(p->key.next_hop_mac)) {
                        kfree(p);
                        continue;
                }
                if (is_zero_ether_addr(p->key.next_hop_mac)) {
                        /* Race: neigh went back to INCOMPLETE between
                         * NETEVENT delivery and our re-lookup.  Re-park.
                         */
                        if (ask_flow_pending_enqueue(p->cookie, &p->key,
                                                     p->oif, p->action_flags,
                                                     p->egress_ifindex,
                                                     p->ingress_ifindex,
                                                     p->dst_ip,
                                                     p->generation) == 0)
                                pr_info_ratelimited("ask: flow_offload: PR14y re-park cookie=0x%llx dev=%s\n",
                                                    p->cookie, netdev_name(dev));
                        kfree(p);
                        continue;
                }

                /*
                 * T-M6-A3 (R2): discard a pending entry whose cookie was
                 * destroyed (tombstoned) or superseded by a newer REPLACE
                 * while it waited for the neighbour. Replaying it would
                 * resurrect a flow no conntrack owns.
                 */
                if (p->generation != 0 &&
                    !ask_flow_gen_is_current(t, p->cookie, p->generation)) {
                        pr_info_ratelimited("ask: flow_offload: drop stale pending cookie=0x%llx gen=%u (destroyed/superseded)\n",
                                            p->cookie, p->generation);
                        kfree(p);
                        continue;
                }

                /*
                 * PR14z5: deferred replay uses FWD by default.  The
                 * direction was effectively decided at REPLACE time
                 * when ask_hw_port_bind() ran; by the time we get
                 * here the cookie's ingress pipeline is already
                 * armed.  Sub-optimal if the deferred cookie should
                 * have been REV (its CC slot will then live in the
                 * FWD cc_tree), but functionally correct: the
                 * silicon still classifies on 5-tuple, and the
                 * second-arrival REPLACE for the REV direction is
                 * what flips first_pid into the REV pipeline.
                 *
                 * Cookies that arrive after first_pid is locked in
                 * route correctly via the synchronous REPLACE path
                 * above.  Cookies that are deferred (rare: only
                 * those whose neigh is INCOMPLETE at REPLACE time)
                 * may land in the "wrong" pipeline but still hit
                 * silicon.  PR14z6 (future) can capture the dir at
                 * defer time and replay it here.
                 */
                rc = ask_flow_insert_owned(t, p->cookie, &p->key, p->oif,
                                           p->action_flags, ASK_HW_DIR_FWD,
                                           p->generation, &hw_id);
                if (rc == -EEXIST)
                        rc = 0;
                if (rc == 0) {
                        atomic_inc(&ask_flow_pending_resolved);
                        pr_info_ratelimited("ask: flow_offload: PR14y deferred-insert OK cookie=0x%llx dev=%s hw_id=0x%08x nh=%pM em=%pM\n",
                                            p->cookie, netdev_name(dev),
                                            hw_id, p->key.next_hop_mac,
                                            p->key.egress_mac);
                } else {
                        pr_info_ratelimited("ask: flow_offload: PR14y deferred-insert FAIL rc=%d cookie=0x%llx dev=%s\n",
                                            rc, p->cookie, netdev_name(dev));
                }
                kfree(p);
        }

        (void)drained;
        return;
}
EXPORT_SYMBOL_GPL(ask_flow_neigh_resolved);

/* Collector context for ask_flow_neigh_mac_changed()'s rhashtable walk. */
struct ask_neigh_mac_ctx {
        int             ifindex;    /* egress oif the neigh resolved on */
        const u8        *dst_ip;    /* next-hop L3 address that changed */
        unsigned int    addr_len;   /* 4 (arp_tbl) or 16 (nd_tbl)       */
        u8              l3_proto;   /* ASK_FLOW_L3_IPV4 / _IPV6         */
        const u8        *new_mac;   /* fresh lladdr from n->ha          */
        struct list_head fixups;    /* ask_neigh_mac_fixup, built here  */
        unsigned int    matched;
};

struct ask_neigh_mac_fixup {
        struct list_head    node;
        u64                 cookie;
        struct ask_flow_key key;    /* copy with next_hop_mac already patched */
        u32                 oif;
        u32                 action_flags;
        u8                  dir;
        u32                 generation; /* A3: owner stamp snapshotted at collect */
};

/*
 * Walk callback: collect installed flows egressing to (ifindex, dst_ip) whose
 * baked-in next_hop_mac no longer matches the neighbour's new lladdr.
 * We only COLLECT here (GFP_ATOMIC, no sleeping) — the rebuild happens after
 * the walk so ask_flow_remove()/insert() run outside the rhashtable iterator.
 *
 * Address family comes from the notifier's neigh table (T-M6-1 piece 4): a
 * v4 event must not match a v6 flow whose first 4 dst_ip bytes happen to
 * collide, hence the explicit l3_proto match before the length-aware memcmp.
 */
static int ask_neigh_mac_collect(struct ask_flow *f, void *arg)
{
        struct ask_neigh_mac_ctx *ctx = arg;
        struct ask_neigh_mac_fixup *fx;

        /*
         * hw_backed, not hw_flow_id != 0. A SW-fallback flow carries a
         * non-zero fake id, so the old predicate admitted every flow the HW
         * gate had rejected (IPv6, and any non-TCP/UDP v4 — ask_hw.c's
         * -EOPNOTSUPP arm). Those flows have no silicon state to repair, and
         * rebuilding them called ask_flow_remove() -> ask_hw_flow_remove()
         * with an id that aliases a live xarray cookie, corrupting an
         * unrelated offloaded flow on ordinary neighbour churn.
         */
        if (!f->hw_backed)                          /* no HW backing to fix */
                return 0;
        if (f->key.l3_proto != ctx->l3_proto)
                return 0;
        if (f->oif != (u32)ctx->ifindex)
                return 0;
        if (memcmp(f->key.dst_ip, ctx->dst_ip, ctx->addr_len) != 0)
                return 0;
        if (ether_addr_equal(f->key.next_hop_mac, ctx->new_mac))
                return 0;                           /* MAC unchanged */

        fx = kzalloc(sizeof(*fx), GFP_ATOMIC);
        if (!fx)
                return 0;                           /* best-effort; skip on OOM */
        fx->cookie       = f->cookie;
        fx->key          = f->key;
        ether_addr_copy(fx->key.next_hop_mac, ctx->new_mac);
        fx->oif          = f->oif;
        fx->action_flags = f->action_flags;
        fx->dir          = f->dir;
        fx->generation   = f->generation;
        list_add_tail(&fx->node, &ctx->fixups);
        ctx->matched++;
        return 0;
}

/*
 * ask_flow_neigh_mac_changed - re-point offloaded flows at a next-hop whose
 * MAC changed, killing stale-MAC blackholing (T-M6-3).
 *
 * When a neighbour toward @dst_ip on @dev resolves to a NEW @new_mac, every
 * already-installed flow egressing there still rewrites the OLD MAC in silicon
 * → frames blackhole.  We collect those flows, then rebuild each cookie-stably
 * (remove stale-MAC HW entry + reinsert with the fresh MAC).  During the brief
 * window a rebuilt flow has no HW backing the kernel SW path carries it, so no
 * packet is lost.  Runs in PROCESS context (workqueue) — insert/remove sleep.
 */
void ask_flow_neigh_mac_changed(struct net_device *dev, const u8 *dst_ip,
                                u8 l3_proto, const u8 *new_mac)
{
        struct ask_flow_table *t = ask_flow_default_table();
        struct ask_neigh_mac_fixup *fx, *tmp;
        struct ask_neigh_mac_ctx ctx;
        u32 hw_id = 0;
        int rc;

        if (!t || !dev || !dst_ip || !new_mac)
                return;
        /* A multicast/zero lladdr is never an offloadable next-hop. */
        if (is_zero_ether_addr(new_mac) || is_multicast_ether_addr(new_mac))
                return;

        ctx.ifindex      = dev->ifindex;
        ctx.dst_ip       = dst_ip;
        ctx.l3_proto     = l3_proto;
        ctx.addr_len     = ask_flow_l3_addr_len(l3_proto);
        ctx.new_mac      = new_mac;
        ctx.matched      = 0;
        INIT_LIST_HEAD(&ctx.fixups);

        ask_flow_walk(t, ask_neigh_mac_collect, &ctx);
        if (!ctx.matched)
                return;

        list_for_each_entry_safe(fx, tmp, &ctx.fixups, node) {
                struct ask_flow *cur;
                struct ask_flow_key old_key;
                u32 old_oif;
                u32 old_action_flags;
                u8 old_dir;
                bool snapshot_ok = false;

                rcu_read_lock();
                cur = ask_flow_lookup(t, fx->cookie);
                if (cur) {
                        old_key = cur->key;
                        old_oif = cur->oif;
                        old_action_flags = cur->action_flags;
                        old_dir = cur->dir;
                        snapshot_ok = true;
                }
                rcu_read_unlock();
                if (!snapshot_ok) {
                        list_del(&fx->node);
                        kfree(fx);
                        continue;
                }

                /*
                 * T-M6-A3 (R2/R5): only rebuild if the flow we snapshotted at
                 * collect time is still the current owner. If a DESTROY or a
                 * newer REPLACE intervened, rebuilding from the stale fixup
                 * would resurrect a dead flow or clobber the newer one. The
                 * rebuild reuses the SAME generation (a MAC repoint is not a
                 * new ownership epoch), and remove_owned/insert_owned enforce
                 * the bound so a race during the remove->insert window cannot
                 * revive a concurrently-destroyed cookie.
                 */
                if (!ask_flow_gen_is_current(t, fx->cookie, fx->generation)) {
                        pr_info_ratelimited("ask: neigh: skip stale-MAC rebuild cookie=0x%llx gen=%u (destroyed/superseded)\n",
                                            fx->cookie, fx->generation);
                        list_del(&fx->node);
                        kfree(fx);
                        continue;
                }

                ask_flow_remove_owned(t, fx->cookie, fx->generation);
                rcu_read_lock();
                cur = ask_flow_lookup(t, fx->cookie);
                rcu_read_unlock();
                if (cur) {
                        list_del(&fx->node);
                        kfree(fx);
                        continue;
                }
                /* Re-check ownership after the remove: a DESTROY racing the
                 * remove tombstones the cookie, and insert_owned will then
                 * refuse to republish (returns -ESTALE) — no resurrection. */
                rc = ask_flow_insert_owned(t, fx->cookie, &fx->key, fx->oif,
                                           fx->action_flags, fx->dir,
                                           fx->generation, &hw_id);
                if (rc == -EEXIST)
                        rc = 0;
                if (rc == -ESTALE) {
                        /* cookie destroyed during rebuild; leave it gone */
                        pr_info_ratelimited("ask: neigh: rebuild aborted (destroyed) cookie=0x%llx\n",
                                            fx->cookie);
                } else if (rc) {
                        int restore_rc;

                        restore_rc = ask_flow_insert_owned(t, fx->cookie, &old_key,
                                                           old_oif, old_action_flags,
                                                           old_dir, fx->generation,
                                                           &hw_id);
                        if (restore_rc == -EEXIST || restore_rc == -ESTALE)
                                restore_rc = 0;
                        if (restore_rc)
                                pr_warn_ratelimited("ask: neigh: rebuild rollback failed cookie=0x%llx rc=%d\n",
                                                    fx->cookie, restore_rc);
                }
                if (l3_proto == ASK_FLOW_L3_IPV6)
                        pr_info_ratelimited("ask: neigh: stale-MAC rebuild cookie=0x%llx dev=%s dst_ip=%pI6 nh=%pM rc=%d\n",
                                            fx->cookie, netdev_name(dev), dst_ip,
                                            fx->key.next_hop_mac, rc);
                else
                        pr_info_ratelimited("ask: neigh: stale-MAC rebuild cookie=0x%llx dev=%s dst_ip=%pI4 nh=%pM rc=%d\n",
                                            fx->cookie, netdev_name(dev), dst_ip,
                                            fx->key.next_hop_mac, rc);
                list_del(&fx->node);
                kfree(fx);
        }
}
EXPORT_SYMBOL_GPL(ask_flow_neigh_mac_changed);

/* ------------------------------------------------------------------------- */
/* PR14z9 (2026-05-19): active-poll fallback for pending queue drain.        */
/*                                                                            */
/* PR14y's design relied entirely on NETEVENT_NEIGH_UPDATE firing when our   */
/* deferred next-hop ARP resolves.  Empirically (M2 2026-05-19 dmesg:        */
/* defer=141, deferred-insert OK=0) that notifier never delivers a useful   */
/* transition for entries we ourselves created via __neigh_create() +       */
/* neigh_event_send() — either it doesn't fire at all for solicited-by-us   */
/* probes (H1) or the (ifindex, dst_ip) filter inside the notifier misses   */
/* (H2).  PR14z8 instrumentation will tell us which; PR14z9 makes the       */
/* answer irrelevant by polling the pending list ourselves every 100 ms     */
/* and re-running neigh_lookup() on each entry.                              */
/*                                                                            */
/* Why 100 ms: Linux ARP retransmits every ~1 s, neigh_event_send() kicks   */
/* the first solicit immediately, so reply latency on a healthy LAN is      */
/* typically <2 ms.  100 ms gives us ≥10 chances/sec to catch the resolve  */
/* before the kernel's flowtable sweeper expires the cookie.  On a fully   */
/* idle pending list this poll costs one spinlock acquire + list_empty()   */
/* check = ~50 ns per tick, negligible.                                     */
/*                                                                            */
/* When the list is empty the work re-arms anyway; cheaper than maintaining */
/* a "should I run?" signal between enqueue and poller.                     */
/* ------------------------------------------------------------------------- */

#define ASK_FLOW_PENDING_POLL_INTERVAL_MS 100

static struct delayed_work ask_flow_pending_poll_work;
static atomic_t ask_flow_pending_poll_runs = ATOMIC_INIT(0);
static atomic_t ask_flow_pending_poll_resolved = ATOMIC_INIT(0);

static void ask_flow_pending_poll_fn(struct work_struct *work)
{
        struct ask_flow_pending *p, *tmp;
        struct ask_flow_table *t;
        LIST_HEAD(ready);
        unsigned int scanned = 0;
        unsigned int resolved_this_tick = 0;

        atomic_inc(&ask_flow_pending_poll_runs);

        /* Fast-path bail-out on empty list to avoid log spam. */
        if (list_empty(&ask_flow_pending_list))
                goto rearm;

        t = ask_flow_default_table();
        if (!t)
                goto rearm;

        /*
         * Walk the pending list under the lock and pull out any entry
         * whose neigh has resolved.  We move ready entries to a local
         * list so we can replay the HW insert outside the lock (the
         * insert path takes its own locks / sleeps in PCD CC API).
         */
        spin_lock_bh(&ask_flow_pending_lock);
        list_for_each_entry_safe(p, tmp, &ask_flow_pending_list, node) {
                struct net_device *dev_try;
                struct neighbour *n;
                u32 dst_key = (__force u32)p->dst_ip;
                bool got_mac = false;
                int tried_ifindex[2];
                int n_tries;
                int i;

                scanned++;

                /*
                 * PR14z10: try BOTH ifindices captured at defer time.
                 * The dst_ip stored in the defer entry may be resolvable
                 * on either end of the flow depending on which direction
                 * the kernel chose to encode in this REPLACE's key.
                 */
                tried_ifindex[0] = p->egress_ifindex;
                n_tries = 1;
                if (p->ingress_ifindex && p->ingress_ifindex != p->egress_ifindex)
                        tried_ifindex[n_tries++] = p->ingress_ifindex;

                for (i = 0; i < n_tries && !got_mac; i++) {
                        rcu_read_lock();
                        dev_try = dev_get_by_index_rcu(&init_net,
                                                       tried_ifindex[i]);
                        if (!dev_try) {
                                rcu_read_unlock();
                                continue;
                        }
                        /*
                         * T-M6-8: a reverse VLAN direction that PUSHes a tag
                         * egresses through a VLAN vif (eth3.100), but the
                         * pending entry is keyed on the physical FMan port
                         * (eth3). ARP/ND belongs to the VIF: the physical dev
                         * has a permanent FAILED neigh for the tagged subnet,
                         * so polling it can never resolve and the PUSH record
                         * stays pending forever. Map the physical candidate to
                         * its VLAN upper by the action's TPID/VID, and do ONLY
                         * the neighbour lookup there. FMan port/TX-FQ remain
                         * physical (6faab5aa).
                         */
                        if (p->key.vlan_edit_flags & ASK_VLANF_PUSH) {
                                struct net_device *vlan_dev;
                                u16 vid = ntohs(p->key.vlan_push_tci) &
                                          VLAN_VID_MASK;

                                vlan_dev = __vlan_find_dev_deep_rcu(
                                        dev_try, p->key.vlan_push_tpid, vid);
                                if (vlan_dev)
                                        dev_try = vlan_dev;
                        }
                        n = neigh_lookup(&arp_tbl, &dst_key, dev_try);
                        rcu_read_unlock();
                        if (!n)
                                continue;

                        read_lock_bh(&n->lock);
                        if (n->nud_state & NUD_VALID) {
                                memcpy(p->key.next_hop_mac, n->ha, ETH_ALEN);
                                got_mac = true;
                        }
                        read_unlock_bh(&n->lock);
                        neigh_release(n);
                }

                if (got_mac) {
                        list_del(&p->node);
                        ask_flow_pending_count--;
                        list_add_tail(&p->node, &ready);
                }
        }
        spin_unlock_bh(&ask_flow_pending_lock);

        /* Now replay HW insert for each ready cookie, lock-free. */
        list_for_each_entry_safe(p, tmp, &ready, node) {
                u32 hw_id = 0;
                int rc;

                list_del(&p->node);
                /* A3 (R2): skip cookies destroyed/superseded while parked. */
                if (p->generation != 0 &&
                    !ask_flow_gen_is_current(t, p->cookie, p->generation)) {
                        pr_info_ratelimited("ask: flow_offload: drop stale pending cookie=0x%llx gen=%u (poll, destroyed/superseded)\n",
                                            p->cookie, p->generation);
                        kfree(p);
                        continue;
                }
                rc = ask_flow_insert_owned(t, p->cookie, &p->key, p->oif,
                                           p->action_flags, ASK_HW_DIR_FWD,
                                           p->generation, &hw_id);
                if (rc == -EEXIST)
                        rc = 0;
                if (rc == 0) {
                        atomic_inc(&ask_flow_pending_resolved);
                        atomic_inc(&ask_flow_pending_poll_resolved);
                        resolved_this_tick++;
                        pr_info_ratelimited("ask: flow_offload: PR14z10 poll-resolved cookie=0x%llx eg_if=%d in_if=%d hw_id=0x%08x nh=%pM em=%pM\n",
                                            p->cookie, p->egress_ifindex,
                                            p->ingress_ifindex,
                                            hw_id, p->key.next_hop_mac,
                                            p->key.egress_mac);
                } else if (rc == -ESTALE) {
                        /* superseded between the gen check and publish; benign */
                } else {
                        pr_info_ratelimited("ask: flow_offload: PR14z10 poll-insert FAIL rc=%d cookie=0x%llx eg_if=%d in_if=%d\n",
                                            rc, p->cookie, p->egress_ifindex,
                                            p->ingress_ifindex);
                }
                kfree(p);
        }

        if (resolved_this_tick || scanned > 0)
                pr_info_ratelimited("ask: flow_offload: PR14z9 poll tick scanned=%u resolved=%u pending=%u\n",
                                    scanned, resolved_this_tick,
                                    READ_ONCE(ask_flow_pending_count));

rearm:
        schedule_delayed_work(&ask_flow_pending_poll_work,
                              msecs_to_jiffies(ASK_FLOW_PENDING_POLL_INTERVAL_MS));
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

/*
 * T-M6-1 (IPv6, piece 1): parse an IPv6 5-tuple flow_cls match into the shared
 * ask_flow_key.  Mirror of ask_parse_match_v4() but copies the full 16-byte v6
 * addresses (the key already reserves src_ip[16]/dst_ip[16]).  Until the v6 HW
 * path (v6 EKFC KeyGen scheme + separate v6 ehash table) lands, ask_hw.c's
 * insert gate returns -EOPNOTSUPP for l3_proto==IPV6, so parsed v6 flows fall
 * back to the kernel SW path (correct, no blackhole) and are visible in
 * `show flows`.
 */
static int ask_parse_match_v6(struct flow_cls_offload *f,
                              struct ask_flow_key *key)
{
        struct flow_rule *rule = flow_cls_offload_flow_rule(f);

        memset(key, 0, sizeof(*key));
        key->l3_proto = ASK_FLOW_L3_IPV6;

        if (flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_BASIC)) {
                struct flow_match_basic m;

                flow_rule_match_basic(rule, &m);
                if (m.key->n_proto != htons(ETH_P_IPV6))
                        return -EOPNOTSUPP;
                key->l4_proto = m.key->ip_proto;
        } else {
                return -EOPNOTSUPP;
        }

        if (flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_IPV6_ADDRS)) {
                struct flow_match_ipv6_addrs m;

                flow_rule_match_ipv6_addrs(rule, &m);
                memcpy(&key->src_ip[0], &m.key->src, 16);
                memcpy(&key->dst_ip[0], &m.key->dst, 16);
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

        return 0;
}

/*
 * Dispatch on the match's EtherType: IPv6 → ask_parse_match_v6, else v4.
 */
static int ask_parse_match(struct flow_cls_offload *f,
                           struct ask_flow_key *key)
{
        struct flow_rule *rule = flow_cls_offload_flow_rule(f);
        struct flow_match_basic m;

        if (!flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_BASIC))
                return -EOPNOTSUPP;
        flow_rule_match_basic(rule, &m);
        if (m.key->n_proto == htons(ETH_P_IPV6))
                return ask_parse_match_v6(f, key);
        return ask_parse_match_v4(f, key);
}

/* ------------------------------------------------------------------------- */
/* Canonical intent lowering (T-M6-A1)                                        */
/*                                                                            */
/* Single translation point from the typed ask_flow_intent to the legacy      */
/* (oif, action_flags) pair the insert/pending/neigh paths still consume.     */
/* For the plain IPv4-unicast flow this MUST reproduce the exact pre-A1       */
/* values (oif = egress ifindex, action_flags = 0) so the stored ask_flow     */
/* and the FE record stay byte-for-byte identical. When NAT/VLAN land, the    */
/* future FE action compiler replaces this body; call sites do not change.    */
/* ------------------------------------------------------------------------- */
int ask_intent_lower(const struct ask_flow_intent *in,
                     u32 *out_oif, u32 *out_action_flags)
{
        u32 oif = 0;
        u32 flags = 0;
        u8 i;

        for (i = 0; i < in->n_actions; i++) {
                switch (in->actions[i].type) {
                case ASK_ACTION_REDIRECT:
                        oif = in->actions[i].oif;
                        break;
                case ASK_ACTION_L2_REWRITE:
                        /* Performed by the FE-VM INSERT_L2_HDR opcode from the
                         * resolved next-hop MAC in the key; no legacy flag. */
                        break;
                case ASK_ACTION_TTL_DEC:
                        /* The FE opcode chain always decrements TTL for a
                         * routed flow; it is implicit in the record and,
                         * pre-A1, was never encoded in action_flags. Keep it
                         * out of the legacy flags to preserve byte-identity. */
                        break;
                case ASK_ACTION_NAT_SRC:
                case ASK_ACTION_NAT_DST:
                case ASK_ACTION_NAPT_SPORT:
                case ASK_ACTION_NAPT_DPORT:
                        /* T-M6-7.1: NAT/PAT. Admitted to the legacy flags only
                         * when the family's NAT gate is enabled (default on); otherwise fail
                         * closed to software (default shipping behaviour). The
                         * actual rewrite values ride the flow key
                         * (key->nat_*), stashed by ask_parse_action; the
                         * preflight re-checks the gate + eth0 exclusion, and
                         * ask_fe_flow_insert only fills action.nat_* when
                         * armed, keeping the F-230 emitter dormant otherwise. */
                        if (in->match->l3_proto == ASK_FLOW_L3_IPV4) {
                                if (!ask_hw_nat44_offload_armed())
                                        return -EOPNOTSUPP;
                        } else if (in->match->l3_proto == ASK_FLOW_L3_IPV6) {
                                if (!ask_hw_nat66_offload_armed())
                                        return -EOPNOTSUPP;
                        } else {
                                return -EOPNOTSUPP;
                        }
			if (in->actions[i].type == ASK_ACTION_NAT_SRC)
				flags |= ASK_ACT_NAT_SRC;
			else if (in->actions[i].type == ASK_ACTION_NAT_DST)
				flags |= ASK_ACT_NAT_DST;
			else
				flags |= ASK_ACT_PAT;
			break;
		case ASK_ACTION_VLAN_POP:
		case ASK_ACTION_VLAN_PUSH:
			/* T-M6-8: admitted to the legacy flags only when the VLAN
			 * gate is armed (default OFF); otherwise fail closed to
			 * software. Values ride key->vlan_*; preflight re-checks the
			 * gate + eth0 exclusion, and ask_fe_flow_insert fills
			 * action.vlan_* only when armed, keeping the F-233
			 * emitter dormant otherwise. */
			if (!ask_hw_vlan_offload_armed())
				return -EOPNOTSUPP;
			flags |= (in->actions[i].type == ASK_ACTION_VLAN_POP)
					? ASK_ACT_VLAN_POP : ASK_ACT_VLAN_PUSH;
			break;
		default:
			return -EOPNOTSUPP;
		}
	}

        if (oif == 0)
                return -EOPNOTSUPP;

        *out_oif = oif;
        *out_action_flags = flags;
        return 0;
}

/* ------------------------------------------------------------------------- */
/* Action parsing                                                             */
/*                                                                            */
/* T-M6-A1: builds the canonical ask_flow_intent (match + typed actions), then*/
/* lowers it to (oif, action_flags). Also returns the egress net_device *     */
/* (act->dev) so the caller can run neigh_lookup() and fill                    */
/* key->next_hop_mac / egress_mac. The pointer is borrowed from the rule and  */
/* is RCU-protected; caller must use it before returning from the             */
/* FLOW_CLS_REPLACE handler.                                                   */
/* ------------------------------------------------------------------------- */

static int ask_parse_action(struct flow_cls_offload *f,
                            struct ask_flow_key *key,
                            u32 *out_action_flags, u32 *out_oif,
                            struct net_device **out_egress_dev)
{
        struct flow_rule *rule = flow_cls_offload_flow_rule(f);
        struct flow_action_entry *act;
        struct net_device *egress = NULL;
        struct ask_flow_intent intent = {
                .match = key,
                .owner = (u64)f->cookie,
                .generation = 0, /* A3 assigns real generations */
        };
        /*
         * T-M6-7.0: accumulate IPv6 NAT addresses across the 4x 32-bit mangle
         * chunks the kernel emits (one flow_action per 32-bit word) before
         * adding a single typed action.
         */
	u8  v6_snat[16] = {0}, v6_dnat[16] = {0};
	u32 v6_snat_seen = 0, v6_dnat_seen = 0;
	bool seen_l3_nat = false;
	/*
	 * T-M6-8: single-tag depth guard. The kernel flowtable can emit up to
	 * NF_FLOW_TABLE_ENCAP_MAX=2 VLAN_POP/PUSH (QinQ); this release supports
	 * exactly one tag per direction, so a second pop or push fails closed.
	 */
	u8  vlan_pop_seen = 0, vlan_push_seen = 0;
	u32 oif = 0;
	int i, rc;

        flow_action_for_each(i, act, &rule->action) {
                switch (act->id) {
                case FLOW_ACTION_REDIRECT:
                case FLOW_ACTION_MIRRED:
                        if (!act->dev)
                                return -EOPNOTSUPP;
                        oif = act->dev->ifindex;
                        egress = act->dev;
                        rc = ask_intent_add(&intent, ASK_ACTION_REDIRECT, oif);
                        if (rc)
                                return rc;
                        /* Every routed flow decrements TTL in the FE chain. */
                        rc = ask_intent_add(&intent, ASK_ACTION_TTL_DEC, 0);
                        if (rc)
                                return rc;
                        break;
                case FLOW_ACTION_CSUM:
                        break;
                case FLOW_ACTION_PTYPE:
                case FLOW_ACTION_ACCEPT:
                        break;
                /*
                 * T-M6-A2 (strict action acceptance, 2026-08-18).
                 *
                 * kernel nf_flow_table (nf_flow_rule_route_common) always
                 * emits FLOW_ACTION_MANGLE of htype ETH for the next-hop
                 * L2 src/dst rewrite on EVERY forwarded flow. The FE-VM
                 * hardware path already performs that L2 rewrite via
                 * INSERT_L2_HDR from the resolved neighbour MAC, so an
                 * ETH-type MANGLE is genuinely satisfied by the HW record
                 * and is accepted.
                 *
                 * MANGLE of htype IP4/IP6/TCP/UDP is only emitted when the
                 * flow carries NF_FLOW_SNAT/DNAT — i.e. address/port NAT.
                 * The HW record does NOT apply those rewrites yet (the NAT
                 * compiler is T-M6-7). Accepting them as a no-op would
                 * publish an in_hw record that forwards NAT traffic WITHOUT
                 * translating it — silent misforwarding. Until T-M6-7,
                 * return -EOPNOTSUPP so the flow stays on the kernel SW
                 * fastpath, which translates correctly.
                 *
                 * FLOW_ACTION_ADD is a NAT-adjacent field increment with the
                 * same "not applied in HW" hazard: reject it.
                 *
                 * VLAN push/pop and PPPoE push set encap that the HW record
                 * does not build (VLAN is deferred T-M6-8, PPPoE is M6-C).
                 * Previously VLAN_PUSH/POP only set action_flags bits that
                 * ask_hw_flow_insert() ignores (ask_hw.c: (void)action_flags),
                 * so those flows offloaded WITHOUT the tag operation — the
                 * same misforwarding class. Reject until implemented.
                 */
                case FLOW_ACTION_MANGLE:
                        if (act->mangle.htype == FLOW_ACT_MANGLE_HDR_TYPE_ETH) {
                                rc = ask_intent_add(&intent,
                                                    ASK_ACTION_L2_REWRITE, 0);
                                if (rc)
                                        return rc;
                                break; /* L2 rewrite done by INSERT_L2_HDR */
                        }
                        /*
                         * T-M6-7.0: an L3/L4 mangle is a NAT/PAT rewrite. Decode
                         * the translated value straight from the mangle entry
                         * ({htype, offset, mask, val}), which is already the
                         * correct per-direction value (the kernel emits distinct
                         * ORIGINAL/REPLY rules). new = (old & mask) | val, val in
                         * network byte order. This is exactly how in-tree SoC
                         * router drivers (MediaTek/Airoha PPE, bnxt) decode NAT.
                         */
                        switch (act->mangle.htype) {
                        case FLOW_ACT_MANGLE_HDR_TYPE_IP4: {
                                __be32 v = (__be32)act->mangle.val;

                                if (act->mangle.offset ==
                                    offsetof(struct iphdr, saddr))
                                        rc = ask_intent_add_nat(&intent,
                                                ASK_ACTION_NAT_SRC,
                                                (const u8 *)&v, 4, 0);
                                else if (act->mangle.offset ==
                                         offsetof(struct iphdr, daddr))
                                        rc = ask_intent_add_nat(&intent,
                                                ASK_ACTION_NAT_DST,
                                                (const u8 *)&v, 4, 0);
                                else
                                        rc = -EOPNOTSUPP;
                                if (rc)
                                        return rc;
                                seen_l3_nat = true;
                                break;
                        }
                        case FLOW_ACT_MANGLE_HDR_TYPE_IP6: {
                                /* One 32-bit chunk per entry; offset is relative
                                 * to the ipv6hdr. Accumulate into the 16-byte
                                 * src/dst buffer and emit the typed action only
                                 * once all four chunks of that address arrive. */
                                __be32 v = (__be32)act->mangle.val;
                                u32 soff = offsetof(struct ipv6hdr, saddr);
                                u32 doff = offsetof(struct ipv6hdr, daddr);

                                if (act->mangle.offset >= soff &&
                                    act->mangle.offset <  soff + 16 &&
                                    !((act->mangle.offset - soff) & 3)) {
                                        u32 idx = act->mangle.offset - soff;

                                        memcpy(&v6_snat[idx], &v, 4);
                                        v6_snat_seen |= 1u << (idx >> 2);
                                        if (v6_snat_seen == 0xf) {
                                                rc = ask_intent_add_nat(&intent,
                                                        ASK_ACTION_NAT_SRC,
                                                        v6_snat, 16, 0);
                                                if (rc)
                                                        return rc;
                                                seen_l3_nat = true;
                                        }
                                } else if (act->mangle.offset >= doff &&
                                           act->mangle.offset <  doff + 16 &&
                                           !((act->mangle.offset - doff) & 3)) {
                                        u32 idx = act->mangle.offset - doff;

                                        memcpy(&v6_dnat[idx], &v, 4);
                                        v6_dnat_seen |= 1u << (idx >> 2);
                                        if (v6_dnat_seen == 0xf) {
                                                rc = ask_intent_add_nat(&intent,
                                                        ASK_ACTION_NAT_DST,
                                                        v6_dnat, 16, 0);
                                                if (rc)
                                                        return rc;
                                                seen_l3_nat = true;
                                        }
                                } else {
                                        return -EOPNOTSUPP;
                                }
                                break;
                        }
                        case FLOW_ACT_MANGLE_HDR_TYPE_TCP:
                        case FLOW_ACT_MANGLE_HDR_TYPE_UDP: {
                                /* offset 0: upper-half mask => source port,
                                 * lower-half => dest port (see
                                 * flow_offload_port_snat/dnat). The port value
                                 * is packed into val at the masked half. */
                                u32 hv = ntohl((__be32)act->mangle.val);

                                /* Reject L4 port NAT with no L3 NAT context
                                 * (bnxt rule) — the FE NAT compiler only
                                 * rewrites ports alongside an address xlate. */
                                if (!seen_l3_nat)
                                        return -EOPNOTSUPP;
                                if (act->mangle.mask == (u32)~htonl(0xffff0000))
                                        rc = ask_intent_add_nat(&intent,
                                                ASK_ACTION_NAPT_SPORT, NULL, 0,
                                                htons((u16)(hv >> 16)));
                                else if (act->mangle.mask ==
                                         (u32)~htonl(0x0000ffff))
                                        rc = ask_intent_add_nat(&intent,
                                                ASK_ACTION_NAPT_DPORT, NULL, 0,
                                                htons((u16)(hv & 0xffff)));
                                else
                                        rc = -EOPNOTSUPP;
                                if (rc)
                                        return rc;
                                break;
                        }
                        default:
                                return -EOPNOTSUPP;
                        }
                        break;
                case FLOW_ACTION_ADD:
                        pr_info_ratelimited("ask: flow_offload: FLOW_ACTION_ADD (NAT field add) not offloaded — SW fallback (T-M6-7)\n");
                        return -EOPNOTSUPP;
		case FLOW_ACTION_VLAN_POP:
			/* T-M6-8: POP has no action fields. Single ingress tag only;
			 * the kernel omits POP when in_vlan_ingress says HW already
			 * stripped it, so every POP reaching us must be represented. */
			if (++vlan_pop_seen > 1)
				return -EOPNOTSUPP;
			rc = ask_intent_add_vlan(&intent, ASK_ACTION_VLAN_POP,
						 0, 0, 0);
			if (rc)
				return rc;
			break;
		case FLOW_ACTION_VLAN_PUSH:
			/* Single 802.1Q tag only. 802.1ad/QinQ and tc-only PCP/DEI
			 * semantics stay in software until independently proven. */
			if (++vlan_push_seen > 1)
				return -EOPNOTSUPP;
			if (act->vlan.proto != htons(ETH_P_8021Q) ||
			    act->vlan.vid > VLAN_VID_MASK ||
			    act->vlan.prio > 7)
				return -EOPNOTSUPP;
			rc = ask_intent_add_vlan(&intent, ASK_ACTION_VLAN_PUSH,
						 act->vlan.vid, act->vlan.proto,
						 act->vlan.prio);
			if (rc)
				return rc;
			break;
		case FLOW_ACTION_VLAN_MANGLE:
		case FLOW_ACTION_VLAN_PUSH_ETH:
		case FLOW_ACTION_VLAN_POP_ETH:
			/* tc-only VLAN mutation / Ethernet-header actions are not emitted
			 * by the routed nft flowtable path. Fail closed rather than silently
			 * applying incomplete semantics. */
			return -EOPNOTSUPP;
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

        /*
         * T-M6-7.0: stash any parsed NAT/PAT translation into the flow key so
         * it round-trips through DESTROY/pending/neighbour rebuilds. This does
         * NOT change the FE comparison key (still the original ingress tuple);
         * T-M6-7.1 consumes these as FE opcode rewrite params. The plain routed
         * flow adds no NAT actions, so key->nat_flags stays 0 and the stored
         * flow is byte-identical to today.
         */
        for (i = 0; i < intent.n_actions; i++) {
                const struct ask_flow_action_ent *e = &intent.actions[i];

                switch (e->type) {
                case ASK_ACTION_NAT_SRC:
                        key->nat_flags |= ASK_NATF_SNAT;
                        memcpy(key->nat_src_ip, e->nat.addr, 16);
                        break;
                case ASK_ACTION_NAT_DST:
                        key->nat_flags |= ASK_NATF_DNAT;
                        memcpy(key->nat_dst_ip, e->nat.addr, 16);
                        break;
                case ASK_ACTION_NAPT_SPORT:
                        key->nat_flags |= ASK_NATF_SPAT;
                        key->nat_sport = e->nat.port;
                        break;
		case ASK_ACTION_NAPT_DPORT:
			key->nat_flags |= ASK_NATF_DPAT;
			key->nat_dport = e->nat.port;
			break;
		case ASK_ACTION_VLAN_POP:
			key->vlan_edit_flags |= ASK_VLANF_POP;
			break;
		case ASK_ACTION_VLAN_PUSH:
			key->vlan_edit_flags |= ASK_VLANF_PUSH;
			/* Compose the 16-bit TCI: PCP<<13 | DEI(0) | VID. The
			 * flowtable path supplies prio=0; DEI has no Linux field. */
			key->vlan_push_tci = htons(((u16)e->nat.vlan.prio << VLAN_PRIO_SHIFT) |
						   (e->nat.vlan.vid & VLAN_VID_MASK));
			key->vlan_push_tpid = e->nat.vlan.tpid;
			break;
		default:
			break;
		}
	}

        /* Lower the canonical intent to the legacy (oif, action_flags) pair.
         * For the IPv4-unicast flow this yields the exact pre-A1 values.
         * When NAT actions are present ask_intent_lower() returns -EOPNOTSUPP
         * (the FE-VM NAT opcode compiler is T-M6-7.1), so the flow fails closed
         * to the software fastpath — but its translation is now fully parsed
         * and carried, ready for 7.1 to consume. */
        rc = ask_intent_lower(&intent, &oif, out_action_flags);
        if (rc)
                return rc;
        *out_oif = oif;
        if (out_egress_dev)
                *out_egress_dev = egress;
        return 0;
}

/* ------------------------------------------------------------------------- */
/* PR14z11 (2026-05-19): recover the true next-hop dst from flow_offload.    */
/*                                                                            */
/* Root cause of PR14z10's M2 FAIL (6.918 Gbps PASS, 66.76% CPU FAIL):       */
/*                                                                            */
/* nf_flow_table_offload.c line 896 sets                                     */
/*    cls_flow->cookie = (unsigned long)tuple                                 */
/* where `tuple` is `struct flow_offload_tuple *` for ONE direction of the   */
/* conntrack flow.  When the REV direction is offered, the FLOW_DISSECTOR    */
/* key built from `tuple->src_v4 / tuple->dst_v4` encodes the REPLY tuple —  */
/* but conntrack's "reply tuple" semantics swap src/dst, so `dst_v4` for     */
/* the REV REPLACE is the DUT's OWN ip (the original src of the FWD).  That  */
/* address is never resolvable as a neighbour anywhere, so PR14z10's poll    */
/* tried `neigh_lookup(dst=DUT-ip)` 300+ times across both ifindices for    */
/* the full 30 s test and never drained a single REV cookie.  Every REV     */
/* packet fell back to the SW flowtable fast path, pushing CPU to 67%.       */
/*                                                                            */
/* The kernel's own `flow_offload_eth_dst()` (nf_flow_table_offload.c        */
/* line 280) reveals the canonical recipe for the                            */
/* FLOW_OFFLOAD_XMIT_NEIGH path:                                              */
/*                                                                            */
/*   this_tuple  = &flow->tuplehash[dir].tuple;                               */
/*   other_tuple = &flow->tuplehash[!dir].tuple;                              */
/*   daddr       = &other_tuple->src_v4;            <- true next-hop IP     */
/*   n = dst_neigh_lookup(this_tuple->dst_cache, daddr);                     */
/*                                                                            */
/* i.e. the OTHER direction's `src_v4` IS this direction's true L3 next-hop. */
/* For a forwarded TCP stream over a routed segment, the OPPOSITE tuple's    */
/* src_v4 is exactly the next-hop the kernel routed against.                 */
/*                                                                            */
/* Recovery from cookie: the cookie is `(unsigned long)tuple`, where tuple   */
/* is the address of `flow->tuplehash[dir].tuple` (an inner field of         */
/* flow_offload_tuple_rhash, which is in turn an array element of            */
/* flow_offload.tuplehash[]).  Two container_of steps reach the parent:     */
/*                                                                            */
/*   rh   = container_of(t,  struct flow_offload_tuple_rhash, tuple);        */
/*   // tuplehash[dir] == *rh, so:                                          */
/*   flow = (struct flow_offload *)((char *)rh -                             */
/*          offsetof(struct flow_offload, tuplehash[t->dir]));               */
/*                                                                            */
/* `t->dir` is encoded in the bitfield set by the kernel at flow install    */
/* time and is valid for the cookie's lifetime.                              */
/*                                                                            */
/* Safety: the cookie/tuple is valid for the duration of the REPLACE         */
/* callback.  The kernel holds the flowtable's rwlock across the offload    */
/* setup path (see nf_flow_offload_work_alloc + nf_flow_offload_tuple)      */
/* so the flow is guaranteed not to be freed while we read.                 */
/* ------------------------------------------------------------------------- */

static __be32 ask_z11_other_src_v4(unsigned long cookie, int *out_dir,
                                   struct net_device **out_iif)
{
        struct flow_offload_tuple *t;
        struct flow_offload_tuple_rhash *rh;
        struct flow_offload *flow;
        struct flow_offload_tuple *other;
        int dir;

        if (out_iif)
                *out_iif = NULL;

        if (!cookie)
                return 0;

        t   = (struct flow_offload_tuple *)cookie;
        dir = t->dir;
        if (dir < 0 || dir >= FLOW_OFFLOAD_DIR_MAX)
                return 0;

        rh   = container_of(t, struct flow_offload_tuple_rhash, tuple);
        flow = (struct flow_offload *)((char *)rh -
                offsetof(struct flow_offload, tuplehash[dir]));
        if (!flow)
                return 0;

        other = &flow->tuplehash[!dir].tuple;

        if (out_dir)
                *out_dir = dir;
        if (out_iif) {
                /*
                 * The OTHER tuple's iifidx is the netdev the kernel
                 * routed the THIS direction's egress against — i.e.
                 * the real egress device for the resolvable next-hop.
                 * Recover it via init_net (DPAA1 is always init_net).
                 */
                rcu_read_lock();
                *out_iif = dev_get_by_index_rcu(&init_net, other->iifidx);
                /* No refcount held outside rcu — caller treats as
                 * borrowed and re-resolves under its own rcu/ref. */
                rcu_read_unlock();
        }
        return other->src_v4.s_addr;
}

/*
 * T-M6-1: IPv6 counterpart of ask_z11_other_src_v4(). Recovers the real
 * next-hop from the conntrack opposite-direction tuple's src_v6 (the L3 dest
 * the kernel routed THIS direction's egress against) and the netdev it routed
 * through, so v6 neighbour resolution and egress selection match what
 * nf_flow_table's FLOW_OFFLOAD_XMIT_NEIGH path computes. Writes the 16-byte
 * next-hop into @out (returns true) or leaves it untouched (returns false).
 * Does NOT mutate key.dst_ip — only local routing state, exactly like v4.
 */
static bool ask_z11_other_src_v6(unsigned long cookie, int *out_dir,
                                 struct net_device **out_iif,
                                 struct in6_addr *out_nh)
{
        struct flow_offload_tuple *t;
        struct flow_offload_tuple_rhash *rh;
        struct flow_offload *flow;
        struct flow_offload_tuple *other;
        int dir;

        if (out_iif)
                *out_iif = NULL;
        if (!cookie || !out_nh)
                return false;

        t   = (struct flow_offload_tuple *)cookie;
        dir = t->dir;
        if (dir < 0 || dir >= FLOW_OFFLOAD_DIR_MAX)
                return false;

        rh   = container_of(t, struct flow_offload_tuple_rhash, tuple);
        flow = (struct flow_offload *)((char *)rh -
                offsetof(struct flow_offload, tuplehash[dir]));
        if (!flow)
                return false;

        other = &flow->tuplehash[!dir].tuple;

        if (out_dir)
                *out_dir = dir;
        if (out_iif) {
                rcu_read_lock();
                *out_iif = dev_get_by_index_rcu(&init_net, other->iifidx);
                rcu_read_unlock();
        }
        *out_nh = other->src_v6;
        return true;
}

/*
 * Serialise @key into the 14-byte EKFC record the FE-VM comparator matches.
 *
 * Layout is the silicon's MSB-first extraction order for EKFC 0x801C0006
 * (F-163, 2026-08-05: KG_SCH_KN_PORT_ID | IPSRC1 | IPDST1 | PTYPE1 |
 * L4PSRC | L4PDST):
 *   PORT_ID(1) SIP(4) DIP(4) PROTO(1) SPORT(2) DPORT(2)
 *
 * The PORT_ID prefix matches the real vendor cdx.ko external-hash key
 * byte-for-byte (union dpa_key, cdx_common.h, nxp-sdk branch) -- see the
 * ASK_FE_KEY_SIZE comment in ask_internal.h for the full provenance.
 *
 * ENDIANNESS (CR-002, fixed 2026-07-26): @sport/@dport are __be16, i.e. they
 * already hold the bytes in wire order. They MUST be copied, not shifted.
 * The previous code did
 *
 *      k[9] = (key->sport >> 8) & 0xff;  k[10] = key->sport & 0xff;
 *
 * which reads the __be16 as a native integer. On this little-endian ARM64
 * kernel a port whose wire bytes are AD 9C has the numeric value 0x9CAD, so
 * the shift emitted 9C AD — the bytes backwards. Insert and delete shared the
 * bug, so they agreed with each other and software-only tests passed, but
 * neither agreed with the key the KeyGen actually extracts. Silicon-verified
 * reference (Qdrant 2026-07-13, pre-PORT_ID 13-byte capture):
 * 0a63026a 0a6302b9 06 ad9c d903.
 *
 * One builder for insert, delete and tests so the two can never diverge again.
 * Non-static solely so tests/ask_test_flow_offload.c can pin the exact bytes.
 */
void ask_fe_build_key(const struct ask_flow_key *key, u8 k[ASK_FE_KEY_SIZE])
{
        /* F-188: k[0] = 0x00, NOT key->port_id. The silicon's PORT_ID
         * extraction reads the scheme's zeroed dv0/dv1 default (0x00) --
         * the raw FMan hw port id (0x11 for eth4) never matches (E25/E26
         * brute-force + live-hash verified; see decomp/experiments.md E28).
         */
        k[0] = 0;
        memcpy(&k[1],  key->src_ip, 4);
        memcpy(&k[5],  key->dst_ip, 4);
        k[9] = key->l4_proto;
        memcpy(&k[10], &key->sport, sizeof(key->sport));
        memcpy(&k[12], &key->dport, sizeof(key->dport));
}

/*
 * M6 Piece 3: v6 ehash key builder.  Same MSB-first EKFC extraction order
 * as v4 (F-163: PORT_ID prefix added), 16-byte addresses:
 * PORT_ID(1)+SIP(16)+DIP(16)+PROTO(1)+SPORT(2)+DPORT(2).
 */
void ask_fe_build_key_v6(const struct ask_flow_key *key, u8 k[ASK_FE_KEY_SIZE_V6])
{
        k[0] = 0;   /* F-188: PORT_ID = 0x00 (zeroed dv default), see ask_fe_build_key() */
        memcpy(&k[1],  key->src_ip, 16);
        memcpy(&k[17], key->dst_ip, 16);
        k[33] = key->l4_proto;
        memcpy(&k[34], &key->sport, sizeof(key->sport));
        memcpy(&k[36], &key->dport, sizeof(key->dport));
}

/*
 * Dual-lane 46-byte key builder (specs/ask2-ipv6-dual-lane-key-design.md).
 * Mirrors the F-224 GEC extraction order byte-for-byte so the software key
 * equals what KeyGen emits. The absent family's lane is zero-filled, exactly
 * as the validated GEC codes (0x0b/0x1b) zero-fill from the reset-0 default
 * register on a wrong-family frame (silicon-proven 2026-08-21: v4 flow -> v6
 * lanes 16+16 zero; v6 flow -> v4 lane 8 zero). ask_flow_key.src_ip/dst_ip are
 * 16-byte; for a v4 flow the address is in the first 4 bytes.
 */
void ask_fe_build_key_dual(const struct ask_flow_key *key,
			   u8 k[ASK_FE_KEY_SIZE_DUAL])
{
        memset(k, 0, ASK_FE_KEY_SIZE_DUAL);
        if (key->l3_proto == ASK_FLOW_L3_IPV6) {
                k[0] = ASK_FE_FAMILY_V6;
                /* v4 lane k[1..8] stays zero */
                memcpy(&k[9],  key->src_ip, 16);   /* IPv6 src */
                memcpy(&k[25], key->dst_ip, 16);   /* IPv6 dst */
        } else {
                k[0] = ASK_FE_FAMILY_V4;
                memcpy(&k[1], key->src_ip, 4);     /* IPv4 src */
                memcpy(&k[5], key->dst_ip, 4);     /* IPv4 dst */
                /* v6 lanes k[9..40] stay zero */
        }
        k[41] = key->l4_proto;
        memcpy(&k[42], &key->sport, sizeof(key->sport));
        memcpy(&k[44], &key->dport, sizeof(key->dport));
}

/* ------------------------------------------------------------------------- */
/* FE-VM debugfs flow insert helper (Phase 3, 2026-07-07) — converts ask_flow_key
 * to FMan hash key (L4PDST+L4PSRC+IPDST+IPSRC) and writes fe_flow debugfs. */
/* F-109: Direct kernel API for flow insert — replaces debugfs loopback.
 * Uses fman_pcd_fe_flow_add() with a struct fman_pcd_fe_flow_action
 * instead of filp_open() + kernel_write() to /sys/kernel/debug/.../fe_flow.
 * Key is MSB-first EKFC extraction order: SIP(4)+DIP(4)+PROTO(1)+SPORT(2)+DPORT(2).
 */
static int ask_fe_flow_insert(const struct ask_flow_key *key,
                              unsigned long enq_off, u32 tx_fqid)
{
        struct fman_pcd_fe_flow_action action;
        struct fman *fm;
        int rc, table_idx;
        u8 key_buf[ASK_FE_KEY_SIZE_DUAL];

        if (!key) {
                ask_pr_dbg("fe_flow_insert: NULL key (flow destroyed) -- skipping\n");
                return -EINVAL;
        }

        fm = ask_hw_get_fman();
        if (!fm) {
                ask_pr_dbg("fe_flow_insert: no bound fman (HW not ready)\n");
                return -ENODEV;
        }
        if (!enq_off) {
                ask_pr_dbg("fe_flow_insert: invalid ENQ FE offset 0x0\n");
                return -EINVAL;
        }

        memset(&action, 0, sizeof(action));

        /* Dual-lane 46-byte key: ONE key/table/scheme for both families
         * (specs/ask2-ipv6-dual-lane-key-design.md). The family byte + zeroed
         * absent lane keep v4 and v6 records disjoint in the one per-port table
         * (table_idx 0), so no second scheme / parser LCV split. */
        ask_fe_build_key_dual(key, key_buf);
        memcpy(action.key, key_buf, ASK_FE_KEY_SIZE_DUAL);
        action.key_size = ASK_FE_KEY_SIZE_DUAL;
        table_idx = 0;
        action.enq_off   = enq_off;
        /* F-204 / T-M6-1 Phase 2a: explicit ehash selector. This is SEPARATE
         * from key->port_id, which remains the ingress FMan port for F-195's
         * own-port miss-FQID resolution. v4 passes 0 exactly as before; v6
         * would pass 1, but preflight still rejects v6 until Phase 3 dispatch. */
        action.table_idx = (u8)table_idx;

        /* T-M7-2 S1 (2026-08-15): hardware TX terminal. Supply the resolved
         * per-egress-interface TX FQID plus the routed L2 rewrite (next-hop
         * MAC as new destination, egress-port MAC as new source, IPv4/IPv6
         * EtherType). When tx_fqid is non-zero, the FE record (F-198) emits
         * INSERT_L2_HDR(0x41) + ENQUEUE_PKT(0x01)-to-tx_fqid so a HIT is
         * forwarded direct-to-wire, bypassing kernel RX reinjection. When it
         * is zero the record keeps the F-197 own-port RX-FQID reinjection
         * terminal (observability / fallback). Both MACs are already resolved
         * in the key by ask_resolve_neigh_v4() (ARP NUD_VALID) at REPLACE
         * time; a zero MAC means neigh unresolved and the caller has already
         * bounced to SW before reaching here. */
        action.tx_fqid = tx_fqid;
        ether_addr_copy(action.next_hop_mac, key->next_hop_mac);
        ether_addr_copy(action.egress_mac, key->egress_mac);
        action.eth_type = (key->l3_proto == ASK_FLOW_L3_IPV6)
                                ? ETH_P_IPV6 : ETH_P_IP;

        /*
         * T-M6-7.1 arming: copy the parsed/carry NAT tuple into the public
         * FMan action only when the family's NAT gate
         * is armed. Disarmed (default), action was memset(0) above and these
         * fields stay zero -> F-230's `if (nat && nat->flags)` is skipped and
         * the FE record remains byte-identical to F-200/F-226. NAT flag bit
         * assignments intentionally match FMAN_PCD_NATF_* (1/2/4/8).
         */
        if (key->nat_flags) {
                /* Close the module-param race between parse/lower/preflight and
                 * record publication: if the gate was cleared after preflight,
                 * NEVER insert a plain routed record for a NAT flow (silent
                 * misforward). Fail closed so the caller removes the tentative
                 * HW-backed SW entry and keeps this flow in the kernel path.
                 * IPv4 uses nat44_offload; IPv6 uses nat66_offload (both default on). */
                bool nat_ok = (key->l3_proto == ASK_FLOW_L3_IPV6)
                                      ? ask_hw_nat66_offload_armed()
                                      : ask_hw_nat44_offload_armed();
                if (!nat_ok)
                        return -EOPNOTSUPP;
		action.nat_flags = key->nat_flags;
		memcpy(action.nat_sip, key->nat_src_ip, 16);
		memcpy(action.nat_dip, key->nat_dst_ip, 16);
		action.nat_sport = key->nat_sport;
		action.nat_dport = key->nat_dport;
	}

	/*
	 * T-M6-8 arming: copy the parsed VLAN edit into the public FMan action
	 * only when the VLAN gate is armed. Disarmed (default), action was
	 * memset(0) above so vlan_flags stays 0 -> the F-233 emitter's
	 * `if (vlan && vlan->flags)` is skipped and the FE record is
	 * byte-identical to the routed/NAT path. Same module-param race close
	 * as NAT: never publish a VLAN flow's record without the tag op.
	 * VLAN flag bit values intentionally match FMAN_PCD_VLANF_* (1/2).
	 */
	if (key->vlan_edit_flags) {
		if (!ask_hw_vlan_offload_armed())
			return -EOPNOTSUPP;
		action.vlan_flags = key->vlan_edit_flags;
		action.vlan_tci   = key->vlan_push_tci;
		action.vlan_tpid  = key->vlan_push_tpid;
		/*
		 * T-M6-8 DIAGNOSTIC: log exactly what VLAN edit each DIRECTION's
		 * record carries, keyed by the flow 5-tuple + oif, so a single
		 * board run unambiguously shows POP on the ingress-strip direction
		 * and PUSH (with TCI/TPID) on the egress-tag direction — without
		 * fighting /dev/mem capture timing. Ratelimited; drop once the
		 * push/pop datapath is validated.
		 */
		pr_info_ratelimited("ask: VLAN-DIAG insert %pI4:%u->%pI4:%u proto=%u txfq=0x%x port=0x%02x flags=%s%s tci=0x%04x tpid=0x%04x nh=%pM em=%pM\n",
				    &key->src_ip[0], ntohs(key->sport),
				    &key->dst_ip[0], ntohs(key->dport),
				    key->l4_proto, tx_fqid, key->port_id,
				    (key->vlan_edit_flags & ASK_VLANF_POP) ? "POP" : "",
				    (key->vlan_edit_flags & ASK_VLANF_PUSH) ? "PUSH" : "",
				    ntohs(key->vlan_push_tci),
				    ntohs(key->vlan_push_tpid),
				    key->next_hop_mac, key->egress_mac);
	}

        /* F-195/F-204 contract: the second argument remains exclusively the
         * ingress FMan port for own-port miss-FQID resolution (eth3=0x200,
         * eth4=0x300). Never overload it with a table index — that historical
         * bug cross-port dropped eth4 flows. The separate action.table_idx
         * field now selects ehash table 0/1 inside fman_pcd_fe_flow_add().
         */
        ask_pr_info("F-195 flow-add call fm=%px action=%px hw_port=0x%02x table_idx=%d key_size=%u sizeof_action=%zu key_size_off=%zu key=%*phN\n",
                    fm, &action, key->port_id, table_idx, action.key_size,
                    sizeof(action),
                    offsetof(struct fman_pcd_fe_flow_action, key_size),
                    action.key_size, action.key);

        /* Drive the real FMan (fman_get_pcd -> ehash) and surface failures so
         * callers can roll back provisional HW-backed ownership. */
        rc = fman_pcd_fe_flow_add(fm, key->port_id, &action);
        if (rc)
                ask_pr_warn("F-195 flow-add return=%d fm=%px action=%px hw_port=0x%02x table_idx=%d key_size=%u\n",
                            rc, fm, &action, key->port_id, table_idx,
                            action.key_size);
        return rc;
}

/*
 * Fix B: remove exactly the one FE-VM silicon record for @key. Builds the
 * identical EKFC key ask_fe_flow_insert() used (including the F-163
 * port_id prefix, preserved in @key since insert time), so fman_pcd_fe_flow_del
 * (per-key, F-117) matches and unlinks just this flow — not clear-all.
 */
static void ask_fe_flow_remove(const struct ask_flow_key *key)
{
        u8 k[ASK_FE_KEY_SIZE_DUAL];
        u8 klen;

        if (!key)
                return;
        /*
         * Build the SAME 46-byte dual-lane key the matching insert used, so
         * per-key fman_pcd_fe_flow_del() unlinks exactly this record. Both
         * families now share the one 46-byte key/table (the family byte +
         * zeroed absent lane keep them distinct).
         */
        ask_fe_build_key_dual(key, k);
        klen = ASK_FE_KEY_SIZE_DUAL;
        /* Phase 1 (per-port tables): pass the ingress hw port so the delete
         * selects THIS port's routed-IPv4 table instance (F-220/F-221), matching
         * the insert side which passed key->port_id. v6 delete still selects the
         * global table by key length; the port arg is harmless there. */
        fman_pcd_fe_flow_del(ask_hw_get_fman(), key->port_id, k, klen);
}


/* FLOW_CLS_* dispatch                                                        */
/* ------------------------------------------------------------------------- */

static int ask_flow_offload_replace(struct net_device *ingress_dev,
                                    struct flow_cls_offload *f)
{
        struct ask_flow_table *t = ask_flow_default_table();
        struct net_device *egress_dev = NULL;
        struct net_device *neigh_dev = NULL;
        struct ask_flow_key key;
        __be32 dst_ip = 0;
        struct in6_addr dst_ip6 = {};   /* T-M6-1: v6 next-hop for neigh resolve */
        bool is_v6;
        u32 hw_id = 0;
        u32 action_flags = 0;
        u32 oif = 0;
        u32 generation;
        u32 true_iif = 0;
        bool have_meta_iif = false;
        int rc;

        if (!t) {
                pr_info_ratelimited("ask: flow_offload: REPLACE early-return (no default table) cookie=0x%lx\n",
                                    f->cookie);
                return -EOPNOTSUPP;
        }

        /*
         * T-M6-A3: claim ownership for this REPLACE up front. This bumps the
         * cookie's generation and clears any prior tombstone, so a DESTROY
         * that arrives while we resolve neighbours / install the FE record is
         * observed by the pre-publish gen check (insert_owned) and by the
         * pending/neigh workers. On registry OOM, fail to software rather
         * than publish an unguarded flow.
         */
        generation = ask_flow_gen_next(t, (u64)f->cookie);
        if (generation == 0) {
                pr_info_ratelimited("ask: flow_offload: REPLACE cookie=0x%lx gen-alloc failed — SW fallback\n",
                                    f->cookie);
                return -EOPNOTSUPP;
        }

        rc = ask_parse_match(f, &key);
        if (rc) {
                pr_info_ratelimited("ask: flow_offload: REPLACE early-return (parse_match=%d) cookie=0x%lx\n",
                                    rc, f->cookie);
                return rc;
        }
        is_v6 = (key.l3_proto == ASK_FLOW_L3_IPV6);

        /*
         * PR14z19 (2026-05-25): populate key.iif from the block_cb's
         * ingress_dev BEFORE handing the key down to ask_flow_insert()
         * → ask_hw_flow_insert().  The HW insert path resolves the
         * ingress FMan port id via dev_get_by_index(&init_net, key.iif)
         * and uses that to look up the per-port cc_v4_tcp / cc_v4_udp
         * node where this 5-tuple's match must land.  Without this
         * line key.iif stays 0, dev_get_by_index returns NULL, and
         * every flow falls into the silent SW-fallback path (M2 gate
         * 2026-05-24 measured 6.950 Gbps / 26.17 % CPU exactly because
         * of this — every dmesg "hw_insert=-19 (SW-fallback)" entry
         * was an -ENODEV from that lookup).
         *
         * The deferred-insert PR14y / poll-replay PR14z9 paths copy
         * the key wholesale via `p->key = *key` in
         * ask_flow_pending_enqueue(), so setting iif here also
         * propagates into the replay path automatically — no separate
         * fix needed in ask_flow_pending_poll_fn().
         */
        if (ingress_dev) {
                pr_info_ratelimited("ask: flow_offload: PR14z19 IIF override key.iif=%d (from ingress_dev=%s) cookie=0x%lx\n",
                                    ingress_dev->ifindex, netdev_name(ingress_dev), f->cookie);
                key.iif = ingress_dev->ifindex;
        }

        /*
         * MULTI-PORT INGRESS FIX (2026-08-21): ingress_dev is the block_cb's
         * netdev, which for an nft flowtable spanning >2 interfaces is NOT
         * reliably the true ingress — the same cookie is delivered to EVERY
         * device's block_cb, and the PR14r dedup keeps whichever arrived first
         * (eth3, alphabetically). That mis-attributed every non-eth3 ingress
         * (e.g. an eth2->eth4 flow) to eth3, so fman_pcd_fe_flow_add() keyed
         * the record into eth3's per-port ehash table (F-220) instead of the
         * true ingress port's table -> the true ingress port's RX classifier
         * looked up its own (empty) table -> permanent MISS -> software forward
         * (board .185 2026-08-21: eth2 flow, ingress=eth3, pkt_count=0).
         *
         * The kernel already encodes the AUTHORITATIVE true ingress in the
         * flow rule's FLOW_DISSECTOR_KEY_META.ingress_ifindex (populated from
         * the conntrack tuple iifidx in nf_flow_table_offload.c), which every
         * mainline offload driver (mlx5/mtk/ocelot/nfp/...) uses. Prefer it
         * over the block_cb dev so the record lands in the correct per-port
         * table for ANY of the five ports. eth3/eth4 flows are unaffected
         * (meta ingress == ingress_dev there).
         */
        {
                struct flow_rule *rule = flow_cls_offload_flow_rule(f);

                if (rule && flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_META)) {
                        struct flow_match_meta mm;

                        flow_rule_match_meta(rule, &mm);
                        if (mm.key && mm.mask &&
                            (mm.key->ingress_ifindex & mm.mask->ingress_ifindex)) {
                                if (key.iif != mm.key->ingress_ifindex)
                                        pr_info_ratelimited("ask: flow_offload: META iif correction key.iif %d -> %u (block dev=%s) cookie=0x%lx\n",
                                                            key.iif,
                                                            mm.key->ingress_ifindex,
                                                            ingress_dev ? netdev_name(ingress_dev) : "?",
                                                            f->cookie);
                                key.iif = mm.key->ingress_ifindex;
                                /* T-M6-8: the authoritative true ingress for
                                 * the egress-echo filter below. */
                                true_iif = mm.key->ingress_ifindex;
                                have_meta_iif = true;
                        }
                }
        }

        rc = ask_parse_action(f, &key, &action_flags, &oif, &egress_dev);
        if (rc) {
                pr_info_ratelimited("ask: flow_offload: REPLACE early-return (parse_action=%d) cookie=0x%lx\n",
                                    rc, f->cookie);
                return rc;
        }

        memcpy(&dst_ip, &key.dst_ip[0], 4);

        /*
         * PR14z11 (2026-05-19): resolve the next-hop dst_ip with the
         * conntrack opposite-direction tuple's src_v4 — this is the
         * REAL next-hop IP the kernel routed against, and matches
         * what nf_flow_table_offload.c's own flow_offload_eth_dst()
         * helper does for the FLOW_OFFLOAD_XMIT_NEIGH case.
         *
         * The kernel-encoded FLOW_DISSECTOR dst_ip in the cls_flow
         * rule encodes the THIS-direction tuple's dst_v4, which under
         * conntrack's swapped reply-tuple semantics for the REV
         * direction equals the DUT's own IP (the original FWD src).
         * That value is never neigh-resolvable, so the PR14z10
         * pending list never drained REV cookies on the M2 gate.
         *
         * We additionally override egress_dev with the opposite-
         * direction tuple's iifidx — i.e. the netdev the kernel
         * actually routed THIS direction's TX through. Without this,
         * the egress_dev that came out of ask_parse_action() is
         * whatever act->dev the REDIRECT named, which for some REV
         * cookies points at the wrong interface (the one that owns
         * the un-resolvable original-fwd-dst IP).
         *
         * CRITICAL: We do NOT mutate key.dst_ip here. The HW CC node
         * built in ask_hw_flow_insert_v4_tcp() uses key.dst_ip to build
         * the 5-tuple match block, which must remain the packet's true
         * L3 destination IP. We only mutate the local dst_ip variable
         * for routing/neighbour resolution purposes.
         */
        {
                struct net_device *z11_iif = NULL;

                if (is_v6) {
                        struct in6_addr z11_nh6;

                        if (ask_z11_other_src_v6((unsigned long)f->cookie,
                                                 NULL, &z11_iif, &z11_nh6) &&
                            !ipv6_addr_any(&z11_nh6)) {
                                dst_ip6 = z11_nh6;
                                if (z11_iif) {
                                        egress_dev = z11_iif;
                                        oif = z11_iif->ifindex;
                                }
                                pr_info_ratelimited("ask: flow_offload: T-M6-1 v6 next-hop cookie=0x%lx nh-dst=%pI6c egress=%s\n",
                                                    f->cookie, &dst_ip6,
                                                    egress_dev ? netdev_name(egress_dev) : "?");
                        }
                } else {
                        __be32 z11_dst;

                        z11_dst = ask_z11_other_src_v4((unsigned long)f->cookie,
                                                       NULL, &z11_iif);
                        if (z11_dst != 0) {
                                dst_ip = z11_dst;
                                if (z11_iif) {
                                        egress_dev = z11_iif;
                                        oif = z11_iif->ifindex;
                                }
                                pr_info_ratelimited("ask: flow_offload: PR14z11 resolved next-hop cookie=0x%lx nh-dst=%pI4 egress=%s\n",
                                                    f->cookie, &z11_dst,
                                                    egress_dev ? netdev_name(egress_dev) : "?");
                        }
                }
        }

        /*
         * PR14z6 (2026-05-19): ingress-side filter.
         *
         * The kernel's nft flowtable offload delivers every
         * FLOW_CLS_REPLACE to EVERY netdev in the flowtable's
         * `devices = { ... }` list, not just the true ingress.  For a
         * 2-port flowtable (eth3, eth4) each cookie therefore arrives
         * twice: once with this block_cb's dev == true ingress, once
         * with this block_cb's dev == the egress (= act->dev).
         *
         * The PR14r dedup below silently drops the second delivery
         * but keeps the FIRST's `ingress_dev` as the "ingress" — and
         * the first delivery is whichever block_cb was registered
         * first (eth3, alphabetically).  Result: every cookie gets
         * tagged with ingress=eth3, the `first_pid` cmpxchg latches
         * eth3's pid as FWD, and the REV pipeline NEVER receives a
         * single flow.  The eth4→eth3 reverse-direction traffic
         * (TCP data return) goes slow-path → ~60% DUT CPU under
         * iperf3 -P 8 (the M2 2026-05-19 measurement was 6.881 Gbps
         * / 59.92% CPU, the smoking gun: dmesg shows ALL "REPLACE
         * installed" entries with `ingress=eth3` regardless of the
         * cookie's true direction).
         *
         * The disambiguator: if this block_cb's dev IS the egress
         * dev named by the REDIRECT/MIRRED action, this is the
         * egress-side echo, NOT the true ingress.  Decline; the
         * other block_cb (true ingress) handles the actual install.
         * Direction auto-discovery via `first_pid` then sees the
         * true ingress pid for each cookie and routes FWD vs REV
         * pipelines correctly.
         */
        /*
         * T-M6-8 FIX: the old heuristic `ingress_dev == egress_dev` broke for
         * VLAN flows. PR14z11 above overrides egress_dev with the opposite
         * conntrack tuple's iifidx, which is the PHYSICAL device (eth3), while
         * the block dev is also physical eth3 — so a HELGA(eth4)->.110(eth3.100)
         * cookie delivered to the eth3 block had ingress_dev==egress_dev==eth3
         * and was WRONGLY skipped as an echo, collapsing both directions onto
         * one and cross-assigning the VLAN action (POP vs PUSH). The rule
         * already carries the AUTHORITATIVE true ingress in
         * FLOW_DISSECTOR_KEY_META.ingress_ifindex (captured above as true_iif);
         * the egress-side echo is exactly the delivery whose block dev is NOT
         * the true ingress. Use that when available; fall back to the old
         * heuristic only for rules without META (should not happen on 6.18).
         */
        if (have_meta_iif) {
                if (ingress_dev && ingress_dev->ifindex != true_iif) {
                        pr_info_ratelimited("ask: flow_offload: REPLACE skip egress-side echo cookie=0x%lx dev=%s (block!=true-ingress %u — true ingress installs)\n",
                                            f->cookie, netdev_name(ingress_dev),
                                            true_iif);
                        return 0;
                }
        } else if (ingress_dev && egress_dev && ingress_dev == egress_dev) {
                pr_info_ratelimited("ask: flow_offload: REPLACE skip egress-side echo cookie=0x%lx dev=%s (act->dev matches block dev — true ingress will install)\n",
                                    f->cookie, netdev_name(ingress_dev));
                return 0;
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
         *
         * PR14z4 (2026-05-19) NOTE: an earlier revision hoisted
         * ask_hw_port_bind() ABOVE this dedup check on the theory that
         * the second-arrival netdev (eth4) needed its own KG scheme
         * for reverse-direction silicon classification.  Empirically
         * (M2 run 2026-05-19) binding TWO KG schemes to the SAME
         * cc_tree HALVED forward-direction silicon throughput (eth3
         * RX dropped fell from 37.9M → 16.0M, eth4 TX from 53.7 GB →
         * 20.5 GB) without enabling reverse-direction silicon (eth4
         * RX still kernel-SW-only).  Hypothesis: FMan v3 cannot
         * usefully share a cc_tree across two schemes — the second
         * scheme's KG hash either fragments the CC slot population or
         * starves a shared QBMan resource.  Reverted; reverse-
         * direction silicon needs a separate cc_tree (PR14z5 future
         * work), not a second scheme.
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
        /*
         * T-M6-8: for a reverse VLAN direction that PUSHes a tag, egress_dev is
         * the PHYSICAL port (eth3, from PR14z11), but the next-hop ARP/ND lives
         * on the VLAN vif (eth3.100) — the physical port holds only a permanent
         * FAILED neigh for the tagged subnet. Resolve the neighbour on the vif
         * (matched by the action's TPID/VID); egress_mac is identical (the vif
         * inherits the physical MAC, which ask_resolve_neigh uses). FMan
         * port/TX-FQ stay physical. If the neigh isn't cached yet the flow
         * parks pending and the poll (also vif-aware) drains it later.
         */
        neigh_dev = egress_dev;
        if (!is_v6 && (key.vlan_edit_flags & ASK_VLANF_PUSH) &&
            egress_dev && !is_vlan_dev(egress_dev)) {
                struct net_device *vdev;
                u16 vid = ntohs(key.vlan_push_tci) & VLAN_VID_MASK;

                rcu_read_lock();
                vdev = __vlan_find_dev_deep_rcu(egress_dev,
                                                key.vlan_push_tpid, vid);
                if (vdev)
                        dev_hold(vdev);
                rcu_read_unlock();
                if (vdev)
                        neigh_dev = vdev;
        }

        if (is_v6)
                ask_resolve_neigh_v6(neigh_dev, &dst_ip6,
                                     key.next_hop_mac, key.egress_mac);
        else
                ask_resolve_neigh_v4(neigh_dev, dst_ip,
                                     key.next_hop_mac, key.egress_mac);

        if (neigh_dev != egress_dev)
                dev_put(neigh_dev);

        /*
         * F-111: Reject multicast/broadcast next-hop MACs before HW insert.
         * The FMan HM chain writes the next-hop MAC into the L2 header;
         * multicast or broadcast destinations would produce invalid frames
         * on the wire.  Return -EAGAIN so the kernel SW fastpath handles
         * the flow (SW forwarding correctly handles multicast/broadcast).
         */
        if (is_multicast_ether_addr(key.next_hop_mac))
                return -EAGAIN;

        /* T-M6-1 first v6 insert iteration: if NDISC is unresolved, trigger a
         * neighbour probe and fall back to software. The legacy pending queue
         * stores a __be32 v4 key; widening/replay is deliberately deferred
         * until the direct stable-neighbour v6 HIT is proven. Our lab topology
         * pre-resolves fd99 neighbours, so the HIT path does not take this arm.
         */
        if (is_v6 && egress_dev && is_zero_ether_addr(key.next_hop_mac)) {
                struct neighbour *n;

                n = neigh_lookup(&nd_tbl, &dst_ip6, egress_dev);
                if (!n)
                        n = __neigh_create(&nd_tbl, &dst_ip6, egress_dev, true);
                if (n && !IS_ERR(n)) {
                        neigh_event_send(n, NULL);
                        neigh_release(n);
                }
                pr_info_ratelimited("ask: flow_offload: T-M6-1 v6 neigh unresolved cookie=0x%lx dst=%pI6c dev=%s — SW fallback\n",
                                    f->cookie, &dst_ip6,
                                    netdev_name(egress_dev));
                return -EAGAIN;
        }

        /*
         * PR14y: if the next-hop MAC is still all-zero, the ARP entry is
         * NUD_NONE / NUD_INCOMPLETE / NUD_FAILED.  Don't burn a CC slot on
         * a guaranteed-failure HW insert — park the cookie on the pending
         * list and let the NETEVENT_NEIGH_UPDATE notifier replay the
         * insert the moment the neigh resolves.  The kernel SW flowtable
         * carries the flow in the meantime, and crucially we send a probe
         * via neigh_event_send() to accelerate ARP resolution rather than
         * waiting for the SW path's natural ARP trigger.
         *
         * Cookie is NOT inserted into the rhashtable yet; if FLOW_CLS_DESTROY
         * arrives before the neigh resolves, ask_flow_offload_destroy()
         * drops the pending entry instead of trying ask_flow_remove() on a
         * cookie that was never installed.
         */
		/* PR14y BUG #2: ARP TOCTOU — neighbour resolved but evicted
		 * before insert.  Deferred to pending queue; residual risk
		 * of indefinite pinning if ARP churns.  M3 eviction policy
		 * will age out entries stuck > 30 s and force SW path.
		 */
        if (!is_v6 && egress_dev && is_zero_ether_addr(key.next_hop_mac)) {
                /* PR14y BUG #2: ARP TOCTOU — neighbour resolved but evicted
                 * before insert.  Deferred pending; M3 eviction TBD.
                 */
                int qrc;
                struct neighbour *n;
                u32 dst_key = (__force u32)dst_ip;

                n = neigh_lookup(&arp_tbl, &dst_key, egress_dev);
                if (!n)
                        n = __neigh_create(&arp_tbl, &dst_key, egress_dev, true);
                if (n && !IS_ERR(n)) {
                        neigh_event_send(n, NULL);
                        neigh_release(n);
                }

                qrc = ask_flow_pending_enqueue((u64)f->cookie, &key, oif,
                                               action_flags,
                                               egress_dev->ifindex,
                                                /* true ingress (META-corrected), not block dev */
                                                key.iif,
                                                dst_ip, generation);
                if (qrc == 0) {
                        pr_info_ratelimited("ask: flow_offload: PR14z10 defer cookie=0x%lx oif=%u eg_if=%d in_if=%d dst=%pI4 (neigh unresolved, ARP probed)\n",
                                            f->cookie, oif,
                                            egress_dev->ifindex,
                                            ingress_dev ? ingress_dev->ifindex : 0,
                                            &dst_ip);
                        return 0;
                }
                /*
                 * PR14z2 (2026-05-18): on queue overflow do NOT fall
                 * through to ask_flow_insert(). The old PR14y fall-
                 * through path called ask_flow_insert() with a zero
                 * next-hop MAC; ask_hw_flow_insert returned -EAGAIN;
                 * the pre-PR14z2 ask_flow_insert then fabricated a
                 * fake hw_id and shoved the cookie into the rht,
                 * pinning the flow to the SW fast path for its
                 * entire lifetime (because the PR14r "REPLACE dedup"
                 * check at line 687 prevented any future retry once
                 * the cookie was in the rht).
                 *
                 * Correct behaviour: return -EOPNOTSUPP. The kernel
                 * nf_flow_table core treats -EOPNOTSUPP as "this
                 * driver declined offload"; the flow stays on the
                 * kernel SW flowtable's own fast path (which is
                 * still considerably cheaper than the per-packet
                 * routing slow path). If/when the operator clears
                 * the ARP storm and a new FLOW_CLS_REPLACE arrives,
                 * the cookie has changed (nf_flow_table generates a
                 * fresh cookie per `flow add`), so dedupe does not
                 * apply and the second-attempt offload can succeed.
                 *
                 * Whether the workload that overflowed a 4096-deep
                 * queue should reach M2 thresholds at all is an
                 * orthogonal question — this branch is a safety
                 * valve, not a fast path.
                 */
                pr_info_ratelimited("ask: flow_offload: PR14z2 queue full (%d) — declining HW offload, kernel SW flowtable retains cookie=0x%lx\n",
                                    qrc, f->cookie);
                return -EOPNOTSUPP;
        }

        /*
         * PR14z5 (2026-05-19): dual-pipeline ingress KG port-bind.
         *
         * Each direction now has its own independent cc_tree + KG
         * scheme.  We decide which pipeline (FWD vs REV) the current
         * REPLACE belongs to by auto-discovering the first-arrival
         * ingress port: the very first ingress pid we ever see is
         * tagged FWD; any other pid is tagged REV.  This is
         * direction-agnostic with respect to physical port wiring —
         * either eth3 or eth4 can be "forward" depending on which
         * iperf stream lands first.
         *
         * `first_pid` is static so it persists across calls.
         * 0xff = uninitialised.  Once locked in at first arrival it
         * never changes for the lifetime of the module.  On rmmod
         * the variable resets to 0xff via the .data zero-init on
         * next insmod (file-scope static in BSS-equivalent storage),
         * giving a clean slate for the next session.
         *
         * Concurrency: multiple FLOW_CLS_REPLACE callbacks can race
         * here under different ingress dev locks.  The READ_ONCE +
         * cmpxchg pattern ensures the first writer wins and all
         * subsequent readers see the same value.  Worst case under
         * extreme race: two REPLACEs both see first_pid==0xff and
         * one of them wins the cmpxchg; the loser proceeds with the
         * winner's value and may be tagged REV when it should have
         * been FWD (or vice versa).  This is acceptable because the
         * two pipelines are functionally symmetric — only the
         * physical port assignment differs.
         */
        {
                enum ask_hw_dir __dir = ASK_HW_DIR_FWD;
                /*
                 * MULTI-PORT INGRESS FIX (2026-08-21): resolve the ingress
                 * hwport from the META-corrected key.iif (the true ingress),
                 * NOT the block_cb's ingress_dev. key.port_id set below is what
                 * fman_pcd_fe_flow_add() uses to pick the per-port ehash table
                 * (F-220); using the block dev put an eth2 flow's record into
                 * eth3's table -> MISS. Look up the true-ingress netdev by
                 * key.iif (ref held only within this scope, released before any
                 * exit). Falls back to ingress_dev if the lookup fails.
                 */
                struct net_device *true_iif_dev =
                        key.iif ? dev_get_by_index(&init_net, key.iif) : NULL;
                struct net_device *bind_dev = true_iif_dev ? true_iif_dev
                                                           : ingress_dev;

                if (bind_dev) {
                        u8 pid;
                        int prc = ask_dpaa_get_fman_port_id(bind_dev, &pid);

                        if (prc == 0) {
                                u8 expected = 0xff;
                                u8 winner;

                                /*
                                 * F-163 (2026-08-05): stash the real
                                 * ingress hwport id in the key so
                                 * ask_fe_build_key()/_v6() can prefix
                                 * it onto the FE-VM ehash key (matches
                                 * the vendor cdx.ko key format). This is
                                 * this flow's own physical ingress port,
                                 * independent of the FWD/REV pipeline-
                                 * slot winner decided below.
                                 */
                                key.port_id = pid;

                                /*
                                 * Race-free first-arrival latch:
                                 * cmpxchg succeeds (returns expected)
                                 * only if ask_flow_first_pid was
                                 * still 0xff at the moment of the
                                 * swap.  PR14z17 (2026-05-22): the
                                 * latch is now file-scope so the
                                 * FLOW_BLOCK_UNBIND handler can
                                 * reset it back to 0xff between nft
                                 * flowtable load cycles.
                                 */
                                if (cmpxchg(&ask_flow_first_pid, expected, pid) == expected)
                                        winner = pid;
                                else
                                        winner = READ_ONCE(ask_flow_first_pid);

                                /*
                                 * PR14z14 candidate fix (symmetric graft):
                                 * Ensure BOTH directions/ports are grafted.
                                 * We call ask_hw_port_bind separately for BOTH
                                 * the winner AND the current pid when they differ,
                                 * but the logic here handles current 'pid'.
                                 * Wait, ask_hw_port_bind handles idempotent binds,
                                 * but earlier we bound ONLY one side.
                                 */
                                __dir = (pid == winner) ? ASK_HW_DIR_FWD
                                                        : ASK_HW_DIR_REV;

                                prc = ask_hw_port_bind(pid, __dir, bind_dev);
                                if (prc) {
                                        if (prc == -EBUSY)
                                                ask_pr_dbg("flow_offload: REPLACE %s pid=%u dir=%u: pipeline busy, SW fallback\n",
                                                           netdev_name(bind_dev),
                                                           pid, __dir);
                                        else if (prc != -ENODEV)
                                                ask_pr_warn("flow_offload: REPLACE %s (pid %u dir %u) port-bind failed: %d\n",
                                                            netdev_name(bind_dev),
                                                            pid, __dir, prc);
                                        if (true_iif_dev)
                                                dev_put(true_iif_dev);
                                        return prc;
                                }
                        } else if (prc != -ENODEV && prc != -ERANGE) {
                                ask_pr_dbg("flow_offload: REPLACE port-id resolve(%s) failed: %d\n",
                                           netdev_name(bind_dev), prc);
                        }
                }
                if (true_iif_dev)
                        dev_put(true_iif_dev);

                rc = ask_flow_insert_owned(t, (u64)f->cookie, &key, oif,
                                           action_flags, __dir, generation,
                                           &hw_id);
        }
        if (rc == -EEXIST)
                return 0;
        if (rc == -ESTALE) {
                /* A3: a DESTROY superseded this REPLACE before publish; the
                 * flow was intentionally not offloaded and its HW rolled back.
                 * Report success — the desired end state (cookie gone) holds. */
                pr_info_ratelimited("ask: flow_offload: REPLACE cookie=0x%lx superseded by DESTROY — left in SW\n",
                                    f->cookie);
                return 0;
        }
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
        /*
         * T-M6-A3 (R4): close the rht-publish -> FE-install race window.
         * ask_flow_insert_owned() has published the SW entry and HW cookie,
         * but the per-key FE record is installed below. A DESTROY can race
         * between those two operations. Check ownership BEFORE FE install;
         * if tombstoned/superseded, remove only our generation and stop.
         */
        if (!ask_flow_gen_is_current(t, (u64)f->cookie, generation)) {
                (void)ask_flow_remove_owned(t, (u64)f->cookie, generation);
                return 0;
        }
        /* Keep offload ownership transactional: only keep the flow in the
         * HW-backed table if the FE-VM record was actually installed. */
        {
                /* T-M7-2 S1: recover the per-egress-interface TX FQID that
                 * ask_hw_flow_insert() resolved and saved in this hw_id's
                 * cookie, so the FE record forwards a HIT direct-to-wire.
                 *
                 * 2026-08-21 PANIC FIX (kernel NULL-deref in
                 * xdp_return_frame <- dpaa_cleanup_tx_fd): the sink FQID MUST
                 * be the per-egress NO-CONFIRM TX FQ (F-199, B0V=0). If the
                 * cookie lookup misses (fe_tx_fqid stays 0), the F-197/F-198
                 * fallback would enqueue the HIT to the port's CONFIRMED
                 * KG-default RX FQID (target_fqid, B0V=1). An FMan-forwarded
                 * HIT frame on a confirmed FQ generates a TX-confirm FD whose
                 * BMan buffer has no dpaa_eth_swbp -> dpaa_cleanup_tx_fd
                 * dereferences a garbage xdp_frame and panics the kernel.
                 * FAIL CLOSED instead: refuse the FE record and keep the flow
                 * in software (matches the resolver's fail-closed contract in
                 * ask_hw_resolve_oif_fqid). A HIT must never target a
                 * confirmed FQ. */
                u32 fe_tx_fqid = 0;
                int fqrc = ask_hw_flow_get_sink_fqid(hw_id, &fe_tx_fqid);

                if (fqrc || fe_tx_fqid == 0) {
                        ask_pr_warn("flow_offload: REPLACE cookie=0x%lx no no-confirm TX FQ (rc=%d fqid=0x%x) - keeping flow in SW\n",
                                    f->cookie, fqrc, fe_tx_fqid);
                        rc = -EAGAIN;
                } else {
                        rc = ask_fe_flow_insert(&key, ask_hw_get_enq_fe_off(),
                                                fe_tx_fqid);
                }
        }
        if (rc) {
                int rrc;

                ask_pr_warn("flow_offload: REPLACE rollback cookie=0x%lx fe_flow_insert=%d\n",
                            f->cookie, rc);
                /* Owned remove: only tear down if we are still the owner —
                 * a newer REPLACE that already superseded us must survive. */
                rrc = ask_flow_remove_owned(t, (u64)f->cookie, generation);
                if (rrc && rrc != -ESTALE)
                        ask_pr_warn("flow_offload: REPLACE rollback remove=%d cookie=0x%lx\n",
                                    rrc, f->cookie);
                return rc;
        }

        /*
         * T-M6-A3 (R4): a DESTROY may have raced during ask_fe_flow_insert().
         * If we are no longer the owner, delete the FE record we just wrote
         * and drop our SW entry so no orphan silicon record survives.
         */
        if (!ask_flow_gen_is_current(t, (u64)f->cookie, generation)) {
                ask_fe_flow_remove(&key);
                (void)ask_flow_remove_owned(t, (u64)f->cookie, generation);
                pr_info_ratelimited("ask: flow_offload: REPLACE cookie=0x%lx destroyed during FE install — record removed\n",
                                    f->cookie);
                return 0;
        }

        return 0;
}

static int ask_flow_offload_destroy(struct flow_cls_offload *f)
{
        struct ask_flow_table *t = ask_flow_default_table();
        u32 destroy_gen;
        int rc;

        if (!t)
                return -EOPNOTSUPP;

        /*
         * T-M6-A3: tombstone the cookie FIRST, before touching the pending
         * queue or the rht. This is the ordering that closes CR-004: once the
         * tombstone is set, any concurrent pending replay or neigh rebuild
         * that reads the generation will discard rather than resurrect, and a
         * REPLACE still in flight for this cookie fails its pre-publish gen
         * check. Snapshot the generation we are destroying so the owned-remove
         * below cannot tear down a NEWER REPLACE that raced in after us.
         */
        destroy_gen = ask_flow_gen_current(t, (u64)f->cookie);
        ask_flow_gen_tombstone(t, (u64)f->cookie);

        /*
         * PR14y: drop any pending deferred-insert entry for this cookie
         * BEFORE attempting the flow-table remove.  If the cookie was
         * still pending (never made it to ask_flow_insert), the rht
         * lookup below would return -ENOENT and we'd silently succeed,
         * but the pending entry would leak until module unload.
         */
        if (ask_flow_pending_drop_cookie((u64)f->cookie)) {
                ask_pr_dbg("flow_offload: DESTROY drop pending cookie=0x%lx\n",
                           f->cookie);
                /* Fall through — also try the rht remove in case the
                 * cookie was simultaneously promoted by the notifier. */
        }

        /* Fix B: capture the flow's 5-tuple BEFORE removing it from the rht,
         * so we can delete exactly its FE-VM silicon record (not clear-all). */
        {
                struct ask_flow *fl;
                struct ask_flow_key dkey;
                bool have_key = false;

                rcu_read_lock();
                fl = ask_flow_lookup(t, (u64)f->cookie);
                if (fl) {
                        dkey = fl->key;
                        have_key = true;
                }
                rcu_read_unlock();

                /*
                 * A3: owned remove bounded by the generation we tombstoned.
                 * If a newer REPLACE re-claimed this recycled cookie between
                 * our tombstone and here, remove_owned is a no-op and we must
                 * NOT delete its FE record — so gate ask_fe_flow_remove on the
                 * remove actually happening for our generation.
                 */
                rc = ask_flow_remove_owned(t, (u64)f->cookie, destroy_gen);
                if (rc == -ENOENT) {
                        ask_flow_gen_release(t, (u64)f->cookie);
                        return 0;
                }
                if (rc == -ESTALE) {
                        /* A newer REPLACE re-claimed this recycled cookie
                         * after our tombstone. It owns the flow and its FE
                         * record now — do NOT delete it, do NOT release the
                         * registry (the new owner's generation lives there). */
                        pr_info_ratelimited("ask: flow_offload: DESTROY cookie=0x%lx superseded by newer REPLACE — FE delete skipped\n",
                                            f->cookie);
                        return 0;
                }
                if (rc)
                        return rc;

                ask_pr_dbg("flow_offload: DESTROY cookie=0x%lx\n", f->cookie);
                /* Fix B: per-key FE-VM delete (F-117) — removes just this
                 * flow's silicon record instead of clearing every flow. */
                if (have_key)
                        ask_fe_flow_remove(&dkey);
                /* Registry entry no longer needed: the flow is gone and no
                 * worker can still be mid-replay for this generation. */
                ask_flow_gen_release(t, (u64)f->cookie);
        }
        return 0;
}

/*
 * T-M8-3: refresh a HW-backed flow's cached counters from the silicon FE
 * ehash record so offloaded flows (whose frames bypass the kernel) report
 * real packet/byte totals to nft and to `dump-flows`.
 *
 * Lifetime/locking: fman_pcd_fe_flow_get_stats() takes pcd->fe_lock (a mutex),
 * so it cannot run under rcu_read_lock. We therefore (1) snapshot the flow's
 * key/port/hw_backed under RCU, (2) drop RCU and read the silicon counters,
 * (3) re-look-up the cookie under RCU and store the result only if the SAME
 * live flow is still present — a concurrent DESTROY that freed the original
 * (call_rcu) simply means we skip the store. Process context (nft STATS
 * callback) only; the RCU-held genl dump path reads the last cached snapshot.
 */
static bool ask_fe_flow_refresh_hw_stats(struct ask_flow_table *t, u64 cookie,
                                         u64 *d_packets, u64 *d_bytes,
                                         bool *is_hw_backed)
{
        struct ask_flow_key key;
        u8 kbuf[ASK_FE_KEY_SIZE_DUAL];
        u64 hw_pkts = 0, hw_bytes = 0;
        struct ask_flow *fl;
        struct fman *fm;
        u32 generation;
        u8 port_id;

        *d_packets = 0;
        *d_bytes = 0;
        *is_hw_backed = false;

        rcu_read_lock();
        fl = ask_flow_lookup(t, cookie);
        if (!fl || !fl->hw_backed) {
                rcu_read_unlock();
                return false;
        }
        *is_hw_backed = true;
        key = fl->key;          /* value copy; safe to use after unlock */
        port_id = fl->key.port_id;
        generation = fl->generation;
        rcu_read_unlock();

        fm = ask_hw_get_fman();
        if (!fm)
                return false;

        ask_fe_build_key_dual(&key, kbuf);
        if (fman_pcd_fe_flow_get_stats(fm, port_id, kbuf,
                                       ASK_FE_KEY_SIZE_DUAL,
                                       &hw_pkts, &hw_bytes, NULL) != 0)
                return false;

        /*
         * Re-look-up and store only if the SAME live flow still owns the
         * cookie. The cookie is a recycled slab pointer; a DESTROY->REPLACE
         * during the getter's fe_lock window can hand it to a NEWER flow. The
         * generation stamp (immutable per flow) is the identity check every
         * other path here uses (ask_flow_gen_is_current); without it we would
         * write one flow's silicon totals onto an unrelated recycled flow.
         */
        rcu_read_lock();
        fl = ask_flow_lookup(t, cookie);
        if (fl && fl->hw_backed && fl->generation == generation) {
                ask_flow_set_hw_stats(fl, hw_pkts, hw_bytes,
                                      d_packets, d_bytes);
                rcu_read_unlock();
                return true;
        }
        rcu_read_unlock();
        return false;
}

static int ask_flow_offload_stats(struct flow_cls_offload *f)
{
        struct ask_flow_table *t = ask_flow_default_table();
        u64 packets = 0, bytes = 0, last_seen_ns = 0;
        u64 d_packets = 0, d_bytes = 0;
        bool is_hw_backed = false, refreshed;
        int rc;

        if (!t)
                return -EOPNOTSUPP;

        /*
         * T-M8-3: for a HW-backed flow, pull the silicon counters into the
         * cached absolute stats (for `dump-flows`/`get-flow`) and get back the
         * per-poll DELTA to feed the nft flowtable's ACCUMULATING
         * flow_stats_update(). For a software-fallback flow (no silicon
         * record) the cached ask_flow.stats already carry SW-path counters, so
         * fall back to reporting those.
         */
        refreshed = ask_fe_flow_refresh_hw_stats(t, (u64)f->cookie,
                                                 &d_packets, &d_bytes,
                                                 &is_hw_backed);

        rc = ask_flow_get_stats(t, (u64)f->cookie,
                                &packets, &bytes, &last_seen_ns);
        if (rc)
                return rc;

        if (!is_hw_backed) {
                /* SW-fallback: no per-poll HW delta exists. Report the cached
                 * absolute totals (legacy behaviour; these are the SW-path
                 * counts maintained by ask_flow_update_stats). */
                d_packets = packets;
                d_bytes = bytes;
        } else if (!refreshed) {
                /* HW-backed but the silicon read missed this poll (transient):
                 * keepalive only, no counter delta. */
                d_packets = 0;
                d_bytes = 0;
        }

        /*
         * PR14z3 (2026-05-19): keep offloaded flows alive against the
         * netfilter flowtable's idle-timeout sweeper.
         *
         * Root cause of the PR14z2 throughput regression (0.941 Gbps /
         * 2.08% CPU on the M2 gate): once a cookie is successfully
         * installed in silicon, ALL data-plane packets bypass the kernel
         * and never touch nf_flow_table_lookup(). The flowtable code in
         * net/netfilter/nf_flow_table_core.c uses `flow->timeout` —
         * driven exclusively by `flow_stats_update(..., lastused, ...)`
         * — to decide when to garbage-collect the entry. With no SW
         * packets and ask_flow_update_stats() never invoked from the
         * production datapath (it's only called from selftests, see
         * grep), `last_seen_ns` stays frozen at install time. The
         * sweeper then runs (~every 30 s on the established-flow path),
         * sees `lastused` lagging by >30 s, calls flow_offload_teardown()
         * → FLOW_CLS_DESTROY arrives, ask_flow_offload_destroy() removes
         * the silicon entry, and the next packet of the same conntrack
         * gets re-offered as FLOW_CLS_REPLACE. The PR14r dedup logic
         * blocks the re-install because the cookie pointer recycled
         * from slab matches one still in our rhashtable for ~1 RCU
         * grace period. Result: install→destroy→reinstall-blocked churn
         * at ~1 Hz, traffic falls to slow path, throughput collapses to
         * single-core forward rate.
         *
         * Fix: report `jiffies` (the kernel-time the flowtable code
         * actually compares against, see nf_flow_offload_gc_step()) as
         * the lastused value. As long as the HW slot still holds this
         * cookie — and we know it does because ask_flow_get_stats() just
         * returned 0 — the flow IS alive, regardless of whether we can
         * read a packet counter back from FMan PCD CC. The kernel
         * flowtable interprets this as a refresh and re-arms the timer.
         *
         * Side-note on units: the prior code passed last_seen_ns
         * (nanoseconds, from ktime_get_ns()) as `lastused`, but the
         * flow_stats_update() lastused parameter is documented in
         * jiffies (see include/net/flow_offload.h and the call sites
         * in mlx5/mlxsw/sfc drivers). Even if ask_flow_update_stats()
         * were wired into a polling loop, the unit was wrong. Passing
         * `jiffies` directly is both semantically correct and decouples
         * keep-alive from per-flow HW counter polling (which we will
         * still want eventually for accurate Gbps reporting via
         * `nft list flowtable`, but is orthogonal to M2 gate pass).
         */
        /* flow_stats_update() ACCUMULATES; cumulative silicon totals must
         * never be passed directly. d_* is current minus the previous poll's
         * per-flow baseline (or current after a silicon record reset). */
        flow_stats_update(&f->stats, d_bytes, d_packets, 0, jiffies,
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

        /*
         * TC_SETUP_CLSMATCHALL is owned by the DPAA1 ingress-policer block
         * handler (board patch 0104 dpaa_setup_tc_block_cb). Both that cb and
         * this ASK flow-offload cb are registered on the same ingress tcf
         * block; when a 'tc ... matchall action police' filter is added the tc
         * core fans TC_SETUP_CLSMATCHALL out to every registered cb. Decline it
         * silently here so the policer cb is the one that returns success —
         * otherwise this cb's -EOPNOTSUPP + a noisy warn is the only verdict
         * the core sees and the hardware policer never installs (skip_sw ->
         * EOPNOTSUPP). See 2026-08-23 policer root-cause.
         */
        if (type == TC_SETUP_CLSMATCHALL)
                return -EOPNOTSUPP;

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

/*
 * PR14z17 (2026-05-22): hoisted from FLOW_CLS_REPLACE function-scope
 * to file scope so the FLOW_BLOCK_UNBIND handler can reset it.
 *
 * `first_pid` latches the first ingress hwport_id observed via the
 * REPLACE path (cmpxchg from 0xff -> pid).  Subsequent REPLACEs on
 * the same pid get tagged ASK_HW_DIR_FWD; any other pid is tagged
 * ASK_HW_DIR_REV.  This is direction-agnostic with respect to
 * physical port wiring — either eth3 or eth4 can be "forward"
 * depending on which iperf stream lands first.
 *
 * On FLOW_BLOCK_UNBIND we reset to 0xff so a fresh nft flowtable
 * load can re-latch from scratch (without this, the second nft
 * `add flowtable` cycle in the same module lifetime would inherit
 * the previous session's FWD/REV assignment, mis-routing the bind
 * if the iperf direction was reversed between runs).
 *
 * 0xff = unlatched / no pipeline bound.
 */

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

                /*
                 * PR14z17 (2026-05-22): symmetric un-graft to repair
                 * the eth3/eth4 RX wedge that nft `delete table`
                 * leaves behind otherwise.
                 *
                 * Background: PR14z13's FLOW_CLS_REPLACE handler
                 * grafted ASK's CC tree onto the kernel-owned KG
                 * scheme by writing KGSE_CCBS, and patch 0043's
                 * RMW of kgse_mode rerouted KG -> CC engine.
                 * Without an inverse, the next REPLACE-less event
                 * (BLOCK_UNBIND fires when the flowtable disappears
                 * — typically via nft `delete table inet …`) tore
                 * down the block_cb but left silicon pointing at a
                 * soon-to-be-destroyed cc_tree, wedging every
                 * subsequent unhashed frame on that port.  Only a
                 * full reboot recovered.
                 *
                 * Fix: resolve this netdev's hwport_id and call
                 * ask_hw_port_unbind() to ungraft (kgse_mode NIA
                 * restored to ENQUEUE_KG_DFLT_NIA + KGSE_CCBS=0 in
                 * a single AR-flushed indirect window per patch
                 * 0043) and destroy the lazily-created cc_v4_tcp
                 * node.  Idempotent if the port is not currently
                 * bound (the common case for any non-DPAA netdev
                 * that landed here via the indr path).
                 *
                 * Reset the first-arrival latch so the next BIND
                 * cycle can re-discover direction from scratch.
                 * This is safe because flow cookies that survived
                 * UNBIND (none expected; FLOW_CLS_DESTROY fires
                 * before UNBIND for every cookie) cannot be
                 * resurrected — the SW table entry is gone and
                 * the silicon CC slot has been tombstoned.
                 */
                if (dev) {
                        u8 pid;
                        int prc = ask_dpaa_get_fman_port_id(dev, &pid);

                        if (prc == 0) {
                                int urc = ask_hw_port_unbind(pid);

                                if (urc && urc != -ENODEV)
                                        ask_pr_warn("flow_offload: UNBIND %s (pid 0x%02x) port-unbind failed: %d\n",
                                                    netdev_name(dev), pid, urc);
                        } else if (prc != -ENODEV && prc != -ERANGE) {
                                ask_pr_dbg("flow_offload: UNBIND port-id resolve(%s) failed: %d\n",
                                           netdev_name(dev), prc);
                        }
                }
                WRITE_ONCE(ask_flow_first_pid, 0xff);

                ask_pr_dbg("flow_offload: UNBIND %s — un-grafted + first_pid latch reset\n",
                           netdev_name(dev));
                return 0;

        default:
                return -EOPNOTSUPP;
        }
}
EXPORT_SYMBOL_GPL(ask_flow_offload_setup_tc);

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
/* arrives.  We accept any netdev the indr core hands us and dispatch         */
/* TC_SETUP_BLOCK + TC_SETUP_FT to the same ask_flow_offload_setup_tc()       */
/* helper.  The binder-type widening for FT was done above (PR14n change to   */
/* ask_flow_offload_setup_tc).                                                */
/*                                                                            */
/* Filtering by netdev: rather than maintaining a separate "is this our      */
/* netdev" predicate (the bnxt / mlx5 / nfp drivers each carry a private one */
/* keyed on their tunnel-device type), we let the per-flow REPLACE handler   */
/* self-filter.  In practice the indr core only reaches us when nft has      */
/* matched a device whose flowtable is bound, so the check is rarely         */
/* exercised; when it is, ask_flow_offload_setup_tc silently no-ops on a     */
/* non-dpaa netdev because the per-block priv allocation succeeds but the    */
/* block_cb's REPLACE handler then fails to resolve the ingress BMI port id  */
/* (-ENODEV) and the flow stays SW-only (same graceful degradation as PR14j  */
/* ingress-only bind).                                                        */
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

/* Forward decl: ask_flow_offload_init's PR14y error path needs to call the
 * indr unregister, which takes the release helper that is defined below. */
static void ask_flow_indr_release(void *cb_priv);

/*
 * Backend slot wrapper for dpaa_setup_tc_flow_block().  When
 * nf_flow_table_offload_setup() calls ndo_setup_tc(dev, TC_SETUP_FT),
 * the in-tree dpaa driver routes to dpaa_setup_tc_flow_block() (patch
 * 0145), which looks up the registered ops and calls setup_tc_block().
 * This wrapper delegates to ask_flow_indr_setup_cb().
 */
static int ask_flow_offload_setup_tc_block(struct net_device *dev,
                                           struct flow_block_offload *fbo)
{
        return ask_flow_indr_setup_cb(dev, NULL, NULL, TC_SETUP_FT,
                                      fbo, NULL, NULL);
}

static const struct dpaa_flow_offload_ops ask_flow_offload_ops = {
        .owner          = THIS_MODULE,
        .setup_tc_block = ask_flow_offload_setup_tc_block,
};

int ask_flow_offload_init(void)
{
        int rc;

        /*
         * Single-image entry: ask.ko registers TWO paths for flow offload:
         *
         * 1. flow_indr_dev_register() — the generic kernel indr path (used
         *    when ndo_setup_tc is NULL on the netdev).
         * 2. dpaa_register_flow_offload_handler() — the DPAA1 backend slot
         *    (patch 0145), used when ndo_setup_tc IS present (which DPAA1
         *    has via board patch 0104).  The slot bridges the gap between
         *    nf_flow_table_offload_setup()'s ndo_setup_tc(TC_SETUP_FT) and
         *    our flow_block_cb chain.
         *
         * Path 1 handles non-DPAA netdevs; path 2 handles DPAA1 eth ports.
         * A non-DPAA netdev degrades gracefully to SW-only when the REPLACE
         * handler cannot resolve an ingress BMI port id.
         */
        rc = flow_indr_dev_register(ask_flow_indr_setup_cb, NULL);
        if (rc) {
                ask_pr_err("flow_offload: flow_indr_dev_register failed: %d\n", rc);
                return rc;
        }

        rc = dpaa_register_flow_offload_handler(&ask_flow_offload_ops);
        if (rc) {
                ask_pr_err("flow_offload: dpaa_register_flow_offload_handler failed: %d\n",
                           rc);
                flow_indr_dev_unregister(ask_flow_indr_setup_cb, NULL,
                                         ask_flow_indr_release);
                return rc;
        }

        /*
         * T-M6-3: the NETEVENT_NEIGH_UPDATE notifier now lives in ask_neigh.c
         * (mainline-aligned single owner for neigh events), which calls
         * ask_flow_neigh_resolved() (deferred-insert drain) and
         * ask_flow_neigh_mac_changed() (stale-MAC rebuild) from a workqueue in
         * process context.  Only the PR14z9 active-poll fallback stays here.
         */

        /*
         * PR14z9 (2026-05-19): arm the active-poll fallback that re-runs
         * neigh_lookup() on the pending list every 100 ms.  Defends
         * against any case where NETEVENT_NEIGH_UPDATE fails to fire
         * (or fails to match our filter) for cookies we deferred.
         */
        INIT_DELAYED_WORK(&ask_flow_pending_poll_work, ask_flow_pending_poll_fn);
        schedule_delayed_work(&ask_flow_pending_poll_work,
                              msecs_to_jiffies(ASK_FLOW_PENDING_POLL_INTERVAL_MS));

        ask_pr_info("flow_offload: ready (PR14y deferred-insert + PR14z9 active poll %d ms + PR14z10 dual-ifindex match + PR14z11 cookie-recovered next-hop)\n",
                    ASK_FLOW_PENDING_POLL_INTERVAL_MS);
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
        struct ask_flow_pending *p, *ptmp;

        /*
         * PR14z9: stop the active poller before unregistering the
         * netevent notifier and draining the list.  cancel_delayed_work_sync
         * guarantees no poll callback is in flight when we return.
         */
        cancel_delayed_work_sync(&ask_flow_pending_poll_work);

        dpaa_unregister_flow_offload_handler(&ask_flow_offload_ops);
        /* T-M6-3: netevent notifier unregistration moved to ask_neigh_exit(). */

        /*
         * PR14y: drain any still-pending deferred-insert entries.  The
         * netevent notifier is already unregistered so no new entries
         * can land; the kernel SW flowtable will continue to carry the
         * flow until userspace tears down the rule.  Just free memory.
         */
        spin_lock_bh(&ask_flow_pending_lock);
        list_for_each_entry_safe(p, ptmp, &ask_flow_pending_list, node) {
                list_del(&p->node);
                ask_flow_pending_count--;
                kfree(p);
        }
        spin_unlock_bh(&ask_flow_pending_lock);

        ask_pr_info("flow_offload: PR14y stats deferred=%d resolved=%d overflow=%d  PR14z9 poll runs=%d poll-resolved=%d\n",
                    atomic_read(&ask_flow_pending_deferred),
                    atomic_read(&ask_flow_pending_resolved),
                    atomic_read(&ask_flow_pending_overflow),
                    atomic_read(&ask_flow_pending_poll_runs),
                    atomic_read(&ask_flow_pending_poll_resolved));

        flow_indr_dev_unregister(ask_flow_indr_setup_cb, NULL,
                                 ask_flow_indr_release);

        spin_lock(&ask_flow_block_priv_lock);
        list_for_each_entry_safe(e, tmp, &ask_flow_block_priv_list, node) {
                list_del(&e->node);
                kfree(e);
        }
        spin_unlock(&ask_flow_block_priv_lock);

        ask_pr_dbg("flow_offload: exit\n");
}
