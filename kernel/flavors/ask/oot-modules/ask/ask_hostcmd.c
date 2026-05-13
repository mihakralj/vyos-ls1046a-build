// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - hostcmd subsystem (PR6 / M1.2)
 *
 * Wire-format encoders and decoders for the FMan 210 host-command
 * protocol described in spec §12.1-§12.6. Pure byte-shuffling: nothing
 * in this file touches hardware. The real I/O path (fman_host_cmd_send)
 * lands in M2 once the kernel patch 0003-fman-host-command-api.patch
 * has a functional implementation; until then PR7-PR8 stitch the
 * encoders into the flow_block_cb path and rely on the stubbed-out
 * sender returning -EOPNOTSUPP.
 *
 * Wire format reminder (spec §12.1):
 *
 *   +----+----+--------+--------+-----------+
 *   | op | rs | len_hi | len_lo | payload   |
 *   +----+----+--------+--------+-----------+
 *     u8   u8    u8       u8      <= 1020 bytes
 *
 * Multi-byte payload fields are big-endian. The encoders take typed
 * native-endian structs (where IPv4/IPv6 addresses and L4 ports are
 * already __be* per Linux convention) and serialize them into a flat
 * byte buffer. Decoders do the inverse for response frames.
 *
 * The kunit suite in tests/ask_test_hostcmd.c golden-tests every byte
 * of every encoder against the canonical example in spec §12.5.
 */

#include <linux/kernel.h>
#include <linux/types.h>
#include <linux/string.h>
#include <linux/errno.h>
#include <linux/byteorder/generic.h>
#include <linux/unaligned.h>

#include "include/ask_internal.h"
#include "ask_trace.h"

/* -------------------------------------------------------------------------
 * Header pack/unpack helpers
 * ------------------------------------------------------------------------- */

static int ask_hc_write_header(u8 *buf, size_t buf_len, u8 op, u16 payload_len)
{
if (payload_len > ASK_HOSTCMD_MAX_PAYLOAD)
return -E2BIG;
if (buf_len < (size_t)ASK_HOSTCMD_HDR_LEN + payload_len)
return -ENOBUFS;

buf[0] = op;
buf[1] = 0; /* reserved */
put_unaligned_be16(payload_len, &buf[2]);
return 0;
}

static int ask_hc_parse_header(const u8 *buf, size_t buf_len,
       u8 *op, u16 *payload_len)
{
u16 len;

if (buf_len < ASK_HOSTCMD_HDR_LEN)
return -EINVAL;
if (buf[1] != 0)
return -EINVAL;

len = get_unaligned_be16(&buf[2]);
if (len > ASK_HOSTCMD_MAX_PAYLOAD)
return -EINVAL;
if (buf_len < (size_t)ASK_HOSTCMD_HDR_LEN + len)
return -EINVAL;

*op = buf[0];
*payload_len = len;
return 0;
}

/* -------------------------------------------------------------------------
 * System-class encoders
 * ------------------------------------------------------------------------- */

int ask_hostcmd_enc_get_ucode_version(void *buf, size_t buf_len)
{
int rc;

if (!buf)
return -EINVAL;

rc = ask_hc_write_header(buf, buf_len,
 ASK_OP_GET_UCODE_VERSION, 0);
if (rc)
return rc;

trace_ask_hostcmd_send(ASK_OP_GET_UCODE_VERSION, 0, NULL);
return ASK_HOSTCMD_HDR_LEN;
}
EXPORT_SYMBOL_GPL(ask_hostcmd_enc_get_ucode_version);

int ask_hostcmd_enc_reset_tables(void *buf, size_t buf_len)
{
int rc;

if (!buf)
return -EINVAL;

rc = ask_hc_write_header(buf, buf_len, ASK_OP_RESET_TABLES, 0);
if (rc)
return rc;

trace_ask_hostcmd_send(ASK_OP_RESET_TABLES, 0, NULL);
return ASK_HOSTCMD_HDR_LEN;
}
EXPORT_SYMBOL_GPL(ask_hostcmd_enc_reset_tables);

