# Phase 5 — Decompiler Infrastructure

**Status: TOOLCHAIN INSTALLED (2026-08-08), awaiting a SLEIGH module.**
Ghidra 11.3.2 + JDK 21 + the ARM64-built native decompiler/sleigh + GhidraMCP
1.4 (27 MCP tools, bridge validated) are installed and wired into Kilo
(`.kilo/kilo.json` → `ghidra`); full record + operational steps in
`decomp/ghidra-setup.md`. The blocking input is still Phase 4: Ghidra can
only hold the FMan blob as raw bytes until a `fman-risc.slaspec` exists, so
the SLEIGH module below is the real work, not the install.

## Goal

Tooling that turns the partial opcode map + labeled structure into readable
pseudocode and runnable semantics for the Phase-6 targets.

## Work items

### 1. Ghidra SLEIGH processor module ("fman-risc")

- Fixed-width 32-bit instructions, big-endian, word-addressed code space
  (address = word index; dispatch targets are word offsets from byte `0xC0`).
- Load all 24 corpus blobs with the same `.slaspec`; apply the Phase-2
  region map (data vs code) and Phase-3 labels as pre-named functions.
- Incremental: SLEIGH semantics can be written per-opcode as Phase 4
  confirms them; Ghidra gives CFG, xrefs, and decompilation for the covered
  subset from day one.
- Entry points: the 24 dispatch slots (pre-attributed in arch doc §1.2).

### 2. Minimal emulator (optional but high-value)

A small Unicorn-style engine (custom, or QEMU TCG plugin if the opcode set
stabilizes) implementing the *cracked subset only*, with a synthetic MURAM
workspace:
- Run an individual labeled handler (e.g. slot 1, KeyGen HC) with crafted
  input state.
- **Read the MURAM tables it writes** = ground-truth semantics of what it
  programs. This extracts algorithms precisely without full ISA coverage —
  the emulation reads behavior the same way fe_probe reads silicon behavior,
  but offline and arbitrarily repeatable.
- Cross-validate emulator output against board `pcd-snapshot` captures of
  the same operation — agreement on known operations is the emulator's
  correctness proof for unknown ones.

### 3. Corpus diff viewer

Web/terminal viewer over the Phase-2 alignment map: click a 210 region →
see 106/108 counterpart (or "unique"), labels, decode. Cheap to build on
the JSON maps; pays for itself during manual reading sessions.

## Deliverables

- `decomp/sleigh/fman-risc.slaspec` (+ loader script)
- `decomp/emu/` (if pursued)
- Decompilation exports per Phase-6 target into `decomp/out/`

## Guardrail

The emulator and decompiler are *consumers* of the opcode map, never
*evidence* for it. A decode that only agrees with itself has happened before
in this project family (the hypothetical spec-§12 opcode map). Every SLEIGH
semantic traces to a Phase-4 oracle experiment or cross-blob proof.
