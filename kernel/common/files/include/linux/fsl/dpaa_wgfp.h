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

#include <linux/atomic.h>
#include <linux/cpumask.h>
#include <linux/percpu.h>
#include <linux/types.h>
#include <linux/in6.h>
#include <linux/dma-mapping.h>
#include <asm/page.h>

struct net_device;
struct sk_buff;

/* Single-writer per-CPU counter increment for BH-disabled NAPI context;
 * avoids ARMv8.0 ldxr/stxr and redundant preempt_disable/enable. Callers
 * outside BH-disabled context must disable BH around this call. */
static inline void dpaa_wgfp_stat_inc(atomic64_t __percpu *stat)
{
	atomic64_t *local = this_cpu_ptr(stat);

	WRITE_ONCE(local->counter, READ_ONCE(local->counter) + 1);
}

static inline u64 dpaa_wgfp_stat_sum(const atomic64_t __percpu *stat)
{
	u64 total = 0;
	int cpu;

	for_each_possible_cpu(cpu)
		total += (u64)READ_ONCE(per_cpu_ptr(stat, cpu)->counter);
	return total;
}

struct dpaa_wgfp_frame {
	u8 *buf;		/* virtual address of the buffer start */
	dma_addr_t addr;	/* bus address of the buffer start */
	struct device *dev;	/* DMA device of the buffer pool */
	u32 off;		/* offset of the frame data in the buffer */
	u32 len;		/* frame length */
	u32 buf_size;		/* usable buffer size from the buffer start */
	int ingress_ifindex;	/* port the steered record belongs to */
	void *fd;		/* the dequeued struct qm_fd (opaque to users) */
};

/* WGFP-v2 owned TX slot (pre-mapped order-0 DMA_TO_DEVICE page).
 * Bytes [0..63] hold internal completion metadata; packet data starts at >= 64. */
#define DPAA_WGFP_TX_HEADROOM	64
#define DPAA_WGFP_TX_BUF_SIZE	(PAGE_SIZE - DPAA_WGFP_TX_HEADROOM)

struct dpaa_wgfp_tx_buf {
	u8 *buf;		/* page base virtual address; write at offset >= 64 */
	u32 buf_size;		/* PAGE_SIZE */
	int ifindex;		/* egress netdev ifindex bound at alloc */
	void *priv;		/* opaque struct wgfp_tx_slot * */
};

struct dpaa_wgfp_ops {
	/* Called in NAPI context, buffer already synced for the CPU.
	 * Return 1 when the consumer took the buffer (it must have called
	 * dpaa_wgfp_tx_submit(), dpaa_wgfp_xmit(), dpaa_wgfp_xmit_skb(),
	 * or dpaa_wgfp_drop()), 0 to decline.
	 */
	int (*frame)(void *priv, struct dpaa_wgfp_frame *f);
	/* Synchronously withdraw all flows/rflows referencing @dev.
	 * Called in process context (may be under RTNL); must NOT take
	 * wgfp_lab_lock. Takes @dev directly because during NETDEV_UNREGISTER
	 * @dev is already unlisted from init_net. Returns 0 when settled or
	 * -EBUSY if stuck.
	 */
	int (*port_quiesce)(void *priv, struct net_device *dev);
	/* Synchronously withdraw all flows/rflows and bump observation epoch.
	 * Called in process context; must NOT take wgfp_lab_lock.
	 */
	int (*policy_revoke)(void *priv);
};

/* An inner flow arriving on a DPAA port (network byte order).  IPv4 uses
 * saddr/daddr (the first word of the 16-byte slots); every byte is part of the
 * identity (compared and hashed whole), so build tuples zero-initialised. */
struct dpaa_wgfp_tuple {
	union { __be32 saddr; struct in6_addr saddr6; };
	union { __be32 daddr; struct in6_addr daddr6; };
	__be16 sport;
	__be16 dport;
	u8 proto;
	u8 family;		/* AF_INET or AF_INET6 */
	u8 _pad[2];
};

/*
 * Offload family policy. WireGuard has no offload setting of its own: a flow
 * is accelerated when the port it arrives on (inner family) and the port
 * under the tunnel (outer family) carry `set interfaces ethernet ethN offload
 * ipv4|ipv6`. ask.ko owns that per-port mask and provides it here; with no
 * provider registered nothing is admitted.
 */
#define DPAA_WGFP_FAM_V4	0x1
#define DPAA_WGFP_FAM_V6	0x2
typedef u8 (*dpaa_wgfp_family_fn)(struct net_device *dev);

#if IS_ENABLED(CONFIG_FSL_DPAA_ETH)
/* @fn runs under rcu_read_lock() and must not sleep; NULL unregisters and
 * waits for running callers. */
void dpaa_wgfp_set_family_policy(dpaa_wgfp_family_fn fn);
/* Does @dev's port have offload for @family (AF_INET or AF_INET6)? */
bool dpaa_wgfp_family_ok(struct net_device *dev, u8 family);
/* True when @dev is a DPAA port whose ehash table is armed (ASK engaged). */
bool dpaa_wgfp_port_ok(struct net_device *dev);
/* Steer @t arriving on @in to a sink belonging to @in (so the dequeued frame
 * carries its ingress port). @rx selects the fan-out distributor, when one is
 * configured, so one outer flow can be decrypted on several CPUs. Process
 * context only. Returns 0 on success or a negative errno. */
int dpaa_wgfp_flow_add(struct net_device *in, const struct dpaa_wgfp_tuple *t,
		       u32 hash, bool rx);
