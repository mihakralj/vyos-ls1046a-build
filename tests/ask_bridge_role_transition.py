"""Source regressions for the ownership-safe bridge role transition."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "kernel/ask/oot-modules/ask/ask_bridge.c"
HW = ROOT / "kernel/ask/oot-modules/ask/ask_hw.c"
FIXUP = ROOT / "bin/kernel-fixups/F_291.py"
HARNESS = ROOT / "tests/dut/ask_bridge_ownership.c"


def section(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


def test_prechange_role_transition_veto_is_synchronous():
    source = BRIDGE.read_text()
    prechange = section(
        source,
        "static int ask_bridge_role_prechange(",
        "\nstatic int ask_bridge_netdev_notifier(",
    )
    notifier = section(
        source,
        "static int ask_bridge_netdev_notifier(",
        "\nstatic struct notifier_block ask_bridge_netdev_nb",
    )

    assert prechange.index("ask_bridge_port_quiesce(port)") < prechange.index(
        "ask_hw_offload_role_prepare("
    )
    assert "if (rc)\n\t\t\tgoto record;" in prechange
    assert "if (rc == -ESTALE || rc == -EACCES || rc == -ESHUTDOWN)\n\t\t\tgoto fail;" in prechange
    assert "\t\tgoto record;" in prechange
    # Join failures veto; the kernel ignores an unlink veto, so leave records.
    assert "return info->linking ? notifier_from_errno(rc) : NOTIFY_OK;" in prechange
    assert "state->needs_prepare = true;" in prechange
    assert notifier.index("NETDEV_PRECHANGEUPPER") < notifier.index(
        "NETDEV_CHANGEUPPER"
    )
    assert "ask_bridge_reconcile_port(port);" in notifier


def test_failed_owner_delete_is_retained():
    source = BRIDGE.read_text()
    harness = HARNESS.read_text()
    delete = section(
        source,
        "static int ask_bridge_rec_del(",
        "\nvoid ask_bridge_vlan_pause(",
    )

    assert "if (r->unsettled && !r->dead)\n\t\treturn -EBUSY;" in delete
    assert "if (rc) {\n\t\t\tr->unsettled = true;" in delete
    assert "return rc;" in delete
    assert "module_put(THIS_MODULE);" not in delete.split("if (rc) {", 1)[1].split(
        "return rc;", 1
    )[0]
    assert "return -EBUSY;" in delete
    assert "hw_available = false;" in harness
    assert "l2_available = false;" in harness
    assert "-ETIMEDOUT" in section(harness, "static const int errors[]", ");")


def test_profile_readback_mismatch_never_marks_detached():
    hw = HW.read_text()
    fixup = FIXUP.read_text()
    prepare = section(
        hw,
        "int ask_hw_offload_role_prepare(",
        "\nint ask_hw_offload_role_commit(",
    )
    verify = section(
        fixup,
        'VERIFY_DETACH = """',
        '\n"""\n\nFAULT_ARM',
    )

    assert "return -EIO;" in verify
    assert "fman_port_verify_fe_detached(rxport)" in section(
        fixup, 'DISARM_KG = """', '\n"""\n\nDISARM_FE'
    )
    assert prepare.index("rc = ask_hw_offload_disengage(hw_port_id)") < prepare.index(
        "*detached = true;"
    )
    failure = prepare.split(
        "rc = ask_hw_offload_disengage(hw_port_id)", 1
    )[1].split("*detached = true;", 1)[0]
    assert "if (rc)\n\t\t\treturn rc;" in failure
    disengage = section(
        hw,
        "int ask_hw_offload_disengage(",
        "\nEXPORT_SYMBOL_GPL(ask_hw_offload_disengage);",
    )
    retained = disengage.split("p->offload_engaged = false;", 1)[1]
    assert "clear_bit(hw_port_id, ask_hw_port_l2);" in retained.split(
        "mutex_unlock(&h->lock);", 1
    )[0]


def test_bridge_admission_is_fail_closed_while_not_ready():
    bridge = BRIDGE.read_text()
    hw = HW.read_text()

    member = section(
        bridge, "static bool ask_bridge_member_ok(", "\nstatic bool ask_bridge_user_mac("
    )
    flow = section(bridge, "static bool ask_bridge_flow_ok(", "\nstatic struct ask_bridge_own")
    family = section(hw, "u8 ask_hw_port_family_mask(", "\n\nbool ask_hw_port_is_l2(")
    l2 = section(hw, "bool ask_hw_port_is_l2(", "\n\n/* Cache-only")

    assert "READ_ONCE(ask_bridge_ready)" in member
    assert "READ_ONCE(ask_bridge_ready)" in flow
    assert "READ_ONCE(ask_hw_ready)" in family
    assert "READ_ONCE(ask_hw_ready)" in l2


def test_generated_reconciliation_survives_commit_failure():
    source = BRIDGE.read_text()
    reconcile = section(
        source,
        "static void ask_bridge_reconcile_roles(",
        "\n/*\n * netdevice chain",
    )
    notifier = section(
        source,
        "static int ask_bridge_netdev_notifier(",
        "\nstatic struct notifier_block ask_bridge_netdev_nb",
    )

    assert "state->generation = ++ask_bridge_role_generation;" in section(
        source,
        "static int ask_bridge_role_prechange(",
        "\nstatic int ask_bridge_netdev_notifier(",
    )
    assert "ask_bridge_reconcile_port(port_id);" in reconcile
    assert "ask_bridge_reconcile_port(port);" in notifier


def test_stale_admin_disengage_cannot_republish_saved_family():
    bridge = BRIDGE.read_text()
    hw = HW.read_text()
    prepare = section(
        hw,
        "int ask_hw_offload_role_prepare(",
        "\nint ask_hw_offload_role_commit(",
    )
    commit = section(
        hw,
        "int ask_hw_offload_role_commit(",
        "\nint ask_hw_offload_engage_role(",
    )
    prechange = section(
        bridge,
        "static int ask_bridge_role_prechange(",
        "\nstatic int ask_bridge_netdev_notifier(",
    )
    assert "*policy_seq = ask_hw_role_policy_seq[hw_port_id];" in prepare
    assert (
        "expected_role_policy_seq != ask_hw_role_policy_seq[hw_port_id]" in hw
    )
    assert "family_mask, policy_seq" in commit
    assert "state->policy_seq = policy_seq;" in prechange
    assert "ask_hw_offload_disengage_admin(port_id)" in (
        ROOT / "kernel/ask/oot-modules/ask/ask_genl.c"
    ).read_text()
    debugfs = (ROOT / "kernel/ask/oot-modules/ask/ask_debugfs.c").read_text()
    assert "ask_hw_offload_engage_role((u8)port" in debugfs
    assert "ask_hw_b2_l2_engage" in hw
    engage_role = section(
        hw,
        "int ask_hw_offload_engage_role(",
        "\n/* T-M6-2 B2",
    )
    assert "saved = family_mask ? family_mask" in engage_role
    assert "saved = ASK_FAM_V4 | ASK_FAM_V6;" in engage_role
    assert "ask_hw_role_admin_disabled[hw_port_id] = true;" in hw
    assert "ask_hw_role_admin_disabled[hw_port_id])" in hw
    assert "hw_port_id >= ARRAY_SIZE(ask_hw_role_policy_seq)" in hw
