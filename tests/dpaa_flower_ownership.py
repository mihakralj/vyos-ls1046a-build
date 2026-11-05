"""Host fault-injection tests for F-279 tc-flower ehash ownership."""

from pathlib import Path
import importlib.util
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
FIXUP = ROOT / "bin/kernel-fixups/F_279.py"
HARNESS = ROOT / "tests/dut/dpaa_flower_ownership.c"
DPAA_ETH = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"


def _edits():
    spec = importlib.util.spec_from_file_location("f279", FIXUP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [new for path, _old, new in module.EDITS if path == DPAA_ETH]


def test_failed_flower_delete_keeps_single_owner(tmp_path):
    news = _edits()
    helpers = next(n for n in news if "dpaa_cls_flower_orphan_retry(struct" in n)
    helpers = helpers[helpers.index("/* F-279: an ehash delete is settled"):]
    tail = next(n for n in news if "mutex_lock(&priv->flower_mutex);" in n
                and "dpaa_setup_tc_flower_destroy(" in n)
    struct = (
        "struct dpaa_cls_flower {\n\tstruct list_head node;\n\tu32 cookie_lo;\n"
        "\tu8 key[FMAN_PCD_FE_ROUTED_KEY_SIZE];\n\tu8 key_size;\n};\n"
    )
    wrapper = (
        "static int flower_replace_tail(struct net_device *net_dev,\n"
        "\t\t\t       struct flow_cls_offload *f, u8 *key, u8 key_size)\n{\n"
        "\tstruct dpaa_priv *priv = netdev_priv(net_dev);\n"
        "\tstruct fman_pcd_fe_flow_action action;\n"
        "\tstruct dpaa_cls_flower *fl, *pos, *old;\n"
        "\tstruct fman *fm = &the_fman;\n\tu8 port_id = 0x10;\n\tint err;\n\n"
    )
    (tmp_path / "dpaa_flower_body.h").write_text(struct + helpers + wrapper + tail)
    binary = tmp_path / "dpaa_flower_ownership"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-unused-function",
            "-fsanitize=address,undefined", "-I", str(tmp_path),
            str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True)