/* -------------------------------------------------------------------------
 * Flow key/action encoders (shared by INSERT_V4_TCP/UDP and INSERT_V6_*)
 *
 * IPv4 key layout (spec §12.3, 24 bytes):
 *
 *   off  size  field
 *     0    4   src_ip      (BE)
 *     4    4   dst_ip      (BE)
 *     8    2   sport       (BE)
 *    10    2   dport       (BE)
 *    12    4   iif         (BE)
 *    16    2   vlan_id     (BE)
 *    18    6   reserved
 *
 * IPv4 action layout (spec §12.4, 40 bytes):
 *
 *   off  size  field
 *     0    4   action_flags (BE)
 *     4    4   oif          (BE)
 *     8    6   rewrite_src_mac
 *    14    6   rewrite_dst_mac
 *    20    4   rewrite_src_ip (BE)
 *    24    4   rewrite_dst_ip (BE)
 *    28    2   rewrite_sport  (BE)
 *    30    2   rewrite_dport  (BE)
 *    32    2   vlan_id        (BE)
 *    34    6   reserved
 *
 * Note the trailing 4 bytes shown in the action diagram (vlan + 2 rsv)
 * still puts us at exactly 40 bytes when paired with this 6-byte tail.
 * The diagram in the spec uses "reserved (2)" but the action TOTAL is
 * documented as 40 — so the real reserved tail is 6 bytes (we need 36
 * bytes of named fields + 4 bytes flags slot already accounted for).
 * Re-checked against the canonical golden bytes in §12.5: the wire
 * stops at offset 38 of action (the "vlan/rsv 00 00 00 00" line).
 * The remaining padding to 40 is also zero. We zero-init the buffer
 * so any unspecified bytes are safely zero.
 * ------------------------------------------------------------------------- */

static void ask_enc_flow_key_v4(u8 *p, const struct ask_hw_flow_key_v4 *key)
{
memset(p, 0, ASK_FLOW_KEY_V4_LEN);
put_unaligned_be32(be32_to_cpu(key->src_ip),  p +  0);
put_unaligned_be32(be32_to_cpu(key->dst_ip),  p +  4);
put_unaligned_be16(be16_to_cpu(key->sport),   p +  8);
put_unaligned_be16(be16_to_cpu(key->dport),   p + 10);
put_unaligned_be32(key->iif,                  p + 12);
put_unaligned_be16(key->vlan_id,              p + 16);
/* p[18..23] reserved, already zero */
}

static void ask_enc_flow_action_v4(u8 *p, const struct ask_hw_action_v4 *act)
{
memset(p, 0, ASK_FLOW_ACTION_V4_LEN);
put_unaligned_be32(act->flags,                  p +  0);
put_unaligned_be32(act->oif,                    p +  4);
memcpy(p +  8, act->rewrite_src_mac, 6);
memcpy(p + 14, act->rewrite_dst_mac, 6);
put_unaligned_be32(be32_to_cpu(act->rewrite_src_ip), p + 20);
put_unaligned_be32(be32_to_cpu(act->rewrite_dst_ip), p + 24);
put_unaligned_be16(be16_to_cpu(act->rewrite_sport),  p + 28);
put_unaligned_be16(be16_to_cpu(act->rewrite_dport),  p + 30);
put_unaligned_be16(act->vlan_id,                     p + 32);
/* p[34..39] reserved, already zero */
}

static void ask_enc_flow_key_v6(u8 *p, const struct ask_hw_flow_key_v6 *key)
{
memset(p, 0, ASK_FLOW_KEY_V6_LEN);
memcpy(p +  0, key->src_ip, 16);
memcpy(p + 16, key->dst_ip, 16);
put_unaligned_be16(be16_to_cpu(key->sport), p + 32);
put_unaligned_be16(be16_to_cpu(key->dport), p + 34);
put_unaligned_be32(key->iif,                p + 36);
put_unaligned_be16(key->vlan_id,            p + 40);
/* p[42..47] reserved, already zero */
}

