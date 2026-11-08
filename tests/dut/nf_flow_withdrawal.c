// SPDX-License-Identifier: GPL-2.0
/* Host fault model for the extracted netfilter hardware withdrawal path. */
#include <assert.h>
#include <errno.h>
#include <netinet/in.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint16_t __be16;
typedef uint32_t u32;
enum flow_offload_tuple_dir {
	FLOW_OFFLOAD_DIR_ORIGINAL, FLOW_OFFLOAD_DIR_REPLY, FLOW_OFFLOAD_DIR_MAX,
};
enum flow_cls_command { FLOW_CLS_REPLACE, FLOW_CLS_DESTROY, FLOW_CLS_STATS };
enum {
	NF_FLOW_HW, NF_FLOW_HW_DYING, NF_FLOW_HW_DEAD, NF_FLOW_PENDING,
	NF_FLOW_HW_BIDIRECTIONAL, NF_FLOW_CLOSING, NF_FLOW_TEARDOWN,
	IPS_HW_OFFLOAD_BIT,
};
#define ETH_P_ALL 3
#define TC_SETUP_CLSFLOWER 1
#define NF_FLOWTABLE_COUNTER 1
#define NF_FLOWTABLE_HW_OFFLOAD 2
#define TCP_CONNTRACK_LAST_ACK 1
#define TCP_CONNTRACK_TIME_WAIT 2
#define NF_FLOW_TABLE_STAT_DEC_ATOMIC(net, count) ((void)(net))
#define net_warn_ratelimited(...) do { } while (0)
#define pr_info_ratelimited(...) do { } while (0)
#define pr_warn_ratelimited(...) do { } while (0)
#define pr_warn(...) do { } while (0)
#define rcu_read_lock() do { } while (0)
#define rcu_read_unlock() do { } while (0)
#define READ_ONCE(value) (value)
#define GFP_KERNEL 0
#define smp_mb__before_atomic() ((void)0)
#define WARN_ON_ONCE(condition) assert(!(condition))
#define read_pnet(p) (*(p))
#define container_of(p, type, member) \
	((type *)((char *)(p) - offsetof(type, member)))

struct list_head { struct list_head *next, *prev; };
#define list_entry(p, type, member) container_of(p, type, member)
#define list_for_each_entry(pos, head, member) \
	for (pos = list_entry((head)->next, typeof(*pos), member); \
	     &pos->member != (head); \
	     pos = list_entry(pos->member.next, typeof(*pos), member))

struct flow_stats { int unused; };
struct netlink_ext_ack { int unused; };
struct flow_cls_offload {
	struct { __be16 protocol; int prio; struct netlink_ext_ack *extack; } common;
	enum flow_cls_command command;
	unsigned long cookie;
	void *rule;
	struct flow_stats stats;
};
struct flow_block_cb {
	int (*cb)(int, void *, void *);
	void *cb_priv;
	struct list_head list;
};
struct nf_flow_rule { void *rule; };
struct flow_offload_tuple { int dir; u32 iifidx; };
struct rhash_head { int unused; };
struct flow_offload_tuple_rhash {
	struct rhash_head node;
	struct flow_offload_tuple tuple;
};
struct rcu_head { void (*func)(struct rcu_head *); };
struct net;
struct nf_conn {
	unsigned long status;
	int lock;
	struct { struct { int state; } tcp; } proto;
	struct net *net;
};
enum flow_offload_type { NF_FLOW_OFFLOAD_UNSPEC, NF_FLOW_OFFLOAD_ROUTE };
struct flow_offload {
	struct flow_offload_tuple_rhash tuplehash[2];
	struct nf_conn *ct;
	unsigned long flags;
	int type;
	struct rcu_head rcu_head;
};
struct net { int unused; };
struct nf_flowtable;
struct nf_flowtable_type {
	void (*free)(struct nf_flowtable *);
	void *owner;
};
struct delayed_work { int unused; };
struct rhashtable { int unused; };
struct nf_flowtable {
	int priority, flow_block_lock, flags;
	struct { struct list_head cb_list; } flow_block;
	struct net *net;
	const struct nf_flowtable_type *type;
	struct list_head list;
	struct delayed_work gc_work;
	struct rhashtable rhashtable;
};
struct nft_flowtable {
	struct nf_flowtable data;
	struct list_head hook_list;
	char *name;
};
struct work_struct { int unused; };
struct flow_offload_work {
	struct work_struct work;
	struct nf_flowtable *flowtable;
	struct flow_offload *flow;
	enum flow_cls_command cmd;
};
struct tcphdr { bool syn, fin, rst; };
struct sk_buff { struct tcphdr tcp; };

