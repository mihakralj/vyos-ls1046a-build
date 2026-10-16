/* SPDX-License-Identifier: BSD-3-Clause OR GPL-2.0-or-later */
/*
 * dpaa_fman_caps.h - FMan PCD capability bits + HW-offload stub API
 *
 * The FMan PCD blocks (CC trees, HM nodes, Policer profiles, HC dispatch,
 * Parser soft sequences) are unlocked by NXP FMan microcode 210+ which
 * U-Boot loads into MURAM from SPI mtd4 at SoC bring-up.  Public mainline
 * 6.18 cannot consume these blocks because:
 *
 *   1. There is no in-tree probe that reports the loaded ucode version.
 *   2. There are no exported helpers (fman_cc_*, fman_hm_*, fman_pol_*).
 *
 * This header is the M3-3b stub layer for both gaps.  It defines:
 *
 *   - FMAN_CAP_* bits matching dpaa1-afxdp-modernization-spec.md sec 3.5
 *   - dpaa_fman_get_caps() returning a u32 bitmask of available caps
 *   - fman_cc_tree_install/add_key/remove_key/destroy stubs returning
 *     -ENOTSUPP unconditionally (productive implementation lands in
 *     follow-up patches per spec sec 5.4)
 *
 * Until the productive ucode-version detection lands (planned: read
 * FMan ucode header from MURAM at fman_load_firmware time, or a DT
 * property "fsl,fman-ucode-version"), dpaa_fman_get_caps() returns 0
 * unless the operator overrides via the dpaa_fman_caps.force= module
 * parameter for development testing.
 *
 * Spec: specs/dpaa1-afxdp-modernization-spec.md v5.0 sec 3.5 + sec 5.4.
 */

#ifndef __DPAA_FMAN_CAPS_H
#define __DPAA_FMAN_CAPS_H

#include <linux/types.h>
#include <linux/bits.h>

struct device;
struct fman;
struct fman_cc_static_tree;
struct fman_cc_key;
struct net_device;

/* Spec sec 3.5: FMan PCD capability bitmask.  Each bit indicates that
 * the loaded FMan microcode supports the corresponding PCD primitive.
 * Add new bits at the end; do not renumber.
 */
#define FMAN_CAP_CC_EXACT_MATCH   BIT(0)   /* ucode 210+: CC trees */
#define FMAN_CAP_HM_NODES         BIT(1)   /* ucode 210+: HM nodes */
#define FMAN_CAP_POLICER_TRTCM    BIT(2)   /* ucode 210+: srTCM/trTCM */
#define FMAN_CAP_HC_DISPATCH      BIT(3)   /* ucode 210+: Host Command */
#define FMAN_CAP_PARSER_SOFTSEQ   BIT(4)   /* ucode 210+: Parser soft seq */

#define FMAN_CAP_ALL (FMAN_CAP_CC_EXACT_MATCH | FMAN_CAP_HM_NODES | \
		      FMAN_CAP_POLICER_TRTCM | FMAN_CAP_HC_DISPATCH | \
		      FMAN_CAP_PARSER_SOFTSEQ)

/**
 * dpaa_fman_get_caps - return the FMan PCD capability bitmask
 *
 * Currently returns the value of the dpaa_fman_caps.force module
 * parameter (default 0 = "no caps", matching mainline ucode 106
 * behaviour).  Future revisions will probe the loaded ucode at
 * runtime and populate this from FMan firmware headers / DT.
 *
 * Caller (dpaa_eth_probe) stores the result in priv->fman_caps so
 * that per-netdev fman_*_install() helpers can short-circuit to
 * -ENOTSUPP on unsupported silicon/ucode combinations without
 * touching MURAM.
 *
 * Spec sec 3.5: capability layer.
 */
u32  dpaa_fman_get_caps(void);

