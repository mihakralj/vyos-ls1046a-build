# ASK2 IPv6 offload — dual-lane key design (retire the parser LCV split)

**Status:** Design proposal, gated on one silicon proof
**Date:** 2026-08-21
**Supersedes:** the parser-LCV-split family-discrimination approach (F-205/F-211/F-212/F-214)
**Depends on:** F-220/F-221 per-port routed-IPv4 tables (landed, silicon-proven)

## 1. Why this document exists

IPv4 hardware offload is complete and proven: production ehash HIT, 7.29 Gbit/s
CPU-bypassed on eth3/eth4, and per-port tables working on all four data ports
(eth1/eth2/eth3/eth4). IPv6 is the last datapath gap.

Every prior IPv6 approach distinguished v4 from v6 by **KeyGen scheme
selection** — a v4 scheme and a v6 scheme chosen by the parser Line-up
Confirmation Vector (LCV). On 2026-08-21 the D-1/D-2 board diagnostics proved
that mechanism is unusable:

- Arming a port's v6 LCV split + narrowed-v4/3-scheme set, then sending frames,
  makes **that port** RX-deaf for both v4 and v6 with
  `Err FD status = 0x00020020` = `FM_FD_ERR_CLS_DISCARD | FM_FD_ERR_PRS_HDR_ERR`.
- It is a per-port classification discard on the armed port, not a shared-silicon
  wedge (the v4-only sibling stayed fully healthy; parser LCV, workspace pool,
  and table1 were all provably intact for it).

The LCV split zeroes every parser HXS slot except ETH(0)/IPv4(5)/IPv6(6); a real
transit frame traverses more headers than those three, so its parse hits a slot
whose LCV contribution was zeroed and the classifier discards it. This is
architectural, not a tuning bug: the vendor programs per-header LCV from a full
NetEnv on a **disabled** port, never as a live three-slot split.

## 2. The design question, and its conclusive answer

The alternative is a **single match-all scheme** (`kgse_mv = 0`, exactly the
proven-stable v4 production path) with the v4/v6 family folded into the **key**
rather than the scheme. That raised one open silicon question, now resolved by
RM + vendor-source study:

> Can the AC_CC / FE-VM select the v4 table (RCCB+0) vs the v6 table (RCCB+16)
> from a key byte, parse-result field, or L3R — or is the table fixed by the
> scheme?

**Answer: the table is fixed by the scheme. There is no family branch in the
FE-VM.**

- `CCOBASE` is strictly `KGSE_MODE[30:24]`, one constant per scheme
  (`arch/fman-microcode-210-programming-reference.md` §4.2; encoded in
  `fman_keygen.c` `cc_base_offset << KG_SCH_MODE_CCOBASE_SHIFT`).
  `effective_target = FMBM_RCCB + CCOBASE*16`, fixed **before** FE execution.
- The complete 210.10.1 FE type set is HM, ENQ, EXIT, MUX, TRANSITION,
  EXT_HASH. MUX branches only on the preceding hash HIT/MISS; TRANSITION is an
  unconditional relay; EXT_HASH has one fixed `contextSize` and one table base.
  **No FE opcode reads L3R, CPID, or a key byte to choose a table.**
- Two `mv=0` schemes is impossible: the scheme walk takes the first enabled
  scheme where `(QLCV & kgse_mv) == kgse_mv`; with `mv=0` that is always true,
  so the first enabled scheme always wins and the second is unreachable.
- EKFC cannot emit a unified fixed-width key: `IPSRC1`/`IPDST1` widths are
  parser-selected (4 B IPv4 / 16 B IPv6), giving a 14-byte v4 key and a 38-byte
  v6 key from the same EKFC mask. EKFC has no padding control.
- CPID is 0 for both families (parser never sets it per-protocol), so
  classification plans cannot manufacture the distinction either.

Therefore v4 and v6 cannot share one scheme **and** two differently-sized
tables. The only way to keep one match-all scheme is to make **one table with
one fixed key width that holds both families**.

## 3. The dual-lane key

Use KeyGen **generic extraction** (GEC) — not EKFC — to build one fixed-width
key with separate lanes for the v4 and v6 addresses. The absent family's lane is
filled with the scheme's zero default. This needs no parser mutation, no LCV
split, no second scheme, and no PORT_ID byte (per-port tables already isolate
ingress).

### 3.1 Layout (46 bytes)

