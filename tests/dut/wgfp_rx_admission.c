// SPDX-License-Identifier: GPL-2.0
/* Host harness for wgfp_pst_rx_sync() and the module parameter default. */
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
static struct wgfp_flow *wgfp_flow_find(const struct dpaa_wgfp_tuple *tuple,
					int ifindex, u32 hash)
{
	(void)tuple;
	(void)ifindex;
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
	struct iphdr *ip = (struct iphdr *)(tmpl.hdr + ETH_HLEN);
	struct udphdr *udp = (struct udphdr *)(tmpl.hdr + ETH_HLEN +
					       sizeof(struct iphdr));
	struct wgfp_flow *flow;

	ip->saddr = htonl(0x0a000001);
	ip->daddr = htonl(0x0a000002);
	udp->source = htons(51820);
	udp->dest = htons(51821);

	wgfp_pst_rx_sync(&pst, &tmpl);
	if (pst.rxfl || wgfp_nflows || admission_checks || work_queued || warnings) {
		fprintf(stderr, "default receive mode unexpectedly admitted a flow\n");
		return 1;
	}

	wgfp_rx = true;
	wgfp_pst_rx_sync(&pst, &tmpl);
	flow = pst.rxfl;
	if (!flow || !flow->rx || flow->ifindex != tmpl.ifindex ||
	    flow->t.saddr != ip->daddr || flow->t.daddr != ip->saddr ||
	    flow->t.sport != udp->dest || flow->t.dport != udp->source ||
	    wgfp_nflows != 1 || admission_checks != 1 ||
	    work_queued != 1 || warnings != 1) {
		fprintf(stderr, "explicit receive opt-in did not schedule the flow\n");
		return 2;
	}

	wgfp_rx = false;
	wgfp_pst_rx_sync(&pst, &tmpl);
	if (pst.rxfl || !flow->del || work_queued != 2) {
		fprintf(stderr, "disabling receive mode did not schedule withdrawal\n");
		return 3;
	}
	free(flow);
	return 0;
}
