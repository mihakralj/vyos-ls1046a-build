# ASK2 Phase 2 — Patch Triage (v1.2 → v1.3) — **EXECUTED (Option C2)**

**Date:** 2026-05-24
**Branch:** `ask20`
**Driver:** `plans/ASK2-COURSE-CORRECTION.md` §2 Phase 2
**Scope:** Classify every patch in `kernel/flavors/ask/patches/0001-0053` as
**KEEP / ARCHIVE / PARTIAL** under the v1.3 Path A architecture, identify the
archive destination, and define the resulting `bin/ci-setup-kernel.sh`
`ASK_PATCH_COUNT` arithmetic.

This document is the authoritative inventory consumed by Phase 2.4–2.7
(physical `git mv` + PARTIAL splits + CI script update).

## Status: COMPLETE — Option C2 executed 2026-05-24

`bash kernel/common/scripts/patch-health.sh --flavor ask --source release`
→ **Pass=55 Fail=0** (10 common patches + 45 active ASK patches).
ASK-tree breakdown: 45 active + 10 archived in
`kernel/flavors/ask/patches/archive-grafted-2026-05-24/`.

The original §8 "Open decision" between Option A (full split) and Option B
(defer split) was resolved in favour of a refined Option C2: full split
PLUS wholesale-archive of 0045 (cross-file keygen.c deps unrecoverable
without reverting 0042/0043) PLUS wholesale-archive of 0051 (revert of
archived 0043, now orphaned) PLUS partial-split 0046 (preserve
cc_node_remove_key public API at 0056, drop the graft-lifecycle bits).
0044 was rebased against the post-archive tree (line offsets 117/247
replacing original 118/322) because original 0032 added `struct
list_head oh_ports;` to `struct fman_pcd` which shifted subsequent line
numbers in `fman_pcd.c`. See §10 below for the executed plan diff.

---

## 1. Disposition rules

A patch is **KEEP** when it encodes a silicon fact, a foundational PCD
primitive, or a Path A enabler that v1.3 still consumes.

A patch is **ARCHIVE** when it exists *only* to support the graft model
or the OH-port two-stage classify→re-inject pipeline that v1.3 deletes
(§13.2 v1.3 budget: `fman_pcd_oh.c` → 0 LOC in v1.0; OH-port deferred to
v1.1 IPsec re-inject only).

A patch is **PARTIAL** when it bundles independent encoders or
primitives where some survive v1.3 and others do not. PARTIAL patches
are physically split (Phase 2.3) into a KEEP half (renumbered into a new
slot) and an ARCHIVE half (moved with the rest).

Archive destination:
`kernel/flavors/ask/patches/archive-grafted-2026-05-24/`

The archive directory name encodes both the *reason* (graft model
abandoned) and the *date* (so a future v1.4 audit can `git log -- … /
archive-grafted-2026-05-24/` and recover full provenance). Archived
patches are **not deleted** — they remain in tree for one release cycle
as a bisect anchor (Risk R1 mitigation in COURSE-CORRECTION §4).

---

## 2. Full inventory (patches 0001–0053)

### 2.1 Foundational PCD subsystem (0001–0025) — all KEEP

Patches 0001 through 0025 are the v1.0/v1.1 PCD foundation
(`fman_pcd.c` core, KG schemes, CC nodes/trees, MANIP base, PLCR,
REPLIC, parser, MURAM budget). None of them depend on the graft
model. All KEEP, unconditionally. No further analysis needed —
they predate the graft architecture and survive Path A intact.

### 2.2 Phase-2 audited range (0026–0053)

