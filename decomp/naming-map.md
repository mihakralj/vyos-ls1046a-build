# decomp/naming-map.md — Authoritative Naming & Structure Map for the Disassembly

**2026-08-08 · Synthesized from qdrant + `arch/fman-*.md` · Consumed by the
Ghidra `fman-risc` module + labeling scripts**

The `fman-risc` disassembly invents ad-hoc names (`dmem`, `r3`, `unk`,
`slotNN_wM`). This file maps those to the **authoritative NXP/SDK/project
vocabulary** already established in the arch docs and qdrant, so Ghidra's
output is consistent with the rest of the corpus and so labeling actually
adds meaning. Confidence tags: **[FACT]** documented/verified,
**[STRONG]** well-supported hypothesis, **[?]** plausible, unverified.

## 1. The two data regions the microcode addresses (the big helper)

SLEIGH currently lumps all loads/stores into one `dmem` space. They are
really **two named regions** of the microcode's 16-bit data space:

### 1.1 `ctx` = per-frame context / Internal Context (IC) / FE workspace — `0xd000–0xd0ff`

**[STRONG]** The `0x04xx`/`0x1xxx` classes' `0xd0xx` addresses are the
FMan Controller's **per-task (per-frame) context**, i.e. the frame's
Internal Context / FE workspace (iter-42 called it the "per-task context
page"; `arch/fman-fe-ehash.md` §8.1). The IC has a **documented sub-layout**
(`arch/fman-microcode-210-programming-reference.md` §12.2, corrected
2026-07-13) — so a `ctx` read is a *named field access*, not an opaque one:

| IC offset | Field | Notes |
|---|---|---|
| `0x00–0x1F` | reserved | |
| `0x20–0x3F` | **Parse Result** (32 B) | protocol IDs, L3/L4 header offsets, shim/next-header |
| `0x40–0x47` | **Timestamp** (8 B) | IEEE-1588 if enabled |
| `0x48–0x4F` | **KG Hash Result** (8 B) | RAW CRC-64 (seed ~0, no final complement) |
| `0x50+` | beyond IC copy window | workspace scratch |

Total IC ≈ 246 B (`0xF6`). **[?]** If `ctx` base = `0xd000`, then
`ctx[0xd020]`=parse_result, `ctx[0xd048]`=kg_hash. This base alignment is a
hypothesis to confirm (the exact `0xd000→IC-0x00` mapping is not proven; an
oracle probe on a parse/hash-dependent `ctx` read would settle it). iter-42
observed the AC_CC handler reading `0xd00c/0x14/0x18/0x1c/0x24/0x98/0x9c`.

### 1.2 `muram` = MURAM structures — `~0x0300–0x4b00`

**[FACT]** The `0xf042` class (addrs `0x0300–0x4b00`) and `0x1080` class
(`0x0843–0x087d`) address on-chip **MURAM**. Static/controller-owned
structures documented in the arch docs:

- **FM_CTL params page** (per-port, 256 B — `t_FmPcdCtrlParamsPage`,
  reference §6): `+0x40` misc (`ALWAYS_ON=0x100`, `OFFLOAD_SUPPORT_EN=0x40000000`),
  `+0x44` `errorsDiscardMask=0x012ee0e8`, `+0x48` discardMask, `+0x50`
  postBmiFetchNia, `+0x54` internalFEBufferManagementIndexAddr, `+0x58`
  internalFEBufferDepletionCounter.
- CC match tables / 16 B Action Descriptors / ≤256 B HMCD chains / FE objects.
- MURAM base = CCSR `0x1A00000` (`ccsr_fman.muram` at FMan offset 0);
  384 KB populated.

**[?]** The `0x1080`-class hot struct at `0x0843–0x087d` (115 accesses) is a
prime candidate for the FM_CTL params page or a per-task register block —
identity still open (`anchors.json` Q03). Its tight 58-byte window matches a
~256 B page's active fields.

**Ghidra action**: create two named memory blocks in the data space —
`ctx` at `0xd000` (256 B) and `muram` at `0x0300` — and name the IC
sub-fields (`ctx_parse_result`, `ctx_kg_hash`). Then `ld r3,[0xd0d4]`
reads as `ld r3, ctx+0xd4`.

## 2. Constant vocabulary (label immediates / data words)

**[FACT]** These are the descriptor/opcode constants the microcode builds or
tests. Define them as Ghidra equates/symbols so `unk 0x0201,0x0000` and data
words show names. (Recall N01/N02: FE *type* words rarely appear as literals;
these are for the ones that do — ENQ `0x02010000`, MUX `0x04000000` — and for
labeling MURAM descriptor data the microcode writes.)

| Value | Name | Source |
|---|---|---|
| `0x01000000` | `FE_TYPE_HM` | reference §7 |
| `0x02000000` / `0x02010000` | `FE_TYPE_ENQ` / `FE_ENQ_W0` | §7; K01 sites w2184/2289/9055/9307 |
| `0x03000000` / `0x03800000` | `FE_TYPE_EXIT` / `FE_EXIT_DEALLOCATE` | §7 |
| `0x04000000` | `FE_TYPE_MUX` | §7; K02 |
| `0x05000000` | `FE_TYPE_TRANSITION` | §7 |
| `0x06000000` | `FE_TYPE_EXT_HASH` | §7.2 |
| `0x40800000` | `FE_ENTER_W0` (CONT_LOOKUP\|NIA_ORDER_RESTOR) | §5; N03 |
| `0x000000F6` | `OPC_FE_ENTER` (=246) | §5 |
| `0x40000000` | `AD_CONT_LOOKUP` | RM 8.7.4 |
| `0x80000000` | `AD_RESULT_DATA_FLOW` | |
| `0x00000000` | `AD_RESULT_CF` | |
| `0xc0000000` | `AD_BYPASS` | |
| `0x20000000` | `AD_NADEN` / `PLCR_DIS` | |
| `0x00800000` | `HMCD_LAST` / `NIA_ORDER_RESTOR` | (bit 23, context-dependent) |
| `0x012ee0e8` | `ERRORS_DISCARD_MASK` | §6 params page |
| `0x00007fff` | `EHASH_MASK` (32768 buckets) | §7.2/§10 |
| `0x80500002` `0xC04C0000` `0x80000006` | `KGSE_MODE_RSS` / `_PLCR` / `_AC_CC` | 2026-06-23 verify |
| `0x00180006` / `0x001C0006` / `0x801C0006` | `KGSE_MV_4TUPLE` / `_5TUPLE` (historical) / `_6TUPLE` (current target) | |

**NIA engine field** (low half-word / bits[22:16], `arch` §5): `0x44`=HWP,
`0x48`=HWK, `0x50`=BMI. Engine index table (`(nia>>20)&0xf`): `0`=DONE,
`2`=PRS, `4`=HWK, `5`=BMI, `6`=QMI_ENQ, `7`=QMI_DEQ, `8`=FM_CTL_A,
`9`=FM_CTL_B, `A`=PLCR, `B`=FR, `C`=CC. (K03 sites carry these in low16.)

**HM opcodes** (reference §8; label HMCT data): `0x00` RMV_HEADER, `0x01`
RMV_BYTES, `0x02` INSRT_REPLACE, `0x08` L2_RMV, `0x0B` VLAN_PRIORITY, `0x0C`
IPV4_UPDATE, `0x0D` INTERNAL_L3_REPLACE, `0x0E` TCP_UDP_UPDATE, `0x34`
HMAN_OC_IP_MANIP, `0x35` HMAN_OC.

**Protocol constants** (parser compares, K04/K05, low16): `0x0800` IPv4,
`0x0806` ARP, `0x86DD` IPv6, `0x0868` GTP-U(2152). (`0x8100`/`0x8864` absent —
hard parser strips tags.)

## 3. Dispatch slots → function names (rename the entry functions)

**[STRONG]** Map slot index → HCOR opcode / NIA engine (2026-08-06 slot-map;
`anchors.json` A-series). Use these to rename Ghidra's `slotNN_wM` functions:

| Slot | target | Name | Confidence |
|---|---|---|---|
| 0 | w633 | `hc_policer_profile` / `done` | [?] |
| 1 | w653 | `hc_keygen` (HCOR 0x01) | [STRONG] |
| 2 | w651 | `hc_sync` / `prs` | [?] |
| 3 | w1626 | `hc_cc_update` (HCOR 0x03) | [STRONG] |
| 4 | w2628 | `hwk` / aging (HCOR 0x04) | [?] |
| 5 | w2432 | `bmi` | [?] |
| 6 | w8622 | `qmi_enq` | [STRONG] |
| 7 | w12172 | `qmi_deq` | [STRONG] |
| 8 | w80 | `fm_ctl_a` (guarded-store cascade) | [FACT] byte-identical all tiers |
| 9 | w227 | `fm_ctl_b` | [?] |
| 11 | w406 | `frame_replicator` | [?] |
| 12 | w75 | `cc_dispatch` (fixed 27 w all tiers) | [STRONG] |
| 16 | w583 | `ipr_timeout` (HCOR 0x10) | [STRONG] |
| 17 | w534 | `ipf` (HCOR 0x11) | [STRONG] |
| 19 | w8669 | `hc_cc_update_aging` (HCOR 0x13, ASK-added) | [FACT] three-way |

Structural anchors (rename by role): w2837 `table_walker`, w8676–w12072
`aging_walker_loop`, w12133 `frame_epilogue`, w12849 `exit_stub`,
w9055 region `enq_builder`.

## 4. SDK function names — reference only (NOT microcode symbols)

**Caveat**: the SDK/kernel function names below are **aarch64 driver code**,
not microcode. They name the *algorithms* the microcode co-implements — use
them to label the *recovered microcode routine's role*, never as if they were
the microcode's own symbols.

- `get_indexed_hash_bucket` / `fman_pcd_ehash_bucket_index` — CRC-64 →
  `(crc>>((6-shift)*8))&mask`. **The CRC-64 is silicon, not microcode**
  (poly `0xC96C5795D7870F42` absent from code words), so the microcode's
  bucket step is a shift+mask over the KG-hash at `ctx+0x48`, not a CRC loop.
- `FmPcdCcBuildFE` / `FmPcdCcBuildContextByFE` — FE context construction (the
  `enq_builder` region's role).
- `ExternalHashTableAddKey`/`Set`/… — kernel↔cdx.ko ABI, DDR-side; **not**
  in the microcode.
- **Golden reference**: RSR 10.3.0.B1 kernel uImage (kallsyms recovered via
  vmlinux-to-elf) is a *diffable aarch64 reference* for these algorithms — a
  cross-check for behavior, not a source of microcode symbol names.

## 5. Structure layouts worth pre-defining as Ghidra types

For when the disassembly touches MURAM descriptors (define as structs so
`st muram[…]` sequences read as field writes):

- **EXT_HASH FE** (28 B, reference §7.2): w0 type\|ctxOffWS\|aging, w1
  `hashMask<<16 | (ctxSize-1)<<8 | hashShift`, w2/w3 DDR bus addr, w4
  missResult, w5 nextFEPtr(MUX), w6 missNextFE(EXIT).
- **ENQ FE** (16 B): w0 `0x02010000`, w1 FQID(24-bit).
- **Action Descriptor** (16 B, `t_AdOfTypeResult`): 0x0 fqid, 0x4 plcrProfile,
  0x8 nia (type[31:30]\|flags\|opcode[3:0]), 0xC res.
- **FM_CTL params page** (256 B) — §1.2 above.
- **DDR flow record** (256 B): 0x0 flags, 0x2/0x4 next_entry, 0x8 key[keysize],
  then next-FE MURAM offset.

## 6. How to apply (Ghidra)

1. **Memory blocks**: create `ctx`@`0xd000` (256 B) + `muram`@`0x0300` in the
   data space; label IC sub-fields.
2. **Equates**: register §2 constants so immediates/data show names.
3. **Function renames**: apply §3 to the dispatch-target functions.
4. **Structs**: define §5 layouts; apply at the MURAM store sites.

A starter script can bulk-apply §3 (function renames) + §2 (equates) via the
GhidraMCP `rename_function`/`set_decompiler_comment` tools or a headless
GhidraScript. This is the immediate next labeling step once the G3 register
model firms up the store operands.
