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
#include <linux/rhashtable.h>
#include <linux/rcupdate.h>
#include <linux/u64_stats_sync.h>
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

/*
 * Test-only direct entry points for the kunit suite (PR9 / M1.5).
 *
 * Production callers always reach these via the genl_family small_ops
 * dispatch table — never call them directly. They are exposed here so
 * tests/ask_test_genl.c can drive the pure-helper logic (skb fill,
 * dump-walker callback, eopnotsupp ratelimited stubs) without standing
 * up a synthetic netlink socket and round-tripping through genl_rcv.
 *
 * struct ask_flow is forward-declared above; struct ask_genl_dump_ctx
 * is defined inside ask_genl.c (the kunit code grabs it through a
 * matching forward declaration so the layout stays single-sourced).
 */
struct sk_buff;
struct genl_info;
struct netlink_callback;
struct ask_flow;
struct ask_genl_dump_ctx;

int ask_genl_get_info_fill(struct sk_buff *skb);
int ask_genl_fill_one_flow(struct sk_buff *skb, struct ask_flow *f);
int ask_genl_dump_one_cb(struct ask_flow *f, void *arg);
int ask_genl_eopnotsupp_doit(struct sk_buff *skb, struct genl_info *info);
int ask_genl_eopnotsupp_dumpit(struct sk_buff *skb,
       struct netlink_callback *cb);

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
/* ------------------------------------------------------------------------- */

/*
 * Flow-table entry. The key is what makes a flow unique on the wire
 * (5-tuple for L4, 3-tuple for multicast, ifindex+dmac for bridge).
 * The action is what the hardware should do once the flow matches.
 *
 * `cookie` is the opaque ID the upper layer uses to refer to this
 * flow. For nf_flow_table the cookie is the `unsigned long` the
 * core hands us in `flow_offload->priv`. For genl-driven test
 * insertions (PR7 kunit harness) it's an arbitrary u64 the caller
 * chose.
 *
 * `hw_flow_id` is what the 210 microcode returns from the
 * INSERT_V4_TCP / INSERT_V6_* / INSERT_BRIDGE responses. PR7 fakes
 * it with an atomic counter (no hardware yet); PR14 replaces the
 * fake with the real hostcmd response value.
 *
 * `stats` is a per-flow byte/packet counter pair guarded by a
 * u64_stats_sync seqcount. The 1Hz dump-stats poller (PR15h) updates
 * it from the bulk OP_FLOW_DUMP_STATS response.
 *
 * Lifetime: allocated with kzalloc(GFP_KERNEL), freed via call_rcu()
 * once the rhashtable removal walk completes. NEVER kfree() directly
 * after a remove — readers in an RCU read-side critical section may
 * still hold a pointer.
 */

#define ASK_FLOW_L3_IPV4    0
#define ASK_FLOW_L3_IPV6    1

struct ask_flow_stats {
struct u64_stats_sync syncp;
u64 packets;
u64 bytes;
u64 last_seen_ns;
};

struct ask_flow_key {
u8  l3_proto;       /* ASK_FLOW_L3_IPV4 / ASK_FLOW_L3_IPV6 */
u8  l4_proto;       /* IPPROTO_TCP / IPPROTO_UDP / 0 for bridge */
__be16 sport;
__be16 dport;
u32 iif;
u16 vlan_id;
u8  src_ip[16];     /* v4 packs into first 4, last 12 zero */
u8  dst_ip[16];
} __packed;

struct ask_flow {
struct rhash_head node;
struct rcu_head rcu;
u64 cookie;
struct ask_flow_key key;
u32 hw_flow_id;
u32 oif;
u32 action_flags;
struct ask_flow_stats stats;
};

/*
 * Per-fman software flow table. The current scaffold has exactly ONE
 * global table because PR7 has no concept of a per-fman device — that
 * layering arrives with the dpaa platform-driver work in M2. The
 * struct is parameterised on a `tag` string so when M2 grows multi-
 * fman support we can clone the table per fman without touching the
 * core lookup/insert code.
 */
