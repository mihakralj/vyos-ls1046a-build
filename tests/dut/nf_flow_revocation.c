// SPDX-License-Identifier: GPL-2.0
/*
 * Host fault & concurrency harness for Priority 1 nf_flow_table revocation:
 *   1. FLOW_OFFLOAD_XMIT_DIRECT retains dst_cache/dst_cookie and fails
 *      nf_flow_dst_check() when dst_check() returns NULL.
 *   2. nf_flow_offload_gc_step() tears down an XMIT_DIRECT flow on stale dst.
 *   3. nf_flow_table_do_cleanup() matches XMIT_DIRECT out.ifidx/hw_ifidx,
 *      dst_dev_rcu(dst_cache), XMIT_NEIGH ifidx, and stale dst (br0/blackhole).
 *   4. flow_offload_del() double-remove race calls flow_offload_free() once
 *      and defers dst_release() until RCU callback (no pre-RCU NULL deref).
 *   5. nf_flow_table_fence_begin()/end() gates flow_offload_add() and
 *      flow_offload_refresh() (preserving conntrack/NAT).
 *   6. flow_offload_work_add() bails on NF_FLOW_TEARDOWN/NF_FLOW_HW_DYING
 *      without setting NF_FLOW_HW_DEAD, and flow_offload_work_handler() runs
 *      inline delete when NF_FLOW_TEARDOWN races with ADD.
 *   7. nf_flow_table_revoke_match()/all() tears down SW+HW (NAT-preserving),
 *      settles cleanly to 0, and reports -EBUSY + unresolved count on failure.
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
typedef uint16_t __be16;
typedef int32_t __s32;

#define ETH_ALEN 6
#define HZ 250
#define NF_CT_DAY (86400 * HZ)
#define NFPROTO_IPV4 2
#define NFPROTO_IPV6 10
#define NF_FLOW_TABLE_ENCAP_MAX 2
#define NF_FLOWTABLE_HW_OFFLOAD 0x1
#define NF_FLOWTABLE_COUNTER 0x2
#define BIT(n) (1U << (n))
#define EXPORT_SYMBOL_GPL(sym)
#define likely(x) (x)
#define unlikely(x) (x)
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define smp_mb() do { } while (0)
#define smp_mb__before_atomic() do { } while (0)
#define smp_mb__after_atomic() do { } while (0)
#define WARN_ON_ONCE(cond) do { if (cond) warn_count++; } while (0)
#define container_of(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
#define read_pnet(p) (*(p))
#define net_warn_ratelimited(...) do { } while (0)
#define NF_FLOW_TABLE_STAT_DEC_ATOMIC(net, field) ((void)(net))

static unsigned int warn_count;
static u32 nf_flowtable_time_stamp = 1000;

typedef struct { int v; } atomic_t;
#define ATOMIC_INIT(x) { (x) }
static int atomic_read(const atomic_t *a) { return a->v; }
static int atomic_inc_return(atomic_t *a) { return ++a->v; }
static int atomic_dec_return(atomic_t *a) { return --a->v; }

struct mutex { bool held; };
#define DEFINE_MUTEX(name) struct mutex name = { false }
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = true; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = false; }

struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD(name) struct list_head name = { &(name), &(name) }
static void list_add_tail(struct list_head *e, struct list_head *h)
{
	e->prev = h->prev;
	e->next = h;
	h->prev->next = e;
	h->prev = e;
}
#define list_entry(p, t, m) container_of(p, t, m)
#define list_for_each_entry(pos, head, m) \
	for (pos = list_entry((head)->next, typeof(*pos), m); \
	     &pos->m != (head); \
	     pos = list_entry(pos->m.next, typeof(*pos), m))

static bool test_bit(int n, const unsigned long *v) { return (*v >> n) & 1UL; }
static void set_bit(int n, unsigned long *v) { *v |= 1UL << n; }
static void __set_bit(int n, unsigned long *v) { *v |= 1UL << n; }
static void clear_bit(int n, unsigned long *v) { *v &= ~(1UL << n); }
static bool test_and_set_bit(int n, unsigned long *v)
{
	bool old = test_bit(n, v);
	set_bit(n, v);
	return old;
}

#define xchg(ptr, newval) ({ \
	typeof(*(ptr)) __old = *(ptr); \
	*(ptr) = (newval); \
	__old; \
})

struct net { int id; };
static bool net_eq(const struct net *a, const struct net *b) { return a == b; }

struct net_device {
	int ifindex;
	struct net *net;
};
static struct net *dev_net(const struct net_device *d) { return d->net; }

struct dst_entry {
	struct net_device *dev;
	bool valid;
	u32 cookie;
	u16 mtu;
	int refs;
};

static struct net_device *dst_dev_rcu(const struct dst_entry *dst)
{
	return dst ? dst->dev : NULL;
}

static struct dst_entry *dst_check(struct dst_entry *dst, u32 cookie)
{
	if (!dst || !dst->valid)
		return NULL;
	if (dst->cookie && dst->cookie != cookie)
		return NULL;
	return dst;
}

static unsigned int dst_releases;
static void dst_release(struct dst_entry *dst)
{
	if (!dst)
		return;
	assert(dst->refs > 0);
	dst->refs--;
	dst_releases++;
}

static u16 ip_dst_mtu_maybe_forward(struct dst_entry *dst, bool forwarding)
{
	return dst ? dst->mtu : 1500;
}
static u16 ip6_dst_mtu_maybe_forward(struct dst_entry *dst, bool forwarding)
{
	return dst ? dst->mtu : 1500;
}
static struct dst_entry *dst_rt6_info(struct dst_entry *dst) { return dst; }
static u32 rt6_get_cookie(struct dst_entry *dst) { return dst ? dst->cookie : 0; }

enum flow_offload_tuple_dir {
	FLOW_OFFLOAD_DIR_ORIGINAL = 0,
	FLOW_OFFLOAD_DIR_REPLY = 1,
	FLOW_OFFLOAD_DIR_MAX = 2,
};

enum flow_offload_xmit_type {
	FLOW_OFFLOAD_XMIT_UNSPEC = 0,
	FLOW_OFFLOAD_XMIT_NEIGH,
	FLOW_OFFLOAD_XMIT_XFRM,
	FLOW_OFFLOAD_XMIT_DIRECT,
	FLOW_OFFLOAD_XMIT_TC,
};

enum flow_offload_type {
	NF_FLOW_OFFLOAD_UNSPEC = 0,
	NF_FLOW_OFFLOAD_ROUTE,
};

enum nf_flow_flags {
	NF_FLOW_SNAT,
	NF_FLOW_DNAT,
	NF_FLOW_CLOSING,
	NF_FLOW_TEARDOWN,
	NF_FLOW_PENDING,
	NF_FLOW_HW,
	NF_FLOW_HW_DYING,
	NF_FLOW_HW_DEAD,
	NF_FLOW_HW_BIDIRECTIONAL,
	NF_FLOW_HW_ESTABLISHED,
};

enum { IPS_OFFLOAD_BIT = 1, IPS_HW_OFFLOAD_BIT = 2 };
enum flow_cls_command { FLOW_CLS_REPLACE, FLOW_CLS_DESTROY, FLOW_CLS_STATS };

struct nf_conn {
	unsigned long status;
	struct net *net;
	int refs;
	bool dying;
};
static struct net *nf_ct_net(const struct nf_conn *ct) { return ct->net; }
static bool nf_ct_is_dying(const struct nf_conn *ct) { return ct->dying; }
static void nf_ct_refresh(struct nf_conn *ct, u32 timeout) { (void)ct; (void)timeout; }
static unsigned int ct_puts;
static void nf_ct_put(struct nf_conn *ct)
{
	assert(ct->refs > 0);
	ct->refs--;
	ct_puts++;
}

struct rhash_head { bool in_table; };
struct flow_offload_tuple {
	int iifidx;
	u8 l3proto;
	u8 l4proto;
	struct { u16 id; __be16 proto; } encap[NF_FLOW_TABLE_ENCAP_MAX];
	u8 dir, xmit_type, encap_num, in_vlan_ingress;
	u16 mtu;
	struct dst_entry *dst_cache;
	u32 dst_cookie;
	union {
		struct { u32 ifidx; };
		struct {
			u32 ifidx;
			u32 hw_ifidx;
			u8 h_source[ETH_ALEN];
			u8 h_dest[ETH_ALEN];
		} out;
		struct { u32 iifidx; } tc;
	};
};

struct flow_offload_tuple_rhash {
	struct rhash_head node;
	struct flow_offload_tuple tuple;
};

struct rcu_head { void (*fn)(struct rcu_head *); };
struct flow_offload {
	struct flow_offload_tuple_rhash tuplehash[FLOW_OFFLOAD_DIR_MAX];
	struct nf_conn *ct;
	unsigned long flags;
	u16 type;
	u32 timeout;
	struct rcu_head rcu_head;
};

struct nf_flow_route {
	struct {
		struct dst_entry *dst;
		struct {
			u32 ifindex;
			struct { u16 id; __be16 proto; } encap[NF_FLOW_TABLE_ENCAP_MAX];
			u8 num_encaps;
			u8 ingress_vlans;
		} in;
		struct {
			u32 ifindex;
			u32 hw_ifindex;
			u8 h_source[ETH_ALEN];
			u8 h_dest[ETH_ALEN];
		} out;
		enum flow_offload_xmit_type xmit_type;
	} tuple[FLOW_OFFLOAD_DIR_MAX];
};

struct rhashtable {
	struct flow_offload *entries[8];
	unsigned int count;
};
struct rhashtable_params { int dummy; };
static const struct rhashtable_params nf_flow_offload_rhash_params = { 0 };

struct delayed_work { int dummy; };
struct nf_flowtable {
	unsigned int flags;
	struct rhashtable rhashtable;
	struct list_head list;
	struct delayed_work gc_work;
	struct net *net;
};

struct nf_flow_rule { int dummy; };
struct work_struct { void (*func)(struct work_struct *); };
struct flow_offload_work {
	struct work_struct work;
	struct nf_flowtable *flowtable;
	struct flow_offload *flow;
	enum flow_cls_command cmd;
};
#define GFP_ATOMIC 0
#define INIT_WORK(w, fn) ((w)->func = (fn))
static void *kmalloc(size_t sz, int gfp) { (void)gfp; return calloc(1, sz); }
static int system_power_efficient_wq_obj;
static void *system_power_efficient_wq = &system_power_efficient_wq_obj;
static unsigned int gc_cancel_calls, gc_rearm_calls, sync_rcu_calls;
static unsigned int event_seq, last_gc_cancel_seq, last_sync_rcu_seq, last_call_rcu_seq;
static void cancel_delayed_work_sync(struct delayed_work *w)
{
	(void)w;
	gc_cancel_calls++;
	last_gc_cancel_seq = ++event_seq;
}
static void queue_delayed_work(void *wq, struct delayed_work *w, unsigned long d)
{
	(void)wq; (void)w; (void)d;
	gc_rearm_calls++;
}
static void synchronize_rcu(void)
{
	sync_rcu_calls++;
	last_sync_rcu_seq = ++event_seq;
}

static DEFINE_MUTEX(flowtable_lock);
static LIST_HEAD(flowtables);
static atomic_t nf_flow_admission_fence = ATOMIC_INIT(0);

static bool nf_flow_admission_fenced(void)
{
	return atomic_read(&nf_flow_admission_fence) > 0;
}

static struct rcu_head *pending_rcu[16];
static unsigned int pending_rcu_n;
static unsigned int kfree_calls;
static unsigned int call_rcu_calls;
static bool allow_kfree_flow;

static void call_rcu(struct rcu_head *h, void (*fn)(struct rcu_head *))
{
	assert(pending_rcu_n < 16);
	h->fn = fn;
	pending_rcu[pending_rcu_n++] = h;
	call_rcu_calls++;
	last_call_rcu_seq = ++event_seq;
}

static void drain_rcu(void)
{
	unsigned int i;

	for (i = 0; i < pending_rcu_n; i++)
		pending_rcu[i]->fn(pending_rcu[i]);
	pending_rcu_n = 0;
}

static void kfree(void *p)
{
	kfree_calls++;
	if (allow_kfree_flow)
		free(p);
}

static int rhashtable_insert_fast(struct rhashtable *ht, struct rhash_head *node,
				  struct rhashtable_params params)
{
	(void)ht;
	(void)params;
	if (node->in_table)
		return -EEXIST;
	node->in_table = true;
	return 0;
}

static int rhashtable_remove_fast(struct rhashtable *ht, struct rhash_head *node,
				  struct rhashtable_params params)
{
	(void)ht;
	(void)params;
	if (!node->in_table)
		return -ENOENT;
	node->in_table = false;
	return 0;
}

static bool nf_flowtable_hw_offload(struct nf_flowtable *ft)
{
	return !!(ft->flags & NF_FLOWTABLE_HW_OFFLOAD);
}

static unsigned long flow_offload_get_timeout(struct flow_offload *flow)
{
	(void)flow;
	return 30 * HZ;
}

static bool nf_flow_has_expired(const struct flow_offload *flow)
{
	(void)flow;
	return false;
}
static bool nf_flow_custom_gc(struct nf_flowtable *ft, const struct flow_offload *flow)
{
	(void)ft; (void)flow;
	return false;
}
static void nf_flow_table_extend_ct_timeout(struct nf_conn *ct) { (void)ct; }
static void nf_flow_offload_stats(struct nf_flowtable *ft, struct flow_offload *flow)
{
	(void)ft; (void)flow;
}

static unsigned int teardowns, add_queues, del_queues, offload_flushes, del_wq_flushes;
static unsigned int rule_adds, work_dels, work_stats;
static bool fail_hw_del;
static bool set_teardown_during_add;
static bool set_ips_hw_on_add = true;

static void flow_offload_teardown(struct flow_offload *flow)
{
	teardowns++;
	clear_bit(IPS_OFFLOAD_BIT, &flow->ct->status);
	set_bit(NF_FLOW_TEARDOWN, &flow->flags);
}

static void nf_flow_offload_add(struct nf_flowtable *ft, struct flow_offload *flow)
{
	(void)ft; (void)flow;
	add_queues++;
}

static struct flow_offload *queued_del_flows[16];
static unsigned int queued_del_n;

static void nf_flow_offload_del(struct nf_flowtable *ft, struct flow_offload *flow)
{
	(void)ft;
	if (test_and_set_bit(NF_FLOW_PENDING, &flow->flags))
		return;
	set_bit(NF_FLOW_HW_DYING, &flow->flags);
	assert(queued_del_n < 16);
	queued_del_flows[queued_del_n++] = flow;
	del_queues++;
}

static void flush_del_wq_impl(void)
{
	unsigned int i;

	del_wq_flushes++;
	for (i = 0; i < queued_del_n; i++) {
		struct flow_offload *f = queued_del_flows[i];

		if (fail_hw_del) {
			clear_bit(NF_FLOW_HW_DYING, &f->flags);
		} else {
			clear_bit(IPS_HW_OFFLOAD_BIT, &f->ct->status);
			set_bit(NF_FLOW_HW_DEAD, &f->flags);
		}
		clear_bit(NF_FLOW_PENDING, &f->flags);
	}
	queued_del_n = 0;
}

static void nf_flow_table_offload_flush(struct nf_flowtable *ft)
{
	(void)ft;
	offload_flushes++;
	flush_del_wq_impl();
}

static void nf_flow_table_offload_flush_del(struct nf_flowtable *ft)
{
	(void)ft;
	flush_del_wq_impl();
}

static int nf_flow_table_iterate(struct nf_flowtable *ft,
				 void (*iter)(struct nf_flowtable *,
					      struct flow_offload *, void *),
				 void *data)
{
	unsigned int i;

	for (i = 0; i < ft->rhashtable.count; i++) {
		struct flow_offload *f = ft->rhashtable.entries[i];

		if (f && f->tuplehash[FLOW_OFFLOAD_DIR_ORIGINAL].node.in_table)
			iter(ft, f, data);
	}
	return 0;
}

static int nf_flow_offload_alloc(struct flow_offload_work *offload,
				 struct nf_flow_rule *rules[])
{
	(void)offload;
	rules[0] = NULL;
	rules[1] = NULL;
	return 0;
}

static int flow_offload_rule_add(struct flow_offload_work *offload,
				 struct nf_flow_rule *rules[])
{
	(void)rules;
	rule_adds++;
	if (set_teardown_during_add)
		set_bit(NF_FLOW_TEARDOWN, &offload->flow->flags);
	return set_ips_hw_on_add ? 0 : -ENOENT;
}

static void nf_flow_offload_destroy(struct nf_flow_rule *rules[]) { (void)rules; }

static void flow_offload_work_del(struct flow_offload_work *offload)
{
	work_dels++;
	if (fail_hw_del) {
		clear_bit(NF_FLOW_HW_DYING, &offload->flow->flags);
		return;
	}
	clear_bit(IPS_HW_OFFLOAD_BIT, &offload->flow->ct->status);
	set_bit(NF_FLOW_HW_DEAD, &offload->flow->flags);
}

static void flow_offload_work_stats(struct flow_offload_work *offload)
{
	(void)offload;
	work_stats++;
}

static u32 flow_offload_dst_cookie(struct flow_offload_tuple *t)
{
	if (t->l3proto == NFPROTO_IPV6 && t->dst_cache)
		return rt6_get_cookie(dst_rt6_info(t->dst_cache));
	return 0;
}

#include "nf_dst_check.h"
#include "nf_fill_route.h"
#include "nf_free_fns.h"
#include "nf_add_del.h"
#include "nf_gc_step.h"
#include "nf_do_cleanup.h"
#include "nf_revoke_fns.h"
#include "nf_quarantine_fn.h"
#include "nf_work_fns.h"

static void reset_counters(void)
{
	warn_count = 0;
	dst_releases = 0;
	ct_puts = 0;
	kfree_calls = 0;
	call_rcu_calls = 0;
	pending_rcu_n = 0;
	teardowns = 0;
	add_queues = 0;
	del_queues = 0;
	offload_flushes = 0;
	del_wq_flushes = 0;
	rule_adds = 0;
	work_dels = 0;
	work_stats = 0;
	queued_del_n = 0;
	fail_hw_del = false;
	set_teardown_during_add = false;
	set_ips_hw_on_add = true;
	allow_kfree_flow = false;
	gc_cancel_calls = 0;
	gc_rearm_calls = 0;
	sync_rcu_calls = 0;
	event_seq = last_gc_cancel_seq = last_sync_rcu_seq = last_call_rcu_seq = 0;
	nf_flow_admission_fence.v = 0;
	flowtables.next = &flowtables;
	flowtables.prev = &flowtables;
}

static void test_direct_dst_check_and_gc(void)
{
	struct net net = { .id = 1 };
	struct net_device dev_in = { .ifindex = 3, .net = &net };
	struct net_device dev_out = { .ifindex = 4, .net = &net };
	struct dst_entry dst0 = { .dev = &dev_out, .valid = true, .cookie = 7, .mtu = 1500, .refs = 1 };
	struct dst_entry dst1 = { .dev = &dev_in, .valid = true, .cookie = 9, .mtu = 1500, .refs = 1 };
	struct nf_conn ct = { .net = &net, .refs = 1, .status = 1UL << IPS_OFFLOAD_BIT };
	struct flow_offload flow = { .ct = &ct, .type = NF_FLOW_OFFLOAD_ROUTE };
	struct nf_flow_route route = {};
	struct nf_flowtable ft = { .flags = NF_FLOWTABLE_HW_OFFLOAD, .net = &net };

	reset_counters();
	flow.tuplehash[0].tuple.l3proto = NFPROTO_IPV6;
	flow.tuplehash[1].tuple.l3proto = NFPROTO_IPV6;
	route.tuple[0].dst = &dst0;
	route.tuple[0].xmit_type = FLOW_OFFLOAD_XMIT_DIRECT;
	route.tuple[0].out.ifindex = 4;
	route.tuple[0].out.hw_ifindex = 4;
	route.tuple[1].dst = &dst1;
	route.tuple[1].xmit_type = FLOW_OFFLOAD_XMIT_DIRECT;
	route.tuple[1].out.ifindex = 3;
	route.tuple[1].out.hw_ifindex = 3;

	flow_offload_fill_route(&flow, &route, FLOW_OFFLOAD_DIR_ORIGINAL);
	flow_offload_fill_route(&flow, &route, FLOW_OFFLOAD_DIR_REPLY);

	/* Both XMIT_DIRECT tuples retain dst_cache + dst_cookie without releasing. */
	assert(dst_releases == 0);
	assert(flow.tuplehash[0].tuple.dst_cache == &dst0);
	assert(flow.tuplehash[0].tuple.dst_cookie == 7);
	assert(flow.tuplehash[1].tuple.dst_cache == &dst1);
	assert(flow.tuplehash[1].tuple.dst_cookie == 9);
	assert(nf_flow_dst_check(&flow.tuplehash[0].tuple));
	assert(nf_flow_dst_check(&flow.tuplehash[1].tuple));

	/* Route invalidation makes nf_flow_dst_check fail on XMIT_DIRECT! */
	dst0.valid = false;
	assert(!nf_flow_dst_check(&flow.tuplehash[0].tuple));

	/* GC tears down the XMIT_DIRECT flow even if packets never hit SW hook. */
	set_bit(NF_FLOW_HW, &flow.flags);
	nf_flow_offload_gc_step(&ft, &flow, NULL);
	assert(teardowns == 1);
	assert(test_bit(NF_FLOW_TEARDOWN, &flow.flags));
	assert(del_queues == 1);

	/* RCU-deferred release: dst_cache stays intact until drain_rcu(). */
	flow.tuplehash[0].node.in_table = true;
	flow.tuplehash[1].node.in_table = true;
	flow_offload_del(&ft, &flow);
	/* Concurrent second flow_offload_del() must be a safe no-op. */
	flow_offload_del(&ft, &flow);
	assert(call_rcu_calls == 1);
	assert(dst_releases == 0);
	assert(flow.tuplehash[0].tuple.dst_cache == &dst0);
	drain_rcu();
	assert(dst_releases == 2);
	assert(dst0.refs == 0 && dst1.refs == 0);
	assert(ct_puts == 1 && kfree_calls == 1);
	assert(warn_count == 0);
}

