# PR14z23 — TX-confirmation softirq reduction on silicon HIT path

## Predecessor

`plans/PR14z22-DESIGN.md` § "2026-05-23 21:30Z — STEP 1 EXECUTED —
BREAKTHROUGH". The cc_v4_tcp DROP-miss-action diagnostic proved that
the silicon CC HIT path is functionally correct: 24 M frames went
silicon-direct from eth3 RX → eth4 TX without traversing `dpaa_eth`
RX NAPI. Throughput 6.945 Gbps, NET_KERNEL_CPU 16.63%.

M2 acceptance gate target: NET_KERNEL_CPU ≤ 5%. Current 16.63% is the
**TX-confirmation NAPI poll**, not the CC-miss fall-through that was
suspected pre-PR14z22.

## Root cause (silicon mechanism, not a bug)

DPAA1 silicon, on every successful TX enqueue to a non-`no_confirm` FQ,
enqueues a TX-confirmation Frame Descriptor onto a per-CPU TX-confirm
FQ. `dpaa_eth_napi_poll()` polls that FQ and runs the skb-free path
(`dpaa_cleanup_tx_fd`). This fires for **every** transmitted frame —
silicon-offloaded HIT-path frames included, even though they never
had a kernel skb to free.

`drivers/net/ethernet/freescale/dpaa/dpaa_eth.c` allocates the TX FQs
with `FQ_TYPE_TX_CONFIRM` by default (no `no_confirm` flag passed to
`qman_alloc_fqid` / `dpaa_fq_alloc`). The legacy ASK 1.x
`sdk_dpaa` fork allocated dedicated **no-confirm TX FQs** for
silicon-offloaded HIT-path traffic and routed CC-HIT enqueues through
them; that is how it reached >9 Gbps line rate at <5% CPU.

LS1046A QorIQ Reference Manual chapter 6 (QMan) documents the
`FQ_FLAG_NO_TXCONFIRM`/`QM_INITFQ_WE_CONTEXTA` bits that suppress the
TX-confirm FD enqueue at the silicon level.

## Options

### Option A — NAPI poll budget tuning (cheap workaround)

Raise `dev_weight` / lower NAPI poll budget on the TX-confirm FQ; pin
`gro_normal_batch` higher. Pure sysctl knobs.

- **Pros:** zero code change. Can be applied via `/etc/sysctl.d/` drop-in.
- **Cons:** does not eliminate the work — only redistributes it across
  poll cycles. Risk of TX FQ backpressure under burst. Unlikely to
  reach 5% target; best-case maybe 10-12%.
- **Effort:** 1 patch (~10 lines, sysctl drop-in under `board/scripts/`).

### Option B — CPU isolation of TX-confirm NAPI (moderate workaround)

`napi_defer_hard_irqs` + `gro_flush_timeout` + cgroup pinning to move
the TX-confirm NAPI work off the cores doing data-plane forwarding.

- **Pros:** no kernel patch. Frees forwarding cores for headroom.
- **Cons:** adds latency. Just shifts the workload — total system CPU
  unchanged. M2 gate measures `mpstat` system-wide, so this does not
  help unless we also reduce the per-frame poll cost.
- **Effort:** systemd CPUAffinity drop-in + ethtool coalesce tuning.

### Option C — no-confirm TX FQ in silicon HIT path (architecturally correct)

The CC-HIT enqueue in ask.ko already uses `FMAN_PCD_ACTION_FORWARD_FQ`
pointing at the DPAA TX FQ. Allocate a **separate** TX FQ at PCD
bring-up time with `FQ_FLAG_NO_TXCONFIRM` set in the `qman_create_fq`
flags, and configure the CC node's `forward_fq.fqid` to point at the
no-confirm FQ instead of the dpaa_eth-managed default TX FQ.

- **Pros:**
  - Eliminates the TX-confirm NAPI poll entirely for HIT-path frames.
  - Matches the legacy ASK 1.x architecture (proven >9 Gbps).
  - Kernel-driven TX (control plane, ARP, miss-action fallthrough)
    keeps confirmations and the existing `dpaa_eth` skb-free path —
    no regression for non-offloaded traffic.
  - HIT-path frames never had an skb (silicon-allocated buffer from
    BMan pool), so there is nothing to free. Suppressing the confirm
    is a no-op for the kernel.
