# DPAA1 Driver Modernization for LS1046A

**Target:** `mihakralj/vyos-ls1046a-build` → `specs/dpaa1-afxdp-modernization-spec.md`
**Kernel base:** Linux 6.18.x mainline (VyOS rolling release)
**Silicon:** NXP LS1046A — Cortex-A72 ×4 (2 clusters of 2 over CCI-400), FMan v3 Rev>1, QMan, BMan, MURAM (384 KiB), PAMU, SEC 5.4 (CAAM), CEETM, SerDes/XFI PCS, CoreNet
**FMan microcode:** package `fsl_fman_ucode_ls1046a_r1.0_210.x.bin` (NXP LSDK, U-Boot loads from SPI `mtd4`)
**Document version:** v5.0, 2026-05-27
**Supersedes:** v4.4, v4.3, v4.2, v4.1, v4.0, v3.0, v2.x, v0.9

**What changed in v5.0 (2026-05-27):**

- **Spec reorganized along a cross-flavor / flavor-specific axis.** The driver-side work was always more general than its v4.x "FLAVOR=vpp" framing implied. The M0 abstraction, the per-CPU NAPI + dedicated BMan channel restructure (§5.2), `ndo_xsk_wakeup` (§5.3), the CC/HM/Policer/CEETM HW-offload primitives (§5.4–§5.7), and the DCSR observability (§5.8) are all **flavor-neutral kernel capabilities** that `default`, `vpp`, and `ask` consume in different ways. Only the AF_XDP zero-copy datapath (XSK-backed BMan pool, ZC RX/TX branches, `xsk_tx_inflight` backpressure) is genuinely VPP-specific; the ASK2 dynamic CC tree + Host Command reconfig is ASK2-specific. New §5 collects all cross-flavor capabilities; new §6 splits the flavor-specific consumers; new §7 documents the co-existence rules that let all three flavors share one kernel binary.
- **M0 augmentation specified: per-netdev flavor-ops.** v4.x assumed a single global `dpaa_register_flavor_ops()` because only one flavor module was expected per kernel boot. v5.0 makes ops registration per-`dpaa_priv` (keyed on the netdev the flavor module is claiming) so an operator can in principle run VPP on eth3/eth4 and ASK2 on eth0/eth1/eth2 from a single kernel binary. The global helper stays as a thin wrapper for the common case. See §3.4.
- **Documented `default` flavor benefits explicitly.** §5.2 datapath restructure improves *kernel skbuf RX*, not just AF_XDP. §5.4 CC steering backs RPS replacement (`ndo_rx_flow_steer`). §5.5 HM offload backs `NETIF_F_HW_VLAN_CTAG_*`. §5.6 Policer backs tc/nftables ingress offload. §5.7 CEETM is already a tc qdisc waiting to be activated.
- **No technical content from v4.4 is dropped.** All previously-validated facts (M0/M1/M2-s1/M2-s2 dut-validated status, M3-3 step 1 patch 0080 landed, `DPAA1_MIN_UMEM_CHUNK=3840`, `fman_port_disable`-anchored detach, MURAM budget < 8 KiB for VPP / < 52 KiB combined, A050385 headroom requirement, CCI-400 cluster pinning, etc.) carry forward.

**Scope:** All three FLAVORs (`default`, `vpp`, `ask`) of `mihakralj/vyos-ls1046a-build`. ASK2 userspace remains in `specs/ask2-rewrite-spec.md`; this spec defines the kernel-side primitives ASK2 consumes. Phase 4 (single-MAC dual-flavor via VSP bifurcation) is a deferred appendix.
**Status:** Implementation-ready. M0 / M1 / M2-s1 / M2-s2 / **M3-3 step 1** dut-validated; M3-3 steps 2–5 in design.

---

## TL;DR

1. **One driver core, one classifier API, one HW-accelerated AF_XDP stack — three flavors that consume it differently.** Shared substrate: two ops tables (`pcd_ops`, `qmgmt_ops`) installed per-`dpaa_priv`. `default` leaves them NULL (mainline behaviour); `vpp` populates them with UMEM-backed BMan pool + ZC RX/TX hooks; `ask` populates them with dynamic CC tree + Host Command reconfig hooks. RCU-NULL-safe — when no flavor module is loaded the driver is byte-identical to mainline.

2. **The hardware offloads (CC / HM / Policer / CEETM) are not VPP-only.** Each exposes a kernel-side consumer that benefits `default` and `ask` too: CC trees back kernel RPS or ASK2 flow steering; HM nodes hook into `NETIF_F_HW_VLAN_CTAG_*`; Policer profiles back tc/nftables ingress rate-limit offload; CEETM is already a tc qdisc that needs only a root-qdisc CLI verb. The VyOS CLI surfaces land as separate vyos-1x patches per flavor.

3. **FMan ucode 210 is a build dependency, not a runtime option.** U-Boot loads the 210 binary from SPI `mtd4` at SoC bring-up; the kernel inherits a fully-armed FMan with Parser/CC/KeyGen/HM/Policer/IPF/IPR available. The DPAA1 driver only consumes capabilities. Capabilities apply equally across all flavors.

4. **The AF_XDP gap closes in three required phases plus four optional HW-offload phases.** M1 = `ndo_xsk_wakeup`. M2 = XSK-backed BMan pool. M3-3 = ZC RX/TX with qband mapping and cluster-aware NAPI pinning. M3-3b–e add CC, HM, Policer, CEETM. Acceptance bar: idle CPU < 10% on VPP main worker after M1; ≥ 7 Gbps single-stream IPv4 forwarding at < 5% kernel-net CPU per worker after M3-3. Passing the VPP gate also delivers the skbuf-path wins for `default` and `ask` as side-effect.

---

## Milestone Tracker

Single source of truth across sessions. Each milestone tracks the cross-flavor kernel deliverable and the per-flavor consumer wiring. Status: **planned**, **in-progress**, **landed** (in-tree, patch-health-clean), **dut-validated**, **blocked**.

| ID | Cross-flavor capability | §  | Status | Per-flavor consumer | Notes |
|---|---|---|---|---|---|
| **M0**    | Per-`dpaa_priv` ops abstraction + RCU + `NETDEV_XDP_ACT_XSK_ZEROCOPY` advertise | 3 | **dut-validated** | default: NULL ops (no change); vpp/ask: each registers its own ops | Patches 0068/0069a/0070/0072; iperf3 13.1 Gbps baseline unchanged, kallsyms anchors present (2026-05-26) |
| **M1**    | `ndo_xsk_wakeup` trampoline + `af_xdp_pool.ko` skeleton + thermal/idle fix | 5.3 | **dut-validated** | default: any AF_XDP app benefits; vpp: VPP drops `poll-sleep-usec`; ask: latent | Patches 0073/0074; `af_xdp_pool: registered` at t≈0.88s |
| **M2-s1** | XSK pool attach gate: kconfig autoload, `xsk_*` counters, BMan seed + DMA map | 6.1 | **dut-validated** | vpp-only | Patches 0071/0075a/0075b/0075c/0077/0079 + `CONFIG_DPAA_AF_XDP_POOL=y` (7dc1768). DUT 2026-05-27: `bind(XDP_ZEROCOPY)=0`, 12 counters exposed |
| **M2-s2** | XSK pool detach safety + RX-ring drain + 100× bind/unbind churn | 6.1 | **dut-validated** | vpp-only | Patch 0076 + sleep-in-RCU fix `039a50c`. ISO `2026.05.27-0501-rolling` kernel `6.18.33-vyos`: 100× churn → attach_ok=102/detach_ok=102, zero fails/timeouts/RCU WARN |
| **M3-3 step 1** | NAPI bind in `qmap[]` + `xsk_set_rx_need_wakeup` body | 5.2, 6.1 | **dut-validated** | vpp (immediate); default/ask (latent) | Patch **0080** = commit `3e9bb83`; ISO `2026.05.27-0648-rolling`. DUT 2026-05-27: `xsk_bind_test eth4 q=0` ZC+NEED_WAKEUP bind OK, 100/100 sendto wakeups returned 0 (pre-0080 was ENXIO), 100× churn `delta_attach=100/delta_detach=100` clean, ftrace caught 101 hits on `af_xdp_pool_xsk_wakeup<-dpaa_xsk_wakeup`, iperf3 942 Mbps within 0.3% of M2-s2 reference |
| **M3-3 step 2** | **Per-CPU NAPI + dedicated BMan channel restructure** | 5.2 | **planned** | **default: faster skbuf RX (HOLDACTIVE elimination)**; vpp: pre-req for ZC RX branch; ask: same as default | Subpatch 0081; biggest cross-flavor win |
| **M3-3 step 3** | RX ZC branch (xdp_buff translation + bpf_prog_run_xdp + xdp_do_redirect) | 6.1 | **planned** | vpp-only | Subpatch 0082 |
| **M3-3 step 4** | NAPI-hooked BMan refill | 6.1 | **planned** | vpp-only | Subpatch 0083 |
| **M3-3 step 5** | TX ZC submission + TxConf recycle + `xsk_tx_inflight` backpressure | 6.1 | **planned** | vpp-only | Subpatch 0084; closes ≥ 7 Gbps gate |
| **M3-3b**  | CC steering — exact-match HW classifier kernel API | 5.4 | **planned** | **default: `ndo_rx_flow_steer` backend (RPS replacement)**; vpp: qband select; ask: dynamic flow steering primitive (shared API) | ucode-210 gated |
| **M3-3c** | HM offload — VLAN/MPLS strip-insert kernel API | 5.5 | **planned** | **default: `NETIF_F_HW_VLAN_CTAG_RX/TX`**; vpp: L2 header relief; ask: L2 normalization | ucode-210 gated |
| **M3-3d** | Policer — per-flow HW ingress rate-limit | 5.6 | **planned** | **default: tc/nftables ingress offload backend**; vpp: ingress shaping; ask: nft `limit` offload | ucode-210 gated |
| **M3-3e** | CEETM — HW hierarchical egress shaping as tc qdisc | 5.7 | **planned** | **all three: `set qos policy shaper hardware ceetm`**; vpp: per-port mutex with VPP-internal shaper | ucode-210 gated (color-aware drop); CEETM scheduler works on ucode 106 |
| **M4**    | Single-MAC dual-flavor coexistence via VSP bifurcation + "Disable BMI single port" HC | App. A | **deferred** | required only for kernel+VPP on the *same* MAC; typical = per-port (§7.1) |

