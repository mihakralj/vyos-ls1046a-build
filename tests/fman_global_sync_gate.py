"""Regression tests for the host-only global FMan SYNC safety gate."""

from pathlib import Path
import runpy
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests/dut/fman_global_sync.c"
GATE = runpy.run_path(str(ROOT / "bin/test-fman-global-sync.py"))

REFERENCE = r"""
static int model_sync(struct fman *fm, u32 *extc)
{
	u32 value;
	int rc = 0;
#ifdef FAKE_UNLOCK_BEFORE_WRITE
	bool locked = true;
#endif

#ifdef FAKE_DELAYED_PARALLEL_REASSERT
	bool delay;
	struct timespec duration = { .tv_nsec = 300000000 };

	mutex_lock(&fm->hw_lock);
	delay = fm->active && fm->second_started && fm->block_reads;
	mutex_unlock(&fm->hw_lock);
	if (delay)
		nanosleep(&duration, NULL);
#elif !defined(FAKE_IDLE_CHECK_RACE)
	mutex_lock(&fm->cc_port_lock);
#endif
#ifdef FAKE_STALE_SUCCESS
	if (fm->active && !fm->stuck) {
		value = fman_get_fpm_extc(fm);
		if (!(value & INV0))
			goto out;
	}
#endif
	value = fman_get_fpm_extc(fm);
	if (value & INV0) {
#ifdef FAKE_FALSE_SUCCESS
		goto out;
#elif !defined(FAKE_REASSERT) && !defined(FAKE_DELAYED_PARALLEL_REASSERT)
		rc = -EBUSY;
		goto out;
#endif
	}
#ifdef FAKE_UNLOCK_BEFORE_WRITE
	mutex_unlock(&fm->cc_port_lock);
	locked = false;
#endif
	fman_set_fpm_extc(fm, INV0);
	for (unsigned int i = 0; i < 100000U; i++) {
		value = fman_get_fpm_extc(fm);
		if (!(value & INV0))
			goto out;
	}
	rc = -ETIMEDOUT;
out:
#ifdef FAKE_NO_EXTC_ON_ERROR
	if (extc && !rc)
#else
	if (extc)
#endif
		*extc = value;
#ifdef FAKE_UNLOCK_BEFORE_WRITE
	if (locked)
		mutex_unlock(&fm->cc_port_lock);
#elif !defined(FAKE_DELAYED_PARALLEL_REASSERT) && !defined(FAKE_IDLE_CHECK_RACE)
	mutex_unlock(&fm->cc_port_lock);
#endif
	return rc;
}

static int fman_port_set_cc_base(struct fman_port *port, u32 off)
{
	return model_sync(port->fm, NULL);
}

static int fman_port_set_oh_cc_base(struct fman_port *port, u32 off)
{
	return model_sync(port->fm, NULL);
}

static int fman_pcd_ehash_sync(struct fman_pcd_ehash_table *table, u32 *extc)
{
	int rc = model_sync(table->pcd->fman, extc);

	if (!rc && ehash_sync_inject) {
		ehash_sync_inject--;
		ehash_sync_injected++;
		table->pcd->fman->inv0_faulted = true;
		return -ETIMEDOUT;
	}
	return rc;
}

static int f177_debug(struct fman_pcd *pcd)
{
#ifdef FAKE_F177_FALSE_SUCCESS
	model_sync(pcd->fman, NULL);
	return 0;
#else
	return model_sync(pcd->fman, NULL);
#endif
}

static int f177_production(struct fman_pcd *pcd)
{
#ifdef FAKE_F177_FALSE_SUCCESS
	model_sync(pcd->fman, NULL);
	return 0;
#else
	return model_sync(pcd->fman, NULL);
#endif
}

static ssize_t fman_pcd_fe_extc_write(struct file *file, const char *buf,
				      size_t count, loff_t *offset)
{
#ifdef FAKE_DIAG_EBUSY
	return -EBUSY;
#else
	return -EOPNOTSUPP;
#endif
}
"""


