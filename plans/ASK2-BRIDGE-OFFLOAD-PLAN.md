# ASK2 bridge offload: current status and remaining work

**2026-10-19 · dpaa1 · Status: working path exists; remaining work is scoped and explicit.**

This document is written to be read by an independent reviewer without any
previous context. It separates the bridge functionality that is already present
from the functionality that is still a pending design or validation task.

## 1. Executive summary

The current bridge path is not a blank slate. It already has a real silicon-
accepted foundation for known-unicast, family-filtered bridge forwarding on the
engaged member path.

The remaining work is not a full rewrite. The remaining work is narrower and
more specific:

- B7b tag pop/push and translation semantics
- multi-table dispatch on one ingress port
- IRB / routed+bridged coexistence
- QinQ / 802.1ad handling
- PPPoE bridging edge cases
- multicast / MDB replication

The document below is the current contract. It intentionally does not duplicate
old archive history or re-litigate earlier design dead ends.

## 2. What is already in place

The active bridge path already has a working baseline:

- learned and static known-unicast IPv4/IPv6 bridge forwarding on supported
  engaged bridge members
- B7a same-tagging support with the live 16-byte key shape
  `DA(6)|SA(6)|TCI(2)|ETYPE(2)` and EKFC `0x74000000`
- per-port table isolation and ingress family masking through the
  `offload ipv4|ipv6` trigger model
- bridge FDB authority for reachability, move/age handling, VLAN state and
  local/control-frame policy
- a global `ask.bridge_offload` kill switch and software fallback for
  unsupported roles or protocol combinations

This is the baseline the future bridge work must preserve.

## 3. What is intentionally not yet complete

The open items are all real engineering gaps, not hidden assumptions:

- B7b access↔trunk tag pop/push and translation semantics
- multi-table dispatch with correct precedence, ownership and flush behavior
- IRB / routed+bridged coexistence on the same port
- QinQ / 802.1ad stacked-tag semantics
- PPPoE bridging without short-frame freeze or session confusion
- multicast / BUM replication and MDB ownership

These are not optional polish tasks. They are the controlled expansion gates for
bridge support beyond the accepted baseline.

## 4. Current operating contract

The live contract is:

- bridge learning and static known-unicast remain in hardware when the port is in a
  supported role and the tag semantics are compatible
- unsupported tag/protocol or mixed-role combinations remain in software
- bridges never decrement TTL or fragment; there is no vendor MTU opcode to rely
  on for a blanket transparent-L2 claim
- the engaged-port clamp and original DA/SA semantics must be preserved
- `NF_BR_FORWARD` remains the validity gate after bridge filtering
- installed records must stay consistent with FDB, STP, isolation, VLAN and port
  state

BUM, local/control frames, unsupported tags/protocols and unsupported mixed
roles remain in software. This is the safe contract for the current silicon path.

## 5. Remaining work, by stage

| Stage | Required outcome | Dependency / gate |
|---|---|---|
| Current-image repeat | Learned/static/move/age/visibility plus B7a rows and software controls | No reimplementation of archived acceptance notes; verify on current image with the hardened harnesses (§5.1) |
| B7b | Access↔trunk tag pop/push and translation if required | L2-only emitter, no TTL, preserve B7a and routed/NAT regressions |
| Multi-table dispatch | Multiple class-specific tables on one ingress port | Correct precedence, owner, miss, flush and resource design |
| IRB | Route through bridge using kernel-selected member and nexthop | Multi-table coexistence gate |
| Routed + bridged same port | Both classes in hardware with independent lifetimes | Multi-table gate and class isolation |
| QinQ / 802.1ad | Stacked-tag membership and rewrite semantics | Shared VLAN work; unsupported depths remain software |
| PPPoE bridging | Session frames forwarded without short-frame freeze | Cold-boot non-IP de-risk and session separation first |
| Multicast | MDB-owned replication | Separate multicast gate; not implied by unicast acceptance |

### 5.1 Harness verdicts (fixed 2026-10-16)

