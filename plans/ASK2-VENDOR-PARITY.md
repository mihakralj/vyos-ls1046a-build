# ASK2 vs NXP ASK: current parity evidence

**Updated 2026-10-16. Full feature parity is not reached.** The authoritative gap
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

### Three-way summary

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
