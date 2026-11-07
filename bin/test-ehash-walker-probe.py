#!/usr/bin/env python3
"""Host check for F-307: the ehash walker probe in the generated fman_pcd.c.

Extracts the F-307 helpers and the real fman_pcd_ehash_del_key(), compiles
them against minimal mocks and checks: probe off frees normally; probe on
retains record and nodes, counts only HITs after the unlink, reports later
HITs as late; a failed SYNC takes the quarantine path; a full probe array
falls back to freeing. Usage: bin/test-ehash-walker-probe.py <kernel-src>
"""

from pathlib import Path
import re
import subprocess
import sys
import tempfile

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <stddef.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
typedef uint64_t __be64; typedef uint64_t dma_addr_t;
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define dma_rmb() do { } while (0)
#define smp_store_release(p, v) (*(p) = (v))
#define smp_load_acquire(p) (*(p))
#define be64_to_cpu(x) __builtin_bswap64(x)
#define sysfs_emit(b, ...) sprintf(b, __VA_ARGS__)
#define EINVAL 22
#define ESHUTDOWN 108
#define ENOENT 2
#define ETIMEDOUT 110
#define pr_warn(...) do { } while (0)
#define pr_warn_ratelimited(...) do { } while (0)
#define module_param_named(...)
#define module_param_cb(...)
#define container_of(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
struct kernel_param { int unused; };
struct kernel_param_ops { int (*get)(char *, const struct kernel_param *); };
struct list_head { struct list_head *next, *prev; };
static void list_del(struct list_head *e) { e->prev->next = e->next; e->next->prev = e->prev; }
static void list_add_tail(struct list_head *e, struct list_head *h)
{ e->prev = h->prev; e->next = h; h->prev->next = e; h->prev = e; }
struct hlist_node { struct hlist_node *next; };
struct hlist_head { struct hlist_node *first; };
static void hlist_del(struct hlist_node *n) { (void)n; }
#define hlist_for_each_entry(pos, head, member) \
	for (pos = (head)->first ? container_of((head)->first, typeof(*pos), member) : NULL; \
	     pos; pos = NULL)
#define FMAN_EHASH_FLOW_KEY_OFF 8
struct fman_pcd_ehash_cu { int id; };
struct fman_pcd_ehash_flow {
	struct list_head node; struct hlist_node bnode;
	void *record; dma_addr_t record_dma; u64 *bucket_h; u16 index; u8 key_size;
};
struct fman_pcd_ehash_table {
	u8 key_size; bool draining; bool poisoned; int rec_pool;
	struct hlist_head bidx[1]; u32 hash_shift, hash_mask;
	struct fman_pcd_ehash_cu *cu[1];
	struct list_head quarantined_cu, quarantined_flows;
};

static int rec_freed, cu_freed, sync_rc, sync_hits;
static u8 rec[320];
static void bump(u64 n)
{
	u64 v = __builtin_bswap64(*(u64 *)(rec + 256)) + n;
	*(u64 *)(rec + 256) = __builtin_bswap64(v);
}
static int fman_pcd_ehash_recover(struct fman_pcd_ehash_table *t) { (void)t; return 0; }
static unsigned int fman_pcd_ehash_bucket_index(const u8 *k, u8 s, u32 a, u32 b)
{ (void)k; (void)s; (void)a; (void)b; return 0; }
static int fman_pcd_ehash_bucket_publish(struct fman_pcd_ehash_table *t, u16 i,
		struct fman_pcd_ehash_flow *x, struct fman_pcd_ehash_cu **old)
{ (void)t; (void)i; (void)x; *old = calloc(1, sizeof(**old)); return 0; }
static int fman_pcd_ehash_sync(struct fman_pcd_ehash_table *t, u32 *extc)
{ (void)t; *extc = 0; bump(sync_hits); return sync_rc; }
static void fman_pcd_ehash_cu_free(struct fman_pcd_ehash_table *t, struct fman_pcd_ehash_cu *c)
{ (void)t; if (c) cu_freed++; free(c); }
static void dma_pool_free(int pool, void *va, dma_addr_t dma) { (void)pool; (void)va; (void)dma; rec_freed++; }
static void kfree(void *p) { free(p); }

