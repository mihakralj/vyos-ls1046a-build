# ASK2 — Modern Linux Implementation of FMan 210 Hardware Offload for VyOS

**Status:** Draft v1.1 (supersedes v1.0). 2026-05-14. Material change vs v1.0: the previous provenance constraint on §13's FMan PCD subsystem has been relaxed. The deleted NXP SDK PCD tree (preserved in `mihakralj/kernel-ls1046a-build@464df181`) is GPL-2.0 (dual-licensed BSD-3-or-GPL-2.0) and the `we-are-mono/ASK` legacy stack is likewise GPL — both are usable as references for silicon behaviour, MURAM byte layouts, and register-write ordering. **What remains rejected is the SDK's architecture, not its silicon facts**: the `handle_t` opaque ABI, `fsl-ncsw` OS-shim layer, AMP IPC, `TRACE_RTOS`, `fm_ehash.c` custom hash, and nested `Peripherals/FM/Pcd/` directory layout are still discarded in favour of modern kernel idioms (typed structs, `kmalloc`/`devm_kzalloc`, `ioremap`/`readl`/`writel`, `mutex`/`spinlock`/`rcu`, `rhashtable`, tracepoints, flat `drivers/net/ethernet/freescale/fman/` layout). The LS1046A Reference Manual chapter 8 remains authoritative for byte-perfect MURAM layouts when SDK code disagrees with itself or omits a field. Net effect: PR14 body work unblocks immediately — sessions without RM access in the workspace can still author body PRs by cross-referencing the archived SDK alongside the RM. v1.0's §12.9 cost survey (~7800 LOC net target after dropping SDK bloat) is unchanged; only the provenance discipline is relaxed. Risk #12 reframed from copyright concern to maintainability concern. Open question #8 closed.
**Target hardware:** NXP LS1046A Mono Gateway DK.
**Target software:** Linux 6.18 LTS, ARM64, VyOS rolling release, FLAVOR=ask.
**Target microcode:** NXP proprietary 210-series fine classifier ucode (LS1046 r1.0). Loaded by U-Boot from SPI flash on every shipped Mono Gateway. Out of scope for this rewrite.

**Position statement.** The NXP ASK source published as GPL-2.0 at `github.com/we-are-mono/ASK` is **the protocol reference**, not the implementation baseline. It documents what the 210 microcode silicon expects on the wire. The Linux side of that source was written against kernel 5.4 with patterns that don't survive modern review: bespoke netlink protocol numbers, ad-hoc ioctl surfaces, per-CPU code paths that predate `this_cpu_*`, spinlock idioms that predate RCU adoption in the bridge fastpath, xfrm offload code that predates `xfrmdev_ops`, and a 5800-line in-tree hooks patch that hooks every netfilter callback in the kernel. None of that is the right starting point for a 2026 Linux 6.18 driver.

**What this spec is.** A from-scratch design for a kernel module + userspace daemon that drives the 210 microcode using only modern Linux 6.18 facilities (`flow_block_offload`, `xfrmdev_ops` packet mode, `genl_family`, `nf_flow_table` HW offload backing, `u64_stats_sync`, RCU for flow tables, generic netdev offload patterns matched by mlx5/ixgbe/nfp). The protocol-level facts about 210 — what bytes go on the wire, what opcodes mean — are documented in Section 12 as reference material extracted from the GPL source. The implementation does not copy that source.

---

## 0. How to read this spec

This document is the architecture source-of-truth for ASK2. It is written to be implementable from the text alone: every section either specifies a concrete file/API/wire format, or documents a hardware fact that must be verified on real silicon before the surrounding code can be trusted.

When the spec is wrong, fix the spec first, then regenerate code. Hardware findings that contradict or extend the spec are folded back into the relevant section (and into Section 12 for protocol-level facts) rather than living only in commit messages.

---

## 1. Why this design, not the NXP design

### 1.1 What the NXP source got right

The architectural model — first packet through conntrack, flow promoted to FMan via host command, subsequent packets handled in silicon — is correct and not negotiable. We keep it. The 210 microcode protocol facts (Section 12) are silicon behaviour, not design choice; we use them as-is.

### 1.2 What the NXP source got wrong for 2026

| NXP 1.x pattern | Why it's wrong now | Modern replacement |
|---|---|---|
| Custom `NETLINK_KEY=32` PF_KEYv2 channel | Hijacks an unused mainline protocol number; netdev maintainers would reject it on review | `genl_family` with multicast event groups |
| 5797-line in-tree hooks patch across 64 files | Mainline rejects this scale of vendor patching; impossible to upstream incrementally | ~600-line patch touching ~8 files, mostly EXPORT_SYMBOL_GPL additions |
| `comcerto_fp_netfilter.c` (600 lines) tapping every netfilter hook | Predates `nf_flow_table` and `flow_offload`; reinvents what mainline now provides | `nf_flow_table` HW-offload backing via `flow_block_cb` |
| Bespoke `ipsec_flow.c` (400 lines) | Predates `xfrm_state` packet-mode offload (merged 6.2, matured 6.10) | `xfrmdev_ops` with `xdo_dev_state_add` / `xdo_dev_policy_add` |
| `cdx_ctrl` chardev with 30+ ioctl numbers | Architecturally retro; no validation, no namespacing, no proper netlink discoverability | One `genl_family` with versioned attribute schema |
| Per-CPU stats via raw atomic_t | Cache-line ping-pong on 4 cores under load | `u64_stats_sync` per-CPU with batched aggregation |
| Spinlocks on flow lookup hot path | Bridge fastpath has used RCU since 2015 | RCU-protected flow lookup, `kfree_rcu` for eviction |
| `auto_bridge` polling bridge FDB | Polling has been wrong since RTM_NEWNEIGH event delivery existed | `register_netevent_notifier` for `NETEVENT_NEIGH_UPDATE`, `register_switchdev_notifier` |
| `cmm` userspace = 25k LOC, no namespacing, no dbus, FreeBSD/Linux portability shims | Code for a different decade | ~6 kLOC daemon: libmnl + sd-event + structured logging |
| CMM polls `/proc/net/route` and `/proc/net/neigh` | Polling routing/neigh on a router was wrong even in 2010 | `rtnetlink` `RTM_NEWROUTE`/`RTM_NEWNEIGH` subscription |
| Vendored SDK FMan/QMan/BMan drivers (266 files) | Kconfig-exclusive with mainline; blocks default flavor coexistence | Mainline `drivers/net/ethernet/freescale/dpaa/` |
| `dpa_app` parses XML at every load | XML parsing in kernel-launch UMH path; wrong layering | nftables-style binary configuration baked at build time, plus runtime UAPI |

### 1.3 The shape of the modern design

Three components, each with one clear job:

```
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│   askd (userspace, sd-event)                                     │
│     • subscribes to rtnetlink, xfrm_user, conntrack events       │
│     • applies policy (ALG excludes, VPP-promote ACLs)            │
│     • sends genl commands to ask.ko                              │
│     • exposes /run/askd.sock for ask-cli                         │
│                                                                  │
└──────────────────────────────────────┬───────────────────────────┘
                                       │ genl_family "ask" v1
                                       │ (Section 7)
┌──────────────────────────────────────▼───────────────────────────┐
│                                                                  │
│   ask.ko (modern kernel module, ~3500 LOC C)                     │
│     • genl_family + RCU flow table + xfrmdev_ops + flow_block_cb │
│     • registers as flow_offload backend for dpaa_eth netdevs     │
│     • talks to 210 ucode through fmd_host_cmd() abstraction      │
│     • talks to caam_qi.ko through caam_qi_ext_consumer_*()       │
│                                                                  │
└──────────────────────────────────────┬───────────────────────────┘
                                       │ fmd_host_cmd: opcode + payload
                                       │ (Section 12)
┌──────────────────────────────────────▼───────────────────────────┐
│                                                                  │
│   FMan 210 ucode + CAAM QI (silicon)                             │
│     • dynamic flow tables in MURAM                               │
│     • CAAM crypto descriptors registered via QI                  │
│     • offline ports for IPsec re-inject                          │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

Plus a four-patch in-tree series (was three in v0.8 — §12.9 added the fourth):

- `0001-caam-qi-share-descriptors.patch` — expose `caam_qi_ext_consumer_register/release` so `ask.ko` can share CAAM descriptors with FMan-initiated dequeues. ~150 lines. Upstream-ready. **Landed PR10.**
- `0002-dpaa-eth-flow-block.patch` — register `flow_block` callbacks on `dpaa_eth` netdevs so `nf_flow_table` and `tc-flower` can offload through `ask.ko`. ~300 lines. Upstream-candidate (NXP `dpaa_eth` maintainers are at Madalin Bucur / Camelia Groza). **Landed PR11.**
- `0003-fman-host-command-api.patch` — expose `fmd_host_cmd()` as a kernel-internal API in `drivers/net/ethernet/freescale/fman/`. ~200 lines. Required infrastructure for a future custom-microcode path (see §12.8); the stock QEF 210.10.1 ucode loaded on shipping Mono Gateways does not implement the opcode-dispatch protocol that motivated this patch. Preserved as future infrastructure. **Landed PR12.**
- `0004-fman-pcd-subsystem.patch` — **NEW in v1.0.** Modern reimplementation of the FMan PCD (Parse / Classify / Distribute) hardware-programming layer that mainline 6.18 is missing. Replaces the deleted NXP SDK `sdk_fman/Peripherals/FM/Pcd/` tree (~30,000 LOC vendor C, dual-licensed BSD-3-Clause-or-GPL-2.0) with **~7800 LOC** of modern kernel C structured per §13. Silicon facts (MURAM byte layouts, register-write ordering, AD encoding) are cross-referenced from the archived SDK and from `we-are-mono/ASK`; the architecture (typed structs, `devm_*` lifetimes, `ioremap`/`readl`/`writel`, `mutex`/`spinlock`/`rcu`, `rhashtable`, tracepoints, flat directory layout) is modern kernel C, not a forward-port. This is the patch that makes ASK2 v1.0 deliverable on stock NXP QEF microcode without resurrecting any NXP SDK architecture. **Not yet started — see plan PR14a–g.**

Total in-tree patch: ~8400 lines across the four patches. Patches 0001/0002/0003 are upstream-candidates today. 0004 is upstream-aspirational — the right consumers (NXP FMan maintainers Madalin Bucur, Camelia Groza, plus anyone shipping LS1043/LS1046 in mainline products) exist, but the patch is large enough that landing it requires sustained review effort.

No vendored SDK. No `cdx_ctrl` ioctl surface. No `NETLINK_KEY=32`. No `comcerto_fp_*`. No `dpa_app` UMH dance. No XML parsing in the load path. No FMC binary blob loading. No `fmlib` userspace dependency.

---

## 2. Hardware context (compressed reference)

Authoritative source: LS1046A Reference Manual chapter 8 (NDA-only). Read RM §8.7-8.10 before touching FMan code.

### 2.1 FMan with 210 ucode

The 210 microcode is a **proprietary fine classifier**: per-flow tables that the host populates at runtime via host commands. Different from public `108_4_9` ucode, which compiles static PCD from XML once at boot. The 210 ucode is what makes the BHR-ASK 7.0.0 performance numbers possible — 20 Gbps line-rate IPv4 forwarding with sub-5% CPU.

Per-FMan resources we touch:

- **32 KeyGen schemes** (silicon limit), used as the front-half of flow-table lookup
- **N classification nodes** managed dynamically by 210 ucode in MURAM
- **384 KB MURAM total** (LS1046A DTSI: `qoriq-fman3-0.dtsi muram@0 reg = <0x0 0x60000>`)
- **Available MURAM for flow tables: ~96 KB** after FIFO/internal-context reservation, giving ~750 hardware flow entries at ~128 B/entry
- **Policer** for slow-path exception-rate limiting
- **8 offline ports** (Mono Gateway DK uses 2 of them: OP1 for IPsec re-inject, OP2 for bridge flood)

### 2.2 CAAM SEC 5.4

- 4 Job Rings, plus QI integration with QMan
- Mainline driver `drivers/crypto/caam/` (Madalin Bucur, Pankaj Gupta maintainers at NXP)
- ASK2 reuses this driver. The one delta is patch `0001-caam-qi-share-descriptors.patch` exposing a small in-kernel API for FMan-initiated dequeues.

### 2.3 Mono Gateway DK port map

Identical to v0.6:

| netdev | FMan MAC | physical port | ASK port_id |
|--------|----------|----------------|-------------|
| `eth0`-`eth4` | mEMAC5/6/2/9/10 | 3×1G + 2×10G | 1,4,5,6,7 |
| OP1 | `fman0-oh@2` | IPsec re-inject | 8 |
| OP2 | `fman0-oh@3` | bridge flood | 9 |

---

## 3. The kernel module — `ask.ko`

### 3.1 What it is

A single kernel module, ~3500 LOC C, building out-of-tree against linux-6.18.x. Loaded after `dpaa_eth`. Registers:

- One `genl_family` named `ask` (version 1) with command ops and three multicast groups
- `flow_block_cb` against every `dpaa_eth` netdev so `nf_flow_table` and tc-flower flows route through it
- `xfrmdev_ops` against the same netdevs for IPsec packet-mode offload
- `nf_conntrack_event` notifier for software-initiated flow promotion
- `register_netevent_notifier` for neighbour updates
- `switchdev_notifier` for bridge FDB updates
- `caam_qi_ext_consumer_register` for each AEAD descriptor we install in CAAM

It does NOT register:
- A character device. Operators talk through genl.
- An ioctl interface. Legacy `cdx_ctrl` compatibility is in `libask` userspace shim only (Section 6.6), never in kernel.
- An ad-hoc netlink protocol number.

### 3.2 File layout

```
drivers/net/ethernet/freescale/ask/             # if we ever upstream
kernel/flavors/ask/oot/ask/                     # OOT location for v1.0
├── Kbuild
├── Kconfig                                     # ASK, ASK_DEBUG
├── ask_main.c                                  # module init/exit, version sysfs
├── ask_genl.c                                  # genl_family ops, validation, dump
├── ask_genl_attr.c                             # nla policy tables
├── ask_flow.c                                  # RCU flow table data structure
├── ask_flow_offload.c                          # flow_block_cb implementation
├── ask_xfrm.c                                  # xfrmdev_ops implementation
├── ask_caam.c                                  # CAAM descriptor lifecycle
├── ask_bridge.c                                # switchdev_notifier for L2 flows
├── ask_neigh.c                                 # NEIGH_UPDATE consumer for next-hop
├── ask_op.c                                    # offline port wiring
├── ask_hostcmd.c                               # 210 host-command wire-format encoder/decoder
├── ask_stats.c                                 # per-CPU u64_stats_sync aggregation
├── ask_debugfs.c                               # /sys/kernel/debug/ask/{flow_table,muram,stats,events}
├── ask_trace.h                                 # tracepoint definitions
├── ask_internal.h                              # private types
└── tests/                                      # in-tree kunit tests
    ├── ask_flow_test.c
    ├── ask_genl_test.c
    └── ask_hostcmd_test.c

