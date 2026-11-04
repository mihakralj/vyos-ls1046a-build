"""Host regression tests for WireGuard's DPAA receive fast path."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/common/files/drivers/net/wireguard/fastpath.c"
HARNESS = ROOT / "tests/dut/wgfp_rx.c"


def test_rx_lifetime_and_udp_checksums(tmp_path):
    source = SOURCE.read_text()
    start = source.index("static int wgfp_rx_frame(")
    end = source.index("\nstatic int wgfp_peer_frame(", start)
    (tmp_path / "wgfp_rx_body.h").write_text(source[start:end])
    binary = tmp_path / "wgfp_rx"
    subprocess.run(
        ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
         "-I", str(tmp_path), str(HARNESS), "-o", str(binary)],
        check=True,
    )
    subprocess.run([str(binary)], check=True)


def test_rx_admission_requires_explicit_opt_in(tmp_path):
    source = SOURCE.read_text()
    flag_start = source.index("static bool wgfp_rx")
    flag_end = source.index("\n", flag_start)
    start = source.index("static void wgfp_pst_rx_sync(", flag_end)
    start = source.index("static void wgfp_pst_rx_sync(", start + 1)
    end = source.index("\n/* ---- learning ---- */", start)
    (tmp_path / "wgfp_rx_admission_body.h").write_text(
        source[flag_start:flag_end] + "\n" + source[start:end]
    )
    binary = tmp_path / "wgfp_rx_admission"
    subprocess.run(
        ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
         "-I", str(tmp_path), str(ROOT / "tests/dut/wgfp_rx_admission.c"),
         "-o", str(binary)],
        check=True,
    )
    subprocess.run([str(binary)], check=True)
