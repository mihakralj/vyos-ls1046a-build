# ASK2 jumbo MTU plan: reach vendor parity (9000+)

Status: **Option B adopted; jumbo acceptance remains open.** Repaired patch 0177 removes the MAC buffer-size clamp; CI run `41887348300` built image `2026.11.04-1827` (kernel `6.18.60-vyos`). The guards allow 1280–9000; this is a configuration range, not a validation claim for every MTU, feature, workload or current image. RX pools remain 4 KiB order-0; contiguous jumbo buffers/page_pool are deferred.

On 2026-11-09, the F-308 gate on DUT1, image `2026.11.08-2332-rolling`, observed the margin return to 256 at MTU 9000, successful full-size DF pings, and approximately 9.8 Gbit/s one-way TCP traffic. Cold versus warm boot was not recorded. CSV writes failed, and the hardware classifier reported PARTIAL using an estimated 1448-byte packet size, so its hardware-share verdict is unreliable for jumbo. The retained iperf JSON reports MSS 8948 and PMTU 9000, but actual packet sizes and full-hardware forwarding were not independently established. See the master plan F-308 row.

On image `2026.11.11-2140`, some unicast, NAT and VLAN-to-VLAN bidirectional 9k cells collapsed in over 50% of runs; a same-rig software control remained steady at about 13.0–13.2 Gbit/s. The cause and current-image status are unresolved, not proof of a new current-image bug. Revalidate with packet sizes, per-direction delivery and hardware share measured.

On cold-booted DUT1 with image `2026.11.16-0638-rolling`, bounded 9k unicast bidirectional tests reproduced a reverse-direction collapse while the forward direction stayed near 9.9 Gbit/s. In one run the directions later reversed: reverse recovered to about 9.9 Gbit/s as forward fell to zero, which argues against a continuous global dataplane wedge in that run. Socket samples showed loss of ACK progress without a closed receive window. In an earlier run with BMI statistics enabled, increases in the generic eth3 RX frame-discard counter closely tracked the aggregate gap between small-frame eth3 ingress and eth4 egress; neither counter identifies individual packets or proves the discard stage. The reverse transition showed a similar eth4-side gap and discard increase, but the counters were read at different times and their alignment with iperf intervals is approximate. In a later run the BMI statistics were disabled, so its zero discard counts are not evidence of no discards. A single-flow capture showed sender-side ACKs without corresponding receiver-side ACKs during the collapse, but the outgoing capture precedes physical NIC delivery. The cause remains open; do not equate this with the WireGuard reset.

**Congestion-control controls, 2026-11-17.** On separate cold boots of the same image, one IPv4 unicast `iperf3 -P 8 -Z --bidir` run of 25 seconds was made with per-socket CUBIC, followed by one with per-socket BBR. Both used MTU 9000 and MSS 8948; socket samples confirmed the requested algorithms. FE records were active in both, but their activity does not establish a 100% hardware share. Receiver goodput and sender retransmits were:

| Per-socket algorithm | Forward / reverse goodput (Gbit/s) | Forward / reverse retransmits | Observation |
|---|---:|---:|---|
| CUBIC | 8.7626 / 8.6754 | 4,831 / 4,511 | Both directions remained active |
| BBR | 9.1434 / 4.2779 | 331 / 939 | Reverse nearly zero during seconds 12–21 |

Assuming untagged 10 GbE, IPv4 TCP timestamps and MSS 8948, the theoretical TCP payload ceiling is 9.9004 Gbit/s per direction. The two results are 88.5% / 87.6% and 92.4% / 43.2% of that ceiling respectively. They are diagnostic controls, not matrix cells or a best-of-three result. The fixed-order, single-run comparison changes the offered traffic as well as the congestion algorithm and does not establish a root cause.

For one reverse-flow ACK stream in the BBR run, endpoint captures matched 103,888 fingerprints, with 41 sender-only and 49 receiver-only fingerprints; 35 sender-only fingerprints fell between 11.2 and 11.3 seconds. The CUBIC control had 2,927 sender-only fingerprints while both directions remained active. Both captures reported zero kernel drops, but filter counts exceeded saved packet counts, capture windows differed, and the sender's outgoing capture precedes wire transmission. These gaps therefore do not identify a loss stage. Earlier socket samples had shown the reverse senders at a small congestion window before ACK progress stopped, consistent with but not proof of a BBR ProbeRTT-related trigger. The generator defaults remained BBR. After the controls, the DUT and generators were restored to MTU 1500; `ask-check` reported 47/49 OK, two expected no-carrier warnings and zero failures, and baseline pings passed in both directions.

**Retained BBR socket timeline.** In four separate captures, one-second samples approximately 10 seconds after the iperf start found 6–8 of the eight reverse data senders at `cwnd=4` and `cwnd_gain=1`; the iperf control connection was excluded. At approximately 11 seconds, all eight had recovered to windows of 36–44 segments; at approximately 12 seconds, 7–8 had fallen to `cwnd=1` as ACK progress collapsed. These are sampled snapshots, not continuous state or proof that ProbeRTT caused the failure.

The generators' DUT-facing X710 qdiscs show cumulative drops while their interface drop totals are lower; these counters have different scopes and histories that cannot be tied to the controls above. Further diagnostics need qdisc deltas before attributing missing ACKs to the DUT; see the generator limitation in `plans/ASK2-WIREGUARD-FASTPATH-PLAN.md`.

