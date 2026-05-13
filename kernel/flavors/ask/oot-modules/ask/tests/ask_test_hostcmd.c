// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - hostcmd encoder/decoder kunit suite (PR6 / M1.2)
 *
 * Golden-hex tests against spec §12.1-§12.5. Every test below pins
 * the exact byte stream the encoder must produce. The §12.5 canonical
 * example (IPv4 TCP NAT flow insert) is reproduced verbatim and
 * compared byte-for-byte with KUNIT_EXPECT_MEMEQ. If the wire format
 * ever drifts these tests fail at compile-and-load time on every CI
 * run before a release tag goes out.
 *
 * The encoders are pure byte-shuffling so there is no hardware
 * dependency — these tests run in any kunit-enabled qemu/kvm guest
 * and on the live LS1046A target equally well.
 */

#include <kunit/test.h>
#include <linux/types.h>
#include <linux/string.h>
#include <linux/byteorder/generic.h>

#include "../include/ask_internal.h"

/* -------------------------------------------------------------------------
 * Header helper test
 * ------------------------------------------------------------------------- */

static void ask_test_hc_get_ucode_version_header(struct kunit *test)
{
u8 buf[8] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };
const u8 expect[ASK_HOSTCMD_HDR_LEN] = { 0x01, 0x00, 0x00, 0x00 };
int n;

n = ask_hostcmd_enc_get_ucode_version(buf, sizeof(buf));
KUNIT_ASSERT_EQ(test, n, ASK_HOSTCMD_HDR_LEN);
KUNIT_EXPECT_MEMEQ(test, buf, expect, ASK_HOSTCMD_HDR_LEN);
/* sentinel bytes after the frame must be untouched */
KUNIT_EXPECT_EQ(test, buf[4], 0xff);
}

static void ask_test_hc_reset_tables_header(struct kunit *test)
{
u8 buf[8] = { 0 };
const u8 expect[ASK_HOSTCMD_HDR_LEN] = { 0x04, 0x00, 0x00, 0x00 };
int n;

n = ask_hostcmd_enc_reset_tables(buf, sizeof(buf));
KUNIT_ASSERT_EQ(test, n, ASK_HOSTCMD_HDR_LEN);
KUNIT_EXPECT_MEMEQ(test, buf, expect, ASK_HOSTCMD_HDR_LEN);
}

static void ask_test_hc_header_overflow(struct kunit *test)
{
u8 buf[2];

KUNIT_EXPECT_EQ(test,
ask_hostcmd_enc_get_ucode_version(buf, sizeof(buf)),
-ENOBUFS);
}

static void ask_test_hc_null_buf(struct kunit *test)
{
KUNIT_EXPECT_EQ(test,
ask_hostcmd_enc_get_ucode_version(NULL, 16),
-EINVAL);
}

/* -------------------------------------------------------------------------
 * Spec §12.5 canonical golden bytes — IPv4 TCP NAT flow insert
 *
 * Goal: 192.168.1.5:5000 -> 8.8.8.8:443, egress eth3 (port 6),
 *       SNAT+PAT to 203.0.113.10:42000, src MAC 00:11:22:33:44:55,
 *       dst MAC aa:bb:cc:dd:ee:ff.
 *
 * Expected wire bytes (68 total = 4 header + 64 payload):
 *
 *   10 00 00 40                                  header
 *   c0 a8 01 05  08 08 08 08  13 88 01 bb        key: srcip,dstip,sport,dport
 *   00 00 00 01  00 00 00 00 00 00              iif, vlan+rsv
 *   00 00 00 07  00 00 00 06                    flags(TTL_DEC|NAT_SRC|PAT),oif
 *   00 11 22 33 44 55  aa bb cc dd ee ff        src MAC, dst MAC
 *   cb 00 71 0a  00 00 00 00                    rewrite_src_ip, rewrite_dst_ip
 *   a4 10  00 00                                 rewrite_sport, rewrite_dport
 *   00 00 00 00 00 00                            vlan + 4 bytes reserved tail
 *
 * Per spec §12.4 the action is exactly 40 bytes; the v4 action layout
 * we encode pads to 40 with zeros after offset 34. Verified against
 * the literal hex block in spec §12.5.
 * ------------------------------------------------------------------------- */