static void test_do_cleanup_vlan_and_bridge_blackhole(void)
{
	struct net net = { .id = 1 };
	struct net_device phys = { .ifindex = 3, .net = &net };
	struct net_device vif = { .ifindex = 30, .net = &net };
	struct net_device br0 = { .ifindex = 40, .net = &net };
	struct net_device blackhole = { .ifindex = 99, .net = &net };
	struct dst_entry dst_vif = { .dev = &vif, .valid = true, .refs = 1 };
	struct dst_entry dst_bh = { .dev = &blackhole, .valid = false, .refs = 1 };
	struct nf_conn ct1 = { .net = &net, .refs = 1 };
	struct nf_conn ct2 = { .net = &net, .refs = 1 };
	struct flow_offload f_vif = { .ct = &ct1 };
	struct flow_offload f_br = { .ct = &ct2 };
	struct nf_flowtable ft = { .net = &net };

	reset_counters();
	/* VLAN vif down: iifidx is physical port 3, out.ifidx + dst->dev is vif 30. */
	f_vif.tuplehash[0].tuple.iifidx = phys.ifindex;
	f_vif.tuplehash[0].tuple.xmit_type = FLOW_OFFLOAD_XMIT_DIRECT;
	f_vif.tuplehash[0].tuple.out.ifidx = vif.ifindex;
	f_vif.tuplehash[0].tuple.out.hw_ifidx = phys.ifindex;
	f_vif.tuplehash[0].tuple.dst_cache = &dst_vif;
	nf_flow_table_do_cleanup(&ft, &f_vif, &vif);
	assert(teardowns == 1);

	/* br0 down after dst_dev_put() replaced dst->dev with blackhole_netdev. */
	f_br.tuplehash[0].tuple.iifidx = phys.ifindex;
	f_br.tuplehash[0].tuple.xmit_type = FLOW_OFFLOAD_XMIT_DIRECT;
	f_br.tuplehash[0].tuple.out.ifidx = 4;
	f_br.tuplehash[0].tuple.out.hw_ifidx = 4;
	f_br.tuplehash[0].tuple.dst_cache = &dst_bh;
	nf_flow_table_do_cleanup(&ft, &f_br, &br0);
	assert(teardowns == 2);
}

