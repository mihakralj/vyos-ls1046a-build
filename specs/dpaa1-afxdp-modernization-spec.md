# DPAA1 Driver Modernization for LS1046A

**Target:** `mihakralj/vyos-ls1046a-build` → `specs/dpaa1-afxdp-modernization-spec.md`
**Kernel base:** Linux 6.18.x mainline (VyOS rolling release)
**Silicon:** NXP LS1046A — Cortex-A72 ×4 (2 clusters of 2 over CCI-400), FMan v3 Rev>1, QMan, BMan, MURAM (384 KiB), PAMU, SEC 5.4 (CAAM), CEETM, SerDes/XFI PCS, CoreNet
**FMan microcode:** package `fsl_fman_ucode_ls1046a_r1.0_210.x.bin` (NXP LSDK, U-Boot loads from SPI `mtd4`)
**Document version:** v5.5, 2026-05-30 (M3-3c `0101` userspace-bridge compile-fix + DUT validation)
**Supersedes:** v5.4, v5.3, v5.2, v5.1, v5.0, v4.4, v4.3, v4.2, v4.1, v4.0, v3.0, v2.x, v0.9

---

## TL;DR

1. **One driver core, one classifier API, one HW-accelerated AF_XDP stack — three flavors that consume it differently.** Shared substrate: two ops tables (`pcd_ops`, `qmgmt_ops`) installed per-`dpaa_priv`. `default` leaves them NULL (mainline behaviour); `vpp` populates them with UMEM-backed BMan pool + ZC RX/TX hooks; `ask` populates them with dynamic CC tree + Host Command reconfig hooks. RCU-NULL-safe — when no flavor module is loaded the driver is byte-identical to mainline.

2. **The hardware offloads (CC / HM / Policer / CEETM) are not VPP-only.** Each exposes a kernel-side consumer that benefits `default` and `ask` too: CC trees back kernel RPS or ASK2 flow steering; HM nodes hook into `NETIF_F_HW_VLAN_CTAG_*`; Policer profiles back tc/nftables ingress rate-limit offload; CEETM is QMan-silicon hierarchical egress shaping (the Linux tc-qdisc driver is NOT in mainline 6.18 and must be forward-ported from the NXP LSDK — see §5.7). The VyOS CLI surfaces land as separate vyos-1x patches per flavor.

3. **FMan ucode 210 is a build dependency, not a runtime option.** U-Boot loads the 210 binary from SPI `mtd4` at SoC bring-up; the kernel inherits a fully-armed FMan with Parser/CC/KeyGen/HM/Policer/IPF/IPR available. The DPAA1 driver only consumes capabilities. Capabilities apply equally across all flavors.

