// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - software flow table (PR7 / M1.3)
 *
 * Lock-free RCU lookup, single-writer-style insert/remove backed by
 * the linux/rhashtable internal locks. Per-flow stats live in a
 * u64_stats_sync seqcount so 32-bit readers cannot tear a 64-bit
 * counter mid-update.
 *
 * No hardware in this PR: hw_flow_id is faked from an atomic counter
 * incremented on every successful insert. PR14 (M2.5) replaces the
 * fake with the value the 210 microcode hands back from the FMan PCD
 * keygen/CC tree (CC bucket index post-add). v1.3 Path A bypasses
 * the §12 host-command wire format entirely: ask_hw_flow_insert_v4_tcp
 * calls fman_pcd_cc_node_add_key() directly. This PR only owns the
 * software bookkeeping that sits between flow_block_cb (PR8) and the
 * ask_hw_flow_insert() hardware entry point.
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
atomic_set(&t->num_hw_backed, 0);
t->tag = tag ? tag : "default";
xa_init(&t->gen_by_cookie);

rc = rhashtable_init(&t->rht, &ask_flow_rht_params);
if (rc) {
ask_pr_err("flow: rhashtable_init('%s') failed: %d\n",
   t->tag, rc);
xa_destroy(&t->gen_by_cookie);
return rc;
}

ask_pr_dbg("flow: table '%s' created\n", t->tag);
return 0;
}
EXPORT_SYMBOL_GPL(ask_flow_table_create);

/* -------------------------------------------------------------------------
 * T-M6-A3: per-cookie ownership generation registry.
 *
 * Value-encoded xarray: entry = xa_mk_value((gen << 1) | state). gen is a
 * 31-bit monotonic counter per cookie; wrap is a non-issue (2^31 REPLACEs of
 * one recycled cookie). Value entries need no allocation, so a tombstone can
 * outlive the ask_flow with zero heap cost and no leak.
 * ------------------------------------------------------------------------- */

#define ASK_GEN_STATE_BITS 1
#define ASK_GEN_STATE_MASK ((1UL << ASK_GEN_STATE_BITS) - 1)

static unsigned long ask_gen_encode(u32 gen, u8 state)
{
	return ((unsigned long)gen << ASK_GEN_STATE_BITS) |
	       (state & ASK_GEN_STATE_MASK);
}

static u32 ask_gen_decode_gen(unsigned long v)
{
	return (u32)(v >> ASK_GEN_STATE_BITS);
}

static u8 ask_gen_decode_state(unsigned long v)
{
	return (u8)(v & ASK_GEN_STATE_MASK);
}

u32 ask_flow_gen_next(struct ask_flow_table *t, u64 cookie)
{
	unsigned long flags, old;
	void *entry;
	u32 gen;
	int rc;

	if (!t)
		return 0;

	xa_lock_irqsave(&t->gen_by_cookie, flags);
	entry = xa_load(&t->gen_by_cookie, (unsigned long)cookie);
	old = xa_is_value(entry) ? xa_to_value(entry) : 0;
	gen = ask_gen_decode_gen(old) + 1;
	if (gen == 0)
		gen = 1; /* never hand out generation 0 (means "unknown") */
	/* GFP_ATOMIC: we hold xa_lock. On failure return 0 so the caller
	 * fails the REPLACE to software rather than publishing unguarded. */
	rc = xa_err(__xa_store(&t->gen_by_cookie, (unsigned long)cookie,
			       xa_mk_value(ask_gen_encode(gen, ASK_GEN_LIVE)),
			       GFP_ATOMIC));
	xa_unlock_irqrestore(&t->gen_by_cookie, flags);
	if (rc) {
		ask_pr_warn("flow: gen registry store failed for cookie=0x%llx rc=%d\n",
			    cookie, rc);
		return 0;
	}
	return gen;
}
EXPORT_SYMBOL_GPL(ask_flow_gen_next);

