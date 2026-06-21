# plans/archive/

Historical design and forensic documents preserved for bisect/audit purposes. These files describe approaches that have been **abandoned**, **superseded**, or are **dated snapshots** of work that has since landed (or been replaced).

**Do not consult these for current architecture.** They are kept because:

1. They contain accurate facts about silicon behaviour and kernel/driver internals that may be useful when re-investigating a problem.
2. They document why we chose the path we are on (the alternatives we ruled out).
3. They are reachable from older Qdrant memory entries that pre-date the archive move; preserving the filenames keeps those memory entries valid.

For current state, see (in order of authority):

- `plans/ASK-PLANS.md` — **the ASK / ASK2 documentation hub — start here.** Single index of every ASK plan (active + archived), the source-of-truth hierarchy, canonical reading order, and the current M2 status snapshot.
- `specs/dpaa1-afxdp-modernization-spec.md` — **the authoritative cross-flavor source-of-truth** (one DPAA1 driver core + `pcd_ops`/`qmgmt_ops`; the FMan PCD subsystem now lives in the common board stack, built-in for default/vpp/ask).
- `specs/ask2-rewrite-spec.md` — ASK2 architectural source-of-truth (v1.6).
- `specs/vpp-dpaa1-ls1046a-spec.md` — VPP-flavor (AF_XDP) design spec.
- `plans/ASK2-COURSE-CORRECTION.md` — ASK2 5-phase execution plan (kept as reference).
- `plans/ASK2-MODERN-ARCHITECTURE-REVIEW.md` / `plans/ASK-VS-ASK2-COMPARATIVE-REVIEW.md` — the reviews that drove Path A.

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
| `PR14z22-DESIGN.md` | DROP-miss diagnostic that proved silicon HIT path works | Diagnostic complete — silicon HIT proven at 6.945 Gbps / 16.63 % baseline |
| `ASK2-IMPLEMENTATION.md` | ASK2 per-PR implementation tracker (target spec v1.1) | Superseded by the `specs/dpaa1-afxdp-modernization-spec.md` cross-flavor milestone table; ASK2 spec is now v1.6 |
| `ASK2-PHASE2-PATCH-TRIAGE.md` | KEEP/ARCHIVE/PARTIAL classification of `kernel/flavors/ask/patches/0001-0053` | The ASK 1.x patch tree it classifies was deleted on `ask20`; FMan PCD now lives in the common board stack |
| `ASK2-CMM-TEST-PARITY.md` | Parity matrix mapping the 38 legacy `cmm/unit_tests` shell tests to ASK2 | The `cmm`/`we-are-mono/ASK` corpus was deleted; ASK2 offload has no CLI harness |
| `ASK2-NEXT-STEPS-2026-05-25.md` | Dated forensic roadmap (KG scheme priority-race) toward ASK2 GA | Snapshot only; references spec v1.3 (now v1.6) and the pre-cross-flavor architecture |
| `PR14z19-PATH-A-DESIGN.md` | Path A boot-time PCD-install design (graft-model replacement) | Design landed; joins its already-archived PR14z* siblings. Current state in the dpaa1 spec |
| `REPO-LAYOUT-REFACTOR.md` | Plan to consolidate `ASK/`, `ask-userspace/`, `data/ask-userspace/` userspace trees | All three trees were deleted on `ask20`; the refactor target no longer exists — **deleted 2026-06-21** |
| `PATCH-MIGRATION-3WAY.md` | `git apply --3way` + Mergiraf + rerere migration plan | Companion `INTEGRATION-PLAN.md` already archived; migration complete and the process is now documented in `AGENTS.md` |
| `MULTI-FLAVOR-RELEASE.md` | Multi-flavor (`default\|ask\|vpp`) build+release architecture | **RETIRED 2026-06-14** — flavor build split collapsed; single-image model now in `DUAL-DATAPLANE.md`. Moved from plans/ 2026-06-21. |
| `ZC-RX-SCOPE.md` | Zero-Copy RX scope brief for DPAA1 AF_XDP | **Functionally resolved 2026-06-10.** Only GAP 2 (bulk-flow steering optimization) remains. Moved from plans/ 2026-06-21. |
| `BUG3B-FLOOD-PLAN.md` | Ingress-policer flood-crash characterization test plan | **PROMOTED to plans/ 2026-06-21** — the flood-crash half of BUG 3b remains OPEN per the DPAA1 spec §5.6. This row kept for bisect reference. |

Archived 2026-05-25 as part of the v1.3 doc consolidation following PR14z21 M2 gate run.

Archived 2026-06-08 as part of the dpaa1 cross-flavor doc consolidation: the ASK2 `ask20`-era execution/triage/test-parity trackers and the completed repo-layout / patch-migration plans (the last seven rows above) were superseded once the FMan PCD subsystem moved into the common board stack and `specs/dpaa1-afxdp-modernization-spec.md` became the cross-flavor source-of-truth.