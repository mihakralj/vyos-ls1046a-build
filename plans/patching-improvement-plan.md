# Improvement Plan: Scalable and Resilient Patching Pipeline

**Document ID:** IP-2026-07-19-003 · **Version 1.1.0** · **HADS 1.0.0**
**Repository:** `mihakralj/vyos-ls1046a-build`, branch `dpaa1`, reviewed at `5811c91`
**Baseline:** `plans/TA-2026-07-18-002-patch-architecture.md` v1.2, implemented across 20 commits (`9a6cec4..5811c91`)
**Purpose:** Score the Phase 0 + Phase R implementation, register the new defects they surfaced and resolved, and lay out remaining work for the corrected sequencing.

## AI READING INSTRUCTION

Read `[SPEC]` and `[BUG]` blocks for authoritative facts. Read `[NOTE]` for rationale.
Sections marked **v1.1** are additions since the v1.0 baseline.

---

## 1. Implementation Scorecard vs TA-2026-07-18-002

| TA item | Status | Evidence and notes |
|---|---|---|
| T1 false count==1 comment fixed | **DONE** | comment states the truth; 7 active count gates added (v1.2) |
| T2 dead-file cleanup + manifest | **DONE** | `0150-*.patch.OLD` and `F_070b.py` deleted; `manifest.json` with 20 active entries |
| T3 `mutate.py` count-gated helper | **BUILT + ADOPTED (7 sites, v1.2)** | helper supports `expected=1` (hard-fail), `expected=-1` (optional), `expected=0` (expect-none), `--check` dry-run, `once` mode. 7 simple sed→mutate conversions done (`581c49c`). F-085 restored to `required(1)` after 0158 regenerated (`5811c91`) |
| T4 3-way fallback detection | **PARTIAL** | per-patch echo on `Falling back` exists; no end-of-run counter, no `::warning` annotation, no refresh artifact |
| T5 mergiraf `.gitattributes` prep | **DONE, dormant** | allowlist `dpaa/*`, denylist `fman_pcd*`/`fman_keygen*`; correctly inert until rebases exist |
| T6 no-zombie gate | **DONE** | `test-fixups.sh` check [4] asserts fixup files match manifest |
| Metadata (Upstream-Status, Risk-Tier) | **DONE** | metadata as series comments; headers-in-patches aborted (breaks `git apply`). Trailers-in-commits for the canonical branch |
| Round-trip tool | **REWRITTEN, USABLE (v1.2)** | `cmd_verify` is non-destructive (exports to tempdir only); `cmd_export` uses Patch-Name trailers, protects 0001-*/0003-* namespace, refuses dirty overwrite without `--force`. Three blocking defects from v1.0 (NF-04 destruct, NF-05 rename, NF-06 missing temp compare) all resolved |
| CI round-trip gate | **NOT STARTED** | tool not wired to any workflow |
| Phase R (Recovery) | **SUBSTANTIALLY COMPLETE (v1.2)** | see §7 Phase R scorecard below |
| Phase 1/2 (canonical branch, fold fixups) | **Phase 1 partial (branch bootstrapped), Phase 2 not started** | canonical branch `vyos-6.18.38-dpaa1` exists (106 commits, `~/kernel-git-cache/linux/`); CI uses persistent git clone; round-trip written; fixup fold-in not started |

Net: Phase 0 landed in one day (v1.1). Phase R landed two days later (v1.2) — 0158 regenerated, 0159–0162 deleted as obsolete, round-trip rewritten, CI git clone operational, 7 fixups count-gated. The remaining gap is fixup fold-in (Phase 2) and silicon-encoding assert (Phase 3).

## 2. Findings From the Implementation Round

### NF-01 (CLOSED v1.2): Five workstream patches dropped from the build

**v1.0 finding:** `a88b006`/`178d6bd` removed `0158` to `0162` from the series. These are not peripheral: fqid-resolution-compose, e2-hash-probe, EKFC programming, RCCB-to-FE_ENTER direct dispatch, and the port-arm EKFC fix.

