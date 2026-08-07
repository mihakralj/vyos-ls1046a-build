# FMan EXT_HASH Config-Value Ledger

## Purpose

`arch/fman-microcode-210-programming-reference.md` verifies *structure*: bit
positions, byte offsets, struct sizes. It has been thorough and mostly
correct at that job. What it never did systematically is verify *value*:
for a field sitting at the right bits in a bit-exact-correct struct, is the
number actually written there the number vendor's real, running production
system writes there?

`F-053` (`en_exthash_node.word_0`'s `hash_bytes_offset`, hardcoded to `1`
since 2026-07-10) sat in a correctly-positioned, bit-exact field for a month
before a value-level cross-check against vendor's real `cdx_pcd.xml` found
it should have been `0`. This ledger exists so the next field-value bug
gets caught the same day it's introduced, not a month later.

**Rule:** any fixup that sets a hardware-visible field value gets a row
here before it's considered done. `status` starts at `not-yet-cross-checked`
and only moves to `cross-checked-match` or `cross-checked-MISMATCH` after
an explicit comparison against a real vendor source (cited below, not
assumed).

## Source tags

Cite one of these for every vendor-side value quoted below:

| Tag | What it is |
|---|---|
| `[999-5.4]` | `~/ask-ref/ask/patches/kernel/999-layerscape-ask-kernel_linux_5_4_3_00_0.patch` — complete vendor SDK diff, kernel 5.4 base. |
| `[6.12-diff]` | `we-are-mono/ASK` `patches/kernel/002-mono-gateway-ask-kernel_linux_6_12.patch` (locally mirrored at `~/kernel-ls1046a-build/reference/ASK-mt-6.12.y/`) — a **different, later** vendor snapshot. Confirmed to genuinely disagree with `[999-5.4]` in places (415-line diff between the two `fm_ehash.c` versions alone) — always note which snapshot a value came from. |
| `[NXP-public]` | Public NXP QorIQ SDK blobs / `nxp-qoriq/linux-extras`, pristine (non-ASK) base. |
| `[cdx-xml]` | `dpa_app/files/etc/cdx_pcd.xml` — vendor's real, deployed FMC config, 16 production `<hashtable>` distributions. The single strongest "what does production actually use" source. |
| `[.106-live]` | Live register/counter read from `.106`, the running vendor ASK production box. |
| `[.185-live]` | Live register/counter read from this project's own test board. |

## `en_exthash_node` — CC-tree AD (4 words, written by `copy_td_to_ccbase()`)