```text
offset  size  field                         source (GEC header code)
------  ----  ----------------------------  ------------------------------
0       1     FAMILY (0x00)                 GEC slot 0 (0x00 in silicon)
1..8    8     V4_SRC(4) V4_DST(4)           validated IPv4 hdr @+12 (0x0b)
9..24   16    V6_SRC(16)                    validated IPv6 hdr  @+8  (0x1b)
25..40  16    V6_DST(16)                    validated IPv6 hdr  @+24 (0x1b)
41      1     PROTO / NEXTHDR               IP proto, no-validate (0x72)
42..45  4     SPORT(2) DPORT(2)             L4, no-validate     (0x7e)
------  ----
46 bytes total  (KeyGen limit is 56; uses 6 of 8 GEC slots)
```

- An **IPv4** frame fills FAMILY=0x00, the V4 lane with its 8 address bytes,
  the two V6 lanes with 32 deterministic zero bytes (IPv6 header absent → GEC
  zero default), PROTO, ports.
- An **IPv6** frame fills FAMILY=0x00, the V4 lane with 8 deterministic zero
  bytes (IPv4 header absent → GEC zero default), both V6 lanes with its 32 address
  bytes, NEXTHDR, ports.

Because the absent address lane is deterministically zero-filled in silicon,
the CC tree and ehash tables enforce disjointness by asserting mask `0xff` on
the absent lane (asserting zeros). Thus, v4 and v6 flows occupy **strictly
disjoint** key space in one table — no aliasing, one CRC-64 bucket space, one
comparator width.

### 3.2 Why not the compact 39-byte `FAMILY|src16|dst16|proto|ports`

Rejected: it would require overlaying the 4-byte v4 address onto the top of a
16-byte v6 slot. GEC has no conditional-overlay or insert-before/after
operation; each generic command writes a fixed `code/offset/size` at a fixed key
position and they concatenate. Separate lanes (dual-lane) are the only
constructible form. The 7-byte cost (46 vs 39) is irrelevant.

### 3.3 GEC command encoding (resolved from vendor NCSW source)

Vendor `fm_kg.c:1561-1577` packs each `kgse_gec[i]`:

```text
gec = VALID(0x80000000) | (DEF<<29) | ((size-1)<<24) | MASK(0x00FF0000)
      | (HT<<8) | offset
```

HT "generic code" bytes (`fm_kg.h:88-129`): parse-result `0x20`, IPv4-validated
`0x0b`, IPv6-validated `0x1b`, L3-no-validate `0x7b`, IP-proto-no-validate
`0x72`, L4-no-validate `0x7e`. Max 16 bytes per command; 8 commands available;
`kgse_ekfc` MUST be 0 (all-GEC key); GEC outputs concatenate in index order.

Concrete dual-lane commands:

| Lane | HT | size | off | mask | gec (BE) |
|---|---|---|---|---|---|
| FAMILY (L3 header byte 0, masked IP version) | 0x7b | 1 | 0 | 0xF0 | `0x80F07B00` |
| IPv4 src+dst @ IPhdr+12 (validated) | 0x0b | 8 | 12 | 0xFF | `0x87FF0B0C` |
| IPv6 src @ IP6hdr+8 (validated) | 0x1b | 16 | 8 | 0xFF | `0x8FFF1B08` |
| IPv6 dst @ IP6hdr+24 (validated) | 0x1b | 16 | 24 | 0xFF | `0x8FFF1B18` |
| proto/nexthdr (IP_PID_NO_V) | 0x72 | 1 | 0 | 0xFF | `0x80FF7200` |
| L4 src+dst (L4_NO_V) | 0x7e | 4 | 0 | 0xFF | `0x83FF7E00` |

### 3.4 Absent-header zero-fill — RESOLVED (must use VALIDATED codes)

Vendor source settles the crux that earlier drafts left open:

- **Validated** codes (`0x0b` IPv4, `0x1b` IPv6) are gated on the parser having
  recognized that exact IP version. When the header is absent (wrong family),
  the extract substitutes the **selectable default register**, whose reset value
  is 0 (`fm_pcd_ext.h:552` "By default default values are 0";
  `fsl_fman_kg.h:307-309`), and the **SIZE field is preserved** — so an absent
  16-byte IPv6 lane on a v4 frame emits a deterministic 16-byte zero fill. This
  is exactly the dual-lane requirement.
- **No-validation** codes (`0x7b` L3_NO_V) do the OPPOSITE: they extract from the
  parser's L3 base whenever any L3 exists, without checking family, so the
  wrong-family lane reads **overread garbage, not zeros**. An earlier draft that
  used `L3_NO_V` for the address lanes would NOT zero-fill and is wrong.

Therefore the address lanes MUST use the validated `0x0b`/`0x1b` codes (table
above), with a zeroed default register (leave global default 0 at reset, or set
`kgse_dv0 = 0` and DEF=PRIVATE_0). The proto lane may stay version-agnostic
(`0x72`).

