# ASK2 VLAN re-architecture — off the FE-VM inline opcodes, onto the HMTD facility

**2026-08-26 · dpaa1 · T-M6-8 · Supersedes the inline-FE-VM-opcode VLAN approach
(F-233/F-234) which is silicon-proven dead.**

## 1. Why the current architecture is dead (established, not re-litigated)

ASK2's VLAN offload emitted the vendor's VLAN header-manip as **inline FE-VM
opcodes** in the ehash flow record: `STRIP_ETH(0x11) → STRIP_ALL_VLAN(0x12) →
UPDATE_TTL(0x21) → [INSERT_VLAN(0x42)] → INSERT_L2(0x41) → ENQUEUE(0x01)`.

Every host-side and encoding hypothesis was byte-matched to the vendor and
silicon-tested. The record is correct; it **freezes after exactly 5+tnums = 21
packets** (a per-task FE-VM management-index resource the VLAN strip/rebuild
path consumes and never releases). Two complementary microcode oracle patches
(qef-patch→kexec) on the action-interpreter epilogue **both failed**. The plain
routed record on the same silicon sustains 7–10 Gbit/s. The vendor's *deployed*
`.110` stack sustains VLAN on the same silicon, but its live record could not be
read (`CONFIG_DEVMEM=n`).

**Decisive microcode fact (`decomp/en-exthash-lookup.asm`):** the ehash HIT path
ends at `execute_fe_actions(record + opcode_offset, record + param_offset)` — the
inline opcode list, *and nothing else*. **An ehash flow record has no HMTD/NADEN
chaining field.** So the fix cannot be "make the ehash record point at an HMTD";
it must move the VLAN edit off the FE-VM inline path entirely.

## 2. The proven primitive we pivot to: the FMan HMCD/HMTD engine

The FMan Controller **Header-Manipulation** facility (RM Ch.5 / 8.7.5) is a
*separate* engine from the FE-VM: an **HMTD** (16-byte MURAM descriptor,
`cfg=TYPE|EXT_HMCT`, `hmcdBasePtr`, `opCode=HMAN_OC 0x35`) points at an **HMCT**
command chain (generic INSRT/RMV command words, `HMCD_LAST` terminator), with
optional **PAHM (parse-after-HM)** so the parser re-establishes offsets after the
edit. This is exactly what the inline FE-VM path lacked (the earlier `0x8020`
EXTRACTION|PRS_HDR_ERR was the parser seeing a stale parse result on the rebuilt
frame).

**This infrastructure is already built, in-tree, and partly silicon-proven:**
- `fman_pcd_manip.c` (board patch **0099**): `fman_pcd_hm_install(pcd, port_id,
  spec, &handle)` allocates HMTD+HMCT MURAM, encodes ops, returns the HMTD MURAM
  offset. Encoders exist for **`hm_encode_vlan_strip`** (generic RMV @off 12,
  size 4) and **`hm_encode_vlan_insert`** (generic INSRT @off 12 + inline
  TPID/PCP/VID). `fman_pcd_hm_destroy()` tears it down.
- `struct fman_hm_spec {num_ops; ops[8]}` with `FMAN_HM_OP_VLAN_STRIP` /
  `VLAN_INSERT` / L3-forward ops (0090a/0119); `fman_hm_node_install/destroy`
  caps-gated bridge (`FMAN_CAP_HM_NODES`, caps 0x17 on this board).
- **Board patch 0101 = silicon-proven RX VLAN strip:** a single-op VLAN_STRIP
  HMTD installed on the RX port via `NETIF_F_HW_VLAN_CTAG_RX` /
  `ethtool -K ethX rxvlan on`. It works today on 210.10.1.
- **NADEN CC→HMTD chaining is HW-proven:** `cc_write_leaf_ad` (0108/0115/0116)
  encodes `word1 = hm>>4`, `word2 NIA |= NADEN(0x20000000)|EXTENDED(0x80000000)`
  so a CC leaf enqueue walks the HMTD before enqueueing to `target_fqid`. ask20
  silicon captured 24M+ frames through exactly this RESULT_CF|NADEN + HMTD
  encoding (PR14z20/PR14z22).