**Qdisc-instrumented BBR repeat, 2026-11-17.** After a fresh cold boot of the same image, one 25-second IPv4 `-P 8 -Z --bidir` run at MTU 9000 again collapsed the reverse direction to nearly zero during approximately seconds 12–23. Forward / reverse receiver goodput was 9.1212 / 4.3557 Gbit/s, with 999 / 95 sender retransmits. Read-only 2 Hz samples spanning 70 seconds showed no change in either generator's X710 root or child qdisc drops, or their `flows_plimit` counters; interface RX/TX drop totals also stayed unchanged. Thus the observed physical-interface qdisc counters do not explain this particular collapse. They do not rule out NIC, wire or other losses, and the extra sampling can affect timing.

The matched ACK captures again showed sender-only fingerprints around the collapse, but the outgoing tap precedes wire transmission and capture accounting remains incomplete; the aggregate DUT MAC size-bin gap does not identify individual packets or a pipeline stage. Afterward both DUT ports and generator interfaces were restored to MTU 1500, baseline pings passed in both directions, and `ask-check` reported 47/49 OK, two expected no-carrier warnings and zero failures.

**Four-point controls, 2026-11-17.** One additional CUBIC and one BBR run used separate cold boots and the same 25-second IPv4 `-P 8 -Z --bidir` workload, MTU 9000 and MSS 8948. Both used 2 Hz read-only DUT MAC, generator NIC and qdisc samples plus 1 Hz socket samples. CUBIC delivered 8.7685 / 8.6492 Gbit/s forward / reverse, with 4,682 / 4,335 retransmits; both directions remained active. BBR delivered 9.0369 / 4.3815 Gbit/s, with 2,174 / 102 retransmits, and reverse was nearly zero around seconds 12–21. These single diagnostic runs are not matrix cells or proof that the congestion algorithm caused the stall.

In the idle-bracketed whole-run jumbo size bin, the four points sender TX → DUT ingress → DUT egress → receiver good RX counted 3,067,819 / 3,067,819 / 3,063,456 / 3,063,456 forward and 3,026,074 / 3,026,074 / 3,021,855 / 3,021,855 reverse for CUBIC; BBR counted 3,158,866 / 3,158,866 / 3,157,806 / 3,157,148 forward and 1,530,642 at all four reverse points. Aggregate equality does not establish delivery of individual frames. A two-packet jumbo ping incremented all eight points equally before each test; that checks basic mapping, not high-rate counter semantics or packet identity.

In the BBR run, Dell2's `port.rx_crc_errors` rose by 659 and `rx_errors` by 660, versus a 658-frame forward jumbo egress-to-good-RX gap. This is consistent with, but does not prove, CRC-corrupted jumbo frames; the DUT transmitter, link and receiver have not been isolated. CUBIC had no Dell2 CRC increment. In the BBR small-frame bin on the reverse-ACK-carrying path, Dell1 TX / DUT eth3 RX / DUT eth4 TX / Dell2 RX counted 918,385 / 918,384–918,385 / 901,623 / 901,618. Two successive half-second DUT ingress-minus-egress deltas, ending around seconds 11.519 and 12.019, were +8,564 and +7,835. CUBIC also had a substantial aggregate small-bin gap, but its comparable half-second gaps near onset were +354 to +711. The burst coincided with the BBR blackout onset; these are size-bin aggregates and do not establish packet identity, ACK loss, CRC size class or a discard stage. Generator physical-qdisc drop and `flows_plimit` counters were unchanged in both controls. BMI counters were disabled on these boots, so their zero values do not exclude discards. MTUs were restored to 1500, both directional pings passed, and `ask-check` reported 47/49 OK, two expected no-carrier warnings and zero failures.

**BMan-instrumented BBR repeat, 2026-11-17.** After another cold boot of the same image, the same workload delivered 9.2325 / 4.0600 Gbit/s forward / reverse, with 589 / 75 retransmits; reverse was essentially idle from seconds 12–25. The hardware free-buffer registers for the live eth3 / eth4 BPIDs 3 / 4 were sampled read-only at 100 Hz alongside the four-point counters. Among 2,500 samples during the 25-second run, minima were 2,829 / 2,637 buffers; the largest gap between samples over the 95-second recording was 12.005 ms. This observes no sustained pool exhaustion, but cannot exclude depletion between samples. Both RX `FMBM_RSTC` values were read as zero before the run, so the disabled BMI discard counters remain inconclusive.

The whole-run small-frame counts on the reverse-ACK-carrying path were 849,905 / 849,905 / 849,212 / 849,212; the largest near-onset half-second DUT ingress-minus-egress gap was +646. No generator NIC error-counter, physical-qdisc drop or `flows_plimit` increment was observed, and DUT netdev drop/error counters did not move. Those counters do not cover every hardware loss path. This repeat shows a similar collapse with a smaller observed aggregate small-bin gap and without the earlier Dell2 CRC increment; it does not prove identical causation or isolate missing packets. MTUs were restored to 1500, both directional pings passed, and `ask-check` again reported 47/49 OK, two expected no-carrier warnings and zero failures.

Between socket samples approximately 12.044 and 19.048 seconds after the iperf JSON start, each of the eight matched Dell2 reverse senders retransmitted three data segments, received no TCP segments and had no newly acknowledged bytes. The matching Dell1 receivers each saw three data segments, sent three segments according to TCP and accepted no new payload. During seconds 15–19, conservatively bounded four-point small-frame counts were Dell1 TX 8, DUT eth3 RX 8–12, DUT eth4 TX 0 and Dell2 RX 0. Sample boundaries differ, TCP output counts precede wire delivery, and the size bins are aggregates: this strongly narrows the missing return traffic to the DUT path but does not identify individual packets, prove a drop rather than misdirection or queueing, or locate an internal stage.

