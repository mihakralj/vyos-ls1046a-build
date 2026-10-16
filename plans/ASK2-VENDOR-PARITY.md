# ASK2 vs NXP ASK: current parity evidence

**Updated 2026-10-16. Full feature parity is not reached.** The authoritative gap
inventory and task order are in [`ASK2-MASTER-PLAN.md`](ASK2-MASTER-PLAN.md)
§1.2/§3/§4. This file owns comparison evidence, not another implementation
backlog. Completed and provisional single-run tables were moved verbatim to
[`archive/ASK2-VENDOR-PARITY-2026-10-15.md`](archive/ASK2-VENDOR-PARITY-2026-10-15.md).

## Completed 10×10 campaign

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
| Final state | 480/480 attempts; completed 2026-10-16 02:54:36 UTC; `COMPLETE_WITH_ISSUES` (one partial-offload run) |
| Summary artifacts | `summary.csv`, `summary.json`, `measurements.csv`, and per-run CSV/JSON on dell2 |

The campaign tested only the listed routed/NAT/VLAN paths. It excludes PPPoE,
bridging, WireGuard, packet rate, connection scale and latency. Passing all
its cells would establish only measured-path throughput parity, not full
feature parity.

## Analysis and verdict contract

Preserve `measurements.csv` and every per-run CSV/JSON, including errors and
non-hardware runs. `summary.csv`/`summary.json` report paired means and
medians, sample SD, missing/non-HW pairs, and 95% paired bootstrap intervals
for ASK2-minus-vendor throughput. The raw `measurements.csv` and per-run
artifacts retain CPU, retransmits, and other run-level diagnostics. The
summary uses complete HW-proven pairs, 20,000 percentile-bootstrap resamples
per cell, and seed 10551. No slow or high-CPU result is trimmed as "jitter";
the partial run remains in the raw data and is identified below.

Intervals are exploratory per cell, not campaign-wide multiplicity-adjusted
claims or proof of equivalence. **A lower ASK2 paired mean in any cell is a
FAIL**, regardless of gap size; report uncertainty separately. Missing or
invalid pairs are incomplete evidence, never a PASS. Throughput is iperf3
payload Gbit/s; bidirectional values sum both directions.

The earlier one-run re-test showed both stacks shifting by about an
MSS-sized step (~1.3%). It does not demonstrate an ASK2 slowdown and is
superseded by the final campaign below. Its exact numbers, directions and
raw-data paths are in the archive. Do not mix older 40 s/two-client medians
or other images into this campaign's baseline.

## Final 10×10 results

The tables give each cell's mean payload throughput, not a best run.
`Δ` is ASK2 minus NXP ASK; its 95% paired bootstrap interval is in Gbit/s.
`PASS`/`FAIL` applies the strict mean rule above, not the interval: any lower
ASK2 mean is a `FAIL`, even when the interval includes zero. All values use
complete pairs with hardware forwarding on both DUTs. `uni` cells are one
way; `-rev` means the reverse client direction. `unicast` is routed traffic,
`nat` is NAT44/NAT66, `vlan` is VID translation, `vlanport` is VLAN-to-plain
port (strip), and `portvlan` is plain-port-to-VLAN (add).

### One-way cells

