# Vendor FMD ioctl ABI (`fm_port_ioctls.h`) — what `.110`'s `/dev/fm0*` exposes

**2026-08-26 · Source of `decomp/vendor-fmd/fm_port_ioctls.h` (963 lines), and why
it does NOT give us a way to read the vendor's live ehash record on `.110`.**

## Provenance

The file `decomp/vendor-fmd/fm_port_ioctls.h` is fetched verbatim from the
**vendor's own fastpath fmlib**:

- Repo: `we-are-mono/opnsense-deps`
- Path: `fastpath/fmlib/include/fmd/Peripherals/fm_port_ioctls.h`
- Commit: `95fd8558bf98d290d37a1b17f3ed55abc56f5bc9`
- Upstream lineage: Freescale/NXP FMD (Frame Manager Driver) userspace ABI,
  `FMD_API_VERSION 21.1.0`.

This is the **exact ABI the `.110` Mono board exposes** on its FMD character
devices (`/dev/fm0`, `/dev/fm0-pcd`, `/dev/fm0-port-{rx,tx,oh}N`, majors 246).
`mihakralj/kernel-ls1046a-build` carries byte-identical copies of the companion
`fm_ioctls.h` / `fm_pcd_ioctls.h`, and the version stamp matches — so this is
authoritative for `.110`.

## Why we went looking for it

`.110` runs the original vendor ASK 1.0 (OpenWrt 6.12.103, CDX + cmm) and
**sustains hardware VLAN offload** on the same LS1046A silicon where ASK2
freezes at ~22 packets. To find what the vendor does differently, we wanted to
read its **live FE/ehash record and FQ wiring**. Two blockers:

1. `.110` has `CONFIG_DEVMEM=n` — `/dev/mem` open returns `ENXIO` even after
   `mknod`. No arbitrary physical reads.
2. No on-board compiler / kernel headers, and building the OpenWrt kernel to get
   a vermagic-matching `Module.symvers` for a custom read-module is a
   multi-hour job.

So the FMD chardev ioctl ABI was the remaining candidate register/memory window.
This header (plus its `fm_ioctls.h` / `fm_pcd_ioctls.h` siblings) defines that
entire ABI.

## The decisive negative: no arbitrary CCSR/MURAM read exists

**The FMD ioctl ABI has no generic "read register at offset X" or "read memory"
call.** It is a *typed* create/modify/delete + *counter-getter* interface only.
Nothing dumps a KeyGen scheme's extraction layout, a CC/hash-table key's bytes,
an ehash record's opcode list, or an FE descriptor. Confirmed across all three
headers:

- `fm_ioctls.h` (FM level, `/dev/fm0`): `FM_IOC_GET_REVISION`,
  `FM_IOC_GET_COUNTER` (QMI enq/deq/confirm totals), `FM_IOC_CTRL_MON_*`
  (FMan-controller CPU utilisation %). No register read.
- `fm_pcd_ioctls.h` (`/dev/fm0-pcd`): `FM_PCD_IOC_GET_COUNTER` (KG total,
  policer colours, parser stats), `FM_PCD_IOC_KG_SCHEME_GET_CNTR` (per-scheme
  SPC — **but only for a scheme handle created on the same fd**, so it cannot
  inspect schemes the kernel/CDX programmed independently),
  `FM_PCD_IOC_MATCH_TABLE_GET_KEY_STAT` / `..._MISS_STAT` /
  `HASH_TABLE_GET_MISS_STAT` (per-key/miss **counters** only — no key bytes,
  no next-engine).
- `fm_port_ioctls.h` (this file, `/dev/fm0-port-*`): everything below.

Consequence: to read actual FMan CCSR/MURAM state (KG scheme AR/AC, BMI bind,
ehash record, FE descriptors) you **must** use `/dev/mem`+mmap — which works on
our own DUT (`.185`, e.g. `pcd-snapshot`, `hash_probe`) but is **impossible on
`.110`**. The vendor's live ehash record bytes are therefore not readable with
the tooling that exists on `.110`; that specific unknown stays closed unless we
build a vermagic-matched OpenWrt kernel module.

## What `fm_port_ioctls.h` actually provides

### Encoding

```c
#define NCSW_IOC_TYPE_BASE 0xe0
#define FM_IOC_TYPE_BASE   (NCSW_IOC_TYPE_BASE+1)   /* 0xe1 — all FM/PCD/PORT ioctls */
#define FM_PORT_IOC_NUM(n) ((n)+70)                 /* port ioctl NR namespace */
```
Port ioctls open `/dev/fm0-port-rx<n>` / `-tx<n>` / `-oh<n>` and use plain
`ioctl(fd, CMD, &struct)`.

### The two useful *read* getters (no /dev/mem needed)

