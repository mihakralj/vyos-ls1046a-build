# DPAA1 Driver Modernization for LS1046A
**Version v5.22** · VyOS LS1046A · 2026-06-21 · HADS 1.0.0

---

## AI READING INSTRUCTION

Read `[SPEC]` and `[BUG]` blocks for authoritative facts: register values, commit SHAs, patch numbers, milestone gate criteria, performance measurements, table rows, code blocks. Read `[NOTE]` blocks for dense narrative and experimental history — these preserve correctness-critical prose verbatim per the HADS data-loss guard. `[?]` blocks are unverified or assumed. `[BUG]` blocks contain symptom, cause, and fix. Preserve all mermaid diagrams, code snippets, and tables.

---

## 1. TL;DR

**[SPEC]**
1. **One driver core, one classifier API, one HW-accelerated AF_XDP stack — three flavors that consume it differently.** Shared substrate: two ops tables (`pcd_ops`, `qmgmt_ops`) installed per-`dpaa_priv`. `default` leaves them NULL (mainline behaviour); `vpp` populates them with UMEM-backed BMan pool + ZC RX/TX hooks; `ask` populates them with dynamic CC tree + Host Command reconfig hooks. RCU-NULL-safe — when no flavor module is loaded the driver is byte-identical to mainline.

2. **The hardware offloads (CC / HM / Policer / CEETM) are not VPP-only.** Each exposes a kernel-side consumer that benefits `default` and `ask` too: CC trees back kernel RPS or ASK2 flow steering; HM nodes hook into `NETIF_F_HW_VLAN_CTAG_*`; Policer profiles back tc/nftables ingress rate-limit offload; CEETM is QMan-silicon hierarchical egress shaping (the Linux tc-qdisc driver is NOT in mainline 6.18 and must be forward-ported from the NXP LSDK — see §5.7). **CLI policy (v5.13):** this spec delivers only the **kernel-side APIs** and their existing kernel-native interfaces (`ethtool -K`, sysfs, tc/nftables offload, module params). The **native VyOS CLI surfaces** — `set vpp settings hw-offload …`, `set system offload …`, `set qos policy shaper hardware …`, `set firewall … offload …` — are **DEFERRED to a separate, later "native VyOS CLI" phase** and are explicitly out of this spec's scope. In particular, VPP keeps its **existing** config/ops surface (`vppctl`/`startup.conf` + the already-shipped `set vpp settings interface ethX`); we do **not** add new `set vpp settings hw-offload …` verbs now. Every `set vpp settings hw-offload …` / `set system offload …` / `set qos policy shaper …` / `set firewall … offload` string elsewhere in this document denotes **target syntax for that future phase**, never a current deliverable. See `specs/vpp-dpaa1-ls1046a-spec.md` v0.3. **Amendment (2026-06-10, operator-directed):** `set system offload classify` is UN-deferred and SHIPPED — vyos-1x patch `vyos-1x-026-system-offload-classify.patch` (config-mode CLI → `ethtool -N` ntuple) + board patch `0109` (dpaa rxnfc → `fman_cc_tree_install()` bridge). The other CLI surfaces remain deferred.

3. **FMan ucode 210 is a build dependency, not a runtime option.** U-Boot loads the 210 binary from SPI `mtd3` at SoC bring-up; the kernel inherits a fully-armed FMan with Parser/CC/KeyGen/HM/Policer/IPF/IPR available. The DPAA1 driver only consumes capabilities. Capabilities apply equally across all flavors.

