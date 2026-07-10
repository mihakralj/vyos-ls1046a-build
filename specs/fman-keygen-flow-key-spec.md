# FMan KeyGen Flow-Key Architecture for ASK2 (LS1046A / DPAA1)

**Status:** Draft v2.0 (supersedes v1.1). 2026-07-10.
**Branch:** dpaa1
**Scope:** KeyGen extraction configuration, FE-VM dispatch topology, flow-key
serialisation, and the engineering practices that govern all three.
**Depends on:** `ask2-rewrite-spec.md` §13, `dpaa1-afxdp-modernization-spec.md` §5.2.

---

## Changelog

**v2.0 (2026-07-10).** Substantial rewrite. Four material changes:

1. **Extraction order is UNVERIFIED, not confirmed.** v1.1 asserted ascending-bit
   order "confirmed by patch-0148 register logging." That claim does not survive
   scrutiny: patch 0148 added logging to `keygen_scheme_setup()`, which prints the
   EKFC *value* being written. Register-write logging cannot observe what the
   silicon does with that value. The ascending-order table in the 2026-07-06 Qdrant
   entry is the author's annotation, not dmesg output. Three competing order models
   exist and **none has direct empirical support.** §3 now treats the order as an
   unresolved parameter and §7 redesigns the code so that resolving it is a
   one-line data change rather than a serialiser rewrite.

2. **OQ4 (CC-hop clobbers KG hash) is closed as moot.** F-044 and F-047 already
   removed the CC group-table hop. `FMBM_RCCB` points directly at `FE_ENTER`. There
   is no hop to clobber anything. §5 establishes that RCCB → FE_ENTER direct is the
   correct, fastest, and safest topology, and that the 0150 scaffold was an
   architectural error built on an invalid negative test.

3. **F-046 must be reverted.** It stripped `FMAN_AD_FE_ENTER_ALLOCATE` from the
   FE_ENTER root AD on a hypothesis this spec retires. ALLOCATE was set in the only
   confirmed HIT in program history. §5.4.

4. **New review sections.** §8 network performance, §9 modern kernel facilities,
   §10 defensive coding requirements. These are not decoration: §8 identifies a
   consequence of ascending order that invalidates the "runtime keysize
   flexibility" claim from v1.0, and §10 codifies the practice failures that
   produced F-047 (MURAM corruption), the stale `${#key}` gate, and the F-046
   speculative change.

**v1.1 (2026-07-10).** Corrected order to ascending; closed kgse_hc. Order claim
now known to be overstated; kgse_hc closure stands and is retained in §4.3.

**v1.0 (2026-07-10).** Initial. Asserted descending order. Superseded.

---

## 0. Scope

**In scope.**

- EKFC register semantics, target value, and field selection
- FE-VM dispatch topology (`FMBM_RCCB` target, AC_CC mode)
- Flow-key serialisation contract between `ask.ko` and the silicon
- The verification sequence that must gate any code change
- Performance, kernel-idiom, and defensive-coding requirements

**Out of scope.**

- The ehash bucket/entry format (settled; see Qdrant `en_exthash_node`,
  `en_ehash_entry`, and the 2026-07-10 SDK oracle diff)
- CRC64 and bucket derivation (settled; §4.3)
- IPv6 flow offload (deferred; §12)
- CC match-table classification (ASK2 fork B uses FE/ehash, not CC match nodes)

---

## 1. Two extraction mechanisms, one scheme

The FMan KeyGen scheme entry carries two independent extraction engines writing to
different registers. They produce different key-buffer layouts. Choosing between
them is an architectural decision, not a tuning knob.

### 1.1 EKFC — hardware known-fields

- Register: `kgse_ekfc`, offset 0x104 in `fman_kg_scheme_regs`
- Each set bit enables extraction of one field the hard parser already recognises
- The parser has, by the time KeyGen runs, written every recognised field's offset
  into the 32-byte Parse Result in the frame's internal context. EKFC extraction is
  a scatter-gather read against that table: no per-field decode, no byte-range
  arithmetic
- The assembly order is **fixed in silicon**. Software selects *which* fields, not
  *where* they land
- Used by: ASK2 dpaa1 (current), mainline DPAA1 RSS hashing

### 1.2 GEC — generic extract commands

- Registers: `kgse_gec[0..7]`, offsets 0x120 to 0x13C
- Each entry names an arbitrary (offset, length) within the frame or Parse Result
- The assembly order is **declared by software**, in GEC index order
- Costs one additional byte-range copy through the internal-context pipeline per
  entry
- Used by: FMC-configured schemes (NXP SDK `cdx_pcd.xml` driven through `dpa_app`)

### 1.3 Decision: EKFC only

Every field ASK2 needs for an IPv4 5-tuple is a hard-parser known field with a
defined EKFC bit. GEC buys arbitrary byte order at the cost of pipeline latency on
the hot path and a second configuration surface to keep coherent. The combined mode
(EKFC prefix plus GEC suffix) makes the key length a function of both, with the
EKFC prefix length varying by L3 protocol. The NXP SDK does not use the combined
mode for its primary classification path either.

**ASK2 uses EKFC exclusively. `kgse_gec[]` stays zero.** This is a load-bearing
decision: §3's order problem is a direct consequence, and §8.3 shows why paying the
GEC latency to escape it is still the wrong trade.

### 1.4 Corollary: ASK2 key order will not match the NXP SDK key order

| Stack | Mechanism | Field order |
|---|---|---|
| NXP ASK 1.x SDK | FMC/GEC, software-declared | SIP, DIP, PROTO, SPORT, DPORT |
| ASK2 | EKFC, silicon-fixed | determined by §3, **not** the SDK order |

Both carry the same five fields and both are 13 bytes. Correctness requires only
that `ask.ko`'s serialiser emits keys in the order the silicon extracts them.

**Do not "fix" the ASK2 layout to match the SDK.** A future session reading
`fill_ehash_key_info` will be tempted. §7.2 puts a comment in the code to stop it.

---

## 2. The defect in the current 4-tuple

Current configuration, post F-043 (`ci-setup-kernel.sh` sed injection):

```
EKFC = 0x00180006
     = KG_SCH_KN_IPSRC1 | KG_SCH_KN_IPDST1 | KG_SCH_KN_L4PSRC | KG_SCH_KN_L4PDST
```

Twelve bytes, four fields, no IP protocol byte. A TCP flow and a UDP flow with the
same `SIP:SPORT -> DIP:DPORT` produce byte-identical keys and alias to one ehash
entry. On a router this is rare but not impossible, and it is a silent
misforwarding, not a drop. It is also information the silicon hands over for free:
`KG_SCH_KN_PTYPE1` is EKFC bit 18, and the parser has already decoded it.

