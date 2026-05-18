/* SPDX-License-Identifier: GPL-2.0 */
/*
 * ASK2 internal API.
 *
 * Forward declarations and module-private function signatures shared
 * between the .c files inside ask.ko. Anything exposed to userspace
 * lives in include/uapi/linux/ask/ask.h instead.
 *
 * See specs/ask2-rewrite-spec.md for the full architecture.
 */
#ifndef _ASK_INTERNAL_H
#define _ASK_INTERNAL_H

#include <linux/types.h>
#include <linux/printk.h>
#include <linux/skbuff.h>
#include <linux/rhashtable.h>
#include <linux/rcupdate.h>
#include <linux/u64_stats_sync.h>
#include <linux/if_ether.h>      /* ETH_ALEN — PR14j L2 header plumbing */
#include <linux/xarray.h>        /* PR14j cookie indirection table */
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
/* ask_hw.c — hardware-derived information shared with userspace               */
/*                                                                            */
/* PR13 (M2.4) introduced this. The microcode version reported by             */
/* ASK_CMD_GET_INFO is read from the QEF (QorIQ Embedded Firmware)            */
/* blob that U-Boot loads from the SPI "fman-ucode" partition into            */
/* FMan IRAM at boot, and which is also re-published in the device            */
/* tree at /soc/fman@<addr>/fman-firmware/fsl,firmware. See                   */
/* specs/ask2-rewrite-spec.md §12.8 for the rationale (the standard           */
/* NXP 210.x QEF microcode does not implement the spec §12.2 host             */
/* command opcode dispatcher; the version is therefore derived from           */
/* the loaded blob, not from a runtime opcode 0x01).                          */
/* ------------------------------------------------------------------------- */
struct ask_hw_ucode_version {
        u16 family;          /* "210" in "210.10.1" */
        u8  major;           /* "10"  in "210.10.1" */
        u8  minor;           /* "1"   in "210.10.1" */
        u16 patch;           /* always 0 for stock NXP QEF; reserved for
                              * a hypothetical custom ASK2 microcode */
        char description[64];/* full QEF description string,
                              * e.g. "Microcode version 210.10.1 for LS1043 r1.0" */
};

int  ask_hw_ucode_get_version(struct ask_hw_ucode_version *out);
int  ask_hw_init(void);
void ask_hw_exit(void);

/*
 * PR14g-body-1 (M2.5g) - FMan PCD bring-up cache.
 *
 * struct ask_hw_pcd holds the per-FMan PCD handles that ask.ko owns
 * for the lifetime of the module: the PCD subsystem handle resolved
 * from DT, a single CC tree (group-count = 1), the per-proto CC nodes
 * that the dispatch layer (PR14g-body-2) populates with exact-match
 * 5-tuple keys, and the upstream KG scheme that chains the
 * silicon's KeyGen hash output into the CC tree's group table.
 *
 * Body-1 brings up exactly one CC node (v4-TCP).  IPv4 UDP, IPv6 TCP,
 * IPv6 UDP land as additional struct fields + bring-up calls in later
 * sub-PRs (M3.x).  The struct is forward-declared opaquely in
 * ask_internal.h scope - the full definition lives in ask_hw.c so
 * other TUs cannot inadvertently grow new dependencies on the PCD
 * handle layout.
 *
 * NULL-safe: if DT resolution or PCD probe fails (no fsl,fman node,
 * fman driver not bound, MURAM exhaustion), ask_hw_pcd_bringup()
 * logs a single warn and returns 0.  ask_hw_pcd_get() then returns
 * NULL and the body-2 dispatcher falls back to software-only mode
 * (existing fake_hw_id_seq atomic in ask_flow.c).  This keeps ask.ko
 * loadable on non-DPAA hosts and on DPAA hosts where the PCD chain
 * has been disabled for diagnostics.
 *
 * Per Q1/Q2/Q3 architectural decisions (approved 2026-05-14):
 *   - hw_flow_id encodes (node_token : 16) | (key_idx : 16) so the
 *     dispatcher can route OP_FLOW_REMOVE / OP_FLOW_QUERY_STATS back
 *     to the right CC node without a side-table lookup.
 *   - node_token is a per-ask_hw_pcd small-integer ID assigned at
 *     bring-up.  Body-1 uses token 1 for v4-TCP; higher numbers are
 *     reserved for the remaining flow types.  Token 0 is a sentinel
 *     ("no HW backing").
 */
struct ask_hw_pcd;

/*
 * Bring up the FMan PCD chain.  Idempotent.  Called once from
 * ask_hw_init() at module load.  Returns 0 on success and on the
 * "no DPAA / no PCD / probe failed" path (in which case ask_hw_pcd_get()
 * returns NULL and the dispatcher uses the software-only fallback).
 */
