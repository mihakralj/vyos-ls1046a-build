/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * NXP DPAA1 (fsl_dpa) flow-offload registration API.
 *
 * The dpaa Ethernet driver does not itself implement tc-flower /
 * nf_flow_table HW offload. Instead it exposes a single registration
 * slot that an external offload backend can occupy at runtime.
 *
 * On NXP LS1046A, the backend is the ASK2 module (ask.ko) which
 * drives the FMan 210-series microcode running on an offline parser.
 * See specs/ask2-rewrite-spec.md for the broader design.
 *
 * The split exists so the dpaa driver itself does not depend on
 * ask.ko (which would create a circular module dependency: ask.ko
 * already depends on FSL_DPAA via Kconfig).
 *
 * Single-slot, single-consumer:
 *   - dpaa_register_flow_offload_handler() returns -EBUSY if a handler
 *     is already registered.
 *   - dpaa_unregister_flow_offload_handler() must pass the same ops
 *     pointer that was registered, or -EINVAL is returned.
 *
 * Copyright 2026 Mono Networks / VyOS LS1046A maintainers.
 */

#ifndef _LINUX_FSL_DPAA_FLOW_OFFLOAD_H
#define _LINUX_FSL_DPAA_FLOW_OFFLOAD_H

#include <linux/types.h>

struct module;
struct net_device;
struct flow_block_offload;
struct rtnl_link_stats64;

/**
 * struct dpaa_flow_offload_ops - external flow-offload backend hooks
 * @owner:	module owning this ops struct (set with THIS_MODULE).
 * @setup_tc_block: invoked from dpaa_setup_tc() for TC_SETUP_BLOCK.
 * 			Must perform the standard FLOW_BLOCK_BIND /
 * 			FLOW_BLOCK_UNBIND dance against fbo. Returns 0 on
 * 			success or a negative errno; -EOPNOTSUPP is treated
 * 			by the upper layer as 'try software fallback'.
 * @offload_stats: invoked from dpaa_get_stats64() after the software
 * 			per-CPU counters have been summed, so the backend
 * 			can fold in hardware-offloaded packets/bytes that
 * 			bypassed the kernel datapath (ASK2). The counters
 * 			must be DISJOINT from the software path to avoid
 * 			double counting; the backend adds into *hw, it does
 * 			not overwrite. Runs under rcu_read_lock() from a
 * 			non-sleeping context, so it must not sleep.
 */
struct dpaa_flow_offload_ops {
	struct module *owner;
	int (*setup_tc_block)(struct net_device *dev,
			      struct flow_block_offload *fbo);
	void (*offload_stats)(struct net_device *dev,
			      struct rtnl_link_stats64 *hw);
};

#if IS_ENABLED(CONFIG_FSL_DPAA)
int dpaa_register_flow_offload_handler(const struct dpaa_flow_offload_ops *ops);
int dpaa_unregister_flow_offload_handler(const struct dpaa_flow_offload_ops *ops);
#else
static inline int
dpaa_register_flow_offload_handler(const struct dpaa_flow_offload_ops *ops)
{
	return -ENODEV;
}
static inline int
dpaa_unregister_flow_offload_handler(const struct dpaa_flow_offload_ops *ops)
{
	return -ENODEV;
}
#endif

#endif /* _LINUX_FSL_DPAA_FLOW_OFFLOAD_H */