The NXP SDK's production key includes `ipv4.nextp`. Ours should too.

---

## 3. Target: EKFC = 0x001C0006, and the unresolved order

```
EKFC = 0x001C0006
     = KG_SCH_KN_IPSRC1  (bit 20, 0x00100000)
     | KG_SCH_KN_IPDST1  (bit 19, 0x00080000)
     | KG_SCH_KN_PTYPE1  (bit 18, 0x00040000)   <-- added
     | KG_SCH_KN_L4PSRC  (bit  2, 0x00000004)
     | KG_SCH_KN_L4PDST  (bit  1, 0x00000002)
```

One bit added. Five fields, 13 bytes total (4 + 4 + 1 + 2 + 2).

### 3.1 The order is not known

Three models have circulated. **None is supported by direct observation of the
silicon.** All three are 13 bytes; all three differ in where the protocol byte and
the IP addresses land.

| Model | Predicted layout | Provenance | Verdict |
|---|---|---|---|
| Descending bit position | SIP, DIP, PROTO, SPORT, DPORT | Convention inferred from `fman_keygen.c` structure | Unsupported |
| Size-grouped | SIP, DIP, SPORT, DPORT, PROTO | Post-hoc rationalisation of the 2026-07-04 HIT | Unsupported |
| Ascending bit position | DPORT, SPORT, PROTO, DIP, SIP | Annotation attached to the 2026-07-06 patch-0148 entry | Unsupported |

The ascending claim deserves specific correction because v1.1 of this spec adopted
it as fact. Patch 0148 added `pr_info()` to `keygen_scheme_setup()`. That function
writes `kgse_ekfc` and friends through the KGAR indirect-access window. Logging it
prints the value being written. **It cannot observe what the silicon does with that
value.** The extraction-order table in that Qdrant entry is the author's reading of
the EKFC bit list, not dmesg output.

Nor does the 2026-07-04 HIT settle it. Four candidate keys were inserted
simultaneously and the match was attributed to `00000000C0A801B6` by elimination,
not isolated. Under ascending order with EKFC=0x00180206 and keysize=8, bytes 0..3
are `L4PDST || L4PSRC` and bytes 4..7 are `IPSEC_SPI`. For an ICMP echo the L4 port
slots carry type/code and checksum (the checksum varies per packet), and the SPI
slot reads whatever sits at the ESP-SPI offset inside the ICMP payload. Neither
`00000000` nor `C0A801B6` falls out of that cleanly. Under descending order bytes
0..3 would be `IPSRC1`, which was `192.168.1.137`, not zero. **The observed key is
consistent with no model.** That is a strong signal the attribution was wrong, or
the frame under test was not what the tester assumed, or the pipeline was already
corrupted (see §5.5).

### 3.2 Engineering response: make the order a parameter, not an assumption

The spec does not need to know the order to be implementable. What it needs is:

1. A **single source of truth** for the order, expressed as data
2. A **serialiser derived from that data**, so flipping the model is a one-line edit
3. A **runtime self-check** that reads the silicon's actual extracted key from a
   live frame and compares it against the prediction
4. A **hard gate** preventing any production build from running with an unverified
   order

§7 specifies all four. §11 gives the experiment that populates the table.

**Working assumption for implementation:** ascending bit position. It is the model
with the least-bad provenance and it is what the code will encode. **It is a
placeholder until §11 E2 confirms it.** The code must not depend on the assumption
being right; §7.3's self-check exists exactly to catch it being wrong.

### 3.3 Layout under the working assumption

```
Offset  Width  Field    EKFC bit   Example: TCP 10.99.1.106:55001 -> 10.99.2.200:5201
------  -----  -------  --------   -------------------------------------------------
[0-1]     2B   L4PDST   bit  1     14 51                    (5201)
[2-3]     2B   L4PSRC   bit  2     D6 D9                    (55001)
[4]       1B   PTYPE1   bit 18     06                       (IPPROTO_TCP)
[5-8]     4B   IPDST1   bit 19     0A 63 02 C8              (10.99.2.200)
[9-12]    4B   IPSRC1   bit 20     0A 63 01 6A              (10.99.1.106)
```

Hex: `1451D6D9060A6302C80A63016A` (26 chars, 13 bytes).

### 3.4 PTYPE1 versus PTYPE2

Bit 18 (`PTYPE1`) is the protocol byte of the **outer** IP header. For plain IPv4
TCP/UDP forwarding, which is the entire v1.0 scope, that is the correct field. For
GRE or IPsec-tunnelled inner flows the outer byte reads 0x2F or 0x32 and the inner
protocol requires bit 13 (`PTYPE2`). Tunnelled inner-flow offload is out of scope.

---

## 4. Register reference

Source: mainline `drivers/net/ethernet/freescale/fman/fman_keygen.c` (NXP, 2017).

### 4.1 EKFC field map

```
Bit  Mask        Constant              Width  Field
---  ----------  --------------------  -----  ------------------------------------
31   0x80000000  KG_SCH_KN_PORT_ID       1B   Ingress port ID
30   0x40000000  KG_SCH_KN_MACDST        6B   Ethernet destination MAC
29   0x20000000  KG_SCH_KN_MACSRC        6B   Ethernet source MAC
28   0x10000000  KG_SCH_KN_TCI1          2B   VLAN TCI, outermost
27   0x08000000  KG_SCH_KN_TCI2          2B   VLAN TCI, QinQ inner
26   0x04000000  KG_SCH_KN_ETYPE         2B   EtherType
25   0x02000000  KG_SCH_KN_PPPSID        2B   PPPoE session ID
24   0x01000000  KG_SCH_KN_PPPID         2B   PPP protocol ID
23   0x00800000  KG_SCH_KN_MPLS1         4B   MPLS label entry 1
22   0x00400000  KG_SCH_KN_MPLS2         4B   MPLS label entry 2
21   0x00200000  KG_SCH_KN_MPLS_LAST     4B   MPLS label entry, last
20   0x00100000  KG_SCH_KN_IPSRC1      4/16B  IP source, outer
19   0x00080000  KG_SCH_KN_IPDST1      4/16B  IP destination, outer
18   0x00040000  KG_SCH_KN_PTYPE1        1B   IP protocol / next-header, outer
17   0x00020000  KG_SCH_KN_IPTOS_TC1     1B   DSCP + ECN, outer
16   0x00010000  KG_SCH_KN_IPV6FL1       3B   IPv6 flow label, outer
15   0x00008000  KG_SCH_KN_IPSRC2      4/16B  IP source, inner tunnel
14   0x00004000  KG_SCH_KN_IPDST2      4/16B  IP destination, inner tunnel
13   0x00002000  KG_SCH_KN_PTYPE2        1B   IP protocol, inner tunnel
12   0x00001000  KG_SCH_KN_IPTOS_TC2     1B   DSCP + ECN, inner tunnel
11   0x00000800  KG_SCH_KN_IPV6FL2       3B   IPv6 flow label, inner tunnel
10   0x00000400  KG_SCH_KN_GREPTYPE      2B   GRE protocol type
 9   0x00000200  KG_SCH_KN_IPSEC_SPI     4B   IPsec SPI (ESP/AH)
 8   0x00000100  KG_SCH_KN_IPSEC_NH      1B   IPsec next header (AH)
 7   0x00000080  KG_SCH_KN_IPPID         2B   IP fragment identification
6-3  reserved. No constants defined. Setting these bits is undefined behaviour.
 2   0x00000004  KG_SCH_KN_L4PSRC        2B   L4 source port
 1   0x00000002  KG_SCH_KN_L4PDST        2B   L4 destination port
 0   0x00000001  KG_SCH_KN_TFLG          1B   TCP flags
```