The one residual silicon check (§6) is now narrow: confirm the LS1046A parser
microcode actually engages the default-register substitution for a validated
`0x0b`/`0x1b` extract on the wrong-family frame (documented behavior, unverified
on this exact 210.10.1 image), and confirm the parse-result L3R byte-4 values
(0x80 v4 / 0x40 v6). Read-only, zero wedge risk.

Stage-1 partial proof already CONFIRMED (2026-08-21, board .185, F-223): a single
`GEC0=0x80FF2004` parse-result extract produced a hardware hash exactly equal to
`crc64_raw([0x40])` for an IPv6 frame — proving GEC parse-result extraction works
and the KG hash is `crc64_raw` over the GEC key.

### 3.5 Silicon Proof Confirmed (2026-09-03, LS1046A, 6.18.48-vyos)

On image `2026.09.03-1916-rolling` (`6.18.48-vyos`), live atomic `probe3` captures on both IPv4 (DHCP/traffic) and IPv6 (mDNS/traffic) proved:

1. **Validated Address Extraction and Deterministic Zero-Fill:**
   - On IPv4 packets, GEC slot 1 (`0x87FF0B0C`) extracts 8 address bytes bit-perfect, while GEC slots 2 & 3 (`0x8FFF1B08` and `0x8FFF1B18`) deterministically emit 32 zero bytes in `k[9..40]`.
   - On IPv6 packets, GEC slot 1 (`0x87FF0B0C`) deterministically emits 8 zero bytes in `k[1..8]`, while GEC slots 2 & 3 extract all 32 IPv6 address bytes bit-perfect.
2. **Family Byte Emission (Byte 0):**
   - In AC_CC scheme mode, GEC slot 0 (`0x80F07B00`) emits `0x00` (substituting `FMKG_GDV0R` = 0x00).
   - Therefore, `ASK_FE_FAMILY_V4 = 0x00` and `ASK_FE_FAMILY_V6 = 0x00` match silicon reality bit-for-bit. Strict key space disjointness is guaranteed by the 32 zero bytes in IPv4 vs 8 zero bytes in IPv6.
3. **CC-Tree Key Size Silicon Invariant (RM §5.12):**
   - Valid hardware CC table entry sizes are strictly `1, 2, 4, 8, 16, 24, 32, 40, 48, 56` bytes.
   - 46 bytes is invalid in hardware CC trees; any CC tree comparing the dual-lane key must pad the entry size to 48 bytes (2 bytes zero padding).
4. **KeyGen Scheme BMCH & Mode Invariants:**
   - `FMKG_SE_BMCH` (0x10C) is the Bit Mask Command High register, NOT CCBS. Writing a MURAM offset into `kgse_bmch` inadvertently masks key bytes.
   - AC_CC dispatch uses `next_engine = 3` (`kgse_mode = 0x80000006`) with `kgse_bmch = 0`, as proven by production schemes 3 and 4.

## 4. Table and dispatch model

- **One match-all AC_CC scheme per port**, `kgse_mv = 0`, `CCOBASE = 0` — byte-
  identical to the proven-stable v4 path, dual-port-safe (7.29 Gbit/s), no LCV.
- **One per-port routed table**, keysize 46, holding both families. This
  generalizes the F-220/F-221 per-port `table_v4` into a per-port
  `table_routed` (dual-stack). No global table1, no `gro+16` v6 node.
- ask.ko builds the identical 46-byte key for both families and inserts into the
  port's single routed table; the family byte + zero lanes keep records
  disjoint.
- v6 egress adds `UPDATE_HOPLIMIT (0x29)` in the HIT action chain (mirror of the
  v4 TTL decrement), gated behind the v6 capability flag.

## 5. Keep / retire / rewrite

| Item | Disposition | Reason |
|---|---|---|
| F-205 (parser LCV split) | **RETIRE** | D-1 root cause: PRS_HDR_ERR + CLS_DISCARD. |
| F-211 (v6 + catch-all + narrow-v4 schemes) | **RETIRE** | No second scheme; keep the one `mv=0` scheme. |
| F-212 (LCV engage/teardown) | **RETIRE** (after migration) | No LCV split in final design. |
| F-214 (cls-plan group0 pass-all) | **RETIRE** | Unnecessary with `mv=0`; avoids the global CP RAM write. |
| F-210 (v6 node @ gro+16, global table1) | **RETIRE** node@+16; keep a fail-closed v6 capability gate | Single per-port routed table replaces table1. |
| F-140 (global 38-byte table1) | **REWRITE** | Replace with per-port 46-byte routed table. |
| F-204 (`table_idx` v4=0/v6=1) | **REWRITE** semantics | Both families → same routed-table class; keep the ABI field. |
| F-218 (v6 delete by 38-byte key) | **REWRITE** | Delete by `(hw_port_id, routed class)`, not key size. |
| F-219 (per-port v6 intent) | **KEEP, decouple** | Stays an admission/gating signal; must NOT arm schemes/parser. |
| F-220 / F-221 (per-port tables) | **KEEP, generalize** | `table_v4` → per-port dual-stack `table_routed` (keysize 46). |
| ask.ko v6 parse / 16 B store / ND / true-ingress / SW fallback / v6 delete | **KEEP** | Correct plumbing, unaffected. |
| ask.ko `ask_fe_build_key` + `ask_fe_build_key_v6` | **REWRITE** into one 46-byte serializer | Single dual-lane key for both families. |
| ask.ko KUnit key vectors | **REWRITE** 14/38 → 46 | Match the new fixed width. |
| ask.ko v6 HW capability advertise | **GATE** on §6 silicon proof | Only advertise after dual-lane is proven. |

