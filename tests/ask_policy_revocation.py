"""Host contract & fault-injection tests for ASK2 Priority 1 policy revocation."""

from pathlib import Path
import os
import shlex
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
OFFLOAD = ROOT / "kernel/ask/oot-modules/ask/ask_flow_offload.c"
GENL = ROOT / "kernel/ask/oot-modules/ask/ask_genl.c"
ATTR = ROOT / "kernel/ask/oot-modules/ask/ask_genl_attr.c"
UAPI = ROOT / "kernel/ask/oot-modules/ask/include/uapi/linux/ask/ask.h"
YAML = ROOT / "kernel/ask/uapi/ask.yaml"
HARNESS = ROOT / "tests/dut/ask_policy_revocation.c"


def _slice(source, start, end):
    i = source.index(start)
    j = source.index(end, i)
    return source[i:j]


def test_policy_revocation_uapi_and_wiring():
    uapi = UAPI.read_text()
    attr = ATTR.read_text()
    genl = GENL.read_text()
    yaml = YAML.read_text()
    offload = OFFLOAD.read_text()

    for sym in (
        "ASK_INFO_ATTR_POLICY_EPOCH",
        "ASK_INFO_ATTR_REVOKE_STATE",
        "ASK_INFO_ATTR_UNRESOLVED_FLOWS",
        "ASK_REVOKE_IDLE",
        "ASK_REVOKE_FENCED",
        "ASK_REVOKE_RETIRED",
        "ASK_REVOKE_CONTAINED",
        "ASK_REVOKE_UNRESOLVED",
    ):
        assert sym in uapi, sym

    for sym in (
        "ASK_INFO_ATTR_POLICY_EPOCH",
        "ASK_INFO_ATTR_REVOKE_STATE",
        "ASK_INFO_ATTR_UNRESOLVED_FLOWS",
    ):
        assert sym in attr, sym
        assert sym in genl, sym

    for key in ("policy-epoch", "revoke-state", "unresolved-flows"):
        assert key in yaml, key

    # ASK_CMD_FLUSH_FLOWS must drive ask_flow_policy_revoke(), not broken ask_flow_flush().
    flush_fn = _slice(
        genl,
        "static int ask_genl_flush_flows_doit(struct sk_buff *skb,\n     struct genl_info *info)\n{",
        "\n}",
    )
    assert "ask_flow_policy_revoke()" in flush_fn
    assert "ask_flow_flush(" not in flush_fn

    # REPLACE ordering: retained_owner MUST stay before policy snapshot/fence!
    replace = _slice(offload, "static int ask_flow_offload_replace(", "\nstatic int ask_flow_offload_destroy(")
    assert "ask_policy_epoch_snapshot(" in replace
    assert replace.index("ask_flow_retained_owner(") < replace.index("ask_policy_epoch_snapshot(")
    # Post-install policy race must tombstone, release on clean rollback (-EAGAIN), or retain (-EINPROGRESS).
    assert "ask_policy_epoch_is_current(" in replace

    # Pending enqueue must accept explicit policy_epoch, not silently stamp the global epoch.
    enqueue_hdr = _slice(offload, "static int ask_flow_pending_enqueue(", "\n{")
    assert "u64 policy_epoch" in enqueue_hdr

    # G3 async flushers must revoke SW flowtable entries before retiring ASK HW records.
    for fn_name in ("ask_devchg_flush_fn", "ask_fdbchg_flush_fn", "ask_fibchg_flush_fn"):
        body = _slice(offload, f"static void {fn_name}(", "\n}\n")
        assert "nf_flow_table_revoke_match(" in body, fn_name
        assert body.index("nf_flow_table_revoke_match(") < body.index("__ask_flow_flush_matching_status("), fn_name

    # Never falsely transition to ASK_REVOKE_CONTAINED without proven hardware quarantine.
    assert "= ASK_REVOKE_CONTAINED" not in offload


def test_policy_revocation_state_machine(tmp_path):
    source = OFFLOAD.read_text()
    policy_body = _slice(
        source,
        "/* Policy revocation and G3 admission fence state (Priority 1, §3.1.1). */",
        "\n/* End of policy revocation state machine. */",
    )
    republish_body = _slice(
        source,
        "static int ask_neigh_republish(struct ask_flow_table *t, u64 cookie,\n"
        "\t\t\t       const struct ask_flow_key *key, u32 oif,\n"
        "\t\t\t       u32 action_flags, enum ask_hw_dir dir,\n"
        "\t\t\t       u64 generation, u64 policy_epoch)\n{",
        "\n/*\n * ask_flow_neigh_mac_changed",
    )
    sw_match_body = _slice(
        source,
        "static bool ask_devchg_sw_match(const struct flow_offload *flow, void *data)\n{",
        "\nstatic void ask_flow_revoke_neigh_sw(",
    )
    (tmp_path / "ask_policy_state_body.h").write_text(policy_body)
    (tmp_path / "ask_neigh_republish_body.h").write_text(republish_body)
    (tmp_path / "ask_sw_match_body.h").write_text(sw_match_body)

    binary = tmp_path / "ask_policy_revocation"
    subprocess.run(
        [
            *shlex.split(os.environ.get("HOSTCC", "cc")),
            "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-unused-function",
            *shlex.split(os.environ.get("HOSTCFLAGS", "")),
            "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    out = subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=30)
    assert "ask_policy_revocation: OK" in out.stdout
