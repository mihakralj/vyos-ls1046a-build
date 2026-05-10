# Kernel-Native DPAA1 Acceleration Stack for VyOS

**Status:** Draft v0.2
**Target hardware:** NXP LS1046A (Cortex-A72 ×4, FMan v3L, QMan v3, BMan v3)
**Target software:** Linux kernel 6.6+, ARM64, VyOS 1.5+
**License intent:** GPL-2.0 (upstreamable)

---

## 1. Scope

A clean-room reimplementation of the LS1046A DPAA1 kernel stack: BMan, QMan, FMan, and the Ethernet driver. Modular, modprobe-friendly, designed around modern kernel networking idioms (NAPI, page_pool, phylink, XDP, AF_XDP, tc-flower offload). Replaces upstream `dpaa_eth` and the entire NXP LSDK out-of-tree variant for VyOS use.

The end state is that VyOS users see normal Linux netdev interfaces. Everything they already know works: `ip`, `ip route`, `tc`, `nftables`, `bridge`, `bonding`, `conntrack`, `ethtool`, `tcpdump`. Performance improvements come from XDP for fast forwarding paths and AF_XDP for userspace consumers that want kernel-bypass without owning the entire stack.

### 1.1 Goals

- Six modular kernel modules with explicit dependency chain, no monolithic blob
- Standard netdev interface per FMan port; no special VyOS integration required for normal use
- Native XDP support including `XDP_REDIRECT` and `XDP_TX` at line rate
- AF_XDP zero-copy support for VPP, DPDK, or custom userspace consumers
- tc-flower offload using FMan's classifier (PCD CC) for ACLs and 5-tuple steering
- ethtool RSS via FMan KeyGen
- Modern memory management (page_pool, page_frag) replacing upstream's manual recycling
- phylink-based MAC abstraction
- Realistically upstreamable to mainline kernel (24-month horizon, not v1)

### 1.2 Non-goals (v1)

- DPAA2, LS1088, LX2160 support
- IEEE 1588 PTP timestamping
- DCB / PFC / lossless Ethernet
- Multi-tenant resource partitioning (kernel + AF_XDP coexistence is in scope; multi-userspace-tenant is not)
- Switchdev model (LS1046A has no switch fabric, just MACs)
- SR-IOV (DPAA1 has no equivalent)

### 1.3 Out-of-scope but planned

- Full Rust port of the netdev driver layer in v2, once kernel Rust netdev abstractions stabilize
- Mainline upstream submission post-v1
- LS1043A support (same FMan v3L, fewer ports, mostly Kconfig work)

---

## 2. Hardware reference

### 2.1 Block topology

```
+---------------+     +---------------+     +-------------+
|   4× A72 CPU  |<--->|  CCI-400      |<--->|   DDR4      |
+---------------+     +---------------+     +-------------+
        |                     |
        |                     v
        |             +---------------+
        |             |    DPAA1      |
        |             |  +---------+  |
        +-------------+->|  QMan   |  |
        |             |  +---------+  |
        |             |  +---------+  |
        +-------------+->|  BMan   |  |
                      |  +---------+  |
                      |  +---------+  |
                      |  |  FMan   |  |
                      |  | (1×)    |  |
                      |  +---------+  |
                      |  +---------+  |
                      |  |  CAAM   |  |
                      |  +---------+  |
                      +---------------+
                              |
                      +---------------+
                      | 6× SGMII MAC  |
                      | 2× 10G MAC    |
                      +---------------+
```

LS1046A has one FMan instance with eight network ports total (six 1G SGMII and two 10G XFI), one QMan instance providing per-CPU portals, one BMan instance providing per-CPU portals, and one CAAM block for crypto. All sit on the internal CCI-400 coherent interconnect with the four A72 CPUs.

### 2.2 Per-CPU portals

Each Cortex-A72 has its own QMan portal and BMan portal mapped at fixed physical addresses. The portal is split into two MMIO regions:

| Region | Cacheability | Purpose |
|--------|--------------|---------|
| CE (Cache-Enabled) | Normal cacheable, write-back | Command rings, DQRR, EQCR, RCR |
| CI (Cache-Inhibited) | Device-nGnRnE | Doorbells, status, interrupt regs |

The CE region is sized 16 KiB per portal; CI is 4 KiB. Kernel init maps both with the correct memory attributes via `ioremap_wc` for CE and `ioremap` for CI. Since this is in-kernel, no PAMU programming for buffer access is needed; the standard DMA API (`dma_map_*`, `dma_alloc_coherent`) handles addressing.

### 2.3 Ring structures

**QMan EQCR (Enqueue Command Ring):** 8 entries × 64 B. Software writes a frame descriptor + verb, increments producer index, rings doorbell. Hardware drains and decrements consumer.

**QMan DQRR (Dequeue Response Ring):** 16 entries × 64 B. Hardware writes dequeue responses (containing FD + FQID + sequence number); software polls consumer index, processes, advances.

**QMan MR (Message Ring):** 8 entries × 64 B. Async events (FQ state changes, congestion notifications). Polled at slow path or threaded IRQ.

**BMan RCR (Release Command Ring):** 8 entries × 64 B. Release up to 8 buffers per command. Acquire is via a separate command path on the BMan portal.

All rings use cyclic indices with a "vbit" toggle so software detects new entries without per-entry zeroing.

### 2.4 FMan structure

One FMan instance with the following resources:

- **MURAM**: 384 KiB shared RAM for FMan internal state (queue contexts, parser config, KeyGen tables). Statically partitioned at init.
- **Ports**: 8 RX ports + 8 TX ports + 2 OP (offline parse) ports. Each network port maps to one MAC.
- **Microcode**: ~16 KiB binary blob loaded into FMan at init. Provides the parse/classify pipeline. NXP-redistributable.
- **Parser**: Microcode-driven parse tree (Ethernet, VLAN, IPv4, IPv6, TCP, UDP, GRE, ESP, custom).
- **KeyGen**: Extracts hash keys from parse results for distribution across queues.
- **Policer**: Two-rate three-color marker per port and per flow.
- **CC (Coarse Classifier)**: Lookup tables for steering and ACLs.

### 2.5 Frame Descriptor (FD)

The 32-byte FD is the unit of work between software and hardware. Layout (little-endian on LS1046A; struct definition is illustrative, the implementer should generate from the LS1046A reference manual):

```c
struct qm_fd {
    union {
        struct {
            u8       dd:2;          /* dynamic debug */
            u8       liodn_offset:6;
            u8       bpid;          /* buffer pool ID */
            u8       eliodn_offset:4;
            u8       reserved:4;
            u8       addr_hi;       /* phys addr [39:32] */
            __be32   addr_lo;       /* phys addr [31:0] */
        };
        u64      opaque_addr;
    };
    __be32   format:3;          /* 0=single buf, 1=S/G, 2=long single */
    __be32   offset:9;          /* data offset within buffer */
    __be32   length:20;         /* data length OR S/G entry count */
    __be32   cmd_status;        /* tx confirmation, rx parse status */
} __attribute__((packed, aligned(8)));
```

For RX, the offset points to the start of the L2 header; FMan writes a parse result block before that, in the headroom. For TX, software sets format=0, addr=phys(buffer_start), offset=headroom, length=packet_len.

### 2.6 FMan parse result

