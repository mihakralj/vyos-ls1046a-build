#!/usr/bin/env python3
"""Host ASan/UBSan gate for F-326 (dpaa_setup_tc_flow_block sleepable SRCU).

Verifies:
  1. F_326.py applies cleanly to dpaa_eth.c (after patches 0145 + 0171) and is
     idempotent on re-run.
  2. Real extracted C functions (dpaa_register_flow_offload_handler,
     dpaa_unregister_flow_offload_handler, dpaa_get_offload_stats,
     dpaa_setup_tc_flow_block) compile under ASan+UBSan (-Wall -Wextra -Werror):
     - ops->setup_tc_block() is invoked outside rcu_read_lock() (rcu_depth == 0)
       and inside srcu_read_lock(&dpaa_fo_srcu) (srcu_depth == 1), so sleeping
       (mutex_lock) inside setup_tc_block() passes might_sleep().
     - ops->offload_stats() stays under atomic rcu_read_lock() (rcu_depth == 1,
       srcu_depth == 0).
     - dpaa_unregister_flow_offload_handler() clears the slot, calls BOTH
       synchronize_rcu() and synchronize_srcu(&dpaa_fo_srcu) BEFORE module_put().
"""

from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
F326 = ROOT / "bin" / "kernel-fixups" / "F_326.py"
DPAA_ETH_C = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"


def extract_block(src: str) -> str:
    start = src.index("/* External flow-offload backend registration (ASK2). */")
    end_marker = "static int dpaa_setup_tc_flow_block("
    fn_start = src.index(end_marker, start)
    fn_end = src.index("\n}\n", fn_start) + 3
    return src[start:fn_end]


def run_asan_harness(dpaa_src: str, expect_sleep_bug: bool = False) -> int:
    assert "#include <linux/srcu.h>" in dpaa_src or expect_sleep_bug
    block = extract_block(dpaa_src)

    harness = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define __rcu
#define EXPORT_SYMBOL_GPL(sym)
#define DEFINE_SPINLOCK(name) int name = 0
#define DEFINE_STATIC_SRCU(name) static struct srcu_struct name = {0}

struct module { int refcnt; };
struct net_device { int ifindex; };
struct flow_block_offload { int command; };
struct rtnl_link_stats64 { uint64_t rx_packets; };
struct srcu_struct { int depth; int sync_calls; };

struct dpaa_flow_offload_ops {
	struct module *owner;
	int (*setup_tc_block)(struct net_device *net_dev,
			      struct flow_block_offload *f);
	void (*offload_stats)(struct net_device *net_dev,
			      struct rtnl_link_stats64 *s);
};

static int g_spin_held;
static int g_rcu_depth;
static int g_sync_rcu_calls;
static int g_module_put_order;
static int g_sync_rcu_order;
static int g_sync_srcu_order;
static int g_order_seq;
static int g_sleep_bug_detected;
static int g_setup_calls;
static int g_stats_calls;

static inline void spin_lock(int *l) { assert(*l == 0); *l = 1; g_spin_held = 1; }
static inline void spin_unlock(int *l) { assert(*l == 1); *l = 0; g_spin_held = 0; }
static inline bool lockdep_is_held(int *l) { return *l != 0; }

static inline void rcu_read_lock(void) { g_rcu_depth++; }
static inline void rcu_read_unlock(void) { assert(g_rcu_depth > 0); g_rcu_depth--; }
static inline void synchronize_rcu(void) {
	assert(g_spin_held == 0 && g_rcu_depth == 0);
	g_sync_rcu_calls++;
	g_sync_rcu_order = ++g_order_seq;
}

static inline int srcu_read_lock(struct srcu_struct *s) {
	assert(g_rcu_depth == 0);
	s->depth++;
	return s->depth;
}
static inline void srcu_read_unlock(struct srcu_struct *s, int idx) {
	assert(s->depth == idx && s->depth > 0);
	s->depth--;
}
static inline void synchronize_srcu(struct srcu_struct *s) {
	assert(g_spin_held == 0 && g_rcu_depth == 0 && s->depth == 0);
	s->sync_calls++;
	g_sync_srcu_order = ++g_order_seq;
}

#define rcu_assign_pointer(p, v) ((p) = (v))
#define rcu_dereference(p) (assert(g_rcu_depth > 0), (p))
#define rcu_dereference_protected(p, c) (assert(c), (p))
#define srcu_dereference(p, sp) (assert((sp)->depth > 0), (p))

static inline bool try_module_get(struct module *m) { m->refcnt++; return true; }
static inline void module_put(struct module *m) {
	assert(m->refcnt > 0);
	m->refcnt--;
	g_module_put_order = ++g_order_seq;
}

