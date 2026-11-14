/* Host harness for the S7 lock-free fast path for never-installed cookies
 * (kernel/ask/oot-modules/ask/ask_flow_offload.c, plans/ASK2-FLOW-SCALING-PLAN.md S7).
 * The real helpers are included from ask_flow_unknown_fast_body.h, extracted by
 * tests/ask_flow_unknown_fast.py; everything they call is mocked here with per-cookie
 * state so each reviewed rule can be checked on its own. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint64_t u64;
#define EAGAIN 11

struct ask_flow_table { int dummy; };
struct flow_cls_offload { unsigned long cookie; };
struct xarray { int id; };

#define MAXC 16
static struct ask_flow_table table;
static struct ask_flow_table *table_ptr = &table;
static bool has_flow[MAXC], bound = true, aged[MAXC];
static u64 gen[MAXC];
static bool owner[MAXC], first_seen[MAXC], small[MAXC];
static unsigned int ask_offload_delay_ms = 500, ask_offload_flood_rate, ask_offload_min_packets, ask_offload_min_bytes;
static int too_young_calls, erases;

static struct xarray ask_flow_owner_block = { 1 }, ask_offload_first_seen = { 2 };

#define READ_ONCE(x) (x)
static void rcu_read_lock(void) {}
static void rcu_read_unlock(void) {}
static struct ask_flow_table *ask_flow_default_table(void) { return table_ptr; }
static void *ask_flow_lookup(struct ask_flow_table *t, u64 c) { (void)t; return has_flow[c] ? &has_flow[c] : NULL; }
static u64 ask_flow_gen_current(struct ask_flow_table *t, u64 c) { (void)t; return gen[c]; }
static void *xa_load(struct xarray *xa, unsigned long c)
{
	assert(xa == &ask_flow_owner_block);
	return owner[c] ? &owner[c] : NULL;
}
static void *xa_erase(struct xarray *xa, unsigned long c)
{
	assert(xa == &ask_offload_first_seen);
	erases++;
	first_seen[c] = false;
	return NULL;
}
static bool ask_flow_block_priv_bound(void *p) { return p && bound; }
/* models ask_offload_too_young(): stores first-seen on the first call, young until aged */
static bool ask_offload_too_young(unsigned long c)
{
	too_young_calls++;
	if (!ask_offload_delay_ms)
		return false;
	if (!first_seen[c]) {
		first_seen[c] = true;
		return true;
	}
	return !aged[c];
}

/* models ask_offload_too_small(): pure read, small until the volume threshold is met */
static bool ask_offload_too_small(unsigned long c)
{
	return (ask_offload_min_packets || ask_offload_min_bytes) && small[c];
}

#include "ask_flow_unknown_fast_body.h"

static void reset(void)
{
	memset(has_flow, 0, sizeof(has_flow)); memset(gen, 0, sizeof(gen));
	memset(owner, 0, sizeof(owner)); memset(first_seen, 0, sizeof(first_seen));
	memset(aged, 0, sizeof(aged)); memset(small, 0, sizeof(small));
	ask_offload_min_packets = ask_offload_min_bytes = 0;
	bound = true; ask_offload_delay_ms = 500; table_ptr = &table; too_young_calls = erases = 0;
}

int main(void)
{
	int priv;
	struct flow_cls_offload f = { 3 };

	/* unknown cookie, bound, delay on: rejected without the lock, first-seen stored */
	reset();
	assert(ask_flow_replace_deferred_unlocked(&priv, &f));
	assert(first_seen[3] && too_young_calls == 1);
	/* aged unknown cookie: not decided here, falls through to the locked path */
	aged[3] = true;
	assert(!ask_flow_replace_deferred_unlocked(&priv, &f));

	/* a cookie with only an owner entry (route/VLAN/PPPoE flush leftover) is known: locked path,
	 * and the age gate must not have been consulted (it stores first-seen as a side effect) */
	reset(); owner[3] = true;
	assert(!ask_flow_replace_deferred_unlocked(&priv, &f));
	assert(!first_seen[3] && too_young_calls == 0);
	assert(!ask_flow_destroy_unknown_unlocked(&f) && erases == 0);

	/* generation entry (live or tombstoned) or a flow: known, locked path, no first-seen side effect */
	reset(); gen[3] = 7;
	assert(!ask_flow_replace_deferred_unlocked(&priv, &f) && too_young_calls == 0);
	assert(!ask_flow_destroy_unknown_unlocked(&f));
	reset(); has_flow[3] = true;
	assert(!ask_flow_replace_deferred_unlocked(&priv, &f) && too_young_calls == 0);
	assert(!ask_flow_destroy_unknown_unlocked(&f));

	/* unbound block or NULL cb_priv: locked path, and no first-seen stored */
	reset(); bound = false;
	assert(!ask_flow_replace_deferred_unlocked(&priv, &f) && !first_seen[3] && too_young_calls == 0);
	reset();
	assert(!ask_flow_replace_deferred_unlocked(NULL, &f) && !first_seen[3]);

	/* delay 0: never decided here */
	reset(); ask_offload_delay_ms = 0;
	assert(!ask_flow_replace_deferred_unlocked(&priv, &f));

	/* S6 volume gate: an aged unknown cookie below the threshold is deferred without the lock */
	reset(); ask_offload_min_packets = 16; aged[3] = true; first_seen[3] = true; small[3] = true;
	assert(ask_flow_replace_deferred_unlocked(&priv, &f));
	small[3] = false;
	assert(!ask_flow_replace_deferred_unlocked(&priv, &f));
	/* volume gate alone (delay 0): deferred, and no first-seen stored */
	reset(); ask_offload_delay_ms = 0; ask_offload_min_bytes = 16384; small[3] = true;
	assert(ask_flow_replace_deferred_unlocked(&priv, &f) && !first_seen[3]);
	/* a known cookie never takes the unlocked decision, small or not */
	reset(); ask_offload_min_packets = 16; small[3] = true; owner[3] = true;
	assert(!ask_flow_replace_deferred_unlocked(&priv, &f) && too_young_calls == 0);

	/* no table: both fall through */
	reset(); table_ptr = NULL;
	assert(!ask_flow_replace_deferred_unlocked(&priv, &f) && !ask_flow_destroy_unknown_unlocked(&f));

	/* DESTROY of an unknown cookie: first-seen erased without the lock, whatever the delay */
	reset(); first_seen[3] = true; ask_offload_delay_ms = 0;
	assert(ask_flow_destroy_unknown_unlocked(&f) && !first_seen[3] && erases == 1);

	puts("ask_flow_unknown_fast: OK");
	return 0;
}
