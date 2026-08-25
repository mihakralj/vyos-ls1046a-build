# ASK2 VLAN HW Offload Plan (T-M6-8)

Status: planning only — no code. S0 QDRANT gate satisfied 2026-08-24 (FMan HM/FE
VLAN opcodes cross-checked against `arch/fman-microcode-210-programming-reference.md`,
`specs/fman-keygen-flow-key-spec.md`, vendor `we-are-mono/ASK cdx/cdx_ehash.c` +
`ncsw .../fm_ehash.h`, and kernel 6.18.44 `nf_flow_table_offload.c`).

This plan mirrors the proven, safe progression used for NAT (T-M6-7): host-side
typed intent with strict `-EOPNOTSUPP` fallback → dormant gated FE emitter →
S0 readback → single-packet capture → matrix → productization. VLAN is the
smallest extension of the already-silicon-validated routed/NAT L2-rewrite path
because it reuses the same FE record builder, per-port ehash tables, TX terminal,
and flowtable control path. It does not need a new accelerator or control-plane
subsystem.

---

## 1. Scope

### In scope (first release)
- Single 802.1Q tag (TPID `0x8100`).
- Ingress VLAN **POP** (tagged in → routed untagged out).
- Egress VLAN **PUSH** (untagged in → tagged out).
- VLAN **translation** (VID X → VID Y), expressed by Linux as POP + PUSH.
- Composition with the existing routed IPv4/IPv6 and NAT44/NAT66 chains.
- Routed flows only (the flowtable path), reusing the L3/L4 46-byte key unchanged.

### Deferred / must fail closed initially
- 802.1ad S-tag (`0x88A8`) — vendor egress build hardcodes `0x8100`; the kernel
  flowtable path never emits `ETH_P_8021AD`. Reject with `-EOPNOTSUPP`.
- QinQ / stacked tags (kernel cap is `NF_FLOW_TABLE_ENCAP_MAX = 2`; vendor
  validate cap is `MAX_VLAN_PER_FLOW = 2`). Start single-tag; add depth-2 only
  after single-tag is silicon-proven.
- PCP/DEI transparency. The kernel `struct flow_action_entry.vlan` carries only
  `{vid, proto, prio}` — **no DEI field**, and the flowtable/routed producer
  never sets `prio` (so PCP arrives as 0). DEI/PCP are therefore NOT preservable
  from the routed flow-action input. Ship VID-only; do not advertise PCP/DEI
  transparency. (tc-flower `FLOW_ACTION_VLAN_MANGLE` can carry `prio`, but the
  flowtable path never emits MANGLE — do not rely on it.)
- Bridge VLAN filtering, VLAN-aware multicast, PPPoE-over-VLAN, tunnel-over-VLAN.
- VLAN in the classification key (`KG_SCH_KN_TCI1/TCI2`, bits 28/27) — per
  `specs/fman-keygen-flow-key-spec.md §4.4`, VLAN TCI is deliberately excluded
  from the key; flows are classified at L3/L4. VLAN is an **action**, not a key
  field. This plan does not change the key.

---

## 2. Authoritative vendor encoding (S0 evidence)

FE-VM opcodes are 1-byte values written into the record's opcode-list region;
their parameter blobs are packed **sequentially in opcode-emission order** (no
per-opcode offset fields — order is mandatory). This is the same record the
routed/NAT chain already builds (`fman_pcd_ehash_add_key`, `FMAN_EHASH_FLOW_REC_SIZE=320`).

Two opcodes, both confirmed in vendor `fm_ehash.h` + `cdx_ehash.c`:

- **POP — `STRIP_ALL_VLAN_HDRS = 0x12`.** Removes ALL stacked ingress VLAN
  headers in one opcode and validates up to `MAX_VLAN_PER_FLOW=2` expected VIDs.
  Param `struct en_ehash_strip_all_vlan_hdrs` (BE, packed): `u16 vlan_id[2]`
  (outer first), a validating `u32 word` (0 when ifstats disabled), `u8 op_flags`
  (`OP_SKIP_VLAN_VALIDATE=1<<0`, `OP_VLAN_FILTER_EN=1<<1`, `OP_VLAN_FILTER_PVID_SET=1<<2`),
  `u8 pad`, `u16 pad1`. Base 12 bytes; emitter `insert_remove_vlan_hm()`.