static void might_sleep_check(void) {
	if (g_rcu_depth > 0 || g_spin_held > 0)
		g_sleep_bug_detected = 1;
}

''' + block + r'''

static int mock_setup_tc_block(struct net_device *dev, struct flow_block_offload *f)
{
	(void)dev;
	(void)f;
	might_sleep_check();
	g_setup_calls++;
	return 0;
}

static void mock_offload_stats(struct net_device *dev, struct rtnl_link_stats64 *s)
{
	(void)dev;
	assert(g_rcu_depth > 0);
	s->rx_packets += 42;
	g_stats_calls++;
}

int main(void)
{
	struct module mod = { .refcnt = 1 };
	struct dpaa_flow_offload_ops ops = {
		.owner = &mod,
		.setup_tc_block = mock_setup_tc_block,
		.offload_stats = mock_offload_stats,
	};
	struct net_device dev = { .ifindex = 5 };
	struct flow_block_offload f = { .command = 1 };
	struct rtnl_link_stats64 st = { 0 };

	/* Unregistered slot -> -EOPNOTSUPP. */
	assert(dpaa_setup_tc_flow_block(&dev, &f) == -EOPNOTSUPP);

	/* Register + double-register -> -EBUSY. */
	assert(dpaa_register_flow_offload_handler(&ops) == 0);
	assert(mod.refcnt == 2);
	assert(dpaa_register_flow_offload_handler(&ops) == -EBUSY);

	/* Process-context TC block setup must allow sleeping (rcu_depth == 0). */
	assert(dpaa_setup_tc_flow_block(&dev, &f) == 0);
	assert(g_setup_calls == 1);
	if (g_sleep_bug_detected) {
		fprintf(stderr, "FAIL: setup_tc_block ran inside rcu_read_lock (Invalid wait context)\n");
		return 2;
	}

	/* Atomic stats hook stays under plain RCU. */
	dpaa_get_offload_stats(&dev, &st);
	assert(g_stats_calls == 1 && st.rx_packets == 42);

	/* Unregister drains BOTH synchronize_rcu and synchronize_srcu before module_put. */
	assert(dpaa_unregister_flow_offload_handler(&ops) == 0);
	assert(mod.refcnt == 1);
	assert(g_sync_rcu_calls == 1);
	assert(g_sync_rcu_order > 0 && g_sync_srcu_order > 0);
	assert(g_sync_rcu_order < g_module_put_order);
	assert(g_sync_srcu_order < g_module_put_order);
	assert(dpaa_setup_tc_flow_block(&dev, &f) == -EOPNOTSUPP);

	printf("F-326 dpaa_setup_tc_flow_block SRCU harness PASS\n");
	return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="test-f326-") as td:
        cpath = Path(td) / "f326_test.c"
        binpath = Path(td) / "f326_test"
        cpath.write_text(harness)
        subprocess.run(
            ["cc", "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
             "-Wno-unused-function", "-fsanitize=address,undefined",
             str(cpath), "-o", str(binpath)],
            check=True,
        )
        res = subprocess.run([str(binpath)], check=False)
        return res.returncode


