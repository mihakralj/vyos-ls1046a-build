// SPDX-License-Identifier: GPL-2.0
/*
 * Host fault-injection tests for T1(a): devchg, fdbchg, and fibchg retirement
 * workers must not silently drop events on GFP_ATOMIC walk OOM, must bound
 * retries on non-OOM hardware delete failure, and must not re-arm after exit.
 */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;

typedef struct { int counter; } atomic_t;
#define ATOMIC_INIT(v) { (v) }
static void atomic_inc(atomic_t *a) { a->counter++; }
static int atomic_read(const atomic_t *a) { return a->counter; }
static void atomic_set(atomic_t *a, int v) { a->counter = v; }

#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define min_t(type, a, b) ((type)(a) < (type)(b) ? (type)(a) : (type)(b))
#define ETH_ALEN 6
#define ASK_FLOW_L3_IPV4 0
#define ASK_FLOW_L3_IPV6 1
#define ASK_NATF_DNAT 2
#define GFP_ATOMIC 0
#define pr_info(...) do { } while (0)
#define pr_warn_ratelimited(...) do { warn_count++; } while (0)
#define pr_err_ratelimited(...) do { err_count++; } while (0)

struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD(n) struct list_head n = { &(n), &(n) }
static void list_add_tail(struct list_head *e, struct list_head *h)
{
	e->prev = h->prev;
	e->next = h;
	h->prev->next = e;
	h->prev = e;
}
static void list_del(struct list_head *e)
{
	e->prev->next = e->next;
	e->next->prev = e->prev;
}
#define container_of(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
#define list_entry(p, t, m) container_of(p, t, m)
#define list_for_each_entry_safe(pos, n, head, m)                           \
	for (pos = list_entry((head)->next, typeof(*pos), m),               \
	     n = list_entry(pos->m.next, typeof(*pos), m);                  \
	     &pos->m != (head);                                             \
	     pos = n, n = list_entry(n->m.next, typeof(*n), m))

struct mutex { bool held; };
#define DEFINE_MUTEX(n) struct mutex n = { false }
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = true; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = false; }
#define lockdep_assert_held(m) assert((m)->held)

struct spinlock { bool held; };
#define DEFINE_SPINLOCK(n) struct spinlock n = { false }
static void spin_lock_bh(struct spinlock *l) { assert(!l->held); l->held = true; }
static void spin_unlock_bh(struct spinlock *l) { assert(l->held); l->held = false; }

#define rcu_read_lock() do { } while (0)
#define rcu_read_unlock() do { } while (0)

static bool ether_addr_equal(const u8 *a, const u8 *b) { return memcmp(a, b, ETH_ALEN) == 0; }
static void ether_addr_copy(u8 *d, const u8 *s) { memcpy(d, s, ETH_ALEN); }
static bool is_zero_ether_addr(const u8 *a)
{
	return (a[0] | a[1] | a[2] | a[3] | a[4] | a[5]) == 0;
}

struct work_struct { int tag; };
struct delayed_work { struct work_struct work; };
#define DECLARE_WORK(n, fn) struct work_struct n = { 0 }
#define DECLARE_DELAYED_WORK(n, fn) struct delayed_work n = { { 0 } }
static unsigned long msecs_to_jiffies(unsigned int ms) { return ms; }

static unsigned int warn_count, err_count;
static int scheduled_work_count;
static int scheduled_delayed_count;
static struct work_struct *last_work;
static struct delayed_work *last_delayed;
static unsigned long last_delay;

static bool schedule_work(struct work_struct *w)
{
	scheduled_work_count++;
	last_work = w;
	return true;
}

static bool schedule_delayed_work(struct delayed_work *dw, unsigned long d)
{
	scheduled_delayed_count++;
	last_delayed = dw;
	last_delay = d;
	return true;
}

static int alloc_fail_after = -1;
static int alloc_calls;
static int live_allocs;
static void *kzalloc(size_t sz, int gfp)
{
	void *p;

	alloc_calls++;
	if (alloc_fail_after >= 0 && alloc_calls > alloc_fail_after)
		return NULL;
	p = calloc(1, sz);
	assert(p);
	live_allocs++;
	return p;
}
static void kfree(void *p)
{
	if (p)
		live_allocs--;
	free(p);
}

struct ask_flow_key {
	u8 l3_proto;
	u8 l4_proto;
	u8 nat_flags;
	u32 iif;
	u32 nh_ifindex;
	u8 dst_ip[16];
	u8 nat_dst_ip[16];
	u8 next_hop_mac[ETH_ALEN];
};

