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
