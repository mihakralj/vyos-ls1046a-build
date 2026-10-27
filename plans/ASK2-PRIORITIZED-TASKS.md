# ASK2 prioritized task order

Snapshot: 2026-10-23. This is a derived, ordered view of
[`ASK2-MASTER-PLAN.md`](ASK2-MASTER-PLAN.md) (§3 execution order, §4 work
program, §5 defects). The master plan stays authoritative; if the two
disagree, the master plan wins and this file is stale. Counts, run IDs and
image names are state: re-derive them before acting. Completed items (P1 B7b
repeat, P3 A15 soak, F-122, NAT44 FIN closure, SYN-punt CPS, F-266 transit,
A14) are removed; their results live in qdrant and the master plan. Numbering
is kept, so P1 and P3 are intentionally absent.

## Where things stand

| Item | State |
|---|---|
| Image under test | `.185` and `.164` run `2026.10.23-1724-rolling` (`6.18.57-vyos`, commit `62dd22fa`, F-266 fix v1: async HW delete at FIN/RST/SYN). It did NOT fix tuple reuse. Fix v2 (drop reused-tuple SYN until `NF_FLOW_HW_DEAD`) is pushed, CI run `40003181649` building |
| P5 hardening | Open: forced-failure A4, deferred-insert replay test, F-266 5-tuple reuse (see recap); IPsec Tier 1 closed 2026-10-21 |
| PPPoE 10,000-cycle gate (A8) | FAILED on `.164`, image `2044`: stopped at cycle 1116/10000 (2026-10-23 ~16:38 UTC), all four flows (decap/encap v4/v6) delivered no data after a session-ID change; 1115 prior cycles clean, drain 0 records, MURAM 52922, `pcd-snapshot diff` clean, no kernel BUG. Cause not yet diagnosed (DUT offload vs `dell2` client). The earlier lockdep stop at 3482 was fixed in `be246329` but not yet passed |
| Vendor comparison | BLOCKED: no board is running NXP ASK |

Rig constraint: `.158` is occupied by the A8 run until it finishes. Do not
touch `.158`, the dells' `n106` namespace or the dell1 concentrator meanwhile.
`.185` uses the root-namespace generator ports; `.149` is the debugfs/KUnit
DUT. Both DUTs share rig addresses, so test one DUT at a time.

## Status recap (2026-10-23)

**Parity achieved (board-verified):**

- Routed, NAT, VLAN and PPPoE offload.
- Bridge B6, B7a and B7b access↔trunk pop/push.
- IPsec Tier 1 (host-terminated ESP on CAAM, reboot-clean).
- P4 per-port hardening on `.185`: engage/disengage churn, MTU 1280-3600
  (F-270), link flap, neighbour change under load (v4/v6, VLAN) and
  concurrent bidirectional traffic. `fe80::185/64` persistent on eth3/eth4.
- Per-flow counters and the KUnit suite.

**Parity gaps (master plan §1.2):**

- IPsec full FMan→CAAM→FMan path (T-M6-4 / IP1 / IP2)
- Multicast (T-M6-MC)
- Multi-table dispatch (T-M6-DISPATCH)
- PPPoE-over-VLAN and relay (T-M6-SP4)
- QinQ (T-M6-VLAN2)
- Tunnels, 6in4 and 4in6 (T-M6-SP5)
- QoS: rate limit and CEETM (T-M6-QOS)
- Generic fragment acceleration (T-M6-FR)

**Gates and tests (2026-10-23, `.185`, harness `bin/testrig-a14.sh`):**

