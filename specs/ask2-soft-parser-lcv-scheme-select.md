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
