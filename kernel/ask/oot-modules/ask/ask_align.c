// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 adaptive RX alignment (2026-11-08).
 *
 * FMan falls behind -- mEMAC RX FIFO overflow on every port at once -- when
 * offloaded frames leave at a start address that is not 8-byte aligned. RX
 * data lands at 256, so a length-neutral edit (routed, NAT, VLAN translate,
 * PPPoE +-8 B) stays aligned while a single VLAN POP or PUSH (+-4 B) does not:
 * vlan-port bidir ran 13.1 Gbit/s with ~355k rdrp per port against ~17 for
 * the others (.185, image 2026.11.07-2039). With the RX start margin at 260,
 * POP lands at 264 and PUSH at 256 and the drops go to zero -- and the
 * length-neutral classes collapse instead. One margin per RX port cannot
 * serve both, so each port follows its offloaded byte mix:
 *
 *   class A (edit length change == 0 mod 8) -> margin 256
 *   class B (edit length change == 4 mod 8) -> margin 260
 *
 * Bytes are the per-poll hardware deltas ask_fe_flow_refresh_hw_stats()
 * already computes, attributed to the flow's ingress BMI port. Every
 * ASK_ALIGN_PERIOD the worker switches a port when it carried at least
 * ASK_ALIGN_MIN_BYTES (misalignment only costs near line rate, and 1G ports
 * such as eth0 never reach it) and the other class outweighed the current
 * one ASK_ALIGN_RATIO times, for ASK_ALIGN_STREAK windows in a row. A switch
 * quiesces the RX port for microseconds (F-308 fman_port_set_rx_start_margin).
 * align_adaptive=N restores 256 everywhere; module exit does the same.
 */
#include <linux/atomic.h>
#include <linux/moduleparam.h>
#include <linux/netdevice.h>
#include <linux/rtnetlink.h>
#include <linux/workqueue.h>

#include "include/ask_internal.h"

struct fman_port;
struct fman_port *dpaa_get_rx_fman_port(struct net_device *dev);
u8 fman_port_get_id(struct fman_port *port);
int fman_port_set_rx_start_margin(struct fman_port *port, u16 margin);	/* F-308 */

#define ASK_ALIGN_PORTS		64	/* BMI hard port ids */
#define ASK_ALIGN_PERIOD	(5 * HZ)
#define ASK_ALIGN_MIN_BYTES	(1000ULL * 1000 * 1000)	/* per period: ~1.6 Gbit/s */
#define ASK_ALIGN_RATIO		2
#define ASK_ALIGN_STREAK	2

static atomic64_t ask_align_bytes[ASK_ALIGN_PORTS][2];
static u16 ask_align_margin[ASK_ALIGN_PORTS];	/* 0 = never changed (256) */
static u8 ask_align_streak[ASK_ALIGN_PORTS];
static bool ask_align_on = true;
static bool ask_align_running;
static void ask_align_work_fn(struct work_struct *w);
static DECLARE_DELAYED_WORK(ask_align_work, ask_align_work_fn);

/* Edit class of a flow: 1 when its length change is 4 mod 8. */
static unsigned int ask_align_class(u8 vlan_edit_flags)
{
	int delta = 0;

	if (vlan_edit_flags & ASK_VLANF_POP)
		delta += 4;
	if (vlan_edit_flags & ASK_VLANF_PUSH)
		delta -= 4;
	if (vlan_edit_flags & ASK_VLANF_PPPOE_STRIP)
		delta += 8;
	if (vlan_edit_flags & ASK_VLANF_PPPOE_INSERT)
		delta -= 8;
	return (delta & 7) == 4;
}

/*
 * Pure decision for one port and one window; kept free of kernel state so the
 * host harness (bin/test-ask-align.py) can drive it. Returns the margin to
 * program (equal to @cur when nothing changes) and updates *@streak.
 */
static u16 ask_align_decide(u64 a, u64 b, u16 cur, u8 *streak)
{
	u16 want = cur;

	if (a + b >= ASK_ALIGN_MIN_BYTES) {
		if (cur == ASK_ALIGN_MARGIN_A && b > ASK_ALIGN_RATIO * a)
			want = ASK_ALIGN_MARGIN_B;
		else if (cur == ASK_ALIGN_MARGIN_B && a > ASK_ALIGN_RATIO * b)
			want = ASK_ALIGN_MARGIN_A;
	}
	if (want == cur) {
		*streak = 0;
		return cur;
	}
	if (++*streak < ASK_ALIGN_STREAK)
		return cur;
	*streak = 0;
	return want;
}

void ask_align_account(u8 port_id, u8 vlan_edit_flags, u64 bytes)
{
	if (!bytes || port_id >= ASK_ALIGN_PORTS || !READ_ONCE(ask_align_on))
		return;
	atomic64_add(bytes, &ask_align_bytes[port_id][ask_align_class(vlan_edit_flags)]);
}

