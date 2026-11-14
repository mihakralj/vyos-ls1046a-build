/* Host harness for the per-port offload counters (kernel/ask/oot-modules/ask/ask_stats.c).
 * The body from "struct ask_port_ctr {" to the end of the file is included from
 * ask_port_stats_body.h, extracted by tests/ask_port_stats.py. Per-CPU storage is mocked as
 * an NCPU array indexed by cur_cpu, so adds from different CPUs land in different slots. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint64_t u64;
#define GFP_KERNEL 0
#define NCPU 4
#define MAXI 16
#define __percpu
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define ask_pr_dbg(...) do { } while (0)

struct rtnl_link_stats64 { u64 rx_packets, rx_bytes, tx_packets, tx_bytes; };
struct xarray { void *s[MAXI]; };

static int cur_cpu, live_allocs;
static void *kzalloc(size_t n, int gfp) { (void)gfp; live_allocs++; return calloc(1, n); }
static void kfree(void *p) { if (p) live_allocs--; free(p); }
#define alloc_percpu(type) ((type *)kzalloc(NCPU * sizeof(type), 0))
#define free_percpu(p) kfree(p)
#define per_cpu_ptr(p, cpu) (&(p)[cpu])
#define for_each_possible_cpu(cpu) for ((cpu) = 0; (cpu) < NCPU; (cpu)++)
static int preempt_off;
#define get_cpu_ptr(p) (assert(!preempt_off), preempt_off = 1, &(p)[cur_cpu])
#define put_cpu_ptr(p) do { assert(preempt_off); preempt_off = 0; } while (0)

static void xa_init(struct xarray *xa) { memset(xa, 0, sizeof(*xa)); }
static void xa_destroy(struct xarray *xa) { (void)xa; }
static void *xa_load(struct xarray *xa, unsigned long i) { return xa->s[i]; }
static bool xa_is_err(void *p) { return (uintptr_t)p == 1; }
static void *xa_cmpxchg(struct xarray *xa, unsigned long i, void *old, void *new, int gfp)
{
	void *cur = xa->s[i];

	(void)gfp;
	if (cur == old)
		xa->s[i] = new;
	return cur;
}
static void *xa_erase(struct xarray *xa, unsigned long i)
{
	void *cur = xa->s[i];

	xa->s[i] = NULL;
	return cur;
}
#define xa_for_each(xa, idx, entry) \
	for ((idx) = 0; (idx) < MAXI; (idx)++) if (((entry) = (xa)->s[idx]) != NULL)

#include "ask_port_stats_body.h"

static struct rtnl_link_stats64 get(int ifindex)
{
	struct rtnl_link_stats64 s = { 1, 1, 1, 1 };	/* get() adds to software totals */

	ask_port_stats_get(ifindex, &s);
	s.rx_packets--; s.rx_bytes--; s.tx_packets--; s.tx_bytes--;
	return s;
}

int main(void)
{
	struct rtnl_link_stats64 s;

	ask_stats_init();
	ask_port_stats_add(0, 5, 5, 5, 5);		/* ifindex <= 0 ignored */
	ask_port_stats_add(5, 0, 0, 0, 0);		/* nothing to add: no entry created */
	assert(live_allocs == 0);
	ask_port_stats_zero(3);				/* zero before create: no-op */
	s = get(3);
	assert(!s.rx_packets && !s.tx_bytes);

	cur_cpu = 0; ask_port_stats_add(3, 10, 1000, 0, 0);
	cur_cpu = 2; ask_port_stats_add(3, 5, 500, 0, 0);
	cur_cpu = 3; ask_port_stats_add(3, 0, 0, 7, 700);
	s = get(3);
	assert(s.rx_packets == 15 && s.rx_bytes == 1500 && s.tx_packets == 7 && s.tx_bytes == 700);
	assert(!get(4).rx_packets);			/* other ports untouched */

	ask_port_stats_zero(3);
	s = get(3);
	assert(!s.rx_packets && !s.rx_bytes && !s.tx_packets && !s.tx_bytes);
	cur_cpu = 1; ask_port_stats_add(3, 2, 200, 3, 300);
	s = get(3);
	assert(s.rx_packets == 2 && s.rx_bytes == 200 && s.tx_packets == 3 && s.tx_bytes == 300);

	ask_stats_exit();
	assert(live_allocs == 0);
	puts("ask_port_stats: OK");
	return 0;
}
