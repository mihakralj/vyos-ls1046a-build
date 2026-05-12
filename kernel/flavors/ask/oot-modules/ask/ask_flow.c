// SPDX-License-Identifier: GPL-2.0
/*
 * ASK 2.0 - software flow table (PR7 / M1.3)
 *
 * Lock-free RCU lookup, single-writer-style insert/remove backed by
 * the linux/rhashtable internal locks. Per-flow stats live in a
 * u64_stats_sync seqcount so 32-bit readers cannot tear a 64-bit
 * counter mid-update.
 *
 * No hardware in this PR: hw_flow_id is faked from an atomic counter
 * incremented on every successful insert. PR14 (M2.5) replaces the
 * fake with the value the 210 microcode hands back from
 * OP_FLOW_INSERT_V4_TCP. The wire path is already shaped by PR6's
 * ask_hostcmd encoders; this PR only owns the software bookkeeping
 * that sits between flow_block_cb (PR8) and the hostcmd sender.
 *
 * Concurrency model:
 *
 *   - Lookup is RCU-only. Caller MUST be in an rcu_read_lock()
 *     section. No allocation, no sleeping.
 *
 *   - Insert/remove use the rhashtable's per-bucket locks. Safe to
 *     call concurrently. Removal frees the entry via call_rcu() so
 *     a concurrent reader holding the pointer through a grace period
 *     stays valid.
 *
 *   - Walks (DUMP_FLOWS) hold the rht walker. The callback runs
 *     under spinlock; it must be allocation-light. The genl dumpit
 *     handler (PR8 onward) builds its reply skb in the doit path
 *     based on a snapshot the callback fills.
 *
 *   - Stats reads use u64_stats_fetch_begin / retry. Stats writes
 *     wrap the field updates in u64_stats_update_begin / end.
 *     ksoftirqd-context updates from the 1Hz poller (PR15h) and
 *     userspace reads via genl coexist safely.
 *
 * The cookie used as the rhashtable key is a u64 chosen by the
 * caller. For nf_flow_table integration (PR8) the cookie is the
 * `unsigned long` priv pointer the netfilter core hands us. For the
 * PR7 kunit harness it's an arbitrary u64 the test picks. There is
 * no semantic meaning beyond uniqueness.
 */

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/atomic.h>
#include <linux/rhashtable.h>
#include <linux/rcupdate.h>
#include <linux/u64_stats_sync.h>
#include <linux/ktime.h>
#include <linux/errno.h>

#include "include/ask_internal.h"

/* -------------------------------------------------------------------------
 * rhashtable parameters
 *
 * Key: 8-byte cookie at offset offsetof(struct ask_flow, cookie).
 * Hash function: jhash2 over the cookie.
 *
 * automatic_shrinking lets the table shrink under sustained removal
 * pressure (matters when nft flushes a large flow set).
 *
 * head_offset positions rhash_head where the hashtable expects it
 * inside struct ask_flow.
 * ------------------------------------------------------------------------- */

static const struct rhashtable_params ask_flow_rht_params = {
.head_offset    = offsetof(struct ask_flow, node),
.key_offset     = offsetof(struct ask_flow, cookie),
.key_len        = sizeof(u64),
.automatic_shrinking = true,
.min_size       = 16,
};

/* -------------------------------------------------------------------------
 * Module-global default table.
 *
 * PR7 has no per-fman concept yet. M2 will replace this with a per-fman
 * struct allocated when the dpaa platform driver probes. The accessor
 * ask_flow_default_table() lets PR8's flow_block_cb find the table
 * without depending on the eventual layering.
 * ------------------------------------------------------------------------- */

static struct ask_flow_table ask_flow_global;
static bool ask_flow_global_initialised;

struct ask_flow_table *ask_flow_default_table(void)
{
return ask_flow_global_initialised ? &ask_flow_global : NULL;
}
EXPORT_SYMBOL_GPL(ask_flow_default_table);

/* -------------------------------------------------------------------------
 * Table lifecycle
 * ------------------------------------------------------------------------- */

int ask_flow_table_create(struct ask_flow_table *t, const char *tag)
{
int rc;

if (!t)
return -EINVAL;

memset(t, 0, sizeof(*t));
atomic_set(&t->fake_hw_id_seq, 0);
atomic_set(&t->num_flows, 0);
t->tag = tag ? tag : "default";

rc = rhashtable_init(&t->rht, &ask_flow_rht_params);
if (rc) {
ask_pr_err("flow: rhashtable_init('%s') failed: %d\n",
   t->tag, rc);
return rc;
}

ask_pr_dbg("flow: table '%s' created\n", t->tag);
return 0;
}
EXPORT_SYMBOL_GPL(ask_flow_table_create);

