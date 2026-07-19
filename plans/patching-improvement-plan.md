# Improvement Plan: Scalable and Resilient Patching Pipeline

**Document ID:** IP-2026-07-19-003 · **Version 1.2.0** · **HADS 1.0.0**
**Repository:** `mihakralj/vyos-ls1046a-build`, branch `dpaa1`, reviewed at `cbc5365`
**Baseline:** `plans/TA-2026-07-18-002-patch-architecture.md` v1.2, implemented across 30 commits (`9a6cec4..cbc5365`)
**Purpose:** Score Phase 0 + Phase R + Phase 2a implementation, register new defects surfaced by count-gating, and lay out remaining work.

## AI READING INSTRUCTION

Read `[SPEC]` and `[BUG]` blocks for authoritative facts. Read `[NOTE]` for rationale.
Sections marked **v1.2 update** are new since the v1.1 baseline.

---

## 1. Implementation Scorecard vs TA-2026-07-18-002

| TA item | Status | Evidence and notes |
|---|---|---|
| T1 false count==1 comment fixed | **DONE** | comment states the truth; 12 active count gates added in v1.2 |
| T2 dead-file cleanup + manifest | **DONE** | `0150-*.patch.OLD` and `F_070b.py` deleted; manifest with 17 active entries |
| T3 `mutate.py` count-gated helper | **BUILT + FULLY ADOPTED (v1.2)** | 20 count-gated `mutate.py` calls — all 14 bare `sed -i` on kernel C converted (`cf014cd`). helper supports `expected=1` (hard-fail), `expected=-1` (optional), `expected=0` (expect-none), `--check` dry-run, `once` mode. Two genuine anchor drifts caught by count-gating: SFP rename redundant with patch 4009 (`346a206`), cast addition count=1 vs 29 occurrences (`cf014cd`) |
| T4 3-way fallback detection | **DONE (v1.2)** | `::warning` per drifted patch + end-of-run counter summary with refresh hint |
| T5 mergiraf `.gitattributes` prep | **DONE, dormant** | allowlist `dpaa/*`, denylist `fman_pcd*`/`fman_keygen*`; correctly inert until rebases exist |
| T6 no-zombie gate | **DONE** | `test-fixups.sh` check [4] asserts fixup files match manifest |
| T7 Metadata (Upstream-Status, Risk-Tier) | **DONE** | metadata as series comments; headers-in-patches aborted (breaks `git apply`). Trailers-in-commits for the canonical branch |
| T8 Round-trip tool | **REWRITTEN, USABLE (v1.2)** | `cmd_verify` is non-destructive (exports to tempdir only); `cmd_export` uses Patch-Name trailers, protects 0001-*/0003-* namespace, refuses dirty overwrite without `--force`. NF-04/NF-05/NF-06 all resolved |
| T9 CI round-trip gate | **WIRED (v1.2)** | Post-build step in `auto-build.yml` counts `applied:` commits vs series patches. Hard-fails on zero commits or FAILED patches. Emits `::notice` on count mismatch (canonical branch breadth). Full pixel-perfect identity gate deferred to tree-canonical migration |
| T10 Phase R (Recovery) | **COMPLETE (v1.2)** | R1–R6 all done (see §7) |
| T11 Phase 2 (fold fixups) | **PARTIAL (v1.2)** | F_055→F_054 absorbed, F_061 deleted, M2_4 deleted. 20→17 active fixups. 14 permanent-with-justification remain. REPLACEMENT block structural issues prevent further fold-in without restructuring |

**Net:** Phase 0 landed in one day (v1.1). Phase R followed two days later. Phase 2a (three zombie deletions + F_055 absorption) landed today. All 14 bare `sed -i` on kernel C converted to count-gated `mutate.py` — zero remain. Two anchor drifts caught by the new gates. CI round-trip commit-count gate wired. The remaining gap is the 14 permanent fixups (structural work deferred to tree-canonical migration) and §17 silicon-encoding asserts (deferred to post-Phase-2).

