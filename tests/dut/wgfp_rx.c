// SPDX-License-Identifier: GPL-2.0
/* Host harness for the actual wgfp_rx_frame() body, supplied at build time. */
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
typedef uint32_t u32;
typedef uint32_t __wsum;
typedef uint16_t __sum16;

#define ETH_HLEN 14
#define IFF_UP 1
#define PACKET_HOST 0
#define unlikely(x) (x)
#define dpaa_wgfp_stat_inc(p) (++*(p))

struct net_device {
	unsigned int flags;
	int ifindex;
};

struct sk_buff {
	u8 storage[512];
	u8 *data;
	u32 len;
	unsigned short protocol;
	int pkt_type, skb_iif;
};

struct dpaa_wgfp_frame {
	u8 *buf;
	u32 off, len;
};

struct peer { void *device; };
struct pst { struct peer *peer; };
struct wgfp_flow { int ifindex; struct { int family; } t; struct pst *pst; };

static int init_net, wgfp_rx_frames, wgfp_rx_stack, fast, stack, delivered_len;
static struct net_device dev = { IFF_UP, 3 };

static struct net_device *dev_get_by_index_rcu(int *net, int index)
{
	(void)net;
	return index == dev.ifindex ? &dev : NULL;
}

static struct sk_buff *netdev_alloc_skb_ip_align(struct net_device *d, u32 len)
{
	struct sk_buff *skb = calloc(1, sizeof(*skb));

	(void)d;
	if (!skb || len > sizeof(skb->storage))
		exit(2);
	skb->data = skb->storage;
	return skb;
}

static void skb_put_data(struct sk_buff *skb, const void *data, u32 len)
{
	memcpy(skb->data, data, len);
	skb->len = len;
}

static void dpaa_wgfp_drop(struct dpaa_wgfp_frame *f)
{
	memset(f->buf + f->off, 0xa5, f->len);
}

static unsigned short eth_type_trans(struct sk_buff *skb, struct net_device *d)
{
	(void)d;
	skb->data += ETH_HLEN;
	skb->len -= ETH_HLEN;
	return 0;
}

static bool ip_is_fragment(const struct iphdr *ip)
{
	return (ntohs(ip->frag_off) & 0x3fff) != 0;
}

static int ip_fast_csum(const u8 *ip, int words)
{
	(void)ip;
	(void)words;
	return 0;
}

static __wsum csum_partial(const void *data, int len, __wsum sum)
{
	const u8 *p = data;

	while (len >= 2) {
		sum += ((u32)p[0] << 8) | p[1];
		p += 2;
		len -= 2;
	}
	if (len)
		sum += (u32)*p << 8;
	return sum;
}

static __sum16 finish_checksum(__wsum sum)
{
	while (sum >> 16)
		sum = (sum & 0xffff) + (sum >> 16);
	return (uint16_t)~sum;
}

static __sum16 csum_tcpudp_magic(__be32 src, __be32 dst, u32 len,
				u8 proto, __wsum sum)
{
	sum = csum_partial(&src, sizeof(src), sum);
	sum = csum_partial(&dst, sizeof(dst), sum);
	return finish_checksum(sum + len + proto);
}

static __sum16 csum_ipv6_magic(const struct in6_addr *src,
			       const struct in6_addr *dst, u32 len,
			       u8 proto, __wsum sum)
{
	sum = csum_partial(src, sizeof(*src), sum);
	sum = csum_partial(dst, sizeof(*dst), sum);
	return finish_checksum(sum + len + proto);
}

static int pskb_trim(struct sk_buff *skb, u32 len)
{
	if (len > skb->len)
		return -1;
	skb->len = len;
	return 0;
}

static void skb_reset_network_header(struct sk_buff *skb) { (void)skb; }
static void skb_set_transport_header(struct sk_buff *skb, u32 off)
{
	(void)skb;
	(void)off;
}

static void netif_receive_skb(struct sk_buff *skb)
{
	stack++;
	free(skb);
}

static void wg_packet_receive(void *device, struct sk_buff *skb)
{
	(void)device;
	fast++;
	delivered_len = skb->len;
	free(skb);
}

/* P6 (wgfp_rx_fast) declines every frame here: the P3 path is under test. */
#define READ_ONCE(x) (x)
#define atomic64_inc(p) (++*(p))
static unsigned int wgfp_rx_fast = 1;
static int wgfp_p6_punt;
static int wgfp_rx_fast_frame(struct dpaa_wgfp_frame *f, struct wgfp_flow *fl,
			      struct net_device *d)
{
	(void)f;
	(void)fl;
	(void)d;
	return 0;
}

