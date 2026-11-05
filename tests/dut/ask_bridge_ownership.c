// SPDX-License-Identifier: GPL-2.0
/* Host fault injection for production bridge admission, ageing and deletion. */
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

#define FMAN_PCD_FE_L2_DA_KEY_SIZE 16
#define ASK_BRIDGE_K_SA 6
#define ASK_BRIDGE_K_TCI 12
#define ASK_BRIDGE_K_DA 0
#define ASK_BRIDGE_REC_MAX 2048
#define ASK_BRIDGE_AGE_PERIOD 5
#define FMAN_PCD_FE_OWNER_ASK 0
#define GFP_KERNEL 0
#define SWITCHDEV_FDB_OFFLOADED 1
#define SWITCHDEV_FDB_ADD_TO_BRIDGE 2
#define pr_warn_ratelimited(...) ((void)0)
#define pr_err_ratelimited(...) (token_errors++)
#define ASSERT_RTNL() ((void)0)
#define lockdep_assert_held(x) ((void)0)
#define time_after_eq(a, b) ((a) >= (b))

struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD(n) struct list_head n = { &(n), &(n) }
#define container_of(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
#define list_entry(p, t, m) container_of(p, t, m)
#define list_for_each_entry(pos, head, member) \
	for (struct list_head *it = (head)->next; \
	     it != (head) && ((pos) = list_entry(it, typeof(*(pos)), member), 1); \
	     it = it->next)
#define list_for_each_entry_safe(pos, tmp, head, member) \
	for (struct list_head *it = (head)->next, *next = it->next; \
	     it != (head) && ((pos) = list_entry(it, typeof(*(pos)), member), \
			      (tmp) = list_entry(next, typeof(*(tmp)), member), 1); \
	     it = next, next = it->next)

static void list_add_tail(struct list_head *node, struct list_head *head)
{
	node->prev = head->prev;
	node->next = head;
	head->prev->next = node;
	head->prev = node;
}

static void list_del(struct list_head *node)
{
	node->prev->next = node->next;
	node->next->prev = node->prev;
}

static bool ether_addr_equal(const u8 *a, const u8 *b)
{
	return memcmp(a, b, 6) == 0;
}

static void ether_addr_copy(u8 *dst, const u8 *src)
{
	memcpy(dst, src, 6);
}

static u16 get_unaligned_be16(const u8 *p)
{
	return (u16)(p[0] << 8) | p[1];
}

#include "bridge_type.h"

static LIST_HEAD(ask_bridge_recs);
static LIST_HEAD(ask_bridge_owns);
static unsigned int ask_bridge_n_recs;
static int releases, freed, token_errors;
static unsigned long jiffies;

struct mutex { bool held; };
static void mutex_lock(struct mutex *lock) { assert(!lock->held); lock->held = true; }
static void mutex_unlock(struct mutex *lock) { assert(lock->held); lock->held = false; }
static struct mutex ask_bridge_lock;
static void ask_bridge_kick(void) {}

struct fman { int unused; };
struct ask_hw_pcd { struct mutex lock; struct fman *fman; };
struct fman_pcd_fe_flow_action {
	u8 key[FMAN_PCD_FE_L2_DA_KEY_SIZE];
	u64 owner_token;
};
static struct fman fman;
static struct ask_hw_pcd hardware = { .fman = &fman };
static bool hw_available = true, l2_available = true, flow_valid;
static int add_result, adds, delete_result, deletes;
static bool live_present;
static u8 live_key[FMAN_PCD_FE_L2_DA_KEY_SIZE];
static u64 live_token, live_pkts, next_token = 1;

static void set_live_record(const u8 *key, u64 token)
{
	memcpy(live_key, key, sizeof(live_key));
	live_token = token;
	live_present = true;
}

static u64 fman_pcd_fe_alloc_token(void)
{
	return next_token ? next_token++ : 0;
}

static struct ask_hw_pcd *ask_hw_pcd_get(void)
{
	return hw_available ? &hardware : NULL;
}

static struct ask_hw_pcd *ask_hw_l2_port(struct ask_hw_pcd *h, u8 port)
{
	assert(h->lock.held && port == 0x10);
	return l2_available ? h : NULL;
}

