/* Host fault tests for generated FE arm and workspace publication. */
#include <arpa/inet.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;

#define __iomem
#define GFP_KERNEL 0
#define FMAN_PP_FE_MGMT_IDX_OFF 0x54
#define FMAN_PP_FE_DEPL_CNT_OFF 0x58
#define FMAN_PCD_FE_PARAMS_PAGE_SIZE 256
#define FMAN_PCD_BMI_FIFO_UNITS 0x100
#define FMAN_PCD_FE_PROFILE_ROUTED 0
#define FMAN_PCD_FE_PROFILE_NR 2
#define IS_ERR_VALUE(value) ((unsigned long)(value) >= (unsigned long)-4095)
#define pr_err(...) ((void)0)
#define pr_warn(...) ((void)0)
#define pr_info(...) ((void)0)
#define dev_info(...) ((void)0)
#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))
#define list_last_entry(head, type, member) \
	container_of((head)->prev, type, member)
#define CHECK(expr) do { \
	if (!(expr)) { \
		fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #expr); \
		return 1; \
	} \
} while (0)

enum fman_pcd_fe_profile { ROUTED, BRIDGE };

struct list_head {
	struct list_head *next, *prev;
};

struct muram_info {
	u8 bytes[0x20000];
};

struct gen_pool {
	int unused;
};

struct fman_port {
	u32 page;
	u8 id, tnums;
	bool present;
};

struct fman {
	struct muram_info *muram;
	struct fman_port ports[0x28];
	struct fman_pcd *pcd;
};

struct fman_pcd_ehash_table {
	struct list_head node;
	u64 table_dma;
	u8 key_size, hash_shift, hash_mask_bits;
};

struct fman_pcd_fe_port {
	struct list_head node;
	u8 port_id;
	struct fman_pcd_ehash_table *table_v4;
	unsigned long pool_raw_off, mgmt_off;
	size_t pool_raw_size, mgmt_size;
	unsigned long scaffold_gro, scaffold_mto, scaffold_ato;
};

struct fman_pcd {
	struct fman *fman;
	struct gen_pool *muram_pool;
	struct list_head fe_ports, fe_ehash_tables, fe_enq;
	struct fman_pcd_ehash_table *fe_arming_v4_table;
	unsigned long fe_scaffold_gro, fe_scaffold_mto, fe_scaffold_ato;
	unsigned long fe_int_buf_off;
	unsigned long fe_port_armed[1], fe_port_faulted[1];
	u8 fe_port_profile[0x28], fe_arming_port_id;
	unsigned int fe_miss_fqid;
	u32 fe_enq_fqid;
	int fe_refcount;
	bool fe_vm_chain_built, fe_vm_chain_faulted, fe_obs_armed;
};

static const struct {
	u32 ekfc;
	bool ekfc_only;
	u8 key_size;
} fman_pcd_fe_profiles[FMAN_PCD_FE_PROFILE_NR] = {
	{ 1, false, 50 }, { 2, true, 14 },
};

enum failure {
	NONE, TABLE_ALLOC, POOL_ALLOC, INDEX_ALLOC, WRONG_PAGE,
	OCCUPIED_PAGE, COUNTER_WRITE, INDEX_WRITE, VERIFY, KG_EARLY,
	KG_LATE, ADVANCED,
};

static struct muram_info muram;
static struct gen_pool pool;
static struct fman fm;
static struct fman_pcd pcd;
static struct list_head enq_node;
static struct fman_pcd_fe_port owners[4];
static struct fman_pcd_ehash_table tables[4];
static enum failure failure;
static unsigned long next_offset;
static unsigned int allocations, frees, owner_allocations, owner_frees;
static unsigned int table_allocations, kg_calls, stop_calls, context_calls;
static unsigned int unsafe_dispatches, unowned_page_writes, page_writes;

static void init_list(struct list_head *head)
{
	head->next = head;
	head->prev = head;
}

static bool list_empty(const struct list_head *head)
{
	return head->next == head;
}

static void list_add_tail(struct list_head *node, struct list_head *head)
{
	node->prev = head->prev;
	node->next = head;
	head->prev->next = node;
	head->prev = node;
}

static bool test_bit(unsigned int bit, const unsigned long *map)
{
	return !!(*map & (1UL << bit));
}

static void set_bit(unsigned int bit, unsigned long *map)
{
	*map |= 1UL << bit;
}

