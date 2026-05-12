/* SPDX-License-Identifier: GPL-2.0 */
/*
 * ASK 2.0 internal API.
 *
 * Forward declarations and module-private function signatures shared
 * between the .c files inside ask.ko. Anything exposed to userspace
 * lives in include/uapi/linux/ask/ask.h instead.
 *
 * See specs/ask-2.0-rewrite-spec.md for the full architecture.
 */
#ifndef _ASK_INTERNAL_H
#define _ASK_INTERNAL_H

#include <linux/types.h>
#include <linux/printk.h>
#include <linux/skbuff.h>
#include <net/netlink.h>

#define ASK_DRV_NAME            "ask"
#define ASK_DRV_VERSION_STR     "2.0.0"
#define ASK_DRV_VERSION_MAJOR   2
#define ASK_DRV_VERSION_MINOR   0
#define ASK_DRV_VERSION_PATCH   0

#define ask_pr_info(fmt, ...)   pr_info(ASK_DRV_NAME ": " fmt, ##__VA_ARGS__)
#define ask_pr_warn(fmt, ...)   pr_warn(ASK_DRV_NAME ": " fmt, ##__VA_ARGS__)
#define ask_pr_err(fmt, ...)    pr_err(ASK_DRV_NAME ": " fmt, ##__VA_ARGS__)
#define ask_pr_dbg(fmt, ...)    pr_debug(ASK_DRV_NAME ": " fmt, ##__VA_ARGS__)

/* ------------------------------------------------------------------------- */
/* ask_genl.c — generic-netlink family lifecycle and dispatch                 */
/* ------------------------------------------------------------------------- */
int  ask_genl_register(void);
void ask_genl_unregister(void);

/* ------------------------------------------------------------------------- */
/* ask_genl_attr.c — nla_policy tables shared across nested attribute sets    */
/* ------------------------------------------------------------------------- */
extern const struct nla_policy ask_top_policy[];
extern const struct nla_policy ask_info_policy[];
extern const struct nla_policy ask_muram_policy[];
extern const struct nla_policy ask_flow_policy[];
extern const struct nla_policy ask_sa_policy[];
extern const struct nla_policy ask_event_policy[];
extern const struct nla_policy ask_policer_policy[];

/* ------------------------------------------------------------------------- */
/* ask_flow.c — software flow table (rhashtable + RCU)                        */
/* PR7 fills these in; for PR1 they are all stubs returning -EOPNOTSUPP.      */
/* ------------------------------------------------------------------------- */
int  ask_flow_init(void);
void ask_flow_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_flow_offload.c — flow_block_cb registration on dpaa netdevs            */
/* PR8 fills these in.                                                       */
/* ------------------------------------------------------------------------- */
int  ask_flow_offload_init(void);
void ask_flow_offload_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_xfrm.c — xfrmdev_ops packet-mode IPsec offload                         */
/* PR16a fills these in.                                                     */
/* ------------------------------------------------------------------------- */
int  ask_xfrm_init(void);
void ask_xfrm_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_caam.c — CAAM QI descriptor sharing                                    */
/* PR16b fills these in.                                                     */
/* ------------------------------------------------------------------------- */
int  ask_caam_init(void);
void ask_caam_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_bridge.c — switchdev notifier driving bridge fast-path                 */
/* PR15e fills these in.                                                     */
/* ------------------------------------------------------------------------- */
int  ask_bridge_init(void);
void ask_bridge_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_neigh.c — netevent notifier for L2 nexthop updates                     */
/* M2 onwards fills these in.                                                */
/* ------------------------------------------------------------------------- */
int  ask_neigh_init(void);
void ask_neigh_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_op.c — offline-port re-injection plumbing                              */
/* PR15f fills these in.                                                     */
/* ------------------------------------------------------------------------- */
int  ask_op_init(void);
void ask_op_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_hostcmd.c — wire-format encoders/decoders for FMan host commands       */
/* ------------------------------------------------------------------------- */
int  ask_hostcmd_init(void);
void ask_hostcmd_exit(void);

/*
 * Wire-format opcodes (spec §12.2). Kept in this private header rather
 * than the UAPI because they are an internal implementation detail —
 * userspace only ever sees the genl_family in <uapi/linux/ask/ask.h>.
 */
#define ASK_OP_GET_UCODE_VERSION    0x01
#define ASK_OP_GET_CAPABILITIES     0x02
#define ASK_OP_GET_MURAM_INFO       0x03
#define ASK_OP_RESET_TABLES         0x04

#define ASK_OP_FLOW_INSERT_V4_TCP   0x10
#define ASK_OP_FLOW_INSERT_V4_UDP   0x11
#define ASK_OP_FLOW_INSERT_V6_TCP   0x12
#define ASK_OP_FLOW_INSERT_V6_UDP   0x13
#define ASK_OP_FLOW_INSERT_V4_MCAST 0x14
#define ASK_OP_FLOW_INSERT_V6_MCAST 0x15
#define ASK_OP_FLOW_INSERT_BRIDGE   0x16
#define ASK_OP_FLOW_REMOVE          0x18
#define ASK_OP_FLOW_QUERY_STATS     0x19
#define ASK_OP_FLOW_DUMP_STATS      0x1A

#define ASK_OP_SA_INSERT_V4_ESP     0x20
#define ASK_OP_SA_INSERT_V6_ESP     0x21
#define ASK_OP_SA_REMOVE            0x28
#define ASK_OP_SA_QUERY_STATS       0x29

#define ASK_OP_OP_CONFIGURE         0x30
#define ASK_OP_OP_FLUSH             0x31

#define ASK_OP_POLICER_SET_EXC_RATE 0x40