static void ask_test_hc_flow_insert_v4_tcp_golden(struct kunit *test)
{
struct ask_hw_flow_key_v4 key = {
.src_ip   = cpu_to_be32(0xC0A80105), /* 192.168.1.5 */
.dst_ip   = cpu_to_be32(0x08080808), /* 8.8.8.8 */
.sport    = cpu_to_be16(5000),
.dport    = cpu_to_be16(443),
.iif      = 1,
.vlan_id  = 0,
};
struct ask_hw_action_v4 act = {
.flags = ASK_ACT_TTL_DEC | ASK_ACT_NAT_SRC | ASK_ACT_PAT,
.oif   = 6,
.rewrite_src_mac = { 0x00, 0x11, 0x22, 0x33, 0x44, 0x55 },
.rewrite_dst_mac = { 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff },
.rewrite_src_ip = cpu_to_be32(0xCB00710A), /* 203.0.113.10 */
.rewrite_dst_ip = cpu_to_be32(0x00000000),
.rewrite_sport = cpu_to_be16(42000),
.rewrite_dport = cpu_to_be16(0),
.vlan_id = 0,
};
const u8 expect[68] = {
/* header: op=0x10, rsv=0, len=0x0040 */
0x10, 0x00, 0x00, 0x40,

/* key (24 bytes) */
0xc0, 0xa8, 0x01, 0x05,
0x08, 0x08, 0x08, 0x08,
0x13, 0x88, 0x01, 0xbb,
0x00, 0x00, 0x00, 0x01,
0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
0x00, 0x00, /* trailing 2 bytes of key reserved */

/* action (40 bytes) */
0x00, 0x00, 0x00, 0x07,
0x00, 0x00, 0x00, 0x06,
0x00, 0x11, 0x22, 0x33, 0x44, 0x55,
0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff,
0xcb, 0x00, 0x71, 0x0a,
0x00, 0x00, 0x00, 0x00,
0xa4, 0x10,
0x00, 0x00,
0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};
u8 buf[ASK_HOSTCMD_MAX_FRAME];
int n;

memset(buf, 0xee, sizeof(buf));
n = ask_hostcmd_enc_flow_insert_v4(ASK_OP_FLOW_INSERT_V4_TCP,
   &key, &act,
   buf, sizeof(buf));
KUNIT_ASSERT_EQ(test, n, 68);
KUNIT_EXPECT_MEMEQ(test, buf, expect, 68);

/* sentinel: byte at offset 68 must be untouched */
KUNIT_EXPECT_EQ(test, buf[68], 0xee);
}

static void ask_test_hc_flow_insert_v4_udp_opcode(struct kunit *test)
{
struct ask_hw_flow_key_v4 key = { .iif = 1 };
struct ask_hw_action_v4 act   = { .oif = 1 };
u8 buf[ASK_HOSTCMD_MAX_FRAME];
int n;

n = ask_hostcmd_enc_flow_insert_v4(ASK_OP_FLOW_INSERT_V4_UDP,
   &key, &act,
   buf, sizeof(buf));
KUNIT_ASSERT_EQ(test, n, 68);
KUNIT_EXPECT_EQ(test, buf[0], ASK_OP_FLOW_INSERT_V4_UDP);
KUNIT_EXPECT_EQ(test, buf[1], 0);
KUNIT_EXPECT_EQ(test, buf[2], 0);
KUNIT_EXPECT_EQ(test, buf[3], 64);
}

static void ask_test_hc_flow_insert_v4_bad_opcode(struct kunit *test)
{
struct ask_hw_flow_key_v4 key = { .iif = 1 };
struct ask_hw_action_v4 act   = { .oif = 1 };
u8 buf[ASK_HOSTCMD_MAX_FRAME];

KUNIT_EXPECT_EQ(test,
ask_hostcmd_enc_flow_insert_v4(0x99, &key, &act,
       buf, sizeof(buf)),
-EINVAL);
}

/* -------------------------------------------------------------------------
 * Flow remove + query stats
 * ------------------------------------------------------------------------- */

static void ask_test_hc_flow_remove_golden(struct kunit *test)
{
const u8 expect[8] = {
0x18, 0x00, 0x00, 0x04,
0x12, 0x34, 0x56, 0x78,
};
u8 buf[16];
int n;

n = ask_hostcmd_enc_flow_remove(0x12345678, buf, sizeof(buf));
KUNIT_ASSERT_EQ(test, n, 8);
KUNIT_EXPECT_MEMEQ(test, buf, expect, 8);
}