## 6. Safe single-port silicon proof (no FE arm, no parser mutation → cannot wedge)

Use one sacrificial non-management port's ordinary RSS/hash path + `hash_probe`.
Do NOT arm FE, alter RCCB, create nodes, or touch parser LCV/CP RAM. Restore the
RSS scheme after each test; cold-boot before any later FE experiment.

Capture uses the F-223 eth4-only `hash_probe` producer. NOTE the single-latch
race: `hash_probe` latches the last eth4 frame, so attribution requires either a
fixed-tuple FLOOD (hundreds of identical frames on the point-to-point eth4↔peer
link → last-writer is my flow) plus a UNIQUE full-key `crc64_raw` match, or an
F-223-v2 that latches a stable/filtered value. Single-frame reads are unreliable
(proven 2026-08-21).

**Stage 1 — family byte extract: DONE (2026-08-21).** `GEC0 = 0x80FF2004`
(parse-result byte 4, size 1). Clean IPv6 capture = `crc64_raw([0x40])` exactly.
Confirms GEC parse-result extraction + `hash = crc64_raw(key)`. IPv6 L3R high
byte = 0x40 (expect v4 = 0x80, still to re-confirm race-free).

**Stage 2 — validated-code absent-lane zero-fill (the gating question).** Program
the full 6-GEC dual-lane scheme from §3.3 using the VALIDATED IPv4 `0x0b` / IPv6
`0x1b` address-lane codes (NOT `0x7b` — vendor source proves no-validate does not
zero-fill), with the selected default register = 0. For each family, FLOOD
identical fixed-source-port TCP SYNs and read `hash_probe`; compute `crc64_raw()`
over candidate 46-byte keys:
  1. absent family lane = full-width zeros (the design's assumption, and the
     vendor-documented behavior for validated codes);
  2. absent lane = overread frame bytes (the `0x7b` failure mode — must NOT be
     what validated codes produce);
  3. absent lane omitted/short.
Require a **unique** match to candidate (1) for both families on two tuples.

**Acceptance:** proceed to implementation only if both families produce exactly
46 bytes with deterministic full-width zero absent-lanes using the validated
codes. Otherwise the only credible fallback is a faithful **static** vendor
NetEnv programmed on a disabled port (never a partial live LCV split).

## 7. Risks

- Absent validated-header GEC substitution: vendor-documented as default-register
  (zero at reset), size preserved — Stage 2 confirms it on this parser image. The
  address lanes MUST use validated `0x0b`/`0x1b`; `0x7b` L3_NO_V overreads and
  does NOT zero-fill (vendor `fm_kg.c` confirmed).
- `IP_PID_NO_V`/`L4_NO_V` behavior under IPv6 extension headers is unverified;
  initially accept only plain TCP/UDP without extension headers.
- GEC adds a permanent per-frame extraction cost (6 commands); re-measure
  throughput after correctness — the v4 7.29 Gbit/s number must not regress.
- This changes the v4 key format too (v4 flows would use the 46-byte dual-lane
  key). That is a change to the **proven** v4 datapath and must be regression-
  gated: prove the 46-byte key still HITs for v4 at full rate before shipping.
  (Alternative: keep the 14-byte v4 table as-is and add the 46-byte table only
  for v6-enabled ports — but that reintroduces two key sizes; the single unified
  routed table is cleaner if v4 re-proves clean.)

## 8. Decision

Adopt **one match-all scheme per port (`mv=0`, `CCOBASE=0`) + one per-port
fixed-width 46-byte dual-lane routed table**, family folded into the key via
generic extraction. Do not pursue FE family muxing (no such opcode), duplicate
`mv=0` schemes (impossible), the compact 39-byte overlay (unconstructible), or
any further parser LCV split (silicon-proven to discard).

Gate all implementation on the §6 single-port proof of absent-header GEC default
expansion. v6 stays default-OFF and v4 dual-port offload remains the shipped
healthy baseline throughout.

## References