static unsigned long ask_hw_get_enq_fe_off(void) { return 1; }

static int ask_bridge_fe_action(const u8 *key, u32 fqid, unsigned long off,
				u8 act, u16 vid,
				struct fman_pcd_fe_flow_action *action)
{
	memset(action, 0, sizeof(*action));
	memcpy(action->key, key, sizeof(action->key));
	return 0;
}

static int fman_pcd_fe_flow_add(struct fman *fm, u8 port,
				struct fman_pcd_fe_flow_action *action)
{
	assert(fm == &fman && port == 0x10 && hardware.lock.held);
	adds++;
	if (add_result)
		return add_result;
	if (live_present && !memcmp(live_key, action->key, sizeof(live_key)) &&
	    live_token && !action->owner_token)
		return -EEXIST;
	if (live_present && !memcmp(live_key, action->key, sizeof(live_key)) &&
	    live_token && action->owner_token < live_token)
		return -ESTALE;
	set_live_record(action->key, action->owner_token);
	return 0;
}

static int fman_pcd_fe_flow_del_identified(struct fman *fm, u8 port,
					  const u8 *key, u8 len, u32 owner,
					  u64 token)
{
	assert(fm == &fman && port == 0x10 && len == FMAN_PCD_FE_L2_DA_KEY_SIZE);
	assert(hardware.lock.held && owner == FMAN_PCD_FE_OWNER_ASK && token);
	deletes++;
	if (delete_result)
		return delete_result;
	if (!live_present || memcmp(live_key, key, sizeof(live_key)))
		return 0;
	if (live_token != token)
		return -ESTALE;
	live_present = false;
	return 0;
}

static int fman_pcd_fe_flow_get_stats_identified(struct fman *fm, u8 port,
						const u8 *key, u8 len, u32 owner,
						u64 token, u64 *pkts, u64 *bytes,
						u32 *ts)
{
	assert(fm == &fman && port == 0x10 && len == FMAN_PCD_FE_L2_DA_KEY_SIZE);
	assert(hardware.lock.held && owner == FMAN_PCD_FE_OWNER_ASK && token);
	if (!live_present || memcmp(live_key, key, sizeof(live_key)))
		return -ENOENT;
	if (live_token != token)
		return -ESTALE;
	*pkts = live_pkts;
	*bytes = 0;
	*ts = 0;
	return 0;
}

#include "bridge_hw.h"

static void ask_bridge_sa_release(int bridge, int ingress, const u8 *sa, u16 vid)
{
	releases++;
}

static void kfree(void *p)
{
	freed++;
	free(p);
}

static void *kzalloc(size_t size, int flags)
{
	return calloc(1, size);
}

static bool ask_bridge_flow_ok(int bridge, int in, int out, const u8 *key,
			       u16 vid, bool filtering, u8 *port, u32 *fqid,
			       u8 *act)
{
	*port = 0x10;
	*fqid = 9;
	*act = 1;
	return flow_valid;
}

#include "bridge_delete.h"

struct net_device { int ifindex; };
struct ask_bridge_own {
	struct list_head node;
	int br_ifindex, port_ifindex;
	u8 addr[6];
	u16 vid;
};
static struct ask_bridge_own existing_own;

static bool ask_bridge_vid(struct net_device *br, struct net_device *in,
			   u16 tci, u16 *vid)
{
	*vid = 4;
	return true;
}
static bool br_vlan_enabled(struct net_device *br) { return false; }
static struct ask_bridge_own *ask_bridge_own_find(int br, int in, const u8 *sa,
						  u16 vid)
{
	return &existing_own;
}
static bool ask_bridge_user_mac(const u8 *sa) { return false; }
static int ask_bridge_fdb_call(unsigned long event, int in, const u8 *sa,
			       u16 vid, bool offloaded)
{
	return 0;
}

#include "bridge_admit.h"

struct work_struct { int unused; };
struct delayed_work { int unused; };
static struct delayed_work ask_bridge_age_work;
static void *system_wq;
static int init_net;
static struct net_device bridge_dev = { .ifindex = 1 };
static unsigned long bridge_age = 1000;

