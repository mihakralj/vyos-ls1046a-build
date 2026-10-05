# Ehash Dual-Fix Verification Plan (2026-08-07)

## Problem statement

Two orthogonal, independently-confirmed defects are blocking a genuine FE-VM
ehash HIT on `.185`:

1. **CC-dispatch-layer negative** — the `CONT_LOOKUP` group-AD wrapper never
   hands off into the FE-VM chain. Confirmed three separate times by three
   different methodologies: F-157/F-158 (2026-08-01, dedicated-TX-FQ
   discriminator + byte-perfect `fe_scaffold` dump), T-M3-R attempt 5
   (2026-08-06, distinct-FQID discriminator), and this session's F-175 retest
   (2026-08-07). The only topology that has EVER produced a real HIT is
   `RCCB→FE_ENTER` direct (2026-07-04) — and per F-165 (already committed),
   `fman_pcd_fe_engage()` already supports switching between the two
   topologies at **runtime**, via whether `fe_enter_off` is zero or not. No
   kernel change is needed for this half of the fix — only correct debugfs
   usage.

2. **Ehash bucket/record DDR format — RESOLVED, not a defect (Phase 0
   result, 2026-08-07).** A first pass through `we-are-mono/ASK` misread the
   wrong vendor function family (`ext_hash_add_key()`/
   `t_FmPcdCcNodeExtHashInfo`, reachable only via `FM_PCD_HashTableSet()`,
   never called by `cdx_ehash.c`) and produced an incorrect "16× stride
   mismatch" verdict. Re-checked against the function `cdx_ehash.c` actually
   calls (`ExternalHashTableAddKey()`, operating on `en_exthash_bucket`/
   `en_exthash_node`/`en_ehash_entry`): this project's existing 16-byte
   bucket, `en_exthash_node` 4-word DDR descriptor encoding, and flow-record
   header are **bit-exact correct**, field by field. No rewrite needed. Full
   correction in `arch/fman-microcode-210-programming-reference.md` §10 and
   qdrant (tag `Phase-0-corrected`).
   
   One genuinely new, still-valid capability surfaced from the *correct*
   struct family: `en_ehash_entry` is a union whose second view exposes
   hardware-writeback `packet_count`/`packet_bytes`/`timestamp` counters
   (entries allocated at 320 bytes instead of 256, gated by
   `SET_STATS_ENABLE`/`SET_TIMESTAMP_ENABLE` flag bits) — a
   dispatch-independent "did hardware even compare this entry" signal, not
   present in this project today. Worth adding as a diagnostic instrument,
   but it is not a fix for a confirmed defect, because no defect was found
   here.

**Net effect**: only defect #1 (CC-dispatch-layer) is confirmed. The
persistent MISS remains unexplained at the structural DDR-format level.
Phase 1 below is scoped accordingly — add the stats/timestamp readback as an
investigative instrument, then let Phase 3's board session (via the
already-working direct-FE_ENTER topology) tell us whether the compare stage
works at all, which is now the open question.

## Guiding principle: minimize GitHub Actions CI runs (the only compile path)

**Standing constraint (2026-08-07): the local dev-loop (`bin/dev-build.sh
kernel`, `bin/local-build.sh ask-mod`, `dev_boot_live`) is NOT to be used for
compiling. GitHub Actions CI is the only accepted way to build kernel code
on this project.** This supersedes the earlier draft of this plan, which
proposed the local loop.

Given that constraint, "least CI rebuilds" means something more disciplined
than iterate-fast-locally-then-build-once: it means **get everything right
in as few CI builds as possible up front**, since every wrong guess costs a
full pipeline run (this session's F-175 cycle burned ~5 CI builds on anchor
mismatches and one avoidable design error). The levers available without
compiling anything:

- `python3 bin/kernel-fixups/mutate.py --check ...` — pure text/anchor
  validation against the local, already-patched `work/linux-6.18.34` git
  tree. This does **not** compile anything (no `gcc`, no `dev-build.sh`) —
  it only confirms a fixup's anchor string exists exactly where expected
  before it's ever applied for real. Using this to catch anchor drift before
  every CI submission is not a build step and stays in scope.
