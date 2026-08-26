# ASK2 offload-capability plan — vendor mechanism, leaner ASK2 mechanism, per capability

**2026-08-26 · dpaa1 · Companion to `plans/ASK2-MASTER-PLAN.md` §4.6.**

Purpose: for **every** offload the vendor ASK 1.0 stack implements, record (1) how
the vendor does it (verified live on `.110`, 2026-08-26), (2) the leaner ASK2
mechanism recommendation, and (3) the concrete build steps + gate, cross-linked to
the master-plan task IDs. This is the reference we will use when building bridging,
multicast, IPsec, and the remaining capabilities.

This plan is subordinate to the master plan's binding rules (§4.6.1): the kernel
(`nf_flow_table`, XFRM, switchdev FDB/MDB, rtnetlink) is the single source of
truth; ASK2 is a fail-closed hardware cache; **never** re-add a `cmm`/FCI/`dpa_app`
daemon or XML-as-runtime-config. Vendor code/CLI is a semantic oracle only.

## 0. The one recommendation that governs all capabilities

The live comparison (`decomp/vendor-vs-ask2-offloads.md`) proved a single
structural choice explains ASK2's wins and its one loss:

| | Vendor ASK 1.0 | ASK2 (leaner) |
|---|---|---|
| Control plane | userspace `cmm` daemon mirrors conntrack/rtnl/xfrm/bridge → FPP → `cdx.ko` | kernel offload callbacks direct into `ask.ko` (no daemon, no second state model) |
| Flow primitive | `ipv4/ipv6 update {orig 4-tuple}{reply 4-tuple}` via FPP | one canonical `ask_offload_intent` (match + typed ordered actions), one FE record builder |
| Datapath | parser→KeyGen→CC→Policer, OH ports, VSPs, **two-stage** CPU-pool-channel FQ w/ stashing | inline-opcode ehash record → **direct** per-egress no-confirm TX FQ |
| Header edits | SDK HMCD chains (separate CC/HM nodes) | typed FE opcodes fused inline in the same ehash record |

**Recommendation (applies to every capability below): keep the ASK2 lean model —
kernel-authoritative ingest + one intent + one FE record builder + inline opcodes +
direct-to-TX-FQ — and only borrow the vendor's *heavier* mechanism (separate HM/CC
node, OH port, two-stage FQ, replication group) where a capability provably cannot
be expressed as inline opcodes on a single ehash record.** The VLAN result is the
first evidence that L2 header-rebuild may be the boundary of "expressible inline."

The lean model wins on: no daemon/start-order coupling, no duplicated state, no
MURAM churn from per-flow HM/CC nodes, lower per-flow latency (direct enqueue), and
one audited record builder instead of N module record formats. It costs: some
capabilities (replication, reassembly, parser-recognized encaps) genuinely need a
heavier primitive, called out per row.

## 1. Capability catalog (vendor verified-live → ASK2 plan)

Legend for "ASK2 mechanism": **inline** = one ehash record + fused FE opcodes +
direct TX FQ (the proven lean path); **inline+param** = same but needs an extra
typed action/param; **heavier** = provably needs a vendor-like separate primitive.

### 1.1 Routed IPv4 / IPv6 unicast — DONE (reference oracle)
- **Vendor:** `FPP_CMD_IP_ROUTE` + `CMD_IPV4/IPV6_CONNTRACK ACTION_UPDATE`
  (orig/reply tuples); offloaded flows freeze in kernel conntrack, forward in CDX
  fast path (verified 0 tcpdump transit, 100% idle @1.4G). `query route` on `.110`
  shows FPP route entries (Id/Output-Iface/Input-Iface/DST-Mac/Mtu).
- **ASK2 mechanism: inline (SHIPPED).** 14-byte v4 / 46-byte dual-lane v6 ehash
  key; `UPDATE_TTL`/`UPDATE_HOPLIMIT` → `INSERT_L2_HDR` → per-egress no-confirm
  `ENQUEUE`. ~7–10 Gbit/s, CPU-bypass. Master plan: DONE (T-M6-P5).