static bool test_bit(int n, const unsigned long *v) { return (*v >> n) & 1; }
static void set_bit(int n, unsigned long *v) { *v |= 1UL << n; }
static void clear_bit(int n, unsigned long *v) { *v &= ~(1UL << n); }
static void down_read(int *lock) { assert(!*lock); *lock = 1; }
static void up_read(int *lock) { assert(*lock); *lock = 0; }
static void spin_lock_bh(int *lock) { assert(!*lock); *lock = 1; }
static void spin_unlock_bh(int *lock) { assert(*lock); *lock = 0; }
static int nf_ct_protonum(struct nf_conn *ct) { return IPPROTO_TCP; }
static void *skb_network_header(struct sk_buff *skb) { return &skb->tcp; }
static void *tracked_nft_flowtable;
static bool tracked_table_freed;
static void kfree(void *p)
{
	if (tracked_nft_flowtable && p == tracked_nft_flowtable)
		tracked_table_freed = true;
	free(p);
}
static void flow_offload_work_stats(struct flow_offload_work *offload) { abort(); }
static struct nf_flow_rule *add_rules[FLOW_OFFLOAD_DIR_MAX];
static int nf_flow_offload_alloc(struct flow_offload_work *offload,
				 struct nf_flow_rule *rules[])
{
	rules[0] = add_rules[0];
	rules[1] = add_rules[1];
	return 0;
}
static void nf_flow_offload_destroy(struct nf_flow_rule *rules[]) {}
static unsigned int teardowns, queued_deletes, gc_removes;
static unsigned int event_seq, last_queue_seq, last_flush_seq;
static unsigned int hash_removes, ct_puts, route_releases;
static void flow_offload_teardown(struct flow_offload *flow)
{
	teardowns++;
	set_bit(NF_FLOW_TEARDOWN, &flow->flags);
}
static void nf_flow_offload_del(struct nf_flowtable *table, struct flow_offload *flow)
{
	queued_deletes++;
	last_queue_seq = ++event_seq;
}
static bool nf_flow_has_expired(struct flow_offload *flow) { return false; }
static bool nf_ct_is_dying(struct nf_conn *ct) { return false; }
static bool nf_flow_custom_gc(struct nf_flowtable *table, struct flow_offload *flow)
{
	return false;
}
static void nf_flow_table_extend_ct_timeout(struct nf_conn *ct) {}
static void nf_flow_offload_stats(struct nf_flowtable *table, struct flow_offload *flow) {}
static void flow_offload_del(struct nf_flowtable *table, struct flow_offload *flow)
{
	gc_removes++;
}
static struct flow_offload *destroy_test_flow;
static void nf_flow_table_iterate(struct nf_flowtable *table,
				 void (*fn)(struct nf_flowtable *,
					    struct flow_offload *, void *),
				 void *data)
{
	if (destroy_test_flow && !gc_removes && hash_removes < 2)
		fn(table, destroy_test_flow, data);
}

#include "nf_flow_withdrawal_body.h"

struct net_device {
	u32 ifindex;
	struct net *net;
	bool vlan;
	struct net_device *physical;
	int refs;
};
static struct net init_net;
static struct net_device *devices[3];
static struct net_device *dev_get_by_index(struct net *net, u32 ifindex)
{
	unsigned int i;

	for (i = 0; i < sizeof(devices) / sizeof(devices[0]); i++) {
		if (devices[i] && devices[i]->ifindex == ifindex) {
			devices[i]->refs++;
			return devices[i];
		}
	}
	return NULL;
}
static bool is_vlan_dev(const struct net_device *dev) { return dev->vlan; }
static struct net_device *vlan_dev_real_dev(struct net_device *dev)
{
	return dev->physical;
}
static void dev_put(struct net_device *dev)
{
	assert(dev->refs > 0);
	dev->refs--;
}

