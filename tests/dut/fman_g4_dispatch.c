// SPDX-License-Identifier: GPL-2.0
/* Host ASan/UBSan behavioral model for F-323 (G4 multi-table dispatch).
 *
 * Compiles the exact extracted F-323 helpers from fman_pcd.c and fman_pcd_kg.c
 * against mocked FMan KeyGen, Parser RAM, HWP SSA, MURAM scaffold, and ehash
 * tables to verify:
 *  1. Top-down KG scheme allocation (31 down, strictly > primary scheme id).
 *  2. 4-word position-independent soft-parser code generation & custom patch.
 *  3. HXS mutual exclusion against F-266 punt slots (5/6/10) and duplicate G4.
 *  4. Configure-before-enable attach order (table -> MURAM node+readback ->
 *     KG scheme+readback+bind -> parser RAM -> HXS attach) and clean unwind on
 *     every failure point.
 *  5. Fail-closed detach order (preflight -EBUSY -> HXS disarm -> SYNC #1 ->
 *     parser restore -> unbind+all-zero scheme clear -> SYNC #2 -> MURAM zero
 *     -> table free) and retention on SYNC failure.
 *  6. Byte-exact S1->S0 restoration (parser RAM, HXS SSA, KG scheme all-zeros,
 *     MURAM scaffold row zero, port partition bitmap).
 */
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
typedef uint64_t dma_addr_t;

#define FM_KG_MAX_NUM_OF_SCHEMES 32
#define FM_KG_NUM_OF_GENERIC_REGS 8
#define HWP_HXS_COUNT 16
#define BIT(n) (1UL << (n))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
#define pr_info(...) ((void)0)
#define pr_warn(...) ((void)0)
#define pr_warn_ratelimited(...) ((void)0)
#define pr_err(...) ((void)0)
#define dev_info(...) ((void)0)
#define dev_warn(...) ((void)0)
#define dev_err(...) ((void)0)
#define EXPORT_SYMBOL_GPL(sym)
#define __iomem

struct mutex { bool held; };
typedef struct { bool held; } spinlock_t;
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = true; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = false; }
static void spin_lock(spinlock_t *s) { assert(!s->held); s->held = true; }
static void spin_unlock(spinlock_t *s) { assert(s->held); s->held = false; }

struct list_head {
	struct list_head *next, *prev;
};
static inline void INIT_LIST_HEAD(struct list_head *h) { h->next = h; h->prev = h; }
static inline bool list_empty(const struct list_head *h) { return h->next == h; }
static inline void list_add_tail(struct list_head *n, struct list_head *h)
{
	n->next = h;
	n->prev = h->prev;
	h->prev->next = n;
	h->prev = n;
}
static inline void list_del(struct list_head *n)
{
	n->prev->next = n->next;
	n->next->prev = n->prev;
	n->next = n->prev = NULL;
}
#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))
#define list_for_each_entry(pos, head, member) \
	for (pos = container_of((head)->next, typeof(*pos), member); \
	     &pos->member != (head); \
	     pos = container_of(pos->member.next, typeof(*pos), member))

static inline bool test_bit(int nr, const unsigned long *addr)
{
	return (addr[nr / 64] & (1UL << (nr % 64))) != 0;
}
static inline void set_bit(int nr, unsigned long *addr)
{
	addr[nr / 64] |= (1UL << (nr % 64));
}
static inline void clear_bit(int nr, unsigned long *addr)
{
	addr[nr / 64] &= ~(1UL << (nr % 64));
}
#define DECLARE_BITMAP(name, bits) unsigned long name[((bits) + 63) / 64]

static u32 mock_muram[0x4000];
static u32 mock_prs_ram[0x200]; /* 2 KiB = 512 words */

static u32 ioread32be(const void *addr)
{
	return *(const u32 *)addr;
}
static void iowrite32be(u32 val, void *addr)
{
	*(u32 *)addr = val;
}

