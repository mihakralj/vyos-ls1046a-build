# CC-Tree Hardware-Offload Rebuild — Test & Implementation Plan

**Status:** Phase 0 code-prep done (2026-08-04): 0a confirmed patch 0107's match-row format is clean;
0a-2 found and fixed a second blocker (patch 0108 hardcoded the wrong branch's EKFC composite into
`cc_pack_key()`) via new fixup `bin/kernel-fixups/F_159.py`, validated with `bin/test-fixups.sh`. 0b's
ground-truth dump tooling is included in that same fixup. **0c partially executed on real hardware
(`.185`, 2026-08-04):** installed a real CC-tree key via `cc_test`, mmap-dumped the raw match-table
bytes, got a byte-exact match against the predicted (buggy) layout — confirms the harness mechanics and
the mmap-verification technique both work end-to-end on silicon. Fully reversible; board left clean.
**Board's running kernel predates F-159 (built 2026-08-01), so this only exercised the known-wrong
layout as a mechanics/negative-control test — the real HIT/MISS answer needs a new build+deploy with
F-159 included.** Deployment (VyOS `add system image`) is intentionally not something this agent
performs; that step is pending user action. Full result: qdrant tag `phase-0c-executed`.
**Context:** code review 2026-08-04 (see `plans/ASK2-MASTER-PLAN.md` top-of-doc 2026-08-04 banner,
`arch/fman-pcd.md`, `arch/software-stack-ask.md`) established that `ask.ko` currently has **no working
hardware-classification insert path**: CC-tree's insert plumbing was deleted from `ask.ko` (CR-007,
`dd364494`, 2026-07-27) before it ever reached hardware, and the remaining wired mechanism (FE-VM
ehash/Fork-B) is separately proven (F-156/F-157/F-158) to never dispatch a HIT. A same-day retraction
further found that **M5's 10.259 Gbps (2026-07-24) — the project's headline throughput number — most
likely measured pure kernel software forwarding, not hardware classification**: the CC-tree shadow
array was already software-only bookkeeping by M5's date (pre-existing "Fix C1" comment found in the
M5-era commit `9ad356a7`), and ehash was broken both before and after M5 per the F-141 saga. **The
project may never have silicon-confirmed a genuine hardware-classified HIT at production throughput, at
any point in its history.** Full evidence trail: qdrant `agent_memory`, tag `ask2-code-review`, entries
dated 2026-08-04 (including the `RETRACTION` / `no-confirmed-hw-hit-ever` entry).

**Ground rule (same convention as `plans/NXP-106-ORACLE-VALIDATION-PLAN.md`):** prefer read-only /
local-build work by default. Any step that writes to live board PCD/MURAM state, flashes an image, or
otherwise mutates shared board state requires explicit user confirmation before executing, even within
an already-approved phase. Every phase below is annotated with a risk level.

**Why phased and gated, not a single push to Phase 4:** this project's own history (M2, M3, M5, F-150,
F-144) is a repeated pattern of "PASSED"/"COMPLETE" claims later retracted for lack of a real oracle.
Phase 0 is deliberately the cheapest phase and the only one that can invalidate everything after it —
find out now, not after rebuilding `ask.ko` plumbing around a microcode limitation that was never real.

---

## Phase 0 — Prove CC-tree can dispatch a HIT at all (read-mostly, one board session, LOW-MEDIUM risk)

**Purpose:** answer the one question nothing in this project's history has actually answered: does
CC-tree exact-match classification produce a real hardware HIT on this silicon (LS1046A) and this
microcode (210.10.1), independent of any `ask.ko` bugs. If this fails, everything below is moot and the
project needs to revisit whether CC-tree is viable on 210.10.1 at all.

**Approach — bypass `ask.ko` entirely.** `kernel/common/patches/board/0107-fman-pcd-cc-test-debugfs-harness.patch`
is an existing, independent debugfs test harness that calls `fman_pcd_cc_static_install()` and
`fman_pcd_cc_static_get_base()` directly — the real kernel-side CC-tree write API, never touched by
CR-007 or the "Fix C1" removal (both of those only affected `ask.ko`'s own bookkeeping, not this
harness or the underlying kernel patches 0086b/0098/0108).

- **0a. (read-only, zero risk) Confirm current wiring — DONE 2026-08-04.** Patch 0107 is staged in
  `bin/ci-setup-kernel.sh` (sorts after 0106, before 101-sfp). Its `cc_test_install()` handler calls
  `fman_pcd_cc_static_install()`, which delegates byte-packing to `cc_pack_key()` (patch 0098) — that
  function already has F-156's fix: `key(16B)+mask(16B)`, 32B stride. **Row format is clean.**
- **0a-2. (read-only, zero risk) NEW BLOCKER FOUND 2026-08-04 — key CONTENT is wrong, not just format.**
  Patch `0108-fman-pcd-cc-per-key-fq-enqueue-ad.patch` (also staged, applies after 0107) rewrites
  `cc_pack_key()` a second time to hardcode the **ask20** branch's EKFC composite —
  `[SIP(4)|DIP(4)|SPI(4)=0|SPORT(2)|DPORT(2)]` (16 B, scheme `0x00180206`) — not **this** branch's real
  EKFC (`0x001C0006` = SIP|DIP|**PROTO**|SPORT|DPORT, 13 B). This is the exact class of bug
  `specs/cc-comparator-compare-window-hypothesis.md` already warned about for the ehash path,
  independently present here too. Running Phase 0 as-is would very likely produce a false-negative
  MISS caused by this layout mismatch, not real evidence about CC-tree/silicon capability. **Must fix
  `cc_pack_key()`'s composite before 0c.** Per the compare-window doc's own methodology: don't assume a
  layout by analogy (not even the seemingly-obvious PROTO-based one) — extend the existing F-158
  `fe_scaffold`-style tooling to dump KG's actual raw emitted bytes for `EKFC=0x001C0006` and match
  `cc_pack_key()` to that observation. Full finding: qdrant tag `phase-0-blocker`.