/**
 * dpaa_fman_caps_log - one-shot KERN_INFO log of the cap bitmask
 *
 * Called from dpaa_eth_probe() for the first netdev only.  Emits one
 * line of the form:
 *
 *   fsl_dpa: FMan PCD caps = 0x00 (mainline ucode 106 / no PCD offload)
 *   fsl_dpa: FMan PCD caps = 0x1F (CC HM POL HC PARSER)
 *
 * so operators can confirm at boot whether the cap-detection layer
 * sees any PCD primitives.  Idempotent across multiple probes.
 */
void dpaa_fman_caps_log(struct device *dev, u32 caps);

/* ------------------------------------------------------------------ *
 * M3-3b STUB API  (spec sec 5.4 CC steering)
 *
 * All four helpers below are unconditional -ENOTSUPP stubs in this
 * patch.  Productive implementations replace the bodies in follow-up
 * patches (0086+ chain).  The API contract is fixed now so consumers
 * (af_xdp_pool.ko qband-select, future vyos-1x set-system-offload-
 * classify CLI, ASK2 flowtable bridge) can call them today and
 * gracefully degrade on ucode-106 silicon.
 * ------------------------------------------------------------------ */

int  fman_cc_tree_install(struct fman *fm, u8 port_id,
			  const struct fman_cc_static_tree *spec);
int  fman_cc_tree_add_key(struct fman *fm, u8 port_id,
			  const struct fman_cc_key *key, u32 *handle);
int  fman_cc_tree_remove_key(struct fman *fm, u8 port_id, u32 handle);
void fman_cc_tree_destroy(struct fman *fm, u8 port_id);

/* Productive CC (Coarse Classification) data structures (spec sec 5.4).
 *
 * The FMan PCD Custom Classifier matches a fixed 5-tuple extracted by
 * the Parser (post-HXS) against a table of keys held in MURAM; each key
 * that hits steers the frame to a target RX FQ (and thereby a qband /
 * CPU per spec sec 5.2). These structs describe the key/tree at the API
 * boundary; the silicon AD-table / group-table encoding (CONT_LOOKUP AD
 * format per LS1046A RM 8.7.4.1) is built inside the productive
 * fman_cc_tree_install()/add_key() bodies (follow-up patch).
 *
 * Match semantics: a field participates in the match iff the
 * corresponding *_mask bit(s) are set; mask 0 = wildcard (don't-care).
 * All multi-byte fields are host-endian at the API; the install body
 * converts to the FMan big-endian MURAM layout.
 */

/* L4 protocol selector for fman_cc_key.proto (IANA numbers). 0 = any. */
#define FMAN_CC_PROTO_ANY	0
#define FMAN_CC_PROTO_TCP	6
#define FMAN_CC_PROTO_UDP	17

/* EtherType selector for fman_cc_key.ethertype (host-endian). 0 = any. */
#define FMAN_CC_ETHERTYPE_ANY	0x0000
#define FMAN_CC_ETHERTYPE_IPV4	0x0800
#define FMAN_CC_ETHERTYPE_IPV6	0x86dd

/**
 * struct fman_cc_key - one exact/masked CC classification key
 * @ethertype:    L2 EtherType to match (host-endian), 0 = wildcard
 * @proto:        L4 protocol (FMAN_CC_PROTO_*), 0 = wildcard
 * @src_ip:       source IPv4 (host-endian); IPv6 uses src_ip6 instead
 * @dst_ip:       destination IPv4 (host-endian)
 * @src_ip6:      source IPv6 (network byte order, 16 bytes)
 * @dst_ip6:      destination IPv6 (network byte order, 16 bytes)
 * @src_port:     L4 source port (host-endian), 0 = wildcard
 * @dst_port:     L4 destination port (host-endian), 0 = wildcard
 * @src_ip_mask:  prefix-style mask for src_ip (host-endian); 0 = any
 * @dst_ip_mask:  prefix-style mask for dst_ip (host-endian); 0 = any
 * @target_qband: qband [0 .. xsk_max_qbands-1] to steer matching frames
 * @target_fqid:  explicit target frame-queue id for matching frames.
 *                Non-zero = the leaf AD is the REAL RM 8.7.4.3 hardware
 *                enqueue-AD: a silicon HIT enqueues directly to this FQ
 *                (per-key FQ steering).  0 = soft fall-through (the frame
 *                proceeds to the normal RSS scheme dispatch).  When 0 and
 *                a consumer wants qband steering it must resolve
 *                target_qband -> FQID itself (the dpaa flavor layer owns
 *                that map) and set this field.
 * @hm_handle:    HM node handle to chain after match (0 = no HM); the
 *                value returned by fman_hm_node_install() per spec sec 5.5.
 *                Honoured only with a non-zero @target_fqid.
 * @is_ipv6:      when true, match src_ip6/dst_ip6 instead of src_ip/dst_ip
 *
 * A field is matched iff it (or its mask) is non-zero. The productive
 * add_key() body rejects a key whose target_qband >= priv->xsk_max_qbands
 * with -ERANGE and one whose hm_handle is non-zero on a build without
 * FMAN_CAP_HM_NODES with -ENOTSUPP.
 */