u32 ask_flow_gen_current(struct ask_flow_table *t, u64 cookie)
{
	unsigned long flags;
	void *entry;
	u32 gen = 0;

	if (!t)
		return 0;
	xa_lock_irqsave(&t->gen_by_cookie, flags);
	entry = xa_load(&t->gen_by_cookie, (unsigned long)cookie);
	if (xa_is_value(entry))
		gen = ask_gen_decode_gen(xa_to_value(entry));
	xa_unlock_irqrestore(&t->gen_by_cookie, flags);
	return gen;
}
EXPORT_SYMBOL_GPL(ask_flow_gen_current);

bool ask_flow_gen_is_current(struct ask_flow_table *t, u64 cookie, u32 gen)
{
	unsigned long flags, v;
	void *entry;
	bool ok = false;

	if (!t || gen == 0)
		return false;
	xa_lock_irqsave(&t->gen_by_cookie, flags);
	entry = xa_load(&t->gen_by_cookie, (unsigned long)cookie);
	if (xa_is_value(entry)) {
		v = xa_to_value(entry);
		ok = (ask_gen_decode_gen(v) == gen) &&
		     (ask_gen_decode_state(v) == ASK_GEN_LIVE);
	}
	xa_unlock_irqrestore(&t->gen_by_cookie, flags);
	return ok;
}
EXPORT_SYMBOL_GPL(ask_flow_gen_is_current);

void ask_flow_gen_tombstone(struct ask_flow_table *t, u64 cookie)
{
	unsigned long flags;
	void *entry;
	u32 gen;

	if (!t)
		return;
	xa_lock_irqsave(&t->gen_by_cookie, flags);
	entry = xa_load(&t->gen_by_cookie, (unsigned long)cookie);
	gen = xa_is_value(entry) ? ask_gen_decode_gen(xa_to_value(entry)) : 0;
	/* Preserve the generation, flip state to TOMBSTONED. GFP_ATOMIC under
	 * lock; a store failure only loses the tombstone hint, and the
	 * generation compare in insert_owned still blocks a stale publish. */
	__xa_store(&t->gen_by_cookie, (unsigned long)cookie,
		   xa_mk_value(ask_gen_encode(gen, ASK_GEN_TOMBSTONED)),
		   GFP_ATOMIC);
	xa_unlock_irqrestore(&t->gen_by_cookie, flags);
}
EXPORT_SYMBOL_GPL(ask_flow_gen_tombstone);

void ask_flow_gen_release(struct ask_flow_table *t, u64 cookie)
{
	unsigned long flags;

	if (!t)
		return;
	xa_lock_irqsave(&t->gen_by_cookie, flags);
	__xa_erase(&t->gen_by_cookie, (unsigned long)cookie);
	xa_unlock_irqrestore(&t->gen_by_cookie, flags);
}
EXPORT_SYMBOL_GPL(ask_flow_gen_release);

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
if (f->hw_backed)
atomic_dec(&t->num_hw_backed);
/*
 * rhashtable_free_and_destroy() iterates with no readers, so an
 * immediate kfree is also safe. Stay consistent with the runtime
 * remove path and use call_rcu so PR9's coverage tests (which
 * exercise this from a kunit context with concurrent readers)
 * see uniform lifecycle handling.
 *
 * F-120: this path deliberately does NOT call ask_hw_flow_remove().
 * It runs from ask_flow_table_destroy(), i.e. module teardown, where
 * ask_hw_pcd_teardown() releases the whole PCD chain wholesale — a
 * per-flow release would be redundant, and rhashtable_free_and_destroy()
 * gives no context in which it would be safe to sleep. The counters are
 * still balanced so a destroy/create cycle starts clean. If this
 * function ever gains a caller outside module teardown, it must be
 * reworked the way ask_flow_flush() was.
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
/*
 * A3: drop the generation registry LAST — after the rht is gone and all
 * call_rcu callbacks have drained, so no late worker can still consult a
 * tombstone. Value entries need no per-entry free; xa_destroy suffices.
 */