#define FMAN_PCD_FE_MAX_CLASSES		3
#define FMAN_PCD_FE_CLASS_CODE_MAX	16
#define FMAN_PCD_FE_CLASS_MAC_MAX	2
#define FMAN_PCD_FE_CLASS_MAC_WORDS	9
#define FMAN_EHASH_MASK_MAX		0x7fffU

struct fman_pcd_fe_class_cfg {
	u8   hxs_slot;
	u8   key_size;
	u32  hash_mask;
	u8   hash_shift;
	bool custom_gec;
	u32  ekfc;
	u32  gec[8];
	u8   code_words;
	u8   patch_word_idx;
	u32  code[FMAN_PCD_FE_CLASS_CODE_MAX];
	u8   mac_exclude_cnt;
	u8   mac_exclude[FMAN_PCD_FE_CLASS_MAC_MAX][6];
};

struct keygen_scheme {
	bool used;
	u8 hw_port_id;
	u32 base_fqid;
	u32 hash_fqid_count;
	bool use_hashing;
	bool symmetric_hash;
	u32 ekfc;
	u8 hashShift;
	u32 match_vector;
	u8 next_engine;
	u8 policer_profile_id;
	u32 cc_base_offset;
	u32 cc_bits_sel;
	bool ekfc_only;
	bool gec_dual_lane;
	bool gec_keep_ekfc;
	bool custom_gec;
	u32 gec[8];
};

struct fman_keygen {
	struct keygen_scheme schemes[FM_KG_MAX_NUM_OF_SCHEMES];
	u32 hw_sp[0x28];
	u32 hw_spc[FM_KG_MAX_NUM_OF_SCHEMES];
	bool hw_cleared[FM_KG_MAX_NUM_OF_SCHEMES];
};

struct fman_port {
	u8 port_id;
	u32 ssa[HWP_HXS_COUNT];
};

struct muram_info { int dummy; };
struct fman {
	void *dev;
	void *hwp_regs;
	struct fman_keygen *keygen;
	struct muram_info muram;
	struct fman_port ports[0x28];
	struct fman_pcd *pcd;
};

struct fman_pcd_ehash_table {
	struct list_head node;
	struct list_head flows;
	struct list_head quarantined_flows;
	struct list_head quarantined_cu;
	dma_addr_t table_dma;
	u32 hash_mask;
	u8 key_size;
	u8 hash_shift;
	u8 hash_mask_bits;
	bool poisoned;
	bool draining;
	bool per_port;
};

struct fman_pcd_fe_class {
	bool active;
	u8   hxs_slot;
	u8   scheme_id;
	u8   code_words;
	u16  prs_off;
	u32  saved_ssa;
	u32  prev_code[FMAN_PCD_FE_CLASS_CODE_MAX];
	struct fman_pcd_ehash_table *table;
	bool mac_filter;
	u8   mac_exclude_cnt;
	u8   mac_exclude[FMAN_PCD_FE_CLASS_MAC_MAX][6];
};

struct fman_pcd_fe_port {
	struct list_head node;
	u8 port_id;
	struct fman_pcd_ehash_table *table_v4;
	unsigned long scaffold_gro;
	struct fman_pcd_fe_class classes[FMAN_PCD_FE_MAX_CLASSES];
};

struct fman_pcd {
	struct fman *fman;
	struct mutex lock;
	struct mutex fe_lock;
	spinlock_t fe_stats_lock;
	unsigned long fe_port_armed[1];
	unsigned long fe_port_faulted[1];
	unsigned long fe_int_buf_off;
	struct list_head fe_ehash_tables;
	struct list_head fe_ports;
};

/* Event trace for verifying strict attach/detach ordering */
enum ev_kind {
	EV_TBL_ALLOC = 1, EV_MURAM_NODE, EV_KG_ADD, EV_PRS_WRITE, EV_SP_ATTACH,
	EV_SP_DISARM, EV_SYNC, EV_PRS_RESTORE, EV_KG_DEL, EV_MURAM_ZERO, EV_TBL_FREE
};
static enum ev_kind trace[64];
static int trace_n;
static void log_ev(enum ev_kind e) { assert(trace_n < 64); trace[trace_n++] = e; }