include/uapi/linux/ask/
└── ask.h                                       # genl protocol UAPI
```

### 3.3 Concurrency model

The single rule: **the data path is RCU; the control path is a mutex.**

```c
struct ask_flow_table {
    struct rhashtable           ht;             /* RCU-safe, mainline hashtable */
    spinlock_t                  insert_lock;    /* serialises ASK_CMD_FLOW_ADD */
    struct mutex                config_lock;    /* serialises table_flush etc. */
    atomic64_t                  generation;     /* incremented on every change */
    struct kmem_cache          *flow_cache;     /* slab-allocated ask_flow */
};

struct ask_flow {
    struct rhash_head           hnode;          /* indexed by 5-tuple hash */
    struct rcu_head             rcu;            /* freed via call_rcu */
    /* immutable after insert: */
    struct ask_flow_key         key;            /* 5-tuple */
    struct ask_flow_action      action;         /* rewrite template */
    u32                         hw_flow_id;     /* opaque id returned by 210 */
    /* mutable, accessed only from the kthread that owns this flow: */
    struct ask_stats __percpu  *stats;
    atomic64_t                  last_seen;
};
```

Lookup: `rcu_read_lock(); rhashtable_lookup_fast(); rcu_read_unlock();`. Zero locks on the fast path. Eviction is via `rhashtable_remove_fast()` + `call_rcu(&flow->rcu, ask_flow_free)`.

Stats aggregation: per-CPU with `u64_stats_sync`, batched read at genl-dump time:

```c
struct ask_stats {
    struct u64_stats_sync       syncp;
    u64                         packets;
    u64                         bytes;
};
```

Stats updates from the hardware-eviction path use `this_cpu_add` under `u64_stats_update_begin/end`. Reads use `u64_stats_fetch_begin/retry`. No global lock, no atomic64 contention.

### 3.4 The 210 host-command interface (in kernel)

The protocol layer is implementable from this spec alone; the first-pass exercise on real silicon is required to confirm IRQ wiring and MURAM doorbell timing.

Single in-kernel function exposed by `0003-fman-host-command-api.patch`:

```c
/* drivers/net/ethernet/freescale/fman/fman_host_cmd.h */
int fmd_host_cmd(struct fman *fman,
                 u8 opcode,
                 const void *req, size_t req_len,
                 void *resp, size_t resp_buf_len,
                 size_t *resp_len_out);
```

All 210-specific binary encoding lives in `ask_hostcmd.c`. Opcode constants and struct layouts in Section 12. The opcode space is treated like an external ABI: validated at every layer, fuzzed in kunit, traced with tracepoint events for every command/response pair.

### 3.5 Kconfig

```
config ASK
    tristate "NXP LS1046A ASK2 (modern)"
    depends on FSL_DPAA
    depends on NF_FLOW_TABLE
    depends on XFRM_OFFLOAD
    depends on CRYPTO_DEV_FSL_CAAM_QI
    select GENERIC_NETLINK
    select RHASHTABLE
    help
      Hardware offload driver for NXP LS1046A FMan with proprietary 210
      microcode. Programs FMan classification tables and CAAM crypto
      descriptors to short-circuit established flows in silicon.

      Requires the 210 microcode loaded by U-Boot. Without it, the module
      detects the absence at probe time and refuses to load.

      This is the modern replacement for the NXP ASK 1.x cdx/auto_bridge/fci
      stack. It uses mainline kernel offload infrastructure (nf_flow_table
      hardware offload, xfrmdev_ops packet mode, generic netlink) rather
      than the vendor-specific patterns the legacy stack used.

config ASK_DEBUG
    bool "ASK debugfs and tracepoint support"
    depends on ASK && DEBUG_FS && TRACING
    default y if DEBUG_KERNEL
```

### 3.6 Probe sequence

```
ask_init():
    1. Locate FMan device(s) via DT (compatible "fsl,fman")
    2. Probe 210 ucode signature via fmd_host_cmd(OP_GET_UCODE_VERSION, ...)
       - if response doesn't match ASK_UCODE_VERSION_210_FAMILY: refuse load,
         dev_info_once("ASK ucode not present, refusing to load")
    3. Allocate flow tables (one per FMan)
    4. Register genl_family
    5. For each dpaa_eth netdev:
        - register flow_block_cb (Section 4)
        - set NETIF_F_HW_TC, NETIF_F_HW_ESP feature bits
        - assign xfrmdev_ops
    6. Configure offline ports OP1/OP2 via fmd_host_cmd
    7. Register nf_conntrack_event_notifier (priority NF_IP_PRI_LAST)
    8. Register netevent_notifier (NEIGH_UPDATE)
    9. Register switchdev_notifier
   10. Initialise debugfs hierarchy
   11. emit ASK_EVENT_READY on multicast group "events"
```

No userspace helper. No XML parsing. No UMH. The kernel loads, probes, registers, done. Userspace tools attach over genl when they're ready.

---

## 4. nf_flow_table HW offload backing

This is the single biggest architectural improvement over NXP 1.x. Instead of `comcerto_fp_netfilter.c` tapping every netfilter hook, ASK2 backs the mainline `nf_flow_table` infrastructure via `flow_block_cb`.

### 4.1 Operator-facing usage

```
nft add table inet filter
nft add flowtable inet filter f {                                   \
    hook ingress priority 0;                                        \
    devices = { eth0, eth1, eth2, eth3, eth4 };                     \
    flags offload;                                                  \
}
nft add chain inet filter forward { type filter hook forward priority 0; }
nft add rule inet filter forward ip protocol { tcp, udp } flow add @f
```

That's it. With `flags offload` and `ask.ko` loaded, every flow that hits the flowtable gets promoted to 210 silicon. Without the offload flag, it stays in software. Operators get HW offload through the same nft syntax they already know.

### 4.2 Kernel-side flow

```
nft user adds rule
    └─> nf_flow_table_offload_add_cb()
            └─> ask.ko flow_block_cb:
                    1. Translate flow_cls_offload to ask_flow_key + action
                    2. Allocate ask_flow, insert into rhashtable
                    3. fmd_host_cmd(OP_FLOW_INSERT, ...) → 210 silicon
                    4. Store returned hw_flow_id in ask_flow
                    5. Return success to nf_flow_table

packet arrives at eth0
    └─> FMan 210 ucode lookup hit
            └─> modify-and-forward via stored action
                    └─> egress on eth1
                    (CPU untouched)

connection ends, conntrack times out
    └─> nf_flow_table notifies offload backend
            └─> ask.ko flow_block_cb:
                    1. fmd_host_cmd(OP_FLOW_REMOVE, hw_flow_id)
                    2. rhashtable_remove + call_rcu free
```

### 4.3 Flow stats

Mainline `nf_flow_table` polls offload drivers for per-flow stats via `FLOW_CLS_STATS`. `ask.ko` handles this by reading per-CPU counters that have been updated from the 210 ucode eviction-notification path:

```c
static int ask_flow_block_cb_stats(struct ask_priv *priv,
                                    struct flow_cls_offload *cls)
{
    struct ask_flow *flow = ask_flow_lookup(priv, cls->cookie);
    u64 packets, bytes;
    unsigned int start;

    if (!flow)
        return -ENOENT;

    /* aggregate per-CPU stats */
    packets = bytes = 0;
    for_each_possible_cpu(cpu) {
        struct ask_stats *s = per_cpu_ptr(flow->stats, cpu);
        do {
            start = u64_stats_fetch_begin(&s->syncp);
            packets += s->packets;
            bytes += s->bytes;
        } while (u64_stats_fetch_retry(&s->syncp, start));
    }

    flow_stats_update(&cls->stats, bytes, packets, 0,
                      atomic64_read(&flow->last_seen),
                      FLOW_ACTION_HW_STATS_DELAYED);
    return 0;
}
```

The 210 ucode periodically dumps per-flow byte/packet deltas into a host-mapped region; `ask.ko` reads this region on a 1 Hz timer and updates the per-CPU stats. Delta-based, not absolute, so concurrent readers don't lose updates.

### 4.4 What this gets us for free

- **Bridge offload**: `nf_flow_table` already understands bridge-flowtable. Operators just add bridge interfaces to the `devices` list.
- **NAT offload**: SNAT/DNAT/PAT rewrites are part of the flow_offload action set. We translate them to 210 modify-and-forward action templates in `ask_flow_offload.c::ask_action_to_hw_template()`.
- **Per-flow stats** via the standard `nf_flow_table` query path.
- **Bypass-on-software-fastpath**: when MURAM is full, `flow_block_cb` returns `-EOPNOTSUPP` and the flow stays in software flowtable. Graceful degradation.
- **tc-flower coexistence**: same `flow_block_cb` handles both `FLOW_CLS_REPLACE` (from tc) and `FLOW_BLOCK_BIND` (from nf_flow_table). Operators who prefer tc-flower can use it.

We did not write a single line of "packet handling code". The kernel does it through standard infrastructure. We wrote: a flow-table data structure, a translation layer to 210 host commands, and a stats aggregator.

---

## 5. xfrm packet-mode offload

Modern Linux 6.18 `xfrmdev_ops` with packet-mode (Leon Romanovsky's series merged 6.2, matured through 6.10). The legacy NXP `ipsec_flow.c` (400 lines of bespoke xfrm tapping) is replaced by ~250 lines of standard `xfrmdev_ops` callbacks.

### 5.1 Operator-facing usage

```
ip xfrm state add src 10.0.0.1 dst 10.0.0.2 proto esp spi 0x100 \
    mode tunnel reqid 1 \
    aead 'rfc4106(gcm(aes))' 0xKEY 128 \
    offload packet dev eth3 dir out

ip xfrm policy add src 192.168.1.0/24 dst 192.168.2.0/24 \
    offload packet dev eth3 dir out \
    tmpl src 10.0.0.1 dst 10.0.0.2 proto esp reqid 1 mode tunnel