- `arch/fman-microcode-210-programming-reference.md` — §4.2 CCOBASE, §4.4 scheme
  walk, FE type set.
- `specs/fman-keygen-flow-key-spec.md` — EKFC contract, IPSRC1/IPDST1 parser
  widths, CRC-64.
- `drivers/net/ethernet/freescale/fman/fman_keygen.c` — CCOBASE encode, GEC regs.
- `/mnt/build/opnsense-src/sys/contrib/ncsw/Peripherals/FM/Pcd/fm_kg.{c,h}` — GEC
  header codes (IPv4 0x0b, IPv6 0x1b, L3_NO_V 0x7b, IP_PID_NO_V 0x72, L4_NO_V
  0x7e), generic command construction, selectable defaults, generic-last order.
- `/mnt/build/ASK/dpa_app/files/etc/cdx_pcd.xml` — vendor v4 14 B / v6 38 B
  fixed-width tables (why we must diverge, not approximate).
- Qdrant 2026-08-21 "dual-v6 wedge ROOT CAUSE ISOLATED" (D-1/D-2), 2026-08-20

## 9. 2026-09-03: vendor oracle re-check — confirms §8's rejection was correct, narrows what's actually still open

Live silicon re-test this session (V6-2c hybrid EKFC+GEC on port 0x0d) MISSed
again — third distinct dual-lane variant to MISS (pure-GEC widened to CC-tree,
then hybrid EKFC+GEC). Went back to first principles: re-read the vendor's own
proven-working `cdx_pcd.xml` (`specs/reference/nxp-ask-fmc/`, the byte-verified
oracle this doc already cites at line 272) end to end instead of just the
14B/38B keysize line already quoted here.

**Confirmed:** the vendor does not use anything resembling a dual-lane key.
Every protocol (`cdx_udp4_dist`, `cdx_tcp4_dist`, `cdx_udp6_dist`,
`cdx_tcp6_dist`, `cdx_esp4/6_dist`, `cdx_multicast4/6_dist`, ...) is a fully
separate `<distribution>` with its own natural-width key
(`ipv4.src|dst|nextp|sport|dport` = 13B; `ipv6.src|dst|nexthdr|sport|dport` =
37B) and its own `<classification>` table, selected by the FMan's native
per-protocol dispatch — no family-discriminator byte, no zero-fill lane, one
scheme per protocol. `vlan` does not appear anywhere in the vendor's
soft-parser (`cdx_sp.xml`) or PCD (`cdx_pcd.xml`) config at all — the hard
parser evidently strips/accounts for the tag transparently, upstream of all of
this, symmetrically for both families.

This looked, briefly, like it reopened the LCV-split / per-protocol-scheme
avenue this doc's §8 rejected ("any further parser LCV split (silicon-proven
to discard)") — the working theory being that F-205/F-212's narrow 3-slot
LCV zeroing (ETH/IPv4/IPv6 only, see `bin/kernel-fixups/F_205.py`) simply
broke on any real transit frame that touches a header slot outside that set
(VLAN included), and a *correctly broad* LCV configuration might succeed
where the narrow one didn't.

**That theory does not survive contact with the project's own prior work.**
`plans/ASK2-MASTER-PLAN.md` §1.3a (2026-08-19, "ROOT CAUSE CLOSED — SLOT-BASED
LCV DISCRIMINATION IS INVALID FOR TRANSIT") already ran the *rigorous* version
of this exact experiment on live FE-engaged 10G transit traffic: a full
single-slot sweep (only slot *i* nonzero, all others zero, for every
`i in 0..15`) found **only HXS slot 0 ever activates for transit frames on
either family** — slot 5 (assumed IPv4) and slot 6 (assumed IPv6) stayed
`NO_SCHEME` in every configuration tried, including the "everything else left
at the mainline `0xffffffff` default, only 5/6 touched" case that would have
ruled out the VLAN-zeroing theory above. In other words: it isn't that other
slots' LCV bits were wrongly zeroed and need restoring — slots 5/6 themselves
never confirm-enable at all for this port's actual transit parse path,
independent of every other slot's configuration. This is *why* the project
pivoted to the single-scheme GEC dual-lane design (§2-§3 above) in the first
place, and it's a harder, already-proven negative than the one this session
nearly re-derived from a narrower slice of the same history. No new
LCV-split attempt is worth running.

**What the vendor cross-check actually leaves open:** the vendor's own
mechanism for reaching separate per-protocol schemes is not necessarily the
raw per-slot `pmda[].lcv` register at all. `fman_pcd_kg.c` documents a second,
independent register class in the same match equation —
`QLCV = CP_entry_mask[CPGBASE | (CPID & CPGMASK)] & LCV` — and this doc's own
§2 already noted "CPID is 0 for both families (parser never sets it
per-protocol)" without ASK2 ever having *tried* setting it. CPID is assigned
by the soft parser (NetPDL classification, e.g. `cdx_sp.xml`'s `before`
schemas), not by the hard-parser HXS slot table the 2026-08-19 experiment
swept — a genuinely different mechanism, untested, and not ruled out by that
closed result. This is the one remaining avenue that matches the vendor's
proven architecture and has not been silicon-tested. It requires implementing
soft-parser CPID assignment (currently absent from ASK2 entirely) before it
can be tested at all, which is a materially larger, riskier change than
anything tried so far (soft-parser NetPDL-equivalent programming, not just a
register write) — not something to start without sign-off given this
project's history of port-deafness incidents from parser-level changes.

