// SPDX-License-Identifier: GPL-2.0
/*
 * Host harness for the actual P6 receive bodies of fastpath.c (wgfp_parse,
 * wgfp_rx_fast_stack, wgfp_rx_forward, wgfp_rx_fast_frame), supplied at build
 * time as wgfp_p6_body.h.  The AEAD is a stub: the "ciphertext" is the
 * plaintext XOR 0x5a and the tag is 16 bytes of 0xaa; a wrong tag leaves the
 * buffer untouched, as chacha20poly1305_decrypt() does.
 */
#include <arpa/inet.h>
#include <assert.h>
#include <linux/if_ether.h>
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
typedef uint64_t u64;

#define unlikely(x) (x)
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define rcu_dereference(p) (p)
#define cpu_to_le32(x) (x)
#define le64_to_cpu(x) (x)
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define HZ 250
#define time_after(a, b) ((long)((b) - (a)) < 0)
#define IFF_UP 1
#define CHECKSUM_UNNECESSARY 1
#define TCPHDR_FIN 0x01
#define TCPHDR_SYN 0x02
#define TCPHDR_RST 0x04
#define INET_ECN_ECT_1 1
#define POLY1305_DIGEST_SIZE 16
#define MESSAGE_DATA 4
#define MESSAGE_MINIMUM_LENGTH 32
#define REJECT_AFTER_MESSAGES (~0ULL - 8192)
#define REJECT_AFTER_TIME 180
#define atomic64_inc(p) (++*(p))
#define dpaa_wgfp_stat_inc(p) (++*(p))
#define DEV_STATS_INC(d, f) ((d)->f++)
#define WGFP_RX_HEADROOM 128
#define CHECKSUM_NONE 0
#define rcu_read_lock() do { } while (0)
#define rcu_read_unlock() do { } while (0)
static bool wgfp_rx_tail = true;
static long wgfp_p7_fwd, wgfp_p7_drop;
static int init_net, direct_xmits, direct_q, direct_ret;
static struct net_device *direct_dev;
static u32 direct_len;
static u8 direct_hdr[ETH_HLEN];

static unsigned long jiffies = 1000;

struct net_device {
	unsigned int flags;
	int ifindex;
	u8 dev_addr[ETH_ALEN];
	int rx_dropped, rx_errors, rx_frame_errors;
	unsigned int real_num_tx_queues;
};

struct dpaa_wgfp_tuple {
	union { uint32_t saddr; struct in6_addr saddr6; };
	union { uint32_t daddr; struct in6_addr daddr6; };
	uint16_t sport, dport;
	u8 proto, family, _pad[2];
};

struct dpaa_wgfp_frame {
	u8 *buf;
	u32 off, len, buf_size;
};

struct noise_keypair {
	struct { __le32 index; } entry;
	struct { bool is_valid; u64 birthdate; u8 key[32]; } receiving;
	struct { u64 counter; } receiving_counter;
};

struct wg_device { int peer_allowedips; struct net_device *dev; };
struct wg_peer {
	struct wg_device *device;
	struct { struct noise_keypair *current_keypair; } keypairs;
	bool is_dead;
	u64 internal_id;
};
struct wgfp_pst {
	struct wg_peer *peer;
	struct net_device *wgdev;
	unsigned long last_rx_timer;
};
struct wgfp_flow {
	struct dpaa_wgfp_tuple t;
	struct wgfp_pst *pst;
};
struct wgfp_tmpl { int egress; int ifindex; u8 hdr[ETH_HLEN]; };
struct wgfp_rflow {
	struct dpaa_wgfp_tuple t;
	int wg_ifindex;
	u64 peer_id;
	u32 mtu;
	unsigned long expires;
	struct wgfp_tmpl l2;
};

struct sk_buff {
	u8 storage[2048];
	u8 *data;
	u32 len;
	unsigned short protocol;
	int ip_summed, csum_level, mac_len;
	struct net_device *dev;
};

