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
#include <linux/errno.h>        /* -E2BIG in ask_intent_add() inline */
#include <linux/xarray.h>       /* ask_flow_table::gen_by_cookie (A3) */
#include <linux/printk.h>
#include <linux/skbuff.h>
#include <linux/rhashtable.h>
#include <linux/rcupdate.h>
#include <linux/u64_stats_sync.h>
#include <linux/if_ether.h>      /* ETH_ALEN — PR14j L2 header plumbing */
#include <linux/xarray.h>        /* PR14j cookie indirection table */
#include <net/netlink.h>

#define ASK_DRV_NAME            "ask"
#define ASK_DRV_VERSION_STR     "2.1.0"
#define ASK_DRV_VERSION_MAJOR   2
#define ASK_DRV_VERSION_MINOR   1
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
 * M1 coarse dataplane mode-switch (control-plane plumbing).
 *
 * ask_hw_offload_engage()/_disengage() are the ask.ko-side wrappers the
 * debugfs trigger (and, in M7, the `set system offload ask` op-mode) calls
 * to flip one FMan RX port between the mainline RSS path (S0) and the AC_CC
 * classifier path (S1).  They forward to the board-exported coarse API
 * fman_pcd_offload_engage()/_disengage() (board patch 0129), guarded by a
 * per-port idempotent "engaged" flag and the PCD instance mutex.  NOT called
 * at module load - ship dormant.  @hw_port_id is the FMan-side hardware RX
 * port id (eth3 = 0x10).
 *
 * Return (engage): 0 on success or if already engaged; -ENODEV if no HW
 * backing (non-DPAA host / PCD bring-up failed); negative errno from the
 * board engage on failure.  disengage is void and idempotent.
 */
int  ask_hw_offload_engage(u8 hw_port_id);
void ask_hw_offload_disengage(u8 hw_port_id);
void ask_hw_offload_set_family(u8 hw_port_id, u8 family_mask);
unsigned long ask_hw_get_enq_fe_off(void);

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

/* Fix B: cached FMan handle (fman_bind() at bringup) for the flow_offload
 * FE-VM add/del path; NULL before bringup / after teardown. */
struct fman;
struct fman *ask_hw_get_fman(void);

/*
 * Phase 4.10 helpers (v1.3 Path A): per-port CC node accessors. The
 * pre-netdev hook (ask_pcd_install_hook) creates one cc_v4_tcp and
 * one cc_v4_udp empty node per claimed FMan ingress port at boot.
 * ask_flow_offload.c looks them up by hwport_id when adding/removing
 * per-flow CC keys. Returns NULL if no pipeline is installed for
 * @hwport_id (i.e. the hook didn't claim that port).
 */
struct fman_pcd_cc_node *ask_hw_pcd_cc_v4_tcp_for_port(u8 hwport_id);
struct fman_pcd_cc_node *ask_hw_pcd_cc_v4_udp_for_port(u8 hwport_id);

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
 *   -EBUSY      the named pipeline already owns a DIFFERENT port id
 *               (caller decided ASK_HW_DIR_FWD for port X then later
 *               asked to bind port Y as FWD too — semantically wrong;
 *               caller should pick a different direction)
 *   -ENODEV     ask_hw_pcd_get() returned NULL (no HW backing - same
 *               software-only fallback signal as the rest of ask_hw)
 *   other -E    propagated from fman_pcd_kg_scheme_create() /
 *               fman_pcd_kg_attach_cc() / fman_pcd_kg_bind_port()
 *
 * PR14v (2026-05-18): replaced the legacy single-scheme bind with a
 * per-port scheme allocator.  LS1046A KGSE_MV is single-port-per-
 * scheme, but multiple schemes may each be attached to the same CC
 * tree.  PR14v allocates one KG scheme per ingress port, attaches
 * each to the shared cc_tree, and binds each to its own port.
 *
 * PR14z5 (2026-05-19): the empirical PR14z4 measurement showed that
 * FMan v3 cannot usefully share a single cc_tree across two KG
 * schemes (binding the second port HALVED forward-direction
 * throughput, 6.83 → 5.24 Gbps).  The architectural fix is to split
 * the classifier into TWO independent pipelines, one per direction.
 * Each pipeline { cc_tree, cc_v4_tcp, scheme } owns exactly one
 * ingress port — FWD = first-arrival (forward direction), REV =
 * second-arrival (reverse direction).  Callers tell us which
 * direction this bind is for via @dir; the pipeline arrays are
 * sized ASK_HW_DIR_NR.
 *
 * Caller should LOG (not fail) on -EBUSY at the BIND callsite so
 * the operator can see "direction mis-assignment, falling back to SW".
 */
enum ask_hw_dir {
        ASK_HW_DIR_FWD = 0,
        ASK_HW_DIR_REV = 1,
        ASK_HW_DIR_NR  = 2,
};

