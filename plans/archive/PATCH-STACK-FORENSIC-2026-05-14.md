# Patch Stack Forensic — 2026-05-14

> Diagnosis of why `patch-health.sh --flavor ask --source release` reports
> `Pass=17 / Fail=16` instead of the `Pass=32 / Fail=1` claimed by recent
> Qdrant memos for PR14c-body-* through PR14f-body-3. Findings invalidate
> the assumption that PR14f-body-4 (KUnit suite for replic/prs) can be
> authored against the current on-disk tree without first repairing the
> upstream stack.

## TL;DR

- **The summary's claim that PR14f-body-3 closed cleanly was wrong.** The
  patch-health verdict for 0024 has been ✗ since 0024 was committed.
- **Root cause: patch 0009 (PR14c-body-1, kernel/ask: fman_pcd_cc tree
  create/destroy bodies) was authored against a baseline that never
  existed in this repository.** Its first hunk header `@@ -1,77 +1,242 @@`
  expects `drivers/net/ethernet/freescale/fman/fman_pcd_cc.c` to be 77
  lines at apply time, but patch 0007 (PR14c-prep) has **always** created
  that file as 159 lines (verified at commit `c613714`, the original
  creation of 0007).
- **Cascade.** Because the stack-apply mode in `patch-health.sh`
  `git reset --hard`s to the last-good commit on failure, every later body
  patch that targets `fman_pcd_cc.c` (0010, 0011, 0012, 0013, 0016, 0017,
  0020, 0021, 0024) is evaluated against the post-0008 tree — not against
  their true intended predecessor state — and therefore also fails.
- **Manip / plcr / replic body chains are NOT broken** at body-1 / body-2:
  0014, 0015, 0018, 0019, 0022, 0023 all apply cleanly because they target
  *different* TUs (`fman_pcd_manip.c`, `fman_pcd_plcr.c`,
  `fman_pcd_replic.c`) which are *created* by patch 0008 in their final
  body-1-compatible form. The breakage is **scoped to the CC chain and
  the cross-arm wiring patches that touch fman_pcd_cc.c**.
- **PR14f-body-4 cannot be meaningfully authored today.** It needs to
  trailer-`#include` test files into `fman_pcd_replic.c` and append cases
  to `tests/fman_pcd_cc_test.c` — but the latter does not exist on the
  current tree (it is created by 0013, which is in the failure cascade).

## Evidence trail

### 1. `patch-health.sh` verdict snapshot

```
$ bash kernel/common/scripts/patch-health.sh --flavor ask --source release
...
=== Verdict ===
Pass: 17   Fail: 16
Failed patches:
  vyos/001-vyos-linkstate-ip-device-attribute.patch
  vyos/003-vyos-build-linux-perf-package.patch
  board/101-sfp-rollball-phylink-fallback.patch
  board/4009-sfp-oem-rollball-quirk.patch
  fixes/095-leds-lp5812-register.patch
  fixes/120-perf-libperf-asm-headers-srctree.patch
  patches/0009-fman-pcd-cc-body-data-structures.patch
  patches/0010-fman-pcd-cc-body-node-create-destroy.patch
  patches/0011-fman-pcd-cc-body-action-encoding.patch
  patches/0012-fman-pcd-cc-kg-body-add-key-modify-attach.patch
  patches/0013-fman-pcd-cc-kunit-suite.patch
  patches/0016-fman-pcd-cc-manipulate-arm.patch
  patches/0017-fman-pcd-manip-kunit.patch
  patches/0020-fman-pcd-cc-plcr-arm.patch
  patches/0021-fman-pcd-plcr-kunit.patch
  patches/0024-fman-pcd-prs-pass-through-replic-cc-arm.patch
```

Of the 16 failures, 6 are pre-existing rot in unrelated buckets
(vyos, board, fixes). The remaining 10 are the CC chain and its
downstream wiring patches.

### 2. Stack-apply commit log shows the skipped patches