- **PUSH — `INSERT_VLAN_HDR = 0x42`** (a DEDICATED opcode; NOT `INSERT_L2_HDR`).
  Param `struct en_ehash_insert_vlan_hdr` (BE, packed): control `u32 word` =
  `(dscp_vlanpcp_map_enable?1<<30:0) | (num_hdrs<<24) | statptr(24)`, followed by
  `u32 vlanhdr[num_hdrs]`, each `= cpu_to_be32((tci<<16) | next_ethertype)`. Tags
  are written inner→outer reversed. The TPID of each tag is transported as the
  EtherType field of the preceding word; the outermost TPID becomes the
  `INSERT_L2_HDR` EtherType (vendor rolls `info->eth_type` to `0x8100` when tags
  are present). Emitter `create_vlan_ins_hm()`.

Vendor plain-forward chain order (`fill_actions()`), VLAN positions in bold:

```
PREEMPTIVE_CHECKS_ON_PKT(0x05)
  → [STRIP_ETH_HDR(0x11)  — only on L2-rebuild path]
  → **STRIP_ALL_VLAN_HDRS(0x12)  — VLAN POP / ingress strip**
  → [NAT fused UPDATE_SPORT/DPORT/SIP/DIP] or [UPDATE_TTL(0x21) / UPDATE_HOPLIMIT(0x29)]
  → **INSERT_VLAN_HDR(0x42)  — VLAN PUSH / egress tags**
  → INSERT_L2_HDR(0x41)  — Ethernet rebuild (dst/src MAC + EtherType)
  → ENQUEUE_PKT(0x01)
```

So in the current ASK2 record the VLAN emitter sits: POP **before** the
TTL/NAT updates region, PUSH **immediately before** `INSERT_L2_HDR`. When a tag
is pushed, the `INSERT_L2_HDR` EtherType must become the outer TPID (`0x8100`)
and the pushed `vlanhdr[]` word's low 16 bits carry the inner EtherType
(`0x0800`/`0x86dd`).

### Known silicon risks (fail-closed until measured)
- **`hdr_xpnd_sz` does not account for VLAN growth** — vendor sets it from
  `tnl_hdr_size` only. VLAN push adds 4 bytes/tag on the wire. Whether the
  microcode independently reserves headroom for inserted VLAN bytes in the
  fragmentation path is unverified. Guard MTU accordingly; measure before trust.
- **Fused/dedicated opcode `0x42`/`0x12` never exercised on 210.10.1** — only
  `0x21/0x29/0x41/0x01` (routed) and `0x22/0x24/0x31/0x32` (NAT, now shipped)
  are silicon-proven. `0x12`/`0x42` are new opcodes for this project.
- **`[TCI|EtherType]` word packing + TPID transport** is inferred from the
  emitter, not microcode disassembly. S0 readback + single-packet capture must
  confirm the on-wire tag byte layout.
- **PREEMPTIVE_CHECKS / STRIP_ETH_HDR** are still not emitted by the current
  ASK2 record (deferred since T-M7-2 S2). STRIP_ETH_HDR is only needed on the
  L2-rebuild path; the current `INSERT_L2_HDR`-alone approach already rewrites
  the 14-byte L2 header, so pop+push compose on top of it without STRIP_ETH_HDR
  for the single-tag case. Confirm this holds on silicon; if the pushed tag
  requires STRIP_ETH_HDR first, add it in the same emitter (still no new subsystem).

---

## 3. Linux flow-action contract (kernel 6.18.44)

From `nf_flow_rule_route_common()` the routed emission order is fixed:

```
eth_src(MANGLE×2) → eth_dst(MANGLE×2) → [VLAN_POP…] → [VLAN_PUSH…]
  → [SNAT/DNAT MANGLE + CSUM] → REDIRECT
```

- `FLOW_ACTION_VLAN_POP` (id) carries **no fields**; skipped when the kernel
  marks the tag ingress-stripped (`in_vlan_ingress` bit) — ASK2 must not re-pop.
- `FLOW_ACTION_VLAN_PUSH` carries `vlan.vid` (host order, 0–4095),
  `vlan.proto` (`__be16` TPID), `vlan.prio` (host order, but **0** from the
  flowtable path).
- Translation = POP + PUSH (never a single MANGLE on the flowtable path).
- QinQ = two POP or two PUSH; depth >2 is not offloadable by the kernel path.
- The eth dst/src MANGLEs already drive the `INSERT_L2_HDR` rewrite ASK2 does;
  VLAN composes on top. `ask_parse_match_v4/v6` already read the VLAN match key
  into `key->vlan_id` (currently unused) — reuse that plumbing.