struct ask_flow_table {
struct rhashtable rht;
atomic_t fake_hw_id_seq; /* PR7 placeholder until real hostcmd */
atomic_t num_flows;
const char *tag;
};

int  ask_flow_init(void);
void ask_flow_exit(void);

/* The (sole) global flow table for PR7. Multi-fman support lands later. */
struct ask_flow_table *ask_flow_default_table(void);

int  ask_flow_table_create(struct ask_flow_table *t, const char *tag);
void ask_flow_table_destroy(struct ask_flow_table *t);

/* Lookup by cookie — RCU read-side, no allocation. */
struct ask_flow *ask_flow_lookup(struct ask_flow_table *t, u64 cookie);

/*
 * Insert. Builds an ask_flow from the supplied key/action, returns
 * 0 on success and stores the assigned hw_flow_id in *out_hw_id.
 * -EEXIST if the cookie is already installed. Takes ownership of
 * neither key nor action — both are copied.
 */
int ask_flow_insert(struct ask_flow_table *t,
    u64 cookie,
    const struct ask_flow_key *key,
    u32 oif, u32 action_flags,
    u32 *out_hw_id);

/* Remove by cookie. Returns 0 on success, -ENOENT if not present. */
int ask_flow_remove(struct ask_flow_table *t, u64 cookie);

/*
 * Snapshot the per-flow stats into the caller-supplied out parameters.
 * Uses u64_stats_fetch_begin/retry around the seqcount so 32-bit
 * readers don't see torn 64-bit values.
 */
int ask_flow_get_stats(struct ask_flow_table *t, u64 cookie,
       u64 *packets, u64 *bytes, u64 *last_seen_ns);

/* Update stats from hardware (used by the 1Hz poller in PR15h). */
void ask_flow_update_stats(struct ask_flow *f, u64 add_packets, u64 add_bytes);

/* Iterate all flows (used by ASK_CMD_DUMP_FLOWS). The walker holds
 * the rht bucket lock across the per-entry callback, so the callback
 * must be allocation-light and must not itself touch the table.
 */
typedef int (*ask_flow_walk_fn)(struct ask_flow *f, void *arg);
int ask_flow_walk(struct ask_flow_table *t, ask_flow_walk_fn fn, void *arg);

/* Flush every flow (used by ASK_CMD_FLUSH_FLOWS). */
void ask_flow_flush(struct ask_flow_table *t);

/* ------------------------------------------------------------------------- */
/* ask_flow_offload.c — flow_block_cb registration on dpaa netdevs            */
/*                                                                            */
/* PR8 lands the FLOW_CLS_* dispatcher (replace/destroy/stats) plus the       */
/* block-bind helper invoked from the in-tree dpaa patch (PR11/M2.2). Until   */
/* that patch lands, the kunit harness drives                                 */
/* ask_flow_offload_setup_tc_block_cb() directly with a synthetic netdev.     */
/*                                                                            */
/* Forward declarations keep the header light — only files that actually      */
/* call these include <net/flow_offload.h> + <net/pkt_cls.h>.                 */
/* ------------------------------------------------------------------------- */
struct net_device;
struct flow_block_offload;
enum tc_setup_type;

int  ask_flow_offload_init(void);
void ask_flow_offload_exit(void);

/*
 * Public block-bind helper. The in-tree dpaa patch (PR11) calls this from
 * dpaa_setup_tc() when type == TC_SETUP_BLOCK; the kunit synthetic-netdev
 * path calls it the same way. Returns 0 on BIND/UNBIND success,
 * -EOPNOTSUPP for non-ingress binders, -ENOMEM / -ENOENT on the usual
 * failure paths.
 */
int ask_flow_offload_setup_tc(struct net_device *dev,
      struct flow_block_offload *fbo);

/*
 * The single flow_block_cb dispatched on TC_SETUP_CLSFLOWER. Exported so
 * kunit can drive it without going through the block-bind dance, and so
 * future per-fman block bindings can re-use the same callback.
 */
int ask_flow_offload_setup_tc_block_cb(enum tc_setup_type type,
       void *type_data, void *cb_priv);

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