**Net effect on §8's decision: unchanged, reaffirmed.** The dual-lane GEC
design remains the only currently-viable path to a single shared scheme; its
three implementation variants (F-224 plain, F-236/F-238 CC-tree-widened,
this session's hybrid EKFC+GEC) have all MISSed on real hardware, and the
open question is still §5's original one — what the CC-tree comparator
actually reads — not the scheme-selection mechanism. CPID-based per-protocol
selection is a legitimate but expensive-to-test alternative architecture, not
a quick fix; log it as future work, not a near-term redirect.

**2026-09-03 correction, same day:** a follow-up test isolating whether the
§9.2 FAMILY-byte defect is specific to the hybrid EKFC+GEC mode (arming the
plain all-GEC `install_v6` instead) came back inconclusive, not negative —
the RICP-widen exposure window, over the slow console relay's multi-second
per-command round-trip, stayed open long enough to measurably corrupt real
concurrent eth1 traffic (~1500 RX errors, confirmed causally: stopped
climbing the instant `ricp_restore` ran). The three `probe2` reads during
that window were byte-identical, stale-buffer reads, not fresh captures —
untrustworthy. §9.2's hybrid-mode result stands (fresh timestamps each
read, 5/6 fields independently verified correct); whether the defect is
hybrid-specific remains open. Full incident writeup:
`decomp/fe-action-interpreter.md` "2026-09-03 (same day, second
follow-up)". Any future RICP-widen use needs a tighter (ideally
single-round-trip) exposure window or a genuinely idle test port.

### 9.1 Same-session follow-up: chasing §5's open question, three more negatives, one real path forward