Reference in-tree validators to mirror: sfc `efx_tc_flower_action_order_ok()`
(action ordering + ≤2 caps), cxgb4 split preflight/program pass, sparx5
mutual-exclusion preflight, mtk_ppe SoC-router reject rules.

---

## 4. Current code state (implementation map)

Kernel FE side is the ordered fixup stack + canonical tree; OOT module under
`kernel/ask/oot-modules/ask/`.

- `ask_flow_offload.c:1459-1462` — `FLOW_ACTION_VLAN_PUSH/POP` currently return
  `-EOPNOTSUPP` (correct fail-closed). `FLOW_ACTION_VLAN_MANGLE` hits `default`.
  `ask_parse_match_v4/v6` (`:1097`, `:1156`) already populate `key->vlan_id`.
- `include/ask_internal.h` — `u16 vlan_id` (`:482`) reserved; legacy flag bits
  `ASK_ACT_VLAN_PUSH (1<<4)` / `ASK_ACT_VLAN_POP (1<<5)` (`:1072-1073`); no typed
  VLAN intent yet. `enum ask_action_type` (`:550`) + `ask_flow_action_ent`
  (`:571`) + `ask_intent_add_nat()` (`:613`) are the NAT precedent to copy.
- `ask_hw.c:1163-1165` — VLAN flags rejected unconditionally. NAT gate pattern
  to mirror: `ask_nat44_offload`/`ask_hw_nat44_offload_armed()` (`:125`),
  family + eth0-exclusion admission (`:1166`, `:1188`), `ask_intent_lower()`
  gate (`ask_flow_offload.c:1193`), armed-only field copy in `ask_fe_flow_insert`
  (`:1827`).
- FE compiler: `fman_pcd_ehash_add_key()` (canonical `fman_pcd.c:2205`); TX
  branch emits `UPDATE_TTL/HOPLIMIT` → (F-230 NAT, dormant) → `INSERT_L2_HDR` →
  `ENQUEUE_PKT` with dynamic offsets, all params packed in emission order.
  `struct fman_pcd_fe_flow_action` (`fman_pcd.h:330`) carries key/L2/eth_type/NAT
  fields; F-204 shows the additive-field pattern, F-230 the params-struct pattern.
- Tripwires: static asserts `kernel/common/files/fman-pcd-fe-static-asserts.h`
  (F-089); KUnit `kernel/common/files/fman_pcd_fe_test.c` (F-089); arm-time
  `fe_verify` (F-097); OOT KUnit `tests/ask_test_flow_offload.c` (VLAN-rejected
  case at `:448`, to be flipped).
- Capability: `ASK_CAP_VLAN (1<<6)` defined (`uapi/.../ask.h:206`) but NOT
  advertised (`ask_genl.c:255`); `ask_test_genl.c:197` pins the shipping bitmap
  (update when advertising).

---

## 5. Staged implementation (mirrors NAT T-M6-7)

### T-M6-8.0 — Host-side typed intent + strict fallback (no silicon change)
- Add `ASK_ACTION_VLAN_POP` / `ASK_ACTION_VLAN_PUSH` typed enum values and carry
  fields (`vlan_tci`/`vlan_proto`/`vlan_flags`; reuse `vlan_id`) in
  `ask_flow_action_ent` / `ask_flow_key`, mirroring `ask_intent_add_nat()`.
- Decode `FLOW_ACTION_VLAN_POP` (no fields) and `FLOW_ACTION_VLAN_PUSH`
  (`vid`/`proto`/`prio`) in `ask_parse_action` into typed intent.