| Gate | Result |
|---|---|
| A14 TTL/hop-limit expiry punt, v4+v6 | PASS (10/10 ICMP; kernel `icmp_ratelimit` can mask 1) |
| A14 stale-neighbour safety, v4+v6, plain and VLAN | PASS (0 frames to the old MAC after +1 s) |
| F-266 transit, 100 distinct tuples | PASS (0 `HW_OFFLOAD`, 0 ehash, MURAM 52922) |
| F-266 transit, 5-tuple reuse (`REUSE=2 FAM=6`) | FAIL: v6 ~6/8 unforced, v4 rare. Mechanism: old FE record outlives the closing flow, so a same-tuple SYN ~0.3 ms later gets its ACK/data HW-forwarded, conntrack stays `SYN_RECV`. Fix v1 (async delete) failed 7/7 on image 1724; fix v2 (patch `fixes/140`) untested |
| PPPoE 10k churn gate | FAILED at cycle 1116 (see above) |

The F-266 gate cannot be called passed until `REUSE=2 FAM=6 ... f266` passes
repeatedly on the fix-v2 image. Production exposure: needs the same 5-tuple
reused within milliseconds, which randomized client ports make rare.

**Next tasks, in order:**

1. Fix v2: when CI `40003181649` finishes, deploy to lxc200, operator
   installs on `.185`; run `REUSE=2 FAM=6 bin/testrig-a14.sh f266` x N,
   unforced v4/v6, then the `ttl`, `neigh` and `testrig-neigh-change.sh`
   regressions. Expect ~1 s retry delay on a reused tuple.
2. Diagnose the `.164` churn stop at cycle 1116 (kernel log and `ask`
   debugfs at ~16:38 UTC, client side on `dell2`), then rerun the 10,000-cycle
   gate on the fix image. Interim reduced run (~46 s/cycle): `CYCLES=1800`
   (~23 h) via `dell2:~/testrig/launch-churn-158.sh` (default 1800, override
   with `CYCLES=`). It must clear cycle 1116 (the failure point); 1800 does
   not reach the lockdep point at 3482, so the full 10,000 criterion in the
   master plan stays open until agreed otherwise.
3. PPPoE MTU-shrink on `.164` after the gate.
4. T-M7-P5 cabling and T-M6-A4 fault injection (need the operator).
5. Existing-feature acceptance: PPPoE exhaustion/soak, bridge B2/capacity on
   `.149`, five-port breadth.
6. IPsec Tier 2 (P6), then P7-P9 dispatch-dependent features.
7. Vendor comparisons stay blocked until a board is restored to NXP ASK.
8. Session end: `bin/qdrant-reconcile.sh`, update the master plan with the
   F-266 result.

## Ordered list

```mermaid
flowchart TD
    P0[P0 Running: A8 PPPoE 10k on .164 (failed at 1116, rerun)] --> P2[P2 Bridge B2 and capacity on .149]
    P0 --> P4[P4 Five-port breadth]
    P5[P5 Resource-failure A4 and IPsec reboot-clean]
    P5 --> P6[P6 IPsec Tier 2]
    P4 --> P7[P7 Multi-table dispatch]
    P7 --> P8[P8 PPPoE-over-VLAN, multicast, IRB]
    P6 --> P9[P9 QinQ, tunnels, QoS, fragments]
    P8 --> P9
    P9 --> P10[P10 Scale, latency, final vendor comparison]
```

### P0. A8 PPPoE churn gate (stopped, rerun needed)

- **A8 PPPoE 10,000-cycle lifecycle** on `.164` (`MODE=mix CYCLES=10000`); last run stopped at cycle 1116 (see recap).
  Pass criteria: zero fails and zero deaf ports, hits on every flow before and
  after each flap, every drain returns records to 0 and MURAM `used` to 52922,
  final `pcd-snapshot diff` clean. Stop only with `pgrep` plus `kill <pid>`,
  never `pkill -f`.

### P2. Bridge B2 and capacity on the debugfs image

- Run on `.149` (`kunit=true` image, debugfs). B2 and capacity need debugfs,
  which the production image lacks.
- Not-yet-claimable: 10G link-down invalidation (known carrier gap) and short
  non-IP bridge frames (restrict to v4/v6 until a cold-boot de-risk passes).

### P4. Five-port and plain-forward hardening

