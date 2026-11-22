"""Host RFC 8439 verification of fastpath.c's out-of-place 1-pass NEON seal."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/common/files/drivers/net/wireguard/fastpath.c"
HARNESS = ROOT / "tests/dut/wgfp_seal_one_dst.c"


def test_wgfp_seal_one_dst_matches_rfc8439_and_never_mutates_src(tmp_path):
    src = SOURCE.read_text()
    start = src.index("static void wgfp_chacha_setup(")
    end = src.index("\n/* @n plaintext bytes from @src into @dst", start)
    (tmp_path / "wgfp_seal_one_dst_body.h").write_text(src[start:end])
    binary = tmp_path / "wgfp_seal_one_dst"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-unused-function", "-Wno-unused-variable",
            "-fsanitize=address,undefined",
            "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True)
