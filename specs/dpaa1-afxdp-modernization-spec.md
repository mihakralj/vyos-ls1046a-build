# DPAA1 Driver Modernization for LS1046A (FLAVOR=vpp)

**Target:** `mihakralj/vyos-ls1046a-build` → `specs/dpaa1-modernization-spec.md`
**Kernel base:** Linux 6.18.x mainline (VyOS rolling release)
**Silicon:** NXP LS1046A — Cortex-A72 ×4 (2 clusters of 2 over CCI-400), FMan v3 Rev>1, QMan, BMan, MURAM (384 KiB), PAMU, SEC 5.4 (CAAM), CEETM, SerDes/XFI PCS, CoreNet
**FMan microcode:** package `fsl_fman_ucode_ls1046a_r1.0_210.x.bin` (NXP LSDK, available on the DUT, loaded by U-Boot from SPI `mtd4` at boot)
**Document version:** v4.4, 2026-05-27
**Supersedes:** v4.3, v4.2, v4.1, v4.0, v3.0 (unified vpp+ask2), v0.9, v2.0, v2.1

**What changed in v4.4 (2026-05-27):**

- **M2-stage-1 hardware validation PASS recorded.** Patches `0075a` (validation arms + LIODN accessor), `0075b` (DMA-map + BMan seed + RCU publish), and `0075c` (LIODN gate removal) landed on the `dpaa1` branch and are functionally verified on the live DUT (LS1046A Mono Gateway DK, ISO `vyos-2026.05.27-0151-rolling-LS1046A-default-arm64.iso`, kernel `6.18.33-vyos`). `bind(XDP_ZEROCOPY)` on `eth4` queue 0 with `chunk_size=4096` returns `rc=0` — `af_xdp_pool_xsk_pool_attach` is reached, all five surviving validation arms pass, `xsk_pool_dma_map()` succeeds on dma-direct (PAMU in firmware bypass), `bman_new_pool()` succeeds, the seed loop completes, and `rcu_assign_pointer(priv->xsk_pool[0], pool)` publishes. See `bin/dpaa1-xsk-bind-probe.py` (130-line Python `ctypes` probe, no `libxdp`/`libbpf` dependency) and qdrant memory `dpaa1-af-xdp-zero-copy M2-stage-1 PASS 2026-05-27`.
- **`chunk_size` vs `frame_size` clarified for userspace integrators.** `DPAA1_MIN_UMEM_CHUNK = 3840` is the **kernel-visible** `xsk_pool_get_rx_frame_size()` value, which is what the driver's validation arm compares. Userspace `XDP_UMEM_REG` callers pass the raw `chunk_size`, which the XSK core (`net/xdp/xdp_umem.c`) requires to be a power-of-2 ≤ PAGE_SIZE. On arm64 with a 4 KiB page kernel the **only valid `chunk_size` is 4096**; it yields `frame_size = 4096 − headroom(0) − XDP_PACKET_HEADROOM(256) = 3840`, which exactly equals `DPAA1_MIN_UMEM_CHUNK`. §5.3 step 1 is the kernel-side check; integrators must use `chunk_size=4096` in their `xdp_umem_reg` struct or `XDP_UMEM_REG` returns `-EINVAL` before the driver is even called. Documented in §5.3.
- **Implementation status of `MODULE_SOFTDEP("pre: af_xdp_pool")` (§3.3).** As of 2026-05-27 the soft-dep is **specified but not yet wired** in `dpaa_eth.c`. Consequence on the deployed ISO: `bind(XDP_ZEROCOPY)` on a DPAA1 netdev returns `-EOPNOTSUPP` (95) at the XSK core layer until an operator runs `modprobe af_xdp_pool` once per boot, because `dev->xdp_features` lacks `NETDEV_XDP_ACT_XSK_ZEROCOPY` until `af_xdp_pool_init()` registers `af_xdp_pool_qmgmt_ops`. Three remediation paths (decision pending — option (b) preferred as zero-kernel-change):
  - (a) Flip `CONFIG_DPAA_AF_XDP_POOL` from `=m` to `=y` in `kernel/common/kernel-config/08-dpaa1.config` (kernel rebuild).
  - (b) Ship `/etc/modules-load.d/dpaa-af-xdp-pool.conf` via a chroot hook (ISO-only, no kernel touch).
  - (c) Add the soft-dep tag — `MODULE_SOFTDEP("pre: af_xdp_pool")` — alongside the existing `MODULE_DEVICE_TABLE(of, dpaa_match)` in `dpaa_eth.c`. This is what §3.3 already prescribes; it just hasn't been written. A small follow-up patch (≤5 lines) on the `dpaa1` branch.

**What changed in v4.3 (2026-05-26):**

- §5.3 `DPAA1_MIN_UMEM_CHUNK` value formally **lowered from 4096 to 3840** (the actual kernel-derived `frame_size` on arm64 4K-page kernels), reconciling the previously-floating "v4.3" reference in §5.3 line 377 with the header version banner. No behavioural change; this is a documentation-only correction of an off-by-`XDP_PACKET_HEADROOM` in the v4.1/4.2 text.
- Driver-side LIODN sanity gate (`if (!fman_port_get_liodn(rxp)) return -ENODEV`) removed as patch `0075c` after on-DUT diagnosis: the gate was a holdover from the PPC reference design (LIODN==0 ⇒ unprobed port). On arm64 Mono Gateway DK, `fsl,liodn` is absent from every FMan-port DT node (verified in `arch/arm64/boot/dts/freescale/fsl-ls1046a.dtsi`) and `fsl_pamu` is PPC-only (`depends on PPC_E500MC`), so `fman_port_get_liodn()` always returns 0 in steady state and the gate fail-closed every single attach. The accessor itself (`fman_port_get_liodn` global, introduced in `0075a`) is retained as an observability hook for future Phase 4 single-MAC dual-flavor coexistence (where it would feed a VSP-bifurcated LIODN map).

**What changed in v4.2:**

- §4.6 PAMU rewritten: mainline `drivers/iommu/fsl_pamu.c` is gated by `depends on PPC_E500MC || (COMPILE_TEST && PPC)` and does **not** build on arm64. On arm64 LS1046A, PAMU runs in firmware-programmed bypass; FMan masters DMA directly via dma-direct. The `fsl_pamu_window_create()` API assumed in v4.1 does not exist on this platform. Phase 2 attach reduces to `xsk_pool_dma_map()` only. The per-attach PAMU-window discussion is reserved for if/when an arm64 PAMU driver is upstreamed.
- §1.1 In-scope item for `drivers/iommu/fsl_pamu.c` extension is withdrawn (PPC-only driver, not built on arm64).
- §5.3 Phase 2 attach sequence step 2 ("PAMU window create") and detach sequence step 8 (`fsl_pamu_window_destroy()`) removed; renumbered. Counter `xsk_pamu_window_fail` retired.
- §6.1 Validation Matrix row "Bad LIODN → `xsk_pamu_window_fail`" replaced with "DMA-map failure → `xsk_dma_map_fail` increments; attach returns `-EFAULT`".
- **DMA device corrected throughout:** `xsk_pool_dma_map()` uses `priv->mac_dev->dev` (the MAC platform device, `struct mac_device::dev` at `mac.h:25`) — NOT `priv->mac_dev->fman->dev` (no such member in `struct mac_device`). DMA attrs corrected to `DMA_ATTR_SKIP_CPU_SYNC` only; `DMA_ATTR_COHERENT` is inapplicable to `xsk_pool_dma_map()` (CoreNet hardware coherency is unconditional on LS1046A — the flag does not make the mapping more coherent, it just passes an undefined attribute to subarch dma-direct). References in §4.6 and §5.3 updated.
- **`xsk_pool_get_nentries()` API does not exist in 6.18.31:** The BMan seed loop exits when `xsk_buff_alloc_batch()` returns 0 (no more UMEM frames). `DPAA1_XSK_INITIAL_SEED` is used as the target count; the driver does not call `xsk_pool_get_nentries()`. §5.3 seed step updated accordingly.
- Phase 2 patch series unchanged otherwise (NAPI-hooked refill, 9-step `fman_port_disable`-anchored detach, INITIAL_SEED = 8192, DCSR observability).
- Phase 3 strict Tx backpressure, two-layer VPP shaper exclusion (§5.8), and FMan capability-detection layer (§4.1) introduced in v4.1 are unchanged.
- Open Question 5 (CC + HM ordering) remains RESOLVED in §5.5.
- Appendix A note about the surgical "Disable BMI single port" Host Command (ucode 210) for Phase 4 single-MAC coexistence remains.

**Scope:** FLAVOR=vpp Track A only. ASK2 (Track B) is the subject of `specs/ask2-rewrite-spec.md` and consumes the shared M0 abstraction defined here. Phase 4 (single-MAC dual-flavor coexistence) is a deferred appendix.
**Status:** Implementation-ready.

---

## TL;DR

1. **One driver core, one classifier API, one HW-accelerated AF_XDP stack.** The shared substrate is two ops tables (`pcd_ops`, `qmgmt_ops`) installed into `struct dpaa_priv` at probe time before `register_netdev()`. FLAVOR=vpp populates them with the UMEM-backed BMan pool, per-CPU qband mapping, `ndo_xsk_wakeup`, and the optional CC/HM/Policer/CEETM offloads enabled by ucode 210. FLAVOR=generic leaves them NULL (no behavioural change vs. mainline). ASK2 populates them per `specs/ask2-rewrite-spec.md`.

2. **FMan ucode 210 is a build dependency, not a runtime option.** U-Boot loads the 210 binary from SPI `mtd4` at SoC bring-up; the kernel inherits a fully-armed FMan with Parser/CC/KeyGen/HM/Policer/IPF/IPR available. The DPAA1 driver only consumes capabilities, it does not load microcode. We commit to 210 because: it is already on the DUT, it exposes exact-match CC trees (true per-flow steering vs. 5-tuple hash), it exposes HM nodes (HW VLAN/MPLS strip-insert without VPP touching the L2 headers), and it exposes the Policer subsystem (per-flow ingress rate-limit at line rate). The licensing constraint (proprietary, not redistributable) is handled at the VyOS image-build layer: 210 ships in the `vyos-ls1046a-build` flavor=vpp artifact; mainline kernel sources are untouched and contain no ucode blob.

3. **The AF_XDP gap is closed in three required phases plus four optional HW-offload phases.** Phase 1 is `ndo_xsk_wakeup` (thermal/idle win, copy-mode AF_XDP, no HW changes beyond exposing the wakeup syscall). Phase 2 is the XSK-backed BMan pool plus PAMU window provisioning (true zero-copy plumbing). Phase 3 is ZC RX/TX with proper qband mapping and cluster-aware NAPI pinning (kills the queue-0 alias, hits the ≥ 7 Gbps acceptance gate). Phases 3b/3c/3d/3e add CC steering, HM offload, Policer, and CEETM respectively, each independently gated by VyOS config, each useful on its own merit. Acceptance bar: idle CPU < 10% on VPP main worker after Phase 1; ≥ 7 Gbps single-stream IPv4 forwarding at < 5% kernel-net CPU per worker after Phase 3.

---

## Milestone Tracker