**Bit 9 warning.** `IPSEC_SPI` is why F-043 existed. On a non-IPsec frame the
parser has no SPI offset, so the extraction reads whatever byte range the Parse
Result's SPI slot happens to point at, which for TCP is header bytes that vary per
connection. Any EKFC containing bit 9 makes the key unpredictable for non-IPsec
traffic. Never enable bit 9 on a scheme that must classify plain TCP/UDP.

### 4.2 EKDV default substitution

When EKFC enables a field the parser did not populate, the silicon substitutes from
`kgse_dv0` or `kgse_dv1` as selected by `kgse_ekdv`:

- `IPSRC1`, `IPDST1`: `kgse_dv0` (default `0x0A0A0A0A`), selector bits [19:18]
- `L4PSRC`, `L4PDST`: `kgse_dv1` (default `0x0B0B0B0B`), selector bits [9:8]
- `PTYPE1`: **no EKDV slot exists.** See §10.6 for the consequence and the guard.

### 4.3 kgse_hc is not a hash-algorithm selector (settled, do not reopen)

```
kgse_hc = (hash_fqid_count - 1)          /* bits [23:0]  FQID spread range   */
        | (hashShift << 24)              /* bits [31:24] hash-result shift    */
        | (symmetric ? 0x40000000 : 0)   /* symmetric-hash flag               */
```

Reference: `fman_keygen.c` lines 569-583.

The KeyGen hash algorithm is **fixed silicon CRC-64**. `kgse_hc` configures how the
result is consumed for FQID distribution. It selects nothing about the hash itself.

Two errors that circulated and are now closed:

- "The DPAA1 driver programs Toeplitz through hc." False. There is no Toeplitz
  anywhere in the freescale DPAA1 drivers. Grep-confirmed.
- "Software CRC64 may not match the silicon hash." False. ASK1 ran
  `get_indexed_hash_bucket()` with CRC64-ECMA-182 (reflected poly
  `0xC96C5795D7870F42`, seed `~0ULL`, no final xor) in production against the same
  210-family microcode. Our `fman_pcd_crc64()` and `fman_pcd_ehash_bucket_index()`
  are verbatim-identical, verified 2026-07-10.

Correct ASK2 settings, all already in place: `hashShift = 0`, `symmetric = false`
(direction-distinct flows, which is what conntrack wants), `mask = 0x7fff`.

**Nothing to align. Do not spend another session reading back `kgse_hc`.**

### 4.4 KGSE indirect write protocol

```
1. Populate the in-memory struct fman_kg_scheme_regs
2. kgar = FM_KG_KGAR_GO | FM_KG_KGAR_WRITE | FM_KG_KGAR_SEL_SCHEME_ENTRY
        | port_id | (scheme_id << 16)
3. iowrite32be(kgar, &kgr->kgar)
4. Poll until FM_KG_KGAR_GO clears
5. Check FM_KG_KGAR_ERR
```

Handled by `keygen_scheme_setup()`. §10.2 requires a readback after step 5.

---

## 5. Dispatch topology: RCCB points at FE_ENTER, not at a CC group table

This section supersedes patch 0150's premise and closes v1.1's OQ4.

### 5.1 The current topology

Post F-044 (`5456274`, commented out `fe_enter_off = gro`) and F-047 (`f4b6882`,
disabled the scaffold allocation), the armed path is:

```
BMI RX -> KeyGen (AC_CC: KGSE_MODE=0x80000006, KGSE_CCBS=0)
       -> FMBM_RCCB -> FE_ENTER (FE-VM root AD)
       -> hash frontend (EXT_HASH)
       -> ehash lookup in DDR
       -> HIT: MUX -> ENQ    /    MISS: Exit
```

There is no CC group table, no CC node, no match table. **The "CC engine hop" that
v1.1's OQ4 asked about does not exist in this build.** Whatever is causing the
current MISS, it is not a hop clobbering the KG hash, because there is no hop.

Verified by readback, 2026-07-09:

```
FE_ENTER  @0x56100: 40800000 00000000 000000f6 00055400   -> 0x55400
hash FE   @0x55400: 06000000 7fffff00 00000000 f7780000
                    00000000 00055100 00055300
                    word0 = EXT_HASH
                    word1 = mask 0x7fff, ctxSize 256, hashShift 0
                    word2/3 = DDR table @0xf7780000
                    word5 = HIT  -> MUX  0x55100
                    word6 = MISS -> Exit 0x55300
```

Frames reach the ehash. `MISS -> EXIT-DEALLOCATE` is observed. The walk executes.
The comparison fails.

### 5.2 Why RCCB -> FE_ENTER is correct

In AC_CC mode the FMan controller dispatches to whatever Action Descriptor sits at
`FMBM_RCCB`. That AD is not required to be a CC `CONT_LOOKUP` group entry. In the
vendor FE/ehash architecture it is the **FE-VM root AD**, a different AD species
with a different word layout: `word2 = pcAndOffsets`, `word3 = next-FE pointer`,
versus a CC `CONT_LOOKUP` whose `word0 = (numKeys << 24) | matchTableAddr`.

**Proof.** 2026-07-04, `echo "engage 10 59200"` produced
`fman_pcd: port 0x10 FE-ARMED (kgse_mode=AC_CC fmbm_rccb=0x59200 kgse_ccbs=0)`.
RCCB pointed at FE_ENTER. That configuration produced the **only confirmed HIT in
program history**: ping forwarded on match, dropped on MISS, both directions,
single-flow isolation, 5/5 packets. The CC engine parsed FE_ENTER without
complaint.

### 5.3 Why 0150's premise was wrong

Patch 0150 justified the group table with: "RCCB pointed directly to FE_ENTER,
which the CC engine cannot parse as a group entry. Result: 256 flows covering every
possible 1-byte key matched zero packets."