| # | Subject | Size | Disposition | Rationale (v1.3) |
|---|---|---:|---|---|
| 0026 | fman-pcd-muram-budget-fix | — | **KEEP** | Silicon fact (MURAM accounting), model-independent. |
| 0027 | fman-pcd-public-handle-helpers | — | **KEEP** | Public handle accessors used by `ask.ko` regardless of model. |
| 0028 | dpaa-export-rx-default-fqid | — | **KEEP** | Path A miss-action target — empty CC tree falls through to kernel RX default FQID. |
| 0029 | dpaa-eth-advertise-hw-tc | — | **KEEP** | `flow_block_offload` wiring; orthogonal to graft. |
| 0030 | dpaa-export-fman-port-id | — | **KEEP** | Path A pre-netdev hook reads port-id. |
| 0031 | dpaa-export-tx-fqid | — | **KEEP** | `FORWARD_FQ_WITH_MANIP` action target = peer-port TX FQID. |
| **0032** | **fman-pcd-oh-port** | 41 104 B | **ARCHIVE** | OH-port driver wholesale (628 LOC `fman_pcd_oh.c` + DT binding + `fman_port.c` match-table arm). v1.3 §13.2 deletes the OH-port from v1.0 budget (deferred to v1.1 IPsec re-inject only). |
| **0033** | **fman-pcd-manip-v1.2-oh-port-primitives** | 28 530 B | **PARTIAL** | Bundles 3 encoders: `MANIP_RMV_ETHERNET` (ARCHIVE — only used by OH-port chain), `MANIP_INSRT_GENERIC` (ARCHIVE — same), `MANIP_FIELD_UPDATE_IPV4_FORWARD` (**KEEP** — Path A `FORWARD_FQ_WITH_MANIP` action reuses for L3-forward TTL--/cksum/DSCP). Also adds `FMAN_PCD_API_VERSION` bump 1→2 (**KEEP** — Path A consumers expect v2). See §3.1 below for split plan. |
| **0034** | **fman-pcd-oh-port-claim-lock-split** | — | **ARCHIVE** | Locking refinement for OH-port `claim()` path. Dead with 0032. |
| 0035 | fman-pcd-cc-node-empty-default-capacity | — | **KEEP** | Path A pre-builds **empty** CC tree at boot — this patch is the empty-tree primitive. Strictly required. |
| **0036** | **fman-pcd-manip-chain** | 14 727 B | **ARCHIVE** | Adds `hmct_used` field + `chain_create` memcpy logic. Only needed for OH-port chained manip (>1 manip per AD walk). v1.3 `FORWARD_FQ_WITH_MANIP` carries up to 4 manip handles inline on the CC-key action atom — no chain primitive required. Spec §13.2 v1.3 reverts `fman_pcd_manip.c` 1600→1200 LOC, dropping chain wiring. |
| **0037** | **fman-pcd-manip-hmct-used-v12-encoders** | 1 392 B | **PARTIAL** | 4 hunks each setting `hmct_used = N` for one encoder: TTL_DEC (**KEEP** — v1.0 primitive, used by Path A), RMV_ETHERNET (ARCHIVE), INSRT_GENERIC (ARCHIVE), IPV4_FORWARD (**KEEP** — Path A consumes). See §3.2 below for split plan. |
| **0038** | **fman-pcd-manip-chain-bytes-used-accessor** | — | **ARCHIVE** | Exports `hmct_used` accessor. Only the chain consumer (0036) reads it. Dead with 0036. |
| 0039 | dpaa-export-rx-fman-port | — | **KEEP** | Path A z7 hook needs RX FMan port handle. |
| 0040 | fman-port-id-use-bmi-hwport | — | **KEEP** | Silicon-correct port-id arithmetic, model-independent. |
| 0041 | fman-pcd-kg-bind-port-widen-hwport-range | — | **KEEP** | Widens hwport range so Path A can claim schemes 3+4 on RX ports 8+9. |
| **0042** | **fman-pcd-kg-graft-cc** | — | **ARCHIVE** | Graft API — attaches CC tree to a KG scheme **already in use** by `dpaa_eth`. v1.3 Path A owns scheme 3+4 from boot, never grafts. |
| **0043** | **fman-pcd-kg-graft-mode-nia** | — | **ARCHIVE** | Graft-mode NIA RMW. Already neutralised by 0051 (the revert) — but the original add is still in the patch series and must be archived alongside 0051. |
| 0044 | fman-pcd-pre-netdev-hook | — | **KEEP** | **This IS Path A.** The pre-`register_netdev()` hook in `fman_memac.c` that `ask_pcd_install()` fires from. |
| 0045 | fman-pcd-debug-regdump | — | **KEEP** | Inspection surface (`/sys/kernel/debug/fman_pcd/`); non-functional, useful for M2 verification. |
| 0046 | fman-pcd-cc-node-remove-key | — | **KEEP** | Needed for `flow_block_cb` REPLACE/DESTROY runtime key removal. |
| 0047 | ask-in-tree-skeleton | — | **KEEP** | `drivers/net/ethernet/freescale/dpaa/ask/` skeleton — Path A requires `ask` built-in for `fs_initcall` ordering. |
| 0048 | ask-in-tree-source-migration | 282 407 B | **KEEP** | The actual `ask` source tree migration. Phase 3 will shrink contents (drop `ask_hostcmd.c`, `ask_neigh.c` defer logic, graft logic) but the migration patch itself stays. |
| 0049 | ask-fs_initcall | — | **KEEP** | `fs_initcall(ask_init)` ordering primitive — fires before `dpaa_eth_probe` `register_netdev`. Path A foundation. |
| 0050 | fman-pcd-cc-wire-group-table-and-miss-ad | — | **KEEP** | Wires CC tree group-table + miss-AD pointer. Required by Path A empty-CC-tree install (`miss_action = FORWARD_FQ(kernel_rx_default_fqid)`). |
| 0051 | fman-keygen-revert-pr14z15-nia-rmw | — | **KEEP** | Revert of 0043's graft-NIA RMW. Stays paired with 0043 in archive logic but the **revert itself** lives in the active series (it's what makes the current tree boot correctly). |
| 0052 | uapi-ask-spdx-syscall-note | — | **KEEP** | UAPI SPDX/syscall-note header. Required by YNL family `ask` (Phase 5). |
| 0053 | dpaa-noconfirm-offload-tx-fq | — | **KEEP** | TX-conf fast-path elision on offloaded FQs. Needed for M2 perf gate (≥2 Gbps + ≤5% CPU). |

