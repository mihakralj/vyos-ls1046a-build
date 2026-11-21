#!/usr/bin/env python3
"""Replay F-320 DPAA FQ teardown and parent resource retention fixup."""

from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXUP = ROOT / "bin/kernel-fixups/F_320.py"
ETH_H = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.h")
ETH_C = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.c")


def fixture_sources():
    mod = runpy.run_path(str(FIXUP))
    h_blocks = [old for path, _, old, _ in mod["EDITS"] if path == ETH_H]
    c_blocks = [old for path, _, old, _ in mod["EDITS"] if path == ETH_C]
    c_blocks.append(
        "static struct platform_driver dpaa_driver = {\n"
        "\t.driver = {\n"
        "\t\t.suppress_bind_attrs = true,\n"
        "\t},\n"
        "};\n"
    )
    return "\n\n".join(h_blocks), "\n\n".join(c_blocks)


class F320FixupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.eth_h, self.eth_c = fixture_sources()
        for rel, content in ((ETH_H, self.eth_h), (ETH_C, self.eth_c)):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

    def run_fixup(self):
        return subprocess.run(
            ["python3", str(FIXUP)], cwd=self.root, capture_output=True, text=True,
        )

    def test_apply_and_idempotence(self):
        res = self.run_fixup()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        h_after = (self.root / ETH_H).read_text()
        c_after = (self.root / ETH_C).read_text()
        self.assertIn("fq_chunks[8]", h_after)
        self.assertIn("dpaa_free_parent_resources", c_after)
        self.assertIn("return -EBUSY;", c_after)
        self.assertNotIn("devm_", c_after)

        res = self.run_fixup()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("already applied", res.stdout)
        self.assertEqual((self.root / ETH_H).read_text(), h_after)
        self.assertEqual((self.root / ETH_C).read_text(), c_after)

    def test_drift_fails_atomically(self):
        for label, h, c in (
            ("missing unbind guard", self.eth_h,
             self.eth_c.replace(".suppress_bind_attrs = true,", "")),
            ("missing fq_free_entry", self.eth_h,
             self.eth_c.replace("static int dpaa_fq_free_entry(", "static int dpaa_fq_free_x(")),
            ("missing header field", "", self.eth_c),
        ):
            with self.subTest(label=label):
                (self.root / ETH_H).write_text(h)
                (self.root / ETH_C).write_text(c)
                res = self.run_fixup()
                self.assertNotEqual(res.returncode, 0, label)
                self.assertEqual((self.root / ETH_H).read_text(), h)
                self.assertEqual((self.root / ETH_C).read_text(), c)


if __name__ == "__main__":
    unittest.main()
