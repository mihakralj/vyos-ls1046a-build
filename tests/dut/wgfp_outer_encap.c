// SPDX-License-Identifier: GPL-2.0
/* Host ASan/UBSan harness for WGFP Stage A: 50B F-259 key, outer L2/VLAN/PPPoE
 * template builder, and L2/VLAN/PPPoE ingress parser. */
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

#define FMAN_PCD_FE_ROUTED_KEY_SIZE 50
#define VLAN_HLEN 4
#define VLAN_VID_MASK 0x0fff
#define ETH_P_8021Q 0x8100
#define ETH_P_8021AD 0x88A8
#define ETH_P_PPP_SES 0x8864
#define PPP_IP 0x0021
#define PPP_IPV6 0x0057

struct rcu_head { int dummy; };
struct net_device { int ifindex; };

struct pppoe_hdr {
	u8 ver_type;
	u8 code;
	__be16 sid;
	__be16 length;
} __attribute__((packed));

struct wgfp_l2_path {
	int phys_ifindex;
	u8 peer_mac[ETH_ALEN];
	u8 src_mac[ETH_ALEN];
	u16 vlan_vid;
	u16 pppoe_sid;
	bool has_vlan;
	bool has_pppoe;
};

static inline void ether_addr_copy(u8 *dst, const u8 *src)
{
	memcpy(dst, src, ETH_ALEN);
}

static inline __be16 get_unaligned_be16_raw(const void *p)
{
	__be16 v;
	memcpy(&v, p, sizeof(v));
	return v;
}

static inline void put_unaligned_be16(u16 val, void *p)
{
	__be16 be = htons(val);
	memcpy(p, &be, sizeof(be));
}

#define get_unaligned(ptr) ({ \
	__be16 __v; \
	memcpy(&__v, (ptr), sizeof(__v)); \
	__v; \
})

#include "wgfp_outer_encap_body.h"

static void test_wgfp_key_50b(void)
{
	struct dpaa_wgfp_tuple t = { 0 };
	u8 key[FMAN_PCD_FE_ROUTED_KEY_SIZE];

	assert(sizeof(struct dpaa_wgfp_tuple) % sizeof(u32) == 0);

	t.family = AF_INET;
	t.saddr = htonl(0x0a633901); /* 10.99.57.1 */
	t.daddr = htonl(0x0a63390a); /* 10.99.57.10 */
	t.proto = IPPROTO_UDP;
	t.sport = htons(51870);
	t.dport = htons(51870);

	/* Plain untagged */
	wgfp_key(&t, key);
	assert(key[0] == 0x40);
	assert(key[41] == IPPROTO_UDP);
	assert(key[46] == 0 && key[47] == 0 && key[48] == 0 && key[49] == 0);

	/* VLAN 7 + PPPoE SID 0x1234 */
	t.vlan_vid = htons(7);
	t.pppoe_sid = htons(0x1234);
	wgfp_key(&t, key);
	assert(key[46] == 0x00 && key[47] == 0x07);
	assert(key[48] == 0x12 && key[49] == 0x34);

	/* IPv6 + PPPoE only (untagged) */
	memset(&t, 0, sizeof(t));
	t.family = AF_INET6;
	t.saddr6.s6_addr[0] = 0xfd;
	t.daddr6.s6_addr[0] = 0xfe;
	t.proto = IPPROTO_UDP;
	t.sport = htons(51820);
	t.dport = htons(51820);
	t.pppoe_sid = htons(0x000a);
	wgfp_key(&t, key);
	assert(key[0] == 0x60);
	assert(key[9] == 0xfd && key[25] == 0xfe);
	assert(key[46] == 0x00 && key[47] == 0x00);
	assert(key[48] == 0x00 && key[49] == 0x0a);
}

