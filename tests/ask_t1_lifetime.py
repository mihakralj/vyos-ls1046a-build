"""Host regression tests for Priority 2 (T1b/T1f) and Priority 3 (P3.4/WGFP revoke) in ask.ko."""

from pathlib import Path
import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
ASK_H = ROOT / "kernel/ask/oot-modules/ask/include/ask_internal.h"
ASK_HW = ROOT / "kernel/ask/oot-modules/ask/ask_hw.c"
ASK_BR = ROOT / "kernel/ask/oot-modules/ask/ask_bridge.c"
ASK_FO = ROOT / "kernel/ask/oot-modules/ask/ask_flow_offload.c"


def _between(src: str, start: str, end: str) -> str:
    i = src.index(start)
    return src[i:src.index(end, i)]


def test_t1b_noconf_tx_netdev_lifetime_and_unregister():
    hw = ASK_HW.read_text()
    br = ASK_BR.read_text()
    fo = ASK_FO.read_text()

    # noconf_tx tracks netdev_hold + legacy_users + l2_users + unregistered + retained
    pcd_struct = _between(hw, "struct ask_hw_pcd {", "\nstatic struct ask_hw_pcd *ask_hw_pcd_inst;")
    for field in (
        "struct net_device *dev;",
        "netdevice_tracker dev_tracker;",
        "u32     legacy_users;",
        "u32     l2_users;",
        "bool    unregistered;",
        "bool    retained;",
    ):
        assert field in pcd_struct, field

    # NETDEV_UNREGISTER notifier with 1-Hz rebroadcast fast-path and clean teardown
    assert "NETDEV_UNREGISTER" in hw
    assert "ask_hw_netdev_notifier" in hw
    unreg = _between(hw, "static int ask_hw_netdev_event(", "\nstatic struct notifier_block ask_hw_netdev_nb")
    assert "noconf_tx[slot_idx].retained" in unreg
    assert "ask_bridge_dev_quiesce(" in unreg
    assert "ask_flow_port_withdraw(" in unreg
    assert "dpaa_wgfp_port_quiesce(" in unreg
    assert "netdev_put(" in unreg
    assert "try_module_get(THIS_MODULE)" in unreg
    # Never acquire ask_hw_prewarm_lock under rtnl in NETDEV_UNREGISTER!
    assert "ask_hw_prewarm_lock" not in unreg

    # ask_hw_pcd_teardown checks retained/users and releases netdev_put
    td = _between(hw, "void ask_hw_pcd_teardown(void)", "\nstruct ask_hw_pcd *ask_hw_pcd_get(")
    assert "noconf_tx" in td
    assert "netdev_put(" in td

    # ask_bridge_rec_del passes r->fqid (not 0) to ask_hw_l2_record and releases l2_users on r->dead
    rec_del = _between(br, "static int ask_bridge_rec_del(", "\nvoid ask_bridge_vlan_pause(")
    assert "ask_hw_l2_record(r->port, r->key, r->fqid," in rec_del
    assert "ask_hw_l2_fq_put(" in rec_del

    # ask_fe_flow_insert re-validates egress tx_fqid under guard->lock before publishing
    fe_start = fo.index("static int ask_fe_flow_insert(", fo.index("/* F-109: Direct kernel API"))
    fe_ins = fo[fe_start:fo.index("\n/*\n * Fix B: remove", fe_start)]
    assert "ask_hw_tx_fqid_live_locked(guard," in fe_ins
    assert fe_ins.index("ask_hw_tx_fqid_live_locked(guard,") < fe_ins.index("ask_fe_flow_publish(")

    # ask_flow_block_priv holds netdev_hold / netdev_put outside spinlock
    bp = _between(fo, "struct ask_flow_block_priv {", "\nstatic struct net_device *\nask_flow_block_priv_dev(")
    assert "netdevice_tracker dev_tracker;" in bp
    assert "netdev_hold(dev, &e->priv.dev_tracker" in bp
    assert "netdev_put(" in bp


def test_t1f_synchronous_port_quiesce_before_disengage():
    hw = ASK_HW.read_text()
    fo = ASK_FO.read_text()
    br = ASK_BR.read_text()

    assert "int ask_bridge_port_quiesce(u8 hw_port_id)" in br
    assert "int ask_bridge_dev_quiesce(u8 hw_port_id, int ifindex)" in br
    assert "int ask_flow_port_withdraw(u8 hw_port_id, int ifindex)" in fo

    # ask_flow_port_withdraw fences SW flowtable admission + synchronize_rcu()
    # across revoke_match and ask_flow_lifecycle_lock flush so live traffic
    # cannot re-admit flows inside patch 155's settle loop (-EBUSY).
    pw = _between(fo, "int ask_flow_port_withdraw(", "\nbool ask_flow_has_port_orphan(")
    for token in (
        "nf_flow_table_fence_begin();",
        "synchronize_rcu();",
        "nf_flow_table_revoke_match(",
        "mutex_lock(&ask_flow_lifecycle_lock);",
        "mutex_unlock(&ask_flow_lifecycle_lock);",
        "nf_flow_table_fence_end();",
    ):
        assert token in pw, token
    assert pw.index("nf_flow_table_fence_begin();") < pw.index("synchronize_rcu();")
    assert pw.index("synchronize_rcu();") < pw.index("nf_flow_table_revoke_match(")
    assert pw.index("nf_flow_table_revoke_match(") < pw.index("mutex_lock(&ask_flow_lifecycle_lock);")
    assert pw.index("mutex_unlock(&ask_flow_lifecycle_lock);") < pw.index("nf_flow_table_fence_end();")

    # ask_hw_offload_disengage_admin and ask_hw_offload_role_prepare withdraw before disengage
    admin = _between(hw, "int ask_hw_offload_disengage_admin(", "\nEXPORT_SYMBOL_GPL(ask_hw_offload_disengage_admin);")
    assert "ask_hw_offload_quiesce_port(" in admin

    prep = _between(hw, "int ask_hw_offload_role_prepare(", "\nint ask_hw_offload_role_commit(")
    assert "ask_hw_offload_quiesce_port_owners(" in prep
    assert prep.index("ask_hw_offload_quiesce_port_owners(") < prep.index("rc = ask_hw_offload_disengage(hw_port_id)")


