// SPDX-License-Identifier: GPL-2.0
/*
 * Host RFC 8439 verification of the actual wgfp_seal_one_dst() implementation
 * extracted from drivers/net/wireguard/fastpath.c across all lengths, padding
 * widths, IPv4 TTL/checksum decrement, IPv6 hop_limit decrement, and read-only
 * src invariance.
 */
#include <arpa/inet.h>
#include <assert.h>
#include <limits.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;

#define CHACHA_BLOCK_SIZE 64
#define CHACHA_KEY_WORDS 8
#define CHACHA20POLY1305_KEY_SIZE 32
#define POLY1305_KEY_SIZE 32
#define POLY1305_BLOCK_SIZE 16
#define POLY1305_DIGEST_SIZE 16
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define max_t(t, a, b) ((t)(a) > (t)(b) ? (t)(a) : (t)(b))
#define ALIGN(x, a) (((x) + ((a) - 1)) & ~((a) - 1))
#define WGFP_OPEN_HEAD (4 * CHACHA_BLOCK_SIZE)
#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
#define round_down(x, y) ((x) & ~((y) - 1))
static int crypto_memneq(const void *a, const void *b, size_t n)
{
	const u8 *x = a, *y = b;
	u8 diff = 0;
	for (size_t i = 0; i < n; i++)
		diff |= x[i] ^ y[i];
	return diff != 0;
}
#define cpu_to_le64(x) (x)
#define asmlinkage
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define likely(x) (x)
#define unlikely(x) (x)
#define module_param(a, b, c)
#define MODULE_PARM_DESC(a, b)

struct chacha_state { u32 x[16]; };

static inline u32 get_unaligned_le32(const u8 *p)
{
	return (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16) | ((u32)p[3] << 24);
}
static inline void put_unaligned_le32(u32 v, u8 *p)
{
	p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24;
}
static inline u32 rol32(u32 v, int n) { return (v << n) | (v >> (32 - n)); }

static void chacha_init(struct chacha_state *st, const u32 k[8], const u8 iv[16])
{
	st->x[0] = 0x61707865U; st->x[1] = 0x3320646eU;
	st->x[2] = 0x79622d32U; st->x[3] = 0x6b206574U;
	for (int i = 0; i < 8; i++)
		st->x[4 + i] = k[i];
	st->x[12] = get_unaligned_le32(iv + 0);
	st->x[13] = get_unaligned_le32(iv + 4);
	st->x[14] = get_unaligned_le32(iv + 8);
	st->x[15] = get_unaligned_le32(iv + 12);
}

static void chacha_block_generic(struct chacha_state *st, u8 out[64], int nrounds)
{
	u32 x[16];
	memcpy(x, st->x, sizeof(x));
	for (int i = 0; i < nrounds; i += 2) {
#define QR(a, b, c, d) do { \
	x[a] += x[b]; x[d] = rol32(x[d] ^ x[a], 16); \
	x[c] += x[d]; x[b] = rol32(x[b] ^ x[c], 12); \
	x[a] += x[b]; x[d] = rol32(x[d] ^ x[a], 8); \
	x[c] += x[d]; x[b] = rol32(x[b] ^ x[c], 7); \
} while (0)
		QR(0, 4, 8, 12); QR(1, 5, 9, 13); QR(2, 6, 10, 14); QR(3, 7, 11, 15);
		QR(0, 5, 10, 15); QR(1, 6, 11, 12); QR(2, 7, 8, 13); QR(3, 4, 9, 14);
#undef QR
	}
	for (int i = 0; i < 16; i++)
		put_unaligned_le32(x[i] + st->x[i], out + 4 * i);
	st->x[12]++;
}

static void crypto_xor_cpy(u8 *dst, const u8 *src1, const u8 *src2, unsigned int n)
{
	for (unsigned int i = 0; i < n; i++)
		dst[i] = src1[i] ^ src2[i];
}
static void memzero_explicit(void *p, size_t n) { memset(p, 0, n); }
static void chacha_zeroize_state(struct chacha_state *st) { memset(st, 0, sizeof(*st)); }