**v1.2 resolution:** Analysis on the canonical branch (`5811c91`) proved that 0159–0162 contained **no unique functionality** not already present in patches 0122–0157 + F-0xx fixups. They were deleted. 0158 was regenerated from the canonical branch (437 lines) and restored to the series. The five-patch gap is closed.

### NF-02 (CONFIRMED + RESOLVED v1.2, root cause): Skipped patches un-appliable by construction

**v1.0 finding:** `0158` to `0162` are raw `git diff` output generated from a fixup-mutated tree. Their preimage context includes text the pristine patch stack never produces → stack-incompatible from birth.

**v1.2 resolution:** Confirmed. 0158 was regenerated from the canonical branch (`git clone v6.18.38` + 0068–0157 applied cleanly), producing a stack-compatible patch. 0159–0162 were audited against the canonical tree and confirmed to add no unique symbols or functionality beyond what 0122–0157 already provide. Deleted. Rule P8 enshrined: patches generated only from trees fully described by the stack.

### NF-03 (RESOLVED v1.2): F-084 regressed to a documented silent no-op

**v1.0 finding:** `fa2f147` reverted F-084 from `mutate.py` back to bare `sed` because its anchor lived inside skipped `0158`. The loud-failure mechanism worked as designed, and the response silenced the detector.

**v1.2 resolution:** 0158 is now in-tree → F-084's anchor is present → the revert is moot. F-084 remains as bare `sed` pending Phase 2 fold-in (its 1-line intent belongs in 0158 itself). The detector is no longer being silenced — the inconsistency that triggered it no longer exists. F-085 was restored from `optional(-1)` to `required(1)`.

### NF-04 (RESOLVED v1.2): `kernel-roundtrip.sh verify` was destructive

**v1.0 finding:** `cmd_verify` called `cmd_export` with output aimed at the real `$PATCH_DIR`, deleting the curated series file and reporting spurious success.

**v1.2 resolution:** Rewritten (`9060ad2`). `cmd_verify` now exports to a tempdir only (`mktemp -d`), compares against the working patch dir with `diff -r`, exits nonzero on any mismatch, and never touches the real directory. `cmd_export` refuses to overwrite a dirty patch dir unless `--force`.

### NF-05 (RESOLVED v1.2): Export renumbers into downstream protection glob

**v1.0 finding:** `cmd_export` used plain `format-patch`, producing `0001-*` onward, colliding with vyos-build's own `0001-*/0003-*` preservation logic. Name stability is a downstream-contract requirement.

**v1.2 resolution:** Rewritten (`9060ad2`). The exporter reads `Patch-Name:` trailers from commits, renames `format-patch` output to the trailer value, preserves today's `NNNN-name.patch` identities, and regenerates the series file with metadata comments from commit trailers. Downstream 0001-*/0003-* namespace is never touched.

### NF-06 (RESOLVED v1.2): Series file carried dangling metadata comments

**v1.0 finding:** Where `0158` to `0162` lines were deleted, their metadata comment lines remained orphaned. Comment-adjacent metadata has no binding to its patch line.

**v1.2 resolution:** Purged (`9060ad2`). 5 orphaned metadata comments deleted; series restored to 260 lines. NF-06 confirms trailers-in-commits as the only durable metadata home (Rule P6).

### NF-07 (MEDIUM — STILL OPEN): Manifest formalizes unaudited debugging archaeology

Active entries include F_055 ("F-060 v3d MUX context write"), F_056 ("F-058 MUX/Transition/ENQ AD writes"), and F_073D ("Terminal ENQ"). These are the diagnosed descriptor-stomping zombies. The manifest needs a disposition audit: each active entry gets `fold-into: <patch>`, `retire-after: <gate>`, or `permanent-with-justification`.

**v1.2 status:** Not yet audited. This is Phase R R6 — should be done before Phase 2 fold-in begins.

### NF-08 (RESOLVED v1.2): `test-fixups.sh` check [1] path bug

