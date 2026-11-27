#!/usr/bin/env python3
"""Host ASan/UBSan gate for F-325 (fman_memac.c memac_set_allmulti multicast preservation).

Verifies:
  1. F_325.py applies cleanly to drivers/net/ethernet/freescale/fman/fman_memac.c
     and is idempotent on re-run.
  2. Real extracted C functions (memac_add_hash_mac_address, memac_set_allmulti,
     memac_del_hash_mac_address) compile under ASan+UBSan (-Wall -Wextra -Werror)
     and preserve active multicast hash buckets (e.g. IPv6 solicited-node
     33:33:ff:00:01:85) across allmulti on -> off transitions while clearing
     empty buckets and buckets whose last address was deleted while allmulti
     was enabled.
"""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
F325 = ROOT / "bin" / "kernel-fixups" / "F_325.py"
MEMAC_C = "drivers/net/ethernet/freescale/fman/fman_memac.c"


def find_kernel_tree() -> Path:
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        p = Path(sys.argv[1])
        if (p / MEMAC_C).is_file():
            return p
        raise RuntimeError(f"Kernel tree path invalid: {p}")
    for c in (Path.home() / "kernel-git-cache" / "linux", ROOT / "work" / "linux-6.18.62"):
        if (c / MEMAC_C).is_file():
            return c
    raise RuntimeError("No kernel tree found for F_325 host gate")


def extract_fn(src: str, sig: str) -> str:
    i = src.index(sig)
    j = src.index("\n}\n", i) + 3
    return src[i:j]


