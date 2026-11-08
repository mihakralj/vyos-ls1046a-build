// SPDX-License-Identifier: GPL-2.0
/* Host fault tests for the generated FE debugfs ownership guards. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;

#define __user
#define FMAN_EHASH_FLOW_KEY_MAX 56
#define FMAN_EHASH_FLOW_KEY_OFF 8
#define pr_debug(...) ((void)0)
#define pr_warn_ratelimited(...) ((void)0)
#define dev_warn(...) ((void)0)
#define dev_dbg(...) ((void)0)
#define lockdep_assert_held(lock) assert((lock)->locked)
#define bitmap_empty(map, bits) (!(map)[0])
#define container_of(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))

#include "fman_debugfs_owner_constants.h"

struct list_head { struct list_head *next, *prev; };
struct hlist_node { struct hlist_node *next; };
struct hlist_head { struct hlist_node *first; };

static void init_list(struct list_head *head) { head->next = head->prev = head; }
static bool list_empty(const struct list_head *head) { return head->next == head; }
static void list_add_tail(struct list_head *item, struct list_head *head)
{
	item->prev = head->prev;
	item->next = head;
	head->prev->next = item;
	head->prev = item;
}
static void list_del(struct list_head *item)
{
	item->prev->next = item->next;
	item->next->prev = item->prev;
}
#define list_for_each_entry(pos, head, member)                            \
	for (struct list_head *__node = (head)->next;                      \
	     __node != (head) &&                                           \
	     ((pos) = container_of(__node, typeof(*(pos)), member), true); \
	     __node = __node->next)
#define hlist_for_each_entry(pos, head, member)                            \
	for (pos = (head)->first ?                                         \
	     container_of((head)->first, typeof(*pos), member) : NULL;     \
	     pos;                                                          \
	     pos = pos->member.next ?                                      \
	     container_of(pos->member.next, typeof(*pos), member) : NULL)
#define list_first_entry_or_null(head, type, member)                       \
	(list_empty(head) ? NULL : container_of((head)->next, type, member))

struct mutex { bool locked; };
struct fman_pcd_ehash_flow {
	struct list_head node;
	struct hlist_node bnode;
	void *record;
	u8 data[FMAN_EHASH_FLOW_KEY_OFF + FMAN_EHASH_FLOW_KEY_MAX];
	u8 key_size, owner;
	u64 owner_token;
};
struct fman_pcd_ehash_table {
	struct list_head node, flows, quarantined_flows, quarantined_cu;
	struct hlist_head bidx[4];
	struct fman_pcd *pcd;
	u8 key_size, hash_shift;
	u32 hash_mask;
	bool draining, poisoned;
};
struct fman_pcd_fe_obj { struct list_head node; unsigned long muram_off; };
struct fman { int unused; };
struct fman_pcd {
	struct mutex fe_lock;
	struct list_head fe_ehash_tables, fe_enq;
	unsigned long fe_exit_off;
	struct fman *fman;
	bool fe_vm_chain_built, fe_vm_chain_faulted, fe_obs_armed;
	unsigned long fe_port_armed[1], fe_port_faulted[1];
	void *fe_arming_v4_table;
};
struct fman_pcd_nat_params;
struct fman_pcd_vlan_params;
struct file { void *private_data; };
struct seq_file { void *private; };

static unsigned int add_calls, del_calls, clear_calls;
static int add_error, del_error, clear_error;
static bool copy_fails;

static void mutex_lock(struct mutex *lock)
{
	assert(!lock->locked);
	lock->locked = true;
}
static void mutex_unlock(struct mutex *lock)
{
	assert(lock->locked);
	lock->locked = false;
}
static int copy_from_user(char *dst, const char *src, size_t count)
{
	if (copy_fails)
		return 1;
	memcpy(dst, src, count);
	return 0;
}
static int fman_pcd_hexval(char c)
{
	if (c >= '0' && c <= '9')
		return c - '0';
	if (c >= 'a' && c <= 'f')
		return c - 'a' + 10;
	if (c >= 'A' && c <= 'F')
		return c - 'A' + 10;
	return -1;
}
static unsigned int fman_pcd_ehash_bucket_index(const u8 *key, u8 size,
						u8 shift, u32 mask)
{
	(void)size;
	(void)shift;
	return key[0] & mask;
}
static struct fman_pcd_ehash_table *
fman_pcd_ehash_table_by_index(struct fman_pcd *pcd, unsigned int index)
{
	struct fman_pcd_ehash_table *table;

	list_for_each_entry(table, &pcd->fe_ehash_tables, node)
		if (index-- == 0)
			return table;
	return NULL;
}
static struct fman *fman_pcd_get_fman(struct fman_pcd *pcd)
{
	return pcd->fman;
}
static unsigned int sync_writes;
static void fman_set_fpm_extc(struct fman *fm, u32 value)
{
	assert(fm && value == 0x80000000U);
	sync_writes++;
}
static u32 fman_get_fpm_extc(struct fman *fm) { (void)fm; return 0; }
static void udelay(unsigned int n) { (void)n; }
static unsigned int ehash_insert_sync_timeouts;	/* F-288 */
static int fman_pcd_ehash_recover(struct fman_pcd_ehash_table *t)
{
	return t->poisoned ? -ETIMEDOUT : 0;
}
static int fman_pcd_ehash_flow_clear_all(struct fman_pcd *pcd);
static int fman_pcd_ehash_del_key(struct fman_pcd_ehash_table *t,
				  const u8 *key, u8 key_size);