static unsigned int wgfp_rx_fast = 2;
static long wgfp_p6_fwd, wgfp_p6_stack, wgfp_p6_replay, wgfp_p6_auth,
	    wgfp_p6_err, wgfp_p6_aip, wgfp_p6_nomem;
static int drops, xmits, xmit_egress, delivered, timers, ecn_outer = -1;
static u32 xmit_off, xmit_len, delivered_len;
static size_t stats;
static u8 seen[4096];
static struct wgfp_rflow *rflow;
static struct wg_peer *aip_peer;
static struct sk_buff *last_skb;

static void dpaa_wgfp_drop(struct dpaa_wgfp_frame *f) { (void)f; drops++; }
static void dpaa_wgfp_xmit(struct dpaa_wgfp_frame *f, int egress, u32 off, u32 len)
{
	(void)f;
	xmits++;
	xmit_egress = egress;
	xmit_off = off;
	xmit_len = len;
}
static bool ether_addr_equal(const u8 *a, const u8 *b) { return !memcmp(a, b, ETH_ALEN); }
static bool ip_is_fragment(const struct iphdr *ip) { return (ntohs(ip->frag_off) & 0x3fff) != 0; }
static int ip_fast_csum(const u8 *p, unsigned int ihl)
{
	u32 sum = 0;

	for (unsigned int i = 0; i < ihl * 4; i += 2)
		sum += (u32)p[i] << 8 | p[i + 1];
	while (sum >> 16)
		sum = (sum & 0xffff) + (sum >> 16);
	return (u16)~sum;
}
static void ip_decrease_ttl(struct iphdr *iph)
{	/* include/net/ip.h */
	u32 check = iph->check;

	check += htons(0x0100);
	iph->check = (u16)(check + (check >= 0xffff));
	--iph->ttl;
}
static u8 ipv6_get_dsfield(const struct ipv6hdr *ip6)
{
	return ntohs(*(const uint16_t *)ip6) >> 4;
}
static bool wg_birthdate_has_expired(u64 b, int s) { (void)s; return b == 1; }
static bool chacha20poly1305_decrypt(u8 *dst, const u8 *src, size_t len,
				     const u8 *ad, size_t adlen, u64 nonce,
				     const u8 *key)
{
	(void)ad; (void)adlen; (void)nonce; (void)key;
	for (size_t i = len - 16; i < len; i++)
		if (src[i] != 0xaa)
			return false;
	for (size_t i = 0; i < len - 16; i++)
		dst[i] = src[i] ^ 0x5a;
	return true;
}
static bool wg_fastpath_rx_validate(struct noise_keypair *kp, u64 nonce)
{
	(void)kp;
	if (seen[nonce])
		return false;
	seen[nonce] = 1;
	return true;
}
static void wg_fastpath_rx_timers(struct wg_peer *p) { (void)p; timers++; }
static void wg_fastpath_rx_stats(struct wg_peer *p, size_t len) { (void)p; stats += len; }
static u32 wgfp_tuple_hash(const struct dpaa_wgfp_tuple *t) { (void)t; return 0; }
static struct wgfp_rflow *wgfp_rflow_lookup(const struct dpaa_wgfp_tuple *t,
					    int wg_ifindex, u32 hash)
{
	(void)hash;
	if (rflow && rflow->wg_ifindex == wg_ifindex &&
	    !memcmp(&rflow->t, t, sizeof(*t)))
		return rflow;
	return NULL;
}
static struct sk_buff *netdev_alloc_skb(struct net_device *d, u32 len)
{
	struct sk_buff *skb = calloc(1, sizeof(*skb));

