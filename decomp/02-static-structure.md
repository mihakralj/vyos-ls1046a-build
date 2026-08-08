# Phase 2 — Zero-ISA Static Structure

**Status: BASELINE LANDED (2026-08-07)** — `decomp/tools/structure-map.py`
committed; `decomp/maps/210.10.1-structure.json` + `decomp/maps/README.md`
written (85 candidate entries, 9 data runs, 210-unique islands w2972–w8096
and w8584–w12090, slot-19 corroboration, branch-class statistics with the
`0xb3ff`→branch hypothesis revision). Refinement continues opportunistically;
the remaining items are Phase 3/4 inputs, not blockers.

## Goal

Extract everything knowable about the code *without* knowing the instruction
semantics: section boundaries, function skeletons, data vs code, and a
cross-blob alignment map. Produces the structural scaffold Phase 3 labels
and Phase 4 cracks.

## Established (evidence in findings.md)

- **Fixed-width 32-bit RISC, BE, word-addressed.** 12,851 words (210).
  Entropy 6.29 bits/byte → uncompressed, unencrypted.
- **24-slot dispatch table at word 0** (bytes `0x00–0xBF`): populated slot =
  `0xb7ffXXXX` branch word + `0xffffffff` pad; `XXXX` = target word offset
  counted from byte `0xC0` (word 24). Slot 0's pad word carries the version
  stamp. Verified empirically: computed targets land on cross-tier
  byte-identical code runs.
- **Lineage**: the same table/encoding exists in all 23 public blobs → one
  ISA family across ~15 years of FMan generations.
- **Prefix histogram** (top-16-bit, 106 vs 210): stable shared classes
  (`0xb3ff` ~3%, `0xebc0`, `0xd841`, `0xbc3f`, `0xf040/41/42`, `0xa3ff`…)
  = base ISA; **`0x0421` expands 3→120 hits (0.04%→0.93%)** and
  `0x0400/01/02` grow ~2.6× — new-in-210 instruction class, prime FE-VM
  suspect.
- Cross-tier chunk overlap (relocation-tolerant): 106↔108 = 60–77%;
  210↔public = 30–42%.

## Work items

1. **CFG skeleton.** Branch encodings are half-known: harvest all words of
   `0xb7ff`-like classes, treat low-16-bit fields as candidate targets,
   keep those landing in-range. Function regions = branch-anchored extents.
   Block terminators are the smallest, most regular instruction subset —
   crack them first in Phase 4.
2. **Data/code separation.** `0xffffffff` pads (449 in 210), dispatch table,
   constant pools (the `b3ffNNNN`/`e9c9MMMM` unrolled loops at slot 8's
   target), alignment runs. Mark likely-data regions so the disassembler
   doesn't desync on them.
3. **Cross-blob alignment map.** Refine the 2026-08-06 chunk matcher:
   for every 210 region, record counterpart-in-106/108/107 or "unique".
   Output feeds Phase 3 directly (shared-base vs DSAR vs CAPWAP vs
   210-only coloring).
4. **Section hypothesis.** 210's growth concentrates at dispatch targets
   3 (1578), 6 (8574), 7 (12124), 19 (8621, 210-only), 22 (12388) — check
   whether unique-210 chunks cluster at those offsets.

## Deliverable

`decomp/maps/210-structure.json` (region map: extents, branch targets,
data/code tag, cross-blob alignment) + rendered map in
`decomp/maps/README.md`.

## Method guardrails

- A "function extent" is a hypothesis until Phase 4 confirms
  prologue/epilogue encodings. Mark confidence per region.
- Do not interpret semantics here. Structure only — semantic labels are
  Phase 3, opcode meaning is Phase 4. Mixing layers produced wrong
  conclusions in earlier sessions (the hypothetical spec-§12 opcode map
  being the canonical cautionary tale).
