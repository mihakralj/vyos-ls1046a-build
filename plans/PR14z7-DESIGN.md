# PR14z7 — Activate FMan port-side KeyGen ingress (silicon offload root cause)

**Status:** design / ready-for-implementation  
**Branch:** `ask20`  
**Depends on:** PR14z5 (dual-pipeline), PR14z6 (egress-echo filter), PR14r (CC node cap-127)  
**Unblocks:** M2 acceptance gate (`bin/verify-ask-flow-offload.sh`)

## 1. Symptom and measurement trail

**Fresh-boot run 2026-05-19** (PR14z5 + PR14z6 active, all CC nodes empty,
shared MURAM intact):

| metric                                  | value          |
| --------------------------------------- | -------------- |
| iperf3 aggregate (8 streams, 30 s)      | 6.849 Gbps     |
| DUT CPU under load (100 − %idle)        | 69.44 %        |
| baseline (same iperf3, ask flowtable removed) | 2.67 Gbps / 85 % softirq |
| `REPLACE installed` (silicon flows)     | 23             |
| `REPLACE skip` (PR14z6 egress-echo)     | 141            |
| `REPLACE dedup` (PR14r)                 | 130            |
| `PR14y defer`                           | 69             |
| `REPLACE flow_insert=-12 (-ENOMEM)`     | 20             |
| eth3 RX packets received                | 602            |
| **eth3 RX dropped**                     | **20,181,991** |

Verdict: **FAIL** on CPU; throughput marginal.

Both KG schemes ARE bound:
```
ask: hw: FMan PCD v4-TCP scheme bound to port 9 dir 0 (PR14z5 dual-pipeline — HW offload active)
ask: hw: FMan PCD v4-TCP scheme bound to port 8 dir 1 (PR14z5 dual-pipeline — HW offload active)
```
Both directions install (mix of `ingress=eth3` and `ingress=eth4` cookies).
Hex `hw_id` values are real silicon slot indices. Per-direction CC trees
fit budget. Cap-127 (PR14r-halved for two pipelines) has plenty of headroom
on a fresh boot. None of the previously suspected blockers (PR14r cap,
PR14z4 KG-scheme contention, PR14r tombstone accumulation) are the
actual cause this time.

The 6.849 Gbps result is **the kernel `nf_flow_table` SW fast-path**,
NOT silicon. Proof: baseline-without-flowtable is 2.67 Gbps; with
flowtable but presumed-silicon it's only 6.85 Gbps — far below the
10 Gbps cap of either SFP+ port. If silicon were actually classifying
frames it would saturate the link at <5 % CPU. Instead CPU sits at
70 % carrying every byte through softirq.

The 20 M eth3 RX drops are the smoking gun: frames arrive on the wire
but the BMan pool drains because the CPU can't ferry them out fast
enough. The FMan classifier never gets a chance to redirect them via
a chain — it's bypassed.

## 2. Root cause

The mainline kernel `drivers/net/ethernet/freescale/fman/fman_port.c`
(linux v6.18, confirmed via upstream-source inspection) exposes exactly
one port-side PCD primitive:

```c
/*
 * fman_port_use_kg_hash
 * @port: A pointer to a FM Port module.
 * @enable: enable or disable
 *
 * Sets the HW KeyGen or the BMI as HW Parser next engine, enabling
 * or bypassing the KeyGen hashing of Rx traffic
 */
void fman_port_use_kg_hash(struct fman_port *port, bool enable)
{
    if (enable)
        /* After the Parser frames go to KeyGen */
        iowrite32be(NIA_ENG_HWK, &port->bmi_regs->rx.fmbm_rfpne);
    else
        /* After the Parser frames go to BMI */
        iowrite32be(NIA_ENG_BMI | NIA_BMI_AC_ENQ_FRAME,
                    &port->bmi_regs->rx.fmbm_rfpne);
}
EXPORT_SYMBOL(fman_port_use_kg_hash);
```

This writes the **per-port BMI Rx Frame-Parser Next-Engine register**
(`FMBM_RFPNE`). The two possible values are:

- `NIA_ENG_HWK` — post-Parser frames hand off to the **HW KeyGen**
  block (our scheme + CC tree pipeline).
- `NIA_ENG_BMI | NIA_BMI_AC_ENQ_FRAME` — post-Parser frames go
  directly to the BMI enqueue path with the **default RX FQ** as
  destination, BYPASSING KeyGen entirely. This is the boot-time
  default for any port whose DTS `pcd_fqs_count` is zero.

The only call site in mainline that flips `FMBM_RFPNE` to `NIA_ENG_HWK`
is inside `fman_port_init()`, gated by `pcd_fqs_count > 0`:

```c
if (port->cfg->pcd_fqs_count) {
    keygen = port->dts_params.fman->keygen;
    err = keygen_port_hashing_init(keygen, port->port_id,
                                   port->cfg->pcd_base_fqid,
                                   port->cfg->pcd_fqs_count);
    if (err)
        return err;

    fman_port_use_kg_hash(port, true);
}
```