That negative test is invalid. It ran with the scaffold **already active**, where
the group table's `CONT_LOOKUP` had `numKeys = 0`, so every frame missed the match
table and was routed by the miss-AD to FQ 0x200. **The ehash was never consulted.**
0/256 is fully explained by that, and says nothing about whether RCCB can point at
FE_ENTER.

Corroboration: the 2026-07-06 group-table build (`28809182051`) reported zero QMan
errors and ping 3/3. It worked because frames went to the kernel via FQ 0x200, not
because the FE-VM did anything. FE_ENTER was unreachable in that topology.

### 5.4 F-046 must be reverted

F-046 (`0f30305`) stripped `FMAN_AD_FE_ENTER_ALLOCATE` (0x00800000) from FE_ENTER
`word0`, taking it from `0x40800000` to `0x40000000`, with the stated rationale
"no ALLOCATE, preserve KG hash."

The 2026-07-04 HIT ran with **ALLOCATE set**. The Phase 1 readback records it
explicitly: `40800000 00000000 000000f6 0004b000 (ALLOCATE bit set,
pcAndOffsets=0xF6)`.

ALLOCATE is what allocates the FE workspace: the scratch region the FE-VM uses to
hold the extracted key and the KG hash result for the hash frontend to read.
Removing it does not preserve the hash context. It removes the place the context
lives.

F-046 is a speculative change made against the one known-good configuration, on a
hypothesis (§5.1) this spec retires. **Revert it. Restore `word0 = 0x40800000`.**
Cost: one sed line. Risk: none; it restores a proven value.

### 5.5 The scaffold was actively corrupting MURAM

F-047's own root cause is precise and worth preserving: the scaffold allocated 304
bytes per engage cycle from `gen_pool`, never freed them, and issued `iowrite32be`
at offsets that "risk overlapping active FMan data structures (KG schemes, CC trees,
params pages, FE objects)." Overlap produces malformed enqueues, `ecir.fqid=0x0`
storms, and eventually locks the peer board's SFP+ link hard enough to need a warm
reboot.

**Every MISS result in the record was collected with that scaffold still
allocating.** F-043 (2026-07-09), the 16-permutation sweep, the 24-permutation
sweep, the 8-byte replay, E1, E2. All of them. F-047 landed 2026-07-10 06:10 and
CI run `29073080840` was dispatched. **No post-F-047 flow-HIT test exists in the
record**, because the SFP+ DAC developed a unidirectional fault (`.185` RX=0 while
`.106` TX=74376) immediately afterwards.

This is the most probable explanation for the entire MISS corpus, and it is
completely untested.

### 5.6 Required actions

1. Keep F-044 and F-047. RCCB -> FE_ENTER direct is the vendor topology.
2. **Delete** the scaffold code rather than leaving `if (0) { ... }`. Dead code
   under a false conditional survives rebases and gets re-enabled by accident. See
   §10.7.
3. Revert F-046. Restore ALLOCATE.
4. Close OQ4 as moot by construction.

### 5.7 Performance consequence

The group table costs, per frame: one extra MURAM descriptor fetch (group entry),
one match-table walk that can never match (`numKeys = 0`), and one miss-AD fetch.
Three additional MURAM round-trips on the hot path, for zero classification value.
Deleting it is strictly better on latency, on MURAM footprint (304 B/cycle
reclaimed), and on blast radius (§5.5).

---

## 6. What changes

| File | Change | Gate |
|---|---|---|
| `bin/ci-setup-kernel.sh` | EKFC sed `0x00180006` -> `0x001C0006` | §11 E2 |
| `bin/ci-setup-kernel.sh` | Revert F-046 sed; restore ALLOCATE | Immediate |
| `fman_pcd.c` | Delete scaffold block (not `if (0)`) | Immediate |
| `fman_pcd_fe_arm_engage()` | ehash keysize 12 -> 13 | §11 E2 |
| `ask_hw.c` | `proto` field, table-driven serialiser, self-check | §11 E2 |
| `vyos-offload-ask` | Derive key length from one constant | Immediate |
| `fman_pcd_crc64` callsite | keylen 12 -> 13 | §11 E2 |
| `tests/` | Golden-vector + order-agnostic kunit params | §11 E2 |

Two changes are **immediate and independent** of the order question: reverting
F-046, and deleting the scaffold. Do those first. Everything else waits on E2.

---

## 7. Flow-key serialisation: table-driven, self-checking

### 7.1 Single source of truth

The extraction order is data, not control flow. Encode it once.

```c
/* drivers/net/ethernet/freescale/fman/fman_pcd_key.c */

/**
 * struct fman_kg_field - one EKFC-extractable field
 * @bit:    EKFC bit position
 * @width:  bytes this field contributes to the key buffer (IPv4 sizing)
 * @name:   for tracepoints and the self-check dump
 */
struct fman_kg_field {
	u8		bit;
	u8		width;
	const char	*name;
};

/*
 * EKFC extraction order.
 *
 * ORDER IS UNVERIFIED AGAINST SILICON. See specs/fman-keygen-flow-key-spec.md
 * §3.1. This table encodes the ASCENDING-bit-position working assumption. If
 * fman_pcd_key_selftest() reports a mismatch, reorder THIS TABLE ONLY. No other
 * code needs to change.
 *
 * Do not reorder to match the NXP SDK's SIP,DIP,PROTO,SPORT,DPORT layout. The
 * SDK uses FMC/GEC declared-order extraction; we use EKFC known-field
 * extraction. Same fields, different silicon order. See §1.4.
 */
static const struct fman_kg_field fman_kg_order_v4[] = {
	{ .bit =  1, .width = 2, .name = "l4pdst"  },
	{ .bit =  2, .width = 2, .name = "l4psrc"  },
	{ .bit = 18, .width = 1, .name = "ptype1"  },
	{ .bit = 19, .width = 4, .name = "ipdst1"  },
	{ .bit = 20, .width = 4, .name = "ipsrc1"  },
};
```

Derive the key length rather than hardcoding it. One constant, one place:

```c
#define FMAN_KG_EKFC_V4_5TUPLE	0x001C0006u

static_assert(FMAN_KG_EKFC_V4_5TUPLE ==
	      (KG_SCH_KN_IPSRC1 | KG_SCH_KN_IPDST1 | KG_SCH_KN_PTYPE1 |
	       KG_SCH_KN_L4PSRC | KG_SCH_KN_L4PDST),
	      "EKFC constant and field selection have diverged");
```

At init, compute and cache the length by walking the table, and cross-check it
against the EKFC bitmask so a table edit that forgets a bit is caught at probe:

```c
static int fman_pcd_key_init(struct fman_pcd *pcd)
{
	u32 covered = 0;
	size_t len = 0;
	int i;

	for (i = 0; i < ARRAY_SIZE(fman_kg_order_v4); i++) {
		covered |= BIT(fman_kg_order_v4[i].bit);
		len += fman_kg_order_v4[i].width;
	}

	if (covered != FMAN_KG_EKFC_V4_5TUPLE) {
		dev_err(pcd->dev,
			"key order table covers 0x%08x, EKFC is 0x%08x\n",
			covered, FMAN_KG_EKFC_V4_5TUPLE);
		return -EINVAL;
	}

	pcd->key_len_v4 = len;		/* 13 */
	return 0;
}
```

### 7.2 Serialiser

```c
struct ask_hw_flow_key_v4 {
	__be32	src_ip;
	__be32	dst_ip;
	__be16	src_port;
	__be16	dst_port;
	u8	proto;
};

/*
 * Emit the key in the byte order the silicon extracts it.
 *
 * Field placement comes from fman_kg_order_v4[]. If the order changes, this
 * function does not.
 *
 * Note the IP fields land at offsets 5 and 9 under the current table: both are
 * unaligned. put_unaligned_be32() is mandatory, not stylistic.
 */
static int fman_pcd_key_serialize_v4(const struct fman_pcd *pcd,
				     const struct ask_hw_flow_key_v4 *k,
				     u8 *buf, size_t buf_len)
{
	size_t off = 0;
	int i;

	if (WARN_ON_ONCE(buf_len < pcd->key_len_v4))
		return -ENOBUFS;

	for (i = 0; i < ARRAY_SIZE(fman_kg_order_v4); i++) {
		switch (fman_kg_order_v4[i].bit) {
		case  1: put_unaligned(k->dst_port, (__be16 *)(buf + off)); break;
		case  2: put_unaligned(k->src_port, (__be16 *)(buf + off)); break;
		case 18: buf[off] = k->proto;                               break;
		case 19: put_unaligned(k->dst_ip,   (__be32 *)(buf + off)); break;
		case 20: put_unaligned(k->src_ip,   (__be32 *)(buf + off)); break;
		default:
			return -EINVAL;   /* table and switch diverged */
		}
		off += fman_kg_order_v4[i].width;
	}

	return off;
}
```

The `default: return -EINVAL` is not defensive theatre. It is the failure mode when
someone adds a field to the table and forgets the switch arm.

### 7.3 Runtime self-check against silicon

This is the piece that makes the unresolved order safe to ship behind a gate.

```c
/*
 * Read the key the silicon actually extracted for one live frame out of the FE
 * workspace, and compare against what fman_pcd_key_serialize_v4() would have
 * produced for the same 5-tuple.
 *
 * Exposed at /sys/kernel/debug/fman_pcd/<fm>/key_selftest. Writing a 5-tuple
 * arms a one-shot capture; reading returns predicted vs observed, byte-aligned,
 * with per-field annotation from fman_kg_order_v4[].
 *
 * A production build MUST NOT engage the FE path until this reports PASS. See
 * §10.4.
 */
int fman_pcd_key_selftest(struct fman_pcd *pcd,
			  const struct ask_hw_flow_key_v4 *expect,
			  const u8 *observed, size_t observed_len);
```

Implementation reuses the 0146 context builder, which already proved capable of
dumping the extracted key (it produced `00000000C0A801B6` on 2026-07-04).

Output shape, so a human can read the answer directly:

```
predicted (ascending):  1451 D6D9 06 0A6302C8 0A63016A
observed:               0A63016A 0A6302C8 06 D6D9 1451
per-field diff:
  off 0  l4pdst  want 1451      got 0A63      MISMATCH
  ...
verdict: FAIL. observed matches model 'descending'. Reorder fman_kg_order_v4[].
```

Have the self-check try all three candidate orders and **name** the one that
matches. That turns a multi-session investigation into a single dmesg line.

---

## 8. Network performance review

### 8.1 Ascending order kills prefix-match keysize reduction

v1.0 of this spec claimed the ehash `keysize` gives "runtime flexibility over what
constitutes a flow." Under ascending order that claim is **false and dangerous**.

The ehash compares `keysize` bytes starting at offset 0. Under ascending order
offset 0 holds `L4PDST`. Reducing `keysize` therefore truncates the **IP
addresses**, not the ports. A `keysize = 8` configuration matches on
`DPORT || SPORT || PROTO || (first 3 bytes of DIP)`. That is not a coarser flow
definition. It is a wrong one: two flows to different hosts in the same /24 with the
same port pair collide.

**Requirement: `keysize` must equal the full extracted key length. 13 for
EKFC=0x001C0006.** §10.3 makes this a checked invariant, not a convention.

This also retroactively condemns the 2026-07-04 test's `keysize = 8` against a
16-byte extraction, and the 2026-07-09 `keysize = 12` against what may have been a
16-byte extraction. Both were comparing a prefix that does not identify a flow.
If the extraction was 16 bytes (EKFC=0x00180206) and keysize was 12, the comparison
covered `DPORT, SPORT, SPI, DIP` and dropped `SIP` entirely. That alone can explain
non-deterministic HIT behaviour without invoking any hash theory.

**This is a candidate root cause that no prior session considered.** Add it to §13.

### 8.2 Cost of the added PTYPE1 byte: zero on the data path

- **Extraction.** The parser already decoded the protocol byte and recorded its
  offset in the Parse Result. EKFC reads it in the same gather that fetches IPSRC1
  and IPDST1. No additional pipeline stage.
- **Hashing.** The KG hash is computed in silicon over the extracted buffer. 13
  bytes versus 12 is one more byte through a hardware CRC-64. Not measurable.
- **Comparison.** The ehash compares 13 bytes instead of 12. Record layout is 8
  bytes of chain header followed by the key at offset 8, so the key spans bytes
  8..20 rather than 8..19. Both fit within a 32-byte DDR burst if records are
  32-byte aligned. **Verify record alignment.** If records are 24-byte aligned, a
  13-byte key crosses into a second burst and adds one DDR round-trip per lookup.
  At 14.88 Mpps that is not free. §11 step 5.
- **Insert path.** `crc64()` over 13 bytes instead of 12: one more table iteration
  per flow insert. Control plane. Irrelevant.

Expected steady-state impact on the 7.34 Gbps HIT path measured 2026-07-07 (OVFQ=1,
MTU 9000): none, contingent on the alignment check above.

### 8.3 Cost of the alternative (GEC) that we are not paying

GEC would give software-declared byte order and eliminate §3's entire problem. It
costs one byte-range copy through the internal-context pipeline per field, five
fields, on every frame. On a 4-core A72 at 1.8 GHz with FMan v3 at ~700 MHz driving
~32 Mpps aggregate, adding five IC copies per frame to the classification stage is
not a rounding error. **The order problem is a one-time engineering cost (§7.3).
The GEC latency would be a permanent per-frame cost.** Stay on EKFC.

