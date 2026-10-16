# ASK2 vs NXP ASK: offload feature parity

**Live status (2026-10-15).** `.185` runs ASK2 image
`2026.10.15-0551-rolling` (`6.18.55-vyos`); `.106` runs the NXP ASK vendor
image (`6.12.103`). The earlier one-run-per-cell comparison showed the two
boards' unidirectional throughput shifting together by about one MSS-sized
step. That is evidence of a rig-level shift, not evidence that ASK2 regressed.
The previous single-run figures below are provisional and are not final parity
verdicts.

A paired campaign is now running on dell2 as systemd unit
`testrig-parity-10x10-20261015T2130Z.service`; it is independent of the Azure
host session. It covers 16 unidirectional and 8 `iperf3 --bidir` routed,
NAT and VLAN cells, with 10 runs per board per cell, adjacent board pairs,
reversing which board runs first each round (480 measurements total). Each run
uses the same 30 s, `-Z`, 8-stream quick harness and 10 s steady-state start.
It does **not** cover PPPoE, WireGuard, packet rate, connection scale, or
latency; those remain separate follow-up campaigns.

Progress, logs, all raw per-run CSV/JSON, and final summaries are on dell2 at
`~/testrig/campaigns/parity-10x10-20261015T2130Z/`. Read `status.json` for the
live count; `measurements.csv` retains every attempt. The generated summary
reports paired means and medians, sample standard deviations, and a per-cell
95% paired bootstrap interval for ASK2-minus-vendor throughput. No low-speed
or high-CPU result is trimmed. Measurement errors and non-hardware runs are
reported separately; only complete hardware-proven pairs contribute to the
paired throughput interval. These per-cell intervals are exploratory, not a
campaign-wide multiplicity-adjusted claim or proof of equivalence. Do not
score the older single-run verdicts below as final until this campaign ends.