static void ask_flow_free_rcu(struct rcu_head *head)
{
struct ask_flow *f = container_of(head, struct ask_flow, rcu);

kfree(f);
}

static void ask_flow_free_walker(void *ptr, void *arg)
{
struct ask_flow *f = ptr;
struct ask_flow_table *t = arg;

atomic_dec(&t->num_flows);
/*
 * rhashtable_free_and_destroy() iterates with no readers, so an
 * immediate kfree is also safe. Stay consistent with the runtime
 * remove path and use call_rcu so PR9's coverage tests (which
 * exercise this from a kunit context with concurrent readers)
 * see uniform lifecycle handling.
 */
call_rcu(&f->rcu, ask_flow_free_rcu);
}

void ask_flow_table_destroy(struct ask_flow_table *t)
{
if (!t)
return;

rhashtable_free_and_destroy(&t->rht, ask_flow_free_walker, t);
/*
 * Synchronise so the call_rcu() callbacks queued above complete
 * before the caller proceeds (it is about to free the surrounding
 * fman context).
 */
rcu_barrier();
ask_pr_dbg("flow: table '%s' destroyed (%d entries freed)\n",
   t->tag ? t->tag : "?",
   atomic_read(&t->num_flows));
}
EXPORT_SYMBOL_GPL(ask_flow_table_destroy);

/* -------------------------------------------------------------------------
 * Lookup / insert / remove
 * ------------------------------------------------------------------------- */

struct ask_flow *ask_flow_lookup(struct ask_flow_table *t, u64 cookie)
{
if (!t)
return NULL;
return rhashtable_lookup_fast(&t->rht, &cookie, ask_flow_rht_params);
}
EXPORT_SYMBOL_GPL(ask_flow_lookup);

int ask_flow_insert(struct ask_flow_table *t,
    u64 cookie,
    const struct ask_flow_key *key,
    u32 oif, u32 action_flags,
    u32 *out_hw_id)
{
struct ask_flow *f;
int rc;
u32 hw_id;

if (!t || !key || !out_hw_id)
return -EINVAL;

f = kzalloc(sizeof(*f), GFP_KERNEL);
if (!f)
return -ENOMEM;

f->cookie       = cookie;
f->key          = *key;
f->oif          = oif;
f->action_flags = action_flags;
u64_stats_init(&f->stats.syncp);

/*
 * Allocate a fake hw_flow_id. PR14 will replace this with the
 * value returned by ask_hw_flow_insert_v4_tcp() once the real
 * fman_host_cmd_send() exists. The atomic counter never wraps in
 * practice (32 bits at typical insert rates is millennia), but
 * if it ever did the rhashtable still keys on cookie so collisions
 * on hw_id are non-fatal here — they would only matter once we
 * round-trip the hw_id through the silicon.
 */
hw_id = (u32)atomic_inc_return(&t->fake_hw_id_seq);
f->hw_flow_id = hw_id;

rc = rhashtable_lookup_insert_fast(&t->rht, &f->node,
   ask_flow_rht_params);
if (rc) {
kfree(f);
if (rc == -EEXIST)
return -EEXIST;
ask_pr_warn("flow: insert(cookie=0x%llx) rht err %d\n",
    cookie, rc);
return rc;
}

atomic_inc(&t->num_flows);
*out_hw_id = hw_id;
return 0;
}
EXPORT_SYMBOL_GPL(ask_flow_insert);

int ask_flow_remove(struct ask_flow_table *t, u64 cookie)
{
struct ask_flow *f;
int rc;

if (!t)
return -EINVAL;

rcu_read_lock();
f = rhashtable_lookup_fast(&t->rht, &cookie, ask_flow_rht_params);
if (!f) {
rcu_read_unlock();
return -ENOENT;
}
/*
 * Pin the entry across the rhashtable_remove_fast() call. The
 * remove path itself does not free; it just unlinks. Once unlink
 * succeeds we hand the entry to call_rcu() so any concurrent
 * lookup that already obtained the pointer drains through a
 * grace period before the kfree fires.
 */
rc = rhashtable_remove_fast(&t->rht, &f->node, ask_flow_rht_params);
rcu_read_unlock();

if (rc)
return rc;

atomic_dec(&t->num_flows);
call_rcu(&f->rcu, ask_flow_free_rcu);
return 0;
}
EXPORT_SYMBOL_GPL(ask_flow_remove);

