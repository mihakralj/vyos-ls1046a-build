// SPDX-License-Identifier: GPL-2.0
/*
 * WireGuard fast path for DPAA1 (plans/ASK2-WIREGUARD-FASTPATH-PLAN.md).
 *
 * Phase P2a, "lab seal": no peer, no handshake.  Frames that FMan flow
 * records steer to the dpaa_wgfp queues (drivers/net/ethernet/freescale/dpaa)
 * are sealed in place with a fixed key into WireGuard transport-data packets
 * and transmitted on a chosen port, never building an sk_buff.  It measures
 * the real cost of the transmit fast path (crypto, header build, enqueue).
 * The key is a lab value, the counter is a plain global, and nothing here
 * runs unless root writes a configuration to the "wgfp_lab" parameter, so it
 * is NOT a usable VPN; P2b replaces the fixed state with a real peer keypair.
 *
 *   echo "<egress> <dst mac> <src mac> <src ip> <dst ip> <sport> <dport> \
 *         <receiver index> <32-byte key as 64 hex digits>" \
 *        > /sys/module/wireguard/parameters/wgfp_lab
 *   echo off > /sys/module/wireguard/parameters/wgfp_lab
 *
 * Input frames must be Ethernet + IP carrying TCP or UDP; others are
 * declined (the DPAA sink just releases them).  Lab mode is IPv4-only.
 */

#include <crypto/chacha.h>
#include <crypto/chacha20poly1305.h>
#include <crypto/poly1305.h>
#include <crypto/utils.h>
#include <linux/atomic.h>
#include <linux/bitops.h>
#include <linux/etherdevice.h>
#include <linux/fsl/dpaa_wgfp.h>
#include <linux/hashtable.h>
#include <linux/hex.h>
#include <linux/if_arp.h>
#include <linux/if_ether.h>
#include <linux/if_pppox.h>
#include <linux/if_vlan.h>
#include <linux/inet.h>
#include <linux/jhash.h>
#include <linux/ppp_defs.h>
#include <linux/tcp.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/module.h>
#include <linux/moduleparam.h>
#include <linux/netdevice.h>
#include <linux/rtnetlink.h>
#include <linux/udp.h>
#include <linux/unaligned.h>
#include <net/checksum.h>
#include <net/ip.h>
#include <net/ipv6.h>

#include <linux/inetdevice.h>
#include <linux/jiffies.h>
#include <linux/workqueue.h>
#include <linux/netfilter.h>
#include <net/arp.h>
#include <net/inet_ecn.h>
#include <net/ip6_route.h>
#include <net/neighbour.h>
#include <net/route.h>
#include <net/tcp.h>
/* Built-in WireGuard cannot reach a conntrack module: the admission below
 * would compile to "refuse everything" and the fast path would never engage. */
#if IS_BUILTIN(CONFIG_WIREGUARD) && IS_MODULE(CONFIG_NF_CONNTRACK)
#error "WGFP: built-in WireGuard needs CONFIG_NF_CONNTRACK=y"
#endif
#if IS_REACHABLE(CONFIG_NF_CONNTRACK)
#include <net/netfilter/nf_conntrack.h>
#include <net/netfilter/nf_conntrack_core.h>
#include <net/netfilter/nf_conntrack_helper.h>
#include <net/netfilter/nf_conntrack_zones.h>
#endif

#include "allowedips.h"
#include "device.h"
#include "fastpath.h"
#include "messages.h"
#include "noise.h"
#include "peer.h"
#include "queueing.h"
#include "timers.h"

/* Ethernet + IPv4 + UDP + WireGuard transport header, in front of the data. */
#define WGFP_HDR_LEN	(ETH_HLEN + sizeof(struct iphdr) + sizeof(struct udphdr) + \
			 sizeof(struct message_data))
#define WGFP_ADDED	(WGFP_HDR_LEN - ETH_HLEN)

struct wgfp_lab {
	u8 key[CHACHA20POLY1305_KEY_SIZE];
	u8 tmpl[ETH_HLEN + sizeof(struct iphdr) + sizeof(struct udphdr)];
	__le32 rcv_idx;
	int egress;
	atomic64_t counter;
};

/* Open: the n mod 320 tail, rounded up to whole blocks, that the open fold
 * decrypts on a stack copy together with block 0 (see wgfp_open_folded()). */
#define WGFP_OPEN_HEAD	256

static bool wgfp_open_foldable(size_t n)
{
	size_t r = n % (5 * CHACHA_BLOCK_SIZE);

	return n > CHACHA_BLOCK_SIZE && r && r <= WGFP_OPEN_HEAD;
}

#define WGFP_NAT_SRC_IP		(1U << 0)
#define WGFP_NAT_DST_IP		(1U << 1)
#define WGFP_NAT_SRC_PORT	(1U << 2)
#define WGFP_NAT_DST_PORT	(1U << 3)

struct wgfp_nat {
	u8 flags;		/* WGFP_NAT_*; 0 = no translation */
	u8 _pad[3];
	union { __be32 saddr; struct in6_addr saddr6; };
	union { __be32 daddr; struct in6_addr daddr6; };
	__be16 sport;
	__be16 dport;
};

static inline void wgfp_csum_replace_u16(u8 *csum_p, const void *from_p,
					 const void *to_p, bool mangle_zero)
{
	u16 sum, from, to;
	u32 s;

	memcpy(&sum, csum_p, 2);
	memcpy(&from, from_p, 2);
	memcpy(&to, to_p, 2);
	s = (u32)(~sum & 0xffff) + (u32)(~from & 0xffff) + (u32)to;
	s = (s & 0xffff) + (s >> 16);
	s = (s & 0xffff) + (s >> 16);
	sum = (u16)(~s & 0xffff);
	if (mangle_zero && !sum)
		sum = 0xffff;
	memcpy(csum_p, &sum, 2);
}

static inline void wgfp_csum_replace_u32(u8 *csum_p, const void *from_p,
					 const void *to_p, bool mangle_zero)
{
	u16 sum;
	u32 from, to, s;

	memcpy(&sum, csum_p, 2);
	memcpy(&from, from_p, 4);
	memcpy(&to, to_p, 4);
	s = (u32)(~sum & 0xffff);
	s += ((~from) >> 16) & 0xffff;
	s += (~from) & 0xffff;
	s += (to >> 16) & 0xffff;
	s += to & 0xffff;
	s = (s & 0xffff) + (s >> 16);
	s = (s & 0xffff) + (s >> 16);
	sum = (u16)(~s & 0xffff);
	if (mangle_zero && !sum)
		sum = 0xffff;
	memcpy(csum_p, &sum, 2);
}

static inline void wgfp_csum_replace_u128(u8 *csum_p, const void *from_p,
					  const void *to_p, bool mangle_zero)
{
	const u8 *f = from_p, *t = to_p;
	u16 sum;
	u32 s;
	int i;

	memcpy(&sum, csum_p, 2);
	s = (u32)(~sum & 0xffff);
	for (i = 0; i < 16; i += 4) {
		u32 fw, tw;

		memcpy(&fw, f + i, 4);
		memcpy(&tw, t + i, 4);
		s += ((~fw) >> 16) & 0xffff;
		s += (~fw) & 0xffff;
		s += (tw >> 16) & 0xffff;
		s += tw & 0xffff;
	}
	while (s >> 16)
		s = (s & 0xffff) + (s >> 16);
	sum = (u16)(~s & 0xffff);
	if (mangle_zero && !sum)
		sum = 0xffff;
	memcpy(csum_p, &sum, 2);
}

static inline bool wgfp_nat_len_ok(u32 ip_len, u8 family, u8 proto,
				   const struct wgfp_nat *nat)
{
	u32 hl, l4_min;

	if (!nat || !nat->flags)
		return true;
	hl = family == AF_INET ? sizeof(struct iphdr) : sizeof(struct ipv6hdr);
	if (proto == IPPROTO_TCP)
		l4_min = 20;	/* sizeof(struct tcphdr) */
	else if (proto == IPPROTO_UDP)
		l4_min = 8;	/* sizeof(struct udphdr) */
	else
		return false;
	return ip_len >= hl + l4_min;
}

/* Apply NAT44/NAT66 rewrite in-place to a writable inner IP packet at @l3
 * (caller has already verified wgfp_nat_len_ok()). */
static void wgfp_apply_nat(u8 *l3, u32 ip_len, u8 family, u8 proto,
			   const struct wgfp_nat *nat)
{
	bool is_udp = proto == IPPROTO_UDP;
	u32 hl = family == AF_INET ? sizeof(struct iphdr) : sizeof(struct ipv6hdr);
	u8 *l4 = l3 + hl;
	u8 *l4_csum = l4 + (is_udp ? 6 : 16);
	bool update_l4 = true;

	if (!nat || !nat->flags || !wgfp_nat_len_ok(ip_len, family, proto, nat))
		return;
	if (is_udp) {
		u16 cur;

		memcpy(&cur, l4_csum, 2);
		if (!cur)
			update_l4 = false;	/* RFC 768 / RFC 6935: UDP 0 = no checksum */
	}
	if (family == AF_INET) {
		u8 *ip_csum = l3 + 10;	/* offsetof(struct iphdr, check) */
		u8 *saddr_p = l3 + 12;	/* offsetof(struct iphdr, saddr) */
		u8 *daddr_p = l3 + 16;	/* offsetof(struct iphdr, daddr) */

		if (nat->flags & WGFP_NAT_SRC_IP) {
			wgfp_csum_replace_u32(ip_csum, saddr_p, &nat->saddr, false);
			if (update_l4)
				wgfp_csum_replace_u32(l4_csum, saddr_p, &nat->saddr, is_udp);
			memcpy(saddr_p, &nat->saddr, 4);
		}
		if (nat->flags & WGFP_NAT_DST_IP) {
			wgfp_csum_replace_u32(ip_csum, daddr_p, &nat->daddr, false);
			if (update_l4)
				wgfp_csum_replace_u32(l4_csum, daddr_p, &nat->daddr, is_udp);
			memcpy(daddr_p, &nat->daddr, 4);
		}
	} else {
		u8 *saddr6_p = l3 + 8;	/* offsetof(struct ipv6hdr, saddr) */
		u8 *daddr6_p = l3 + 24;	/* offsetof(struct ipv6hdr, daddr) */

		if (nat->flags & WGFP_NAT_SRC_IP) {
			if (update_l4)
				wgfp_csum_replace_u128(l4_csum, saddr6_p, &nat->saddr6, is_udp);
			memcpy(saddr6_p, &nat->saddr6, 16);
		}
		if (nat->flags & WGFP_NAT_DST_IP) {
			if (update_l4)
				wgfp_csum_replace_u128(l4_csum, daddr6_p, &nat->daddr6, is_udp);
			memcpy(daddr6_p, &nat->daddr6, 16);
		}
	}
	if (nat->flags & WGFP_NAT_SRC_PORT) {
		if (update_l4)
			wgfp_csum_replace_u16(l4_csum, l4, &nat->sport, is_udp);
		memcpy(l4, &nat->sport, 2);
	}
	if (nat->flags & WGFP_NAT_DST_PORT) {
		if (update_l4)
			wgfp_csum_replace_u16(l4_csum, l4 + 2, &nat->dport, is_udp);
		memcpy(l4 + 2, &nat->dport, 2);
	}
}

/*
 * One NEON section a packet (fold mode 2).  The library, and modes 0 and 1,
 * enter kernel_neon_begin()/end() once per ChaCha call and once per Poly1305
 * update: three or four sections a full-size packet, 4.3-4.8% of a packet in
 * the profiles.  Here ChaCha and Poly1305 run in one section, calling the
 * lib/crypto/arm64 routines directly; packets up to wgfp_neon_min bytes
 * (ACKs) open none and run on general registers.  Those routines are global
 * but not exported, so this is built only when WireGuard and both crypto
 * libraries are built in (ASK master c7a6e76 does the same with exports).
 */
#if IS_BUILTIN(CONFIG_WIREGUARD) && IS_BUILTIN(CONFIG_CRYPTO_LIB_CHACHA) && \
    IS_BUILTIN(CONFIG_CRYPTO_LIB_POLY1305) && IS_ENABLED(CONFIG_ARM64) && \
    IS_ENABLED(CONFIG_CRYPTO_LIB_CHACHA_ARCH) && \
    IS_ENABLED(CONFIG_CRYPTO_LIB_POLY1305_ARCH)
#define WGFP_ONE_NEON	1

#include <asm/neon.h>
#include <crypto/internal/simd.h>

static void wgfp_chacha_setup(struct chacha_state *st, u64 nonce,
			      const u8 key[CHACHA20POLY1305_KEY_SIZE])
{
	__le64 iv[2] = { 0, cpu_to_le64(nonce) };
	u32 k[CHACHA_KEY_WORDS];
	int i;

	for (i = 0; i < CHACHA_KEY_WORDS; i++)
		k[i] = get_unaligned_le32(key + 4 * i);
	chacha_init(st, k, (const u8 *)iv);
	memzero_explicit(k, sizeof(k));
}

asmlinkage void chacha_block_xor_neon(const struct chacha_state *state,
				      u8 *dst, const u8 *src, int nrounds);
asmlinkage void chacha_4block_xor_neon(const struct chacha_state *state,
				       u8 *dst, const u8 *src,
				       int nrounds, int bytes);
asmlinkage void poly1305_blocks_arm64(struct poly1305_block_state *state,
				      const u8 *src, u32 len, u32 hibit);
asmlinkage void poly1305_blocks_neon(struct poly1305_block_state *state,
				     const u8 *src, u32 len, u32 hibit);

/* Packets up to this many bytes (ACKs) run on general registers and open no
 * NEON section; 0 sends every packet through NEON. */
static unsigned int wgfp_neon_min = 128;
module_param(wgfp_neon_min, uint, 0600);
MODULE_PARM_DESC(wgfp_neon_min, "Mode 2: packets up to this many bytes skip NEON (default 128, 0 = never)");

/* chacha_doneon() of lib/crypto/arm64/chacha.h; inside a NEON section */
static void wgfp_chacha_neon(struct chacha_state *st, u8 *dst, const u8 *src,
			     unsigned int bytes)
{
	while (bytes) {
		unsigned int l = min_t(unsigned int, bytes, 5 * CHACHA_BLOCK_SIZE);

		if (l <= CHACHA_BLOCK_SIZE) {
			u8 buf[CHACHA_BLOCK_SIZE];

			memcpy(buf, src, l);
			chacha_block_xor_neon(st, buf, buf, 20);
			memcpy(dst, buf, l);
			st->x[12]++;
			break;
		}
		chacha_4block_xor_neon(st, dst, src, 20, l);
		st->x[12] += DIV_ROUND_UP(l, CHACHA_BLOCK_SIZE);
		bytes -= l;
		src += l;
		dst += l;
	}
}

static void wgfp_chacha_gen(struct chacha_state *st, u8 *dst, const u8 *src,
			    unsigned int bytes)
{
	u8 ks[CHACHA_BLOCK_SIZE];

	while (bytes) {
		unsigned int l = min_t(unsigned int, bytes, CHACHA_BLOCK_SIZE);

		chacha_block_generic(st, ks, 20);
		crypto_xor_cpy(dst, src, ks, l);
		bytes -= l;
		src += l;
		dst += l;
	}
	memzero_explicit(ks, sizeof(ks));
}

/* direct calls: the perlasm routines are not BTI targets */
static void wgfp_poly_blocks(struct poly1305_desc_ctx *mac, const u8 *src,
			     unsigned int len, bool neon)
{
	if (neon)
		poly1305_blocks_neon(&mac->state, src, len, 1);
	else
		poly1305_blocks_arm64(&mac->state, src, len, 1);
}

/* RFC 8439 tag of @n bytes at @m, no associated data; @neon: inside a NEON
 * section.  Whole blocks only, so poly1305_final() just emits. */
static void wgfp_mac(const u8 key[POLY1305_KEY_SIZE], const u8 *m,
		     unsigned int n, bool neon, u8 tag[POLY1305_DIGEST_SIZE])
{
	unsigned int full = round_down(n, POLY1305_BLOCK_SIZE);
	__le64 lens[2] = { 0, cpu_to_le64(n) };
	struct poly1305_desc_ctx mac;
	u8 blk[POLY1305_BLOCK_SIZE];

	poly1305_init(&mac, key);
	if (full)
		wgfp_poly_blocks(&mac, m, full, neon);
	if (n != full) {
		memset(blk, 0, sizeof(blk));
		memcpy(blk, m + full, n - full);
		wgfp_poly_blocks(&mac, blk, sizeof(blk), neon);
	}
	wgfp_poly_blocks(&mac, (const u8 *)lens, sizeof(lens), neon);
	poly1305_final(&mac, tag);
}

/* Out-of-place 1-pass NEON seal: reads @ip_len inner IP bytes from read-only
 * @src, zero-pads to @plain (@plain >= @ip_len), optionally decrements IPv4
 * TTL or IPv6 hop_limit on the encrypted plaintext without mutating @src, and
 * writes @plain ciphertext bytes plus 16-byte Poly1305 tag to @dst in a single
 * NEON section.  When @scratch_b0 is true, caller guarantees 64 writable
 * scratch bytes immediately before @dst (which will be overwritten by the outer
 * header), eliminating the 256-byte stack copy-back and 320-byte stack wipe. */
static void wgfp_seal_one_dst_nat(u8 *dst, const u8 *src, u32 ip_len, u32 plain,
				  u8 family, bool scratch_b0, u64 nonce,
				  const u8 key[CHACHA20POLY1305_KEY_SIZE],
				  unsigned int neon_min,
				  const struct wgfp_nat *nat)
{
	bool neon = plain > neon_min && crypto_simd_usable();
	u8 head_buf[5 * CHACHA_BLOCK_SIZE];
	u32 head = min_t(u32, plain, 4 * CHACHA_BLOCK_SIZE);
	u32 copy_head = min_t(u32, ip_len, head);
	u32 mid_end;
	struct chacha_state st;

	wgfp_chacha_setup(&st, nonce, key);

	if (!neon) {
		u32 wipe = CHACHA_BLOCK_SIZE + head;

		memset(head_buf, 0, CHACHA_BLOCK_SIZE);
		memcpy(head_buf + CHACHA_BLOCK_SIZE, src, copy_head);
		if (head > copy_head)
			memset(head_buf + CHACHA_BLOCK_SIZE + copy_head, 0,
			       head - copy_head);
		if (family == AF_INET && copy_head >= sizeof(struct iphdr)) {
			ip_decrease_ttl((struct iphdr *)(head_buf + CHACHA_BLOCK_SIZE));
			if (unlikely(nat && nat->flags))
				wgfp_apply_nat(head_buf + CHACHA_BLOCK_SIZE,
					       copy_head, AF_INET,
					       ((const struct iphdr *)(head_buf + CHACHA_BLOCK_SIZE))->protocol,
					       nat);
		} else if (family == AF_INET6 && copy_head >= sizeof(struct ipv6hdr)) {
			((struct ipv6hdr *)(head_buf + CHACHA_BLOCK_SIZE))->hop_limit--;
			if (unlikely(nat && nat->flags))
				wgfp_apply_nat(head_buf + CHACHA_BLOCK_SIZE,
					       copy_head, AF_INET6,
					       ((const struct ipv6hdr *)(head_buf + CHACHA_BLOCK_SIZE))->nexthdr,
					       nat);
		}

		chacha_block_generic(&st, head_buf, 20);
		wgfp_chacha_gen(&st, dst, head_buf + CHACHA_BLOCK_SIZE, head);
		if (plain > head) {
			if (ip_len == plain) {
				wgfp_chacha_gen(&st, dst + head, src + head,
						plain - head);
			} else {
				u32 off;

				mid_end = max_t(u32, head,
						round_down(ip_len,
							   CHACHA_BLOCK_SIZE));
				if (mid_end > head)
					wgfp_chacha_gen(&st, dst + head,
							src + head,
							mid_end - head);
				off = mid_end;
				while (off < plain) {
					u32 chunk = min_t(u32, plain - off,
							  4 * CHACHA_BLOCK_SIZE);
					u32 avail = off < ip_len ?
						min_t(u32, ip_len - off, chunk) : 0;
					u8 *tmp = head_buf + CHACHA_BLOCK_SIZE;

					if (avail)
						memcpy(tmp, src + off, avail);
					if (chunk > avail)
						memset(tmp + avail, 0,
						       chunk - avail);
					wgfp_chacha_gen(&st, dst + off, tmp,
							chunk);
					wipe = sizeof(head_buf);
					off += chunk;
				}
			}
		}
		wgfp_mac(head_buf, dst, plain, false, dst + plain);
		chacha_zeroize_state(&st);
		memzero_explicit(head_buf, wipe);
		return;
	}

	{
		u32 tail_wipe = 0;

		if (likely(scratch_b0)) {
			u8 *b0 = dst - CHACHA_BLOCK_SIZE;

			memset(b0, 0, CHACHA_BLOCK_SIZE);
			memcpy(dst, src, copy_head);
			if (head > copy_head)
				memset(dst + copy_head, 0, head - copy_head);
			if (family == AF_INET && copy_head >= sizeof(struct iphdr)) {
				ip_decrease_ttl((struct iphdr *)dst);
				if (unlikely(nat && nat->flags))
					wgfp_apply_nat(dst, copy_head, AF_INET,
						       ((const struct iphdr *)dst)->protocol,
						       nat);
			} else if (family == AF_INET6 && copy_head >= sizeof(struct ipv6hdr)) {
				((struct ipv6hdr *)dst)->hop_limit--;
				if (unlikely(nat && nat->flags))
					wgfp_apply_nat(dst, copy_head, AF_INET6,
						       ((const struct ipv6hdr *)dst)->nexthdr,
						       nat);
			}

			kernel_neon_begin();
			wgfp_chacha_neon(&st, b0, b0, CHACHA_BLOCK_SIZE + head);
			memcpy(head_buf, b0, POLY1305_KEY_SIZE);
			memzero_explicit(b0, CHACHA_BLOCK_SIZE);
		} else {
			memset(head_buf, 0, CHACHA_BLOCK_SIZE);
			memcpy(head_buf + CHACHA_BLOCK_SIZE, src, copy_head);
			if (head > copy_head)
				memset(head_buf + CHACHA_BLOCK_SIZE + copy_head, 0,
				       head - copy_head);
			if (family == AF_INET && copy_head >= sizeof(struct iphdr)) {
				ip_decrease_ttl((struct iphdr *)(head_buf + CHACHA_BLOCK_SIZE));
				if (unlikely(nat && nat->flags))
					wgfp_apply_nat(head_buf + CHACHA_BLOCK_SIZE,
						       copy_head, AF_INET,
						       ((const struct iphdr *)(head_buf + CHACHA_BLOCK_SIZE))->protocol,
						       nat);
			} else if (family == AF_INET6 && copy_head >= sizeof(struct ipv6hdr)) {
				((struct ipv6hdr *)(head_buf + CHACHA_BLOCK_SIZE))->hop_limit--;
				if (unlikely(nat && nat->flags))
					wgfp_apply_nat(head_buf + CHACHA_BLOCK_SIZE,
						       copy_head, AF_INET6,
						       ((const struct ipv6hdr *)(head_buf + CHACHA_BLOCK_SIZE))->nexthdr,
						       nat);
			}

			kernel_neon_begin();
			wgfp_chacha_neon(&st, head_buf, head_buf,
					 CHACHA_BLOCK_SIZE + head);
			memcpy(dst, head_buf + CHACHA_BLOCK_SIZE, head);
		}
		if (plain > head) {
			if (ip_len == plain) {
				wgfp_chacha_neon(&st, dst + head, src + head,
						 plain - head);
			} else {
				u32 off;

				mid_end = max_t(u32, head,
						round_down(ip_len,
							   CHACHA_BLOCK_SIZE));
				if (mid_end > head)
					wgfp_chacha_neon(&st, dst + head,
							 src + head,
							 mid_end - head);
				off = mid_end;
				while (off < plain) {
					u32 chunk = min_t(u32, plain - off,
							  4 * CHACHA_BLOCK_SIZE);
					u32 avail = off < ip_len ?
						min_t(u32, ip_len - off, chunk) : 0;
					u8 *tmp = head_buf + CHACHA_BLOCK_SIZE;

					if (avail)
						memcpy(tmp, src + off, avail);
					if (chunk > avail)
						memset(tmp + avail, 0,
						       chunk - avail);
					wgfp_chacha_neon(&st, dst + off, tmp,
							 chunk);
					if (chunk > tail_wipe)
						tail_wipe = chunk;
					off += chunk;
				}
			}
		}
		wgfp_mac(head_buf, dst, plain, true, dst + plain);
		kernel_neon_end();
		chacha_zeroize_state(&st);
		memzero_explicit(head_buf, scratch_b0 ? POLY1305_KEY_SIZE :
					   CHACHA_BLOCK_SIZE);
		if (unlikely(tail_wipe))
			memzero_explicit(head_buf + CHACHA_BLOCK_SIZE, tail_wipe);
	}
}