static struct fman_pcd_fe_port *
fman_pcd_fe_port_find(struct fman_pcd *owner, u8 port)
{
	struct list_head *node;

	for (node = owner->fe_ports.next; node != &owner->fe_ports;
	     node = node->next) {
		struct fman_pcd_fe_port *entry =
			container_of(node, struct fman_pcd_fe_port, node);

		if (entry->port_id == port)
			return entry;
	}
	return NULL;
}

static struct fman *fman_pcd_get_fman(struct fman_pcd *owner)
{
	return owner->fman;
}

static struct fman_pcd *fman_get_pcd(struct fman *fman)
{
	return fman->pcd;
}

static struct muram_info *fman_get_muram(struct fman *fman)
{
	return fman ? fman->muram : NULL;
}

static void *fman_get_dev(struct fman *fman)
{
	return fman;
}

static struct fman_port *fman_port_lookup_rx(struct fman *fman, u8 id)
{
	return fman && id < 0x28 && fman->ports[id].present ?
		&fman->ports[id] : NULL;
}

static u32 fman_port_get_params_page(struct fman_port *port)
{
	return port->page;
}

static u8 fman_port_get_total_tnums(struct fman_port *port)
{
	return port->tnums;
}

static int fman_pcd_port_ensure_params_page(struct fman_pcd *owner,
					    struct fman_port *port)
{
	return port->page ? 0 : -ENXIO;
}

static bool gen_pool_has_addr(struct gen_pool *p, unsigned long off, size_t size)
{
	return p && failure != WRONG_PAGE &&
	       off >= 0x1000 && off + size <= sizeof(muram.bytes);
}

static void *kzalloc(size_t size, int flags)
{
	if (size != sizeof(owners[0]) || owner_allocations >= 4)
		abort();
	return &owners[owner_allocations++];
}

static void kfree(void *ptr)
{
	if (!ptr)
		abort();
	owner_frees++;
}

static unsigned long fman_pcd_muram_alloc(struct fman_pcd *owner, size_t size)
{
	unsigned long off;

	allocations++;
	if ((failure == POOL_ALLOC && allocations == 4) ||
	    (failure == INDEX_ALLOC && allocations == 5))
		return (unsigned long)-ENOMEM;
	off = next_offset;
	next_offset = (next_offset + size + 63) & ~63UL;
	if (next_offset > sizeof(muram.bytes))
		abort();
	return off;
}

static void fman_pcd_muram_free(struct fman_pcd *owner, unsigned long off,
				size_t size)
{
	if (!off || !size)
		abort();
	frees++;
}

static uintptr_t fman_muram_offset_to_vbase(struct muram_info *m,
					    unsigned long off)
{
	if (!m || off >= sizeof(m->bytes))
		abort();
	return (uintptr_t)&m->bytes[off];
}

static void memset_io(void *address, int value, size_t size)
{
	memset(address, value, size);
}

static u32 ioread32be(const void *address)
{
	u32 value;

	memcpy(&value, address, sizeof(value));
	return ntohl(value);
}

static u8 ioread8(const void *address)
{
	return *(const u8 *)address;
}

static void iowrite32be(u32 value, void *address)
{
	unsigned int id;

	for (id = 0x08; id < 0x28; id++) {
		struct fman_port *port = &fm.ports[id];
		u8 *page = muram.bytes + port->page;
		struct fman_pcd_fe_port *entry;
		bool index, counter;

		if (!port->present)
			continue;
		index = address == page + FMAN_PP_FE_MGMT_IDX_OFF;
		counter = address == page + FMAN_PP_FE_DEPL_CNT_OFF;
		if (!index && !counter)
			continue;
		page_writes++;
		entry = fman_pcd_fe_port_find(&pcd, id);
		if (!entry || !entry->table_v4 || !entry->mgmt_off ||
		    !entry->pool_raw_off || !entry->scaffold_gro)
			unowned_page_writes++;
		if ((index && failure == INDEX_WRITE) ||
		    (counter && failure == COUNTER_WRITE))
			return;
	}
	value = htonl(value);
	memcpy(address, &value, sizeof(value));
}

static void iowrite8(u8 value, void *address)
{
	*(u8 *)address = value;
}

static u8 fman_pcd_fe_port_profile(struct fman_pcd *owner, u8 id)
{
	return owner->fe_port_profile[id];
}