Sixty-four bytes written by FMan into the buffer at a configurable offset (we put it at byte 0 of the buffer). Contains L2/L3/L4 protocol identifiers, header offsets, hash result, VLAN tag info, and parse status flags. Bit-exact layout in LS1046A RM §8.7; the implementer should generate the struct from there rather than transcribing it from any other source.

### 2.7 Stashing

QMan can pre-fetch the start of incoming packet data into L1 or L2 cache before raising the dequeue response. Configured per-FQ:

- `stash_lines`: number of 64 B lines to stash from packet data
- `stash_data_l1` / `stash_data_l2`: target cache level
- `stash_annotation_lines`: lines of parse result to stash
- `stash_context_lines`: lines of FQ context to stash

Critical for line rate. The difference between "stashing tuned" and "stashing off" is roughly 3× in L3 forwarding throughput on this SoC. v1 default is 1 + 2 + 1 (annotation + data + context) targeting L1.

### 2.8 Errata to handle

The LS1046A errata list (chip rev 1.0 and 1.1) includes ~30 DPAA-related items. Must-implement for v1:

- A-009885 (FMan): RX FIFO threshold tuning per port speed
- A-010022 (QMan): EQCR consumer index read after enqueue write
- A-010165 (BMan): minimum 8-buffer release granularity below pool depletion threshold
- A-010379 (FMan v3L): parser stall on certain VLAN+IPv6 ext header combinations, requires soft parser override

The full list lives in the LS1046A chip errata document. Each erratum should be tagged in code with its number and the conditions under which it applies. Several PowerPC-era errata in NXP's code do not apply to A72 and should be omitted, not transcribed.

---

## 3. Software architecture

### 3.1 Module layering

```
                  +---------------------------+
                  |     fsl-dpaa1-caam.ko     |  optional
                  |   (xfrm IPsec offload)    |
                  +---------------------------+
                            |  CAAM JR
                            v
+---------------------+  +---------------------+
| fsl-dpaa1-pcd.ko    |  | fsl-dpaa1-eth.ko    |
| (tc-flower offload) |  | (netdev driver)     |
+---------------------+  +---------------------+
            \             /
             \           /
              v         v
            +-------------+
            | fsl-fman.ko |
            +-------------+
                  |
            +-------------+
            | fsl-qman.ko |
            +-------------+
                  |
            +-------------+
            | fsl-bman.ko |
            +-------------+

Reused upstream: phylink, mdio, mdio-mux, of_mdio, page_pool, xdp_sock_buff
```

### 3.2 Module responsibilities

**`fsl-bman.ko`** (~2 kLOC):
- BMan global init, portal infrastructure
- Per-CPU portal driver with kernel-internal API
- BPID allocation, buffer release/acquire primitives
- Pool depletion notifications
- Exposes `<linux/fsl/bman.h>` to higher layers

**`fsl-qman.ko`** (~3.5 kLOC, softdep `fsl-bman`):
- QMan global init, portal infrastructure
- Per-CPU portal driver with EQCR push and DQRR poll
- FQ state machine (OOS, SCHED, PARKED, RETIRED)
- Channel and CGR (congestion group) management
- IRQ handling, threaded IRQ for portal events
- Exposes `<linux/fsl/qman.h>`

**`fsl-fman.ko`** (~3 kLOC, softdep `fsl-qman fsl-bman`):
- FMan global init, MURAM allocator
- Microcode loader (firmware request, version check, load)
- Port management (RX/TX/OP), MAC binding
- phylink integration via existing `mac` driver pattern
- Default parser/KeyGen profiles
- Exposes `<linux/fsl/fman.h>`

**`fsl-dpaa1-pcd.ko`** (~2 kLOC, softdep `fsl-fman`):
- PCD configuration: parser tweaks, KeyGen profiles, CC tables
- tc-flower offload: translate flower rules into CC entries
- Per-port classifier hierarchies
- Optional module; without it, ports use a basic L4 hash profile

**`fsl-dpaa1-eth.ko`** (~3.5 kLOC, softdep `fsl-fman`):
- Per-port netdev registration
- NAPI poll loops for RX
- TX function with multi-queue
- page_pool-backed buffer pools
- ethtool ops (stats, link, RSS, ring, channels)
- Native XDP, XDP_REDIRECT, AF_XDP zero-copy
- Sysfs/debugfs surfaces

**`fsl-dpaa1-caam.ko`** (~1.5 kLOC, softdep `caam_jr`):
- Glue between kernel xfrm and CAAM job rings
- Algorithm registration (AES-GCM, AES-CBC + HMAC-SHA, etc.)
- Async crypto request submission and completion handling
- Optional module; without it, IPsec runs in software

Total: ~15.5 kLOC against ~60-80 kLOC for the equivalent upstream + LSDK code.

### 3.3 NAPI worker model

One NAPI instance per RX FQ. RX FQs are pinned to specific portals (one portal per CPU). The kernel scheduler runs the NAPI on the CPU owning the portal, IRQ affinity keeps wakeups local, and CPU hotplug callbacks migrate FQs to a surviving portal cleanly.

No threaded NAPI in v1 (the gain is marginal when portals are already CPU-local). Easy toggle in v2 if profiling justifies it.

### 3.4 Why a clean rewrite beats patching upstream

Upstream `drivers/net/ethernet/freescale/dpaa/` carries 15 years of QorIQ history. Specifically:

- PowerPC-era assumptions still latent in buffer alignment and barrier patterns
- Manual buffer recycling instead of page_pool
- Raw `phy_device` instead of phylink
- No XDP, no AF_XDP, no tc-flower offload
- Tight coupling of FMan, QMan, BMan into a single source dir without clean module boundaries
- `fsl_mac` driver glued in via custom platform-data passing
- Per-FQ context structures hand-managed without RCU

Patching this incrementally toward the target architecture is a multi-year exercise involving many maintainers. A parallel implementation under `drivers/net/ethernet/freescale/dpaa1-ng/` (or out-of-tree first) ships in a fraction of the time and is far easier to review because each module is self-contained and small.

---

## 4. Memory model

### 4.1 RX buffer model: page_pool

Each RX queue allocates a `struct page_pool` with:

- `pool_size = 1024` pages (tunable via ethtool `-G`)
- `flags = PP_FLAG_DMA_MAP | PP_FLAG_DMA_SYNC_DEV`
- `dma_dir = DMA_FROM_DEVICE`
- `nid = NUMA_NO_NODE` (LS1046A is single-NUMA)

One 4 KiB page hosts two 2 KiB BMan buffers (head and tail of page). page_pool's fragment API (`page_pool_alloc_frag`) handles this. DMA mapping is cached in the page; BMan release returns the page to the pool.

```
    +-----------------------------+
    | Page (4 KiB)                |
    | +-------------------------+ |
    | | Buffer 0 (2 KiB)        | |
    | |  - 64 B parse result    | |
    | |  - 64 B headroom        | |
    | |  - 1920 B packet+tail   | |
    | +-------------------------+ |
    | | Buffer 1 (2 KiB)        | |
    | |  (same layout)          | |
    | +-------------------------+ |
    +-----------------------------+
```

### 4.2 SKB construction

On dequeue:

```c
page = bm_buf_to_page(fd_addr);
skb = napi_build_skb(page_address(page) + buf_offset, frag_size);
skb_reserve(skb, headroom);
skb_put(skb, fd->length);
skb_record_rx_queue(skb, rx_queue_idx);
skb->protocol = eth_type_trans(skb, netdev);
/* Map FMan parse result into skb hints */
dpaa1_parse_to_skb(parse_result, skb);
```

`napi_build_skb` reuses the slab cache for skb headers and avoids per-packet `alloc_skb`. The buffer page itself stays on page_pool; on free, the page returns to the pool, not to the allocator.

### 4.3 XDP buffer model

For XDP, the same page is wrapped in an `xdp_buff`:

```c
struct xdp_buff xdp;
xdp_init_buff(&xdp, frag_size, &rxq->xdp_rxq);
xdp_prepare_buff(&xdp, page_address(page), headroom, fd->length, false);
act = bpf_prog_run_xdp(prog, &xdp);
```

Headroom is always 64 B (above parse result), which is enough for `XDP_TX` to push an Ethernet header rewrite or a small encap.

### 4.4 AF_XDP zero-copy

When an AF_XDP socket binds in zero-copy mode to an RX queue:

1. The driver replaces page_pool buffers with UMEM buffers from the socket's pool
2. BMan is reseeded with UMEM-backed buffers (DMA addresses from `xsk_buff_pool`)
3. RX path produces FDs whose addresses correspond to UMEM frames
4. Driver hands UMEM descriptors directly to the socket; no skb construction
5. TX from the socket: descriptor address goes straight into FD, FMan transmits, BMan returns

UMEM frames are 4 KiB by default (matches our buffer page assumption) but the driver supports 2 KiB UMEM if the socket requests it. The trick is that BMan only manages buffers from one source at a time per BPID; the cleanest implementation is one BPID per RX queue, swapped between page_pool and UMEM modes on bind/unbind.

### 4.5 TX buffer model

TX is skb-based normally. The skb's frag list gets translated to a single-segment FD when possible (no fragments) or an S/G FD for multi-segment skbs.

```c
if (skb_is_nonlinear(skb)) {
    fd = build_sg_fd(skb);  /* allocate S/G table from a small per-CPU pool */
} else {
    fd = build_simple_fd(skb);
}
```

S/G tables are allocated from a dedicated DMA-coherent per-CPU pool (256 entries of 256 B = 64 KiB per CPU). After TX confirmation, the S/G table returns to the pool.

For XDP_TX and AF_XDP TX, no skb is involved; FDs point directly at the page or UMEM frame.

### 4.6 Buffer accounting

Sysfs surface per netdev:

```
/sys/class/net/eth0/dpaa1/
├── rx_pool_depth
├── rx_pool_recycled
├── rx_pool_allocated
├── tx_inflight
├── tx_sg_pool_used
└── bman_pool_depth
```

Pool exhaustion triggers backpressure: FMan drops at MAC, increments port drop counter. The driver reads BMan depletion thresholds at init and configures reasonable refill points.

---

## 5. Resource model

### 5.1 ID allocations

Resource IDs are assigned at probe time by the relevant module:

| Resource | Range | Per-instance |
|----------|-------|--------------|
| FQ IDs | 0x100 - 0xFFFF | ~64 K |
| BPIDs | 8 - 63 | 56 (0-7 reserved) |
| Channels | 0x21 - 0x2F | 15 (one per portal + spares) |
| CGR IDs | 0 - 255 | 256 |

Kernel-internal allocator API:

```c
int qman_alloc_fqid_range(u32 *fqid, u32 num);
void qman_release_fqid_range(u32 fqid, u32 num);

int bman_alloc_bpid(u32 *bpid);
void bman_release_bpid(u32 bpid);
```

These are kept compatible with upstream `qbman` headers so the FMan and Ethernet drivers can move between this implementation and upstream's during migration.

### 5.2 Per-port resource layout (default)

For each FMan port assigned to the driver:

- 1 default RX FQ (catch-all)
- 8 hashed RX FQs (KeyGen distribution targets)
- 1 error FQ
- N TX FQs where N = num_online_cpus() (one per CPU for lockless TX)
- 1 TX confirmation FQ (shared across TX FQs of the same port)
- 1 BPID per port (separate per-port pools simplify accounting)

For LS1046A with 4 CPUs and 8 ports, total FQ usage is 8 × (1 + 8 + 1 + 4 + 1) = 120 FQs, well under the 64K budget.

### 5.3 Channel assignment

One channel per CPU portal. RX FQs schedule into the portal of the CPU that owns the queue (round-robin: FQ N goes to CPU N % num_cpus). TX FQs schedule into the portal of the CPU that produces the work (current CPU at TX time).

### 5.4 Congestion groups

One CGR per port for ingress. Threshold set to 80% of BMan pool depth; triggers an ECN mark on TCP if `ip_mark_ecn` sysctl is set, otherwise drops. CGR notifications drive netdev tx_queue stop/wake to throttle xmit.

---

## 6. Initialization sequence

### 6.1 Module load order

```
modprobe fsl-bman              # foundation
modprobe fsl-qman              # auto-pulls fsl-bman via softdep
modprobe fsl-fman              # auto-pulls qman + bman
modprobe fsl-dpaa1-eth         # auto-pulls fman, registers netdevs
modprobe fsl-dpaa1-pcd         # optional, enables tc-flower offload
modprobe fsl-dpaa1-caam        # optional, enables xfrm offload
```

Dracut/initramfs hook adds these to early boot when DPAA1 is detected. VyOS image build script handles inclusion.

### 6.2 Probe sequence per module

**`fsl-bman`** probe:
1. Map BMan global registers via OF
2. Initialize buffer pool config space
3. For each online CPU: probe portal, mmap CE/CI, request IRQ, register per-CPU state
4. Register CPU hotplug callback for portal migration

**`fsl-qman`** probe (after BMan ready):
1. Map QMan global registers
2. Allocate FQ ID and channel ID ranges from device tree
3. Per-CPU portal init (similar to BMan)
4. Initialize FQ state machine workqueue

**`fsl-fman`** probe (after QMan + BMan ready):
1. Map FMan registers, initialize MURAM
2. `request_firmware()` for microcode, validate, load
3. For each child MAC node: register subdevice
4. Initialize default PCD profile (basic L3 hash)

**`fsl-dpaa1-eth`** probe (per port, after FMan ready):
1. Allocate netdev, set ops
2. Allocate per-CPU TX FQs and per-port RX FQs
3. Allocate BPID, create page_pool, seed buffers via BMan release
4. Setup phylink, parse PHY from DT
5. Register netdev with kernel
6. ethtool, NAPI, XDP setup

### 6.3 Microcode

```c
const struct firmware *fw;
err = request_firmware(&fw, "fsl_fman_ucode_ls1046_r1.0_106_4_18.bin", dev);
if (err) return err;
err = fman_validate_ucode(fman, fw->data, fw->size);
if (!err) fman_load_ucode(fman, fw->data, fw->size);
release_firmware(fw);
```

Microcode version is logged at probe; mismatch with FMan revision is a warning (driver tries to continue) or error (if mismatch is incompatible). v1 supports microcode 106.4.18 (current as of LSDK 21.08). Newer microcode versions add features (fragmentation/reassembly, IPSec lookup) we don't use in v1.

### 6.4 Failure modes

