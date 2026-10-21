# ASK2 prioritized task order

Snapshot: 2026-10-21. This is a derived, ordered view of
[`ASK2-MASTER-PLAN.md`](ASK2-MASTER-PLAN.md) (§3 execution order, §4 work
program, §5 defects). The master plan stays authoritative; if the two
disagree, the master plan wins and this file is stale. Counts, run IDs and
image names are state: re-derive them before acting.

## Where things stand

| Item | State |
|---|---|
| Image under test | `2026.10.20-1922-rolling`, `6.18.57-vyos`, commit `03742c87`, deployed to lxc200 `latest.iso` and installed on `.185` |
| F-266 transit gate | PASS on `.185`: 300/300 flows delivered, all conntrack entries CLOSE after the 30 s flowtable timeout, 0 hardware flows, MURAM `used` 52922 |
| A14 TTL/hop-limit | PASS on `.185`: TTL/hlim 1 and 0 return ICMP, TTL 2 forwards as 1, nothing leaves with hlim 0 or 255 |
| A14 stale neighbour | PASS on `.185` with fix `03742c87`: traffic follows the new MAC within the same second |
| PPPoE 10,000-cycle gate (A8) | RUNNING on `.158`, image `1310`, launched 2026-10-20 15:15 UTC, ETA about 2026-10-26; log `dell2:~/testrig/out/pppoe-churn-158-10k-1020-1515.log` |
| Vendor comparison | BLOCKED: no board is running NXP ASK |

Rig constraint: `.158` is occupied by the A8 run until it finishes. Do not
touch `.158`, the dells' `n106` namespace or the dell1 concentrator meanwhile.
`.185` uses the root-namespace generator ports; `.149` is the debugfs/KUnit
DUT. Both DUTs share rig addresses, so test one DUT at a time.

## Ordered list

```mermaid
flowchart TD
    P0[P0 Running: A8 PPPoE 10k on .158] --> P3
    P1[P1 B7b CI-image repeat] --> P2[P2 Bridge B2 and capacity on .149]
    P1 --> P3[P3 A15 mixed-feature soak]
    P3 --> P4[P4 Five-port breadth]
    P3 --> P5[P5 Resource-failure A4 and IPsec reboot-clean]
    P5 --> P6[P6 IPsec Tier 2]
    P4 --> P7[P7 Multi-table dispatch]
    P7 --> P8[P8 PPPoE-over-VLAN, multicast, IRB]
    P6 --> P9[P9 QinQ, tunnels, QoS, fragments]
    P8 --> P9
    P9 --> P10[P10 Scale, latency, final vendor comparison]
```

### P0. Running now (no action, monitor)

- **A8 PPPoE 10,000-cycle lifecycle** on `.158` (`MODE=mix CYCLES=10000`).
  Pass criteria: zero fails and zero deaf ports, hits on every flow before and
  after each flap, every drain returns records to 0 and MURAM `used` to 52922,
  final `pcd-snapshot diff` clean. Stop only with `pgrep` plus `kill <pid>`,
  never `pkill -f`.

### P1. B7b access↔trunk pop/push, CI-image repeat

- Silicon gate and regression smoke passed 2026-10-20 on cold-booted `.155`
  (image `0326`). Repeat on a CI image from a cold boot: IPv4/IPv6 pop/push
  wire/hit/fallback/churn, then B6/B7a zero failures and the routed/NAT/VLAN/
  PPPoE quick matrix 23/23 HW, teardown PCD state exact, MURAM unchanged.
- Runbook: `ASK2-BRIDGE-OFFLOAD-PLAN.md` §6.1.
- Why first: it is the active milestone and the master plan's priority 1.

### P2. Bridge B2 and capacity on the debugfs image

- Run on `.149` (`kunit=true` image, debugfs). B2 and capacity need debugfs,
  which the production image lacks.
- Not-yet-claimable: 10G link-down invalidation (known carrier gap) and short
  non-IP bridge frames (restrict to v4/v6 until a cold-boot de-risk passes).

### P3. A15 30-minute mixed-feature soak

- Cold boot first, one DUT at a time. Mixed routed, NAT, VLAN, PPPoE and bridge
  traffic; zero kernel errors, flat MURAM/FQ/flow counts, `ask-check` 36/36
  before and after, `pcd-snapshot diff` clean.
- Use `.185` (root-namespace ports); do not use `.158` until A8 finishes.
- This is new-image acceptance, not a reopening of the closed Phase 1 RX-stall
  gate.

### P4. Five-port and plain-forward hardening

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
- **IPsec Tier 1 reboot-clean** (last open Tier 1 item).
- Confirm F-122 (repeat engage idempotence) in the current-image lifecycle run.
- Audit deferred-insert paths (`PR14y`/`PR14z10`) that call
  `ask_flow_insert_owned` without an FE insert, flagged in the master plan
  during the A14 work.
- Not yet measured: NAT44 FIN closure (the rig has no IPv4 NAT) and the CPS
  cost of SYN punting to one CPU under `tcp_ctl_punt`.

### P6. IPsec Tier 2 (T-M6-4 / IP1 / IP2 / A9)

- XFRM-owned ESP ehash → OH/SEC → TX, AES-CBC-SHA256 first, AES-CTR-SHA256
  later. Keep the CAAM A24a GCM refusal.
- Gates: add/delete/update/lifetime/rekey, tunnel v4/v6, in/out, ESN,
  anti-replay, NAT-T, PMTU/fragments, interop, bad-auth/replay, reboot,
  disable fallback. `NETIF_F_HW_ESP` is advertised LAST.
- Owner: `ASK2-IPSEC-OFFLOAD-PLAN.md`. Depends on P5 reboot-clean.

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
  **Suricata bypass** (design only, approval contract first).

## Known open defects (do not claim as passing)

| Defect | Note |
|---|---|
| IPv6 fragment-ID reuse | Concurrent oversize flows can corrupt reassembly; keep `ipv6_hw_frag=0` opt-out visible |
| Short non-IP bridge frames | Historical FE hit-path freeze; bridge restricted to v4/v6 |
| 10G link-down invalidation | Bridge remote-carrier test limited by the known carrier gap |
| F-076 eth4 debugfs direct arm | Diagnostic only; can leave RX deaf |
| VPP ZC refill | Flood can freeze refill and drain the pool; separate VPP track |
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
