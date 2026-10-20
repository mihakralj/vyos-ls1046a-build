# ASK2 Master Plan

**Version 2.34.0 · 2026-10-19**

This is the single ASK2 task list and execution order. Feature plans own
detailed designs and runbooks; `ASK2-VENDOR-PARITY.md` owns current measurement
scope, method and verdicts. The rewrite plan has been absorbed here.
Completed work, experiments, rejected approaches and dated results live in
[`archive/`](archive/README.md), not in the active backlog.

**Full vendor feature parity is not reached.** Throughput parity for existing
routed/NAT/VLAN paths cannot close IPsec, multicast, tunnels, QoS or the
remaining bridge/encapsulation gaps.

## 1. Current state

### 1.1 Working baseline

Boards and images (2026-10-19):

| Board | Image | Role |
|---|---|---|
| `.185` | `2026.10.19-1504-rolling`, `6.18.57-vyos` (production; `latest.iso`) | Primary rig DUT on the generators' root-namespace ports |
| `.149` | `2026.10.19-1845-rolling`, `6.18.57-vyos` (`kunit=true`, ASK debugfs) | KUnit/debugfs DUT on the generators' `n106` ports; same rig addresses as `.185` (`10.99.1.185`, `10.99.2.185`, …), management on eth2 DHCP |
| `.145` | `2026.10.16-2149-rolling` | Former NXP vendor board (`.106`), now VyOS; no vendor ASK board exists until one is restored |

Dell1 `.112` and dell2 `.113` drive eth3/eth4. Both DUTs carry the same rig
addresses, so run a test against one DUT at a time; `pppoe10` works only on
`.185` (the PPPoE server listens on the root-namespace port).

Routed IPv4/IPv6, NAT44/NAT66/PAT, single-tag IPv4/IPv6 VLAN, PPPoE
encap/decap and MTU-shrink fragmentation exist. Bridge learned/static unicast
and B7a same-tagging have silicon acceptance and a current-image repeat. The
production YNL family serves per-record silicon counters and `get-flow`.

F-266 TCP control punt is implemented but default-off; its transit gate and
the A14 TTL/neighbour gate both FAILED on 2026-10-18 (§4.1). Closed TCP
flows can linger in conntrack. Do not claim scale acceptance.

The Dell-controlled paired 10×10 routed/NAT/VLAN campaign on `0551` is
`COMPLETE_WITH_ISSUES`: 17 of 24 cells PASS under the strict-mean rule. Six
failing cells are one-way deficits of 0.002–0.140%; the largest is VLAN↔port
IPv6 at −0.366% with an interval including zero. VLAN-translate IPv4 3→4 and
bidirectional VLAN↔port IPv6 are the two material deficits. Verdicts and raw
data: `ASK2-VENDOR-PARITY.md`. A same-session A/B (2026-10-19) showed no
image effect between `0551` and `2149`.

Gates closed since the 2026-10-15 snapshot. The evidence is in
`archive/ASK2-MASTER-PLAN-2026-10-19.md`:

| Gate | Closed | Residual caveat |
|---|---|---|
| Per-port disable/re-enable (no flowtable rearm after the last family is deleted) | `1941`/`2012`, 2026-10-16 | — |
| Factory default (`offload ipv4 ipv6` on eth1–eth4, flowtable `FT`) | fresh USB install of `2149` on `.145`, 2026-10-19 | Live (non-installed) boot and 1G forwarding through eth1 untested |
| Offload regression battery, 23 cells (20 base + 3 combo) | `2012`, 2026-10-16 | Smoke, not parity or soak |
| T-PARITY-10 bidirectional delta | Same-session A/B, 2026-10-19: rig/day drift, not image | Vendor comparison needs a restored NXP board |
| Bridge harness verdicts (nonzero exit on any failure) | 2026-10-16 | — |
| Bridge B6/B7a current-image repeat and 100-cycle soak | `2012` cold boot, 2026-10-17 | B2 and capacity need the debugfs image (`.149`) |
| PPPoE ASK2-side fragmentation | `2012`, 2026-10-17 | Vendor half, 10,000-cycle, exhaustion, soak open |
| IPsec Tier 1 (CAAM-backed host-terminated ESP, SW baseline, rekey) | 2026-10-17 | Reboot-clean open |
| T-M8-3 per-flow counters, YNL `get-flow` fix, KUnit all-green | Counters on `.145`/`2149`; `get-flow` fix on `1504`/`.185`; KUnit (`ask_genl` 16/16) on `1845`/`.149`; 2026-10-19 | Per-op genl policies (§4.7) |

### 1.2 Feature-parity blockers

These rows describe missing hardware outcomes, not features the kernel
cannot forward in software. Vendor source support is not a live benchmark.

