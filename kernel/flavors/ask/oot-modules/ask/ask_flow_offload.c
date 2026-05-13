 // SPDX-License-Identifier: GPL-2.0
/*
 * ASK 2.0 - flow_offload subsystem (PR8 / M1.4)
 *
 * Implements the `flow_block_cb` dispatcher that the in-tree dpaa
 * patch (PR11/M2.2 — `0002-dpaa-eth-flow-block.patch`) plugs into the
 * `dpaa_setup_tc()` path. Until that patch lands, ask_flow_offload
 * exposes the same callback as a public symbol so:
 *
 *   - PR9 kunit can drive it against a synthetic netdev,
 *   - PR15 (per-flow-type expansion) can layer parsers on top,
 *   - the in-tree patch PR11 only needs to invoke the existing
 *     `ask_flow_offload_setup_tc()` from `dpaa_setup_tc()` — all the
 *     translation logic stays in this OOT file.
 *
 * Translation shape (FLOW_CLS_* → ask_flow_*):
 *
 *   FLOW_CLS_REPLACE: parse `flow_cls_offload->rule->match` into
 *     struct ask_flow_key, parse `->rule->action` into action_flags +
 *     oif, then call ask_flow_insert(default_table, cookie, key, oif,
 *     flags, &hw_id). The cookie is the `flow_cls_offload->cookie`
 *     unsigned long handed by the netfilter/tc core. The hw_id is
 *     fake (atomic counter) until PR14 wires the real hostcmd path;
 *     we still return 0 on success so the upper layer caches our
 *     willingness to offload.
 *
 *   FLOW_CLS_DESTROY: ask_flow_remove(table, cookie). -ENOENT is
 *     swallowed (the upper layer occasionally double-destroys when
 *     a connection ages out concurrently with an explicit nft delete).
 *
 *   FLOW_CLS_STATS: ask_flow_get_stats(table, cookie, &p, &b, &lns)
 *     and stuff the result into `flow_cls_offload->stats` via
 *     `flow_stats_update()`. nf_flow_table polls this every second to
 *     decide whether to keep offloading.
 *
 * What's NOT in PR8:
 *   - real hardware insert (PR14)
 *   - the in-tree dpaa patch that wires `ask_flow_offload_setup_tc()`
 *     into `dpaa_setup_tc()` (PR11)
 *   - tc-flower-specific quirks beyond the shared FLOW_CLS_* dispatch
 *     (those land in PR15 once we have a hardware target to validate
 *     against)
 *   - IPv6 / multicast / bridge parsers (PR15a/c/d/e)
 *
 * Concurrency:
 *   The callback is invoked from process context (nft commit path) or
 *   softirq (nf_flow_table refresh). We rely on ask_flow.c's RCU/
 *   rhashtable concurrency guarantees — the callback itself takes no
 *   private locks.
 *
 * Spec ref: §4.3 (flow_block_cb integration), §11.1 (M1 acceptance
 * gate), §16 #6 (recommended bring-up order: dummy netdev first).
 */

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/in.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/list.h>
#include <linux/spinlock.h>
#include <linux/netdevice.h>
#include <net/flow_offload.h>
#include <net/pkt_cls.h>
#include <linux/fsl/dpaa_flow_offload.h>

#include "include/ask_internal.h"

/* ------------------------------------------------------------------------- */
/* Per-block state                                                            */
/*                                                                            */
/* The flow_block_offload core hands us an opaque `void *cb_priv` we may use  */
/* to thread per-block context. PR8 keeps it minimal: a back-pointer to the   */
/* netdev for diagnostic logging only. PR15 will extend this to carry a       */
/* per-fman flow-table pointer once multi-fman support arrives.               */
/* ------------------------------------------------------------------------- */

struct ask_flow_block_priv {
struct net_device *dev;
};

/*
 * We track every block we have bound to so unregister can find them by
 * netdev. The list is small (one entry per dpaa netdev — at most 5 on
 * LS1046A) so a flat list with a spinlock is fine; no need for an
 * rhashtable here.
 */
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

/* ------------------------------------------------------------------------- */
/* Match parsing                                                              */
/*                                                                            */
/* PR8 supports IPv4 5-tuple + IIF only. IPv6 lands in PR15a, multicast in    */
/* PR15c/d, bridge in PR15e. Returns 0 on success, -EOPNOTSUPP if the rule    */
/* uses a key element we don't yet handle (the upper layer interprets that    */
/* as "keep in software", not as a hard error).                               */
/* ------------------------------------------------------------------------- */