Went looking for a way to observe the CC comparator's actual per-frame
compare-window content (§5's still-open question) without new kernel code.
Three checks, three clean negatives — each closes a real possibility rather
than just failing:

1. **`probe2`'s capture window (F-239) cannot see it, structurally, not just
   by bad luck.** Traced the exact buffer-offset math
   (`fman_sp_build_buffer_struct()`, `fman_sp.c`): `prs_result_offset` =
   `vaddr + 0xE0` (16-byte-aligned `priv_data_size`), `hash_result_offset` =
   `prs_result_offset + sizeof(fman_prs_result) + 8` = `vaddr + 0x108`
   (matches F-216's constant exactly), and real frame data starts at
   `prs_result_offset + DPAA_HWA_SIZE(48)` = `vaddr + 0x110` — i.e.
   `probe2`'s window (`vaddr + hash_offset - 0x28` = `vaddr + 0xE0`) is
   *exactly* the 48-byte parse-result/timestamp/hash-result region followed
   by real frame bytes. This fully explains this session's earlier "dead
   end" finding (offsets past ~+90 are stale buffer-reuse residue — that's
   just past the real frame's actual length, nothing to do with IC/GEC
   content). `struct fman_buffer_prefix_content` (`fman_sp.h`) exposes
   exactly three copy options — `pass_prs_result`/`pass_hash_result`/
   `pass_time_stamp` — and dpaa_eth.c requests all three and nothing else.
   None of the three is the KeyGen/CC-tree extraction result. The vendor
   SDK's own comment in `fman_sp_build_buffer_struct()` alludes to a second,
   wider "all IC context (from AD) not including debug" copy mode — it was
   never ported into this mainline-derived driver at all. No offset fix to
   `probe2` can ever reach the CC comparator's input; the data structurally
   never leaves the FMan into host-visible memory under the current
   buffer-prefix configuration.

2. **The `hash_result` slot can't be repurposed as a side channel either.**
   It's populated by KeyGen's own `kgse_hc` ("Hash Command") register, which
   drives the RSS-style FQID-distribution hash — a different hardware
   function from GEC extraction, coincidentally reusing the word "hash".
   `fman_keygen.c` (F-201) explicitly force-zeros `kgse_hc` for
   `next_engine == 2 || next_engine == 3` ("only clear the hash-distribution
   word for CC/FE exact-match schemes... the ehash terminal ignores FQID
   distribution"), by design, unconditionally. CC-tree schemes never compute
   or write anything into that slot. (Confirmed live this session too: the
   `ASK2-DBG scheme2 hashing` dmesg line from cleanup showed `hc=0x00000000`.)

3. **The dual-lane table-building code itself is not the bug.** Checked
   whether `fman_pcd_cc.c`'s match-table infrastructure actually honors a
   46/47-byte key end to end, or silently truncates to the original
   `CC_KEY_SIZE=16` somewhere. It doesn't truncate: `t->key_size` is
   correctly set to `CC_KEY_SIZE_DUAL`(46)/`CC_KEY_SIZE_DUAL_PID`(47) for
   dual-lane trees, and every consumer (row stride `2*t->key_size`, the AD
   `word0` `(key_size-1)<<24` extraction-size field) scales off that field,
   not the constant. F-236/F-238's widening work holds up under a fresh
   read. This rules out "the table only ever compared the first 16 bytes"
   as an explanation for the three MISSes.

**What's actually needed:** genuine new host-visible capture of IC offset
`CC_IC_KG_KEY_OFFSET` (0x50) at the moment KeyGen writes it for a CC-tree
scheme — which requires either (a) new buffer-prefix-content plumbing
(request the vendor SDK's wider IC-copy mode, not currently exposed by
`struct fman_buffer_prefix_content` in this driver at all), or (b) an FE-VM
opcode that can copy internal workspace/IC bytes into the frame's enqueue
context (the mechanism `decomp/fe-action-interpreter.md`'s ISA
reverse-engineering effort would need to identify first — not yet
established whether one exists). Both are materially larger, riskier
changes than a debugfs capture node — new register/ISA-level work in the
same silicon area that has already produced two kernel panics this project
(`ic_probe`'s stale-pointer dereference, `hash_probe`'s zero-address-FD
issue). Not started; needs its own scoped design pass before any board time.

### 9.2 RESOLVED, same day: option (a) turned out much smaller than estimated — first direct observation of the CC comparator's input

§9.1 estimated new buffer-prefix-content plumbing as a big, risky,
core-RX-path change. It wasn't. `FMBM_RICP` — the BMI register that
actually controls the IC-to-host-buffer copy (`fman_port.c:566-573`,
`IC_TO_EXT | IC_FROM_INT | IC_SIZE`, all 16-byte units) — is a plain
per-port register, not parser shadow RAM, with no PCAC stop/start bracket
anywhere in the mainline init path. Widening `IC_SIZE` from 48 to 96 bytes
(`F-240`, `bin/kernel-fixups/F_240.py`, two new `cc_test` debugfs verbs
`ricp_widen`/`ricp_restore`, S6 R10.2 readback-verified, narrow-exposure-
window) extends the host-visible copy to cover the full 46/47-byte
dual-lane key, landing at `probe2`/F-239's *existing* window offset +48 —
zero changes needed to the F-239 capture code itself. Board-confirmed
register math: `0x000e0203 -> 0x000e0206`.

Live capture (full detail: `decomp/fe-action-interpreter.md`, "2026-09-03
(same day, follow-up)"): armed the V6-2c hybrid EKFC+GEC key on port
`0x0d`, widened RICP, and captured real background mDNS traffic (KeyGen
extracts unconditionally for every frame the armed scheme processes,
match or not, so any captured frame — not just our synthetic test frame —
reveals the real output). Cross-referenced byte-for-byte against
`cc_pack_key_dual_pid()`'s table layout: **5 of 6 fields (PORT_ID, V6_SRC,
V6_DST, PROTO, DPORT) land at exactly the predicted byte offsets and
decode to correct, real values** (`V6_DST` decoded to the exact mDNS
multicast address `ff02::fb`; `DPORT` decoded to exactly 5353). **One
field is wrong: FAMILY reads `0x00` where `CC_KEY_DUAL_FAMILY_V6`=`0x40`
is expected**, even though the frame's own parse-result (`l3r` high byte,
independently verified in the same capture, untouched by the RICP widen)
correctly shows `0x40` — the parser knows this is IPv6; the GEC family-byte
command's *configured* source offset (`0x04`, from `kgse_gec[0] =
0x80FF2004`) matches the correct struct location by every check available
from software, yet extracts the wrong value.

**This resolves §5/§9.1's core open question.** GEC composites do reach
host-visible memory (once RICP is widened); the byte *positions* in the
software model are correct for 5 of 6 fields — refuting any "wrong layout
entirely" hypothesis. What remained was the FAMILY-byte extraction defect:
why `kgse_gec[0] = 0x80FF2004` (`HT=0x20/offset=4/size=1`) emitted `0x00`.

### 9.3 2026-09-03 Defect Resolution: `HT=0x20` Root Cause & `HT=0x7b` Solution

1. **Root Cause of `0x20` Failure:**
   - DPAA RM §5.10.3.12.9 (Table 5-380, p. 5-443) documents that `HT=7'h20`
     extracts from a 35-byte address space (`EO[2:7]=0x00..0x1F` for the 32-byte
     Parse Result, `0x20..0x22` for the 3-byte FQID in `IC[AD]`). This matches
     vendor `fman_kg.c:201-213`.
   - In Table 5-380, code `0x20` lacks the `0x70` unvalidated flag. In AC_CC
     scheme mode (`kgse_mode = 0x80000006`), if KeyGen evaluates generic
     extraction from `0x20` as unfulfilled or invalid, it silently substitutes
     the configured default register (`DV=00` -> `FMKG_GDV0R` = `0x00`).
   - The vendor never uses `0x20` for classifier keys (only for FQID `extractedOrs`
     at offset 0 or CAPWAP reassembly at offset 20).

2. **The `HT=0x7b` (`L3_NO_V`) Solution:**
   - Rather than extracting non-header metadata, the family discriminator is
     extracted directly from Layer 3 header byte 0.
   - Every IPv4 header begins with `Version = 4` (byte 0 = `0x45` or `0x4x`).
   - Every IPv6 header begins with `Version = 6` (byte 0 = `0x60` or `0x6x`).
   - Code `0x7b` (`KG_SCH_GEN_L3_NO_V`) extracts relative to `IPOffset_1`
     without validation.
   - Programming `kgse_gec[0] = 0x80F07B00` (VALID=1, DEF=0, SIZE=1, MASK=0xF0,
     HT=0x7b, OFFSET=0) extracts byte 0 and resets the low nibble:
     - IPv4 frame: `0x45 & 0xF0 = 0x40`.
     - IPv6 frame: `0x60 & 0xF0 = 0x60`.
     - Non-IP frame (ARP): `IPOffset_1 = 0xFF` -> default substitute `0x00`.
   - Software contract update:
     - `CC_KEY_DUAL_FAMILY_V4 = 0x40` (was `0x80`)
     - `CC_KEY_DUAL_FAMILY_V6 = 0x60` (was `0x40`)
     - `kgse_gec[0] = 0x80F07B00` (was `0x80FF2004`)
   - This bypasses the metadata defect entirely and uses the silicon's proven
     frame-header reading engine (which already extracts 5 of 6 fields bit-perfect).

### 9.4 2026-09-03 Live Silicon Findings on DUT (.185): Absent-Lane Zero Enforcement

1. **Live Hardware Captures:**
   - On image `2026.09.03-1835-rolling` (`6.18.48-vyos`), live packet captures on
     both IPv4 (DHCP broadcast) and IPv6 (mDNS multicast) confirmed:
     - 45 of 46 bytes (`[1..45]`) are extracted **bit-perfect** on hardware.
     - The absent address lane is **deterministically zero-filled** in hardware
       by the validated GEC address commands:
       - IPv4 frame: bytes `[1..8]` carry IPv4 addresses; bytes `[9..40]` carry
         exactly 32 zero bytes.
       - IPv6 frame: bytes `[1..8]` carry exactly 8 zero bytes; bytes `[9..40]`
         carry IPv6 addresses.
   - GEC slot 0 in AC_CC mode substitutes the default value register (`0x00`)
     for all incoming frames on this silicon/microcode revision.

2. **Root Cause of CC Mismatch:**
   - In `cc_pack_key_dual()`, software programmed `key[0] = 0x40` or `0x60`
     under mask `0xff`, while silicon emits `0x00`.
   - The hardware comparator evaluated `hw_key[0] (0x00) & msk[0] (0xff) != key[0]`,
     causing every frame to miss.

3. **Definitive Architectural Resolution:**
   - Byte 0 is not required to discriminate family because the address lanes are
     disjoint and zero-filled by silicon.
   - Set `CC_KEY_DUAL_FAMILY_V4 = 0x00U` and `CC_KEY_DUAL_FAMILY_V6 = 0x00U` (and
     `ASK_FE_FAMILY_V4 = 0x00`, `ASK_FE_FAMILY_V6 = 0x00`).
   - In `cc_pack_key_dual()`, assert mask `0xff` on the absent lane:
     - IPv4 rule: asserts `msk[9..40] = 0xff` (requiring the 32 zero bytes that
       only an IPv4 frame produces).
     - IPv6 rule: asserts `msk[1..8] = 0xff` (requiring the 8 zero bytes that
       only an IPv6 frame produces).
   - This aligns software with silicon reality, achieves strict key-space
     disjointness, and eliminates the byte-0 mismatch in the CC comparator.
