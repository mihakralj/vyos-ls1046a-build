// SPDX-License-Identifier: GPL-2.0
/* Execute the production block lifetime helpers against kernel-style lists. */
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint64_t u64;
typedef struct { long counter; } atomic64_t;
#define ATOMIC64_INIT(v) { (v) }
static long atomic64_inc_return(atomic64_t *v) { return ++v->counter; }
#define DEFINE_XARRAY(name) __attribute__((unused)) int name
#define GFP_KERNEL 0
#define THIS_MODULE NULL
#define WRITE_ONCE(x, v) ((x) = (v))
#define ask_pr_dbg(...) ((void)0)
#define ask_pr_warn(...) ((void)0)
#define ask_pr_info(...) ((void)0)
#define ask_pr_err(...) ((void)0)
#define pr_info_ratelimited(...) ((void)0)
#define EXPORT_SYMBOL_GPL(x)
#define DEFINE_SPINLOCK(name) int name
#define ERR_PTR(err) ((void *)(intptr_t)(err))
#define PTR_ERR(ptr) ((int)(intptr_t)(ptr))
#define IS_ERR(ptr) ((uintptr_t)(ptr) >= (uintptr_t)-4095)
#define container_of(p, type, member) \
	((type *)((char *)(p) - offsetof(type, member)))
struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD(name) struct list_head name = { &name, &name }
#define list_entry(p, type, member) container_of(p, type, member)
#define list_for_each_entry(p, head, member) \
	for (p = list_entry((head)->next, typeof(*p), member); \
	     &p->member != (head); \
	     p = list_entry(p->member.next, typeof(*p), member))
#define list_for_each_entry_safe(p, n, head, member) \
	for (p = list_entry((head)->next, typeof(*p), member), \
	     n = list_entry(p->member.next, typeof(*p), member); \
	     &p->member != (head); \
	     p = n, n = list_entry(n->member.next, typeof(*n), member))
static void init_list(struct list_head *h) { h->next = h->prev = h; }
static bool list_empty(struct list_head *h) { return h->next == h; }
static void insert(struct list_head *n, struct list_head *prev,
		   struct list_head *next)
{
	n->prev = prev;
	n->next = next;
	prev->next = next->prev = n;
}
static void list_add(struct list_head *n, struct list_head *h)
{
	insert(n, h, h->next);
}
static void list_add_tail(struct list_head *n, struct list_head *h)
{
	insert(n, h->prev, h);
}
static void list_del(struct list_head *n)
{
	n->prev->next = n->next;
	n->next->prev = n->prev;
	n->next = n->prev = NULL;
}
static void list_move(struct list_head *n, struct list_head *h)
{
	list_del(n);
	list_add(n, h);
}
static void spin_lock(int *lock) { assert(!*lock); *lock = 1; }
static void spin_unlock(int *lock) { assert(*lock); *lock = 0; }

