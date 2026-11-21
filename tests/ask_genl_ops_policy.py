"""Host contract tests for ASK2 per-op generic-netlink policies (ask_genl.c)."""

from pathlib import Path
import re

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
GENL = ROOT / "kernel/ask/oot-modules/ask/ask_genl.c"
ATTR = ROOT / "kernel/ask/oot-modules/ask/ask_genl_attr.c"


def test_genl_family_uses_per_op_policies():
    genl = GENL.read_text()
    attr = ATTR.read_text()

    assert "static const struct genl_ops ask_genl_ops[]" in genl
    assert "genl_small_ops" not in genl
    assert "GENL_DONT_VALIDATE_STRICT" not in genl
    assert "GENL_DONT_VALIDATE_DUMP" not in genl

    fam_start = genl.index("static struct genl_family ask_genl_family")
    fam = genl[fam_start:genl.index("\n};", fam_start) + 3]
    assert ".ops" in fam and "ask_genl_ops" in fam
    assert ".n_ops" in fam
    assert ".small_ops" not in fam
    assert ".policy" not in fam
    assert ".maxattr" not in fam
    assert ".netnsok" in fam and "false" in fam

    ops_start = genl.index("static const struct genl_ops ask_genl_ops[]")
    ops = genl[ops_start:genl.index("\n};", ops_start) + 3]
    expected_policies = {
        "ASK_CMD_GET_INFO": "ask_no_attr_policy",
        "ASK_CMD_GET_MURAM": "ask_no_attr_policy",
        "ASK_CMD_DUMP_FLOWS": "ask_no_attr_policy",
        "ASK_CMD_GET_FLOW": "ask_get_flow_op_policy",
        "ASK_CMD_DUMP_SAS": "ask_no_attr_policy",
        "ASK_CMD_FLUSH_FLOWS": "ask_no_attr_policy",
        "ASK_CMD_FLUSH_SAS": "ask_no_attr_policy",
        "ASK_CMD_SET_POLICER": "ask_set_policer_op_policy",
        "ASK_CMD_ENGAGE": "ask_engage_op_policy",
        "ASK_CMD_DISENGAGE": "ask_disengage_op_policy",
    }
    blocks = re.findall(r"\{[^{}]+\}", ops)
    assert len(blocks) == len(expected_policies)
    seen = {}
    for block in blocks:
        cmd = re.search(r"\.cmd\s*=\s*(\w+)", block).group(1)
        pol = re.search(r"\.policy\s*=\s*(\w+)", block).group(1)
        assert re.search(r"\.maxattr\s*=\s*\S+", block), cmd
        assert "GENL_ADMIN_PERM" in block, cmd
        seen[cmd] = pol
    assert seen == expected_policies

    for sym in (
        "ask_no_attr_policy",
        "ask_get_flow_op_policy",
        "ask_set_policer_op_policy",
        "ask_engage_op_policy",
        "ask_disengage_op_policy",
    ):
        assert f"const struct nla_policy {sym}[" in attr

    # GET_FLOW policy must validate flat ASK_FLOW_ATTR_ID as NLA_U64 (no INFO nest collision).
    gf_start = attr.index("const struct nla_policy ask_get_flow_op_policy[")
    gf = attr[gf_start:attr.index("\n};", gf_start) + 3]
    assert "[ASK_FLOW_ATTR_ID]" in gf and "NLA_U64" in gf
    assert "[ASK_ATTR_FLOW]" in gf and "NLA_POLICY_NESTED(ask_flow_policy)" in gf

    # SET_POLICER doit accepts both flat ask.yaml attrs and legacy nested ASK_ATTR_POLICER.
    sp_start = genl.index(
        "static int ask_genl_set_policer_doit(struct sk_buff *skb, struct genl_info *info)\n{"
    )
    sp = genl[sp_start:genl.index("\n}\n", sp_start) + 3]
    assert "info->attrs[ASK_ATTR_POLICER]" in sp
    assert "attrs = info->attrs;" in sp
    assert "attrs[ASK_POLICER_ATTR_PORT_ID]" in sp

    # GET_FLOW rejects ambiguous hybrid requests carrying both flat id and nested flow.
    gc_start = genl.index(
        "int ask_genl_get_flow_cookie(const struct nlmsghdr *nlh, struct nlattr **attrs,\n"
        "\t\t\t     u64 *cookie, struct netlink_ext_ack *extack)\n{"
    )
    gc = genl[gc_start:genl.index("\n}\n", gc_start) + 3]
    assert "attrs[ASK_FLOW_ATTR_ID] && attrs[ASK_ATTR_FLOW]" in gc
