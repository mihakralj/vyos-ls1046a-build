# ASK2 vs NXP ASK: current parity evidence

**2026-10-15. Full feature parity is not reached.** The authoritative gap
inventory and task order are in [`ASK2-MASTER-PLAN.md`](ASK2-MASTER-PLAN.md)
§1.2/§3/§4. This file owns comparison evidence, not another implementation
backlog. Completed and provisional single-run tables were moved verbatim to
[`archive/ASK2-VENDOR-PARITY-2026-10-15.md`](archive/ASK2-VENDOR-PARITY-2026-10-15.md).
No final campaign values have been published here yet.

## Current campaign

| Property | Contract |
|---|---|
| ASK2 | `.185`, `2026.10.15-0551-rolling`, `6.18.55-vyos` |
| NXP ASK | `.106`, `6.12.103` |
| Traffic | dell1 `.112` → DUT eth3/eth4 → dell2 `.113`, and reverse |
| Controller | dell2-local systemd unit `testrig-parity-10x10-20261015T2130Z.service`; independent of Azure disconnect |
| Scope | 16 one-way and 8 bidirectional routed/NAT/VLAN cells |
| Repetitions | 10 per board per cell, adjacent paired runs; first board alternates each round; 480 attempts |
| Per run | 30 s, iperf3 `-Z -P8`, steady receiver intervals from 10 s; bidirectional uses `--bidir` |
| Output | dell2 `~/testrig/campaigns/parity-10x10-20261015T2130Z/` |
| State | In progress at this planning checkpoint; read `status.json`, not a copied counter |

Do not overlap other rig traffic, alter steering/MTU, or stop this campaign.
It excludes PPPoE, bridging, WireGuard, packet rate, connection scale and
latency. Passing all its cells would establish only measured-path throughput
parity, not full feature parity.

## Analysis and verdict contract

Preserve `measurements.csv` and every per-run CSV/JSON, including errors and
non-hardware runs. Summary must report paired means and medians, sample SD,
CPU, retransmits, missing/non-HW pairs, and 95% paired bootstrap intervals
for ASK2-minus-vendor throughput. No slow or high-CPU result is trimmed as
"jitter". Only complete HW-proven pairs contribute to paired throughput
intervals; exclusions and their reasons must remain visible.

Intervals are exploratory per cell, not campaign-wide multiplicity-adjusted
claims or proof of equivalence. **A lower ASK2 paired mean in any cell is a
FAIL**, regardless of gap size; report uncertainty separately. Missing or
invalid pairs are incomplete evidence, never a PASS. Throughput is iperf3
payload Gbit/s; bidirectional values sum both directions.

The earlier one-run re-test showed both stacks shifting by about an
MSS-sized step (~1.3%). It does not demonstrate an ASK2 slowdown and is not
a final parity verdict. Its exact numbers, directions and raw-data paths
are in the archive. Do not mix older 40 s/two-client medians or other images
into this campaign's baseline.

## Evidence still required

| Surface | Current acceptance state | Required comparison |
|---|---|---|
| Routed v4/v6, NAT44/NAT66, VLAN translate/strip/add | Implemented; current 10×10 result pending | Publish all 24 paired-cell summaries |
| NAT over VLAN | Implemented; prior quick results historical | Same-conditions current-image combo |
| PPPoE v4/v6 encap/decap, NAT over PPPoE | Implemented; prior runs not same-session | Both DUTs with vendor `pppoe10` up and dell1 RPS |
| PPPoE fragmentation | Prior ASK2 intact delivery; vendor UDP-header corruption observed | Both DUTs, controls/warm/new/DF/PTB matrix |
| Bridge learned/static and same-tagging | B6/B7a silicon acceptance is dated, not absent | Current-image functional repeat and vendor throughput per VLAN mode |
| Ingress policer | Historical cap measurements on non-ASK ports | Current wire rate accuracy ±15%; ASK-path coexistence is missing functionality |
| 64-byte pps / IMIX | Historical results, not 0551 acceptance | Same-rig offered/delivered/loss sweep and overload recovery |
| Connection/session/install scale | Not accepted; F-266 conntrack close blocks claims | Correct close first, then held-session CPS, 8k PDR and insert latency/rate |
| Latency and mixed-feature soak | Pending | Latency beside offloaded traffic; 30-minute error/resource gate |
| WireGuard | Software on both stacks; steering decision pending | Fresh tunnel per rep, eth4 bidir RPS A/B and eth3 10 reps |
| IPsec, multicast, tunnels, QoS, remaining bridge/VLAN/PPPoE gaps | Not implemented in full | Implementation plus all per-feature gates before performance verdicts |

