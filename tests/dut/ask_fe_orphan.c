/* Host mocks for ask.ko's retained FE-delete owner (F-269 caller side). */
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef struct { int counter; } atomic_t;
#define ATOMIC_INIT(v) { (v) }
static void atomic_inc(atomic_t *a) { a->counter++; }
static int atomic_read(const atomic_t *a) { return a->counter; }

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
#define list_for_each_entry(pos, head, m)                                  \
	for (pos = list_entry((head)->next, typeof(*pos), m);               \
	     &pos->m != (head);                                             \
	     pos = list_entry(pos->m.next, typeof(*pos), m))

struct mutex { pthread_mutex_t native; bool held; pthread_t owner; };
#define DEFINE_MUTEX(n) struct mutex n = { .native = PTHREAD_MUTEX_INITIALIZER }
#define lockdep_assert_held(m) \
	assert((m)->held && pthread_equal((m)->owner, pthread_self()))
static void mutex_lock(struct mutex *m)
{
	assert(pthread_mutex_lock(&m->native) == 0);
	m->held = true;
	m->owner = pthread_self();
}
static void mutex_unlock(struct mutex *m)
{
	lockdep_assert_held(m);
	m->held = false;
	assert(pthread_mutex_unlock(&m->native) == 0);
}
static struct mutex *owner_guard;
static pthread_mutex_t event_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t event_cond = PTHREAD_COND_INITIALIZER;
static bool pause_delete, pause_add, operation_waiting, operation_release;
static bool other_started;

static void pause_operation(void)
{
	assert(pthread_mutex_lock(&event_lock) == 0);
	operation_waiting = true;
	assert(pthread_cond_broadcast(&event_cond) == 0);
	while (!operation_release)
		assert(pthread_cond_wait(&event_cond, &event_lock) == 0);
	assert(pthread_mutex_unlock(&event_lock) == 0);
}

#define pr_info_ratelimited(...) do { } while (0)
#define pr_warn_ratelimited(...) do { warning_count++; } while (0)
#define pr_err_ratelimited(...) do { } while (0)
#define pr_err(...) do { } while (0)
#define pr_info(...) do { } while (0)
#define ask_pr_info(...) printf(__VA_ARGS__)
#define GFP_KERNEL 0
#define GFP_ATOMIC 0
#define rcu_read_lock() do { } while (0)
#define rcu_read_unlock() do { } while (0)

static int alloc_fail, live_allocs;
static unsigned int warning_count;
static bool allocator_expect_lock = true;
static void *kzalloc(size_t n, int gfp)
{
	void *p;

	if (allocator_expect_lock) {
		assert(owner_guard);
		lockdep_assert_held(owner_guard);
	}
	if (alloc_fail)
		return NULL;
	p = calloc(1, n);
	live_allocs++;
	return p;
}
static void kfree(void *p)
{
	if (p)
		live_allocs--;
	free(p);
}

#define ASK_FE_KEY_SIZE_DUAL 50
struct fman { int unused; };
struct fman_pcd_fe_flow_action {
	u8 key[ASK_FE_KEY_SIZE_DUAL];
	u64 owner_token;
};
static struct fman the_fman;
static struct fman *fman_ptr = &the_fman;
static struct fman *ask_hw_get_fman(void) { return fman_ptr; }

struct ask_flow_key { u8 port_id; u8 id; };
static void ask_fe_build_key_dual(const struct ask_flow_key *key,
				  u8 k[ASK_FE_KEY_SIZE_DUAL])
{
	memset(k, 0, ASK_FE_KEY_SIZE_DUAL);
	k[0] = key->id;
}

/* Per-key (k[0]) delete outcome and call count. */
static int del_rc[256], del_calls[256], add_calls;
static bool replacement_present[256];
static u64 replacement_token[256];
static int fman_pcd_fe_flow_del_identified(struct fman *fm, u8 port, u8 *k,
					   u8 len, u64 token)
{
	assert(fm == &the_fman);
	assert(len == ASK_FE_KEY_SIZE_DUAL);
	assert(owner_guard);
	lockdep_assert_held(owner_guard);
	del_calls[k[0]]++;
	if (pause_delete && k[0] == 3)
		pause_operation();
	if (!token)
		return -EINVAL;
	if (del_rc[k[0]])
		return del_rc[k[0]];
	if (replacement_present[k[0]] && replacement_token[k[0]] != token)
		return -ESTALE;
	replacement_present[k[0]] = false;
	return 0;
}

