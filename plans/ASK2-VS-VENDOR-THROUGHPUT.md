# ASK2 vs vendor NXP ASK — routed throughput (A6)

Measured 2026-10-05. This is the A6 gate scoreboard for
`ASK2-REWRITE-PLAN.md` Phase 1; re-run and replace the tables when either side
changes.

## Setup

| | ASK2 | NXP ASK (vendor) |
|---|---|---|
| Board | `.185` | `.106` |
| Software | VyOS image `2026.10.05-1355-rolling`, `dpaa1` `1e8865d5` (patches 0215–0218), kernel 6.18.54-vyos | Vendor's own NXP ASK on OpenWrt (Mono 25.12.5, kernel 6.12.103) |
| Offload proof | ehash hit counters: ~16M hits per unidir run, 24–33M per bidir run | Kernel RX packets in the window: 1–4 per run |
| Raw data | `/mnt/builds/ask2-review/oracle/a6-ask2-185.csv` | `/mnt/builds/ask2-review/oracle/a6-vendor-owrt-106.csv` |

Method (binding, `ASK2-REWRITE-PLAN.md` §8): `oracle/baseline3.sh`, iperf3 `-Z`,
40 s per run, unidir `-P 8`, bidir two concurrent `-P 16` clients, tuned Dells
(dell1 `.112` ↔ DUT ↔ dell2 `.113`, rig `bin/testrig-combo-matrix.sh`), routed
with no NAT on every combo, median of 3 runs. Throughput is aggregate Gbit/s.
Retransmits are TCP retransmits per 40 s run, summed over all streams. Warm boot
on both boards.

## Unidirectional

| Combo | ASK2 Gbit/s | ASK2 retransmits | Vendor Gbit/s | Vendor retransmits |
|---|---|---|---|---|
| port→port v4 | 9.14 | 708k | 9.34 | 10 |
| port→port v6 | 9.04 | 711k | 9.22 | 2 |
| vlan→port v4 | 9.34 | 834 | 9.32 | 1 |
| vlan→port v6 | 9.18 | 620 | 9.19 | 5 |
| vlan→vlan v4 | 9.34 | 4 | 9.34 | 0 |
| vlan→vlan v6 | 9.19 | 2 | 9.20 | 45 |

## Bidirectional

| Combo | ASK2 Gbit/s | ASK2 retransmits | Vendor Gbit/s | Vendor retransmits | ASK2 vs vendor |
|---|---|---|---|---|---|
| port↔port v4 | 13.59 | 10.70M | 17.67 | 339k | −23 % |
| port↔port v6 | 13.35 | 10.00M | 17.32 | 294k | −23 % |
| vlan↔port v4 | 10.27 | 14.96M | 12.84 | 9.77M | −20 % |
| vlan↔port v6 | 9.72 | 15.04M | 12.53 | 8.89M | −22 % |
| vlan↔vlan v4 | 12.53 | 13.71M | 17.61 | 323k | −29 % |
| vlan↔vlan v6 | 12.66 | 14.13M | 17.20 | 210k | −26 % |

## Reading the results

- **Unidir throughput is at parity**, except port→port, which is 2 % lower.
- **Untagged ingress loses frames even unidir.** Port→port runs show a
  steady ~710k retransmits (706k–712k on every run, v4 and v6). VLAN→VLAN
  unidir loses almost nothing. The loss is that steady and needs no
  overload, so it looks like a fixed mechanism, not congestion.
- **Bidir fails A6 on every combo.** ASK2 is 20–29 % below the vendor. The
  vendor's vlan↔port loss (~9M retransmits) is the known MAC RX FIFO
  overflow, so that combo is partly a silicon limit. Port↔port and
  vlan↔vlan run near 17.5 Gbit/s on the vendor with low loss.
- **Bidir is lopsided on port↔port.** One direction gets 5.1–6.5 Gbit/s
  and the other gets 8.0–9.2.
- **Ruled out:** per-port BMI resources (tasks, DMAs, FIFO size and the
  `fmbm_rfp` thresholds match or favour ASK2). See `ASK2-REWRITE-PLAN.md`,
  "Port-resource hypothesis: FALSIFIED".
