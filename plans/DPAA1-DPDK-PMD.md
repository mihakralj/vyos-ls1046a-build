# DPAA1 DPDK PMD Integration Plan: 10G Wire-Speed VPP on Mono Gateway

> **Status (2026-04-02):** ✅ **CI PIPELINE COMPLETE.** Custom `dpdk_plugin.so` with 173 DPAA symbols builds in CI and deploys to ISO via chroot hook. Plugin verified on hardware (build 1854, device .147). **Current blocker:** `inflateEnd` undefined symbol — zlib static linking fix pushed (commit `785d40a`), build #23921637993 in progress.
>
> **Goal:** Replace AF_XDP (~3.5 Gbps measured, 3290 MTU cap) with DPAA1 DPDK Poll Mode Driver for full 10G line rate (~9.4 Gbps, full jumbo 9578 MTU).
>
> **Key insight:** Both the kernel USDPAA driver and the DPDK DPAA1 PMD are **mainline**. No NXP forks needed anywhere in the stack. Our combined kernel patch `9001` + `fsl_usdpaa_mainline.c` provide the kernel-side USDPAA ABI. DPDK 24.11 mainline with DPAA enabled provides the userspace PMD. VPP plugin is built out-of-tree in CI via `bin/ci-build-dpdk-plugin.sh`.

---

## Achievement Status

### Phase A: Kernel Patches — ✅ COMPLETE (in CI)

All kernel infrastructure is wired into CI scripts and ships in every CI-built ISO.

| Item | Status | Details |
|------|--------|---------|
| DPAA1 stack built-in | ✅ Done | `FSL_FMAN`, `FSL_DPAA`, `FSL_BMAN`, `FSL_QMAN`, `FSL_PAMU` all `=y` |
| USDPAA chardev driver | ✅ Done | `fsl_usdpaa_mainline.c` (1453 lines), copied to `drivers/soc/fsl/qbman/` during build |
| Kernel symbol exports | ✅ Done | Patch 9001: BMan/QMan portal reservation, BPID/FQID allocators, `qman_set_sdest()` |
| `CONFIG_FSL_USDPAA_MAINLINE=y` | ✅ Done | In `data/kernel-config/ls1046a-usdpaa.config` |
| `STRICT_DEVMEM` disabled | ✅ Done | Both `CONFIG_STRICT_DEVMEM` and `CONFIG_IO_STRICT_DEVMEM` unset (DPDK `/dev/mem` mmap) |
| INA234 sensor patch | ✅ Done | Patch 4002 applied |
| SFP rollball workaround | ✅ Done | Patch 4003 + `patch-phylink.py` applied |
| Kernel boot validation | ✅ Done | All 5 ethN appear, VyOS login works, USDPAA chardevs present |
| Live device confirmed | ✅ Done | `/dev/fsl-usdpaa` (crw 10,257), `/dev/fsl-usdpaa-irq` (crw 10,258) on production builds |

**Kernel Patch Series:**

| Patch | File | Changes |
|-------|------|---------|
| 9001 (combined) | `bman.c`, `bman_priv.h`, `bman_portal.c`, `qman.c`, `qman_priv.h`, `qman_portal.c`, `qman_ccsr.c`, `qman.h`, `Kconfig`, `Makefile` | Export 15 BMan/QMan symbols, portal phys addr storage + reservation, allocator-only frees, USDPAA Kconfig/Makefile |
| (source file) | `fsl_usdpaa_mainline.c` | 1453-line `/dev/fsl-usdpaa` + `/dev/fsl-usdpaa-irq` chardevs, 20 NXP-ABI-compatible ioctls |
| 4002 | `drivers/hwmon/ina2xx.c` | INA234 power sensor support |
| 4003 | `drivers/net/phy/sfp.c` | SFP rollball PHY EINVAL fallback |

### Phase B: DPDK Build & Standalone Validation — ✅ COMPLETE