- **Done 2026-10-21 (`.185`, image 0729, eth3 and eth4):**
  `bin/testrig-port-loop.sh` (idempotent engage ×3, MTU 1280/2500/3600/1500,
  link down/up ×3, 100-cycle disengage/engage churn with a ping running).
  Both ports 0 failures: MURAM `used` 52922 throughout, `pcd-snapshot diff`
  clean, 0 kernel errors, `ask-check` 36/36.
- **Done 2026-10-21 (eth4 only):** CLI remove/reapply ×10
  (`delete`/`set interfaces ethernet eth4 offload ipv4|ipv6`, commit each)
  with a ping through the DUT: MURAM 52922 after every cycle, `pcd-snapshot
  diff` clean, `ask-check` 36/36, 0 kernel errors. eth3 run 2026-10-23 (`.185`, image 2044, `bin/testrig-cli-cycle.sh` ×10, ping running): MURAM 52922 after every cycle, 0 kernel errors, `ask-check` 36/36.
- **Done 2026-10-22 (`.185`, image 1955, eth3→eth4 routed iperf3 4 streams,
  `bin/testrig-neigh-change.sh`):** neighbour flush under load costs one
  ~7.9 Gbit/s second and recovers; a 1.5 s wrong-MAC blackhole then restore
  returns the same flow to 9.4 Gbit/s with hardware hits (+3.3 M pkts/3 s) in
  about 6 s, MURAM 52922, 0 kernel errors, `ask-check` 36/36. A 10 s
  blackhole leaves the TCP flow stalled by RTO backoff, but a fresh flow runs
  at 9.41 Gbit/s, so this is not a hardware fault. eth3 side run 2026-10-23 (image 2044, gen2→gen1, OUTIF=eth3): same result, 2 s dip then 9.41 Gbit/s, hardware hits +3.4 M pkts/3 s, MURAM 52922, 0 kernel errors, 36/36. IPv6 run 2026-10-23 (eth3→eth4, NAT66 masquerade rule present, reverse neighbour pinned): same recovery, 9.28 Gbit/s, hits +3.4 M, MURAM 52922, 0 kernel errors. Finding: with the reverse neighbour unpinned the DUT's ND for gen1 on eth3 stays INCOMPLETE/FAILED when triggered by forwarded IPv6 traffic (no NS seen on the wire), though `ping -6 -I eth3` resolves it; unexplained, needs follow-up.
- **Done 2026-10-23 (`.185`, image 2044, `bin/testrig-port-loop.sh`):** eth3 and eth4 each: idempotent engage, MTU 1280/2500/3600/1500, link down/up ×3, 100-cycle disengage/engage churn with ping: MURAM 52922 throughout, `pcd-snapshot diff` clean (S0↔S1 reversible), 0 kernel errors, 36/36.
- **Done 2026-10-22 (`.185`, image 1955, `bin/testrig-concurrent.sh`):** concurrent
  eth3↔eth4 `--bidir` 4 streams: 8.95 / 9.02 Gbit/s per direction against 9.26
  single-direction (>= 0.97x), hardware hits +7.1 M pkts/3 s, MURAM 52922,
  0 kernel errors, `ask-check` 36/36.
- **Module unload: refused by design 2026-10-22 (`.185`).** `rmmod ask` returns
  "in use" (refcnt 1) with ports engaged, after disengaging both ports, and
  after `nft flush ruleset`: board patch `0145` `dpaa_register_flow_offload_handler()`
  takes `try_module_get(ops->owner)` on registration, so a loaded `ask.ko` pins
  itself and `ask_exit()` is unreachable. No unload-state hazard; disengage left
  MURAM at 35280 and ping worked; ruleset restored and ports re-engaged, 36/36.
  A real unload test would need an explicit handler-unregister path (not planned).
- **Cold boot with offload engaged: PASS 2026-10-22 (`.185`, image 1955).**
  Auto-engage, `ask-check` 36/36, MURAM 52922, both ports ping.