- Microcode missing: FMan stays disabled, dependent ports do not register netdevs, kernel log makes the cause obvious
- BMan pool seeding fails: port stays down, ethtool reports "no buffers"
- PHY missing: phylink reports link down, port stays operational at netdev level (so userspace can configure)
- IRQ allocation fails: probe fails, port unregisters cleanly

All failures unwind via `devm_*` so partial init doesn't leak resources.

---

## 7. RX data plane

### 7.1 NAPI poll

```c
static int dpaa1_napi_poll(struct napi_struct *napi, int budget)
{
    struct dpaa1_napi *dn = container_of(napi, struct dpaa1_napi, napi);
    struct qman_portal *p = dn->portal;
    int work = 0;

    while (work < budget) {
        const struct qm_dqrr_entry *dq = qman_portal_peek_dqrr(p);
        if (!dq) break;

        if (likely(dq->fqid == dn->rx_fqid)) {
            dpaa1_rx_one(dn, dq);
        } else if (dq->fqid == dn->err_fqid) {
            dpaa1_rx_error(dn, dq);
        } else {
            dpaa1_rx_unexpected(dn, dq);
        }

        qman_portal_consume_dqrr(p);
        work++;
    }

    if (work < budget) {
        if (napi_complete_done(napi, work))
            qman_portal_irq_enable(p);
    }

    return work;
}
```

Default budget is `NAPI_POLL_WEIGHT` (64). Tuned via ethtool `-C`.

### 7.2 RX path detail

```c
static void dpaa1_rx_one(struct dpaa1_napi *dn, const struct qm_dqrr_entry *dq)
{
    struct page *page = bm_addr_to_page(dn, qm_fd_addr(&dq->fd));
    void *va = page_address(page) + page_offset(dq->fd.bpid, page);
    struct fm_parse_result *pr = va;
    void *data = va + dn->buf_layout.data_offset;
    u32 len = dq->fd.length;
    struct sk_buff *skb;

    /* DMA sync: device wrote, CPU reads */
    dma_sync_single_for_cpu(dn->dev,
                            page_pool_get_dma_addr(page) + page_offset(...),
                            len + DPAA1_PARSE_RESULT_SIZE,
                            DMA_FROM_DEVICE);

    /* XDP first */
    if (READ_ONCE(dn->xdp_prog)) {
        u32 act = dpaa1_xdp_run(dn, page, data, len);
        switch (act) {
        case XDP_PASS:    break;
        case XDP_DROP:    goto recycle;
        case XDP_TX:      dpaa1_xdp_tx(dn, page, data, len); return;
        case XDP_REDIRECT: dpaa1_xdp_redirect(dn, page, data, len); return;
        default:          goto recycle;
        }
    }

    /* Build skb backed by page_pool page */
    skb = napi_build_skb(va, dn->buf_layout.frag_size);
    if (!skb) goto recycle;

    skb_reserve(skb, dn->buf_layout.data_offset);
    skb_put(skb, len);
    skb_mark_for_recycle(skb);  /* tells skb_release to return page to pool */
    skb_record_rx_queue(skb, dn->rx_idx);
    skb->protocol = eth_type_trans(skb, dn->netdev);

    /* Map FMan parse result -> skb metadata */
    if (pr->ip_pid == FM_PR_IPv4 || pr->ip_pid == FM_PR_IPv6)
        skb->ip_summed = pr->cksum_valid ? CHECKSUM_UNNECESSARY : CHECKSUM_NONE;
    if (pr->shimr & FM_PR_VLAN)
        __vlan_hwaccel_put_tag(skb, htons(ETH_P_8021Q), pr->vlan_tci);
    skb_set_hash(skb, pr->keygen_hash,
                 pr->ip_pid ? PKT_HASH_TYPE_L4 : PKT_HASH_TYPE_L3);

    napi_gro_receive(&dn->napi, skb);
    return;

recycle:
    page_pool_put_full_page(dn->page_pool, page, true);
}
```

### 7.3 Stashing tuning

Per-FQ stashing config: 1 + 2 + 1 cache lines (annotation + data + context). Numbers come from empirical profiling on the target CPU. Stashing config is set when the FQ is initialized via QMan `INIT` verb and cannot be changed without retire/reinit.

### 7.4 Buffer refill

After NAPI consumes packets, BMan needs replenishment. Refill happens lazily: at the end of each NAPI poll, if pool depth drops below threshold, allocate up to 8 pages from page_pool and release to BMan in a single RCR command. Eight-buffer batching is mandatory (see errata A-010165 and basic perf hygiene).

```c
static void dpaa1_refill_pool(struct dpaa1_napi *dn)
{
    int depth = bman_pool_depth(dn->bpid);
    int needed = dn->refill_target - depth;
    struct bm_buffer bufs[8];
    int i;

    while (needed >= 8) {
        for (i = 0; i < 8; i++) {
            struct page *p = page_pool_alloc_frag(dn->page_pool, &offset, ...);
            bm_buffer_set64(&bufs[i], page_to_phys(p) + offset);
        }
        bman_release(dn->bpid, bufs, 8);
        needed -= 8;
    }
}
```

---

## 8. TX data plane

### 8.1 ndo_start_xmit

```c
static netdev_tx_t dpaa1_start_xmit(struct sk_buff *skb, struct net_device *dev)
{
    struct dpaa1_priv *priv = netdev_priv(dev);
    int cpu = smp_processor_id();
    struct dpaa1_tx_q *txq = &priv->tx_qs[cpu];
    struct qm_fd fd;
    int err;

    if (skb_is_nonlinear(skb)) {
        err = build_sg_fd(priv, skb, &fd);
    } else {
        err = build_simple_fd(priv, skb, &fd);
    }
    if (unlikely(err)) goto drop;

    /* Stash skb pointer in FD opaque field for tx confirm */
    fd.cmd_status = (u32)txq_record_skb(txq, skb);

    err = qman_enqueue(txq->fq, &fd);
    if (unlikely(err)) {
        if (err == -EBUSY) {
            netif_tx_stop_queue(netdev_get_tx_queue(dev, cpu));
            return NETDEV_TX_BUSY;
        }
        goto drop;
    }

    return NETDEV_TX_OK;

drop:
    dev_kfree_skb_any(skb);
    dev->stats.tx_dropped++;
    return NETDEV_TX_OK;
}
```

Multiqueue: one txq per CPU. `dev->real_num_tx_queues = num_online_cpus()`. Default queue selection is `__netdev_pick_tx` (which uses the running CPU index by default), so packets stay on their producer CPU.

### 8.2 TX confirmation

A separate per-port confirmation FQ collects done frames. A NAPI instance for the confirm FQ runs on the CPU that owns the FQ's portal, drains FDs, looks up the original skb via the opaque field, and frees it.

```c
static int dpaa1_tx_confirm_poll(struct napi_struct *napi, int budget)
{
    /* Drain confirmation DQRR, free skbs, return S/G entries to per-CPU pool */
    while (work < budget) {
        const struct qm_dqrr_entry *dq = qman_portal_peek_dqrr(p);
        if (!dq) break;
        struct sk_buff *skb = txq_recover_skb(dq->fd.cmd_status);
        if (skb_is_sg_fd(&dq->fd)) free_sg_table(...);
        napi_consume_skb(skb, budget);
        qman_portal_consume_dqrr(p);
        work++;
    }
    /* ... */
}
```

