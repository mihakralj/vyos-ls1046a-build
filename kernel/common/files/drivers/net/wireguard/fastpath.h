/* SPDX-License-Identifier: GPL-2.0 */
#ifndef _WG_FASTPATH_H
#define _WG_FASTPATH_H

#include <linux/kconfig.h>

struct sk_buff;
struct wg_peer;
struct wg_device;

#if IS_ENABLED(CONFIG_FSL_DPAA_ETH)
void wg_fastpath_uninit(void);
/* wg_xmit(): offer a packet for @peer to the DPAA fast path (P2b-2). */
void wg_fastpath_learn(struct wg_peer *peer, struct sk_buff *skb);
#else
static inline void wg_fastpath_uninit(void) { }
static inline void wg_fastpath_learn(struct wg_peer *peer, struct sk_buff *skb) { }
#endif
/* wg_receive(): record an outer tuple the stack admitted, for P3. */
void wg_fastpath_rx_observe(struct wg_device *wg, struct sk_buff *skb);

#endif /* _WG_FASTPATH_H */
