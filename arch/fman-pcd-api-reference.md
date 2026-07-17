# FMan PCD Kernel API Reference (LS1046A)

**Version 2.0.0 · 2026-07-17 · HADS 1.0.0**

## AI READING INSTRUCTION

This document is the consolidated reference for **every exported C wrapper** of the FMan PCD (Parse-Classify-Distribute) subsystem on the NXP LS1046A, grouped by function group. It is derived from the in-tree public header `include/linux/fsl/fman_pcd.h` and the `EXPORT_SYMBOL_GPL` surface materialized by board patches `0086`–`0150` (`kernel/common/patches/board/`). The **code is the single source of truth**; when this doc and the header disagree, trust the header and file a fix here.

Read order: §1 scope → §2 programming model & conventions (READ THIS BEFORE ANY CALL) → §3 capability model + **silicon-function coverage audit** → §4–§14 the function groups → §15 public structs/enums → **§16 defensive coding requirements (BINDING) + defect register**. Every `**[SPEC]**` paragraph is a binding contract (signature, params, return, side effects, MURAM/caps); the §16 `R#`/`T#`/`C#` requirements are binding on every patch and reviewers MUST reject violations. `**[NOTE]**` is rationale/history. `**[?]**` marks a fact inferred from call sites or partial prototypes and NOT yet verified against the header — treat as provisional. `**[BUG]**` blocks record footguns with symptom + cause + fix.

Companion docs: `arch/fman-microcode-210-programming-reference.md` (register/MURAM substrate the wrappers write — §7.11 CONT_LOOKUP group AD, §6 params page, §7.2 EXT_HASH), `arch/fman-fe-ehash.md` (FE/ehash init contract + §4 `FmPortSetFESupport`), `specs/fman-keygen-flow-key-spec.md` v4.0 (settled dispatch topology §6.1), `specs/dpaa1-afxdp-modernization-spec.md` §5 (offload milestones + consumers).

---

## 1. Scope

**[SPEC]** This reference covers the **kernel-side, process-context control-plane API** only. It programs FMan tables at flow-setup rate; the per-packet fast path never enters these calls — the FMan silicon does the lookup directly against the MURAM/DDR tables these functions install.

**[SPEC]** The API is exported from three kernel objects: `fsl_dpaa_fman.ko`/built-in (the `fman_*`, `fman_pcd_*`, and demoted-static `keygen_*` families, owning all MURAM), the DPAA driver bridge (`dpaa_fman_caps.c` + `dpaa_eth.c` — the port-scoped consumer wrappers and the `dpaa_*` flavor-ops/flow-offload registration surface), and `caam` (`caam_qi_ext_consumer_*`, the SEC QI share for future IPsec). All symbols are `EXPORT_SYMBOL_GPL`. Total exported surface at this revision: **~78 functions** across 14 groups, plus the FE-VM debugfs bring-up surface (§10.2, deliberately NOT exported) and the planned `fman_port_recover()` (§13, design-only). The QMan-CEETM shaper surface (~40 `qman_ceetm_*` exports, patches `0111`/`0112`) is control-plane API for the egress-QoS capability but lives in the QMan driver — documented in [`arch/qman-ceetm.md`](qman-ceetm.md), cross-referenced in §14.4.

**[NOTE]** This is not the NXP SDK API. The design deliberately rejected the SDK's `handle_t` opaque-pointer ABI, `fsl-ncsw` OS-shim, AMP IPC, and 16-flavor `NextEngineParams` hierarchy in favour of typed `struct fman_pcd_*` handles, `ERR_PTR` error propagation, `devm_*` teardown, `rhashtable`, and kunit. The SDK was consulted only as a silicon-behaviour oracle (MURAM byte layouts, register-write ordering).

---

## 2. Programming Model and Calling Conventions

**[SPEC]** **Register → MURAM → silicon.** Every wrapper ultimately (a) allocates MURAM via `fman_pcd_muram_alloc()`, (b) writes a byte-exact table/descriptor with big-endian iomem accessors, and (c) points a CCSR register (or pushes through an indirect Action Register) at that MURAM offset. There is no host-command doorbell on this board (`FMAN_CAP_HC_DISPATCH` clear, `caps=0x17`), so `fmd_host_cmd_send()` returns `-ENXIO` and runtime table mutation uses in-tree MURAM rewrite (AttachPCD/DetachPCD save-restore), never a doorbell.

**[SPEC]** **Two API tiers.** Each engine exposes a **pcd-scoped object API** (`fman_pcd_<engine>_*`, opaque-handle create/destroy, takes `struct fman_pcd *`) and a **port-scoped consumer wrapper** (`fman_cc_tree_* / fman_hm_* / fman_policer_*`, takes `struct fman *fm, u8 port_id`) that the kernel-native bridges (`ethtool -N`, `tc` matchall, `NETIF_F_HW_VLAN_*`) call. The wrapper resolves the `struct fman_pcd *` via `fman_get_pcd()` and delegates to the object API.

**[SPEC]** **Error model.** Constructor-style calls returning a pointer use `ERR_PTR(-errno)` on failure — test with `IS_ERR()`/`PTR_ERR()`, never `== NULL`. `fman_pcd_muram_alloc()` returns an `unsigned long` MURAM offset using the mainline `IS_ERR_VALUE()` convention (errno encoded in the high bits) — test with `IS_ERR_VALUE()`. `int`-returning calls return `0` on success and `-errno` on failure. Common codes: `-ENOTSUPP` (capability absent), `-ENXIO`/`-ENODEV` (no FMan/PCD/port), `-EINVAL`/`-ERANGE` (bad argument), `-ENOMEM` (MURAM budget), `-EOPNOTSUPP` (unsupported KG extract slot).

**[SPEC]** **Address-space split.** Descriptor fields carry EITHER a MURAM offset (gen_pool offset, ~16-bit, from `fman_pcd_muram_alloc()`) OR a DDR bus address (`dma_addr_t` from `dma_alloc_coherent`, e.g. the ehash bucket array). These MUST NOT be crossed. Mixing them is a silent hardware hang, not an error return.

**[SPEC]** **Endianness / access.** All CCSR registers and MURAM words are big-endian u32. MURAM is iomem — use `memset_io`/`__iowrite32_copy`/`iowrite32be`, never `memcpy`.

**[SPEC]** **Concurrency.** All calls are process-context and take a single per-FMan `pcd->lock` mutex internally; none are atomic-safe or callable from IRQ/softirq. The lock is coarse (guards all engines) — correct for the cold flow-setup path, not optimized for high flow-insert rates.

