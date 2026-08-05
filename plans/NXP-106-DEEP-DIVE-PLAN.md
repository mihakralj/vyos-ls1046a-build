# NXP `.106` Deep-Dive Plan — Resolving the CC-Tree Architecture Question

**Status:** Drafted 2026-08-05, not yet executed. Continues `plans/NXP-106-ORACLE-VALIDATION-PLAN.md` (executed 2026-08-01, all phases run, Phase 3 blocked on topology — see that doc for Phase 0–2 results this plan builds on).
**Board:** `.106` — genuine NXP vendor ASK 1.x stack (`cdx.ko`/`fci.ko`/`auto_bridge.ko`, `cmm`, `dpa_app`, `fmc`), version `2026.07.02-2130-rolling`, kernel `6.12.49-vyos`. Contrast with `.185`, which runs this project's from-scratch `dpaa1` branch (currently through F-162).
**Why now:** the 2026-08-05 session ran a decisive stress test — `.106`'s real vendor stack survived 400+ frames of classified TCP/ICMP traffic with 0% loss, while `.185`'s `cc_test`-driven bare `CONT_LOOKUP` mechanism freezes within 17–30 frames, across five independently vendor-verified register fixes (F-159 through F-162). This settles *that* `cc_test`'s architecture is the problem, not any individual register. It does not yet settle *what to build instead* at the byte level — that's this plan's job.

**Ground rule (unchanged from the prior plan):** `.106` is a reference oracle, not a test mule. Prefer read-only observation. **Never manually invoke `fmc`/`dpa_app`** — the 2026-08-05 session confirmed this can take the board down (see qdrant entry "`.106` operational notes"). If the vendor stack isn't running, the fix is getting `.106` to boot the correct image cleanly (see that entry for the automatic boot sequence to verify via `journalctl -b`), never replaying init by hand. Every phase below is read-only or additive-only on `.106`; nothing here reconfigures `cdx.ko` or touches the running PCD config.

**Known operational hazard, carried forward:** `.106` is a live/network-boot system with no on-device image selector. Which image it boots depends on network/TFTP boot config that this project's own CI publish step may inadvertently affect. **Always verify `.106`'s actual booted version (`cat /usr/share/vyos/version.json`) before trusting any prior session's assumption about what's running.**

---

## Phase A — Full, precise `t_ExtHashFe` decode (read-only, zero risk)

Purpose: Phase 2e of the prior plan got exactly one word (`w2=0x04020808`) from `FMBM_RCCB`'s target on real hardware and could not decide between "needs a transform" and "different AD species entirely." We now have a strong candidate structure (`t_ExtHashFe`, `fm_cc.h`, nxp-sdk branch: `misc(4B) | hashMask(2B)+contextSize(1B)+hashShift(1B) | liodnTableAndTablePtrHi(4B) | tablePtrLow(4B) | missResultPtr(4B) | nextFEPtr(4B) | missNextFEPtr(4B)` — 28 bytes, 7 words) and should decode the full structure, not one word.

