"""Host-only characterization of DPAA egress and QMan FQ lifetimes."""

from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/test-dpaa-lifetime.py"
KERNEL = ROOT / "kernel/common/work/linux-6.18.60"
PORTAL_FIXUP = ROOT / "bin/kernel-fixups/F_296.py"
CGR_FIXUP = ROOT / "bin/kernel-fixups/F_297.py"
CGR_TEST = ROOT / "bin/test-qman-cgr-update.py"
OWNER_FIXUP = ROOT / "bin/kernel-fixups/F_298.py"
OWNER_TEST = ROOT / "bin/test-qman-cgr-owner.py"
MC_TEST = ROOT / "bin/test-qman-mc-timeout.py"
PORTALS = Path("drivers/soc/fsl/qbman")
PORTAL_GUARD = (
    "\t\t/* F-296: no sysfs unbind; per-CPU portals retain devres. */\n"
    "\t\t.suppress_bind_attrs = true,\n"
)


def run(*arguments):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(KERNEL), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_current_source_reports_uncontained_device_and_fq_cases():
    result = run("--characterize")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "WGFP reused ifindex: NOT CONTAINED" in result.stdout
    assert "ASK reused ifindex: NOT CONTAINED" in result.stdout
    assert "Egress characterization: 2 uncontained case(s)" in result.stdout
    assert "pending retirement: NOT CONTAINED" in result.stdout
    assert "nonempty retired queue: NOT CONTAINED" in result.stdout
    assert "retire timeout: NOT CONTAINED" in result.stdout
    assert "QMan teardown characterization: 4 uncontained case(s)" in result.stdout
    assert "probe unwind ignores FQ cleanup status" in result.stdout
    assert "remove logs errors but still releases the netdev" in result.stdout
    assert "does not prove hardware" in result.stdout


def test_oracle_controls_detect_simulated_retention_and_invalidation():
    result = run("--oracle-controls")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Egress characterization: 0 uncontained case(s)" in result.stdout
    assert "QMan teardown characterization: 0 uncontained case(s)" in result.stdout
    assert "simulate protections" in result.stdout