static int fman_pcd_ehash_table_set(struct fman_pcd *owner, u32 mask,
				    u8 key_size, u8 shift)
{
	struct fman_pcd_ehash_table *table;

	if (failure == TABLE_ALLOC)
		return -ENOMEM;
	if (table_allocations >= 4 || mask != 0x7fff)
		abort();
	table = &tables[table_allocations++];
	table->table_dma = 0x12340000 + table_allocations * 0x10000;
	table->key_size = key_size;
	table->hash_shift = shift;
	table->hash_mask_bits = 15;
	list_add_tail(&table->node, &owner->fe_ehash_tables);
	return 0;
}

static int fman_pcd_fe_verify_internal(struct fman_pcd *owner, u8 port)
{
	return failure == VERIFY ? -EPROTO : 0;
}

static int fman_port_disable(struct fman_port *port)
{
	stop_calls++;
	return 0;
}

static int fman_pcd_kg_port_arm_fe(struct fman_pcd *owner, u8 id,
				   u32 enter, u8 *saved, u32 ekfc,
				   bool *started)
{
	struct fman_pcd_fe_port *entry = fman_pcd_fe_port_find(owner, id);
	u8 *page = muram.bytes + fm.ports[id].page;

	kg_calls++;
	if (!entry || !entry->table_v4 || !entry->mgmt_off ||
	    entry->scaffold_gro != enter ||
	    ioread32be(page + FMAN_PP_FE_MGMT_IDX_OFF) != entry->mgmt_off ||
	    ioread32be(page + FMAN_PP_FE_DEPL_CNT_OFF) ||
	    owner->fe_arming_v4_table)
		unsafe_dispatches++;
	*started = failure != KG_EARLY;
	*saved = 0;
	if (failure == KG_EARLY || failure == KG_LATE)
		return -EIO;
	return 0;
}

static void fman_pcd_fe_build_contexts(struct fman_pcd *owner)
{
	context_calls++;
}

static int __fman_pcd_fe_build_vm_chain(struct fman_pcd *owner)
{
	owner->fe_refcount = 1;
	list_add_tail(&enq_node, &owner->fe_enq);
	return 0;
}

static unsigned int fman_pcd_resolve_miss_fqid(struct fman_pcd *owner, u8 id)
{
	return 0x200;
}

static int fman_pcd_fe_adv_offload(struct fman_pcd *owner, u8 id, bool enable)
{
	return failure == ADVANCED ? -EIO : 0;
}

#include "arm_workspace_body.h"

static void reset(void)
{
	memset(&muram, 0, sizeof(muram));
	memset(&fm, 0, sizeof(fm));
	memset(&pcd, 0, sizeof(pcd));
	memset(owners, 0, sizeof(owners));
	memset(tables, 0, sizeof(tables));
	fm.muram = &muram;
	fm.pcd = &pcd;
	fm.ports[0x10] = (struct fman_port) {
		.page = 0x1000, .id = 0x10, .tnums = 16, .present = true,
	};
	fm.ports[0x11] = (struct fman_port) {
		.page = 0x1200, .id = 0x11, .tnums = 16, .present = true,
	};
	pcd.fman = &fm;
	pcd.muram_pool = &pool;
	pcd.fe_int_buf_off = 0x3000;
	init_list(&pcd.fe_ports);
	init_list(&pcd.fe_ehash_tables);
	init_list(&pcd.fe_enq);
	init_list(&enq_node);
	next_offset = 0x4000;
	allocations = frees = owner_allocations = owner_frees = 0;
	table_allocations = kg_calls = stop_calls = context_calls = 0;
	unsafe_dispatches = unowned_page_writes = page_writes = 0;
	failure = NONE;
}

static void seed_page(u8 id, unsigned int offset, u32 value)
{
	u32 encoded = htonl(value);

	memcpy(muram.bytes + fm.ports[id].page + offset, &encoded, sizeof(encoded));
}

static bool workspace_valid(struct fman_pcd_fe_port *entry)
{
	unsigned long aligned;
	u8 *index, i;

	if (!entry || !entry->table_v4 || !entry->mgmt_off)
		return false;
	aligned = (entry->pool_raw_off + 0xff) & ~0xffUL;
	index = muram.bytes + entry->mgmt_off;
	if (index[0] != 4 ||
	    (ioread32be(index) & 0x00ffffff) != aligned ||
	    index[4 + fm.ports[entry->port_id].tnums] != 0xff)
		return false;
	for (i = 0; i < fm.ports[entry->port_id].tnums; i++)
		if (index[4 + i] != i)
			return false;
	return true;
}

