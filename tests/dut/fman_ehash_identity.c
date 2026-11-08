/* Host fault tests for F-286, using functions extracted from the kernel tree. */
#include <assert.h>
#include <endian.h>
#include <errno.h>
#include <limits.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int64_t s64;
typedef u16 __be16;
typedef u32 __be32;
typedef u64 __be64;

#define FMAN_FE_FLOW_KEY_MAX 56
#define FMAN_EHASH_FLOW_KEY_OFF 8
#define FMAN_PCD_FE_OWNER_MASK 0xffU
#define FMAN_PCD_FE_OWNER_ASK 0U
#define FMAN_PCD_FE_OWNER_FLOWER 1U
#define FMAN_PCD_FE_OWNER_DEBUGFS 4U
#define S64_MAX INT64_MAX
#define EXPORT_SYMBOL_GPL(name)
#define be64_to_cpu(v) be64toh(v)
#define be32_to_cpu(v) be32toh(v)
#define dma_rmb() do { } while (0)
#define pr_info(...) do { } while (0)
#define pr_debug(...) do { } while (0)
#define pr_warn_ratelimited(...) do { } while (0)
#define dev_warn(...) do { } while (0)
#define dev_dbg(...) do { } while (0)

typedef struct { s64 value; pthread_mutex_t lock; } atomic64_t;
#define ATOMIC64_INIT(v) { .value = (v), .lock = PTHREAD_MUTEX_INITIALIZER }
static s64 atomic64_read(atomic64_t *a)
{
	s64 value;

	assert(!pthread_mutex_lock(&a->lock));
	value = a->value;
	assert(!pthread_mutex_unlock(&a->lock));
	return value;
}
static s64 atomic64_cmpxchg(atomic64_t *a, s64 old, s64 new)
{
	s64 value;

	assert(!pthread_mutex_lock(&a->lock));
	value = a->value;
	if (value == old)
		a->value = new;
	assert(!pthread_mutex_unlock(&a->lock));
	return value;
}

struct hlist_node { struct hlist_node *next; };
struct hlist_head { struct hlist_node *first; };
#define container_of(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
#define hlist_for_each_entry(pos, head, member)                            \
	for (pos = (head)->first ?                                         \
	     container_of((head)->first, typeof(*pos), member) : NULL;     \
	     pos;                                                          \
	     pos = pos->member.next ?                                      \
	     container_of(pos->member.next, typeof(*pos), member) : NULL)

struct mutex { bool held; };
static void mutex_lock(struct mutex *lock)
{
	assert(!lock->held);
	lock->held = true;
}
static void mutex_unlock(struct mutex *lock)
{
	assert(lock->held);
	lock->held = false;
}

#include "fman_identity_types.h"

struct fman_pcd_ehash_flow {
	struct hlist_node bnode;
	void *record;
	u8 key_size;
	u8 owner;
	u64 owner_token;
};
struct fman_pcd_ehash_table {
	struct hlist_head bidx[4];
	struct fman_pcd *pcd;
	u8 key_size;
	u32 hash_mask;
	u8 hash_shift;
	bool draining;
	bool poisoned;
};
struct fman_pcd_fe_obj { unsigned long muram_off; };
struct fman_pcd_fe_port { struct fman_pcd_ehash_table *table_v4; };
struct fman_pcd {
	struct mutex fe_lock;
	unsigned long fe_port_armed[1], fe_port_faulted[1];
	struct fman_pcd_fe_port *port;
	struct fman_pcd_ehash_table *table;
	struct { struct fman_pcd_fe_obj *first; } fe_enq;
	unsigned long fe_exit_off;
	struct fman *fman;
};
struct fman { struct fman_pcd *pcd; };

#ifdef FMAN_TEST_LIFECYCLE_LOCK
static bool test_bit(unsigned int bit, const unsigned long *map)
{
	return !!(*map & (1UL << bit));
}
static struct fman_pcd_fe_port *
fman_pcd_fe_port_find(struct fman_pcd *pcd, u8 port)
{
	return pcd->port;
}
#endif

