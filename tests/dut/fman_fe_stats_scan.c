/* Host harness for F-315 fman_pcd_fe_stats_scan() (bin/kernel-fixups/F_315.py).
 * The generated function is included from fe_stats_scan_body.h, extracted by bin/test-fe-stats-scan.py
 * from a fixup-applied copy of fman_pcd.c; tables, records, the hlist and the lock are mocked. Build
 * with ASan: a resume past a smaller table's last bucket must not read t->bidx out of bounds. */
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint64_t __be64;
#define be64_to_cpu(x) __builtin_bswap64(x)
#define dma_rmb() do { } while (0)
#define EXPORT_SYMBOL_GPL(x)
#define FMAN_PCD_FE_OWNER_MASK 0xffU

struct hlist_node { struct hlist_node *next; };
struct hlist_head { struct hlist_node *first; };
#define container_of(p, T, m) ((T *)((char *)(p) - __builtin_offsetof(T, m)))
#define hlist_for_each_entry(pos, head, member) \
	for (struct hlist_node *__n = (head)->first; \
	     __n && ((pos) = container_of(__n, __typeof__(*(pos)), member)); __n = __n->next)

struct fman_pcd_ehash_flow { struct hlist_node bnode; void *record; u8 owner; u64 owner_token; };
struct fman_pcd_ehash_table { u32 hash_mask; struct hlist_head *bidx; };
struct fman_pcd { int fe_stats_lock; int ntables; struct fman_pcd_ehash_table *tables[4]; };
struct fman { struct fman_pcd *pcd; };

static int locks, unlocks;
static void spin_lock(int *l) { assert(!*l); *l = 1; locks++; }
static void spin_unlock(int *l) { assert(*l); *l = 0; unlocks++; }
static struct fman_pcd *fman_get_pcd(struct fman *fm) { return fm->pcd; }
static struct fman_pcd_ehash_table *fman_pcd_ehash_table_by_index(struct fman_pcd *pcd, unsigned int i)
{
	assert(pcd->fe_stats_lock);
	return (int)i < pcd->ntables ? pcd->tables[i] : NULL;
}

typedef void (*fman_pcd_fe_stats_fn)(void *arg, u64 owner_token, u64 packets, u64 bytes);

#include "fe_stats_scan_body.h"

static u64 got_tok[64], got_pk[64], got_by[64];
static int ngot;
static void cb(void *arg, u64 tok, u64 pk, u64 by)
{
	(void)arg;
	assert(ngot < 64);
	got_tok[ngot] = tok; got_pk[ngot] = pk; got_by[ngot] = by; ngot++;
}

static struct fman_pcd_ehash_table *mk_table(u32 mask)
{
	struct fman_pcd_ehash_table *t = calloc(1, sizeof(*t));

	t->hash_mask = mask;
	t->bidx = calloc(mask + 1, sizeof(*t->bidx));	/* exactly mask + 1 heads, like kvcalloc */
	return t;
}

static void add(struct fman_pcd_ehash_table *t, u32 b, u8 owner, u64 tok, u64 pk, u64 by)
{
	struct fman_pcd_ehash_flow *f = calloc(1, sizeof(*f));
	u8 *r = calloc(1, 320);

	*(u64 *)(r + 256) = __builtin_bswap64(pk);	/* big-endian, as the microcode writes them */
	*(u64 *)(r + 264) = __builtin_bswap64(by);
	f->record = r; f->owner = owner; f->owner_token = tok;
	f->bnode.next = t->bidx[b].first;
	t->bidx[b].first = &f->bnode;
}

static int scan(struct fman *fm, u32 *cur, u32 budget)
{
	int rc = fman_pcd_fe_stats_scan(fm, 0, cur, budget, cb, NULL);

	assert(locks == unlocks);
	return rc;
}

int main(void)
{
	struct fman_pcd pcd = { 0 };
	struct fman fm = { &pcd };
	u32 cur = 0;
	int rc, i;

	pcd.tables[0] = mk_table(7);
	pcd.tables[1] = mk_table(3);
	pcd.ntables = 2;
	add(pcd.tables[0], 1, 0, 11, 5, 500);
	add(pcd.tables[0], 1, 3, 12, 9, 900);		/* WGFP owner: not reported */
	add(pcd.tables[0], 7, 0, 13, 6, 600);
	add(pcd.tables[0], 2, 0, 0, 1, 1);		/* no token: not reported */
	add(pcd.tables[1], 0, 0, 14, 0x0102030405ULL, 0x1122334455667788ULL);

	/* one full pass */
	assert(scan(&fm, &cur, 100) == 1 && cur == 0 && ngot == 3);
	assert(got_tok[0] == 11 && got_pk[0] == 5 && got_by[0] == 500);
	assert(got_tok[1] == 13 && got_tok[2] == 14);
	assert(got_pk[2] == 0x0102030405ULL && got_by[2] == 0x1122334455667788ULL);

	/* budget 1 record per call: same records, resumed across calls, last call wraps */
	ngot = 0; cur = 0;
	for (i = 0; i < 20 && (rc = scan(&fm, &cur, 1)) == 0; i++)
		;
	assert(rc == 1 && cur == 0 && ngot == 3 && got_tok[0] == 11 && got_tok[1] == 13 && got_tok[2] == 14);

	/* resume past the end of a smaller table (a larger table at this index was freed): no OOB read */
	ngot = 0; cur = (1u << 16) | 7;
	assert(scan(&fm, &cur, 100) == 1 && ngot == 0 && cur == 0);

	/* a table freed between calls shifts the indices: the cursor names nothing, the pass ends */
	ngot = 0; cur = (1u << 16) | 2;
	pcd.tables[0] = pcd.tables[1]; pcd.ntables = 1;
	assert(scan(&fm, &cur, 100) == 1 && ngot == 0);

	/* bucket cap per hold on an empty full-size table */
	pcd.tables[0] = mk_table(0x7fff); pcd.ntables = 1; cur = 0;
	assert(scan(&fm, &cur, 256) == 0 && cur == 4096);
	while ((rc = scan(&fm, &cur, 256)) == 0)
		;
	assert(rc == 1 && cur == 0);

	/* bad arguments */
	assert(fman_pcd_fe_stats_scan(&fm, 0, NULL, 1, cb, NULL) == -EINVAL);
	assert(fman_pcd_fe_stats_scan(&fm, 0, &cur, 0, cb, NULL) == -EINVAL);
	assert(fman_pcd_fe_stats_scan(&fm, 0, &cur, 1, NULL, NULL) == -EINVAL);

	puts("fman_fe_stats_scan: OK");
	return 0;
}
