# ASK2 PPPoE offload: remaining acceptance and parity

**2026-10-15 · T-M6-SP4 · Gate A8**

Execution priority belongs to `ASK2-MASTER-PLAN.md`. Completed recognition,
decap/encap, MTU-check bring-up, rig setup and superseded soft-parser phases
are preserved verbatim in
[`archive/ASK2-PPPOE-OFFLOAD-PLAN-2026-10-15.md`](archive/ASK2-PPPOE-OFFLOAD-PLAN-2026-10-15.md).
The hard parser exposes inner IP: normal PPPoE forwarding is not blocked on
soft-parser execution. TCP/TTL safety remains a separate shared task.

## Current contract

Automatic on the engaged PPPoE source-interface port with `offload ipv4|ipv6`;
no PPPoE CLI leaf. `ask.pppoe_offload` is the global kill switch.
Discovery/LCP/IPCP/IPv6CP remain kernel-owned. Records bind the session ID
and kernel flow identity; session-down flush must remove hardware as well
as software objects before reconnect/reuse.

Routed key is 50 bytes, with outer VID at `[46..47]`, PPPoE SID `[48..49]`.
FE strip opcode `0x14`, insert `0x43`, Ethernet session EtherType `0x8864`;
control discovery EtherType `0x8863`, inner PPP v4/v6 `0x0021`/`0x0057`.
Do not resurrect the old `0x24` opcode claim or vendor relay-table
`ccbase+=0x30` as a routed-path recipe.

The egress MTU check emits `05 PREEMPTIVE_CHECKS` with real frag-info and
ENQUEUE MTU. F-262 requires the advanced-offload RX triple (params misc
`|=0x40000000`, RCMNE `0x0e`, RFENE `0x22`) with readback/reverse restoration.
Pool: 2048×2 KiB buffers, `frag_options=0x000c`; do not revert to the
superseded no-pool experiment. The detailed layouts remain in archive §3.6.

IPv4 DF-clear fragments; DF-set reaches host ICMP fragmentation-needed.
IPv6 HW fragmentation defaults on by operator decision; `ipv6_hw_frag=0`
keeps MTU-shrink flows software and produces Packet Too Big. Hardware never
produces IPv6 PTB in the tested configuration. Keep MSS clamping recommended.

## Open acceptance

| Gate | Required result |
|---|---|
| Same-session throughput | Current `.185` and `.106`, v4/v6 encap/decap, NAT over PPPoE; HW proof, CPU/retransmits, matched parameters |
| Fragmentation correctness | Both DUTs, fitting control, warm/new flow, DF clear/set, IPv6 default/opt-out; intact receiver datagrams and PMTU behavior |
| Session lifecycle | 10,000 connect/reconnect/disconnect cycles under traffic; same/reused/new SID, peer/concentrator changes, no stale records or mappings |
| MTU/MRU | Negotiated limits and changes, no oversized silent forwarding or invalid PPPoE length |
| Control/unsupported protocols | Discovery/LCP/IPCP/IPv6CP visible to kernel; unsupported PPP protocols stay software |
| Kill switch | Live 1→0 removes PPPoE HW records, SW forwards, 0→1 reoffloads; no stale SID |
| Resources | Fragment-pool exhaustion and long mixed-size soak; no RX-deaf port, error/SYNC growth or persistent resource delta |
| Current unsupported combinations | PPPoE-over-VLAN returns `-EOPNOTSUPP` before publication and forwards in software |

The old single-session flap and fragmentation probes are archived successes,
not substitutes for the unrun 10,000-cycle/exhaustion gates.