- `fman_hm_nexthop_get/put` (0120) already shares one HMTD per L3 adjacency
  (refcounted) to keep MURAM O(next-hops) not O(flows).

So both hard pieces — the HMTD encoder and the NADEN chaining AD — exist and are
silicon-validated. The re-architecture is composition, not new silicon research.

## 3. Options considered

| Opt | Mechanism | VLAN edit runs on | New silicon risk | Verdict |
|---|---|---|---|---|
| **A** | **CC leaf AD + NADEN → VLAN HMTD**, VLAN flows classify via CC-tree, routed/NAT stay on ehash | FMan HM engine (post-CC) | LOW — NADEN+HMTD both HW-proven; RX-strip HMTD proven | **RECOMMENDED** |
| B | Port-level RX HW strip (0101) + TX insert, keep plain routed ehash record | MAC/BMI RX+TX | MED — blanket per-port strip is wrong on mixed/multi-VLAN trunks; per-flow TX insert unproven | Rejected (not general) |
| C | Keep inline FE-VM opcodes, fix the 5+tnums release | FE-VM | — proven dead (2 oracle patches failed) | Rejected (user: rearchitect) |
| D | ehash ENQUEUE → OH (offline) port whose PCD has the VLAN HMTD, re-enqueue to TX FQ | FMan HM engine (OH port) | MED-HIGH — OH-port PCD + re-enqueue plumbing is a new subsystem | Fallback if A blocked |

### Why A over D
Both use the proven HMTD engine and both structurally avoid the FE-VM freeze
(no inline VLAN opcodes execute). A reuses the **already-in-tree, 24M-frame-proven
NADEN CC-leaf-AD** and needs no offline-port PCD. D matches the vendor's OH-port
pattern (serdes-ethernet.md: OP1/OP2 are reinject/replicate targets) and will be
needed anyway for multicast replication and IPsec reinject — but it's a larger
first build. Start with A; keep D as the documented fallback and the eventual
shared primitive for multicast/IPsec.

## 4. Recommended architecture (Option A)

**Principle:** a VLAN flow is a *plain routed flow whose CC leaf action chains an
HMTD that does the tag edit*. The FE-VM never executes a VLAN opcode.

```
ingress → Parser → KeyGen → CC-tree (VLAN flows)                routed/NAT flows → ehash (unchanged)
                              │  leaf AD HIT (5-tuple match)
                              │  word0 = target_fqid
                              │  word1 = vlan_hmtd >> 4
                              │  word2 = BMI ENQ | NO_OM_VSPE | NADEN | EXTENDED
                              ▼
                        HM engine walks HMTD:
                          POP  = {VLAN_STRIP}                 (generic RMV @12/4)
                          PUSH = {VLAN_INSERT(vid,tpid,pcp)}  (generic INSRT @12/4)
                          (PAHM reparses)
                              ▼
                        enqueue → per-egress no-confirm TX FQ  (unchanged)
```

- **Classification:** VLAN flows go on a **CC-tree leaf** (5-tuple key, same key
  material as ehash), not the ehash record. Routed/NAT unicast stay on ehash
  untouched — zero regression to the shipped 10G path.
- **Edit:** POP = a 1-op VLAN_STRIP HMTD; PUSH = a 1-op VLAN_INSERT HMTD carrying
  (vid, tpid=0x8100, pcp). Built via the existing `fman_pcd_hm_install`.
- **Chaining:** the CC leaf AD carries `hm_handle = HMTD_off` and sets NADEN so
  the HM runs before the enqueue to `target_fqid` (the resolved egress TX FQ).
- **Dedup:** share one HMTD per (direction, VID, tpid, egress) via a refcounted
  cache mirroring `fman_hm_nexthop_get/put` (0120), so MURAM is O(VLANs) not
  O(flows).