- **FIXED + HW-VERIFIED 2026-10-22 (F-270, ISO 2026.10.22-2044-rolling, .185): `bin/testrig-mtu-steps.sh` 1280/1500/1800/2500 = 36/36 PASS, ~9.2-9.5 Gbit/s, MURAM 52922, no wedge. UDP payload sweep at MTU 2500 (`bin/testrig-udp-sweep.sh`, 1472/1473/1480/1500/1800/2000/2400) 0 loss; 1473 no longer wedges. Churn gate relaunched on the old .158 (now .164). MTU 3000/3600 also pass (9.6 Gbit/s, MURAM 52922). Routed MTU-shrink (eth4 1400, 1472 payload) 0 loss, 0 kernel errors. Not run: PPPoE-specific shrink case.**
- **(was) DEFECT 2026-10-22: MTU > 1500 with offload engaged wedges eth3 RX.**
  With DUT eth3/eth4 and both generators at the same MTU (1800 and 2500
  tested, three cold-boot repeats), pings pass but the first TCP burst freezes
  eth3 `rx_packets` after ~15-20 frames (link up, eth4 fine, down/up and
  disengage do not recover; cold boot only). With eth3 disengaged at MTU 2500
  the same iperf3 runs 8.6 Gbit/s, so the fault is in the engaged FE path.
  Not the 4 KB RX buffer limit (~3712 B). The 2026-10-21 MTU loop passed only
  because the generators stayed at 1500. Next: bisect the threshold between
  1500 and 1800, inspect FE-VM/BMI state on a wedged port, then qdrant-gate any
  fix. `bin/testrig-mtu-steps.sh` reproduces it. Cold boot afterwards.
  Update 2026-10-22: the F-262 RX-port triple (RFENE 0x22, RCMNE 0x0e, params
  misc bit 30) is RULED OUT: reverting it live on port 0x10 changed nothing at
  MTU 2500. It is a regression, because the 2026-08-17 battery passed 1280-2500
  engaged. On a wedged port (MTU 1800, BMI stats on) the mEMAC kept counting
  received frames (~+40k) while BMI RX `rfrc` was 0x20 and the stack saw 80
  packets: the stall is inside FMan after ~32 frames, not at the MAC or in
  BMan. DMA/FPM/QMI/BMI-common blocks show no error flag versus a healthy
  idle dump (only activity counters differ). (The "+40k MAC frames" figure
  was a mislabelled mEMAC register; MAC `rfrm` rose only ~43 frames.)
  RIM/RPSO (patch 0217) also ruled out by live revert.
  ROOT CAUSE 2026-10-22: a UDP size sweep at MTU 2500 passes payload 1472 and
  wedges on payload 1473, so the threshold is exactly IP length > 1500. The
  ehash emitter wrote ENQUEUE mtu 1500 with bpid 0 and no frag-info block on
  plain routed records; with the F-262 RX-port triple the microcode takes its
  fragmentation path with no pool and the port goes deaf. The 08-17 battery
  predates the triple. FIX (verified on board, see above): `F_270.py` writes ENQUEUE
  mtu 0xffff (EN_EHASH_DISABLE_FRAG, as F-261/F-268); F-262 still overwrites
  it where it seals a real MTU check. Verify on a new image with
  `bin/testrig-mtu-steps.sh` at 1280/1500/1800/2500, the payload sweep and
  the F-262 PPPoE MTU-shrink case.

- **T-M7-P5:** eth1 routed TCP/UDP HIT and load, then all-five simultaneous
  trafficked churn, identical-tuple cross-ingress isolation, and eth0 LAST with
  serial recovery in place (opt-in, high risk; never engage eth0 with SSH as
  the only recovery).
- Per port: idempotent CLI and mutex, MTU 1280/1500/2500/3600, neighbour
  change, down/up, remove/reapply, unload, cold boot; 100-cycle churn returns
  to the S0 PCD/MURAM/FQ baseline; concurrent 1G traffic must not regress
  eth3↔eth4.
- **T-M7-2 S2:** `PREEMPTIVE_CHECKS_ON_PKT(0x05)` on every plain routed record,
  only with owned frag-info and a measured regression gate.