	(void)d;
	assert(skb && len <= sizeof(skb->storage));
	skb->data = skb->storage;
	return skb;
}
static void skb_reserve(struct sk_buff *skb, u32 n) { skb->data += n; }
static void skb_put_data(struct sk_buff *skb, const void *p, u32 len)
{
	memcpy(skb->data, p, len);
	skb->len = len;
}
static void skb_reset_mac_header(struct sk_buff *skb) { (void)skb; }
static void skb_reset_network_header(struct sk_buff *skb) { (void)skb; }
#define ip_hdr(skb) ((struct iphdr *)(skb)->data)
#define ipv6_hdr(skb) ((struct ipv6hdr *)(skb)->data)
static void INET_ECN_decapsulate(struct sk_buff *skb, u8 outer, u8 inner)
{
	(void)skb; (void)inner;
	ecn_outer = outer;
}
static struct wg_peer *wg_allowedips_lookup_src(int *t, struct sk_buff *skb)
{
	(void)t; (void)skb;
	return aip_peer;
}
static void wg_peer_put(struct wg_peer *p) { (void)p; }
static const void *skb_header_pointer(const struct sk_buff *skb, int off, int len, void *buf)
{
	(void)buf;
	return (u32)(off + len) <= skb->len ? skb->data + off : NULL;
}
static bool pskb_may_pull(struct sk_buff *skb, u32 len) { return len <= skb->len; }
static int skb_cow_head(struct sk_buff *skb, u32 h) { return skb->data - skb->storage < (long)h; }
static u8 *skb_push(struct sk_buff *skb, u32 n) { skb->data -= n; skb->len += n; return skb->data; }
static u32 skb_get_hash(struct sk_buff *skb) { (void)skb; return 0x80000000u; }
static u32 reciprocal_scale(u32 v, u32 n) { return (u32)(((u64)v * n) >> 32); }
static struct net_device *tail_eg;
static struct net_device *dev_get_by_index_rcu(int *net, int ifindex)
{
	(void)net;
	return tail_eg && tail_eg->ifindex == ifindex ? tail_eg : NULL;
}
static bool dev_xmit_complete(int rc) { return rc == 0; }
static int dev_direct_xmit(struct sk_buff *skb, u16 q)
{
	direct_xmits++;
	direct_q = q;
	direct_dev = skb->dev;
	direct_len = skb->len;
	memcpy(direct_hdr, skb->data, ETH_HLEN);
	return direct_ret;
}
static void kfree_skb(struct sk_buff *skb) { free(skb); }
static void netif_receive_skb(struct sk_buff *skb)
{
	delivered++;
	delivered_len = skb->len;
	free(last_skb);
	last_skb = skb;
}

struct message_data {
	struct { __le32 type; } header;
	__le32 key_idx;
	__le64 counter;
};

#include "wgfp_p6_body.h"
#include "wgfp_p7_body.h"

/* ---- fixtures ---- */

static const u8 our_mac[ETH_ALEN] = { 2, 0, 0, 0, 0, 0x11 };
static const u8 out_l2[ETH_HLEN] = { 0xde, 0xad, 0, 0, 0, 1, 2, 0, 0, 0, 0, 0x22, 0x08, 0x00 };

struct pkt {
	int outer6, inner6;
	u8 inner_proto, tcp_flags, ttl, outer_tos;
	u32 payload;		/* inner L4 payload bytes after the 20-byte TCP/8-byte UDP header */
	u32 pad;		/* WireGuard padding after the inner packet */
	u64 nonce;
	__le32 key_idx;
	bool bad_tag, keepalive, zero_udp6_csum, wrong_mac, bogus_inner;
};

static u8 frame[2048];
static u32 inner_off, inner_len;

static struct dpaa_wgfp_frame build(const struct pkt *p)
{
	u32 ohl = p->outer6 ? sizeof(struct ipv6hdr) : sizeof(struct iphdr);
	u32 ihl = p->inner6 ? sizeof(struct ipv6hdr) : sizeof(struct iphdr);
	u32 l4 = p->inner_proto == IPPROTO_TCP ? 20 : 8;
	u32 off = 64, plain, ulen;
	u8 *eth = frame + off, *ip = eth + ETH_HLEN, *in;
	struct udphdr *udp = (struct udphdr *)(ip + ohl);
	struct message_data *m = (struct message_data *)(udp + 1);