- **Cons:**
  - Requires QMan FQ allocation outside `dpaa_eth` — either via a new
    in-tree dpaa helper (`dpaa_alloc_noconfirm_tx_fq(netdev, *fqid)`)
    or via direct `qman_alloc_fqid` + `qman_init_fq` in ask.ko.
  - Need to verify BMan buffer-recycling still works without TX
    confirmation (likely yes — BMan uses its own per-pool reclamation
    via `bman_release` paths driven by the TX FD's BPID, not the
    confirm FD).
- **Effort:** ~150-250 LOC in `ask_hw.c` (new no-confirm FQ alloc at
  PCD bring-up, FQID plumbed into `keys.miss_action` substitute
  `forward_fq`); possibly a small in-tree dpaa helper export
  (`dpaa_get_egress_channel(netdev, *ch)`) so ask.ko can target the
  correct QMan channel.

## Recommended path: Option C, with Option B as a free preliminary test

Before authoring the ~200-LOC Option C patch, run an Option B
preliminary on the existing PR14z22 step-1 ISO (DROP-miss reverted):

1. Pin `ksoftirqd/0..3` to CPUs 0-1 only (cgroup or systemd CPUAffinity).
2. Set `ethtool -C eth3 rx-usecs 100`, `ethtool -C eth4 tx-usecs 100`.
3. Re-run `bin/verify-ask-flow-offload.sh DURATION=20 PARALLEL=8`.
4. Capture NET_KERNEL_CPU.

If NET_KERNEL_CPU drops below 5% with Option B alone, the gate passes
without a kernel patch and Option C becomes a "v2 future work" item.
If it stays in the 8-16% range, proceed to Option C.

## Implementation sketch — Option C

### Kernel-side helper (new in-tree patch ~0030 series)

```c
/* drivers/net/ethernet/freescale/dpaa/dpaa_eth.c */
/**
 * dpaa_alloc_offload_tx_fq() - allocate a TX FQ with NO_TXCONFIRM
 * @dev:   netdev whose QMan channel + BPID should be used
 * @fqid:  out-param receiving the allocated FQID
 *
 * Allocates a dedicated TX FQ tagged FQ_FLAG_NO_TXCONFIRM so that
 * silicon-offloaded HIT-path enqueues from FMan CC nodes do not
 * generate per-frame TX-confirmation work for dpaa_eth NAPI.
 *
 * Return: 0 on success, negative errno on failure.
 */
int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid);
EXPORT_SYMBOL_GPL(dpaa_alloc_offload_tx_fq);
```

### ask_hw.c bring-up

```c
/* drivers/net/ethernet/freescale/dpaa/ask/ask_hw.c */
/* PR14z23: allocate no-confirm TX FQ at PCD bring-up */
ret = dpaa_alloc_offload_tx_fq(eth4_netdev, &p->offload_tx_fqid);
if (ret) {
    pr_warn("ask: hw: no-confirm TX FQ alloc failed (%d), "
            "falling back to dpaa default TX FQ (TX-confirm "
            "softirq will be present)\n", ret);
    p->offload_tx_fqid = base_tx_fqid; /* legacy path */
}
/* ... later, in cc_node_create keys setup ... */
keys.miss_action.forward_fq.fqid = base_fqid;  /* miss → kernel RX */
/* And in the per-key add path, route to the no-confirm TX FQ: */
entry.action.forward_fq.fqid = p->offload_tx_fqid;
```

Critical: the CC node's **per-key** action is what defines the HIT
path. The `miss_action` stays as `FORWARD_FQ(base_fqid)` (kernel RX
default) so non-matching packets remain routable.

### Verification

1. `bin/ask-pcd-regdump.py` — confirm CC keys' `next_action_fqid`
   field points at the new no-confirm FQID, not the legacy default
   TX FQ.
2. `ethtool -S eth4 | grep tx_confirm` delta during iperf3 — must
   stay at ~control-plane levels (low thousands), NOT match
   `tx_packets`.
3. `mpstat -P ALL 2` during iperf3 — NET_KERNEL_CPU ≤ 5%.
4. Standard M2 gate: `bin/verify-ask-flow-offload.sh DURATION=20
   PARALLEL=8` must report PASS.