Throughput is Gbit/s of iperf3 `-Z` payload on the dell rig (X710, 10G DAC),
from `bin/testrig-offload-quick.sh`: 30 s per cell, 8 streams, steady state
from 10 s. "eth3→eth4" is dell1 → DUT eth3 → DUT eth4 → dell2 and "eth4→eth3"
is the reverse, so each port is measured as ingress and as egress; each is one
client. "bidir" is one client with `iperf3 --bidir` (8 streams each way, both
directions' streams connect before data flows); Gbit/s is the sum of both
directions. NXP ASK is `.106`, ASK2 is `.185`. Every cell is checked as
hardware-forwarded (per-record hit counts on ASK2, frames that never reach the
kernel on `.106`). Dates are 2026. "—" means not offloaded.

A cell holding two numbers is written `v4 <Gbit/s> / v6 <Gbit/s>`: the label
always comes before its number, so `v4 9.28 / v6 9.15` is IPv4 9.28 and IPv6
9.15.

**Pass rule: ASK2 slower than NXP ASK in any measured cell is a FAIL** and must
be investigated, whatever the size of the gap. For the live 10-run campaign,
compare paired per-cell means and report confidence intervals separately;
uncertainty does not convert a lower ASK2 mean into a pass. The feature tables
below retain initial one-run readings for context only: their PASS/FAIL labels
are provisional and superseded by the same-session campaign.

### Same-conditions vendor re-test (2026-10-15)

The 0551 one-way numbers came out about 1.3 % below the earlier ASK2 batches
(9.41 → 9.28, 9.28 → 9.15) on every cell, so `.106` was re-run back to back
with the same harness and cells (`oracle/quick-106-retest-uni-2100.csv`,
`oracle/quick-106-retest-bidir-2111.csv`). The vendor dropped by the same step,
so the shift is the rig (the known MSS level step), not ASK2. One run per cell;
both boards fully in hardware.

| Cell | NXP ASK uni (fwd / rev) | ASK2 uni (fwd / rev) | NXP ASK bidir | ASK2 bidir |
|---|---|---|---|---|
| Routed v4 | 9.40 / 9.28 | 9.28 / 9.29 | 18.03 | 18.15 |
| Routed v6 | 9.16 / 9.15 | 9.27 / 9.15 | 17.86 | 17.85 |
| NAT44 | 9.29 / 9.28 | 9.28 / 9.29 | 16.53 | 18.15 |
| NAT66 | 9.15 / 9.15 | 9.15 / 9.16 | 17.66 | 17.91 |
| VLAN translate v4 | 9.26 / 9.26 | 9.26 / 9.26 | 18.13 | 18.17 |
| VLAN translate v6 | 9.13 / 9.14 | 9.13 / 9.25 | 17.87 | 17.82 |
| VLAN↔port v4 (strip / add) | 9.26 / 9.26 | 9.26 / 9.26 | 12.61 | 12.94 |
| VLAN↔port v6 (strip / add) | 9.13 / 9.13 | 9.13 / 9.14 | 12.80 | 12.93 |

This one-run-per-cell retest is retained as an initial checkpoint, not as the
10-run campaign result. Under those conditions 13 of the 16 one-way values agree within
0.01 Gbit/s. Three differ by one MSS level step (about 1.2–1.3 %): routed v4 forward
favours the vendor (9.40 vs 9.28), while routed v6 forward (9.27 vs 9.16) and
VLAN translate v6 reverse (9.25 vs 9.14) favour ASK2. These look like
single-run level jumps rather than a dataplane gap. Two-way, ASK2 is ahead in
six cells (largest: NAT44 +9.8 %, a single vendor dip) and within 0.3 % below
in two (routed v6 17.85 vs 17.86, VLAN translate v6 17.82 vs 17.87). The older
table values below predate the rig shift. Both
the original and this one-run retest are superseded for final statistical
comparison by the active paired campaign.

## Routing and NAT

64 B rows use `testrig-pktgen.sh` (single primed flow).

| Feature | Direction | NXP ASK (.106) | ASK2 (.185, 0551) | Verdict |
|---|---|---|---|---|
| Routed | eth3→eth4 | v4 9.28 / v6 9.15 | v4 9.28 / v6 9.27 | provisional PASS (1 run) |
| Routed | eth4→eth3 | v4 9.40 / v6 9.15 | v4 9.29 / v6 9.15 | provisional FAIL v4 (−1.2 %); campaign pending |
| Routed | bidir | v4 17.70 / v6 17.72 | v4 18.15 / v6 17.85 | provisional PASS (1 run) |
| NAT (v4 NAT44 masquerade, v6 NAT66) | eth3→eth4 | v4 9.28 / v6 9.15 | v4 9.28 / v6 9.15 | provisional PASS (1 run) |
| NAT (v4 NAT44 masquerade, v6 NAT66) | eth4→eth3 | v4 9.28 / v6 9.28 | v4 9.29 / v6 9.16 | provisional FAIL v6 (−1.3 %); campaign pending |
| NAT (v4 NAT44 masquerade, v6 NAT66) | bidir | v4 17.86 / v6 17.68 | v4 18.15 / v6 17.91 | provisional PASS (1 run) |
| Port translation (PAT) | | in the NAT numbers | in the NAT numbers | |
| 64 B, highest rate at ≤ 0.001 % loss | one way | not stable at 2.79 Mpps; clean at 1.84 Mpps | pending | pending |
| 64 B, 3.14 Mpps offered | one way | loss 0.011–0.053 % | pending | pending |
| 64 B, unpaced flood (14.7 Mpps offered) | one way | 3.40 Mpps delivered | pending | pending |
| Per-flow counters | | yes (`cmm` query) | yes (`ynl dump-flows`, matches the FMan record) | n/a |

## VLAN

The VLAN sits on eth3 in both tag cells (strip tag eth3→eth4, add tag
eth4→eth3); the other pairing (add tag eth3→eth4, strip tag eth4→eth3, VLAN on
eth4) is not measured. VLAN↔port bidir is strip tag in one direction and add
tag in the other at the same time; both boards lose frames at the MAC receive
FIFO on this traffic mix, which caps both near 13 Gbit/s.

| Feature | Direction | NXP ASK (.106) | ASK2 (.185, 0551) | Verdict |
|---|---|---|---|---|
| Strip tag (VLAN in, untagged out) | eth3→eth4 | v4 9.38 / v6 9.13 | v4 9.26 / v6 9.13 | provisional FAIL v4 (−1.3 %); campaign pending |
| Add tag (untagged in, VLAN out) | eth4→eth3 | v4 9.26 / v6 9.13 | v4 9.26 / v6 9.14 | provisional PASS (1 run) |
| Strip tag + add tag (VLAN↔port) | bidir | v4 12.88 / v6 12.79 | v4 12.94 / v6 12.93 | provisional PASS (1 run) |
| Translate VID (VLAN in, other VLAN out) | eth3→eth4 | v4 9.26 / v6 9.13 | v4 9.26 / v6 9.13 | provisional PASS (1 run) |
| Translate VID | eth4→eth3 | v4 9.26 / v6 9.13 | v4 9.26 / v6 9.25 | provisional PASS (1 run) |
| Translate VID | bidir | v4 17.28 / v6 17.84 | v4 18.17 / v6 17.82 | provisional FAIL v6 (−0.1 %); campaign pending |
| NAT across VLANs (VLAN 10 in, masquerade, VLAN 20 out) | eth3→eth4 | v4 9.26 / v6 9.13 | pending | pending |
| QinQ / 802.1ad | | supported, not measured | — | |
| DSCP → VLAN priority | | supported, not measured | — (pushed tags carry PCP 0) | |

## PPPoE

The vendor session is a runtime `pppd` client on `.106` eth3 named `pppoe10`
against a second `pppoe-server` in dell1's `n106` namespace
(`bin/testrig-pppoe-vendor.sh up|down|status`). Same credentials, concentrator
addresses (10.99.50.1 / `fd99:50::1`) and route layout as the ASK2 session, so
both boards see identical traffic. RPS is set on dell1's DUT-facing NIC for
both boards (that NIC cannot RSS-hash PPPoE frames).

| Feature | Direction | NXP ASK (.106) | ASK2 (.185, 0551) | Verdict |
|---|---|---|---|---|
| PPPoE decap (PPPoE in on eth3, plain IP out on eth4) | eth3→eth4 | v4 9.28 / v6 9.11 | v4 9.25 / v6 9.11 | provisional only; not same-session |
| PPPoE encap (plain IP in on eth4, PPPoE out on eth3) | eth4→eth3 | v4 9.23 / v6 9.14 | v4 9.23 / v6 9.10 | provisional only; not same-session |
| NAT over PPPoE (masquerade out of the session) | eth4→eth3 | v4 9.27 | pending | pending |
| PPPoE over a VLAN | | supported, not measured | — | not covered on either board |
| PPPoE relay | | supported, not measured | — | |

The 0551 PPPoE values are initial ASK2-only runs; the vendor's older values
are not a same-session baseline. Rerun both boards with the vendor PPPoE
session up and dell1 RPS enabled before scoring or attributing any gap.

### Fragmenting into the PPPoE MTU

dell2 sends 6000 UDP datagrams at 500 pps with a 1500-byte IP packet each
(`bin/testrig-pppoe-frag.py ask2|vendor`), through the DUT into the 1492-byte
session toward dell1, which counts the datagrams that arrive intact. Hardware
proof is the DUT's kernel RX count (`ethtool -S`, `rx packets [TOTAL]` on
eth3 + eth4). A "warm" case first sends 1500 small datagrams on the same
5-tuple so the flow is offloaded before the oversize datagrams start. On ASK2
the FMan fragmentation block (`0x1A54300`) is a second proof.