	memset(frame, 0, sizeof(frame));
	inner_len = p->keepalive ? 0 : ihl + l4 + p->payload;
	plain = inner_len + p->pad;
	ulen = sizeof(*udp) + sizeof(*m) + plain + 16;
	memcpy(eth, p->wrong_mac ? out_l2 : our_mac, ETH_ALEN);
	eth[12] = p->outer6 ? 0x86 : 0x08;
	eth[13] = p->outer6 ? 0xdd : 0x00;
	if (p->outer6) {
		struct ipv6hdr *o = (struct ipv6hdr *)ip;

		o->version = 6;
		o->priority = p->outer_tos >> 4;
		o->flow_lbl[0] = (p->outer_tos & 0xf) << 4;
		o->payload_len = htons(ulen);
		o->nexthdr = IPPROTO_UDP;
		udp->check = p->zero_udp6_csum ? 0 : 0x1234;
	} else {
		struct iphdr *o = (struct iphdr *)ip;

		o->version = 4;
		o->ihl = 5;
		o->tos = p->outer_tos;
		o->tot_len = htons(ohl + ulen);
		o->ttl = 64;
		o->protocol = IPPROTO_UDP;
		o->check = htons(ip_fast_csum(ip, 5));
	}
	udp->len = htons(ulen);
	m->header.type = MESSAGE_DATA;
	m->key_idx = p->key_idx;
	m->counter = p->nonce;
	in = (u8 *)(m + 1);
	inner_off = in - frame;
	if (p->inner6) {
		struct ipv6hdr *i6 = (struct ipv6hdr *)in;

		i6->version = 6;
		i6->payload_len = htons(l4 + p->payload);
		i6->nexthdr = p->inner_proto;
		i6->hop_limit = p->ttl;
		i6->saddr.s6_addr[15] = 1;
		i6->daddr.s6_addr[15] = 2;
	} else if (!p->keepalive) {
		struct iphdr *i4 = (struct iphdr *)in;

		i4->version = p->bogus_inner ? 5 : 4;
		i4->ihl = 5;
		i4->tot_len = htons(inner_len);
		i4->ttl = p->ttl;
		i4->protocol = p->inner_proto;
		i4->saddr = htonl(0x0a636e01);
		i4->daddr = htonl(0x0a636f02);
		i4->check = htons(ip_fast_csum(in, 5));
	}
	if (!p->keepalive) {
		in[ihl] = 0x13;
		in[ihl + 1] = 0x88;	/* sport 5000 */
		in[ihl + 2] = 0x14;
		in[ihl + 3] = 0x51;	/* dport 5201 */
		if (p->inner_proto == IPPROTO_TCP)
			in[ihl + 13] = p->tcp_flags;
		for (u32 i = 0; i < p->payload; i++)
			in[ihl + l4 + i] = (u8)i;
	}
	for (u32 i = 0; i < plain; i++)
		in[i] ^= 0x5a;
	memset(in + plain, p->bad_tag ? 0x55 : 0xaa, 16);
	return (struct dpaa_wgfp_frame){ .buf = frame, .off = off,
		.len = ETH_HLEN + ohl + ulen, .buf_size = sizeof(frame) };
}

static struct net_device port = { .flags = IFF_UP, .ifindex = 6,
				  .dev_addr = { 2, 0, 0, 0, 0, 0x11 } };
static struct net_device wg0 = { .flags = IFF_UP, .ifindex = 9 };
static struct wg_device wgd;
static struct noise_keypair kp = { { 7 }, { true, 0, { 0 } }, { 0 } };
static struct wg_peer peer = { &wgd, { &kp }, false, 42 };
static struct wgfp_pst pst = { &peer, &wg0, 0 };
static struct wgfp_rflow rec;

static void learn(const struct pkt *p)
{
	struct dpaa_wgfp_frame f = build(p);
	u32 tlen;

	/* the record the POST_ROUTING learner would make for this flow */
	memset(&rec, 0, sizeof(rec));
	for (u32 i = 0; i < inner_len; i++)
		frame[inner_off + i] ^= 0x5a;
	assert(wgfp_parse(frame + inner_off, p->inner6 ? htons(ETH_P_IPV6) : htons(ETH_P_IP),
			  inner_len, inner_len, &rec.t, &tlen));
	(void)f;
	rec.wg_ifindex = wg0.ifindex;
	rec.peer_id = peer.internal_id;
	rec.mtu = 1420;
	rec.expires = jiffies + HZ;
	rec.l2.egress = 3;
	memcpy(rec.l2.hdr, out_l2, ETH_HLEN);
	rflow = &rec;
}