```

That's it. Same `ip xfrm` syntax everyone uses. The kernel routes the SA to ASK's `xdo_dev_state_add()` which:

1. Calls `caam_qi_ext_consumer_register()` to install the AEAD descriptor in CAAM
2. Calls `fmd_host_cmd(OP_SA_INSERT, ...)` to insert the ESP-SPI flow in 210 with action "to CAAM RX FQ"
3. Calls `fmd_host_cmd(OP_OP_CONFIG, ...)` to wire OP1 to re-inject decrypted frames

### 5.2 The xfrmdev_ops surface

```c
static const struct xfrmdev_ops ask_xfrmdev_ops = {
    .xdo_dev_state_add      = ask_xfrm_state_add,
    .xdo_dev_state_delete   = ask_xfrm_state_delete,
    .xdo_dev_state_free     = ask_xfrm_state_free,
    .xdo_dev_offload_ok     = ask_xfrm_offload_ok,
    .xdo_dev_state_update_stats = ask_xfrm_state_update_stats,
    .xdo_dev_policy_add     = ask_xfrm_policy_add,
    .xdo_dev_policy_delete  = ask_xfrm_policy_delete,
    .xdo_dev_policy_free    = ask_xfrm_policy_free,
};
```

Set on the netdev at probe:

```c
netdev->xfrmdev_ops = &ask_xfrmdev_ops;
netdev->features |= NETIF_F_HW_ESP | NETIF_F_HW_ESP_TX_CSUM;
netdev->hw_enc_features |= NETIF_F_HW_ESP | NETIF_F_HW_ESP_TX_CSUM;
```

No `cmm.c::ipsec.c` (1200 lines in NXP source). No `fci.c::sa_handler` (600 lines). No `cdx.c::cdx_ipsec_*` (800 lines). The kernel's xfrm subsystem already does SA lifecycle management; we provide the device callbacks and the protocol does the rest.

### 5.3 Supported AEAD algorithms v1.0

In priority order, gated by what `caam_qi.ko` already exposes via crypto API:

1. `rfc4106(gcm(aes))` 128-bit and 256-bit — primary target
2. `authenc(hmac(sha256),cbc(aes))` 128/256 — legacy enterprise VPN
3. `rfc7539esp(chacha20,poly1305)` — if mainline `caam_qi` supports it; check `/proc/crypto` at probe

Tunnel mode and transport mode share the same `xdo_dev_state_add` path; the action template differs in whether it strips an outer header.

### 5.4 What we do NOT write

- ESP framing logic (kernel xfrm handles it)
- Anti-replay window management (kernel xfrm handles it)
- IKE/ISAKMP (strongSwan handles it)
- Key derivation (kernel xfrm handles it)
- AEAD descriptor construction (mainline `caamalg_qi.c` handles it)
- Decryption fallback (kernel `esp_input` handles it when offload not present)

We provide: the device callback that translates SA state into 210 + CAAM hardware programming.

---

## 6. The userspace daemon — `askd`

### 6.1 What it is

A small daemon, ~4000 LOC C with sd-event and libmnl, doing exactly two things:

1. Subscribe to kernel events (rtnetlink, conntrack, xfrm_user) and decide whether each event becomes a flow promotion
2. Implement operator-facing CLI and policy configuration

It does not duplicate kernel work. It does not parse routing tables from `/proc`. It does not run a control plane.

### 6.2 Why askd exists at all

The kernel module handles `nf_flow_table` promotions automatically once the operator's nftables ruleset says `flow add @f`. So askd is only needed for:

- **Promotion policy** — decide WHICH conntrack flows are promotion-eligible based on ALG exclusion list, VPP-promote ACLs, etc.
- **Bytes-back keepalive** — refresh conntrack last-used time so software conntrack doesn't expire hardware-active flows
- **Operator CLI** — show flows, show stats, show MURAM, clear flows
- **VPP handoff orchestration** — when a flow matches a VPP-promote ACL, set up memif handoff instead of direct hardware offload
- **Metrics export** — Prometheus exporter on TCP /metrics

Operators who want pure declarative configuration via nftables can run without askd. The kernel module is functional on its own. askd adds: policy, operator UX, and VPP integration.

### 6.3 Architecture

```
askd/
├── meson.build                                 # meson build, not autotools
├── src/
│   ├── main.c                                  # argv, sd_notify ready, signal handlers
│   ├── event_loop.c                            # sd_event main loop
│   ├── genl_client.c                           # libmnl wrapper for ask genl family
│   ├── conntrack.c                             # libnetfilter_conntrack subscription
│   ├── xfrm_events.c                           # xfrm_user RTM_NEWSA subscription
│   ├── rtnl.c                                  # RTM_NEWROUTE / RTM_NEWNEIGH subscription
│   ├── policy.c                                # ALG exclude, VPP-promote ACLs
│   ├── promotion.c                             # decision: hw offload | vpp memif | leave to nft
│   ├── vpp_memif.c                             # libmemif handoff to VPP
│   ├── varlink_api.c                           # systemd Varlink API for ask-cli
│   ├── prometheus.c                            # /metrics HTTP endpoint
│   └── log.c                                   # structured journald output
├── data/
│   ├── askd.service                            # systemd service unit
│   ├── askd.preset                             # systemd preset
│   ├── askd.policy                             # polkit policy for varlink
│   └── ask.conf                                # /etc/ask/ask.conf default
├── tests/
│   ├── test_promotion.c                        # cmocka unit tests
│   ├── test_policy.c
│   └── integration/
│       └── test_e2e.py                         # pytest end-to-end on real hardware
└── README.md
```

Modern choices vs NXP `cmm`:

| `cmm` (legacy) | `askd` (2026) |
|---|---|
| autotools | meson |
| glib event loop | sd-event |
| libcli (1990s Cisco-style) | systemd Varlink for IPC |
| `/proc/net/route` polling | rtnetlink subscription |
| `/var/log/cmm.log` text logging | structured journald with fields |
| Hand-rolled netlink message construction | libmnl with type-safe attribute helpers |
| `fork()`-and-daemonize | sd_notify Type=notify |
| No metrics | Prometheus /metrics exporter |
| No IPC API | Varlink interface for ask-cli + scripts |

### 6.4 Promotion decision logic

```c
/* Pseudocode — actual implementation in promotion.c */
askd_promotion_decide(struct conntrack_event *ev)
{
    /* 1. Filter by protocol */
    if (alg_exclusion_match(ev->proto, ev->src_port, ev->dst_port))
        return PROMOTE_NONE;

    /* 2. Resolve next-hop */
    nh = rtnl_lookup_next_hop(ev->dst_ip);
    if (!nh || !nh->reachable)
        return PROMOTE_RETRY;

    /* 3. Check egress interface offloadability */
    if (!iface_offload_capable(nh->oif))
        return PROMOTE_NONE;

    /* 4. Check VPP-promote ACL */
    if (vpp_promote_acl_match(ev))
        return PROMOTE_VPP_MEMIF;

    /* 5. Default: promote to hardware */
    return PROMOTE_HARDWARE;
}
```

Decisions flow to either `ASK_CMD_FLOW_ADD` over genl (hardware path) or `vpp_memif.c` (VPP path).

### 6.5 systemd integration

```ini
# /lib/systemd/system/askd.service
[Unit]
Description=ASK2 promotion daemon
Documentation=man:askd(8)
After=network-pre.target systemd-modules-load.service
Wants=network-pre.target
ConditionPathExists=/sys/module/ask
ConditionCapability=CAP_NET_ADMIN

[Service]
Type=notify
NotifyAccess=main
ExecStart=/usr/bin/askd
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5
LimitNOFILE=131072

# Sandboxing
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=no
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
RestrictNamespaces=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @resources @mount @debug @cpu-emulation @obsolete @raw-io

[Install]
WantedBy=multi-user.target
```

Compare to `cmm.service`: no `ConditionPathExists=/dev/cdx_ctrl`, no `ExecStartPre` to write sysfs hooks, no LimitMEMLOCK gymnastics, full systemd sandboxing.

### 6.6 ask-cli (operator tool)

A small CLI in Python that talks to askd over Varlink. Replaces `cmmctl` (1500 LOC C).

```
ask-cli flows list
ask-cli flows show <flow-id>
ask-cli stats summary
ask-cli muram
ask-cli events tail
ask-cli policy show
ask-cli policy reload
```

Python because: VyOS already pulls in Python, the CLI is not performance-critical, Varlink has a clean Python client (`varlink` package), and tabular output looks better with `rich`.

### 6.7 No legacy ABI compatibility shim

NXP 1.x had `/dev/cdx_ctrl` ioctls and `libfci.so.1`. Vendor tools linked against these. **We deliberately do not preserve this ABI.**

Reasoning: there are no surviving vendor tools that depend on it outside Mono's own builds. Mono builds the entire stack; they recompile against the new genl interface. Preserving 30+ ioctl numbers and a parallel netlink protocol for hypothetical legacy tools adds ~500 LOC of pure compat shim that nobody uses.

If a deployment surfaces that genuinely needs `cdx_ctrl` compatibility, ship a separate `libask-compat.so.1` that translates the legacy ioctl surface to genl. Optional, out-of-tree, not in v1.0.

---

## 7. The genl_family protocol

### 7.1 Family registration

```c
/* include/uapi/linux/ask/ask.h */
#define ASK_GENL_NAME      "ask"
#define ASK_GENL_VERSION   1

enum ask_genl_mcgrp {
    ASK_MCGRP_EVENTS,       /* lifecycle, MURAM, ucode errors */
    ASK_MCGRP_FLOWS,        /* flow add/del/evict events */
    ASK_MCGRP_SAS,          /* SA add/del/expire events */
};

enum ask_genl_cmd {
    ASK_CMD_UNSPEC,
    /* Query operations */
    ASK_CMD_GET_INFO,           /* version, capabilities, ucode info */
    ASK_CMD_GET_MURAM,          /* MURAM allocation report */
    ASK_CMD_DUMP_FLOWS,         /* dump all installed flows */
    ASK_CMD_GET_FLOW,           /* query single flow by id */
    ASK_CMD_DUMP_SAS,           /* dump all installed SAs */
    /* Control operations (gated by CAP_NET_ADMIN) */
    ASK_CMD_FLUSH_FLOWS,        /* admin: remove all flows */
    ASK_CMD_FLUSH_SAS,
    ASK_CMD_SET_POLICER,        /* exception-rate-limit configuration */
    /* Internal: flow_block_cb and xfrmdev_ops drive flow/SA insert,
     * not direct genl commands. Operators who want raw insert use
     * nft 'flow add' which goes through flow_block_cb. */
    __ASK_CMD_MAX,
};
```

There are NO `ASK_CMD_FLOW_ADD` / `ASK_CMD_SA_ADD` operations in the genl interface. Flow and SA insertion go through `nf_flow_table` + `xfrmdev_ops` respectively, which is what mainline drivers do. Operators talk to mainline subsystems; the kernel routes the work to `ask.ko` via callbacks. This is the inversion that matters: the legacy stack made userspace push flows; the modern stack lets the kernel pull them through standard infrastructure.

### 7.2 Attribute schema

```c
enum ask_genl_attr {
    ASK_ATTR_UNSPEC,
    ASK_ATTR_INFO,                  /* nested ask_info */
    ASK_ATTR_MURAM,                 /* nested ask_muram */
    ASK_ATTR_FLOW,                  /* nested ask_flow */
    ASK_ATTR_SA,                    /* nested ask_sa */
    ASK_ATTR_EVENT,                 /* nested ask_event */
    ASK_ATTR_POLICER,               /* nested ask_policer */
    __ASK_ATTR_MAX,
};

enum ask_flow_attr {
    ASK_FLOW_ATTR_UNSPEC,
    ASK_FLOW_ATTR_ID,               /* u64 */
    ASK_FLOW_ATTR_L3_PROTO,         /* u16: ETH_P_IP|ETH_P_IPV6 */
    ASK_FLOW_ATTR_L4_PROTO,         /* u8 */
    ASK_FLOW_ATTR_SRC_IP,           /* binary, 4 or 16 */
    ASK_FLOW_ATTR_DST_IP,
    ASK_FLOW_ATTR_SPORT,            /* u16 NBO */
    ASK_FLOW_ATTR_DPORT,
    ASK_FLOW_ATTR_IIF,              /* u32 ifindex */
    ASK_FLOW_ATTR_OIF,              /* u32 ifindex */
    ASK_FLOW_ATTR_PACKETS,          /* u64 */
    ASK_FLOW_ATTR_BYTES,            /* u64 */
    ASK_FLOW_ATTR_LAST_SEEN_NS,     /* u64 */
    ASK_FLOW_ATTR_HW_FLOW_ID,       /* u32, opaque */
    __ASK_FLOW_ATTR_MAX,
};
```

All attributes validated through `nla_policy` tables in `ask_genl_attr.c`. Type-checked at parse time. No raw buffer copying from userspace.

### 7.3 Capability gating

Every command op carries `GENL_ADMIN_PERM` (CAP_NET_ADMIN). Query operations are admin-only too — flow tables expose connection metadata that's privacy-relevant.

### 7.4 Generic netlink discoverability

Operators discover the family without hardcoding family IDs:

```sh
genl ctrl-list | grep ask
ynl --family ask --do get-info
```

Modern Linux tooling (`ynl` from kernel/tools/net/ynl) generates per-family Python/C clients from a YAML schema. We ship `ask.yaml` alongside the UAPI header. Future tools auto-generate.

---

## 8. CAAM integration (reuse + 1 small patch)

The in-tree patch in §8.1 is upstream-targeted; it gets a normal upstream review cycle in parallel with the rest of the implementation.

The mainline `caam_qi.ko` already does crypto descriptor construction for all AEAD algorithms we care about. The single missing piece is: descriptors are created assuming kernel-crypto-API callers will use them. For ASK we need FMan to dequeue from CAAM's RX queue without going through kernel crypto API.

### 8.1 The patch `0001-caam-qi-share.patch`

~150 lines against `drivers/crypto/caam/qi.c`, plus a new
upstream-ready header at `include/linux/crypto/caam_qi_share.h`:

```c
/* SPDX-License-Identifier: GPL-2.0 */
/* include/linux/crypto/caam_qi_share.h
 *
 * CAAM/QI external descriptor sharing — for in-kernel consumers that
 * need to share a CAAM AEAD descriptor with a non-crypto-API dequeuer
 * (e.g. FMan 210 ucode on the ASK fast path).
 *
 * See specs/ask2-rewrite-spec.md §8.1.
 */
#ifndef _LINUX_CRYPTO_CAAM_QI_SHARE_H
#define _LINUX_CRYPTO_CAAM_QI_SHARE_H

#include <linux/types.h>

struct caam_drv_ctx;

/**
 * caam_qi_ext_consumer_register - share a CAAM/QI descriptor's request FQ
 *                                  with an external dequeuer (e.g. FMan 210
 *                                  ucode driving an offline port).
 * @ctx:           descriptor context previously created via
 *                 caam_drv_ctx_init() (typically by caamalg_qi.c on behalf
 *                 of an xfrm SA install).
 * @consumer_name: diagnostic string for /proc/crypto and tracepoints,
 *                 e.g. "ask:fman0:op1:spi-0x12345678".
 * @sink_fqid:     FQID that CAAM should enqueue completed frames to.
 *                 Caller-owned (typically a 210-managed FMan offline-port
 *                 RX FQ). REPLACES the response path that
 *                 caam_qi_enqueue() callbacks would have used; while an
 *                 external consumer is registered, in-kernel crypto-API
 *                 callers of this @ctx will not receive completions via
 *                 the normal callback.
 * @caam_req_fqid: [out] FQID of the CAAM request queue that the external
 *                 producer (e.g. FMan 210 ucode) must enqueue encrypted
 *                 ESP frames to. This is @ctx->req_fq->fqid exposed
 *                 read-only to the caller.
 *
 * Single-consumer: returns -EBUSY if @ctx already has an external
 * consumer registered.
 * Refcount: increments @ctx->refcnt on success. The caller must invoke
 * caam_qi_ext_consumer_release() to balance it.
 *
 * Return: 0 on success, -EINVAL on bad arguments, -EBUSY if an external
 * consumer is already registered, -ENOMEM on allocation failure.
 */