#define list_first_entry_or_null(head, type, member) ((head)->first)
static struct fman_pcd *fman_get_pcd(struct fman *fm) { return fm ? fm->pcd : NULL; }
static struct fman *fman_pcd_get_fman(struct fman_pcd *pcd) { return pcd->fman; }
static struct fman_pcd_ehash_table *
fman_pcd_ehash_table_for_port(struct fman_pcd *pcd, u8 port)
{
	return pcd->table;
}
static struct fman_pcd_ehash_table *
fman_pcd_ehash_table_by_index(struct fman_pcd *pcd, u8 index)
{
	return pcd->table;
}
static unsigned int fman_pcd_ehash_bucket_index(const u8 *key, u8 size,
						u8 shift, u32 mask)
{
	return key[0] & mask;
}
static u32 fman_pcd_resolve_miss_fqid(struct fman_pcd *pcd, u8 port) { return 1; }
static unsigned int sync_writes;
static void fman_set_fpm_extc(struct fman *fm, u32 value)
{
	assert(fm && value == 0x80000000U);
	sync_writes++;
}
static u32 fman_get_fpm_extc(struct fman *fm) { return 0; }
static void udelay(unsigned int n) { }
static unsigned int ehash_insert_sync_timeouts;	/* F-288 */

static int fman_pcd_ehash_recover(struct fman_pcd_ehash_table *t);
static int fman_pcd_ehash_del_key(struct fman_pcd_ehash_table *t,
				  const u8 *key, u8 key_size);
static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,
				  const u8 *key, u8 key_size,
				  u32 enq_off, u32 fqid, bool stats,
				  const u8 *dst, const u8 *src, u16 eth_type,
				  const struct fman_pcd_nat_params *nat,
				  const struct fman_pcd_vlan_params *vlan,
				  u8 owner, u64 owner_token);

#include "fman_identity_body.h"

static int recover_rc, recover_calls, delete_calls, add_rc, add_calls;
static _Alignas(8) u8 record[320];
static struct fman_pcd_ehash_flow live;

static int fman_pcd_ehash_recover(struct fman_pcd_ehash_table *t)
{
	if (!t->poisoned)
		return 0;
	recover_calls++;
	if (!recover_rc)
		t->poisoned = false;
	return recover_rc;
}
static int fman_pcd_ehash_del_key(struct fman_pcd_ehash_table *t,
				  const u8 *key, u8 key_size)
{
	struct fman_pcd_ehash_flow *f;
	unsigned int b;

	delete_calls++;
	if (!key || key_size != t->key_size)
		return -EINVAL;
	f = fman_pcd_ehash_lookup(t, key, key_size);
	if (!f)
		return -ENOENT;
	b = fman_pcd_ehash_bucket_index(key, key_size, t->hash_shift, t->hash_mask);
	assert(t->bidx[b].first == &f->bnode);
	t->bidx[b].first = f->bnode.next;
	return 0;
}
static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,
				  const u8 *key, u8 key_size,
				  u32 enq_off, u32 fqid, bool stats,
				  const u8 *dst, const u8 *src, u16 eth_type,
				  const struct fman_pcd_nat_params *nat,
				  const struct fman_pcd_vlan_params *vlan,
				  u8 owner, u64 owner_token)
{
	unsigned int b = fman_pcd_ehash_bucket_index(key, key_size,
						    t->hash_shift, t->hash_mask);
	int f269_err;

	add_calls++;
#include "fman_ehash_add_guard.h"
	if (add_rc)
		return add_rc;
	assert(key_size == t->key_size);
	memset(record, 0, sizeof(record));
	memcpy(record + FMAN_EHASH_FLOW_KEY_OFF, key, key_size);
	live.record = record;
	live.key_size = key_size;
	live.owner = owner;
	live.owner_token = owner_token;
	live.bnode.next = NULL;
	t->bidx[b].first = &live.bnode;
	return 0;
}

#define THREADS 6
#define TOKENS_PER_THREAD 500
static u64 tokens[THREADS][TOKENS_PER_THREAD];
static void *allocate_tokens(void *arg)
{
	u64 *row = arg;
	int i;

	for (i = 0; i < TOKENS_PER_THREAD; i++)
		row[i] = fman_pcd_fe_alloc_token();
	return NULL;
}

