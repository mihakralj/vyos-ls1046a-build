# ASK2 NAT / PAT Hardware Offload — Task Plan (T-M6-7)

Status: planning (no code yet). Branch `dpaa1`. S0 QDRANT gate: DONE
(2026-08-22). This plan decomposes the M6-B NAT/PAT compiler into staged,
independently-validatable tasks with explicit silicon gates and a strict
software-fallback safety contract.

## 1. Goal and non-goals

Goal: hardware-offload routed IPv4 (then IPv6) unicast flows that also require
**NAT** — SNAT, DNAT, and NAPT/PAT (port translation) — through the FMan FE-VM
ehash HIT path, so the CPU is bypassed for NAT'd flows exactly as it already is
for plain routed flows.

Non-goals (out of scope for T-M6-7): hairpin/twice-NAT, NAT64/46, NAT of
fragments, ALG/helper-assisted flows (FTP/SIP/etc. stay in software), and any
NAT on `eth0` (management lifeline — never offloaded).

## 2. Where NAT sits in the existing datapath

The shipped routed HIT record already emits an FE opcode chain per flow:

```
UPDATE_TTL(0x21) / UPDATE_HOPLIMIT(0x29) -> INSERT_L2_HDR(0x41) -> ENQUEUE_PKT(0x01)
```

NAT extends this chain with in-place L3/L4 field rewrites **before**
`INSERT_L2_HDR`, using additional FE-VM opcodes the silicon already defines but
that this project has never exercised:

| Opcode | Value | Rewrite | Param |
|---|---|---|---|
| `UPDATE_SIP_V4` | `0x22` | IPv4 source addr | `u32` (4 B) |
| `UPDATE_DIP_V4` | `0x24` | IPv4 dest addr | `u32` (4 B) |
| `UPDATE_SIP_V6` | `0x2A` | IPv6 source addr | `u8[16]` (16 B) |
| `UPDATE_DIP_V6` | `0x2C` | IPv6 dest addr | `u8[16]` (16 B) |
| `UPDATE_SPORT` | `0x31` | L4 source port | shared `{u16 dport; u16 sport}` (4 B) |
| `UPDATE_DPORT` | `0x32` | L4 dest port | (same shared struct) |

The silicon **auto-recomputes** the IP and L4 checksums as a side effect of
these opcodes; there is no separate checksum opcode. Vendor param structs pack
sequentially in opcode-emission order (no per-opcode offset fields), so order
is mandatory. The `en_ehash_update_port` struct is `{dport, sport}` — **dport
first** — getting this backwards silently misforwards.

Important: `arch/fman-microcode-210-programming-reference.md` §8.3 documents a
*different* mechanism (the FMan-Controller HMCD chain, opcodes `0x0C`/`0x0E`).
ASK2 does **not** use the HMCD path; it uses the FE-VM ehash opcode list above.
§8.3 must be corrected/split during T-M6-7 to avoid misleading a future author.

## 3. How NAT arrives from Linux

There is **no dedicated NAT action** in `FLOW_CLS_REPLACE`. NAT is a sequence of
generic `FLOW_ACTION_MANGLE` entries plus `FLOW_ACTION_CSUM`, emitted by
`nf_flow_rule_route_ipv4/6`. Each mangle is `{htype, offset, mask, val}` where
`htype` is `IP4/IP6/TCP/UDP`, `mask` is inverted (bits to keep), `val` is
network byte order.

- SNAT: `htype IP4, offset=offsetof(iphdr,saddr)=12`, value = translated source.
- DNAT: `htype IP4, offset=daddr=16`, value = translated dest.
- NAPT: `htype TCP/UDP, offset=0`; upper-half mask = source port, lower-half
  mask = dest port.

The authoritative translated values are also available from the conntrack reply
tuple, which `ask.ko` can already reach via its existing
`ask_z11_other_src_v4/v6()` `container_of` helpers:
- SNAT translated src = `tuplehash[REPLY].dst`; DNAT translated dst =
  `tuplehash[REPLY].src`; translated ports from `tuplehash[REPLY]`.

**Chosen strategy (hybrid):** detect NAT *presence and kind* from the mangle
entries (portable, no struct-layout assumption), but read the *values* from the
reply tuple (avoids the 32-bit-chunk mangle-reassembly hazard that sfc/cxgb4
document). Reference decoders to mirror: MediaTek `mtk_ppe_offload.c`
(SoC-router HNAPT, closest analogue) and Broadcom `bnxt_tc.c`
(`bnxt_tc_parse_pedit` — clean NAT-flag model; also rejects L4-only NAT without
L3, a rule we adopt).

## 4. Record-budget check (already verified)

320-byte record, 46-byte dual key, opcode list @56, params @72. Worst case:
- IPv4 full NAT params: DSCP(4)+PORT(4)+SIP(4)+DIP(4)+L2HDR(20)+ENQ(16)+ctx(8)
  = 60 B → ends ~132.
- IPv6 full NAT params: SIP(16)+DIP(16)+... = 84 B → ends ~156.
Both fit within 320; neither collides with the stats block at +256/+264/+272.
(The `F_198`/`F_200` docstrings still show stale `key_size=14` offset examples;
re-annotate when the NAT fixup lands.)

## 5. Staged task breakdown

Each stage is independently committable and CI-buildable. Silicon stages are
gated (cold-boot, one-variable, eth3 sacrificial, a few packets never a flood).
Keep the A2 strict `-EOPNOTSUPP` software fallback as the shipping default until
the corresponding silicon stage passes; advertise the NAT capability LAST.