- **PAHM:** enable parse-after-HM on the HMTD so the rebuilt frame's parse result
  is fresh before enqueue (fixes the class of error the inline path hit).
- **Kernel authority:** unchanged — `nf_flow_table` FLOW_ACTION_VLAN_POP/PUSH is
  authoritative; ASK2 mirrors, fails closed to SW on any unsupported case.

### Lifecycle / safety (per master-plan §4.6.5)
- HMTD install is process-context sleepable; reserve MURAM in preflight; readback
  the HMTD+HMCT before marking `in_hw`; destroy on flow delete/flush/disengage
  with the same owner-generation rules; `pcd-snapshot` byte-clean after teardown.
- Fail closed to SW: 802.1ad, QinQ depth>1, PCP/DEI transparency, unsupported
  tag depth — return `-EOPNOTSUPP` (kernel keeps forwarding in SW).
- `ASK_CAP_VLAN` stays unadvertised until the full matrix passes on silicon.

## 5. Implementation increments (each build+silicon-gated)

- **R1 — retire the inline path.** Remove F-233 (inline VLAN opcode emitter) and
  F-234 (frag context) from the VLAN flow path. VLAN returns to `-EOPNOTSUPP`
  (fail-closed) so the tree is clean and routed/NAT are byte-identical. Gate:
  routed/NAT regression unchanged; VLAN traffic forwards in SW.
- **R2 — VLAN HMTD builder + dedup cache.** `fman_pcd_vlan_hm_get(pcd, port_id,
  {POP|PUSH, vid, tpid, pcp, egress_key}, &handle)` on top of
  `fman_pcd_hm_install`, refcounted like 0120. KUnit + dormant readback.
- **R3 — CC leaf-AD VLAN classification path.** Add a per-port CC node for VLAN
  flows; `ask.ko` inserts a VLAN flow as a CC key with `target_fqid` = egress TX
  FQ and `hm_handle` = the VLAN HMTD. Gate on a sacrificial port, cold-boot,
  single flow: prove a CC HIT + HMTD-edited frame on the wire, `pkt_count`
  climbs **past 21** and **sustains**.
- **R4 — ask.ko rewire.** VLAN intent → {resolve VID/TPID/egress → get HMTD →
  insert CC key}. POP and PUSH directions, vif resolution reused from the
  existing (correct) host-side work (6faab5aa/d976ea38 ingress-VID resolver).
- **R5 — matrix + productization.** untagged↔tagged, tagged↔tagged (POP+PUSH),
  PCP/DEI policy, MTU 1280–2500, checksum/L2, churn, +NAT, no-wrong-forward;
  then advertise `ASK_CAP_VLAN`, `show offload` label, default decision.

### De-risk experiments before R4 (cheap, on sacrificial port)
1. Install a VLAN_STRIP HMTD via the **proven 0101 path** (`ethtool -K ethX
   rxvlan on`) and confirm it still works on the current image — baseline that
   the HM engine is live.
2. Hand-arm (debugfs) a CC leaf with `hm_handle`=a VLAN_INSERT HMTD + NADEN on a
   sacrificial port, inject one matching flow, and confirm on-wire the tag is
   inserted AND `pkt_count` sustains past 21 — the single decisive proof that the
   CC+HMTD path avoids the FE-VM freeze. Only after this passes, write R3/R4.

## 6. What this fixes vs the old path
- **No FE-VM VLAN opcodes execute** → the 5+tnums management-index freeze cannot
  occur (it was specific to the FE-VM strip/rebuild handlers).
- **PAHM** re-establishes the parse result the inline rebuild corrupted.
- Uses **only HW-proven mechanisms** (RX-strip HMTD live; NADEN CC-AD 24M frames).
- Routed/NAT/IPv6 ehash path is **untouched** → zero regression risk to the
  shipped 10G production path.