- **Recommendation:** this IS the lean template; every new capability is measured
  against "can it be a composition of actions on this record?" Keep it as the
  regression oracle.

### 1.2 NAT44 / NAT66 / PAT — DONE
- **Vendor:** no separate NAT command — NAT is the orig≠reply case of the same
  `ipv4/ipv6 update`; port translation = differing sport/dport. Verified live: nft
  `srcnat`+`masquerade` (v4 and v6) with advancing counters.
- **ASK2 mechanism: inline (SHIPPED default-on).** Bit-fused in-place L3/L4
  rewrites (F-230, v4 L3 `0x27`, v6 L3 `0x2f`, ports `0x33`); silicon
  auto-recomputes checksums. `nat44_offload`/`nat66_offload` gates.
- **Recommendation:** already leaner than vendor (fused into the record vs a
  separate conntrack-update object). No change. NAT46/NAT64 stay software (no
  family-conversion opcode; architecturally impossible in HW).

### 1.3 Ingress policer / rate-limit — DONE (mechanism), same silicon block
- **Vendor:** `set qm ingress queue <0-7> policer on` + `cir/pir`
  (`CMD_QM_INGRESS_POLICER_*`, two-rate CIR/PIR on FMan **FMPL**); plus dedicated
  `qm ff_rate` (fast-forward) and `qm sec_rate` (IPsec) policers.
- **ASK2 mechanism: inline/side (SHIPPED, F-231).** `tc police`/`ingress-policer`
  → the **same FMPL** profiles (G/Y/R, CIR/PIR/CBS/PBS, block-enable). Driven
  per-interface via tc instead of per-queue via a daemon.
- **Recommendation:** keep tc-driven FMPL — leaner (no cmm, kernel tc is the
  authority). Note the vendor's per-queue granularity and ff/sec dedicated
  policers are richer; adopt only if a VyOS QoS requirement appears. ASK-engaged
  ports route AC_CC/FE-VM and bypass PLCR by design (per-interface mutex).

### 1.4 VLAN pop/push — BLOCKED (the boundary case)
- **Vendor:** VLAN via the SDK **parser + HMCD header-manip chain** (`set rx
  bridge` svlanprio/cvlanprio/vlan-queue, `tx` DSCP-VLAN-PCP map, parser
  `set_vlan_tpid1/2`), standard parser→KG→CC + OH reassembly. `query vlan` shows
  `eth3.100 VID 100`. **Sustains** on identical silicon.
- **ASK2 mechanism attempted: inline** (STRIP_ETH 0x11, STRIP_ALL_VLAN 0x12,
  INSERT_VLAN 0x42, INSERT_L2 0x41 fused in the ehash record). Records
  byte-correct but **freeze after ~22 packets** (= 5+tnums FE-VM resource);
  falsified: bpid/word2 frag-context, the `[0xd0b8]` epilogue (both oracle
  directions), TX-FQ drain (frm_cnt=0). See `decomp/fe-action-interpreter.md`,
  `decomp/vendor-vs-ask2-offloads.md`.
- **Recommendation — REVISED per this comparison: do NOT keep tuning the inline
  ehash record for VLAN.** The vendor's success via a *separate HMCD chain* + the
  ASK2 inline freeze together indicate L2 teardown+rebuild is the first capability
  that likely needs the **heavier** primitive. Two ranked options, master-plan
  T-M6-8:
  1. **HMCD header-manip node (vendor-like, recommended next):** build the VLAN
     strip/insert as a real FMan Header-Manipulation Command Descriptor chain (RM
     Ch.5 HMCD: L2 remove, insert-N-bytes, DSCP→VLAN-prio, reparse-after-HM),
     referenced from the flow's next-engine, instead of inline FE-VM opcodes.
     This is the proven vendor path; it costs a per-VLAN-flow (or per-VID) HM
     MURAM node + the shared HMCD infra (reusable by IPsec/tunnels/PPPoE later).
  2. **Two-stage FQ (if HMCD alone insufficient):** enqueue the HIT to an
     intermediate pool-channel FQ with context stashing (like the vendor's
     channel-9 classify FQ) before TX, if the L2 rebuild needs the CPU/portal
     recycle step the direct no-confirm TX FQ can't provide.
  - Gate: master plan T-M6-8 (untagged↔tagged, tagged↔tagged, PCP/DEI, MTU
    1280–2500, checksum/L2, unsupported depth → SW, IPv4 regression).