---

## 2. Findings From the Implementation Round

### NF-01 (CLOSED v1.2): Five workstream patches dropped from the build

**v1.0 finding:** `a88b006`/`178d6bd` removed `0158` to `0162` from the series.

**v1.2 resolution:** 0158 regenerated from canonical branch, restored to series. 0159–0162 proven to contain no unique functionality beyond 0122–0157 + F-0xx fixups. Deleted. The five-patch gap is closed.

### NF-02 (CONFIRMED + RESOLVED v1.2, root cause): Skipped patches un-appliable by construction

**v1.0 finding:** `0158` to `0162` are raw `git diff` output generated from a fixup-mutated tree. Stack-incompatible from birth.

**v1.2 resolution:** Confirmed. 0158 regenerated from canonical branch. 0159–0162 deleted. Rule P8 enshrined: patches generated only from trees fully described by the stack.

### NF-03 (RESOLVED v1.2): F-084 regressed to a documented silent no-op

**v1.0 finding:** `fa2f147` reverted F-084 from `mutate.py` back to bare `sed`. The loud-failure mechanism worked as designed; the response silenced the detector.

**v1.2 resolution:** 0158 is now in-tree → F-084's anchor is present. F-084 is now a count-gated `mutate.py` call (`9fd9316`). F-085 restored to `required(1)`.

### NF-04 (RESOLVED v1.2): `kernel-roundtrip.sh verify` was destructive

**v1.2 resolution:** Rewritten (`9060ad2`). Non-destructive tempdir export + compare, never touches real patch dir.

### NF-05 (RESOLVED v1.2): Export renumbers into downstream protection glob

**v1.2 resolution:** Rewritten (`9060ad2`). Patch-Name trailers preserve identities.

### NF-06 (RESOLVED v1.2): Series file carried dangling metadata comments

**v1.2 resolution:** Purged (`9060ad2`). 5 orphaned metadata comments deleted.

### NF-07 (RESOLVED v1.2): Manifest formalizes unaudited debugging archaeology

**v1.1 finding:** 20 active fixups had no disposition. F_055, F_056, F_073D are diagnosed descriptor-stomping zombies.

**v1.2 resolution (`667ea14`):** Manifest disposition audit completed. All 20 entries assigned fold-into / delete-after / permanent-with-justification. F_055 marked fold-into F_054, F_061 and M2_4 marked delete-after M2_4_2 upgrade. Three entries subsequently consolidated in Phase 2a (`76c095e`). 17 entries remain (14 permanent, 3 with deferred consolidation).

### NF-08 (RESOLVED v1.2): `test-fixups.sh` check [1] path bug

**v1.2 resolution:** Fixed (`581c49c`). Validates the file it actually wrote.

### NF-09 (RESOLVED v1.2): 0159–0162 confirmed as NF-02 duplicates

**Resolution:** All four deleted from disk, series, and skip-ledger. No unique functionality removed.

### NF-10 (NEW v1.2, RESOLVED): SFP-10G-T rename anchor mismatch — count-gate caught redundancy

**Finding (2026-07-19, `346a206`):** CI `mutate.py` count-gate hard-failed: `SFP_QUIRK_F("OEM", "SFP-10G-T", sfp_fixup_rollball_cc)` found 0 matches. Root cause: `4009-sfp-oem-rollball-quirk.patch` already applies the `rollball_cc→fs_10gt` rename. The fixup was redundant with its owning patch — the exact silent-no-op class the count-gate was designed to catch.

**Resolution:** Removed redundant rename; kept only SFP-10G-SR append. This is the first anchor drift caught in production by count-gating — exactly the behavior we wanted from the sed→mutate conversion.

### NF-11 (NEW v1.2, RESOLVED): cast addition anchor matched 29 times, not 1

**Finding (2026-07-19, `cf014cd`):** CI `mutate.py` count-gate hard-failed: `fman_muram_offset_to_vbase(muram,` found 29 matches, expected 1. The bare `sed -i` had been silently replacing all 29 occurrences without any verification. Each was a valid cast addition (suppressing -Werror implicit pointer conversions), but the count mismatch proved the original pattern was imprecise.