### Current execution focus

**M3-3 step 1 dut-validated 2026-05-27** as patch `0080` (commit `3e9bb83`, ISO `2026.05.27-0648-rolling`). `priv->qmap[queue_id].napi` is populated at attach with `&per_cpu_ptr(priv->percpu_priv, 0)->np.napi`; the previously-stub `af_xdp_pool_xsk_set_rx_need_wakeup` body iterates `xsk_pool[0..xsk_max_qbands]` under RCU and arms `XDP_USE_NEED_WAKEUP` on every bound pool. DUT gates: 100/100 sendto wakeups returned 0 (pre-0080 was ENXIO); 100× attach/detach churn `delta_attach=100/delta_detach=100` with zero error counters; ftrace caught 101 hits on `af_xdp_pool_xsk_wakeup<-dpaa_xsk_wakeup` per single bind-test run (1 attach probe + 100 sendto wakeups, all via M0 RCU trampoline); iperf3 eth1→lxc200 942 Mbps (within 0.3% of M2-s2 baseline); thermals 48–50 °C across all 5 zones, no rise.

**Next subpatch: M3-3 step 2 (0081)** promotes the cross-flavor BMan + NAPI restructure: one dedicated BMan channel per qband (4 per netdev); per-qband NAPI affined to the QMan SWP that owns the qband's channel. Largest cross-flavor win — `default` and `ask` get measurable skbuf RX gains.

### Reviewer-feedback delta coverage

| Delta | Milestone | Status | Anchor |
|---|---|---|---|
| 1. NAPI-hooked refill + `xsk_bman_starve` + batch 32→256 escalation | M3-3 step 4 | planned (0083) | §6.1.3 |
| 2. CEETM CGR HW tail-drop + `xsk_tx_inflight` ≤ 1024 + low-water 512 + `XDP_USE_NEED_WAKEUP` | M3-3 step 5 + M3-3e | partial — `XDP_USE_NEED_WAKEUP` wired in 0080; `xsk_tx_inflight` planned (0084) | §6.1.4, §5.7 |
| 3. 9-step `fman_port_disable(rxp)`-anchored detach, 10 ms link bounce accepted | M2-s2 | **dut-validated** (sleep-in-RCU fix `039a50c`, 100× churn clean) | §6.1.1 |
| 4. `DPAA1_XSK_INITIAL_SEED=8192` + OQ9 rate-scaling | M2-s1 | landed | §6.1.1 |
| 5. `priv->fman_caps` bitmask + `hw_offload_unavailable` counter + ucode-106 row | M0 augmentation, gates M3-3b–e | planned | §3.5, §4.1 |
| 6. Parser→CC→HM→QMan ordering normative; OQ5 closed | M3-3b doc | landed (doc) | §5.4 |
| 7. Two-layer VPP shaper exclusion (VyOS validator + `ceetm_active` sysfs) | M3-3e + §7.4 | planned | §5.7, §7.4 |

---

## 1. Purpose and Scope

Kernel-side changes to `drivers/net/ethernet/freescale/dpaa/` + FMan/QMan/BMan support code to modernize DPAA1 on LS1046A across all three VyOS flavors. The deliverable is a single kernel binary that:

- Exposes a per-`dpaa_priv` flavor-ops abstraction (M0).
- Implements `ndo_xsk_wakeup`, `XDP_SETUP_XSK_POOL`, advertises `NETDEV_XDP_ACT_XSK_ZEROCOPY`.
- Restructures RX as per-CPU NAPI + dedicated BMan channels — improves *kernel skbuf RX*, not just AF_XDP.
- Delivers true zero-copy RX/TX on AF_XDP via VPP-specific `af_xdp_pool.ko` (≥ 7 Gbps single-stream).
- Exposes HW classification, header manipulation, ingress policing, egress shaping as **kernel APIs**. Each is independently consumable by `default` (RPS, `NETIF_F_HW_VLAN_CTAG_*`, tc/nftables offload, root qdisc), `vpp` (`set vpp settings hw-offload …`), `ask` (ASK2 flavor module).

### 1.1 In scope

- `dpaa_eth.c`, `dpaa_eth_sysfs.c` changes for per-`dpaa_priv` ops, RX datapath restructure, XSK pool lifecycle, qband mapping correctness.
- `drivers/net/ethernet/freescale/fman/` changes for CC-tree install, HM node install, Policer profile install, CEETM channel setup. All called via M0 abstraction; flavor modules own policy.
- Existing `dpaa_eth_ceetm.c` wired as a tc root qdisc replacement.
- VPP plugin integration via existing AF_XDP plugin; no VPP source changes.
- VyOS CLI glue per flavor — follow-on vyos-1x patches not in this spec's direct scope.

### 1.2 Out of scope

- ASK2 userspace, nft flowtable offload userspace bridge — see `specs/ask2-rewrite-spec.md`.
- DPAA2 (`dpaa2-eth`) — already native ZC.
- DPDK DPAA PMD coexistence — RC#31 blocks mixed mode.
- VPP source-tree modifications.
- MTU > 3290 with XDP loaded.
- Microcode authoring or modification.

### 1.3 Cross-flavor reach

The single kernel binary ships in all three ISO flavors. The M0 abstraction makes the hot path RCU-NULL-safe — when no flavor module registers ops, behaviour is byte-identical to mainline DPAA1. §5 capabilities are kernel APIs consumable by:

- **`default`** — through existing kernel offload interfaces (RPS, `NETIF_F_HW_VLAN_CTAG_*`, tc/nftables offload, root qdisc). No flavor module loaded; §5.2 datapath restructure improves skbuf RX automatically. CC/HM/Policer/CEETM activation requires vyos-1x CLI patches (follow-on, not blocking M3-3).
- **`vpp`** — through `af_xdp_pool.ko` (M0 ops consumer) plus VPP `af_xdp` plugin. §5 primitives back the `set vpp settings hw-offload …` CLI verbs. Most demanding consumer and drives the M3-3 acceptance gates.
- **`ask`** — through the ASK2 flavor module (per `specs/ask2-rewrite-spec.md`). Consumes the §5.4 CC tree primitive (as dynamic-key map), the §5.5 HM primitive (for L2 normalization), the §5.6 Policer primitive (for nft `limit` offload). Does NOT consume the §6.1 XSK-backed BMan pool.

Acceptance gates in §8 are written against the VPP datapath because it is the highest bar; passing them also delivers the skbuf-path wins for the other two flavors as a side-effect.

---

## 2. Background

### 2.1 What mainline 6.18.x gives us

`dpaa_eth.c` declares:

```c
net_dev->xdp_features = NETDEV_XDP_ACT_BASIC |
                        NETDEV_XDP_ACT_REDIRECT |
                        NETDEV_XDP_ACT_NDO_XMIT;
```

No `NETDEV_XDP_ACT_XSK_ZEROCOPY`. No `ndo_xsk_wakeup`. No `XDP_SETUP_XSK_POOL`. AF_XDP works only in copy mode through the generic XDP layer.

