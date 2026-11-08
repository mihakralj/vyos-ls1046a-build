"""Host regression tests for fail-closed WGFP sink lifetime handling."""

from pathlib import Path
import os
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_wgfp.c"
HARNESS = ROOT / "tests/dut/wgfp_sink_lifetime.c"


def test_sink_pool_is_not_destroyed_or_reused_after_uncertain_creation(tmp_path):
    source = SOURCE.read_text()
    start = source.index("static int wgfp_sink_create(")
    end = source.index("\nstatic const struct file_operations wgfp_ctl_fops", start)
    (tmp_path / "wgfp_sink_body.h").write_text(source[start:end])
    binary = tmp_path / "wgfp_sink_lifetime"
    subprocess.run(
        ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-parameter", "-I", str(tmp_path), str(HARNESS), "-o", str(binary)],
        check=True,
    )
    subprocess.run([str(binary)], check=True)


def test_sink_stats_preserve_unmasked_counts_and_report_query_failures(tmp_path):
    source = SOURCE.read_text()
    start = source.index("static void wgfp_sink_show(")
    end = source.index("\nDEFINE_SHOW_ATTRIBUTE(wgfp_stats);", start)
    (tmp_path / "wgfp_stats_body.h").write_text(source[start:end])
    binary = tmp_path / "wgfp_sink_stats"
    subprocess.run(
        ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-parameter", "-I", str(tmp_path),
         str(ROOT / "tests/dut/wgfp_sink_stats.c"), "-o", str(binary)],
        check=True,
    )
    subprocess.run([str(binary)], check=True)


@pytest.mark.parametrize(
    ("script", "settings"),
    [
        ("bin/testrig-wgfp-p1.sh", {"RECREATE": "1"}),
        ("bin/testrig-wgfp-p2a.sh", {"RECREATE": "1"}),
        ("bin/testrig-wgfp-p2b.sh", {"AUTO": "0"}),
    ],
)
def test_legacy_harness_rejects_destroy_before_contacting_the_board(tmp_path, script, settings):
    marker = tmp_path / "ssh-called"
    ssh = tmp_path / "ssh"
    ssh.write_text(f"#!/bin/sh\n: > '{marker}'\nexit 99\n")
    ssh.chmod(0o755)
    env = os.environ | settings | {"PATH": f"{tmp_path}:{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", str(ROOT / script)], env=env, text=True, capture_output=True
    )
    assert result.returncode != 0
    assert "sink destruction is not safe" in result.stderr
    assert not marker.exists()


REGISTER_HARNESS = r"""
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#define __rcu
#define WGFP_MAX_SINKS 4
#define EXPORT_SYMBOL_GPL(x)
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define rcu_access_pointer(p) (p)
#define rcu_assign_pointer(p, v) ((p) = (v))
struct dpaa_wgfp_ops { int unused; };
static const struct dpaa_wgfp_ops __rcu *wgfp_ops;
static void *wgfp_ops_priv;
static unsigned int wgfp_per_port;
static int wgfp_lock, lock_held;
static int num_online_cpus(void) { return 1; }
static void mutex_lock(int *m) { (void)m; assert(!lock_held); lock_held = 1; }
static void mutex_unlock(int *m) { (void)m; assert(lock_held); lock_held = 0; }
/* An old reader still in flight: it already loaded the old ops and loads
 * priv now.  A register that can run here must be excluded by wgfp_lock. */
static const struct dpaa_wgfp_ops new_ops;
static int new_priv, gp_checked;
static void synchronize_rcu(void)
{
	gp_checked = 1;
	assert(lock_held && "register could republish priv during the grace period");
}
#include "wgfp_register_body.h"
int main(void)
{
	static const struct dpaa_wgfp_ops old_ops;
	static int old_priv;
	assert(dpaa_wgfp_register(&old_ops, &old_priv) == 0);
	assert(dpaa_wgfp_register(&new_ops, &new_priv) == -EBUSY);
	dpaa_wgfp_unregister();
	assert(gp_checked && !lock_held && wgfp_ops == NULL);
	assert(wgfp_per_port == 1);
	assert(dpaa_wgfp_register(&new_ops, &new_priv) == 0);
	assert(wgfp_ops_priv == &new_priv);
	return 0;
}
"""


def test_unregister_excludes_register_until_grace_period_ends(tmp_path):
    source = SOURCE.read_text()
    start = source.index("int dpaa_wgfp_register(")
    end = source.index("\nstatic void wgfp_egress_ern(", start)
    (tmp_path / "wgfp_register_body.h").write_text(source[start:end])
    harness = tmp_path / "harness.c"
    harness.write_text(REGISTER_HARNESS)
    binary = tmp_path / "wgfp_register"
    subprocess.run(
        ["cc", "-std=gnu11", "-Wall", "-Werror", "-Wno-unused-variable",
         "-I", str(tmp_path), str(harness), "-o", str(binary)],
        check=True,
    )
    subprocess.run([str(binary)], check=True)
