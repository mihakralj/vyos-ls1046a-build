#!/usr/bin/env python3
"""Run the patched ehash insert/delete code against a model of the FMan walker.

Usage: python3 bin/test-ehash-delete.py <kernel-source-dir>
The tree must already carry the F-254/F-256, F-264, F-267, F-269, F-271,
F-282 through F-286 fixups; ci-setup-kernel.sh runs this after their
integration. Keys select one of two
buckets by their second byte, so one bucket fills while the other must stay
reachable.

The kernel source is used verbatim: bucket publish, SYNC, drain, del_key and
add_key()'s publish tail. Only allocation, list and hardware interfaces are
stubbed. After every bucket-head store a model of the microcode 210.10.1
lookup (cumulative nodes, decomp/en-exthash-lookup.asm) walks every bucket.
Present keys must hit, deleted keys must miss, and a pool block may be freed
only after a SYNC that completed after the head store that unlinked it.
Fault cases cover timeout quarantine, mutation exclusion, recovery and
fail-closed drain. The harness models the documented SYNC contract; it is
not hardware lifecycle validation.
"""

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


HARNESS = r"""
#include <assert.h>
#include <endian.h>
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
typedef u64 dma_addr_t;

#define FMAN_EHASH_FLOW_KEY_OFF 8
#define FMAN_EHASH_FLOW_REC_SIZE 320
#define FMAN_FE_CTX_SIZE 16
#define FMAN_EHASH_FLOW_BLK_SIZE 512
#define FMAN_EHASH_BUCKET_SIZE 16
#define FMAN_EHASH_CU_SIZE 256
#define FMAN_EHASH_CU_NODE 0x80
#define FMAN_EHASH_CU_NEXT 0x20
#define FMAN_EHASH_CU_KEYS_OFF 12
#define FMAN_EHASH_CU_PTR_SIZE 8
#define KEY_MAX 56
#define BUCKETS 2
#define INV0 0x80000000U
#define GFP_KERNEL 0
#define READ_ONCE(x) (x)
#define swab64(x) __builtin_bswap64(x)
#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
#define min(a, b) ((a) < (b) ? (a) : (b))
#define struct_size(p, member, n) \
	(sizeof(*(p)) + (size_t)(n) * sizeof((p)->member[0]))
#define WRITE_ONCE(x, value) head_store(&(x), (value))

static u64 get_be64(const u8 *p) { u64 v; memcpy(&v, p, 8); return be64toh(v); }
static void put_unaligned_be64(u64 v, u8 *p) { v = htobe64(v); memcpy(p, &v, 8); }

struct list_head { struct list_head *next, *prev; };
#define list_entry(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))
static void INIT_LIST_HEAD(struct list_head *head)
{
	head->next = head;
	head->prev = head;
}
static void list_add_tail(struct list_head *node, struct list_head *head)
{
	node->next = head;
	node->prev = head->prev;
	head->prev->next = node;
	head->prev = node;
}
#define list_for_each_entry_safe(pos, tmp, head, member) \
	for ((pos) = (head)->next == (head) ? NULL : \
	     list_entry((head)->next, __typeof__(*(pos)), member); \
	     (pos) && ((tmp) = (pos)->member.next == (head) ? NULL : \
	     list_entry((pos)->member.next, __typeof__(*(pos)), member), 1); \
	     (pos) = (tmp))
static void list_del(struct list_head *node)
{
	node->prev->next = node->next;
	node->next->prev = node->prev;
}

struct hlist_node { struct hlist_node *next, **pprev; };
struct hlist_head { struct hlist_node *first; };
#define hlist_entry_safe(ptr, type, member) \
	((ptr) ? list_entry(ptr, type, member) : NULL)
#define hlist_for_each_entry(pos, head, member) \
	for ((pos) = hlist_entry_safe((head)->first, __typeof__(*(pos)), member); \
	     (pos); \
	     (pos) = hlist_entry_safe((pos)->member.next, __typeof__(*(pos)), member))
static void hlist_add_head(struct hlist_node *n, struct hlist_head *h)
{
	n->next = h->first;
	if (h->first)
		h->first->pprev = &n->next;
	h->first = n;
	n->pprev = &h->first;
}
static void hlist_del(struct hlist_node *n)
{
	*n->pprev = n->next;
	if (n->next)
		n->next->pprev = n->pprev;
	n->next = NULL;
	n->pprev = NULL;
}

struct fman { bool timeout; unsigned int polls; unsigned int fail_n; bool pending_fail; };
struct fman_pcd { struct fman *fman; };
struct fman_pcd_ehash_flow {
	struct list_head node;
	struct hlist_node bnode;
	void *record;
	dma_addr_t record_dma;
	void *ctx;
	dma_addr_t ctx_dma;
	u64 *bucket_h;
	u16 index;
	u8 key_size;
};
struct fman_pcd_ehash_cu {
	struct list_head quarantine_node;
	unsigned int n;
	struct {
		void *va;
		dma_addr_t dma;
	} node[];
};
struct fman_pcd_ehash_table {
	void *table_base;
	struct fman_pcd *pcd;
	u32 hash_mask;
	u8 key_size;
	u8 hash_shift;
	struct list_head flows;
	struct hlist_head *bidx;
	void *rec_pool;
	void *cu_pool;
	struct fman_pcd_ehash_cu **cu;
	struct list_head quarantined_cu;
	struct list_head quarantined_flows;
	bool poisoned;
	bool draining;
};

/* Every pool block the code under test owns, with the last head store
 * after which the FMan walker could reach it. */
struct block {
	void *va;
	bool node;
	bool live;
	bool uncertain;
	unsigned int seen;
};
static struct block blocks[256];
static unsigned int nblocks;
static unsigned int head_writes, sync_mark, syncs, reads, warnings;
static unsigned int uncertain_frees;
static unsigned int ehash_sync_timeouts, ehash_sync_recovered, ehash_sync_wedged;
static unsigned int ehash_sync_inject, ehash_sync_injected;
static unsigned int ehash_poison_clears;
static bool ehash_sync_diag;
static unsigned int ehash_sync_diag_attempts, ehash_sync_diag_completed;
static unsigned int ehash_sync_diag_pending, ehash_sync_diag_max_polls;
static unsigned int ehash_sync_diag_last_extc, ehash_sync_diag_last_pending_extc;
static unsigned long long ehash_sync_diag_max_ns;
static unsigned long long ehash_sync_diag_lt_1us, ehash_sync_diag_lt_10us;
static unsigned long long ehash_sync_diag_lt_100us, ehash_sync_diag_lt_1ms;
static unsigned long long ehash_sync_diag_lt_10ms, ehash_sync_diag_lt_100ms;
static unsigned long long ehash_sync_diag_ge_100ms;
static u64 fake_ktime_ns;
#define FMAN_EHASH_SYNC_TRIES 3
static unsigned int metadata_freed, fail_in;
static bool wmb_pending, barrier;
static struct fman_pcd_ehash_table *cur;
static bool hc_sync_enabled;
static int hc_sync_result;
static unsigned int hc_sync_calls;

static int fman_pcd_hc_ehash_sync(struct fman_pcd *pcd)
{
	(void)pcd;
	if (!hc_sync_enabled)
		return 0;
	hc_sync_calls++;
	return hc_sync_result;
}

static struct block *block_of(const void *va)
{
	for (unsigned int i = 0; i < nblocks; i++)
		if (blocks[i].va == va && blocks[i].live)
			return &blocks[i];
	return NULL;
}

/* Microcode 210.10.1 lookup (decomp/en-exthash-lookup.asm): returns the
 * record that @key hits from bucket @b, or NULL. When @stamp is set, marks
 * every block the walk can touch. */
static const u8 *walk(struct fman_pcd_ehash_table *t, unsigned int b,
		      const u8 *key, bool stamp)
{
	u64 h = ((u64 *)t->table_base)[b * 2];
	const u8 *p, *hit = NULL;

	if (!h)
		return NULL;
	p = (const u8 *)(uintptr_t)swab64(h);
	for (;;) {
		struct block *blk = block_of(p);

		assert(blk && !((uintptr_t)p & 0xff));
		if (stamp)
			blk->seen = head_writes;
		if (!(p[0] & FMAN_EHASH_CU_NODE)) {
			assert(!blk->node);
			if (key && !hit &&
			    !memcmp(p + FMAN_EHASH_FLOW_KEY_OFF, key, t->key_size))
				hit = p;
			return hit;
		}
		assert(blk->node && !(p[0] & 0x40));
		assert(p[1] >= 1 && p[2] >= t->key_size);
		assert(p[3] == FMAN_EHASH_CU_KEYS_OFF + 1 + p[1] * p[2]);
		assert(p[3] + p[1] * FMAN_EHASH_CU_PTR_SIZE <= FMAN_EHASH_CU_SIZE);
		assert(p[FMAN_EHASH_CU_KEYS_OFF + p[1] * p[2]] == 0);
		for (unsigned int i = 0; i < p[1]; i++) {
			const u8 *r = (const u8 *)(uintptr_t)
				get_be64(p + p[3] + i * FMAN_EHASH_CU_PTR_SIZE);
			struct block *rb = block_of(r);

			assert(rb && !rb->node && !((uintptr_t)r & 0xff));
			assert(!memcmp(p + FMAN_EHASH_CU_KEYS_OFF + i * p[2],
				       r + FMAN_EHASH_FLOW_KEY_OFF, t->key_size));
			if (stamp)
				rb->seen = head_writes;
			if (key && !hit &&
			    !memcmp(p + FMAN_EHASH_CU_KEYS_OFF + i * p[2], key,
				    t->key_size))
				hit = r;
		}
		if (!(p[0] & FMAN_EHASH_CU_NEXT))
			return hit;
		p = (const u8 *)(uintptr_t)get_be64(p + 4);
	}
}

static void head_store(u64 *head, u64 value)
{
	assert(!value || wmb_pending);
	wmb_pending = false;
	*head = value;
	head_writes++;
	for (unsigned int b = 0; b < BUCKETS; b++)
		walk(cur, b, NULL, true);
}

static void wmb(void) { wmb_pending = true; }
static void dma_wmb(void) { barrier = true; }
static void udelay(unsigned int us)
{
	assert(us == 1);
	fake_ktime_ns += (u64)us * 1000;
}
static u64 ktime_get_ns(void) { return fake_ktime_ns; }
static struct fman *fman_pcd_get_fman(struct fman_pcd *pcd)
{
	return pcd->fman;
}
static void fman_set_fpm_extc(struct fman *fm, u32 value)
{
	assert(barrier && value == INV0);
	barrier = false;
	syncs++;
	fm->pending_fail = fm->fail_n && fm->fail_n--;
}
static u32 fman_get_fpm_extc(struct fman *fm)
{
	reads++;
	if (fm->timeout || fm->pending_fail)
		return INV0;
	if (fm->polls) {
		fm->polls--;
		return INV0;
	}
	sync_mark = head_writes;
	return 0;
}
static void record_warning(const char *format, ...)
{
	(void)format;
	warnings++;
}
#define pr_warn(...) record_warning(__VA_ARGS__)
#define pr_warn_ratelimited(...) record_warning(__VA_ARGS__)

static bool inject(void)
{
	return fail_in && --fail_in == 0;
}
static void *kzalloc(size_t size, int gfp)
{
	(void)gfp;
	return inject() ? NULL : calloc(1, size);
}
static void kfree(void *ptr) { if (ptr) metadata_freed++; free(ptr); }
static void *dma_pool_zalloc(void *pool, int gfp, dma_addr_t *dma)
{
	bool node = pool == cur->cu_pool;
	size_t size = node ? FMAN_EHASH_CU_SIZE : FMAN_EHASH_FLOW_BLK_SIZE;
	void *va;

	(void)gfp;
	assert(pool == cur->cu_pool || pool == cur->rec_pool);
	if (node && inject())
		return NULL;
	va = aligned_alloc(size, size);
	assert(va && nblocks < 256);
	memset(va, 0, size);
	blocks[nblocks++] = (struct block){ .va = va, .node = node, .live = true };
	*dma = (uintptr_t)va;
	return va;
}
/* Every retirement requires a successful post-unlink SYNC. */
static void dma_pool_free(void *pool, void *va, dma_addr_t dma)
{
	struct block *blk = block_of(va);

	assert(blk && dma == (uintptr_t)va);
	assert(blk->node == (pool == cur->cu_pool));
	assert(blk->node || pool == cur->rec_pool);
	if (blk->uncertain)
		uncertain_frees++;
	assert(blk->seen == 0 || sync_mark > blk->seen);
	blk->live = false;
	free(va);
}
static u16 fman_pcd_ehash_bucket_index(const u8 *key, u8 key_size,
				       u8 hash_shift, u32 mask)
{
	(void)key_size; (void)hash_shift;
	return key[1] & mask;
}

@KERNEL_FUNCTIONS@

/* add_key()'s F-269 publish tail runs verbatim; the record and opcode
 * writes before it are reduced to the key. */
static int add_tail(struct fman_pcd_ehash_table *t,
		    struct fman_pcd_ehash_flow *flow, u8 *r, dma_addr_t rdma,
		    u16 index, u8 key_size)
{
	struct fman_pcd_ehash_cu *old_cu = NULL;
	int f269_err;

@ADD_TAIL@
	return 0;
}

static u64 heads[BUCKETS * 2] __attribute__((aligned(16)));
static struct fman_pcd_ehash_cu *cus[BUCKETS];
static struct hlist_head bidx[BUCKETS];
static unsigned int pool_tags[2];

static void setup(struct fman_pcd_ehash_table *t, struct fman_pcd *pcd,
		  u8 key_size)
{
	for (unsigned int i = 0; i < nblocks; i++)
		assert(!blocks[i].live);
	memset(heads, 0, sizeof(heads));
	memset(cus, 0, sizeof(cus));
	memset(bidx, 0, sizeof(bidx));
	*t = (struct fman_pcd_ehash_table){ .table_base = heads, .pcd = pcd,
		.hash_mask = BUCKETS - 1, .key_size = key_size, .bidx = bidx,
		.rec_pool = &pool_tags[0], .cu_pool = &pool_tags[1], .cu = cus };
	t->flows.next = t->flows.prev = &t->flows;
	cur = t;
	INIT_LIST_HEAD(&t->flows);
	INIT_LIST_HEAD(&t->quarantined_cu);
	INIT_LIST_HEAD(&t->quarantined_flows);
	nblocks = head_writes = sync_mark = syncs = reads = warnings = 0;
	metadata_freed = fail_in = 0;
	uncertain_frees = 0;
	wmb_pending = barrier = false;
}

static void make_key(u8 *key, unsigned int id, unsigned int bucket)
{
	memset(key, 0, KEY_MAX);
	key[0] = id;
	key[1] = bucket;
	key[2] = 0xa5;
}

static int add(struct fman_pcd_ehash_table *t, unsigned int id,
	       unsigned int bucket)
{
	struct fman_pcd_ehash_flow *flow;
	dma_addr_t rdma;
	u8 *r;
	u8 key[KEY_MAX];
	int err, f269_err;

@ADD_GUARD@
	flow = calloc(1, sizeof(*flow));
	assert(flow);
	r = dma_pool_zalloc(t->rec_pool, GFP_KERNEL, &rdma);
	assert(r);
	make_key(key, id, bucket);
	memcpy(r + FMAN_EHASH_FLOW_KEY_OFF, key, t->key_size);
	flow->bucket_h = (u64 *)((u8 *)t->table_base +
				 (size_t)bucket * FMAN_EHASH_BUCKET_SIZE);
	err = add_tail(t, flow, r, rdma, bucket, t->key_size);
	if (err)
		return err;
	flow->node.next = t->flows.next;
	flow->node.prev = &t->flows;
	t->flows.next->prev = &flow->node;
	t->flows.next = &flow->node;
	return 0;
}

static int del(struct fman_pcd_ehash_table *t, unsigned int id,
	       unsigned int bucket)
{
	u8 key[KEY_MAX];

	make_key(key, id, bucket);
	return fman_pcd_ehash_del_key(t, key, t->key_size);
}

static bool hits(struct fman_pcd_ehash_table *t, unsigned int id,
		 unsigned int bucket)
{
	u8 key[KEY_MAX];
	const u8 *r;

	make_key(key, id, bucket);
	r = walk(t, bucket, key, false);
	assert(!r || !memcmp(r + FMAN_EHASH_FLOW_KEY_OFF, key, t->key_size));
	return r != NULL;
}

static unsigned int indexed(unsigned int bucket)
{
	unsigned int n = 0;

	for (struct hlist_node *p = bidx[bucket].first; p; p = p->next)
		n++;
	return n;
}

static unsigned int live(bool node)
{
	unsigned int n = 0;

	for (unsigned int i = 0; i < nblocks; i++)
		n += blocks[i].live && blocks[i].node == node;
	return n;
}

/* Snapshot the current published generation as if a walker could still be
 * using it after a later head replacement. This is a test oracle, not a
 * claim that silicon retains the pointer or that INV0 is a DDR barrier. */
static void mark_current_generation_uncertain(struct fman_pcd_ehash_table *t,
					      unsigned int bucket)
{
	(void)walk(t, bucket, NULL, true);
	for (unsigned int i = 0; i < nblocks; i++)
		if (blocks[i].live && blocks[i].seen == head_writes)
			blocks[i].uncertain = true;
}

static int drain(struct fman_pcd_ehash_table *t)
{
	int err = fman_pcd_ehash_flow_drain(t);

	if (err)
		return err;
	assert(!heads[0] && !heads[2] && !cus[0] && !cus[1]);
	assert(t->flows.next == &t->flows && !bidx[0].first && !bidx[1].first);
	assert(t->quarantined_cu.next == &t->quarantined_cu);
	assert(t->quarantined_flows.next == &t->quarantined_flows);
	assert(live(false) == 0 && live(true) == 0);
	return 0;
}

/* Fill one bucket, delete in @order, and check every step against the
 * walker: present keys hit, deleted keys miss, nodes match the count. */
static void test_bucket(u8 key_size, unsigned int n, const unsigned int *order)
{
	struct fman fm = { .polls = 1 };
	struct fman_pcd pcd = { .fman = &fm };
	struct fman_pcd_ehash_table t;
	unsigned int per, present = n;
	bool gone[32] = { false };

	setup(&t, &pcd, key_size);
	per = 243 / (fman_pcd_ehash_cu_stride(key_size) + 8);
	add(&t, 99, 1);		/* other bucket stays untouched */
	for (unsigned int i = 0; i < n; i++) {
		assert(add(&t, i, 0) == 0);
		for (unsigned int j = 0; j <= i; j++)
			assert(hits(&t, j, 0));
		assert(live(true) == (i ? DIV_ROUND_UP(i + 1, per) : 0));
		assert(hits(&t, 99, 1));
	}
	for (unsigned int s = 0; s < n; s++) {
		unsigned int id = order[s];
		unsigned int before = syncs;

		assert(del(&t, id, 0) == 0);
		assert(syncs == before + 1 && warnings == 0);
		gone[id] = true;
		present--;
		assert(indexed(0) == present);
		for (unsigned int j = 0; j < n; j++)
			assert(hits(&t, j, 0) == !gone[j]);
		assert(live(true) == (present > 1 ? DIV_ROUND_UP(present, per) : 0));
		assert(live(false) == present + 1);
		assert(hits(&t, 99, 1));
		assert(del(&t, id, 0) == -ENOENT);
	}
	assert(!heads[0] && !cus[0]);
	assert(drain(&t) == 0);
}

static void test_insert_enomem(void)
{
	struct fman fm = { 0 };
	struct fman_pcd pcd = { .fman = &fm };
	struct fman_pcd_ehash_table t;
	u64 head;

	setup(&t, &pcd, 50);
	for (unsigned int i = 0; i < 3; i++)
		assert(add(&t, i, 0) == 0);
	head = heads[0];
	for (unsigned int f = 1; f <= 3; f++) {	/* cu, node 0, node 1 */
		fail_in = f;
		assert(add(&t, 7, 0) == -ENOMEM);
		assert(heads[0] == head && indexed(0) == 3 && !hits(&t, 7, 0));
		for (unsigned int i = 0; i < 3; i++)
			assert(hits(&t, i, 0));
		assert(live(false) == 3 && live(true) == 1);
	}
	assert(drain(&t) == 0);
}

static void test_delete_enomem(void)
{
	struct fman fm = { 0 };
	struct fman_pcd pcd = { .fman = &fm };
	struct fman_pcd_ehash_table t;

	setup(&t, &pcd, 50);
	for (unsigned int i = 0; i < 4; i++)
		assert(add(&t, i, 0) == 0);
	fail_in = 1;
	assert(del(&t, 0, 0) == 0);
	assert(warnings == 1 && !heads[0] && !cus[0] && indexed(0) == 3);
	assert(live(true) == 0 && live(false) == 3);
	for (unsigned int i = 1; i < 4; i++)
		assert(!hits(&t, i, 0));	/* software until the next rebuild */
	assert(add(&t, 4, 0) == 0);
	for (unsigned int i = 1; i < 5; i++)
		assert(hits(&t, i, 0));
	assert(drain(&t) == 0);
}

static void test_delete_timeout(void)
{
	struct fman fm = { 0 };
	struct fman_pcd pcd = { .fman = &fm };
	struct fman_pcd_ehash_table t;
	struct block *rec, *node;
	unsigned int freed, synced;

	/* rec0 0, rec1 1, node A 2, rec2 3, node B 4 (A synced and freed) */
	setup(&t, &pcd, 50);
	for (unsigned int i = 0; i < 3; i++)
		assert(add(&t, i, 0) == 0);
	assert(syncs == 1 && warnings == 0);
	mark_current_generation_uncertain(&t, 0);
	fm.timeout = true;
	freed = metadata_freed;
	synced = syncs;
	reads = 0;
	assert(del(&t, 1, 0) == -ETIMEDOUT);	/* unlinked, quarantined */
	assert(syncs == synced + 3 && reads == 300000 && warnings == 3);
	assert(ehash_sync_wedged == 1 && ehash_sync_timeouts == 3 && ehash_sync_recovered == 0);
	assert(metadata_freed == freed);
	assert(!hits(&t, 1, 0) && hits(&t, 0, 0) && hits(&t, 2, 0));
	assert(live(false) == 3 && live(true) == 2);
	rec = &blocks[1];
	node = &blocks[4];
	assert(rec->live && !rec->node && node->live && node->node);
	assert(rec->va && ((u8 *)rec->va)[FMAN_EHASH_FLOW_KEY_OFF] == 1);
	assert(del(&t, 0, 0) == -ETIMEDOUT);
	assert(drain(&t) == -ETIMEDOUT);
	assert(rec->live && node->live && uncertain_frees == 0);
	fm.timeout = false;
	assert(drain(&t) == 0);
	assert(uncertain_frees > 0);
}

static void test_hc_sync_failure_quarantine(void)
{
	struct fman fm = { 0 };
	struct fman_pcd pcd = { .fman = &fm };
	struct fman_pcd_ehash_table t;
	unsigned int freed, calls, uncertain;

	setup(&t, &pcd, 50);
	for (unsigned int i = 0; i < 3; i++)
		assert(add(&t, i, 0) == 0);

	hc_sync_calls = 0;
	hc_sync_enabled = true;
	hc_sync_result = -ETIMEDOUT;
	freed = metadata_freed;
	uncertain = uncertain_frees;
	assert(del(&t, 1, 0) == -ETIMEDOUT);
	assert(hc_sync_calls == 1 && metadata_freed == freed);
	assert(indexed(0) == 2 && t.poisoned && uncertain_frees == uncertain);
	assert(!hits(&t, 1, 0) && hits(&t, 0, 0) && hits(&t, 2, 0));

	hc_sync_result = 0;
	calls = hc_sync_calls;
	assert(drain(&t) == 0);
	assert(hc_sync_calls > calls && metadata_freed > freed);
	assert(live(false) == 0 && live(true) == 0);
	hc_sync_enabled = false;
}

static void test_hc_sync_error_propagation(void)
{
	static const int errors[] = { -EBUSY, -EIO, -ENODEV, -ETIMEDOUT };

	for (unsigned int i = 0; i < sizeof(errors) / sizeof(errors[0]); i++) {
		struct fman fm = { 0 };
		struct fman_pcd pcd = { .fman = &fm };
		struct fman_pcd_ehash_table t;
		unsigned int freed;

		setup(&t, &pcd, 50);
		for (unsigned int k = 0; k < 3; k++)
			assert(add(&t, k, 0) == 0);
		mark_current_generation_uncertain(&t, 0);
		hc_sync_enabled = true;
		hc_sync_result = errors[i];
		freed = metadata_freed;

		/* A post-unlink barrier failure retains the old generation. */
		assert(del(&t, 1, 0) == errors[i]);
		assert(t.poisoned && indexed(0) == 2);
		assert(metadata_freed == freed && uncertain_frees == 0);
		assert(!hits(&t, 1, 0) && hits(&t, 0, 0) && hits(&t, 2, 0));

		/* Recovery failure is pre-unlink and blocks all mutations. */
		assert(del(&t, 0, 0) == errors[i]);
		assert(add(&t, 3, 0) == errors[i]);
		assert(indexed(0) == 2 && hits(&t, 0, 0) && !hits(&t, 3, 0));
		assert(metadata_freed == freed && uncertain_frees == 0);

		/* Drain clears heads but cannot reclaim on a barrier error. */
		assert(drain(&t) == errors[i]);
		assert(t.draining && !heads[0] && indexed(0) == 2);
		assert(metadata_freed == freed && uncertain_frees == 0);
		hc_sync_result = 0;
		assert(drain(&t) == 0);
		assert(uncertain_frees > 0);
		hc_sync_enabled = false;
	}
}

static void test_missing_sync_owner_blocks_insert(void)
{
	for (unsigned int mode = 0; mode < 4; mode++) {
		struct fman fm = { 0 };
		struct fman_pcd pcd = { .fman = &fm };
		struct fman_pcd_ehash_table t;
		unsigned int calls = hc_sync_calls;

		setup(&t, &pcd, 50);
		hc_sync_enabled = mode & 2;
		hc_sync_result = 0;
		if (mode & 1)
			t.pcd = NULL;
		else
			pcd.fman = NULL;

		assert(add(&t, 0, 0) == -ENODEV);
		assert(!heads[0] && !indexed(0) && !nblocks);
		assert(!syncs && hc_sync_calls == calls);
		t.pcd = &pcd;
		pcd.fman = &fm;
		hc_sync_enabled = false;
		assert(drain(&t) == 0);
	}
}

static void test_missing_sync_owner_retains_generations(void)
{
	for (unsigned int mode = 0; mode < 4; mode++) {
		struct fman fm = { 0 };
		struct fman_pcd pcd = { .fman = &fm };
		struct fman_pcd_ehash_table t;
		unsigned int freed, writes, calls;

		setup(&t, &pcd, 50);
		for (unsigned int i = 0; i < 3; i++)
			assert(add(&t, i, 0) == 0);
		mark_current_generation_uncertain(&t, 0);
		freed = metadata_freed;
		writes = syncs;
		calls = hc_sync_calls;
		hc_sync_enabled = mode & 2;
		hc_sync_result = 0;
		if (mode & 1)
			t.pcd = NULL;
		else
			pcd.fman = NULL;

		assert(del(&t, 1, 0) == -ENODEV);
		assert(t.poisoned && indexed(0) == 2);
		assert(!hits(&t, 1, 0) && hits(&t, 0, 0) && hits(&t, 2, 0));
		assert(metadata_freed == freed && uncertain_frees == 0);
		assert(del(&t, 0, 0) == -ENODEV);
		assert(add(&t, 3, 0) == -ENODEV);
		assert(drain(&t) == -ENODEV);
		assert(t.draining && metadata_freed == freed && uncertain_frees == 0);
		assert(syncs == writes && hc_sync_calls == calls);

		t.pcd = &pcd;
		pcd.fman = &fm;
		hc_sync_enabled = false;
		assert(drain(&t) == 0);
		assert(live(false) == 0 && live(true) == 0);
		assert(uncertain_frees > 0);
	}
}

static void test_hc_insert_published_before_error(void)
{
	struct fman fm = { 0 };
	struct fman_pcd pcd = { .fman = &fm };
	struct fman_pcd_ehash_table t;

	setup(&t, &pcd, 50);
	for (unsigned int i = 0; i < 3; i++)
		assert(add(&t, i, 0) == 0);
	mark_current_generation_uncertain(&t, 0);
	hc_sync_enabled = true;
	hc_sync_result = -EIO;

	/* The new record is already visible; report success and retain it. */
	assert(add(&t, 3, 0) == 0);
	assert(t.poisoned && indexed(0) == 4 && hits(&t, 3, 0));
	assert(uncertain_frees == 0);
	assert(add(&t, 4, 0) == -EIO && del(&t, 1, 0) == -EIO);
	assert(indexed(0) == 4 && hits(&t, 1, 0));
	hc_sync_result = 0;
	assert(del(&t, 1, 0) == 0);
	assert(drain(&t) == 0 && uncertain_frees > 0);
	hc_sync_enabled = false;
}

static void test_insert_timeout(void)
{
	struct fman fm = { 0 };
	struct fman_pcd pcd = { .fman = &fm };
	struct fman_pcd_ehash_table t;

	setup(&t, &pcd, 50);
	for (unsigned int i = 0; i < 2; i++)
		assert(add(&t, i, 0) == 0);
	assert(syncs == 0);	/* 1 -> 2 replaces a record head, no nodes */
	fm.timeout = true;
	assert(add(&t, 2, 0) == 0);
	assert(syncs == 3 && warnings == 1);
	for (unsigned int i = 0; i < 3; i++)
		assert(hits(&t, i, 0));
	assert(live(true) == 2);	/* new node + kept old node */
	fm.timeout = false;
	assert(drain(&t) == 0);
}

static void test_uncertain_generation_quarantine(void)
{
	struct fman fm = { 0 };
	struct fman_pcd pcd = { .fman = &fm };
	struct fman_pcd_ehash_table t;
	unsigned int freed, clears;

	setup(&t, &pcd, 50);
	for (unsigned int i = 0; i < 3; i++)
		assert(add(&t, i, 0) == 0);
	assert(live(true) == 1);
	mark_current_generation_uncertain(&t, 0);

	/* This publish leaves the previous generation quarantined. */
	fm.timeout = true;
	assert(add(&t, 3, 0) == 0);
	assert(warnings == 1 && live(true) == 3);
	assert(hits(&t, 0, 0) && hits(&t, 1, 0) &&
	       hits(&t, 2, 0) && hits(&t, 3, 0));

	/* Mutations stay blocked while recovery SYNC times out. */
	assert(del(&t, 0, 0) == -ETIMEDOUT);
	assert(add(&t, 0, 0) == -ETIMEDOUT);
	assert(uncertain_frees == 0);

	/* A failed drain removes heads but retains every reachable allocation. */
	freed = metadata_freed;
	assert(drain(&t) == -ETIMEDOUT);
	assert(t.draining && !heads[0] && indexed(0) == 4);
	assert(live(false) == 4 && live(true) == 3);
	assert(metadata_freed == freed && uncertain_frees == 0);
	assert(add(&t, 4, 0) == -ESHUTDOWN);

	/* A later successful barrier releases all retained generations. */
	fm.timeout = false;
	clears = ehash_poison_clears;
	assert(drain(&t) == 0);
	assert(uncertain_frees > 0);
	assert(ehash_poison_clears == clears + 1);
}

static void test_uncertain_generation_delete_reinsert(void)
{
	struct fman fm = { 0 };
	struct fman_pcd pcd = { .fman = &fm };
	struct fman_pcd_ehash_table t;
	unsigned int clears;

	setup(&t, &pcd, 50);
	for (unsigned int i = 0; i < 3; i++)
		assert(add(&t, i, 0) == 0);
	mark_current_generation_uncertain(&t, 0);

	fm.timeout = true;
	assert(add(&t, 3, 0) == 0);
	assert(del(&t, 1, 0) == -ETIMEDOUT);
	assert(add(&t, 1, 0) == -ETIMEDOUT);
	assert(indexed(0) == 4 && hits(&t, 1, 0));
	assert(uncertain_frees == 0);

	/* The successful recovery barrier must precede delete and reinsertion. */
	fm.timeout = false;
	clears = ehash_poison_clears;
	assert(del(&t, 1, 0) == 0);
	assert(ehash_poison_clears == clears + 1);
	assert(indexed(0) == 3 && !hits(&t, 1, 0));
	assert(add(&t, 1, 0) == 0);
	assert(indexed(0) == 4);
	for (unsigned int i = 0; i < 4; i++)
		assert(hits(&t, i, 0));
	assert(uncertain_frees > 0);
	assert(drain(&t) == 0);
}

static void test_sync_retry(void)
{
	struct fman fm = { 0 };
	struct fman_pcd pcd = { .fman = &fm };
	struct fman_pcd_ehash_table t;
	unsigned int synced, timeouts = ehash_sync_timeouts;
	unsigned int wedged = ehash_sync_wedged, recovered = ehash_sync_recovered;

	/* first attempt times out, second succeeds: the old node is freed */
	setup(&t, &pcd, 50);
	for (unsigned int i = 0; i < 3; i++)
		assert(add(&t, i, 0) == 0);
	fm.fail_n = 1;
	synced = syncs;
	assert(del(&t, 1, 0) == 0);
	assert(syncs == synced + 2 && warnings == 0);
	assert(ehash_sync_timeouts == timeouts + 1 && ehash_sync_recovered == recovered + 1);
	assert(ehash_sync_wedged == wedged);
	assert(!hits(&t, 1, 0) && hits(&t, 0, 0) && hits(&t, 2, 0));
	assert(live(true) == 1 && live(false) == 2);
	assert(drain(&t) == 0);
}

static void test_sync_diagnostics(void)
{
	struct fman fm = { 0 };
	struct fman_pcd pcd = { .fman = &fm };
	struct fman_pcd_ehash_table t = { .pcd = &pcd };
	unsigned int completed = ehash_sync_diag_completed;
	unsigned int pending = ehash_sync_diag_pending;
	unsigned int writes = syncs, polls = reads;
	u32 extc = 0;

	assert(!ehash_sync_diag && ehash_sync_diag_attempts == 0);
	hc_sync_calls = 0;
	fm.polls = 2;
	assert(fman_pcd_ehash_sync(&t, &extc) == 0 && extc == 0);
	assert(hc_sync_calls == 0);
	assert(syncs == writes + 1 && reads == polls + 3);
	writes = syncs;
	polls = reads;
	fm.polls = 2;
	ehash_sync_diag = true;
	assert(fman_pcd_ehash_sync(&t, &extc) == 0 && extc == 0);
	assert(ehash_sync_diag_attempts == 1);
	assert(ehash_sync_diag_completed == completed + 1);
	assert(ehash_sync_diag_pending == pending);
	assert(syncs == writes + 1 && reads == polls + 3);
	assert(ehash_sync_diag_max_polls == 3);
	assert(ehash_sync_diag_max_ns == 2000);
	assert(ehash_sync_diag_lt_10us == 1 && ehash_sync_diag_last_extc == 0);

	/* F-282's post-completion injection must not look like a real pending INV0. */
	ehash_sync_inject = 1;
	writes = syncs;
	polls = reads;
	assert(fman_pcd_ehash_sync(&t, &extc) == 0);
	assert(syncs == writes + 2 && reads == polls + 2);
	assert(ehash_sync_injected == 1 && ehash_sync_inject == 0);
	assert(ehash_sync_diag_completed == completed + 3);
	assert(ehash_sync_diag_pending == pending);

	fm.timeout = true;
	pending = ehash_sync_diag_pending;
	writes = syncs;
	polls = reads;
	assert(fman_pcd_ehash_sync(&t, &extc) == -ETIMEDOUT);
	assert(syncs == writes + FMAN_EHASH_SYNC_TRIES);
	assert(reads == polls + FMAN_EHASH_SYNC_TRIES * 100000U);
	assert(ehash_sync_diag_pending == pending + FMAN_EHASH_SYNC_TRIES);
	assert(ehash_sync_diag_max_polls == 100000);
	assert(ehash_sync_diag_last_extc == INV0 &&
	       ehash_sync_diag_last_pending_extc == INV0);
	assert(ehash_sync_diag_ge_100ms == FMAN_EHASH_SYNC_TRIES);
	fm.timeout = false;
	assert(fman_pcd_ehash_sync(&t, &extc) == 0);
	assert(ehash_sync_diag_last_extc == 0 &&
	       ehash_sync_diag_last_pending_extc == INV0);
	ehash_sync_diag = false;

	/* The second barrier is inert by default and fail-closed when enabled. */
	hc_sync_enabled = true;
	hc_sync_result = 0;
	assert(fman_pcd_ehash_sync(&t, &extc) == 0 && hc_sync_calls == 1);
	hc_sync_result = -ETIMEDOUT;
	assert(fman_pcd_ehash_sync(&t, &extc) == -ETIMEDOUT && extc == 0);
	assert(hc_sync_calls == 2);
	hc_sync_result = -EBUSY;	/* qman_enqueue rejection */
	assert(fman_pcd_ehash_sync(&t, &extc) == -EBUSY && extc == 0);
	hc_sync_result = -EIO;		/* HC error-FQ confirmation */
	assert(fman_pcd_ehash_sync(&t, &extc) == -EIO && extc == 0);
	assert(hc_sync_calls == 4);
	pcd.fman = NULL;
	hc_sync_result = 0;
	assert(fman_pcd_ehash_sync(&t, &extc) == -ENODEV && extc == 0);
	assert(hc_sync_calls == 4);
	hc_sync_enabled = false;
	assert(fman_pcd_ehash_sync(&t, &extc) == -ENODEV && extc == 0);
	t.pcd = NULL;
	assert(fman_pcd_ehash_sync(&t, &extc) == -ENODEV && extc == 0);
	assert(hc_sync_calls == 4);
	t.pcd = &pcd;
	pcd.fman = &fm;
	hc_sync_result = 0;
}

static void test_missing_and_invalid(void)
{
	struct fman fm = { 0 };
	struct fman_pcd pcd = { .fman = &fm };
	struct fman_pcd_ehash_table t;
	u8 key[KEY_MAX] = { 1 };

	setup(&t, &pcd, 50);
	assert(fman_pcd_ehash_del_key(&t, key, 50) == -ENOENT);
	assert(fman_pcd_ehash_del_key(NULL, key, 50) == -EINVAL);
	assert(fman_pcd_ehash_del_key(&t, NULL, 50) == -EINVAL);
	assert(fman_pcd_ehash_del_key(&t, key, 0) == -EINVAL);
	assert(fman_pcd_ehash_del_key(&t, key, 49) == -EINVAL);
	assert(syncs == 0 && nblocks == 0);
}

int main(void)
{
	static const unsigned int fwd[] = { 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 };
	static const unsigned int rev[] = { 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0 };
	static const unsigned int mid[] = { 4, 1, 7, 0, 6, 2, 5, 3 };

	assert(fman_pcd_ehash_cu_stride(1) == 1 && fman_pcd_ehash_cu_stride(3) == 4 &&
	       fman_pcd_ehash_cu_stride(14) == 16 && fman_pcd_ehash_cu_stride(50) == 56 &&
	       fman_pcd_ehash_cu_stride(56) == 56);
	test_bucket(50, 8, fwd);
	test_bucket(50, 8, rev + 4);
	test_bucket(50, 8, mid);
	test_bucket(50, 2, fwd);
	test_bucket(14, 12, fwd);
	test_bucket(14, 12, rev);
	test_bucket(38, 7, rev + 5);
	test_insert_enomem();
	test_delete_enomem();
	test_delete_timeout();
	test_hc_sync_failure_quarantine();
	test_hc_sync_error_propagation();
	test_missing_sync_owner_blocks_insert();
	test_missing_sync_owner_retains_generations();
	test_hc_insert_published_before_error();
	test_insert_timeout();
	test_sync_retry();
	test_sync_diagnostics();
	test_missing_and_invalid();
	test_uncertain_generation_quarantine();
	test_uncertain_generation_delete_reinsert();
	puts("OK: ehash walker model; generation quarantine, blocked delete/reinsert, recovery, and fail-closed drain");
	return 0;
}
"""

