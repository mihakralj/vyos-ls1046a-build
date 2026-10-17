# ASK2 Master Plan

**Version 2.33.2 · 2026-10-17**

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

ASK2 `.185` runs `2026.10.16-2012-rolling`, kernel `6.18.55-vyos`;
the NXP ASK comparison campaign used `.106` on `6.12.103`.
Dell1 `.112` and dell2 `.113` drive eth3/eth4.
Routed IPv4/IPv6, NAT44/NAT66/PAT, single-tag IPv4/IPv6 VLAN, PPPoE
encap/decap and MTU-shrink fragmentation exist. Bridge learned/static unicast
and B7a same-tagging have dated silicon acceptance; they are not dormant
groundwork. Current-image repeat evidence is separate from implementation.

F-266 TCP control punt is implemented but default-off and has no end-to-end
offloaded-transit acceptance. Closed TCP flows can linger in conntrack.
Do not claim scale acceptance or enable the punt based on register readback.

The Dell-controlled paired 10×10 routed/NAT/VLAN campaign is complete:
480/480 attempts at 2026-10-16 02:54:36 UTC on `0551`, `COMPLETE_WITH_ISSUES`
because one NAT44 run was only partially offloaded. Under the strict mean rule
17 of 24 cells PASS and 7 FAIL. Six of the failing cells are one-way, with
deficits of 0.002–0.140%; the largest is VLAN↔port strip/add IPv6 at −0.366%,
with an interval that includes zero. Verdicts and raw data:
`ASK2-VENDOR-PARITY.md`; campaign directory
`~/testrig/campaigns/parity-10x10-20261015T2130Z/` on dell2.

**Release smoke checkpoint, 2026-10-16:** the operator cold-booted `.185`
on `2026.10.16-0357-rolling`, kernel `6.18.55-vyos`. Boot evidence: boot ID
`2568c1f3`, up since 17:19:56 UTC; the previous boot's journal ends at
17:19:42 without any shutdown sequence, which is consistent with a power cut,
not a warm reboot or kexec. Routed, NAT and VLAN
IPv4/IPv6 cells all forwarded in hardware at 9.152–9.282 Gbit/s.
Policy-routing tests for both families also forwarded in hardware with a
deliberately wrong main-table gateway. F-267 used 512-byte coherent DMA-pool
blocks, eight per page; 64 UDP streams had zero loss. These are smoke results,
not full scale, soak or vendor-parity acceptance.

**The per-port disable/re-enable gate passed on `1941` and `2012`; factory-default live-boot validation remains an outstanding post-publication follow-up (see §4.1).** On `0357`, deleting both
`interfaces ethernet eth3 offload ipv4` and `ipv6` and committing initially
disarmed port `0x10`, but the nft hardware flowtable retained eth3/eth3.10.
New traffic rearmed `0x10` and hardware-forwarded despite the absent CLI
leaves: IPv4 9.326 Gbit/s and IPv6 9.198 Gbit/s. A separate IPv4 reproduction
confirmed the absent leaves and both armed ports after traffic. The journal
shows the pattern three times (17:37:59, 17:42:41 and 17:46:32; the latter
two follow eth3 CLI commits): `0x10` is disengaged, then a flowtable REPLACE
on eth3 re-engages it 2–8 s later without any commit. This violated the
S1→S0 contract; the fix and successful re-tests are recorded below. The original CLI
configuration was restored, both ports were armed, `ask-check` passed 36/36,
and there were no kernel error-level messages at the end.
Qdrant anchor: `2026-10-16 cold-boot 0357 release smoke: per-port disable rearm`.