static struct net *nf_ct_net(struct nf_conn *ct) { return ct->net; }
static struct net *dev_net(struct net_device *dev) { return dev->net; }
static bool net_eq(struct net *a, struct net *b) { return a == b; }
static int flowtable_lock;
static void mutex_lock(int *lock) { assert(!*lock); *lock = 1; }
static void mutex_unlock(int *lock) { assert(*lock); *lock = 0; }
static void list_del(struct list_head *node) { assert(node); }
static void cancel_delayed_work_sync(struct delayed_work *work) { assert(work); }
static int workqueues[3];
static void *nf_flow_offload_add_wq = &workqueues[0];
static void *nf_flow_offload_del_wq = &workqueues[1];
static void *nf_flow_offload_stats_wq = &workqueues[2];
static void flush_workqueue(void *queue)
{
	assert(queue);
	last_flush_seq = ++event_seq;
}
static const int nf_flow_offload_rhash_params;
static void rhashtable_remove_fast(struct rhashtable *hash,
				   struct rhash_head *node, int params)
{
	assert(hash && node);
	hash_removes++;
}
static void nf_ct_put(struct nf_conn *ct)
{
	assert(ct);
	ct_puts++;
}
static void flow_offload_route_release(struct flow_offload *flow)
{
	route_releases++;
}
static void call_rcu(struct rcu_head *head, void (*func)(struct rcu_head *))
{
	func(head);
}
static bool nf_flowtable_hw_offload(struct nf_flowtable *table)
{
	return table->flags & NF_FLOWTABLE_HW_OFFLOAD;
}
static unsigned int hash_destroyed, hash_destroyed_with_entry;
static void rhashtable_free_and_destroy(struct rhashtable *hash,
				       void (*free_fn)(void *, void *),
				       void *arg)
{
	assert(hash && !free_fn && !arg);
	hash_destroyed++;
	hash_destroyed_with_entry += destroy_test_flow && !gc_removes &&
				     hash_removes < 2;
}
static void nft_hooks_destroy(struct list_head *hooks) { assert(hooks); }
static void module_put(void *module) { assert(module); }

#include "nf_flow_destroy_body.h"

#define ECHO_CONTINUE 42
static int ask_echo_filter(bool have_meta_iif, struct net_device *ingress_dev,
			   struct net_device *egress_dev, u32 true_iif,
			   struct flow_cls_offload *f)
{
	(void)f;
#include "ask_echo_filter_body.h"
	return ECHO_CONTINUE;
}

typedef uint64_t u64;
struct xarray {
	struct { unsigned long cookie, value; bool present; } entry[2];
};
static struct xarray ask_offload_first_seen;
static unsigned int ask_offload_delay_ms;
static unsigned long jiffies;
static void *xa_mk_value(unsigned long value)
{
	return (void *)((value << 1) | 1);
}
static bool xa_is_value(const void *value) { return (uintptr_t)value & 1; }
static unsigned long xa_to_value(const void *value)
{
	return (uintptr_t)value >> 1;
}
static int xa_err(const void *value) { return 0; }
static void *xa_load(struct xarray *xa, unsigned long cookie)
{
	unsigned int i;

	for (i = 0; i < sizeof(xa->entry) / sizeof(xa->entry[0]); i++)
		if (xa->entry[i].present && xa->entry[i].cookie == cookie)
			return xa_mk_value(xa->entry[i].value);
	return NULL;
}
static void *xa_store(struct xarray *xa, unsigned long cookie, void *value,
		      int flags)
{
	unsigned int i, free_entry = 2;

	assert(xa_is_value(value));
	for (i = 0; i < sizeof(xa->entry) / sizeof(xa->entry[0]); i++) {
		if (xa->entry[i].present && xa->entry[i].cookie == cookie)
			break;
		if (!xa->entry[i].present && free_entry == 2)
			free_entry = i;
	}
	if (i == sizeof(xa->entry) / sizeof(xa->entry[0]))
		i = free_entry;
	assert(i < sizeof(xa->entry) / sizeof(xa->entry[0]));
	xa->entry[i].cookie = cookie;
	xa->entry[i].value = xa_to_value(value);
	xa->entry[i].present = true;
	return NULL;
}
static void xa_erase(struct xarray *xa, unsigned long cookie)
{
	unsigned int i;

	for (i = 0; i < sizeof(xa->entry) / sizeof(xa->entry[0]); i++)
		if (xa->entry[i].present && xa->entry[i].cookie == cookie)
			xa->entry[i].present = false;
}
static unsigned long msecs_to_jiffies(unsigned int ms) { return ms; }
#define time_before(a, b) ((long)((a) - (b)) < 0)
#include "ask_delay_body.h"

