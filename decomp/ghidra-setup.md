# decomp/ghidra-setup.md — Ghidra + GhidraMCP on the Cobalt runner (ARM64)

**Installed 2026-08-08 on the aarch64 build host.** This is the *real*
install record for this machine (the earlier "Task complete" summary
described paths/versions that were never present here). Reproducible; every
non-obvious step is the ARM64 tax.

## What's installed

| Component | Version | Path |
|---|---|---|
| Temurin JDK | 21.0.12+8 | `/opt/jdk-21.0.12+8` |
| Ghidra | 11.3.2 (PUBLIC 20250415) | `/opt/ghidra_11.3.2_PUBLIC` |
| Ghidra native decompiler + sleigh | built from source (aarch64) | `…/Ghidra/Features/Decompiler/os/linux_arm_64/{decompile,sleigh}` |
| GhidraMCP extension | 1.4 | `…/Ghidra/Extensions/GhidraMCP` |
| GhidraMCP Python bridge | 1.4 | `/opt/ghidra-mcp/bridge_mcp_ghidra.py` |
| Xvfb + X11 libs | 21.1.7 | apt |
| Kilo MCP entry | — | `.kilo/kilo.json` → `ghidra` (stdio bridge → `http://127.0.0.1:8080/`) |
| PATH/env | — | `/etc/profile.d/ghidra.sh`, `ghidraRun` + `ghidra-analyzeHeadless` in `/usr/local/bin` |

## The ARM64 tax (non-obvious)

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

Also: GhidraMCP's `Module.manifest` uses `KEY=value`; Ghidra wants `KEY:
value`, so it errored on every scan — emptied it (`truncate -s 0`), which is
a valid Ghidra module manifest.

## Test results (2026-08-08)

- **Bridge MCP handshake — PASS.** `python3 bridge_mcp_ghidra.py --transport
  stdio` responds to `initialize` + `tools/list` with **27 tools**
  (`decompile_function`, `list_methods`, `rename_function`, `list_segments`,
  `search_functions_by_name`, …). This is what Kilo will register.
- **Decompiler headless — PASS.** `analyzeHeadless` on a test ELF now runs
  the Decompiler analyzers (no `does not exist` error) after the native build.
- **GUI under Xvfb — PASS.** `ghidraRun` launches its Swing JVM cleanly under
  `Xvfb :99` on this headless ARM64 box (process healthy, no fatal errors).
- **Live `:8080` — PENDING one-time GUI action** (below). It is closed until
  the plugin is enabled on an open program.

## Bringing the server up (operational)

1. **Restart Kilo once** so it loads the `ghidra` MCP server from
   `.kilo/kilo.json` (the 27 tools appear after reload).
2. Start Ghidra under Xvfb: `decomp/tools/ghidra-mcp-server.sh
   [/tmp/kilo/ghidra-proj/decomp.gpr]`.
3. **One-time per project**: open a program in the CodeBrowser, then
   `File > Configure > Miscellaneous > check GhidraMCPPlugin > OK`. The plugin
   binds `127.0.0.1:8080` and the setting persists.
4. Verify: `curl -s http://127.0.0.1:8080/methods` lists the program's
   functions. Now the Kilo `ghidra` tools return real data.

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
