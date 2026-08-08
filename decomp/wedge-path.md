# decomp/wedge-path.md — Locating the Microcode Wedge Mechanism

**2026-08-08 · Goal: find why FE-VM/AC_CC engage or teardown wedges the
board (T-M6-5 and related), and what a soft de-wedge needs to touch ·
Consumes: naming-map.md, the tail-of-image disassembly · Feeds: `fe_recover`
(patch 0163), the ASK2 reversibility contract**

## Why this matters

Every board session in this project's history that engages the FE-VM/AC_CC
datapath carries a **teardown-wedge risk** (T-M6-5) and, in its worst form,
an **arm-time wedge** that hangs the port before any traffic is even sent
(2026-08-05 finding: "not one fault latched ... the walk does not error —
it reaches a point with no terminal disposition and simply WAITs"). Two
kernel-side mitigations already exist and are board-validated:

- **F-168** (`FMFP_EXTC` SYNC inserted into the arm path) — fixes the
  immediate arm-time wedge for the cases tested (2026-08-06, repeated
  engage/disengage cycles clean).
- **`fman_pcd_port_recover`** (patch 0163, debugfs `fe_recover`) — a
  documented soft de-wedge for a *different* failure mode: FE workspace-pool
  exhaustion. Its own commit message states the mechanism precisely: "every
  MISS frame through FE_ENTER ALLOCATE consumes one buffer from the
  per-port ... pool (ring index at params-page **+0x54**). If EXIT
  DEALLOCATE does not correctly return the buffer ... all slots drain and
  every subsequent BMI task stalls waiting for an allocation — port goes
  deaf." Depletion counter at **+0x58** must stay 0.

Neither mitigation was derived from reading the microcode itself — both
came from kernel-side/SDK inference. This session went looking for the
actual silicon mechanism the numbers `+0x54`/`+0x58` correspond to.

## What the disassembly shows

### A dedicated pool-management routine at the tail of the image

Scanned the **whole** 12,851-word image (not just `bucket_index`/
`ehash_walker`) for the exact numeric constants patch 0163 uses (`0x54`,
`0x58`, ring-cursor-reinit `0x04`, sentinel `0xff`, slot size `0x200`).
`0x54` and `0x58` each appear as a `ld`/`st` pair **right next to each
other**, at `w12830`/`w12832` and `w12836`/`w12838` — 8 words apart, near
the very end of the 12,851-word image:

```
w12824  ld r2,[0x50]
w12825  op_f0 r2,[0x1301]
w12826  st [0x50],r2
w12827  ?ce r1,0x81
w12828  m_f4 r2,[0x1304]
w12829  brc ...
w12830  ld r2,[0x54]          <- patch 0163's "ring index" offset
w12831  op_f0 r2,[0x1301]
w12832  st [0x54],r2
w12833  ?ce r1,0x81
w12834  m_f4 r2,[0x1302]
w12835  brc ...
w12836  ld r2,[0x58]          <- patch 0163's "depletion counter" offset
w12837  op_f0 r2,[0x1301]
w12838  st [0x58],r2
```

This is a straight-line routine (roughly `w12667`–`w12850`) that walks a
sequence of small offsets — `0x08, 0x0c, 0x10, 0x14, 0x18, 0x1c, 0x20, 0x24,
0x28, 0x2c, 0x30, 0x34, 0x38, 0x3c, 0x40, 0x44, 0x48, 0x4c, 0x50, 0x54,
0x58, 0x5c, 0x60` — each with a `ld`/(validity-check or `op_f0`/`m_f4`
MURAM-touch)/`brc`/`st` template. It reads **per-frame Internal Context**
fields first (`ctx[0x00]`, `ctx[0x08]`, `ctx[0x18]` at `w12652`/`w12654`/
`w12655`), consistent with being invoked **once per frame** — matching
"ALLOCATE happens per FE_ENTER, DEALLOCATE happens per EXIT."

**Refinement of patch 0163's model (disassembly-grounded, not yet
oracle-confirmed):** `0x54` and `0x58` get **exactly the same template** as
their ~18 neighboring offsets — nothing in the instruction stream singles
them out as structurally special ("the ring cursor+pool pointer" vs. "the
depletion counter" vs. "an ordinary slot"). Two readings are both
consistent with this: (a) patch 0163's semantic labels for `+0x54`/`+0x58`
are an SDK-derived approximation that doesn't exactly match how *this*
compiled microcode organizes its own bookkeeping, or (b) the real
distinguishing logic lives in a base-register computation this session
didn't trace, and `+0x54`/`+0x58` genuinely are special but the microcode
doesn't need different *code* to treat them that way. Either way: a soft
recovery routine that only touches `+0x54`/`+0x58` (as documented) is
touching two entries in a **~22-entry table** (`0x08`–`0x60`) the microcode
walks as a unit — recovery may need to re-seed more of that table than the
two fields currently named.

### A rare, deliberate trap/halt vector guards this routine's entry

`w12665: br 0x0003fbac` — an **unconditional** jump to word 65259, far
outside the 12,851-word code image. A whole-image census found only **12
such out-of-range branch targets in 1,446 total branches** (0.8%), and all
12 cluster in a narrow **315-word band** (words 65259–65574, i.e. roughly
0x3FBAC–0x40098 bytes — straddling the 256 KiB mark). This is not a
decode-formula error (if it were, out-of-range targets would be common or
follow a clean per-opcode pattern; instead they're a small, tightly
clustered special case). The most defensible reading: **a
hardware-recognized trap/halt/idle vector**, reached deliberately when a
guard condition fails, not a real fetchable instruction address.

The guard immediately before it (`w12663: unk 0x2e3f,0xfebd` — an
unmodeled, not-yet-classified conditional-skip-shaped opcode, distinct from
every `brc`/`br`/`jmp`/`park` family member already in the slaspec) sits
**directly in front of the pool-management routine's real entry point**
(`w12667` onward). Structurally: *check something about this frame's
context/pool state → if bad, jump to the out-of-range trap → else fall
through to the per-slot walk.*

### Bearing on the wedge mechanism — two tiers, not one

This gives a two-tier picture, consistent with (and sharpening) the
existing kernel-side findings:

1. **Soft/recoverable tier** (matches F-136/F-069/patch-0163's own
   framing): the per-frame ALLOCATE/DEALLOCATE bookkeeping table (`0x08`–
   `0x60`) gets out of sync — e.g. a slot never gets returned — and a
   *later* ALLOCATE simply **waits** for a free slot that will never
   appear. No fault register trips (this is a legitimate resource-wait, not
   an error), matching the documented "silent WAIT, no fault latched"
   signature exactly. Re-seeding the table (what `fe_recover` does, per
   this session's finding possibly needing to cover more than `+0x54`/
   `+0x58` alone) can restore it without a reboot.
2. **Hard/unrecoverable tier** (new, this session): if the guard check
   at `w12663` ever fails — e.g. the per-frame context is inconsistent with
   the pool state in a way the microcode considers unrecoverable — it takes
   the deliberate **out-of-range trap branch** at `w12665`. This is not a
   resource-wait; it looks like a genuine hardware halt vector. If real,
   **no debugfs write can reach or reverse it** — matching every case in
   qdrant's history where only a **cold power-cycle**, never `fe_recover`
   or a warm reboot, restored the board (e.g. the 2026-08-05 "port-wedge"
   finding, and the general T-M6-5 pattern before F-136/F-168 landed).

This is a hypothesis the disassembly *supports structurally* but has not
*proven* — no oracle experiment has deliberately driven the guard condition
at `w12663` to observe the trap firing. See Follow-ups.

## The per-field key-compare candidate (side finding, feeds hitmiss-path.md)

While extending `ehash_walker`'s window (`w3096`–`w3500`) looking for the
byte-compare this session's earlier pass didn't find, several **tight
backward loops** turned up — much better shaped than anything in the
`w2837`–`w3096` window (which was dominated by DMA-poll idioms):

| Loop | Body | Shared constants |
|---|---|---|
| `w3309 → w3304` | 5 words | `op_db r3,0x213d`; `op_d8 r10,0x2138`; `op_f0 r3,[0x1b01]`; `tst_dc r3,0x28f8` |
| `w3339 → w3316` | 23 words | reuses `0x213d`/`0x2138` |
| `w3345 → w3311` | 34 words | reuses `0x213d`/`0x2138` |

The tightest (`w3304`–`w3309`) reads from a fixed small address (`[0x1b01]`,
plausibly a staging-buffer/streaming-read port) each iteration and
`tst_dc`s the result — the right *shape* for "read next chunk; compare;
loop." Constants `0x213d`/`0x2138` **recur across the different nearby
loops**, consistent with several small loops sharing base
pointers/parameters rather than one generic 13-byte memcmp — which would
actually make sense given the extraction is known (silicon-confirmed,
2026-07-13) to be **field-based** (SIP, DIP, PROTO, SPORT, DPORT as
distinct fields, not a flat byte array). Reading this as "one small compare
block per key field" is a plausible structural hypothesis, **not yet
oracle-confirmed** — the operation `tst_dc` performs and what `0x1b01`
actually streams from remain unverified.

## Follow-ups (oracle-gated, not run this session)

- Drive the `w12663` guard condition deliberately (e.g. via a controlled
  pool-exhaustion test) and watch whether the board's known "silent wait,
  no fault" wedge correlates with reaching `w12665`'s trap — this is the
  one experiment that would upgrade "two-tier hypothesis" to "confirmed."
- Patch one of the tight-loop's `tst_dc` immediates (`0x28f8`) or bump
  `[0x1b01]`'s apparent stream and watch for a HIT/MISS behavior change on
  a known-HIT config — the same falsifiable-test principle as
  `hitmiss-path.md`'s E-HM1.
- Classify the `0x2e3f` conditional-skip family (currently unmodeled) —
  it may be a sibling worth adding to the branch family alongside the
  `brc` variants already found 2026-08-08.

Tools: `decomp/ghidra/scripts/FmanWedgeHunt.py` (whole-image `park` census +
constant search), `FmanAllocDealloc.py` (tail-of-image dump),
`FmanBranchRange.py` (branch-target range census), `FmanKeyCompare.py`
(extended `ehash_walker` window + tight-loop detector).
