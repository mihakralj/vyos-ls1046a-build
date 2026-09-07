# ASK2 F-195 Progress and Resolver Diagnosis
**Version 1.1.0** · VyOS LS1046A · 2026-08-15 · HADS 1.0.0

---

## AI READING INSTRUCTION

Read `[SPEC]` and `[BUG]` blocks for authoritative facts.
Read `[NOTE]` blocks for investigation history and rationale.
`[?]` blocks identify an unverified inference that must not be implemented as a behavior change yet.

## 1. Scope

**[SPEC]** This report records the production hardware-flowtable validation after F-195, the remaining target-FQID failure, and the diagnostic required before changing FMan queue-resolution behavior. It applies to board `192.168.1.185`, kernel `6.18.44-vyos`, image `VyOS 2026.08.15-1751-rolling`.

## 2. Completed Work

**[SPEC]** F-195 corrected the OOT caller in `kernel/ask/oot-modules/ask/ask_flow_offload.c`: `fman_pcd_fe_flow_add()` now receives `key->port_id`, not the IPv4/IPv6 table index. The FMan API uses this argument as an ingress hardware-port identifier while selecting ehash table 0 internally.

**[SPEC]** The bounded fixed-source tuple was:

```text
10.99.1.201:51283 -> 10.99.2.106:5201
key = 00|10.99.1.201|10.99.2.106|TCP|51283|5201
hex = 000a6301c90a63026a06c8531451
```

**[SPEC]** The production flowtable test established all of the following:

- F-195 passed actual ingress ports to the FMan layer: eth3 = `0x10`, eth4 = `0x11`.
- Both FE ports engaged and each flow insertion returned `hw_insert OK`.
- F-192 workspace cursors advanced on both ports.
- F-189 ehash records observed packet writeback: `pkt_count=1,8,10,1`; `pkt_bytes=66,824,684,66`.
- No kernel BUG, WARN, Oops, panic, or management-path loss occurred.
- Board `.185` and source container `lxc202` retained `3/3` pings to `10.99.2.106`.

## 3. Active Failure

**[BUG] Flow records receive target FQID zero**

**Symptom:** The bounded iperf3 transfer stalls rather than carrying payload. F-193 logs show `target_fqid=0x0` for both `hw_port=0x10` and `hw_port=0x11`; interface drops rose from eth3 `14 -> 21` and eth4 `0 -> 4` during the test.

**Cause:** `fman_pcd_resolve_miss_fqid()` reads only the FM_CTL per-port params-page field at `FMAN_PP_RX_DEFAULT_FQID_OFF` (`+0x0c`). The page exists, but the field is zero on the live production configuration. The resolver returns that zero directly instead of identifying a valid own-port RX/PCD FQID.

**Fix:** First emit read-only diagnostics for the params-page value and the matching KeyGen scheme's `base_fqid`. Only after live evidence confirms the candidate mapping may the resolver prefer a non-zero authoritative own-port scheme base FQID when the params-page field is zero.

## 4. Confirmed Invariants

**[SPEC]** F-185/F-186/F-190 are coherent and must not be changed for this failure. Their live vendor VARIANT-B nodes have valid 14-byte key configuration, direct ENQUE miss action, and own-port miss targets.

| Ingress | Hardware port | Required own-port FQID |
|---|---:|---:|
| eth3 | `0x10` | `0x200` |
| eth4 | `0x11` | `0x300` |

**[SPEC]** Cross-port FQID enqueue is invalid for this path: E25 demonstrated that a foreign queue can be delivered to the wrong netdev and then dropped because the frame's buffer belongs to the ingress port's BMan pool.

**[SPEC]** The active KeyGen path programs each port's RSS/PCD range through `struct fman_port_rx_params`: `pcd_base_fqid` and `pcd_fqs_count` initialize the matching KeyGen scheme's `base_fqid` and hash range. `fman_pcd_kg.c` also captures `slot->base_fqid` for the F-186 ENQUE miss target.

## 5. Diagnosis

**[NOTE]** Six candidate causes were considered:

1. F-195 still passed a table index rather than a port identifier.
2. The 14-byte PORT_ID|5-tuple serializer or EKFC is wrong.
3. F-185/F-186/F-190 dispatch node encoding is wrong.
4. Ehash insertion/writeback failed.
5. The FE workspace lifecycle is corrupt.
6. The flow-record target-FQID resolver reads an uninitialized params-page default field.

**[SPEC]** Candidates 1–5 are contradicted by the F-195 logs, 14-byte key evidence, live node readback, successful flow inserts, workspace advancement, and F-189 packet writeback. Candidate 6 is the leading diagnosis.

**[SPEC]** The approved fallback source is `keygen->schemes[i].base_fqid`, matched by `hw_port_id`. It is the configured RSS/PCD base and F-186 already uses it for ENQUE miss delivery. F-197 preserves a non-zero params-page value and otherwise accepts only a unique non-zero same-port scheme candidate; conflicting candidates fail closed.

## 6. Diagnostic Plan

**[SPEC]** F-196 will not alter queue selection, descriptors, MURAM, DDR, KeyGen registers, or packet disposition. For every rate-limited resolver invocation it will log:

- requested `hw_port_id`;
- RX-port lookup and params-page MURAM offset;
- params-page default FQID at `+0x0c`;
- each used KeyGen scheme bound to that port, including scheme ID, `base_fqid`, and hash-FQ count;
- the unchanged resolver return value.

**[SPEC]** Acceptance evidence for a subsequent minimal behavior change is:

```text
hw_port=0x10: params_fqid=0x0, scheme_base_fqid=0x200
hw_port=0x11: params_fqid=0x0, scheme_base_fqid=0x300
```

**[SPEC]** F-197 was authorized after this diagnosis. It rejects conflicting non-zero same-port scheme candidates rather than selecting arbitrarily; the F-196 trace remains the deployment-time proof of the live mapping.

## 7. Validation After Correction

**[NOTE]** `bin/dev-build.sh kernel` stages through `bin/ci-stage-kernel.sh`, while F-196/F-197 are Layer-2 fixups injected by `bin/ci-setup-kernel.sh` into the package-build path. The direct dev-loop build completed, but its TFTP kernel did not contain F-196/F-197 and was not deployed to the DUT. The fix itself compiled successfully after applying F-196 then F-197 to an isolated post-fixup derived tree. DUT validation therefore requires an ISO/package build that executes `bin/ci-setup-kernel.sh`; the direct TFTP artifact is not valid evidence for this change.

**[SPEC]** After deploying an F-197-containing image, rerun the fixed-source bounded test and require:

```text
hw_port=0x10 ... target_fqid=0x200
hw_port=0x11 ... target_fqid=0x300
```

**[SPEC]** Verify payload delivery, record writeback, workspace state, ehash statistics, interface drops, and conntrack state. Do not use a concurrent two-port manual `hit-engage` test because the FE singleton/ehash objects are global and a second manual arm fails with `-EEXIST`.

## 8. Deployment Result

**[SPEC]** Image `2026.08.15-1855-rolling` (CI `31902476844`, commit
`e8692203`, kernel `6.18.44-vyos`) contains F-196/F-197. Board `.185` produced
the required proof on every production flow insert:

```text
hw_port=0x10 params_fqid=0x0 scheme_fqid=0x200 target_fqid=0x200
hw_port=0x11 params_fqid=0x0 scheme_fqid=0x300 target_fqid=0x300
```

**[SPEC]** F-197 is correct. Each ingress port resolves to its own PCD base
FQID, unused zero-base schemes are ignored, conflicting non-zero candidates
fail closed, `hw_insert OK` is logged, and conntrack marks the transit flow
`[HW_OFFLOAD]`. Production UDP transit is loss-free through 200 Mbit/s.

## 9. Next Defect: RX-Reinjection Throughput Ceiling

**[BUG] The HIT terminal reinjects to a kernel RX FQ, capping at single-CPU software forwarding**

**Symptom:** A bounded UDP sweep on the F-197 image passed at 10, 50, 100,
and 200 Mbit/s; loss began at 300 Mbit/s (0.19% for 1 s, 2.42% for 3 s). A
500 Mbit/s run lost 39.43% and hard-wedged the FMan until cold power-cycle.
During the 300 Mbit/s run, eth3 `rx dropped [CPU 0]` rose by 77,610 for 77,595
packets and QMan portal 0 handled all traffic; portals 1–3 remained idle.
BMan pool counts remained healthy and no FMan/QMan/BMan congestion or
taildrop error appeared.

**Cause:** F-197 resolves the own-port kernel RX FQID (eth3 `0x200`, eth4
`0x300`), and the FE record enqueues each HIT there. This re-enters the kernel
`NAPI → route → qman_enqueue` software forward path on the single CPU that
services that FQ's portal — the ~1.5 Gbps software-forwarding ceiling from
binding fact 9, concentrated on one core. This is the E25/E26 gate design
(RX enqueue for observability), not a production forwarding terminal.

**Fix:** Build the binding-fact-9 hardware terminal using the exact opcode
bytes verified from `we-are-mono/ASK@fe36f30`: extend F-181's single
`ENQUEUE_PKT(0x01)` to the plain-forward chain
`PREEMPTIVE_CHECKS_ON_PKT(0x05) → STRIP_ALL_VLAN_HDRS(0x12) → UPDATE_TTL(0x21)
→ INSERT_L2_HDR(0x41) → ENQUEUE_PKT(0x01)`. `STRIP_ETH_HDR(0x11)` is not used
for a plain flow; it is conditional on VLAN/PPPoE/tunnel/IPsec operations.
`INSERT_L2_HDR` writes next-hop MAC, egress MAC, and EtherType. Target a
**per-egress-interface** TX FQ on that port's FMan TX DC-portal — the current
P4.1 code's one global FQ hardwired to eth4 channel `0x801` is insufficient for
reverse eth3 egress. Do not RSS-spread the RX FQ. Validate per-portal interrupt
spread, per-port TX-FQ counters, and the bounded rate sweep before any flood
test. This is tracked as T-M7-2 in `plans/ASK2-MASTER-PLAN.md`.

**[NOTE]** The earlier MURAM-leak diagnosis was false. Authoritative
`muram_budget` returns to the intentional F-136 warm-chain baseline of 34,992
bytes after disengage. F-133's diagnostic `muram_allocations` tracker does not
decrement on free and over-reports 52,634 bytes; fix or remove that diagnostic,
but do not change datapath teardown based on it.