static void wgfp_seal_one_dst(u8 *dst, const u8 *src, u32 ip_len, u32 plain,
			      u8 family, bool scratch_b0, u64 nonce,
			      const u8 key[CHACHA20POLY1305_KEY_SIZE],
			      unsigned int neon_min)
{
	wgfp_seal_one_dst_nat(dst, src, ip_len, plain, family, scratch_b0,
			      nonce, key, neon_min, NULL);
}

/* @n plaintext bytes from @src into @dst (may equal @src), tag at src + n;
 * @dst is untouched when the tag does not verify, and @src is never written
 * when @dst != @src. Block 0 rides with the open fold's head when the length
 * allows it, else a one-block NEON call. */
static bool wgfp_open_one_dst(u8 *dst, const u8 *src, size_t n, u64 nonce,
			      const u8 key[CHACHA20POLY1305_KEY_SIZE],
			      unsigned int neon_min)
{
	bool neon = n > neon_min && crypto_simd_usable();
	u8 buf[CHACHA_BLOCK_SIZE + WGFP_OPEN_HEAD];
	u8 tag[POLY1305_DIGEST_SIZE];
	struct chacha_state st;
	size_t head = 0;
	bool ok;

	wgfp_chacha_setup(&st, nonce, key);
	if (!neon) {
		chacha_block_generic(&st, buf, 20);
		wgfp_mac(buf, src, n, false, tag);
		ok = !crypto_memneq(tag, src + n, POLY1305_DIGEST_SIZE);
		if (ok)
			wgfp_chacha_gen(&st, dst, src, n);
	} else {
		memset(buf, 0, CHACHA_BLOCK_SIZE);
		kernel_neon_begin();
		if (wgfp_open_foldable(n)) {
			head = min_t(size_t, n, ALIGN(n % (5 * CHACHA_BLOCK_SIZE),
						      CHACHA_BLOCK_SIZE));
			memcpy(buf + CHACHA_BLOCK_SIZE, src, head);
			wgfp_chacha_neon(&st, buf, buf, CHACHA_BLOCK_SIZE + head);
		} else {
			chacha_block_xor_neon(&st, buf, buf, 20);
			st.x[12]++;
		}
		wgfp_mac(buf, src, n, true, tag);
		ok = !crypto_memneq(tag, src + n, POLY1305_DIGEST_SIZE);
		if (ok) {
			if (n > head)
				wgfp_chacha_neon(&st, dst + head, src + head, n - head);
			memcpy(dst, buf + CHACHA_BLOCK_SIZE, head);
		}
		kernel_neon_end();
	}
	chacha_zeroize_state(&st);
	memzero_explicit(buf, CHACHA_BLOCK_SIZE + head);
	return ok;
}

static bool wgfp_open_one(u8 *p, size_t n, u64 nonce,
			  const u8 key[CHACHA20POLY1305_KEY_SIZE],
			  unsigned int neon_min)
{
	return wgfp_open_one_dst(p, p, n, nonce, key, neon_min);
}

/* Both directions against the library once: seal with and without room in
 * front, open of library-sealed data, and a forged tag refused with the
 * buffer unchanged.  -1 while another CPU is testing. */
static int wgfp_one_ok(void)
{
#define WGFP_ONE_TEST_MAX	1500
	static const u32 lens[] = { 1, 15, 16, 17, 63, 64, 65, 127, 128, 129,
				    143, 191, 192, 255, 256, 257, 319, 320, 321,
				    576, 577, 1280, 1419, 1420, 1424, 1440, 1472,
				    WGFP_ONE_TEST_MAX };
	static u8 a[CHACHA_BLOCK_SIZE + WGFP_ONE_TEST_MAX + POLY1305_DIGEST_SIZE];
	static u8 b[WGFP_ONE_TEST_MAX + POLY1305_DIGEST_SIZE];
	static u8 m[WGFP_ONE_TEST_MAX];
	static u8 m2[WGFP_ONE_TEST_MAX];
	static u8 b2[WGFP_ONE_TEST_MAX + POLY1305_DIGEST_SIZE];
	static atomic_t busy = ATOMIC_INIT(0);
	static int state = -1;
	u8 key[CHACHA20POLY1305_KEY_SIZE], *p = a + CHACHA_BLOCK_SIZE;
	int i, j, nm, ok = 1;

	if (READ_ONCE(state) >= 0)
		return READ_ONCE(state);
	if (atomic_xchg(&busy, 1))
		return -1;
	for (i = 0; i < sizeof(key); i++)
		key[i] = 0x20 + i;
	for (i = 0; i < ARRAY_SIZE(lens) && ok; i++) {
		u32 len = lens[i];
		u32 ip_len_pad = len > 7 ? len - 7 : len;
		u64 nonce = 0x0f0e0d0cULL + len;

		if (WARN_ON_ONCE(len > WGFP_ONE_TEST_MAX)) {
			ok = 0;
			break;
		}
		for (j = 0; j < len; j++)
			m[j] = j * 11 + len;
		chacha20poly1305_encrypt(b, m, len, NULL, 0, nonce, key);
		/* Padded + TTL/hop_limit reference in m2/b2 */
		memcpy(m2, m, ip_len_pad);
		if (len > ip_len_pad)
			memset(m2 + ip_len_pad, 0, len - ip_len_pad);
		if (ip_len_pad >= sizeof(struct ipv6hdr)) {
			((struct ipv6hdr *)m2)->hop_limit--;
			chacha20poly1305_encrypt(b2, m2, len, NULL, 0, nonce, key);
		} else if (ip_len_pad >= sizeof(struct iphdr)) {
			ip_decrease_ttl((struct iphdr *)m2);
			chacha20poly1305_encrypt(b2, m2, len, NULL, 0, nonce, key);
		} else {
			chacha20poly1305_encrypt(b2, m2, len, NULL, 0, nonce, key);
		}
		/* every length both ways: all NEON (0) and all general registers */
		for (nm = 0; nm < 2 && ok; nm++) {
			unsigned int neon_min = nm ? UINT_MAX : 0;
			u8 fam = ip_len_pad >= sizeof(struct ipv6hdr) ? AF_INET6 :
				 ip_len_pad >= sizeof(struct iphdr) ? AF_INET : 0;

			/* Out-of-place seal (scratch_b0=false and true) + src untouched */
			memset(a, 0xa5, CHACHA_BLOCK_SIZE);
			memset(p, 0x5a, len + POLY1305_DIGEST_SIZE);
			wgfp_seal_one_dst(p, m, len, len, 0, false, nonce, key, neon_min);
			ok = !memcmp(p, b, len + POLY1305_DIGEST_SIZE) &&
			     !memchr_inv(a, 0xa5, CHACHA_BLOCK_SIZE);
			/* Out-of-place seal with scratch_b0=true + zero-padding + TTL/hop_limit dec */
			memset(a, 0xa5, CHACHA_BLOCK_SIZE);
			memset(p, 0x5a, len + POLY1305_DIGEST_SIZE);
			wgfp_seal_one_dst(p, m, ip_len_pad, len, fam, true, nonce, key, neon_min);
			ok = ok && !memcmp(p, b2, len + POLY1305_DIGEST_SIZE);
			/* Verify src m was never mutated */
			for (j = 0; j < len && ok; j++)
				if (m[j] != (u8)(j * 11 + len))
					ok = 0;
			/* In-place and out-of-place open */
			memcpy(p, b, len + POLY1305_DIGEST_SIZE);
			ok = ok && wgfp_open_one(p, len, nonce, key, neon_min) &&
			     !memcmp(p, m, len);
			memset(p, 0x3c, len);
			ok = ok && wgfp_open_one_dst(p, b, len, nonce, key, neon_min) &&
			     !memcmp(p, m, len);
			memcpy(p, b, len + POLY1305_DIGEST_SIZE);
			p[len] ^= 1;
			ok = ok && !wgfp_open_one(p, len, nonce, key, neon_min) &&
			     !memcmp(p, b, len) && p[len] == (b[len] ^ 1);
		}
		if (!ok)
			pr_err("wgfp: one-section AEAD differs from the library at %u bytes; not used\n",
			       len);
	}
	if (ok)
		pr_info("wgfp: one-section AEAD matches the library (%zu lengths)\n",
			ARRAY_SIZE(lens));
	WRITE_ONCE(state, ok);
	atomic_set(&busy, 0);
	return ok;
}
#else
static int wgfp_one_ok(void) { return 0; }
#endif

/* Seal with the Poly1305 key block folded into the data's ChaCha pass.
 * chacha20poly1305_encrypt() makes block 0 (the one-time Poly1305 key) with a
 * separate 32-byte chacha20_crypt(), which arm64 sends to the generic C code
 * (chacha_crypt_arch() takes NEON only above 64 bytes): 3-5% of a sealed
 * packet.  Running ChaCha over the 64 bytes in front of the data plus the
 * data gives block 0 from the same NEON pass, in lanes the tail call computes
 * anyway (1420 = 4 x 320 + 140).  Those 64 bytes are saved and restored.
 * Decrypt cannot do this: the tag is checked over the ciphertext before it
 * is decrypted in place, so the key must exist first.
 * -1: self-test against the library on the first seal, then 2 when the
 * one-section path is built and matches, else 1 (0 if the fold differs);
 * 0: library; 1: fold; 2: one NEON section a packet. */
static int wgfp_seal_fold = -1;
module_param(wgfp_seal_fold, int, 0600);
MODULE_PARM_DESC(wgfp_seal_fold, "Seal: -1 self-test then best, 0 library, 1 key block folded into the NEON pass, 2 one NEON section a packet");

static void wgfp_seal_folded(u8 *p, u32 len, u64 nonce,
			     const u8 key[CHACHA20POLY1305_KEY_SIZE])
{
	static const u8 pad0[16];
	u8 *b0 = p - CHACHA_BLOCK_SIZE;
	struct poly1305_desc_ctx mac;
	struct chacha_state st;
	u32 k[CHACHA_KEY_WORDS];
	u8 save[CHACHA_BLOCK_SIZE];
	__le64 iv[2] = { 0, cpu_to_le64(nonce) };
	__le64 lens[2] = { 0, cpu_to_le64(len) };
	int i;

	for (i = 0; i < CHACHA_KEY_WORDS; i++)
		k[i] = get_unaligned_le32(key + 4 * i);
	chacha_init(&st, k, (const u8 *)iv);
	memcpy(save, b0, sizeof(save));
	memset(b0, 0, sizeof(save));
	chacha20_crypt(&st, b0, b0, CHACHA_BLOCK_SIZE + len);
	poly1305_init(&mac, b0);	/* block 0 keystream = the MAC key */
	memcpy(b0, save, sizeof(save));
	poly1305_update(&mac, p, len);
	if (len & 0xf)
		poly1305_update(&mac, pad0, 0x10 - (len & 0xf));
	poly1305_update(&mac, (const u8 *)lens, sizeof(lens));
	poly1305_final(&mac, p + len);
	chacha_zeroize_state(&st);
	memzero_explicit(k, sizeof(k));
}

/* Compare against the library once (lengths around the 64- and 320-byte
 * boundaries and full-size packets); a CPU that loses the race seals with
 * the library meanwhile. */
static int wgfp_seal_selftest(void)
{
	static const u32 lens[] = { 1, 15, 16, 63, 64, 65, 255, 256, 320, 321,
				    1280, 1420, 1424, 1440 };
	static u8 a[CHACHA_BLOCK_SIZE + 1440 + POLY1305_DIGEST_SIZE];
	static u8 b[1440 + POLY1305_DIGEST_SIZE];
	static atomic_t busy = ATOMIC_INIT(0);
	u8 key[CHACHA20POLY1305_KEY_SIZE];
	int i, j, ok = 1, one;

	if (atomic_xchg(&busy, 1))
		return 0;
	one = wgfp_one_ok();
	if (one < 0) {		/* another CPU is testing it: library this time */
		atomic_set(&busy, 0);
		return 0;
	}
	for (i = 0; i < sizeof(key); i++)
		key[i] = 0x40 + i;
	for (i = 0; i < ARRAY_SIZE(lens) && ok; i++) {
		u32 len = lens[i];

		memset(a, 0xa5, CHACHA_BLOCK_SIZE);
		for (j = 0; j < len; j++)
			a[CHACHA_BLOCK_SIZE + j] = b[j] = j * 7 + len;
		chacha20poly1305_encrypt(b, b, len, NULL, 0, 0x0102030405ULL + len, key);
		wgfp_seal_folded(a + CHACHA_BLOCK_SIZE, len, 0x0102030405ULL + len, key);
		ok = !memcmp(a + CHACHA_BLOCK_SIZE, b, len + POLY1305_DIGEST_SIZE) &&
		     !memchr_inv(a, 0xa5, CHACHA_BLOCK_SIZE);
		if (!ok)
			pr_err("wgfp: folded seal differs from the library at %u bytes; using the library\n",
			       len);
	}
	ok = ok ? (one ? 2 : 1) : 0;
	WRITE_ONCE(wgfp_seal_fold, ok);
	if (ok)
		pr_info("wgfp: folded seal matches the library (%zu lengths), mode %d\n",
			ARRAY_SIZE(lens), ok);
	atomic_set(&busy, 0);
	return ok;
}

/* Out-of-place seal from read-only @src (@ip_len bytes) into @dst (@plain + 16
 * bytes), with optional inner IPv4 TTL or IPv6 hop_limit decrement and NAT. */
static void wgfp_seal_dst(u8 *dst, const u8 *src, u32 ip_len, u32 plain,
			  u8 family, u32 room, u64 nonce,
			  const u8 key[CHACHA20POLY1305_KEY_SIZE],
			  const struct wgfp_nat *nat)
{
	int fold = READ_ONCE(wgfp_seal_fold);

	if (unlikely(fold < 0))
		fold = wgfp_seal_selftest();
#ifdef WGFP_ONE_NEON
	if (likely(fold == 2)) {
		wgfp_seal_one_dst_nat(dst, src, ip_len, plain, family,
				      room >= CHACHA_BLOCK_SIZE, nonce, key,
				      READ_ONCE(wgfp_neon_min), nat);
		return;
	}
#endif
	memcpy(dst, src, ip_len);
	if (family == AF_INET && ip_len >= sizeof(struct iphdr)) {
		ip_decrease_ttl((struct iphdr *)dst);
		if (unlikely(nat && nat->flags))
			wgfp_apply_nat(dst, ip_len, AF_INET,
				       ((const struct iphdr *)dst)->protocol,
				       nat);
	} else if (family == AF_INET6 && ip_len >= sizeof(struct ipv6hdr)) {
		((struct ipv6hdr *)dst)->hop_limit--;
		if (unlikely(nat && nat->flags))
			wgfp_apply_nat(dst, ip_len, AF_INET6,
				       ((const struct ipv6hdr *)dst)->nexthdr,
				       nat);
	}
	if (plain > ip_len)
		memset(dst + ip_len, 0, plain - ip_len);
	if (fold > 0 && likely(room >= CHACHA_BLOCK_SIZE))
		wgfp_seal_folded(dst, plain, nonce, key);
	else
		chacha20poly1305_encrypt(dst, dst, plain, NULL, 0, nonce, key);
}

/* The decrypt side of the same fold.  The tag must be checked over the
 * ciphertext before it is decrypted in place, so block 0 and the first t
 * bytes (t = the n mod 320 tail, rounded up to whole blocks) are done on a
 * stack copy: one NEON call yields the MAC key and t bytes of plaintext,
 * the rest (now a whole number of 320-byte NEON calls) is decrypted in
 * place after the tag verifies, and the t bytes are copied back.  Same
 * number of NEON calls as the library, minus its generic block 0; the
 * buffer is untouched when the tag does not verify.  Applies when
 * 64 < n and 0 < n mod 320 <= 256 (1420-byte packets: t = 192).
 * -1: self-test on the first open, then 2 when the one-section path is
 * built and matches, else 1 (0 if the fold differs); 0 library; 1 fold;
 * 2 one NEON section a packet. */
static int wgfp_open_fold = -1;
module_param(wgfp_open_fold, int, 0600);
MODULE_PARM_DESC(wgfp_open_fold, "Open: -1 self-test then best, 0 library, 1 key block folded into a NEON call, 2 one NEON section a packet");

/* @n plaintext bytes from @src into @dst (may equal @src), tag at src + n;
 * caller checked foldable. */
static bool wgfp_open_folded_dst(u8 *dst, const u8 *src, size_t n, u64 nonce,
				 const u8 key[CHACHA20POLY1305_KEY_SIZE])
{
	static const u8 pad0[16];
	size_t head = min_t(size_t, n, ALIGN(n % (5 * CHACHA_BLOCK_SIZE),
					     CHACHA_BLOCK_SIZE));
	u8 buf[CHACHA_BLOCK_SIZE + WGFP_OPEN_HEAD];
	__le64 iv[2] = { 0, cpu_to_le64(nonce) };
	__le64 lens[2] = { 0, cpu_to_le64(n) };
	struct poly1305_desc_ctx mac;
	u8 tag[POLY1305_DIGEST_SIZE];
	struct chacha_state st;
	u32 k[CHACHA_KEY_WORDS];
	bool ok;
	int i;

	for (i = 0; i < CHACHA_KEY_WORDS; i++)
		k[i] = get_unaligned_le32(key + 4 * i);
	chacha_init(&st, k, (const u8 *)iv);
	memset(buf, 0, CHACHA_BLOCK_SIZE);
	memcpy(buf + CHACHA_BLOCK_SIZE, src, head);
	chacha20_crypt(&st, buf, buf, CHACHA_BLOCK_SIZE + head);
	poly1305_init(&mac, buf);
	poly1305_update(&mac, src, n);
	if (n & 0xf)
		poly1305_update(&mac, pad0, 0x10 - (n & 0xf));
	poly1305_update(&mac, (const u8 *)lens, sizeof(lens));
	poly1305_final(&mac, tag);
	ok = !crypto_memneq(tag, src + n, POLY1305_DIGEST_SIZE);
	if (ok) {
		/* head is whole blocks here, so st continues at the next one */
		if (n > head)
			chacha20_crypt(&st, dst + head, src + head, n - head);
		memcpy(dst, buf + CHACHA_BLOCK_SIZE, head);
	}
	chacha_zeroize_state(&st);
	memzero_explicit(buf, CHACHA_BLOCK_SIZE + head);
	memzero_explicit(k, sizeof(k));
	return ok;
}

static bool wgfp_open_folded(u8 *p, size_t n, u64 nonce,
			     const u8 key[CHACHA20POLY1305_KEY_SIZE])
{
	return wgfp_open_folded_dst(p, p, n, nonce, key);
}

/* Seal with the library, open folded: plaintext and verdict must match for
 * every foldable length, and a bad tag must leave the buffer untouched. */
static int wgfp_open_selftest(void)
{
#define WGFP_OPEN_TEST_MAX	1472
	static const u32 lens[] = { 65, 100, 127, 128, 255, 256, 321, 400,
				    576, 1088, 1280 + 140, 1440, WGFP_OPEN_TEST_MAX };
	static u8 a[WGFP_OPEN_TEST_MAX + POLY1305_DIGEST_SIZE];
	static u8 m[WGFP_OPEN_TEST_MAX + POLY1305_DIGEST_SIZE];
	static atomic_t busy = ATOMIC_INIT(0);
	u8 key[CHACHA20POLY1305_KEY_SIZE];
	int i, j, ok = 1, one;

	if (atomic_xchg(&busy, 1))
		return 0;
	one = wgfp_one_ok();
	if (one < 0) {
		atomic_set(&busy, 0);
		return 0;
	}
	for (i = 0; i < sizeof(key); i++)
		key[i] = 0x60 + i;
	for (i = 0; i < ARRAY_SIZE(lens) && ok; i++) {
		u32 len = lens[i];
		u64 nonce = 0x0a0b0c0dULL + len;

		if (WARN_ON_ONCE(len > WGFP_OPEN_TEST_MAX)) {
			ok = 0;
			break;
		}

		for (j = 0; j < len; j++)
			m[j] = j * 13 + len;
		chacha20poly1305_encrypt(a, m, len, NULL, 0, nonce, key);
		ok = wgfp_open_foldable(len) &&
		     wgfp_open_folded(a, len, nonce, key) && !memcmp(a, m, len);
		/* bad tag: refused, buffer as it was */
		chacha20poly1305_encrypt(a, m, len, NULL, 0, nonce, key);
		a[len] ^= 1;
		memcpy(m, a, len + POLY1305_DIGEST_SIZE);
		ok = ok && !wgfp_open_folded(a, len, nonce, key) &&
		     !memcmp(a, m, len + POLY1305_DIGEST_SIZE);
		if (!ok)
			pr_err("wgfp: folded open differs from the library at %u bytes; using the library\n",
			       len);
	}
	ok = ok ? (one ? 2 : 1) : 0;
	WRITE_ONCE(wgfp_open_fold, ok);
	if (ok)
		pr_info("wgfp: folded open matches the library (%zu lengths), mode %d\n",
			ARRAY_SIZE(lens), ok);
	atomic_set(&busy, 0);
	return ok;
}

/* Out-of-place chacha20poly1305_decrypt() from read-only @src into @dst;
 * @clen includes the 16-byte tag. */
static bool wgfp_open_dst(u8 *dst, const u8 *src, size_t clen, u64 nonce,
			  const u8 key[CHACHA20POLY1305_KEY_SIZE])
{
	int fold = READ_ONCE(wgfp_open_fold);

	if (unlikely(fold < 0))
		fold = wgfp_open_selftest();
#ifdef WGFP_ONE_NEON
	if (likely(fold == 2) && clen > POLY1305_DIGEST_SIZE)
		return wgfp_open_one_dst(dst, src, clen - POLY1305_DIGEST_SIZE,
					 nonce, key, READ_ONCE(wgfp_neon_min));
#endif
	if (fold > 0 && clen > POLY1305_DIGEST_SIZE &&
	    wgfp_open_foldable(clen - POLY1305_DIGEST_SIZE))
		return wgfp_open_folded_dst(dst, src, clen - POLY1305_DIGEST_SIZE,
					    nonce, key);
	return chacha20poly1305_decrypt(dst, src, clen, NULL, 0, nonce, key);
}

static struct wgfp_lab wgfp_lab_state;
static bool wgfp_lab_on;
static DEFINE_MUTEX(wgfp_lab_lock);
static char wgfp_lab_cfg[16] = "off";
#define WGFP_OWNER_TOKEN_LEN	32
static char wgfp_peer_owner[WGFP_OWNER_TOKEN_LEN + 1];
struct wgfp_binds;
static struct wgfp_binds __rcu *wgfp_bs;

