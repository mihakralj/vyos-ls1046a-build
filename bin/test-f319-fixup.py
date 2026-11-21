#!/usr/bin/env python3
"""Replay F-319 QMan MC serialization, timeout fence, and shutdown fixup."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXUP = ROOT / "bin/kernel-fixups/F_319.py"
MC_TEST = ROOT / "bin/test-qman-mc-timeout.py"
KERNEL = Path(os.environ["QMAN_KERNEL_TREE"]) if os.environ.get("QMAN_KERNEL_TREE") else None
QMAN = Path("drivers/soc/fsl/qbman/qman.c")


def minimal_qman_fixture():
    """Extract the exact anchor blocks from F_319.py for standalone drift tests."""
    import runpy

    mod = runpy.run_path(str(FIXUP))
    return "\n\n".join(old for _, old, _ in mod["EDITS"])


class F319FixupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        target = self.root / QMAN
        target.parent.mkdir(parents=True, exist_ok=True)
        self.base = minimal_qman_fixture()
        target.write_text(self.base)

    def run_fixup(self):
        return subprocess.run(
            ["python3", str(FIXUP)], cwd=self.root, capture_output=True, text=True,
        )

    def test_apply_and_idempotence(self):
        res = self.run_fixup()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        applied = (self.root / QMAN).read_text()
        self.assertIn("F-319: serialize QMan portal MC", applied)
        self.assertIn("bool wedged;", applied)
        self.assertIn("raw_spinlock_t lock;", applied)
        self.assertIn("QM_SHUTDOWN_DRAIN_TIMEOUT", applied)

        res = self.run_fixup()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("already applied", res.stdout)
        self.assertEqual((self.root / QMAN).read_text(), applied)

    def test_drift_fails_atomically(self):
        for label, damaged in (
            ("missing struct qm_mc", self.base.replace("struct qm_mc {", "struct qm_mc_x {", 1)),
            ("missing shutdown", self.base.replace("int qman_shutdown_fq(u32 fqid)", "int qman_shutdown_x(u32 fqid)", 1)),
            ("duplicate query_fq", self.base + "\n" + self.base[self.base.index("int qman_query_fq("):self.base.index("int qman_query_fq_np(")]),
        ):
            with self.subTest(label=label):
                (self.root / QMAN).write_text(damaged)
                res = self.run_fixup()
                self.assertNotEqual(res.returncode, 0, label)
                self.assertEqual((self.root / QMAN).read_text(), damaged)

    @unittest.skipUnless(KERNEL and (KERNEL / QMAN).exists(), "QMAN_KERNEL_TREE not set")
    def test_real_kernel_source_passes_strict_mc_and_shutdown_gate(self):
        for rel in (
            QMAN,
            Path("drivers/soc/fsl/qbman/dpaa_sys.h"),
            Path("drivers/soc/fsl/qbman/Kconfig"),
            Path(".config"),
        ):
            dst = self.root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(KERNEL / rel, dst)

        before = subprocess.run(
            ["python3", str(MC_TEST), str(self.root)],
            capture_output=True, text=True, check=False, timeout=30,
        )
        self.assertEqual(before.returncode, 1, before.stdout + before.stderr)

        res = self.run_fixup()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        again = self.run_fixup()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("already applied", again.stdout)

        for extra in ((), ("--check-retire-snapshot",), ("--check-verb",), ("--check-shutdown-responses",)):
            after = subprocess.run(
                ["python3", str(MC_TEST), str(self.root), *extra],
                capture_output=True, text=True, check=False, timeout=30,
            )
            self.assertEqual(after.returncode, 0, f"{extra}: {after.stdout + after.stderr}")


if __name__ == "__main__":
    unittest.main()