| Area | Remaining gap | Owner / gate |
|---|---|---|
| IPsec ESP | Full FMan→CAAM→FMan path absent; SA lifecycle, v4/v6 tunnel mode, supported suites, NAT-T, ESN/anti-replay, rekey/expiry, encrypted fragmentation and SEC rate control | T-M6-4 / IP1 / IP2; IPsec plan |
| Multicast | IPv4/IPv6 routed multicast and MDB-owned replication absent | T-M6-MC |
| Bridge | B7b access↔trunk pop/push, IRB, routed+bridged on one port, PPPoE-session bridging; QinQ remains unsupported | T-M6-2; bridge plan |
| Multi-table dispatch | One key profile/table per port prevents simultaneous L2/routed/ESP classes | T-M6-DISPATCH; bridge plan |
| VLAN | QinQ/802.1ad/stacked tags and DSCP→PCP absent; routed PCP≠0 fallback needs board proof | T-M6-VLAN2 |
| PPPoE | PPPoE-over-VLAN hardware path and relay absent; current-image validation, churn and exhaustion still open | T-M6-SP4; PPPoE plan |
| Tunnels | 6in4 and 4in6 absent; other modes require vendor datapath proof before scope expansion | T-M6-SP5 / TN |
| QoS | Rate limit on ASK-forwarded traffic, CEETM shaping/scheduling, DSCP→queue and exception-path rate limit absent; ingress policer bypassed on ASK-engaged ports | T-M6-QOS |
| Parser safety | F-266 transit gate, TTL/hop-limit expiry punts, IKE/NAT-T control separation | T-M6-SP1 / SP2 / SP3 |
| Fragments | Generic fragment-flow acceleration absent; MTU-shrink fragmentation does not implement reassembly | T-M6-FR; software policy gate, not an assumed vendor gap |

IPsec suite breadth is not a promise to enable unsafe algorithms. Preserve
the CAAM A24a GCM refusal until independently resolved. IPsec transport mode,
IP reassembly, NAT46/64 and GRE-over-IPv6 are not established vendor hardware
parity requirements on this board. Wi-Fi, RTP/VoIP/socket 3-tuple acceleration
are excluded. MACVLAN/ICC/packet capture have no vendor cdx handler and are not
parity blockers. Optional coarse-flow work is separate (§4.6).

## 2. Binding architecture

The maintained silicon/API authorities are
`arch/fman-microcode-210-programming-reference.md`, `arch/fman-fe-ehash.md`,
`arch/fman-pcd-api-reference.md`, `specs/fman-keygen-flow-key-spec.md` and
`DUAL-DATAPLANE.md`. Historical register dumps in the archive are not current
programming recipes. Resolve contradictions before touching code.

- Single image; S0 kernel/RSS at boot, S1 ASK per interface and IP family,
  S2 VPP overlay. ASK↔VPP transitions pass through S0 with a per-interface
  mutex. No build-time dataplane selection.
- The only engage leaves are `set interfaces ethernet ethN offload ipv4|ipv6`.
  VLAN/NAT/PPPoE/bridge are automatic parts of an eligible engaged port;
  PPPoE engages its source-interface port. Global kill switches include
  `vlan_offload`, `pppoe_offload`, `nat44_offload`, `nat66_offload`,
  `ipv6_hw_frag` and `bridge_offload`; bridge defaults on. No per-feature CLI.
- Kernel flowtable/tc, XFRM, switchdev FDB/MDB and route/neighbour state own
  objects. ASK is a hardware cache/translation layer, never a second
  CMM/FCI/askd control plane. Debugfs is diagnostic, not production control.
- Ehash is the production flow-scale mechanism. Preserve direct RCCB
  dispatch, per-egress no-confirm TX FQs, owned FE workspace
  (`FmPortSetFESupport`, `contextOffsetInWS=0`) and resource budgets.
  CC-tree is not a proven replacement classifier.
- Keep the shipped routed 50-byte dual-lane GEC key unless measured evidence
  justifies change. The historical EKFC-only 14-byte model is not today's
  routed recipe. Bridge B7a uses its distinct 16-byte L2 key. Raw CRC-64 uses
  reflected polynomial `0xC96C5795D7870F42`, seed `~0ULL`, no final complement.
- Normalize ingest into typed intent and use one action compiler: validate
  ordering, offsets, record size and resources before publishing. Keep owner
  cookie/generation, atomic publication and idempotent delete/flush under the
  shared lifecycle lock. Stale DESTROY cannot delete a newer record.
- Unsupported actions, unresolved neighbours, exhausted resources and failed
  readback return framework-appropriate fallback errors before `in_hw`.
  Never accept a rewrite as a no-op or call fallback successful after hardware
  has already modified/consumed a frame.