In the earlier separate cold-boot capture with full FE record dumps, eight table-3 entries had reverse-flow TCP keys and averaged approximately 67.7 bytes per hit. Their hit increments tracked the incoming small-frame aggregate during several blackout samples, while the eth4 small-frame aggregate stayed near zero. Two non-atomic dumps started about 7.1 and 24.5 seconds after iperf began: all 32 records appeared at the same DMA address, table and index, with identical first 256 bytes in both dumps and advancing packet counts. This shows no record-byte difference at those snapshots, not continuous identity or unchanged state between reads. The earlier size bins were inferred from total-minus-jumbo counts rather than calibrated directly; a key hit does not prove that the corresponding packet completed FE action execution or reached the intended egress.

The post-run live ASK settings were `offload_delay_ms=500`, `offload_flood_rate=1000`, and both volume thresholds zero. The current source applies these admission gates when a flow is offered for offload, not as a per-packet mouse/elephant decision. The BBR sampler saw 32 FE record address suffixes with the same sampled set from about 1.6 through 24.6 seconds, including the blackout; suffixes and one-second samples cannot prove uninterrupted record identity or exclude replacement between samples. These observations do not support the admission delay alone as an explanation for the later blackout, but do not rule out other lifecycle defects or establish that the current source exactly matches the deployed module.

**Jumbo runner.** `bin/testrig-ask2-9k.sh` permits read-only `preflight` and
`status`; `up`, `down` and `run` refuse before remote access until FE
disengagement and restoration are safe. An existing lease is retained; no
ownership-safe recovery procedure is established. Do not delete the lease,
change offload state or run other traffic tests on potentially affected links
until the ownership and hardware state have been resolved. `status` and
`--preflight` do not prove FE walker or QMan drain. The candidate transition
code takes a boot-bound lease,
claims generator links, attempts to disengage ASK, checks `fe_arm`, changes
MTUs, re-arms and probes the paths. Host tests model its ownership checks.
Earlier cold-boot `up`/`down` validation does not prove FE walker or QMan
quiescence.

**Paired routed jumbo coordinator.** `bin/testrig-paired-jumbo.sh` alternates
VyOS then OpenWrt for each repetition of `unicast-9k`, `nat-9k`, `vlan-9k`
and `vlan-port-9k`. It requires a finished 1500-byte paired reference in
`PARITY_REFERENCE_DIR`, independently confirmed boot IDs in
`PARITY_ASK2_COLD_BOOT_ID` and `PARITY_VENDOR_COLD_BOOT_ID`, the eight
`BR_G{1,2}_IPV{4,6}` / `JUMBO_G{1,2}_VLAN{4,6}` endpoints, and `JUMBO_BPID`.
The pool must match the live BPID of both OpenWrt test ports. `--preflight`
checks both adapters and sends baseline pings without changing MTUs. A normal
run checks generator settings, source hashes and owned cleanup before accepting
paired results. Its socket checks cover the benchmark ports and sockets bound
to the test addresses, not all possible raw or kernel traffic. Matched live
preflight passed on the same cold boots as the 1500-byte reference without
changing MTUs. Normal paired runs are disabled until ownership-safe offload
disengagement and restoration are proven; `bridge-9k` is not supported.

**Jumbo loss stage unresolved.** Matched cold-boot runs (`RIG_PARITY=1`, BBR, P8, 32 s, unicast-v4 bidirectional) gave 10.65, 11.62 and 10.57 Gbit/s; a direction stalled for about 12 s. A 1 Hz read-only mEMAC/BMI sampler, with `FMBM_RSTC` set to 0x80000000 on 0x10/0x11/0x30/0x31 (it is 0 after a cold boot), found correlated MAC RX-minus-TX gaps and BMI `rfdc` (+0x214) increments on the affected receive path. `rfdc` is a generic frame-discard counter, so this correlation does not identify a status bit or prove that the counted frames are the missing ACKs. `rodc`/`rldec`, MAC `tfcs`/`terr`, generator `rx_crc_errors`, and sampled QMan congestion/error counters stayed at zero; these observations weaken, but do not exclude, silent failures at those stages. Earlier `fdc` zero readings were taken with BMI statistics disabled and are inconclusive. DUT2 did not stall under the same harness.

The default `FMBM_RFSDM` value 0x000a0000 includes the PHYSICAL and CLS_DISCARD bits, but this alone does not prove that either status caused the loss. Two reversible eth3 mask tests changed `RFSDM`/`RFSEM` first to 0x80000/0x126e0e8 and then to 0/0x12ee0e8. Both reproduced the reverse-direction stall; neither produced visible `Err FD status` or `rx_errors`. Original masks were restored after each test. These tests do not support the earlier claim that a simple RFSDM-filtered classifier-discard path is established.

A separate cold-boot capture (`t9`) sampled all FE ehash statistics and raw records during a reproduced 12-zero-interval reverse stall. It showed 32 records in tables 4 and 5, with stable raw keys at the sampled times. The 66-byte-average records in table 4 (key direction 10.99.1.112 to 10.99.2.113) nearly stopped accumulating hits during the stall, while the corresponding small records in table 5 continued to advance. Some table-5 jumbo-sized records also continued advancing despite zero receiver throughput. FE hit counters therefore show class- and direction-specific changes, but do not establish that a matching packet completed its action, reached the intended egress, or was delivered; the snapshots are non-atomic and aggregate.