## 7. Reusability (why this is the right infra investment)
The HMTD builder + NADEN chaining + (later) OH-port reinject are the **shared
header-manipulation substrate** the capability plan (`plans/
OFFLOAD-CAPABILITY-PLAN.md`) calls for: the same primitives serve IPsec ESP
encap, tunnels (encap/decap), PPPoE, and MPLS. Building VLAN on HMTD is the first
and smallest consumer of infrastructure every future encap capability needs.

## 7b. R4 production design — CORRECTED after R3b (2026-08-26)

R3b PASSED: a hand-armed CC leaf → NADEN → **VLAN_STRIP-only** HMTD sustained
273k pkts/5s (~54.6k pps), ErrFD=0, kernel vif RX flat — the FE-VM 5+tnums
freeze is gone and the CC+HMTD mechanism is silicon-validated. Two production
facts R3b surfaced that R4 MUST honour:

1. **A routed-VLAN flow needs a COMBINED HMTD, not VLAN-only.** The CC leaf
   enqueue does NOT rebuild L2 (verified: `cc_write_leaf_ad` word2 is just
   BMI ENQ|NADEN; the HM engine owns all header edits). A real routed-VLAN flow
   must, in ONE HMTD chain, do both the tag edit AND the L3 next-hop rewrite the
   ehash path's opcodes did:
   - **POP + route** (tagged ingress → untagged egress):
     `{VLAN_STRIP, RMV_ETHERNET, INSRT_GENERIC(dstMAC=nexthop, srcMAC=egress,
       ethertype), IPV4_FORWARD(dec_ttl, l4_csum)}`
   - **PUSH + route** (untagged ingress → tagged egress):
     `{RMV_ETHERNET, INSRT_GENERIC(new L2), IPV4_FORWARD, VLAN_INSERT(vid,tpid,pcp)}`
   i.e. R4 composes R2's VLAN op with the existing next-hop ops
   (`fman_hm_nexthop_get`'s `{RMV_ETHERNET, INSRT_GENERIC(14B L2), IPV4_FORWARD}`).
   The R2 `fman_hm_vlan_get` (VLAN-only) stays for a *bridged* (non-routed) VLAN
   flow; routed-VLAN uses the new combined builder.

2. **CC and ehash cannot both own a port's KG dispatch simultaneously.**
   `fman_pcd_kg_port_attach_cc` grafts the port's scheme to the CC tree
   (KGSE_CCBS → CC group); the production ehash path grafts the same scheme to
   RCCB→FE_ENTER. They are mutually exclusive per physical port. Coexistence
   options, ranked:
   - **(pref) Unified CC tree with FE fall-through:** RCCB → one CC tree per
     port; VLAN flows are CC keys (combined HMTD); the CC **miss** chains to the
     FE_ENTER ehash root (routed/NAT). The F-182 `fe_off` path in `cc_test`
     already proves a CC leaf can carry the FE_ENTER AD form, so miss→FE is
     reachable. Highest effort, cleanest.
   - **(interim) Per-port capability mutex:** a port doing VLAN offload uses the
     CC path for ALL its offloaded flows (VLAN + routed-via-CC), a port with no
     VLAN uses ehash. Simpler; VLAN implies that port's routed flows also move
     to CC (which R3b shows sustains). Acceptable first cut.

**CC add/del is whole-tree:** only `fman_pcd_cc_static_install` exists (atomic
rebuild), no per-flow dynamic CC add. So `ask.ko` keeps a per-port software
shadow of the CC key set and rebuilds the `fman_pcd_cc_hw_spec` on each VLAN
flow add/del. `FMAN_PCD_CC_HW_MAX_KEYS` bounds concurrent CC flows/port (fail
closed to SW past the cap). Static reinstall briefly rebuilds the live tree —
acceptable at modest VLAN flow counts; revisit if churn is high.

### R4 increments (staged, each build+silicon-gated, mirrors R2/R3)
- **R4a:** combined VLAN+route HMTD builder `fman_hm_vlan_route_get/put`
  (compose R2 VLAN op + next-hop L3/L2 ops; refcounted dedup by
  (vlan-op,vid,tpid,pcp,nexthop-adjacency,egress_fqid)). DORMANT.
- **R4b (de-risk):** extend `cc_test install_vlan` to arm the COMBINED HMTD
  (add nexthop dst/src MAC args) and verify on silicon a routed-VLAN flow
  forwards with correct L2 (dst=nexthop MAC at the sink) + TTL-decrement +
  sustains. This closes the R3b L2-rebuild gap before any ask.ko change.
- **R4c:** `ask.ko` production path — per-port CC shadow + rebuild; VLAN flow
  intent → resolve VID/TPID/egress+nexthop → `fman_hm_vlan_route_get` →
  add CC key (target_fqid + hm_handle) → `fman_pcd_cc_static_install` +
  `kg_port_attach_cc`; choose the coexistence model above. Reuse the correct
  vif/ingress-VID resolvers (6faab5aa/d976ea38).
- **R4d:** teardown/lifecycle (flow del → rebuild tree, put HMTD; disengage →
  detach CC, restore ehash/RSS; pcd-snapshot byte-clean).

## 7c. R4c staging + the coexistence de-risk gate (2026-08-26, after R4b PASS)

R4b PASSED end-to-end: the combined routed-VLAN HMTD (VLAN_STRIP + RMV_ETHERNET
+ INSRT_GENERIC(L2) + IPV4_FORWARD) behind a CC leaf forwarded frames that
arrived at the sink with the **correct next-hop dst MAC, egress src MAC, VLAN
stripped, and TTL decremented (64→63)**, sustaining ~55k pps, kernel vif RX = 0,
Err FD = 0. The full production header edit works in the HM engine.

**But R4b (like R3b) ran with ASK ehash DISENGAGED** so the CC tree was the sole
KG dispatch on the port. Production is different: a real VLAN-routing box
(tagged `eth3.100` ↔ untagged `eth4`) carries BOTH tagged flows (need CC+HMTD)
AND untagged routed/NAT flows (use ehash) **on the same physical ports**. CC and
ehash both graft the port's KG scheme (KGSE_CCBS/RCCB) — they are mutually
exclusive per port. So the per-port "capability mutex" idea does NOT fit the real
topology; production VLAN **requires** the unified model:

> **RCCB → one CC tree per port. CC keys = VLAN flows (combined HMTD leaf).
> CC MISS → the FE_ENTER ehash root** (so untagged routed/NAT flows still hit the
> proven ehash path).

The primitive exists: the F-182 `fe_off` path already overwrites a CC AD row
with the FE_ENTER AD's 4 words (pre-attach, per RM 5.12.14.1 — a post-attach raw
overwrite faults the controller / watchdog-resets). Applying that to the **miss
row** yields CC-miss→FE.

