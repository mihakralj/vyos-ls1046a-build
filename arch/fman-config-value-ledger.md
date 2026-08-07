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
| `word_0.hash_bytes_offset` (bits 17:16) | `0` (after `t->hash_shift`, dynamic) | patch 0125, **`F-053` retracted 2026-08-07** (commit `ee276acb`) | `0`, all 16 real tables | `[cdx-xml]` `hashshift="0"` × 16; `[999-5.4]` `fm_pcd_ext.h` field doc | **cross-checked-match, fix confirmed applied on-board (`node` word_0 read back as `0d000000`, bit 16 clear, vs the pre-fix `0d010000`) — board-tested 2026-08-07, `pkt_count` still `0`. Fixing this value alone did NOT produce a HIT; it was a real bug but not (or not the only) blocker.** |
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

## Port dispatch NIA (`fmbm_rfpne`)

| Field | This project | Set by | Vendor value | Source | Status |
|---|---|---|---|---|---|
| `NIA_KG_DIRECT` (bit 8) \| `physicalSchemeId` (bits 4:0) | **now set** (`F-178`, 2026-08-07) — `fmbm_rfpne = 0x00480304` (scheme 4) on arm | `fman_pcd_kg_port_arm_fe()` (patch 0132 + `F-178`), the function backing `fe_arm engage` | **always OR'd in for a single-bound-scheme port** (`fm_port.c` `SetPcd()`, `PRS_AND_KG_AND_CC`/`PRS_AND_KG` cases, `directScheme` branch) | `[999-5.4]` `fm_port.c`; `[.185-live]` dmesg confirmed `"KG direct-scheme addressing set, scheme 4 (rfpne 0x00480304)"` on arm | **cross-checked-match, fix confirmed applied on-board, board-tested NEGATIVE.** `F-162`'s helper now correctly fires from the live arm path (confirmed via dmesg, exact vendor-matching encoding). Genuinely cold-booted board, same 13-byte key/EKFC=`0x001c0006` combination used for the very first Phase 1 test (isolating this one variable), matching frame confirmed transmitted. `pkt_count` stayed `0`. This was the strongest structural hypothesis this investigation produced and it did not resolve the symptom either — see `plans/ASK2-MASTER-PLAN.md` §4.1 for the full writeup. |

## KeyGen scheme register (`kgse_ekfc`)

| Field | This project | Set by | Vendor value | Source | Status |
|---|---|---|---|---|---|
| `KG_SCH_KN_PORT_ID` (EKFC bit 31) | tested SET, `EKFC=0x801c0006`, 14-byte key, `portid=0x00`–`0x0f` (full 4-bit range, batch test) — board-negative, all 16 | `fe_kg_ekfc set`, board tests 2026-08-07 | **forced set unconditionally, every scheme, no guard** (`fm_kg.c` `BuildSchemeRegs()`, `//bmr` hack: `knownTmp \|= KG_SCH_KN_PORT_ID;`) | `[999-5.4]`/`[6.12-diff]` `fm_kg.c`; `[.106-live]` all 12 live schemes show bit 31 set | **cross-checked-match on structure (EKFC bit + key layout both confirmed against `cdx_ehash.c`/`cdx_common.h`), board-tested comprehensively NEGATIVE — but now known CONFOUNDED, not conclusive.** First `portid=0` alone, then a single-cycle batch test inserting all 16 possible 4-bit `portid` values (`0x00`–`0x0f`) as separate flow records for the identical 5-tuple, one genuine cold boot, one arm, one matching frame, all 16 records checked afterward. **Every one stayed `pkt_count=0`.** However, 2026-08-07 same-day follow-up traced `KG_SCH_KN_PORT_ID`'s actual extraction source to `kgse_dv0`/`kgse_dv1` (`privateDflt0`/`1`, per `fm_pcd_ext.h`'s `t_FmPcdExtractEntry` having no dedicated union member for `PORT_PRIVATE_INFO`) — and live-read those registers as `0x0a0a0a0a`/`0x0b0b0b0b`, an exact match to mainline `fman_keygen.c`'s unrelated `DEFAULT_HASH_KEY_IPv4_ADDR`/`DEFAULT_HASH_KEY_L4_PORT` RSS-fallback constants, never intentionally set by this project. Since the sweep tested single-byte DDR-key values against a comparator keyed off this uncontrolled register (not a byte the test accounted for), **the negative result does not rule out the mechanism** — see `arch/fman-microcode-210-programming-reference.md` §10.5a for the full correction and the proposed resolving experiment (explicitly zero `kgse_dv0`/`dv1` before retesting). |
| `<combine portid="true" offset="16" mask="0xF"/>` (NetPCD XML directive) | not used (this branch has no NetPCD/FMC XML compile step) | n/a | builds KeyGen **"extractedOrs"** (`e_FM_PCD_KG_EXTRACT_PORT_PRIVATE_INFO`, AN4760 "OR Data Vector") — affects computed **FQID**, not the raw key | `[fmc@5b9f4b1]` `FMCPCDReader.cpp`/`FMCPCDModel.cpp`/`FMCCModelOutput.cpp` (vendor's own public FMC source, pinned by this project's `fmc_git.bb`) | **corrects an earlier (pre-2026-08-07) doc claim that `<combine>` was a GEC/`kgse_gec[]` key mechanism — it is not.** `<combine>` is FQID-only and irrelevant to the ehash comparison key; it does not explain vendor's portid disambiguation inside the key at all. Vendor's real key-side portid byte, if any, would have to come from `KG_SCH_KN_PORT_ID` (row above), not `<combine>`. |

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
