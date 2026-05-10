# VPP Native Plugin for NXP LS1046A DPAA1

**Status:** Draft v0.1
**Target hardware:** NXP LS1046A (Cortex-A72 ×4, FMan v3L, QMan v3, BMan v3)
**Target software:** VPP 24.10+, Linux kernel 6.6+, ARM64
**License intent:** Apache 2.0 (VPP-compatible)

---

## 1. Scope

A VPP input/output plugin that drives the LS1046A DPAA1 datapath directly, without DPDK, USDPAA, or NXP's `fmlib`/`fmc`. The plugin owns QMan and BMan portals from userspace, configures FMan ports and PCD via a small kernel helper, and feeds packets into VPP's vector graph using the native buffer format.

### 1.1 Goals

- Single dependency stack: VPP + this plugin + thin kernel helper. No DPDK, no NXP SDK.
- Wire-rate L3 forwarding on 2×XFI + 6×SGMII (~26 Gbps aggregate, 64-byte packets).
- Per-worker portal ownership, lockless fast path.
- Hugepage-backed buffer pool with one-time BMan seeding.
- XDP-style header offsets so VPP buffer metadata maps directly to FMan parse results.
- Reproducible builds, no vendor blobs except the FMan microcode (redistributable).

### 1.2 Non-goals (v1)

- DPAA2, LS1088, LX2160 support
- IEEE 1588 PTP timestamping
- DCB / PFC / lossless Ethernet
- Hardware classification beyond VLAN, MAC, basic 5-tuple
- ONIC / direct-attached storage offload
- Kernel netdev coexistence on the same FMan port (the plugin takes the port exclusively)

### 1.3 Out-of-scope but planned

- CAAM crypto offload as a separate VPP IPsec backend plugin (Section 11)
- Optional kernel slow-path netdev on a dedicated FMan port for management traffic
- Live FQ rebalance for CPU hotplug

---

## 2. Hardware reference

### 2.1 Block topology on LS1046A

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

### 2.2 Per-CPU portals

Each Cortex-A72 has its own QMan portal and BMan portal mapped at fixed physical addresses. The portal is split into two MMIO regions:

| Region | Cacheability | Purpose |
|--------|--------------|---------|
| CE (Cache-Enabled) | Normal cacheable, write-back | Command rings, DQRR, EQCR, RCR |
| CI (Cache-Inhibited) | Device-nGnRnE | Doorbells, status, interrupt regs |

The CE region is sized 16 KiB per portal; CI is 4 KiB. Userspace must `mmap` both with the correct memory attributes. The kernel helper module exposes `/dev/dpaa1/qman_portal_<N>` and `/dev/dpaa1/bman_portal_<N>` character devices that produce the right mappings via `mmap()` callbacks.

### 2.3 Ring structures

**QMan EQCR (Enqueue Command Ring):** 8 entries × 64 B. Software writes a frame descriptor + verb, increments producer index, rings doorbell. Hardware drains and decrements consumer.

**QMan DQRR (Dequeue Response Ring):** 16 entries × 64 B. Hardware writes dequeue responses (containing FD + FQID + sequence number); software polls consumer index, processes, advances.

**QMan MR (Message Ring):** 8 entries × 64 B. Async events (FQ state changes, congestion notifications). Polled at slow path.

**BMan RCR (Release Command Ring):** 8 entries × 64 B. Release up to 8 buffers per command (acquire is similar via separate command path).

All rings use cyclic indices with a "vbit" toggle so software detects new entries without per-entry zeroing.

### 2.4 FMan structure

One FMan instance with the following resources:

- **MURAM**: 384 KiB shared RAM for FMan internal state. Statically partitioned at init.
- **Ports**: 8 RX ports + 8 TX ports + 2 OP (offline parse) ports. Each maps to one MAC.
- **Microcode**: ~16 KiB binary blob loaded into FMan at init. Provides parse/classify pipeline. NXP-redistributable.
- **Parser**: Software-configurable parse tree (Ethernet, VLAN, IPv4, IPv6, TCP, UDP, GRE, custom).
- **KeyGen**: Extracts hash keys from parse results for distribution.
- **Policer**: Two-rate three-color marker per port and per flow.
- **CC (Coarse Classifier)**: Lookup tables for steering.

### 2.5 Frame Descriptor (FD)

The 32-byte FD is the unit of work between software and hardware. Layout (little-endian on LS1046A):

```c
struct qm_fd {
    union {
        struct {
            uint8_t  dd:2;       /* dynamic debug */
            uint8_t  liodn_offset:6;
            uint8_t  bpid;       /* buffer pool ID */
            uint8_t  eliodn_offset:4;
            uint8_t  reserved:4;
            uint8_t  addr_hi;    /* phys addr [39:32] */
            uint32_t addr_lo;    /* phys addr [31:0] */
        };
        uint64_t opaque_addr;
    };
    uint32_t format:3;       /* 0=single buf, 1=S/G, 2=long single */
    uint32_t offset:9;       /* data offset within buffer */
    uint32_t length:20;      /* data length OR S/G entries */
    uint32_t cmd_status;     /* tx confirmation, rx parse status */
} __attribute__((packed, aligned(8)));
```