def run_asan_harness(memac_src: str) -> None:
    add_fn = extract_fn(memac_src, "static int memac_add_hash_mac_address(")
    allm_fn = extract_fn(memac_src, "static int memac_set_allmulti(")
    del_fn = extract_fn(memac_src, "static int memac_del_hash_mac_address(")

    harness = r'''
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
typedef u8 enet_addr_t[6];
#define __iomem
#define GFP_ATOMIC 0
#define pr_err(...) do {} while (0)

#define HASH_CTRL_MCAST_EN	0x00000100U
#define HASH_CTRL_ADDR_MASK	0x0000003FU
#define GROUP_ADDRESS		0x0000010000000000ULL
#define HASH_TABLE_SIZE		64

#define ENET_ADDR_TO_UINT64(a) \
	(((u64)(a)[0] << 40) | ((u64)(a)[1] << 32) | ((u64)(a)[2] << 24) | \
	 ((u64)(a)[3] << 16) | ((u64)(a)[4] << 8)  | (u64)(a)[5])

struct list_head { struct list_head *next, *prev; };
static inline void INIT_LIST_HEAD(struct list_head *h) { h->next = h; h->prev = h; }
static inline bool list_empty(const struct list_head *h) { return h->next == h; }
static inline void list_add_tail(struct list_head *n, struct list_head *h) {
	n->next = h; n->prev = h->prev; h->prev->next = n; h->prev = n;
}
static inline void list_del_init(struct list_head *n) {
	n->prev->next = n->next; n->next->prev = n->prev; INIT_LIST_HEAD(n);
}
#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))
#define list_for_each(pos, head) \
	for ((pos) = (head)->next; (pos) != (head); (pos) = (pos)->next)

struct eth_hash_entry {
	u64 addr;
	struct list_head node;
};
#define ETH_HASH_ENTRY_OBJ(ptr) container_of(ptr, struct eth_hash_entry, node)

struct eth_hash_t {
	u16 size;
	struct list_head *lsts;
};

struct memac_regs {
	u32 hashtable_ctrl;
};

struct fman_mac {
	struct memac_regs *regs;
	struct eth_hash_t *multicast_addr_hash;
	bool allmulti_enabled;
};

static bool hw_hash_en[HASH_TABLE_SIZE];

static void iowrite32be(u32 val, void *addr)
{
	(void)addr;
	u32 idx = val & HASH_CTRL_ADDR_MASK;
	assert(idx < HASH_TABLE_SIZE);
	hw_hash_en[idx] = (val & HASH_CTRL_MCAST_EN) != 0;
}

static void *kmalloc(size_t sz, int flags) { (void)flags; return malloc(sz); }
static void kfree(const void *p) { free((void *)p); }

/* Exact CRC-less 6-bit XOR fold from fman_memac.c */
static u32 get_mac_addr_hash_code(u64 eth_addr)
{
	u32 xor_val = 0;
	for (int i = 0; i < 6; i++) {
		u8 b = (u8)((eth_addr >> (i * 8)) & 0xff);
		xor_val ^= b;
	}
	return xor_val & HASH_CTRL_ADDR_MASK;
}

''' + add_fn + "\n" + allm_fn + "\n" + del_fn + r'''

int main(void)
{
	struct memac_regs regs = { 0 };
	struct list_head lists[HASH_TABLE_SIZE];
	struct eth_hash_t htbl = { .size = HASH_TABLE_SIZE, .lsts = lists };
	struct fman_mac memac = {
		.regs = &regs,
		.multicast_addr_hash = &htbl,
		.allmulti_enabled = false,
	};
	for (int i = 0; i < HASH_TABLE_SIZE; i++) {
		INIT_LIST_HEAD(&lists[i]);
		hw_hash_en[i] = false;
	}

	/* Subscribe IPv6 solicited-node multicast (33:33:ff:00:01:85) + all-nodes + temp */
	enet_addr_t snm = { 0x33, 0x33, 0xff, 0x00, 0x01, 0x85 };
	enet_addr_t allnodes = { 0x33, 0x33, 0x00, 0x00, 0x00, 0x01 };
	enet_addr_t temp_mc = { 0x01, 0x00, 0x5e, 0x12, 0x34, 0x56 };

	u32 h_snm = get_mac_addr_hash_code(ENET_ADDR_TO_UINT64(snm));
	u32 h_all = get_mac_addr_hash_code(ENET_ADDR_TO_UINT64(allnodes));
	u32 h_tmp = get_mac_addr_hash_code(ENET_ADDR_TO_UINT64(temp_mc));
	assert(h_snm != h_tmp && h_all != h_tmp);

	assert(memac_add_hash_mac_address(&memac, &snm) == 0);
	assert(memac_add_hash_mac_address(&memac, &allnodes) == 0);
	assert(memac_add_hash_mac_address(&memac, &temp_mc) == 0);
	assert(hw_hash_en[h_snm] && hw_hash_en[h_all] && hw_hash_en[h_tmp]);

	/* Bridge join -> allmulti ON: all 64 hardware buckets enabled */
	assert(memac_set_allmulti(&memac, true) == 0);
	assert(memac.allmulti_enabled);
	for (int i = 0; i < HASH_TABLE_SIZE; i++)
		assert(hw_hash_en[i]);

	/* Unsubscribe temp_mc while allmulti ON: hardware stays enabled until allmulti OFF */
	assert(memac_del_hash_mac_address(&memac, &temp_mc) == 0);
	assert(hw_hash_en[h_tmp]);

	/* Bridge leave -> allmulti OFF: active buckets MUST stay enabled, deleted/empty MUST clear */
	assert(memac_set_allmulti(&memac, false) == 0);
	assert(!memac.allmulti_enabled);
	assert(hw_hash_en[h_snm]);
	assert(hw_hash_en[h_all]);
	assert(!hw_hash_en[h_tmp]);
	for (int i = 0; i < HASH_TABLE_SIZE; i++) {
		bool expect = (i == (int)h_snm || i == (int)h_all);
		assert(hw_hash_en[i] == expect);
	}

	/* Clean up remaining entries (ASan leak check) */
	assert(memac_del_hash_mac_address(&memac, &snm) == 0);
	assert(memac_del_hash_mac_address(&memac, &allnodes) == 0);
	assert(!hw_hash_en[h_snm] && !hw_hash_en[h_all]);

	/* Defensive NULL multicast_addr_hash check */
	memac.multicast_addr_hash = NULL;
	assert(memac_set_allmulti(&memac, true) == 0);
	assert(memac_set_allmulti(&memac, false) == 0);
	for (int i = 0; i < HASH_TABLE_SIZE; i++)
		assert(!hw_hash_en[i]);

	printf("ALL F-325 ASAN/UBSAN MEMAC ALLMULTI CHECKS PASSED\n");
	return 0;
}
'''

    with tempfile.TemporaryDirectory(prefix="f325-asan-") as td:
        c_path = Path(td) / "f325_harness.c"
        bin_path = Path(td) / "f325_harness"
        c_path.write_text(harness)
        comp = subprocess.run(
            ["cc", "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
             "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
             str(c_path), "-o", str(bin_path)],
            capture_output=True, text=True,
        )
        assert comp.returncode == 0, f"ASan harness compile failed:\n{comp.stderr}"
        run = subprocess.run([str(bin_path)], capture_output=True, text=True)
        assert run.returncode == 0, f"ASan harness failed:\n{run.stdout}\n{run.stderr}"
        print(run.stdout.strip())


def main() -> int:
    tree = find_kernel_tree()
    check_only = "--check-only" in sys.argv
    if check_only:
        run_asan_harness((tree / MEMAC_C).read_text())
        print("PASS: test-f325-memac-allmulti.py (--check-only)")
        return 0

    if not F325.is_file():
        print(f"FAIL: {F325} does not exist yet")
        return 1

    with tempfile.TemporaryDirectory(prefix="f325-apply-") as td:
        td_p = Path(td)
        dst = td_p / MEMAC_C
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tree / MEMAC_C, dst)

        r1 = subprocess.run([sys.executable, str(F325)], cwd=td_p,
                            capture_output=True, text=True)
        assert r1.returncode == 0, f"F_325 apply failed:\n{r1.stdout}\n{r1.stderr}"
        r2 = subprocess.run([sys.executable, str(F325)], cwd=td_p,
                            capture_output=True, text=True)
        assert r2.returncode == 0, f"F_325 idempotency failed:\n{r2.stdout}\n{r2.stderr}"
        run_asan_harness(dst.read_text())

    print("PASS: test-f325-memac-allmulti.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