static int ask_parse_match_v4(struct flow_cls_offload *f,
      struct ask_flow_key *key)
{
struct flow_rule *rule = flow_cls_offload_flow_rule(f);
struct flow_dissector *d = rule->match.dissector;

memset(key, 0, sizeof(*key));
key->l3_proto = ASK_FLOW_L3_IPV4;

/* Control / basic — required for any rule we accept. */
if (flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_BASIC)) {
struct flow_match_basic m;

flow_rule_match_basic(rule, &m);
if (m.key->n_proto != htons(ETH_P_IP))
return -EOPNOTSUPP;
key->l4_proto = m.key->ip_proto;
} else {
return -EOPNOTSUPP;
}

/* Source / dest IPv4. */
if (flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_IPV4_ADDRS)) {
struct flow_match_ipv4_addrs m;

flow_rule_match_ipv4_addrs(rule, &m);
memcpy(&key->src_ip[0], &m.key->src, 4);
memcpy(&key->dst_ip[0], &m.key->dst, 4);
} else {
return -EOPNOTSUPP;
}

/* L4 ports — TCP or UDP only. */
if (key->l4_proto == IPPROTO_TCP || key->l4_proto == IPPROTO_UDP) {
struct flow_match_ports m;

if (!flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_PORTS))
return -EOPNOTSUPP;
flow_rule_match_ports(rule, &m);
key->sport = m.key->src;
key->dport = m.key->dst;
}

/* Optional VLAN. */
if (flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_VLAN)) {
struct flow_match_vlan m;

flow_rule_match_vlan(rule, &m);
key->vlan_id = m.key->vlan_id;
}

/* Suppress an unused-variable warning if d isn't otherwise read. */
(void)d;
return 0;
}

/* ------------------------------------------------------------------------- */
/* Action parsing                                                             */
/*                                                                            */
/* Translate flow_action_entry list into ask action_flags + oif. Anything we  */
/* don't yet implement (rewrite SRC/DST, mangle, mirror, etc.) returns        */
/* -EOPNOTSUPP so the upper layer stays in software for that flow.            */
/* ------------------------------------------------------------------------- */

static int ask_parse_action(struct flow_cls_offload *f,
    u32 *out_action_flags, u32 *out_oif)
{
struct flow_rule *rule = flow_cls_offload_flow_rule(f);
struct flow_action_entry *act;
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
break;
case FLOW_ACTION_VLAN_PUSH:
flags |= ASK_ACT_VLAN_PUSH;
break;
case FLOW_ACTION_VLAN_POP:
flags |= ASK_ACT_VLAN_POP;
break;
case FLOW_ACTION_CSUM:
/* Hardware always recomputes — silently accept. */
break;
case FLOW_ACTION_PTYPE:
case FLOW_ACTION_ACCEPT:
break;
default:
/* Unknown action → keep the flow in software. */
return -EOPNOTSUPP;
}
}

if (oif == 0)
return -EOPNOTSUPP;

*out_action_flags = flags;
*out_oif = oif;
return 0;
}

/* ------------------------------------------------------------------------- */
/* FLOW_CLS_* dispatch                                                        */
/* ------------------------------------------------------------------------- */

static int ask_flow_offload_replace(struct flow_cls_offload *f)
{
struct ask_flow_table *t = ask_flow_default_table();
struct ask_flow_key key;
u32 hw_id = 0;
u32 action_flags = 0;
u32 oif = 0;
int rc;

if (!t)
return -EOPNOTSUPP;

rc = ask_parse_match_v4(f, &key);
if (rc)
return rc;

rc = ask_parse_action(f, &action_flags, &oif);
if (rc)
return rc;

rc = ask_flow_insert(t, (u64)f->cookie, &key, oif,
     action_flags, &hw_id);
if (rc == -EEXIST) {
/*
 * Upper layer occasionally re-issues REPLACE for an
 * already-installed flow when an attribute is touched.
 * Treat as success — we already track the cookie and the
 * key is immutable in 5-tuple offload.
 */
return 0;
}
if (rc)
return rc;

ask_pr_dbg("flow_offload: REPLACE cookie=0x%lx hw_id=%u oif=%u\n",
   f->cookie, hw_id, oif);
return 0;
}