/*
 * PR14z7 (2026-05-19) — extended signature with @ingress_dev so the
 * implementation can look up the underlying RX `struct fman_port *`
 * via dpaa_get_rx_fman_port() and arm the per-port BMI Rx Frame-Parser
 * Next-Engine register (FMBM_RFPNE, RM 8.7.4) via
 * fman_port_use_kg_hash(port, true). Without that final step the
 * KG scheme is bound to the silicon but post-Parser frames bypass
 * KeyGen entirely and route to the dpaa default RX FQ — defeating
 * the entire HW classifier pipeline (measured at 6.85 Gbps / 70%
 * CPU on M2 baseline; see plans/PR14z7-DESIGN.md §1 for the trace).
 *
 * @ingress_dev may be NULL for unit-test contexts that drive the
 * port_bind path without a live netdev; in that case FMBM_RFPNE is
 * left at its boot-default value and the pipeline is bound-but-inert.
 */
int ask_hw_port_bind(u8 port_id, enum ask_hw_dir dir,
                     struct net_device *ingress_dev);

/*
 * PR14z17 (2026-05-22): symmetric un-bind for FLOW_BLOCK_UNBIND.
 *
 * Walks both pipelines; for any pipeline whose bound_pid matches
 * @port_id, ungrafts the kernel-owned KG scheme (patch 0043 RMW
 * restores kgse_mode NIA back to ENQUEUE_KG_DFLT_NIA and clears
 * KGSE_CCBS atomically), destroys the lazily-created cc_v4_tcp
 * node, and resets the pipeline state to unbound.  The
 * per-direction cc_tree is left intact for subsequent bind cycles.
 *
 * Idempotent: returns 0 if @port_id is not currently bound to any
 * pipeline (the common case under nft `delete table` after the
 * matching FLOW_CLS_DESTROYs have already removed all flows).
 */
int ask_hw_port_unbind(u8 port_id);

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

/* Still referenced by the bring-up accessor prototypes below (reworked
 * onto the board substrate in a later stage). */
struct fman_pcd_cc_node;
struct fman_pcd_manip;

/*
 * ASK2 board-substrate per-flow cookie (2026-06-15, updated 2026-07-27).
 *
 * Supersedes the PR14j/PR14x ASK-flavor MANIP-chain bookkeeping.  The
 * old model owned per-flow fman_pcd_manip handles (RMV_ETHERNET +
 * INSRT_GENERIC + FIELD_UPDATE_IPV4_FORWARD) via the deprecated
 * <linux/fsl/fman_pcd.h> API and cost ~3 HMTD allocations PER FLOW —
 * the source of the 327x `fman_pcd_manip_chain_create(...) failed -12`
 * MURAM-exhaustion blocker.
 *
 * CR-007 (2026-07-27): Fork-A shadow/HM bookkeeping removed. The
 * cc_handle and hm_handle fields, the per-port shadow[] array, nkeys,
 * next_key_id, and cc_installed are all gone. The FE-VM ehash path
 * (Fork-B) manages its own flow keys via ask_fe_flow_insert/remove()
 * in ask_flow_offload.c. The cookie now snapshots only the port identity
 * and sink metadata for teardown.
 *
 * fm / port_id are the ingress port's FMan handle and BMI hwport id,
 * snapshotted so teardown can resolve the port without re-looking up
 * the netdev.  sink_ifindex / sink_fqid remain informational snapshots
 * for stats and debugability; they are not dereferenced during teardown.
 *
 * Cookie 0 is reserved as the "no HW backing" sentinel so ask_flow.c
 * can call ask_hw_flow_remove() unconditionally on every tear-down.
 * The xarray is initialised with XA_FLAGS_ALLOC1 so the allocator
 * skips 0.
 */
struct fman;