def test_qman_shutdown_reports_uncontained_retirement_and_ring_cases():
    result = subprocess.run(
        [sys.executable, str(MC_TEST), str(KERNEL), "--characterize"],
        capture_output=True, text=True, check=False, timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    for case in (
        "foreign FQRN", "pending FQRN flags", "foreign FQRL",
        "unrelated MR/DQRR entries", "result overwritten before snapshot",
    ):
        assert f"QMan shutdown {case}: NOT CONTAINED" in result.stdout
    assert "QMan shutdown characterization: 5 uncontained case(s)" in result.stdout
    assert "characterization is not a safety pass" in result.stdout


def test_qman_shutdown_oracle_simulates_refusal_without_claiming_a_fix():
    result = subprocess.run(
        [sys.executable, str(MC_TEST), str(KERNEL), "--oracle-controls"],
        capture_output=True, text=True, check=False, timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Oracle controls simulate containment, not production code" in result.stdout
    assert "QMan shutdown pending FQRN flags: simulated refusal" in result.stdout
    assert "QMan shutdown characterization: 0 uncontained case(s)" in result.stdout


def test_qman_strict_gate_stays_closed_on_uncontained_cases():
    result = subprocess.run(
        [sys.executable, str(MC_TEST), str(KERNEL)],
        capture_output=True, text=True, check=False, timeout=30,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "QMan MC timeout: 2 uncontained checks" in result.stdout
    assert "QMan shutdown characterization: 5 uncontained case(s)" in result.stdout


@pytest.fixture
def qbman_tree(tmp_path):
    files = [
        *(PORTALS / name for name in (
            "qman_portal.c", "bman_portal.c", "qman.c", "bman.c",
            "Kconfig", "Makefile",
        )),
        Path("drivers/base/bus.c"),
    ]
    for path in files:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(KERNEL / path, target)
    for name in ("qman", "bman"):
        portal = tmp_path / PORTALS / f"{name}_portal.c"
        portal.write_text(portal.read_text().replace(PORTAL_GUARD, ""))
    return tmp_path


def portal_fixup(tree):
    return subprocess.run(
        [sys.executable, str(PORTAL_FIXUP)], cwd=tree,
        capture_output=True, text=True, check=False,
    )


def portal_check(tree):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(tree), "--verify-qbman-portal-guard"],
        capture_output=True, text=True, check=False,
    )


def test_qbman_portal_unbind_guards_apply_once(qbman_tree):
    assert portal_check(qbman_tree).returncode != 0
    result = portal_fixup(qbman_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    result = portal_check(qbman_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    paths = [qbman_tree / PORTALS / f"{name}_portal.c" for name in ("qman", "bman")]
    before = [path.read_bytes() for path in paths]
    assert portal_fixup(qbman_tree).returncode == 0
    assert [path.read_bytes() for path in paths] == before


@pytest.mark.parametrize("damage", ["missing", "duplicate", "partial"])
def test_qbman_guard_refuses_partial_or_ambiguous_source(qbman_tree, damage):
    qman = qbman_tree / PORTALS / "qman_portal.c"
    bman = qbman_tree / PORTALS / "bman_portal.c"
    if damage == "missing":
        bman.write_text(bman.read_text().replace(
            "static struct platform_driver bman_portal_driver",
            "static struct platform_driver other_portal_driver",
        ))
    elif damage == "duplicate":
        source = bman.read_text()
        start = source.index("static struct platform_driver bman_portal_driver = {")
        end = source.index("\n};", start) + 3
        bman.write_text(source + "\n" + source[start:end] + "\n")
    else:
        assert portal_fixup(qbman_tree).returncode == 0
        bman.write_text(bman.read_text().replace(PORTAL_GUARD, ""))
    before = [qman.read_bytes(), bman.read_bytes()]
    result = portal_fixup(qbman_tree)
    assert result.returncode != 0
    assert "FATAL" in result.stderr
    assert [qman.read_bytes(), bman.read_bytes()] == before


@pytest.fixture
def qman_update_tree(tmp_path):
    files = (
        PORTALS / "qman.c",
        Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"),
        Path("kernel/smp.c"),
    )
    for path in files:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(KERNEL / path, target)
    qman = tmp_path / files[0]
    source = qman.read_text()
    fixup = runpy.run_path(str(CGR_FIXUP))
    if fixup["MARKER"] in source:
        applied = [
            text for text in (fixup["NEW"], fixup["NEW_WITH_PORTAL_CPU"])
            if source.count(text) == 1
        ]
        assert len(applied) == 1
        qman.write_text(source.replace(applied[0], fixup["OLD"], 1))
    return tmp_path


def cgr_fixup(tree):
    return subprocess.run(
        [sys.executable, str(CGR_FIXUP)], cwd=tree,
        capture_output=True, text=True, check=False,
    )


def cgr_check(tree):
    return subprocess.run(
        [sys.executable, str(CGR_TEST), str(tree)],
        capture_output=True, text=True, check=False,
    )


def test_cgr_update_reports_remote_dispatch_failure(qman_update_tree):
    before = cgr_check(qman_update_tree)
    assert before.returncode != 0
    assert "offline dispatch returned 0" in before.stderr
    result = cgr_fixup(qman_update_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    result = cgr_check(qman_update_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    qman = qman_update_tree / PORTALS / "qman.c"
    before = qman.read_bytes()
    assert cgr_fixup(qman_update_tree).returncode == 0
    assert qman.read_bytes() == before


def test_cgr_update_fixup_rejects_changed_anchor(qman_update_tree):
    qman = qman_update_tree / PORTALS / "qman.c"
    qman.write_text(qman.read_text().replace(
        "int qman_update_cgr_safe(", "int changed_cgr_update(", 1,
    ))
    before = qman.read_bytes()
    result = cgr_fixup(qman_update_tree)
    assert result.returncode != 0
    assert "FATAL" in result.stderr
    assert qman.read_bytes() == before


@pytest.fixture
def qman_owner_tree(tmp_path):
    files = (
        PORTALS / "qman.c",
        Path("include/soc/fsl/qman.h"),
        Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"),
        Path("kernel/smp.c"),
    )
    for path in files:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(KERNEL / path, target)

    qman = tmp_path / files[0]
    header = tmp_path / files[1]
    fixup = runpy.run_path(str(OWNER_FIXUP))
    source = qman.read_text()
    header_source = header.read_text()
    if fixup["MARKER"] in header_source:
        assert header_source.count(fixup["NEW_FIELD"]) == 1
        assert source.count(fixup["NEW_ASSIGN"]) == 1
        source = source.replace(fixup["NEW_ASSIGN"], fixup["OLD_ASSIGN"], 1)
        assert source.count(fixup["NEW_LOOKUP"]) == 4
        source = source.replace(fixup["NEW_LOOKUP"], fixup["OLD_LOOKUP"])
        source = source.replace(
            "void qman_init_cgr_all(void)\n",
            fixup["OLD_ARRAY"] + "\nvoid qman_init_cgr_all(void)\n",
            1,
        )
        qman.write_text(source)
        header.write_text(header_source.replace(
            fixup["NEW_FIELD"], fixup["OLD_FIELD"], 1,
        ))
    result = cgr_fixup(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    return tmp_path


def owner_fixup(tree):
    return subprocess.run(
        [sys.executable, str(OWNER_FIXUP)], cwd=tree,
        capture_output=True, text=True, check=False,
    )


def owner_check(tree):
    return subprocess.run(
        [sys.executable, str(OWNER_TEST), str(tree)],
        capture_output=True, text=True, check=False,
    )


def test_shared_cgrid_routes_each_registration_to_its_portal(qman_owner_tree):
    before = owner_check(qman_owner_tree)
    assert before.returncode == 1
    assert "shared CGRID update missed first portal" in before.stderr
    assert "shared CGRID delete left the first callback linked" in before.stderr

    result = owner_fixup(qman_owner_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    result = owner_check(qman_owner_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    result = cgr_check(qman_owner_tree)
    assert result.returncode == 0, result.stdout + result.stderr

    paths = [qman_owner_tree / PORTALS / "qman.c",
             qman_owner_tree / "include/soc/fsl/qman.h"]
    before = [path.read_bytes() for path in paths]
    assert owner_fixup(qman_owner_tree).returncode == 0
    assert cgr_fixup(qman_owner_tree).returncode == 0
    assert [path.read_bytes() for path in paths] == before


@pytest.mark.parametrize("damage", ["duplicate", "partial", "lookup"])
def test_cgr_owner_fixup_rejects_ambiguous_source(qman_owner_tree, damage):
    qman = qman_owner_tree / PORTALS / "qman.c"
    header = qman_owner_tree / "include/soc/fsl/qman.h"
    fixup = runpy.run_path(str(OWNER_FIXUP))
    if damage == "duplicate":
        header.write_text(header.read_text().replace(
            fixup["OLD_FIELD"], fixup["OLD_FIELD"] * 2, 1,
        ))
    elif damage == "partial":
        qman.write_text(qman.read_text().replace(
            fixup["OLD_ASSIGN"], fixup["NEW_ASSIGN"], 1,
        ))
    else:
        qman.write_text(qman.read_text().replace(fixup["OLD_LOOKUP"], "0", 1))
    before = [qman.read_bytes(), header.read_bytes()]
    result = owner_fixup(qman_owner_tree)
    assert result.returncode != 0
    assert "FATAL" in result.stderr
    assert [qman.read_bytes(), header.read_bytes()] == before