enum fail_point {
	FAIL_NONE = 0, FAIL_KG_SETUP, FAIL_KG_SETUP_AND_CLEAR,
	FAIL_KG_BIND, FAIL_KG_BIND_AND_CLEAR, FAIL_PRS_WRITE,
	FAIL_SP_ATTACH, FAIL_SP_ATTACH_AND_DEL, FAIL_SYNC1, FAIL_SYNC2,
	FAIL_SP_DISARM
};
static enum fail_point inject_fail;
static int sync_count;
static bool mock_wants_v6;
static bool mock_primary_conflict;
static u32 mock_miss_fqid = 0x200;

static bool fman_pcd_port_wants_v6(struct fman_pcd *pcd, u8 id)
{
	(void)pcd;
	(void)id;
	return mock_wants_v6;
}

static bool fman_pcd_fe_primary_has_hxs_conflict(struct fman_pcd_fe_port *fp,
						 u8 hxs_slot)
{
	(void)fp;
	(void)hxs_slot;
	return mock_primary_conflict;
}

static struct fman *fman_pcd_get_fman(struct fman_pcd *pcd) { return pcd->fman; }
static struct mutex *fman_pcd_get_lock(struct fman_pcd *pcd) { return &pcd->lock; }
static struct fman_pcd *fman_get_pcd(struct fman *fm) { return fm ? fm->pcd : NULL; }
static struct muram_info *fman_get_muram(struct fman *fm) { return &fm->muram; }
static void *fman_muram_offset_to_vbase(struct muram_info *m, unsigned long off)
{
	(void)m;
	assert((off % 4) == 0 && off / 4 < ARRAY_SIZE(mock_muram));
	return &mock_muram[off / 4];
}
static struct fman_port *fman_port_lookup_rx(struct fman *fm, u8 id)
{
	return (id >= 0x08 && id < 0x28) ? &fm->ports[id] : NULL;
}
static u32 fman_pcd_resolve_miss_fqid(struct fman_pcd *pcd, u8 id)
{
	(void)pcd;
	if (!mock_miss_fqid)
		return 0;
	return (id == 0x10) ? mock_miss_fqid : 0x300;
}

static struct fman_pcd_fe_port *
fman_pcd_fe_port_find(struct fman_pcd *pcd, u8 port_id)
{
	struct fman_pcd_fe_port *fp;
	list_for_each_entry(fp, &pcd->fe_ports, node)
		if (fp->port_id == port_id)
			return fp;
	return NULL;
}

static int fman_pcd_fe_flow_ready_locked(struct fman_pcd *pcd, u8 port_id)
{
	struct fman_pcd_fe_port *fp;
	if (port_id < 0x08 || port_id >= 0x28)
		return -EINVAL;
	if (test_bit(port_id, pcd->fe_port_faulted))
		return -ESHUTDOWN;
	if (!test_bit(port_id, pcd->fe_port_armed))
		return -ENODEV;
	fp = fman_pcd_fe_port_find(pcd, port_id);
	return (fp && fp->table_v4) ? 0 : -ENODEV;
}

static struct keygen_scheme *
kg_find_port_scheme(struct fman_keygen *kg, u8 hw_port_id, u8 *id_out)
{
	for (int i = 0; i < FM_KG_MAX_NUM_OF_SCHEMES; i++) {
		if (kg->schemes[i].used && kg->schemes[i].hw_port_id == hw_port_id) {
			*id_out = (u8)i;
			return &kg->schemes[i];
		}
	}
	return NULL;
}