### THE GATING UNKNOWN — coexistence de-risk (R4c-pre), silicon
Whether CC(VLAN) + ehash(routed) coexist on one live port via CC-miss→FE is a
**new, unproven silicon question**. It MUST be de-risked on the `cc_test` harness
before any ask.ko production wiring:
- Extend `cc_test` to install a VLAN CC key AND set the **miss row = FE_ENTER AD**
  (reuse the F-182 write, applied to the miss slot), then engage ASK ehash on the
  same port.
- Prove BOTH simultaneously on silicon: a tagged VLAN flow forwards via CC+HMTD
  AND an untagged routed flow still forwards via ehash (CC miss → FE), both
  sustained, Err FD = 0, no wedge.
- Only if this passes is the unified model viable; else fall back to a
  design where VLAN forces the whole port to CC and routed flows also move to
  CC leaves (heavier, but R3b shows CC sustains routed too).

### R4c sub-increments (staged; the risky wiring is gated on R4c-pre)
- **R4c-1 (SAFE, dormant — implement now):** production CC-shadow + HMTD
  lifecycle in `ask.ko` — a per-port software shadow of the VLAN CC key set,
  `fman_hm_vlan_route_get`/`put` for the HMTD, and rebuild via
  `fman_pcd_cc_static_install`, with **correct teardown ordering**
  (detach graft → quiesce → restore RCCB/RSS → free MURAM; NEVER churn VyOS
  config on the port mid-teardown — the 2026-08-26 wedge lesson). Ships DORMANT
  (no flowtable wiring), exercised via a genl/debug trigger. Zero risk to the
  10G ehash path.
