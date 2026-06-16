/* SPDX-License-Identifier: GPL-2.0 */
/*
 * ask_fman_caps.h — vendored, OOT-safe view of the dpaa1 COMMON-board
 * FMan PCD capability + CC-steering + header-manip (HM) consumer API.
 *
 * THIS IS A FAITHFUL COPY of the subset of
 *   drivers/net/ethernet/freescale/dpaa/dpaa_fman_caps.h
 * that ask.ko consumes.  The in-tree header is assembled at build time by
 * the unconditional COMMON-board patch series and is NOT shipped in the
 * linux-headers snapshot the OOT build compiles against (a bindeb-pkg
 * headers package carries only build scaffolding — include/, scripts/,
 * Makefiles/Kconfig, Module.symvers — never arbitrary driver headers).
 * The pre-existing OOT pattern in this module (see the inline re-declares
 * of dpaa_get_fman_port_id()/dpaa_get_tx_fqid() in ask_hw.c) is therefore
 * the only workable mechanism: re-declare the API locally.
 *
 * !!! ABI WARNING !!!
 * The struct layouts below MUST stay byte-identical to the in-tree
 * definitions, because they cross the EXPORT_SYMBOL_GPL boundary by
 * pointer.  Any drift silently corrupts MURAM programming.  These mirror:
 *   - struct fman_cc_key / fman_cc_static_tree : board patch 0086b
 *     + the target_fqid / miss_fqid additions in board patch 0108
 *   - fman_cc_tree_install/add_key/remove_key/destroy : board patch 0086
 *     (productive 0098/0106/0108/0115)
 *   - fman_hm_nexthop_get / fman_hm_nexthop_put : board patch 0120
 *   - fman_pcd_offload_engage / fman_pcd_offload_disengage : board patch 0129
 * When those board patches change a struct or prototype, update this file
 * in the SAME commit.
 */
#ifndef __ASK_FMAN_CAPS_H
#define __ASK_FMAN_CAPS_H

#include <linux/types.h>

/* Opaque to the OOT consumer; resolved inside fsl_dpa.ko. */
struct fman;

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
 *
 * All multi-byte scalar fields are HOST-ENDIAN at this API; the install
 * body converts to the FMan big-endian MURAM layout.  A field is matched
 * iff it (or its mask) is non-zero.  Mirrors board patch 0086b + the
 * target_fqid addition in 0108.
 *
 * @target_qband: qband [0 .. xsk_max_qbands-1] to steer matching frames.
 * @target_fqid:  explicit egress FQID for matching frames.  Non-zero = the
 *                leaf AD is the REAL RM 8.7.4.3 hardware enqueue-AD (per-key
 *                FQ steering — a silicon HIT enqueues directly here).  0 =
 *                soft fall-through to the normal RSS scheme dispatch.  A
 *                consumer wanting qband steering must resolve
 *                target_qband -> FQID itself and set this field.
 * @hm_handle:    HM node handle to chain after match (0 = no HM); the value
 *                returned by fman_hm_nexthop_get().  Honoured ONLY with a
 *                non-zero @target_fqid (the FORWARD_FQ_WITH_MANIP atom).
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

/*
 * Bounds the static tree at the ~5 KiB MURAM budget (32 keys x ~150 B).
 * Mirrors board patch 0086b.
 */
#define FMAN_CC_MAX_STATIC_KEYS	32

/**
 * struct fman_cc_static_tree - a port's complete static CC table
 *
 * Passed to fman_cc_tree_install() for the static (default/vpp) lifecycle.
 * The dynamic (ask) lifecycle builds keys one-at-a-time via
 * fman_cc_tree_add_key().  Mirrors board patch 0086b + the miss_fqid
 * addition in 0108.
 *
 * @miss_qband: qband for frames matching no key (default 0 = RSS path).
 * @miss_fqid:  explicit FQID for non-matching frames; non-zero steers ALL
 *              miss traffic to a hardware enqueue-AD (use deliberately),
 *              0 = miss traffic stays on the existing RSS-hash path.
 */
