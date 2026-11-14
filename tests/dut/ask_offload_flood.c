/* Host harness for the flood-adaptive age gate, ask_offload_too_young() and its rate window
 * (kernel/ask/oot-modules/ask/ask_flow_offload.c, plans/ASK2-FLOW-SCALING-PLAN.md §0.2).
 * The real code is included from ask_offload_flood_body.h, extracted by
 * tests/ask_offload_flood.py; jiffies, atomics and the first-seen xarray are mocked. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define HZ 100
#define GFP_KERNEL 0
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define time_before(a, b) ((long)((a) - (b)) < 0)
#define time_after_eq(a, b) ((long)((a) - (b)) >= 0)
#define max(a, b) ((a) > (b) ? (a) : (b))
typedef struct { int counter; } atomic_t;
static void atomic_inc(atomic_t *a) { a->counter++; }
static int atomic_read(const atomic_t *a) { return a->counter; }
static void atomic_set(atomic_t *a, int v) { a->counter = v; }
static int atomic_xchg(atomic_t *a, int v) { int o = a->counter; a->counter = v; return o; }
#define cmpxchg(p, o, n) ({ __typeof__(*(p)) __o = *(p); if (__o == (o)) *(p) = (n); __o; })

static unsigned long jiffies;
static unsigned long msecs_to_jiffies(unsigned int ms) { return ms * HZ / 1000; }

/* first-seen xarray: value entries per cookie */
#define MAXC 20000
struct xarray { int id; };
static unsigned long seen_at[MAXC];
static bool seen[MAXC], fail_store;
static int stores;
static void *xa_mk_value(unsigned long v) { return (void *)((v << 1) | 1); }
static bool xa_is_value(const void *e) { return (uintptr_t)e & 1; }
static unsigned long xa_to_value(const void *e) { return (uintptr_t)e >> 1; }
static int xa_err(void *e) { return e == (void *)-12 ? -12 : 0; }
static void *xa_load(struct xarray *xa, unsigned long c) { (void)xa; return seen[c] ? xa_mk_value(seen_at[c]) : NULL; }
static void *xa_store(struct xarray *xa, unsigned long c, void *e, int gfp)
{
	(void)xa; (void)gfp;
	if (fail_store)
		return (void *)-12;
	seen[c] = true; seen_at[c] = xa_to_value(e); stores++;
	return NULL;
}

#define module_param_named(...)
#define MODULE_PARM_DESC(...)
static struct xarray ask_offload_first_seen;
static unsigned int ask_offload_delay_ms;

#include "ask_offload_flood_body.h"

static void reset(unsigned int delay, unsigned int rate)
{
	memset(seen, 0, sizeof(seen)); stores = 0; fail_store = false;
	ask_offload_delay_ms = delay; ask_offload_flood_rate = rate;
	atomic_set(&ask_offload_new_cur, 0); ask_offload_new_prev = 0; ask_offload_win_start = 0;
}

int main(void)
{
	unsigned long c;

	/* boot: jiffies starts 300 s before wrap; arrivals at a low rate never look like a flood */
	jiffies = (unsigned long)(-300L * HZ);
	reset(0, 1000);
	for (c = 1; c <= 3000; c++) {
		jiffies += 2;			/* 50 new cookies/s = 25 connections/s, for 60 s */
		assert(!ask_offload_too_young(c));
	}
	assert(stores == 3000);			/* stamped, so a later flood can age them */

	/* flood: 2,500 connections (5,000 cookies) in one second */
	reset(0, 1000);
	jiffies = 1000 * HZ;
	for (c = 1; c <= 5000; c++)
		ask_offload_too_young(c);
	assert(ask_offload_flooding());
	/* a cookie first seen during the flood waits 500 ms, then goes */
	assert(ask_offload_too_young(5000));
	jiffies += 20;				/* +200 ms */
	assert(ask_offload_too_young(5000));
	jiffies += 40;				/* +600 ms */
	assert(!ask_offload_too_young(5000));
	/* the flood ends: after two quiet seconds nothing is gated */
	assert(ask_offload_too_young(4000) == false);	/* first seen ~1 s ago: aged anyway */
	c = 6000;
	jiffies += 3 * HZ;
	assert(!ask_offload_flooding());
	assert(!ask_offload_too_young(c));

	/* flood_rate 0: the fixed delay alone, exactly the pre-adaptive gate */
	reset(500, 0);
	jiffies = 2000 * HZ;
	assert(ask_offload_too_young(7));
	jiffies += 60;
	assert(!ask_offload_too_young(7));
	/* both off: nothing stored, nothing gated */
	reset(0, 0);
	assert(!ask_offload_too_young(8) && stores == 0);

	/* a fixed delay longer than the flood floor wins during a flood */
	reset(800, 1000);
	jiffies = 3000 * HZ;
	for (c = 1; c <= 5000; c++)
		ask_offload_too_young(c);
	jiffies += 60;				/* +600 ms: past 500, short of 800 */
	assert(ask_offload_too_young(4000));
	jiffies += 30;
	assert(!ask_offload_too_young(4000));

	/* memory pressure on the stamp: offload at once */
	reset(500, 1000);
	fail_store = true;
	assert(!ask_offload_too_young(9));

	puts("ask_offload_flood: OK");
	return 0;
}