- Reading the actual current state of `work/linux-6.18.34` (which fixups
  have already mutated which lines) to write new anchors against reality
  instead of against a stale assumption — the single biggest source of this
  session's wasted CI runs.
- Reading the vendor source fully (Phase 0) before writing any code, so the
  design itself doesn't need a CI cycle to discover it's wrong.

Target: **one** CI build carrying every capability Phase 3's full test
matrix needs (the fix itself, the new stats-readback debugfs node, and
confirmation that EKFC arm + the `fe_arm` RCCB pointer are both already
runtime-selectable — no rebuild needed to switch topology or bucket format
during board testing). A second CI build is an accepted fallback only if
Phase 3 surfaces a genuine missing capability that Phase 1 didn't
anticipate — not a routine expectation.

## Phase 0 — Reconcile the bucket-format ambiguity (no build, no board)

Pure source-reading. Goal: get a definitive verdict on whether our existing
16B-bucket/LIFO-linked-256B-record design is silicon-compatible with
package-210 microcode, or needs the 256B set-associative rewrite.

0.1. Read the **full** body of `ExternalHashTableAddKey()` /
    `ext_hash_add_key()` / `ext_hash_lookup()` in `we-are-mono/ASK`'s
    `fm_cc.c` (mono-patched or mt-6.12.y branch — the kernel-6.12 family,
    not the stale LSDK 5.4 `999.patch`). Confirm definitively: does the
    *live* insert/lookup path write/walk `t_FmExtHashBucket.key_result[]`
    (set-associative), or does it thread `en_ehash_entry.next_entry`
    (LIFO-linked, matching our current design)? These may not be
    contradictory — `t_FmExtHashBucket` could be a bucket-pool allocator
    that still hangs `en_ehash_entry`-shaped records off it. Read enough of
    both structures' actual field usage in the same function to settle this,
    not just their declarations.

0.2. Cross-check against `arch/fman-microcode-210-programming-reference.md`
    §7.2 EXT_HASH FE `w0` — the doc already flags `aging | stats` bits exist
    in `w0` but doesn't give exact bit positions. Find them (either from the
    same `fm_ehash.h`/`fm_cc.h` headers already pulled, or by pattern-
    matching against the `en_exthash_node` word0 encoding the earlier
    GitHub agent partially decoded).

0.3. Write the verdict back into `arch/fman-microcode-210-programming-
    reference.md` §10.2a (supersede, don't delete, per this project's
    existing convention) and a qdrant entry. Two possible outcomes gate
    Phase 1's scope:

    - **Verdict A (surgical)**: our flat-record design is a valid subset/
      configuration of the same mechanism; only the missing
      `SET_STATS_ENABLE`/`SET_TIMESTAMP_ENABLE` flag bits (and reading back
      the resulting counters) need adding. Phase 1 becomes a small, low-risk
      change.
    - **Verdict B (structural)**: the real silicon requires the
      set-associative `t_FmExtHashBucket` layout and our design is
      fundamentally incompatible. Phase 1 becomes a real rewrite of
      `fman_pcd_ehash_add_key()`/the bucket allocator.

    **Checkpoint**: do not proceed past Phase 0 without this verdict written
    down. Guessing wrong here is exactly what cost F-144/F-150 (documented
    process-failure entries, 2026-07-31) — the qdrant gate rule this project
    already adopted for exactly this reason.

**RESULT (2026-08-07, corrected same day)**: initial Phase 0 work read the
wrong vendor function family (`ext_hash_add_key()`/`t_FmPcdCcNodeExtHashInfo`
— reachable only via `FM_PCD_HashTableSet()`, never called by `cdx_ehash.c`)
and produced an incorrect "16× stride mismatch" verdict. Re-checking against
the function `cdx_ehash.c` actually calls (`ExternalHashTableAddKey()`,
operating on `en_exthash_bucket`/`en_exthash_node`/`en_ehash_entry`) found
this project's bucket format is **bit-exact correct** — no rewrite needed.
See `arch/fman-microcode-210-programming-reference.md` §10 for the full
corrected verdict and qdrant (tag `Phase-0-corrected`) for the retraction.
**Verdict A applies, but narrower than originally framed: no fix is needed
at all, only the stats/timestamp readback addition (below), now recast as
an investigative instrument rather than a bug fix.** The persistent MISS
remains unexplained at the structural level — Phase 1 no longer carries a
confirmed defect to fix, only a new diagnostic to add.