A second synchronized capture (`t11`, same cold boot and normal masks) scored 10.420 Gbit/s: forward 7.372, reverse 3.048, with 3 forward and 12 reverse zero intervals. During the reverse stall, the 66-byte-average table-4 record hits were 12, 8, 4, 4, 0, 0, 8, 0, 0, 0, 0, 0 per second; eth3 `rfdc` deltas in the same samples were 14, 10, 6, 7, 2, 2, 10, 2, 2, 2, 0, 0. Table-5 small records continued near 49,000 hits/s while its jumbo-sized records stopped. At stall onset, table-4 small-record hits were 35,710 while eth3 `rfdc` rose by 744; the preceding second had 42,512 such hits and no `rfdc` increment. The later rate correlation is consistent with the residual ACK-sized frames reaching FE records and then being discarded, but does not prove packet identity or action completion; the onset mismatch leaves the loss stage unresolved.

The live debugfs tree exposes FE record statistics and keys, but no per-scheme KeyGen or per-node coarse-classification hit/miss counters. Stable sampled keys do not establish intact bucket links or unchanged action fields. A classifier miss, an FE/PCD discard after lookup, direct-enqueue rejection, and later forwarding loss therefore remain possible. No silicon source fix is justified yet. Next, add a bounded read-only dump of the active FE record/action bytes (including the destination FQID) under the existing `fe_lock`, then correlate it with egress-side MAC/queue counters through stall onset; the current `fe_flow` and `fe_ehash_stats` output is insufficient.

**Loss stage localized: egress FQ tail drop, 2026-11-19.** One cold boot of the image from commit `685905c0` was used. 5 Hz per-port BMI/QMI samples were taken (`FMBM_RSTC` enabled, restored afterwards), together with 5 Hz `QUERYFQ_NP` probes of the F-199 no-confirm egress FQs through ask `fq_probe`. The probed FQs were 0x2b9 for eth4 egress and 0x2ba for eth3 egress. Each egress port has one FQ, and it carries both the forward data and the reverse ACKs to that port. Four bidirectional 9k BBR P8 runs were made.

- **Where the frames are discarded.** On the RX ports, QMI `etfc` equalled BMI `rfrc` in every interval. TX dequeue equalled RX `rfrc − rfdc`, and `tfdc`, `rodc`, `rfldec` and `rlfc` were 0. The RX `rfdc` discards therefore occur at QMan enqueue rejection, after the QMI enqueue attempt, not in the classifier or BMI.
- **The egress FQ sits at its tail-drop threshold.** `byte_cnt` is returned raw big-endian and needs a byte swap. During a stall, 0x2b9 alternated only between 4,191,510 and 4,200,524 bytes. These are 465 × 9014 and 466 × 9014, straddling `DPAA_FQ_TD` = 4 MiB (4,194,304). The 4 MiB value comes from the mutate in `bin/ci-setup-kernel.sh`, and F-199 sets `QM_FQCTRL_TDE` with no CGE.
  - eth3 RX and eth4 TX both ran at line rate, about 138.4 kfps, so the standing queue could not drain.
  - The queue built after a roughly 200 ms dip near 10 s, which may be BBR ProbeRTT. It held at 3.5–3.7 MB without discards for about 10 s, then reached the threshold together with `rfdc` bursts of 200–2,000 per 200 ms.
- **Discards follow the threshold.** Across 2,542 intervals of 200 ms:
  - Near the threshold: 151 intervals, 102 of them with discards, 9,887 discards in total.
  - Below the threshold: 2,391 intervals, 9 of them with discards, 3,844 discards in total. In all 9 the FQ swung by about 3.5 MB between samples, so it could have reached the threshold between samples.
- **A rate cap reduces the stall.** Capping each stream at 1.15 Gbit/s (9.2 Gbit/s per direction) reduced threshold samples and stalls: 16.9 and 17.7 Gbit/s, with 2 and 0 zero-seconds. The uncapped run gave 10.5 Gbit/s with 19 zero-seconds.
- **Vendor design differs.** In `cdx/devman.c` the vendor creates 8 or 16 flow-hashed egress FQs per port. They share a frame-mode CGR with tail drop at about 276 frames at 10G / 9000, and have no FQD TDE.

This localization needed no extra counter image, because QMan has no cumulative per-FQ counters.

**Cause proven: tail-drop lock-out of cross-direction ACKs, 2026-11-19.** The test used the same cold boot, harness `bin/testrig-ask2-9k.sh`, and one bidirectional 9k P8 run per arm. A signed, out-of-tree lab module changed only TDE/`TD_THRESH` on the idle FQs 0x2b9/0x2ba. It did this with RETIRE → OOS → `INITFQ_SCHED`, and every other FQD field read back unchanged. Afterwards the module restored 4 MiB, the readback matched the original, and the module was unloaded. Artifacts are in `oracle/dut1-9k-tdab-20261119T1605Z/a1…a10`.

| Arm | Threshold | CC | Total Gbit/s | Reverse zero-s | Max FQ bytes | `rfdc` | `rodc` |
|---|---|---|---|---|---|---|---|
| a1 | 4 MiB | BBR | 10.46 | 13 | 4,200,524 | 2,660 | 0 |
| a2 | TDE off | BBR | 15.92 | 0 | 7,343,534 | 0 | 263,695 |
| a3 | 8 MiB | BBR | 16.23 | 0 | 6,106,504 | 0 | 316,766 |
| a4 | 2 MiB | BBR | 9.95 | 17 | 2,100,262 | 2,030 | 60 |
| a5 | 4 MiB restored | BBR | 11.27 | 14 | 4,201,976 | 20,657 | 2,023 |
| a8 | 4 MiB | CUBIC | 17.76 | 0 | 4,193,292 | 25,478 | 25,457 |
| a9 | 8 MiB | CUBIC | 17.81 | 0 | 7,283,274 | 0 | 19,722 |

