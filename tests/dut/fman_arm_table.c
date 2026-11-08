/* Host fault tests for generated FMan per-port table allocation. */
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>

typedef uint8_t u8;
typedef uint32_t u32;

#define FMAN_PCD_FE_PROFILE_ROUTED 0
#define pr_warn(...) ((void)0)
#define dev_info(...) ((void)0)

struct fman_pcd_ehash_table {
	unsigned long table_dma;
};
struct fake_list {
	struct fman_pcd_ehash_table *first;
	struct fman_pcd_ehash_table *last;
};
struct fman_pcd {
	struct fake_list fe_ehash_tables;
	struct fman_pcd_ehash_table *fe_arming_v4_table;
	u8 fe_arming_port_id;
	void *fman;
	unsigned int profile;
};
static const struct { u8 key_size; } fman_pcd_fe_profiles[] = {
	{ 14 }, { 50 },
};
static struct fman_pcd_ehash_table template_table, new_table;
static int allocation_error, allocations, frees;
static unsigned long freed_off[3];
static unsigned int freed_size[3];

#define list_first_entry_or_null(list, type, member) ((list)->first)
#define list_last_entry(list, type, member) ((list)->last)

static unsigned int fman_pcd_fe_port_profile(struct fman_pcd *pcd, u8 port)
{
	assert(port == 0x10);
	return pcd->profile;
}

static int fman_pcd_ehash_table_set(struct fman_pcd *pcd, u32 mask,
				    u8 key_size, u8 shift)
{
	assert(mask == 0x7fff && shift == 0);
	assert(key_size == fman_pcd_fe_profiles[pcd->profile].key_size);
	allocations++;
	if (!allocation_error)
		pcd->fe_ehash_tables.last = &new_table;
	return allocation_error;
}

static void fman_pcd_muram_free(struct fman_pcd *pcd, unsigned long off,
				unsigned int size)
{
	assert(frees < 3);
	freed_off[frees] = off;
	freed_size[frees++] = size;
}

#include "arm_table_fragment.h"

int main(void)
{
	struct fman_pcd pcd;
	struct fman_pcd_ehash_table *chosen;
	static const int errors[] = { -ENOMEM, -EIO };

	for (unsigned int profile = 0; profile < 2; profile++) {
		for (unsigned int i = 0; i < sizeof(errors) / sizeof(errors[0]); i++) {
			pcd = (struct fman_pcd) {
				.fe_ehash_tables = { &template_table, &template_table },
				.profile = profile,
			};
			chosen = &template_table;
			allocation_error = errors[i];
			allocations = frees = 0;
			assert(prepare_port_table(&pcd, 0x10, 0x100, 0x200,
						  0x300, &chosen) == errors[i]);
			assert(allocations == 1 && frees == 3);
			assert(freed_off[0] == 0x100 && freed_size[0] == 256);
			assert(freed_off[1] == 0x200 && freed_size[1] == 64);
			assert(freed_off[2] == 0x300 && freed_size[2] == 32);
			assert(!pcd.fe_arming_v4_table && chosen == &template_table);
		}

		pcd = (struct fman_pcd) {
			.fe_ehash_tables = { &template_table, &template_table },
			.profile = profile,
		};
		chosen = NULL;
		allocation_error = allocations = frees = 0;
		assert(!prepare_port_table(&pcd, 0x10, 0x100, 0x200,
					   0x300, &chosen));
		assert(allocations == 1 && !frees);
		assert(chosen == &new_table && pcd.fe_arming_v4_table == chosen);
		assert(pcd.fe_arming_port_id == 0x10);
	}
	puts("FE arm: per-port allocation failures unwind without template fallback");
	return 0;
}