struct ask_flow { u64 generation; };
struct ask_flow_table { int unused; };
static struct ask_flow_table ask_table;
static struct ask_flow old_owner = { .generation = 7 };
static u64 old_cookie;
static bool old_owner_present;
static struct ask_flow *ask_flow_lookup(struct ask_flow_table *t, u64 cookie)
{
	assert(t == &ask_table);
	return old_owner_present && cookie == old_cookie ? &old_owner : NULL;
}
static bool ask_flow_gen_is_current(struct ask_flow_table *t, u64 cookie,
				    u64 generation)
{
	assert(t == &ask_table && cookie == old_cookie);
	assert(generation == old_owner.generation);
	return false; /* retained, tombstoned owner */
}
#include "ask_retained_body.h"

static int ask_early_delay_guard(struct flow_cls_offload *f)
{
	struct ask_flow_table *t = &ask_table;
	int rc;

#include "ask_early_delay_body.h"
	return ECHO_CONTINUE;
}

static int ask_dedup_guard(struct net_device *ingress_dev,
			   struct flow_cls_offload *f)
{
	struct ask_flow_table *t = &ask_table;

	(void)ingress_dev;
#include "ask_dedup_body.h"
	return ECHO_CONTINUE;
}

struct add_rule_data {
	bool have_meta_iif;
	u32 true_iif;
	struct net_device *egress_dev;
};
struct mock_add {
	struct flow_offload *flow;
	int owner_result[FLOW_OFFLOAD_DIR_MAX];
	unsigned int owner_calls[FLOW_OFFLOAD_DIR_MAX];
	bool retained_owner;
};
struct add_block {
	struct mock_add *add;
	struct net_device *dev;
};

static int add_callback(int type, void *data, void *priv)
{
	struct flow_cls_offload *f = data;
	struct add_block *block = priv;
	struct mock_add *add = block->add;
	struct add_rule_data *rule = f->rule;
	int dir, rc;

	assert(type == TC_SETUP_CLSFLOWER && f->command == FLOW_CLS_REPLACE);
	dir = f->cookie == (unsigned long)&add->flow->tuplehash[0].tuple ? 0 : 1;
	assert(f->cookie == (unsigned long)&add->flow->tuplehash[dir].tuple);
	rc = ask_early_delay_guard(f);
	if (rc != ECHO_CONTINUE)
		return rc;
	rc = ask_echo_filter(rule->have_meta_iif, block->dev, rule->egress_dev,
			     rule->true_iif, f);
	if (rc == ECHO_CONTINUE) {
		add->owner_calls[dir]++;
		if (dir == FLOW_OFFLOAD_DIR_ORIGINAL && add->retained_owner) {
			rc = ask_dedup_guard(block->dev, f);
			if (rc != ECHO_CONTINUE)
				return rc;
		}
		return add->owner_result[dir];
	}
	assert(rc < 0);
	return rc;
}

struct mock_owner {
	struct flow_offload *flow;
	bool present[2];
	int fail_dir;
	int unrelated_result;
	unsigned int calls[2];
	unsigned int unrelated_calls;
};

static int owner_callback(int type, void *data, void *priv)
{
	struct flow_cls_offload *f = data;
	struct mock_owner *owner = priv;
	int dir;

	assert(type == TC_SETUP_CLSFLOWER && f->command == FLOW_CLS_DESTROY);
	dir = f->cookie == (unsigned long)&owner->flow->tuplehash[0].tuple ? 0 : 1;
	assert(f->cookie == (unsigned long)&owner->flow->tuplehash[dir].tuple);
	owner->calls[dir]++;
	if (dir == owner->fail_dir)
		return -EAGAIN;
	owner->present[dir] = false;
	return 0;
}

static int unrelated_callback(int type, void *data, void *priv)
{
	struct mock_owner *owner = priv;

	assert(type == TC_SETUP_CLSFLOWER);
	owner->unrelated_calls++;
	return owner->unrelated_result;
}

