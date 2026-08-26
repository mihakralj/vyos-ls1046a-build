// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - stats subsystem.
 *
 * Per-interface ASK2 hardware-offload bandwidth accounting (Design 2).
 *
 * The FMan FE engine forwards offloaded flows entirely in silicon, so the
 * DPAA netdev software per-CPU counters (dpaa_get_stats64) never see them and
 * /proc/net/dev — and tools such as btop that read it — under-report offloaded
 * throughput. ask.ko already reads each offloaded flow's absolute silicon
 * counters on the nf_flowtable stats poll (FLOW_CLS_STATS, see
 * ask_flow_offload.c) and computes per-poll deltas; those same deltas are
 * attributed here to the flow's ingress (RX) and egress (TX) interface.
 *
 * The DPAA driver folds these offload-only counters into its
 * ndo_get_stats64() through the struct dpaa_flow_offload_ops::offload_stats
 * hook (board patch 0171), so /proc/net/dev reports software + offloaded
 * totals WITHOUT double counting: the software path and the offload path are
 * disjoint by construction, and each field is a strict superset counter.
 *
 * Counter set (rx_packets/rx_bytes/tx_packets/tx_bytes) is keyed by netdev
 * ifindex in an xarray. Entries are created lazily on first attribute, reset
 * (not freed) at FLOW_BLOCK_UNBIND, and freed at module exit. Writers are the
 * nf_flow_offload_stats_wq (WQ_UNBOUND, concurrent across CPUs), so every
 * field is an atomic64.
 */

#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/atomic.h>
#include <linux/xarray.h>
#include <linux/netdevice.h>
#include "include/ask_internal.h"

struct ask_port_stats {
	atomic64_t rx_packets;
	atomic64_t rx_bytes;
	atomic64_t tx_packets;
	atomic64_t tx_bytes;
};

static struct xarray ask_port_stats_xa;

void ask_port_stats_add(int ifindex,
			u64 rx_packets, u64 rx_bytes,
			u64 tx_packets, u64 tx_bytes)
{
	struct ask_port_stats *st, *cur;

	if (ifindex <= 0)
		return;

	st = xa_load(&ask_port_stats_xa, ifindex);
	if (!st) {
		st = kzalloc(sizeof(*st), GFP_KERNEL);
		if (!st)
			return;

		cur = xa_cmpxchg(&ask_port_stats_xa, ifindex, NULL, st,
				 GFP_KERNEL);
		if (xa_is_err(cur)) {
			kfree(st);
			return;
		}
		if (cur) {	/* lost a concurrent create: use theirs */
			kfree(st);
			st = cur;
		}
	}

	atomic64_add(rx_packets, &st->rx_packets);
	atomic64_add(rx_bytes, &st->rx_bytes);
	atomic64_add(tx_packets, &st->tx_packets);
	atomic64_add(tx_bytes, &st->tx_bytes);
}

void ask_port_stats_zero(int ifindex)
{
	struct ask_port_stats *st;

	if (ifindex <= 0)
		return;

	st = xa_load(&ask_port_stats_xa, ifindex);
	if (!st)
		return;

	atomic64_set(&st->rx_packets, 0);
	atomic64_set(&st->rx_bytes, 0);
	atomic64_set(&st->tx_packets, 0);
	atomic64_set(&st->tx_bytes, 0);
}

void ask_port_stats_get(int ifindex, struct rtnl_link_stats64 *hw)
{
	struct ask_port_stats *st;

	if (ifindex <= 0 || !hw)
		return;

	st = xa_load(&ask_port_stats_xa, ifindex);
	if (!st)
		return;

	/* Add (never overwrite): the caller (dpaa_get_stats64) has already
	 * summed the software per-CPU counters into *hw. */
	hw->rx_packets += atomic64_read(&st->rx_packets);
	hw->rx_bytes   += atomic64_read(&st->rx_bytes);
	hw->tx_packets += atomic64_read(&st->tx_packets);
	hw->tx_bytes   += atomic64_read(&st->tx_bytes);
}

int ask_stats_init(void)
{
	xa_init(&ask_port_stats_xa);
	ask_pr_dbg("stats: per-port offload counters initialised\n");
	return 0;
}

void ask_stats_exit(void)
{
	struct ask_port_stats *st;
	unsigned long idx;

	/* Safe: ask_flow_offload_exit() unregisters the DPAA offload_stats
	 * hook (synchronize_rcu) before we are reached, so no reader can be
	 * inside ask_port_stats_get(). */
	xa_for_each(&ask_port_stats_xa, idx, st) {
		xa_erase(&ask_port_stats_xa, idx);
		kfree(st);
	}
	xa_destroy(&ask_port_stats_xa);
	ask_pr_dbg("stats: exit\n");
}