---

## 3. PARTIAL patch split plans

### 3.1 Split 0033 (`fman-pcd-manip-v1.2-oh-port-primitives`, 28 530 B)

**Hunk inventory:**

| Hunk # | File | What it adds | Disposition |
|---|---|---|---|
| H1 | `fman_pcd_manip.c` lines 234 + comment block | Encoder coverage docstring listing all 3 v1.2 tags | **SPLIT** — keep IPV4_FORWARD line, drop RMV/INSRT lines |
| H2 | `fman_pcd_manip.c` macro block | `FMAN_PCD_L2_HDR_OFFSET`, `_RMV_SIZE`, `HMCD_IPV4_UPDATE_DSCP`, `IPV4_DSCP_SHIFT`, `IPV4_DSCP_MAX` | **SPLIT** — IPv4 DSCP macros (KEEP); L2-header macros (ARCHIVE) |
| H3 | `fman_pcd_manip.c` `manip_encode_ttl_dec` reflow | Pure whitespace cleanup, no semantic change | **KEEP** (cosmetic, no cost to retain) |
| H4 | `fman_pcd_manip.c` `manip_encode_rmv_ethernet` body | New encoder function | **ARCHIVE** |
| H5 | `fman_pcd_manip.c` `manip_encode_insrt_generic` body | New encoder function | **ARCHIVE** |
| H6 | `fman_pcd_manip.c` `manip_encode_field_update_ipv4_forward` body | New encoder function | **KEEP** |
| H7 | `fman_pcd_manip.c` `fman_pcd_manip_create()` switch arms | 3 new `case` labels | **SPLIT** — keep IPV4_FORWARD case; drop RMV/INSRT cases |
| H8 | `fman_pcd_manip.c` `fman_pcd_manip_create()` encoder dispatch | 3 new dispatch arms | **SPLIT** — keep IPV4_FORWARD arm; drop RMV/INSRT arms |
| H9 | `tests/fman_pcd_manip_test.c` | 5 new KUnit cases (RMV, INSRT-L2, INSRT-reject, IPV4_FORWARD-TTL, IPV4_FORWARD-DSCP) + suite registration | **SPLIT** — keep 2 IPV4_FORWARD cases; drop 3 RMV/INSRT cases |
| H10 | `include/linux/fsl/fman_pcd.h` | `FMAN_PCD_API_VERSION` bump 1→2 | **KEEP** |
| H11 | `include/linux/fsl/fman_pcd.h` | 3 new enum members + max-size macro + 2 new union arms | **SPLIT** — keep IPV4_FORWARD enum + union; drop RMV_ETHERNET + INSRT_GENERIC enum + max-size + insrt union arm |

**Resulting patch files:**