Before 2026-10-16 the bridge harnesses printed PASS/FAIL per row but always
exited 0. `chk()` ended in `return 0`, and the final MURAM, `pcd-snapshot`,
kernel-error and record-count checks only printed values. A run could
therefore "complete" with failing rows, and archived results taken from these
scripts are only as good as whoever read the output.

All four scripts now count failures, print `== result: N failure(s)` with an
overall PASS/FAIL, and exit nonzero on any failure:
`bin/testrig-bridge-b6.sh`, `testrig-bridge-b2.sh`,
`testrig-bridge-capacity.sh` and `testrig-bridge-soak.sh`.

The following count as failures:
- every FAIL row;
- the M1 extern_learn/offload takeover check (b6);
- a nonzero `pcd-snapshot diff` exit;
- leftover 16-byte records after teardown;
- kernel lock, WARNING, BUG, DMA or SYNC errors. For b6 this means any such
  error in the current boot, so start from a cold boot. For capacity it means
  the last 15 minutes.

The soak harness also counts its STOP condition. Informational `tag_*` rows in
b2 are still not scored. The current-image repeat must quote each script's exit
status, not just its row table.

**2026-10-17:** `testrig-bridge-b6.sh` on a cold boot of
`2026.10.16-2012-rolling` (6.18.55-vyos) exited 0 with `== result: 0
failure(s)`. All B6 and B7a rows passed, and the M1 takeover check passed.
Offload ran 9.31 Gbit/s at 1.5% DUT busy against 2.92 Gbit/s at 30.2% in
software. MURAM was at baseline, `pcd-snapshot diff` was clean, and there were
0 kernel errors and 0 records left. `testrig-bridge-soak.sh` (defaults: 100
cycles, `JOIN_EVERY=10`) also exited 0 with 0 failures. Records held steady
at 66 learned and 2 base, MURAM stayed at 52922, there were 0 kernel errors
and `pcd-snapshot diff` was clean. Background iperf3 ran at about 9.41 Gbit/s,
dipping to about 7.4 Gbit/s during each eth3 leave/rejoin.
`testrig-bridge-b2.sh` and `testrig-bridge-capacity.sh` still need repeating,
but they require a `CONFIG_NET_ASK_DEBUG_FS` (kunit) image; the production
image has no ASK debugfs.

## 6. B7b and mixed-role gate requirements

The next implementation block is not a free-form extension of the basic bridge
path. It must be done under explicit structural requirements:

- reuse the routed VLAN opcode primitives, not an ad hoc action chain
- keep the `INSERT_L2_HDR` record semantics aligned with DA/SA and the live VLAN
  state
- determine ingress tag and egress tag from bridge VLAN state, not only from a
  post-`br_handle_vlan()` skb view
- keep family-mask, firewall, TTL, routed/NAT and B7a behavior stable

Before implementation, verify the current Qdrant facts, silicon state and live
flow-key spec. Resolve record size, owned stats parameters, tag order and RX
headroom before new feature work is considered accepted.

### 6.1 B7b implementation (2026-10-20, pop/push silicon gate passed)

The pop/push silicon gate and the B6/B7a, routed/NAT and PPPoE regression
smoke checks passed on `.155`. This does not close the separate correctness,
capacity, lifecycle-soak or vendor-parity gates.

**Kernel emitter (fixup `F-268`, `bin/kernel-fixups/F_268.py`).** A new
`FMAN_PCD_VLANF_BRIDGE` (bit 4) flag marks an L2/TX record as a bridge record.
Such a record:

- emits no TTL or hop-limit opcode (`0x21`/`0x29` are skipped);
- writes ENQ mtu `0xffff`, as the vendor `fill_bridge_actions()` does;
- is rejected with `-EINVAL` unless it carries the L2 terminal (DA, SA and
  EtherType), exactly one of POP or PUSH, no NAT flags, no PPPoE flags and no
  `egress_mtu`.