XDP support was added by Camelia Groza (NXP) in a 7-patch v5 series (netdev cover `<cover.1606322126.git.camelia.groza@nxp.com>`, accepted 2020-12-01). XDP buffers remain `dpaa_bp`-backed kernel pages, recycled through `bman_release()`. Default RSS spreads ingress across 128 PCD FQs using FMan KeyGen 5-tuple hashing. `QM_FQCTRL_HOLDACTIVE` plus stashing flags keep bursts on one CPU portal.

CEETM infrastructure exists in `drivers/soc/fsl/qbman/qman_ceetm.c` and `drivers/net/ethernet/freescale/dpaa/dpaa_eth_ceetm.c` but `dpaa_eth.c` does not wire it as a default qdisc.

### 2.2 What FMan ucode 210 unlocks (over public 106.4.18)

1. **Exact-match Custom Classifier trees** with deep node nesting and per-key statistics — protocol-aware steering as HW config.
2. **Parser soft sequences** for non-standard protocols (VXLAN inner, MPLS label stack, IPv6 fragment).
3. **Header Manipulation (HM) nodes** — insert/remove/replace on VLAN (single + Q-in-Q), MPLS (push/pop with TC propagation), arbitrary byte ranges. Chains with CC trees.
4. **Policer profiles** — srTCM (RFC 2697) and trTCM (RFC 2698) per-flow or per-qband. Color-marking in FD status.
5. **IPv4 fragmentation (IPF) and reassembly (IPR)** with timeout-driven flush.
6. **Host Command (HC) dispatch** for runtime PCD modifications without AttachPCD/DetachPCD save-restore.

210 is signed by NXP, distributed as part of LSDK BSP, loaded by U-Boot from SPI `mtd4`. The M0 capability bitmask (§3.5) gates `pcd_ops->install` accordingly.

### 2.3 The queue-0 alias and why it must die

`dpaa_eth_napi_poll()` operates per `struct dpaa_napi_portal` — per-queue identity does not exist. When AF_XDP binds queue 0 in copy mode, all portal dequeues are treated as queue 0; XSKs bound to other queues see nothing. This violates AF_XDP's `(netdev, queue_id)` contract and forces the `patch-dpaa-xdp-queue-index.py` workaround that collapses all RX FQs onto `queue_index = 0`. M3-3 step 2 introduces true per-qband queue identity.

---

## 3. Architectural Model

### 3.1 Per-`dpaa_priv` flavor-ops abstraction (M0)

`struct dpaa_priv` grows four fields:

```c
const struct dpaa_pcd_ops   *pcd_ops;    /* RCU-protected; per-netdev */
const struct dpaa_qmgmt_ops *qmgmt_ops;  /* RCU-protected; per-netdev */
void                        *flavor_priv;
u32                          fman_caps;  /* §3.5 */
```

```c
struct dpaa_pcd_ops {
    int  (*install)  (struct dpaa_priv *priv);
    void (*teardown) (struct dpaa_priv *priv);
    int  (*reconfig) (struct dpaa_priv *priv, struct nlattr *params);
    int  (*dump_state)(struct dpaa_priv *, struct seq_file *m);
};

struct dpaa_qmgmt_ops {
    int  (*alloc_rx_fqs)   (struct dpaa_priv *, struct list_head *);
    bool (*rx_hook)        (struct dpaa_priv *, struct qman_fq *, const struct qm_dqrr_entry *);
    int  (*xsk_pool_attach)(struct dpaa_priv *, struct xsk_buff_pool *, u16 queue_id);
    int  (*xsk_pool_detach)(struct dpaa_priv *, u16 queue_id);
    int  (*xsk_wakeup)     (struct dpaa_priv *, u32 queue_id, u32 flags);
};
```

Default ops are NULL; call sites short-circuit to mainline. Flavors register **per-netdev** (§3.4).

### 3.2 Hook points in `dpaa_eth_probe()`

```
dpaa_eth_probe(pdev):
  bman/qman portals probed ............. -> -EPROBE_DEFER
  alloc_etherdev_mq(sizeof(*priv), DPAA_ETH_TXQ_NUM)
  fman_get_caps(...) -> priv->fman_caps  <-- §3.5
  fman_port_bind for RX and TX
  dpaa_priv_bp_create
  dpaa_alloc_all_fqs(...)                <-- qmgmt_ops->alloc_rx_fqs
  dpaa_eth_init_tx_port / dpaa_eth_init_rx_port
  dpaa_fq_init for each fq
  dpaa_eth_napi_add
  net_dev->netdev_ops = &dpaa_ops
  net_dev->xdp_features = NETDEV_XDP_ACT_BASIC | ...
                                          <-- pcd_ops->install
  register_netdev(net_dev)
```

### 3.3 Module load ordering

**Current decision (vpp): kconfig autoload (Option B).** `CONFIG_DPAA_AF_XDP_POOL=y` (built-in). `af_xdp_pool_init()` runs at `late_initcall` before `dpaa_eth_probe()`'s `register_netdev()`; `qmgmt_ops` is non-NULL on first publish; `NETDEV_XDP_ACT_XSK_ZEROCOPY` advertised. `MODULE_SOFTDEP` unreachable under this posture.

For ASK2 (when its module lands per ASK2 spec): same Option B posture, or `MODULE_SOFTDEP("pre: ask2_flavor")` with deferred-probe replay.

### 3.4 Per-netdev flavor-ops registration

v4.x assumed a single global registration. v5.0 makes registration per-`dpaa_priv`:

```c
int  dpaa_priv_attach_flavor_ops(struct dpaa_priv *,
                                 const struct dpaa_pcd_ops *,
                                 const struct dpaa_qmgmt_ops *,
                                 void *flavor_priv);
void dpaa_priv_detach_flavor_ops(struct dpaa_priv *);

/* Convenience wrapper: claim every DPAA1 netdev. Common case. */
int  dpaa_register_flavor_ops(const struct dpaa_pcd_ops *,
                              const struct dpaa_qmgmt_ops *, void *);
void dpaa_unregister_flavor_ops(void);
```

Pointers RCU-protected; `synchronize_rcu()` in detach/unregister. Conflict resolution: a second attach attempt on a netdev that already has ops returns `-EBUSY`. The flavor module is responsible for which netdevs to claim — typically via a module parameter (`af_xdp_pool.interfaces=eth3,eth4`) or via a DT-property iterator. Detach is enforced by stashing the caller's `THIS_MODULE` pointer.

### 3.5 FMan capability-detection layer

```c
#define FMAN_CAP_CC_EXACT_MATCH   BIT(0)   /* ucode 210+ */
#define FMAN_CAP_HM_NODES         BIT(1)   /* ucode 210+ */
#define FMAN_CAP_POLICER_TRTCM    BIT(2)   /* ucode 210+ */
#define FMAN_CAP_HC_DISPATCH      BIT(3)   /* ucode 210+ */
#define FMAN_CAP_PARSER_SOFTSEQ   BIT(4)   /* ucode 210+ */
```

Each exported `fman_*_install()` helper returns `-ENOTSUPP` if the corresponding cap bit is clear, **before** touching MURAM. Phase 3b–e flavor code: `install → if (-ENOTSUPP) { priv->hw_offload_unavailable++; continue; }` — fail-soft.

**MURAM budget:**

| Flavor | CC tree | HM nodes | Policer | Total | Of 64 KiB PCD reservation |
|---|---|---|---|---|---|
| `default` (CLI-driven CC) | ~5 KiB | ~1 KiB | ~2 KiB | ~8 KiB | ~12% |
| `vpp` | ~5 KiB | ~1 KiB | ~2 KiB | ~8 KiB | ~12% |
| `ask` (dynamic, up to 255 keys × 150 B) | up to ~38 KiB | ~2 KiB | ~4 KiB | up to ~44 KiB | ~69% |
| Combined `vpp` + `ask` (different netdevs) | ~43 KiB | ~3 KiB | ~6 KiB | ~52 KiB | ~81% |

Combined `vpp` + `ask` on the same MAC (Phase 4) is gated by VSP bifurcation.

---

## 4. LS1046A Hardware Modules — Per-Capability Engagement

