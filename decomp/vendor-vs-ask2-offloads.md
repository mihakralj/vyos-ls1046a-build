# Vendor ASK 1.0 vs ASK2 — per-offload architecture comparison

**2026-08-26 · Verified live on the vendor board `.110` (OpenWrt 6.12.103, CDX +
cmm + FMD/PCD SDK) via the `cmm` CLI, its FPP command set, and the nftables
ruleset. Compares each offload ASK2 has implemented so far against how the
vendor achieves the same thing.**

This does not re-derive silicon facts — it records, per capability, (a) what the
vendor actually does, verified on `.110`, and (b) how ASK2 does it differently.

## The single structural difference (root of everything below)

The two stacks offload flows through **fundamentally different mechanisms**:

- **Vendor ASK 1.0** = the NXP **FMD/PCD SDK pipeline** driven by a userspace
  daemon `cmm` (Conntrack Monitor Module) over an **FPP command protocol**.
  `cmm` watches Linux conntrack and pushes flows to hardware with
  `CMD_IPV4_CONNTRACK` / `CMD_IPV6_CONNTRACK` `ACTION_REGISTER/UPDATE/DEREGISTER`.
  The datapath is parser → KeyGen → Coarse-Classification → Policer, with
  Offline/Host (OH) ports for reassembly, per-port FQID ranges, and Virtual
  Storage Profiles. Forwarding rides a **two-stage FQ path** (classify FQ on a
  CPU-pool channel with context stashing → per-port TX FQ).
- **ASK2** = a bespoke **inline-opcode FE-VM ehash record** built directly in
  `fman_pcd.c` from the kernel nft flowtable, enqueuing **directly** to a single
  per-port **no-confirm TX FQ**. No cmm, no FPP, no OH ports, no SDK PCD graph.

Every per-capability difference below is a consequence of this split.

## The vendor's universal flow primitive: orig+reply tuple "update"

The vendor programs **one hardware flow with BOTH direction tuples at once**:

```
ipv4 update {orig-src orig-dst orig-sport orig-dport}
            {reply-src reply-dst reply-sport reply-dport} {proto} {mark}
ipv6 update {...same, v6...}
```

This is the key insight: **routing, NAT, and PAT are all the same primitive** to
the vendor. Plain routing = orig/reply tuples are mirror images; NAT/PAT = the
reply tuple's addresses/ports differ from the orig (the translation is *implicit*
in the tuple delta). This is conceptually the same as ASK2's conntrack-flowtable
model (ASK2 also derives both directions from the ct entry), just expressed via
FPP instead of an ehash record.

## Per-capability comparison

### 1. Routed IPv4 unicast — BOTH WORK
- **Vendor:** `FPP_CMD_IP_ROUTE` + `CMD_IPV4_CONNTRACK` register/update.
  Conntrack-driven; offloaded flows freeze in kernel conntrack (`packets=2,
  [PERMANENT]`) and forward in the CDX fast path (verified: 0 tcpdump transit,
  ~0 QMan portal IRQ delta, 100% idle CPU under 1.4G).
- **ASK2:** inline ehash record, direct-to-TX-FQ. Silicon-validated ~7.12 Gbit/s.
- **Verdict:** parity in function; different mechanism. ASK2 is actually faster
  per-flow (direct enqueue) when it works.

### 2. Routed IPv6 unicast — BOTH WORK
- **Vendor:** identical model via `CMD_IPV6_CONNTRACK ACTION_UPDATE` (same
  orig/reply-tuple `ipv6 update`). Separate v6 conntrack command, same pipeline.
- **ASK2:** separate v6 KG scheme + v6 ehash table (16-byte addrs). Shipped
  (`ASK_CAP_IPV6`), silicon-passed.
- **Verdict:** parity; both treat v6 as a parallel table/command to v4.