static void rtnl_lock(void) {}
static void rtnl_unlock(void) {}
static struct net_device *__dev_get_by_index(int *net, int ifindex)
{
	return ifindex == bridge_dev.ifindex ? &bridge_dev : NULL;
}
static unsigned long br_get_ageing_time(struct net_device *br)
{
	return bridge_age;
}
static unsigned long clock_t_to_jiffies(unsigned long age) { return age; }
static void queue_delayed_work(void *wq, struct delayed_work *work,
			       unsigned long delay) {}

#include "bridge_age.h"
#include "bridge_forget.h"

static struct ask_bridge_rec *new_record(u8 da)
{
	struct ask_bridge_rec *r = calloc(1, sizeof(*r));

	assert(r);
	r->port = 0x10;
	r->key[0] = da;
	r->key[ASK_BRIDGE_K_SA] = 0x42;
	r->br_ifindex = 1;
	r->in_ifindex = 2;
	r->out_ifindex = 3;
	r->vid = 4;
	r->fqid = 9;
	r->act = 1;
	r->fe_token = 1000 + da;
	list_add_tail(&r->node, &ask_bridge_recs);
	ask_bridge_n_recs++;
	return r;
}

static void discard_record(struct ask_bridge_rec *r)
{
	list_del(&r->node);
	ask_bridge_n_recs--;
	free(r);
}

static void assert_unsettled(struct ask_bridge_rec *r, int expected_deletes)
{
	u8 key[FMAN_PCD_FE_L2_DA_KEY_SIZE];

	memcpy(key, r->key, sizeof(key));
	assert(ask_bridge_n_recs == 1 && releases == 0);
	assert(ask_bridge_rec_find(0x10, key) == r);
	assert(ask_bridge_sa_in_use(1, 2, key + ASK_BRIDGE_K_SA, 4));

	/* A different ASK caller can publish the same key after recovery. */
	flow_valid = true;
	delete_result = 0;
	set_live_record(key, r->fe_token + 1);
	ask_bridge_resync();
	assert(deletes == expected_deletes && live_present);
	assert(ask_bridge_n_recs == 1 && releases == 0);
	assert(ask_bridge_rec_find(0x10, key) == r);
	ask_bridge_rec_del(r);
	assert(deletes == expected_deletes && live_present);
	/* A void port-disengage result cannot settle an earlier failed delete. */
	ask_bridge_forget_port(0x10);
	ask_bridge_resync();
	assert(deletes == expected_deletes && live_present);
	assert(ask_bridge_n_recs == 1 && releases == 0);
	assert(ask_bridge_rec_find(0x10, key) == r);
}