| Block | M0 | M1 wakeup | M2 XSK pool | M3-3 datapath | 3b CC | 3c HM | 3d Pol | 3e CEETM |
|---|---|---|---|---|---|---|---|---|
| FMan BMI | — | — | bind (vpp) | bind | — | — | — | — |
| FMan Parser | — | — | — | — | soft seq | — | — | — |
| FMan KeyGen | — | — | — | scheme tune | — | — | — | — |
| FMan CC | — | — | — | — | tree install | — | — | — |
| FMan HM | — | — | — | — | — | node install | — | — |
| FMan Policer | — | — | — | — | — | — | profile install | — |
| FMan MURAM | — | — | — | — | CC tree | HM nodes | profiles | — |
| FMan DCSR | — | observe | observe | observe | observe | observe | observe | observe |
| BMan | — | — | XSK pool (vpp) | dedicated chans (all) | — | — | — | — |
| QMan SWP | — | wakeup | — | per-CPU pin (all) | — | — | — | dequeue |
| QMan FQ | — | — | retire/reactivate | qband mapping | CC targets | — | color-aware drop | CEETM LFQs |
| QMan CEETM | — | — | — | — | — | — | — | full (all via tc) |
| PAMU | — | — | — | — | — | — | — | — |
| CCI-400 | — | — | — | cluster hint | — | — | — | — |
| CoreNet | coherent | coherent | coherent | coherent | coherent | coherent | coherent | coherent |
| SerDes/XFI PCS | observe | observe | observe | observe | — | — | — | — |
| SEC 5.4 (CAAM) | — | — | — | — | — | — | — | — |

**(all)** = applies to default/vpp/ask; **(vpp)** = VPP-flavor only.

### 4.1 FMan v3 with ucode 210 — 384 KiB MURAM, 4 BMI ports, 32 KG schemes. Capability layer (§3.5) gates feature install per-cap; fail-soft on ucode 106.

### 4.2 BMan — 64 pool IDs system-wide.
- Mainline today: 3 IDs/netdev.
- §5.2 dedicated channels (all flavors post-M3-3 step 2): 4 IDs/netdev.
- §6.1 XSK-backed pool (vpp-only): +4 IDs/netdev when AF_XDP bound.
- Worst-case (5 netdevs × 4 dedicated + 2 vpp-attached × 4 XSK): 28 of 64. Fits.

### 4.3 QMan/SWPs — Four SWPs (one per A72), 28 pool channels + 4 dedicated channels available. The 4-dedicated-channel consumption (one per qband) is within budget. Stash effectiveness requires polling CPU = portal CPU; `qmap[]` (§5.2) enforces.

### 4.4 QMan CEETM — 8-level hierarchical scheduler. `dpaa_eth_ceetm.c` already mainline as a tc qdisc. §5.7 wires as root qdisc replacement.

### 4.5 MURAM — Combined < 52 KiB respecting PR14h 64 KiB reservation.

### 4.6 PAMU — **arm64 LS1046A: firmware bypass, kernel driver is PPC-only** (`depends on PPC_E500MC || (COMPILE_TEST && PPC)`). FMan masters DMA directly via dma-direct. No per-attach `fsl_pamu_window_create()` possible. §6.1 uses `xsk_pool_dma_map(pool, priv->mac_dev->dev, DMA_ATTR_SKIP_CPU_SYNC)` only; `DMA_ATTR_COHERENT` NOT passed (no defined semantics on arm64 dma-direct). 9-step detach anchored on `fman_port_disable(rxp)` before `xsk_pool_dma_unmap()`.

### 4.7 CCI-400 vs CoreNet — Two coherency fabrics. CoreNet (A72↔DPAA1) guarantees coherency unconditionally; `DMA_ATTR_SKIP_CPU_SYNC` mappings safe in hot path. CCI-400 (A72 cluster↔cluster) matters for worker pinning — `qmap[]` builder prefers same-cluster RX qband + consumer.

### 4.8 SerDes/XFI PCS — `phy-connection-type = "xgmii"` critical. `xsk_socket__create()` runs `phylink_resolve()` before allowing attach.

### 4.9 FMan DCSR — Exception window at FMan offset `0x0F_0000`. §5.8 wires debugfs `/sys/kernel/debug/dpaa-eth/<iface>/dcsr/{bmi,parser,kg,pol}_err`, rate-limited ≥ 1 ms.

---

## 5. Cross-Flavor Capabilities

Each subsection: kernel-side capability that is **not flavor-specific**. The capability lands as exported API or behavioural change in the driver core. The **Consumers** paragraph enumerates how `default`, `vpp`, `ask` use it.

### 5.1 M0 — Per-`dpaa_priv` flavor-ops abstraction

**Patches:** `0068`, `0069a`, `0070`, `0072`. **Status:** dut-validated.

Structural refactor introducing `struct dpaa_pcd_ops` and `struct dpaa_qmgmt_ops` in `drivers/net/ethernet/freescale/dpaa/dpaa_flavor.h`. Adds the four `priv` fields per §3.1. Wires NULL-checked call sites. Adds the per-netdev registration helpers from §3.4.

**Consumers:**
- **`default`** — passive. Ops stay NULL; hot path byte-identical to mainline (verified DUT 2026-05-26: iperf3 13.1 Gbps baseline).
- **`vpp`** — `af_xdp_pool.ko` registers `qmgmt_ops`.
- **`ask`** — ASK2 flavor module registers `pcd_ops` + subset of `qmgmt_ops` (no XSK callbacks).

**Acceptance gate M0:**
1. `modprobe fsl_dpaa_eth` without flavor module: zero change. ✅
2. `rmmod && modprobe` ×100 no leak. ✅
3. Patches `checkpatch.pl`-clean. ✅

### 5.2 Per-CPU NAPI + dedicated BMan channels (RX datapath restructure)

**Patches:** `0081` (planned, M3-3 step 2). **Status:** planned. **Largest cross-flavor win in the pipeline.**

Introduce "qband" as RX queue identity. `qband ∈ [0..3]` maps to a contiguous band of 32 PCD FQs on a **dedicated** BMan channel and **dedicated** QMan SWP. KeyGen `KGSE_BASEFQID` set so hash mod 32 picks FQ within qband; top 2 bits pick qband.

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

**Queue mapping correctness** (applies to skbuf RX *and* AF_XDP-ZC RX):
1. Delete `data/kernel-patches/patch-dpaa-xdp-queue-index.py` under `CONFIG_DPAA_XSK_MULTIQ=y` (retained at `=n` for emergency rollback).
2. `xdp_rxq_info_reg()` call sites pass real queue index, not 0.
3. Build `priv->qmap[qband] = { fqid_base, swp_id, napi, cpu }` at probe. Exposed via debugfs `/sys/kernel/debug/dpaa-eth/<iface>/qmap`. `WARN_ON(napi != &priv->qmap[qband].napi)` in dequeue.
4. Pin each NAPI to its QMan SWP CPU via `netif_napi_add()` + `netif_set_napi_irq_affinity_hint()`.
5. **Cluster-affinity hint** (§4.7). `workers N ≤ 2` → cores 0,1 (cluster 0); `N = 4` → 2-and-2 split. Exposed read-only as `/sys/class/net/<iface>/dpaa/cluster_map`.

**Dedicated BMan channels.** Replace pool-channel-with-HOLDACTIVE with one dedicated channel per qband. Eliminates HOLDACTIVE arbitration overhead; stash window pre-warms exactly the right CPU. **Measurable cycles-per-packet win in `dpaa_rx_napi_poll()` — benefits *every* RX path, AF_XDP or not.**

**Consumers:**
- **`default`** — gets the restructure automatically. Expected: lower per-packet cycles, better 4-flow RSS scaling, HOLDACTIVE arbitration eliminated. No CLI knobs. Validation: multi-flow kernel forwarding throughput improves vs. pre-M3-3 baseline.
- **`vpp`** — same restructure plus qband identity becomes queue_id for AF_XDP-ZC. Enables `xdpsock -q 3`.
- **`ask`** — same skbuf gain as default; ASK2 flavor module can additionally use qband as a flow-steering target.

### 5.3 `ndo_xsk_wakeup` (M1)

**Patches:** `0070`, `0072`, `0073`, `0074`. **Status:** dut-validated.

```c
static int dpaa_xsk_wakeup(struct net_device *ndev, u32 queue_id, u32 flags)
{
    struct dpaa_priv *priv = netdev_priv(ndev);
    const struct dpaa_qmgmt_ops *ops;

    if (unlikely(!netif_running(ndev))) return -ENETDOWN;
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

For `XDP_WAKEUP_RX`, kick NAPI via `napi_schedule()`. For `XDP_WAKEUP_TX`, poke QMan egress portal via `qman_p_irqsource_add`. After `napi_complete_done()`, arm `xsk_set_rx_need_wakeup()` on every bound pool with `xsk_uses_need_wakeup()` (patch 0080, M3-3 step 1).

**Consumers:**
- **`default`** — latent. Any AF_XDP application in copy mode benefits (no busy-spin). Not VyOS CLI-exposed but available via `xdp-tools`.
- **`vpp`** — VPP drops `set vpp settings unix poll-sleep-usec 100`. Idle CPU ~100% → < 10%.
- **`ask`** — latent.

**Acceptance gate M1 (VPP, has the userspace consumer):**
1. VPP `af_xdp` runs in copy mode without `poll-sleep-usec`. ✅
2. Idle CPU < 10%. ✅
3. Thermal zones stay COOL/WARM. ✅
4. `xsk_ring_prod__needs_wakeup()` returns correct values. ✅

### 5.4 CC steering (M3-3b)

**Patch:** `0085` (planned). **Status:** planned (ucode-210 gated). **CC + HM chain ordering (resolves former OQ5):** Parser → CC (unmodified ingress) → HM (egress from matching CC node) → QMan. CC keys match wire-format; consumers receive post-HM frames.

```c
int  fman_cc_tree_install (struct fman *fm, u8 port_id,
                           const struct fman_cc_static_tree *spec);