int caam_qi_ext_consumer_register(struct caam_drv_ctx *ctx,
                                  const char *consumer_name,
                                  u32 sink_fqid,
                                  u32 *caam_req_fqid);

/**
 * caam_qi_ext_consumer_release - undo caam_qi_ext_consumer_register().
 * @ctx: descriptor context that was previously registered.
 *
 * Restores the default response path so subsequent caam_qi_enqueue()
 * calls on @ctx are dispatched to the normal callback again.
 * Idempotent: a no-op if no external consumer is currently registered
 * on @ctx. RCU-safe: in-flight responses already on the sink FQ are
 * not retroactively redirected.
 * Decrements @ctx->refcnt.
 */
void caam_qi_ext_consumer_release(struct caam_drv_ctx *ctx);

#endif /* _LINUX_CRYPTO_CAAM_QI_SHARE_H */
```

The implementation in `drivers/crypto/caam/qi.c` adds two
EXPORT_SYMBOL_GPL functions plus a small per-`caam_drv_ctx` extension
(an `ext_consumer` pointer guarded by RCU; the dispatcher in
`caam_qi_poll_resp_fq()` / the QMan response callback consults it and
diverts to `sink_fqid` via `qman_enqueue()` when set, otherwise runs
the normal callback path). Net diff in `qi.c`: ~150 LOC.

Patch is upstream-ready. Submit alongside ASK2 v1.0 release.
Precedent: mlx5 does similar descriptor sharing between RDMA and
Ethernet paths via `mlx5_core_modify_qp_state()` + RDMA-CM device
ownership transfer.

### 8.2 Why not reimplement CAAM descriptors

CAAM descriptor construction in mainline `caamalg_qi.c` is ~2000 lines covering every AEAD/cipher/hash combination. Reimplementing it would:

- Duplicate ~2000 lines we don't maintain
- Introduce new bugs in a security-critical path
- Block future CAAM hardware/firmware updates that NXP feeds into the mainline driver
- Have no functional benefit — we get the same descriptors either way

This is the one place where "use the existing GPL code" is unambiguously the right answer. The mainline CAAM driver is well-maintained by NXP themselves.

---

## 9. VPP coexistence

The data-path mechanics here are spec-driven; final tuning of CPU pinning and memif queue depths is a hardware-measurement step.

### 9.1 The model

VyOS 1.5 LTS ships VPP. Mono Gateway has 4 cores. Reasonable split for the hybrid case:

- Cores 0-1: kernel slow path, conntrack, control plane
- Cores 2-3: VPP workers
- FMan + CAAM: silicon, no cores

ASK fast path runs in silicon regardless of VPP. The interaction surface is: how do flows reach VPP when they need it?

### 9.2 Three deployment modes

| Mode | Trigger | When |
|---|---|---|
| **ASK-only** | `set system offload ask enable` | Pure routing, NAT, basic firewall, IPsec |
| **VPP-only** | `set vpp enable; set vpp interface ethN` | Specialised CGNAT/SR appliance with no kernel features |
| **Hybrid** | both enabled | Most flows direct via ASK, specific flows via VPP plugins |

### 9.3 Hybrid mode promotion path

```
flow arrives at eth3
    ├─ 210 ucode hit? → forwarded in silicon, done
    └─ 210 ucode miss → enqueued to A72 slow path
                        └─ conntrack EST event → askd
                                ├─ matches vpp-promote ACL?
                                │   YES → push to VPP memif RX FQ
                                │         VPP processes via its plugin graph
                                │         VPP sends back via memif TX FQ → egress netdev
                                └─ NO  → ask.ko inserts ASK fast-path flow
                                          subsequent packets → 210 silicon, done
```

memif latency on A72 is sub-µs. The promotion cost is constant; high-bandwidth flows pay it once at setup, not per packet.

### 9.4 VyOS CLI for VPP-promote ACL

```
set system offload ask promote vpp acl 100
set policy access-list 100 rule 10 action permit
set policy access-list 100 rule 10 destination address 10.0.0.0/8
```

Flows matching ACL 100 go to VPP instead of direct ASK hardware. Everything else uses ASK.

### 9.5 What we don't do

- No VPP plugin that talks to `ask.ko` directly. VPP runs in its lane, ASK runs in its lane. They communicate through memif (existing VPP infrastructure) and rtnetlink (existing kernel infrastructure). No new coupling.
- No promotion of hardware-offloaded flows into VPP mid-flow. Once a flow is in 210 silicon, it stays there until eviction. Operators wanting VPP processing classify flows up-front via ACL.

---

## 10. Build pipeline and VyOS integration

### 10.1 Repository layout

```
vyos-ls1046a-build/                     # existing single-repo
├── kernel/
│   ├── common/                         # shared with default flavor
│   └── flavors/ask/
│       ├── patches/
│       │   ├── 0001-caam-qi-share-descriptors.patch
│       │   ├── 0002-dpaa-eth-flow-block.patch
│       │   └── 0003-fman-host-command-api.patch
│       └── oot/
│           ├── ask/                    # the kernel module source
│           └── ask-vyos.conf           # /etc/modules-load.d
├── userspace/
│   ├── askd/                           # userspace daemon source
│   └── ask-cli/                        # python CLI source
├── vyos-1x/                            # CLI integration submodule
│   ├── interface-definitions/
│   │   ├── system-offload-ask.xml.in
│   │   └── interfaces-ethernet-offload.xml.in
│   ├── src/conf_mode/
│   │   └── system_offload_ask.py
│   └── src/op_mode/
│       └── offload_ask.py
└── bin/
    ├── ci-stage-kernel-ask.sh
    ├── ci-build-ask-module.sh
    ├── ci-build-askd.sh
    ├── ci-build-ask-cli.sh
    ├── ci-build-iso-ask.sh
    └── ci-verify-ask-iso.sh
```

### 10.2 Kernel build flow

1. Stage kernel sources at linux-6.18.x
2. Apply three small in-tree patches (~650 lines total)
3. Build kernel with `CONFIG_FSL_DPAA=y CONFIG_NF_FLOW_TABLE=y CONFIG_XFRM_OFFLOAD=y CONFIG_CRYPTO_DEV_FSL_CAAM_QI=y`
4. Build `ask.ko` out-of-tree against the staged kernel
5. Sign module with in-tree signing key (`MODULE_SIG_FORCE=y`)
6. Package as `.deb` for VyOS image inclusion

### 10.3 Userspace build flow

1. Build `askd` via meson with system libmnl, libnetfilter_conntrack, libsystemd, libmemif
2. Build `ask-cli` Python wheel
3. Package both as `.deb`s
4. Install during ISO chroot phase via `data/hooks/97-ask-userspace.chroot`

### 10.4 VyOS CLI surface

```sh
# Top-level enable
set system offload ask

# MURAM budget hint (advisory; kernel reports actual)
set system offload ask max-flows 750

# Behaviour when MURAM exhausted
set system offload ask overflow software   # fall back to nf_flow_table sw path
set system offload ask overflow drop       # drop excess flows

# ALG exclusion list
set system offload ask exclude-alg ftp sip pptp

# Per-interface enable
set interfaces ethernet eth0 offload ask
set interfaces ethernet eth3 offload ask

# Optional: VPP promotion ACL
set system offload ask promote vpp acl 100
```

### 10.5 op_mode commands

```sh
show offload ask flows
show offload ask flows table inet
show offload ask flows interface eth3
show offload ask stats
show offload ask stats interface eth0
show offload ask muram
show offload ask events recent
show offload ask sas
show offload ask info

clear offload ask flows
clear offload ask flows interface eth3
clear offload ask sas

monitor offload ask events
monitor offload ask flows
```

All `op_mode` commands talk to askd via Varlink, which queries the kernel via genl.

---

## 11. Performance targets and acceptance gates

### 11.1 Hard performance gates for v1.0 GA

Measured on Mono Gateway DK with Spirent or Keysight CyPerf, both directions, 4 A72 cores:

| Test | GO | NO-GO |
|---|---|---|
| IPv4 forwarding, 512 B, bidirectional | ≥14 Gbps | <10 Gbps |
| IPv4 forwarding, 1518 B, bidirectional | ≥18 Gbps | <14 Gbps |
| IPv4 forwarding, 64 B, bidirectional | ≥8 Gbps | <5 Gbps |
| IPv4 NAT (SNAT+DNAT), 1024 B | ≥18 Gbps | <14 Gbps |
| AES-GCM-128 IPsec tunnel, 1024 B | ≥3 Gbps | <2 Gbps |
| L2 bridge offload, 1518 B | ≥18 Gbps | <14 Gbps |
| Real HTTP through-traffic (Geerling/STH test) | ≥1 Mpps | <700 Kpps |
| CPU utilisation at 17 Gbps forwarding | <20% | >35% |

### 11.2 Functional gates

- nft `flow add @f` over 5-tuple → flow appears in `ask-cli flows list`
- `ip xfrm state add ... offload packet dev eth3` → SA appears in `ask-cli sas`
- Bridge offload through `nf_flow_table` works on a 2-port bridge
- Software flowtable fallback engages when MURAM full (controlled MURAM-fill test)
- Conntrack timeout doesn't expire hardware-active flows (bytes-back working)
- VyOS CLI: every documented command produces a working config
- Module reload (`rmmod ask; modprobe ask`) doesn't crash the kernel
- Reboot persistence: VyOS config restored on boot → flows offload correctly

### 11.3 Quality gates

- kunit test coverage ≥80% for `ask_flow.c`, `ask_hostcmd.c`, `ask_genl_attr.c`
- askd tests via cmocka, coverage ≥70%
- Integration tests pass on real hardware via the CI pipeline (Mono provides a test board)
- Sparse and `make C=2` clean
- `checkpatch.pl --strict` clean
- `MODULE_SIG_FORCE=y` and module signs against ASK signing key
- KMSAN clean on x86_64 simulator (where applicable to portable code paths)

---

## 12. The 210 microcode protocol (reference)

This section documents the wire-level protocol that the 210 microcode expects. **It is extracted from the GPL-2.0 source published at `github.com/we-are-mono/ASK` and treated as documentation of silicon behaviour, not as code to copy.** The implementation in `ask_hostcmd.c` derives only from these documented facts, not from the source file structure or function names.

### 12.1 Host command framing

All commands are sent through the FMan I/O block via the `fmd_host_cmd()` API exposed by patch `0003-fman-host-command-api.patch`. Wire format:

```
+----+----+--------+--------+-----------+
| op | rs | len_hi | len_lo | payload   |
+----+----+--------+--------+-----------+
  u8   u8    u8       u8       len bytes
