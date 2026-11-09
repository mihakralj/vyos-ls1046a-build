# ASK2-only performance matrix and system-metric battery

**Status:** Design only (2026-11-07). No driver from this plan exists yet; the
`M01–M13` catalogue rows and the matrix driver mapping described in §6.9 are
pending and will start as `open`. This suite is deliberately **ASK2-only**: the
vendor stacks (OWRT, OPNS) are not executed by it, and no vendor column is
printed. Vendor figures quoted anywhere in this document are stored only as
targets in catalogue evidence.

## Goal

One command that exercises every ASK2 capability and prints one fixed-width
table with a **two-row header**. Each flow type row carries six numbers: Upload,
Download and Bidir, with v4 and v6 under each group.

```text
Flow type       Upload          Download          Bidir
                v4      v6      v4      v6      v4      v6
unicast       9.41    9.38    9.39    9.36    16.9    16.8
nat           9.22    9.18    9.20    9.16    16.4    16.3
```

The table is **ASK2-only** (no vendor columns) and prints **no hardware-share
and no verdict columns**. Both are still measured: they are written to the CSV
and JSON artifacts (§4) and determine the process exit status.

A second table covers the system-level battery ("how fast, how lossy, how
stable") independently of the flow mix, with a single ASK2 value column:

```text
System metric                    Unit            ASK2
M01 64 B bidir NDR               Mpps            3.42
M02 IMIX L2 per direction        Gbit/s          4.02
```

The suite must run whether or not a flow is hardware-offloaded, and must record
which it was. A cell that silently fell back to software is a valid result, not
a failure, but it must never be recorded as hardware performance. The printed
table omits that field; the CSV and JSON carry it.

## Non-goals

- No vendor (OWRT/OPNS) execution. Cross-OS comparison stays in
  `bin/testrig-compare.sh` and `plans/ASK2-VENDOR-PERF-BATTERY.md`.
- No replacement of the acceptance runbooks (bridge, PPPoE, WireGuard, IPsec);
  this suite consumes their cells rather than re-deriving correctness.
- No new correctness tests. A performance number without a correctness gate is
  reported as `UNVERIFIED`.

## 1. What exists today

| Piece | Status |
|---|---|
| `bin/testrig-offload-quick.sh` | 20 cell definitions (routed, NAT, VLAN, VLAN↔port, PPPoE, v4/v6, up/reverse, plus opt-in combos), one run per cell, CSV `ts,cell,gbps,dut_busy,dut_softirq,gen_cpu,retrans,hw_pkts,data_pkts_est,hw_ratio,verdict`; HW proof from `fe_ehash_stats` (ASK2) or kernel-bypass counters |
| `bin/testrig-combo-matrix.sh` | port/VLAN × v4/v6 × unidir/bidir matrix with auto HW/SW labelling; own known-issue notes |
| `bin/testrig-pktgen.sh` | 64-byte v4/v6 offered sweep, IMIX mode |
| `bin/testrig-cps.py`, `bin/testrig-latprobe.py`, `bin/testrig-offload-delay.py` | connection rate, latency probe, engage delay |
| `bin/testrig-bridge-*`, `bin/testrig-pppoe-*`, `bin/testrig-wg*`, `bin/testrig-a14.sh` | capability-specific correctness and perf |
| `perf/catalogue.csv` (117 rows), `bin/perf-ingest.py`, `bin/perf-catalogue-render.py`, `perf/runs.csv`, `perf/campaigns.csv` | test catalogue, ledger ingest, reporting |
| `plans/ASK2-PERFORMANCE-TEST-HARNESS.md` | rig, addressing, repeatability rules |
| `plans/OFFLOAD-TEST-CATALOGUE.md` | human-readable catalogue (must stay in sync with the CSV) |

Gaps this plan fills:

1. No single driver that produces the six-number table.
2. No row-per-flow-type table; cells exist but results are scattered across
   drivers and campaign files.
3. No ASK2-only system-metric battery for NDR, IMIX, MTU ceiling, flow-scale
   loss, CPS, teardown loss, latency/jitter stability, VLAN-tag NDR.
4. Broadcast/multicast are absent from the throughput matrix (only gap rows
   G01–G03 exist).
5. `perf-ingest.py` has no columns for direction, family, offload mode or HW
   share per direction.

## 2. Test layering

### Layer A — capability matrix

One row per flow type. Directions are defined relative to the rig:
`Up` = gen1 (eth3) → gen2 (eth4), `Down` = gen2 → gen1, `Bidir` = aggregate of
both directions (`iperf3 --bidir`, sum of both directions' goodput).

Output format (fixed-width, two-row header, columns in this order):

```text
Flow type       Upload          Download          Bidir
                v4      v6      v4      v6      v4      v6
unicast       9.41    9.38    9.39    9.36    16.9    16.8
```

Mock rendering of the full matrix (synthetic values, Gbit/s; rows marked
`Mpps` are packets per second):

```text
Flow type       Upload          Download          Bidir
                v4      v6      v4      v6      v4      v6
unicast       9.41    9.38    9.39    9.36    16.9    16.8
nat           9.22    9.18    9.20    9.16    16.4    16.3
vlan-vlan     9.35    9.31    9.33    9.30    16.7    16.6
vlan-port     9.30    9.26    9.28    9.24    16.6    16.5
pppoe         9.35    9.31    9.33    9.29    16.5    16.4
wireguard     2.45    2.40    2.42    2.38     4.8     4.7
ipsec         1.62    1.58    1.60    1.55     3.1     3.0
bridge        9.38    9.35    9.36    9.33    16.8    16.7
broadcast     0.38       -       -       -       -       -
multicast     0.31    0.29       -       -       -       -
fragments     9.39    9.35    9.37    9.34    16.8    16.7
policer       1.00    1.00    1.00    1.00    2.00    2.00
```

Flow types (one row each):

| Row | Type | How it is built | Notes |
|---|---|---|---|
| `unicast` | routed port↔port | existing R01–R06 cells | baseline |
| `nat` | NAT44 + NAT66 masquerade | N rows | one row covers v4 and v6 separately in the six slots |
| `vlan-vlan` | tag 10 → tag 20 translation | V01–V12 | includes strip/push cells |
| `vlan-port` | tagged ↔ untagged | V05–V08, V11–V12 | |
| `pppoe` | PPPoE encap/decap on `pppoe10` | P01–P04 | Up = encap (LAN→WAN), Down = decap |
| `wireguard` | tunnel terminated on DUT | `testrig-wg*`, `testrig-wgfp-*` | software crypto; expected HW share 0 |
| `bridge` | L2 forwarding across `br0` members | B01–B06 | L2 only; no NAT |
| `policer` | ingress policer, shaped rate | Q01 | delivered within ±15 % of configured rate |

Not measured (decision 2026-11-09): `ipsec`, `multicast` and `broadcast` have no ASK2 hardware offload (IPsec has only the Tier 1 CAAM crypto substrate, multicast/MDB replication is not implemented, broadcast/BUM stays in the kernel by design), so their rows would only show the Linux software rate; fragment cells are not needed. Add a row back when its offload exists.

Rules:

- Every number is **TCP payload goodput** for TCP-capable rows, in Gbit/s to two
  decimals, measured as the receiver-side steady-state mean (same definition as
  `testrig-offload-quick.sh`, `DUR=30 s`, `STEADY_FROM=10 s`) so historical
  numbers stay comparable.
- `broadcast`/`multicast` report delivered Mpps; the unit is stated once in the
  context block and stored per row in the CSV (`unit` column).
- Invalid slots are `-`, never 0 and never blank.
- The printed table has exactly seven columns: flow type plus the six numbers.
  Hardware share, offload proof and verdict are **not printed**; they are
  written to the CSV/JSON artifacts and used for exit status and reporting.
- Hardware share is still mandatory internally: per direction it is
  `fe_ehash_stats` record bytes vs receiver bytes (ASK2), and a cell whose
  declared mode is hardware but whose proof shows software is recorded as a
  fallback, never as hardware.
- Verdict values (`PASS`, `REGRESS`, `SW`, `UNVERIFIED`) exist only in the CSV
  and JSON, not in the printed table.

### Layer B — system metrics

Independent of flow type; each metric is one row of a second, ASK2-only table
with a single value column:

```text
System metric                    Unit            ASK2
M01 64 B bidir NDR               Mpps            3.42
M02 IMIX L2 per direction        Gbit/s          4.02
M03 Max MTU / rate               bytes, %        9000, 96.9
M04 Loss @ 16k/64k/128k/256k     %               0/0/0/26
M05 HW share @ 64k               %               100
M06 CPS 10k, p99 connect         conn/s, ms      9.9k, 171
M07 CPS 25k                      outcome         collapse
M08 Loss during teardown         %               0.008
M09 p99 latency @ 90 % NDR       µs              6.9
M10 p99 latency @ 99 % NDR       µs              8.1
M11 10-min p99/p99.9/max         µs              5.8/6.5/19.2
M12 Jitter p99/p99.9/max         µs              3.4/5.0/29
M13 VLAN-tag 64 B NDR            Mpps            3.61
```

Definitions:

| ID | Metric | Unit | Method |
|---|---|---|---|
| M01 | 64 B bidirectional NDR | Mpps | pktgen, offered sweep, NDR = ≤0.001 % loss per direction |
| M02 | IMIX (7:4:1, 64/570/1518) L2 rate per direction | Gbit/s | pktgen IMIX, steady 30 s |
| M03 | Max MTU and rate at max MTU | bytes, % line | MTU step sweep 1500→9000, then 30 s run |
| M04 | Loss at 16k/64k/128k/256k held flows | % | flow-scale generator, fixed offered rate, 30 s |
| M05 | HW share at 64k flows | % | `ynl dump-flows` / conntrack + silicon bytes |
| M06 | CPS with held sessions, 10k offered | conn/s, p99 connect | `testrig-cps.py` |
| M07 | CPS collapse threshold (25k offered) | outcome | same, report first failure rate |
| M08 | Loss during teardown | % | delete all flows mid-traffic (`conntrack -F`), 10 s window |
| M09 | p99 latency at 90 % NDR | µs | hardware-timestamp probe (`testrig-latprobe.py`) |
| M10 | p99 latency at 99 % NDR | µs | same |
| M11 | 10-minute p99 / p99.9 / max latency | µs | steady 10 min at fixed load |
| M12 | Jitter p99 / p99.9 / max | µs | inter-packet delay variation from the same capture |
| M13 | VLAN-tagged 64 B NDR | Mpps | pktgen with 802.1Q tag |

Reference values: the 2026-11-07 operator table (which also carries OWRT/ask
figures) is stored as **targets in `perf/catalogue.csv` evidence**, never as an
output column. Only ASK2 measurements are printed. Provenance of those targets
(image, boot, rig) is unstated and must be re-measured before use as a gate.

Notes and conflicts to resolve before these become gates:

- The reference 64 B and IMIX figures differ from the catalogue evidence
  (`S01`: ASK2 3.10 Mpps vs vendor 0.05 % loss, 2026-10-10; `S02`: 3.17 vs
  3.21 Mpps; archive Phase 0.4: 2.43 Mpps IMIX). The suite records the image,
  boot type and pktgen tuning with every sample so the difference is
  explainable rather than averaged away.
- `S05` records a CPS ceiling of ~10–11k (P5 2026-10-21) and `S08` a 1.0–1.06 s
  engage delay; the new table's p99 connect time (176 ms) measures a different
  quantity. Both must be reported; do not substitute one for the other.
- Flow-scale numbers depend on `ask.max_flows` (default 262,144 directions) and
  on the 2026-10-10 stats-path convoy; run with the telemetry plan's
  stats-scaling fix or record the known limitation.

### Layer C — capability regression hooks

The suite calls, but does not duplicate, the existing capability drivers and
records their pass/fail as the `verdict` source for rows that need correctness:
`testrig-bridge-*`, `testrig-pppoe-*`, `testrig-wg*`, `testrig-a14.sh`
(offload closed), `testrig-policer` cells, `testrig-mtu-steps.sh`.

## 3. Offload mode handling

`--mode` selects how the same physical cell is measured:

| Mode | Setup | Purpose |
|---|---|---|
| `hw` (default) | production config: `offload ipv4 ipv6`, auto flowtable | shipping performance |
| `sw` | delete the `offload` leaves (or set `ask.max_flows=0` for a budget-only SW run) | software reference, same rig and generators |
| `both` | runs both, prints both lines with a `mode=` prefix | change attribution |

Every result stores the effective mode and the HW share, so a `hw` run that
silently fell back is visible in the line and in the ledger. Software results
use the same six-number schema; they are not a separate reporting format.

## 4. Output contracts

Human output: the fixed-width, two-row-header ASK2-only table (§2), for both
layers, with no hardware-share or verdict columns:

```text
Flow type       Upload          Download          Bidir
                v4      v6      v4      v6      v4      v6
unicast       9.41    9.38    9.39    9.36    16.9    16.8
```

CSV (`OUT/matrix-<date>-<time>.csv`), one row per type, family, direction and
mode. Hardware share and verdict live here and in the JSON, not in the table:

```text
ts,dut,image,kernel,boot,mode,type,fam,up,down,bidir,hw_up,hw_down,hw_bidir,unit,verdict,dur_s,streams,note
```

JSON per cell (`OUT/json/<type>-<mode>.json`): raw iperf3/pktgen output plus the
offload proof (record bytes/packets, conntrack count, `fe_ehash_stats`) so a
number can be re-derived without rerunning. The process exit status is derived
from the CSV verdicts; a `REGRESS` row fails the run even though the printed
table shows only numbers.

The CSV is ingestible by `bin/perf-ingest.py`; the plan adds the new columns and
a `matrix` driver mapping (`perf/catalogue.csv` rows `R*`, `N*`, `V*`, `P*`,
`B*`, `G*`, `M*`). `bin/perf-catalogue-render.py` renders the same two-row-header
table from the ledger.

## 5. Determinism and comparability

- Fixed `DUR=30 s`, `STEADY_FROM=10 s`, `STREAMS=8` unless the cell declares
  otherwise; 3 repetitions, report the median and the spread.
- Same MTU (1500) and same generator tuning for all cells; the MTU cell is the
  only exception.
- Record DUT temperature, CPU governor, boot type (`cold|warm`), image and
  kernel with every run; a warm reboot does not clear BMI/MURAM, so any
  offload anomaly requires a cold-boot repeat (project rule).
- One cell at a time; unrelated background traffic on the rig invalidates the
  run and is recorded as `UNVERIFIED`.
- The suite never changes persistent rig config; all NAT/PPPoE/WireGuard setup is
  runtime and restored on exit, matching the quick driver's behaviour.

## 6. Work items

| # | Item | Files | Effort | Risk |
|---|---|---|---|---|
| 6.1 | Cell-definition library extracted from `testrig-offload-quick.sh` (shared by both drivers) | `bin/testrig-cells.sh` (new) | 3–5 d | Low |
| 6.2 | `bin/testrig-ask2-matrix.sh`: six-number driver, `--mode hw|sw|both`, CSV/JSON, HW proof, resume | new | 1.5–2 w | Medium |
| 6.3 | Broadcast/multicast generators and slots (`-` semantics) | new helper + cells | 3–5 d | Medium (no TCP; Mpps/loss only) |
| 6.4 | System battery: NDR search, IMIX, MTU ceiling | extend `testrig-pktgen.sh` + new `testrig-ask2-sys.sh` | 1.5–2 w | Medium |
| 6.5 | Flow-scale generator to 16k/64k/128k/256k + teardown-loss measurement | extend `testrig-cps.py`, `_flowtable_connections.py` | 2 w | **High** (generator cost, stats convoy, conntrack sizing) |
| 6.6 | CPS + p99 connect + collapse detection | `testrig-cps.py` | 3–5 d | Medium |
| 6.7 | Latency/jitter battery at 90/99 % NDR and 10-minute stability | `testrig-latprobe.py` | 3–5 d | Medium |
| 6.8 | Integrate WireGuard/IPsec/bridge/PPPoE/policer cells and their verdict hooks | new driver | 1 w | Low |
| 6.9 | Catalogue rows + ingest columns + renderer | `perf/*`, `bin/perf-*.py` | 3–5 d | Low |
| 6.10 | Docs: update `ASK2-PERFORMANCE-TEST-HARNESS.md`, `OFFLOAD-TEST-CATALOGUE.md` | docs | 2–3 d | Low |

**Total: 7–9 engineer-weeks serial, about 5–6 weeks with 6.3–6.8 parallelised.**

## 7. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Generator becomes the bottleneck at 64–256k flows | High | High | Use `-Z`/pktgen, pre-built flows, per-CPU sharding, verify with the receiver-side HW share and DUT counters; document the generator ceiling |
| Silent SW fallback invisible in the printed table | Medium | High | HW proof mandatory per cell and stored in CSV/JSON; `REGRESS`/fallback in the CSV fails the run even though the table shows only numbers |
| Mixed units (payload Gbit/s vs L2) create false comparisons | Medium | Medium | Unit field in every row; Layer A is payload, Layer B is L2 Mpps/Gbit/s as labelled |
| Reference table provenance unknown | Certain | Medium | Store as targets in catalogue evidence with `provenance=operator-2026-11-07`; never a gate until re-measured |
| Flow-scale runs trigger the known stats convoy (2026-10-10) | Medium | High | Run after the telemetry plan's stats-scaling item, or record `UNVERIFIED` for M04/M05 |
| Warm-boot contamination after offload faults | Medium | High | Cold-boot rule; record boot type; refuse to publish offload results after a detected fault |
| Broadcast/multicast disturb the rig or neighbours | Low | Medium | Dedicated VLANs, TTL scope, short runs, cleanup trap |
| Rig shared with other work | Medium | Medium | Lock file plus `RUN_ID`; abort on foreign traffic detection |

## 8. Acceptance gates for the suite itself

**G1 — Determinism.** Same image, three consecutive runs: each Layer A number
within ±1 %, each Layer B latency within ±5 %.

**G2 — Honest labelling.** A deliberately disabled offload (`--mode sw`) records
`hw=0` and a software verdict in the CSV/JSON for every cell, while the printed
table keeps its seven columns; re-enabling yields the same numbers as a fresh
`hw` run within ±2 % on offloadable rows.

**G3 — Completeness.** Every row in Layer A and Layer B emits a line and a CSV
row; missing cells are explicit `n/a` with a reason, never silently skipped.

**G4 — Time budget.** Full Layer A (12 types × 6 slots, `hw` mode) ≤ 3 h on an
idle rig; Layer B ≤ 2 h; `--quick` subset ≤ 20 min.

**G5 — Reproducibility.** The JSON artifacts and the CSV are sufficient to
re-derive every printed number; `bin/perf-ingest.py --check` passes and the
renderer reproduces the printed table.

## 9. Open questions

- Is `bidir` the sum of both directions (proposed) or the weaker direction? The
  reference table does not say; the CSV stores both direction values so either
  can be derived.
- Should `unicast` define `up` as eth3→eth4 (LAN→WAN) or as "client→server" for
  each cell? Proposed: rig-relative (eth3→eth4 = up) for all rows.
- Which flow-scale transport: UDP 1 pps per tuple (cheapest) or TCP handshakes
  (realistic but expensive at 256k)?
- Does the 10-minute stability metric run at a fixed pps or at 90 % NDR? Proposed
  fixed 90 % NDR of M01.
- Where do the reference numbers come from (image, boot, pktgen tuning)? Needed
  before they become gates.

## 10. References

- `plans/ASK2-PERFORMANCE-TEST-HARNESS.md` (rig, addressing, repeatability).
- `plans/OFFLOAD-TEST-CATALOGUE.md`, `perf/catalogue.csv`, `perf/runs.csv`,
  `perf/campaigns.csv`, `bin/perf-ingest.py`, `bin/perf-catalogue-render.py`.
- `bin/testrig-offload-quick.sh` (cell and CSV schema), `bin/testrig-combo-matrix.sh`,
  `bin/testrig-pktgen.sh`, `bin/testrig-cps.py`, `bin/testrig-latprobe.py`,
  `bin/testrig-offload-delay.py`.
- `plans/ASK2-VENDOR-PERF-BATTERY.md` (cross-OS battery definitions).
- `plans/ASK2-FLOW-SCALING-PLAN.md` (admission and pressure; affects M04/M05).
- `plans/ASK2-SILICON-TELEMETRY-PLAN.md` (stats scaling; affects M04/M05).
- Operator-supplied reference table, 2026-11-07 (provenance TBD).