@F307@

#undef list_add_tail
#define list_add_tail(e, h) do { (void)(e); (void)(h); } while (0)
@DEL_KEY@

static struct fman_pcd_ehash_table t;
static const u8 key[4] = { 1, 2, 3, 4 };
static int del(void)
{
	struct fman_pcd_ehash_flow *x = calloc(1, sizeof(*x));
	struct list_head h = { &x->node, &x->node };

	x->node.next = x->node.prev = &h;
	x->record = rec; x->key_size = 4;
	memcpy(rec + FMAN_EHASH_FLOW_KEY_OFF, key, 4);
	t.key_size = 4; t.bidx[0].first = &x->bnode;
	return fman_pcd_ehash_del_key(&t, key, 4);
}
static u64 late(void)
{
	char buf[32];

	fman_pcd_ehash_walker_probe_late_get(buf, NULL);
	return strtoull(buf, NULL, 10);
}
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main(void)
{
	/* Probe off: normal reclamation. */
	CHECK(del() == 0 && rec_freed == 1 && cu_freed == 1 && ehash_walker_probe_kept == 0);
	/* Probe on: HITs before the unlink are excluded, SYNC-window HITs are
	 * in-flight, record and nodes are retained, budget decrements. */
	memset(rec, 0, sizeof(rec)); rec_freed = cu_freed = 0;
	bump(5); sync_hits = 3; ehash_walker_probe = 2;
	CHECK(del() == 0 && rec_freed == 0 && cu_freed == 0);
	CHECK(ehash_walker_probe_kept == 1 && ehash_walker_probe_inflight == 3 && ehash_walker_probe == 1);
	CHECK(late() == 0);
	bump(2);
	CHECK(late() == 2);
	/* Failed SYNC: quarantine, no probe record, budget kept. */
	sync_rc = -ETIMEDOUT;
	CHECK(del() == -ETIMEDOUT && t.poisoned && ehash_walker_probe_kept == 1 && ehash_walker_probe == 1);
	CHECK(rec_freed == 0);
	/* Full array: falls back to freeing. */
	sync_rc = 0; t.poisoned = false; ehash_walker_probe_kept = FMAN_EHASH_WALKER_PROBE_MAX;
	CHECK(del() == 0 && rec_freed == 1 && ehash_walker_probe == 1);
	printf("F-307 walker probe: off/retain/inflight/late/sync-failure/full cases passed\n");
	return 0;
}
"""


def block(source, start, end):
    i = source.find(start)
    if i < 0:
        sys.exit(f"FAIL: {start!r} not found")
    return source[i:source.index(end, i) + len(end)]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    src = (Path(sys.argv[1]) / "drivers/net/ethernet/freescale/fman/fman_pcd.c").read_text()
    f307 = block(src, "/* F-307: lab-only walker probe", "NULL, 0444);\n")
    head = "static int fman_pcd_ehash_del_key(struct fman_pcd_ehash_table *t,\n\t\t\t\t  const u8 *key, u8 key_size)\n{\n"
    del_key = block(src, head, "\n}\n")
    if "f307_probe" not in del_key:
        sys.exit("FAIL: F-307 not applied to fman_pcd_ehash_del_key")
    del_key = del_key.replace("&old_cu->quarantine_node", "NULL")
    with tempfile.TemporaryDirectory(prefix="f307-") as d:
        c = Path(d) / "probe.c"
        c.write_text(HARNESS.replace("@F307@", f307).replace("@DEL_KEY@", del_key))
        exe = Path(d) / "probe"
        cc = ["gcc", "-std=gnu11", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-Wno-unused-const-variable",
              "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
              str(c), "-o", str(exe)]
        if subprocess.run(cc).returncode:
            sys.exit("FAIL: F-307 harness does not compile against this source")
        if subprocess.run([str(exe)], env={"ASAN_OPTIONS": "detect_leaks=0"}).returncode:
            sys.exit("FAIL: F-307 walker probe check failed")


if __name__ == "__main__":
    main()