- **0b. (local build, zero board risk) Prepare the test.** Install one IPv4 exact-match key via the
  harness (SIP/DIP/PROTO/SPORT/DPORT matching a real test-plane flow) with the corrected key+mask
  format. Reuse F-157's technique — a dedicated TX FQ wired into the HIT path, distinct from the MISS
  FQ — as the unambiguous discriminator (this was the fix that finally made HIT/MISS distinguishable
  for ehash; the same principle applies here). Reuse F-158's `fe_scaffold`-style debugfs ground-truth
  dump, adapted to read back the installed CC match-table content, to confirm the write landed
  byte-correct before trusting any traffic result.
- **0c. (board-mutating — requires explicit confirmation before running) Execute on `.185`.** Install
  the key, send matching traffic, observe which FQ it lands on. A genuine CC-tree HIT here is new
  information this project has never had. A MISS here (with a byte-confirmed-correct match table) would
  point at a genuine microcode/silicon limitation on 210.10.1, not a wiring bug — a materially different
  and important finding in its own right.

**Exit criteria:** a board-confirmed, oracle-validated HIT (or a board-confirmed, byte-verified MISS,
which is also a valid — if worse — outcome). No further phases start without this result.

---

## Phase 1 — Rebuild `ask.ko`'s CC-tree insert path (code only, no board risk until Phase 2)

Gated on Phase 0 passing. `git show dd364494^` gives a starting skeleton for the bookkeeping CR-007
removed (`struct ask_hw_cc_slot`, shadow array, `nkeys`/`next_key_id`/`cc_installed`, `cc_handle`/
`hm_handle` on the flow cookie) — but that skeleton alone is insufficient: it was already **not**
reaching hardware by M5's time (the "Fix C1" comment, predating even M5, already describes the shadow
array as software-only). The actually-missing piece is whatever `ask_hw_port_reinstall()` (removed by
"Fix C1", date/commit not yet identified — see Open Question below) did to turn the shadow key into a
real `fman_pcd_cc_static_install()` / `fman_pcd_cc_node_add_key()` call. Reimplement that, incorporating
F-156's match-row fix from day one (do not resurrect the pre-F-156 byte format).

**Open question to resolve before/during this phase:** the exact commit and diff for "Fix C1" was not
pinned down during the 2026-08-04 review (git-history search was inconclusive — see qdrant
`no-confirmed-hw-hit-ever` entry). Finding it (likely via `git log -p --all -- '**/ask_hw.c'` around
commits before 2026-07-24, filtered manually rather than by pickaxe on the current path, given the
pickaxe-vs-rename pitfall already hit once) would de-risk this phase significantly.

## Phase 2 — Wire into `ask_flow_offload_replace()`, demote ehash (code + board validation)

Make the rebuilt CC-tree path the actual insert target on every REPLACE, not parallel bookkeeping next
to ehash. Keep ehash code present but non-primary (matches already-documented intent; no reason to keep
exercising a mechanism with a ~1.5 Gbps DDR ceiling even when working). Re-run the Phase 0 oracle
technique through the real `ask.ko` path end-to-end (CLI engage → REPLACE → HIT), not just the debugfs
harness, to confirm the wiring itself doesn't reintroduce a gap.

## Phase 3 — Get one trustworthy throughput number

Re-run the standard throughput/CPU test with oracle-confirmed HITs occurring throughout. This becomes
the real M5 replacement — the number this project has never actually had confirmed.

## Phase 4 — Capacity + multi-node scale-out (original T-M6-5 scope)

Only now does raising `FMAN_CC_MAX_STATIC_KEYS`/`FMAN_PCD_CC_HW_MAX_KEYS` toward the silicon limit
(`FMAN_PCD_CC_NODE_KEYS_MAX=255`) and implementing multi-node CC allocation mean anything.

## Phase 5 — Cleanup

Remove the two orphaned `ask_hw_pcd_cc_v4_tcp_for_port()`/`ask_hw_pcd_cc_v4_udp_for_port()` exports (or
repurpose them if Phase 1 needs a per-port CC-node handle — check before deleting), fix the ~19 stale
Fork-A-era comments across `ask_flow.c`/`ask_hw.c`/`ask_flow_offload.c` found in the 2026-08-04 review,
and flip the correction banners in `arch/fman-pcd.md`, `arch/software-stack-ask.md`, and
`plans/ASK2-MASTER-PLAN.md` from "not currently wired" to "restored + validated on [date]".

---

## Internal notes

- This plan supersedes T-M6-5's prior scope in `plans/ASK2-MASTER-PLAN.md` §5; that entry should point
  here once this doc is committed.
- Reuses tooling from `plans/NXP-106-ORACLE-VALIDATION-PLAN.md` (`bin/muram-mmap-dump.py`,
  `bin/kg-scheme-read.py`) if MURAM/KG-register-level verification is needed during Phase 0/1.
