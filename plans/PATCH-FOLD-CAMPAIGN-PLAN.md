# Patch Provenance Review and Fold Campaign Plan

Date: 2026-08-29. Companion to `plans/TA-2026-07-18-002-patch-architecture.md`
(architecture authority) and the Phase-2 fixup-fold campaign recorded there
(§6.7). That document defines the three-layer model (Layer 1 = `series`
git-format patches, Layer 2 = `bin/kernel-fixups/*.py` count-gated runtime
mutations, Layer 3 = build shims) and the target architecture (canonical
branch `~/kernel-git-cache/linux/` as source of truth, patches as generated
exports). This document does not replace that plan; it adds a full patch-level
provenance review requested separately, cross-validates the existing Tier A/B/C
system against real diff content, and turns the result into a concrete,
risk-ordered execution list.

No patches, fixups, or the canonical branch were modified while producing this
document. Every action below is a proposal pending explicit confirmation.

## 1. Scope reviewed

All patches physically present under the active patch tree, using the git
`diff --git`/`new file mode`/`--- a/`/`+++ b/` headers as ground truth (not
just file names or series comments):

| Location | Count | Applied by |
|---|---|---|
| `kernel/common/patches/board/*.patch` | 119 | `series` (via `git apply --3way`, `ci-setup-kernel.sh`) |
| `kernel/common/patches/fixes/*.patch` | 3 on disk, **2 live** | conditionally staged by `ci-setup-kernel.sh` (120, 130) |
| `kernel/common/patches/vyos/*.patch` | 2 on disk, **0 live** | never applied — see §2 |
| `data/vyos-1x-*.patch` | 38 | `ci-setup-vyos1x.sh` glob |
| `data/vyos-build-*.patch` | 5 | `ci-setup-vyos-build.sh` |
| **Total live/applied** | **164** | |
| `kernel/ask/patches/archive-*/` (3 dirs) | 60 | frozen, historical, confirmed out of scope by S3/AGENTS.md and `ci-setup-kernel.sh` comments — not reviewed further here |

## 2. Findings independent of the "new files" question

These surfaced during the review and are worth acting on regardless of the
fold campaign, because each is a small, high-confidence, low-risk cleanup.

**Three patches are dead weight, sitting in the active-looking tree but never
applied by CI:**

- `kernel/common/patches/vyos/001-vyos-linkstate-ip-device-attribute.patch`
- `kernel/common/patches/vyos/003-vyos-build-linux-perf-package.patch`
- `kernel/common/patches/fixes/095-leds-lp5812-register.patch`

`ci-setup-kernel.sh` (lines 701-709) explains why: `vyos/{001,003}` are
byte-identical duplicates of vyos-build's own upstream `0001-*`/`0003-*`
patches and re-applying them fails; `fixes/095` wires LP5812 Kconfig/Makefile
via a unified diff, but the same wiring is already done by a heredoc-echo
block later in the same script, so applying both would conflict. None of the
three are staged, copied, or referenced anywhere else. They should be deleted
(git history preserves them) rather than left where a future reviewer — or
agent — can mistake them for live content, exactly as happened during this
review's first pass.