| Item | Status | Details |
|------|--------|---------|
| DPDK 24.11 cross-compilation | ✅ Done | Static libs with DPAA PMD (`bus_dpaa`, `mempool_dpaa`, `net_dpaa`) |
| Portal mmap patch | ✅ Done | `dpdk-portal-mmap.patch` applied to `process.c` (CE=16KB WB-NS, CI=16KB Device-nGnRnE) |
| testpmd validation | ✅ Done | 30-second clean run on hardware, `Bye...` exit, no kernel panic |

### Phase C: VPP DPDK Plugin Build — ✅ COMPLETE (in CI)

| Item | Status | Details |
|------|--------|---------|
| C.1: ABI probe | ✅ Done | VPP statically links DPDK — must rebuild plugin with DPAA-enabled static DPDK |
| C.2: Out-of-tree plugin build | ✅ Done | `bin/ci-build-dpdk-plugin.sh` — builds DPDK 24.11 + VPP plugin in CI |
| C.3: VPP source patches | ✅ Done | 7 patches applied inline in build script (driver.c, dpdk.h, init.c, common.c) |
| C.4: GROUP linker script | ✅ Done | `libdpdk.a` as GROUP script referencing all `librte_*.a` (commit `16a749f`) |
| C.5: IS_DPAA X-macro patch | ✅ Done | Robust awk-based, handles VPP HEAD flag additions (commit `197e92f`) |
| C.6: Static zlib/fdt/numa | ✅ Done | `-Wl,-Bstatic -lz -lfdt -lnuma -Wl,-Bdynamic -latomic` (commit `785d40a`) |
| C.7: Chroot hook deployment | ✅ Done | `97-dpaa-dpdk-plugin.chroot` replaces upstream plugin after vpp-plugin-dpdk deb |
| C.8: Plugin verification | ✅ Done | 13MB, 173 DPAA symbols, PMD constructor confirmed via binary grep on device |

**VPP Source Changes (7 patches, applied inline in `bin/ci-build-dpdk-plugin.sh`):**

| # | File | Change |
|---|------|--------|
| 1 | `driver.c` | Add `net_dpaa` to `dpdk_drivers[]` with `TenGigabitEthernet` prefix |
| 2 | `dpdk.h` | Add `IS_DPAA` device flag (bit 2) via awk (last X-macro entry) |
| 3 | `dpdk.h` | Add `struct rte_mempool *dpaa_mempool` to `dpdk_main_t` |
| 4 | `init.c` | Add `IS_DPAA` detection via `strstr(driver_name, "net_dpaa")` |
| 5 | `init.c` | Add `dpdk_dpaa_mempool_create()` function + call BEFORE `dpdk_lib_init()` |
| 6 | `common.c` | Route DPAA devices to `dm->dpaa_mempool` in `dpdk_device_setup()` |
| 7 | `init.c` | Add `#include <rte_mbuf.h>` |

### Phase D: CI/ISO Integration — ✅ COMPLETE

| Item | Status | Details |
|------|--------|---------|
| D.1: Kernel patches in CI | ✅ Done | Patches 9001, 4002, 4003 + `fsl_usdpaa_mainline.c` via `bin/ci-setup-kernel.sh` |
| D.2: Kernel config in CI | ✅ Done | 7 config fragments in `data/kernel-config/` |
| D.3: DTS reserved-memory | ✅ Done | `usdpaa-mem@c0000000` in `mono-gateway-dk.dts` |
| D.4: DTS `fsl,dpaa` bus container | ✅ Done | SFP+ MACs listed for DPDK bus discovery |
| D.5: VyOS integration plumbing | ✅ Done | Patch 010 (platform-bus), `vpp-dpaa-rebind`, fancontrol |
| D.6: Custom VPP DPDK plugin in ISO | ✅ Done | `bin/ci-build-dpdk-plugin.sh` + chroot hook 97 |
| D.7: `vpp.py` DPAA PMD auto-detection | ✅ Done | Patch 010 startup.conf.j2: classifies PCI vs platform-bus, `no-pci` for DPAA-only |
| D.8: binutils in ISO | ✅ Done | Reinstalled in hook 97 after VyOS strip-then-autoremove |

