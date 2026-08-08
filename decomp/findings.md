# decomp/findings.md — Discovery Log

**Newest first · Every entry states its evidence · "Open question —" marks the unresolved**

This is the running log of everything established about the QEF 210.10.1
blob and its corpus. Phase files hold the *plan*; this file holds the
*facts*. Cross-reference: `arch/fman-microcode-210-programming-reference.md`
(register/AD-level contract, §1.2 has the quantified blob comparison and
dispatch-table attribution).

---

## 2026-08-08 (mid-7) — Static bucket-index probe + branch-family completion

Attempted to crack the `0xce/0xcf` bucket shift/mask statically (option "b").
Outcome — one confirmed, one oracle-deferred, one bonus:

**Bucket mask FOUND**: `w1944 e9 r0,0xffff` masks the hash register; `0xffff`
is the immediate in **63/118** `0xe9` sites → `0xe9` is an AND/mask op and the
bucket index is masked to **16 bits**, consistent with `mask ≤ 0x7fff`. The
hash-register op chain is `b8 (w1942) → e9&0xffff (w1944) → ce (w1947) → cf
(w1948)`.

**Shift `>>48` NOT statically confirmable**: no shift immediate (`0x30/0x10/
0x08`) appears near the hash; the shift is implicit in the load byte-position
or inside a black-box op. Confirming needs the oracle (patch the shift/mask,
observe bucket placement) — reinforces the plan's oracle-gating.

**BONUS — branch-family gap found + fixed.** A family scan of `0xa0–0xbf`
found **9 more conditional-branch classes** (`b03f/b83f/b41f/bc1f/b81f/b01f/
b45f/b17f/a7ff`, all with the `_f` suffix, 100% relative-in-range) that were
`unk` in **both** `cfg-map.py` and the SLEIGH — so the original G1
cross-validation "matched exactly" while **both shared the blind spot**. Added
to both: **brc 966 → 1240** (+274 branches), `unk` 5534 → 4247.
- Honest correction: G1 confirmed SLEIGH↔cfg-map *consistency*, not
  *completeness*. The block map (`210.10.1-blocks.json`) should be regenerated
  with the full branch set (follow-up).
- Directly relevant to HIT/MISS: the branch opcode's `_f` suffix byte **encodes
  the condition** — so "which `brc` = HIT vs MISS" is an opcode-level question,
  answerable by mapping opcode → condition (oracle).

Repo: `fman-risc.slaspec` (full branch family), `cfg-map.py` (synced).

---

## 2026-08-08 (mid-6) — EXT_HASH HIT/MISS discriminator located

Targeted the months-old flow-MISS mystery by locating the microcode that
decides HIT vs MISS. Full analysis in `decomp/hitmiss-path.md`.

**Bucket-index setup (`bucket_index`, w1928–1948)**: `w1936 ld r0,[0xd048]`
reads the KG hash; `w1947 ?ce r0,0x0189 ; w1948 ?cf r0,0x0241` operate on the
hash register = the **shift/mask** forming the bucket index. Decompile shows
it assembles hi/lo addresses (`CONCAT22(dmem[0x6301],dmem[0x6303])`) and
fetches via `0xf4` — a DMA/table-fetch candidate — using a second workspace
at `0xe000`.

**Compare-and-dispatch walker (`ehash_walker`, w2837)**: decompiles to
`iVar2 = fman_test_dc(ctx[0xa8], 0x10f8); if (iVar2==0) {…muram[0x13a8]…} else
{…muram[0xba0]…}` — `0xdc` (`fman_test_dc`) **confirmed as the comparator**
(its result is the `if` predicate); reads context/key fields
(`ctx 0x98/0x9c/0xa8/0xb4`); the walker's `?op_e1 0x0008/0x000c` immediates
(**8, 12**) match the DDR key offset (+8) and keysize (12/13).

**G3+ SLEIGH**: added black-box pcodeops for the walker's classes (`0xe1`,
`0xef`, `0xd9`, `0x77`, `0x78`, `0xf1`, `0xf4`) so the HIT/MISS path
decompiles readably. `decomp/ghidra/scripts/FmanHitMiss.py`.

**Critical unknowns** (each oracle-confirmable): the `0xce/0xcf` shift/mask
(bucket index), the DDR **DMA-read** (`0xf4`/`0xf1` lead), the exact `test_dc`
compare length, and which `brc` = HIT vs MISS. **Decisive experiment E-HM1**:
on the ASK2 engage path, force the `test_dc` branch to the match path — if
flows then HIT, the MISS is a **key-comparison failure** (step 5), not
bucket-index/DMA; one measurement splits the candidate space. Needs the ehash
path engaged (ASK2 M3) since the islands are cold on the mainline path.

Scope: ~2 functions + ~5 encodings, oracle-confirmable — the decomp oracle and
the ASK2 flow-HIT work converge here.

Repo: `decomp/hitmiss-path.md`, `fman-risc.slaspec` (G3+), `FmanHitMiss.py`.