- **The threshold controls the collapse.** The occupancy ceiling tracks the threshold exactly (233 or 466 frames). With BBR the collapse appears at 2 and 4 MiB, vanishes with TDE off or at 8 MiB, and returns when 4 MiB is restored. Above the threshold, occupancy is capped instead by BMan pool exhaustion (about 605 frames). The loss then moves to `rodc` out-of-buffer discards, which SACK recovers: 180k retransmits but only 18 RTOs. Their class mix is not measured; jumbo frames need more buffers than ACKs. At 2 MiB there were only 2.1k retransmits but 120 RTOs.
- **The hardware never stalls.** At 5 Hz, TX dequeue equals accepted RX in every interval at line rate. During a lock-out, one direction's FQ is pinned at the threshold while the starved direction's own FQ is empty. Its ingress then carries only ACKs, and it recovers exactly when the pinned queue drains.
- **The tail drop selectively discards ACKs.** Endpoint captures matched pure ACKs between senders and receivers by flow, ack and TSval (a10, 2 MiB, 14 reverse zero-seconds).
  - Lost ACKs make up about 90% of the FQ discards on the matching ingress port: 3,956 of 4,347 `rfdc` on 0x11, and 1,035 of 1,169 on 0x10.
  - Losses occur only in lock-out seconds, at 42–100% of the starved flows' ACKs. Repeated 8/8 losses are the one RTO-retransmission ACK per flow.
  - The dominant flows' ACKs and data are almost never dropped.
- **Proven cause.** The FQD hard tail drop on the single shared per-port egress FQ starves the cross-direction ACKs. A loss-insensitive BBR direction holds that FQ at its byte threshold, and the starved flows go into RTOs and stay locked out until the dominant queue drains. CUBIC backs off on loss and does not hold the queue pinned, so there is no lock-out even with 25k tail drops.
- **Unproven micro-mechanism.** Why admission discriminates so strongly against ACKs is not established at packet scale. Self-clocked refill of each freed slot (a Floyd–Jacobson phase effect), BBR pacing synchronisation and NIC batching all remain candidates, and 5 Hz sampling cannot resolve admission-slot timing.
- **Capture caveat.** The ACK captures used multiset matching with overlapping windows. Generator capture and NIC drop deltas were not recorded. Capture-side loss is unlikely to explain losses that appear only in lock-out seconds and match `rfdc` within about 10%.

Raising `DPAA_FQ_TD` removes this failure mode in these runs. It is not a demonstrated fix: it lengthens the standing queue and moves the loss to buffer-pool exhaustion. Candidate designs keep ACKs from sharing one hard-tail-drop FIFO with jumbo data:
- vendor-style flow-hashed egress FQs per port under a frame-mode CGR;
- a WRED-style congestion-group policy in place of the FQD TDE (a plain CGR threshold is not AQM and can still drop ACKs);
- a separate path for small or pure-ACK frames.

Each needs its own A/B, including the 9k BBR bidir lock-out, hash collisions and pool headroom. The mechanism behind the vendor board's immunity has not been measured. Changing the egress queue design is ask-first.

### Fix under test: per-flow egress FQ sets (F-316 + ask.ko)

- **Kernel (F-316).** `struct dpaa_fq` gains `td_bytes`; `dpaa_fq_init()` programs it as the FQD tail-drop when nonzero, else `DPAA_FQ_TD`. The F-199/F-295 allocator becomes `dpaa_alloc_offload_tx_fq_td(dev, td_bytes, *fqid)` (values above `DPAA_FQ_TD` rejected); `dpaa_alloc_offload_tx_fq()` stays as the `td_bytes = 0` wrapper, so WGFP is unchanged. Host check: `bin/test-dpaa-offload-fq.py` case `td`.
- **ask.ko.** Engage-time prewarm (`ask_hw_prewarm_egress_fq`, process context, never under `flow_block_lock`) allocates up to 16 extra no-confirm FQs per egress port at `egress_fq_td` (default 256 KiB, load-time only). Each routed flow takes the least-used FQ of its port's set at insert and releases it at remove; the cookie's `sink_fqid` carries it to the FE record. Bridge records and `egress_fqs=0` use the legacy 4 MiB FQ.
- **Scope.** A flow admitted while a set FQ is unused shares a tail-drop with no other flow; flows never migrate, so churn can leave two flows on one FQ. The 8-stream bidirectional cell puts 16 data/ACK flows plus the iperf control connections on each port. 16 × 256 KiB equals the legacy 4 MiB per port; it is not a BMan pool reservation.
- **Unlike the vendor.** Assignment is least-loaded, not hashed, and each FQ keeps an FQD byte tail-drop; there is no shared CGR, because FE-enqueue CGR membership is unproven.
- **A/B.** `/sys/module/ask/parameters/egress_fqs` (16 or 0) applies to new flows only; each arm needs fresh connections after the previous arm's flows have aged out. `echo 1 > /sys/module/ask/parameters/fq_probe` prints the set with per-FQ `users`.
- **Intra-WQ credit.** All set FQs of a port sit on the same port-channel WQ (6). With `ICS_CRED = 0`, QMan's intra-WQ DRR is plain per-frame round robin, so FMan TX dequeue switches FQ every frame. The TX port then stalls near 770 kfps while its queues stay backlogged, and receiver GRO aggregation falls from about 9 to about 3 segments. F-316 therefore also programs `ICS_CRED = 0x4000` (16 KiB per visit) on set FQs (`td_bytes != 0`); the legacy FQ and WGFP keep 0. `0x7FFF` disables DRR (each visit drains the FQ), which costs about 3 ms of kernel-path RTT under load. `PREFERINCACHE` had no effect.
- **Results (.185, cold boot, bidirectional BBR P8, ICS applied live with a lab module before the 0x4000 build).**

  | MTU | Arm | Gbit/s | Kernel-path ping avg under load |
  |---|---|---|---|
  | 9000 | legacy FQ (`egress_fqs=0`) | 10.5–11.7, reverse zero-seconds 12 | 0.93 ms |
  | 9000 | set, ICS 0 | 18.3–18.4 | 0.76 ms |
  | 9000 | set, ICS 0x4000 | 18.5 | 0.84 ms |
  | 9000 | set, ICS 0x7FFF | 19.3 | 3.8 ms |
  | 1500 | legacy FQ | 17.5–17.9 | 0.63 ms |
  | 1500 | set, ICS 0 | 15.6–15.7 | 0.71 ms |
  | 1500 | set, ICS 0x1000 / 0x2000 / 0x4000 | 16.8 / 17.1 / 17.3–17.4 | 0.74 / 0.82 / 0.92 ms |
  | 1500 | set, ICS 0x7FFF | 17.4–17.6 | 3.2 ms |

  9000 CUBIC does not collapse on the legacy FQ (18.0); the lock-out is BBR-specific.
