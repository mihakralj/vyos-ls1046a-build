# plans/archive/

Historical design and forensic documents preserved for bisect/audit purposes. These files describe approaches that have been **abandoned**, **superseded**, or are **dated snapshots** of work that has since landed (or been replaced).

**Do not consult these for current architecture.** They are kept because:

1. They contain accurate facts about silicon behaviour and kernel/driver internals that may be useful when re-investigating a problem.
2. They document why we chose the path we are on (the alternatives we ruled out).
3. They are reachable from older Qdrant memory entries that pre-date the archive move; preserving the filenames keeps those memory entries valid.

For current state, see (in order of authority):

- `specs/ask2-rewrite-spec.md` — architectural source-of-truth (currently v1.3 → v1.4 update pending).
- `plans/ASK2-COURSE-CORRECTION.md` — active 5-phase execution plan.
- `plans/ASK2-MODERN-ARCHITECTURE-REVIEW.md` — driver doc that drove the v1.3 course-correction.
- `plans/ASK2-IMPLEMENTATION.md` — running per-PR tracker.
- `plans/ASK2-PHASE2-PATCH-TRIAGE.md` — patch-table (KEEP/ARCHIVE/PARTIAL classification).
- `plans/PR14z19-PATH-A-DESIGN.md` — the Path A design that's actually shipping.

## File index

| File | Topic | Why archived |
|---|---|---|
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

Archived 2026-05-25 as part of the v1.3 doc consolidation following PR14z21 M2 gate run.