/* -------------------------------------------------------------------------
 * Stats
 * ------------------------------------------------------------------------- */

void ask_flow_update_stats(struct ask_flow *f, u64 add_packets, u64 add_bytes)
{
if (!f)
return;

u64_stats_update_begin(&f->stats.syncp);
f->stats.packets       += add_packets;
f->stats.bytes         += add_bytes;
f->stats.last_seen_ns   = ktime_get_ns();
u64_stats_update_end(&f->stats.syncp);
}
EXPORT_SYMBOL_GPL(ask_flow_update_stats);

int ask_flow_get_stats(struct ask_flow_table *t, u64 cookie,
       u64 *packets, u64 *bytes, u64 *last_seen_ns)
{
struct ask_flow *f;
unsigned int seq;
u64 p, b, l;

if (!t || !packets || !bytes || !last_seen_ns)
return -EINVAL;

rcu_read_lock();
f = rhashtable_lookup_fast(&t->rht, &cookie, ask_flow_rht_params);
if (!f) {
rcu_read_unlock();
return -ENOENT;
}

do {
seq = u64_stats_fetch_begin(&f->stats.syncp);
p   = f->stats.packets;
b   = f->stats.bytes;
l   = f->stats.last_seen_ns;
} while (u64_stats_fetch_retry(&f->stats.syncp, seq));
rcu_read_unlock();

*packets      = p;
*bytes        = b;
*last_seen_ns = l;
return 0;
}
EXPORT_SYMBOL_GPL(ask_flow_get_stats);

/* -------------------------------------------------------------------------
 * Walk + flush
 * ------------------------------------------------------------------------- */

int ask_flow_walk(struct ask_flow_table *t, ask_flow_walk_fn fn, void *arg)
{
struct rhashtable_iter iter;
struct ask_flow *f;
int rc = 0;

if (!t || !fn)
return -EINVAL;

rhashtable_walk_enter(&t->rht, &iter);
rhashtable_walk_start(&iter);

while ((f = rhashtable_walk_next(&iter)) != NULL) {
if (IS_ERR(f)) {
/* -EAGAIN: walker hit a resize. Restart safely. */
if (PTR_ERR(f) == -EAGAIN)
continue;
rc = PTR_ERR(f);
break;
}
rc = fn(f, arg);
if (rc)
break;
}

rhashtable_walk_stop(&iter);
rhashtable_walk_exit(&iter);
return rc;
}
EXPORT_SYMBOL_GPL(ask_flow_walk);

void ask_flow_flush(struct ask_flow_table *t)
{
struct rhashtable_iter iter;
struct ask_flow *f;
int removed = 0;

if (!t)
return;

rhashtable_walk_enter(&t->rht, &iter);
rhashtable_walk_start(&iter);

while ((f = rhashtable_walk_next(&iter)) != NULL) {
if (IS_ERR(f)) {
if (PTR_ERR(f) == -EAGAIN)
continue;
break;
}
/*
 * remove_fast inside an active walker iteration is supported
 * by rhashtable; it adjusts the iterator state. Once removed
 * the entry goes through the same call_rcu free path as
 * regular removal so concurrent lookups stay safe.
 */
if (rhashtable_remove_fast(&t->rht, &f->node,
   ask_flow_rht_params) == 0) {
atomic_dec(&t->num_flows);
call_rcu(&f->rcu, ask_flow_free_rcu);
removed++;
}
}

rhashtable_walk_stop(&iter);
rhashtable_walk_exit(&iter);

ask_pr_dbg("flow: flushed table '%s' (%d entries)\n",
   t->tag ? t->tag : "?", removed);
}
EXPORT_SYMBOL_GPL(ask_flow_flush);

/* -------------------------------------------------------------------------
 * Lifecycle
 * ------------------------------------------------------------------------- */

int ask_flow_init(void)
{
int rc = ask_flow_table_create(&ask_flow_global, "default");

if (rc)
return rc;

ask_flow_global_initialised = true;
ask_pr_info("flow: subsystem ready (rhashtable + RCU)\n");
return 0;
}

void ask_flow_exit(void)
{
if (!ask_flow_global_initialised)
return;

ask_flow_table_destroy(&ask_flow_global);
ask_flow_global_initialised = false;
ask_pr_dbg("flow: subsystem exit\n");
}