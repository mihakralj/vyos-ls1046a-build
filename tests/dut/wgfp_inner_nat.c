// SPDX-License-Identifier: GPL-2.0
/* Host ASan/UBSan verification of fastpath.c's inline NAT44/NAT66 rewrite and
 * incremental RFC 1624 IPv4/TCP/UDP checksum updates against from-scratch
 * RFC 1071 reference checksums. */
#include <arpa/inet.h>
#include <assert.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/tcp.h>
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
#define likely(x) (x)

#include "wgfp_inner_nat_body.h"

/* Reference 1's-complement checksum over wire bytes (returns wire-order u16). */
static u16 ref_csum_add(u32 sum, const u8 *buf, size_t len)
{
	while (len >= 2) {
		u16 w;
		memcpy(&w, buf, 2);
		sum += w;
		buf += 2;
		len -= 2;
	}
	if (len) {
		u16 w = 0;
		memcpy(&w, buf, 1);
		sum += w;
	}
	while (sum >> 16)
		sum = (sum & 0xffff) + (sum >> 16);
	return (u16)(~sum & 0xffff);
}

static u32 ref_csum_partial(const u8 *buf, size_t len, u32 sum)
{
	while (len >= 2) {
		u16 w;
		memcpy(&w, buf, 2);
		sum += w;
		buf += 2;
		len -= 2;
	}
	if (len) {
		u16 w = 0;
		memcpy(&w, buf, 1);
		sum += w;
	}
	return sum;
}

static u16 ref_ip4_csum(const struct iphdr *iph)
{
	struct iphdr tmp = *iph;

	tmp.check = 0;
	return ref_csum_add(0, (const u8 *)&tmp, sizeof(tmp));
}

static u16 ref_l4_v4_csum(const u8 *pkt, u32 ip_len, u8 proto)
{
	const struct iphdr *iph = (const struct iphdr *)pkt;
	u16 l4_len = ip_len - sizeof(*iph);
	u8 pseudo[12];
	u8 l4copy[256];
	u32 sum;
	u16 res;

	assert(l4_len <= sizeof(l4copy));
	memcpy(l4copy, pkt + sizeof(*iph), l4_len);
	if (proto == IPPROTO_TCP)
		memset(l4copy + 16, 0, 2);
	else
		memset(l4copy + 6, 0, 2);

	memcpy(pseudo, &iph->saddr, 4);
	memcpy(pseudo + 4, &iph->daddr, 4);
	pseudo[8] = 0;
	pseudo[9] = proto;
	u16 be_len = htons(l4_len);
	memcpy(pseudo + 10, &be_len, 2);

	sum = ref_csum_partial(pseudo, sizeof(pseudo), 0);
	res = ref_csum_add(sum, l4copy, l4_len);
	if (proto == IPPROTO_UDP && res == 0)
		res = 0xffff;
	return res;
}

static u16 ref_l4_v6_csum(const u8 *pkt, u32 ip_len, u8 proto)
{
	const struct ipv6hdr *ip6 = (const struct ipv6hdr *)pkt;
	u32 l4_len = ip_len - sizeof(*ip6);
	u8 pseudo[40];
	u8 l4copy[256];
	u32 sum;
	u16 res;

	assert(l4_len <= sizeof(l4copy));
	memcpy(l4copy, pkt + sizeof(*ip6), l4_len);
	if (proto == IPPROTO_TCP)
		memset(l4copy + 16, 0, 2);
	else
		memset(l4copy + 6, 0, 2);

	memset(pseudo, 0, sizeof(pseudo));
	memcpy(pseudo, &ip6->saddr, 16);
	memcpy(pseudo + 16, &ip6->daddr, 16);
	u32 be_len = htonl(l4_len);
	memcpy(pseudo + 32, &be_len, 4);
	pseudo[39] = proto;

	sum = ref_csum_partial(pseudo, sizeof(pseudo), 0);
	res = ref_csum_add(sum, l4copy, l4_len);
	if (proto == IPPROTO_UDP && res == 0)
		res = 0xffff;
	return res;
}