static void ask_enc_flow_action_v6(u8 *p, const struct ask_hw_action_v6 *act)
{
memset(p, 0, ASK_FLOW_ACTION_V6_LEN);
put_unaligned_be32(act->flags, p + 0);
put_unaligned_be32(act->oif,   p + 4);
memcpy(p +  8, act->rewrite_src_mac, 6);
memcpy(p + 14, act->rewrite_dst_mac, 6);
/*
 * v6 action lays the rewrite IPs back-to-back where v4 had the
 * single 4-byte rewrites; the action total is 48 bytes so we have
 * 28 bytes left after offset 20 for src_ip(16) + dst_ip(16) = 32,
 * which is more than fits — therefore the v6 action elides the
 * dst-IP rewrite slot (NAT66 is not v1.0 territory) and only
 * carries src-IP rewrite. The dst-IP rewrite is impossible with a
 * single 16-byte slot in 28 remaining bytes so the spec leaves it
 * to a follow-up opcode if ever needed. Keep the field on the
 * struct for ABI symmetry but do not serialize it.
 */
memcpy(p + 20, act->rewrite_src_ip, 16);
put_unaligned_be16(be16_to_cpu(act->rewrite_sport), p + 36);
put_unaligned_be16(be16_to_cpu(act->rewrite_dport), p + 38);
put_unaligned_be16(act->vlan_id,                    p + 40);
/* p[42..47] reserved, already zero */
}

int ask_hostcmd_enc_flow_insert_v4(u8 op,
   const struct ask_hw_flow_key_v4 *key,
   const struct ask_hw_action_v4 *act,
   void *buf, size_t buf_len)
{
const u16 payload = ASK_FLOW_KEY_V4_LEN + ASK_FLOW_ACTION_V4_LEN;
u8 *p = buf;
int rc;

if (!buf || !key || !act)
return -EINVAL;
if (op != ASK_OP_FLOW_INSERT_V4_TCP &&
    op != ASK_OP_FLOW_INSERT_V4_UDP &&
    op != ASK_OP_FLOW_INSERT_V4_MCAST &&
    op != ASK_OP_FLOW_INSERT_BRIDGE)
return -EINVAL;

rc = ask_hc_write_header(p, buf_len, op, payload);
if (rc)
return rc;

ask_enc_flow_key_v4(p + ASK_HOSTCMD_HDR_LEN, key);
ask_enc_flow_action_v4(p + ASK_HOSTCMD_HDR_LEN + ASK_FLOW_KEY_V4_LEN,
       act);

trace_ask_hostcmd_send(op, payload, p + ASK_HOSTCMD_HDR_LEN);
return ASK_HOSTCMD_HDR_LEN + payload;
}
EXPORT_SYMBOL_GPL(ask_hostcmd_enc_flow_insert_v4);

int ask_hostcmd_enc_flow_insert_v6(u8 op,
   const struct ask_hw_flow_key_v6 *key,
   const struct ask_hw_action_v6 *act,
   void *buf, size_t buf_len)
{
const u16 payload = ASK_FLOW_KEY_V6_LEN + ASK_FLOW_ACTION_V6_LEN;
u8 *p = buf;
int rc;

if (!buf || !key || !act)
return -EINVAL;
if (op != ASK_OP_FLOW_INSERT_V6_TCP &&
    op != ASK_OP_FLOW_INSERT_V6_UDP &&
    op != ASK_OP_FLOW_INSERT_V6_MCAST)
return -EINVAL;

rc = ask_hc_write_header(p, buf_len, op, payload);
if (rc)
return rc;

ask_enc_flow_key_v6(p + ASK_HOSTCMD_HDR_LEN, key);
ask_enc_flow_action_v6(p + ASK_HOSTCMD_HDR_LEN + ASK_FLOW_KEY_V6_LEN,
       act);

trace_ask_hostcmd_send(op, payload, p + ASK_HOSTCMD_HDR_LEN);
return ASK_HOSTCMD_HDR_LEN + payload;
}
EXPORT_SYMBOL_GPL(ask_hostcmd_enc_flow_insert_v6);