static void reset(void)
{
	drops = xmits = delivered = timers = 0;
	xmit_off = xmit_len = delivered_len = 0;
	stats = 0;
	ecn_outer = -1;
	wgfp_p6_fwd = wgfp_p6_stack = wgfp_p6_replay = wgfp_p6_auth = 0;
	wgfp_p6_err = wgfp_p6_aip = wgfp_p6_nomem = 0;
	pst.last_rx_timer = 0;
	aip_peer = &peer;
}

#define CHECK(c) do { if (!(c)) { fprintf(stderr, "%s:%d: %s\n", __func__, __LINE__, #c); exit(1); } } while (0)

static int run(const struct pkt *p, struct dpaa_wgfp_frame *f)
{
	struct wgfp_flow fl = { .pst = &pst };

	fl.t.family = p->outer6 ? AF_INET6 : AF_INET;
	*f = build(p);
	reset();
	return wgfp_rx_fast_frame(f, &fl, &port);
}

static void test_forward_v4(void)
{
	struct pkt p = { .inner_proto = IPPROTO_TCP, .tcp_flags = 0x10, .ttl = 64,
			 .payload = 100, .pad = 4, .nonce = 1, .key_idx = 7 };
	struct dpaa_wgfp_frame f;
	struct iphdr *i4;

	learn(&p);
	CHECK(run(&p, &f) == 1);
	CHECK(xmits == 1 && drops == 0 && delivered == 0 && wgfp_p6_fwd == 1);
	CHECK(xmit_egress == 3);
	CHECK(xmit_off == inner_off - ETH_HLEN);
	CHECK(xmit_len == ETH_HLEN + inner_len);		/* padding dropped */
	CHECK(!memcmp(frame + xmit_off, out_l2, ETH_HLEN));
	i4 = (struct iphdr *)(frame + inner_off);
	CHECK(i4->ttl == 63 && ip_fast_csum((u8 *)i4, 5) == 0);
	CHECK(frame[inner_off + 20 + 20] == 0 && frame[inner_off + 20 + 21] == 1);	/* payload decrypted */
	CHECK(stats == (size_t)ntohs(((struct udphdr *)(frame + 64 + ETH_HLEN + 20))->len) - 8);
	CHECK(timers == 1);

	/* the same nonce again: authenticated, then refused by the window */
	CHECK(run(&p, &f) == 1);
	CHECK(drops == 1 && xmits == 0 && wgfp_p6_replay == 1);
}

static void test_forward_v6_over_v6(void)
{
	struct pkt p = { .outer6 = 1, .inner6 = 1, .inner_proto = IPPROTO_UDP, .ttl = 9,
			 .payload = 64, .nonce = 2, .key_idx = 7 };
	struct dpaa_wgfp_frame f;

	learn(&p);
	CHECK(run(&p, &f) == 1);
	CHECK(xmits == 1 && xmit_len == ETH_HLEN + inner_len);
	CHECK(((struct ipv6hdr *)(frame + inner_off))->hop_limit == 8);
}

