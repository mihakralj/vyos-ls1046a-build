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

    # ask_flow_port_withdraw revokes SW flowtable BEFORE ask_flow_lifecycle_lock
    pw = _between(fo, "int ask_flow_port_withdraw(", "\nbool ask_flow_has_port_orphan(")
    assert "nf_flow_table_revoke_match(" in pw
    assert pw.index("nf_flow_table_revoke_match(") < pw.index("mutex_lock(&ask_flow_lifecycle_lock);")

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

    # A1 decision: default ipv6_hw_frag = false (RFC 8200 safe), opt-in =1
    assert "static bool ask_ipv6_hw_frag = false;" in fo

    # Patch 155 SW revocation before ask_flow_lifecycle_lock in ask_flow_v6_frag_flush_sync
    v6_flush = _between(fo, "static int ask_flow_v6_frag_flush_sync(void)\n{", "\n/* T-M6-SP4:")
    assert "nf_flow_table_revoke_match(" in v6_flush
    assert v6_flush.index("nf_flow_table_revoke_match(") < v6_flush.index("mutex_lock(&ask_flow_lifecycle_lock);")

    # ask_flow_policy_revoke also revokes WGFP flows/rflows/obs
    pol = _between(fo, "int ask_flow_policy_revoke(void)", "\nEXPORT_SYMBOL_GPL(ask_flow_policy_revoke);")
    assert "dpaa_wgfp_policy_revoke()" in pol