### 1.5 L2 bridge / FDB — NOT IMPLEMENTED (target)
- **Vendor:** `FPP_CMD_RX_L2FLOW_ENTRY` / `RX_L2BRIDGE_MODE`; `set rx bridge add
  da/sa/type → queue/output/svlanprio/cvlanprio/sessionid`; `set bridge timeout`.
  L2 flow = {DA, SA, ethertype} → egress queue/iface (+optional VLAN prio,
  +sessionid for PPPoE). `query l2flows` = "L2 and L3-4 flows".
- **ASK2 mechanism: inline, separate key type.** A distinct **L2 ehash key**
  ({DA,SA,ethertype[,VID]}) on its own KG scheme/table (never reuse the L3
  5-tuple key/table), action = `INSERT_L2`/none + `ENQUEUE` to the egress port's
  TX FQ. Unknown-unicast/broadcast/STP-blocked stay software.
- **Kernel authority:** switchdev **FDB** add/del/flush; bridge owns STP/port
  state, VLAN filtering, learn/static, ageing.
- **Recommendation: lean inline with a new L2 key type** — no HMCD needed for a
  plain forward (dst is already correct at L2 for a bridged frame; no L3 rewrite).
  This is close to the routed template with a different key. Master plan T-M6-2.
  Reuse VLAN's HMCD infra (1.4) only if VLAN-aware bridging needs tag edits.

### 1.6 IPv4 / IPv6 multicast — NOT IMPLEMENTED (needs heavier primitive)
- **Vendor:** `set mc4/mc6 ... group {mask}{src}{dst} mode {bridged|routed}
  queue ... listener [mc|uc {mac}] [queue] [if] ...` — a group object with a
  **replication list** (N listeners, per-listener queue/shaper/egress).
- **ASK2 mechanism: HEAVIER — bounded replication.** A group key ({[S,]G}) whose
  action is a **replication set** (FMan Rx frame-replication / a fan-out FQ set),
  NOT N independent unicast records. This genuinely exceeds the single-record
  inline model.
- **Kernel authority:** switchdev **MDB** / kernel mroute.
- **Recommendation:** implement as an owned group object + bounded replication FQ
  group; do not encode as many unicast ehash records (state blow-up, no atomic
  join/leave). Master plan T-M6-MC. Sequence AFTER bridge (shares L2 egress
  plumbing) and AFTER the HMCD infra exists.

### 1.7 IPsec ESP — NOT IMPLEMENTED (heavier; SEC/CAAM path)
- **Vendor:** `cdx_esp4/6_cc` + 15 FCI SA commands + `cmm` XFRM listener +
  CAAM; `FPP_CMD_IPSEC_SA_ACTION_OFFLOAD`; `qm sec_rate` policer; SEC failure
  stats (`query secfailstats`).
- **ASK2 mechanism: HEAVIER — SA table + CAAM descriptor path + FE→SEC→TX.** An
  SA object (not a flow), a CAAM shared/job descriptor, and an ESP FE action that
  routes the frame FE→SEC→TX. Cannot be a plain ehash-record forward.
- **Kernel authority:** XFRM `xfrmdev_ops` (SA add/del/update/lifetime,
  anti-replay, NAT-T). Never infer SAs from conntrack; never a cmm mirror.