struct fman_cc_key {
	u16 ethertype;
	u8  proto;
	u8  is_ipv6;
	u32 src_ip;
	u32 dst_ip;
	u32 src_ip_mask;
	u32 dst_ip_mask;
	u8  src_ip6[16];
	u8  dst_ip6[16];
	u16 src_port;
	u16 dst_port;
	u16 target_qband;
	u32 target_fqid;
	u32 hm_handle;
};

/**
 * struct fman_cc_static_tree - a port's complete static CC table
 * @num_keys: number of valid entries in @keys
 * @miss_qband: qband for frames matching no key (the tree's miss action);
 *              default 0 maps to the existing RSS-hash default path
 * @miss_fqid: explicit FQID for frames matching no key; non-zero = the
 *             miss slot carries a hardware enqueue-AD (steers ALL
 *             non-matching traffic - use deliberately), 0 = miss traffic
 *             stays on the existing RSS-hash path (the default)
 * @keys: the key array, evaluated in order; first match wins
 *
 * Passed to fman_cc_tree_install() for the static (default/vpp) lifecycle.
 * The dynamic (ask) lifecycle builds the same key one-at-a-time via
 * fman_cc_tree_add_key(). FMAN_CC_MAX_STATIC_KEYS bounds the static tree
 * at the ~10 KiB MURAM budget from spec sec 3.5 (64 keys x ~150 B; raised
 * from 32 2026-08-31, T-M6-8 VLAN throughput investigation).
 */
#define FMAN_CC_MAX_STATIC_KEYS	64

struct fman_cc_static_tree {
	u16 num_keys;
	u16 miss_qband;
	u32 miss_fqid;
	/* R4c: non-zero FE_ENTER MURAM AD offset for CC-miss -> ehash. */
	u32 miss_fe_off;
	struct fman_cc_key keys[FMAN_CC_MAX_STATIC_KEYS];
};

/* ------------------------------------------------------------------ *
 * M3-3c STUB API  (spec sec 5.5 Header Manipulation)
 *
 * Header Manipulation (HM) nodes program the FMan PCD HM block to
 * mutate L2/L3 headers (VLAN push/pop, MAC rewrite, IPv4/IPv6 NAT,
 * TTL decrement, DSCP rewrite) entirely in silicon after CC matching
 * and before QMan enqueue. Per spec sec 5.4 chain ordering:
 *
 *   Parser \u2192 CC \u2192 HM \u2192 QMan
 *
 * Gated on FMAN_CAP_HM_NODES which lights up automatically on ucode
 * 210+ silicon (verified on Mono Gateway DK 2026-05-28 — "Microcode
 * version 210.10.1 for LS1043 r1.0" advertises HM_NODES per the
 * dpaa_fman_caps_from_id() string parser).
 *
 * All three helpers below are unconditional -ENOTSUPP stubs in this
 * patch; the productive implementation lands in a follow-up patch
 * per spec sec 5.5. The API contract is fixed now so consumers
 * (af_xdp_pool egress rewrite, ASK2 flowtable bridge, vyos-1x NAT
 * offload CLI) can wire calls today and gracefully degrade on
 * ucode-106 silicon.
 * ------------------------------------------------------------------ */

