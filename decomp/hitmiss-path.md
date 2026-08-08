# decomp/hitmiss-path.md — Locating the EXT_HASH HIT/MISS Discriminator

**2026-08-08 · Goal: understand exactly how the FMan 210.10.1 microcode
decides flow HIT vs MISS · Consumes: naming-map.md, maps/, the G3 SLEIGH ·
Feeds: the months-old ASK2 flow-MISS mystery**

## Why this matters

Flow-HIT was **proven working 2026-07-19** (ASK2 M3+M5 gates on .185) — the
original MISS was **F-053** (compare must start at DDR record **+8**, past the
8-byte link header, not +0). The current open MISS (task #26) is a
regression/config-drift, not a never-solved failure. Reading the discriminator
in the microcode makes the F-053/F-141/F-163 candidate chain **observed rather
than inferred** — the walker's `?op_e1 0x0008` immediate already corroborates
the record+8 key offset. The live confirmation experiment is **E-HM1** in
`decomp/experiments.md` (uses the known-HIT config as a silicon oracle).

## The HIT/MISS path (EXT_HASH FE, 6 steps)

1. Read KG hash from `ctx+0x48`.
2. Bucket index = `(hash >> ((6-shift)*8)) & mask` (mask from FE descriptor `w1`).
3. DMA-read the DDR bucket at `table_base + index*stride` (`table_base` = FE
   descriptor `w2/w3`).
4. Walk the 256B flow-record collision chain (DMA each record).
5. Byte-compare `keysize` bytes: record key (`+8`) vs the extracted key.
6. Match → HIT → `nextFE` (`w5`, MUX→ENQ). End-of-chain → MISS → `missNextFE`
   (`w6`, EXIT→DEALLOCATE).

## Localization (this session)

All 210-only, in the unique islands. Decompiled with the G3+ SLEIGH.

### Bucket-index setup — `w1928–1948` (fn `bucket_index`)

- **`w1936  ld r0,[0xd048]`** — reads the KG hash (`ctx+0x48`) into r0.
- **`w1947 ?ce r0,0x0189 ; w1948 ?cf r0,0x0241`** — `0xce`/`0xcf` ops *on the
  hash register* → the **shift/mask** that forms the bucket index.
- Decompile shows it assembles addresses from parts and dereferences them:
  `fman_mem_f4(uVar1, CONCAT22(dmem[0x6301], dmem[0x6303]))` — a hi/lo address
  built then fetched via **`0xf4`** (DMA/table-fetch candidate), working a
  second workspace at **`0xe000–0xe002`** alongside the `0xd0xx` context.

### The compare-and-dispatch walker — `w2837…` (fn `ehash_walker`, B01)

The `table_walker` loop nest (island 1, up to w5127). Decompiled head:

```c
fman_op_ef(ctx[0x98], 0);                    // key/context field touch
iVar2 = fman_test_dc(ctx[0xa8], 0x10f8);     // COMPARE (0xdc) -> flag
if (iVar2 == 0) { fman_alu_f0(uVar1, muram[0x13a8]); … }   // path A
else            { fman_alu_f0(uVar1, muram[0xba0]);  … }   // path B
```

`fman_test_dc` (**`0xdc`**) is confirmed as the comparator (its result is the
`if` predicate). It reads context/key fields (`ctx 0x98/0x9c/0xa8/0xb4`) and
branches two ways — the HIT/MISS-shaped structure. The `?op_e1 0x0008/0x000c`
in the walker (immediates **8** and **12**) match the DDR key offset (`+8`)
and keysize (12/13) — the byte-compare access.

**2026-08-08 (extended pass, 260 words, raw hex not just mnemonics):** the
snippet above is only the function *head*. A wider read (`w2837`–`w3096`,
`decomp/ghidra/scripts/FmanHashOffset2.py`) shows `ehash_walker` self-loops
onto its own entry (`jmp 0x00002c54`, at least twice) and contains ~8
repetitions of a DMA-issue/spin-wait/DMA-read idiom
(`op_f0`/`unk`/`park`/`op_f0`) — real confirmation of a per-record walk loop,
but the **actual per-byte key-compare was not located** in this window; the
visible `tst_dc` instances there are DMA-status polls, not an obvious
13-byte compare. The region is bigger/more tangled than this doc's original
estimate. `op_eb`/`op_e1` were confirmed to compute their offsets from
**compile-time immediate constants** (not a descriptor/register read) —
real evidence the record-header-skip mechanism exists in silicon, but
**orthogonal to, not a resolution of**, the F-053/2026-08-07
`hash_bytes_offset` AD-word controversy (see `decomp/findings.md`
2026-08-08-late for the full writeup, including a self-caught correction).

**2026-08-08 (later, key-compare candidate found):** extending the window
further (`w3096`–`w3500`, `decomp/ghidra/scripts/FmanKeyCompare.py`) turned
up several **tight backward loops** (5–34 words) — a much better structural
match for a compare than the DMA-poll-dominated region above. The tightest,
`w3304`→`w3309` (5-word body), reads from a fixed small address
(`op_f0 r3,[0x1b01]`) each iteration and immediately `tst_dc`s the result —
the right shape for "read next chunk; compare; loop." Two nearby loops
(`w3316`–`w3339`, `w3311`–`w3345`) **reuse the same base constants**
(`0x213d`/`0x2138`), consistent with several small per-**field** compare
blocks (SIP, DIP, PROTO, SPORT, DPORT — matching the silicon-confirmed
field-based MSB-first extraction) rather than one generic 13-byte memcmp.
**Not yet oracle-confirmed** — this is the best candidate found so far, not
a proven answer. Full writeup: `decomp/wedge-path.md` (found during the
same pass that investigated the microcode wedge mechanism).

## The encodings on the critical path (what to crack, and status)

| Encoding | Role | Status |
|---|---|---|
| `0xdc` `fman_test_dc` | the **compare** → `cc` | modeled (black box); the *key-byte* compare instance still not located (2026-08-08) — instances found so far are DMA-status polls |
| `0xe1` (imm 8/12) | key-offset / byte access | modeled; confirmed (2026-08-08) these are **compile-time immediates** in `op_eb`/`op_e1` pairs, not descriptor-driven — real but doesn't resolve the F-053 `hash_bytes_offset` question |
| `0xce`/`0xcf` (on hash reg) | **shift/mask** for bucket index | **partly cracked (2026-08-08)**: `0xe9 & 0xffff` = the 16-bit bucket mask (63/118 sites), confirmed via disassembler `regfld` decode to chain `e9(r0)→ce(r0)→cf(r0)`; the *operation* ce/cf perform (shift? by how much?) is still oracle-only — `ce`/`cf` have zero slaspec rules |
| `0xf4`/`0xf1` (assembled addr) | **DMA / table fetch** (DDR bucket+record) | modeled as mem black box; confirm it's the DDR read |
| `0x77`/`0x78`/`0xca`/`0xcb` | address-carrying (high `0xf8xx`) | unmodeled; DMA/addressing family |
| `brc` (b3ff/b43f/bc3f/**b03f/b83f/b41f/…**) | HIT vs continue-walk vs MISS | **family completed (2026-08-08, +274 branches)**; the opcode `_f`-suffix byte **encodes the condition** — map opcode→condition (oracle) to label HIT vs MISS |
| FE-descriptor reads | `w1` mask, `w2/w3` table_base, `w5` nextFE, `w6` missNextFE | trace which MURAM offsets |

**Single most important unknown**: the **DDR DMA-read** (buckets+records live
in DDR). `0xf4`/`0xf1` (which dereference assembled hi/lo addresses) are the
leading candidates. Confirming it reveals the bucket/record fetch that feeds
the compare.

## The decisive experiment (why HIT/MISS is the best oracle target)

Unlike the rest of the ISA, HIT/MISS outcome is **directly observable** via
the ASK2 flow-HIT harness (`fe_flow`/`fe_probe` debugfs, packet test). The
patch→kexec oracle (`qef-patch`) + that harness = confirmation:

**E-HM1 — force the compare (isolate the months-old MISS).** On the ASK2
engage path (flow inserted, packet sent), patch the `ehash_walker`'s
`fman_test_dc` (0xdc) branch to **always take the match path**. If flows then
HIT, the MISS is a **key-comparison failure** (not bucket-index or DMA) —
finally isolating it to step 5. If still MISS, the fault is upstream
(bucket-index/DMA, steps 2–4). Either way it splits the candidate space in
one measurement.

Follow-ups: patch the shift (`0xce/0xcf`) → observe bucket placement (step 2);
NOP the `0xf4` fetch → observe walk break (step 3/4); patch keysize/offset
(`0xe1`) → observe compare length (step 5).

**Prerequisite**: E-HM1 needs the ehash path *engaged* (ASK2 M3 flow-insert on
the dpaa1 branch) — the islands are cold on the mainline path (E2). This is
where the decomp oracle and the ASK2 flow-HIT work converge.

## Scope

Understanding the full HIT/MISS path = **~2 functions** (`bucket_index` +
`ehash_walker`) and **~5 encodings** (`0xdc` compare, `0xe1` byte-access,
`0xce/0xcf` shift, `0xf4/0xf1` DMA, the `brc` conditions), each
**oracle-confirmable** against observable HIT/MISS behavior. Narrow and
high-leverage — not the whole ISA.

## Silicon verification (2026-08-08, E-HM1 safe variant)

Engaged the FE-VM ehash path on eth4 and drove the matching flow from .106
(`decomp/experiments.md` E-HM1 RESULT). **Decomp findings confirmed on
silicon**: EXT_HASH descriptor `w1=0x0fff0c00` (mask 0x0fff, contextSize=13,
shift 0); flow in **bucket 0x008 = (sw_crc>>48)&mask** — verifying the
decomp's `bucket=(hash>>48)&mask` + the `e9&0xffff` mask.

**Correction (same day, later):** the initial write-up here concluded "the
silicon KG hash isn't the software CRC-64" (HW `0x50b43c9c…` ≠ SW
`0x600824e7…`). That conclusion doesn't survive cross-checking against
qdrant — the "KG-hash-vs-CRC64" hypothesis (2026-07-10 "Candidate 2") was
already independently disproven back on 2026-07-13 via a cleaner,
RSS-path-only measurement, and there's a documented precedent for
`hash_probe` capturing unrelated background traffic (not the intended test
flow) rather than a genuine algorithm mismatch. Retracted; do not cite the
"silicon KG hash ≠ CRC-64" framing above as settled. See
`decomp/findings.md` for the fuller reconciliation.

## Definitive result, 2026-08-08 (later): wiring confirmed correct, record never touched

Re-ran the armed test with the CRC-64-independently-reconfirmed
`portid=0x00` 14-byte key (`decomp/wedge-path.md`'s companion investigation
covers the same session). Two things nailed down that go beyond anything
above:

1. **`FMBM_RCCB` read back via `/dev/mem` immediately after arming equals
   the FE_ENTER offset exactly** (`0x00056c00` vs `enter_off=0x56c00`, no
   shift, no scaling). This rules out the historical F-165 failure mode
   (`arch/fman-microcode-210-programming-reference.md` §10.5a: an earlier
   "byte-correct MISS" turned out to mean the engage path never pointed the
   port at the built chain at all) — for *this* test, the port is
   genuinely, verifiably wired straight to the built ehash chain.
2. **The full 320-byte DDR flow record, read raw via `/dev/mem`, is
   byte-for-byte identical before and after sending 3 confirmed-transmitted
   matching TCP SYNs.** Not one byte changed anywhere in the record —
   not the stats fields, not any scratch/flag bit. Combined with (1) and
   the already-established bucket/linkage correctness, this is the
   strongest evidence yet that the comparator doesn't *fail to match* —
   it **never reaches this record at all** during live processing.

This sharpens (does not just repeat) the open question. With wiring, key
content, bucket index, and DDR linkage all independently confirmed correct,
the two live candidates are: **(A)** something upstream of the ehash walker
silently drops or redirects the frame per-frame, without wedging the port
(a different failure shape than this session's `park`-forever wedge
mechanism); or **(B)** the microcode's *live* bucket-index computation
doesn't match the `(hash>>48)&mask` software assumption — i.e. the
`ce`/`cf` opcodes chained onto the hash register after the `e9` mask
(`e9(r0,0xffff)→ce(r0,0x0189)→cf(r0,0x0241)`, found earlier this session,
semantics still unconfirmed) apply some further transformation, so the
microcode looks in a different bucket than 0x508 at runtime, finds it
empty, and correctly (from its own perspective) returns MISS without ever
touching this record. **(B) directly connects this session's disassembly
work to this silicon result** and is now the most concrete, targeted next
oracle experiment: patch one of `ce`/`cf`'s immediates and observe whether
the bucket a known-good key lands in changes.

**E-HM2 (live patch, `ce` immediate zeroed) result: no effect** — see
`decomp/experiments.md`. Zeroing `w1947`'s immediate (`0xce000189 →
0xce000000`) via the proven `qef-patch`→DTB→`kexec` pipeline produced no
change at all: the record was still byte-for-byte untouched. This doesn't
resolve (A) vs (B) on its own — it's consistent with either "`ce` isn't the
load-bearing part of bucket selection" or "the code isn't reached at all
regardless of what's patched there."

## Deep CFG trace toward the actual HIT/MISS dispatch (2026-08-08, later)

Went back to disassembly specifically to find the instruction(s) that read
the EXT_HASH descriptor's `w5` (`nextFEPtr`, HIT) vs `w6` (`missNextFE`,
MISS) and branch accordingly — using Ghidra's own flow APIs
(`getFlows()`), not manual hex arithmetic, to keep every branch target
exact (`decomp/ghidra/scripts/FmanCFGTrace.py`).

**Precisely located the compare-loop's exit fork:**
```
w3384  ldb r0,[0x86a]
w3385  op_eb r0,0x0
w3386  tst_dc r0,0xf838
w3387  brc -> w4187      (exit)
w3388  jmp -> w2837       (unconditional retry: back to ehash_walker's own entry)
```
This is the cleanest, most precise branch-fork found in the whole
investigation — a genuine two-way split at the CFG level, not an inferred
one.

**Followed the exit path (`w4187`) through several more forks** — all
CFG-traced, not hand-read — converging on:
- a large, self-looping "frame epilogue" region (`w12091`–`w12851`-ish,
  the tail of the image) doing generic per-frame bookkeeping: context
  field byte-swaps, timestamp/stat-adjacent writes (`ctx 0xd030`–`0xd03f`
  processed in a repetitive 8-byte-pair pattern), and its own internal loop
  (`w12313: jmp → w12133`);
- a genuine, structurally unambiguous **dispatch-table pattern** at
  `w40`–`w180` (repeating `brc → w104` / data-setup instruction pairs —
  a real switch-statement shape, not a guess). The epilogue's several exits
  land on *specific slots* within this table (`w87`, `w98`, `w114`,
  `w116`), not a generic "restart" point — meaning the epilogue selects
  *which* table entry to jump to based on some outcome, which is
  structurally exactly where a HIT/MISS-shaped selection would live.

**A tempting lead, traced fully and retracted.** The code selecting
between those table slots (`w12690`–`w12789`) contains `ld r1,[0x14]` and
`ld r1,[0x18]` — matching `w5`/`w6`'s exact byte offsets. Before trusting
this, checked the SLEIGH model: `ld` reads an **absolute** 16-bit address
(no register-relative mode exists in this ISA model), so `[0x14]` cannot
be a runtime-relocatable read of the EXT_HASH descriptor (which lives at a
different MURAM address every time it's built) — it must be a **fixed,
low-address scratch slot**. Traced where that slot gets *written* to check
whether it's populated *from* the descriptor: it isn't. `0x14` and `0x18`
turned out to be two of **eight** slots (`0x20, 0x24, 0x28, 0x14, 0x18,
0x1c, 0x2c, 0x60`) refreshed by a generic loop — each iteration does
`op_f0 r1,[0xb01]` (combine with a fixed hardware-status address, the same
DMA-poll idiom found earlier in `ehash_walker`) → error-check → write
back. The offset match is coincidental, not a targeted descriptor read.
**Retracted before being written up as a finding** — left here so the next
session doesn't re-chase the same coincidence.

**Where this leaves the search:** the exact instruction(s) that read
`nextFEPtr`/`missNextFE` and dispatch to `MUX`/`EXIT` have not been
isolated. The architecture appears to funnel many different FE-VM
execution paths through this same shared epilogue/dispatch-table
machinery — the HIT/MISS distinction may be carried as a *value* (in a
register or a context field not yet identified) through several layers of
shared code, rather than existing as one clean, isolated branch. Finding
it precisely likely needs either: (a) tracing the *specific* register/value
set right at the `w3387` fork (the `r0`/`r1`/`r8` values set via
`op_eb ...,0x90` / `0x1a` / `0x0` immediately before the two observed
`jmp → w12133` sites, which differ between the two paths and are a
plausible "which outcome" parameter carried into the shared epilogue), or
(b) an oracle test on the dispatch-table region itself once a specific,
well-motivated patch target is identified there.

Tools from this pass: `decomp/ghidra/scripts/{FmanHitMissDispatch,
FmanCFGTrace,FmanW4187,FmanEpilogues,FmanEpilogueTerminal,
FmanDispatchReturn,FmanSlotSelector,FmanScratchWrites,
FmanScratchOrigin}.py`.

## Register trace + decompiler re-read (2026-08-08, continued)

Tried the most targeted remaining lead: does the register asymmetry right
before the two `jmp → w12133` sites (`w4197: op_eb r8,0x1a` vs
`w4263: op_eb r1,0x0`) carry the HIT/MISS outcome into the shared epilogue?
Precisely decoded `regfld` for every instruction (not just modeled ones)
across the epilogue's first 127 words
(`decomp/ghidra/scripts/FmanRegTrace.py`). **Negative**: `r8` is referenced
**zero times** anywhere in that window — dead after being set, at least
locally. `r1` gets touched 29 times, but the first touch sits inside a
block `w12144`'s branch can skip entirely, so its incoming value isn't
reliably consulted either. This specific hypothesis doesn't hold up.

**More significant: re-read the "key-compare loop" through the decompiler
and revised the framing.** Earlier this session, the tight loop at
`w3304`–`w3309` (reading `[0x1b00]`/`[0x1b01]`) was described as the
best candidate for a byte-by-byte key-compare loop. Decompiling the region
(`decomp/ghidra/scripts/FmanCompareDecompile.py`) shows `fman_test_dc`
there checking a 4-byte fetch from `[0x1b00:0x1b04]` against fixed
patterns like `0x28f8` — and the *surrounding* logic (`decomp/experiments`
region) tests further fixed patterns (`0x27f8`, `0x20b8`, `0xf8b8`,
`0x20f8`, `0xf978`, `0x1938`, `0xf878`) that don't resemble literal
expected key-byte values. Given the shared `0xf8`-suffix across almost all
of them, `fman_test_dc` more plausibly performs a **masked status check**
(AND with a fixed mask, e.g. `0xf8` = top 5 bits, then compare) than a
literal byte-equality test — i.e. this is very likely more of the same
generic "poll a hardware resource's status" idiom found throughout, not a
software memcmp loop.

**Revised working hypothesis**: the EXT_HASH FE's actual byte-for-byte key
comparison may run in **dedicated hardware** (a comparator engine separate
from the general-purpose FE-VM RISC core), with the microcode's role
limited to: DMA-fetch the candidate record → trigger the comparator →
poll for completion (all the `park`/`tst_dc`/`op_f0` idioms found
throughout this region) → read a single match/no-match status bit. Under
this reading, the original `w3384`–`w3388` decision point (`ldb
r0,[0x86a]` → `tst_dc r0,0xf838` → branch) is *more* plausible as the real
HIT/MISS check, not less — it would be reading the comparator's final
completion/match status after all the surrounding polling infrastructure
finishes, rather than a byte-by-byte loop needing to be found separately.
**Still unconfirmed** — this is a structural reinterpretation that makes
the existing evidence more coherent, not a new proof. The oracle test that
would settle it: patch the immediate `0xf838` (or the byte at `[0x86a]`'s
source) and observe whether HIT/MISS behavior changes in a predictable
way.