static int fman_pcd_fe_flow_add(struct fman *fm, u8 port,
				const struct fman_pcd_fe_flow_action *action)
{
	assert(fm == &the_fman);
	assert(owner_guard);
	lockdep_assert_held(owner_guard);
	add_calls++;
	if (pause_add && action->key[0] == 4)
		pause_operation();
	replacement_present[action->key[0]] = true;
	replacement_token[action->key[0]] = action->owner_token;
	return 0;
}
static int ask_fe_flow_remove(const struct ask_flow_key *key, u64 fe_token)
{
	u8 k[ASK_FE_KEY_SIZE_DUAL];

	ask_fe_build_key_dual(key, k);
	return fman_pcd_fe_flow_del_identified(ask_hw_get_fman(), key->port_id, k,
					       ASK_FE_KEY_SIZE_DUAL, fe_token);
}

#include "ask_fe_orphan_body.h"

struct ask_flow {
	struct ask_flow_key key;
	u64 cookie;
	u64 fe_token;
	u64 generation;
	bool hw_backed;
};
struct ask_flow_table { int unused; };
static struct ask_flow_table flush_table;
static struct ask_flow flush_flow;
static bool flush_owner_present;
static u64 current_generation;
static bool generation_tombstoned;
static unsigned int pending_drops, generation_releases;
static int delete_calls_at_removal, sw_removals;
static DEFINE_MUTEX(ask_flow_lifecycle_lock);

static bool ask_flow_gen_is_current(struct ask_flow_table *t, u64 cookie,
				    u64 generation)
{
	assert(t == &flush_table);
	return cookie == flush_flow.cookie && generation &&
	       generation == current_generation && !generation_tombstoned;
}
static bool ask_flow_gen_tombstone_if_current(struct ask_flow_table *t,
					       u64 cookie, u64 generation)
{
	assert(t == &flush_table);
	if (cookie != flush_flow.cookie || !generation ||
	    generation != current_generation)
		return false;
	generation_tombstoned = true;
	return true;
}
static void ask_flow_gen_release(struct ask_flow_table *t, u64 cookie,
				 u64 generation)
{
	assert(t == &flush_table && cookie == flush_flow.cookie);
	assert(generation == current_generation && generation_tombstoned);
	generation_releases++;
	current_generation = 0;
	generation_tombstoned = false;
}
static bool ask_flow_pending_drop_cookie(u64 cookie, u64 generation)
{
	assert(cookie == flush_flow.cookie && generation == current_generation);
	pending_drops++;
	return true;
}
static struct ask_flow *ask_flow_lookup(struct ask_flow_table *t, u64 cookie)
{
	assert(t == &flush_table);
	return flush_owner_present && cookie == flush_flow.cookie ?
	       &flush_flow : NULL;
}

static int ask_flow_remove_owned_identified(struct ask_flow_table *t, u64 cookie,
					    u64 gen, u64 fe_token);
#include "ask_fe_owner_status.h"

static struct ask_flow_table *ask_flow_default_table(void)
{
	return &flush_table;
}
static int ask_flow_walk(struct ask_flow_table *t,
			 int (*collect)(struct ask_flow *, void *), void *arg)
{
	assert(t == &flush_table);
	return collect(&flush_flow, arg);
}
static int ask_flow_remove_owned_identified(struct ask_flow_table *t, u64 cookie,
					    u64 gen, u64 fe_token)
{
	assert(t == &flush_table && cookie == flush_flow.cookie);
	assert(gen == flush_flow.generation && flush_owner_present);
	assert(fe_token == flush_flow.fe_token);
	lockdep_assert_held(&ask_flow_lifecycle_lock);
	delete_calls_at_removal = del_calls[flush_flow.key.id];
	sw_removals++;
	flush_owner_present = false;
	return 0;
}

#include "ask_fe_flush_body.h"

static int collect_flush_flow(struct ask_flow *f, void *arg)
{
	struct ask_flush_victim *v = calloc(1, sizeof(*v));

	assert(v);
	live_allocs++;
	v->cookie = f->cookie;
	v->fe_token = f->fe_token;
	v->generation = f->generation;
	v->hw_backed = f->hw_backed;
	v->key = f->key;
	list_add_tail(&v->node, arg);
	return 0;
}