/* Action flag bits (spec §12.4) */
#define ASK_ACT_TTL_DEC             (1U << 0)
#define ASK_ACT_NAT_SRC             (1U << 1)
#define ASK_ACT_NAT_DST             (1U << 2)
#define ASK_ACT_PAT                 (1U << 3)
#define ASK_ACT_VLAN_PUSH           (1U << 4)
#define ASK_ACT_VLAN_POP            (1U << 5)
#define ASK_ACT_TO_CAAM             (1U << 6)
#define ASK_ACT_TO_OP               (1U << 7)

/* Frame sizes (spec §12.1, §12.3, §12.4) */
#define ASK_HOSTCMD_HDR_LEN         4
#define ASK_HOSTCMD_MAX_PAYLOAD     1020
#define ASK_HOSTCMD_MAX_FRAME       (ASK_HOSTCMD_HDR_LEN + ASK_HOSTCMD_MAX_PAYLOAD)

#define ASK_FLOW_KEY_V4_LEN         24
#define ASK_FLOW_KEY_V6_LEN         48
#define ASK_FLOW_ACTION_V4_LEN      40
#define ASK_FLOW_ACTION_V6_LEN      48

/* Typed structs the rest of ask.ko hands to the encoders (spec §12.6). */
struct ask_hw_flow_key_v4 {
__be32 src_ip;
__be32 dst_ip;
__be16 sport;
__be16 dport;
u32    iif;
u16    vlan_id;
};

struct ask_hw_flow_key_v6 {
u8     src_ip[16];
u8     dst_ip[16];
__be16 sport;
__be16 dport;
u32    iif;
u16    vlan_id;
};

struct ask_hw_action_v4 {
u32    flags;
u32    oif;
u8     rewrite_src_mac[6];
u8     rewrite_dst_mac[6];
__be32 rewrite_src_ip;
__be32 rewrite_dst_ip;
__be16 rewrite_sport;
__be16 rewrite_dport;
u16    vlan_id;
};

struct ask_hw_action_v6 {
u32    flags;
u32    oif;
u8     rewrite_src_mac[6];
u8     rewrite_dst_mac[6];
u8     rewrite_src_ip[16];
u8     rewrite_dst_ip[16];
__be16 rewrite_sport;
__be16 rewrite_dport;
u16    vlan_id;
};

struct ask_hw_sa_v4 {
__be32 spi;
__be32 dst_ip;
u32    caam_rx_fqid;
u32    op_inject_fqid;
u8     key_material[64];
};

struct ask_hw_policer {
u8  port_id;
u32 rate_bps;
u32 burst_bytes;
};

/*
 * Encoders. Each encoder builds a host-command frame (header + payload)
 * into the caller-supplied buffer 'buf' of capacity 'buf_len' bytes,
 * returns the number of bytes written on success, or a negative errno
 * on overflow / bad input. None of these touch hardware; the hardware
 * I/O happens in fman_host_cmd() (added in PR2 placeholder, real
 * implementation in M2).
 *
 * The "out_buf" alternative variants allocate an sk_buff via
 * alloc_skb(GFP_KERNEL) for callers that want to push the frame
 * straight into the FMan I/O block.
 */
int ask_hostcmd_enc_get_ucode_version(void *buf, size_t buf_len);

int ask_hostcmd_enc_flow_insert_v4(u8 op,
   const struct ask_hw_flow_key_v4 *key,
   const struct ask_hw_action_v4 *act,
   void *buf, size_t buf_len);

int ask_hostcmd_enc_flow_insert_v6(u8 op,
   const struct ask_hw_flow_key_v6 *key,
   const struct ask_hw_action_v6 *act,
   void *buf, size_t buf_len);

int ask_hostcmd_enc_flow_remove(u32 hw_flow_id,
void *buf, size_t buf_len);

int ask_hostcmd_enc_flow_query_stats(u32 hw_flow_id,
     void *buf, size_t buf_len);

int ask_hostcmd_enc_sa_insert_v4_esp(const struct ask_hw_sa_v4 *sa,
     void *buf, size_t buf_len);

int ask_hostcmd_enc_sa_remove(u32 hw_sa_id, void *buf, size_t buf_len);

int ask_hostcmd_enc_policer_set(const struct ask_hw_policer *p,
void *buf, size_t buf_len);

int ask_hostcmd_enc_op_flush(u8 port_id, void *buf, size_t buf_len);

int ask_hostcmd_enc_reset_tables(void *buf, size_t buf_len);

/*
 * Decoders. Parse a response frame (already stripped of any FMan
 * transport framing — these see the raw 4-byte header + payload).
 * Validate opcode echo + length, then unpack into the typed out
 * parameter. Return 0 on success, -EINVAL on malformed input,
 * -EPROTO on opcode mismatch.
 */
int ask_hostcmd_dec_ucode_version(const void *buf, size_t buf_len,
  u16 *family, u8 *major,
  u8 *minor, u16 *patch);

int ask_hostcmd_dec_flow_insert(const void *buf, size_t buf_len,
u8 expected_op, u32 *hw_flow_id);

int ask_hostcmd_dec_flow_query_stats(const void *buf, size_t buf_len,
     u64 *bytes, u64 *packets);

int ask_hostcmd_dec_sa_insert(const void *buf, size_t buf_len,
      u8 expected_op, u32 *hw_sa_id);

/* ------------------------------------------------------------------------- */
/* ask_stats.c — u64_stats_sync wrappers                                      */
/* PR7 fills these in.                                                       */
/* ------------------------------------------------------------------------- */
int  ask_stats_init(void);
void ask_stats_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_debugfs.c - /sys/kernel/debug/ask (gated on CONFIG_DEBUG_FS)           */
/* ------------------------------------------------------------------------- */
int  ask_debugfs_init(void);
void ask_debugfs_exit(void);

#endif /* _ASK_INTERNAL_H */