- **R4c-pre (silicon de-risk, gates the rest):** the coexistence proof above.
- **R4c-2 (wire, gated on R4c-pre PASS):** hook `ask_flow_offload_replace/destroy`
  so a VLAN flow → CC-shadow add/rebuild; routed/NAT → ehash unchanged; CC miss →
  FE. Per-flow add/del rebuilds the tree (bounded by `FMAN_PCD_CC_HW_MAX_KEYS`,
  fail closed past the cap).
- **R4c-3:** teardown/disengage integration + `pcd-snapshot` byte-clean gate.

### Teardown ordering (binding — from the 2026-08-26 wedge + qdrant prior art)
The R4b wedge was the SAME class as the 2026-07/08 CC teardown bugs. The
authoritative rules from that history (0106/0147, F-129, F-134, F-136/137):

1. **`detach_cc` MUST restore RSS `next_engine=2`, never 0.** Board patch
   0106/0147: `fman_pcd_kg_port_detach_cc()` setting `next_engine=0` disables
   the KG scheme entirely (hc/fqb/mv zeroed) → port needs a cold reboot. It must
   restore `next_engine=2` (RSS/bmi-enqueue). **This is the likely R4b wedge
   root cause** — confirm the in-tree detach restores 2, not 0.
2. **Order (F-134): disarm/detach BEFORE freeing MURAM.** Write RCCB back to the
   RSS/scheme form FIRST; only then free the CC tree + HMTD MURAM. Freeing MURAM
   while `FMBM_RCCB` still points at it → BMI dereferences freed memory → bus
   lockup / hard hang.
3. **Quiesce in-flight frames** between detach and free (F-136/137: freeing MURAM
   with frames in flight through an armed port = bus lockup). A short drain
   delay (F-135 used ~5 ms) or a dispatch-idle check.
4. **Never churn VyOS config / delete the vif on the port during teardown**
   (the R4b compounding factor).
5. **Scope teardown to the production function** (F-129 v4): match the specific
   function body, not the first occurrence of a helper call.
6. Cold-boot before/after CC experiments.

Sequence: `kg_port_detach_cc` (restore next_engine=2, RCCB→RSS) → quiesce →
`fman_port_set_cc_base(port, 0)` → `fman_pcd_cc_static_destroy` +
`fman_hm_vlan_route_put`.

### R4c-1 open item to verify first
Before writing R4c-1, confirm on the current tree whether
`fman_pcd_kg_port_detach_cc()` restores `next_engine=2` (0106/0147 landed) — if
so, the R4b wedge came from the vif-config churn (rule 4) + destroying under a
live graft, and the production teardown just needs rules 2–4. If not, that fix
lands first.

## 8. Provenance
- Constraint (ehash HIT = inline opcodes only): `decomp/en-exthash-lookup.asm`
  `execute_fe_actions`.
- HMTD/HMCT/NADEN encodings: board patches 0090a/0099/0101/0108/0115/0116/0119/
  0120; RM Ch.5 8.7.5 / 8.7.4.3 (qdrant).
- Freeze evidence: `decomp/fe-action-interpreter.md`,
  `decomp/vendor-vs-ask2-offloads.md`, T-M6-8 silicon records (qdrant).
- Kernel authority + gates: `plans/ASK2-MASTER-PLAN.md` §4.6.