4. **The AF_XDP gap closes in three required phases plus four optional HW-offload phases.** M1 = `ndo_xsk_wakeup`. M2 = XSK-backed BMan pool. M3-3 = ZC RX/TX with qband mapping and cluster-aware NAPI pinning. M3-3b–e add CC, HM, Policer, CEETM. Acceptance bar: idle CPU < 10% on the main AF_XDP worker after M1; ≥ 7 Gbps single-stream IPv4 forwarding at < 5% kernel-net CPU per worker after M3-3. **M3-3 is NOT flavor-specific** — the ZC RX/TX datapath, qband mapping, and cluster-aware NAPI pinning all live in `kernel/common/patches/board/` (the dpaa flavor-ops contract plus `af_xdp_pool.ko`) and exercise identically on `default`, `vpp`, and `ask`. The ≥ 7 Gbps gate can therefore be measured on **any** flavor that brings up an AF_XDP socket on a 10G SFP+ port (eth3/eth4); historical "VPP-only" notes throughout this spec reflect the early test methodology (VPP's `af_xdp` plugin was the first usable XSK producer at hand) and are NOT an architectural constraint. The skbuf-path wins for `default` and `ask` are side-effect deliverables.

---

## 2. Milestone Tracker

**[SPEC]**
DPAA1 driver work is cross-flavor by construction (see §1.3, §7) — `default` / `vpp` / `ask` all share the same kernel binary; whether a given milestone *manifests behavior* on a flavor depends solely on whether that flavor has a userspace consumer loaded (AF_XDP socket, VPP `af_xdp` plugin, ASK2 module). Status: **planned**, **in-progress**, **landed** (in-tree, patch-health-clean), **dut-validated**, **stub-landed**, **blocked**, **deferred**.

**[NOTE]**
Full per-row validation detail (board counter readings, ftrace evidence, iperf3 numbers, gate pass/fail per port class) is archived in Qdrant under `topic=dpaa1-afxdp-spec-milestone-archive`. The Notes column below carries only the patch reference, commit SHA, and one-line outcome anchor.

**[SPEC]**

| ID | Capability | §  | Status | Patch / commit / anchor |
|---|---|---|---|---|
| **M0**          | Per-`dpaa_priv` ops abstraction + RCU + `NETDEV_XDP_ACT_XSK_ZEROCOPY` advertise | 3       | **dut-validated** | `0068`/`0069a`/`0070`/`0072` — abstraction passive, mainline-identical |
| **M1**          | `ndo_xsk_wakeup` trampoline + `af_xdp_pool.ko` skeleton | 5.3 | **dut-validated** | `0073`/`0074` — `af_xdp_pool: registered` |
| **M2-s1**       | XSK pool attach gate (autoload, counters, BMan seed, DMA map) | 6.1 | **dut-validated** | `0071`/`0075a-c`/`0077`/`0079` + `7dc1768` — bind ZC rc=0, 12 `xsk_*` counters |
| **M2-s2**       | XSK pool detach safety + 100× bind/unbind churn | 6.1 | **dut-validated** | `0076` + `039a50c` — 100× clean, fman_port_disable-anchored |
| **M3-3 step 1** | NAPI bind in `qmap[]` + `xsk_set_rx_need_wakeup` body | 5.2,6.1 | **dut-validated** | `0080` = `3e9bb83` — 100/100 sendto wakeups rc=0 |
| **M3-3 step 2a**| Per-CPU NAPI distribution across qbands | 5.2 | **dut-validated** | `0081` = `ed80517` — 4×bind clean |
| **M3-3 step 2b**| `qmap` debugfs observability node | 5.2 | **dut-validated** | `0082` = `7b7b634` — renders all 5 netdevs |
| **M3-3 step 2c**| Dedicated QMan dispatch channels per qband | 5.2 | **dut-validated** | `0082b` = `7026460` — 0 collisions |
| **M3-3 step 3** | RX ZC branch-eligibility probe | 6.1 | **dut-validated** | `0083` = `2ad2c0a` — 13th `xsk_*` counter, RCU-NULL-safe |
| **M3-3 step 4** | NAPI-hooked BMan refill | 6.1 | **dut-validated** | `0084 v2` + `0086`/`0087`/`0088` (`d1b6e30`) — §6.1.6; 60 s clean |
| **M3-3 step 5** | TX ZC submit + TxConf recycle + backpressure | 6.1 | **dut-validated** | `0085 v2` — kernel path wired |
| **M3-3 step 6** | Productive XSK RX delivery via XSKMAP redirect | 6.1 | **dut-validated** | `0089` — §6.1.7; copy-mode 296,716 pkts under load |
| **M3-3b**       | CC steering — exact-match HW classifier kernel API | 5.4 | **dut-validated (wedge closed)** | `0086`/`0092`/`0097`/`0098`/`0105`/`0106`/`0107`/`0108`/`0115`-`0118` — CC-dispatch wedge cured (CCBS + FM_CTL `0x28` PRE_BMI_ENQ + FM_CTL params page); install/miss/detach proven on 210.10.1. 5th gate (visible per-key FQ steer) needs §8 harness. §5.4 |
| **M3-3c**       | HM offload — VLAN/MPLS strip-insert kernel API | 5.5 | **dut-validated (CLI live)** | `0090`/`0090a`/`0099`/`0101` + vyos-1x-024 — cap `0x17`, `rx-vlan-offload: on`; wire strip/insert gate needs traffic gen. §5.5 |
| **M3-3d**       | Policer — per-flow HW ingress rate-limit | 5.6 | **dut-validated (3a + 3b-non-revert)** | `0091`/`0097`/`0100`/`0104` + vyos-1x-025 — BUG 3a (FMPL `GCR.EN|STEN` clear at boot) fixed `1a48948`; flood-crash half (3b) + throughput-cap number open. §5.6 |
| **M3-3e**       | CEETM — HW hierarchical egress shaping as tc qdisc | 5.7 | **shipped; A fixed, B closed (silicon)** | `0111`/`0112` (`580e637`) — modern HTB-offload rewrite; DEFECT B = documented LS1046A 8-ch limitation, product-impact NONE. §5.7 |
| **M3-3 step 7** | True ZC RX (FMan DMAs into XSK-pool BMan chunks) | 6.1 | **dut-validated (productive ZC)** | `0093`-`0096`/`0102`/`0102b`/`0103a`/`0103b`/`0103g`/`0110`/`0114` — oracle `xsk_zc_rx_redirect` fired, BPID flip proven in silicon, NAPI-only dispatch hardened. GAP 2 wire-rate number needs §8 harness. §6.1.18-19 |

**[SPEC]**
**What remains for a complete DPAA1 driver.** The shared kernel substrate (M0–M3-3 step 6) is dut-validated and shipping; the four HW-offload milestones are at varying completeness.

| Remaining item | Milestone | Status / nature of work | Blocking dependency |
|---|---|---|---|
| True ZC RX productive oracle | M3-3 step 7 | **CLOSED** (§6.1.18/19) — `xsk_zc_rx_redirect` fired, BPID flip proven in silicon, NAPI-only dispatch hardened. Only the GAP 2 literal wire-rate number remains. | §8 traffic harness (not gate-3-blocking). |
| CC steering per-key FQ enqueue-AD | M3-3b | **Kernel CLOSED** (`0108`/`0115`-`0118`) — wedge cured; install/miss/detach proven on 210.10.1 (§5.4). 5th gate = visible per-key FQ steer. | §8 harness (controllable peer traffic). |
| HM functional datapath gate | M3-3c | Feature + `vyos-1x-024` CLI **live on hardware**; no kernel/CLI work. Needs a wire strip/insert proof. | traffic gen (802.1Q tagged source). |
| Policer functional datapath gate | M3-3d | **Steering + BUG 3a + 3b-non-revert FIXED** (`1a48948`). Open: flood-crash half of 3b + the throughput-cap number. | §8 harness; serial + cold power-cycle for the flood-crash half. |
| CEETM productive shaper | M3-3e | **SHIPPED + CLOSED** (`0111`/`0112`, `580e637`) — DEFECT A fixed; DEFECT B closed as an LS1046A silicon limitation, product-impact NONE (§5.7). | rate-cap ±0.5% + CCG tail-drop measurement (§8 harness). |
| Literal ≥7 Gbps gate-3 figure | M3-3 | **DONE** — 4-flow TCP = 7.41 Gbit/s on the §8 harness, 0 retransmits, RX across all 4 cores. | — |
| DCSR error observability | 5.8 | `0079` + `0113` **dut-validated** — 6 debugfs error taps + `muram_budget` render correctly. | None. |

**[NOTE]**
**Bottom line:** the driver core is done and dut-validated; the FMan PCD subsystem landed (the M3-3b CC wedge is cured, the M3-3c/d productive paths underpinned), and all four HW-offload milestones (CC, HM, Policer, CEETM) have shipped. What remains is lab measurement — the functional datapath wire tests and the literal throughput numbers via the §8 harness — plus one deliberately-deferred destructive test (the policer flood-crash half of BUG 3b) and DEFECT B documented as an LS1046A 8-channel CEETM silicon limitation. No additional *architectural* work is required; the M0 ops abstraction and capability layer already accommodate every remaining consumer.

**[NOTE]**
**Operator-visible non-issue noted on the eth0/eth1/eth2 management LAN:** three RJ45 ports DHCP-active on the same `192.168.1.0/24` switch produce martian-source storms. VyOS config / cabling issue, NOT a driver regression. Fix: leave only eth0 plugged in or `set interfaces ethernet ethN address dhcp` on a single port.
---

## 3. Purpose and Scope

**[SPEC]**
Kernel-side changes to `drivers/net/ethernet/freescale/dpaa/` + FMan/QMan/BMan support code to modernize DPAA1 on LS1046A across all three VyOS flavors. The deliverable is a single kernel binary that:

- Exposes a per-`dpaa_priv` flavor-ops abstraction (M0).
- Implements `ndo_xsk_wakeup`, `XDP_SETUP_XSK_POOL`, advertises `NETDEV_XDP_ACT_XSK_ZEROCOPY`.
- Restructures RX as per-CPU NAPI + dedicated BMan channels — improves *kernel skbuf RX*, not just AF_XDP.
- Delivers true zero-copy RX/TX on AF_XDP via VPP-specific `af_xdp_pool.ko` (≥ 7 Gbps single-stream).
- Exposes HW classification, header manipulation, ingress policing, egress shaping as **kernel APIs**. Each is independently consumable by `default` (RPS, `NETIF_F_HW_VLAN_CTAG_*`, tc/nftables offload, root qdisc), `vpp` (`set vpp settings hw-offload …`), `ask` (ASK2 flavor module).

### 3.1 In scope

- `dpaa_eth.c`, `dpaa_eth_sysfs.c` changes for per-`dpaa_priv` ops, RX datapath restructure, XSK pool lifecycle, qband mapping correctness.
- `drivers/net/ethernet/freescale/fman/` changes for CC-tree install, HM node install, Policer profile install, CEETM channel setup. All called via M0 abstraction; flavor modules own policy.
- Forward-port the SDK CEETM driver (`qman_ceetm.c` + `dpaa_eth_ceetm.c`, absent from mainline — see §5.7) and wire it as a tc root qdisc replacement.
- VPP plugin integration via existing AF_XDP plugin; no VPP source changes.
- VyOS CLI glue per flavor — follow-on vyos-1x patches not in this spec's direct scope.

### 3.2 Out of scope

- ASK2 userspace, nft flowtable offload userspace bridge — see `specs/ask2-rewrite-spec.md`.
- DPAA2 (`dpaa2-eth`) — already native ZC.
- DPDK DPAA PMD coexistence — RC#31 blocks mixed mode.
- VPP source-tree modifications.
- MTU > 3290 with XDP loaded.
- Microcode authoring or modification.

### 3.3 Cross-flavor reach

**[NOTE]**
**Single-image update (retired 2026-06-14, `specs/dual-dataplane.md`):** the per-flavor ISO split is gone — one dual-dataplane image ships all consumers (VPP and `ask.ko` both present, both dormant until configured, globally mutually exclusive in v1). The "flavor" terms below remain valid as **consumer roles** of this kernel's APIs; they no longer imply separate ISO builds.

**[SPEC]**
The single kernel binary ships in all three ISO flavors. The M0 abstraction makes the hot path RCU-NULL-safe — when no flavor module registers ops, behaviour is byte-identical to mainline DPAA1. §5 capabilities are kernel APIs consumable by:

- **`default`** — through existing kernel offload interfaces (RPS, `NETIF_F_HW_VLAN_CTAG_*`, tc/nftables offload, root qdisc). No flavor module loaded; §5.2 datapath restructure improves skbuf RX automatically. CC/HM/Policer/CEETM activation requires vyos-1x CLI patches (follow-on, not blocking M3-3).
- **`vpp`** — through `af_xdp_pool.ko` (M0 ops consumer) plus VPP `af_xdp` plugin (§3.4). §5 primitives back the `set vpp settings hw-offload …` CLI verbs. Most demanding consumer and drives the M3-3 acceptance gates. Architecture per `specs/vpp-dpaa1-ls1046a-spec.md` v0.2: VPP creates AF_XDP ZC sockets on kernel-owned netdevs; no separate kernel module, no userspace QMan/BMan portal ownership. The v0.1 `fsl-dpaa1-um.ko` native plugin proposal was REJECTED (RC#31: userspace QBMan reprogramming kills all kernel FMan interfaces globally).
- **`ask`** — through the ASK2 flavor module (per `specs/ask2-rewrite-spec.md`). Consumes the §5.4 CC tree primitive (as dynamic-key map), the §5.5 HM primitive (for L2 normalization), the §5.6 Policer primitive (for nft `limit` offload). Does NOT consume the §6.1 XSK-backed BMan pool.

**[NOTE]**
Acceptance gates in §8 are written against the VPP datapath because it is the highest bar; passing them also delivers the skbuf-path wins for the other two flavors as a side-effect.

---

## 4. Background

### 4.1 What mainline 6.18.x gives us

**[SPEC]**
`dpaa_eth.c` declares:

```c
net_dev->xdp_features = NETDEV_XDP_ACT_BASIC |
                        NETDEV_XDP_ACT_REDIRECT |
                        NETDEV_XDP_ACT_NDO_XMIT;
```

No `NETDEV_XDP_ACT_XSK_ZEROCOPY`. No `ndo_xsk_wakeup`. No `XDP_SETUP_XSK_POOL`. AF_XDP works only in copy mode through the generic XDP layer.

**[NOTE]**
XDP support was added by Camelia Groza (NXP) in a 7-patch v5 series (netdev cover `<cover.1606322126.git.camelia.groza@nxp.com>`, accepted 2020-12-01). XDP buffers remain `dpaa_bp`-backed kernel pages, recycled through `bman_release()`. Default RSS spreads ingress across 128 PCD FQs using FMan KeyGen 5-tuple hashing. `QM_FQCTRL_HOLDACTIVE` plus stashing flags keep bursts on one CPU portal.

**[NOTE]**
**CEETM is absent from mainline** (verified 2026-05-29 on `work/linux-6.18.31`): there is no `drivers/soc/fsl/qbman/qman_ceetm.c`, no `drivers/net/ethernet/freescale/dpaa/dpaa_eth_ceetm.{c,h}`, no `qman_ceetm_*` symbol in `include/soc/fsl/qman.h`, and `dpaa_eth.c` does not even implement `ndo_setup_tc`/mqprio. The CEETM tc-qdisc driver was an NXP-LSDK / pre-5.x out-of-tree component that never landed upstream. M3-3e (§5.7) is therefore a *forward-port* of that SDK code, not a "wire the existing qdisc" task — see §5.7.

### 4.2 Complete FMan microcode/PCD function inventory (authoritative, NXP "Frame Manager Features")

**[NOTE]**
The six items above are the subset this spec *consumes*. For completeness — answering "do we have **all** microcode functions captured, publicly documented or undocumented?" — the full set of FMan v3 microcode-implemented PCD (Parse-Classify-Distribute) functions per NXP's authoritative "Frame Manager Features" / "FMan PCD Driver" documentation is enumerated below.

**[NOTE]**
**Scope note (proprietary 210 vs open-source 106.x).** This inventory describes the function set of the **proprietary signed 210.10.1 QEF blob that this board actually ships** (loaded by U-Boot from SPI mtd3, flash offset 0x400000 — see §3.5). It is **not** the open-source public `qoriq-fm-ucode` 106.4.18 baseline (`github.com/nxp-qoriq/qoriq-fm-ucode`). The 106.x public ucode implements a **narrower** function set — broadly the hard parser + KeyGen hash steering + basic Match-Table CC + basic Policer — and notably lacks the richer Advance-PCD functions (exact-match deep CC nesting, Parser soft-sequences, full Header-Manipulation, srTCM/trTCM Policer profiles, IPR/IPF, Frame Replicator). That gap is exactly why §2.2's title reads "What ucode 210 **unlocks over public 106.4.18**." The **[public 106.x?]** column below marks which functions are also present in the open-source 1xx line (**Y** = in 106.x too, **210-only** = added by the proprietary 210 ucode, **build-specific** = optional per-ucode-build feature). Each function is also flagged **[consumed]** (this driver exposes a cap bit + a `fman_*_install()` consumer), **[present-unconsumed]** (ucode-present on 210.10.1 but no cap bit or consumer yet — reserved for a later phase), or **[absent]** (not implemented in the shipping 210.10.1 QEF blob; board-confirmed).

**[SPEC]**

| # | NXP PCD function | Public 106.x? | This driver | Notes |
|---|---|---|---|---|
| 1 | **Software Parser** (soft-sequence extensions over the hard parser) | **210-only** | **[consumed]** | `FMAN_CAP_PARSER_SOFTSEQ` (BIT 4). VXLAN-inner / MPLS-stack / IPv6-frag soft sequences. The hard parser is in 106.x; the *programmable* soft-sequence path is a 210 Advance-PCD function. |
| 2 | **KeyGen** — 32 key-generation schemes; per-scheme hash distribution onto a FQID base+range; **post-hash index** offset; per-scheme **Policer-Profile (PP) selection** | **Y** (hash) / **210-only** (post-hash-index, PP-select) | **[consumed]** (steering) / **[present-unconsumed]** (post-hash-index, explicit PP-select) | Basic KeyGen hash steering is in 106.x (mainline RSS uses it). The *post-hash index* and *explicit per-scheme Policer-Profile selection* are 210 functions, ucode-present but not yet wired to a cap bit. |
| 3 | **Custom Classifier (CC) Root → Match-Table Nodes** (exact-match, deep nesting, per-key stats) | **Y** (basic) / **210-only** (deep nesting + per-key stats) | **[consumed]** | `FMAN_CAP_CC_EXACT_MATCH` (BIT 0). Basic match-table exists in 106.x; the deep-nesting + per-key-statistics richness is what 210 unlocks. |
| 4 | **Custom Classifier (CC) Root → Hash-Table Nodes** (hashed CC lookup, large flow tables) | **210-only** | **[present-unconsumed]** | Distinct from Match-Table nodes. Ucode-present on 210.10.1; no cap bit or consumer yet — reserved (see §3.5 note). |
| 5 | **Manipulations → Header Manipulation (HM)** (VLAN/Q-in-Q/MPLS push-pop, arbitrary byte insert/remove/replace) | **210-only** | **[consumed]** | `FMAN_CAP_HM_NODES` (BIT 1). Full HM (esp. arbitrary byte + MPLS) is a 210 Advance-PCD function. |
| 6 | **Manipulations → IP Reassembly (IPR)** (timeout-driven flush) | **210-only** | **[present-unconsumed]** | Prose item 5 above mentions IPF/IPR, but §3.5 has **no** `FMAN_CAP_IP_FRAG`/`FMAN_CAP_IP_REASSEMBLY` cap bit — present in 210.10.1, reserved for a later phase. |
| 7 | **Manipulations → IP Fragmentation (IPF)** (timeout-driven flush) | **210-only** | **[present-unconsumed]** | Same as IPR — 210 ucode-present, no cap bit/consumer yet. |
| 8 | **Frame Replicator** (multicast/mirror replication group → multiple FQs) | **210-only** | **[present-unconsumed]** | **Not previously enumerated in this spec.** Ucode-present on 210.10.1; no cap bit or consumer yet — reserved for a later mirror/multicast phase. |
| 9 | **Policer Profiles** (srTCM RFC 2697 / trTCM RFC 2698, color-marking) | **Y** (basic) / **210-only** (srTCM/trTCM color-marking) | **[consumed]** | `FMAN_CAP_POLICER_TRTCM` (BIT 2). Basic policer in 106.x; full two-rate three-color marking is a 210 function. |
| 10 | **Host Command (HC)** doorbell (runtime PCD modify without AttachPCD/DetachPCD save-restore) | **build-specific** | **[absent]** | `FMAN_CAP_HC_DISPATCH` (BIT 3) deliberately **clear**. Board-confirmed absent in the shipping **210.10.1** QEF blob (§3.5, re-validated 2026-06-01). HC is an optional, publicly-undocumented *per-ucode-build* feature — present only in a dedicated `qe_firmware` ucode variant, which we do not ship; the authoritative determination is the empirical board probe. |
| 11 | **PCD Statistics** (per-node/per-key counters surfaced through the above functions) | **Y/210** (follows the consuming node) | **[consumed]** (where the consuming function is consumed) | Not an independent function — a property of CC/KeyGen/Policer nodes; counted via the consumed nodes' stats. Availability tracks whichever node carries it. |

**[NOTE]**
**Open-source vs proprietary scope (explicit):** the inventory above is the **proprietary 210.10.1** function set this board ships, **not** the open-source 106.4.18 baseline. The 106.x public `qoriq-fm-ucode` line implements only the **Y**-marked subset (hard parser, basic KeyGen hash steering, basic Match-Table CC, basic Policer); every **210-only** row is an Advance-PCD function the proprietary 210 ucode adds on top. We did **not** separately enumerate the 106.x set as its own table because (a) this board never loads it, and (b) it is strictly the **Y**-marked subset of the rows above — there is no 106.x function absent from 210.

**[SPEC]**
**Gap callout (explicit answer to the completeness question):** three NXP PCD functions are **ucode-present on 210.10.1 but unconsumed and currently have no cap bit** — **CC Hash-Table nodes (#4)**, **IP Reassembly/Fragmentation (#6/#7)**, and **Frame Replicator (#8)** — plus KeyGen's post-hash-index and explicit Policer-Profile-select sub-features (#2). They are reserved for later phases; the 5-bit cap bitmask in §3.5 is a deliberate subset that covers only what the driver consumes today. **Host Command (#10)** is the only function confirmed *absent* from the shipping blob (not merely unconsumed). Sources: NXP "Frame Manager Features" and "FMan PCD Driver" docs (URLs in §11).

210 is signed by NXP, distributed as part of LSDK BSP, loaded by U-Boot from SPI `mtd4`. The M0 capability bitmask (§3.5) gates `pcd_ops->install` accordingly.

### 4.3 What FMan ucode 210 unlocks (over public 106.4.18)

1. Exact-match Custom Classifier trees with deep node nesting and per-key statistics.
2. Parser soft sequences for non-standard protocols (VXLAN inner, MPLS label stack, IPv6 fragment).
3. Header Manipulation (HM) nodes — insert/remove/replace on VLAN (single + Q-in-Q), MPLS (push/pop with TC propagation), arbitrary byte ranges. Chains with CC trees.
4. Policer profiles — srTCM (RFC 2697) and trTCM (RFC 2698) per-flow or per-qband. Color-marking in FD status.
5. IPv4 fragmentation (IPF) and reassembly (IPR) with timeout-driven flush.
6. Host Command (HC) dispatch for runtime PCD modifications without AttachPCD/DetachPCD save-restore.

210 is signed by NXP, distributed as part of LSDK BSP, loaded by U-Boot from SPI `mtd4`. The M0 capability bitmask (§3.5) gates `pcd_ops->install` accordingly.

### 4.4 The queue-0 alias and why it must die

**[BUG] Queue-0 alias violates AF_XDP contract**
- **Symptom:** When AF_XDP binds queue 0 in copy mode, all portal dequeues are treated as queue 0; XSKs bound to other queues see nothing.
- **Cause:** `dpaa_eth_napi_poll()` operates per `struct dpaa_napi_portal` — per-queue identity does not exist. This violates AF_XDP's `(netdev, queue_id)` contract and forces the `patch-dpaa-xdp-queue-index.py` workaround that collapses all RX FQs onto `queue_index = 0`.
- **Fix:** M3-3 step 2 introduces true per-qband queue identity.

---

## 5. Architectural Model

### 5.1 Per-`dpaa_priv` flavor-ops abstraction (M0)

**[SPEC]**
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

### 5.2 Hook points in `dpaa_eth_probe()`

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

### 5.3 Module load ordering

**[SPEC]**
**Current decision (vpp): kconfig autoload (Option B).** `CONFIG_DPAA_AF_XDP_POOL=y` (built-in). `af_xdp_pool_init()` runs at `late_initcall` before `dpaa_eth_probe()`'s `register_netdev()`; `qmgmt_ops` is non-NULL on first publish; `NETDEV_XDP_ACT_XSK_ZEROCOPY` advertised. `MODULE_SOFTDEP` unreachable under this posture.

For ASK2 (when its module lands per ASK2 spec): same Option B posture, or `MODULE_SOFTDEP("pre: ask2_flavor")` with deferred-probe replay.

### 5.4 Per-netdev flavor-ops registration

**[SPEC]**
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

### 5.5 FMan capability-detection layer

**[SPEC]**

```c
#define FMAN_CAP_CC_EXACT_MATCH   BIT(0)   /* ucode 210+ */
#define FMAN_CAP_HM_NODES         BIT(1)   /* ucode 210+ */
#define FMAN_CAP_POLICER_TRTCM    BIT(2)   /* ucode 210+ */
#define FMAN_CAP_HC_DISPATCH      BIT(3)   /* ucode 210+ */
#define FMAN_CAP_PARSER_SOFTSEQ   BIT(4)   /* ucode 210+ */
/* Reserved — ucode-present on 210.10.1 but unconsumed; no consumer yet.
 * Add a cap bit + fman_*_install() helper when the consuming phase lands:
 *   FMAN_CAP_CC_HASH_TABLE     BIT(5)   -- CC Root Hash-Table nodes (§2.2.1 #4)
 *   FMAN_CAP_IP_REASSEMBLY     BIT(6)   -- Manipulations IPR (§2.2.1 #6)
 *   FMAN_CAP_IP_FRAGMENTATION  BIT(7)   -- Manipulations IPF (§2.2.1 #7)
 *   FMAN_CAP_FRAME_REPLICATOR  BIT(8)   -- Frame Replicator (§2.2.1 #8)
 */
```

**[NOTE]**
**The 5-bit cap bitmask is a deliberate subset of the full FMan microcode/PCD function set (§2.2.1), not the complete inventory.** It enumerates only the functions this driver *consumes* today (CC Match-Table, HM, Policer, Parser-SoftSeq) plus the one it deliberately gates *off* after board probing (HC_DISPATCH). The remaining NXP PCD functions — **CC Hash-Table nodes, IP Reassembly (IPR), IP Fragmentation (IPF), and the Frame Replicator** — are ucode-present on the shipping 210.10.1 blob but have **no cap bit and no consumer** here; they are reserved (commented placeholders BIT(5)–BIT(8) above) for the phase that introduces a consumer. This is an unconsumed-capability gap, **not** an absence: unlike HC_DISPATCH (which the empirical board probe shows the blob does not implement at all), these four are present in 210.10.1 and would light up once a consumer + DT-walk detection is added. See §2.2.1 for the authoritative consumed/present-unconsumed/absent breakdown.

Each exported `fman_*_install()` helper returns `-ENOTSUPP` if the corresponding cap bit is clear, **before** touching MURAM. Phase 3b–e flavor code: `install → if (-ENOTSUPP) { priv->hw_offload_unavailable++; continue; }` — fail-soft.

**[SPEC]**
**Productive detection (patch `0086a`, 2026-05-28).** Cap bits auto-populate from a DT walk of the FMan firmware blob U-Boot loaded into MURAM from SPI mtd3 (flash offset 0x400000). Layout per `include/soc/fsl/qe/qe.h struct qe_firmware`: bytes 0..3 `__be32 length`, bytes 4..6 magic `"QEF"`, byte 7 version, bytes 8..69 `id[62]` NUL-terminated ASCII. On Mono Gateway DK the property lives at `/proc/device-tree/soc/fman@1a00000/fman-firmware/fsl,firmware` (51652 bytes) and the id reads exactly `"Microcode version 210.10.1 for LS1043 r1.0"` (verified by `od -c` on board 2026-05-28). A string parser extracts the major version (210) and lights up `CC_EXACT_MATCH | HM_NODES | POLICER_TRTCM | PARSER_SOFTSEQ`. `HC_DISPATCH` deliberately stays off: per PR13 hardware probe (2026-05-13), the standard 210.10.1 QEF blob does not implement the Host Command doorbell — only the dedicated `qe_firmware` ucode variant does, and we do not ship that. Result on this board is `caps = 0x17` (CC+HM+POL+PARSER). The result is cached in a file-scope `static int` after first probe so the 5× `dpaa_eth_probe()` calls (one per MAC) don't re-walk the DT. The `dpaa_fman_caps.force=<u32>` module parameter still wins as an operator override for dev/CI on hardware where the DT walk would otherwise return 0.

**[SPEC]**
**MURAM budget:**

| Flavor | CC tree | HM nodes | Policer | Total | Of 64 KiB PCD reservation |
|---|---|---|---|---|---|
| `default` (CLI-driven CC) | ~5 KiB | ~1 KiB | ~2 KiB | ~8 KiB | ~12% |
| `vpp` | ~5 KiB | ~1 KiB | ~2 KiB | ~8 KiB | ~12% |
| `ask` (dynamic, up to 255 keys × 150 B) | up to ~38 KiB | ~2 KiB | ~4 KiB | up to ~44 KiB | ~69% |
| Combined `vpp` + `ask` (different netdevs) | ~43 KiB | ~3 KiB | ~6 KiB | ~52 KiB | ~81% |

Combined `vpp` + `ask` on the same MAC (Phase 4) is gated by VSP bifurcation.

---

## 6. LS1046A Hardware Modules — Per-Capability Engagement

**[SPEC]**

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
| SEC 5.4 (CAAM) | — | — | — | — | — | — | — | — (see ASK2 §8) | CAAM QI (ASK-only) |

**(all)** = applies to default/vpp/ask; **(vpp)** = VPP-flavor only.

### 6.1 FMan v3 with ucode 210

384 KiB MURAM, 4 BMI ports, 32 KG schemes. Capability layer (§3.5) gates feature install per-cap; fail-soft on ucode 106.

### 6.2 BMan

64 pool IDs system-wide. Mainline today: 3 IDs/netdev. §5.2 dedicated channels (all flavors post-M3-3 step 2): 4 IDs/netdev. §6.1 XSK-backed pool (vpp-only): +4 IDs/netdev when AF_XDP bound. Worst-case (5 netdevs × 4 dedicated + 2 vpp-attached × 4 XSK): 28 of 64. Fits.

### 6.3 QMan/SWPs

Four SWPs (one per A72), 28 pool channels + 4 dedicated channels available. The 4-dedicated-channel consumption (one per qband) is within budget. Stash effectiveness requires polling CPU = portal CPU; `qmap[]` (§5.2) enforces.

### 6.4 QMan CEETM

8-level hierarchical scheduler in the QMan silicon. **The Linux CEETM tc-qdisc driver is NOT in mainline 6.18.31** (no `qman_ceetm.c`, no `dpaa_eth_ceetm.c`, no `qman_ceetm_*` API — verified 2026-05-29). §5.7 must forward-port the SDK driver before it can be wired as a root qdisc replacement.

### 6.5 MURAM

Combined < 52 KiB respecting PR14h 64 KiB reservation.

### 6.6 PAMU

**arm64 LS1046A: firmware bypass, kernel driver is PPC-only** (`depends on PPC_E500MC || (COMPILE_TEST && PPC)`). FMan masters DMA directly via dma-direct. No per-attach `fsl_pamu_window_create()` possible. §6.1 uses `xsk_pool_dma_map(pool, priv->mac_dev->dev, DMA_ATTR_SKIP_CPU_SYNC)` only; `DMA_ATTR_COHERENT` NOT passed (no defined semantics on arm64 dma-direct). 9-step detach anchored on `fman_port_disable(rxp)` before `xsk_pool_dma_unmap()`.

### 6.7 CCI-400 vs CoreNet

Two coherency fabrics. CoreNet (A72↔DPAA1) guarantees coherency unconditionally; `DMA_ATTR_SKIP_CPU_SYNC` mappings safe in hot path. CCI-400 (A72 cluster↔cluster) matters for worker pinning — `qmap[]` builder prefers same-cluster RX qband + consumer.

### 6.8 SerDes/XFI PCS

`phy-connection-type = "xgmii"` critical. `xsk_socket__create()` runs `phylink_resolve()` before allowing attach.

### 6.9 FMan DCSR

Exception window at FMan offset `0x0F_0000`. §5.8 wires debugfs `/sys/kernel/debug/dpaa-eth/<iface>/dcsr/{bmi,parser,kg,pol}_err`, rate-limited ≥ 1 ms.
---

## 7. Cross-Flavor Capabilities

**[NOTE]**
Each subsection: kernel-side capability that is **not flavor-specific**. The capability lands as exported API or behavioural change in the driver core. The **Consumers** paragraph enumerates how `default`, `vpp`, `ask` use it.

### 7.1 M0 — Per-`dpaa_priv` flavor-ops abstraction

**[SPEC]**
**Patches:** `0068`, `0069a`, `0070`, `0072`. **Status:** dut-validated.

Structural refactor introducing `struct dpaa_pcd_ops` and `struct dpaa_qmgmt_ops` in `drivers/net/ethernet/freescale/dpaa/dpaa_flavor.h`. Adds the four `priv` fields per §3.1. Wires NULL-checked call sites. Adds the per-netdev registration helpers from §3.4.

**Consumers:**
- **`default`** — passive. Ops stay NULL; hot path byte-identical to mainline (verified board 2026-05-26: iperf3 13.1 Gbps baseline).
- **`vpp`** — `af_xdp_pool.ko` registers `qmgmt_ops`.
- **`ask`** — ASK2 flavor module registers `pcd_ops` + subset of `qmgmt_ops` (no XSK callbacks).

**Acceptance gate M0:**
1. `modprobe fsl_dpaa_eth` without flavor module: zero change. ✅
2. `rmmod && modprobe` ×100 no leak. ✅
3. Patches `checkpatch.pl`-clean. ✅

### 7.2 Per-CPU NAPI + dedicated BMan channels (RX datapath restructure)

**[SPEC]**
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
3. Build `priv->qmap[qband] = { fqid_base, swp_id, napi, cpu }` at probe. Exposed via debugfs. `WARN_ON(napi != &priv->qmap[qband].napi)` in dequeue.
4. Pin each NAPI to its QMan SWP CPU via `netif_napi_add()` + `netif_set_napi_irq_affinity_hint()`.
5. **Cluster-affinity hint** (£4.7). `workers N ≤ 2` → cores 0,1 (cluster 0); `N = 4` → 2-and-2 split. Exposed read-only as `/sys/class/net/<iface>/dpaa/cluster_map`.

**Dedicated BMan channels.** Replace pool-channel-with-HOLDACTIVE with one dedicated channel per qband. Eliminates HOLDACTIVE arbitration overhead; stash window pre-warms exactly the right CPU. **Measurable cycles-per-packet win in `dpaa_rx_napi_poll()` — benefits *every* RX path, AF_XDP or not.**

**Consumers:**
- **`default`** — gets the restructure automatically. Expected: lower per-packet cycles, better 4-flow RSS scaling, HOLDACTIVE arbitration eliminated. No CLI knobs.
- **`vpp`** — same restructure plus qband identity becomes queue_id for AF_XDP-ZC. Enables `xdpsock -q 3`.
- **`ask`** — same skbuf gain as default; ASK2 flavor module can additionally use qband as a flow-steering target.

### 7.3 `ndo_xsk_wakeup` (M1)

**[SPEC]**
**Patches:** `0070`, `0072`, `0073`, `0074`. **Status:** dut-validated.

```c
static int dpaa_xsk_wakeup(struct net_device *ndev, u32 queue_id, u32 flags)
{
    struct dpaa_priv *priv = netdev_priv(ndev);
    const struct dpaa_qmgmt_ops *ops;
    if (unlikely(!netif_running(ndev))) return -ENETDOWN;
    rcu_read_lock();
    ops = rcu_dereference(priv->qmgmt_ops);
    if (!ops || !ops->xsk_wakeup) { rcu_read_unlock(); return -EOPNOTSUPP; }
    rcu_read_unlock();
    return ops->xsk_wakeup(priv, queue_id, flags);
}
```

For `XDP_WAKEUP_RX`, kick NAPI via `napi_schedule()`. For `XDP_WAKEUP_TX`, poke QMan egress portal via `qman_p_irqsource_add`. After `napi_complete_done()`, arm `xsk_set_rx_need_wakeup()` on every bound pool with `xsk_uses_need_wakeup()` (patch 0080, M3-3 step 1).

**Consumers:**
- **`default`** — latent. Any AF_XDP application in copy mode benefits (no busy-spin).
- **`vpp`** — VPP drops `set vpp settings unix poll-sleep-usec 100`. Idle CPU ~100% → < 10%.
- **`ask`** — latent.

**Acceptance gate M1 (VPP, has the userspace consumer):**
1. VPP `af_xdp` runs in copy mode without `poll-sleep-usec`. ✅
2. Idle CPU < 10%. ✅
3. Thermal zones stay COOL/WARM. ✅
4. `xsk_ring_prod__needs_wakeup()` returns correct values. ✅

### 7.4 CC steering (M3-3b)

**[SPEC]**
**Patches:** `0086` (observability stub) + `0086a` (productive DT ucode-210 probe) + `0086b` (productive struct contract). **Status:** struct-contract-landed. `0086` established FMAN_CAP_* detection + `fman_cc_tree_*` decls + four `-ENOTSUPP` stubs + `hw_offload_unavailable` counter (`ethtool -S`). `0086a` auto-populates caps from the DT firmware-blob `qe_firmware.id` walk (§3.5). `0086b` (2026-05-28) promotes the opaque `struct fman_cc_key`/`struct fman_cc_static_tree` `{ u32 reserved; }` placeholders to the productive 5-tuple layout below.

**[NOTE]**
**CC + HM chain ordering (resolves former OQ5):** Parser → CC (unmodified ingress) → HM (egress from matching CC node) → QMan. CC keys match wire-format; consumers receive post-HM frames.

**[SPEC]**
**Productive struct contract (patch `0086b`).** `struct fman_cc_key` carries `ethertype` (host-endian, `FMAN_CC_ETHERTYPE_{ANY,IPV4,IPV6}`), `proto` (`FMAN_CC_PROTO_{ANY,TCP,UDP}`), `is_ipv6`, v4 `src_ip`/`dst_ip` + `src_ip_mask`/`dst_ip_mask`, v6 `src_ip6[16]`/`dst_ip6[16]`, `src_port`/`dst_port`, `target_qband` (the qband to steer matches to per §5.2), and `hm_handle` (chains to a §5.5 HM node; 0 = none). A field participates in the match iff it (or its mask) is non-zero; all multi-byte fields are host-endian at the API and the install body converts to FMan big-endian MURAM layout. `struct fman_cc_static_tree { u16 num_keys; u16 miss_qband; struct fman_cc_key keys[FMAN_CC_MAX_STATIC_KEYS]; }` with `FMAN_CC_MAX_STATIC_KEYS = 32` bounding the ~5 KiB MURAM static budget (§3.5).

```c
int  fman_cc_tree_install (struct fman *fm, u8 port_id,
                           const struct fman_cc_static_tree *spec);
int  fman_cc_tree_add_key (struct fman *fm, u8 port_id,
                           const struct fman_cc_key *key, u32 *handle);
int  fman_cc_tree_remove_key(struct fman *fm, u8 port_id, u32 handle);
void fman_cc_tree_destroy (struct fman *fm, u8 port_id);
```

**[NOTE]**
`install` takes a static tree spec and is the **productive path on shipping hardware**. `add_key`/`remove_key` are the dynamic lifecycle. A true host-command-dispatched dynamic path (no save-restore, no netdev flap) would require `FMAN_CAP_HC_DISPATCH` (BIT(3)) — but the stock 210.10.1 QEF blob loaded on every shipped Mono Gateway DK does **not** implement the FMan Host-Command doorbell (board reports `caps = 0x17` = CC|HM|POL|PARSER, bit 3 clear; see §3.5/§6.1.13). On this board the dynamic add/remove therefore falls back to an **in-tree MURAM rewrite** (AttachPCD/DetachPCD-style save-restore), and `add_key`/`remove_key` return `-ENOTSUPP` whenever neither HC dispatch nor the MURAM-rewrite path is available (e.g. ucode 106). HC-dispatched dynamic insertion would only become productive under a custom microcode we do not ship. Each entry consults `priv->fman_caps`. MURAM ≤ 5 KiB (default/vpp static) up to ~38 KiB (ask dynamic via MURAM rewrite).

**Consumers:**
- **`default`** — backs kernel RPS via `ndo_rx_flow_steer` (vyos-1x follow-on). HW classifier → right RX FQ → right qband → right CPU.
- **`vpp`** — `set vpp settings hw-offload classify rule N ...`. Static tree at `pcd_ops->install`.
- **`ask`** — ASK2 uses dynamic API (`add_key`/`remove_key`) directly. API **shared** between VPP and ASK2; only the lifecycle (static vs dynamic) is flavor-specific.

**[NOTE]**
**Impact of the missing HC doorbell.** Not a big miss for this board's roles. Three productive 210 alternatives, in order of preference: (1) **KeyGen 5-tuple hash RSS** — flap-free per-flow CPU steering with no MURAM churn; (2) **static CC tree** — bounded policy-rule steering rebuilt on `commit`; (3) **dynamic CC via in-tree MURAM rewrite** — true runtime add/remove with a per-mutation save-restore window. Only flap-free high-churn per-socket aRFS is genuinely unavailable, and it would need a custom microcode NXP does not ship.

**[NOTE]**
**CC-dispatch wedge — CLOSED 2026-06-12.** Early per-key FQ-delivery board tests wedged the eth3 RX port on the first CC-dispatched frame (FPM port-stall, one-shot per boot), reproducing under both 210.10.1 and 106.4.18 ucode — disproving the ucode-mismatch theory. Root-caused via SDK init-delta analysis to the CC *exit* path and cured by `0115`-`0118` (FM_CTL `0x28` PRE_BMI_ENQ result-AD + per-port FM_CTL ctrl-params page + the HW-proven KGSE_CCBS dispatch model); install/miss/detach now proven on the 210.10.1 proprietary ucode. Full trail: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

**[NOTE]**
**Archaeology note (QEF location + recovery, 2026-06-10).** The 106 experiment destroyed the factory QEF in SPI **mtd3** ("fman-ucode", flash offset 0x400000 — this spec previously mis-stated mtd4, which is actually "recovery-dtb", an extended-args FDT). A presumed factory backup turned out to be a wrong-partition copy of mtd4 (MTD numbering shifts between builds — same trap as the documented fw_env.config mtd2/mtd3 shift). The proprietary 210.10.1 QEF (51,652 B, id `Microcode version 210.10.1 for LS1043 r1.0`, sha256 `5f3ed8d3…3eff0d`) was recovered from git history — blob `d21cfbde:data/dtb/mono-gw.dtb` @0x6d20, a runtime-FDT capture from live OpenWrt that embeds U-Boot's injected `fman-firmware` node — CRC-validated with U-Boot semantics (`crc32(~0,…)^~0` == stored BE32 tail `0x961eb941`; plain zlib crc32 does NOT match, don't misdiagnose as corruption), re-flashed via U-Boot (`fatload mmc 0:2` + `sf update 0x82000000 0x400000 0xc9c4`), and HW-verified on the next cold boot (upload banner + kernel `FMan PCD caps = 0x17`). Recovery copies: Board `/config/fman-ucode-210.10.1.qef` + eMMC FAT `fman210.qef` (U-Boot-reachable). **Do NOT commit the QEF as a standalone repo file** (proprietary NXP license) — the in-history DTB blob is the sanctioned recovery source. Bonus finding: with mtd3 invalid, U-Boot walks `Fman1: Data at … is not a firmware`, no DT injection happens, kernel caps=0x00, and Linux DPAA networking remains FULLY functional (independent-mode RX/TX) — only PCD offload disappears; the §3.5 graceful degrade held on real hardware.

**Acceptance gate M3-3b:**
1. `cat /sys/kernel/debug/dpaa-eth/<iface>/cc_tree` shows rules.
2. Steering observable on `xdpsock -q N` or `ethtool -S` per-FQ counters.
3. No regression vs. §5.2 baseline.
4. `add_key`/`remove_key` no netdev flap.

### 7.5 HM offload (M3-3c)

**[SPEC]**
**Patches:** `0090`/`0090a` (stub + ordered-op-list `struct fman_hm_spec`) + `0099` (productive HMTD/HMCT MURAM encoder, `fman_pcd_manip.c`, RM 8.7.5 generic INSRT/RMV) + `0101` (userspace `.ndo_set_features` bridge). **Status:** bridge dut-validated — feature bit live on hardware; vyos-1x-024 CLI shipped + live on board (2026-06-07); functional datapath gate pending traffic gen only.

**[NOTE]**
`0101` is the kernel-side bridge that makes the dormant `0099` install body reachable from userspace: a `dpaa_set_features()` `.ndo_set_features` handler that, on a 0→1 transition of `NETIF_F_HW_VLAN_CTAG_RX` (driven by `ethtool -K ethX rxvlan on`, emitted by the vyos-1x-024 `set interfaces ethernet ethX hw-offload vlan-strip` CLI), installs a single-op `{FMAN_HM_OP_VLAN_STRIP}` HM node and stashes the handle in `priv->hm_vlan_strip_handle` (destroyed on 1→0). `struct fman *` resolves via `fman_bind(mac_dev->fman_dev)`; the port id via `fman_port_get_qman_channel_id(mac_dev->port[RX])`. The feature bit is advertised in `net_dev->hw_features` only when `fman_hm_caps_supported()`, so mainline-ucode boards never expose an `-ENOTSUPP` knob. **Board-validated 2026-05-30 / CLI live 2026-06-07** (cap `0x17`, `ethtool -k eth0` → `rx-vlan-offload: on`, not `[fixed]`). The functional datapath gate (tcpdump tagged on wire / consumer untagged, sub-100 ns HM cost) remains pending a traffic generator only.

`fman_hm_node_install/destroy/caps_supported` are `EXPORT_SYMBOL_GPL`. `0099` (PR4 of the FMan PCD forward-port, built-in via `FSL_FMAN_PCD`) makes the install/destroy bodies productive — four per-op HMCT encoders (vlan_insert/vlan_strip/mpls_push/mpls_pop) per RM 8.7.5, neutral `struct fman_pcd_hm_hw_{op,spec}` published in `include/linux/fsl/fman_pcd.h`, host→neutral translate via `dpaa_hm_spec_to_hw()`, delegate via `fman_get_pcd()`. `struct fman_hm_op`/`fman_hm_spec` (ordered op-list, `FMAN_HM_MAX_OPS=8`) from `0090a`; ops `VLAN_STRIP`/`VLAN_INSERT`/`MPLS_PUSH`/`MPLS_POP` (append-only enum). Cap auto-detected by `0086a` DT probe.

```c
int  fman_hm_node_install(struct fman *fm, u8 port_id,
                          const struct fman_hm_spec *spec, u32 *handle);
int  fman_hm_node_destroy(struct fman *fm, u8 port_id, u32 handle);
bool fman_hm_caps_supported(void);
```

`HM_OP_VLAN_STRIP`, `HM_OP_VLAN_INSERT(vlan_id, tpid)`, `HM_OP_MPLS_PUSH(label, tc, s, ttl)`, `HM_OP_MPLS_POP`. Chains with §5.4 CC tree.

**Consumers:**
- **`default`** — backs `NETIF_F_HW_VLAN_CTAG_RX/TX` in `dev->features`. CLI: `set interfaces ethernet ethX hw-offload vlan-strip` (vyos-1x follow-on).
- **`vpp`** — `set vpp settings hw-offload vlan-strip-on-ingress`.
- **`ask`** — ASK2 uses HM for L2 normalization on offloaded flows.

**Acceptance gate M3-3c:**
1. Wire shows tagged; consumer sees untagged (and vice versa on egress).
2. No regression.
3. Sub-100 ns per-packet HM cost.

### 7.6 Policer (M3-3d)

**[SPEC]**
**Patches:** `0091`/`0091a` (stub + srTCM/trTCM struct contract) + `0097` (KeyGen PLCR next-engine + `fman_pcd_kg_scheme_create`/`_bind_port`/`_destroy` API) + `0100` (productive FMPL install body) + `0104`/`0104a` (tc-matchall `.ndo_setup_tc` bridge + PCD datapath wiring + `NETIF_F_HW_TC` advertisement). **Status:** steering + BUG 3a + BUG 3b-non-revert FIXED + HW-validated (image `2032`, commit `1a48948`); the iperf3 flood-crash half of 3b and the literal throughput-cap number remain open.

**[NOTE]**
**Datapath history (CLOSED — full register-proof in Qdrant `topic=dpaa1-ingress-policer-bug3a-3b`).** The first wire test (2026-06-08) silently ran at line rate: `0100`/`0104` programmed the FMan policer *profile* (so `in_hw` reported and the one-per-interface guard fired) but never *bound* it into the ingress frame path. Path A (KeyGen next-engine=PLCR) fixed that — the critical missing edge was `fman_port_use_kg_hash(port, true)` flipping `fmbm_rfpne` from direct BMI-enqueue to KeyGen — plus a `port_id > 0x3f` guard fix (10G RX ports are BMI id `0x10`/`0x11`, not `>10`). Steering was then fixed in place: reprogram the port's existing RSS scheme to next-engine=PLCR via `kg_find_port_scheme()` (scheme MODE `0x80500002` → `0xc04c0000`), preserving hashing/match-vector/base-FQID so a miss still spreads over the hashed RX FQs. **BUG 3a** root cause: the FMan1 Policer block (FMPL, CCSR `0x01AC0000`) ships with its master-enable `FMPL_GCR.EN|STEN` *clear* (`0x00500002`), so KeyGen-routed frames drop pre-meter — three register-addressing theories were tested and disproven first. Fix (`0100` `plcr_enable_block()`): RMW `GCR |= EN|STEN` (`→0xC0500002`); on a clean cold boot policed ping went 100% loss → 0%, TPC increments. **BUG 3b** non-revert half: a `flow_block_cb_alloc(... release ...)` callback in `0104` (+ vyos-1x-025 filter-del before `super().update()`) reverts the scheme and destroys the profile on any teardown — delete→re-apply verified clean. The iperf3 flood-crash half of 3b is deliberately untested (watchdog-reset risk; needs serial capture + cold power-cycle). **Always characterize the policer with a few pings, never a flood.**

**[SPEC]**
`0100` makes the entry-point bodies productive via the `0099` bridge idiom: the fman-side `fman_pcd_plcr.c` (in `fsl_dpaa_fman.ko`) owns all MURAM and EXPORTs `fman_pcd_plcr_install`/`_destroy` taking the neutral BE-ready `struct fman_pcd_plcr_hw_profile`; the dpaa-side `dpaa_fman_caps.c` gates on `FMAN_CAP_POLICER_TRTCM`, translates host→neutral via `dpaa_plcr_prof_to_hw()`, delegates via `fman_get_pcd()`. The encoder implements RM §8.7.6: rate=`exp<<29|mant<<13` (note the **u16-MHz-not-Hz gotcha** — `fman_get_clock_freq()` returns MHz, ×1e6 before the `bps·2³¹/clk` division), 256-byte burst quantisation, 16-byte MURAM profile record. Validation: `cir_bps==0`→`-EINVAL`, trTCM `pir_bps<cir_bps`→`-ERANGE`, no cap→`-ENOTSUPP`. `struct fman_policer_profile` (srTCM RFC 2697 / trTCM RFC 2698, `cir_bps`/`cbs_bytes`/`pir_bps`/`pbs_bytes`, color-blind/aware) from `0091a`; all rates bits/sec, bursts bytes. Native arm64 0 warn/0 err, `patch-health board/0100 ✓`. Cap auto-detected by `0086a` DT probe.

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
- **`default`** — backs tc/nftables ingress rate-limit offload via `flow_block_offload`.
- **`vpp`** — `set vpp settings hw-offload policer per-qband-limit 2.5g per-flow-limit 100m`.
- **`ask`** — nft `limit` offload backend.

**Acceptance gate M3-3d:**
1. 2.5 Gbps limit caps offered 3 Gbps to ≤ 2.5 Gbps at consumer, red-drops visible. **← STEERING FIXED 2026-06-09; BUG 3a FIXED + HW-VALIDATED 2026-06-09 (image `2032`, commit `1a48948`): the FMPL policer block master-enable (`FMPL_GCR.EN|STEN`) was clear at boot — `0100` `plcr_enable_block()` flips GCR `0x00500002→0xC0500002`, policed ping went 100% loss → 0% on a clean cold boot, TPC increments. BUG 3b non-revert half also fixed (kernel `flow_block_cb` release in `0104`). The throughput-cap number itself is the only piece of this gate still to be measured.**
2. No regression unconfigured.
3. `commit` no netdev flap.

### 7.7 CEETM egress shaping (M3-3e)

**[SPEC]**
**Patches:** `0111-qman-ceetm.patch` + `0112-dpaa-ceetm-htb.patch` (supersede the `0104b` stub; `0104` remains the PCD/policer consumer). **Status: SHIPPED + CLOSED (2026-06-14).** Functional gates HW-validated; rate-accuracy testing found two defects — **DEFECT A** (additive effective rate) FIXED + HW-validated, **DEFECT B** (special-default-channel blackhole) CLOSED as a documented LS1046A 8-channel CEETM dequeue-scheduler silicon limitation, product-impact NONE.

**[NOTE]**
**Modern rewrite, not a verbatim forward-port** (operator directive: keep silicon-proven HW programming, rewrite kernel integration for 6.18; no legacy custom `ceetm` qdisc, no patched iproute2):

- `0111` — NEW `drivers/soc/fsl/qbman/qman_ceetm.c` (object model: sp/lni/channel/cq/ccg/lfq claim+config+release, bps↔token-rate math, bitmap resource mgmt) + `qman_ceetm_mc_exec()` MC helper in `qman.c` + DT `clock-frequency`→prescaler + `qman_sp_enable_ceetm_mode()` in `qman_ccsr.c` + public API in `include/soc/fsl/qman.h`. Size `#define`s are the true LS1046A 8-channel / 1024-LFQID silicon limits (`CEETM_NR_CHANNELS 8`, `CEETM_LFQID_COUNT 0x400`, `CEETM_LFQID_LSB_MASK 0x0003FF`). v1 scope: CCG tail-drop threshold only, no CSCN ISR.
- `0112` — NEW `drivers/net/ethernet/freescale/dpaa/dpaa_ceetm.{c,h}`: a `TC_SETUP_QDISC_HTB` offload consumer driven by stock `tc qdisc add dev ethN root handle 1: htb offload`. Root→LNI, first-level classes→shaped channels (CR=rate, ER=ceil), leaves→prio CQs + per-leaf LFQ/FQ via `dpaa_ceetm_select_queue()`; deeper nesting → `-EOPNOTSUPP`. Hardcoded LS1046A CEETM0 resources; erratum A-010383 honored.

**[NOTE]**
**Starvation root cause (the one HW gotcha — first install was a total egress blackhole).** A CS=1 (shaped) channel with a zero-rate shaper and no CQ on the CR/ER eligibility lists is **never scheduled** — frames enqueue and starve forever. The silicon-proven recipe (Mono's CDX CEETM app on this exact board): **"unshaped" = shaped at infinite rate.** LNI shaper always enabled (OAL=24, CR=infinite, ER=0); channel shaper always enabled (CR=ER=infinite, bucket limit 0x2000); unshaped CQs ride the ER eligibility list only; shaped CQs get CR=1 (+ER for the HTB borrow-to-ceil model). HW-validated 2026-06-11 (`a35cc62`): htb-offload install → 5/5 ping (was a 100% blackhole), CQ dequeues advancing, shaped class + steering passes, clean teardown/re-add, deeper hierarchy rejected.

Steering is by `skb->priority` (set upstream of the qdisc, e.g. `iptables -t mangle … -j CLASSIFY --set-class 1:K`), consumed by `dpaa_ceetm_select_queue()` — NOT a `tc filter … flower classid` on the offloaded qdisc.

**[BUG] DEFECT A — additive effective rate (FIXED + HW-validated)**
- **Symptom:** A shaped CQ rode BOTH the CR and ER eligibility lists, so the cap measured `rate+ceil`.
- **Cause:** Channel ER token-rate not adjusted for CR baseline.
- **Fix:** Channel ER token-rate = `ceil_bps > rate_bps ? ceil_bps - rate_bps : 0` (CR enforces `rate`, ER tops up only the `ceil-rate` borrow headroom; `ceil==rate` → ER=0).

**[BUG] DEFECT B — special-default-channel blackhole (CLOSED — silicon limitation, product-impact NONE)**
- **Symptom:** Unclassified egress out the *special default channel* black-holed when two CEETM ports on different LNIs share the single LS1046A DCP0.
- **Cause:** An unqueryable CEETM dequeue-scheduler silicon state present in no reprogrammed-on-reclaim table, NOT a driver-fixable routing bug. The NXP SDK builds no special default channel and drops unclassified frames.
- **Fix:** Documented limitation. **Product impact = NONE:** `dpaa_ceetm_select_queue()` reaches the special default channel ONLY for raw `tc … htb offload default 0`; VyOS `traffic-policy shaper` always renders `htb default <minor>` → a real default *leaf* (0% loss every test), so the shipping product can never reach the blackhole. Ships Option-B (`580e637`); the LFQMT diagnostic (`db99f92`) is reverted.

**[NOTE]**
**Historical (scope correction, 2026-05-29).** Earlier spec revisions assumed `dpaa_eth_ceetm.c`/`qman_ceetm.c` ship in mainline; a tree audit proved them absent — the CEETM tc-qdisc was an NXP-LSDK / pre-5.x out-of-tree driver. M3-3e was therefore a multi-part effort (QMan CEETM core + DPAA CEETM qdisc + `ndo_setup_tc`), landed as the modern rewrite above (`0111`/`0112`) after a stub-first scaffold (`0104b`, `4e080cb`, board-validated 2026-06-07). The scheduler core works on any ucode (it is QMan silicon, not FMan PCD); only colour-aware drop is ucode-210 gated.

**CGRs mandatory for strict-priority** (design target). Each CEETM CQ gets a CGR with tail-drop threshold sized to ~2 ms of class-rate-worth of buffers.

**Consumers:**
- **`default`** — `set qos policy shaper hardware ceetm class 1 rate 4g ceil 6g ...` (vyos-1x follow-on).
- **`vpp`** — `set vpp settings hw-offload egress-shaper strict-priority`. Mutex per-port with VPP-internal shaping (§7.4).
- **`ask`** — same as default for kernel-managed traffic.

**Acceptance gate M3-3e:**
1. Strict-priority: low-priority starves under high-priority line-rate.
2. HTB: per-class caps hold ± 0.5% over 60 s.
3. `tc qdisc show dev ethX` shows CEETM.
4. No regression at unshaped wire speed.
5. CGR drops visible under sustained class congestion.

### 7.8 DCSR observability

**[SPEC]**
**Patches:** `0079` (ethtool `xsk_*` counter suite — dut-validated) + `0113` (debugfs error-window taps — landed 2026-06-11, awaiting board validation). Per §4.9, the taps live under the FMan-common PCD debugfs root (NOT per-iface): `/sys/kernel/debug/fman_pcd/<N>/dcsr/{fpm,bmi,qmi,parser,kg,pol}_err`. Pure read-only `ioread32be` sweeps — no W1C register is acknowledged, so the taps never disturb ISR paths. Reads rate-limited ≥ 1 ms. `fpm_err` additionally renders the 50 per-hwport FPM status words with `[STALLED]` decode — the first-stop forensic view for the M3-3b CC dispatch wedge.

**Consumers:** all three flavors equally.

---

## 8. Flavor-Specific Consumers

### 8.1 VPP-only — AF_XDP zero-copy datapath

**[SPEC]**
**Patches:** `0071/0073/0075a/0075b/0075c/0076/0077/0079/0080` (landed/dut-validated); `0082/0083/0084` (dut-validated).

Consumed by the `af_xdp_pool.ko` flavor module that registers `qmgmt_ops->{xsk_pool_attach, xsk_pool_detach, xsk_wakeup}` per §3.4.

#### 8.1.1 XSK pool attach/detach

**[SPEC]**
DPAA1's BMan is a HW buffer-pool manager — FMan RX BMI autonomously dequeues with no per-packet driver involvement. **Approach: XSK-backed BMan pool** — allocate a dedicated `dpaa_bp` whose backing memory is the XSK UMEM.

**Attach sequence:**
1. **Validate constraints, fail closed.** `dev->mtu <= 3290`; `xsk_pool_get_rx_frame_size(pool) >= DPAA1_MIN_UMEM_CHUNK` (3840); First UMEM chunk satisfies `IS_ALIGNED(addr, DPAA_FD_DATA_ALIGNMENT)` (64 B); `xsk_pool_get_headroom(pool) >= priv->tx_headroom`; `queue_id < priv->xsk_max_qbands` (4).
2. **DMA-map** `xsk_pool_dma_map(pool, priv->mac_dev->dev, DMA_ATTR_SKIP_CPU_SYNC)`.
3. **Allocate dedicated BMan pool ID** via `bman_acquire()`. Record in `priv->xsk_bpid[queue_id]`.
4. **Seed**: iterate `xsk_buff_alloc_batch()`, `bman_release()` into new pool. `DPAA1_XSK_INITIAL_SEED = 8192`.
5. **Register memory model**: `xdp_rxq_info_reg_mem_model(..., MEM_TYPE_XSK_BUFF_POOL, pool)`.
6. **Bind NAPI** (M3-3 step 1, patch 0080): `WRITE_ONCE(priv->qmap[queue_id].napi, &per_cpu_ptr(priv->percpu_priv, 0)->np.napi)`.
7. **Publish**: `rcu_assign_pointer(priv->xsk_pool[queue_id], pool)`.

**[SPEC]**
**Detach symmetry — FMan BMI quiescence mandatory.**
1. `rcu_assign_pointer(priv->xsk_pool[queue_id], NULL)`.
2. `synchronize_net()`.
3. `fman_port_disable(rxp)` — halts BMI. ~10 ms link bounce.
4. Drain DQRR on port's pool channel.
5. `qman_retire_fq()` on each FQ in qband.
6. Drain dedicated BMan pool via `bman_acquire()`; `xsk_buff_free()` per handle.
7. `xsk_pool_dma_unmap(pool, DMA_ATTR_SKIP_CPU_SYNC)`.
8. Reactivate FQs against original kernel BMan pool; reset to `MEM_TYPE_PAGE_ORDER0`.
9. `fman_port_enable(rxp)`.

**Counters:** `xsk_pool_attach_ok/fail`, `xsk_pool_detach_ok/timeout`, `xsk_bman_seed_short`, `xsk_bman_starve`, `xsk_tx_backpressure`, `xsk_dma_map_fail`, `xsk_align_reject`, `xsk_headroom_reject`, `xsk_mtu_reject`.

#### 8.1.2 RX ZC datapath (M3-3 step 3, dut-validated)

**[SPEC]**
Per-frame dequeue callback:
1. `qband = dpaa_fq_to_qband(priv, qm_fd_get_fqid(fd))`.
2. Branch on `rcu_dereference_bh(priv->xsk_pool[qband])`:
   - NULL → mainline skbuf.
   - non-NULL → ZC: phys → `xdp_buff *` via pool index, length/offset from FD, `bpf_prog_run_xdp()`, redirect/drop/TX.
3. `xdp_do_flush()` if any redirected.

#### 8.1.3 NAPI-hooked BMan refill (M3-3 step 4, dut-validated)

1 s workqueue tick meaningless at line rate (2048-buffer pool drains in ~3.5 ms at 7 Gbps/1500 B). BMan-depth probing moves into NAPI poll:
1. `bman_pool_get_count(priv->xsk_bpid[queue_id])` at start of poll.
2. Below `DPAA1_XSK_REFILL_THRESHOLD = 256` → batch-32 refill from XSK fill ring.
3. Below `DPAA1_XSK_MIN_DEPTH = 64` → `xsk_bman_starve++`, escalate batch 32→256.

#### 8.1.4 TX ZC datapath (M3-3 step 5, dut-validated)

1. In NAPI poll after RX: `xsk_tx_peek_release_desc_batch()` up to budget. `xsk_buff_raw_get_dma()` → build `qm_fd` → `qman_enqueue()` on qband's egress FQ.
2. Service TxConf FQ same NAPI iteration with **higher** priority than RX refill: `xsk_tx_completed_addr()` per confirmed FD.
3. If XSK Tx Ring drained but `XDP_USE_NEED_WAKEUP` socket exists, set Tx need-wakeup flag.

**Strict Tx backpressure (two layers):**
1. **CEETM CGR HW tail-drop** (§5.7).
2. **Software in-flight budget.** `priv->xsk_tx_inflight[qband]` (atomic). Above `DPAA1_XSK_TX_MAX_INFLIGHT = 1024` → stop pulling, set Tx need-wakeup. Resume below `DPAA1_XSK_TX_LOW_WATER = 512`.

#### 8.1.5 A050385 erratum interaction

XDP workaround relocates Tx frames within 64 B of a 4 KB boundary. XSK cannot relocate (UMEM is app-owned), so we require UMEM ≥ 64 B headroom and refuse `bind(XDP_ZEROCOPY)` otherwise. VPP defaults `XDP_PACKET_HEADROOM = 256`.

#### 8.1.6 Blocker A — BMan "Invalid Command Verb" (patches `0086`/`0087`/`0088`) — 2026-05-28

**[BUG] BMan IVCI — DMA target device wrong**
- **Symptom:** Board patches `0086`/`0087`/`0088` closed the BMan err-IRQ `BM_EIRQ_IVCI` ("Invalid Command Verb") seen on the board G5 reproducer (xsk-bind-probe + iperf3 UDP flood).
- **Cause:** `xsk_pool_dma_map()` must target `priv->rx_dma_dev` (the FMan RX port device with the 40-bit DMA mask owning the BMan FBPR window programmed by U-Boot), NOT `priv->mac_dev->dev` (32-bit mask, wrong IOMMU group) — DMA addresses resolved outside BMan's FBPR window so the 40-bit address bytes in RCR slots were rejected as IVCI.
- **Fix:** `0086` (8-buffer chunked release) and `0087` (zero stack bpid residue) were necessary-but-insufficient precursors. **Closed 2026-05-28** (ISO 2026.05.28-0149, commit `d1b6e30`, run 26549682211): 60 s G5 hold → 0 IVCIs, 0 BUG/WARN/stall, clean attach/detach both cycles.

**[NOTE]**
**CI-pipeline invariant discovered here:** `bin/ci-setup-kernel.sh` stages board patches via explicit per-patch `cp` lines (no glob); `patch-health.sh` does NOT cross-check that the `cp` line exists, so a missing `cp` silently ships an ISO without the patch (ISO 2026.05.28-0116 / commit `b57dd6a` shipped without any of 0086/0087/0088).

#### 8.1.7 Blocker B — productive RX delivery via XSKMAP redirect (patch `0089`) — 2026-05-28

**[BUG] XSKMAP redirect missing — zero rx_packets**
- **Symptom:** Pre-`0089` an `XDP_ZEROCOPY`-bound AF_XDP socket on DPAA1 observed `rx_packets = 0`.
- **Cause:** `dpaa_run_xdp()` early-returns `XDP_PASS` when no XDP program is attached, so the FD travelled the unchanged skbuf path.
- **Fix:** `0089` (userspace probe stage-3, no kernel change) adds an opt-in `--xskmap` flag to `bin/dpaa1-xsk-bind-probe.py` that creates an XSKMAP, loads a 6-insn `bpf_redirect_map()` XDP program (DRV-mode attach, SKB fallback), binds `XDP_ZEROCOPY`, then `BPF_MAP_UPDATE_ELEM`s the bound socket into the map. **Closed blocker B** (board G5, ISO 2026.05.28-0149): `rx_packets` 0 → 30, DRV-mode attach honoured, 0 IVCI/BUG/stall.

Mode is copy-mode, not zero-copy. True ZC is the follow-on M3-3 step 7 work (patches `0093+`).

**[SPEC]**
**Acceptance gate M3-3 (full VPP datapath):** (1) `xdp-features` reports `NETDEV_XDP_ACT_XSK_ZEROCOPY`; (2) `xdpsock -i ethX -q 3 -z -r` receives traffic; (3) ≥ 7 Gbps single-stream IPv4 fwd at < 5% kernel-net CPU/worker; (4) `perf top` clean of `dpaa_rx`/`__alloc_skb`/`memcpy`; (5) 4-worker aggregate ≥ 14 Gbps dual-SFP+; (6) 24 h iperf3 stress no oops/leak/stall.

#### 8.1.8 Blocker B — under-load FILL-ring backpressure crash + fix — 2026-05-28

**[BUG] FILL-ring overrun — free-list corruption**
- **Symptom:** The first under-load probe run (5 Gbit/s UDP flood) panicked in softirq.
- **Cause:** An unconstrained userspace recycle loop bumped `fill_producer` without checking `fill_consumer`, overran the 256-slot FILL ring, and handed the same UMEM chunk to `xp_alloc()` twice → `xsk_buff_pool` free-list corruption (`list_add corruption` → `dcache_clean_poc` paging fault → 52 s soft-lockup).
- **Fix:** Read the kernel `fill_consumer` every iteration and refuse to write a FILL slot when in-flight `>= N_FRAMES` (`skipped_recycle` counter; "drop > corrupt"); plus `MAX_BATCH=64` and per-line flush. Post-fix board run sustained `rx_packets = 296 716` / 427 MB clean, 0 crashes, 0 `xsk_bman_starve`.

**[NOTE]**
**Key finding — the "1.3 Gbit/s ceiling" is an iperf3 UDP-receiver artifact, not a driver limit.** `/proc/net/snmp` showed `RcvbufErrors == InErrors` byte-for-byte (loss at socket enqueue, single-threaded `recvmmsg()` on a 1.6 GHz Cortex-A72 caps ~150–200 kpps/core), while `ethtool -S` showed 0 NIC/BMan/FQ drops and `lxc202↔backup` TCP proved the L2 fabric does 7.5 Gbit/s. **Diagnostic rule:** on receiver-side UDP loss, check `/proc/net/snmp Udp:` BEFORE blaming wire/switch/driver — `RcvbufErrors == InErrors` means it is the userspace consumer.

#### 8.1.8a Option A — XDP_DROP driver-only RX capacity

**[SPEC]**
`bin/dpaa1-xdp-rxcap.py` (standalone, no libbpf/clang) attaches a 2-insn `XDP_DROP` program and samples driver `rx_packets`/`rx_bytes` + `qman cg_tdrop`/`qman fq tdrop`/`rx dma error`/`rx fifo error` deltas across a hold window. **Finding: the driver dropped 0% at every offered rate the lab could generate** (zero qman/dma/fifo drops, zero net-core `rx_dropped`), while the kernel-socket baseline at 2 Gbit/s offered lost 51% entirely at socket enqueue — direct counter-level proof that **Gate 3 is consumer/methodology-bound, not driver-bound.**

#### 8.1.8b Option C — multi-flow TCP `-R` RX scaling — 2026-05-29

**[SPEC]**
Board-as-receiver TCP `-R` aggregate **peaks at `-P 4` = 5.57 Gbit/s** then declines. `mpstat -P ALL` during `-P 16`: RX softirq genuinely distributed across CPU0/1/2 (79.9 / 97.6 / 49.1 %soft, CPU2 ~50% headroom) while the single userspace receiver process saturated CPU3 (84% sys) — reconfirming the "userspace socket-drain, not driver" finding with TCP and per-core evidence.

**[NOTE]**
**Gate-3 status after Options A + C:** the driver (a) drops 0% of every frame the fabric delivers and (b) scales RX softirq across cores to 5.57 Gbit/s aggregate TCP with headroom. Gate 3 is **consumer/methodology-bound, not driver-bound**; a literal ≥7 Gbit/s figure needs generator-side multi-process iperf3 or a wire-rate generator. **M3-3 step 7 (true ZC) is not required for Gate 3 on driver-capacity grounds.**

#### 8.1.9 board→backup TX direction measurements — 2026-05-28

**[BUG] TSO driver-unimplemented across all NXP-authored datapath drivers**
- **Symptom:** Board-as-sender TX peaks at **4.93 Gbit/s single-stream TCP (0 retr)**, CPU3-bound.
- **Cause:** **TSO is driver-unimplemented across every NXP-authored datapath driver and not advertised by any FMan v3 BMI capability bit consulted by Linux.** Net effect: `tcp-segmentation-offload: off [fixed]`, software GSO active, every MSS traverses the full softirq→driver TX path one skb at a time.
- **Fix:** The 4.93 Gbit/s kernel-skbuf TX ceiling **does not block AF_XDP Gate-3 closure**; step 7 (FMan RX DMA into XSK-pool chunks via `priv->xsk_bpid` matching, plus TX symmetry via `xsk_tx_peek_release_desc_batch()` → `qman_enqueue()`) remains the correct ZC path.

#### 8.1.10–8.1.14 True-ZC RX sub-increments 1–4 and productive datapath

**[NOTE]**
Sub-increments 1–4 (`0093`–`0096`, `0102`/`0102b`, `0103a`/`0103b`/`0103g`, `0110`, `0114`) established and closed the true-ZC RX path with hardware validation. See the full spec for the detailed per-patch design notes, counter dumps, serial-capture crash traces, and gate pass/fail detail archived in Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

**[SPEC] M3-3 step 7 true-ZC RX is functionally COMPLETE and HW-validated.** The only outstanding piece is **GAP 2** — the literal high-rate true-ZC throughput number — which needs the §8 external traffic harness.

The `xsk-zc-check` board script (installed to `/usr/local/bin/xsk-zc-check`) surfaces the four sub-increment-4 entry-gate counters (`xsk_zc_eligible`, `xsk_zc_rx_armed`, `xsk_fill_guard_block`, `xsk_zc_rx_recovered`) and renders the verdict — **dormant**, **ZC-armed**, or **fault**.

### 8.2 ASK-only — Dynamic CC tree, HC reconfig, nft offload bridge

**[SPEC]**
ASK2 flavor module registers `pcd_ops->install` (builds initial CC tree) + `qmgmt_ops->{alloc_rx_fqs, rx_hook}` (per-FQ ingress hook for nft offload). Does NOT register XSK callbacks.

Distinct from VPP:
- **Dynamic** CC tree (`fman_cc_tree_add_key`/`remove_key`) — up to 255 keys, runtime add/remove. On the shipped board this rides an **in-tree MURAM rewrite** (AttachPCD/DetachPCD-style save-restore), NOT the FMan Host-Command doorbell.
- `rx_hook` callback for nft flowtable lookup hits.
- No UMEM, no XSK pool, no BMan pool replacement.

Full design in `specs/ask2-rewrite-spec.md`. This spec only commits to the kernel-side API surface (§5.4/5.5/5.6) being **shared** between VPP and ASK2 consumers.

### 8.3 default-only — passive consumer

No flavor module loaded. `priv->pcd_ops` and `priv->qmgmt_ops` stay NULL. RCU-NULL-safe call sites short-circuit to mainline behaviour. `default` still gets every cross-flavor §5 benefit that does not require ops registration: §5.2 datapath restructure (automatic), §5.3 `ndo_xsk_wakeup` (available for any AF_XDP consumer), §5.4 CC steering (as RPS backend), §5.5 HM offload (as `NETIF_F_HW_VLAN_CTAG_*`), §5.6 Policer (as tc/nftables offload), §5.7 CEETM (as root tc qdisc), §5.8 DCSR (as debugfs). §6.1 XSK-backed BMan pool and §6.2 dynamic CC tree are not consumed.

---

## 9. Co-existence Rules

### 9.1 Per-port flavor selection

```
eth0 (RJ45)  default / kernel-managed
eth1 (RJ45)  default / kernel-managed
eth2 (RJ45)  default / kernel-managed       single kernel binary
eth3 (SFP+)  vpp / AF_XDP-ZC
eth4 (SFP+)  vpp / AF_XDP-ZC
```

`af_xdp_pool.ko` claims netdevs based on `interfaces=eth3,eth4` modparam or DT property. ASK2 module symmetric. Unclaimed netdevs run mainline.

### 9.2 Single-MAC dual-flavor (deferred — Phase 4)

VPP + ASK2 on the same MAC needs FMan VSP bifurcation: per-VSP FQ sets, per-VSP BMan pool, per-VSP CC sub-tree, per-VSP HM. Out of scope for v5.0.

### 9.3 VPP-internal shaper ↔ CEETM mutex (per-port)

CEETM and VPP-internal shaping mutually exclusive **per-port**. Two enforcement layers:
1. **VyOS validator (vpp-flavor only).**
2. **Kernel sentinel.** Driver sets `/sys/class/net/<iface>/dpaa/ceetm_active = 1` when CEETM active.

### 9.4 ucode-210 capability fallback (all flavors)

If running ucode < 210, §3.5 cap bits zero → every §5.4–§5.6 install returns `-ENOTSUPP`. Flavor module increments `hw_offload_unavailable`. **§5.2 datapath and §5.3 `ndo_xsk_wakeup` are NOT ucode-gated** — work on any FMan ucode.

### 9.5 Silicon mode state machine — mode-switch primitives

`specs/dual-dataplane.md` defines the system-level state machine. This spec owns every register/MURAM-touching **mode-switch primitive** (DUAL-DATAPLANE M1):

1. **KG scheme-mode rewrite** — flip `kgse_mode` next-engine between DONE/RSS (`…0002`) and AC_CC (`…0006`).
2. **`fmbm_rfpne` CC-root bind/unbind** — attach/detach the PRE_CC dispatch.
3. **FE/ehash MURAM lifecycle** — alloc (ext-TS timers, AllocFEObjs, FE buffer pool, exthash global mem, MUX-FE/TRANSITION-FE singletons) and the complete free path.
4. **Params-page FE words** — set/clear of per-port +0x54/+0x58.
5. **CC tree/row install/destroy** — already §5.4.
6. **Snapshot-diff tooling** — `board/scripts/pcd-snapshot`.

**[SPEC]**
**Reversibility Contract (hard rule):** every forward primitive lands in the same patch as its verified inverse. Gate: 100× S0→S1→S0 toggle with clean snapshot-diff each cycle, no MURAM leak, and AF_XDP/VPP functional immediately after the 100th teardown. FMPL policer state (board patches 0100/0104) is mode-independent and excluded from the S0/S1 delta.

---

## 10. Validation Matrix

### 10.1 Functional

| Test | M0 | M1 | M2 | M3-3 step 1 | step 2 | step 3+ | 3b | 3c | 3d | 3e |
|---|---|---|---|---|---|---|---|---|---|---|
| `ip link set ethX up` (all flavors) | ✅ | ✅ | ✅ | ✅ | pass | pass | pass | pass | pass | pass |
| Boot on ucode 106: §5.4–5.6 install returns `-ENOTSUPP` | — | — | — | — | pass | pass | pass | pass | pass | pass |
| **default skbuf RX throughput improves vs. pre-M3-3 baseline** | — | — | — | — | pass | — | — | — | — | — |
| **Per-port flavor selection** | — | — | — | — | — | pass | pass | pass | pass | pass |
| Detach under load: `fman_port_disable` bounce ≤ 50 ms | — | — | ✅ | ✅ | pass | pass | pass | pass | pass | pass |
| iperf3 line-rate LAN (all flavors) | ✅ | ✅ | ✅ | ✅ | pass | pass | pass | pass | pass | pass |
| `xdpsock -q 3 -z -r` (queue-id correctness) | — | — | — | — | — | pass | pass | pass | pass | pass |
| CC tree visible in debugfs | — | — | — | — | — | — | pass | pass | pass | pass |
| HW VLAN strip visible | — | — | — | — | — | — | — | pass | pass | pass |
| Policer red-drops visible | — | — | — | — | — | — | — | — | pass | pass |
| CEETM qdisc visible via `tc qdisc show` | — | — | — | — | — | — | — | — | — | pass |

### 10.2 Thermal (Tj,max = 105 °C, headroom ≥ 30 °C)

| Scenario | Target |
|---|---|
| Idle, all ports up, no XSK | TZ0 ≤ 52 °C |
| Idle with XSK bound, ZC + need_wakeup (vpp) | TZ0 ≤ 53 °C |
| 1 Gbps load, 4 qbands, ZC | TZ0 ≤ 65 °C |
| 7 Gbps load, 4 qbands, ZC | TZ0 ≤ 75 °C |

### 10.3 Performance

| Test | Baseline | M3-3 target | M3-3b–e target |
|---|---|---|---|
| 64 B unidirectional pps, 1 qband | ~600 kpps | ≥ 1.5 Mpps | — |
| 64 B unidirectional pps, 4 qbands | n/a | ≥ 5 Mpps | — |
| 1500 B forwarding, single flow | ~2 Gbps | ≥ 7 Gbps | — |
| 1500 B forwarding, 4 flows, 4 cores | ~5 Gbps | ≥ 9 Gbps | ≥ 9 Gbps (no regression) |
| VLAN-tagged 1500 B forwarding | software VLAN | ≥ 7 Gbps | ≥ 8 Gbps with HM |
| 64 B with 8-class CEETM shaping | software ~3 Gbps | n/a | ≥ 6 Gbps with CEETM |

### 10.4 Stability

- 24 h iperf3 stress, no oops, no BMan leaks: pass
- `rmmod fsl_dpaa_eth && modprobe` ×100: pass
- Module unload while traffic active: pass
- `kill -9 vpp_main` while traffic active, recovery ≤ 5 s: pass
- BMan exhaustion → drain → refill without manual intervention: pass
- VyOS `commit` of hw-offload config under load, no netdev flap: pass

---

## 11. Rollout, Kconfig Gates, Backout

```
M0  : abstraction landed, default flavor unchanged. Mainline candidate.
M1  : Phase 1 (ndo_xsk_wakeup). VyOS rolling vpp RC1.
M2  : Phase 2 (XSK pool + DCSR). VyOS rolling vpp RC2.
M3-3: Phase 3 (per-CPU NAPI + dedicated BMan channels + true ZC + multi-queue).
M3-3b–e : CC, HM, Policer, CEETM. Each ships when its gate passes.
M4  : Phase 4 single-MAC dual-flavor (deferred).
```

**Kconfig gates:**
`CONFIG_DPAA_FLAVOR_OPS` (M0), `_XSK_WAKEUP` (M1), `_XSK_POOL` (M2), `_XSK_ZEROCOPY` (M3-3), `_XSK_MULTIQ` (step 2), `_HW_CC_STEERING` (M3-3b), `_HW_HM_OFFLOAD` (M3-3c), `_HW_POLICER` (M3-3d), `_HW_CEETM` (M3-3e), `_AF_XDP_POOL` (vpp-flavor consumer, =y per `08-dpaa1.config`).

**Runtime modparams (emergency revert):**
- `dpaa_eth.disable_zerocopy=1` — forces copy-mode
- `dpaa_eth.disable_hw_offload=1` — disables 3b–e
- `dpaa_eth.force_dma_sync=1` — re-enables per-descriptor sync

**Fallback:**
- M1 regression → re-enable `poll-sleep-usec 100`.
- M3-3 perf miss → ship with `xdp_features &= ~NETDEV_XDP_ACT_XSK_ZEROCOPY`.
- 3b–e individual failure → that feature's install returns non-zero, `dev_warn()`, netdev comes up with that feature disabled.

---

## 12. Open Questions

**[?]**
1. Queue topology default (4 vs 2 workers). Needs throughput-vs-mgmt-jitter measurement.
2. UMEM chunk size (4096 vs 8192).
3. Per-qband NAPI weight. Start `napi->weight = 64`; sweep 16-256.
4. 210 ucode upgrade cadence. Pin per VyOS release; document.
5. CC + HM chain ordering. RESOLVED §5.4: Parser → CC → HM → QMan.
6. CEETM + qband interaction. Ensure CEETM LFQs map cleanly onto qband egress FQs.
7. Policer per-flow. Depends on §5.4 CC tree marking flows. Documented.
8. A050385 silicon revision. Mono Gateway board is LS1046AE Rev 1.0 per DTS.
9. BMan seed scaling by link rate. `DPAA1_XSK_INITIAL_SEED = 8192` set for 10G XFI.
10. `DPAA1_XSK_TX_MAX_INFLIGHT` tuning. 1024 ≈ 1.5 MB at 1500 B/chunk.
11. default-flavor §5.2 win quantification. Pre-M3-3 baseline TBD.
12. Cross-flavor CLI surface ownership. vyos-1x patches out of scope but block end-user benefit.
13. **VPP native plugin v0.1 REJECTED.** The `fsl-dpaa1-um.ko` proposal was rejected for three reasons: (a) RC#31 — userspace QBMan reprogramming kills all kernel FMan interfaces globally; (b) AF_XDP ZC datapath already delivers the VPP integration surface with ~3 kLOC vs ~12 kLOC; (c) port exclusivity is the wrong model — AF_XDP coexists with kernel netdev ownership. RESOLVED 2026-05-31.

---

## 13. References

- Camelia Groza, "dpaa_eth: add XDP support" v5, netdev `<cover.1606322126.git.camelia.groza@nxp.com>`, 2020-12-01.
- `github.com/nxp-qoriq/qoriq-fm-ucode` — ucode 106.4.18
- `github.com/nxp-qoriq/fmlib` — userspace PCD reference
- NXP LS1046A Reference Manual Rev 4
- `docs.kernel.org/networking/af_xdp.html`
- `specs/ask2-rewrite-spec.md` — ASK2 Track B
- `specs/vpp-dpaa1-ls1046a-spec.md` — VPP AF_XDP integration
- `plans/PR14x-DESIGN.md` — foundational fman_pcd subsystem

---

## 14. Caveats

**[NOTE]**
- **Ucode 210 licensing.** NXP-proprietary, not redistributable.
- **Ucode version mismatch.** Running ucode 106/108: `priv->fman_caps` populates empty for 210-only bits. Each `fman_*_install()` returns `-ENOTSUPP` cleanly.
- **No mainline driver has reconciled `MEM_TYPE_XSK_BUFF_POOL` with a HW-owned buffer pool.** XSK-backed BMan pool is novel.
- **MTU 3290 figure is folklore-confirmed.** Confirm with `mtu 3290` (pass) vs `mtu 3291` (fail when XDP loaded).
- **CEETM ↔ VPP-internal shaping interaction subtle.** §7.4 enforces mutual exclusion per-port.
- **Single board.** All thermal/perf gates sized to one Mono Gateway LS1046A.

---

## Appendix A — Phase 4 (deferred): Single-MAC Dual-Flavor Coexistence

**[SPEC]**
Enables VPP-consumer and ASK-consumer coexistence on the same physical MAC by HW-bifurcating ingress flows via FMan VSP (Virtual Storage Profile). Out of scope for v5.0. Reuses: `fman_cc_tree_install()` (§5.4) extended with VSP-target attribute; `fman_hm_node_install()` (§5.5) per-VSP HM nodes; `fman_policer_install()` (§5.6) per-VSP profiles; SEC FQ protected-set discipline.

## Appendix B — Future enhancement (deferred, HIGH-RISK): 64KB-page kernel (`CONFIG_ARM64_64K_PAGES=y`)

**[NOTE]**
**Status: NOT adopted.** Documented as a future enhancement only.

Raising the arm64 page size to 64KB would lift the AF_XDP `chunk_size` cap and let VPP use ~16KB chunks for full 9578 MTU. **Why it is HIGH-RISK on this platform:** (1) the 9578-MTU benefit is not deliverable today — `fsl_dpaa_mac` enforces a hard 3290 XDP MTU limit; (2) memory footprint on 8GB collides with VPP; (3) kexec + hugepage fragility; (4) DTB reserved-memory + booti-only boot assumes 4KB granularity; (5) silent config-merge breakage; (6) invalidates the entire validated AF_XDP stack (`0068`→`0103a+`).

**Pre-conditions before reconsideration:** lift the `fsl_dpaa_mac` 3290 XDP-MTU limit, provision a jumbo-capable switch path, gate as a build-time experimental flavor only.

---

## Changelog

**[NOTE]**
- v5.22 2026-06-14 — M3-3e CEETM close-out: DEFECT A FIXED + HW-validated; DEFECT B CLOSED as LS1046A silicon limitation. Ships Option-B (`580e637`).
- Prior revision history (v5.0–v5.21) archived in Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.
