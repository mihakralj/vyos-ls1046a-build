# FE-VM action interpreter — opcode dispatch + handler island (w8648–w9520)

**Status: 2026-08-25 — extends `en-exthash-lookup.asm` past the opcode fetch
into the action interpreter, driven by a standalone SLEIGH-model disassembler
(`decomp/tools/fman-disasm.py`) on the live board blob
(`/tmp/kilo/fman-ucode-mtd3.bin`, mtd3 dump, id "210.10.1", CRC OK).**

This answers the long-open **Q06** ("do FE-VM flow-record opcodes execute in
controller code at all") — **yes**, they execute in this island — and closes
the gap the process doc flagged: `en-exthash-lookup.asm` ended at
`ehash_fetch_action_opcode` (`opcode = *opcode_ptr`) with the per-opcode
handlers undisassembled. The VLAN opcodes (0x11/0x12/0x41/0x42) run here.

## Where the opcode fetch leads

`ehash_derive_opcode_cursor` computes:
- `param_ptr  = record + ((flags & 0x3f) << 2)`
- `opcode_ptr = record + (((flags >> 6) & 0x1f) << 2)`
- `opcode     = *opcode_ptr`   (`ehash_fetch_action_opcode`)

then flows into the **action dispatch** at **w8648–w8649**:

```
w8648  e3c00054   (build dispatch index; 0x54 = QMI-enqueue NIA base seed)
w8649  2c3f0000   br_tbl [0x0000]     ; COMPUTED/TABLE dispatch on the opcode
```

`2c3f` is the table-trampoline class (low16 = table base, index in a register).
So opcode→handler is **table-indexed, never a literal opcode compare** — exactly
consistent with the N02 negative result (no `0x11`/`0x12`/… constants in code).

A second, opcode-class fan-out sits at **w8683–w8723** (two cascades of `brc`):
```
w8685 tst_dc r6,...        ; classify opcode
w8687 op_f0  r0,[0x9b00]   ; read per-opcode dispatch word
w8691 brc -> w9115         ; class handler
w8693 br  -> w9554
w8695 brc -> w9788
w8697 brc -> w9857
w8699 brc -> w10408
w8701 brc -> w10733
...
w8712-w8723 second fan-out -> w8958 / w8975 / w8990 / w9032 / w9082 / w9115
```
The handler bodies for the L2-rebuild opcode group live in the
**w9040–w9520 island** (the "enq_builder" region prior work located but did not
decode).

## Handler island structure (w9040–w9520)

Handlers are delimited by a repeated **epilogue** (the interpreter's
"advance-to-next-opcode + management-index update"), which appears at
**w9067, w9111, w9241–9254, w9435–9448, w9487–9500**:

```
ebce001a   op_eb r14,0x1a          ; r14 = word0 >> 26  (re-extract FE type)
73ee7106   tst_73 r14,0x7106       ; test type == EXT_HASH (0x06)
020cd016   (cursor/context op @ d016)
f14c1b0f   m_f1 r12,[0x1b0f]       ; opcode-list cursor advance
ec230002   (…)
bc3f..     brc  -> (skip)          ; CONDITIONAL skip of the index update
0432d0b8   ld  r18,[0xd0b8]        ; r18 = per-task management index (IC+0xb8)
b43f..     brc -> (skip)
77529104   m_77 r18,[0x9104]       ; r18 = op(r18, workspace[0x9104])
1412d010   st  r18,[0xd010]        ; workspace[0xd010] = r18
ebce0000   op_eb r14,0x0           ; r14 = 0
140ed0b8   st  r14,[0xd0b8]        ; management index = 0   (RESET, conditional)
120ed016   (…)
283ff800   br_tbl [0xf800]         ; -> FM_CTL dispatch window (next opcode / done)
```

The blocks **between** these epilogues are the individual opcode handlers.

### Confirmed handler identifications (by materialized constants)

- **INSERT_L2_HDR (0x41)** — **w9328–w9354** (HIGH confidence). It selects the
  inner EtherType and stores it into the rebuilt L2 header:
  ```
  w9344 op_d9 r4,0x0878
  w9345 op_eb r0,0x0800     ; ETHERTYPE_IPV4
  w9346 brc -> w9350
  w9348 op_eb r0,0x86dd     ; ETHERTYPE_IPV6
  w9350 ld  r0,[0x18d4] ; w9352 m_f1 ; w9353 st [0x18d4]  ; write ethertype
  ```
  This is exactly `create_ethernet_hm`'s `*(u16*)(l2hdr+12) = htons(eth_type)`
  with the IPv4/IPv6 selection. The `[0x18d4]` workspace slot is the L2-rebuild
  scratch.

- **ENQUEUE_PKT (0x01)** — handler containing **w9307** `0x02010000` (ENQ FE
  word-0 materialization), region **w9291–w9435**. Reads task context and
  builds the enqueue descriptor (matches the E2 cold-region site).

- **ENQ_ONLY (0x03) / ALLOCATE-form** — handler containing **w9055** `0x02010000`
  (second ENQ materialization), region **w9040–w9067**.

### VLAN handlers (0x11 STRIP_ETH, 0x12 STRIP_ALL_VLAN, 0x42 INSERT_VLAN)

These are the remaining island handlers reached from the w8712/w9131 sub-
dispatch (`cf15 8041` at w9132/w9134 branch to **w9451** and **w9502**; `cf00`
at w9257/w9291). The w9451–9487 and w9502–9520 blocks are the strip/insert
group. Their **byte-level per-opcode semantics are not yet fully cracked**
(the ALU classes `op_d8/op_d9/op_db/op_f0/m_f4` remain black-box pcodeops per
the SLASpec's G3 caveat), but the structural role is fixed by position and the
shared epilogue.

## The load-bearing structural finding — per-task management index at IC+0xb8

> **STATUS — HISTORICAL ROOT CAUSE, DATAPATH RESOLVED (2026-08-26).** This
> analysis applies only to the retired F-233/F-234 inline FE-VM VLAN opcode
> path. Its ~20-packet freeze is closed architecturally: production VLAN
> pop/push now runs through a CC-leaf → combined HMTD in the separate header-
> manipulation engine, never these FE-VM strip/rebuild handlers, and is
> silicon-validated through R4c. `ask_fe_flow_insert()` rejects VLAN intent so
> this interpreter path cannot be re-entered accidentally. The analysis below
> remains the forensic explanation for why the old path was abandoned; the
> proposed microcode experiments are no longer required for shipping VLAN.

**Every** handler epilogue in the island touches the **per-task management index
at IC `[0xd0b8]`** via the same template (`ld [0xd0b8]` → `m_77 [0x9104]` →
`st [0xd010]` → `st r14=0,[0xd0b8]`), and the reset-to-0 store at the end
(`140ed0b8`) is **guarded by a conditional branch** (`bc3f`/`b43f`) that can
**skip** the update on one path.

Cross-references that make this the prime suspect for the ~20-packet VLAN halt:
- `[0xd0b8]` is one of the hottest per-task IC fields (naming-map §7:
  `0xd000–0xd0ff` hot offsets `0x08/0x18/0x0c/0xb8/0xc0/0xd4`).
- `fman-fe-ehash.md`: `FmPortSetFESupport` publishes a **"5 + tnums management
  index"** — a small, bounded per-port FE resource. On LS1046 (tnums ≈ 16) that
  is ≈ **21 entries** — the same order as the observed **~20-packet** freeze.
- **E-HM9** proved the FE **dealloc / pool-slot-walk** (w12667–w12850, which
  updates params-page `+0x54/+0x58`) is **never reached** for an armed frame.
  A management index consumed per frame but never released would exhaust after
  ≈ 5+tnums frames.
- The **FQ-probe verdict (2026-08-25)**: during the freeze the TX FQ `frm_cnt=0`
  — frames **never reach the TX FQ**; the FE-VM stops producing output after
  ~20. That is consistent with the interpreter failing to obtain a management-
  index slot to process further frames, upstream of ENQUEUE.

**Hypothesis (structural, not yet oracle-proven):** the VLAN opcode handlers
(0x12/0x42, the strip/insert group) drive the epilogue down the path that
**consumes a management-index slot without the reset** (or without the balancing
release that the never-reached dealloc walk would perform), so after ≈5+tnums
VLAN-rebuilt frames the per-task management index is exhausted and the FE-VM
silently stops enqueuing — no ErrFD, no FMFP stall, no params-page `+0x58`
depletion (that counter belongs to the *other*, buffer pool). Plain routed
(INSERT_L2 + ENQ, **no** STRIP group) takes the epilogue path that resets the
index, so it sustains.

### Caveats (do not overstate)
- `[0xd0b8]` is firmly a hot per-task IC field, but its identification **as** the
  "5+tnums management index" is inference from position + the fe-ehash.md
  contract, not an oracle-confirmed label.
- The `op_77`/`op_f0` ALU semantics in the epilogue are black-box; "consumes a
  slot" is the structural reading of `ld/​op/​st [0xd010]` + conditional
  `st 0,[0xd0b8]`, not a proven increment/decrement.
- Which epilogue path (reset vs skip) each specific opcode takes is set by the
  `bc3f/b43f` conditions, whose cc source is a preceding black-box op.

## E-VLAN-1 / E-VLAN-2 — the epilogue hypothesis, oracle-tested and FALSIFIED (2026-08-25)

**This section was never added when these ran — backfilling it now (2026-09-03)
since the doc above still frames the hypothesis as "not yet oracle-proven,"
which is stale.**

Both directions of experiment #2 above were actually run, same day, via the
qef-patch→kexec pipeline, byte-exact delivery confirmed via live DTB readback
each time:

- **E-VLAN-1 (always-SKIP)**: patched the 4 guard sites (`w9072/9246/9440/9492`,
  the `bc3f` half of each pair) to unconditionally skip the reset. VLAN froze
  identically to unpatched (pkt_count 18/11, 0 delta). Negative.
- **E-VLAN-2 (always-RUN)**: patched the same 4 sites to NOP (reset always
  executes unconditionally). VLAN froze identically again (pkt_count 18/10, 0
  delta). Negative.

**Conclusion: both directions of the guarded-reset mechanism leave the freeze
unchanged. The `[0xd0b8]` epilogue is exonerated as the freeze mechanism.**
This is why the project pivoted the next day to `plans/ASK2-VLAN-REARCH.md`
(Option A, CC-leaf→HMTD, bypassing the FE-VM entirely) rather than continuing
this dig — which is also why production VLAN no longer depends on this
interpreter path at all (see the STATUS callout above). The analysis below is
pure forensic follow-up, done 2026-09-03, entirely offline (local blob
disassembly only, zero board time), prompted by a fresh look at whether the
falsification was actually complete.

## 2026-09-03 — correcting an "untested site" theory, and CFG-tracing into the VLAN handlers for the first time

A fresh hypothesis was floated this session: E-VLAN-1/2 only patched 4 of the
5 known `ebce001a`+`73ee7106` sites (`w9067, w9111, w9241, w9435, w9487`),
leaving `w9111` untested. **Direct disassembly disproves this.** The four
sites `w9067/9241/9435/9487` are byte-for-byte IDENTICAL 14-word blocks
containing the full guard+reset pattern documented above — all four were
patched. `w9111` is structurally different and much shorter:

```
w9111  ebce001a   op_eb r14,0x1a       ; re-extract FE type
w9112  73ee7106   tst_73 r14,0x7106    ; test type == EXT_HASH
w9113  283ff800   br_tbl [0xf800]      ; task.redispatch — immediately, no guard
w9114  ffffffff   nop
```

No guard pair, no `[0xd0b8]` touch at all. There is no missed reset-guard
instance. The original falsification was complete, not partial.

**CFG trace into the STRIP_ALL_VLAN (0x12) handler** (entered at `w9451`, via
the `w9133` sub-dispatch `cf158041 -> w9451`) — never done before; prior work
stopped at the shared epilogue block and never traced into the handler bodies
themselves. Found a real conditional fork:
- `w9459 tst_dc r3,0x20f8` → `w9460 b83f001b -> w9487`: jumps **directly into**
  site E's guarded-reset epilogue (the exact site E-VLAN-1/2 toggled both ways).
- Otherwise, falls through the block body (w9461–9484, includes a mid-handler
  `park`/`retry.sm`-family instruction at `w9478`) to `w9485 b3ff097a -> w11911`,
  a generic shared exit used pervasively elsewhere in this region — bypassing
  the reset entirely.

This **reinforces** rather than reopens E-VLAN-1/2: the guard they toggled is
confirmed reachable from STRIP's real execution (not a dead branch), so the
negative result wasn't a false negative from testing unreachable code.

The INSERT_VLAN_HDR (0x42) handler (entered at `w9502`) was disassembled
through `w9528`; no branch back to any of the 4 reset-guard sites was found in
that window. Its own exit path is still uncharacterized.

## 2026-09-03 — a genuinely new candidate: a semaphore-guarded retry loop inside ENQUEUE_PKT's own completion

Scanning the full 12,851-word image for the ISA-confirmed `tnum.alloc`
encoding (`0x7800f900`, mask `0xffe0f800`) turned up 3 hits inside the
ENQUEUE_PKT (0x01) handler (`w9291–9435`, previously characterized only by its
head at `w9307`): `w9286, w9372, w9403`. `w9286` and `w9403` are the same
instruction, occurring in two structurally similar 4-word micro-sequences —
and `w9403`'s occurrence sits inside a real backward-branching loop:

```
w9383  ◄─────────────────────────────┐  loop head
  w9392   ld.sm                      │  acquire a semaphore
  w9393   retry.sm                   │  retry on failed acquisition
  w9394-9397  (arithmetic)           │
  w9399   st.sm                      │  release the semaphore
  w9400-9401  li16/orhi16            │  build a constant
  w9402   unit.submit / unit12.submit│  submit operand to numbered unit
  w9403   unit.read0 / unit12.read   │  read its result
  w9404-9405  li16, branch           │
  w9412   ld.sm                      │  SECOND acquire
  w9413   retry.sm                   │  SECOND retry point
  w9425   cmp32 r4,r0 sel.4          │  loop-exit test: either halfword of r4 zero?
  w9426   brc -> w9383  (else fall through to w9427 -> w9435, site D's epilogue)
```

**Correction to my own first pass**: I initially called `w9403` "tnum.alloc"
from a loose mask match. Cross-referencing the *entire* w9280–9435 region
against the full 201-entry table (not just one pattern) found a **tighter**
match: `unit12.read` (exact mask `0xffe0ffff`, vs. `tnum.alloc`'s looser
`0xffe0f800`), paired with `unit12.submit` immediately before it. The tighter
match is the more trustworthy identification. It may still be the same
physical mechanism as `tnum.alloc` under a different name from the other ISA
capture effort — genuinely unresolved, not a confirmed refutation. Likewise,
what earlier disassembly labeled generically as `park` is more precisely
`retry.sm` — a semaphore-retry, not a generic task yield.

The loop-exit condition (`w9425`) is now precisely decoded, not just
structurally guessed, because it's a tight `cmp32` match: `lhs=r4, rhs=r0
(zero), selector 4 = "either result halfword zero"`. What r4 semantically
represents (a retry counter? a status word?) is not yet traced back to its
origin.

**Why this is a different, and structurally more promising, candidate than
the exonerated `[0xd0b8]` epilogue**: it's a different code region, a
different resource class (a lock + a numbered hardware unit, not a per-task
IC field reset), and it sits on the ENQUEUE handler that both routed and VLAN
chains share as their terminal opcode — so if VLAN's rebuild path (which has
its own separate semaphore-adjacent instruction at `w9478`) causes this loop
to iterate more times than a plain routed frame, it would consume more of
whatever resource "unit 12" and the semaphore guard, on the identical shared
code, explaining the routed-sustains/VLAN-freezes asymmetry without needing
the epilogue mechanism at all. **Not yet oracle-tested.** A read-only
register/IC probe correlating loop-iteration count (or the `unit12.read`
result) between a sustaining routed flow and a freezing VLAN flow would
discriminate this — lower risk than E-VLAN-1/2 since it requires no
microcode write, only reads.

### Full-table cross-reference (2026-09-03)

Re-running every word in `w9280–9435` against the complete 201-entry
instruction table (`arch/fman-instruction-table.html`) — rather than the
coarse prefix-class SLEIGH model (`fman-risc.slaspec`), which still models
`op_eb/op_f0/op_d8/op_db/tst_dc/op_e1/op_ef/op_d9/m_77/m_78/m_f1/m_f4` as
opaque `pcodeop`s with zero semantic claim — resolved nearly every word to a
named instruction (`add32`, `sub32`, `and32`/`or32`, `cmp32`,
`bitfield.xform`/`.merge`, `addlane8`/`andlane8`/`orlane8`, `memw.read`/
`.write`, `ld.sm`/`st.sm`/`retry.sm`, `unit12.submit`/`.read`, `li16`,
`xfer14`/`brbitset14`/`brbitclr14` branch families). This is a real
resolution of *which* named operation runs, still short of full semantics for
most of them (the table's own pseudocode is generic for several, e.g.
`bitfield.xform`), but a large step up from the SLEIGH model's blanket
"opaque function ran" placeholder for this region specifically. The same
cross-reference has not yet been run against the rest of the handler island
(`w9040–9520` outside this window) or the `w12667–12850` pool-drain loop.

## 2026-09-03 (cont'd) — field-level decode of the VLAN handler bodies, and a correction to the "epilogue" reading

Built a second cross-reference pass (`xref2.py`, ad hoc, not checked in) that,
unlike the earlier mnemonic-only cross-reference, also parses each matched
instruction's **bit-layout** from `arch/fman-instruction-table.html` and
extracts real operand field values (register numbers, immediates, MURAM
offsets) from each word — not just "which named op ran." Ran it across
w9451–9560 (the STRIP_ALL_VLAN / INSERT_VLAN_HDR bodies the doc above says are
"not yet fully cracked") and re-ran it across the shared epilogue and the
ENQUEUE_PKT retry loop (w9040–9520, w9283–9435) to check the new field-level
reading against the structural story already on record.

### Correction: the "FE type dispatch idiom" is not a shift+type-test

Every occurrence of the two-word idiom `ebce001a` + `73ee7106` (w9067/9068,
w9241/9242, w9435/9436, w9487/9488, and the short-form site at w9111/9112) has
been described throughout this project's decomp record — this doc included —
as "re-extract FE type (word0>>26)" + "test type == EXT_HASH (0x06)". That
reading came from the coarse SLEIGH model, which has no semantics for the
`op_eb`/`tst_73` prefix classes beyond "opaque pcodeop, family guessed from
position." Cross-referencing against the full 201-entry table gives an exact,
tightly-masked (11 and 22 fixed bits respectively — not the loose 6-8 bit
family matches that would indicate guesswork) different answer:

- `ebce001a` = **`li16 r14, 0x1a`** — loads the *literal* 0x1a into r14. Not a
  shift, not an extraction from word0.
- `73ee7106` = **`task.set_end_nia`**, exact encoding `0x73e00106`/mask
  `0xffe007ff` (HYP-0166, confirmed on LS1046A for controller actions 0x1a and
  0x1e): `task.end_nia = low24(r14)`. It is a **state-set instruction, not a
  conditional test** — it doesn't touch any condition flag, so there's nothing
  here for a following `brc` to test on. (The alternative looser match,
  `unit.config`, only has a 6-bit fixed mask and doesn't fit the doc's own
  "conditional test feeding a brc" story either.)

So the real reading of the epilogue's opening two words is: **arm the task's
End-NIA to controller action 0x1a**, which Chapter 5's IP-acceleration NIA
table (captured separately in qdrant) names as *pre-BMI prepare-to-enqueue* —
right before the epilogue's `task.redispatch` (`283ff800`) hands the task off
through that NIA. Not a type check at all. This is consistent with the
already-established fact (N01–N03) that there are no literal opcode/type
compares anywhere in this island — the "type == EXT_HASH" story was itself
an inference the coarse model invited, not something the model actually
asserted with any confidence.

**Scope of this correction**: confined to this exact two-word form as it
appears in the w9040–9520 island (5 sites). Other `naming-map.md` prose and
earlier commit messages (`86e31532`, `ea06e49a`, `ad28d421`) describe the same
idiom the old way — worth fixing next time either file is touched, not done
here (out of scope for this pass, and low value to backfill on its own).

### `op_77` in the epilogue resolves to `task.set_fqid`

`77529104` (the previously black-box `m_77 r18,[0x9104]` step between the
`[0xd0b8]` read and the `[0xd010]` write) matches `task.set_fqid` — strong
confidence, "probable active-task enqueue-FQID update; all acquired firmware
sites duplicate the two register operands" — both its operands decode to r18.
So the epilogue's `[0xd0b8]` sequence is now legible field-by-field: `r18 =
IC[0xd0b8]` (with a zero-test) → `task.set_fqid(r18, r18)` → `IC[0xd010] =
r18` → (guarded) `IC[0xd0b8] = 0`. **This sharpens, but does not reopen, the
already-exonerated management-index story above**: `[0xd0b8]` isn't just "a
hot per-task field some black-box op touches" — it's read out and installed
as the task's own enqueue FQID before every redispatch. E-VLAN-1/2 already
proved toggling the guarded reset doesn't move the freeze, so this doesn't
change the verdict on that mechanism — it just replaces a `?` with a name.

### STRIP_ALL_VLAN (w9451–9487) — first semantic (not just structural) read

Past documentation only established the entry point and the w9459/9460 fork
into the shared epilogue. Field-level decode of the fallthrough path
(w9461–9484) gives concrete semantics for the first time:

```
w9461  li16   r3, 0x4
w9462  andlane8  r4 = r0 & 0xe000            ; mask bits 15:13 of r0
w9464  memb.write [IC+0x9c] = r4              ; save that 3-bit field
w9466  memw.read  r0 = [r5+0xc0]
w9467  sub32      r0 = r0 - r3(=4)
w9468  memw.write [r5+0xc0] = r0              ; frame-length field -= 4
w9469  memw.readz r4 = [r2+0]                 ; test, conditional skip
w9471  add32      FRAME = FRAME + r3(=4)      ; advance frame base by 4 (guarded)
```

`r0 & 0xe000` extracting bits 15:13 is exactly the **PCP field position
within a VLAN TCI** (a 16-bit TCI is PCP[15:13] | DEI[12] | VID[11:0]), so
`IC+0x9c` reads as "saved VLAN priority" — plausible, not oracle-confirmed.
The `[r5+0xc0] -= 4` / `FRAME += 4` pair is a direct, literal "shrink the
frame by one VLAN tag's width and advance past it" — the first hard evidence
that this handler actually performs the tag removal accounting, not just
occupying the right structural slot. `r5` (frame-length base) and `r2`
(source of the w9469 test) are not yet traced to their producers.

At w9477–9484 the handler runs its own **`ld.sm` / `retry.sm` / `st.sm`**
critical section — address register **r4**, offset +0 (semaphore) / +8
(counter): `ld.sm [r4+0]` → `read [r4+8]` → `add32` → `write [r4+8]` →
`st.sm [r4+0]`. This is the same instruction the doc's "genuinely new
candidate" section (above) found twice inside ENQUEUE_PKT's own retry loop
(w9392–9399 and w9412–9419) — except *that* pair addresses **r7**, not r4.

### INSERT_VLAN_HDR (w9502–9673) — exit path now closed

Extended the decode from w9560 to its actual exit at w9673. Past the r4
critical section (w9544/9545/9551) the handler runs real **checksum
maintenance**: three `csum.init`/`csum.setup`/`dma.bufop`/`csum.result`
groups (w9585–9625, offsets +0x8/+0x20/+0x18/+0x0 into the frame via
`addlane8`), then two RFC-1624-style incremental 16-bit-fold updates
(w9629–9654 and w9655–9673: read old halfword → `alu.special`/`bitfield.merge`
combine → `lsr32i 0x10` + `andi16 0xffff` + `add32` end-around-carry fold →
write back at `[r4+0x6]` and `[r3+0x10]`). This is exactly the shape of "fix
up a stored header checksum after the preceding bytes shifted by 4" — i.e.
INSERT_VLAN_HDR repairs whatever checksum(s) sit downstream of the inserted
tag, not just writing the tag itself.

**The exit**: w9673 is `xfer14 → w11911` — the *exact same* target STRIP_ALL_VLAN's
non-reset-guard path already reached (`w9485 → w11911`, recorded above).
Decoding w11911 onward shows it isn't a dead end or another opcode handler:
w11916 is `task.boundary` (`2c3f0000`, the same word previously read as the
`br_tbl` computed-dispatch trampoline in the coarse model — the fuller table
gives it a real identity: it programs `task.DRD3`/`task.DRD0` from two
registers, i.e. it's staging the *next* task activation's continuation
state). So **w11911 is a shared task-continuation setup point that both VLAN
handlers' completion funnels into** — this closes the "actual exit is still
open" gap from the earlier pass: INSERT_VLAN_HDR's exit is now characterized,
and it's the same shared exit as STRIP's, not a separate one.

### A second, complementary resource-consumption candidate

STRIP_ALL_VLAN and INSERT_VLAN_HDR **each** run their own copy of the
`ld.sm`/counter-increment/`st.sm` idiom (address reg r4), separate from and
in addition to whatever ENQUEUE_PKT's own loop does (address reg r7, twice
per loop pass). A VLAN-carrying frame — STRIP + INSERT + ENQUEUE — therefore
touches this idiom at least 4 times (2 fixed + however many the ENQUEUE loop
iterates); a plain routed frame — INSERT_L2 + ENQUEUE only — touches it only
however many times the ENQUEUE loop iterates, with no STRIP/INSERT_VLAN
contribution. Whether r4 and r7 resolve to the same physical MURAM/semaphore
resource across handlers is **not traced** (their producers weren't followed
back), so this is not yet the same claim as "VLAN drains the ENQUEUE-side
pool faster" — it's a second, independent, additive candidate sitting
alongside the tnum.alloc/unit12 retry-loop candidate already on record, not
a replacement for it. **Not oracle-tested.** Same caveats as above apply:
op_f0/tst_dc-family black-box reads remain where the table itself says
"selected_operand = field_transform(...)" with no resolved selector table,
and no board time was used for any of this — pure offline blob disassembly,
same as the rest of this section.

### Pool-drain loop (w12667–12850) — the other outstanding cross-reference, now run

The full-table cross-reference had not been run against this region either
(flagged above as outstanding, alongside the VLAN handlers). It confirms
rather than revises the existing `pool_status_loop` reading (naming-map §…,
commit `7d138781a98d`): three near-identical `andlane8`-masked nibble
comparisons against a byte at `[r0+8]`/`[r0+c]`/`[r0+10]` (params-page-style
fields), each guarding an increment-and-store-back of that same word — i.e.
per-field debounce counters, as already documented, just now confirmed at
the field level rather than inferred from the coarse model. One new item:
`97410001` at w12710 resolves to **`jmptbl8`** — a genuine indexed jump table
(`pc = pc + imm + 2*(register[index]&7)`), not previously named — dispatching
across the `[r0+0x14]`/`[+0x18]`/`[+0x1c]` reads that follow. Peripheral to
the VLAN-freeze question (E-HM9 already proved this loop is unreached for an
armed frame), so not pursued further than this confirmation pass.

## 2026-09-03 (cont'd) — live silicon probing: the shipped fix re-validated, plus a real crash bug found in diagnostic tooling

Everything above this point was offline blob disassembly. This section is
live board work (serial console to the DUT at `192.168.1.185` via the TCP
relay at `192.168.1.16:5555`, and to the OpenWrt lab peer at `192.168.1.116`
via `192.168.1.16:5556` / direct SSH), done specifically to answer the
question this whole document keeps circling: **does VLAN offload actually
sustain on real hardware today, past the historical 5+tnums=21 ceiling?**

### A real, reproducible kernel panic in `fman_pcd_ic_probe_show`

`cat /sys/kernel/debug/fman_pcd/0/ic_probe` (no prior write) panics the
kernel **every time**, reproduced twice, identical fault both times: `Unable
to handle kernel paging request`, level-1 translation fault, `PC :
fman_pcd_ic_probe_show+0xb4/0x168`. Board auto-reboots cleanly each time
(~3 min, no hard power-cycle needed, `config.boot` reapplies and ASK
re-arms automatically) — so this is safe to trigger, just disruptive.

The bug is real and current, not a stale report: `kernel/common/patches/
board/0169-fman-pcd-fe-obs-canary.patch` documents a "v23" fix for the
*exact same* crash signature (*"a forward-direction read past the mapped
page causes a level-1 translation fault (board-reproduced 2026-07-31, PC in
fman_pcd_ic_probe_show)"*), bounding the scan to stay within `vaddr`'s page.
That fix addresses scanning *past* a valid page — it does nothing if
`fman_pcd_ic_vaddr` itself already points at an unmapped page (e.g. a stale
capture pointer left over after the buffer it pointed at was freed/reused).
Under normal background traffic that's apparently the common case, since it
crashed on the very first read after a fresh boot with no traffic sent
somewhat suggests the pointer gets populated automatically by ordinary
hash-probe/IC-capture activity, not just an explicit manual arm — not
confirmed, would need read of the arming path to be sure. **Do not `cat`
this node without checking whether a capture is fresh; it's a live landmine
in shipped diagnostic tooling, unrelated to anything VLAN-specific.**

### VLAN offload sustains — direct proof, today's image, the hardest case

Built a self-contained test entirely on the OpenWrt lab peer (`.116`), using
Linux network namespaces to force genuine cross-DUT routing (a box's own
locally-owned IP can never be forced onto the wire via routing tricks —
the kernel's `local` table always wins over anything in `main`, confirmed
the hard way after a failed self-loop attempt). Moved `eth3.6` (VID 6,
`10.150.1.0/24`) and a newly-added `eth3.8` (VID 8, `10.250.1.0/24`) into
two separate namespaces on the same physical port, both matching the DUT's
already-armed `vif 6`/`vif 8` config on eth3 (`offload vlan` armed,
both flowtable members). Routing VID6→VID8 through the DUT is a **same-port
VID-to-VID translate** — POP the ingress tag, PUSH a different egress tag —
the specific shape a real historical bug lived in (`ask_vlan_cc.c`'s comment
on `is_push` once collapsing translate flows to PUSH-only, dropping the
strip half; already fixed, this is a live re-check of it).

25-second `iperf3` TCP run, VID6 → VID8, through the DUT:

```
[  5]   0.00-25.01  sec  3.79 GBytes  1.30 Gbits/sec  14853  sender
[  5]   0.00-25.01  sec  3.79 GBytes  1.30 Gbits/sec         receiver
```

Steady ~1.2–1.4 Gbit/s every single second of the full 25s, no drop-off, no
stall — the opposite of the historical signature (which went to exactly 0
after ~21 packets). Confirmed hardware, not software fallback, checked
*during* the run from the DUT side:

- `conntrack -L`: **`[HW_OFFLOAD]`** on both directions (the ASK2-specific
  tag, not just generic nft flowtable offload).
- `cc_test` debugfs: 4 live CC-tree keys on port 0x10 (eth3), real 16-byte
  match/mask rows readable — decoded the bytes by hand: `0a 96 01 6a` =
  10.150.1.106, `0a fa 01 6a` = 10.250.1.106, proto `06` = TCP, ports
  `b7fe`/`14b6` — genuinely programmed for this exact flow.
- `show offload flow`: **"VLAN port 0x10 hardware aggregate: 1,878,607
  packets / 2,749,942,037 bytes"** accumulated — ~89,000× past the
  historical 21-packet ceiling.
- `fe_ehash_stats` stayed empty the entire time — confirms this flow never
  touched the ehash/FE-VM path at all, exactly as Option A's design intends.

Nontrivial TCP retransmits (14,853 over 25s) but bitrate stayed flat
throughout — ordinary congestion-control behavior on this link, not a
freeze signature; a freeze looks like throughput going to exactly zero and
staying there, not fluctuating around a steady mean for 25 straight
seconds.

**Conclusion:** the STATUS callout at the top of this document (Option A
CC+HMTD, silicon-validated through R4c) is re-confirmed live, on today's
image (`2026.09.03-0022-rolling`), in the harder same-port translate case,
not just the simpler cross-port case the original R3b/R4b validation used.
Everything above this section — the epilogue correction, the STRIP/INSERT
handler decode, the tnum.alloc search, the still-unlocated root cause of
*why the old FE-VM path specifically froze* — remains genuinely unresolved
forensic history. It just no longer has any bearing on whether VLAN offload
works, which this section answers directly: yes, confirmed on live silicon,
today.

## Tooling added
- `decomp/tools/fman-isa-xref.py` — field-level cross-reference: for a given
  word range, matches each blob word against the full 201-entry table in
  `arch/fman-instruction-table.html` (tightest-mask match wins), then parses
  that entry's bit-layout to print the real operand values (register
  numbers, immediates, MURAM offsets), not just the mnemonic. `usage:
  fman-isa-xref.py <start_word> <end_word>` (blob path is hardcoded, edit
  `BLOB` for a different capture). This is what produced the STRIP_ALL_VLAN /
  INSERT_VLAN_HDR semantic decode and the `task.set_end_nia`/`task.set_fqid`
  corrections above.
- `decomp/tools/fman-disasm.py` — standalone FMan-RISC disassembler implementing
  the `fman-risc.slaspec` decode model (branch families, prefix8/prefix16
  classes, reg=bits[20:16], `simm16*4` word-relative branch targets). Validated:
  w9055/w9307 = `0x02010000` (ENQ const) exactly as findings.md records. Lets the
  enq_builder island be decoded without a full Ghidra install (Ghidra was wiped
  with `/tmp/kilo`; blob re-fetched from board mtd3 this session).