static int wgfp_lab_frame(void *priv, struct dpaa_wgfp_frame *f)
{
	struct wgfp_lab *l = priv;
	const u8 *data = f->buf + f->off;
	const u8 *inner = data + ETH_HLEN;
	const struct iphdr *iph;
	struct dpaa_wgfp_tx_buf tx;
	struct message_data *wg;
	struct udphdr *udp;
	struct iphdr *oip;
	u32 ip_len, plain, tot, frame_len;
	u64 ctr;
	u8 *dst, *hdr;

	if (f->len < ETH_HLEN + sizeof(*iph) ||
	    ((const struct ethhdr *)data)->h_proto != htons(ETH_P_IP))
		return 0;
	iph = (const struct iphdr *)inner;
	if (iph->version != 4)
		return 0;
	ip_len = ntohs(iph->tot_len);
	if (ip_len < sizeof(*iph) || ETH_HLEN + ip_len > f->len)
		return 0;

	plain = ALIGN(ip_len, 16);
	frame_len = WGFP_HDR_LEN + plain + POLY1305_DIGEST_SIZE;
	{
		u32 pad_off = WGFP_HDR_LEN < CHACHA_BLOCK_SIZE ?
			      CHACHA_BLOCK_SIZE - WGFP_HDR_LEN : 0;
		u32 off = DPAA_WGFP_TX_HEADROOM + pad_off;

		if (unlikely(pad_off + frame_len > DPAA_WGFP_TX_BUF_SIZE ||
			     !dpaa_wgfp_tx_alloc(l->egress, 0,
						 pad_off + frame_len, &tx))) {
			dpaa_wgfp_drop(f);
			return 1;
		}
		hdr = (u8 *)tx.buf + off;
		dst = hdr + WGFP_HDR_LEN;

		ctr = (u64)atomic64_inc_return(&l->counter) - 1;
		wgfp_seal_dst(dst, inner, ip_len, plain, 0, CHACHA_BLOCK_SIZE,
			      ctr, l->key, NULL);

		memcpy(hdr, l->tmpl, sizeof(l->tmpl));

		tot = sizeof(struct iphdr) + sizeof(struct udphdr) +
		      sizeof(struct message_data) + plain + POLY1305_DIGEST_SIZE;
		oip = (struct iphdr *)(hdr + ETH_HLEN);
		oip->tot_len = htons(tot);
		oip->check = 0;
		oip->check = ip_fast_csum((const u8 *)oip, oip->ihl);
		udp = (struct udphdr *)(oip + 1);
		udp->len = htons(tot - sizeof(struct iphdr));

		wg = (struct message_data *)(udp + 1);
		wg->header.type = cpu_to_le32(MESSAGE_DATA);
		wg->key_idx = l->rcv_idx;
		wg->counter = cpu_to_le64(ctr);

		dpaa_wgfp_tx_submit(f, &tx, off, frame_len);
	}
	return 1;
}

static const struct dpaa_wgfp_ops wgfp_lab_ops = {
	.frame = wgfp_lab_frame,
};

static void wgfp_lab_stop(void)
{
	if (wgfp_lab_on) {
		dpaa_wgfp_unregister();
		wgfp_lab_on = false;
	}
}

static int wgfp_lab_start(const char *cfg)
{
	struct wgfp_lab *l = &wgfp_lab_state;
	char egress[IFNAMSIZ], dmac[24], smac[24], sip[20], dip[20], hex[80];
	struct net_device *dev;
	struct ethhdr *eth;
	struct iphdr *ip;
	struct udphdr *udp;
	u8 mac_d[ETH_ALEN], mac_s[ETH_ALEN];
	__be32 a_s, a_d;
	u16 sport, dport;
	u32 idx;
	int err;

	if (sscanf(cfg, "%15s %23s %23s %19s %19s %hu %hu %u %79s",
		   egress, dmac, smac, sip, dip, &sport, &dport, &idx, hex) != 9)
		return -EINVAL;
	if (!mac_pton(dmac, mac_d) || !mac_pton(smac, mac_s) ||
	    !in4_pton(sip, -1, (u8 *)&a_s, -1, NULL) ||
	    !in4_pton(dip, -1, (u8 *)&a_d, -1, NULL) ||
	    strlen(hex) != 2 * CHACHA20POLY1305_KEY_SIZE ||
	    hex2bin(l->key, hex, CHACHA20POLY1305_KEY_SIZE))
		return -EINVAL;

	dev = dev_get_by_name(&init_net, egress);
	if (!dev)
		return -ENODEV;
	err = dpaa_wgfp_egress(dev);
	dev_put(dev);
	if (err < 0)
		return err;
	l->egress = err;

	eth = (struct ethhdr *)l->tmpl;
	ether_addr_copy(eth->h_dest, mac_d);
	ether_addr_copy(eth->h_source, mac_s);
	eth->h_proto = htons(ETH_P_IP);
	ip = (struct iphdr *)(eth + 1);
	memset(ip, 0, sizeof(*ip));
	ip->version = 4;
	ip->ihl = 5;
	ip->frag_off = htons(IP_DF);
	ip->ttl = 64;
	ip->protocol = IPPROTO_UDP;
	ip->saddr = a_s;
	ip->daddr = a_d;
	udp = (struct udphdr *)(ip + 1);
	memset(udp, 0, sizeof(*udp));
	udp->source = htons(sport);
	udp->dest = htons(dport);	/* check stays 0: legal for IPv4 */
	l->rcv_idx = cpu_to_le32(idx);
	atomic64_set(&l->counter, 0);

	err = dpaa_wgfp_register(&wgfp_lab_ops, l);
	if (err)
		return err;
	wgfp_lab_on = true;
	pr_info("fastpath lab seal on: %s -> %pI4:%u, receiver index %u\n",
		egress, &a_d, dport, idx);
	return 0;
}

static int wgfp_lab_cfg_set(const char *val, const struct kernel_param *kp)
{
	char buf[256];
	int err = 0;

	strscpy(buf, val, sizeof(buf));
	strim(buf);
	mutex_lock(&wgfp_lab_lock);
	if (!strcmp(buf, "off")) {
		wgfp_lab_stop();
		strscpy(wgfp_lab_cfg, "off", sizeof(wgfp_lab_cfg));
	} else if (wgfp_peer_owner[0]) {
		err = -EBUSY;
	} else {
		wgfp_lab_stop();
		err = rcu_access_pointer(wgfp_bs) ? -EBUSY : wgfp_lab_start(buf);
		strscpy(wgfp_lab_cfg, err ? "off" : "on", sizeof(wgfp_lab_cfg));
	}
	mutex_unlock(&wgfp_lab_lock);
	return err;
}

static int wgfp_lab_cfg_get(char *buf, const struct kernel_param *kp)
{
	return scnprintf(buf, PAGE_SIZE, "%s sealed=%lld\n", wgfp_lab_cfg,
			 (long long)atomic64_read(&wgfp_lab_state.counter));
}

static const struct kernel_param_ops wgfp_lab_ops_param = {
	.set = wgfp_lab_cfg_set,
	.get = wgfp_lab_cfg_get,
};
module_param_cb(wgfp_lab, &wgfp_lab_ops_param, NULL, 0600);
MODULE_PARM_DESC(wgfp_lab, "DPAA fast path lab seal (P2a): see fastpath.c; \"off\" disables");

/*
 * Peer mode (P2b/P2c): the same in-place seal, keyed from real WireGuard
 * peers of one or more bound wg devices.  Each frame uses its peer's current
 * keypair (receiver index, key, atomic sending counter), honours
 * REJECT_AFTER_*, triggers a rekey at REKEY_AFTER_*, pads to 16 within the
 * device MTU and feeds the peer timers.
 *
 * Flows, not peers, are the unit (P2b-2): wg_xmit() offers every packet to
 * wg_fastpath_learn(), which learns inner IPv4 TCP/UDP flows of a bound
 * device that arrived on a DPAA port and that the firewall admitted, and
 * installs an FMan record steering the flow's later frames to the sinks.  The
 * sink path finds the flow (lockless, RCU) and through it the per-peer state:
 * the outer Ethernet/IPv4/UDP template, built from the peer's endpoint through
 * the routing table and neighbour cache (any DPAA port can be the egress),
 * refreshed once a second or at once when a frame shows the peer roamed.
 *
 * A frame it cannot seal is punted (P2b-3): copied into an sk_buff and
 * received again on its flow's ingress port, so the stack handles it
 * (handshake queueing, ICMP, PMTU, TTL, AllowedIPs).  AllowedIPs are re-checked
 * for every record each aging period.
 *
 * P3 receive offload (wgfp_rx): an outer flow is admitted only after one of
 * its packets was delivered to the WireGuard socket on the stack, which proves
 * the exact tuple passed INPUT, and the ingress port comes from that
 * observation.  Sink queues are per port, so a dequeued frame carries its
 * ingress identity and cannot select a flow learned on another port.  The RX
 * record drives a per-port distributor that round-robins the untouched buffer
 * to the port's worker queues, so decryption of one outer flow spreads over
 * the CPUs instead of pinning one.  Off by default; the board gates use it.
 *
 *   echo "wg0 [wg1 ...]" > /sys/module/wireguard/parameters/wgfp_peer
 *   echo off > /sys/module/wireguard/parameters/wgfp_peer
 *
 * Writing a list replaces the bound set (a full teardown and rebuild: flows
 * relearn within a second; the VyOS CLI writes the whole list on commit).
 * A wgfp_peer_lease reservation blocks other peer and lab writers.  A leased
 * bind resolves owner-tagged link altnames; stop retains the reservation until
 * the owner has removed its links and explicitly releases it.
 */

#define WGFP_MAX_BIND	8
#define WGFP_OWNER_ALIAS_MAX	63
#define WGFP_OWNER_ALIASES_SIZE	(WGFP_MAX_BIND * (WGFP_OWNER_ALIAS_MAX + 1))

#define WGFP_PPPOE_HLEN		8	/* 6-byte PPPoE header + 2-byte PPP protocol */
#define WGFP_MAX_L2_HLEN	(ETH_HLEN + VLAN_HLEN + WGFP_PPPOE_HLEN)

struct wgfp_tmpl {
	struct rcu_head rcu;
	int egress;		/* dpaa_wgfp_egress() handle (physical DPAA port) */
	int ifindex;		/* physical DPAA port ifindex */
	int route_ifindex;	/* logical route device ifindex (port, vif or ppp) */
	u32 mtu;		/* outer route MTU */
	u8 family;		/* outer: AF_INET or AF_INET6 */
	u8 l2_hlen;		/* Ethernet (+ optional 802.1Q + optional PPPoE/PPP) */
	u8 pppoe_off;		/* offset of PPPoE header in hdr[], 0 if none */
	u8 hlen;		/* l2_hlen + IP + UDP */
	u8 hdr[WGFP_MAX_L2_HLEN + sizeof(struct ipv6hdr) + sizeof(struct udphdr)];
};

struct wgfp_binds {
	unsigned int n;
	struct net_device *wgdev[WGFP_MAX_BIND];	/* references */
	unsigned long invalidated;
	bool stopping;
};

struct wgfp_rx_cnt {
	u64 bytes;	/* single writer: this CPU in BH-disabled NAPI */
	u64 folded;	/* single writer: wgfp_rx_fold() */
};

/* One peer with learned flows.  Created by the first flow, freed by the aging
 * pass once it has none; flows point at it (fl->pst) and keep it alive. */
struct wgfp_pst {
	struct hlist_node node;		/* wgfp_psts, under wgfp_flows_lock */
	struct rcu_head rcu;
	struct wg_peer *peer;		/* reference */
	struct net_device *wgdev;
	struct wgfp_tmpl __rcu *tmpl;
	struct delayed_work refresh;
	unsigned long last_timer;
	unsigned long last_rx_timer;	/* P6 receive timers, same 10 ms rate */
	unsigned long last_refresh_kick;/* rate-limit TX alloc failure refresh */
	unsigned int nflows;		/* under wgfp_flows_lock */
	struct wgfp_flow *rxfl;		/* P3 outer record, under the lock */
	struct wgfp_rx_cnt __percpu *rx_pend;	/* P6 bytes not yet in peer->rx_bytes */
	int last_rx_egress;		/* P6 speculative TX slot egress hint */
	int last_rx_ifindex;		/* P6 speculative TX slot ifindex hint */
	bool stopping;
};

/* P6 receive statistics.  Upstream update_rx_stats() adds every packet to
 * peer->rx_bytes, one cache line that all four CPUs write in spread mode
 * (0.7% of the download profile), without synchronisation.  The bytes collect
 * per CPU (single-writer monotonic u64, no ARMv8.0 ldxr/stxr) and
 * wgfp_rx_fold() moves the delta to the peer from the refresh worker once a
 * second and at stop, so the peer's transfer count lags by up to a second.
 * The device counters stay per packet and per CPU. */
static void wgfp_rx_stats(struct wgfp_pst *pst, u32 len)
{
	struct wgfp_rx_cnt *c = this_cpu_ptr(pst->rx_pend);

	dev_sw_netstats_rx_add(pst->peer->device->dev, len);
	WRITE_ONCE(c->bytes, c->bytes + len);
}

static void wgfp_rx_fold(struct wgfp_pst *pst)
{
	u64 sum = 0;
	int cpu;

	for_each_possible_cpu(cpu) {
		struct wgfp_rx_cnt *c = per_cpu_ptr(pst->rx_pend, cpu);
		u64 cur = READ_ONCE(c->bytes);

		sum += cur - c->folded;
		c->folded = cur;
	}
	if (sum)
		pst->peer->rx_bytes += sum;
}

/*
 * P3 admission.  A flow is admitted only after a packet of its exact outer
 * tuple was delivered to the WireGuard socket on the same ingress port.
 * wg_receive() runs when the UDP tunnel encap delivers to the socket, i.e.
 * after IP input and the INPUT hook, so that observation proves this tuple
 * passed INPUT; skb_iif identifies the port.  The record then steers later
 * packets around that path.  As with an offloaded flowtable flow, the record
 * stays until the endpoint, the template or the binding changes, or the port
 * drops the family; a later firewall change does not re-admit it.
 */
#define WGFP_OBS_PER_CPU	8

#define WGFP_OBS_TTL	(30 * HZ)

struct wgfp_obs {
	struct dpaa_wgfp_tuple t;
	int wg_ifindex;
	int ifindex;
	unsigned long jiffies;
	u64 epoch;
};

struct wgfp_obs_ring {
	u32 seq;
	struct wgfp_obs e[WGFP_OBS_PER_CPU];
	u32 next;
};

static DEFINE_PER_CPU(struct wgfp_obs_ring, wgfp_obs);
static atomic64_t wgfp_obs_epoch = ATOMIC64_INIT(1);
static atomic_t wgfp_quiesce_active = ATOMIC_INIT(0);

/* On by default: WireGuard offload follows the port offload, and a bound
 * peer's receive side is accelerated like its transmit side (the P6, P2b-3
 * and upstream netns.sh gates pass with it on; 0 is the A/B kill switch). */
static bool wgfp_rx = true;
module_param(wgfp_rx, bool, 0600);
MODULE_PARM_DESC(wgfp_rx, "P3 receive offload for tuples observed on the stack");
static void wgfp_pst_rx_sync(struct wgfp_pst *pst);
static bool wgfp_ct_admitted(struct net *net, struct sk_buff *skb,
			     const struct dpaa_wgfp_tuple *t,
			     struct dpaa_wgfp_tuple *orig_out,
			     struct wgfp_nat *nat_out);

static char wgfp_peer_cfg[WGFP_MAX_BIND * (IFNAMSIZ + 1) + 8] = "off";
static char wgfp_owner_aliases[WGFP_OWNER_ALIASES_SIZE];
static DEFINE_PER_CPU(atomic64_t, wgfp_sealed);
static atomic64_t wgfp_declined, wgfp_rekeys, wgfp_punted;
static DEFINE_PER_CPU(atomic64_t, wgfp_rx_frames);
static DEFINE_PER_CPU(atomic64_t, wgfp_rx_stack);

struct wgfp_l2_path {
	int phys_ifindex;
	u8 peer_mac[ETH_ALEN];
	u8 src_mac[ETH_ALEN];
	u16 vlan_vid;
	u16 pppoe_sid;
	bool has_vlan;
	bool has_pppoe;
};

/* Resolve a logical outer egress/ingress device (physical DPAA port, single
 * 802.1Q VLAN vif, PPPoE session, or PPPoE-over-802.1Q) to its physical
 * Ethernet device ifindex and L2 encapsulation parameters.  Caller holds RCU. */
static bool wgfp_resolve_l2_path(struct net_device *dev,
				 struct wgfp_l2_path *out)
{
	memset(out, 0, sizeof(*out));
	if (!dev || !netif_running(dev))
		return false;
	if (dev->type == ARPHRD_PPP) {
		struct net_device_path_stack stack;
		u8 zero[ETH_ALEN] = {};
		bool seen_pppoe = false, seen_vlan = false, seen_eth = false;
		int i;

		if (dev_fill_forward_path(dev, zero, &stack) < 0)
			return false;
		for (i = 0; i < stack.num_paths; i++) {
			const struct net_device_path *path = &stack.path[i];

			if (!path->dev)
				return false;
			if (is_zero_ether_addr(out->src_mac))
				ether_addr_copy(out->src_mac, path->dev->dev_addr);
			switch (path->type) {
			case DEV_PATH_PPPOE:
				if (i != 0 || seen_pppoe || path->dev != dev ||
				    path->encap.proto != htons(ETH_P_PPP_SES) ||
				    !path->encap.id ||
				    !is_valid_ether_addr(path->encap.h_dest))
					return false;
				ether_addr_copy(out->peer_mac, path->encap.h_dest);
				out->pppoe_sid = path->encap.id;
				out->has_pppoe = true;
				seen_pppoe = true;
				break;
			case DEV_PATH_VLAN:
				if (!seen_pppoe || seen_vlan ||
				    path->encap.proto != htons(ETH_P_8021Q) ||
				    !path->encap.id ||
				    path->encap.id > VLAN_VID_MASK ||
				    !netif_running(path->dev) ||
				    !net_eq(dev_net(path->dev), dev_net(dev)))
					return false;
				out->vlan_vid = path->encap.id;
				out->has_vlan = true;
				seen_vlan = true;
				break;
			case DEV_PATH_ETHERNET:
				if (seen_eth || i != stack.num_paths - 1 ||
				    path->dev->type != ARPHRD_ETHER ||
				    !netif_running(path->dev) ||
				    !net_eq(dev_net(path->dev), dev_net(dev)))
					return false;
				out->phys_ifindex = path->dev->ifindex;
				seen_eth = true;
				break;
			default:
				return false;
			}
		}
		return seen_pppoe && seen_eth && out->phys_ifindex > 0 &&
		       is_valid_ether_addr(out->src_mac);
	}
	if (is_vlan_dev(dev)) {
		struct net_device *real;
		u16 vid;

		if (vlan_dev_vlan_proto(dev) != htons(ETH_P_8021Q))
			return false;
		vid = vlan_dev_vlan_id(dev);
		if (!vid || vid > VLAN_VID_MASK)
			return false;
		real = vlan_dev_real_dev(dev);
		if (!real || is_vlan_dev(real) || real->type != ARPHRD_ETHER ||
		    !netif_running(real) ||
		    !net_eq(dev_net(real), dev_net(dev)))
			return false;
		out->phys_ifindex = real->ifindex;
		ether_addr_copy(out->src_mac, dev->dev_addr);
		out->vlan_vid = vid;
		out->has_vlan = true;
		return true;
	}
	if (dev->type != ARPHRD_ETHER)
		return false;
	out->phys_ifindex = dev->ifindex;
	ether_addr_copy(out->src_mac, dev->dev_addr);
	return true;
}

static void wgfp_tmpl_build_l2(struct wgfp_tmpl *t,
			       const struct wgfp_l2_path *p,
			       const u8 *dst_mac, __be16 l3_proto)
{
	u8 *pos = t->hdr;

	ether_addr_copy(pos, dst_mac);
	ether_addr_copy(pos + ETH_ALEN, p->src_mac);
	pos += 2 * ETH_ALEN;
	if (p->has_vlan) {
		put_unaligned_be16(ETH_P_8021Q, pos);
		put_unaligned_be16(p->vlan_vid & VLAN_VID_MASK, pos + 2);
		pos += VLAN_HLEN;
	}
	if (p->has_pppoe) {
		put_unaligned_be16(ETH_P_PPP_SES, pos);
		pos += 2;
		t->pppoe_off = pos - t->hdr;
		pos[0] = 0x11;	/* PPPoE version 1, type 1 */
		pos[1] = 0x00;	/* code = session data */
		put_unaligned_be16(p->pppoe_sid, pos + 2);
		put_unaligned_be16(0, pos + 4);	/* filled per packet */
		put_unaligned_be16(l3_proto == htons(ETH_P_IPV6) ?
				   PPP_IPV6 : PPP_IP, pos + 6);
		pos += WGFP_PPPOE_HLEN;
	} else {
		memcpy(pos, &l3_proto, sizeof(l3_proto));
		pos += 2;
		t->pppoe_off = 0;
	}
	t->l2_hlen = pos - t->hdr;
}

static bool wgfp_tmpl_l2(struct wgfp_tmpl *t, struct net_device *dev,
			 struct neigh_table *tbl, const void *nh, __be16 proto)
{
	struct ethhdr *eth = (struct ethhdr *)t->hdr;
	struct neighbour *n;

	n = tbl == &arp_tbl ? __ipv4_neigh_lookup(dev, *(const u32 *)nh) :
	    __ipv6_neigh_lookup(dev, nh);
	if (!n || !(n->nud_state & NUD_VALID)) {
		if (!n) {
			n = __neigh_create(tbl, nh, dev, true);
			if (IS_ERR(n))
				return false;
			neigh_event_send(n, NULL);
		}
		neigh_release(n);
		return false;
	}
	read_lock_bh(&n->lock);
	ether_addr_copy(eth->h_dest, n->ha);
	read_unlock_bh(&n->lock);
	neigh_release(n);
	ether_addr_copy(eth->h_source, dev->dev_addr);
	eth->h_proto = proto;
	t->ifindex = dev->ifindex;
	t->l2_hlen = ETH_HLEN;
	t->pppoe_off = 0;
	return true;
}

static bool wgfp_tmpl_l2_path(struct wgfp_tmpl *t, struct net_device *dev,
			      const struct wgfp_l2_path *p,
			      struct neigh_table *tbl, const void *nh,
			      __be16 proto)
{
	u8 dst_mac[ETH_ALEN];

	if (p->has_pppoe) {
		ether_addr_copy(dst_mac, p->peer_mac);
	} else {
		struct neighbour *n;

		n = tbl == &arp_tbl ? __ipv4_neigh_lookup(dev, *(const u32 *)nh) :
		    __ipv6_neigh_lookup(dev, nh);
		if (!n || !(n->nud_state & NUD_VALID)) {
			if (!n) {
				n = __neigh_create(tbl, nh, dev, true);
				if (IS_ERR(n))
					return false;
				neigh_event_send(n, NULL);
			}
			neigh_release(n);
			return false;
		}
		read_lock_bh(&n->lock);
		ether_addr_copy(dst_mac, n->ha);
		read_unlock_bh(&n->lock);
		neigh_release(n);
	}
	wgfp_tmpl_build_l2(t, p, dst_mac, proto);
	t->ifindex = p->phys_ifindex;
	t->route_ifindex = dev->ifindex;
	return true;
}

static struct wgfp_tmpl *wgfp_tmpl4(struct wgfp_pst *pst, const struct endpoint *ep)
{
	struct wg_device *wg = pst->peer->device;
	struct flowi4 fl = {
		.daddr = ep->addr4.sin_addr.s_addr,
		.saddr = ep->src4.s_addr,
		.fl4_dport = ep->addr4.sin_port,
		.flowi4_mark = wg->fwmark,
		.flowi4_proto = IPPROTO_UDP,
	};
	struct wgfp_l2_path path;
	struct net_device *phys_dev = NULL;
	struct wgfp_tmpl *t = NULL;
	struct rtable *rt;
	struct iphdr *ip;
	struct udphdr *udp;
	bool resolved;
	__be32 nh;

	rt = ip_route_output_key(dev_net(pst->wgdev), &fl);
	if (IS_ERR(rt))
		return NULL;
	rcu_read_lock();
	resolved = wgfp_resolve_l2_path(rt->dst.dev, &path);
	rcu_read_unlock();
	if (!resolved)
		goto out;
	phys_dev = dev_get_by_index(dev_net(pst->wgdev), path.phys_ifindex);
	if (!phys_dev)
		goto out;
	t = kzalloc(sizeof(*t), GFP_KERNEL);
	if (!t)
		goto out_put;
	t->egress = dpaa_wgfp_egress(phys_dev);	/* < 0: not a DPAA port */
	nh = rt_nexthop(rt, fl.daddr);
	/* outer family policy: the physical port under the tunnel */
	if (t->egress < 0 || !dpaa_wgfp_family_ok(phys_dev, AF_INET) ||
	    !wgfp_tmpl_l2_path(t, rt->dst.dev, &path, &arp_tbl, &nh,
			       htons(ETH_P_IP))) {
		kfree(t);
		t = NULL;
		goto out_put;
	}
	t->family = AF_INET;
	t->mtu = dst_mtu(&rt->dst);
	t->hlen = t->l2_hlen + sizeof(*ip) + sizeof(*udp);
	ip = (struct iphdr *)(t->hdr + t->l2_hlen);
	ip->version = 4;
	ip->ihl = 5;
	ip->frag_off = htons(IP_DF);
	ip->ttl = ip4_dst_hoplimit(&rt->dst);
	ip->protocol = IPPROTO_UDP;
	ip->saddr = fl.saddr ?: inet_select_addr(rt->dst.dev, nh, RT_SCOPE_UNIVERSE);
	ip->daddr = fl.daddr;
	udp = (struct udphdr *)(ip + 1);
	udp->source = htons(wg->incoming_port);
	udp->dest = fl.fl4_dport;
out_put:
	dev_put(phys_dev);
out:
	ip_rt_put(rt);
	return t;
}

