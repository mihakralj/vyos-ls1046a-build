# ASK2 offload-capability reference: remaining capabilities

**2026-10-15 · dpaa1 · Mechanism reference for `plans/ASK2-MASTER-PLAN.md` §1.2 and §4.6.** For each capability ASK2 still lacks, this document records how the vendor ASK 1.0 stack does it (verified live on `.110`, 2026-08-26) and which ASK2 mechanism is recommended. Priority order, task gates and acceptance belong to the master plan; this document does not set them. The shipped capabilities and their full history are in `plans/archive/OFFLOAD-CAPABILITY-PLAN-2026-10-15.md`.

This plan is subordinate to the master plan's binding rules (§2, §4.6.1):

- the kernel (`nf_flow_table`, XFRM, switchdev FDB/MDB, rtnetlink) is the single source of truth;
- ASK2 is a fail-closed hardware cache;
- never re-add a `cmm`/FCI/`dpa_app` daemon or XML-as-runtime-config;
- vendor code and CLI serve only as a semantic oracle.

## 0. The rule that governs every capability

| | Vendor ASK 1.0 | ASK2 (leaner) |
|---|---|---|
| Control plane | userspace `cmm` mirrors conntrack/rtnl/xfrm/bridge → FPP → `cdx.ko` | kernel offload callbacks direct into `ask.ko` |
| Flow primitive | `ipv4/ipv6 update {orig}{reply}` via FPP | one canonical `ask_offload_intent`, one FE record builder |
| Datapath | parser→KeyGen→CC→Policer, OH ports, two-stage pool-channel FQ | inline-opcode ehash record → direct per-egress no-confirm TX FQ |
| Header edits | SDK HMCD chains (separate CC/HM nodes) | typed FE opcodes fused inline in the ehash record |

Keep the lean model (kernel-authoritative ingest, one intent, one FE record builder, inline opcodes, direct-to-TX-FQ). Borrow a heavier vendor primitive (separate HM/CC node, OH port, two-stage FQ, replication group) only where a capability provably cannot be expressed as inline opcodes on a single ehash record. VLAN pop/push/translate proved expressible inline (2026-10-05), so "the L2 rebuild is the inline boundary" is not a valid argument for a heavier path.

## 1. Already shipped (details archived)

| Capability | ASK2 mechanism | Remaining work owner |
|---|---|---|
| Routed IPv4/IPv6 unicast | Inline: TTL/hop-limit → `INSERT_L2_HDR` → no-confirm `ENQUEUE`; the regression oracle for every new capability | Current-image acceptance: master §4.1 |
| NAT44/NAT66/PAT | Inline bit-fused L3/L4 rewrites (F-230); NAT46/64 stay software | Current-image acceptance: master §4.1 |
| Ingress policer | tc → FMPL profiles (F-231); bypassed on ASK-engaged ports by design | T-M6-QOS |
| Single-tag VLAN pop/push/translate | Inline FE-VM opcodes (`0x11`, `0x12`, `0x41`, `0x42`), default-on; CC-leaf+HMTD path retired | QinQ and DSCP→PCP: T-M6-VLAN2 |
| PPPoE encap/decap | Inline on an engaged source-interface port; no longer gated on the soft parser | T-M6-SP4; `ASK2-PPPOE-OFFLOAD-PLAN.md` |
| Bridge learned/static unicast, B7a same-tagging | Dedicated per-port 16-byte L2 ehash key | T-M6-2; `ASK2-BRIDGE-OFFLOAD-PLAN.md` |

## 2. Remaining capabilities

### 2.1 Bridge remainder (T-M6-2, T-M6-DISPATCH)

- **Vendor:** `FPP_CMD_RX_L2FLOW_ENTRY` / `RX_L2BRIDGE_MODE`; `set rx bridge add da/sa/type → queue/output/svlanprio/cvlanprio/sessionid`. An L2 flow is {DA, SA, ethertype} → egress, with optional VLAN priority and a PPPoE `sessionid`.
- **ASK2:** keep the distinct L2 key and table. Never reuse the L3 5-tuple table. B7b access↔trunk pop/push reuses the inline VLAN opcodes. IRB, routed+bridged on one port and PPPoE-session bridging all need multi-table per-port dispatch first.
- **Kernel authority:** switchdev FDB, bridge STP and port state, VLAN filtering, ageing.

### 2.2 IPv4/IPv6 multicast (T-M6-MC): needs a heavier primitive