static void key_bytes(u8 id, u8 k[ASK_FE_KEY_SIZE_DUAL])
{
	struct ask_flow_key key = { .port_id = 0x10, .id = id };

	ask_fe_build_key_dual(&key, k);
}

struct concurrent_case {
	struct ask_flow_key key;
	u8 bytes[ASK_FE_KEY_SIZE_DUAL];
	struct fman_pcd_fe_flow_action action;
	int remove_rc, publish_rc;
};

static void *remove_thread(void *arg)
{
	struct concurrent_case *c = arg;

	if (pause_add) {
		assert(pthread_mutex_lock(&event_lock) == 0);
		other_started = true;
		assert(pthread_cond_broadcast(&event_cond) == 0);
		assert(pthread_mutex_unlock(&event_lock) == 0);
	}
	c->remove_rc = ask_fe_flow_remove_retain(&c->key, c->key.id,
						 100 + c->key.id, "race");
	return NULL;
}

static void *publish_thread(void *arg)
{
	struct concurrent_case *c = arg;

	if (pause_delete) {
		assert(pthread_mutex_lock(&event_lock) == 0);
		other_started = true;
		assert(pthread_cond_broadcast(&event_cond) == 0);
		assert(pthread_mutex_unlock(&event_lock) == 0);
	}
	c->publish_rc = ask_fe_flow_publish(&the_fman, c->key.port_id,
					    c->bytes, &c->action);
	return NULL;
}

static void check_interleaving(u8 id, bool delete_first)
{
	struct concurrent_case c = { .key = { .port_id = 0x10, .id = id } };
	pthread_t first, second;
	int before = add_calls;

	key_bytes(id, c.bytes);
	memcpy(c.action.key, c.bytes, sizeof(c.bytes));
	c.action.owner_token = 200 + id;
	del_rc[id] = -ETIMEDOUT;
	pause_delete = delete_first;
	pause_add = !delete_first;
	operation_waiting = operation_release = other_started = false;
	assert(pthread_create(&first, NULL,
			      delete_first ? remove_thread : publish_thread, &c) == 0);
	assert(pthread_mutex_lock(&event_lock) == 0);
	while (!operation_waiting)
		assert(pthread_cond_wait(&event_cond, &event_lock) == 0);
	assert(pthread_mutex_unlock(&event_lock) == 0);
	assert(pthread_create(&second, NULL,
			      delete_first ? publish_thread : remove_thread, &c) == 0);
	assert(pthread_mutex_lock(&event_lock) == 0);
	while (!other_started)
		assert(pthread_cond_wait(&event_cond, &event_lock) == 0);
	assert(pthread_mutex_trylock(&ask_fe_orphan_lock.native) == EBUSY);
	operation_release = true;
	assert(pthread_cond_broadcast(&event_cond) == 0);
	assert(pthread_mutex_unlock(&event_lock) == 0);
	assert(pthread_join(first, NULL) == 0);
	assert(pthread_join(second, NULL) == 0);
	assert(c.remove_rc == -EAGAIN);
	assert(c.publish_rc == (delete_first ? -EBUSY : 0));
	assert(add_calls == before + !delete_first);
	pause_delete = pause_add = false;
}