/* IPv6 outer (C2): the UDP checksum is mandatory, so the seal path sums the
 * datagram in software after encrypting it. */
static struct wgfp_tmpl *wgfp_tmpl6(struct wgfp_pst *pst, const struct endpoint *ep)
{
#if IS_ENABLED(CONFIG_IPV6)
	struct wg_device *wg = pst->peer->device;
	struct net *net = dev_net(pst->wgdev);
	struct flowi6 fl = {
		.daddr = ep->addr6.sin6_addr,
		.saddr = ep->src6,
		.fl6_dport = ep->addr6.sin6_port,
		.fl6_sport = htons(wg->incoming_port),
		.flowi6_mark = wg->fwmark,
		.flowi6_oif = ep->addr6.sin6_scope_id,
		.flowi6_proto = IPPROTO_UDP,
	};
	struct wgfp_l2_path path;
	struct net_device *phys_dev = NULL;
	const struct in6_addr *nh;
	struct wgfp_tmpl *t = NULL;
	struct dst_entry *dst;
	struct ipv6hdr *ip6;
	struct udphdr *udp;
	bool resolved;

	dst = ip6_route_output(net, NULL, &fl);
	if (dst->error)
		goto out;
	if (ipv6_addr_any(&fl.saddr) &&
	    ipv6_dev_get_saddr(net, dst->dev, &fl.daddr, 0, &fl.saddr))
		goto out;
	rcu_read_lock();
	resolved = wgfp_resolve_l2_path(dst->dev, &path);
	rcu_read_unlock();
	if (!resolved)
		goto out;
	phys_dev = dev_get_by_index(net, path.phys_ifindex);
	if (!phys_dev)
		goto out;
	t = kzalloc(sizeof(*t), GFP_KERNEL);
	if (!t)
		goto out_put;
	t->egress = dpaa_wgfp_egress(phys_dev);
	nh = rt6_nexthop(dst_rt6_info(dst), &fl.daddr);
	if (t->egress < 0 || !dpaa_wgfp_family_ok(phys_dev, AF_INET6) ||
	    !wgfp_tmpl_l2_path(t, dst->dev, &path, &nd_tbl, nh,
			       htons(ETH_P_IPV6))) {
		kfree(t);
		t = NULL;
		goto out_put;
	}
	t->family = AF_INET6;
	t->mtu = dst_mtu(dst);
	t->hlen = t->l2_hlen + sizeof(*ip6) + sizeof(*udp);
	ip6 = (struct ipv6hdr *)(t->hdr + t->l2_hlen);
	ip6->version = 6;
	ip6->nexthdr = IPPROTO_UDP;
	ip6->hop_limit = ip6_dst_hoplimit(dst);
	ip6->saddr = fl.saddr;
	ip6->daddr = fl.daddr;
	udp = (struct udphdr *)(ip6 + 1);
	udp->source = fl.fl6_sport;
	udp->dest = fl.fl6_dport;
out_put:
	dev_put(phys_dev);
out:
	dst_release(dst);
	return t;
#else
	return NULL;
#endif
}

static void wgfp_pst_refresh(struct work_struct *w)
{
	struct wgfp_pst *pst = container_of(to_delayed_work(w), struct wgfp_pst,
					    refresh);
	struct wg_peer *peer = pst->peer;
	struct wgfp_tmpl *t = NULL, *old;
	struct endpoint ep;

	if (READ_ONCE(pst->stopping))
		return;
	read_lock_bh(&peer->endpoint_lock);
	ep = peer->endpoint;
	read_unlock_bh(&peer->endpoint_lock);
	/* No usable route, DPAA egress, neighbour or endpoint: no template,
	 * so frames punt to the stack instead of leaving with stale headers. */
	if (ep.addr.sa_family == AF_INET)
		t = wgfp_tmpl4(pst, &ep);
	else if (ep.addr.sa_family == AF_INET6)
		t = wgfp_tmpl6(pst, &ep);
	old = rcu_replace_pointer(pst->tmpl, t, true);
	if (old)
		kfree_rcu(old, rcu);
	wgfp_pst_rx_sync(pst);
	wgfp_rx_fold(pst);
	if (!READ_ONCE(pst->stopping))
		schedule_delayed_work(&pst->refresh, HZ);
}

/* ---- flow table ---- */

#define WGFP_FLOW_BITS		10
#define WGFP_FLOW_MAX		4096
#define WGFP_PST_BITS		6
#define WGFP_AGE_PERIOD		(2 * HZ)
#define WGFP_AGE_IDLE		5	/* periods without hits */
#define WGFP_FAIL_HOLD		(30 * HZ)
#define WGFP_BATCH		16	/* job arrays live on the stack */
#define WGFP_ADMIT		32	/* slow-path packets before a record */

enum { WGFP_LEARNING, WGFP_PENDING, WGFP_INSTALLING, WGFP_INSTALLED, WGFP_FAILED,
       WGFP_STUCK /* delete failed: hardware may still reference the record */ };

/* Writers hold wgfp_flows_lock; the sink path reads the table under RCU, so
 * entries are unhashed with hash_del_rcu() and freed with kfree_rcu(). */
struct wgfp_flow {
	struct hlist_node node;
	struct rcu_head rcu;
	struct dpaa_wgfp_tuple t;
	struct wgfp_nat nat;
	struct wgfp_pst *pst;
	int ifindex;
	u32 hash;
	u8 dst_mac[ETH_ALEN];	/* P3/P6 expected outer DMAC (port or VLAN MAC) */
	u8 state;
	bool del;
	bool gone;		/* unhashed, waiting for its grace period */
	bool rx;		/* P3: the peer's inbound outer flow */
	u8 slot;		/* sink index given to dpaa_wgfp_flow_add() */
	u8 idle;
	u64 pkts;
	unsigned long since;
};

static DEFINE_HASHTABLE(wgfp_flows, WGFP_FLOW_BITS);
static DEFINE_HASHTABLE(wgfp_psts, WGFP_PST_BITS);
static DEFINE_SPINLOCK(wgfp_flows_lock);
static unsigned int wgfp_nflows, wgfp_npsts;
static atomic64_t wgfp_installs, wgfp_withdraws, wgfp_install_fail, wgfp_withdraw_fail;
static atomic64_t wgfp_decline_withdraws, wgfp_aip_withdraws;
static void wgfp_flow_work_fn(struct work_struct *w);
static void wgfp_age_fn(struct work_struct *w);
static DECLARE_WORK(wgfp_flow_work, wgfp_flow_work_fn);
static DECLARE_DELAYED_WORK(wgfp_age_work, wgfp_age_fn);

/* Tuples are built zero-initialised, so the whole struct is the identity. */
static bool wgfp_tuple_eq(const struct dpaa_wgfp_tuple *a,
			  const struct dpaa_wgfp_tuple *b)
{
	return !memcmp(a, b, sizeof(*a));
}

static u32 wgfp_tuple_hash(const struct dpaa_wgfp_tuple *t)
{
	return jhash2((const u32 *)t, sizeof(*t) / sizeof(u32), 0);
}

/*
 * The flow of an inner packet at @l3 (@eth_proto from L2): plain IPv4 (no
 * options, not a fragment) or IPv6 without extension headers, carrying TCP or
 * UDP.  @avail bytes are readable at @l3; the packet has @total bytes from
 * @l3 on.  Fills @t and the packet length; false otherwise.
 */
static bool wgfp_parse(const u8 *l3, __be16 eth_proto, u32 avail, u32 total,
		       struct dpaa_wgfp_tuple *t, u32 *len)
{
	const __be16 *ports;

	if (eth_proto == htons(ETH_P_IP)) {
		const struct iphdr *iph = (const struct iphdr *)l3;

		if (avail < sizeof(*iph) + 4 || iph->version != 4 || iph->ihl != 5 ||
		    ip_is_fragment(iph) ||
		    (iph->protocol != IPPROTO_TCP && iph->protocol != IPPROTO_UDP))
			return false;
		*len = ntohs(iph->tot_len);
		if (*len < sizeof(*iph) + 4 || *len > total)
			return false;
		t->family = AF_INET;
		t->saddr = iph->saddr;
		t->daddr = iph->daddr;
		t->proto = iph->protocol;
		ports = (const __be16 *)(l3 + sizeof(*iph));
	} else if (eth_proto == htons(ETH_P_IPV6)) {
		const struct ipv6hdr *ip6 = (const struct ipv6hdr *)l3;

		if (avail < sizeof(*ip6) + 4 || ip6->version != 6 ||
		    (ip6->nexthdr != IPPROTO_TCP && ip6->nexthdr != IPPROTO_UDP))
			return false;
		*len = sizeof(*ip6) + ntohs(ip6->payload_len);
		if (*len < sizeof(*ip6) + 4 || *len > total)
			return false;
		t->family = AF_INET6;
		t->saddr6 = ip6->saddr;
		t->daddr6 = ip6->daddr;
		t->proto = ip6->nexthdr;
		ports = (const __be16 *)(l3 + sizeof(*ip6));
	} else {
		return false;
	}
	t->sport = ports[0];
	t->dport = ports[1];
	return true;
}

static bool wgfp_parse_l2(const u8 *data, u32 len, u32 *l3_off,
			  u32 *l3_avail, __be16 *l3_proto, __be16 *vlan_vid,
			  __be16 *pppoe_sid)
{
	__be16 proto;
	u32 off = ETH_HLEN;
	u32 avail;

	if (len < ETH_HLEN)
		return false;
	*vlan_vid = 0;
	*pppoe_sid = 0;
	proto = ((const struct ethhdr *)data)->h_proto;
	if (proto == htons(ETH_P_8021Q)) {
		__be16 tci;

		if (len < off + VLAN_HLEN)
			return false;
		tci = get_unaligned((const __be16 *)(data + off));
		*vlan_vid = tci & htons(VLAN_VID_MASK);
		if (!*vlan_vid)
			return false;
		proto = get_unaligned((const __be16 *)(data + off + 2));
		off += VLAN_HLEN;
	}
	if (proto == htons(ETH_P_PPP_SES)) {
		__be16 sid, ppp_proto;
		u16 plen;

		if (len < off + WGFP_PPPOE_HLEN)
			return false;
		if (data[off] != 0x11 || data[off + 1] != 0x00)
			return false;
		sid = get_unaligned((const __be16 *)(data + off + 2));
		plen = ntohs(get_unaligned((const __be16 *)(data + off + 4)));
		ppp_proto = get_unaligned((const __be16 *)(data + off + 6));
		if (!sid || plen < 2 || off + 6 + plen > len)
			return false;
		if (ppp_proto == htons(PPP_IP))
			proto = htons(ETH_P_IP);
		else if (ppp_proto == htons(PPP_IPV6))
			proto = htons(ETH_P_IPV6);
		else
			return false;
		*pppoe_sid = sid;
		off += WGFP_PPPOE_HLEN;
		avail = plen - 2;
	} else {
		avail = len - off;
	}
	if (proto != htons(ETH_P_IP) && proto != htons(ETH_P_IPV6))
		return false;
	*l3_off = off;
	*l3_avail = avail;
	*l3_proto = proto;
	return true;
}

/* Writer side, under wgfp_flows_lock. */
static struct wgfp_flow *wgfp_flow_find(const struct dpaa_wgfp_tuple *t,
					int ifindex, u32 hash)
{
	struct wgfp_flow *fl;

	hash_for_each_possible(wgfp_flows, fl, node, hash)
		if (fl->ifindex == ifindex && wgfp_tuple_eq(&fl->t, t))
			return fl;
	return NULL;
}

/* Same tuple and ingress port (a sink frame carries its port). */
static struct wgfp_flow *wgfp_flow_find_tuple(const struct dpaa_wgfp_tuple *t,
					      int ifindex, u32 hash)
{
	struct wgfp_flow *fl;

	hash_for_each_possible(wgfp_flows, fl, node, hash)
		if (fl->ifindex == ifindex && wgfp_tuple_eq(&fl->t, t))
			return fl;
	return NULL;
}

/* Families the TX path handles; which of them a port admits is the port's
 * offload policy (dpaa_wgfp_family_ok()). */
static bool wgfp_tx_family_allowed(u8 family)
{
	return family == AF_INET || family == AF_INET6;
}

/* Sink side, under RCU; the frame's port must be the flow's port. */
static struct wgfp_flow *wgfp_flow_lookup(const struct dpaa_wgfp_tuple *t,
					  int ifindex)
{
	struct wgfp_flow *fl;
	struct wgfp_flow *match = NULL;

	hash_for_each_possible_rcu(wgfp_flows, fl, node, wgfp_tuple_hash(t))
		if (fl->ifindex == ifindex && wgfp_tuple_eq(&fl->t, t)) {
			if (match) {
				pr_warn_ratelimited(
					"wgfp: ambiguous sink tuple; dropping frame\n");
				return NULL;
			}
			match = fl;
		}
	return match;
}

static void wgfp_flow_free_locked(struct wgfp_flow *fl)
{
	if (fl->rx && fl->pst->rxfl == fl)
		fl->pst->rxfl = NULL;
	hash_del_rcu(&fl->node);
	fl->gone = true;
	fl->pst->nflows--;
	wgfp_nflows--;
	kfree_rcu(fl, rcu);
}

/* Mark @fl for withdrawal (the flow worker deletes the record). */
static void wgfp_flow_withdraw(struct wgfp_flow *fl, atomic64_t *why)
{
	spin_lock_bh(&wgfp_flows_lock);
	if (!READ_ONCE(fl->pst->stopping) && !fl->gone && !fl->del) {
		fl->del = true;
		if (why)
			atomic64_inc(why);
		queue_work(system_wq, &wgfp_flow_work);
	}
	spin_unlock_bh(&wgfp_flows_lock);
}

/* ---- sink path ---- */

/* ---- P6: zero-skb decrypt (plans/ASK2-WIREGUARD-FASTPATH-PLAN.md 4b) ----
 *
 * wgfp_rx_fast (needs wgfp_rx): a data message of the peer's current keypair
 * that reaches a P3 sink is decrypted in place in the DPAA buffer, checked
 * against upstream's own replay window (wg_fastpath_rx_validate()) and feeds
 * upstream's receive timers and stats.  1 = the plaintext is then copied into
 * an skb on the wg device, as upstream's receive path would hand it on (still
 * no crypto queue, no per-peer serial poll).  2 = an inner flow the stack has
 * forwarded leaves directly: outer headers dropped, the learned Ethernet
 * header written, TTL / hop limit decremented, dpaa_wgfp_xmit() on the egress
 * port's no-confirm TX FQ.
 *
 * An inner flow is learned (struct wgfp_rflow) only from a packet of a bound
 * wg device seen at POST_ROUTING, i.e. after routing and the forward
 * firewall, with an established non-NAT conntrack entry, a non-xfrm route to
 * an ASK2 DPAA port that offloads the family, and a valid neighbour.  A record
 * lives WGFP_RFLOW_LIFE: then the next packet takes the stack again and, if
 * the stack still forwards it, renews the record.  So route, neighbour, MTU,
 * firewall, family-policy and AllowedIPs changes take effect within a second,
 * at the cost of one stack packet per flow per second.
 *
 * Everything else goes on to the P3 path untouched (other keypairs,
 * handshakes, keepalives, VLAN-tagged or non-local outer frames).  After a
 * successful decrypt the buffer holds plaintext, so from there on a frame is
 * forwarded, handed to the stack or dropped, never punted.  The UDP checksum
 * is not verified: the AEAD tag authenticates every byte that is used.
 */
static unsigned int wgfp_rx_fast = 2;	/* P6 direct forward via WGFP TX slots */
module_param(wgfp_rx_fast, uint, 0600);
MODULE_PARM_DESC(wgfp_rx_fast, "P6 decrypt on P3 sinks: 0 off (P3), 1 decrypt then stack, 2 also forward learned inner flows (default)");

/* P7, see wg_fastpath_rx_tail() */
static bool wgfp_rx_tail;
module_param(wgfp_rx_tail, bool, 0600);
MODULE_PARM_DESC(wgfp_rx_tail, "P7: forward learned inner flows directly at the end of the upstream receive path");

static DEFINE_PER_CPU(atomic64_t, wgfp_p6_fwd);
static DEFINE_PER_CPU(atomic64_t, wgfp_p6_stack);
static atomic64_t wgfp_p6_punt, wgfp_p6_replay, wgfp_p6_auth, wgfp_p6_err,
		  wgfp_p6_aip, wgfp_p6_nomem, wgfp_p6_learn;

#define WGFP_RFLOW_BITS		10
#define WGFP_RFLOW_MAX		4096
#define WGFP_RFLOW_LIFE		HZ
/* skb headroom for the stack hand-off: what the DPAA TX path wants in front */
#define WGFP_RX_HEADROOM	128

struct wgfp_rflow {
	struct hlist_node node;		/* wgfp_rflows, writers under wgfp_flows_lock */
	struct rcu_head rcu;
	struct dpaa_wgfp_tuple t;	/* pre-NAT inner flow (as decrypted by WG) */
	struct wgfp_nat nat;		/* inner NAT rewrite to apply before TX */
	int wg_ifindex;			/* the wg device it arrives on */
	u64 peer_id;			/* wg_peer.internal_id owning the source */
	u32 mtu;			/* egress route MTU */
	unsigned long expires;
	struct wgfp_tmpl l2;		/* Ethernet header, egress handle and port */
};

static DEFINE_HASHTABLE(wgfp_rflows, WGFP_RFLOW_BITS);
static unsigned int wgfp_nrflows;

static struct wgfp_rflow *wgfp_rflow_lookup(const struct dpaa_wgfp_tuple *t,
					    int wg_ifindex, u32 hash)
{
	struct wgfp_rflow *r;

	hash_for_each_possible_rcu(wgfp_rflows, r, node, hash)
		if (r->wg_ifindex == wg_ifindex && wgfp_tuple_eq(&r->t, t))
			return r;
	return NULL;
}

/* Free expired records (@all: every record; no reader or writer is left). */
static void wgfp_rflows_reap(bool all)
{
	struct wgfp_rflow *r;
	struct hlist_node *tmp;
	unsigned int bkt;

	spin_lock_bh(&wgfp_flows_lock);
	hash_for_each_safe(wgfp_rflows, bkt, tmp, r, node) {
		if (!all && time_before(jiffies, r->expires))
			continue;
		hash_del_rcu(&r->node);
		wgfp_nrflows--;
		kfree_rcu(r, rcu);
	}
	spin_unlock_bh(&wgfp_flows_lock);
}

/* An egress handle is allocated in process context; the hook asks for it,
 * at most once a second (a port that cannot get one must not storm). */
static int wgfp_eg_want;
static unsigned long wgfp_eg_next;

static void wgfp_eg_work_fn(struct work_struct *w)
{
	struct net_device *dev = dev_get_by_index(&init_net, xchg(&wgfp_eg_want, 0));

	if (dev) {
		dpaa_wgfp_egress(dev);
		dev_put(dev);
	}
}
static DECLARE_WORK(wgfp_eg_work, wgfp_eg_work_fn);

#if IS_ENABLED(CONFIG_NETFILTER)
static unsigned int wgfp_rflow_hook(void *priv, struct sk_buff *skb,
				    const struct nf_hook_state *state)
{
	struct wgfp_binds *bs = rcu_dereference(wgfp_bs);
	struct net_device *out = state->out, *wgdev = NULL;
	struct dpaa_wgfp_tuple t = {}, orig = {};
	struct wgfp_nat nat = {};
	struct dst_entry *dst = skb_dst(skb);
	struct wgfp_rflow *r, *old;
	struct wg_peer *peer;
	u8 hb[sizeof(struct ipv6hdr) + 14];
	unsigned int i, hl;
	const u8 *l3;
	u32 hash, len, total;
	int egress;
	bool ok;

	/* the records serve P6 direct forward and the P7 tail */
	if (!((READ_ONCE(wgfp_rx_fast) >= 2 && READ_ONCE(wgfp_rx)) ||
	      READ_ONCE(wgfp_rx_tail)) || !bs ||
	    READ_ONCE(bs->stopping) || !out || !dst || dst_xfrm(dst) ||
	    !skb->skb_iif)
		return NF_ACCEPT;
	/* skb_iif is only a bound wg device for a packet it received and the
	 * stack forwarded: locally generated packets carry 0 */
	for (i = 0; i < bs->n; i++)
		if (bs->wgdev[i]->ifindex == skb->skb_iif)
			wgdev = bs->wgdev[i];
	if (!wgdev || skb->len <= skb_network_offset(skb))
		return NF_ACCEPT;
	total = skb->len - skb_network_offset(skb);
	l3 = skb_header_pointer(skb, skb_network_offset(skb),
				min_t(u32, sizeof(hb), total), hb);
	if (!l3 || !wgfp_parse(l3, skb->protocol, min_t(u32, sizeof(hb), total),
			       total, &t, &len))
		return NF_ACCEPT;
	hl = t.family == AF_INET ? sizeof(struct iphdr) : sizeof(struct ipv6hdr);
	/* a closing TCP flow is not (re)learned */
	if (t.proto == IPPROTO_TCP &&
	    (min_t(u32, sizeof(hb), total) < hl + 14 ||
	     (l3[hl + 13] & (TCPHDR_FIN | TCPHDR_RST | TCPHDR_SYN))))
		return NF_ACCEPT;
	if (!wgfp_ct_admitted(state->net, skb, &t, &orig, &nat))
		return NF_ACCEPT;
	/* Keep AllowedIPs src check exact on skb: decline egress-side SNAT */
	if (nat.flags & WGFP_NAT_SRC_IP)
		return NF_ACCEPT;
	hash = wgfp_tuple_hash(&orig);
	r = wgfp_rflow_lookup(&orig, wgdev->ifindex, hash);
	if (r && time_before(jiffies, READ_ONCE(r->expires)))
		return NF_ACCEPT;
	/* ponytail: a VLAN egress takes the stack hand-off; direct transmit
	 * needs the tag in the template and the physical port's handle */
	if (is_vlan_dev(out) || !dpaa_wgfp_family_ok(out, t.family))
		return NF_ACCEPT;
	egress = dpaa_wgfp_egress_find(out);
	if (egress < 0) {
		if (time_after(jiffies, READ_ONCE(wgfp_eg_next))) {
			WRITE_ONCE(wgfp_eg_next, jiffies + HZ);
			WRITE_ONCE(wgfp_eg_want, out->ifindex);
			schedule_work(&wgfp_eg_work);
		}
		return NF_ACCEPT;
	}
	/* the inner source must belong to a peer of this device */
	peer = wg_allowedips_lookup_src(&((struct wg_device *)netdev_priv(wgdev))->peer_allowedips,
					skb);
	if (!peer)
		return NF_ACCEPT;
	r = kzalloc(sizeof(*r), GFP_ATOMIC);
	if (!r) {
		wg_peer_put(peer);
		return NF_ACCEPT;
	}
	r->peer_id = peer->internal_id;
	wg_peer_put(peer);
	if (t.family == AF_INET) {
		const struct rtable *rt = skb_rtable(skb);
		__be32 nh = rt_nexthop(rt, ip_hdr(skb)->daddr);

		ok = rt->rt_gw_family != AF_INET6 &&
		     wgfp_tmpl_l2(&r->l2, out, &arp_tbl, &nh, htons(ETH_P_IP));
	} else {
		ok = wgfp_tmpl_l2(&r->l2, out, &nd_tbl,
				  rt6_nexthop(dst_rt6_info(dst), &ipv6_hdr(skb)->daddr),
				  htons(ETH_P_IPV6));
	}
	if (!ok) {
		kfree(r);
		return NF_ACCEPT;
	}
	r->t = orig;
	r->nat = nat;
	r->wg_ifindex = wgdev->ifindex;
	r->mtu = dst_mtu(dst);
	r->expires = jiffies + WGFP_RFLOW_LIFE;
	r->l2.egress = egress;
	r->l2.family = t.family;
	r->l2.hlen = ETH_HLEN;

	spin_lock_bh(&wgfp_flows_lock);
	if (unlikely(atomic_read(&wgfp_quiesce_active))) {
		kfree(r);
		r = NULL;
	} else if ((old = wgfp_rflow_lookup(&orig, wgdev->ifindex, hash))) {
		hlist_replace_rcu(&old->node, &r->node);
		kfree_rcu(old, rcu);
	} else if (wgfp_nrflows < WGFP_RFLOW_MAX) {
		hash_add_rcu(wgfp_rflows, &r->node, hash);
		wgfp_nrflows++;
	} else {
		kfree(r);
		r = NULL;
	}
	spin_unlock_bh(&wgfp_flows_lock);
	if (r)
		atomic64_inc(&wgfp_p6_learn);
	return NF_ACCEPT;
}