def extract_function(source: str, name: str) -> str:
    match = re.search(
        rf"(?m)^(?:static )?(?:bool|int|void|u8|ssize_t|enum qman_cb_dqrr_result) "
        rf"{re.escape(name)}\([^;]*?\n\{{\n", source)
    if match is None:
        raise ValueError(f"Kernel function {name} was not found")
    end = source.index("\n}\n", match.end()) + len("\n}\n")
    return source[match.start():end]


def extract_add_tail(source: str) -> str:
    """add_key()'s F-269 publish block: flow fields through the old-node
    free. Everything between runs unchanged in the harness."""
    first = "\tflow->record = r;\n\tflow->record_dma = rdma;\n"
    last = "\t/* F-148 v6: Write key + mask to CC match table"
    if source.count(first) != 1 or source.count(last) != 1:
        raise ValueError("add_key() F-269 publish block was not found")
    start = source.index(first)
    return source[start:source.index(last, start)]


def extract_add_guard(source: str) -> str:
    add = extract_function(source, "fman_pcd_ehash_add_key")
    first = "\tif (t->draining)\n"
    last = "\tif (key_size > FMAN_EHASH_FLOW_KEY_MAX)\n"
    if add.count(first) != 1 or add.count(last) != 1:
        raise ValueError("add_key() recovery guard was not found")
    start = add.index(first)
    return add[start:add.index(last, start)]