/* Productive HM (Header Manipulation) spec (spec sec 5.5).
 *
 * An HM node is an ordered list of header-mutation operations the FMan
 * PCD HM block applies in silicon after a CC match and before QMan
 * enqueue (Parser -> CC -> HM -> QMan). Each op is one TLV entry; the
 * productive fman_hm_node_install() body walks @ops[0 .. num_ops-1] and
 * emits the matching HMCT (Header Manipulation Command Table) records
 * into MURAM, finalising one HMTD (Header Manipulation Table Descriptor)
 * whose offset becomes the returned @handle (the value a CC action's
 * HM-pointer field carries per spec sec 5.4).
 */

/**
 * enum fman_hm_op_type - one header-manipulation operation kind
 * @FMAN_HM_OP_VLAN_STRIP:  pop the outermost VLAN tag (ingress)
 * @FMAN_HM_OP_VLAN_INSERT: push a VLAN tag (uses .vlan_insert)
 * @FMAN_HM_OP_MPLS_PUSH:   push an MPLS label (uses .mpls_push)
 * @FMAN_HM_OP_MPLS_POP:    pop the outermost MPLS label
 *
 * Per spec sec 5.5 op list: VLAN_STRIP, VLAN_INSERT(vlan_id, tpid),
 * MPLS_PUSH(label, tc, s, ttl), MPLS_POP. Add new ops at the end; do not
 * renumber (the value is encoded into the HMCT opcode by the install
 * body).
 */
enum fman_hm_op_type {
	FMAN_HM_OP_VLAN_STRIP	= 0,
	FMAN_HM_OP_VLAN_INSERT	= 1,
	FMAN_HM_OP_MPLS_PUSH	= 2,
	FMAN_HM_OP_MPLS_POP	= 3,
	/* L3-forward op-set extension (board patch 0119). */
	FMAN_HM_OP_RMV_ETHERNET	= 4,
	FMAN_HM_OP_INSRT_GENERIC = 5,
	FMAN_HM_OP_IPV4_FORWARD	= 6,
	/* IPv6 L3-forward counterpart (board patch 0201). */
	FMAN_HM_OP_IPV6_FORWARD	= 7,
};

/* Parameters for FMAN_HM_OP_VLAN_INSERT. */
struct fman_hm_vlan_insert {
	u16 vlan_id;/* 12-bit VID in the low bits (host-endian) */
	u16 tpid;/* 0x8100 C-VLAN or 0x88a8 S-VLAN (host-endian) */
	u8  pcp;/* 3-bit priority code point */
};

/* Parameters for FMAN_HM_OP_MPLS_PUSH. */
struct fman_hm_mpls_push {
	u32 label;/* 20-bit MPLS label (host-endian) */
	u8  tc;/* 3-bit traffic class */
	u8  s;/* bottom-of-stack bit */
	u8  ttl;/* MPLS TTL */
};

/* Parameters for FMAN_HM_OP_INSRT_GENERIC: @size header bytes inserted at
 * frame offset 0 (a new outer L2 header).  @size in 1..32.
 */
#define FMAN_HM_INSRT_GENERIC_MAX	32
struct fman_hm_insrt_generic {
	u8 size;/* header bytes to insert (1..32) */
	u8 hdr[FMAN_HM_INSRT_GENERIC_MAX];/* the header image */
};

/* Parameters for FMAN_HM_OP_IPV4_FORWARD: routed-frame IPv4 edits. */
struct fman_hm_ipv4_forward {
	u8 dec_ttl;/* nonzero: decrement the IPv4 TTL */
	u8 l4_csum;/* nonzero: recompute L3+L4 checksums in silicon */
};