#include "wgfp_rx_body.h"

enum packet_kind {
	VALID,
	ZERO_CHECKSUM,
	BAD_CHECKSUM,
	UDP_TOO_SHORT,
	UDP_TOO_LONG,
	TRUNCATED_UDP,
};

static int test_packet(int family, enum packet_kind kind, bool expect_fast)
{
	u8 frame[128] = { 0 };
	u32 hl = family == AF_INET ? sizeof(struct iphdr) : sizeof(struct ipv6hdr);
	u32 udp_len = sizeof(struct udphdr) + 3;
	u32 ip_len = hl + (kind == TRUNCATED_UDP ? 4 : udp_len);
	struct dpaa_wgfp_frame f = { .buf = frame, .len = ETH_HLEN + ip_len };
	struct peer peer = { 0 };
	struct pst pst = { .peer = &peer };
	struct wgfp_flow flow = { .ifindex = 3, .t.family = family, .pst = &pst };
	struct iphdr *ip = (struct iphdr *)(frame + ETH_HLEN);
	struct ipv6hdr *ip6 = (struct ipv6hdr *)(frame + ETH_HLEN);
	struct udphdr *udp = (struct udphdr *)(frame + ETH_HLEN + hl);
	__sum16 checksum;

	if (family == AF_INET) {
		ip->version = 4;
		ip->ihl = 5;
		ip->tot_len = htons(ip_len);
		ip->protocol = IPPROTO_UDP;
		ip->saddr = htonl(0x0a000001);
		ip->daddr = htonl(0x0a000002);
	} else {
		ip6->version = 6;
		ip6->payload_len = htons(ip_len - hl);
		ip6->nexthdr = IPPROTO_UDP;
		ip6->saddr.s6_addr[15] = 1;
		ip6->daddr.s6_addr[15] = 2;
	}
	udp->source = htons(10000);
	udp->dest = htons(51820);
	udp->len = htons(udp_len);
	memcpy((u8 *)(udp + 1), "wg!", 3);
	if (family == AF_INET)
		checksum = csum_tcpudp_magic(ip->saddr, ip->daddr, udp_len,
					     IPPROTO_UDP, csum_partial(udp, udp_len, 0));
	else
		checksum = csum_ipv6_magic(&ip6->saddr, &ip6->daddr, udp_len,
					   IPPROTO_UDP, csum_partial(udp, udp_len, 0));
	udp->check = htons(checksum ? checksum : 0xffff);
	if (kind == ZERO_CHECKSUM)
		udp->check = 0;
	else if (kind == BAD_CHECKSUM)
		udp->check ^= htons(1);
	else if (kind == UDP_TOO_SHORT)
		udp->len = htons(sizeof(*udp) - 1);
	else if (kind == UDP_TOO_LONG)
		udp->len = htons(udp_len + 1);

	fast = stack = delivered_len = wgfp_rx_frames = wgfp_rx_stack = 0;
	wgfp_p6_punt = 0;
	if (wgfp_rx_frame(&f, &flow) != 1 || wgfp_p6_punt != 1 ||
	    fast != expect_fast || stack != !expect_fast ||
	    wgfp_rx_frames != expect_fast || wgfp_rx_stack != !expect_fast ||
	    (expect_fast && delivered_len != (int)ip_len)) {
		fprintf(stderr, "family=%d kind=%d fast=%d stack=%d len=%d\n",
			family, kind, fast, stack, delivered_len);
		return 1;
	}
	return 0;
}

int main(void)
{
	int failures = 0;

	failures += test_packet(AF_INET, VALID, true);
	failures += test_packet(AF_INET, ZERO_CHECKSUM, true);
	failures += test_packet(AF_INET, BAD_CHECKSUM, false);
	failures += test_packet(AF_INET6, VALID, true);
	failures += test_packet(AF_INET6, ZERO_CHECKSUM, false);
	failures += test_packet(AF_INET6, BAD_CHECKSUM, false);
	failures += test_packet(AF_INET, UDP_TOO_SHORT, false);
	failures += test_packet(AF_INET6, UDP_TOO_LONG, false);
	failures += test_packet(AF_INET, TRUNCATED_UDP, false);
	return failures ? 1 : 0;
}