**Fixed-image gate re-test, 2026-10-16:** `.185` was running
`2026.10.16-1941-rolling` (kernel `6.18.55-vyos`). With both eth3 offload
families deleted, `ask-check` reported only eth4 configured and engaged
(32/32 OK). The eth3→eth4 IPv4 and IPv6 test cells forwarded at 8.891 and
9.133 Gbit/s with 87.1% and 65.6% DUT CPU; aggregate FE-hit ratios were 0.13
and 0.09 (the v4 cell was PARTIAL by the harness threshold, consistent with
some reverse-direction hits on still-engaged eth4). Reverse eth4→eth3 cells
remained hardware-forwarded at 9.268 and 9.138 Gbit/s (ratios 1.01 and 1.02).
No eth3 re-engage appeared during the disabled interval; the single `ENGAGED`
event at 20:47:35 UTC followed the restore commit. After restoring eth3,
forward v4/v6 reached 9.281/9.152 Gbit/s in hardware; the reverse-v6 repeat
reached 9.170 Gbit/s, ratio 1.10. `ask-check` passed 36/36 and the kernel
error-level log was empty after the first enabled battery. The board became
unreachable from the runner after the final repeat, so no final health
snapshot was captured.

CI run `38960509088` succeeded on `38c40eaa` and produced
`2026.10.16-2012-rolling`. The publish job was skipped; the ISO and its
`.minisig` were deployed manually to lxc200 on 2026-10-16 21:01 and both
`latest.iso` symlinks now point to `2012` (sidecar hash verified, HTTP 200).
After a transient lab outage `.185` was still healthy on `1941` (`ask-check`
36/36, no error-level dmesg). The operator subsequently deployed `2012`.

**Installed-image gate, 2026-10-16 21:13–21:27 UTC:** `/proc/cmdline`
confirmed `vyos-union=/boot/2026.10.16-2012-rolling`; boot ID
`e74804be-e9e0-4c13-a66e-7b5e7659974d` remained unchanged through the tests.
The journal starts at 21:07:33 UTC, but the boot method was not established:
this is installed-image acceptance, not a new cold-boot result.
`bin/testrig-offload-quick.sh` ran four routed cells per phase with
`DUR=20 STEADY_FROM=5 STREAMS=8`; these are smoke tests, not parity or soak.

| State | eth3→eth4 IPv4 / IPv6 (Gbit/s) | eth4→eth3 IPv4 / IPv6 (Gbit/s) | Evidence |
|---|---|---|---|
| Both ports enabled | 9.322 / 9.198 | 9.324 / 9.196 | All HW; aggregate FE-hit ratios 1.06–1.11; DUT CPU 0.2–0.3% |
| eth3 offload families deleted | 9.292 / 8.638 | 9.313 / 9.186 | eth3 ingress software, CPU 54.4% / 83.9%, aggregate ratios 0.07 / 0.12; eth4 ingress HW, ratios 1.00 / 1.02 |
| eth3 families restored | 9.323 / 9.278 | 9.321 / 9.193 | All HW; aggregate ratios 1.08–1.10; DUT CPU 0.2–0.3% |

The live `fe_arm` readback showed exactly `Armed ports: 0x11` both before
and after disabled traffic. No eth3 `ENGAGED` event occurred during
21:20:56–21:23:23 UTC; its only new engagement was the restoration commit at
21:23:24 UTC. The disabled IPv6 cell's aggregate `PARTIAL` verdict does not
indicate eth3 rearm: eth4 remained engaged and aggregate counters also include
reverse traffic. `ask-check` passed 32/32 with eth3 disabled and 36/36 after
restoration. Final live ports were `0x10 0x11`, kernel error-level output
was empty, and saved and running configuration hashes matched their
pre-test values. Management eth0 was untouched.

The shipped `caam-check` matched the source and passed: 129 CAAM-backed
algorithms were registered, the checked IPsec transforms selected CAAM,
and their reported self-tests passed. This does not establish full ESP
dataplane offload. The shipped `/opt/vyatta/etc/config.boot.default` matched
`board/vyos-config/config.boot.dhcp` byte-for-byte (SHA-256
`625b97561979eb9594518f026f45e585b459fa11cf367a5b240acd514b168ba9`).
The active config still used the preserved lab flowtable `ASK1`, not factory
`FT`; factory-default live-boot validation therefore remains untested.