This is a correctness test, not a throughput test. The **vendor result is a
known defect**: its hardware fragments reach dell1 and reassemble, but the
first fragment's UDP header is all zeros, so the UDP layer rejects every
datagram (0 of 6000 intact, v4 and v6). ASK2 previously delivered 6000/6000
intact; re-verify on 0551.

| Case | NXP ASK (.106) | ASK2 (.185, 0551) | Verdict |
|---|---|---|---|
| Control, 1000 B IP packet (no fragmentation), v4 and v6 | offloaded | pending | pending |
| IPv4, DF clear, warm flow | hardware; **0 of 6000 intact** | pending (prev 6000/6000) | pending |
| IPv6, warm flow | hardware; **0 of 6000 intact** | pending (prev 6000/6000) | pending |
| IPv4, DF clear, new flow | hardware from first packet; 1 of 6000 intact | pending | pending |
| IPv4, DF set | host path, ICMP frag-needed, 0 delivered | pending | pending |
| IPv6, new flow, oversize first packet | kernel Packet Too Big, 0 delivered | pending | pending |

Two rig traps for the re-run: the DF-set case makes the DUT send ICMP
frag-needed and dell2 then caches a 1492 path MTU and pre-fragments every later
datagram, so the probe must flush dell2's route cache before every case and
abort if a cached `mtu` remains; and a cold IPv6 flow whose first packet is
oversize never gets offloaded (the kernel drops it and conntrack never confirms
the flow), so only a warm flow tests v6 fragmentation.