**v1.0 finding:** check [1] wrote `_fixup_bash_check.sh` to CWD but ran `bash -n` against `/tmp/_fixup_bash_check.sh`.

**v1.2 resolution:** Fixed (`581c49c`). The check now validates the file it actually wrote: `bash -n "$TMPFILE"` where `TMPFILE` is the path it wrote to. Both CWD and `/tmp` fallback paths are correct.

### NF-09 (NEW v1.2, RESOLVED): 0159–0162 confirmed as NF-02 duplicates — obsolete, not skipped

**Finding (2026-07-19, `5811c91`):** When the canonical branch was bootstrapped (v6.18.38 + patches 0068–0157), systematic comparison showed that every claimed function of 0159–0162 already existed at the expected source locations:

| Patch | Claimed function | Already present in | Verdict |
|---|---|---|---|
| 0159 (e2-hash-probe) | `fman_pcd_fe_hash_probe_show()` debugfs | 0122 + F-073D | **OBSOLETE** |
| 0160 (EKFC programming) | EKFC register writes for FE-VM | 0122–0157 + F-068/F-072 series | **OBSOLETE** |
| 0161 (RCCB→FE_ENTER direct) | dispatch topology | 0131 + F-073D | **OBSOLETE** |
| 0162 (port-arm EKFC fix) | `fman_pcd_fe_port_arm()` EKFC configuration | 0131 + F-072/F-073D | **OBSOLETE** |

**Resolution:** All four deleted from disk, series, and skip-ledger. The NF-02 diagnosis was correct: these were pure fixup-layer artifacts, not independent patches. Their absence from the build does not remove any functionality.

## 3. Synthesis

**[SPEC — v1.2]** Phase 0 achieved its goal: drift is now loud. Phase R restored the workstream (0158 regenerated, 0159–0162 proven obsolete). The canonical branch exists and the round-trip tool works. The remaining gap is structural: fold the fixup layer into commits (Phase 2) so that the patch stack becomes the single source of truth, and encode the silicon contract (Phase 3) so that the recurring bug classes fail in CI, not on the board.

**[SPEC]** The NF-01/NF-02 emergency that defined the v1.1 plan is closed. 0158 is restored, 0159–0162 are proven to contain nothing unique. The NF-03 silent-no-op precedent is moot — the inconsistency it silenced no longer exists. The three tool regressions (NF-04/NF-05/NF-08) are fixed.

## 4. Corrected Target Model (deltas from TA)

The TA model stands. Four corrections from implementation evidence, now all implemented:

1. **Metadata lives in commit trailers** (Rule P6) — never in patch files (breaks `git apply`) and never as series comments (unbound, NF-06). **v1.2: Implemented in round-trip exporter, canonical branch carries trailers.**
2. **Name stability via `Patch-Name:` trailer** (Rule P7) — exporter renames `format-patch` output to trailer value. **v1.2: Implemented in round-trip (`9060ad2`).**
3. **Non-destructive verify** — exports to tempdir only, compares, exits nonzero on mismatch, never touches real dir. **v1.2: Implemented in round-trip (`9060ad2`).**
4. **CI becomes a refresh generator** — when a patch lands via 3-way fallback, `git format-patch -1` of that commit updates the patch. **Not yet implemented (partial: per-patch fallback echo exists; no artifact upload).**

## 5. Tooling Decisions (final)

| Tool | Decision | Role and conditions |
|---|---|---|
| `git apply --3way` + per-patch commits | KEEP | add fallback counter summary + `::warning` + refresh-artifact upload; iterate series file instead of `find \| sort` |
| `git quiltimport` / `format-patch` round-trip | ADOPT as canonical | rewritten (v1.2); requires CI gate |
| `git rebase -i --autosquash` + `commit --fixup` | ADOPT | sole sanctioned mechanism for changing existing patches; replaces Layer 2 |
| `git rerere` | ADOPT at Phase 3 | `rerere.enabled=true` in canonical clone; export `rr-cache` |
| `mutate.py` | KEEP for transition | `--check` dry-run mode added (v1.2); `expected=-1` optional mode (v1.2); `expected=0` expect-none mode (v1.2); 7 sites converted; scheduled for deletion at end of Phase 2 |
| mergiraf | HOLD until Phase 3 | activates only inside canonical-branch rebases; denylist + §17 gate |
| Coccinelle | ADOPT for Tier B | static-demotion/export patches become semantic patches |
| quilt | REJECTED | unchanged from TA §3.2 |
| stgit / jj | OPTIONAL | single-developer trial only |