struct ask_hw_flow_cookie {
        struct fman              *fm;           /* ingress port's FMan */
        u8                        port_id;      /* ingress BMI hwport id */
        int                       sink_ifindex;
        u32                       sink_fqid;
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
 *   return  0           -> non-zero cookie hw_flow_id in *out_hw_id;
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
 * ask_hw_flow_remove() is NULL-safe when @hw_flow_id is 0
 * (SW-only fallback / no HW backing) and returns 0 in that case so
 * callers can call it unconditionally on every flow tear-down without
 * inspecting the id first.
 *
 * ask_hw_flow_query_stats() returns -EOPNOTSUPP at body-2; per-key
 * MURAM counters land in M3 with the bulk OP_FLOW_DUMP_STATS poller.
 */
struct ask_flow_key;

/*
 * T-M6-A4 preflight: validate every resource class the current flow requires
 * WITHOUT allocating a cookie, writing MURAM/DDR, or changing an FQ. Returns
 * 0 if the subsequent ask_hw_flow_insert can be attempted; -EOPNOTSUPP /
 * -ENODEV / -EAGAIN / -ENOSPC otherwise so the caller can fail to software
 * before any partial programming. The check is advisory against races; the
 * real insert still validates and rolls back every acquisition.
 */
int  ask_hw_flow_preflight(const struct ask_flow_key *key,
                           u32 oif, u32 action_flags,
                           enum ask_hw_dir dir);
/* NAT offload gates: IPv4 is shipping/default-on; NAT66 is experiment/default-off. */
bool ask_hw_nat44_offload_armed(void);
bool ask_hw_nat66_offload_armed(void);
/* T-M6-8 VLAN offload gate: default-OFF until S0-S4 silicon gates pass. */
bool ask_hw_vlan_offload_armed(void);
int  ask_vlan_cc_flow_add(const struct ask_flow_key *key, u32 tx_fqid,
			  struct net_device *egress_dev);
void ask_vlan_cc_flow_del(const struct ask_flow_key *key);
void ask_vlan_cc_teardown_port(u8 port_id);
int  ask_hw_flow_insert(const struct ask_flow_key *key,
        u32 oif, u32 action_flags,
        enum ask_hw_dir dir,
        u32 *out_hw_id);
int  ask_hw_flow_remove(u32 hw_flow_id);
int  ask_hw_flow_get_sink_fqid(u32 hw_flow_id, u32 *fqid);
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
/*
 * F-163 (2026-08-05): ingress FMan hwport id, set by
 * ask_flow_offload_replace() from ask_dpaa_get_fman_port_id(ingress_dev).
 * Not part of the 5-tuple, but kept inside the key (like next_hop_mac/
 * egress_mac above) so it survives into the stored ask_flow and is
 * available, unchanged, at DESTROY time for the symmetric FE-VM ehash
 * delete. Prepended as byte 0 of the FE-VM ehash key by ask_fe_build_key()/
 * _v6() -- see ASK_FE_KEY_SIZE comment.
 */
u8  port_id;
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

/*
 * T-M6-7.0: translated NAT/PAT tuple carried with the stored flow so
 * DESTROY/pending/neighbour rebuilds preserve the exact action intent.
 * These fields are NOT part of the FE comparison key (which remains the
 * original 46-byte ingress tuple); T-M6-7.1 consumes them only as rewrite
 * parameters in the FE opcode chain. ask_flow_rht_params hashes by cookie,
 * so widening this struct does not perturb flow lookup.
 */
u8     nat_flags;
#define ASK_NATF_SNAT	BIT(0)
#define ASK_NATF_DNAT	BIT(1)
#define ASK_NATF_SPAT	BIT(2)
#define ASK_NATF_DPAT	BIT(3)
	u8     nat_src_ip[16];
	u8     nat_dst_ip[16];
	__be16 nat_sport;
	__be16 nat_dport;

	/*
	 * T-M6-8: VLAN pop/push edit carried with the stored flow so
	 * DESTROY/pending/neighbour rebuilds preserve the exact action intent.
	 * NOT part of the FE comparison key (VLAN TCI is excluded from the key
	 * per fman-keygen-flow-key-spec §4.4); consumed only as FE opcode params
	 * by the F-233 emitter. Single 802.1Q tag: vlan_push_tci is the
	 * 16-bit TCI (PCP<<13 | DEI<<12 | VID) to insert; vlan_push_tpid is the
	 * outer EtherType (0x8100). vlan_edit_flags gates each op.
	 */
	u8     vlan_edit_flags;
#define ASK_VLANF_POP	BIT(0)	/* strip all ingress VLAN tags */
#define ASK_VLANF_PUSH	BIT(1)	/* insert one egress 802.1Q tag */
	__be16 vlan_push_tci;
	__be16 vlan_push_tpid;
	/*
	 * T-M6-8 (2026-08-25): the ingress VID to hand the STRIP_ALL_VLAN
	 * opcode. The vendor insert_remove_vlan_hm() writes the real ingress
	 * VID into en_ehash_strip_all_vlan_hdrs.vlan_id[0] (outer-first, be16)
	 * and leaves op_flags=0 (VALIDATE) for a routed tagged POP; only bridge
	 * flows with no tag set OP_SKIP_VLAN_VALIDATE. ASK2 previously emitted a
	 * zero VID + SKIP, which left the 0x12 strip's parse geometry
	 * inconsistent and silently dropped bulk POP frames on silicon. Sourced
	 * from the ingress VLAN vif (vlan_dev_vlan_id) since the flowtable POP
	 * action carries no VID. Host order 1..4094; 0 = no ingress tag.
	 */
	u16    vlan_ingress_vid;
} __packed;