static void test_fence_blocks_add_and_refresh(void)
{
	struct net net = { .id = 1 };
	struct nf_conn ct = { .net = &net, .refs = 1 };
	struct flow_offload flow = { .ct = &ct };
	struct nf_flowtable ft = { .flags = NF_FLOWTABLE_HW_OFFLOAD, .net = &net };

	reset_counters();
	nf_flow_table_fence_begin();
	assert(nf_flow_admission_fenced());
	assert(flow_offload_add(&ft, &flow) == -EBUSY);
	assert(!flow.tuplehash[0].node.in_table && !flow.tuplehash[1].node.in_table);
	assert(add_queues == 0);

	/* Refresh must not queue ADD while fenced or tearing down. */
	flow_offload_refresh(&ft, &flow, true);
	assert(add_queues == 0);

	nf_flow_table_fence_end();
	assert(!nf_flow_admission_fenced());
	assert(flow_offload_add(&ft, &flow) == 0);
	assert(add_queues == 1);

	/* Refresh must not queue ADD once NF_FLOW_TEARDOWN is set. */
	set_bit(NF_FLOW_TEARDOWN, &flow.flags);
	flow_offload_refresh(&ft, &flow, true);
	assert(add_queues == 1);
	assert(warn_count == 0);
}

static void test_work_add_teardown_race(void)
{
	struct net net = { .id = 1 };
	struct nf_conn ct = { .net = &net, .refs = 1 };
	struct flow_offload flow = { .ct = &ct };
	struct nf_flowtable ft = { .flags = NF_FLOWTABLE_HW_OFFLOAD, .net = &net };
	struct flow_offload_work *w;

	/* Case A: TEARDOWN already set before work_add runs -> bail, no rule_add, no HW_DEAD. */
	reset_counters();
	set_bit(NF_FLOW_HW, &flow.flags);
	set_bit(NF_FLOW_TEARDOWN, &flow.flags);
	set_bit(NF_FLOW_PENDING, &flow.flags);
	w = calloc(1, sizeof(*w));
	w->flowtable = &ft;
	w->flow = &flow;
	w->cmd = FLOW_CLS_REPLACE;
	allow_kfree_flow = true;
	flow_offload_work_handler(&w->work);
	assert(rule_adds == 0);
	assert(!test_bit(IPS_HW_OFFLOAD_BIT, &ct.status));
	assert(!test_bit(NF_FLOW_HW_DEAD, &flow.flags));
	assert(!test_bit(NF_FLOW_PENDING, &flow.flags));

	/* Case B: TEARDOWN set concurrently during rule_add -> inline delete runs before PENDING clears. */
	reset_counters();
	memset(&flow, 0, sizeof(flow));
	flow.ct = &ct;
	ct.status = 0;
	set_bit(NF_FLOW_HW, &flow.flags);
	set_bit(NF_FLOW_PENDING, &flow.flags);
	set_teardown_during_add = true;
	w = calloc(1, sizeof(*w));
	w->flowtable = &ft;
	w->flow = &flow;
	w->cmd = FLOW_CLS_REPLACE;
	allow_kfree_flow = true;
	flow_offload_work_handler(&w->work);
	assert(rule_adds == 1);
	assert(work_dels == 1);
	assert(test_bit(NF_FLOW_HW_DEAD, &flow.flags));
	assert(!test_bit(IPS_HW_OFFLOAD_BIT, &ct.status));
	assert(!test_bit(NF_FLOW_PENDING, &flow.flags));
}