xa_destroy(&t->gen_by_cookie);
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

static int ask_flow_insert_core(struct ask_flow_table *t,
    u64 cookie,
    const struct ask_flow_key *key,
    u32 oif, u32 action_flags,
    enum ask_hw_dir dir,
    u32 generation,
    u32 *out_hw_id)
{
struct ask_flow *f;
int rc;
u32 hw_id = 0;
bool hw_inserted = false;

if (!t || !key || !out_hw_id)
return -EINVAL;

if (dir >= ASK_HW_DIR_NR)
return -EINVAL;

/*
 * F-112: Fast-path duplicate check BEFORE kzalloc().
 * rhashtable_lookup_insert_fast() rejects -EEXIST, but by that
 * point we've already allocated memory and potentially inserted
 * into hardware (requiring a rollback).  Checking first avoids
 * unnecessary slab alloc/free churn and HW slot waste during
 * duplicate-flow storms (e.g. nft flowtable re-insertion races).
 */
{
/*
 * CR-010: rhashtable_lookup_fast() must run inside an RCU read-side
 * critical section — every other ask_flow_lookup() caller wraps it.
 * The result is only a hint that lets us skip the kzalloc on an
 * obvious duplicate; rhashtable_lookup_insert_fast() below remains
 * the authoritative arbiter, so an insert racing between the unlock
 * and the real insert is still rejected correctly.
 */
bool dup;

rcu_read_lock();
dup = ask_flow_lookup(t, cookie) != NULL;
rcu_read_unlock();
if (dup)
return -EEXIST;
}

f = kzalloc(sizeof(*f), GFP_KERNEL);
if (!f)
return -ENOMEM;

f->cookie       = cookie;
f->key          = *key;
f->oif          = oif;
f->action_flags = action_flags;
f->dir          = (u8)dir;
u64_stats_init(&f->stats.syncp);

/*
 * PR14g-body-3: try the silicon fast path first.
 *
 *   rc == 0          -> hw_id is a packed (token, key_idx) referring
 *                       to a real CC-node slot. Caller's tear-down
 *                       path (ask_flow_remove) will pass it back to
 *                       ask_hw_flow_remove() to free the slot.
 *   rc == -ENODEV    -> no HW backing (no DPAA on this host, PCD
 *                       bring-up failed, or @oif is not a dpaa-backed
 *                       netdev). Fall back to the software-only fake
 *                       counter so the flow still appears in the SW
 *                       table for stats / dump purposes.
 *   rc == -EOPNOTSUPP-> protocol path not implemented in HW yet
 *                       (body-2 ships v4-TCP only; v4-UDP / v6-* land
 *                       in M3.x). Same fallback as -ENODEV.
 *   other -E         -> hard failure (MURAM exhaustion, key table
 *                       full, mask/size mismatch). Propagate so
 *                       userspace sees the real error rather than
 *                       silently believing a flow is offloaded when
 *                       it is not. Free the freshly-allocated entry
 *                       and bail out.
 *
 * The dispatcher contract is documented in include/ask_internal.h
 * (PR14g-body-2 section).
 *
 * ID-SPACE INVARIANT (corrected 2026-07-26): @hw_flow_id is NOT
 * self-describing. Real HW ids are xarray cookies from
 * xa_alloc(..., XA_LIMIT(1, U32_MAX), ...); SW-only fallback ids come
 * from atomic_inc_return(&t->fake_hw_id_seq). Both start at 1 and
 * increment densely, so they collide from the first flow of each
 * class onward.
 *
 * An earlier version of this comment claimed the two could not
 * collide because ids were packed (bit 31..16 = node token,
 * bit 15..0 = slot) and that a stray SW id would be absorbed by a
 * TOKEN_NONE arm in ask_hw_flow_remove(). Neither is true of the
 * current code: packing survives only as the debug helper
 * ask_priv_pack_hw_flow_id(), and ask_hw_flow_remove() resolves its
 * argument as an xarray cookie. Never infer HW backing from the
 * numeric value — use struct ask_flow::hw_backed, set just below.
 */
rc = ask_hw_flow_insert(key, oif, action_flags, dir, &hw_id);
if (rc == 0) {
hw_inserted = true;
pr_info_ratelimited("ask: flow: hw_insert OK cookie=0x%llx oif=%u hw_id=0x%08x\n",
    cookie, oif, hw_id);
} else if (rc == -ENODEV || rc == -EOPNOTSUPP) {
hw_id = (u32)atomic_inc_return(&t->fake_hw_id_seq);
pr_info_ratelimited("ask: flow: hw_insert=%d (SW-fallback) cookie=0x%llx oif=%u l3=%u l4=%u sport=%u dport=%u\n",
    rc, cookie, oif, key->l3_proto, key->l4_proto,
    ntohs(key->sport), ntohs(key->dport));
	} else if (rc == -EAGAIN) {
		/*
		 * PR14z2 (2026-05-18): NEVER fabricate a fake hw_id and
		 * shove the flow into the SW rhashtable on -EAGAIN.
		 *
		 * Older PR14y code installed the cookie into the SW table
		 * with a fake hw_flow_id (atomic_inc_return on the
		 * SW-fallback counter), reasoning that the kernel SW
		 * flowtable would carry the flow until the neighbour
		 * resolves and a later REPLACE retried the HW insert.
		 * In practice that broke M2 in two ways:
		 *
		 *  (a) Once the cookie is in the rht, ask_flow_offload_replace
		 *      dedupes future REPLACE callbacks for the same cookie
		 *      (PR14r "REPLACE dedup" path) — so even after ARP
		 *      resolves we never re-try the HW insert. The flow is
		 *      pinned in SW for its entire lifetime → 43 % CPU at
		 *      6.9 Gbps (M2 gate is ≤ 5 % at ≥ 2 Gbps).
		 *
		 *  (b) On DESTROY, ask_hw_flow_remove(fake_id) walks the
		 *      ask_hw xarray, finds no matching entry, and warns
		 *      "ask: hw: remove: unknown cookie 0x… (already
		 *      freed?)" once per orphan fake-id. Cosmetic only,
		 *      but it floods dmesg under flow churn.
		 *
		 * Correct behaviour: propagate -EAGAIN to the caller. The
		 * caller (ask_flow_offload_replace) is expected to handle
		 * -EAGAIN by parking the cookie on the PR14y deferred-
		 * insert pending queue and replaying through the
		 * NETEVENT_NEIGH_UPDATE notifier the moment the ARP entry
		 * lands in NUD_VALID. The cookie is NOT in the rht while
		 * pending, so the dedupe race in (a) cannot trigger and
		 * the eventual deferred replay reaches ask_hw_flow_insert
		 * with a real next-hop MAC.
		 *
		 * Defence in depth: the caller (ask_flow_offload_replace)
		 * actually intercepts is_zero_ether_addr(next_hop_mac)
		 * BEFORE invoking ask_flow_insert, so reaching this arm
		 * means a TOCTOU race (neigh resolved during the resolve
		 * call, then evicted before hw_insert ran) — rare but
		 * possible. Returning -EAGAIN lets the caller fall back
		 * to the pending queue.
		 */
		pr_info_ratelimited("ask: flow: hw_insert=-EAGAIN (neigh unresolved) cookie=0x%llx oif=%u nh=%pM em=%pM — caller defers via PR14y pending queue\n",
				    cookie, oif, key->next_hop_mac, key->egress_mac);
		kfree(f);
		return -EAGAIN;
	} else {
ask_pr_warn("flow: hw_insert(cookie=0x%llx) hard fail %d\n",
    cookie, rc);
kfree(f);
return rc;
}
f->hw_flow_id = hw_id;
f->hw_backed  = hw_inserted;
f->generation = generation;

/*
 * T-M6-A3: generation gate, checked immediately before publication.
 *
 * @generation == 0 means the caller opted out of generation guarding
 * (ask_flow_insert() legacy/administrative/test path) — publish as before.
 *
 * Otherwise verify this REPLACE is STILL the current, non-tombstoned owner
 * of @cookie. If a newer REPLACE bumped the generation, or a DESTROY
 * tombstoned the cookie, while we were resolving neighbours / installing
 * the FE record, we must NOT publish — doing so would either shadow the
 * newer flow or leave a record the just-run DESTROY already tried to
 * remove. Roll back our own HW slot and return -ESTALE. This is the
 * "checked immediately before publication" requirement (CR-004 / R4).
 */
if (generation != 0 &&
    !ask_flow_gen_is_current(t, cookie, generation)) {
if (hw_inserted) {
int rm_rc = ask_hw_flow_remove(hw_id);

if (rm_rc && rm_rc != -ENODEV)
ask_pr_warn("flow: stale-publish HW rollback (cookie=0x%llx gen=%u) rc=%d\n",
    cookie, generation, rm_rc);
}
kfree(f);
pr_info_ratelimited("ask: flow: REPLACE cookie=0x%llx gen=%u superseded before publish — not offloaded\n",
    cookie, generation);
return -ESTALE;
}

rc = rhashtable_lookup_insert_fast(&t->rht, &f->node,
   ask_flow_rht_params);
if (rc) {
/*
 * Rollback: the silicon already has the key installed (slot
 * reserved by ask_hw_flow_insert above) but the SW table
 * rejected the cookie (most commonly -EEXIST from a duplicate
 * nft flow add). Drop the silicon slot before freeing the
 * software entry so we do not leak a forever-routed CC slot
 * to a now-orphan flow.
 *
 * The @hw_inserted guard is load-bearing, not defensive: on the
 * SW-fallback path @hw_id is a fake counter value that would
 * alias a live xarray cookie, so calling ask_hw_flow_remove()
 * here would tear down an unrelated flow's silicon state.
 */
if (hw_inserted) {
int rm_rc = ask_hw_flow_remove(hw_id);

if (rm_rc)
ask_pr_warn("flow: hw_remove rollback (cookie=0x%llx hw_id=0x%08x) failed %d\n",
    cookie, hw_id, rm_rc);
}
kfree(f);
if (rc == -EEXIST)
return -EEXIST;
ask_pr_warn("flow: insert(cookie=0x%llx) rht err %d\n",
    cookie, rc);
return rc;
}

atomic_inc(&t->num_flows);
if (hw_inserted)
atomic_inc(&t->num_hw_backed);
*out_hw_id = hw_id;
return 0;
}

