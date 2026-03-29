# Mainline Kernel Patch Specification: USDPAA for DPDK DPAA1 PMD

> **Status:** ✅ **IMPLEMENTED** (2026-03-27). All 6 patches written, compiled, and running on device.
> See [§13 of USDPAA-IOCTL-SPEC.md](USDPAA-IOCTL-SPEC.md#13-mainline-implementation-status-2026-03-28) for implementation vs spec comparison.
> **Target kernel:** Linux 6.6.y (VyOS mainline)
> **Target board:** NXP LS1046A (Mono Gateway DK)
> **Author:** Beast (agentic analysis)
> **Date:** 2026-03-26
> **Prerequisite:** [USDPAA-IOCTL-SPEC.md](USDPAA-IOCTL-SPEC.md)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Mainline Kernel Export Audit](#2-mainline-kernel-export-audit)
3. [Gap Analysis: What's Missing](#3-gap-analysis--whats-missing)
4. [DPDK Userspace Call Trace](#4-dpdk-userspace-call-trace)
5. [Patch 1: BMan BPID Range Allocator Export](#5-patch-1--bman-bpid-range-allocator-export)
6. [Patch 2: BMan Portal Phys Addr + Reservation](#6-patch-2--bman-portal-phys-addr--reservation)
7. [Patch 3: QMan Portal Phys Addr + Reservation](#7-patch-3--qman-portal-phys-addr--reservation)
8. [Patch 4: QMan set_sdest Export](#8-patch-4--qman-set_sdest-export)
9. [Patch 5: fsl_usdpaa_mainline.c Module](#9-patch-5--fsl_usdpaa_mainlinec-module)
10. [Patch 6: DTS Reserved Memory](#10-patch-6--dts-reserved-memory)
11. [Build Integration](#11-build-integration)
12. [Risk Assessment](#12-risk-assessment)

---

## 1. Executive Summary

DPDK's DPAA1 PMD requires `/dev/fsl-usdpaa`, a character device providing ioctls for:
- **Resource allocation** (BPID, FQID, pool-channel, CGRID ranges)
- **Portal reservation** (physical addresses for userspace mmap)
- **DMA memory management** (contiguous memory from reserved-memory DT node)
- **Link status queries** (PHY state via net_device)

NXP's SDK implementation (`fsl_usdpaa.c`, 2,622 lines) is mutually exclusive with mainline
DPAA (`FSL_SDK_DPA depends on !FSL_DPAA`). Two codebases cannot coexist. The rewrite
strategy: build a clean ~700-line module against mainline APIs, adding 5 small patches
(~145 lines total) to export missing internals.

**Total new/modified code: ~860 lines across 6 patches.**

---

## 2. Mainline Kernel Export Audit

### 2.1 BMan: `drivers/soc/fsl/qbman/bman.c`

| Symbol | Status | Signature |
|--------|--------|-----------|
| `bman_new_pool()` | ✅ EXPORT_SYMBOL | `struct bman_pool *bman_new_pool(void)` |
| `bman_free_pool()` | ✅ EXPORT_SYMBOL | `void bman_free_pool(struct bman_pool *pool)` |
| `bman_get_bpid()` | ✅ EXPORT_SYMBOL | `int bman_get_bpid(const struct bman_pool *pool)` |
| `bman_release()` | ✅ EXPORT_SYMBOL | `int bman_release(struct bman_pool *pool, const struct bm_buffer *bufs, u8 num)` |
| `bman_acquire()` | ✅ EXPORT_SYMBOL | `int bman_acquire(struct bman_pool *pool, struct bm_buffer *bufs, u8 num)` |
| `bm_alloc_bpid_range()` | ❌ **static** | `static int bm_alloc_bpid_range(u32 *result, u32 count)` |
| `bm_release_bpid()` | ❌ **static** | `static int bm_release_bpid(u32 bpid)` |
| `bm_shutdown_pool()` | ❌ **static** | `static int bm_shutdown_pool(u32 bpid)` |
| `bm_bpalloc` | ❌ file-scope | `struct gen_pool *bm_bpalloc` (genalloc pool) |

**Key finding:** `bman_new_pool()` wraps `bm_alloc_bpid_range()` but allocates exactly 1 BPID
inside a `struct bman_pool`. USDPAA needs raw range allocation (`count > 1`).

### 2.2 QMan: `drivers/soc/fsl/qbman/qman.c`

| Symbol | Status | Signature |
|--------|--------|-----------|
| `qman_alloc_fqid_range()` | ✅ EXPORT_SYMBOL | `int qman_alloc_fqid_range(u32 *result, u32 count)` |
| `qman_alloc_pool_range()` | ✅ EXPORT_SYMBOL | `int qman_alloc_pool_range(u32 *result, u32 count)` |
| `qman_alloc_cgrid_range()` | ✅ EXPORT_SYMBOL | `int qman_alloc_cgrid_range(u32 *result, u32 count)` |
| `qman_release_fqid()` | ✅ EXPORT_SYMBOL | `int qman_release_fqid(u32 fqid)` |
| `qman_release_pool()` | ✅ EXPORT_SYMBOL | `int qman_release_pool(u32 qp)` |
| `qman_release_cgrid()` | ✅ EXPORT_SYMBOL | `int qman_release_cgrid(u32 cgrid)` |
| `qman_get_qm_portal_config()` | ✅ EXPORT_SYMBOL | `const struct qm_portal_config *qman_get_qm_portal_config(struct qman_portal *portal)` |

**All QMan range allocators are already exported.** No patches needed for resource allocation.

### 2.3 QMan CCSR: `drivers/soc/fsl/qbman/qman_ccsr.c`

| Symbol | Status | Signature |
|--------|--------|-----------|
| `qman_set_sdest()` | ❌ **not exported** | `void qman_set_sdest(u16 channel, unsigned int cpu_idx)` |
| `qm_get_pools_sdqcr()` | ❌ file-scope | `u32 qm_get_pools_sdqcr(void)` |

**`qman_set_sdest()` exists but lacks `EXPORT_SYMBOL`.** DPDK needs it for stashing destination (CPU affinity for portal cache-line prefetch).

### 2.4 Portal Config Structures

**BMan** (`bman_priv.h`):
```c
struct bm_portal_config {
    void *addr_virt_ce;         // cache-enabled (WC memremap)
    void __iomem *addr_virt_ci; // cache-inhibited (ioremap)
    struct list_head list;
    struct device *dev;
    int cpu;
    int irq;
    // ❌ NO physical address fields
};
```

**QMan** (`qman_priv.h`):
```c
struct qm_portal_config {
    void *addr_virt_ce;
    void __iomem *addr_virt_ci;
    struct device *dev;
    struct iommu_domain *iommu_domain;
    struct list_head list;
    int cpu;
    int irq;
    u16 channel;                // ← DPDK needs this
    u32 pools;
    // ❌ NO physical address fields  
};
```

**Critical gap:** Physical addresses are obtained from `platform_get_resource()` during probe
but only used for `memremap()`/`ioremap()`. They are **not stored** in the config structures.
USDPAA needs them for the `ALLOC_RAW_PORTAL` ioctl (returns phys addrs to userspace for mmap).

### 2.5 Portal Probe Flow

Both `bman_portal.c` and `qman_portal.c` follow the same pattern:

```c
static int Xman_portal_probe(struct platform_device *pdev) {
    struct resource *addr_phys[2];
    
    addr_phys[CE] = platform_get_resource(pdev, IORESOURCE_MEM, DPAA_PORTAL_CE);
    addr_phys[CI] = platform_get_resource(pdev, IORESOURCE_MEM, DPAA_PORTAL_CI);
    
    pcfg->addr_virt_ce = memremap(addr_phys[CE]->start, ...);  // phys addr discarded!
    pcfg->addr_virt_ci = ioremap(addr_phys[CI]->start, ...);   // phys addr discarded!
    
    cpu = cpumask_first(&portal_available_cpus);  // assign to next CPU
    // ... create affine portal
}
```

On LS1046A: **10 BMan portals, 10 QMan portals, 4 CPUs**.
Kernel assigns 4 portals affine to CPUs; **6 portals probe but sit idle** (cpu = -1).
Those idle portals become the reservation pool for DPDK.

---

## 3. Gap Analysis: What's Missing

| # | Gap | Severity | Patch | Lines |
|---|-----|----------|-------|-------|
| G1 | `bm_alloc_bpid_range()` is static | HIGH | Patch 1 | ~20 |
| G2 | Portal configs lack physical addresses | CRITICAL | Patch 2+3 | ~60+60 |
| G3 | No portal reservation/release API | CRITICAL | Patch 2+3 | (included) |
| G4 | `qman_set_sdest()` not exported | HIGH | Patch 4 | ~5 |
| G5 | No `/dev/fsl-usdpaa` chardev | CRITICAL | Patch 5 | ~700 |
| G6 | No reserved-memory DT node for DMA | HIGH | Patch 6 | ~15 |

---

## 4. DPDK Userspace Call Trace

From `drivers/bus/dpaa/base/qbman/process.c` (DPDK main branch):

### 4.1 Initialization sequence

```
1. open("/dev/fsl-usdpaa", O_RDWR)           → fd
2. ioctl(fd, IOCTL_DMA_MAP, {len=256MB})      → phys_addr of DMA region
3. mmap(fd, DMA offset)                        → userspace DMA pointer
```

### 4.2 Per-thread portal setup

```
4. ioctl(fd, IOCTL_ALLOC_RAW_PORTAL, {type=qman, ...})
   → returns {cinh=0xXXXX, cena=0xYYYY, index=N}
5. mmap(/dev/mem, cinh, 0x1000)               → CI mapping
6. mmap(/dev/mem, cena, 0x4000)               → CE mapping
7. ioctl(irqfd, IOCTL_PORTAL_IRQ_MAP, {index=N})
   → returns IRQ eventfd
```

### 4.3 Resource allocation

```
8. ioctl(fd, IOCTL_ID_ALLOC, {type=fqid, num=32})   → base FQID
9. ioctl(fd, IOCTL_ID_ALLOC, {type=bpid, num=1})     → BPID
10. ioctl(fd, IOCTL_ID_ALLOC, {type=pool, num=1})    → pool channel
11. ioctl(fd, IOCTL_ID_ALLOC, {type=cgrid, num=1})   → CGRID
```

### 4.4 Key observations

- DPDK uses `ALLOC_RAW_PORTAL (0x0C)` exclusively, NOT `PORTAL_MAP (0x07)`
- DPDK mmaps portals via `/dev/mem`, not via the USDPAA fd
- DMA memory IS mmap'd via the USDPAA fd (custom `usdpaa_mmap()`)
- Link status ioctls used only during eth_dev_start/stop/link_update
- CEETM ioctls **never used by DPDK**. Safe to stub.

---

## 5. Patch 1: BMan BPID Range Allocator Export

**File:** `drivers/soc/fsl/qbman/bman.c`
**Lines changed:** ~20
**Risk:** LOW (additive only, no behavior change)

### Changes

```c
// bman.c — Remove 'static', add EXPORT_SYMBOL

-static int bm_alloc_bpid_range(u32 *result, u32 count)
+int bm_alloc_bpid_range(u32 *result, u32 count)
 {
     unsigned long addr;
     addr = gen_pool_alloc(bm_bpalloc, count);
     if (!addr)
         return -ENOMEM;
     *result = addr & ~DPAA_GENALLOC_OFF;
     return 0;
 }
+EXPORT_SYMBOL(bm_alloc_bpid_range);

-static int bm_release_bpid(u32 bpid)
+int bm_release_bpid(u32 bpid)
 {
     int ret;
     ret = bm_shutdown_pool(bpid);
     if (ret) {
         pr_debug("BPID %d leaked\n", bpid);
         return ret;
     }
     gen_pool_free(bm_bpalloc, bpid | DPAA_GENALLOC_OFF, 1);
     return 0;
 }
+EXPORT_SYMBOL(bm_release_bpid);
```

### Header addition (`bman_priv.h`)

```c
// Add declarations for newly-exported functions
int bm_alloc_bpid_range(u32 *result, u32 count);
int bm_release_bpid(u32 bpid);
```

---

## 6. Patch 2: BMan Portal Phys Addr + Reservation

**Files:** `drivers/soc/fsl/qbman/bman_priv.h`, `drivers/soc/fsl/qbman/bman_portal.c`
**Lines changed:** ~60
**Risk:** MEDIUM (modifies probe path, but additive)

### 6.1 Struct extension (`bman_priv.h`)

```c
 struct bm_portal_config {
     void *addr_virt_ce;
     void __iomem *addr_virt_ci;
+    /* Physical addresses for userspace mmap */
+    resource_size_t addr_phys_ce;
+    resource_size_t addr_phys_ci;
+    size_t size_ce;
+    size_t size_ci;
     struct list_head list;
     struct device *dev;
     int cpu;
     int irq;
+    /* true if reserved for userspace (not affine to any CPU) */
+    bool reserved;
 };
```

### 6.2 Probe modification (`bman_portal.c`)

```c
+/* List of portals available for userspace reservation */
+static LIST_HEAD(bman_free_portals);
+static DEFINE_SPINLOCK(bman_free_lock);

 static int bman_portal_probe(struct platform_device *pdev)
 {
     struct resource *addr_phys[2];
     
     addr_phys[0] = platform_get_resource(pdev, IORESOURCE_MEM, DPAA_PORTAL_CE);
+    pcfg->addr_phys_ce = addr_phys[0]->start;
+    pcfg->size_ce = resource_size(addr_phys[0]);
     
     addr_phys[1] = platform_get_resource(pdev, IORESOURCE_MEM, DPAA_PORTAL_CI);
+    pcfg->addr_phys_ci = addr_phys[1]->start;
+    pcfg->size_ci = resource_size(addr_phys[1]);
     
     // ... existing memremap/ioremap ...
     
     cpu = cpumask_first(&portal_available_cpus);
-    if (cpu >= nr_cpu_ids) {
-        /* Portal not needed, cleanup */
+    if (cpu >= nr_cpu_ids) {
+        /* No CPU available — add to free portal pool for USDPAA */
+        pcfg->reserved = false;
+        spin_lock(&bman_free_lock);
+        list_add_tail(&pcfg->list, &bman_free_portals);
+        spin_unlock(&bman_free_lock);
+        return 0;
     }
```

### 6.3 Reservation API exports

```c
+/**
+ * bman_portal_reserve - Reserve a BMan portal for userspace
+ * @pcfg_out: Output pointer to portal config (phys addrs, irq)
+ * Returns 0 on success, -ENOENT if no portals available
+ */
+int bman_portal_reserve(struct bm_portal_config **pcfg_out)
+{
+    struct bm_portal_config *pcfg;
+    spin_lock(&bman_free_lock);
+    if (list_empty(&bman_free_portals)) {
+        spin_unlock(&bman_free_lock);
+        return -ENOENT;
+    }
+    pcfg = list_first_entry(&bman_free_portals,
+                            struct bm_portal_config, list);
+    list_del(&pcfg->list);
+    pcfg->reserved = true;
+    spin_unlock(&bman_free_lock);
+    *pcfg_out = pcfg;
+    return 0;
+}
+EXPORT_SYMBOL(bman_portal_reserve);
+
+/**
+ * bman_portal_release_reserved - Return a reserved portal
+ */
+void bman_portal_release_reserved(struct bm_portal_config *pcfg)
+{
+    spin_lock(&bman_free_lock);
+    pcfg->reserved = false;
+    list_add_tail(&pcfg->list, &bman_free_portals);
+    spin_unlock(&bman_free_lock);
+}
+EXPORT_SYMBOL(bman_portal_release_reserved);
```

---

## 7. Patch 3: QMan Portal Phys Addr + Reservation

**Files:** `drivers/soc/fsl/qbman/qman_priv.h`, `drivers/soc/fsl/qbman/qman_portal.c`
**Lines changed:** ~60
**Risk:** MEDIUM (same pattern as Patch 2)

### 7.1 Struct extension (`qman_priv.h`)

```c
 struct qm_portal_config {
     void *addr_virt_ce;
     void __iomem *addr_virt_ci;
+    resource_size_t addr_phys_ce;
+    resource_size_t addr_phys_ci;
+    size_t size_ce;
+    size_t size_ci;
     struct device *dev;
     struct iommu_domain *iommu_domain;
     struct list_head list;
     int cpu;
     int irq;
     u16 channel;
     u32 pools;
+    bool reserved;
 };
```

### 7.2 Probe + Reservation API (`qman_portal.c`)

Identical pattern to Patch 2:
- `qman_free_portals` list + `qman_free_lock` spinlock
- Store phys addrs during probe from `platform_get_resource()`
- Unaffined portals (cpu >= nr_cpu_ids) go to free list
- `qman_portal_reserve()` / `qman_portal_release_reserved()` exported

**Additional:** QMan portals carry a `channel` field. DPDK needs this for frame queue scheduling.

---

## 8. Patch 4: QMan set_sdest Export

**File:** `drivers/soc/fsl/qbman/qman_ccsr.c`
**Lines changed:** ~5
**Risk:** LOW (single line addition)

```c
 void qman_set_sdest(u16 channel, unsigned int cpu_idx)
 {
     // ... existing implementation unchanged ...
 }
+EXPORT_SYMBOL(qman_set_sdest);
```

Also move the declaration from `qman_priv.h` to a public header (or ensure our module
can include `qman_priv.h`).

---

## 9. Patch 5: fsl_usdpaa_mainline.c Module

**File:** `drivers/soc/fsl/qbman/fsl_usdpaa_mainline.c` (NEW)
**Lines:** ~700
**Risk:** LOW (new file, no modification to existing code paths)

### 9.1 Module structure

```c
// fsl_usdpaa_mainline.c — Clean USDPAA for mainline kernel
//
// Implements /dev/fsl-usdpaa character device for DPDK DPAA1 PMD.
// Maps DPDK ioctl ABI to mainline BMan/QMan APIs.

#include <linux/miscdevice.h>
#include <linux/fs.h>
#include <linux/mm.h>
#include <linux/slab.h>
#include <linux/genalloc.h>
#include <linux/of_reserved_mem.h>
#include <linux/dma-mapping.h>
#include <linux/eventfd.h>

#include "bman_priv.h"     // struct bm_portal_config, bm_alloc_bpid_range
#include "qman_priv.h"     // struct qm_portal_config, qman_set_sdest
#include <soc/fsl/qbman/bman.h>
#include <soc/fsl/qbman/qman.h>

#define USDPAA_IOCTL_MAGIC 'u'
```

### 9.2 Per-FD context

```c
struct usdpaa_ctx {
    /* Resource tracking for cleanup on close */
    struct list_head alloc_fqids;     /* allocated FQID ranges */
    struct list_head alloc_bpids;     /* allocated BPID ranges */
    struct list_head alloc_pools;     /* allocated pool-channel ranges */
    struct list_head alloc_cgrids;    /* allocated CGRID ranges */
    struct list_head portal_maps;     /* reserved portals */
    struct list_head dma_maps;        /* DMA memory mappings */
    struct mutex lock;
};
```

### 9.3 Ioctl dispatch table

| ioctl | cmd | Implementation |
|-------|-----|----------------|
| `ID_ALLOC` | 0x01 | Switch on `id_type`: FQID→`qman_alloc_fqid_range()`, BPID→`bm_alloc_bpid_range()`, POOL→`qman_alloc_pool_range()`, CGRID→`qman_alloc_cgrid_range()` |
| `ID_RELEASE` | 0x02 | Reverse of above: `qman_release_fqid()`, `bm_release_bpid()`, etc. |
| `ID_RESERVE` | 0x0A | Return `-ENOSYS` (DPDK doesn't use) |
| `DMA_MAP` | 0x03 | Allocate from reserved-memory genalloc pool, track in ctx |
| `DMA_UNMAP` | 0x04 | Release to genalloc pool, remove from ctx |
| `DMA_LOCK` | 0x05 | No-op, return 0 |
| `DMA_UNLOCK` | 0x06 | No-op, return 0 |
| `DMA_USED` | 0x0B | Return current allocation stats |
| `PORTAL_MAP` | 0x07 | Legacy: call `Xman_portal_reserve()`, return config |
| `PORTAL_UNMAP` | 0x08 | Release portal back to free pool |
| `PORTAL_IRQ_MAP` | 0x09 | Create eventfd, connect to portal IRQ |
| `ALLOC_RAW_PORTAL` | 0x0C | **PRIMARY**: reserve portal, return phys CE/CI addrs |
| `FREE_RAW_PORTAL` | 0x0D | Release raw portal |
| `EN_LINK_STATUS` | 0x0E | Stub, return `-ENOSYS` |
| `DIS_LINK_STATUS` | 0x0F | Stub, return `-ENOSYS` |
| `GET_LINK_STATUS` | 0x10 | Query via `dev_get_by_name()` + ethtool ops |
| `SET_LINK_STATUS` | 0x11 | Stub, return `-ENOSYS` |
| `SET_LINK_SPEED` | 0x12 | Stub, return `-ENOSYS` |
| `RESTART_AUTONEG` | 0x13 | Stub, return `-ENOSYS` |
| `GET_VERSION` | 0x14 | Return `USDPAA_IOCTL_VERSION = 2` |

### 9.4 DMA Memory management

```c
/* Reserved-memory pool for DPDK DMA allocations */
static struct gen_pool *usdpaa_mem_pool;
static phys_addr_t usdpaa_mem_phys;
static size_t usdpaa_mem_size;

struct dma_mapping {
    struct list_head node;
    phys_addr_t phys;
    size_t len;
    unsigned long virt_offset; /* for mmap */
};

/* Fragment-based allocation from reserved-memory region */
static int ioctl_dma_map(struct usdpaa_ctx *ctx, 
                         struct usdpaa_ioctl_dma_map __user *arg)
{
    struct usdpaa_ioctl_dma_map map;
    struct dma_mapping *dm;
    unsigned long addr;
    
    if (copy_from_user(&map, arg, sizeof(map)))
        return -EFAULT;
    
    addr = gen_pool_alloc(usdpaa_mem_pool, map.len);
    if (!addr)
        return -ENOMEM;
    
    dm = kmalloc(sizeof(*dm), GFP_KERNEL);
    dm->phys = gen_pool_virt_to_phys(usdpaa_mem_pool, addr);
    dm->len = map.len;
    
    mutex_lock(&ctx->lock);
    list_add(&dm->node, &ctx->dma_maps);
    mutex_unlock(&ctx->lock);
    
    map.phys_addr = dm->phys;
    if (copy_to_user(arg, &map, sizeof(map)))
        return -EFAULT;
    
    return 0;
}
```

### 9.5 Portal allocation (ALLOC_RAW_PORTAL)

```c
static int ioctl_alloc_raw_portal(struct usdpaa_ctx *ctx,
                                  struct usdpaa_ioctl_raw_portal __user *arg)
{
    struct usdpaa_ioctl_raw_portal rp;
    int ret;
    
    if (copy_from_user(&rp, arg, sizeof(rp)))
        return -EFAULT;
    
    if (rp.type == usdpaa_portal_qman) {
        struct qm_portal_config *qpcfg;
        ret = qman_portal_reserve(&qpcfg);
        if (ret)
            return ret;
        
        /* Configure stash destination if requested */
        if (rp.enable_stash)
            qman_set_sdest(qpcfg->channel, rp.sdest);
        
        rp.cinh = qpcfg->addr_phys_ci;
        rp.cena = qpcfg->addr_phys_ce;
        rp.index = qpcfg->channel - QM_CHANNEL_SWPORTAL0;
        
        /* Track for cleanup */
        // ... add to ctx->portal_maps ...
    } else {
        struct bm_portal_config *bpcfg;
        ret = bman_portal_reserve(&bpcfg);
        if (ret)
            return ret;
        
        rp.cinh = bpcfg->addr_phys_ci;
        rp.cena = bpcfg->addr_phys_ce;
        rp.index = 0; /* BMan portals don't have channel index */
    }
    
    if (copy_to_user(arg, &rp, sizeof(rp)))
        return -EFAULT;
    
    return 0;
}
```

### 9.6 mmap handler

```c
static int usdpaa_mmap(struct file *filp, struct vm_area_struct *vma)
{
    struct usdpaa_ctx *ctx = filp->private_data;
    unsigned long offset = vma->vm_pgoff << PAGE_SHIFT;
    size_t len = vma->vm_end - vma->vm_start;
    
    /* DMA memory mapping — offset matches phys_addr from DMA_MAP */
    if (offset >= usdpaa_mem_phys &&
        offset < usdpaa_mem_phys + usdpaa_mem_size) {
        vma->vm_page_prot = pgprot_writecombine(vma->vm_page_prot);
        return remap_pfn_range(vma, vma->vm_start,
                               offset >> PAGE_SHIFT, len,
                               vma->vm_page_prot);
    }
    
    return -EINVAL;
}
```

### 9.7 Cleanup on close

```c
static int usdpaa_release(struct inode *inode, struct file *filp)
{
    struct usdpaa_ctx *ctx = filp->private_data;
    struct dma_mapping *dm, *tmp_dm;
    struct portal_reservation *pr, *tmp_pr;
    struct resource_alloc *ra, *tmp_ra;
    
    /* Release all DMA mappings */
    list_for_each_entry_safe(dm, tmp_dm, &ctx->dma_maps, node) {
        gen_pool_free(usdpaa_mem_pool, dm->phys, dm->len);
        list_del(&dm->node);
        kfree(dm);
    }
    
    /* Release all portals back to free pool */
    list_for_each_entry_safe(pr, tmp_pr, &ctx->portal_maps, node) {
        if (pr->type == usdpaa_portal_qman)
            qman_portal_release_reserved(pr->qpcfg);
        else
            bman_portal_release_reserved(pr->bpcfg);
        list_del(&pr->node);
        kfree(pr);
    }
    
    /* Release all resource IDs */
    // ... iterate alloc_fqids, alloc_bpids, etc. ...
    
    mutex_destroy(&ctx->lock);
    kfree(ctx);
    return 0;
}
```

### 9.8 Module init

```c
static struct miscdevice usdpaa_miscdev = {
    .minor = MISC_DYNAMIC_MINOR,
    .name  = "fsl-usdpaa",
    .fops  = &usdpaa_fops,
};

static int __init usdpaa_init(void)
{
    struct device_node *mem_node;
    struct resource res;
    int ret;
    
    /* Find reserved-memory node for DMA pool */
    mem_node = of_find_compatible_node(NULL, NULL, "fsl,usdpaa-mem");
    if (!mem_node) {
        pr_warn("fsl-usdpaa: no reserved memory, DMA_MAP disabled\n");
    } else {
        of_address_to_resource(mem_node, 0, &res);
        usdpaa_mem_phys = res.start;
        usdpaa_mem_size = resource_size(&res);
        
        usdpaa_mem_pool = gen_pool_create(PAGE_SHIFT, -1);
        gen_pool_add_virt(usdpaa_mem_pool,
                          (unsigned long)usdpaa_mem_phys,
                          usdpaa_mem_phys,
                          usdpaa_mem_size, -1);
        of_node_put(mem_node);
    }
    
    ret = misc_register(&usdpaa_miscdev);
    if (ret) {
        pr_err("fsl-usdpaa: misc_register failed: %d\n", ret);
        return ret;
    }
    
    pr_info("fsl-usdpaa: registered (DMA pool: %zu MB @ 0x%llx)\n",
            usdpaa_mem_size >> 20, (u64)usdpaa_mem_phys);
    return 0;
}
module_init(usdpaa_init);
```

---

## 10. Patch 6: DTS Reserved Memory

**File:** `data/dtb/mono-gateway-dk.dts`
**Lines changed:** ~15
**Risk:** LOW (additive DT node)

Add to the existing reserved-memory node (or create one):

```dts
/ {
    reserved-memory {
        #address-cells = <2>;
        #size-cells = <2>;
        ranges;
        
        /* DPDK DMA memory pool — 256MB contiguous */
        usdpaa_mem: usdpaa-mem@c0000000 {
            compatible = "fsl,usdpaa-mem";
            reg = <0x0 0xc0000000 0x0 0x10000000>;  /* 256MB @ 3GB */
            no-map;
        };
    };
};
```

**Address selection:** `0xc0000000` (3GB) sits above the typical kernel mapping for 8GB RAM
and below the 4GB boundary for DMA. The LS1046A has full 40-bit DMA addressing. Safe.

**Alternative:** CMA instead of static reserved-memory. CMA is more flexible but
requires `CONFIG_CMA=y` and `dma_alloc_coherent()` integration. Static reserved-memory
is simpler, matches NXP SDK behavior, and works on day one.

---

## 11. Build Integration

### 11.1 Kconfig addition

```
# drivers/soc/fsl/qbman/Kconfig
config FSL_USDPAA_MAINLINE
    tristate "Userspace DPAA driver for DPDK"
    depends on FSL_DPAA
    help
      Provides /dev/fsl-usdpaa character device for DPDK's DPAA1 PMD.
      Allows userspace applications to allocate QBMan resources (BPIDs,
      FQIDs, pool channels, CGRIDs), reserve portals for direct hardware
      access, and manage DMA memory from reserved-memory regions.
      
      Required for DPDK with the dpaa bus driver.
      
      If unsure, say N.
```

### 11.2 Makefile addition

```makefile
# drivers/soc/fsl/qbman/Makefile
obj-$(CONFIG_FSL_USDPAA_MAINLINE) += fsl_usdpaa_mainline.o
```

### 11.3 VyOS kernel config addition

In `auto-build.yml`, add to the kernel config printf block:

```bash
# === DPDK USDPAA support ===
CONFIG_FSL_USDPAA_MAINLINE=y
```

### 11.4 Patch application order

```
1. data/kernel-patches/0001-usdpaa-bman-qman-exports-and-driver.patch  (combined: BMan/QMan exports, portal reservation, Kconfig/Makefile)
2. data/kernel-patches/fsl_usdpaa_mainline.c                           (copied separately into drivers/soc/fsl/qbman/ during build)
3. data/kernel-patches/0006-dts-ls1046a-usdpaa-reserved-mem.patch      (already applied in mono-gateway-dk.dts, kept for reference)
```

---

## 12. Risk Assessment

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Portal probe order differs across boots | HIGH | LOW | Use DT cell-index, not probe order |
| Reserved-memory address conflicts | HIGH | LOW | Verify against U-Boot memory map |
| compat_ioctl needed for 32-bit DPDK | MED | LOW | DPDK is 64-bit only on arm64 |
| gen_pool fragmentation in DMA pool | MED | LOW | DPDK allocates large contiguous blocks |
| Portal IRQ routing incorrect | MED | MED | Test with `cat /proc/interrupts` |
| Mainline portal driver changes in 6.12+ | LOW | MED | Monitor `drivers/soc/fsl/qbman/` diffs |

### 12.1 What we DON'T need (confirmed by DPDK source analysis)

- **CEETM support.** DPDK never touches it. Stub with `-ENOSYS`.
- **PAMU IOMMU stashing.** LS1046A has no PAMU. No-op.
- **Legacy PORTAL_MAP (0x07).** DPDK uses ALLOC_RAW_PORTAL exclusively. PORTAL_MAP can be a thin wrapper.
- **Link status interrupts.** DPDK polls link state. Stub enable/disable.
- **32-bit compat ioctls.** arm64 DPDK is 64-bit native.

---

## Appendix A: Mainline vs SDK Symbol Comparison

| SDK Function | Mainline Equivalent | Status |
|--------------|---------------------|--------|
| `bman_alloc_bpid_range()` | `bm_alloc_bpid_range()` | ❌ static → **Patch 1** |
| `bman_release_bpid_range()` | `bm_release_bpid()` | ❌ static → **Patch 1** |
| `bman_reserve_bpid_range()` | N/A | Stub `-ENOSYS` |
| `qman_alloc_fqid_range()` | `qman_alloc_fqid_range()` | ✅ identical |
| `qman_alloc_pool_range()` | `qman_alloc_pool_range()` | ✅ identical |
| `qman_alloc_cgrid_range()` | `qman_alloc_cgrid_range()` | ✅ identical |
| `qman_release_fqid()` | `qman_release_fqid()` | ✅ identical |
| `qman_release_pool()` | `qman_release_pool()` | ✅ identical (named qpool_release) |
| `qman_release_cgrid()` | `qman_release_cgrid()` | ✅ identical |
| `qman_set_sdest()` | `qman_set_sdest()` | ❌ not exported → **Patch 4** |
| `qman_alloc_ceetm()` | N/A | Stub `-ENOSYS` |
| Portal phys addr access | `platform_get_resource()` | ❌ not stored → **Patch 2+3** |
| Portal reserve/release pool | N/A | ❌ doesn't exist → **Patch 2+3** |

## Appendix B: DPDK ioctl Header ABI

The USDPAA ioctl numbers and structures must match exactly. DPDK's
`drivers/bus/dpaa/include/fsl_usd.h` defines:

```c
#define DPAA_IOCTL_MAGIC 'u'
#define DPAA_IOCTL_ID_ALLOC          _IOWR(DPAA_IOCTL_MAGIC, 0x01, ...)
#define DPAA_IOCTL_ID_RELEASE        _IOW (DPAA_IOCTL_MAGIC, 0x02, ...)
#define DPAA_IOCTL_DMA_MAP           _IOWR(DPAA_IOCTL_MAGIC, 0x03, ...)
#define DPAA_IOCTL_DMA_UNMAP         _IOW (DPAA_IOCTL_MAGIC, 0x04, ...)
#define DPAA_IOCTL_DMA_LOCK          _IOW (DPAA_IOCTL_MAGIC, 0x05, ...)
#define DPAA_IOCTL_DMA_UNLOCK        _IOW (DPAA_IOCTL_MAGIC, 0x06, ...)
#define DPAA_IOCTL_PORTAL_MAP        _IOWR(DPAA_IOCTL_MAGIC, 0x07, ...)
#define DPAA_IOCTL_PORTAL_UNMAP      _IOW (DPAA_IOCTL_MAGIC, 0x08, ...)
#define DPAA_IOCTL_PORTAL_IRQ_MAP    _IOW (DPAA_IOCTL_MAGIC, 0x09, ...)
#define DPAA_IOCTL_ID_RESERVE        _IOWR(DPAA_IOCTL_MAGIC, 0x0A, ...)
#define DPAA_IOCTL_DMA_USED          _IOR (DPAA_IOCTL_MAGIC, 0x0B, ...)
#define DPAA_IOCTL_ALLOC_RAW_PORTAL  _IOWR(DPAA_IOCTL_MAGIC, 0x0C, ...)
#define DPAA_IOCTL_FREE_RAW_PORTAL   _IOR (DPAA_IOCTL_MAGIC, 0x0D, ...)
```

**The ioctl magic ('u'), numbers (0x01-0x14), and structure layouts MUST be binary-compatible
with NXP's `fsl_usdpaa.h`.** Same ABI, different implementation. That is the entire premise.