### 8.4 Hash distribution

Adding the protocol byte adds ~1 bit of realistic entropy (TCP versus UDP dominate).
With `mask = 0x7fff` (32768 buckets) and the MURAM-bounded ceiling of roughly 750
concurrent hardware flows (`ask2-rewrite-spec.md` §2.1), the load factor is 0.023.
Essentially every bucket holds zero or one entry; chain walks are one deep. Bucket
distribution is not a performance lever at this scale. Do not tune it.

### 8.5 Where the remaining performance actually is

Ranked, from the measured record:

1. **OVFQ=1 on TX FQ `context_a`** (landed 2026-07-07): +10.4% average, 6.65 ->
   7.34 Gbps. Already in.
2. **B0V=0 dedicated offload FQ**: estimated +3%. Not yet done.
3. **L3 MANIP offload** for full `cdx.ko` parity (>=8 Gbps sustained). Not yet done.
4. **Scaffold deletion** (§5.7): three MURAM round-trips per frame. Unmeasured but
   strictly positive.
5. Key width: zero.

The EKFC upgrade is a correctness fix, not a performance fix. Do not sell it as one.

---

## 9. Modern kernel facilities

The FMan PCD code predates several facilities that would have prevented the specific
bugs in this program's history. Adopt them.

### 9.1 Compile-time invariants

```c
#include <linux/build_bug.h>

static_assert(FMAN_KG_EKFC_V4_5TUPLE == 0x001C0006u);
static_assert(ARRAY_SIZE(fman_kg_order_v4) == 5);

/* The stale ${#key} gate (F-043 era) had a C analogue: a hardcoded 12. */
BUILD_BUG_ON(ASK_HW_FLOW_KEY_V4_SIZE != 13);
```

The `${#key} -ne 24` shell gate survived two key-size changes and silently prevented
a 13-byte test key from ever reaching hardware. The kernel-side equivalent is a
magic number. Kill both: §10.3.

### 9.2 Unaligned accessors

Under the working order, `IPDST1` lands at offset 5 and `IPSRC1` at offset 9.
Neither is 4-byte aligned. arm64 tolerates unaligned loads in most contexts but
`__be32 *` dereference at an odd offset is UB and sparse will flag it.

```c
#include <linux/unaligned.h>	/* 6.12+; was <asm/unaligned.h> */

put_unaligned(k->dst_ip, (__be32 *)(buf + 5));
```

### 9.3 Scope-based cleanup for MURAM

F-047's root cause was a 304-byte leak per engage cycle, compounded by writes to
offsets whose ownership was never asserted. `linux/cleanup.h` (6.5+) makes the leak
structurally impossible.

```c
#include <linux/cleanup.h>

DEFINE_FREE(fman_muram, void *,
	    if (_T) fman_muram_free_mem(muram, _T, size))

static int fe_arm_engage(struct fman_pcd *pcd)
{
	void *grp __free(fman_muram) = fman_muram_alloc(pcd->muram, 16);
	if (!grp)
		return -ENOMEM;
	...
	return 0;	/* grp freed automatically on every exit path */
}
```

And for the locking that produced the 0148-v2 double-lock deadlock:

```c
guard(mutex)(&pcd->fe_lock);
```

or, where the pool must be taken before the lock:

```c
scoped_guard(mutex, &pcd->fe_lock) {
	...
}
```

### 9.4 Tracepoints, not `pr_info`

The 0148 debug logging that produced the false "order confirmed" claim was a
`pr_info` in a hot init path. Static tracepoints cost nothing when disabled and give
a structured record that cannot be misread as something it is not.

```c
TRACE_EVENT(fman_kg_scheme_write,
	TP_PROTO(u8 scheme, u32 ekfc, u32 mode, u32 hc, u32 ccbs),
	...
);

TRACE_EVENT(fman_pcd_key_serialize,
	TP_PROTO(const u8 *key, size_t len, u32 bucket),
	...
);
```

Print the register **value** and label it as such. Never print an interpretation of
silicon behaviour as if it were an observation.

### 9.5 kunit parameterised tests for the order table

The order is a parameter. Test it as one.

```c
static const struct order_case {
	const char *name;
	u8 bits[5];
	const char *golden_hex;	/* for the canonical 5-tuple */
} order_cases[] = {
	{ "ascending",   { 1, 2, 18, 19, 20 }, "1451D6D9060A6302C80A63016A" },
	{ "descending",  { 20, 19, 18, 2, 1 }, "0A63016A0A6302C806D6D91451" },
	{ "size_grouped",{ 20, 19, 2, 1, 18 }, "0A63016A0A6302C8D6D9145106" },
};
KUNIT_ARRAY_PARAM(order, order_cases, order_case_desc);
```

When E2 names the winner, delete the losers and keep the golden vector as a
regression test.

### 9.6 Readback verification of MMIO

Every `iowrite32be()` into MURAM or a KGSE indirect register should be followed by
an `ioread32be()` compare in debug builds. The FMan does not report a failed
indirect write except through `FM_KG_KGAR_ERR`, and MURAM writes report nothing at
all. §10.2.

### 9.7 `dev_err_probe()` and friends

Probe-path error returns should use `dev_err_probe()` so `-EPROBE_DEFER` does not
spam. Trivial, but the FMan PCD patches predate it.

### 9.8 What not to reach for

- **No `__packed` struct overlaying the key buffer.** The order is silicon-defined
  and may change. A serialiser function is the right abstraction; a struct is a
  premature commitment that will be wrong in the IPv6 case (§12).
- **No `memcpy()` of a host struct into the key buffer.** Field order in C and
  field order in silicon are unrelated facts.

---

## 10. Defensive coding requirements

Each requirement below traces to a specific defect in this program's history. They
are not general advice.

### 10.1 Never write MURAM at an offset you do not own

**Origin:** F-047. The scaffold issued `iowrite32be` to `gen_pool` offsets it had
allocated, but the arithmetic derived neighbouring offsets from them. When those
overlapped a live KG scheme or FE object, the FMan generated `ecir.fqid=0x0`
enqueues, flooded the QMan error path, and locked the peer board's SFP+ port hard
enough to require a warm reboot.

**Rule.** Every MURAM write goes to an address returned by `fman_muram_alloc()` for
this object, offset by less than the allocated size. Assert it:

```c
static inline void muram_write32(struct fman_muram_obj *o, size_t off, u32 v)
{
	if (WARN_ON_ONCE(off + 4 > o->size))
		return;
	iowrite32be(v, o->base + off);
}
```

Never compute a MURAM address from another object's address.

### 10.2 Readback every silicon write that has no error report