static void check_v4_case(u8 proto, u8 flags, bool udp_zero_csum, u16 payload_seed)
{
	u8 pkt[128];
	u32 l4_hdr = proto == IPPROTO_TCP ? sizeof(struct tcphdr) : sizeof(struct udphdr);
	u32 ip_len = sizeof(struct iphdr) + l4_hdr + 37;
	struct iphdr *iph = (struct iphdr *)pkt;
	struct wgfp_nat nat = { 0 };

	memset(pkt, 0, sizeof(pkt));
	iph->version = 4;
	iph->ihl = 5;
	iph->tot_len = htons(ip_len);
	iph->ttl = 64;
	iph->protocol = proto;
	iph->saddr = htonl(0x0a630271); /* 10.99.2.113 */
	iph->daddr = htonl(0x0a63e670); /* 10.99.230.112 */
	for (u32 i = sizeof(*iph) + l4_hdr; i < ip_len; i++)
		pkt[i] = (u8)(i * 31 + payload_seed);

	if (proto == IPPROTO_TCP) {
		struct tcphdr *th = (struct tcphdr *)(pkt + sizeof(*iph));
		th->source = htons(50000 + payload_seed);
		th->dest = htons(5201);
		th->doff = 5;
		th->ack = 1;
		th->check = ref_l4_v4_csum(pkt, ip_len, proto);
	} else {
		struct udphdr *uh = (struct udphdr *)(pkt + sizeof(*iph));
		uh->source = htons(40000 + payload_seed);
		uh->dest = htons(5201);
		uh->len = htons(ip_len - sizeof(*iph));
		uh->check = udp_zero_csum ? 0 : ref_l4_v4_csum(pkt, ip_len, proto);
	}
	iph->check = ref_ip4_csum(iph);

	nat.flags = flags;
	nat.saddr = htonl(0x0a63e601); /* 10.99.230.1 */
	nat.daddr = htonl(0x0a630102); /* 10.99.1.2 */
	nat.sport = htons(41234);
	nat.dport = htons(8443);

	assert(wgfp_nat_len_ok(ip_len, AF_INET, proto, &nat));
	wgfp_apply_nat(pkt, ip_len, AF_INET, proto, &nat);

	if (flags & WGFP_NAT_SRC_IP)
		assert(iph->saddr == nat.saddr);
	if (flags & WGFP_NAT_DST_IP)
		assert(iph->daddr == nat.daddr);
	assert(iph->check == ref_ip4_csum(iph));

	if (proto == IPPROTO_TCP) {
		struct tcphdr *th = (struct tcphdr *)(pkt + sizeof(*iph));
		if (flags & WGFP_NAT_SRC_PORT)
			assert(th->source == nat.sport);
		if (flags & WGFP_NAT_DST_PORT)
			assert(th->dest == nat.dport);
		/* 0x0000 and 0xffff are both valid 0 in 1's-complement; verify
		 * full verification sums to 0xffff (complement 0). */
		assert(ref_l4_v4_csum(pkt, ip_len, proto) == th->check ||
		       (ref_l4_v4_csum(pkt, ip_len, proto) == 0 && th->check == 0xffff) ||
		       (ref_l4_v4_csum(pkt, ip_len, proto) == 0xffff && th->check == 0));
	} else {
		struct udphdr *uh = (struct udphdr *)(pkt + sizeof(*iph));
		if (flags & WGFP_NAT_SRC_PORT)
			assert(uh->source == nat.sport);
		if (flags & WGFP_NAT_DST_PORT)
			assert(uh->dest == nat.dport);
		if (udp_zero_csum)
			assert(uh->check == 0);
		else
			assert(uh->check == ref_l4_v4_csum(pkt, ip_len, proto));
	}
}