```

- `op` (1 byte) — opcode (see Section 12.2)
- `rs` (1 byte) — reserved, must be 0
- `len` (2 bytes, big-endian) — payload length, max 1020 bytes
- `payload` — opcode-specific, byte-packed, big-endian for multi-byte fields

Response format identical, with `op` reflected and `payload` containing the response body.

### 12.2 Opcode space (verified against `cdx-5.03.1/cdx_cmd_codes.h` and `fci-9.00.12/include/fpp_*.h` in we-are-mono/ASK)

**System opcodes:**

| Opcode | Name | Purpose |
|---|---|---|
| 0x01 | `OP_GET_UCODE_VERSION` | Returns ucode family + major/minor/patch. Probed at module init. Response: 6 bytes (family u16 BE, major u8, minor u8, patch u16 BE). Family = 0x0210 for ASK. |
| 0x02 | `OP_GET_CAPABILITIES` | Bitmap of supported features (NAT, IPv6, ESP, multicast, etc). Response: 16 bytes. |
| 0x03 | `OP_GET_MURAM_INFO` | MURAM total, free, allocated-to-tables. Response: 12 bytes. |
| 0x04 | `OP_RESET_TABLES` | Flush all flow and SA tables. No payload. |

**Flow table opcodes:**

| Opcode | Name | Purpose |
|---|---|---|
| 0x10 | `OP_FLOW_INSERT_V4_TCP` | Insert IPv4 TCP 5-tuple flow with action template. Payload: 64 bytes (key 24 + action 40). |
| 0x11 | `OP_FLOW_INSERT_V4_UDP` | Same as TCP, different table partition |
| 0x12 | `OP_FLOW_INSERT_V6_TCP` | IPv6 TCP. Payload: 96 bytes (key 48 + action 48). |
| 0x13 | `OP_FLOW_INSERT_V6_UDP` | IPv6 UDP. |
| 0x14 | `OP_FLOW_INSERT_V4_MCAST` | IPv4 multicast 3-tuple. |
| 0x15 | `OP_FLOW_INSERT_V6_MCAST` | IPv6 multicast 3-tuple. |
| 0x16 | `OP_FLOW_INSERT_BRIDGE` | L2 bridge flow keyed on (bridge ifindex, dst MAC). |
| 0x18 | `OP_FLOW_REMOVE` | Remove flow by hw_flow_id. Payload: 4 bytes. |
| 0x19 | `OP_FLOW_QUERY_STATS` | Get bytes/packets for flow. Payload: 4 bytes. Response: 16 bytes. |
| 0x1A | `OP_FLOW_DUMP_STATS` | Bulk dump byte/packet deltas since last dump. Used by 1Hz timer for `nf_flow_table` stats. |

**IPsec SA opcodes:**

| Opcode | Name | Purpose |
|---|---|---|
| 0x20 | `OP_SA_INSERT_V4_ESP` | Insert IPv4 ESP SA. Payload: 80 bytes (SPI 4 + dst 4 + caam_rx_fqid 4 + op_inject_fqid 4 + reserved 4 + key material 64). |
| 0x21 | `OP_SA_INSERT_V6_ESP` | IPv6 ESP SA. Payload: 92 bytes. |
| 0x28 | `OP_SA_REMOVE` | Remove by hw_sa_id. Payload: 4 bytes. |
| 0x29 | `OP_SA_QUERY_STATS` | Get bytes/packets for SA. |

**Offline port opcodes:**

| Opcode | Name | Purpose |
|---|---|---|
| 0x30 | `OP_OP_CONFIGURE` | Configure offline port: source FQ, action template (re-classify, drop, forward-to-port). Payload: 32 bytes. |
| 0x31 | `OP_OP_FLUSH` | Flush offline port queue. |

**Policer opcodes:**

| Opcode | Name | Purpose |
|---|---|---|
| 0x40 | `OP_POLICER_SET_EXCEPTION_RATE` | Set per-port exception-rate-limit (slow-path lift bytes/sec). Payload: 8 bytes (port_id u8 + rate u32 BE + burst u32 BE). |

**Event opcodes (silicon → host):**

These arrive asynchronously via a separate event channel (FMan IRQ → ask.ko event handler). Not host-initiated.

| Event | Name | Body |
|---|---|---|
| 0x80 | `EV_FLOW_EVICTED` | 210 evicted a flow under pressure. 8 bytes: hw_flow_id + reason. |
| 0x81 | `EV_TABLE_FULL` | A flow table is full. 4 bytes: table_id. |
| 0x82 | `EV_UCODE_ERROR` | ucode hit an unrecoverable error. 4 bytes: error_code. |
| 0x83 | `EV_SA_EXPIRED` | SA hit a lifetime limit. 8 bytes: hw_sa_id + reason. |

### 12.3 Flow key encoding (binary, big-endian)

IPv4 TCP/UDP key (24 bytes):
```
+--------+--------+--------+--------+
| src_ip (4 bytes)                  |
+--------+--------+--------+--------+
| dst_ip (4 bytes)                  |
+--------+--------+--------+--------+
| sport  | dport  | iif (4)         |
+--------+--------+--------+--------+
| vlan_id (2) | reserved (6)        |
+--------+--------+--------+--------+
```

IPv6 TCP/UDP key (48 bytes):
```
+--------+...+--------+
| src_ip (16 bytes)   |
+--------+...+--------+
| dst_ip (16 bytes)   |
+--------+...+--------+
| sport  | dport       |
+--------+--------+----+
| iif (4)             |
+--------+--------+----+
| vlan_id (2) | rsv (10) |
+--------+...+--------+
```

### 12.4 Flow action encoding (40 bytes for v4, 48 for v6)

```
+--------+--------+--------+--------+
| action_flags (4)                  |
| (TTL_DEC | NAT_SRC | NAT_DST |    |
|  PAT | VLAN_PUSH | VLAN_POP |     |
|  TO_CAAM | TO_OP)                 |
+--------+--------+--------+--------+
| oif (4) - egress port id          |
+--------+--------+--------+--------+
| rewrite_src_mac (6)               |
+--------+--------+--------+--------+
| rewrite_dst_mac (6)               |
+--------+--------+--------+--------+
| rewrite_src_ip (4 or 16)          |
+--------+--------+--------+--------+
| rewrite_dst_ip (4 or 16)          |
+--------+--------+--------+--------+
| rewrite_sport (2)                 |
+--------+--------+--------+--------+
| rewrite_dport (2)                 |
+--------+--------+--------+--------+
| vlan_id (2) | reserved (2)        |
+--------+--------+--------+--------+
```

### 12.5 Concrete example — IPv4 TCP NAT flow insert

Goal: forward TCP flow `192.168.1.5:5000 → 8.8.8.8:443` egress on eth3 (port 6), rewriting src to `203.0.113.10:42000` (SNAT+PAT), dst MAC `aa:bb:cc:dd:ee:ff`, src MAC `00:11:22:33:44:55`.

Wire bytes (hex):
```
10 00 00 40                                       # opcode 0x10, reserved 0, len 0x0040 (64)
c0 a8 01 05                                       # src_ip 192.168.1.5
08 08 08 08                                       # dst_ip 8.8.8.8
13 88 01 bb                                       # sport 5000, dport 443
00 00 00 01                                       # iif (eth0 = ifindex 1, placeholder)
00 00 00 00 00 00                                 # vlan + reserved

00 00 00 07                                       # action_flags: TTL_DEC | NAT_SRC | PAT_SRC
00 00 00 06                                       # oif = port 6 (eth3)
00 11 22 33 44 55                                 # rewrite src MAC
aa bb cc dd ee ff                                 # rewrite dst MAC
cb 00 71 0a                                       # rewrite src IP 203.0.113.10
00 00 00 00                                       # no dst IP rewrite (zero = no change)
a4 10                                             # rewrite sport 42000
00 00                                             # no dport rewrite
00 00 00 00                                       # vlan, reserved
```

Response:
```
10 00 00 04                                       # opcode echo, len 4
00 00 12 34                                       # hw_flow_id = 0x1234
```

### 12.6 The `ask_hostcmd.c` interface to the rest of the module

```c
/* ask_hostcmd.c — clean abstraction over wire protocol */
struct ask_hw_flow_key_v4 {
    __be32 src_ip, dst_ip;
    __be16 sport, dport;
    u32    iif;
    u16    vlan_id;
};

struct ask_hw_action {
    u32    flags;
    u32    oif;
    u8     rewrite_src_mac[6];
    u8     rewrite_dst_mac[6];
    __be32 rewrite_src_ip_v4;
    __be32 rewrite_dst_ip_v4;
    __be16 rewrite_sport;
    __be16 rewrite_dport;
    u16    vlan_id;
};

int ask_hw_flow_insert_v4_tcp(struct fman *fman,
                               const struct ask_hw_flow_key_v4 *key,
                               const struct ask_hw_action *action,
                               u32 *out_hw_flow_id);

int ask_hw_flow_remove(struct fman *fman, u32 hw_flow_id);

int ask_hw_flow_query_stats(struct fman *fman, u32 hw_flow_id,
                             u64 *bytes, u64 *packets);

int ask_hw_sa_insert_v4_esp(struct fman *fman,
                             const struct ask_hw_sa_v4 *sa,
                             u32 *out_hw_sa_id);

