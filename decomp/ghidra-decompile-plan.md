# decomp/ghidra-decompile-plan.md — Plan: Decompile 210.10.1 with Ghidra

**2026-08-08 · Consumes: `decomp/ghidra-setup.md` (toolchain), `decomp/maps/`
(CFG + anchors), `decomp/experiments.md` (oracle) · Feeds: Phase 6 targets**

> **STATUS 2026-08-08: G0–G3 landed (structure); ALU semantics oracle-gated.**
> `fman-risc` SLEIGH decodes control flow (G1, cross-validated vs cfg-map),
> memory access (G2: `ld r3,[0xd0d4]`), and the top ALU/op classes (G3:
> `0xeb/f0/d8/db` as black-box pcodeops, `0xdc`→`cc`). The slot-19 aging
> handler now decompiles to readable pseudocode with tracked registers, ctx/
> MURAM accesses, and real conditionals (no `while(true)` collapse). Naming
> from `decomp/naming-map.md` applied via `FmanLabels.py`. Working driver =
> headless `analyzeHeadless` + GhidraScript. **Remaining (oracle-gated)**:
> concrete ALU semantics per `fman_alu_XX`, the `test_dc` predicate, load/store
> direction, full 8-bit register field — the multi-week gamble; structure is
> already decompiled and readable.

## The core problem, stated once

Ghidra cannot disassemble one byte of this blob without a **SLEIGH processor
module** for the FMan controller RISC ISA — an ISA with no public reference.
So "use Ghidra to decompile 210.10.1" is not an import-and-go task; it is:

> author `fman-risc` SLEIGH incrementally from the ISA facts we have already
> silicon-validated, cross-validate each layer against our independent
> `cfg-map.py` results, confirm every new encoding on the mutation oracle,
> and drive labeling/decompilation with the 27 Ghidra automation API tools.

Ghidra is a **force multiplier on the ISA we crack**, not a shortcut around
cracking it. The 2026-07-11 "don't blind-RE the ISA" decision still holds for
*blind* RE; this plan is the bounded, anchored, oracle-checked version, and it
keeps that decision's kill gate.

## What we already have (the SLEIGH seed)

Silicon/statistically established (see `anchors.json`, `findings.md`):

- Fixed **32-bit big-endian** instructions, word-addressed; 12,851 words at
  blob offset 244. Entropy 6.29 → real unpacked code.
- **Control flow is cracked** (relative-branch model 100% in-range, E1/E2
  oracle pipeline live):
  - `0xb7ff` = **absolute branch**, target word `= 48 + imm16` (verified by
    cross-tier byte identity at computed targets).
  - `0xb3ff / 0xb43f / 0xbc3f` = **relative conditional branch**, target
    `= PC + sext16(imm16)` words.
  - `0xa3ff` = long relative branch / **call** candidate.
  - `0xb7df` = **park/halt** (branch-to-self).
- **Memory/context classes**: `0x04xx` + `0x1xxx` = per-task **context-page**
  access (addresses `0xd0xx`); `0xf042` = memory (`0x0300–0x4b00`); `0x1080`
  = one hot struct (`0x0843–0x087d`); `0x0082` = offset-0 op.
- **No data-table dispatch** (Q05): FE-type dispatch is a compare-and-branch
  cascade, not a jump table.
- **Structure map**: 2,201 blocks; walker loop nest at **w2837**; aging
  walker **w8676–w12072** (slot 19 enters it); epilogue join **w12133**; exit
  stub **w12849**; slots are 1–3-word trampolines into bodies.
- **Anchors** (`anchors.json`): 9 slot identities, 10 opcode-class readings,
  memory regions, constant sites, negative results.

Unknown (the SLEIGH gaps to fill): the **register file** (size, operand-field
position), the **ALU** ops, the **condition** encoding for conditional
branches, exact **load/store** operand layout.

## Stages

### G0 — Load the blob, raw (no SLEIGH yet)