## Phase 1 — Design + static verification (no build, no board)

1.1. No bucket/record format fix is needed (Phase 0 result). Write a single
    fixup adding a debugfs readback node exposing the hardware-writeback
    stats fields — `packet_count`(8B)/`packet_bytes`(8B)/`timestamp`(4B) per
    `en_ehash_entry`'s second union view, entries allocated at 320 bytes
    (not 256) when stats-enable is requested, gated by `SET_STATS_ENABLE`
    (bit 12)/`SET_TIMESTAMP_ENABLE` (bit 13) on the entry's `flags` word.
    This is the single most valuable addition in this whole plan: it is a
    **dispatch/FQID-independent** discriminator — "did hardware even attempt
    a compare at this entry" — something no previous test in this project's
    history has had. It answers, in one board session, whether the problem
    is the compare stage or the dispatch stage (topology/ENQ), instead of
    continuing to infer this indirectly from FQID delivery.

1.2. Validate every fixup's anchor against the current state of
    `work/linux-6.18.34` with `python3 bin/kernel-fixups/mutate.py --check
    ...` (or the fixup script's own dry-run path, as F-175 has). This is
    text-only anchor validation, not a compile — it does not use
    `dev-build.sh` or invoke `gcc` — and it catches the exact anchor-drift
    failure class that cost this session ~4 CI cycles on F-175 alone, before
    a single CI run is spent.

1.3. Read through every changed function once more by hand against the
    Phase 0 verdict and the microcode-210 reference's byte layouts (§7.2,
    §10.2a/§10.4 as corrected). No compiler is available before CI, so this
    manual pass is the only check standing between a design mistake and a
    burned CI run — treat it accordingly.

    **Checkpoint**: every fixup's anchor validated against the real current
    tree state, every byte-layout claim cross-checked against the doc, before
    moving to Phase 2.

## Phase 2 — One CI build bundling everything Phase 3 needs

Wire the Phase 1 fixup(s) into `ci-setup-kernel.sh`/`manifest.json` exactly
as every prior fixup in this project has been (F-172 through F-175, etc.) —
there is no separate "local-only" staging step now; CI wiring *is* the next
step after Phase 1. Before triggering the run, confirm the fixup set
contains **every debugfs knob Phase 3's test matrix needs**, so Phase 3 can
run entirely inside one boot session with zero further CI builds in between:

- `fe_arm engage <port> <off> <fqid>` — already generic (F-165), no change
  needed; used to flip between direct-FE_ENTER and group-AD-wrapped without
  rebuilding.
- EKFC arm at `0x801C0006` (14-byte PORT_ID-prefixed key) reachable via
  whatever debugfs/scheme-arm path is already used for `cc_test`/`fe_group`
  — confirm this is a runtime parameter, not compiled in, before triggering
  CI.
- The new stats-enable insert flag and stats-readback node from Phase 1.1.
- A debugfs readback of the *inserted* bucket/entry raw bytes at both insert
  time and after a test frame (Phase 3.2 below needs this to distinguish
  "wrong bucket index" from "right bucket, compare still failed").

**Checkpoint**: written test plan for Phase 3 (below) with each step mapped
to an already-existing debugfs command — if any step requires a debugfs
capability not yet built, add it now, before triggering CI, not mid-Phase-3
(that would force the fallback second build this plan is trying to avoid).
Trigger the one CI/ISO build, deploy to lxc200 per the existing convention.

## Phase 3 — Single board session, multiple sub-tests, zero rebuilds

Push the Phase 2 build to TFTP, `run dev_boot_live` on `.185` **(explicit
go-ahead required before this step and before every `fe_arm engage`/flow-
insert write, per standing session discipline)**. All of the following run
against the *same* boot — no rebuild between them:

- **3.1 — Compare-stage sanity, current topology.** Arm `RCCB→FE_ENTER`
  direct (per F-165, `fe_enter_off != 0`), EKFC `0x801C0006`, corrected
  14-byte PORT_ID key, insert one flow with the new stats-enable flag set.
  Send one genuinely-matching frame. Read back the stats counter.
  - **PASS** (counter increments): hardware performed a real compare at this
    entry — confirms the bucket/record format (already known bit-exact
    correct per Phase 0) really is being read correctly by hardware, and the
    remaining problem is purely dispatch/ENQ (already the most-tested part
    of this project — do not re-litigate F-175's ENQ work, focus next effort
    on §7.3's dispatch layer instead).
  - **FAIL** (counter stays zero): the compare itself never happens even
    through the one topology that's ever produced a HIT, despite a
    bit-exact-correct format on paper — proceed to 3.2 to localize why.

- **3.2 — Bucket/index sanity check** (only if 3.1 failed). Read back the
  raw bucket bytes (via the Phase 2 debugfs dump) at the exact index the
  driver computed for the inserted key, both right after insert and after
  the test frame. Confirm: (a) the bucket head pointer the driver wrote is
  still there — rules out something else overwriting it between insert and
  test; (b) independently recompute the CRC-64/bucket-index by hand from the
  same key bytes and hash_shift/hash_mask values armed, to catch a
  transcription bug in the *live* arm (not the design, which Phase 0 already
  checked) — e.g. EKFC not actually taking effect, or `hash_shift`/
  `hash_mask` fields programmed differently than intended for this specific
  arm. This isolates a live-arm bug from a genuine hardware/microcode
  behavior gap, without assuming a format rewrite is needed.

- **3.3 — Dispatch-layer cross-check, group-AD wrapper.** Re-run 3.1's exact
  flow/key/stats-enable setup but through the `CONT_LOOKUP` group-AD
  topology (already known non-discriminating on FQID grounds). Read the
  stats counter here too.
  - If the counter increments here despite FQID delivery still being
    wrong, that's new information: the compare stage works fine even
    through the group-AD wrapper, and the bug is *purely* dispatch/ENQ,
    strengthening the case to abandon the wrapper topology outright in favor
    of direct FE_ENTER for any production path.
  - If it doesn't increment here either, that's consistent with F-157/158's
    original finding (CC never reaches the FE-VM chain at all through this
    topology) and rules out re-investigating the wrapper further.

- **3.4 — End-to-end HIT/MISS FQID discrimination**, only attempted once
  3.1 (or 3.2) shows the compare stage genuinely working: distinct HIT vs
  MISS FQIDs (per F-157's dedicated-TX-FQ technique), matching vs
  non-matching traffic, confirm frames land on the correct, *different*
  FQIDs. This is the actual acceptance test for "genuine HIT achieved."

**Checkpoint / stop conditions**: if 3.1 fails and 3.2's byte-level check
finds nothing wrong (bucket index correct, head pointer intact, key bytes
match), stop — that combination means the design is right, the live arm is
right, and hardware still isn't comparing. That's a genuine, deeper gap
(microcode behavior, workspace allocation, or something not yet modeled at
all) and needs a fresh root-cause pass, not another guess at this plan's
scope.

## Phase 4 — Close-out (only if Phase 3 fails to fully pass)

Phase 2's CI build is already the deployable artifact — there is no separate
"final build" step when Phase 3 passes cleanly. Phase 4 exists only as the
fallback: if Phase 3 uncovers a genuine missing capability Phase 1 didn't
anticipate, write the follow-up fixup, repeat Phase 1's static checks, and
trigger a second CI build. Every such fallback build should be logged in
qdrant with what was missed and why, so the next plan front-loads it.

Once Phase 3.4 passes (whether on the first or a fallback CI build): update
`arch/fman-fe-ehash.md`, the microcode-210 reference, and qdrant with the
final, confirmed mechanism — closing out task #26.

## Explicit gates carried over from standing session discipline

- No `add system image` / `install image` on any board — `dev_boot_live` is
  the only board-boot mechanism this plan uses, and even that requires
  explicit go-ahead each time, same as any board-state-changing step.
- No `fe_arm engage` or flow-insert write without a fresh per-attempt
  go-ahead.
- No disengage/teardown of an armed chain without explicit permission.
- Findings — positive or negative — get written to qdrant and the relevant
  arch docs at each checkpoint, not just at the end.
