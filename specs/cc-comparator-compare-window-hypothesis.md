# CC Comparator Compare-Window Hypothesis — What Bytes Does the Coarse Classifier Actually Compare?

**Status:** Open question, narrowed to a testable hypothesis with a validated methodology (not a guess). 2026-08-01.
**Branch:** dpaa1 (cross-referencing the ask20 branch's resolution of the analogous question)
**One-sentence summary:** We know our match-table *write* is byte-perfect against our own model (F-158). We do not know whether our model of what the CC engine's comparator *reads* is correct — and a sibling branch already proved, on different silicon config, that the obvious model (a hand-derived "canonical" field composite) is wrong by construction.

---

## 1. The gap in NXP's own documentation

Two things are not specified in any public NXP reference (DPAA Reference Manual, 210.10.1 microcode manual, mainline `fman_keygen.c`, or the LSDK SDK source):

1. **`contextOffsetInWS`** — how Internal Context (IC) byte offsets map onto FE-VM workspace offsets is controlled entirely by the FMan microcode and is not documented. The SDK just passes `0` and it happens to work for the EHASH path.
2. **What bytes the CC (Coarse Classifier) engine's match comparator actually reads for a given KeyGen scheme** — as opposed to what bytes land in the EHASH/EXT_HASH FE's workspace (IC offset `0x48`) for the *same* scheme. These are two different hardware consumption points, and nothing in NXP's documentation states whether they see identical bytes.

Gap 2 is the one that has killed every FE-VM-ehash HIT attempt on this project, on two separate branches.

## 2. What we've actually verified, precisely

| Claim | Verified how | Scope |
|---|---|---|
| EKFC `0x001C0006` (dpaa1) extracts `SIP(4)\|DIP(4)\|PROTO(1)\|SPORT(2)\|DPORT(2)`, MSB-first, 13 bytes | Hardware CRC-64 hash match: `crc64_raw(key)` computed in software over this exact byte sequence matched the KG's own hash output, for two independent live TCP flows (2026-07-13) | **The EHASH/EXT_HASH FE path only** — this is the byte sequence the KG writes to IC offset `0x48`, which the EHASH comparator reads from the FE workspace after `ALLOCATE`. |
| CC match-table row format is `key(16B)+mask(16B)`, 32-byte stride, `(numKeys+1)` rows | In-tree `cc_pack_key()` source (dpaa1, `kernel/common/patches/board/0098-fman-pcd-cc-static-install.patch`) + board dump via `fe_scaffold` (F-158, 2026-08-01) showing the live table matches this model exactly, byte-for-byte | **Row structure only** — this says nothing about what content should be inside the 16-byte key field, only how the CC engine expects rows to be laid out in MURAM. |
| Writing `SIP\|DIP\|PROTO\|SPORT\|DPORT` (the EHASH-validated order) into that key field, with `0xff` mask on 13 bytes, `0x00` on 3 pad bytes | Board-confirmed byte-perfect via `fe_scaffold` dump (F-158) | Confirms **our write matches our model**. Says nothing about whether **our model matches silicon reality** at the CC comparator specifically. |
| A matching frame dispatches through the CC engine to the FE-VM | **Never observed.** F-157's dedicated-TX-FQ discriminator (2026-08-01) — the first test capable of telling HIT from MISS apart at all — showed a genuine, unambiguous MISS on the byte-perfect build. | — |

**The gap: nothing above ever put a probe on what the CC comparator itself reads.** Every fact about "SIP|DIP|PROTO|SPORT|DPORT, MSB-first" comes from the EHASH path. We wrote that same byte sequence into the CC match table on the assumption that both hardware consumers see identical bytes for the same KG scheme — an assumption that has never been tested, and that a sibling branch already found to be false in an analogous situation.

## 3. The hypothesis, stated precisely

> The CC CONT_LOOKUP comparator's 16-byte compare window, for KeyGen scheme `EKFC=0x001C0006`, is populated with a different byte sequence (order, offset, or field set) than what lands at IC offset `0x48` for the EHASH/EXT_HASH FE path — and our match-table content, though byte-perfect against the *wrong* model, has therefore never had a chance to match a real frame.

This hypothesis does **not** claim the FE-VM ehash dispatch mechanism is broken, unreachable, or requires a kernel change beyond the match-table content. It claims a specific, narrow, testable thing: **we don't know what the CC engine actually compares against, and we've been guessing by analogy instead of observing it.**

## 4. Why this is a hypothesis with a methodology, not a guess — the ask20/patch-0108 precedent

The ask20 branch hit the identical class of problem on 2026-06-10 (patch `0108-fman-pcd-cc-per-key-fq-enqueue-ad.patch`, findings PR14z14 / PR14z22):

- Their *original* `cc_pack_key()` constructed a hand-derived "canonical" composite — `[ETYPE(2)|PROTO(1)|FLAGS(1)|SRCIP(4)|DSTIP(4)|SPORT(2)|DPORT(2)]` — that looked like a reasonable 5-tuple-plus encoding but was never actually what their KG scheme (`EKFC=0x00180206`) emitted in hardware.
- Root cause, stated in their own patch notes: **"the walker does NOT re-extract; it compares KG-emitted bytes."** The CC engine has no concept of a software-constructed canonical composite — it compares whatever raw bytes its own KeyGen extraction physically produces for that compare window, nothing more.
- Fix: rewrite `cc_pack_key()` to emit the actual KG output for their scheme — `[SIP(4)|DIP(4)|SPI(4)=0|SPORT(2)|DPORT(2)]`, 16 bytes — determined by direct silicon observation, not by assumption.
- Validation: **24M frames matched** in the PR14z22 DROP-miss diagnostic.

**This does not transfer literally.** ask20's `EKFC=0x00180206` is a different KeyGen scheme configuration than dpaa1's `EKFC=0x001C0006` (different field set — theirs includes a zero-filled SPI slot and excludes PROTO; ours includes PROTO and excludes SPI). The specific byte layout `[SIP|DIP|SPI=0|SPORT|DPORT]` is meaningless for our scheme.

**What transfers is the method:** don't assume any fixed layout — not the old "canonical" one, and not the EHASH-validated one either, however tempting the analogy — **observe what the KG scheme actually emits toward the CC comparator, for this specific EKFC configuration, on this specific silicon.**

## 5. The experiment

Not yet run. Estimated cost: one board session, using tooling that already exists.

1. Extend `fe_scaffold` (F-158, `bin/kernel-fixups/F_158.py`) — or complete the 2026-07-12 "annotation-hash-match" technique, proposed but never finished — to capture the raw bytes arriving at the CC engine's compare input for a live frame under `EKFC=0x001C0006`, independent of and prior to any FE-VM entry.
2. Diff against what's currently written to the match table (known from F-158: `0a 63 01 6a 0a 63 01 b9 06 14 51 d9 03 00 00 00`, mask `ff×13 00×3`).
3. **If they match:** the layout hypothesis is refuted. The CC engine sees the right bytes and still doesn't dispatch — the defect is elsewhere (group/AD table addressing, dispatch-stage logic, or something not yet modeled), and warrants a fresh, narrower investigation independent of this document.
4. **If they don't match:** write the observed layout to the match table and re-run the F-157 dedicated-TX-FQ discriminator. A genuine HIT would confirm the hypothesis and be new information — a 13-byte, non-vendor EKFC scheme successfully dispatching through FE-VM ehash, which nothing on this project has ever achieved.

**Independent, complementary evidence source:** `.106` runs a genuine, working NXP vendor ASK 1.x stack on the same silicon — a system where the CC/hash-table dispatch mechanism is known to function. `plans/NXP-106-ORACLE-VALIDATION-PLAN.md` is a read-mostly test plan to directly observe, on that working system, whether its compare-window content is raw KG-emitted bytes or something else.

## 5a. Live `.106` evidence (2026-08-01) — the field order matches; the byte *count* doesn't

Phase 0 of the `.106` oracle plan has been executed. The live `/etc/cdx_pcd.xml` on `.106` defines the working TCP4 hash-table classification (`cdx_tcp4_cc`, `hashtable external="yes" mask="0x7fff" hashshift="0" keysize="14"`) via distribution `cdx_tcp4_dist`:

```xml
<key>
  <fieldref name="ipv4.src" header_index="last"/>
  <fieldref name="ipv4.dst" header_index="last"/>
  <fieldref name="ipv4.nextp" header_index="last"/>
  <fieldref name="tcp.sport"/>
  <fieldref name="tcp.dport"/>
</key>
<combine portid="true" offset="16" mask="0xF"/>
```

That's `SIP(4)|DIP(4)|PROTO(1)|SPORT(2)|DPORT(2)` = 13 bytes — **the identical field set, in the identical order**, to dpaa1's `EKFC=0x001C0006`. This is a real data point against the "field order is silently different at the CC comparator" hypothesis as originally framed: on a *working* system, the declared/extraction order for this exact field set is exactly what dpaa1 has been writing to the match table. It does not prove the CC comparator sees this order — that still requires Phase 2/3 of the oracle plan (a live MURAM/hash-table dump) — but it removes "our order is probably just wrong" as the most likely explanation.

**What it surfaces instead:** `keysize="14"`, not 13. The 14th byte comes from `<combine portid="true" offset="16" mask="0xF"/>` — NXP appends a 4-bit physical-port-ID discriminator, **because this hash table is `shared="true"` across multiple physical RX ports**. Confirmed by checking dpaa1's own code (`bin/kernel-fixups/F_090.py`/`F_092.py`, `pcd->fe_vm_chain_built`): the FE-VM chain — including the CC group/match/AD table this whole investigation centers on — is built **once per `pcd`, not once per port**, and reused across every port that engages. Structurally, dpaa1 has the identical sharing pattern NXP's production config has, but without NXP's port-ID disambiguation byte.

**This does not explain F-157/F-158's decisive negative** — that test armed a single port (`0x10`/eth3) with a single flow, so no second port's traffic existed to collide with it. It is a real, separate finding: a genuine multi-port correctness gap that would need fixing before any future multi-port ehash test, independent of whatever the compare-window-content answer turns out to be.

## 6. What this does *not* change, regardless of outcome

The production scale-out decision (`plans/ASK2-MASTER-PLAN.md` §3 decision 14: CC-tree, multi-node, not FE-VM ehash, is the binding >32-flow scale mechanism) is **independent of this experiment's result**:

- FE-VM ehash is DDR-per-frame-latency-bound to ~1.5 Gbps even with a working HIT (measured 2026-07-19) — an architectural ceiling, not a bug.
- It is not the Linux flow-offload model (`TC Flower`/`nf_flowtable` offload is a TCAM-classifier-table abstraction — the CC-tree — not a per-frame hash lookup).
- ~~It is not NXP's vendor architecture (`cdx.ko` uses a hardware opcode/manip chain, not per-frame DDR hash).~~ **❌ REFUTED, 2026-08-05 (F-163, closing the 2026-08-01 flag below).** The 2026-08-01 flag asked for direct confirmation that the vendor's *software* connection-tracking path — not just the `fm_ehash.h` structural layout — actually drives production traffic through DDR ehash, before calling the "not vendor architecture" claim settled either way. That confirmation now exists: `cmm`'s connection-tracker (the genuine deployed userspace daemon on `.106`, source at `kernel/flavors/ask/userspace/cmm/`) accelerates every flow via `insert_entry_in_classif_table()` (`cdx_ehash.c`) → `fill_key_info()` → `ExternalHashTableAddKey()` — i.e. `cmm` itself, not just the FMan structural layout, targets external-hash for real TCP/UDP/ESP acceleration. This directly falsifies "cdx.ko uses a hardware opcode/manip chain, **not** per-frame DDR hash" — it uses both, together (the opcode/manip chain executes *from within* each DDR ehash entry, exactly as the 2026-08-01 flag's point (1) already suspected). Full finding, including the resulting key-format fix: `specs/fman-keygen-flow-key-spec.md` §1.2a/§4.3a, `arch/fman-fe-ehash.md` (un-retirement banner), `arch/fman-microcode-210-programming-reference.md` §10.5a.
- **⚠ What is still genuinely open (not resolved by the above):** the ~1.5 Gbps DDR-per-frame throughput ceiling claimed on the line below. Today's finding is architectural/mechanistic ("does the vendor use ehash at all" — yes), not a throughput measurement. Whether vendor ehash sustains line-rate in production (pipelined/overlapped DDR access across concurrent flows, dedicated hardware prefetch, etc.) versus this project's own single-flow ~1.5 Gbps measurement remains unmeasured. Do not treat the throughput-ceiling bullet below as also refuted by this entry — it needs its own, separate live measurement.

A confirmed HIT from this experiment would close out a genuine intellectual gap (and might inform IPv6 dual-scheme EHASH work, T-M6-1) but would not, by itself, reopen FE-VM ehash as a production scale path — the throughput-ceiling and Linux-model arguments above would still need to hold up under Phase 3 scrutiny. A refuted hypothesis narrows the remaining unknown for anyone who picks this up again later.

## References

- `plans/ASK2-MASTER-PLAN.md` §1.3a — full M3/M5 false-positive timeline, F-156/F-157/F-158 board evidence, architectural assessment
- `specs/fman-keygen-flow-key-spec.md` §6.1.3, §3.4 — CC match-table row format, EHASH extraction-order settlement and its scope caveat
- `arch/fman-microcode-210-programming-reference.md` §7.2 (EXT_HASH FE), §10 (DDR ehash flow store) — byte-level reference for the EHASH path
- ask20 branch, patch `kernel/common/patches/board/0108-fman-pcd-cc-per-key-fq-enqueue-ad.patch` (2026-06-10) — the precedent this document generalizes from