### P5. Correctness hardening before new default-on paths

- **T-M6-A4:** forced DDR/MURAM/FQ allocation failures, no partial cookie or
  record, no `in_hw`, flat MURAM. Needs ASK2-specific fault injection; the
  production image reports `failslab=false`.
  First injector (2026-10-27, uncommitted): `ask.fault_cookie_nth=N` fails every Nth
  flow-cookie allocation in `ask_hw_cookie_alloc()` (default 0 = off). Board gate:
  set N, run `testrig-a14.sh f266`, expect failed inserts fall back to software
  with flat MURAM and `pcd-snapshot diff` clean. DDR/MURAM/FQ injectors still open.
- **IPsec Tier 1 reboot-clean: PASS 2026-10-21** (`.185`, image 0729, runtime
  IKEv2 PSK tunnel with dell2, AES-CBC-256/SHA2-256). 20 s × 4 streams with a
  rekey at 8 s: CAAM `rq_dequeued` 809 → 1,030,444, `ib_bytes_decrypted`
  ~997 MB, 0 xfrm leftovers after teardown. After a cold boot: `/proc/crypto`
  CAAM priorities identical to pre-boot (129 lines), 167 self-tests all
  `passed`, `caam-check` healthy, `ask-check` 36/36, MURAM 52922, no xfrm
  state or policy, RNG registered (`rng-caam`, `prng-caam`; Era 8, 3 JRs). A
  tunnel re-established after the boot moved the CAAM counters again (533,699
  dequeued). Tier 1 is now closed; the IPv6, routed-forwarding and SEC QoS
  decode items remain open and do not block Tier 2.
- **Deferred-insert paths (`PR14y`/`PR14z10`): audited and fixed in source,
  commit `465e8ae8`.** They called `ask_flow_insert_owned` without an FE
  insert, so a deferred flow was hardware-backed in the SW table with no
  ehash record. Replay now goes through `ask_neigh_republish()`. Still open:
  a board test that parks a flow on an unresolved neighbour (a neighbour flush
  during flow start still resolved synchronously, so the replay did not run) and
  a KUnit case; both need a `kunit=true` image or a debug hook. Deferred VLAN-edit
  flows are unverified because the republish call passes no egress device.

### P6. IPsec Tier 2 (T-M6-4 / IP1 / IP2 / A9)

- XFRM-owned ESP ehash → OH/SEC → TX, AES-CBC-SHA256 first, AES-CTR-SHA256
  later. Keep the CAAM A24a GCM refusal.
- Gates: add/delete/update/lifetime/rekey, tunnel v4/v6, in/out, ESN,
  anti-replay, NAT-T, PMTU/fragments, interop, bad-auth/replay, reboot,
  disable fallback. `NETIF_F_HW_ESP` is advertised LAST.
- Owner: `ASK2-IPSEC-OFFLOAD-PLAN.md`. Tier 1 reboot-clean is closed (2026-10-21); the next gate is the standalone `0134-caam-qi-share.patch` de-risk.

### P7. Multi-table dispatch (T-M6-DISPATCH)

- Design and test per-port multi-table classification: protocol precedence,
  miss delivery, owner/generation isolation, reversibility.
- Prerequisite for IRB, routed+bridge coexistence and routed+ESP coexistence.

### P8. Features that need dispatch or new tables

- PPPoE-over-VLAN hardware path and relay (T-M6-SP4); first reconcile the
  `ask_vlan_cc.c` STOP in master plan §4.3.
- Multicast (T-M6-MC / A10): MDB/mroute-owned v4/v6 groups with bounded
  replication; join/leave, TTL/hop-limit, port down, churn, exhaustion.
- Bridge IRB, routed+bridged on one port, PPPoE-session bridging (T-M6-2).

### P9. Remaining parity features

- QinQ/802.1ad/stacked tags and DSCP→PCP (T-M6-VLAN2); validate the routed
  PCP≠0 fallback first.
