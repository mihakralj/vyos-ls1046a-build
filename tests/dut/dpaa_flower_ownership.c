/* Host mocks for F-279 tc-flower ehash ownership (0198 + F-279). */
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

struct list_head { struct list_head *next, *prev; };
static void INIT_LIST_HEAD(struct list_head *h) { h->next = h->prev = h; }
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
	e->next = e->prev = NULL;
}
#define container_of(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
#define list_entry(p, t, m) container_of(p, t, m)
#define list_for_each_entry(pos, head, m)                                   \
	for (pos = list_entry((head)->next, typeof(*pos), m);               \
	     &pos->m != (head);                                             \
	     pos = list_entry(pos->m.next, typeof(*pos), m))
#define list_for_each_entry_safe(pos, n, head, m)                           \
	for (pos = list_entry((head)->next, typeof(*pos), m),               \
	     n = list_entry(pos->m.next, typeof(*pos), m);                  \
	     &pos->m != (head);                                             \
	     pos = n, n = list_entry(n->m.next, typeof(*n), m))

struct mutex { int held; };
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = 1; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = 0; }
typedef struct { int held; } spinlock_t;
static void spin_lock(spinlock_t *l) { assert(!l->held); l->held = 1; }
static void spin_unlock(spinlock_t *l) { assert(l->held); l->held = 0; }

#define GFP_KERNEL 0
static int live_allocs;
static void *kzalloc(size_t n, int gfp)
{
	live_allocs++;
	return calloc(1, n);
}
static void kfree(void *p)
{
	if (p)
		live_allocs--;
	free(p);
}

#define FMAN_PCD_FE_ROUTED_KEY_SIZE 50
#define FMAN_FE_FLOW_KEY_MAX 54
struct fman { int unused; };
struct device { int unused; };
struct fman_port { u8 id; };
struct mac_device { struct device *fman_dev; struct fman_port *port[2]; };
struct net_device { int unused; };
struct dpaa_priv {
	struct mac_device *mac_dev;
	struct net_device *net_dev;
	spinlock_t flower_lock;
	struct list_head cls_flowers;
	struct mutex flower_mutex;
	struct list_head cls_flower_orphans;
};
struct flow_cls_offload { unsigned long cookie; };
struct fman_pcd_fe_flow_action {
	u8 key[FMAN_FE_FLOW_KEY_MAX];
	u8 key_size;
	u8 table_idx;
	u8 drop_action;
};

static struct fman the_fman;
static struct dpaa_priv the_priv;
static struct net_device the_netdev;
static struct dpaa_priv *netdev_priv(struct net_device *n) { return &the_priv; }
static struct fman *fman_bind(struct device *d) { return &the_fman; }
static u8 fman_port_get_id(struct fman_port *p) { return p->id; }
#define netdev_err(...) do { } while (0)

/* Mock silicon: keys are distinct by byte 1; present[] is the ehash. */
static bool present[256];
static int add_rc, del_rc;
static int fman_pcd_fe_flow_add(struct fman *fm, u8 port,
				struct fman_pcd_fe_flow_action *a)
{
	if (add_rc)
		return add_rc;
	present[a->key[1]] = true;	/* F-219: a duplicate is evicted */
	return 0;
}
static int fman_pcd_fe_flow_del(struct fman *fm, u8 port, const u8 *k,
				u8 key_size)
{
	if (!present[k[1]])
		return -ENOENT;
	if (del_rc)
		return del_rc;
	present[k[1]] = false;
	return 0;
}

#include "dpaa_flower_body.h"

static void key_of(u8 id, u8 *k)
{
	memset(k, 0, FMAN_PCD_FE_ROUTED_KEY_SIZE);
	k[0] = 0x40;
	k[1] = id;
}

static int do_replace(unsigned long cookie, u8 id)
{
	struct flow_cls_offload f = { .cookie = cookie };
	u8 key[FMAN_PCD_FE_ROUTED_KEY_SIZE];

	key_of(id, key);
	return flower_replace_tail(&the_netdev, &f, key,
				   FMAN_PCD_FE_ROUTED_KEY_SIZE);
}

static int do_destroy(unsigned long cookie)
{
	struct flow_cls_offload f = { .cookie = cookie };

	return dpaa_setup_tc_flower_destroy(&the_netdev, &f);
}

/* Every silicon key has exactly one software owner (active or orphan). */
static void check_owners(void)
{
	struct dpaa_cls_flower *pos;
	int owners[256] = { 0 };
	int i;

	list_for_each_entry(pos, &the_priv.cls_flowers, node)
		owners[pos->key[1]]++;
	list_for_each_entry(pos, &the_priv.cls_flower_orphans, node)
		owners[pos->key[1]]++;
	for (i = 0; i < 256; i++) {
		assert(owners[i] <= 1);
		assert(present[i] == (owners[i] == 1) || !present[i]);
		if (present[i])
			assert(owners[i] == 1);
	}
	assert(!the_priv.flower_mutex.held && !the_priv.flower_lock.held);
}

static int count(struct list_head *h)
{
	struct list_head *p;
	int n = 0;

	for (p = h->next; p != h; p = p->next)
		n++;
	return n;
}

int main(void)
{
	static struct fman_port rx = { .id = 0x10 };
	static struct device fdev;
	static struct mac_device mac = { .fman_dev = &fdev, .port = { &rx } };

	the_priv.mac_dev = &mac;
	the_priv.net_dev = &the_netdev;
	INIT_LIST_HEAD(&the_priv.cls_flowers);
	INIT_LIST_HEAD(&the_priv.cls_flower_orphans);

	assert(do_replace(1, 7) == 0 && present[7]);
	check_owners();

	/* Another cookie may not take the same key (F-219 would evict). */
	assert(do_replace(2, 7) == -EEXIST);
	check_owners();

	/* Identical re-REPLACE is a no-op. */
	assert(do_replace(1, 7) == 0 && count(&the_priv.cls_flowers) == 1);

	/* Failed DESTROY keeps the record owned as an orphan. */
	del_rc = -ETIMEDOUT;
	assert(do_destroy(1) == -ETIMEDOUT);
	assert(present[7] && count(&the_priv.cls_flower_orphans) == 1);
	check_owners();

	/* Its key is refused while the delete stays unsettled. */
	assert(do_replace(3, 7) == -EBUSY);
	check_owners();

	/* Once the delete settles, the next op reaps the orphan. */
	del_rc = 0;
	assert(do_replace(3, 7) == 0);
	assert(count(&the_priv.cls_flower_orphans) == 0 && present[7]);
	check_owners();

	/* Same-cookie REPLACE with a new key: the old key is released, and
	 * on delete failure retained rather than silently overwritten. */
	del_rc = -ESHUTDOWN;
	assert(do_replace(3, 8) == 0);
	assert(present[7] && present[8]);
	assert(count(&the_priv.cls_flowers) == 1);
	assert(count(&the_priv.cls_flower_orphans) == 1);
	check_owners();

	/* A failed add leaves nothing behind. */
	del_rc = 0;
	add_rc = -ENOSPC;
	assert(do_replace(4, 9) == -ENOSPC && !present[9]);
	add_rc = 0;
	check_owners();
	assert(count(&the_priv.cls_flower_orphans) == 0 && !present[7]);

	/* A record the PCD already unlinked (-ENOENT) settles on DESTROY. */
	present[8] = false;
	assert(do_destroy(3) == 0 && count(&the_priv.cls_flowers) == 0);
	check_owners();
	assert(live_allocs == 0);

	printf("dpaa_flower_ownership: all checks passed\n");
	return 0;
}