---

## 2026-08-08 (mid-5) — Ghidra G3: ALU classes decoded, conditions modeled → readable decompilation

**Field analysis** identified the top unknown classes' operand structure:
`0xeb` = register-immediate op (602 words, small imm in low16); `0xf0` =
MURAM-addressing op (994, addr in low16); `0xd8`/`0xdb` = reg ops with
operand; **`0xdc` = the dominant pre-branch op (177× before conditional
branches) → the condition-setter** (no dedicated compare exists; the flag is
a side-effect of prior ops, loads `0x04` and `0xdc` being most common).

**SLEIGH G3** decodes these via **black-box `pcodeop`s** (`fman_alu_eb/f0/d8/db`,
`fman_test_dc`) — honest: it tracks register/memory dataflow *without*
asserting the exact ALU operation (which stays unverified pending oracle).
`0xdc` writes `cc`; conditional branches test it. Decode counts: eb=602,
f0=994, d8=678, db=533, dc=496 (`unk` 8837→5534); G1/G2 counts unchanged.

**Result — real decompilation.** The slot-19 aging handler now decompiles to
coherent pseudocode with tracked registers (`in_r4`…`in_r31`), context/MURAM
accesses (`in_dmem_0000d0d4` = the ctx field, `in_dmem_00009b00` = MURAM), and
**real conditionals** (`iVar9 = fman_test_dc(uVar6,0x9b8); if (iVar9==0){…}`)
instead of the G2 `while(true)` collapse. Example recovered logic:
`fman_alu_eb(ctx[0xd0d4], 8); test_dc(...); if(...) { alu_eb(r4,0xf);
alu_f0(r28, muram[0x4318]); … }`. The `0xdc`→`cc` hypothesis is the pivot that
unlocked structured output.

**Honest boundary**: the ALU **semantics** are still black boxes — which
concrete op each `fman_alu_XX` is (add/sub/and/or/shift) and the exact
`test_dc` predicate are unverified. Resolving them is the remaining G3 work
and is **oracle-gated** (patch a known op, observe) — the multi-week gamble
the plan flagged. But the *structure* (operands, dataflow, control flow) is
now decompiled and readable, which is the substantive G3 deliverable and a
large multiplier for manual analysis. Load/store direction and the full
8-bit register field also remain oracle-confirmable refinements.

Repo: `fman-risc.slaspec` (G3), `decomp/ghidra/scripts/FmanDecompile.py`.

---

## 2026-08-08 (mid-4) — Naming/structure map harvested from arch+qdrant, applied to Ghidra

Compared the `fman-risc` disassembly's ad-hoc names against all
`arch/fman-*.md` + qdrant; harvested the authoritative NXP/SDK/project
vocabulary into **`decomp/naming-map.md`** and applied the high-confidence
parts to the program.

**Biggest structural helper**: the `0xd0xx` context space the `0x04xx`/`0x1xxx`
loads/stores address is the per-frame **FMan Internal Context (IC) / FE
workspace** — with a *documented* sub-layout (reference §12.2): parse result
`0x20–0x3F`, timestamp `0x40–0x47`, **KG hash result `0x48`** (raw CRC-64).
So `ld r3,[0xd0d4]` is a named field access, not an opaque one. (The exact
`ctx base = 0xd000 → IC-0x00` alignment is a hypothesis to confirm via an
oracle probe on a parse/hash-dependent read.) The `0xf042`/`0x1080` MURAM
window (`0x03xx–0x4bxx`) maps to the **FM_CTL params page** (`+0x44
errorsDiscardMask = 0x012ee0e8`, etc.) + CC/AD/HMCD structures.

**Vocabulary harvested** (full tables in naming-map.md): FE type constants
(`0x01–0x06<<24`, ENQ `0x02010000`, MUX `0x04000000`, FE_ENTER `0x40800000`,
OPC_FE_ENTER `0xF6`); AD result types (`CONT_LOOKUP 0x40000000`, NADEN
`0x20000000`, …); NIA engine codes (HWP `0x44`/HWK `0x48`/BMI `0x50`, engine
table DONE…CC); HM opcodes (`0x00`–`0x0E`, HMAN_OC `0x35`); magic
`0x012ee0e8`, `EHASH_MASK 0x7fff`, KGSE modes. Dispatch slots → HCOR/NIA
function names (slot 1 `hc_keygen`, slot 3 `hc_cc_update`, slot 19
`hc_cc_update_aging`, slot 8 `fm_ctl_a`, slot 12 `cc_dispatch`, slot 6/7
`qmi_enq`/`qmi_deq`, …).

**Caveat recorded**: the SDK/kernel names (`get_indexed_hash_bucket`,
`FmPcdCcBuildFE`, `ExternalHashTable*`) are **aarch64 driver code, not
microcode** — they name the *algorithm's role*, never the microcode's own
symbols. The CRC-64 is **silicon** (poly absent from code words), so the
microcode's bucket step is a shift+mask over `ctx+0x48`, not a CRC loop.