The session-lifecycle driver is `bin/testrig-pppoe-churn.sh` (ASK2 only; run
on gen2 with `RUN_ON=gen2`). Each cycle runs 100 Mbit/s TCP per flow for v4/v6
encap and decap, requires hardware hits (per-record `pkt_count` growth in
`fe_ehash_stats`) before the flap and again after the reconnect on the same
flows, and requires every flow to deliver data. `MODE=client` flaps with
`systemctl stop/start ppp@pppoe10`; `MODE=server` kills the dell1 pppd and
lets the DUT redial (holdoff 30 s); `MODE=mix` (default) makes every
`SERVER_EVERY`-th cycle server-side. Every `DRAIN_EVERY` cycles and at the
end it deletes the test conntrack entries and requires records and MURAM
`used` back at baseline, then a clean `pcd-snapshot diff`. The gate run is
`CYCLES=10000` (about 30–130 s per cycle, several days) after a cold boot.
The concentrator assigns a new DUT address per session (10.99.50.10, .11,
.12, …); only `10.99.50.1` is stable. `MODE=kill` does not flap the session:
it toggles `ask.pppoe_offload` 1→0→1 under traffic and requires zero records
and zero hardware hits (software forwarding) while off, then a re-offload on
the same flows. `RIG_NS=<netns>` runs both generators' commands as root inside
that netns (the dual-DUT rig keeps the second DUT's ports in `n106`).
The second DUT's concentrator is the dell1 unit `ask2-pppoe-server-n106.service`
(`NetworkNamespacePath=/run/netns/n106`, `-I enp1s0f1np1`, `BindsTo=lab-n106.service`,
installed by `bin/testrig-dell-net.sh gen1`). It survives pppoe-server crashes and dell1
reboots. The root-namespace `ask2-pppoe-server.service` (`enp1s0f0np0`) serves the first DUT;
leave it running. For multi-day runs use `RUN_ON=dell2` from `~/testrig` there, so the run
survives control-VM restarts. Do not stop such a run with `pkill -f testrig-pppoe-churn` over
SSH, because the pattern also matches the SSH command line and kills the session. Use `pgrep`
plus `kill <pid>`.

2026-10-18 warm smoke runs on `.185` (`2026.10.16-2012-rolling`, 6.18.55-vyos;
validation of the driver, not gate evidence): one client cycle and one
mixed client+server pair passed. Every flap produced a new SID (0x0003 → 0x0004
→ 0x0005), `pppoe10` teardown dropped all records to 0 (decap included), the
same TCP flows re-offloaded after reconnect, and MURAM `used` (52922), record
count (0) and the PCD snapshot returned to baseline. Records from completed
flows remain until their conntrack entries expire (28 before the cycle-2 flap),
which is why the drain step deletes the test entries.

### 2026-10-20 evidence on DUT2 `.156` (image 0326, 6.18.57-vyos, cold boot)

These runs are ASK2-only. Rig: generators in netns `n106` on dell1/dell2 (`enp1s0f1np1`), with
`pppoe10` up over eth3, MTU 1492, `pppoe_offload=Y`, `ipv6_hw_frag=1`. Every run
ended with records 0, MURAM `used` 52922 (baseline) and a `[OK]` `pcd-snapshot diff`.

| Gate | Result |
|---|---|
| Session lifecycle (smoke, not the 10k gate) | `CYCLES=20 MODE=mix` v4+v6 encap/decap: 0 failures; every cycle had hardware hits before and after the flap |
| Kill switch | `MODE=kill` ×5: while off, 0 records, 0 HW hits, ~127–134k kernel packets per window (software forwarding); every 1 re-offloaded. PASS |
| Fragmentation | `testrig-pppoe-frag.py`: v4 DF-clear cold and warm 6000/6000 intact, HW-fragmented (DUT kernel RX ≈0); DF-set → 6000 ICMP frag-needed, host path; IPv6 cold → PTB from the kernel (expected); IPv6 warm 6000/6000 intact via HW. With `ipv6_hw_frag=0`, IPv6 warm → 3000 PTB, nothing HW-fragmented (opt-out works) |
| MTU/MRU | Live `pppoe10` MTU change 1492→1400 applied without a redial; offload continued with no invalid PPPoE length on the wire. PASS |
| Control/unsupported | Discovery/LCP/IPCP/IPv6CP remain visible to the kernel; unsupported PPP protocols are not offloaded. PASS |
| PPPoE-over-VLAN | Second session `pppoe11` over `eth3.10`: dmesg `REPLACE PPPoE flow not offloaded (T-M6-SP4 unsupported) - SW fallback`, 0 HW records, software flowtable (`[OFFLOAD]`) ≈600 Mbit/s, while concurrent `pppoe10` stayed in HW at 9.36 Gbit/s. PASS |
| NAT over PPPoE | Masquerade out `pppoe10`: encap 9.37 Gbit/s with 0 retransmits (`[HW_OFFLOAD]`, dell1 sees 10.99.50.12); decap (`iperf3 -R -P 8 -Z` from the LAN side) 9.38 Gbit/s, 18 HW conntracks/36 records. PASS |
| Fragment-pool exhaustion | Pool bpid 5 (2048×2 KiB). pktgen 1500-byte DF-clear UDP on an offloaded flow at line rate for 60 s: pool held at 0–2 free buffers; dell1 received 93.4% of datagrams as intact fragment pairs (10G egress limit, since 2 fragments need 10.57 Gbit/s); 0 `rx_oversize`/`rx_size_big`; pool returned to 2048 afterwards, port and TCP 9.36 Gbit/s healthy. At 100 kpps: 100% delivered. PASS |
| Mixed-size soak | 30 min: bidirectional TCP over `pppoe10` (decap 3.53, encap 3.56 Gbit/s, `-P 4 -b 1500M` each) plus pktgen IMIX 64:7/570:4/1514:1 at 300 kpps on an offloaded UDP flow (540M packets, 0 errors). Samples showed the pool at 2048, MURAM 52922 and 0 kernel errors throughout; 0 `rx_oversize`/`rx_size_big`/`rx_length_errors` at dell1. After draining: records 0, pool 2048, `pcd-snapshot diff [OK]`, ping 0% loss, TCP 9.36 Gbit/s. PASS |

Driver: `bin/testrig-pppoe-resources.sh` with `MODE=fragpool` (exhaustion) or `MODE=soak` (mixed-size soak). It checks pool, records, MURAM, snapshot, kernel errors, oversize and port health, and prints PASS or FAIL. It was validated with `MODE=fragpool DURATION=10` on `.156`: 93.2% delivered, PASS.

Methods:

- **Pool level:** read the frag pool with `sudo busybox devmem 0x1890614 32` (BMan pool-content register `0x600 + bpid*4`). The value is byte-swapped: `0x00080000` = 2048.
- **UDP load:** iperf3 UDP fails through the DUT on this rig ("unable to read from stream socket"), so generate UDP load with kernel pktgen inside the netns. Set `delay 0` before `ratep`, otherwise it resets the rate.
- **Fragment delivery:** count it exactly with the dell1 X710 `port.rx_size_*`/`rx_oversize` counters.
- **Decap throughput:** with one or two decap streams, dell1's PPPoE sender caps at about 2.8 Gbit/s with heavy retransmits (the DUT is not the limit). Use `-P 8 -Z`.
- **Rig hook:** dell1's `/etc/ppp/ip-up.d/ask2-rig` points table 150 at the newest ppp interface. Restore it to `ppp0` when a second session is brought up.

## Missing vendor features

PPPoE-over-VLAN **hardware** forwarding, relay/pass-through and
PPPoE-through-bridge remain feature gaps even when the software fallback
passes. The earlier Tier-1-only scope must not be mistaken for full parity.

After existing-path acceptance, implement session+VLAN identity and tag
ordering in the shared action compiler, with independent fallback and
reconnect isolation. Relay uses a distinct match/action type
(vendor `REPLACE_PPPOE_HDR 0x45`), not a truncation of the routed key.
Bridge-session forwarding depends on the bridge non-IP safety/multi-class
gates. Discovery and link control never move to hardware.

## Known IPv6 fragment-ID limitation

The shared `v6_identification` counter can reuse IDs under concurrent FMan
tasks. Multiple oversize flows sharing a source/destination can interleave
fragments and corrupt receiver reassembly; the kernel may exhaust receiver
fragment queues for 60 s after a flood. Historical two-flow unthrottled
delivery was only 1.7%, despite a healthy DUT.

Keep the limitation explicit and preserve `ask.ipv6_hw_frag=0` for correct
RFC 8200 PMTUD. Re-test low-rate and concurrent traffic separately, allow
receiver queues to drain, capture IDs and intact datagrams, and never infer
correctness from throughput or a single flow. Any fix/policy change requires
a distinct measured decision, not a silent default inversion.

## Rig and measurement controls

Do not touch the rig until the active 10×10 campaign ends.
Use `bin/testrig-pppoe-vendor.sh up|down|status` for `.106` runtime `pppoe10`;
its OpenWrt forwarding requires both ingress and egress accept rules.
Dell1 concentrator and current ASK2 client setup are recorded in archive §3a;
do not recreate them merely because the old setup work moved.

PPPoE concentrator addresses are `10.99.50.1` / `fd99:50::1`; dell2 is behind
DUT eth4. Verify Dell return routes after link flap/reboot, session IPv4 and
IPv6CP, and dell1 RPS: its NIC cannot RSS-hash PPPoE and otherwise imposes
an approximately 5.9 Gbit/s generator ceiling. Restore temporary routes,
firewall/NAT and steering on exit.

Run `bin/testrig-offload-quick.sh` PPPoE cells plus regression/explicit combo
cells; compare with matched SW controls. Use the current paired method in
`ASK2-VENDOR-PARITY.md`, not old standalone throughput numbers.

Fragment driver: `bin/testrig-pppoe-frag.py ask2|vendor`, 6000 datagrams at
500 pps into 1492 MTU, warm with 1500 fitting datagrams. Flush cached PMTU
before each case, abort if cached MTU remains. A cold IPv6 oversize-first
flow tests kernel PTB, not hardware fragmentation. Check actual intact
datagrams and wire headers; historical vendor zeroed-UDP-header fragments
reassembled but delivered no valid UDP payload.

After each gate: records return to baseline, MURAM/FQ/DDR stable, no kernel
errors, ports responsive and `pcd-snapshot diff` byte-clean. Publish raw
evidence and verdicts in the scoreboard; archive completed run narratives.