static int keygen_scheme_setup_verified(struct fman_keygen *kg, u8 sid)
{
	kg->schemes[sid].used = true;
	kg->hw_cleared[sid] = false;
	if (inject_fail == FAIL_KG_SETUP ||
	    inject_fail == FAIL_KG_SETUP_AND_CLEAR)
		return -EIO;
	return 0;
}

static int keygen_scheme_clear(struct fman_keygen *kg, u8 sid)
{
	if (inject_fail == FAIL_KG_SETUP_AND_CLEAR ||
	    inject_fail == FAIL_KG_BIND_AND_CLEAR)
		return -EIO;
	memset(&kg->schemes[sid], 0, sizeof(kg->schemes[sid]));
	kg->hw_cleared[sid] = true;
	kg->hw_spc[sid] = 0;
	return 0;
}

static int keygen_scheme_read_spc(struct fman_keygen *kg, u8 sid, u32 *spc)
{
	*spc = kg->hw_spc[sid];
	return 0;
}

static int keygen_bind_port_to_schemes(struct fman_keygen *kg, u8 sid, bool bind)
{
	if (bind && (inject_fail == FAIL_KG_BIND ||
		     inject_fail == FAIL_KG_BIND_AND_CLEAR))
		return -EIO;
	if (!bind && inject_fail == FAIL_SP_ATTACH_AND_DEL)
		return -EIO;
	u8 p = kg->schemes[sid].hw_port_id;
	u32 bit = 1U << (31 - sid);
	if (bind)
		kg->hw_sp[p] |= bit;
	else
		kg->hw_sp[p] &= ~bit;
	return 0;
}

static int fman_pcd_ehash_table_set(struct fman_pcd *pcd, u32 mask,
				    u8 key_size, u8 hash_shift)
{
	if (mask == 0 || ((mask + 1) & mask) != 0)
		return -EINVAL;
	struct fman_pcd_ehash_table *t = calloc(1, sizeof(*t));
	assert(t);
	INIT_LIST_HEAD(&t->flows);
	INIT_LIST_HEAD(&t->quarantined_flows);
	INIT_LIST_HEAD(&t->quarantined_cu);
	t->hash_mask = mask;
	t->key_size = key_size;
	t->hash_shift = hash_shift;
	t->hash_mask_bits = (u8)__builtin_ctz(mask + 1);
	t->table_dma = 0x88000000ULL + (u64)key_size * 0x10000ULL;
	spin_lock(&pcd->fe_stats_lock);
	list_add_tail(&t->node, &pcd->fe_ehash_tables);
	spin_unlock(&pcd->fe_stats_lock);
	log_ev(EV_TBL_ALLOC);
	return 0;
}

static int fman_pcd_ehash_table_free(struct fman_pcd *pcd,
				     struct fman_pcd_ehash_table *t)
{
	struct fman_pcd_fe_port *fp;
	spin_lock(&pcd->fe_stats_lock);
	list_del(&t->node);
	list_for_each_entry(fp, &pcd->fe_ports, node) {
		if (fp->table_v4 == t)
			fp->table_v4 = NULL;
		for (int c = 0; c < FMAN_PCD_FE_MAX_CLASSES; c++)
			if (fp->classes[c].table == t)
				fp->classes[c].table = NULL;
	}
	spin_unlock(&pcd->fe_stats_lock);
	free(t);
	log_ev(EV_TBL_FREE);
	return 0;
}

static int fman_pcd_fe_teardown_sync(struct fman_pcd *pcd)
{
	(void)pcd;
	sync_count++;
	log_ev(EV_SYNC);
	if (inject_fail == FAIL_SYNC1 && sync_count == 1)
		return -ETIMEDOUT;
	if (inject_fail == FAIL_SYNC2 && sync_count == 2)
		return -ETIMEDOUT;
	return 0;
}

static struct mutex fman_pcd_punt_lock;
static u16 mock_punt_hxs[0x28];

static bool fman_pcd_punt_hxs_busy_locked(u8 hw_port_id, u8 hxs_slot)
{
	return (mock_punt_hxs[hw_port_id] & BIT(hxs_slot)) != 0;
}