enum tc_setup_type { TC_SETUP_CLSFLOWER, TC_SETUP_BLOCK, TC_SETUP_FT };
enum { FLOW_BLOCK_BIND, FLOW_BLOCK_UNBIND };
enum { FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS = 1 };
struct net_device { int ifindex; };
struct Qdisc { int unused; };
struct flow_block { struct list_head cb_list; };
struct flow_block_cb {
	int (*cb)(enum tc_setup_type, void *, void *);
	void *cb_ident, *cb_priv;
	void (*release)(void *);
	struct list_head list, driver_list;
	struct {
		struct list_head list;
		void *data, *cb_priv;
		struct net_device *dev;
		struct Qdisc *sch;
		void (*cleanup)(struct flow_block_cb *);
	} indr;
};
struct flow_block_offload {
	int binder_type, command;
	struct flow_block *block;
	struct list_head cb_list, *driver_block_list;
};
static LIST_HEAD(indirect_blocks);
static int module_refs, priv_allocs, callback_allocs;
static int stats_zero[8], unbinds, cleanup_calls;
static bool module_live = true, fail_private_alloc, fail_callback_alloc;
static u8 ask_flow_first_pid = 0x10;
static void *kzalloc(size_t n, int flags)
{
	void *p;

	if (fail_private_alloc)
		return NULL;
	p = calloc(1, n);
	assert(p);
	priv_allocs++;
	return p;
}
static void kfree(void *p) { assert(p); priv_allocs--; free(p); }
static bool try_module_get(void *module)
{
	if (!module_live)
		return false;
	module_refs++;
	return true;
}
static void module_put(void *module) { assert(module_refs > 0); module_refs--; }
static struct flow_block_cb *flow_block_cb_alloc(
	int (*cb)(enum tc_setup_type, void *, void *), void *ident,
	void *priv, void (*release)(void *))
{
	struct flow_block_cb *block;

	if (fail_callback_alloc)
		return ERR_PTR(-ENOMEM);
	block = calloc(1, sizeof(*block));
	assert(block);
	callback_allocs++;
	block->cb = cb;
	block->cb_ident = ident;
	block->cb_priv = priv;
	block->release = release;
	return block;
}
static void flow_block_cb_free(struct flow_block_cb *cb)
{
	cb->release(cb->cb_priv);
	callback_allocs--;
	free(cb);
}
static struct flow_block_cb *flow_block_cb_lookup(
	struct flow_block *block, int (*cb)(enum tc_setup_type, void *, void *),
	void *ident)
{
	struct flow_block_cb *p;

	list_for_each_entry(p, &block->cb_list, list)
		if (p->cb == cb && p->cb_ident == ident)
			return p;
	return NULL;
}
static void *flow_block_cb_priv(struct flow_block_cb *cb) { return cb->cb_priv; }
static struct flow_block_cb *flow_indr_block_cb_alloc(
	int (*cb)(enum tc_setup_type, void *, void *), void *ident,
	void *priv, void (*release)(void *), struct flow_block_offload *bo,
	struct net_device *dev, struct Qdisc *sch, void *data, void *indr_priv,
	void (*cleanup)(struct flow_block_cb *))
{
	struct flow_block_cb *block = flow_block_cb_alloc(cb, ident, priv, release);

	if (IS_ERR(block))
		return block;
	block->indr.dev = dev;
	block->indr.sch = sch;
	block->indr.data = data;
	block->indr.cb_priv = indr_priv;
	block->indr.cleanup = cleanup;
	list_add(&block->indr.list, &indirect_blocks);
	return block;
}
static void flow_block_cb_add(struct flow_block_cb *cb,
			     struct flow_block_offload *bo)
{
	list_add_tail(&cb->list, &bo->cb_list);
}
static void flow_block_cb_remove(struct flow_block_cb *cb,
				struct flow_block_offload *bo)
{
	list_move(&cb->list, &bo->cb_list);
}
static void flow_indr_block_cb_remove(struct flow_block_cb *cb,
				     struct flow_block_offload *bo)
{
	list_del(&cb->indr.list);
	flow_block_cb_remove(cb, bo);
}
static struct net_device *ask_flow_block_priv_dev(void *priv);
static int ask_flow_offload_setup_tc_block_cb(enum tc_setup_type type,
					     void *data, void *priv)
{
	return ask_flow_block_priv_dev(priv) ? 0 : -ENODEV;
}
static int ask_dpaa_get_fman_port_id(struct net_device *dev, u8 *pid)
{
	*pid = dev->ifindex;
	return 0;
}
static int withdraws, withdraws_at_port_unbind;
static int ask_hw_port_unbind(u8 pid)
{
	unbinds++;
	withdraws_at_port_unbind = withdraws;
	return 0;
}
struct ask_flow_block_priv;
static void ask_flow_block_withdraw(struct ask_flow_block_priv *p);
static void ask_port_stats_zero(int ifindex) { stats_zero[ifindex]++; }
static const int ask_flow_offload_ops;
static void dpaa_unregister_flow_offload_handler(const void *ops) {}
typedef int (*indr_cb_t)(struct net_device *, struct Qdisc *, void *,
			enum tc_setup_type, void *, void *,
			void (*)(struct flow_block_cb *));
static bool fail_indirect_register, fail_backend_register;
static struct flow_block *existing_block;
static struct net_device *existing_dev;
static void bind_existing(void);
static int flow_indr_dev_register(indr_cb_t cb, void *priv)
{
	if (fail_indirect_register)
		return -EIO;
	if (existing_block)
		bind_existing();
	return 0;
}
static int dpaa_register_flow_offload_handler(const void *ops)
{
	return fail_backend_register ? -EIO : 0;
}
static void flow_indr_dev_unregister(indr_cb_t cb, void *priv,
				     void (*release)(void *))
{
	struct flow_block_cb *p, *next;

	/* Match the core's release-function and provider-private identity. */
	list_for_each_entry_safe(p, next, &indirect_blocks, indr.list) {
		if (p->release != release || p->indr.cb_priv != priv)
			continue;
		list_del(&p->indr.list);
		p->indr.cleanup(p);
	}
}

#include "ask_flow_block_body.h"

/* UNBIND must withdraw this block's flows after REPLACE is gated off. */
static void ask_flow_block_withdraw(struct ask_flow_block_priv *p)
{
	assert(p->dev && p->id);
	assert(!ask_flow_block_priv_bound(p));
	withdraws++;
}