static int callback_count(struct nf_flowtable *table, struct flow_offload *flow,
			  enum flow_offload_tuple_dir dir)
{
	return nf_flow_offload_tuple(table, flow, NULL,
				     dir, 0,
				     FLOW_CLS_DESTROY, NULL,
				     &table->flow_block.cb_list);
}

static void init_callbacks(struct nf_flowtable *table,
			   struct flow_block_cb *block,
			   struct flow_block_cb *other)
{
	struct list_head *head = &table->flow_block.cb_list;

	head->next = &block->list;
	head->prev = &other->list;
	block->list.prev = head;
	block->list.next = &other->list;
	other->list.prev = &block->list;
	other->list.next = head;
}

struct add_observation {
	bool hw_status, live;
	int syn_verdict;
};

static void run_add_work(struct nf_flowtable *table, struct flow_offload *flow)
{
	struct flow_offload_work *work = calloc(1, sizeof(*work));

	assert(work);
	set_bit(NF_FLOW_PENDING, &flow->flags);
	work->flowtable = table;
	work->flow = flow;
	work->cmd = FLOW_CLS_REPLACE;
	flow_offload_work_handler(&work->work);
}

static struct add_observation run_add_case(bool owner_success,
					   bool retained_owner, bool delayed,
					   bool reverse_order,
					   bool retry_after_destroy)
{
	struct nf_conn ct = {};
	struct flow_offload flow = { .ct = &ct };
	struct net net = {};
	struct nf_flowtable table = { .net = &net };
	struct net_device a = { .ifindex = 5 }, b = { .ifindex = 6 };
	struct net_device vlan = { .ifindex = 100, .vlan = true, .physical = &a };
	struct add_rule_data rule_data[] = {
		{ .have_meta_iif = true, .true_iif = a.ifindex, .egress_dev = &b },
		{ .have_meta_iif = true, .true_iif = b.ifindex, .egress_dev = &a },
	};
	struct nf_flow_rule rules[] = {
		{ .rule = &rule_data[0] }, { .rule = &rule_data[1] },
	};
	struct mock_add add = {
		.flow = &flow,
		.owner_result = { owner_success ? 0 : -EAGAIN, -EAGAIN },
		.retained_owner = retained_owner,
	};
	struct add_block priv_a = { .add = &add, .dev = &a };
	struct add_block priv_b = { .add = &add, .dev = &b };
	struct flow_block_cb cb_a = { .cb = add_callback, .cb_priv = &priv_a };
	struct flow_block_cb cb_b = { .cb = add_callback, .cb_priv = &priv_b };
	struct flow_cls_offload cls = {};
	struct sk_buff syn = { .tcp.syn = true };
	struct add_observation result;

	devices[0] = &a;
	devices[1] = &b;
	devices[2] = &vlan;
	assert(ask_echo_filter(true, &a, &b, vlan.ifindex, &cls) == ECHO_CONTINUE);
	assert(ask_echo_filter(true, &b, &a, vlan.ifindex, &cls) != ECHO_CONTINUE);
	assert(ask_echo_filter(false, &b, &b, 0, &cls) != ECHO_CONTINUE);
	assert(ask_echo_filter(false, &a, &b, 0, &cls) == ECHO_CONTINUE);
	init_callbacks(&table, reverse_order ? &cb_b : &cb_a,
		       reverse_order ? &cb_a : &cb_b);
	add_rules[0] = &rules[0];
	add_rules[1] = &rules[1];
	old_cookie = (u64)&flow.tuplehash[0].tuple;
	old_owner_present = retained_owner;
	ask_offload_first_seen = (struct xarray){};
	ask_offload_delay_ms = delayed ? 500 : 0;
	jiffies = 100;
	flow.tuplehash[0].tuple.dir = FLOW_OFFLOAD_DIR_ORIGINAL;
	flow.tuplehash[1].tuple.dir = FLOW_OFFLOAD_DIR_REPLY;
	set_bit(NF_FLOW_HW, &flow.flags);
	set_bit(NF_FLOW_HW_BIDIRECTIONAL, &flow.flags);
	run_add_work(&table, &flow);
	if (retained_owner)
		assert(test_bit(IPS_HW_OFFLOAD_BIT, &ct.status));
	if (retry_after_destroy) {
		/* The tombstoned owner settles and the delete clears status. */
		old_owner_present = false;
		clear_bit(IPS_HW_OFFLOAD_BIT, &ct.status);
		/* The extracted DESTROY path erases this cookie's age entry. */
		xa_erase(&ask_offload_first_seen,
			 (unsigned long)&flow.tuplehash[0].tuple);
		xa_erase(&ask_offload_first_seen,
			 (unsigned long)&flow.tuplehash[1].tuple);
		jiffies += 1000;
		run_add_work(&table, &flow);
		assert(!test_bit(IPS_HW_OFFLOAD_BIT, &ct.status));
		jiffies += 500;
		run_add_work(&table, &flow);
	}
	/* A retained owner is reported before the age gate or owner path. */
	assert(add.owner_calls[0] == (retry_after_destroy ||
				      (!delayed && !retained_owner)));
	assert(add.owner_calls[1] == (!delayed || retry_after_destroy));
	assert(!test_bit(NF_FLOW_PENDING, &flow.flags));
	result.hw_status = test_bit(IPS_HW_OFFLOAD_BIT, &ct.status);
	result.live = nf_flow_hw_record_live(&flow);
	set_bit(NF_FLOW_CLOSING, &flow.flags);
	result.syn_verdict = nf_flow_state_check(&table, &flow, IPPROTO_TCP,
						 &syn, 0);
	assert(!a.refs && !b.refs && !vlan.refs);
	devices[0] = devices[1] = devices[2] = NULL;
	add_rules[0] = add_rules[1] = NULL;
	old_owner_present = false;
	ask_offload_delay_ms = 0;
	return result;
}

