# FMD Shim Kernel Module — Implementation Specification

> **Status (2026-04-04):** 🚧 **SKELETON IMPLEMENTED.** Chardevs + GET_API_VERSION ioctl built into kernel via `data/kernel-patches/fsl_fmd_shim.c` + `data/kernel-config/ls1046a-fmd-shim.config` + `bin/ci-setup-kernel.sh` injection. PCD/PORT ioctls return -ENOSYS (pending KG register programming). Full 8-ioctl spec below is the target for Phase 3.

## Overview

A minimal kernel module (`fsl_fmd_shim`) that creates `/dev/fm0*` character devices
and translates DPDK fmlib ioctls into direct FMan CCSR register writes. This enables
the DPDK DPAA PMD "FMCLESS" path for runtime RSS/distribution configuration without
the full NXP SDK `fmd` driver (~30K LOC).

**Scope**: ~2000 lines. 8 ioctls. No microcode interaction (U-Boot handles that).

---

## Architecture

```
┌──────────────┐          ┌──────────────────┐        ┌──────────────────┐
│  DPDK fmlib  │──ioctl──▶  fsl_fmd_shim     │──MMIO──▶  FMan CCSR Regs │
│  (userspace) │          │  (kernel module) │        │  (0x1a00000)     │
└──────────────┘          └──────────────────┘        └──────────────────┘
       │                           │
  open("/dev/fm0")       ioremap(0x1a00000)
  open("/dev/fm0-pcd")   Program KG/Parser/CC
  open("/dev/fm0-port-rx0")
```

**What it does NOT do:**
- Load/modify FMan microcode (U-Boot injects from SPI flash `mtd4`)
- Manage buffer pools or frame queues (USDPAA module handles this)
- Create network interfaces (mainline `fman` driver handles this)
- Handle interrupts or DMA (FMan hardware is autonomous)

---

## Character Devices

| Device | Purpose | Created by |
|--------|---------|------------|
| `/dev/fm0` | FMan control (API version query) | Module init |
| `/dev/fm0-pcd` | PCD engine (KeyGen, parser, classifier config) | Module init |
| `/dev/fm0-port-rx0` | RX port 0 (MAC2, eth0) | Module init |
| `/dev/fm0-port-rx1` | RX port 1 (MAC5, eth1) | Module init |
| `/dev/fm0-port-rx2` | RX port 2 (MAC6, eth2) | Module init |
| `/dev/fm0-port-rx3` | RX port 3 (MAC9, eth3 SFP+) | Module init |
| `/dev/fm0-port-rx4` | RX port 4 (MAC10, eth4 SFP+) | Module init |

Port chardevs use misc_register with dynamic minor numbers.

---

## Ioctl ABI (8 ioctls required)

All ioctls use magic byte `0xe1` (`FM_IOC_TYPE_BASE = NCSW_IOC_TYPE_BASE + 1`).
Numbering: `FM_IOC_NUM(n)=n`, `FM_PCD_IOC_NUM(n)=n+20`, `FM_PORT_IOC_NUM(n)=n+70`.

### 1. FM_IOC_GET_API_VERSION

- **Device**: `/dev/fm0`
- **Direction**: `_IOR(0xe1, 7, ioc_fm_api_version_t)` — 4 bytes out
- **Struct**:
  ```c
  typedef union {
      struct { uint8_t major; uint8_t minor; uint8_t respin; uint8_t reserved; } version;
      uint32_t ver;
  } ioc_fm_api_version_t;
  ```
- **Implementation**: Return hardcoded `{ .major=21, .minor=1, .respin=0 }`.
  DPDK fmlib checks `major >= 21 && minor >= 1`. No register access needed.

### 2. FM_PCD_IOC_NET_ENV_CHARACTERISTICS_SET

- **Device**: `/dev/fm0-pcd`
- **Direction**: `_IOWR(0xe1, 40, ioc_fm_pcd_net_env_params_t)` — in+out
- **Purpose**: Define which protocol headers the parser should recognize
- **Key fields used by DPDK**:
  ```c
  struct {
      uint8_t num_of_distinctions;    // Number of header types
      struct {
          e_NetHeaderType hdr;        // e.g. HEADER_TYPE_IPV4, HEADER_TYPE_TCP
          e_FmPcdHdrIndex hdr_index;  // 0 for most
      } units[FM_PCD_MAX_NUM_OF_DISTINCTION_UNITS]; // DPDK sets ~8 entries
      void *id;                       // OUT: opaque handle returned by kernel
  } ioc_fm_pcd_net_env_params_t;
  ```
