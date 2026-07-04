# DPAA1 + VPP + ASK2 — Consolidated Completion Plan
**Version 1.3.0** · 2026-07-04 · HADS 1.0.0

---

## AI READING INSTRUCTION

Read `[SPEC]` and `[BUG]` blocks for authoritative facts.
Read `[NOTE]` only if additional context is needed.
`[?]` blocks are unverified — treat with lower confidence.

---

## 1. METADATA & SOURCE-OF-TRUTH

**[SPEC]**
- Date: 2026-07-04.
- Branch: `puddle-cornet` (ASK2 work, mainline 6.18.34) · `nxp-sdk` (ASK 1.x vendor oracle, lf-6.12.49, frozen reference).
- Status: Active roadmap — single cross-flavor view of all remaining work.
- **For ASK2 detailed plan see [`plans/ASK2-DEVELOPMENT-PLAN.md`](ASK2-DEVELOPMENT-PLAN.md) (v1.1.0) — this document only provides the high-level status summary and context.**

**[SPEC]**
Authoritative specs (source-of-truth; this doc only sequences them):
- `specs/dpaa1-afxdp-modernization-spec.md` (v5.22) — the cross-flavor DPAA1 driver. One driver core + two ops tables (`pcd_ops`, `qmgmt_ops`); the FMan PCD subsystem lives in the common board stack (built-in for `default`/`vpp`/`ask`).
- `specs/vpp-dpaa1-ls1046a-spec.md` (v0.2) — VPP flavor over AF_XDP (native DPDK plugin rejected, Appendix A).
- `specs/ask2-rewrite-spec.md` (v1.7) — ASK2 modern FMan-210 offload (`ask.ko` consumes the common PCD stack via `pcd_ops`).

**[NOTE]**
This plan is a router, not a second source-of-truth. Where it disagrees with a spec, the spec wins — update this doc. All structural/architectural decisions are already settled in the specs; what remains is forward-port volume, datapath debug, and lab/harness provisioning.

---

## 2. ONE-PARAGRAPH SUMMARY

**[NOTE]**
The DPAA1 driver core is board-validated and shipping in the `default`/`vpp` ISOs. The two big kernel forward-ports are **DONE**: the FMan PCD subsystem (common board stack, `0092`/`0097`–`0101`) and the QMan-CEETM driver (`0111`/`0112`, shipped + closed). M3-3b CC steering, M3-3c HM, M3-3d policer BUG 3a + 3b-non-revert, true-ZC RX, and M3-3e CEETM are all closed / HW-validated. What remains is (1) lab/harness quantitative gates — the literal ≥7 Gbps figure, the policer 2.5 Gbps cap number, the M3-3c 802.1Q wire gate; (2) the BUG 3b flood-crash characterization (serial + cold power-cycle); and (3) the ASK2 `puddle-cornet` work-stream, now active on the Fork-B FE/eHash path with Phase 1 `0133` as the immediate dispatch gate.

---

## 3. STATUS AT A GLANCE

**[SPEC]**
```mermaid
graph TD
    subgraph CORE["DPAA1 driver core — DONE / shipping"]
        M0["M0 ops abstraction + capability layer ✅"]
        AFXDP["AF_XDP datapath + XSKMAP RX ✅"]
        PCD["FMan PCD subsystem ✅ (0092/0097–0101)"]
        CEETM["QMan-CEETM shaper ✅ (0111/0112)"]
        ZC["True ZC-RX oracle ✅ (0102b/0103g/0110)"]
    end
    subgraph OPEN["Remaining — lab + deferred"]
        GEN["Quantitative wire gates<br/>(≥7G literal · policer cap · 802.1Q HM)"]
        FLOOD["BUG 3b flood-crash characterization"]
        ASK2["ASK2 Phase 1 AC_CC arm gate\n(puddle-cornet)"]
    end
    CORE --> OPEN
    GEN -->|same MURAM gen_pool| ASK2
```

**[SPEC]**