int ask_hostcmd_enc_flow_remove(u32 hw_flow_id, void *buf, size_t buf_len)
{
u8 *p = buf;
int rc;

if (!buf)
return -EINVAL;

rc = ask_hc_write_header(p, buf_len, ASK_OP_FLOW_REMOVE, 4);
if (rc)
return rc;

put_unaligned_be32(hw_flow_id, p + ASK_HOSTCMD_HDR_LEN);
trace_ask_hostcmd_send(ASK_OP_FLOW_REMOVE, 4,
       p + ASK_HOSTCMD_HDR_LEN);
return ASK_HOSTCMD_HDR_LEN + 4;
}
EXPORT_SYMBOL_GPL(ask_hostcmd_enc_flow_remove);

int ask_hostcmd_enc_flow_query_stats(u32 hw_flow_id,
     void *buf, size_t buf_len)
{
u8 *p = buf;
int rc;

if (!buf)
return -EINVAL;

rc = ask_hc_write_header(p, buf_len, ASK_OP_FLOW_QUERY_STATS, 4);
if (rc)
return rc;

put_unaligned_be32(hw_flow_id, p + ASK_HOSTCMD_HDR_LEN);
trace_ask_hostcmd_send(ASK_OP_FLOW_QUERY_STATS, 4,
       p + ASK_HOSTCMD_HDR_LEN);
return ASK_HOSTCMD_HDR_LEN + 4;
}
EXPORT_SYMBOL_GPL(ask_hostcmd_enc_flow_query_stats);

/* -------------------------------------------------------------------------
 * IPsec SA encoders (spec §12.2 IPsec block, payload = 80 bytes for v4)
 *
 * Layout (BE multibyte):
 *
 *   off  size  field
 *     0    4   spi
 *     4    4   dst_ip
 *     8    4   caam_rx_fqid
 *    12    4   op_inject_fqid
 *    16    4   reserved
 *    20   60   key material (truncated/zero-padded to 60 of the 64
 *              ABI bytes — the wire payload field carries 60 of them
 *              after the 20-byte header block, totalling 80).
 * ------------------------------------------------------------------------- */

int ask_hostcmd_enc_sa_insert_v4_esp(const struct ask_hw_sa_v4 *sa,
     void *buf, size_t buf_len)
{
const u16 payload = 80;
u8 *p = buf;
int rc;

if (!buf || !sa)
return -EINVAL;

rc = ask_hc_write_header(p, buf_len, ASK_OP_SA_INSERT_V4_ESP, payload);
if (rc)
return rc;

memset(p + ASK_HOSTCMD_HDR_LEN, 0, payload);
put_unaligned_be32(be32_to_cpu(sa->spi),    p + ASK_HOSTCMD_HDR_LEN +  0);
put_unaligned_be32(be32_to_cpu(sa->dst_ip), p + ASK_HOSTCMD_HDR_LEN +  4);
put_unaligned_be32(sa->caam_rx_fqid,        p + ASK_HOSTCMD_HDR_LEN +  8);
put_unaligned_be32(sa->op_inject_fqid,      p + ASK_HOSTCMD_HDR_LEN + 12);
/* +16..19 reserved (zero) */
memcpy(p + ASK_HOSTCMD_HDR_LEN + 20, sa->key_material, 60);

trace_ask_hostcmd_send(ASK_OP_SA_INSERT_V4_ESP, payload,
       p + ASK_HOSTCMD_HDR_LEN);
return ASK_HOSTCMD_HDR_LEN + payload;
}
EXPORT_SYMBOL_GPL(ask_hostcmd_enc_sa_insert_v4_esp);