- **Validated build (.185, cold boot, image with ICS 0x4000 in F-316, no lab override).** Readback: all 64 set FQs (16 on each of 4 ports) `fq_ctrl=0x0200 ics=0x4000 td=262144`; legacy FQ `ics=0`.

  | MTU | Cell | Set FQs (`egress_fqs=16`) | Legacy (`egress_fqs=0`) |
  |---|---|---|---|
  | 1500 | BBR P8 | 17.30, 17.31, 17.40 (ping 0.90 ms) | 17.17, 16.50 (ping 0.63 ms) |
  | 1500 | BBR P1 | 18.04 | 16.27 |
  | 9000 | BBR P8 | 18.47, 18.53, zero zero-seconds (ping 0.86 ms) | 12.60, reverse zero-seconds 13 |
  | 9000 | CUBIC P8 | 18.56 | not rerun |

### Matched measurement protocol

The shared parity profile uses three repetitions, eight TCP streams, BBR, 32 seconds of traffic and the bounded interval 10–30 seconds. CUBIC is an explicit common control (`PARITY_CC=cubic`), not a default workaround. Extra DUT sampling and tracing are disabled on both boards. The shared scorer duration-weights complete intervals, retains zero-rate intervals and rejects missing coverage, gaps, overlaps and malformed data. It does not extrapolate bytes from an extended terminal interval. Bidirectional client interval sums mean forward sender throughput plus reverse receiver throughput, not two receiver-goodput measurements.

`bin/testrig-parity-bidir.sh --preflight` checks existing conditions without traffic or configuration changes. It compares both DUT-facing generator paths' MTUs, addresses, rings, queues, coalescing, RSS keys and fields, RPS, offload features, qdiscs and link state; the generator tuning check also verifies IRQ affinities and CPU settings. The traffic runner alternates DUT order between repetitions, uses the same SSH orchestration and validates actual iperf parameters and cell identity before accepting a pair. It retains low and software-path results rather than selecting only hardware-labelled trials. Artifacts include protocol, harness hashes, generator conditions, boot IDs and per-pair results; a failed campaign has no `COMPLETE` marker.

For already prepared MTU-1500 paths, a core comparison is:

```bash
REPS=3 bin/testrig-parity-bidir.sh nat-v4 nat-v6 vlan-v4 vlan-v6
```

`BIDIR=0` selects one-way quick cells. `RIG_PARITY=1` opts the existing matrix/WAN/WireGuard adapters into the common timing and sampling profile, but does not make their setup topologies equivalent. The legacy ASK2 bridge adapter rejects parity mode. Jumbo preparation is per board: `testrig-ask2-9k.sh` for DUT1 and `testrig-vendor-jumbo.sh` for DUT2, both honouring `RIG_PARITY=1`.

The paired runner never changes MTUs. `PARITY_MTU=9000` additionally requires both paths already at the requested MTU, a jumbo MSS in the result and `PARITY_ASK2_COLD_BOOT_ID` / `PARITY_VENDOR_COLD_BOOT_ID` matching independently confirmed cold boots. These variables are declarations to validate against live boot IDs, not permission to power-cycle or bypass the ownership gate.

Offline review proves a scoring defect, not its sufficiency as the jumbo root cause. Excluding the ambiguous tail with the common 10–29-second window still leaves large NAT6/VLAN bidirectional gaps and whole-second directional stalls in DUT1 traces. Those archived runs are unpaired, with different repetition counts and rig conditions; corrected old scores are not new matched benchmark results. The loss stage remains unresolved (see above); no silicon fix has been established.

### Step 0 result (HW, `.185`, image 2026.10.31-0245, offload engaged, cold-ish boot)