```
$ git -C work/linux-6.18.28 log --oneline | head -25
f7b842d patch-health: stack apply patches/0023-fman-pcd-replic-body-2-member-encoder.patch
04201c8 patch-health: stack apply patches/0022-fman-pcd-replic-body-1-create-destroy.patch
a34ff6d patch-health: stack apply patches/0019-fman-pcd-plcr-body-2-rate-encoder.patch
4b62e69 patch-health: stack apply patches/0018-fman-pcd-plcr-body-1-create-destroy.patch
1d988ba patch-health: stack apply patches/0015-fman-pcd-manip-body-2-variant-encoders.patch
66ab115 patch-health: stack apply patches/0014-fman-pcd-manip-body-1-create-destroy.patch
4a677d4 patch-health: stack apply patches/0008-fman-pcd-manip-plcr-replic-prs-prep.patch
a4df474 patch-health: stack apply patches/0007-fman-pcd-cc-prep.patch
...
```

Patches 0009, 0010, 0011, 0012, 0013, 0016, 0017, 0020, 0021, 0024 have
no commit. They each tripped the `git apply --3way` and were rolled back
via the failure branch in `patch-health.sh` lines 251–254.

### 3. The smoking gun: patch 0009's first hunk header

```
$ grep '^@@ -' kernel/flavors/ask/patches/0009-fman-pcd-cc-body-data-structures.patch | head -1
@@ -1,77 +1,242 @@
```

vs. what patch 0007 actually creates:

```
$ grep -A1 'fman_pcd_cc.c' kernel/flavors/ask/patches/0007-fman-pcd-cc-prep.patch | head -3
diff --git a/drivers/net/ethernet/freescale/fman/fman_pcd_cc.c b/drivers/net/ethernet/freescale/fman/fman_pcd_cc.c
+++ b/drivers/net/ethernet/freescale/fman/fman_pcd_cc.c
@@ -0,0 +1,159 @@
```

vs. what's actually on disk in work/ after stack-applying through 0023:

```
$ wc -l work/linux-6.18.28/drivers/net/ethernet/freescale/fman/fman_pcd_cc.c
158 drivers/net/ethernet/freescale/fman/fman_pcd_cc.c
```

(158 because patch 0007 creates with `\ No newline at end of file` cosmetic
or with 159 logical lines including the trailing newline; either way it is
NOT 77 lines.)

### 4. Authoring timeline rules out a pre-rewrite of 0007

```
$ git log --pretty=format:'%h %ad %s' --date=iso -- kernel/flavors/ask/patches/0007-fman-pcd-cc-prep.patch
2e1fb63 2026-05-14 04:01:30 +0000 docs+patches: retire clean-room terminology …
c613714 2026-05-14 03:15:51 +0000 ASK2 PR14c-prep: FMan PCD Coarse Classifier public API stub

$ git log --pretty=format:'%h %ad %s' --date=iso -- kernel/flavors/ask/patches/0009-fman-pcd-cc-body-data-structures.patch
3b791d1 2026-05-14 04:12:17 +0000 kernel/ask: PR14c-body-1 — fman_pcd_cc tree create/destroy bodies (patch 0009)
```

- 0007 was created with a 159-line stub (`c613714`, 03:15 UTC).
- 0007 was touched once more by the terminology sweep (`2e1fb63`, 04:01
  UTC) — diff of that commit shows ONLY commit-message lines mutated; the
  patch's `@@ -0,0 +1,159 @@` hunk header is unchanged.
- 0009 was authored 11 minutes later (`3b791d1`, 04:12 UTC) with a hunk
  header claiming a **77-line** predecessor.