The resulting chains reuse the routed primitives that were silicon-proven on
2026-10-05:

- pop: `04 11 12 41 01`, where STRIP_ALL_VLAN validates the ingress VID;
- push: `04 11 12 42 41 01`, where INSERT_VLAN uses TCI = VID with PCP 0 and
  TPID `0x8100`. Push relies on the RX headroom from patch 0217.

In both chains INSERT_L2_HDR re-inserts the frame's own DA and SA.

**ask.ko.** `ask_bridge_fe_action()` takes an action argument (`SAME`, `POP` or
`PUSH`) and the bridge VID:

- `SAME` produces the B7a record, unchanged byte for byte (KUnit pins this).
- `POP` and `PUSH` move the record to the L2/TX terminal (`tx_fqid`,
  `rx_fqid` 0).
- A pop requires a key tagged with that VID. A push requires an untagged key.
- Only IPv4 and IPv6 EtherTypes are accepted.

`ask_bridge_flow_ok()` derives the action from the record's ingress tag and the
egress port's untagged flag for the VLAN. A pop or push is refused unless the
port's `ask.vlan_offload` is armed. Resync deletes a record whose action has
changed. (Corrected after run 1, below: the gate is
`ask_hw_bridge_vlan_armed_port()`, not the routed
`ask_hw_vlan_offload_armed_port()`.)

The forward hook sees only the egress tag, so admission enumerates the possible
ingress forms:

- A tagged egress frame whose TCI equals the ingress PVID (PCP 0) is installed
  as an untagged ingress record (a push).
- Any other egress frame is installed as-is. If it is untagged on a filtering
  bridge whose egress PVID differs from the ingress PVID, a second, tagged
  ingress form in the egress PVID is also tried (a pop).

The live FDB check in each VLAN rejects wrong guesses. A guess that passes but
is still wrong never hits and ages out.

**Limitation.** Pop keys carry PCP 0, so prioritized tagged frames stay in
software.

**Silicon run 1 (2026-10-19, image `vyos-2026.10.19-2223-rolling`, DUT
`.155` (the former `.106` board), warm boot, `bin/testrig-bridge-b7b.sh`):
FAIL, no pop/push record ever installed.** Software forwarding was correct
(push frames left tagged VLAN 30, pop frames left untagged, TTL and hop limit
64), ARP, PCP 5 and kill-switch fallback rows passed, `pcd-snapshot diff` was
clean and no kernel errors were logged. Three defects were found:

1. `ask_bridge_flow_ok()` gated pop/push on `ask_hw_vlan_offload_armed_port()`,
   which by design refuses L2 ports (`ask_hw_port_l2`). On a bridge member it
   is always false, so every pop/push candidate was dropped silently. Fix: a new
   `ask_hw_bridge_vlan_armed_port()` (same `vlan_offload` switch and family
   mask, but requires the L2_DA role). KUnit pins that a routed-engaged port
   does not pass it.
2. Turning `vlan_offload` 1→0 ran `ask_hw_vlan_disarm_all()`, whose
   `ask_hw_fe_reengage()` disengaged every engaged port and re-engaged it with
   the ROUTED profile, including L2_DA bridge ports. The port kept its L2 role
   bit, so every later 16-byte SAME add failed with
   `bridge: add ... act 0 on port 0x10 failed: -22`. Fix: `ask_hw_fe_reengage()`
   is a no-op for a non-ROUTED port (an L2 port has no VLAN CC tree to
   restore). A `vlan_offload` change now also kicks a bridge resync, so pop/push
   records are removed or re-admitted promptly.
3. Test-harness bug: the frame kind `t306` (tagged, IPv6) was parsed as VID 306
   IPv4, so the pop-v6 rows sent frames the bridge correctly dropped. The kind
   is now `t<vid>v6`.