- RX is order-0/4 KiB; ASK MTU range is 1280–3600. Change MTU only disengaged,
  match endpoints, restore 1500 on exit/abort. Do not revive order-1 pools.
- MTU-shrink fragmentation retains F-262's owned frag-info and RX triple:
  params misc `|=0x40000000`, RCMNE `0x0e`, RFENE `0x22`, readback and reverse
  restoration. IPv6 HW fragmentation defaults on by operator decision;
  `ask.ipv6_hw_frag=0` is the RFC 8200/software-PMTUD opt-out. Concurrent
  oversize IPv6 flows can reuse fragment IDs; this limitation remains open.
- Per-flow MURAM allocation is zero in the production ehash path. Do not
  revive archived allocators or CC-tree scale-out without a measured need.
- IPsec starts AES-CBC-SHA256; AES-CTR-SHA256 is a later suite. GCM remains
  refused for CAAM A24a. XFRM owns keys/lifetimes; no secrets in diagnostics.

## 3. Execution order

| Priority | Work | Dependency / exit |
|---|---|---|
| 1 | **B7b access↔trunk pop/push** (active; silicon run 1 on `.155` image 2223 FAILED 2026-10-19 — bridge pop/push used the routed VLAN gate and `vlan_offload` toggle re-engaged L2 ports as ROUTED; fixes staged, need a new image) | F-268 bridge emitter flag (no TTL, mtu 0xffff) + ask.ko action/ingress-form enumeration; KUnit-pinned. Next: re-run `bin/testrig-bridge-b7b.sh` on `.155`, B7a/routed/NAT/PPPoE regressions. Runbook: `ASK2-BRIDGE-OFFLOAD-PLAN.md` §6.1 |
| 2 | F-266 FIN/RST transit and NAT correctness; A14 IPv6 TTL and stale-neighbour safety | Both FAILED 2026-10-18; must pass before connection-scale claims or new default-on safety paths |
| 3 | Existing-feature acceptance: PPPoE cycles/exhaustion/soak, bridge B2/capacity (debugfs on `.149`), resource failure, five-port breadth | Use owning runbooks; do not reimplement shipped paths |
| 4 | IPsec Tier 1 reboot-clean, then Tier 2 design/bring-up | Full path required for parity |
| 5 | Multi-table dispatch; PPPoE-over-VLAN; multicast | Dispatch must prove coexistence before IRB/mixed classes/ESP coexistence |
| 6 | QinQ, relay, tunnels, QoS | Feature-specific kernel ownership and forward/inverse/fallback gates |
| Blocked | Refreshed vendor comparisons | Restore a board to NXP ASK first (the former `.106` now runs VyOS as `.145`) |
| Parallel, not on the active rig | Phase 2 code/patch consolidation | No behavior change; preserve regression evidence |
| After correctness gates | Current pps, scale, latency and soak; final per-feature vendor comparisons | No historical figures substituted for current acceptance |
| Separate track | WireGuard, VPP ZC, upstream preparation, genl per-op policies | Not missing vendor hardware features |

## 4. Open work program

### 4.1 Measurement and correctness

- **Vendor comparison:** the strict-mean FAIL cells (§1.1) stay open against
  historical OpenWrt/vendor data. Restore NXP ASK on a board before a
  refreshed comparison. Preserve raw data; no jitter or outlier trimming.
- **T-M6-SP3 / F-266:** establish and offload transit TCP; FIN and RST must
  reach conntrack while established data stays HW; test NAT44/NAT66 mapping
  survival and clean disarm snapshot. Keep `tcp_ctl_punt=0` until all pass.
  The dormant feature is verified to make no PCD writes while off; the live
  arm path and TCP parser attachment were readback-verified. Use real transit
  endpoints on `10.99.1.0/24` and `10.99.2.0/24`; an earlier attempted test
  had no transit traffic, empty ARP and zero flowtable counters, so it did not
  exercise the feature. Do not use a flood for this gate. Keep the parameter
  off unless all checks pass. No software idle reaper: it cannot distinguish
  closed and quiet flows.
  **2026-10-18 cold-boot transit test (FAIL, `.185`, `2012`, `6.18.55-vyos`):**
  after the approved hard power-cycle, root `ask-check` passed 36/36 and both
  routed directions passed. With `tcp_ctl_punt=N`, 300/300 sequential 20-KB
  TCP flows completed; at 125 s all 300 conntrack entries remained
  `ESTABLISHED`, confirming the baseline linger. With `tcp_ctl_punt=Y`, both
  RX ports logged the expected HXS slot-10 soft-parser attach/readback
  (`0x80000000 -> 0x80000420`), and 300/300 flows still delivered data, but
  all 300 entries remained `[HW_OFFLOAD]` with 600 ehash records after close.
  Thus the FIN-close gate did not pass; NAT44/NAT66 mapping tests were not run.
  The parameter was returned to `N`, test conntrack entries were deleted, and
  the final `pcd-snapshot diff` was clean with MURAM `used=52922` at baseline.
  Keep F-266 default-off and the acceptance gate open.