def build_synthetic_pre_f326_source() -> str:
    """Build a minimal synthetic dpaa_eth.c matching 0145 + 0171 for standalone testing."""
    return (
        "#include <linux/fsl/dpaa_flow_offload.h>\n"
        "/* External flow-offload backend registration (ASK2). */\n"
        "static const struct dpaa_flow_offload_ops __rcu *dpaa_fo_ops_slot;\n"
        "static DEFINE_SPINLOCK(dpaa_fo_ops_lock);\n\n"
        "int dpaa_register_flow_offload_handler(const struct dpaa_flow_offload_ops *ops)\n"
        "{\n"
        "\tconst struct dpaa_flow_offload_ops *cur;\n\n"
        "\tif (!ops || !ops->setup_tc_block)\n"
        "\t\treturn -EINVAL;\n\n"
        "\tspin_lock(&dpaa_fo_ops_lock);\n"
        "\tcur = rcu_dereference_protected(dpaa_fo_ops_slot,\n"
        "\t\t\t\t\tlockdep_is_held(&dpaa_fo_ops_lock));\n"
        "\tif (cur) {\n"
        "\t\tspin_unlock(&dpaa_fo_ops_lock);\n"
        "\t\treturn -EBUSY;\n"
        "\t}\n"
        "\tif (ops->owner && !try_module_get(ops->owner)) {\n"
        "\t\tspin_unlock(&dpaa_fo_ops_lock);\n"
        "\t\treturn -ENODEV;\n"
        "\t}\n\n"
        "\trcu_assign_pointer(dpaa_fo_ops_slot, ops);\n"
        "\tspin_unlock(&dpaa_fo_ops_lock);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(dpaa_register_flow_offload_handler);\n\n"
        "int dpaa_unregister_flow_offload_handler(const struct dpaa_flow_offload_ops *ops)\n"
        "{\n"
        "\tconst struct dpaa_flow_offload_ops *cur;\n\n"
        "\tif (!ops)\n"
        "\t\treturn -EINVAL;\n\n"
        "\tspin_lock(&dpaa_fo_ops_lock);\n"
        "\tcur = rcu_dereference_protected(dpaa_fo_ops_slot,\n"
        "\t\t\t\t\tlockdep_is_held(&dpaa_fo_ops_lock));\n"
        "\tif (cur != ops) {\n"
        "\t\tspin_unlock(&dpaa_fo_ops_lock);\n"
        "\t\treturn -EINVAL;\n"
        "\t}\n\n"
        "\trcu_assign_pointer(dpaa_fo_ops_slot, NULL);\n"
        "\tspin_unlock(&dpaa_fo_ops_lock);\n"
        "\tsynchronize_rcu();\n\n"
        "\tif (ops->owner)\n"
        "\t\tmodule_put(ops->owner);\n\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(dpaa_unregister_flow_offload_handler);\n\n"
        "static void dpaa_get_offload_stats(struct net_device *net_dev,\n"
        "\t\t\t\t   struct rtnl_link_stats64 *s)\n"
        "{\n"
        "\tconst struct dpaa_flow_offload_ops *ops;\n\n"
        "\trcu_read_lock();\n"
        "\tops = rcu_dereference(dpaa_fo_ops_slot);\n"
        "\tif (ops && ops->offload_stats)\n"
        "\t\tops->offload_stats(net_dev, s);\n"
        "\trcu_read_unlock();\n"
        "}\n\n"
        "static int dpaa_setup_tc_flow_block(struct net_device *net_dev,\n"
        "\t\t\t\tstruct flow_block_offload *f)\n"
        "{\n"
        "\tconst struct dpaa_flow_offload_ops *ops;\n"
        "\tint err;\n\n"
        "\trcu_read_lock();\n"
        "\tops = rcu_dereference(dpaa_fo_ops_slot);\n"
        "\tif (!ops) {\n"
        "\t\trcu_read_unlock();\n"
        "\t\treturn -EOPNOTSUPP;\n"
        "\t}\n\n"
        "\terr = ops->setup_tc_block(net_dev, f);\n"
        "\trcu_read_unlock();\n\n"
        "\treturn err;\n"
        "}\n"
    )


def main() -> int:
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        ksrc = Path(sys.argv[1])
        dpaa_path = ksrc / DPAA_ETH_C
        if not dpaa_path.is_file():
            raise RuntimeError(f"Kernel tree path invalid: {dpaa_path}")
        src = dpaa_path.read_text()
        # In CI, F_326.py has already been applied; verify idempotency and run harness.
        subprocess.run([sys.executable, str(F326)], cwd=ksrc, check=True)
        src2 = dpaa_path.read_text()
        assert src2 == src, "F_326.py not idempotent"
        rc = run_asan_harness(src2, expect_sleep_bug=False)
        if rc != 0:
            return rc
        print("OK: F-326 verified against kernel tree")
        return 0

    # Standalone host self-test: prove pre-F-326 fails, then apply F_326 twice and prove pass.
    pre_src = build_synthetic_pre_f326_source()
    pre_rc = run_asan_harness(pre_src, expect_sleep_bug=True)
    assert pre_rc == 2, f"expected pre-F-326 sleep-in-RCU failure (rc=2), got {pre_rc}"
    print("OK: pre-F-326 reproduced sleep-in-RCU failure (rc=2)")

    with tempfile.TemporaryDirectory(prefix="f326-apply-") as td:
        p = Path(td) / DPAA_ETH_C
        p.parent.mkdir(parents=True)
        p.write_text(pre_src)
        subprocess.run([sys.executable, str(F326)], cwd=td, check=True)
        patched1 = p.read_text()
        subprocess.run([sys.executable, str(F326)], cwd=td, check=True)
        patched2 = p.read_text()
        assert patched1 == patched2, "F_326.py is not idempotent"
        post_rc = run_asan_harness(patched2, expect_sleep_bug=False)
        assert post_rc == 0, f"post-F-326 harness failed with rc={post_rc}"
    print("OK: F-326 standalone apply + idempotency + ASan/UBSan PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