4. **The AF_XDP gap closes in three required phases plus four optional HW-offload phases.** M1 = `ndo_xsk_wakeup`. M2 = XSK-backed BMan pool. M3-3 = ZC RX/TX with qband mapping and cluster-aware NAPI pinning. M3-3b–e add CC, HM, Policer, CEETM. Acceptance bar: idle CPU < 10% on the main AF_XDP worker after M1; ≥ 7 Gbps single-stream IPv4 forwarding at < 5% kernel-net CPU per worker after M3-3. **M3-3 is NOT flavor-specific** — the ZC RX/TX datapath, qband mapping, and cluster-aware NAPI pinning all live in `kernel/common/patches/board/` (the dpaa flavor-ops contract plus `af_xdp_pool.ko`) and exercise identically on `default`, `vpp`, and `ask`. The ≥ 7 Gbps gate can therefore be measured on **any** flavor that brings up an AF_XDP socket on a 10G SFP+ port (eth3/eth4); historical "VPP-only" notes throughout this spec reflect the early test methodology (VPP's `af_xdp` plugin was the first usable XSK producer at hand) and are NOT an architectural constraint. The skbuf-path wins for `default` and `ask` are side-effect deliverables.

---

## Milestone Tracker

DPAA1 driver work is cross-flavor by construction (see §1.3, §7) — `default` / `vpp` / `ask` all share the same kernel binary; whether a given milestone *manifests behavior* on a flavor depends solely on whether that flavor has a userspace consumer loaded (AF_XDP socket, VPP `af_xdp` plugin, ASK2 module). Status: **planned**, **in-progress**, **landed** (in-tree, patch-health-clean), **dut-validated**, **stub-landed**, **blocked**, **deferred**.

Full per-row validation detail (DUT counter readings, ftrace evidence, iperf3 numbers, gate pass/fail per port class) is archived in Qdrant under `topic=dpaa1-afxdp-spec-milestone-archive`. The Notes column below carries only the patch reference, commit SHA, and one-line outcome anchor.

| ID | Capability | §  | Status | Patch / commit / anchor |
|---|---|---|---|---|
| **M0**          | Per-`dpaa_priv` ops abstraction + RCU + `NETDEV_XDP_ACT_XSK_ZEROCOPY` advertise | 3       | **dut-validated**         | `0068`/`0069a`/`0070`/`0072` (2026-05-26) — abstraction passive, mainline-identical |
| **M1**          | `ndo_xsk_wakeup` trampoline + `af_xdp_pool.ko` skeleton                          | 5.3     | **dut-validated**         | `0073`/`0074` — `af_xdp_pool: registered` at t≈0.88s |
| **M2-s1**       | XSK pool attach gate (kconfig autoload, counters, BMan seed, DMA map)            | 6.1     | **dut-validated**         | `0071`/`0075a-c`/`0077`/`0079` + `7dc1768` (2026-05-27) — bind ZC returns 0, 12 `xsk_*` counters live |
| **M2-s2**       | XSK pool detach safety + 100× bind/unbind churn                                  | 6.1     | **dut-validated**         | `0076` + sleep-in-RCU fix `039a50c` — 100× clean, fman_port_disable-anchored detach |
| **M3-3 step 1** | NAPI bind in `qmap[]` + `xsk_set_rx_need_wakeup` body                            | 5.2,6.1 | **dut-validated**         | `0080` = `3e9bb83` (ISO `2026.05.27-0648`) — 100/100 sendto wakeups rc=0 |
| **M3-3 step 2a**| Per-CPU NAPI distribution across qbands (control-plane wiring)                   | 5.2     | **dut-validated**         | `0081` = `ed80517` — `priv->qmap[q].napi = &per_cpu_ptr(...)->np.napi`, 4×bind clean |
| **M3-3 step 2b**| `qmap` debugfs observability node                                                | 5.2     | **dut-validated**         | `0082` = `7b7b634` — `/sys/kernel/debug/af_xdp_pool/qmap` renders all 5 netdevs |
| **M3-3 step 2c**| Dedicated QMan dispatch channels per qband (contiguous-banding)                  | 5.2     | **dut-validated**         | `0082b` = `7026460` — `fqid_base` 32-FQID progression on 5 netdevs × 4 bands, 0 collisions |
| **M3-3 step 3** | RX ZC branch-eligibility probe (`dpaa_fq_to_qband()` + `xsk_rx_branch` counter) | 6.1     | **dut-validated**         | `0083` = `2ad2c0a` — 13th `xsk_*` counter live, RCU-NULL-safe, no perf regression |
| **M3-3 step 4** | NAPI-hooked BMan refill                                                          | 6.1     | **dut-validated**         | `0084 v2` (option-c single-CPU pin) + blocker-A chain `0086`+`0087`+`0088` (`d1b6e30`, ISO `2026.05.28-0149`, run 26549682211) — §6.1.6; 60 s G5 hold: 0 IVCI / 0 BUG/WARN/softlock / 16 refill batches |
| **M3-3 step 5** | TX ZC submit + TxConf recycle + `xsk_tx_inflight` backpressure                   | 6.1     | **dut-validated (hang-fix)** | `0085 v2` — kernel path wired; productive TX waits on TX-capable XSK producer (`xdpsock -t` / VPP `af_xdp` / custom) |
| **M3-3 step 6** | Productive XSK RX delivery via XSKMAP redirect                                   | 6.1     | **dut-validated**         | `0089` (userspace probe stage-3, no kernel change) — §6.1.7; copy-mode `rx_packets`=30 incidental → 296,716 under load (§6.1.8) |
| **M3-3b**       | CC steering — exact-match HW classifier kernel API                               | 5.4     | **in-progress (PCD forward-port)** | `0086` = `c36e6c0` (CC stubs + counter) + `0086a` (productive DT ucode-210 probe, 2026-05-28) + `0086b` (productive 5-tuple `fman_cc_key`/`fman_cc_static_tree` struct contract, 2026-05-28 — replaces `{u32 reserved}` placeholder; ethertype/proto/v4+v6 addr+mask/ports/target_qband/hm_handle, `FMAN_CC_MAX_STATIC_KEYS=32`); `fman_cc_tree_*` bodies remain `-ENOTSUPP`, MURAM CONT_LOOKUP AD encoding pending |
| **M3-3c**       | HM offload — VLAN/MPLS strip-insert kernel API                                   | 5.5     | **bridge dut-validated (feature live on hardware; functional datapath gate pending vyos-1x-024 CLI + traffic gen)** | `0090`/`0090a` (stub + struct contract) + `0099` (productive HM-node install, 2026-05-29) + **`0101` (userspace bridge, 2026-05-30)**. `0099` adds `fman_pcd_manip.c` (HMTD/HMCT MURAM encoder, durable generic INSRT/RMV primitive per RM 8.7.5), publishes the neutral `fman_pcd_hm_hw_{op,spec}` in `fman_pcd.h`, and makes `fman_hm_node_install/destroy` productive (gate on `FMAN_CAP_HM_NODES`, host→neutral translate via `dpaa_hm_spec_to_hw`, delegate via `fman_get_pcd`). **`0101` makes the dormant `0099` install body reachable from userspace** — a new `dpaa_set_features()` `.ndo_set_features` handler in `dpaa_eth.c` that, on a 0→1 transition of `NETIF_F_HW_VLAN_CTAG_RX`, installs a single-op `{FMAN_HM_OP_VLAN_STRIP}` HM node (resolving `struct fman *` via `fman_bind(mac_dev->fman_dev)` and the port id via `fman_port_get_qman_channel_id(mac_dev->port[RX])` — the real in-tree getter the driver already uses in `dpaa_fq_setup()`) and stashes the handle in `priv->hm_vlan_strip_handle`; the feature bit is advertised in `net_dev->hw_features` only when `fman_hm_caps_supported()` so mainline-ucode boards never expose an `-ENOTSUPP` knob. This is the true prerequisite for the vyos-1x `set interfaces ethernet ethX hw-offload vlan-strip` CLI (which only drives `ethtool -K ethX rxvlan on`). Native arm64 compile 0 warn/0 err; `patch-health` 0099 ✓ (0101 standalone ✗ is the documented cumulative-chain false-positive — it depends on every prior `dpaa_eth.c` edit). **DUT-validated 2026-05-30** (ISO `vyos-2026.05.30-0233-rolling-LS1046A-default-arm64`, CI run `26672112397`, commit `02fb1ac`, kernel 6.18.33-vyos): `FMan PCD caps = 0x17 (CC HM POL PARSER)` (HM cap live), `ethtool -k eth0` → `rx-vlan-offload: on` (NOT `[fixed]`) — the HM-cap-gated `NETIF_F_HW_VLAN_CTAG_RX` advertisement and the `dpaa_set_features()` enable-transition both work on real silicon; eth0-eth4 UP, mgmt ping 0% loss, no regression. The functional datapath acceptance gate (§5.5: tcpdump tagged on wire / consumer untagged, sub-100 ns HM cost) remains pending the `vyos-1x-024` CLI consumer + a traffic generator |
| **M3-3d**       | Policer — per-flow HW ingress rate-limit                                         | 5.6     | **install-body-landed (compile-validated; DUT cap-probe ✓, datapath gate pending)** | `0091` (stub) + `0091a` (productive srTCM/trTCM struct contract, 2026-05-28) + **`0100` (productive install body, 2026-05-29 — board patch; `fman_pcd_plcr.c` srTCM/trTCM MURAM encoder in `fsl_dpaa_fman.ko` per RM 8.7.6 rate=exp<<29\|mant<<13 + 256-byte burst quantisation + 16-byte profile record; `dpaa_fman_caps.c` `fman_policer_install`/`destroy` made productive — gate on `FMAN_CAP_POLICER_TRTCM`, validate `cir_bps==0`→`-EINVAL` / trTCM `pir_bps<cir_bps`→`-ERANGE`, translate host→neutral `fman_pcd_plcr_hw_profile`, delegate via `fman_get_pcd()`; native arm64 0 warn/0 err, patch-health `0100 ✓`)**. **DUT-confirmed 2026-05-29** on Mono Gateway DK (ISO 2026.05.29-2339-rolling, kernel 6.18.33-vyos): boot dmesg `FMan PCD caps = 0x17 (CC HM POL PARSER)` — the **POL** (`FMAN_CAP_POLICER_TRTCM`) cap-bit is live-probed by `0086a`, PCD subsystem ready (64 KiB MURAM), all built-in, no regression. The functional datapath acceptance gate (§5.6: 2.5 Gbps cap on offered 3 Gbps, red-drops visible) remains pending — gated on the vyos-1x CLI consumer that does not exist yet |
| **M3-3e**       | CEETM — HW hierarchical egress shaping as tc qdisc                               | 5.7     | **blocked (no mainline CEETM)** | `0092` reserved. **Mainline 6.18.31 ships NO CEETM** — `qman_ceetm.c`, `dpaa_eth_ceetm.{c,h}`, and every `qman_ceetm_*` symbol in `include/soc/fsl/qman.h` are absent (verified 2026-05-29 by tree grep on `work/linux-6.18.31`). DPAA `dpaa_eth.c` has no `ndo_setup_tc`/mqprio wiring either. M3-3e is therefore an SDK-CEETM *forward-port* (`drivers/soc/fsl/qbman/qman_ceetm.c` ~2600 LOC + `dpaa_eth_ceetm.c` ~1900 LOC from NXP LSDK/pre-5.x), NOT a "wire the existing qdisc" task. Scope reclassified — see §5.7 |
| **M3-3 step 7** | True ZC RX (FMan DMAs into XSK-pool BMan chunks via `priv->xsk_bpid` match)      | 6.1     | **sub-increments 1-3 dut-validated; sub-increment 4 deferred** | `0093`-`0096` (sub-increments 1-3, §6.1.10-§6.1.14) — diagnostic `xsk_zc_eligible`/`xsk_zc_rx_armed`/`xsk_fill_guard_block`/`xsk_zc_rx_recovered` (17th-20th `xsk_*`), all zero-datapath-change/dormant; DUT-validated 2026-05-29 (ISO `vyos-2026.05.29-1554-rolling-LS1046A-vpp-arm64`, kernel `6.18.33-vyos`, run `26647449274`). Sub-increment 4 (FMan-port-BPID MURAM reprogram WRITE + `xsk_buff_recv()`) deferred — `fman_pcd_*` forward-port now LANDED (`f307193`) and its MURAM-budget regression fixed (96→64 KiB, `b90ef86`/`cd1b9c5`, §6.1.13); gated only on a fresh ISO (≥`cd1b9c5`) restoring non-NULL `pcd->state` + the three §6.1.12 preconditions. Not required for gate-3 capacity (§6.1.8a/§6.1.8b). |


### Current execution focus

In-flight state as of **2026-05-29**. Closed work-fronts are recorded tersely below with a pointer to their full §6.1.x prose (per-step DUT detail is archived in Qdrant under `topic=dpaa1-afxdp-spec-milestone-archive`). This subsection covers only the current open / just-closed front:

1. **Blocker A — BMan "Invalid Command Verb" — CLOSED** (§6.1.6). `0088` fix: `xsk_pool_dma_map()` uses `priv->rx_dma_dev`. 60 s G5 hold clean.
2. **Blocker B — productive XSK RX delivery — CLOSED** (§6.1.7, §6.1.8). Probe `0089` (`--xskmap`, no kernel change) → 296,716 pkts under load, copy-mode. True-ZC is M3-3 step 7 scope.
3. **Under-load FILL-ring backpressure — STABILIZED** (§6.1.8). Probe-side fix (live `fill_consumer` check, `MAX_BATCH=64`). Driver RX capacity ≥ 7.5 Gbit/s; the probe's 1.33 Gbit/s was an iperf3 single-core receiver artifact, not the NIC.
4. **TX direction — CHARACTERIZED** (§6.1.9). Kernel-skbuf single-stream eth4→backup = **4.93 Gbit/s** (no TSO on FMan v3 Linux). Irrelevant to AF_XDP-ZC TX (bypasses softirq).
5. **M3-3 step 7 sub-increments 1–3 — DUT-VALIDATED** (§6.1.10–§6.1.14). Diagnostic counters `0093`–`0096` dormant on `6.18.33-vyos`; sub-increment 4 deferred — `fman_pcd_*` forward-port now LANDED (`f307193`) and its MURAM-budget regression fixed (96→64 KiB, `b90ef86`/`cd1b9c5`, §6.1.13); gated only on a fresh ISO (≥`cd1b9c5`).
6. **M3-3b/c/d closure — APPROVED DIRECTION (2026-05-29), forward-port IN PROGRESS** (§5.4–§5.6). Operator decisions: (a) the FMan PCD subsystem moves OUT of the `FLAVOR=ask` gate into the **common board stack** (built-in for default/vpp/ask); (b) the L2 MAC-rewrite ceiling (Qdrant `PR14g`) is resolved the **durable** way — a proper in-tree FMan PCD MANIP L2-rewrite primitive (extend `enum fman_pcd_manip_type` + HMCT encoder per RM 8.7.3.x), NOT the ask20 `0058`/`0065` HMD/graft shortcut. The productive bodies are **10,342 lines across 40+ stacked patches** (ask20 `0004`–`0065`), each authored vs 6.18.28 and re-anchored vs the dpaa1 6.18.31 base IN SEQUENCE. Empirical drift test 2026-05-29: raw `0004` `--3way --check` vs vanilla 6.18.31 — Kconfig clean, `Makefile@6`/`fman.c@2509`/`fman.h@382` fail. **Discovered prerequisite:** `0004`'s Makefile hunk references `fman_host_cmd.o`, which is added by ask20 `0001`–`0003` (Host Command prep) — so the forward-port chain starts at `0001`, not `0004`. Re-anchor dataset + full closure order archived in Qdrant (`topic=dpaa1-m3-3bcd-approved-direction-0004-reanchor`).

**Gate-3 entry point — Options A + C DONE (2026-05-28/29), gate is methodology-bound not driver-bound.** Option A (`XDP_DROP` driver-only capacity, §6.1.8a) proved the driver drops **0%** of every offered frame; Option C (multi-flow TCP `-R`, §6.1.8b) hit **5.57 Gbit/s** aggregate RX with softirq distributed across CPU0/1/2 and ~50% headroom on CPU2 — the bottleneck is the single userspace receiver process (CPU3 84% sys), not the DPAA1 RX path. Neither needed a new ISO (the installed `6.18.33-vyos` blocker-A/B build carries the full datapath: 16 `xsk_*` counters, `qmap` debugfs, caps=0x17). **Remaining for a *literal* ≥7 Gbps Gate-3 figure:** multiple iperf3 *server* processes on the generator (so the receiver side divides across cores) — needs generator-side creds (`backup` `admin@192.168.1.3`, §6.1.8 item 6) — or a wire-rate generator (TRex/DPDK-pktgen). This is a lab-provisioning task, NOT a kernel-code task. M3-3 step 7 (true ZC) is not required for gate-3 capacity.

**Operator-visible non-issue noted on the eth0/eth1/eth2 management LAN:** three RJ45 ports DHCP-active on the same `192.168.1.0/24` switch produce martian-source storms. VyOS config / cabling issue, NOT a driver regression. Fix: leave only eth0 plugged in or `set interfaces ethernet ethN address dhcp` on a single port.

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
- Forward-port the SDK CEETM driver (`qman_ceetm.c` + `dpaa_eth_ceetm.c`, absent from mainline — see §5.7) and wire it as a tc root qdisc replacement.
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

**CEETM is absent from mainline** (verified 2026-05-29 on `work/linux-6.18.31`): there is no `drivers/soc/fsl/qbman/qman_ceetm.c`, no `drivers/net/ethernet/freescale/dpaa/dpaa_eth_ceetm.{c,h}`, no `qman_ceetm_*` symbol in `include/soc/fsl/qman.h`, and `dpaa_eth.c` does not even implement `ndo_setup_tc`/mqprio. The CEETM tc-qdisc driver was an NXP-LSDK / pre-5.x out-of-tree component that never landed upstream. M3-3e (§5.7) is therefore a *forward-port* of that SDK code, not a "wire the existing qdisc" task — see §5.7.

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

**Productive detection (patch `0086a`, 2026-05-28).** Cap bits auto-populate from a DT walk of the FMan firmware blob U-Boot loaded into MURAM from SPI mtd4. Layout per `include/soc/fsl/qe/qe.h struct qe_firmware`: bytes 0..3 `__be32 length`, bytes 4..6 magic `"QEF"`, byte 7 version, bytes 8..69 `id[62]` NUL-terminated ASCII. On Mono Gateway DK the property lives at `/proc/device-tree/soc/fman@1a00000/fman-firmware/fsl,firmware` (51652 bytes) and the id reads exactly `"Microcode version 210.10.1 for LS1043 r1.0"` (verified by `od -c` on DUT 2026-05-28). A string parser extracts the major version (210) and lights up `CC_EXACT_MATCH | HM_NODES | POLICER_TRTCM | PARSER_SOFTSEQ`. `HC_DISPATCH` deliberately stays off: per PR13 hardware probe (2026-05-13), the standard 210.10.1 QEF blob does not implement the Host Command doorbell — only the dedicated `qe_firmware` ucode variant does, and we do not ship that. Result on this board is `caps = 0x17` (CC+HM+POL+PARSER). The result is cached in a file-scope `static int` after first probe so the 5× `dpaa_eth_probe()` calls (one per MAC) don't re-walk the DT. The `dpaa_fman_caps.force=<u32>` module parameter still wins as an operator override for dev/CI on hardware where the DT walk would otherwise return 0.

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

### 4.4 QMan CEETM — 8-level hierarchical scheduler in the QMan silicon. **The Linux CEETM tc-qdisc driver is NOT in mainline 6.18.31** (no `qman_ceetm.c`, no `dpaa_eth_ceetm.c`, no `qman_ceetm_*` API — verified 2026-05-29). §5.7 must forward-port the SDK driver before it can be wired as a root qdisc replacement.

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

**Patches:** `0086` (observability stub) + `0086a` (productive DT ucode-210 probe) + `0086b` (productive struct contract). **Status:** struct-contract-landed. `0086` established FMAN_CAP_* detection + `fman_cc_tree_*` decls + four `-ENOTSUPP` stubs + `hw_offload_unavailable` counter (`ethtool -S`). `0086a` auto-populates caps from the DT firmware-blob `qe_firmware.id` walk (§3.5). `0086b` (2026-05-28) promotes the opaque `struct fman_cc_key`/`struct fman_cc_static_tree` `{ u32 reserved; }` placeholders to the productive 5-tuple layout below — fixing the CC API *contract* so downstream consumers (af_xdp_pool qband-select, vyos-1x `set system offload classify` CLI, ASK2 flowtable bridge) build against stable field names today. The four `fman_cc_tree_*` entry-point bodies remain `-ENOTSUPP`; the silicon MURAM AD-table / group-table CONT_LOOKUP encoding (LS1046A RM 8.7.4.1) is a follow-up productive patch. **CC + HM chain ordering (resolves former OQ5):** Parser → CC (unmodified ingress) → HM (egress from matching CC node) → QMan. CC keys match wire-format; consumers receive post-HM frames.

**Productive struct contract (patch `0086b`).** `struct fman_cc_key` carries `ethertype` (host-endian, `FMAN_CC_ETHERTYPE_{ANY,IPV4,IPV6}`), `proto` (`FMAN_CC_PROTO_{ANY,TCP,UDP}`), `is_ipv6`, v4 `src_ip`/`dst_ip` + `src_ip_mask`/`dst_ip_mask`, v6 `src_ip6[16]`/`dst_ip6[16]`, `src_port`/`dst_port`, `target_qband` (the qband to steer matches to per §5.2), and `hm_handle` (chains to a §5.5 HM node; 0 = none). A field participates in the match iff it (or its mask) is non-zero; all multi-byte fields are host-endian at the API and the install body converts to FMan big-endian MURAM layout. `struct fman_cc_static_tree { u16 num_keys; u16 miss_qband; struct fman_cc_key keys[FMAN_CC_MAX_STATIC_KEYS]; }` with `FMAN_CC_MAX_STATIC_KEYS = 32` bounding the ~5 KiB MURAM static budget (§3.5). The productive `add_key()` body will reject `target_qband >= priv->xsk_max_qbands` with `-ERANGE` and a non-zero `hm_handle` on a build without `FMAN_CAP_HM_NODES` with `-ENOTSUPP`. Header compiles clean (`dpaa_fman_caps.o`, ARCH=arm64, zero warnings); `git apply --3way --check` and `patch-health.sh --flavor default` both green.

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

**Patches:** `0090` (stub-landed 2026-05-28, renumbered from `0086` per §6.1.6) + `0090a` (productive struct contract, 2026-05-28) + `0099` (productive HM-node install, 2026-05-29) + `0101` (userspace `.ndo_set_features` bridge, 2026-05-30; compile-fixed 2026-05-30). **Status:** bridge dut-validated (feature bit live on hardware; functional datapath gate pending vyos-1x-024 CLI + traffic gen).

**Userspace bridge (patch `0101`, 2026-05-30).** `0099` made the HM install body productive but left it dormant — `fman_hm_node_install/destroy` are `EXPORT_SYMBOL_GPL` kernel-C entry points with no userspace caller, and a vyos-1x Python CLI patch cannot reach a kernel symbol. `0101` is the missing kernel-side bridge: a new `static int dpaa_set_features(struct net_device *, netdev_features_t)` `.ndo_set_features` handler in `dpaa_eth.c` (wired into `dpaa_ops` next to `.ndo_xsk_wakeup`), plus a `u32 hm_vlan_strip_handle` field in `struct dpaa_priv` (0 = not installed). On a 0→1 transition of `NETIF_F_HW_VLAN_CTAG_RX` (driven by `ethtool -K ethX rxvlan on`, which the forthcoming vyos-1x-024 `set interfaces ethernet ethX hw-offload vlan-strip` CLI emits) it installs a single-op `{ .num_ops = 1, .ops[0].type = FMAN_HM_OP_VLAN_STRIP }` HM node and stashes the returned HMTD handle; on 1→0 it destroys it. The `struct fman *` is resolved via `fman_bind(priv->mac_dev->fman_dev)` (as the `0086a` caps probe does); the FMan port id is resolved via **`(u8)fman_port_get_qman_channel_id(priv->mac_dev->port[RX])`** — the real in-tree getter (declared in `fman_port.h:125`, already `#include`d at `dpaa_eth.c:39` and already used at `dpaa_eth.c:959` in `dpaa_fq_setup()`), NOT the non-existent `fman_mac_get_hw_id()` an earlier draft of this paragraph wrongly claimed (the `0086a` caps probe resolves NO port id — it walks the DT firmware blob, so it was never a port-id source). The first commit of `0101` (`675987d`) called `fman_mac_get_hw_id(priv->mac_dev)` and failed the CI cross-compile with `dpaa_eth.c:3478:15: error: implicit declaration of function 'fman_mac_get_hw_id' [-Werror=implicit-function-declaration]`; the fix (commit `02fb1ac`) substitutes the real getter. The feature is advertised in `net_dev->hw_features` (in `dpaa_netdev_init()`) **only** when `fman_hm_caps_supported()`, so mainline-ucode boards never expose a knob that would `-ENOTSUPP`; on an HM-capable board an unreachable enable still propagates `-ENOTSUPP` so the `ethtool` set fails cleanly. No new includes (`dpaa_eth.c` already pulls in `dpaa_fman_caps.h`, `fman.h`, `mac.h`). Native arm64 compile (`dpaa_eth.o` + `built-in.a`, `LOCALVERSION=-vyos`) is 0 warnings/0 errors and `patch -p1 --dry-run --forward` against the on-disk board-stack tree is clean; the standalone `patch-health.sh board/0101 ✗` is the documented cumulative-dependency-chain false-positive (0101 depends on every prior `dpaa_eth.c` edit 0068→0085 and cannot match pristine context). Staged in CI via an explicit `cp` line in `bin/ci-setup-kernel.sh` after `0100` (it depends on `0099` productive install + `0090a` `struct fman_hm_spec` + `0086a` `fman_hm_caps_supported`, so it must sort last). **This is the true prerequisite for the vyos-1x-024 CLI.** **DUT-validated 2026-05-30** (ISO `vyos-2026.05.30-0233-rolling-LS1046A-default-arm64`, CI run `26672112397`, commit `02fb1ac`, kernel `6.18.33-vyos`): `FMan PCD caps = 0x17 (CC HM POL PARSER)` (HM cap live), `ethtool -k eth0` → `rx-vlan-offload: on` (NOT `[fixed]`) — the HM-cap-gated `NETIF_F_HW_VLAN_CTAG_RX` advertisement and the `dpaa_set_features()` enable-transition both work on real silicon; eth0-eth4 UP, mgmt ping 0% loss, no regression. The §5.5 *functional* datapath acceptance gate below (tcpdump tagged on wire / consumer untagged, sub-100 ns HM cost) remains pending the `vyos-1x-024` CLI consumer + a traffic generator.

`0090` established the `fman_hm_node_install/destroy/caps_supported` `-ENOTSUPP` stubs + `CONFIG_DPAA_HW_HM_OFFLOAD` Kconfig; `0090a` the productive ordered-op-list `struct fman_hm_spec`; `0099` (PR4 of the FMan PCD forward-port, built-in for all flavors via `FSL_FMAN_PCD`) makes the install/destroy bodies productive. `0099` adds `fman_pcd_manip.c` in `fsl_dpaa_fman.ko` — four per-op HMCT encoders (vlan_insert/vlan_strip/mpls_push/mpls_pop) emitting a real FMan generic INSRT/RMV command word (+inline 4-byte payload for inserts) per LS1046A RM 8.7.5, a 16-byte HMTD + 128-byte HMCT MURAM allocation, `HMCD_LAST` walk-termination, and `cfg=TYPE|EXT_HMCT`/`hmcdBasePtr`/`opCode=HMAN_OC=0x35` HMTD finalisation; the returned HMTD MURAM byte offset is the opaque handle a CC action carries as `hm_handle`. Per operator decision 2026-05-29 the L2/VLAN/MPLS rewrites use the durable in-tree generic INSRT/RMV primitive, NOT the ask20 `0058`/`0065` HMD/graft shortcut. The neutral `struct fman_pcd_hm_hw_{op,spec}` is published in `include/linux/fsl/fman_pcd.h` (no circular include); `dpaa_fman_caps.c` gates `fman_hm_node_install` on `FMAN_CAP_HM_NODES` before touching MURAM, translates host→neutral via `dpaa_hm_spec_to_hw()` (VLAN `tpid→tpid_be`/`vlan_id→vid`/`pcp→pcp`; MPLS name-for-name; VLAN_STRIP/MPLS_POP no params), resolves the PCD via `fman_get_pcd(fm)`, and delegates. Native arm64 compile of `fman/` + `dpaa/` with `LOCALVERSION=-vyos` is 0 warnings/0 errors (`fman_pcd_manip.o`, `dpaa_fman_caps.o`, both `built-in.a` clean); `patch-health.sh --flavor default --source release` shows `board/0099` ✓ (the 0092/0097 standalone ✗ are the known cumulative-dependency-chain false-positive — the PCD board series 0092→0099 is a stacking chain). HM cap-bit auto-detected by `0086a` DT probe on Mono Gateway DK ucode 210.10.1. **DUT cap-probe + PCD-init confirmed 2026-05-29** on the Mono Gateway DK (ISO `vyos-2026.05.29-2339-rolling-LS1046A-default-arm64`, kernel `6.18.33-vyos`, built from CI run `26667757734`): boot dmesg `fsl_dpa dpaa-ethernet.0: FMan PCD caps = 0x17 (CC HM POL PARSER)` — the **HM** (`FMAN_CAP_HM_NODES`) cap-bit is live-probed by `0086a`; `fsl-fman 1a00000.fman: fman_pcd: ready (64 KiB MURAM reserved at offset 0x4ac00)` confirms the §6.1.13 MURAM 96→64 KiB regression fix landed (non-NULL `pcd->state`); `CONFIG_DPAA_HW_HM_OFFLOAD=y` + `CONFIG_FSL_FMAN_PCD=y` built-in; no regression (eth0-eth4 UP). **The functional datapath acceptance gate below (insert a CC→HM chain, verify VLAN/MPLS rewrite on egress, sub-100 ns cost) remains pending** — it is gated on the vyos-1x CLI consumer (`set interfaces ethernet ethX hw-offload vlan-strip`) that does not exist yet, plus a traffic generator; the install/destroy bodies and cap detection are now hardware-confirmed.

The stub API mirrors §5.4 CC-steering cadence exactly: `fman_hm_node_install()` returns `-ENOTSUPP` and writes `0` to `*handle` (or `-EINVAL` if `handle == NULL`); `fman_hm_node_destroy()` is an idempotent no-op returning `0`; `fman_hm_caps_supported()` wraps `(dpaa_fman_get_caps() & FMAN_CAP_HM_NODES) != 0`. All three are `EXPORT_SYMBOL_GPL`.

**Productive struct contract (patch `0090a`).** Promotes the opaque `struct fman_hm_spec { u32 reserved; }` placeholder to an ordered op-list so downstream consumers (`af_xdp_pool` egress rewrite, ASK2 flowtable bridge, vyos-1x NAT/VLAN offload CLI) build against stable field names today and degrade gracefully on ucode <210. `enum fman_hm_op_type { FMAN_HM_OP_VLAN_STRIP=0, FMAN_HM_OP_VLAN_INSERT=1, FMAN_HM_OP_MPLS_PUSH=2, FMAN_HM_OP_MPLS_POP=3 }` (values encode into the HMCT opcode; append-only, no renumber). `struct fman_hm_op { enum fman_hm_op_type type; union { struct fman_hm_vlan_insert {u16 vlan_id; u16 tpid; u8 pcp;}; struct fman_hm_mpls_push {u32 label; u8 tc; u8 s; u8 ttl;}; }; }` — STRIP/POP carry no parameters. `struct fman_hm_spec { u8 num_ops; struct fman_hm_op ops[FMAN_HM_MAX_OPS]; }` with `FMAN_HM_MAX_OPS = 8` bounding the ~1 KiB MURAM HM budget (§3.5); the productive `install` body walks `ops[0..num_ops-1]`, emits the matching HMCT records into MURAM, finalises one HMTD whose MURAM offset becomes the returned `handle` (the value a §5.4 CC action's `hm_handle` carries), and rejects `num_ops == 0` or `> FMAN_HM_MAX_OPS` with `-EINVAL`. Applies on top of `0086b` (both edit `dpaa_fman_caps.h`; staged in that order). Header compiles clean (`dpaa_fman_caps.o`, ARCH=arm64, zero warnings); `git apply --3way --check` and `patch-health.sh --flavor default` both green.

```c
int  fman_hm_node_install(struct fman *fm, u8 port_id,
                          const struct fman_hm_spec *spec, u32 *handle);
int  fman_hm_node_destroy(struct fman *fm, u8 port_id, u32 handle);
bool fman_hm_caps_supported(void);
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

**Patches:** `0091` (stub, renumbered from `0087` per §6.1.6) + `0091a` (productive struct contract, 2026-05-28) + board `0100-fman-pcd-plcr-install.patch` (productive install body, 2026-05-29). **Status:** install-body-landed (compile-validated; DUT cap-probe ✓, datapath gate pending). `0091` established `fman_policer_install` returning `-ENOTSUPP`, `fman_policer_destroy` as an idempotent void no-op, `fman_policer_caps_supported()` wrapping `(dpaa_fman_get_caps() & FMAN_CAP_POLICER_TRTCM)`, and `CONFIG_DPAA_HW_POLICER_OFFLOAD` (default y, depends on `DPAA_HW_CC_STEERING`). `0091a` (2026-05-28) promoted the opaque `struct fman_policer_profile { u32 reserved; }` placeholder to the productive srTCM/trTCM layout. Board patch `0100` (2026-05-29) makes the entry-point bodies productive, mirroring the `0098`/`0099` bridge idiom exactly: the fman-side TU `fman_pcd_plcr.c` (compiled into `fsl_dpaa_fman.ko` via `fsl_dpaa_fman-$(CONFIG_FSL_FMAN_PCD)`) owns all MURAM and EXPORTs `fman_pcd_plcr_install(pcd, port_id, profile_id, hw)` / `fman_pcd_plcr_destroy(...)` taking a neutral BE-ready spec `struct fman_pcd_plcr_hw_profile` (published in `include/linux/fsl/fman_pcd.h`); the dpaa-side `dpaa_fman_caps.c` (`fsl_dpa.ko`) gates on the cap-bit, translates host→neutral, calls `fman_get_pcd(fm)`, and delegates. The encoder implements RM §8.7.6 verbatim: `plcr_encode_rate()` produces the exp/mant field (`rate = exp<<29 | mant<<13`, smallest `exp ∈ [0..7]` keeping `mant` in `u16`, saturating at `exp=7,mant=0xffff`, zero-rate→0) using `clk_hz = fman_get_clock_freq(fm) * 1000000ULL` (the **u16-MHz-not-Hz gotcha** — `fman_get_clock_freq` returns MHz, caller must ×1e6 before the `bps·2³¹/clk` division); `plcr_encode_burst()` quantises bursts to 256-byte units (`DIV_ROUND_UP(bytes,256)`, saturate `0xffff`); `plcr_build_mode()` assembles the PMR control word (COLOR_AWARE=0x8000, ALG_TRTCM=0x2000, PACKET_MODE=0x1000, PIR_DISABLED=0x0040 — srTCM sets PIR_DISABLED, trTCM sets ALG_TRTCM); `plcr_program_record()` writes the 16-byte MURAM profile record via 4× `iowrite32be` (word0=mode, word1=CIR, word2=CBS, word3=EIR<<16|EBS). The productive `dpaa_fman_caps.c` `fman_policer_install` gates `!fm||!prof`→`-EINVAL`, `!fman_policer_caps_supported()`→`-ENOTSUPP`, `cir_bps==0`→`-EINVAL`, trTCM `pir_bps<cir_bps`→`-ERANGE`, then `dpaa_plcr_prof_to_hw()` translate + `fman_get_pcd()` delegate; `destroy` gates+delegates idempotently. Profile records are reaped via `pcd->plcr_profiles` (INIT_LIST_HEAD'd in `fman_pcd_init`, `WARN_ON(!list_empty)`'d in `fman_pcd_release`). Native arm64 compile (`ARCH=arm64 LOCALVERSION=-vyos`) 0 warn/0 err — `fman_pcd_plcr.o` + `dpaa_fman_caps.o` both in `built-in.a`, all four `EXPORT_SYMBOL_GPL` resolve via `nm`. `patch-health.sh --flavor default --source release` shows board/0100 ✓ (0092–0097 standalone ✗ is the documented cumulative-dependency-chain false-positive — those patches stack and only validate against the last-committed state). Cap-bit auto-detected by `0086a` DT probe on Mono Gateway DK ucode 210.10.1 (`caps & FMAN_CAP_POLICER_TRTCM` = true).

**Productive struct contract (patch `0091a`).** `enum fman_policer_mode { FMAN_POLICER_MODE_SRTCM = 0 /* RFC 2697: CIR + CBS + EBS */, FMAN_POLICER_MODE_TRTCM = 1 /* RFC 2698: CIR + CBS + PIR + PBS */ }` and `enum fman_policer_color_mode { FMAN_POLICER_COLOR_BLIND = 0, FMAN_POLICER_COLOR_AWARE = 1 }` (append-only, no renumber — values encode into the policer profile control word). `struct fman_policer_profile { enum fman_policer_mode mode; enum fman_policer_color_mode color_mode; u64 cir_bps; u32 cbs_bytes; u64 pir_bps; u32 pbs_bytes; }`. In trTCM mode `pir_bps`/`pbs_bytes` carry the peak rate/burst; in srTCM mode they carry the excess (EBS) parameters — a single struct serves both RFCs. All rates are bits/sec and all bursts are bytes at the API; the productive install body (board `0100`) converts each rate to the FMan exp/mant field and quantises bursts to 256-byte units per RM §8.7.6. The productive body rejects `cir_bps == 0` with `-EINVAL`, trTCM `pir_bps < cir_bps` with `-ERANGE`, and `!fman_policer_caps_supported()` with `-ENOTSUPP`. Header compiles clean (`dpaa_fman_caps.o`, ARCH=arm64, zero warnings); `git apply --3way --check` and `patch-health.sh --flavor default` both green. Applies on top of `0090a` (all three of `0086b`/`0090a`/`0091a` edit `dpaa_fman_caps.h`; staged in that order).

**Neutral bridge contract (board patch `0100`).** The fman-side encoder consumes `struct fman_pcd_plcr_hw_profile { bool trtcm; bool color_aware; u64 cir_bps; u32 cbs_bytes; u64 pir_bps; u32 pbs_bytes; }` (published in `include/linux/fsl/fman_pcd.h`) — a BE-ready neutral spec distinct from the host-facing `struct fman_policer_profile`, keeping the silicon layout out of the dpaa netdev TU and avoiding a circular include. `dpaa_plcr_prof_to_hw()` in `dpaa_fman_caps.c` is the host→neutral translator (maps `FMAN_POLICER_MODE_TRTCM`→`trtcm`, `FMAN_POLICER_COLOR_AWARE`→`color_aware`, copies rate/burst fields verbatim).

```c
int  fman_pcd_plcr_install(struct fman_pcd *pcd, u8 port_id, u8 profile_id,
                           const struct fman_pcd_plcr_hw_profile *hw);
void fman_pcd_plcr_destroy(struct fman_pcd *pcd, u8 port_id, u8 profile_id);
```

```c
int  fman_policer_install(struct fman *fm, u8 port_id, u8 profile_id,
                          const struct fman_policer_profile *prof);
void fman_policer_destroy(struct fman *fm, u8 port_id, u8 profile_id);
bool fman_policer_caps_supported(void);
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

**Patch:** `0092` (reserved, renumbered from `0088` per §6.1.6). **Status:** **blocked — no mainline CEETM**.

**Scope correction (2026-05-29).** Earlier revisions of this spec assumed `dpaa_eth_ceetm.c` and `qman_ceetm.c` ship in mainline and that M3-3e merely needs to "wire the existing qdisc as a root qdisc replacement". A tree audit on `work/linux-6.18.31` proves that assumption **false**: none of `drivers/soc/fsl/qbman/qman_ceetm.c`, `drivers/net/ethernet/freescale/dpaa/dpaa_eth_ceetm.{c,h}`, or any `qman_ceetm_*` symbol in `include/soc/fsl/qman.h` exists, and `dpaa_eth.c` has no `ndo_setup_tc`. The CEETM tc-qdisc was an NXP-LSDK / pre-5.x out-of-tree driver dropped on the road to mainline.

**Revised work breakdown.** M3-3e is now a multi-part *forward-port*, materially larger than 3b/3c/3d:

1. **QMan CEETM core** — forward-port `qman_ceetm.c` (~2600 LOC: LNI/channel/CQ/CCG/LFQ allocation, shaper-rate programming, CGR config) plus the `qman_ceetm_*` API into `include/soc/fsl/qman.h`. Must reconcile with the mainline 6.18 `qman.c` portal/FQ APIs that have drifted since the SDK fork.
2. **DPAA CEETM qdisc** — forward-port `dpaa_eth_ceetm.{c,h}` (~1900 LOC: the `Qdisc_ops`/`tcf` glue, class hierarchy, `mqprio`-style mapping) and add `ndo_setup_tc` to `dpaa_eth.c`'s netdev_ops.
3. **Stub-first landing (matches 3b/3c/3d cadence)** — once the core forward-port compiles, the *first* board patch should be a `dpaa_fman_caps`-style stub: a Kconfig `CONFIG_DPAA_HW_CEETM`, an opaque qdisc-config struct, and `-ENOTSUPP` entry points, so downstream CLI consumers can wire calls before the full datapath lands. Productive shaping then follows.

The scheduler core works on any ucode (it is QMan silicon, not FMan PCD); only **colour-aware drop** is ucode-210 gated. This is the one M3-3 sub-milestone that needs no FMan-PCD subsystem — but it needs the QMan CEETM forward-port instead.

**CGRs mandatory for strict-priority** (design target, once the forward-port lands). Each CEETM CQ gets a CGR with tail-drop threshold sized to ~2 ms of class-rate-worth of buffers. Drop counts exposed as `ethtool -S fm_ceetm_cgr_drops_class_<N>`. §6.1.4 software `xsk_tx_inflight` budget sits on top for VPP; CGR tail-drop is the first line for all flavors.

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

#### 6.1.3 NAPI-hooked BMan refill (M3-3 step 4, dut-validated kernel-skbuf path)

1 s workqueue tick meaningless at line rate (2048-buffer pool drains in ~3.5 ms at 7 Gbps/1500 B). BMan-depth probing moves into NAPI poll:
1. `bman_pool_get_count(priv->xsk_bpid[queue_id])` at start of poll.
2. Below `DPAA1_XSK_REFILL_THRESHOLD = 256` → batch-32 refill from XSK fill ring.
3. Below `DPAA1_XSK_MIN_DEPTH = 64` → `xsk_bman_starve++`, escalate batch 32→256.

#### 6.1.4 TX ZC datapath (M3-3 step 5, dut-validated kernel-skbuf path)

1. In NAPI poll after RX: `xsk_tx_peek_release_desc_batch()` up to budget. `xsk_buff_raw_get_dma()` → build `qm_fd` → `qman_enqueue()` on qband's egress FQ.
2. Service TxConf FQ same NAPI iteration with **higher** priority than RX refill: `xsk_tx_completed_addr()` per confirmed FD.
3. If XSK Tx Ring drained but `XDP_USE_NEED_WAKEUP` socket exists, set Tx need-wakeup flag.

**Strict Tx backpressure (two layers):**
1. **CEETM CGR HW tail-drop** (§5.7).
2. **Software in-flight budget.** `priv->xsk_tx_inflight[qband]` (atomic). Above `DPAA1_XSK_TX_MAX_INFLIGHT = 1024` → stop pulling, set Tx need-wakeup, `xsk_tx_backpressure++`. Resume below `DPAA1_XSK_TX_LOW_WATER = 512`. Hysteresis prevents oscillation.

#### 6.1.5 A050385 erratum interaction

XDP workaround relocates Tx frames within 64 B of a 4 KB boundary. XSK cannot relocate (UMEM is app-owned), so we require UMEM ≥ 64 B headroom and refuse `bind(XDP_ZEROCOPY)` otherwise. VPP defaults `XDP_PACKET_HEADROOM = 256`.

#### 6.1.6 Blocker A — BMan "Invalid Command Verb" (patches `0086`/`0087`/`0088`) — 2026-05-28

Board patches `0086`/`0087`/`0088` closed the BMan err-IRQ `BM_EIRQ_IVCI` ("Invalid Command Verb") seen on the DUT G5 reproducer (xsk-bind-probe + iperf3 UDP flood). **Root cause (`0088`):** `xsk_pool_dma_map()` must target `priv->rx_dma_dev` (the FMan RX port device with the 40-bit DMA mask owning the BMan FBPR window programmed by U-Boot), NOT `priv->mac_dev->dev` (32-bit mask, wrong IOMMU group) — DMA addresses resolved outside BMan's FBPR window so the 40-bit address bytes in RCR slots were rejected as IVCI. `0086` (8-buffer chunked release) and `0087` (zero stack bpid residue) were necessary-but-insufficient precursors. **Closed 2026-05-28** (ISO 2026.05.28-0149, commit `d1b6e30`, run 26549682211): 60 s G5 hold → 0 IVCIs, 0 BUG/WARN/stall, clean attach/detach both cycles.

**CI-pipeline invariant discovered here:** `bin/ci-setup-kernel.sh` stages board patches via explicit per-patch `cp` lines (no glob); `patch-health.sh` does NOT cross-check that the `cp` line exists, so a missing `cp` silently ships an ISO without the patch (ISO 2026.05.28-0116 / commit `b57dd6a` shipped without any of 0086/0087/0088). Every new board patch requires a matching `cp` line AND a post-build grep of the staged source for the expected token. (Original 0086/0087 reservation for HM/Policer is superseded — those re-numbered to `0090`/`0091`.)

> Full diagnosis chain, verification matrix, and counter snapshots: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.7 Blocker B — productive RX delivery via XSKMAP redirect (patch `0089`) — 2026-05-28

Pre-`0089` an `XDP_ZEROCOPY`-bound AF_XDP socket on DPAA1 observed `rx_packets = 0` despite healthy driver mechanics: `dpaa_run_xdp()` early-returns `XDP_PASS` when no XDP program is attached, so the FD travelled the unchanged skbuf path and the bound XSK socket saw nothing. **`0089` (userspace probe stage-3, no kernel change)** adds an opt-in `--xskmap` flag to `bin/dpaa1-xsk-bind-probe.py` that creates an XSKMAP, loads a 6-insn `bpf_redirect_map()` XDP program (DRV-mode attach, SKB fallback), binds `XDP_ZEROCOPY`, then `BPF_MAP_UPDATE_ELEM`s the bound socket into the map. **Closed blocker B** (DUT G5, ISO 2026.05.28-0149): `rx_packets` 0 → 30, DRV-mode attach honoured, 0 IVCI/BUG/stall, blocker-A invariants held under XSKMAP load.

**Mode is copy-mode, not zero-copy.** The page-backed `xdp_buff` is copied into a UMEM chunk; true ZC (FMan DMA directly into XSK-pool BMan chunks) is the follow-on M3-3 step 7 work scoped to patches `0093+`, structured as the three-mechanism plan — **Recognise** (match `fd->bpid` against `priv->xsk_bpid[band]`), **Recover** (`xsk_buff_recv()` instead of `build_skb()`), **reProgram** (set the FMan RX port primary BPID to the XSK pool BPID).

**Acceptance gate M3-3 (full VPP datapath):** (1) `xdp-features` reports `NETDEV_XDP_ACT_XSK_ZEROCOPY`; (2) `xdpsock -i ethX -q 3 -z -r` receives traffic; (3) ≥ 7 Gbps single-stream IPv4 fwd at < 5% kernel-net CPU/worker; (4) `perf top` clean of `dpaa_rx`/`__alloc_skb`/`memcpy`; (5) 4-worker aggregate ≥ 14 Gbps dual-SFP+; (6) 24 h iperf3 stress no oops/leak/stall.

> Probe-side fixes (`bpf_attr` `__aligned_u64` pad, `XDP_FLAGS_UPDATE_IF_NOEXIST` removal), full verification matrix, counter snapshots: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.8 Blocker B — under-load FILL-ring backpressure crash + fix — 2026-05-28

The first under-load probe run (5 Gbit/s UDP flood) panicked in softirq: an unconstrained userspace recycle loop bumped `fill_producer` without checking `fill_consumer`, overran the 256-slot FILL ring, and handed the same UMEM chunk to `xp_alloc()` twice → `xsk_buff_pool` free-list corruption (`list_add corruption` → `dcache_clean_poc` paging fault → 52 s soft-lockup). **Probe fix (no kernel change):** read the kernel `fill_consumer` every iteration and refuse to write a FILL slot when in-flight `>= N_FRAMES` (`skipped_recycle` counter; "drop > corrupt"); plus `MAX_BATCH=64` and per-line flush. Post-fix DUT run sustained `rx_packets = 296 716` / 427 MB clean, 0 crashes, 0 `xsk_bman_starve`.

**Key finding — the "1.3 Gbit/s ceiling" is an iperf3 UDP-receiver artifact, not a driver limit.** `/proc/net/snmp` showed `RcvbufErrors == InErrors` byte-for-byte (loss at socket enqueue, single-threaded `recvmmsg()` on a 1.6 GHz Cortex-A72 caps ~150–200 kpps/core), while `ethtool -S` showed 0 NIC/BMan/FQ drops and `lxc202↔backup` TCP proved the L2 fabric does 7.5 Gbit/s. **Diagnostic rule:** on receiver-side UDP loss, check `/proc/net/snmp Udp:` BEFORE blaming wire/switch/driver — `RcvbufErrors == InErrors` means it is the userspace consumer.

**Crash exposed a kernel-side invariant for step 7:** the eventual true-ZC path needs a FILL-ring-empty/double-release guard (the under-load failure mode here was free-list corruption, not a clean `NULL` from `xp_alloc()`). Stored under tag `dpaa1+xsk_bman_refill+fill_ring_invariant` for the step-7 design pass (addressed by `0095`, §6.1.12). Gate-3 entry options for the next session: **A** XDP_DROP driver-only capacity (§6.1.8a), **B** `xdpsock` C-based probe, **C** multi-process iperf3 (§6.1.8b).

> Full crash trace, probe-fix detail, cross-check measurement table, and lab topology/creds: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.8a Option A — XDP_DROP driver-only RX capacity (tool `bin/dpaa1-xdp-rxcap.py`) — 2026-05-28

`bin/dpaa1-xdp-rxcap.py` (standalone, no libbpf/clang) attaches a 2-insn `XDP_DROP` program (DRV-mode, SKB fallback) and samples driver `rx_packets`/`rx_bytes` + `qman cg_tdrop`/`qman fq tdrop`/`rx dma error`/`rx fifo error` deltas across a hold window. XDP_DROP frees each frame in-driver with no skb/socket/userspace consumer, so only the driver RX path advances. **Finding: the driver dropped 0% at every offered rate the lab could generate** (zero qman/dma/fifo drops, zero net-core `rx_dropped`), while the kernel-socket baseline at 2 Gbit/s offered lost 51% entirely at socket enqueue — direct counter-level proof that **Gate 3 is consumer/methodology-bound, not driver-bound.** The literal ≥7 Gbit/s number could not be hit because no lab traffic source reaches it: iperf3 is structurally incompatible with XDP_DROP (control channel stalls), nping is CPU-bound ~1 Gbit/s and doesn't scale with parallelism, no kernel pktgen on the backup. A wire-rate generator (TRex/DPDK-pktgen) is needed for a *literal* Gate-3 pass; the tool is proven and reusable for that re-measurement.

> Full result matrix, methodology notes, and lab creds/recipe: Qdrant `topic=dpaa1-afxdp-gate3-option-a-measurement`.

#### 6.1.8b Option C — multi-flow TCP `-R` RX scaling — 2026-05-29

Ran on DUT image `6.18.33-vyos` (full AF_XDP datapath live: 16 `xsk_*` counters, `FMan PCD caps = 0x17`, `af_xdp_pool: registered`) — pure userspace measurement, no new ISO needed. DUT-as-receiver TCP `-R` aggregate **peaks at `-P 4` = 5.57 Gbit/s** then declines (`-P 8` 4.12, `-P 16` 3.75) due to single-iperf3-server contention on the generator. `mpstat -P ALL` during `-P 16`: RX softirq genuinely distributed across CPU0/1/2 (79.9 / 97.6 / 49.1 %soft, CPU2 ~50% headroom) while the single userspace receiver process saturated CPU3 (84% sys) — reconfirming the §6.1.8/§6.1.8a "userspace socket-drain, not driver" finding with TCP and per-core evidence. The per-CPU NAPI + contiguous-banding work (M3-3 steps 2a–2c) is doing its job.

**Gate-3 status after Options A + C:** the driver (a) drops 0% of every frame the fabric delivers and (b) scales RX softirq across cores to 5.57 Gbit/s aggregate TCP with headroom. Gate 3 is **consumer/methodology-bound, not driver-bound**; a literal ≥7 Gbit/s figure needs generator-side multi-process iperf3 or a wire-rate generator (a lab-provisioning task, not kernel code). **M3-3 step 7 (true ZC) is not required for Gate 3 on driver-capacity grounds.**

> Per-stream results, full `mpstat` table, generator-credential detail: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.9 DUT→backup TX direction measurements — 2026-05-28

DUT-as-sender TX peaks at **4.93 Gbit/s single-stream TCP (0 retr)**, CPU3-bound. Root cause: **no in-tree, NXP-SDK, or DPDK driver exposes TSO/LSO for FMan v3 on Linux** (verified across mainline `dpaa_eth.c`, NXP SDK `sdk_dpaa/dpaa_eth.c`, DPDK 25.03 DPAA PMD docs, and NXP's own `dpaa.rst`). The AGENTS.md shorthand "TSO is hardware-impossible on DPAA1" is conclusion-correct but mechanism-imprecise: more accurately, **TSO is driver-unimplemented across every NXP-authored datapath driver and not advertised by any FMan v3 BMI capability bit consulted by Linux**. Net effect: `tcp-segmentation-offload: off [fixed]`, software GSO active, every MSS traverses the full softirq→driver TX path one skb at a time.

**Implication for Gate 3:** the gate is defined against the AF_XDP zero-copy TX path which bypasses the kernel softirq TX entirely. The 4.93 Gbit/s kernel-skbuf TX ceiling **does not block AF_XDP Gate-3 closure**; step 7 (FMan RX DMA into XSK-pool chunks via `priv->xsk_bpid` matching, plus TX symmetry via `xsk_tx_peek_release_desc_batch()` → `qman_enqueue()`) remains the correct ZC path.

> Full TX measurement table and TSO source citations: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.10 True-ZC RX sub-increment 1 — `xsk_zc_eligible` recognition probe (patch `0093`) — 2026-05-29

Step 7 is split into small individually-DUT-validated sub-increments because mechanism 3 (FMan RX-port BPID reprogram) is hardware-risky (§6.1.8 records three under-load crashes). **Sub-increment 1 = mechanism 1 (Recognise) only**, a strictly-diagnostic counter with zero datapath change. `0093` adds the 17th `xsk_*` ethtool counter `xsk_zc_eligible`, bumped inside the existing `0083` RCU read-section in `rx_default_dqrr()` when an FD reaches a band with a bound XSK pool AND `fd->bpid == priv->xsk_bpid[_band]` — the exact arithmetic mechanism 2 (Recover) will rely on. Byte-identical to mainline on `default`/`vpp` (no XSK pool → equality never holds → dead code). It proves the FD-recognition arithmetic in the live hot path is correct before any hardware-risky reprogram, and becomes the observability oracle once the reprogram lands.

> Host validation detail (cumulative apply/compile, patch-health stack-dependent-fail rationale): Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.11 True-ZC RX sub-increment 2 — `xsk_zc_rx_armed` attach-time arm observability (patch `0094`) — 2026-05-29

Re-applying the observe-before-write discipline, the original monolithic "sub-increment 2 = reprogram WRITE + recover" is split: `0094` is the strictly-diagnostic *read side* of mechanism 3, the hardware-risky WRITE moves to a later sub-increment. `0094` adds the 18th counter `xsk_zc_rx_armed`, bumped once per successful `xsk_pool_attach()` when the dedicated XSK BPID (`priv->xsk_bpid[queue_id]`) differs from the kernel page-pool BPID the FMan RX port currently DMAs into (`priv->dpaa_bp->bpid`). **No `fman_port` write happens** — copy-mode RX is preserved bit-for-bit. It proves, from userspace at attach time before any hardware write, that the reprogram has a distinct, meaningful target (`xsk_bpid != dpaa_bp->bpid`); a zero value would catch a `bman_new_pool()` BPID collision dormant, before it could crash the DUT.

> Host validation detail and CI `cp`-line wiring: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.12 True-ZC RX sub-increment 3 GATE — `xsk_fill_guard_block` FILL-ring double-release guard (patch `0095`) — 2026-05-29

Before the hardware-risky WRITE lands, the observe/assert-before-write cadence requires an in-driver assertion that the XSK FILL-ring single-producer invariant actually holds. The §6.1.8 root cause (a FILL-ring producer outrunning the consumer hands the same chunk to `xp_alloc()` twice) escalates from a dropped packet (copy-mode) to an **FMan DMA overwrite of a live chunk** once FMan DMAs directly into XSK-pool chunks. `0095` adds the 19th counter `xsk_fill_guard_block`: inside `af_xdp_pool_napi_refill()`, after `xsk_buff_alloc_batch()`, each per-handle DMA cookie is scanned against the `bmbs[]` already filled this batch; on a duplicate it bumps the counter, warns rate-limited, and **skips the whole round's `bman_release()`** (drop > corrupt — a duplicate proves the SPSC cursor is already inconsistent so the whole batch is untrusted). Byte-identical to `0084` v3 under a correct producer; the guard never fires.

**Sub-increment 4 entry conditions after `0095`** (all observable from userspace before any MURAM write): (1) `xsk_zc_rx_armed > 0` after a bind (`0094` — distinct target); (2) `xsk_fill_guard_block == 0` under sustained load (`0095` — FILL producer well-behaved); (3) `xsk_zc_eligible` (`0093`) growing after the reprogram lands (per-FD success oracle).

> Host validation detail, anchor-disambiguation note, and CI `cp`-line wiring: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.13 True-ZC RX sub-increment 3 — `xsk_zc_rx_recovered` Recover read-side, dormant (patch `0096`) — 2026-05-29

`0096` lands the last *read-only* mechanism — the dormant read side of mechanism 2 (Recover) — as the 20th counter `xsk_zc_rx_recovered`. Inside the `0083`/`0093` RCU read-section, after the recognition test, a guarded Recover decision bumps the counter when the band is armed (`xsk_zc_rx_armed`, `0094`) AND the FILL guard has never fired (`!xsk_fill_guard_block`, `0095`); the FD then continues through the UNCHANGED skbuf path (no `xsk_buff_recv()`, no `xdp_do_redirect()`). After `0096` every one of the three mechanisms has its observable half landed and only the hardware-risky reProgram WRITE plus the productive recover body remains. Byte-identical to mainline on `default`/`vpp` (bpid-match false → dead code) — the cost is a single integer compare extending the `0093` test.

**Scope finding (the original blocker) — and its resolution.** During `0096` authoring it was confirmed that no FMan RX-port external-BPID-reprogram API existed in the dpaa1 board stack — the productive FMan PCD subsystem (`fman_pcd_*`) lived ONLY in the ASK flavor patches. **This forward-port is now LANDED:** commit `f307193` (board patches `0092`/`0097`/`0098`/`0099`/`0100`) forward-ported the FMan PCD subsystem into the common board stack via the bridge idiom (fman-side TU defines silicon + EXPORTs neutral-spec `_install`/`_destroy`, owns MURAM; dpaa-side `dpaa_fman_caps.c` gates on caps and delegates; public `include/linux/fsl/fman_pcd.h` carries the neutral contract). The `fman_pcd_*` accessors that sub-increment 4 needs now exist in the common board stack, so **sub-increment 4 is no longer blocked on the forward-port** — it remains gated only on the three userspace-observable preconditions above (§6.1.12) plus incremental DUT validation with a short hold first.

**MURAM-budget regression in the forward-port — found and fixed (2026-05-29).** The initial `f307193` forward-port shipped the OLD 96 KiB `FMAN_PCD_MURAM_RESERVED_BYTES` (`board/0092-fman-pcd-subsystem.patch`), reproducing the PR14b `-ENOMEM`: on the DUT `fman_pcd: cannot reserve 98304 bytes MURAM (err -12)` → `PCD subsystem init failed (err -12) - continuing without PCD` (96 KiB exceeds the post-CAM/FIFO free MURAM window on LS1046A). The validated fix (ask20 `f6fd24f`/patch `0026`) was NOT carried across with the forward-port. Corrected to 64 KiB (`64U * 1024U`) in `b90ef86` (further PCD graft stack-apply in `cd1b9c5`), pushed to `origin/dpaa1`. Verified: the applied tree (`work/linux-6.18.31/.../fman_pcd.c`) now carries `(64U * 1024U)`; the standalone `patch-health.sh board/0092 ✗` is the documented cumulative-dependency-chain false-positive. The driver degrades gracefully (caps=0x17 detected, datapath dormant-clean), so the dormant shipping image is healthy, but `pcd->state` stays NULL until a fresh ISO ships the fix — **a new ISO built from `cd1b9c5` or later is the precondition** for `fman_pcd: ready (64 KiB MURAM reserved at offset 0x4ac00)`, non-NULL `pcd->state`, productive CC/HM/Policer (`0099`/`0100`), and sub-increment 4. The default ISO DUT-validated earlier (`vyos-2026.05.29-2226-rolling-LS1046A-default-arm64`) predated `b90ef86` and still logged the `-12` soft-fail. **This precondition is now SATISFIED on the deployed default ISO.** The `0101`-carrying ISO `vyos-2026.05.30-0233-rolling-LS1046A-default-arm64` (CI run `26672112397`, commit `02fb1ac` — well past `cd1b9c5`) DUT-confirmed 2026-05-30: boot dmesg `fsl-fman 1a00000.fman: fman_pcd: ready (64 KiB MURAM reserved at offset 0x4ac00)` at t≈0.87 s (non-NULL `pcd->state`) + `fsl_dpa dpaa-ethernet.0: FMan PCD caps = 0x17 (CC HM POL PARSER)`. The MURAM-budget regression is closed end-to-end on real silicon; the FMan PCD subsystem is productive (CC/HM/Policer `_install`/`_destroy` reachable), so sub-increment 4 is now gated only on the three §6.1.12 userspace-observable preconditions plus a short incremental DUT hold.

**Operator DUT gate reader — `xsk-zc-check`.** The four sub-increment-4 entry-gate counters (`xsk_zc_eligible`, `xsk_zc_rx_armed`, `xsk_fill_guard_block`, `xsk_zc_rx_recovered`) are surfaced to `board/scripts/xsk-zc-check` (installed to `/usr/local/bin/xsk-zc-check`). It reads the 20-counter `xsk_*` suite via `ethtool -S` on eth3/eth4 and renders the §6.1.12 verdict — **dormant** (no ZC bind, all `xsk_zc_*` 0, the expected shipping state), **ZC-armed** (`xsk_zc_rx_armed > 0` AND `xsk_fill_guard_block == 0` → preconditions (1)+(2) MET), or **fault** (`xsk_fill_guard_block > 0` / hard attach/DMA error → WRITE must stay disabled). Exit 0/1/2 — usable as a Nagios/monit probe and the single command to confirm gate state before sub-increment 4 lands.

> Host validation detail (hunk arithmetic, rebrace note) and CI `cp`-line wiring: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.14 True-ZC RX sub-increments 1–3 — DUT validation (dormant read-side) — 2026-05-29

Sub-increments 1–3 (Recognise/Recover read-side counters `0093`–`0096`) are **dut-validated** on real LS1046A hardware. Build under test: ISO `vyos-2026.05.29-1554-rolling-LS1046A-vpp-arm64`, kernel `6.18.33-vyos`, CI run `26647449274` (`dpaa1`, `FLAVOR=vpp`). All five gates GREEN: G1 eth0–eth4 expose the full 20-entry `xsk_*` suite; G2 the four entry-gate counters read 0 (expected dormant/no-ZC-bind state); G3 `/usr/local/bin/xsk-zc-check` reports healthy, exits 0; G4 dmesg clean of IVCI/`list_add`/lockup/Oops/BUG, `xsk_fill_guard_block` 0 on all ports; G5 thermal 54–55 °C all zones. Sub-increment 4 (the FMan-port-BPID MURAM reprogram WRITE plus `xsk_buff_recv()`) is now unblocked by the `f307193` PCD forward-port (§6.1.13) and remains the only outstanding step-7 work.

> Full per-port counter dumps, ftrace evidence, and gate pass/fail detail: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`. (vbash gotcha: invoke `/usr/sbin/ethtool` by absolute path — a bare `ethtool` is intercepted by VyOS op-mode.)

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
- **§5.7** CEETM — exposed as root tc qdisc once the SDK CEETM driver is forward-ported (NOT in mainline — see §5.7).
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

M3-3 ≥ 7 Gbps gate is **flavor-agnostic** — the AF_XDP zero-copy datapath lives in `kernel/common/patches/board/` and can be exercised on any flavor (`default`, `vpp`, `ask`) that brings up an AF_XDP socket on a 10G SFP+ port. Historical "VPP measurement" framing throughout earlier revisions of this spec reflects the test methodology (VPP's `af_xdp` plugin was the first usable XSK producer at hand), not an architectural constraint. Acceptable producers for the gate measurement: VPP `af_xdp` plugin, `xdp-tools xdpsock`, or a custom XSK socket app on any flavor. §8 validation adds rows for measurable default-flavor improvements (multi-flow kernel forwarding throughput, HOLDACTIVE arbitration cycles via `perf`) as side-effect deliverables.

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
| 64 B unidirectional pps, 1 qband (any flavor + XSK producer) | ~600 kpps | ≥ 1.5 Mpps | — |
| 64 B unidirectional pps, 4 qbands (any flavor + XSK producer) | n/a | ≥ 5 Mpps | — |
| 1500 B forwarding, single flow (any flavor + XSK producer) | ~2 Gbps | ≥ 7 Gbps | — |
| 1500 B forwarding, 4 flows, 4 cores (any flavor + XSK producer) | ~5 Gbps | ≥ 9 Gbps | ≥ 9 Gbps (no regression) |
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
       ≥ 7 Gbps gate is flavor-agnostic — measurable on default/vpp/ask
       with any AF_XDP producer (VPP af_xdp plugin, xdp-tools xdpsock,
       custom XSK app). Default-flavor side-effect wins (skbuf-path RX,
       HOLDACTIVE elimination) land in the same patch set.
       VyOS rolling vpp RC3 (first ISO carrying a built-in XSK producer).
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
- `drivers/net/ethernet/freescale/dpaa/{dpaa_eth.{c,h}}` (NB: `dpaa_eth_ceetm.{c,h}` is NOT present in mainline — see §5.7)
- `drivers/net/ethernet/freescale/fman/{fman.c, fman_port.c, fman_keygen.c, fman_muram.c, fman_sp.c, mac.c}`
- `drivers/soc/fsl/qbman/{qman.c, bman.c, qman_portal.c, bman_portal.c}` (NB: `qman_ceetm.c` is NOT present in mainline — must be forward-ported for M3-3e, see §5.7)

### NXP LSDK out-of-tree (forward-port source for M3-3e CEETM)
- `qman_ceetm.c` + `qman_ceetm_*` API in `include/soc/fsl/qman.h` (QMan CEETM channel/CQ/LFQ/CGR allocation)
- `dpaa_eth_ceetm.{c,h}` (the tc `Qdisc_ops` glue) — both from the NXP LSDK / pre-5.x QorIQ kernel tree
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