- **A14:** TTL/hop-limit 0/1 must produce kernel ICMP time-exceeded;
  neighbour/route changes must not forward stale; no MURAM/DDR leak. Run the
  TTL/hop-limit boundary cases separately from F-266: test 0 and 1 for routed
  IPv4 and IPv6, verify ICMP time-exceeded reaches the source, and check that
  no stale hardware record remains after route/neighbour replacement.
  **2026-10-18 cold-boot A14 test (FAIL, `.185`, `2012`, `6.18.55-vyos`):**
  from dell1 `.112` to dell2 `.113`, IPv4 TTL 0/1 probes matching a live
  `[HW_OFFLOAD]` TCP tuple returned ICMP type 11/code 0 from `10.99.1.185`.
  Ordinary IPv6 ping hop-limit 1 and an unmatched IPv6 TCP probe returned
  ICMPv6 time-exceeded, but matching TTL 0/1 TCP probes on a live offloaded
  IPv6 tuple returned no ICMPv6 response. During a paced IPv4 flow, replacing
  the `eth4` neighbour for `10.99.2.113` with a test MAC left 2 conntrack
  entries `[HW_OFFLOAD]` and 4 ehash records in place; dell2 RX advanced by
  5754 packets over 3 s, showing the old hardware forwarding continued.
  The real neighbour MAC was restored and ping passed. Test flows were removed;
  `tcp_ctl_punt=N`, MURAM `used=52922`, the ehash table empty, root
  `ask-check` 36/36 and `pcd-snapshot diff` clean at final verification. The
  kernel-path baseline passes, but IPv6 TTL and stale-neighbour safety remain
  open A14 failures.
- **A2 / A13 / scale:** repeat 64-byte loss/rate and IMIX; connection rate
  with held sessions, 8,000-session bidirectional PDR, install rate and first
  insert latency; measure latency beside HW traffic. External 20k CPS and
  8k-session vendor benchmarks are targets, not same-rig evidence.
  Install target ≥2,370/s; first insert <2.6 s, per-flow engage target <100 ms.
- **A6 / exceed-vendor:** investigate any lower ASK2 mean. Separately track
  residual bidirectional retransmits and VLAN↔port MAC RX FIFO loss;
  stretch target ≥16.0 Gbit/s VLAN↔port bidirectional, not the shared cap.
- **A15:** 30-minute mixed-feature soak, zero kernel errors, flat resources.
  This is new-feature/current-image acceptance, not reopening the closed
  Phase 1 RX-stall gate.

### 4.2 Five-port and plain-forward hardening

- **T-M7-2 S2:** emit `PREEMPTIVE_CHECKS_ON_PKT(0x05)` on every plain routed
  record, not only MTU-shrink records, only with owned frag-info and a measured
  regression gate.
- **T-M7-P5:** eth1 routed TCP/UDP HIT/load, then eth0 with serial recovery;
  all-five simultaneous trafficked churn; identical-tuple cross-ingress
  isolation. eth0 is LAST and remains opt-in/high-risk.
- For each port: idempotent CLI and per-port mutex, own RX PCD range and
  distinct TX FQ/channel, correct TTL/L2/checksums/flow owner and HW counters;
  test both directions against supported ports, MTU 1280/1500/2500/3600,
  neighbour change, down/up, remove/reapply, unload and cold boot.
  Per-port/all-five 100-cycle churn must restore S0 PCD/MURAM/FQ baseline.
  1G ports need wire rate without one-core saturation; concurrent 1G traffic
  must not regress eth3↔eth4.
- Port map (RX/TX/channel): eth2 `0x09/0x29/0x803`, eth0
  `0x0c/0x2c/0x806`, eth1 `0x0d/0x2d/0x807`, eth3 `0x10/0x30/0x800`,
  eth4 `0x11/0x31/0x801`. Never engage eth0 with SSH as the only recovery.

### 4.3 Phase 2 consolidation

Verify each candidate against today's tree before deleting/folding; old
fixup ranges are not blanket removal instructions.