- **DPDK sets these headers**: ETH, VLAN, IPv4, IPv6, TCP, UDP, SCTP, GRE
- **Implementation**: Store the header list. Return a unique handle (pointer/index).
  No register writes needed at this stage — this defines the "vocabulary" for
  subsequent KeyGen schemes.

### 3. FM_PCD_IOC_KG_SCHEME_SET

- **Device**: `/dev/fm0-pcd`
- **Direction**: `_IOWR(0xe1, 24, ioc_fm_pcd_kg_scheme_params_t)` — in+out
- **Purpose**: Create a KeyGen hash distribution scheme (THE core RSS config)
- **Key fields used by DPDK**:
  ```c
  struct {
      bool modify;                    // false for new scheme
      union {
          struct { void *net_env_id; } new_params; // handle from NET_ENV_SET
      } scm_id;
      bool always_direct;             // false
      struct {
          uint8_t num_of_used_extracts;   // Number of hash key fields
          struct {
              e_FmPcdExtractType type;    // e_FM_PCD_EXTRACT_BY_HDR
              struct {
                  e_NetHeaderType hdr;    // HEADER_TYPE_IPV4
                  e_FmPcdHdrIndex index;
                  e_FmPcdExtractFrom src; // e_FM_PCD_EXTRACT_FULL_FIELD
                  struct {
                      e_FmPcdHdrIndex ipv4; // e_FM_PCD_HDR_INDEX_NONE
                      e_IPF field;          // IP_SRC / IP_DST
                  } full_field;
              } extract_by_hdr;
          } extracts_array[FM_PCD_KG_MAX_NUM_OF_EXTRACTS_PER_KEY]; // Up to 16
      } key_extract_and_hash_params;
      uint32_t base_fqid;             // Base frame queue ID for distribution
      uint8_t num_of_used_extracted_ors; // 0
      bool use_hash;                  // true
      uint16_t hash_dist_num_of_fqids; // Number of FQs to distribute across (4)
      void *id;                       // OUT: scheme handle
  } ioc_fm_pcd_kg_scheme_params_t;
  ```
- **DPDK configures these hash fields**:
  1. IPv4 src address (`HEADER_TYPE_IPV4, IP_SRC`)
  2. IPv4 dst address (`HEADER_TYPE_IPV4, IP_DST`)
  3. IPv6 src address (`HEADER_TYPE_IPV6, IP_SRC`) (if requested)
  4. IPv6 dst address (`HEADER_TYPE_IPV6, IP_DST`) (if requested)
  5. TCP src port (`HEADER_TYPE_TCP, TCP_PORT_SRC`)
  6. TCP dst port (`HEADER_TYPE_TCP, TCP_PORT_DST`)
  7. UDP src port (`HEADER_TYPE_UDP, UDP_PORT_SRC`)
  8. UDP dst port (`HEADER_TYPE_UDP, UDP_PORT_DST`)
  9. Protocol field (`HEADER_TYPE_IPV4, IP_PROTO`) — for L3-only distribution
- **hash_dist_num_of_fqids**: Set to `DPAA_MAX_NUM_PCD_QUEUES` (4 typically)
- **base_fqid**: Per-port, assigned from QMan FQID allocator
- **Implementation**: This is the CRITICAL ioctl. Must program FMan KeyGen registers:
  - Write key extraction config to KG scheme registers
  - Set hash function parameters
  - Configure FQID base + distribution range
  - FMan CCSR KeyGen block at `FMan_base + 0x80000`
  - Each scheme uses a scheme register set (LS1046A has up to 32 schemes)

### 4. FM_PCD_IOC_KG_SCHEME_DELETE

- **Device**: `/dev/fm0-pcd`
- **Direction**: `_IOW(0xe1, 26, ioc_fm_pcd_obj_t)` — handle in
- **Purpose**: Delete/free a previously created KG scheme
- **Implementation**: Clear the scheme registers, free the scheme index

### 5. FM_PCD_IOC_ENABLE

- **Device**: `/dev/fm0-pcd`
- **Direction**: `_IO(0xe1, 21)` — no data
- **Purpose**: Enable the PCD engine globally
- **Implementation**: Set PCD enable bit in FMan control registers