int main(void)
{
	static const int errors[] = { -ETIMEDOUT, -EIO, -ESHUTDOWN };
	struct ask_bridge_rec *r = new_record(1);

	hw_available = false;
	ask_bridge_rec_del(r);
	hw_available = true;
	assert_unsettled(r, 0);
	discard_record(r);

	r = new_record(2);
	l2_available = false;
	ask_bridge_rec_del(r);
	l2_available = true;
	assert_unsettled(r, 0);
	discard_record(r);

	for (unsigned int i = 0; i < sizeof(errors) / sizeof(errors[0]); i++) {
		r = new_record(3 + i);
		delete_result = errors[i];
		ask_bridge_rec_del(r);
		assert_unsettled(r, i + 1);
		discard_record(r);
	}
	live_present = false;

	/* An SA stays owned until its final record is removed. */
	r = new_record(7);
	struct ask_bridge_rec *other = new_record(8);
	ask_bridge_rec_del(r);
	assert(ask_bridge_n_recs == 1 && releases == 0);
	ask_bridge_rec_del(other);
	assert(ask_bridge_n_recs == 0 && releases == 1 && freed == 2);

	/* The existing successful-disengage bookkeeping remains unchanged. */
	r = new_record(9);
	int before = deletes;
	ask_bridge_forget_port(0x10);
	ask_bridge_resync();
	assert(deletes == before && ask_bridge_n_recs == 0);
	assert(releases == 2 && freed == 3);

	/* Exhaustion and failed admission must not publish a software owner. */
	struct net_device in = { .ifindex = 2 };
	u8 key[FMAN_PCD_FE_L2_DA_KEY_SIZE] = { 10 };
	key[ASK_BRIDGE_K_SA] = 0x42;
	int before_adds = adds;
	next_token = 0;
	ask_bridge_admit_key(&bridge_dev, &in, 3, key);
	assert(token_errors == 1 && adds == before_adds && !ask_bridge_n_recs);
	next_token = 2000;
	add_result = -ENOMEM;
	ask_bridge_admit_key(&bridge_dev, &in, 3, key);
	assert(adds == before_adds + 1 && !ask_bridge_n_recs && !live_present);
	add_result = 0;

	/* Admission, ageing and deletion carry the same nonzero FE identity. */
	assert(ask_hw_l2_record(0x10, key, 9, 1, 4, 0, true) == -EINVAL);
	assert(ask_hw_l2_record(0x10, key, 0, 1, 4, 0, false) == -EINVAL);
	u64 packets = 999;
	assert(ask_hw_l2_record_hits(0x10, key, 0, &packets) == -EINVAL);
	jiffies = 10;
	ask_bridge_admit_key(&bridge_dev, &in, 3, key);
	r = ask_bridge_rec_find(0x10, key);
	assert(r && r->fe_token == 2001 && live_token == r->fe_token);
	live_pkts = 7;
	jiffies = 20;
	ask_bridge_age_fn(NULL);
	assert(r->pkts == 7 && r->last_hit == 20);

	/* Legacy key-only publishers cannot replace a tokenized bridge record. */
	struct fman_pcd_fe_flow_action action = { .owner_token = 0 };
	memcpy(action.key, key, sizeof(action.key));
	mutex_lock(&hardware.lock);
	assert(fman_pcd_fe_flow_add(&fman, 0x10, &action) == -EEXIST);
	action.owner_token = fman_pcd_fe_alloc_token();
	assert(action.owner_token > r->fe_token);
	assert(!fman_pcd_fe_flow_add(&fman, 0x10, &action));
	mutex_unlock(&hardware.lock);
	live_pkts = 99;
	jiffies = 30;
	before = deletes;
	ask_bridge_age_fn(NULL);
	assert(r->pkts == 7 && r->last_hit == 20 && r->unsettled);
	assert(deletes == before && live_present && releases == 2);
	assert(ask_hw_l2_record_hits(0x10, key, r->fe_token, &packets) == -ESTALE);
	assert(packets == 999);
	assert(live_token == action.owner_token);
	ask_bridge_forget_port(0x10);
	ask_bridge_rec_del(r);
	assert(deletes == before && live_present && !r->dead);
	discard_record(r);
	live_present = false;

	/* An old record cannot delete a newer identified replacement. */
	key[0] = 11;
	jiffies = 40;
	ask_bridge_admit_key(&bridge_dev, &in, 3, key);
	r = ask_bridge_rec_find(0x10, key);
	assert(r);
	memcpy(action.key, key, sizeof(action.key));
	action.owner_token = fman_pcd_fe_alloc_token();
	mutex_lock(&hardware.lock);
	assert(!fman_pcd_fe_flow_add(&fman, 0x10, &action));
	mutex_unlock(&hardware.lock);
	before = deletes;
	ask_bridge_rec_del(r);
	assert(deletes == before + 1 && r->unsettled);
	assert(ask_bridge_n_recs == 1 && releases == 2 && live_present);
	assert(live_token == action.owner_token);
	discard_record(r);
	live_present = false;

	/* Matching identity still permits normal deletion and FDB release. */
	key[0] = 12;
	jiffies = 50;
	ask_bridge_admit_key(&bridge_dev, &in, 3, key);
	r = ask_bridge_rec_find(0x10, key);
	assert(r && live_present && live_token == r->fe_token);
	ask_bridge_rec_del(r);
	assert(!live_present && !ask_bridge_n_recs && releases == 3);

	puts("bridge identity: admission, ageing, replacement and failure containment pass");
	return 0;
}
