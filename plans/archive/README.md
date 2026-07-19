# plans/archive/

Historical design and forensic documents preserved for bisect/audit purposes. These files describe approaches that have been **abandoned**, **superseded**, or are **dated snapshots** of work that has since landed (or been replaced).

**Do not consult these for current architecture.** They are kept because:

1. They contain accurate facts about silicon behaviour and kernel/driver internals that may be useful when re-investigating a problem.
2. They document why we chose the path we are on (the alternatives we ruled out).
3. They are reachable from older Qdrant memory entries that pre-date the archive move; preserving the filenames keeps those memory entries valid.

**Redirect-note policy (2026-07-19, user decision):** archived docs do **not**
keep a redirect stub at their old `plans/` path — `plans/` holds live documents
only. Instead each archived doc has a sibling `<name>.archive-note.md` here
recording where the content went and which qdrant-cited old path it replaces.
Old `plans/<name>.md` paths cited in pre-2026-07-19 memory entries resolve by
looking up `<name>.md` (or `<name>.md.archive-note.md`) in this directory.

For current state, see (in order of authority):

- `plans/ASK2-MASTER-PLAN.md` — **THE authoritative ASK2 execution plan — start here.** Ground state, gaps, binding architecture decisions, milestone chain M2–M8 with gates, live TODO list, open defects, harness/gate mechanics, and the superseded-document register (§8) that maps every archived ASK2 plan's content forward.
- `specs/dpaa1-afxdp-modernization-spec.md` — **the authoritative cross-flavor source-of-truth** (one DPAA1 driver core + `pcd_ops`/`qmgmt_ops`; the FMan PCD subsystem now lives in the common board stack, built-in for default/vpp/ask).
- `specs/ask2-rewrite-spec.md` — ASK2 architecture index (v1.10).
- `specs/vpp-dpaa1-ls1046a-spec.md` — VPP-flavor (AF_XDP) design spec.
- `plans/DUAL-DATAPLANE.md` — S0↔S1↔S2 state machine + per-interface CLI contract.
- `plans/archive/ASK2-PATH-A-ARCHITECTURE-DECISION-RECORD.md` — **combined Path A decision record** (merged 2026-07-14) in three parts: (1) ASK-vs-ASK2 comparative analysis, (2) architecture review, (3) course-correction execution plan.

## File index