### T-M6-7.0 — Host-side intent plumbing (no silicon, unit-testable)
- Extend `enum ask_action_type` (NAT_SRC/DST, NAPT_SPORT/DPORT already reserved)
  and give the action entry a value union `{__be32 v4; struct in6_addr v6;
  __be16 port;}`.
- Add NAT fields to `struct ask_flow_key` (`nat_flags`, `nat_src/dst`,
  `nat_sport/dport`) so DESTROY/rebuild round-trips them (rht keys on cookie
  only, so widening the key is safe).
- Rewrite `ask_parse_action()` to decode NAT mangles into typed actions (replace
  the current `-EOPNOTSUPP` reject), using the hybrid strategy in §3.
- Extend `ask_intent_lower()` to set `ASK_ACT_NAT_*` + values.
- KUnit: SNAT/DNAT/NAPT parse vectors, plus the "reject L4-only NAT without L3"
  rule and IPv6 4-chunk address reassembly.
- **`ask_hw_flow_preflight()` keeps rejecting NAT bits** — nothing published to
  silicon yet. This stage is pure parse/carry; flows still fall back to SW.

### T-M6-7.1 — FE record NAT opcode emitter (kernel fixup, gated OFF)
- New count-gated fixup (after F-198/F-200/F-226, which share the
  `eth_type==0x0800` anchor block in `fman_pcd_ehash_add_key` — order after and
  re-anchor carefully).
- Extend `struct fman_pcd_fe_flow_action` with `nat_*` fields.
- Emit ports (`0x31`/`0x32` + `{dport,sport}`) then TTL+SIP+DIP before
  `INSERT_L2_HDR`, recomputing `enqueue_off` after the NAT params (drift-
  sensitive, mirror F-200/F-226).
- §17 tripwires: static asserts for the NAT param offsets/sizes; KUnit record
  byte-layout vectors; `fe_verify` MURAM readback NAT cases.
- Gate behind a default-OFF module param (e.g. `ask.nat_offload`) so v4/v6
  routed byte-identity is preserved until proven.

### T-M6-7.2 — S0 silicon read-only (no traffic)
- Cold boot; insert one NAT record on eth3; `fe_verify` MURAM readback proves
  the record bytes match the vendor param structs. No frames. Confirms encoding
  before any packet touches it.

### T-M6-7.3 — S1 port-only PAT (single flow, single packet)
- Smallest datapath change: a flow with only `UPDATE_DPORT`. Capture on the peer
  that dport is rewritten and the L4 checksum is valid. Isolates the fused-
  opcode encoding + `{dport,sport}` order + auto-checksum questions.

### T-M6-7.4 — S2 single-address DNAT
- Add `UPDATE_DIP_V4` only; capture dst-IP rewrite + IP and L4 checksum
  correctness.

### T-M6-7.5 — S3 full SNAT+DNAT+PAT matrix
- Both directions, TCP and UDP, packet capture both sides, conntrack tuple +
  counter cross-check, checksum validation, flow-expiry restores SW, 10k
  create/delete churn, and an explicit no-wrong-flow-forwarding check.

### T-M6-7.6 — IPv6 NAT
- Repeat the emitter + S-stages for `UPDATE_SIP_V6(0x2A)`/`UPDATE_DIP_V6(0x2C)`
  on the dual-lane path.

### T-M6-7.7 — VyOS integration + capability advertise
- Confirm VyOS `nat`/`nat66` config drives the flowtable NAT actions to ask.ko
  (nf_flow_table already emits them when conntrack has `IPS_*_NAT`).
- `show offload flow` already renders NAT'd flows by their original 5-tuple key;
  consider surfacing the translation. Advertise the NAT capability in
  `get-info` only after all gates pass.

## 6. Silicon unknowns (must be resolved empirically, in order)

1. `UPDATE_SIP/DIP/SPORT/DPORT` (`0x22/0x24/0x31/0x32`) have never been
   exercised on this 210.10.1 microcode (only `0x21/0x29/0x41/0x01` are proven).
2. Auto-checksum behavior (L3 + L4, and the "skip if L4 checksum originally 0"
   UDP rule) is assumed from RM/vendor, not measured.
3. **Fused-opcode OR encoding** — the vendor ORs `UPDATE_SPORT|UPDATE_DPORT`
   into one opcode byte and `UPDATE_TTL|UPDATE_SIP|UPDATE_DIP` into another.
   Whether the 210.10.1 FE-VM decodes OR'd opcode bytes as intended (vs.
   requiring separate slots) is the single riskiest assumption — S1 tests it.
4. `en_ehash_update_port` `{dport, sport}` byte order.
5. DF-bit / IP-ID handling under NAT.

## 7. Safety contract (binding)

- The kernel/conntrack is the source of truth; hardware is a disposable cache.
- Until each silicon stage passes, NAT flows return `-EOPNOTSUPP` and fall back
  to software forwarding — never a silent no-op rewrite (which would misforward).
- Never offload NAT on `eth0`.
- Every FE register/record write is read back before the flow is published.
- Cold-boot before each silicon NAT experiment (warm reboot does not clear
  BMI/MURAM); reproduce with a few packets, never a flood (BUG-3b watchdog
  hazard).
- One variable per silicon experiment.
- Advertise the NAT capability only after forward + inverse + readback +
  fallback gates all pass.

## 8. First actionable step

Implement **T-M6-7.0** (host-side parse/carry, unit-testable, no silicon, no
datapath change — flows still fall back to SW). This is the safe foundation and
mirrors the proven staged approach used for the L2/TTL terminal (F-198/F-200)
and the IPv6 dual-lane key (Phase A).