**Offload regression battery, same boot, 2026-10-16:**
`bin/testrig-offload-quick.sh` covered all 20 base cells plus the 3 combo
cells (`DUR=20 STEADY_FROM=5 STREAMS=8`, unidirectional). The base cells
were unicast, NAT, VLAN↔VLAN, VLAN↔port and PPPoE down/up, in IPv4 and
IPv6 and in both directions. The combo cells were `nat-pppoe-up-v4`,
`nat-vlan-v4` and `nat-vlan-v6`. **All 23 cells were HW**, at 9.14–9.40
Gbit/s with FE-hit ratios 1.05–1.13 and DUT busy 0.2–0.5%. Afterwards
`ask-check` passed 36/36, ports `0x10 0x11` were armed, and kernel
error-level output was empty. This covers the forwarding paths that the
`ae98749c` rearm fix could affect. It is a functional and throughput smoke
test, not the strict-mean parity campaign or a soak.

**Factory-default static validation, same boot:** the shipped
`/opt/vyatta/etc/config.boot.default` has no version footer, so a live boot
runs every migration. A copy migrated cleanly as root with
`/usr/libexec/vyos/run-config-migration.py` (rc 0). The migrated file kept
the eth1–eth4 `offload ipv4 ipv6` leaves, flowtable `FT` with `offload
hardware` and `hardware-offload-delay 500`, and the IPv4/IPv6 rule 10
targeting `FT`; eth0 had no offload families. The migrated file then loaded
without error into a `vyattacfg` configure session. `compare` showed the
expected `ASK1`→`FT` and eth1/eth2 differences, and the session was
discarded. The saved config hash and live armed ports (`0x10 0x11`) were
unchanged.

One trap: loading the unmigrated file from a non-root vbash session fails in
`interfaces/20-to-21` with a JSON decode error, because `ethtool` lives in
`/usr/sbin` and is not on that user's PATH. This is a test-harness artifact;
boot migration runs as root. It does not replace the live-boot gate, which
still has to prove the boot commit, engagement, and the first offload engage
on the 1G RJ45 ports eth1/eth2. No eth1/eth2 engage evidence exists in this
plan or in qdrant.

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
| 1 | Fresh/live-boot validation of the published `2012` factory default | Publication is complete; installed-image checks do not exercise first-boot config application; preserve the lab install |
| 1a | Investigate the two persistent strict-mean FAIL cells and the bidirectional rerun delta | Same-session `0551`/`2149` A/B for the bidirectional delta; repeat vendor comparisons only after `.106` is restored to NXP ASK |
| 2 | F-266 FIN/RST transit and NAT correctness; TTL safety | Must pass before connection-scale claims or new default-on safety paths |
| 3 | Close existing-feature acceptance: PPPoE, bridge current-image repeat, resource failure, five-port breadth | Use owning runbooks; do not reimplement shipped paths |
| 4 | IPsec Tier 1 tunnel gate and SW baseline, then Tier 2 design/bring-up | Tier 1 substrate verified 2026-10-16 (CAAM built in, wins priority, self-tests pass, snoop set); full path remains required for full parity |
| 5 | B7b and multi-table dispatch; PPPoE-over-VLAN; multicast | B7b can proceed independently; shared dispatch must prove coexistence before IRB/mixed classes/ESP coexistence |
| 6 | QinQ, relay, tunnels, QoS | Feature-specific kernel ownership and forward/inverse/fallback gates |
| Parallel, not on the active rig | Phase 2 code/patch consolidation | No behavior change; preserve regression evidence |
| After correctness gates | Current pps, scale, latency and soak; final per-feature vendor comparisons | No historical figures substituted for current acceptance |
| Separate track | WireGuard improvement, VPP ZC, upstream preparation | Not missing vendor hardware features |

## 4. Open work program

### 4.1 Measurement and correctness

