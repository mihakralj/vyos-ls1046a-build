/* Host mocks for F-280 ehash owner checks (extracted helpers). */
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;

struct hlist_node { struct hlist_node *next; };
struct hlist_head { struct hlist_node *first; };
#define container_of(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
#define hlist_for_each_entry(pos, head, m)                                  \
	for (pos = (head)->first ?                                          \
		container_of((head)->first, typeof(*pos), m) : NULL;        \
	     pos;                                                           \
	     pos = pos->m.next ? container_of(pos->m.next, typeof(*pos), m) \
			       : NULL)

#define FMAN_EHASH_FLOW_KEY_OFF 8
#define FMAN_PCD_FE_OWNER_MASK 0xffU
#define FMAN_PCD_FE_OWNER_ASK 0x00U
#define FMAN_PCD_FE_OWNER_FLOWER 0x01U
#define FMAN_PCD_FE_OWNER_WGFP 0x03U
#define NBUCKETS 4

struct fman_pcd_ehash_flow {
	struct hlist_node bnode;
	void *record;
	u8 key_size;
	u8 owner;
};
struct fman_pcd_ehash_table {
	struct hlist_head bidx[NBUCKETS];
	u8 key_size;
	int hash_shift;
	unsigned int hash_mask;
};

static unsigned int fman_pcd_ehash_bucket_index(const u8 *key, u8 ks,
						int shift, unsigned int mask)
{
	return key[0] & mask;
}

#include "fman_ehash_owner_body.h"

static u8 recs[3][64];
static struct fman_pcd_ehash_flow flows[3];

static void put(struct fman_pcd_ehash_table *t, int i, u8 k0, u8 k1, u8 owner)
{
	recs[i][FMAN_EHASH_FLOW_KEY_OFF] = k0;
	recs[i][FMAN_EHASH_FLOW_KEY_OFF + 1] = k1;
	flows[i].record = recs[i];
	flows[i].key_size = 2;
	flows[i].owner = owner;
	flows[i].bnode.next = t->bidx[k0 & t->hash_mask].first;
	t->bidx[k0 & t->hash_mask].first = &flows[i].bnode;
}

int main(void)
{
	struct fman_pcd_ehash_table t = { .key_size = 2, .hash_mask = 3 };
	u8 a[2] = { 1, 7 }, b[2] = { 5, 9 }, c[2] = { 1, 8 }, wrong[3] = { 1, 7, 0 };

	put(&t, 0, 1, 7, FMAN_PCD_FE_OWNER_ASK);
	put(&t, 1, 5, 9, FMAN_PCD_FE_OWNER_FLOWER);	/* same bucket as a */

	assert(fman_pcd_ehash_lookup(&t, a, 2) == &flows[0]);
	assert(fman_pcd_ehash_lookup(&t, b, 2) == &flows[1]);
	assert(!fman_pcd_ehash_lookup(&t, c, 2));
	assert(!fman_pcd_ehash_lookup(&t, NULL, 2));
	/* A request wider than the table never matches, even a stray record. */
	put(&t, 2, 1, 7, FMAN_PCD_FE_OWNER_WGFP);
	flows[2].key_size = 3;
	assert(!fman_pcd_ehash_lookup(&t, wrong, 3));
	t.bidx[1].first = flows[2].bnode.next;

	/* Same owner: not foreign, so F-219 eviction/delete proceeds. */
	assert(!fman_pcd_ehash_foreign(&t, a, 2, FMAN_PCD_FE_OWNER_ASK));
	assert(!fman_pcd_ehash_foreign(&t, b, 2, FMAN_PCD_FE_OWNER_FLOWER));
	/* Other owners are refused, both directions. */
	assert(fman_pcd_ehash_foreign(&t, a, 2, FMAN_PCD_FE_OWNER_FLOWER));
	assert(fman_pcd_ehash_foreign(&t, a, 2, FMAN_PCD_FE_OWNER_WGFP));
	assert(fman_pcd_ehash_foreign(&t, b, 2, FMAN_PCD_FE_OWNER_ASK));
	/* Only the low byte is the owner. */
	assert(!fman_pcd_ehash_foreign(&t, b, 2,
				       0x100U | FMAN_PCD_FE_OWNER_FLOWER));
	/* An absent key belongs to nobody. */
	assert(!fman_pcd_ehash_foreign(&t, c, 2, FMAN_PCD_FE_OWNER_WGFP));

	printf("fman_ehash_owner: all checks passed\n");
	return 0;
}