- **Vendor:** `set mc4/mc6 ... group ... mode {bridged|routed} ... listener ...`, a group object with a replication list.
- **ASK2:** a group key ({[S,]G}) whose action is a bounded replication set (FMan RX frame replication or a fan-out FQ set). Do not encode it as N unicast records: that causes state blow-up and gives no atomic join/leave. Sequence it after bridge, because it shares the L2 egress plumbing.
- **Kernel authority:** switchdev MDB / kernel mroute.

### 2.3 IPsec ESP (T-M6-4): heavier, SEC/CAAM path

- **Vendor:** `cdx_esp4/6_cc`, 15 FCI SA commands, a `cmm` XFRM listener and CAAM; `FPP_CMD_IPSEC_SA_ACTION_OFFLOAD`; `qm sec_rate`; `query secfailstats`.
- **ASK2:** an SA object (not a flow), a CAAM shared/job descriptor and an ESP FE action routing FE→SEC→TX. Start with AES-CBC-SHA256, keep the CAAM A24a GCM refusal and advertise `NETIF_F_HW_ESP` last.
- **Kernel authority:** XFRM `xfrmdev_ops`. Never infer SAs from conntrack.
- **Owner:** `plans/ASK2-IPSEC-OFFLOAD-PLAN.md`.

### 2.4 PPPoE remainder (T-M6-SP4)

- **Vendor:** `cdx_pppoe_cc`, PPPoE FCI, the `cdx_sp.xml` soft parser (`ccbase += 0x30`), and the bridge `sessionid`.
- **ASK2:** PPPoE-over-VLAN and relay are still absent. If parser recognition is needed, use the owned, relocatable soft-parser arena (1984 bytes, shared with tunnels). Never load the vendor sequence unmodified.

### 2.5 Fragments (T-M6-FR): policy decision, default punt

- **Vendor:** `set frag ipv4|ipv6|sam-ipv4 [timeout][mode acp|drop]`, a reassembly module with its own BMan pool (`cdx_frag4/6_cc`).
- **ASK2:** fragments go to software by default. Build hardware reassembly only on measured need, with kernel ownership and bounded memory and timeouts. MTU-shrink fragmentation (F-262) is shipped and is not reassembly.

### 2.6 Tunnels, 6in4 / 4in6 (T-M6-SP5 / TN)

- **Vendor:** tunnel FCI plus the `cdx_sp.xml` IPv4-nextproto re-dispatch.
- **ASK2:** the inner-flow intent applies after soft-parser re-dispatch, and encap/decap are typed actions. Add one tunnel type at a time, only through a kernel tunnel netdev, and reuse the PPPoE parser arena.

### 2.7 Egress QoS / CEETM and ASK-path rate limits (T-M6-QOS): heavier

- **Vendor:** `qm` shaper, WBFQ, 16 class queues per channel, DSCP→FQ map, and the `qm ff_rate`/`expt_rate` policers.
- **ASK2:** QMan CEETM is a separate scheduling subsystem, not a flow action. The kernel authority is tc qdisc.

### 2.8 Conditional and out of scope

- **3-tuple / coarse flows (T-M6-T3):** a separate key type and table. Never truncate a 5-tuple key: `keysize` must equal the extraction length. Only a concrete kernel wildcard-flow requirement activates this.
- **MACVLAN (T-M6-MV):** only if a VyOS requirement maps cleanly to switchdev or flowtable ownership.
- **Permanently out of scope:** RTP/RTCP relay, Wi-Fi/VAP direct path, voice buffers and appliance packet capture.

## 3. Shared infrastructure still to build

1. **Multi-table per-port dispatch:** needed for IRB, routed+bridge on one port and routed+ESP.
2. **Bounded replication group:** multicast.
3. **Owned soft-parser arena:** PPPoE remainder and tunnels. Reuse F-266's minimal loader.
4. **SA/CAAM descriptor path and an offline-port/SEC substrate:** IPsec, shared with the WireGuard P4 phase.
5. **HMCD header-manipulation nodes:** only if an encap-bearing capability provably cannot be expressed inline.

The canonical intent and the single FE record builder (T-M6-A1..A4) already exist; every new capability is a composition of typed actions on that builder.

## 4. Evidence provenance

- Vendor live behaviour on `.110` (2026-08-26): `cmm -c "query {route|connections|vlan|l2flows|...}"`, `set {qm|rx|frag|route|ipsec} ...`, the `FPP_CMD_*` catalog.
- `decomp/vendor-vs-ask2-offloads.md`, `decomp/vendor-fmd-ioctl-abi.md`, `decomp/fe-action-interpreter.md`.
- HMCD, replication and SEC silicon facts: NXP LS1046A/LS1043A DPAA RM Chapter 5 (qdrant).
