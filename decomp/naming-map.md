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
| `0x00–0x07` | reserved | |
| `0x08` | **AD base pointer** | FE-VM entry (w214) reads the AD base from `IC[0xd008]` → MURAM slot `0x1b00` word0 |
| `0x10–0x1F` | reserved / FQD context | |
| `0x20–0x3F` | **Parse Result** (32 B = `struct fman_prs_result`) | authoritative field names in §8; per-byte layout kernel-confirmed |
| `0x40–0x47` | **Timestamp** (8 B) | IEEE-1588 if enabled |
| `0x48–0x4F` | **KG Hash Result** (8 B) | RAW CRC-64 (seed ~0, no final complement) |
| `0x50+` | beyond IC copy window | workspace scratch |
| `0xC4` | **Current-NIA / action slot** | `e9c9` guarded-store cascade (w75–w103) writes the incoming NIA/action here before action-table dispatch |

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
| 13 | w585 | `fm_ctl_action_table` (action-dispatch preamble) | [FACT] 2026-08-08 (was `ipr_timeout`) |
| 16 | w583 | `fm_ctl_action_table` (action-dispatch preamble) | [FACT] 2026-08-08 (was `ipr_timeout`) |
| 17 | w534 | `ipf` (HCOR 0x11) | [STRONG] |
| 19 | w8669 | `hc_cc_update_aging` (HCOR 0x13, ASK-added) | [FACT] three-way |

Structural anchors (rename by role): w2837 `table_walker`, w8676–w12072
`aging_walker_loop`, w12133 `frame_epilogue`, w12667–w12848 `pool_slot_walk`
(per-slot `ld [off] → op_f0 [0xb01] status-poll → brc w12849 → st [off]`
template over offsets 0x08–0x60, ~22 slots — **corrected 2026-08-08-2**:
w12849 `b3fffed6` imm `0xfed6` = −298 targets word **12551**, NOT w12830 —
the earlier "pool_status_loop loop-back" reading misdecoded `0xfed6` as
`0xffed`; w12849 is the guard-failure convergence point that jumps to the
shared status-check region w12551 (`[0xf808]` FM_CTL status + `op_eb
r1,0x30c` params-base compute), not a self-loop), w9040–w9520 `enq_builder`
(ENQ constant materialized at w9055).

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

## 7. Address-window map — 2026-08-08 (full-image census)

**Complete address-window census from the regenerated listing.** Every
`ld`/`st`/`m_77`/`m_78`/`m_f1`/`m_f4`/`op_f0` operand was counted. The FMan
210 controller has ONE flat 16-bit data address space; the regions are:

| Window | Accesses | Role | Evidence |
|---|---|---|---|
| `0xd000–0xd0ff` | ~787 | **per-task Internal Context (IC)** — §1.1 | `ld`/`st` hot offsets 0x08/0x18/0x0c/0xb8/0xc0/0xd4 |
| `0x0300 + n·0x800` (n=0..12) | 502/266/184/105/62/49/43/30/19/18/17/15/13 | **per-tnum MURAM workspace slots** (0x800 = 2048 B apart) | every slot shares a header at `+0x500–0x548` (below) |
| `0x0800`/`0x1000`/`0x1800` | 1023/929/597 | deeper tier of the same slot array | same `+0x500` header offsets |
| `0x8000–0x80ff` | 232 | **AD-base / frame-command window** — CC reads the AD base from `[0x8040]`/`[0x8050]` | w1854 `ld r1,[0x8040]` immediately before AD-type extract |
| `0xf800–0xf8ff` | 125 at `+0x00` | **FM_CTL status / current-NIA window** — the dispatcher's `[0xf800]` slots | w603 `m_78 r2,[0xf800]` right after the action table |
| `0xf900`/`0xfb00`/`0xfc00` | 18/78/20 | more FM_CTL status slots (parse-result echo, error status) | `m_78`-only (`0x78XX` ops) |
| `0xc000–0xc0ff` | 184 | per-frame IC/command region | |