static void table_cleanup(struct flow_block_cb *cb)
{
	assert(cb->indr.data);
	assert(cb->indr.dev);
	assert(cb->indr.sch);
	cleanup_calls++;
	list_del(&cb->list);
	list_del(&cb->driver_list);
	flow_block_cb_free(cb);
}
static void apply_callbacks(struct flow_block_offload *bo)
{
	struct flow_block_cb *cb, *next;

	list_for_each_entry_safe(cb, next, &bo->cb_list, list) {
		list_del(&cb->list);
		if (bo->command == FLOW_BLOCK_BIND) {
			list_add_tail(&cb->list, &bo->block->cb_list);
			assert(!cb->cb(TC_SETUP_CLSFLOWER, NULL, cb->cb_priv));
		} else {
			flow_block_cb_free(cb);
		}
	}
}
static int setup(struct flow_block *block, struct net_device *dev,
		 bool indirect, int command)
{
	struct flow_block_offload bo = {
		.binder_type = FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS,
		.command = command, .block = block,
	};
	static struct Qdisc sch;
	int rc;

	init_list(&bo.cb_list);
	if (indirect)
		rc = ask_flow_indr_setup_cb(dev, &sch, NULL, TC_SETUP_FT, &bo,
					    block, table_cleanup);
	else
		rc = ask_flow_offload_setup_tc(dev, &bo);
	if (!rc)
		apply_callbacks(&bo);
	else
		assert(list_empty(&bo.cb_list));
	return rc;
}
static void init_block(struct flow_block *block) { init_list(&block->cb_list); }
static void bind_existing(void)
{
	assert(!setup(existing_block, existing_dev, true, FLOW_BLOCK_BIND));
}
static void assert_empty(void)
{
	assert(!module_refs && !priv_allocs && !callback_allocs);
	assert(list_empty(&ask_flow_block_priv_list));
	assert(list_empty(&ask_flow_block_cb_list));
	assert(list_empty(&indirect_blocks));
}

#define __init
#define __exit
enum {
	HW, STATS, FLOW, NEIGH, BRIDGE, OP, CAAM, XFRM, OFFLOAD, DEBUGFS, GENL,
	REGISTER,
};
static int fail_init = -1, init_calls[12], exit_calls[12];
static bool hw_ready;
#define SUBSYSTEM(name, slot) \
	static int name##_init(void) { \
		init_calls[slot]++; return fail_init == slot ? -EIO : 0; \
	} \
	static void name##_exit(void) { exit_calls[slot]++; }
SUBSYSTEM(ask_hw, HW)
SUBSYSTEM(ask_stats, STATS)
SUBSYSTEM(ask_flow, FLOW)
SUBSYSTEM(ask_neigh, NEIGH)
SUBSYSTEM(ask_bridge, BRIDGE)
SUBSYSTEM(ask_op, OP)
SUBSYSTEM(ask_caam, CAAM)
SUBSYSTEM(ask_xfrm, XFRM)
SUBSYSTEM(ask_debugfs, DEBUGFS)
SUBSYSTEM(ask_flow_offload, OFFLOAD)
static int ask_genl_register(void)
{
	init_calls[GENL]++;
	return fail_init == GENL ? -EIO : 0;
}
static void ask_genl_unregister(void) { exit_calls[GENL]++; }
static int ask_flow_offload_register(void)
{
	init_calls[REGISTER]++;
	return fail_init == REGISTER ? -EIO : 0;
}
static void ask_hw_set_ready(bool ready) { hw_ready = ready; }
#include "ask_main_body.h"