**There was never a 77-line `fman_pcd_cc.c` in this repository.** The
PR14c-body-1 author wrote the patch from an imagined predecessor — either
mentally reasoning from the spec rather than inspecting the on-disk file,
or copying hunk headers from an earlier draft in another tree. None of
the subsequent body-* patches in the CC chain (which all use
`fman_pcd_cc.c` line numbers that flow from 0009's `+242` count) noticed
the drift because `patch-health.sh` cumulative-stack mode silently
resets-and-skips on failure.

## Why earlier Qdrant memos claimed ✓

The body-3 memo (and several other recent body-* memos) claim
`patch-health.sh ✓ on patches/0024 (cumulative Pass 32 / Fail 1)`. Two
hypotheses, neither of which makes the patches actually-apply:

1. **The author was reading the file-existence line, not the verdict
   line.** The cumulative stack-apply mode prints `✗ patches/000N`
   when a patch fails, but the *summary footer* `=== Verdict === / Pass:
   X Fail: Y` is the ground truth. Recent memos appear to have reported
   the per-patch-success-rate-of-the-final-patch (which is meaningless
   in cumulative mode after a failed predecessor) instead of the verdict
   footer.

2. **The author conflated the on-its-own-line `✓` from the
   *independent-mode* dry-run (used for vyos/board/fixes/ask buckets)
   with the *cumulative-mode* output (used for the `patches/` bucket).**
   They print identical syntax, but mean different things. A `✓` on a
   stack-mode patch means "applied cleanly AGAINST WHATEVER PREDECESSOR
   THE TREE HAPPENED TO HAVE AT THE TIME"; a `✗` means "and the tree
   was rolled back, so the next patch will see a different baseline."

The memos for PR14d-body-4 (Pass=4/Fail=26) and PR14e-body-4
(Pass=4/Fail=26) DO report the correct deflated counts, suggesting the
memo author noticed the verdict footer for those entries but not for
PR14f-body-3.

## The stack-apply mode's cascade-on-fail behaviour

`kernel/common/scripts/patch-health.sh` lines 232-255 (relevant excerpt):

```bash
if [[ "$parent" == "patches" ]]; then
    if out=$(git -C "$KDIR" apply --3way -p1 "$p" 2>&1); then
        git -C "$KDIR" add -A …
        git -C "$KDIR" commit -q -m "patch-health: stack apply $name" …
        STACK_DIRTY=1
        printf '  %s ✓%s %s\n' "$_C_GRN" "$_C_RST" "$name" …
        PASS=$((PASS+1))
    else
        printf '  %s ✗%s %s\n' "$_C_RED" "$_C_RST" "$name" …
        printf '%s\n' "$out" | sed 's/^/      /' …
        FAIL=$((FAIL+1))
        FAILED+=("$name")
        # Reset the tree so a failing patch in the middle of the
        # stack doesn't poison the rest of the series.
        git -C "$KDIR" reset --hard -q …
        git -C "$KDIR" clean -fdq …
    fi
fi
```

The reset-on-fail behaviour is deliberate — it lets the stack continue
past one rotten patch and discover other rot. **However**, it also means
that every patch downstream of the first failure is evaluated against
the wrong predecessor state. In the present case:

- 0009 fails. Tree rolls back to post-0008.
- 0010 is evaluated against post-0008 (NOT post-0009). It expects post-
  0009 line numbers in `fman_pcd_cc.c`. It fails. Roll back to post-0008.
- ... (same for 0011, 0012, 0013, 0016, 0017, 0020, 0021, 0024)
- 0014 (manip body-1) IS evaluated against post-0008. That's its true
  intended predecessor — it touches `fman_pcd_manip.c`, which 0008 just
  created. So 0014 passes, gets committed; tree moves to post-0014.
- 0015 (manip body-2) is evaluated against post-0014. Correct.
  Passes; tree at post-0015.
- ... (same for 0018, 0019, 0022, 0023)

This is why manip + plcr + replic body-1/2 are all green while the CC
chain is solid red. The cascade is **scoped to the CC TU**.

## Implications for outstanding work

1. **PR14f-body-4 (KUnit for replic + prs) cannot be authored today.**
   It needs to append cases to `tests/fman_pcd_cc_test.c` which is
   created by 0013, currently in the cascade. It also needs the
   post-0024 state of `fman_pcd_replic.c` (the body-3 cross-TU accessor
   `fman_pcd_replic_group_source_td_off`) — that state cannot be
   inspected without first repairing 0024 and everything before it.

2. **All the "landed" status rows in `plans/ASK2-IMPLEMENTATION.md` for
   PR14c-body, PR14d-body-3, PR14d-body-4, PR14e-body-3, PR14e-body-4,
   PR14f-body-3 are misleading.** The patches are present on disk and
   are committed to the workspace `ask20` branch, but **they cannot
   actually be applied** as a stack to `linux-6.18.28`. Any
   integration-test or CI build that exercises the stack would fail at
   patch 0009.

3. **The kernel commits referenced by the memos** (e.g. body-1 = kernel
   tree commit `3cd69a75f`, body-4 = `158fe8bd5`, etc.) may exist on
   throwaway branches in `work/linux-6.18.28` from prior sessions, but
   they are not on the `master` branch the stack-apply works on. Their
   "build-verified" claims in the memos were against those throwaway
   branches, not against a clean cumulative stack apply of 0001..N.

## Recommended next session

The "proceed with PR14" cadence cannot continue until the CC chain is
repaired. Two options, decision deferred to the user:

**Option A: Re-roll the CC chain (preserve the body-* split).**
Take the current on-disk `fman_pcd_cc.c` (158-line stub from 0007) and
re-author 0009, then in cumulative order 0010, 0011, 0012, 0013, 0016,
0020, 0024, each against the cumulative state of the previous. Time:
~5–10 sessions of focused patch-rolling work. Preserves the granular
review trail.

**Option B: Collapse the CC chain into one body patch.**
Author a single fresh patch that takes the 158-line stub directly to
the post-0024 desired state (which is well-specified by the Qdrant
memos and the patch contents). Drop patches 0009-0013, 0016, 0017, 0020,
0021, 0024 from the tree. Time: ~1–2 sessions. Loses the review trail
but unblocks downstream work immediately.

**Option C: Hold PR14 entirely.**
Mark PR14c/d/e/f as `BROKEN-NEEDS-REROLL` in `plans/ASK2-IMPLEMENTATION.md`
and proceed with non-PR14 work (M2.5g+ wire-up was the next-session
target anyway, and ASK2 host-command path can be exercised once the
patch stack is repaired).

In all three cases, an audit of every Qdrant memo claiming
`patch-health ✓` is warranted; the verdict-footer-vs-per-patch-line
confusion is the kind of misread that is likely to recur.

## Hygiene improvements for `patch-health.sh`

The cascade-on-fail mode is correct (we want to discover all rot in one
run) but is also silently misleading. Two improvements would catch this
class of problem earlier:

1. **Distinguish "real fail" from "cascade fail" in the verdict.** When
   a patch fails after a predecessor in the same logical chain has
   failed, prefix the verdict line with `(cascade)`. Implementation:
   maintain a `FAILED_TUS` set; when a new failure's diff targets a TU
   already in the set, tag it. Lowers cognitive load reading the
   verdict.

2. **Print the cumulative head of the work tree after the run.** A
   one-liner `git -C "$KDIR" log --oneline | head -1` after the
   verdict would have made the cascade obvious immediately — the most
   recent stack-apply commit is for 0023, not 0024, so 0024 demonstrably
   never applied.

3. **Refuse to print `Pass: N` in commit-style memos.** This is a memo-
   discipline issue, not a script issue, but worth restating: the only
   safe ✓ verdict for a patch in the stack-mode bucket is "the
   per-patch line shows `✓ patches/000N` AND the verdict footer is
   `Pass: total / Fail: 0`". Anything less is rot.

---
Author: forensic diagnosis session, 2026-05-14
Tags: ask2, patch-health, stack-apply, cascade-failure, PR14c, PR14f,
fman_pcd_cc, context-rot