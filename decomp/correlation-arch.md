# decomp/correlation-arch.md — Correlation of decomp Findings with arch/fman-*.md

**2026-08-07 · Scope: all `arch/fman-*.md` files · Method: full/partial reads +
recomputation against the staged corpus**

What the disassembly program's findings mean for the existing architecture
docs: which claims they confirm, refine, contradict, or extend; what was
edited as a result; which questions the correlation opened. Verdicts:
**CONFIRMS / REFINES / CONTRADICTS / EXTENDS / OPEN**.

## Verdict summary

| decomp finding | arch claim | Verdict |
|---|---|---|
| Trailer = raw CRC-32 (init 0, xorout 0) over `blob[:len-4]`, all 24 blobs | (no arch claim; an earlier project note assumed the `qe_firmware.rst` zlib formula) | **EXTENDS** — §3 header table gained the trailer row |
| Dispatch table re-parse (24 slots, `0xb7ff`, base 0xC0) | §1.2 table + addressing | **CONFIRMS** (and fixed a 24-word base bug in *our own* tools, not the doc) |
| `0xb3ff` low16 bimodal (small + `0xffxx`) | §1.2 "most plausibly load 16-bit immediate" | **CONTRADICTS** — relative-branch shape; see §1.2 follow-up note |
| `0xb7df` low16 ≈ `0xffff`/`0xfffe` only | (our own evening-pass guess: load-imm16) | **CONTRADICTS** that guess — park/halt stubs |
| `0xe9c9` = 13 occurrences blob-wide | §1.2 "recurring candidate opcode class" | **REFINES** — locally true at slot 8, blob-wide rare |
| Unique island w8648–w10262 contains slot-19 target w8669 | §1.2 slot 19 "210-only, cleanest match" + inventory's ASK-added `HC_HCOR_OPCODE_CC_UPDATE_WITH_AGING` | **CONFIRMS**, three-way (dispatch + alignment + source) |
| 779 words carry `0xd0xx` via `0x04xx`/`0x1xxx` classes | iter-42 (fe-ehash §8.1#3): AC_CC handler reads per-task context page `0xd0xx` | **CONFIRMS** statistically |
| iter-42's "vector-table entry word `0x630`" | dispatch-table addressing (base 0xC0) | **OPEN** — frames don't reconcile; artifact lost |
| Blob header fully enumerated: no capability field exists | caps `0x17` phrasing in several docs | **REFINES** — caps is *driver-derived* (patch `0086a` walks `qe_firmware.id`); not blob-carried |
| FE-VM opcode constants known from lf-5.4 (`0x80000010` STRIP, `0x80000200` TTL_DEC, `0x8000C001` ETH_REBUILD, `0x81000000` ENQ) | fe-ehash §10 opcode chain | **EXTENDS** Phase 3 — interpreter code that decodes these must contain the constants; hunt in the unique islands |
| Slot-19 handler exists but board lacks the HC doorbell (caps bit 3) | reference §5.3.3 "Aging requires Host Command" | **OPEN** — how does vendor invoke aging updates on 210? Phase 6 target |

## Per-document detail

### 1. `fman-microcode-210-programming-reference.md`

- **§3 QEF container — CONFIRMS + EXTENDS.** Every header field re-parsed
  and verified across the 24-blob corpus; the only undocumented field was
  the 4-byte trailer, now solved (raw CRC-32, init 0, no complement) and
  verified on all blobs. Edited: trailer row + solution note added.
- **§1.2 dispatch table — CONFIRMS.** All slot offsets re-verified from the
  freshly pulled blob. The decomp tools' first version had an internal
  24-word base bug (now fixed); the doc's own `0xC0`-relative convention was
  always correct and is what the fixed tools reproduce (slot 8 → byte
  `0x140` exactly as the doc's cross-tier byte-identity proof requires).
- **§1.2 "Basic disassembly probes" — REFINES/CONTRADICTS.** The probe's
  two candidate classes don't survive corpus-wide distribution analysis:
  `0xb3ff`'s low16 is bimodal branch-offset-shaped (see maps/README.md for
  the cascade proof), and `0xe9c9` occurs only 13 times blob-wide. The
  probe's *structural* results (table base 0xC0, the bracketed unrolled
  construct, 210's second instantiation) stand. Edited: dated follow-up
  paragraph appended (original text kept as the record).
- **§1.2 slot table, slot 19 — CONFIRMS.** Three independent signals now
  agree: structurally 210-only slot (doc), target region byte-unique vs
  public blobs (alignment map), and the vendor-source HC opcode name
  (`HC_HCOR_OPCODE_CC_UPDATE_WITH_AGING`, `[ASK]`-added — function
  inventory §2). Edited: corroboration sentence in the post-table note.
- **§12 / caps `0x17` — REFINES.** The blob carries **no** capability field
  (header fully enumerated in Phase 1). `caps=0x17` is produced driver-side
  by patch `0086a` walking the `qe_firmware.id` string
  (fman-pcd-api-reference.md line 45) — i.e. version-string inference plus
  empirical probing, not a blob bitfield. No edit needed (docs never claimed
  a blob caps field); noted here to prevent future misreading.

### 2. `fman-fe-ehash.md`

- **§8.1 item 3 (iter-42 "full disassembly", 2026-06-12) — CONFIRMS in part,
  OPEN in part.** The claim that the AC_CC handler's loads target a per-task
  context page at `0xd0xx` is now statistically corroborated (779 words
  blob-wide carry `0xd0xx` low16; carriers are the `0x04xx`/`0x1xxx`
  classes — which are also 210's growth classes). The claim "PRE_CC is
  near-instruction-identical between 106 and 210" is *consistent* with the
  alignment map (large shared runs exist outside the two unique islands) but
  was not pinpoint-verified. **Unreconciled:** the "vector-table entry word
  `0x630`" address — 0x630 (1584) matches no dispatch-slot target under the
  verified base (nearest: slot 3 → w1626, raw offset 1578) and no known
  NIA/vector numbering. iter-42's scripts were scratchpad-only and are lost;
  committed `decomp/tools/` supersedes them. Re-deriving the `0x630` frame
  is a Phase-3 task. Edited: follow-up note appended to §8.1 item 3.

### 3. `fman-function-inventory.md`

- **HC opcode names — EXTENDS Phase 3.** `HC_HCOR_OPCODE_SYNC` /
  `HC_HCOR_OPCODE_CC` / `HC_HCOR_OPCODE_CC_UPDATE_WITH_AGING` /
  `HC_HCOR_OPCODE_CC_AGE_MASK` (+ debug opcodes gated
  `CONFIG_DBG_UCODE_INFRA`) are named, source-level anchors for the
  106/108 HC handlers — Phase-3 anchor class B material. Slot-19 ↔
  `CC_UPDATE_WITH_AGING` is the model three-way match (dispatch §1.2 +
  alignment map + this inventory).
- **Aging-without-HC — OPEN.** The inventory documents ASK's aging HC
  wrappers; the reference documents "aging requires Host Command"
  (§5.3.3); the board's blob lacks the HC doorbell (caps bit 3 clear) yet
  *contains* the slot-19 aging handler. How does the vendor stack invoke it
  on 210 — another dispatch path, or never? The answer is inside the
  slot-19 region's code (Phase 6 target 1/5). No edit; tracked here.

### 4. `fman-config-value-ledger.md` / `fman-vendor-source-extraction-2026-08-07.md`

- **No microcode-level claims to correct.** The correlation runs the other
  way: their open question — does the *live-packet-side* ehash comparison
  key carry a `portid` byte (the `KG_SCH_KN_PORT_ID`/`kgse_dv0/dv1`
  confound) — is exactly what decomp **Phase 6 target 1** (the FE-VM ehash
  interpreter in the w8584–w12090 island) can answer from the code itself:
  the EXT_HASH FE's key-compare loop reveals the real key layout it walks.
  Added to Phase-6 motivation. No edits to these docs.

### 5. `fman-pcd.md` / `fman-pcd-api-reference.md` / `fman.md` / `muram.md` / `software-stack-ask.md`

- **No controller-ISA claims found** (grep + reads). Their "opcode" tables
  are HM/FE-*descriptor* opcodes (`0x0C` IPV4_FWD, `0x0E` TCP_UDP_UPDATE,
  the §10 `FM_PCD_OPC_*` chain) — data interpreted *by* the microcode, a
  different opcode space from the controller RISC ISA this program
  disassembles. Recorded so future readers don't conflate the two spaces.

## Documents updated from this correlation

1. `arch/fman-microcode-210-programming-reference.md` §3 — trailer row +
   solved-CRC note (pointer to `decomp/01-container.md`).
2. `arch/fman-microcode-210-programming-reference.md` §1.2 — dated follow-up
   to the "Basic disassembly probes" paragraph (distribution-shape
   revision); slot-19 corroboration sentence in the post-table note.
3. `arch/fman-fe-ehash.md` §8.1 item 3 — dated follow-up: statistical
   corroboration + `0x630` open question + tooling supersession pointer.

## Open questions raised by the correlation

1. iter-42's `0x630` addressing frame — which table, which unit?
2. Aging-update invocation path on 210 given no HC doorbell.
3. Identity of the `0x1080`-class hot structure (115 accesses, tight
   `0x0843`–`0x087d` window).
4. Meaning of `0x0082 0000` (110 identical offset-0 words — common
   instruction or data-table row?).