/* RTNL held. Programs @margin on the RX port with BMI id @port_id. */
static int ask_align_apply(u8 port_id, u16 margin)
{
	struct net_device *dev;

	for_each_netdev(&init_net, dev) {
		struct fman_port *port = dpaa_get_rx_fman_port(dev);

		if (port && fman_port_get_id(port) == port_id)
			return fman_port_set_rx_start_margin(port, margin);
	}
	return -ENODEV;
}

static void ask_align_set(u8 port_id, u16 margin, u64 a, u64 b)
{
	u16 cur = ask_align_margin[port_id] ?: ASK_ALIGN_MARGIN_A;
	int err;

	err = ask_align_apply(port_id, margin);
	if (err) {
		ask_pr_warn("align: port 0x%02x margin %u -> %u failed: %d\n",
			    port_id, cur, margin, err);
		return;
	}
	ask_align_margin[port_id] = margin;
	ask_pr_info("align: port 0x%02x RX margin %u -> %u (window bytes: neutral %llu, +-4 %llu)\n",
		    port_id, cur, margin, a, b);
}

static void ask_align_restore_all(void)
{
	unsigned int p;

	rtnl_lock();
	for (p = 0; p < ASK_ALIGN_PORTS; p++) {
		if (ask_align_margin[p] && ask_align_margin[p] != ASK_ALIGN_MARGIN_A)
			ask_align_set(p, ASK_ALIGN_MARGIN_A, 0, 0);
		ask_align_streak[p] = 0;
		atomic64_set(&ask_align_bytes[p][0], 0);
		atomic64_set(&ask_align_bytes[p][1], 0);
	}
	rtnl_unlock();
}

static void ask_align_work_fn(struct work_struct *w)
{
	unsigned int p;

	if (!READ_ONCE(ask_align_on)) {
		ask_align_restore_all();
		return;		/* re-armed by the param setter */
	}
	rtnl_lock();
	for (p = 0; p < ASK_ALIGN_PORTS; p++) {
		u64 a = atomic64_xchg(&ask_align_bytes[p][0], 0);
		u64 b = atomic64_xchg(&ask_align_bytes[p][1], 0);
		u16 cur = ask_align_margin[p] ?: ASK_ALIGN_MARGIN_A;
		u16 want = ask_align_decide(a, b, cur, &ask_align_streak[p]);

		if (want != cur)
			ask_align_set(p, want, a, b);
	}
	rtnl_unlock();
	if (READ_ONCE(ask_align_running))
		schedule_delayed_work(&ask_align_work, ASK_ALIGN_PERIOD);
}

static int ask_align_on_set(const char *val, const struct kernel_param *kp)
{
	int err = param_set_bool(val, kp);

	if (!err && READ_ONCE(ask_align_running))
		mod_delayed_work(system_wq, &ask_align_work, 0);
	return err;
}

static const struct kernel_param_ops ask_align_on_ops = {
	.set = ask_align_on_set,
	.get = param_get_bool,
};
module_param_cb(align_adaptive, &ask_align_on_ops, &ask_align_on, 0644);
MODULE_PARM_DESC(align_adaptive,
		 "Adapt each RX port's buffer start margin (256/260) to its offloaded edit mix so +-4 B VLAN POP/PUSH frames stay 8-byte aligned (default Y; N restores 256)");

static int ask_align_ports_get(char *buf, const struct kernel_param *kp)
{
	int n = 0;
	unsigned int p;

	for (p = 0; p < ASK_ALIGN_PORTS; p++)
		if (ask_align_margin[p])
			n += sysfs_emit_at(buf, n, "0x%02x=%u ", p, ask_align_margin[p]);
	return n + sysfs_emit_at(buf, n, "\n");
}

/* Read-only, but parse_args() calls ->set for "ask.align_ports=" on a command line. */
static int ask_align_ports_set(const char *val, const struct kernel_param *kp)
{
	return -EPERM;
}

static const struct kernel_param_ops ask_align_ports_ops = {
	.set = ask_align_ports_set,
	.get = ask_align_ports_get,
};
module_param_cb(align_ports, &ask_align_ports_ops, NULL, 0444);
MODULE_PARM_DESC(align_ports, "RX ports whose margin the adaptive alignment has set (port=margin)");

void ask_align_start(void)
{
	WRITE_ONCE(ask_align_running, true);
	schedule_delayed_work(&ask_align_work, ASK_ALIGN_PERIOD);
}

void ask_align_stop(void)
{
	WRITE_ONCE(ask_align_running, false);
	cancel_delayed_work_sync(&ask_align_work);
	ask_align_restore_all();
}
