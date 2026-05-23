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