For RX, the offset points to the start of the L2 header; FMan writes a parse result block before that, in the headroom. For TX, software sets format=0, addr=phys(packet_start - headroom), offset=headroom, length=packet_len.

### 2.6 Stashing

QMan can pre-fetch the start of incoming packet data into L1 or L2 cache before raising the dequeue response. Configured per-FQ via `stash_lines` (number of 64 B lines) and `stash_data_l1`/`stash_data_l2` flags. Critical for hitting line rate; the difference between "stashing tuned" and "stashing off" is roughly 3× in L3 forwarding throughput on this SoC.

### 2.7 Errata to handle

The LS1046A errata list (chip rev 1.0 and 1.1) includes ~30 DPAA-related items. Must-implement for v1:

- A-009885 (FMan): RX FIFO threshold tuning per port speed
- A-010022 (QMan): EQCR consumer index read after enqueue write
- A-010165 (BMan): minimum 8-buffer release granularity below pool depletion threshold
- A-010379 (FMan v3L): parser stall on certain VLAN+IPv6 ext header combinations, requires SP override

The full list lives in the LS1046A chip errata document; the implementer should walk it and tag each as applicable/not-applicable to userspace consumption. Several PowerPC-era errata in NXP's code do not apply to A72.

---

## 3. Software architecture

### 3.1 Component layout

```
                +--------------------------------------+
                |              VPP process             |
                |  +--------------------------------+  |
                |  |       dpaa1_plugin.so          |  |
                |  |  +---------+  +-------------+  |  |
                |  |  | input   |  | output      |  |  |
                |  |  | node    |  | node        |  |  |
                |  |  +---------+  +-------------+  |  |
                |  |  +-------------------------+   |  |
                |  |  | libdpaa1_um (userspace) |   |  |
                |  |  |  - QMan portal driver   |   |  |
                |  |  |  - BMan portal driver   |   |  |
                |  |  |  - FD encode/decode     |   |  |
                |  |  |  - Buffer recycling     |   |  |
                |  |  +-------------------------+   |  |
                |  +--------------------------------+  |
                +--------------------------------------+
                                  |
                          ioctl / mmap
                                  |
                +--------------------------------------+
                |             Linux kernel             |
                |  +--------------------------------+  |
                |  |      fsl-dpaa1-um.ko           |  |
                |  |  - Portal mmap + IRQ relay     |  |
                |  |  - FMan init + microcode load  |  |
                |  |  - Port config (MAC bind, PCD) |  |
                |  |  - FQ/BPID allocation          |  |
                |  |  - Hugepage IOMMU mapping      |  |
                |  +--------------------------------+  |
                |  +--------------------------------+  |
                |  |   reused upstream drivers      |  |
                |  |  - phylink, mdio, mdio-mux     |  |
                |  |  - of_iommu, smmu-v2 (PAMU)    |  |
                |  +--------------------------------+  |
                +--------------------------------------+
                                  |
                +--------------------------------------+
                |     Hardware: FMan / QMan / BMan     |
                +--------------------------------------+
```

### 3.2 Module boundaries

