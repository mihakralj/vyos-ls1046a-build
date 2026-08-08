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

## The encodings on the critical path (what to crack, and status)

| Encoding | Role | Status |
|---|---|---|
| `0xdc` `fman_test_dc` | the **compare** → `cc` | modeled (black box); semantics = compare, verify what & how many bytes |
| `0xe1` (imm 8/12) | key-offset / byte access | modeled; likely the per-byte compare/access |
| `0xce`/`0xcf` (on hash reg) | **shift/mask** for bucket index | **partly cracked (2026-08-08)**: `0xe9 & 0xffff` = the 16-bit bucket mask (63/118 sites); the `>>48` shift is oracle-only |
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
decomp's `bucket=(hash>>48)&mask` + the `e9&0xffff` mask. **MISS root cause
found**: HW hash `0x50b43c9c…`→bucket `0x0b4` ≠ SW CRC-64 `0x600824e7…`→
bucket `0x008` (`pkt_count=0`) — the silicon KG hash isn't the software CRC-64
(the 2026-07-10 "Candidate 2"), so the frame and the flow land in different
buckets. The decomp's bucket *math* is correct; the open question moves to the
KeyGen `kgse_hc`/EKFC config (why the KG hash ≠ CRC-64 on this build).