@pytest.mark.parametrize(
    ("mutation", "returncode", "marker"),
    [
        (None, 0, "Global INV0 characterization: 0 uncontained checks"),
        ("FAKE_DIAG_EBUSY", 0, "Global INV0 characterization: 0 uncontained checks"),
        ("FAKE_NO_EXTC_ON_ERROR", 0, "Global INV0 characterization: 0 uncontained checks"),
        ("FAKE_REASSERT", 1, "subsequent operation leaves pending INV0 alone: NOT CONTAINED"),
        ("FAKE_FALSE_SUCCESS", 1, "pre-existing pending request: NOT CONTAINED"),
        ("FAKE_STALE_SUCCESS", 1, "late recovery covers later publication or refuses: NOT CONTAINED"),
        ("FAKE_F177_FALSE_SUCCESS", 1, "F-177 debug insert with INV0 already pending: rc=0"),
        ("FAKE_DELAYED_PARALLEL_REASSERT", 1, "concurrent writers: NOT CONTAINED"),
        ("FAKE_IDLE_CHECK_RACE", 1, "idle-check race: NOT CONTAINED"),
        ("FAKE_UNLOCK_BEFORE_WRITE", 1, "idle-check race: NOT CONTAINED"),
    ],
)
def test_global_sync_gate_controls(tmp_path, mutation, returncode, marker):
    (tmp_path / "fman_global_sync_body.h").write_text(REFERENCE)
    binary = tmp_path / "global-sync"
    command = [
        "cc", "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
        "-Wno-unused-parameter", "-Wno-unused-function", "-Wno-unused-variable",
        "-pthread", "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
    ]
    if mutation:
        command.append(f"-D{mutation}")
    subprocess.run(command, check=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    assert result.returncode == returncode, result.stdout + result.stderr
    assert marker in result.stdout
    if mutation == "FAKE_F177_FALSE_SUCCESS":
        assert "F-177 production insert with INV0 already pending: rc=0" in result.stdout


@pytest.mark.parametrize("rogue_path", [
    "drivers/net/ethernet/freescale/fman/fman_port.c",
    "drivers/net/ethernet/freescale/dpaa/rogue.c",
    "net/rogue.c",
])
def test_writer_inventory_refuses_unmodeled_direct_calls(tmp_path, rogue_path):
    port = tmp_path / "drivers/net/ethernet/freescale/fman/fman_port.c"
    port.parent.mkdir(parents=True)
    modeled = "void modeled(void) { fman_set_fpm_extc(fm, val); }\n"
    port.write_text(modeled)
    rogue = tmp_path / rogue_path
    rogue.parent.mkdir(parents=True, exist_ok=True)
    with rogue.open("a") as source:
        source.write("void unexpected(void) { fman_set_fpm_extc(fm, val); }\n")
    with pytest.raises(ValueError, match="unmodeled"):
        GATE["check_writer_inventory"](tmp_path, {port: [modeled]}, {})


def test_writer_inventory_ignores_comments_strings_and_declarations(tmp_path):
    port = tmp_path / "drivers/net/ethernet/freescale/fman/fman_port.c"
    port.parent.mkdir(parents=True)
    modeled = "void modeled(void) { fman_set_fpm_extc(fm, val); }\n"
    port.write_text(
        '/* fman_set_fpm_extc() */\n'
        'const char *message = "fman_set_fpm_extc()";\n'
        "void fman_set_fpm_extc(struct fman *fm, u32 val);\n"
        + modeled
    )
    GATE["check_writer_inventory"](tmp_path, {port: [modeled]}, {})


F177_BLOCK = "if (!err) { fman_set_fpm_extc(fm, INV0); }\n"


def test_f177_caller_lock_accepts_early_unlock_and_return():
    owner = (
        "int owner(void) {\n"
        "mutex_lock(&pcd->fe_lock);\n"
        "if (bad) { mutex_unlock(&pcd->fe_lock); return -EINVAL; }\n"
        + F177_BLOCK
        + "mutex_unlock(&pcd->fe_lock); return 0;\n}\n"
    )
    GATE["check_f177_caller_lock"](owner, F177_BLOCK, "owner")


@pytest.mark.parametrize("before", [
    "mutex_lock(&pcd->fe_lock); mutex_unlock(&pcd->fe_lock);",
    "if (conditional) mutex_lock(&pcd->fe_lock);",
    "if (conditional) { mutex_lock(&pcd->fe_lock); }",
    "mutex_lock(&pcd->fe_lock); if (bad) { mutex_unlock(&pcd->fe_lock); }",
    "goto skip; mutex_lock(&pcd->fe_lock); skip:",
])
def test_f177_caller_lock_rejects_unprotected_paths(before):
    owner = (
        "int owner(void) {\n"
        + before + "\n"
        + F177_BLOCK
        + "mutex_unlock(&pcd->fe_lock); return 0;\n}\n"
    )
    with pytest.raises(ValueError, match="FE ownership lock"):
        GATE["check_f177_caller_lock"](owner, F177_BLOCK, "owner")
