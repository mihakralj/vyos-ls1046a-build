# ASK2 vs NXP ASK: current parity evidence

**Updated 2026-10-18. Full feature parity is not reached.** The authoritative gap
inventory and task order are in [`ASK2-MASTER-PLAN.md`](ASK2-MASTER-PLAN.md)
§1.2/§3/§4. This file owns comparison evidence, not another implementation
backlog. Completed and provisional single-run tables were moved verbatim to
[`archive/ASK2-VENDOR-PARITY-2026-10-15.md`](archive/ASK2-VENDOR-PARITY-2026-10-15.md).

## Current `.106` rig: OPNsense

On 2026-10-16, `.106` is running OPNsense 26.7.7 on the custom
FreeBSD 15.1-RELEASE-p4 `MONO-GATEWAY` kernel, not the OpenWrt image used
in the completed campaign below. CDX/FCI/auto_bridge and CMM are present.
Keep future OPNsense measurements separate from the historical OpenWrt
baseline; loaded modules alone do not prove hardware forwarding.
On 2026-10-18 the vendor updated `.106` to `mono-gateway` 26.7.7_8; the
current OPNS results are the [26.7.7_8 battery](#opnsense-106-full-battery-on-2677_8-2026-10-18).

The test interfaces are saved in OPNsense `/conf/config.xml`, including
IP Alias virtual addresses for the harness's pinned `.185` gateways.
Native `.106` addresses remain available on each subnet.

| Rig connection | OPNsense interface | IPv4 addresses (/24) | IPv6 addresses (/64) |
|---|---|---|---|
| dell1 X710 p2 `enp1s0f1np1`, namespace `n106` | `dtsec3` (`opt1`, logical eth3) | `10.99.1.106`, `10.99.1.185` | `fd99:1::106`, `fd99:1::185` |
| Same link, VLAN 10 | `vlan01` (`opt3`), parent `dtsec3` | `10.99.10.106`, `10.99.10.185` | `fd99:10::106`, `fd99:10::185` |
| dell2 X710 p2 `enp1s0f1np1`, namespace `n106` | `dtsec4` (`opt2`, logical eth4) | `10.99.2.106`, `10.99.2.185` | `fd99:2::106`, `fd99:2::185` |
| Same link, VLAN 20 | `vlan02` (`opt4`), parent `dtsec4` | `10.99.20.106`, `10.99.20.185` | `fd99:20::106`, `fd99:20::185` |

Both physical links are active at 10G, MTU 1500. Management remains
`192.168.1.106/16` on `dtsec0`, default gateway `192.168.1.1`; management
and WAN configuration were unchanged. IPv4 and IPv6 forwarding are enabled.
The Dell addresses, routes and source-policy tables were already correct
and were not changed; `.185` remains on the Dells' other X710 ports in the
root namespace. Do not merge the namespaces: the pinned gateways overlap.
The pre-change backup is `/conf/backup/pre-testrig-20261016.xml` on `.106`.

Connectivity acceptance: all six paths (plain, VLAN-to-VLAN, VLAN-to-plain,
each IPv4/IPv6) passed three-packet pings in both directions with zero loss.
Twelve short TCP checks, initiated from dell2 through dell1, also passed
(forward and reverse for each path, requested 10 Mbit/s, two seconds).
These are connectivity checks, not throughput or hardware-offload results.

For the campaign below, pf was enabled with NAT rules in the `testrig`
anchor, which the harness clears and rewrites on every run. The harness copy
`testrig-offload-quick-opns.sh` (`DUT_KIND=opnsense`) adds a FreeBSD backend
for interface mapping, pf/NAT, CPU sampling and CDX hardware-bypass
counters. The supplemental campaign below adds runtime-only PPPoE and
WireGuard test configurations. Bridge tests remain outside its scope.
Do not run the OpenWrt backend against `.106`.

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

## ASK2 rerun on the `2149` release code (2026-10-17)

The `2026.10.16-2149-rolling` release changed the ASK2 forwarding path after
`0551` (`dfe66471`, `2946a038`, `e9bed698`, `ae98749c`), so the ASK2 side of
the 10×10 campaign was repeated. The OpenWrt side cannot be repeated because
`.106` now runs OPNsense.

| Property | Contract |
|---|---|
| ASK2 | `.185`, `2026.10.16-2012-rolling` (same code as the `2149` release), `6.18.55-vyos`, up 2 h 51 m at start; no cold boot before the run |
| Protocol | Same 24 cells, 30 s, iperf3 `-Z -P8`, steady from 10 s, `SAMPLE=0`, 10 rounds, same cell order; single board (no pairing) |
| Harness | `testrig-offload-quick.sh` sha256 `c863d424…e1795d588`, byte-identical to the `0551` campaign copy |
| Controller | dell2-local systemd unit `testrig-ask2-10x10-2149`, `ask2-campaign.py` (the `0551` controller with `.106` removed) |
| Output | dell2 `~/testrig/campaigns/ask2-10x10-2149-20261017T0000Z/`; `compare.py` → `compare.csv`/`compare.json` |
| Final state | 240/240 attempts, all HW, 0 issues; 2026-10-16 23:58:46 → 2026-10-17 02:51:20 UTC; `COMPLETE` |
| Post-run | `ask-check` 36/36, no kernel errors; `.185` controls (addresses, MTU, link state) identical before and after |

`Δ0551` is the new mean minus the `0551` `.185` mean. Its 95% interval is an
unpaired bootstrap, because the runs are on different days. `ΔOWRT` uses the
historical OpenWrt `.106` mean (all 10 HW runs). That makes the strict-mean
verdict a cross-day, unpaired comparison: valid for spotting regressions, but
weaker than the paired `0551` verdicts above.

| Cell | ASK2 2149 mean (SD) | `0551` mean | Δ0551 [95% CI] | OWRT mean | ΔOWRT | Strict vs OWRT (`0551`) | OPNS mean |
|---|---:|---:|---:|---:|---:|---|---:|
| Routed IPv4, 3→4 | 9.2821 (0.0012) | 9.2822 | −0.0001 [−0.0011, +0.0009] | 9.2822 | −0.0001 | FAIL (PASS) | 9.283 |
| Routed IPv6, 3→4 | 9.1543 (0.0018) | 9.1539 | +0.0004 [−0.0011, +0.0019] | 9.1545 | −0.0002 | FAIL (FAIL) | 9.229 |
| NAT44, 3→4 | 9.2808 (0.0022) | 9.2811 | −0.0003 [−0.0021, +0.0015] | 9.2816 | −0.0008 | FAIL (FAIL) | 9.295 |
| NAT66, 3→4 | 9.1540 (0.0013) | 9.1538 | +0.0002 [−0.0013, +0.0019] | 9.1540 | +0.0000 | PASS (FAIL) | 9.241 |
| VLAN translate IPv4, 3→4 | 9.2585 (0.0026) | 9.2577 | +0.0008 [−0.0010, +0.0025] | 9.2707 | −0.0122 | FAIL (FAIL) | 9.284 |
| VLAN translate IPv6, 3→4 | 9.1309 (0.0019) | 9.1307 | +0.0002 [−0.0012, +0.0016] | 9.1300 | +0.0009 | PASS (PASS) | 9.145 |
| Routed IPv4, 4→3 | 9.2815 (0.0028) | 9.3202 | −0.0387 [−0.0758, −0.0033] | 9.2832 | −0.0017 | FAIL (PASS) | 9.308 |
| Routed IPv6, 4→3 | 9.1668 (0.0381) | 9.1657 | +0.0011 [−0.0349, +0.0363] | 9.1539 | +0.0129 | PASS (PASS) | 9.179 |
| NAT44, 4→3 | 9.2816 (0.0025) | 9.3187 | −0.0371 [−0.0746, −0.0008] | 9.2808 | +0.0008 | PASS (PASS) | 9.099 |
| NAT66, 4→3 | 9.1798 (0.0518) | 9.1659 | +0.0139 [−0.0236, +0.0515] | 9.1552 | +0.0246 | PASS (PASS) | 9.254 |
| VLAN translate IPv4, 4→3 | 9.2586 (0.0028) | 9.2836 | −0.0250 [−0.0613, +0.0004] | 9.2584 | +0.0002 | PASS (PASS) | 9.270 |
| VLAN translate IPv6, 4→3 | 9.1421 (0.0379) | 9.1544 | −0.0123 [−0.0496, +0.0243] | 9.1314 | +0.0107 | PASS (PASS) | 9.201 |
| VLAN→port IPv4 (strip), 3→4 | 9.2707 (0.0378) | 9.2590 | +0.0117 [−0.0017, +0.0361] | 9.2592 | +0.0115 | PASS (FAIL) | 9.270 |
| Port→VLAN IPv4 (add), 4→3 | 9.2872 (0.0521) | 9.2864 | +0.0008 [−0.0373, +0.0393] | 9.2625 | +0.0247 | PASS (PASS) | 9.265 |
| VLAN→port IPv6 (strip), 3→4 | 9.1430 (0.0384) | 9.1303 | +0.0127 [−0.0007, +0.0376] | 9.1428 | +0.0002 | PASS (FAIL) | 9.143 |
| Port→VLAN IPv6 (add), 4→3 | 9.1468 (0.0381) | 9.1947 | −0.0479 [−0.0857, −0.0009] | 9.1339 | +0.0129 | PASS (PASS) | 9.151 |
| Bidir routed IPv4 | 18.1172 (0.0439) | 18.1304 | −0.0132 [−0.0564, +0.0348] | 17.9932 | +0.1240 | PASS (PASS) | 15.935 |
| Bidir routed IPv6 | 17.8366 (0.0473) | 17.8974 | −0.0608 [−0.0920, −0.0302] | 17.6136 | +0.2230 | PASS (PASS) | 15.667 |
| Bidir NAT44 | 18.1252 (0.0224) | 18.1681 | −0.0429 [−0.0599, −0.0264] | 17.9047 | +0.2205 | PASS (PASS) | 16.166 |
| Bidir NAT66 | 17.8530 (0.0389) | 17.9061 | −0.0531 [−0.0812, −0.0236] | 17.2940 | +0.5590 | PASS (PASS) | 15.998 |
| Bidir VLAN translate IPv4 | 18.1476 (0.0269) | 18.1525 | −0.0049 [−0.0269, +0.0154] | 17.9906 | +0.1570 | PASS (PASS) | 13.309 |
| Bidir VLAN translate IPv6 | 17.8518 (0.0386) | 17.9007 | −0.0489 [−0.0749, −0.0215] | 17.7910 | +0.0608 | PASS (PASS) | 14.110 |
| Bidir VLAN↔port IPv4 | 12.9579 (0.1142) | 12.9983 | −0.0404 [−0.1311, +0.0513] | 12.9309 | +0.0270 | PASS (PASS) | 11.437 |
| Bidir VLAN↔port IPv6 | 12.7095 (0.1485) | 12.7526 | −0.0431 [−0.1762, +0.0959] | 12.7994 | −0.0899 | FAIL (FAIL) | 11.389 |

The OPNS column is the 2026-10-16 (`26.7.7_4`) OPNsense campaign's
`summary.csv` mean over HW runs; the 26.7.7_8 battery is in its own section below.
For the two round-10 SW cells it averages rounds 1–9 only, so it differs
slightly from the redo-substituted three-way table below.

**Reading.** No regression in offload: every run was HW, and the `0551`
partial-offload anomaly (bidir NAT44 round 5) did not recur (10/10 HW).
Busy stayed at 0.2–0.5%. The one-way steady levels are unchanged. Three
one-way cells have an interval that excludes zero (routed IPv4 4→3, NAT44
4→3 and port→VLAN IPv6); VLAN translate IPv4 4→3 just includes it. All four
come from the known two-level throughput step. In those cells `0551` had
2–4 runs on the upper step (≈9.40 v4 / 9.25 v6); this run had 0–1. For
example, routed IPv4 4→3 was 9.279–9.288 in both campaigns, plus three
`0551` runs at 9.401–9.410. That is a per-run TCP/MSS-step lottery, not a
forwarding change.

Every bidirectional cell is lower than `0551`, by 0.03–0.34%. The interval
excludes zero for routed IPv6 (−0.34%), NAT44 (−0.24%), NAT66 (−0.30%) and
VLAN translate IPv6 (−0.27%). The cause is not established. This campaign
cannot separate an image change from rig drift (different day, unpaired, no
cold boot). It needs a same-session A/B against the `0551` image before it
is called either. All bidirectional cells except VLAN↔port IPv6 remain
0.2–3.2% above the historical OpenWrt means, and 11.6–36.4% above OPNsense.

Under the strict mean rule against the historical OpenWrt means, 6 of 24
cells are FAIL (`0551`: 7). Routed IPv6 3→4, NAT44 3→4, VLAN translate
IPv4 3→4 and bidir VLAN↔port IPv6 remain FAIL. NAT66 3→4 and VLAN→port
IPv4/IPv6 strip now pass. Routed IPv4 3→4 (−0.0001) and routed IPv4 4→3
(−0.0017, the step-lottery case) are new FAILs. Only VLAN translate IPv4
3→4 (−0.13%) and bidir VLAN↔port IPv6 (−0.70%) are larger than ±0.01%.
Both were already FAIL at `0551`, and both remain the cells to investigate.

### Investigation: `0551` → `2149` bidirectional delta

The post-`0551` code-delta review found no single steady-state forwarding
change that explains all eight lower bidirectional means:

- F-267 changes ehash record/context allocation from separate coherent
  allocations to packed 512-byte DMA-pool blocks, and demotes per-insert logs.
  It preserves record contents and synchronization, but changes DMA address
  placement and page locality; a memory-locality effect remains possible.
- `2946a038` changes next-hop recovery for direct-xmit flowtable tuples during
  flow insertion/replacement, which changes the egress-MAC inputs used to
  build FE actions. Correlate it across every direct-xmit cell; current
  evidence does not show whether any resolved MAC or action bytes changed.
- `e9bed698` changes generic-netlink authorization, and `ae98749c` closes the
  flowtable admission race for administratively disabled ports. These are
  control/admission-path changes rather than established per-packet work.
- The factory-default change in `c84499e5` is not active in the installed
  campaign configuration; the 2012 upgrade preserved the lab configuration.

This triage does not establish causality. Run the eight bidirectional cells
on `0551` and `2149` in the same session, alternating image order across at
least three paired rounds, with matching cold-boot state and unchanged rig
configuration. Record per-cell hardware ratio, delivered rate, DUT busy and
softirq, retransmits, TCP/MSS mode, port counters, image/boot IDs and
`fe_ehash_stats`. First confirm the observed two-level TCP/MSS rate step and
separate any software-fallback run; do not compare a partial-HW result as a
forwarding-rate sample. Keep VLAN-translate IPv4 3→4 and bidirectional
VLAN↔port IPv6 as independent vendor-gap investigations.

**2026-10-18 raw-data follow-up (analysis only; no new campaign):**

- For VLAN-translate IPv4 3→4, the historical NXP `.106` series has one
  9.376 Gbit/s run and nine runs from 9.256–9.262. ASK2 was tightly clustered
  at 9.256–9.260 on `0551` and 9.253–9.262 on `2149`; every run was hardware
  verified. The strict-mean deficit is therefore dominated by one NXP upper
  rate-step sample, while ASK2 matches the NXP lower cluster. This narrows the
  lead to the campaign's TCP/MSS rate-step variance; it does not waive the
  strict-mean FAIL or prove equal peak capacity.
- For bidirectional VLAN↔port IPv6, steady per-direction means from the raw
  iperf JSON (`intervals[10:]`) were: `0551` ASK2 6.282 Gbit/s VLAN→port
  (strip) and 6.471 port→VLAN (add); NXP 6.354 and 6.445; `2149`-code ASK2
  6.321 and 6.389. Relative to `0551`, strip improved while add declined.
  This is a useful direction-specific diagnostic, not a causal result: the
  campaigns are not same-session and no FIFO counters were captured for this
  extraction.
- The `0551`/`2149` A/B remains blocked on an operator image switch. The
  installed DUT is the `2012` image carrying the `2149` code; no image was
  installed or switched during this follow-up.

## OPNsense `.106` 10-run campaign (2026-10-16)

| Property | Contract |
|---|---|
| Board | `.106`, OPNsense 26.7.7, FreeBSD 15.1-RELEASE-p4 `MONO-GATEWAY`, CDX `5.03.1-fbsd`, pf + CMM |
| Protocol | Same 24 cells, 30 s, iperf3 `-Z -P8`, steady from 10 s, 10 rounds; single board (no pairing) |
| Controller | dell2-local systemd unit `opns-parity.service`, `opns-campaign.py`; harness `testrig-offload-quick-opns.sh` sha256 `feb7b48d…24824ce` |
| Output | dell2 `~/testrig/campaigns/opns-10x10-20261016T0643Z/` |
| Final state | 240/240 attempts, 06:43–09:25 UTC; `COMPLETE_WITH_ISSUES` (two round-10 SW runs, redone below) |

**Round-10 SW fallback and redo.** In round 10, bidirectional VLAN
translate IPv4 (1.674 Gbit/s) and VLAN↔port IPv4 (1.749 Gbit/s) ran in
software: `hw_pkts=0`, DUT busy ~80%. All nine earlier rounds of both cells
were HW. At 13:52 UTC three reruns of each cell were still SW (1.60–1.72
Gbit/s), and so were 15 s one-way probes of `vlan-v4` and `vlanport-v4`.
Every VLAN-10-ingress IPv4 flow had stopped offloading. Meanwhile, the
`vlan-v6` bidirectional and `unicast-v4` bidirectional probes still ran HW.
ARP/NDP entries and pf were intact. `service cmm restart`
(pid 29761 → 18229, `dev.cdx.stats.active_connections` 53 → 0) restored HW
offload immediately. Three full-protocol reruns of each cell after the
restart were all HW: vlan-v4 bidir 13.428 / 12.903 / 13.432, vlanport-v4
bidir 11.355 / 11.512 / 11.348 Gbit/s. The OPNS values for these two cells
below use rounds 1–9 plus the first post-restart rerun as round 10. The SW
runs remain in `measurements.csv`; reruns are in `redo-20261016T1355Z/`
(pre-restart, SW) and `redo-20261016T1405Z-postcmm/` (post-restart, HW).
Root cause is not established: a vendor CMM/CDX state fault that hit
VLAN-ingress IPv4 only after ~2.7 h of churn. Keep it as a vendor stability
observation, not jitter. During the failure, `dmesg` showed large
`dtsec3`/`dtsec4` RX error counts with `fd_status=0x00080000` (phy=1). Their
relation to the fault has not been checked.

One NAT44 eth4→eth3 run was HW but slow (7.320 Gbit/s); it stays in the
mean (9.10; median 9.28).

**Supplemental missing-cell measurements (2026-10-16, completed 15:13:46 UTC).**
Artifacts and the runtime-only controller are on dell2 at
`~/testrig/campaigns/opns-missing-20261016/`. NAT across VLANs uses the same
30 s, eight-stream quick harness with steady state from 10 s. Three runs
per family were all HW (`hw_ratio=1.00`): IPv4 9.261 / 9.257 / 9.266
Gbit/s (mean 9.2613), IPv6 9.101 / 9.098 / 9.094 (mean 9.0977).
The table includes these means.

The initial PPPoE session used a hand-started OPNS `pppoe10`
(mpd5/Netgraph, `dtsec3`, MTU 1492), without an OPNsense PPP device or assignment,
with the kernel-mode concentrator in dell1's `n106` namespace and dell1
RPS enabled by the quick harness. Three repetitions per cell finished:

| PPPoE cell | IPv4 runs (Gbit/s) | IPv6 runs (Gbit/s) | IPv4 / IPv6 mean | Offload |
|---|---|---|---|---|
| Decap, eth3→eth4 | 0.628 / 0.633 / 0.789 | 0.722 / 0.723 / 0.770 | 0.6833 / 0.7383 | SW, all six runs |
| Encap, eth4→eth3 | 0.826 / 0.822 / 0.815 | 0.924 / 0.972 / 0.944 | 0.8210 / 0.9467 | SW, all six runs |

**These initial PPPoE results are superseded in the summary by the native
OPNsense interface retest below.** The IP-stack counter check reports
`hw_ratio=0.00` in all 12 runs, with DUT busy 42.7–58.7%.
CMM's PF event ring also saturated during software traffic; restarting CMM
with standard descriptors redirected drained the ring while idle but did
not restore PPPoE offload or prevent dropped events under load. The hand-started
session was removed after the measurements. Earlier diagnostic pilots
remain separately in `unhealthy-pilots/` and are not included in these
three-run means.

**Native OPNsense PPPoE retest and vendor diagnostics (2026-10-16,
16:38–16:44 UTC).** The vendor identified the missing OPNsense-managed
PPP device/assignment as a likely reason for the software fallback. The
retest created the equivalent of an Interfaces → Point-to-Point → Devices
PPPoE entry (`pppoe0`, parent `dtsec3`, MTU/MRU 1492), assigned it to
enabled interface `opt5` (`ipaddr=pppoe`, `ipaddrv6=pppoev6`), and invoked
OPNsense's `interface_ppps_configure('opt5')`. OPNsense generated
`/var/etc/mpd_opt5.conf` and started its own `mpd5`, including the native
link-up/down scripts and Netgraph rename helper. This was configured through
OPNsense's PHP configuration functions, not browser automation or another
hand-written mpd configuration. IPv6CP negotiated; `fd99:50::10/64` was added
to the established session for the fixed test subnet. The concentrator,
generator RPS and traffic settings matched the earlier test.

| Native PPPoE cell | IPv4 Gbit/s | IPv6 Gbit/s | Offload | DUT busy |
|---|---|---|---|---|
| Decap, eth3→eth4 | 9.340 | 9.111 | HW, both families; ratio 1.00 | 0.3% / 2.7% |
| Encap, eth4→eth3 | 9.230 | 9.097 | HW, both families; ratio 1.00 | 0.3% / 0.3% |

These are one 30 s run per cell, steady from 10 s, eight streams, not new
three-run means. They confirm native PPPoE hardware forwarding and replace
the misconfigured software values in the summary; fragmentation correctness
was not retested. No new cold boot preceded these diagnostics.

For NAT diagnostics, CMM was stopped as a service and run foreground with
`cmm -f -d 3 2>&1 | tee /root/cmm-debug.log`. NAT across VLANs reached
9.266 Gbit/s and plain NAT44 reached 9.284 Gbit/s, both ratio 1.00/HW.
The log shows `vlan01`/`vlan02` registration and explicit `ct4 offloaded`
entries with translated reply tuples for both tests. While each test was
active, `pfctl -a testrig -sn` returned, respectively:

```text
nat on vlan02 inet from 10.99.10.0/24 to any -> 10.99.20.185
nat on dtsec4 inet from 10.99.1.0/24 to any -> 10.99.2.185
```

CMM received SIGINT after capture and was restored to service mode
(`/usr/local/sbin/cmm -d 1`). A subsequent routed IPv4 bidirectional run
measured 16.023 Gbit/s, ratio 1.00/HW and 5,730,996 TCP retransmits.
It used `iperf3 -Z --bidir -P 8 -t 30`: eight TCP streams per direction,
16 total, MTU 1500 and reported MSS 1448. Full data frames are nominally
1514 bytes excluding FCS (1518 including FCS), with smaller ACKs; this is
not fixed-size packet generation.

| Interface | RX packets delta | RX errors delta | Idrop / Oerrs / Drop delta |
|---|---|---|---|
| dtsec3 | 24,028,310 | 2,239,798 | 0 / 0 / 0 |
| dtsec4 | 24,227,700 | 2,188,471 | 0 / 0 / 0 |

Full `sysctl dev.dtsec` snapshots were captured immediately before and
after. This build exposes no RX-error/drop counters there; only `bman_free`
changed (dtsec3 2189→1142, dtsec4 1105→1118, dtsec0 1122→1263).
The error deltas above are from `netstat -I dtsecN -nd -b`, captured alongside
the sysctls. They reinforce the earlier receive-error observation but do
not establish its cause.

Raw CSVs, iperf3 JSON, CMM log, active NAT-rule dumps and full counter
snapshots are in dell2 `~/testrig/campaigns/opns-followup-20261016/`
(diagnostic captures copied there after the run). The temporary PPP
device/assignment, concentrator and pass rules were removed; the original
pf filter hash was restored (`5501cd23a8d0cff2cf0c66cc2f5ba830`), the NAT
anchor was empty and CMM service mode was verified.

WireGuard completed 36/36 runs at 15:01:28 UTC: three fresh-tunnel
repetitions for each encrypted-leg port, inner IP family and direction.
Native FreeBSD `if_wg` terminates the tunnel on OPNS; the peer is in the
appropriate Dell's `n106` namespace. Settings match the archived
WireGuard protocol: IPv4 outer transport, MTU 1420, four TCP streams,
30 s per run, steady state from 10 s, and bidirectional throughput summed
across both directions. Table values are medians, not means. Raw client
and server JSON, `wg.csv`, `wg-summary.json`, and before/after health
snapshots are retained in the supplemental directory. Temporary tunnel
interfaces, routes and filter permissions were removed after each run.
The OPNS values are not a same-session paired comparison with the older
OWRT/VyOS WireGuard measurements. PF and CMM remained enabled: CMM's
dropped-notification counter rose from 68,139,958 to 286,449,922 during
this matrix. These are as-configured tunnel-throughput measurements,
including that control-plane load, not isolated crypto benchmarks.

The 64-byte IPv4 single-flow measurements used kernel pktgen on dell1,
four TX threads and the X710 MAC unicast counters on both Dells, adapting
`testrig-pktgen.sh` only for Dell-local SSH and FreeBSD IP-stack bypass
proof. A plain IPv4 reference first measured 9.284 Gbit/s with
`hw_ratio=1.00`. Each trial primed the UDP flow in both directions, then
sent for approximately 10 s. The final batch contains a ten-step binary
search, three low-loss confirmations, three calibrated fixed-rate trials
and three unpaced floods (19 trials total):

| 64-byte measurement | OPNS result | Conditions |
|---|---|---|
| Highest tested rate confirmed at ≤0.001% loss | 0.8462 Mpps median delivered | 0.857397 Mpps requested; 0.840527–0.847188 Mpps actually offered; three confirmations |
| Loss near 3.14 Mpps offered | 62.86708–62.92991% | 3.134224–3.140420 Mpps actually offered, 1.1638–1.1659 Mpps delivered |
| Unpaced flood | 1.1597 Mpps median delivered | 14.6800–14.7034 Mpps actually offered; 92.10957–92.11260% loss |

All final trials reported kernel bypass share 1.000. This is not proof
that lost frames were forwarded or a diagnosis of where they were lost.
The confirmed low-loss rate is the largest confirmed search candidate,
not an exact physical ceiling. MAC counters are sampled separately and
include incidental unicast traffic: two confirmations had RX exceed TX
by one packet, and the third lost one packet. Their raw signed loss
values (−0.00001%, +0.00001%, −0.00001%) remain in the artifacts; a
one-frame negative count discrepancy is treated as zero loss for the
acceptance check, not as negative physical loss.

An initial 16-trial batch remains in `initial-pps/`. The final
`opns-pps-refined.service` batch increased search resolution from seven
to ten steps, accepted the observed one-frame counter skew, and calibrated
the fixed-rate request from 3.14 to 3.177085 Mpps so the actual offered
rate was near 3.14 Mpps rather than approximately 3.103 Mpps. Final
artifacts are `pps.csv`, `pps-trials.json`, `pps-summary.json`, per-trial
logs and before/after health snapshots in the supplemental directory.
`final-summary.json` records the derived results, measurement counts and
controller/harness SHA-256 hashes. The original pf filters were restored,
temporary tunnel interfaces and WireGuard policy rules were absent on
all hosts, and post-test IPv4/IPv6 forwarding pings both had zero loss.

### Three-way summary (2026-10-16, `26.7.7_4`)

Superseded for OPNS by the [26.7.7_8 summary](#three-way-summary-2026-10-18-2677_8);
kept as the first OPNS session.

OWRT = OpenWrt NXP ASK `.106` and VyOS = ASK2 `.185`, both from the 10×10
campaign above. OPNS = this campaign and its supplements. Verdicts in the
detailed tables compare VyOS against OWRT under the strict mean rule;
OPNS is unpaired context, not a verdict input. Rows
below the VLAN-translate block (NAT across VLANs, PPPoE, WireGuard, 64 B)
are earlier OWRT/ASK2 results carried over from the archive. OPNS NAT
across VLANs, PPPoE, WireGuard and 64-byte results are from the supplemental
measurements above. OPNS PPPoE uses the single-run native-interface HW
retest, not the earlier three-run hand-started-mpd software results.

```text
Gbit/s unless noted               OPNS           OWRT           VyOS
                                  v4     v6      v4     v6      v4     v6
ROUTED
 3->4                            9.28   9.23    9.28   9.15    9.28   9.15
 4->3                            9.31   9.18    9.28   9.15    9.32   9.17
 bidir                          15.94  15.67   17.99  17.61   18.13  17.90
NAT44 / NAT66 (PAT included)
 3->4                            9.30   9.24    9.28   9.15    9.28   9.15
 4->3                            9.10   9.25    9.28   9.16    9.32   9.17
 bidir                          16.17  16.00   17.88  17.29   18.17  17.91
VLAN
 strip tag 3->4                  9.27   9.14    9.26   9.14    9.26   9.13
 add tag 4->3                    9.27   9.15    9.26   9.13    9.29   9.19
 strip+add bidir                11.43  11.39   12.93  12.80   13.00  12.75
 xlate VID 3->4                  9.28   9.14    9.27   9.13    9.26   9.13
 xlate VID 4->3                  9.27   9.20    9.26   9.13    9.28   9.15
 xlate VID bidir                13.32  14.11   17.99  17.79   18.15  17.90
 NAT across VLANs 3->4           9.26   9.10    9.26   9.13    9.38   9.25
PPPoE (session on eth3; OPNS native interface, one run)
 decap 3->4                      9.34   9.11    9.28   9.11    9.32   9.23
 encap 4->3                      9.23   9.10    9.23   9.14    9.37   9.15
WIREGUARD (software crypto)
 eth3 up                         0.83   0.43    1.39   1.27    1.69   1.63
 eth3 down                       0.96   0.93    1.21   1.22    1.54   1.42
 eth3 bidir                      0.95   0.76    1.42   1.42    1.42   1.47
 eth4 up                         0.84   0.77    1.34   1.30    1.57   1.62
 eth4 down                       0.95   0.75    1.31   1.29    1.48   1.50
 eth4 bidir                      0.93   0.74    1.57   1.44    1.41   1.37

64 B ONE WAY (Mpps)               OPNS             OWRT                   VyOS
 highest rate at <= 0.001 % loss  0.85             1.84 (2.79 unstable)   2.64
 3.14 Mpps offered, loss          62.867-62.930 %  0.011-0.053 %          0.00001-0.046 %
 unpaced flood delivered          1.16             3.40                   4.12
```

On the original routed/NAT/VLAN one-way cells, OPNS is at line rate like
the other two. Its IPv6 means are slightly higher because more of its
runs landed on the upper MSS step
(9.28 vs 9.15). Bidirectional, OPNS is 7–26% below OWRT and further below VyOS:
routed and NAT reach ~16 Gbit/s, VLAN translate 13.3/14.1, and VLAN↔port
11.4. In those cells DUT busy is 13–23%, against ≤2.4% one-way.

## OPNsense `.106` full battery on 26.7.7_8 (2026-10-18)

The vendor shipped `mono-gateway` 26.7.7_8, so the whole OPNS protocol was
repeated on it. Per-run dtsec RX error capture was added, and the
supplements got more repetitions.

| Property | Contract |
|---|---|
| Board | `.106`, OPNsense 26.7.7, `mono-gateway` 26.7.7_8 (installed 2026-10-18 16:46 UTC), kernel `mono-gateway-dev-n283733-28b84252869b`, pf + CMM (`cmm -d 1`). The 2026-10-16 campaign ran 26.7.7_4, kernel `n283731-4bfb3641e7b5` |
| Boot state | Soft reboot about 4 min before the start; no cold power cycle was available. Up 3 min at the start with 0 CDX connections. The 2026-10-16 run started at 47 min uptime with 34 active connections |
| 10×10 | Same 24 cells, order, 30 s, iperf3 `-Z -P8`, steady from 10 s, `SAMPLE=0`, 10 rounds. Adds per-run `netstat -I dtsec3/dtsec4 -bdn` deltas of `Ipkts`/`Ierrs` (`rx_pkts3`, `rx_errs3`, `rx_pkts4`, `rx_errs4` in `measurements.csv`) |
| Supplements | NAT across VLANs, v4/v6 × 10. Native OPNsense PPPoE (`pppoe0`/`opt5`, mpd5 generated by `interface_ppps_configure`) decap/encap v4/v6 × 10, with the `pppoe0` pass rules re-checked before each rep. WireGuard: 12 cells × 3 fresh tunnels. 64 B: 3 independent runs, each with a 10-step search, 3 confirmations, 3 fixed-rate trials near 3.14 Mpps and 3 floods |
| Controller | dell2 transient unit `opns-battery-20261018.service`, `run-battery.sh` → `opns-campaign.py`, `opns-extra.py`, `opns-pktgen.sh`. Harness `testrig-offload-quick.sh` sha256 `feb7b48d…24824ce` (unchanged). Script hashes are in `SOURCES.sha256` |
| Output | dell2 `~/testrig/campaigns/opns-battery-20261018/`; copy at `/mnt/builds/ask2-review/oracle/opns-battery-20261018/` with `analyze_battery.py` and its `analysis.txt` |
| Final state | 2026-10-18 18:00:12 → 22:02:47 UTC, `COMPLETE`, no stage failed. 10×10: 240/240 HW, 0 issues. NAT across VLANs 20/20 HW, PPPoE 40/40 HW, WireGuard 36/36 complete, 64 B 3/3 complete |
| Post-run | `pppoe-cleanup.txt`: pf rules equal to the baseline (automatic table names normalized), no `pppoe0`, no `mpd5`. `cmm -d 1` running. The dell1 `-C OPNS-BATTERY` concentrator is stopped and dell1 `n106` RPS is back to `00` |

### 24 cells

Δ is the 26.7.7_8 mean minus the 26.7.7_4 mean, with an unpaired bootstrap
95% interval. For bidir VLAN translate IPv4 and VLAN↔port IPv4, the
26.7.7_4 mean uses rounds 1–9 (the HW runs). ΔOWRT and ΔVyOS are against
the 10×10 campaign's `.106` OpenWrt and `.185` `0551` means. "Upper" counts
one-way runs on the upper TCP rate step (more than 0.06 above the cell
minimum). RX errors is the mean of
(`Ierrs3`+`Ierrs4`)/(`Ipkts3`+`Ipkts4`) per run.

| Cell | Mean (SD) | Median | Upper | 26.7.7_4 mean | Δ [95% CI] | ΔOWRT | ΔVyOS | RX errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Routed IPv4, 3→4 | 9.320 (0.059) | 9.285 | 3 | 9.283 | +0.037 [+0.002, +0.074] | +0.41% | +0.41% | 0.00% |
| Routed IPv6, 3→4 | 9.168 (0.039) | 9.155 | 1 | 9.229 | −0.061 [−0.100, −0.013] | +0.14% | +0.15% | 0.00% |
| NAT44, 3→4 | 9.295 (0.039) | 9.284 | 1 | 9.295 | +0.000 [−0.036, +0.037] | +0.15% | +0.15% | 0.00% |
| NAT66, 3→4 | 9.168 (0.039) | 9.155 | 1 | 9.241 | −0.073 [−0.111, −0.024] | +0.15% | +0.16% | 0.00% |
| VLAN translate IPv4, 3→4 | 9.271 (0.040) | 9.259 | 1 | 9.284 | −0.012 [−0.050, +0.026] | +0.01% | +0.15% | 0.00% |
| VLAN translate IPv6, 3→4 | 9.131 (0.002) | 9.131 | 0 | 9.145 | −0.014 [−0.038, −0.000] | +0.01% | +0.00% | 0.00% |
| Routed IPv4, 4→3 | 9.283 (0.002) | 9.284 | 0 | 9.308 | −0.025 [−0.062, +0.001] | +0.00% | −0.40% | 0.00% |
| Routed IPv6, 4→3 | 9.178 (0.051) | 9.155 | 2 | 9.179 | −0.001 [−0.042, +0.038] | +0.26% | +0.13% | 0.00% |
| NAT44, 4→3 | 9.283 (0.003) | 9.284 | 0 | 9.099 | +0.184 [−0.037, +0.589] | +0.02% | −0.38% | 0.00% |
| NAT66, 4→3 | 9.168 (0.038) | 9.157 | 1 | 9.254 | −0.086 [−0.122, −0.048] | +0.14% | +0.02% | 0.00% |
| VLAN translate IPv4, 4→3 | 9.260 (0.002) | 9.260 | 0 | 9.270 | −0.010 [−0.035, +0.004] | +0.02% | −0.25% | 0.00% |
| VLAN translate IPv6, 4→3 | 9.166 (0.057) | 9.133 | 3 | 9.201 | −0.035 [−0.084, +0.014] | +0.38% | +0.13% | 0.00% |
| VLAN→port IPv4 (strip), 3→4 | 9.258 (0.002) | 9.258 | 0 | 9.270 | −0.012 [−0.039, +0.004] | −0.02% | −0.01% | 0.00% |
| Port→VLAN IPv4 (add), 4→3 | 9.256 (0.006) | 9.259 | 0 | 9.265 | −0.009 [−0.037, +0.008] | −0.07% | −0.33% | 0.00% |
| VLAN→port IPv6 (strip), 3→4 | 9.129 (0.004) | 9.130 | 0 | 9.143 | −0.014 [−0.039, +0.000] | −0.15% | −0.01% | 0.00% |
| Port→VLAN IPv6 (add), 4→3 | 9.127 (0.008) | 9.128 | 0 | 9.151 | −0.024 [−0.059, +0.003] | −0.08% | −0.74% | 0.00% |
| Bidir routed IPv4 | 16.078 (0.191) | 16.020 | — | 15.935 | +0.143 [−0.062, +0.345] | −10.64% | −11.32% | 9.73% |
| Bidir routed IPv6 | 15.749 (0.343) | 15.761 | — | 15.667 | +0.082 [−0.158, +0.320] | −10.59% | −12.00% | 9.50% |
| Bidir NAT44 | 16.259 (0.174) | 16.214 | — | 16.166 | +0.093 [−0.080, +0.275] | −9.19% | −10.51% | 8.41% |
| Bidir NAT66 | 16.135 (0.193) | 16.108 | — | 15.998 | +0.137 [−0.007, +0.285] | −6.70% | −9.89% | 8.19% |
| Bidir VLAN translate IPv4 | 13.393 (0.025) | 13.398 | — | 13.309 | +0.084 [−0.020, +0.223] | −25.55% | −26.22% | 13.57% |
| Bidir VLAN translate IPv6 | 14.143 (0.049) | 14.148 | — | 14.110 | +0.034 [−0.024, +0.103] | −20.50% | −20.99% | 12.62% |
| Bidir VLAN↔port IPv4 | 11.471 (0.123) | 11.521 | — | 11.437 | +0.034 [−0.060, +0.122] | −11.29% | −11.75% | 19.18% |
| Bidir VLAN↔port IPv6 | 11.354 (0.091) | 11.371 | — | 11.389 | −0.036 [−0.122, +0.071] | −11.30% | −10.97% | 19.37% |

| Bidir cell | DUT busy 26.7.7_8 / _4 | Retransmits per run, M (_8 / _4) | dtsec3 / dtsec4 RX errors | Per-run RX error range |
|---|---:|---:|---:|---:|
| Routed IPv4 | 14.4% / 16.7% | 6.02 / 6.11 | 9.8% / 9.6% | 8.0–10.8% |
| Routed IPv6 | 14.8% / 13.9% | 5.81 / 6.23 | 9.6% / 9.4% | 5.8–11.2% |
| NAT44 | 13.0% / 13.5% | 5.64 / 5.86 | 8.5% / 8.3% | 7.7–9.1% |
| NAT66 | 12.0% / 14.1% | 5.55 / 5.72 | 8.2% / 8.2% | 7.6–8.7% |
| VLAN translate IPv4 | 15.6% / 13.8% | 9.21 / 9.10 | 13.5% / 13.7% | 13.5–13.7% |
| VLAN translate IPv6 | 15.5% / 15.6% | 8.73 / 8.72 | 12.6% / 12.7% | 12.4–12.8% |
| VLAN↔port IPv4 | 21.7% / 22.6% | 9.68 / 9.66 | 18.8% / 19.6% | 18.9–19.3% |
| VLAN↔port IPv6 | 22.2% / 20.3% | 9.77 / 9.77 | 18.9% / 19.8% | 19.1–19.5% |

**Reading.**

- **One-way cells replicate at line rate.** All 160 one-way runs sit on the
  two known TCP rate levels. Five one-way intervals exclude zero: routed
  IPv4 3→4 (+0.037), and routed IPv6 3→4, NAT66 3→4, NAT66 4→3 and VLAN
  translate IPv6 3→4 (−0.014 to −0.086). In each, the count of upper-step
  runs changed. The 26.7.7_4 session had more IPv6 runs on the upper step.
  This is not a forwarding change; only the median and upper-step count
  are meaningful for these cells.
- **The NAT44 4→3 slow run did not recur.** The 26.7.7_4 HW run at 7.320
  Gbit/s had no counterpart: all 10 runs measured 9.278–9.288.
- **The bidirectional deficit replicates.** All eight bidir means are
  within +0.9%/−0.3% of the 26.7.7_4 means, and every interval includes
  zero. The battery covers two builds, two boots and two days. OPNS is
  6.7–25.6% below the OpenWrt vendor means and 9.9–26.2% below VyOS
  `0551`. DUT busy is 12–22% and retransmits are unchanged (5.6–9.8 M per
  run). This gap is a robust result, not run-to-run noise.
- **Bidirectional load produces heavy dtsec RX errors.** One-way runs
  never exceed 0.015% RX errors (at most 3,924 per run). Bidirectional
  runs count 8.2–19.4% of received frames as `Ierrs` on both ports
  equally. For example, routed IPv4 round 1 had 2,543,812 / 2,451,397
  errors on 23.8 / 23.9 M frames. The error rate ranks inversely with
  throughput:

  | Cells | RX errors | Throughput, Gbit/s |
  |---|---:|---:|
  | NAT | 8.2–8.4% | 16.1–16.3 |
  | Routed | 9.5–9.7% | 15.7–16.1 |
  | VLAN translate | 12.6–13.6% | 13.4–14.1 |
  | VLAN↔port | 19.2–19.4% | 11.4–11.5 |

  At ≤22% busy, the bidir ceiling coincides with receive-side frame errors,
  not CPU. The cause is not established. The counter is FreeBSD ifnet
  `Ierrs`; earlier `dmesg` showed `fd_status=0x00080000` (phy=1). The
  2026-10-05 OpenWrt vendor run used mEMAC `rdrp` instead. It lost about
  10 K frames per port per 30 s on port↔port bidir and about 1.75 M on
  VLAN↔port. OPNS loses about 2.5 M per port on plain routed bidir.
  Because the counters differ, that comparison is indicative only.
- **The VLAN-ingress fault did not recur.** The 26.7.7_4 software
  fallback (VLAN-10-ingress IPv4, after about 2.7 h) did not appear in the
  2 h 42 m 10×10 or in the 4 h battery. That does not prove a fix: this
  battery started 3 min after a soft reboot, while the original fault
  developed from a 47 min uptime start.

### Supplements

| Measurement | 26.7.7_8 result | 26.7.7_4 result |
|---|---|---|
| NAT across VLANs IPv4 | mean 9.272, median 9.259, 9.256–9.385 (n=10, HW) | 9.2613 (n=3) |
| NAT across VLANs IPv6 | mean 9.123, median 9.093, 9.057–9.210 (n=10, HW) | 9.0977 (n=3) |
| PPPoE decap 3→4 IPv4 / IPv6 | mean 9.251 / 9.111, median 9.241 / 9.112 (n=10 each, HW) | 9.340 / 9.111 (one run) |
| PPPoE encap 4→3 IPv4 / IPv6 | mean 9.265 / 9.113, median 9.229 / 9.102 (n=10 each, HW) | 9.230 / 9.097 (one run) |

PPPoE DUT busy averaged 1.3–1.5%. Native PPPoE hardware forwarding is now
confirmed at n=10. Still not covered: PPPoE bidir, NAT over PPPoE (the
opnsense harness backend lacks it) and fragmentation.

WireGuard medians (Gbit/s, n=3 fresh tunnels each; 26.7.7_4 in parentheses):

| Leg | IPv4 up | IPv4 down | IPv4 bidir | IPv6 up | IPv6 down | IPv6 bidir |
|---|---:|---:|---:|---:|---:|---:|
| eth3 | 0.81 (0.83) | 0.86 (0.96) | 0.91 (0.95) | 0.42 (0.43) | 0.82 (0.93) | 0.73 (0.76) |
| eth4 | 0.82 (0.84) | 0.88 (0.95) | 0.92 (0.93) | 0.73 (0.77) | 0.75 (0.75) | 0.70 (0.74) |

Down is lower than on 26.7.7_4 (IPv4 −0.07 to −0.10, eth3 IPv6 −0.11); the
other cells are within 0.04. With n=3 per build, treat this as a lead, not
a regression. `net.pfnotify.events_dropped` rose from 0 to 209,430,087
during the WireGuard stage. As before, these are as-configured tunnel
throughput numbers that include CMM event-ring overload, not crypto
benchmarks.

64-byte IPv4 single flow (Mpps delivered):

| Run | ≤0.001% loss, confirmed | Loss near 3.14 Mpps offered | Unpaced flood |
|---|---:|---:|---:|
| 1 | 0.847 (0.857 requested) | 62.858–62.949% | 1.160 |
| 2 | 1.120 (1.134 requested) | 62.747–63.006% | 1.162 |
| 3 | 1.090 (1.104 requested) | 62.854–62.927% | 1.170 |
| 26.7.7_4 | 0.846 | 62.867–62.930% | 1.160 |

The low-loss figure is not a stable threshold. Sporadic losses of about
0.001–0.002% (100–170 frames per 10 s trial) appear anywhere between 0.86
and 1.15 Mpps offered, and they decide where each binary search stops:

- Run 1 failed at 0.924 and 0.862 Mpps offered (0.00172%, 0.00114%).
- Run 2 passed 0.919–1.120 Mpps and failed at 1.134 and 1.149 Mpps (0.00145%, 0.00148%).
- Run 3 passed at 1.147 Mpps.

The real cliff is about 1.16 Mpps: run 3 lost 0.36% at 1.164, 1.67% at
1.174 and 3.92% at 1.207 Mpps offered, and every flood delivered
1.160–1.170 Mpps. The 26.7.7_4 value of 0.85 was one draw from this
spread. Report OPNS 64 B as about 1.16 Mpps forwarding capacity, with the
confirmed ≤0.001%-loss rate falling between 0.85 and 1.12 Mpps depending on
the run. Bypass share was 1.000 in every trial.

### Three-way summary (2026-10-18, 26.7.7_8)

OPNS is this battery: 10×10 means; NAT across VLANs and PPPoE at n=10;
WireGuard medians of 3. OWRT and VyOS are copied unchanged from the
2026-10-16 block above. OPNS remains unpaired context, not a verdict input.

```text
Gbit/s unless noted               OPNS           OWRT           VyOS
                                  v4     v6      v4     v6      v4     v6
ROUTED
 3->4                            9.32   9.17    9.28   9.15    9.28   9.15
 4->3                            9.28   9.18    9.28   9.15    9.32   9.17
 bidir                          16.08  15.75   17.99  17.61   18.13  17.90
NAT44 / NAT66 (PAT included)
 3->4                            9.30   9.17    9.28   9.15    9.28   9.15
 4->3                            9.28   9.17    9.28   9.16    9.32   9.17
 bidir                          16.26  16.13   17.88  17.29   18.17  17.91
VLAN
 strip tag 3->4                  9.26   9.13    9.26   9.14    9.26   9.13
 add tag 4->3                    9.26   9.13    9.26   9.13    9.29   9.19
 strip+add bidir                11.47  11.35   12.93  12.80   13.00  12.75
 xlate VID 3->4                  9.27   9.13    9.27   9.13    9.26   9.13
 xlate VID 4->3                  9.26   9.17    9.26   9.13    9.28   9.15
 xlate VID bidir                13.39  14.14   17.99  17.79   18.15  17.90
 NAT across VLANs 3->4           9.27   9.12    9.26   9.13    9.38   9.25
PPPoE (session on eth3; OPNS native pppoe0, n=10, HW)
 decap 3->4                      9.25   9.11    9.28   9.11    9.32   9.23
 encap 4->3                      9.27   9.11    9.23   9.14    9.37   9.15
WIREGUARD (software crypto)
 eth3 up                         0.81   0.42    1.39   1.27    1.69   1.63
 eth3 down                       0.86   0.82    1.21   1.22    1.54   1.42
 eth3 bidir                      0.91   0.73    1.42   1.42    1.42   1.47
 eth4 up                         0.82   0.73    1.34   1.30    1.57   1.62
 eth4 down                       0.88   0.75    1.31   1.29    1.48   1.50
 eth4 bidir                      0.92   0.70    1.57   1.44    1.41   1.37

64 B ONE WAY (Mpps)               OPNS             OWRT                   VyOS
 highest rate at <= 0.001 % loss  0.85-1.12 (3x)   1.84 (2.79 unstable)   2.64
 3.14 Mpps offered, loss          62.75-63.01 %    0.011-0.053 %          0.00001-0.046 %
 unpaced flood delivered          1.16-1.17        3.40                   4.12
```

**How far the OPNS column can be trusted.** It now rests on two sessions,
two builds and two boots, with 10 runs per cell except WireGuard (3).

- **One-way cells** show line rate and nothing finer. Their means move
  with the TCP rate-step lottery, so ±0.1 Gbit/s differences between
  stacks are not findings.
- **The bidirectional gap and the 64 B capacity** reproduced within 1%
  across sessions. They are the defensible OPNS results.
- **What is missing:** a cold boot, and a same-session paired run against
  `.185`. OWRT can no longer be re-run.

## Evidence still required

| Surface | Current acceptance state | Required comparison |
|---|---|---|
| Routed v4/v6, NAT44/NAT66, VLAN translate/strip/add | Implemented; 10×10 complete (7 strict-mean FAIL at `0551`; 6 on the 2026-10-17 `2149`-code ASK2-only rerun vs historical OWRT) | Investigate VLAN translate IPv4 3→4 (−0.13%) and bidir VLAN↔port IPv6 (−0.70%), FAIL in both campaigns; same-session A/B of `0551` vs `2149` for the ~0.3% bidir decrease; repeat small deficits under the alternating protocol when a vendor board is available |
| NAT over VLAN | Implemented; prior quick results historical | Same-conditions current-image combo |
| PPPoE v4/v6 encap/decap, NAT over PPPoE | Implemented; prior runs not same-session | Both DUTs with vendor `pppoe10` up and dell1 RPS |
| PPPoE fragmentation | Prior ASK2 intact delivery; vendor UDP-header corruption observed | Both DUTs, controls/warm/new/DF/PTB matrix |
| Bridge learned/static and same-tagging | B6/B7a silicon acceptance is dated, not absent | Current-image functional repeat and vendor throughput per VLAN mode |
| Ingress policer | Historical cap measurements on non-ASK ports | Current wire rate accuracy ±15%; ASK-path coexistence is missing functionality |
| 64-byte pps / IMIX | Historical results, not 0551 acceptance | Same-rig offered/delivered/loss sweep and overload recovery |
| Connection/session/install scale | Not accepted; F-266 close fix proven live on silicon 2026-10-20, CI image retest pending | Retest close on the CI image, then held-session CPS (SYN now punted to one CPU, as on the vendor), 8k PDR and insert latency/rate |
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