**`fsl-dpaa1-um.ko`** (kernel, ~3 kLOC C):
- Probes DPAA1 nodes from device tree
- Loads FMan microcode from `/lib/firmware/fsl_fman_ucode_ls1046_*.bin`
- Initializes FMan ports, MAC binding, PHY linkage (delegates to phylink)
- Allocates FQ ID ranges, BPID ranges, channel IDs
- Programs PAMU (LS1046A's IOMMU) for userspace hugepage access
- Exposes per-portal char devices for `mmap`
- Exposes ioctl for FQ create/destroy, BPID create, PCD program
- Forwards portal interrupts to userspace via `eventfd`

**`libdpaa1_um.so`** (userspace, ~6 kLOC C):
- `mmap`s portal regions
- Implements EQCR push, DQRR poll, RCR release
- Buffer pool manager backed by hugepages
- FD encode/decode helpers
- No VPP dependencies; reusable by other userspace apps

**`dpaa1_plugin.so`** (VPP plugin, ~3 kLOC C):
- Registers VPP graph nodes (input, output, error)
- Maps VPP `vlib_buffer_t` to libdpaa1 buffer slots
- Implements `interface_tx` callback
- VAT / CLI bindings
- Statistics counters

### 3.3 Worker model

VPP runs N worker threads pinned to CPUs 1..N (CPU 0 is main thread / control). Each worker exclusively owns:

- One QMan portal
- One BMan portal
- A subset of RX FQs (load-distributed by FMan KeyGen hash)
- One TX FQ per FMan port (workers share TX ports via per-worker FQ)

No locking on the data path. Cross-worker traffic uses VPP's existing handoff infrastructure.

---

## 4. Memory model

### 4.1 Buffer layout

Single-segment packet buffers, 2048 bytes total, aligned to 64 B (cache line):

```
+---------------------------+  offset 0
| FMan parse result (64 B)  |
+---------------------------+  offset 64
| VPP vlib_buffer_t (128 B) |
+---------------------------+  offset 192
| Headroom (64 B)           |
+---------------------------+  offset 256  <-- FD.offset
| Packet data (up to 1792)  |
+---------------------------+  offset 2048
```

The FD `offset` field always points to byte 256. FMan writes parse results to bytes 0-63 of the buffer (configurable via `prs_result_offset` per port). VPP `vlib_buffer_t` lives at bytes 64-191; the plugin sets `current_data` and `current_length` on RX so VPP graph nodes see the packet starting at byte 256.

### 4.2 Hugepage-backed pool

One 1 GiB hugepage per worker, mapped MAP_POPULATE | MAP_HUGETLB. Each page holds (1 GiB / 2 KiB) = 524288 buffers. v1 sizing: 64 K buffers per worker pool, leaving headroom for VPP's own pools and IPsec working memory.

The hugepage is mapped to userspace, then registered with the kernel helper which programs PAMU to make the same range DMA-accessible from FMan. PAMU operates with 4 KiB granularity but works on contiguous physical regions; hugepages give us large contiguous regions trivially.

### 4.3 PAMU configuration

LS1046A uses PAMU (Peripheral Access Management Unit) for IOMMU-style isolation. Each DPAA1 LIODN (Logical I/O Device Number) needs a window programmed to access userspace buffers. Kernel helper:

1. Locks the hugepage in memory (`mlock` from userspace, then `pin_user_pages` in kernel)
2. Programs a PAMU PAACE entry mapping the hugepage virtual range to its physical range
3. Sets the LIODN of the FMan port to use this PAACE

Without PAMU programming, FMan DMA accesses fault. NXP's SDK does this via fmlib's quirky ioctl path; we replace it with a clean `DPAA1_REGISTER_BUFFER_REGION` ioctl that takes (vaddr, size, liodn) and returns success/fail.

### 4.4 Buffer accounting

BMan tracks free buffers in pool, but software tracks "in-flight" (handed to FMan but not yet returned). Per-worker stats:

- `pool_free`: BMan pool depth (read from BMan portal)
- `inflight_rx`: buffers currently held by FMan ingress
- `inflight_tx`: buffers handed to FMan TX, awaiting confirmation
- `vpp_held`: buffers held in VPP graph nodes between input and tx

Pool exhaustion triggers congestion: FMan drops at MAC, increments per-port drop counter. The plugin polls BMan depletion notifications and logs.

---

## 5. Resource model

### 5.1 ID allocations

Resource IDs are assigned at init time by the kernel helper and exposed via sysfs:

| Resource | Range | Per-instance |
|----------|-------|--------------|
| FQ IDs | 0x100 - 0xFFFF | ~64 K |
| BPIDs | 8 - 63 | 56 (0-7 reserved) |
| Channels | 0x21 - 0x2F | 15 (one per portal + spares) |
| CGR IDs | 0 - 255 | 256 |

Default v1 layout for a 4-worker config:

- 4 BPIDs (one per worker pool)
- 8 RX FQs per port × 8 ports = 64 RX FQs
- 4 TX FQs per port × 8 ports = 32 TX FQs (one per worker per port)
- 4 portals (one per worker)

### 5.2 Frame Queue states

FQs progress through states: OOS → SCHED → PARKED. Operations:

- `INIT`: OOS → SCHED, sets channel, work queue, stashing, congestion group
- `RETIRE`: SCHED → PARKED, drains pending frames
- `OOS`: destroy FQ

The kernel helper owns all FQ state changes via ioctl; userspace just enqueues/dequeues. This isolates the most error-prone bit (FQ state machine bugs are a major source of NXP SDK pain) and keeps it auditable from kernel logs.

### 5.3 Channel scheduling

Each portal has a dedicated channel. Each FQ schedules into one channel. FMan's KeyGen hashes parse results and steers to one of N FQs in the same channel group, distributing load across workers.

---

## 6. Initialization sequence

### 6.1 Boot order

1. **Kernel boot**: Standard Linux boot, device tree describes DPAA1 nodes
2. **`fsl-dpaa1-um.ko` loads**:
   a. Probe `fsl,qman` node, map registers, initialize QMan
   b. Probe `fsl,bman` node, map registers, initialize BMan
   c. Probe `fsl,fman` node, allocate MURAM, load microcode
   d. For each MAC node: bind to FMan port, attach phylink
   e. Allocate FQ/BPID/channel ranges
   f. Create char devices
3. **VPP starts**:
   a. Plugin reads `/sys/class/dpaa1/` for resource inventory
   b. Plugin opens portal char devices for each worker
   c. Plugin allocates hugepage pools, registers via ioctl
   d. Plugin requests FQ creation for each (worker, port, direction)
   e. Plugin requests PCD program (parse + KeyGen + distribution)
   f. Plugin seeds BMan pools with buffers
   g. Plugin issues `PORT_ENABLE` ioctl for each port
4. **Link up**: phylink brings up MACs, FMan starts feeding RX FQs

### 6.2 Microcode load

```c
const struct firmware *fw;
request_firmware(&fw, "fsl_fman_ucode_ls1046_r1.0_106_4_18.bin", dev);
/* validate header, version, checksum */
fman_load_ucode(fman, fw->data, fw->size);
release_firmware(fw);
```

Microcode version is logged at probe; mismatch with FMan revision is a hard error. v1 supports microcode 106.4.18 (current as of LSDK 21.08). Newer microcode versions add features (fragmentation/reassembly, IPSec lookup) we don't use in v1.

### 6.3 PCD program for v1

Parse profile: Ethernet → VLAN (optional) → IPv4/IPv6 → TCP/UDP. Two pre-built profiles:

- `default_l3`: parse to L3, hash on (src_ip, dst_ip), distribute across N RX FQs
- `default_l4`: parse to L4, hash on 5-tuple, distribute across N RX FQs

KeyGen extracts the configured fields, computes CRC32, masks to log2(N) bits, indexes the FQ table.

CC (coarse classifier) is unused in v1. Policers are unused in v1.

---

## 7. RX data plane

### 7.1 Per-worker poll loop

```c
static uword
dpaa1_input_node_fn(vlib_main_t *vm, vlib_node_runtime_t *node,
                    vlib_frame_t *frame)
{
    dpaa1_per_worker_t *pw = &dpaa1_main.workers[vm->thread_index];
    u32 n_rx = 0;
    u32 bi[VLIB_FRAME_SIZE];
    vlib_buffer_t *b[VLIB_FRAME_SIZE];

    /* Drain DQRR up to VLIB_FRAME_SIZE */
    while (n_rx < VLIB_FRAME_SIZE) {
        const struct qm_dqrr_entry *dq = qman_portal_peek_dqrr(pw->qman);
        if (!dq) break;

        /* Buffer is already in our hugepage region, parsed by FMan,
         * stashed into L2 cache. Just translate addr and pull metadata. */
        u64 paddr = qm_fd_addr(&dq->fd);
        void *vaddr = paddr_to_vaddr(pw, paddr);
        vlib_buffer_t *vb = vaddr + VLIB_BUFFER_OFFSET;

        vb->current_data = 0;
        vb->current_length = dq->fd.length;
        vb->flags = VLIB_BUFFER_TOTAL_LENGTH_VALID;
        /* Map FMan parse result -> VPP buffer flags */
        dpaa1_parse_to_vpp_flags(vaddr, vb);

        bi[n_rx] = vlib_get_buffer_index(vm, vb);
        b[n_rx] = vb;
        n_rx++;

        qman_portal_consume_dqrr(pw->qman);
    }

    if (n_rx == 0) return 0;

    /* Hand off to next graph node (typically ethernet-input) */
    vlib_buffer_enqueue_to_next(vm, node, bi, next_indices, n_rx);

    vlib_increment_combined_counter(...);
    return n_rx;
}
```

### 7.2 Stashing tuning

Per-FQ stashing config:

```c
struct fq_stash_config {
    u8 annotation_lines; /* parse result lines to stash: 1 (64 B) */
    u8 data_lines;       /* packet data lines to stash: 2 (128 B) */
    u8 context_lines;    /* FQ context lines: 1 (64 B) */
};
```

Values determined empirically. 1+2+1 = 4 cache lines per packet covers parse result + first 128 B of data + FQ context. Going higher costs cache pressure; going lower causes L1 misses on Ethernet header processing.

### 7.3 Buffer recycling

After VPP processes the packet (forward, drop, terminate), the buffer must return to BMan. Three return paths:

- **TX**: handed to TX FQ; FMan returns to BMan after transmit completes (or via TX confirmation FQ if we want explicit confirmation)
- **Free**: VPP releases via plugin's free callback, which batches into BMan RCR releases of 8
- **Drop**: same as free

Batching releases (up to 8 per RCR command) is mandatory for performance. Single-buffer release is 5× slower per packet.

### 7.4 Parse result mapping

FMan parse result (64 B per buffer) contains:

- Frame status (errors, parse stops)
- L2/L3/L4 protocol identifiers
- L3 offset, L4 offset
- Hash result
- VLAN tag info

Mapping to VPP `vnet_buffer_t`:

```c
vnet_buffer(vb)->l2_hdr_offset = pr->l2_offset;
vnet_buffer(vb)->l3_hdr_offset = pr->l3_offset;
vnet_buffer(vb)->l4_hdr_offset = pr->l4_offset;
if (pr->shimr & FM_PR_VLAN) vb->flags |= VNET_BUFFER_F_L2_HDR_OFFSET_VALID;
if (pr->ip_pid == FM_PR_IPv4) vb->flags |= VNET_BUFFER_F_IS_IP4;
/* etc */
```

This skips ethernet-input's parsing for packets FMan already classified, saving roughly 30 cycles per packet.

---

## 8. TX data plane

### 8.1 Per-worker TX

```c
VNET_DEVICE_CLASS_TX_FN(dpaa1_device_class)(vlib_main_t *vm,
                                            vlib_node_runtime_t *node,
                                            vlib_frame_t *frame)
{
    dpaa1_per_worker_t *pw = &dpaa1_main.workers[vm->thread_index];
    u32 *from = vlib_frame_vector_args(frame);
    u32 n_left = frame->n_vectors;
    u32 n_tx = 0;

    while (n_left > 0) {
        struct qm_fd fd;
        vlib_buffer_t *b = vlib_get_buffer(vm, from[0]);

        /* Build FD pointing at buffer */
        u64 paddr = vaddr_to_paddr(pw, b);
        qm_fd_init(&fd, paddr - VLIB_BUFFER_OFFSET,
                   VLIB_BUFFER_OFFSET + b->current_data,
                   b->current_length, pw->bpid);

        if (qman_portal_enqueue(pw->qman, pw->tx_fqid[port], &fd) == 0) {
            n_tx++;
            from++;
            n_left--;
        } else {
            /* EQCR full: ring doorbell, retry */
            qman_portal_eqcr_kick(pw->qman);
            /* spin or yield; v1 spins for max 100 cycles then drops */
        }
    }

    qman_portal_eqcr_kick(pw->qman);
    return n_tx;
}
```

### 8.2 TX confirmation

Two modes:

- **No-confirm**: FMan releases buffer to BMan after transmit. Simplest, lowest overhead. Buffer must be fully described by FD (no software-only state needed post-TX).
- **Confirm**: FMan enqueues a confirmation FD to a confirmation FQ. Plugin polls the confirm FQ, runs cleanup.

v1 uses no-confirm exclusively. Confirm mode is needed for things like TX timestamping or zero-copy from non-pool memory, neither of which v1 supports.

### 8.3 Scatter-gather

v1: not supported. All packets must fit in a single 2048 B buffer (1792 B usable). MTU capped at 1500 with appropriate headroom.

v2: S/G FD format with up to 8 segments. Required for jumbo frames and for VPP's chained buffers from IPsec ESP encap.

### 8.4 Multi-queue TX

Each worker has its own TX FQ per port. Different workers transmit to the same MAC without contention; FMan TX port serializes at the MAC. Per-FQ congestion groups detect MAC backpressure and signal the worker to drop or queue.

---

## 9. PCD configuration

### 9.1 Parse-Classify-Distribute pipeline

```
ingress -> Parser -> KeyGen -> Distribution -> CC (optional) -> Policer (optional) -> FQ
```

v1 uses Parser + KeyGen + Distribution. CC and Policer are no-ops.

### 9.2 Parser

Parser is microcode-driven; the configuration tells it where to start (Ethernet) and which extensions to chase. v1 enables:

- Ethernet (always)
- VLAN single + double tag
- IPv4 with options
- IPv6 with hop-by-hop, routing, fragment ext headers
- TCP, UDP, ICMP, ICMPv6, GRE, ESP

Soft Parser (custom protocols) is not used in v1. NXP's `fmc` tool exists exclusively to compile XML descriptions of Soft Parser into microcode patches; we omit it entirely.

### 9.3 KeyGen

KeyGen hashes selected fields from parse result. v1 ships two profiles:

```c
/* Profile: l3_hash */
struct kg_profile l3_hash = {
    .fields = KG_FIELD_SRC_IP | KG_FIELD_DST_IP,
    .hash_shift = 0,
    .num_fqs = 8,           /* 2^3 */
    .base_fqid = ...,
};

/* Profile: l4_hash (5-tuple) */
struct kg_profile l4_hash = {
    .fields = KG_FIELD_SRC_IP | KG_FIELD_DST_IP |
              KG_FIELD_SRC_PORT | KG_FIELD_DST_PORT |
              KG_FIELD_PROTO,
    .hash_shift = 0,
    .num_fqs = 8,
    .base_fqid = ...,
};
```

Profile is selected per port at config time.

### 9.4 PCD ioctl interface

```c
struct dpaa1_pcd_config {
    u32 port_id;
    u32 profile;        /* DPAA1_PCD_L3_HASH | DPAA1_PCD_L4_HASH */
    u32 num_rx_fqs;
    u32 base_fqid;
    u32 default_fqid;   /* for unparseable frames */
};

#define DPAA1_IOCTL_PCD_PROGRAM _IOW('d', 0x10, struct dpaa1_pcd_config)
```

The kernel helper translates this into FMan parser/KeyGen register writes. v1 has two profiles, so the implementation is essentially two static tables.

---

## 10. CAAM crypto integration

CAAM is a separate accelerator with its own interface (job rings, not QMan FQs on LS1046A). v1 does not integrate CAAM.

v2 plan: separate `crypto_caam_plugin.so` that registers as a VPP crypto engine. Job ring access via the existing kernel `caam` driver's userspace interface, or direct mmap if performance demands. Synchronous and asynchronous operation modes. IPsec ESP, AES-GCM, ChaCha20-Poly1305 (note: ChaCha20 is software-only on this chip, CAAM does not accelerate it).

Out of scope for this document; specced separately.

---

## 11. VPP plugin structure

### 11.1 Files

```
src/plugins/dpaa1/
├── plugin.c           /* VLIB_PLUGIN_REGISTER, init */
├── device.c           /* dpaa1_device_t lifecycle */
├── input.c            /* RX node */
├── output.c           /* TX function */
├── format.c           /* CLI formatters */
├── cli.c              /* CLI commands */
├── api.c              /* VAT API */
├── dpaa1_api.api      /* API definition */
├── node.h
├── dpaa1.h
└── CMakeLists.txt
```

### 11.2 Node graph

```
                            +--------------+
                            | dpaa1-input  |
                            +--------------+
                                   |
                                   v
                  +-------------------------------+
                  | (parse-result fast path)      |
                  | ip4-input-no-checksum  ←→     |
                  | ip6-input              ←→     |
                  | ethernet-input (fallback) ←→  |
                  +-------------------------------+
                                   ...
                                   v
                            +--------------+
                            | dpaa1-output |
                            +--------------+
```

`dpaa1-input` has multiple `next_index` slots. The plugin steers each packet based on FMan parse result: IPv4 directly to `ip4-input-no-checksum` (FMan validated checksum), IPv6 to `ip6-input`, anything weird to `ethernet-input`. Saves the ethernet parse for the common case.

### 11.3 CLI

```
vpp# show hardware-interfaces
              Name                Idx   Link  Hardware
fm0-mac1                            1     up   fm0-mac1
  Link speed: 10 Gbps
  Ethernet address 00:04:9f:11:22:33
  DPAA1 backend, FQ range 0x200-0x207, BPID 8

vpp# set interface state fm0-mac1 up
vpp# set interface ip address fm0-mac1 192.0.2.1/24
vpp# show dpaa1 stats fm0-mac1
  rx packets: 12345678
  rx bytes:   8765432109
  rx drops:   42
  tx packets: 12345000
  ...

vpp# show dpaa1 portal worker 0
  QMan portal: CPU 1
  EQCR: pi=4 ci=4 (idle)
  DQRR: pi=11 ci=11 entries=0
  RCR:  pi=2 ci=2
  ...
```

### 11.4 Startup config (`startup.conf`)

```
plugins {
    plugin dpaa1_plugin.so { enable }
}

dpaa1 {
    num-rx-queues-per-port 8
    pcd-profile l4_hash
    hugepage-size 1G
    buffers-per-pool 65536
    enable-stashing
}

cpu {
    main-core 0
    workers 3
    corelist-workers 1-3
}
```

---

## 12. Configuration interface

### 12.1 Sysfs inventory (read-only)

```
/sys/class/dpaa1/
├── chip_revision
├── microcode_version
├── fman0/
│   ├── ports/
│   │   ├── mac1/
│   │   │   ├── speed
│   │   │   ├── liodn
│   │   │   └── available
│   │   └── ...
├── qman/
│   ├── num_portals
│   ├── fqid_range
│   └── channel_range
└── bman/
    ├── num_portals
    └── bpid_range
```

### 12.2 Char devices

```
/dev/dpaa1/
├── ctrl                  /* control channel for ioctls */
├── qman_portal_0..3      /* per-CPU QMan portals */
├── bman_portal_0..3      /* per-CPU BMan portals */
└── fman0                 /* FMan management */
```

### 12.3 Ioctls

| Ioctl | Direction | Payload | Purpose |
|-------|-----------|---------|---------|
| `DPAA1_FQ_CREATE` | W | `struct dpaa1_fq_init` | Create and init FQ |
| `DPAA1_FQ_DESTROY` | W | `u32 fqid` | Retire and OOS |
| `DPAA1_BPID_CREATE` | W | `struct dpaa1_bpid_init` | Allocate BPID |
| `DPAA1_BUFFER_REGION_REGISTER` | W | `struct dpaa1_buf_region` | Register hugepage with PAMU |
| `DPAA1_PCD_PROGRAM` | W | `struct dpaa1_pcd_config` | Apply parse/KeyGen |
| `DPAA1_PORT_ENABLE` | W | `u32 port_id` | Start FMan port |
| `DPAA1_PORT_DISABLE` | W | `u32 port_id` | Stop FMan port |
| `DPAA1_IRQ_REGISTER` | RW | `struct dpaa1_irq_reg` | Get eventfd for portal IRQ |

All ioctls validate caller capabilities (`CAP_NET_ADMIN` minimum) and resource ownership.

### 12.4 Device tree bindings

Reuses existing `fsl,fman`, `fsl,qman`, `fsl,bman` bindings from upstream. No new bindings required for v1; the plugin and kernel helper consume what's already defined.

Adds one optional property to FMan port nodes:

```
fsl,userspace-managed;
```

When present, the kernel helper takes the port for userspace consumption (no kernel netdev). Without it, the port stays bound to the upstream `dpaa_eth` driver. This lets a single LS1046A run kernel networking on one MAC and VPP on the rest.

---

## 13. Build and packaging

### 13.1 Kernel module

Out-of-tree first, in-tree later if VyOS upstream interest exists.

```
fsl-dpaa1-um/
├── Kbuild
├── Makefile
├── dpaa1_main.c
├── fman_init.c
├── pamu.c
├── portal_dev.c
├── pcd.c
├── ioctl.c
└── include/uapi/linux/fsl_dpaa1_um.h  /* user-facing */
```

DKMS package for VyOS image build. CI matrix tests against kernel 6.6 LTS, 6.12 LTS, latest.

### 13.2 Userspace library

Standard autotools or Meson. Distributes:

- `libdpaa1_um.so.1`
- `libdpaa1_um-dev` headers
- `dpaa1-tools` (introspection: `dpaa1-portal-stat`, `dpaa1-fq-list`)

### 13.3 VPP plugin

Built as part of VPP plugin tree, either upstreamed or as out-of-tree plugin loaded via `plugin_path`. CMake target produces `dpaa1_plugin.so`.

### 13.4 VyOS integration

VyOS package `vyos-dpaa1`:
- pulls kernel module via DKMS
- ships `libdpaa1_um.so` and `dpaa1_plugin.so`
- ships VPP startup config snippet
- VyOS CLI hooks under `set system dpaa1 ...` for selecting profile, worker count, port assignment

---

## 14. Testing strategy

### 14.1 Unit tests (userspace)

- FD encode/decode round-trip
- Buffer pool alloc/free correctness under concurrent access (TSAN)
- Mock QMan portal that exercises EQCR/DQRR semantics
- PCD config serialization

### 14.2 Hardware-in-the-loop

CI lab with at least one Mono Gateway board:

- **Smoke**: load module, init plugin, ping over each port
- **L2**: bridge two ports, iperf3 single-flow at 1G and 10G
- **L3**: forward between ports, multi-flow via TRex
- **Stress**: 24-hour soak at line rate with packet capture sampling
- **Pool exhaustion**: deliberately undersized pool, verify graceful degradation
- **Hotplug**: CPU offline/online during traffic
- **Microcode**: verify versions, deliberate mismatch produces clean error

### 14.3 Performance gates

CI fails if any of these regress more than 5%:

| Test | v1 target | Comment |
|------|-----------|---------|
| L3 forwarding 64B, 1 flow, 1 worker | 4.0 Mpps | single QMan portal limit |
| L3 forwarding 64B, 8 flows, 4 workers | 14.0 Mpps | wire on 2×XFI |
| L3 forwarding 1500B, 4 workers | 25 Gbps | wire on 2×XFI |
| End-to-end latency p50, 1500B | < 12 µs | poll mode |
| End-to-end latency p99 | < 30 µs | excludes BMan refill |

These numbers are estimates; first-cut measurement on real hardware will set the actual baseline.

### 14.4 Fuzz / robustness

- Malformed FDs from a deliberately broken peer (verify FMan error paths)
- Random PCD configs via property-based testing
- Buffer pool starvation injection
- IRQ storm simulation

---

## 15. Performance targets

### 15.1 v1

- 14 Mpps L3 forwarding aggregate (64 B), 4 workers
- 25 Gbps line rate for 1500 B
- < 30 µs p99 latency
- < 1 W extra power vs. kernel networking baseline (poll mode tax)

### 15.2 Headroom analysis

LS1046A QMan can process roughly 16 Mpps aggregate across 4 portals. FMan can parse/classify ~14 Mpps with full v3L pipeline at 800 MHz. MAC ports cap at 14.88 Mpps for 2×XFI (64 B). The bottleneck is FMan; QMan headroom exists for slightly larger packets (jumbo) where FMan parse time amortizes.

### 15.3 What kills performance

In rough order of damage if you get them wrong:

1. Stashing not configured (3× hit)
2. Buffer release not batched (5× hit on TX-heavy workload)
3. Single TX FQ shared across workers (cache line ping-pong, 2× hit)
4. PAMU misconfigured forcing bounce buffers (10× hit, traffic basically stops)
5. RX FQ not pinned to portal channel (random-CPU dispatch, 1.5× hit)
6. NAPI-style budget instead of full DQRR drain (1.3× hit)

---

## 16. Phasing plan

### Phase 0: Foundation (3 months)
- Kernel helper module: portal mmap, FMan init, microcode load
- Userspace library: portal driver, buffer pool
- "Hello world" userspace program: receive 1 packet, echo back
- Goal: prove the userspace bypass path works end-to-end

### Phase 1: Basic VPP plugin (3 months)
- `dpaa1-input` and TX function
- One port, one worker, no PCD beyond default
- L2 bridge between two ports at any rate
- Goal: VPP runs, packets move

### Phase 2: Multi-worker + PCD (3 months)
- 4-worker config with KeyGen distribution
- L3/L4 hash profiles
- All 8 ports usable
- Performance tuning: stashing, batching, prefetch
- Goal: hit 80% of v1 perf targets

### Phase 3: VyOS integration (2 months)
- VyOS package, DKMS, CLI
- Documentation
- CI lab with Mono Gateway in loop
- Goal: VyOS image builds with plugin, end-to-end provisioning works

### Phase 4: Hardening (2 months)
- Errata coverage audit
- Stress and soak testing
- Hot-path optimization based on profiling
- Goal: production-ready for Mono Gateway shipments

### Phase 5 (deferred): CAAM, jumbo, S/G, PTP

Total: ~13 months calendar time, 2 engineers (one kernel-leaning, one userspace/VPP-leaning).

---

## 17. Open questions

1. **PAMU vs SMMU coexistence**: LS1046A has both PAMU (legacy, for DPAA1) and SMMU-500 (for non-DPAA peripherals). Confirm that our PAMU programming doesn't conflict with the kernel's SMMU usage for other PCIe devices.

2. **Microcode redistribution**: Confirm the FMan microcode license permits redistribution in a VyOS image. NXP's `linux-firmware` patches include it under a specific NXP license, not GPL. Need legal sign-off for VyOS package.

3. **Slow path coexistence**: Should we support `dpaa_eth` on one port and our plugin on another simultaneously? This requires QMan/BMan resource partitioning between kernel and userspace consumers. Adds complexity; v1 leans toward "no, all ports are userspace if the plugin is loaded."

4. **CPU isolation strategy**: VPP wants exclusive CPUs (`isolcpus`); VyOS users may not. Document the trade-off and support both via startup.conf.

5. **Hugepage allocation timing**: 1 GiB hugepages must be reserved at boot via `hugepagesz=1G hugepages=N` cmdline. Mono Gateway image needs to bake this in. 2 MiB hugepages would be more flexible but require many more PAMU windows; v1 sticks with 1 GiB.

6. **Multi-FMan future-proofing**: LS1046A has one FMan, but the design should accommodate LS1043A (one FMan, fewer ports) and LS1088A (no FMan, DPAA2 instead, out of scope but the plugin abstraction shouldn't preclude a sibling DPAA2 plugin).

7. **VPP buffer allocator integration**: VPP has its own buffer allocator. Decide whether DPAA1 buffers participate in VPP's pool (cleaner integration but more code) or stay in their own pool with explicit handoff (simpler, possibly faster). v1 starts with separate pools.

8. **AF_PACKET fallback**: For debugging or when the plugin fails to init, do we fall back to kernel `dpaa_eth` automatically? Probably no in v1; explicit user choice via VyOS config.

---

## Appendix A: Frame Descriptor reference

Layout, fields, format codes, and parse result structure are documented in:

- LS1046A Reference Manual, Chapter 8 (DPAA), §8.4 Frame Descriptor
- LS1046A Reference Manual, §8.7 Frame Manager Parse Results

Implementer should cross-check against the upstream `drivers/soc/fsl/qbman/qman.c` for known-good bit layouts before fabricating struct definitions.

## Appendix B: Portal register map

QMan portal CE region (per-CPU, 16 KiB):
- `0x0000-0x01FF`: EQCR (8 × 64 B)
- `0x0200-0x05FF`: DQRR (16 × 64 B)
- `0x0600-0x07FF`: MR (8 × 64 B)
- `0x0800-0x09FF`: RCR (8 × 64 B)
- ...

Full layout in LS1046A RM §8.5.6. Do not paraphrase from memory; copy from the manual into a header file once and treat as canonical.

## Appendix C: Kconfig and module dependencies

```
config FSL_DPAA1_UM
    tristate "NXP LS1046A DPAA1 userspace consumer"
    depends on ARM64 && OF
    depends on FSL_QMAN_PORTAL
    depends on FSL_BMAN_PORTAL
    select FW_LOADER
    help
      Kernel helper for userspace VPP plugin consuming DPAA1.

      This driver claims FMan ports marked with the
      "fsl,userspace-managed" device tree property and exposes
      portal regions to userspace via /dev/dpaa1/*.
```

Module dependency chain (modprobe-resolvable):

```
fsl-dpaa1-um  →  fsl_qman, fsl_bman, fsl_fman, of_iommu, phylink
```

All upstream modules; no out-of-tree dependencies beyond `fsl-dpaa1-um.ko` itself.

---

**End of v0.1 spec.**

Next steps: get this in front of a kernel-networking reviewer and a VPP plugin reviewer, walk Section 17 to closure, then start Phase 0.
