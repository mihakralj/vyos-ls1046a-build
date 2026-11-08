"""Host regression tests for unique WGFP sink tuple identity."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/common/files/drivers/net/wireguard/fastpath.c"
HARNESS = ROOT / "tests/dut/wgfp_flow_identity.c"


def test_sink_lookup_rejects_cross_ingress_tuple_ambiguity(tmp_path):
    source = SOURCE.read_text()
    start = source.index("static struct wgfp_flow *wgfp_flow_find(")
    end = source.index("\nstatic void wgfp_flow_free_locked(", start)
    (tmp_path / "wgfp_flow_identity_body.h").write_text(source[start:end])
    binary = tmp_path / "wgfp_flow_identity"
    subprocess.run(
        ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
         "-I", str(tmp_path), str(HARNESS), "-o", str(binary)],
        check=True,
    )
    subprocess.run([str(binary)], check=True)

    assert "wgfp_flow_find_tuple(&t, skb->skb_iif, hash)" in source
    assert source.count("wgfp_tx_family_allowed(") >= 3