For our LS1046A board, `mono-gateway-dk.dts` does **not** set
`pcd_base_fqid` / `pcd_fqs_count` on any FMan port node. So
`fman_port_use_kg_hash(true)` is never called. Every RX FMan port
boots with `FMBM_RFPNE = NIA_ENG_BMI`, and every frame goes:

```
PHY → MAC → Parser → BMI default FQ → dpaa_eth NAPI → kernel stack
```

— never touching our KG scheme even though it's correctly bound at
the KG block.

This matches the live measurement perfectly:
- KG scheme programmed: yes
- CC tree programmed: yes
- MANIP chain handles created per flow: yes
- Frames actually entering the KG pipeline: **zero**
- Effective offload achieved: zero
- Apparent acceleration (6.85 vs 2.67 Gbps): SW flowtable only

## 3. Fix design

### 3.1 New kernel patch `0039-dpaa-export-rx-fman-port.patch`

Export a tiny helper that maps a `struct net_device *` (a DPAA1
ingress netdev like `eth3`) to its underlying `struct fman_port *`.

The dpaa-eth driver already carries this reference internally via
`dpaa_priv->mac_dev->port`. We expose it.

```c
/* drivers/net/ethernet/freescale/dpaa/dpaa_eth.c (new) */

#include <linux/fsl/fman_port.h>     /* struct fman_port */
#include <linux/fsl/dpaa_flow_offload.h>

/**
 * dpaa_get_rx_fman_port - resolve the RX FMan port behind a DPAA netdev
 * @dev:   DPAA1 netdev (e.g. eth3)
 *
 * The underlying RX FMan port descriptor is the handle accepted by
 * fman_port_use_kg_hash() and the rest of the fman_port_* API.  This
 * helper walks dpaa_priv → mac_dev → port to expose it to out-of-tree
 * consumers that have a netdev but no direct port pointer.
 *
 * Returns NULL for non-DPAA netdevs.
 */
struct fman_port *dpaa_get_rx_fman_port(struct net_device *dev)
{
    struct dpaa_priv *priv;

    if (!dev || dev->netdev_ops != &dpaa_ops)
        return NULL;
    priv = netdev_priv(dev);
    if (!priv->mac_dev)
        return NULL;
    return priv->mac_dev->port;
}
EXPORT_SYMBOL_GPL(dpaa_get_rx_fman_port);
```

And the declaration in the OOT-consumer header
`include/linux/fsl/dpaa_flow_offload.h`:

```c
struct fman_port;
struct fman_port *dpaa_get_rx_fman_port(struct net_device *dev);
```

Patch sequencing: this becomes patch 0039 (after 0038), staged via the
same `kernel/common/scripts/apply-to-tree.sh` flow as the rest of the
ASK series.

### 3.2 `ask_hw.c` — call site

In `ask_hw_port_bind(port_id, dir, ingress_dev)`:

After `fman_pcd_kg_bind_port()` succeeds and we commit the pipeline
state under `h->lock`, look up the FMan port and arm KeyGen:

```c
struct fman_port *fp = dpaa_get_rx_fman_port(ingress_dev);
if (fp) {
    fman_port_use_kg_hash(fp, true);
    p->fman_port = fp;          /* stash for teardown */
    ask_pr_info("hw: port %u dir %u: FMBM_RFPNE → NIA_ENG_HWK (KeyGen enabled)\n",
                port_id, dir);
} else {
    ask_pr_warn("hw: port %u dir %u: dpaa_get_rx_fman_port returned NULL — frames will bypass our KG scheme\n",
                port_id, dir);
}
```

Note: the existing `ask_hw_port_bind()` signature does NOT take
`ingress_dev`. We extend it (and the single caller in
`ask_flow_offload.c::ask_flow_offload_replace`, which already has
`ingress_dev` in scope from `flow_block_cb_priv()`).

In `ask_hw_pcd_teardown()`:

```c
for (d = 0; d < ASK_HW_DIR_NR; d++) {
    struct ask_hw_pipeline *p = &h->pipe[d];
    if (p->fman_port) {
        fman_port_use_kg_hash(p->fman_port, false);
        p->fman_port = NULL;
    }
    /* ... existing scheme/cc_v4_tcp/cc_tree teardown ... */
}
```

This restores the port to the boot-time default `FMBM_RFPNE` value
(BMI bypass) so a subsequent rmmod+modprobe cycle returns the kernel
to a clean state.

### 3.3 `struct ask_hw_pipeline` extension

```c
struct ask_hw_pipeline {
    struct fman_pcd_cc_tree   *cc_tree;
    struct fman_pcd_cc_node   *cc_v4_tcp;
    struct fman_pcd_kg_scheme *scheme;
    struct fman_port          *fman_port;   /* PR14z7: NULL until bind */
    u8                         bound_pid;
};
```

`fman_port` is the only new field. It's purely a back-pointer for
teardown — the bound_pid is still authoritative for "is this pipeline
in use".

### 3.4 Failure modes