static int fman_pcd_prs_write(struct fman *fm, unsigned int off,
			      const u32 *code, u32 *prev, unsigned int n)
{
	(void)fm;
	if (prev && inject_fail == FAIL_PRS_WRITE)
		return -ETIMEDOUT;
	assert((off % 4) == 0 && (off / 4) + n <= ARRAY_SIZE(mock_prs_ram));
	if (prev) {
		for (unsigned int i = 0; i < n; i++)
			prev[i] = mock_prs_ram[off / 4 + i];
		log_ev(EV_PRS_WRITE);
	} else {
		log_ev(EV_PRS_RESTORE);
	}
	for (unsigned int i = 0; i < n; i++)
		mock_prs_ram[off / 4 + i] = code[i];
	return 0;
}

static int fman_port_sp_attach(struct fman_port *port, u32 hxs_slot, u32 label,
			       u32 *saved_ssa)
{
	if (inject_fail == FAIL_SP_ATTACH ||
	    inject_fail == FAIL_SP_ATTACH_AND_DEL)
		return -EIO;
	*saved_ssa = port->ssa[hxs_slot];
	port->ssa[hxs_slot] = (*saved_ssa & ~0x7ffU) | 0x400U | (label & 0x3ffU);
	log_ev(EV_SP_ATTACH);
	return 0;
}

static int fman_port_sp_disarm(struct fman_port *port, u32 hxs_slot,
			       u32 saved_ssa)
{
	if (inject_fail == FAIL_SP_DISARM)
		return -EIO;
	port->ssa[hxs_slot] = saved_ssa;
	log_ev(EV_SP_DISARM);
	return 0;
}

#define list_last_entry(ptr, type, member) \
	container_of((ptr)->prev, type, member)

#include "g4_kg_body.h"
#include "g4_pcd_body.h"

static void reset_env(struct fman *fm, struct fman_keygen *kg,
		      struct fman_pcd *pcd, struct fman_pcd_fe_port *fp,
		      struct fman_pcd_ehash_table *primary_tbl)
{
	memset(mock_muram, 0, sizeof(mock_muram));
	memset(mock_prs_ram, 0, sizeof(mock_prs_ram));
	memset(fm, 0, sizeof(*fm));
	memset(kg, 0, sizeof(*kg));
	memset(pcd, 0, sizeof(*pcd));
	memset(fp, 0, sizeof(*fp));
	memset(primary_tbl, 0, sizeof(*primary_tbl));
	memset(mock_punt_hxs, 0, sizeof(mock_punt_hxs));
	memset(fman_pcd_g4_prs_slots, 0, sizeof(fman_pcd_g4_prs_slots));
	memset(fman_pcd_g4_hxs_mask, 0, sizeof(fman_pcd_g4_hxs_mask));
	trace_n = 0;
	inject_fail = FAIL_NONE;
	sync_count = 0;
	mock_wants_v6 = false;
	mock_primary_conflict = false;
	mock_miss_fqid = 0x200;

	fm->dev = (void *)1;
	fm->hwp_regs = (void *)1;
	fm->keygen = kg;
	fm->pcd = pcd;
	for (int p = 0; p < 0x28; p++) {
		fm->ports[p].port_id = (u8)p;
		fm->ports[p].ssa[10] = 0x80000000U;
		fm->ports[p].ssa[11] = 0x80000000U;
	}
	pcd->fman = fm;
	pcd->fe_int_buf_off = 0x4bf00;
	INIT_LIST_HEAD(&pcd->fe_ehash_tables);
	INIT_LIST_HEAD(&pcd->fe_ports);

	/* Simulate eth3 (0x10) armed on primary scheme 3, row 0 */
	kg->schemes[3].used = true;
	kg->schemes[3].hw_port_id = 0x10;
	kg->schemes[3].base_fqid = 0x200;
	kg->schemes[3].next_engine = 3;
	kg->schemes[3].cc_base_offset = 0;
	kg->hw_sp[0x10] = 1U << (31 - 3);