def test_p3_ipv6_frag_default_killswitch_mutex_and_wgfp_policy_revoke():
    hdr = ASK_H.read_text()
    hw = ASK_HW.read_text()
    fo = ASK_FO.read_text()

    assert "extern struct mutex ask_killswitch_mutex;" in hdr
    assert "DEFINE_MUTEX(ask_killswitch_mutex);" in hw

    n44 = _between(hw, "static int ask_nat44_offload_set(", "\nstatic const struct kernel_param_ops ask_nat44_offload_ops")
    n66 = _between(hw, "static int ask_nat66_offload_set(", "\nstatic const struct kernel_param_ops ask_nat66_offload_ops")
    v6f = _between(fo, "static int ask_ipv6_hw_frag_set(", "\nstatic const struct kernel_param_ops ask_ipv6_hw_frag_ops")
    for fn in (n44, n66, v6f):
        assert "mutex_lock(&ask_killswitch_mutex);" in fn
        assert "mutex_unlock(&ask_killswitch_mutex);" in fn

    # Vendor NXP ASK parity: default ipv6_hw_frag = true (opt-out =0 for RFC 8200)
    assert "static bool ask_ipv6_hw_frag = true;" in fo

    # Patch 155 SW revocation fenced with nf_flow_table_fence_begin/end after
    # the early ask_ipv6_hw_frag check and across ask_flow_lifecycle_lock.
    v6_flush = _between(fo, "static int ask_flow_v6_frag_flush_sync(void)\n{", "\n/* T-M6-SP4:")
    for token in (
        "if (READ_ONCE(ask_ipv6_hw_frag))",
        "nf_flow_table_fence_begin();",
        "synchronize_rcu();",
        "nf_flow_table_revoke_match(",
        "mutex_lock(&ask_flow_lifecycle_lock);",
        "mutex_unlock(&ask_flow_lifecycle_lock);",
        "nf_flow_table_fence_end();",
    ):
        assert token in v6_flush, token
    assert v6_flush.index("return -EINVAL;") < v6_flush.index("nf_flow_table_fence_begin();")
    assert v6_flush.index("nf_flow_table_fence_begin();") < v6_flush.index("synchronize_rcu();")
    assert v6_flush.index("synchronize_rcu();") < v6_flush.index("nf_flow_table_revoke_match(")
    assert v6_flush.index("nf_flow_table_revoke_match(") < v6_flush.index("mutex_lock(&ask_flow_lifecycle_lock);")
    assert v6_flush.index("mutex_unlock(&ask_flow_lifecycle_lock);") < v6_flush.index("nf_flow_table_fence_end();")

    # ask_flow_policy_revoke also revokes WGFP flows/rflows/obs
    pol = _between(fo, "int ask_flow_policy_revoke(void)", "\nEXPORT_SYMBOL_GPL(ask_flow_policy_revoke);")
    assert "dpaa_wgfp_policy_revoke()" in pol


def test_bridge_runner_per_phase_pcd_snapshot_and_muram_check():
    br_sh = (ROOT / "bin/testrig-ask2-bridge.sh").read_text()

    # Token-scoped snapshot path and separate tool-availability flag
    assert 'SNAP_FILE="/tmp/ask2-bridge-${TOKEN}.json"' in br_sh
    assert "HAVE_PCD_TOOL=" in br_sh
    assert "BASE_MURAM=" in br_sh

    # In cleanup(): active phase pcd-snapshot diff MUST run BEFORE testrig-ask2-9k.sh down
    # (otherwise 9k down re-engages 0x10/0x11 and can swap fmbm_rccb offsets).
    cln = _between(br_sh, "cleanup(){", "\ntrap cleanup EXIT")
    assert 'if [ "$HAVE_PCD_SNAP" = 1 ]; then' in cln
    assert 'if [ "$JUMBO_UP" = 1 ]; then' in cln
    assert cln.index('if [ "$HAVE_PCD_SNAP" = 1 ]; then') < cln.index('if [ "$JUMBO_UP" = 1 ]; then')
    assert 'rm -f -- \\"$SNAP_FILE\\"' in cln or 'rm -f -- "$SNAP_FILE"' in cln
    assert "BASE_MURAM" in cln
    assert '[ "$PCD_SNAP_FAILED" = 0 ] || clean_rc=1' in cln

    # In main MTU loop: snapshot is captured per phase AFTER 9k up and diffed BEFORE 9k down
    loop = br_sh[br_sh.index('for m in "${MTU_LIST[@]}"; do'):]
    assert loop.index('"$HERE/testrig-ask2-9k.sh" up') < loop.index("pcd-snapshot capture")
    assert loop.index("pcd-snapshot diff") < loop.index('"$HERE/testrig-ask2-9k.sh" down')
    assert "PCD_SNAP_FAILED=1" in loop