| Cell | NXP ASK mean | ASK2 mean | Δ | 95% paired CI for Δ | Verdict | HW pairs |
|---|---:|---:|---:|---:|---|---:|
| Routed IPv4, eth3→eth4 | 9.2822 | 9.2822 | +0.0000 (+0.000%) | [−0.0014, +0.0011] | PASS | 10/10 |
| Routed IPv6, eth3→eth4 | 9.1545 | 9.1539 | −0.0006 (−0.007%) | [−0.0025, +0.0012] | FAIL | 10/10 |
| NAT44, eth3→eth4 | 9.2816 | 9.2811 | −0.0005 (−0.005%) | [−0.0021, +0.0011] | FAIL | 10/10 |
| NAT66, eth3→eth4 | 9.1540 | 9.1538 | −0.0002 (−0.002%) | [−0.0015, +0.0011] | FAIL | 10/10 |
| VLAN translate IPv4, eth3→eth4 | 9.2707 | 9.2577 | −0.0130 (−0.140%) | [−0.0368, −0.0003] | FAIL | 10/10 |
| VLAN translate IPv6, eth3→eth4 | 9.1300 | 9.1307 | +0.0007 (+0.008%) | [−0.0003, +0.0017] | PASS | 10/10 |
| Routed IPv4, eth4→eth3 | 9.2832 | 9.3202 | +0.0370 (+0.399%) | [+0.0012, +0.0748] | PASS | 10/10 |
| Routed IPv6, eth4→eth3 | 9.1539 | 9.1657 | +0.0118 (+0.129%) | [−0.0022, +0.0372] | PASS | 10/10 |
| NAT44, eth4→eth3 | 9.2808 | 9.3187 | +0.0379 (+0.408%) | [+0.0023, +0.0744] | PASS | 10/10 |
| NAT66, eth4→eth3 | 9.1552 | 9.1659 | +0.0107 (+0.117%) | [−0.0034, +0.0363] | PASS | 10/10 |
| VLAN translate IPv4, eth4→eth3 | 9.2584 | 9.2836 | +0.0252 (+0.272%) | [−0.0004, +0.0623] | PASS | 10/10 |
| VLAN translate IPv6, eth4→eth3 | 9.1314 | 9.1544 | +0.0230 (+0.252%) | [−0.0026, +0.0592] | PASS | 10/10 |
| VLAN→port IPv4 (strip), eth3→eth4 | 9.2592 | 9.2590 | −0.0002 (−0.002%) | [−0.0013, +0.0009] | FAIL | 10/10 |
| Port→VLAN IPv4 (add), eth4→eth3 | 9.2625 | 9.2864 | +0.0239 (+0.258%) | [−0.0013, +0.0608] | PASS | 10/10 |
| VLAN→port IPv6 (strip), eth3→eth4 | 9.1428 | 9.1303 | −0.0125 (−0.137%) | [−0.0361, 0.0000] | FAIL | 10/10 |
| Port→VLAN IPv6 (add), eth4→eth3 | 9.1339 | 9.1947 | +0.0608 (+0.666%) | [+0.0250, +0.0964] | PASS | 10/10 |

### Bidirectional cells

| Cell | NXP ASK mean | ASK2 mean | Δ | 95% paired CI for Δ | Verdict | HW pairs |
|---|---:|---:|---:|---:|---|---:|
| Routed IPv4 | 17.9932 | 18.1304 | +0.1372 (+0.763%) | [+0.0702, +0.1996] | PASS | 10/10 |
| Routed IPv6 | 17.6136 | 17.8974 | +0.2838 (+1.611%) | [+0.1196, +0.5051] | PASS | 10/10 |
| NAT44 | 17.8798 | 18.1681 | +0.2883 (+1.613%) | [+0.1141, +0.5334] | PASS | 9/10 |
| NAT66 | 17.2940 | 17.9061 | +0.6121 (+3.539%) | [+0.3184, +0.9102] | PASS | 10/10 |
| VLAN translate IPv4 | 17.9906 | 18.1525 | +0.1619 (+0.900%) | [+0.0690, +0.2581] | PASS | 10/10 |
| VLAN translate IPv6 | 17.7910 | 17.9007 | +0.1097 (+0.617%) | [+0.0461, +0.1651] | PASS | 10/10 |
| VLAN↔port strip/add IPv4 | 12.9309 | 12.9983 | +0.0674 (+0.521%) | [+0.0115, +0.1215] | PASS | 10/10 |
| VLAN↔port strip/add IPv6 | 12.7994 | 12.7526 | −0.0468 (−0.366%) | [−0.1653, +0.0402] | FAIL | 10/10 |

Under the strict mean rule, **7 of 24 cells are FAIL**: six one-way cells
(routed IPv6 forward, NAT44/NAT66 forward, VLAN-translate IPv4 forward,
VLAN→port IPv4/IPv6) and bidirectional VLAN↔port IPv6. Four of those
one-way deficits are extremely small (0.002–0.007%); they remain FAIL by
contract and need follow-up rather than being reclassified as ties.

One excluded non-HW pair accounts for the campaign's `COMPLETE_WITH_ISSUES`
status. In round 5, ASK2 `.185` bidirectional NAT44 was `PARTIAL` at
13.898 Gbit/s, with `hw_ratio=0.79`, DUT busy 29.7%, softirq 28.9%, and
118,142 retransmits. The run returned successfully but was not a complete
hardware-forwarded pair, so NAT44 bidirectional has 9 valid pairs. No cause
has been established; the observation must not be dismissed as jitter.
Its raw data is `bidir-nat-v4-185-r05.csv` beside the summary artifacts.

These results are for the `0551` ASK2 image listed above, not the later
F-267/routing-optimization image or the uncommitted PBR next-hop fix. The
campaign did not exercise policy routing.

## Evidence still required

| Surface | Current acceptance state | Required comparison |
|---|---|---|
| Routed v4/v6, NAT44/NAT66, VLAN translate/strip/add | Implemented; 10×10 complete, 7 strict-mean FAIL cells | Investigate the seven lower-mean cells; repeat small deficits under the same alternating protocol |
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