- 6in4 and 4in6 tunnels (T-M6-SP5 / TN / A11).
- QoS (T-M6-QOS / A12): ASK-path rate limit, policer coexistence, CEETM,
  DSCP→queue; wire-level rate accuracy within ±15%.
- Fragments (T-M6-FR): software handling first (first/non-first, overlap, tiny,
  IPv6 extensions, exhaustion); hardware fragment-flow work only with an
  explicit scope.

### P10. After correctness gates: scale and comparison

- A2/A13: 64-byte loss/rate and IMIX, connection rate with held sessions, 8,000
  session bidirectional PDR, install rate (target ≥2,370/s), first insert
  <2.6 s, per-flow engage <100 ms, latency beside HW traffic.
- A6: investigate residual VLAN↔port loss and retransmits; stretch target
  ≥16.0 Gbit/s bidirectional.
- Refreshed vendor comparisons: blocked until a board is restored to NXP ASK.
  Do not substitute historical figures.

## Parallel, not on the active rig

- **Phase 2 consolidation** (no behaviour change; preserve regression
  evidence): remove dead code only after verifying each candidate against
  today's tree; fold F_199/F_201/F_222/F_224/F_227/F_242 into canonical
  commits; add the boot key selftest refusal (`-EPROTO` until
  `fman_pcd_key_selftest()` passes); CI budgets ask.ko ≤9.5k LOC, kernel PCD
  ≤15k LOC.
- **genl per-op policies:** full `genl_ops` with `.policy`/`.maxattr`,
  ideally generated from `ask.yaml` by ynl-gen-c.
- **T-M8-5:** upstream-format series packaging and reviewed dpaa1→main
  integration (`git merge --no-ff --log=200` with a descriptive subject).
- **WireGuard fast path** (`ASK2-WIREGUARD-FASTPATH-PLAN.md`), **VPP zero-copy**
  (`VPP-AFXDP-ZC-FULLSPEED.md`, T-M4-5a through 4g, operator installs images),
  **Suricata flow offload** (`ASK2-SURICATA-OFFLOAD-PLAN.md`; design only,
  approval contract first).

## Known open defects (do not claim as passing)

| Defect | Note |
|---|---|
| IPv6 fragment-ID reuse | Concurrent oversize flows can corrupt reassembly; keep `ipv6_hw_frag=0` opt-out visible |
| Short non-IP bridge frames | Historical FE hit-path freeze; bridge restricted to v4/v6 |
| 10G link-down invalidation | Bridge remote-carrier test limited by the known carrier gap |
| F-076 eth4 debugfs direct arm | Diagnostic only; can leave RX deaf |
| VPP ZC refill | Flood can freeze refill and drain the pool; separate VPP track |
| Deferred-insert replay (PR14y/PR14z10) | Fixed in source (`465e8ae8`), not exercised on silicon or in KUnit |
| F-266 5-tuple reuse (same tuple within ms) | Stale HW record; fix v1 failed, fix v2 (`fixes/140`) pending board test |
| PPPoE churn gate stop at cycle 1116 (2026-10-23) | Undiagnosed; gate not passed |
| Closed flows lingering in conntrack | Entries stay until the flowtable timeout; the F-266 punt fix reduces but the 30 s timeout still applies |

## Standing gate rules for every item

- Qdrant, register, spec and live cross-check before any FMan/PCD or
  kernel-fixup change; qdrant wins on contradiction.
- Cold boot before silicon experiments; record boot type, image SHA, kernel,
  rig and exact conditions. One variable, one key, one flow, one packet class.
- Begin new paths with pings, not floods. Never mutate eth0 experiments
  without serial capture and tested rollback.
- Forward and inverse land together: `pcd-snapshot capture/diff` must match
  warm S0 byte-exactly after teardown, and MURAM/FQ/DDR return to baseline.
- Operator installs images. Agents publish the URL only and never run
  `add system image` or `install image`. Refresh both `latest.iso` and
  `latest.iso.minisig`; operator URL `http://192.168.1.137:8080/iso/latest.iso`.
- No auto-commit or push without an explicit request.