## Bridging

| Feature | NXP ASK | ASK2 |
|---|---|---|
| Learned-MAC bridging (known-unicast IPv4/IPv6) | supported, not measured | Historical pre-0551 result: 9.41 one way at 1.5 % CPU (software 2.22 at 32.9 %), `-P 4`, 15 s; re-measure pending |
| Static FDB entries | supported, not measured | Historical pre-0551 result: 20/20 frames in hardware; re-verification pending |
| Bridge firewall before offload | yes (hook after the filter) | yes (same hook point) |
| MAC move, STP, isolation, ageing | yes | yes; idle records gone within 25 s at a 10 s ageing time |
| VLAN-aware bridge, same tagging (access↔access, trunk↔trunk, tagged frames on a non-filtering bridge) | supported, not measured | B7a behavior on an earlier image; throughput not measured per VLAN mode, current-image validation pending |
| VLAN-aware bridge, strip/add (access↔trunk) | supported, not measured | — |
| Routing through a bridge (IRB) | supported, not measured | — |
| Routed and bridged on one port | supported | — (one table per port) |
| PPPoE through a bridge | supported, not measured | — |

## Multicast and tunnels

| Feature | NXP ASK | ASK2 |
|---|---|---|
| IPv4 multicast | supported, not measured | — |
| IPv6 multicast | supported, not measured | — |
| 6in4 tunnel | supported, not measured | — |
| 4in6 tunnel | supported, not measured | — |

## QoS

| Feature | NXP ASK | ASK2 |
|---|---|---|
| Ingress policer | per-queue policer, not measured | Historical pre-0551 result: caps 10/25/50/80 Mbit/s measured 8.8/21.9/43.7/70.3 (payload), tested with port offload disabled; current-image validation pending |
| Rate limit on offloaded traffic | supported (`QM_FF_RATE`), not measured | — |
| Egress shaping and scheduling (CEETM: shaper, weighted and class queues) | supported, not measured | — |
| DSCP → queue mapping | supported, not measured | — |
| Exception-path rate limit (traffic punted to the CPU) | supported (`QM_EXPT_RATE`), not measured | — |

## IPsec

None of this is built yet: `ASK2-IPSEC-OFFLOAD-PLAN.md` is "DRAFT, NOT
STARTED" and `ask_xfrm.c` / `ask_caam.c` / `ask_op.c` are lifecycle stubs. The
vendor offloads ESP through FMan → SEC (CAAM) → FMan with SAs pushed by `cmm`.

| Feature | NXP ASK | ASK2 |
|---|---|---|
| ESP tunnel mode, IPv4 | supported, not measured | — |
| ESP tunnel mode, IPv6 | supported, not measured | — |
| AES-CBC with HMAC-SHA1/256/384/512 | supported, not measured | — |
| AES-GCM / CCM / CTR / GMAC | supported, not measured | — |
| Legacy algorithms (3DES, DES, HMAC-MD5, AES-XCBC) | supported, not measured | — |
| NAT traversal | supported, not measured | — |
| ESN and anti-replay | supported, not measured | — |
| SA lifetime, rekey, expiry | supported, not measured | — |
| Fragmenting encrypted packets | supported, not measured | — |
| SEC crypto rate policer | supported, not measured | — |

## WireGuard