static void test_stack_handoffs(void)
{
	struct pkt base = { .inner_proto = IPPROTO_TCP, .tcp_flags = 0x10, .ttl = 64,
			    .payload = 50, .pad = 2, .key_idx = 7 };
	struct dpaa_wgfp_frame f;
	struct pkt p;

	/* no record */
	rflow = NULL;
	p = base; p.nonce = 10;
	CHECK(run(&p, &f) == 1 && delivered == 1 && xmits == 0 && wgfp_p6_stack == 1);
	CHECK(delivered_len == inner_len && last_skb->protocol == htons(ETH_P_IP));
	CHECK(last_skb->ip_summed == CHECKSUM_UNNECESSARY);
	CHECK(((struct iphdr *)last_skb->data)->ttl == 64);	/* the stack decrements */

	/* TTL 1, SYN, FIN, outer CE, oversize, foreign peer, expired record */
	learn(&base);
	p = base; p.nonce = 11; p.ttl = 1;
	CHECK(run(&p, &f) == 1 && delivered == 1 && xmits == 0);
	p = base; p.nonce = 12; p.tcp_flags = 0x02;
	CHECK(run(&p, &f) == 1 && delivered == 1 && xmits == 0);
	p = base; p.nonce = 13; p.tcp_flags = 0x11;
	CHECK(run(&p, &f) == 1 && delivered == 1 && xmits == 0);
	p = base; p.nonce = 14; p.outer_tos = 3;
	CHECK(run(&p, &f) == 1 && delivered == 1 && xmits == 0 && ecn_outer == 3);
	p = base; p.nonce = 15; rec.mtu = inner_len - 1;
	CHECK(run(&p, &f) == 1 && delivered == 1 && xmits == 0);
	rec.mtu = 1420;
	p = base; p.nonce = 16; rec.peer_id = 41;
	CHECK(run(&p, &f) == 1 && delivered == 1 && xmits == 0);
	rec.peer_id = peer.internal_id;
	p = base; p.nonce = 17; rec.expires = jiffies - 1;
	CHECK(run(&p, &f) == 1 && delivered == 1 && xmits == 0);
	rec.expires = jiffies + HZ;
	p = base; p.nonce = 18; wgfp_rx_fast = 1;
	CHECK(run(&p, &f) == 1 && delivered == 1 && xmits == 0);
	wgfp_rx_fast = 2;

	/* AllowedIPs on the stack hand-off */
	rflow = NULL;
	p = base; p.nonce = 19;
	f = build(&p);
	reset();
	aip_peer = NULL;
	{
		struct wgfp_flow fl = { .t.family = AF_INET, .pst = &pst };

		CHECK(wgfp_rx_fast_frame(&f, &fl, &port) == 1);
	}
	CHECK(delivered == 0 && wgfp_p6_aip == 1 && wg0.rx_errors == 1);
}

static void test_left_to_p3(void)
{
	struct pkt base = { .inner_proto = IPPROTO_UDP, .ttl = 64, .payload = 8,
			    .nonce = 30, .key_idx = 7 };
	struct dpaa_wgfp_frame f;
	u8 before[sizeof(frame)];
	struct pkt p;

#define UNTOUCHED(mod) do { p = base; mod; f = build(&p); memcpy(before, frame, sizeof(frame)); \
	reset(); { struct wgfp_flow fl = { .t.family = p.outer6 ? AF_INET6 : AF_INET, .pst = &pst }; \
	CHECK(wgfp_rx_fast_frame(&f, &fl, &port) == 0); } \
	CHECK(!memcmp(before, frame, sizeof(frame)) && drops == 0 && xmits == 0 && delivered == 0); } while (0)

	UNTOUCHED(p.keepalive = true);
	UNTOUCHED(p.key_idx = 8);			/* previous / next keypair */
	UNTOUCHED(p.wrong_mac = true);
	UNTOUCHED((p.outer6 = 1, p.zero_udp6_csum = true));
	kp.receiving.is_valid = false;
	UNTOUCHED((void)0);
	kp.receiving.is_valid = true;
	kp.receiving.birthdate = 1;			/* expired */
	UNTOUCHED((void)0);
	kp.receiving.birthdate = 0;
	wg0.flags = 0;
	UNTOUCHED((void)0);
	wg0.flags = IFF_UP;

	/* a bad tag: dropped, buffer as received */
	p = base; p.nonce = 31; p.bad_tag = true;
	f = build(&p);
	memcpy(before, frame, sizeof(frame));
	reset();
	{
		struct wgfp_flow fl = { .t.family = AF_INET, .pst = &pst };

		CHECK(wgfp_rx_fast_frame(&f, &fl, &port) == 1);
	}
	CHECK(drops == 1 && wgfp_p6_auth == 1 && !memcmp(before, frame, sizeof(frame)));
	CHECK(!seen[31]);				/* the window did not move */

	/* garbage that authenticates: dropped as a frame error */
	p = base; p.nonce = 32; p.bogus_inner = true;
	CHECK(run(&p, &f) == 1 && drops == 1 && wgfp_p6_err == 1);
}