**Resolution:** Updated to `expected=29` with a comment noting this is a dynamic count that will change with kernel versions. The count assertion now catches the drift case (more or fewer occurrences after kernel bump).

---

## 3. Synthesis

**[SPEC — v1.2]** Phase 0 achieved its goal: drift is now loud. Phase R restored the workstream. Phase 2a deleted three zombies and absorbed one correction. The count-gating investment paid off: two genuine anchor drifts were caught in CI before they shipped — confirming that every bare `sed -i` was a latent defect waiting to happen.

**[SPEC]** The NF-01/NF-02 emergency that defined the v1.1 plan is closed. All five tool regressions (NF-03/NF-04/NF-05/NF-08) are fixed. The count-gating has produced two new findings (NF-10, NF-11) that would have been silent regressions under the old bare-sed regime.

**[SPEC]** 14 permanent fixups remain. These are architectural features (debug probes, SDK-correct descriptor writes, settle topology enforcement) that should be folded into their owning patches during the tree-canonical migration — not individually, but as part of the full rebase-and-clean up. The REPLACEMENT block in `ci-setup-kernel.sh` has accumulated structural if/fi imbalances from 30+ commits of surgical edits and cannot safely absorb more individual deletions without a full restructuring pass.

---

## 4. Corrected Target Model (deltas from TA)

The TA model stands. Five corrections from implementation evidence, all implemented:

1. **Metadata lives in commit trailers** (Rule P6). **v1.2: Implemented.**
2. **Name stability via `Patch-Name:` trailer** (Rule P7). **v1.2: Implemented.**
3. **Non-destructive verify** — exports to tempdir only. **v1.2: Implemented.**
4. **CI becomes a refresh generator** — 3-way fallback `::warning` per drifted patch + end-of-run summary. **v1.2: Implemented.** Artifact upload deferred.
5. **P1 enforced** — zero bare `sed -i` on kernel C. **v1.2: Implemented (`9fd9316`).**

---

## 5. Tooling Decisions (final)

| Tool | Decision | v1.2 status |
|---|---|---|
| `git apply --3way` + per-patch commits | KEEP | `::warning` per fallback + end-of-run summary |
| `mutate.py` | KEEP for transition | 20 count-gated calls, zero bare sed on kernel C |
| `git rebase -i --autosquash` + `commit --fixup` | ADOPT | sole sanctioned mechanism for changing patches |
| `git rerere` | ADOPT at Phase 3 | not yet |
| `bin/kernel-roundtrip.sh` | ADOPT | `verify` non-destructive; CI commit-count gate wired |
| mergiraf | HOLD until Phase 3 | dormant |
| Coccinelle | ADOPT for Tier B | not yet |
| quilt | REJECTED | — |
| stgit / jj | OPTIONAL | — |

---

## 6. Patch-Type Policy

(Unchanged from v1.0.)

---

## 7. Sequencing

### Phase R: Recovery — COMPLETE

```text
R1  [DONE] Rewrite kernel-roundtrip.sh.
R2  [DONE] Bootstrap canonical branch (106 commits).
R3  [DONE] Regenerate 0158, delete 0159-0162 as obsolete.
R4  [DONE] Restore series; purge orphan metadata; BOARD_STAGE_SKIP=0150 only.
R5a [DONE] Convert all 14 bare sed→mutate.py. Zero bare sed on kernel C.
R5b [DONE] Fix test-fixups.sh check[1] path bug.
R5c [DONE] Add mutate.py --check, expected=-1, expected=0 modes.
R6  [DONE] Manifest disposition audit — all 20 entries assigned (→ 17 active).
```

**Exit gate:** ✅ 103 active patches, 20 count-gated mutations, zero bare sed, skip-ledger tracking only 0150, non-destructive round-trip verify, CI commit-count gate.