int  fman_cc_tree_add_key (struct fman *fm, u8 port_id,
                           const struct fman_cc_key *key, u32 *handle);
int  fman_cc_tree_remove_key(struct fman *fm, u8 port_id, u32 handle);
void fman_cc_tree_destroy (struct fman *fm, u8 port_id);
```

`install` takes a static tree spec; `add_key`/`remove_key` operate dynamically via ucode-210 HC dispatch (no AttachPCD/DetachPCD save-restore, no netdev flap). Each consults `priv->fman_caps` and returns `-ENOTSUPP` on ucode 106. MURAM ≤ 5 KiB (default/vpp static) up to ~38 KiB (ask dynamic).

**Consumers:**
- **`default`** — backs kernel RPS via `ndo_rx_flow_steer` (vyos-1x follow-on: `set system offload classify rule N ...`). HW classifier → right RX FQ → right qband → right CPU. Replaces software RPS table lookup. Static tree; `commit` rebuilds.
- **`vpp`** — `set vpp settings hw-offload classify rule N protocol vxlan target-qband 0` etc. Static tree at `pcd_ops->install`. Rule toggle via `commit` is HC-dispatched (no netdev flap).
- **`ask`** — ASK2 uses dynamic API (`add_key`/`remove_key`) directly. Each nft flow → one CC key. API **shared** between VPP and ASK2; only the lifecycle (static vs dynamic) is flavor-specific.

**Acceptance gate M3-3b:**
1. `cat /sys/kernel/debug/dpaa-eth/<iface>/cc_tree` shows rules.
2. Steering observable on `xdpsock -q N` or `ethtool -S` per-FQ counters.
3. No regression vs. §5.2 baseline.
4. `add_key`/`remove_key` no netdev flap.

### 5.5 HM offload (M3-3c)

**Patch:** `0086` (planned). **Status:** planned (ucode-210 gated).

```c
int  fman_hm_node_install(struct fman *fm, u8 port_id,
                          const struct fman_hm_spec *spec);
void fman_hm_node_destroy(struct fman *fm, u8 port_id);
```

`HM_OP_VLAN_STRIP`, `HM_OP_VLAN_INSERT(vlan_id, tpid)`, `HM_OP_MPLS_PUSH(label, tc, s, ttl)`, `HM_OP_MPLS_POP`. Chains with §5.4 CC tree.

**Consumers:**
- **`default`** — backs `NETIF_F_HW_VLAN_CTAG_RX/TX` in `dev->features`. Kernel `__netif_receive_skb()` sees pre-stripped frames; egress `vlan_dev_hard_start_xmit()` bypassed. CLI: `set interfaces ethernet ethX hw-offload vlan-strip` (vyos-1x follow-on). Sub-100 ns HM cost vs. multi-µs software path.
- **`vpp`** — `set vpp settings hw-offload vlan-strip-on-ingress`. Frees VPP from per-packet L2 manipulation.
- **`ask`** — ASK2 uses HM for L2 normalization on offloaded flows.

**Acceptance gate M3-3c:**
1. Wire shows tagged; consumer sees untagged (and vice versa on egress).
2. No regression. 3. Sub-100 ns per-packet HM cost.

### 5.6 Policer (M3-3d)

**Patch:** `0087` (planned). **Status:** planned (ucode-210 gated).

```c
int  fman_policer_install(struct fman *fm, u8 port_id, u8 profile_id,
                          const struct fman_policer_profile *prof);