**Origin:** the entire pipeline-verification burden of 2026-07-09, where debugfs
readback was bolted on after the fact to discover FE_ENTER was dispatching to 0x10.

**Rule.** After programming an FE descriptor or a KGSE entry, read it back and
compare. Fail the engage on mismatch. This is a control-plane operation; the cost is
irrelevant.

```c
if (ioread32be(o->base + off) != v) {
	dev_err(pcd->dev, "MURAM readback @%zx: wrote %08x read %08x\n",
		off, v, ioread32be(o->base + off));
	return -EIO;
}
```

### 10.3 Derive lengths from one constant, in one place

**Origin:** `vyos-offload-ask`'s `${#key} -ne 24` gate survived a sed-based change
to 13-byte keys. The flow was rejected before insertion. The failure looked
identical to a MISS, and a full test cycle was lost chasing the wrong layer.

**Rule.** The key length has exactly one definition. The kernel exports it; the
shell reads it.

```c
/* debugfs: /sys/kernel/debug/fman_pcd/<fm>/key_len -> "13\n" */
```

```sh
KEY_LEN=$(cat "${DEBUGFS}/key_len")
EXPECT_CHARS=$((KEY_LEN * 2))
if [ ${#key} -ne "$EXPECT_CHARS" ]; then
    echo "ERROR: key must be ${KEY_LEN} bytes (${EXPECT_CHARS} hex chars), got $(( ${#key} / 2 ))"
    exit 1
fi
```

No literal `24`, no literal `26`, anywhere.

### 10.4 A build that cannot verify its own key layout must refuse to engage

**Origin:** three sessions of MISS testing against an unverified extraction order.

**Rule.** `fe_arm_engage()` returns `-EPROTO` unless `fman_pcd_key_selftest()` has
passed for the current EKFC value since boot. Expose the state:

```
/sys/kernel/debug/fman_pcd/<fm>/key_verified -> "0" | "1"
```

A developer can force-engage with `fman_pcd.force_unverified=1` for experiments. A
production build cannot.

This single rule would have prevented every MISS-chasing session in the record.

### 10.5 keysize == extracted length, checked

**Origin:** §8.1. Configuring `keysize < key_len` silently truncates whichever
fields the silicon placed last, which under ascending order are the IP addresses.

**Rule.**

```c
if (ehash->keysize != pcd->key_len_v4) {
	dev_err(pcd->dev, "ehash keysize %u != extracted key length %zu\n",
		ehash->keysize, pcd->key_len_v4);
	return -EINVAL;
}
```

If a future design genuinely wants a coarser flow key, it changes EKFC, not keysize.

### 10.6 Guard the PTYPE1 default gap

**Origin:** §4.2. `kgse_ekdv` has no default-value slot for `PTYPE1`. On a non-IP
frame the silicon writes the parser's "no IP header" indication (expected `0x00`,
but this is not documented and not observed).

**Analysis.** ASK2 gates the FE path on the parser's IPv4 indication, so non-IP
frames do not reach a PTYPE1-enabled scheme. If one did, it produces a deterministic
key with `0x00` at the PTYPE1 offset, matches no inserted flow (none carries
`proto == 0`), and MISSes cleanly to the kernel.

**Rule.** Reject `proto == 0` at flow insert. It is not a valid IP protocol number
in this context and its presence means a caller has an uninitialised field.

```c
if (!k->proto)
	return -EINVAL;
```

This is a two-line guard that turns a silent aliasing hazard into an insert error.

### 10.7 Delete dead code; do not disable it

**Origin:** F-047 disabled the scaffold with `if (0) { /* F-047 */ }`. F-044
disabled `fe_enter_off = gro` with a comment.

**Rule.** Both blocks are architecturally wrong (§5), not temporarily inconvenient.
Delete them. Dead code under a false conditional survives `git apply --3way`
rebases, gets re-enabled by a well-meaning conflict resolution, and carries no
signal about *why* it is dead. The git history holds it.

Where a block is genuinely experimental, gate it on a module parameter with a
`MODULE_PARM_DESC` explaining the experiment, not on `if (0)`.

### 10.8 Never change a known-good configuration on a hypothesis

**Origin:** F-046 stripped ALLOCATE from the FE_ENTER AD, on the "preserve KG hash"
theory, against the only configuration that had ever produced a HIT.

**Rule.** Changes to a proven-good state require either (a) an observation that
contradicts the state, or (b) an A/B under which both states are measured. A
hypothesis is neither. When the hypothesis is about silicon behaviour that no
register readback can settle, the change is unfalsifiable and must not land.

### 10.9 Cold-boot before any silicon experiment

**Origin:** the "port goes deaf after disengage" symptom bisected to accumulated
BMI corruption from pre-fix builds, not to any commit. Four clean engage/disengage
cycles pass on a cold boot.

**Rule.** Silicon experiments run on a cold-booted DUT. A warm reboot does not
clear BMI or MURAM state. Record the boot type in every result.

### 10.10 One variable per experiment

**Origin:** the 2026-07-04 HIT inserted four candidate keys simultaneously and
attributed the match by elimination. That attribution is the single load-bearing
data point behind two competing order models, and it does not support either (§3.1).

**Rule.** One key, one flow, one packet class. If four candidates must be tested,
test them in four runs with a clear between each.

---

## 11. Verification sequence

Execute in order. Stop at the first PASS that unblocks the next step.

**Step 0. Repair the test rig.**
`.185` eth3 shows TX=48 RX=0 while `.106` shows TX=74376 RX=1884. Unidirectional
DAC fault. Swap the cable, then swap the SFP cage. No experiment is meaningful
without a working RX path.

**Step 1. Land the two order-independent fixes.**
Revert F-046 (restore `word0 = 0x40800000`). Delete the scaffold block. Rebuild.
Cold-boot (§10.9).

**Step 2. Reproduce the known-good HIT.**
Arm with the exact 2026-07-04 recipe. Insert the single 8-byte ICMP key. Ping from
the peer. This is a regression check against a proven state, not a discovery.

- **HIT** -> §5.5 confirmed: the MISS corpus was scaffold-induced MURAM corruption.
  You now have a working baseline. Proceed to Step 3.
- **MISS** -> the scaffold was not the only problem. Proceed to Step 3 anyway; the
  workspace dump answers both questions.

**Step 3. E2: single-packet workspace dump.**
Use the 0146 context builder to capture the FE workspace for one live TCP frame with
known 5-tuple. Read the extracted key bytes. Run `fman_pcd_key_selftest()` (§7.3),
which reports which of the three candidate orders matches, or none.

This settles §3.1 permanently and in one packet. It also reads the KG hash from the
workspace if present, letting you compare `crc64(observed_key) >> 48 & mask` against
the observed bucket, which settles bucket derivation at the same time.