## 6. Patch-Type Policy ("what kinds of patches we perform")

(Unchanged from v1.0 — reproduced for completeness.)

| Type | Definition | Rules |
|---|---|---|
| T-NEW (Tier A) | adds new files, touches only its own Makefile/Kconfig line | plain patch; near-zero bump risk |
| T-EXPORT (Tier B) | demotes statics, adds `EXPORT_SYMBOL_GPL`, adds accessors to hot files | Coccinelle semantic patch where feasible |
| T-HOTEDIT (Tier C) | modifies upstream logic in `dpaa_eth.c`, `fman_port.c`, `fman_keygen.c`, `sfp.c` | size budget (soft cap 150 lines); mandatory human review at every bump |
| T-BACKPORT | cherry-picked upstream commit | carries `Upstream-Status: Backport <sha>`; deleted at the bump that contains it |
| T-SHIM | mutation of vyos-build scripts (Layer 3) | anchor-verified with hard exit |
| T-CONFIG | Kconfig fragment forcing | config files only |
| T-OOT | out-of-tree module source | preferred destination for all new logic |
| T-FIXUP | post-apply source mutation | **forbidden as steady state.** Transitional only, folded or deleted within two integration cycles (Rule P5) |

SKIP is not a type. A series SKIP requires a ledger entry (`plans/skip-ledger.md`): reason, owner, expiry date, restore plan. CI fails when a SKIP passes its expiry.

## 7. Sequencing

### Phase R: Recovery (~2 days) — STATUS: SUBSTANTIALLY COMPLETE

```text
R1  [DONE] Rewrite kernel-roundtrip.sh (non-destructive verify, trailer-driven
     export, dirty-dir refusal).                                          [NF-04, NF-05]
R2  [DONE] Bootstrap canonical branch: clone v6.18.38, apply 103 patches,
     one commit per patch. Branch: vyos-6.18.38-dpaa1, 106 commits.      [TA Phase 1]
R3  [DONE] Regenerated 0158 from canonical branch (437 lines). Analyzed
     0159-0162 against canonical tree — found OBSOLETE. Deleted 0159-0162
     from disk, series, skip-ledger. F-084 anchor resolved, F-085 restored
     to required(1).                                                      [NF-01, NF-02, NF-03]
R4  [DONE] Exported; restored 0158 to series; removed 0159-0162 entries;
     purged 5 orphan metadata comments; BOARD_STAGE_SKIP now only 0150.   [NF-06]
R5a [DONE] Converted 7 simple sed→mutate.py (count-gated, optional-mode
     for stage-dependent fixups). ~28 complex multiline sed deferred to
     Phase 2 fold-in.                                                     [NF-03 class]
R5b [DONE] Fixed test-fixups.sh check[1] path bug.                        [NF-08]
R5c [DONE] Added mutate.py --check dry-run mode, expected=-1 optional,
     expected=0 expect-none modes.                                        [NF-03 class]
R6  [OPEN] Manifest disposition audit: every active fixup gets fold-into /
     retire-after / permanent-with-justification. F_055, F_056, F_073D
     audited first against settled dispatch topology.                     [NF-07]
```

**Exit gate:** build ships with 0158 restored, 0159–0162 deleted, 103 active patches in series, 7 count-gated fixups, non-destructive round-trip verify, skip-ledger tracking only 0150 (permanent).

### Phase 1: Canonicalize (~1 week)