void fman_policer_destroy(struct fman *fm, u8 port_id, u8 profile_id);
```

Per-qband or per-flow ingress rate-limit using srTCM/trTCM. Yellow/red drop in FMan; FD status carries color. 256 profile slots; budget 8 per netdev (4 per-qband + 4 per-flow).

**Consumers:**
- **`default`** — backs tc/nftables ingress rate-limit offload via `flow_block_offload`. CLI: `set firewall ... limit offload hw-policer` (vyos-1x follow-on). Replaces CPU-side `tc actions police`.
- **`vpp`** — `set vpp settings hw-offload policer per-qband-limit 2.5g per-flow-limit 100m`. DoS protection, SLA enforcement.
- **`ask`** — nft `limit` offload backend. Each rate-limited rule → one Policer slot.

**Acceptance gate M3-3d:**
1. 2.5 Gbps limit caps offered 3 Gbps to ≤ 2.5 Gbps at consumer, red-drops visible.
2. No regression unconfigured. 3. `commit` no netdev flap.

### 5.7 CEETM egress shaping (M3-3e)

**Patch:** `0088` (planned). **Status:** planned (ucode-210 gated for color-aware drop; CEETM scheduler works on ucode 106).

Wires `dpaa_eth_ceetm.c` as a root qdisc replacement. Sub-µs accuracy + zero CPU cost vs. software token bucket.

**CGRs mandatory for strict-priority.** Each CEETM CQ gets a CGR with tail-drop threshold sized to ~2 ms of class-rate-worth of buffers. Drop counts exposed as `ethtool -S fm_ceetm_cgr_drops_class_<N>`. §6.1.4 software `xsk_tx_inflight` budget sits on top for VPP; CGR tail-drop is the first line for all flavors.

**Consumers:**
- **`default`** — `set qos policy shaper hardware ceetm class 1 rate 4g ceil 6g ...` (vyos-1x follow-on; tc qdisc already mainline). Replaces software `htb`/`tbf`.
- **`vpp`** — `set vpp settings hw-offload egress-shaper strict-priority` (or `htb ...`). Mutex per-port with VPP-internal shaping (§7.4).
- **`ask`** — same as default for kernel-managed traffic.

**Acceptance gate M3-3e:**
1. Strict-priority: low-priority starves under high-priority line-rate.
2. HTB: per-class caps hold ± 0.5% over 60 s.
3. `tc qdisc show dev ethX` shows CEETM.
4. No regression at unshaped wire speed.
5. CGR drops visible under sustained class congestion.

### 5.8 DCSR observability

**Patches:** `0079`. **Status:** dut-validated. Per §4.9, debugfs `/sys/kernel/debug/dpaa-eth/<iface>/dcsr/{bmi,parser,kg,pol}_err`. Reads rate-limited ≥ 1 ms.

**Consumers:** all three flavors equally.

---

## 6. Flavor-Specific Consumers

### 6.1 VPP-only — AF_XDP zero-copy datapath

**Patches:** `0071/0073/0075a/0075b/0075c/0076/0077/0079/0080` (landed/dut-validated); `0082/0083/0084` (planned).

Consumed by the `af_xdp_pool.ko` flavor module that registers `qmgmt_ops->{xsk_pool_attach, xsk_pool_detach, xsk_wakeup}` per §3.4.

#### 6.1.1 XSK pool attach/detach

DPAA1's BMan is a HW buffer-pool manager — FMan RX BMI autonomously dequeues with no per-packet driver involvement. Other ZC drivers use software page pools; DPAA1 cannot swap. **Approach: XSK-backed BMan pool** — allocate a dedicated `dpaa_bp` whose backing memory is the XSK UMEM.

Attach sequence:

1. **Validate constraints, fail closed.**
   - `dev->mtu <= 3290`
   - `xsk_pool_get_rx_frame_size(pool) >= DPAA1_MIN_UMEM_CHUNK` (3840 — derived from MTU 3290 + L2 14 B + DPAA_FD ≤ 64 B + 64 B alignment slack. On arm64 4 KiB-page kernels the only valid `chunk_size` is 4096 → `frame_size = 4096 − headroom(0) − XDP_PACKET_HEADROOM(256) = 3840 = DPAA1_MIN_UMEM_CHUNK`.)
   - First UMEM chunk satisfies `IS_ALIGNED(addr, DPAA_FD_DATA_ALIGNMENT)` (64 B)
   - `xsk_pool_get_headroom(pool) >= priv->tx_headroom`
   - `queue_id < priv->xsk_max_qbands` (4)
2. **DMA-map** `xsk_pool_dma_map(pool, priv->mac_dev->dev, DMA_ATTR_SKIP_CPU_SYNC)`. `priv->mac_dev->dev` = `struct mac_device::dev` (`mac.h:25`). CoreNet-coherent identity-mapped via dma-direct. Failure → `xsk_dma_map_fail++`, `-EFAULT`.
3. **Allocate dedicated BMan pool ID** via `bman_acquire()`. Record in `priv->xsk_bpid[queue_id]`.
4. **Seed**: iterate `xsk_buff_alloc_batch()`, `bman_release()` into new pool. `DPAA1_XSK_INITIAL_SEED = 8192`.
5. **Register memory model**: `xdp_rxq_info_reg_mem_model(..., MEM_TYPE_XSK_BUFF_POOL, pool)`.
6. **Bind NAPI** (M3-3 step 1, patch 0080): `WRITE_ONCE(priv->qmap[queue_id].napi, &per_cpu_ptr(priv->percpu_priv, 0)->np.napi)` — BSP cpu 0 stopgap until M3-3 step 2 lands true per-qband NAPI.
7. **Publish**: `rcu_assign_pointer(priv->xsk_pool[queue_id], pool)`.

**Detach symmetry — FMan BMI quiescence mandatory.** Packet arriving after `xsk_pool=NULL` published but before `xsk_pool_dma_unmap()` still holds a DMA cookie into about-to-be-unmapped UMEM. Detach MUST halt RX BMI:

1. `rcu_assign_pointer(priv->xsk_pool[queue_id], NULL)`.
2. `synchronize_net()`.
3. `fman_port_disable(rxp)` — halts BMI. ~10 ms link bounce. Acceptable (control-plane event).
4. Drain DQRR on port's pool channel.
5. `qman_retire_fq()` on each FQ in qband.
6. Drain dedicated BMan pool via `bman_acquire()`; `xsk_buff_free()` per handle.
7. `xsk_pool_dma_unmap(pool, DMA_ATTR_SKIP_CPU_SYNC)`.
8. Reactivate FQs against original kernel BMan pool; reset to `MEM_TYPE_PAGE_ORDER0`.
9. `fman_port_enable(rxp)`.

Phase 4 (single-MAC dual-flavor) replaces step 3 with surgical ucode-210 "Disable BMI single port" HC.

**Counters:** `xsk_pool_attach_ok/fail`, `xsk_pool_detach_ok/timeout`, `xsk_bman_seed_short`, `xsk_bman_starve`, `xsk_tx_backpressure`, `xsk_dma_map_fail`, `xsk_align_reject`, `xsk_headroom_reject`, `xsk_mtu_reject`.

#### 6.1.2 RX ZC datapath (M3-3 step 3, planned)

Per-frame dequeue callback:
1. `qband = dpaa_fq_to_qband(priv, qm_fd_get_fqid(fd))`.
2. Branch on `rcu_dereference_bh(priv->xsk_pool[qband])`:
   - NULL → mainline skbuf.
   - non-NULL → ZC: phys → `xdp_buff *` via pool index, length/offset from FD, `bpf_prog_run_xdp()`, redirect/drop/TX.
3. `xdp_do_flush()` if any redirected.

#### 6.1.3 NAPI-hooked BMan refill (M3-3 step 4, planned)

1 s workqueue tick meaningless at line rate (2048-buffer pool drains in ~3.5 ms at 7 Gbps/1500 B). BMan-depth probing moves into NAPI poll:
1. `bman_pool_get_count(priv->xsk_bpid[queue_id])` at start of poll.
2. Below `DPAA1_XSK_REFILL_THRESHOLD = 256` → batch-32 refill from XSK fill ring.
3. Below `DPAA1_XSK_MIN_DEPTH = 64` → `xsk_bman_starve++`, escalate batch 32→256.

#### 6.1.4 TX ZC datapath (M3-3 step 5, planned)

1. In NAPI poll after RX: `xsk_tx_peek_release_desc_batch()` up to budget. `xsk_buff_raw_get_dma()` → build `qm_fd` → `qman_enqueue()` on qband's egress FQ.
2. Service TxConf FQ same NAPI iteration with **higher** priority than RX refill: `xsk_tx_completed_addr()` per confirmed FD.
3. If XSK Tx Ring drained but `XDP_USE_NEED_WAKEUP` socket exists, set Tx need-wakeup flag.

**Strict Tx backpressure (two layers):**
1. **CEETM CGR HW tail-drop** (§5.7).
2. **Software in-flight budget.** `priv->xsk_tx_inflight[qband]` (atomic). Above `DPAA1_XSK_TX_MAX_INFLIGHT = 1024` → stop pulling, set Tx need-wakeup, `xsk_tx_backpressure++`. Resume below `DPAA1_XSK_TX_LOW_WATER = 512`. Hysteresis prevents oscillation.

#### 6.1.5 A050385 erratum interaction

XDP workaround relocates Tx frames within 64 B of a 4 KB boundary. XSK cannot relocate (UMEM is app-owned), so we require UMEM ≥ 64 B headroom and refuse `bind(XDP_ZEROCOPY)` otherwise. VPP defaults `XDP_PACKET_HEADROOM = 256`.

**Acceptance gate M3-3 (full VPP datapath):**
1. `xdp-features` reports `NETDEV_XDP_ACT_XSK_ZEROCOPY`.
2. `xdpsock -i ethX -q 3 -z -r` receives traffic. Queue-0 alias dead.
3. ≥ 7 Gbps single-stream IPv4 forwarding at < 5% kernel-net CPU per worker.
4. `perf top -p $(pidof vpp_main)` no `dpaa_rx`, `__alloc_skb`, `skb_copy_from_linear_data`, `memcpy` in top 10.
5. 4-worker scaling: aggregate ≥ 14 Gbps for 1500 B IPv4 on dual SFP+.
6. 24 h iperf3 stress: no oops, no `kmemleak`, no RCU stall.

### 6.2 ASK-only — Dynamic CC tree, HC reconfig, nft offload bridge

**Patches:** TBD per `specs/ask2-rewrite-spec.md`.

ASK2 flavor module registers `pcd_ops->install` (builds initial CC tree) + `qmgmt_ops->{alloc_rx_fqs, rx_hook}` (per-FQ ingress hook for nft offload). Does NOT register XSK callbacks.

Distinct from VPP:
- **Dynamic** CC tree (`fman_cc_tree_add_key`/`remove_key` via HC) — up to 255 keys, runtime add/remove without netdev flap. VPP uses static tree.
- `rx_hook` callback for nft flowtable lookup hits.
- No UMEM, no XSK pool, no BMan pool replacement. Kernel BMan pool stays primary; ASK2 steers flows into different FQs mapping to ASK2 worker threads.

Full design in `specs/ask2-rewrite-spec.md`. This spec only commits to the kernel-side API surface (§5.4/5.5/5.6) being **shared** between VPP and ASK2 consumers.

### 6.3 default-only — passive consumer

No flavor module loaded. `priv->pcd_ops` and `priv->qmgmt_ops` stay NULL. RCU-NULL-safe call sites in `dpaa_eth_probe()`, `dpaa_eth_napi_poll()`, `dpaa_remove()`, `dpaa_xdp()` short-circuit to mainline behaviour.

`default` still gets every cross-flavor §5 benefit that does not require ops registration:
- **§5.2** datapath restructure — automatic, no opt-in.
- **§5.3** `ndo_xsk_wakeup` — available for any AF_XDP consumer (e.g. `xdpsock`).
- **§5.4** CC steering — exposed as RPS backend via `ndo_rx_flow_steer` (vyos-1x follow-on).
- **§5.5** HM offload — exposed as `NETIF_F_HW_VLAN_CTAG_*` (vyos-1x follow-on).
- **§5.6** Policer — exposed as tc/nftables ingress offload backend (vyos-1x follow-on).
- **§5.7** CEETM — exposed as root tc qdisc (existing in mainline; only CLI surface needed).
- **§5.8** DCSR — available as debugfs.

§6.1 XSK-backed BMan pool *not* consumed. §6.2 dynamic CC tree *not* consumed.

---

## 7. Co-existence Rules

### 7.1 Per-port flavor selection (the common case)

```
eth0 (RJ45)  default / kernel-managed
eth1 (RJ45)  default / kernel-managed
eth2 (RJ45)  default / kernel-managed       single kernel binary
eth3 (SFP+)  vpp / AF_XDP-ZC
eth4 (SFP+)  vpp / AF_XDP-ZC
```

`af_xdp_pool.ko` claims netdevs based on `interfaces=eth3,eth4` modparam or DT property. ASK2 module symmetric. Unclaimed netdevs run mainline.

**Validation:** boot one kernel binary, `af_xdp_pool.ko` attached to eth3/4 only, verify eth0/1/2 still kernel-skbuf at line rate.

### 7.2 Single-MAC dual-flavor (deferred — Phase 4)

VPP + ASK2 on the same MAC needs FMan VSP bifurcation: per-VSP FQ sets, per-VSP BMan pool, per-VSP CC sub-tree, per-VSP HM. Out of scope for v5.0; Appendix A.

### 7.3 MURAM budget across flavors

Per §3.5 table. Combined `vpp` + `ask` on different netdevs: ~52 KiB of 64 KiB PCD reservation. Headroom for ASK2 tree growth.

### 7.4 VPP-internal shaper ↔ CEETM mutex (per-port)

§5.7 CEETM and VPP-internal shaping mutually exclusive **per-port**. Two enforcement layers:

1. **VyOS validator (vpp-flavor only).** Commit-confirm errors if `set vpp settings hw-offload egress-shaper interface ethX` active while VPP `startup.conf` has internal shaping for ethX.
2. **Kernel sentinel.** Driver sets `/sys/class/net/<iface>/dpaa/ceetm_active = 1` when CEETM active. VyOS startup reads this before launching VPP, refuses internal shaping for that interface.

For `default`/`ask` (no VPP-internal shaper) only the sysfs sentinel matters.

### 7.5 BMan pool ID budget across flavors

Per §4.2. Five netdevs × 4 dedicated = 20 of 64. Two vpp-attached = +8 XSK pools = 28 of 64. ASK2 (different netdevs) might reserve ~4/netdev × 3 = +12 = 40 of 64. Comfortably within.

### 7.6 ucode-210 capability fallback (all flavors)

If running ucode < 210, §3.5 cap bits zero → every §5.4–§5.6 install returns `-ENOTSUPP`. Flavor module increments `hw_offload_unavailable`. **§5.2 datapath and §5.3 `ndo_xsk_wakeup` are NOT ucode-gated** — work on any FMan ucode.

### 7.7 Acceptance gates and cross-flavor visibility

M3-3 ≥ 7 Gbps gate is a VPP measurement. §8 validation adds rows for measurable default-flavor improvements (multi-flow kernel forwarding throughput, HOLDACTIVE arbitration cycles via `perf`) but does not gate milestones on them — they are side-effects, not destinations.

---

## 8. Validation Matrix

### 8.1 Functional

| Test | M0 | M1 | M2 | M3-3 step 1 | step 2 | step 3+ | 3b | 3c | 3d | 3e |
|---|---|---|---|---|---|---|---|---|---|---|
| `ip link set ethX up` (all flavors) | ✅ | ✅ | ✅ | ✅ | pass | pass | pass | pass | pass | pass |
| Boot on ucode 106: §5.4–5.6 install returns `-ENOTSUPP`, no MURAM writes, RSS works | — | — | — | — | pass | pass | pass | pass | pass | pass |
| **default skbuf RX throughput improves vs. pre-M3-3 baseline (multi-flow)** | — | — | — | — | pass | — | — | — | — | — |
| **default: HOLDACTIVE arbitration cycles drop in `perf top`** | — | — | — | — | pass | — | — | — | — | — |
| **Per-port flavor selection** (§7.1): vpp on eth3/4, default on eth0/1/2, all line rate | — | — | — | — | — | pass | pass | pass | pass | pass |
| `xsk_bman_starve` stays 0 under 7 Gbps + bursty mix (vpp) | — | — | — | — | — | pass | pass | pass | pass | pass |
| `xsk_tx_backpressure` only under intentional CEETM low-class congestion (vpp) | — | — | — | — | — | pass | pass | pass | pass | pass |
| Detach under load: `fman_port_disable` bounce ≤ 50 ms (vpp) | — | — | ✅ | ✅ | pass | pass | pass | pass | pass | pass |
| iperf3 line-rate LAN (all flavors) | ✅ | ✅ | ✅ | ✅ | pass | pass | pass | pass | pass | pass |
| `ip -d link show` reports `xsk_zerocopy` (vpp) | — | — | ✅ | ✅ | pass | pass | pass | pass | pass | pass |
| `xdpsock -q 0 -z -r` (vpp) | — | — | ✅ | ✅ | pass | pass | pass | pass | pass | pass |
| `xdpsock -q 3 -z -r` (queue-id correctness, vpp) | — | — | — | — | — | pass | pass | pass | pass | pass |
| DMA-map failure → `xsk_dma_map_fail`, attach `-EFAULT` (vpp) | — | — | ✅ | ✅ | pass | pass | pass | pass | pass | pass |
| CC tree visible in debugfs | — | — | — | — | — | — | pass | pass | pass | pass |
| HW VLAN strip visible (`tcpdump` tagged, consumer untagged) | — | — | — | — | — | — | — | pass | pass | pass |
| Policer red-drops visible (`ethtool -S fm_pol_red_drops`) | — | — | — | — | — | — | — | — | pass | pass |
| CEETM qdisc visible via `tc qdisc show` | — | — | — | — | — | — | — | — | — | pass |

### 8.2 Thermal (Tj,max = 105 °C, headroom ≥ 30 °C)

| Scenario | Target |
|---|---|
| Idle, all ports up, no XSK | TZ0 ≤ 52 °C |
| Idle with XSK bound, ZC + need_wakeup (vpp) | TZ0 ≤ 53 °C |
| 1 Gbps load, 4 qbands, ZC | TZ0 ≤ 65 °C |
| 7 Gbps load, 4 qbands, ZC | TZ0 ≤ 75 °C |

### 8.3 Performance

| Test | Baseline | M3-3 target | M3-3b–e target |
|---|---|---|---|
| 64 B unidirectional pps, 1 qband (vpp) | ~600 kpps | ≥ 1.5 Mpps | — |
| 64 B unidirectional pps, 4 qbands (vpp) | n/a | ≥ 5 Mpps | — |
| 1500 B forwarding, single flow (vpp) | ~2 Gbps | ≥ 7 Gbps | — |
| 1500 B forwarding, 4 flows, 4 cores (vpp) | ~5 Gbps | ≥ 9 Gbps | ≥ 9 Gbps (no regression) |
| **default: 1500 B kernel forwarding, 4 flows** | TBD pre-M3-3 baseline | improvement measurable in `perf` | — |
| VLAN-tagged 1500 B forwarding (any flavor) | software VLAN | ≥ 7 Gbps | ≥ 8 Gbps with HM offload |
| 64 B with 8-class CEETM shaping (any flavor) | software ~3 Gbps | n/a | ≥ 6 Gbps with CEETM |

### 8.4 Stability

| Test | All phases |
|---|---|
| 24 h iperf3 stress, no oops, no BMan leaks | pass |
| `rmmod fsl_dpaa_eth && modprobe fsl_dpaa_eth` ×100 | pass |
| Module unload while traffic active | pass |
| `kill -9 vpp_main` while traffic active, recovery ≤ 5 s | pass |
| BMan exhaustion → drain → refill without manual intervention | pass |
| VyOS `commit` of hw-offload config under load, no netdev flap | pass |

---

## 9. Rollout, Kconfig Gates, Backout

```
M0  : abstraction landed, default flavor unchanged. Mainline candidate.
M1  : Phase 1 (ndo_xsk_wakeup). VyOS rolling vpp RC1.
M2  : Phase 2 (XSK pool + DCSR). VyOS rolling vpp RC2.
M3-3: Phase 3 (per-CPU NAPI + dedicated BMan channels + true ZC + multi-queue).
       7 Gbps VPP gate; default-flavor side-effect wins land here too.
       VyOS rolling vpp RC3.
