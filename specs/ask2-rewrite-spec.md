# ASK2 Architecture — Canonical Index (v1.8)

**Status:** Architecture index — this document maps the ASK2 architecture landscape. It does NOT contain the architecture itself. The full ASK2 rewrite spec (v1.1–v1.7, ~6 kLOC) was retired 2026-07-14 when the Fork-B FE-VM ehash path invalidated the Fork-A/OH-port-era ceiling numbers and MURAM budget assumptions.

## AI READING INSTRUCTION

This document is an index. For silicon facts, go to `arch/`. For design intent, go to `specs/`. For sequencing, go to `plans/`. For day-to-day operational rules, go to `AGENTS.md`.

---

## 1. Architecture — where the facts live now

| Old spec § | Topic | Current authoritative source |
|---|---|---|
| §2 (Hardware context) | FMan v3, 210.10.1 microcode, DPAA1 | `arch/fman.md`, `arch/fman-microcode-210-programming-reference.md` |
| §2.4 (Interrupts) | FMan event IRQ wiring | `arch/soc-integration.md` §4 |
| §2.4(6) (M0 verdict) | FE-VM ehash path flows on 210.10.1 | `arch/fman-fe-ehash.md` (the M0 oracle) |
| §3 (API surfaces) | pcd_ops, qmgmt_ops, flavor ops | `specs/dpaa1-afxdp-modernization-spec.md` §5 |
| §3.5a (API consumption table) | Which consumer uses which API | `specs/dpaa1-afxdp-modernization-spec.md` §5 |
| §5 (ask.ko module) | ~2800 LOC in-tree OOT module | `kernel/flavors/ask/oot-modules/ask/` |
| §6 (userspace daemons) | askd, ask-cli — deleted in v1.3 | `plans/archive/ASK2-IMPLEMENTATION.md` |
| §11.1 (Flow ceilings) | MURAM budget, 750-flow ceiling | `arch/muram.md` (Fork-B: DDR ehash, unbounded flows) |
| §12 (Wire format) | CDX ↔ kernel serialization — deleted v1.3 | `plans/archive/ASK2-IMPLEMENTATION.md` |
| §13 (fman_pcd subsystem) | KeyGen, CC, HM, Policer, replicator | `arch/fman-pcd.md` (pipeline narrative), `arch/fman-microcode-210-programming-reference.md` (register reference) |
| §13.3 (MURAM exhaustion) | gen_pool reservation, chain_create -ENOMEM | `arch/muram.md` |
| §15 (Implementation status) | Per-module STARTED/NOT_STARTED | `plans/OFFLOAD-CAPABILITIES.md`, `plans/MODULE-INVENTORY.md` |
| §16 (Risk register) | MURAM sizing, HM chain caps | `arch/muram.md`, `plans/DUAL-DATAPLANE.md` |

## 2. Design intent documents (active)

| Document | Topic |
|---|---|
| `specs/fman-keygen-flow-key-spec.md` | EKFC extraction, CRC-64 hash, FE-VM ehash flow-table architecture. Confirmed 5-tuple extraction order (MSB-first). |
| `specs/dpaa1-afxdp-modernization-spec.md` | Shared kernel driver substrate (PCD, QMgmt, AF_XDP ZC) serving all consumers. |
| `specs/vpp-dpaa1-ls1046a-spec.md` | VPP AF_XDP integration on DPAA1. |
| `plans/DUAL-DATAPLANE.md` | S0↔S1 dataplane mode state machine, reversibility contract, CLI semantics. |
| `plans/ASK2-DEVELOPMENT-PLAN.md` | Current execution plan (Fork-B FE-VM path). |

## 3. Hardware silicon reference (arch/)

| Document | Topic |
|---|---|
| `arch/fman-microcode-210-programming-reference.md` | **Authoritative** 210.10.1 register/FE/resource reference. Read this first for any register question. |
| `arch/fman-fe-ehash.md` | FE-VM init contract, M3-3b disposition fork, reversibility contract. |
| `arch/fman-pcd.md` | PCD pipeline FLAGSHIP — narrative overview of Parser→KeyGen→CC→Policer→Manip. |
| `arch/fman.md` | FMan v3 plumbing (BMI, QMI, FPM, DMA, ports, mEMAC). |
| `arch/muram.md` | MURAM budget, 750-flow ceiling, allocation model. |
| `arch/dpaa1-architecture.md` | DPAA1 programming model primer. |
| `arch/README.md` | Complete arch/ document index. |

## 4. Execution plans (plans/)

| Document | Topic |
|---|---|
| `plans/DUAL-DATAPLANE.md` | Dataplane mode state machine (S0/S1/S2). |
| `plans/ASK2-DEVELOPMENT-PLAN.md` | Fork-B execution plan. |
| `plans/ASK2-PHASE2-AUTOMATION-PLAN.md` | Flow offload automation (M2 gate passed: 7.37 Gbps). |
| `plans/COMPLETION-PLAN.md` | Consolidated cross-track roadmap. |
| `plans/OFFLOAD-CAPABILITIES.md` | Living inventory of silicon-verified offload capabilities. |
| `plans/MODULE-INVENTORY.md` | Delivered kernel patch inventory (91 board patches). |

## 5. Architecture decision records (archive)

| Document | Topic |
|---|---|
| `plans/archive/ASK2-PATH-A-ARCHITECTURE-DECISION-RECORD.md` | **Combined Path A decision record** — Part 1: ASK-vs-ASK2 comparative analysis (Path A origin), Part 2: Architecture review (five simplifications), Part 3: Course-correction execution plan (five phases, 28-patch audit). All three parts superseded by Fork B — FE-VM ehash (July 2026). |
| `plans/archive/ASK2-IMPLEMENTATION.md` | Historical implementation plan — superseded by Fork-B |

---

*Maintainers: when you add a new architecture fact, file it under the appropriate `arch/`, `specs/`, or `plans/` document — do NOT expand this index. This index exists solely to redirect readers who follow old § references.*
