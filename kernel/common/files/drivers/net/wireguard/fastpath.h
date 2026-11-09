/* SPDX-License-Identifier: GPL-2.0 */
#ifndef _WG_FASTPATH_H
#define _WG_FASTPATH_H

#include <linux/kconfig.h>

#include <linux/types.h>

struct sk_buff;
struct wg_peer;
struct wg_device;
struct noise_keypair;

#if IS_ENABLED(CONFIG_FSL_DPAA_ETH)
void wg_fastpath_uninit(void);
/* wg_xmit(): offer a packet for @peer to the DPAA fast path (P2b-2). */
void wg_fastpath_learn(struct wg_peer *peer, struct sk_buff *skb);
#else
static inline void wg_fastpath_uninit(void) { }
static inline void wg_fastpath_learn(struct wg_peer *peer, struct sk_buff *skb) { }
#endif
/* wg_packet_consume_data_done(): forward a learned inner flow directly (P7);
 * true when it took the skb. */
#if IS_ENABLED(CONFIG_FSL_DPAA_ETH)
bool wg_fastpath_rx_tail(struct wg_peer *peer, struct sk_buff *skb);
#else
static inline bool wg_fastpath_rx_tail(struct wg_peer *peer, struct sk_buff *skb) { return false; }
#endif
/* wg_receive(): record an outer tuple the stack admitted, for P3. */
void wg_fastpath_rx_observe(struct wg_device *wg, struct sk_buff *skb);
/* receive.c bookkeeping for packets fastpath.c decrypts itself (P6). */
bool wg_fastpath_rx_validate(struct noise_keypair *keypair, u64 nonce);
void wg_fastpath_rx_timers(struct wg_peer *peer);
void wg_fastpath_rx_stats(struct wg_peer *peer, size_t len);

#endif /* _WG_FASTPATH_H */