/* ------------------------------------------------------------------------- */
/* Canonical flow intent (T-M6-A1, 2026-08-18).                               */
/*                                                                            */
/* The single typed description of what a REPLACE asks the hardware to do.    */
/* It is the source of truth that ask_parse_action() produces and that        */
/* ask_intent_lower() lowers to the legacy (oif, action_flags) representation */
/* the insert/pending/neigh paths still consume. Introducing it now (before   */
/* NAT/VLAN/IPsec) gives every later M6 feature ONE place to add a typed      */
/* action and ONE compiler (ask_intent_lower / the future FE action compiler) */
/* instead of ad-hoc action_flags bits.                                       */
/*                                                                            */
/* A1 CONTRACT: for the plain IPv4-unicast flow (REDIRECT, plus the kernel's  */
/* mandatory ETH-type MANGLE L2 rewrite that INSERT_L2_HDR already performs)  */
/* the lowering MUST reproduce the exact pre-A1 values — oif = egress ifindex */
/* and action_flags = 0 — so the stored ask_flow and the FE record are        */
/* byte-for-byte identical. The ehash key bytes come solely from the match    */
/* (ask_fe_build_key), which A1 does not touch.                               */
/* ------------------------------------------------------------------------- */
enum ask_action_type {
	ASK_ACTION_REDIRECT    = 0, /* forward to egress netdev (oif) */
	ASK_ACTION_L2_REWRITE  = 1, /* next-hop L2 rewrite (ETH MANGLE) */
	ASK_ACTION_TTL_DEC     = 2, /* decrement IPv4 TTL / IPv6 hop-limit */
	/*
	 * T-M6-7.0 (NAT/PAT parse + carry). The kernel expresses NAT as
	 * FLOW_ACTION_MANGLE of L3/L4 header bytes; ask_parse_action() decodes
	 * those into these typed actions and reads the translated values from
	 * the conntrack reply tuple. ask_intent_lower() carries them into the
	 * flow key but STILL returns -EOPNOTSUPP (the FE-VM NAT opcode compiler
	 * is T-M6-7.1), so NAT flows keep falling back to the software fastpath
	 * until the hardware rewrite is proven. No datapath change vs. today.
	 */
	ASK_ACTION_NAT_SRC     = 3, /* rewrite IPv4/IPv6 source address */
	ASK_ACTION_NAT_DST     = 4, /* rewrite IPv4/IPv6 destination address */
	ASK_ACTION_NAPT_SPORT  = 5, /* rewrite L4 source port */
	ASK_ACTION_NAPT_DPORT  = 6, /* rewrite L4 destination port */
	/*
	 * T-M6-8 (VLAN pop/push). The kernel flowtable expresses VLAN edits as
	 * FLOW_ACTION_VLAN_POP (no fields) and FLOW_ACTION_VLAN_PUSH
	 * ({vid, proto, prio}); translation arrives as POP+PUSH. ask_parse_action
	 * decodes those into these typed actions. ask_intent_lower carries them
	 * into the flow key but returns -EOPNOTSUPP unless the VLAN gate is armed
	 * (the FE-VM emitter is F-233 and default-off until S0-S4 pass), so
	 * VLAN flows fail closed to software. Single 802.1Q tag only; 802.1ad and
	 * stacked tags are rejected at parse.
	 */
	ASK_ACTION_VLAN_POP    = 7, /* strip ingress 802.1Q tag(s) */
	ASK_ACTION_VLAN_PUSH   = 8, /* insert one egress 802.1Q tag */
};

/*
 * T-M6-8: raised 8 -> 16 so a composed VLAN+NAT flow fits. The kernel routed
 * emission for such a flow is eth_src(MANGLE x2) + eth_dst(MANGLE x2) [each ->
 * one ASK_ACTION_L2_REWRITE] + VLAN_POP + VLAN_PUSH + SNAT + DNAT + SPORT +
 * DPORT + CSUM + REDIRECT + implicit TTL_DEC ~= 13 typed actions. 16 matches
 * the FE record's MAX_OPCODES ceiling. Overflow still fails closed (-E2BIG).
 */
#define ASK_INTENT_MAX_ACTIONS 16

struct ask_flow_action_ent {
	enum ask_action_type type;
	u32 oif;   /* valid for ASK_ACTION_REDIRECT */
	/*
	 * Translated value for the NAT actions (network byte order):
	 *  - ASK_ACTION_NAT_SRC/DST      -> addr[] (4 bytes v4 / 16 bytes v6)
	 *  - ASK_ACTION_NAPT_SPORT/DPORT -> port
	 * Zero for REDIRECT/L2_REWRITE/TTL_DEC.
	 */
	union {
		u8     addr[16];
		__be16 port;
		/*
		 * T-M6-8: VLAN push tag. vid is host order (0-4095), tpid is the
		 * __be16 EtherType (must be ETH_P_8021Q), prio is host order PCP
		 * (0 from the flowtable path). POP carries no value.
		 */
		struct {
			u16    vid;
			__be16 tpid;
			u8     prio;
		} vlan;
	} nat;
};

struct ask_flow_intent {
	const struct ask_flow_key  *match;   /* borrowed; not owned */
	struct ask_flow_action_ent  actions[ASK_INTENT_MAX_ACTIONS];
	u8  n_actions;
	u64 owner;        /* kernel flow cookie that owns this intent */
	u32 generation;   /* A3 hook: bumped per REPLACE; 0 until A3 lands */
};

static inline int ask_intent_add(struct ask_flow_intent *in,
				 enum ask_action_type type, u32 oif)
{
	if (in->n_actions >= ASK_INTENT_MAX_ACTIONS)
		return -E2BIG;
	in->actions[in->n_actions].type = type;
	in->actions[in->n_actions].oif  = oif;
	memset(&in->actions[in->n_actions].nat, 0,
	       sizeof(in->actions[in->n_actions].nat));
	in->n_actions++;
	return 0;
}

/*
 * T-M6-7.0: add a NAT/PAT action carrying its translated value.
 * @addr (network byte order) is used for ASK_ACTION_NAT_SRC/DST (@addr_len is
 * 4 for IPv4, 16 for IPv6); @port for ASK_ACTION_NAPT_SPORT/DPORT. The other
 * field is left zero.
 */