struct ask_flow {
	bool present;
	bool tombstoned;
	bool hw_backed;
	u64 cookie;
	u64 fe_token;
	u64 generation;
	u32 oif;
	struct ask_flow_key key;
};

struct ask_flow_table { int unused; };
static struct ask_flow_table default_table;
static struct ask_flow_table *ask_flow_default_table(void) { return &default_table; }

#define MAX_FLOWS 8
static struct ask_flow flows[MAX_FLOWS];
static int withdraw_rc_by_cookie[16];
static int withdraw_calls;
static DEFINE_MUTEX(ask_flow_lifecycle_lock);

static int ask_flow_walk(struct ask_flow_table *t,
			 int (*fn)(struct ask_flow *, void *), void *arg)
{
	unsigned int i;

	for (i = 0; i < MAX_FLOWS; i++) {
		int rc;

		if (!flows[i].present)
			continue;
		rc = fn(&flows[i], arg);
		if (rc)
			return rc;
	}
	return 0;
}

static struct ask_flow *ask_flow_lookup(struct ask_flow_table *t, u64 cookie)
{
	unsigned int i;

	for (i = 0; i < MAX_FLOWS; i++)
		if (flows[i].present && flows[i].cookie == cookie)
			return &flows[i];
	return NULL;
}

static bool ask_flow_gen_tombstone_if_current(struct ask_flow_table *t,
					      u64 cookie, u64 gen)
{
	struct ask_flow *f = ask_flow_lookup(t, cookie);

	if (!f || f->generation != gen || f->tombstoned)
		return false;
	f->tombstoned = true;
	return true;
}

static void ask_flow_pending_drop_cookie(u64 cookie, u64 gen) { }

static int ask_flow_withdraw_owned(struct ask_flow_table *t, u64 cookie,
				   u64 gen, u64 fe_token,
				   const struct ask_flow_key *key,
				   bool hw_backed, const char *what,
				   bool *superseded)
{
	struct ask_flow *f = ask_flow_lookup(t, cookie);
	int rc;

	withdraw_calls++;
	if (superseded)
		*superseded = false;
	rc = withdraw_rc_by_cookie[cookie & 15];
	if (!rc && f)
		f->present = false;
	return rc;
}

static void ask_flow_gen_release(struct ask_flow_table *t, u64 cookie, u64 gen) { }

#include "ask_retire_flush_body.h"
#include "ask_retire_workers_body.h"

static void reset_state(void)
{
	memset(flows, 0, sizeof(flows));
	memset(withdraw_rc_by_cookie, 0, sizeof(withdraw_rc_by_cookie));
	withdraw_calls = 0;
	alloc_fail_after = -1;
	alloc_calls = 0;
	warn_count = err_count = 0;
	scheduled_work_count = scheduled_delayed_count = 0;
	last_work = NULL;
	last_delayed = NULL;
	last_delay = 0;

	ask_devchg_n = 0;
	ask_devchg_all = false;
	ask_devchg_snap_n = 0;
	ask_devchg_snap_all = false;
	ask_fdbchg_n = 0;
	ask_fdbchg_all = false;
	ask_fdbchg_snap_n = 0;
	ask_fdbchg_snap_all = false;
	ask_fibchg_n = 0;
	ask_fibchg_all = false;
	ask_fibchg_snap_n = 0;
	ask_fibchg_snap_all = false;
	ask_fdb_live = true;
	ask_fib_live = true;
	(void)ask_fib_nb_ok;
}

static void add_flow(unsigned int idx, u64 cookie, u32 iif, u32 oif,
		     const u8 *mac, u8 ip_last)
{
	flows[idx] = (struct ask_flow) {
		.present = true,
		.hw_backed = true,
		.cookie = cookie,
		.fe_token = 100 + cookie,
		.generation = 1,
		.oif = oif,
		.key = {
			.l3_proto = ASK_FLOW_L3_IPV4,
			.l4_proto = 6,
			.iif = iif,
			.nh_ifindex = oif,
			.dst_ip = { 10, 99, 2, ip_last },
		},
	};
	if (mac)
		memcpy(flows[idx].key.next_hop_mac, mac, ETH_ALEN);
}

static unsigned int live_flow_count(void)
{
	unsigned int i, n = 0;

	for (i = 0; i < MAX_FLOWS; i++)
		if (flows[i].present)
			n++;
	return n;
}

