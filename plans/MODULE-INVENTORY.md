# ASK2 Module Inventory — Delivered & Planned

**Date:** 2026-08-05 (status correction; original 2026-08-01)
**Version:** 2.1
**Branch:** `dpaa1`
**CI build:** #28733398715
**ISO:** `vyos-2026.07.05-0730-rolling-LS1046A-arm64.iso`

> **⚠ STATUS CORRECTION (2026-08-05) — the "shipping architecture" [SPEC] below is SUPERSEDED.**
> (1) FE-VM ehash is UN-RETIRED (F-163, commit `f212c701`): the deployed vendor `cdx.ko`'s
> production classification **is** external-hash; this branch's key builder was fixed to the
> vendor's 14-byte PORT_ID-prefixed `union dpa_key` (`EKFC 0x801C0006`). (2) "CC-tree shipping"
> was never implemented in `ask.ko` (CR-007, `dd364494`) and the `cc_test` harness is
> architecturally broken (F-159–F-162: five vendor-verified register fixes, RX-silent within
> 17–30 frames vs `.106` vendor stack's 400+; `cc_test` to be retired). (3) "never dispatched a
> single HIT" is overstated — F-165 (commit `e4f23948`) showed every prior arm test pointed the
> port at an empty scaffold; the corrected chain has never been genuinely exercised (T-M3-R
> retest pending). **No dispatch path has a confirmed hardware HIT on this branch.** Sections
> marked "RETIRED / DIAGNOSTIC ONLY" below should be read as "built and byte-verified, no
> confirmed HIT, un-retired and under re-validation."

**[SPEC — 2026-08-01, SUPERSEDED above] ~~Shipping HW-offload architecture (2026-08-01): CC-tree classification (top-N hot flows) + kernel SW flowtable (tail) + hardware manip-chain forwarding.~~** ~~The FE-VM ehash HIT path (Fork-B) is RETIRED — it never dispatched a single HIT across the project's history (F-156/F-157/F-158 proved the scaffold byte-perfect and the CC engine still not dispatching to it), and even if fixed is architecturally capped at ~1.5 Gbps by per-frame DDR latency.~~ The FE-VM ehash infrastructure (F-156/F-157/F-158 fixups, fe_scaffold oracle, dedicated TX FQ 0x2b9) is retained as diagnostic/experimental tooling. CC-tree scales: 32 software caps vs 255 HW keys/node, ~8 nodes in 64 KiB → ~2,000+ flows (arithmetic; no confirmed HIT). M3/M5 "HIT PASSED" were false positives; M2 7.37 Gbps real pass-through; only real HIT RCCB→FE_ENTER direct 2026-07-04.

---

## Delivered — In-Tree Kernel (91 board patches, ~12K LOC)

### FMan PCD Subsystem (classification engine) — SHIPPING PATH
| Patch | Component | Purpose | Silicon Status |
|-------|-----------|---------|----------------|
| 0092 | `fman_pcd.c` | PCD core, MURAM genpool, debugfs | ON SILICON |
| 0097 | `fman_pcd_kg.c`, `fman_keygen.c` | KeyGen: scheme create/bind, port attach | ON SILICON |
| 0098 | `fman_pcd_cc.c` | CC tree: static install, per-key AD | **ON SILICON — SHIPPING** |
| 0099 | `fman_pcd_manip.c` | HM: node install/destroy, L3 forward ops | **ON SILICON — SHIPPING** |
| 0100 | `fman_pcd_plcr.c` | Policer: srTCM/trTCM profile install | ON SILICON |
| 0106 | `fman_keygen.c` | CC→KG graft wiring (KGSE_CCBS) | ON SILICON |
| 0108 | `fman_pcd_cc.c` | CC per-key FQ enqueue AD (NADEN) | **ON SILICON — SHIPPING** |
| 0113 | `fman_pcd_dcsr.c` | DCSR error taps | COMPILED |
| 0117 | `fman.c` | FMan load ctrl microcode | ON SILICON |
| 0118 | `fman_keygen.c` | Revert CCBS dispatch, real AC_CC prep | ON SILICON |
| 0119 | `fman_pcd_manip.c` | HM L3 forward ops (TTL dec, cksum) | **COMPILED — SHIPPING** |
| 0120 | `fman_pcd_manip.c` | HM nexthop dedup | **COMPILED — SHIPPING** |

### FE-VM Subsystem (Fork B ehash path) — UN-RETIRED 2026-08-05, under re-validation
**[NOTE — updated 2026-08-05]** The FE-VM ehash HIT path (Fork-B) was RETIRED 2026-08-01 and UN-RETIRED 2026-08-05 (F-163 — the deployed vendor `cdx.ko` classifies via external-hash; key format corrected to the 14-byte PORT_ID-prefixed `union dpa_key`). It has no confirmed HIT on this branch, but the F-165 finding (engage-path scaffold overwrite) means the corrected chain has never been genuinely exercised — retest = T-M3-R. The infrastructure below is built and byte-verified. CC-tree (above) is the *intended* classifier but is not wired in `ask.ko` and its `cc_test` harness is broken (F-159–F-162).

| Patch | Component | Purpose | Silicon Status |
|-------|-----------|---------|----------------|
| 0122 | `fman_pcd.c` | FE pool alloc/free (100×28B, refcounted) | ON SILICON (Phase 1) — diagnostic |
| 0123 | `fman_pcd.c` | FE port support, registration | ON SILICON — diagnostic |
| 0124 | `fman_pcd.c` | FE singletons (MUX/Transition/Exit) | ON SILICON (Phase 1) — diagnostic |
| 0125 | `fman_pcd.c` | Ehash table create/destroy + DDR bucket array | ON SILICON (Phase 1) — diagnostic |
| 0126 | `fman_pcd.c` | MURAM genpool (64 KiB reserve) | ON SILICON |
| 0127 | `fman_pcd.c` | ENQ FE root (per-flow FQID) + FE_ENTER root AD | ON SILICON (Phase 1) — diagnostic |
| 0128 | `fman_pcd.c` | Flow insert (CRC64 hash + DDR record + next-FE ptr) | COMPILED — diagnostic |
| 0130 | `fman_pcd.c` | FE ehash DMA coherent (DDR bucket array) | COMPILED — diagnostic |
| 0131 | `fman_pcd.c` | FE hash object (t_ExtHashFe 7-word encoder) | ON SILICON (Phase 1) — diagnostic |
| 0132 | `fman_pcd.c` | FE arm debugfs (engage/disengage) | ON SILICON (Phase 1) — diagnostic |
| **0133** | `fman_keygen.c` | **Real AC_CC arm** (KGSE_MODE 0x80000006, corrects CCBS placebo) | **ON SILICON (Phase 1) — diagnostic** |
| **0135** | `fman_pcd.c` | **FE context builder** (FmPcdCcBuildContextByFE port from lf-5.4 L8954) | **COMPILED — diagnostic** |

### TX Confirm Bypass
| Patch | Component | Purpose | Status |
|-------|-----------|---------|--------|
| **0136** | `fman_port.c` | `fman_port_set_silicon_hit_release_mode()` + `_all()` | **COMPILED, ask.ko wired** |

### MANIP Chain API (L3 forwarding) — SHIPPING PATH
| Patch | Component | Purpose | Status |
|-------|-----------|---------|--------|
| **0137 v2** | `fman_pcd_manip.c` | MANIP create/chain API, `HMAN_OC_IP_MANIP=0x34` fix, HMCD_LAST clearing | **COMPILED, SDK-verified — SHIPPING** |

### CAAM (Hardware Crypto)
| Patch | Component | Purpose | Status |
|-------|-----------|---------|--------|
| 0134 | `drivers/crypto/caam/qi.c` | CAAM QI descriptor sharing | COMPILED (dormant) |

### AF_XDP / VPP Infrastructure
| Patch | Component | Purpose | Status |
|-------|-----------|---------|--------|
| 0068-0069a | `dpaa_flavor.c/h`, `dpaa_eth.c` | Flavor ops registration + RCU dispatch | COMPILED |
| 0070-0077 | `dpaa_eth.c`, `af_xdp_pool/` | XDP pool setup, wakeup, detach | COMPILED |
| 0079-0083 | `dpaa_ethtool.c` | 22 xsk_* ethtool counters | COMPILED |
| 0084 | `af_xdp_pool/` | NAPI-hooked BMan refill | COMPILED (crash fixed by 0139) |
| 0085 | `af_xdp_pool/`, `dpaa_eth.c` | TX ZC + inflight backpressure | COMPILED |
| 0088 | `af_xdp_pool/` | Use RX DMA dev for XSK pool DMA map | COMPILED |
| 0093-0096 | `dpaa_eth.c`, `af_xdp_pool/` | True-ZC RX eligibility, arm, guard, recover | COMPILED |
| 0102 v2 | `fman_port.c` | `fman_port_set_rx_bpool()` — persistent-table fix | COMPILED |
| 0103a-g | `dpaa_eth.c`, `af_xdp_pool/` | ZC reprogram-redirect, sw-ring, rxq NULL fix | COMPILED |
| 0110 | `af_xdp_pool/` | NAPI-only flush | COMPILED |
| 0114 | `af_xdp_pool/`, `dpaa_eth.c` | ZC eligible realign | COMPILED |
| **0139** | `af_xdp_pool/` | **BMan IVCI crash fix** (bm_buffer_set_bpid on each slot) | **COMPILED** |

### DPAA1 Networking
| Patch | Component | Purpose | Status |
|-------|-----------|---------|--------|
| 0086-0086b | `dpaa_fman_caps.c/h` | FMan caps detection + CC stub | ON SILICON |
| 0090-0091a | `dpaa_fman_caps.c/h` | HM/Policer stubs + productive structs | ON SILICON |
| 0101 | `dpaa_eth.c` | HW VLAN strip (ndo_set_features) | ON SILICON |
| 0104 | `dpaa_eth.c` | Ingress policer tc matchall bridge | ON SILICON |
| 0104a | `dpaa_eth.c` | Advertise hw-tc-offload | ON SILICON |
| 0104b | `dpaa_fman_caps.c/h` | CEETM stub | COMPILED |
| 0105 | `fman_port.c` | `fman_port_set_cc_base()` | ON SILICON |
| 0107 | `fman_pcd.c`, `fman_pcd_cc.c` | CC test debugfs harness | COMPILED |
| 0109 | `dpaa_eth.c` | Ethernet ntuple CC steering bridge | ON SILICON |
| 0111-0112 | `qbman/qman.c`, `dpaa_ceetm.c` | QMan CEETM + HTB (stubs) | COMPILED |
| 0115-0116 | `fman_keygen.c`, `fman_pcd_cc.c` | Convergent bringup + FM_CTL params page | ON SILICON |
| 0121 | `dpaa_eth.c` | Export CC target resolvers | COMPILED |
| **0145** | `dpaa_eth.c` | **Flow-offload backend slot** (RCU-protected for ask.ko) | **COMPILED** |

### Diagnostic / Experimental Fixups (Layer 2) — FE-VM ehash (un-retired 2026-08-05, under re-validation)
**[NOTE] F-156/F-157/F-158 fixups + fe_scaffold oracle + dedicated TX FQ 0x2b9 are diagnostic tooling that proved the CC-match stage is not a production path. Retained for provenance; do not re-enable for shipping.**

| Fixup | Purpose | Status |
|-------|---------|--------|
| F-156 | CC match-key row format fix (key+mask 32B stride) | DEPLOYED 2026-07-31 — diagnostic |
| F-157 | Dedicated TX FQ 0x2b9 HIT/MISS discriminator | DEPLOYED 2026-08-01 — diagnostic |
| F-158 | fe_scaffold debugfs ground-truth oracle | DEPLOYED 2026-08-01 — diagnostic |

### Board Scripts (userspace)
| Script | Purpose | Status |
|--------|---------|--------|
| `fan-pid` | Multi-zone PI fan controller (EMC2305 direct i2c) | DEPLOYED |
| `fan-check` | Thermal + fan status reporter | DEPLOYED |
| `caam-check` | CAAM crypto engine status | DEPLOYED |
| `firmware-check` | Boot firmware / U-Boot env / FMan ucode | DEPLOYED |
| `pcd-snapshot` | FMan PCD state capture + diff (S0↔S1 gate) | **DEPLOYED, Phase 1 proven** |
| `xsk-zc-check` | AF_XDP ZC gate counter reader | DEPLOYED |
| `ask-check` | ASK2 plain-IPv4 preview readiness probe; deferred features report software fallback | DEPLOYED |
| `support-bundle` | Paste-ready ASK2 issue report (identity, version, offload config/live state, ask-check, firmware-check, dmesg) | DEPLOYED |
| `fman-fq-qdisc` | FMan FQ QMan qdisc helper | DEPLOYED |
| `sfp-check` | SFP module health reporter | DEPLOYED |
| `led.py` | LP5812 RGBW LED control | DEPLOYED |
| `vyos-postinstall` | U-Boot env + vyos.env boot selector | DEPLOYED |

---

## Delivered — OOT ask.ko (~1500 LOC active + tests)

| Component | File | LOC | Purpose | Status |
|-----------|------|-----|---------|--------|
| Module core | `ask_main.c` | ~200 | Module init, `dpaa_flavor_ops` registration | COMPILED |
| HW engage | `ask_hw.c` | ~500 | Engage/disengage: FE pool → chain → `fe_arm` → **TX bypass** | COMPILED |
| Flow table | `ask_flow.c` | ~300 | Hash table, per-flow insert/lookup/delete | COMPILED |
| Flow offload | `ask_flow_offload.c` | ~1025 | nf_flow_table BIND/REPLACE/DESTROY handler | **COMPILED — SHIPPING (CC-tree + SW flowtable)** |
| Control plane | `ask_genl.c` (+attr) | ~400 | Generic netlink (`ask` family) | COMPILED |
| DebugFS | `ask_debugfs.c` | ~200 | Diagnostic debugfs nodes | COMPILED |
| Neighbor | `ask_neigh.c` | ~150 | Next-hop neighbor resolution | COMPILED |
| Operations | `ask_op.c` | ~150 | Per-flow action encoder | COMPILED |
| Statistics | `ask_stats.c` | ~100 | HW offload stat counters | COMPILED |
| **L2 bridge** | **`ask_bridge.c`** | **417 B** | **L2 switchdev — STUB** | **STUB** |
| **CAAM** | **`ask_caam.c`** | **100** | **CAAM integration — STUB** | **STUB** |
| **IPsec** | **`ask_xfrm.c`** | **1 KB** | **ESP xfrmdev_ops — STUB** | **STUB** |
| Headers | `ask_internal.h`, `ask_fman_caps.h`, `uapi/ask.h` | ~500 | Internal + public UAPI types | COMPILED |
| Tests | `ask_test_*.c` (5 files) | ~800 | KUnit: main, flow, flow_offload, genl, hw_pcd | COMPILED |
| Tracing | `ask_trace.h` | ~50 | Trace event definitions | COMPILED |

---

## Planned — Not Yet Implemented

### Phase 2: M2 gate → working flow offload
| Component | Description | Dependency |
|-----------|-------------|------------|
| ask.ko full FE datapath | `fe_flow add` with real 5-tuple key, TX bypass engage, ct rule | Phase 1 (DONE) |
| MANIP chain → CC AD | Wire `fman_pcd_manip_chain_create()` HMCT handle into CC AD word3 (NADEN=0x20000000) | 0137 (DONE) |
| Flow-offload REPLACE diag | Kernel patch adding nf_flow_table offload diagnostic logging | C6 analysis |
| iperf3 M2 gate | Gen→DUT→Sink, 8-stream TCP, ≥2 Gbps AND ≤5% CPU | ask.ko datapath |

### Phase 3: L2 switchdev
| Component | Description | Dependency |
|-----------|-------------|------------|
| `ask_bridge.ko` full body | L2 bridge flow detection + offload (replaces `auto_bridge.ko`) | Phase 2 |
| Bridge flow types | VLAN-aware, MAC-learning, flood control | Phase 2 |

### Phase 4: HW IPsec
| Component | Description | Dependency |
|-----------|-------------|------------|
| ESP xfrmdev_ops full body | IPsec ESP offload via CAAM SEC 5.4 (CBC+CTR, GCM refused by ucode cap) | CAAM QI share (0134) |
| SA add/delete | xfrm state → CAAM job ring → SEC FQ programming | Phase 2 |
| anti-replay | Sequence number window in SEC descriptor | Phase 2 |

### Phase 5: Operator CLI
| Component | Description | Dependency |
|-----------|-------------|------------|
| `set interfaces ethernet eth<n> offload ask` | VyOS native per-interface CLI for ASK engage/disengage | Phase 2 |
| VPP mutual exclusion | Per-interface ASK↔VPP exclusion (a port can't be both; other ports free) | Phase 2 |
| Telemetry | ethtool -S ASK counters, ask-check integration | Phase 2 |

### Phase 6: Soak & Performance
| Component | Description | Dependency |
|-----------|-------------|------------|
| Reversibility 100-cycle | pcd-snapshot engage/disengage × 100, verify byte-clean each cycle | Phase 2 |
| Policer throughput cap | Literal wire-level policer measurement with iperf3 (not just pings) | Phase 2 |
| MURAM leak soaks | gen_pool high-water baseline across repeated engage/disengage | Phase 2 |
| Thermal VPP+ASK | Both dataplanes exercised simultaneously (different ports) | Phase 2 |

---

## Summary

| Category | Delivered | Planned |
|----------|-----------|---------|
| In-tree kernel patches | 91 (85 active, 6 dormant primitives) | — |
| New kernel .c files | 12 (`fman_pcd*`, `fman_keygen*`, `af_xdp_pool*`, `dpaa_flavor*`) | — |
| Public kernel headers | 3 (`fman_pcd.h`, `dpaa_flow_offload.h`, `caam_qi_share.h`) | — |
| OOT ask.ko source | 14 .c/.h files (~1500 LOC code, ~800 LOC tests) | 3 stubs to complete |
| Board userspace scripts | 14 | — |
| **M2 gate condition (1)** | **SATISFIED on silicon** | — |
| **M2 gate condition (2)** | — | **fe_flow add HIT test** |
| Phase 3-6 | — | 4 phases, ~12 components |

**[SPEC — 2026-08-01, SUPERSEDED 2026-08-05 (see top-of-doc correction)]** ~~Architecture status (2026-08-01):~~ Shipping HW-offload was declared = CC-tree classification (top-N) + kernel SW flowtable (tail) + manip-chain forwarding; FE-VM ehash HIT path (Fork-B) was declared RETIRED — never dispatched a HIT, ~1.5 Gbps DDR ceiling. **2026-08-05 reality:** ehash un-retired (F-163, vendor production path, 14-byte PORT_ID key); CC-tree never wired (CR-007) with a broken harness (F-159–F-162); no confirmed HIT on either path; F-165 retest (T-M3-R) is next. M3/M5 "HIT PASSED" were false positives; M2 7.37 Gbps real pass-through; only real HIT RCCB→FE_ENTER direct 2026-07-04.