/* After every other POST_ROUTING hook (SNAT included): what is seen here is
 * what the stack actually sends. */
static const struct nf_hook_ops wgfp_nf_ops[] = {
	{ .hook = wgfp_rflow_hook, .pf = NFPROTO_IPV4,
	  .hooknum = NF_INET_POST_ROUTING, .priority = INT_MAX },
	{ .hook = wgfp_rflow_hook, .pf = NFPROTO_IPV6,
	  .hooknum = NF_INET_POST_ROUTING, .priority = INT_MAX },
};
static bool wgfp_nf_on;

static void wgfp_nf_register(void)
{
	int err = nf_register_net_hooks(&init_net, wgfp_nf_ops, ARRAY_SIZE(wgfp_nf_ops));

	if (err)
		pr_warn("wgfp: no P6 inner-flow learning (%d); wgfp_rx_fast 2 acts as 1\n", err);
	wgfp_nf_on = !err;
}

static void wgfp_nf_unregister(void)
{
	if (wgfp_nf_on)
		nf_unregister_net_hooks(&init_net, wgfp_nf_ops, ARRAY_SIZE(wgfp_nf_ops));
	wgfp_nf_on = false;
}
#else
static void wgfp_nf_register(void) { }
static void wgfp_nf_unregister(void) { }
#endif

/*
 * P7 (wgfp_rx_tail): the end of upstream's ordered receive path.  Upstream
 * keeps decrypting on every CPU and keeps the per-peer order; a decrypted
 * inner packet of a learned flow (struct wgfp_rflow, same admission and 1 s
 * lifetime as P6) then leaves straight for its egress port instead of
 * napi_gro_receive(): TTL / hop limit decremented, the learned Ethernet header
 * pushed, dev_direct_xmit() on a flow-hashed queue (a flow keeps its queue
 * when the peer's NAPI thread changes CPU).  GRO, ip_forward, the forward and
 * postrouting hooks, the qdisc and GSO are skipped.  Called from
 * wg_packet_consume_data_done() after the AllowedIPs check, NAPI context;
 * false leaves the skb to upstream untouched.
 */
static DEFINE_PER_CPU(atomic64_t, wgfp_p7_fwd);
static atomic64_t wgfp_p7_drop;

bool wg_fastpath_rx_tail(struct wg_peer *peer, struct sk_buff *skb)
{
	struct dpaa_wgfp_tuple t = {};
	const struct wgfp_rflow *r;
	struct net_device *eg;
	u8 hb[sizeof(struct ipv6hdr) + 14];
	u32 hl, len, n, q;
	const u8 *l3;
	bool done = false;

	if (!READ_ONCE(wgfp_rx_tail) || !skb->len)
		return false;
	n = min_t(u32, sizeof(hb), skb->len);
	l3 = skb_header_pointer(skb, 0, n, hb);
	if (!l3 || !wgfp_parse(l3, skb->protocol, n, skb->len, &t, &len) ||
	    len != skb->len)
		return false;
	hl = t.family == AF_INET ? sizeof(struct iphdr) : sizeof(struct ipv6hdr);
	/* SYN, FIN and RST go through the stack (conntrack sees the flow end) */
	if (t.proto == IPPROTO_TCP &&
	    (n < hl + 14 || (l3[hl + 13] & (TCPHDR_FIN | TCPHDR_RST | TCPHDR_SYN))))
		return false;
	/* TTL 1 needs an ICMP error; ip_rcv() would drop a bad header */
	if (t.family == AF_INET ? (((const struct iphdr *)l3)->ttl <= 1 ||
				   ip_fast_csum(l3, 5)) :
	    ((const struct ipv6hdr *)l3)->hop_limit <= 1)
		return false;
	rcu_read_lock();
	r = wgfp_rflow_lookup(&t, peer->device->dev->ifindex, wgfp_tuple_hash(&t));
	if (!r || time_after(jiffies, READ_ONCE(r->expires)) ||
	    r->peer_id != peer->internal_id || skb->len > r->mtu ||
	    r->nat.flags)
		goto out;
	eg = dev_get_by_index_rcu(&init_net, r->l2.ifindex);
	if (!eg || !(eg->flags & IFF_UP) ||
	    !pskb_may_pull(skb, hl) || skb_cow_head(skb, ETH_HLEN))
		goto out;
	q = reciprocal_scale(skb_get_hash(skb), eg->real_num_tx_queues);
	if (t.family == AF_INET)
		ip_decrease_ttl(ip_hdr(skb));
	else
		ipv6_hdr(skb)->hop_limit--;
	memcpy(skb_push(skb, ETH_HLEN), r->l2.hdr, ETH_HLEN);
	skb_reset_mac_header(skb);
	skb->mac_len = ETH_HLEN;
	skb->dev = eg;
	skb->ip_summed = CHECKSUM_NONE;	/* inner checksums are intact */
	if (dev_xmit_complete(dev_direct_xmit(skb, q)))
		dpaa_wgfp_stat_inc(&wgfp_p7_fwd);
	else
		atomic64_inc(&wgfp_p7_drop);	/* dev_direct_xmit() freed it */
	done = true;
out:
	rcu_read_unlock();
	return done;
}

/* P6 stack hand-off: the plaintext inner packet at @pt as an skb on the wg
 * device, prepared like wg_packet_consume_data_done() does. */
static int wgfp_rx_fast_stack(struct sk_buff *skb, struct wgfp_pst *pst,
			      u32 len, u8 ds, u32 msg_len)
{
	struct net_device *dev = pst->wgdev;
	struct wg_peer *routed;

	skb_trim(skb, len);
	skb_reset_mac_header(skb);
	skb_reset_network_header(skb);
	/* the Poly1305 tag covered it: as upstream, no checksum to verify */
	skb->ip_summed = CHECKSUM_UNNECESSARY;
	skb->csum_level = ~0;
	if ((skb->data[0] >> 4) == 4) {
		skb->protocol = htons(ETH_P_IP);
		INET_ECN_decapsulate(skb, ds, ip_hdr(skb)->tos);
	} else {
		skb->protocol = htons(ETH_P_IPV6);
		INET_ECN_decapsulate(skb, ds, ipv6_get_dsfield(ipv6_hdr(skb)));
	}
	routed = wg_allowedips_lookup_src(&pst->peer->device->peer_allowedips, skb);
	wg_peer_put(routed);
	if (unlikely(routed != pst->peer)) {
		DEV_STATS_INC(dev, rx_errors);
		DEV_STATS_INC(dev, rx_frame_errors);
		atomic64_inc(&wgfp_p6_aip);
		kfree_skb(skb);
		return 1;
	}
	wgfp_rx_stats(pst, msg_len);
	dpaa_wgfp_stat_inc(&wgfp_p6_stack);
	dpaa_wgfp_orp_settle();
	netif_receive_skb(skb);
	return 1;
}

/* P6 direct forward of the decrypted inner packet at @pt (@len bytes) of a
 * learned flow.  When @tx is non-NULL (speculative WGFP TX slot with @pt at
 * tx->buf + DPAA_WGFP_TX_HEADROOM + ETH_HLEN), forwards zero-copy if the
 * learned egress matches (@hint_eg, tx->ifindex) or copies into a target TX
 * slot on multi-egress mismatch.  When @tx is NULL, upgrades @skb to a WGFP
 * TX slot (preserving ORP order) or falls back to dpaa_wgfp_xmit_skb().
 * Returns true when consumed; false leaves @tx/@skb untouched. */
static bool wgfp_rx_forward(struct sk_buff *skb,
			    struct dpaa_wgfp_tx_buf *tx, int hint_eg,
			    u8 *pt, struct wgfp_pst *pst,
			    u32 len, u32 msg_len)
{
	struct dpaa_wgfp_tuple t = {};
	const struct wgfp_rflow *r;
	struct dpaa_wgfp_tx_buf tx2;
	u32 hl, tlen;
	u8 *hdr;

	if (!wgfp_parse(pt, (pt[0] >> 4) == 4 ? htons(ETH_P_IP) : htons(ETH_P_IPV6),
			len, len, &t, &tlen))
		return false;
	hl = t.family == AF_INET ? sizeof(struct iphdr) : sizeof(struct ipv6hdr);
	/* SYN, FIN and RST go through the stack (conntrack sees the flow end) */
	if (t.proto == IPPROTO_TCP &&
	    (len < hl + 14 || (pt[hl + 13] & (TCPHDR_FIN | TCPHDR_RST | TCPHDR_SYN))))
		return false;
	r = wgfp_rflow_lookup(&t, pst->wgdev->ifindex, wgfp_tuple_hash(&t));
	if (!r || time_after(jiffies, READ_ONCE(r->expires)) ||
	    r->peer_id != pst->peer->internal_id || len > r->mtu ||
	    !wgfp_nat_len_ok(len, t.family, t.proto, &r->nat))
		return false;
	if (t.family == AF_INET) {
		const struct iphdr *iph = (const struct iphdr *)pt;

		/* ip_rcv() would drop a bad header; TTL 1 needs an ICMP error */
		if (iph->ttl <= 1 || ip_fast_csum((const u8 *)iph, iph->ihl))
			return false;
	} else {
		const struct ipv6hdr *ip6 = (const struct ipv6hdr *)pt;

		if (ip6->hop_limit <= 1)
			return false;
	}
	if (unlikely(READ_ONCE(pst->last_rx_egress) != r->l2.egress ||
		     READ_ONCE(pst->last_rx_ifindex) != r->l2.ifindex)) {
		WRITE_ONCE(pst->last_rx_ifindex, r->l2.ifindex);
		WRITE_ONCE(pst->last_rx_egress, r->l2.egress);
	}
	if (tx && likely(r->l2.egress == hint_eg &&
			 r->l2.ifindex == tx->ifindex)) {
		if (t.family == AF_INET)
			ip_decrease_ttl((struct iphdr *)pt);
		else
			((struct ipv6hdr *)pt)->hop_limit--;
		if (unlikely(r->nat.flags))
			wgfp_apply_nat(pt, len, t.family, t.proto, &r->nat);
		memcpy((u8 *)tx->buf + DPAA_WGFP_TX_HEADROOM, r->l2.hdr, ETH_HLEN);
		wgfp_rx_stats(pst, msg_len);
		dpaa_wgfp_stat_inc(&wgfp_p6_fwd);
		dpaa_wgfp_tx_submit(NULL, tx, DPAA_WGFP_TX_HEADROOM,
				    ETH_HLEN + len);
		return true;
	}
	if (ETH_HLEN + len <= DPAA_WGFP_TX_BUF_SIZE &&
	    dpaa_wgfp_tx_alloc(r->l2.egress, r->l2.ifindex,
			       ETH_HLEN + len, &tx2)) {
		u8 *dst = (u8 *)tx2.buf + DPAA_WGFP_TX_HEADROOM + ETH_HLEN;

		memcpy((u8 *)tx2.buf + DPAA_WGFP_TX_HEADROOM, r->l2.hdr,
		       ETH_HLEN);
		memcpy(dst, pt, len);
		if (t.family == AF_INET)
			ip_decrease_ttl((struct iphdr *)dst);
		else
			((struct ipv6hdr *)dst)->hop_limit--;
		if (unlikely(r->nat.flags))
			wgfp_apply_nat(dst, len, t.family, t.proto, &r->nat);
		if (tx)
			dpaa_wgfp_tx_cancel(tx);
		else
			kfree_skb(skb);
		wgfp_rx_stats(pst, msg_len);
		dpaa_wgfp_stat_inc(&wgfp_p6_fwd);
		dpaa_wgfp_tx_submit(NULL, &tx2, DPAA_WGFP_TX_HEADROOM,
				    ETH_HLEN + len);
		return true;
	}
	if (!skb)
		return false;
	if (t.family == AF_INET)
		ip_decrease_ttl((struct iphdr *)pt);
	else
		((struct ipv6hdr *)pt)->hop_limit--;
	if (unlikely(r->nat.flags))
		wgfp_apply_nat(pt, len, t.family, t.proto, &r->nat);
	skb_trim(skb, len);
	hdr = skb_push(skb, ETH_HLEN);
	memcpy(hdr, r->l2.hdr, ETH_HLEN);
	wgfp_rx_stats(pst, msg_len);
	dpaa_wgfp_stat_inc(&wgfp_p6_fwd);
	dpaa_wgfp_xmit_skb(NULL, r->l2.egress, r->l2.ifindex, skb);
	return true;
}

/*
 * P6 in the P3 sink, for the outer flow @fl arriving on @dev.  Returns 0 to
 * leave the frame (unchanged) to the P3 path, 1 when it was consumed.  NAPI
 * context, RCU held by the sink. Decrypts out-of-place from read-only f->buf
 * into either a speculative WGFP TX slot (zero-copy forward) or an owned skb,
 * so the DMA_FROM_DEVICE BMan RX page is never mutated.
 */
static int wgfp_rx_fast_frame(struct dpaa_wgfp_frame *f, struct wgfp_flow *fl,
			      struct net_device *dev)
{
	const struct ethhdr *eth = (const struct ethhdr *)(f->buf + f->off);
	struct wgfp_pst *pst = fl->pst;
	struct wg_peer *peer = pst->peer;
	struct noise_keypair *kp;
	const struct message_data *msg;
	const struct udphdr *udp;
	struct dpaa_wgfp_tx_buf tx = {};
	struct sk_buff *skb = NULL;
	u32 l3_off, l3_avail, hl, ilen, ulen, clen, plen, len;
	int hint_eg = -1, hint_ifindex = 0;
	bool have_tx = false;
	__be16 l3_proto, vid, sid;
	const u8 *ct, *l3, *exp_mac;
	u8 ds, *pt;
	unsigned long t;
	u64 nonce;

	exp_mac = is_zero_ether_addr(fl->dst_mac) ? dev->dev_addr : fl->dst_mac;
	if (f->len < ETH_HLEN || !(pst->wgdev->flags & IFF_UP) ||
	    !ether_addr_equal(eth->h_dest, exp_mac))
		return 0;
	if (!wgfp_parse_l2((const u8 *)eth, f->len, &l3_off, &l3_avail,
			   &l3_proto, &vid, &sid) ||
	    vid != fl->t.vlan_vid || sid != fl->t.pppoe_sid)
		return 0;
	l3 = (const u8 *)eth + l3_off;
	if (fl->t.family == AF_INET) {
		const struct iphdr *iph = (const struct iphdr *)l3;

		if (l3_proto != htons(ETH_P_IP) ||
		    l3_avail < sizeof(*iph) || iph->ihl != 5 ||
		    ip_is_fragment(iph) || ip_fast_csum((const u8 *)iph, 5))
			return 0;
		hl = sizeof(*iph);
		ilen = ntohs(iph->tot_len);
		ds = iph->tos;
	} else {
		const struct ipv6hdr *ip6 = (const struct ipv6hdr *)l3;

		if (l3_proto != htons(ETH_P_IPV6) ||
		    l3_avail < sizeof(*ip6) || ip6->nexthdr != IPPROTO_UDP)
			return 0;
		hl = sizeof(*ip6);
		ilen = hl + ntohs(ip6->payload_len);
		ds = ipv6_get_dsfield(ip6);
	}
	if (ilen < hl + sizeof(*udp) || ilen > l3_avail)
		return 0;
	udp = (const struct udphdr *)(l3 + hl);
	ulen = ntohs(udp->len);
	/* keepalives (no plaintext) and runts stay on the P3 path */
	if (ulen > ilen - hl || ulen <= sizeof(*udp) + MESSAGE_MINIMUM_LENGTH ||
	    (fl->t.family == AF_INET6 && !udp->check))
		return 0;
	msg = (const struct message_data *)(udp + 1);
	if (msg->header.type != cpu_to_le32(MESSAGE_DATA))
		return 0;
	/* only the current keypair; the previous and the next one (whose
	 * first packet confirms a handshake) take the stack */
	kp = rcu_dereference(peer->keypairs.current_keypair);
	if (!kp || kp->entry.index != msg->key_idx || READ_ONCE(peer->is_dead) ||
	    !READ_ONCE(kp->receiving.is_valid) ||
	    wg_birthdate_has_expired(kp->receiving.birthdate, REJECT_AFTER_TIME) ||
	    READ_ONCE(kp->receiving_counter.counter) >= REJECT_AFTER_MESSAGES)
		return 0;
	nonce = le64_to_cpu(msg->counter);
	ct = (const u8 *)(msg + 1);
	clen = ulen - sizeof(*udp) - sizeof(*msg);	/* ciphertext + tag */
	plen = clen - POLY1305_DIGEST_SIZE;
	if (READ_ONCE(wgfp_rx_fast) >= 2 && !(ds & INET_ECN_ECT_1) &&
	    ETH_HLEN + plen <= DPAA_WGFP_TX_BUF_SIZE) {
		hint_eg = READ_ONCE(pst->last_rx_egress);
		hint_ifindex = READ_ONCE(pst->last_rx_ifindex);
		if (hint_eg >= 0) {
			have_tx = dpaa_wgfp_tx_alloc(hint_eg, hint_ifindex,
						     ETH_HLEN + plen, &tx);
			if (unlikely(!have_tx))
				WRITE_ONCE(pst->last_rx_egress, -1);
		}
	}
	if (have_tx) {
		pt = (u8 *)tx.buf + DPAA_WGFP_TX_HEADROOM + ETH_HLEN;
	} else {
		skb = netdev_alloc_skb(pst->wgdev, WGFP_RX_HEADROOM + plen);
		if (unlikely(!skb)) {
			dpaa_wgfp_drop(f);
			DEV_STATS_INC(pst->wgdev, rx_dropped);
			atomic64_inc(&wgfp_p6_nomem);
			return 1;
		}
		skb_reserve(skb, WGFP_RX_HEADROOM);
		pt = skb_put(skb, plen);
	}
	/* Decrypt out-of-place: f->buf is read-only DMA_FROM_DEVICE memory. */
	if (!wgfp_open_dst(pt, ct, clen, nonce, kp->receiving.key)) {
		atomic64_inc(&wgfp_p6_auth);
		if (have_tx)
			dpaa_wgfp_tx_cancel(&tx);
		else
			kfree_skb(skb);
		dpaa_wgfp_drop(f);
		return 1;
	}
	dpaa_wgfp_drop(f);
	/* after authentication, as wg_packet_rx_poll() does */
	if (!wg_fastpath_rx_validate(kp, nonce)) {
		atomic64_inc(&wgfp_p6_replay);
		if (have_tx)
			dpaa_wgfp_tx_cancel(&tx);
		else
			kfree_skb(skb);
		return 1;
	}
	t = READ_ONCE(pst->last_rx_timer);
	if (time_after(jiffies, t) &&
	    cmpxchg(&pst->last_rx_timer, t, jiffies + HZ / 100) == t)
		wg_fastpath_rx_timers(peer);
	if ((pt[0] >> 4) == 4 && plen >= sizeof(struct iphdr))
		len = ntohs(((const struct iphdr *)pt)->tot_len);
	else if ((pt[0] >> 4) == 6 && plen >= sizeof(struct ipv6hdr))
		len = sizeof(struct ipv6hdr) +
		      ntohs(((const struct ipv6hdr *)pt)->payload_len);
	else
		len = 0;
	if (unlikely(len < sizeof(struct iphdr) || len > plen)) {
		DEV_STATS_INC(pst->wgdev, rx_errors);
		DEV_STATS_INC(pst->wgdev, rx_frame_errors);
		atomic64_inc(&wgfp_p6_err);
		if (have_tx)
			dpaa_wgfp_tx_cancel(&tx);
		else
			kfree_skb(skb);
		return 1;
	}
	/* outer ECT(1) or CE needs INET_ECN_decapsulate(): the stack hand-off */
	if (READ_ONCE(wgfp_rx_fast) >= 2 && !(ds & INET_ECN_ECT_1) &&
	    wgfp_rx_forward(skb, have_tx ? &tx : NULL, hint_eg, pt, pst,
			    len, ulen - sizeof(*udp)))
		return 1;
	if (have_tx) {
		skb = netdev_alloc_skb(pst->wgdev, WGFP_RX_HEADROOM + len);
		if (unlikely(!skb)) {
			dpaa_wgfp_tx_cancel(&tx);
			DEV_STATS_INC(pst->wgdev, rx_dropped);
			atomic64_inc(&wgfp_p6_nomem);
			return 1;
		}
		skb_reserve(skb, WGFP_RX_HEADROOM);
		skb_put_data(skb, pt, len);
		dpaa_wgfp_tx_cancel(&tx);
	}
	return wgfp_rx_fast_stack(skb, pst, len, ds, ulen - sizeof(*udp));
}

/*
 * P2b-3 slow-path punt: copy the frame into an sk_buff and receive it again on
 * the ingress port of its flow, so the stack forwards it into wg_xmit() (or
 * elsewhere, if routing changed) with every check the fast path skips.  The
 * copy is fine: punts are the exception (handshake gaps, roaming, oversize).
 * With @withdraw (oversize, dead peer) the record goes too, so the flow's later
 * packets take the stack without a detour through the sink.  The DPAA buffer
 * goes back to its pool.  NAPI context, RCU held by the sink.
 */
static int wgfp_punt(struct dpaa_wgfp_frame *f, struct wgfp_flow *fl,
		     bool withdraw)
{
	struct net_device *dev;
	struct sk_buff *skb;

	if (withdraw)
		wgfp_flow_withdraw(fl, &wgfp_decline_withdraws);
	dev = dev_get_by_index_rcu(&init_net, fl->ifindex);
	if (!dev || !(dev->flags & IFF_UP))
		return 0;
	skb = netdev_alloc_skb_ip_align(dev, f->len);
	if (!skb)
		return 0;
	skb_put_data(skb, f->buf + f->off, f->len);
	skb->protocol = eth_type_trans(skb, dev);
	dpaa_wgfp_drop(f);
	dpaa_wgfp_orp_settle();
	atomic64_inc(&wgfp_punted);
	netif_receive_skb(skb);
	return 1;
}

/* Does the template still point at the peer's endpoint?  Lockless. */
static bool wgfp_endpoint_matches(const struct wg_peer *peer,
				  const struct wgfp_tmpl *t)
{
	const struct udphdr *udp = (const struct udphdr *)(t->hdr + t->hlen -
							   sizeof(*udp));

	if (READ_ONCE(peer->endpoint.addr.sa_family) != t->family)
		return false;
	if (t->family == AF_INET)
		return READ_ONCE(peer->endpoint.addr4.sin_addr.s_addr) ==
		       ((const struct iphdr *)(t->hdr + t->l2_hlen))->daddr &&
		       READ_ONCE(peer->endpoint.addr4.sin_port) == udp->dest;
	return READ_ONCE(peer->endpoint.addr6.sin6_port) == udp->dest &&
	       !memcmp(&peer->endpoint.addr6.sin6_addr,
		       &((const struct ipv6hdr *)(t->hdr + t->l2_hlen))->daddr, 16);
}

/*
 * P3: an inbound outer frame of a peer endpoint (the record admits only
 * that exact UDP 5-tuple).  Copy it into an sk_buff and give it to upstream
 * wg_packet_receive() as wg_receive() would, minus the IP/netfilter/UDP layers
 * in front.  Packets that fail the IP or UDP checks go through the full stack.
 * NAPI context, RCU held by the sink.
 */