int main(void)
{
	static const u8 mac_a[ETH_ALEN] = { 0x02, 0, 0, 0, 0, 0xaa };
	static const u8 dst_pfx[4] = { 10, 99, 2, 0 };

	/* 1. devchg OOM mid-walk must restore snapshot, schedule retry, and drain on retry. */
	reset_state();
	ask_devchg_live = true;
	add_flow(0, 1, 10, 20, mac_a, 1);
	add_flow(1, 2, 10, 20, mac_a, 2);
	add_flow(2, 3, 10, 20, mac_a, 3);
	ask_devchg_queue(10);
	alloc_fail_after = 1; /* flow 0 collected, flow 1 hits -ENOMEM */
	ask_devchg_flush_fn(&ask_devchg_flush_work.work);
	assert(live_flow_count() == 2); /* flow 0 retired; flows 1 and 2 still need retry */
	assert(ask_devchg_n == 1 && ask_devchg_ifx[0] == 10);
	assert(scheduled_delayed_count == 1 && last_delayed == &ask_devchg_flush_work);

	alloc_fail_after = -1;
	ask_devchg_flush_fn(&ask_devchg_flush_work.work);
	assert(live_flow_count() == 0);
	assert(ask_devchg_n == 0 && !ask_devchg_all);
	assert(scheduled_delayed_count == 1);
	assert(live_allocs == 0);

	/* 2. fdbchg OOM mid-walk must restore snapshot, schedule retry, and drain on retry. */
	reset_state();
	add_flow(0, 1, 10, 20, mac_a, 1);
	add_flow(1, 2, 10, 20, mac_a, 2);
	add_flow(2, 3, 10, 20, mac_a, 3);
	ask_flow_fdb_change(mac_a, 0);
	scheduled_delayed_count = 0;
	alloc_fail_after = 1;
	ask_fdbchg_flush_fn(&ask_fdbchg_flush_work.work);
	assert(live_flow_count() == 2);
	assert(ask_fdbchg_n == 1 && ether_addr_equal(ask_fdbchg_q[0].addr, mac_a));
	assert(scheduled_delayed_count == 1 && last_delayed == &ask_fdbchg_flush_work);

	alloc_fail_after = -1;
	ask_fdbchg_flush_fn(&ask_fdbchg_flush_work.work);
	assert(live_flow_count() == 0);
	assert(ask_fdbchg_n == 0 && !ask_fdbchg_all);
	assert(scheduled_delayed_count == 1);
	assert(live_allocs == 0);

	/* 3. fibchg OOM mid-walk + concurrent queue overflow degrades to all=true and drains. */
	reset_state();
	add_flow(0, 1, 10, 20, mac_a, 1);
	add_flow(1, 2, 10, 20, mac_a, 2);
	add_flow(2, 3, 10, 20, mac_a, 3);
	ask_fibchg_queue(ASK_FLOW_L3_IPV4, dst_pfx, 4, 24, false);
	scheduled_delayed_count = 0;
	alloc_fail_after = 1;
	ask_fibchg_flush_fn(&ask_fibchg_flush_work.work);
	assert(live_flow_count() == 2);
	assert(ask_fibchg_n == 1);
	assert(scheduled_delayed_count == 1 && last_delayed == &ask_fibchg_flush_work);

	alloc_fail_after = -1;
	ask_fibchg_flush_fn(&ask_fibchg_flush_work.work);
	assert(live_flow_count() == 0);
	assert(ask_fibchg_n == 0 && !ask_fibchg_all);
	assert(scheduled_delayed_count == 1);
	assert(live_allocs == 0);

	/* 4. Non-OOM HW delete failure: other victims retire, no retry storm, error accounted. */
	reset_state();
	ask_devchg_live = true;
	atomic_set(&ask_retire_incomplete, 0);
	add_flow(0, 1, 10, 20, mac_a, 1);
	add_flow(1, 2, 10, 20, mac_a, 2);
	withdraw_rc_by_cookie[1] = -ETIMEDOUT;
	ask_devchg_queue(10);
	scheduled_delayed_count = 0;
	ask_devchg_flush_fn(&ask_devchg_flush_work.work);
	assert(flows[0].present && flows[0].tombstoned); /* stuck flow retained */
	assert(!flows[1].present);                       /* sibling flow still retired */
	assert(scheduled_delayed_count == 0);            /* no infinite retry storm */
	assert(atomic_read(&ask_retire_incomplete) == 1);
	assert(err_count >= 1);

	/* 5. Module exit (live=false) must not reschedule retry even on walk OOM. */
	reset_state();
	ask_devchg_live = false;
	add_flow(0, 1, 10, 20, mac_a, 1);
	ask_devchg_ifx[0] = 10;
	ask_devchg_n = 1;
	alloc_fail_after = 0;
	ask_devchg_flush_fn(&ask_devchg_flush_work.work);
	assert(scheduled_delayed_count == 0);
	assert(live_allocs == 0);

	puts("ask_retire_fault: OK");
	return 0;
}
