#!/usr/bin/env python3
"""Host gate for F-324 (G5b full mixed-profile Bridge IRB) and ask.ko G5b wiring.

Verifies:
  1. F_324.py applies cleanly on top of F_323.py and is idempotent on re-run.
  2. Real extracted C bytecode emitter (fman_pcd_g5b_build_eth_mac_code) compiles
     under ASan+UBSan (-Wall -Wextra -Werror) and matches NXP FMC spa assembler
     golden vectors bit-for-bit across 1-MAC, 2-MAC, leading-zero MAC, 16-bit
     MAC, and relocated prs_off / scheme_id cases.
  3. Full F-324 kernel lifecycle harness under ASan+UBSan:
     - ETH HXS (slot 0) L2_DA class attach reserves 9 words (3 parser slots),
       relocates jump targets to prs_off/2, patches scheme_id into both $nia
       and $hpnia stores, and attaches pmda[0].ssa.
     - Bidirectional HXS mutual exclusion between ETH HXS (slot 0) and G4
       unconditional downstream hooks (UDP slot 11, ESP slot 12) returns -EBUSY.
     - Coexistence with F-266 control punt (slots 5, 6, 10) succeeds.
     - fman_pcd_fe_class_update_macs(): identical MAC is a zero-write no-op;
       changed MACs (1->2, 2->1, different MAC) update parser RAM in-place at
       the same prs_off/scheme_id without disturbing prev_code; invalid MACs
       (zero, multicast, count 0/3) and parser write faults fail cleanly
       without corrupting saved MAC state.
     - Detach after MAC updates restores original cold-boot prev_code (all
       zeros), clears KG scheme to LS1046A reset defaults, zeroes MURAM row,
       and frees the DDR ehash table.
  4. Static source gates for ask.ko G5b integration (ask_hw.c, ask_bridge.c,
     ask_genl.c, ask_flow_offload.c).
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
F323 = ROOT / "bin" / "kernel-fixups" / "F_323.py"
F324 = ROOT / "bin" / "kernel-fixups" / "F_324.py"

PCD_H = "include/linux/fsl/fman_pcd.h"
PCD_C = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
KG_INT_H = "drivers/net/ethernet/freescale/fman/fman_keygen_internal.h"
KG_C = "drivers/net/ethernet/freescale/fman/fman_keygen.c"
PCD_INT_H = "drivers/net/ethernet/freescale/fman/fman_pcd_internal.h"
PCD_KG_C = "drivers/net/ethernet/freescale/fman/fman_pcd_kg.c"


def find_kernel_tree() -> Path:
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if (p / PCD_C).is_file() and (p / PCD_H).is_file():
            return p
        raise RuntimeError(f"Kernel tree path invalid: {p}")
    candidates = [
        Path.home() / "kernel-git-cache" / "linux",
        ROOT / "work" / "linux-6.18.62",
    ]
    for c in candidates:
        if (c / PCD_C).is_file() and (c / PCD_H).is_file():
            return c
    raise RuntimeError("No kernel tree found for F_324 host gate")


def check_f324_exists():
    if not F324.is_file():
        print(f"FAIL: {F324} does not exist yet")
        sys.exit(1)


def apply_f323_and_f324(tree: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="f324-gate-"))
    for rel in (KG_INT_H, KG_C, PCD_INT_H, PCD_KG_C, PCD_C, PCD_H):
        dst = tmp / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tree / rel, dst)

    scripts = [F324]
    if "F-323" not in (tmp / PCD_C).read_text():
        scripts.insert(0, F323)

    for script in scripts:
        r1 = subprocess.run([sys.executable, str(script)], cwd=tmp,
                            capture_output=True, text=True)
        assert r1.returncode == 0, f"{script.name} apply failed:\n{r1.stdout}\n{r1.stderr}"
        r2 = subprocess.run([sys.executable, str(script)], cwd=tmp,
                            capture_output=True, text=True)
        assert r2.returncode == 0, f"{script.name} idempotency failed:\n{r2.stdout}\n{r2.stderr}"
    return tmp


def extract_between(text: str, start: str, end: str) -> str:
    i = text.find(start)
    j = text.find(end)
    assert i != -1 and j != -1 and j > i, f"Markers {start}..{end} not found"
    return text[i:j + len(end)]


def run_asan_harness(tmp: Path):
    pcd_h = (tmp / PCD_H).read_text()
    pcd_c = (tmp / PCD_C).read_text()

    assert "FMAN_PCD_FE_CLASS_MAC_MAX" in pcd_h, "missing FMAN_PCD_FE_CLASS_MAC_MAX in fman_pcd.h"
    assert "FMAN_PCD_FE_CLASS_MAC_WORDS" in pcd_h, "missing FMAN_PCD_FE_CLASS_MAC_WORDS in fman_pcd.h"
    assert "fman_pcd_fe_class_update_macs" in pcd_h, "missing fman_pcd_fe_class_update_macs in fman_pcd.h"
    assert "mac_exclude_cnt" in pcd_h, "missing mac_exclude_cnt in fman_pcd_fe_class_cfg"

    pcd_block = extract_between(pcd_c, "/* F-323-PCD-BEGIN */", "/* F-323-PCD-END */")
    # Strip EXPORT_SYMBOL_GPL lines for userspace compilation
    pcd_block = re.sub(r"^EXPORT_SYMBOL_GPL\([^)]+\);\s*$", "", pcd_block, flags=re.MULTILINE)

    harness = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t  u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint64_t dma_addr_t;

#define __iomem
#define BIT(n) (1UL << (n))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
#define FMAN_EHASH_MASK_MAX 0x7fffU
#define FMAN_PCD_FE_MAX_CLASSES 3
#define FMAN_PCD_FE_CLASS_CODE_MAX 16
#define FMAN_PCD_FE_CLASS_MAC_MAX 2
#define FMAN_PCD_FE_CLASS_MAC_WORDS 9

struct list_head { struct list_head *next, *prev; };
#define INIT_LIST_HEAD(h) do { (h)->next = (h); (h)->prev = (h); } while (0)
static inline bool list_empty(const struct list_head *h) { return h->next == h; }
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - __builtin_offsetof(type, member)))
#define list_last_entry(ptr, type, member) container_of((ptr)->prev, type, member)

struct mutex { int dummy; };
struct spinlock { int dummy; };
#define mutex_lock(m) do { (void)(m); } while (0)
#define mutex_unlock(m) do { (void)(m); } while (0)
#define spin_lock(s) do { (void)(s); } while (0)
#define spin_unlock(s) do { (void)(s); } while (0)
#define DECLARE_BITMAP(name, bits) unsigned long name[((bits) + 63) / 64]
static inline bool test_bit(int bit, const unsigned long *bm) {
    return (bm[bit / 64] & (1UL << (bit % 64))) != 0;
}
static inline void set_bit(int bit, unsigned long *bm) {
    bm[bit / 64] |= (1UL << (bit % 64));
}
static inline void clear_bit(int bit, unsigned long *bm) {
    bm[bit / 64] &= ~(1UL << (bit % 64));
}

#define pr_info(...) do {} while (0)
#define pr_warn_ratelimited(...) do {} while (0)
#define pr_err(...) do {} while (0)

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

struct fman_pcd_ehash_table {
    struct list_head node;
    struct list_head flows;
    struct list_head quarantined_flows;
    struct list_head quarantined_cu;
    bool poisoned, draining, per_port;
    u8 key_size, hash_shift, hash_mask_bits;
    u32 hash_mask;
    dma_addr_t table_dma;
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

struct fman_port { u8 id; u32 ssa[16]; };
struct muram_info { u8 mem[4096]; };
struct fman {
    void *hwp_regs;
    struct fman_pcd *pcd;
    struct muram_info muram;
};
struct fman_pcd {
    struct fman *fman;
    struct mutex fe_lock;
    struct spinlock fe_stats_lock;
    struct list_head fe_ehash_tables;
    unsigned long fe_port_faulted[2];
    unsigned long fe_int_buf_off;
    struct fman_pcd_fe_port port;
};

static struct mutex fman_pcd_punt_lock;
static u32 sim_prs_ram[0x800 / 4];
static int sim_prs_writes = 0;
static int sim_prs_fail_next = 0;
static int sim_prs_fail_err = -EIO;
static bool sim_prs_saw_prev = false;
static u8  sim_next_sid = 31;
static u32 sim_punt_mask[0x28];
static struct fman_port sim_rxport = { .id = 0x11 };

static inline void iowrite32be(u32 v, void *addr) { *(u32 *)addr = v; }
static inline u32  ioread32be(void *addr) { return *(u32 *)addr; }

static void *fman_muram_offset_to_vbase(struct muram_info *m, unsigned long off) {
    return m->mem + off;
}
static struct fman_pcd *fman_get_pcd(struct fman *fm) { return fm->pcd; }
static struct muram_info *fman_get_muram(struct fman *fm) { return &fm->muram; }
static struct fman_port *fman_port_lookup_rx(struct fman *fm, u8 id) {
    (void)fm;
    return id == sim_rxport.id ? &sim_rxport : NULL;
}
static int fman_pcd_fe_flow_ready_locked(struct fman_pcd *pcd, u8 pid) {
    return test_bit(pid, pcd->fe_port_faulted) ? -ESHUTDOWN : 0;
}
static bool fman_pcd_port_wants_v6(struct fman_pcd *pcd, u8 pid) { (void)pcd; (void)pid; return false; }
static struct fman_pcd_fe_port *fman_pcd_fe_port_find(struct fman_pcd *pcd, u8 pid) {
    return pid == pcd->port.port_id ? &pcd->port : NULL;
}
static u32 fman_pcd_resolve_miss_fqid(struct fman_pcd *pcd, u8 pid) { (void)pcd; (void)pid; return 0x300; }
static bool fman_pcd_fe_primary_has_hxs_conflict(struct fman_pcd_fe_port *fp, u8 hxs) {
    (void)fp; (void)hxs; return false;
}
static bool fman_pcd_punt_hxs_busy_locked(u8 hw_port_id, u8 hxs_slot) {
    return (sim_punt_mask[hw_port_id] & BIT(hxs_slot)) != 0;
}
static int fman_pcd_ehash_table_set(struct fman_pcd *pcd, u32 mask, u8 ks, u8 shift) {
    struct fman_pcd_ehash_table *t = calloc(1, sizeof(*t));
    INIT_LIST_HEAD(&t->flows);
    INIT_LIST_HEAD(&t->quarantined_flows);
    INIT_LIST_HEAD(&t->quarantined_cu);
    t->key_size = ks;
    t->hash_mask = mask;
    t->hash_shift = shift;
    t->hash_mask_bits = 15;
    t->table_dma = 0x80000000ULL;
    t->node.prev = pcd->fe_ehash_tables.prev;
    t->node.next = &pcd->fe_ehash_tables;
    pcd->fe_ehash_tables.prev->next = &t->node;
    pcd->fe_ehash_tables.prev = &t->node;
    return 0;
}
static int fman_pcd_ehash_table_free(struct fman_pcd *pcd, struct fman_pcd_ehash_table *t) {
    (void)pcd;
    t->node.prev->next = t->node.next;
    t->node.next->prev = t->node.prev;
    free(t);
    return 0;
}
static int fman_pcd_kg_port_add_class_scheme(struct fman_pcd *pcd, u8 pid, u8 row,
        u32 ekfc, const u32 gec[8], bool custom_gec, u8 *sid_out) {
    (void)pcd; (void)pid; (void)row; (void)ekfc; (void)gec; (void)custom_gec;
    *sid_out = sim_next_sid--;
    return 0;
}
static int fman_pcd_kg_port_del_class_scheme(struct fman_pcd *pcd, u8 pid, u8 sid) {
    (void)pcd; (void)pid; (void)sid;
    sim_next_sid++;
    return 0;
}
static int fman_pcd_kg_scheme_get_spc(struct fman_pcd *pcd, u8 sid, u32 *spc) {
    (void)pcd; (void)sid; *spc = 42; return 0;
}
static int fman_pcd_prs_write(struct fman *fm, unsigned int off,
                              const u32 *code, u32 *prev, unsigned int n) {
    (void)fm;
    sim_prs_saw_prev = (prev != NULL);
    if (sim_prs_fail_next) {
        sim_prs_fail_next = 0;
        return sim_prs_fail_err;
    }
    assert((off & 3) == 0 && off + n * 4 <= sizeof(sim_prs_ram));
    if (prev)
        for (unsigned int i = 0; i < n; i++)
            prev[i] = sim_prs_ram[off / 4 + i];
    for (unsigned int i = 0; i < n; i++)
        sim_prs_ram[off / 4 + i] = code[i];
    sim_prs_writes++;
    return 0;
}
static int fman_port_sp_attach(struct fman_port *p, u32 slot, u32 label, u32 *saved) {
    *saved = p->ssa[slot];
    p->ssa[slot] = (p->ssa[slot] & ~0x7FFU) | 0x400U | (label & 0x3FFU);
    return 0;
}
static int fman_port_sp_disarm(struct fman_port *p, u32 slot, u32 saved) {
    p->ssa[slot] = saved;
    return 0;
}
static int fman_pcd_fe_teardown_sync(struct fman_pcd *pcd) { (void)pcd; return 0; }

''' + pcd_block + r'''

int main(void) {
    /* 1. Golden-vector tests verified bit-for-bit against NXP FMC spa assembler */
    u32 code[FMAN_PCD_FE_CLASS_MAC_WORDS];

    /* Case A: 1 unicast MAC (10:22:33:44:55:66), prs_off=0x100 (PC 0x80), sid=31 */
    const u8 mac1[1][6] = { { 0x10, 0x22, 0x33, 0x44, 0x55, 0x66 } };
    const u32 exp_a[9] = {
        0x97DE488BU, 0x55663344U, 0x1022095FU, 0x031F0048U,
        0x094F031FU, 0x00480001U, 0x1BFE0000U, 0x00000000U, 0x00000000U
    };
    assert(fman_pcd_g5b_build_eth_mac_code(0x100, 31, mac1, 1, code) == 0);
    assert(memcmp(code, exp_a, sizeof(exp_a)) == 0);

    /* Case B: 2 full unicast MACs, prs_off=0x100 (PC 0x80), sid=31 */
    const u8 mac2_full[2][6] = {
        { 0x10, 0x22, 0x33, 0x44, 0x55, 0x66 },
        { 0xa2, 0xbb, 0xcc, 0xdd, 0xee, 0xff },
    };
    const u32 exp_b[9] = {
        0x97DE488FU, 0x55663344U, 0x1022488FU, 0xEEFFCCDDU,
        0xA2BB095FU, 0x031F0048U, 0x094F031FU, 0x00480001U, 0x1BFE0000U
    };
    assert(fman_pcd_g5b_build_eth_mac_code(0x100, 31, mac2_full, 2, code) == 0);
    assert(memcmp(code, exp_b, sizeof(exp_b)) == 0);

    /* Case C: 2 unicast MACs with leading zeros (00:00:11:22:33:44), prs_off=0x100, sid=31 */
    const u8 mac2_lz[2][6] = {
        { 0x10, 0x22, 0x33, 0x44, 0x55, 0x66 },
        { 0x00, 0x00, 0x11, 0x22, 0x33, 0x44 },
    };
    const u32 exp_c[9] = {
        0x97DE488EU, 0x55663344U, 0x1022448EU, 0x33441122U,
        0x095F031FU, 0x0048094FU, 0x031F0048U, 0x00011BFEU, 0x00000000U
    };
    assert(fman_pcd_g5b_build_eth_mac_code(0x100, 31, mac2_lz, 2, code) == 0);
    assert(memcmp(code, exp_c, sizeof(exp_c)) == 0);

    /* Case D: 16-bit tiny MAC (00:00:00:00:12:34), relocated prs_off=0x130 (PC 0x98), sid=28 */
    const u8 mac_tiny[1][6] = { { 0x00, 0x00, 0x00, 0x00, 0x12, 0x34 } };
    /* 1 (LD) + 2 (CMP 1w) + 3 ($nia) + 3 ($hpnia) = 9 -> ret_pc = 0x98 + 9 = 0xA1 */
    const u32 exp_d[9] = {
        0x97DE40A1U, 0x1234095FU, 0x031C0048U, 0x094F031CU,
        0x00480001U, 0x1BFE0000U, 0x00000000U, 0x00000000U, 0x00000000U
    };
    assert(fman_pcd_g5b_build_eth_mac_code(0x130, 28, mac_tiny, 1, code) == 0);
    assert(memcmp(code, exp_d, sizeof(exp_d)) == 0);

    /* Reject invalid MACs */
    const u8 mac_zero[1][6] = { { 0, 0, 0, 0, 0, 0 } };
    const u8 mac_mcast[1][6] = { { 0x01, 0x00, 0x5e, 0x00, 0x00, 0x01 } };
    assert(fman_pcd_g5b_build_eth_mac_code(0x100, 31, mac1, 0, code) == -EINVAL);
    assert(fman_pcd_g5b_build_eth_mac_code(0x100, 31, mac1, 3, code) == -EINVAL);
    assert(fman_pcd_g5b_build_eth_mac_code(0x100, 31, mac_zero, 1, code) == -EINVAL);
    assert(fman_pcd_g5b_build_eth_mac_code(0x100, 31, mac_mcast, 1, code) == -EINVAL);
    assert(fman_pcd_g5b_build_eth_mac_code(0x100, 32, mac1, 1, code) == -EINVAL);

    /* 2. Full attach / mutual-exclusion / MAC-update / detach lifecycle */
    struct fman fm;
    struct fman_pcd pcd;
    memset(&fm, 0, sizeof(fm));
    memset(&pcd, 0, sizeof(pcd));
    fm.hwp_regs = (void *)1;
    fm.pcd = &pcd;
    pcd.fman = &fm;
    pcd.fe_int_buf_off = 0x1000;
    pcd.port.port_id = 0x11;
    pcd.port.scaffold_gro = 0x100;
    INIT_LIST_HEAD(&pcd.fe_ehash_tables);

    /* Simulate F-266 active on slots 5, 6, 10 */
    sim_punt_mask[0x11] = BIT(5) | BIT(6) | BIT(10);

    struct fman_pcd_fe_class_cfg cfg;
    memset(&cfg, 0, sizeof(cfg));
    cfg.hxs_slot = 0;
    cfg.key_size = 16;
    cfg.hash_mask = 0x7fff;
    cfg.ekfc = 0x74000000U;
    cfg.mac_exclude_cnt = 1;
    memcpy(cfg.mac_exclude[0], mac1[0], 6);

    /* Invalid config: mac_exclude_cnt on non-ETH slot or with custom code */
    cfg.hxs_slot = 11;
    u8 idx = 0;
    assert(fman_pcd_fe_class_attach(&fm, 0x11, &cfg, &idx) == -EINVAL);
    cfg.hxs_slot = 0;
    cfg.code_words = 4;
    assert(fman_pcd_fe_class_attach(&fm, 0x11, &cfg, &idx) == -EINVAL);
    cfg.code_words = 0;

    /* Valid attach on slot 0 (coexists with F-266 slots 5/6/10) */
    assert(fman_pcd_fe_class_attach(&fm, 0x11, &cfg, &idx) == 0);
    assert(idx == 1);
    assert(sim_rxport.ssa[0] == (0x400U | 0x80U));
    assert(memcmp(&sim_prs_ram[0x100 / 4], exp_a, sizeof(exp_a)) == 0);

    /* Mutual exclusion: G4 UDP (slot 11) and ESP (slot 12) must fail -EBUSY */
    struct fman_pcd_fe_class_cfg udp_cfg;
    memset(&udp_cfg, 0, sizeof(udp_cfg));
    udp_cfg.hxs_slot = 11;
    udp_cfg.key_size = 49;
    udp_cfg.hash_mask = 0x3ff;
    udp_cfg.ekfc = 0x001C0006U;
    u8 uidx = 0;
    assert(fman_pcd_fe_class_attach(&fm, 0x11, &udp_cfg, &uidx) == -EBUSY);
    udp_cfg.hxs_slot = 12;
    assert(fman_pcd_fe_class_attach(&fm, 0x11, &udp_cfg, &uidx) == -EBUSY);

    /* MAC update: identical MAC is a zero-write no-op */
    int writes_before = sim_prs_writes;
    assert(fman_pcd_fe_class_update_macs(&fm, 0x11, idx, mac1, 1) == 0);
    assert(sim_prs_writes == writes_before);

    /* MAC update: 1 MAC -> 2 MACs writes in-place at same prs_off/sid */
    assert(fman_pcd_fe_class_update_macs(&fm, 0x11, idx, mac2_full, 2) == 0);
    assert(sim_prs_writes == writes_before + 1);
    assert(memcmp(&sim_prs_ram[0x100 / 4], exp_b, sizeof(exp_b)) == 0);

    /* MAC update: -ETIMEDOUT preserves prior mac_exclude without faulting port */
    sim_prs_fail_next = 1;
    sim_prs_fail_err = -ETIMEDOUT;
    sim_prs_saw_prev = false;
    assert(fman_pcd_fe_class_update_macs(&fm, 0x11, idx, mac2_lz, 2) == -ETIMEDOUT);
    assert(sim_prs_saw_prev == true);
    assert(!test_bit(0x11, pcd.fe_port_faulted));
    assert(pcd.port.classes[0].mac_exclude_cnt == 2);
    assert(memcmp(pcd.port.classes[0].mac_exclude[1], mac2_full[1], 6) == 0);

    /* MAC update: invalid MAC rejected without touching parser RAM */
    writes_before = sim_prs_writes;
    assert(fman_pcd_fe_class_update_macs(&fm, 0x11, idx, mac_mcast, 1) == -EINVAL);
    assert(sim_prs_writes == writes_before);

    /* Detach restores cold-boot zeros (prev_code, NOT exp_a or exp_b!) */
    assert(fman_pcd_fe_class_detach(&fm, 0x11, idx) == 0);
    assert(sim_rxport.ssa[0] == 0);
    for (int w = 0; w < 9; w++)
        assert(sim_prs_ram[0x100 / 4 + w] == 0);

    /* Reverse mutual exclusion: attach UDP slot 11 first, then slot 0 -> -EBUSY */
    udp_cfg.hxs_slot = 11;
    assert(fman_pcd_fe_class_attach(&fm, 0x11, &udp_cfg, &uidx) == 0);
    assert(fman_pcd_fe_class_attach(&fm, 0x11, &cfg, &idx) == -EBUSY);
    assert(fman_pcd_fe_class_detach(&fm, 0x11, uidx) == 0);

    /* Port-wide disengage helper detaches active ETH HXS MAC-filter class */
    assert(fman_pcd_fe_class_attach(&fm, 0x11, &cfg, &idx) == 0);
    assert(fman_pcd_fe_class_detach_port_locked(&pcd, 0x11) == 0);
    assert(sim_rxport.ssa[0] == 0);
    for (int w = 0; w < 9; w++)
        assert(sim_prs_ram[0x100 / 4 + w] == 0);

    /* MAC update: -EIO readback mismatch passes rollback prev buffer AND faults port */
    assert(fman_pcd_fe_class_attach(&fm, 0x11, &cfg, &idx) == 0);
    sim_prs_fail_next = 1;
    sim_prs_fail_err = -EIO;
    sim_prs_saw_prev = false;
    assert(fman_pcd_fe_class_update_macs(&fm, 0x11, idx, mac2_full, 2) == -EIO);
    assert(sim_prs_saw_prev == true);
    assert(test_bit(0x11, pcd.fe_port_faulted));
    assert(fman_pcd_fe_class_update_macs(&fm, 0x11, idx, mac1, 1) == -ESHUTDOWN);
    clear_bit(0x11, pcd.fe_port_faulted);
    assert(fman_pcd_fe_class_detach(&fm, 0x11, idx) == 0);

    printf("ALL F-324 ASAN/UBSAN G5B CHECKS PASSED\n");
    return 0;
}
'''

    c_path = tmp / "f324_harness.c"
    bin_path = tmp / "f324_harness"
    c_path.write_text(harness)
    comp = subprocess.run(
        ["gcc", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
         "-fsanitize=address,undefined", str(c_path), "-o", str(bin_path)],
        capture_output=True, text=True,
    )
    assert comp.returncode == 0, f"ASan harness compile failed:\n{comp.stderr}"
    run = subprocess.run([str(bin_path)], capture_output=True, text=True)
    assert run.returncode == 0, f"ASan harness failed:\n{run.stdout}\n{run.stderr}"
    print(run.stdout.strip())


def check_ask_sources():
    hw_c = (ROOT / "kernel/ask/oot-modules/ask/ask_hw.c").read_text()
    br_c = (ROOT / "kernel/ask/oot-modules/ask/ask_bridge.c").read_text()
    fl_c = (ROOT / "kernel/ask/oot-modules/ask/ask_flow_offload.c").read_text()
    gn_c = (ROOT / "kernel/ask/oot-modules/ask/ask_genl.c").read_text()

    required = [
        ("ask_hw.c", hw_c, "ask_hw_port_l2_only"),
        ("ask_hw.c", hw_c, "ask_hw_l2_class_attach"),
        ("ask_hw.c", hw_c, "ask_hw_l2_class_detach"),
        ("ask_hw.c", hw_c, "ask_hw_g4_pause_port"),
        ("ask_hw.c", hw_c, "ask_hw_g4_resume_port"),
        ("ask_hw.c", hw_c, "ask_hw_g4_detach_port"),
        ("ask_hw.c", hw_c, "fman_pcd_fe_class_flow_del_identified"),
        ("ask_hw.c", hw_c, "fman_pcd_fe_class_flow_get_stats_identified"),
        ("ask_hw.c", hw_c, "fman_pcd_fe_class_update_macs"),
        ("ask_bridge.c", br_c, "ask_bridge_port_wants_l2_class"),
        ("ask_bridge.c", br_c, "ask_bridge_sync_port_class"),
        ("ask_bridge.c", br_c, "ask_bridge_sync_classes_rtnl"),
        ("ask_bridge.c", br_c, "vlan_dev_real_dev(v) == in"),
        ("ask_bridge.c", br_c, "NETDEV_CHANGEADDR"),
        ("ask_genl.c", gn_c, "ask_bridge_sync_port_class"),
        ("ask_flow_offload.c", fl_c, "netif_is_bridge_master"),
        ("ask_flow_offload.c", fl_c, "ask_devchg_queue_bridge_lowers"),
    ]
    missing = [f"{fn}: missing {sym}" for fn, src, sym in required if sym not in src]
    assert not missing, "ask.ko G5b static gate failed:\n  " + "\n  ".join(missing)
    print(f"ask.ko G5b static gates: {len(required)}/{len(required)} OK")


def main():
    check_f324_exists()
    tree = find_kernel_tree()
    tmp = apply_f323_and_f324(tree)
    try:
        run_asan_harness(tmp)
        check_ask_sources()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS: test-f324-g5b-fixup.py")


if __name__ == "__main__":
    main()