/*
 * Legacy / administrative / test entry point: claim a fresh generation for
 * the cookie so even direct callers get an ownership stamp, then insert.
 * Callers that must thread a previously-claimed generation (production
 * REPLACE, pending replay, neigh rebuild) use ask_flow_insert_owned().
 */
int ask_flow_insert(struct ask_flow_table *t,
    u64 cookie,
    const struct ask_flow_key *key,
    u32 oif, u32 action_flags,
    enum ask_hw_dir dir,
    u32 *out_hw_id)
{
u32 gen = t ? ask_flow_gen_next(t, cookie) : 0;

return ask_flow_insert_core(t, cookie, key, oif, action_flags, dir,
    gen, out_hw_id);
}
EXPORT_SYMBOL_GPL(ask_flow_insert);

int ask_flow_insert_owned(struct ask_flow_table *t, u64 cookie,
  const struct ask_flow_key *key,
  u32 oif, u32 action_flags, enum ask_hw_dir dir,
  u32 generation, u32 *out_hw_id)
{
return ask_flow_insert_core(t, cookie, key, oif, action_flags, dir,
    generation, out_hw_id);
}
EXPORT_SYMBOL_GPL(ask_flow_insert_owned);

static int ask_flow_remove_core(struct ask_flow_table *t, u64 cookie,
u32 generation, bool honor_generation)
{
struct ask_flow *f;
u32 hw_id;
bool hw_backed;
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
 * T-M6-A3: generation-bounded remove. A DESTROY (or worker) that
 * carries an OLDER generation than the flow currently occupying this
 * cookie must be a no-op — the cookie was recycled and now owns a newer
 * flow that this stale caller must not tear down (CR-004 / R1, R5).
 * honor_generation==false (ask_flow_remove) keeps the unconditional
 * behaviour for administrative flush / teardown / tests.
 */
if (honor_generation && generation != 0 &&
    f->generation > generation) {
rcu_read_unlock();
pr_info_ratelimited("ask: flow: stale DESTROY cookie=0x%llx gen=%u < live gen=%u — ignored\n",
    cookie, generation, f->generation);
return -ESTALE;
}
/*
 * Snapshot hw_flow_id + hw_backed BEFORE the rht unlink so we can
 * hand them to ask_hw_flow_remove() after the SW table no longer
 * references the entry. Both fields are set once at insert time and
 * never mutated, so reading them under rcu_read_lock is safe.
 *
 * Pin the entry across the rhashtable_remove_fast() call. The
 * remove path itself does not free; it just unlinks. Once unlink
 * succeeds we hand the entry to call_rcu() so any concurrent
 * lookup that already obtained the pointer drains through a
 * grace period before the kfree fires.
 */
hw_id     = f->hw_flow_id;
hw_backed = f->hw_backed;
rc = rhashtable_remove_fast(&t->rht, &f->node, ask_flow_rht_params);
rcu_read_unlock();

if (rc)
return rc;

/*
 * Drop the silicon slot — ONLY for flows that actually have one.
 *
 * This call used to be unconditional, on the theory that a SW-only
 * id would be harmlessly ignored by a TOKEN_NONE arm inside
 * ask_hw_flow_remove(). That arm does not exist: the live id form is
 * an xarray cookie (ask_hw.c, ask_priv_pack_hw_flow_id() is a debug
 * helper kept only for ABI), and ask_hw_flow_remove() resolves its
 * argument with ask_hw_cookie_lookup(). Because SW fallback ids and
 * real cookies both start at 1 and increment densely, the very first
 * SW-fallback teardown aliased the very first HW flow's cookie and
 * silently freed that flow's CC shadow slot and HM next-hop
 * reference — leaving a live, still-advertised flow with no silicon
 * backing. Gate on @hw_backed instead of trying to tell the two id
 * spaces apart numerically; see struct ask_flow::hw_backed.
 *
 * A non-zero return is logged but not propagated — the SW table has
 * already released ownership of the cookie and the caller (nft flow
 * destroy) cannot re-attempt; surfacing the error here would just
 * leak the SW entry.
 */
if (hw_backed) {
int rm_rc = ask_hw_flow_remove(hw_id);

if (rm_rc && rm_rc != -ENODEV)
ask_pr_warn("flow: hw_remove(cookie=0x%llx hw_id=0x%08x) %d\n",
    cookie, hw_id, rm_rc);
atomic_dec(&t->num_hw_backed);
}

atomic_dec(&t->num_flows);
call_rcu(&f->rcu, ask_flow_free_rcu);
return 0;
}