| Flavor | Substrate | Functional state | Single biggest blocker |
|---|---|---|---|
| **default** | common DPAA1 core ✅ | M3-3b/3c/3d/3e all closed / HW-validated; true-ZC RX closed | quantitative wire gates + the BUG 3b flood-crash characterization (lab) |
| **vpp** | common DPAA1 core ✅ (AF_XDP) | plumbed + shipping in CI; **not benchmarked on HW after the patch-022 AF_XDP cutover** | a HW benchmark run |
| **ask** | common DPAA1 core ✅ (FE/ehash chain dormant in every ISO since 2026-06-14) | AC_CC arm (0133) authored + compiled, awaiting one-shot board experiment; FE context (0135), TX bypass (0136), MANIP chain (0137 v2), CAAM QI share (0134) all LANDED dormant; ASK1 bridge-offload oracle (nxp-sdk) confirms L2 path + validates switchdev ask_bridge plan | the one-shot Phase 1 AC_CC arm experiment (D9-B) — make-or-break M2 dispatch test |

---

## 4. DPAA1 (`default`) COMPLETION

**[SPEC]**
Remaining items in dependency order (mirrors the spec's "What remains for a complete DPAA1 driver" table, §60–75).

### 4.1 FMan PCD subsystem forward-port ✅ DONE

**[SPEC]**
Landed in the common board stack (`0092`/`0097`–`0101`, bridge idiom; 64 KiB MURAM reserved, `caps = 0x17`). Unblocked M3-3b CC steering (CLOSED 2026-06-12) and underpins the live M3-3c/M3-3d productive paths. Same PCD/MURAM substrate ASK2 will consume via `pcd_ops`.

### 4.2 QMan-CEETM shaper ✅ SHIPPED + CLOSED (2026-06-14)

**[SPEC]**
`0111` (`qman_ceetm.c` object model + MC helper) + `0112` (`TC_SETUP_QDISC_HTB` offload consumer) supersede the `0104b` stub. DEFECT A FIXED; DEFECT B closed as a documented LS1046A silicon limitation (product-impact NONE — see spec §5.7). HW hierarchical egress shaping live as a stock `tc htb offload` qdisc; the literal rate-cap accuracy number wants the §8 generator.

### 4.3 True ZC-RX productive oracle ✅ DONE (2026-06-10)

**[SPEC]**
`xsk_zc_rx_redirect` fires + reproducible; BMI BPID flip proven (`0102b`), NULL-`xdp.rxq` crash fixed (`0103g`), NAPI-only flush (`0110`). Crash-free + reversible. **Open:** GAP 2 — the literal high-rate true-ZC throughput number (needs the §8 peer-flood harness; NOT gate-3-blocking).

### 4.4 Policer datapath (M3-3d) — BUG 3a + 3b-non-revert FIXED + HW-validated; flood-crash half OPEN

**[SPEC]**
Steering + BUG 3a (FMPL block master-enable `GCR.EN|STEN` clear at boot) + the BUG 3b non-revert half (release-cb scheme revert) all FIXED + HW-validated (image `2032`, `1a48948`, `0100`/`0104` + `vyos-1x-025`). Register-proof trail in Qdrant `topic=dpaa1-ingress-policer-bug3a-3b`. **Open:** (1) the iperf3 flood-crash half of BUG 3b (serial capture + cold power-cycle; watchdog-reset risk — always characterize with pings, never a flood); (2) the literal 2.5 Gbps cap + red-drop number on the §8 harness.

### 4.5 HM functional datapath gate (M3-3c) — lab-blocked

**[SPEC]**
- State: feature live on hardware (cap `0x17`, `rx-vlan-offload: on`, MURAM 0→144→0 proven 2026-06-07); `vyos-1x-024` CLI shipped + live on the board. No kernel work, no CLI work.
- Remaining: a controllable 802.1Q tagged source to prove the §5.5 strip/insert gate. Lower silent-fail risk than the policer (VLAN-strip has a normal kernel SW fallback).

### 4.6 Literal ≥7 Gbps gate-3 figure — ≥7G PROVEN; single-stream line-rate deferred

**[SPEC]**
- gate-3 ≥7 Gbps PROVEN (7.41 Gbit/s @4 flows, 2026-06-12, §8 harness). A literal single-stream line-rate figure still wants a multi-process iperf3 server (split receiver across cores) or a wire-rate generator (TRex / DPDK-pktgen). No kernel work.

### 4.7 DCSR error observability (§5.8) — incremental

**[SPEC]**
- `0079` landed; remaining debugfs error-window taps (`{bmi,parser,kg,pol}_err`, §4.9) are incremental, no blocker.

---

## 5. VPP CONSUMER-MODE COMPLETION

**[SPEC]**
- State: plumbed and shipping in every image. AF_XDP datapath on the SFP+ ports (`fsl_dpa` → `driver='xdp'`, patch `vyos-1x-022`); native VyOS CLI (`set vpp settings …`); dormant until configured. Native DPDK plugin path is rejected (RC#31; spec Appendix A).
- Remaining (no architecture work):
  1. HW benchmark — the flavor has not been benchmarked on hardware since the patch-022 AF_XDP cutover. Confirm the ~3.5 Gbps SFP+ figure, thermal behaviour (`poll-sleep-usec 100` mandatory), and the MTU ≤3290 AF_XDP constraint hold.
  2. Hugepage / kexec one-shot — verify the `set vpp settings`-triggered hugepage kexec still lands cleanly on the 6.18.x kernel.
  3. Feeds the shared §8 generator dependency for any literal throughput claim.

---

## 6. ASK2 COMPLETION (`puddle-cornet` BRANCH)

**[SPEC]**
- State: `kernel/flavors/ask/` is no longer scaffold-only. The common FMan-PCD substrate, FE/eHash dormant chain, `ask.ko` control plane, CAAM QI share, TX-confirm bypass, MANIP chain API, and flow-offload backend slot are all present and shipping dormant. The offload still remains functionally inactive until Phase 1 proves the real AC_CC classifier→FE arm on hardware and Phase 2 wires real flow population into `ask.ko`.
- Runtime model: single image, config-driven late-bind. Boot lands S0 mainline/RSS; ASK enters S1 only after `set system offload ask` exists and its future commit path loads/engages ASK. Any ASK teardown must return byte-exactly to S0 before VPP can run.
- Components still to land: productive FE/eHash flow population in `ask.ko`, `ask_bridge.ko` switchdev implementation, `ask_xfrm.c` + `ask_caam.c` packet-mode IPsec, YNL/op-mode polish, and VyOS CLI/validator (`set system offload ask`, ASK↔VPP mutex). Userspace daemon = 0 (single YNL family; no `askd`, no `libfci` ABI).

**[BUG] ASK2 M2 CPU gate FAILED (Fork A, 2026-05-25) — superseded by Fork B FE/ehash path**
- The `[BUG]` block below is historical: Fork A (exact-match `CONT_LOOKUP` / `FORWARD_FQ_WITH_MANIP`) was proven DEAD on 210.10.1 microcode — iter-49/50 fault-capture showed it stalls with no latched fault. Fork A is NOT the M2 path. **Fork B** (external-hash + FE opcode VM, assembled dormant in board patches `0122`→`0131`) is the active M2 dispatch test; its full sequenced plan lives in [`plans/ASK2-DEVELOPMENT-PLAN.md`](ASK2-DEVELOPMENT-PLAN.md). The MANIP-dedup MURAM guard remains required for Fork B Phase 2, but as a per-next-hop dedup via `fman_hm_nexthop_get/put` (`0120`), not as the M2 unblock.
- Symptom preserved for historical reference: 327× `fman_pcd_manip_chain_create(3 manips) failed: -12` (`-ENOMEM`) — every per-flow L2-rewrite chain fails, rewrite stays on CPU. Root cause: O(flows) MANIP allocation exhausts tiny MURAM. Fix: dedup by adjacency (shared MANIP per next-hop).

**[NOTE]**
Cross-flavor leverage: the PCD-subsystem forward-port is already shared in common (consumed via `pcd_ops`). The per-next-hop MANIP-dedup cache lives in `ask.ko`'s flow-offload path, but the underlying shared-MANIP refcount API (`fman_pcd_manip_*`) belongs in the common board stack so the default-flavor CC tree can reuse it.

### 6.1 ASK2 build order (refreshed 2026-07-04, aligned with ASK2-DEVELOPMENT-PLAN v1.1.0)

**[SPEC]**
Build order is **bottom-up by dependency layer**, NOT module-by-module. `ask.ko` is built incrementally in layers and `ask_bridge.ko` lands late. The spec §14 numbered list still names the deleted `ask_hostcmd.c` (step 4) and `askd` (step 12) — both removed in v1.3 (YNL-only); ignore them.

1. **Substrate (mostly DONE, common / `main`)** — the FMan-PCD subsystem (`0092`/`0097`–`0101`) + `dpaa_flavor_ops` RCU hooks; `ask.ko` is a `pcd_ops` consumer. Remaining substrate task = the productive CC-forwarding wiring (group_off getter → `fman_port_set_cc_base` call-site → `attach_cc` on the RSS scheme) + the shared-MANIP refcount API. Gates M2; shared with DPAA1.
2. **`ask.ko` skeleton** — builds, **signs** (`MODULE_SIG_FORCE`), loads with `LOCALVERSION=-vyos`; in-tree patches applied (0004 stub OK).
3. **`ask.ko` control plane** — `ask_main.c` + `ask_genl.c` → YNL family `ask` (`ASK_CMD_GET_INFO`); verify `ynl --family ask --do get-info`.
4. **`ask.ko` flow core** — `ask_flow.c` (rhashtable+RCU) → `ask_flow_offload.c` (`flow_block_cb`; nft `flow add` reaches the callback). **Substantially built today.**
5. **Arm the FE datapath → M2 gate (current blocker)** — Fork B, not Fork A. The dormant `0122`→`0131` FE/ehash chain was byte-validated against the oracle and torn down clean on HW (Phase 0 PASS, 2026-06-16). The **real AC_CC arm** (`0133`, board `fman-pcd-fe-arm-real-accc`, `KGSE_MODE 0x80000006`) is authored + compiled, correcting the `0132` CCBS placebo. One-shot board experiment pending: build the FE chain → `fe_flow add` a real test key + live `fe_enq` FQID → `fe_arm engage` → ping → HIT-path verdict. Also landed dormant under Phase 2: `0135` (FE context builder), `0136` (TX confirm bypass, wired into `ask_hw.c`), `0137` v2 (MANIP chain API with HMAN_OC 0x34 fix), `0145` (flow-offload backend slot). Full detail: [`plans/ASK2-DEVELOPMENT-PLAN.md`](ASK2-DEVELOPMENT-PLAN.md) Phases 0–2.
6. **Broaden flow types + `ask_bridge.ko`** — IPv6, mcast, then L2-bridge. `ask_bridge.ko` lands **after** the IPv4 datapath passes M2 — it is NOT a peer of `ask.ko`.
7. **`ask_xfrm.c`** — `xdo_dev_state_add` + CAAM shared descriptors. The CAAM QI descriptor-sharing API (`0134`, `caam_qi_ext_consumer_register`) is **already landed** in the common board tree (2026-06-17, CAAM stack forced `=y` incl. `CONFIG_CRYPTO_DEV_FSL_CAAM_QI`). The remaining work is the `xfrmdev_ops` packet-mode consumer in `ask_xfrm.c`. **M4: AES-CBC-SHA256 @ 3 Gbps** (GCM is refused per spec §5.3 — the §11.1 AES-GCM-128 gate is a contradiction to reconcile; see ASK2-DEVELOPMENT-PLAN §4.5).
8. **YNL schema finalize + VyOS CLI** — `set system offload ask`; op_mode calls `ynl` from Python (no daemon).
9. **VPP coexistence + soak** — global ASK↔VPP mutex; the Reversibility-Contract gate (100× toggle, pcd-snapshot diff clean, VPP works after the 100th teardown) → v1.0 RC.

**[SPEC]**
Direct answer: **`ask.ko` first** (built in layers 2–5), **`ask_bridge.ko` later** (step 6) — but the true prerequisite to both is the common PCD substrate (step 1, already landed via DPAA1). The immediate next step is the **Fork-B FE arm (D9-B)**, gated behind a clean `fe_*` byte-validation of the dormant `0122`→`0131` chain; the MURAM MANIP-dedup is a Phase-2 guard under that path, not the M2 unblock.

---

## 7. RECOMMENDED SEQUENCING

**[SPEC]**
The forward-ports and datapath debug are DONE (PCD, CEETM, true-ZC, CC steering, policer 3a/3b-non-revert all closed). What remains is sequencing-free:
1. Run the quantitative wire gates on the §8 harness (≥7 Gbps literal, policer 2.5 Gbps cap + red-drops, M3-3c 802.1Q tagged source).
2. Characterize the BUG 3b flood-crash (serial capture + cold power-cycle) — riskiest, do last.
3. VPP HW benchmark on 6.18.x.
4. ASK2 (active on `puddle-cornet`, mainline 6.18.34): follow the **§6.1 build order** and [`plans/ASK2-DEVELOPMENT-PLAN.md`](ASK2-DEVELOPMENT-PLAN.md) — Phase 1 one-shot AC_CC arm experiment (the make-or-break M2 dispatch test) is the immediate next step. Then Phase 2 ask.ko FE-wiring + 0136 TX bypass + MANIP-dedup MURAM guard, then `ask_bridge.ko`/IPsec/CLI. The ASK 1.x nxp-sdk vendor-stack lineage (lf-6.12.49) is a **frozen reference oracle** — it proved bridge offload works end-to-end without `auto_bridge.ko` (validates the small switchdev `ask_bridge.c` plan), the 210 ucode accepts full PCD programming, and PCD miss-blackhole + VyOS notrack are cross-effort traps to avoid.

---

## 8. THE TRAFFIC HARNESS — PROVISIONED 2026-06-08

**[SPEC]**
- Five separate acceptance gates were lab-blocked on the same missing piece — a controllable traffic generator on the board SFP+ peers. Now resolved.
- The harness is two purpose-built Proxmox LXCs on heidi (`192.168.1.15`, root via `ssh heidi`), one per board SFP+ subnet, with the board as their L3 gateway so all CT201↔CT202 traffic is forced through the board router (eth3 → ip_forward → eth4).
- Full reference: `plans/TRAFFIC-HARNESS.md`.

| Peer | LXC | IP / gw | Board port |
|---|---|---|---|
| eth3 peer | CT201 `lxc201` | `10.99.1.2/30` → `10.99.1.1` | Board eth3 |
| eth4 peer | CT202 `lxc202` | `10.11.1.2/29` → `10.11.1.1` | Board eth4 |

**[SPEC]**
- Both Debian 12 with iperf3 preinstalled, on the 10G `vmbr0`→`enp35s0f1` (ixgbe) bridge.
- Validated end-to-end 2026-06-08: `TTL=63` one-hop, 0% loss, 4.14 Gbit/s @ 8 TCP streams routed through the board (default-flavor software-forwarding floor).

| Gate | Needs | Harness coverage |
|---|---|---|
| M3-3c HM wire test | controllable 802.1Q tagged source | needs scapy/TRex (bridge is untagged) — see SR-IOV upgrade in harness doc |
| M3-3d policer throughput cap | >2.5 Gbps offered source, red-drop visibility | `iperf3 -u -b 9G` ✅ |
| Gate-3 ≥7 Gbps literal | multi-core iperf3 / wire-rate generator | `iperf3 -P`; TRex via SR-IOV VF for true line-rate |
| VPP flavor benchmark | sustained >3.5 Gbps SFP+ source | ✅ (MTU ≤3290 on AF_XDP) |
| ASK2 M2 (≥7 Gbps @ ≤5% CPU) | eth3↔eth4 forwarding load at line rate | CT201→board→CT202 ✅ |

**[SPEC]**
- Wire-rate / 802.1Q upgrade (deferred): `enp35s0f1` exposes 63 SR-IOV VFs; pass a VF into a dedicated LXC for TRex/DPDK-pktgen when iperf3 cannot hit the literal ≥7 Gbps figure or when precise 802.1Q stateless generation is required.
- Do NOT bind the PF to DPDK (would drop the bridge + existing harness). `main` (production gateway) is off-limits as a generator.

---

## 9. DEFINITION OF DONE (PER CONSUMER MODE)

**[SPEC]**
- default: M3-3b CC steering productive tree installs + steers on board; M3-3c/3d/3e wire gates pass on the generator; gate-3 literal ≥7 Gbps measured; DCSR error taps complete. (Core already done.)
- vpp: HW benchmark recorded (throughput + thermal + MTU constraint verified) on 6.18.x; hugepage-kexec one-shot confirmed.
- ask: ASK2 components landed (`ask.ko`/`ask_bridge.ko` + PCD `0092`–`0145` + YNL `ask` family); M2 hard gate PASSES (≥2 Gbps at ≤5% kernel-net CPU, stretch ≥7 Gbps) after the FE-arm + MANIP-dedup + TX-bypass integration; `set system offload ask` engages a real offload (no longer a no-op). Phase 0 FE-chain byte-validation already PASSED; Phase 1 AC_CC arm (`0133`) awaits the one-shot board experiment.