static unsigned int test_add_fanout(void)
{
	unsigned int unsafe = 0;
	int reverse;

	for (reverse = 0; reverse < 2; reverse++) {
		struct add_observation failed = run_add_case(false, false,
							    false, reverse, false);
		struct add_observation retained = run_add_case(false, true,
							      false, reverse, false);
		struct add_observation delayed = run_add_case(false, true,
							     true, reverse, false);
		struct add_observation retried = run_add_case(true, true,
							     true, reverse, true);
		struct add_observation installed = run_add_case(true, false,
							       false, reverse, false);

		printf("add fanout, %s order: clean declines: "
		       "HW_OFFLOAD=%u live=%u SYN verdict=%d\n",
		       reverse ? "reversed" : "normal", failed.hw_status,
		       failed.live, failed.syn_verdict);
		if (failed.hw_status || failed.live)
			unsafe++; /* false status when no record exists */
		printf("  reused cookie, retained owner: "
		       "HW_OFFLOAD=%u live=%u SYN verdict=%d\n",
		       retained.hw_status, retained.live, retained.syn_verdict);
		if (!retained.hw_status || !retained.live ||
		    retained.syn_verdict != -1)
			unsafe++; /* unsafe fallback while a record remains */
		printf("  reused cookie during offload delay: "
		       "HW_OFFLOAD=%u live=%u SYN verdict=%d\n",
		       delayed.hw_status, delayed.live, delayed.syn_verdict);
		if (!delayed.hw_status || !delayed.live ||
		    delayed.syn_verdict != -1)
			unsafe++;
		printf("  settled owner, delayed retry: HW_OFFLOAD=%u "
		       "live=%u SYN verdict=%d\n",
		       retried.hw_status, retried.live, retried.syn_verdict);
		if (!retried.hw_status || !retried.live ||
		    retried.syn_verdict != -1)
			unsafe++;
		assert(installed.hw_status && installed.live &&
		       installed.syn_verdict == -1);
	}
	return unsafe;
}