/* ... similar for v6, mcast, bridge, op, policer ... */
```

The rest of `ask.ko` calls these typed functions. The wire format never leaks out of `ask_hostcmd.c`. Tracepoints fire on every call for observability.

### 12.7 What's NOT documented and must be probed

Most of the opcode space is documented in the GPL source. Three things require live-hardware confirmation:

1. **Exact MURAM partition behaviour** — how 210 ucode allocates within MURAM when multiple flow types coexist. Worth measuring with `OP_GET_MURAM_INFO` under increasing load.
2. **Event channel binding** — the GPL source binds events to a specific FMan IRQ; need to confirm IRQ number and event-queue layout against the Mono hardware DTB.
3. **Eviction policy tunables** — there may be hidden opcodes (0x90-0x9F space?) that tune LRU vs LFU eviction. Worth scanning the opcode space for response patterns.

Estimated probe time: **1-2 weeks**, not 8-10 weeks. The protocol is documented; we're confirming edge cases.

### 12.8 Confirmed hardware behaviour (PR13 findings, 2026-05-13)

PR13 (M2.4 — first hardware-validated read against the live Mono Gateway DK) probed the §12.7 unknowns and surfaced a fundamental mismatch between the §12.1–§12.6 host-command protocol and the microcode actually loaded on the silicon.

**Finding 1 — the loaded microcode is stock NXP QEF 210.10.1, not an opcode-dispatch microcode.**
U-Boot loads the microcode blob from SPI flash partition `mtd3` (`fman-ucode`, 1 MiB) into FMan IRAM at boot. The DT exposes the same blob via `/soc/fman@1a00000/fman-firmware/fsl,firmware` (reachable from kernel space via `of_find_compatible_node("fsl,fman-firmware")` + `of_get_property("fsl,firmware")`). The blob carries the magic `'Q' 'E' 'F' 0x01` at offset 4, followed by a 64-byte ASCII description at offset 8 reading `"Microcode version 210.10.1 for LS1043 r1.0"`. This is the upstream NXP QorIQ Engine Firmware that mainline `drivers/net/ethernet/freescale/fman/fman.c` and the in-tree FMan PCD API are designed to drive — it implements the parser, classifier, policer, and KeyGen entirely via MURAM-resident config tables, **not** via a CPU-side host-command doorbell.

**Finding 2 — the §12 host-command opcode dispatcher does not exist on this microcode.**
The FPM host-command region (`fmfp_cev[0..3]` at FMan base + 0xC30E0, mask `fmfp_cee[0..3]` at +0xC3040, response IRQ bits `INTR_EN_REV0..3 = 0x8000/0x4000/0x2000/0x1000` in `fm_npi` at +0xC30D4) is physically present and writeable, but stock 210.x QEF microcode does not poll the CEV doorbell or raise REV events in response. Confirmed indirectly: mainline `fman_irq()` in `drivers/net/ethernet/freescale/fman/fman.c` never dispatches the REV0..3 bits, and `fman_register_intr(fman, FMAN_EV_FMAN_CTRL_0..3, ...)` is `EXPORT_SYMBOL`'d but has zero in-tree callers. There is no upstream consumer of the CPU↔ucode message-passing channel because no upstream microcode implements the receive side. The §12.2 opcode map (`OP_GET_UCODE_VERSION=0x01`, `OP_FLOW_INSERT_V4_TCP=0x10`, …) was extracted from `cdx-5.03.1`/`fci-9.00.12` source headers in the legacy `we-are-mono/ASK` tree, where those opcodes were dispatched by a **custom NXP/proprietary microcode binary** that shipped with that vendor stack — not by the QEF blob in mainline U-Boot or in our SPI flash.

**Finding 3 — `0003-fman-host-command-api.patch` (landed in PR12) is correct as-is and stays.**
The kernel-side transport layer (`fmd_host_cmd_ring_doorbell`, `fmd_host_cmd_arm_irq`) is sound. It correctly returns `-ENXIO` from `fmd_host_cmd_send()` because the doorbell is genuinely unanswered for the running microcode. The patch is preserved as future infrastructure for a hypothetical custom ASK2 microcode (or for a future revision of QEF that adds opcode dispatch); no rework is required if and when such a microcode appears. PR12 was not wasted work — it is the architecturally correct wire layer; only the §12.2 opcode contract above it is hypothetical.

**Finding 4 — §12.7 question 2 (event-channel binding) is partially answered, questions 1 and 3 are moot.**
SPI 44 (Linux IRQ 59) is the FMan event IRQ on LS1046A; SPI 45 is the FMan err IRQ (shared with `bman-err`/`qman-err`). The CEV/REV register topology is documented above. However, §12.7 questions 1 (MURAM partition behaviour under `OP_GET_MURAM_INFO`) and 3 (eviction-policy tunables in opcode space 0x90–0x9F) are not answerable because the opcodes themselves are not implemented — there is nothing to scan. Hardware-side MURAM partitioning is observable instead via the in-tree `fman_muram` allocator API and via the parser/classifier table layouts programmed through the FMan PCD interface.

**Finding 5 — `STRICT_DEVMEM=y` blocks `/dev/mem` peeks on the FLAVOR=default 6.18 kernel.**
Register-level probing of FMan FPM from userspace requires either a debug kernel build with `STRICT_DEVMEM=n` or a kernel-side ioremap (the path PR13 took for the DT blob read). This is intentional and stays.

**Mechanism switch for ASK2 v1.0 (binding decision).**
ASK2 v1.0 will program the FMan PCD (parser/classifier/KeyGen/policer) via the in-tree mainline FMan API in `drivers/net/ethernet/freescale/fman/fman_keygen.c` (and siblings `fman_port.c`, `fman_dtsec.c`, `fman_memac.c`), driven from `ask.ko` and from `askd` userspace. The §12.1–§12.6 host-command protocol is **deferred indefinitely** — it remains in this spec as a reference for what a future custom-microcode path would look like, and PR12's wire layer is preserved against that future. PR14+ (§5.5 onwards) is re-scoped accordingly: the consumer of `ask_hostcmd.c` becomes the FMan PCD table-programming pathway, not opcode-dispatch insertion. The `ask_hw_ucode_get_version()` helper added in PR13 (sourced from the QEF blob in DT, not from `OP_GET_UCODE_VERSION`) is the canonical and the only required version-read mechanism.

**Verification** — `ask.ko` loaded on the live Mono Gateway DK with kernel 6.18.28, `dmesg | grep '^ask: hw'` shows `ask: hw: FMan microcode 210.10.1 ("Microcode version 210.10.1 for LS1043 r1.0")`; the `ASK_INFO` genl reply carries `family=210, major=10, minor=1, patch=0` matching the DT blob byte-for-byte.

### 12.9 The in-tree FMan PCD does not exist — Option C-modernize decision (PR14-prep, 2026-05-14)

§12.8 above bound ASK2 v1.0 to "the in-tree mainline FMan API in `drivers/net/ethernet/freescale/fman/fman_keygen.c` (and siblings `fman_port.c`, `fman_dtsec.c`, `fman_memac.c`)". Inspection of the actual linux-6.18.28 source tree on 2026-05-14 proves that **the "siblings" required for ASK2 (classifier, policer, match-vector schemes, header manipulation) do not exist in mainline**. §12.8 made a hardware-truth decision ("QEF doesn't dispatch opcodes") but never checked the code-truth ("mainline doesn't expose PCD").

**Evidence (linux-6.18.28):**

- `ls drivers/net/ethernet/freescale/fman/` → `fman.c`, `fman_dtsec.c`, `fman_memac.c`, `fman_tgec.c`, `fman_keygen.c`, `fman_muram.c`, `fman_port.c`, `fman_sp.c`, `fman_host_cmd.c` (from PR12). **No `fman_cc.c`, no `fman_plcr.c`, no `fman_pcd.c`, no `fman_manip.c`.**
- `grep -nE 'classif|coarse_class|FMan_CC|fman_cc' fman.c` → zero hits.
- `fman_keygen.c` exports two symbols: `keygen_init()` (one-shot per FMan) and `keygen_port_hashing_init()` (RSS hashing). Both `EXPORT_SYMBOL` (old, not `_GPL`).
- `keygen_port_hashing_init()` explicitly sets `scheme->match_vector = 0` — meaning no header inspection, no exact-match lookup, just a hash bucket index modulo `hash_size`. This is **RSS load-balancing only**, not classifier programming.
- `fman.c` mentions "policer" only as the FPM clock-divider threshold register `plcr_disp_tsh` — no policer-profile programming surface.
- The only callers in the whole kernel tree: `fman_init → keygen_init` (once at probe) and `fman_port_init → keygen_port_hashing_init` (per port). `dpaa_eth.c` uses this only for `NETIF_F_RXHASH` via `ethtool -K rxhash on/off`.

**Cost survey of the deleted SDK PCD path (2026-05-14, archived `mihakralj/kernel-ls1046a-build` repo):**

| File | LOC (.c) | Purpose |
|---|---|---|
| `fm_cc.c` | ~7,500 | Coarse Classifier — 5-tuple match trees |
| `fm_manip.c` | ~6,000 | Header manipulation — NAT rewrite, checksum, VLAN, TTL |
| `fm_kg.c` | ~3,500 | KeyGen scheme programming (match_vector ≠ 0) |
| `fm_pcd.c` | ~2,500 | PCD top-level orchestration |
| `fm_plcr.c` | ~1,900 | Policer profiles |
| `fm_ehash.c` | ~1,700 | Exact-match hash tables |
| `fm_replic.c` | ~900 | Frame replication (multicast) |
| `fman_kg.c` | ~600 | Low-level KeyGen register access |
| `fm_prs.c` | ~400 | Parser configuration |
| `fman_prs.c` | ~100 | Low-level parser registers |
| Headers (10×) | ~6,000 | |
| **SDK total** | **~30,000** | Vendor C, dual-licensed BSD-3-or-GPL-2.0 |

**Three options were on the table:**

- **C-forward-port** — pull SDK PCD code from the archive, modernize for 6.18 APIs, ship as a 4th in-tree patch. Cost: ~15,000–30,000 LOC kept, plus per-file license audit, plus removal of the SDK's `fsl-ncsw` OS shim, `TRACE_RTOS` macros, `handle_t` opaque ABI, AMP IPC layer (`fm_pcd_ipc.h`), and the nested `Peripherals/FM/` directory structure. Architecturally tied to a 2015-era SDK model.
- **D** — software fast-path only via `nf_flow_table` sw mode + PR11's `flow_block_cb` without hardware backing. ~500 LOC, ~1–2 Gbps cap. Misses §11.1 perf gates by an order of magnitude.
- **E** — cancel ASK2 v1.0 on this SoC. Zero LOC. Defeats the project.

**Decision (2026-05-14, revised in v1.1): Option C-modernize.** Write a new FMan PCD subsystem targeting ~7800 LOC of modern kernel C. The deleted NXP SDK and the `we-are-mono/ASK` legacy stack are both GPL — they remain available as silicon-behaviour references for byte layouts, register-write ordering, and microcode-handshake sequences. **The SDK's *architecture* is what we reject** (per §13.1 below): `handle_t` opaque ABI, `fsl-ncsw` OS shim, AMP multi-OS IPC, `TRACE_RTOS`, nested `Peripherals/FM/Pcd/` layout, 16-flavour next-engine struct hierarchy. The LS1046A Reference Manual chapter 8 remains authoritative when SDK code is ambiguous or contradicts itself. Per-file copyright headers will cite GPL-2.0-only (matching mainline `fman.c`) since both source pools are GPL-compatible. The full module decomposition is **§13 below**.

---

## 13. The FMan PCD subsystem — `0004-fman-pcd-subsystem.patch`

This section specifies a new in-tree kernel patch series that resurrects the FMan Parse / Classify / Distribute (PCD) hardware-programming layer that mainline 6.18 is missing. It is the largest single block of new C in ASK2 v1.0 (~7800 LOC, larger than `ask.ko` itself) but the smallest path to delivering the spec's perf gates. **Source provenance (v1.1):** the LS1046A Reference Manual chapter 8 (RM §8.7–8.10) is the authoritative reference for byte-perfect MURAM layouts and register semantics. The deleted NXP SDK PCD tree (preserved at `mihakralj/kernel-ls1046a-build@464df181`, dual-licensed BSD-3-or-GPL-2.0) and the `we-are-mono/ASK` legacy stack (GPL) are **usable as silicon-behaviour references** for layout disambiguation, register-write ordering, microcode handshakes, and ucode-version quirks. What we explicitly do **not** carry forward is the SDK's *architecture*: §13.1 below enumerates the patterns rejected. Every non-trivial function carries a comment citing its primary RM section; where SDK or `we-are-mono` code was consulted for disambiguation, the comment names the source file (e.g. `/* RM §8.7.4.2 + cross-ref SDK fm_cc.c MatchTableTryLockAcquire() for the polling-loop semantics */`).

### 13.1 Architectural principles

The legacy NXP SDK PCD code was a 25,000 LOC vendor port of a multi-OS abstraction layer ("ncsw" — Network Co-processor Subsystem Wrapper) designed to run on Linux, VxWorks, AMP-mode bare-metal, and FreeBSD from a single source. The ASK2 PCD subsystem keeps the silicon contract (register layouts, MURAM table formats, KeyGen scheme bit fields, classifier-node walk semantics) and discards everything else. Specifically:

| SDK pattern | ASK2 replacement | LOC saved |
|---|---|---|
| `handle_t` opaque-pointer ABI throughout | Typed `struct fman_pcd_*` pointers | ~800 |
| `fsl-ncsw` OS-shim layer (XX_Malloc, XX_Free, IO_MEMORY_MAP_DECLARE, mutex/spinlock wrappers) | Direct kernel primitives (`kmalloc`/`devm_kzalloc`, `ioremap`/`readl`/`writel`, `mutex`, `spinlock`, `rcu`) | ~2,500 |
| `TRACE_RTOS` macros | Kernel tracepoints (`include/trace/events/fman_pcd.h`) | ~400 |
| AMP multi-OS IPC layer (`fm_pcd_ipc.h`, ~1900 LOC) | Deleted entirely — Linux-only, no remote OS to coordinate with | ~1,900 |
| SDK exact-match hash (`fm_ehash.c`, ~1700 LOC) | Kernel `rhashtable` reused from `ask_flow.c` | ~1,700 |
| SDK `crc64.h` inline tables | Kernel `<linux/crc64.h>` | ~400 |
| `fm_cc_dbg.h` debug-only macros (~700 LOC) | Kernel tracepoints + `dynamic_debug` | ~700 |
| Per-platform `dflags.h` matrix (8 SoCs) | Single `fmanv3_dflags.h` for LS1043/LS1046 | ~200 |
| 16 separate flow-table types in userspace contract | Single `struct fman_pcd_action` discriminator | ~600 |
| Nested `Peripherals/FM/Pcd/` directory layout | Flat in `drivers/net/ethernet/freescale/fman/` | ~50 (build-system only) |
| **Total SDK savings** | | **~9,250 LOC** |

Net target: **~7,800 LOC** of new kernel C (≈ 1/4 the SDK's volume, ≈ 1/2 the planned ASK2 kernel module size).

### 13.2 Module decomposition

Seven new `.c` files plus one public header. All flat in `drivers/net/ethernet/freescale/fman/` (mainline preferred layout). One new UAPI-adjacent header in `include/linux/fsl/`. No new directories.

```
drivers/net/ethernet/freescale/fman/
├── fman.c                       (existing — no changes)
├── fman_keygen.c                (existing — extended with non-static helpers; see §13.3)
├── fman_port.c                  (existing — extended with PCD attach hook; see §13.4)
├── fman_pcd.c            NEW    ~800 LOC   Top-level orchestration
├── fman_pcd_kg.c         NEW    ~1500 LOC  KeyGen schemes (match_vector ≠ 0)
├── fman_pcd_cc.c         NEW    ~2500 LOC  Coarse Classifier match trees
├── fman_pcd_manip.c      NEW    ~1200 LOC  Header manipulation (NAT rewrite, checksum, VLAN, TTL)
├── fman_pcd_plcr.c       NEW    ~800 LOC   Policer profiles (rate, burst, color)
├── fman_pcd_prs.c        NEW    ~400 LOC   Parser configuration (HXS — header examination sequences)
└── fman_pcd_replic.c     NEW    ~600 LOC   Frame replication for multicast egress

include/linux/fsl/
└── fman_pcd.h            NEW    ~600 LOC   Public API: typed handles, action structs,
                                            register-bit constants exposed to ask.ko
```

**Net: 7,800 LOC of new C across 7 .c files + 1 header.** The 7-file split mirrors the silicon's natural functional boundaries (KeyGen / Classifier / Policer / Parser / Manipulator / Replicator / orchestration) and matches the RM §8.7–8.10 section structure.

### 13.3 Per-module responsibility

#### `fman_pcd.c` — orchestration (~800 LOC)

Top-level entry points for `ask.ko`:

```c
struct fman_pcd *fman_pcd_init(struct fman *fman);
void fman_pcd_release(struct fman_pcd *pcd);
struct fman_pcd_muram_budget fman_pcd_get_muram_budget(struct fman_pcd *pcd);
```

Owns: the per-FMan MURAM partition between KG schemes, CC trees, policer profiles, and PCD-internal scratch. Implements MURAM tracking via the existing in-tree `fman_muram` allocator (no new allocator). Wires the per-FMan PCD state into the existing `struct fman` via a single new accessor.

EXPORT_SYMBOL_GPL: 6 functions consumed by `ask.ko`.

#### `fman_pcd_kg.c` — KeyGen schemes with match_vector ≠ 0 (~1500 LOC)

Programs the 32 hardware KeyGen schemes for **exact-match flow lookup** (not RSS hashing — that's the existing `keygen_port_hashing_init`).

Public API:

```c
struct fman_pcd_kg_scheme;

struct fman_pcd_kg_scheme *
fman_pcd_kg_scheme_create(struct fman_pcd *pcd,
                          const struct fman_pcd_kg_extract *extract,
                          u32 base_fqid, u32 num_of_fqs);

int fman_pcd_kg_scheme_bind_port(struct fman_pcd_kg_scheme *scheme,
                                  u8 hw_port_id);

int fman_pcd_kg_scheme_attach_cc(struct fman_pcd_kg_scheme *scheme,
                                  struct fman_pcd_cc_tree *tree);

void fman_pcd_kg_scheme_destroy(struct fman_pcd_kg_scheme *scheme);
```

Extends `fman_keygen.c` by demoting two file-static helpers (`keygen_scheme_setup`, `keygen_bind_port_to_schemes`) to non-static and EXPORT_SYMBOL_GPL — same pattern PR10 used for `caam_qi`. ~30 LOC change to `fman_keygen.c`, the rest is new.

#### `fman_pcd_cc.c` — Coarse Classifier match trees (~2500 LOC)

The exact-match table layer. KG scheme extracts the key; CC walks a match tree and selects an action.

```c
struct fman_pcd_cc_tree;
struct fman_pcd_cc_node;

struct fman_pcd_cc_tree *
fman_pcd_cc_tree_create(struct fman_pcd *pcd, u8 num_of_groups);

struct fman_pcd_cc_node *
fman_pcd_cc_node_create(struct fman_pcd_cc_tree *tree,
                        const struct fman_pcd_cc_extract *extract,
                        const struct fman_pcd_cc_key_table *keys);

int fman_pcd_cc_node_modify_next_action(struct fman_pcd_cc_node *node,
                                         u16 key_index,
                                         const struct fman_pcd_action *action);

void fman_pcd_cc_node_destroy(struct fman_pcd_cc_node *node);
void fman_pcd_cc_tree_destroy(struct fman_pcd_cc_tree *tree);
```

The `struct fman_pcd_action` discriminator carries all action types (drop, forward-to-FQ, forward-to-CAAM, replicate, manipulate-and-forward) in a single typed union — replacing the SDK's 16 separate `t_FmPcdCcNextEngineParams` flavors.

#### `fman_pcd_manip.c` — Header manipulation (~1200 LOC)

The NAT engine. Programs the in-silicon header rewriters: SNAT/DNAT/PAT (IPv4 + IPv6), VLAN push/pop, TTL decrement, IPv4/UDP/TCP checksum recomputation.

```c
struct fman_pcd_manip;

struct fman_pcd_manip *
fman_pcd_manip_create(struct fman_pcd *pcd,
                      const struct fman_pcd_manip_params *params);

void fman_pcd_manip_destroy(struct fman_pcd_manip *manip);
```

`fman_pcd_manip_params` is a tagged-union: `MANIP_NAT_V4`, `MANIP_NAT_V6`, `MANIP_VLAN_PUSH`, `MANIP_VLAN_POP`, `MANIP_TTL_DEC`. Each carries its required fields and nothing more.

#### `fman_pcd_plcr.c` — Policer profiles (~800 LOC)

Two-rate three-color (RFC 4115) policer profiles for exception-rate-limiting and DDOS protection.

```c
struct fman_pcd_plcr_profile;