/*
 * Parameters for FMAN_HM_OP_IPV6_FORWARD: routed-frame IPv6 edits (board
 * patch 0201). IPv6 has no header checksum and hop-limit is not part of
 * the L4 pseudo-header, so unlike IPV4_FORWARD there is no l4_csum flag --
 * decrementing the hop limit needs no checksum recompute at all (NXP SDK
 * fm_manip.c BuildHmct, HMCD_OPCODE_IPV6_UPDATE branch).
 */
struct fman_hm_ipv6_forward {
	u8 dec_hl;/* nonzero: decrement the IPv6 Hop Limit */
};

/**
 * struct fman_hm_op - one entry in an HM node's op list
 * @type:        which mutation (enum fman_hm_op_type)
 * @vlan_insert: parameters when @type == FMAN_HM_OP_VLAN_INSERT
 * @mpls_push:   parameters when @type == FMAN_HM_OP_MPLS_PUSH
 *
 * STRIP / POP ops carry no parameters. The union keeps the per-op
 * footprint small while remaining extensible.
 */
struct fman_hm_op {
	enum fman_hm_op_type type;
	union {
		struct fman_hm_vlan_insert vlan_insert;
		struct fman_hm_mpls_push   mpls_push;
		struct fman_hm_insrt_generic insrt_generic;
		struct fman_hm_ipv4_forward  ipv4_forward;
		struct fman_hm_ipv6_forward  ipv6_forward;
	};
};

/**
 * struct fman_hm_spec - an ordered HM operation list for one node
 * @num_ops: number of valid entries in @ops
 * @ops: the operations, applied in array order
 *
 * FMAN_HM_MAX_OPS bounds a single node at the ~1 KiB MURAM HM budget
 * from spec sec 3.5. The productive install body rejects num_ops == 0
 * or > FMAN_HM_MAX_OPS with -EINVAL.
 */
#define FMAN_HM_MAX_OPS	8

struct fman_hm_spec {
	u8 num_ops;
	struct fman_hm_op ops[FMAN_HM_MAX_OPS];
};

/**
 * fman_hm_node_install - allocate and program a HM node for @port_id
 *
 * On success the returned @handle is opaque and must be passed back to
 * fman_hm_node_destroy() for cleanup. The handle is also the value a
 * matching CC action's HM-pointer field must carry per spec sec 5.4
 * chain encoding (Parser \u2192 CC \u2192 HM \u2192 QMan).
 *
 * Returns 0 on success and writes @handle; -ENOTSUPP on stub build /
 * unsupported silicon (will bump priv->hw_offload_unavailable on the
 * caller side, mirroring the CC steering convention).
 *
 * Spec sec 5.5: HM API.
 */
int  fman_hm_node_install(struct fman *fm, u8 port_id,
			  const struct fman_hm_spec *spec, u32 *handle);

/**
 * fman_hm_node_destroy - release a HM node previously installed
 *
 * Idempotent — destroying an already-released or never-installed handle
 * is a no-op and returns 0. Detaches the node from any CC actions that
 * still reference it (their AD records are rewritten to bypass HM).
 *
 * Spec sec 5.5: HM API.
 */
int  fman_hm_node_destroy(struct fman *fm, u8 port_id, u32 handle);

/**
 * fman_hm_caps_supported - returns true when ucode advertises HM
 *
 * Cheap wrapper around (dpaa_fman_get_caps() & FMAN_CAP_HM_NODES).
 * Provided so consumers can early-skip building HM specs they can't
 * install anyway.
 */
bool fman_hm_caps_supported(void);

/**
 * fman_hm_nexthop_get - shared HM node for an L3 next-hop adjacency
 *
 * Resolves (installing on first use) the dedup'd header-manip node that
 * rewrites L2 for the (egress_tx_fqid, src_mac, dst_mac) adjacency and
 * decrements the IPv4 TTL, then bumps its refcount.  On success @handle
 * receives the HMTD MURAM offset for a CC FORWARD_FQ_WITH_MANIP action.
 * Many flows toward one next hop share a single node, so MURAM use scales
 * O(next-hops) not O(flows) (spec sec 13.4).  Sleepable; process context.
 *
 * Returns 0 and writes @handle; -ENOTSUPP when ucode lacks HM caps.
 */