**[SPEC]** **Lifecycle & ownership.** `fman_pcd_init()` is called by `fman_probe()` after `fman_muram_init()`; `fman_pcd_release()` is wired via `devm_add_action_or_reset()` and tears down every child object (per-engine lists). Object create/destroy calls MUST be symmetric — every allocation has an inverse that returns MURAM to the budget, and `used_bytes` MUST return to baseline after teardown. These are enforced as binding requirements in **§16 (Defensive Coding Requirements: R# memory-leak, T# strong-typing, C# completeness)** — read §16 before writing or reviewing any wrapper.

---

## 3. Capability Model

**[SPEC]** Each port carries `priv->fman_caps`, a bitmask auto-populated at probe by patch `0086a` walking the DT firmware-blob `qe_firmware.id`. Consumer wrappers gate on it and return `-ENOTSUPP` when the required cap is absent (e.g. ucode 106, or a board with no QEF injected).

**[SPEC]** Capability bits (`kernel/common/patches/board/0086*`):

| Bit | Symbol | Gates |
|---|---|---|
| `BIT(0)` | `FMAN_CAP_CC_EXACT_MATCH` | Coarse Classification (§7) |
| `BIT(1)` | `FMAN_CAP_HM_NODES` | Header Manipulation (§8) |
| `BIT(2)` | `FMAN_CAP_POLICER_TRTCM` | Policer (§9) |
| `BIT(3)` | `FMAN_CAP_HC_DISPATCH` | Host-Command doorbell — **CLEAR on the shipping 210.10.1 QEF** |
| `BIT(4)` | `FMAN_CAP_PARSER_SOFTSEQ` | Soft-parser sequences |

**[SPEC]** The Mono Gateway DK reports `caps = 0x17` (`CC | HM | POLICER | PARSER_SOFTSEQ`, HC clear). With no QEF injected the board reports `caps = 0x00` and degrades gracefully to mainline KG-RSS only — every offload wrapper returns `-ENOTSUPP` and the datapath stays fully functional (independent-mode RX/TX).

**[SPEC]** Reserved capability-bit placeholders for the four present-unconsumed 210.10.1 silicon functions (spec `dpaa1-afxdp-modernization-spec.md` §3.5): `BIT(5)` `FMAN_CAP_CC_HASH_TABLE`, `BIT(6)` `FMAN_CAP_IP_REASSEMBLY`, `BIT(7)` `FMAN_CAP_IP_FRAGMENTATION`, `BIT(8)` `FMAN_CAP_FRAME_REPLICATOR`. A patch that consumes one of these functions MUST claim its placeholder bit and gate on it — never overload an existing bit.

### 3.1 Silicon-Function Coverage Audit

**[SPEC]** The 210.10.1 microcode implements 12 PCD functions (microcode reference §12). This table is the completeness contract — every silicon function maps to an API group here or an explicit deferral. A patch consuming a "reserved" function extends this table in the same commit:

| # | Silicon function | Cap bit | API group | Status |
|---|---|---|---|---|
| 1 | Hard Parser | — | mainline `fman_prs.c` (no wrapper needed) | consumed |
| 2 | Soft Parser (1984 B soft-sequence) | BIT(4) | **§14.5 — cap advertised, NO kernel API yet** | gap (planned) |
| 3 | KeyGen (32 schemes, CRC-64, EKFC) | — | §6 Group C | consumed |
| 4 | KG post-hash index + PP-select | — | none — assessed P4/skip (no benefit on 4×A72) | deferred |
| 5 | CC Match-Table (exact-match) | BIT(0) | §7 Group D | consumed |
| 6 | CC Hash-Table (DDR) | BIT(5) rsvd | none — redundant with FE-VM ehash (P3/skip) | deferred |
| 7 | Header Manipulation | BIT(1) | §8 Group E | consumed |
| 8 | Policer (256 profiles) | BIT(2) | §9 Group F | consumed |
| 9 | FE-VM ehash (DDR flow store) | — | §10 Group G (dormant for HIT phase) | consumed |
| 10 | Frame Replicator | BIT(8) rsvd | §14.5 — KUnit exists, no wrapper (P1 after M4) | reserved |
| 11 | IP Reassembly | BIT(6) rsvd | §14.5 — params page `+0x10/+0x14` NIA fields exist (P0 after M3) | reserved |
| 12 | IP Fragmentation | BIT(7) rsvd | §14.5 — params page `+0x30` counter exists (P2) | reserved |

---

## 4. Group A — Subsystem Lifecycle & MURAM

Defined in patch `0092` (`fman_pcd.c`, `include/linux/fsl/fman_pcd.h`).

```c
struct fman_pcd *fman_pcd_init(struct fman *fman);
void             fman_pcd_release(struct fman_pcd *pcd);
unsigned long    fman_pcd_muram_alloc(struct fman_pcd *pcd, size_t size);
void             fman_pcd_muram_free(struct fman_pcd *pcd, unsigned long offset, size_t size);
struct fman_pcd_muram_budget fman_pcd_get_muram_budget(struct fman_pcd *pcd);
```

**[SPEC]** `fman_pcd_init(fman)` — initialise the PCD subsystem for one FMan instance. Reserves `FMAN_PCD_MURAM_RESERVED_BYTES` (64 KiB) from FMan MURAM, creates the per-instance debugfs dir, initialises the per-engine object lists and `pcd->lock`. Returns the root `struct fman_pcd *` handle or `ERR_PTR(-errno)` (`-ENOMEM` if the MURAM reservation fails — a real regression cause; see §16). Called once by `fman_probe()`.

**[SPEC]** `fman_pcd_release(pcd)` — tear down the subsystem: walk `kg/cc/manip/plcr/replic` lists freeing every child, release the MURAM reservation, remove debugfs. NULL-safe. Wired via `devm_add_action_or_reset()` so it runs on unbind/probe-failure.

**[SPEC]** `fman_pcd_muram_alloc(pcd, size)` — the single MURAM allocator all objects draw from. Returns a MURAM byte offset (test with `IS_ERR_VALUE()`; `-EINVAL` if `size==0`, `-ENXIO` if no muram, `-ENOMEM` if exhausted). Updates `pcd->muram_used`/`muram_high_water` under lock.

**[SPEC]** `fman_pcd_muram_free(pcd, offset, size)` — inverse of alloc; returns bytes to the budget. `offset` MUST be a value previously returned by `fman_pcd_muram_alloc()` for `pcd`.

**[SPEC]** `fman_pcd_get_muram_budget(pcd)` — returns a by-value `struct fman_pcd_muram_budget` snapshot (`reserved/used/free/high_water`, §15). Consumed by the debugfs `muram_budget` node and op-mode. `used == 0` after a full disengage is the reversibility acceptance gate (`pcd-snapshot`).

---

## 5. Group B — FMan Core Accessors

Provided by the `fman.c` integration hunks (patch `0092` + FMan driver).

```c
struct device     *fman_get_dev(struct fman *fman);
u8                 fman_get_id(struct fman *fman);
struct muram_info *fman_get_muram(struct fman *fman);
struct fman_pcd   *fman_get_pcd(struct fman *fman);
```

**[SPEC]** `fman_get_dev(fman)` — the backing `struct device *` (for `dev_err`, DMA ops). `fman_get_muram(fman)` — the `struct muram_info *` used by `fman_muram_alloc()`. `fman_get_pcd(fman)` — the per-FMan `struct fman_pcd *`; the bridge accessor every port-scoped wrapper uses to reach the object API. Returns NULL if PCD init failed (degraded boot) — callers must NULL-check and return `-ENOTSUPP`.

**[SPEC]** `fman_get_id(fman)` returns the 0-based FMan instance index as `u8` (`0` on single-FMan LS1046A). Confirmed `u8 fman_get_id(struct fman *fman)` in patch `0092` (the call-site `snprintf("%d", …)` is ordinary integer promotion).

---

## 6. Group C — KeyGen (KG)

Defined in patch `0097` (`fman_pcd_kg.c`, `fman_keygen.c`). Programs the 32 KeyGen schemes via the indirect `FMKG_AR` Action Register (`arch/fman-microcode-210-programming-reference.md` §4).

```c
struct fman_pcd_kg_scheme *fman_pcd_kg_scheme_create(struct fman_pcd *pcd,
                              const struct fman_pcd_kg_scheme_params *params);
void fman_pcd_kg_scheme_destroy(struct fman_pcd_kg_scheme *scheme);
int  fman_pcd_kg_bind_port(struct fman_pcd_kg_scheme *scheme, u8 port_id);
int  fman_pcd_kg_attach_cc(struct fman_pcd_kg_scheme *scheme, struct fman_pcd_cc_tree *cc);
int  fman_pcd_kg_port_attach_cc(struct fman_pcd *pcd, u8 hw_port_id, fman_muram_off_t cc_group_off);
int  fman_pcd_kg_port_detach_cc(struct fman_pcd *pcd, u8 hw_port_id);
int  fman_pcd_kg_port_attach_policer(struct fman_pcd *pcd, u8 hw_port_id, u8 profile_id);
int  fman_pcd_kg_port_detach_policer(struct fman_pcd *pcd, u8 hw_port_id);
int  fman_pcd_kg_port_arm_fe(struct fman_pcd *pcd, u8 hw_port_id, fman_muram_off_t fe_enter_off, u8 *saved_engine);
void fman_pcd_kg_port_disarm_fe(struct fman_pcd *pcd, u8 hw_port_id, u8 saved_engine);
/* demoted from file-static in fman_keygen.c (patch 0097/0133) — low-level scheme plumbing */
int  keygen_scheme_setup(struct fman_keygen *keygen, u8 scheme_id, bool enable);                    /* [?] */
int  keygen_bind_port_to_schemes(struct fman_keygen *keygen, u8 hwport_id, u32 schemes, bool bind); /* [?] */
```

**[SPEC]** `keygen_scheme_setup` / `keygen_bind_port_to_schemes` — mainline `fman_keygen.c` internals demoted to `EXPORT_SYMBOL_GPL` so the PCD layer can flip a scheme's next-engine (RSS ↔ AC_CC ↔ PLCR) and rebind ports without duplicating the FMKG_AR indirect-write protocol (microcode reference §4.1). **Plumbing, not consumer API** — wrapper bodies call them; bridges and `ask.ko` MUST NOT. **[?]** Exact signatures inferred — verify against the header before use.

**[SPEC]** `fman_pcd_kg_scheme_create(pcd, params)` — allocate and program one KeyGen scheme from `struct fman_pcd_kg_scheme_params` (EKFC extract mask, match vector, FQID base+range, hash mask/shift/symmetric, next-engine). Returns a `struct fman_pcd_kg_scheme *` handle or `ERR_PTR`. Returns `-EOPNOTSUPP` if a requested extract offset is not in the whitelisted known-field set (see §16 IPSEC_SPI defect).

**[SPEC]** `fman_pcd_kg_scheme_destroy(scheme)` — disable and free the scheme (frees its MURAM, clears the KGSE entry).

**[SPEC]** `fman_pcd_kg_bind_port(scheme, port_id)` — bind the scheme to a hardware port (writes `fmkg_pe_sp[hwport]`, reverse-bit encoded per RM 8.7.4; lowest-scheme-id-wins on multi-scheme ports).

**[SPEC]** `fman_pcd_kg_attach_cc(scheme, cc)` — flip a scheme into CC next-engine mode by RMW of `KGSE_CCBS` with the tree's group-table offset. `KGSE_MODE` stays `0x80500002` — CC walking on FMan v3 is implicit when `KGSE_CCBS != 0`. Arg 2 is `struct fman_pcd_cc_tree *cc` (**non-const** — the attach links scheme↔tree state; confirmed in patch `0097`).

**[SPEC]** `fman_pcd_kg_port_attach_cc(pcd, hw_port_id, cc_group_off)` / `_detach_cc(pcd, hw_port_id)` — per-port graft of the port's live scheme to a CC group table at `cc_group_off` (attach) / restore (detach). Port-scoped, does not create a new scheme (avoids the KG-priority race).

**[SPEC]** `fman_pcd_kg_port_attach_policer(pcd, hw_port_id, profile_id)` / `_detach_policer` — reprogram the port's existing RSS scheme to next-engine = PLCR (MODE `0x80500002` → `0xc04c0000`) in place, preserving hashing/match-vector/base-FQID so a policer miss still spreads over the hashed RX FQs. `profile_id` (`u8`, confirmed in patch `0097`) selects the FMPL profile (§9). This is the ingress-policer steering path.

**[SPEC]** `fman_pcd_kg_port_arm_fe(pcd, hw_port_id, fe_enter_off, saved_engine)` — arm the FE-VM path on a port: point the port's CC base at the FE_ENTER root AD at `fe_enter_off`, saving the prior next-engine byte into `*saved_engine` for reversal. `_disarm_fe(pcd, hw_port_id, saved_engine)` restores it. Underpins `fman_pcd_fe_engage()` (§10).

---

## 7. Group D — Coarse Classification (CC)

Two tiers: consumer wrapper (patch `0106`/`0109`, the `ethtool -N` / `set system offload classify` bridge) and static object API (patch `0098`).

```c
/* consumer wrapper (port-scoped) */
int  fman_cc_tree_install (struct fman *fm, u8 port_id, const struct fman_cc_static_tree *spec);
int  fman_cc_tree_add_key (struct fman *fm, u8 port_id, const struct fman_cc_key *key, u32 *handle);
int  fman_cc_tree_remove_key(struct fman *fm, u8 port_id, u32 handle);
void fman_cc_tree_destroy (struct fman *fm, u8 port_id);
/* object API + helpers (pcd-scoped) */
int  fman_pcd_cc_static_install (struct fman_pcd *pcd, u8 port_id, const struct fman_pcd_cc_hw_spec *hw);
int  fman_pcd_cc_static_get_base(struct fman_pcd *pcd, u8 port_id, fman_muram_off_t *cc_muram_off);
void fman_pcd_cc_static_destroy (struct fman_pcd *pcd, u8 port_id);
fman_muram_off_t fman_pcd_cc_tree_group_table_off(const struct fman_pcd_cc_tree *t);
void fman_pcd_cc_seq_dump(struct fman_pcd *pcd, struct seq_file *m);
```

**[SPEC]** `fman_cc_tree_install(fm, port_id, spec)` — the **productive path on shipping hardware**. Installs a static exact-match tree from `struct fman_cc_static_tree` (≤ `FMAN_CC_MAX_STATIC_KEYS` = 32 keys, ~5 KiB MURAM). Gates on `FMAN_CAP_CC_EXACT_MATCH`. `commit` rebuilds the whole tree (no per-key doorbell without HC).

**[SPEC]** `fman_cc_tree_add_key(fm, port_id, key, handle)` / `_remove_key(fm, port_id, handle)` — dynamic lifecycle. On this board (no HC) they ride an in-tree MURAM rewrite (save-restore). `add_key` returns `*handle` on success; rejects `target_qband >= priv->xsk_max_qbands` with `-ERANGE`, a non-zero `hm_handle` on a build without `FMAN_CAP_HM_NODES` with `-ENOTSUPP`, and returns `-ENOTSUPP` if neither HC nor MURAM-rewrite path exists. Key insertion is an ordered mutation: write `match_table[n]` + `ad_table[n]` (miss-AD slid down), then publish via the `group_table[0]` `numKeys` bump (see §16 CC group-table defect).

**[SPEC]** `fman_cc_tree_destroy(fm, port_id)` — free the whole tree (group table + match/ad tables) and return MURAM.

**[SPEC]** `fman_pcd_cc_static_install(pcd, port_id, hw)` — the fman-side silicon programmer the wrapper delegates to; takes the neutral BE-ready `struct fman_pcd_cc_hw_spec`. `fman_pcd_cc_static_get_base(pcd, port_id, cc_muram_off)` fetches the MURAM byte offset of a port's installed static tree (for grafting via `fman_pcd_kg_port_attach_cc`). `fman_pcd_cc_static_destroy` frees it.

**[SPEC]** `fman_pcd_cc_tree_group_table_off(t)` — the MURAM offset of a tree's group table (for `KGSE_CCBS` graft). `fman_pcd_cc_seq_dump(pcd, m)` — debugfs `cc_tree` renderer.

---

## 8. Group E — Header Manipulation (HM)

Consumer wrapper (patch `0099`/`0101`, the `NETIF_F_HW_VLAN_*` bridge) + object/manip API (patches `0099`, `0119`, `0120`, `0137`).

```c
/* consumer wrapper (port-scoped) */
bool fman_hm_caps_supported(void);
int  fman_hm_node_install (struct fman *fm, u8 port_id, const struct fman_hm_spec *spec, u32 *handle);
int  fman_hm_node_destroy (struct fman *fm, u8 port_id, u32 handle);
int  fman_hm_nexthop_get  (struct fman *fm, u8 port_id, u32 egress_tx_fqid,
                           const u8 *src_mac, const u8 *dst_mac, u32 *handle);
int  fman_hm_nexthop_put  (struct fman *fm, u8 port_id, u32 handle);
/* object API (pcd-scoped) */
int  fman_pcd_hm_install (struct fman_pcd *pcd, u8 port_id, const struct fman_pcd_hm_hw_spec *spec, u32 *handle);
int  fman_pcd_hm_destroy (struct fman_pcd *pcd, u8 port_id, u32 handle);
struct fman_pcd_manip *fman_pcd_manip_create(struct fman_pcd *pcd, enum fman_pcd_manip_type type,
                             const struct fman_pcd_manip_params *params, const char *label);
void fman_pcd_manip_destroy(struct fman_pcd_manip *manip);
struct fman_pcd_manip *fman_pcd_manip_chain_create(struct fman_pcd *pcd,
                             struct fman_pcd_manip * const *manips, u8 n_manips);
void fman_pcd_manip_chain_destroy(struct fman_pcd_manip *chain);
fman_muram_off_t fman_pcd_manip_hmtd_off(const struct fman_pcd_manip *manip);
```

**[SPEC]** `fman_hm_caps_supported()` — true iff `FMAN_CAP_HM_NODES`; the feature bit is advertised in `net_dev->hw_features` only when true, so mainline-ucode boards never expose an `-ENOTSUPP` knob.

**[SPEC]** `fman_hm_node_install(fm, port_id, spec, handle)` — install an ordered HM op-list (`struct fman_hm_spec`, `FMAN_HM_MAX_OPS = 8`) as an HMCT chain in MURAM; returns `*handle`. Ops: `FMAN_HM_OP_VLAN_STRIP`, `VLAN_INSERT`, `MPLS_PUSH`, `MPLS_POP`, `RMV_ETHERNET`, `INSRT_GENERIC`, `IPV4_*` (append-only enum). `_destroy(fm, port_id, handle)` frees it. Chains with a §7 CC node (Parser → CC → HM → QMan).

**[SPEC]** `fman_hm_nexthop_get(fm, port_id, egress_tx_fqid, src_mac, dst_mac, handle)` / `_put(fm, port_id, handle)` — refcounted dedup of an L3-forward nexthop rewrite node (multiple flows sharing the same egress `egress_tx_fqid` + `src_mac`/`dst_mac` rewrite share one HMCT chain). `_get` bumps refcount / builds on first use and returns `*handle`; `_put` drops it (frees on last put). `src_mac`/`dst_mac` are `const u8 *` 6-byte MAC arrays (confirmed in patches `0119`/`0120`).

**[SPEC]** `fman_pcd_hm_install(pcd, port_id, spec, handle)` / `_destroy` — the fman-side programmer the wrapper delegates to (neutral `struct fman_pcd_hm_hw_spec`).

**[SPEC]** `fman_pcd_manip_create(pcd, type, params, label)` — build one HMCT manip object of `enum fman_pcd_manip_type` from `struct fman_pcd_manip_params`; `label` is a debug string. Returns a `struct fman_pcd_manip *`. `fman_pcd_manip_chain_create(pcd, manips, n_manips)` concatenates N source manip HMCTs into one bigger HMCT (clears `HMCD_LAST` on intermediates, sets it on the final word); source manips are NOT consumed (caller keeps them alive). `fman_pcd_manip_hmtd_off(manip)` returns the MURAM offset of the manip's HMTD (for wiring into a CC action). Destroy inverses free MURAM. **Pre-allocate chains at install time — do not churn at runtime (MURAM fragmentation).**

---

## 9. Group F — Policer

Consumer wrapper (patch `0104`, the `tc` matchall bridge) + object API (patch `0100`). srTCM (RFC 2697) / trTCM (RFC 2698).

```c
/* consumer wrapper (port-scoped) */
bool fman_policer_caps_supported(void);
int  fman_policer_install(struct fman *fm, u8 port_id, u8 profile_id, const struct fman_policer_profile *prof);
void fman_policer_destroy(struct fman *fm, u8 port_id, u8 profile_id);
/* object API (pcd-scoped) */
int  fman_pcd_plcr_install(struct fman_pcd *pcd, u8 port_id, u8 profile_id, const struct fman_pcd_plcr_hw_profile *hw);
void fman_pcd_plcr_destroy(struct fman_pcd *pcd, u8 port_id, u8 profile_id);
```

**[SPEC]** `fman_policer_caps_supported()` — true iff `FMAN_CAP_POLICER_TRTCM`. `fman_policer_install(fm, port_id, profile_id, prof)` — install one of 256 FMPL profiles from `struct fman_policer_profile` (`cir_bps`/`cbs_bytes`/`pir_bps`/`pbs_bytes`, color-blind/aware; all rates bits/sec, bursts bytes). Validation: `cir_bps==0` → `-EINVAL`, trTCM `pir_bps<cir_bps` → `-ERANGE`, no cap → `-ENOTSUPP`. Budget 8 per netdev (4 per-qband + 4 per-flow). `_destroy` frees the slot.

**[SPEC]** `fman_pcd_plcr_install(pcd, port_id, profile_id, hw)` — the fman-side FMPL programmer (neutral BE-ready `struct fman_pcd_plcr_hw_profile`), implements RM §8.7.6 rate encoding (`rate = exp<<29 | mant<<13`; clock is MHz — ×1e6 before the `bps·2³¹/clk` division). `_destroy` inverses. **The FMPL block master-enable must be set once (`FMPL_GCR.EN|STEN`) or every profile is inert** (see §16 BUG 3a).

---

## 10. Group G — FE-VM / DDR ehash Flow Offload

Patches `0122`–`0150`. **Settled dispatch topology (2026-07-16, spec v4.0 §6.1):** shipping MISS→kernel delivery is the **AC_CC + CONT_LOOKUP group-table pass-through** (`RCCB → group AD, numKeys=0 → miss-AD → port's kernel-polled PCD FQ`, silicon-proven 7.37 Gbps / 0.16% CPU); the FE-VM chain (`FE_ENTER → EXT_HASH → DDR bucket → MUX/Transition/ENQ`) is **dormant**, reserved for the HIT phase (`numKeys>0` match entry → FE_ENTER). The FE-VM is the HIT executor only — it has **no viable kernel-delivery terminal** (three ENQ-as-MISS variants failed on silicon; EXIT-DEALLOCATE is a drop; see §16.4).

### 10.1 Exported API

```c
int  fman_pcd_fe_engage    (struct fman *fm, u8 hw_port_id);
void fman_pcd_fe_disengage (struct fman *fm, u8 hw_port_id);
int  fman_pcd_fe_flow_add  (struct fman *fm, u8 hw_port_id, const u8 *key, u8 key_size, fman_muram_off_t enq_off);
int  fman_pcd_fe_flow_del  (struct fman *fm, u8 hw_port_id, const u8 *key, u8 key_size);
int  fman_pcd_fe_context_build(void __iomem *ctx, u16 ws_offset, const struct fman_pcd_fe_context_params *p);
```

**[SPEC]** `fman_pcd_fe_engage(fm, hw_port_id)` — arm the offload path on a port, in this mandatory order: (1) build the CONT_LOOKUP group table + miss-AD (RM 8.7.4.1 encoding — `w0=(numKeys<<24)|matchTable`, `w1=adTable`, `w2=0x40000000|((keySize-1)<<24)`, `w3=0`; miss-AD = `{w0=fqid, w1=0, w2=RESULT_CF(0), w3=0}` with the FQID **sourced from the port's kernel-polled RX/PCD range at engage time — never hardcoded** (rule T8)); (2) `ensure_params_page`; (3) graft the port's scheme to AC_CC (`next_engine=3, KGSE_CCBS=0`) and point RCCB at the group table; (4) **if and only if any entry dispatches into the FE-VM**, arm the per-port FE workspace pool (`FmPortSetFESupport` port — pool `tnums×512 B` + management index ring, publish params page `+0x54`, zero `+0x58`). Without step 4, any FE-VM frame carves its workspace at MURAM offset 0 → cumulative corruption (F-072, §16.4).

**[SPEC]** `fman_pcd_fe_disengage(fm, hw_port_id)` — the exact inverse, in the **vendor `FmPortDeleteFESupport` order (rule R10)**: clear params page `+0x54` and free the FE pool **while the params page still exists**, THEN restore the scheme/RCCB, then free the group/AD tables (the historical +36 B/cycle scaffold leak is an R4 violation). Each MURAM object is freed **exactly once by its single owner (rule R11)** — duplicated teardown verbs double-free into `gen_pool` (`BUG at lib/genalloc.c:508`, F-075).

**[SPEC]** `fman_pcd_fe_flow_add(fm, hw_port_id, key, key_size, enq_off)` — insert one flow record into the DDR ehash table (a `struct fman_ddr_region`: `dma_alloc_coherent` bucket array + per-flow records, owned by the FE-hash object and freed with it, rule R7): key bytes at record offset `FMAN_EHASH_FLOW_KEY_OFF` (=8), HIT forwards to the ENQ FE at MURAM offset `enq_off` (an `fman_muram_off_t` — the DDR bus address of the bucket array is a `dma_addr_t` and is NOT interchangeable with it). `key_size` MUST equal the full EKFC-extracted length (truncation drops high-offset fields — see §16, rule T7). `key` bytes MUST be in the silicon's EKFC assembly order (MSB-first: SIP→DIP→PROTO→SPORT→DPORT). Record allocation size = `align_up(8 + key_size, 8)` — a fixed 16 B stride over-reads DDR past the entry boundary and stalls the BMI port on the first frame (F-063). `_flow_del` removes by key.

**[SPEC]** `fman_pcd_fe_context_build(ctx, ws_offset, p)` — low-level builder that writes an FE context/workspace descriptor into caller-owned iomem `ctx` at `ws_offset` from `struct fman_pcd_fe_context_params`. `ws_offset` is a **workspace-relative** offset (contextOffsetInWS), a `u16` — deliberately NOT an `fman_muram_off_t`, because it indexes within the caller's `ctx` buffer, not the MURAM gen_pool. Internal helper used by engage.

### 10.2 FE-VM debugfs Bring-Up Surface (deliberately NOT exported)

**[SPEC]** The step-wise FE-VM builders are driven through debugfs nodes under `/sys/kernel/debug/fman_pcd/<N>/`, not exported symbols — they exist for silicon bring-up and the `vyos-offload-ask` script, and MUST NOT grow kernel callers (the exported `fe_engage`/`fe_disengage` compose them):

| Node | Verbs | Builds |
|---|---|---|
| `fe_pool` | `get` / `put` | 16×28 B FE object pool (refcounted) |
| `fe_singletons` | `build` / `clear` | MUX + Transition + EXIT singletons |
| `fe_ehash` | `set <mask> <keysize> <shift>` / `clear` | DDR bucket array (`dma_alloc_coherent`) |
| `fe_hashfe` | `build` / `clear` | `t_ExtHashFe` 7-word descriptor (§7.2 layout) |
| `fe_enq` | `build <fqid>` / `clear` | ENQ FE |
| `fe_enter` | `build` / `clear` | FE_ENTER root AD |
| `fe_flow` | `add <tbl> <key> <enq_off>` / `del` / `clear` | DDR flow records |
| `fe_arm` | `engage <port> <off>` / `disengage <port>` | scheme graft + RCCB |
| `fe_port` | `set <port>` / `del <port>` | **`FmPortSetFESupport`** workspace pool (+0x54/+0x58) |

**[SPEC]** Sequencing constraints (hardware-proven): `fe_port set` runs **after** `fe_arm engage` (the params page is created during engage; a pre-engage `set` silently arms nothing); teardown runs `fe_port del` **before** `fe_arm disengage` (rule R10); after `fe_arm disengage`, NO further `clear` verbs (the disengage already freed those objects — rule R11 / F-075).

**[BUG] F-076 — port RX deaf after FE-VM-armed disengage (OPEN).** Symptom: after any engage→disengage cycle with the FE pool armed, port RX stays at zero despite hardware-clean state (schemes=RSS, RCCB=0, `pcd-snapshot` clean, SFP link UP); `fe_arm.engaged` software state also stays YES, blocking re-engage. Cause: suspected incomplete KG-scheme restoration in `detach_cc` for 10G ports + a missing software-state sync in `disarm_fe`. Fix: cold boot recovers; root-cause pass required before the FE-VM-armed path can claim C1 idempotent teardown. (The CONT_LOOKUP pass-through disengage — no FE pool — is clean.)

---

## 11. Group H — Offload Orchestration

Patch `0129`. Single top-level entry composing KeyGen-arm + CC + HM + Policer + FE-VM.

```c
int  fman_pcd_offload_engage   (struct fman *fman, u8 hw_port_id);
void fman_pcd_offload_disengage(struct fman *fman, u8 hw_port_id);
```

**[SPEC]** `fman_pcd_offload_engage(fman, hw_port_id)` — full port-level engage: build/arm the complete PCD pipeline on one port in one call, per the settled topology (§10: CONT_LOOKUP pass-through for MISS→kernel; FE-VM armed only when HIT entries exist, with the workspace pool per §10.2). `_disengage` is the exact inverse (rules R10/R11 ordering) and MUST leave MURAM `used` at its pre-engage baseline (the reversibility contract; verified by `pcd-snapshot`). Engaging a port has silicon-global side effects on that port's RX pipeline — never call on the management port (eth0) of a live session (documented to break inter-port routing / SSH).

---

## 12. Group I — FMan Port Primitives

Patches `0102`, `0105`, `0116`, `0123`, `0136`. Low-level per-port BMI/params helpers the higher layers compose.

```c
int  fman_port_set_cc_base(struct fman_port *port, fman_muram_off_t cc_muram_off);
int  fman_port_set_rx_bpool(struct fman_port *port, u8 old_bpid, u8 new_bpid);
int  fman_port_set_params_page(struct fman_port *port, fman_muram_off_t muram_off, void __iomem *page);
fman_muram_off_t fman_port_get_params_page(struct fman_port *port);
int  fman_pcd_port_ensure_params_page(struct fman_pcd *pcd, struct fman_port *rxport);
u32  fman_port_get_liodn(struct fman_port *port);
u8   fman_port_get_total_tnums(struct fman_port *port);
struct fman_port *fman_port_lookup_rx(struct fman *fm, u8 port_id);
int  fman_port_set_silicon_hit_release_mode(struct fman_port *port, bool enable);
int  fman_port_set_silicon_hit_release_all(struct fman *fm, bool enable);
```

**[SPEC]** `fman_port_set_cc_base(port, cc_muram_off)` — write `FMBM_RCCB` (RX CC base) to point at a MURAM AD (0 = clear/restore). `fman_port_set_rx_bpool(port, old_bpid, new_bpid)` — hot-swap the RX BMI buffer-pool ID (the AF_XDP true-ZC BPID flip); operates on the persistent `port->ext_buf_pools` table (see §16 kfree-cfg defect).

**[SPEC]** `fman_port_set_params_page(port, muram_off, page)` / `fman_port_get_params_page(port)` — set/get the per-port FM_CTL params page (MURAM offset + iomem pointer). `fman_pcd_port_ensure_params_page(pcd, rxport)` — allocate-and-init the params page for an RX port if absent (idempotent); writes the FE-buffer free-list fields (+0x54/+0x58) for the FE-VM path.

**[SPEC]** `fman_port_get_liodn(port)` — the port's LIODN (logical IO device number). `fman_port_get_total_tnums(port)` — committed + extra task (tnum) count; bounds the drain loop in port quiesce/recovery. `fman_port_lookup_rx(fm, port_id)` — resolve a `struct fman_port *` for an RX BMI port id. The as-built code types the first argument `void *fm`; the mandated signature is `struct fman *fm` (rule T1 — no `void *` in a typed API), a one-line conformance fix. 

**[SPEC]** `fman_port_set_silicon_hit_release_mode(port, enable)` / `_all(fm, enable)` — control silicon release of internal FE buffers on HIT for one port / all ports. Relevant to internal-FE-buffer lifetime and the recovery path (§13).

---

## 13. Group J — Port Recovery (PLANNED)

Specified in `specs/dpaa1-afxdp-modernization-spec.md` §5.9 (milestone M3-3f). **Design-only — not yet implemented.**

```c
int fman_port_recover(struct fman *fm, u8 port_id);
```

**[SPEC]** `fman_port_recover(fm, port_id)` — best-effort, **port-scoped** software de-wedge for a stalled/deaf RX port, to avoid a full cold boot. Escalates Tier 0 (clean re-teardown) → Tier 1 (per-port BMI quiesce + internal-FE-buffer free-list rebuild + zero `internalFEBufferDepletionCounter` +0x58), verifying by readback. Returns `0` (recovered + verified), `-EAGAIN` (software recovery insufficient for this corruption class → POR/cold-boot required, port left safely disabled), `-EBUSY` (tnums did not drain), `-ENODEV`/`-EINVAL`. MUST NOT be invoked on eth0 of a live session. The all-ports Tier-2 FMan FPM soft reset is a separate, flagged, serial-console-only entry, NOT reachable through this port-scoped function in v1.

**[NOTE]** Composes existing exports (`fman_port_lookup_rx`, `fman_port_set_cc_base`, `fman_port_set_silicon_hit_release_all/_mode`, `fman_port_get_total_tnums`, `fman_pcd_port_ensure_params_page`, `fman_pcd_offload_disengage`) — no new low-level MURAM writer beyond a BMI enable/quiesce helper.

**[?]** Whether any software reset short of POR clears the deepest BMI/MURAM corruption is UNVERIFIED — the standing "warm reboot does not clear BMI/MURAM state" rule implies the FPM soft reset may be insufficient (erratum A007273 sequencing; MURAM/FIFO not cleared). Success must be measured, never assumed.

---

## 14. Group K — Introspection, Bridge & Ancillary Exports

### 14.1 Introspection / debugfs (patches `0107`, `0113`)

```c
void fman_pcd_cc_test_debugfs_init(struct dentry *parent, struct fman_pcd *pcd);
void fman_pcd_dcsr_debugfs_init   (struct dentry *parent, struct fman_pcd *pcd);
```

**[SPEC]** `fman_pcd_cc_test_debugfs_init(parent, pcd)` — install the CC test/bring-up debugfs harness under `parent`. `fman_pcd_dcsr_debugfs_init(parent, pcd)` — install the FMan DCSR error-window taps (`/sys/kernel/debug/fman_pcd/<N>/dcsr/{fpm,bmi,qmi,parser,kg,pol}_err`); read-only `ioread32be` sweeps, rate-limited ≥ 1 ms, never ack a W1C register. `fpm_err` decodes the 50 per-hwport FPM status words with `[STALLED]` — the first-stop forensic view for BMI/CC dispatch wedges.

### 14.2 DPAA Bridge & Flavor/Flow-Offload Registration

```c
u32  dpaa_fman_get_caps(struct net_device *net_dev);                 /* [?] arg */
void dpaa_fman_caps_log(struct net_device *net_dev);                 /* [?] arg */
struct fman_port *dpaa_get_rx_fman_port(struct net_device *net_dev); /* [?] */
u32  dpaa_get_tx_fqid(struct net_device *net_dev);                   /* [?] */
int  dpaa_register_flavor_ops  (const struct dpaa_flavor_ops *ops);  /* [?] */
void dpaa_unregister_flavor_ops(const struct dpaa_flavor_ops *ops);  /* [?] */
int  dpaa_register_flow_offload_handler  (/* handler */);            /* [?] */
void dpaa_unregister_flow_offload_handler(/* handler */);            /* [?] */
```

**[SPEC]** The consumer-side bridge `ask.ko` (and any future flavor module) uses: `dpaa_fman_get_caps`/`dpaa_fman_caps_log` (capability query/report per §3), `dpaa_get_rx_fman_port` (netdev → RX `struct fman_port *` resolution — the §12 primitives all need it), `dpaa_get_tx_fqid` (the port's kernel TX FQID for egress targeting — see rule T8 on FQ direction), and the paired RCU registration surfaces `dpaa_register/unregister_flavor_ops` (datapath ops table) and `dpaa_register/unregister_flow_offload_handler` (`nf_flow_table`/`TC_SETUP_FT` delivery into the offload module). Registration pairs obey rule C1 (unregister is NULL-safe and idempotent). **[?]** Signatures reconstructed from call sites — verify each against the header before use.

### 14.3 CAAM QI Share (patch `0134`, dormant)

```c
int  caam_qi_ext_consumer_register(/* dev, ops */);   /* [?] full signature */
void caam_qi_ext_consumer_release (/* handle */);     /* [?] */
```

**[SPEC]** The SEC 5.4 QI (QMan-interface) descriptor-sharing surface for the M5 HW-IPsec milestone: an external consumer (`ask_xfrm.c`/`ask_caam.c`) registers to share CAAM's QI frame queues for FMan-targeted crypto dequeue without core involvement. Compiled, exported, and **dormant — never exercised on silicon**; treat as bring-up-grade (rule C2 readback discipline applies doubly). Register/release form a C1 pair. **[?]** Full signatures not yet captured — verify against the patch before first use.

### 14.4 QMan-CEETM Shaper (cross-reference)

**[SPEC]** The egress-QoS control plane (~40 `qman_ceetm_*` exports + `dpaa_ceetm_supported/qdisc_install/qdisc_destroy`, patches `0111`/`0112`, the `tc htb offload` consumer) is QMan API, not FMan PCD — authoritative reference [`arch/qman-ceetm.md`](qman-ceetm.md). Listed here solely so this document's completeness audit covers every offload control-plane surface on the SoC. The same R/T/C rules of §16 bind CEETM wrappers (claim/release pairs, typed handles, no raw channel integers across module boundaries).

### 14.5 Reserved Capability Groups (silicon present, no API — planned)

**[SPEC]** Per the §3.1 audit, four 210.10.1 silicon functions have no API group yet. Reservations so future patches land coherently:

- **Soft Parser** (`FMAN_CAP_PARSER_SOFTSEQ`, BIT(4) — cap SET on shipping ucode, no kernel API). The production reference programs it via FMC NetPDL (`cdx_sp.xml`, 194 lines: PPPoE ccbase-slide, TTL/hop-limit punt, SYN/FIN/RST punt, 6in4 dispatch). A future `fman_pcd_prs_*` group MUST load the compiled soft-sequence into the 1984-byte instruction space, attach per port, and carry the inverse (detach + instruction-space free). Required for PPPoE WAN offload — today every PPPoE frame is a guaranteed classification MISS.
- **Frame Replicator** (BIT(8) reserved; `struct fman_pcd_replic_group` handle + `replic` teardown list + KUnit tests already exist). Planned `fman_pcd_replic_group_create/destroy` + member add/remove (source-TD + member-AD chain → multiple egress FQs). P1 after M4 (multicast offload).
- **IP Reassembly** (BIT(6) reserved; params page `+0x10 iprIpv4Nia` / `+0x14 iprIpv6Nia` fields exist, currently zero). P0 after M3 — without it, every fragmented frame bypasses HW offload (structural DoS vector).
- **IP Fragmentation** (BIT(7) reserved; params page `+0x30 ipfOptionsCounter`). P2 — egress-side completeness.

**[SPEC]** Each future group MUST arrive with: its cap-bit claim (§3), typed handles (T1/T2), full C1 inverse including params-page field zeroing at teardown, C2 readback, an entry in `fman_pcd_release()`'s walk (R3), and a §3.1 audit-table update — all in the same patch series.

---

## 15. Public Structs & Enums (Appendix)

**[SPEC]** `struct fman_pcd_muram_budget { size_t reserved_bytes, used_bytes, free_bytes, high_water_bytes; }` — returned by value from `fman_pcd_get_muram_budget()`.

**[SPEC]** `struct fman_cc_key` (patch `0086b`) — 5-tuple match key: `ethertype` (`FMAN_CC_ETHERTYPE_{ANY,IPV4,IPV6}`), `proto` (`FMAN_CC_PROTO_{ANY,TCP,UDP}`), `is_ipv6`, v4 `src_ip`/`dst_ip` + `src_ip_mask`/`dst_ip_mask`, v6 `src_ip6[16]`/`dst_ip6[16]`, `src_port`/`dst_port`, `target_qband`, `hm_handle` (0 = none). A field participates in the match iff it (or its mask) is non-zero; all multi-byte fields are host-endian at the API, converted to FMan big-endian in the install body.

**[SPEC]** `struct fman_cc_static_tree { u16 num_keys; u16 miss_qband; struct fman_cc_key keys[FMAN_CC_MAX_STATIC_KEYS]; }`, `FMAN_CC_MAX_STATIC_KEYS = 32`.

**[SPEC]** `struct fman_hm_spec` — ordered op-list, `FMAN_HM_MAX_OPS = 8`; ops enum `FMAN_HM_OP_{VLAN_STRIP,VLAN_INSERT,MPLS_PUSH,MPLS_POP,RMV_ETHERNET,INSRT_GENERIC,IPV4_*}` (append-only). `enum fman_pcd_manip_type` + `struct fman_pcd_manip_params` drive `fman_pcd_manip_create()`.

**[SPEC]** `struct fman_policer_profile` — srTCM/trTCM: `cir_bps`, `cbs_bytes`, `pir_bps`, `pbs_bytes`, color-blind/aware. Neutral HW forms: `struct fman_pcd_cc_hw_spec`, `struct fman_pcd_hm_hw_spec`, `struct fman_pcd_plcr_hw_profile`, `struct fman_pcd_fe_context_params`, `struct fman_pcd_kg_scheme_params` (opaque BE-ready translation targets published in `include/linux/fsl/fman_pcd.h`).

**[SPEC]** **Address types (mandatory, §2, §16 T2).** MURAM offsets and DDR bus addresses are distinct strong types that the compiler/sparse refuse to cross:
```c
typedef u32 __bitwise fman_muram_off_t;          /* MURAM byte offset token */
#define FMAN_MURAM_OFF_INVAL ((__force fman_muram_off_t)~0u)
struct fman_muram_region { fman_muram_off_t off; void __iomem *vaddr; size_t size; };  /* owned MURAM alloc */
struct fman_ddr_region   { void *cpu; dma_addr_t dma; size_t size; struct device *dev; }; /* owned DDR coherent alloc */
```
Every function parameter or return that names a MURAM location uses `fman_muram_off_t` (never `u32`/`unsigned long`); every DDR location uses `dma_addr_t` (carried inside a `struct fman_ddr_region` for owned allocations). The pair `{off, size}` / `{dma, size}` is what makes rules C3 (bounds-checked MURAM writes) and R7 (DDR alloc/free pairing) enforceable. Workspace-relative offsets (e.g. `fman_pcd_fe_context_build`'s `ws_offset`) are plain `u16` and are deliberately NOT `fman_muram_off_t`.

**[SPEC]** Opaque handles (defined in their `.c` files): `struct fman_pcd`, `fman_pcd_kg_scheme`, `fman_pcd_cc_tree`, `fman_pcd_cc_node`, `fman_pcd_manip`, `fman_pcd_plcr_profile`, `fman_pcd_replic_group`. ABI guard: `FMAN_PCD_API_VERSION` (currently `1`).

**[NOTE]** **As-built vs mandated (conformance status).** The current header (`0092`/`0097`/…) still types MURAM offsets as raw `u32`/`unsigned long` and `fman_port_lookup_rx`'s first argument as `void *`. The signatures in this document are the **mandated** target under §16 T1/T2; converting the header to `fman_muram_off_t` / `struct fman_muram_region` / `struct fman_ddr_region` / `struct fman *` is a required, sparse-verifiable refactor (`make C=2` must be clean afterward). Until it lands, this doc's typed signatures lead the code.

---

## 16. Defensive Coding Requirements & Defect Notes

**[SPEC]** The requirements in §16.1–§16.3 are **binding on every patch** that adds or modifies an FMan PCD wrapper. A reviewer MUST reject a patch that violates them. Each requirement is tagged `R#` (memory-leak), `T#` (typing), or `C#` (completeness) and traces to a real defect or hardware finding. §16.4 is the current defect register.

### 16.1 Memory-leak prevention (MURAM, DDR, list, and internal hardware buffers)

**[SPEC]** **R1 — single allocator, symmetric free on ALL paths.** MURAM is allocated only through `fman_pcd_muram_alloc()` and freed only through `fman_pcd_muram_free()`. Every successful alloc MUST have a matching free reachable on **both** the success teardown path and **every** error path of the same function. No raw `gen_pool_*`/`fman_muram_*` calls from wrapper bodies.

**[SPEC]** **R2 — error-path unwind, reverse order.** Constructors use a `goto` ladder that frees in reverse allocation order and returns `ERR_PTR(-errno)`; no early `return` after an allocation without unwinding it. A constructor that allocates N objects and fails at step k frees exactly k−1, never 0 and never N.

**[SPEC]** **R3 — list/devm ownership so `fman_pcd_release()` reclaims everything.** Every created object is inserted into its per-engine list (`kg/cc/manip/plcr/replic`) and freed by `fman_pcd_release()`. A new object type MUST be added to `release()`'s teardown walk in the same patch that introduces it. Root-level allocations use `devm_*` so probe-failure unwinds.

**[SPEC]** **R4 — budget invariant.** After a full `fman_pcd_offload_disengage()` / object-destroy cycle, `fman_pcd_get_muram_budget().used_bytes` MUST return to its pre-operation baseline (`used == 0` at full teardown). `high_water_bytes` is the standing leak detector; the `pcd-snapshot` gate asserts `used == 0` after disengage. A patch that raises the steady-state `used` after a create/destroy round-trip is a leak and is rejected.

**[SPEC]** **R5 — publish last.** A handle is made reachable (inserted into a list, or exposed via a returned pointer, or activated by the silicon) ONLY after it is fully built. Mirrors the hardware discipline where a CC key becomes live only on the final `group_table[0].numKeys` bump. No partially-constructed object is ever observable.

**[SPEC]** **R6 — every `struct list_head` gets `INIT_LIST_HEAD()`.** A new list field in `struct fman_pcd` MUST get a matching `INIT_LIST_HEAD()` in `fman_pcd_init()` in the same patch. Grep-parity check: `struct list_head` field count == `INIT_LIST_HEAD(&pcd->…)` count. (Origin: the `0060`/`0061` probe panic.)

**[SPEC]** **R7 — DDR coherent buffers paired inside the owning object.** Each `dma_alloc_coherent()` (ehash bucket array, per-flow records, miss context) is owned by exactly one object and freed by that object's destroy path with `dma_free_coherent()`. DDR bus addresses never leak into MURAM-offset fields (see T2).

**[SPEC]** **R8 — hardware-buffer leaks count as memory leaks.** The per-port internal FE-buffer free-list is a finite MURAM resource tracked by `internalFEBufferDepletionCounter` (+0x58). Any engage that arms the FE path MUST guarantee its MISS terminal carries DEALLOCATE and MUST zero +0x58 at disengage. Leaking these is the documented "port deaf after disengage" fault (§16.4) and is a leak, not a transient.

**[SPEC]** **R9 — never dereference freed init state.** `fman_port_init()` ends with `kfree(port->cfg)`; wrapper bodies MUST operate on persistent `struct fman_port` fields (e.g. `port->ext_buf_pools`), never `port->cfg->…`, after init. (Origin: the `0102` `set_rx_bpool` `-EINVAL`.)

**[SPEC]** **R10 — ordered teardown per the vendor inverse.** Multi-object teardown follows the vendor's documented order, not convenience order. For the FE workspace pool (`FmPortDeleteFESupport`): clear params page `+0x54` and free the pool **while the params page still exists**, THEN detach the port PCD (scheme/RCCB restore), then free group/AD tables. Inverting this writes to freed MURAM — a NULL page pointer resolves to MURAM offset 0, the exact garbage-write pattern of F-072. (Origin: the F-074 disengage crash, hardware-proven 2026-07-15.)

**[SPEC]** **R11 — single-owner teardown, no duplicated free verbs.** Every MURAM/DDR object has exactly ONE owner responsible for freeing it. A composite teardown (e.g. `fe_arm disengage` → `detach_cc`) that already frees child objects means subsequent per-object `clear`/`free` calls are double-frees — `gen_pool_free_owner` `BUG at lib/genalloc.c:508`. Script- or debugfs-driven teardown sequences MUST NOT repeat frees the composite verb already performed; the composite verb's documentation MUST list exactly what it frees. (Origin: F-075, crashed on the 3rd engage/disengage cycle.)

### 16.2 Strong typing

**[SPEC]** **T1 — opaque typed handles, never `void *`.** Every object is a distinct incomplete `struct fman_pcd_*` pointer. The NXP SDK `handle_t`/`void *` ABI is banned. Cross-engine references pass typed pointers, not integers or `void *`.

**[SPEC]** **T2 — MURAM offsets and DDR bus addresses are distinct strong types.** They are distinct address spaces (§2). MURAM locations MUST be `fman_muram_off_t` (`typedef u32 __bitwise`; §2/§15), tested with `fman_muram_off_valid()` / `FMAN_MURAM_OFF_INVAL`, never `== 0`/`< 0`/`IS_ERR_VALUE`. DDR locations MUST be `dma_addr_t`, carried in a `struct fman_ddr_region` for owned allocations. The `__bitwise` tag makes any assignment between the two (or between either and a raw integer) a **sparse error**; `make C=2` MUST be clean. Raw bits are extracted only at the final big-endian descriptor write, via `fman_muram_off_raw()` (MURAM) or `lower_32_bits()`/`upper_16_bits()` (DDR) — never by implicit conversion. A single function MUST NOT reuse one variable for both an offset and a bus address. (Origin: the EXT_HASH `w2/w3` DDR vs `w5/w6` MURAM split and the F-069 `w4 = 0x55300` MURAM/EXIT-descriptor confusion — both would be compile errors under this rule.)

**[SPEC]** **T3 — sparse annotations mandatory.** All MMIO pointers are `void __iomem *`; all big-endian register/MURAM fields are `__be32`/`__be16` and converted with `cpu_to_be32`/`be32_to_cpu` (never bare casts). New/modified files MUST build `make C=2` (sparse) with zero new warnings.

**[SPEC]** **T4 — `ERR_PTR` discipline.** Pointer-returning constructors return `ERR_PTR(-errno)` on failure and never a bare `NULL` to mean error; callers use `IS_ERR()`/`PTR_ERR()`. `int`-returning calls return `0` / `-errno`. A `NULL` return is reserved for genuinely-absent optional state (e.g. `fman_get_pcd()` on a degraded boot) and is documented per-function.

**[SPEC]** **T5 — enum discriminators with exhaustive `default`.** Selectors use the published enums (`enum fman_pcd_manip_type`, `FMAN_HM_OP_*`, `FMAN_CC_PROTO_*`, `FMAN_CC_ETHERTYPE_*`), never magic ints. Every `switch` over such an enum MUST have a `default:` returning `-EOPNOTSUPP` (origin: the KG extract-slot whitelist silently accepting `IPSEC_SPI` offset 32 → `-EOPNOTSUPP` only by luck).

**[SPEC]** **T6 — const-correctness.** All input spec/params structs are passed `const *` (`const struct fman_cc_key *`, `const struct fman_hm_spec *`, …); handles that are mutated are non-const. A wrapper MUST NOT write through a `const` spec pointer.

**[SPEC]** **T7 — bounded arrays and range-checked scalars.** Fixed-capacity tables carry an explicit cap (`FMAN_CC_MAX_STATIC_KEYS = 32`, `FMAN_HM_MAX_OPS = 8`) and every index is checked against it (`>= cap → -ERANGE`). Scalar hardware selectors are range-checked at entry: `port_id` is a BMI id validated against the real map (10G RX ports are `0x10`/`0x11`, NOT `> 10`), `profile_id < 256`, `target_qband < priv->xsk_max_qbands`. `key_size` for a flow insert MUST equal the full EKFC-extracted length — a shorter value silently truncates high-offset fields and is a checked error, not a "coarser match."

**[SPEC]** **T8 — FQID direction and ownership validated at the API boundary.** A FQID written into any delivery descriptor (miss-AD, ENQ FE, CC enqueue-AD) MUST be resolved at engage/install time from the authoritative source for its direction — kernel-RX delivery uses the port's kernel-polled Rx-default/PCD FQ (via the `fqids` resolution path or `dpaa_get_rx_default_fqid`); wire-TX delivery uses `dpaa_get_tx_fqid` or a dedicated offload TX FQ. Literal FQID constants in wrapper bodies are a reviewer-blocking error. A kernel-delivery descriptor pointed at a TX-channel FQ (or any FQ outside the kernel's polled ranges) silently blackholes frames — no error, no counter. (Origin: the `0x2b9`-as-miss-target and hardcoded-`0x200`-on-eth4 blackholes, hardware-proven 2026-07-10/16.)

### 16.3 Completeness & symmetry audit

**[SPEC]** **C1 — every lifecycle verb has its inverse, and teardown is idempotent.** Constructor↔destructor, attach↔detach, engage↔disengage, get↔put MUST both exist; the inverse MUST be safe to call on an already-torn-down / never-armed object (NULL-safe, double-free-safe). Query/accessor functions (`*_get_base`, `*_group_table_off`, `*_caps_supported`, `*_get_*`, `*_seq_dump`) are read-only and need no inverse.

**[SPEC]** **C2 — readback verification of every un-erroring silicon write.** MMIO/indirect-AR writes have no synchronous error return (GO-clear means "consumed," not "correct"). After programming a KGSE entry, an FE descriptor, a CC AD, or an FMPL profile, the body MUST read it back and compare, failing the call with `-EIO` on mismatch. This SHOULD be baked into the low-level primitive so it cannot be forgotten; a build that cannot verify its own key layout MUST refuse to engage (`-EPROTO`).

**[SPEC]** **C3 — never write a MURAM offset you do not own.** Every MURAM write targets an address returned by `fman_pcd_muram_alloc()` for this object, at an offset `< size`. Bounds are asserted, not assumed. (Origin: the scaffold MURAM corruption / `ecir.fqid=0x0` storms.)

**[SPEC]** **Symmetry matrix (current API).** Verified pairs and the three completeness gaps to close:

| Verb | Inverse | Status |
|---|---|---|
| `fman_pcd_init` | `fman_pcd_release` | ✅ (devm) |
| `fman_pcd_muram_alloc` | `fman_pcd_muram_free` | ✅ |
| `fman_pcd_kg_scheme_create` | `fman_pcd_kg_scheme_destroy` | ✅ |
| `fman_pcd_kg_port_attach_cc` | `fman_pcd_kg_port_detach_cc` | ✅ |
| `fman_pcd_kg_port_attach_policer` | `fman_pcd_kg_port_detach_policer` | ✅ |
| `fman_pcd_kg_port_arm_fe` | `fman_pcd_kg_port_disarm_fe` | ✅ |
| `fman_cc_tree_install` / `add_key` | `fman_cc_tree_destroy` / `remove_key` | ✅ |
| `fman_pcd_cc_static_install` | `fman_pcd_cc_static_destroy` | ✅ |
| `fman_hm_node_install` | `fman_hm_node_destroy` | ✅ |
| `fman_hm_nexthop_get` | `fman_hm_nexthop_put` | ✅ (refcounted) |
| `fman_pcd_hm_install` | `fman_pcd_hm_destroy` | ✅ |
| `fman_pcd_manip_create` / `chain_create` | `fman_pcd_manip_destroy` / `chain_destroy` | ✅ |
| `fman_policer_install` / `fman_pcd_plcr_install` | `fman_policer_destroy` / `fman_pcd_plcr_destroy` | ✅ |
| `fman_pcd_fe_engage` / `fe_flow_add` | `fman_pcd_fe_disengage` / `fe_flow_del` | ✅ |
| `fman_pcd_offload_engage` | `fman_pcd_offload_disengage` | ✅ |
| `dpaa_register_flavor_ops` | `dpaa_unregister_flavor_ops` | ✅ |
| `dpaa_register_flow_offload_handler` | `dpaa_unregister_flow_offload_handler` | ✅ |
| `caam_qi_ext_consumer_register` | `caam_qi_ext_consumer_release` | ✅ (dormant — unexercised) |
| debugfs `fe_port set` | debugfs `fe_port del` | ✅ (order-sensitive, R10) |
| `fman_port_set_cc_base(off)` / `set_rx_bpool(a,b)` / `set_silicon_hit_release_mode(en)` | self-inverse via argument (0 / reversed / false) | ✅ (must roll back on failure) |
| `fman_pcd_kg_bind_port` | — | ⚠️ **GAP C1a**: no explicit unbind; reversal only via `scheme_destroy`. Add `fman_pcd_kg_unbind_port()` or document `scheme_destroy` as the sole inverse. |
| `fman_pcd_kg_attach_cc` (object-level) | — | ⚠️ **GAP C1b**: no object-level `detach_cc`; reversal via `scheme_destroy` or the port-level `detach_cc`. Add the symmetric inverse or document. |
| `fman_pcd_fe_context_build` | — | ✅ by design: writes into caller-owned `__iomem` workspace whose lifetime is owned by the enclosing FE object; documented, no separate destroy. |

**[NOTE]** GAP C1a/C1b are the only asymmetries in the surface. Both are *safe today* (the destroy path does reverse them) but violate C1's "explicit inverse" rule; closing them removes a class of "I unbound the port but the scheme still holds a stale CCBS" reviewer question. Track as follow-up API additions, not blockers.

### 16.4 Defect register

**[BUG] MURAM reservation ENOMEM at init.** Symptom: `fman_pcd: cannot reserve N bytes MURAM (err -12)` → PCD stays NULL, all `fman_pcd_*` calls fail. Cause: the reservation exceeded the free window after mainline CAM+FIFO carveouts (96 KiB was too aggressive). Fix: `FMAN_PCD_MURAM_RESERVED_BYTES` tuned to 64 KiB (confirmed live: reserved 65536 / used 0). See R6 for the paired `INIT_LIST_HEAD` panic in the same area.

**[BUG] CC group-table miss-AD not wired → all frames dropped.** Symptom: 256 flows covering every 1-byte key match zero packets. Cause: `cc_node_create()` wrote match/ad tables but left `group_table[0]` all-zero → decoded as `RESULT_CF fqid=0` (reserved-invalid). Fix (patch `0050`): encode miss_action into `ad_table[0]` at create, write `group_table[0]` with a CONT_LOOKUP AD, and on `add_key` slide the miss-AD down before bumping `numKeys` (the R5 publish-last write).

**[BUG] FMPL policer inert at boot (BUG 3a).** Symptom: policed traffic 100% dropped, all green/yellow/red counters frozen. Cause: `FMPL_GCR.EN|STEN` clear at boot (`0x00500002`) → whole policer block disabled → KeyGen-routed frames drop pre-meter. Fix (patch `0100` `plcr_enable_block()`): RMW `GCR |= EN|STEN` → `0xC0500002`; HW-validated 100% → 0% loss. A C2 readback of `GCR` would have caught the inert state at install time.

**[BUG] set_rx_bpool -EINVAL on a live port.** Symptom: `fman_port_set_rx_bpool()` returns -22 at ZC attach. Cause: `fman_port_init()` ends with `kfree(port->cfg)` → the v1 body dereferenced the freed `port->cfg->ext_buf_pools` (an R9 violation). Fix (patch `0102` v2): operate on the persistent `port->ext_buf_pools` table and call the static `set_bpools()` directly.

**[BUG] Port deaf after disengage (internal-FE-buffer leak — SUPERSEDED root cause).** Symptom: RX silent after an engage/disengage cycle until cold boot. Original theory: EXIT built without DEALLOCATE leaks a 256-byte internal FE buffer per MISS frame (an R8 violation). **Superseded 2026-07-15 by F-072 (below):** the dominant corruption source was the never-armed workspace pool, not EXIT flags. The R8 discipline (MISS terminal carries DEALLOCATE inside the FE-VM; zero `+0x58` at disengage) remains binding. Residual post-F-072 deafness is tracked as F-076 (§10.2). Warm reboot does NOT clear this — cold boot does.

**[BUG] F-072 — FE workspace pool never armed (params page `+0x54` = 0).** Symptom: every FE-VM configuration ever tested corrupted MURAM under traffic — BMI stall, port deafness, disengage crash, kernel OOPS in `build_skb` (corrupt BMan pointer). Cause: `FmPortSetFESupport` (per-port FE internal buffer pool + management index ring + params-page `+0x54` publication) was never ported; with `+0x54` = 0 the microcode does RMW bookkeeping at MURAM offset 0 and carves per-frame workspaces at garbage offsets. Every pre-F-072 FE-VM delivery result is void. Fix: patch `0123` `fe_port` support, armed post-engage; Gate A proven 2026-07-15 (pool `0x54400`/8448 B, 600-frame MISS flood, zero corruption, first clean disengage in program history).

**[BUG] F-074 — inverted FE teardown order crashes.** Symptom: disengage with the pool armed crashed the board. Cause: `fe_arm disengage` (frees the params page) ran BEFORE `fe_port del` (which then wrote `+0x54` into freed MURAM / offset 0). Fix: teardown order per vendor `FmPortDeleteFESupport` — `fe_port del` first (rule R10).

**[BUG] F-075 — duplicated teardown verbs double-free (`genalloc.c:508 BUG`).** Symptom: 3rd engage/disengage cycle panics in `gen_pool_free_owner` (`x19` = FE_ENTER offset). Cause: script-level `fe_enter clear` (and sibling clears) after `fe_arm disengage`, which had already freed those objects via `detach_cc`. Fix: disengage sequence stripped to `fe_port del` + `fe_arm disengage` only (rule R11).

**[BUG] ENQ FE is not a MISS→kernel terminal (three variants closed).** Symptom: zero sustained kernel delivery from `missNextFE → ENQ` despite vendor-correct encodings; one variant OOPSed the kernel (`build_skb` on a corrupted BMan pointer), one exhausted BMI FIFO to watchdog reset. Cause: architectural — the FE-VM has no kernel-delivery terminal; with `ws_offset` set the ENQ reads its FQID from the MURAM FE workspace (microcode-populated), not from any CPU-writable DDR context; EXIT-DEALLOCATE is a drop; EXIT-without-DEALLOCATE strands the frame (no scheme-NIA fallback in AC_CC mode). Fix: settled topology (spec v4.0 §6.1) — MISS→kernel is the CC-layer miss-AD's job; the FE-VM serves HIT only. Do not re-attempt ENQ-as-missNextFE.

**[BUG] CONT_LOOKUP group AD `w1=0` / miss-AD `w2=NIA` regression.** Symptom: pass-through scaffold blackholes all frames (works at 7.37 Gbps with correct bytes). Cause: a re-implementation dropped the AD-table pointer from group `w1` (CC engine then resolves the miss-AD at MURAM offset 0) and injected a NIA (`0x00500002`) into the enqueue-AD's `w2`, which must be `RESULT_CF (0x00000000)` per RM 8.7.4.3. Fix: byte-exact restore of the proven encoding — group `{w0=(numKeys<<24)|mto, w1=ato, w2=0x40000000|((keySize-1)<<24), w3=0}`, enqueue-AD `{w0=fqid, w1=0, w2=0, w3=0}`. A C2 readback-vs-oracle would have caught both at engage time. (Origin: F-079 series, 2026-07-16.)