Neither stack offloads WireGuard: ChaCha20-Poly1305 runs in software on the
A72 cores (an ASK2 fast path is only planned,
`ASK2-WIREGUARD-FASTPATH-PLAN.md`), so these rows compare the software crypto
and forwarding cost of the two kernels. The DUT terminates the tunnel; the
encrypted leg runs over one port and the plain leg over the other. One tunnel,
4 TCP streams, MTU 1420, 30 s runs, and a new tunnel for every rep (the serial
transmit worker lands on a different CPU with each new `wg0`, so a single
tunnel is bimodal; the figure is the median of the reps). Run with
`bin/wg-parity.sh 106|185` and build the table with `bin/wg-parity-table.py`.

| Tunnel on | Direction | NXP ASK (.106) | ASK2 (.185, 0551) | Verdict |
|---|---|---|---|---|
| eth3 | up (DUT encrypts) | v4 1.39 / v6 1.27 | pending | pending |
| eth3 | down (DUT decrypts) | v4 1.21 / v6 1.22 | pending | pending |
| eth3 | bidir | v4 1.42 / v6 1.42 | pending | pending |
| eth4 | up (DUT encrypts) | v4 1.34 / v6 1.30 | pending | pending |
| eth4 | down (DUT decrypts) | v4 1.31 / v6 1.29 | pending | pending |
| eth4 | bidir | v4 1.57 / v6 1.44 | pending | pending (see open item) |

**Open item — WireGuard eth4 bidir steering.** On the earlier image the eth4
bidir cell was the one WireGuard FAIL, and the cause was isolated to the RPS
steering default, not the engine: VyOS `offload rps`
(`vyos/ifconfig/ethernet.py` ~510–538: `rps_cpus &= ~1` plus RFS) excludes
cpu0 and makes it a dispatcher, leaving ~18 % of the box idle. A 10-rep A/B
(`bin/wg-rps-ab.sh`) showed the vendor-style per-port single-CPU mask
(eth3=2, eth4=1, flows 0) at +~20 % over the product default and +~20 % over
the vendor, with no range overlap; the product default was only +1–2 % over
the vendor. Changing the shipping mask is a `vyos-1x` change
(`ethernet.py`, patch 031) and still needs the owner's decision. Re-run the
eth4 bidir A/B on 0551 and extend eth3 to 10 reps before deciding.

## Scale and robustness

All ASK2 figures here predate the 0551 stack (F-264, `offload_delay_ms`) and
must be re-measured with `bin/testrig-compare.sh` / `bin/testrig-cps.py`.

| Feature | NXP ASK | ASK2 (re-measure on 0551) |
|---|---|---|
| New connections per second (plus 20k held) | 20k/s (external tuned-ASK benchmark, other rig) | pending |
| 8,000 concurrent sessions (bidir 64 B line rate) | 8k-session PDR in the external benchmark | pending |
| Flow install rate | ≥ 2,370/s | pending |
| Churn soak | not measured | bridge: 100 cycles × 64 learned MACs, 0 errors; routed: 1,123 cycles, 0 FMan stalls (re-verify) |

## Not counted

