/* Host mocks for ASK generation and flush functions extracted from source. */
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef struct { int value; } atomic_t;
static void atomic_dec(atomic_t *v) { v->value--; }
#define EXPORT_SYMBOL_GPL(name)
#define GFP_ATOMIC 0
#define ask_pr_warn(...) do { } while (0)
#define ask_pr_dbg(...) do { } while (0)
#define pr_info_ratelimited(...) do { } while (0)
#define rcu_read_lock() do { } while (0)
#define rcu_read_unlock() do { } while (0)
#define container_of(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
#define ASK_GEN_LIVE 0
#define ASK_GEN_TOMBSTONED 1

struct xarray_entry { unsigned long index; void *value; bool used; };
struct xarray {
	pthread_mutex_t lock;
	struct xarray_entry slots[32];
};
#define xa_lock_irqsave(xa, flags) do {                                    \
	(flags) = 0;                                                       \
	assert(!pthread_mutex_lock(&(xa)->lock));                          \
} while (0)
#define xa_unlock_irqrestore(xa, flags) do {                               \
	(void)(flags);                                                      \
	assert(!pthread_mutex_unlock(&(xa)->lock));                        \
} while (0)
static int store_fail;
static void *xa_mk_value(unsigned long v) { return (void *)((v << 1) | 1); }
static bool xa_is_value(const void *p) { return ((uintptr_t)p & 1) != 0; }
static unsigned long xa_to_value(const void *p) { return (uintptr_t)p >> 1; }
static int xa_err(const void *p) { return (intptr_t)p < 0 ? (int)(intptr_t)p : 0; }
static void *xa_load(struct xarray *xa, unsigned long index)
{
	unsigned int i;

	for (i = 0; i < 32; i++)
		if (xa->slots[i].used && xa->slots[i].index == index)
			return xa->slots[i].value;
	return NULL;
}
static void *__xa_store(struct xarray *xa, unsigned long index, void *value,
			int flags)
{
	unsigned int i, free_slot = 32;

	if (store_fail)
		return (void *)(intptr_t)-ENOMEM;
	for (i = 0; i < 32; i++) {
		if (xa->slots[i].used && xa->slots[i].index == index) {
			xa->slots[i].value = value;
			return NULL;
		}
		if (!xa->slots[i].used && free_slot == 32)
			free_slot = i;
	}
	assert(free_slot < 32);
	xa->slots[free_slot] = (struct xarray_entry){ index, value, true };
	return NULL;
}
static void __xa_erase(struct xarray *xa, unsigned long index)
{
	unsigned int i;

	for (i = 0; i < 32; i++)
		if (xa->slots[i].used && xa->slots[i].index == index)
			xa->slots[i].used = false;
}

static pthread_mutex_t token_lock = PTHREAD_MUTEX_INITIALIZER;
static u64 token_seq;
static u64 fman_pcd_fe_alloc_token(void)
{
	u64 token;

	assert(!pthread_mutex_lock(&token_lock));
	token = ++token_seq;
	assert(!pthread_mutex_unlock(&token_lock));
	return token;
}

struct rhash_head { int unused; };
struct rcu_head { int unused; };
struct ask_flow {
	struct rhash_head node;
	struct rcu_head rcu;
	u64 cookie;
	u64 fe_token;
	u64 generation;
	u32 hw_flow_id;
	bool hw_backed;
};
struct rhashtable { struct ask_flow *slots[32]; };
struct ask_flow_table {
	struct xarray gen_by_cookie;
	struct rhashtable rht;
	atomic_t num_flows;
	atomic_t num_hw_backed;
	const char *tag;
};
struct rhashtable_iter { struct rhashtable *table; unsigned int next; };
static const int ask_flow_rht_params;
#define IS_ERR(p) false
#define PTR_ERR(p) (-EINVAL)

static void *rhashtable_lookup_fast(struct rhashtable *table, const u64 *cookie,
				    int params)
{
	unsigned int i;

	for (i = 0; i < 32; i++)
		if (table->slots[i] && table->slots[i]->cookie == *cookie)
			return table->slots[i];
	return NULL;
}
static int removed_hw, remove_calls;
static int rhashtable_remove_fast(struct rhashtable *table,
				  struct rhash_head *node, int params)
{
	struct ask_flow *f = container_of(node, struct ask_flow, node);
	unsigned int i;

	for (i = 0; i < 32; i++) {
		if (table->slots[i] == f) {
			table->slots[i] = NULL;
			remove_calls++;
			return 0;
		}
	}
	return -ENOENT;
}
static int ask_hw_flow_remove(u32 id) { removed_hw++; return 0; }
static void ask_flow_free_rcu(struct rcu_head *head) { }
static void call_rcu(struct rcu_head *head, void (*fn)(struct rcu_head *))
{
	fn(head);
}
static void rhashtable_walk_enter(struct rhashtable *table, struct rhashtable_iter *iter)
{
	iter->table = table;
	iter->next = 0;
}
static void rhashtable_walk_start(struct rhashtable_iter *iter) { }
static struct ask_flow *rhashtable_walk_next(struct rhashtable_iter *iter)
{
	while (iter->next < 32) {
		struct ask_flow *f = iter->table->slots[iter->next++];

		if (f)
			return f;
	}
	return NULL;
}
static void rhashtable_walk_stop(struct rhashtable_iter *iter) { }
static void rhashtable_walk_exit(struct rhashtable_iter *iter) { }

#include "ask_owner_generation_body.h"

static void init_table(struct ask_flow_table *t)
{
	memset(t, 0, sizeof(*t));
	assert(!pthread_mutex_init(&t->gen_by_cookie.lock, NULL));
	t->tag = "host";
}