static void check_v6_case(u8 proto, u8 flags, bool udp_zero_csum, u16 payload_seed)
{
	u8 pkt[160];
	u32 l4_hdr = proto == IPPROTO_TCP ? sizeof(struct tcphdr) : sizeof(struct udphdr);
	u32 ip_len = sizeof(struct ipv6hdr) + l4_hdr + 35;
	struct ipv6hdr *ip6 = (struct ipv6hdr *)pkt;
	struct wgfp_nat nat = { 0 };

	memset(pkt, 0, sizeof(pkt));
	ip6->version = 6;
	ip6->payload_len = htons(ip_len - sizeof(*ip6));
	ip6->nexthdr = proto;
	ip6->hop_limit = 64;
	inet_pton(AF_INET6, "fd99:2::113", &ip6->saddr);
	inet_pton(AF_INET6, "fd99:230::112", &ip6->daddr);
	for (u32 i = sizeof(*ip6) + l4_hdr; i < ip_len; i++)
		pkt[i] = (u8)(i * 19 + payload_seed);

	if (proto == IPPROTO_TCP) {
		struct tcphdr *th = (struct tcphdr *)(pkt + sizeof(*ip6));
		th->source = htons(51000 + payload_seed);
		th->dest = htons(5201);
		th->doff = 5;
		th->check = ref_l4_v6_csum(pkt, ip_len, proto);
	} else {
		struct udphdr *uh = (struct udphdr *)(pkt + sizeof(*ip6));
		uh->source = htons(41000 + payload_seed);
		uh->dest = htons(5201);
		uh->len = htons(ip_len - sizeof(*ip6));
		uh->check = udp_zero_csum ? 0 : ref_l4_v6_csum(pkt, ip_len, proto);
	}

	nat.flags = flags;
	inet_pton(AF_INET6, "fd99:230::1", &nat.saddr6);
	inet_pton(AF_INET6, "fd99:99::42", &nat.daddr6);
	nat.sport = htons(33333);
	nat.dport = htons(9443);

	assert(wgfp_nat_len_ok(ip_len, AF_INET6, proto, &nat));
	wgfp_apply_nat(pkt, ip_len, AF_INET6, proto, &nat);

	if (flags & WGFP_NAT_SRC_IP)
		assert(!memcmp(&ip6->saddr, &nat.saddr6, 16));
	if (flags & WGFP_NAT_DST_IP)
		assert(!memcmp(&ip6->daddr, &nat.daddr6, 16));

	if (proto == IPPROTO_TCP) {
		struct tcphdr *th = (struct tcphdr *)(pkt + sizeof(*ip6));
		assert(ref_l4_v6_csum(pkt, ip_len, proto) == th->check ||
		       (ref_l4_v6_csum(pkt, ip_len, proto) == 0 && th->check == 0xffff) ||
		       (ref_l4_v6_csum(pkt, ip_len, proto) == 0xffff && th->check == 0));
	} else {
		struct udphdr *uh = (struct udphdr *)(pkt + sizeof(*ip6));
		if (udp_zero_csum) {
			assert(uh->check == 0);
		} else {
			assert(uh->check != 0);
			assert(uh->check == ref_l4_v6_csum(pkt, ip_len, proto));
		}
	}
}

/* Force a UDP NAT transition whose exact 1's-complement checksum hits 0x0000,
 * proving CSUM_MANGLED_0 (0xffff) is emitted instead of 0x0000. */