int  fman_hm_nexthop_get(struct fman *fm, u8 port_id, u32 egress_tx_fqid,
			 const u8 *src_mac, const u8 *dst_mac, u32 *handle);

/**
 * fman_hm_nexthop_put - release a next-hop reference from fman_hm_nexthop_get()
 *
 * Drops one reference; when the last user of the adjacency goes away the
 * shared HM node is destroyed and its MURAM reclaimed.  @handle is the
 * value a prior _get() returned.  Idempotent for an unknown handle.
 */
int  fman_hm_nexthop_put(struct fman *fm, u8 port_id, u32 handle);

/**
 * fman_hm_vlan_get - shared VLAN header-manip node for a tag-edit adjacency
 *                    (T-M6-8 R2; plans/ASK2-VLAN-REARCH.md)
 * @is_push:        true = INSERT; false = STRIP
 * @egress_tx_fqid: egress TX FQ (dedup key)
 * @handle:         HMTD MURAM offset for a CC leaf hm_handle
 */
int  fman_hm_vlan_get(struct fman *fm, u8 port_id, bool is_push,
		      u16 vid, u16 tpid, u8 pcp, u32 egress_tx_fqid,
		      u32 *handle);

/** fman_hm_vlan_put - release a VLAN reference from fman_hm_vlan_get() */
int  fman_hm_vlan_put(struct fman *fm, u8 port_id, u32 handle);

/**
 * fman_hm_vlan_route_get - combined VLAN-edit + L3 next-hop HM node for a
 *                          ROUTED-VLAN adjacency (T-M6-8 R4a; translate R4d)
 * @do_pop:          strip the ingress tag before the L2/L3 rebuild
 * @do_push:         insert (@vid/@tpid/@pcp) after the L2/L3 rebuild
 * @is_v6:           the routed frame's L3 family (board patch 0201) --
 *                   selects IPV4_FORWARD(dec_ttl,l4_csum) vs
 *                   IPV6_FORWARD(dec_hl) for the L3 rebuild step, and the
 *                   rebuilt L2 header's EtherType (0x0800 vs 0x86dd). Part
 *                   of the dedup key: a v4 and v6 flow sharing every other
 *                   parameter must NEVER share a cached HMTD.
 * @vid/@tpid/@pcp:  pushed tag params (@do_push only)
 * @src_mac:         egress-port source MAC for the rebuilt L2 header
 * @dst_mac:         next-hop destination MAC for the rebuilt L2 header
 * @egress_tx_fqid:  egress TX FQ (dedup key)
 * @handle:          on success, HMTD MURAM offset for a CC leaf hm_handle
 *
 * @do_pop and @do_push are independent -- POP-only, PUSH-only, or BOTH (a
 * same-port VID-to-VID TRANSLATE, e.g. two 802.1Q vifs on one physical port
 * routing to each other) are all valid; at least one must be set. Builds one
 * HMTD that does the tag edit(s) AND {RMV_ETHERNET, INSRT_GENERIC(L2),
 * IPV4_FORWARD(dec_ttl,l4_csum) or IPV6_FORWARD(dec_hl)} - everything a
 * routed-VLAN frame needs. Use fman_hm_vlan_get() (VLAN-only) for a bridged
 * VLAN flow instead. Refcounted; sleepable; process context.
 */
int  fman_hm_vlan_route_get(struct fman *fm, u8 port_id,
			    bool do_pop, bool do_push, bool is_v6,
			    u16 vid, u16 tpid, u8 pcp,
			    const u8 *src_mac, const u8 *dst_mac,
			    u32 egress_tx_fqid, u32 *handle);

/** fman_hm_vlan_route_put - release a ref from fman_hm_vlan_route_get() */
int  fman_hm_vlan_route_put(struct fman *fm, u8 port_id, u32 handle);