Single source of truth for "where are we?" across sessions. Each milestone maps to one or more sections of this spec. Status field is one of: **planned**, **in-progress**, **landed** (in-tree on `dpaa1` branch and patch-health-clean), **dut-validated** (proven on hardware), **blocked**.

| ID | Milestone | Spec § | Status | Notes |
|---|---|---|---|---|
| **M0**    | Shared abstraction (`pcd_ops` + `qmgmt_ops` + flavor-attach hooks + retro-attach + `NETDEV_XDP_ACT_XSK_ZEROCOPY` advertise) | 3.1, 3.2, 3.3, 5.1 | **dut-validated** | Patches 0068/0069a/0070/0072; verified on DUT 2026-05-26 (kernel 6.18.31-vyos), iperf3 13.1 Gbps baseline, kallsyms shows ksymtab anchors |
| **M1**    | Phase 1 — `ndo_xsk_wakeup` trampoline + `af_xdp_pool.ko` skeleton (copy-mode AF_XDP, thermal/idle win) | 5.2, 5.4 | **dut-validated** | Patches 0073 (skeleton) + 0074 (wakeup NAPI kick); `af_xdp_pool: registered` banner at t≈0.88 s |
| **M2-s1** | Phase 2 stage-1 — XSK pool attach gate: kconfig autoload (Option B, `CONFIG_DPAA_AF_XDP_POOL=y`), ethtool xsk_* counters, attach-side BMan pool seed + PAMU window + DMA map | 5.3 step 1, 5.3 step 2 | **dut-validated** | Patches 0071/0075a/0075b/0075c/0077/0079 + Option B kconfig (commit 7dc1768). DUT 2026-05-27: `bind(XDP_ZEROCOPY)=0`, 12 counters exposed |
| **M2-s2** | Phase 2 stage-2 — XSK pool detach safety + RX-ring drain validation + 100× bind/unbind churn | 5.3 step 3, 5.3 step 4 | **dut-validated** | Patch 0076 detach + commit `039a50c` sleep-in-RCU fix (dispatcher captures fn pointers under RCU then drops the lock before invoking the sleeping callback). DUT 2026-05-27 ISO `2026.05.27-0501-rolling` kernel `6.18.33-vyos`: stage-1 bind `rc=0`, stage-2 `--hold 30` clean detach, **100× churn** → attach_ok=102 / detach_ok=102, zero fails, zero timeouts, zero RCU WARN, zero stall, zero oops. RX drain count 0 (expected — XSKMAP redirect is M3-3 territory). |
| **M3-3**  | Phase 3 — True ZC RX/TX NAPI datapath, NAPI-hooked refill, `xsk_bman_starve` watchdog, `xsk_tx_inflight` backpressure, qband mapping, cluster-aware NAPI pin | 5.4 | **planned** | Phase 3a scaffold (patch 0072) landed M0. Remaining work: real `rx_hook`, `xsk_xmit`, refill loop in `dpaa_eth_napi_poll`, TX-inflight budget, hits ≥ 7 Gbps acceptance gate |
| **M3-3b** | Phase 3b — CC steering (exact-match), Parser→CC→HM→QMan ordering | 5.5 | **planned** | ucode-210 gated; requires `priv->fman_caps` capability layer (M0 augmentation TBD) |
| **M3-3c** | Phase 3c — HM offload (VLAN/MPLS strip-insert) | 5.6 | **planned** | ucode-210 gated |
| **M3-3d** | Phase 3d — Policer (per-flow ingress rate-limit) | 5.7 | **planned** | ucode-210 gated |
| **M3-3e** | Phase 3e — CEETM egress shaping + per-class CGR tail-drop + VPP-shaper exclusion (VyOS validator + `ceetm_active` sysfs sentinel) | 5.8 | **planned** | ucode-210 gated; mutually exclusive with VPP internal shaping |
| **M4**    | Phase 4 (optional) — Single-MAC dual-flavor coexistence via surgical FMan Host Command "Disable BMI single port" (ucode-210 opcode) | Appendix A | **deferred** | Avoids the ~10 ms link bounce that Phase 2/3 accept on detach |

### Current execution focus

**M3-3** is the active milestone. M2-s2 closed on 2026-05-27 with commit `039a50c` (sleep-in-RCU dispatcher fix) shipped in ISO `2026.05.27-0501-rolling` and validated on the DUT: 102 attach / 102 detach across stage-1, stage-2 (`--hold 30`), and 100× churn loop — zero error-counter bumps, zero RCU WARN, zero stall. The Phase 2 control-plane is structurally complete. M3-3 (true ZC RX/TX datapath: real `rx_hook`, `xsk_xmit`, NAPI-hooked refill, qband mapping, ≥ 7 Gbps acceptance gate) is now open.

### Reviewer-feedback delta coverage (audit table)

The seven reviewer deltas from the v4.2 audit are implementation requirements that span M2-s2 and M3. Tracked here so a future session can grep for "delta" and see status without re-reading the audit log.

| Delta | Lives in milestone | Status | Anchor in spec |
|---|---|---|---|
| 1. NAPI-hooked refill + `xsk_bman_starve` + batch 32→256 escalation | M3-3 | planned | §5.4 (lines 18, 403, 405, 411, 413, 633) |
| 2. CEETM CGR HW tail-drop + `xsk_tx_inflight` ≤ 1024 + low-water 512 + `XDP_USE_NEED_WAKEUP` | M3-3 + M3-3e | planned | §5.4 + §5.8 (lines 233, 403, 467, 468, 609, 634, 736) |
| 3. 9-step `fman_port_disable(rxp)`-anchored detach, 10 ms link bounce accepted | M2-s2 | **dut-validated** (sleep-in-RCU fix `039a50c` 2026-05-27, 100× churn clean) | §5.3 step 3 (lines 18, 260, 391, 399) |
| 4. `DPAA1_XSK_INITIAL_SEED=8192` + Open Question 9 rate-scaling | M2-s1 | landed | §5.3 step 2 (lines 383, 415) |
| 5. `priv->fman_caps` bitmask + per-cap `-ENOTSUPP` + `hw_offload_unavailable` counter + ucode-106 row | M0 augmentation, gates M3-3b/3c/3d/3e | planned | §5.1 (lines 205, 208–212, 303–306, 310, 316–318) |
| 6. Parser→CC→HM→QMan ordering normative; OQ5 closed | M3-3b doc | landed (doc) | §5.5 (lines 493, 731) |
| 7. Two-layer VPP shaper exclusion (VyOS validator + `ceetm_active` sysfs sentinel) | M3-3e | planned | §5.8 (lines 613, 614) |

---

## 1. Purpose and Scope

This document specifies the kernel-side changes to `drivers/net/ethernet/freescale/dpaa/` and related FMan/QMan/BMan support code required to ship FLAVOR=vpp in the `mihakralj/vyos-ls1046a-build` VyOS rolling image. The deliverable is a `dpaa_eth.ko` (plus optional `af_xdp_pool.ko`) that:

- Exposes `NETDEV_XDP_ACT_XSK_ZEROCOPY` and implements `ndo_xsk_wakeup`, `XDP_SETUP_XSK_POOL`.
- Delivers true zero-copy RX/TX on AF_XDP with one qband per A72 core (no queue-0 alias collapse).
- Optionally exposes HW classification, header manipulation, ingress policing, and egress shaping via per-feature VyOS config knobs.
- Carries a flavor-ops abstraction that ASK2 also consumes, without code-level dependency between flavors.

### 1.1 In scope

- Mainline `dpaa_eth.c` and `dpaa_eth_sysfs.c` changes for the ops abstraction, XSK pool lifecycle, multi-queue correctness.
- `drivers/net/ethernet/freescale/fman/` changes for CC-tree install, HM node install, Policer profile install, CEETM channel setup. All called via the M0 abstraction so the flavor module owns the policy.
- VPP plugin integration via existing AF_XDP plugin; no VPP source changes.
- ~~`drivers/iommu/fsl_pamu.c` extension for per-attach PAMU window create/destroy.~~ **Withdrawn in v4.2:** mainline `fsl_pamu` is PPC-only (`depends on PPC_E500MC`), arm64 LS1046A runs PAMU in firmware bypass and FMan DMAs via dma-direct. See §4.6.
- VyOS config glue: `set vpp settings hw-offload {classify, vlan-strip, policer, egress-shaper}`.

### 1.2 Out of scope

- ASK2 / nft flowtable offload. See `specs/ask2-rewrite-spec.md`.
- DPAA2 (`dpaa2-eth`) — DPAA2 already has native ZC.
- DPDK DPAA PMD coexistence. RC#31 still blocks mixed mode; not addressed here.
- VPP source-tree modifications.
- MTU > 3290 with XDP loaded. Hard limit on current BP slot size; relaxable only by enlarging BP past 4096 or supporting XDP S/G, both deferred.
- Microcode authoring or modification. The 210 binary ships as a U-Boot-loaded blob; we consume capabilities only.

---

## 2. Background

### 2.1 What mainline 6.18.x gives us

`dpaa_eth.c` declares:

```c
net_dev->xdp_features = NETDEV_XDP_ACT_BASIC |
                        NETDEV_XDP_ACT_REDIRECT |
                        NETDEV_XDP_ACT_NDO_XMIT;
```

XDP_DROP / XDP_PASS / XDP_TX / XDP_REDIRECT / `ndo_xdp_xmit`. No `NETDEV_XDP_ACT_XSK_ZEROCOPY`. No `ndo_xsk_wakeup`. No `XDP_SETUP_XSK_POOL`. AF_XDP works only in copy mode through the generic XDP layer.

XDP support was added by Camelia Groza (NXP) in a 7-patch v5 series posted to netdev on 2020-11-25, acked by Jakub Kicinski on 2020-12-01 (lkml cover `<cover.1606322126.git.camelia.groza@nxp.com>`). Patch 1/7 introduced `struct dpaa_eth_swbp`; patch 3/7 introduced `xdp_validate_mtu()` capping MTU at ~3290 on the default 4096-byte BP. None of it touched BMan: XDP buffers remain `dpaa_bp`-backed kernel pages, recycled through `bman_release()`, refilled by `_dpa_bp_add_8_bufs()` when per-CPU count drops below `FSL_DPAA_ETH_REFILL_THRESHOLD = 80` (target `FSL_DPAA_ETH_MAX_BUF_COUNT = 128`).

Default RSS spreads ingress across 128 PCD FQs (`DPAA_ETH_PCD_RXQ_NUM`) using FMan KeyGen 5-tuple hashing. PCD FQ IDs are aligned: `qman_alloc_fqid_range(&fq_base, 2 * DPAA_ETH_PCD_RXQ_NUM, ...)`. `QM_FQCTRL_HOLDACTIVE` plus stashing flags keep bursts on one CPU portal. Egress is `DPAA_ETH_TXQ_NUM` Tx FQs (NR_CPU × number of TX traffic classes, default 1 TC).

CEETM (Customer Edge Egress Traffic Manager) infrastructure exists in `drivers/soc/fsl/qbman/qman_ceetm.c` and `drivers/net/ethernet/freescale/dpaa/dpaa_eth_ceetm.c` but `dpaa_eth.c` does not wire it as a default qdisc. We pick this up in Phase 3e.

### 2.2 What FMan ucode 210 unlocks

