# Phase 4 — ISA Inference (oracle-assisted)

**Status: ORACLE OPERATIONAL (2026-08-08)** — E1 (cosmetic id patch) and E2
(cold-region word patch) both PASS on .185; the delivery pipeline is proven
byte-exact (see `decomp/experiments.md`). **Kill gate stands: if control-flow
encodings are not cracked after ~2 weeks of oracle time, stop ISA work** and
fall back to the observability stack (fe_probe / pcd-snapshot / behavioral
CRC matching), which already answers the production questions.

## Goal

A *partial* opcode map: control flow + load/store + immediate-load + the
ALU/DMA idioms needed to read the Phase-6 target routines. Full ISA coverage
is explicitly **not** the goal (benchmark: multi-year academic efforts
reached ~40% on x86 microcode with worse tools than ours but no anchors).

## Why our odds are better than the benchmark

1. 24-blob same-ISA differential corpus (lineage proven 2026-08-07).
2. Pre-attributed 24-slot dispatch table + labeled anchor regions.
3. **Live mutation oracle** — the decisive advantage, below.

## Method A — field-entropy analysis (static, first)

Across the aligned corpus, classify bit-fields by entropy:
- opcode fields: low entropy, few distinct values, fixed position per class
- immediate fields: high entropy, uniformly distributed
- register selectors: uniform over a small set
- address/offset fields: cluster at known structure offsets

Cluster words by top-prefix (the histogram classes from Phase 2:
`0xb3ff`, `0xebc0`, `0xf04x`, `0xd841`, `0x04xx`…), then solve per-cluster
operand layouts. Candidate starting interpretations from arch doc §1.2:
`0xb3ffNNNN` ≈ load-imm16; `0xe9c9` ≈ store/index; `0x1409d0c4` ≈
call/branch bracket.

## Method B — mutation oracle on .185 (dynamic, decisive)

**As built and proven (2026-08-08, E1/E2 — supersedes the TFTP/`fman_ucode`
design sketched earlier):** the delivery path is `qef-patch.py` (patch words
/ header, recompute trailer CRC) → patch the live DTB's `fsl,firmware`
property in place (`--fdt`, located by magic + trailer CRC) →
`kexec -l … --dtb=PATCHED --reuse-cmdline && kexec -e` → kernel patch `0117`
`load_fman_ctrl_code()` re-streams the patched blob into IRAM (fires on
kexec boots). Full protocol + gotchas: `decomp/experiments.md`.

- **No SPI flash writes, no serial, no U-Boot env edits.** Recovery = any
  plain reboot (kexec is one-shot; eMMC boot pulls the pristine SPI blob);
  worst case = smart-plug power cycle (`restart-dut`).
- Observables: dmesg `0117` id line, DT property md5 (precomputed per
  patch), `pcd-snapshot` diff, link state, ping.
- Experiment classes:
  - *NOP-equivalence*: replace a word with a candidate NOP; unchanged
    behavior → NOP confirmed.
  - *Branch inversion*: flip candidate condition bits in a known-executed
    branch; behavior flips → condition field located.
  - *Word invalidation*: patch one word in a labeled region; gross crash =
    always-executed path; specific delta = semantic signal; no effect =
    dead/cold region (E2 proved this class works: w9055 mutation, zero
    delta).
- **Discipline**: one variable per experiment; record blob md5 + boot type
  per result; cold boot before any experiment that might have touched BMI
  (kexec warm-boot is acceptable only for cold-region and E1-class probes —
  hot-path experiments that stall the port need a full reboot between
  trials).

## Order of attack

1. Control flow: unconditional branch (`0xb7ff` confirmed-ish), call vs
   jump distinction (return-site analysis), conditional branch (via
   NOP/inversion oracle), return instruction.
2. Load-imm (`0xb3ff` class) + store (`0xe9c9` class) — validates against
   the known unrolled loop at slot 8's target.
3. ALU basics + register file size (from field entropy).
4. The `0x0421` new-in-210 class — crack inside a Phase-3-labeled
   FE-VM region where expected semantics constrain the decode.

## Deliverable

`decomp/isa/opcode-map.md` — per-encoding: bit layout, semantics, evidence
(static count + oracle experiment IDs), confidence. Feeds Phase 5's SLEIGH
spec. Every entry traces to at least one oracle experiment or a
cross-blob consistency proof.