**Applied**: `decomp/ghidra/scripts/FmanLabels.py` (reusable `-postScript`)
renamed **24 functions** to authoritative names and labeled **4 ctx fields**
(`ctx_base`/`ctx_parse_result`/`ctx_timestamp`/`ctx_kg_hash`) — verified
headless. The disassembly/decompilation now reads in project vocabulary.
Next Ghidra-labeling step (post-G3): equate the §2 constants + define the §5
MURAM descriptor structs at their store sites.

Repo: `decomp/naming-map.md`, `decomp/ghidra/scripts/FmanLabels.py`.

---

## 2026-08-08 (mid-3) — Ghidra G2: memory access decoded; slot-8 cascade confirmed

**Memory-access format cracked** by field analysis: `[op8][reg8][addr16]`.
`0x04`/`0x14`/`0x10` are load/store variants (reg = bits[23:16], addr =
low16); the low16 addresses the per-task context page (`0xd0xx`) and the
internal MURAM window (`0x0300–0x4b00`). SLEIGH v1 (updated
`fman-risc.slaspec`) adds a `dmem` space + `ld`(0x04)/`st`(0x14)/`ldb`(0x10),
and reclassifies `a3ff` from `call` to unconditional `jmp` (matches cfg-map's
"rel" bucket and removes bogus call/return; Ghidra had itself flagged
"changing call to branch").

**Decode validated exactly**: `ld=1499, st=714, ldb=344` (matching the
field-analysis counts to the instruction); `unk` dropped 11394→8837;
br/brc/park unchanged. The **context read is now explicit**: w9053 decodes as
**`ld r3,[0xd0d4]`** (the iter-42 per-task-context-page access), and the
`0x1409d0c4` bracket resolves to **`st [0xd0c4],r9`**.

**Slot-8 guarded-store cascade CONFIRMED in disassembly**: w80–w102 are
`brc rel+N` with N stepping down by 2 (24,22,20,18,16,14,10,8,4,2) — all
**converging on w104 = `st [0xd0c4],r9`**. This is exactly the cascade
predicted from the `b3ffNNNN`-stepping-down pattern: a sequence of `e9c9`
ops guarded by conditional branches that all skip to a common context store.
The static hypothesis is now a decoded, readable structure.

**Honest boundary — decompiler C is gated on G3, not G2.** With `cc` (the
branch condition) unmodeled, every conditional is `if(in_cc)` over an unknown
value, so the decompiler can't reason about loop termination → bodies
collapse to `while(true)` and loads get dead-eliminated. So G2's real product
is a **semantically rich disassembly** (branches + loads/stores + resolved
context/MURAM addresses); clean decompiler *dataflow* needs G3
(condition/register/return semantics). Also: the load/store **direction**
(`0x04` load vs `0x14` store) is a hypothesis, not yet oracle-confirmed —
a candidate for the deferred E3-class experiment.

Repo: `fman-risc.slaspec` (G2), `decomp/ghidra/scripts/FmanG2.py`.

---

## 2026-08-08 (mid-2) — Ghidra G0/G1 DONE: fman-risc SLEIGH v0 cross-validates + decompiles

**SLEIGH v0 authored + compiled.** `decomp/ghidra/fman-risc/` (slaspec =
branch family + fixed-width `:unk` catch-all; pspec/cspec/ldefs), compiled to
`.sla` by the ARM64 `sleigh` binary via `decomp/tools/build-fman-sleigh.sh`,
installed as Ghidra language `fman-risc:BE:32:default`. Branch encodings:
`b7ff`=absolute `goto (48+imm16)*4`; `b3ff/b43f/bc3f`=relative `if(cc)goto
inst_start+simm16*4`; `a3ff`=call; `b7df`=park `goto inst_start`.

**G1 CROSS-VALIDATION PASS (decisive).** Headless `analyzeHeadless` imported
the code-only region (`blob[244:]`, base 0 so byte=4·word) under `fman-risc`;
`FmanG1Validate.py` linearly decoded all 12,851 words. Ghidra's independent
engine produced **exactly** cfg-map.py's counts: **br=97, brc=966, call=109,
park=285** (TOTAL 12,851, no desync), and predecessor counts at the anchors
match too — **w2837=12, w12133=36, w12849=16** (loop-nest head / hottest join
/ exit stub). Two independent implementations agreeing to the instruction
validates both the branch models and the SLEIGH encoding.

**Decompilation works.** `FmanDecompile.py` created + labeled 22 functions at
the dispatch entries + anchors and ran Ghidra's (ARM64-built) decompiler. The
**slot-19 aging handler** decompiles to structured C: it loops calling a
subroutine, then branches into `table_walker_B01` (w2837) and
`frame_epilogue_B03` (w12133) — a real structural finding (**the aging update
uses the table walker**), recovered from the blob. Output is rough (opaque
`in_cc` conditions, `while(true)` from park stubs) — exactly the G1
expectation: control-flow-exact, ALU opaque until G3.