static inline int ask_intent_add_nat(struct ask_flow_intent *in,
				     enum ask_action_type type,
				     const u8 *addr, u8 addr_len, __be16 port)
{
	struct ask_flow_action_ent *e;

	if (in->n_actions >= ASK_INTENT_MAX_ACTIONS)
		return -E2BIG;
	e = &in->actions[in->n_actions];
	e->type = type;
	e->oif = 0;
	memset(&e->nat, 0, sizeof(e->nat));
	if (type == ASK_ACTION_NAT_SRC || type == ASK_ACTION_NAT_DST) {
		if (addr_len != 4 && addr_len != 16)
			return -EINVAL;
		memcpy(e->nat.addr, addr, addr_len);
	} else if (type == ASK_ACTION_NAPT_SPORT ||
		   type == ASK_ACTION_NAPT_DPORT) {
		e->nat.port = port;
	} else {
		return -EINVAL;
	}
	in->n_actions++;
	return 0;
}

/*
 * T-M6-8: add a VLAN pop or push action. For ASK_ACTION_VLAN_PUSH, @vid is the
 * host-order VLAN id, @tpid the __be16 EtherType (ETH_P_8021Q), @prio the PCP.
 * For ASK_ACTION_VLAN_POP all value args are ignored.
 */
static inline int ask_intent_add_vlan(struct ask_flow_intent *in,
				      enum ask_action_type type,
				      u16 vid, __be16 tpid, u8 prio)
{
	struct ask_flow_action_ent *e;

	if (in->n_actions >= ASK_INTENT_MAX_ACTIONS)
		return -E2BIG;
	if (type != ASK_ACTION_VLAN_POP && type != ASK_ACTION_VLAN_PUSH)
		return -EINVAL;
	e = &in->actions[in->n_actions];
	e->type = type;
	e->oif = 0;
	memset(&e->nat, 0, sizeof(e->nat));
	if (type == ASK_ACTION_VLAN_PUSH) {
		e->nat.vlan.vid  = vid;
		e->nat.vlan.tpid = tpid;
		e->nat.vlan.prio = prio;
	}
	in->n_actions++;
	return 0;
}

/*
 * Lower a canonical intent to the legacy (oif, action_flags) pair the rest of
 * the insert path consumes. Kept as the single translation point so the FE
 * action compiler can later replace the body without touching call sites.
 * Returns 0 on success; -EOPNOTSUPP if the intent has no egress.
 */
int ask_intent_lower(const struct ask_flow_intent *in,
		     u32 *out_oif, u32 *out_action_flags);

struct ask_flow {
struct rhash_head node;
struct rcu_head rcu;
u64 cookie;
struct ask_flow_key key;
u32 hw_flow_id;
u32 oif;
u32 action_flags;
/*
 * PR14z5: which silicon pipeline (FWD or REV) owns this flow's
 * CC slot.  Snapshotted at insert time so the remove path (which
 * only has the cookie / hw_flow_id) can route teardown to the
 * correct cc_tree without re-deriving direction from oif.
 */
u8  dir;
/*
 * True iff ask_hw_flow_insert() actually programmed silicon for this
 * flow, i.e. @hw_flow_id is a real xarray cookie from
 * ask_hw_cookie_alloc() rather than a software-only counter value.
 *
 * This exists because @hw_flow_id ALONE CANNOT ANSWER THAT QUESTION.
 * Two producers share its numeric space and both start at 1 and
 * increment densely: xa_alloc(..., XA_LIMIT(1, U32_MAX), ...) for real
 * HW cookies, and atomic_inc_return(&t->fake_hw_id_seq) for SW-only
 * fallbacks. The first flow of each class therefore collides, and
 * ask_hw_flow_remove() resolves whatever it is given as an xarray
 * cookie — so a SW-fallback teardown used to free an unrelated real
 * flow's CC slot and HM next-hop reference while that flow stayed in
 * this table believing it was still offloaded.
 *
 * Set once at insert, never mutated, so it is safe to read under
 * rcu_read_lock() alongside @hw_flow_id.
 */
bool hw_backed;
/*
 * T-M6-A3: the ownership generation this flow was published under. Set once
 * at insert, immutable, read under rcu_read_lock alongside hw_flow_id. A
 * DESTROY/worker carrying an older generation must not act on this flow.
 */
u32 generation;
struct ask_flow_stats stats;
/*
 * T-M8-3: baseline of the silicon FE ehash record's cumulative
 * packet/byte counters as of the previous FLOW_CLS_STATS poll. The
 * hardware totals are absolute; the nft flowtable's flow_stats_update()
 * ACCUMULATES, so ask_flow_offload_stats() must report the delta since
 * this baseline (mirrors sfc/cxgb4/enetc old_* pattern) and then advance
 * it. Guarded by stats.syncp. A per-key record is destroyed+recreated on
 * DESTROY->REPLACE, so the absolute can reset to < baseline; that is
 * treated as a counter reset (delta = current, baseline = current).
 * Distinct from stats.packets/bytes, which hold the ABSOLUTE total that
 * dump-flows/get-flow report.
 */
u64 hw_reported_packets;
u64 hw_reported_bytes;
};