static int ask_flow_offload_destroy(struct flow_cls_offload *f)
{
struct ask_flow_table *t = ask_flow_default_table();
int rc;

if (!t)
return -EOPNOTSUPP;

rc = ask_flow_remove(t, (u64)f->cookie);
if (rc == -ENOENT) {
/* Double-destroy race — not a real error. */
return 0;
}
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
 * Returning -EOPNOTSUPP for an unhandled command is the documented pattern
 * (see net/sched/cls_flower.c) — the core falls back to software.
 */
int ask_flow_offload_setup_tc_block_cb(enum tc_setup_type type, void *type_data,
       void *cb_priv)
{
struct flow_cls_offload *f = type_data;

if (type != TC_SETUP_CLSFLOWER)
return -EOPNOTSUPP;

switch (f->command) {
case FLOW_CLS_REPLACE:
return ask_flow_offload_replace(f);
case FLOW_CLS_DESTROY:
return ask_flow_offload_destroy(f);
case FLOW_CLS_STATS:
return ask_flow_offload_stats(f);
default:
return -EOPNOTSUPP;
}
}
EXPORT_SYMBOL_GPL(ask_flow_offload_setup_tc_block_cb);

/* ------------------------------------------------------------------------- */
/* Public block-bind helper called from the in-tree dpaa patch (PR11) and    */
/* from the kunit synthetic-netdev path.                                      */
/*                                                                            */
/* The caller provides a flow_block_offload* (which the netdev's setup_tc    */
/* delivered) and the netdev itself (so we can stash it in cb_priv for        */
/* logging).                                                                  */
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
ask_pr_dbg("flow_offload: BIND %s\n", netdev_name(dev));
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
/* dpaa_flow_offload_ops backend registration (in-tree patch PR11/M2.2).     */
/*                                                                            */
/* The dpaa_eth driver carries a single-slot RCU-protected registration       */
/* point (include/linux/fsl/dpaa_flow_offload.h, added by                    */
/* 0002-dpaa-eth-flow-block.patch). When ask.ko loads, we plug ourselves     */
/* in; on rmmod we unplug. dpaa_setup_tc() RCU-derefs the slot for every     */
/* TC_SETUP_BLOCK and dispatches to ops->setup_tc_block (= the function      */
/* below, which is just a thin wrapper around ask_flow_offload_setup_tc()).  */
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
/* Lifecycle                                                                  */
/* ------------------------------------------------------------------------- */

int ask_flow_offload_init(void)
{
int rc;

rc = dpaa_register_flow_offload_handler(&ask_dpaa_fo_ops);
if (rc == -ENODEV) {
/*
 * Built without CONFIG_FSL_DPAA, or the dpaa driver did not
 * load. ask.ko stays usable for kunit and for genl-only
 * surface; the synthetic-netdev test path exercises
 * ask_flow_offload_setup_tc_block_cb() directly.
 */
ask_pr_info("flow_offload: dpaa backend unavailable (-ENODEV); "
    "running standalone\n");
return 0;
}
if (rc) {
ask_pr_err("flow_offload: dpaa_register_flow_offload_handler "
   "failed: %d\n", rc);
return rc;
}

ask_pr_info("flow_offload: ready (registered with dpaa_eth)\n");
return 0;
}

void ask_flow_offload_exit(void)
{
struct ask_flow_block_priv_entry *e, *tmp;
int rc;

/*
 * Drop the dpaa registration first so dpaa_setup_tc() stops
 * dispatching new TC_SETUP_BLOCK events to us. The unregister
 * path inside dpaa_eth synchronize_rcu()s before returning, so
 * by the time it completes no inflight dispatcher is still
 * holding a pointer to ask_dpaa_fo_ops.
 */
rc = dpaa_unregister_flow_offload_handler(&ask_dpaa_fo_ops);
if (rc && rc != -ENODEV && rc != -EINVAL)
ask_pr_warn("flow_offload: dpaa_unregister_flow_offload_handler "
    "failed: %d\n", rc);

/*
 * Ordinarily every block_cb gets torn down by the netdev's
 * UNBIND path before module exit. This sweep is defensive — if
 * the in-tree patch (PR11) ever fails to unbind on netdev
 * teardown, we still leak no memory at rmmod time.
 */
spin_lock(&ask_flow_block_priv_lock);
list_for_each_entry_safe(e, tmp, &ask_flow_block_priv_list, node) {
list_del(&e->node);
kfree(e);
}
spin_unlock(&ask_flow_block_priv_lock);

ask_pr_dbg("flow_offload: exit\n");
}
