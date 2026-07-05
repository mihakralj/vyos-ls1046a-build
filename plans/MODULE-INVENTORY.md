# ASK2 Module Inventory — Delivered & Planned

**Date:** 2026-07-05
**Branch:** `dpaa1`
**CI build:** #28733398715
**ISO:** `vyos-2026.07.05-0730-rolling-LS1046A-arm64.iso`

---

## Delivered — In-Tree Kernel (91 board patches, ~12K LOC)

### FMan PCD Subsystem (classification engine)
| Patch | Component | Purpose | Silicon Status |
|-------|-----------|---------|----------------|
| 0092 | `fman_pcd.c` | PCD core, MURAM genpool, debugfs | ON SILICON |
| 0097 | `fman_pcd_kg.c`, `fman_keygen.c` | KeyGen: scheme create/bind, port attach | ON SILICON |
| 0098 | `fman_pcd_cc.c` | CC tree: static install, per-key AD | ON SILICON |
| 0099 | `fman_pcd_manip.c` | HM: node install/destroy, L3 forward ops | ON SILICON |
| 0100 | `fman_pcd_plcr.c` | Policer: srTCM/trTCM profile install | ON SILICON |
| 0106 | `fman_keygen.c` | CC→KG graft wiring (KGSE_CCBS) | ON SILICON |
| 0108 | `fman_pcd_cc.c` | CC per-key FQ enqueue AD (NADEN) | ON SILICON |
| 0113 | `fman_pcd_dcsr.c` | DCSR error taps | COMPILED |
| 0117 | `fman.c` | FMan load ctrl microcode | ON SILICON |
| 0118 | `fman_keygen.c` | Revert CCBS dispatch, real AC_CC prep | ON SILICON |
| 0119 | `fman_pcd_manip.c` | HM L3 forward ops (TTL dec, cksum) | COMPILED |
| 0120 | `fman_pcd_manip.c` | HM nexthop dedup | COMPILED |

### FE-VM Subsystem (Fork B ehash path)
| Patch | Component | Purpose | Silicon Status |
|-------|-----------|---------|----------------|
| 0122 | `fman_pcd.c` | FE pool alloc/free (100×28B, refcounted) | ON SILICON (Phase 1) |
| 0123 | `fman_pcd.c` | FE port support, registration | ON SILICON |
| 0124 | `fman_pcd.c` | FE singletons (MUX/Transition/Exit) | ON SILICON (Phase 1) |
| 0125 | `fman_pcd.c` | Ehash table create/destroy + DDR bucket array | ON SILICON (Phase 1) |
| 0126 | `fman_pcd.c` | MURAM genpool (64 KiB reserve) | ON SILICON |
| 0127 | `fman_pcd.c` | ENQ FE root (per-flow FQID) + FE_ENTER root AD | ON SILICON (Phase 1) |
| 0128 | `fman_pcd.c` | Flow insert (CRC64 hash + DDR record + next-FE ptr) | COMPILED |
| 0130 | `fman_pcd.c` | FE ehash DMA coherent (DDR bucket array) | COMPILED |
| 0131 | `fman_pcd.c` | FE hash object (t_ExtHashFe 7-word encoder) | ON SILICON (Phase 1) |
| 0132 | `fman_pcd.c` | FE arm debugfs (engage/disengage) | ON SILICON (Phase 1) |
| **0133** | `fman_keygen.c` | **Real AC_CC arm** (KGSE_MODE 0x80000006, corrects CCBS placebo) | **ON SILICON (Phase 1)** |
| **0135** | `fman_pcd.c` | **FE context builder** (FmPcdCcBuildContextByFE port from lf-5.4 L8954) | **COMPILED** |

### TX Confirm Bypass
| Patch | Component | Purpose | Status |
|-------|-----------|---------|--------|
| **0136** | `fman_port.c` | `fman_port_set_silicon_hit_release_mode()` + `_all()` | **COMPILED, ask.ko wired** |

### MANIP Chain API (L3 forwarding)
| Patch | Component | Purpose | Status |
|-------|-----------|---------|--------|
| **0137 v2** | `fman_pcd_manip.c` | MANIP create/chain API, `HMAN_OC_IP_MANIP=0x34` fix, HMCD_LAST clearing | **COMPILED, SDK-verified** |

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

### Board Scripts (userspace)
| Script | Purpose | Status |
|--------|---------|--------|
| `fan-pid` | Multi-zone PI fan controller (EMC2305 direct i2c) | DEPLOYED |
| `fan-check` | Thermal + fan status reporter | DEPLOYED |
| `caam-check` | CAAM crypto engine status | DEPLOYED |
| `firmware-check` | Boot firmware / U-Boot env / FMan ucode | DEPLOYED |
| `pcd-snapshot` | FMan PCD state capture + diff (S0↔S1 gate) | **DEPLOYED, Phase 1 proven** |
| `xsk-zc-check` | AF_XDP ZC gate counter reader | DEPLOYED |
| `ask-check` | ASK2 readiness probe | DEPLOYED |
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
| Flow offload | `ask_flow_offload.c` | ~1025 | nf_flow_table BIND/REPLACE/DESTROY handler | COMPILED (REPLACE untested) |
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
| `set system offload ask` | VyOS native CLI for ASK engage/disengage | Phase 2 |
| VPP mutual exclusion | Global ASK↔VPP mutually exclusive (runtime select, single image) | Phase 2 |
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