static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,
				  const u8 *key, u8 key_size,
				  u32 enq_off, u32 fqid, bool stats,
				  const u8 *dst, const u8 *src, u16 eth_type,
				  const struct fman_pcd_nat_params *nat,
				  const struct fman_pcd_vlan_params *vlan,
				  u8 owner, u64 owner_token);

#include "fman_debugfs_owner_body.h"

static void remove_flow(struct fman_pcd_ehash_table *t,
			struct fman_pcd_ehash_flow *flow)
{
	u8 *key = (u8 *)flow->record + FMAN_EHASH_FLOW_KEY_OFF;
	unsigned int bucket = fman_pcd_ehash_bucket_index(key, flow->key_size,
							   t->hash_shift, t->hash_mask);
	struct hlist_node **node = &t->bidx[bucket].first;

	while (*node && *node != &flow->bnode)
		node = &(*node)->next;
	assert(*node);
	*node = flow->bnode.next;
	list_del(&flow->node);
	free(flow);
}

static struct fman_pcd_ehash_flow *
install_flow(struct fman_pcd_ehash_table *t, const u8 *key, u8 owner, u64 token)
{
	struct fman_pcd_ehash_flow *flow = calloc(1, sizeof(*flow));
	unsigned int bucket;

	assert(flow);
	flow->record = flow->data;
	flow->key_size = t->key_size;
	flow->owner = owner;
	flow->owner_token = token;
	memcpy(flow->data + FMAN_EHASH_FLOW_KEY_OFF, key, flow->key_size);
	bucket = fman_pcd_ehash_bucket_index(key, flow->key_size,
					     t->hash_shift, t->hash_mask);
	flow->bnode.next = t->bidx[bucket].first;
	t->bidx[bucket].first = &flow->bnode;
	list_add_tail(&flow->node, &t->flows);
	return flow;
}

static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,
				  const u8 *key, u8 key_size,
				  u32 enq_off, u32 fqid, bool stats,
				  const u8 *dst, const u8 *src, u16 eth_type,
				  const struct fman_pcd_nat_params *nat,
				  const struct fman_pcd_vlan_params *vlan,
				  u8 owner, u64 owner_token)
{
	struct fman_pcd_ehash_flow *old;
	int f269_err;

	(void)enq_off; (void)fqid; (void)stats; (void)dst; (void)src;
	(void)eth_type; (void)nat; (void)vlan;
	add_calls++;
#include "fman_ehash_add_guard.h"
	if (add_error)
		return add_error;
	if (key_size != t->key_size)
		return -EINVAL;
	old = fman_pcd_ehash_lookup(t, key, key_size);
	if (old)
		remove_flow(t, old);
	install_flow(t, key, owner, owner_token);
	return 0;
}

static int fman_pcd_ehash_del_key(struct fman_pcd_ehash_table *t,
				  const u8 *key, u8 key_size)
{
	struct fman_pcd_ehash_flow *flow;

	del_calls++;
	if (del_error)
		return del_error;
	if (key_size != t->key_size)
		return -EINVAL;
	flow = fman_pcd_ehash_lookup(t, key, key_size);
	if (!flow)
		return -ENOENT;
	remove_flow(t, flow);
	return 0;
}

static int fman_pcd_ehash_flow_clear_all(struct fman_pcd *pcd)
{
	struct fman_pcd_ehash_table *t;

	assert(pcd->fe_lock.locked);
	clear_calls++;
	if (clear_error)
		return clear_error;
	list_for_each_entry(t, &pcd->fe_ehash_tables, node)
		while (!list_empty(&t->flows))
			remove_flow(t, container_of(t->flows.next,
						    struct fman_pcd_ehash_flow, node));
	return 0;
}

static ssize_t run(struct file *file, const char *command)
{
	loff_t offset = 0;

	return fman_pcd_fe_flow_write(file, command, strlen(command), &offset);
}