### 3. NAT44 (SNAT/DNAT/masquerade) — BOTH WORK
- **Vendor:** no separate "NAT" command — NAT is the orig≠reply case of
  `ipv4 update`. Verified live on `.110`: nft `srcnat` rule
  `ip saddr 10.99.11.0/24 ip daddr 10.99.12.0/24 snat to 10.99.2.110` +
  WAN `masquerade`, both with packet counters advancing → offloaded via the same
  conntrack-update primitive.
- **ASK2:** `nat44_offload` gate (default on), `ASK_ACT_NAT_SRC/DST/PAT`; the
  ehash record rewrites addr/port inline. Silicon-validated S0-S3.
- **Verdict:** parity; both fold NAT into the bidirectional flow primitive.

### 4. NAT66 — BOTH WORK
- **Vendor:** orig≠reply case of `ipv6 update`. Verified live: nft
  `ip6 saddr fd99:11::/64 ip6 daddr fd99:12::/64 snat to fd99:2::110`, counters
  advancing.
- **ASK2:** `nat66_offload` gate (default on). Shipped.
- **Verdict:** parity.

### 5. PAT / NAPT (port translation) — BOTH WORK
- **Vendor:** implicit — the `update` primitive carries orig+reply **ports**, so
  port translation is just differing sport/dport between the two tuples.
- **ASK2:** `ASK_CAP_PAT`, advertised.
- **Verdict:** parity.

### 6. Ingress policer / rate-limit — BOTH WORK (SAME silicon block)
- **Vendor:** `set qm ingress queue <0-7> policer on` + `cir/pir`
  (FPP `CMD_QM_INGRESS_POLICER_ENABLE/CONFIG`). Two-rate CIR/PIR on the FMan
  **Policer (FMPL)** block. Also `qm ff_rate` (fast-forward rate) and
  `qm sec_rate` (IPsec rate) as dedicated policers.
- **ASK2:** `ingress-policer` CLI / `tc police` → the **same FMPL** profiles
  (G/Y/R, CIR/PIR/CBS/PBS, block-enable). Silicon-validated (F-231, BUG-3a/3b).
- **Verdict:** parity on the policer block itself. Vendor drives it per ingress
  queue via cmm; ASK2 drives it per-interface via tc — same hardware.

### 7. VLAN pop/push — VENDOR WORKS, ASK2 BLOCKED
- **Vendor:** VLAN is a property of the PCD parser + the SDK header-manipulation
  chain, applied through `set rx bridge` (svlanprio/cvlanprio/vlan queue),
  `tx` DSCP-VLAN-PCP map, and the parser's `set_vlan_tpid1/2`. Forwarding uses
  the standard parser→KG→CC path + OH reassembly ports. `cmm -c "query vlan"`
  shows `eth3.100 VID 100`. **Sustains** on the same silicon.
- **ASK2:** bespoke inline VLAN opcodes (STRIP_ETH 0x11, STRIP_ALL_VLAN 0x12,
  INSERT_VLAN 0x42, INSERT_L2 0x41) in the ehash record. Records byte-correct,
  but **freezes after ~22 packets** (T-M6-8 blocked; `ask_vlan_offload` gate
  default-off, `ASK_CAP_VLAN` unadvertised).
- **Verdict:** THE gap. The vendor does VLAN through the SDK PCD/header-manip
  pipeline (a proven path); ASK2 tries to do it inline in the FE-VM ehash record
  (an unproven path that hits a ~22-frame FE-VM resource limit). This is the
  strongest architectural evidence that ASK2's inline-ehash approach to VLAN is
  the wrong mechanism vs the vendor's parser+HM-chain approach.

## Capabilities the VENDOR has that ASK2 does NOT (scope reference)

Not ASK2 targets, but they show how much of the SDK the vendor leverages:

- **L2 bridging / bridge fast-flows** (`FPP_CMD_RX_L2FLOW_ENTRY`,
  `RX_L2BRIDGE_MODE`, `set rx bridge`) — ASK2 has `ASK_CAP_BRIDGE` bit defined
  but not implemented.