static bool match_ifidx_3(const struct flow_offload *flow, void *data)
{
	int target = *(int *)data;
	return flow->tuplehash[0].tuple.iifidx == target;
}

static void test_revoke_match_settled_and_unsettled(void)
{
	struct net net = { .id = 1 };
	struct nf_conn ct_a = { .net = &net, .refs = 1, .status = (1UL << IPS_OFFLOAD_BIT) | (1UL << IPS_HW_OFFLOAD_BIT) };
	struct nf_conn ct_b = { .net = &net, .refs = 1, .status = (1UL << IPS_OFFLOAD_BIT) | (1UL << IPS_HW_OFFLOAD_BIT) };
	struct flow_offload fa = { .ct = &ct_a };
	struct flow_offload fb = { .ct = &ct_b };
	struct nf_flowtable ft = { .flags = NF_FLOWTABLE_HW_OFFLOAD, .net = &net };
	unsigned int unresolved = 99;
	int target = 3;
	int rc;

	/* Settled match: only matching flow torn down & retired, NAT/ct preserved! */
	reset_counters();
	fa.tuplehash[0].tuple.iifidx = 3;
	fb.tuplehash[0].tuple.iifidx = 4;
	fa.tuplehash[0].node.in_table = fa.tuplehash[1].node.in_table = true;
	fb.tuplehash[0].node.in_table = fb.tuplehash[1].node.in_table = true;
	set_bit(NF_FLOW_HW, &fa.flags);
	set_bit(NF_FLOW_HW, &fb.flags);
	ft.rhashtable.entries[0] = &fa;
	ft.rhashtable.entries[1] = &fb;
	ft.rhashtable.count = 2;
	list_add_tail(&ft.list, &flowtables);

	rc = nf_flow_table_revoke_match(&net, match_ifidx_3, &target, true, &unresolved);
	assert(rc == 0 && unresolved == 0);
	/* Finding 1: gc_work cancelled before revoke, synchronize_rcu() before call_rcu(), gc re-armed. */
	assert(gc_cancel_calls == 1 && gc_rearm_calls == 1 && sync_rcu_calls >= 1);
	assert(last_gc_cancel_seq < last_sync_rcu_seq);
	assert(last_sync_rcu_seq < last_call_rcu_seq);
	assert(test_bit(NF_FLOW_TEARDOWN, &fa.flags));
	assert(!test_bit(IPS_OFFLOAD_BIT, &ct_a.status));
	assert(test_bit(NF_FLOW_HW_DEAD, &fa.flags));
	assert(!fa.tuplehash[0].node.in_table);
	assert(!test_bit(NF_FLOW_TEARDOWN, &fb.flags));
	assert(test_bit(IPS_OFFLOAD_BIT, &ct_b.status));
	assert(fb.tuplehash[0].node.in_table);
	drain_rcu();
	assert(ct_a.refs == 0 && ct_b.refs == 1); /* ct_a not destroyed; only flow ref put */

	/* Finding 1 defense-in-depth: work_alloc refuses NF_FLOW_HW_DEAD and clears PENDING. */
	{
		struct flow_offload dead = { .ct = &ct_b };

		set_bit(NF_FLOW_HW_DEAD, &dead.flags);
		assert(nf_flow_offload_work_alloc(&ft, &dead, FLOW_CLS_STATS) == NULL);
		assert(!test_bit(NF_FLOW_PENDING, &dead.flags));
	}

	/* Finding 6: quarantine RCU defers route dst_release() until RCU callback fires. */
	{
		struct dst_entry qdst = { .valid = true, .refs = 1 };
		struct nf_conn qct = { .net = &net, .refs = 1 };
		struct flow_offload qflow = { .ct = &qct, .type = NF_FLOW_OFFLOAD_ROUTE };

		dst_releases = 0;
		ct_puts = 0;
		qflow.tuplehash[0].tuple.dst_cache = &qdst;
		call_rcu(&qflow.rcu_head, flow_offload_quarantine_rcu);
		assert(dst_releases == 0 && qdst.refs == 1 && qct.refs == 1);
		drain_rcu();
		assert(dst_releases == 1 && qdst.refs == 0 && qct.refs == 0 && qflow.ct == NULL);
	}

	/* Unsettled HW delete: returns -EBUSY, unresolved == 1, flow retained. */
	reset_counters();
	ct_b.status = (1UL << IPS_OFFLOAD_BIT) | (1UL << IPS_HW_OFFLOAD_BIT);
	fb.flags = 0;
	set_bit(NF_FLOW_HW, &fb.flags);
	fb.tuplehash[0].node.in_table = fb.tuplehash[1].node.in_table = true;
	ft.rhashtable.entries[0] = &fb;
	ft.rhashtable.count = 1;
	list_add_tail(&ft.list, &flowtables);
	fail_hw_del = true;

	rc = nf_flow_table_revoke_all(&net, true, &unresolved);
	assert(rc == -EBUSY && unresolved == 1);
	assert(test_bit(NF_FLOW_TEARDOWN, &fb.flags));
	assert(!test_bit(NF_FLOW_HW_DEAD, &fb.flags));
	assert(fb.tuplehash[0].node.in_table);
	assert(warn_count == 0);
}

int main(void)
{
	test_direct_dst_check_and_gc();
	test_do_cleanup_vlan_and_bridge_blackhole();
	test_fence_blocks_add_and_refresh();
	test_work_add_teardown_race();
	test_revoke_match_settled_and_unsettled();
	puts("nf_flow_revocation: OK (5/5 suites)");
	return 0;
}
