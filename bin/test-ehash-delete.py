#!/usr/bin/env python3
"""Run the patched ehash deletion code with a simulated FMan SYNC.

Usage: python3 bin/test-ehash-delete.py <kernel-source-dir>
The tree must already carry the F-254/F-256/F-257 fixups; ci-setup-kernel.sh
runs this right after them.

The kernel source is used verbatim; only allocation, list and hardware
interfaces are stubbed. This checks lifetime/chain invariants, not silicon.
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
typedef uint16_t __be16;
typedef uint32_t __be32;
typedef uint64_t __be64;
typedef u64 dma_addr_t;

#define FMAN_EHASH_FLOW_KEY_OFF 8
#define FMAN_EHASH_FLOW_REC_SIZE 320
#define FMAN_FE_CTX_SIZE 16
#define KEY_SIZE 46
#define INV0 0x80000000U
#define be16_to_cpu(x) be16toh(x)
#define be32_to_cpu(x) be32toh(x)
#define be64_to_cpu(x) be64toh(x)
#define cpu_to_be64(x) htobe64(x)
#define swab64(x) __builtin_bswap64(x)
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, value) ((x) = (value))
#define virt_to_phys(x) ((uintptr_t)(x))

struct list_head { struct list_head *next, *prev; };
#define list_entry(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))
#define list_for_each_entry(pos, head, member) \
	for (struct list_head *it = (head)->next; \
	     it != (head) && ((pos) = list_entry(it, __typeof__(*(pos)), member), 1); \
	     it = it->next)
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

struct fman { bool timeout; unsigned int polls; };
struct fman_pcd { struct fman *fman; };
struct fman_pcd_ehash_flow {
	struct list_head node;
	u8 key_size;
	u32 index;
	void *record;
	dma_addr_t record_dma;
	void *ctx;
	dma_addr_t ctx_dma;
	u64 *bucket_h;
	u64 prev_head;
};
struct fman_pcd_ehash_table {
	u8 key_size;
	struct list_head flows;
	struct fman_pcd *pcd;
	void *dev;
};

static unsigned int records_freed, contexts_freed, metadata_freed;
static unsigned int syncs, reads, warnings;
static bool barrier, quiesced;

static void dma_wmb(void) { barrier = true; }
static void udelay(unsigned int us) { assert(us == 1); }
static struct fman *fman_pcd_get_fman(struct fman_pcd *pcd)
{
	return pcd->fman;
}
static void fman_set_fpm_extc(struct fman *fm, u32 value)
{
	(void)fm;
	assert(barrier && value == INV0);
	quiesced = false;
	syncs++;
}
static u32 fman_get_fpm_extc(struct fman *fm)
{
	reads++;
	if (fm->timeout)
		return INV0;
	if (fm->polls) {
		fm->polls--;
		return INV0;
	}
	quiesced = true;
	return 0;
}
static void record_warning(const char *format, ...)
{
	(void)format;
	warnings++;
}
#define pr_warn(...) record_warning(__VA_ARGS__)
#define pr_warn_ratelimited(...) record_warning(__VA_ARGS__)
static void dma_free_coherent(void *dev, size_t size, void *ptr, dma_addr_t dma)
{
	assert(dev && quiesced && ptr && dma == (uintptr_t)ptr);
	if (size == FMAN_EHASH_FLOW_REC_SIZE)
		records_freed++;
	else {
		assert(size == FMAN_FE_CTX_SIZE);
		contexts_freed++;
	}
	free(ptr);
}
static void kfree(void *ptr) { metadata_freed++; free(ptr); }

@KERNEL_FUNCTIONS@

static void setup(struct fman_pcd_ehash_table *table, struct fman_pcd *pcd)
{
	*table = (struct fman_pcd_ehash_table){ .key_size = KEY_SIZE,
		.pcd = pcd, .dev = table };
	table->flows.next = table->flows.prev = &table->flows;
	records_freed = contexts_freed = metadata_freed = 0;
	syncs = reads = warnings = 0;
	barrier = quiesced = false;
}

static struct fman_pcd_ehash_flow *add(struct fman_pcd_ehash_table *table,
				      u64 *head, u8 id, bool context)
{
	struct fman_pcd_ehash_flow *flow = calloc(1, sizeof(*flow));
	assert(flow);
	flow->record = calloc(1, FMAN_EHASH_FLOW_REC_SIZE);
	assert(flow->record);
	flow->record_dma = (uintptr_t)flow->record;
	assert(!(flow->record_dma >> 48));
	if (context) {
		flow->ctx = malloc(FMAN_FE_CTX_SIZE);
		assert(flow->ctx);
		memset(flow->ctx, 0xab, FMAN_FE_CTX_SIZE);
		flow->ctx_dma = (uintptr_t)flow->ctx;
	}
	flow->key_size = KEY_SIZE;
	((u8 *)flow->record)[FMAN_EHASH_FLOW_KEY_OFF] = id;
	flow->bucket_h = head;
	flow->prev_head = *head;
	*(__be64 *)flow->record = cpu_to_be64(
		((u64)(0x1234 + id) << 48) | swab64(*head));
	*head = swab64(flow->record_dma);
	flow->node.next = table->flows.next;
	flow->node.prev = &table->flows;
	table->flows.next->prev = &flow->node;
	table->flows.next = &flow->node;
	return flow;
}

static void test_chain(unsigned int target)
{
	struct fman fm = { .polls = 2 };
	struct fman_pcd pcd = { .fman = &fm };
	struct fman_pcd_ehash_table table;
	struct fman_pcd_ehash_flow *flows[3];
	u64 head = 0;
	u8 key[KEY_SIZE] = { target + 1 };

	setup(&table, &pcd);
	for (unsigned int i = 0; i < 3; i++)
		flows[i] = add(&table, &head, i + 1, true);
	assert(fman_pcd_ehash_del_key(&table, key, sizeof(key)) == 0);
	assert(records_freed == 1 && contexts_freed == 1 && metadata_freed == 1);
	assert(syncs == 1 && reads == 3 && warnings == 0);
	assert(swab64(head) == flows[target == 2 ? 1 : 2]->record_dma);
	if (target < 2) {
		u64 word = be64_to_cpu(*(__be64 *)flows[target + 1]->record);
		assert((word >> 48) == 0x1234 + target + 2);
		assert((word & 0x0000ffffffffffffULL) ==
		       (target ? flows[0]->record_dma : 0));
	}
	fman_pcd_ehash_flow_drain(&table);
	assert(head == 0 && table.flows.next == &table.flows);
	assert(records_freed == 3 && contexts_freed == 3 && metadata_freed == 3);
}

static void test_missing_and_invalid(void)
{
	struct fman fm = { 0 };
	struct fman_pcd pcd = { .fman = &fm };
	struct fman_pcd_ehash_table table;
	u8 key[KEY_SIZE] = { 1 };

	setup(&table, &pcd);
	assert(fman_pcd_ehash_del_key(&table, key, sizeof(key)) == -ENOENT);
	assert(fman_pcd_ehash_del_key(NULL, key, sizeof(key)) == -EINVAL);
	assert(fman_pcd_ehash_del_key(&table, NULL, sizeof(key)) == -EINVAL);
	assert(fman_pcd_ehash_del_key(&table, key, 0) == -EINVAL);
	assert(fman_pcd_ehash_del_key(&table, key, sizeof(key) - 1) == -EINVAL);
	assert(syncs == 0 && records_freed == 0 && contexts_freed == 0);
}

static void test_no_context(void)
{
	struct fman fm = { 0 };
	struct fman_pcd pcd = { .fman = &fm };
	struct fman_pcd_ehash_table table;
	u64 head = 0;
	u8 key[KEY_SIZE] = { 1 };

	setup(&table, &pcd);
	add(&table, &head, 1, false);
	assert(fman_pcd_ehash_del_key(&table, key, sizeof(key)) == 0);
	assert(records_freed == 1 && contexts_freed == 0 && metadata_freed == 1);
	assert(head == 0 && table.flows.next == &table.flows);
}

static void test_timeout(void)
{
	struct fman fm = { .timeout = true };
	struct fman_pcd pcd = { .fman = &fm };
	struct fman_pcd_ehash_table table;
	struct fman_pcd_ehash_flow *flow;
	u64 head = 0;
	u8 key[KEY_SIZE] = { 1 };
	void *record, *ctx;

	setup(&table, &pcd);
	flow = add(&table, &head, 1, true);
	record = flow->record;
	ctx = flow->ctx;
	assert(fman_pcd_ehash_del_key(&table, key, sizeof(key)) == 0);
	assert(records_freed == 0 && contexts_freed == 0 && metadata_freed == 1);
	assert(syncs == 1 && reads == 100000 && warnings == 3 && !quiesced);
	assert(head == 0 && table.flows.next == &table.flows);
	assert(((u8 *)record)[FMAN_EHASH_FLOW_KEY_OFF] == 1);
	assert(((u8 *)ctx)[0] == 0xab);
	/* Test cleanup only: the production timeout must keep both allocations. */
	free(record);
	free(ctx);
}

int main(void)
{
	for (unsigned int target = 0; target < 3; target++)
		test_chain(target);
	test_missing_and_invalid();
	test_no_context();
	test_timeout();
	puts("OK: ehash head/middle/tail deletion, ctx release, invalid/missing keys and SYNC-timeout retention");
	return 0;
}
"""


def extract_function(source: str, name: str) -> str:
    match = re.search(rf"static (?:int|void) {name}\([^;]*?\n\{{\n", source)
    if match is None:
        raise ValueError(f"Kernel function {name} was not found")
    end = source.index("\n}\n", match.end()) + len("\n}\n")
    return source[match.start():end]


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
        "fman_pcd_ehash_flow_drain", "fman_pcd_ehash_del_key"))
    with tempfile.TemporaryDirectory(prefix="ask2-ehash-delete-") as directory:
        path = Path(directory)
        code = path / "delete.c"
        binary = path / "delete"
        code.write_text(HARNESS.replace("@KERNEL_FUNCTIONS@", functions))
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