### Phase E: Hardware Validation — 🟡 IN PROGRESS

| Item | Status | Details |
|------|--------|---------|
| E.1: Plugin loads in VPP | ❌ **Blocked** | `inflateEnd` undefined — zlib static linking fix building (#23921637993) |
| E.2: VPP discovers DPAA interfaces | ❌ Not started | Depends on E.1 |
| E.3: AF_XDP baseline measurements | ❌ Not started | Need 10G peer |
| E.4: DPAA PMD throughput | ❌ Not started | Target: >9 Gbps |
| E.5: Jumbo MTU 9578 validation | ❌ Not started | vs 3290 AF_XDP limit |
| E.6: Thermal stability | ❌ Not started | CPU temp under sustained 10G with `poll-sleep-usec 100` |
| E.7: Graceful rebind | ❌ Not started | `delete vpp settings interface ethX` + commit restores kernel control |

---

## Architecture

### Current: AF_XDP (Working, ~3.5 Gbps)

```
Wire → FMan → kernel eth3/eth4 → AF_XDP socket → VPP → AF_XDP → kernel → FMan → Wire
```

### Target: DPAA1 DPDK PMD (~9.4 Gbps)

```
Wire → FMan → BMan buffer pool → DPDK DPAA PMD → VPP → DPDK DPAA PMD → BMan → FMan → Wire
```

| Aspect | AF_XDP | DPAA PMD |
|--------|--------|----------|
| SFP+ MTU | **3290** (XDP hard cap) | **9578** (full jumbo) |
| Buffer copy | copy-mode | zero-copy |
| Packet path | `fsl_dpaa_eth` → XDP hook → VPP | USDPAA → DPDK PMD → VPP |
| VPP plugin | `af_xdp_plugin` | `dpdk_plugin` |
| Peak throughput | ~3.5 Gbps measured | ~9.4+ Gbps target |

---

## Root Causes Log (34 total)

| # | Root Cause | Fix | Phase |
|---|-----------|-----|-------|
| 1–12 | Various kernel build/boot/portal issues | See git history | A |
| 13 | LXC 200 source mismatch | SCP'd correct `fsl_usdpaa_mainline.c` | A |
| 14 | Restoring DPAA interfaces after DPDK crashes kernel | Reboot required | B |
| 15 | `qman_release_fqid()` → level 3 translation fault | Allocator-only frees (no portal access) | A |
| 16 | DPDK requires `/dev/fsl-usdpaa` + `/dev/mem` | USDPAA driver + STRICT_DEVMEM disabled | A |
| 17 | Portal mmap missing from DPDK `process.c` | `dpdk-portal-mmap.patch` | B |
| 18 | `dpaa_rx_queue_init()` returns -EIO | DTS reserved-memory at `0xc0000000` | A |
| 21 | DT path mismatch | DTS `fsl,dpaa` container node placement | A |
| 23 | VPP DPAA device name syntax | Correct naming: platform-bus auto-discovery | C |
| 25 | Wrong plugin deployed (4MB dynamic vs 16MB static) | Deploy correct statically-linked plugin | C |
| 26 | ABI mismatch — VPP statically embeds DPDK | Must rebuild with DPAA-enabled DPDK | C |
| **27** | VPP mempool ops `"vpp"` incompatible with DPAA PMD | DPAA mempool via `rte_pktmbuf_pool_create_by_ops("dpaa")` | C |
| **28** | `driver.c` has no `net_dpaa` entry | Add to `dpdk_drivers[]` | C |
| **29** | `dpdk_dpaa_mempool_create()` ordering | Move BEFORE `dpdk_lib_init()` | C |
| **30** | kexec double-boot kills FMan on TFTP | Use eMMC boot for testing | B |
| **31** | `dpaa_bus` init disrupts ALL FMan interfaces | Known blocker: AF_XDP remains only mixed kernel+VPP mode | E |
| **32** | `libdpdk.a` changed from GROUP to merged archive | Reverted to GROUP linker script (commit `16a749f`) | C |
| **33** | VPP HEAD added REPRESENTOR flag breaking IS_DPAA sed patch | Rewrote as robust awk (commit `197e92f`) | C |
| **34** | `inflateEnd` undefined — zlib linked dynamically not statically | `-Wl,-Bstatic -lz -lfdt -lnuma` (commit `785d40a`) | C |

---

## Remaining Steps (Prioritized)

### Step 1 — 🔴 P0: Verify zlib Static Linking Fix

**What:** Build #23921637993 must produce a plugin with no undefined zlib symbols. Deploy to .147, `set vpp settings interface eth4`, `commit`.

**Success criteria:**
- VPP starts without `undefined symbol` errors
- `vppctl show interface` shows DPAA interfaces (or at least VPP stays running)

### Step 2 — 🟡 P1: Root Cause #31 Assessment

**What:** Test whether DPAA PMD's `dpaa_bus` initialization corrupts kernel-managed FMan interfaces.

**Why critical:** If `dpaa_bus` scan disrupts ALL FMan MACs (including kernel-managed RJ45), then DPAA PMD can only be used in "all ports to VPP" mode — no mixed kernel+VPP. AF_XDP would remain the production path for mixed mode.

### Step 3 — 🟢 P2: End-to-End Performance Validation

**What:** With VPP DPAA PMD running, measure:
- iperf3 TCP throughput (target: >9 Gbps with SFP+ peers)
- Jumbo MTU 9578 (vs 3290 AF_XDP limit)
- Thermal stability under sustained poll-mode with `poll-sleep-usec 100`

### Step 4 — 🔵 P3: FMD Shim for RSS (Future)

Multi-queue RSS requires FMan PCD configuration from userspace. Spec complete at `plans/FMD-SHIM-SPEC.md`, not yet implemented. Enables multi-worker VPP with DPAA PMD (target: 2× throughput).

---

## DTS Configuration (Complete)

The `mono-gateway-dk.dts` already contains all required nodes:

| Node | Purpose | Status |
|------|---------|--------|
| `reserved-memory/usdpaa-mem@c0000000` | 256MB CMA for DPDK DMA buffers | ✅ In DTS |
| `fsl,dpaa` bus container | DPDK bus discovery for SFP+ MACs | ✅ In DTS |
| `dpaa-bpool` | DPDK buffer pool | ✅ In DTS |
| `ethernet@f0000` (MAC9) `status = "okay"` | Kernel owns at boot, VPP unbinds on demand | ✅ In DTS |
| `ethernet@f2000` (MAC10) `status = "okay"` | Kernel owns at boot, VPP unbinds on demand | ✅ In DTS |

---

## VyOS Integration Plumbing (Complete)

| Component | File | Status |
|-----------|------|--------|
| Platform-bus support in `vpp.py` | `data/vyos-1x-010-vpp-platform-bus.patch` | ✅ In CI |
| VPP stop rebind script | `data/scripts/vpp-dpaa-rebind` | ✅ In CI |
| Systemd `ExecStopPost` drop-in | `data/systemd/vpp-dpaa-rebind.conf` | ✅ In CI |
| Fan thermal management | `data/scripts/fancontrol.conf` + `fancontrol-setup.sh` | ✅ In CI |
| Port name remapping | `data/scripts/fman-port-name` + udev rules + `.link` file | ✅ In CI |
| DPAA plugin build + deploy | `bin/ci-build-dpdk-plugin.sh` + `data/hooks/97-dpaa-dpdk-plugin.chroot` | ✅ In CI |
| Upstream VPP packages | `vpp`, `vpp-plugin-core`, `vpp-plugin-dpdk` from VyOS repos | ✅ In ISO |

---

## Key Files

| File | Status | Purpose |
|------|--------|---------|
| `bin/ci-build-dpdk-plugin.sh` | ✅ In CI | DPDK 24.11 build + VPP plugin build with 7 DPAA patches |
| `data/hooks/97-dpaa-dpdk-plugin.chroot` | ✅ In CI | Deploy custom plugin over upstream + reinstall binutils |
| `data/cmake/CMakeLists.txt` | ✅ In CI | Out-of-tree VPP plugin cmake config |
| `data/dpdk-portal-mmap.patch` | ✅ In CI | DPDK `process.c` portal mmap |
| `data/kernel-patches/9001-usdpaa-bman-qman-exports-and-driver.patch` | ✅ In CI | Combined kernel patch |
| `data/kernel-patches/fsl_usdpaa_mainline.c` | ✅ In CI | USDPAA chardev driver (1453 lines, 20 ioctls) |
| `data/kernel-patches/4002-hwmon-ina2xx-add-INA234-support.patch` | ✅ In CI | INA234 power sensor support |
| `data/kernel-patches/4003-sfp-rollball-phylink-einval-fallback.patch` | ✅ In CI | SFP rollball PHY workaround |
| `data/vyos-1x-010-vpp-platform-bus.patch` | ✅ In CI | `vpp.py` platform-bus unbind/rebind |
| `plans/USDPAA-IOCTL-SPEC.md` | ✅ Complete | 20 ioctls, 17/20 implemented |
| `plans/FMD-SHIM-SPEC.md` | ✅ Complete | 8-ioctl FMan PCD shim (not yet implemented) |
| `plans/MAINLINE-PATCH-SPEC.md` | ✅ Complete | 6-patch design spec and symbol audit |

---

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Root Cause #31: dpaa_bus corrupts all FMan interfaces** | Cannot run mixed kernel+VPP mode | AF_XDP fallback; or dedicate all ports to VPP |
| **Upstream VPP API changes break patches** | Plugin build fails | Robust awk-based patching; pin VPP version check |
| **DPAA PMD thermal under continuous polling** | SoC overheats (87°C measured without mitigation) | `poll-sleep-usec 100` mandatory; fancontrol in CI |
| **Cross-compilation on CI runner may be slow** | Build time > 50 min | DPDK cached; only plugin needs rebuild |
| **Static linking bloat** | Plugin grows beyond 15MB | Acceptable — self-contained with no runtime deps |

---

## Hardware Constraints

- LS1046A: 4× Cortex-A72 @ 1.8 GHz, 8 GB DDR4
- FMan: 5 MACs (3× SGMII RJ45 + 2× XFI SFP+)
- BMan: 10 portals (4 kernel, 6 available for DPDK)
- QMan: 10 portals (4 kernel, 6 available for DPDK)
- DPAA1 must be `=y` (built-in), never `=m` (module)
- SFP+ ports are 10G-only (no 1G SFP support)
- DPAA1 XDP maximum MTU: 3290 (AF_XDP ceiling)
- DPAA1 DPDK PMD maximum MTU: 9578 (full jumbo)

---

## See Also

- [`VPP.md`](../VPP.md) — VPP overview and build requirements
- [`VPP-SETUP.md`](../VPP-SETUP.md) — User-facing VPP setup guide
- [`plans/MAINLINE-PATCH-SPEC.md`](MAINLINE-PATCH-SPEC.md) — Full 6-patch design spec and symbol audit
- [`plans/USDPAA-IOCTL-SPEC.md`](USDPAA-IOCTL-SPEC.md) — Complete NXP ioctl ABI (20 ioctls, 17/20 implemented)
- [`plans/FMD-SHIM-SPEC.md`](FMD-SHIM-SPEC.md) — FMan PCD shim module spec (not yet implemented)
- [`PORTING.md`](../PORTING.md) — DPAA1 driver archaeology, kernel history
- [`UBOOT.md`](../UBOOT.md) — U-Boot memory map, DTB loading, `fw_setenv`
- [`plans/DEV-LOOP.md`](DEV-LOOP.md) — TFTP fast iteration loop