- **Wi-Fi, RTP relay / VoIP / socket offload** (the vendor's 3-tuple tables):
  permanently out of scope (`OFFLOAD-CAPABILITY-PLAN.md` §1.14).
- **NAT46 / NAT64:** neither stack can do it in hardware.
- **IPsec transport mode:** the vendor does not offload it on LS104x
  (`cmm/src/module_ipsec.c:467`).
- **IP reassembly:** the vendor's shipped `cdx_pcd.xml` comments the fragment
  distributions out, so both stacks pass fragments to software.
- **GRE over IPv6:** the vendor accepts the tunnel, but its record builder
  only handles 6in4 and 4in6 (`create_tunnel_insert_hm()` in `cdx_ehash.c`),
  so it is not offloaded.
- **MACVLAN, ICC, packet capture:** the vendor's `cmm` has them, but `cdx.ko`
  registers no handler, so the vendor does not offload them either.

## Open issue: short TCP flows linger in conntrack with `offload_delay_ms=0`

With `offload_delay_ms=0` every flow is offloaded from its first packet and the
hardware swallows the FIN/RST, so a short TCP flow that ends while offloaded
never lets its close reach conntrack. Measured with 300 sequential 20 KB TCP
connections (`shortconn.py`): on `.185` at delay 0 all 300 entries stay
`ESTABLISHED` (~7,300 s timeout) 125 s after the run; the vendor lets the close
reach conntrack (286 `TIME_WAIT`, ages out in ~2 minutes). The same lingering
happens for any long flow that ends while offloaded; the old start delay only
hid it for short flows. At 20k connections/s the table would fill
(`nf_conntrack_max`) within minutes, so **this is a FAIL** for the
connection-rate and 8k-session rows once they are re-measured.

A software reaper is not the fix: when the flowtable tears an offloaded TCP
flow down as idle it restores the conntrack entry with the last state it saw
(ESTABLISHED, since the FIN never arrived), and it cannot tell a closed flow
from a quiet one. A NAT44/NAT66 flow then loses its mapping and the connection
breaks. The real fix is a TCP control-segment punt (`tcp.flags & 7` to the host
queue), which is what the vendor does with its `cdx_sp.xml` TCP soft parser.

### F-266: TCP control punt — implemented, default off, end-to-end validation pending

`bin/kernel-fixups/F_266.py` loads the vendor-identical TCP block
(`specs/reference/ask2-sp/tcp_ctl_punt.xml`, byte-identical to the vendor
`cdx_sp.xml`) by the RM rule, and ask.ko arms it per port. The first armed port
writes the 8 code words at parser byte `0x40` with IRQs off, PEN cleared and
PSTAT idle, reads them back and restores PEN; each port's TCP HXS
(`pmda[10].ssa`) goes `0x80000000 → 0x80000420` by read-modify-write with
readback; the last disarm restores both. `tcp_ctl_punt` defaults to 0 and
`ask_hw_tcp_punt_apply()` early-returns on every path when off, so a build with
F-266 behaves exactly as before until it is set (provably zero PCD writes;
`pcd-snapshot diff` stays clean).

Status on 0551: every verifiable layer is correct — HXS slot 10 matches the
standard FMan HXS ordering, the tcpschema is byte-identical to the vendor, and
`HPNIA=0x500002` = `NIA_ENG_BMI | AC_ENQ_FRAME` (direct enqueue to the port
default FQ per the programming reference §5.1). Live arm loads the 8 code words
(PEN 1→0→1) and attaches both ports (`ssa 0x80000000 → 0x80000420`, readback
OK); disarm restores cleanly. What is **not** yet proven is that the punt
actually diverts the control segments of an *offloaded* flow end to end: that
needs a shortconn transit harness (hosts on 10.99.1.x via eth3 and 10.99.2.x
via eth4 — establish and offload a TCP flow, close it, confirm the FIN reaches
conntrack). Until that passes, F-266 **ships dormant** and the conntrack-linger
FAIL stays open.

## Pending work, in order

1. Finish the active paired 10×10 dell2 campaign for routed/NAT/VLAN
   (`status.json` in the run directory above). Review missing/non-HW pairs,
   then publish paired means, uncertainty, and strict per-cell verdicts from
   its summary; replace this document's provisional rows with those values.
2. Re-run PPPoE throughput against `.106` in the same session with
   `pppoe10` up and RPS on dell1. Run the PPPoE fragmentation matrix on both
   boards. The current campaign does not include PPPoE.
3. Validate F-266 end-to-end with the shortconn transit harness: establish and
   offload a TCP flow; test FIN and RST; verify conntrack closes while data
   remains hardware-forwarded; test NAT44/NAT66 mapping survival; disarm and
   require a clean `pcd-snapshot diff`. Keep `tcp_ctl_punt=0` unless all gates
   pass.
4. Run the 64 B packet-rate repeat; separately run new-connection rate,
   concurrent-session, install-rate, and latency-under-offload tests. Current
   short-flow conntrack linger is a release blocker for scale claims.
5. Run WireGuard eth4 bidir RPS A/B on 0551 and decide whether the product
   default should change; then extend the selected comparison to eth3.
6. Continue bridge B2–B5 from `ASK2-BRIDGE-OFFLOAD-PLAN.md`; test the expected
   software fallback for PPPoE-over-VLAN and PCP ≠ 0; then schedule remaining
   multicast, QinQ/tunnel, IPsec, and egress-QoS features from their owning
   plans. Do not duplicate their implementation steps here.