/*
 * Per-fman software flow table. The current scaffold has exactly ONE
 * global table because PR7 has no concept of a per-fman device — that
 * layering arrives with the dpaa platform-driver work in M2. The
 * struct is parameterised on a `tag` string so when M2 grows multi-
 * fman support we can clone the table per fman without touching the
 * core lookup/insert code.
 */
/*
 * Per-cookie ownership generation registry (T-M6-A3, 2026-08-18).
 *
 * The flow lifecycle keys everything on `cookie`, an unsigned-long slab
 * pointer to the kernel's flow_offload_tuple that is RECYCLED across a
 * DESTROY->REPLACE boundary. Without an ownership stamp, a late DESTROY, a
 * stale neighbour-rebuild worker, or a duplicate pending replay can act on a
 * cookie that now belongs to a NEWER flow — deleting or resurrecting the wrong
 * flow (CR-004), or leaving an orphan silicon record if a DESTROY races the
 * post-publish FE install (a MURAM-leak class per AGENTS.md S6).
 *
 * A monotonic per-cookie generation plus a tombstone closes all of these:
 *  - REPLACE bumps the generation and clears any tombstone (new owner).
 *  - The generation is checked immediately before the rhashtable publish; a
 *    superseded REPLACE rolls back its own HW and does not publish.
 *  - DESTROY tombstones the cookie; it is idempotent but can never affect a
 *    newer generation.
 *  - Workers (neigh rebuild, pending replay) snapshot the generation and
 *    discard on mismatch/tombstone.
 *
 * The registry entry must OUTLIVE the ask_flow (which is freed via call_rcu),
 * so a late worker can still observe "cookie now at gen N, tombstoned". It is
 * a small xarray guarded by the xarray's own internal lock (xa_lock). Registry
 * ops take NO hardware action and never sleep, so they are safe to call from
 * process context outside (but never nested inside) the pending/rht locks.
 * Lock order, where both are held: xa_lock BEFORE pending_lock.
 */
enum ask_gen_state {
	ASK_GEN_LIVE       = 0,
	ASK_GEN_TOMBSTONED = 1,
};

/* gen_by_cookie stores xa_mk_value((generation << 1) | state); no per-flow
 * allocation, so tombstones can safely outlive ask_flow without leaking heap
 * objects. Entries are reclaimed wholesale when the flow table is destroyed. */

struct ask_flow_table {
struct rhashtable rht;
atomic_t fake_hw_id_seq; /* PR7 placeholder until real hostcmd */
struct xarray gen_by_cookie;   /* cookie -> xa_value(gen|state), A3 */
atomic_t num_flows;
/*
 * Count of entries with ask_flow::hw_backed set. Lets the neigh
 * stale-MAC path skip its full-table walk outright when nothing is
 * offloaded — the common case whenever ASK is disengaged, where every
 * ARP/ND update would otherwise walk the entire table to match zero
 * flows. Advisory: read without the rht lock purely as a fast-path
 * skip, so a stale read costs at most one redundant (or skipped-then-
 * retried-next-event) walk, never correctness.
 */
atomic_t num_hw_backed;
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
    enum ask_hw_dir dir,
    u32 *out_hw_id);

/* Remove by cookie. Returns 0 on success, -ENOENT if not present.
 * Unconditional: used by administrative flush, teardown, and tests. Does not
 * consult the generation registry. Production DESTROY uses the _owned form. */
int ask_flow_remove(struct ask_flow_table *t, u64 cookie);

/*
 * T-M6-A3 generation-aware variants used by the production REPLACE/DESTROY and
 * the neighbour/pending workers. @generation is the ownership stamp the caller
 * claimed via ask_flow_gen_next().
 *
 * ask_flow_insert_owned(): checks, immediately before the rhashtable publish,
 * that @generation is still the current non-tombstoned owner of @cookie; if a
 * newer REPLACE or a DESTROY intervened it rolls back its own HW and returns
 * -ESTALE without publishing.
 *
 * ask_flow_remove_owned(): removes only if @generation is >= the stored flow's
 * generation (i.e. the caller is the current or a newer owner); a stale caller
 * returns -ESTALE (internal control signal) and leaves the newer flow intact.
 * The top-level DESTROY converts -ESTALE to user-visible success but MUST skip
 * the FE per-key delete, which would otherwise delete the newer HW record.
 */
int ask_flow_insert_owned(struct ask_flow_table *t, u64 cookie,
			  const struct ask_flow_key *key,
			  u32 oif, u32 action_flags, enum ask_hw_dir dir,
			  u32 generation, u32 *out_hw_id);
int ask_flow_remove_owned(struct ask_flow_table *t, u64 cookie,
			  u32 generation);

/* ------------------------------------------------------------------------- */
/* T-M6-A3: per-cookie ownership generation registry.                         */
/*                                                                            */
/* All keyed by the flow cookie and independent of the ask_flow lifetime.     */
/* None sleep or touch hardware; safe from process context. Callers must NOT  */
/* hold ask_flow_pending_lock when calling these (xa_lock is taken first per  */
/* the documented lock order).                                                */
/* ------------------------------------------------------------------------- */