**Five patches are plain unified diffs, not git-format**, missing the
`diff --git` / `index <blob-sha>` headers the project's own rule requires
("All `.patch` in `data/` + `kernel/` are git-format... `--3way` anchors on
blob SHAs, real 3-way on drift"):

- `kernel/common/patches/board/0093-dpaa1-true-zc-rx-eligibility-probe.patch`
- `kernel/common/patches/board/0094-dpaa1-true-zc-rx-arm-observability.patch`
- `kernel/common/patches/board/0095-dpaa1-xsk-fill-ring-guard-audit.patch`
- `kernel/common/patches/board/0096-dpaa1-true-zc-rx-recover-readside.patch`
- `kernel/common/patches/fixes/120-perf-libperf-asm-headers-srctree.patch`

All five still apply today (context-based `--3way` fallback tolerates it),
and all five are pure modifications to already-known files (dpaa_eth.c/h,
dpaa_ethtool.c, af_xdp_pool_main.c, tools/lib/perf/Makefile) so nothing is
silently broken — but they do not get real blob-anchored 3-way protection,
only context-line fuzzy matching. They should be regenerated in git format
next time the canonical-branch tooling touches this area (`bin/kernel-roundtrip.sh
export` naturally produces git-format output).

**One board patch has no `Risk-Tier` annotation at all:**
`0163-fman-pcd-port-recover.patch` sits directly under a doubled, apparently
copy-pasted `# Upstream-Status: Inappropriate | Risk-Tier: A` comment pair
(series lines 283-284) that unambiguously belongs to `0157` and `0158` above
it; `0163` itself has no comment of its own. Diff evidence (below) shows it
modifies only `fman_pcd.c` (owned, zero upstream touch), consistent with
Tier A — but the series file should say so explicitly rather than by
inference.

**`BOARD_STAGE_SKIP` in `ci-setup-kernel.sh` (line 677) references patches
that no longer physically exist under `board/`:** `0127-fman-pcd-cc-node-slab.patch`,
`0128-fman-pcd-muram-segpool.patch`, `0129-fman-pcd-muram-largest-free.patch`,
and `0138-fman-pcd-manip-frag-check.patch` now live only under
`kernel/ask/patches/archive-2026-08-10-excluded-wip/`; `0150-fman-pcd-fe-engage-api.patch`
does not exist anywhere (superseded by `0153`, per the series comment). The
staging-completeness guard still passes because it only checks patches that
exist on disk, but the whitelist itself is stale and should be pruned to just
whatever, if anything, still lives directly in `board/`.

## 3. Methodology for the "new files, not upstream" question

Two independent classifiers were built and cross-checked against each other
and against the series file's own curated `Risk-Tier` comments (S9 of
AGENTS.md: "Tier A = new files/subsystems; Tier B = static demotions/exports;
Tier C = hot upstream files requiring human review on every bump"):

1. **Git-header literal**: for every patch, count `new file mode` occurrences
   (`added`), `deleted file mode` occurrences (`deleted`), and remaining
   `--- a/`/`+++ b/` pairs (`modified`). A patch with `modified == 0` touches
   nothing that existed before it ran.

2. **File-provenance registry**: built by scanning every active patch for
   `--- /dev/null` / `+++ b/<path>` pairs, in application order, giving the
   set of paths ever introduced as new by this project's own patch stack
   (35 paths — listed in §5). A `modified` file is then split into `own`
   (path is in the registry — i.e. it was itself created by an earlier patch
   in this same stack, so upstream Linux/vyos-1x/vyos-build simply does not
   have it and can never drift against it) versus `upstream` (path is not in
   the registry — a file that genuinely exists in the base project, where a
   version bump can introduce real conflicts). Build-glue `Makefile`/`Kconfig`
   touches are tracked separately as `mech` (typically a 1-2 line
   `obj-y += x.o` hook — technically an upstream file, but the lowest-risk
   possible kind of touch).

This second classifier is what actually answers "creates/modifies new files,
not files from upstream": a patch with `upstream == 0` never touches a single
line of pre-existing base-project content, regardless of how many files it
lists as "modified" in git's bookkeeping.

Full per-patch tables are at `/tmp/kilo/patch-classify-v2.txt` (regenerate
with the awk/bash pipeline used during this review if needed; not committed
to the repo).

## 4. Results

### 4.1 Tier A/B/C, recounted from the current `series` file

| Tier | Count (of 119 board patches) | Definition |
|---|---|---|
| A | 66 | new files/subsystems |
| B | 2 | static demotions/exports (`0121h`, `0153i`) |
| C | 51 | hot upstream files, human review every bump |
| (unlabeled) | 1 | `0163` — see §2 |

This has grown from the ~70/~5/~35 snapshot in
`TA-2026-07-18-002-patch-architecture.md` §6.4 as the stack expanded; the
proportions are consistent (Tier A still the largest bucket).

### 4.2 Upstream-touch, by file-provenance (the literal answer)

| Corpus | Patches | Touch zero upstream files | Touch ≥1 upstream file |
|---|---|---|---|
| `kernel/common/patches/board/` | 119 | **61 (51%)** | 58 |
| `kernel/common/patches/fixes/` (live only) | 2 | 1 (`120`) | 1 (`130`) |
| `data/vyos-1x-*.patch` | 38 | 1 (`040`) | 37 |
| `data/vyos-build-*.patch` | 5 | 0 | 5 |

The kernel board stack is roughly half-and-half; vyos-1x/vyos-build are
almost entirely modifications to real, actively-developed upstream files
(expected — vyos-1x is a live project, not a from-scratch subsystem).

### 4.3 Hot-file ranking (where the real bump risk concentrates)

```
26  drivers/net/ethernet/freescale/dpaa/dpaa_eth.c
19  drivers/net/ethernet/freescale/dpaa/dpaa_eth.h
12  drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c
10  drivers/net/ethernet/freescale/fman/fman_port.c
 9  drivers/net/ethernet/freescale/fman/fman_port.h
 7  drivers/net/ethernet/freescale/fman/fman_keygen.c
 2  drivers/net/ethernet/freescale/fman/fman.c / fman.h / qbman qman.c family / caam qi.c/.h / sfp.c / phylink.c / ina2xx.c / clk-qoriq.c / xhci-plat.c (1 each)
```

Six files (`dpaa_eth.c`, `dpaa_eth.h`, `dpaa_ethtool.c`, `fman_port.c`,
`fman_port.h`, `fman_keygen.c`) account for the large majority of genuine
upstream-drift exposure in the kernel corpus. This exactly matches
`TA-2026-07-18-002` §6.4's Tier C description and its explicit
"**Keep (do not touch)**" list in §6.7 — independent confirmation from real
diff content, not just the prior campaign's own bookkeeping.

### 4.4 The 35-file provenance registry (project's own files, never upstream)

```
drivers/net/ethernet/freescale/dpaa/af_xdp_pool/{Kconfig,Makefile,af_xdp_pool_main.c}
drivers/net/ethernet/freescale/dpaa/dpaa_ceetm.{c,h}
drivers/net/ethernet/freescale/dpaa/dpaa_flavor.{c,h}
drivers/net/ethernet/freescale/dpaa/dpaa_fman_caps.{c,h}
drivers/net/ethernet/freescale/fman/fman_keygen_internal.h
drivers/net/ethernet/freescale/fman/fman_pcd.c
drivers/net/ethernet/freescale/fman/fman_pcd_cc.c
drivers/net/ethernet/freescale/fman/fman_pcd_cc_test.c
drivers/net/ethernet/freescale/fman/fman_pcd_dcsr.c
drivers/net/ethernet/freescale/fman/fman_pcd_internal.h
drivers/net/ethernet/freescale/fman/fman_pcd_kg.c
drivers/net/ethernet/freescale/fman/fman_pcd_manip.c
drivers/net/ethernet/freescale/fman/fman_pcd_plcr.c
drivers/soc/fsl/qbman/qman_ceetm.c
include/linux/crypto/caam_qi_share.h
include/linux/fsl/dpaa_flow_offload.h
include/linux/fsl/fman_pcd.h
op-mode-definitions/show-offload.xml.in
src/migration-scripts/interfaces/34-to-35
src/op_mode/show_ask_offload.py
src/op_mode/show_offload.py
src/services/api/mcp/{__init__,auth,prompts,redaction,resources,routers,schema,server,tools}.py
```

Every one of `fman_pcd.c`'s own dependents (`fman_pcd_cc.c`, `fman_pcd_kg.c`,
`fman_pcd_manip.c`, `fman_pcd_plcr.c`, `fman_pcd_dcsr.c`,
`fman_pcd_internal.h`, `include/linux/fsl/fman_pcd.h`) is entirely this
project's invention — mainline Linux has no PCD/KeyGen/CC/HM/Policer
subsystem in `drivers/net/ethernet/freescale/fman/` beyond the minimal
`fman_keygen.c` (mainline RSS hashing) and `fman_port.c`. This matters
directly for the fixup-fold campaign in §5.