int main(void)
{
	struct ask_flow_key a = { .port_id = 0x10, .id = 1 };
	struct ask_flow_key b = { .port_id = 0x10, .id = 2 };
	struct ask_flow_key c = { .port_id = 0x20, .id = 5 };
	u8 ka[ASK_FE_KEY_SIZE_DUAL], kb[ASK_FE_KEY_SIZE_DUAL];
	struct fman_pcd_fe_flow_action action = { .owner_token = 42 };
	int calls;
	unsigned int warnings;

	owner_guard = &ask_fe_orphan_lock;
	key_bytes(1, ka);
	key_bytes(2, kb);

	assert(ask_fe_flow_remove_retain(NULL, 0, 1, "t") == -EINVAL);

	/* Settled deletes keep no owner. */
	assert(ask_fe_flow_remove_retain(&a, 1, 1, "t") == 0);
	del_rc[1] = -ENOENT;
	assert(ask_fe_flow_remove_retain(&a, 1, 1, "t") == 0);
	assert(ask_fe_orphan_count == 0);

	/* A failed delete retains the key; do not retry an uncertain unlink. */
	del_rc[1] = -ETIMEDOUT;
	assert(ask_fe_flow_remove_retain(&a, 1, 1, "t") == -EAGAIN);
	assert(ask_fe_orphan_count == 1);

	/* A same-owner caller can publish a replacement after recovery. */
	replacement_present[1] = true;
	replacement_token[1] = 2;
	del_rc[1] = 0;
	calls = del_calls[1];
	memcpy(action.key, ka, sizeof(ka));
	assert(ask_fe_flow_publish(&the_fman, 0x10, ka, &action) == -EBUSY);
	assert(ask_fe_flow_remove_retain(&a, 1, 1, "t") == -EAGAIN);
	assert(del_calls[1] == calls && replacement_present[1]);
	assert(ask_fe_orphan_count == 1 && add_calls == 0);

	/* A different key or port remains usable. */
	memcpy(action.key, kb, sizeof(kb));
	assert(ask_fe_flow_publish(&the_fman, 0x10, kb, &action) == 0);
	memcpy(action.key, ka, sizeof(ka));
	assert(ask_fe_flow_publish(&the_fman, 0x11, ka, &action) == 0);
	assert(del_calls[1] == calls && add_calls == 2);

	/* A settled delete needs no allocation even under memory pressure. */
	alloc_fail = 1;
	c.id = 7;
	assert(ask_fe_flow_remove_retain(&c, 7, 7, "t") == 0);
	assert(!ask_fe_emergency[0x20].emergency);
	alloc_fail = 0;
	c.id = 5;

	/* A legacy owner without a token must not disappear silently. */
	flush_flow.key = c;
	flush_flow.cookie = 5;
	flush_flow.fe_token = 0;
	flush_flow.generation = 1;
	flush_flow.hw_backed = true;
	flush_owner_present = true;
	current_generation = 1;
	calls = del_calls[5];
	assert(ask_flow_fe_owner_status(&flush_table, 5, 1, 0) == -ESTALE);
	flush_flow.fe_token = 50;
	assert(ask_flow_fe_owner_status(&flush_table, 5, 1, 50) == 0);
	assert(ask_flow_fe_owner_status(&flush_table, 5, 1, 51) == -ESTALE);
	assert(ask_flow_fe_owner_status(&flush_table, 5, 2, 50) == -ESTALE);
	flush_flow.hw_backed = false;
	assert(ask_flow_fe_owner_status(&flush_table, 5, 1, 50) == -EOPNOTSUPP);
	flush_flow.hw_backed = true;
	flush_flow.fe_token = 0;
	warnings = warning_count;
	ask_flow_flush_matching(collect_flush_flow, "legacy flush");
	assert(flush_owner_present && del_calls[5] == calls);
	assert(ask_fe_orphan_count == 1);
	assert(warning_count == warnings + 1);

	/* A software-only identified flow has no FE entry to withdraw. */
	flush_flow.fe_token = 50;
	flush_flow.hw_backed = false;
	ask_flow_flush_matching(collect_flush_flow, "software-only flush");
	assert(!flush_owner_present && del_calls[5] == calls);
	assert(ask_fe_orphan_count == 1 && generation_releases == 1);
	alloc_fail = 0;

	/* Collection OOM is reported and leaves the owner intact. */
	flush_flow.hw_backed = true;
	flush_owner_present = true;
	current_generation = 1;
	allocator_expect_lock = false;
	alloc_fail = 1;
	warnings = warning_count;
	ask_flow_flush_matching(ask_collect_flush_victim, "collector OOM");
	assert(flush_owner_present && del_calls[5] == calls);
	assert(warning_count == warnings + 1);
	allocator_expect_lock = true;
	alloc_fail = 0;

	/* A failed FE delete retains the SW owner even if orphan allocation fails. */
	flush_flow.key = c;
	flush_flow.cookie = 5;
	flush_flow.fe_token = 50;
	flush_flow.generation = 1;
	flush_flow.hw_backed = true;
	flush_owner_present = true;
	del_rc[5] = -ETIMEDOUT;
	alloc_fail = 1;
	calls = sw_removals;
	ask_flow_flush_matching(collect_flush_flow, "flush");
	alloc_fail = 0;
	assert(flush_owner_present && sw_removals == calls);
	assert(generation_tombstoned && ask_fe_orphan_count == 2);
	assert(ask_fe_emergency[0x20].emergency);
	assert(ask_fe_emergency[0x20].cookie == 5);
	key_bytes(5, kb);
	assert(!memcmp(ask_fe_emergency[0x20].k, kb, sizeof(kb)));
	memcpy(action.key, kb, sizeof(kb));
	assert(ask_fe_flow_publish(&the_fman, 0x20, kb, &action) == -EBUSY);
	key_bytes(6, kb);
	memcpy(action.key, kb, sizeof(kb));
	assert(ask_fe_flow_publish(&the_fman, 0x20, kb, &action) == -EBUSY);
	c.id = 6;
	assert(ask_fe_flow_remove_retain(&c, 6, 6, "t") == -EAGAIN);
	assert(del_calls[6] == 0);
	assert(ask_fe_flow_publish(&the_fman, 0x21, kb, &action) == 0);

	/* A successful withdrawal unlinks FE before releasing its SW owner. */
	flush_flow.key.port_id = 0x40;
	flush_flow.key.id = 9;
	flush_flow.cookie = 9;
	flush_flow.fe_token = 90;
	flush_flow.generation = 2;
	flush_owner_present = true;
	current_generation = 2;
	generation_tombstoned = false;
	calls = del_calls[9];
	ask_flow_flush_matching(collect_flush_flow, "settled flush");
	assert(!flush_owner_present && del_calls[9] == calls + 1);
	assert(delete_calls_at_removal == calls + 1);
	assert(generation_releases == 2 && !current_generation);
	assert(pending_drops == 3);

	/* An outdated generation must not tombstone or remove its successor. */
	flush_owner_present = true;
	current_generation = 3;
	calls = del_calls[9];
	warnings = warning_count;
	ask_flow_flush_matching(collect_flush_flow, "stale flush");
	assert(flush_owner_present && !generation_tombstoned);
	assert(del_calls[9] == calls && warning_count == warnings + 1);

	/* Even a rejected delete does not prove that the old key is gone. */
	key_bytes(2, kb);
	del_rc[2] = -EINVAL;
	assert(ask_fe_flow_remove_retain(&b, 2, 2, "t") == -EAGAIN);
	assert(ask_fe_orphan_count == 3);
	del_rc[2] = 0;
	calls = del_calls[2];
	memcpy(action.key, kb, sizeof(kb));
	assert(ask_fe_flow_publish(&the_fman, 0x10, kb, &action) == -EBUSY);
	assert(ask_fe_flow_remove_retain(&b, 2, 2, "t") == -EAGAIN);
	assert(del_calls[2] == calls);

	/* Exercise both publication/delete interleavings against the real lock. */
	check_interleaving(3, true);
	check_interleaving(4, false);
	assert(ask_fe_orphan_count == 5);

	/* An old cookie's settled mismatch must leave the new FE owner live. */
	c.port_id = 0x30;
	c.id = 8;
	key_bytes(8, kb);
	memcpy(action.key, kb, sizeof(kb));
	action.owner_token = 200;
	assert(ask_fe_flow_publish(&the_fman, c.port_id, kb, &action) == 0);
	assert(ask_fe_flow_remove_retain(&c, 80, 100, "stale") == 0);
	assert(replacement_present[8] && replacement_token[8] == 200);
	assert(ask_fe_flow_remove_retain(&c, 81, 200, "current") == 0);
	assert(!replacement_present[8] && ask_fe_orphan_count == 5);

	/* Exit must not attempt a key-only delete of either replacement. */
	calls = del_calls[1] + del_calls[2] + del_calls[3] +
		del_calls[4] + del_calls[5];
	ask_fe_orphan_exit();
	assert(del_calls[1] + del_calls[2] + del_calls[3] +
	       del_calls[4] + del_calls[5] == calls);
	assert(replacement_present[1] && ask_fe_orphan_count == 0);
	assert(live_allocs == 0);

	printf("ask_fe_orphan: all checks passed\n");
	return 0;
}