- **Release gate / per-port disable:** prevent new flowtable callbacks from
  rearming a port after its last offload family is deleted. Root cause:
  `ask_hw_port_bind()` (flowtable REPLACE) called `ask_hw_offload_engage()`,
  whose compatibility default turned a zero family mask back into V4+V6;
  disengage also cleared the mask only after dropping `h->lock`. Fix committed
  2026-10-16 in `ask_hw.c`: the flow path engages only when the mask is
  non-zero (checked under `h->lock`, `-ENODEV` = quiet SW fallback), and
  disengage clears the mask under `h->lock` before teardown. Built by CI run
  `38956672515` (head `087a40e5`) as `2026.10.16-1941-rolling`. The gate
  passed on `.185` on `1941` and `2012` on 2026-10-16: deleting both eth3 leaves left eth3
  unconfigured/disengaged while eth4 stayed engaged; generated eth3-ingress
  traffic was software-forwarded, reverse eth4-ingress traffic stayed in
  hardware, and no flowtable REPLACE re-engaged eth3 before the restore
  commit. Re-enable restored hardware forwarding in both families.
  `ask-check` was 32/32 while eth3 was disabled and 36/36 after restoration;
  no kernel error-level messages were present after the first `1941` enabled
  battery or at the final `2012` check. The `2012` gate also verified the live
  `fe_arm` port set and unchanged saved/running config hashes. Detailed rates,
  boot scope and the earlier `1941` reachability caveat are in §1.1.
- **Factory default (commit `c84499e5`, included in `2012` but not `1941`):** the ISO's
  `config.boot.default` now enables `offload ipv4 ipv6` on eth1–eth4 (not
  eth0, the management port), a hardware flowtable `FT` over eth1–eth4 with
  `hardware-offload-delay 500`, and IPv4/IPv6 forward rule 10 → `offload`.
  This applies only to fresh installs and live boots; `add system image` keeps
  the existing config. The `2012` factory default still needs live-boot
  validation. The initial `2012` CI run's publish job was skipped; the image
  was later published as `2026.10.16-2149-rolling` after the `dpaa1`→`main`
  merge. On 2026-10-16 21:01 UTC,
  the ISO and sidecar were deployed to lxc200, both `latest.iso` symlinks
  were verified against `2012` (matching sidecar hash, HTTP 200). The operator
  then upgraded `.185` to `2012`; `ask-check` passed 36/36 and the shipped
  default file matched the source, but the running config was preserved.
  A separate fresh/live boot must apply the factory config without operator
  commits; the installed-image checks do not close that gate.
- **T-PARITY-10:** DONE on `0551` (480/480 attempts, 17/24 PASS) and rerun
  ASK2-only on the `2149` code (240/240 hardware attempts). The `0551`
  partial NAT44 run did not recur. Six strict-mean FAIL cells remain against
  historical OpenWrt; VLAN-translate IPv4 3→4 and bidirectional VLAN↔port
  IPv6 are the two material deficits in both campaigns. All eight
  bidirectional means were lower on the unpaired `2149` rerun (0.03–0.34%);
  cause is unresolved. Same-session `0551`/`2149` A/B is needed to distinguish
  image effects from rig/day drift. `.106` now runs OPNsense; restore NXP ASK
  before any refreshed vendor comparison. Preserve raw data; no jitter or
  outlier trimming.
- **Harness verdicts:** the bridge harnesses (`bin/testrig-bridge-*.sh`) now
  exit nonzero on any FAIL row, `pcd-snapshot` diff, leftover record or kernel
  error (`ASK2-BRIDGE-OFFLOAD-PLAN.md` §5.1). Earlier bridge results came from
  scripts that always exited 0; the current-image repeat must quote exit
  status.
- **T-M6-SP3 / F-266:** establish and offload transit TCP; FIN and RST must
  reach conntrack while established data stays HW; test NAT44/NAT66 mapping
  survival and clean disarm snapshot. Keep `tcp_ctl_punt=0` until all pass.
  The dormant feature is verified to make no PCD writes while off; the live
  arm path and TCP parser attachment were readback-verified, but no
  end-to-end transit result exists. Next test on the installed `2012` image:
  cold boot, verify routed test hosts on eth3/eth4, capture an offloaded
  short-TCP baseline with the parameter off, then enable the parameter and
  repeat the 300-connection/20-KB close test. Confirm FIN/RST-driven conntrack
  closure while bulk data remains hardware-forwarded; repeat through NAT44
  and NAT66, then disable the parameter and require a clean PCD snapshot.
  Use real transit endpoints on `10.99.1.0/24` and `10.99.2.0/24`; an earlier
  attempted test had no transit traffic, empty ARP and zero flowtable counters,
  so it did not exercise the feature. Do not use a flood for this gate. Keep
  the parameter off unless all checks pass. No software idle reaper: it cannot
  distinguish closed and quiet flows.