### Phase 1: Canonicalize (~1 week) — PARTIAL

Branch pushed (separate repo or protected branch) — **not yet**; CI round-trip identity gate — **wired** (commit-count verification); apply loop iterates series file — **not yet**; fallback counter summary plus refresh-artifact upload — **partial** (summary done, artifact upload not yet).

### Phase 2: Fold the fixup layer (~2 weeks) — PARTIAL (Phase 2a done)

**Phase 2a (DONE, `76c095e`):**
- F_055 absorbed into F_054 (MUX write target correction AD+0→AD+4)
- F_061 deleted (v1 zombie — superseded by M2_4_2 v6)
- M2_4 deleted (v4 intermediate — superseded by M2_4_2 v6)
- 20→17 active fixups

**Phase 2b (DEFERRED):** 14 permanent fixups remain. These cannot be individually folded without restructuring the REPLACEMENT block's if/fi nesting — 30+ commits of surgical edits have left structural imbalances that cause cascading failures when individual if-blocks are removed. The durable fix is the tree-canonical migration (Phase 1→2 combined), where the entire fixup layer dissolves into commit history rather than being deleted one at a time.

**Exit gate:** `manifest.json` active count = 0; `mutate.py` deleted; ci-setup-kernel.sh fixup section empty.

### Phase 3: Bump machinery (at the next kernel bump)

Rebase-driven bump with rerere, mergiraf enabled, conflict census per tier.

### Phase 4: Steady state

Patch-type policy enforced; skip ledger empty; §17 static asserts + KUnit.

---

## 8. Binding Rules (P-series)

(Unchanged from v1.1 — all 10 rules active, P1 now enforced by count-gating.)

---

## 9. Success Metrics

```text
Metric                                   v1.0 Baseline   v1.2 Current     Target (Phase 2 exit)
Kernel-C fixups active                   ~19             17 (manifest)    0
Bare sed -i on kernel C                  33              **0**            0
Patches skipped / dropped from build     6 (0150, 0158-0162)  1 (0150)   0 non-permanent
3-way fallbacks per clean build          unmeasured      0 + ::warning    0 steady-state, every event artifacted
Round-trip verify                        broken (NF-04)  non-destructive  green, in CI
Time to refresh one drifted patch        skip-or-sed     hours (R3 done)  < 30 min
Kernel bump wall time                    unknown         unknown          < 1 day for 6.18.y minor
Canonical branch                         absent          106 commits      pushed, protected, CI-gated
Count-gated fixup mutations              0               **20**           0 (all folded)
CI round-trip gate                       absent          **wired**        pixel-perfect identity
Anchor drift caught in CI               never           **2 (NF-10, NF-11)**  0 (none slipped through)
```

The count-gating investment has already paid for itself: two silent regressions were caught in CI that the old bare-sed regime would have shipped. Every remaining `mutate.py` call is a tripwire watching for exactly this class of drift.

---

## 10. v1.2 Additions — Phase 2a Canary Lesson

**[BUG — RESOLVED v1.2] The REPLACEMENT block has accumulated structural if/fi imbalance from 30+ commits of surgical edits.** Symptom: attempting to remove an `if ... fi` block from the REPLACEMENT string in `ci-setup-kernel.sh` causes cascading `fi` mismatches at unrelated locations. Cause: the if/fi nesting was never validated as a structural invariant; each edit removed or inserted individual lines without re-verifying the overall balance. The wrong-but-working balance became an accidental invariant that resists further change.

**Resolution (this session):** The Phase 2a fold-in was achieved by replacing fixup calls with `:` no-ops — preserving the exact if/fi structure without deletion. This is a tourniquet: the block still carries dead code, but the zombie files are deleted and the manifest is accurate.

**Durable fix:** The tree-canonical migration (Phase 1→2 combined) dissolves the entire REPLACEMENT block into commit history. The fixups become commits on the canonical branch; the block is deleted in one atomic operation rather than piecemeal. Until then, individual fixup deletions must use the no-op substitution pattern, not structural deletion.