Import `fman-ucode-210.10.1.bin` **as Raw Binary**, base `0x0`, so byte
`4·w` = word `w`. With no processor module it is just bytes, but this proves
the import path and lets us define the pad regions (`0xffffffff` runs) and the
24-slot dispatch table as data. Low value alone — its purpose is to host the
SLEIGH module next.

### G1 — SLEIGH v0: control flow only (the cross-validation gate)

Author a minimal `fman-risc` processor module implementing **only** the
branch family + a fixed-width **catch-all** so disassembly never desyncs.
Files under `Ghidra/Processors/fman-risc/data/languages/`:
`fman-risc.ldefs`, `.pspec`, `.cspec`, `.slaspec` (compiled by the ARM64
`sleigh` binary we built). Skeleton (iterate against the compiler):

```sleigh
define endian=big;
define alignment=4;
define space code     type=ram_space      size=4 default;   # byte = 4*word
define space register type=register_space  size=4;
define space ctxt     type=ram_space      size=2;           # 0xd0xx context page
define register offset=0x40 size=4 [ pc ];

define token instr(32)
    prefix16=(16,31)  imm16=(0,15)  simm16=(0,15) signed  regA=(16,19);

abs_dest: rel is imm16       [ rel=(48+imm16)*4; ]        { export *[code]:4 rel; }
rel_dest: rel is simm16      [ rel=inst_start+simm16*4; ] { export *[code]:4 rel; }

:BR   abs_dest is prefix16=0xb7ff & abs_dest { goto abs_dest; }
:BRC  rel_dest is prefix16=0xb3ff & rel_dest { if (cc) goto rel_dest; } # cc: provisional
:BRC  rel_dest is prefix16=0xb43f & rel_dest { if (cc) goto rel_dest; }
:BRC  rel_dest is prefix16=0xbc3f & rel_dest { if (cc) goto rel_dest; }
:CALL rel_dest is prefix16=0xa3ff & rel_dest { call rel_dest; }
:PARK          is prefix16=0xb7df & imm16    { goto inst_start; }
:UNK  imm16    is imm16                      { }   # opaque; least-specific -> lowest priority
```

Mark the 24 dispatch entries as functions; auto-analyze.

**GATE G1 (cross-validation)**: Ghidra's analyzer, built on SLEIGH v0, must
independently reproduce `cfg-map.py`'s results — same ~2,201 blocks, same
dispatch targets, and the **w2837 loop nest** + **w8676 aging loop** must
appear as loops in Ghidra. Two independent implementations agreeing validates
*both* the branch models and the SLEIGH encoding. If they disagree, the
disagreement localizes the bug before any semantics are trusted.

### G2 — SLEIGH v1: loads/stores + address spaces (dataflow appears)

Add the memory classes so the decompiler shows data movement:
- `0x04xx` / `0x1xxx` → load/store against the `ctxt` space (address = low16).
- `0xf042` → load/store against a `muram`/internal space (`0x0300–0x4b00`).
- `0x1080` → access to the `0x0843–0x087d` hot struct (name it once G3
  identifies it — Q03).
Provisionally attach the operand register field (`regA`, position
**unconfirmed** — resolve in G3) so the decompiler tracks a value.

**GATE G2**: decompile the **ENQ builder at w9055** — its pseudocode should
show a **context read at `0xd0d4`** immediately followed by materialization of
the `0x02010000` ENQ descriptor (this also starts answering Q06: how the
FE-VM enqueue path is constructed). Control flow is exact; only ALU stays
opaque.

### G3 — SLEIGH v2: ALU + registers + conditions (oracle-gated)

The hard, gated layer. For each candidate encoding (ALU op, register-field
position, branch-condition bits):
1. Hypothesis from **field-entropy** across the 24-blob corpus (which bits
   vary like opcode vs operand vs immediate).
2. **Confirm on silicon before trusting** (`decomp/experiments.md`): patch one
   word of that class, kexec, observe (`pcd-snapshot`/ping/dmesg). E.g. branch
   inversion confirms the condition field; register-swap confirms the operand
   position; a controlled context-address change confirms load semantics.
