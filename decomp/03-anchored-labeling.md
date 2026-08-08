# Phase 3 — Anchored Semantic Labeling

**Status: IN PROGRESS** — anchor database landed (`decomp/maps/anchors.json`,
2026-08-07 night): 9 dispatch slots, 10 opcode-class readings, 2 memory
regions, 5 constant anchors, 3 verified negative results, 6 open questions.
Key early lesson: **descriptor types and FE-VM opcodes are never literal
32-bit constants in the code** (negative results N01–N03) — semantic labeling
must track mask/shift idioms and indexed dispatch tables, not opcode
literals.

## Goal

Attach *meaning* to Phase-2 regions using anchors — places where we know
from outside evidence what the code must be doing. This is the core trick
that distinguishes this program from blind ISA RE: we never face a blank
blob.

## Anchor classes

### A. Feature-presence differencing (primary)

The corpus gives four independent splits; a chunk present exactly where
feature X exists is X's code:

| Split | Isolates | Corpus support |
|---|---|---|
| 106.4 → 108.4 | CAPWAP tunnel offload (+fixes) | 6 SoC pairs |
| 106.4 → 107.4 | DSAR (deep-sleep auto-response) | 2 SoC pairs |
| same version, cross-SoC | SoC-specific vs core | 5× 106.1, 6× 106.4, 5× 108.x |
| 106/108 → 210 | FE-VM ehash, CC hash-table, aging, soft-parser ext | LS1043/LS1046 |

Feature descriptions for labeling come from the public repo's release-note
PDFs (`DPAA_DSAR_ReleaseNote.pdf`, `DPAA_IPACC_ReleaseNote.pdf`,
`DPAA_NG_CAPWAP_ReleaseNote.pdf`) and the readme capability matrix
(CC/IM/HC/IPF/IPR/HM/DSAR/CAPWAP per family).

### B. Documented-interface anchors

106/107/108 implement the **Host-Command interface** (cap matrix HC column;
absent from our 210 blob — caps `0x17` bit 3 clear). HC commands are
QMan-delivered with documented command codes (HCOR). The dispatch-table
slots in 106/108 that 210 dropped or rewrote are HC handlers — matching
them to documented command semantics gives *named, understood* routines
inside the shared codebase. Arch doc §1.2 already attributes slot 1 →
KeyGen HC (HCOR 0x01), slot 3 → dynamic CC update (HCOR 0x03), slot 19 →
aging CC update (HCOR 0x13, 210-only).

### C. Constant anchors

- **Present**: ethertype `0x00000800` at 210 w3016, w9721 (parser-adjacent).
  Extend the list: protocol numbers (6/17/1/47/50/51), ethertypes in
  half-word immediate form (`0x86DD`, `0x8100`, `0x8864`), well-known ports
  (GTP-U 2152), FE type codes 0x01–0x06, HM opcodes
  0x00/0x01/0x02/0x0C/0x0E.
- **Absent (negative results, 2026-08-07)**: CRC64 poly halves, NIA engine
  encodings (0x44/0x48/0x50). Lesson: expect *orchestration* code
  (register/offset arithmetic driving hardware blocks), not arithmetic
  kernels. Hash/parse math lives in silicon.
- MURAM offsets and register addresses from the arch reference doc are
  candidate immediates — any code word containing a known MURAM structure
  offset labels the routine that touches it.

### D. Cross-SoC invariance

Routines byte-identical from P2041 → T4240 → LS1046 are SoC-independent
core algorithms. Their stability across 15 years of builds makes them the
safest first reads and the calibration set for Phase-4 opcode hypotheses:
any claimed opcode encoding must decode these identically-meaning regions
consistently across all blobs.

## Work items

1. Build the anchor database: `decomp/maps/anchors.json` —
   `(blob, word_offset) → {label, evidence class, confidence}`.
2. Half-word immediate hunt (the full-word hunt missed 16-bit encodings).
3. Map 106/108 HC slots to documented HCOR command codes.
4. Color the Phase-2 structure map with A/B/C/D labels; every colored region
   becomes a named Phase-6 extraction target.

## Guardrail

Labels are hypotheses with evidence classes, not facts, until Phase 4/5
confirms the semantics of at least the region's entry instructions. The
2026-07 slot attributions carry "candidate identity / evidence" columns —
keep that discipline everywhere.