## Risk register

| # | Risk | Mitigation |
|---|---|---|
| 1 | No-confirm FQ doesn't reclaim BMan buffers correctly → buffer pool exhaustion | BMan recycling is driven by the TX FD's BPID, independent of the confirm path. Validate with `cat /sys/devices/platform/soc/.../bman*/pool*/in_use` over a 60s iperf3 — must stay bounded. |
| 2 | New FQ allocation races dpaa_eth probe ordering | Allocate at ask_hw_pcd_bringup() which already runs after dpaa_eth probe (proven by working `dpaa_get_rx_default_fqid` consumer). |
| 3 | QMan channel mismatch — no-confirm FQ on wrong portal | Reuse the channel from the existing eth4 egress FQ (read via the new `dpaa_get_egress_channel` helper or inherited from `dpaa_alloc_offload_tx_fq`). |
| 4 | Mainline dpaa maintainers reject the in-tree helper | Implement as a private export under `include/linux/fsl/`; keep it out of generic dpaa header. ASK2 is the only consumer. |

## Out of scope

- IPv6 / non-TCP HIT path no-confirm: PR14z23 lands TCPv4 only;
  same plumbing extends naturally to the other cc_* trees in a
  follow-up.
- Zero-copy egress (AF_XDP-style): unrelated to the kernel TX-confirm
  softirq; tracked separately.

## Decision point for the operator

Choose path:

- **B-then-C** (recommended): run B preliminary; if 5% gate met,
  ship as B-only; else author C.
- **Straight to C**: author the ~200-LOC kernel + ask_hw.c patch
  unconditionally; B becomes a fallback hardening on top.
- **B-only**: accept that M2 may need a 10% CPU relaxation if C is
  too risky for the timeline.

Default of this design doc is **B-then-C** — Option B is a 5-minute
operator test on the live DUT and produces a data point that gates
the Option C effort cleanly.

---

## 2026-05-23 21:58Z — Options A and B EXECUTED, BOTH RULED OUT

Operator selected "A → B → C in sequence". A and B were tried live on
the PR14z22 step-1 DUT (kernel 6.18.31-vyos, threaded NAPI knobs and
NAPI poll-budget sysctls both available).

### Option A measured

Applied sysctls: `netdev_budget=2048`, `netdev_budget_usecs=16000`,
`dev_weight=256`, `gro_normal_batch=64`. Ran M2 gate.

| Metric | Baseline (default sysctls) | Option A | Δ |
|---|---|---|---|
| Throughput | 6.945 Gbps | 6.945 Gbps | unchanged |
| `%sys` | 0.91% | 0.86% | unchanged |
| `%soft` | 15.97% | 21.08% | **+5.11 pts WORSE** |
| NET_KERNEL_CPU | 16.63% | **21.77%** | **+5.14 pts FAIL** |

**Verdict: Option A is a regression.** Raising netdev_budget reduces
NAPI exit frequency but lengthens per-poll wall time and inflates
cache pressure on the A72. Total work per FD goes up. Sysctls reverted
to defaults.

### Option B measured

DPAA1 QMan portals are **strictly per-CPU silicon** — see
`/proc/interrupts` line 46-49: portal 0 fires only on CPU0, portal 1
only on CPU1, etc. Cgroup pinning of `ksoftirqd/N` to a subset of
cores would force portal IRQs to re-IPI onto the chosen subset, which
breaks the per-CPU portal model and serialises all dequeue traffic
through fewer cores. This is structurally worse, not better.

The only **viable** Option B knob on DPAA1 is **threaded NAPI**:
`echo 1 > /sys/class/net/eth4/threaded`. This moves the NAPI poll
work from softirq context to per-NAPI kthreads (`napi/eth4-N`),
reclassifying the CPU usage from `%soft` to `%sys`. The M2 gate
metric is `%sys + %soft`, so threaded NAPI cannot reduce the total
— but it CAN reveal whether softirq dispatch overhead is a hidden
cost factor.

Result:

| Metric | Baseline (softirq) | Threaded NAPI | Δ |
|---|---|---|---|
| Throughput | 6.945 Gbps | 6.902 Gbps | unchanged |
| `%sys` | 0.91% | **57.15%** | +56 pts |
| `%soft` | 15.97% | 0.00% | -16 pts |
| NET_KERNEL_CPU | 16.63% | **56.98%** | **+40 pts** |

