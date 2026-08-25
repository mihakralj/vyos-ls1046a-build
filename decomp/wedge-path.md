# decomp/wedge-path.md — Locating the Microcode Wedge Mechanism

**2026-08-08 · Goal: find why FE-VM/AC_CC engage or teardown wedges the
board (T-M6-5 and related), and what a soft de-wedge needs to touch ·
Consumes: naming-map.md, the tail-of-image disassembly · Feeds: `fe_recover`
(patch 0163), the ASK2 reversibility contract**

## Why this matters

All recorded FE-VM/AC_CC engage tests
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
came from kernel-side/SDK inference. The disassembly analysis examined the
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
distinguishing logic lives in a base-register computation that remains
didn't trace, and `+0x54`/`+0x58` genuinely are special but the microcode
doesn't need different *code* to treat them that way. Either way: a soft
recovery routine that only touches `+0x54`/`+0x58` (as documented) is
touching two entries in a **~22-entry table** (`0x08`–`0x60`) the microcode
walks as a unit — recovery may need to re-seed more of that table than the
two fields currently named.

**2026-08-08 (later, independent cross-validation from the kernel side):**
while checking for an existing MURAM-physical-address convention,
`bin/kernel-fixups/F_073D.py`'s "F-070c: zero FM_CTL params" disengage
cleanup code turned up — it writes zero to **exactly `pp+0x54` and
`pp+0x58`** (`iowrite32be(0, pp + 0x54/4); iowrite32be(0, pp + 0x58/4);`).
This is a **third, independent source** (alongside this doc's own
microcode disassembly and patch 0163's `fe_recover`) agreeing these two
offsets matter to the pool-management mechanism — the kernel driver's own
disengage-time cleanup targets the identical two fields the tail-of-image
routine manipulates. Doesn't resolve the "same template as ~18 neighbors"
caveat above, but strengthens confidence this is the right region, not a
coincidence.

### A rare, deliberate trap/halt vector guards this routine's entry — **FALSIFIED 2026-08-09 (E-HM18)**

**REVISED**: the "w12665 → 0x3FBAC out-of-range trap" reading was a DECODE
ARTIFACT of the old (wrong) `b7ff` model `(48+imm16)*4`. With the corrected
signed-relative-word model (`target = i + s16(low16)`), **w12665 (`b7fffebb`)
→ w12340 and w12663 (`2e3ffebd`) → w12340 — both in-range**, landing on
`0x7c19f808` (a common prologue, 34 sites in the image). A whole-image census
of all 17 branch families (1550 branches) finds **zero out-of-range targets**.
There is NO trap band at 0x3FBAC-0x40098. See experiments.md E-HM18.

The guard immediately before it (`w12663: 0x2e3f,0xfebd` — now modeled as
`brc2e3f`, a conditional relative branch to **w12340**) sits in front of a
branch to the FE-VM main-loop region (w12340), not in front of a trap. The
"check → trap" two-tier structure is FALSIFIED; both w12663 and w12665 are
normal in-range branches to the same helper.

### Bearing on the wedge mechanism — re-derived 2026-08-09 (E-HM18)

The two-tier picture (soft pool-wait + hard out-of-range trap) is FALSIFIED
in its hard tier — no out-of-range branch exists anywhere. The remaining
structure:

1. **Soft/recoverable tier** (matches F-136/F-069/patch-0163's own
   framing): the per-frame ALLOCATE/DEALLOCATE bookkeeping table (`0x08`–
   `0x60`) gets out of sync — e.g. a slot never gets returned — and a
   *later* ALLOCATE simply **waits** for a free slot that will never
   appear. No fault register trips (this is a legitimate resource-wait, not
   an error), matching the documented "silent WAIT, no fault latched"
   signature exactly. Re-seeding the table (what `fe_recover` does, per
   the current evidence suggests recovery may need to cover more than `+0x54`/
   `+0x58` alone) can restore it without a reboot. **Re-checked under the
   corrected CFG**: w12667 (the per-slot walk) is reached from **w654**
   (`b7ff2eed`), NOT from w12663 — the "guard" at w12663 branches to w12340
   (main loop), so the pool-walk entry is a different call site than
   wedge-path.md assumed.
2. **Hard/unrecoverable tier — FALSIFIED.** There is no out-of-range trap
   branch; w12665 is a normal branch to w12340. Every prior "cold-power-
   cycle-only recovery" case remains valid, but the mechanism is
   NOT a microcode branch into a hardware halt vector.

**Open after E-HM18**: the wedge mechanism must be re-derived from the
corrected CFG. Candidates: (a) the `2c3f`-dispatched handler at w242 (FE
type → engine-internal handler slot, host-unreadable) looping/parking
without re-arming; (b) the pool walk w12667 (entered from w654) starving on
a slot never returned; (c) an FPM/TNUM task-completion handshake never
released. A clean re-test of the E-HM12/13/15 sites under the CORRECTED
model (their patches actually landed on w512/w452/w511/w531, not the
claimed w270/w280/w290) is a candidate next oracle experiment.

## The per-field key-compare candidate (side finding, feeds hitmiss-path.md)

While extending `ehash_walker`'s window (`w3096`–`w3500`) looking for the
byte-compare the earlier disassembly pass did not find, several **tight
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
pointers/parameters rather than one generic 14-byte memcmp — which would
actually make sense given the extraction is known (silicon-confirmed,
2026-07-13) to be **field-based** (SIP, DIP, PROTO, SPORT, DPORT as
distinct fields, not a flat byte array). Reading this as "one small compare
block per key field" is a plausible structural hypothesis, **not yet
oracle-confirmed** — the operation `tst_dc` performs and what `0x1b01`
actually streams from remain unverified.

## Follow-ups not yet executed

- **Re-test the E-HM12/13/15 patch sites under the CORRECTED branch model.**
  E-HM18 showed those patches actually landed on w512/w452/w511/w531 (not the
  claimed w270/w280/w290), and the "trap band" they targeted never existed.
  A clean single-variable re-test (e.g. w242's `2c3ff000` dispatch target,
  or the w654→w12667 pool-walk caller) under the corrected model is the
  highest-value next oracle experiment.
- Drive the pool-exhaustion condition deliberately (controlled pool test)
  and watch whether the "silent wait, no fault" wedge correlates with the
  w12667 walk stalling — this would confirm the soft tier.
- Patch one of the tight-loop's `tst_dc` immediates (`0x28f8`) or bump
  `[0x1b01]`'s apparent stream and watch for a HIT/MISS behavior change on
  a known-HIT config — the same falsifiable-test principle as
  `hitmiss-path.md`'s E-HM1.
- Classify the `0x79` dispatch-stub family (w585-w606) — it is CODE, not
  data (E-HM18), and the entry vector w0 → w585 makes it the first-executed
  instruction stream; its semantics (`0x79 <action> 0xf800`) are the
  FM_CTL action-dispatch chain.

Tools: `decomp/ghidra/scripts/FmanWedgeHunt.py` (whole-image `park` census +
constant search), `FmanAllocDealloc.py` (tail-of-image dump),
`FmanBranchRange.py` (branch-target range census), `FmanKeyCompare.py`
(extended `ehash_walker` window + tight-loop detector).