- **NEW** `0054-fman-pcd-manip-ipv4-forward-encoder.patch` — KEEP half. Contains:
  - Docstring entry for IPV4_FORWARD only
  - IPv4 DSCP macros (`HMCD_IPV4_UPDATE_DSCP`, `IPV4_DSCP_SHIFT`, `IPV4_DSCP_MAX`)
  - `manip_encode_field_update_ipv4_forward()` body
  - `case FMAN_PCD_MANIP_FIELD_UPDATE_IPV4_FORWARD:` switch arm + dispatch
  - 2 KUnit cases (`_ttl_test`, `_dscp_test`)
  - `FMAN_PCD_API_VERSION` 1→2
  - `FMAN_PCD_MANIP_FIELD_UPDATE_IPV4_FORWARD` enum + `ipv4_forward` union arm

- **ARCHIVE** `archive-grafted-2026-05-24/0033-fman-pcd-manip-v1.2-oh-port-primitives-RMV-INSRT-only.patch`
  — ARCHIVE half. Contains the OH-port-chain-specific primitives:
  - L2-header macros
  - `manip_encode_rmv_ethernet()` body
  - `manip_encode_insrt_generic()` body
  - 2 switch arms + 2 dispatch arms (RMV, INSRT)
  - 3 KUnit cases (`_rmv_ethernet`, `_insrt_generic_l2`, `_insrt_generic_reject`)
  - `MANIP_RMV_ETHERNET` + `MANIP_INSRT_GENERIC` enum members
  - `FMAN_PCD_MANIP_INSRT_GENERIC_MAX_SIZE` macro
  - `insrt_generic` union arm

### 3.2 Split 0037 (`fman-pcd-manip-hmct-used-v12-encoders`, 1 392 B)

**Hunk inventory:**

| Hunk # | Encoder | Adds | Disposition |
|---|---|---|---|
| H1 | `manip_encode_ttl_dec` | `manip->hmct_used = 4;` | **KEEP** |
| H2 | `manip_encode_rmv_ethernet` | `manip->hmct_used = 4;` | **ARCHIVE** |
| H3 | `manip_encode_insrt_generic` | `manip->hmct_used = 4 + n_words * 4;` | **ARCHIVE** |
| H4 | `manip_encode_field_update_ipv4_forward` | `manip->hmct_used = emit_payload ? 8 : 4;` | **KEEP** |

**Resulting patch files:**

- **NEW** `0055-fman-pcd-manip-hmct-used-ttl-and-ipv4-forward.patch` — KEEP half. Contains H1 + H4.
- **ARCHIVE** `archive-grafted-2026-05-24/0037-fman-pcd-manip-hmct-used-v12-encoders-RMV-INSRT-only.patch` — ARCHIVE half. Contains H2 + H3.

**Subtle dependency:** H4 (`ipv4_forward` hmct_used) depends on the
`emit_payload` local in `manip_encode_field_update_ipv4_forward()`,
which is introduced by 0033 H6. Since H6 lives in the new
`0054-…ipv4-forward-encoder.patch`, **0055 must apply after 0054**.
The numbering 0054 < 0055 enforces this naturally (patches apply in
sorted order per `bin/ci-setup-kernel.sh` glob).

---

## 4. Archive list (6 wholesale + 2 partial halves)

Move via `git mv` into `kernel/flavors/ask/patches/archive-grafted-2026-05-24/`:

| Patch | Reason |
|---|---|
| `0032-fman-pcd-oh-port.patch` | OH-port driver wholesale |
| `0033-fman-pcd-manip-v1.2-oh-port-primitives-RMV-INSRT-only.patch` (post-split) | OH-port chain encoders |
| `0034-fman-pcd-oh-port-claim-lock-split.patch` | OH-port locking |
| `0036-fman-pcd-manip-chain.patch` | Chain primitive (superseded by inline CC-key action) |
| `0037-fman-pcd-manip-hmct-used-v12-encoders-RMV-INSRT-only.patch` (post-split) | hmct_used for OH-only encoders |
| `0038-fman-pcd-manip-chain-bytes-used-accessor.patch` | Chain accessor (dead with 0036) |
| `0042-fman-pcd-kg-graft-cc.patch` | Graft API |
| `0043-fman-pcd-kg-graft-mode-nia.patch` | Graft-NIA RMW (paired with 0051 revert) |

---

## 5. Resulting patch-stack arithmetic

**Active series after Phase 2:**