**Step 4. Fix the order table.**
Reorder `fman_kg_order_v4[]` to whatever Step 3 named. Delete the losing kunit
parameter cases. Keep the winner as a golden vector.

**Step 5. Verify ehash record alignment (§8.2).**
Read the DDR record layout. Confirm a 13-byte key at offset 8 does not straddle a
DDR burst boundary. If it does, either pad the record to 32-byte alignment or accept
and document the extra round-trip.

**Step 6. Apply the EKFC upgrade.**
`0x00180006 -> 0x001C0006`, keysize 12 -> 13, key length constant 12 -> 13. Rebuild,
cold-boot, self-check passes, engage.

**Step 7. E-EKFC-1: confirm the 5-tuple key.**

```sh
echo "set 0x7fff 13 0" > /sys/kernel/debug/fman_pcd/0/fe_ehash
echo "add $(cat .../key_len | ...)" > /sys/kernel/debug/fman_pcd/0/fe_flow
socat TCP-CONNECT:10.99.2.200:5201,sourceport=55001,reuseport - < /dev/null
cat /sys/kernel/debug/fman_pcd/0/fe_stats
```

**Step 8. Regression.**
Re-run the 2026-07-07 throughput benchmark (OVFQ=1, MTU 9000). Confirm the HIT path
holds at 7.34 Gbps +/- noise. Confirm no `ecir.fqid=0x0` in dmesg across ten
engage/disengage cycles.

---

## 12. IPv6 (deferred, but the design must not preclude it)

With `IPSRC1` and `IPDST1` set and an IPv6 frame, the silicon extracts 16 bytes per
address. EKFC=0x001C0006 then yields `2 + 2 + 1 + 16 + 16 = 37` bytes.

The ehash has a fixed `keysize`. A 13-byte table cannot classify a 37-byte key. §8.1
rules out papering over this with a short `keysize`, because the truncation would
land on the addresses.

Therefore IPv6 requires a **separate KG scheme and a separate ehash table**, with CC
or parser-result dispatch on EtherType (`0x0800` versus `0x86DD`) selecting between
them. The `fman_kg_order_v4[]` table generalises to `fman_kg_order_v6[]` with
`.width = 16` on the address rows; the serialiser (§7.2) needs no structural change,
which is the reason §9.8 forbids a `__packed` struct here.

Out of scope for v1.0. Do not close the door on it.

---

## 13. Candidate root causes for the flow-HIT failure, ranked

| # | Candidate | Status | Discriminating test |
|---|---|---|---|
| 1 | Scaffold MURAM corruption (§5.5) | **Untested.** Every MISS predates F-047. | Step 2 |
| 2 | `keysize < extracted length` truncating the IP addresses (§8.1) | **Never considered.** 07-04 used 8 vs 16; 07-09 used 12 vs possibly 16. | Step 3 dump gives the true length |
| 3 | F-046 stripping ALLOCATE (§5.4) | **Untested.** Removed a bit set in the only HIT. | Step 1 + Step 2 |
| 4 | Wrong extraction order (§3.1) | Possible, but permutation-exhaustion argues against it | Step 3 |
| 5 | CC-hop clobbers KG hash | **Closed.** No hop exists post-F-044. | n/a |
| 6 | CRC64 / kgse_hc mismatch | **Closed.** §4.3. | n/a |
| 7 | Bucket or entry struct layout | **Closed.** Verbatim match to SDK oracle. | n/a |
| 8 | `contextOffsetInWS` | **Closed.** Both pass 0. | n/a |

Candidate 2 is new to this revision and deserves attention. If the 2026-07-09 runs
had `keysize = 12` while EKFC=0x00180006 extracted 12 bytes, they were consistent.
But the F-043 commit changed EKFC *and* keysize in the same build. If the extraction
was still producing 16 bytes at that point (stale scheme, or the sed not taking on
all five schemes), a 12-byte comparison would have covered `DPORT, SPORT, SPI, DIP`
and dropped `SIP`. That is a plausible mechanism for uniform MISS across all
permutations of a 12-byte key: the comparison window itself was wrong.

Step 3's workspace dump reads the true extracted length and closes this.

---

## 14. Open questions

1. **Extraction order.** Unresolved. Three candidate models, none with direct
   empirical support (§3.1). Resolved by Step 3. The code is structured so that the
   answer is a table edit (§7.1).

2. **ehash record alignment for a 13-byte key.** Does a key at offset 8 spanning
   bytes 8..20 cross a DDR burst boundary? Measure at Step 5.

3. **PTYPE1 substitution value on non-IP frames.** Expected `0x00`, undocumented,
   unobserved. Guarded by §10.6 regardless. Confirm opportunistically during Step 3.

*Closed in v2.0:*

- ~~CC-hop clobbers the KG hash~~ Moot. F-044 and F-047 removed the hop (§5.1).
- ~~kgse_hc requires alignment~~ Nothing to align (§4.3).
- ~~Extraction order confirmed ascending~~ Overstated in v1.1; retracted (§3.1).

---

## 15. Summary of changes and their gates

**Immediate, no gate:**

| Change | Rationale |
|---|---|
| Revert F-046; restore `word0 = 0x40800000` | §5.4. Removed a bit set in the only HIT. |
| Delete the scaffold block | §5.6, §10.7. Wrong architecture, MURAM corruption vector. |
| Export `key_len` via debugfs; shell reads it | §10.3. Kills the stale `${#key}` class of bug. |
| Add `proto == 0` insert guard | §10.6. |
| Cold-boot protocol for all silicon tests | §10.9. |

**Gated on Step 3 (E2 workspace dump):**

| Change | Size |
|---|---|
| `fman_kg_order_v4[]` table, order confirmed | ~15 lines |
| Table-driven serialiser + `key_init()` cross-check | ~60 lines |
| `fman_pcd_key_selftest()` + debugfs node | ~120 lines |
| `key_verified` gate on `fe_arm_engage()` | ~10 lines |
| EKFC `0x00180006 -> 0x001C0006` | 1 line |
| keysize 12 -> 13; `key_len_v4` derived | 3 lines |
| `crc64()` keylen from `key_len_v4` | 1 line |
| kunit: three order params, golden vectors | ~60 lines |
| Tracepoints replacing 0148 `pr_info` | ~30 lines |
| `__free(fman_muram)` + `guard(mutex)` conversion | ~40 lines |

**Total: ~340 lines**, of which ~180 is verification and defensive scaffolding that
pays for itself the first time an assumption about silicon turns out to be wrong,
which on this program has been every time.

---

**End of v2.0, 2026-07-10.**

The single most important sentence in this document: *the extraction order has never
been observed, and no code should be written that cannot survive being wrong about
it.* §7 is that code.
