/* Host mocks for ask.ko's retained FE-delete owner (F-269 caller side). */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
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

struct mutex { int held; };
#define DEFINE_MUTEX(n) struct mutex n = { 0 }
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = 1; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = 0; }

struct work_struct { int unused; };
struct delayed_work { struct work_struct work; };
#define DECLARE_DELAYED_WORK(n, f) struct delayed_work n
static int system_wq_obj;
#define system_wq (&system_wq_obj)
static int scheduled, sched_delay_ms, cancelled;
static unsigned long msecs_to_jiffies(unsigned int ms) { return ms; }
static void mod_delayed_work(int *wq, struct delayed_work *w, unsigned long d)
{
	scheduled++;
	sched_delay_ms = (int)d;
}
static void cancel_delayed_work_sync(struct delayed_work *w) { cancelled++; }

#define min(a, b) ((a) < (b) ? (a) : (b))
#define pr_info_ratelimited(...) do { } while (0)
#define pr_warn_ratelimited(...) do { } while (0)
#define pr_err_ratelimited(...) do { } while (0)
#define pr_err(...) do { } while (0)
#define ask_pr_info(...) do { } while (0)
#define GFP_KERNEL 0

static int alloc_fail, live_allocs;
static void *kzalloc(size_t n, int gfp)
{
	void *p;

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
static int del_rc[256], del_calls[256];
static int fman_pcd_fe_flow_del(struct fman *fm, u8 port, u8 *k, u8 len)
{
	assert(fm == &the_fman);
	assert(len == ASK_FE_KEY_SIZE_DUAL);
	del_calls[k[0]]++;
	return del_rc[k[0]];
}
static int ask_fe_flow_remove(const struct ask_flow_key *key)
{
	u8 k[ASK_FE_KEY_SIZE_DUAL];

	ask_fe_build_key_dual(key, k);
	return fman_pcd_fe_flow_del(ask_hw_get_fman(), key->port_id, k,
				    ASK_FE_KEY_SIZE_DUAL);
}

#include "ask_fe_orphan_body.h"

static void key_bytes(u8 id, u8 k[ASK_FE_KEY_SIZE_DUAL])
{
	struct ask_flow_key key = { .port_id = 0x10, .id = id };

	ask_fe_build_key_dual(&key, k);
}

int main(void)
{
	struct ask_flow_key a = { .port_id = 0x10, .id = 1 };
	struct ask_flow_key b = { .port_id = 0x10, .id = 2 };
	u8 ka[ASK_FE_KEY_SIZE_DUAL], kb[ASK_FE_KEY_SIZE_DUAL];

	key_bytes(1, ka);
	key_bytes(2, kb);

	/* Settled deletes keep no owner. */
	assert(ask_fe_flow_remove_retain(&a, 1, "t") == 0);
	del_rc[1] = -ENOENT;
	assert(ask_fe_flow_remove_retain(&a, 1, "t") == 0);
	assert(ask_fe_orphan_count == 0 && scheduled == 0);

	/* A failed delete retains the FE key and schedules a retry. */
	del_rc[1] = -ETIMEDOUT;
	assert(ask_fe_flow_remove_retain(&a, 1, "t") == -EAGAIN);
	assert(ask_fe_orphan_count == 1 && scheduled == 1);
	assert(sched_delay_ms == 100);

	/* No memory to retain: the caller gets the error and keeps its owner. */
	alloc_fail = 1;
	del_rc[2] = -ESHUTDOWN;
	assert(ask_fe_flow_remove_retain(&b, 2, "t") == -ESHUTDOWN);
	alloc_fail = 0;
	assert(ask_fe_orphan_count == 1);

	/* Reinsert of the retained key is refused while the delete fails. */
	del_calls[1] = 0;
	assert(ask_fe_orphan_claim(&the_fman, 0x10, ka) == -EBUSY);
	assert(del_calls[1] == 1 && ask_fe_orphan_count == 1);

	/* Another key or another port is not blocked and does not delete it. */
	del_calls[1] = 0;
	assert(ask_fe_orphan_claim(&the_fman, 0x10, kb) == 0);
	assert(ask_fe_orphan_claim(&the_fman, 0x11, ka) == 0);
	assert(del_calls[1] == 0 && ask_fe_orphan_count == 1);

	/* A failed retry keeps the orphan and backs off. */
	scheduled = 0;
	ask_fe_orphan_retry_fn(NULL);
	assert(ask_fe_orphan_count == 1 && scheduled == 1);
	assert(sched_delay_ms == 200);

	/* No FMan bound: retry keeps the owner. */
	fman_ptr = NULL;
	ask_fe_orphan_retry_fn(NULL);
	assert(ask_fe_orphan_count == 1);
	fman_ptr = &the_fman;

	/* A settled claim transfers the key to the new insert; a later
	 * retry must never delete the record the new flow now owns. */
	del_rc[1] = 0;
	assert(ask_fe_orphan_claim(&the_fman, 0x10, ka) == 0);
	assert(ask_fe_orphan_count == 0);
	del_calls[1] = 0;
	scheduled = 0;
	ask_fe_orphan_retry_fn(NULL);
	assert(del_calls[1] == 0 && scheduled == 0);

	/* The retry worker settles an orphan once the PCD reports -ENOENT
	 * (record unlinked and quarantined by the PCD itself). */
	del_rc[2] = -ETIMEDOUT;
	assert(ask_fe_flow_remove_retain(&b, 2, "t") == -EAGAIN);
	del_rc[2] = -ENOENT;
	scheduled = 0;
	ask_fe_orphan_retry_fn(NULL);
	assert(ask_fe_orphan_count == 0 && scheduled == 0);

	/* Exit stops retries, frees software state, reschedules nothing. */
	del_rc[2] = -ETIMEDOUT;
	assert(ask_fe_flow_remove_retain(&b, 2, "t") == -EAGAIN);
	scheduled = 0;
	ask_fe_orphan_exit();
	assert(cancelled == 1 && ask_fe_orphan_count == 0);
	assert(live_allocs == 0);
	assert(ask_fe_flow_remove_retain(&b, 2, "t") == -EAGAIN);
	assert(scheduled == 0);

	printf("ask_fe_orphan: all checks passed\n");
	return 0;
}
