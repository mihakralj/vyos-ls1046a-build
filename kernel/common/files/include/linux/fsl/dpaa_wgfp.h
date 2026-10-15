/* SPDX-License-Identifier: GPL-2.0 */
/*
 * WireGuard fast path hook between the DPAA driver and the wireguard module
 * (plans/ASK2-WIREGUARD-FASTPATH-PLAN.md).
 *
 * dpaa_wgfp.c (built into fsl_dpa) owns QMan frame queues that FMan flow
 * records steer frames to, and drains them without building an sk_buff.  A
 * registered consumer (the wireguard module, which holds the keys and counters)
 * gets each frame as a raw buffer, transforms it in place and hands the same
 * buffer back for transmission, or declines it.
 */
#ifndef _LINUX_FSL_DPAA_WGFP_H
#define _LINUX_FSL_DPAA_WGFP_H

#include <linux/types.h>
#include <linux/dma-mapping.h>

struct net_device;

struct dpaa_wgfp_frame {
	u8 *buf;		/* virtual address of the buffer start */
	dma_addr_t addr;	/* bus address of the buffer start */
	struct device *dev;	/* DMA device of the buffer pool */
	u32 off;		/* offset of the frame data in the buffer */
	u32 len;		/* frame length */
	u32 buf_size;		/* usable buffer size from the buffer start */
	void *fd;		/* the dequeued struct qm_fd (opaque to users) */
};

struct dpaa_wgfp_ops {
	/* Called in NAPI context, buffer already synced for the CPU.
	 * Return 1 when the consumer took the buffer (it must have called
	 * dpaa_wgfp_xmit() or dpaa_wgfp_drop()), 0 to decline.
	 */
	int (*frame)(void *priv, struct dpaa_wgfp_frame *f);
};

#if IS_ENABLED(CONFIG_FSL_DPAA_ETH)
int dpaa_wgfp_register(const struct dpaa_wgfp_ops *ops, void *priv);
void dpaa_wgfp_unregister(void);
/* Select the egress port; resolves its no-confirm TX frame queue. */
int dpaa_wgfp_set_egress(struct net_device *dev);
/* Transmit the (modified) buffer: new data offset and length in the buffer. */
void dpaa_wgfp_xmit(struct dpaa_wgfp_frame *f, u32 new_off, u32 new_len);
/* Return the buffer to its pool. */
void dpaa_wgfp_drop(struct dpaa_wgfp_frame *f);
#else
static inline int dpaa_wgfp_register(const struct dpaa_wgfp_ops *ops, void *priv)
{ return -ENODEV; }
static inline void dpaa_wgfp_unregister(void) { }
static inline int dpaa_wgfp_set_egress(struct net_device *dev) { return -ENODEV; }
static inline void dpaa_wgfp_xmit(struct dpaa_wgfp_frame *f, u32 o, u32 l) { }
static inline void dpaa_wgfp_drop(struct dpaa_wgfp_frame *f) { }
#endif

#endif /* _LINUX_FSL_DPAA_WGFP_H */