static void ask_test_hc_flow_query_stats_golden(struct kunit *test)
{
const u8 expect[8] = {
0x19, 0x00, 0x00, 0x04,
0x00, 0x00, 0x12, 0x34,
};
u8 buf[16];
int n;

n = ask_hostcmd_enc_flow_query_stats(0x1234, buf, sizeof(buf));
KUNIT_ASSERT_EQ(test, n, 8);
KUNIT_EXPECT_MEMEQ(test, buf, expect, 8);
}

/* -------------------------------------------------------------------------
 * Policer + offline-port flush
 * ------------------------------------------------------------------------- */

static void ask_test_hc_policer_set_golden(struct kunit *test)
{
struct ask_hw_policer pol = {
.port_id = 6,
.rate_bps = 1000000,        /* 1 Mbps */
.burst_bytes = 16384,       /* 16 KiB */
};
const u8 expect[13] = {
0x40, 0x00, 0x00, 0x09,
0x06,
0x00, 0x0f, 0x42, 0x40,     /* 1000000 BE */
0x00, 0x00, 0x40, 0x00,     /* 16384 BE */
};
u8 buf[16];
int n;

n = ask_hostcmd_enc_policer_set(&pol, buf, sizeof(buf));
KUNIT_ASSERT_EQ(test, n, 13);
KUNIT_EXPECT_MEMEQ(test, buf, expect, 13);
}

static void ask_test_hc_op_flush_golden(struct kunit *test)
{
const u8 expect[5] = { 0x31, 0x00, 0x00, 0x01, 0x06 };
u8 buf[8];
int n;

n = ask_hostcmd_enc_op_flush(6, buf, sizeof(buf));
KUNIT_ASSERT_EQ(test, n, 5);
KUNIT_EXPECT_MEMEQ(test, buf, expect, 5);
}

/* -------------------------------------------------------------------------
 * IPsec SA insert (v4 ESP) - 84-byte frame, only structure spot-checks
 * since the full key material is opaque
 * ------------------------------------------------------------------------- */

static void ask_test_hc_sa_insert_v4_esp_structure(struct kunit *test)
{
struct ask_hw_sa_v4 sa = {
.spi = cpu_to_be32(0xDEADBEEF),
.dst_ip = cpu_to_be32(0xC0A80101),
.caam_rx_fqid = 0x100,
.op_inject_fqid = 0x200,
};
u8 buf[ASK_HOSTCMD_MAX_FRAME];
int n;
int i;

memset(sa.key_material, 0xAA, sizeof(sa.key_material));
memset(buf, 0, sizeof(buf));

n = ask_hostcmd_enc_sa_insert_v4_esp(&sa, buf, sizeof(buf));
KUNIT_ASSERT_EQ(test, n, 4 + 80);

/* header: op=0x20, rsv=0, len=0x50 */
KUNIT_EXPECT_EQ(test, buf[0], 0x20);
KUNIT_EXPECT_EQ(test, buf[1], 0x00);
KUNIT_EXPECT_EQ(test, buf[2], 0x00);
KUNIT_EXPECT_EQ(test, buf[3], 0x50);

/* SPI BE at +0 */
KUNIT_EXPECT_EQ(test, buf[4 +  0], 0xDE);
KUNIT_EXPECT_EQ(test, buf[4 +  1], 0xAD);
KUNIT_EXPECT_EQ(test, buf[4 +  2], 0xBE);
KUNIT_EXPECT_EQ(test, buf[4 +  3], 0xEF);

/* dst IP BE at +4 */
KUNIT_EXPECT_EQ(test, buf[4 +  4], 0xC0);
KUNIT_EXPECT_EQ(test, buf[4 +  5], 0xA8);
KUNIT_EXPECT_EQ(test, buf[4 +  6], 0x01);
KUNIT_EXPECT_EQ(test, buf[4 +  7], 0x01);

/* caam_rx_fqid BE at +8 */
KUNIT_EXPECT_EQ(test, buf[4 +  8], 0x00);
KUNIT_EXPECT_EQ(test, buf[4 +  9], 0x00);
KUNIT_EXPECT_EQ(test, buf[4 + 10], 0x01);
KUNIT_EXPECT_EQ(test, buf[4 + 11], 0x00);

/* op_inject_fqid BE at +12 */
KUNIT_EXPECT_EQ(test, buf[4 + 12], 0x00);
KUNIT_EXPECT_EQ(test, buf[4 + 13], 0x00);
KUNIT_EXPECT_EQ(test, buf[4 + 14], 0x02);
KUNIT_EXPECT_EQ(test, buf[4 + 15], 0x00);

/* +16..19 reserved */
for (i = 16; i < 20; i++)
KUNIT_EXPECT_EQ(test, buf[4 + i], 0x00);

/* key material (60 bytes of 0xAA) starts at +20 */
for (i = 0; i < 60; i++)
KUNIT_EXPECT_EQ(test, buf[4 + 20 + i], 0xAA);
}

