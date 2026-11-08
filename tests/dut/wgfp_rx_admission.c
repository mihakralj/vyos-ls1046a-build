// SPDX-License-Identifier: GPL-2.0
/* Host harness for P3 admission: only an exact outer tuple observed on the
 * stack, on that ingress port, may install a receive record. */
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

#define ETH_P_IP 0x0800
#define ETH_P_IPV6 0x86dd
#define GFP_ATOMIC 0
#define WGFP_FLOW_MAX 16
#define WGFP_PENDING 1
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define smp_wmb() do { } while (0)
#define smp_rmb() do { } while (0)
#define pr_warn_once(...) (++warnings)
#define hash_add_rcu(table, ...) ((void)(table))
#define DEFINE_PER_CPU(type, name) type name
#define this_cpu_ptr(p) (p)
#define per_cpu_ptr(p, cpu) ((void)(cpu), (p))
#define for_each_possible_cpu(cpu) for ((cpu) = 0; (cpu) < 1; (cpu)++)
#define module_param(...)
#define MODULE_PARM_DESC(...)

#define skb_transport_header(skb) ((skb)->data + 20)
#define skb_tail_pointer(skb) ((skb)->tail)
#define udp_hdr(skb) ((struct udphdr *)skb_transport_header(skb))
#define ip_hdr(skb) ((struct iphdr *)(skb)->data)
#define ipv6_hdr(skb) ((struct ipv6hdr *)(skb)->data)
#define ip_is_fragment(iph) ((iph)->frag_off & htons(0x3fff))

struct sk_buff {
	u8 *head, *data, *tail;
	__be16 protocol;
	int skb_iif;
};

struct net_device {
	int ifindex;
};

struct wg_device {
	struct net_device *dev;
};

struct dpaa_wgfp_tuple {
	union { u32 saddr; struct in6_addr saddr6; };
	union { u32 daddr; struct in6_addr daddr6; };
	u16 sport, dport;
	u8 proto, family, pad[2];
};

struct endpoint {
	union {
		struct sockaddr addr;
		struct sockaddr_in addr4;
		struct sockaddr_in6 addr6;
	};
};

struct wg_peer {
	struct endpoint endpoint;
	bool is_dead;
};

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
static int wgfp_flows;
static unsigned int wgfp_nflows, work_queued, warnings;
static unsigned long jiffies;

static void spin_lock_bh(int *lock) { (void)lock; }
static void spin_unlock_bh(int *lock) { (void)lock; }
static bool wgfp_tuple_eq(const struct dpaa_wgfp_tuple *a,
			  const struct dpaa_wgfp_tuple *b)
{
	return memcmp(a, b, sizeof(*a)) == 0;
}
static u32 wgfp_tuple_hash(const struct dpaa_wgfp_tuple *tuple)
{
	(void)tuple;
	return 1;
}
static struct wgfp_flow *wgfp_flow_find_tuple(
	const struct dpaa_wgfp_tuple *tuple, int ifindex, u32 hash)
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
static int init_net;
static struct net_device fake_dev;
static struct net_device *dev_get_by_index(void *net, int ifindex)
{
	(void)net;
	fake_dev.ifindex = ifindex;
	return ifindex ? &fake_dev : NULL;
}
#define dev_put(dev) ((void)(dev))
#define dpaa_wgfp_family_ok(dev, family) ((void)(dev), (void)(family), true)
static void queue_work(int queue, int *work)
{
	(void)queue;
	(void)work;
	work_queued++;
}

#include "wgfp_rx_admission_body.h"

static void obs_put(struct net_device *dev, struct dpaa_wgfp_tuple *t,
		    int ifindex)
{
	struct wgfp_obs_ring *ring = &wgfp_obs;
	struct wgfp_obs *o = &ring->e[0];

	ring->seq++;
	o->t = *t;
	o->wg_ifindex = dev->ifindex;
	o->ifindex = ifindex;
	o->jiffies = jiffies;
	ring->seq++;
}

int main(void)
{
	struct net_device eth4 = { .ifindex = 4 };
	struct wg_peer peer = { 0 };
	struct wgfp_pst pst = { .peer = &peer, .wgdev = &eth4 };
	struct dpaa_wgfp_tuple t = { 0 };

	wgfp_rx = true;

	peer.endpoint.addr4.sin_family = AF_INET;
	peer.endpoint.addr4.sin_port = htons(40001);
	peer.endpoint.addr4.sin_addr.s_addr = htonl(0x0a630171);
	t.family = AF_INET;
	t.sport = htons(40001);
	t.saddr = htonl(0x0a630171);
	t.daddr = htonl(0x0a630271);
	t.dport = htons(51999);
	t.proto = IPPROTO_UDP;

	/* no observation: nothing admitted */
	wgfp_pst_rx_sync(&pst);
	if (pst.rxfl || wgfp_nflows || work_queued || warnings) {
		fprintf(stderr, "unobserved tuple admitted\n");
		return 1;
	}

	/* exact tuple on eth4: admitted, carries that ingress port */
	obs_put(&eth4, &t, eth4.ifindex);
	wgfp_pst_rx_sync(&pst);
	if (!pst.rxfl || wgfp_nflows != 1 || !work_queued ||
	    pst.rxfl->ifindex != eth4.ifindex || !pst.rxfl->rx ||
	    pst.rxfl->state != WGFP_PENDING || !warnings) {
		fprintf(stderr, "observed tuple not admitted with provenance\n");
		return 2;
	}

	/* same tuple observed on another port moves the record there */
	obs_put(&eth4, &t, 6);
	wgfp_pst_rx_sync(&pst);
	if (!pst.rxfl || pst.rxfl->ifindex != 6) {
		fprintf(stderr, "ingress change did not move the record\n");
		return 3;
	}

	/* endpoint moved: the stale observation no longer matches */
	peer.endpoint.addr4.sin_port = htons(40002);
	wgfp_pst_rx_sync(&pst);
	if (pst.rxfl) {
		fprintf(stderr, "endpoint change kept the record\n");
		return 4;
	}

	/* and the receive switch is still the outer gate */
	peer.endpoint.addr4.sin_port = htons(40001);
	t.sport = htons(40001);
	wgfp_rx = false;
	obs_put(&eth4, &t, eth4.ifindex);
	wgfp_pst_rx_sync(&pst);
	if (pst.rxfl) {
		fprintf(stderr, "disabled receive offload admitted a flow\n");
		return 5;
	}
	return 0;
}