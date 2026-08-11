# ASK2 Architecture — Canonical Index (v1.12)

**[CORRECTION, 2026-08-11]** The "FE-VM ehash is a DEAD END / not the shipping datapath; CC-tree + SW flowtable is shipping" framing (v1.11) is now **superseded for the per-packet HIT path** by the 2026-08-11 qdrant discoveries, which together show the vendor's genuine production classification path IS external-hash:
- Vendor `.106` stack (live): `cdx.ko` production path = `insert_entry_in_classif_table()` → `ExternalHashTableAddKey()` (external ehash), full arming/offload process mapped (see `arch/fman-fe-ehash.md`, `qdrant: ask-arm-offload-every-step`).
- 999-patch HIT/PASS encoding decoded: `en_exthash_node` CC-leaf AD + `en_ehash_entry` DDR record with **16 B `t_ExtHashResult`** (per-flow MUX context + monitor stats) after the key, stats at +256 (see `arch/fman-fe-ehash.md`, `qdrant: hit-pass-flow-encoding-decoded`).
- CC-tree classification **never produced a confirmed HIT** (`cc_test` retired; M5's 10.259 Gbps is a SW-flowtable/CC-tree *throughput* result, mechanism unresolved — see `plans/ASK2-MASTER-PLAN.md` M5).

**Consequences for this index (v1.12):**
1. **The per-packet HIT path is the external ehash (FE-VM), not CC-tree.** The "DEAD END" banner below the line is retained only as historical framing; the authoritative course-corrected architecture is `plans/ASK2-PRODUCTION-ARCHITECTURE.md`.
2. **M3 OPEN = the three silicon deltas** (RCCB AD species at `FMBM_RCCB`; record-side `t_ExtHashResult`; params `OFFLOAD_SUPPORT_EN` + FE buffer pool `+0x54/+0x58`) — see `plans/ASK2-PRODUCTION-ARCHITECTURE.md` §3.1/§4 Phase 2.
3. **Binding production requirement: the control surface is genl/YNL only. debugfs is `CONFIG_ASK_DEBUG_FS` / `CONFIG_FMAN_PCD_DEBUG_FS`-gated and compiled out of production images.** Consumers must never depend on `/sys/kernel/debug/ask` or `fman_pcd/fe_*`. The genl `ask` family (UAPI `kernel/ask/uapi/ask.yaml`) is the sole production engage/disengage/flow/stats path.

**[CORRECTION, 2026-08-05]** This index's "FE-VM ehash is a DEAD END / not vendor architecture" framing (v1.11 line below) is partly refuted: the genuine deployed vendor `cdx.ko` driver's production classification path IS external-hash (`insert_entry_in_classif_table()` → `ExternalHashTableAddKey()`, `cdx_ehash.c`, nxp-sdk branch) — see `arch/fman-fe-ehash.md`'s un-retirement banner and `specs/fman-keygen-flow-key-spec.md` §1.2a for the full finding, and F-163 (`kernel/ask/oot-modules/ask/ask_flow_offload.c`) for the resulting key-format fix. What is NOT refuted: the ~1.5 Gbps DDR-per-frame throughput-ceiling claim (unmeasured against real vendor traffic) and the fact that this branch's own FE-VM has still never produced a confirmed hardware HIT (a 2026-08-05 live board test, byte-correct end-to-end including the corrected key, still MISSed — see `arch/fman-microcode-210-programming-reference.md` §10.5a). Whether CC-tree remains the right *shipping* mechanism for other reasons (MURAM footprint, avoiding per-frame DDR) is a separate, still-open question this correction does not settle either way.

**Status:** Architecture index — this document maps the ASK2 architecture landscape. It does NOT contain the architecture itself. The full ASK2 rewrite spec (v1.1–v1.7, ~6 kLOC) was retired 2026-07-14 when the Fork-B FE-VM ehash path invalidated the Fork-A/OH-port-era ceiling numbers and MURAM budget assumptions. **v1.11 (2026-08-01):** The SHIPPING HW-offload architecture is CC-tree + kernel SW flowtable + manip-chain forwarding (M5 10.259 Gbps @ 0.16% CPU). The FE-VM ehash HIT path (Fork-B) is a DEAD END — per-frame DDR hash (~1.5 Gbps ceiling), never dispatched by the CC engine, documented as experimental only. CC-tree scales to ~2000+ flows (255 keys/node, ~8 nodes in 64 KiB MURAM). CC comparator reads KG-emitted bytes (patch 0108 cc_pack_key rewrite); EKFC order MSB-first (SIP,DIP,PROTO,SPORT,DPORT). S1 (ASK) redefined as CC-tree + SW flowtable + manip chain. Per-interface CLI contract (`set interfaces ethernet eth<n> offload ask`, per-interface ASK↔VPP mutex, `system offload classify` CLI deprecated — mechanism kept as silent default).

## AI READING INSTRUCTION

This document is an index. For silicon facts, go to `arch/`. For design intent, go to `specs/`. For sequencing, go to `plans/`. For day-to-day operational rules, go to `AGENTS.md`.

---

## 1. Architecture — where the facts live now

| Old spec § | Topic | Current authoritative source |
|---|---|---|
| §2 (Hardware context) | FMan v3, 210.10.1 microcode, DPAA1 | `arch/fman.md`, `arch/fman-microcode-210-programming-reference.md` |
| §2.4 (Interrupts) | FMan event IRQ wiring | `arch/soc-integration.md` §4 |
| §2.4(6) (M0 verdict) | FE-VM ehash path — experimental, NOT the shipping datapath. Shipping HW-offload = CC-tree + SW flowtable + manip chain. | `arch/fman-fe-ehash.md` (experimental reference), `arch/fman-pcd.md` (CC-tree pipeline) |
| §3 (API surfaces) | pcd_ops, qmgmt_ops, flavor ops | `specs/dpaa1-afxdp-modernization-spec.md` §5 |
| §3.5a (API consumption table) | Which consumer uses which API | `specs/dpaa1-afxdp-modernization-spec.md` §5 |
| §5 (ask.ko module) | ~2800 LOC in-tree OOT module | `kernel/ask/oot-modules/ask/` |
| §6 (userspace daemons) | askd, ask-cli — deleted in v1.3 | `plans/archive/ASK2-IMPLEMENTATION.md` |
| §11.1 (Flow ceilings) | CC-tree: 255 keys/node, ~8 nodes in 64 KiB MURAM → ~2000+ flows. FE-VM ehash: DDR-backed, unbounded but ~1.5 Gbps ceiling (experimental). | `arch/muram.md`, `arch/fman-pcd.md` |
| §12 (Wire format) | CDX ↔ kernel serialization — deleted v1.3 | `plans/archive/ASK2-IMPLEMENTATION.md` |
| §13 (fman_pcd subsystem) | KeyGen, CC, HM, Policer, replicator | `arch/fman-pcd.md` (pipeline narrative), `arch/fman-microcode-210-programming-reference.md` (register reference) |
| §13.3 (MURAM exhaustion) | gen_pool reservation, chain_create -ENOMEM | `arch/muram.md` |
| §15 (Implementation status) | Per-module STARTED/NOT_STARTED | `plans/OFFLOAD-CAPABILITIES.md`, `plans/MODULE-INVENTORY.md` |
| §16 (Risk register) | MURAM sizing, HM chain caps | `arch/muram.md`, `plans/DUAL-DATAPLANE.md` |

## 2. Design intent documents (active)

| Document | Topic |
|---|---|
| `specs/fman-keygen-flow-key-spec.md` | EKFC extraction, CRC-64 hash, CC-tree flow-table architecture. Confirmed 5-tuple extraction order (MSB-first: SIP,DIP,PROTO,SPORT,DPORT). CC comparator reads KG-emitted bytes (patch 0108). FE-VM ehash documented as experimental only. |
| `specs/dpaa1-afxdp-modernization-spec.md` | Shared kernel driver substrate (PCD, QMgmt, AF_XDP ZC) serving all consumers. |
| `specs/vpp-dpaa1-ls1046a-spec.md` | VPP AF_XDP integration on DPAA1. |
| `plans/DUAL-DATAPLANE.md` | S0↔S1↔S2 dataplane mode state machine, reversibility contract, CLI semantics (per-interface `set interfaces ethernet eth<n> offload ask`). S1 (ASK) = CC-tree + SW flowtable + manip chain. |
| `plans/ASK2-MASTER-PLAN.md` | **Single authoritative execution plan** — milestones M2–M8, gates, live TODO list. |
| `plans/ASK2-PRODUCTION-ARCHITECTURE.md` | **Course-correction plan (2026-08-11)** — production architecture (mainline control plane, NXP DPAA1 data plane, **no debugfs in production**) + 6-phase plan (Phases 1–6). The three silicon deltas for M3 OPEN: RCCB AD species, `t_ExtHashResult` record, `OFFLOAD_SUPPORT_EN`/FE-pool. Supersedes the v1.11 "FE-VM ehash dead end" framing for the HIT path. |

## 3. Hardware silicon reference (arch/)

| Document | Topic |
|---|---|
| `arch/fman-microcode-210-programming-reference.md` | **Authoritative** 210.10.1 register/FE/resource reference. Read this first for any register question. |
| `arch/fman-fe-ehash.md` | FE-VM init contract, M3-3b disposition fork, reversibility contract. **Experimental only** — FE-VM ehash never dispatched by CC engine; shipping datapath is CC-tree + SW flowtable. |
| `arch/fman-pcd.md` | PCD pipeline FLAGSHIP — narrative overview of Parser→KeyGen→CC→Policer→Manip. CC-tree = shipping HW-offload path. |
| `arch/fman.md` | FMan v3 plumbing (BMI, QMI, FPM, DMA, ports, mEMAC). |
| `arch/muram.md` | MURAM budget, CC-tree node allocation model (~8 nodes in 64 KiB), FE-VM ehash DDR-backed (experimental). |
| `arch/dpaa1-architecture.md` | DPAA1 programming model primer. |
| `arch/README.md` | Complete arch/ document index. |

## 4. Execution plans (plans/)

| Document | Topic |
|---|---|
| `plans/ASK2-MASTER-PLAN.md` | **THE authoritative ASK2 execution plan (v1.0.0, 2026-07-19).** Ground state, gaps A–E, binding decisions, milestone chain M2–M8 with gates, live TODO list, open defects, harness/gate mechanics, superseded-doc register. Read this and nothing else for sequencing. |
| `plans/ASK2-PRODUCTION-ARCHITECTURE.md` | **Course-correction (2026-08-11).** Production architecture + 6-phase plan. See the top correction banner for the M3 reframe and the genl-only/no-debugfs binding requirement. |
| `plans/DUAL-DATAPLANE.md` | Dataplane mode state machine (S0/S1/S2) + CLI contract. v1.3 (2026-07-19). S1 = CC-tree + SW flowtable + manip chain. |
| `plans/TF-2026-07-18-001-function-inventory.md` | Stub/type-drift inventory (F-01–F-23) feeding the master plan §5 re-land series. |
| `plans/TRAFFIC-HARNESS.md` | Board SFP+ acceptance-gate traffic generator (CT201/CT202). |
| `plans/OFFLOAD-CAPABILITIES.md` | Living inventory of silicon-verified offload capabilities. |
| `plans/MODULE-INVENTORY.md` | Delivered kernel patch inventory (107 board patches as of 2026-07-18). |

## 5. Architecture decision records (archive)

| Document | Topic |
|---|---|
| `plans/archive/ASK2-PATH-A-ARCHITECTURE-DECISION-RECORD.md` | **Combined Path A decision record** — Part 1: ASK-vs-ASK2 comparative analysis (Path A origin), Part 2: Architecture review (five simplifications), Part 3: Course-correction execution plan (five phases, 28-patch audit). All three parts superseded by Fork B — FE-VM ehash (July 2026). |
| `plans/archive/ASK2-IMPLEMENTATION.md` | Historical implementation plan — superseded by Fork-B |
| `plans/archive/ASK2-JOURNEY-REVIEW-2026-07-18.md` | Journey review (M2 PASS, M3 infrastructure, NXP SDK oracle comparison) — folded into `plans/ASK2-MASTER-PLAN.md` §1/§2/§3/§4/§6 (archived 2026-07-19) |
| `plans/archive/ASK2-DEVELOPMENT-PLAN.md` | Phase 0–6 execution plan — folded into master plan §1/§3/§4/§5/§7 (archived 2026-07-19) |
| `plans/archive/COMPLETION-PLAN.md` | Cross-track DPAA1/VPP/ASK2 roadmap — folded into master plan §4/§7 (archived 2026-07-19) |
| `plans/archive/ASK2-PHASE2-AUTOMATION-PLAN.md` | Flow-offload automation T1–T6 — folded into master plan §5 M5 + §6 (archived 2026-07-19) |
| `plans/archive/ASK2-PERFORMANCE-MODERNIZATION.md` | cdx.ko parity + opcode gaps — folded into master plan §4/§5/§7 (archived 2026-07-19) |
| `plans/archive/ASK2-F3-F6-UNBLOCK-PROPOSAL.md` | F3/F6 bisect findings — folded into master plan §6 (archived 2026-07-19) |
| `plans/archive/ASK-PLANS.md` | 2026-06-09 doc hub — role superseded by this index + master plan §8 (archived 2026-07-19) |

---

*Maintainers: when you add a new architecture fact, file it under the appropriate `arch/`, `specs/`, or `plans/` document — do NOT expand this index. This index exists solely to redirect readers who follow old § references.*