static void ask_test_hc_sa_remove_golden(struct kunit *test)
{
const u8 expect[8] = {
0x28, 0x00, 0x00, 0x04,
0xCA, 0xFE, 0xBA, 0xBE,
};
u8 buf[16];
int n;

n = ask_hostcmd_enc_sa_remove(0xCAFEBABE, buf, sizeof(buf));
KUNIT_ASSERT_EQ(test, n, 8);
KUNIT_EXPECT_MEMEQ(test, buf, expect, 8);
}

/* -------------------------------------------------------------------------
 * Decoders
 * ------------------------------------------------------------------------- */

static void ask_test_hc_dec_ucode_version_golden(struct kunit *test)
{
const u8 frame[10] = {
0x01, 0x00, 0x00, 0x06,
0x02, 0x10,         /* family 0x0210 */
0x05,               /* major 5 */
0x03,               /* minor 3 */
0x00, 0x01,         /* patch 1 */
};
u16 family = 0, patch = 0;
u8 major = 0, minor = 0;
int rc;

rc = ask_hostcmd_dec_ucode_version(frame, sizeof(frame),
   &family, &major,
   &minor, &patch);
KUNIT_ASSERT_EQ(test, rc, 0);
KUNIT_EXPECT_EQ(test, family, 0x0210);
KUNIT_EXPECT_EQ(test, major, 5);
KUNIT_EXPECT_EQ(test, minor, 3);
KUNIT_EXPECT_EQ(test, patch, 1);
}

static void ask_test_hc_dec_ucode_version_bad_op(struct kunit *test)
{
const u8 frame[10] = {
0x99, 0x00, 0x00, 0x06,
0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};
u16 family = 0, patch = 0;
u8 major = 0, minor = 0;

KUNIT_EXPECT_EQ(test,
ask_hostcmd_dec_ucode_version(frame, sizeof(frame),
      &family, &major,
      &minor, &patch),
-EPROTO);
}

static void ask_test_hc_dec_ucode_version_bad_len(struct kunit *test)
{
const u8 frame[6] = {
0x01, 0x00, 0x00, 0x02,
0x00, 0x00,
};
u16 family = 0, patch = 0;
u8 major = 0, minor = 0;

KUNIT_EXPECT_EQ(test,
ask_hostcmd_dec_ucode_version(frame, sizeof(frame),
      &family, &major,
      &minor, &patch),
-EINVAL);
}

static void ask_test_hc_dec_ucode_version_bad_reserved(struct kunit *test)
{
const u8 frame[10] = {
0x01, 0xFF, 0x00, 0x06,         /* reserved byte != 0 */
0x02, 0x10, 0x05, 0x03, 0x00, 0x01,
};
u16 family = 0, patch = 0;
u8 major = 0, minor = 0;

KUNIT_EXPECT_EQ(test,
ask_hostcmd_dec_ucode_version(frame, sizeof(frame),
      &family, &major,
      &minor, &patch),
-EINVAL);
}

static void ask_test_hc_dec_flow_insert_golden(struct kunit *test)
{
const u8 frame[8] = {
0x10, 0x00, 0x00, 0x04,
0x00, 0x00, 0x12, 0x34,
};
u32 hw_flow_id = 0;
int rc;

rc = ask_hostcmd_dec_flow_insert(frame, sizeof(frame),
 ASK_OP_FLOW_INSERT_V4_TCP,
 &hw_flow_id);
KUNIT_ASSERT_EQ(test, rc, 0);
KUNIT_EXPECT_EQ(test, hw_flow_id, 0x1234u);
}

static void ask_test_hc_dec_flow_insert_op_mismatch(struct kunit *test)
{
const u8 frame[8] = {
0x11, 0x00, 0x00, 0x04,
0x00, 0x00, 0x00, 0x00,
};
u32 hw_flow_id = 0;

KUNIT_EXPECT_EQ(test,
ask_hostcmd_dec_flow_insert(frame, sizeof(frame),
    ASK_OP_FLOW_INSERT_V4_TCP,
    &hw_flow_id),
-EPROTO);
}