static void test_tail(void)
{
	struct net_device eth3 = { .flags = IFF_UP, .ifindex = 5, .real_num_tx_queues = 8 };
	struct pkt p = { .inner_proto = IPPROTO_TCP, .tcp_flags = 0x10, .ttl = 64,
			 .payload = 100, .key_idx = 7 };
	struct sk_buff skb;
	struct iphdr *i4;

	eth3.ifindex = 5;
	wgd.dev = &wg0;
	tail_eg = &eth3;
	learn(&p);			/* the frame now holds the inner packet in clear */
	rec.l2.ifindex = 5;
#define FRESH() do { memset(&skb, 0, sizeof(skb)); skb.data = skb.storage + 128; \
	memcpy(skb.data, frame + inner_off, inner_len); skb.len = inner_len; \
	skb.protocol = htons(ETH_P_IP); direct_xmits = 0; } while (0)
	FRESH();
	CHECK(wg_fastpath_rx_tail(&peer, &skb));
	CHECK(direct_xmits == 1 && direct_dev == &eth3 && direct_len == ETH_HLEN + inner_len);
	CHECK(!memcmp(direct_hdr, out_l2, ETH_HLEN) && skb.mac_len == ETH_HLEN);
	CHECK(skb.ip_summed == CHECKSUM_NONE && direct_q < 8);
	i4 = (struct iphdr *)(skb.data + ETH_HLEN);
	CHECK(i4->ttl == 63 && ip_fast_csum((u8 *)i4, 5) == 0);

	/* each refusal leaves the skb exactly as it was */
#define REFUSED(setup, undo) do { FRESH(); setup; u8 copy[2048]; memcpy(copy, skb.storage, sizeof(copy)); \
	CHECK(!wg_fastpath_rx_tail(&peer, &skb) && direct_xmits == 0); \
	CHECK(!memcmp(copy, skb.storage, sizeof(copy)) && skb.data == skb.storage + 128); undo; } while (0)
	REFUSED(wgfp_rx_tail = false, wgfp_rx_tail = true);
	REFUSED(rec.expires = jiffies - 1, rec.expires = jiffies + HZ);
	REFUSED(rec.peer_id = 1, rec.peer_id = peer.internal_id);
	REFUSED(rec.mtu = inner_len - 1, rec.mtu = 1420);
	REFUSED(eth3.flags = 0, eth3.flags = IFF_UP);
	REFUSED(rec.wg_ifindex = 77, rec.wg_ifindex = wg0.ifindex);
	REFUSED((skb.data[8] = 1, skb.data[10] = 0, skb.data[11] = 0, ((struct iphdr *)skb.data)->check = htons(ip_fast_csum(skb.data, 5))), (void)0);
	REFUSED(skb.data[20 + 13] = 0x02, (void)0);	/* SYN */
	REFUSED(skb.data[20 + 13] = 0x11, (void)0);	/* FIN */
	REFUSED(skb.data[1] ^= 0x04, (void)0);		/* header checksum broken (tos) */
	REFUSED(skb.len = inner_len + 4, (void)0);	/* trailing bytes: not trimmed */

	/* a failed transmit still consumed the skb and is counted */
	FRESH();
	direct_ret = 1;
	CHECK(wg_fastpath_rx_tail(&peer, &skb) && wgfp_p7_drop == 1);
	direct_ret = 0;
}

int main(void)
{
	test_forward_v4();
	test_forward_v6_over_v6();
	test_stack_handoffs();
	test_left_to_p3();
	test_tail();
	free(last_skb);
	puts("wgfp_p6: ok");
	return 0;
}
