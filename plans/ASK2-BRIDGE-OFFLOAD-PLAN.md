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

### 6.1 B7b implementation (2026-10-19, code complete, silicon untested)

The implementation is in place but has not yet been accepted on silicon.

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
changed.

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

**Remaining gates.**

1. Silicon pop/push on `.149`: hit counters, wire captures for the tag and an
   unchanged TTL, and correct ARP/non-IP fallback.
2. Repeat the B7a, routed/NAT and PPPoE regressions.
3. Toggle `vlan_offload` as a kill switch.
4. Exercise PVID and untagged-flag churn, which must resync the records.

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