**Step 2 (MCP server) — empirical conclusion**: `:8080` does **not**
auto-start when the GUI opens the project (confirmed under Xvfb); the
GhidraMCP server needs a one-time manual GUI action (open a program + enable
`GhidraMCPPlugin`). The **headless `analyzeHeadless` + GhidraScript pipeline
is the working unattended path** and is what delivered G1 + decompilation. The
`ghidra_*` MCP tools are loaded in Kilo and go live once the plugin is enabled.

**Step 4 (E3 oracle) — deferred with rationale**: the branch model is now
doubly validated (cfg-map + Ghidra SLEIGH, exact agreement), so E3's marginal
value dropped; a clean hot-path E3 first needs mainline-hot-code mapping (a G2
task). Running an ill-targeted board patch would violate the one-clear-signal
discipline, so it waits for G2.

Repo: `decomp/ghidra/fman-risc/**`, `decomp/ghidra/scripts/{FmanG1Validate,
FmanDecompile}.py`, `decomp/tools/build-fman-sleigh.sh`.

---

## 2026-08-08 (mid) — Ghidra + GhidraMCP installed on Cobalt (ARM64)

Full Phase-5 toolchain stood up on the aarch64 runner (the pasted "Task
complete" summary had described a machine where none of it was present —
Java was 17, no Ghidra, no bridge, no Xvfb). Installed: Temurin JDK 21.0.12
(`/opt/jdk-21.0.12+8`), Ghidra 11.3.2 (`/opt/ghidra_11.3.2_PUBLIC`),
GhidraMCP 1.4 extension + bridge (`/opt/ghidra-mcp/bridge_mcp_ghidra.py`),
Xvfb + X11 libs. Full record: `decomp/ghidra-setup.md`.

**ARM64 tax**: Ghidra ships no `linux_arm_64` native decompiler → built
`decompile` + `sleigh` from the bundled C++ source (fix: `ARCH_TYPE=` empty
to kill the Makefile's default `-m32`; pre-create `*_opt` obj dirs). GhidraMCP
`Module.manifest` used `KEY=value` vs Ghidra's `KEY: value` → emptied it.

**Wired into Kilo**: `.kilo/kilo.json` → `ghidra` MCP server (stdio bridge →
`http://127.0.0.1:8080/`). **Tests**: bridge MCP handshake exposes **27
tools** (PASS); decompiler runs headless after the native build (PASS); GUI
launches under Xvfb on ARM64 (PASS). Live `:8080` is PENDING one manual GUI
action (open a program + enable GhidraMCPPlugin — the server is a per-tool
GUI plugin and 11.x default tools are jar resources, so it can't be
pre-seeded headlessly). **Restart Kilo once** to load the 27 tools.

**Caveat**: no FMan processor module exists, so the blob only imports as raw
bytes in Ghidra — lower value than our word-indexed tools until Phase 4
yields a `fman-risc.slaspec`. The built `sleigh` binary will compile it.

---

## 2026-08-08 (early) — Silicon oracle OPERATIONAL: E1/E2 PASS on .185

**The mutation oracle works end-to-end.** Delivery pipeline (no flash
writes, no serial, no U-Boot env edits): `decomp/tools/qef-patch.py` patches
code words / header bytes and recomputes the solved trailer CRC → patches
the live DTB's `fsl,firmware` property in place (`--fdt`, blob located by
magic + CRC at DTB offset `0x60f8`) → `kexec -l /boot/vmlinuz
--initrd=/boot/initrd.img --dtb=PATCHED --reuse-cmdline && kexec -e` → patch
`0117` re-streams the patched blob into IRAM on the kexec'd boot. Round-trip
~90–120 s. Recovery = any plain reboot (eMMC pulls pristine SPI blob);
worst case = smart-plug power cycle.

**E1 (cosmetic id-string patch) — PASS.** id `…LS1043…`→`…LS1046…` (caps
safe: patch `0086a` parses only the `"Microcode version "` prefix + major ≥
210, verified in source). Post-kexec dmesg shows the patched id; live DT
property md5 = precomputed E1 blob md5 (`5ae2f890…`). Delivery is byte-exact.

**E2 (cold-region negative control) — PASS.** Code word w9055
(`0x02010000` → `0xffffffff`, ENQ materialization site inside 210-only
island 2): zero behavioral delta — links up, ping 3/3, **pcd-snapshot diff
vs pre-kexec baseline fully clean**. Confirms on silicon that island 2 is
cold on the mainline/RSS path, and that code-word mutation + CRC fixup is
behaviorally safe in cold regions.

**Board state note**: .185 is currently running the E2 blob via one-shot
kexec (datapath-equivalent to pristine); any plain reboot returns it to
pristine eMMC boot.