int ask_flow_remove(struct ask_flow_table *t, u64 cookie)
{
return ask_flow_remove_core(t, cookie, 0, false);
}
EXPORT_SYMBOL_GPL(ask_flow_remove);

int ask_flow_remove_owned(struct ask_flow_table *t, u64 cookie,
  u32 generation)
{
return ask_flow_remove_core(t, cookie, generation, true);
}
EXPORT_SYMBOL_GPL(ask_flow_remove_owned);

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

/*
 * Cookies collected per pass. On-stack, so keep it small; flush is a rare
 * admin/recovery command and extra passes cost nothing that matters.
 */
#define ASK_FLOW_FLUSH_BATCH 32

/*
 * Consecutive passes that collect cookies but remove none before flush gives
 * up. Only reachable when another thread destroys the same cookies as fast as
 * we collect them; a handful of retries settles any real race.
 */
#define ASK_FLOW_FLUSH_MAX_STALLS 8

/*
 * F-120 (2026-07-26): flush is now remove-EQUIVALENT.
 *
 * It used to unlink entries straight out of the walker and call_rcu them,
 * which skipped ask_hw_flow_remove() entirely. Every HW-backed flow therefore
 * leaked its xarray cookie + kmem_cache object (ask_hw_cookie_free never ran).
 *
 * CR-007 (2026-07-27): the Fork-A CC shadow slot and HM next-hop references
 * that the original F-120 comment described are now deleted — the FE-VM ehash
 * path (Fork-B) manages its own flow keys via ask_fe_flow_insert/remove().
 *
 * It also left t->num_hw_backed permanently high, which disabled the
 * neigh stale-MAC fast-path skip in ask_flow_neigh_mac_changed().
 *
 * Why two phases rather than simply calling ask_hw_flow_remove() from inside
 * the loop: rhashtable_walk_start() opens an RCU read-side critical section,
 * and ask_hw_flow_remove() takes h->lock (a mutex) and can sleep. Collect
 * cookies under the walker, then replay them through the ordinary
 * ask_flow_remove() path outside it — which keeps exactly one implementation
 * of teardown ordering, HW release and counter maintenance.
 */