static bool run_case(int fail_dir)
{
	struct nf_conn ct = { .proto.tcp.state = TCP_CONNTRACK_LAST_ACK };
	struct flow_offload flow = { .ct = &ct };
	struct net net = {};
	struct nf_flowtable table = { .net = &net };
	struct mock_owner owner = {
		.flow = &flow, .present = { true, true }, .fail_dir = fail_dir,
		.unrelated_result = 0,
	};
	struct flow_block_cb other = { .cb = unrelated_callback };
	struct flow_block_cb block = { .cb = owner_callback, .cb_priv = &owner };
	struct flow_offload_work *work = calloc(1, sizeof(*work));
	struct sk_buff syn = { .tcp.syn = true };
	unsigned int queued_before;
	bool live;
	int verdict;

	assert(work);
	other.cb_priv = &owner;
	init_callbacks(&table, &block, &other);
	flow.tuplehash[0].tuple.dir = FLOW_OFFLOAD_DIR_ORIGINAL;
	flow.tuplehash[1].tuple.dir = FLOW_OFFLOAD_DIR_REPLY;
	set_bit(NF_FLOW_HW, &flow.flags);
	set_bit(NF_FLOW_HW_DYING, &flow.flags);
	set_bit(NF_FLOW_HW_BIDIRECTIONAL, &flow.flags);
	set_bit(NF_FLOW_CLOSING, &flow.flags);
	set_bit(NF_FLOW_PENDING, &flow.flags);
	set_bit(IPS_HW_OFFLOAD_BIT, &ct.status);
	gc_removes = 0;
	set_bit(NF_FLOW_TEARDOWN, &flow.flags);
	nf_flow_offload_gc_step(&table, &flow, NULL);
	assert(gc_removes == 0);
	clear_bit(NF_FLOW_TEARDOWN, &flow.flags);
	work->flowtable = &table;
	work->flow = &flow;
	work->cmd = FLOW_CLS_DESTROY;
	if (fail_dir >= 0) {
		int callbacks = callback_count(&table, &flow, fail_dir);

		assert(callbacks == -EBUSY);
		assert(owner.unrelated_calls == 1);
		assert(owner.present[fail_dir] && owner.present[1 - fail_dir]);
		owner.present[0] = true;
		owner.present[1] = true;
		owner.calls[0] = 0;
		owner.calls[1] = 0;
		owner.unrelated_calls = 0;
	}
	flow_offload_work_handler(&work->work);
	assert(owner.calls[0] == 1 && owner.calls[1] == 1);
	assert(owner.unrelated_calls == 2);
	assert(!test_bit(NF_FLOW_PENDING, &flow.flags));
	live = nf_flow_hw_record_live(&flow);
	verdict = nf_flow_state_check(&table, &flow, IPPROTO_TCP, &syn, 0);
	queued_before = queued_deletes;
	nf_flow_offload_gc_step(&table, &flow, NULL);

	if (fail_dir < 0) {
		assert(!owner.present[0] && !owner.present[1]);
		assert(!live && test_bit(NF_FLOW_HW_DEAD, &flow.flags));
		assert(!test_bit(IPS_HW_OFFLOAD_BIT, &ct.status));
		assert(ct.proto.tcp.state == TCP_CONNTRACK_TIME_WAIT && verdict == 1);
		assert(gc_removes == 1);
		puts("successful deletion: both directions settled");
		return true;
	}

	assert(owner.present[fail_dir] && !owner.present[1 - fail_dir]);
	/* HW_DYING was cleared, so GC queues the delete again. */
	assert(queued_deletes == queued_before + 1);
	printf("failed %s deletion: retained record=%u HW_DEAD=%u HW_OFFLOAD=%u "
	       "live=%u SYN verdict=%d GC removed=%u\n",
	       fail_dir ? "reply" : "original", owner.present[fail_dir],
	       test_bit(NF_FLOW_HW_DEAD, &flow.flags),
	       test_bit(IPS_HW_OFFLOAD_BIT, &ct.status), live, verdict, gc_removes);
	return live && !test_bit(NF_FLOW_HW_DEAD, &flow.flags) && verdict == -1 &&
	       gc_removes == 0;
}