def extract_struct(source: str, name: str) -> str:
    start = source.index(f"struct {name} {{\n")
    return source[start:source.index("\n};", start) + len("\n};")]


SANITIZERS = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
              "-fno-omit-frame-pointer"]


def host_compiler() -> list:
    if os.environ.get("HOSTCC"):
        compiler = shlex.split(os.environ["HOSTCC"])
        if not shutil.which(compiler[0]):
            sys.exit(f"FAIL: HOSTCC compiler not found: {compiler[0]}")
        return compiler
    for name in ("cc", "gcc"):
        if shutil.which(name):
            return [name]
    sys.exit("FAIL: no host C compiler found (set HOSTCC)")


def sanitizers_usable(compiler: list, directory: Path, env: dict) -> bool:
    # Build and run a trivial program: a toolchain without the sanitizer
    # runtime fails to compile, one whose ASan cannot start fails to run.
    # Either way the harness falls back to its explicit counter checks, so
    # an environment limit is never mistaken for a kernel-code violation.
    probe = directory / "probe.c"
    probe.write_text("int main(void) { return 0; }\n")
    binary = directory / "probe"
    built = subprocess.run([*compiler, str(probe), "-o", str(binary),
                            *SANITIZERS], capture_output=True)
    return (built.returncode == 0 and
            subprocess.run([str(binary)], env=env,
                           capture_output=True).returncode == 0)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python3 bin/test-ehash-delete.py <kernel-source-dir>")
    source_root = Path(sys.argv[1])
    source = (source_root /
              "drivers/net/ethernet/freescale/fman/fman_pcd.c").read_text()
    before_sync = (
        "fman_pcd_ehash_cu_stride", "fman_pcd_ehash_cu_free",
        "fman_pcd_ehash_bucket_publish", "fman_pcd_ehash_sync_diag_record")
    functions = "\n".join(extract_function(source, name) for name in before_sync)
    sync = extract_function(source, "fman_pcd_ehash_sync")
    sync = sync.replace(
        "WRITE_ONCE(ehash_sync_inject, ehash_sync_inject - 1);",
        "ehash_sync_inject--;")
    if "WRITE_ONCE(ehash_sync_inject" in sync:
        sys.exit("FAIL: could not normalize F-282 param write for host harness")
    after_sync = (
        "fman_pcd_ehash_recover", "fman_pcd_ehash_flow_drain",
        "fman_pcd_ehash_del_key")
    functions += "\n" + sync + "\n"
    functions += "\n".join(extract_function(source, name) for name in after_sync)
    tail = extract_add_tail(source)
    guard = extract_add_guard(source)
    with tempfile.TemporaryDirectory(prefix="ask2-ehash-delete-") as directory:
        path = Path(directory)
        (path / "fman_ehash_add_guard.h").write_text(guard)
        code = path / "delete.c"
        binary = path / "delete"
        code.write_text(HARNESS.replace("@KERNEL_FUNCTIONS@", functions)
                        .replace("@ADD_TAIL@", tail)
                        .replace("@ADD_GUARD@", guard))
        # Leak accounting is explicit in the harness (freed-allocation
        # counters), so LSan, which needs ptrace, is not required.
        env = {**os.environ,
               "ASAN_OPTIONS": os.environ.get("ASAN_OPTIONS", "detect_leaks=0")}
        compiler = host_compiler()
        build = [*compiler, "-std=gnu11", "-Wall", "-Wextra",
                 str(code), "-o", str(binary)]
        use_sanitizers = sanitizers_usable(compiler, path, env)
        if use_sanitizers:
            build += SANITIZERS
        else:
            print("note: sanitizers unavailable; counter checks only",
                  flush=True)
        if subprocess.run(build).returncode != 0:
            sys.exit("FAIL: harness does not compile against this kernel source")
        if subprocess.run([str(binary)], env=env).returncode != 0:
            sys.exit("FAIL: ehash delete harness found a violation (see above)")

        hc_source = (source_root /
                     "drivers/net/ethernet/freescale/fman/fman_pcd_hc.c").read_text()
        if "smp_store_release(&hc_state, hc);" not in hc_source:
            sys.exit("FAIL: HC state must be published before the barrier can read it")
        param = re.search(
            r"static bool ehash_hc_sync_enable;\n"
            r"module_param_named\(ehash_hc_sync_enable,\s*"
            r"ehash_hc_sync_enable,\s*bool,\s*0400\);", hc_source)
        if not param:
            sys.exit("FAIL: HC barrier must be a read-only boot parameter")
        hc_functions = param.group() + "\n"
        hc_functions += "\n".join(
            extract_function(hc_source, name)
            for name in ("hc_dqrr", "hc_sync_one",
                         "fman_pcd_hc_ehash_sync", "hc_fini")
        )
        (path / "hc_barrier_body.h").write_text(hc_functions)
        hc_binary = path / "hc-barrier"
        hc_harness = (Path(__file__).resolve().parents[1] /
                      "tests/dut/fman_hc_barrier.c")
        hc_build = [*compiler, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                    "-I", str(path), str(hc_harness), "-o", str(hc_binary)]
        if use_sanitizers:
            hc_build += SANITIZERS
        if subprocess.run(hc_build).returncode != 0:
            sys.exit("FAIL: HC barrier harness does not compile")
        failed = [
            scenario for scenario in ("boot", "sequence", "transport")
            if subprocess.run([str(hc_binary), scenario], env=env).returncode
        ]
        if failed:
            sys.exit(f"FAIL: HC barrier scenarios failed: {', '.join(failed)}")

        lifecycle_lock = (
            "F-291: caller holds fe_lock across this check and publication."
            in source
        )
        chain_guard = "static bool fman_pcd_fe_debug_mutation_blocked_locked(" in source
        guard = (extract_function(source, "fman_pcd_fe_debug_mutation_blocked_locked")
                 if chain_guard else "")
        teardown = guard + "\n".join(
            extract_function(source, name)
            for name in ("fman_pcd_fe_disengage_full_write",
                         "fman_pcd_ehash_write")
        )
        if lifecycle_lock:
            teardown += "\n" + extract_function(source, "fman_pcd_port_recover")
        (path / "fman_teardown_body.h").write_text(teardown)
        teardown_binary = path / "fman-teardown"
        teardown_harness = (Path(__file__).resolve().parents[1] /
                            "tests/dut/fman_teardown.c")
        teardown_build = [
            *compiler, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-sign-compare", "-I", str(path),
            str(teardown_harness), "-o", str(teardown_binary),
        ]
        if lifecycle_lock:
            teardown_build += ["-DFMAN_TEST_LIFECYCLE_LOCK",
                               "-DFMAN_TEST_RECOVERY_BLOCKED"]
        if chain_guard:
            teardown_build += ["-DFMAN_TEST_DEBUG_CHAIN_GUARD"]
        if use_sanitizers:
            teardown_build += SANITIZERS
        if subprocess.run(teardown_build).returncode != 0:
            sys.exit("FAIL: FMan teardown harness does not compile")
        if subprocess.run([str(teardown_binary)], env=env).returncode != 0:
            sys.exit("FAIL: FMan teardown did not fail closed")

        # F-289: check retained state before even the armed-port fast path.
        engage = extract_function(
            source, "__fman_pcd_fe_engage_profile_locked" if lifecycle_lock
            else "fman_pcd_fe_engage_profile"
        )
        gate = engage.find("fman_pcd_fe_retained_check(pcd, hw_port_id)")
        armed = engage.find("test_bit(hw_port_id, pcd->fe_port_armed)")
        arm = engage.find("__fman_pcd_fe_arm_engage(")
        if not (0 <= gate < armed < arm):
            sys.exit("FAIL: engage must refuse retained FE state before arming KG")
        retained_check = extract_function(source, "fman_pcd_fe_retained_check")
        retained = retained_check + "\n"
        if lifecycle_lock:
            retained += extract_function(source, "__fman_pcd_fe_disengage_locked")
        retained += extract_function(source, "fman_pcd_fe_disengage")
        (path / "fman_retained_body.h").write_text(retained)
        retained_binary = path / "fman-retained"
        retained_harness = (Path(__file__).resolve().parents[1] /
                            "tests/dut/fman_retained.c")
        retained_build = [
            *compiler, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-I", str(path),
            str(retained_harness), "-o", str(retained_binary),
        ]
        if "F-291: failed FE programming cannot be retried safely" in source:
            if "test_bit(port_id, pcd->fe_port_faulted)" not in retained_check:
                sys.exit("FAIL: F-291 retained check does not fence port faults")
            retained_build.append("-DFMAN_TEST_PORT_FAULT")
        if lifecycle_lock:
            retained_build.append("-DFMAN_TEST_LIFECYCLE_LOCK")
        if use_sanitizers:
            retained_build += SANITIZERS
        if subprocess.run(retained_build).returncode != 0:
            sys.exit("FAIL: FMan retained-teardown harness does not compile")
        if subprocess.run([str(retained_binary)], env=env).returncode != 0:
            sys.exit("FAIL: failed FE disengage is not reported and retained")

        token_start = source.index("static atomic64_t fman_pcd_fe_token_seq")
        token_end = source.index(
            "EXPORT_SYMBOL_GPL(fman_pcd_fe_alloc_token);", token_start
        ) + len("EXPORT_SYMBOL_GPL(fman_pcd_fe_alloc_token);")
        lookup_start = source.index("/* F-280: the live flow for @key")
        lookup_end = source.index("int fman_pcd_fe_flow_add(", lookup_start)
        identity = source[token_start:token_end] + "\n"
        identity += source[lookup_start:lookup_end]
        identity += "\n".join(
            extract_function(source, name)
            for name in ("fman_pcd_fe_flow_add",
                         "fman_pcd_fe_flow_del_checked",
                         "fman_pcd_fe_flow_del_owner",
                         "fman_pcd_fe_flow_del_identified",
                         "fman_pcd_fe_flow_get_stats_checked",
                         "fman_pcd_fe_flow_get_stats",
                         "fman_pcd_fe_flow_get_stats_identified")
        )
        (path / "fman_identity_body.h").write_text(identity)
        pcd_header = (source_root / "include/linux/fsl/fman_pcd.h").read_text()
        (path / "fman_identity_types.h").write_text("\n".join(
            extract_struct(pcd_header, name)
            for name in ("fman_pcd_fe_flow_action", "fman_pcd_nat_params",
                         "fman_pcd_vlan_params")
        ))
        identity_binary = path / "fman-identity"
        identity_harness = (Path(__file__).resolve().parents[1] /
                            "tests/dut/fman_ehash_identity.c")
        identity_build = [
            *compiler, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-pthread", "-I", str(path),
            str(identity_harness), "-o", str(identity_binary),
        ]
        if lifecycle_lock:
            identity_build.append("-DFMAN_TEST_LIFECYCLE_LOCK")
        if use_sanitizers:
            identity_build += SANITIZERS
        if subprocess.run(identity_build).returncode != 0:
            sys.exit("FAIL: FMan identity harness does not compile")
        if subprocess.run([str(identity_binary)], env=env).returncode != 0:
            sys.exit("FAIL: FMan identity harness found a violation")

        lookup_end = source.index(
            "static int fman_pcd_ehash_token_add_check", lookup_start
        )
        debugfs = guard + source[lookup_start:lookup_end]
        debugfs += "\n".join(
            extract_function(source, name)
            for name in ("fman_pcd_ehash_add_key_natwrap_dbgfs",
                         "fman_pcd_ehash_debug_clear",
                         "fman_pcd_fe_flow_write")
        )
        (path / "fman_debugfs_owner_body.h").write_text(debugfs)
        owner_constants = []
        for owner in ("MASK", "ASK", "FLOWER", "NTUPLE", "WGFP", "DEBUGFS"):
            match = re.search(
                rf"(?m)^#define FMAN_PCD_FE_OWNER_{owner}\b[^\n]*",
                pcd_header,
            )
            if not match:
                sys.exit(f"FAIL: FMan owner constant {owner} is missing")
            owner_constants.append(match.group())
        (path / "fman_debugfs_owner_constants.h").write_text(
            "\n".join(owner_constants) + "\n"
        )
        debugfs_binary = path / "fman-debugfs-owner"
        debugfs_harness = (Path(__file__).resolve().parents[1] /
                           "tests/dut/fman_debugfs_owner.c")
        debugfs_build = [
            *compiler, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-sign-compare", "-I", str(path),
            str(debugfs_harness), "-o", str(debugfs_binary),
        ]
        if chain_guard:
            debugfs_build += ["-DFMAN_TEST_DEBUG_CHAIN_GUARD"]
        if use_sanitizers:
            debugfs_build += SANITIZERS
        if subprocess.run(debugfs_build).returncode != 0:
            sys.exit("FAIL: FMan debugfs ownership harness does not compile")
        if subprocess.run([str(debugfs_binary)], env=env).returncode != 0:
            sys.exit("FAIL: FMan debugfs ownership harness found a violation")


if __name__ == "__main__":
    main()
