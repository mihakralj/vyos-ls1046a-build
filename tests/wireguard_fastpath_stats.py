"""Host regression test for fast-path per-CPU telemetry."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "kernel/common/files/include/linux/fsl/dpaa_wgfp.h"
HARNESS = ROOT / "tests/dut/wgfp_stats.c"


def test_per_cpu_stats_include_all_possible_cpus(tmp_path):
    source = HEADER.read_text()
    start = source.index("static inline void dpaa_wgfp_stat_inc(")
    end = source.index("\nstruct dpaa_wgfp_frame", start)
    (tmp_path / "wgfp_stats_body.h").write_text(source[start:end])
    binary = tmp_path / "wgfp_stats"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-pthread",
            "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True)