- **A14:** TTL/hop-limit 0/1 must produce kernel ICMP time-exceeded;
  neighbour/route changes must not forward stale; no MURAM/DDR leak. Run the
  TTL/hop-limit boundary cases separately from F-266: test 0 and 1 for routed
  IPv4 and IPv6, verify ICMP time-exceeded reaches the source, and check that
  no stale hardware record remains after route/neighbour replacement.
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
- **T-M6-SP1/SP2:** reusable parser compiler/artifact and typed owned
  load/readback/attach/detach/free API. Capture instructions, entry points,
  lengths and relocations against live vendor readback. Reuse F-266's
  minimal loader where applicable; do not implement it twice.
  Full arena budget 1984 bytes; bounds/refcounts and byte-exact restoration
  required. Vendor `$ccbase+0x30`, `0x4C0000`/`0x500002` and port binding
  must resolve ASK-owned objects, never be copied as runtime literals.
- **T-M6-SP3:** F-266 and TTL gates in §4.1; IKE/NAT-T control packets remain
  visible to the kernel before ESP offload.
- **T-M6-SP4 / A8:** PPPoE current-image comparison, fragmentation, fallback,
  10,000 session cycles, pool exhaustion and soak; then implement missing
  over-VLAN and relay outcomes. Owner `ASK2-PPPOE-OFFLOAD-PLAN.md`.
- **T-M6-4 / IP1 / IP2 / A9:** audit the IPsec draft, verify Tier 1 Crypto
  API/CAAM, measure SW baseline, then build XFRM-owned ESP ehash→OH/SEC→TX
  with AES-CBC-SHA256 first. Tier 1 alone is not full vendor parity.
  2026-10-16 audit: the draft's "CAAM not built" claim was wrong.
  `ci-setup-kernel.sh` forces it to `=y`; the bogus `CAAM_QI` symbol was
  replaced by explicit `CRYPTO_API` and `CRYPTO_API_QI` pins. On the running
  image, `-caam` (priority 3000) wins over `-caam-qi` (2000) and the software
  drivers for all common ESP transforms, with self-tests passed and the SEC
  snoop bits set. `caam-check` §5b now reports and gates this. Remaining
  Tier 1 work: the strongSwan tunnel gate with debugfs counter proof and the
  software-only baseline.
  Add/delete/update/lifetime/rekey, tunnel v4/v6, inbound/outbound, ESN,
  anti-replay, NAT-T, PMTU/fragments, peer interoperability, bad-auth/replay,
  reboot and disable fallback all gate `NETIF_F_HW_ESP`, advertised LAST.
  Transport mode is optional breadth, not a vendor requirement.
  Owner `ASK2-IPSEC-OFFLOAD-PLAN.md`.
- **T-M6-2 / A7:** bridge B7b and current-image repeats; retain learned-MAC,
  filtering, ageing, move/STP/isolation and clean teardown. Do not reopen
  B0–B6 implementation. Owner `ASK2-BRIDGE-OFFLOAD-PLAN.md`.
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

- **T-M8-3:** reconcile old "no per-flow counter board evidence" with current
  per-record HW proof; validate production YNL `packets`, `bytes`,
  `last-seen-ns` against silicon, not debugfs alone. `max-flows=0` denotes
  no fixed limit, not unlimited resources.
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

The exact pre-cleanup master, bridge, PPPoE, IPsec, parity, WireGuard
fast-path, offload-capability, CC-ACL and dual-dataplane texts are preserved as
`archive/<original-stem>-2026-10-15.md`; the completed WireGuard software plan
and matrix moved to `archive/` under their original names; the full rewrite plan is now
`archive/ASK2-REWRITE-PLAN.md`. Earlier 2026-10-09 snapshots remain intact.
Old A6/Phase 0.4 reports are archived under their original filenames.
Register layouts, offsets, commands, dates and rejected hypotheses remain
there verbatim; they have not been silently discarded.

When a gate closes, move its detailed evidence to the archive, remove the
completed task from this plan, update the owner/index and reconcile Qdrant.
Retain only the minimum current baseline needed to interpret unfinished work.