Vendor external 20k CPS / 8k-session PDR claims are other-rig targets.
Vendor insert rate ≥2,370/s is a measured lower bound, not a new same-session
result. Neither substitutes for a repeat on this rig.

## Reusable measurement controls

Quick functional driver: `bin/testrig-offload-quick.sh [cell ...]`;
`unicast`, `nat`, `vlan`, `pppoe-down`, `pppoe-up`, each `-v4`/`-v6`.
Combos `nat-pppoe-up-v4` and `nat-vlan-v4` are explicit opt-ins.
Use `bin/testrig-combo-matrix.sh setup` after a DUT power cycle restores
links; Dell runtime routes can disappear on link flap.

For ASK2, use per-record `pkt_count` growth, not a summed delta across
records that can age out. Quick-harness rate ratio ≥0.9 is HW,
0.1–0.9 PARTIAL, <0.1 SW; ACK-direction offload can put the ratio near 1.2.
For vendor, establish bulk kernel RX bypass, not merely a successful insert.
Require wire delivery and sane CPU/error counters as well.

Use iperf3 3.20 zero-copy `-Z`; iperf2 `-Z` means TCP congestion selection,
not zero-copy. Keep identical masks, MTU/MSS, streams, duration, direction,
warm-up, topology and offload proof on both boards. Record any generator
limitation rather than assigning it to the DUT.

Historical generator tuning: performance governor/EPP, mlx4 RX/TX rings
4096, `net.core.{rmem,wmem}_max=67108864`, TCP buffer maxima 64 MiB,
`netdev_max_backlog=250000`; use `/sbin/ethtool` and `/sbin/sysctl`.
Tuning is runtime and must be verified before a campaign, never changed
mid-campaign. PPPoE specifically requires dell1 receive RPS because its NIC
cannot RSS-hash PPPoE. Archive contains the exact older 40 s dual-client
protocol and baseline3 results; use it only to reproduce those results.

### PPPoE correctness controls

`bin/testrig-pppoe-frag.py ask2|vendor`: 6000 UDP datagrams at 500 pps,
1500-byte IP packets into a 1492-byte session. Warm with 1500 small datagrams
on the same tuple. Capture receiver intact delivery/reassembly and DUT kernel
RX/FMan fragment counters, not TCP throughput alone.

Flush dell2's cached PMTU before each case and abort if cached `mtu` remains.
A new IPv6 flow whose first packet is oversize stays software/PTB, so only a
warm flow proves HW fragmentation. The vendor's historical fragments
reassembled but had zeroed UDP headers (0/6000 intact); repeat before claiming
that defect on current conditions. Known ASK2 concurrent IPv6 fragment-ID
reuse remains a limitation; preserve `ipv6_hw_frag=0` opt-out testing.

### WireGuard controls

`bin/wg-parity.sh 106|185`, table `bin/wg-parity-table.py`: DUT terminates
one tunnel, four TCP streams, MTU 1420, 30 s, a fresh tunnel every rep;
worker placement makes single-tunnel results bimodal. Prior vendor-style
single-CPU masks (eth3=2, eth4=1, flows 0) outperformed the VyOS default
RPS/RFS mask that excludes CPU0. Those results are archived, not a shipping
policy decision. Re-test current images before changing patch 031.

## Historical evidence

- [Pre-cleanup parity scoreboard](archive/ASK2-VENDOR-PARITY-2026-10-15.md):
  single-run tables, missing-feature inventory, F-266 setup/readback and
  conntrack-linger measurements.
- [Old A6 results](archive/ASK2-VS-VENDOR-THROUGHPUT.md): 2026-10-06
  throughput/retransmit tables and RX pool/alignment diagnosis.
- [Completed Phase 0.4 report](archive/ASK2-PHASE0.4-PERFORMANCE-REPORT.md):
  historical packet-rate/IMIX/64k-flow evidence.
- [Rewrite review](archive/ASK2-REWRITE-PLAN.md): byte-exact vendor records,
  RX register diff, rejected hypotheses and the old acceptance protocol.

Historical PASS does not close a current-image or missing-feature gate.