**2026-10-18 source audit — STOP on `ask_vlan_cc.c` deletion.** The current
tree still builds this file (`kernel/ask/oot-modules/ask/Kbuild`), routes
VLAN-edit REPLACE events to `ask_vlan_cc_flow_add()` before
`ask_fe_flow_insert()` (`ask_flow_offload.c`), reports its aggregate stats
from generic netlink (`ask_genl.c`), and tears down its CC state from
`ask_hw.c`. It is therefore an active path in this checkout, not dead code.
This conflicts with the later inline-FE VLAN silicon notes and the Phase 2
deletion item. Do not remove or fold it until the code path, deployed image,
and silicon evidence are reconciled; the former `.106` vendor board now runs VyOS (`.145`), so the
vendor-ASK live cross-check is unavailable.

- Physically remove dead `ask_vlan_cc.c` and Kbuild/genl/debugfs/stat proxies,
  commented frees, duplicate `fe_buffer_setup`, legacy `offload_engage` and
  netdev-TX proxy counters that cannot distinguish HW/SW.
- Fold production F_199/F_201/F_222/F_224/F_227/F_242 into owning canonical
  commits; verify round trip before exporting. Never hand-edit generated
  kernel patch hunks or export a fixup-mutated derived tree.
- Audit F_236/F_238/F_239–F_251 individually for diagnostic-only removal or
  one debug Kconfig. Preserve active parser/selftest dependencies.
- Remove the duplicate 100 ms neighbour poller and cookie-pointer spelunking
  only after replacement tests prove route/session/NAT behavior.
- Replace disengage's global `conntrack -F` with port-owned teardown only
  after proving flowtable cannot immediately re-arm and unrelated NAT
  sessions survive. Preserve CR-003 fail-closed commit behavior.
- Add the missing boot key selftest/refusal gate: `-EPROTO` until
  `fman_pcd_key_selftest()` passes; experimental override only
  `fman_pcd.force_unverified=1`.
- CI budgets: ask.ko ≤9.5k LOC excluding tests, kernel PCD ≤15k LOC.
  Counts/patch membership come from the current tree, `series`, manifest.

### 4.4 Deferred classifier work

**T-M6-5** CC-tree scale-out is deferred, not a parity prerequisite. `cc_test`
is retired and no CC-tree HIT is proven; increasing static-key constants
alone does nothing after CR-007. If a bounded coarse-policy requirement
justifies reopening, first build a new silicon classifier oracle and restore
the deleted ingest path behind it. Do not displace ehash.

### 4.5 VPP zero-copy follow-up

Separate from ASK2 vendor parity; owner `VPP-AFXDP-ZC-FULLSPEED.md`.
Preserve task IDs: **T-M4-5a** operator install/cold boot of a current libxdp
image (do not install an old cited ISO blindly); **T-M4-4d** verify DRV mode,
`xsks_map[0]` and `xsk_zc_rx_redirect>0`; **4e** throughput ≥3.0 Gbit/s;
**4f** reversibility; **4g** completion only after steered ZC RX.
Investigate frozen `refill_batches`/pool drain under flood after the path
works. Image installation is never the agent's task.

### 4.6 Feature implementation and acceptance

#### 4.6.1 Kernel authority and fail-closed ownership

Flowtable/tc owns routed/NAT/VLAN objects; XFRM owns SAs; switchdev/bridge
owns FDB/MDB; route/neighbour callbacks own nexthops. Ingest adapters
normalize owner, cookie, generation, typed match/actions, ingress/egress and
resolved TX FQ before allocation. Resource/preflight failure must leave no
cookie, record, MURAM change or `in_hw`. No CMM/FCI/XML runtime control.

#### 4.6.2 Shared action compiler

Use one compiler for TTL/hop-limit, address/port/checksum, VLAN/L2,
enqueue/policer/replicate/CAAM actions. Compute typed offsets, validate
256/320-byte format limits, reserve bounded DDR/MURAM/FQ resources, read back
unreporting writes, then publish atomically. Preserve generations and exact
reverse teardown across every feature.

#### 4.6.3 Remaining capability owners

Feature gaps are enumerated once in §1.2. Detailed bridge, PPPoE and IPsec
runbooks below are subordinate to this plan, not independent task priorities.

#### 4.6.4 Program tasks and gates

- **T-M6-A1:** current MTU/performance/lifecycle battery plus FE record-byte
  identity for canonical-intent lowering. Historical pre-A1 performance is
  not current acceptance.
- **T-M6-A4:** force resource failures before allocation/publication; no
  partial cookie/record, no `in_hw`, flat MURAM and correct SW forwarding.
  **2026-10-17 preflight:** the current production DUT reports `failslab=false`;
  imported `flowtable_failslab.py` is CDX-only and is not ASK2 evidence. Run
  this gate only with ASK2-specific fault injection or a suitable instrumented
  image.