/* ------------------------------------------------------------------ *
 * OOT CC-target resolvers (board patch 0121)
 *
 * Helpers an out-of-tree PCD consumer (ask.ko) uses to turn a kernel
 * net_device into the DPAA1 hardware identifiers a static CC tree needs:
 * the ingress RX fman_port (-> fman_port_get_id() -> port_id key) and an
 * egress TX frame-queue id (-> fman_cc_key.target_fqid).  Both validate
 * that the device is DPAA-owned before dereferencing private state.
 * ------------------------------------------------------------------ */
struct net_device;
struct fman_port;

/**
 * dpaa_get_rx_fman_port - ingress RX fman_port backing a DPAA netdev
 * @dev: a DPAA-owned net_device
 *
 * Returns the RX-side struct fman_port, or NULL when @dev is not a DPAA
 * interface or has no bound MAC.  Feed the result to fman_port_get_id()
 * to obtain the BMI hard port id used as the fman_cc_tree_* port key.
 */
struct fman_port *dpaa_get_rx_fman_port(struct net_device *dev);

/**
 * dpaa_get_tx_fqid - egress TX frame-queue id for a DPAA netdev
 * @dev:   a DPAA-owned net_device (the flow's sink/egress interface)
 * @queue: egress queue index (< dpaa_max_num_txqs())
 * @fqid:  out; receives the QMan FQID on success
 *
 * Returns 0 and writes @fqid, or -EINVAL/-ENODEV/-ERANGE/-ENOENT.  The
 * FQID is the target_fqid a CC FORWARD_FQ[_WITH_MANIP] action enqueues to.
 */
int dpaa_get_tx_fqid(struct net_device *dev, u32 queue, u32 *fqid);

/* ------------------------------------------------------------------ *
 * M3-3d STUB API  (spec sec 5.6 Policer)
 *
 * Policer profiles program the FMan PCD Policer block to do per-qband
 * or per-flow ingress rate-limit using srTCM (single-rate three-colour
 * marker, RFC 2697) or trTCM (two-rate three-colour marker, RFC 2698)
 * colour-aware marking entirely in silicon. Yellow/red drop decisions
 * are made in FMan; surviving FDs carry colour state in their status
 * field for downstream PCD steering.
 *
 * Hardware budget: 256 profile slots total per FMan, with a soft
 * per-netdev budget of 8 (4 per-qband + 4 per-flow) to leave headroom
 * for ASK2 flowtable bridge and vyos-1x firewall policer offload.
 *
 * Gated on FMAN_CAP_POLICER_TRTCM which lights up automatically on
 * ucode 210+ silicon (verified on Mono Gateway DK 2026-05-28 -- the
 * standard 210.10.1 QEF blob advertises POLICER_TRTCM per the
 * dpaa_fman_caps_from_id() string parser).
 *
 * All three helpers below are unconditional stubs in this patch
 * (-ENOTSUPP on install, no-op destroy); the productive implementation
 * lands in a follow-up patch per spec sec 5.6. The API contract is
 * fixed now so consumers (vyos-1x firewall limit offload CLI, VPP
 * per-qband rate-limit, ASK2 nft limit offload backend) can wire calls
 * today and gracefully degrade on ucode-106 silicon.
 * ------------------------------------------------------------------ */

/* Productive Policer profile (spec sec 5.6).
 *
 * One profile programs the FMan PCD Policer block to meter a flow (or a
 * whole qband) with a three-colour marker and drop/recolour out-of-profile
 * traffic in silicon, before QMan enqueue. The marker runs in the
 * Parser -> CC -> Policer -> HM -> QMan chain (spec sec 5.4): a CC action
 * whose policer-pointer field carries this profile's slot id arms the
 * meter for every frame the key matched.
 *
 * @mode selects single-rate three-colour (srTCM, RFC 2697 -- CIR + CBS +
 * EBS, @pir_bps/@pbs_bytes ignored) or two-rate three-colour (trTCM,
 * RFC 2698 -- CIR + CBS + PIR + PBS). @color_mode selects colour-blind
 * (ignore any pre-colour the frame already carries) or colour-aware
 * (honour the incoming colour as the marker's starting point).
 *
 * Rates are bits-per-second and bursts are bytes at the API; the
 * productive install body converts to the FMan exponent/mantissa
 * rate-field encoding (LS1046A RM 8.7.6: rate exp<<29 | mant<<13,
 * burst quantised to 256-byte units) and writes the 16-byte MURAM
 * profile record. The four fman_policer_* entry points stay -ENOTSUPP
 * stubs until that encoder lands (follow-up patch).
 */