static int neon_sections, neon_depth;
static bool crypto_simd_usable(void) { return true; }
static void kernel_neon_begin(void) { assert(neon_depth == 0); neon_depth = 1; neon_sections++; }
static void kernel_neon_end(void) { assert(neon_depth == 1); neon_depth = 0; }

/* Exact semantics of arm64 chacha_block_xor_neon & chacha_4block_xor_neon */
void chacha_block_xor_neon(const struct chacha_state *state, u8 *dst,
			   const u8 *src, int nrounds)
{
	struct chacha_state tmp = *state;
	u8 ks[64];
	assert(neon_depth == 1);
	chacha_block_generic(&tmp, ks, nrounds);
	crypto_xor_cpy(dst, src, ks, 64);
}

void chacha_4block_xor_neon(const struct chacha_state *state, u8 *dst,
			    const u8 *src, int nrounds, int bytes)
{
	struct chacha_state tmp = *state;
	u8 ks[64];
	assert(neon_depth == 1 && bytes > 64 && bytes <= 5 * 64);
	while (bytes > 0) {
		int l = bytes < 64 ? bytes : 64;
		chacha_block_generic(&tmp, ks, nrounds);
		crypto_xor_cpy(dst, src, ks, l);
		dst += l;
		src += l;
		bytes -= l;
	}
}

/* Exact RFC 8439 Poly1305 (radix 2^26) */
struct poly1305_block_state {
	u32 r[5], h[5], pad[4];
};
struct poly1305_desc_ctx {
	struct poly1305_block_state state;
};

static void poly1305_init(struct poly1305_desc_ctx *ctx, const u8 key[32])
{
	u32 t0 = get_unaligned_le32(key + 0);
	u32 t1 = get_unaligned_le32(key + 4);
	u32 t2 = get_unaligned_le32(key + 8);
	u32 t3 = get_unaligned_le32(key + 12);
	memset(ctx, 0, sizeof(*ctx));
	ctx->state.r[0] = t0 & 0x3ffffffU;
	ctx->state.r[1] = ((t0 >> 26) | (t1 << 6)) & 0x3ffff03U;
	ctx->state.r[2] = ((t1 >> 20) | (t2 << 12)) & 0x3ffc0ffU;
	ctx->state.r[3] = ((t2 >> 14) | (t3 << 18)) & 0x3f03fffU;
	ctx->state.r[4] = (t3 >> 8) & 0x00fffffU;
	ctx->state.pad[0] = get_unaligned_le32(key + 16);
	ctx->state.pad[1] = get_unaligned_le32(key + 20);
	ctx->state.pad[2] = get_unaligned_le32(key + 24);
	ctx->state.pad[3] = get_unaligned_le32(key + 28);
}

static void poly1305_blocks_impl(struct poly1305_block_state *st,
				 const u8 *m, u32 bytes, u32 hibit)
{
	u32 r0 = st->r[0], r1 = st->r[1], r2 = st->r[2], r3 = st->r[3], r4 = st->r[4];
	u32 s1 = r1 * 5, s2 = r2 * 5, s3 = r3 * 5, s4 = r4 * 5;
	u32 h0 = st->h[0], h1 = st->h[1], h2 = st->h[2], h3 = st->h[3], h4 = st->h[4];
	u32 hb = hibit ? (1U << 24) : 0;

	while (bytes >= 16) {
		u64 d0, d1, d2, d3, d4;
		u32 c;
		h0 += get_unaligned_le32(m + 0) & 0x3ffffffU;
		h1 += (get_unaligned_le32(m + 3) >> 2) & 0x3ffffffU;
		h2 += (get_unaligned_le32(m + 6) >> 4) & 0x3ffffffU;
		h3 += (get_unaligned_le32(m + 9) >> 6) & 0x3ffffffU;
		h4 += (get_unaligned_le32(m + 12) >> 8) | hb;

		d0 = (u64)h0 * r0 + (u64)h1 * s4 + (u64)h2 * s3 + (u64)h3 * s2 + (u64)h4 * s1;
		d1 = (u64)h0 * r1 + (u64)h1 * r0 + (u64)h2 * s4 + (u64)h3 * s3 + (u64)h4 * s2;
		d2 = (u64)h0 * r2 + (u64)h1 * r1 + (u64)h2 * r0 + (u64)h3 * s4 + (u64)h4 * s3;
		d3 = (u64)h0 * r3 + (u64)h1 * r2 + (u64)h2 * r1 + (u64)h3 * r0 + (u64)h4 * s4;
		d4 = (u64)h0 * r4 + (u64)h1 * r3 + (u64)h2 * r2 + (u64)h3 * r1 + (u64)h4 * r0;

		c = (u32)(d0 >> 26); h0 = (u32)d0 & 0x3ffffffU;
		d1 += c; c = (u32)(d1 >> 26); h1 = (u32)d1 & 0x3ffffffU;
		d2 += c; c = (u32)(d2 >> 26); h2 = (u32)d2 & 0x3ffffffU;
		d3 += c; c = (u32)(d3 >> 26); h3 = (u32)d3 & 0x3ffffffU;
		d4 += c; c = (u32)(d4 >> 26); h4 = (u32)d4 & 0x3ffffffU;
		h0 += c * 5; c = h0 >> 26; h0 &= 0x3ffffffU;
		h1 += c;
		m += 16;
		bytes -= 16;
	}
	st->h[0] = h0; st->h[1] = h1; st->h[2] = h2; st->h[3] = h3; st->h[4] = h4;
}