- **T-M6-SP1/SP2:** reusable parser compiler/artifact and typed owned
  load/readback/attach/detach/free API. Capture instructions, entry points,
  lengths and relocations against live vendor readback. Reuse F-266's
  minimal loader where applicable; do not implement it twice.
  Full arena budget 1984 bytes; bounds/refcounts and byte-exact restoration
  required. Vendor `$ccbase+0x30`, `0x4C0000`/`0x500002` and port binding
  must resolve ASK-owned objects, never be copied as runtime literals.
- **T-M6-SP3:** F-266 and TTL gates in §4.1; IKE/NAT-T control packets remain
  visible to the kernel before ESP offload.
- **T-M6-SP4 / A8:** PPPoE vendor comparison, 10,000 session cycles, pool
  exhaustion and soak; then implement the missing over-VLAN and relay
  outcomes. Owner `ASK2-PPPOE-OFFLOAD-PLAN.md`. The 23-cell smoke and the
  ASK2-side fragmentation run passed (§1.1). No 10,000-cycle runner exists
  yet, and no vendor board is available.
- **T-M6-4 / IP1 / IP2 / A9:** build XFRM-owned ESP ehash→OH/SEC→TX with
  AES-CBC-SHA256 first. Tier 1 alone is not full vendor parity. The Tier 1
  substrate and host-terminated tunnel run passed (§1.1): CAAM wins
  encrypt 1.4–1.9× but loses decrypt to ARMv8-CE software, which strengthens
  the Tier 2 case. Remaining Tier 1 gap: reboot-clean.
  Add/delete/update/lifetime/rekey, tunnel v4/v6, inbound/outbound, ESN,
  anti-replay, NAT-T, PMTU/fragments, peer interoperability, bad-auth/replay,
  reboot and disable fallback all gate `NETIF_F_HW_ESP`, advertised LAST.
  Transport mode is optional breadth, not a vendor requirement.
  Owner `ASK2-IPSEC-OFFLOAD-PLAN.md`.
- **T-M6-2 / A7:** B7b access↔trunk pop/push is the active bridge task
  (F-268 + ask.ko code complete 2026-10-19; silicon gate pending);
  retain learned-MAC, filtering, ageing, move/STP/isolation and clean
  teardown. Do not reopen B0–B6 implementation. B2 and capacity repeats need
  the debugfs image, now on `.149`. Owner `ASK2-BRIDGE-OFFLOAD-PLAN.md`.
- **T-M6-DISPATCH:** design/test multi-table per-port classification before
  IRB, routed+bridge coexistence or routed+ESP; protocol precedence, miss
  delivery, owner/generation isolation and reversibility are mandatory.
- **T-M6-MC / A10:** MDB/mroute-owned v4/v6 group objects with bounded
  REPLICATE/egress resources; join/leave, source-specific groups where
  supported, TTL/hop-limit, port down, churn, exhaustion and no duplicates.
  Not a fan-out of unrelated unicast records.
- **T-M6-VLAN2:** QinQ/802.1ad/stacked tag semantics and DSCP→PCP;
  validate PCP≠0 fallback first. Bridge PCP is already in B7a's key and is
  distinct from routed PCP rewriting.
- **T-M6-SP5 / TN / A11:** 6in4 and 4in6 via kernel tunnel netdev/intent;
  prove encap/decap wire bytes, TTL/hop-limit, route/neighbour changes and
  unsupported nesting fallback. No generic vendor command compatibility.
- **T-M6-QOS / A12:** ASK-path rate limit and policer coexistence; CEETM
  shaping/scheduling/weighted/class queues, DSCP→FQ, `QM_FF_RATE` and
  `QM_EXPT_RATE`. Wire-level rate accuracy target ±15%, plus mixed
  offload/SW load, queue lifecycle/exhaustion, no routing regression.
- **T-M6-FR / A11:** validate software handling of first/non-first,
  out-of-order, overlap/tiny fragments, IPv6 extensions, timeout/exhaustion
  and firewall/NAT safety. No vendor reassembly gap is established; hardware
  fragment-flow work requires an explicit supported scope and ownership.
- **T-M6-T3 / MV (conditional, not parity blockers):** only a concrete kernel
  wildcard-flow or MACVLAN requirement can activate these. 3-tuple packing
  exists, dispatch does not; never truncate an extracted key or infer
  wildcard semantics from nf_flowtable. Historical scheme-7 discrepancy and
  proposed wiring are preserved in the 2026-10-15 master snapshot.

#### 4.6.5 Per-feature acceptance contract

No feature is complete until wire capture proves semantics and HW bypass,
kernel ownership/counters/lifetime are coherent, unsupported cases forward
correctly in software, and all of these pass:

1. Create/replace/delete/flush, neighbour/route change, down/up, config
   removal, unload and reboot.
2. Concurrent REPLACE/DESTROY/clear/disengage under DEBUG_LIST/lockdep;
   no stale generation, double free, poison or deadlock.