/* Metering algorithm for fman_policer_profile.mode. */
enum fman_policer_mode {
	FMAN_POLICER_MODE_SRTCM	= 0,/* RFC 2697: CIR + CBS + EBS */
	FMAN_POLICER_MODE_TRTCM	= 1,/* RFC 2698: CIR + CBS + PIR + PBS */
};

/* Colour-awareness for fman_policer_profile.color_mode. */
enum fman_policer_color_mode {
	FMAN_POLICER_COLOR_BLIND	= 0,/* ignore any pre-existing frame colour */
	FMAN_POLICER_COLOR_AWARE	= 1,/* honour the incoming frame colour */
};

/**
 * struct fman_policer_profile - one srTCM/trTCM metering profile
 * @mode:       srTCM or trTCM (enum fman_policer_mode)
 * @color_mode: colour-blind or colour-aware (enum fman_policer_color_mode)
 * @cir_bps:    committed information rate, bits/sec (both modes)
 * @cbs_bytes:  committed burst size, bytes (both modes)
 * @pir_bps:    peak information rate, bits/sec (trTCM only; 0 for srTCM)
 * @pbs_bytes:  peak/excess burst size, bytes (trTCM PBS / srTCM EBS)
 *
 * The productive install body rejects @cir_bps == 0 with -EINVAL, a
 * trTCM profile whose @pir_bps < @cir_bps with -ERANGE, and a rate that
 * overflows the FMan exponent/mantissa field by saturating at the
 * silicon ceiling (RM 8.7.6).
 */
struct fman_policer_profile {
	enum fman_policer_mode       mode;
	enum fman_policer_color_mode color_mode;
	u64 cir_bps;
	u32 cbs_bytes;
	u64 pir_bps;
	u32 pbs_bytes;
};

/**
 * fman_policer_install - allocate slot @profile_id and program profile
 *
 * Programs a single policer profile in slot @profile_id on FMan @fm
 * for port @port_id. The caller is responsible for slot bookkeeping
 * (selecting a free profile_id within the per-netdev budget of 8);
 * the productive implementation will reject double-install on the
 * same slot.
 *
 * After install, a matching CC action's policer-pointer field must
 * carry @profile_id per spec sec 5.4 chain encoding
 * (Parser -> CC -> Policer -> HM -> QMan).
 *
 * Returns 0 on success; -ENOTSUPP on stub build / unsupported silicon
 * (will bump priv->hw_offload_unavailable on the caller side,
 * mirroring the CC steering convention); -EINVAL if @prof is NULL.
 *
 * Spec sec 5.6: Policer API.
 */
int  fman_policer_install(struct fman *fm, u8 port_id, u8 profile_id,
  const struct fman_policer_profile *prof);

/**
 * fman_policer_destroy - release policer slot @profile_id
 *
 * Idempotent -- destroying an unused or never-installed slot is a
 * no-op. Detaches the profile from any CC actions that still
 * reference it (their AD records are rewritten to bypass the
 * policer).
 *
 * Spec sec 5.6: Policer API.
 */
void fman_policer_destroy(struct fman *fm, u8 port_id, u8 profile_id);

/**
 * fman_policer_caps_supported - returns true when ucode advertises Policer
 *
 * Cheap wrapper around (dpaa_fman_get_caps() & FMAN_CAP_POLICER_TRTCM).
 * Provided so consumers can early-skip building policer profiles they
 * can't install anyway.
 */
bool fman_policer_caps_supported(void);

#endif /* __DPAA_FMAN_CAPS_H */