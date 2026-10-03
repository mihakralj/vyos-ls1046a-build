# Production VLAN HW-offload throughput ceiling — root cause and fix

**2026-09-10 · Branch `vlan-offload-rework` (forked from `main` @ `7503dda7`,
image `2026.09.07-0402-rolling` — the exact build the ASK2/ASK1 comparison
benchmark used). This is the production CC-tree → HMTD VLAN path, unrelated
to the retired inline FE-VM opcode research on `p0-fevm-vlan-leak`.**

## 1. The symptom

ASK2/ASK1 comparison benchmark (`mono-v25.12.5-r1788817801` reference vs
ASK2 `2026.09.07-0402-rolling`, both on identical LS1046A silicon):

| Combo | ASK2 bidir | ASK1 bidir | ASK2/ASK1 |
|---|---|---|---|
| port→port v4 | 15.50 Gbps | 16.63 Gbps | 93.2% |
| vlan→vlan v4 | 5.27 Gbps | 16.50 Gbps | 31.9% |
| vlan→vlan v4 + NAT44 | 5.27 Gbps | — | — |

Plain routing loses almost nothing to the vendor reference. VLAN loses
two-thirds of it, flat regardless of unidirectional vs bidirectional
(bidir not scaling up from unidir is the signature of a serialized
bottleneck, not a bandwidth limit).

## 2. Live reproduction (this session)

Testers: `.116` (OpenWrt reference board, modest embedded CPU — capped
around 3 Gbps regardless of destination, generator-bound) and `.135` (32-core
AMD Ryzen AI Max+ 395, RTL8126 5GbE NIC — capable of exposing real
concurrency effects `.116` couldn't reach).

Same-generator, same-parameters (`iperf3 -P 8 --bidir -t 12`) A/B comparison
from `.135` through the DUT (image `2026.09.07-0402-rolling`, clean, no
P0-branch contamination):

| Path | Aggregate throughput | DUT CPU (avg softirq) |
|---|---|---|
| Routed + NAT (untagged, masqueraded) | ~4.02 Gbps | **0%** |
| VLAN↔VLAN (VID10↔VID20 translate) | ~3.57 Gbps (lower) | **65.5%** (two cores ~80%) |

Routed+NAT is essentially free — genuine hardware offload, CPU untouched.
VLAN burns 65% average CPU to deliver *less* throughput. At the throughput
`.116` could generate (~3 Gbps peak, no real concurrent flow churn), this
gap was invisible — both paths measured statistically identical. It only
appears once enough concurrent flow setup/teardown happens per second to
hit the mechanism below.

## 3. The mechanism (`ask_vlan_cc.c`)

Confirmed directly in `dmesg` during the `.135` test — repeating for every
flow, under load:

```
ask: vlan_cc: port 0x11 rebuild N remaining keys before HMTD put 0x57600
fman_port: FMFP_EXTC SYNC cleared after 0 poll(s)
fman_port: RX coarse-classification base set to MURAM off 0x...
ASK2-DBG scheme4 hashing / EKFC write ...
fman_port: KG direct-scheme addressing set, scheme 4
```

Every VLAN flow **add or delete** that changes the per-port CC-tree
(`ask_vlan_cc_flow_add()` / `ask_vlan_cc_flow_del()`) triggers
`ask_vlan_cc_rebuild_locked()` — a full re-install of the CC-tree with
**every** remaining key (`fman_cc_tree_install()`), including real hardware
register writes (KeyGen scheme reprogram, RX classification base). On
**delete**, when other flows remain, this was followed by an **unconditional
5-6ms `usleep_range()`** — and critically, the single **global**
`ask_vlan_cc_lock` (one mutex for all 64 ports, not per-port) was held
across the *entire* rebuild + sleep + `fman_hm_vlan_route_put()` sequence.

That means every VLAN flow teardown blocked **every other VLAN flow
add/delete on every port** for the full ~5-6ms+ window. Under concurrent
multi-flow load (8 parallel streams, TCP connection churn), this
serialization is the ceiling — not silicon bandwidth.

This connects directly to an **already-documented finding from
2026-08-31** (comment already in the code, `ask_vlan_cc.c:217-226`): the
exact same 8-stream bidirectional iperf3 test pattern was found, in a prior
session, to churn the CC-tree's key table (`FMAN_CC_MAX_STATIC_KEYS`, 32 at
the time, since bumped to 64) from 0→31→0 within one second, with 61 of 65
install attempts failing `-ENOSPC` and falling back to **software
forwarding** — "never exceeded software throughput." That session's fix
(dedup re-installs, never grow `nkeys` for an already-tracked flow) reduced
churn but didn't address the lock-across-drain serialization measured
tonight, which is the more fundamental structural cause.

## 4. Why routed/NAT is unaffected

Routed/NAT uses the completely separate ehash/FE-VM path (a hash table
keyed by flow tuple, upserts naturally, no full-table rebuild or
lock-held-drain pattern on teardown). It shares no code path with
`ask_vlan_cc.c` at all.

## 5. The fix (this commit)

`ask_vlan_cc_flow_del()`: release `ask_vlan_cc_lock` **immediately after**
the CC-tree rebuild/destroy call returns, instead of holding it across the
drain sleep and `fman_hm_vlan_route_put()`. Safety invariant preserved
exactly — the CC tree is still fully rebuilt/destroyed, and the drain still
fully elapses, before the HMTD is freed (same ordering, unchanged). Only
the *lock*'s scope shrinks: by the time the rebuild call returns, the
shared `port->keys[]`/`nkeys` bookkeeping is already consistent and done
being mutated for this call, so nothing after that point needs the lock —
the remaining work (sleep, then free) operates only on the local
`hm_handle`.

This is not a new pattern in this file: the idempotent-refresh path in
`ask_vlan_cc_flow_add()` already calls `fman_hm_vlan_route_put()` after
unlocking (line ~236-238) — `fman_hm_vlan_route_put()` has its own
independent locking. This fix extends that same established pattern to the
one call site that hadn't adopted it.

**Explicitly not changed in this pass** (deferred, higher-risk, needs
separate verification):
- The global-vs-per-port lock scope (`ask_vlan_cc_lock` still covers all 64
  ports). This fix already removes the worst of the contention (the drain
  wait) from under the lock; converting to per-port locking is a
  reasonable follow-up if contention remains measurable after this fix
  lands, but is a separate, independently-testable change.
- Whether `fman_cc_tree_install()` could do an incremental single-leaf
  update instead of a full-table rebuild on every add/delete — this would
  need to be verified against what the hardware/CC-tree design actually
  supports before attempting; not assumed safe here.

## 6. Verification status

**Not yet tested on hardware.** This is a locking-discipline change in code
whose own comments document a real, previously-observed hardware-fault
risk (a cold-boot-only FMan wedge from a past `delete vif`-under-live-
traffic incident) if the free-before-drain ordering is violated. The fix
here does not change that ordering — only the lock's scope — but it must
be verified on real hardware before being trusted, given the stakes of
getting this specific invariant wrong.

Next steps: CI build, then a *careful*, incremental board test (start with
a single flow add/delete under the existing safety ground rules — cold
boot before the experiment, one variable, smart-plug recovery available —
before attempting the same concurrent-load repro that surfaced the
bottleneck).
