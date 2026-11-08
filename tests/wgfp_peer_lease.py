"""Host tests for the actual WGFP binding reservation and control callbacks."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/common/files/drivers/net/wireguard/fastpath.c"
HARNESS = ROOT / "tests/dut/wgfp_peer_lease.c"


def test_peer_reservation_serializes_writers_and_recovers_lost_responses(tmp_path):
    source = SOURCE.read_text()
    sections = [
        ("static int wgfp_lab_cfg_set(", "\nstatic int wgfp_lab_cfg_get("),
        ("static void wgfp_stop_work_fn(", "\nstatic DECLARE_WORK("),
        ("static int wgfp_netdev_event(", "\nstatic struct notifier_block wgfp_netdev_nb"),
        ("static int wgfp_peer_stop(void)\n{", "\nstatic const struct kernel_param_ops wgfp_peer_lease_ops"),
    ]
    body = "\n".join(
        source[begin:source.index(end, begin)]
        for start, end in sections
        for begin in [source.index(start)]
    )
    begin = source.index("void wg_fastpath_uninit(void)")
    body += "\n" + source[begin:source.index("\n}", begin) + 2]
    (tmp_path / "wgfp_peer_lease_body.h").write_text(body)
    binary = tmp_path / "wgfp_peer_lease"
    subprocess.run(
        ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-parameter", "-pthread", "-I", str(tmp_path),
         str(HARNESS), "-o", str(binary)],
        check=True,
    )
    subprocess.run([str(binary)], check=True)
