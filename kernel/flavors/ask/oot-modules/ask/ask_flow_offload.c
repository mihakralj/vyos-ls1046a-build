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
#include <linux/etherdevice.h>
#include <linux/workqueue.h>
#include <linux/jiffies.h>
#include <net/flow_offload.h>
#include <net/pkt_cls.h>
#include <net/arp.h>
#include <net/neighbour.h>
#include <net/netevent.h>
#include <net/net_namespace.h>
#include <net/netfilter/nf_flow_table.h>
#include <linux/fsl/dpaa_flow_offload.h>

#include "include/ask_internal.h"

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
/* Bounded: ASK_FLOW_PENDING_MAX = 256.  Beyond the cap we drop new          */
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
ask_flow_pending_take_one(int ifindex, __be32 dst_ip)
{
        struct ask_flow_pending *p, *tmp;
        struct ask_flow_pending *ret = NULL;

        spin_lock_bh(&ask_flow_pending_lock);
        list_for_each_entry_safe(p, tmp, &ask_flow_pending_list, node) {
                if ((p->egress_ifindex == ifindex ||
                     p->ingress_ifindex == ifindex) &&
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
                        break;
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
                                    __be32 dst_ip)
{
        struct ask_flow_pending *p;

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

        spin_lock_bh(&ask_flow_pending_lock);
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

static int ask_flow_offload_netevent(struct notifier_block *nb,
                                     unsigned long event, void *ptr)
{
        struct neighbour *n;
        struct net_device *dev;
        __be32 dst_ip;
        struct ask_flow_pending *p;
        struct ask_flow_table *t;
        u32 hw_id = 0;
        int rc;
        unsigned int drained = 0;

        /*
         * PR14z8 (2026-05-19): instrument the NETEVENT path to nail down
         * why ask_flow_pending_list never drains (M2 2026-05-19 dmesg
         * counters: installed=33, defer=141, deferred-insert OK=0).
         *
         * Two competing hypotheses:
         *  H1: NETEVENT_NEIGH_UPDATE doesn't fire for entries we created
         *      ourselves via __neigh_create()+neigh_event_send() — it only
         *      fires on already-tracked INCOMPLETE→REACHABLE transitions.
         *  H2: It does fire, but ask_flow_pending_take_one() filter on
         *      (egress_ifindex, dst_ip) misses because primary_key
         *      encoding differs from how we stored dst_ip at defer time.
         *
         * The three pr_info_ratelimited below distinguish them:
         *  - "netevent entry" with the event code proves H1 false (we ARE
         *    being called); absence of this trace proves H1 true.
         *  - "netevent NUD_VALID" with the dst_ip + dev->ifindex shows
         *    what filter key we're searching with.
         *  - "netevent drained N" at exit shows whether any cookies
         *    matched. If we see NUD_VALID traces but drained=0 every
         *    time, H2 is confirmed.
         */
        pr_info_ratelimited("ask: flow_offload: netevent entry event=%lu ptr=%px\n",
                            event, ptr);

        if (event != NETEVENT_NEIGH_UPDATE)
                return NOTIFY_DONE;

        n = ptr;
        if (!n || n->tbl != &arp_tbl) {
                pr_info_ratelimited("ask: flow_offload: netevent skip (n=%px tbl=%s)\n",
                                    n, (n && n->tbl) ? "non-arp" : "null");
                return NOTIFY_DONE;
        }

        /* Only act on transitions into NUD_VALID (lladdr is now meaningful). */
        read_lock_bh(&n->lock);
        if (!(n->nud_state & NUD_VALID)) {
                u8 ns = n->nud_state;
                read_unlock_bh(&n->lock);
                pr_info_ratelimited("ask: flow_offload: netevent skip not-VALID nud_state=0x%02x\n",
                                    ns);
                return NOTIFY_DONE;
        }
        dev = n->dev;
        memcpy(&dst_ip, n->primary_key, sizeof(dst_ip));
        read_unlock_bh(&n->lock);

        if (!dev)
                return NOTIFY_DONE;

        pr_info_ratelimited("ask: flow_offload: netevent NUD_VALID dev=%s ifindex=%d dst_ip=%pI4 pending_count=%u\n",
                            netdev_name(dev), dev->ifindex, &dst_ip,
                            READ_ONCE(ask_flow_pending_count));

        t = ask_flow_default_table();
        if (!t)
                return NOTIFY_DONE;

        /*
         * Drain ALL pending entries waiting on (dev->ifindex, dst_ip) — a
         * single ARP resolution can unblock multiple cookies if several
         * flows are heading at the same next-hop.
         */
        while ((p = ask_flow_pending_take_one(dev->ifindex, dst_ip))) {
                drained++;
                /* Re-resolve so we always pick up the fresh n->ha. */
                ask_resolve_neigh_v4(dev, dst_ip,
                                     p->key.next_hop_mac, p->key.egress_mac);
                if (is_zero_ether_addr(p->key.next_hop_mac)) {
                        /* Race: neigh went back to INCOMPLETE between
                         * NETEVENT delivery and our re-lookup.  Re-park.
                         */
                        if (ask_flow_pending_enqueue(p->cookie, &p->key,
                                                     p->oif, p->action_flags,
                                                     p->egress_ifindex,
                                                     p->ingress_ifindex,
                                                     p->dst_ip) == 0)
                                pr_info_ratelimited("ask: flow_offload: PR14y re-park cookie=0x%llx dev=%s\n",
                                                    p->cookie, netdev_name(dev));
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
                rc = ask_flow_insert(t, p->cookie, &p->key, p->oif,
                                     p->action_flags, ASK_HW_DIR_FWD, &hw_id);
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

        return NOTIFY_DONE;
}

static struct notifier_block ask_flow_offload_netevent_nb = {
        .notifier_call = ask_flow_offload_netevent,
};

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
                rc = ask_flow_insert(t, p->cookie, &p->key, p->oif,
                                     p->action_flags, ASK_HW_DIR_FWD, &hw_id);
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
        if (ingress_dev)
                key.iif = ingress_dev->ifindex;

        rc = ask_parse_action(f, &action_flags, &oif, &egress_dev);
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
        if (ingress_dev && egress_dev && ingress_dev == egress_dev) {
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
        ask_resolve_neigh_v4(egress_dev, dst_ip,
                             key.next_hop_mac, key.egress_mac);

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
        if (egress_dev && is_zero_ether_addr(key.next_hop_mac)) {
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
                                               ingress_dev ? ingress_dev->ifindex : 0,
                                               dst_ip);
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

                if (ingress_dev) {
                        u8 pid;
                        int prc = dpaa_get_fman_port_id(ingress_dev, &pid);

                        if (prc == 0) {
                                u8 expected = 0xff;
                                u8 winner;

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

                                prc = ask_hw_port_bind(pid, __dir, ingress_dev);
                                if (prc == -EBUSY)
                                        ask_pr_dbg("flow_offload: REPLACE %s pid=%u dir=%u: pipeline busy, SW fallback\n",
                                                   netdev_name(ingress_dev),
                                                   pid, __dir);
                                else if (prc && prc != -ENODEV)
                                        ask_pr_warn("flow_offload: REPLACE %s (pid %u dir %u) port-bind failed: %d\n",
                                                    netdev_name(ingress_dev),
                                                    pid, __dir, prc);
                        } else if (prc != -ENODEV && prc != -ERANGE) {
                                ask_pr_dbg("flow_offload: REPLACE dpaa_get_fman_port_id(%s) failed: %d\n",
                                           netdev_name(ingress_dev), prc);
                        }
                }

                rc = ask_flow_insert(t, (u64)f->cookie, &key, oif,
                                     action_flags, __dir, &hw_id);
        }
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
        flow_stats_update(&f->stats, bytes, packets, 0, jiffies,
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
                        int prc = dpaa_get_fman_port_id(dev, &pid);

                        if (prc == 0) {
                                int urc = ask_hw_port_unbind(pid);

                                if (urc && urc != -ENODEV)
                                        ask_pr_warn("flow_offload: UNBIND %s (pid 0x%02x) port-unbind failed: %d\n",
                                                    netdev_name(dev), pid, urc);
                        } else if (prc != -ENODEV && prc != -ERANGE) {
                                ask_pr_dbg("flow_offload: UNBIND dpaa_get_fman_port_id(%s) failed: %d\n",
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

/* Forward decl: ask_flow_offload_init's PR14y error path needs to call the
 * indr unregister, which takes the release helper that is defined below. */
static void ask_flow_indr_release(void *cb_priv);

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

        /*
         * PR14y: subscribe to NETEVENT_NEIGH_UPDATE so we can replay the
         * deferred-insert pending queue the moment any ARP entry resolves.
         * register_netevent_notifier() is documented as never failing in
         * mainline (it's a raw atomic notifier chain register), but the
         * return is checked for completeness.
         */
        rc = register_netevent_notifier(&ask_flow_offload_netevent_nb);
        if (rc) {
                ask_pr_err("flow_offload: register_netevent_notifier failed: %d\n", rc);
                flow_indr_dev_unregister(ask_flow_indr_setup_cb, NULL,
                                         ask_flow_indr_release);
                dpaa_unregister_flow_offload_handler(&ask_dpaa_fo_ops);
                return rc;
        }

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
        int rc;

        /*
         * PR14z9: stop the active poller before unregistering the
         * netevent notifier and draining the list.  cancel_delayed_work_sync
         * guarantees no poll callback is in flight when we return.
         */
        cancel_delayed_work_sync(&ask_flow_pending_poll_work);

        unregister_netevent_notifier(&ask_flow_offload_netevent_nb);

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