- 0001–0031 (31 patches, all KEEP)
- 0035 (KEEP; 0032/0033/0034 archived, 0033-KEEP-half becomes 0054)
- 0039–0041 (3 KEEP; 0036/0037/0038 archived, 0037-KEEP-half becomes 0055)
- 0044–0053 (10 KEEP; 0042/0043 archived between 0041 and 0044)
- 0054 (NEW — `0033-KEEP-half`)
- 0055 (NEW — `0037-KEEP-half`)

Count: 31 + 1 + 3 + 10 + 2 = **47 active patches**.

**Archived:** 6 wholesale + 2 partial-archive-halves = **8 patches in
`archive-grafted-2026-05-24/`**.

**Original count:** 53 patches.
**Active reduction:** 53 → 47 active. **Slot 0033 and 0037 vacated** in
active series; their KEEP content lives at 0054 + 0055 respectively.

---

## 6. `bin/ci-setup-kernel.sh` changes required (Phase 2.7)

Current state (lines 218–340):

- Explicit glob enumeration of `0001-*.patch` … `0053-*.patch`
- Rename case `0NNN- → 1NNN-` for every active patch
- Assertion `[ "$ASK_PATCH_COUNT" -ne 53 ]` at line 337

Required changes:

1. **Remove** slot enumerations for `0032`, `0033`, `0034`, `0036`,
   `0037`, `0038`, `0042`, `0043` from the explicit glob loop and the
   rename case.
2. **Add** slot enumerations for `0054` and `0055`.
3. **Update** assertion: `[ "$ASK_PATCH_COUNT" -ne 47 ]`.
4. **Add** a comment block above the glob loop documenting the
   archive (`# Slots 0032/0033-archive-half/0034/0036/0037-archive-half/
   0038/0042/0043 archived 2026-05-24 — see plans/
   ASK2-PHASE2-PATCH-TRIAGE.md`).

The archive directory itself must **not** be globbed by
`ci-setup-kernel.sh` — leave the `kernel/flavors/ask/patches/*.patch`
glob bare so it picks up only active patches at the top level.
Sub-directories are not recursed.

---

## 7. Verification gate (Phase 2.8)

After all moves + splits + script updates land:

```bash
bash kernel/common/scripts/patch-health.sh --flavor ask --source release
```

Expected output: `Pass=47 Fail=0`.

If any of the 47 active patches fails to apply (most likely 0035, 0039,
0040, or 0041 — they sit between archived neighbours and may have
context lines that drift), the offending patch's context block needs to
be refreshed against the post-archive tree. `git apply --3way` with
mergiraf as the merge driver (per `.gitattributes`) handles mechanical
drift; manual review only required if mergiraf reports a conflict.

---

## 8. Open decision (blocks Phase 2.3+)

The split work in §3 (extracting KEEP halves of 0033 and 0037 into new
patches 0054 and 0055) is **~3 hours of patch surgery** —
`filterdiff --include='…'` won't cleanly separate the IPV4_FORWARD
hunks from the RMV/INSRT hunks because they're interleaved in the
switch statements and the enum block.

**Option A — Full split now** (Phase 2.3 as written in
COURSE-CORRECTION.md):
- Pros: clean active series, no dead RMV_ETHERNET/INSRT_GENERIC code
  reaches the kernel build.
- Cons: ~3 h of careful hunk extraction; risk of context drift if the
  splits are mechanically reassembled wrong.
- Verification: `patch-health.sh` covers it.

**Option B — Archive only the 6 wholesale patches; defer split to a
follow-up commit**:
- Pros: Phase 2 lands today; the wholesale archives (0032, 0034, 0036,
  0038, 0042, 0043) are the structurally important ones — they're the
  ones that define the **architectural** deletion.
- Cons: dead RMV_ETHERNET + INSRT_GENERIC encoders remain compiled
  into the kernel until the split lands. Code cost: ~120 LOC of dead
  code in `fman_pcd_manip.c` + ~80 LOC in tests + ~40 LOC in the
  header. These encoders are unreachable (no caller after 0032/0036
  archive) so the cost is binary size only, not runtime risk.
- Verification: `patch-health.sh` still passes because 0033 and 0037
  apply cleanly — they just add unused encoders.

**Recommendation: Option B for the Phase 2 commit, follow up with a
dedicated `ask20-cleanup-2` PR for the split.** Rationale: the
architectural commit message (`build(ask): archive OH-port + graft
patches per v1.3 spec`) is cleaner when it does **only** archive
moves, and the split is mechanically tricky enough to deserve its own
review cycle. Option A is acceptable if the user wants a single
Phase-2 commit that fully realises the v1.3 patch stack.