int ask_hw_pcd_bringup(void);

/* Tear down the FMan PCD chain.  Called from ask_hw_exit(). */
void ask_hw_pcd_teardown(void);

/*
 * Accessor for the body-2 dispatcher.  Returns NULL if bring-up did not
 * complete (either by failure or because ask.ko is running on a host
 * without DPAA).  Callers MUST treat NULL as the "software-only mode"
 * signal - it is the expected return on non-DPAA platforms.
 *
 * No lock required on the return pointer: the struct is allocated once
 * at module init and freed once at module exit.  Fields inside it that
 * may mutate at runtime (per-proto handle pointers, etc.) are guarded
 * by the struct's own internal mutex - body-2 uses dedicated accessors
 * that take that lock, not raw field access.
 */
struct ask_hw_pcd *ask_hw_pcd_get(void);

/*
 * ask_hw_port_bind() - bind the v4-TCP KG scheme to an FMan ingress port.
 *
 * Called from the flow_offload FLOW_BLOCK_BIND path (ask_flow_offload.c)
 * once we have a netdev and have resolved its FMan port id via the
 * in-tree dpaa_get_fman_port_id() helper (kernel patch 0030).
 *
 * The underlying fman_pcd_kg_bind_port() is single-port-per-scheme on
 * LS1046A silicon: the second call with a different @port_id returns
 * -EBUSY because the KGSE_MV match-vector slot is already claimed. The
 * wrapper here tracks the bound port internally and returns:
 *
 *   0           first bind for this @port_id succeeded
 *   0           idempotent re-bind for an ALREADY-BOUND @port_id (safe
 *               no-op — supports the FLOW_CLS_REPLACE bind path that
 *               may run on the same port multiple times)
 *   -ENOSPC    bind-array full (more than ASK_HW_V4_TCP_MAX_BINDS
 *               distinct ports tried to bind); caller logs and falls
 *               back to SW for the extra ports
 *   -ENODEV     ask_hw_pcd_get() returned NULL (no HW backing - same
 *               software-only fallback signal as the rest of ask_hw)
 *   other -E    propagated from fman_pcd_kg_scheme_create() /
 *               fman_pcd_kg_attach_cc() / fman_pcd_kg_bind_port()
 *
 * PR14v (2026-05-18): replaced the legacy single-scheme bind with a
 * per-port scheme allocator.  LS1046A KGSE_MV is single-port-per-
 * scheme, but multiple schemes may each be attached to the same CC
 * tree.  PR14v allocates one KG scheme per ingress port, attaches
 * each to the shared cc_tree, and binds each to its own port — so
 * both eth3 (port 8) and eth4 (port 9) classify v4-TCP in silicon
 * to the same CC node.  Pre-PR14v the second port hit -EBUSY and
 * ran in SW.
 *
 * Caller should LOG (not fail) on -ENOSPC at the BIND callsite so
 * the operator can see "scheme pool exhausted, falling back to SW".
 */
int ask_hw_port_bind(u8 port_id);

/*
 * hw_flow_id helpers (PR14g-body-1).
 *
 * The 32-bit hw_flow_id stored in struct ask_flow encodes:
 *   bits 31..16   node_token  - which CC node owns this key
 *   bits 15..0    key_idx     - 0-based slot inside the CC node's key table
 *
 * The token half is opaque to ask_flow.c: only the body-2 dispatcher
 * and removal path interpret it.  Token 0 + idx 0 is reserved for the
 * "software-only fallback" case (no HW backing) - the fake_hw_id_seq
 * atomic in ask_flow.c uses values 1..U32_MAX which never collide with
 * a real packed (token, idx) because real token is always >= 1.
 *
 * Both helpers are pure functions (no global state).  Inline at the
 * call site once gcc sees them in a single TU; declared here so other
 * TUs (ask_genl.c dump path, ask_debugfs.c) can use the unpacker for
 * diagnostic display.
 */
#define ASK_HW_FLOW_ID_TOKEN_NONE       0u
#define ASK_HW_FLOW_ID_TOKEN_V4_TCP     1u
/* Reserved for future bring-up: V4_UDP=2, V6_TCP=3, V6_UDP=4 */

/*
 * Legacy helpers — PR14g body-1.  After PR14j the live insert/remove
 * path no longer encodes hw_flow_id as a packed (token, key_idx) tuple;
 * it returns an opaque xarray cookie (see ask_hw_cookie_alloc() below).
 * The pack/unpack helpers are kept exported so debugfs and genl
 * pretty-printers that still want to display the legacy form (or that
 * synthesise sentinel ids in kunit) keep building.  Do NOT call them
 * from the runtime fast path.
 */
