
# decomp/findings.md — Discovery Log

**Newest first · Every entry states its evidence · "Open question —" marks the unresolved**

This is the running log of everything established about the QEF 210.10.1
blob and its corpus. Phase files hold the *plan*; this file holds the
*facts*. Cross-reference: `arch/fman-microcode-210-programming-reference.md`
(register/AD-level contract, §1.2 has the quantified blob comparison and
dispatch-table attribution).

---

## 2026-08-08 (newest) — CORRECTION: w12849 branches to w12551, not w12830; pool tail is a one-pass slot walk with a shared error/continue exit

**The earlier "pool_status_loop loop-back" reading misdecoded the branch
immediate.** w12849 is `b3fffed6` — imm16 `0xfed6` sign-extends to **−298**,
not `0xffed`/−19. Target = word **12551** (byte `0xc41c`, confirmed by the
listing's own decode `brc 0x0000c41c`). w12849 is the **guard-failure
convergence point** for the pool slot walk: all the per-slot
`ld [off] → op_f0 [0xb01] → brc 0xc8c4 → st [off]` templates (w12675,
w12685, w12698, w12751, w12763, … ≈12+ sites over offsets 0x08–0x60)
converge on w12849, which jumps to the **shared status-check region
w12551** (`7c19f808` reads FM_CTL status `[0xf808]`; `ld r1,[0xd018]`;
`tst_dc r0,0xf838`; `op_eb r1,0x30c` params-base compute; `m_77 r0,[0x300]`;
`m_78 r1,[0xfb00]`) — not back to w12830. So the tail routine
(w12667–w12848) is a **one-pass per-frame bookkeeping walk** over ~22
params-page offsets with a common error/continue exit into w12551, not a
self-refresh loop. Naming-map §3 anchor corrected: `pool_status_loop` →
`pool_slot_walk` (w12667–w12848) + `w12551 shared_status_check`.

## 2026-08-08 (late) — Naming harvest verified against qdrant + NXP; parse-result sub-fields named and applied

Cross-checked the naming vocabulary (qdrant agent-memory + `nxp_docs` on
LS1046ADPAARM.pdf / LSDKUG_Rev21.08.pdf) against `decomp/naming-map.md`:
the harvest is correct and complete for FE types, NIA engines, HCOR
dispatch slots, HM opcodes, FM_CTL params-page fields, and the BMI port
registers (FMBM_RFPNE/RFQID/RCCB/RICP + NIA decode). Two stale labels
corrected in `decomp/ghidra/scripts/FmanLabels.py` (v2): `w583` was
`ipr_timeout` (superseded — slots 13/15/16 target the FM_CTL action-
dispatch table) and `w12849` was `exit_stub` (corrected to
`pool_status_loop_loopback`, with `w12830` named `pool_status_loop`).

**New object names added** (from `struct fman_prs_result`, NXP-copyrighted
mainline `fman.h`, cross-checked vs AN4760 Table 23 + LSDKUG Table 79 FMC
result-array names): the 32 B parse-result sub-fields at IC `0x20–0x3F` —
`lpid, shimr, l2r, l3r, l4r, cplan, nxthdr, cksum, flags_frag_off,
route_type, rhp_ip_valid, shim_off[0..1], ip_pid_off, eth_off,
llc_snap_off, vlan_off[0..1], etype_off, pppoe_off, mpls_off[0..1],
ip_off[0..1], gre_off, l4_off, nxthdr_off` (full table in naming-map §8).
These are read by frame_epilogue (w12133) at IC `0xd031–0xd042` = the
header-offset tail (`shim_off[1]` … `nxthdr_off`, +1 byte into timestamp).
Also labeled `ctx_ad_base` (IC 0xd008, read by the FE-VM entry w214),
`ctx_current_nia` (IC 0xd0c4, the e9c9 cascade target), and the §7
windows (`ad_base_window` 0x8000, `cc_ad_base` 0x8040/0x8050,
`fm_ctl_status_window` 0xf800, +0xf900/0xfb00/0xfc00).

**Base-convention warning recorded** (2026-08-06 derived finding):
AN4760/FMC "parse-array byte" numbering counts from the start of the
annotation region (+16 from `struct fman_prs_result` offsets) — a silent
16 B base mismatch is a known bug class for RICP/contextOffsetInWS work.

**Applied + verified headless**: FmanLabels.py v2 renamed 25 functions
(both `fm_ctl_action_table` at w583/w585 applied cleanly) and labeled 40
ctx/parse/window fields at the correct IC base (`0xd020+`); stale
wrong-base `prs_*` labels (from a first run at `0x20–0x3f`) removed.
`FmanVerifyNames.py` added for post-verify dumps. Project
`/tmp/kilo/ghidra-proj/fman.gpr`, program `fman-code-210.bin`.

---

## 2026-08-08 (newest) — FE-VM decompile: register/window map + 2c3f computed-branch + frame core identified

Full-image address-window census from the regenerated listing (all `ld/st/
m_77/m_78/m_f1/m_f4/op_f0` operands counted) — documented in `naming-map.md`
§7. Highlights:
- **Per-task IC** `0xd000–0xd0ff` (hot offsets 0x08/0x18/0x0c/0xb8/0xc0/0xd4).
- **Per-tnum MURAM workspace slots** `0x0300 + n·0x800` (n=0..12), each with a
  common header at `+0x500–0x548` (uniform per-tnum control block).
- **AD-base / frame-command window** `0x8000` (CC reads AD base from
  `[0x8040]`/`[0x8050]` right before the AD-type extract).
- **FM_CTL status/current-NIA window** `0xf800–0xf8ff` (+0xf900/+0xfb00/
  +0xfc00): the dispatcher's handler-pointer slots.
- **`0x79XX` = DATA, corrected** (w585–w606): the FM_CTL action-dispatch
  table (action codes 0x02 ENQ…0x1e DISCARD → `0xf800` handler slots); the
  older "w583 = ipr_timeout" naming-map label is superseded for slots 13/15/16.
- **`2c3f` = computed-branch / table trampoline** (29 sites): low16 = table
  base; the runtime handler index comes from a register. `2c3ff000` targets
  the `0xf8xx` window. Added to the SLASpec (compiles; disassembly in sync).
- **AD-type extraction idiom**: `c600001e` (shift 30) = AD type bits[31:30]
  (CONT_LOOKUP=1, RESULT=2, BYPASS=3); sibling `c600001a`/`op_eb r14,0x1a`
  (shift 26) = FE type bits[31:26]. Both confirm N01–N03 (field-wise decode).
- **No `0xf6` literal anywhere in the image** — the FE_ENTER opcode (246) is
  never compared as a constant; recognition is structural (AD type + flags).
- **`e9c9` guarded-store cascade** (CC dispatch stub w75–w103): conditional
  stores to `ctx[0xd0c4]` (current-NIA slot) dispatching on the incoming
  action, converging at w104 → `b7ff0217` → w583 action table.
- **frame_epilogue w12133**: per-frame status-assembly loop (reads IC parse-
  result bytes 0xd031–0xd042, assembles 32-bit fields, `tst_73` gates).
- **bucket_index w1928**: reads IC `[0xd048]` (KG hash result) + `[0xe000]`
  (DDR bucket-table base), computes the index (`op_db` shifts/masks) — the
  §4.3/§4.5 documented CRC-64→bucket path, now with the 73/2c3f decode.
- CFG reachability: cc_dispatch (w75) reaches the AD-type-dispatch (w1854)
  and frame_epilogue (w12133); the FE interpreter (w9040) reaches both; the
  pool routine (w12667) is NOT statically reachable from these entries
  (consistent with E-HM9: pool untouched — but 2c3f computed branches may
  route to it, so this is not proof).

## 2026-08-08 — FE-VM decompile restored; the `0x73` family is the FE-VM conditional-test core (the type dispatch is now readable)

Restored the full Ghidra decompile pipeline after `/tmp/kilo` wipe (blob
re-fetched from `.185`, code region = blob offset 244 → `fman-code-210.bin`,
12852 words; import MUST use the full language ID `fman-risc:BE:32:default`,
the short form `fman-risc` fails with "Unsupported language"). Key new decode:
**the `0x73xx` family (190 sites) is the FE-VM's conditional-test core** —
`prefix8=0x73`, `reg=bits[20:16]`, `imm16=low16`, semantics "test reg against
the MURAM word at imm16 (or immediate), set cc; a following `brc` consumes
it" — same role as the already-modeled `tst_dc` (`0xdc`). Added to the SLASpec
as `tst_73` and recompiled; the decompiler now resolves the FE-VM branch
skeleton (cc_dispatch/fm_ctl_a/enq_builder/frame_epilogue all decompile with
real if/while structure). **The FE type dispatch idiom** is `ebce001a`
(`op_eb r14, 0x1a` = shift word0 right 26 → extract type field bits[31:26])
followed by `73ee7106` (`tst_73 r14, 0x7106`, low byte `0x06` = EXT_HASH, the
highest FE type 1..6) — the per-FE-object type check, confirming anchors
N01-N03 (types decoded field-wise, never full-word constants). **Correction:
the region w790–w900 is a DATA TABLE, not code** (44 identical `73f8c420`
records + `ffffffff` pads; all its `2e5f` offsets converge on w1268–w1270) —
so of the 8 `ebce001a`+`73ee7106` sites only the five in the enq_builder
region (w9068/w9112/w9242/w9436/w9488) are live code; w1805/w1832/w1844 sit
in the data table. The FE-VM interpreter core is the enq_builder region
w9040–w9520 (ENQ constant `0x02010000` materialized at w9055). New scripts in
`decomp/ghidra/scripts/`: FmanFEVM.py, FmanFEVM2.py, FmanFullListing.py,
FmanCCDisasm.py. Full listing `/tmp/kilo/fman-listing.txt`, decompiles
`/tmp/kilo/fevm3.log`.

## 2026-08-08 — E-HM9: wedge bisection localizes the wedge to the CC-engine dispatch of a frame to the FE_ENTER AD (before the FE-VM pool machinery)

Full writeup in `decomp/experiments.md` E-HM9. Single-variable bisection
using E-HM8's wedge-after-one-frame as the observable: the M2 scaffold
(CONT_LOOKUP numKeys=0) classifies 3 frames 1:1, delivers to kernel, and
does NOT wedge — the AC_CC dispatch machinery is healthy. The FE-VM chain
wedges after one frame, and the wedge **survives** clearing the FE_ENTER
`ALLOCATE` bit, clearing the EXIT `DEALLOCATE` bit, and bypassing EXT_HASH
entirely (`FE_ENTER w3 → EXIT`). Post-wedge, the FE workspace pool is
correctly configured and **completely untouched** (read index 4, depletion
0, free-list intact) — the FE-VM ALLOCATE never consumed a slot. So the
frame never reaches the FE-VM pool machinery: the wedge/consumption is at
the **CC engine's dispatch of a frame to the FE_ENTER-form AD
(CONT_LOOKUP|ALLOCATE) itself**. Two methodology corrections: FMBM_RGPR is
at **port-base + 0x30C** (not 0x38); and earlier params-page reads were
invalid (mmap not page-aligned to the target — the correct pattern is
pcd-snapshot's single-page-aligned reads). Board left cold-booted and
clean.

## 2026-08-08 (earlier) — E-HM8: armed FE-VM wedges port RX after one frame; earlier armed nulls were frame-less

See `decomp/experiments.md` E-HM8 for the full writeup. The headline facts:
(1) frames were not arriving at `.185`'s eth4 for most of today's armed
test cycles (eth4 kernel RX 0, tcpdump 0 while `.106` transmitted) — the
link works after a cold boot, then the port goes RX-deaf after FE-VM
arming, surviving disarm, recoverable only by another cold boot. (2)
Today's E-HM4/5/6/7 armed null results, the params-page `+0x54/+0x58`
observation, and the FE_ENTER AD w3/w0 corruption canaries are all
invalidated — they were frame-less. (3) With a genuinely-arriving frame
(cold boot → arm → one SYN → `kgse_spc` 0→1, consumed by FMan, kernel RX
unchanged), the DDR record is still byte-for-byte identical — so the
"record never touched" finding is now confirmed with real traffic, and the
fault window is definitively "after KeyGen classification, before the ehash
comparator". (4) The wedge-after-one-frame is a new reproducible silicon
behavior matching `decomp/wedge-path.md`'s predicted pool-drain mechanism,
and is the recommended diagnostic observable going forward (patch
microcode, watch whether the wedge disappears). Also disproven cheaply:
the FM_CTL params-page/`FMBM_RGPR` hypothesis (working vendor board `.106`
has `FMBM_RGPR=0` too); `/dev/mem` port-BMI-block writes don't stick on
6.18.41 while MURAM writes do, and `m.flush()` EINVALs (writes actually
succeed — scripts must skip flush); the `w12667`–`w12850` "pool routine"
is a generic status-refresh loop, not the ALLOCATE pool routine; full-MURAM
diffing is too noisy on a live board.

## 2026-08-08 (earlier) — Methodology bug found: this session's own test script never re-synced KeyGen EKFC after reboot/kexec; corrected retest (E-HM7) still negative

Prompted by the user asking why 13-byte (rather than 14-byte) keys were in
use. Investigating found the live boot-default KeyGen scheme 4 EKFC is
actually **`0x00180006`** (12-byte, `IPSRC1|IPDST1|L4PSRC|L4PDST`, no
PROTO, no PORT_ID) — neither the 13 bytes `AGENTS.md` documents as
"Target EKFC" nor the 14 bytes this whole investigation has otherwise
assumed. Traced the cause: this session's own `T26b-shift-sweep.sh`
(reused unmodified across E-HM4, E-HM5, E-HM6) never calls `fe_kg_ekfc` —
it only builds the ehash side. Every kexec/reboot this session ran before
those three experiments reset KeyGen back to this 12-byte boot-default,
meaning E-HM4/E-HM5/E-HM6 ran with KeyGen extracting a fundamentally
different key than the one written into the ehash table — their specific
conclusions about `ce`/`cf`/`hash_shift` are confounded, though the
observed "record never touched" pattern itself still held. Corrected
retest (E-HM7): explicitly ran `fe_kg_ekfc set 4 801c0006` before arming,
confirmed live via `kg-scheme-read.py` that EKFC was genuinely
`0x801c0006` at arm time, re-ran the standard baseline test. **Still
byte-for-byte identical / `pkt_count=0`** — a properly-synchronized
14-byte portid-prefixed key still does not HIT, now independently
confirmed a third time (after 2026-08-06's discovery and 2026-08-07's
16-candidate batch test). Also flagged, not fixed here: `AGENTS.md` §S6
"Target EKFC" still says `0x001C0006`/13 bytes, stale relative to the
2026-08-06/07 PORT_ID discovery — needs the project owner's correction
since `AGENTS.md` is the binding rules document. Full writeup:
`decomp/experiments.md` "METHODOLOGY CORRECTION" section.
**UPDATE 2026-08-08:** AGENTS.md §S6 has since been corrected to the 14-byte target.

## 2026-08-08 (later) — E-HM5 + E-HM6: `ce`/`cf` isolated and compound-zeroed on silicon — both negative

Direct follow-through on E-HM2's own noted next steps ("test `cf` the same
way to isolate which of the pair matters"; "patch both together for a
stronger perturbation"). Live-read the true current values first
(`w1947=0xce000189` subop `000`; `w1948=0xcf800241` subop `100` — refines,
does not contradict, the schematic notation used earlier). **E-HM5**:
zeroed `w1948` alone, delivered via the proven kexec pipeline (post-kexec
blob md5 matched the precomputed patch exactly), ran the standard armed
test — byte-for-byte identical record, clean faults, correct wiring, same
null result as E-HM2's `ce`-alone test. **E-HM6**: zeroed `w1947` and
`w1948` together (compound, from a fresh pristine baseline) — again
byte-for-byte identical, clean faults, correct wiring. Three independent
mutations of increasing strength on the same 2-instruction chain (`ce`
alone, `cf` alone, both together) now all produce identical null results —
materially stronger evidence for Candidate A (frames never reach this deep
into `bucket_index`/`ehash_walker`) than any single test, since the
compound mutation was the one most likely to show *some* divergence if
these opcodes did anything load-bearing for this flow. Board `.185`
rebooted after E-HM6 and confirmed fully restored to pristine (blob md5
`6f23090a3d5ae8b302ea41fd90a14d4d`, no ehash tables, no armed ports, all
expected links up). Full writeup: `decomp/experiments.md` (E-HM5, E-HM6),
`decomp/hitmiss-path.md` (updated). The next test that would directly
discriminate reachability — a canary write or deliberate infinite loop on
`bucket_index`'s first instruction — carries a materially different risk
profile (possible shared-engine hang affecting all FMan1 ports, recoverable
only by hard power-cycle) and was not attempted; it needs its own
specifically-scoped confirmation.

## 2026-08-08 (later) — E-HM4: hash_shift sweep (0-3), all clean negative

Follow-up to the `nxp_docs` survey below. LSDKUG's "4 lower bits must be
cleared" mask convention turned out untestable through this project's own
software interface at all — `fman_pcd_ehash_table_set()` structurally
requires `mask = 2^n-1` (validated, `-EINVAL` otherwise). The nearest
testable analog, sweeping `hash_shift` (0-3, the field's full range) to see
if the silicon selects a different 16-bit window of the 64-bit hash than
software assumes, was run as **E-HM4**: all three untested values (1, 2, 3;
0 was already covered by the original baseline) came back clean negative —
correctly-computed bucket indices (verified independently in Python),
correct `FMBM_RCCB` wiring, clean fault registers, byte-for-byte untouched
320-byte DDR records after matching traffic, every time. Pure debugfs
configuration sweep, no microcode patch. Exhaustively closes "wrong
shift/window" as a hypothesis class; leaves the low-mask-bits question
itself open (untestable without a microcode patch or a hand-rolled
raw-memory dual-bucket insert, neither attempted). Fourth independent
variation (after E-HM2, E-HM3) producing the identical "wiring perfect,
record never touched" signature. Full writeup:
`decomp/experiments.md` (E-HM4), `decomp/hitmiss-path.md` "new source"
section (updated).

## 2026-08-08 (later) — New NXP documentation source surveyed (nxp_docs qdrant)

A separate `nxp_docs` qdrant MCP server (distinct from this project's own
agent-memory qdrant) became available, indexing `LS1046ADPAARM.pdf` (QorIQ
LS1046A DPAA Reference Manual, Rev 0, 03/2017 — the actual chip-specific RM,
not the LS1043A analog previously relied on) and `LSDKUG_Rev21.08.pdf`
(Layerscape SDK User Guide, Rev 21.08, 09/2022 — full FMan PCD driver + FMC
XML config reference). Surveyed with ~13 focused queries. Full writeup and
the one new testable hypothesis it produced (a possible "4 lower bits must
be cleared" constraint on hash-selection masks, from the RM/driver's
*documented* CC Hash-Table construct — a different, RM-covered mechanism
from this project's own proprietary EXT_HASH FE, so not confirmed to apply)
are in `decomp/hitmiss-path.md`'s "New source" section, dated the same day.
Other yield: a complete FMC Result-Array byte-offset table (confirms `nia`
is a real, 3-byte NXP-documented field, matching this project's own NIA
convention independently); confirmation the LS1046A RM has its own Chapter
5 "Frame Manager (FMan)" (content itself not yet surfaced by any query
tried); two RM table references ("Table 8-398. Table Descriptor (Type =
01)", "Table 8-399. Operation Code Description") that sound directly
relevant to AD/opcode encoding but whose content did not surface — they
appear to belong to a different, more generic "DPAA Reference Manual"
numbering not (yet) present in this qdrant corpus. Nothing found
contradicts any previously-settled fact in this project's own docs.

## 2026-08-08 (later still) — Wedge-mechanism disassembly + key-compare candidate
User asked to find (a) the key-comparison instruction and (b) why the
microcode wedges, and how to unwedge — via disassembly, not further qdrant
archaeology. qdrant was still consulted first (per S1) to gather the
existing kernel-side model to test against: patch 0163
(`fman_pcd_port_recover`/`fe_recover`) documents a workspace-pool-exhaustion
wedge mechanism (ring index at per-port params-page **+0x54**, depletion
counter at **+0x58** — see full mechanism in `decomp/wedge-path.md`), and
F-168 documents a *different* arm-time wedge fixed by inserting an
`FMFP_EXTC` SYNC.

**Wedge mechanism — found a strong disassembly-grounded match, with a
caveat.** A whole-image scan (`FmanWedgeHunt.py`) for the exact patch-0163
constants found `ld`/`st [0x54]` and `ld`/`st [0x58]` pairs sitting right
next to each other (`w12830`/`w12832`, `w12836`/`w12838`) inside a
dedicated straight-line routine at the tail of the 12,851-word image
(`w12667`–`w12850`, `FmanAllocDealloc.py`) that walks ~22 small offsets
(`0x08`–`0x60`) with a uniform read/touch/branch/write-back template, after
first reading three per-frame Internal-Context fields — consistent with a
per-frame ALLOCATE/DEALLOCATE bookkeeping pass. Caveat: `0x54`/`0x58` get
the *exact same* template as ~18 neighboring offsets — nothing
distinguishes them as "the special ring cursor/depletion fields" in the
instruction stream itself, which either means patch 0163's SDK-derived
labels are an approximation of a wider table, or the real distinction lives
in an untraced base-register computation.

**Second, independent finding — FALSIFIED 2026-08-09 (E-HM18):** the
"rare hardware trap/halt vector" reading of `w12665: br 0x0003fbac` was a
decode artifact of the old (wrong) `b7ff` model `(48+imm16)*4`. Under the
corrected signed-relative-word model (`target = i + s16(low16)`), **w12665
→ w12340 and w12663 (`2e3ffebd`) → w12340 — both in-range**, landing on
`0x7c19f808` (a common prologue). A whole-image census of all 17 branch
families (1550 branches) finds **zero out-of-range targets**; the
"0x3FBAC–0x40098 trap band" never existed. The two-tier wedge model's hard
tier is void; see experiments.md E-HM18 for the full correction, and
E-HM18b for the mis-targeted E-HM12/13/15 patches.

**Key-compare — found the best candidate so far, still not confirmed.**
Extending `ehash_walker`'s window further (`w3096`–`w3500`,
`FmanKeyCompare.py`) turned up several tight backward loops (5–34 words) —
a much better shape for a comparison than the DMA-poll-dominated region
found in the earlier pass. The tightest, `w3304`→`w3309`, reads a fixed
small address (`op_f0 r3,[0x1b01]`) each iteration and `tst_dc`s the
result. Nearby loops reuse the same base constants (`0x213d`/`0x2138`),
consistent with several small per-**field** compare blocks (matching the
silicon-confirmed MSB-first SIP/DIP/PROTO/SPORT/DPORT extraction order)
rather than one generic 13-byte memcmp. Plausible, not proven — `tst_dc`'s
actual operation and what `[0x1b01]` streams from remain unverified.

Full writeup, tables, and follow-up oracle experiments:
`decomp/wedge-path.md` (new file). `hitmiss-path.md` updated with the
key-compare candidate. Scripts promoted: `FmanWedgeHunt.py`,
`FmanAllocDealloc.py`, `FmanBranchRange.py`, `FmanKeyCompare.py`.

---

## 2026-08-08 (late) — Ghidra disassembly pass on `bucket_index`/`ehash_walker`, re-triggered by the F-053/`hash_bytes_offset` qdrant controversy

User redirected away from a hypothesis (permutation brute-force of the
E-HM1 hash divergence) and back to ground truth: read the actual microcode.
Re-staged the blob (`/tmp/kilo` had been cleared; re-fetched from `.185`'s DT
property, SHA-256 verified identical), re-imported into a fresh headless
Ghidra project with the `fman-risc:BE:32:default` SLEIGH module (already
compiled+installed at `/opt/ghidra_11.3.2_PUBLIC/Ghidra/Processors/fman-risc/`
from earlier this session), disassembled + decompiled `bucket_index` (w1928)
and `ehash_walker` (w2837) with full raw 32-bit words printed alongside
whatever mnemonic the slaspec resolves.

**Confirmed, disassembly-grounded (upgrade from speculation to fact):**
- `w1944` (`0xe920ffff`): the `0xe9`-prefixed instruction's `regfld` decodes
  to **r0** — the *same* register `w1936 ld r0,[0xd048]` (the KG hash) loaded
  8 instructions earlier, with r0 untouched in between. This independently
  reproduces, via the real disassembler (not manual hex decode), the
  2026-08-08-mid-7 entry's "`e9 r0,0xffff` masks the hash register" claim.
  The *operation* `e9` performs is still unconfirmed — only the
  register-chaining (e9 operates on the hash register, not some unrelated
  register) is now disassembler-verified.
- A second, previously-undocumented read: `w1946` (`0x0604d040`, prefix8=
  `0x06`, NOT the modeled `0x04` `ld` rule — a sibling opcode, itself
  unconfirmed) reads Internal-Context offset **0x40** — 8 bytes *before* the
  hash at 0x48. This is per-frame IC space, not FE-descriptor/scheme-config
  space, so it does **not** look like a read of the AD-word's
  `hash_bytes_offset` field (F-053/2026-08-07 qdrant finding) — that field
  lives in the scheme/FE-descriptor, addressed differently (via
  MURAM-addressing ops like `op_f0`/`m_77`/`m_78`), not as a fixed IC offset.
- In `ehash_walker`, the `op_eb`/`op_e1` pair (both modeled pcodeops) computes
  DDR-record byte offsets using **compile-time immediate constants** — `8`
  (twice, w2856/w2943) and `0xc`/12 (three times, w2899/w2915/w2922) — baked
  into the instruction encoding, **not loaded from any descriptor/register
  field**. This is real evidence a "+8 byte-offset" mechanism genuinely
  exists in silicon (the F-053 author's original mechanism intuition was
  onto something real), but it is a fixed compiled-in behavior, structurally
  independent of wherever (if anywhere) the SDK's separate AD-word
  `hash_bytes_offset` bits[17:16] get consumed — no load of such a field was
  found feeding into either `op_eb`/`op_e1` pair in this window.
- `ehash_walker` self-loops (`jmp 0x00002c54`, back to its own entry) at
  least twice (`w2983`, `w3008`) — confirms a per-bucket-entry walk loop, as
  documented. The loop body contains ~8 repetitions of a
  `op_f0`(issue-DMA)/`unk`(go-bit?)/`park`(spin-wait)/`op_f0`(read result at
  fixed `[0xb01]`) idiom — a DMA-issue-and-poll pattern, not obviously a
  byte-comparison loop.

**Correction / honesty check on existing docs — self-corrected within this
same pass.** First wrote (in an earlier draft of this entry) that `ce`/`cf`
chaining onto the hash register was unconfirmed and that the mid-7 entry's
"hash-register op chain ... → ce → cf" was speculation dressed as fact.
That was **wrong** — manually decoding `regfld` for `w1947`/`w1948` using
the *exact same field position* (`bits[20:16]`) every modeled opcode in the
slaspec already uses gives `regfld=r0` for **both** `ce` and `cf`, same as
`e9`. So: **`e9(r0,0xffff) → ce(r0,0x0189) → cf(r0,0x0241)` is a real,
consistent three-instruction chain on the hash register**, confirmed by
the same decode method used for every other opcode in this ISA (not proof
by analogy for `ce`/`cf` specifically, since they have zero slaspec rules
of their own — but the field convention is uniform across every *other*
opcode observed, so extrapolating it here is reasonable, not a stretch).
The `subop` bits (23:21, "flags" per the slaspec comment) differ across the
three: `e9`=`001`, `ce`=`000`, `cf`=`100` — plausibly a variant selector
(consistent with two *different* shift/mask sub-steps).

**What is still genuinely unconfirmed** (this is the part the mid-7 entry
correctly hedged and that still stands): the *operation* `ce`/`cf` perform.
Zero slaspec rules match `prefix8=0xce`/`0xcf` — Ghidra can only report the
raw operand bytes (`0x0189`, `0x0241`), not semantics. Those immediates are
not small integers in the 0–63 range one would expect for a literal
"shift by N bits" encoding, so "shift" remains a plausible label inherited
from the mid-7 entry, not a confirmed operation — it could equally be an
address, a table index, or something else. Confirming needs the oracle
(patch one of `0x0189`/`0x0241` and observe bucket placement on silicon),
exactly as the mid-7 entry already said.

**Not found in this pass:** the actual per-byte (or per-word) flow-key
comparison loop. The `tst_dc` instances visible in a 260-word window
(`w2837`–`w3096`) are DMA-status polls (each paired with an adjacent `park`)
or isolated conditional checks, not an obvious tight loop comparing 13 key
bytes against a fetched record. The region is larger and more tangled than
the original ~90-word `ehash_walker` estimate assumed — likely several
logically distinct sub-handlers/FE-type cases concatenated, not a single
clean function. Locating the real compare needs either (a) much deeper
static reading across the full extent, or (b) an oracle experiment (e.g.
patch one candidate `tst_dc`'s immediate and observe HIT/MISS effect on
silicon) — the latter is exactly the kind of controlled, falsifiable test
this program is supposed to prefer over static guessing.

**Bearing on the F-053/`hash_bytes_offset` qdrant controversy (2026-08-07):**
this pass neither confirms nor refutes the SDK-derived claim that
`hash_bytes_offset` (AD-word bits 17:16) drives live bucket-index derivation
— no load of that specific field was located feeding into the bucket-index
or record-offset arithmetic examined here. It *does* independently confirm
a real, hardcoded "+8" record-navigation behavior exists in the walker,
which is consistent with — but not proof of — the original F-053 mechanism
being real while being a different register/field than the AD-word's
`hash_bytes_offset`. This question remains open pending either deeper
disassembly or a silicon oracle test.

Tools: `/tmp/kilo/ghidra_scripts/FmanHashOffset.py`,
`/tmp/kilo/ghidra_scripts/FmanHashOffset2.py` (not yet copied into
`decomp/ghidra/scripts/` — ad hoc probes, promote if they prove reusable).

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