| Field | This project | Set by | Vendor value | Source | Status |
|---|---|---|---|---|---|
| `word_0.table_base_hi` | DDR phys addr, dynamic | patch 0125 | same formula | `[999-5.4]` `fm_ehash.c` | N/A — address-derived, not a policy value |
| `word_0.hash_bytes_offset` (bits 17:16) | `0` (after `t->hash_shift`, dynamic) | patch 0125, **`F-053` retracted 2026-08-07** (commit `ee276acb`) | `0`, all 16 real tables | `[cdx-xml]` `hashshift="0"` × 16; `[999-5.4]` `fm_pcd_ext.h` field doc | **cross-checked-MISMATCH → FIXED, board retest pending** (task #34) |
| `word_0.key_size` (bits 29:24) | dynamic, `fe_ehash set` arg 2 | patch 0125 | dynamic, per-table `keysize` XML attr | `[cdx-xml]` | cross-checked-match (mechanism); **actual byte count is the open PORT_ID question below** |
| `word_0.miss_action_type` (bits 31:30) | `0`, unused — miss dispatch handled by a separate FE chain (`EXIT`+`DEALLOCATE`), not this field | patch 0128 | `EN_EHASH_MISS_ACTION_{NIA,ENQUE,DONE,DROP}`, set per table | `[999-5.4]` `fm_ehash.c` `ExternalHashTableSet()` | **intentional architectural difference** — this project's miss path doesn't route through this field at all. Not a value bug; flag if miss-path behavior is ever in question. |
| `word_1.table_base_lo` | DDR phys addr, dynamic | patch 0125 | same formula | `[999-5.4]` | N/A — address-derived |
| `word_2.hash_mask_bits` (bits 15:12) | `log2(mask+1)`, dynamic | patch 0125 | identical formula | `[999-5.4]` `fm_ehash.c` | cross-checked-match (structurally guaranteed by shared formula) |
| `word_2.global_mem_offset` (bits 11:0) | `(EN_INTERNAL_BUFF_POOL_SIZE>>8)&0xfff` = `0x080` | patch 0125 | `EN_INTERNAL_BUFF_POOL_SIZE >> 8` (constant, size-based not address-based) | `[999-5.4]` `fm_ehash.c` `ExternalHashTableSet()` | **cross-checked-match** (2026-08-07 T-M3-R Phase 2 item 1; independently reconfirmed live on-board via `fe_ehash` node readback `0x04c6f080`) |
| `word_2.int_buf_pool_addr` (bits 31:16) | `(muram_off>>8)&0xffff`, dynamic | patch 0125 | `p_FmPcd->InternalBufMgmtMuramArea`, `>>=8`'d once at `FM_PCD_Init()` before assignment | `[999-5.4]` `fm_pcd.c` `FM_PCD_Init()` + `fm_ehash.c` | **cross-checked-match** (same date/session as above) |
| `word_3` (`nia`/`fqid` union) | not used this way — dispatch goes through the separate `FE_ENTER`/`ENQ`/`EXIT` FE chain | patch 0128/0175 | `NIA_ENG_KG`/`FQID` per miss config | `[999-5.4]` | **intentional architectural difference**, same reasoning as `miss_action_type` above |

## `en_ehash_entry` — per-flow DDR record (flags + chain header + key)

| Field | This project | Set by | Vendor value | Source | Status |
|---|---|---|---|---|---|
| `flags` bit 15 (`INVALID_ENTRY`) | `0` (valid) for active entries | patch 0128 | `0` for valid entries (semantic definition) | `[999-5.4]` `fm_ehash.h` | cross-checked-match |
| `flags` bit 13 (`TIMESTAMP_EN`) | `0` (dropped, `F-176` Phase 1 correction 2026-08-07) | `F-176` | `1`, forced unconditionally on every key | `[999-5.4]` `fm_ehash.c` `ExternalHashTableSet()` (the `if (p_Param->agingSupport)` guard is commented out) | **intentional divergence** — vendor's bit is backed by a live 4-slot MURAM pool (`extHashTsInfo`) + userspace timer (`cdx_timer.c`) this project doesn't implement; enabling the bit without that infra was suspected of tainting a HIT test — board-tested clean either way (Phase 1 retest, both `0x1000` and `0x3000` gave `pkt_count=0`), so this bit is confirmed **not** the ehash-HIT blocker, but still not vendor-matched if timestamp readback is ever needed |
| `flags` bit 12 (`STATS_EN`) | `1` (`F-176`) | `F-176` | conditional on `p_Param->statisticsMode` | `[999-5.4]` | cross-checked-match (mechanism); value is a diagnostic choice, not a correctness requirement |
| `next_entry_hi:16`/`next_entry_lo:32` (chain pointer, `swab64`'d) | matches | `F-144` (byte-order fix) | same | `[999-5.4]` `fm_ehash.c` `ExternalHashTableAddKey()` | cross-checked-match |
| key bytes, position (offset 8) | matches | patch 0128 | same (8-byte header before key) | `[999-5.4]` `fm_ehash.h` | cross-checked-match — **structural** position only; key *content* (does it include `PORT_ID`?) is the open question below |

## KeyGen scheme register (`kgse_ekfc`)

| Field | This project | Set by | Vendor value | Source | Status |
|---|---|---|---|---|---|
| `KG_SCH_KN_PORT_ID` (EKFC bit 31) | clear — test EKFC `0x001c0006` | `fe_kg_ekfc set`, this project's test config | **forced set unconditionally, every scheme, no guard** (`fm_kg.c` `BuildSchemeRegs()`, `//bmr` hack: `knownTmp \|= KG_SCH_KN_PORT_ID;`) | `[999-5.4]` `fm_kg.c`; `[.106-live]` all 12 live schemes show bit 31 set | **NOT YET cross-checked / open**. This is the single highest-priority open row in this ledger. See `arch/fman-vendor-source-extraction-2026-08-07.md` §5 for the full open question (does PORT_ID reach the live-packet-side extracted key, or is it consumed by the separate `<combine>` OR-vector KeyGen stage instead?). Recommended test (compare KG hash for `EKFC=0x001c0006` vs `0x801c0006` on an identical controlled frame) has never been run — blocked on rebuilding a reliable, ALLOCATE-independent hash-capture mechanism (the original 2026-07-13 UNKNOWN-1 technique, not yet re-located this session; `fe_hash_probe` confirmed unreliable, same transient-workspace problem as the older `fe_probe`). |

## FE-VM insert-path sync (not a value field, but a policy choice worth tracking here)

| Mechanism | This project | Vendor | Status |
|---|---|---|---|
| `FMFP_EXTC[INV0]` SYNC on ehash bucket-head publish | Added (`F-177`, 2026-08-07) | Not called — `ExternalHashTableAddKey()`'s fast/fresh-insert path calls no sync of any kind | This project now does **more** than vendor here, not less — additive-only, board-tested negative either way (Phase 2, `pkt_count=0` before and after `F-177`). Not a mismatch requiring a fix, just noted for completeness. |
| Host Command dispatch on insert | Never called | Never called (fast path) | cross-checked-match |

## How to use this

Before writing a new fixup that sets a hardware-visible field: check this
table first. If the field isn't here, that's the gap — add a row (even
`not-yet-cross-checked` is useful, it documents that the question exists).
When a board test or a vendor-source read resolves a row, update its
`Status` and `Source` columns in the same commit as the fixup, and link the
qdrant entry that has the full narrative (this ledger stays terse by
design — see `arch/fman-microcode-210-programming-reference.md` and
`plans/ASK2-MASTER-PLAN.md` for the story behind each row).

## Cross-references

- `arch/fman-microcode-210-programming-reference.md` — structural/register
  reference (bit layouts, byte offsets, FE opcodes). This ledger assumes
  that doc's structural claims are correct and focuses only on values.
- `arch/fman-vendor-source-extraction-2026-08-07.md` — the full narrative
  behind the open `PORT_ID`/`<combine>` question.
- `plans/ASK2-MASTER-PLAN.md` §4.1 — T-M3-R phase history, including the
  `F-053` retraction and its board-test status.
- qdrant tags: `F-053-hash-bytes-offset-wrong-finding`,
  `T-M3-R-phase-2-final-result`, `combine-portid-open-question`.