	INIT_LIST_HEAD(&primary_tbl->flows);
	INIT_LIST_HEAD(&primary_tbl->quarantined_flows);
	INIT_LIST_HEAD(&primary_tbl->quarantined_cu);
	primary_tbl->per_port = true;
	primary_tbl->key_size = 50;
	list_add_tail(&primary_tbl->node, &pcd->fe_ehash_tables);

	fp->port_id = 0x10;
	fp->table_v4 = primary_tbl;
	fp->scaffold_gro = 0x400;
	list_add_tail(&fp->node, &pcd->fe_ports);
	set_bit(0x10, pcd->fe_port_armed);

	/* F-266 punt owns IPv4(5), IPv6(6), TCP(10) on eth3 */
	mock_punt_hxs[0x10] = BIT(5) | BIT(6) | BIT(10);
}

int main(void)
{
	struct fman fm;
	struct fman_keygen kg;
	struct fman_pcd pcd;
	struct fman_pcd_fe_port fp;
	struct fman_pcd_ehash_table primary_tbl;
	struct fman_pcd_fe_class_cfg cfg = {};
	u8 idx1 = 0, idx2 = 0, sid = 0;
	u32 spc = 0;

	/* 1. Happy path: attach UDP (slot 11, 46B) + ESP (slot 12, 38B), verify
	 * top-down scheme IDs (31, 30), parser words, MURAM nodes, spc read,
	 * and byte-exact detach restore. */
	reset_env(&fm, &kg, &pcd, &fp, &primary_tbl);
	cfg.hxs_slot = 11;
	cfg.key_size = 46;
	cfg.hash_mask = 0x3ff;
	cfg.custom_gec = true;
	cfg.gec[0] = 0x80F07B00;
	assert(fman_pcd_fe_class_attach(&fm, 0x10, &cfg, &idx1) == 0);
	assert(idx1 == 1);
	assert(fp.classes[0].active && fp.classes[0].scheme_id == 31);
	assert(kg.schemes[31].used && kg.schemes[31].cc_base_offset == 1);
	assert(kg.hw_sp[0x10] == ((1U << (31 - 3)) | (1U << (31 - 31))));
	/* Verify 4-word direct-dispatch code at parser byte 0x100 (label 0x80) */
	assert(fp.classes[0].prs_off == 0x100);
	assert(mock_prs_ram[0x100 / 4 + 0] == (0x095F0300U | 31));
	assert(mock_prs_ram[0x100 / 4 + 1] == 0x00480001U);
	assert(mock_prs_ram[0x100 / 4 + 2] == 0x1BFF0001U);
	assert(mock_prs_ram[0x100 / 4 + 3] == 0x1BFE0000U);
	/* Verify HWP SSA preserved short-pad bit 0x80000000 and set 0x480 */
	assert(fm.ports[0x10].ssa[11] == 0x80000480U);
	/* Verify MURAM row 1 (gro + 16) */
	assert((mock_muram[(0x400 + 16) / 4 + 0] >> 24) == ((2U << 6) | 46));
	assert(mock_muram[(0x400 + 16) / 4 + 3] == 0x200U);

	/* Second class on slot 12 (ESP, 38B) -> row 2, scheme 30, parser 0x110 */
	cfg.hxs_slot = 12;
	cfg.key_size = 38;
	assert(fman_pcd_fe_class_attach(&fm, 0x10, &cfg, &idx2) == 0);
	assert(idx2 == 2);
	assert(fp.classes[1].scheme_id == 30);
	assert(fp.classes[1].prs_off == 0x110);
	assert(mock_prs_ram[0x110 / 4 + 0] == (0x095F0300U | 30));
	assert(fm.ports[0x10].ssa[12] == 0x00000488U);

	/* Per-class spc counter read */
	kg.hw_spc[31] = 12345;
	assert(fman_pcd_fe_class_stats(&fm, 0x10, idx1, &sid, &spc) == 0);
	assert(sid == 31 && spc == 12345);

	/* 2. HXS conflict checks: F-266 slot 10 and duplicate G4 slot 11 -> -EBUSY */
	cfg.hxs_slot = 10;
	assert(fman_pcd_fe_class_attach(&fm, 0x10, &cfg, &idx2) == -EBUSY);
	cfg.hxs_slot = 11;
	assert(fman_pcd_fe_class_attach(&fm, 0x10, &cfg, &idx2) == -EBUSY);

	/* 3. Detach refuses live flows (-EBUSY) */
	struct list_head fake_flow;
	list_add_tail(&fake_flow, &fp.classes[0].table->flows);
	assert(fman_pcd_fe_class_detach(&fm, 0x10, idx1) == -EBUSY);
	list_del(&fake_flow);

	/* 4. Clean detach restores everything byte-exact in strict order */
	trace_n = 0;
	assert(fman_pcd_fe_class_detach(&fm, 0x10, idx1) == 0);
	assert(!fp.classes[0].active && !fp.classes[0].table);
	assert(fm.ports[0x10].ssa[11] == 0x80000000U);
	assert(mock_prs_ram[0x100 / 4 + 0] == 0 && mock_prs_ram[0x100 / 4 + 3] == 0);
	assert(kg.hw_cleared[31] && !kg.schemes[31].used);
	assert(mock_muram[(0x400 + 16) / 4 + 0] == 0 && mock_muram[(0x400 + 16) / 4 + 3] == 0);
	/* Verify HXS disarm precedes SYNC #1, which precedes parser/KG/MURAM/table */
	assert(trace[0] == EV_SP_DISARM);
	assert(trace[1] == EV_SYNC);
	assert(trace[2] == EV_PRS_RESTORE);
	assert(trace[3] == EV_SYNC);
	assert(trace[4] == EV_TBL_FREE);

	assert(fman_pcd_fe_class_detach(&fm, 0x10, 2) == 0);
	assert(kg.hw_sp[0x10] == (1U << (31 - 3)));

	/* 5. Attach unwind on SP_ATTACH failure leaves zero leaked state */
	reset_env(&fm, &kg, &pcd, &fp, &primary_tbl);
	inject_fail = FAIL_SP_ATTACH;
	cfg.hxs_slot = 11;
	cfg.key_size = 46;
	assert(fman_pcd_fe_class_attach(&fm, 0x10, &cfg, &idx1) == -EIO);
	assert(!fp.classes[0].active && !fp.classes[0].table);
	assert(kg.hw_cleared[31] && !kg.schemes[31].used);
	assert(kg.hw_sp[0x10] == (1U << (31 - 3)));
	assert(mock_prs_ram[0x100 / 4 + 0] == 0);
	assert(mock_muram[(0x400 + 16) / 4 + 0] == 0);

	/* 6. Detach fail-closed on SYNC #1 timeout retains table/scheme/MURAM */
	reset_env(&fm, &kg, &pcd, &fp, &primary_tbl);
	cfg.hxs_slot = 11;
	cfg.key_size = 46;
	assert(fman_pcd_fe_class_attach(&fm, 0x10, &cfg, &idx1) == 0);
	inject_fail = FAIL_SYNC1;
	assert(fman_pcd_fe_class_detach(&fm, 0x10, idx1) == -ETIMEDOUT);
	assert(fp.classes[0].active && fp.classes[0].table != NULL);
	assert(kg.schemes[31].used);
	assert(test_bit(0x10, pcd.fe_port_faulted));
	/* Clean up allocated table for ASan */
	fman_pcd_ehash_table_free(&pcd, fp.classes[0].table);

	/* 7. Attach fail-closed when unwind KG delete fails: faults port & retains table */
	reset_env(&fm, &kg, &pcd, &fp, &primary_tbl);
	inject_fail = FAIL_SP_ATTACH_AND_DEL;
	cfg.hxs_slot = 11;
	cfg.key_size = 49;
	assert(fman_pcd_fe_class_attach(&fm, 0x10, &cfg, &idx1) == -EIO);
	assert(test_bit(0x10, pcd.fe_port_faulted));
	assert(kg.schemes[31].used);
	assert(mock_muram[(0x400 + 16) / 4 + 0] != 0);
	/* Clean up retained table for ASan */
	fman_pcd_ehash_table_free(&pcd, list_last_entry(&pcd.fe_ehash_tables,
							struct fman_pcd_ehash_table, node));

	/* 8. Validation guards: wants_v6, table-0 conflict, miss_fqid==0, all-zero custom_gec, hash_shift>3 */
	reset_env(&fm, &kg, &pcd, &fp, &primary_tbl);
	cfg.hxs_slot = 11;
	cfg.key_size = 49;
	cfg.hash_shift = 4;
	assert(fman_pcd_fe_class_attach(&fm, 0x10, &cfg, &idx1) == -EINVAL);
	cfg.hash_shift = 0;
	cfg.gec[0] = 0;
	assert(fman_pcd_fe_class_attach(&fm, 0x10, &cfg, &idx1) == -EINVAL);
	cfg.gec[0] = 0x80F07B00;
	mock_wants_v6 = true;
	assert(fman_pcd_fe_class_attach(&fm, 0x10, &cfg, &idx1) == -EBUSY);
	mock_wants_v6 = false;
	mock_primary_conflict = true;
	assert(fman_pcd_fe_class_attach(&fm, 0x10, &cfg, &idx1) == -EBUSY);
	mock_primary_conflict = false;
	mock_miss_fqid = 0;
	assert(fman_pcd_fe_class_attach(&fm, 0x10, &cfg, &idx1) == -EIO);

	/* 9. Attach fail-closed when KG setup/bind fails AND scheme clear fails:
	 * must fault port and retain MURAM row + DDR table + parser slot so
	 * a live enabled KG scheme cannot reference a freed table. */
	reset_env(&fm, &kg, &pcd, &fp, &primary_tbl);
	inject_fail = FAIL_KG_SETUP_AND_CLEAR;
	cfg.hxs_slot = 11;
	cfg.key_size = 49;
	assert(fman_pcd_fe_class_attach(&fm, 0x10, &cfg, &idx1) == -EIO);
	assert(test_bit(0x10, pcd.fe_port_faulted));
	assert(kg.schemes[31].used);
	assert(mock_muram[(0x400 + 16) / 4 + 0] != 0);
	assert(list_last_entry(&pcd.fe_ehash_tables,
			       struct fman_pcd_ehash_table, node) != &primary_tbl);
	fman_pcd_ehash_table_free(&pcd, list_last_entry(&pcd.fe_ehash_tables,
							struct fman_pcd_ehash_table, node));

	reset_env(&fm, &kg, &pcd, &fp, &primary_tbl);
	inject_fail = FAIL_KG_BIND_AND_CLEAR;
	cfg.hxs_slot = 11;
	cfg.key_size = 49;
	assert(fman_pcd_fe_class_attach(&fm, 0x10, &cfg, &idx1) == -EIO);
	assert(test_bit(0x10, pcd.fe_port_faulted));
	assert(kg.schemes[31].used);
	assert(mock_muram[(0x400 + 16) / 4 + 0] != 0);
	assert(list_last_entry(&pcd.fe_ehash_tables,
			       struct fman_pcd_ehash_table, node) != &primary_tbl);
	fman_pcd_ehash_table_free(&pcd, list_last_entry(&pcd.fe_ehash_tables,
							struct fman_pcd_ehash_table, node));

	printf("fman_g4_dispatch: all checks passed\n");
	return 0;
}