int ask_hostcmd_enc_sa_remove(u32 hw_sa_id, void *buf, size_t buf_len)
{
u8 *p = buf;
int rc;

if (!buf)
return -EINVAL;

rc = ask_hc_write_header(p, buf_len, ASK_OP_SA_REMOVE, 4);
if (rc)
return rc;

put_unaligned_be32(hw_sa_id, p + ASK_HOSTCMD_HDR_LEN);
trace_ask_hostcmd_send(ASK_OP_SA_REMOVE, 4, p + ASK_HOSTCMD_HDR_LEN);
return ASK_HOSTCMD_HDR_LEN + 4;
}
EXPORT_SYMBOL_GPL(ask_hostcmd_enc_sa_remove);

/* -------------------------------------------------------------------------
 * Policer + offline-port encoders
 *
 * POLICER_SET_EXCEPTION_RATE payload (spec §12.2, 8 bytes after rounding):
 *
 *   off  size  field
 *     0    1   port_id
 *     1    3   reserved
 *     4    4   rate_bps     (BE)
 *     8    4   burst_bytes  (BE)  (total = 12 once aligned; the spec
 *                                  rounds the documented "8 bytes"
 *                                  to the actual 12 bytes the wire
 *                                  carries — the extra 4 bytes are
 *                                  the burst, named in the C ABI but
 *                                  counted with the rate in the spec
 *                                  payload-length column for brevity).
 *
 * To keep the spec literally honest (`Payload: 8 bytes (port_id u8 +
 * rate u32 BE + burst u32 BE)` = 1+4+4 = 9, the spec uses a 1-byte
 * port + back-to-back u32s with no padding for a tight 9-byte
 * payload). We emit exactly that 9-byte payload.
 * ------------------------------------------------------------------------- */

int ask_hostcmd_enc_policer_set(const struct ask_hw_policer *p_in,
void *buf, size_t buf_len)
{
const u16 payload = 9;
u8 *p = buf;
int rc;

if (!buf || !p_in)
return -EINVAL;

rc = ask_hc_write_header(p, buf_len,
 ASK_OP_POLICER_SET_EXC_RATE, payload);
if (rc)
return rc;

p[ASK_HOSTCMD_HDR_LEN + 0] = p_in->port_id;
put_unaligned_be32(p_in->rate_bps,    p + ASK_HOSTCMD_HDR_LEN + 1);
put_unaligned_be32(p_in->burst_bytes, p + ASK_HOSTCMD_HDR_LEN + 5);

trace_ask_hostcmd_send(ASK_OP_POLICER_SET_EXC_RATE, payload,
       p + ASK_HOSTCMD_HDR_LEN);
return ASK_HOSTCMD_HDR_LEN + payload;
}
EXPORT_SYMBOL_GPL(ask_hostcmd_enc_policer_set);

int ask_hostcmd_enc_op_flush(u8 port_id, void *buf, size_t buf_len)
{
u8 *p = buf;
int rc;

if (!buf)
return -EINVAL;

rc = ask_hc_write_header(p, buf_len, ASK_OP_OP_FLUSH, 1);
if (rc)
return rc;

p[ASK_HOSTCMD_HDR_LEN] = port_id;
trace_ask_hostcmd_send(ASK_OP_OP_FLUSH, 1, p + ASK_HOSTCMD_HDR_LEN);
return ASK_HOSTCMD_HDR_LEN + 1;
}
EXPORT_SYMBOL_GPL(ask_hostcmd_enc_op_flush);

/* -------------------------------------------------------------------------
 * Decoders
 * ------------------------------------------------------------------------- */