- **A1.** Re-run `bin/muram-mmap-dump.py` (already proven safe and working on `.106`, Phase 2a) for a fresh full-MURAM capture on the currently-booted vendor image.
- **A2.** Re-run `bin/kg-scheme-read.py` to reconfirm the 5 `FMBM_RCCB` target offsets (expect the same or similar sequential pattern to Phase 2e's `0x48a00`/`0x48b00`/.../`0x48e00`, but re-derive rather than assume — port/scheme bindings could differ run to run).
- **A3.** Decode all 7 words (28 bytes) at one confirmed `FMBM_RCCB` target offset against `t_ExtHashFe`'s field layout. Check specifically: does `misc`'s top byte match `FM_PCD_AD_CONT_LOOKUP_TYPE|FM_PCD_AD_FE_ENTER_ALLOCATE` (per `FillAdOfTypeContLookup()`'s `externalHash=TRUE` branch)? Does `hashMask`/`contextSize`/`hashShift` look plausible (compare against `cdx_pcd.xml`'s per-distribution `mask`/`keysize`/`hashshift` attributes for whichever policy/port this scheme serves)? Does `tablePtrLow`(+`liodnTableAndTablePtrHi`) point somewhere sane within the 384 KiB MURAM capture?
- **A4.** If `t_ExtHashFe` doesn't fit cleanly, the fallback hypothesis is `t_FEOfTypeHash` (also in `fm_cc.h`: `general|maskOffset|addrHigh|addrLow|missResultPointer|nextFEPointer|missNextFEPointer` — same word count, different field semantics) — try both before concluding neither fits.
- **A5.** Cross-check the decoded hash table pointer/mask against a **second** read: walk to the DDR/external hash-bucket table itself (if `tablePtrLow` resolves to a sane physical address) and see if its structure matches this project's own `en_ehash_entry`/bucket model (`arch/fman-microcode-210-programming-reference.md` §10.2/§10.2a).

**Deliverable:** a byte-exact, confirmed decode of what `FMBM_RCCB` actually points to on working vendor hardware — settling the `t_ExtHashFe`-vs-`t_FEOfTypeHash`-vs-something-else question the prior plan left open.

## Phase B — Genuine transit/forwarded-traffic HIT capture (mutating: real traffic, still read-only on `.106`'s config)

Purpose: the prior plan's Phase 3 found `cmm`'s flow-offload counters only engage for genuinely **forwarded** traffic (`.106` routing packets through itself to a third party), not locally-terminated traffic — and concluded this was blocked because both `.106` and `.185` are directly dual-homed on both test subnets (no natural third-party destination requiring a forwarding hop). This blocker needs a deliberate topology fix, not another workaround attempt against the existing 2-node setup.

- **B1.** Decide the topology fix. Two options, pick one before starting:
  - **B1a (preferred if available):** a genuine third host/VM on one of the `10.99.1.x`/`10.99.2.x` segments, so `.185` (or another sender) can address traffic through `.106` to a destination `.106` must actually route toward.
  - **B1b (fallback, no new hardware):** a network namespace or veth pair on `.185` acting as a synthetic third-party endpoint on a *new*, third subnet reachable only via `.106` — forcing `.106` to genuinely forward, not just locally deliver. Lower confidence this fully replicates `cmm`'s real-world forwarding path (worth flagging explicitly in write-up if used), but doesn't require new physical hardware.
- **B2.** With the topology fix in place, generate a real forwarded TCP or UDP flow through `.106` matching one of `cdx_pcd.xml`'s active distributions (e.g., plain TCP, matching `cdx_tcp4_dist`).
- **B3.** Confirm `cmm`'s flow-offload actually engaged: `cmm -c 'show stat connection query'` shows a non-zero active connection, and/or `/proc/fqid_stats/pcd/{eth3,eth4}/*` counters move for the specific classification FQID.
- **B4.** With a confirmed real HIT, capture the actual hardware-matched flow key bytes — either via the MURAM dump (bucket entry content) or, if the `CDX_CTRL_DPA_GET_MURAM_DATA` ioctl turns out to be compiled into *this* boot's `cdx.ko` (check `journalctl -k` for "unsupported ioctl cmd" before relying on it — the prior plan found it missing on the image tested then, but a different-dated image might differ), the more direct kernel-side path.
- **B5.** Decode the captured key against dpaa1's own EKFC assumptions (`0x001C0006`/F-159, `0x00180006`/F-161, and the still-open Aug-1 hypothesis of the 0098 canonical `[ETYPE|PROTO|FLAGS|SRCIP|DSTIP|SPORT|DPORT]` layout) — this is the decisive test for the long-standing, still-unresolved EKFC/match-window layout question in `specs/cc-comparator-compare-window-hypothesis.md`.

**Deliverable:** a real, hardware-confirmed HIT's raw key bytes — something this project has never had on any board, vendor or `.185`, for the CC-comparator's actual compare window.

## Phase C — Validate Fork-B against the vendor decode

Purpose: once Phase A/B produce a confirmed byte-level model of real AC_CC/external-hash dispatch, check it against what this project's own Fork-B (`F-090` through `F-158`'s FE-VM chain, `fman_pcd_fe_engage()`/`fman_pcd_fe_build()`/`fman_pcd_ehash_table_set()`) actually builds — not by re-deriving from SDK docs again, but by direct comparison against Phase A/B's ground truth.

- **C1.** Compare the vendor's `t_ExtHashFe` (or whichever structure Phase A confirms) word-for-word against `fman_pcd_fe_build()`'s FE_ENTER AD construction (patch 0124) and `fman_pcd_ehash_table_set()`'s hash-table setup.
- **C2.** Specifically re-examine the still-open Aug-1 finding ("CC match-key row byte LAYOUT vs KG extraction layout mismatch... official 0098 cc_pack_key layout is `[ETYPE|PROTO|FLAGS|SRCIP|DSTIP|SPORT|DPORT]`... our hybrid uses EKFC `[SIP|DIP|PROTO|SPORT|DPORT]`") against Phase B's real captured key, resolving it definitively instead of by inference.
- **C3.** List every concrete gap found, each as a candidate fixup (F-16x style) against Fork-B specifically — not against `cc_test`, which per the 2026-08-05 session's conclusion should be retired rather than further patched.

**Deliverable:** a concrete, evidence-backed punch list of what Fork-B needs to reach a genuine, confirmed HIT — replacing today's still-open "leading hypothesis" status with settled fact.

## Phase D — Write-up

- **D1.** Fold Phase A's `t_ExtHashFe`/`t_FEOfTypeHash` decode into `arch/fman-microcode-210-programming-reference.md`.
- **D2.** Fold Phase B's captured key bytes into `specs/cc-comparator-compare-window-hypothesis.md`, resolving the EKFC-order question there.
- **D3.** Fold Phase C's gap list into `plans/CC-TREE-REBUILD-PLAN.md` as the next concrete implementation phase (post-`cc_test`-retirement).
- **D4.** Store a qdrant entry summarizing the resolved questions, cross-referencing the 2026-08-05 entries this plan was drafted from.

---

## What this plan deliberately does not attempt

- No re-flash, no `cdx.ko` reconfiguration, no manual `fmc`/`dpa_app` invocation, no kernel rebuild for `.106` — same ground rule as the prior plan, reinforced by this session's board-outage lesson.
- Does not itself implement the Fork-B fixes Phase C identifies — that's separate follow-on work once this plan's findings land.
- Does not attempt to make `.106`'s `cdx_pcd.xml` EKFC match `.185`'s exactly — Phase B/C use `.106` to validate the *mechanism and real byte layout*, not to transplant vendor's exact config onto `dpaa1`.
