/* SPDX-License-Identifier: GPL-2.0 */
#ifndef _WG_FASTPATH_H
#define _WG_FASTPATH_H

#include <linux/kconfig.h>

struct sk_buff;
struct wg_peer;

#if IS_ENABLED(CONFIG_FSL_DPAA_ETH)
void wg_fastpath_uninit(void);
/* wg_xmit(): offer a packet for @peer to the DPAA fast path (P2b-2). */
void wg_fastpath_learn(struct wg_peer *peer, struct sk_buff *skb);
#else
static inline void wg_fastpath_uninit(void) { }
static inline void wg_fastpath_learn(struct wg_peer *peer, struct sk_buff *skb) { }
#endif

#endif /* _WG_FASTPATH_H */