3. Forced DDR/MURAM/FQ/SA/replication exhaustion; no partial publication and
   snapshot/resource return to baseline.
4. Same-rig vendor comparison, SW control, per-core CPU/errors/MTU and no
   existing-path regression; required kernel control packets stay visible.
5. Firewall/NAT/XFRM/STP/VLAN safety and malformed-packet fallback.
6. `ask-check`, flow/counter/support-bundle visibility without secrets or
   production debugfs control. Advertise capabilities only after these pass.

#### 4.6.6 Global requirements

Keep separate match types and appropriate tables; one key-length constant
per type, checked against live extraction. Reserve before publication,
teardown in reverse, fail closed, read back every unreporting write and
preserve per-port/family isolation. No unbounded resources or speculative
changes to known-good encodings. TCP control, expiry, IKE/control,
unsupported fragments/extensions/actions and unresolved neighbours must not
be silently consumed.

### 4.7 Productization and beyond-vendor work

- **genl per-op policies:** the `get-flow` `-EINVAL` defect (fixed 2026-10-19)
  came from one family policy shared by every command, where
  `ASK_FLOW_ATTR_ID` and `ASK_ATTR_INFO` both use type 1. The structural fix
  is full `genl_ops` with `.policy`/`.maxattr`, ideally generated from
  `ask.yaml` by ynl-gen-c. Counter semantics to preserve: YNL serves a polled
  snapshot (up to ~2.3 s behind silicon), `last-seen-ns` is the poll time
  that saw growth, counters reset when a record is re-created, and
  `max-flows=0` means no fixed limit.
- **Preview:** release notes/package plus an external operator validation
  cycle using `ASK-ISO-BUILD-AND-INSTALL.md`. Do not describe the historical
  M8 release as full feature-parity completion.
- **T-M8-5:** upstream-format series packaging, remaining structural
  reindent preserving required initializers, and reviewed dpaa1→main
  integration of test/policy fixes. Audit current branch state before
  cherry-picking the historical `136fc794`/`fad54f15`/`23bb53ce`/`8813a3a7`
  references. KUnit remains opt-in through `kunit`, never a production default.
- **WireGuard:** current-image eth4 bidirectional RPS A/B, eth3 10 reps,
  then operator decision on shipping steering; neither stack HW-offloads WG.
  Owner `ASK2-WIREGUARD-FASTPATH-PLAN.md` (history in `archive/`).
- **Beyond parity:** sub-100 ms per-flow engage, port-scoped conntrack
  teardown, non-proxy HW observability, ≥16 Gbit/s VLAN↔port bidirectional.
- **Suricata:** design only (`ASK2-SURICATA-BYPASS-PLAN.md`). The native
  VyOS service is AF_PACKET IDS, not inline, so it cannot approve flows.
  With the factory-default offload it sees only each flow's slow-path prefix.
  Before any code, the approval contract (§9 of that plan) must be written:
  explicit verdict → conntrack mark → flowtable match, tuple/NAT identity,
  revocation on reload or alert, and fail-closed behaviour.
- **Memory:** consider DMA pools before increasing bridge caps: historical
  coherent record/context allocations cost ~9 KiB per record due to page
  rounding. Preserve memory/resource accounting and collision-chain gates.

## 5. Open defects and limitations

| Item | Remaining issue / next gate |
|---|---|
| F-266 / conntrack close | Default-off; parser arm/readback proven, transit and NAT closure untested; §4.1 blocks scale acceptance |
| IPv6 fragment-ID reuse | Concurrent oversize flows can corrupt reassembly; keep limitation and `ipv6_hw_frag=0` opt-out visible; PPPoE plan |
| Short non-IP bridge frames | Historical FE hit-path freeze; restrict bridge to v4/v6 until a new cold-boot de-risk gate passes |
| 10G link-down invalidation | Bridge remote-carrier test is limited by the known carrier gap; do not claim it passed |
| F-076 / eth4 debugfs direct arm | Unsupported `hit-engage`/`hit-disengage` can leave RX deaf; production YNL did not reproduce; diagnostic-only investigation, no reopened M3 gate |
| CR-007 | Remove dead Fork-A bookkeeping in consolidation; no requirement to restore CC-tree routing |
| F-122 | Idempotence code exists; confirm repeat engage in current-image lifecycle acceptance |
| VPP ZC refill | Flood can freeze refill and drain pool; separate VPP track |
| A6 residuals | Current paired verdicts/retransmits and VLAN↔port loss investigation, not the closed F-263 RX-stall gate |

Closed CR-003, F-120 and A6-LOSS/Phase 1 are archived. No repeated completed
checklists are required to keep their closure.