**Silicon runs 2 and 3 (2026-10-20, image
`vyos-2026.10.20-0326-rolling`, commit `1e845e2d`, kernel `6.18.57-vyos`,
DUT `.155`, operator-confirmed cold boot): PASS after correcting three harness
assertions.** Both runs used the same cold-boot session; no silicon code changed
between them. Cold-boot evidence: the previous boot's journal (`7378564a…`)
ends abruptly at 04:04:07 UTC on routine daemon messages, with no shutdown
sequence, consistent with the operator's cold-boot confirmation. The test boot
(`b6386f2e…`, from 04:05:07 UTC) later ends with an orderly `Journal stopped`, the
warm reboot that restored configuration after the test. Generators were dell1/dell2 in netns `n106` on
`enp1s0f1np1`.

Four 16-byte records installed. Each IPv4/IPv6 push/pop row produced 20 hardware
hits and 20 peer packets, with zero kernel RX packets in the corrected run.
Wire captures showed VLAN 30 on push and no tag on pop, with TTL/hop limit 64.
ARP and PCP 5 stayed in software and were delivered. Turning `vlan_offload` off
removed the records and preserved software forwarding; turning it back on
restored all four records and hardware hits without changing the L2_DA role.

Untagged-flag churn produced SAME forwarding without a tag, then restored tagged
push forwarding. PVID 30→40 invalidated untagged push admission: all 20 packets
reached the kernel, none hit hardware and none reached eth4, which is not a VID
40 member. Restoring PVID 30 restored hardware forwarding. The original harness
incorrectly required zero total records during churn (unchanged tagged ingress
forms may remain, and FDB cleanup can cascade asynchronously) and expected VID
40 delivery. The corrected harness checks forwarding behavior instead and adds
a wire check after tagged egress is restored.

Corrected run: `bin/testrig-bridge-b7b.sh` reported zero failures. Teardown left
zero records, MURAM usage unchanged at 52922 bytes, an exact `pcd-snapshot diff`
match, and zero kernel errors. Log: `b7b-155-0326-cold-run3.log` in the session
artifacts. A normal reboot afterwards restored the test-removed VLAN interfaces
and addresses; `ask-check` reported 36/36 OK.

**Regression run (2026-10-20, same image and commit): PASS.** The operator
confirmed another cold boot. Boot ID
`15c72345-1876-4db1-8956-ad4844543d63` began at 06:39:10 UTC; the previous
journal ended at 06:38:14 on routine PPPoE messages, without a shutdown
sequence. Both regression harnesses ran in that boot, without intervening
reboots.

`bin/testrig-offload-quick.sh`, with `DUR=20`, `STEADY_FROM=5`, `STREAMS=8`
and `RIG_NS=n106`, reported 23/23 HW verdicts: routed IPv4/IPv6, NAT44/NAT66,
VLAN translation and VLAN/port paths in both directions, PPPoE encap/decap
for both families, and the NAT-over-PPPoE/NAT-over-VLAN combinations.
Throughput ranged from 9.144 to 9.325 Gbit/s; hardware-hit ratios were
1.05–1.14. TCP retransmissions ranged from 0 to 6435 per cell. This is an
offload smoke check, not zero-loss, wire-level NAT correctness or
same-session vendor-parity acceptance.

`bin/testrig-bridge-b6.sh`, with `DUT=vyos@192.168.1.155`, `NS=n106`,
`IF=enp1s0f1np1`, `VLAN=1` and `THROUGHPUT=1`, reported zero failures.
Learned/static/moved/aged FDB entries, STP/isolation/family invalidation,
ARP/unknown-DA/broadcast fallback, B7a tagged/PCP records, VLAN-interface
exclusion and filtering all passed. Offloaded hit rows had 20/20 hits and
peer packets with zero kernel RX. The bridge throughput control measured
9.31 Gbit/s with hardware offload (15291939 hits), versus 0.56 Gbit/s in
software (zero hits). Teardown left zero bridge records, 52922 bytes of
MURAM used (unchanged), an exact PCD baseline match and zero kernel errors.