static int wgfp_rx_frame(struct dpaa_wgfp_frame *f, struct wgfp_flow *fl)
{
	const struct iphdr *iph;
	const struct ipv6hdr *ip6;
	const struct udphdr *udp;
	struct net_device *dev = dev_get_by_index_rcu(&init_net, fl->ifindex);
	struct sk_buff *skb;
	u32 len, hl, udp_len;
	bool plain;

	if (!dev || !(dev->flags & IFF_UP))
		return 0;
	if (READ_ONCE(wgfp_rx_fast)) {
		if (wgfp_rx_fast_frame(f, fl, dev))
			return 1;
		atomic64_inc(&wgfp_p6_punt);
	}
	skb = netdev_alloc_skb_ip_align(dev, f->len);
	if (!skb)
		return 0;
	skb_put_data(skb, f->buf + f->off, f->len);
	dpaa_wgfp_drop(f);
	dpaa_wgfp_orp_settle();
	skb->protocol = eth_type_trans(skb, dev);
	/* Outer VLAN or PPPoE non-P6 frames (handshakes, keepalives, ECN)
	 * return to the full stack so vlan/pppoe receive handles them. */
	if (fl->t.vlan_vid || fl->t.pppoe_sid) {
		dpaa_wgfp_stat_inc(&wgfp_rx_stack);
		netif_receive_skb(skb);
		return 1;
	}
	iph = (const struct iphdr *)skb->data;
	ip6 = (const struct ipv6hdr *)skb->data;
	/* the tuple matched, so the IP header and the UDP ports are there */
	if (fl->t.family == AF_INET) {
		hl = sizeof(*iph);
		len = ntohs(iph->tot_len);
		plain = iph->ihl == 5 && !ip_is_fragment(iph) &&
			!ip_fast_csum((const u8 *)iph, 5);
	} else {
		hl = sizeof(*ip6);
		len = hl + ntohs(ip6->payload_len);
		plain = ip6->nexthdr == IPPROTO_UDP;
	}
	plain = plain && skb->pkt_type == PACKET_HOST && len <= skb->len &&
		len >= hl + sizeof(*udp);
	if (plain) {
		udp = (const struct udphdr *)(skb->data + hl);
		udp_len = ntohs(udp->len);
		plain = udp_len >= sizeof(*udp) && udp_len <= len - hl;
		if (plain && udp->check) {
			__wsum csum = csum_partial(udp, udp_len, 0);

			plain = fl->t.family == AF_INET ?
				!csum_tcpudp_magic(iph->saddr, iph->daddr, udp_len,
						   IPPROTO_UDP, csum) :
				!csum_ipv6_magic(&ip6->saddr, &ip6->daddr, udp_len,
						 IPPROTO_UDP, csum);
		} else if (fl->t.family == AF_INET6) {
			plain = false;
		}
	}
	if (unlikely(!plain)) {
		dpaa_wgfp_stat_inc(&wgfp_rx_stack);
		netif_receive_skb(skb);
		return 1;
	}
	pskb_trim(skb, len);
	skb_reset_network_header(skb);
	skb_set_transport_header(skb, hl);
	skb->skb_iif = dev->ifindex;
	dpaa_wgfp_stat_inc(&wgfp_rx_frames);
	wg_packet_receive(fl->pst->peer->device, skb);
	return 1;
}

static int wgfp_peer_frame(void *priv, struct dpaa_wgfp_frame *f)
{
	u8 *data = f->buf + f->off;
	u8 *inner;
	struct dpaa_wgfp_tuple tup = {};
	struct noise_keypair *kp;
	const struct wgfp_tmpl *t;
	const struct iphdr *iph;
	struct message_data *wg;
	struct wgfp_flow *fl;
	struct wgfp_pst *pst;
	struct wg_peer *peer;
	struct udphdr *udp;
	struct iphdr *oip;
	__be16 l3_proto;
	u32 l3_off, l3_avail, ip_len, plain, tot, outer_ip_len, mtu, added;
	bool withdraw = false;
	u64 ctr;
	u8 *hdr;

	/* Only TCP/UDP of a learned flow reaches a sink; anything else (a
	 * stale or hand-made record) is dropped. */
	if (!wgfp_parse_l2(data, f->len, &l3_off, &l3_avail, &l3_proto,
			   &tup.vlan_vid, &tup.pppoe_sid))
		return 0;
	inner = data + l3_off;
	if (!wgfp_parse(inner, l3_proto, l3_avail, l3_avail, &tup, &ip_len))
		return 0;
	iph = (const struct iphdr *)inner;
	fl = wgfp_flow_lookup(&tup, f->ingress_ifindex);
	if (!fl)
		return 0;
	if (!wgfp_tx_family_allowed(tup.family))
		return wgfp_punt(f, fl, true);
	if (fl->rx)
		return wgfp_rx_frame(f, fl);
	pst = fl->pst;
	peer = pst->peer;
	mtu = READ_ONCE(pst->wgdev->mtu);

	/* Oversize (the stack fragments or sends ICMP) and a removed peer are
	 * permanent for the flow: punt and withdraw its record. */
	if (ip_len > mtu || READ_ONCE(peer->is_dead))
		goto decline_flow;
	/* We forward the inner packet: TTL must drop by one, as on the slow
	 * path.  TTL <= 1 is left to the stack (ICMP time exceeded); ASK's
	 * ttl_punt normally sends those to the kernel before the record. */
	if ((tup.family == AF_INET ? iph->ttl :
	     ((const struct ipv6hdr *)inner)->hop_limit) <= 1)
		goto decline;
	if (unlikely(!wgfp_nat_len_ok(ip_len, tup.family, tup.proto, &fl->nat)))
		goto decline;

	t = rcu_dereference(pst->tmpl);
	if (unlikely(!t))
		goto decline;
	/* outer L2 (+ VLAN/PPPoE) + IP + UDP + WireGuard header */
	added = t->hlen + sizeof(struct message_data);
	plain = min_t(u32, mtu, ALIGN(ip_len, MESSAGE_PADDING_MULTIPLE));
	tot = sizeof(struct udphdr) + sizeof(struct message_data) + plain +
	      POLY1305_DIGEST_SIZE;		/* the UDP datagram */
	outer_ip_len = (t->family == AF_INET ? sizeof(*oip) :
			sizeof(struct ipv6hdr)) + tot;
	if (unlikely(t->mtu && outer_ip_len > t->mtu))
		goto decline;
	{
		/* Keep scratch_b0 = dst - 64 strictly >= DPAA_WGFP_TX_HEADROOM
		 * (64) so IPv4 outer (added=58) never touches [0..63] metadata
		 * and lands dst on a 64B-aligned cacheline (offset 128). */
		u32 pad_off = added < CHACHA_BLOCK_SIZE ?
			      CHACHA_BLOCK_SIZE - added : 0;
		u32 off = DPAA_WGFP_TX_HEADROOM + pad_off;
		u32 frame_len = added + plain + POLY1305_DIGEST_SIZE;
		struct dpaa_wgfp_tx_buf tx;
		u8 *dst;

		if (unlikely(pad_off + frame_len > DPAA_WGFP_TX_BUF_SIZE))
			goto decline_flow;
		/* Roaming: unlocked compare, as wg_socket_set_peer_endpoint() does; a
		 * torn read costs one punt.  The stopping test pairs with the
		 * synchronize_rcu() before the refresh work is cancelled for good. */
		if (unlikely(!wgfp_endpoint_matches(peer, t))) {
			if (!READ_ONCE(pst->stopping))
				mod_delayed_work(system_wq, &pst->refresh, 0);
			goto decline;
		}
		kp = rcu_dereference(peer->keypairs.current_keypair);
		if (unlikely(!kp || !READ_ONCE(kp->sending.is_valid) ||
			     wg_birthdate_has_expired(kp->sending.birthdate, REJECT_AFTER_TIME)))
			goto decline;

		if (unlikely(!dpaa_wgfp_tx_alloc(t->egress, t->ifindex,
						 pad_off + frame_len, &tx))) {
			unsigned long next = READ_ONCE(pst->last_refresh_kick);

			if (!READ_ONCE(pst->stopping) &&
			    time_after(jiffies, next) &&
			    cmpxchg(&pst->last_refresh_kick, next,
				    jiffies + HZ / 4) == next)
				mod_delayed_work(system_wq, &pst->refresh, 0);
			goto decline;
		}
		ctr = (u64)atomic64_inc_return(&kp->sending_counter) - 1;
		if (unlikely(ctr >= REJECT_AFTER_MESSAGES)) {
			dpaa_wgfp_tx_cancel(&tx);
			goto decline;
		}
		hdr = (u8 *)tx.buf + off;
		dst = hdr + added;
		/* Out-of-place 1-pass seal from read-only DMA_FROM_DEVICE f->buf. */
		wgfp_seal_dst(dst, inner, ip_len, plain, tup.family,
			      CHACHA_BLOCK_SIZE, ctr, kp->sending.key, &fl->nat);

		memcpy(hdr, t->hdr, t->hlen);
		if (t->pppoe_off)
			put_unaligned_be16(2 + outer_ip_len,
					   hdr + t->pppoe_off + 4);
		if (t->family == AF_INET) {
			oip = (struct iphdr *)(hdr + t->l2_hlen);
			oip->tot_len = htons(outer_ip_len);
			oip->check = 0;
			oip->check = ip_fast_csum((const u8 *)oip, oip->ihl);
			udp = (struct udphdr *)(oip + 1);
		} else {
			((struct ipv6hdr *)(hdr + t->l2_hlen))->payload_len = htons(tot);
			udp = (struct udphdr *)(hdr + t->l2_hlen + sizeof(struct ipv6hdr));
		}
		udp->len = htons(tot);
		wg = (struct message_data *)(udp + 1);
		wg->header.type = cpu_to_le32(MESSAGE_DATA);
		wg->key_idx = kp->remote_index;
		wg->counter = cpu_to_le64(ctr);
		if (t->family == AF_INET6) {
			const struct ipv6hdr *ip6 = (const struct ipv6hdr *)(hdr + t->l2_hlen);

			udp->check = csum_ipv6_magic(&ip6->saddr, &ip6->daddr, tot,
						     IPPROTO_UDP, csum_partial(udp, tot, 0));
			if (!udp->check)
				udp->check = CSUM_MANGLED_0;
		}

		if (time_after(jiffies, READ_ONCE(pst->last_timer))) {
			WRITE_ONCE(pst->last_timer, jiffies + HZ / 100);
			wg_timers_any_authenticated_packet_traversal(peer);
			wg_timers_any_authenticated_packet_sent(peer);
			wg_timers_data_sent(peer);
			/* Same test as keep_key_fresh(); the send is itself rate limited. */
			if (ctr > REKEY_AFTER_MESSAGES ||
			    (kp->i_am_the_initiator &&
			     wg_birthdate_has_expired(kp->sending.birthdate, REKEY_AFTER_TIME))) {
				atomic64_inc(&wgfp_rekeys);
				wg_packet_send_queued_handshake_initiation(peer, false);
			}
		}
		dpaa_wgfp_stat_inc(&wgfp_sealed);
		dpaa_wgfp_tx_submit(f, &tx, off, frame_len);
		return 1;
	}
decline_flow:
	withdraw = true;
decline:
	atomic64_inc(&wgfp_declined);
	return wgfp_punt(f, fl, withdraw);
}

static int wgfp_port_quiesce(void *priv, struct net_device *dev);
static int wgfp_policy_revoke(void *priv);

static const struct dpaa_wgfp_ops wgfp_peer_ops = {
	.frame = wgfp_peer_frame,
	.port_quiesce = wgfp_port_quiesce,
	.policy_revoke = wgfp_policy_revoke,
};

/* Is @t the peer's current endpoint (unlocked, as wgfp_endpoint_matches())? */
static bool wgfp_endpoint_is(const struct wg_peer *peer,
			     const struct dpaa_wgfp_tuple *t)
{
	if (READ_ONCE(peer->endpoint.addr.sa_family) != t->family)
		return false;
	if (t->family == AF_INET)
		return READ_ONCE(peer->endpoint.addr4.sin_port) == t->sport &&
		       READ_ONCE(peer->endpoint.addr4.sin_addr.s_addr) == t->saddr;
	return READ_ONCE(peer->endpoint.addr6.sin6_port) == t->sport &&
	       !memcmp(&peer->endpoint.addr6.sin6_addr, &t->saddr6, 16);
}

/*
 * Called from wg_receive() in the UDP tunnel encap path, after IP input and
 * the INPUT hook delivered the packet to the WireGuard socket.  Record the
 * exact outer tuple and the ingress port; wgfp_pst_rx_sync() admits a record
 * only for a tuple observed this way.  Lockless: one writer per CPU.
 */
void wg_fastpath_rx_observe(struct wg_device *wg, struct sk_buff *skb)
{
	struct wgfp_obs_ring *ring;
	struct dpaa_wgfp_tuple t = {};
	struct wgfp_obs *o;
	const struct iphdr *iph;
	const struct ipv6hdr *ip6;
	const struct udphdr *udp;
	u32 avail;

	if (!wg || !skb || !wg->dev || !skb->skb_iif)
		return;
	if ((u8 *)skb_transport_header(skb) < skb->head ||
	    (u8 *)(udp_hdr(skb) + 1) > skb_tail_pointer(skb))
		return;
	udp = udp_hdr(skb);
	if (skb->protocol == htons(ETH_P_IP)) {
		iph = ip_hdr(skb);
		if ((u8 *)iph < skb->head ||
		    (u8 *)(iph + 1) > (u8 *)udp ||
		    iph->version != 4 || iph->protocol != IPPROTO_UDP ||
		    iph->ihl != 5 || ip_is_fragment(iph))
			return;
		t.family = AF_INET;
		t.saddr = iph->saddr;
		t.daddr = iph->daddr;
	} else if (skb->protocol == htons(ETH_P_IPV6)) {
		ip6 = ipv6_hdr(skb);
		if ((u8 *)ip6 < skb->head ||
		    (u8 *)(ip6 + 1) > (u8 *)udp ||
		    ip6->version != 6 || ip6->nexthdr != IPPROTO_UDP)
			return;
		t.family = AF_INET6;
		t.saddr6 = ip6->saddr;
		t.daddr6 = ip6->daddr;
	} else {
		return;
	}
	avail = (u8 *)skb_tail_pointer(skb) - (u8 *)udp;
	if (avail < sizeof(*udp))
		return;
	t.sport = udp->source;
	t.dport = udp->dest;
	t.proto = IPPROTO_UDP;

	ring = this_cpu_ptr(&wgfp_obs);
	o = &ring->e[ring->next++ % WGFP_OBS_PER_CPU];
	WRITE_ONCE(ring->seq, ring->seq + 1);
	smp_wmb();
	o->t = t;
	o->wg_ifindex = wg->dev->ifindex;
	o->ifindex = skb->skb_iif;
	o->jiffies = jiffies;
	o->epoch = atomic64_read(&wgfp_obs_epoch);
	smp_wmb();
	WRITE_ONCE(ring->seq, ring->seq + 1);
}

/* Find a fresh observation of @pst's current endpoint on any port. */
static bool wgfp_rx_observed(struct wgfp_pst *pst, struct dpaa_wgfp_tuple *rt,
			     int *ifindex)
{
	u64 cur_epoch = atomic64_read(&wgfp_obs_epoch);
	unsigned long now = jiffies;
	int cpu;
	unsigned int i;

	for_each_possible_cpu(cpu) {
		struct wgfp_obs_ring *ring = per_cpu_ptr(&wgfp_obs, cpu);
		u32 seq;

		seq = READ_ONCE(ring->seq);
		if (seq & 1)
			continue;
		smp_rmb();
		for (i = 0; i < WGFP_OBS_PER_CPU; i++) {
			struct wgfp_obs *o = &ring->e[i];

			if (!o->t.family ||
			    o->wg_ifindex != pst->wgdev->ifindex ||
			    o->ifindex <= 0 ||
			    o->epoch != cur_epoch ||
			    time_after(now, o->jiffies + WGFP_OBS_TTL))
				continue;
			if (!wgfp_endpoint_is(pst->peer, &o->t))
				continue;
			*rt = o->t;
			*ifindex = o->ifindex;
			smp_rmb();
			if (READ_ONCE(ring->seq) == seq)
				return true;
			break;	/* racing writer: try another CPU's ring */
		}
	}
	return false;
}

/* WGFP sinks require contiguous single-buffer RX FDs; jumbo ports (>3300 MTU)
 * stay in software where dpaa_eth's sg_fd_to_skb() handles scatter/gather. */
static bool wgfp_ingress_mtu_ok(const struct net_device *dev)
{
	return dev && READ_ONCE(dev->mtu) <= DPAA_WGFP_MAX_CONTIG_MTU;
}

/* Install, move or withdraw the peer's outer RX record. */
static void wgfp_pst_rx_sync(struct wgfp_pst *pst)
{
	struct dpaa_wgfp_tuple rt = {};
	struct wgfp_flow *fl;
	u8 dst_mac[ETH_ALEN] = {};
	bool want = false;
	int ifindex = 0;

	if (READ_ONCE(wgfp_rx) && !READ_ONCE(pst->peer->is_dead) &&
	    !READ_ONCE(pst->stopping) && wgfp_rx_observed(pst, &rt, &ifindex)) {
		struct net_device *dev = dev_get_by_index(&init_net, ifindex);

		if (dev) {
			struct wgfp_l2_path path;
			struct net_device *phys_dev = NULL;
			bool resolved;

			rcu_read_lock();
			resolved = wgfp_resolve_l2_path(dev, &path);
			rcu_read_unlock();
			dev_put(dev);
			if (resolved)
				phys_dev = dev_get_by_index(&init_net,
							    path.phys_ifindex);
			if (phys_dev) {
				if (wgfp_ingress_mtu_ok(phys_dev) &&
				    dpaa_wgfp_family_ok(phys_dev, rt.family)) {
					ifindex = phys_dev->ifindex;
					rt.vlan_vid = path.has_vlan ?
						htons(path.vlan_vid & VLAN_VID_MASK) : 0;
					rt.pppoe_sid = path.has_pppoe ?
						htons(path.pppoe_sid) : 0;
					ether_addr_copy(dst_mac, path.src_mac);
					want = true;
				}
				dev_put(phys_dev);
			}
		}
		if (want)
			pr_info_once("wireguard: P3 receive offload admits only tuples observed on the stack\n");
	}
	spin_lock_bh(&wgfp_flows_lock);
	fl = pst->rxfl;
	if (fl && (!want || fl->ifindex != ifindex ||
		   !wgfp_tuple_eq(&fl->t, &rt) ||
		   !ether_addr_equal(fl->dst_mac, dst_mac))) {
		if (!fl->del) {
			fl->del = true;
			queue_work(system_wq, &wgfp_flow_work);
		}
		pst->rxfl = NULL;
		fl = NULL;
	}
	if (want && !fl && !atomic_read(&wgfp_quiesce_active) &&
	    wgfp_nflows < WGFP_FLOW_MAX &&
	    !wgfp_flow_find_tuple(&rt, ifindex, wgfp_tuple_hash(&rt))) {
		fl = kzalloc(sizeof(*fl), GFP_ATOMIC);
		if (fl) {
			fl->t = rt;
			fl->pst = pst;
			fl->rx = true;
			fl->ifindex = ifindex;
			ether_addr_copy(fl->dst_mac, dst_mac);
			fl->hash = wgfp_tuple_hash(&rt);
			fl->state = WGFP_PENDING;
			fl->since = jiffies;
			pst->nflows++;
			pst->rxfl = fl;
			hash_add_rcu(wgfp_flows, &fl->node, fl->hash);
			wgfp_nflows++;
			queue_work(system_wq, &wgfp_flow_work);
		}
	}
	spin_unlock_bh(&wgfp_flows_lock);
}

/* ---- learning ---- */

#if IS_REACHABLE(CONFIG_NF_CONNTRACK)
/* Look up @ct and packet direction from the post-NAT wire tuple @post when
 * skb->_nfct is NULL (e.g. packets forwarded by nf_flow_offload).  For any
 * confirmed conntrack entry (NAT or non-NAT, ORIG or REPLY), invert(@post)
 * equals ct->tuplehash[!dir].tuple, so !NF_CT_DIRECTION(h) is the packet's
 * own direction and ct->tuplehash[dir].tuple is its pre-NAT wire tuple.
 * Caller must nf_ct_put() the returned ct. */
static struct nf_conn *wgfp_ct_find_post(struct net *net,
					 const struct dpaa_wgfp_tuple *post,
					 enum ip_conntrack_dir *dir_out)
{
	struct nf_conntrack_tuple inv = {};
	struct nf_conntrack_tuple_hash *h;

	inv.src.l3num = post->family;
	if (post->family == AF_INET6) {
		inv.src.u3.in6 = post->daddr6;
		inv.dst.u3.in6 = post->saddr6;
	} else {
		inv.src.u3.ip = post->daddr;
		inv.dst.u3.ip = post->saddr;
	}
	inv.src.u.all = post->dport;
	inv.dst.u.all = post->sport;
	inv.dst.protonum = post->proto;
	inv.dst.dir = IP_CT_DIR_ORIGINAL;
	h = nf_conntrack_find_get(net, &nf_ct_zone_dflt, &inv);
	if (!h)
		return NULL;
	*dir_out = !NF_CT_DIRECTION(h);
	return nf_ct_tuplehash_to_ctrack(h);
}
#endif

/* Extract pre-NAT wire tuple from @ct for @skb (for FIN/RST withdrawal even
 * if the conntrack state is already closing). */
static void wgfp_ct_orig_tuple(struct net *net, struct sk_buff *skb,
			       const struct dpaa_wgfp_tuple *post,
			       struct dpaa_wgfp_tuple *orig_out)
{
	*orig_out = *post;
#if IS_REACHABLE(CONFIG_NF_CONNTRACK)
	{
		enum ip_conntrack_info ctinfo;
		enum ip_conntrack_dir dir = IP_CT_DIR_ORIGINAL;
		struct nf_conn *ct = skb ? nf_ct_get(skb, &ctinfo) : NULL;
		bool put = false;

		if (ct) {
			dir = CTINFO2DIR(ctinfo);
		} else {
			ct = wgfp_ct_find_post(net, post, &dir);
			put = ct != NULL;
		}
		if (ct && (ct->status & IPS_NAT_MASK)) {
			const struct nf_conntrack_tuple *ct_orig =
				&ct->tuplehash[dir].tuple;

			if (ct_orig->src.l3num == post->family &&
			    ct_orig->dst.protonum == post->proto) {
				if (post->family == AF_INET6) {
					orig_out->saddr6 = ct_orig->src.u3.in6;
					orig_out->daddr6 = ct_orig->dst.u3.in6;
				} else {
					orig_out->saddr = ct_orig->src.u3.ip;
					orig_out->daddr = ct_orig->dst.u3.ip;
				}
				orig_out->sport = ct_orig->src.u.all;
				orig_out->dport = ct_orig->dst.u.all;
			}
		}
		if (put)
			nf_ct_put(ct);
	}
#endif
}

/*
 * Check for an established conntrack entry without master or helper.  For
 * NAT44/NAT66 flows, populate @orig_out with the pre-NAT wire tuple and
 * @nat_out with the rewrite delta (@orig_out -> @post).  Packets forwarded by
 * the software flowtable carry no attached skb->_nfct, so fall back to an
 * inverse-post-tuple lookup that recovers both NAT and non-NAT entries.
 */
static bool wgfp_ct_admitted(struct net *net, struct sk_buff *skb,
			     const struct dpaa_wgfp_tuple *post,
			     struct dpaa_wgfp_tuple *orig_out,
			     struct wgfp_nat *nat_out)
{
#if IS_REACHABLE(CONFIG_NF_CONNTRACK)
	enum ip_conntrack_info ctinfo;
	enum ip_conntrack_dir dir = IP_CT_DIR_ORIGINAL;
	struct nf_conn *ct = skb ? nf_ct_get(skb, &ctinfo) : NULL;
	bool put = false, ok = false;

	*orig_out = *post;
	memset(nat_out, 0, sizeof(*nat_out));

