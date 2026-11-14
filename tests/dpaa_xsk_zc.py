"""Host regression test for the DPAA AF_XDP zero-copy cookie map."""

from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/"
          "af_xdp_pool/af_xdp_pool_main.c")
HARNESS = ROOT / "tests/dut/dpaa_xsk_zc.c"


def extract_map():
    source = SOURCE.read_text()
    start = source.index("/* AFX_MAP_BEGIN")
    end = source.index("/* AFX_MAP_END */")
    return source[start:end]


def test_cookie_map_semantics(tmp_path):
    (tmp_path / "afx_map.h").write_text(extract_map())
    binary = tmp_path / "zc"
    subprocess.run(
        ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", f"-I{tmp_path}",
         str(HARNESS), "-o", str(binary)],
        check=True, capture_output=True, text=True,
    )
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