void ask_flow_flush(struct ask_flow_table *t)
{
int removed = 0;
unsigned int stalls = 0;

if (!t)
return;

for (;;) {
u64 batch[ASK_FLOW_FLUSH_BATCH];
struct rhashtable_iter iter;
struct ask_flow *f;
unsigned int n = 0, i, freed = 0;

/* Phase 1 — collect only. No allocation, no sleeping. */
rhashtable_walk_enter(&t->rht, &iter);
rhashtable_walk_start(&iter);
while (n < ASK_FLOW_FLUSH_BATCH &&
       (f = rhashtable_walk_next(&iter)) != NULL) {
if (IS_ERR(f)) {
if (PTR_ERR(f) == -EAGAIN)
continue;
break;
}
batch[n++] = f->cookie;
}
rhashtable_walk_stop(&iter);
rhashtable_walk_exit(&iter);

if (!n)
break;

/*
 * Phase 2 — process context, safe to sleep. ask_flow_remove()
 * performs the HW teardown and decrements both counters.
 * -ENOENT means a concurrent remove won the race; that is
 * success from flush's point of view.
 */
for (i = 0; i < n; i++) {
if (ask_flow_remove(t, batch[i]) == 0)
freed++;
}
removed += freed;

/*
 * CR-009: do NOT treat a fully-raced batch as completion.
 * If a concurrent destroy removed every cookie we collected,
 * each replayed remove returns -ENOENT and @freed is 0 — but
 * other flows may still be present, so breaking here left the
 * table non-empty while reporting success. Completion is proven
 * only by a collection that yields zero cookies (the `if (!n)
 * break` above). Bound the zero-progress case instead, so a
 * pathological race cannot spin a genl doit handler forever.
 */
if (!freed && ++stalls >= ASK_FLOW_FLUSH_MAX_STALLS) {
ask_pr_warn("flow: flush gave up after %u zero-progress passes on '%s' (%d removed)\n",
    stalls, t->tag ? t->tag : "?", removed);
break;
}
if (freed)
stalls = 0;
}

ask_pr_dbg("flow: flushed table '%s' (%d entries, hw_backed now %d)\n",
   t->tag ? t->tag : "?", removed,
   atomic_read(&t->num_hw_backed));
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