Branch pushed (separate `ls1046a-kernel` repo or protected branch); CI round-trip identity gate on every PR touching `kernel/common/patches/`; apply loop iterates the series file (not `find|sort`); fallback counter summary plus refresh-artifact upload; `test-fixups.sh` runs in CI on every `bin/` change with a dry-run staging pass.

### Phase 2: Fold the fixup layer (~2 weeks, interleaved with board work)

Ordered by the R6 disposition list, lowest-risk first: each fixup becomes `commit --fixup` on its owning patch-commit, autosquash, export, delete from script and manifest, FE-VM-affecting ones gated on board `fe_verify` pass. Exit gate: `manifest.json` active count for kernel-C mutations = 0; `mutate.py` deleted; ci-setup-kernel.sh shrinks to config + Layer 3 shims + staging.

### Phase 3: Bump machinery (at the next kernel bump)

Rebase-driven bump with rerere (cache committed), mergiraf enabled per the prepped attributes, every 3-way and mergiraf event logged into a bump record, conflict census per tier feeding the first upstreaming batch (M8), weekly canary extended to rebase the canonical branch onto `linux-6.18.y` HEAD and report conflicts per commit.

### Phase 4: Steady state

Patch-type policy enforced at review; skip ledger empty; §17 static asserts plus KUnit descriptor audit in every build (carried over from TF-2026-07-18-001 Priority 1).

## 8. Binding Rules (P-series, same force as the R/T/C rules)

```text
P1  No bare sed/regex mutation of kernel C anywhere in the pipeline.
    Transitional mutations go through mutate.py; steady state has none.
P2  Every mutation is count-gated, manifest-listed with a disposition,
    and series-aware: it hard-fails only when its anchor-owning patch
    is staged, and refuses to run when it is not.
P3  No SKIP without a ledger entry (reason, owner, expiry, restore
    plan). CI fails on expired skips. Silencing a detector is never an
    accepted resolution of what it detected.
P4  Drift gets refreshed, not skipped: a 3-way fallback or mutate
    failure files a refresh task; the CI refresh artifact is the
    starting point; resolution lands within one integration cycle.
P5  Fixups are temporary by definition: fold or delete within two
    integration cycles, enforced by manifest disposition dates.
P6  Patch metadata lives in commit trailers only. Patch files and the
    series file are generated artifacts; hand edits to them are
    rejected once Phase 1 lands.
P7  Patch names are stable identities (Patch-Name trailer). Nothing in
    the pipeline may emit patches named into the downstream 0001-*/0003-*
    protection namespace.
P8  Patches are generated only from trees whose state is fully
    described by the stack. Diffing a fixup-mutated tree into a patch
    is the NF-02 poison and is banned.
P9  Meta-tooling changes (apply loop, roundtrip, mutate, test-fixups)
    require test-fixups.sh green plus a dry-run staging build in CI.
P10 Structured/auto merge output (3-way, mergiraf) is never review.
    Tier C resolutions and anything under the mergiraf denylist get
    human eyes; everything gets the compile-time §17 asserts.
```

## 9. Success Metrics

```text
Metric                                   v1.0 Baseline   v1.2 Current    Target (Phase 2 exit)
Kernel-C fixups active                   ~19             20 (manifest)   0
Bare sed -i on kernel C                  33              28              0
Patches skipped / dropped from build     6 (0150, 0158-0162)  1 (0150 permanent)  0 non-permanent
3-way fallbacks per clean build          unmeasured      0 (clean apply)  0 steady-state, every event artifacted
Round-trip verify                        broken (NF-04)  non-destructive  green, in CI
Time to refresh one drifted patch        skip-or-sed     hours (R3 done)  < 30 min via branch + export
Kernel bump wall time                    unknown         unknown          < 1 day for 6.18.y minor
Canonical branch                         absent          106 commits      pushed, protected, CI-gated
Count-gated fixup mutations              0               7                0 (all folded into commits)
```

The single most important behavioral change is P3/P4: when the pipeline gets loud, the answer is the refresh verb, never the mute button. Phase R made that verb real; Phase 2 makes it the default.