Reading the LSDK 20.04/21.08 FMan driver user guide and `nxp-qoriq/fmlib`, package 210.x adds the following capabilities over public 106.4.18:

1. **Exact-match Custom Classifier trees** with deeper node nesting (≥ 8 levels vs. 106's effective 3-4 ceiling) and per-key statistics ADs. Allows protocol-aware steering: "send VXLAN to qband 0, IKE to qband 2, default to qband 3" as a HW config, not a software hash.
2. **Parser soft sequences** for non-standard protocols. Configurable opcodes that let the parser extract custom L7 headers (DTLS, QUIC, GENEVE, NSH) and feed them into CC keys. We use a small set: VXLAN inner header, MPLS label stack, IPv6 fragment extension.
3. **Header Manipulation (HM) nodes** with insert/remove/replace operations on VLAN tags (single and Q-in-Q), MPLS labels (push/pop with TC propagation), and arbitrary byte ranges. HM nodes chain with CC trees, so a "match VXLAN → strip outer headers → deliver inner Ethernet to qband 1" path is one PCD config.
4. **Policer profiles** with srTCM (single-rate three-color) and trTCM (two-rate three-color) algorithms per RFC 2697/2698. Each profile is per-flow or per-qband. Color-marking in the FD status field drives drop/yellow/green actions consumed downstream by CEETM or by the driver's ethtool drop counters.
5. **IPv4 fragmentation (IPF) and reassembly (IPR)** with timeout-driven flush. Useful for IPsec ESP MTU and for any tunneling path that crosses an MTU boundary.
6. **Host Command (HC) dispatch** for runtime PCD modifications without the full AttachPCD/DetachPCD register save-restore dance. This is the API ASK2 will lean on heavily; for FLAVOR=vpp we use it only for occasional reconfig (VyOS config reload).

The 210 binary is signed by NXP, distributed as part of LSDK BSP, and loaded by U-Boot from SPI flash `mtd4` at the `bootcmd` `fdt fixup` stage. The kernel reads the loaded microcode version from the FMan version register at probe and refuses to install advanced PCD configurations if the running ucode is < 210. We expose this check via `/sys/class/net/<iface>/dpaa/fman_ucode_caps`.

### 2.3 The queue-0 alias and why it must die

In current mainline `dpaa_eth.c`, `dpaa_eth_napi_poll()` operates per `struct dpaa_napi_portal` (QMan portal NAPI); per-queue identity does not exist. When an AF_XDP socket binds to queue 0 in copy mode, all portal dequeues are treated as queue 0; XSKs bound to other queues see nothing. This violates AF_XDP's `(netdev, queue_id)` contract and forces the workaround `data/kernel-patches/patch-dpaa-xdp-queue-index.py`, which collapses all RX FQs onto `queue_index = 0` so VPP's single XSK socket can find them in XSKMAP.

The workaround scales to one VPP worker. For four-worker VPP profiles (one per A72) we need true per-qband queue identity. Phase 3 introduces it.

---

## 3. Architectural Model

### 3.1 Shared abstraction (M0)

`struct dpaa_priv` grows three fields:

```c
const struct dpaa_pcd_ops   *pcd_ops;
const struct dpaa_qmgmt_ops *qmgmt_ops;
void                        *flavor_priv;
```

```c
struct dpaa_pcd_ops {
    int  (*install)(struct dpaa_priv *priv);              /* probe-time, pre-register_netdev */
    void (*teardown)(struct dpaa_priv *priv);             /* dpaa_remove */
    int  (*reconfig)(struct dpaa_priv *priv, struct nlattr *params);
    int  (*dump_state)(struct dpaa_priv *, struct seq_file *m);
};

struct dpaa_qmgmt_ops {
    int  (*alloc_rx_fqs)(struct dpaa_priv *, struct list_head *);
    bool (*rx_hook)(struct dpaa_priv *, struct qman_fq *, const struct qm_dqrr_entry *);
    int  (*xsk_pool_attach)(struct dpaa_priv *, struct xsk_buff_pool *, u16 queue_id);
    int  (*xsk_pool_detach)(struct dpaa_priv *, u16 queue_id);
    int  (*xsk_wakeup)(struct dpaa_priv *, u32 queue_id, u32 flags);
};
```

Default ops are NULL and the call sites short-circuit to current mainline behaviour. Flavors register at module init:

```c
int  dpaa_register_flavor_ops(const struct dpaa_pcd_ops *,
                              const struct dpaa_qmgmt_ops *);
void dpaa_unregister_flavor_ops(void);
```

Pointers are RCU-protected; `synchronize_rcu()` in unregister.

### 3.2 Hook points in `dpaa_eth_probe()`

```
dpaa_eth_probe(pdev):
  bman_is_probed(); qman_is_probed()         -> -EPROBE_DEFER
  bman_portals_probed(); qman_portals_probed()
  alloc_etherdev_mq(sizeof(*priv), DPAA_ETH_TXQ_NUM)
  fman_port_bind for RX and TX
  dpaa_priv_bp_create
  dpaa_alloc_all_fqs(...)                    <-- qmgmt_ops->alloc_rx_fqs HERE
  dpaa_eth_init_tx_port / dpaa_eth_init_rx_port
  dpaa_fq_init for each fq
  dpaa_eth_napi_add
  net_dev->netdev_ops = &dpaa_ops
  net_dev->xdp_features = NETDEV_XDP_ACT_BASIC | ...
                                              <-- pcd_ops->install HERE
  register_netdev(net_dev)
```

`pcd_ops->install` runs after FMan port setup completes (all BMI/KG registers at cold-init values, ucode 210 fully armed) but before any userspace sees the netdev.

### 3.3 Module load ordering

`MODULE_SOFTDEP("pre: af_xdp_pool")` on `dpaa_eth.ko` for FLAVOR=vpp builds. Pattern precedent: `lib/libcrc32c.c` (`MODULE_SOFTDEP("pre: crc32c")`, Clay Haapala). Deferred-probe semantics already in place re-run `dpaa_eth_probe()` if the flavor module loads after `dpaa_eth.ko`.

---

## 4. LS1046A Hardware Modules — Per-Phase Engagement Matrix

| Block | Phase 1 | Phase 2 | Phase 3 | 3b CC | 3c HM | 3d Pol | 3e CEETM | Notes |
|---|---|---|---|---|---|---|---|---|
| FMan v3 BMI | — | — | bind | — | — | — | — | RX port bound to UMEM BMan pool |
| FMan v3 Parser | — | — | — | soft seq | — | — | — | Custom protocol extraction |
| FMan v3 KeyGen | — | — | scheme tune | — | — | — | — | Qband hash via `KGSE_BASEFQID` |
| FMan v3 CC | — | — | — | tree install | — | — | — | Static tree, probe-time, no runtime AD edits |
| FMan v3 HM | — | — | — | — | node install | — | — | VLAN/MPLS strip-insert |
| FMan v3 Policer | — | — | — | — | — | profile install | — | srTCM/trTCM per qband or per flow |
| FMan v3 IPF/IPR | — | — | — | — | — | — | — | Reserved for ASK2 IPsec path |
| FMan v3 MURAM | — | — | — | small CC tree | HM nodes | profiles | — | < 8 KiB total for FLAVOR=vpp |
| FMan v3 DCSR | — | observe | observe | observe | observe | observe | observe | Exception telemetry via debugfs |
| BMan | refill | dedicated pool | refill from XSK | — | — | — | — | XSK-backed pool, 4 pool IDs per netdev |
| QMan SWP | wakeup | — | per-CPU pin | — | — | — | dequeue | One portal per qband, cluster-aware |
| QMan FQ | — | retire/reactivate | qband mapping | CC targets | — | color-aware drop | CEETM LFQs | 4 qbands × 32 PCD FQs |
| QMan CEETM | — | — | — | — | — | — | full | LNI/CQ/CGR for egress shaping |
| MURAM (PCD) | — | budget check | — | yes (small) | yes | yes | — | Tracked in `/sys/.../muram_used` |
| PAMU | — | — | — | — | — | — | — | arm64 firmware bypass; fsl_pamu is PPC-only (§4.6) |
| CCI-400 | — | — | cluster hint | — | — | — | — | A72 cluster co-residency for workers |
| CoreNet | coherent | coherent | coherent | coherent | coherent | coherent | coherent | HW-coherent; `DMA_ATTR_SKIP_CPU_SYNC` on xsk_pool_dma_map(); no explicit COHERENT attr needed |
| SerDes / XFI PCS | observe | observe | observe | — | — | — | — | `phylink_resolve()` precondition |
| SEC 5.4 (CAAM) | — | — | — | — | — | — | — | Out of FLAVOR=vpp scope; protected FQ set respected if Phase 4 lands |

Legend: "—" = not touched; named verb = the action this phase performs on that block.

The matrix is normative. Any phase claiming to use a block not marked above is a spec violation and should be flagged in review.

### 4.1 FMan v3 with ucode 210

384 KiB MURAM, 4 BMI ports, 32 KG schemes, Parser + CC + HM + Policer + IPF + IPR + HC available. The driver does not load microcode; U-Boot does, before kernel hand-off.

**Capability-detection layer (M0).** A `dev_warn()` on ucode-version mismatch is too soft: `pcd_ops->install` could still write to MURAM offsets that don't exist in ucode 106's layout, producing silent corruption rather than a clean failure. Instead, the driver reads `FM_REV`, `FM_IP_REV_1`, `FM_IP_REV_2` and any feature-discovery registers at probe and populates a `priv->fman_caps` bitmask:

```c
#define FMAN_CAP_CC_EXACT_MATCH   BIT(0)   /* ucode 210+: deep exact-match CC trees */
#define FMAN_CAP_HM_NODES         BIT(1)   /* ucode 210+: HM insert/remove/replace */
#define FMAN_CAP_POLICER_TRTCM    BIT(2)   /* ucode 210+: trTCM profiles */
#define FMAN_CAP_HC_DISPATCH      BIT(3)   /* ucode 210+: runtime PCD edits via HC */
#define FMAN_CAP_PARSER_SOFTSEQ   BIT(4)   /* ucode 210+: soft sequences */
```

Each exported `fman_*_install()` helper takes the `priv` pointer and returns `-ENOTSUPP` if the corresponding cap bit is clear. Phase 3b-e flavor code becomes a series of "try to install, accept `-ENOTSUPP` gracefully, increment a `hw_offload_unavailable` counter" calls. No MURAM write is attempted on an unsupported ucode. The Validation Matrix (§6.1) gets a new row: boot with ucode 106, verify Phase 3b-e installs return `-ENOTSUPP`, basic RSS still works, no MURAM writes attempted (verified via `cat /sys/kernel/debug/fman_muram/used` showing constant value across feature-install attempts).

MURAM consumption budget for FLAVOR=vpp at full feature engagement: roughly 5 KiB for CC tree (one root + four protocol branches × four qband leaves), 1 KiB for HM nodes (VLAN strip + insert templates), 2 KiB for Policer profiles (one per qband). Total < 8 KiB out of 384 KiB available. PR14h's 64 KiB PCD reservation is consumed primarily by ASK2's dynamic tree (up to 255 keys × ~150 B per key including stats ADs); FLAVOR=vpp's static tree never exceeds ~5% of the reservation.

### 4.2 BMan

64 pool IDs system-wide. FLAVOR=vpp reserves 4 per netdev (one per qband). Dual-port (eth3 + eth4) deployment consumes 8 of 64. Per-pool depth defaults to 2048 buffers (twice mainline default); seeded from XSK UMEM at `XDP_SETUP_XSK_POOL` attach time.

LS1046A BMan supports both **pool channels** (any portal can dequeue) and **dedicated channels** (one portal owns the FQ). FLAVOR=vpp uses dedicated channels for each qband so one A72 owns one qband and the QMan stash window pre-warms that core's L1 with the next DQRR entry. This is a measurable wins vs. mainline's pool-channel-with-HOLDACTIVE default for steady traffic, because dedicated channels eliminate the HOLDACTIVE arbitration entirely.

### 4.3 QMan and QMan SWPs

Four SWPs, one per A72. LS1046A exposes 28 pool channels via `qoriq-qman1-portals.dtsi` plus 4 dedicated channels (one per CPU). FLAVOR=vpp's 4-dedicated-channel consumption is within budget.

Each SWP has CCSR registers plus a cacheable stash window. Stash effectiveness requires the polling CPU to be the same CPU the portal is affined to; the `qmap[]` table (§6.3) enforces this.

### 4.4 QMan CEETM

CEETM (Customer Edge Egress Traffic Manager) hangs off the QMan egress path. It implements an 8-level hierarchical scheduler with logical FQs (LFQs), class queues (CQs), and congestion groups (CGRs). The mainline driver `dpaa_eth_ceetm.c` exposes it as a `tc qdisc` (root qdisc replacement) but `dpaa_eth.c` does not enable it by default.

Phase 3e wires CEETM as the egress qdisc for FLAVOR=vpp interfaces. VPP-internal output shaping (token bucket in software) is replaced by HW shaping with sub-microsecond accuracy. Two profiles ship: a flat 8-class strict-priority profile for "I just want priority", and a hierarchical HTB-like profile with weights per qband.

### 4.5 MURAM

Per §4.1, FLAVOR=vpp consumption is < 8 KiB. We respect PR14h's 64 KiB PCD reservation (introduced by ASK2 for its larger tree) and PR14j/k's non-recursive mutex discipline (allocate outside FMan port locks). MURAM-budget checks at `pcd_ops->install` time, not at XSK attach time (XSK attach does not touch MURAM in FLAVOR=vpp).

### 4.6 PAMU

DPAA1's IOMMU. On legacy QorIQ PPC platforms (P4080, T4240, etc.), each DPAA1 master (FMan port, QMan portal, BMan portal, SEC) has a Logical I/O Device Number (LIODN) and DMAs only into PAMU-provisioned windows tagged with that LIODN, with the kernel `fsl_pamu` driver creating/destroying windows at attach/detach.

**arm64 LS1046A is different — PAMU runs in firmware bypass and the kernel driver is PPC-only.** Mainline 6.18.x `drivers/iommu/fsl_pamu.c` Kconfig declares:

```kconfig
config FSL_PAMU
    bool "Freescale IOMMU support"
    depends on PCI
    depends on PPC_E500MC || (COMPILE_TEST && PPC)
```

There is no arm64 build path. On the LS1046A SoC, U-Boot's pre-kernel BSP programs the PAMU's LIODN tables for FMan/QMan/BMan/SEC into a permissive bypass configuration once, and the kernel never touches PAMU registers. FMan masters DMA directly against system DRAM via the dma-direct path; `dma_map_single()` and `dma_alloc_coherent()` produce identity-mapped CoreNet-coherent physical addresses with no IOMMU page-table walk on the data path.

**Phase 2 consequences:**

1. No per-attach `fsl_pamu_window_create()` call is required or possible — the symbol does not exist on arm64. The `xsk_pool_dma_map(pool, priv->mac_dev->dev, DMA_ATTR_SKIP_CPU_SYNC)` call in §5.3 step 2 is sufficient: it produces the CoreNet-coherent DMA addresses that FMan BMI can dequeue from BMan and write into directly. (`priv->mac_dev->dev` is `struct mac_device::dev` at `mac.h:25` — the MAC's own platform device. `struct mac_device` has no `fman` member. `DMA_ATTR_COHERENT` is dropped: CoreNet ensures hardware coherency unconditionally on LS1046A regardless of DMA attrs.)
2. The v4.1 attach sequence step "PAMU window create" and detach sequence step `fsl_pamu_window_destroy()` are removed in v4.2. The `xsk_pamu_window_fail` counter is retired; failures of `xsk_pool_dma_map()` increment the existing `xsk_dma_map_fail` counter and fail attach with `-EFAULT`.
3. The 9-step UMEM detach sequence remains anchored on `fman_port_disable(rxp)` (halt BMI) before `xsk_pool_dma_unmap()`. This is what protects against the "packet arrives between `xsk_pool=NULL` publish and DMA unmap" race; the PAMU window destroy was never the load-bearing part of that race on arm64.
4. If an arm64 `fsl_pamu` driver is upstreamed in a future kernel cycle (or if NXP backports one), per-attach window provisioning becomes a hardening option — finer-grained LIODN scope reduces blast radius of a runaway FMan DMA. Spec would then re-introduce the create/destroy pair as an optional Phase 2 hardening tier, not a correctness requirement.

**PPC vs arm64 verification check.** `grep -l FSL_PAMU drivers/iommu/Kconfig` shows the gating; `make ARCH=arm64 olddefconfig` followed by `grep FSL_PAMU .config` produces `# CONFIG_FSL_PAMU is not set` (the symbol is unselectable, not just unset). Any spec claim of "PAMU window create on arm64" is a defect — file a doc bug against this section.

### 4.7 CCI-400 vs CoreNet

LS1046A has two coherency fabrics. CCI-400 handles A72 cluster ↔ cluster and A72 ↔ outer caches. CoreNet handles A72 ↔ DPAA1 (FMan/QMan/BMan/SEC).

The principle of "hardware coherency over software cache management" is a CoreNet guarantee. UMEM DMA-mapped with `DMA_ATTR_SKIP_CPU_SYNC` is coherent with FMan DMA over CoreNet without per-descriptor `xsk_buff_dma_sync_for_cpu()` — CoreNet guarantees coherency unconditionally; no `DMA_ATTR_COHERENT` flag is needed or passed (it has no defined semantics on arm64 dma-direct). We rely on this in the hot path; the `dpaa_eth.force_dma_sync=1` modparam re-enables explicit sync if a future SoC revision or DDR controller errata invalidates the assumption.

CCI-400 matters for VPP worker pinning. The four A72s are arranged as two clusters of two; workers on the same cluster share L2 over CCI-400; cross-cluster does not. The `qmap[]` builder (§6.3) prefers to keep an XSK queue and its consuming VPP worker on the same cluster. With `workers N ≤ 2` we pick cores 0 and 1 (cluster 0); with `N = 4` we spread 2-and-2 across clusters.

### 4.8 SerDes and XFI PCS

The two 10G SFP+ cages (eth3, eth4) come off LS1046A SerDes lanes configured for XFI (10GBASE-R). The PCS sits inside the FMan v3 mEMAC; `phy-connection-type = "xgmii"` in DTS is critical.

`xsk_socket__create()` runs `phylink_resolve()` before allowing attach. If the PCS state machine is wedged (SFP-10G-T rollball PHY without the patch-4003 EINVAL fallback) the XSK attach succeeds but RX stays silent. Validation §8.1 distinguishes PCS-lock failure from PAMU-window miss from BMan-pool seed shortage.

### 4.9 FMan DCSR exception window

`drivers/net/ethernet/freescale/fman/fman.c` reads but does not expose the DCSR (Debug Control and Status Register) window at FMan offset `0x0F_0000`. Per-block exception flags (BMI buffer-pool-empty, parser exception, KG exception, Policer exception) are silently dropped.

Phase 2 wires DCSR reads into a debugfs tree under `/sys/kernel/debug/dpaa-eth/<iface>/dcsr/`. Reads are rate-limited to ≥ 1 ms apart because tight polling over CoreNet perturbs FMan microcode timing in lab. Phase 3+ adds `ethtool -S` counters: `fm_bmi_err_*`, `fm_parser_err_*`, `fm_kg_err_*`, `fm_pol_err_*`.

---

## 5. Implementation Plan

Three required phases (1, 2, 3) plus four optional HW-offload phases (3b, 3c, 3d, 3e). Each phase ships independently. Optional phases are gated by VyOS config and by ucode-210 capability detection.

### 5.1 M0 — Shared abstraction (mainline candidate)

**Patches:** `0068-dpaa-flavor-ops.patch`, `0069-dpaa-flavor-hooks.patch`

Pure structural refactor: introduce `struct dpaa_pcd_ops` and `struct dpaa_qmgmt_ops` in a new `drivers/net/ethernet/freescale/dpaa/dpaa_flavor.h`. Add `priv->pcd_ops`, `priv->qmgmt_ops`, `priv->flavor_priv` to `struct dpaa_priv`. Wire NULL-checked call sites in `dpaa_eth_probe()`, `dpaa_remove()`, `dpaa_xdp()` (for the future `XDP_SETUP_XSK_POOL` case). Add `dpaa_register_flavor_ops()` / `dpaa_unregister_flavor_ops()` with RCU-protected globals.

Mainline-suitable as a standalone series: zero behavioural change, ~120 lines net, framed in the cover letter as the in-tree replacement for the removed `sdk_dpaa` `FM_PORT_SetPCD()` callback. Cc: Madalin Bucur, Camelia Groza, Sean Anderson, Ioana Ciornei.

**Microcode capability-detection layer (M0).** A `dev_warn()` on a downgrade from ucode 210 → ucode 106 is too soft: even with a warning, a `pcd_ops->install` that blindly writes to MURAM offsets defined by ucode 210's layout will silently corrupt MURAM on ucode 106 instead of failing cleanly. M0 introduces a capability bitmask populated at probe by reading FMan version registers:

```c
/* drivers/net/ethernet/freescale/fman/fman.h */
#define FMAN_CAP_CC_EXACT_MATCH   BIT(0)  /* ucode-210 exact-match CC nodes      */
#define FMAN_CAP_HM_NODES         BIT(1)  /* ucode-210 Header-Manipulation nodes */
#define FMAN_CAP_POLICER_TRTCM    BIT(2)  /* ucode-210 trTCM 256-profile policer */
#define FMAN_CAP_HC_DISPATCH      BIT(3)  /* ucode-210 Host Command dispatch     */
/* … room for future cap bits as new ucode features surface … */

/* struct dpaa_priv — populated by mac_dev probe via fman_get_caps(priv->mac_dev->fman) */
u32 fman_caps;
```

Each exported install helper takes `priv` and returns `-ENOTSUPP` if the corresponding cap bit is not set, **before** touching MURAM:

```c
int fman_cc_tree_install (struct dpaa_priv *priv, …);  /* needs FMAN_CAP_CC_EXACT_MATCH */
int fman_hm_node_install (struct dpaa_priv *priv, …);  /* needs FMAN_CAP_HM_NODES      */
int fman_policer_install (struct dpaa_priv *priv, …);  /* needs FMAN_CAP_POLICER_TRTCM */
```

Phase 3b–e flavor code is structured as `install → if (-ENOTSUPP) { priv->hw_offload_unavailable++; continue; }` — fail-soft. Basic RSS (KeyGen, ucode-106-compatible) still works on a ucode-106 boot; only the optional 210-gated features are skipped. This is exercised by validation matrix row "Boot on ucode 106: Phase 3b–e install returns `-ENOTSUPP`, no MURAM writes, RSS works" (§6.1) — verified by reading a constant value from `/sys/kernel/debug/fman_muram/used` across the install attempts.

**Acceptance gate M0:**

1. `modprobe fsl_dpaa_eth` without any flavor module: zero behavioural change. All eth interfaces come up; iperf3 line-rate works.
2. `rmmod fsl_dpaa_eth && modprobe fsl_dpaa_eth` ×100 with no leak.
3. Patches apply cleanly on `net-next/main` and pass `checkpatch.pl` and the netdev pre-merge bots.

### 5.2 Phase 1 — `ndo_xsk_wakeup` (thermal/idle, copy-mode AF_XDP)

**Patch:** `0070-dpaa1-xsk-wakeup.patch`

```c
static int dpaa_xsk_wakeup(struct net_device *ndev, u32 queue_id, u32 flags)
{
    struct dpaa_priv *priv = netdev_priv(ndev);
    const struct dpaa_qmgmt_ops *ops;

    if (unlikely(!netif_running(ndev)))
        return -ENETDOWN;
    rcu_read_lock();
    ops = rcu_dereference(priv->qmgmt_ops);
    if (!ops || !ops->xsk_wakeup) {
        rcu_read_unlock();
        return -EOPNOTSUPP;
    }
    rcu_read_unlock();
    return ops->xsk_wakeup(priv, queue_id, flags);
}
```

For `XDP_WAKEUP_RX`, kick the corresponding NAPI context via `napi_if_scheduled_mark_missed()` / `napi_schedule()` against the resolved `dpaa_napi_portal`. For `XDP_WAKEUP_TX`, poke the QMan egress portal via `qman_p_irqsource_add` so completion processing resumes.

After `napi_complete_done()` and before re-arming the QMan interrupt, if any `xsk_pool[qband]` is bound and `xsk_uses_need_wakeup()` is true, call `xsk_set_rx_need_wakeup()`. On the next dequeue burst, `xsk_clear_rx_need_wakeup()`. In Phase 1 this is harmless because no UMEM is yet bound; the wiring is the seed for Phase 3's full integration.

Pattern reference: Magnus Karlsson's `ndo_xsk_wakeup` introduction (kernel 5.3); Maxim Mikityanskiy's RCU-safe wrapper (kernel 5.4 stable).

**Acceptance gate Phase 1:**

1. VPP `af_xdp` in copy mode runs WITHOUT `set vpp settings unix poll-sleep-usec 100`.
2. Idle CPU on the VPP main worker drops from ~100% to < 10%. Measured via `mpstat -P ALL 1` and `/sys/class/thermal/thermal_zone0/temp`.
3. All five LS1046A thermal zones stay within `fan-pid` COOL/WARM bands at idle and under light traffic.
4. `xsk_ring_prod__needs_wakeup(&xsk->tx)` returns 1 when driver wants a kick, 0 otherwise.

### 5.3 Phase 2 — XSK-backed BMan pool, PAMU window, DCSR observability

**Patch:** `0071-dpaa1-xsk-pool-setup.patch`

DPAA1's BMan is a hardware buffer-pool manager: FMan RX BMI autonomously dequeues buffers from BMan with no per-packet driver involvement. Other mainline ZC drivers (Intel i40e/ixgbe/ice/igc/igb, Mellanox mlx5, Marvell mvneta/mvpp2, Microchip lan966x, virtio-net) all use software page pools. DPAA1 cannot swap; BMan is the source and BMan only understands physical addresses.

**Approach: XSK-backed BMan pool.** Allocate a dedicated `dpaa_bp` whose backing memory is the XSK UMEM. Pull a chunk via `xsk_buff_alloc()`, DMA-map via `xsk_buff_dma_map()`, release the physical address via `bman_release()`. Hardware drains and refills as usual; only the source of refill memory changes.

Attach sequence (`af_xdp_pool.ko`'s `xsk_pool_attach`):

1. **Validate constraints, fail closed.**
   - `dev->mtu <= 3290`
   - `xsk_pool_get_rx_frame_size(pool) >= DPAA1_MIN_UMEM_CHUNK` (3840 — derived from MTU 3290 + L2 14 B + DPAA_FD ≤ 64 B + 64 B alignment slack, comfortably fits an Ethernet frame at the spec MTU ceiling. Previously 4096; lowered in v4.3 because the kernel XSK core enforces `chunk_size ≤ PAGE_SIZE` and `xsk_pool_get_rx_frame_size() = chunk_size − headroom − XDP_PACKET_HEADROOM(256)`, capping `frame_size` at 3840 on arm64 4K-page kernels. The 4096 floor was a round number, not a hardware requirement.)
   - First UMEM chunk satisfies `IS_ALIGNED(addr, DPAA_FD_DATA_ALIGNMENT)` (64 B on FMan v3 Rev>1)
   - `xsk_pool_get_headroom(pool) >= priv->tx_headroom`
   - `queue_id < priv->xsk_max_qbands` (4)
2. **DMA-map** with `xsk_pool_dma_map(pool, priv->mac_dev->dev, DMA_ATTR_SKIP_CPU_SYNC)`. `priv->mac_dev->dev` is the MAC platform device (`struct mac_device::dev`, `mac.h:25`). On arm64 LS1046A this produces CoreNet-coherent identity-mapped physical addresses via dma-direct (PAMU is in firmware bypass — see §4.6). `DMA_ATTR_COHERENT` is NOT passed: CoreNet hardware coherency is unconditional and `xsk_pool_dma_map()` has no defined semantics for that attr on arm64 dma-direct. Failure → `xsk_dma_map_fail++`, return `-EFAULT`.
3. **Allocate dedicated BMan pool ID** via `bman_acquire()`. Record in `priv->xsk_bpid[queue_id]`.
4. **Seed the new BMan pool**: iterate `xsk_buff_alloc_batch(pool, &handles, N)`, translate each to DMA address via `xsk_buff_xdp_get_dma()`, `bman_release()` into the new pool. Initial seed `DPAA1_XSK_INITIAL_SEED = 8192` (see "Initial BMan pool seed" rationale below).
5. **Register memory model**: `xdp_rxq_info_reg_mem_model(&priv->rxq_info[queue_id], MEM_TYPE_XSK_BUFF_POOL, pool)`.
6. **Publish**: `rcu_assign_pointer(priv->xsk_pool[queue_id], pool)`.

**Detach symmetry — FMan BMI quiescence is mandatory.** FMan BMI operates autonomously: a packet entering the MAC after `priv->xsk_pool[queue_id]=NULL` is published but before `xsk_pool_dma_unmap()` runs can still hold a DMA cookie into the about-to-be-unmapped UMEM. On arm64 LS1046A there is no PAMU window to translate (§4.6 — PAMU is in firmware bypass and the dma-direct mapping is identity), but the FMan BMI write into a freed UMEM page still corrupts memory and the A72 sees the result as a synchronous external abort the next time that page is reused. The detach sequence MUST halt RX BMI before touching memory mappings:

1. `rcu_assign_pointer(priv->xsk_pool[queue_id], NULL)`.
2. `synchronize_net()` — flush in-flight NAPI dereferences.
3. `fman_port_disable(rxp)` — halts BMI, blocks new DMAs. Costs ~10 ms link bounce on detach. **Acceptable**: detach is a control-plane event (operator config change or process exit), not a hot path.
4. Wait for any in-flight FD to complete by draining DQRR on this port's pool channel.
5. `qman_retire_fq()` on each FQ in the qband.
6. Drain the dedicated BMan pool via `bman_acquire()`; return handles to XSK pool via `xsk_buff_free()`.
7. `xsk_pool_dma_unmap()` with `SKIP_CPU_SYNC`.
8. Reactivate FQs against original kernel BMan pool; reset memory model to `MEM_TYPE_PAGE_ORDER0`.
9. `fman_port_enable(rxp)` — re-arms port for kernel BMan pool.

For Phase 4 single-MAC dual-flavor coexistence, a full `fman_port_disable()` would impact the kernel domain on the shared MAC. Phase 4 will replace step 3 with a surgical "Disable BMI single port" Host Command (FMan control RISC opcode exposed by ucode 210 per NXP LSDK driver docs). The opcode call-out lives in Appendix A when Phase 4 lands.

DCSR exception window plumbing lands here. New debugfs files under `/sys/kernel/debug/dpaa-eth/<iface>/dcsr/`: `bmi_err`, `parser_err`, `kg_err`, `pol_err`. Read paths are rate-limited to 1 ms.

**Counters (Phase 2 introduces; Phase 3 grows):** `xsk_pool_attach_ok/fail`, `xsk_pool_detach_ok/timeout`, `xsk_bman_seed_short`, `xsk_bman_starve` (Phase 3, NAPI-hooked refill), `xsk_tx_backpressure` (Phase 3, in-flight budget), `xsk_dma_map_fail`, `xsk_align_reject`, `xsk_headroom_reject`, `xsk_mtu_reject`. (`xsk_pamu_window_fail` retired in v4.2 — see §4.6.)

**NAPI-hooked BMan refill (no workqueue tick).** A 1 s workqueue tick is meaningless at line rate: at 7 Gbps / 1500 B (583 Kpps) a 2048-buffer pool drains in ~3.5 ms; at 64 B / 1.5 Mpps it drains in ~1.4 ms. BMan-depth probing belongs in the hot path, not in a periodic worker.

The depth check moves into `dpaa_eth_napi_poll()`, called every poll:

1. At the start of each poll, read `bman_pool_get_count(priv->xsk_bpid[queue_id])`.
2. If below `DPAA1_XSK_REFILL_THRESHOLD = 256`, refill opportunistically from the XSK fill ring via `xsk_buff_alloc_batch()` + `bman_release()` in batches of 32 after the dequeue burst (per Phase 3's RX path step 5).
3. If below `DPAA1_XSK_MIN_DEPTH = 64`, increment the `xsk_bman_starve` event counter and bump the refill batch from 32 to 256 until depth clears the threshold.

The Phase 2 workqueue tick is dropped entirely — it was both insufficient (1 s vs. millisecond drain) and redundant with the NAPI-poll path Phase 3 already implements.

**Initial BMan pool seed.** `DPAA1_XSK_INITIAL_SEED = 8192` (not 2048). Memory cost is zero from the driver's perspective because UMEM is VPP-owned and pre-allocated; the driver just releases more pointers into BMan. VPP's `af_xdp` plugin default UMEM is 16384 chunks per ring, so 8192 leaves headroom. The 8192 seed provides a latency buffer against VPP scheduling jitter at multi-Gbps rates. See Open Question 9 on whether the seed should scale with link rate (8192 for 10G XFI, 4096 for 1G RGMII) or one size is sufficient.

**Acceptance gate Phase 2:**

1. `ip -d link show fm1-mac3` reports `xsk_zerocopy` in xdp-features.
2. `xdpsock -i fm1-mac3 -q 0 -z -r` succeeds.
3. Attach/detach ≥ 100 times in stress loop, no crash, no `kmemleak`, no BMan pool ID leak.
4. DMA-map failure (synthetic fault injection via `dma_map_single` shim) → `xsk_dma_map_fail` increments; attach returns `-EFAULT`; no silent traffic loss.
5. Throughput allowed no worse than copy mode; TX ZC lands in Phase 3.

### 5.4 Phase 3 — True ZC RX/TX, multi-queue correctness, qband mapping

**Patch:** `0072-dpaa1-xsk-zc-datapath.patch`

Introduce "qband" as AF_XDP queue identity. `qband ∈ [0..3]` maps to a contiguous band of 32 PCD FQs each on a dedicated BMan channel and a dedicated QMan SWP. KeyGen `KGSE_BASEFQID` is set so hash result mod 32 picks an FQ within the qband; top 2 bits pick qband.

```c
static inline u16 dpaa_fq_to_qband(struct dpaa_priv *priv, u32 fqid)
{
    if (fqid < priv->pcd_fq_base ||
        fqid >= priv->pcd_fq_base + DPAA_ETH_PCD_RXQ_NUM)
        return 0;
    return (fqid - priv->pcd_fq_base) /
           (DPAA_ETH_PCD_RXQ_NUM / priv->xsk_max_qbands);
}
```

**RX path** (modify per-frame dequeue callback in `dpaa_eth_napi_poll()`):

1. Compute `qband = dpaa_fq_to_qband(priv, qm_fd_get_fqid(fd))`.
2. Branch on `rcu_dereference_bh(priv->xsk_pool[qband])`:
   - NULL → existing copy-mode path.
   - non-NULL → zero-copy branch.
3. Zero-copy:
   1. Read physical address from `qm_fd_addr(fd)`.
   2. Translate to `struct xdp_buff *` via pool's address index (UMEM is contiguous; arithmetic mapping).
   3. Apply parsed length/offset from FD.
   4. Run XDP program with `bpf_prog_run_xdp()`.
   5. `XDP_REDIRECT` → `xsk_buff_set_size()` + `xdp_do_redirect()`.
   6. `XDP_DROP` → `bman_release()` back to qband's BMan pool.
   7. `XDP_TX` → build outbound FD pointing at same UMEM chunk, enqueue to qband's egress FQ, rely on TxConf for recycle.
4. After dequeue burst, `xdp_do_flush()` if any frame redirected.
5. Refill qband's BMan pool from XSK Fill Ring: `xsk_buff_alloc_batch()` + `bman_release()` in batches of 32.

**TX path** (modify `dpaa_start_xmit()` and add `dpaa_xsk_xmit_zc()`):

1. In NAPI poll, after RX servicing, drain XSK TX ring: `xsk_tx_peek_release_desc_batch()` up to budget. For each descriptor, look up UMEM physical address via `xsk_buff_raw_get_dma()`, build `struct qm_fd`, `qman_enqueue()` on qband's egress FQ.
2. Service TxConf FQ in same NAPI iteration with **higher** priority than RX refill: for each confirmed FD, recover UMEM address, push to XSK Completion Ring via `xsk_tx_completed_addr()`.
3. If XSK Tx Ring drained but `XDP_USE_NEED_WAKEUP` socket exists, set Tx need-wakeup flag.

**Strict Tx backpressure against TxConf recycling stalls.** Under Phase 3e strict-priority CEETM, low-class traffic can queue indefinitely in CEETM. The UMEM chunks tied to those FDs are unreclaimable until CEETM dequeues them. Without backpressure the XSK Completion Ring stalls, VPP runs out of TX descriptors, then RX BMan starves because the fill ring is empty. Two mitigations layered:

1. **Hardware tail-drop via CEETM CGRs.** Phase 3e configures Congestion Groups with per-class tail-drop thresholds. Frames drop in HW before the buffer accumulates and ties up UMEM. See §5.8 for the CGR config and counters.
2. **Software in-flight budget.** Track `priv->xsk_tx_inflight[qband]` (atomic, incremented at enqueue, decremented at TxConf). If it exceeds `DPAA1_XSK_TX_MAX_INFLIGHT = 1024`, stop pulling from the XSK Tx ring, set the `XDP_USE_NEED_WAKEUP` Tx flag, increment `xsk_tx_backpressure`. Drain resumes when TxConf brings the counter below the low-water mark `DPAA1_XSK_TX_LOW_WATER = 512`. Hysteresis prevents oscillation under sustained backpressure.

**Queue mapping correctness:**

1. Delete `data/kernel-patches/patch-dpaa-xdp-queue-index.py` under `CONFIG_DPAA_XSK_MULTIQ=y` (retained at `=n` for emergency rollback).
2. `xdp_rxq_info_reg()` call sites pass real queue index, not 0.
3. Build explicit `priv->qmap[qband] = { fqid_base, swp_id, napi, cpu }` at probe. Exposed via debugfs at `/sys/kernel/debug/dpaa-eth/<iface>/qmap`. Runtime assertions via `WARN_ON(napi != &priv->qmap[qband].napi)` in dequeue callback.
4. Pin each NAPI context to CPU owning the matching QMan SWP via `netif_napi_add()` + `netif_set_napi_irq_affinity_hint()`.
5. **Cluster-affinity hint** (§4.7). `workers N ≤ 2` → cores 0 and 1 (cluster 0); `N = 4` → 2-and-2 across clusters. Exposed read-only as `/sys/class/net/<iface>/dpaa/cluster_map` so the operator can verify VPP's `cpu { workers ... }` matches.

**Dedicated BMan channels.** Replace mainline's default pool-channel-with-HOLDACTIVE with one dedicated channel per qband. Eliminates the HOLDACTIVE arbitration overhead; the stash window pre-warms exactly the right CPU.

**A050385 erratum interaction.** Camelia Groza's XDP workaround relocates Tx frames whose buffer starts within 64 B of a 4 KB boundary. For XSK we cannot relocate (UMEM ownership is the app's), so we require UMEM chunks with ≥ 64 B headroom and refuse `bind(XDP_ZEROCOPY)` otherwise. VPP defaults to `XDP_PACKET_HEADROOM = 256`, safely > 64.

**Acceptance gate Phase 3:**

1. `xdp-features` reports `NETDEV_XDP_ACT_XSK_ZEROCOPY`.
2. `xdpsock -i fm1-mac3 -q 3 -z -r` (non-zero queue) receives traffic. Queue-0 alias is dead.
3. ≥ 7 Gbps single-stream IPv4 forwarding at < 5% kernel-net CPU per worker.
4. `perf top -p $(pidof vpp_main)` no longer shows `dpaa_rx`, `__alloc_skb`, `skb_copy_from_linear_data`, or `memcpy` in top 10.
5. 4-worker multi-queue scaling: aggregate ≥ 14 Gbps for 1500 B IPv4 forwarding on dual SFP+.
6. 24 h iperf3 stress: no wedge, no `kmemleak`, no `RCU stall`.

### 5.5 Phase 3b — CC steering (optional, ucode-210 gated)

**Confirmed CC + HM chain ordering.** FMan v3 evaluates the parser first, then the CC tree on unmodified ingress headers, then HM on egress from the matching CC node, then QMan enqueue. Therefore CC keys match wire-format headers and VPP receives post-HM frames. Validation: on a VLAN-tagged ingress flow with both a CC steering rule and an HM vlan-strip rule active, `tcpdump` sees tagged frames at the wire and VPP `show trace` shows untagged frames. (This resolves prior Open Question 5; the answer is now normative.)

**Patch:** `0073-dpaa1-xsk-cc-steering.patch`

VyOS config:

```
set vpp settings hw-offload classify rule 10 protocol vxlan target-qband 0
set vpp settings hw-offload classify rule 20 protocol ipsec-esp target-qband 1
set vpp settings hw-offload classify rule 30 protocol bgp     target-qband 2
set vpp settings hw-offload classify default-target-qband 3
```

This translates into a static CC tree installed at `pcd_ops->install` time. Tree shape: one root match-table keyed on `(L4_PROTO, L4_DST_PORT)`, four leaf action descriptors each pointing at the qband's FQ range base.

API surface (in `drivers/net/ethernet/freescale/fman/`):

```c
int  fman_cc_tree_install(struct fman *fm, u8 port_id,
                          const struct fman_cc_static_tree *spec);
void fman_cc_tree_destroy(struct fman *fm, u8 port_id);
```

`fman_cc_static_tree` carries 5-tuple + protocol-id rules and target FQID. The implementation uses ucode-210's exact-match CC node primitives via `FM_PCD_MatchTableSet()`. MURAM allocation is bounded (≤ 4 KiB total for the FLAVOR=vpp tree).

This API also lands in the FMan headers as an exported symbol so `specs/ask2-rewrite-spec.md`'s Path A can consume it. Both flavors share the same `fman_cc_*` primitive; the static-vs-dynamic distinction lives in the flavor module.

When CC is not enabled in VyOS config, Phase 3 runs in pure KeyGen-RSS mode (qband selection by hash). When CC is enabled, KeyGen still hashes within the chosen qband (32 FQs per qband, 5-tuple hash mod 32).

**Acceptance gate Phase 3b:**

1. `tcpdump -i fm1-mac3 -nn` shows VXLAN frames; `xdpsock -i fm1-mac3 -q 0 -z -r` receives them; `-q 1` does not.
2. `cat /sys/kernel/debug/dpaa-eth/fm1-mac3/cc_tree` shows the installed rules.
3. No throughput regression vs. Phase 3.
4. Toggling rules via `set vpp settings hw-offload classify` and `commit` succeeds without netdev flap (uses ucode-210 HC dispatch, not AttachPCD).

### 5.6 Phase 3c — HM offload (optional, ucode-210 gated)

**Patch:** `0074-dpaa1-xsk-hm-offload.patch`

VyOS config:

```
set vpp settings hw-offload vlan-strip-on-ingress
set vpp settings hw-offload vlan-insert-on-egress vlan 100
```

HW VLAN strip on ingress + insert on egress without VPP touching L2 headers. Frees VPP from per-packet `vlib_buffer_chain_increment_length` calls. Useful when VPP is doing pure L3 forwarding and the L2 framing is uniform (single VLAN per port).

API surface:

```c
int  fman_hm_node_install(struct fman *fm, u8 port_id,
                          const struct fman_hm_spec *spec);
void fman_hm_node_destroy(struct fman *fm, u8 port_id);
```

`fman_hm_spec` describes the operation: `HM_OP_VLAN_STRIP`, `HM_OP_VLAN_INSERT(vlan_id, tpid)`, `HM_OP_MPLS_PUSH(label, tc, s, ttl)`, `HM_OP_MPLS_POP`. Chained with CC tree if Phase 3b is also enabled.

**Acceptance gate Phase 3c:**

1. Frames arrive at VPP with VLAN tag stripped (visible in VPP `show trace`).
2. Egress frames leave with VLAN tag inserted (visible in tcpdump on peer).
3. No throughput regression vs. Phase 3.
4. Sub-100 ns per-packet HM cost (FMan microcode budget).

### 5.7 Phase 3d — Policer (optional, ucode-210 gated)

**Patch:** `0075-dpaa1-xsk-policer.patch`

VyOS config:

```
set vpp settings hw-offload policer per-qband-limit 2500m
set vpp settings hw-offload policer per-flow-limit  100m
```

Per-qband or per-flow ingress rate-limit using srTCM/trTCM. Yellow/red frames drop in FMan before reaching XSK; FD status field carries the color for downstream observability. Useful for protecting VPP from DoS bursts and for enforcing per-customer SLAs in the edge-router use case.

API surface:

```c
int  fman_policer_install(struct fman *fm, u8 port_id, u8 profile_id,
                          const struct fman_policer_profile *prof);
void fman_policer_destroy(struct fman *fm, u8 port_id, u8 profile_id);
```

LS1046A's Policer subsystem has 256 profile slots; we reserve 8 for FLAVOR=vpp (4 for per-qband + 4 for future per-flow integration with Phase 3b's CC tree).

**Acceptance gate Phase 3d:**

1. Configured 2.5 Gbps per-qband limit: offered load 3 Gbps results in measured ≤ 2.5 Gbps at VPP ingress with red-color drops visible in `ethtool -S fm_pol_red_drops`.
2. No throughput regression vs. Phase 3 with limit unconfigured.
3. Policer config change via `commit` does not flap the netdev.

### 5.8 Phase 3e — CEETM egress shaping (optional, ucode-210 gated)

**Patch:** `0076-dpaa1-xsk-ceetm.patch`

VyOS config:

```
set vpp settings hw-offload egress-shaper strict-priority
set vpp settings hw-offload egress-shaper htb root rate 9.5g
set vpp settings hw-offload egress-shaper htb class 1 rate 4g ceil 6g
set vpp settings hw-offload egress-shaper htb class 2 rate 3g ceil 6g
set vpp settings hw-offload egress-shaper htb class 3 rate 2g ceil 4g
set vpp settings hw-offload egress-shaper htb class 4 rate 0.5g ceil 1g
```

Replace VPP's internal output shaping (software token bucket per worker) with QMan CEETM's HW hierarchical scheduler. CEETM has sub-microsecond accuracy and zero CPU cost; VPP's software shaping costs measurable CPU at multi-Gbps rates.

Wires through `dpaa_eth_ceetm.c` (already in tree but unused by mainline `dpaa_eth.c`) as a root qdisc replacement on the netdev. VPP's own shaper is disabled by VyOS config when this is active.

API surface: leverages existing `qman_ceetm_*` family in `drivers/soc/fsl/qbman/qman_ceetm.c`. No new exports needed; just wire it from the flavor module.

**CEETM Congestion Groups (CGRs) — mandatory for strict-priority profile.** Strict-priority lets low-class traffic queue indefinitely if higher classes saturate the link, which holds UMEM chunks captive and stalls TxConf recycling (see Phase 3 TX path "Strict Tx backpressure"). Each CEETM class queue gets a CGR with a tail-drop threshold sized to roughly 2 ms of class-rate-worth of buffers. Frames exceeding the threshold drop in HW before the buffer accumulates. Drop counts exposed as `ethtool -S fm_ceetm_cgr_drops_class_<N>`. The software in-flight budget (`DPAA1_XSK_TX_MAX_INFLIGHT = 1024`) sits on top as a second line of defense; CGR tail-drop is the first.

**Mutual exclusion with VPP-internal shaping — two enforcement layers.** Documentation alone is insufficient; double-shaping silently degrades throughput and produces unavoidable support tickets.

1. **VyOS config layer.** A commit-confirm validator errors out if `set vpp settings hw-offload egress-shaper` is active while VPP's `startup.conf` has any non-zero internal shaping config. Error message: *"HW egress shaper is incompatible with VPP-internal shaping. Disable VPP internal shaping in `set vpp settings`, or remove `hw-offload egress-shaper`."*
2. **Kernel module layer.** When `pcd_ops->install` activates CEETM, the driver writes a sentinel to `/sys/class/net/<iface>/dpaa/ceetm_active = 1`. VyOS startup scripts read this before launching VPP and refuse to start VPP with internal shaping enabled. Belt and braces.

**Acceptance gate Phase 3e:**

1. Strict-priority: low-priority traffic is starved when high-priority offers ≥ line rate. VPP `show node counters` confirms.
2. HTB: per-class rate caps hold within 0.5% over 60 s windows.
3. `tc qdisc show dev fm1-mac3` shows the CEETM qdisc.
4. No throughput regression at unshaped wire speed.

---

## 6. Validation Matrix

### 6.1 Functional

| Test | M0 | Phase 1 | Phase 2 | Phase 3 | 3b | 3c | 3d | 3e |
|---|---|---|---|---|---|---|---|---|
| `ip link set fm1-mac3 up` | pass | pass | pass | pass | pass | pass | pass | pass |
| Boot on ucode 106: Phase 3b-e install returns `-ENOTSUPP`, no MURAM writes, RSS works | — | — | — | — | pass | pass | pass | pass |
| `xsk_bman_starve` stays 0 under sustained 7 Gbps + bursty mix | — | — | — | pass | pass | pass | pass | pass |
| `xsk_tx_backpressure` fires only under intentional CEETM low-class congestion | — | — | — | pass | pass | pass | pass | pass |
| Detach under load: `fman_port_disable()` link bounce ≤ 50 ms, no PAMU bus error in dmesg, no SError | — | — | pass | pass | pass | pass | pass | pass |
| iperf3 line-rate over LAN | pass | pass | pass | pass | pass | pass | n/a | pass |
| `ip -d link show` reports `xsk_zerocopy` | — | — | pass | pass | pass | pass | pass | pass |
| `xdpsock -i ethX -q 0 -z -r` | — | — | pass | pass | pass | pass | pass | pass |
| `xdpsock -i ethX -q 3 -z -r` (queue-id correctness) | — | — | — | pass | pass | pass | pass | pass |
| DMA-map failure → `xsk_dma_map_fail` increments, attach `-EFAULT` | — | — | pass | pass | pass | pass | pass | pass |
| CC tree shows in debugfs | — | — | — | — | pass | pass | pass | pass |
| HW VLAN strip visible | — | — | — | — | — | pass | pass | pass |
| Policer red-color drops visible | — | — | — | — | — | — | pass | pass |
| CEETM qdisc visible via tc | — | — | — | — | — | — | — | pass |

### 6.2 Thermal (LS1046A `Tj,max` = 105 °C, steady-state headroom ≥ 30 °C)

| Test | Baseline (copy) | Target |
|---|---|---|
| Idle, all ports up, no XSK | TZ0 ~52 °C | TZ0 ≤ 52 °C |
| Idle with XSK bound, copy mode | TZ0 ~62 °C (one core saturated) | n/a |
| Idle with XSK, ZC + need_wakeup | n/a | TZ0 ≤ 53 °C |
| 1 Gbps load, 4 qbands, ZC | n/a | TZ0 ≤ 65 °C |
| 7 Gbps load, 4 qbands, ZC | n/a | TZ0 ≤ 75 °C |

### 6.3 Performance

| Test | Baseline (copy) | Phase 3 target | Phase 3b-e target |
|---|---|---|---|
| 64 B unidirectional pps, 1 qband | ~600 kpps | ≥ 1.5 Mpps | — |
| 64 B unidirectional pps, 4 qbands | n/a | ≥ 5 Mpps | — |
| 1500 B forwarding, single flow | ~2 Gbps | ≥ 7 Gbps | — |
| 1500 B forwarding, 4 flows, 4 cores | ~5 Gbps | ≥ 9 Gbps | ≥ 9 Gbps (no regression) |
| VLAN-tagged 1500 B forwarding | software VLAN | ≥ 7 Gbps | ≥ 8 Gbps with HM offload |
| 64 B with 8-class CEETM shaping | software shaper limits to ~3 Gbps | n/a | ≥ 6 Gbps with CEETM |

### 6.4 Stability

| Test | All phases |
|---|---|
| 24 h iperf3 stress, no oops, no BMan leaks | pass |
| `rmmod fsl_dpaa_eth && modprobe fsl_dpaa_eth` ×100 | pass |
| Module unload while traffic active | pass (clean unregister) |
| `kill -9 vpp_main` while traffic active, recovery ≤ 5 s | pass |
| BMan pool exhaustion → drain → refill without manual intervention | pass |
| VyOS `commit` of hw-offload config under load, no netdev flap | pass |

---

## 7. Rollout, Kconfig Gates, Backout

```
M0  (T+0 weeks): abstraction landed in dpaa_eth.c, FLAVOR=generic only,
                 no behavioural change. Mainline candidate.
M1  (T+2 weeks): Phase 1 (ndo_xsk_wakeup) lands in vyos-ls1046a-build.
                 VyOS rolling vpp flavor RC1.
M2  (T+5 weeks): Phase 2 (XSK pool + PAMU + DCSR). VyOS rolling vpp RC2.
M3  (T+8 weeks): Phase 3 (true ZC + multi-queue). 7 Gbps gate passed.
                 VyOS rolling vpp RC3.
M4  (T+10 weeks): VyOS rolling release ships FLAVOR=vpp as first-class
                  artifact.
M5+ (T+13+ weeks): Phases 3b-e land as separate VyOS releases gated by
                   VyOS config feature flags. Order: 3b CC, 3c HM, 3d Pol,
                   3e CEETM. Each ships when its acceptance gate passes.
```

**Kconfig gates:**

- `CONFIG_DPAA_FLAVOR_OPS` (M0; default y on FLAVOR=vpp/ask2 builds, n on generic)
- `CONFIG_DPAA_XSK_WAKEUP` (Phase 1)
- `CONFIG_DPAA_XSK_POOL` (Phase 2)
- `CONFIG_DPAA_XSK_ZEROCOPY` (Phase 3, depends on `_POOL`)
- `CONFIG_DPAA_XSK_MULTIQ` (Phase 3 multi-queue)
- `CONFIG_DPAA_HW_CC_STEERING` (Phase 3b)
- `CONFIG_DPAA_HW_HM_OFFLOAD` (Phase 3c)
- `CONFIG_DPAA_HW_POLICER` (Phase 3d)
- `CONFIG_DPAA_HW_CEETM` (Phase 3e)

Each defaults `y` *after* its phase ships and is field-verified; until then defaults `n` and legacy path stays primary.

**Runtime modparams for emergency revert without rebuild:**

- `dpaa_eth.disable_zerocopy=1` — forces copy-mode
- `dpaa_eth.disable_hw_offload=1` — disables all of 3b-e
- `dpaa_eth.force_dma_sync=1` — re-enables per-descriptor cache sync

**Fallback paths:**

- Phase 1 regression → re-enable `poll-sleep-usec 100`.
- Phase 3 perf miss → ship with `xdp_features &= ~NETDEV_XDP_ACT_XSK_ZEROCOPY`; Phase 1 thermal win retained.
- 3b-e individual failure → that feature's `pcd_ops->install` returns non-zero, driver `dev_warn()`s, netdev comes up with that feature disabled, other features unaffected.

---

## 8. Open Questions

1. **Queue topology default.** Four worker queues (max parallelism, max BMan footprint) or two (lower footprint, more cores for kernel mgmt)? Needs throughput-vs-mgmt-jitter measurements on production traffic.
2. **UMEM chunk size.** 4096 fits MTU 3290 comfortably. 8192 simplifies future MTU bumps but doubles UMEM footprint.
3. **Per-qband NAPI weight.** Start `napi->weight = 64`; sweep 16-256 under VPP adaptive-polling.
4. **210 ucode upgrade cadence.** NXP releases new 210 sub-versions periodically. Pin a specific sub-version per VyOS release; document in release notes.
5. **CC + HM chain ordering.** RESOLVED in §5.5. Parser → CC (unmodified ingress) → HM (egress from CC node) → QMan. CC keys match wire-format, VPP sees post-HM frames.
6. **CEETM + qband interaction.** Phase 3e's CEETM schedules egress; Phase 3 already pins TX FQs to qbands. Make sure CEETM's LFQs map cleanly onto qband egress FQs without recreating them.
7. **Policer per-flow.** Phase 3d currently lists per-qband + per-flow as targets. Per-flow requires Phase 3b's CC tree to mark flows. Document the dependency explicitly.
8. **A050385 silicon revision.** Mono Gateway DUT is LS1046AE Rev 1.0 (affected revision per DTS `fsl,soc-rev`). Confirm; document R2 behaviour.
9. **BMan seed scaling by link rate.** `DPAA1_XSK_INITIAL_SEED = 8192` is set for 10G XFI. Does the seed need to scale with link rate (8192 for 10G, 4096 for 1G RGMII) to keep refill latency uniform, or is one size sufficient given that drain rate at 1G is proportionally lower? Needs measurement once Phase 3 lands on a 1G port.
10. **`DPAA1_XSK_TX_MAX_INFLIGHT` tuning.** 1024 chunks ≈ 1.5 MB at 1500 B / chunk. Low-water 512. Validate the watermarks against (a) line-rate single-flow at 1500 B, (b) 64 B Mpps with full CEETM strict-priority engaged, (c) bursty VPP scheduling jitter. Adjust if `xsk_tx_backpressure` fires under non-pathological traffic.

---

## 9. References

### Upstream

- **Camelia Groza**, "dpaa_eth: add XDP support" v5 series (7 patches), netdev cover `<cover.1606322126.git.camelia.groza@nxp.com>`, posted 2020-11-25, accepted 2020-12-01 by Jakub Kicinski. Reviewed by Maciej Fijalkowski; acked by Madalin Bucur.
- Magnus Karlsson, "xsk: replace ndo_xsk_async_xmit with ndo_xsk_wakeup", kernel 5.3.
- Björn Töpel, "Introduce AF_XDP buffer allocation API", kernel 5.8. `MEM_TYPE_XSK_BUFF_POOL`, `xsk_buff_alloc/free`, `xsk_buff_dma_map/sync_for_cpu`.
- Maxim Mikityanskiy, "xsk: Add rcu_read_lock around the XSK wakeup", kernel 5.4 stable.
- Marek Majtyka / Lorenzo Bianconi, "xsk: add usage of XDP features flags", kernel 6.3.
- Kurt Kanzenbach (Linutronix), Intel IGB AF_XDP ZC, Linux 6.14. rxdrop 220 Kpps → 350 Kpps for 64 B on i210 (lwn.net/Articles/986006/).

### NXP / Freescale source

- `github.com/nxp-qoriq/qoriq-fm-ucode` README — ucode 106.4.18 public feature matrix; ucode 210.x referenced as LSDK-bundled.
- `github.com/nxp-qoriq/fmlib` — userspace reference for PCD API (NOT used in-kernel).
- NXP LSDK 20.04 / 21.08 FMan Linux Driver User Guide — §8.2.5 PCD configuration, §8.2.6 HM, §8.2.7 Policer, §8.2.8 CEETM.
- NXP LS1046A Reference Manual Rev 4 (06/2020) — FMan v3, BMI port registers, KeyGen scheme registers, PAMU, MURAM, CEETM.
- NXP AN5340 — LS1046ARDB image flashing and ucode location reference.

### Mainline DPAA1 source (Linux 6.18)

- `drivers/net/ethernet/freescale/dpaa/dpaa_eth.{c,h}`
- `drivers/net/ethernet/freescale/dpaa/dpaa_eth_ceetm.{c,h}` (CEETM qdisc, already in tree)
- `drivers/net/ethernet/freescale/fman/{fman.c, fman_port.c, fman_keygen.c, fman_muram.c, fman_sp.c, mac.c}`
- `drivers/soc/fsl/qbman/{qman.c, bman.c, qman_portal.c, bman_portal.c, qman_ceetm.c}`
- `drivers/iommu/fsl_pamu*.c`
- `include/soc/fsl/{qman.h, bman.h}`
- `Documentation/networking/device_drivers/ethernet/freescale/dpaa.rst`

### AF_XDP zero-copy reference implementations

- Intel **i40e/ixgbe/ice/igc/igb** — `drivers/net/ethernet/intel/*/...xsk.c`. Canonical Tx-ZC batching, queue-pair semantics, completion handling.
- **Mellanox mlx5** — multi-queue + adaptive interrupt + wakeup; RCU-safe wakeup.
- **Microchip lan966x**, **virtio-net** — non-Intel ZC implementations.
- **Marvell mvneta** / **mvpp2** — XDP basic + multi-buffer via `page_pool`.
- **Precedent for hardware-buffer-pool + AF_XDP ZC: none in mainline.** DPAA1's BMan is unique. Our XSK-backed BMan pool is novel; cover-letter justification required if upstreamed.

### VPP / VyOS

- `github.com/FDio/vpp/src/plugins/af_xdp/{cli.c, device.c, input.c, output.c}` — VPP master AF_XDP plugin.
- `fd.io/docs/vpp/master/developer/devicedrivers/af_xdp.html` — `num-rx-queues`, `zero-copy`, `prog`, `netns`.
- `docs.vyos.io/en/latest/vpp/configuration/dataplane/{unix,interface}.html` — `poll-sleep-usec`, rx-mode `adaptive`/`polling`/`interrupt`.

### Companion specs

- `specs/ask2-rewrite-spec.md` v1.1 — Track B (FLAVOR=ask2) Path A design. Consumes M0 abstraction and `fman_cc_tree_install()` from this spec.
- `plans/PR14x-DESIGN.md` — PR14a-x foundational fman_pcd subsystem retained by ASK2.

### Kernel documentation

- `docs.kernel.org/networking/af_xdp.html` — UAPI, `XDP_USE_NEED_WAKEUP`, `XDP_SHARED_UMEM`.
- `docs.kernel.org/networking/xdp-rx-metadata.html` — XDP metadata kfuncs; relevant for future `bpf_xdp_metadata_rx_hash()` returning FMan KeyGen hash.

---

## 10. Caveats

- **Ucode 210 licensing.** 210 is NXP-proprietary, not redistributable in public repos. The `vyos-ls1046a-build` flavor=vpp image fetches it from the local LSDK BSP tarball at build time and writes it to the image's SPI flash partition during install. Public mirrors of this spec MUST NOT bundle the binary. Mainline kernel sources contain no 210 reference; the dependency lives entirely in the image-build layer.
- **Ucode version mismatch.** If the running ucode is 106.x or 108.x (e.g. an image flashed from public NXP firmware without LSDK overlay), the `priv->fman_caps` bitmask (§4.1) is populated empty for the 210-only bits. Each `fman_*_install()` returns `-ENOTSUPP` cleanly; the flavor module increments `hw_offload_unavailable` and continues. No MURAM write is attempted on an unsupported layout. Phase 3 still works (KeyGen-RSS only). Document this for operators.
- **No mainline driver has reconciled `MEM_TYPE_XSK_BUFF_POOL` with a hardware-owned buffer pool.** The XSK-backed BMan pool is novel. Risk that `xsk_buff_pool` lifecycle assumes the driver owns refill timing, and we subvert that by handing chunks to BMan and letting hardware refill asynchronously. The detach case (pool=NULL while BMan holds chunks) needs a synchronous drain before declaring detach complete; we do this but the kernel API design did not anticipate it.
- **DPDK DPAA PMD numbers are anecdotal.** NXP claims line-rate on LS1046A without published reproducible benchmarks. The 7 Gbps gate is set against practical home-lab gateway needs, not against a verified DPDK-PMD ceiling. If you want a hard DPDK-PMD vs. VPP-AF_XDP-ZC comparison, plan an explicit shoot-out at M3 acceptance.
- **MTU 3290 figure is folklore-confirmed, not derived from primary source.** `xdp_validate_mtu()` computes `max_contig_data = priv->dpaa_bp->size - priv->rx_headroom` and clamps MTU; the result is ~3290 on a 4096 BP with 256 headroom plus parser metadata. Confirm on DUT with `ip link set fm1-mac3 mtu 3290` (pass) and `mtu 3291` (fail when XDP loaded).
- **`ndo_xsk_wakeup` thermal benefit is theoretical until measured.** The 100% → < 10% claim is based on the mechanism (VPP `poll()` sleeps when driver signals no work); actual numbers depend on traffic patterns. Measure before claiming.
- **CEETM's interaction with VPP-internal shaping is subtle.** If both are active, VPP shapes first (in software), then CEETM shapes again (in hardware). Net effect is "VPP rate", not "CEETM rate". Phase 3e requires VPP-internal shaping to be **off** when CEETM is configured; the VyOS config layer enforces this mutual exclusion.
- **`fman_cc_tree_install()` API is shared with ASK2.** This spec defines the API surface (static tree); ASK2's spec covers the dynamic-key API on top (`cc_add_key`/`cc_remove_key`). The two flavor modules consume the same primitive but in different lifecycle patterns. If ASK2's review reveals the shared API needs reshaping, this spec also updates.
- **A050385 silicon revision check needed.** Mono Gateway DUT is LS1046AE Rev 1.0 (affected revision per DTS `fsl,soc-rev`). Phase 3 enforces ≥ 64 B UMEM headroom which sidesteps the erratum on the data plane. Confirm rev on DUT; document R2 behaviour if/when LS1046AR2 boards appear.
- **The home-lab DUT is one board.** All thermal/performance gates are sized to your Mono Gateway LS1046A. A second DUT (LS1043ARDB or another LS1046ARDB) would harden validation; without it, single-DUT numbers are indicative.

---

## Appendix A — Phase 4 (Optional, Deferred): Single-MAC Dual-Flavor Coexistence

Phase 4 enables FLAVOR=vpp and FLAVOR=ask2 to coexist on the same physical MAC by hardware-bifurcating ingress flows via FMan VSP. Out of scope for v4.0. Triggers if-and-only-if real VyOS deployments need a single image that does both nft-offload flow tracking and AF_XDP zero-copy on the same port (uncommon; typical edge-router deployment uses separate physical ports for kernel-managed and VPP-managed traffic).

If/when Phase 4 lands, it reuses:

- The `fman_cc_tree_install()` API from Phase 3b (extended with VSP-target attribute on actions).
- The `fman_hm_node_install()` API from Phase 3c (per-VSP HM nodes).
- The `fman_policer_install()` API from Phase 3d (per-VSP policer profiles).
- The SEC FQ protected-set discipline (existing in ASK2 spec for IPsec offload).