u32  ask_priv_pack_hw_flow_id(u16 node_token, u16 key_idx);
void ask_priv_unpack_hw_flow_id(u32 hw_flow_id,
        u16 *node_token, u16 *key_idx);

/*
 * PR14j (M2.5j) - two-stage OH-port chain bookkeeping.
 *
 * The widened hw_flow_id is an opaque u32 cookie that indexes into a
 * per-ask_hw_pcd xarray of struct ask_hw_flow_cookie.  Each entry
 * tracks the four silicon objects PR14j allocates per v4-TCP flow:
 *
 *   1. ingress CC slot (cc_node + key_idx) - same as PR14g body-1
 *   2. shared MANIP_RMV_ETHERNET handle (pcd-wide, owned by ask_hw_pcd)
 *   3. per-flow MANIP_INSRT_GENERIC handle (new L2 header bytes)
 *   4. shared MANIP_FIELD_UPDATE_IPV4_FORWARD handle (pcd-wide)
 *
 * The shared (rmv, ipv4_forward) handles are NOT freed per-flow; only
 * the per-flow m_insrt is destroyed in ask_hw_flow_remove().  This
 * cuts MURAM HMTD allocations from 3*N_flows to 2 + N_flows.
 *
 * sink_ifindex / sink_fqid are snapshotted for stats and debugability;
 * they are not dereferenced during teardown.
 *
 * Cookie 0 is reserved as the "no HW backing" sentinel so ask_flow.c
 * can call ask_hw_flow_remove() unconditionally on every tear-down.
 * The xarray is initialised with XA_FLAGS_ALLOC1 so the allocator
 * skips 0.
 */
struct fman_pcd_cc_node;
struct fman_pcd_manip;

/*
 * PR14s (2026-05-18) — per-flow OH-port ownership.
 *
 * The shared-OH-chain model that PR14j shipped at v1.0 is the M2
 * gate blocker: every set_chain() reprogrammed the SINGLE shared OH
 * port's AD chain with the latest flow's per-flow m_insrt, so only
 * the most-recent flow had its destination MAC armed in silicon.
 * All earlier flows either (a) hit the chain with the wrong dst-MAC
 * and got dropped at the peer link, or (b) the OH BMI's stall during
 * set_chain caused frames to NAPI-loop back into the kernel SW path.
 * Net effect: CPU dominated by SW forwarding, M2 gate FAIL.
 *
 * PR14s pivots to a per-flow OH-port model.  LS1046A exposes six
 * Offline Host ports at cell-index 0x2..0x7 → oh_idx 0..5; ask.ko
 * claims all six at bring-up and allocates one per HW-offloaded
 * flow.  When the pool is empty the insert path returns -ENOSPC and
 * the flow stays on the SW fast path (graceful degradation).  Each
 * OH port's AD chain is now exclusively owned by one flow for its
 * lifetime, so set_chain is called ONCE per flow at insert and
 * disarmed at remove — no inter-flow chain rewrite races.
 *
 * `oh_idx` is the cookie field that records which OH port this
 * flow owns.  `oh_owned == true` means ask_hw_flow_remove() must
 * call fman_pcd_oh_port_set_chain(NULL, 0, 0) on that port AND
 * clear bit oh_idx in the pcd-wide ask_hw_pcd.oh_alloc_bitmap.
 * `oh_owned == false` is the legacy / failure path: no OH port
 * was actually programmed for this cookie, so teardown must not
 * touch the pool.
 */
struct ask_hw_flow_cookie {
        struct fman_pcd_cc_node  *cc_node;
        u16                       key_idx;
        struct fman_pcd_manip    *m_rmv;     /* shared — do NOT destroy */
        struct fman_pcd_manip    *m_insrt;   /* per-flow */
        struct fman_pcd_manip    *m_ipv4;    /* shared — do NOT destroy */
        int                       sink_ifindex;
        u32                       sink_fqid;
        u8                        oh_idx;    /* PR14s: which OH port */
        bool                      oh_owned;  /* PR14s: pool slot held */
};

/*
 * PR14j cookie-table helpers.  Implemented in ask_hw.c.  All three
 * are NULL-safe on a NULL ask_hw_pcd; alloc returns 0 (the sentinel,
 * which ask_flow.c treats as "no HW backing — use SW fake counter").
 *
 * ask_hw_cookie_lookup() returns a pointer that is valid until
 * ask_hw_cookie_free() runs for the same cookie.  Callers must not
 * mutate the returned struct.
 */
u32  ask_hw_cookie_alloc(struct ask_hw_pcd *h,
                         const struct ask_hw_flow_cookie *src);
