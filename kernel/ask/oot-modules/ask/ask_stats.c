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
 * nf_flow_offload_stats_wq (WQ_UNBOUND, concurrent across CPUs), so the
 * counters are per-CPU: the A72 has no LSE atomics, and shared atomic64
 * counters cost 3 % of CPU in LL/SC retries at 65536 flows.
 */

#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/percpu.h>
#include <linux/xarray.h>
#include <linux/netdevice.h>
#include "include/ask_internal.h"

struct ask_port_ctr {
	u64 rx_packets;
	u64 rx_bytes;
	u64 tx_packets;
	u64 tx_bytes;
};

/* Zeroing records the current sum as a baseline instead of writing the
 * per-CPU slots, which a remote CPU's in-flight add could overwrite. */
struct ask_port_stats {
	struct ask_port_ctr __percpu *pcpu;
	struct ask_port_ctr base;
};

static struct xarray ask_port_stats_xa;

static void ask_port_stats_sum(struct ask_port_stats *st, struct ask_port_ctr *sum)
{
	int cpu;

	memset(sum, 0, sizeof(*sum));
	for_each_possible_cpu(cpu) {
		const struct ask_port_ctr *c = per_cpu_ptr(st->pcpu, cpu);

		sum->rx_packets += READ_ONCE(c->rx_packets);
		sum->rx_bytes   += READ_ONCE(c->rx_bytes);
		sum->tx_packets += READ_ONCE(c->tx_packets);
		sum->tx_bytes   += READ_ONCE(c->tx_bytes);
	}
}

static void ask_port_stats_free(struct ask_port_stats *st)
{
	free_percpu(st->pcpu);
	kfree(st);
}

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
		st->pcpu = alloc_percpu(struct ask_port_ctr);
		if (!st->pcpu) {
			kfree(st);
			return;
		}

		cur = xa_cmpxchg(&ask_port_stats_xa, ifindex, NULL, st,
				 GFP_KERNEL);
		if (xa_is_err(cur)) {
			ask_port_stats_free(st);
			return;
		}
		if (cur) {	/* lost a concurrent create: use theirs */
			ask_port_stats_free(st);
			st = cur;
		}
	}

	this_cpu_add(st->pcpu->rx_packets, rx_packets);
	this_cpu_add(st->pcpu->rx_bytes, rx_bytes);
	this_cpu_add(st->pcpu->tx_packets, tx_packets);
	this_cpu_add(st->pcpu->tx_bytes, tx_bytes);
}

void ask_port_stats_zero(int ifindex)
{
	struct ask_port_stats *st;
	struct ask_port_ctr sum;

	if (ifindex <= 0)
		return;

	st = xa_load(&ask_port_stats_xa, ifindex);
	if (!st)
		return;

	ask_port_stats_sum(st, &sum);
	WRITE_ONCE(st->base.rx_packets, sum.rx_packets);
	WRITE_ONCE(st->base.rx_bytes, sum.rx_bytes);
	WRITE_ONCE(st->base.tx_packets, sum.tx_packets);
	WRITE_ONCE(st->base.tx_bytes, sum.tx_bytes);
}

/* A reader racing a zero can see the new baseline before the sum catches up. */
static u64 ask_port_ctr_delta(u64 sum, u64 base)
{
	return sum > base ? sum - base : 0;
}

void ask_port_stats_get(int ifindex, struct rtnl_link_stats64 *hw)
{
	struct ask_port_stats *st;
	struct ask_port_ctr sum;

	if (ifindex <= 0 || !hw)
		return;

	st = xa_load(&ask_port_stats_xa, ifindex);
	if (!st)
		return;

	/* Add (never overwrite): the caller (dpaa_get_stats64) has already
	 * summed the software per-CPU counters into *hw. */
	ask_port_stats_sum(st, &sum);
	hw->rx_packets += ask_port_ctr_delta(sum.rx_packets, READ_ONCE(st->base.rx_packets));
	hw->rx_bytes   += ask_port_ctr_delta(sum.rx_bytes, READ_ONCE(st->base.rx_bytes));
	hw->tx_packets += ask_port_ctr_delta(sum.tx_packets, READ_ONCE(st->base.tx_packets));
	hw->tx_bytes   += ask_port_ctr_delta(sum.tx_bytes, READ_ONCE(st->base.tx_bytes));
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
		ask_port_stats_free(st);
	}
	xa_destroy(&ask_port_stats_xa);
	ask_pr_dbg("stats: exit\n");
}