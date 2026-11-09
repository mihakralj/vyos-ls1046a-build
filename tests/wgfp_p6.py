"""Host tests for the actual P6 (zero-skb decrypt) receive bodies of fastpath.c."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/common/files/drivers/net/wireguard/fastpath.c"
HARNESS = ROOT / "tests/dut/wgfp_p6.c"


def _between(source, start, end):
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


def test_p6_decrypt_forward_handoff_and_punts(tmp_path):
    source = SOURCE.read_text()
    body = _between(source, "static bool wgfp_parse(", "\n/* Writer side")
    body += "\n" + _between(source, "static int wgfp_rx_fast_stack(", "\n/*\n * P2b-3 slow-path punt")
    (tmp_path / "wgfp_p6_body.h").write_text(body)
    tail = _between(source, "bool wg_fastpath_rx_tail(", "\n/* P6 stack hand-off")
    (tmp_path / "wgfp_p7_body.h").write_text(tail)
    binary = tmp_path / "wgfp_p6"
    subprocess.run(
        ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
         "-Wno-unused-function", "-fsanitize=address,undefined", "-I", str(tmp_path),
         str(HARNESS), "-o", str(binary)],
        check=True,
    )
    subprocess.run([str(binary)], check=True)


def test_p6_wiring():
    source = SOURCE.read_text()
    rx = _between(source, "static int wgfp_rx_frame(", "\nstatic int wgfp_peer_frame(")
    # P6 runs before the P3 copy, and only when enabled
    assert rx.index("wgfp_rx_fast_frame(f, fl, dev)") < rx.index("netdev_alloc_skb_ip_align")
    stop = _between(source, "static int wgfp_peer_stop(void)\n{", "\n/* @names")
    # learning unhooked before the sinks go, records freed after
    assert stop.index("wgfp_nf_unregister()") < stop.index("dpaa_wgfp_unregister()")
    assert stop.index("dpaa_wgfp_unregister()") < stop.index("wgfp_rflows_reap(true)")
    # the replay check follows authentication
    fast = _between(source, "static int wgfp_rx_fast_frame(", "\n/*\n * P2b-3 slow-path punt")
    assert fast.index("chacha20poly1305_decrypt(") < fast.index("wg_fastpath_rx_validate(")