int main(void)
{
	static const u8 a[] = { 1, 2 }, b[] = { 3, 4 };
	static const u8 owners[] = { FMAN_PCD_FE_OWNER_ASK,
		FMAN_PCD_FE_OWNER_FLOWER, FMAN_PCD_FE_OWNER_NTUPLE,
		FMAN_PCD_FE_OWNER_WGFP };
	struct fman_pcd pcd = {};
	struct fman fm = {};
	struct fman_pcd_ehash_table t0 = { .key_size = 2, .hash_mask = 3 };
	struct fman_pcd_ehash_table t1 = { .key_size = 2, .hash_mask = 3 };
	struct fman_pcd_fe_obj enq = { .muram_off = 0x100 };
	struct seq_file seq = { .private = &pcd };
	struct file file = { .private_data = &seq };
	struct fman_pcd_ehash_flow *flow;
	struct list_head uncertain;
	unsigned int before;

	pcd.fman = &fm;
	t0.pcd = &pcd;
	t1.pcd = &pcd;
	init_list(&pcd.fe_ehash_tables);
	init_list(&pcd.fe_enq);
	init_list(&t0.flows); init_list(&t0.quarantined_flows);
	init_list(&t0.quarantined_cu); init_list(&t1.flows);
	init_list(&t1.quarantined_flows); init_list(&t1.quarantined_cu);
	list_add_tail(&t0.node, &pcd.fe_ehash_tables);
	list_add_tail(&t1.node, &pcd.fe_ehash_tables);
	list_add_tail(&enq.node, &pcd.fe_enq);

#ifdef FMAN_TEST_DEBUG_CHAIN_GUARD
	pcd.fe_obs_armed = true;
	assert(run(&file, "add 0 0102 1") == -EBUSY);
	assert(run(&file, "del 0102") == -EBUSY);
	assert(run(&file, "clear") == -EBUSY);
	assert(!add_calls && !del_calls && !clear_calls && !sync_writes &&
	       !pcd.fe_lock.locked);
	pcd.fe_obs_armed = false;
#endif

	for (size_t i = 0; i < sizeof(owners) / sizeof(owners[0]); i++) {
		flow = install_flow(&t0, a, owners[i], i ? 0 : 123);
		before = add_calls;
		assert(run(&file, "add 0 0102 1") == -EEXIST);
		assert(add_calls == before);
		before = del_calls;
		assert(run(&file, "del 0102") == -EBUSY);
		assert(del_calls == before);
		assert(fman_pcd_ehash_lookup(&t0, a, 2) == flow);
		assert(flow->owner == owners[i]);
		remove_flow(&t0, flow);
	}

	t0.pcd = NULL;
	assert(run(&file, "add 0 0102 1") == -ENODEV);
	t0.pcd = &pcd;
	pcd.fman = NULL;
	assert(run(&file, "add 0 0102 1") == -ENODEV);
	assert(!fman_pcd_ehash_lookup(&t0, a, 2));
	assert(!sync_writes && !pcd.fe_lock.locked);
	pcd.fman = &fm;
	assert(run(&file, "add 0 0102 1") == 12);
	assert(sync_writes == 1);
	flow = fman_pcd_ehash_lookup(&t0, a, 2);
	assert(flow && flow->owner == FMAN_PCD_FE_OWNER_DEBUGFS &&
	       flow->owner_token == 0);
	assert(run(&file, "add 0 0102 1") == 12);
	flow = fman_pcd_ehash_lookup(&t0, a, 2);
	assert(flow && flow->owner == FMAN_PCD_FE_OWNER_DEBUGFS);
	del_error = -ETIMEDOUT;
	assert(run(&file, "del 0102") == -ETIMEDOUT);
	assert(fman_pcd_ehash_lookup(&t0, a, 2) == flow);
	del_error = 0;
	assert(run(&file, "del 0102") == 8);
	assert(run(&file, "del 0102") == -ENOENT);

	assert(run(&file, "add 0 0102 1") == 12);
	flow = install_flow(&t1, b, FMAN_PCD_FE_OWNER_ASK, 0);
	before = clear_calls;
	assert(run(&file, "clear") == -EBUSY);
	assert(clear_calls == before && fman_pcd_ehash_lookup(&t0, a, 2));
	remove_flow(&t1, flow);
	list_add_tail(&uncertain, &t1.quarantined_flows);
	assert(run(&file, "clear") == -EBUSY);
	list_del(&uncertain);
	list_add_tail(&uncertain, &t1.quarantined_cu);
	assert(run(&file, "clear") == -EBUSY);
	list_del(&uncertain);
	t1.poisoned = true;
	assert(run(&file, "clear") == -EBUSY);
	t1.poisoned = false;
	t1.draining = true;
	assert(run(&file, "clear") == -EBUSY);
	t1.draining = false;
	assert(clear_calls == before);
	clear_error = -ETIMEDOUT;
	assert(run(&file, "clear") == -ETIMEDOUT);
	assert(fman_pcd_ehash_lookup(&t0, a, 2));
	clear_error = 0;
	assert(run(&file, "clear") == 5);
	assert(!fman_pcd_ehash_lookup(&t0, a, 2));
	assert(!pcd.fe_lock.locked);

#ifdef FMAN_TEST_DEBUG_CHAIN_GUARD
	pcd.fe_vm_chain_built = true;
	before = add_calls + del_calls + clear_calls + sync_writes;
	assert(run(&file, "add 0 0102 1") == -ESHUTDOWN);
	assert(run(&file, "del 0102") == -ESHUTDOWN);
	assert(run(&file, "clear") == -ESHUTDOWN);
	assert(before == add_calls + del_calls + clear_calls + sync_writes);
	assert(!pcd.fe_lock.locked);
	pcd.fe_vm_chain_built = false;
#endif

	copy_fails = true;
	assert(run(&file, "clear") == -EFAULT);
	assert(!pcd.fe_lock.locked);
	puts("OK: debugfs FE owners and quarantine are protected");
	return 0;
}