3. Only after the oracle agrees, encode it in SLEIGH and re-decompile.

Register-file size comes out of the field-entropy pass (operand field width);
start with a provisional bank and widen.

### G4 — Decompile the Phase-6 targets

With G1–G3 in place, drive Ghidra automation API over the ranked targets
(`06-algorithm-extraction.md`), best-understood first:
1. **Aging walker** (slot 19 → w8676–w12072, B02) — the structurally clearest
   210-only routine; recovers the aging-update algorithm and helps answer Q02
   (how aging is invoked without an HC doorbell).
2. **ENQ / FE-VM enqueue builder** (w9055/w9307 region) — Q06 + Phase-6 target.
3. **KeyGen HC** (slot 1 → w12061) — validates arch §4.
4. **Table walker** (B01, w2837 nest) — the largest routine; likely the CC /
   match-table walk.

## Ghidra automation API-driven workflow (the 27 tools)

Once G1 loads and analyzes, use the automation API or equivalent headless
scripts to inspect the program:
- `list_methods` / `list_segments` — enumerate the auto-created functions;
  confirm they map to slot handlers + branch-target functions.
- `rename_function` — apply `anchors.json` labels (`FUN_…@w8669` →
  `cc_aging_update`, `w2837` → `table_walker`, `slot1` → `keygen_hc`, w9055
  region → `enq_builder`).
- `set_decompiler_comment` / rename data — pin the memory regions
  (`ctxt:0xd0xx` = per-task context page; M01 struct).
- `decompile_function` — read pseudocode per target; it is control-flow-exact
  from G1 and gains data/ALU detail through G2/G3.
- `search_functions_by_name`, cross-refs — trace call graphs from the
  trampolines into the convergence zone (w12061–w12271).

A tiny GhidraScript can bulk-apply `anchors.json` (rename + comment) so labels
survive re-analysis.

## Milestones, gates, kill criteria

| Milestone | Deliverable | Gate |
|---|---|---|
| G0 | blob imported raw; automation endpoint live | `list_methods` returns |
| G1 | SLEIGH v0 (branches + catch-all) | **Ghidra CFG == cfg-map** (2201 blocks, w2837/w8676 loops) |
| G2 | SLEIGH v1 (loads/stores, spaces) | w9055 decompiles to `ctx[0xd0d4]`→ENQ pattern |
| G3 | SLEIGH v2 (ALU/regs/cond), oracle-confirmed | ≥3 encodings each confirmed by an E-experiment |
| G4 | ≥1 Phase-6 target decompiled to readable prose | aging walker algorithm recovered + cross-checked vs arch doc |

**Kill gate**: if the ALU/register layer (G3) resists after ~2 weeks or ~10
oracle experiments, **stop at G2**. G1+G2 already deliver an accurate CFG +
dataflow-lite — a large multiplier for CFG-guided manual reading — which is
worth keeping even if full decompilation stalls. Do not chase 100% ISA
coverage (the AMD/Intel benchmark reached ~40% over years).

## Effort & risk

- G1 is days and high-confidence (branch models are silicon-validated).
- G2 is days–weeks.
- G3 is the gamble; the oracle makes it *measurement*, not guessing, but ALU
  encoding density is unknown.
- Full readable decompilation of a target (G4) is plausible for the
  aging walker within weeks *if* G3 yields the common ALU/load/store ops; it
  is not guaranteed. The CFG+dataflow value (G1/G2) is guaranteed.

## Immediate next actions

1. Start the automation bridge or use the equivalent headless GhidraScript workflow.
2. Author `fman-risc` SLEIGH v0; compile with the built `sleigh`; import the
   blob under it (G0→G1).
3. Run the G1 cross-validation against `decomp/maps/210.10.1-blocks.json`.
4. In parallel, run oracle experiment **E3** (hot-path `b3ff` branch, queued
   in `experiments.md`) — it independently confirms the relative-branch model
   that G1's SLEIGH encodes.