static void test_tmpl_and_parse_roundtrip(void)
{
	const u8 dst_mac[ETH_ALEN] = { 0xec, 0x0d, 0x9a, 0xbc, 0x34, 0x90 };
	const u8 src_mac[ETH_ALEN] = { 0xe8, 0xf6, 0xd7, 0x00, 0x16, 0x02 };
	u8 frame[128];
	u32 l3_off, l3_avail;
	__be16 l3_proto, vid, sid;

	/* 1. Plain IPv4 */
	{
		struct wgfp_tmpl t = { 0 };
		struct wgfp_l2_path p = { 0 };

		memcpy(p.src_mac, src_mac, ETH_ALEN);
		wgfp_tmpl_build_l2(&t, &p, dst_mac, htons(ETH_P_IP));
		assert(t.l2_hlen == 14);
		assert(t.pppoe_off == 0);
		memset(frame, 0, sizeof(frame));
		memcpy(frame, t.hdr, t.l2_hlen);
		assert(wgfp_parse_l2(frame, t.l2_hlen + 20, &l3_off, &l3_avail, &l3_proto, &vid, &sid));
		assert(l3_off == 14 && l3_avail == 20 && l3_proto == htons(ETH_P_IP) && vid == 0 && sid == 0);
	}

	/* 2. VLAN 7 IPv6 */
	{
		struct wgfp_tmpl t = { 0 };
		struct wgfp_l2_path p = { .has_vlan = true, .vlan_vid = 7 };

		memcpy(p.src_mac, src_mac, ETH_ALEN);
		wgfp_tmpl_build_l2(&t, &p, dst_mac, htons(ETH_P_IPV6));
		assert(t.l2_hlen == 18);
		assert(t.pppoe_off == 0);
		assert(frame[0] != 0xff);
		memset(frame, 0, sizeof(frame));
		memcpy(frame, t.hdr, t.l2_hlen);
		assert(wgfp_parse_l2(frame, t.l2_hlen + 40, &l3_off, &l3_avail, &l3_proto, &vid, &sid));
		assert(l3_off == 18 && l3_avail == 40 && l3_proto == htons(ETH_P_IPV6) && vid == htons(7) && sid == 0);
	}

	/* 3. PPPoE untagged IPv4 (including Ethernet trailing padding capped by PPPoE plen - 2) */
	{
		struct wgfp_tmpl t = { 0 };
		struct wgfp_l2_path p = { .has_pppoe = true, .pppoe_sid = 0x0042 };

		memcpy(p.peer_mac, dst_mac, ETH_ALEN);
		memcpy(p.src_mac, src_mac, ETH_ALEN);
		wgfp_tmpl_build_l2(&t, &p, p.peer_mac, htons(ETH_P_IP));
		assert(t.l2_hlen == 22);
		assert(t.pppoe_off == 14);
		memset(frame, 0, sizeof(frame));
		memcpy(frame, t.hdr, t.l2_hlen);
		put_unaligned_be16(2 + 40, frame + t.pppoe_off + 4);
		assert(wgfp_parse_l2(frame, t.l2_hlen + 48, &l3_off, &l3_avail, &l3_proto, &vid, &sid));
		assert(l3_off == 22 && l3_avail == 40 && l3_proto == htons(ETH_P_IP) && vid == 0 && sid == htons(0x0042));
	}

	/* 4. PPPoE SID 0x000a over VLAN 7 IPv4 + IPv6 */
	for (int v6 = 0; v6 <= 1; v6++) {
		struct wgfp_tmpl t = { 0 };
		struct wgfp_l2_path p = {
			.has_vlan = true, .vlan_vid = 7,
			.has_pppoe = true, .pppoe_sid = 0x000a,
		};
		__be16 want_proto = v6 ? htons(ETH_P_IPV6) : htons(ETH_P_IP);

		memcpy(p.peer_mac, dst_mac, ETH_ALEN);
		memcpy(p.src_mac, src_mac, ETH_ALEN);
		wgfp_tmpl_build_l2(&t, &p, p.peer_mac, want_proto);
		assert(t.l2_hlen == 26);
		assert(t.pppoe_off == 18);
		memset(frame, 0, sizeof(frame));
		memcpy(frame, t.hdr, t.l2_hlen);
		put_unaligned_be16(2 + 48, frame + t.pppoe_off + 4);
		assert(wgfp_parse_l2(frame, t.l2_hlen + 48, &l3_off, &l3_avail, &l3_proto, &vid, &sid));
		assert(l3_off == 26 && l3_avail == 48 && l3_proto == want_proto &&
		       vid == htons(7) && sid == htons(0x000a));

		/* Reject truncated PPPoE length */
		assert(!wgfp_parse_l2(frame, t.l2_hlen + 40, &l3_off, &l3_avail, &l3_proto, &vid, &sid));
		/* Reject bad PPPoE code */
		frame[t.pppoe_off + 1] = 0xa7; /* PADT */
		assert(!wgfp_parse_l2(frame, t.l2_hlen + 48, &l3_off, &l3_avail, &l3_proto, &vid, &sid));
		frame[t.pppoe_off + 1] = 0x00;
		/* Reject non-IP PPP protocol (LCP 0xc021) */
		put_unaligned_be16(0xc021, frame + t.pppoe_off + 6);
		assert(!wgfp_parse_l2(frame, t.l2_hlen + 48, &l3_off, &l3_avail, &l3_proto, &vid, &sid));
		/* Reject SID 0 */
		put_unaligned_be16(v6 ? PPP_IPV6 : PPP_IP, frame + t.pppoe_off + 6);
		put_unaligned_be16(0, frame + t.pppoe_off + 2);
		assert(!wgfp_parse_l2(frame, t.l2_hlen + 48, &l3_off, &l3_avail, &l3_proto, &vid, &sid));
		/* Reject VID 0 */
		put_unaligned_be16(0x000a, frame + t.pppoe_off + 2);
		put_unaligned_be16(0, frame + 14);
		assert(!wgfp_parse_l2(frame, t.l2_hlen + 48, &l3_off, &l3_avail, &l3_proto, &vid, &sid));
	}

	/* 5. Reject QinQ (802.1AD and double 802.1Q) and runt frames */
	memset(frame, 0, sizeof(frame));
	put_unaligned_be16(ETH_P_8021AD, frame + 12);
	assert(!wgfp_parse_l2(frame, 64, &l3_off, &l3_avail, &l3_proto, &vid, &sid));

	put_unaligned_be16(ETH_P_8021Q, frame + 12);
	put_unaligned_be16(7, frame + 14);
	put_unaligned_be16(ETH_P_8021Q, frame + 16);
	put_unaligned_be16(8, frame + 18);
	put_unaligned_be16(ETH_P_IP, frame + 20);
	assert(!wgfp_parse_l2(frame, 64, &l3_off, &l3_avail, &l3_proto, &vid, &sid));

	assert(!wgfp_parse_l2(frame, 13, &l3_off, &l3_avail, &l3_proto, &vid, &sid));
}

int main(void)
{
	test_wgfp_key_50b();
	test_tmpl_and_parse_roundtrip();
	printf("wgfp_outer_encap: all checks passed\n");
	return 0;
}