int dpaa_wgfp_flow_del(struct net_device *in, const struct dpaa_wgfp_tuple *t);
/* Silicon packet counter of the record for @t. Process context only. */
int dpaa_wgfp_flow_packets(struct net_device *in,
			   const struct dpaa_wgfp_tuple *t, u64 *packets);
int dpaa_wgfp_register(const struct dpaa_wgfp_ops *ops, void *priv);
void dpaa_wgfp_unregister(void);
/* Egress handle (>= 0) of a DPAA port: its no-confirm TX frame queue,
 * allocated on first use.  Process context, not under RTNL. */
int dpaa_wgfp_egress(struct net_device *dev);
/* The handle dpaa_wgfp_egress() already gave @dev, or -ENOENT; any context. */
int dpaa_wgfp_egress_find(const struct net_device *dev);
/* Synchronously quiesce WGFP records targeting @dev (T1(f)/T1(b)). */
int dpaa_wgfp_port_quiesce(struct net_device *dev);
/* Synchronously revoke all WGFP records and rflows on policy change (P3). */
int dpaa_wgfp_policy_revoke(void);
/* Allocate a pre-mapped WGFP-v2 TX slot for @egress (validating @expected_ifindex
 * if > 0 and @max_len <= DPAA_WGFP_TX_BUF_SIZE). Reserving inflight on @egress;
 * every true return must be paired with dpaa_wgfp_tx_submit() or dpaa_wgfp_tx_cancel(). */
bool dpaa_wgfp_tx_alloc(int egress, int expected_ifindex, u32 max_len,
			struct dpaa_wgfp_tx_buf *out);
/* Release an unsubmitted WGFP-v2 TX slot back to its per-CPU pool. */
void dpaa_wgfp_tx_cancel(struct dpaa_wgfp_tx_buf *tx);
/* Submit a populated WGFP-v2 TX slot directly to QMan (via ORP if owed, else
 * per-CPU tx_batch / direct enqueue) and release @f (if non-NULL) to BMan. */
void dpaa_wgfp_tx_submit(struct dpaa_wgfp_frame *f, struct dpaa_wgfp_tx_buf *tx,
			 u32 off, u32 len);
/* Settle any owed ORP sequence slot immediately with a hole before handing
 * a packet to the Linux network stack (prevents ORL head-of-line stalls). */
void dpaa_wgfp_orp_settle(void);
/* Allocate an owned skb for @egress (validating @expected_ifindex if > 0). */
struct sk_buff *dpaa_wgfp_alloc_xmit_skb(int egress, int expected_ifindex,
					 u32 total_len);
/* Transmit an already-built owned @skb on @egress (validating @expected_ifindex
 * if > 0) and release @f (if non-NULL) before dev_queue_xmit(). */
void dpaa_wgfp_xmit_skb(struct dpaa_wgfp_frame *f, int egress,
			int expected_ifindex, struct sk_buff *skb);
/* Transmit the (modified) buffer on @egress: new data offset and length. */
void dpaa_wgfp_xmit(struct dpaa_wgfp_frame *f, int egress, u32 new_off,
		    u32 new_len);
/* Return the buffer to its pool. */
void dpaa_wgfp_drop(struct dpaa_wgfp_frame *f);
#else
static inline void dpaa_wgfp_set_family_policy(dpaa_wgfp_family_fn fn) { }
static inline bool dpaa_wgfp_family_ok(struct net_device *dev, u8 family) { return false; }
static inline bool dpaa_wgfp_port_ok(struct net_device *dev) { return false; }
static inline int dpaa_wgfp_flow_add(struct net_device *in,
				     const struct dpaa_wgfp_tuple *t, u32 hash,
				     bool rx)
{ return -ENODEV; }
static inline int dpaa_wgfp_flow_del(struct net_device *in,
				     const struct dpaa_wgfp_tuple *t)
{ return -ENODEV; }
static inline int dpaa_wgfp_flow_packets(struct net_device *in,
					 const struct dpaa_wgfp_tuple *t,
					 u64 *packets)
{ return -ENODEV; }
static inline int dpaa_wgfp_register(const struct dpaa_wgfp_ops *ops, void *priv)
{ return -ENODEV; }
static inline void dpaa_wgfp_unregister(void) { }
static inline int dpaa_wgfp_egress(struct net_device *dev) { return -ENODEV; }
static inline int dpaa_wgfp_egress_find(const struct net_device *dev) { return -ENODEV; }
static inline int dpaa_wgfp_port_quiesce(struct net_device *dev) { return 0; }
static inline int dpaa_wgfp_policy_revoke(void) { return 0; }
static inline bool dpaa_wgfp_tx_alloc(int e, int i, u32 m,
				      struct dpaa_wgfp_tx_buf *o)
{ return false; }
static inline void dpaa_wgfp_tx_cancel(struct dpaa_wgfp_tx_buf *tx) { }
static inline void dpaa_wgfp_tx_submit(struct dpaa_wgfp_frame *f,
				       struct dpaa_wgfp_tx_buf *tx, u32 o, u32 l) { }
static inline void dpaa_wgfp_orp_settle(void) { }
static inline struct sk_buff *dpaa_wgfp_alloc_xmit_skb(int e, int i, u32 l) { return NULL; }
static inline void dpaa_wgfp_xmit_skb(struct dpaa_wgfp_frame *f, int e, int i,
				      struct sk_buff *skb) { }
static inline void dpaa_wgfp_xmit(struct dpaa_wgfp_frame *f, int e, u32 o, u32 l) { }
static inline void dpaa_wgfp_drop(struct dpaa_wgfp_frame *f) { }
#endif

#endif /* _LINUX_FSL_DPAA_WGFP_H */