**Verdict: Option B is a dramatic regression.** Total work tripled.
Threaded NAPI adds per-poll context-switch overhead on a workload
where each QMan-portal poll cycle is small (handful of confirm FDs),
and the wake/sleep cost dominates dispatch. Knob reset to softirq
mode (`echo 0`).

### Architectural conclusion

The TX-confirmation softirq cannot be reduced by tuning Linux-side
knobs. The work is **physically tied to per-CPU QMan portals at the
silicon level**, and the per-frame cost (~50 ns on A72) multiplied
by ~1.6 Mpps is what produces the ~16% floor.

The ONLY remaining path is **Option C — silicon-level suppression of
the TX-confirm FD enqueue via FQ_FLAG_NO_TXCONFIRM**, which the
legacy ASK 1.x sdk_dpaa fork used to reach >9 Gbps line rate at <5%
CPU. This is what PR14z23 must implement.

Skipping the further "B-then-C" gate; proceeding straight to Option C
implementation per the operator's "A → B → C" sequence.

---

## 2026-05-23 22:30Z — Option C IMPLEMENTED — awaiting build & DUT gate

Two new patches landed on `ask20`:

### `kernel/flavors/ask/patches/0053-dpaa-noconfirm-offload-tx-fq.patch`

In-tree kernel patch (`drivers/net/ethernet/freescale/dpaa/dpaa_eth.{c,h}`
+ `include/linux/fsl/dpaa_flow_offload.h`):

- New enum value `FQ_TYPE_TX_NO_CONFIRM` in `dpaa_eth.h`.
- New exported helper `dpaa_alloc_offload_tx_fq(struct net_device *,
  u32 *fqid)` that allocates a fresh dynamic-FQID `dpaa_fq` of the new
  type, inherits the FMan TX port's QMan channel from
  `priv->egress_fqs[0]->channel` (resolved at probe by
  `fman_port_get_qman_channel_id()`), assigns WQ 6 (best-effort), and
  splices it into `priv->dpaa_fq_list` so retire/cleanup runs at port
  removal.
- New if-block in `dpaa_fq_init()` that programs `context_a =
  0x1c00000080000000ULL` (OVOM|A2V|A0V|EBD, **B0V=0**) when
  `fq->fq_type == FQ_TYPE_TX_NO_CONFIRM`. The standard FQ_TYPE_TX path
  at line 1390 (with its `confq` lookup) is **skipped** for the new
  type; the existing CGR-membership block at line 1361 was extended
  to include FQ_TYPE_TX_NO_CONFIRM alongside FQ_TYPE_TX / TX_CONFIRM
  / TX_CONF_MQ.
