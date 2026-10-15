# ASK2 bridge offload: remaining work

**2026-10-15 · T-M6-2 / T-M6-DISPATCH · Gate A7**

Execution priority and global gates belong to `ASK2-MASTER-PLAN.md`.
B0–B6 and B7a implementation/result logs have moved intact to
[`archive/ASK2-BRIDGE-OFFLOAD-PLAN-2026-10-15.md`](archive/ASK2-BRIDGE-OFFLOAD-PLAN-2026-10-15.md).
They are not an open B2–B5 work queue. Completed vendor mechanism analysis is
in `archive/ASK2-BRIDGE-OFFLOAD-ARCHITECTURE-ANALYSIS.md`, not a live plan.

## Current contract

Traffic-driven learned/static known-unicast IPv4/IPv6 bridging and B7a
same-tagging have silicon acceptance. B7a key is 16 bytes
`DA(6)|SA(6)|TCI(2)|ETYPE(2)`, EKFC `0x74000000`; TCI includes PCP/DEI.
Per-port tables isolate ingress; the port currently has one role/key profile,
not simultaneous L2 and routed tables.

`offload ipv4|ipv6` on a bridge member is the trigger, with ingress family
mask filtering. `ask.bridge_offload` defaults to 1 and is the global kill
switch; no bridge-specific CLI leaf. FDB authority stays with the bridge.
The `NF_BR_FORWARD` hook runs after bridge filtering; installed records must
remain valid against FDB, STP, isolation, port and VLAN state. Hardware source
liveness uses hit counters and externally learned entries with move/age
handoff. Do not replace it with the old static-only shadow/promotion design.

BUM, local/control frames, unsupported tags/protocols and unsupported mixed
roles remain in software. Bridges never decrement TTL or fragment; no `05`
MTU-check opcode, vendor bridge MTU terminal `0xffff`. Maintain the
1280–3600 engaged-port clamp and correct original DA/SA.

## Remaining stages

| Stage | Required outcome | Dependency / gate |
|---|---|---|
| Current-image repeat | B6 learned/static/move/age/visibility plus every B7a row and SW controls on 0551 | Dated acceptance is not reimplementation; no overlap with active parity campaign |
| B7b | Access↔trunk tag pop/push, then translation if required | L2-only emitter, no TTL; retain B7a and routed/NAT regressions |
| Multi-table dispatch | Multiple class-specific tables on one ingress port | Explicit precedence/miss/ownership/resource design, silicon proof and clean inverse |
| IRB | Route through bridge using kernel-selected member and nexthop | Multi-table/coexistence gate; no learning/routing shadow state |
| Routed + bridged same port | Both classes HW with independent lifetimes | Multi-table gate, collision/flush/family isolation |
| QinQ / 802.1ad | Stacked-tag membership and rewrite semantics | Shared VLAN2 task; unsupported depths remain SW |
| PPPoE bridging | Session frames forwarded without short-frame freeze | Cold-boot non-IP de-risk and session/control separation first |
| Multicast | MDB-owned replication | Separate T-M6-MC; no implied BUM offload from unicast support |

## B7b design and gate

Reuse the routed VLAN opcode primitives, not the routed action chain.
Vendor `fill_bridge_actions()` shape is `04 11 12 [42] 41 01`; do not add a
TTL/hop-limit opcode. `INSERT_L2_HDR` uses the record's DA/SA. Determine
ingress tagging/PVID and egress tagging from live bridge VLAN state, not
only the skb's post-`br_handle_vlan()` egress tag.

Before implementation, cross-check Qdrant, current silicon/flow-key specs
and vendor/live state. Resolve record size, owned stats parameters, tag order
and RX headroom; preserve opcode-0 ordering constraints.

Acceptance: access→trunk and trunk→access, v4/v6 both ways, PCP/DEI,
VID/PVID/membership changes under traffic, multiple VLANs with identical
DA/SA pairs, filtered VID and wrong tag fallback, tagged/untagged exact wire
capture. No family-mask, firewall, TTL, routed/NAT or B7a regression.

## Multi-table and mixed-role gate

Vendor has class-specific ESP/IP/PPPoE/L2 distributions; ASK2 has one key
profile/table per port. A key-profile change alone is not coexistence.
Design bounded resources and ordered class dispatch before IRB/routed+bridge
or routed+ESP on one port. Do not use disproven transit slot-LCV discrimination
or an unproven CC match walker as assumed infrastructure.

Prove each class hits its own record, misses reach the kernel, unsupported
packets are untouched, and identical tuples/MACs on other ports/tables cannot
alias. Replace/delete/flush/role change of one class must preserve others;
exhaustion/partial engage must unwind with byte-clean S0 restoration.

## Safety and resource follow-ups

- Short non-IPv4/IPv6 frames previously froze the FE HIT path after a handful
  of packets. Keep those protocols out of HW; no blanket transparent-L2 claim.
  Only a new cold-boot, one-variable gate can lift this restriction.
- Remote 10G link-down invalidation is not accepted while the carrier gap
  prevents the harness from observing it. Distinguish administrative down
  from physical link loss.
- Keep `ASK_BRIDGE_ENTRY_MAX=512`, `ASK_BRIDGE_REC_MAX=2048` until a deliberate
  budget decision. Earlier raw-table tests reached 32,768 records, not a
  production-cap acceptance. Coherent record/context page rounding costs
  ~9 KiB/record; investigate DMA pools before raising caps. O(R²) per-port
  ageing reads are bounded by the record cap and need scale measurement.
- Preserve safe dynamic↔static/local FDB handling. VLAN/PVID changes can
  remove externally learned entries and force relearning/software flooding;
  test correct delivery rather than assuming a fixed record count.

## Runbook and completion

After the independent routed/NAT/VLAN campaign ends, use
`bin/testrig-bridge-b6.sh` including B7a rows (`VLAN=0` skips them),
`bin/testrig-bridge-soak.sh`, and capacity probes only with explicit budgets.
The obsolete static-only `b3a` harness is not the B6 acceptance oracle.

Record image SHA, kernel, cold/warm boot, per-record hits, peer delivery,
kernel RX, per-core CPU, counters and errors. Require:

1. Untagged/access↔access/trunk↔trunk and transparent tagged forwarding;
   PCP 5 distinct record; ingress vif for a VID invalidates bridge records.
2. Learn/move/delete/age/static/local, STP/isolation/family masks,
   port down/leave/rejoin, kill switch, unload and cold boot.
3. Concurrent FDB/role churn under load, resource exhaustion fallback and
   no stale owner/generation; teardown zero records, flat MURAM/FQs,
   byte-exact `pcd-snapshot`, zero kernel errors.
4. SW/HW/vendor throughput by VLAN mode on the same rig. Historical 9.41
   Gbit/s results are not new measurements.

Advertise no new breadth until these and master §4.6.5 pass. Completed logs,
decisions D1–D8, gaps G1–G13 and their superseding corrections are preserved
in the archive, rather than repeated as contradictory instructions here.
