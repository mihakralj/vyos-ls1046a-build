/* Host fault tests for F-291 per-port FE workspace ownership. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
#define __iomem
#define FMAN_PP_FE_MGMT_IDX_OFF 0x54
#define FMAN_PP_FE_DEPL_CNT_OFF 0x58
#define FMAN_PCD_FE_PARAMS_PAGE_SIZE 256
#define pr_err(...) ((void)0)
#define dev_err(...) ((void)0)
#define dev_info(...) ((void)0)

struct list_head { bool removed; };
struct fman_pcd_ehash_table { int unused; };
struct gen_pool { int unused; };
struct muram_info { int unused; };
struct fman_port { int unused; };
struct fman {
	struct muram_info *muram;
	struct fman_port *port;
};
struct fman_pcd_fe_port {
	struct list_head node;
	struct fman_pcd_ehash_table *table_v4;
	unsigned long mgmt_off, pool_raw_off;
	size_t mgmt_size, pool_raw_size;
	unsigned long scaffold_ato, scaffold_mto, scaffold_gro;
};
struct fman_pcd {
	struct fman *fman;
	struct fman_pcd_fe_port *port;
	struct gen_pool *muram_pool;
};

enum failure {
	NONE, NO_ENTRY, NO_FMAN, NO_PORT, NO_MURAM, NO_PAGE, NO_INDEX,
	NO_POOL, STALE_PAGE, NO_MAPPING, WRONG_OWNER,
	TABLE_FAILURE, INDEX_WRITE_FAILURE, COUNTER_WRITE_FAILURE,
};
static enum failure failure;
static u32 page[0x100 / sizeof(u32)];
static unsigned int table_frees, muram_frees, writes, entry_frees;
static unsigned long freed_offsets[5];

static struct muram_info *fman_get_muram(struct fman *fm)
{
	return fm ? fm->muram : NULL;
}
static struct fman *fman_pcd_get_fman(struct fman_pcd *pcd) { return pcd->fman; }
static struct fman_port *fman_port_lookup_rx(struct fman *fm, u8 port)
{
	assert(port == 0x10);
	return failure == NO_PORT ? NULL : fm->port;
}
static struct fman_pcd_fe_port *fman_pcd_fe_port_find(struct fman_pcd *pcd,
						      u8 port)
{
	assert(port == 0x10);
	return failure == NO_ENTRY ? NULL : pcd->port;
}
static u32 fman_port_get_params_page(struct fman_port *port)
{
	assert(port);
	return failure == NO_PAGE ? 0 : 0x100;
}
static unsigned long fman_muram_offset_to_vbase(struct muram_info *muram,
						 u32 offset)
{
	assert(muram && offset == 0x100);
	return failure == NO_MAPPING ? 0 : (unsigned long)page;
}
static __attribute__((unused)) bool gen_pool_has_addr(struct gen_pool *pool,
						      unsigned long off,
						      size_t size)
{
	assert(pool && off == 0x100 && size == 0x100);
	return failure != STALE_PAGE;
}
static __attribute__((unused)) u32 ioread32be(const void *address)
{
	u32 value;

	memcpy(&value, address, sizeof(value));
	return value;
}
static void iowrite32be(u32 value, void *address)
{
	writes++;
	if ((failure == INDEX_WRITE_FAILURE &&
	     address == (u8 *)page + FMAN_PP_FE_MGMT_IDX_OFF) ||
	    (failure == COUNTER_WRITE_FAILURE &&
	     address == (u8 *)page + FMAN_PP_FE_DEPL_CNT_OFF))
		return;
	memcpy(address, &value, sizeof(value));
}
static int fman_pcd_ehash_table_free(struct fman_pcd *pcd,
				     struct fman_pcd_ehash_table *table)
{
	assert(pcd->port->table_v4 == table);
	table_frees++;
	return failure == TABLE_FAILURE ? -ETIMEDOUT : 0;
}
static void fman_pcd_muram_free(struct fman_pcd *pcd, unsigned long off,
				size_t size)
{
	assert(pcd && off && size);
	assert(muram_frees < sizeof(freed_offsets) / sizeof(freed_offsets[0]));
	freed_offsets[muram_frees++] = off;
}
static void list_del(struct list_head *node) { node->removed = true; }
static void kfree(void *ptr) { assert(ptr); entry_frees++; }

#include "workspace_disarm_body.h"

int main(void)
{
	struct muram_info muram = {};
	struct fman_port port = {};
	struct fman fm = { .muram = &muram, .port = &port };
	struct gen_pool pool = {};
	struct fman_pcd_ehash_table table = {};
	struct fman_pcd_fe_port fp;
	struct fman_pcd pcd = { .fman = &fm, .port = &fp };

	for (failure = NO_ENTRY; failure <= COUNTER_WRITE_FAILURE; failure++) {
		int rc, expected;

		fp = (struct fman_pcd_fe_port) {
			.table_v4 = &table, .mgmt_off = 0x200, .mgmt_size = 21,
			.pool_raw_off = 0x300, .pool_raw_size = 8192,
			.scaffold_ato = 0x400, .scaffold_mto = 0x500,
			.scaffold_gro = 0x600,
		};
		memset(page, 0, sizeof(page));
		if (failure == NO_INDEX)
			fp.mgmt_off = 0;
		page[FMAN_PP_FE_MGMT_IDX_OFF / sizeof(u32)] =
			failure == WRONG_OWNER ? 0x204 : fp.mgmt_off;
		page[FMAN_PP_FE_DEPL_CNT_OFF / sizeof(u32)] = 1;
		fm.muram = failure == NO_MURAM ? NULL : &muram;
		pcd.fman = failure == NO_FMAN ? NULL : &fm;
		pcd.muram_pool = failure == NO_POOL ? NULL : &pool;
		table_frees = muram_frees = writes = entry_frees = 0;
		rc = fman_pcd_fe_port_del(&pcd, 0x10);
		switch (failure) {
		case NO_ENTRY: expected = -ENOENT; break;
		case NO_PORT: expected = -ENODEV; break;
		case NO_FMAN: case NO_MURAM: case NO_PAGE: case NO_INDEX:
			expected = -ENXIO;
			break;
		case TABLE_FAILURE: expected = -ETIMEDOUT; break;
		default: expected = -EIO; break;
		}
		assert(rc == expected && !muram_frees && !entry_frees &&
		       !fp.node.removed);
		if (failure <= WRONG_OWNER) {
			assert(!table_frees && !writes && fp.table_v4 == &table);
		} else if (failure == TABLE_FAILURE) {
			assert(table_frees == 1 && !writes && fp.table_v4 == &table);
		} else {
			assert(table_frees == 1 && !fp.table_v4);
			assert(writes == (failure == INDEX_WRITE_FAILURE ? 1U : 2U));
		}
	}

	failure = NONE;
	fp = (struct fman_pcd_fe_port) {
		.table_v4 = &table, .mgmt_off = 0x200, .mgmt_size = 21,
		.pool_raw_off = 0x300, .pool_raw_size = 8192,
		.scaffold_ato = 0x400, .scaffold_mto = 0x500, .scaffold_gro = 0x600,
	};
	fm.muram = &muram;
	pcd.fman = &fm;
	pcd.muram_pool = &pool;
	memset(page, 0, sizeof(page));
	page[FMAN_PP_FE_MGMT_IDX_OFF / sizeof(u32)] = fp.mgmt_off;
	page[FMAN_PP_FE_DEPL_CNT_OFF / sizeof(u32)] = 1;
	table_frees = muram_frees = writes = entry_frees = 0;
	assert(fman_pcd_fe_port_del(&pcd, 0x10) == 0);
	assert(table_frees == 1 && muram_frees == 5 && writes == 2 &&
	       entry_frees == 1 && fp.node.removed);
	assert(freed_offsets[0] == 0x200 && freed_offsets[1] == 0x300 &&
	       freed_offsets[2] == 0x400 && freed_offsets[3] == 0x500 &&
	       freed_offsets[4] == 0x600);
	assert(!page[FMAN_PP_FE_MGMT_IDX_OFF / sizeof(u32)] &&
	       !page[FMAN_PP_FE_DEPL_CNT_OFF / sizeof(u32)]);
	puts("FE workspace: absent owners and failed readback retain MURAM");
	return 0;
}