void poly1305_blocks_arm64(struct poly1305_block_state *st, const u8 *s, u32 len, u32 hb)
{
	assert(neon_depth == 0);
	poly1305_blocks_impl(st, s, len, hb);
}
void poly1305_blocks_neon(struct poly1305_block_state *st, const u8 *s, u32 len, u32 hb)
{
	assert(neon_depth == 1);
	poly1305_blocks_impl(st, s, len, hb);
}

static void poly1305_final(struct poly1305_desc_ctx *ctx, u8 mac[16])
{
	u32 h0 = ctx->state.h[0], h1 = ctx->state.h[1], h2 = ctx->state.h[2],
	    h3 = ctx->state.h[3], h4 = ctx->state.h[4];
	u32 c, g0, g1, g2, g3, g4, mask;
	u64 f;

	c = h1 >> 26; h1 &= 0x3ffffffU;
	h2 += c; c = h2 >> 26; h2 &= 0x3ffffffU;
	h3 += c; c = h3 >> 26; h3 &= 0x3ffffffU;
	h4 += c; c = h4 >> 26; h4 &= 0x3ffffffU;
	h0 += c * 5; c = h0 >> 26; h0 &= 0x3ffffffU;
	h1 += c;

	g0 = h0 + 5; c = g0 >> 26; g0 &= 0x3ffffffU;
	g1 = h1 + c; c = g1 >> 26; g1 &= 0x3ffffffU;
	g2 = h2 + c; c = g2 >> 26; g2 &= 0x3ffffffU;
	g3 = h3 + c; c = g3 >> 26; g3 &= 0x3ffffffU;
	g4 = h4 + c - (1U << 26);

	mask = (g4 >> 31) - 1;
	g0 &= mask; g1 &= mask; g2 &= mask; g3 &= mask; g4 &= mask;
	mask = ~mask;
	h0 = (h0 & mask) | g0; h1 = (h1 & mask) | g1;
	h2 = (h2 & mask) | g2; h3 = (h3 & mask) | g3; h4 = (h4 & mask) | g4;

	h0 = (h0 | (h1 << 26)) & 0xffffffffU;
	h1 = ((h1 >> 6) | (h2 << 20)) & 0xffffffffU;
	h2 = ((h2 >> 12) | (h3 << 14)) & 0xffffffffU;
	h3 = ((h3 >> 18) | (h4 << 8)) & 0xffffffffU;

	f = (u64)h0 + ctx->state.pad[0]; h0 = (u32)f;
	f = (u64)h1 + ctx->state.pad[1] + (f >> 32); h1 = (u32)f;
	f = (u64)h2 + ctx->state.pad[2] + (f >> 32); h2 = (u32)f;
	f = (u64)h3 + ctx->state.pad[3] + (f >> 32); h3 = (u32)f;

	put_unaligned_le32(h0, mac + 0);
	put_unaligned_le32(h1, mac + 4);
	put_unaligned_le32(h2, mac + 8);
	put_unaligned_le32(h3, mac + 12);
}