With the 0177 buffer clamp neutralised (mEMAC MAXFRM written live to 9026 after `ip link set mtu 9000`), DF pings at 3000, 3600, 4500, 6000 and 8972 passed through the DUT, and `iperf3 -P4` at MTU 9000 with hardware offload engaged ran 9.86 Gbit/s (127 retransmits), `fe_ehash_stats` hits growing (about 650k per 3 s), `rx dma error` and `rx frame physical error` 0, no kernel warnings, no wedge. The CPU RX path can reassemble scatter-gather frames with `sg_fd_to_skb()`; that does not describe the FE-hit path, which bypasses CPU reassembly. This experiment observed selected jumbo forwarding with FE hits, not universal SG safety or current-image acceptance.

The repaired `0177` no longer caps the MAC max frame by `dpaa_bp->size`; the `data/vyos-1x-036/037/043` guard ceilings are raised to 9000. Image `0511` already tested A3 at 9000: 96.9% L1 rate, 0.00291% loss and reported hardware share 1.000 (`suite-ask2-jumbo-0511`); boot type was not recorded in that evidence. The stricter zero-loss target and current-build repeat remain open, as do a 30 min MTU-9000 soak (`MemFree`, buddyinfo), MTU-1500 regression (A1, B1, C1), and feature-specific VLAN/PPPoE jumbo tests.



## 1. Goal and gap

| DUT | Largest working MTU (DF ping, 2026-10-30 battery) | Basis |
|---|---|---|
| OpenWrt + NXP ASK (.70, SDK `fsl_dpa` driver) | at least 9578 | DF pings pass at 3562, 3600, 4000, 6000, 9000, 9578; A3 at 9000: 84.9 % line rate, 0 % loss, 100 % hardware |
| vyos/ASK2 (DUT1, mainline `dpaa_eth` + our patches) | 3562 (3568 fails) in that historical battery, before clamp removal | A3 at 3562: 88.8 % line rate, 100 % hardware; later selected 9000 tests are described above |

Target: ASK2 passes A3 at MTU 9000 with offload engaged (100 % hardware share, 0 % loss, no port wedge), with the 1500 MTU baseline unchanged (A1 3.70 Mpps, B1, C1, C2) and the default memory footprint unchanged.

## 2. Root cause (corrected)

The limit is not in kernel.org. Upstream v6.18 `dpaa_eth.c` sets `max_mtu = fsl_fm_max_frm - 22` (9578 with `fsl_fm_max_frm=9600`), has no MTU clamp against the buffer size, and has an RX scatter-gather path (`sg_fd_to_skb()`), so FMan can spread a jumbo frame over several 4 KiB buffers. The 2026-08-16 qdrant note ("no RX SG in mainline", "frames above about 3700 B cannot be received") is wrong about RX scatter-gather and needs a correcting entry.

The historical limit came from two things in our tree:

1. **`0177-dpaa-memac-max-frame-from-mtu.patch`** added `dpaa_apply_max_frame()`, which formerly clamped the MAC maximum frame length to `dpaa_bp->size`; the repaired patch removes that clamp.
2. **`dpaa_bp->size` is 3584.** `DPAA_BP_RAW_SIZE` is 4096 (F-222 reverted F-203's order-1 buffers to order 0). With `CONFIG_DPAA_ERRATUM_A050385=y`, `dpaa_bp_size()` is `(4096 − 320) & ~255 = 3584` (320 is the assumed arm64 `skb_shared_info` size). Under the former clamp the largest frame was 3584 B including FCS, which is MTU 3566. That matches 3562 passing and 3568 failing. The DT on both boards has no `fsl,erratum-a050385` property, so the 256-byte rounding appears to buy nothing here.

The clamp was added because earlier oversized frames wedged the port (2026-08-16, 2026-10-22 notes). Whether the cause was scatter-gather frames reaching the offload (FE) path or the since-fixed ENQUEUE-MTU bug (F-270) is not established.

## 3. How the vendor does it

Source: OpenWrt `target/linux/layerscape/files/.../sdk_dpaa/` (`dpaa_eth.h`, `dpaa_eth_sg.c`, `dpaa_eth_common.c`); build config `target/linux/layerscape/armv8_64b/config-6.12`; live `.70`.

| Item | Vendor (SDK `fsl_dpa`) | Ours |
|---|---|---|
| Kconfig | `CONFIG_FSL_DPAA_ETH_JUMBO_FRAME=y`, `CONFIG_FSL_FM_MAX_FRAME_SIZE=9600` (Kconfig says the option requires 9600) | stock mainline, `fsl_fm_max_frm=9600` module parameter |
| RX buffer raw size | `(9600 + DPA_MAX_FD_OFFSET + sizeof(skb_shared_info) + 128) & ~(SMP_CACHE_BYTES-1)`, about 10 KB, one buffer per frame | 4096 (`DPAA_BP_ORDER 0`) |
| Allocation | `netdev_alloc_frag(SMP_CACHE_BYTES + DPA_BP_RAW_SIZE)`: page-fragment cache, normally 32 KiB order-3 blocks, about 3 buffers per block | `dev_alloc_pages(0)` |
| Usable buffer size | `dpa_bp_size() = SKB_WITH_OVERHEAD(raw) − SMP_CACHE_BYTES` | 3584 |
| MAC clamp to buffer | none; MTU limit is `dpa_max_frm − 22` | removed from `dpaa_apply_max_frame()` by repaired 0177 |
| A050385 | `fm_has_errata_a050385()` reads DT `fsl,erratum-a050385`; if set the buffer drops to 2048 and the alloc is a plain page. `.70` FMan DT has no such property, so the jumbo path is active | compile-time rounding only; DT property absent |
| Offload of jumbo flows | A3 at 9000 reported 100 % hardware, consistent with jumbo frames traversing the FE hit path in one contiguous buffer | selected 9000 FE-hit observations and historical A3 above; current acceptance open |
| Caveat in ASK source | `ask_flowtable_core.c` (`ft_detours`, A394): a jumbo frame through the offline-port detour arrives in several buffers and a flood of them timed out FMan's host-command channel, so ports with a jumbo MTU are excluded from that detour | n/a |

So the vendor reaches 9000 with large contiguous RX buffers on the same silicon and FMan microcode (210.10.1). It does not use scatter-gather on the main RX path. The same family of risk we hit with F-203/F-222 (large-order page allocation under fragmentation) exists in the vendor, but it refills 32 KiB blocks (one order-3 allocation per three buffers) instead of one order-1 allocation per buffer.

## 4. Options

Option B was adopted, but its selected observations do not close the outstanding acceptance gates.

- **Option A (deferred): vendor-style jumbo buffers.** A possible design would add a module parameter (for example `fsl_dpa.jumbo_rx=1`, default 0, settable from the kernel command line like `fsl_dpaa_fman.fsl_fm_max_frm`). When set, the RX bpool would use a raw size computed like the vendor (`9600 + headroom + shinfo + 128`, cache-line aligned) allocated with a page-fragment cache (`page_frag_alloc`) rather than one `dev_alloc_pages()` call per buffer; DMA map/unmap and `free_pages()` sites would become fragment-aware. Default behaviour (4 KiB order-0) would remain unchanged, limiting the F-222 regression risk to the opt-in mode. Fixing mode at boot would avoid re-seeding a live pool. The old idea of automatically following `dpaa_bp->size` in `dpaa_apply_max_frame()` is superseded by the repaired 0177.
- **Option B (adopted): lift the clamp and retain order-0 buffers.** CPU-bound SG frames use upstream reassembly; FE hits follow a separate path. The October observation supports selected FE jumbo traffic, not every offload or sustained workload. The vendor's A394 note remains a warning about the offline-port detour, not proof that all SG offload fails.
- **Option C: a reserved-memory (`shared-dma-pool`/CMA) region for the jumbo buffers.** Deterministic against fragmentation, at the cost of boot-time reserved memory and a DTS change. Use only if Option A fails the soak test.

## 5. Work plan

| Step | Work | Exit criterion |
|---|---|---|
| 0 | Historical Option B experiment on DUT1: live MAXFRM override and selected DF pings and engaged traffic on 0245, cold-ish boot. The proposed full sweep used `bin/testrig-mtu-steps.sh` and `bin/testrig-udp-sweep.sh` | Selected observations above; not the complete per-mode matrix |
| 1 | Before further silicon-facing changes, run the qdrant gate: at least three focused queries on BMI buffer size and `FMBM_*` fields, SG FD layout, F-203/F-222 history; cross-check `arch/fman-microcode-210-programming-reference.md` and `specs/fman-keygen-flow-key-spec.md` | Gate recorded and contradictions resolved |
| 2 | If reconsidering Option A, check how buffer size reaches FMan (`fman_port` ext buffer size, `FMBM_EBMPI`) and per-interface bpool count (`config_count`): roughly count × raw size per port | Memory budget on the 4 GB board written down |
| 3 | Option B implemented by repaired 0177; Option A Layer-1 patch and parameter are deferred, not a current requirement | Any future alternative builds and passes `bin/test-fixups.sh`, preserving default behaviour |
| 4 | Guards `data/vyos-1x-036`/`037`/`043` allow 1280–9000 without a jumbo-pool parameter | Allowed bounds are distinct from the tested subset |
| 5 | Cold-boot validation, one variable per run: MTU 3600, 4500, 6000, 9000 engaged with verified frame sizes and FE attribution; PPPoE and VLAN jumbo; investigate 2140 bidirectional collapse and repeat on current build. Run a 30 min soak at 800 Mbit/s routed (the F-222 failure case), logging `MemFree` and `/proc/buddyinfo` | No wedge or allocation failures; current A3 at 9000 meets 100 % hardware and zero-loss target |
| 6 | Regression at MTU 1500 under adopted Option B: A1, A2, B1 (16k flows), C1, C2 | Within run-to-run noise of the 2026-10-30 results |
| 7 | Repeat A3 against the vendor DUT and update `plans/ASK2-VENDOR-PERF-BATTERY.md`; retain the 0511 result and its nonzero loss | Comparison row updated; stricter zero-loss and current-build acceptance remain open |

## 6. Risks

- Port wedge (RX-deaf, cold boot required) on an oversized or scatter-gather frame. Always cold-boot before each experiment; never test on eth0.
- Option A, if reconsidered, would cost about 2.5 times the RAM with 10 KB buffers versus 4 KiB (four times with 16 KiB page-per-buffer blocks) and risks large-order allocation failure under fragmentation (F-222). Option B retains order-0; the soak and fragmentation logging still remain required.
- XDP/AF_XDP (`xdp_validate_mtu()`, the 3290 cap) must keep working; any future jumbo pool must not be selected for XDP-attached ports, or the XDP path must refuse jumbo mode.
- `ask.ko` FE records and the F-262/F-270 MTU fields (ENQUEUE MTU 0xffff) must stay correct for frames above 1500.
- Memory-footprint trade-off: the vendor notes small-frame throughput may drop with jumbo buffers; measure if Option A is revisited.
- WGFP's sink releases non-contiguous descriptors rather than processing them; selected routed FE observations do not establish broad WireGuard jumbo safety.

## 7. Out of scope

AF_XDP/VPP MTU (3290), IPv6 egress MTU handling in `ask.ko`, the F-262 PPPoE MTU-shrink case, and the DPAA2 driver.