1. **`FM_PORT_IOC_GET_BMI_COUNTERS`** `_IOR(0xe1, FM_PORT_IOC_NUM(42),
   ioc_fm_port_bmi_stats_t)` — the BMI statistics block per port:
   ```c
   typedef struct ioc_fm_port_bmi_stats_t {
     uint32_t cnt_cycle, cnt_task_util, cnt_queue_util, cnt_dma_util,
              cnt_fifo_util, cnt_rx_pause_activation;
     uint32_t cnt_frame;                       /* frames processed          */
     uint32_t cnt_discard_frame;               /* BMI discards              */
     uint32_t cnt_dealloc_buf;                 /* buffers returned to BMan   */
     uint32_t cnt_rx_bad_frame, cnt_rx_large_frame, cnt_rx_filter_frame,
              cnt_rx_list_dma_err, cnt_rx_out_of_buffers_discard,
              cnt_wred_discard, cnt_length_err, cnt_unsupported_format;
   } ioc_fm_port_bmi_stats_t;
   ```
   `cnt_frame` vs `cnt_discard_frame` vs `cnt_dealloc_buf` on the vendor's Rx
   port under VLAN load is a real, readable signal for how the vendor's forward
   path accounts frames/buffers — the closest ABI proxy for "is it draining
   cleanly."

2. **`FM_PORT_IOC_GET_MAC_STATISTICS`** `_IOR(0xe1, FM_PORT_IOC_NUM(41),
   ioc_fm_port_mac_statistics_t)` — full RMON/MIB-II MAC counters (in/out
   octets/pkts/ucast/mcast/bcast/discards/errors, pause frames, size buckets).
   Line-rate ground truth per MAC.

3. **`FM_PORT_IOC_GET_MAC_FRAME_SIZE_COUNTERS`** `FM_PORT_IOC_NUM(43)` — per
   size-bucket frame counts.

### The `ioc_fm_port_counters` enum (what BMI/QMI counters exist)

`e_IOC_FM_PORT_COUNTERS_*`: BMI perf (CYCLE, TASK/QUEUE/DMA/FIFO_UTIL,
RX_PAUSE_ACTIVATION), BMI stats (FRAME, DISCARD_FRAME, **DEALLOC_BUF**,
RX_BAD/LARGE/FILTER_FRAME, RX_LIST_DMA_ERR, **RX_OUT_OF_BUFFERS_DISCARD**,
PREPARE_TO_ENQUEUE, WRED_DISCARD, LENGTH_ERR, UNSUPPORTED_FORMAT), and QMI
(DEQ_TOTAL, ENQ_TOTAL, DEQ_FROM_DEFAULT, **DEQ_CONFIRM**). `DEALLOC_BUF`,
`RX_OUT_OF_BUFFERS_DISCARD` and `DEQ_CONFIRM` are the ones relevant to the
ASK2 "frames consumed but never released / never confirmed" hypothesis.

### The rest of the header (control, not readback)

Everything else is *configuration*, and confirms the vendor's PCD model:
`FM_PORT_IOC_SET_PCD` (`ioc_fm_port_pcd_params_t`: parser + KG + CC + policer +
IP-reassembly manip per port), `..._ALLOC_PCD_FQIDS` (per-port FQID range),
`..._PCD_KG_BIND/UNBIND_SCHEMES`, `..._PCD_CC_MODIFY_TREE`,
`..._VSP_ALLOC` (virtual storage profiles), rate-limit, congestion-groups,
Tx pause frames, hash-MAC filter. Notably the parser VLAN options
(`vlan_prs_options.tag_protocol_id1/2`, `set_vlan_tpid1/2`) show the vendor
drives VLAN through the standard PCD parser+KG+CC path — i.e. the vendor's
offload is the SDK FMD/PCD pipeline (parser→KG→CC→policer + reassembly OH
ports), **not** the inline-opcode FE-VM ehash record ASK2 builds. This matches
the live `.110` harvest (139 pcd FQs + 16 tx FQs per eth port on CPU-pool
channels 6–9, oh1/oh2 = reassembly, two-stage FQ path with context stashing).

## Net conclusion for ASK2

- **Tooling answer:** the FMD ioctl ABI cannot read the vendor's live ehash
  record / FE descriptors. Only BMI/MAC/QMI **counters** and PCD **stats** are
  reachable without `/dev/mem` (which is compiled out on `.110`). The remaining
  read path is a vermagic-matched OpenWrt kernel module (heavy, not yet built).
- **Architecture answer (from the header + live harvest):** the vendor uses the
  **SDK FMD/PCD pipeline** (parser→KeyGen→CC→policer, OH ports for reassembly,
  per-port FQID ranges, VSPs, two-stage CPU-pool-channel FQs with stashing).
  ASK2 instead uses a bespoke **inline-opcode FE-VM ehash record** that enqueues
  **directly** to a single no-confirm TX FQ. That architectural gap — not any
  single record field — is the leading remaining explanation for why the
  vendor sustains VLAN and ASK2 freezes.

## Files

- `decomp/vendor-fmd/fm_port_ioctls.h` — the verbatim vendor header (963 lines).
- Companion ABI (magic base `0xe1`, `FM_IOC_*` / `FM_PCD_IOC_*` getters) is in
  the FMD `fm_ioctls.h` / `fm_pcd_ioctls.h` (kernel UAPI; not vendored here, but
  documented above and in the 2026-08-26 qdrant record).
