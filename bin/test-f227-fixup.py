"""Replay F-227 on fresh, previously guarded and drifted source fixtures."""

from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
FIXUP = ROOT / "bin/kernel-fixups/F_227.py"
ETH_C = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.c")

DMA = (
    "\tif (unlikely(qm_fd_get_format(fd) == qm_fd_sg)) {\n"
    "\t\tdma_unmap_page(priv->tx_dma_dev, addr,\n"
)
SWBP = (
    "\tswbp = (struct dpaa_eth_swbp *)vaddr;\n"
    "\tskb = swbp->skb;\n"
    "\n"
    "\t/* No skb backpointer is set when running XDP. An xdp_frame\n"
    "\t * backpointer is saved instead.\n"
    "\t */\n"
    "\tif (!skb) {\n"
    "\t\txdp_return_frame(swbp->xdpf);\n"
    "\t\treturn NULL;\n"
    "\t}\n"
)
IC_COMMENT = "\t/* F-069a v9: capture frame-data vaddr (no bp var found) */\n"
IC_MANGLED = (
    IC_COMMENT +
    "\tfman_pcd_ic_vaddr = vaddr;\tconst struct qm_sg_entry *sgt;\n"
)
IC_CAPTURE = IC_COMMENT + "\tfman_pcd_ic_vaddr = vaddr;\n\n"
LEGACY_GUARD = (
    "\t/* F-227 (2026-08-21): reject FMan-forwarded HIT FDs before touching\n"
    "\t * swbp. Kernel skb/XDP TX confirms always carry FSL_DPAA_BPID_INV\n"
    "\t * (set in skb_to_contig_fd/skb_to_sg_fd/dpaa_xdp_xmit_frame). A HIT\n"
    "\t * frame that landed a confirm FD on a confirmed egress FQ carries a\n"
    "\t * live RX BMan pool id and has NO dpaa_eth_swbp; its buffer was\n"
    "\t * already deallocated by FMan (EBD=1). Dereferencing swbp->xdpf here\n"
    "\t * panics (NULL/garbage), and dpaa_fd_release() would double-free.\n"
    "\t * Ignore the confirm: nothing to free on the host side. This guard\n"
    "\t * should never fire in a correct config (ask.ko fails closed to SW\n"
    "\t * unless it has a no-confirm FQ); a firing means a HIT reached a\n"
    "\t * confirmed FQ, so log it ratelimited for diagnosis. */\n"
    "\tif (unlikely(fd->bpid != FSL_DPAA_BPID_INV)) {\n"
    "\t\tnet_warn_ratelimited(\"%s: F-227 dropped confirm for FMan HIT FD bpid=%u (HIT on confirmed FQ)\\n\",\n"
    "\t\t\t\t     priv->net_dev->name, fd->bpid);\n"
    "\t\treturn NULL;\n"
    "\t}\n"
    "\n"
)


def fixture(guard="", capture=False):
    return (
        "/* F-199 recorded an F-227 dropped-confirm count. */\n"
        "struct sk_buff *dpaa_cleanup_tx_fd(const struct dpaa_priv *priv,\n"
        "\t\t\t\t  const struct qm_fd *fd, bool ts)\n"
        "{\n"
        + (IC_MANGLED if capture else "")
        + DMA
        + "\t\t\t       len, dma_dir);\n"
        + "\t}\n"
        + guard
        + SWBP
        + "\treturn skb;\n"
        + "}\n"
    )


class F227FixupTests(unittest.TestCase):
    def apply(self, tmp_path, source):
        target = tmp_path / ETH_C
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
        result = subprocess.run(
            ["python3", str(FIXUP)], cwd=tmp_path, capture_output=True, text=True,
        )
        return target, result

    def test_fresh_and_legacy_guards_precede_dma(self):
        for name, old_guard in (("fresh", ""), ("legacy", LEGACY_GUARD)):
            for capture in (False, True):
                with self.subTest(name=name, capture=capture), tempfile.TemporaryDirectory() as directory:
                    target, result = self.apply(Path(directory), fixture(old_guard, capture))
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    patched = target.read_text()
                    guard = "if (unlikely(fd->bpid != FSL_DPAA_BPID_INV))"
                    self.assertEqual(patched.count(guard), 1)
                    self.assertLess(patched.index(guard), patched.index(DMA))
                    self.assertLess(patched.index(DMA), patched.index(SWBP))
                    if capture:
                        self.assertEqual(patched.count(IC_CAPTURE), 1)
                        self.assertLess(patched.index(guard), patched.index(IC_CAPTURE))
                        self.assertLess(patched.index(IC_CAPTURE), patched.index(DMA))

                    _, result = self.apply(Path(directory), patched)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("already applied", result.stdout)
                    self.assertEqual(target.read_text(), patched)

    def test_upgrades_guard_after_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            target, result = self.apply(path, fixture(capture=True))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            patched = target.read_text()
            old_order = patched.replace(IC_CAPTURE, "", 1).replace(
                "\tconst struct qm_sg_entry *sgt;\n", IC_MANGLED, 1,
            )
            target, result = self.apply(path, old_order)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(target.read_text(), patched)

    def test_extra_guard_marker_fails_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            target, result = self.apply(path, fixture())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            source = target.read_text().replace(
                DMA, "\t/* F-227 unexpected extra guard */\n" + DMA,
            )
            target, result = self.apply(path, source)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FATAL", result.stdout)
            self.assertEqual(target.read_text(), source)

    def test_drift_fails_without_writing(self):
        for change in ("unknown-guard", "missing-dma", "duplicate-dma",
                       "unknown-capture", "duplicate-capture",
                       "changed-capture-value", "extra-capture-read",
                       "duplicate-function"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                source = fixture(capture="capture" in change)
                if change == "unknown-guard":
                    source = source.replace(DMA, "\t/* F-227 unrecognized guard */\n" + DMA)
                elif change == "missing-dma":
                    source = source.replace(DMA, "")
                elif change == "duplicate-dma":
                    source = source.replace(DMA, DMA + DMA)
                elif change == "unknown-capture":
                    source = source.replace(IC_COMMENT, "\t/* changed diagnostic */\n")
                elif change == "duplicate-capture":
                    source = source.replace(IC_MANGLED, IC_MANGLED + IC_CAPTURE)
                elif change == "changed-capture-value":
                    source = source.replace(
                        "fman_pcd_ic_vaddr = vaddr;",
                        "fman_pcd_ic_vaddr = vaddr + 1;",
                    )
                elif change == "extra-capture-read":
                    source = source.replace(
                        IC_MANGLED,
                        IC_MANGLED + "\tif (fman_pcd_ic_vaddr) return NULL;\n",
                    )
                else:
                    source += fixture()
                target, result = self.apply(Path(directory), source)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FATAL", result.stdout)
                self.assertEqual(target.read_text(), source)


if __name__ == "__main__":
    unittest.main()