## 6. Experiment and gate rules

- Qdrant/register/spec/live-vendor cross-check before FMan/PCD/kernel-fixup
  changes. Cold boot before silicon experiments; record boot type, image SHA,
  kernel, rig and exact conditions. One variable/key/flow/packet class.
- Begin new paths with pings, not floods. Never mutate eth0 experiments;
  management testing requires serial capture and tested rollback.
- Forward and inverse land together. `pcd-snapshot capture/diff` must match
  warm S0 byte-exactly after teardown; MURAM/FQ/DDR return to baseline.
- MURAM is iomem: `memset_io`/`memcpy_toio`/`writel`/`readl`, zero after
  allocation; never write outside an owned allocation. Ehash buckets are DDR.
- Verify key length/layout and every unreporting write before engage or
  publication; preserve compile-time, KUnit and arm-time readback tripwires.
- Keep UAPI/`ask.yaml` and decoding synchronized; `ask-check` is the
  read-only production health contract, not a debug-milestone scoreboard.
- Any `fman_pcd.c`/`dpaa_eth.c` change retains the pass-through regression
  monitor as well as current HW-forwarding gates.
- IPv6 software/pre-HIT tests are capped at ≤100 Mbit/s; use VyOS-configured
  DUT ULAs, not temporary raw addresses lost on commit.
- Operator installs images. Agents publish URL only, never run `add system
  image`/`install image`. Refresh both `latest.iso` and `latest.iso.minisig`;
  operator URL `http://192.168.1.137:8080/iso/latest.iso`.

## 7. Environment and evidence

Current rig: dell1 `.112` → DUT eth3/eth4 → dell2 `.113`;
`bin/testrig-combo-matrix.sh` and `bin/testrig-offload-quick.sh`.
Use `ASK2-VENDOR-PARITY.md` for the current paired method and
`ASK2-PERFORMANCE-TEST-HARNESS.md` for operational detail. Do not mix the
archived 40-second dual-client baseline with the current 30-second
`--bidir` campaign. Old HELGA/heidi/LXC topology is historical only.

## 8. Document ownership and archive

| Document | Owns |
|---|---|
| `ASK2-VENDOR-PARITY.md` | Current comparison scope, evidence and acceptance method |
| `ASK2-BRIDGE-OFFLOAD-PLAN.md` | Remaining bridge design and runbook |
| `ASK2-PPPOE-OFFLOAD-PLAN.md` | Remaining PPPoE acceptance and feature gaps |
| `ASK2-IPSEC-OFFLOAD-PLAN.md` | Tier 1/2 runbook (Tier 1 substrate verified 2026-10-16); must satisfy this plan's current scope/architecture |
| `ASK2-SURICATA-BYPASS-PLAN.md` | Suricata IDS/IPS integration design and approval contract (no code) |
| `archive/ASK2-BRIDGE-OFFLOAD-ARCHITECTURE-ANALYSIS.md` | Completed vendor topology/reference analysis |
| `OFFLOAD-CAPABILITY-PLAN.md` | Vendor vs ASK2 mechanism for each remaining capability |
| `CC-ACL-OFFLOAD-PLAN.md` | Static ACL backend (open Phase 2 drop ACL; not a parity blocker) |
| `DUAL-DATAPLANE.md`, `specs/ask2-vlan-cli-grammar.md` §9 | Runtime/CLI contract |
| `specs/reference/nxp-ask-fmc/` | Vendor source oracle, never runtime config |
| `arch/fman-function-inventory.md` | Function/stub inventory |
| `TA-2026-07-18-002-patch-architecture.md` | Canonical patch process |
| `archive/README.md` | Historical documents and relocation map |

The full pre-cleanup master text of 2026-10-19 is preserved as
`archive/ASK2-MASTER-PLAN-2026-10-19.md`: closed-gate narratives (per-port
disable, factory default, regression battery, T-PARITY-10 A/B, T-M8-3
counters and `get-flow`, bridge/PPPoE/IPsec runs). The exact pre-cleanup
master, bridge, PPPoE, IPsec, parity, WireGuard fast-path,
offload-capability, CC-ACL and dual-dataplane texts of 2026-10-15 are
`archive/<original-stem>-2026-10-15.md`; the completed WireGuard software plan
and matrix moved to `archive/` under their original names; the full rewrite
plan is `archive/ASK2-REWRITE-PLAN.md`. Earlier 2026-10-09 snapshots remain
intact. Register layouts, offsets, commands, dates and rejected hypotheses
remain there verbatim; they have not been silently discarded.

When a gate closes, move its detailed evidence to the archive, remove the
completed task from this plan, update the owner/index and reconcile Qdrant.
Retain only the minimum current baseline needed to interpret unfinished work.
