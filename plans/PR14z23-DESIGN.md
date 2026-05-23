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