M3-3b–e : CC, HM, Policer, CEETM. Each ships when its gate passes, gated by
          VyOS config feature flag. CLI surfaces split between vpp
          (set vpp settings hw-offload …) and default/ask (set qos policy
          shaper hardware …, set system offload …, set firewall … offload).
M4  : Phase 4 single-MAC dual-flavor (deferred).
```

**Kconfig gates:**
- `CONFIG_DPAA_FLAVOR_OPS` (M0)
- `CONFIG_DPAA_XSK_WAKEUP` (M1)
- `CONFIG_DPAA_XSK_POOL` (M2)
- `CONFIG_DPAA_XSK_ZEROCOPY` (M3-3, depends on `_POOL`)
- `CONFIG_DPAA_XSK_MULTIQ` (M3-3 step 2)
- `CONFIG_DPAA_HW_CC_STEERING` (M3-3b)
- `CONFIG_DPAA_HW_HM_OFFLOAD` (M3-3c)
- `CONFIG_DPAA_HW_POLICER` (M3-3d)
- `CONFIG_DPAA_HW_CEETM` (M3-3e)
- `CONFIG_DPAA_AF_XDP_POOL` (vpp-flavor consumer, =y per `08-dpaa1.config`)

**Runtime modparams (emergency revert):**
- `dpaa_eth.disable_zerocopy=1` — forces copy-mode (vpp)
- `dpaa_eth.disable_hw_offload=1` — disables 3b–e
- `dpaa_eth.force_dma_sync=1` — re-enables per-descriptor sync

**Fallback:**
- M1 regression → re-enable `poll-sleep-usec 100`.
- M3-3 perf miss → ship with `xdp_features &= ~NETDEV_XDP_ACT_XSK_ZEROCOPY`; M1 thermal win retained.
- 3b–e individual failure → that feature's install returns non-zero, `dev_warn()`, netdev comes up with that feature disabled, others unaffected.

---

## 10. Open Questions

1. **Queue topology default.** 4 worker queues (max parallelism) vs 2 (lower footprint). Needs throughput-vs-mgmt-jitter measurement.
2. **UMEM chunk size.** 4096 fits MTU 3290; 8192 simplifies future MTU bumps but doubles UMEM footprint.
3. **Per-qband NAPI weight.** Start `napi->weight = 64`; sweep 16-256.
4. **210 ucode upgrade cadence.** Pin per VyOS release; document.
5. **CC + HM chain ordering.** RESOLVED §5.4. Parser → CC → HM → QMan.
6. **CEETM + qband interaction.** Ensure CEETM LFQs map cleanly onto qband egress FQs.
7. **Policer per-flow.** Depends on §5.4 CC tree marking flows. Documented.
8. **A050385 silicon revision.** Mono Gateway DUT is LS1046AE Rev 1.0 per DTS `fsl,soc-rev`. Confirm; document R2.
9. **BMan seed scaling by link rate.** `DPAA1_XSK_INITIAL_SEED = 8192` set for 10G XFI. Scale for 1G? Needs measurement.
10. **`DPAA1_XSK_TX_MAX_INFLIGHT` tuning.** 1024 ≈ 1.5 MB at 1500 B/chunk. Validate against (a) 1500 B line-rate, (b) 64 B Mpps with strict-priority CEETM, (c) bursty VPP scheduling jitter.
11. **default-flavor §5.2 win quantification.** Pre-M3-3 baseline TBD; need iperf3 / `perf top` numbers for a measurable claim.
12. **Cross-flavor CLI surface ownership.** vyos-1x patches for `set qos policy shaper hardware`, `set system offload classify`, `set firewall ... offload policer` are out of this spec's scope but block end-user benefit on `default`/`ask`. Open: who owns those patches and on what schedule?

---

## 11. References

### Upstream
- **Camelia Groza**, "dpaa_eth: add XDP support" v5 (7 patches), netdev `<cover.1606322126.git.camelia.groza@nxp.com>`, accepted 2020-12-01.
- Magnus Karlsson, `ndo_xsk_wakeup`, kernel 5.3.
- Björn Töpel, "Introduce AF_XDP buffer allocation API", kernel 5.8.
- Maxim Mikityanskiy, "xsk: Add rcu_read_lock around the XSK wakeup", kernel 5.4 stable.
- Marek Majtyka / Lorenzo Bianconi, "xsk: add usage of XDP features flags", kernel 6.3.

### NXP / Freescale
- `github.com/nxp-qoriq/qoriq-fm-ucode` — ucode 106.4.18 public feature matrix.
- `github.com/nxp-qoriq/fmlib` — userspace PCD reference.
- NXP LSDK 20.04 / 21.08 FMan Linux Driver User Guide.
- NXP LS1046A Reference Manual Rev 4.
- NXP AN5340.

### Mainline Linux 6.18
- `drivers/net/ethernet/freescale/dpaa/{dpaa_eth.{c,h}, dpaa_eth_ceetm.{c,h}}`
- `drivers/net/ethernet/freescale/fman/{fman.c, fman_port.c, fman_keygen.c, fman_muram.c, fman_sp.c, mac.c}`
- `drivers/soc/fsl/qbman/{qman.c, bman.c, qman_portal.c, bman_portal.c, qman_ceetm.c}`
- `drivers/iommu/fsl_pamu*.c` (PPC-only — see §4.6)
- `include/soc/fsl/{qman.h, bman.h}`
- `Documentation/networking/device_drivers/ethernet/freescale/dpaa.rst`

### AF_XDP ZC reference implementations
- Intel i40e/ixgbe/ice/igc/igb — `drivers/net/ethernet/intel/*/...xsk.c`
- Mellanox mlx5 — multi-queue + adaptive interrupt + wakeup
- Microchip lan966x, virtio-net, Marvell mvneta/mvpp2
- **Precedent for HW-buffer-pool + AF_XDP ZC: none in mainline.** DPAA1's BMan unique. The XSK-backed BMan pool is novel.

### VPP / VyOS
- `github.com/FDio/vpp/src/plugins/af_xdp/` — VPP AF_XDP plugin.
- `fd.io/docs/vpp/master/developer/devicedrivers/af_xdp.html`
- `docs.vyos.io/en/latest/vpp/`

### Companion specs
- `specs/ask2-rewrite-spec.md` — ASK2 Track B; consumes §5.4/5.5/5.6 APIs from this spec.
- `plans/PR14x-DESIGN.md` — PR14a-x foundational fman_pcd subsystem retained by ASK2.

### Kernel documentation
- `docs.kernel.org/networking/af_xdp.html`
- `docs.kernel.org/networking/xdp-rx-metadata.html`

---

## 12. Caveats

- **Ucode 210 licensing.** 210 is NXP-proprietary, not redistributable. The `vyos-ls1046a-build` image fetches from local LSDK BSP tarball at build time. Public mirrors of this spec MUST NOT bundle the binary.
- **Ucode version mismatch.** Running ucode 106/108: `priv->fman_caps` populates empty for 210-only bits. Each `fman_*_install()` returns `-ENOTSUPP` cleanly; flavor module increments `hw_offload_unavailable`. No MURAM write on unsupported layout. M3-3 datapath restructure (§5.2) and `ndo_xsk_wakeup` (§5.3) still work.
- **No mainline driver has reconciled `MEM_TYPE_XSK_BUFF_POOL` with a HW-owned buffer pool.** XSK-backed BMan pool is novel. Risk: `xsk_buff_pool` lifecycle assumes driver owns refill timing; we hand chunks to BMan and let hardware refill async. Synchronous drain on detach addresses this.
- **DPDK DPAA PMD numbers anecdotal.** 7 Gbps gate is set against home-lab gateway needs, not against a verified DPDK-PMD ceiling.
- **MTU 3290 figure is folklore-confirmed.** `xdp_validate_mtu()` math gives ~3290 on a 4096 BP with 256 headroom. Confirm with `mtu 3290` (pass) vs `mtu 3291` (fail when XDP loaded).
- **`ndo_xsk_wakeup` thermal benefit theoretical until measured.** 100% → < 10% claim depends on traffic patterns. Measure before claiming.
- **CEETM ↔ VPP-internal shaping interaction subtle.** Both active means VPP shapes first (software) then CEETM (hardware). §7.4 enforces mutual exclusion per-port.
- **`fman_cc_tree_install()` API shared with ASK2.** If ASK2 review reveals API needs reshaping, this spec also updates.
- **A050385 silicon revision check needed.** Mono Gateway DUT is LS1046AE Rev 1.0 per DTS. Phase 3 enforces ≥ 64 B UMEM headroom which sidesteps erratum on data plane.
- **Single DUT.** All thermal/perf gates sized to one Mono Gateway LS1046A. A second DUT would harden validation; without it numbers are indicative.
- **default-flavor §5.2 win is claimed but unmeasured.** Need pre-M3-3 baseline (iperf3 + `perf top`) before the M3-3-step-2 changelog can quantify the gain. OQ11.
- **Cross-flavor CLI surfaces (default/ask) are vyos-1x follow-on.** End users don't benefit from §5.4–§5.7 on `default`/`ask` until those vyos-1x patches land. OQ12.

---

## Appendix A — Phase 4 (deferred): Single-MAC Dual-Flavor Coexistence

Enables FLAVOR=vpp and FLAVOR=ask coexistence on the same physical MAC by HW-bifurcating ingress flows via FMan VSP (Virtual Storage Profile). Out of scope for v5.0. Triggers if-and-only-if real VyOS deployments need a single image that does both nft-offload and AF_XDP-ZC on the same port (uncommon; typical = §7.1 per-port flavor selection).

If/when Phase 4 lands, it reuses:
- `fman_cc_tree_install()` (§5.4) extended with VSP-target attribute on actions.
- `fman_hm_node_install()` (§5.5) per-VSP HM nodes.
- `fman_policer_install()` (§5.6) per-VSP profiles.
- SEC FQ protected-set discipline (ASK2 spec).
- ucode-210 "Disable BMI single port" HC opcode to avoid full `fman_port_disable()` link bounce on per-VSP detach.