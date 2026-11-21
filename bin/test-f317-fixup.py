#!/usr/bin/env python3
"""Replay the unsafe IC probe retirement and reject source drift."""

from pathlib import Path
import importlib.util
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXUP = ROOT / "bin/kernel-fixups/F_317.py"
ETH = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.c")
PCD = Path("drivers/net/ethernet/freescale/fman/fman_pcd.c")
CAPTURE = (
    "\t/* F-069a v9: capture frame-data vaddr (no bp var found) */\n"
    "\tfman_pcd_ic_vaddr = vaddr;\n\n"
)


def original_show():
    patch = (ROOT / "kernel/common/patches/board/0169-fman-pcd-fe-obs-canary.patch").read_text()
    start = patch.index("+static int fman_pcd_ic_probe_show(")
    end = patch.index("\n+}\n", start) + len("\n+}\n")
    return "".join(line[1:] for line in patch[start:end].splitlines(keepends=True))


def sources():
    eth = (
        "extern void *fman_pcd_ic_vaddr;\n"
        "struct sk_buff *dpaa_cleanup_tx_fd(const struct dpaa_priv *priv,\n"
        "\t\t\t\t  const struct qm_fd *fd, bool ts)\n"
        "{\n"
        "\t/* F-227: a real-BPID FD is not a kernel skb/XDP TX mapping. Its */\n"
        "\tif (unlikely(fd->bpid != FSL_DPAA_BPID_INV)) {\n"
        "\t\treturn NULL;\n"
        "\t}\n\n"
        + CAPTURE +
        "\treturn NULL;\n"
        "}\n"
    )
    return eth, (
        "void *fman_pcd_ic_vaddr;\n"
        "extern void *fman_pcd_ic_vaddr;\n"
        + original_show()
    )


class F317FixupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.eth, self.pcd = sources()
        for path, data in ((ETH, self.eth), (PCD, self.pcd)):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(data)

    def run_fixup(self):
        return subprocess.run(
            ["python3", str(FIXUP)], cwd=self.root, capture_output=True, text=True,
        )

    def test_retirement_and_idempotence(self):
        result = self.run_fixup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        eth, pcd = (self.root / ETH).read_text(), (self.root / PCD).read_text()
        self.assertNotIn(CAPTURE, eth)
        self.assertNotIn("fman_pcd_ic_vaddr =", eth)
        self.assertIn("disabled: TX buffer lifetime is not tracked", pcd)
        self.assertNotIn("vaddr = fman_pcd_ic_vaddr;", pcd)
        result = self.run_fixup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / ETH).read_text(), eth)
        self.assertEqual((self.root / PCD).read_text(), pcd)

    def test_drift_fails_before_writing_either_file(self):
        for name, eth, pcd in (
            ("missing capture", self.eth.replace(CAPTURE, ""), self.pcd),
            ("duplicate capture", self.eth.replace(CAPTURE, CAPTURE * 2), self.pcd),
            ("changed capture", self.eth.replace("= vaddr;", "= vaddr + 1;"), self.pcd),
            ("missing guard", self.eth.replace("fd->bpid != FSL_DPAA_BPID_INV", "false"), self.pcd),
            ("capture before guard", self.eth.replace(CAPTURE, "").replace(
                "\t/* F-227:", CAPTURE + "\t/* F-227:", 1), self.pcd),
            ("extra writer", self.eth + "void f(void) { fman_pcd_ic_vaddr = 0; }\n", self.pcd),
            ("WRITE_ONCE writer", self.eth + "void f(void) { WRITE_ONCE(fman_pcd_ic_vaddr, 0); }\n", self.pcd),
            ("pointer alias", self.eth + "void **p = &fman_pcd_ic_vaddr;\n", self.pcd),
            ("changed consumer", self.eth, self.pcd.replace("max_fwd = 512;", "max_fwd = 256;")),
            ("duplicate consumer", self.eth, self.pcd + original_show()),
        ):
            with self.subTest(name=name):
                (self.root / ETH).write_text(eth)
                (self.root / PCD).write_text(pcd)
                result = self.run_fixup()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("FATAL", result.stdout + result.stderr)
                self.assertEqual((self.root / ETH).read_text(), eth)
                self.assertEqual((self.root / PCD).read_text(), pcd)

    def test_f227_replay_after_retirement(self):
        spec = importlib.util.spec_from_file_location(
            "f227_tests", ROOT / "bin/test-f227-fixup.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        (self.root / ETH).write_text(
            "extern void *fman_pcd_ic_vaddr;\n" + module.fixture(capture=True)
        )
        f227 = ["python3", str(ROOT / "bin/kernel-fixups/F_227.py")]
        first = subprocess.run(f227, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        result = self.run_fixup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        eth, pcd = (self.root / ETH).read_text(), (self.root / PCD).read_text()
        again = subprocess.run(f227, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("already applied", again.stdout)
        self.assertEqual((self.root / ETH).read_text(), eth)
        self.assertEqual((self.root / PCD).read_text(), pcd)

    def test_retired_reader_rejects_live_capture_or_missing_guard(self):
        result = self.run_fixup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        retired_eth = (self.root / ETH).read_text()
        retired_pcd = (self.root / PCD).read_text()
        for eth in (self.eth, retired_eth.replace(
                "fd->bpid != FSL_DPAA_BPID_INV", "false")):
            with self.subTest(eth=eth):
                (self.root / ETH).write_text(eth)
                result = self.run_fixup()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FATAL", result.stdout + result.stderr)
                self.assertEqual((self.root / ETH).read_text(), eth)
                self.assertEqual((self.root / PCD).read_text(), retired_pcd)


if __name__ == "__main__":
    unittest.main()