int ask_hostcmd_dec_ucode_version(const void *buf, size_t buf_len,
  u16 *family, u8 *major,
  u8 *minor, u16 *patch)
{
const u8 *p = buf;
u16 plen;
u8 op;
int rc;

if (!buf || !family || !major || !minor || !patch)
return -EINVAL;

rc = ask_hc_parse_header(p, buf_len, &op, &plen);
if (rc)
return rc;
if (op != ASK_OP_GET_UCODE_VERSION)
return -EPROTO;
if (plen != 6)
return -EINVAL;

*family = get_unaligned_be16(p + ASK_HOSTCMD_HDR_LEN + 0);
*major  = p[ASK_HOSTCMD_HDR_LEN + 2];
*minor  = p[ASK_HOSTCMD_HDR_LEN + 3];
*patch  = get_unaligned_be16(p + ASK_HOSTCMD_HDR_LEN + 4);

trace_ask_hostcmd_recv(op, plen, 0, p + ASK_HOSTCMD_HDR_LEN);
return 0;
}
EXPORT_SYMBOL_GPL(ask_hostcmd_dec_ucode_version);

int ask_hostcmd_dec_flow_insert(const void *buf, size_t buf_len,
u8 expected_op, u32 *hw_flow_id)
{
const u8 *p = buf;
u16 plen;
u8 op;
int rc;

if (!buf || !hw_flow_id)
return -EINVAL;

rc = ask_hc_parse_header(p, buf_len, &op, &plen);
if (rc)
return rc;
if (op != expected_op)
return -EPROTO;
if (plen != 4)
return -EINVAL;

*hw_flow_id = get_unaligned_be32(p + ASK_HOSTCMD_HDR_LEN);
trace_ask_hostcmd_recv(op, plen, 0, p + ASK_HOSTCMD_HDR_LEN);
return 0;
}
EXPORT_SYMBOL_GPL(ask_hostcmd_dec_flow_insert);

int ask_hostcmd_dec_flow_query_stats(const void *buf, size_t buf_len,
     u64 *bytes, u64 *packets)
{
const u8 *p = buf;
u16 plen;
u8 op;
int rc;

if (!buf || !bytes || !packets)
return -EINVAL;

rc = ask_hc_parse_header(p, buf_len, &op, &plen);
if (rc)
return rc;
if (op != ASK_OP_FLOW_QUERY_STATS)
return -EPROTO;
if (plen != 16)
return -EINVAL;

*bytes   = get_unaligned_be64(p + ASK_HOSTCMD_HDR_LEN + 0);
*packets = get_unaligned_be64(p + ASK_HOSTCMD_HDR_LEN + 8);
trace_ask_hostcmd_recv(op, plen, 0, p + ASK_HOSTCMD_HDR_LEN);
return 0;
}
EXPORT_SYMBOL_GPL(ask_hostcmd_dec_flow_query_stats);

int ask_hostcmd_dec_sa_insert(const void *buf, size_t buf_len,
      u8 expected_op, u32 *hw_sa_id)
{
const u8 *p = buf;
u16 plen;
u8 op;
int rc;

if (!buf || !hw_sa_id)
return -EINVAL;
if (expected_op != ASK_OP_SA_INSERT_V4_ESP &&
    expected_op != ASK_OP_SA_INSERT_V6_ESP)
return -EINVAL;

rc = ask_hc_parse_header(p, buf_len, &op, &plen);
if (rc)
return rc;
if (op != expected_op)
return -EPROTO;
if (plen != 4)
return -EINVAL;

*hw_sa_id = get_unaligned_be32(p + ASK_HOSTCMD_HDR_LEN);
trace_ask_hostcmd_recv(op, plen, 0, p + ASK_HOSTCMD_HDR_LEN);
return 0;
}
EXPORT_SYMBOL_GPL(ask_hostcmd_dec_sa_insert);

/* -------------------------------------------------------------------------
 * Lifecycle
 * ------------------------------------------------------------------------- */

int ask_hostcmd_init(void)
{
ask_pr_dbg("hostcmd: init (encoders ready, sender stub)\n");
return 0;
}

void ask_hostcmd_exit(void)
{
ask_pr_dbg("hostcmd: exit\n");
}