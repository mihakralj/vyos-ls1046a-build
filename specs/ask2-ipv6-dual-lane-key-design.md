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
0       1     FAMILY  (0x40 v4 / 0x80 v6)   parse-result L3R byte (0x20)
1       8     V4_SRC(4) V4_DST(4)           validated IPv4 hdr @+12 (0x0b)
9       16    V6_SRC(16)                    validated IPv6 hdr  @+8  (0x1b)
25      16    V6_DST(16)                    validated IPv6 hdr  @+24 (0x1b)
41      1     PROTO / NEXTHDR               IP proto, no-validate (0x72)
42      4     SPORT(2) DPORT(2)             L4, no-validate     (0x7e)
------  ----
46 bytes total  (KeyGen limit is 56; uses 6 of 8 GEC slots)
```

- An **IPv4** frame fills FAMILY=0x40, the V4 lane with its 8 address bytes, the
  two V6 lanes with zero (IPv6 header absent → GEC zero default), PROTO, ports.
- An **IPv6** frame fills FAMILY=0x80, the V4 lane with zero (IPv4 header absent),
  both V6 lanes with its 32 address bytes, NEXTHDR, ports.

Because FAMILY differs and the absent lanes are deterministic zeros, v4 and v6
flows occupy **disjoint** key space in one table — no aliasing, one CRC-64
bucket space, one comparator width.

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

| Lane | HT | size | off | gec (BE) |
|---|---|---|---|---|
| FAMILY (parse-result L3R byte 4) | 0x20 | 1 | 4 | `0x80FF2004` |
| IPv4 src+dst @ IPhdr+12 (validated) | 0x0b | 8 | 12 | `0x87FF0B0C` |
| IPv6 src @ IP6hdr+8 (validated) | 0x1b | 16 | 8 | `0x8FFF1B08` |
| IPv6 dst @ IP6hdr+24 (validated) | 0x1b | 16 | 24 | `0x8FFF1B18` |
| proto/nexthdr (IP_PID_NO_V) | 0x72 | 1 | 0 | `0x80FF7200` |
| L4 src+dst (L4_NO_V) | 0x7e | 4 | 0 | `0x83FF7E00` |

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
and the KG hash is `crc64_raw` over the GEC key. Only the validated-code absent-
lane substitution remains to confirm.

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
  "IPv6 PATH DECISION".