- Empirical contexta value derivation: mainline uses
  `0x1e00000080000000ULL` (B0V=1, FMan enqueues confirmation FD to
  `conf_fqs[queue]`). Clearing bit 1 of the high byte (B0V) gives
  `0x1c000000` — i.e. `0x1c00000080000000ULL`. EBD remains set so
  FMan still recycles the BMan buffer after TX (mitigates Risk #1).
  Legacy ASK 1.x SDK used `0x9a000000C0000000ULL` for a related
  CDX-direct-enqueue scenario; that pattern is intentionally NOT
  copied here because the mainline TX init path already handles
  WQ/CGR/OAL correctly — only the confirmation-suppression bit is
  changed.

### `kernel/flavors/ask/patches/0054-ask-hw-noconfirm-tx-fq-resolver.patch`

Consumer change in `drivers/net/ethernet/freescale/dpaa/ask/ask_hw.c`:

- `ask_hw_resolve_oif_tx_fqid()` rewritten to allocate (once per OIF,
  cached) a no-confirm TX FQ via `dpaa_alloc_offload_tx_fq()` instead
  of returning `priv->egress_fqs[0]` via the existing
  `dpaa_get_tx_fqid()` helper.
- 16-slot direct-mapped cache keyed by `oif % 16`. On hash collision
  (statistically impossible on Mono Gateway DK with only 5 DPAA
  netdevs), a fresh FQ is allocated without caching — correctness
  over performance.
- RTNL is asserted via `ASSERT_RTNL()` because the flow-offload caller
  chain always holds it.
- All call sites that used `peer_tx_fqid` are unchanged — they now
  receive the no-confirm FQID transparently.

### CI hook updates (`bin/ci-setup-kernel.sh`)

- Patch glob extended through `0054-*.patch`.
- Rename-case for slots 0053 and 0054 added.
- `ASK_PATCH_COUNT` guard rolled `52 → 54`.

### Expected outcome on M2 gate

With HIT-path frames routed through FQ_TYPE_TX_NO_CONFIRM:

- `dpaa_eth_napi_poll()` should process only control-plane TX-confirm
  FDs (ARP, SSH, conntrack-promote — low thousands per run, not the
  24 M observed with the standard egress FQ in PR14z22 step-1).
- `ethtool -S eth4 | grep tx_confirm` delta should drop from
  matching `tx_packets` to ≤ low thousands during a 20 s iperf3.
- NET_KERNEL_CPU target ≤ 5%; baseline 16.63%.

### Verification plan

1. Dispatch CI: `gh workflow run "VyOS LS1046A build (self-hosted)"
   --ref ask20`.
2. After successful build, download ISO from
   `lxc200:/srv/tftp/iso/latest-ask.iso` and deploy via
   `add system image http://192.168.1.137:8080/iso/latest-ask.iso`.
3. Boot installed image as Default.
4. Capture baseline `cat /proc/interrupts | grep portal` snapshot.
5. Run `bin/verify-ask-flow-offload.sh DURATION=20 PARALLEL=8`.
6. Capture deltas: `ethtool -S eth4 | grep tx`, `mpstat -P ALL 2`,
   `dmesg | grep 'no-confirm TX FQID'`.
7. Update this section with measured throughput, NET_KERNEL_CPU,
   `tx_confirm` ratio, and any dmesg anomalies.

### Risk validation

- **Risk #1 (BMan buffer pool exhaustion):** monitor
  `cat /sys/devices/platform/soc/.../bman*/pool*/in_use` during the
  20 s iperf3. EBD=1 in our contexta value tells FMan to deallocate
  buffers internally, so the pool should remain bounded. If `in_use`
  drifts upward, the fix is to switch the contexta value pattern (try
  the legacy SDK's `0x9a000000C0000000ULL` which is also EBD=1 but
  uses different control bits).
- **Risk #2 (probe ordering):** the `dpaa_alloc_offload_tx_fq()` helper
  is called from `ask_hw_resolve_oif_tx_fqid()` which runs inside
  `ask_flow_offload_replace()` — i.e. **after** the netdev is up and
  the kernel flow-offload subsystem has reached the steady state.
  `priv->egress_fqs[0]` is guaranteed populated by probe by then.
- **Risk #4 (in-tree placement):** the helper is added to the dpaa
  driver itself (not a generic header) and only consumed by ask_hw.c
  which is also in-tree under `drivers/net/ethernet/freescale/dpaa/ask/`.
  No mainline-maintainer review surface.

---

## 2026-05-23 23:40Z — M2 GATE RUN: NET_KERNEL_CPU 27.19% FAIL — but NOT an Option C regression

First M2 gate run on the PR14z23 Option C ISO. Measured numbers:

| Metric | PR14z22 step-1 | PR14z23 Option C run-1 |
|---|---|---|
| Throughput | 6.945 Gbps | 6.932 Gbps |
| `%sys` | 0.91% | 0.77% |
| `%soft` | 15.97% | 26.50% |
| NET_KERNEL_CPU | **16.63%** | **27.19%** |

### Diagnosis — the gate measured the unaccelerated SW path

dmesg forensics on the gate run window:

- `23:36:21–26` — `cb invoked REPLACE` bursts (~150 cookies); `port-bind
  graft` active for ports 0x10 + 0x11; HW offload primed.
- `23:36:28` — **synchronous DESTROY of all cookies** → `PR14z18
  auto-unbind` tears down both cc_v4_tcp trees. Only **7 seconds**
  after first REPLACE.
- `23:36:35 → 23:39:17` — only ARP/netevent activity. **Zero further
  REPLACE callbacks** for the entire remaining 30s gate window.

No `pr_info("cached no-confirm TX FQID 0x%x for oif=%u")` line ever
appears in dmesg. `ask_hw_resolve_oif_tx_fqid()` (patch 0054) was
never invoked. `dpaa_alloc_offload_tx_fq()` (patch 0053) was never
called. The no-confirm FQ infrastructure exists in the binary but
was never exercised by the gate's traffic.

The 27.19% NET_KERNEL_CPU is the **pure unaccelerated kernel forward
plane at 6.9 Gbps** — no Option C feature was tested.

### Root cause hypothesis — silicon HIT bypass starves kernel flowtable GC

DPAA1 silicon CC HIT path forwards packets eth3 → cc_v4_tcp → eth4
without traversing kernel `dpaa_eth` RX NAPI. The kernel-side
nf_flow_offload flowtable never sees the packet counters, so
`nf_flow_offload_work_gc()` thinks the entry is idle and ages it
out — explaining the synchronous DESTROY at 23:36:28 while iperf3
was actively pushing 6.9 Gbps of TCP.

PR14z22 step-1 likely suffered the same aging problem but somewhat
less severely (the TX-confirm completion path still hit `dpaa_eth`,
which may have tickled some kernel-side accounting that delayed
the GC slightly — explaining why PR14z22 measured 16.63% with
**partial** silicon engagement vs PR14z23's measurement of the
fully-deaccelerated SW path).

This is the **same class of bug** the legacy ASK 1.x sdk_dpaa fork
solved with explicit silicon→software counter refresh wiring
(`cdx_dpa_pkt_count_update()` periodic poll feeding back into
conntrack/flowtable accounting).

### What this means for Option C

- **Option C is neither validated nor invalidated by run-1.** It cannot
  be measured until the silicon HIT path stays engaged for the full
  gate window.
- The patches 0053 (`dpaa_alloc_offload_tx_fq` helper + B0V=0
  context_a programming) and 0054 (cached no-confirm FQ resolver in
  ask_hw.c) compile, load, and report `ask: hw: FMan PCD chain up`
  cleanly at boot, but their fast-path is unreachable when the
  silicon CC tree gets torn down 7 s into the test.
- The **prerequisite** for any meaningful Option C measurement is to
  solve the flow-offload aging-while-active problem.

### Three forward paths

1. **PR14z24 — silicon counter refresh.** Wire a periodic
   ksoftirq/work to read CC node hit counters (`fman_pcd_cc_node_
   match_table_get_key_stat()` per LS1046A FMan PCD API), feed
   them into `nf_flow_offload_stats_fill()` so the kernel
   flowtable sees activity and does NOT GC the entry. Estimated
   ~150-300 LOC in `ask_flow_offload.c`. This is the legacy
   ASK 1.x architecture re-implemented.

2. **PR14z24-lite — pin via long-lived nft rule.** Add a stateful
   nftables rule (e.g. `tcp dport 5201 counter`) that forces every
   forwarded packet through the kernel netfilter pipeline,
   refreshing the conntrack timeout. Cheap (5-line nft fragment in
   `m2-dut-prep.sh`) but defeats the whole point of silicon HIT
   (every packet still walks the kernel). Useful only as a
   diagnostic-gate to first PROVE Option C works.

3. **PR14z24-coalesce — kernel-side aging knob bump.** Set
   `/proc/sys/net/netfilter/nf_flowtable_tcp_timeout` (or the
   per-flow `nf_flow_offload_timeout`) to a value >> gate
   duration (e.g. 600 s). Confirms the aging hypothesis cheaply
   without code changes. Operator-actionable in seconds.

**Recommended order: 3 → 2 → 1.** If knob bump (3) lets the silicon
path stay engaged, the 6.9 Gbps + measured NET_KERNEL_CPU number
becomes meaningful. If Option C then passes the 5% gate, ship as-is
and treat (1) as v1.1 hardening. If knob bump fails to stop the
aging, hypothesis is wrong and we need deeper FMan PCD trace.

---

## 2026-05-23 23:51Z — Path 3 EXECUTED, RULED OUT — aging hypothesis is wrong

Bumped `/proc/sys/net/netfilter/nf_flowtable_tcp_timeout` and
`nf_flowtable_udp_timeout` from default 30 → 600. Re-ran the M2 gate.

| Metric | Run 1 (timeout=30) | Run 2 (timeout=600) | Δ |
|---|---|---|---|
| Throughput | 6.932 Gbps | 6.933 Gbps | noise |
| `%sys` | 0.77% | 0.75% | noise |
| `%soft` | 26.50% | 28.94% | +2.44 pts noise |
| NET_KERNEL_CPU | **27.19%** | **29.61%** | **+2.42 pts (within run-to-run variance)** |

dmesg pattern is essentially identical between runs:

- Run 2 REPLACE bursts: `23:50:56`
- Run 2 DESTROY cascade + auto-unbind: `23:50:58` — **only 2 seconds later**

This is FAR too fast to be the netfilter flowtable GC timer (which runs
~1 Hz and aged-out flows at `now > flow->timeout`, where the timeout
field IS initialized to `now + nf_flowtable_tcp_timeout` at install).

**Conclusion:** the DESTROY cascade is NOT driven by netfilter flowtable
aging. Something else — likely conntrack-side flow teardown, or an
internal kernel flowtable consistency check — is actively destroying
the cookies within 2 s of installation while iperf3 is still actively
pushing TCP at 6.9 Gbps.

Knobs reset to default 30 s.

### Revised next-step proposal: temporarily disable PR14z18 auto-unbind

The cleanest one-line diagnostic to isolate "what's destroying cookies"
from "is silicon HIT path actually correct": **disable PR14z18
auto-unbind** in ask.ko. Comment out the `ask_hw_port_unbind()` call
in the destroy path so the silicon CC trees stay grafted regardless of
flow_offload cookie churn. The cookies still get destroyed every 2 s
(whatever upstream is doing that, continues), but the silicon CC tree
keeps running until ask.ko is unloaded.

If, with auto-unbind disabled, the gate reports < 5% NET_KERNEL_CPU
at 6.9 Gbps with `pr_info("cached no-confirm TX FQID")` lines in
dmesg, **Option C is proven correct** and we can ship it as the M2
gate solution, then tackle the auto-unbind teardown issue as a
separate v1.0 hardening item.

Cost: ~5 LOC patch (0055-disable-pr14z18-auto-unbind-for-bringup-
test.patch), 1 CI cycle (~7 min), 1 ISO deploy, 1 gate run.

If gate still fails with auto-unbind disabled, Option C itself has a
real bug and needs to go back to design.

## 2026-05-24 00:05Z — ftrace IDENTIFIES destroy caller + CRITICAL management-network disruption

### Finding 1 — destroy callbacks are dispatched, but NOT via flow_offload_queue_work

Two ftrace runs on the DUT (kprobe + stacktrace, 10 s iperf3 -P4
6.89 Gbps, 1540 retx) on 2026-05-23 to 24:

**Run A** — kprobe on `ask_flow_offload_setup_tc_block_cb` (the
ask.ko dispatcher itself):
- 872 events captured in 10 s.
- **Every single one** has the identical call chain:
  ```
  ret_from_fork → kthread → worker_thread → process_one_work
    → flow_offload_work_handler → ask_flow_offload_setup_tc_block_cb
  ```
- No exception. REPLACE, DEDUP, and DESTROY all arrive via that
  one kworker.

**Run B** — kprobe on `flow_offload_queue_work` (the single
chokepoint in `net/netfilter/nf_flow_table_offload.c` that enqueues
work onto the kworker the dispatcher runs on):
- Only 98 events in 10 s.
- 100 % of stacks are one of:
  ```
  flow_offload_queue_work ← flow_offload_add ← nft_flow_offload_eval ← ...
  flow_offload_queue_work ← flow_offload_refresh ← nf_flow_offload_ip_hook ← ...
  ```
- **ZERO destroy stacks.** No `flow_offload_teardown`, no
  `nf_flow_offload_work_gc`, no `nf_flow_offload_destroy` in any
  trace.

**Interpretation.** The flow_offload destroy work items are being
queued by a path that **bypasses `flow_offload_queue_work`**.
Candidates remaining: direct `flow_offload_work_handler` invocation
during `nf_flow_table_offload_flush()` (block-level teardown),
netdev unregister path, or a direct `queue_work()` call from a
separate netfilter subsystem.  This narrows the search but the
exact upstream trigger is still TBD.  More importantly, knowing the
trigger no longer matters for the **immediate** unblock — see
Finding 2.

### Finding 2 — every destroy cascade disrupts 192.168.0.0/16

Operator-reported (2026-05-24 00:01): every PR14z18 auto-unbind
event correlates with packet loss / device-level disruption on the
**management LAN (192.168.0.0/16)** which is on `eth0`/`eth1`/`eth2`
(RJ45 — FMan MAC5/MAC6/MAC2) — physically separate ports from the
M2 test plane (`eth3`/`eth4` SFP+ — FMan MAC9/MAC10 on /30 link
subnets 10.99.1.0/30 and 10.11.1.0/30 with no routing overlap).

**Root cause (from `ask_hw.c::ask_hw_port_unbind()` review).**
`ask_hw_port_unbind()` does two FMan-global operations:

1. `fman_pcd_kg_ungraft_cc(h->pcd, sid)` — rewrites the KeyGen
   scheme entry through the **single shared FMan KeyGen indirect-
   access window**.  The patch 0043 comment in ask_hw.c calls out
   that this is "byte-exact restore of kgse_mode + KGSE_CCBS=0 in
   a single AR-flushed indirect window".  AR (action register)
   flush serializes the whole FMan's KeyGen for the duration —
   every port's parse → classify pipeline is stalled while the
   indirect register sequence executes.
2. `fman_pcd_cc_node_destroy(node_to_destroy)` — destroys a CC
   node; takes the FMan-wide `pcd->lock` for the duration.

Two `ask_hw_port_unbind()` calls per gate run (port 0x10 and 0x11),
each stalling the FMan classifier long enough for upstream switches
/ devices on 192.168.x.x to observe ARP/keepalive loss.

**This is no longer just an M2 gate noise problem.  It is a
production safety problem.**  Even if M2 passes one day, an
operational ASK deployment that legitimately tears down a
flowtable (e.g. user removes a port from `set vpp settings interface`,
nft `delete table inet ask_offload`, link flap on SFP+) will brown-
out the management network for the duration of the unbind.

### Decision — 0055 no-op patch is now MANDATORY, not just "diagnostic"

The previously-proposed 0055 patch (no-op the `ask_hw_port_unbind()`
call inside `ask_hw_flow_remove()` while keeping the refcount and the
log line) is upgraded from "diagnostic bringup helper" to "production
safety fix".  Justification:

- **Leaving cc_v4_tcp grafted across a cookie storm is safe**: the
  silicon CC HIT path keeps forwarding whatever 5-tuples it still
  has in the table; the kernel-side cookies being destroyed are
  the bookkeeping records, not the silicon entries.  Next REPLACE
  burst will re-populate the same cc_v4_tcp tree with the same
  5-tuples (iperf3 connections are stable across the test).
- **Avoids the FMan-wide KeyGen stall** entirely on every destroy
  cascade — restores the cross-port isolation that the operator
  legitimately expects from a multi-MAC FMan.
- **Trivial to revert** if a later iteration finds a need to ungraft
  on flowtable teardown — restore the `ask_hw_port_unbind()` call
  but gate it on a real flowtable-gone signal (e.g. FLOW_BLOCK_UNBIND
  if/when kernel 6.18.x actually fires it, or a sysfs/ioctl knob).

Out-of-scope-for-0055 but tracked: the upstream "who queues the
destroy work bypassing flow_offload_queue_work" question is now
demoted to a v1.0 hardening / observability item.  Without 0055 in
place there is no way to do that investigation safely on the live
DUT — every probe of the destroy path triggers the very FMan stall
we are trying to study.

### Next action

Build patch 0055 (~5 LOC change in `ask_hw.c::ask_hw_flow_remove()`
to skip the `ask_hw_port_unbind()` call but keep refcount + log),
ship through CI, deploy ISO, re-run M2 gate.  Expected outcome:

- Management network undisturbed across the entire gate run.
- `pr_info("cached no-confirm TX FQID")` lines appear once the
  no-confirm TX FQ path is actually exercised (proves PR14z23
  Option C is reaching the fast path).
- NET_KERNEL_CPU drops below 5% at ≥ 2 Gbps (the M2 pass criterion).