**Environment notes**: board shell is vbash — real binaries by full path
only (`sudo -n /sbin/kexec`, `sudo -n /usr/local/bin/pcd-snapshot`); no
`which`/`strings`. `/tmp` is tmpfs — wiped per boot; baselines go in
`/home/vyos/`. U-Boot env on .185 already has `fman_ucode=fbc11d00` (unused
by this path). Full protocol + experiment queue: `decomp/experiments.md`.

---

## 2026-08-07 (night) — Phase 3 kickoff: constant hunt + anchors.json, then CFG skeleton v2

**CFG skeleton v2 landed** (`decomp/tools/cfg-map.py` →
`decomp/maps/210.10.1-blocks.json`): 2,201 block starts from 97 absolute +
1,075 relative + 285 park branches. Relative-branch model validates: 100% of
targets in range, 134 convergent targets, 116 loop-shaped backward branches.
**No secondary jump tables and no raw offset tables exist anywhere** — Q05
(FE-type dispatch mechanism) answered negatively: no indexed data-table
dispatch; remaining hypotheses are compare-and-branch cascade (favored:
branch-rich ISA, small type constants) or computed indirect branch
(Phase-4 encoding question).

**Two mega-structures identified**: (B01) a 9-deep loop nest headed at
**w2837** (12 predecessors), spans up to w5127 — 2,290 words reaching into
island 1, the blob's largest control-flow feature, reads as a table walker;
(B02) a single 3,396-word loop **w8676–w12072** covering most of island 2 —
slot 19 (aging CC update, w8669) falls into it after a 6-word preamble: the
aging walker, first code-level confirmation of slot 19's function. **Hot
join points**: w12133 (36 predecessors — frame-handling epilogue candidate),
w12271 (24), w12849 (16 — exit stub at code end), w11911, w2837, w104,
w12091. **Dispatch slots are trampolines**: 1–3-word stubs branching to real
bodies (slot 1 → w12061, slot 8 → w104, slot 6 → w72); handler bodies live
in the w12061–w12271 convergence zone and the islands.

**FE-VM opcode constants are NOT in the code — negative result that steers
the whole approach.** The known FE-VM flow-record opcodes (`0x80000010`
STRIP_ETH_HDR, `0x80000200` TTL_DECREMENT, `0x8000C001` ETH_HDR_REBUILD,
`0x81000000` ENQUEUE_PKT — fe-ehash §10) appear **zero** times in every
tier. Likewise the FE type codes `0x01000000`–`0x06000000` never appear as
full words, and neither do `0x40800000` (FE_ENTER w0), `0x00007fff`,
`0x80000000`, `0x0000d000`. Conclusion: descriptor types and flow-record
opcodes are decoded by **bit-field tests or indexed table dispatch, never
by literal 32-bit compare** (or FE-VM opcode execution isn't controller-code
at all — open question Q06). Phase-4 implication: stop hunting opcode
literals; look for mask/shift idioms and dispatch-table data instead.

