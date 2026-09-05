# Ghidra automation setup on ARM64

**Installed 2026-08-08 on the AArch64 build host.** This records the verified
installation. Earlier unverified path and version assumptions are superseded
by the values below.

## What's installed

| Component | Version | Path |
|---|---|---|
| Temurin JDK | 21.0.12+8 | `/opt/jdk-21.0.12+8` |
| Ghidra | 11.3.2 (PUBLIC 20250415) | `/opt/ghidra_11.3.2_PUBLIC` |
| Ghidra native decompiler + sleigh | built from source (aarch64) | `…/Ghidra/Features/Decompiler/os/linux_arm_64/{decompile,sleigh}` |
| Optional Ghidra automation extension | 1.4 | `…/Ghidra/Extensions/GhidraMCP` |
| Optional Python automation bridge | 1.4 | `/opt/ghidra-mcp/bridge_mcp_ghidra.py` |
| Xvfb + X11 libs | 21.1.7 | apt |
| Automation endpoint | — | stdio bridge to `http://127.0.0.1:8080/` |
| PATH/env | — | `/etc/profile.d/ghidra.sh`, `ghidraRun` + `ghidra-analyzeHeadless` in `/usr/local/bin` |

## ARM64-specific build requirements

Ghidra ships prebuilt native binaries only for `linux_x86_64`, `mac_*`,
`win_x86_64` — **no `linux_arm_64`**. Without them the decompiler dies with
`os/linux_arm_64/decompile does not exist`. The C++ source ships with the
release; build it (g++ 12, make, bison, flex all present):

```bash
cd /opt/ghidra_11.3.2_PUBLIC/Ghidra/Features/Decompiler/src/decompile/cpp
sudo mkdir -p ../../../os/linux_arm_64 ghi_opt sla_opt com_opt
# the Makefile has no aarch64 arch branch -> defaults to x86 '-m32'; override:
sudo make -j"$(nproc)" ghidra_opt sleigh_opt ARCH_TYPE= OSDIR=linux_arm_64 \
     GHIDRA_BIN=/opt/ghidra_11.3.2_PUBLIC
sudo cp ghidra_opt ../../../os/linux_arm_64/decompile
sudo cp sleigh_opt ../../../os/linux_arm_64/sleigh
sudo chmod +x ../../../os/linux_arm_64/{decompile,sleigh}
```

`ARCH_TYPE=` (empty) is the fix — the Makefile's `ifeq ($(ARCH),x86_64)` has
no aarch64 branch and falls through to `-m32`. Also pre-create the `*_opt`
object dirs or the `-j` build races. Result: aarch64 ELF `decompile`
(3.7 MB) + `sleigh` (944 KB). The `sleigh` binary also lets us compile a
custom FMan `.slaspec` later (Phase 5 proper).

The optional automation extension's `Module.manifest` uses `KEY=value`, while
Ghidra expects `KEY: value`. An empty manifest (`truncate -s 0`) is valid and
prevents extension-scan errors.

## Test results (2026-08-08)

- **Automation bridge handshake — PASS.** `python3 bridge_mcp_ghidra.py
  --transport stdio` responds to `initialize` and `tools/list` with **27
  operations** (`decompile_function`, `list_methods`, `rename_function`,
  `list_segments`, `search_functions_by_name`, …).
- **Decompiler headless — PASS.** `analyzeHeadless` on a test ELF now runs
  the Decompiler analyzers (no `does not exist` error) after the native build.
- **GUI under Xvfb — PASS.** `ghidraRun` launches its Swing JVM cleanly under
  `Xvfb :99` on this headless ARM64 box (process healthy, no fatal errors).
- **Live `:8080` — PENDING one-time GUI action** (below). It is closed until
  the plugin is enabled on an open program.

## Starting the automation service

1. Start Ghidra under Xvfb: `decomp/tools/ghidra-mcp-server.sh
   [${DECOMP_WORKDIR:-/tmp/fman-decomp}/ghidra-proj/decomp.gpr]`.
2. **One-time per project**: open a program in the CodeBrowser, then
   `File > Configure > Miscellaneous > check GhidraMCPPlugin > OK`. The plugin
   binds `127.0.0.1:8080` and the setting persists.