| failure                                  | behaviour                                  |
| ---------------------------------------- | ------------------------------------------ |
| `dpaa_get_rx_fman_port()` returns NULL   | warn, leave scheme bound but KG bypassed (SW fallback active). |
| `fman_port_use_kg_hash()` is void — cannot fail | no failure return; relies on iowrite32be visibility. |
| Module rebuild without patch 0039        | symbol not exported → modprobe link-time fail; CI catches this at `bin/local-build.sh`. |
| Reload ask without rebooting             | teardown disables KG, init re-enables it on rebind. Idempotent. |

### 3.5 Expected post-fix M2 numbers

Working hypothesis (silicon path engaged for all 23-30 active 5-tuples):

| metric                | predicted          | gate    |
| --------------------- | ------------------ | ------- |
| iperf3 aggregate      | ≥ 9 Gbps           | ≥ 2.0   |
| DUT CPU under load    | < 5 %              | ≤ 5.0   |
| eth3 RX dropped       | < 1000             | n/a     |

These predictions assume the existing PR14x MANIP chain (RMV +
INSRT + IPv4 TTL/cksum) actually executes correctly when the slot
gets a hit. If the chain is broken in some other way, we'll see
high CPU drop but throughput regression toward the BMan-pool-
drain limit; further bisection will be needed.

## 4. Implementation plan

1. **kernel patch 0039** — write `0039-dpaa-export-rx-fman-port.patch`
   in `kernel/flavors/ask/patches/`. Verify it applies cleanly with
   `bash scripts/patch-health.sh --source release` (CI-level health
   check). Visual `grep` the produced `dpaa_eth.c` for the new
   `dpaa_get_rx_fman_port` export.
2. **ask_internal.h** — extend `struct ask_hw_pipeline` with
   `struct fman_port *fman_port;`. Forward-declare `struct fman_port`.
   Bump `ask_hw_port_bind` signature.
3. **ask_hw.c** — call `fman_port_use_kg_hash(fp, true)` in port_bind;
   `fman_port_use_kg_hash(fp, false)` in teardown.
4. **ask_flow_offload.c** — pass `ingress_dev` into the `ask_hw_port_bind`
   call (already in scope).
5. **build** — `bin/dev-build.sh kernel` to produce a kernel with patch
   0039 applied, plus rebuild ask.ko, push to DUT.
6. **measurement** — fresh boot, `bash bin/m2-dut-prep.sh`,
   `bash bin/verify-ask-flow-offload.sh`. Confirm:
   - `dmesg | grep 'FMBM_RFPNE → NIA_ENG_HWK'` shows both bindings.
   - CPU < 5 %, throughput ≥ 2 Gbps (M2 gate passes).
   - eth3 RX drops near zero.
7. **commit** — PR14z5 + PR14z6 + PR14z7 hunks together, plus the
   updated `plans/ASK2-IMPLEMENTATION.md` tracker rows 75-79.
8. **Qdrant** — record the M2-pass result and the FMBM_RFPNE invariant.

## 5. Risks

- **Promiscuous frames bypass KG anyway.** Some FMan flow types
  (broadcast, multicast, error-classified) may route around KeyGen
  regardless of FMBM_RFPNE. If iperf3 is unicast over a /30 this
  is not an issue; for the full M2 workload it shouldn't matter.
- **Per-port-side ownership conflict.** If the dpaa-eth driver
  later re-writes FMBM_RFPNE during a link-change or MTU change, our
  setting gets clobbered. Workaround: re-arm KG after every
  flow_indr_dev_register UP event. Defer to v1.1 unless verify shows
  the symptom.
- **PR14r tombstone accumulation still real.** Once KG is armed and
  flows actually classify, slot tombstones from churn WILL accumulate
  to cap-127 over time (a few hours of session churn). PR14z8 will
  add slot-reuse freelist. Out of scope for M2 first-pass.
- **MANIP chain bug uncovered by silicon engagement.** The chain
  `[m_rmv, m_insrt, m_ipv4]` has never been exercised at line rate.
  TTL decrement + IPv4 cksum recompute is silicon-only at PR14x.
  Possible: malformed egress frames → TX FIFO error → drop. Mitigation:
  watch eth4 TX error counters during verify run; if non-zero, file
  PR14z9 to investigate MANIP chain integrity.

## 6. Stable invariants captured by this PR

- The FMan **per-port** Rx Frame-Parser Next-Engine register
  (`FMBM_RFPNE`, offset `0x040` of the BMI Rx port regs) is the
  authoritative gate between Parser and KeyGen. Without
  `NIA_ENG_HWK` in this register, **no PCD work below the
  port-level is reachable** — the entire `fman_pcd_*` API surface
  is essentially a no-op for traffic.
- The mainline kernel only flips this gate in `fman_port_init()`
  when DTS supplies `pcd_fqs_count > 0`. Boards that do not
  configure DTS PCD FQs (LS1046A Mono Gateway DK is one) need
  explicit run-time activation via `fman_port_use_kg_hash(port, true)`.
- The activation handle (`struct fman_port *`) is private to
  the dpaa-eth driver and must be exported (PR14z7 patch 0039)
  for any out-of-tree silicon-offload module to reach it.