"""Host ASan/UBSan tests for WGFP Stage B: inline inner NAT44/NAT66 + conntrack refresh."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
FASTPATH = ROOT / "kernel/common/files/drivers/net/wireguard/fastpath.c"
HARNESS = ROOT / "tests/dut/wgfp_inner_nat.c"
CT_HARNESS = ROOT / "tests/dut/wgfp_ct_admit.c"


def _between(src: str, start: str, end: str) -> str:
    idx = src.index(start)
    return src[idx:src.index(end, idx)]


def test_stage_b_ct_admitted_flowtable_fallback(tmp_path):
    fp = FASTPATH.read_text()

    ct_code = _between(fp, "/* ---- learning ---- */", "\n/* Under wgfp_flows_lock")
    (tmp_path / "wgfp_ct_admit_body.h").write_text(ct_code)

    binary = tmp_path / "wgfp_ct_admit"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-unused-function",
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
            "-I", str(tmp_path), str(CT_HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True)


def test_stage_b_inline_nat_checksums_and_bounds(tmp_path):
    fp = FASTPATH.read_text()

    nat_code = _between(fp, "#define WGFP_NAT_SRC_IP", "\n/*\n * One NEON section a packet")
    (tmp_path / "wgfp_inner_nat_body.h").write_text(nat_code)

    binary = tmp_path / "wgfp_inner_nat"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-unused-function",
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
            "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True)


def test_stage_b_wiring_in_fastpath():
    fp = FASTPATH.read_text()
    learn = _between(fp, "void wg_fastpath_learn(", "\n/* ---- install, withdraw")
    rflow_hook = _between(fp, "static unsigned int wgfp_rflow_hook(", "\n/* After every other POST_ROUTING")
    rx_fwd = _between(fp, "static bool wgfp_rx_forward(", "\n/*\n * P6 in the P3 sink")
    peer_frame = _between(fp, "static int wgfp_peer_frame(", "\nstatic const struct dpaa_wgfp_ops")
    age_fn = _between(fp, "static void wgfp_age_fn(", "\n/* Teardown:")
    rx_tail = _between(fp, "bool wg_fastpath_rx_tail(", "\n/* P6 stack hand-off")

    ct_admit = _between(fp, "static bool wgfp_ct_admitted(", "\n/* Under wgfp_flows_lock")
    assert "IP_CT_TCP_FLAG_BE_LIBERAL" in ct_admit
    assert "IPS_SEQ_ADJUST" in ct_admit
    assert "nf_ct_zone_equal_any(" in ct_admit
    assert "wgfp_ct_admitted(" in learn
    assert "fl->nat = nat;" in learn
    assert "wgfp_nat_len_ok(" in peer_frame
    assert "&fl->nat" in peer_frame
    assert "wgfp_ct_nat_matches(" in age_fn
    assert "cmpxchg(&ct->timeout" in age_fn
    assert "kcalloc(WGFP_BATCH" in age_fn
    assert "r->nat = nat;" in rflow_hook
    assert "wgfp_nat_len_ok(" in rx_fwd
    assert "wgfp_apply_nat(" in rx_fwd
    # P7 must not forward an untranslated NAT rflow
    assert "r->nat.flags" in rx_tail