struct ask_hw_flow_cookie *
     ask_hw_cookie_lookup(struct ask_hw_pcd *h, u32 cookie);
void ask_hw_cookie_free(struct ask_hw_pcd *h, u32 cookie);

/*
 * PR14g-body-2 - runtime flow insert / remove dispatcher.
 *
 * struct ask_flow_key is forward-declared further down in this header
 * (ask_flow.c section); body-2 only needs the pointer type, not the
 * full layout, so the order here works.
 *
 * Contract for ask_hw_flow_insert():
 *   return  0           -> packed (token, idx) hw_flow_id in *out_hw_id;
 *                          caller stores it in struct ask_flow.hw_flow_id
 *                          and uses ask_hw_flow_remove() at teardown
 *           -ENODEV     -> no HW backing for this protocol/netdev (no DPAA,
 *                          PCD bring-up failed, or @oif is not a dpaa port);
 *                          caller MUST fall back to the SW-only fake_hw_id
 *                          atomic so the flow still appears in the SW table
 *           -EOPNOTSUPP -> protocol path not implemented yet (body-2 ships
 *                          v4-TCP only; v4-UDP / v6-TCP / v6-UDP land later);
 *                          caller falls back identically to -ENODEV
 *           other -E    -> hard failure (MURAM exhaustion, key table full,
 *                          mask/size mismatch); caller MUST fail the insert
 *                          rather than silently fall back, so userspace
 *                          observes the error
 *
 * ask_hw_flow_remove() is NULL-safe when @hw_flow_id has token NONE
 * (an SW-only id from the fake counter) and returns 0 in that case so
 * callers can call it unconditionally on every flow tear-down without
 * inspecting the token first.
 *
 * ask_hw_flow_query_stats() returns -EOPNOTSUPP at body-2; per-key
 * MURAM counters land in M3 with the bulk OP_FLOW_DUMP_STATS poller.
 */
struct ask_flow_key;

int  ask_hw_flow_insert(const struct ask_flow_key *key,
        u32 oif, u32 action_flags,
        u32 *out_hw_id);
int  ask_hw_flow_remove(u32 hw_flow_id);
int  ask_hw_flow_query_stats(u32 hw_flow_id, u64 *packets, u64 *bytes);

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

/*
 * PR14j (M2.5j) - L2 header for the OH-port MANIP_INSRT_GENERIC
 * silicon template.  Populated by ask_flow_offload.c via
 * neigh_lookup() against @dst_ip on the egress netdev.  Read once
 * inside ask_hw_flow_insert_v4_tcp() to build the per-flow
 * fman_pcd_manip_params.insrt_generic.hdr[] byte array; not used
 * at lookup time, but kept inside the key (and therefore the
 * rhashtable hash inputs) so a subsequent neighbour change forces
 * the flow to be re-inserted rather than silently routing to a
 * stale MAC.  Zero-initialised by ask_parse_match_v4(); if either
 * MAC is all-zero when ask_hw_flow_insert() runs, the dispatcher
 * returns -EAGAIN so the upper layer keeps the flow in SW until
 * the neighbour resolves.
 */
u8  next_hop_mac[ETH_ALEN]; /* dst MAC the OH chain pushes */
u8  egress_mac[ETH_ALEN];   /* src MAC = peer port's own MAC */
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

/*
 * PR14j direction-aware FLOW_BLOCK_BIND.
 *
 * The LS1046A KGSE_MV silicon supports a single port per KG scheme;
 * binding the second port returns -EBUSY (see ask_hw_port_bind()
 * contract in ask_hw.c).  PR14g first-binder-wins picked egress eth4
 * over ingress eth3 and the classifier never saw RX traffic.
 *
 * Solution: walk the dpaa netdev's of_node parent chain to the FMan
 * port node and inspect its compatible string for "*-rx" vs "*-tx".
 * Only ingress ports are passed to ask_hw_port_bind(); egress ports
 * participate as OH-chain sinks (via dpaa_get_tx_fqid()) and are
 * deliberately not bound to a KG scheme.
 *
 * Returns ASK_DIR_INGRESS / ASK_DIR_EGRESS / ASK_DIR_UNKNOWN.  The
 * UNKNOWN return is fail-closed (caller treats it identically to
 * EGRESS - skip the bind) so a synthetic kunit netdev with no
 * of_node parent chain cannot inadvertently consume the single-port
 * scheme slot.
 */
enum ask_flow_direction {
        ASK_DIR_UNKNOWN = 0,
        ASK_DIR_INGRESS = 1,
        ASK_DIR_EGRESS  = 2,
};

int ask_flow_offload_classify_dir(const struct net_device *dev);

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