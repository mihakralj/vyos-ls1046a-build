# FMan Function Inventory

Split out from `arch/fman-microcode-210-programming-reference.md` §12
(2026-08-07) — this is a catalogue (what exists, what it does), not
architecture. Inventories grow unboundedly and don't need to live inside
the structural/register reference doc. For field-*value* cross-checks
(this project's value vs. vendor's real production value), see
`arch/fman-config-value-ledger.md` instead — this file only tracks
function-level relevance/status, not the specific numbers each function
writes.

**Source note:** most of §12.1 below was gathered from `we-are-mono/ASK`
(branch `mt-6.12.y`) — tag `[6.12-diff]` in the ledger's terms. A second,
independent vendor snapshot (`~/ask-ref/ask/patches/kernel/999-layerscape-
ask-kernel_linux_5_4_3_00_0.patch`, kernel 5.4 base — tag `[999-5.4]`) was
read in full 2026-08-07 and confirmed the same mechanisms with a few
additions noted inline; the two snapshots are NOT always identical (see
the ledger's source-tag table for a concrete example), so treat unlabeled
rows below as `[6.12-diff]`-sourced unless a `[999-5.4]` note says
otherwise.

## 1. Capability/Feature Inventory

| # | Function | Status | Cap Bit | Driver Consumer |
|---|---|---|---|---|
| 1 | **Hard Parser** (L2–L4 header recognition, 16 protocols) | Consumed | - | Mainline `fman_prs.c` |
| 2 | **Soft Parser** (custom protocol extensions, 1984-byte instruction space) | **210-only** (consumed) | BIT 4 | `fman_pcd_prs.c`. The NXP-official RSR 10.3.0.B1 stack uses this to program a 194-line NetPDL protocol (`/etc/cdx_sp.xml`) handling PPPoE ccbase-slide, TTL/hop-limit kernel-punt, 6-in-4 dispatch, and OH-port Ethernet re-parse |
| 3 | **KeyGen** (32 schemes, raw CRC-64 hash, EKFC/GEC extraction, FQID distribution) | Consumed | - | `fman_pcd_kg.c` |
| 4 | **KeyGen post-hash index + explicit PP-select** | Present (unconsumed) | - | None |
| 5 | **CC Match-Table** (exact-match, ≤255 entries, ≤3 nested hops, per-key stats) | Consumed | BIT 0 | `fman_pcd_cc.c` |
| 6 | **CC Hash-Table** (hashed CC lookup, DDR-based, large flow tables) | **210-only** (unconsumed) | BIT 5 placeholder | None |
| 7 | **Header Manipulation** (VLAN/Q-in-Q/MPLS push-pop, arbitrary byte insert/remove/replace) | Consumed | BIT 1 | `fman_pcd_manip.c` |
| 8 | **Policer** (256 profiles, RFC 2698 srTCM / RFC 4115 trTCM, color-marking) | Consumed | BIT 2 | `fman_pcd_plcr.c` |
| 9 | **FE-VM ehash** (EXT_HASH → MUX → ENQ → EXIT dispatch, DDR flow store) | **210-only** (consumed) | - | `fman_pcd_fe.c` |
| 10 | **Frame Replicator** (source-TD + member-AD chain → multiple egress FQs) | **210-only** (unconsumed) | BIT 8 placeholder | KUnit tests exist |
| 11 | **IP Reassembly** (timeout-driven flush) | **210-only** (unconsumed) | BIT 6 placeholder | None |
| 12 | **IP Fragmentation** | **210-only** (unconsumed) | BIT 7 placeholder | None |

Capability bitmask: `0x17 = CC_EXACT_MATCH | HM_NODES | POLICER_TRTCM |
PARSER_SOFTSEQ`. Bit 3 (`HC_DISPATCH`) is deliberately clear; the Host
Command doorbell is absent from this blob.

**[NOTE]** "210-only" labels here describe caps-bit gating and driver
consumption. Per the main reference doc's entry-point analysis, code for
features like Frame Replicator, IP Reassembly, and IP Fragmentation
exists in the public blobs too — it is simply never advertised or invoked
there.

## 2. Vendor Source Function Catalogue (complete read, `we-are-mono/ASK` + pristine NXP SDK base + `999-5.4`)

Every `Fm*`/`FM_*`/`fm_*` function this project has actually read (not
grepped) across both vendor snapshots and the pristine `nxp-qoriq/linux-
extras` base (`[6.12-diff]` patches against commit
`b9482121ae39ba7c297870670ecbfefb179af402`), gathered across three deep-read
passes done to find the still-open ehash-HIT gap. `[ASK]` = added or
modified by the ASK patch; `[SDK]` = unmodified pristine NXP function,
read for context only. `E` = directly relevant to the ehash comparator;
`T` = tooling/debug only; `D` = confirmed dead code for this board's
active config (`USE_ENHANCED_EHASH=1`).

**`Peripherals/FM/Pcd/fm_kg.c`** (KeyGen scheme programming)

| Function | Relevance | Notes |
|---|---|---|
| `BuildSchemeRegs()` | E | Builds all `kgse_*` scheme registers from `t_FmPcdKgScheme`. EKFC (`knownTmp`→`kgse_ekfc`) and GEC (`kgse_gec[]`) are **not parallel** — GEC is fallback-only for fields with no known-field bit. `[ASK]` unconditionally OR's `KG_SCH_KN_PORT_ID` into every scheme (`//bmr` hack, no guard) — see the ledger's open `PORT_ID` row. Confirmed identical in both `[6.12-diff]` and `[999-5.4]`. |
| `GetKnownFieldId(bitMask)` | E | `[SDK]` MSB-first field-ID assignment (leading-zero-count from bit 31). `PORT_ID` (bit 31) → ID 0, sorts first. |
| `GetKnownProtMask()` | E | `[SDK]` Maps a protocol/field pair to its `KG_SCH_KN_*` bitmask, or returns 0 (triggering the GEC fallback). |
| `GetGenHdrCode()` / `GetGenFieldCode()` / `GetGenCode()` | — | `[SDK]` GEC opcode helpers for the non-known-field fallback path. Not exercised by a standard 5-tuple scheme. |
| `disp_sch_info()` | T | `[ASK]` debug printk of scheme id/matchVector. |
| `FmPcdKgGetSchemeId()` / `FmPcdKgGetVspe()` | — | `[SDK]` trivial accessors. |

**`Peripherals/FM/Pcd/fm_cc.c`** (CC-tree / classification build)

| Function | Relevance | Notes |
|---|---|---|
| `copy_td_to_ccbase()` | **E, key finding** | `[ASK]`. Writes the ehash `en_exthash_node`'s 4 words **directly into the CC-tree root's own AD slot** — the same MURAM location `FMBM_RCCB` points at. Write order `word_1, table_base_lo, word_2, word_0` (word_0, the type/valid bits, written **last** — torn-write avoidance). No group-AD/match-table indirection. Confirms this project's `F_147`/`F_148` (direct-RCCB topology) independently arrived at the same mechanism. `[999-5.4]` note: there are actually TWO variants of this function, gated by a nested `#ifndef EXCLUDE_FMAN_IPR_OFFLOAD` inside the outer `#ifdef USE_ENHANCED_EHASH` — a 3-arg reassembly-aware version and a 2-arg version matching this board's `EXCLUDE_FMAN_IPR_OFFLOAD` config. Both write the same 4 words in the same order — confirmed no divergence in the part that matters. |
| `FM_PCD_CcRootBuild()` | E | `[ASK]`-modified, `#ifdef USE_ENHANCED_EHASH`. Calls `copy_td_to_ccbase()` for every root entry whose next-engine is `e_FM_PCD_CC`. The pristine locking block (`FmPcdLockTryLockAll`/`Unlock`) is `#ifdef`'d **out** for this path with comment `// jyos following code is crashing in case of EHASH` — software mutex only, no register writes skipped. `[999-5.4]` confirms the same structure, no additional register writes or "arm"/"enable" step beyond building and writing these AD words. |
| `CcUpdateParam()` | D | Calls `FmPortSetFESupport()` only in the `#ifndef USE_ENHANCED_EHASH` branch; that branch is dead code on this build (`USE_ENHANCED_EHASH` unconditionally `1`, see `fm_pcd_ext.h`). The `#else` (active) branch has the call explicitly commented out (`// FmPortSetFESupport(h_FmPort);`) and just returns `E_OK`. `FmPortSetFESupport`/`internalFEBufferManagementIndexAddr` is **not wired up under the active path** — ruled out as a missing-wiring hypothesis, re-confirmed via full `[999-5.4]` guard-boundary read 2026-08-07. |
| `set_reassembly_tds()` | — | IP-reassembly table descriptors, unrelated to plain ehash. |

**`Peripherals/FM/Pcd/fm_ehash.c`** (new file, complete — `we-are-mono/ASK` and `999-5.4` both ship this as a wholesale new file, no pristine equivalent)

| Function | Relevance | Notes |
|---|---|---|
| `ExternalHashTableAddKey()` | **E** | The live insert path `cdx_ehash.c` calls. Fast path (fresh insert, empty bucket) calls **no sync of any kind** — not `FmPcdHcSync`, nothing. Sets `flags \|= TIMESTAMP_EN` **unconditionally** — the `if (p_Param->agingSupport)` guard is commented out in vendor's own code. |
| `ExternalHashTableDeleteKey()` | E | Always calls `FmPcdHcSync()` before returning (every branch) — deletes need it because they free memory the walker might still read; inserts into DMA-coherent DDR structures do not. |
| `ExternalHashTableSet()` | E | Builds the 4-word `en_exthash_node` template in **host heap memory only** (`info->node`); does NOT itself write to MURAM — see `copy_td_to_ccbase()` above for that step. `word_1`'s `int_buf_pool_addr`/`global_mem_offset` fields are populated from `p_FmPcd->InternalBufMgmtMuramArea` (see `fm_pcd.c` below). **Cross-checked bit-exact against this project's own encoding, see the ledger — resolved 2026-08-07, not a gap.** |
| `ExternalHashTableModifyMissNextEngine()` | — | Miss-path next-engine update for an existing table; writes `word_0` last (same torn-write pattern). |
| `ExternalHashTableFmPcdHcSync()` | E | `EXPORT_SYMBOL`'d public wrapper around `FmPcdHcSync()`. Its only 2 call sites in the whole vendor tree are `dpa_control_mc.c` (multicast group member removal) and `control_rtp_relay.c` (updating params on an *already-inserted* entry) — never a fresh key insert. |
| `ExternalHashGetSECfailureStats()` / `ExternalHashResetSECfailureStats()` | — | IPsec/SEC-engine failure counters, unrelated. |
| `ExternalHashSetDscpVlanpcpMapCfg()` / `...Get...` | — | QoS remap config, unrelated. |
| `FM_PCD_HashTableModifyMissMonitorAddr()` | — | Declared in `fm_pcd_ext.h`, implemented here; reassigns `p_HashTbl->extHashInfo.missMonitorAddr`. Belongs to the *other*, `FM_PCD_HashTableSet()`-only hash-table API family (see main reference doc §10 Phase 0 correction) — not the mechanism `cdx_ehash.c` uses. |

**`Peripherals/FM/Pcd/fm_cc.c` legacy `ExternalHashTable*` family** `[999-5.4]`, new for this catalogue

| Function | Relevance | Notes |
|---|---|---|
| `ExternalHashTableSet()` / `ExternalHashTableAddKey()` / `ExternalHashTableDelete()` / `ExternalHashBuildResult()` / `ExternalHashResultGetContextAddr()` / `ExternalHashResultGetMonitorAddr()` (all defined *inside* `fm_cc.c`, distinct from the same-named functions in `fm_ehash.c`) | D | All gated `#ifndef USE_ENHANCED_EHASH` — dead code for this board's active config. This is the SAME legacy `FM_PCD_HashTableSet()`-only mechanism already ruled out (main reference doc §10 Phase 0 correction), now directly confirmed via `#ifndef`/`#endif` guard boundaries rather than inferred from ioctl-table gating alone. Do not confuse with `fm_ehash.c`'s same-named, genuinely-active functions above — two different files, two different feature flags, easy to misread if grepping without checking the guard. |

**`Peripherals/FM/Pcd/fm_pcd.c`** (top-level PCD init/API)

| Function | Relevance | Notes |
|---|---|---|
| `FM_PCD_Init()` | **E, key finding** | `[ASK]`-extended, `DPAA_VERSION>=11`/`USE_ENHANCED_EHASH`-gated. Allocates (a) a 100-slot × 28-byte FE-object MURAM pool (`AllocFEObjs()`) pre-building Mux-FE/Transition-FE/Exit-FE/HM-FE singletons, and (b) a 32 KiB+ "internal buffer management" MURAM pool whose compressed offset becomes `en_exthash_node.word_2`'s `int_buf_pool_addr`/`global_mem_offset` (see `fm_ehash.c` above), stored as `p_FmPcd->InternalBufMgmtMuramArea` — critically, `>>= 8`'d in place here, BEFORE `ExternalHashTableSet()` ever reads it (confirmed via full `[999-5.4]` read — this is why the direct-assignment-looking line in `fm_ehash.c` is still bit-exact-correct against this project's own explicit `>>8` shift). Also allocates `extHashTsInfo`, a 4-slot timestamp pool (see below). |
| `FM_PCD_Free()` | — | Frees both pools above. |
| `AllocFEObjs()` / `FmPcdGetFE()` | E | FE-singleton pool allocator/lookup, dedup'd by `memcmp` on `t_FmPcdFEParams`. This project's own `fman_pcd_fe_singletons_build()` is the from-scratch equivalent — already board-validated byte-correct against `t_ExtHashFe`. |
| `FmPcdCcBuildFE()` | — | Declared/used; the real (non-stub) implementation lives in the *older* LSDK 5.4 oracle — confirmed this project's F-175 byte-matches it. |
| `FM_PCD_UpdateExtTimeStamp(id, val)` / `FM_PCD_GetExtTimeStampAddr(id)` / `FM_PCD_GetExtTsRef(id)` | E | Manage the 4-slot `extHashTsInfo` MURAM pool that every key's forced `TIMESTAMP_EN` bit references via its `timestamp_counter` field. Kept alive by `cdx/cdx_timer.c`'s periodic kernel timer calling `dpa_update_timestamp()` — infrastructure entirely **outside** `sdk_fman`, easy to omit in a from-scratch reimplementation. This project's `F-176` intentionally sets `TIMESTAMP_EN=0` (Phase 1 correction) rather than implement this pool — board-tested clean, see the ledger, resolved 2026-08-07. |
| `FM_PCD_SetAdvancedOffloadSupport()` | — | `[ASK]` gained an `ASK_UCODE_PACKAGE_NUMBER` version-check diagnostic; not functional. `[999-5.4]` confirms this gate is orthogonal to `USE_ENHANCED_EHASH`'s own init path (unconditional, not gated on `advancedOffloadSupport`) — ruled out as relevant to the ehash mechanism, 2026-08-07. |

**`Peripherals/FM/Port/fm_port.c`** (OO port-level PCD attach)

| Function | Relevance | Notes |
|---|---|---|
| `SetPcd()` | **E** | Read completely. Writes `FMBM_RCCB`/`fmbm_occb` with the raw MURAM CC-tree offset (via `FmPcdCcBindTree()`), unconditionally when `pcdEngines & FM_PCD_CC`. For `PRS_AND_KG(_AND_CC)`, also writes `fmbm_rfpne`/`p_BmiPrsNia` = `NIA_ENG_KG \| NIA_KG_CC_EN` (+`NIA_KG_DIRECT\|schemeId` for a direct scheme). No write to `FMBM_RICP`/`RIM`/`RPP`/`RCMNE` anywhere in this function. |
| `AttachPCD()` | — | Where `fmbm_rcmne`/`ocmne` actually gets written (gated on a `requiredAction` flag set elsewhere, not in `SetPcd()`) — confirms the earlier "arm vs attach" two-phase split this project already uses is correct. |
| `DetachPCD()` | — | Only caller of `FmPcdHcSync()` on the *teardown* side in the whole port-attach flow. |
| `FmPortSetFESupport()` / `FmPortDeleteFESupport()` | D | Confirmed dead code for the active `USE_ENHANCED_EHASH` build (see `CcUpdateParam()` above) — do not chase as a missing-wiring hypothesis. `[999-5.4]` note: this same source file also has an unrelated `FM_PORT_SetOhPortOfne()`/`FM_PORT_SetOhPortRda()` pair (OH-port-only NIA/DMA-attribute setters) — not applicable to a plain RX port's ehash dispatch. |

**`Peripherals/FM/Port/fman_port.c`** (flib register-programming layer, distinct from the OO `fm_port.c` above)

| Function | Relevance | Notes |
|---|---|---|
| `init_bmi_rx()` | E | Computes `tmp` from the driver's own `ic_int_offset`/`ic_size` config, then **unconditionally overwrites it**: `tmp = 0x00000007;` immediately before `iowrite32be(tmp, &regs->fmbm_ricp)`. This is where vendor's `FMBM_RICP=0x7` (vs. this project's mainline-derived `0x000e0203`) actually originates. `Peripherals/FM/inc/fm_sp_common.h`'s `DEFAULT_FM_SP_bufferPrefixContent_privDataSize` also changed `0→128` — a second, related buffer-layout divergence from pristine defaults. Confirmed identical in `[999-5.4]`. Assessed low priority: Rx-buffer/host-side layout, downstream of the FMan-internal classification decision. |

**`Peripherals/FM/HC/hc.c`** (Host Command frame dispatch)

| Function | Relevance | Notes |
|---|---|---|
| `FmHcPcdSync()` | **E, ruled out on this board** | `[SDK]`, read completely. Builds a genuine `HC_HCOR_OPCODE_SYNC` **Host Command frame** and enqueues it via `EnQFrm()` to the FMan's dedicated HC port/queue — a real hardware frame dispatch, not a CCSR register poke. This board's 210.10.1 microcode has **no Host Command support** (`caps=0x17` bit 3 clear, `fmd_host_cmd_send()` returns `-ENXIO`) — this exact mechanism is structurally unavailable regardless of whether it would help. Since `ExternalHashTableAddKey()`'s fast path never calls it anyway, this also weakens (not proves false) the general "missing sync on insert" hypothesis — vendor doesn't need *any* sync, HC-based or otherwise, for a plain insert either. |
| `FmHcPcdCcDoDynamicChange()` | — | `[SDK]` Same HC-frame pattern, `HC_HCOR_OPCODE_CC` — used for live AD swaps via Host Command; N/A on this board (no HC). |
| `FmHcPcdCcDoDynamicChangeWithAging()` / `FmHcPcdCcResetAgingMask()` | — | `[ASK]`-added, aging-specific HC opcodes (`HC_HCOR_OPCODE_CC_UPDATE_WITH_AGING`, `HC_HCOR_OPCODE_CC_AGE_MASK`). This branch's own `fe_ehash` does not request aging — not applicable. |
| `FmHcGetPort()` | — | Trivial accessor. |
| `FmHcPcdDbgUcodeHCmd()` / `FmHcPcdDbgUcodeTest()` / `FmHcPcdDMAreadTest()` | T | `[999-5.4]` only, gated `CONFIG_DBG_UCODE_INFRA`/`CONFIG_DMAR_TEST` — debug/DMA-test HC opcodes, all still Host-Command-frame-based (same "structurally unavailable on this board" reasoning as `FmHcPcdSync()` above). |

**`Peripherals/FM/Pcd/fm_manip.c`, `fm_plcr.c`, `Peripherals/FM/fm.c`, `fm_muram.c`, `Peripherals/FM/SP/fm_sp.c`** — swept, low/no relevance

| Function | Relevance | Notes |
|---|---|---|
| `FmPcdManipGetInternaltHmTdAndNonHmAd()` / `FmPcdManipLocalHMGetParams()` | — | HM-FE context-building support; only relevant if the ehash table's next-engine involves header manipulation (this project's test config does not). |
| `SetProfileNia()` | — | `[ASK]` gained 2 new policer next-engine targets; unrelated to ehash. |
| `FM_Init()` | — | `[ASK]` gained an extra `CONFIG_FMAN_ARM`-gated FM-reset step + `FM_ReadTimeStamp()`/`FM_GetTimeStampIncrementPerUsec()` (reads `fpm_reg->fmfp_tsp`, the FMan free-running HW timestamp — a *second*, independent consumer of timestamp infra alongside `extHashTsInfo`, used by the legacy `fmc`/`fmd` userspace stack via `lnxwrp_ioctls_fm.c`'s `FM_IOC_READ_TIMESTAMP`). No `FMFP_EXTC` reference anywhere in this file. |
| `get_muram_data()` | T | `[ASK]`-added, `EXPORT_SYMBOL`'d full-MURAM-snapshot debug helper — a potentially useful live-diff tool, not itself a finding. |
| `FM_VSP_GetRelativeProfileId()` | — | Trivial getter; buffer-prefix/IC-copy computation itself is untouched from pristine SDK in this file. |

**Wrapper/ioctl layer** (`src/wrapper/lnxwrp_*.c`) — swept, tooling-relevant only

| Function | Relevance | Notes |
|---|---|---|
| `fm_port_dump_regs_bmi()` | T | Gains a `USE_ENHANCED_EHASH`-conditional dump of `fmbm_rccb` (behind `DEBUG_ERRORS`) — a live-verification aid if reproducible in this project's own image. |
| `HASH_TABLE_GET_MISS_STAT` / `HASH_TABLE_DELETE` / `HASH_TABLE_REMOVE_KEY` ioctls | D | Stubbed to `E_INVALID_SELECTION` under `USE_ENHANCED_EHASH` — the legacy ioctl-driven hash-table management API is deliberately disabled; confirms `en_exthash_node`/direct-AD is the only supported path, consistent with everything above. Confirmed identically stubbed in `[999-5.4]`. |
| `fm_get_fw_rev()` / `fm_port_get_hwid()` / `fm_mac_set_allmulti()` | — | Unrelated additions. |
| `FM_IOC_READ_TIMESTAMP` / `FM_IOC_GET_TIMESTAMP_INCREMENT` | T | `[999-5.4]` only. Userspace-facing FMan free-running-timestamp ioctls (`FM_ReadTimeStamp()`/`FM_GetTimeStampIncrementPerUsec()`) — a possible future diagnostic hook, not itself a finding. |

**Confirmed irrelevant, swept and closed out**: `Peripherals/FM/MAC/fm_mac.c`/`.h`, `memac.c`, `dtsec.c`, `tgec.c` (Ethernet-address multicast hash filter — a different "hash" entirely, MAC-layer not classification-layer), `src/system/sys_io.c`, `src/xx/xx_arm_linux.c` (no barrier/ordering divergence from pristine SDK), all public headers not already cited above (`fm_ext.h`, `fm_mac_ext.h`, `fm_port_ext.h`, `fm_vsp_ext.h`, `fsl_fman.h`, `stdlib_ext.h`, `fm_cc_dbg.h` — the last is pure `display_*` debug-dump code, no functional logic), `lnxwrp_fm_port.c`'s error-discard-mask change (unrelated to ehash).

## 3. Ranked open leads (updated 2026-08-07, post-`999-5.4` forensic pass)

Superseded — see `arch/fman-config-value-ledger.md` for the current,
maintained list. As of this split, in priority order:

1. **`hash_bytes_offset` (`F-053`)** — cross-checked-MISMATCH found and
   fixed (retraction commit `ee276acb`), board retest pending. Was the
   top-ranked open lead as of this pass.
2. `KG_SCH_KN_PORT_ID` / EKFC extraction content — still open, not yet
   cross-checked (blocked on rebuilding a reliable hash-capture tool).
3. ~~`TIMESTAMP_EN` forced on with no `extHashTsInfo` pool~~ — **resolved
   2026-08-07**, board-tested not the blocker (Phase 1 retest).
4. ~~`en_exthash_node.word_2`'s `int_buf_pool_addr`/`global_mem_offset`~~ —
   **resolved 2026-08-07**, cross-checked-match.
5. `FMBM_RICP`/buffer-prefix divergence — resolved (explains the register
   value), but assessed low priority as a HIT-blocker (downstream of the
   classification decision).
6. `FmPortSetFESupport()`/ctrl-params-page timestamp fields, HC-based sync
   — both ruled out for this board's active config.
