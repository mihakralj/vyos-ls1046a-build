# decomp/ — QEF 210.10.1 Microcode Disassembly Program

**Started 2026-08-07 · Status: Phases 0–2 done, Phase 3 in progress (anchors.json v1), Phase 4 oracle OPERATIONAL (E1/E2 PASS on .185), Phase 5 toolchain installed (Ghidra+MCP)**

Reverse-engineering program for the NXP FMan v3 controller microcode (QEF
container, version 210.10.1) that runs on the LS1046A's FMan. Goal is
**semantic recovery of ~10 named routines** (FE-VM ehash interpreter, KeyGen
HC handler, CC walker, policer path, parser error paths, …) — *not* a full
decompile of all 12,851 code words.

This directory holds the plan, the running discovery log, and per-phase
notes. The register/AD-level contract the microcode implements is documented
separately in `arch/fman-microcode-210-programming-reference.md`; decomp/
covers the *code itself*.

## Contents

| File | Covers |
|---|---|
| [findings.md](findings.md) | Dated discovery log — every established fact with evidence, newest first |
| [correlation-arch.md](correlation-arch.md) | Correlation of decomp findings with `arch/fman-*.md` (verdicts, edits applied, open questions) |
| [experiments.md](experiments.md) | Silicon-oracle experiment log (delivery pipeline, E1/E2, queue) |
| [ghidra-setup.md](ghidra-setup.md) | Ghidra 11.3.2 + GhidraMCP install record (ARM64 native build, MCP wiring, operational steps) |
| [ghidra-decompile-plan.md](ghidra-decompile-plan.md) | Staged plan to decompile 210.10.1 with Ghidra (SLEIGH module G0–G4, cross-validation gate, oracle loop, MCP workflow) |
| [naming-map.md](naming-map.md) | Authoritative naming/structure vocabulary (context/IC layout, MURAM structs, constants, dispatch-slot function names) harvested from `arch/` + qdrant |
| [00-acquisition.md](00-acquisition.md) | Phase 0 — blob acquisition, provenance, hashes, corpus |
| [01-container.md](01-container.md) | Phase 1 — QEF container parse tooling |
| [02-static-structure.md](02-static-structure.md) | Phase 2 — zero-ISA static structure (entropy, dispatch table, segmentation, histograms) |
| [03-anchored-labeling.md](03-anchored-labeling.md) | Phase 3 — differential + constant-anchor semantic labeling |
| [04-isa-inference.md](04-isa-inference.md) | Phase 4 — opcode cracking with the live silicon oracle |
| [05-decompiler-infra.md](05-decompiler-infra.md) | Phase 5 — Ghidra SLEIGH module, emulation |
| [06-algorithm-extraction.md](06-algorithm-extraction.md) | Phase 6 — ranked algorithm extraction targets |
| [tools/](tools/) | committed tooling: `qef-parse.py` (container parse/CRC), `structure-map.py` (zero-ISA structure) |
| [maps/](maps/) | generated structure maps: `210.10.1-structure.json` + human summary |

## Why this is tractable (and why it was previously judged not to be)

On 2026-07-11 the project deliberately chose observability (`fe_probe`,
`pcd-snapshot`, behavioral CRC matching) over ISA disassembly for the ASK2
EKFC/workspace questions. That call was correct for landing M3 — those
questions closed behaviorally on 2026-07-13. The blind-RE benchmark was grim:
RUB-SysSec's AMD K8/K10 microcode RE (USENIX 2017) and chip-red-pill's Intel
Goldmont disassembler were multi-year efforts reaching ~40% coverage.

This program is *not* blind RE. Four assets the AMD/Intel teams lacked:

1. **A 24-blob same-ISA differential corpus.** Proven 2026-08-07: every public
   blob from P1023 (160.0.18) through P4080/T4240/B4860/LS1043/LS1046
   (106/107/108) and our proprietary 210.10.1 shares the `0xb7ffXXXX`
   branch-word dispatch-table encoding. One fixed-width 32-bit RISC ISA,
   stable across ~15 years of NXP toolchain builds.
2. **A pre-attributed 24-entry dispatch table** at code offset 0 (arch doc
   §1.2): ~15/24 slots have candidate identities; slot 19 is a confirmed
   210-only entry point.
3. **Labeled anchors** — 106/108 implement the *documented* Host-Command
   interface, protocol constants appear as immediates, and feature-presence
   splits (106→108, 107-DSAR, 210-only) name regions by construction.
4. **A live, brick-safe mutation oracle**: kernel patch
   `0117 load_fman_ctrl_code()` re-streams whatever blob U-Boot injected into
   the DT at every boot. Patched blobs load via TFTP + the `fman_ucode` env
   var — no SPI flash writes. The dev board (.185) observes the effect.

## Ground rules

- **Never write SPI flash** for microcode experiments. Mutation = TFTP-loaded
  patched blob → DT injection → kernel re-stream. A bad blob costs one reboot.
- Experiments run on the **dev board (.185)** only. Never .190 (production-ish
  SSH lifeline board).
- The blob is NXP proprietary, LA_OPT EULA. Do not commit the blob or derived
  disassembly listings to public remotes. Markdown analysis is fine.
- **No replacement-microcode ambition** — we have no assembler, no signing
  keys. Read-only understanding.
- **Soft-parser rabbit hole warning**: the parser's NetPDL/XML tooling is real
  and documented but programs a *different* engine. It does not unlock the
  controller microcode. Do not burn time there.
- **Decision gate** at Phase 4: if control-flow encodings are not cracked
  after ~2 weeks of oracle time, stop ISA work and fall back to the
  observability stack, which already answers the production questions.

## Working artifacts (volatile — re-acquire with the recipes in 00)

- `/tmp/kilo/fman-ucode-210.10.1.bin` — canonical blob (SHA-256
  `5f3ed8d32b8659aafd8912d5d9920306350cae7a85884d81859152b9723eff0d`)
- `/tmp/kilo/qoriq-fm-ucode/` — 23-blob public corpus
- `/tmp/kilo/qef_probe.py` — original ad-hoc probe, superseded by
  `decomp/tools/qef-parse.py` + `decomp/tools/structure-map.py` (committed)