- Preflight stays fail-closed: keep returning `-EOPNOTSUPP` for VLAN until the
  FE emitter is silicon-validated (like NAT A2). Enforce the closed set:
  reject `proto != ETH_P_8021Q`, reject depth >1 (initially), respect
  `in_vlan_ingress` (don't re-pop), reject PUSH+POP/PUSH+MANGLE conflicts.
- KUnit: VID parse correctness, 802.1ad reject, depth>1 reject, ingress-stripped
  no-pop, no-publish-on-reject. Flip `ask_test_flow_offload.c:448` accordingly.
- Gate: on-board, confirm tagged/translated flows forward correctly **in
  software** with NO `in_hw` record (no misforward, no silent no-op).

### T-M6-8.1 — Dormant FE emitter (default-off)
- New kernel fixup ordered **after F-230**, re-anchoring on the `INSERT_L2_HDR`
  line in `fman_pcd_ehash_add_key`, count-gated + idempotent. Emit:
  - `STRIP_ALL_VLAN_HDRS(0x12)` before the TTL/NAT update region (POP), and
  - `INSERT_VLAN_HDR(0x42)` immediately before `INSERT_L2_HDR` (PUSH), rolling
    the `INSERT_L2_HDR` EtherType to `0x8100` and setting the pushed word's low
    half to the inner EtherType.
- Add VLAN fields to `struct fman_pcd_fe_flow_action` (+ a `fman_pcd_vlan_params`
  mirror like `fman_pcd_nat_params`). Recompute enqueue/param offsets after the
  VLAN params (drift-sensitive; same discipline as F-200/F-226/F-230).
- Module-param gate `ask_vlan_offload` + `ask_hw_vlan_offload_armed()`; admit
  `ASK_ACT_VLAN_*` in preflight only when armed; copy fields only when armed.
- Verify record budget: single-tag POP(12) + PUSH(control4 + 4/tag) fits well
  inside 320 even composed with v6-NAT (current worst-case ends ~156B).
- Extend all three tripwires: static-assert the `0x12`/`0x42` opcode bytes and
  param sizes; KUnit encoder vectors for pop/push/translate; fe_verify if the
  descriptor words change.

### T-M6-8.2 — Silicon gates (cold-boot, one variable, eth3 sacrificial, pings not floods)
1. **S0 readback:** arm one VLAN record, no traffic; read back the opcode list +
   params via debugfs/`/dev/mem`; confirm `0x12`/`0x42` bytes and param layout.
2. **S1 ingress POP:** tagged frame in → untagged routed frame out (capture at
   sink confirms tag removed, L3/L4 intact, TTL decremented).
3. **S2 egress PUSH:** untagged in → correctly tagged out (capture confirms TPID
   `0x8100`, correct VID, inner EtherType, MAC rewrite intact).
4. **S3 translation:** VID X → VID Y (POP+PUSH), capture confirms new tag.
5. **S4 matrix:** IPv4+IPv6 × TCP+UDP; VLAN+NAT44; VLAN+NAT66; MTU battery
   (respect the 1280–2500 clamp and the `hdr_xpnd_sz` risk — verify no
   oversize/fragmentation fault when pushing a tag near MTU); flow expiry →
   SW restore; 10,000-flow churn MURAM-stable; unsupported (802.1ad, depth>2)
   stays SW; **no wrong-flow forwarding**; IPv4/IPv6 routed + NAT regression
   (must not regress the ~10 Gbit/s path).

### T-M6-8.3 — Productization
- Advertise `ASK_CAP_VLAN` in `ask_genl.c`; fix `ask_test_genl.c` expectation.
- Add `vlan` label to `show offload config` (vyos-1x-040 style).
- Decide default-on vs runtime-gated (`vlan_offload`) per NAT precedent; document
  eth0 exclusion and per-family behavior.
- Reconcile `plans/ASK2-MASTER-PLAN.md` T-M6-8 row and capability matrix.

---

## 6. Binding rules (from AGENTS.md / master plan §4.6)

- NEVER accept `FLOW_ACTION_VLAN_PUSH/POP` as a no-op — until the FE rewrite is
  implemented AND silicon-tested, return `-EOPNOTSUPP` (fail closed; the previous
  silent no-op only set ignored `action_flags` bits and would misforward).
- Do not advertise `ASK_CAP_VLAN` before the forward + readback + fallback gates
  pass on silicon.
- One opcode/variable per cold-boot experiment; eth3 sacrificial, never eth0;
  pings first, never a flood before the terminal is proven (BUG-3b discipline).
- Do not add VLAN to the classification key; VLAN is an action, key stays the
  46-byte dual-lane L3/L4 key.
- New emitter is a fixup ordered after F-230 with a count-gated, idempotent,
  carefully re-anchored edit; do not hand-edit generated patch hunks.

---

## 7. Why VLAN is the right next capability
- Reuses the proven FE record builder, per-port ehash tables, TX terminal, and
  flowtable control path — no new accelerator or control-plane subsystem.
- Linux already supplies `FLOW_ACTION_VLAN_PUSH/POP`; vendor supplies exact
  opcodes (`0x12`/`0x42`). The unknowns are narrow silicon confirmations.
- Materially smaller than PPPoE (needs the soft-parser compiler/loader), IPsec
  (XFRM + CAAM + SA lifecycle), or bridge/multicast (switchdev FDB/MDB +
  replication).