	if (ct) {
		if (ctinfo != IP_CT_ESTABLISHED &&
		    ctinfo != IP_CT_ESTABLISHED_REPLY)
			return false;
		dir = CTINFO2DIR(ctinfo);
	} else {
		ct = wgfp_ct_find_post(net, post, &dir);
		if (!ct)
			return false;
		put = true;
		if (!test_bit(IPS_SEEN_REPLY_BIT, &ct->status))
			goto out_put;
	}
	if (!nf_ct_zone_equal_any(ct, &nf_ct_zone_dflt) ||
	    nf_ct_is_dying(ct) || nf_ct_is_expired(ct) ||
	    (ct->status & IPS_SEQ_ADJUST) ||
	    ct->master || nfct_help(ct))
		goto out_put;
	if (post->proto == IPPROTO_TCP) {
		if (READ_ONCE(ct->proto.tcp.state) != TCP_CONNTRACK_ESTABLISHED)
			goto out_put;
		/* WGFP bypasses conntrack on packets 33+, so disable strict
		 * TCP window tracking just as nf_flow_offload_add() does;
		 * otherwise 1s P6 renewal and punted FIN/RST fail
		 * tcp_in_window() and lose reverse-NAT / ct lookup. */
		if (!(READ_ONCE(ct->proto.tcp.seen[0].flags) &
		      IP_CT_TCP_FLAG_BE_LIBERAL) ||
		    !(READ_ONCE(ct->proto.tcp.seen[1].flags) &
		      IP_CT_TCP_FLAG_BE_LIBERAL)) {
			spin_lock_bh(&ct->lock);
			ct->proto.tcp.seen[0].flags |= IP_CT_TCP_FLAG_BE_LIBERAL;
			ct->proto.tcp.seen[1].flags |= IP_CT_TCP_FLAG_BE_LIBERAL;
			spin_unlock_bh(&ct->lock);
		}
	}
	if (ct->status & IPS_NAT_MASK) {
		const struct nf_conntrack_tuple *ct_orig =
			&ct->tuplehash[dir].tuple;

		if (ct_orig->src.l3num != post->family ||
		    ct_orig->dst.protonum != post->proto)
			goto out_put;
		if (post->family == AF_INET6) {
			orig_out->saddr6 = ct_orig->src.u3.in6;
			orig_out->daddr6 = ct_orig->dst.u3.in6;
			if (memcmp(&orig_out->saddr6, &post->saddr6, 16)) {
				nat_out->flags |= WGFP_NAT_SRC_IP;
				nat_out->saddr6 = post->saddr6;
			}
			if (memcmp(&orig_out->daddr6, &post->daddr6, 16)) {
				nat_out->flags |= WGFP_NAT_DST_IP;
				nat_out->daddr6 = post->daddr6;
			}
		} else {
			orig_out->saddr = ct_orig->src.u3.ip;
			orig_out->daddr = ct_orig->dst.u3.ip;
			if (orig_out->saddr != post->saddr) {
				nat_out->flags |= WGFP_NAT_SRC_IP;
				nat_out->saddr = post->saddr;
			}
			if (orig_out->daddr != post->daddr) {
				nat_out->flags |= WGFP_NAT_DST_IP;
				nat_out->daddr = post->daddr;
			}
		}
		orig_out->sport = ct_orig->src.u.all;
		orig_out->dport = ct_orig->dst.u.all;
		if (orig_out->sport != post->sport) {
			nat_out->flags |= WGFP_NAT_SRC_PORT;
			nat_out->sport = post->sport;
		}
		if (orig_out->dport != post->dport) {
			nat_out->flags |= WGFP_NAT_DST_PORT;
			nat_out->dport = post->dport;
		}
	}
	ok = true;
out_put:
	if (put)
		nf_ct_put(ct);
	return ok;
#else
	*orig_out = *post;
	memset(nat_out, 0, sizeof(*nat_out));
	return false;	/* no conntrack: no proof the flow was admitted */
#endif
}

/* Under wgfp_flows_lock, atomic context. */
static struct wgfp_pst *wgfp_pst_get_locked(struct wg_peer *peer,
					    struct net_device *wgdev)
{
	struct wgfp_pst *pst;

	hash_for_each_possible(wgfp_psts, pst, node, (unsigned long)peer)
		if (pst->peer == peer && !pst->stopping)
			return pst;
	pst = kzalloc(sizeof(*pst), GFP_ATOMIC);
	if (!pst)
		return NULL;
	pst->rx_pend = alloc_percpu_gfp(struct wgfp_rx_cnt, GFP_ATOMIC);
	if (!pst->rx_pend) {
		kfree(pst);
		return NULL;
	}
	pst->peer = wg_peer_get(peer);
	pst->wgdev = wgdev;
	pst->last_rx_egress = -1;
	INIT_DELAYED_WORK(&pst->refresh, wgfp_pst_refresh);
	hash_add(wgfp_psts, &pst->node, (unsigned long)peer);
	wgfp_npsts++;
	schedule_delayed_work(&pst->refresh, 0);
	return pst;
}

/*
 * Phase P2b-2, automatic flow install.  wg_xmit() offers every packet to
 * wg_fastpath_learn().  An inner IPv4 TCP/UDP flow of a bound device that
 * arrived on a DPAA port, whose conntrack entry is established, not NAT'ed,
 * not related to a master and has no helper, is tracked from that packet on;
 * after WGFP_ADMIT slow-path packets it gets an FMan record (installed by a
 * worker; the record API sleeps) that steers its later frames on that port
 * straight to a sink (round-robin), where wgfp_peer_frame() seals them.  Mice
 * (an iperf3 control connection, DNS) never take a record or a sink.  Only
 * the first packet needs the conntrack proof (wgfp_ct_admitted()).  A tracked
 * flow that does not reach WGFP_ADMIT within 10 s is forgotten.  TCP SYN
 * is never learned; FIN/RST (punted to the kernel by ASK's tcp_ctl_punt)
 * withdraws the record, as does 10 s without silicon hits or unbinding.
 *
 * Like an offloaded flowtable flow, a fast-pathed flow skips the firewall
 * after it was admitted: rule changes apply when the record is withdrawn.
 */
void wg_fastpath_learn(struct wg_peer *peer, struct sk_buff *skb)
{
	struct wgfp_binds *bs = rcu_dereference_bh(wgfp_bs);
	struct net_device *wgdev = NULL;
	struct dpaa_wgfp_tuple post = {}, t = {};
	struct wgfp_nat nat = {};
	struct wgfp_pst *pst;
	struct wgfp_flow *fl;
	u8 hb[sizeof(struct ipv6hdr) + 4];
	unsigned int thoff, nhoff, i;
	const u8 *l3;
	bool fin = false, in_ok, admitted = false;
	u32 hash, len, total;

	if (!bs || READ_ONCE(bs->stopping) || !skb->skb_iif ||
	    (skb->protocol != htons(ETH_P_IP) && skb->protocol != htons(ETH_P_IPV6)))
		return;
	for (i = 0; i < bs->n; i++)
		if (netdev_priv(bs->wgdev[i]) == peer->device)
			wgdev = bs->wgdev[i];
	if (!wgdev)
		return;
	nhoff = skb_network_offset(skb);
	if (skb->len <= nhoff)
		return;
	total = skb->len - nhoff;
	l3 = skb_header_pointer(skb, nhoff, min_t(u32, sizeof(hb), total), hb);
	if (!l3 || !wgfp_parse(l3, skb->protocol, min_t(u32, sizeof(hb), total),
			       total, &post, &len))
		return;
	if (!wgfp_tx_family_allowed(post.family))
		return;
	/* Inner family policy and contiguous-RX MTU bound of ingress port. */
	rcu_read_lock();
	{
		struct net_device *indev = dev_get_by_index_rcu(&init_net, skb->skb_iif);

		in_ok = wgfp_ingress_mtu_ok(indev) &&
			dpaa_wgfp_family_ok(indev, post.family);
	}
	rcu_read_unlock();
	if (!in_ok)
		return;
	thoff = nhoff + (post.family == AF_INET ?
		sizeof(struct iphdr) : sizeof(struct ipv6hdr));
	if (post.proto == IPPROTO_TCP) {
		struct tcphdr _th;
		const struct tcphdr *th = skb_header_pointer(skb, thoff,
							     sizeof(_th), &_th);

		if (!th || th->syn)
			return;
		fin = th->fin || th->rst;
	}
	if (fin) {
		wgfp_ct_orig_tuple(dev_net(skb->dev), skb, &post, &t);
	} else {
		admitted = wgfp_ct_admitted(dev_net(skb->dev), skb, &post, &t, &nat);
		if (!admitted)
			return;
	}
	hash = wgfp_tuple_hash(&t);

	/* ponytail: one global lock per slow-path packet of a bound device;
	 * an RCU fast check first if it ever shows in a profile. */
	spin_lock_bh(&wgfp_flows_lock);
	if (atomic_read(&wgfp_quiesce_active))
		goto out;
	fl = wgfp_flow_find(&t, skb->skb_iif, hash);
	if (fl) {
		if (fin) {
			if (!fl->del) {
				fl->del = true;
				queue_work(system_wq, &wgfp_flow_work);
			}
		} else if (fl->pst->peer != peer ||
			   memcmp(&fl->nat, &nat, sizeof(nat))) {
			if (fl->state == WGFP_LEARNING && fl->pst->peer == peer) {
				fl->nat = nat;
				fl->pkts = 1;
				fl->since = jiffies;
			} else if (!fl->del) {
				fl->del = true;
				queue_work(system_wq, &wgfp_flow_work);
			}
		} else if (fl->state == WGFP_LEARNING && ++fl->pkts >= WGFP_ADMIT) {
			fl->pkts = 0;
			fl->state = WGFP_PENDING;
			queue_work(system_wq, &wgfp_flow_work);
		}
		goto out;
	}
	if (fin || !admitted || wgfp_nflows >= WGFP_FLOW_MAX ||
	    wgfp_flow_find_tuple(&t, skb->skb_iif, hash))
		goto out;
	pst = wgfp_pst_get_locked(peer, wgdev);
	if (!pst)
		goto out;
	fl = kzalloc(sizeof(*fl), GFP_ATOMIC);
	if (!fl)
		goto out;	/* an empty pst is freed by the next aging pass */
	fl->t = t;
	fl->nat = nat;
	fl->pst = pst;
	fl->ifindex = skb->skb_iif;
	fl->hash = hash;
	fl->state = WGFP_LEARNING;
	fl->pkts = 1;
	fl->since = jiffies;
	pst->nflows++;
	hash_add_rcu(wgfp_flows, &fl->node, hash);
	wgfp_nflows++;
out:
	spin_unlock_bh(&wgfp_flows_lock);
}

/* ---- install, withdraw, aging ---- */

/*
 * The sink (CPU) for a new record: the least used among the records of the
 * same port and kind (P3 outer RX, or TX), so four tunnels decrypt on four
 * CPUs even after churn.  dpaa_wgfp takes the index modulo its sink count.
 * ponytail: counts records, not traffic; per-sink silicon counters if one
 * heavy flow and many mice share a port.
 */
#define WGFP_SLOTS	8

static u8 wgfp_flow_slot_locked(const struct wgfp_flow *nfl)
{
	unsigned int use[WGFP_SLOTS] = {}, n = min_t(unsigned int, nr_cpu_ids, WGFP_SLOTS);
	const struct wgfp_flow *fl;
	unsigned int bkt, i, best = 0;

	hash_for_each(wgfp_flows, bkt, fl, node)
		if (fl != nfl && fl->ifindex == nfl->ifindex && fl->rx == nfl->rx &&
		    (fl->state == WGFP_INSTALLED || fl->state == WGFP_INSTALLING))
			use[fl->slot % n]++;
	for (i = 1; i < n; i++)
		if (use[i] < use[best])
			best = i;
	return best;
}

/* Pinned target netdev during wgfp_port_quiesce() (guarded by wgfp_flows_lock)
 * so NETDEV_UNREGISTER deletes succeed even after unlist_netdevice(). */
static struct net_device *wgfp_quiesce_dev;

/* Install PENDING records, withdraw del-marked ones.  Only this worker and
 * the teardown (after cancelling it) free entries; nobody frees an
 * INSTALLING entry, so the job pointers stay valid across the unlock. */
static void wgfp_flow_work_fn(struct work_struct *w)
{
	struct { struct wgfp_flow *fl; struct net_device *dev;
		 struct dpaa_wgfp_tuple t; int ifindex;
		 bool add, rx; u8 slot; } job[WGFP_BATCH];
	struct wgfp_flow *fl;
	struct hlist_node *tmp;
	unsigned int n = 0, i, bkt;
	bool more = false;

	spin_lock_bh(&wgfp_flows_lock);
	hash_for_each_safe(wgfp_flows, bkt, tmp, fl, node) {
		bool add;

		if (fl->state == WGFP_INSTALLING || fl->state == WGFP_STUCK)
			continue;
		if (fl->del) {
			if (fl->state != WGFP_INSTALLED) {
				wgfp_flow_free_locked(fl);
				continue;
			}
			add = false;
		} else if (fl->state == WGFP_PENDING) {
			add = true;
		} else {
			continue;
		}
		if (n == WGFP_BATCH) {
			more = true;
			break;
		}
		if (add)
			fl->slot = wgfp_flow_slot_locked(fl);
		fl->state = WGFP_INSTALLING;
		job[n].slot = fl->slot;
		job[n].fl = fl;
		job[n].dev = NULL;
		if (!add && wgfp_quiesce_dev &&
		    wgfp_quiesce_dev->ifindex == fl->ifindex) {
			dev_hold(wgfp_quiesce_dev);
			job[n].dev = wgfp_quiesce_dev;
		}
		job[n].t = fl->t;
		job[n].ifindex = fl->ifindex;
		job[n].add = add;
		job[n].rx = fl->rx;
		n++;
	}
	spin_unlock_bh(&wgfp_flows_lock);

	for (i = 0; i < n; i++) {
		struct net_device *dev = job[i].dev ? job[i].dev :
			dev_get_by_index(&init_net, job[i].ifindex);
		int err = -ENODEV;

		if (dev) {
			err = job[i].add ?
			      dpaa_wgfp_flow_add(dev, &job[i].t, job[i].slot,
						 job[i].rx) :
			      dpaa_wgfp_flow_del(dev, &job[i].t);
			dev_put(dev);
		}
		spin_lock_bh(&wgfp_flows_lock);
		fl = job[i].fl;
		if (job[i].add) {
			if (err < 0) {
				atomic64_inc(&wgfp_install_fail);
				/* Uncertain SYNC/barrier failure may leave an
				 * unowned or quarantined HW record; keep STUCK
				 * instead of freeing after WGFP_FAIL_HOLD. */
				fl->state = (err == -ETIMEDOUT || err == -EIO) ?
					    WGFP_STUCK : WGFP_FAILED;
			} else {
				atomic64_inc(&wgfp_installs);
				fl->state = WGFP_INSTALLED;
			}
			fl->since = jiffies;
			if (fl->del)
				more = true;	/* FIN while installing */
		} else if (err < 0) {
			/* not provably unlinked: keep the entry so the tuple is
			 * neither relearned nor reinserted over a live record */
			atomic64_inc(&wgfp_withdraw_fail);
			fl->state = WGFP_STUCK;
		} else {
			atomic64_inc(&wgfp_withdraws);
			wgfp_flow_free_locked(fl);
		}
		spin_unlock_bh(&wgfp_flows_lock);
	}
	if (more)
		queue_work(system_wq, &wgfp_flow_work);
}

static int wgfp_quiesce_flows_locked_loop(int ifindex)
{
	const unsigned int max_passes =
		DIV_ROUND_UP(WGFP_FLOW_MAX, WGFP_BATCH) + 16;
	struct wgfp_flow *fl;
	struct wgfp_rflow *r;
	struct hlist_node *tmp;
	unsigned int bkt, pass, pending = 0, stuck = 0;

	for (pass = 0; pass < max_passes; pass++) {
		pending = 0;
		stuck = 0;
		spin_lock_bh(&wgfp_flows_lock);
		hash_for_each(wgfp_flows, bkt, fl, node) {
			if (ifindex > 0 && fl->ifindex != ifindex)
				continue;
			if (fl->state == WGFP_STUCK) {
				stuck++;
				continue;
			}
			fl->del = true;
			if (fl->pst && fl->pst->rxfl == fl)
				fl->pst->rxfl = NULL;
			if (fl->state == WGFP_PENDING ||
			    fl->state == WGFP_INSTALLING ||
			    fl->state == WGFP_INSTALLED ||
			    fl->state == WGFP_LEARNING ||
			    fl->state == WGFP_FAILED)
				pending++;
		}
		spin_unlock_bh(&wgfp_flows_lock);
		if (!pending)
			break;
		queue_work(system_wq, &wgfp_flow_work);
		flush_work(&wgfp_flow_work);
	}

	spin_lock_bh(&wgfp_flows_lock);
	hash_for_each_safe(wgfp_rflows, bkt, tmp, r, node) {
		if (ifindex > 0 && r->l2.ifindex != ifindex)
			continue;
		hash_del_rcu(&r->node);
		wgfp_nrflows--;
		kfree_rcu(r, rcu);
	}
	spin_unlock_bh(&wgfp_flows_lock);
	return (stuck || pending) ? -EBUSY : 0;
}

static int wgfp_port_quiesce(void *priv, struct net_device *dev)
{
	int rc;

	(void)priv;
	if (!dev || dev->ifindex <= 0)
		return 0;
	atomic_inc(&wgfp_quiesce_active);
	spin_lock_bh(&wgfp_flows_lock);
	wgfp_quiesce_dev = dev;
	spin_unlock_bh(&wgfp_flows_lock);
	synchronize_rcu();
	rc = wgfp_quiesce_flows_locked_loop(dev->ifindex);
	/* Ensure no worker batch still holds a reference obtained from
	 * wgfp_quiesce_dev before clearing it and returning. */
	flush_work(&wgfp_flow_work);
	spin_lock_bh(&wgfp_flows_lock);
	wgfp_quiesce_dev = NULL;
	spin_unlock_bh(&wgfp_flows_lock);
	atomic_dec(&wgfp_quiesce_active);
	return rc;
}

static int wgfp_policy_revoke(void *priv)
{
	int rc;

	(void)priv;
	atomic_inc(&wgfp_quiesce_active);
	atomic64_inc(&wgfp_obs_epoch);
	wgfp_rflows_reap(true);
	synchronize_rcu();
	rc = wgfp_quiesce_flows_locked_loop(0);
	atomic_dec(&wgfp_quiesce_active);
	return rc;
}

/* Free peers without flows: unhash under the lock, then let every sink-path
 * reader that may still see them finish before the refresh work is cancelled
 * for good (the sink path re-arms it only while !stopping). */
static void wgfp_psts_reap(bool all)
{
	struct wgfp_pst *dead[WGFP_BATCH], *pst;
	struct hlist_node *tmp;
	unsigned int n = 0, i, bkt;

	do {
		n = 0;
		spin_lock_bh(&wgfp_flows_lock);
		hash_for_each_safe(wgfp_psts, bkt, tmp, pst, node) {
			if ((pst->nflows && !all) || n == WGFP_BATCH)
				continue;
			pst->stopping = true;
			hash_del(&pst->node);
			wgfp_npsts--;
			dead[n++] = pst;
		}
		spin_unlock_bh(&wgfp_flows_lock);
		if (!n)
			return;
		synchronize_rcu();
		for (i = 0; i < n; i++) {
			pst = dead[i];
			cancel_delayed_work_sync(&pst->refresh);
			kfree_rcu(rcu_dereference_protected(pst->tmpl, true), rcu);
			/* no reader is left after synchronize_rcu() */
			wgfp_rx_fold(pst);
			free_percpu(pst->rx_pend);
			wg_peer_put(pst->peer);
			kfree_rcu(pst, rcu);
		}
	} while (n == WGFP_BATCH);
}

#if IS_REACHABLE(CONFIG_NF_CONNTRACK)
static bool wgfp_ct_nat_matches(const struct nf_conn *ct,
				const struct nf_conntrack_tuple_hash *h,
				const struct dpaa_wgfp_tuple *orig,
				const struct wgfp_nat *nat)
{
	const struct nf_conntrack_tuple *opp =
		&ct->tuplehash[!NF_CT_DIRECTION(h)].tuple;
	__be16 post_sport = (nat->flags & WGFP_NAT_SRC_PORT) ?
			    nat->sport : orig->sport;
	__be16 post_dport = (nat->flags & WGFP_NAT_DST_PORT) ?
			    nat->dport : orig->dport;

	if (opp->src.l3num != orig->family || opp->dst.protonum != orig->proto ||
	    opp->dst.u.all != post_sport || opp->src.u.all != post_dport)
		return false;
	if (orig->family == AF_INET6) {
		const struct in6_addr *ps = (nat->flags & WGFP_NAT_SRC_IP) ?
					    &nat->saddr6 : &orig->saddr6;
		const struct in6_addr *pd = (nat->flags & WGFP_NAT_DST_IP) ?
					    &nat->daddr6 : &orig->daddr6;

		return !memcmp(&opp->dst.u3.in6, ps, 16) &&
		       !memcmp(&opp->src.u3.in6, pd, 16);
	}
	return opp->dst.u3.ip == ((nat->flags & WGFP_NAT_SRC_IP) ?
				  nat->saddr : orig->saddr) &&
	       opp->src.u3.ip == ((nat->flags & WGFP_NAT_DST_IP) ?
				  nat->daddr : orig->daddr);
}
#endif

/* Withdraw records whose silicon counter stopped moving or whose inner
 * destination no longer maps to their peer (AllowedIPs changed); forget
 * failures and flows that never reached WGFP_ADMIT; free peers without flows.
 * The teardown cancels this work before it frees anything else. */
static void wgfp_age_fn(struct work_struct *w)
{
	struct wgfp_age_job {
		struct wgfp_flow *fl;
		struct dpaa_wgfp_tuple t;
		struct wgfp_nat nat;
		u64 prev_pkts;
		int ifindex;
		u32 hash;
		struct wgfp_pst *pst;
	} *job;
	static unsigned int cursor;
	unsigned int n = 0, i, bkt, seen = 0;
	struct wgfp_flow *fl;
	struct hlist_node *tmp;
	struct sk_buff *probe;
	bool kick = false;

	job = kcalloc(WGFP_BATCH, sizeof(*job), GFP_KERNEL);
	if (!job)
		goto reschedule;

	/* wg_allowedips_lookup_dst() takes an skb; one IP header suffices. */
	probe = alloc_skb(sizeof(struct ipv6hdr), GFP_KERNEL);
	if (probe) {
		skb_reset_network_header(probe);
		skb_put_zero(probe, sizeof(struct ipv6hdr));
	}

	spin_lock_bh(&wgfp_flows_lock);
	hash_for_each_safe(wgfp_flows, bkt, tmp, fl, node) {
		if ((fl->state == WGFP_FAILED && !fl->del &&
		     time_after(jiffies, fl->since + WGFP_FAIL_HOLD)) ||
		    (fl->state == WGFP_LEARNING &&
		     time_after(jiffies, fl->since + WGFP_AGE_IDLE * WGFP_AGE_PERIOD))) {
			wgfp_flow_free_locked(fl);
			continue;
		}
		/* the outer record lives as long as its peer state */
		if (fl->state != WGFP_INSTALLED || fl->del || fl->rx)
			continue;
		/* round-robin over passes when there are more than a batch */
		if (seen++ < cursor || n == WGFP_BATCH)
			continue;
		job[n].fl = fl;
		job[n].t = fl->t;
		job[n].nat = fl->nat;
		job[n].prev_pkts = fl->pkts;
		job[n].ifindex = fl->ifindex;
		job[n].hash = fl->hash;
		job[n].pst = fl->pst;	/* only this work frees psts */
		n++;
	}
	cursor = (n == WGFP_BATCH) ? cursor + n : 0;
	spin_unlock_bh(&wgfp_flows_lock);

	for (i = 0; i < n; i++) {
		struct net_device *dev = dev_get_by_index(&init_net, job[i].ifindex);
		struct wgfp_pst *pst = job[i].pst;
		bool foreign = false, family = false, ct_dead = false;
		u64 pkts = 0;
		int err = -ENODEV;

		if (dev) {
			err = dpaa_wgfp_flow_packets(dev, &job[i].t, &pkts);
			/* the port may have dropped this family since install */
			family = dpaa_wgfp_family_ok(dev, job[i].t.family);
			dev_put(dev);
		}
#if IS_REACHABLE(CONFIG_NF_CONNTRACK)
		if (job[i].nat.flags && !err) {
			struct nf_conntrack_tuple tuple = {};
			struct nf_conntrack_tuple_hash *h;

			tuple.src.l3num = job[i].t.family;
			if (job[i].t.family == AF_INET6) {
				tuple.src.u3.in6 = job[i].t.saddr6;
				tuple.dst.u3.in6 = job[i].t.daddr6;
			} else {
				tuple.src.u3.ip = job[i].t.saddr;
				tuple.dst.u3.ip = job[i].t.daddr;
			}
			tuple.src.u.all = job[i].t.sport;
			tuple.dst.u.all = job[i].t.dport;
			tuple.dst.protonum = job[i].t.proto;
			tuple.dst.dir = IP_CT_DIR_ORIGINAL;
			h = nf_conntrack_find_get(&init_net, &nf_ct_zone_dflt, &tuple);
			if (!h) {
				ct_dead = true;
			} else {
				struct nf_conn *ct = nf_ct_tuplehash_to_ctrack(h);

				if (nf_ct_is_dying(ct) || nf_ct_is_expired(ct) ||
				    !(ct->status & IPS_NAT_MASK) ||
				    (ct->status & IPS_SEQ_ADJUST) ||
				    (job[i].t.proto == IPPROTO_TCP &&
				     READ_ONCE(ct->proto.tcp.state) !=
				     TCP_CONNTRACK_ESTABLISHED) ||
				    !wgfp_ct_nat_matches(ct, h, &job[i].t,
							 &job[i].nat)) {
					ct_dead = true;
				} else if (pkts != job[i].prev_pkts) {
					u32 ct_timeout = READ_ONCE(ct->timeout);
					s32 rem = ct_timeout - nfct_time_stamp;

					if (rem > 0 && rem < 60 * HZ)
						cmpxchg(&ct->timeout, ct_timeout,
							nfct_time_stamp + 60 * HZ);
				}
				nf_ct_put(ct);
			}
		}
#endif
		if (probe) {
			struct wg_peer *p;

			if (job[i].t.family == AF_INET6) {
				probe->protocol = htons(ETH_P_IPV6);
				ipv6_hdr(probe)->daddr =
					(job[i].nat.flags & WGFP_NAT_DST_IP) ?
					job[i].nat.daddr6 : job[i].t.daddr6;
			} else {
				probe->protocol = htons(ETH_P_IP);
				ip_hdr(probe)->daddr =
					(job[i].nat.flags & WGFP_NAT_DST_IP) ?
					job[i].nat.daddr : job[i].t.daddr;
			}
			p = wg_allowedips_lookup_dst(&pst->peer->device->peer_allowedips,
						     probe);
			foreign = p != pst->peer;
			wg_peer_put(p);
		}
		spin_lock_bh(&wgfp_flows_lock);
		/* the flow may have been withdrawn and freed meanwhile: only
		 * touch it if it is still the entry the table holds */
		fl = wgfp_flow_find(&job[i].t, job[i].ifindex, job[i].hash);
		if (fl == job[i].fl && fl->state == WGFP_INSTALLED && !fl->del) {
			if (!family) {
				fl->del = true;
				kick = true;
			} else if (ct_dead) {
				fl->del = true;
				kick = true;
			} else if (foreign) {
				fl->del = true;
				kick = true;
				atomic64_inc(&wgfp_aip_withdraws);
			} else if (err || pkts == fl->pkts) {
				if (++fl->idle >= WGFP_AGE_IDLE) {
					fl->del = true;
					kick = true;
				}
			} else {
				fl->idle = 0;
				fl->pkts = pkts;
			}
		}
		spin_unlock_bh(&wgfp_flows_lock);
	}
	kfree_skb(probe);
	kfree(job);
	if (kick)
		queue_work(system_wq, &wgfp_flow_work);
	wgfp_rflows_reap(false);
	wgfp_psts_reap(false);
reschedule:
	if (rcu_access_pointer(wgfp_bs))
		schedule_delayed_work(&wgfp_age_work, WGFP_AGE_PERIOD);
}

