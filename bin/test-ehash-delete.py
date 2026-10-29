#!/usr/bin/env python3
"""Run the patched ehash insert/delete code against a model of the FMan walker.

Usage: python3 bin/test-ehash-delete.py <kernel-source-dir>
The tree must already carry the F-254/F-256, F-264, F-267 and F-269 fixups;
ci-setup-kernel.sh runs this right after F-269. Keys select one of two
buckets by their second byte, so one bucket fills while the other must stay
reachable.

The kernel source is used verbatim: bucket publish, SYNC, drain, del_key and
add_key()'s publish tail. Only allocation, list and hardware interfaces are
stubbed. After every bucket-head store a model of the microcode 210.10.1
lookup (cumulative nodes, decomp/en-exthash-lookup.asm) walks every bucket.
Present keys must hit, deleted keys must miss, and a pool block may be freed
only after a SYNC that completed after the head store that unlinked it. This
checks layout, lifetime and reachability invariants, not silicon.
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
};

/* Every pool block the code under test owns, with the last head store
 * after which the FMan walker could reach it. */
struct block { void *va; bool node; bool live; unsigned int seen; };
static struct block blocks[256];
static unsigned int nblocks;
static unsigned int head_writes, sync_mark, syncs, reads, warnings;
static unsigned int ehash_sync_timeouts, ehash_sync_recovered, ehash_sync_wedged;
#define FMAN_EHASH_SYNC_TRIES 3
static unsigned int metadata_freed, fail_in;
static bool wmb_pending, barrier, draining;
static struct fman_pcd_ehash_table *cur;

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
static void udelay(unsigned int us) { assert(us == 1); }
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
/* A block may be freed only once no head reaches it and a SYNC completed
 * after the head store that unlinked it (drain runs with ports detached). */
static void dma_pool_free(void *pool, void *va, dma_addr_t dma)
{
	struct block *blk = block_of(va);

	assert(blk && dma == (uintptr_t)va);
	assert(blk->node == (pool == cur->cu_pool));
	assert(blk->node || pool == cur->rec_pool);
	assert(draining || blk->seen == 0 || sync_mark > blk->seen);
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
	nblocks = head_writes = sync_mark = syncs = reads = warnings = 0;
	metadata_freed = fail_in = 0;
	wmb_pending = barrier = draining = false;
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
	struct fman_pcd_ehash_flow *flow = calloc(1, sizeof(*flow));
	dma_addr_t rdma;
	u8 *r = dma_pool_zalloc(t->rec_pool, GFP_KERNEL, &rdma);
	u8 key[KEY_MAX];
	int err;

	assert(flow && r);
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

static void drain(struct fman_pcd_ehash_table *t)
{
	draining = true;
	fman_pcd_ehash_flow_drain(t);
	draining = false;
	assert(!heads[0] && !heads[2] && !cus[0] && !cus[1]);
	assert(t->flows.next == &t->flows && !bidx[0].first && !bidx[1].first);
	assert(live(false) == 0 && live(true) == 0);
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
	drain(&t);
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
	drain(&t);
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
	drain(&t);
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
	fm.timeout = true;
	freed = metadata_freed;
	synced = syncs;
	reads = 0;
	assert(del(&t, 1, 0) == 0);	/* publishes node C 5, keeps B */
	assert(syncs == synced + 3 && reads == 300000 && warnings == 3);
	assert(ehash_sync_wedged == 1 && ehash_sync_timeouts == 3 && ehash_sync_recovered == 0);
	assert(metadata_freed == freed + 2);	/* flow + old node list */
	assert(!hits(&t, 1, 0) && hits(&t, 0, 0) && hits(&t, 2, 0));
	assert(live(false) == 3 && live(true) == 2);	/* record + old node kept */
	rec = &blocks[1];
	node = &blocks[4];
	assert(rec->live && !rec->node && node->live && node->node);
	assert(rec->va && ((u8 *)rec->va)[FMAN_EHASH_FLOW_KEY_OFF] == 1);
	rec->live = node->live = false;	/* test cleanup only */
	free(rec->va);
	free(node->va);
	fm.timeout = false;
	drain(&t);
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
	blocks[2].live = false;	/* test cleanup only: the old node */
	assert(blocks[2].node);
	free(blocks[2].va);
	fm.timeout = false;
	drain(&t);
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
	drain(&t);
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
	test_insert_timeout();
	test_sync_retry();
	test_missing_and_invalid();
	puts("OK: ehash cumulative-node buckets (walker model), copy-on-write SYNC-before-free, ENOMEM, SYNC-timeout retention, drain");
	return 0;
}
"""

def extract_function(source: str, name: str) -> str:
    match = re.search(rf"static (?:int|void|u8) {name}\([^;]*?\n\{{\n", source)
    if match is None:
        raise ValueError(f"Kernel function {name} was not found")
    end = source.index("\n}\n", match.end()) + len("\n}\n")
    return source[match.start():end]


def extract_add_tail(source: str) -> str:
    """add_key()'s F-269 publish block: flow fields through the old-node
    free. Everything between runs unchanged in the harness."""
    first = "\tflow->record = r;\n\tflow->record_dma = rdma;\n"
    last = "\t\t\tfman_pcd_ehash_cu_free(t, old_cu);\n\t}\n"
    if source.count(first) != 1 or source.count(last) != 1:
        raise ValueError("add_key() F-269 publish block was not found")
    start = source.index(first)
    return source[start:source.index(last, start) + len(last)]


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
    source = (Path(sys.argv[1]) /
              "drivers/net/ethernet/freescale/fman/fman_pcd.c").read_text()
    functions = "\n".join(extract_function(source, name) for name in (
        "fman_pcd_ehash_cu_stride", "fman_pcd_ehash_cu_free",
        "fman_pcd_ehash_bucket_publish", "fman_pcd_ehash_sync",
        "fman_pcd_ehash_flow_drain", "fman_pcd_ehash_del_key"))
    tail = extract_add_tail(source)
    with tempfile.TemporaryDirectory(prefix="ask2-ehash-delete-") as directory:
        path = Path(directory)
        code = path / "delete.c"
        binary = path / "delete"
        code.write_text(HARNESS.replace("@KERNEL_FUNCTIONS@", functions)
                        .replace("@ADD_TAIL@", tail))
        # Leak accounting is explicit in the harness (freed-allocation
        # counters), so LSan, which needs ptrace, is not required.
        env = {**os.environ,
               "ASAN_OPTIONS": os.environ.get("ASAN_OPTIONS", "detect_leaks=0")}
        compiler = host_compiler()
        build = [*compiler, "-std=gnu11", "-Wall", "-Wextra",
                 str(code), "-o", str(binary)]
        if sanitizers_usable(compiler, path, env):
            build += SANITIZERS
        else:
            print("note: sanitizers unavailable; counter checks only",
                  flush=True)
        if subprocess.run(build).returncode != 0:
            sys.exit("FAIL: harness does not compile against this kernel source")
        if subprocess.run([str(binary)], env=env).returncode != 0:
            sys.exit("FAIL: ehash delete harness found a violation (see above)")


if __name__ == "__main__":
    main()