struct fman_pcd_plcr_profile *
fman_pcd_plcr_profile_create(struct fman_pcd *pcd,
                             const struct fman_pcd_plcr_params *params);

int fman_pcd_plcr_profile_set_rates(struct fman_pcd_plcr_profile *prof,
                                     u32 cir_bps, u32 cbs_bytes,
                                     u32 eir_bps, u32 ebs_bytes);

void fman_pcd_plcr_profile_destroy(struct fman_pcd_plcr_profile *prof);
```

#### `fman_pcd_prs.c` — Parser configuration (~400 LOC)

Programs the FMan parser's "header examination sequences" (HXS). Mainline already initialises the parser for stock IPv4/IPv6/TCP/UDP/ESP/VLAN — this module adds HXS for the L7-tunnel/custom-header cases ASK2 needs (GRE, VXLAN, MPLS in v1.1; pass-through in v1.0).

#### `fman_pcd_replic.c` — Frame replication (~600 LOC)

Multicast egress fanout. Builds and maintains the silicon's frame-replication tables. Consumed by `ask_flow.c` when `OP_FLOW_INSERT_V4_MCAST` / `OP_FLOW_INSERT_V6_MCAST` paths are wired in M3.

#### `include/linux/fsl/fman_pcd.h` — Public API (~600 LOC)

Single header consumed by `ask.ko`. Contains:
- All typed handle forward declarations (`struct fman_pcd`, `struct fman_pcd_kg_scheme`, etc.)
- All function prototypes
- `struct fman_pcd_action` and the action-type discriminator enum
- Register-bit constants the consumer needs at compile time (e.g. action-flags bitmap, scheme-extract masks)
- Versioning macro: `FMAN_PCD_API_VERSION 1` for ABI gating

### 13.4 Integration with existing in-tree files

Three existing files get small additions (≤ 50 LOC each):

- **`fman.c`** — add `fman_get_pcd(struct fman *)` accessor + lifecycle wire-up of `fman_pcd_init/release` in `fman_probe/remove`. ~30 LOC.
- **`fman_keygen.c`** — demote two static helpers (`keygen_scheme_setup`, `keygen_bind_port_to_schemes`) to non-static + EXPORT_SYMBOL_GPL. ~10 LOC.
- **`fman_port.c`** — add `fman_port_pcd_attach(struct fman_port *port, struct fman_pcd_kg_scheme *scheme)` for routing port ingress into the PCD pipeline. ~40 LOC.

Total in-tree additions outside the new files: ~80 LOC. Existing functionality untouched — patch is additive, not invasive.

### 13.5 What `ask.ko` calls

`ask_hostcmd.c` (the wire-format encoders from PR6) is repurposed as the **typed-struct layer**. The existing `ask_hw_flow_insert_v4_tcp(fman, key, action, out_id)` signature stays — the implementation switches from "encode wire bytes + send via fmd_host_cmd" to "build fman_pcd_cc_key_table + call fman_pcd_cc_node_create". Same surface for the rest of `ask.ko`; only the back-end changes.

```c
/* ask_hostcmd.c — after PR14 lands */
int ask_hw_flow_insert_v4_tcp(struct fman *fman,
                               const struct ask_hw_flow_key_v4 *key,
                               const struct ask_hw_action *action,
                               u32 *out_hw_flow_id)
{
    struct fman_pcd *pcd = fman_get_pcd(fman);
    struct fman_pcd_cc_key_table entry = { ... build from *key ... };
    struct fman_pcd_action act        = { ... build from *action ... };
    struct fman_pcd_cc_node *node     = ask_priv_cc_node_for_v4_tcp(fman);
    int idx = fman_pcd_cc_node_add_key(node, &entry, &act);
    if (idx < 0) return idx;
    *out_hw_flow_id = ask_priv_pack_hw_flow_id(node, idx);
    return 0;
}
```

The §12 wire-format encoders survive as **dead code preserved against a future custom-microcode path** — same fate as PR12's `fmd_host_cmd_send()`. They have golden-hex kunit tests (PR6) and stay green.

### 13.6 Module signing and Kconfig

`CONFIG_FSL_FMAN_PCD` (new, tristate, default `m` on ARM64, depends on `FSL_FMAN`). Builds as part of `fsl_dpaa_fman.ko` (same module as existing `fman.c` / `fman_keygen.c` etc.). No new .ko introduced — fewer artefacts to sign and load. `MODULE_SIG_FORCE=y` covers it via the existing FMan module signature.

### 13.7 Acceptance gates for the patch

PR14a–g (see implementation plan):

1. `bash kernel/common/scripts/patch-health.sh --flavor ask --source release` shows `patches/0004-fman-pcd-subsystem.patch` green.
2. `ls /sys/bus/platform/devices/1a00000.fman/` shows no new entries (no operator-facing sysfs — pure ask.ko backend).
3. `cat /sys/kernel/debug/fman_pcd/muram_budget` (debugfs, added in PR14a) shows non-zero free MURAM.
4. kunit suites for each module pass: `tests/fman_pcd_kg_test.c`, `fman_pcd_cc_test.c`, etc.
5. On real silicon: `ask-cli muram` reports MURAM budget after `modprobe ask` and `set system offload ask enable`.
6. M2 acceptance gate (§11.1) passes: nft `flow add` → packet traverses 210 fast path → CPU < 5% at ≥ 2 Gbps.

### 13.8 Upstream submission posture

`0004-fman-pcd-subsystem.patch` is **upstream-aspirational**, not upstream-ready at landing time. The right reviewers (NXP FMan maintainers Madalin Bucur, Camelia Groza, plus anyone shipping LS1043/LS1046 in mainline products) exist, but at ~7800 LOC across 7 files this is a large enough patch series that landing it requires (a) splitting into 3–4 reviewable chunks (orchestration + KG + CC + manip-plcr-prs-replic) and (b) sustained review effort across multiple cycles. The patch ships out-of-tree under `kernel/flavors/ask/patches/0004-*.patch` for ASK2 v1.0 GA. Upstream submission is a v1.1 milestone.

License: GPL-2.0-only (matches existing `fman.c`). Both the NXP SDK PCD tree (dual-licensed BSD-3-or-GPL-2.0 — we elect GPL-2.0) and the `we-are-mono/ASK` stack (GPL) are GPL-compatible, so referencing them for silicon behaviour does not encumber the GPL-2.0-only license assertion on `0004-fman-pcd-subsystem.patch`. No SDK *headers* are pulled in verbatim — the public ABI in `include/linux/fsl/fman_pcd.h` is a fresh design per §13.1 (modern Linux idioms, typed structs, single discriminator-union per spec §13.5).

---

## 14. Testing strategy

Unit and integration tests are implementable from this spec; the performance gates in §11.1 require live hardware and a traffic generator.

### 14.1 Unit tests (kunit, in-tree)

Cover `ask_flow.c` (RCU semantics, hash table operations), `ask_hostcmd.c` (wire format encoding/decoding, byte-perfect), `ask_genl_attr.c` (nla policy enforcement, malformed input rejection).

Run on every patch via kunit_runner in CI on x86_64 emulation. No hardware required.

### 14.2 Integration tests (pytest, hardware-in-the-loop)

Run on a real Mono Gateway DK in the CI lab. Tests:

- Install one flow via nft, send packets, verify hardware path
- Install 750 flows (MURAM full), verify graceful overflow to software
- Install IPsec SA via ip xfrm, verify ESP frames decrypted via CAAM
- Module rmmod/insmod cycle, verify no kernel crash
- VyOS CLI commands produce expected configuration
- Real HTTP traffic generator (h2load) at 1 Mpps → verify offload counters

### 14.3 Fuzzing (syzkaller, periodic)

The genl interface is a syscall surface. Add ask family to syzkaller fuzzing harness. Catches: malformed nla attributes, missing capability checks, integer overflows in length fields, double-free in flow_block_cb.

### 14.4 Performance benchmarks (Spirent / Keysight, pre-release)

The acceptance gates in Section 11.1 must pass on the Mono test rig before v1.0 GA tag.

---

## 15. Effort estimation

### 15.1 LOC estimate

| Component | LOC | Notes |
|---|---|---|
| ask.ko (kernel module) | 3500 | Modern C, RCU/u64_stats_sync, type-safe genl |
| In-tree patches 0001/0002/0003 | 650 | Small, upstream-ready |
| **In-tree patch 0004 — FMan PCD subsystem (§13)** | **7800** | **NEW in v1.0. Modern in-tree FMan PCD across 7 .c + 1 .h** |
| askd (userspace daemon) | 4000 | meson/libmnl/sd-event, modern systemd integration |
| ask-cli (Python) | 800 | Varlink client, rich tabular output |
| VyOS CLI integration | 1200 | XML defs + conf_mode + op_mode |
| Build pipeline | 600 | bin/ci-build-ask-*.sh, hooks |
| Test suite (kunit + pytest) | 2500 | Unit + integration + fuzzing harness |
| Documentation | 1500 | man pages, operator guide, dev guide |
| **Total** | **~22550 LOC** | v0.8 was ~14750; +7800 for §13 PCD subsystem |

The PCD subsystem (§13) is the single largest component and the gating block for §11.1 perf gates. It is still **~9,250 LOC smaller** than a forward-port of the deleted SDK PCD code (~30,000 LOC vendor C) would have been — see §12.9 cost survey and §13.1 savings table.

### 15.2 Implementation time (spec-driven)

Given the spec is concrete and the design uses standard mainline patterns, productivity is bounded by the strength of mainline precedent for each component:

| Component | Precedent strength | Productivity (LOC/day) | Eng-days |
|---|---|---|---|
| ask.ko core (flow table, RCU, genl) | Very strong (mlx5, nfp examples) | 600 | 6 |
| ask.ko offload (flow_block, xfrmdev) | Strong (mainline patterns) | 400 | 5 |
| ask.ko hostcmd (wire protocol) | Strong (Section 12 specification) | 500 | 3 |
| ask.ko CAAM glue | Medium (one in-tree patch) | 300 | 2 |
| In-tree patches 0001/0002/0003 | Strong (mainline conventions) | 200 | 4 |
| **In-tree patch 0004 — FMan PCD (§13)** | **Medium (RM §8.7–8.10, no upstream precedent)** | **300** | **26** |
| askd | Very strong (modern daemon patterns) | 800 | 5 |
| ask-cli | Very strong (Python Varlink client) | 1000 | 1 |
| VyOS CLI | Very strong (VPP precedent) | 600 | 2 |
| Build pipeline | Strong | 400 | 2 |
| Test suite | Strong | 500 | 5 |
| Documentation | Strong | 800 | 2 |
| **Subtotal spec-implementable** | | | **63 eng-days** |

### 15.3 Hardware-bound and review-bound work

These items require live silicon, a traffic generator, or upstream review and run on a calendar (not LOC) basis. They overlap heavily with §14.2:

| Activity | Calendar weeks |
|---|---|
| FMan PCD silicon-bringup against RM §8.7–8.10 (KG schemes, CC trees, manip, plcr) | 4-6 |
| Hardware verification at each milestone | 4 |
| Performance gate measurement runs | 2 |
| Upstream patch review (caam-qi-share, dpaa-eth-flow-block) | 8-12 (parallel) |
| Upstream patch review (0004 PCD — v1.1 target, not v1.0 critical path) | 20+ (parallel, post-GA) |
| Code review and integration | 3 |
| **Subtotal calendar (mostly parallel)** | **12-14 weeks** |

### 15.4 Realistic total

Combining the spec-implementable work with the hardware-bound and review-bound calendar:

- **Months 1-2**: ask.ko core + in-tree patches 0001/0002/0003 + skeleton ask.ko on hardware. M1 gate: `modprobe ask` succeeds, `ASK_CMD_GET_INFO` round-trip works.
- **Months 2-3**: **FMan PCD subsystem (§13 / patch 0004)** — orchestration, KG schemes (match_vector ≠ 0), CC match trees. PR14a–c.
- **Month 4**: PCD manip + plcr + prs + replic (PR14d–f) + end-to-end wire-up (PR14g). M2 gate: nft `flow add` → packet traverses 210 fast path on real hardware → CPU < 5 % at ≥ 2 Gbps.
- **Month 5**: All non-IPsec flow types + bridge + offline ports + xfrm packet-mode + CAAM integration. M3+M4 gates: NAT works, bridge offload works, AES-GCM-128 IPsec at 3 Gbps.
- **Month 6**: askd + ask-cli + VyOS CLI. M5 gate: vanilla Mono Gateway DK boots VyOS rolling with `set system offload ask` and forwards at line rate.
- **Month 7**: VPP coexistence, performance tuning, soak testing, v1.0 RC.

**Total: 7 months end-to-end.** v0.8 estimated 6 months on the (incorrect) assumption that the in-tree FMan PCD existed. The added month covers §13 (PCD subsystem) which §12.9 identified as a hard gating dependency.

If hardware verification of the PCD subsystem reveals RM-undocumented edge cases, add 1-2 months. If upstream patch review delays affect critical path, those happen in parallel — they don't gate v1.0 GA.

---

## 16. Risk register

| # | Risk | Probability | Impact | Mitigation |
|---|---|---|---|---|
| 1 | RM §8.7–8.10 leaves PCD register/MURAM-layout corner cases undocumented | Medium | High | Live-silicon probing during PR14b–d; preserve SDK archive as last-resort reference (read, don't copy) |
| 2 | `caam_qi_ext_consumer_register` API insufficient | Low | Medium | Extend patch; mainline maintainers are at NXP, expect cooperation |
| 3 | `dpaa-eth-flow-block` patch rejected upstream | Low | Low | Keep as OOT for v1.0; revisit upstream after stabilisation |
| 4 | `0004-fman-pcd-subsystem.patch` (§13) rejected by NXP FMan maintainers as out-of-scope | Medium | Low | Patch is upstream-aspirational, not blocking — v1.0 ships it OOT; upstream is a v1.1 target |
| 5 | Performance gates miss Geerling 1 Mpps target | Medium | High | Cache-line alignment tuning; verify RCU latency; offline-port plumbing |
| 6 | Mono changes 210 ucode and breaks PCD register contract | Low | High | RM §8.7–8.10 is silicon-stable; ucode change wouldn't move PCD registers |
| 7 | VyOS Inc. changes VPP integration model | Low | Low | Track 1.5 LTS until 2027 |
| 8 | Subtle bugs in fast-path / RCU / genl code | High | Medium | kunit ≥80%, mandatory code review, syzkaller fuzzing |
| 9 | 6.18 LTS gets superseded mid-project | Low | Medium | Pin to LTS until 2027, update at boundary |
| 10 | NETIF_F_HW_ESP / NETIF_F_HW_TC interaction with VyOS networking config | Low | Low | Test early, document any caveats |
| 11 | RCU grace period under high flow churn causes memory pressure | Low | Medium | call_rcu rate-limiting if needed; monitor in soak testing |
| 12 | PCD subsystem inadvertently carries forward SDK architectural patterns (handle_t, OS-shim, AMP IPC, nested Peripherals/ layout) → maintainability concern (v1.1) | Medium | Medium | Reviewer focuses on modern-kernel idiom enforcement (typed structs, RCU correctness, devm_*, no opaque handles); every non-trivial function cites its primary RM section in a comment; SDK/we-are-mono cross-refs cited only for silicon-behaviour disambiguation, never for code structure |

---

## 17. Open questions

1. **210 event channel binding** — confirm IRQ number and event queue layout on Mono hardware. Ask Tomaž. *(partially answered §12.8 — SPI 44 / Linux IRQ 59 is the FMan event IRQ on LS1046A.)*
2. **Mono's exact 210 build version** — confirmed §12.8: QEF 210.10.1 LS1043 r1.0.
3. **VPP shipping path in VyOS 1.6** — track VyOS Inc. roadmap; if VPP becomes default, hybrid mode becomes default.
4. **Upstream NXP cooperation on `dpaa-eth-flow-block` and `0004-fman-pcd-subsystem.patch`** — Madalin Bucur and Camelia Groza are the maintainers. Open conversation early; their feedback shapes patch design.
5. **MURAM allocation tuning for PCD subsystem** — what's the right partition between KG scheme storage, CC match trees, manipulator templates, policer profiles, and FIFO reservations on a 384 KB MURAM? Probe during PR14a using the new debugfs `/sys/kernel/debug/fman_pcd/muram_budget`.
6. **Spirent vs CyPerf for performance gates** — Patrick Kennedy at STH ran Geerling's test on Keysight. Can we get access? Otherwise document software-generator caveats.
7. **VyOS rolling vs LTS target for v1.0** — rolling makes sense for early adopters; 1.6 LTS (mid-2026?) for production. Confirm with VyOS Inc.
8. ~~**Provenance-reviewer assignment**~~ — *Closed in v1.1.* The previous strict-provenance constraint was dropped; both NXP SDK and `we-are-mono/ASK` are GPL and usable as silicon-behaviour references. Reviewer focus shifts to modern-kernel idiom enforcement per risk #12 mitigation above.

---

## 18. Reference table — every modern Linux 6.18 facility we use

| Facility | Where we use it | Replaces what NXP 1.x had |
|---|---|---|
| `genl_family` | `ask_genl.c` | Custom NETLINK_KEY=32 channel |
| `rhashtable` | `ask_flow.c` | Custom hash table with spinlocks |
| `u64_stats_sync` | `ask_stats.c` | atomic_t with cache-line contention |
| `call_rcu` / `kfree_rcu` | `ask_flow.c` | refcounted free with read locks |
| `register_netevent_notifier` | `ask_neigh.c` | /proc/net/neigh polling |
| `register_switchdev_notifier` | `ask_bridge.c` | auto_bridge.ko polling thread |
| `nf_conntrack_event_notifier` | `ask_genl.c` | Direct nf_conntrack hook patching |
| `flow_block_cb` / `tc_setup_cb` | `ask_flow_offload.c` | comcerto_fp_netfilter.c |
| `nf_flow_table` HW offload | implicit via flow_block | Bespoke fastpath in CDX |
| `xfrmdev_ops` + packet mode | `ask_xfrm.c` | ipsec_flow.c |
| Tracepoints | `ask_trace.h` | `printk` debug noise |
| kunit | `tests/` | None (NXP had no unit tests) |
| sd-event (userspace) | `askd/event_loop.c` | glib mainloop |
| systemd Varlink | `askd/varlink_api.c` | Cisco-style libcli |
| journald structured logging | `askd/log.c` | text file logs |
| meson build | `askd/meson.build` | autotools |
| sd_notify Type=notify | `askd.service` | fork/daemonize |
| systemd sandboxing | `askd.service` | None |
| `fman_muram` allocator | `fman_pcd.c` (§13.3) | SDK XX_Malloc OS-shim |
| `rhashtable` (PCD-side reuse) | `fman_pcd_cc.c` (§13.2) | SDK fm_ehash.c (~1700 LOC) |
| Kernel `<linux/crc64.h>` | `fman_pcd_kg.c` | SDK crc64.h inline tables |
| `include/trace/events/fman_pcd.h` | `fman_pcd*.c` | SDK TRACE_RTOS macros |

---

## 19. What we don't do

For clarity, listing the things this spec explicitly does NOT include:

- Rewrite mainline `dpaa_eth`, `fman` (core), `fsl_qbman`, `caam_qi`. We use mainline as-is. §13's `0004` patch is **additive** in `drivers/net/ethernet/freescale/fman/` — it does not modify existing files beyond ~80 LOC of accessor additions.
- Rewrite the 210 ucode. It's silicon firmware.
- Forward-port the deleted NXP SDK `sdk_fman/Peripherals/FM/Pcd/` tree. §12.9 cost survey rejected this in favour of the modern reimplementation in §13.
- Vendor SDK FMan/QMan/BMan drivers. We use mainline `drivers/net/ethernet/freescale/dpaa/`.
- `/dev/cdx_ctrl` ioctl compatibility shim. Out of v1.0 scope.
- `libfci.so.1` ABI preservation. Out of v1.0 scope.
- XML configuration files for runtime PCD. Configuration is operator-facing nft/iproute2, not vendor XML.
- A VPP plugin. VPP talks to ASK only through standard memif + rtnetlink.
- A DPDK PMD. ASK is kernel-side offload; DPDK has its own path.
- tc-flower as the primary user-facing interface. We support it via flow_block_cb but nft `flow add` is the preferred operator surface for VyOS users.
- Upstream submission of `ask.ko` itself. The module stays out-of-tree until the protocol/MURAM accounting matures and the dpaa_eth flow_block patch lands.
- Upstream submission of `0004-fman-pcd-subsystem.patch` in v1.0. §13.8 makes it upstream-aspirational; v1.1 target.

These are non-goals. Don't slip them in.

---

## 20. Implementation cookbook

1. **Read Sections 0, 1, 3, 4, 5, 7, 12, 13 in full** before writing any code. §13 is the largest single block of new C and the gate to §11.1 perf.
2. **Set up the build pipeline first.** Get a kernel building with the four in-tree patches applied, even if 0004 is an empty stub. Verify the OOT module skeleton builds, signs, and loads.
3. **Implement `ask_main.c` + `ask_genl.c`** with `ASK_CMD_GET_INFO` returning version 1. Verify with `genl ctrl-list | grep ask` and `ynl --family ask --do get-info`.
4. **Implement `ask_hostcmd.c`** with full Section 12 wire-format support. Backed by kunit tests for every encoding path. Do NOT touch hardware yet. (Preserved against future custom-microcode path per §12.8; consumed by §13 PCD path in steps 7–10.)
5. **Implement `ask_flow.c`** with rhashtable + RCU. kunit tests for insert/lookup/remove under simulated concurrent access.
6. **Implement `ask_flow_offload.c`** registering `flow_block_cb` on a dummy netdev in test. Verify nft `flow add` translates to your callback.
7. **Implement §13 patch 0004 — FMan PCD subsystem.** PR14a (orchestration) → PR14b (KG schemes) → PR14c (CC trees) → PR14d (manip) → PR14e (plcr) → PR14f (prs+replic). kunit per module. Hardware bring-up at PR14b end (single KG scheme programs a known FQID).
8. **Wire `ask_hostcmd.c` to the PCD API** (§13.5). The wire-format encoders become typed-struct builders calling `fman_pcd_cc_node_add_key()` etc. Same surface for the rest of `ask.ko`.
9. **End-to-end on hardware.** Generate a flow with iperf, watch it install via tracepoints, watch packets traverse the silicon. M2 gate (PR14g).
10. **Iterate through remaining flow types** (IPv6, mcast, bridge) by adding CC node specialisations in `ask_hostcmd.c`.
11. **Implement `ask_xfrm.c`** with `xdo_dev_state_add` + CAAM descriptor sharing. M4 gate: AES-GCM-128 at 3 Gbps.
12. **Implement `askd`** with sd-event + libmnl. Wire to genl multicast events.
13. **Wire VyOS CLI** following the VPP precedent.
14. **Performance gates** (Section 11.1) run on real hardware against the M5 build.

---

## 21. Glossary

- **210 ucode** — NXP QEF microcode (210.10.1 LS1043 r1.0 on shipping Mono Gateway DK, see §12.8). Loaded by U-Boot from SPI flash. Not redistributed by us. **Does not implement the §12.1–§12.6 host-command opcode-dispatch protocol** — that protocol is reference material against a hypothetical future custom microcode.
- **PCD (Parse / Classify / Distribute)** — FMan's silicon-side classification and forwarding pipeline. Specified in LS1046A RM chapter 8. Implemented for ASK2 by §13's `0004-fman-pcd-subsystem.patch` because mainline 6.18 ships only KG-for-RSS, not the full PCD surface.
- **KG (KeyGen)** — FMan block that extracts a key from a packet's headers and dispatches it into a classification scheme. 32 schemes per FMan. RSS hashing is `match_vector = 0`; exact-match flow lookup needs `match_vector ≠ 0` (§13.3).
- **CC (Coarse Classifier)** — FMan block that walks a match-tree using the KG-extracted key and selects an action. The exact-match table layer of §13.
- **Manip (Header Manipulation)** — FMan block that rewrites headers in silicon (SNAT/DNAT/PAT, VLAN, TTL, checksum). §13's NAT engine.
- **Plcr (Policer)** — FMan block implementing RFC 4115 two-rate three-color policing.
- **flow_block_cb** — Mainline Linux callback structure for HW offload drivers. Used by `nf_flow_table` and `tc-flower` to push flows to drivers.
- **xfrmdev_ops** — Mainline Linux device callbacks for IPsec offload. Packet mode (since 6.2) lets hardware own SA state.
- **CAAM QI** — Queue Interface path on CAAM SEC. Crypto descriptors dequeue/enqueue via QMan, not CPU.
- **OP (Offline Port)** — FMan port with no MAC, used for packet re-injection after CAAM decryption.
- **MURAM** — FMan shared scratchpad. 384 KB on LS1046A. Holds flow tables, FIFOs, internal contexts.
- **fmd_host_cmd** — Kernel-internal API exposed by patch 0003 for sending host commands to FMan microcode.
- **Varlink** — Modern IPC protocol used by systemd ecosystem. Replaces D-Bus for new services.
- **ynl** — Kernel tool (in `tools/net/ynl`) that generates type-safe genl clients from YAML schemas.
- **QEF (QorIQ Engine Firmware)** — NXP's upstream microcode blob format for FMan, distinguished from the hypothetical custom-microcode path of §12.1–§12.6 by the magic `'Q' 'E' 'F' 0x01` at offset 4 of the SPI-flash blob. §12.8.
- **Provenance discipline (v1.1)** — §13's `0004-fman-pcd-subsystem.patch` is **RM-authoritative**: the LS1046A Reference Manual chapter 8 is the source of truth for byte-perfect MURAM layouts. Both the archived NXP SDK PCD tree (`mihakralj/kernel-ls1046a-build@464df181`, dual-licensed BSD-3-or-GPL-2.0) and the `we-are-mono/ASK` legacy stack are GPL-compatible and are usable as silicon-behaviour cross-references. What is rejected is the SDK's *architecture* (handle_t, ncsw, AMP IPC, nested Peripherals/) — not its silicon facts. See §13 intro and risk #12. (The v1.0 strict-provenance constraint that this section formerly documented was dropped in v1.1.)

---

**End of v1.1.** Supersedes v1.0 in entirety. v1.1's material change is the **provenance relaxation** on §13's FMan PCD subsystem: the previous strict-provenance constraint is replaced with "RM-authoritative, SDK + we-are-mono usable as silicon references, modern kernel architecture mandatory". This unblocks PR14c-body / PR14d-body / PR14e-body / PR14f-body work in sessions that have the archived `mihakralj/kernel-ls1046a-build@464df181` SDK tree loaded but not the (NDA) LS1046A Reference Manual — they can author body PRs by reading silicon facts from the SDK and modernizing the architecture per §13.1. v1.0's §12.9 cost survey and §13 module decomposition are unchanged. Risk #12 reframed from copyright→maintainability. Open question #8 closed. LOC budget, calendar, milestone gates: unchanged from v1.0.