int main(void)
{
	struct fman_pcd_fe_port *first, *second;
	static const enum failure failures[] = {
		TABLE_ALLOC, POOL_ALLOC, INDEX_ALLOC, WRONG_PAGE, OCCUPIED_PAGE,
		COUNTER_WRITE, INDEX_WRITE, VERIFY, KG_EARLY, KG_LATE, ADVANCED,
	};
	unsigned int i;

	reset();
	pcd.fe_obs_armed = true;
	CHECK(__fman_pcd_fe_engage_profile_locked(&fm, 0x10, 0, ROUTED) == -EBUSY);
	CHECK(!pcd.fe_vm_chain_faulted && !kg_calls && !allocations);

	reset();
	CHECK(!__fman_pcd_fe_engage_profile_locked(&fm, 0x10, 0, ROUTED));
	first = fman_pcd_fe_port_find(&pcd, 0x10);
	CHECK(kg_calls == 1 && !unsafe_dispatches && !unowned_page_writes);
	CHECK(workspace_valid(first) && test_bit(0x10, pcd.fe_port_armed));
	CHECK(!pcd.fe_arming_v4_table && page_writes == 2);
	CHECK(!__fman_pcd_fe_engage_profile_locked(&fm, 0x11, 0, BRIDGE));
	second = fman_pcd_fe_port_find(&pcd, 0x11);
	CHECK(kg_calls == 2 && !unsafe_dispatches && !unowned_page_writes);
	CHECK(workspace_valid(first) && workspace_valid(second));
	CHECK(first->table_v4 != second->table_v4 &&
	      first->mgmt_off != second->mgmt_off);

	for (i = 0; i < sizeof(failures) / sizeof(failures[0]); i++) {
		int rc;

		reset();
		failure = failures[i];
		if (failure == OCCUPIED_PAGE)
			seed_page(0x10, FMAN_PP_FE_MGMT_IDX_OFF, 0x9999);
		if (failure == COUNTER_WRITE)
			seed_page(0x10, FMAN_PP_FE_DEPL_CNT_OFF, 7);
		rc = __fman_pcd_fe_engage_profile_locked(&fm, 0x10, 0, ROUTED);
		CHECK(rc < 0 && !unsafe_dispatches && !unowned_page_writes);
		CHECK(!test_bit(0x10, pcd.fe_port_armed) ||
		      failure == ADVANCED);
		if (failure == KG_EARLY || failure == KG_LATE ||
		    failure == ADVANCED) {
			CHECK(kg_calls == 1 && workspace_valid(
				fman_pcd_fe_port_find(&pcd, 0x10)));
			CHECK(stop_calls == (failure == KG_EARLY ? 0U : 1U));
			CHECK(test_bit(0x10, pcd.fe_port_faulted));
			CHECK(!frees && !owner_frees);
		} else {
			CHECK(!kg_calls && !stop_calls);
		}
		if (failure == COUNTER_WRITE || failure == INDEX_WRITE) {
			CHECK(fman_pcd_fe_port_find(&pcd, 0x10));
			CHECK(test_bit(0x10, pcd.fe_port_faulted));
			CHECK(!frees && !owner_frees && !pcd.fe_arming_v4_table);
		}
		if (failure == WRONG_PAGE || failure == OCCUPIED_PAGE) {
			CHECK(!page_writes && pcd.fe_arming_v4_table);
			CHECK(pcd.fe_arming_port_id == 0x10);
			CHECK(test_bit(0x10, pcd.fe_port_faulted));
		}
		if (failure == OCCUPIED_PAGE)
			CHECK(ioread32be(muram.bytes + fm.ports[0x10].page +
					 FMAN_PP_FE_MGMT_IDX_OFF) == 0x9999);
		if (failure == POOL_ALLOC || failure == INDEX_ALLOC ||
		    failure == VERIFY) {
			CHECK(pcd.fe_arming_v4_table &&
			      pcd.fe_arming_port_id == 0x10);
			CHECK(test_bit(0x10, pcd.fe_port_faulted));
		}
	}
	puts("FE workspace: owner and verified page precede modeled KG dispatch");
	return 0;
}