static void check_udp_zero_mangle(void)
{
	u8 pkt[64];
	u32 ip_len = sizeof(struct iphdr) + sizeof(struct udphdr) + 4;
	struct iphdr *iph = (struct iphdr *)pkt;
	struct udphdr *uh = (struct udphdr *)(pkt + sizeof(*iph));
	struct wgfp_nat nat = { .flags = WGFP_NAT_SRC_PORT };

	memset(pkt, 0, sizeof(pkt));
	iph->version = 4;
	iph->ihl = 5;
	iph->tot_len = htons(ip_len);
	iph->ttl = 64;
	iph->protocol = IPPROTO_UDP;
	iph->saddr = htonl(0x0a000001);
	iph->daddr = htonl(0x0a000002);
	uh->source = htons(1000);
	uh->dest = htons(2000);
	uh->len = htons(sizeof(*uh) + 4);
	uh->check = ref_l4_v4_csum(pkt, ip_len, IPPROTO_UDP);
	iph->check = ref_ip4_csum(iph);

	/* Choose new sport so new L4 checksum would be 0x0000 -> must become 0xffff. */
	u16 cur = ntohs(uh->check);
	u16 target_sport = (u16)(1000 + cur);
	if (target_sport == 1000)
		target_sport++;
	nat.sport = htons(target_sport);

	/* Brute-search around target_sport to guarantee we hit the exact 0xffff case. */
	bool hit_ffff = false;
	for (u32 delta = 0; delta < 4; delta++) {
		u8 copy[64];
		memcpy(copy, pkt, sizeof(pkt));
		nat.sport = htons((u16)(target_sport + delta - 1));
		wgfp_apply_nat(copy, ip_len, AF_INET, IPPROTO_UDP, &nat);
		struct udphdr *uh2 = (struct udphdr *)(copy + sizeof(*iph));
		assert(uh2->check == ref_l4_v4_csum(copy, ip_len, IPPROTO_UDP));
		if (uh2->check == 0xffff)
			hit_ffff = true;
	}
	assert(hit_ffff);
}

static void check_bounds(void)
{
	struct wgfp_nat nat = { .flags = WGFP_NAT_SRC_IP };

	assert(!wgfp_nat_len_ok(39, AF_INET, IPPROTO_TCP, &nat));
	assert(wgfp_nat_len_ok(40, AF_INET, IPPROTO_TCP, &nat));
	assert(!wgfp_nat_len_ok(27, AF_INET, IPPROTO_UDP, &nat));
	assert(wgfp_nat_len_ok(28, AF_INET, IPPROTO_UDP, &nat));
	assert(!wgfp_nat_len_ok(59, AF_INET6, IPPROTO_TCP, &nat));
	assert(wgfp_nat_len_ok(60, AF_INET6, IPPROTO_TCP, &nat));
	assert(!wgfp_nat_len_ok(47, AF_INET6, IPPROTO_UDP, &nat));
	assert(wgfp_nat_len_ok(48, AF_INET6, IPPROTO_UDP, &nat));

	nat.flags = 0;
	assert(wgfp_nat_len_ok(24, AF_INET, IPPROTO_TCP, &nat));
}

int main(void)
{
	const u8 combos[] = {
		WGFP_NAT_SRC_IP,
		WGFP_NAT_DST_IP,
		WGFP_NAT_SRC_IP | WGFP_NAT_SRC_PORT,
		WGFP_NAT_DST_IP | WGFP_NAT_DST_PORT,
		WGFP_NAT_SRC_IP | WGFP_NAT_DST_IP | WGFP_NAT_SRC_PORT | WGFP_NAT_DST_PORT,
	};

	for (size_t i = 0; i < sizeof(combos); i++) {
		for (u16 seed = 0; seed < 16; seed++) {
			check_v4_case(IPPROTO_TCP, combos[i], false, seed);
			check_v4_case(IPPROTO_UDP, combos[i], false, seed);
			check_v4_case(IPPROTO_UDP, combos[i], true, seed);
			check_v6_case(IPPROTO_TCP, combos[i], false, seed);
			check_v6_case(IPPROTO_UDP, combos[i], false, seed);
			check_v6_case(IPPROTO_UDP, combos[i], true, seed);
		}
	}
	check_udp_zero_mangle();
	check_bounds();
	printf("wgfp_inner_nat: all checks passed\n");
	return 0;
}