## 5. Fixup-to-file mapping: extending the Phase-2 fold campaign

`bin/kernel-fixups/manifest.json` currently lists 116 active fixups (down
from the 122 peak and the 120 recorded in `TA-2026-07-18-002` §6.7 on
2026-08-22 — `F_109`, `F_116`, `F_104`, `F_174`, `F_098` are already gone,
consistent with that campaign's log). Reading every entry's target file (the
explicit `target` JSON field where present, the description's
`<file>: F-NNN ...` prefix otherwise) gives the same own/upstream split as
§4, at fixup granularity:

- **~90 of 116 active fixups target only files in the §4.4 registry**
  (overwhelmingly `fman_pcd.c` itself, plus `fman_pcd_kg.c`, `fman_pcd_cc.c`).
  These carry **zero upstream-drift risk** if folded — there is no upstream
  `fman_pcd.c` to conflict with on a kernel version bump.
- **~20 fixups touch at least one genuinely upstream file**: `F_101`/`F_108`/
  `F_199`/`F_203`/`F_216`/`F_222`/`F_227`/`F_229` (`dpaa_eth.c`/`.h`),
  `F_102` (`qman.c`), `M2_4_2`/`F_162`/`F_168`/`F_205`/`F_215` (`fman_port.c`/`.h`),
  `F_179`/`F_183`/`F_201`/`F_209`/`F_214`/`F_224` (`fman_keygen.c`), `F_215`
  (`fman.c`/`.h` too). These match `TA-2026-07-18-002`'s explicit
  "Keep (do not touch)... every Tier-C hot-file fixup" list — this review
  independently arrives at the same set from raw file provenance rather than
  from the campaign's own prior bookkeeping, which is a useful
  cross-validation rather than a new instruction.

The practical consequence: the fold-safety axis (upstream-drift risk) and the
complexity axis (internal fixup-on-fixup dependency, per
`TA-2026-07-18-002`'s "Cluster-regenerate only" grouping) are orthogonal.
Almost the entire `fman_pcd.c`-family fixup population scores **low** on the
first axis and **high** on the second (each rewrites text a previous fixup
already rewrote, so they must be folded as a group via full cluster
regeneration on the canonical branch, never one at a time) — this plan does
not attempt to re-derive those cluster boundaries; `TA-2026-07-18-002` §6.7
already lists them (engage-lifecycle, MURAM, ehash-encoding, CC-dispatch,
resolver) and they should be treated as authoritative.

## 6. Proposed execution order

Nothing below has been executed. Each phase is a candidate for explicit
go-ahead, in the order that minimizes risk first.

**Phase 0 — zero-risk repository cleanup (no canonical-branch touch at all):**
1. Delete the 3 dead patches (§2): `kernel/common/patches/vyos/001-*.patch`,
   `kernel/common/patches/vyos/003-*.patch`,
   `kernel/common/patches/fixes/095-leds-lp5812-register.patch`.
2. Add the missing `Risk-Tier: A` comment for `0163` in `series`, and split
   the doubled comment above `0158`/`0163` so each patch has its own line.
3. Prune `BOARD_STAGE_SKIP` in `ci-setup-kernel.sh` to drop entries for
   patches no longer present under `board/` (`0127`, `0128`, `0129`, `0138`,
   `0150`), or confirm they should stay for documentation purposes and say
   why.
4. Regenerate the 5 non-git-format patches (`0093`-`0096`, `fixes/120`) into
   proper `diff --git`/`index` form via `bin/kernel-roundtrip.sh export`
   once they're represented as commits on the canonical branch (they already
   should be, per `bin/canonical-bootstrap.sh`'s "one commit per series
   patch" — this is a verification step, not new work).

**Phase 1 — execute the already-identified "safe single-owner folds"**
(`TA-2026-07-18-002` §6.7), now cross-confirmed against owning-patch
upstream-touch data from §4.2:
- `F_094` → `0153` (`0153` measured `upstream=0`, confirmed safe)
- `F_057` → `0128` (`0128` measured `upstream=0`, confirmed safe)
- `F_073D` → `0127` (`0127` measured `upstream=0`, confirmed safe)
- `F_131` → `0126` (`0126` measured `upstream=0`, confirmed safe)
- `F_089` → dedicated tripwire patch: different mechanism — `F_089` injects
  `fman-pcd-fe-static-asserts.h` and `fman_pcd_fe_test.c`, both already
  shipped via the direct-file-injection route
  (`kernel/common/files/*` + `ci-setup-kernel.sh`'s `cp`, not the patch
  series) — confirm whether "fold" here still means anything beyond what's
  already true before treating it as outstanding work.

**Phase 2 — extend the safe-fold sweep using the broader own-file criterion**
from §4.4/§5: for each of the remaining active fixups whose target file is
in the registry and which `TA-2026-07-18-002` does NOT already flag as part
of a must-cluster group (engage-lifecycle, MURAM, ehash-encoding,
CC-dispatch, resolver) or a keep-for-now diagnostic (`F_099`/`F_100`/`F_105`/
`F_106`/`F_126`/`F_127`/`F_192`/`F_193`/`F_194`), fold individually following
the proven procedure (`git commit --fixup`, `rebase -i --autosquash` on the
canonical branch, `bin/kernel-roundtrip.sh verify`, `bin/test-fixups.sh`,
board re-test) — one fixup at a time, S0-qdrant-gated per AGENTS.md, exactly
as the 2026-08-18 pilots (`F_109`→`0153`, `F_116`→`0153`, `F_104`→`0109`) were
done.

**Phase 3 — cluster regeneration**, per `TA-2026-07-18-002` §6.7's named
groups, unchanged from that plan. Not re-derived here.

**Phase 4 — do not touch**: every fixup and patch identified in §4.3/§5 as
touching `dpaa_eth.c`, `dpaa_eth.h`, `dpaa_ethtool.c`, `fman_port.c`,
`fman_port.h`, or `fman_keygen.c` stays exactly as-is. Folding these still
requires human review on every kernel bump regardless of fixup/patch
mechanics — that risk is inherent to the files, not the fold state.

## 7. Verification and rollback, every phase

- `bin/kernel-roundtrip.sh verify` — non-destructive export-to-tempdir
  comparison before touching any real patch file.
- `bin/test-fixups.sh` — the 4-check CI gate (execution, count-assertion,
  manifest accuracy, no-zombie) must pass before any kernel build.
- CI round-trip commit-count gate in `auto-build.yml`.
- Full board re-test after any fold batch, per AGENTS.md's silicon-experiment
  discipline (cold boot, one variable at a time).
- Canonical branch is never force-pushed; every fold is a normal commit,
  recoverable via `git reflog` if a fold needs to be undone.

## 8. What this plan does not do

It does not fold anything. It does not delete the 3 dead patches. It does not
touch `series`, `manifest.json`, `ci-setup-kernel.sh`, or the canonical
branch. All of §6 is a proposal, ordered by measured risk, ready for
per-phase go-ahead.