| File | Topic | Why archived |
|---|---|---|---|
| `ASK2-PATH-A-ARCHITECTURE-DECISION-RECORD.md` | **Combined Path A decision record** in three parts: (1) ASK-vs-ASK2 comparative analysis — forensic SDK evidence, sequence diagrams, residual-state model, Path A/B/C options; (2) Architecture review — five simplifications, LOC reduction table, component dispositions; (3) Course-correction execution — five phases, 28-patch KEEP/ARCHIVE/PARTIAL audit, M2 gate findings | Superseded by Fork B — FE-VM ehash (July 2026). Three original docs merged 2026-07-14; redirect notes at `ASK-VS-ASK2-COMPARATIVE-REVIEW.md.archive-note.md`, `ASK2-MODERN-ARCHITECTURE-REVIEW.md.archive-note.md`, `ASK2-COURSE-CORRECTION.md.archive-note.md`. |
| `MULTI-FLAVOR-RELEASE.md` | RETIRED multi-flavor build plan (`default` / `ask` / `vpp`) | Retired 2026-06-14; single-image model supersedes per `plans/DUAL-DATAPLANE.md` |
| `ASK-UPSTREAM-SYNC.md` | Legacy ASK 1.x SDK upstream sync workflow | ASK 1.x branch deleted; ASK2 is a clean re-architecture |
| `INTEGRATION-PLAN.md` | Original integration plan before ASK2 spec existed | Superseded by spec + ASK2-IMPLEMENTATION |
| `MIGRATION-PLAN-6.18.md` | Kernel migration from 6.6 → 6.18 | Migration complete; mainline 6.18 is live |
| `PATCH-STACK-FORENSIC-2026-05-14.md` | Dated forensic snapshot of patch stack | Snapshot only — current state lives in ASK2-PHASE2-PATCH-TRIAGE.md |
| `PR14j-DESIGN.md` | Two-stage OH-port MANIP chain wire-up | OH-port subsystem archived in v1.3 (deferred to v1.1 for IPsec re-inject only) |
| `PR14o-DESIGN.md` | FLOW_CLS_REPLACE delivery diagnostic | REPLACE delivery fixed; current blocker is downstream (chain_create -ENOMEM) |
| `PR14x-DESIGN.md` | `fman_pcd_manip_chain_create()` primitive design | API landed and is in use; design doc itself is historical |
| `PR14z5-DESIGN.md` | Dual-pipeline (per-direction CC tree) experiment | Superseded by Path A's single pre-installed CC tree per protocol |
| `PR14z7-DESIGN.md` | FMBM_RFPNE per-port KG-arming via `fman_port_use_kg_hash()` | Superseded by Path A pre-`register_netdev()` PCD install |
| `PR14z22-DESIGN.md` | DROP-miss diagnostic that proved silicon HIT path works | Diagnostic complete — silicon HIT proven at 6.945 Gbps / 16.63 % baseline |
| `PR14z23-DESIGN.md` | TX-confirm NAPI softirq reduction (no-confirm FQ + bpid fast-path) | Approach superseded by Path A inline `FORWARD_FQ_WITH_MANIP` action atom |
| `ASK2-IMPLEMENTATION.md` | ASK2 per-PR implementation tracker (target spec v1.1) | Superseded by the `specs/dpaa1-afxdp-modernization-spec.md` cross-flavor milestone table; ASK2 spec is now v1.6 |
| `ASK2-PHASE2-PATCH-TRIAGE.md` | KEEP/ARCHIVE/PARTIAL classification of `kernel/flavors/ask/patches/0001-0053` | The ASK 1.x patch tree it classifies was deleted on `ask20`; FMan PCD now lives in the common board stack |
| `ASK2-CMM-TEST-PARITY.md` | Parity matrix mapping the 38 legacy `cmm/unit_tests` shell tests to ASK2 | The `cmm`/`we-are-mono/ASK` corpus was deleted; ASK2 offload has no CLI harness |
| `ASK2-NEXT-STEPS-2026-05-25.md` | Dated forensic roadmap (KG scheme priority-race) toward ASK2 GA | Snapshot only; references spec v1.3 (now v1.6) and the pre-cross-flavor architecture |
| `PR14z19-PATH-A-DESIGN.md` | Path A boot-time PCD-install design (graft-model replacement) | Design landed; joins its already-archived PR14z* siblings. Current state in the dpaa1 spec |
| `REPO-LAYOUT-REFACTOR.md` | Plan to consolidate `ASK/`, `ask-userspace/`, `data/ask-userspace/` userspace trees | All three trees were deleted on `ask20`; the refactor target no longer exists |
| `DPAA1-FULL-DRIVER-PLAN.md` | Task tracker for the shared DPAA1 kernel binary (single-image, Phase A–E gates) | All PHASE A–C gates closed by 2026-06-14 (AF_XDP ZC, CC/HM/Policer, CEETM); Phase D consumer wiring complete; remaining open items (flood-crash, wire gates, VPP benchmark, ASK2 M2) tracked in `plans/ASK2-MASTER-PLAN.md` §4/§5/§6 (archived 2026-07-19) |
| `PATCH-MIGRATION-3WAY.md` | `git apply --3way` + Mergiraf + rerere migration plan | Companion `INTEGRATION-PLAN.md` already archived; migration complete and the process is now documented in `AGENTS.md` |
| `ASK2-JOURNEY-REVIEW-2026-07-18.md` | ASK2 status + forward plan (M2 PASS, M3 infrastructure, NXP SDK oracle, F-072b/c/d, defect register) | Folded into `plans/ASK2-MASTER-PLAN.md` §1/§2/§3/§4/§6 (2026-07-19); redirect note at `ASK2-JOURNEY-REVIEW-2026-07-18.md.archive-note.md` |
| `ASK2-DEVELOPMENT-PLAN.md` | Phase 0–6 Fork-B execution plan + execution log | Folded into master plan §1/§3/§4/§5/§7 (2026-07-19); redirect note at `ASK2-DEVELOPMENT-PLAN.md.archive-note.md` |
| `COMPLETION-PLAN.md` | Cross-track DPAA1/VPP/ASK2 consolidated roadmap | ASK2 build order + harness + DoD folded into master plan §4/§7 (2026-07-19); DPAA1/VPP tracks complete; redirect note at `COMPLETION-PLAN.md.archive-note.md` |
| `ASK2-PHASE2-AUTOMATION-PLAN.md` | Flow-offload automation (nft/YNL/debugfs paths, T1–T6) | Folded into master plan §5 M5 + §6 (2026-07-19); redirect note at `ASK2-PHASE2-AUTOMATION-PLAN.md.archive-note.md` |
| `ASK2-PERFORMANCE-MODERNIZATION.md` | cdx.ko parity analysis + MANIP/NAT opcode gap tables | Parity targets + opcode backlog folded into master plan §4/§5/§7 (2026-07-19); redirect note at `ASK2-PERFORMANCE-MODERNIZATION.md.archive-note.md` |
| `ASK2-F3-F6-UNBLOCK-PROPOSAL.md` | F3/F6 blocker analysis + regression bisect | Findings folded into master plan §6 + §5 (2026-07-19); redirect note at `ASK2-F3-F6-UNBLOCK-PROPOSAL.md.archive-note.md` |
| `ASK-PLANS.md` | 2026-06-09 ASK/ASK2 documentation hub | Hub role superseded by `plans/ASK2-MASTER-PLAN.md` §8 + `specs/ask2-rewrite-spec.md` v1.10 (2026-07-19); hub maintenance rules carried into this README; redirect note at `ASK-PLANS.md.archive-note.md` |

Archived 2026-05-25 as part of the v1.3 doc consolidation following PR14z21 M2 gate run.

Archived 2026-06-08 as part of the dpaa1 cross-flavor doc consolidation: the ASK2 `ask20`-era execution/triage/test-parity trackers and the completed repo-layout / patch-migration plans (the last seven rows above) were superseded once the FMan PCD subsystem moved into the common board stack and `specs/dpaa1-afxdp-modernization-spec.md` became the cross-flavor source-of-truth.

Archived 2026-07-19 as part of the ASK2 master-plan consolidation: the seven plan documents above (journey review, development plan, completion plan, phase-2 automation, performance modernization, F3/F6 unblock proposal, and the ASK-PLANS hub) were superseded by the single authoritative `plans/ASK2-MASTER-PLAN.md`. Later the same day (user decision), the redirect stubs at their old `plans/` paths were moved into this archive as `<name>.md.archive-note.md` files, leaving `plans/` with live documents only.

## Maintenance rules (carried over from the archived ASK-PLANS hub)

- When an active ASK2 plan is **archived**, add its row to the index above with rationale, and verify the superseded-document register (`plans/ASK2-MASTER-PLAN.md` §8) maps its content forward — in the same change.
- New ASK2 planning content goes **into the master plan** (`plans/ASK2-MASTER-PLAN.md`), not into new plan documents.
- This index lists archives; it does **not** carry architecture. Architectural facts belong in `specs/` and `arch/` per the `AGENTS.md` spec/implementation layering rule.