---

## 9. References

- `plans/ASK2-COURSE-CORRECTION.md` §2 Phase 2 (driver)
- `specs/ask2-modern-architecture-review.md` (2026-05-24, root cause)
- `specs/ask2-rewrite-spec.md` v1.3 §13.2 (module LOC budget),
  §15.1 (line-count table)
- `bin/ci-setup-kernel.sh` lines 218–340 (the patch-enumeration block
  this triage feeds)
- Qdrant tags: `ASK2`, `path-A`, `oh-port`, `graft`, `phase-2`,
  `patch-triage`
---

## 10. Option C2 executed delta (2026-05-24)

The plan in §1–§9 above describes Option A (full PARTIAL split for 0033
+ 0037, no other archives beyond the original 6 wholesale). During
execution two further patches turned out to be unrecoverable on the
post-archive tree, and 0044 required a full rebase. Final disposition:

### 10.1 Additional wholesale archives

| Patch | Original disposition (§2.2) | Executed disposition | Reason |
|---|---|---|---|
| `0045-fman-pcd-debug-regdump.patch` | KEEP | **ARCHIVE (wholesale)** | The debugfs regdump touches `fman_keygen.c` symbols that were only made non-static by 0042/0043. After archiving 0042+0043, the `keygen.c` deps for 0045 vanish and the patch fails to apply. Splitting out only the `fman_pcd.c` half is non-trivial because the regdump scaffolding cross-references the keygen exports. Archived as `0045-fman-pcd-debug-regdump-WHOLESALE-keygen-deps.patch`. Re-implementation is a follow-up (the regdump is observability-only, not on the M2 critical path). |
| `0051-fman-keygen-revert-pr14z15-nia-rmw.patch` | KEEP (per §2.2: "the **revert itself** lives in the active series") | **ARCHIVE (wholesale)** | Once 0043 (`fman-pcd-kg-graft-mode-nia`) is archived, 0051 is reverting code that no longer exists — it becomes a no-op revert against a hunk that's never applied. `git apply --3way` rejects it cleanly. Archived as `0051-fman-keygen-revert-pr14z15-nia-rmw-orphaned.patch`. |

### 10.2 Additional PARTIAL split: 0046

| Patch | Original (§2.2) | Executed | Reason |
|---|---|---|---|
| `0046-fman-pcd-cc-node-remove-key.patch` | KEEP wholesale | **PARTIAL — split into KEEP half `0056-fman-pcd-cc-node-remove-key-api-only.patch`; ARCHIVE half not preserved as a separate file** | The full 0046 patch bundled the `cc_node_remove_key()` public API (needed for `flow_block_cb` REPLACE/DESTROY) with internal graft-lifecycle bookkeeping that only made sense when CC trees could be re-grafted at runtime. Path A pre-installs the CC tree once at boot and never re-grafts, so the lifecycle half is dead. Split-out preserves only the public API surface; the lifecycle bits were dropped (reconstructable from pre-Phase-2 git history if ever needed). |

### 10.3 0044 rebase (post-archive line offsets)

`0044-fman-pcd-pre-netdev-hook.patch` required regeneration. The
original was authored on top of 0032 (which added `struct list_head
oh_ports;` to `struct fman_pcd`) and a different 0033 / 0036 chain.
After archiving those, `fman_pcd.c` line numbers in the post-archive
tree shifted: hunk 1 anchor moved from line 118→117, hunk 2 anchor
moved from line 322→247. The patch body itself is byte-identical — only
the `@@ -X,Y +X,Y @@` headers changed. Regenerated via:

```bash
cd work/linux-6.18.31
# stack-apply 0001-0041 cleanly against pristine baseline
# hand-edit fman_pcd.c, fman_pcd_internal.h, fman_port.c, fman_pcd.h
# with TAB-aware Python (this repo's fman_* files use TAB indentation)
git -c user.email=ci@local -c user.name=ci commit -m 'rebased'
git format-patch -1 HEAD --stdout --no-signature --zero-commit > /tmp/0044-new.patch
```

The original commit message (rich PR14z19 architectural rationale) was
spliced back over the regenerated `git format-patch` placeholder header.

### 10.4 Final active count: 45 (not 47)

§5's projected count of 47 assumed Option A. Option C2's additional
archives bring the count down further:

```
Active = 53 original
       - 8 (Option A wholesale archives — §4 list, but with 0033/0037
            PARTIAL counted as 1 archived half each)
       - 2 (Option C2 additional wholesale archives — 0045, 0051)
       - 1 (0046 PARTIAL — ARCHIVE half dropped without separate file)
       + 3 (KEEP halves — 0054 from 0033, 0055 from 0037, 0056 from 0046)
       = 45
```

`bin/ci-setup-kernel.sh` assertion is `[ "$ASK_PATCH_COUNT" -ne 45 ]`
(see the comment block at lines ~330–345 in the current script — the
arithmetic is documented inline).

### 10.5 Archive directory contents (final, 10 patches)

```
kernel/flavors/ask/patches/archive-grafted-2026-05-24/
├── 0032-fman-pcd-oh-port.patch
├── 0033-fman-pcd-manip-v1.2-oh-port-primitives-RMV-INSRT-only.patch
├── 0034-fman-pcd-oh-port-claim-lock-split.patch
├── 0036-fman-pcd-manip-chain.patch
├── 0037-fman-pcd-manip-hmct-used-v12-encoders-RMV-INSRT-only.patch
├── 0038-fman-pcd-manip-chain-bytes-used-accessor.patch
├── 0042-fman-pcd-kg-graft-cc.patch
├── 0043-fman-pcd-kg-graft-mode-nia.patch
├── 0045-fman-pcd-debug-regdump-WHOLESALE-keygen-deps.patch
└── 0051-fman-keygen-revert-pr14z15-nia-rmw-orphaned.patch
```

### 10.6 Verification result

```
$ bash kernel/common/scripts/patch-health.sh --flavor ask --source release
…
=== Verdict ===
Pass: 55   Fail: 0
 ✓ all patches apply cleanly against linux-6.18.31 (flavor=ask)
```

Pass=55 = 10 common patches (vyos/board/fixes) + 45 active ASK patches.
Fail=0. `ASK_PATCH_COUNT` assertion in `bin/ci-setup-kernel.sh` matches.

### 10.7 Follow-up work

1. **0045 re-implementation**: the debugfs regdump observability surface
   is unused for M2 but useful for L3-forward triage. Re-author against
   the post-archive tree, dropping the `fman_keygen.c` static→non-static
   conversions (which were a graft-model artefact).
2. **0046 ARCHIVE-half** is *not* on disk as a separate patch — only the
   KEEP half exists as 0056. If a future audit wants the dropped
   lifecycle bookkeeping back, reconstruct from pre-Phase-2 git history.
3. **Diff against Option A** in the spec: spec §13.2 budgets the
   `fman_pcd_manip.c` LOC at 1200; Option C2 leaves it at slightly less
   (the 0046 ARCHIVE-half wasn't accounted for in the v1.3 budget). No
   spec update needed — the spec describes the target architecture, not
   the patch-stack accounting.

### 10.8 Lessons learned (stored to Qdrant 2026-05-24)

- **TAB-separator convention discovery.** This repo's `fman_*.c` /
  `fman_*.h` source files use literal TAB indentation, NOT spaces.
  Patch context lines must include the TAB after the leading space
  (` <TAB>struct list_head replic_groups;`). Hand-edits via
  `replace_in_file` or similar tools can strip TABs silently, causing
  `git apply --3way` to fail with "patch does not apply" even when the
  diff *looks* correct. Always use TAB-aware Python with explicit
  string anchors when regenerating fman_* patches.

- **`patch-health.sh` BASELINE_REF pollution.** The script captures
  `BASELINE_REF=$(git rev-parse HEAD)` at start of run. If HEAD already
  carries stack-applied commits from a previous interrupted run, the
  pseudo-baseline is polluted and every cumulative-stack patch fails
  with "does not exist in index" against the wrong tree. Cure: `git
  reset --hard <pristine SHA> -q && git clean -fdq` before re-running.
  The pristine SHA is the first commit (the one created by `git init`
  during fetch-kernel.sh's tarball extraction).

- **Cascade-failure-from-single-mid-stack-failure diagnostic pattern.**
  When `patch-health.sh` reports many sequential failures with "does
  not exist in index", the *first* failure is the real one — the rest
  are downstream cascade because the script resets to BASELINE_REF
  after the first fail, undoing all prior successful stack commits.
  Always look at the first ✗ in the verdict block, not the count.
