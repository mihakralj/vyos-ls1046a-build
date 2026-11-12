"""Host regression test for the DPAA AF_XDP detach BMan drain."""

from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/"
          "af_xdp_pool/af_xdp_pool_main.c")
HARNESS = ROOT / "tests/dut/dpaa_xsk_drain.c"

# The loop that shipped before the fix: it waits for bman_acquire() to
# return 0, which the kernel never does.
LEGACY = """
static int legacy_drain(struct bman_pool *bpool)
{
	struct bm_buffer bmbs[8];
	int loops, n;

	for (loops = 0; loops < 16384; loops++) {
		n = bman_acquire(bpool, bmbs, 8);
		if (n < 0)
			return -EAGAIN;
		if (!n)
			return 0;
	}
	return -EAGAIN;
}
"""


def run_harness(tmp_path, helper, call):
    (tmp_path / "drain.h").write_text(helper + f"\n#define DRAIN_CALL {call}\n")
    binary = tmp_path / "drain"
    subprocess.run(
        ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", f"-I{tmp_path}",
         str(HARNESS), "-o", str(binary)],
        check=True, capture_output=True, text=True,
    )
    return subprocess.run([str(binary)], capture_output=True, text=True)


def extract_helper():
    source = SOURCE.read_text()
    start = source.index("static int af_xdp_pool_drain_bman(")
    end = source.index("\n}\n", start) + 3
    return source[start:end]


def test_drain_handles_every_pool_size(tmp_path):
    result = run_harness(tmp_path, extract_helper(), "af_xdp_pool_drain_bman")
    assert result.returncode == 0, result.stderr


def test_legacy_drain_is_detected(tmp_path):
    result = run_harness(tmp_path, LEGACY, "legacy_drain")
    assert result.returncode != 0
    assert "ret=-11" in result.stderr