/*
 * Claim ownership for a REPLACE: bump the cookie's generation, clear any
 * tombstone, and return the new generation (>= 1). Returns 0 on OOM; the
 * caller MUST fail the REPLACE to software rather than publish an unguarded
 * flow (memory pressure must not reopen CR-004).
 */
u32  ask_flow_gen_next(struct ask_flow_table *t, u64 cookie);

/* Current owning generation for a cookie, or 0 if unknown. */
u32  ask_flow_gen_current(struct ask_flow_table *t, u64 cookie);

/* True iff @gen is the current, non-tombstoned owner of @cookie. */
bool ask_flow_gen_is_current(struct ask_flow_table *t, u64 cookie, u32 gen);

/* Mark a cookie tombstoned (DESTROY). Idempotent. */
void ask_flow_gen_tombstone(struct ask_flow_table *t, u64 cookie);

/* Drop the registry entry entirely (called when the SW flow is finally
 * freed and no worker can still reference the cookie). Idempotent. */
void ask_flow_gen_release(struct ask_flow_table *t, u64 cookie);

/*
 * Snapshot the per-flow stats into the caller-supplied out parameters.
 * Uses u64_stats_fetch_begin/retry around the seqcount so 32-bit
 * readers don't see torn 64-bit values.
 */
int ask_flow_get_stats(struct ask_flow_table *t, u64 cookie,
       u64 *packets, u64 *bytes, u64 *last_seen_ns);

/* Update stats from hardware (used by the 1Hz poller in PR15h). */
void ask_flow_update_stats(struct ask_flow *f, u64 add_packets, u64 add_bytes);

/* T-M8-3: store ABSOLUTE per-flow counters read back from silicon (FMan FE
 * ehash record) and, atomically under the same seqcount, compute the DELTA
 * since the previous poll's baseline (advancing the baseline). @d_packets /
 * @d_bytes receive the delta the nft flowtable's accumulating
 * flow_stats_update() must be fed; a silicon reset (absolute < baseline, e.g.
 * after a per-key DESTROY->REPLACE) is reported as delta = current. The
 * absolute total lands in f->stats.{packets,bytes} for dump-flows/get-flow.
 * last_seen_ns advances only when the packet total grew. */
void ask_flow_set_hw_stats(struct ask_flow *f, u64 hw_packets, u64 hw_bytes,
			   u64 *d_packets, u64 *d_bytes);

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

/*
 * T-M6-3 neigh entry points, driven by ask_neigh.c's netevent notifier from a
 * workqueue (process context — both sleep via GFP_KERNEL insert):
 *   ask_flow_neigh_resolved()    — drain deferred-insert pending toward a
 *                                  now-resolved (dev, dst_ip) next-hop.
 *   ask_flow_neigh_mac_changed() — rebuild installed flows egressing to
 *                                  (dev, dst_ip) whose baked-in next_hop_mac
 *                                  no longer matches @new_mac (kills stale-MAC
 *                                  blackholing).
 *
 * T-M6-1 piece 4: ask_flow_neigh_mac_changed() takes the next-hop as a raw
 * byte pointer plus an ASK_FLOW_L3_* discriminator so it serves both arp_tbl
 * (4-byte key) and nd_tbl (16-byte key) without a parallel v6 copy — the flow
 * key already stores dst_ip as 16 bytes.  @dst_ip must point at
 * ask_flow_l3_addr_len(@l3_proto) readable bytes.
 *
 * ask_flow_neigh_resolved() stays IPv4-only on purpose: it drains the
 * deferred-insert pending queue, which is keyed by __be32 and is only ever
 * populated by the v4 HW-insert path.  v6 flows are parsed and SW-tracked but
 * rejected at the HW gate (-EOPNOTSUPP) until T-M6-1 pieces 2-3 land, so there
 * is nothing for a v6 event to drain.  Generalise it together with the pending
 * queue when the v6 HW insert branch lands.
 */
static inline unsigned int ask_flow_l3_addr_len(u8 l3_proto)
{
return l3_proto == ASK_FLOW_L3_IPV6 ? 16 : 4;
}

/*
 * FE-VM EKFC key serialiser (CR-002). Exposed only so the kunit suite can
 * assert the exact wire layout; production callers are the insert and
 * delete paths inside ask_flow_offload.c. @k must have room for
 * ASK_FE_KEY_SIZE(_V6) bytes.
 *
 * F-163 (2026-08-05): PORT_ID prefix added. The real NXP cdx.ko driver
 * (kernel/flavors/ask/sources/cdx/cdx-5.03.1/cdx_common.h `union dpa_key`,
 * cdx_ehash.c `fill_key_info()`, nxp-sdk branch) builds its external-hash
 * key as portid(1B) | SIP | DIP | PROTO | SPORT | DPORT -- a leading
 * port-id byte no prior EKFC hypothesis on this branch included. KeyGen's
 * EKFC has a matching field, KG_SCH_KN_PORT_ID (bit 31, 1 byte,
 * specs/fman-keygen-flow-key-spec.md sec.4.1); since the EKFC assembly
 * order was independently silicon-confirmed MSB-first descending
 * (sec.3.4, 2026-07-13 CRC-64 hardware match), bit 31 being the highest
 * set bit lands it at byte offset 0, ahead of every other field --
 * exactly the vendor's layout. Widths bumped 13->14 (v4) and 37->38 (v6)
 * accordingly.
 */
