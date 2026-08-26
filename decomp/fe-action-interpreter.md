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

## Decisive next step (silicon oracle, not another blind build)

Two experiments discriminate the hypothesis, both using the proven
qef-patch→kexec oracle and/or a read-only probe:
1. **Read `[0xd0b8]` (per-task management index) during a frozen VLAN flow** vs a
   sustaining routed flow — via a small FE debugfs/`/dev/mem` IC-context probe.
   Exhaustion (index pinned at its ceiling, ~5+tnums) confirms it.
2. **Patch the epilogue** so the management-index reset (`140ed0b8`) is
   unconditional (NOP the guarding `bc3f`/`b43f` on the VLAN-handler path), kexec,
   and re-test VLAN sustain. If the freeze clears, the management-index
   consume-without-release is the root cause.

Either result finally converts the ~20-packet freeze from "FE-VM execution
limit, mechanism unknown" to a named, patchable microcode behavior.

## Tooling added
- `decomp/tools/fman-disasm.py` — standalone FMan-RISC disassembler implementing
  the `fman-risc.slaspec` decode model (branch families, prefix8/prefix16
  classes, reg=bits[20:16], `simm16*4` word-relative branch targets). Validated:
  w9055/w9307 = `0x02010000` (ENQ const) exactly as findings.md records. Lets the
  enq_builder island be decoded without a full Ghidra install (Ghidra was wiped
  with `/tmp/kilo`; blob re-fetched from board mtd3 this session).