static void test_error_fanout_is_disambiguated(void)
{
	struct nf_conn ct = {};
	struct flow_offload flow = { .ct = &ct };
	struct net net = {};
	struct nf_flowtable table = { .net = &net };
	struct mock_owner owner = {
		.flow = &flow, .present = { true, true }, .fail_dir = -1,
	};
	struct flow_block_cb other = {
		.cb = unrelated_callback, .cb_priv = &owner,
	};
	struct flow_block_cb block = {
		.cb = owner_callback, .cb_priv = &owner,
	};
	int owner_failure_count, nonowner_decline_count;

	init_callbacks(&table, &block, &other);
	owner.fail_dir = FLOW_OFFLOAD_DIR_ORIGINAL;
	owner.unrelated_result = 0;
	owner_failure_count = callback_count(&table, &flow,
					     FLOW_OFFLOAD_DIR_ORIGINAL);
	assert(owner_failure_count == -EBUSY && owner.present[0]);

	owner.present[0] = true;
	owner.calls[0] = 0;
	owner.unrelated_calls = 0;
	owner.fail_dir = -1;
	owner.unrelated_result = -EOPNOTSUPP;
	nonowner_decline_count = callback_count(&table, &flow,
						FLOW_OFFLOAD_DIR_ORIGINAL);
	assert(nonowner_decline_count == 1 && !owner.present[0]);
	puts("callback fanout: owner failure is -EBUSY, non-owner decline counts 1");
}

static bool run_table_destroy_case(bool settled)
{
	struct nf_conn ct = {};
	struct flow_offload flow = { .ct = &ct };
	struct nft_flowtable *nft = calloc(1, sizeof(*nft));
	int owner;
	struct nf_flowtable_type type = {
		.free = nf_flow_table_free, .owner = &owner,
	};
	bool safe;

	assert(nft);
	nft->data.type = &type;
	nft->data.flags = NF_FLOWTABLE_HW_OFFLOAD;
	set_bit(NF_FLOW_HW, &flow.flags);
	set_bit(NF_FLOW_HW_DYING, &flow.flags);
	if (settled)
		set_bit(NF_FLOW_HW_DEAD, &flow.flags);
	else
		set_bit(IPS_HW_OFFLOAD_BIT, &ct.status);
	destroy_test_flow = &flow;
	tracked_nft_flowtable = nft;
	tracked_table_freed = false;
	hash_destroyed = hash_destroyed_with_entry = gc_removes = queued_deletes = 0;
	hash_removes = ct_puts = route_releases = 0;
	event_seq = last_queue_seq = last_flush_seq = 0;
	nf_tables_flowtable_destroy(nft);
	/* Every queued delete must be drained by a later flush. */
	assert(!queued_deletes || last_queue_seq < last_flush_seq);

	if (settled) {
		assert(tracked_table_freed && hash_destroyed == 1 &&
		       !hash_destroyed_with_entry && gc_removes == 1);
		puts("settled flowtable destruction: flow removed before table free");
		safe = true;
	} else {
		/*
		 * An unsettled flow is quarantined: unhashed, its conntrack
		 * reference dropped after RCU and the flow object itself leaked
		 * so its cookie can never be reused by a new flow.
		 */
		safe = tracked_table_freed && hash_destroyed == 1 &&
		       !hash_destroyed_with_entry && !gc_removes &&
		       hash_removes == 2 && ct_puts == 1;
		printf("unsettled flow at table free: table freed=%u hash "
		       "destroyed=%u unhashed=%u ct put=%u GC removed=%u "
		       "retained deletes=%u\n", tracked_table_freed,
		       hash_destroyed, hash_removes, ct_puts, gc_removes,
		       queued_deletes);
	}
	destroy_test_flow = NULL;
	tracked_nft_flowtable = NULL;
	if (!tracked_table_freed)
		free(nft);
	return safe;
}

int main(int argc, char **argv)
{
	bool characterize = argc == 2 && !strcmp(argv[1], "--characterize");
	unsigned int unsafe = 0, unsafe_add, unsafe_destroy;

	assert(argc == 1 || characterize);
	test_error_fanout_is_disambiguated();
	unsafe_add = test_add_fanout();
	assert(run_case(-1));
	if (!run_case(FLOW_OFFLOAD_DIR_ORIGINAL))
		unsafe++;
	if (!run_case(FLOW_OFFLOAD_DIR_REPLY))
		unsafe++;
	assert(run_table_destroy_case(true));
	unsafe_destroy = !run_table_destroy_case(false);
	printf("Flowtable add-status gate: %u uncontained check(s)\n",
	       unsafe_add);
	printf("Flowtable withdrawal gate: %u uncontained direction(s)\n", unsafe);
	printf("Flowtable destruction gate: %u uncontained scenario(s)\n",
	       unsafe_destroy);
	return !characterize && (unsafe || unsafe_add || unsafe_destroy) ? 1 : 0;
}