### 6. FM_PCD_IOC_DISABLE

- **Device**: `/dev/fm0-pcd`
- **Direction**: `_IO(0xe1, 22)` — no data
- **Purpose**: Disable PCD engine
- **Implementation**: Clear PCD enable bit

### 7. FM_PORT_IOC_SET_PCD

- **Device**: `/dev/fm0-port-rxN`
- **Direction**: `_IOW(0xe1, 73, ioc_fm_port_pcd_params_t)` — params in
- **Purpose**: Bind a PCD tree (schemes, CC nodes) to an RX port
- **Key fields**:
  ```c
  struct {
      e_FmPortPcdSupport pcd_support;  // e_FM_PORT_PCD_SUPPORT_PRS_AND_KG
      void *net_env_id;                // handle from NET_ENV_SET
      struct {
          void *first_scheme_id;       // handle from KG_SCHEME_SET
      } kg_params;
      void *prs_params;               // NULL for basic config
  } ioc_fm_port_pcd_params_t;
  ```
- **Implementation**: Write port PCD control registers to point at the scheme

### 8. FM_PORT_IOC_DELETE_PCD

- **Device**: `/dev/fm0-port-rxN`
- **Direction**: `_IO(0xe1, 74)` — no data
- **Purpose**: Unbind PCD from port (cleanup)
- **Implementation**: Clear port PCD control registers

---

## FMan CCSR Register Map (LS1046A)

FMan base: `0x1a00000` (from DTB `fsl,fman` node).
Total region: 2MB (`0x1a00000` – `0x1bfffff`).

| Block | Offset | Size | Purpose |
|-------|--------|------|---------|
| FPM (Frame Processing Manager) | `+0x00000` | 4KB | Global control, events |
| BMI (Buffer Manager Interface) | `+0x00400` | 1KB | BMI global config |
| QMI (Queue Manager Interface) | `+0x00800` | 1KB | QMI global config |
| Parser | `+0x80800` | 1KB | Parse result, soft parser |
| KeyGen (KG) | `+0x80000` | 2KB | Scheme registers (RSS config) |
| Policer | `+0xC0000` | 4KB | Policer profiles |
| mEMAC ports | `+0xE0000`+ | Per-MAC | Port-specific PCD binding |

### KeyGen Scheme Registers (most critical for RSS)

Each scheme occupies 64 bytes. LS1046A supports up to 32 schemes.
Scheme base: `KG_base + scheme_index * 0x40`.

Key registers per scheme:
- `KG_SCHEME_CFG` (+0x00): Enable, hash mode, default FQID
- `KG_SCHEME_EXTRACT_0..7` (+0x04..0x20): Key extraction config
  - Specifies header type, field offset, field size for each extract
- `KG_SCHEME_HASH_CFG` (+0x24): Hash function configuration
- `KG_SCHEME_FQ_BASE` (+0x28): Base FQID for distribution
- `KG_SCHEME_FQ_MASK` (+0x2C): FQ distribution mask (num_fqs - 1)

---

## Module Structure

```c
// fsl_fmd_shim.c

#include <linux/module.h>
#include <linux/miscdevice.h>
#include <linux/io.h>
#include <linux/of.h>

#define FMAN_CCSR_BASE    0x1a00000
#define FMAN_CCSR_SIZE    0x200000

#define FM_IOC_TYPE_BASE  0xe1

// Per-FD context
struct fmd_shim_ctx {
    enum { FMD_FM, FMD_PCD, FMD_PORT } type;
    int port_index;              // for PORT type
    void *allocated_schemes[32]; // track per-fd allocations
    int scheme_count;
};

// Global state
struct fmd_shim_global {
    void __iomem *fman_regs;
    struct miscdevice fm_dev;
    struct miscdevice pcd_dev;
    struct miscdevice port_devs[5]; // 5 RX ports on LS1046A
    // Net env tracking
    struct { ... } net_envs[4];
    int net_env_count;
    // Scheme tracking
    uint32_t scheme_bitmap;        // 32 schemes, bit=1 means allocated
    struct { ... } schemes[32];
};

static long fmd_fm_ioctl(struct file *, unsigned int, unsigned long);
static long fmd_pcd_ioctl(struct file *, unsigned int, unsigned long);
static long fmd_port_ioctl(struct file *, unsigned int, unsigned long);

// Per-FD cleanup on close — free all schemes/net_envs allocated by this fd
static int fmd_release(struct inode *inode, struct file *filp) { ... }
```

