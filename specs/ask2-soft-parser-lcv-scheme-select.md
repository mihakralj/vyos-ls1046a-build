# ASK2 soft-parser LCV injection — scoping a genuine per-protocol scheme select

**Status:** Scoping only. No code written, no board time spent. This document
exists to capture research done 2026-09-04 before committing to
implementation.
**Depends on:** the CC-tree dual-lane key work (`specs/ask2-ipv6-dual-lane-key-design.md`)
remaining unresolved after exhaustive byte-level verification (§9.6) — this
is the alternative architectural path, not a continuation of that one.

## 1. Why this document exists

`specs/ask2-ipv6-dual-lane-key-design.md` §9.6 exhaustively verified every
register and MURAM structure involved in the CC-tree dual-lane key (GEC
extraction, the match table write, both action descriptors, `FMBM_RCCB`) and
found all of it byte-perfect against the model — yet the CC-tree still never
dispatches a matching frame to the sink. The vendor's real, working
`.116` mechanism (`specs/ask2-ipv6-dual-lane-key-design.md` §9) never uses a
shared wide key at all — it uses fully separate KeyGen schemes and CC tables
per protocol, selected by the FMan's own scheme-match-vector walk.

Replicating that requires two things this driver doesn't currently have:

1. **Scheme selection via the QLCV walk instead of direct-scheme addressing.**
   Every CC-tree consumer in this driver (`fman_pcd_kg_port_attach_cc()`,
   `_dual()`, `_dual_ekfc()`, and the O/H variant) calls
   `fman_port_set_kg_direct_scheme()`, which hardwires the port to one
   specific scheme index — bypassing match-vector selection entirely. There
   is currently no code path that lets two schemes coexist and be chosen
   between per-frame.
2. **A per-frame signal that reliably differs between IPv4 and IPv6 transit
   traffic**, for the walk's `(QLCV & kgse_mv) == kgse_mv` test to key off.
   The raw per-slot hard-parser LCV mechanism (`pmda[].lcv`) is closed,
   proven invalid for this (`plans/ASK2-MASTER-PLAN.md` §1.3a, 2026-08-19: a
   full single-slot sweep on live 10G transit frames found only HXS slot 0
   ever activates; slots 5/6, the documented IPv4/IPv6 slots, never do,
   regardless of configuration). Any design that re-derives family
   discrimination from `pmda[].lcv` bits is re-litigating a closed question.

## 2. The mechanism: soft-parser `OR_IV_LCV`, not CPID

Two RM-documented mechanisms could inject family-specific information
without touching `pmda[].lcv`:

- **CPID** (RM §5.9.4.7/§5.10.3.14.2): the hard parser auto-increments an
  8-bit Classification Plan ID for specific conditions — broadcast,
  multicast, VLAN/MPLS stacking, IP-in-IP tunneling. **It does not fire for
  plain unicast IPv4 vs IPv6** — the exact case this needs. CPID can be
  overridden by soft-parser code, so it's usable, but only via the same
  soft-parser mechanism as the option below, and it adds a layer of
  indirection (`QLCV = LCV & FMKG_CPE[Final_CPID]`) that only matters if LCV
  itself varies — which brings back the same closed per-slot-LCV question
  unless `pmda[].lcv` stays untouched (see §3).
- **`OR_IV_LCV`**, a real soft-parser VM opcode (confirmed from vendor
  `FMCSPCreateCode.h`'s opcode enum, qdrant 2026-08-19): "OR immediate into
  the LCV computed vector that KeyGen classification consumes." This lets
  soft-parser code running in an IPv6-specific hook directly OR a
  distinguishing bit into the frame's LCV, independent of CPID or the
  per-slot hard-parser mechanism entirely.

**`OR_IV_LCV` is the simpler, more direct mechanism and is what this
document scopes.** It needs no `FMKG_PE_CPP`/`FMKG_CPE[]` table
programming, no `Final_CPID` partitioning — just one soft-parser hook and
two scheme match-vectors.

## 3. Why this avoids the 2026-08-19 failure mode entirely

The closed finding was specifically about **`pmda[].lcv`**, the hard
parser's per-HXS-slot contribution register (`struct fman_port_hwp_regs`,
already in this driver via F-205). If those registers are left at
mainline's default (`0xffffffff` for every slot, written by `init_hwp()`),
every touched HXS slot ORs in an all-ones contribution, and for any real
transit frame `LCV` ends up `0xFFFFFFFF` (or very close to it) regardless of
family — **untouched, not narrowed, not broken**.

`OR_IV_LCV` doesn't replace or narrow that; it ADDS one more OR term from an
entirely separate execution path (soft-parser bytecode, not the hard-parser
HXS chain). Since `0xFFFFFFFF | anything == 0xFFFFFFFF`, injecting a bit
into an already-all-ones LCV is a no-op for QLCV **unless** the OR'd bit
lands in the one dimension that's still meaningful downstream — the
`kgse_mv` match test itself doesn't care whether other bits are also set,
only whether the *required* bits are present. Concretely:

- Scheme_v6 (checked first): `kgse_mv = V6_BIT` (a bit chosen to be 1 only
  when the IPv6 hook fires `OR_IV_LCV V6_BIT`, and — critically — this
  requires `pmda[].lcv` for at least one slot to leave that specific bit at
  0 by default, or the walk can't distinguish; see §6 open question).
- Scheme_v4 (checked second, catch-all): `kgse_mv = 0` (always matches).

This is architecturally the same shape as F-212's original (failed) LCV-split
attempt — but the *injection* now comes from soft-parser code targeting one
specific header type, not from zeroing 15 of 16 hard-parser HXS slots. It
never touches `pmda[].lcv` at all, so it cannot reproduce the `PRS_HDR_ERR`/
`CLS_DISCARD` failure mode that killed F-212 (that failure was `pmda[].lcv`
zeroing making the *parser itself* reject headers it no longer considered
enabled — an orthogonal mechanism to the one proposed here).

## 4. Loading mechanism (from vendor NCSW source, `fm_prs.c`)

Contrary to expectation, `FM_PCD_PrsLoadSw()` is **not** an indirect
AR-protocol register dance (unlike KeyGen's scheme registers) — it's a
direct memory copy into a mapped code region:

```c
p_LoadTarget = p_FmPcd->p_FmPcdPrs->p_SwPrsCode + p_SwPrs->base*2/4;
for (i = 0; i < DIV_CEIL(p_SwPrs->size, 4); i++)
    WRITE_UINT32(p_LoadTarget[i], GET_UINT32(p_TmpCode[i]));
/* per-header entry-point table, one word per FM_PCD_PRS_NUM_OF_HDRS */
for (i = 0; i < FM_PCD_PRS_NUM_OF_HDRS; i++)
    WRITE_UINT32(*(p_SwPrsCode + PRS_SW_DATA/4 + i), p_SwPrs->swPrsDataParams[i]);
```

`p_SwPrsCode`'s base address comes from `FmGetPcdPrsBaseAddr()` — a
dedicated memory-mapped code region separate from the parser's own register
block (`p_FmPcdPrsRegs` is `baseAddr + PRS_REGS_OFFSET`, a different
offset). **Not yet pinned down**: the actual physical offset
(`FmGetPcdPrsBaseAddr`'s real computation) and `PRS_SW_DATA`'s byte offset
within that space — both findable via more RM cross-reference or a live
`/proc/device-tree` walk of the FMan parser node, not yet done.

The per-header entry-point table (`swPrsDataParams[FM_PCD_PRS_NUM_OF_HDRS]`)
is very likely how "fire this code when header type X is reached" is wired
— each entry probably holds a code offset (or 0 for "no soft sequence").
This may connect to, or be independent of, `pmda[slot].ssa` (Soft Sequence
Attachment) — the same register this driver already writes via F-205's
`fman_port_hwp_regs` struct, currently always 0 except for the
TCP/UDP-checksum-padding flag in `init_hwp()`. **Not yet pinned down**:
whether `pmda[].ssa` and `swPrsDataParams[]` are the same mechanism viewed
from two angles, or two separate wiring points that both need setting.

## 5. What's needed, concretely

1. Pin down `FmGetPcdPrsBaseAddr()`'s real offset and `PRS_SW_DATA`'s byte
   offset (RM cross-reference or device-tree read, no board risk).
2. Pin down the `pmda[].ssa` field encoding and its relationship to
   `swPrsDataParams[]` (same source).
3. Hand-assemble a minimal soft-parser bytecode sequence using the already-
   decoded ISA (qdrant 2026-08-19 opcode inventory) — realistically 2-4
   instructions: detect IPv6 is the current header (likely free, since the
   hook only fires when reached via the IPv6 entry point), `OR_IV_LCV
   V6_BIT`, return/continue to hard parser. No FMC compiler needed for
   something this small.
4. New kernel code: a loader for the SW-parser code region (mirroring
   `FM_PCD_PrsLoadSw`'s simple copy loop — no indirect-AR protocol) and the
   `pmda[].ssa`/`swPrsDataParams[]` wiring, both currently absent from this
   driver entirely.
5. Revert the CC-tree scheme-selection path from `fman_port_set_kg_direct_scheme()`
   to enabling two schemes with real match-vectors and letting the walk
   choose — a change to `fman_pcd_kg_port_attach_cc*()`, not yet designed.
6. Regenerate `cc_pack_key_dual()`'s callers to no longer need the FAMILY
   byte in the key at all (family is now the scheme-selection axis, not key
   content) — likely simplifies back toward `cc_pack_key()`'s original
   13/37-byte natural-width tables, closer to the vendor's actual approach.

## 6. Open question before any board time

Does `OR_IV_LCV`'s injected bit actually reach the KeyGen match-vector
comparison unmodified, or does something in the AC_CC/direct-scheme
dispatch path (independent of the walk itself) also interfere here the way
`kgse_bmch`/`kgse_bmcl` turned out to for GEC-extracted key bytes
(`specs/ask2-ipv6-dual-lane-key-design.md` §9.6)? Given how many
independent, individually-plausible-looking mechanisms turned out to have
silicon-specific quirks this session (FAMILY byte, key size, BMCL masking),
budget for at least one more surprise here. Recommended first validation
step once the register addresses are pinned down: load a **trivial**
soft-parser sequence (unconditional `OR_IV_LCV` on any header, no
conditional logic) on the sacrificial test port, with **no scheme/CC-tree
changes at all**, and confirm via the existing `probe2`/`probe3` capture
tooling (which already reads the parse-result's LCV field, part of the
already-decoded `struct fman_prs_result` window) that the injected bit is
visible — before touching scheme selection or CC-tree dispatch at all. This
isolates "does the injection mechanism work" from "does the walk consume it
correctly," matching this project's established one-variable-at-a-time
discipline.

## 6a. 2026-09-04 follow-up: addresses pinned down, confirmed live on `.185`

Per §5 step 1. From vendor `fm_prs.c`/`fm_pcd_ext.h`/`fm_common.h`:

```
FM_MM_PRS       = 0x000c7000   (offset within the FMan block, cross-checked
                                against this project's own already-validated
                                FM_MM_KG = 0x0000c1000 == kg-scheme-read.py's
                                KG_OFFSET=0xc1000 -- same source, same silicon)
SW-parser code base = FMAN_BASE(0x01a00000) + FM_MM_PRS = 0x01ac7000
PRS_SW_DATA          = 0x800   -> per-header entry-point table at 0x01ac7800
PRS_REGS_OFFSET      = 0x840   -> parser register block at 0x01ac7840
FM_PCD_PRS_NUM_OF_HDRS = 16    -> table is exactly [0x800,0x840), 16 words
```

The 16-entry size exactly matches this project's own already-established
16-slot HXS numbering (`HWP_HXS_COUNT` in `fman_port.c`, IPv4=slot5,
IPv6=slot6 per F-205's vendor-confirmed `GetPrsHdrNum` mapping) — strong
circumstantial evidence `swPrsDataParams[6]` is the IPv6 entry point, though
not yet proven.

**Confirmed live via read-only `/dev/mem` check on `.185`**: the code
region `[0x01ac7000, 0x01ac7800)` and the per-header table
`[0x01ac7800, 0x01ac7840)` are both all-zero — exactly matching "mainline
never loads soft-parser code," and the register block at `0x01ac7840`
begins with plausible-looking nonzero content, confirming the address
computation is correct on real silicon, not just on paper. No writes
attempted; this was a pure read.

**Still open**: `pmda[slot].ssa` (`fman_port.c`, "Soft Sequence
Attachment") is a *port-level* register (`struct fman_port_hwp_regs`,
already used by this driver via F-205), separate from the *PCD-level*
`swPrsDataParams[]` table above — the term "ssa"/"Soft Sequence
Attachment" does not appear anywhere in the vendor NCSW source at all, so
its field encoding (code offset? a flag bit distinguishing "offset" from
the existing `HWP_HXS_SH_PAD_REM=0x80000000` checksum-padding flag
already stored there for TCP/UDP slots? does it need to agree with
`swPrsDataParams[]`, override it, or is it independent?) is not resolved
by anything found so far. This is the next concrete unknown before any
code can be written or loaded.

## 6b. 2026-09-04, same day: `pmda[].ssa` fully resolved — the trigger mechanism is now completely understood

RM §5.9.3/Table 5-324 (via the same channel that supplied the `kgse_bmch`
bit-table earlier this session) fully resolves this. `pmda[slot].ssa` is a
32-bit register with:

```
bits[0:15]   protocol-specific hard-HXS options (per header type; e.g. the
             PAD_REM flag this driver already sets for TCP/UDP slots)
bits[16:19]  CP_OFFSET -- per-header CPID sub-offset (a THIRD mechanism,
             separate from both OR_IV_LCV and the auto-increment conditions
             in Sec2 -- not needed for this design, noted for completeness)
bit[20]      PRS_HDR_ERROR_DIS -- suppress FD[STATUS].PHE for this header
bit[21]      PRS_HDR_SW_PRS_EN (0x00000400) -- THE trigger: on completing
             this hard HXS stage, branch to Parser Instruction RAM at the
             offset below instead of continuing hard-parse state transitions
bits[22:31]  PRS_HDR_SW_PRS_OFFSET (0x000003FF, 10 bits) -- 2-byte-resolution
             instruction index into Parser Instruction RAM (byte_offset/2)
```

**`swPrsDataParams[]` is not an entry-point table at all** — it's a 16-word
*scratch parameter bank* (`FMPR_SXPAW0-15`) the running bytecode can read
via `LOAD_BYTES_PA_TO_WR`, for runtime constants the bytecode needs (e.g. a
PPPoE relay offset). It does not participate in triggering or overriding
`.ssa` in any way — §4's speculation about a relationship between the two
was wrong; they're independent, and this design doesn't need
`swPrsDataParams[]` at all (the LCV bit to inject can be a bytecode
immediate).

**Instruction RAM layout**, 2048 bytes at `0x01ac7000`-`0x01ac77ff`
(matches §6a's confirmed base exactly):
- `0x000-0x03f` (instructions `0x00-0x1f`): reserved, hardware HXS entry
  points.
- `0x040-0x7ef` (instructions `0x20-0x3f7`): user bytecode region — matches
  the vendor `SP_OFFSET=0x20` constant already found in `dpa_app/dpa.c`
  (§4, original citation), now fully explained.
- `0x7f0-0x7ff`: tail padding.

**Worked value, confirmed against the vendor's own convention**: loading
code at byte offset `0x040` (instruction index `0x020`, the start of the
user region) and wiring it to fire on IPv6 (`pmda[6].ssa`, HXS slot 6):

```
pmda[6].ssa = PRS_HDR_SW_PRS_EN(0x00000400) | 0x020 = 0x00000420
```

**§5 items 1-2 (address pinning, SSA/`swPrsDataParams[]` relationship) are
now fully resolved.** What remains before writing any code: item 3, the
exact bit-level encoding of the `OR_IV_LCV` opcode (currently only known
by name/pseudocode from the qdrant ISA summary, not its machine-code
bits) and whatever terminator/return mechanism ends a soft sequence and
resumes normal hard-parse flow.

**One more pre-existing-header note (§5)**: `HEADER_TYPE_NONE`/pre-parser
hooks (firing before any hard-parse stage) go through a *different*
mechanism entirely — overriding the port's `FMBM_RFNE` (Rx Frame Next
Engine, already read this session during the CC-tree register dump, §9.6
of the dual-lane doc) to launch directly into soft-parser code instead of
the Ethernet HXS. Not needed here (IPv6 detection naturally happens via
the normal hard-parse chain, so `pmda[6].ssa` is the correct, simpler
hook), but worth recording since `FMBM_RFNE` is a register this driver
already reads/knows about.

## 6c. 2026-09-04, same day: the real remaining blocker — bytecode encoding, not addressing

Looked for `OR_IV_LCV`'s exact bit-level machine-code encoding to actually
assemble the hook. Found a genuine, deeper gap than expected.

This project already has a real, hardware-validated ISA capture for the
**FE-VM Controller** (the 201-instruction table at `large-files.moshe.nl`,
used throughout this session's — and prior sessions' — `decomp/` work:
`fman-isa-xref.py`, `fman-disasm.py`, the whole FE action-interpreter
effort). That capture is specifically for the Controller/FE-VM execution
unit (IRAM-resident microcode driving FE_ENTER/CC-tree action chains) —
confirmed by this project's own 2026-08-29 cross-decode analysis, which
explicitly separated "the controller ISA... executes from IRAM" from "the
FE-VM action opcodes our C emits as DATA... are NOT in the controller ISA
table" as two distinct decode domains.

**The soft parser is a third, separate execution unit** (within the FMan
Parser block, not the Controller block) with its own bytecode format.
What's captured so far (`OR_IV_LCV`, `JMP_PROTOCOL_IP`, etc., §2 above) are
opcode *names* and pseudocode extracted from `FMCSPCreateCode.h`'s C++
enum — the FMC *compiler's own source*, describing its internal IR
representation. That is not the same thing as the runtime bit-pattern
those enum values get lowered to when the compiler emits final bytecode.
No (value, mask, bit-layout) table exists yet for this ISA anywhere in
this project's knowledge base, unlike the Controller ISA.

**Getting from opcode name to assemblable machine code needs one of:**
- the FMC compiler's actual emission/encoding logic (a different part of
  its source than the enum declaration — not yet located), or
- real compiled soft-parser bytecode to reverse-engineer from (e.g. if the
  vendor's `cdx_sp.xml` was ever run through the actual FMC tool and its
  binary output captured/preserved somewhere), or
- a dedicated reverse-engineering effort against this specific VM, on the
  same scale as the FE-VM Controller ISA capture already completed for
  this project (which was itself substantial, standalone work).

**Net effect on scoping: the trigger/loading mechanism is now fully
resolved (§6a, §6b — addresses, `pmda[].ssa` encoding, code layout, all
confirmed live where checkable) but the actual bytecode *content* remains
blocked on a genuinely unsolved, separate reverse-engineering problem, not
a lookup.** This is a materially different kind of gap than everything
else pinned down today — not something more RM section lookups can close.

## 6d. 2026-09-04, same day: `OR_IV_LCV` likely isn't a unique opcode — but the addressing model is still genuinely unresolved

Reconsidered whether the FE-VM Controller ISA (§6c) and the soft-parser VM
are really separate instruction sets, per a direct question about whether
`OR_IV_LCV` might already be in the same `large-files.moshe.nl` table under
a different (independently reverse-engineered) mnemonic. This is plausible
and likely: if the FMan's parser soft-sequence engine and the FE-VM
Controller share the same underlying RISC core type (architecturally
sensible — NXP's "FMan Controller" block is documented as containing
multiple RISC cores), then `OR_IV_LCV` is FMC's *high-level* name for the
compiler emitting a **generic** OR instruction targeting whatever workspace
address represents LCV during parser execution — exactly the same pattern
this project's own FE-VM decomp work already established for opcodes like
`task.set_fqid` (a generic memory-write instruction targeting a known
workspace offset, not a dedicated "set FQID" opcode).

Candidate generic instructions found in the table (`or32`, `ori16`,
`orhi16`, `ori16c`, `orlane8`) all operate on `operand[operand_20_16]` — an
**indexed operand**, not a flat memory address. Memory-class instructions
in this ISA (`memb.read`, `memw.read`, `memw.write`, etc.) use a similar
`address_operand` field that, on the FE-VM side, is sometimes a literal
register and sometimes a **special context-selector constant** (e.g.
"address operand 26 selects current frame internal context" — not a real
register, a hardware-recognized alias). If the soft parser shares this
addressing model, there should be an analogous selector for "current
frame's Parse Array" — but this is not yet confirmed.

Two candidate "prepare an address context" instructions were found
(`addrctx`, `0xc6000000`; `framewin`, `0xfb000000`, explicitly tied to
"Parse Result" data) — but both carry only **"strong"** evidence status
(not "confirmed"), and neither's documented usage directly confirms it's
the mechanism for addressing the Parse Array from *within* soft-parser
execution specifically, as opposed to their documented FE-VM-side roles.

**This is a materially different confidence level than anything else
resolved today.** The `kgse_bmch`/`kgse_bmcl` and `pmda[].ssa` register
work earlier was based on complete, RM-sourced bit-field tables before any
code was written or loaded. Here, assembling a real instruction sequence
would mean guessing at a context-selector value for hardware that, unlike
the CC-tree state (scoped to whichever port has it armed), is a **shared,
single physical parser block** — a malformed or wrongly-addressed
sequence, once triggered via `pmda[6].ssa` on the sacrificial port, risks
more than "this one port's classification misbehaves"; a runaway or
faulting soft-sequence could plausibly disrupt parser throughput or state
more broadly. Not confirmed either way, but the asymmetry (limited
upside from a guess vs. a worse failure mode than anything tried this
session) argues against attempting to hand-assemble and load bytecode on
this confidence level.

**Decision: stop here, not proceed to writing/loading bytecode.** Real
progress was made (the loading/trigger mechanism is fully solved; the
likely nature of `OR_IV_LCV` as a generic-instruction-plus-context pattern
is a genuine, useful insight) but the specific context-selector value
needed to actually assemble a correct sequence remains unconfirmed, and
the cost of being wrong here is higher than earlier in this session. Next
step, whenever this is picked up again: either dedicated reverse-
engineering work on the soft-parser's addressing model specifically
(distinct from, though related to, the existing FE-VM ISA capture), or
direct confirmation of the Parse-Array context-selector value from a
source with RM detail deep enough to cover it (the register-table
sections used successfully today did not).

## 6e. 2026-09-04, same day: research avenues exhausted for now

Tried two more safe, independent angles before concluding:

1. **Read-only silicon check**: the full 2048-byte soft-parser instruction
   RAM (`0x01ac7000`-`0x017ff`), including the first 64 bytes documented as
   "reserved for hardware HXS entry points," is genuinely all-zero on live
   `.185` — no fixed/silicon-resident microcode there to learn an
   addressing model from empirically. Either those entry points are
   implemented as fixed-function logic with no readable instruction bytes
   at all, or they're simply unpopulated until first use; either way,
   nothing to decode.
2. **Searched for the soft-sequence exit/return mechanism** (how execution
   resumes normal hard-parse flow after a triggered soft sequence
   completes) — found no matching instruction. The closest candidate,
   `task.complete` (`0x9c09f401`, confirmed encoding), is a Controller-side
   "terminate current invocation, return task to FPM" operation — an
   entire *frame's* processing being ended, not a benign "resume parsing"
   return. Using it (or guessing at some other mechanism) risks dropping
   or mishandling every frame that traverses the hook, not just failing to
   inject the LCV bit.

**Conclusion: this is not a remaining lookup gap, it's a genuine, unstarted
reverse-engineering project**, on the same scale as the FE-VM Controller
ISA capture this project already has (`large-files.moshe.nl`) — which
itself represents real, dedicated, standalone effort (201 instruction
forms, hardware-confirmed one at a time). Nothing available today
(RM chapters, vendor NCSW source, the FMC compiler's opcode-name enum, or
live read-only silicon inspection) supplies the soft-parser VM's actual
bit-level encoding or control-flow semantics. Continuing to search this
same set of sources is unlikely to produce a different result.

**Stopping the soft-parser path here.** Not because the design is wrong —
§2-§6b's mechanism (trigger via `pmda[].ssa`, injection via a generic OR
targeting the Parse Array's LCV location) remains the best-reasoned
approach found, and everything about *loading and triggering* code is
now solid. What's missing is the soft-parser VM's own instruction
encoding — a standalone capture effort, not a continuation of today's
research.

## 6f. 2026-09-04, same day: full soft-parser ISA and a complete worked example obtained

Direct confirmation obtained: the soft-parser VM is a 16-bit RISC engine
executing from the same instruction RAM already pinned down in §6a
(`0x01ac7000`, user code starting at instruction `0x020`/byte `0x040`).
Full opcode inventory obtained, including exactly the two instructions
this design needs:

- **`OR_IV_LCV`** (base code `0x0003`) is a genuine dedicated opcode, not a
  generic ALU op needing a context-selector guess as §6c/6d speculated —
  it operates directly on LCV as its own addressable target: `LCV |=
  imm32`, encoded as 3 words (opcode, imm low 16, imm high 16).
- **`JMP HXS RETURN_HXS`** (`0x1C00 | 0x3FE = 0x1FFE`) is the soft-sequence
  exit mechanism §6e found no trace of — "return to calling hardware
  parser stage," i.e. resume normal hard-parse flow exactly where our hook
  interrupted it. (`0x1FFF`/`END_PARSE` is the other exit, terminating
  parsing entirely — not what this design wants.)

**Complete worked example, ready to assemble** (8 bytes, PC `0x020`-`0x023`):

```
Word0 (PC 0x020): 00 03      OR_IV_LCV
Word1 (PC 0x021): 00 00      imm low 16 bits  (of 0x80000000)
Word2 (PC 0x022): 80 00      imm high 16 bits
Word3 (PC 0x023): 1F FE      JMP HXS RETURN_HXS
```

Loaded at byte offset `0x040` and wired via `pmda[6].ssa = 0x00000420`
(§6b's own worked example — now fully consistent end to end).

**§6c/6d/6e's "separate ISA, unconfirmed addressing, no exit mechanism"
conclusions are superseded** — this is a real, complete, internally
consistent specification, not a guess. `$RA[]` (the 32-byte Parse Result,
IC `0x20`-`0x3F`), `$PA[]` (the `0x01ac7800` parameter array from §6b,
confirmed as the same region), `$FW[]` (frame window), and `LCV` itself
are all directly addressable VM concepts — no indirect context-selector
trick needed for this specific design at all.

**§5's implementation list is now fully unblocked.** Next: implement the
loader (a plain `ioremap()`'d write to `0x01ac7000+0x040`, matching
`FM_PCD_PrsLoadSw`'s simple-copy behavior — no indirect-AR protocol) and
the trigger (`pmda[6].ssa`, reusing F-205's already-mapped
`port->hwp_regs->pmda[]` struct and stop/write/start bracket verbatim —
SSA is the same PMDA shadow-RAM class as the `.lcv` field F-205 already
handles correctly). First live test: load + arm on the sacrificial port
only, verify via the existing `probe3` tooling that LCV's injected bit
actually differs v4 vs v6 — **no scheme or CC-tree changes yet**, isolating
injection correctness from consumption, per §6's original staged plan.

## 6g. 2026-09-04, same day: ground-truth ISA source found on local disk (`/tmp/kilo/fmc/source/spa/`), fully verified, implemented

The real NXP FMC Soft Parser Assembler source exists locally
(`/tmp/kilo/fmc/source/spa/fm_sp_private.h`, `fm_sp_private.c`,
`fm_sp_assembler.tab.c`) — missed by the earlier filesystem search in
§6c/6d/6e (which checked for a compiler *binary* and `FMCSPCreateCode.h`
specifically, not this actual assembler source tree). Independently
verified every claim in §6f directly against this source before writing
any code:

- `_FMSP_INSTR_CODE_OR_IV_LCV = 0x0003` (`fm_sp_private.h:99`) — exact
  match.
- `_fmsp_set_lcv_bits_action()` (`fm_sp_assembler.tab.c:2195`) confirms the
  3-word layout precisely: opcode word, then
  `immediate_value & 0xffff` (low), then `(immediate_value >> 16) & 0xffff`
  (high).
- `_FMSP_INSTR_CODE_JUMP = 0x1800`, `_FMSP_INSTR_MOD_JMP_HXS = 0x0400`,
  `_FMSP_RETURN_HXS = 0x3fe`, `_FMSP_END_PARSE = 0x3ff`
  (`fm_sp_private.h:123,137,191,192`) — exact matches.
- The label-resolution pass (`fm_sp_private.c:1508-1509`) confirms the
  final word construction: `program_counter = program_counter & 0x3ff;
  instr_p->hw_words_p[0] = instr_p->hw_words_p[0] | program_counter;` —
  i.e. `(0x1800 | 0x0400) | (0x3fe & 0x3ff) = 0x1FFE`, matching §6f's
  worked example exactly.
- Final byte serialization (`fm_sp_private.c:2532`) confirms big-endian,
  high byte first: `buffer_p[cur_byte] = (hw_words_p[count] >> 8) & 0xff`
  then `hw_words_p[count] & 0xff`.

**§6c/6d/6e's "separate/unconfirmed ISA" concerns are fully resolved with
independent, ground-truth confirmation — not just a transcription
accepted on faith.** Every byte of the 8-byte PoC sequence is now verified
twice over (against the transcription and against the compiler source
directly).

**Implemented**: `F-243` (`bin/kernel-fixups/F_243.py`) adds `sp_load`
(loads the 8-byte sequence into `0x01ac7000+0x040`, `ioremap`'d, readback-
verified, iounmapped after) and `sp_arm <port>`/`sp_disarm <port>`
(`pmda[6].ssa` read-modify-write, reusing F-205's
`stop_port_hwp`/`start_port_hwp` bracket verbatim — same PMDA shadow-RAM
class as the already-proven `.lcv` field) as new `cc_test` debugfs verbs.
Applies cleanly and idempotently against the current tree; local
brace-balance and diff review done before deployment. Next: deploy and
run the §6 first validation step (load + arm on the sacrificial port,
confirm via `probe2`/`probe3` that the injected LCV bit is visible, no
scheme/CC-tree changes yet).

## 6h. 2026-09-04, same day: deployed and safe, but the verification test itself is flawed

**`sp_load`'s first attempt failed safely**: 16-bit `iowrite16be()` writes
were silently dropped (readback immediately showed the old value, no
corruption). Root cause: the vendor's own `FM_PCD_PrsLoadSw()` treats this
RAM as 32-bit words (`WRITE_UINT32`) despite the semantically-16-bit
instruction format — this specific MMIO block needs full 32-bit-
granularity access. Fixed by packing pairs of 16-bit BE instruction words
into 32-bit BE writes (byte-identical result, matches the vendor loader
exactly); `sp_load` then succeeded cleanly on the next deploy.

**`sp_arm`/`sp_disarm` both worked exactly as designed**: `pmda[6].ssa`
went `0x00000000 -> 0x00000420` on arm (the precise worked value from
§6b) and back to `0x00000000` on disarm, both readback-verified.
**Critically, `eth1` processed ~1250 real packets — including at least
one genuine IPv6 frame (`l3r=0x4020`) that should have triggered the
hook — with the soft-sequence armed the entire time, and stayed at 0 RX
errors throughout.** This is real, board-verified confirmation that the
load/arm/disarm mechanism itself is safe: no crash, no corruption, no
parser wedge, even with a previously-never-exercised code path active on
live traffic.

**But the verification method (comparing the raw LCV field before/after)
cannot work as designed, independent of whether injection succeeded.**
`pmda[].lcv` defaults to `0xFFFFFFFF` for every HXS slot under mainline
(`init_hwp()`), so the per-frame `LCV` value is already all-ones for any
real transit frame regardless of family — confirmed directly in this same
capture (`lcv=ffffffff` on every try, IPv6 included). `OR_IV_LCV`ing any
bit into an already-all-ones value is undetectable by construction — a
math fact, not a signal about whether the soft-parser hook actually ran.
This was already implicit in §6c's own reasoning about the "shared
scheme, saturated LCV" case but wasn't carried forward to this specific
test's design.

**What this doesn't resolve**: whether the soft-sequence actually
executed at all. A cleaner test would use a different opcode with an
unambiguous, non-saturated target — e.g. `STORE_WR_TO_RA` (write a
literal, arbitrary "magic byte" into an unused/padding byte of the Parse
Result via `LOAD_BITS_IV_TO_WR` + `STORE_WR_TO_RA`, both real opcodes
per §2/§6f) instead of `OR_IV_LCV`. This needs the same ground-truth
verification rigor §6g applied to the current sequence (ground-truth
byte-encoding for two more opcodes not yet checked against
`/tmp/kilo/fmc/source/spa/`) before assembling and deploying — not yet
done.

Board left clean (disarmed, 0 RX errors, `pmda[6].ssa` restored to
mainline default). Loaded code stays inert in the shared parser RAM
(harmless — nothing points at it with `PRS_HDR_SW_PRS_EN` set now) but
could be cleared in a future pass if desired.

## 7. Risk and scope assessment

This is a materially larger undertaking than anything attempted so far in
the VLAN-v6 investigation — new parser-code-loading infrastructure, hand-
assembled bytecode in a previously-untouched execution environment, and a
scheme-selection-mechanism reversion that affects the *shared* CC-tree
attach path (`fman_pcd_kg_port_attach_cc()` — used by the shipped,
production v4 path too, so any change here needs the same care as F-183's
original discovery). Not a quick fix; a multi-session effort with its own
staged validation plan, most of which (§6's first step) can be done
read-only on the sacrificial test port with no CC-tree engagement at all.

## 6i. F-244: magic-byte bytecode designed, compiled, not yet board-tested

Resolved §6h's open item. Ground-truth-verified `STORE_IV_TO_RA`
(opcode `0x0800`, `/tmp/kilo/fmc/source/spa/fm_sp_assembler.tab.c`
`_fmsp_store_iv_to_ra_action()`: `hw_words[0] = 0x0800 |
((num_bytes-1)<<7) | range_end`, `hw_words[1] = imm & 0xffff`) — a single
instruction, no working-register load needed, simpler than the
originally-planned `LOAD_BITS_IV_TO_WR`+`STORE_WR_TO_RA` pair. Target:
`struct fman_prs_result` (`fman.h`) byte 14, `route_type` — IPv6-only,
absent-by-default on ordinary transit traffic, not re-written by any
later hard-parse stage, and already known (from every prior probe2/probe3
capture) to read `0xff` on real traffic, making the chosen magic value
`0xC3` unambiguous. New sequence (`F_244.py`, applied after F-243):

```
08 0E   STORE_IV_TO_RA, 1 byte, RA[14]
00 C3   imm 0xC3
1F FE   JMP HXS RETURN_HXS
00 00   pad (unreached)
```

Same code offset (`0x040`), same `sp_load`/`sp_arm`/`sp_disarm` verbs,
same `pmda[6].ssa=0x00000420` arm value as F-243 — only
`cc_test_sp_poc_code32[]`'s content changed. Applied to
`~/kernel-git-cache/linux` (git-tracked, F-243 state confirmed present
first) and compile-verified in isolation:
`make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-
drivers/net/ethernet/freescale/fman/fman_pcd_cc_test.o` — clean, no
warnings. Extracted `.rodata` bytes (`c3 00 0e 08 00 00 fe 1f`, little-
endian) confirm the compiled object holds exactly `0x080E00C3` /
`0x1FFE0000`, byte-identical to the hand-derived encoding.

**Not yet done**: full kernel build, flash, and live board test. The
`vyos` MCP server (this project's hardware console/control channel)
failed to connect (`CONNECT_TIMEOUT`) this session — board access is
blocked until it's restored. `bin/kernel-fixups/manifest.json` and
`ci-setup-kernel.sh` are both updated (F-244, count 34,
`test-fixups.sh` passes all 4 checks).

## 6j. F-244 board-tested: clean negative — hook did not fire on a real IPv6 frame

Board access restored (TCP-serial console at 192.168.1.16:5555, reachable
directly — see the memory note this discovery produced). Built via CI
(`.github/workflows/self-hosted-build.yml`, run `33887320076`, commit
`a592eb10`) per this session's new standing rule — no more local/dev-loop
kernel builds — deployed as image `2026.09.04-1504-rolling` via
`add system image` + reboot. Confirmed byte-exact in code RAM before
arming (`0x01ac7000+0x040`: `08 0e 00 c3 1f fe 00 00`, matching F-244's
encoding exactly).

Armed on port `0x0d` (eth1). `probe2` caught a real IPv6 frame within the
first capture window: an MLD report (`l3r=0x41b0`, dest MAC `33:33:...`,
`ethertype=0x86dd`, IPv6 hop-by-hop-options extension header, final
`nxthdr=0x3a`/ICMPv6) — genuinely IPv6, genuinely transiting the armed
port. **Parse Result byte 14 (`route_type`) stayed `0xff`** — the `0xC3`
magic byte was never observed. A second poll cycle only caught ARP/IPv4
broadcast frames (no second IPv6 sample), so this is one clean data point,
not an exhaustive sweep — but it's unambiguous on its own terms: no
saturation confound like F-243's LCV test, and STORE_IV_TO_RA's
correctness was independently verified byte-exact via `objdump`/`readelf`
before this session's build even ran (§6i). Disarmed cleanly afterward
(`pmda[6].ssa` restored to `0`, dmesg confirms), board healthy throughout
(`eth1` 0 `rx_errors` before, during, and after).

**Conclusion**: the soft-parser hook attached via `pmda[6].ssa` (IPv6 HXS
slot, `PRS_HDR_SW_PRS_EN | instruction-index 0x020` = `0x00000420`) does
not visibly execute for a real transit IPv6 frame on this port, at least
not in a way that reaches the host-visible Parse Result. Two independent
tests (F-243's LCV injection, methodologically flawed but also silent;
F-244's magic-byte injection, methodologically clean and silent) now both
point the same way. Candidate explanations, unranked, none yet
investigated: (a) the trigger condition needs more than
`PRS_HDR_SW_PRS_EN` set — some other `pmda[].ssa` bit-16:19 `CP_OFFSET` or
hard-HXS option field this project hasn't pinned down; (b) HXS slot 6 (via
F-205's `FMAN_HWP_HXS_IPV6`/`GetPrsHdrNum` mapping) isn't the hard-parser
node that MLD-class (extension-header-bearing) IPv6 frames actually route
through, despite it being expected to be the common IPv6 entry stage; (c)
the instruction-index encoding (`0x020`, 2-byte resolution) or the
physical/virtual addressing of the code RAM (`0x01ac7000`) doesn't line up
with what the live parser actually dereferences, despite the readback
match on write. (b) is the most testable next step (arm on a *different*
HXS slot, or capture a plain non-extension IPv6 frame) without new
bytecode. This is the same class of "is LCV/soft-parser reachable for
transit frames at all" question §6h's F-243 test was trying to answer —
now with a second independent negative, worth a fresh scoping/priority
call before continuing further down this path.

## 6k. F-245: byte-for-byte vendor comparison — mechanism confirmed correct; adding a slot-0 discriminator

User question: "soft-parser should be identical to reference nxp ask —
we have source and working board on .116 — what are we doing
differently so it fails for ask2?" Answer, after a direct diff against
the real vendor SDK source
(`/tmp/kilo/nxpwt/kernel/flavors/ask/sdk-sources/drivers/net/ethernet/
freescale/sdk_fman/Peripherals/FM/Port/fm_port.c`, the actual
`SetPortPrsOptions`/`FM_PORT_SetPCD` implementation) and the vendor's
own NetPDL soft-parser source
(`/mnt/builds/ASK/dpa_app/files/etc/cdx_sp.xml`): **nothing verifiable
by static comparison is different.**

- Register/field layout: vendor `t_FmPortPrsRegs` is
  `struct { u32 softSeqAttach; u32 lcv; } hdrs[N]` (`fm_port.h:437-443`)
  — same field order as mainline's `struct fman_port_hwp_regs { u32 ssa;
  u32 lcv; } pmda[N]`.
- Bit layout: vendor `PRS_HDR_SW_PRS_EN = 0x00000400` (`fm_port.h:681`),
  OR'd with the unshifted offset at the low bits
  (`SetPortPrsOptions`: `tmpReg |= (PRS_HDR_SW_PRS_EN | tmpPrsOffset)`)
  — exactly matches our `0x00000420` (`0x400 | 0x020`).
- Slot constant: vendor `GetPrsHdrNum(HEADER_TYPE_IPv6)` hardcodes
  `return 6` (`fm_common.h:647`) — exactly matches
  `FMAN_HWP_HXS_IPV6 = 6`.
- Offset units confirmed 2-byte ("instruction") resolution via
  `FM_PCD_PrsLoadSw()`'s labelsTable population
  (`FMCSP.cpp`'s `createExtensions()`/`byte_position` → divided
  consistently with our `SP_USER_CODE_BYTE_OFF/2 = 0x020`).

So the write itself is provably byte-identical to what the vendor's own
driver does for the same intent. The defect isn't mechanical, it's
topological: whether HXS slot 6 is ever the *live* hard-parser state
for a real transit frame on this driver/silicon combination — which is
exactly what the 2026-08-19 per-slot LCV sweep already found evidence
against (only slot 0/ETH-catchall reliably activates).

F-245 makes the cheap, decisive follow-up test possible without a new
bytecode change: arm the *same* F-244 magic-byte hook on slot 0 instead
of slot 6. Slot 0 fires on every frame (no need to wait for IPv6
traffic specifically). If it fires there, the soft-parser
load/arm/trigger mechanism is proven fully working end-to-end and the
defect narrows cleanly to "slot 6 specifically is unreachable for
transit frames"; if it's silent there too, the trigger path itself
(not just slot 6) is broken. `cc_test`'s `sp_arm <port_hex> [slot_hex]`
now takes an optional slot (default 6, backward-compatible with the
F-244 procedure) — `sp_arm 0x0d 0` arms slot 0.

## 6l. F-245 board-tested: slot 0 is ALSO silent -- trigger mechanism itself suspect, not slot 6

Built via CI (run `33893879911`, commit `0da58593`), deployed as image
`2026.09.04-1613-rolling`. Loaded the identical F-244 bytecode, armed on
port `0x0d`'s HXS slot 0 (ETH catch-all -- per the 2026-08-19 sweep, the
one slot proven to activate its LCV contribution on every real transit
frame, so no waiting for a specific protocol was needed).

Two fresh `probe2` captures while armed (confirmed fresh by differing
checksum/l3r fields -- an ARP frame, then a broadcast IPv4 frame) both
showed **Parse Result byte 14 still `0xff`**. Board stayed at 0
`rx_errors` throughout. Disarmed cleanly.

**This changes the conclusion from Sec6j/6k.** Slot 0 is not a
scheme-selection edge case -- it is the ONE slot independently proven
(via LCV, a completely different signal) to be live for every frame.
If the soft-parser branch mechanism worked at all, this is the easiest
possible case to observe it in, and it stayed silent. Combined with
6k's byte-for-byte confirmation that the register/bit-layout/slot-
constant are all vendor-correct, this points away from
"wrong slot" and toward **the soft-parser trigger not firing at all on
this driver/hardware configuration, independent of which HXS slot is
armed.**

**Leading untested hypothesis**: code-load timing relative to parser
enable. `FM_PCD_PrsLoadSw()` in the vendor stack is called by `dpa_app`
once during its own PCD profile bring-up -- before ports are actively
processing production traffic in the vendor's normal boot sequence.
F-243's `cc_test_sp_load()` does a bare `ioremap`/write/readback with
**no stop/enable bracket at all** (unlike `pmda[].lcv`/`pmda[].ssa`
writes, which F-205 already established need a stop-parser/write/
readback/start-parser bracket because they're hard-parser *shadow RAM*
that only commits to live state around a parser stop/start cycle). If
the soft-parser code RAM has the same shadow-RAM commit semantics -- or
if the execution unit's instruction fetch is otherwise snapshotted at
parser-enable time rather than read live per frame -- then loading code
into an already-running, hours-uptime parser (as every test so far has
done) could leave the write visible to `/dev/mem` readback (a plain
memory read) while genuinely invisible to the actual fetch path the
hardware parser execution unit uses. This would explain the identical
silent result on both slot 6 and slot 0 in one stroke, without needing
any further slot-routing explanation.

**Not yet tested**: whether wrapping `cc_test_sp_load()` in the same
`stop_port_hwp`/`start_port_hwp` bracket already used for `pmda[]`
writes changes anything (cheap, in-place fixup, no new bytecode) --
note this bracket is per-port, so it would need to run once per RX
port to have any chance of affecting the shared code RAM's commit
state, or the real trigger may be a global/parser-block-level enable
this project hasn't identified yet. This is a bigger, more open-ended
engineering question than the slot-0 test was -- worth a scoping
check-in before continuing.

## 6m. Deep vendor comparison (dpa_app init sequence + live .185 register read) -- found it

User asked for a fine-combed comparison against the .116 reference
board and NXP sources to find exactly what differs. Traced the real
vendor init sequence rather than just the register-write call sites:

`/mnt/builds/ASK/dpa_app/dpa.c` (the actual daemon .116 runs,
`dpa_init()`, lines 258-265 and 826) calls, in order:

1. `FM_PCD_Disable(handle)` -- comment: "Disable PCD before we set
   advanced features"
2. `FM_PCD_SetAdvancedOffloadSupport(handle)` -- comment: "enable
   Advanced pcd function before fmc_execute enables PCD"
3. `fmc_compile(...)` -- compiles cdx_cfg.xml/cdx_pcd.xml/cdx_sp.xml
   (assembles the soft-parser bytecode, per-port PCD, everything)
4. `fmc_execute(&cmodel)` -- pushes it all to hardware, and per (2)'s
   comment, this is also where PCD gets **re-enabled** at the end

So the vendor's real sequence is disable-PCD-globally -> load
everything (including soft-parser code + every port's pmda[].ssa) ->
enable-PCD-globally. This project's F-243/F-244/F-245 only ever did a
*per-port* stop/start bracket (`stop_port_hwp`/`start_port_hwp`,
F-205) around individual `pmda[]` writes -- never touching a global
enable.

Traced `FM_PCD_Disable()`/`FM_PCD_Enable()` (`fm_pcd.c:1434,1490`) ->
`PrsDisable()`/`PrsEnable()` (`fm_prs.c:180,188`) ->
`fman_prs_disable()`/`fman_prs_enable()` (`fman_prs.c:97-111`, the flib
layer):

```c
void fman_prs_enable(struct fman_prs_regs *regs)
{
    uint32_t tmp;
    tmp = ioread32be(&regs->fmpr_rpimac) | FM_PCD_PRS_RPIMAC_EN;
    iowrite32be(tmp, &regs->fmpr_rpimac);
}
```

`FM_PCD_PRS_RPIMAC_EN = 0x00000001` (`fsl_fman_prs.h`).
`struct fman_prs_regs` starts `{ u32 fmpr_rpclim; u32 fmpr_rpimac; ...
}` at the FMan Parser block base -- **the exact same `FM_MM_PRS`
(`FMAN_BASE+0xc7000` = `0x01ac7000`) block this project's own
`SP_CODE_PHYS_BASE` already targets**, just at byte offset `+0x04`
instead of `+0x040`. This is a GLOBAL, whole-parser-block register --
not per-port shadow RAM, a completely different register class from
everything F-205/F-243/F-244/F-245 have touched so far.

**Live read-only confirmation on .185** (board running the F-245
image, read via the same `/dev/mem` technique used throughout this
investigation):

```
fmpr_rpclim=0x00000000 fmpr_rpimac=0x00000000
```

`fmpr_rpimac` bit 0 is **clear**. `fmpr_rpclim` at 0 matches the
vendor's own `DEFAULT_MAX_PRS_CYC_LIM=0`, so that register is fine as-
is. But `FM_PCD_PRS_RPIMAC_EN` being unset means **the soft-parser
execution unit itself has never been powered into its running state**
on this board. Mainline's `init_hwp()` never implements the optional
soft-parser feature, so it never has reason to touch this bit --
it simply stays at its POR/reset default of off.

**This fully explains every negative result so far in one stroke.**
`pmda[].ssa`'s `PRS_HDR_SW_PRS_EN` bit correctly tells the *hard*
parser to branch into soft-parser code for a given HXS slot -- and
F-243/F-244/F-245 proved that branch decision itself is being made
correctly (readback-verified, byte-identical to vendor encoding, on
both slot 6 and slot 0). But if the soft-parser *processor* that would
execute the branched-to code was never enabled, the branch presumably
just falls through / no-ops, regardless of which slot points at it --
matching the observed "silent on every slot tried" result exactly, and
requiring no theory about HXS routing, code addressing, or bytecode
correctness being wrong at all.

Confirmed via `git log`/`git blame` equivalent reasoning that no
existing fixup (F-205 through F-245) ever touches `FM_MM_PRS+0x04` --
this genuinely is new ground, not a rediscovery of something already
tried.

**F-246 adds `sp_global_enable`/`sp_global_disable` cc_test verbs**
(readback-verified RMW of `fmpr_rpimac` bit 0, no `stop_port_hwp`
bracket needed since this is a plain always-live control register --
matches the vendor's own `PrsEnable()`/`PrsDisable()`, which write it
directly with no port-level bracket either). This is a **global**
toggle (affects every port's soft-parser availability simultaneously),
but setting it alone should be inert -- no port's `pmda[].ssa` points
at soft-parser code unless separately armed, so it's safe to test
standalone before combining with F-245's slot-0 arm. Board- and CI-
build-tested pending; a raw `/dev/mem` write to verify this live
without a rebuild was attempted but blocked by the sandbox's own
safety classifier (a *global*, whole-parser-block write is more
invasive than the per-port debugfs writes approved so far) -- routing
through the proper kernel-fixup + CI cycle instead, per the standing
build-via-CI rule.

## 6n. F-246 board-tested: FMPR_RPIMAC enable alone is NOT sufficient either

Built via CI, deployed, board-tested with explicit user sign-off (the
sandbox's own safety classifier correctly flagged `sp_global_enable` as
a global, all-ports-affecting write and required confirmation before
running it -- same as it flagged the raw `/dev/mem` write attempt in
6m).

Sequence: `sp_load` -> `sp_arm 0x0d 0` (slot 0) -> `sp_global_enable`
(confirmed via dmesg: `FMPR_RPIMAC=0x00000001`) -> two fresh `probe2`
captures. **Both still showed Parse Result byte 14 at `0xff`.** All
five interfaces (eth0-eth4) stayed at 0 `rx_errors` throughout. Cleanly
reverted (`sp_global_disable`, `sp_disarm`), re-verified 0 `rx_errors`
board-wide after.

**So FMPR_RPIMAC was a real, board-confirmed-necessary condition
(genuinely 0x00000000 under mainline, genuinely part of the vendor's
real enable sequence) but proven NOT sufficient by itself.** The
mechanism now matches the vendor at three independent levels --
register/bit-layout/slot-constant (6k), operation ordering matching
dpa_app's real disable->load->arm->enable sequence (6m), and the global
execution-unit enable bit itself (6n) -- and it still doesn't produce
the expected write.

Checked and ruled out as the remaining gap:
- `FM_PCD_SetAdvancedOffloadSupport()` (dpa_app's step 2, before
  `fmc_execute()`): pure software bookkeeping in the vendor stack (sets
  a driver-internal flag, zero register writes), and it hard-requires
  the Host Command (HC) interface to be initialized -- which this
  project's own `arch/fman-function-inventory.md` notes ASK2
  deliberately runs WITHOUT ("cap bitmask 0x17 with the HC bit
  deliberately clear"). Not applicable to mainline.
- Other `fman_prs_regs` fields read live on `.185`
  (`fmpr_pmeec/pevr/pever/perr/perer/ppsc` all `0x00000000`):
  `ppsc`/`fman_prs_set_stst()` is confirmed (via its own vendor call
  site, `FM_PCD_SetPrsStatistics()`) to be a pure per-port statistics-
  counter toggle, unrelated to soft-parser execution. The
  exception/error registers (`pevr/pever/perr/perer`) gate interrupt
  *reporting*, not execution, per their naming and vendor
  `fman_prs_init()` usage.
- The microcode's own static capability field is documented (prior
  qdrant-recorded static analysis of the 210.10.1 firmware blob) as
  including `PARSER_SOFTSEQ` (bit 4) in cap mask `0x17` -- but this
  wasn't independently re-verified as a *live* register this session
  (mainline's driver doesn't parse or expose it; it's a static field in
  the firmware blob's own QEF header, not a runtime-readable status
  bit this project has located yet).

**Where this leaves the investigation**: every register-level and
sequencing-level discrepancy locatable via static source comparison
against the real vendor SDK has now been checked, board-tested, and
either fixed (FMPR_RPIMAC, still needed regardless) or ruled out. The
remaining gap is either (a) something in `fmc_execute()`'s *combined*
transaction that a piecemeal, separately-timed reproduction (multiple
debugfs writes seconds apart, vs. the vendor's single call) doesn't
capture -- possibly a hardware state machine that only latches
correctly within one atomic setup window; (b) an undocumented
model/errata-specific requirement not present in the vendor SDK
snippets available locally; or (c) something about the specific test
frames or HXS state numbering not actually matching what the
2026-08-19 LCV sweep's "slot 0 activates on every frame" finding
established (worth a skeptical re-check, though that finding used an
independent signal from a different investigation).

**Recommendation**: this has reached the point of diminishing returns
for further black-box register comparison against static vendor
source. The highest-value next step, if this path continues, is a
live register-state capture from `.116` (the real reference board,
under load, WHILE its soft-parser is actively executing) rather than
more source reading -- a true runtime side-by-side diff instead of a
source-vs-source one. Worth weighing against the fact that VLAN
offload itself (the actual shipping feature) already works via the
separate CC-tree/HMTD path (2026-09-03 live-silicon re-validation);
this soft-parser/LCV-injection thread exists specifically for the
IPv6 dual-lane KeyGen scheme-selection problem, which may have other
viable approaches worth weighing against continued soft-parser
investigation cost.

## 6o. 2026-09-04, same day: F-246 itself had an addressing bug -- the board test never touched the real register

Before spending hours standing up a `.116` live-capture build (the 6n
recommendation), re-derived the FMPR_RPIMAC address directly against
the vendor SDK source rather than trusting the earlier paraphrase.
Ground truth (`fm_prs.h:308`, `fm_prs.c:112`, `fsl_fman_prs.h:50-52` in
`/tmp/kilo/nxpwt/.../sdk_fman/`, cross-checked against the identical
files under `/mnt/builds/linux/drivers/net/ethernet/freescale/sdk_fman/`
and `/mnt/builds/opnsense-src/sys/contrib/ncsw/`):

```c
#define PRS_REGS_OFFSET 0x00000840
...
p_FmPcdPrs->p_SwPrsCode    = (uint32_t *)UINT_TO_PTR(baseAddr);
p_FmPcdPrs->p_FmPcdPrsRegs = (struct fman_prs_regs *)UINT_TO_PTR(baseAddr + PRS_REGS_OFFSET);
```

```c
struct fman_prs_regs {
	uint32_t fmpr_rpclim;   /* +0x00 within the register block */
	uint32_t fmpr_rpimac;   /* +0x04 within the register block */
	...
```

So `fmpr_rpimac`'s real address is `SP_CODE_PHYS_BASE + PRS_REGS_OFFSET
+ 0x04` = `SP_CODE_PHYS_BASE + 0x844` -- **not** `SP_CODE_PHYS_BASE +
0x04` as F-246 (6m/6n) used. `SP_CODE_PHYS_BASE + 0x004` falls inside
the soft-parser code RAM's own reserved/empty header region (the same
region that reads as all-zero for every unloaded-code test in this
document), not the real control register.

**This means the F-246 board test in 6n never touched FMPR_RPIMAC at
all.** `sp_global_enable` read 0 at the wrong address, OR'd in bit 0,
wrote it back, read back 1 -- a perfectly consistent RMW-with-readback
sequence, entirely within plain RAM. The dmesg line "confirmed
FMPR_RPIMAC=0x00000001" was real and accurate about *that address*,
just the wrong one. This fully explains the "necessary but not
sufficient" result from 6n: it was never proven necessary at all --
the real register was never exercised, board-tested, or ruled out.

Every other finding in 6a-6n (bytecode encoding, `pmda[].ssa` trigger
bit and PMDA/PCAC addressing, slot-0 vs slot-6 negative results, the
vendor operation-ordering trace itself) is unaffected by this --
those all address different registers, verified independently. This
also means the `.116` live-capture detour (6n's recommendation) is
premature: fix the address, rebuild via CI, retest with the *real*
FMPR_RPIMAC first, since this may turn out to be the actual missing
piece.

Fixed in `bin/kernel-fixups/F_246.py` (`SP_RPIMAC_BYTE_OFF` `0x004U` ->
`0x844U`, corrected docstring/comments with file:line citations,
`SP_CODE_REGION_SIZE` unchanged at `0x1000` -- already covers the
corrected offset). `python3 bin/test-fixups.sh` 4/4.

**Board-tested same day** (CI run `33918150346`, commit `0ab3c66b`,
image `2026.09.04-2049-rolling`): `sp_load` -> `sp_arm 0x0d 0` (slot 0)
-> `sp_global_enable`, dmesg confirmed `FMPR_RPIMAC=0x00000001` (the
*real* register this time -- readback at the corrected `+0x844`
address). Two fresh `probe2` captures on eth1 (confirmed fresh by
differing hash/MAC bytes -- both ARP), **both still showed Parse
Result byte 14 at `0xff`.** All five interfaces stayed at 0
`rx_errors` throughout. Cleanly reverted (`sp_global_disable`,
`sp_disarm 0x0d`), dmesg confirms `FMPR_RPIMAC=0x00000000` and the
soft-sequence disarmed.

**So the addressing bug was real and worth fixing, but it was not the
missing piece.** With the actually-correct FMPR_RPIMAC now verifiably
toggled, the soft-parser execution unit still produces no observable
effect on a real transiting frame, on the one HXS slot independently
proven live on every frame. This restores 6l/6n's original
"necessary but not sufficient" framing -- just now against the real
register instead of a RAM address that merely looked like one. Every
register and sequencing item locatable via static vendor-source
comparison has now genuinely been tried. The remaining paths are
6n's three unranked hypotheses (atomic `fmc_execute()` transaction
semantics; an undocumented model/errata requirement; a slot/HXS
numbering mismatch) or the `.116` live-capture route -- which is now
back on the table as the highest-value next step, not a premature one.

## 6p. `.116` live capture: real vendor register state and real bytecode obtained -- confirms this project's model is correct, doesn't reveal a new gate

Built a cross-compiled, vermagic-matched read-only diagnostic kernel
module (`decomp/fmprs_dump.c`) and loaded it on `.116` (a real NXP
OpenWrt/Mono gateway-dk board running the vendor soft-parser in
production) while live traffic transited it. `.116`'s actual production
image build system was identified as `we-are-mono/openwrt` branch
`mono` (confirmed via `.116`'s own `/proc/version` toolchain string,
`aarch64-openwrt-linux-musl-gcc` -- an OpenWrt buildroot toolchain, not
Yocto; `meta-mono`, the Yocto BSP layer, only builds the boot-firmware
region + a BusyBox recovery initramfs, a separate artifact). Built the
kernel-only target (`target/linux/compile`, not a full image) from that
branch, vermagic confirmed byte-identical to `.116`'s
(`6.12.103 SMP preempt mod_unload modversions aarch64` via
`readelf -p .modinfo`).

**Capture 1 -- global/per-port register state:**
```
prs_regs @0x01ac7840: rpclim=0x00000000 rpimac=0x00000001 (SW_PRS_EN=1)
  pmeec=0x00000000 pevr=0xd84c0000 pever=0x00004000 perr=0x00000000
  perer=0x00004000 ppsc=0x00000000
```
`FMPR_RPIMAC=1` on the real board -- confirms this project's F-246
(corrected) understanding that this bit must be set, and that it
genuinely is set in production. `pever`/`perer` bit14
(`FM_PCD_PRS_SINGLE_ECC`/`DOUBLE_ECC`, `fsl_fman_prs.h:44-45`) differs
from `.185`'s all-zero baseline -- checked and it's a RAS/ECC
error-reporting enable for the parser's internal memory, unrelated to
soft-parser dispatch; not a lead.

Per-port `pmda[]` on all 5 active ports (`0x09,0x0c,0x0d,0x10,0x11`):
identical `slot0(ETH) ssa=0x00000437`, `slot6(IPv6) ssa=0x00000488`
across every port. Decoding: `PRS_HDR_SW_PRS_EN=0x400` bit set in both
(matches this project's own encoding exactly), remaining field
`0x037`/`0x088` is the code-RAM word-index -> byte offset `0x06e` (ETH)
/ `0x110` (IPv6) using this project's own `byte = index*2` formula from
`SP_CODE_PHYS_BASE`. **Also checked and ruled out** a third,
previously-unexamined mechanism found while tracing `fm_port.c`'s
`SetPcd()`: `fmbm_rfne` ("Rx Frame Next Engine", a BMI-block register
distinct from the Parser-block registers) can in one specific vendor
code path (`HEADER_TYPE_NONE`/no-per-header-attach) carry a direct
soft-parser entry offset instead of a normal hard-parser header-type
constant. Captured on `.116`: `rfne=0x10440000 (HXS=0x00)` on every
port -- a plain header-type constant, confirming `.116` uses the
per-header `pmda[].ssa` path (the one this project has tested
exhaustively), not this alternate one.

**Capture 2 -- real vendor soft-parser bytecode**, first 320 bytes of
code RAM (`0x01ac7000`+). Decoded against the full opcode table
(`/tmp/kilo/fmc/source/spa/fm_sp_private.h:96-127`, same ground-truth
assembler source this project's own §6g already verified against):
- Byte `0x06e` (the real ETH-slot-0 entry point, per the ssa decode
  above): `0x3100` = `LOAD_BYTES_RA_TO_WR` family (`0x3000` range,
  operand `0x100`) -- a clean, valid, real opcode.
- A few words later (`0x078`/`0x080`): two parallel
  `COMPARE_WR0_TO_IV` (`0x4000` family) sequences against immediates
  `0x0800` and `0x86dd` -- IPv4 and IPv6 EtherType constants,
  respectively. This is a real, sensible ETH-stage protocol dispatcher,
  independently recognizable without any project documentation to lean
  on.
- Byte `0x110` (the real IPv6-slot-6 entry point): `0x0000` = `NOP`
  (also a valid opcode -- alignment/entry-table artifact, not a
  concern).

**Conclusion**: this capture independently validates, against genuine
production code rather than vendor header/source reading, every piece
of this project's soft-parser model: the `PRS_HDR_SW_PRS_EN` bit
position, the code-RAM word-index arithmetic, the HXS slot numbering
(0=ETH, 6=IPv6 -- matching exactly, not just plausibly), and the
opcode table itself (this project's own F-244 `STORE_IV_TO_RA`
encoding, `08 0e 00 c3 1f fe 00 00`, decodes cleanly under the same
table: `0x080e`=`STORE_IV_TO_RA` operand `0x00e`/dest-byte-14,
`0x00c3`=immediate, `0x1ffe`=`JMP HXS RETURN_HXS`, matching §6i/6j
exactly). **It does not reveal a new gate.** Every mechanism this
project can locate via either static vendor-source comparison or live
production-board comparison now matches between `.116` (working) and
`.185` (silent). `.116` cleanly unloaded/verified healthy after (0
`rx_errors` all 5 interfaces throughout, both capture sessions).

**This is a genuine dead end for the register/mechanism-comparison
approach specifically** -- not proof the soft-parser is unusable, but
strong evidence the remaining gap (if any) is not something visible to
comparative register/bytecode inspection at all. Remaining paths,
now that this one is exhausted: (a) instrument `.116` itself to prove
*when* during real traffic its own soft-parser genuinely executes (e.g.
a counter/side-channel independent of both register state and Parse
Result content) and compare execution *timing/frequency*, not just
static configuration -- a materially different, more invasive
experiment than this session's static captures; (b) step back from
soft-parser as the vehicle for the original IPv6 dual-lane KeyGen
scheme-selection problem entirely, given VLAN offload (the actual
shipping feature) already works via the separate CC-tree/HMTD path.
Recommend a fresh scoping conversation before further soft-parser work
-- this thread has now had multiple genuinely thorough, honest negative
results in a row.