**What DID land — descriptor materialization sites.** `0x02010000` (ENQ FE
word-0, type+flag) appears 4× in 210: w2184, w2289, and **w9055 + w9307
inside the second 210-only island** — the FE-VM's own enqueue-path
construction sites. Context around w9055 shows `0403d0d4` (context-page
access at `0xd0d4`) immediately before the ENQ constant — consistent with
"read task context, then build ENQ descriptor". MUX constant `0x04000000`
appears in ALL tiers (210: w3998, w4772, w11081) — FE machinery is **not**
210-only at the base level (ENQ FE terminates HM chains in public microcode
too); 210-only is the ehash/aging/CC-hash layer on top. This refines the
"210-only" inventory label again (after §1.2's slots 11/16/17 note).

**NIA engine codes exist in low16 form** (`0x44`/`0x48`/`0x50` as low
half-words: 10/5/15 hits) — the full-word hunt earlier was the wrong form
(engine field is bits[22:16] of a constructed word). Protocol constants in
low16: IPv4 `0x0800` 82×, ARP 7×, IPv6 `0x86DD` 2× (w391, w9348), GTP 6×;
**VLAN `0x8100` and PPPoE `0x8864` absent** (hard parser strips tags).

**ISA big picture from the top-byte histogram**: `0x04` = 11.7% of all code
(the workhorse context/memory class), `0xf0` 7.7%, `0x14` 5.6%,
`0xd8/db/dc` combined 13.3%, `0xeb` 4.7%, branch bytes `0xb3/b4/b7/bc…`
~12%. Context-page + memory access classes (`04xx`+`1xxx`) ≈ **20% of the
entire code** — this is a table-driven state machine, not an arithmetic
engine. Matches the CRC64-poly-absent negative result from kickoff.

**`decomp/maps/anchors.json` landed** — the Phase-3 anchor database: 9
dispatch slots with identities/confidence, 10 opcode-class readings, 2
memory regions (incl. the `0x0843`–`0x087d` hot structure = open Q03),
5 constant anchors, 3 verified negative results, 6 open questions (Q01–Q06).

---

## 2026-08-07 (late) — Tool bug fix (target base), distribution-shape analysis, arch correlation

**Tool bug found and fixed: dispatch targets are `48 + XXXX`, not
`24 + XXXX`.** The dispatch table is 24 slots × 8 bytes = **48 words**
(`0xC0`); my `qef-parse.py dispatch` and `structure-map.py` used the slot
count as the word base, putting every derived target 24 words early. Fixed
in both tools; slot 8 now correctly lands at byte `0x140`, matching the arch
doc's empirically cross-verified `0xc0 + 32×4` address. Corrected absolute
targets: slot 0→w633, 1→w653, 2→w651, 3→w1626, 4→w2628, 5→w2432, 6→w8622,
7→w12172, 8→w80, 9→w227, 11→w406, 12→w75, 13→w585, 15=16→w583, 17→w534,
18→w646, **19→w8669**, 20=21→w652, 22→w12436. (The raw `XXXX` offsets in
arch doc §1.2's table are unaffected — they are offsets from table end.)
Alignment runs are base-independent, unchanged. Slot-19 corroboration
survives the fix (w8669 sits 21 words inside the w8648–w10262 unique
island); slot 7's target (w12172) turns out to sit *past* the
w10349–w12090 island — that island is branch-reached, not slot-dispatched.

**Distribution-shape analysis (supersedes the evening pass AND revises arch
doc §1.2).** low16 min/median/p90/max + value histograms per class:
- `0xb3ff` (378×) is **bimodal** — small values (median `0x001b`, top hits
  0x03/0x06/0x08) plus an `0xffxx` tail → **relative conditional branch**
  (short skips + loops), NOT §1.2's "load 16-bit immediate". Structural
  proof in the slot-8 construct: `b3ffNNNN` stepping down by 2 while the
  surrounding code steps up by 2 keeps `PC+NNNN` constant — every branch in
  the cascade lands on the same target (guarded-store cascade).
- `0xb43f`/`0xbc3f`: same shape, short conditional branches. `0xa3ff`:
  median `0x1f56` — long relative branch / call candidate.
- `0xb7df` (285×) low16 is almost always `0xfffe`/`0xffff` → **park/halt
  stubs** (branch-to-self), NOT my evening-pass "load-imm16" guess.
- `0xf042` values span `0x0300`–`0x4b00`, never small/negative → memory
  address operands. `0x1080` (115×) tightly clustered `0x0843`–`0x087d` →
  one hot structure (unknown — prime Phase-3 target). `0x0082` (110×) all
  `0x0000` → fixed offset-0 op. `0x0421` mixes `~0x98` and `0x48xx–0x78xx`.
- `0xe9c9` is only **13** occurrences blob-wide — §1.2's "recurring class"
  is locally true (slot-8 construct) but not a blob-wide player.

**iter-42 context-page claim statistically corroborated.** 779 words
blob-wide carry `0xd0xx` in low16, carried by the `0x04xx`/`0x1xxx` classes
— the same classes that grew most in 210 (`0x0400` 160→421). The
per-task-context-page access pattern from `arch/fman-fe-ehash.md` §8.1
item 3 now has corpus-level support.

**arch/ correlation done** — full table in `decomp/correlation-arch.md`;
surgical edits applied to `arch/fman-microcode-210-programming-reference.md`
(§3 trailer row + §1.2 follow-up note + slot-19 corroboration) and
`arch/fman-fe-ehash.md` (iter-42 cross-ref + the `0x630` addressing
open question).

---

## 2026-08-07 (evening) — Phase 1 closed, Phase 2 baseline landed

**Trailer integrity word SOLVED.** Raw reflected CRC-32 (poly `0xEDB88320`),
**init 0, xorout 0**, over `blob[0 : length-4]` — the U-Boot
`crc32_no_comp(0, …)` style. Verified on **all 24 corpus blobs** via
`decomp/tools/qef-parse.py crc` (exit 0). The `qe_firmware.rst` formula
(`crc32(-1, blob, len-4) ^ -1` = zlib CRC-32) does not apply to FMan blobs —
the arch doc §3 quote of it was wrong for this container class. First-pass
brute-force (7 variants × 7 scopes) missed only because the exact
(reflected, init 0, xorout 0) combo wasn't in the set; extended pass (4 polys
× 2 directions × init/xorout ∈ {0,FF…} × word-swapped feeds × 2 scopes,
cross-tier consistency required) found it.

**Tooling committed** (repo `decomp/tools/`): `qef-parse.py` (info /
dump-words / dispatch / crc) and `structure-map.py` (dispatch decode,
branch-class statistics, entry harvest, pad-run detection, cross-blob
exact-sequence alignment). Both stdlib-only.

**Structure map baseline** (`decomp/maps/210.10.1-structure.json`):
- 85 candidate function entries; 9 pad/data runs. Pad runs at w1184–1207,
  w1235–1247, w1574–1583 immediately precede dispatch entries (slot 3 →
  w1602) — alignment padding doubles as a function-separator signal.
- Exact-sequence (≥4-word) alignment: 210↔106 = 22.2% covered, 210↔108 =
  25.6%. Stricter than the 2026-08-06 byte-chunk method (30–42%); same story.
- **210's new code concentrates in two islands**: w2972–w8096 (~5.0K words,
  three contiguous unique runs) and w8584–w12090 (~3.4K words).