static void test_token_sequence(void)
{
	pthread_t threads[THREADS];
	bool seen[THREADS * TOKENS_PER_THREAD + 1] = { false };
	int i, j;

	for (i = 0; i < THREADS; i++)
		assert(!pthread_create(&threads[i], NULL, allocate_tokens, tokens[i]));
	for (i = 0; i < THREADS; i++)
		assert(!pthread_join(threads[i], NULL));
	for (i = 0; i < THREADS; i++) {
		for (j = 0; j < TOKENS_PER_THREAD; j++) {
			u64 token = tokens[i][j];

			assert(token && token <= THREADS * TOKENS_PER_THREAD);
			assert(!seen[token]);
			seen[token] = true;
		}
	}
	fman_pcd_fe_token_seq.value = S64_MAX - 1;
	assert(fman_pcd_fe_alloc_token() == S64_MAX);
	assert(fman_pcd_fe_alloc_token() == 0);
	assert(fman_pcd_fe_alloc_token() == 0);
}

static void test_owner_identity(void)
{
	struct fman_pcd_ehash_table table = { .key_size = 2, .hash_mask = 3 };
	struct fman_pcd_fe_obj enq = { .muram_off = 0x100 };
	struct fman_pcd pcd = { .table = &table, .fe_enq.first = &enq };
	struct fman fm = { .pcd = &pcd };
	struct fman_pcd_fe_flow_action a = {
		.key = { 1, 7 }, .key_size = 2, .owner_token = 100,
	};
	u64 packets = 999, bytes = 999;
	u32 ts = 999;
	int before;

	table.pcd = &pcd;
	pcd.fman = &fm;

#ifdef FMAN_TEST_LIFECYCLE_LOCK
	struct fman_pcd_fe_port port = { .table_v4 = &table };

	assert(fman_pcd_fe_flow_add(&fm, 0x28, &a) == -EINVAL);
	assert(fman_pcd_fe_flow_add(&fm, 0x10, &a) == -ENODEV);
	pcd.fe_port_armed[0] = 1UL << 0x10;
	assert(fman_pcd_fe_flow_add(&fm, 0x10, &a) == -ENODEV);
	pcd.port = &port;
	port.table_v4 = NULL;
	assert(fman_pcd_fe_flow_add(&fm, 0x10, &a) == -ENODEV);
	port.table_v4 = &table;
	pcd.fe_port_faulted[0] = 1UL << 0x10;
	assert(fman_pcd_fe_flow_add(&fm, 0x10, &a) == -ESHUTDOWN);
	pcd.fe_port_faulted[0] = 0;
	assert(!add_calls && !pcd.fe_lock.held);
#endif

	a.flags = FMAN_PCD_FE_OWNER_DEBUGFS;
	assert(fman_pcd_fe_flow_add(&fm, 0x10, &a) == -EINVAL);
	assert(!add_calls);
	a.flags = FMAN_PCD_FE_OWNER_ASK;
	table.pcd = NULL;
	assert(fman_pcd_fe_flow_add(&fm, 0x10, &a) == -ENODEV);
	table.pcd = &pcd;
	pcd.fman = NULL;
	assert(fman_pcd_fe_flow_add(&fm, 0x10, &a) == -ENODEV);
	assert(!table.bidx[1].first && !sync_writes && !pcd.fe_lock.held);
	pcd.fman = &fm;
	assert(!fman_pcd_fe_flow_add(&fm, 0x10, &a));
	assert(sync_writes == 1);
	assert(live.owner == FMAN_PCD_FE_OWNER_ASK && live.owner_token == 100);
	live.owner = FMAN_PCD_FE_OWNER_DEBUGFS;
	a.owner_token = 200;
	assert(fman_pcd_fe_flow_add(&fm, 0x10, &a) == -EEXIST);
	assert(live.owner == FMAN_PCD_FE_OWNER_DEBUGFS);
	live.owner = FMAN_PCD_FE_OWNER_ASK;
	a.owner_token = 200;
	assert(!fman_pcd_fe_flow_add(&fm, 0x10, &a));
	assert(live.owner_token == 200);

	/* A delayed older owner and legacy or foreign owners cannot replace it. */
	before = add_calls;
	a.owner_token = 100;
	assert(fman_pcd_fe_flow_add(&fm, 0x10, &a) == -ESTALE);
	a.owner_token = 0;
	assert(fman_pcd_fe_flow_add(&fm, 0x10, &a) == -EEXIST);
	a.owner_token = 300;
	a.flags = FMAN_PCD_FE_OWNER_FLOWER;
	assert(fman_pcd_fe_flow_add(&fm, 0x10, &a) == -EEXIST);
	assert(add_calls == before && live.owner_token == 200);

	/* A failed add before eviction must not retag the existing owner. */
	a.flags = FMAN_PCD_FE_OWNER_ASK;
	add_rc = -ENOMEM;
	assert(fman_pcd_fe_flow_add(&fm, 0x10, &a) == -ENOMEM);
	assert(live.owner == FMAN_PCD_FE_OWNER_ASK && live.owner_token == 200);
	add_rc = 0;

	*(u64 *)(record + 256) = htobe64(123);
	*(u64 *)(record + 264) = htobe64(456);
	*(u32 *)(record + 272) = htobe32(7);
	assert(fman_pcd_fe_flow_get_stats_identified(&fm, 0x10, a.key, 2,
			FMAN_PCD_FE_OWNER_ASK, 100, &packets, &bytes, &ts) == -ESTALE);
	assert(packets == 999 && bytes == 999 && ts == 999);
	assert(fman_pcd_fe_flow_get_stats_identified(&fm, 0x10, a.key, 2,
			FMAN_PCD_FE_OWNER_FLOWER, 200, &packets, &bytes, &ts) == -ESTALE);
	assert(fman_pcd_fe_flow_get_stats_identified(&fm, 0x10, a.key, 2,
			FMAN_PCD_FE_OWNER_ASK, 0, &packets, &bytes, &ts) == -EINVAL);
	assert(!fman_pcd_fe_flow_get_stats_identified(&fm, 0x10, a.key, 2,
			FMAN_PCD_FE_OWNER_ASK, 200, &packets, &bytes, &ts));
	assert(packets == 123 && bytes == 456 && ts == 7);
	assert(!fman_pcd_fe_flow_get_stats(&fm, 0x10, a.key, 2,
					   &packets, &bytes, &ts));

	before = delete_calls;
	assert(fman_pcd_fe_flow_del_identified(&fm, 0x10, a.key, 2,
					       FMAN_PCD_FE_OWNER_ASK, 100) == -ESTALE);
	assert(fman_pcd_fe_flow_del_owner(&fm, 0x10, a.key, 2,
					  FMAN_PCD_FE_OWNER_ASK) == -EBUSY);
	assert(fman_pcd_fe_flow_del_identified(&fm, 0x10, a.key, 2,
					       FMAN_PCD_FE_OWNER_ASK, 0) == -EINVAL);
	assert(fman_pcd_fe_flow_del_identified(&fm, 0x10, NULL, 0,
					       FMAN_PCD_FE_OWNER_ASK, 200) == -EINVAL);
	assert(fman_pcd_fe_flow_del_owner(&fm, 0x10, NULL, 0,
					  FMAN_PCD_FE_OWNER_ASK) == -EOPNOTSUPP);
	assert(fman_pcd_fe_flow_del_owner(&fm, 0x10, NULL, 0,
					  FMAN_PCD_FE_OWNER_FLOWER) == -EOPNOTSUPP);
	assert(delete_calls == before && live.owner_token == 200);

	/* Owner mismatch is not settled until a poisoned table recovers. */
	table.poisoned = true;
	recover_rc = -ETIMEDOUT;
	assert(fman_pcd_fe_flow_del_identified(&fm, 0x10, a.key, 2,
					       FMAN_PCD_FE_OWNER_ASK, 100) == -ETIMEDOUT);
	assert(recover_calls == 1 && delete_calls == before);
	table.draining = true;
	assert(fman_pcd_fe_flow_del_identified(&fm, 0x10, a.key, 2,
					       FMAN_PCD_FE_OWNER_ASK, 100) == -ESHUTDOWN);
	assert(recover_calls == 1);
	table.draining = false;
	recover_rc = 0;
	assert(fman_pcd_fe_flow_del_identified(&fm, 0x10, a.key, 2,
					       FMAN_PCD_FE_OWNER_ASK, 100) == -ESTALE);
	assert(recover_calls == 2 && !table.poisoned && delete_calls == before);
	assert(!fman_pcd_fe_flow_del_identified(&fm, 0x10, a.key, 2,
						FMAN_PCD_FE_OWNER_ASK, 200));
	assert(!fman_pcd_fe_flow_del_identified(&fm, 0x10, a.key, 2,
						FMAN_PCD_FE_OWNER_ASK, 100));
	assert(!pcd.fe_lock.held);
}

int main(void)
{
	test_token_sequence();
	test_owner_identity();
	puts("OK: F-286 identity, recovery, stats and token exhaustion");
	return 0;
}