- **Recommendation:** kernel-authoritative XFRM ingest is the leaner control
  plane (vs cmm's XFRM listener). Start AES-CBC-SHA256 only; keep the binding GCM
  refusal (CAAM A24a erratum). Advertise `NETIF_F_HW_ESP` LAST. Master plan
  T-M6-4 / IP1 / IP2. This is the largest capability; sequence after HMCD infra.

### 1.8 PPPoE — NOT IMPLEMENTED (soft-parser)
- **Vendor:** `cdx_pppoe_cc` + PPPoE FCI + `cdx_sp.xml` soft-parser (`ccbase +=
  0x30`, session recognition); bridge `sessionid` field carries the PPPoE session.
- **ASK2 mechanism: inline + soft-parser prerequisite.** A relocatable soft-parse
  sequence exposes the inner IP so the *existing* route/NAT/VLAN intent applies —
  i.e. once the parser recognizes PPPoE, the forward is the lean inline path again.
- **Kernel authority:** PPPoE netdev + normal flowtable after recognition.
- **Recommendation:** lean — the only new heavy piece is the owned soft-parser
  arena (reused by tunnels). Never load the vendor sequence unmodified; relocate
  `+0x30`/NIAs to ASK2 PCD objects. Master plan T-M6-SP1..SP4.

### 1.9 IP fragmentation / reassembly — NOT IMPLEMENTED (heavier or punt)
- **Vendor:** `set frag ipv4|ipv6|sam-ipv4 [timeout][mode acp|drop]` — a
  dedicated frag/reassembly module with its own BMan spill pool
  (`cdx_frag4/6_cc`). (ASK2's F-234 mimicked this pool for VLAN but it is unused
  for sub-MTU frames — a dead end for VLAN.)
- **ASK2 mechanism: policy decision, default PUNT.** Either (a) a fragment key +
  bounded HW reassembly (heavy, own pool + timeouts), or (b) always punt fragments
  to the kernel (leanest, safe). ASK2 order-1 buffers already cap MTU (no RX SG),
  so HW reassembly has limited value.
- **Recommendation: punt fragments to software by default** (leanest, no firewall/
  NAT bypass risk); only build HW reassembly if a measured need appears, with a
  kernel ownership model + bounded memory/timeouts. Master plan T-M6-FR.

### 1.10 Tunnels / 6-in-4 — NOT IMPLEMENTED (soft-parser + encap actions)
- **Vendor:** tunnel FCI + `cdx_sp.xml` IPv4-nextproto re-dispatch.
- **ASK2 mechanism: inline + soft-parser re-dispatch + explicit encap/decap
  action.** Inner-flow intent after the parser re-dispatches; encap/decap as typed
  actions.
- **Kernel authority:** kernel tunnel netdev + flowtable.
- **Recommendation:** one tunnel type at a time, only via a kernel tunnel netdev;
  reuse the PPPoE soft-parser arena. Master plan T-M6-SP5 / T-M6-TN.

### 1.11 3-tuple / coarse flows — NOT IMPLEMENTED
- **Vendor:** `cdx_tuple3*` tables.
- **ASK2 mechanism: inline, separate key type.** A distinct KG extraction / key
  type / table; **never** truncate a 5-tuple key (`keysize` MUST equal extraction
  length — a standing silicon rule).
- **Recommendation:** lean, only when kernel wildcard-flow semantics require it.
  Master plan T-M6-T3.

### 1.12 Egress hierarchical QoS / CEETM — NOT IMPLEMENTED (heavier, scoped out)
- **Vendor:** `qm` shaper + WBFQ + 16 class-queues/channel + DSCP-to-FQ map
  (`FPP_CMD_QM_*`).
- **ASK2 mechanism: HEAVIER — QMan CEETM.** A separate scheduling subsystem, not
  a flow action.
- **Recommendation:** scope separately from the flow-offload roadmap; adopt only
  on a concrete VyOS QoS requirement. Kernel authority = tc qdisc.

### 1.13 MACVLAN — conditional
- **Vendor:** `CMD_MACVLAN_ENTRY` / `query macvlan`.
- **Recommendation:** implement ONLY if a VyOS requirement maps cleanly to
  switchdev/flowtable ownership; vendor having it is not justification. Master
  plan T-M6-MV.

### 1.14 Permanently out of scope
RTP/RTCP relay, WiFi/VAP direct path, voice buffers, appliance packet-capture —
vendor appliance-specific, not VyOS router requirements. Never expand ASK2 to
these (master plan §4.6.4).

## 2. Shared infrastructure this plan implies (build once, reuse)

Ordered by how many capabilities unlock:

1. **Canonical intent + one FE record builder** — DONE (T-M6-A1..A4). Every
   capability is a composition of typed actions on this builder.
2. **HMCD header-manipulation infrastructure** — NEW, highest leverage. Needed by
   VLAN (1.4), and reusable by IPsec ESP encap, tunnels, PPPoE. Build as owned
   MURAM HM nodes with strict lifecycle + readback (RM Ch.5 HMCD). **Recommend
   this is the next infra investment** because it likely unblocks VLAN and is a
   prerequisite for the encap-bearing capabilities.
3. **Separate key types / tables** — L2 key (bridge 1.5), 3-tuple (1.11),
   per-family already done. One KG scheme + owned table per key type; never alias.
4. **Bounded replication group** — multicast (1.6); shares L2 egress plumbing
   with bridge.
5. **Owned soft-parser arena** — PPPoE (1.8), tunnels (1.10). One 1984-byte arena,
   relocatable, per-port refcount.
6. **SA/CAAM descriptor path** — IPsec (1.7).

## 3. Recommended build order (leanest-first, dependency-aware)

1. **Bridge/FDB (T-M6-2)** — closest to the proven inline template (new L2 key,
   plain forward, no HMCD). Highest value / lowest new-mechanism risk.
2. **HMCD infra + VLAN (T-M6-8)** — the boundary capability; unblocks the encap
   family. Adopt the vendor-like HM chain rather than more inline-opcode tuning.
3. **Multicast (T-M6-MC)** — reuses bridge L2 egress + adds bounded replication.
4. **Soft-parser + PPPoE (T-M6-SP*)** — owned parser arena; forward stays inline.
5. **IPsec ESP (T-M6-4)** — largest; SA/CAAM path; after HMCD infra exists.
6. **Tunnels (T-M6-TN)**, **3-tuple (T-M6-T3)**, **fragments (T-M6-FR, punt
   default)** — remaining router breadth.
7. **Egress QoS/CEETM, MACVLAN** — only on explicit VyOS requirement.

Each step follows the master plan §4.6.5 per-feature acceptance contract:
capability + kernel-authority + forward/inverse + concurrency + safety +
observability gates, and MUST NOT regress the ~10 Gbit/s IPv4 path.

## 4. Evidence provenance
- Vendor live behavior: `cmm -c "query {route|connections|vlan|l2flows|...}"`,
  `set {qm|rx|frag|route|ipsec} ...` usage, `FPP_CMD_*` catalog, nft ruleset on
  `.110` (2026-08-26).
- Companion docs: `decomp/vendor-vs-ask2-offloads.md` (per-capability comparison),
  `decomp/vendor-fmd-ioctl-abi.md` (why live vendor records aren't readable),
  `decomp/fe-action-interpreter.md` (VLAN freeze locus).
- Authoritative gates/rules: `plans/ASK2-MASTER-PLAN.md` §4.6 (this plan does not
  replace it — it adds vendor-mechanism detail + the leaner-mechanism
  recommendation per capability).
- HMCD/replication/SEC silicon facts: NXP LS1046A/LS1043A DPAA RM Ch.5 (qdrant).