**[IMPORTANT 2026-08-09, E-HM18]** the `0xf000`/`0xf800` dmem windows are
FM_CTL **engine-internal** (the microcode's own flat 16-bit data space),
NOT host-visible MURAM at physical 0x1A00000+offset. Live /dev/mem reads at
0x1A0F000/0x1A0F800 on BOTH boards return volatile aliased content (mDNS
service strings "Spotify Desktop Launcher", "_spotify-connect._tcp.local"
on .185; 0x0400015E/0x04008040 on .106) — packet/config data, not a static
handler table. The host cannot read or populate the FE-type dispatch slots
that `2c3ff000` at w242 indexes. E-HM17's "pristine reads return clean
0x00000000" was a transient artifact (misread of the same aliased window);
CAND-1's original "aliased to volatile userspace memory / not a
kernel-populated MURAM table" conclusion is RESTORED.

**Common per-slot header at `+0x500–0x548` (every workspace slot):** byte
offsets `0x500/0x502/0x504/0x508/0x510/0x518/0x534/0x538/0x53c/0x540` appear
in *all* 13+ 0x800-slots — a fixed 0x48-byte per-tnum control block the FE-VM
writes uniformly (pointers/counters). **[?]** individual field identity open.

**`0x79XX` = an instruction family (DISPATCH STUBS), NOT data** (re-corrected
2026-08-09, E-HM18). The 2026-08-08 "0x79XX = DATA" correction was based on
the old (WRONG) `b7ff` decode `(48+imm16)*4` which made w585 unreachable. With
the corrected signed-relative-word model, **w0 (`b7ff0249`) → w585**, so w585
is the FIRST instruction executed: `0x7902f800`. The 16 words `w585–w606`
(`0x7902f800`, `0x7904f800`, …, `0x791ef800`, `0x7900f800`) are a DISPATCH
CHAIN of `0x79`-family action-code stubs: constant low16 `0xf800`, middle
byte = action code (0x02 ENQ, 0x04, 0x06 CC, 0x08/0x0a IND_MODE, 0x0c HC,
0x0e POP_TO_N_STEP, 0x10/0x12/0x14/0x18 BMI-fetch variants, 0x1a PRE_BMI_ENQ,
0x1e DISCARD, 0x00 wrap) → each "dispatch action code <n> via the `0xf800`
handler window". w603 (`m_78 r2,[0xf800]`) reads the resolved pointer. **[?]**
exact 0x79 semantics (indirect jump vs load) open — but they are CODE, and
the whole-image census shows only these 17 `0x79`-family words.

**AD-type extraction idiom (CC engine):** `c600001e` (shift right 30) extracts
the **AD type field bits[31:30]** (`0x40000000` CONT_LOOKUP→1, `0x80000000`
RESULT→2, `0xc0000000` BYPASS→3) — the CC engine's dispatch on the action
descriptor at RCCB. Sibling `c600001a` / `op_eb r14,0x1a` (shift 26) sites
(w121/192/241/347 in the CC region; w9067/9111/9241/9435/9487 in the FE
interpreter) extract the **FE type field bits[31:26]**. Both confirm anchors
N01–N03: type/opcode fields are decoded field-wise, never compared as full
32-bit constants.

**`e9c9` guarded-store cascade (CC dispatch stub, w75–w103):** pairs of
`b3ff <skip>` + `e9c9 <imm>` with immediates `0x0e/0x06/0x1e/0x16/0x3e/0x36/
0x01/0xc2/0x142/0x200/0x40a/0x802` — conditional stores to `ctx[0xd0c4]` (the
current-NIA/action slot) that dispatch on the incoming action before
converging at w104 → `b7ff0217` → w583 (action table). The imm values straddle
the FM_CTL action-code space (0x06/0x0e/0x1e) and NIA values (0x200
KG-CC_EN, 0x40a/0x802) — **[?]** exact compare semantics open.

## 8. Parse-Result / Result-Array field vocabulary (authoritative)

The 32 B parse result at **IC `0x20–0x3F`** is `struct fman_prs_result`
(NXP-copyrighted mainline kernel `drivers/net/ethernet/freescale/fman/
fman.h`, cross-checked against AN4760 Table 23 and the FMC result-array
variable names in LSDKUG Table 79). Per-field names and IC offsets:

| IC offset (IC `0x20` base) | Field | Meaning |
|---|---|---|
| `+0x00` (`IC 0x20`) | `lpid` | Logical port id |
| `+0x01` (`0x21`) | `shimr` | Shim header result |
| `+0x02` (`0x22`) | `l2r` | Layer 2 result flags (u16) |
| `+0x04` (`0x24`) | `l3r` | Layer 3 result flags (u16; bit15 IPv4, bit14 IPv6) |
| `+0x06` (`0x26`) | `l4r` | Layer 4 result flags (u8; bit6 UDP, bit5 TCP) |
| `+0x07` (`0x27`) | `cplan` | Classification plan id |
| `+0x08` (`0x28`) | `nxthdr` | Next header EtherType/IP-proto (u16) |
| `+0x0A` (`0x2A`) | `cksum` | Running checksum (u16) |
| `+0x0C` (`0x2C`) | `flags_frag_off` | Flags & fragment offset of last IP header (u16) |
| `+0x0E` (`0x2E`) | `route_type` | IPv6 routing ext header routing type |
| `+0x0F` (`0x2F`) | `rhp_ip_valid` | Routing Ext Header Present; LSB = IP valid |
| `+0x10` (`0x30`) | `shim_off[0]` | Shim offset, first |
| `+0x11` (`0x31`) | `shim_off[1]` | Shim offset, last — **read by frame_epilogue** |
| `+0x12` (`0x32`) | `ip_pid_off` | IP PID (last IP-proto) offset |
| `+0x13` (`0x33`) | `eth_off` | ETH header offset |
| `+0x14` (`0x34`) | `llc_snap_off` | LLC/SNAP offset |
| `+0x15` (`0x35`) | `vlan_off[0]` | VLAN offset, first |
| `+0x16` (`0x36`) | `vlan_off[1]` | VLAN offset, last |
| `+0x17` (`0x37`) | `etype_off` | EtherType offset |
| `+0x18` (`0x38`) | `pppoe_off` | PPPoE offset |
| `+0x19` (`0x39`) | `mpls_off[0]` | MPLS offset, first |
| `+0x1A` (`0x3A`) | `mpls_off[1]` | MPLS offset, last |
| `+0x1B` (`0x3B`) | `ip_off[0]` | IP offset, first (outer) — **read by frame_epilogue** |
| `+0x1C` (`0x3C`) | `ip_off[1]` | IP offset, last (inner/tunneled) |
| `+0x1D` (`0x3D`) | `gre_off` | GRE offset |
| `+0x1E` (`0x3E`) | `l4_off` | L4 header offset — **read by frame_epilogue** |
| `+0x1F` (`0x3F`) | `nxthdr_off` | Parser end-point (next-header) offset |

**Base-convention warning (2026-08-06 derived finding):** AN4760 / FMC
"parse-array byte" numbering counts from the **start of the annotation
region** (16 B reserved + 32 B parse result), i.e. `AN4760_byte =
fman_prs_result_offset + 16`. `struct fman_prs_result` counts from the
parse result proper. IC offsets (`FMBM_RICP iciof/iceof/icsz`,
`contextOffsetInWS`) count from the IC base — be explicit which base is
meant; a silent 16 B mismatch is a known bug class. Hardware annotation
layout: `[16 B reserved][32 B parse result][8 B timestamp][8 B KG hash]`
(`DPAA_HWA_SIZE = 48`).

**frame_epilogue (w12133) relevance:** the per-frame status-assembly loop
reads IC parse-result bytes `0xd031`–`0xd042` — i.e. `shim_off[1]`,
`ip_pid_off`, `eth_off`, `llc_snap_off`, `vlan_off[0..1]`, `etype_off`,
`pppoe_off`, `mpls_off[0..1]`, `ip_off[0..1]`, `gre_off`, `l4_off`,
`nxthdr_off` — the header-offset tail of the parse result (plus one byte
past `0x3F` into timestamp, per the observed window). These are named
fields, not opaque bytes.

**Flag bits (kernel-confirmed):** `l3r` bit15 = IPv4 present, bit14 = IPv6
present; `l4r` bit6 = UDP, bit5 = TCP.