### 8.3 XDP_TX

XDP_TX from RX path is enqueued back on the same port's TX FQ. The same buffer page is reused (no skb allocation, no copy). The TX confirm path detects "this FD came from XDP" via a flag bit in `cmd_status` and returns the page to page_pool instead of freeing an skb.

### 8.4 XDP_REDIRECT

Standard `xdp_do_redirect()` path. Target may be another DPAA1 port (in which case we hit XDP_TX path on the target), a different netdev (e.g., a tap or veth, packet goes through the kernel's generic redirect path), or an AF_XDP socket.

### 8.5 AF_XDP TX

When XSK is bound for TX, xsk completion ring entries map to FDs. The driver runs an XSK TX poll function:

```c
static int dpaa1_xsk_tx(struct dpaa1_napi *dn, int budget)
{
    struct xdp_desc desc;
    while (work < budget && xsk_tx_peek_desc(xsk, &desc)) {
        struct qm_fd fd;
        u64 dma = xsk_buff_raw_get_dma(xsk_pool, desc.addr);
        qm_fd_init(&fd, dma, 0, desc.len, dn->bpid);
        if (qman_enqueue(dn->tx_fq, &fd)) break;
        work++;
    }
    xsk_tx_release(xsk);
    return work;
}
```

---

## 9. PCD configuration and tc-flower offload

### 9.1 Default PCD (no `fsl-dpaa1-pcd.ko` loaded)

Without the optional PCD module, each port runs a hardcoded "L4 5-tuple hash → 8 RX FQs" profile. This is enough for normal forwarding and is what most users will have.

### 9.2 With `fsl-dpaa1-pcd.ko` loaded

The driver advertises `NETIF_F_HW_TC` and registers tc-flower offload callbacks. Supported keys in v1:

- `eth_dst`, `eth_src`, `eth_type`
- `vlan_id`, `vlan_priority`
- `ipv4_src`, `ipv4_dst`, `ipv6_src`, `ipv6_dst`
- `ip_proto`
- `tcp_src`, `tcp_dst`, `udp_src`, `udp_dst`

Supported actions:

- `drop` (CC entry with discard verdict)
- `pass` (default verdict)
- `redirect` to specific RX FQ (steers to a CPU)
- `mirred ingress redirect` to another DPAA1 port (uses FMan's offline parse for cross-port redirect; v1 limited support)

Mapping:

```
tc filter add dev fm0-mac1 ingress \
    protocol ip flower \
    dst_ip 10.0.0.0/8 ip_proto tcp dst_port 22 \
    action drop
```

becomes a CC entry on FMan's classifier table for that port. Match miss falls through to the default KeyGen path. v1 supports up to 256 CC entries per port (FMan CC table size).

### 9.3 ethtool RSS

```
ethtool -X fm0-mac1 hkey <key> hfunc toeplitz
ethtool -X fm0-mac1 equal 8
```

translates to KeyGen reconfiguration. Supported hash functions: Toeplitz (FMan native), CRC32 (FMan default). Indirection table size is 8 (matches default 8 RX FQs).

```
ethtool -N fm0-mac1 rx-flow-hash tcp4 sdfn
```

modifies KeyGen field selection for TCP/IPv4. Supported flow types: tcp4, udp4, tcp6, udp6, ah4, esp4, ah6, esp6, ip4, ip6.

### 9.4 ACL example

```
# Drop SSH from anywhere except management subnet
tc qdisc add dev fm0-mac1 ingress
tc filter add dev fm0-mac1 ingress prio 1 protocol ip flower \
    src_ip 192.168.0.0/16 dst_port 22 ip_proto tcp action pass
tc filter add dev fm0-mac1 ingress prio 2 protocol ip flower \
    dst_port 22 ip_proto tcp action drop
```

These rules execute at FMan, before any kernel CPU sees the packet. Throughput-irrelevant: the drop happens at line rate regardless of system load.

VyOS firewall config can opportunistically push compatible rules through tc-flower; rules that don't fit (stateful, complex) stay in nftables. Hybrid hardware/software firewall is a natural fit.

---

## 10. CAAM crypto integration

### 10.1 Job ring access

CAAM has its own driver in upstream (`drivers/crypto/caam/`). We reuse it. Job rings are independent of QMan/BMan/FMan and have their own register interface. Upstream `caam_jr` is functional; the gap is xfrm offload, which `fsl-dpaa1-caam.ko` provides.

### 10.2 xfrm offload

Register as a crypto offload device:

```c
static const struct xfrmdev_ops dpaa1_xfrmdev_ops = {
    .xdo_dev_state_add    = dpaa1_xfrm_state_add,
    .xdo_dev_state_delete = dpaa1_xfrm_state_delete,
    .xdo_dev_offload_ok   = dpaa1_xfrm_offload_ok,
    .xdo_dev_state_advance_esn = NULL,  /* v1: not supported */
};
```

Each offloaded SA gets a CAAM descriptor pre-built at state-add time. xmit path detects offload-eligible packets via `skb->sp` and submits to CAAM's job ring instead of the FMan TX FQ. CAAM completion is handled in soft IRQ; the crypted skb is then enqueued to FMan TX as a normal packet.

### 10.3 Algorithms

v1: AES-128-GCM, AES-256-GCM, AES-CBC + HMAC-SHA256 (legacy for IKEv1).
v1 deferred: ChaCha20-Poly1305 (CAAM does not accelerate; runs in software regardless).
v2: SHA-3 family if customers ask.

### 10.4 Throughput

CAAM on LS1046A reportedly does ~6 Gbps AES-GCM-128 with reasonable packet sizes. That's the offload ceiling; software AES-NI is irrelevant on ARM, so offload is effectively the only path to non-trivial IPsec throughput on this SoC.

---

## 11. AF_XDP zero-copy detail

### 11.1 Bind flow

When userspace calls `bind(AF_XDP, ...)` with `XDP_ZEROCOPY`:

1. Driver receives `XDP_SETUP_XSK_POOL` via `ndo_bpf`
2. Validate `xsk_pool->headroom`, frame size; reject if incompatible
3. Stop the target RX queue's NAPI
4. Drain BMan pool of page_pool buffers, return to page_pool
5. Convert UMEM frames to BMan buffers via `xsk_buff_alloc_batch` and release to BMan
6. Restart NAPI; from now on, dequeue produces UMEM-backed FDs
7. Driver handoff: instead of `napi_build_skb`, call `xsk_buff_set_size` and queue to socket

### 11.2 RX path with XSK

```c
struct xdp_buff *xdp = xsk_buff_alloc_from_dma_addr(xsk_pool, fd_addr);
xsk_buff_set_size(xdp, fd->length);
xsk_buff_dma_sync_for_cpu(xdp);

if (READ_ONCE(dn->xdp_prog)) {
    act = bpf_prog_run_xdp(dn->xdp_prog, xdp);
    /* XDP_REDIRECT to xsk: standard handling via xsk_redirect */
}
```

### 11.3 Socket queue handling

XSK fill queue: userspace pushes "free" UMEM descriptors. Driver consumes from fill queue when refilling BMan.

XSK rx queue: driver pushes received descriptors. Userspace consumes.

XSK tx queue: userspace pushes packets to send. Driver consumes in poll function and enqueues to FMan.

XSK completion queue: driver pushes after FMan TX confirm. Userspace consumes to know when UMEM frames are reusable.

### 11.4 Performance ceiling

AF_XDP zero-copy on LS1046A should reach ~22-24 Gbps small-packet, close to wire on 2×XFI. The bottleneck is FMan parse/classify, not the buffer path. This gives kernel-bypass performance to a userspace consumer of choice (DPDK app, custom packet processor) without replacing the kernel network stack wholesale.

---

## 12. Module structure and Kconfig

### 12.1 Kconfig

```
menuconfig FSL_DPAA1_NG
    bool "Freescale DPAA1 next-generation drivers"
    depends on ARM64 && OF
    help
      Modular, modprobe-friendly DPAA1 driver stack for LS1046A and
      LS1043A. Replaces the legacy CONFIG_FSL_DPAA driver family.
      Choose this OR the legacy stack, not both.

if FSL_DPAA1_NG

config FSL_BMAN_NG
    tristate "BMan portal driver"
    select FSL_BMAN_PORTAL_DEFAULTS

config FSL_QMAN_NG
    tristate "QMan portal driver"
    depends on FSL_BMAN_NG

config FSL_FMAN_NG
    tristate "FMan v3L driver"
    depends on FSL_QMAN_NG && FSL_BMAN_NG
    select FW_LOADER
    select PHYLINK

config FSL_DPAA1_ETH
    tristate "DPAA1 Ethernet driver"
    depends on FSL_FMAN_NG
    select PAGE_POOL
    select XDP_SOCKETS

config FSL_DPAA1_PCD
    tristate "tc-flower offload via FMan classifier"
    depends on FSL_DPAA1_ETH && NET_CLS_FLOWER

config FSL_DPAA1_CAAM
    tristate "IPsec/xfrm offload via CAAM"
    depends on FSL_DPAA1_ETH && CRYPTO_DEV_FSL_CAAM_JR
    select XFRM_OFFLOAD

endif
```

### 12.2 Source tree (out-of-tree first)

```
fsl-dpaa1-ng/
├── bman/
│   ├── bman.c
│   ├── bman_portal.c
│   ├── bman_test.c
│   └── Makefile
├── qman/
│   ├── qman.c
│   ├── qman_portal.c
│   ├── qman_fq.c
│   ├── qman_test.c
│   └── Makefile
├── fman/
│   ├── fman.c
│   ├── fman_muram.c
│   ├── fman_ucode.c
│   ├── fman_port.c
│   ├── fman_mac.c
│   ├── fman_pcd.c
│   ├── fman_kg.c
│   └── Makefile
├── eth/
│   ├── dpaa1_eth.c
│   ├── dpaa1_ethtool.c
│   ├── dpaa1_xdp.c
│   ├── dpaa1_xsk.c
│   ├── dpaa1_tx.c
│   ├── dpaa1_rx.c
│   └── Makefile
├── pcd/
│   ├── dpaa1_pcd.c
│   ├── dpaa1_flower.c
│   └── Makefile
├── caam/
│   ├── dpaa1_caam.c
│   ├── dpaa1_xfrm.c
│   └── Makefile
├── include/
│   └── linux/fsl/
│       ├── bman.h
│       ├── qman.h
│       └── fman.h
└── dkms.conf
```

For upstreaming, this becomes `drivers/net/ethernet/freescale/dpaa1-ng/` with the BMan/QMan parts moving to `drivers/soc/fsl/qbman-ng/` to match upstream's existing structure.

### 12.3 Coexistence with upstream `dpaa_eth`

Two strategies:

**Mutually exclusive (preferred for v1):** Kconfig conflicts; users pick one stack. VyOS picks `FSL_DPAA1_NG` for Mono Gateway, leaves others on legacy.

**Per-port handoff (for upstream):** New device tree property `fsl,driver = "dpaa1-ng";` on FMan MAC nodes lets the new driver claim specific ports while the legacy driver claims others. More work, only needed if upstream merges and existing users want gradual migration.

---

## 13. Configuration interface

### 13.1 Standard Linux interfaces (no custom config needed)

VyOS users get all of these from the netdev:

- `ip link`, `ip addr`, `ip route` (basic config)
- `ethtool -S/-G/-K/-C/-X/-N` (stats, ring, features, coalesce, RSS, NTUPLE)
- `tc qdisc/filter/class` (queueing, classification, offload)
- `nftables` / `iptables` (firewall, NAT)
- `bridge`, `bonding`, `vlan` (L2 services)
- `ip xfrm` (IPsec, with offload if `fsl-dpaa1-caam.ko` loaded)
- `ip link set ... xdp obj ...` (XDP attach)
- AF_XDP via standard sockets API

This is the entire point of this design. Nothing here is DPAA1-specific from the operator's perspective.

### 13.2 DPAA1-specific debugging surfaces

Sysfs (read-only):

```
/sys/class/net/<ifname>/dpaa1/
├── port_id
├── mac_id
├── fman_idx
├── rx_fqids
├── tx_fqids
├── bpid
├── parse_result_size
└── microcode_version
```

Debugfs (read-only, for kernel devs):

```
/sys/kernel/debug/dpaa1/
├── qman/
│   └── portal_<N>/
│       ├── eqcr_state
│       ├── dqrr_state
│       └── stats
├── bman/
│   └── portal_<N>/
│       ├── rcr_state
│       └── pool_<bpid>/
│           ├── depth
│           └── stats
├── fman/
│   ├── muram_map
│   ├── port_<N>/
│   │   ├── stats
│   │   └── pcd
└── eth/
    └── <ifname>/
        ├── napi_stats
        ├── xdp_stats
        └── xsk_stats
```

Devlink (when applicable):

```
devlink dev show
devlink port show
devlink dev info pci/0000:fsl_fman0   /* version, microcode rev */
devlink dev param set pci/0000:fsl_fman0 name parser_profile value l4_hash
```

v1 ships minimal devlink (info only). Devlink-based runtime config is v2.

### 13.3 VyOS integration

Most things require zero VyOS-side change because the netdev presents normally. The few additions:

- `set system dpaa1 worker-cpus 1-3` (CPU affinity hints, optional)
- `set system dpaa1 xdp-mode {none|native|generic}` (default native)
- `set system dpaa1 caam-offload {enable|disable}` (loads or not the CAAM module)

VyOS firewall and QoS subsystems opportunistically use tc-flower offload; this is invisible to the operator beyond a `show interfaces ethernet eth0 hardware-offload` summary.

### 13.4 Device tree

Reuses existing upstream `fsl,fman`, `fsl,qman`, `fsl,bman` bindings. One new optional property to choose driver stack:

```dts
fman@1a00000 {
    compatible = "fsl,fman";
    fsl,driver = "dpaa1-ng";    /* new property */
    /* ... */

    ethernet@e0000 {
        compatible = "fsl,fman-memac";
        /* ... */
    };
};
```

Without `fsl,driver`, behavior matches upstream legacy. With `"dpaa1-ng"`, the new stack claims the FMan and all its ports.

---

## 14. Build, packaging, upstreaming

### 14.1 DKMS package for VyOS

`vyos-dpaa1-ng-dkms`:
- Source under `/usr/src/dpaa1-ng-<ver>/`
- DKMS rebuild on kernel update
- Postinstall depmod and microcode firmware install

### 14.2 Firmware package

`vyos-dpaa1-firmware`:
- `/lib/firmware/fsl_fman_ucode_ls1046_*.bin`
- License from NXP firmware redistribution agreement
- Hashed and signed for reproducible builds

### 14.3 Upstream submission plan

12-18 month horizon after v1 ships:

**Stage 1**: BMan/QMan rework as `qbman-ng` under `drivers/soc/fsl/`. RFC patch series. Coexistence with legacy via Kconfig.

**Stage 2**: FMan rework. RFC. This is the contentious one because the upstream FMan code base is complex; reviewers will want detailed justification for not patching incrementally. Counter-argument: the diff would be larger than the rewrite, and the rewrite has features (RSS, tc offload) the legacy code lacks fundamentally.

**Stage 3**: Ethernet driver. Easier; XDP/AF_XDP/page_pool are all things upstream wants more of, and the legacy driver's lack of these is a known gap.

**Stage 4**: tc-flower offload, CAAM xfrm offload as separate series.

Realistic outcome: 50/50 chance of full upstreaming. Worst case, downstream VyOS-only forever, maintenance ~1 engineer-quarter/year.

---

## 15. Testing strategy

### 15.1 Unit tests

Per-module kernel selftests (`tools/testing/selftests/drivers/fsl-dpaa1-ng/`):

- BMan: pool acquire/release correctness, depletion handling
- QMan: FQ state transitions, EQCR/DQRR ring management
- FMan: microcode load round-trip, MURAM allocator
- Eth: skb/page_pool round-trip, XDP verdict handling, XSK bind/unbind

### 15.2 Hardware-in-the-loop

CI lab with at least one Mono Gateway board:

- **Smoke**: load all six modules in dependency order, verify netdevs appear, ping over each port
- **L2**: bridge two ports, iperf3 single-flow at 1G and 10G in both directions
- **L3**: route between ports, multi-flow via TRex generator
- **Stress**: 24-hour soak at line rate with packet capture sampling
- **Pool exhaustion**: deliberately undersized pool, verify graceful degradation (not deadlock)
- **CPU hotplug**: take CPUs offline and online during sustained traffic, verify FQ migration
- **Microcode**: verify version reporting, deliberate version mismatch produces clean error
- **Module unload/reload**: rmmod and modprobe in reverse and forward order, no leaks
- **XDP soak**: attach a redirect program for 24 hours, verify no buffer leaks
- **AF_XDP soak**: bind and rebind XSK sockets repeatedly under load, verify BPID swap correctness
- **tc-flower scale**: install 256 flower rules, verify all match correctly at line rate
- **IPsec soak**: 24-hour AES-GCM tunnel at offloaded throughput, verify no SA state corruption

### 15.3 Performance gates

CI fails if any of these regress more than 5%:

| Test | v1 target | Notes |
|------|-----------|-------|
| iperf3 TCP, single flow, 1500B | 9.0 Gbps | wire on 10G |
| Linux IP forwarding, 1500B, 4 cores | 18 Gbps | aggregate, multi-flow |
| Linux IP forwarding, 64B, 4 cores | 4.5 Mpps | aggregate |
| XDP_DROP, 64B, 4 cores | 14 Mpps | drop in driver |
| XDP_TX, 64B, 4 cores | 12 Mpps | bounce |
| XDP_REDIRECT, 64B, 4 cores | 11 Mpps | inter-port |
| AF_XDP zero-copy RX, 64B, 4 cores | 22 Mpps | wire on 2×XFI |
| nftables 1000-rule ACL, 1500B | 14 Gbps | software |
| tc-flower 1000-rule ACL, 1500B | 25 Gbps | offloaded |
| IPsec AES-GCM-128, 1500B | 6 Gbps | CAAM offloaded |

Numbers are estimates; first run on real hardware sets the actual baseline. Regressions >5% fail CI.

### 15.4 Bisect-friendliness

Each module independently buildable and loadable. Each commit in the eventual upstream series compiles and runs in isolation. CI bisects on perf regressions automatically.

---

## 16. Performance targets and analysis

### 16.1 Performance modes

This stack offers three operational modes, selected by what userspace does with the netdev:

**Standard kernel networking** (default): packets traverse skb path, full L2/L3/L4 kernel features. Saturates at ~18 Gbps aggregate L3 forwarding, ~4.5 Mpps small-packet. This is what nftables/conntrack/bridge users get.

**XDP fast path**: BPF program runs in driver before skb construction. XDP_DROP and XDP_TX hit ~12-14 Mpps small-packet. XDP_REDIRECT to another DPAA1 port hits ~11 Mpps. This is the path for software firewalls and load balancers that can be expressed in eBPF.

**AF_XDP zero-copy**: userspace owns RX/TX queues, zero copy between hardware and userspace. ~22 Mpps small-packet, close to wire on 2×XFI. This is the path for userspace packet processors (DPDK apps, custom code) that want bypass without owning the whole stack.

All three modes coexist. A single Mono Gateway can run kernel forwarding on six ports and AF_XDP on two without conflict.

### 16.2 Where to spend optimization budget

Highest-impact items, roughly in order:

1. Stashing tuned per port speed (cache prefetch correct for 1G vs 10G)
2. NAPI budget sized to match DQRR depth (16 entries per portal pull)
3. page_pool fragment caching to avoid `dma_sync` on recycled buffers
4. GRO enabled by default with FMan-provided hash
5. Receive packet steering disabled (FMan KeyGen already steers)
6. XPS configured to match TX FQ to CPU
7. Per-port BPID separation (avoid pool contention on shared workloads)

### 16.3 What kills performance

In rough order of damage if you get them wrong:

1. Stashing not configured (3× hit on L3 forwarding)
2. Buffer release not batched (5× hit on TX-heavy workload)
3. Single TX FQ shared across CPUs (cache line ping-pong, 2× hit)
4. RX FQ not pinned to portal channel (random-CPU dispatch, 1.5× hit)
5. Buffer not marked `skb_mark_for_recycle` (skb destructor frees the page instead of recycling, 2× slower)
6. RPS enabled (forces software re-steer over FMan's already-correct steer, 1.4× hit)
7. NAPI not pinned to portal CPU (cross-CPU portal access serializes, 2× hit)
8. Refill threshold too aggressive (causes BMan pool empty events at line rate)
9. DMA sync direction wrong (full sync vs cpu-only or device-only, 1.3× hit)
10. SMMU page table lookups not cached (TLB pressure, varies)

---

## 17. Phasing plan

### Phase 0: Foundation (3 months)
- BMan, QMan, FMan modules with microcode load
- "Hello world" raw FQ test program in kernel selftest
- Goal: prove modular structure works, microcode loads, packets traverse FMan

### Phase 1: Basic netdev (3 months)
- DPAA1 Ethernet driver with NAPI, page_pool, simple TX
- ethtool basic ops, phylink integration
- Goal: ping and iperf3 work on all 8 ports

### Phase 2: tc-flower offload and ethtool RSS (2 months)
- PCD module with CC table programming
- Flower parser, action translator
- ethtool RSS and NTUPLE via KeyGen
- Goal: 25 Gbps with 1000-rule ACL

### Phase 3: Native XDP (2 months)
- XDP with all four return codes
- XDP_REDIRECT
- Goal: hit XDP_DROP and XDP_TX targets

### Phase 4: AF_XDP zero-copy (2 months)
- XSK pool integration
- BMan reseed on bind/unbind
- TX from XSK descriptors
- Goal: 22 Mpps AF_XDP RX

### Phase 5: CAAM xfrm offload (2 months)
- xfrmdev_ops registration
- Per-SA descriptor cache
- Async completion plumbing
- Goal: 6 Gbps IPsec

### Phase 6: VyOS integration and hardening (2 months)
- DKMS package
- VyOS CLI hooks (the few that exist)
- Documentation
- Soak testing
- Goal: Mono Gateway ships

Total: ~16 months calendar, 2 engineers (one driver-focused, one networking-stack focused).

Phase 2 is intentionally before native XDP: tc-flower offload is the most visible win over upstream `dpaa_eth` and is what motivates VyOS migration. XDP and AF_XDP are tablestakes for modern net drivers but don't differentiate the stack on their own.

---

## 18. Open questions

1. **Page-pool fragment vs full-page buffers**: With 2 KiB buffers, two per page, DMA accounting gets fiddly. Alternatives: 4 KiB buffers (wastes memory but simpler), per-buffer pages with smaller MTU (limits jumbo). Resolve via prototype and profiling in Phase 1.

2. **AF_XDP BPID swap atomicity**: Switching a BPID from page_pool to UMEM mode requires draining FMan first. The CPU cost of drain-and-reseed on bind is non-trivial. Alternative: dedicate one BPID per RX queue per port permanently as "AF_XDP-eligible," accept some inefficiency when AF_XDP isn't bound. Decide in Phase 4.

3. **CAAM job ring sharing**: Upstream `caam_jr` allocates job rings to consumers somewhat arbitrarily. We need predictable allocation for xfrm offload. Either patch caam_jr or build a thin allocator on top. Investigate in Phase 5.

4. **Mainline path for FMan rewrite**: Upstream maintainers' tolerance for parallel implementations is uncertain. If RFC gets pushback in Stage 2, fallback is permanent downstream and that's fine for VyOS, less fine for community.

5. **CPU hotplug correctness**: NAPI per portal per CPU means hotplug needs portal migration. Either move FQs to a surviving portal (complex) or stop the affected NAPI and lose that queue's traffic until hotplug back (simpler, acceptable if rare). Default v1: stop and resume.

6. **XDP multi-buffer**: jumbo XDP requires `XDP_FLAGS_HAS_FRAGS`, which means S/G FDs in XDP path. Defer to v2.

7. **Switchdev fakery**: VyOS users may want bridge-offload semantics across DPAA1 ports. LS1046A has no switch, but FMan can do MAC-based steering between ports via OP ports. Worth a switchdev driver? Probably not for v1; revisit if Mono Gateway use cases demand it.

8. **Microcode redistribution**: confirm the FMan microcode license permits redistribution in a VyOS image. NXP's `linux-firmware` patches include it under a specific NXP license, not GPL. Need legal sign-off for VyOS package.

---

## Appendix A: Frame Descriptor reference

The 32-byte FD fields, format codes, and parse result structure are documented in:

- LS1046A Reference Manual, Chapter 8 (DPAA), §8.4 Frame Descriptor
- LS1046A Reference Manual, §8.7 Frame Manager Parse Results

Implementer should generate `struct qm_fd` and `struct fm_prs_result` from the manual and cross-check against the upstream `drivers/soc/fsl/qbman/qman.c` and `drivers/net/ethernet/freescale/fman/fman.h` for known-good bit layouts. Do not transcribe from memory or other secondary sources; the bit positions matter exactly.

Format codes:

| Format | Meaning |
|--------|---------|
| 0 | Single buffer, contiguous |
| 1 | Scatter/gather table |
| 2 | Long single buffer (>256 KiB, unused on this SoC) |
| 3-7 | Reserved |

`cmd_status` field carries:
- On RX: parse status, error flags, parse result offset hint
- On TX: software-defined opaque used for confirmation lookup
- On TX confirmation: completion status, error flags

## Appendix B: Portal register map

QMan portal CE region (per-CPU, 16 KiB, mapped cacheable WB):

| Offset | Size | Purpose |
|--------|------|---------|
| 0x0000 | 0x200 | EQCR (8 entries × 64 B) |
| 0x0200 | 0x400 | DQRR (16 entries × 64 B) |
| 0x0600 | 0x200 | MR (8 entries × 64 B) |
| 0x0800 | 0x200 | RCR (8 entries × 64 B) |
| 0x0A00 | 0x600 | Reserved / extended |

QMan portal CI region (per-CPU, 4 KiB, mapped device-nGnRnE):

| Offset | Size | Purpose |
|--------|------|---------|
| 0x0000 | 0x40 | EQCR producer/consumer |
| 0x0040 | 0x40 | DQRR producer/consumer + verb |
| 0x0080 | 0x40 | MR producer/consumer |
| 0x00C0 | 0x40 | RCR producer/consumer |
| 0x0E00 | 0x100 | Status, IRQ enable/disable |

BMan portal layout is structurally similar (CE for RCR + ACR, CI for doorbells/status) but smaller; full layout in LS1046A RM §8.6.

Generate header constants from the manual; do not paraphrase the offsets above into code without verifying against the manual page-by-page.

## Appendix C: Module dependency chain

Kernel modprobe deps (resolved automatically via `MODULE_SOFTDEP`):

```
fsl-dpaa1-eth → fsl-fman → fsl-qman → fsl-bman
fsl-dpaa1-pcd → fsl-fman
fsl-dpaa1-caam → caam_jr (upstream)
```

Boot-time load order via `/etc/modules-load.d/dpaa1.conf`:

```
fsl-bman
fsl-qman
fsl-fman
fsl-dpaa1-eth
fsl-dpaa1-pcd
fsl-dpaa1-caam
```

Removal works in reverse (rmmod refuses while in use).

## Appendix D: ethtool feature matrix

| Feature | Status | Mechanism |
|---------|--------|-----------|
| `rx-checksum` | Yes | FMan validates IPv4/TCP/UDP checksums |
| `tx-checksum-ip-generic` | Yes | FMan computes |
| `rx-vlan` | Yes | FMan parses, strips into `__vlan_hwaccel_put_tag` |
| `tx-vlan` | Yes | FMan inserts |
| `tso` | Yes | FMan TSO engine, IPv4 TCP only in v1 |
| `gso` | Software | Standard kernel |
| `gro` | Software | Standard kernel, GRO uses FMan hash |
| `rxhash` | Yes | FMan KeyGen, ethtool `-X` configurable |
| `ntuple` | Yes (with PCD) | FMan CC tables |
| `hw-tc-offload` | Yes (with PCD) | FMan CC + KeyGen |
| `rx-fcs` | No | Not exposed by FMan |
| `rx-all` | No | FMan filters at MAC |
| `xdp` | Yes | Native |
| `xdp-redirect` | Yes | Native |
| `xdp-zerocopy` | Yes | XSK |

---

**End of v0.2 spec.**

Build order recommendation: Phase 0 → Phase 1 → Phase 2 (tc-flower) → Phase 3 (XDP) → Phase 4 (AF_XDP) → Phase 5 (CAAM) → Phase 6.