static void ip_decrease_ttl(struct iphdr *iph)
{
	u32 check = iph->check;
	check += htons(0x0100);
	iph->check = (u16)(check + (check >= 0xffff));
	--iph->ttl;
}

#include "wgfp_seal_one_dst_body.h"

static void ref_aead_encrypt(u8 *dst, const u8 *src, u32 len, u64 nonce, const u8 key[32])
{
	struct chacha_state st;
	u8 ks[64];

	wgfp_chacha_setup(&st, nonce, key);
	chacha_block_generic(&st, ks, 20);
	wgfp_chacha_gen(&st, dst, src, len);
	wgfp_mac(ks, dst, len, false, dst + len);
}

int main(void)
{
	static const u32 lens[] = { 1, 15, 16, 17, 20, 40, 63, 64, 65, 127, 128, 129,
				    191, 192, 255, 256, 257, 319, 320, 321,
				    576, 577, 1280, 1419, 1420, 1424, 1440, 1472, 1500 };
	u8 key[32], src[1500], src_copy[1516], pt[1500], ref[1516];
	u8 out_buf[16 + CHACHA_BLOCK_SIZE + 1516 + 16];
	u8 *out = out_buf + 16 + CHACHA_BLOCK_SIZE;
	u8 guard_a5[16], zero64[CHACHA_BLOCK_SIZE];

	memset(guard_a5, 0xa5, sizeof(guard_a5));
	memset(zero64, 0, sizeof(zero64));

	for (int i = 0; i < 32; i++)
		key[i] = 0x20 + i;

	for (size_t i = 0; i < ARRAY_SIZE(lens); i++) {
		u32 plain = lens[i];
		u64 nonce = 0x0f0e0d0cULL + plain;

		for (u32 pad = 0; pad <= 15; pad++) {
			if (pad >= plain)
				continue;
			u32 ip_len = plain - pad;
			u8 fams[3] = { 0, AF_INET, AF_INET6 };

			for (int f = 0; f < 3; f++) {
				u8 fam = fams[f];
				if (fam == AF_INET && ip_len < sizeof(struct iphdr))
					continue;
				if (fam == AF_INET6 && ip_len < sizeof(struct ipv6hdr))
					continue;
				for (u32 j = 0; j < ip_len; j++)
					src[j] = (u8)(j * 13 + plain + pad + f);
				if (fam == AF_INET) {
					struct iphdr *ip = (struct iphdr *)src;
					ip->ttl = 64;
					ip->check = htons(0x1234);
				} else if (fam == AF_INET6) {
					((struct ipv6hdr *)src)->hop_limit = 64;
				}
				memcpy(src_copy, src, ip_len);
				memcpy(pt, src, ip_len);
				if (plain > ip_len)
					memset(pt + ip_len, 0, plain - ip_len);
				if (fam == AF_INET)
					ip_decrease_ttl((struct iphdr *)pt);
				else if (fam == AF_INET6)
					((struct ipv6hdr *)pt)->hop_limit--;
				ref_aead_encrypt(ref, pt, plain, nonce, key);

				for (int nm = 0; nm < 2; nm++) {
					unsigned int neon_min = nm ? UINT_MAX : 0;

					for (int sb = 0; sb < 2; sb++) {
						int prev_sec = neon_sections;

						memset(out_buf, 0xa5, sizeof(out_buf));
						memset(out, 0x5a, plain + 16);
						wgfp_seal_one_dst(out, src, ip_len, plain, fam,
								  sb != 0, nonce, key, neon_min);
						assert(neon_depth == 0);
						assert(neon_sections == prev_sec + (nm ? 0 : 1));
						assert(memcmp(out, ref, plain + 16) == 0);
						assert(memcmp(src, src_copy, ip_len) == 0);
						/* Guard before dst - 64 is never touched */
						assert(memcmp(out_buf, guard_a5, 16) == 0);
						/* Trailing guard after ciphertext+tag is never touched */
						assert(memcmp(out + plain + 16, guard_a5, 16) == 0);
						if (!sb || nm) {
							/* No scratch_b0 (or non-NEON): dst-64..dst-1 untouched */
							for (int k = 0; k < CHACHA_BLOCK_SIZE; k++)
								assert(out_buf[16 + k] == 0xa5);
						} else {
							/* NEON + scratch_b0: block-0 keystream zeroized */
							assert(memcmp(out - CHACHA_BLOCK_SIZE, zero64,
								      CHACHA_BLOCK_SIZE) == 0);
						}
					}
				}
			}
		}
	}
	/* Exercise multi-iteration tail padding loop (> 256 zero-pad bytes) */
	{
		u32 plain = 1420, ip_len = 800;
		u64 nonce = 0x55aa1122ULL;

		for (u32 j = 0; j < ip_len; j++)
			src[j] = (u8)(j * 17 + 3);
		memcpy(src_copy, src, ip_len);
		memcpy(pt, src, ip_len);
		memset(pt + ip_len, 0, plain - ip_len);
		ref_aead_encrypt(ref, pt, plain, nonce, key);
		for (int nm = 0; nm < 2; nm++) {
			for (int sb = 0; sb < 2; sb++) {
				memset(out_buf, 0xa5, sizeof(out_buf));
				memset(out, 0x5a, plain + 16);
				wgfp_seal_one_dst(out, src, ip_len, plain, 0,
						  sb != 0, nonce, key, nm ? UINT_MAX : 0);
				assert(memcmp(out, ref, plain + 16) == 0);
				assert(memcmp(src, src_copy, ip_len) == 0);
				assert(memcmp(out_buf, guard_a5, 16) == 0);
				assert(memcmp(out + plain + 16, guard_a5, 16) == 0);
				if (!sb || nm) {
					for (int k = 0; k < CHACHA_BLOCK_SIZE; k++)
						assert(out_buf[16 + k] == 0xa5);
				} else {
					assert(memcmp(out - CHACHA_BLOCK_SIZE, zero64,
						      CHACHA_BLOCK_SIZE) == 0);
				}
			}
		}
	}
	/* Verify wgfp_open_one_dst() (valid decrypt, forged-tag dst invariance,
	 * no scratch writes before dst, and read-only src invariance) */
	for (size_t li = 0; li < ARRAY_SIZE(lens); li++) {
		u32 plain = lens[li];
		u64 nonce = 0x7766554433221100ULL + plain;

		for (u32 j = 0; j < plain; j++)
			pt[j] = (u8)(j * 19 + plain);
		ref_aead_encrypt(ref, pt, plain, nonce, key);
		memcpy(src_copy, ref, plain + 16);

		for (int nm = 0; nm < 2; nm++) {
			unsigned int neon_min = nm ? UINT_MAX : 0;
			int prev_sec = neon_sections;
			bool ok;

			memset(out_buf, 0xa5, sizeof(out_buf));
			memset(out, 0x5a, plain);
			ok = wgfp_open_one_dst(out, ref, plain, nonce, key,
					       neon_min);
			assert(ok);
			assert(neon_depth == 0);
			assert(neon_sections == prev_sec + (nm ? 0 : 1));
			assert(memcmp(out, pt, plain) == 0);
			assert(memcmp(ref, src_copy, plain + 16) == 0);
			assert(memcmp(out_buf, guard_a5, 16) == 0);
			assert(memcmp(out + plain, guard_a5, 16) == 0);
			for (int k = 0; k < CHACHA_BLOCK_SIZE; k++)
				assert(out_buf[16 + k] == 0xa5);

			/* Forged tag: must fail and leave dst + src untouched */
			ref[plain] ^= 0x01;
			memset(out_buf, 0xa5, sizeof(out_buf));
			memset(out, 0x5a, plain);
			ok = wgfp_open_one_dst(out, ref, plain, nonce, key,
					       neon_min);
			assert(!ok);
			assert(neon_depth == 0);
			assert(memcmp(out_buf, guard_a5, 16) == 0);
			assert(memcmp(out + plain, guard_a5, 16) == 0);
			for (int k = 0; k < CHACHA_BLOCK_SIZE; k++)
				assert(out_buf[16 + k] == 0xa5);
			for (u32 k = 0; k < plain; k++)
				assert(out[k] == 0x5a);
			ref[plain] ^= 0x01;
		}
	}
	puts("wgfp_seal_one_dst: ok");
	return 0;
}