---

## Build Integration

### Kernel patch: `data/kernel-patches/9002-fmd-shim-chardev.patch`

Adds:
- `drivers/soc/fsl/fmd_shim/fsl_fmd_shim.c` (~2000 lines)
- `drivers/soc/fsl/fmd_shim/Kconfig` (single `CONFIG_FSL_FMD_SHIM` tristate)
- `drivers/soc/fsl/fmd_shim/Makefile`
- Hook into `drivers/soc/fsl/Kconfig` and `drivers/soc/fsl/Makefile`

### Kernel config: `data/kernel-config/ls1046a-fmd-shim.config`

```
CONFIG_FSL_FMD_SHIM=y
```

Must be `=y` (built-in) — needed before rootfs mount for VPP early startup.

---

## Testing Plan

1. **Boot verification**: `ls -la /dev/fm0*` — all chardevs present
2. **ioctl smoke test**: Write a userspace tool that opens `/dev/fm0` and calls
   `FM_IOC_GET_API_VERSION` — should return `21.1.0`
3. **DPDK integration**: Apply `fmc_q=0` patch, start VPP, check logs for:
   - `dpaa_fm_init` success (no ENOENT on `/dev/fm0`)
   - `dpaa_fm_config` success per port
   - `"RX queues: 4"` in port init log
4. **RSS verification**: Send traffic with varied 5-tuples, verify distribution
   across multiple frame queues via `vppctl show interface` rx counters
5. **Multi-worker**: Set `cpu-cores 2`, verify both cores processing packets

---

# DPDK Patch Specification

## Patch: `data/dpdk-fmcless.patch`

One-line change in `drivers/net/dpaa/dpaa_ethdev.c`:

```diff
-static int fmc_q = 1;
+static int fmc_q;  /* 0 = FMCLESS mode: fmlib programs FMan at runtime */
```

This causes `!(default_q || fmc_q)` to be true when `/tmp/fmc.bin` is absent,
triggering `dpaa_fm_init()` instead of falling to `default_q=1` (single queue).

### Application in CI

Add to `bin/ci-build-dpdk-plugin.sh` after the portal mmap patch:

```bash
# Enable FMCLESS mode — runtime FMan RSS configuration via fmlib
sed -i 's/^static int fmc_q = 1;/static int fmc_q; \/* FMCLESS: fmlib runtime RSS *\//' \
  drivers/net/dpaa/dpaa_ethdev.c
```

### Verification

After building, check that `dpaa_ethdev.o` has `fmc_q` initialized to 0:
```bash
nm build/drivers/librte_net_dpaa.a | grep fmc_q
# Should show 'B fmc_q' (BSS = zero-initialized) not 'D fmc_q' (data = non-zero)
```

---

# VPP Multi-Worker Configuration

After RSS is confirmed working, update `data/config.boot.default`:

```
vpp {
    settings {
        interface eth3
        interface eth4
        cpu-cores 2
        poll-sleep-usec 100
    }
}
```

`cpu-cores 2` creates 1 main thread + 1 worker thread. Each gets dedicated
QMan portal + frame queues. With 4 FQs per port distributed by 5-tuple hash,
2 workers each handle 2 queues.

---

# Complete Implementation Order

| Phase | Task | LOC | Depends On |
|-------|------|-----|------------|
| **0** | Confirm current VPP DPAA PMD works (single-queue, build running) | 0 | Build #23885218871 |
| **1** | Write fmd shim module spec (this document) | — | Phase 0 |
| **2** | Extract NXP KG register spec from SDK headers | — | Phase 1 |
| **3** | Implement `fsl_fmd_shim.c` | ~2000 | Phase 2 |
| **4** | Add kernel patch 9002 + config | ~50 | Phase 3 |
| **5** | Add DPDK `fmc_q=0` sed patch to CI | 2 | Phase 3 |
| **6** | Build + TFTP test on device | — | Phase 4+5 |
| **7** | Verify RSS distribution (4 queues × 2 SFP+ ports) | — | Phase 6 |
| **8** | Enable VPP multi-worker, benchmark with flent/iperf3 | 5 | Phase 7 |
| **9** | Update config.boot.default + VPP-SETUP.md | ~20 | Phase 8 |