- **Slot-19 corroboration**: the structurally 210-only dispatch slot targets
  **w8669** (corrected base — see the late entry's tool-fix note), 21 words
  inside the 1,615-word unique-vs-public island w8648–w10262. Independent
  methods agree → slot 19 attribution (dynamic CC-table update,
  aging-specific; matches ASK-added `HC_HCOR_OPCODE_CC_UPDATE_WITH_AGING`)
  upgraded from "cleanest hypothesis" to high-confidence.

**Branch-class statistics (first pass, superseded later the same day — see
the late entry).** Per top-16 prefix, fraction of occurrences whose low16 is
an in-range code target (random baseline ≈ 19.6%): `0x1080` and `0x0082`
100%, `0xf042` 99%, `0xb43f` 98.9%, `0xebc0/c1` ~95%, `0xd841` 95%,
`0xf041/40` ~90-94%, `0xbc3f` 88%, `0xb3ff` 86.8%, `0x0421` 66.7%, `0xa3ff`
62.4%, `0x0400/01/02`+`0x1400/01` 31–48%, `0xb7df` 0.0%. **Methodological
flaw in this pass**: in-range% cannot distinguish "branch to anywhere" from
"small immediate" (small values are trivially in-range for a 12.8K-word
code). Superseded by the distribution-shape analysis in the late entry.

---

## 2026-08-07 — Program kickoff recon

**Blob staged locally.** Pulled from board .185's DT property
(`/proc/device-tree/soc/fman@1a00000/fman-firmware/fsl,firmware`, rootless
read) → `/tmp/kilo/fman-ucode-210.10.1.bin`. 51,652 bytes, SHA-256
`5f3ed8d32b8659aafd8912d5d9920306350cae7a85884d81859152b9723eff0d` — exact
match to the canonical fingerprint. Board .190 was unreachable from the
runner ("no route to host"); .185 is the working oracle board.

**Public corpus staged.** `git clone --depth 1
https://github.com/nxp-qoriq/qoriq-fm-ucode.git` → 23 blobs across 12 SoC
targets (P1023, P2041, P3041, P4080, P5020, P5040, T1024, T1040, T2080,
T4240, B4860, LS1043, LS1046), generations 106.1/106.2/106.4/107.4/108.x/160.
Includes `NXP-Binary-EULA.txt` and three release-note PDFs (DSAR, IPACC,
NG-CAPWAP) — feature descriptions usable as region labels in Phase 3.

**ISA lineage proof.** All 23 public blobs *and* 210.10.1 use the same
24-slot dispatch table at code offset 0: populated slots are `0xb7ffXXXX`
branch words + `0xffffffff` pad; unpopulated slots are all-`0xffffffff`. The
count of populated slots varies by generation (P1023 160: fewer; 106.1:
16/24; 106.4/108: 20/24), but the branch encoding is constant. Conclusion:
one fixed-width 32-bit RISC ISA family, one toolchain lineage, ~15 years of
builds. The 23 public blobs are a valid differential corpus for the 210 blob.

**Dispatch table re-verified.** Parsed 210's table from the freshly pulled
blob — all slot targets identical to arch doc §1.2 (slot 0→585, 1→605,
2→603, 3→1578, 4→2580, 5→2384, 6→8574, 7→12124, 8→32, 9→179, 11→358,
12→27, 13→537, 15=16→535, 17→486, 18→598, 19→8621, 20=21→604, 22→12388;
slots 10/14/23 unpopulated). Targets are word offsets counted from byte
`0xC0` (end of table).

**Candidate new-in-210 instruction class.** Top-16-bit word-prefix histogram
across tiers: prefix `0x0421` goes from 3 hits (106) / 0.04% to 120 hits
(210) / 0.93% — a ~30× expansion. `0x0400` (160→421), `0x0401` (94→239),
`0x0402` (67→146) also grow strongly. Stable classes (`0xb3ff` 3.6%→2.9%,
`0xebc0`, `0xd841`, …) are the shared base ISA. The `0x04xx` family is the
prime suspect for 210's new machinery (FE-VM ehash / CC hash-table).

**Negative result: CRC64 poly is not in the code.** Neither
`0xC96C5795` nor `0xD7870F42` (nor byte-swapped forms) appears as a word
anywhere in the 210 code. Confirms arch doc §4.3's framing: the CRC-64 is
computed by KeyGen *silicon*, not by microcode. Lesson recorded in Phase 3:
many "algorithms" of this firmware are table-driven orchestration of hardware
blocks — expect register/offset arithmetic, not arithmetic kernels, in the
code.

**Negative result: NIA engine encodings not immediates.** `0x00440000` (HWP),
`0x00480000` (HWK), `0x00500000` (BMI) do not appear as code words. NIAs are
constructed at runtime or sourced from MURAM tables/registers.

**Parser constants do appear.** Ethertype `0x00000800` (IPv4) at w3016 and
w9721 — two parser-adjacent code sites. (No hits for 0x86DD/0x8100/0x8864 as
full words — likely masked-compare encodings or 16-bit immediates; Phase 3
will hunt half-word forms.)

**Open question — QEF trailer integrity word.** Blob length = 124-byte
header + 120-byte descriptor + code + **4-byte trailer**, but the stored
trailer does not match zlib CRC-32 over `blob[:-4]` on *any* tier (210:
stored `0x961eb941` vs calc `0x2bd707ca`; 106: `0x5564b433` vs `0x2e2f34f0`;
108: `0x66b3f8da` vs `0xef4ce797`). Scope or CRC variant differs from the
qe_firmware.rst description. Non-blocking (nothing in our load path validates
it); Phase 1 closes it by brute-forcing CRC scope × variant, cross-checked
against U-Boot `qe_upload_firmware()`.

**Tooling.** Ad-hoc probe `/tmp/kilo/qef_probe.py` (parse + dispatch table +
immediate hunt + prefix histogram) — works on all tiers. Hardens into
`bin/qef-parse.py` in Phase 1.

---

## 2026-08-06 — Quantified 210 vs 106/108 comparison (arch doc §1.2, commit 3dc4d004)

- Blob sizes: 106.4.18 = 32,604 B / 8,089 words; 108.4.9 = 37,560 B / 9,328
  words; 210.10.1 = 51,652 B / 12,851 words.
- `soc.model`: 106/108 = `0x0416` (LS1046), 210 = `0x0413` (LS1043) —
  **cosmetic**, proven at code level: neither loader validates it (QE loader
  printks it; patch 0117 never reads it).
- `eccr` (`0x20800000`) and `code_offset` (244) identical across all tiers —
  structural constants.
- Content overlap (relocation-tolerant chunk matching): 106↔108 share
  60–77%; 210 shares only ~30–42% with either. Roughly 60–70% of 210 has no
  public counterpart. First byte-level evidence for the 210-only feature set.
- Neither public code stream appears in 210 as a contiguous substring —
  tiers are compiled/linked as a whole; substring comparison underestimates
  reuse.
- Whole-code entropy 6.29 bits/byte (210) → uncompressed fixed-width machine
  code.
- Candidate opcode classes by frequency/context: `0xb3ff` (plausibly
  load-imm16), `0xe9c9` (store/index pair), whole-word bracket `0x1409d0c4`
  (plausibly call/branch). At slot 8's target all tiers share a bracketed
  unrolled decrementing loop; 210 re-instantiates the construct twice —
  manual disassembly of the region judged tractable.

## 2026-07-13 — Canonical blob fingerprint (RSR 10.3.0.B1)

- Official NXP RSR images carry the identical blob at offset `0x900000`
  (rdb QSPI firmware.bin, sdboot sdcard.img, secure firmware.bin — all three
  byte-identical). Extraction: `dd if=firmware.bin bs=1 skip=$((0x900000))
  count=51652`.
- Establishes provenance: the Mono Gateway's factory microcode *is* the RSR
  blob; no board-specific variant exists.

## 2026-07-11/12 — Boundary of knowability; observability-first decision

- QEF container (`struct qe_firmware`) fully documented in U-Boot/kernel
  source; opcode ISA fully undocumented. NXP: "There is no customer-available
  document about the microcode."
- Host-Command doorbell **absent** from shipping 210.10.1 (caps = `0x17`,
  bit 3 clear), DUT-confirmed; 106/107/108 public blobs *do* implement HC —
  their dispatch handlers are matchable to documented command semantics
  (Phase 3 anchor).
- Decision (for ASK2 purposes): read what the microcode *does* via fe_probe
  rather than disassemble. EKFC extraction order closed behaviorally
  2026-07-13 (MSB-first, raw CRC-64, no final complement). That decision
  scoped to ASK2 questions; the present program is the general RE effort the
  decision explicitly deferred.

## Longer-standing anchors (pre-2026-07, from arch doc)

- 210.10.1 is the proprietary ASK fastpath family; public 106/108 lack the
  FE-VM/ehash engine entirely (no FE column in the public capability matrix).
- F-063 keysize stall proves the 210 microcode actively parses EXT_HASH
  descriptors and DMA-reads DDR bucket records (keysize 8 vs 13 changes
  hardware behavior).
- NXP ASK release notes list "Port-lockup crash is observed with collisions
  in Flow table" as a **known issue** on this exact microcode — a silicon-age
  bug we may be able to root-cause from recovered code (Phase 6, target 1).