#define ASK_FE_KEY_SIZE 14
#define ASK_FE_KEY_SIZE_V6 38
/*
 * Dual-lane 46-byte key (specs/ask2-ipv6-dual-lane-key-design.md, silicon-proven
 * 2026-08-21). One fixed-width key carries BOTH families so a single match-all
 * AC_CC scheme + one per-port table serve v4 and v6 with no parser LCV split.
 * Layout (matches the F-224 GEC extraction order exactly):
 *   [0]      FAMILY   0x80 v4 / 0x40 v6   (parse-result L3R byte 4)
 *   [1..8]   IPv4 src(4) dst(4)           (zero on a v6 flow)
 *   [9..24]  IPv6 src(16)                 (zero on a v4 flow)
 *   [25..40] IPv6 dst(16)                 (zero on a v4 flow)
 *   [41]     proto / next-header
 *   [42..45] L4 sport(2) dport(2)
 */
#define ASK_FE_KEY_SIZE_DUAL 46
#define ASK_FE_FAMILY_V4 0x80
#define ASK_FE_FAMILY_V6 0x40
void ask_fe_build_key(const struct ask_flow_key *key, u8 k[ASK_FE_KEY_SIZE]);
void ask_fe_build_key_v6(const struct ask_flow_key *key, u8 k[ASK_FE_KEY_SIZE_V6]);
void ask_fe_build_key_dual(const struct ask_flow_key *key,
			   u8 k[ASK_FE_KEY_SIZE_DUAL]);

void ask_flow_neigh_resolved(struct net_device *dev, __be32 dst_ip);
void ask_flow_neigh_mac_changed(struct net_device *dev, const u8 *dst_ip,
       u8 l3_proto, const u8 *new_mac);

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
/* ask flow-offload action flag bits                                          */
/*                                                                            */
/* These were originally the §12.4 wire-format action flag bits consumed by   */
/* the ask_hostcmd encoder layer. v1.3 Phase 3 deleted that opcode/encoder    */
/* layer (the Path A architecture talks to FMan via fman_pcd_cc_node_* and    */
/* fman_pcd_manip_* directly, not via a wire-format command channel), but    */
/* the flag-bit values themselves remain the ask.ko-internal ABI between     */
/* ask_flow_offload (which parses nft / netfilter actions into a flag        */
/* bitmask) and ask_hw (which consumes the bitmask to decide which          */
/* PCD manip variants to arm). The numeric values are arbitrary — no on-     */
/* wire compatibility requirement — but they are kept stable here so       */
/* existing ask_flow_offload.c / ask_hw.c references compile unchanged.     */
/* ------------------------------------------------------------------------- */
#define ASK_ACT_TTL_DEC             (1U << 0)
#define ASK_ACT_NAT_SRC             (1U << 1)
#define ASK_ACT_NAT_DST             (1U << 2)
#define ASK_ACT_PAT                 (1U << 3)
#define ASK_ACT_VLAN_PUSH           (1U << 4)
#define ASK_ACT_VLAN_POP            (1U << 5)
#define ASK_ACT_TO_CAAM             (1U << 6)
#define ASK_ACT_TO_OP               (1U << 7)

/* ------------------------------------------------------------------------- */
/* ask_stats.c — per-interface ASK2 HW-offload bandwidth accounting (Design 2) */
/*                                                                            */
/* Offloaded flows are forwarded by the FMan FE engine, bypassing the DPAA    */
/* netdev software counters, so /proc/net/dev under-reports offloaded         */
/* throughput. The per-flow silicon deltas read by ask_flow_offload.c's       */
/* FLOW_CLS_STATS poll are also attributed here to the flow's ingress (RX)    */
/* and egress (TX) ifindex, and the DPAA driver folds them into               */
/* ndo_get_stats64() via struct dpaa_flow_offload_ops::offload_stats          */
/* (board patch 0171).                                                        */
/* ------------------------------------------------------------------------- */
struct rtnl_link_stats64;

int  ask_stats_init(void);
void ask_stats_exit(void);
void ask_port_stats_add(int ifindex, u64 rx_packets, u64 rx_bytes,
			u64 tx_packets, u64 tx_bytes);
void ask_port_stats_zero(int ifindex);
void ask_port_stats_get(int ifindex, struct rtnl_link_stats64 *hw);

/* ------------------------------------------------------------------------- */
/* ask_debugfs.c - /sys/kernel/debug/ask (gated on CONFIG_DEBUG_FS)           */
/* ------------------------------------------------------------------------- */
int  ask_debugfs_init(void);
void ask_debugfs_exit(void);

#endif /* _ASK_INTERNAL_H */