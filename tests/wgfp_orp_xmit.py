"""Host test for the real wgfp_orp_xmit(): immediate enqueue, no clock read on the fast path."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
WGFP = ROOT / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_wgfp.c"
HARNESS = ROOT / "tests/dut/wgfp_orp_xmit.c"


def extract(source, start, end):
    offset = source.index(start)
    return source[offset:source.index(end, offset)]


def test_orp_xmit(tmp_path):
    wgfp = WGFP.read_text()
    body = "\n\n".join([
        extract(wgfp, "struct wgfp_orp_ctx {", "\n/* One WGFP NAPI per CPU."),
        extract(wgfp, "static bool wgfp_orp_xmit(", "\nvoid dpaa_wgfp_xmit("),
    ])
    (tmp_path / "wgfp_orp_xmit_body.h").write_text(body + "\n")
    binary = tmp_path / "wgfp_orp_xmit"
    subprocess.run(
        ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
         "-I", str(tmp_path), str(HARNESS), "-o", str(binary)],
        check=True,
    )
    subprocess.run([str(binary)], check=True)