3. Verify that `curl -s http://127.0.0.1:8080/methods` lists the program's
   functions.
4. Start the stdio bridge if an external automation client is required.

Because the server lives in a per-tool GUI plugin and Ghidra 11.x keeps its
default tools as jar resources (no on-disk `.tool` to pre-seed), step 3
cannot be fully automated headlessly — it is a single GUI action, after
which the state is durable.

## FMan-blob caveat (why Ghidra is still low-value right now)

Ghidra has **no processor module for the FMan controller ISA**, so the
210.10.1 blob can only be imported as *raw binary* (bytes, no
disassembly) — which our own tools already navigate by word index with real
semantics. Ghidra becomes worthwhile only after Phase 4 cracks enough
encodings to write a `fman-risc.slaspec` SLEIGH module (Phase 5 proper); the
`sleigh` binary built above compiles it. Until then, use Ghidra for the test
ELF / structure browsing, and keep the ISA work in `decomp/tools/` + the
silicon oracle.
## Addendum 2026-09-05 — custom fman-risc processor LIVE; caveat above superseded

Reinstalled from scratch on the current build host (previous install did not survive machine moves) and took it all the way through a decompiling import of the 210.10.1 blob. The Phase 7 work produced a full 201-instruction SLEIGH spec, so the "raw binary only" caveat above no longer applies.

1. **Install (as documented above, verified again):** Temurin JDK 21 (`/opt/jdk-21.0.12.1+1`, mandatory — Ghidra 11.3 rejects 17), Ghidra 11.3.2 PUBLIC at `/opt/ghidra_11.3.2_PUBLIC`, native `decompile`+`sleigh` built from source into `Features/Decompiler/os/linux_arm_64/` with `make ... ARCH_TYPE= OSDIR=linux_arm_64`. Sanity: the built `sleigh` compiles the stock MIPS32 spec byte-identically.
2. **Processor packaging gotcha (the one that cost the most time):** a processor directory under `Ghidra/Processors/<name>/` is invisible to the GhidraClassLoader unless it looks like a module — an (empty is fine) `Module.manifest` PLUS the data packaged as `lib/<name>.jar` (stock processors ship exactly this shape). Symptom otherwise: `InvalidInputException: Unsupported language: fman-risc:BE:32:default` from `analyzeHeadless`. Setup: `Ghidra/Processors/fman-risc/{Module.manifest, data/languages/{fman-risc.slaspec,sla,ldefs,pspec,cspec}, lib/fman-risc.jar}` (jar contents = `data/languages/*`).
3. **Slaspec compile fixes** (all in `decomp/tools/generate-sleigh.py`, regenerated spec committed): delayslot must be `delayslot(1);` INSIDE the `{}` block, not `[ delayslot(1); ]` between pattern and block; flag-compare and mixed-size semantics need explicit sizes (`== 0:2`, `(f_15_0:4)`, `f_15_11[0,16]`); fields constrained in the pattern (`f_5_0=0x15`) must never appear in the semantics (shift-immediates get a dedicated unattached alias token field `f_15_11_imm`); dmem addresses must be built through a 2-byte local (`local addr:2 = f_10_0; addr = addr + f_15_11[0,16];`); `:fill`/`:nop` collide on `0xffffffff` (fill skipped, `:nop` catch-all kept); `attach variables [ f_20_16 f_15_11 f_10_6 ] [ r0..r31 ]` gives real register dataflow through every register-operand instruction.
4. **Import recipe:** strip the 244-byte QEF header from `fman-ucode-210.10.1.bin` (`dd bs=1 skip=244`, 51404 B = 12851 words), then `analyzeHeadless <projdir> fman210 -import blob.words.bin -processor fman-risc:BE:32:default -cspec default -scriptPath <dir> -postScript DecoCC.py`. Keep the project under `~/.cache/fman-decomp` (a hostile process has been observed deleting files under /tmp on this host — never stage decomp work in /tmp).
5. **Decompile quality caveats:** artificial function boundaries (CreateFunctionCmd at word anchors) produce `in_*` parameter noise; the `cc` register (condition flags for the brbitclr/brbitset families) is never written by the spec so those branches appear as opaque `in_cc` tests; the `keycmp.run` compare unit and DMA engines are pcodeop black boxes. Usable for structure, not for automatic clean C.