- **Multicast v4/v6** (`set mc4`/`mc6`, bridged/routed, per-listener shapers) —
  ASK2 has `ASK_CAP_MULTICAST` bit, not implemented.
- **Egress hierarchical QoS** — shaper, WBFQ, 16 class-queues/channel,
  DSCP-to-FQ map (`FPP_CMD_QM_*`). ASK2 has no egress QoS offload.
- **IPsec SA offload** (`FPP_CMD_IPSEC_SA_ACTION_OFFLOAD`, `sec_rate`) — ASK2
  has `ASK_CAP_ESP_OFFLOAD` bit, not implemented (ASK 1.x kernel IPsec offload
  was deleted; ASK2 re-architects outside kernel).
- **IP fragmentation/reassembly** (`set frag ipv4|ipv6|sam-ipv4`, acp/drop) —
  the dedicated frag BMan pool. ASK2's F-234 mimicked the frag pool for VLAN but
  it is unused for sub-MTU frames (falsified as the VLAN-freeze cause).
- **PPPoE, RTP relay, WiFi VAP, DSCP-VLANPCP map** — not ASK2 scope.

## Net comparison

| Capability | Vendor ASK 1.0 | ASK2 | Status |
|---|---|---|---|
| Routed IPv4 | conntrack-update, PCD pipeline | inline ehash, direct TX-FQ | both work |
| Routed IPv6 | `CMD_IPV6_CONNTRACK` | v6 ehash table | both work |
| NAT44 | orig≠reply `ipv4 update` | inline rewrite, `nat44_offload` | both work |
| NAT66 | orig≠reply `ipv6 update` | `nat66_offload` | both work |
| PAT/NAPT | ports in update tuple | `ASK_CAP_PAT` | both work |
| Ingress policer | `qm ingress policer` (FMPL) | `tc police` (FMPL) | both work, same block |
| VLAN pop/push | parser + SDK HM chain + OH | inline FE-VM opcodes | **vendor works, ASK2 blocked** |
| NAT46/NAT64 | — | software-only (impossible in HW) | neither (architectural) |

**Conclusion:** For every capability where both stacks forward via a
straightforward flow rewrite (routing, NAT44/66, PAT, policer), ASK2 achieves
functional parity with the vendor using a *leaner* mechanism (inline ehash +
direct TX-FQ vs cmm/FPP + two-stage FQ). The **only** capability where the
mechanisms diverge in outcome is **VLAN**: the vendor routes it through the
proven SDK parser + header-manipulation pipeline, while ASK2's inline-ehash
implementation hits an FE-VM execution limit (~22 frames). This reinforces that
the ASK2 VLAN fix likely requires adopting a vendor-like header-manipulation /
two-stage path rather than more tuning of the inline ehash record.

## Evidence provenance
- Vendor cmm module surface + usage: `cmm -c "set|show|query <module>"` on `.110`.
- Vendor NAT: `.110` nft `srcnat`/`masquerade` rules with advancing counters.
- Vendor flow primitive: `cmm` strings `ipv4/ipv6 update {orig...}{reply...}`,
  `CMD_IPV4/IPV6_CONNTRACK ACTION_REGISTER/UPDATE/DEREGISTER`.
- Vendor FPP command set: `FPP_CMD_IP_ROUTE`, `RX_L2FLOW_ENTRY`,
  `IPSEC_SA_ACTION_OFFLOAD`, `QM_*`, `DSCP_VLANPCP_MAP_CFG`.
- Vendor policer: `set qm ingress queue N policer`, `CMD_QM_INGRESS_POLICER_*`.
- ASK2 caps: `ask.h` `ASK_CAP_*`; gates in `ask_hw.c`
  (`nat44/nat66/vlan_offload`), advertised `IPV4|IPV6|NAT|PAT` in `ask_genl.c`.
- Companion: `decomp/vendor-fmd-ioctl-abi.md` (FMD ioctl ABI, why live record
  bytes aren't readable on `.110`).
