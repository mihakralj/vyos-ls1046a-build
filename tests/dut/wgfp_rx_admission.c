// SPDX-License-Identifier: GPL-2.0
/* Host harness for the fail-closed wgfp_pst_rx_sync() default. */
#include <arpa/inet.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/udp.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;

#define ETH_HLEN 14
#define GFP_ATOMIC 0
#define WGFP_FLOW_MAX 16
#define WGFP_PENDING 1
#define READ_ONCE(x) (x)
#define pr_warn_once(...) (++warnings)
#define hash_add_rcu(table, node, key) ((void)(node), (void)(key))

struct dpaa_wgfp_tuple {
	union { u32 saddr; struct in6_addr saddr6; };
	union { u32 daddr; struct in6_addr daddr6; };
	u16 sport, dport;
	u8 proto, family, pad[2];
};
struct wgfp_tmpl {
	int ifindex;
	u8 family, hlen;
	u8 hdr[ETH_HLEN + sizeof(struct ipv6hdr) + sizeof(struct udphdr)];
};
struct wg_peer { bool is_dead, can_receive; };
struct net_device { int unused; };
struct wgfp_flow;
struct wgfp_pst {
	struct wg_peer *peer;
	struct net_device *wgdev;
	struct wgfp_flow *rxfl;
	unsigned int nflows;
	bool stopping;
};
struct wgfp_flow {
	struct dpaa_wgfp_tuple t;
	struct wgfp_pst *pst;
	int ifindex, node, state;
	u32 hash;
	unsigned long since;
	bool rx, del;
};

static int wgfp_flows_lock, wgfp_flow_work, system_wq;
static unsigned int wgfp_nflows, work_queued, admission_checks, warnings;
static unsigned long jiffies;

static void spin_lock_bh(int *lock) { (void)lock; }
static void spin_unlock_bh(int *lock) { (void)lock; }
static void *dev_net(struct net_device *dev) { return dev; }
static bool wgfp_tuple_eq(const struct dpaa_wgfp_tuple *a,
			  const struct dpaa_wgfp_tuple *b)
{
	return memcmp(a, b, sizeof(*a)) == 0;
}
static bool wgfp_ct_admitted(void *net, void *skb,
			     const struct dpaa_wgfp_tuple *tuple, bool *absent)
{
	(void)net;
	(void)skb;
	(void)tuple;
	admission_checks++;
	*absent = true;
	return false;
}
static bool wgfp_peer_can_receive(struct wg_peer *peer)
{
	return peer->can_receive;
}
static u32 wgfp_tuple_hash(const struct dpaa_wgfp_tuple *tuple)
{
	(void)tuple;
	return 1;
}
static struct wgfp_flow *wgfp_flow_find_tuple(
	const struct dpaa_wgfp_tuple *tuple, u32 hash)
{
	(void)tuple;
	(void)hash;
	return NULL;
}
static void *kzalloc(size_t size, int flags)
{
	(void)flags;
	return calloc(1, size);
}
static void queue_work(int queue, int *work)
{
	(void)queue;
	(void)work;
	work_queued++;
}

#include "wgfp_rx_admission_body.h"

int main(void)
{
	struct wg_peer peer = { .can_receive = true };
	struct net_device dev = { 0 };
	struct wgfp_pst pst = { .peer = &peer, .wgdev = &dev };
	struct wgfp_tmpl tmpl = {
		.ifindex = 4,
		.family = AF_INET,
		.hlen = ETH_HLEN + sizeof(struct iphdr) + sizeof(struct udphdr),
	};

	wgfp_pst_rx_sync(&pst, &tmpl);
	if (pst.rxfl || wgfp_nflows || admission_checks || work_queued || warnings) {
		fprintf(stderr, "disabled receive mode unexpectedly admitted a flow\n");
		return 1;
	}

	wgfp_pst_rx_sync(&pst, &tmpl);
	if (pst.rxfl || wgfp_nflows || admission_checks || work_queued || warnings) {
		fprintf(stderr, "repeated sync changed disabled receive state\n");
		return 2;
	}
	return 0;
}