static void ask_test_hc_dec_flow_query_stats_golden(struct kunit *test)
{
const u8 frame[20] = {
0x19, 0x00, 0x00, 0x10,
0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x40, 0x00,  /* 16384 bytes */
0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x10,  /* 16 packets */
};
u64 bytes = 0, packets = 0;
int rc;

rc = ask_hostcmd_dec_flow_query_stats(frame, sizeof(frame),
      &bytes, &packets);
KUNIT_ASSERT_EQ(test, rc, 0);
KUNIT_EXPECT_EQ(test, bytes, 16384ULL);
KUNIT_EXPECT_EQ(test, packets, 16ULL);
}

static void ask_test_hc_dec_sa_insert_golden(struct kunit *test)
{
const u8 frame[8] = {
0x20, 0x00, 0x00, 0x04,
0x00, 0xAB, 0xCD, 0xEF,
};
u32 hw_sa_id = 0;
int rc;

rc = ask_hostcmd_dec_sa_insert(frame, sizeof(frame),
       ASK_OP_SA_INSERT_V4_ESP, &hw_sa_id);
KUNIT_ASSERT_EQ(test, rc, 0);
KUNIT_EXPECT_EQ(test, hw_sa_id, 0x00ABCDEFu);
}

/* -------------------------------------------------------------------------
 * Round-trip: encode -> decode flow_remove echo response
 * ------------------------------------------------------------------------- */

static void ask_test_hc_round_trip_flow_remove_response(struct kunit *test)
{
/*
 * The sender encodes a flow_remove command (0x18). The microcode
 * reflects the opcode and returns hw_flow_id in the body. Build
 * the synthetic response and parse it back through
 * ask_hostcmd_dec_flow_insert() (which also accepts the 0x18
 * opcode echo for symmetry — its only check is opcode == expected).
 */
u8 send_buf[8];
u8 resp[8];
u32 parsed = 0;
int n;

n = ask_hostcmd_enc_flow_remove(0xDEADBEEF, send_buf, sizeof(send_buf));
KUNIT_ASSERT_EQ(test, n, 8);

/* Build response: opcode echo + 4-byte hw_flow_id payload */
resp[0] = ASK_OP_FLOW_REMOVE;
resp[1] = 0;
resp[2] = 0;
resp[3] = 4;
resp[4] = 0x00; resp[5] = 0x00; resp[6] = 0x12; resp[7] = 0x34;

KUNIT_EXPECT_EQ(test,
ask_hostcmd_dec_flow_insert(resp, sizeof(resp),
    ASK_OP_FLOW_REMOVE,
    &parsed),
0);
KUNIT_EXPECT_EQ(test, parsed, 0x1234u);
}

/* -------------------------------------------------------------------------
 * Suite registration
 * ------------------------------------------------------------------------- */

static struct kunit_case ask_hostcmd_test_cases[] = {
KUNIT_CASE(ask_test_hc_get_ucode_version_header),
KUNIT_CASE(ask_test_hc_reset_tables_header),
KUNIT_CASE(ask_test_hc_header_overflow),
KUNIT_CASE(ask_test_hc_null_buf),
KUNIT_CASE(ask_test_hc_flow_insert_v4_tcp_golden),
KUNIT_CASE(ask_test_hc_flow_insert_v4_udp_opcode),
KUNIT_CASE(ask_test_hc_flow_insert_v4_bad_opcode),
KUNIT_CASE(ask_test_hc_flow_remove_golden),
KUNIT_CASE(ask_test_hc_flow_query_stats_golden),
KUNIT_CASE(ask_test_hc_policer_set_golden),
KUNIT_CASE(ask_test_hc_op_flush_golden),
KUNIT_CASE(ask_test_hc_sa_insert_v4_esp_structure),
KUNIT_CASE(ask_test_hc_sa_remove_golden),
KUNIT_CASE(ask_test_hc_dec_ucode_version_golden),
KUNIT_CASE(ask_test_hc_dec_ucode_version_bad_op),
KUNIT_CASE(ask_test_hc_dec_ucode_version_bad_len),
KUNIT_CASE(ask_test_hc_dec_ucode_version_bad_reserved),
KUNIT_CASE(ask_test_hc_dec_flow_insert_golden),
KUNIT_CASE(ask_test_hc_dec_flow_insert_op_mismatch),
KUNIT_CASE(ask_test_hc_dec_flow_query_stats_golden),
KUNIT_CASE(ask_test_hc_dec_sa_insert_golden),
KUNIT_CASE(ask_test_hc_round_trip_flow_remove_response),
{}
};

struct kunit_suite ask_hostcmd_suite = {
.name = "ask_hostcmd",
.test_cases = ask_hostcmd_test_cases,
};