struct fman_cc_static_tree {
	u16 num_keys;
	u16 miss_qband;
	u32 miss_fqid;
	struct fman_cc_key keys[FMAN_CC_MAX_STATIC_KEYS];
};

/* ---- CC steering (spec sec 5.4 / board 0086,0098,0106,0108,0115) ---- */

int  fman_cc_tree_install(struct fman *fm, u8 port_id,
			  const struct fman_cc_static_tree *spec);
int  fman_cc_tree_add_key(struct fman *fm, u8 port_id,
			  const struct fman_cc_key *key, u32 *handle);
int  fman_cc_tree_remove_key(struct fman *fm, u8 port_id, u32 handle);
void fman_cc_tree_destroy(struct fman *fm, u8 port_id);

/* ---- next-hop header-manip dedup (spec sec 13.4 / board 0120) -------- */

/**
 * fman_hm_nexthop_get - resolve (and refcount) the shared HM node for an
 * L3 next-hop adjacency, installing it on first use.
 *
 * Builds, on first sight of an adjacency, the 3-op spec
 * {RMV_ETHERNET, INSRT_GENERIC(14-byte egress L2 = {dst_mac,src_mac,IPv4}),
 * IPV4_FORWARD(dec TTL + recompute cksums)}.  Later flows toward the same
 * next hop share the cached node, so MURAM use scales O(next-hops) not
 * O(flows).  On success @handle receives the HMTD MURAM offset to embed in
 * a CC key's @hm_handle (with a non-zero @target_fqid).  Sleepable; process
 * context only.  Returns -ENOTSUPP when the ucode lacks HM caps.
 *
 * NOTE on MAC argument order: the INSRT_GENERIC op writes @dst_mac at
 * hdr[0..5] and @src_mac at hdr[6..11], i.e. the egress L2 header is
 * dst=next-hop MAC, src=egress-port MAC.  Callers therefore pass
 * src_mac = the egress port's own MAC, dst_mac = the next-hop MAC.
 */
int  fman_hm_nexthop_get(struct fman *fm, u8 port_id, u32 egress_tx_fqid,
			 const u8 *src_mac, const u8 *dst_mac, u32 *handle);

/**
 * fman_hm_nexthop_put - release a reference taken by fman_hm_nexthop_get().
 *
 * Drops one reference; when the last flow using the adjacency goes away the
 * shared HM node is destroyed and its MURAM reclaimed.  @handle is the value
 * a prior _get() returned.  Idempotent for an unknown handle (returns 0).
 */
int  fman_hm_nexthop_put(struct fman *fm, u8 port_id, u32 handle);

/* ---- coarse offload mode-switch (board patch 0129) ------------------ */

/**
 * fman_pcd_offload_engage - flip ONE RX port S0 (mainline RSS) -> S1 (AC_CC).
 *
 * The single coarse entry ask.ko calls to engage hardware offload on a port.
 * Resolves the PCD internally from @fm (so ask.ko mirrors only this prototype,
 * never the in-kernel CC spec struct), installs a benign single-key CC tree
 * and grafts the port's KeyGen scheme onto it (KGSE_CCBS) - the exact
 * reversible sequence proven by the cc_test harness (board 0107) and the 100x
 * S0<->S1 soak.  M1 carries no classification semantics; the engaged port is
 * not trafficked by the mode-switch gate.  @hw_port_id is the FMan-side
 * hardware RX port id (eth3 = 0x10).  Process context only.
 *
 * Return: 0 on success; -ENODEV if @fm has no PCD; negative errno from the
 * install/graft on failure (no partial state is left installed).
 */
int  fman_pcd_offload_engage(struct fman *fm, u8 hw_port_id);

/**
 * fman_pcd_offload_disengage - flip ONE RX port S1 (AC_CC) -> S0 (RSS).
 *
 * Reverse of fman_pcd_offload_engage(): detaches the KGSE_CCBS graft (restores
 * RSS) then frees the CC tree.  Idempotent - a port with nothing engaged is a
 * safe no-op.
 */
void fman_pcd_offload_disengage(struct fman *fm, u8 hw_port_id);

#endif /* __ASK_FMAN_CAPS_H */