The B6 harness now accepts the DUT/generator/interface/namespace settings
and discovers the generator MACs, retaining the original root-namespace
defaults. Its unlearned VID 30 rows remain software-forwarding checks;
they are not a claim that B7b pop/push is unsupported. The temporary
namespace PPPoE concentrator and its policy rules were removed afterwards.
The restoration warm reboot changed DUT2's DHCP address from `.155` to
`.156` (same SSH Ed25519 host key and eth2 MAC `e8:f6:d7:00:16:a9`).
Boot `4345907c-6317-457a-9659-945e5503abf2` restored eth3/eth4 addresses and
eth3.10/eth4.20; `ask-check` reported 36/36 OK, with 52922 bytes of MURAM used.
Logs and raw JSON are in session artifacts:
`regression-155-0326-{quick.log,quick.csv,b6-b7a.log,cold-kernel.log}` and
`regression-155-0326-json/`; restoration evidence is
`regression-155-0326-restored-156.log`.

Separate F-266/A14 correctness gates, PPPoE churn and vendor parity remain
open in their owning plans. PPPoE exhaustion and soak passed on 2026-10-20 (see
`ASK2-PPPOE-OFFLOAD-PLAN.md`).

### 6.2 DUT2 B2 and capacity (2026-10-20, `.156`, image 0326, debugfs build)

**B2 short-frame check: PASS.** All cases behaved as expected. After restore,
MURAM `used` was 52922 and `pcd-snapshot diff` reported `[OK]`. Log:
`bridge-b2-156.log`.

**B2 row 7 capacity: FAIL**, reproduced on a warm boot and on a cold boot.
`testrig-bridge-capacity.sh` with N=8192 inserted and deleted every record
without error (about 34 s each). MURAM stayed flat, `pcd-snapshot` was clean
and there were 0 kernel errors. However, the probes to DA indices `0` and
`0x1000` got 0 hits and 0 frames at dell2; only `0x1fff` hit 20/20. N=3, 512
and 2048 pass. With N=4096, exactly the indices below 2048 fail.

Root cause, narrowed:

- **Collisions:** the L2 key is 16 bytes (DA+SA+TCI+EtherType). CRC-64 is
  linear over GF(2), so the sequential test DAs put exactly two entries in each
  of 2048 buckets (`fe_ehash_stats` `chainlen 2 buckets 2048`).
- **Which entry hits:** the newer record is inserted at the bucket head and hits.
  The older record behind it in the DDR collision chain misses in hardware.
- **Manual check:** record 0 hit while it was alone in its bucket and stopped
  hitting once a new head linked to it. Its own bytes did not change.

The 2026-10-13 `.185` pass did not exercise chained entries on the probed
indices. It used a different key layout, so this is either specific to the
16-byte key or a regression, with F-267 (per-table `dma_pool` records,
2026-10-16) as the first suspect.

**Routed A/B (same image and boot): the miss is general, not bridge-specific.**
The test used 3000 routed UDP flows from dell2 to dell1 (eth4→eth3, no PPPoE),
all with `[HW_OFFLOAD]` conntrack state: 6000 records with 50-byte keys. After
sending 20 packets per flow, the record `pkt_count` deltas, grouped by table and
bucket, were:

| Record position | Hit | Miss |
|---|---|---|
| Alone in its bucket | 5056 | 0 |
| Head of a two-entry chain | 472 | 0 |
| Behind the head | 0 | 472 |

The head's next link is the `bucket_head_now` value: shift it right by 32 and
byte-swap the u32, and it equals the low 32 bits of the next record's
`record_dma`.

Impact: about 8% of flows at 3000 flows, and more as the table fills, stay in
software despite `[HW_OFFLOAD]` state. Nothing is misforwarded and no errors
are logged. This blocks capacity and parity claims for routed, PPPoE and
bridge alike.

