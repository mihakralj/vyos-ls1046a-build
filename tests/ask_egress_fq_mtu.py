"""Host ASan/UBSan test for MTU-aware per-flow egress FQ selection in ask_hw.c."""

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_hw.c"
HARNESS = ROOT / "tests/dut/ask_egress_fq_mtu.c"


def test_ask_hw_flow_fq_is_mtu_aware(tmp_path):
    src = SOURCE.read_text()
    start = src.index("static u32 ask_hw_flow_fq_get(")
    end = src.index("\n/* Account an L2 bridge record", start)
    (tmp_path / "ask_flow_fq_body.h").write_text(src[start:end])

    defines = []
    if "ask_egress_fq_min_mtu" in src[start:end]:
        defines.append("-DHAVE_EGRESS_FQ_MIN_MTU")

    binary = tmp_path / "ask_egress_fq_mtu"
    subprocess.run(
        [
            "cc",
            "-std=gnu11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fsanitize=address,undefined",
            "-fno-omit-frame-pointer",
            "-I",
            str(tmp_path),
            *defines,
            str(HARNESS),
            "-o",
            str(binary),
        ],
        check=True,
    )
    res = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
    assert "ask_egress_fq_mtu: ok" in res.stdout