int main(int argc, char **argv)
{
	struct net_device a = { .ifindex = 1 }, b = { .ifindex = 2 };
	struct flow_block first, second;

	assert(argc == 2);
	init_block(&first);
	init_block(&second);
	if (strcmp(argv[1], "registration-failure"))
		assert(!actual_provider_register());
	if (!strcmp(argv[1], "native-identity")) {
		struct ask_flow_block_priv *pa, *pb;

		assert(!setup(&first, &a, false, FLOW_BLOCK_BIND));
		assert(!setup(&first, &b, false, FLOW_BLOCK_BIND));
		assert(module_refs == 2);
		pa = flow_block_cb_priv(flow_block_cb_lookup(&first,
			ask_flow_offload_setup_tc_block_cb, &a));
		pb = flow_block_cb_priv(flow_block_cb_lookup(&first,
			ask_flow_offload_setup_tc_block_cb, &b));
		/* Owner ids are distinct, nonzero and never the orphan id. */
		assert(ask_flow_block_priv_bound(pa) && ask_flow_block_priv_bound(pb));
		assert(pa->id && pb->id && pa->id != pb->id);
		assert(!ask_flow_block_priv_bound(NULL));
		assert(!setup(&first, &a, false, FLOW_BLOCK_UNBIND));
		assert(withdraws == 1);
		assert(flow_block_cb_lookup(&first,
			ask_flow_offload_setup_tc_block_cb, &b));
		assert(module_refs == 1 && ask_flow_first_pid == 0x10);
		assert(setup(&first, &a, false, FLOW_BLOCK_UNBIND) == -ENOENT);
		assert(!setup(&first, &b, false, FLOW_BLOCK_UNBIND));
		assert(ask_flow_first_pid == 0xff);
		assert(withdraws == 2);
	} else if (!strcmp(argv[1], "shared-device")) {
		assert(!setup(&first, &a, false, FLOW_BLOCK_BIND));
		assert(!setup(&second, &a, false, FLOW_BLOCK_BIND));
		assert(!setup(&first, &a, false, FLOW_BLOCK_UNBIND));
		assert(!stats_zero[a.ifindex] && !unbinds);
		assert(ask_flow_first_pid == 0x10);
		assert(!setup(&second, &a, false, FLOW_BLOCK_UNBIND));
		assert(stats_zero[a.ifindex] == 1 && unbinds == 1);
		/* Both blocks withdrew before the last one released the port. */
		assert(withdraws == 2 && withdraws_at_port_unbind == 2);
		assert(ask_flow_first_pid == 0xff);
	} else if (!strcmp(argv[1], "duplicate-bind")) {
		assert(!setup(&first, &a, false, FLOW_BLOCK_BIND));
		assert(setup(&first, &a, false, FLOW_BLOCK_BIND) == -EEXIST);
		assert(setup(&first, &a, true, FLOW_BLOCK_BIND) == -EEXIST);
		assert(module_refs == 1 && callback_allocs == 1 && priv_allocs == 1);
		assert(!setup(&first, &a, false, FLOW_BLOCK_UNBIND));
	} else if (!strcmp(argv[1], "allocation-failure")) {
		fail_private_alloc = true;
		assert(setup(&first, &a, false, FLOW_BLOCK_BIND) == -ENOMEM);
		fail_private_alloc = false;
		fail_callback_alloc = true;
		assert(setup(&first, &a, false, FLOW_BLOCK_BIND) == -ENOMEM);
		assert_empty();
		assert(setup(&first, &a, true, FLOW_BLOCK_BIND) == -ENOMEM);
		fail_callback_alloc = false;
		module_live = false;
		assert(setup(&first, &a, false, FLOW_BLOCK_BIND) == -ENODEV);
	} else if (!strcmp(argv[1], "indirect-unbind")) {
		assert(!setup(&first, &a, true, FLOW_BLOCK_BIND));
		assert(!module_refs && !list_empty(&indirect_blocks));
		assert(!setup(&first, &a, true, FLOW_BLOCK_UNBIND));
		assert(withdraws == 1);
		unregister_provider();
		assert(!cleanup_calls);
	} else if (!strcmp(argv[1], "provider-cleanup")) {
		assert(!setup(&first, &a, true, FLOW_BLOCK_BIND));
		assert(!setup(&second, &b, true, FLOW_BLOCK_BIND));
		unregister_provider();
		assert(cleanup_calls == 2);
	} else if (!strcmp(argv[1], "registration-failure")) {
		fail_indirect_register = true;
		assert(actual_provider_register() == -EIO);
		assert(!ask_flow_offload_registered);
		assert_empty();
		fail_indirect_register = false;
		existing_block = &first;
		existing_dev = &a;
		fail_backend_register = true;
		assert(actual_provider_register() == -EIO);
		assert(!ask_flow_offload_registered && cleanup_calls == 1);
		assert(list_empty(&first.cb_list));
		assert_empty();
		fail_backend_register = false;
		assert(!actual_provider_register());
		unregister_provider();
		assert(cleanup_calls == 2 && list_empty(&first.cb_list));
		assert(list_empty(&first.cb_list) && list_empty(&second.cb_list));
		unregister_provider();
		assert(cleanup_calls == 2);
	} else if (!strcmp(argv[1], "init-unwind")) {
		for (int failure = 0; failure <= REGISTER; failure++) {
			memset(init_calls, 0, sizeof(init_calls));
			memset(exit_calls, 0, sizeof(exit_calls));
			fail_init = failure;
			assert(ask_init() == -EIO && !hw_ready);
			/* No callback registration before all fallible setup passes. */
			assert(init_calls[REGISTER] == (failure == REGISTER));
			for (int i = 0; i < failure; i++)
				assert(exit_calls[i] == 1);
			assert(!exit_calls[failure]);
		}
		fail_init = -1;
		assert(!ask_init() && hw_ready);
		ask_exit();
		assert(!hw_ready);
	} else {
		abort();
	}
	unregister_provider();
	assert_empty();
	puts(argv[1]);
	return 0;
}