/* Teardown: the binding is marked stopping and RCU learning readers drained. */
static int wgfp_flows_flush(void)
{
	struct wgfp_flow *fl;
	struct wgfp_pst *pst;
	struct hlist_node *tmp;
	unsigned int bkt;
	int first_err = 0;

	cancel_delayed_work_sync(&wgfp_age_work);
	spin_lock_bh(&wgfp_flows_lock);
	hash_for_each(wgfp_psts, bkt, pst, node)
		WRITE_ONCE(pst->stopping, true);
	spin_unlock_bh(&wgfp_flows_lock);
	/* The aging worker is stopped, so peer-state entries remain stable here. */
	hash_for_each(wgfp_psts, bkt, pst, node)
		cancel_delayed_work_sync(&pst->refresh);
	cancel_work_sync(&wgfp_flow_work);
	/* no worker runs now; nothing else frees entries, so drop the lock
	 * around the sleeping record delete */
	spin_lock_bh(&wgfp_flows_lock);
	hash_for_each_safe(wgfp_flows, bkt, tmp, fl, node) {
		int err = 0;

		if (fl->state == WGFP_INSTALLED) {
			struct dpaa_wgfp_tuple t = fl->t;
			int ifindex = fl->ifindex;
			struct net_device *dev;

			spin_unlock_bh(&wgfp_flows_lock);
			dev = dev_get_by_index(&init_net, ifindex);
			if (dev) {
				err = dpaa_wgfp_flow_del(dev, &t);
				dev_put(dev);
			} else {
				err = -ENODEV;
			}
			spin_lock_bh(&wgfp_flows_lock);
			if (err < 0) {
				atomic64_inc(&wgfp_withdraw_fail);
				pr_warn_ratelimited("wgfp: teardown could not delete a record (%d); it may still steer packets\n", err);
				fl->state = WGFP_STUCK;
				if (!first_err)
					first_err = err;
			} else {
				atomic64_inc(&wgfp_withdraws);
				wgfp_flow_free_locked(fl);
			}
		} else if (fl->state == WGFP_STUCK) {
			pr_warn_ratelimited("wgfp: teardown with an unresolved record\n");
			if (!first_err)
				first_err = -EBUSY;
		} else {
			wgfp_flow_free_locked(fl);
		}
	}
	spin_unlock_bh(&wgfp_flows_lock);
	return first_err;
}

/* ---- binding ---- */

/*
 * Peer mode holds references on the bound wg devices; drop them when one goes
 * away (ip link del wg0), else unregister would wait forever.  The notifier
 * runs under RTNL and the stop sleeps in the record API: defer it.
 */
static int wgfp_peer_stop(void);
static int wgfp_peer_start(char *names, bool distinct);

static void wgfp_stop_work_fn(struct work_struct *w)
{
	char names[sizeof(wgfp_peer_cfg)] = "";
	struct wgfp_binds *bs;
	bool gone = false;
	unsigned int i;

	mutex_lock(&wgfp_lab_lock);
	bs = rcu_dereference_protected(wgfp_bs, lockdep_is_held(&wgfp_lab_lock));
	if (bs) {
		for (i = 0; i < bs->n; i++) {
			if (test_bit(i, &bs->invalidated) ||
			    READ_ONCE(bs->wgdev[i]->reg_state) != NETREG_REGISTERED ||
			    dev_net(bs->wgdev[i]) != &init_net) {
				pr_info("fastpath: %s unregistered or moved, unbinding it\n",
					bs->wgdev[i]->name);
				gone = true;
				continue;
			}
			strlcat(names, bs->wgdev[i]->name, sizeof(names));
			strlcat(names, " ", sizeof(names));
		}
	}
	if (gone) {
		char copy[sizeof(names)];
		int err;

		err = wgfp_peer_stop();
		if (err) {
			pr_warn_ratelimited("wgfp: keeping binding and consumer after failed teardown (%d)\n",
					    err);
			mutex_unlock(&wgfp_lab_lock);
			return;
		}
		strscpy(wgfp_peer_cfg, "off", sizeof(wgfp_peer_cfg));
		wgfp_owner_aliases[0] = '\0';
		strscpy(copy, strim(names), sizeof(copy));
		if (!wgfp_peer_owner[0] && copy[0] &&
		    !wgfp_peer_start(names, false))	/* consumes names */
			strscpy(wgfp_peer_cfg, copy, sizeof(wgfp_peer_cfg));
	}
	mutex_unlock(&wgfp_lab_lock);
}
static DECLARE_WORK(wgfp_stop_work, wgfp_stop_work_fn);

static void wgfp_refresh_psts(void)
{
	struct wgfp_pst *pst;
	unsigned int bkt;

	spin_lock_bh(&wgfp_flows_lock);
	hash_for_each(wgfp_psts, bkt, pst, node)
		if (!READ_ONCE(pst->stopping))
			mod_delayed_work(system_wq, &pst->refresh, 0);
	spin_unlock_bh(&wgfp_flows_lock);
}

static int wgfp_netdev_event(struct notifier_block *nb, unsigned long event,
			     void *ptr)
{
	struct net_device *dev = netdev_notifier_info_to_dev(ptr);
	struct wgfp_binds *bs;
	unsigned int i;

	if (event == NETDEV_CHANGEMTU) {
		if (dev && !wgfp_ingress_mtu_ok(dev))
			(void)wgfp_port_quiesce(NULL, dev);
		return NOTIFY_DONE;
	}
	if ((event == NETDEV_DOWN || event == NETDEV_UNREGISTER) && dev &&
	    (dev->type == ARPHRD_PPP || is_vlan_dev(dev)))
		wgfp_refresh_psts();
	if (event != NETDEV_UNREGISTER)
		return NOTIFY_DONE;
	rcu_read_lock();
	bs = rcu_dereference(wgfp_bs);
	for (i = 0; bs && i < bs->n; i++)
		if (bs->wgdev[i] == dev) {
			set_bit(i, &bs->invalidated);
			schedule_work(&wgfp_stop_work);
		}
	rcu_read_unlock();
	return NOTIFY_DONE;
}

static struct notifier_block wgfp_netdev_nb = {
	.notifier_call = wgfp_netdev_event,
};

static int wgfp_peer_stop(void)
{
	struct wgfp_binds *bs = rcu_dereference_protected(wgfp_bs,
					lockdep_is_held(&wgfp_lab_lock));
	unsigned int i;
	int err;

	if (!bs)
		return 0;
	WRITE_ONCE(bs->stopping, true);
	synchronize_rcu();
	err = wgfp_flows_flush();
	if (err)
		return err;
	RCU_INIT_POINTER(wgfp_bs, NULL);	/* learning runs under RCU-bh */
	wgfp_nf_unregister();
	cancel_work_sync(&wgfp_eg_work);
	unregister_netdevice_notifier(&wgfp_netdev_nb);
	synchronize_rcu();
	dpaa_wgfp_unregister();		/* synchronize_rcu(): no sink path left */
	wgfp_rflows_reap(true);
	wgfp_psts_reap(true);
	for (i = 0; i < bs->n; i++)
		dev_put(bs->wgdev[i]);
	kfree(bs);
	module_put(THIS_MODULE);
	return 0;
}

/* @names: space-separated wg devices.  Called with wgfp_lab_lock held. */
static int wgfp_peer_start(char *names, bool distinct)
{
	struct wgfp_binds *bs;
	char *name, *resolved_names[WGFP_MAX_BIND];
	unsigned int i;
	bool module_ref = false;
	int err;

	bs = kzalloc(sizeof(*bs), GFP_KERNEL);
	if (!bs)
		return -ENOMEM;
	while ((name = strsep(&names, " \t\n"))) {
		struct net_device *dev;

		if (!*name)
			continue;
		err = -E2BIG;
		if (bs->n == WGFP_MAX_BIND)
			goto err;
		err = -ENODEV;
		dev = dev_get_by_name(&init_net, name);
		if (!dev)
			goto err;
		if (distinct) {
			for (i = 0; i < bs->n; i++) {
				if (bs->wgdev[i] == dev) {
					dev_put(dev);
					err = -EINVAL;
					goto err;
				}
			}
		}
		resolved_names[bs->n] = name;
		bs->wgdev[bs->n++] = dev;
		err = -EINVAL;
		if (!dev->rtnl_link_ops || strcmp(dev->rtnl_link_ops->kind, "wireguard"))
			goto err;
	}
	err = -EINVAL;
	if (!bs->n)
		goto err;
	err = -ENODEV;
	/* The sink callback must remain resident until all records are withdrawn. */
	if (!try_module_get(THIS_MODULE))
		goto err;
	module_ref = true;
	err = dpaa_wgfp_register(&wgfp_peer_ops, NULL);
	if (err)
		goto err;
	err = register_netdevice_notifier(&wgfp_netdev_nb);
	if (err)
		goto err_consumer;
	rtnl_lock();
	for (i = 0; i < bs->n; i++) {
		if (READ_ONCE(bs->wgdev[i]->reg_state) != NETREG_REGISTERED ||
		    dev_net(bs->wgdev[i]) != &init_net ||
		    __dev_get_by_name(&init_net, resolved_names[i]) != bs->wgdev[i]) {
			err = -ENODEV;
			break;
		}
	}
	if (!err)
		rcu_assign_pointer(wgfp_bs, bs);
	rtnl_unlock();
	if (err) {
		unregister_netdevice_notifier(&wgfp_netdev_nb);
		goto err_consumer;
	}
	wgfp_nf_register();
	schedule_delayed_work(&wgfp_age_work, WGFP_AGE_PERIOD);
	pr_info("fastpath peer mode on: %u device(s)\n", bs->n);
	return 0;
err_consumer:
	dpaa_wgfp_unregister();
err:
	if (module_ref)
		module_put(THIS_MODULE);
	while (bs->n)
		dev_put(bs->wgdev[--bs->n]);
	kfree(bs);
	return err;
}

static int wgfp_peer_cfg_set(const char *val, const struct kernel_param *kp)
{
	char buf[sizeof(wgfp_peer_cfg)], copy[sizeof(wgfp_peer_cfg)];
	int err = 0;

	strscpy(buf, val, sizeof(buf));
	strim(buf);
	strscpy(copy, buf, sizeof(copy));
	mutex_lock(&wgfp_lab_lock);
	if (wgfp_peer_owner[0]) {
		err = -EBUSY;
		goto out;
	}
	err = wgfp_peer_stop();
	if (err)
		goto out;
	strscpy(wgfp_peer_cfg, "off", sizeof(wgfp_peer_cfg));
	if (!strcmp(buf, "off") || !buf[0]) {
		/* unbound */
	} else if (wgfp_lab_on) {
		err = -EBUSY;
	} else {
		err = wgfp_peer_start(copy, false);
		if (!err)
			strscpy(wgfp_peer_cfg, buf, sizeof(wgfp_peer_cfg));
	}
out:
	mutex_unlock(&wgfp_lab_lock);
	return err;
}

static char *wgfp_next_arg(char **args)
{
	char *arg;

	while ((arg = strsep(args, " \t\n")) && !*arg)
		;
	return arg;
}

static bool wgfp_owner_token_valid(const char *token)
{
	size_t i;

	if (!token || strlen(token) != WGFP_OWNER_TOKEN_LEN)
		return false;
	for (i = 0; i < WGFP_OWNER_TOKEN_LEN; i++)
		if (!((token[i] >= '0' && token[i] <= '9') ||
		      (token[i] >= 'a' && token[i] <= 'f')))
			return false;
	return true;
}

static bool wgfp_owner_alias_valid(const char *alias, const char *token)
{
	const char *suffix;
	size_t len = strlen(alias);

	if (len <= WGFP_OWNER_TOKEN_LEN + 5 || len > WGFP_OWNER_ALIAS_MAX ||
	    strncmp(alias, "wgo-", 4) ||
	    strncmp(alias + 4, token, WGFP_OWNER_TOKEN_LEN) ||
	    alias[WGFP_OWNER_TOKEN_LEN + 4] != '-')
		return false;
	for (suffix = alias + WGFP_OWNER_TOKEN_LEN + 5; *suffix; suffix++)
		if (!((*suffix >= 'a' && *suffix <= 'z') ||
		      (*suffix >= '0' && *suffix <= '9') ||
		      *suffix == '-'))
			return false;
	return true;
}

static int wgfp_peer_lease_set(const char *val, const struct kernel_param *kp)
{
	char aliases[WGFP_OWNER_ALIASES_SIZE] = "";
	char copy[WGFP_OWNER_ALIASES_SIZE];
	char *items[WGFP_MAX_BIND], *input, *args, *action, *token, *arg;
	struct wgfp_binds *bs;
	unsigned int n = 0, i;
	int err = 0;

	input = kstrdup(val, GFP_KERNEL);
	if (!input)
		return -ENOMEM;
	args = strim(input);
	action = wgfp_next_arg(&args);
	token = wgfp_next_arg(&args);
	if (!action || !wgfp_owner_token_valid(token)) {
		err = -EINVAL;
		goto out_free;
	}
	if (!strcmp(action, "bind")) {
		while ((arg = wgfp_next_arg(&args))) {
			if (n == WGFP_MAX_BIND) {
				err = -E2BIG;
				goto out_free;
			}
			if (!wgfp_owner_alias_valid(arg, token)) {
				err = -EINVAL;
				goto out_free;
			}
			for (i = 0; i < n; i++)
				if (!strcmp(items[i], arg)) {
					err = -EINVAL;
					goto out_free;
				}
			items[n++] = arg;
			if ((n > 1 && strlcat(aliases, " ", sizeof(aliases)) >=
				      sizeof(aliases)) ||
			    strlcat(aliases, arg, sizeof(aliases)) >= sizeof(aliases)) {
				err = -E2BIG;
				goto out_free;
			}
		}
		if (!n) {
			err = -EINVAL;
			goto out_free;
		}
	} else if ((strcmp(action, "reserve") && strcmp(action, "stop") &&
		    strcmp(action, "release")) || wgfp_next_arg(&args)) {
		err = -EINVAL;
		goto out_free;
	}

	mutex_lock(&wgfp_lab_lock);
	if (!strcmp(action, "reserve")) {
		if (wgfp_peer_owner[0]) {
			if (strcmp(wgfp_peer_owner, token))
				err = -EBUSY;
		} else if (rcu_access_pointer(wgfp_bs) || wgfp_lab_on) {
			err = -EBUSY;
		} else {
			strscpy(wgfp_peer_owner, token, sizeof(wgfp_peer_owner));
		}
		goto out_unlock;
	}
	if (strcmp(wgfp_peer_owner, token)) {
		err = -EPERM;
		goto out_unlock;
	}
	if (!strcmp(action, "stop")) {
		err = wgfp_peer_stop();
		if (!err) {
			strscpy(wgfp_peer_cfg, "off", sizeof(wgfp_peer_cfg));
			wgfp_owner_aliases[0] = '\0';
		}
	} else if (!strcmp(action, "release")) {
		if (rcu_access_pointer(wgfp_bs)) {
			err = -EBUSY;
		} else {
			wgfp_peer_owner[0] = '\0';
			wgfp_owner_aliases[0] = '\0';
		}
	} else if (rcu_access_pointer(wgfp_bs)) {
		bs = rcu_dereference_protected(wgfp_bs,
					       lockdep_is_held(&wgfp_lab_lock));
		if (READ_ONCE(bs->stopping) ||
		    strcmp(wgfp_owner_aliases, aliases))
			err = -EBUSY;
	} else if (wgfp_lab_on) {
		err = -EBUSY;
	} else {
		strscpy(copy, aliases, sizeof(copy));
		err = wgfp_peer_start(copy, true);
		if (!err) {
			bs = rcu_dereference_protected(wgfp_bs,
						lockdep_is_held(&wgfp_lab_lock));
			wgfp_peer_cfg[0] = '\0';
			for (i = 0; i < bs->n; i++) {
				if (i)
					strlcat(wgfp_peer_cfg, " ",
						sizeof(wgfp_peer_cfg));
				strlcat(wgfp_peer_cfg, bs->wgdev[i]->name,
					sizeof(wgfp_peer_cfg));
			}
			strscpy(wgfp_owner_aliases, aliases,
				sizeof(wgfp_owner_aliases));
		}
	}
out_unlock:
	mutex_unlock(&wgfp_lab_lock);
out_free:
	kfree(input);
	return err;
}

static int wgfp_peer_lease_get(char *buf, const struct kernel_param *kp)
{
	int len;

	mutex_lock(&wgfp_lab_lock);
	if (wgfp_peer_owner[0])
		len = scnprintf(buf, PAGE_SIZE, "owned=%s dev=%s\n",
				wgfp_peer_owner, wgfp_peer_cfg);
	else
		len = scnprintf(buf, PAGE_SIZE, "unowned\n");
	mutex_unlock(&wgfp_lab_lock);
	return len;
}

static const struct kernel_param_ops wgfp_peer_lease_ops = {
	.set = wgfp_peer_lease_set,
	.get = wgfp_peer_lease_get,
};
module_param_cb(wgfp_peer_lease, &wgfp_peer_lease_ops, NULL, 0600);
MODULE_PARM_DESC(wgfp_peer_lease,
		 "Atomic WGFP reservation: reserve, bind, stop or release with an owner token");

static int wgfp_peer_cfg_get(char *buf, const struct kernel_param *kp)
{
	struct wgfp_binds *bs;
	struct wgfp_pst *pst;
	bool stopping;
	unsigned int bkt, tmpl = 0;

	rcu_read_lock();
	bs = rcu_dereference(wgfp_bs);
	if (!bs) {
		rcu_read_unlock();
		return scnprintf(buf, PAGE_SIZE, "off\n");
	}
	stopping = READ_ONCE(bs->stopping);
	rcu_read_unlock();
	spin_lock_bh(&wgfp_flows_lock);
	hash_for_each(wgfp_psts, bkt, pst, node)
		tmpl += rcu_access_pointer(pst->tmpl) != NULL;
	spin_unlock_bh(&wgfp_flows_lock);
	return scnprintf(buf, PAGE_SIZE, "on stopping=%u sealed=%lld declined=%lld punted=%lld rekeys=%lld rx=%lld rx_stack=%lld peers=%u tmpl=%u flows=%u installs=%lld install_fail=%lld withdraws=%lld withdraw_fail=%lld decline_withdraws=%lld aip_withdraws=%lld p6=%u p6_fwd=%lld p6_stack=%lld p6_punt=%lld p6_replay=%lld p6_auth=%lld p6_err=%lld p6_aip=%lld p6_nomem=%lld p6_learn=%lld rflows=%u p7=%u p7_fwd=%lld p7_drop=%lld dev=%s\n",
			 stopping,
			 (long long)dpaa_wgfp_stat_sum(&wgfp_sealed),
			 (long long)atomic64_read(&wgfp_declined),
			 (long long)atomic64_read(&wgfp_punted),
			 (long long)atomic64_read(&wgfp_rekeys),
			 (long long)dpaa_wgfp_stat_sum(&wgfp_rx_frames),
			 (long long)dpaa_wgfp_stat_sum(&wgfp_rx_stack),
			 READ_ONCE(wgfp_npsts), tmpl, READ_ONCE(wgfp_nflows),
			 (long long)atomic64_read(&wgfp_installs),
			 (long long)atomic64_read(&wgfp_install_fail),
			 (long long)atomic64_read(&wgfp_withdraws),
			 (long long)atomic64_read(&wgfp_withdraw_fail),
			 (long long)atomic64_read(&wgfp_decline_withdraws),
			 (long long)atomic64_read(&wgfp_aip_withdraws),
			 READ_ONCE(wgfp_rx_fast),
			 (long long)dpaa_wgfp_stat_sum(&wgfp_p6_fwd),
			 (long long)dpaa_wgfp_stat_sum(&wgfp_p6_stack),
			 (long long)atomic64_read(&wgfp_p6_punt),
			 (long long)atomic64_read(&wgfp_p6_replay),
			 (long long)atomic64_read(&wgfp_p6_auth),
			 (long long)atomic64_read(&wgfp_p6_err),
			 (long long)atomic64_read(&wgfp_p6_aip),
			 (long long)atomic64_read(&wgfp_p6_nomem),
			 (long long)atomic64_read(&wgfp_p6_learn),
			 READ_ONCE(wgfp_nrflows),
			 READ_ONCE(wgfp_rx_tail),
			 (long long)dpaa_wgfp_stat_sum(&wgfp_p7_fwd),
			 (long long)atomic64_read(&wgfp_p7_drop),
			 wgfp_peer_cfg);
}

static const struct kernel_param_ops wgfp_peer_param_ops = {
	.set = wgfp_peer_cfg_set,
	.get = wgfp_peer_cfg_get,
};
module_param_cb(wgfp_peer, &wgfp_peer_param_ops, NULL, 0600);
MODULE_PARM_DESC(wgfp_peer, "DPAA fast path for the peers of these wg devices: \"wg0 [wg1 ...]\"; \"off\" disables");

void wg_fastpath_uninit(void)
{
	int err;

	mutex_lock(&wgfp_lab_lock);
	err = wgfp_peer_stop();
	if (err)
		pr_err("wgfp: refusing peer teardown with unresolved hardware records (%d)\n",
		       err);
	else
		wgfp_lab_stop();
	mutex_unlock(&wgfp_lab_lock);
	cancel_work_sync(&wgfp_stop_work);
}