**Root cause (2026-10-20):** microcode 210.10.1 never follows a link from a
regular record. The walker reads byte 0 of the bucket head target: with bit 7
(0x80) set it is a vendor cumulative node (`en_cumulative_entry`), otherwise it
is a single regular record that either matches or misses. ASK2 head-inserts
regular records and writes the old head into the record's bytes 2..7, which the
walker ignores, so every record behind a head is unreachable. The vendor builds
cumulative nodes whenever a bucket holds two or more entries. The vendor
`NO_CUMULATIVE_ENTRY` build is never shipped; see F-254.

Cumulative node layout (vendor header, cross-checked against
`decomp/en-exthash-lookup.asm`):

- 256 bytes, 256-byte aligned
- byte 0: flags. 0x80 = node, 0x40 = invalid, 0x20 = next node present
- byte 1: number of keys n
- byte 2: key stride (`ks` if ks ≤ 2, 4 if ks ≤ 4, else `(ks + 7) & 0x38`)
- byte 3: `13 + n*stride`, the offset of the record pointers
- bytes 4..11: next node address, big-endian
- keys from byte 12, then one zero byte
- n big-endian 8-byte record pointers

That gives 3 keys per node for the 50-byte routed key, and 10 for a 14-byte key.

**Fix: F-269**, copy-on-write bucket rebuild:

- One flow in a bucket: the bucket head points at the record, as today.
- Two or more flows: a fresh chain of cumulative nodes is built from the
  bucket index, the bucket head is switched with one 64-bit store, a SYNC
  (FMFP_EXTC INV0) is issued, and then the old nodes are freed.
- Delete: rebuild without the deleted record, switch the head, SYNC, then free
  the record. If node allocation fails during delete, the bucket head is
  cleared, so the bucket falls back to software until its next rebuild.

The fix is pending CI. The retest is the routed chain A/B (`/tmp/chainab.sh`,
N=3000): heads and records behind them must both hit. After that, rerun bridge
capacity at N=8192.

**Bridge soak: PASS on `.156` (2026-10-20, warm boot after capacity, image
`2026.10.20-0326-rolling`).** `testrig-bridge-soak.sh` ran 100 cycles, 10 of
them FDB join/leave, over 1549 s:

- 0 failures and 0 kernel errors
- MURAM `used` held at 52922 bytes throughout
- 2 records while running and 0 after teardown
- `pcd-snapshot diff` matched the baseline exactly (S0↔S1 reversible)
- background iperf3 held 9.26–9.42 Gbit/s, dipping to about 7.1 Gbit/s only
  in the 10 s windows that contained a join/leave cycle

Log: `files/bridge-soak-156.log`.

## 7. Safety, resource and fallback rules

These are the required gates for the bridge work to remain safe:

- short non-IPv4/IPv6 frames must remain out of hardware until a clean cold-boot
  gate proves the path is safe
- link-down invalidation must be measured and verified before it is considered a
  supported hardware behavior
- keep `ASK_BRIDGE_ENTRY_MAX=512` and `ASK_BRIDGE_REC_MAX=2048` until a deliberate
  budget decision is made
- preserve safe dynamic↔static/local FDB handling under VLAN/PVID churn
- require exact validation of delete/flush/move/age under load before broadening
  the feature window

## 8. Review criteria for completion

A reviewer should require the following before calling the bridge work complete:

1. tagged and untagged forwarding under access and trunk semantics
2. PCP/DEI and VID/PVID correctness, including membership and wrong-tag fallback
3. learn/move/delete/age/static/local behavior, plus STP/isolation/family masks
4. port down/leave/rejoin, kill switch and cold-boot recovery
5. resource exhaustion fallback and clean teardown with zero stale state
6. wire-level throughput and correctness on the same test rig under VLAN mode

Historical throughput numbers are not a replacement for a fresh acceptance run.

## 9. Bottom line

The current bridge path already has a valid operating base. The remaining work is
specific, bounded and gate-driven. The bridge plan should therefore be read as a
status-and-gating document, not as an archive of every old design attempt.

The right standard is simple: keep the working baseline intact, prove each next
bridge feature in isolation, and only broaden scope after the current path is
still correct under load and under teardown.