struct release_race {
	struct ask_flow_table *table;
	pthread_mutex_t lock;
	pthread_cond_t cond;
	u64 old, current;
	bool tombstoned, reclaimed;
};
static void *release_old(void *arg)
{
	struct release_race *r = arg;

	r->old = ask_flow_gen_next(r->table, 0x11);
	assert(ask_flow_gen_tombstone(r->table, 0x11) == r->old);
	assert(!pthread_mutex_lock(&r->lock));
	r->tombstoned = true;
	assert(!pthread_cond_broadcast(&r->cond));
	while (!r->reclaimed)
		assert(!pthread_cond_wait(&r->cond, &r->lock));
	assert(!pthread_mutex_unlock(&r->lock));
	ask_flow_gen_release(r->table, 0x11, r->old);
	return NULL;
}
static void *reclaim_cookie(void *arg)
{
	struct release_race *r = arg;

	assert(!pthread_mutex_lock(&r->lock));
	while (!r->tombstoned)
		assert(!pthread_cond_wait(&r->cond, &r->lock));
	r->current = ask_flow_gen_next(r->table, 0x11);
	r->reclaimed = true;
	assert(!pthread_cond_broadcast(&r->cond));
	assert(!pthread_mutex_unlock(&r->lock));
	return NULL;
}

static void test_generation(void)
{
	struct ask_flow_table t;
	struct release_race r = {
		.table = &t,
		.lock = PTHREAD_MUTEX_INITIALIZER,
		.cond = PTHREAD_COND_INITIALIZER,
	};
	pthread_t first, second;
	u64 old, next;

	init_table(&t);
	assert(!pthread_create(&first, NULL, release_old, &r));
	assert(!pthread_create(&second, NULL, reclaim_cookie, &r));
	assert(!pthread_join(first, NULL));
	assert(!pthread_join(second, NULL));
	assert(r.current > r.old);
	assert(ask_flow_gen_is_current(&t, 0x11, r.current));
	assert(ask_flow_gen_tombstone(&t, 0x11) == r.current);
	ask_flow_gen_release(&t, 0x11, r.old);
	assert(ask_flow_gen_current(&t, 0x11) == r.current);
	ask_flow_gen_release(&t, 0x11, r.current);
	assert(!ask_flow_gen_current(&t, 0x11));

	next = ask_flow_gen_next(&t, 0x11);
	assert(next > r.current);
	old = next;
	assert(!ask_flow_gen_tombstone_if_current(&t, 0x11, old + 1));
	assert(ask_flow_gen_is_current(&t, 0x11, old));
	assert(ask_flow_gen_tombstone_if_current(&t, 0x11, old));
	assert(!ask_flow_gen_is_current(&t, 0x11, old));
	assert(!ask_flow_gen_tombstone_if_current(&t, 0x11, old));
	ask_flow_gen_release(&t, 0x11, old);
	old = ask_flow_gen_next(&t, 0x11);
	assert(old > next);
	store_fail = 1;
	assert(!ask_flow_gen_next(&t, 0x11));
	assert(ask_flow_gen_is_current(&t, 0x11, old));
	assert(!ask_flow_gen_tombstone(&t, 0x11));
	assert(ask_flow_gen_is_current(&t, 0x11, old));
	store_fail = 0;
	assert(ask_flow_gen_tombstone(&t, 0x11) == old);
	ask_flow_gen_release(&t, 0x11, old);
	next = ask_flow_gen_next(&t, 0x11);
	assert(next > old);
	assert(!ask_flow_gen_is_current(&t, 0x11, old));

	token_seq = ULONG_MAX >> 2;
	assert(!ask_flow_gen_next(&t, 0x11));
	assert(ask_flow_gen_is_current(&t, 0x11, next));
	assert(!pthread_mutex_destroy(&t.gen_by_cookie.lock));
}

static void test_flush(void)
{
	struct ask_flow_table t;
	struct ask_flow legacy = {
		.cookie = 1, .generation = 10, .hw_flow_id = 1, .hw_backed = true,
	};
	struct ask_flow identified = {
		.cookie = 2, .generation = 20, .fe_token = 200,
		.hw_flow_id = 2, .hw_backed = true,
	};
	struct ask_flow unknown = { .cookie = 3 };

	init_table(&t);
	t.rht.slots[0] = &legacy;
	t.rht.slots[1] = &identified;
	t.num_flows.value = 2;
	t.num_hw_backed.value = 2;
	assert(ask_flow_flush(&t) == -EBUSY);
	assert(!t.rht.slots[0] && t.rht.slots[1] == &identified);
	assert(t.num_flows.value == 1 && removed_hw == 1 && remove_calls == 1);
	assert(ask_flow_remove(&t, identified.cookie) == -EBUSY);
	assert(ask_flow_remove_owned_identified(&t, identified.cookie,
						 20, 100) == -ESTALE);
	assert(t.rht.slots[1] == &identified && removed_hw == 1);
	assert(!ask_flow_remove_owned_identified(&t, identified.cookie, 20, 200));
	assert(!ask_flow_flush(&t));
	assert(!t.num_flows.value && removed_hw == 2);

	t.rht.slots[0] = &unknown;
	t.num_flows.value = 1;
	assert(ask_flow_flush(&t) == -ESTALE);
	assert(t.rht.slots[0] == &unknown);
	assert(!ask_flow_remove(&t, unknown.cookie));
	assert(!pthread_mutex_destroy(&t.gen_by_cookie.lock));
}

int main(void)
{
	test_generation();
	test_flush();
	puts("OK: ASK generation ABA, conditional release and flush containment");
	return 0;
}
