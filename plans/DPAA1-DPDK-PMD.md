# DPAA1 DPDK PMD Integration Plan: 10G Wire-Speed VPP on Mono Gateway

> **Status (2026-03-31):** ~75% complete. Kernel infrastructure, DPDK standalone, and VPP source patches all done. **Critical blocker:** custom VPP DPDK plugin not wired into CI/ISO pipeline.
>
> **Goal:** Replace AF_XDP (~3.5 Gbps measured, 3290 MTU cap) with DPAA1 DPDK Poll Mode Driver for full 10G line rate (~9.4 Gbps, full jumbo 9578 MTU).
>
> **Key insight:** Both the kernel USDPAA driver and the DPDK DPAA1 PMD are **mainline**. No NXP forks needed anywhere in the stack. Our 6-patch kernel series + `fsl_usdpaa_mainline.c` provide the kernel-side USDPAA ABI. DPDK 24.11 mainline with `-Dplatform=dpaa` provides the userspace PMD. The entire path from silicon to VPP runs on upstream code.

---

## Achievement Status

### Phase A: Kernel Patches — ✅ COMPLETE (in CI)

All kernel infrastructure is wired into `auto-build.yml` and ships in every CI-built ISO.

| Item | Status | Details |
|------|--------|---------|
| DPAA1 stack built-in | ✅ Done | `FSL_FMAN`, `FSL_DPAA`, `FSL_BMAN`, `FSL_QMAN`, `FSL_PAMU` all `=y` |
| USDPAA chardev driver | ✅ Done | `fsl_usdpaa_mainline.c` (1514 lines), copied to `drivers/soc/fsl/qbman/` during build |
| Kernel symbol exports | ✅ Done | Patch 9001: BMan/QMan portal reservation, BPID/FQID allocators, `qman_set_sdest()` |
| `CONFIG_FSL_USDPAA_MAINLINE=y` | ✅ Done | In defconfig printf block (line ~206 of auto-build.yml) |
| `STRICT_DEVMEM` disabled | ✅ Done | Both `CONFIG_STRICT_DEVMEM` and `CONFIG_IO_STRICT_DEVMEM` unset (DPDK `/dev/mem` mmap) |
| INA234 sensor patch | ✅ Done | Patch 4002 applied |
| SFP rollball workaround | ✅ Done | Patch 4003 + `patch-phylink.py` applied |
| Kernel boot validation | ✅ Done | All 5 ethN appear, VyOS login works, USDPAA chardevs present |
| Live device confirmed | ✅ Done | `/dev/fsl-usdpaa` (crw 10,257), `/dev/fsl-usdpaa-irq` (crw 10,258) on CI-built image |

**Kernel Patch Series (6 patches, all stable):**

| Patch | File | Changes |
|-------|------|---------|
| 9001 (combined) | `bman.c`, `bman_priv.h`, `bman_portal.c`, `qman.c`, `qman_priv.h`, `qman_portal.c`, `qman_ccsr.c`, `qman.h`, `Kconfig`, `Makefile` | Export BMan/QMan allocators + portal phys addrs + reservation pool + allocator-only frees + USDPAA Kconfig/Makefile |
| (source file) | `fsl_usdpaa_mainline.c` | 1514-line `/dev/fsl-usdpaa` + `/dev/fsl-usdpaa-irq` chardevs, 20 NXP-ABI-compatible ioctls |
| 4002 | `drivers/hwmon/ina2xx.c` | INA234 power sensor support |
| 4003 | `drivers/net/phy/sfp.c` | SFP rollball PHY EINVAL fallback |

### Phase B: DPDK Build & Standalone Validation — ✅ COMPLETE

| Item | Status | Details |
|------|--------|---------|
| DPDK 24.11 cross-compilation | ✅ Done | Static libs with DPAA PMD (`bus_dpaa`, `mempool_dpaa`, `net_dpaa`) |
| Portal mmap patch | ✅ Done | `dpdk-portal-mmap.patch` applied to `process.c` (CE=64KB WB-NS, CI=16KB Device-nGnRnE) |
| testpmd validation | ✅ Done | 30-second clean run on hardware, `Bye...` exit, no kernel panic |
| 10 test script iterations | ✅ Done | `test-vpp-dpaa-v1..v10.sh` — progressive debugging |

### Phase C: VPP DPDK Plugin Integration — 🟡 IN PROGRESS

| Item | Status | Details |
|------|--------|---------|
| C.1: ABI probe | ✅ Done | Confirmed VPP statically links DPDK — Option A (shared libs) impossible, Option B (rebuild) required |
| C.2: Custom `dpdk_plugin.so` built | ✅ Done | 16MB, 2362 DPAA symbols, MD5: `622949ff` |
| VPP source patches written | ✅ Done | 7 patches across 4 files via `bin/patch-vpp-dpaa-mempool.sh` |
| Root causes #27-#30 identified & fixed | ✅ Done | DPAA mempool, `net_dpaa` driver registration, init ordering, kexec |
| Root causes #31-#33 identified | ✅ Done | libatomic1 missing, dev syntax wrong, sysfs path verification pending |
| **C.3: Gateway hardware test** | ❌ **BLOCKED** | Gateway unreachable (192.168.1.110), needs power cycle. Root causes #31-33 found in last session |
| C.4: VPP startup.conf DPAA template | ✅ Done | Patch 010 already handles: `no-pci`, skips `dev` entries for DPAA (auto-discovered), PCI/platform-bus classification |
| C.5: End-to-end VPP DPAA validation | ❌ Not started | Depends on C.3 |

**Root Causes Found & Fixed (30 total across Phases A–C):**

| # | Root Cause | Fix |
|---|-----------|-----|
| 1–12 | Various kernel build/boot/portal issues | See git history |
| 13 | LXC 200 source mismatch — sed scripts didn't apply | SCP'd correct `fsl_usdpaa_mainline.c` directly |
| 14 | Restoring DPAA interfaces after DPDK crashes kernel | Reboot instead of `ip link set up` after testpmd |
| 15 | `qman_release_fqid()` → level 3 translation fault | Added `qman_free_fqid_range/pool_range/cgrid_range()` allocator-only frees |
| 16 | DPDK DPAA PMD requires `/dev/fsl-usdpaa` and `/dev/mem` | USDPAA driver + STRICT_DEVMEM disabled |
| 17 | Portal mmap missing from DPDK `process.c` | `dpdk-portal-mmap.patch` |
| 18 | `dpaa_rx_queue_init()` returns -EIO | DTS reserved-memory at `0xc0000000` |
| 21 | DT path mismatch (`/proc/device-tree/fsl,dpaa` vs `soc:fsl,dpaa`) | DTS `fsl,dpaa` container node placement |
| 23 | VPP DPAA device name syntax: `dpaa,fm1-mac9` not `dpaa_bus:fm1-mac9` | Correct device naming |
| 24 | VPP DPDK EAL init messages go to stdout only | Logging fix |
| 25 | VPP deploying wrong plugin (4MB dynamic instead of 16MB static) | Deploy correct static-linked plugin |
| 26 | ABI mismatch — VPP statically embeds DPDK | Must rebuild VPP, not deploy shared libs |
| **27** | VPP mempool ops `"vpp"` incompatible with DPAA PMD (SEGV) | Create separate DPAA mempool via `rte_pktmbuf_pool_create_by_ops("dpaa")` |
| **28** | VPP `driver.c` has no `net_dpaa` entry | Add `net_dpaa` to `dpdk_drivers[]` |
| **29** | `dpdk_dpaa_mempool_create()` called AFTER `dpdk_lib_init()` | Move mempool creation BEFORE `dpdk_lib_init()` |
| **30** | kexec double-boot kills FMan on TFTP-booted systems | Use eMMC boot for testing |
| **31** | `libatomic.so.1` missing on gateway — custom `dpdk_plugin.so` NEEDED it | Manually copied; permanent fix: added `libatomic1` to ISO chroot hook in `auto-build.yml` |
| **32** | `dev dpaa,fm1-mac9` syntax invalid in startup.conf | DPAA uses platform-bus auto-discovery, not PCI `dev` directives — patch 010 already skips `dev` entries for fsl_dpa NICs |
| **33** | DPDK DPAA bus not probing — `rte_dpaa_bus_scan()` checks sysfs paths | DTS `fsl,dpaa` under `&soc` with `simple-bus` compatible should create `/sys/devices/platform/soc/soc:fsl,dpaa` matching `DPAA_DEV_PATH1`. Needs gateway verification. |

**VPP Source Changes (7 patches across 4 files):**

Implemented in [`bin/patch-vpp-dpaa-mempool.sh`](../bin/patch-vpp-dpaa-mempool.sh):

| # | File | Change |
|---|------|--------|
| 1 | `driver.c` | Add `net_dpaa` to `dpdk_drivers[]` |
| 2 | `dpdk.h` | Add `IS_DPAA` device flag (bit 2) |
| 3 | `dpdk.h` | Add `struct rte_mempool *dpaa_mempool` to `dpdk_main_t` |
| 4 | `init.c` | Add `IS_DPAA` detection via `strstr(driver_name, "net_dpaa")` |
| 5 | `init.c` | Add `dpdk_dpaa_mempool_create()` function + call BEFORE `dpdk_lib_init()` |
| 6 | `common.c` | Route DPAA devices to `dm->dpaa_mempool` in `dpdk_device_setup()` |
| 7 | `init.c` | Add `#include rte_mbuf.h` |

### Phase D: CI/ISO Integration — 🟡 PARTIAL

| Item | Status | Details |
|------|--------|---------|
| D.1: Kernel patches in CI | ✅ Done | Patches 9001, 4002, 4003 + `fsl_usdpaa_mainline.c` staged in auto-build.yml (lines 219-249) |
| D.1: Kernel config in CI | ✅ Done | `CONFIG_FSL_USDPAA_MAINLINE=y`, STRICT_DEVMEM disabled |
| D.1: DTS reserved-memory | ✅ Done | `usdpaa-mem@c0000000` in `mono-gateway-dk.dts` |
| D.1: DTS `fsl,dpaa` bus container | ✅ Done | SFP+ MACs listed for DPDK bus discovery |
| D.1: VyOS integration plumbing | ✅ Done | Patch 010 (platform-bus), `vpp-dpaa-rebind`, fancontrol |
| **D.2: Custom VPP DPDK plugin in ISO** | ❌ **NOT DONE** | **CRITICAL BLOCKER** — no CI step to build DPAA-enabled VPP |
| D.3: `vpp.py` DPAA PMD auto-detection | ✅ Done | Patch 010 startup.conf.j2: classifies PCI vs platform-bus, `no-pci` for DPAA-only, skips `dev` entries |
| D.4: U-Boot bootargs for DPAA portals | ✅ N/A | Mainline USDPAA driver uses portal reservation API (patches 0002/0003) — `bportals`/`qportals` bootargs only needed for NXP SDK kernel (not referenced in DPDK source) |
| D.5: `libatomic1` in ISO | ✅ Done | Added to `98-fancontrol.chroot` hook in `auto-build.yml` (custom `dpdk_plugin.so` NEEDED: `libatomic.so.1`) |

### Phase E: Benchmarks — ❌ NOT STARTED

| Item | Status | Details |
|------|--------|---------|
| E.1: AF_XDP baseline | ❌ Not started | Reference measurements needed |
| E.2: DPAA PMD jumbo MTU | ❌ Not started | 9578 MTU validation |
| E.3: Throughput benchmarks | ❌ Not started | iperf3 comparison |
| E.4: Thermal validation | ❌ Not started | CPU temp under sustained 10G |
| E.5: Regression testing | ❌ Not started | AF_XDP fallback, kernel ports unaffected |

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

## Remaining Steps (Prioritized)

### Step 1 — 🔴 P0: Complete Phase C.3 Gateway Hardware Test

**What:** Deploy the existing custom `dpdk_plugin.so` (16MB, MD5 `622949ff`) to the Mono Gateway via serial console and validate VPP discovers DPAA1 interfaces.

**Why blocked:** Requires serial console access to the gateway hardware. The v9 plugin is already deployed but hasn't been tested end-to-end.

**Success criteria:**
- `vppctl show interface` shows `dpaa-fm1-mac9` and `dpaa-fm1-mac10`
- `vppctl show hardware-interfaces` shows `driver=dpaa`
- No VPP crash within 60 seconds

**Files:** `data/scripts/test-vpp-dpaa-v10.sh` (latest test script)

### Step 2 — 🔴 P0: Wire Custom VPP DPDK Plugin Build into CI

**What:** Add CI steps to `auto-build.yml` that:
1. Cross-compile DPDK 24.11 with DPAA1 PMD enabled + apply `data/dpdk-portal-mmap.patch`
2. Apply VPP source patches via `bin/patch-vpp-dpaa-mempool.sh`
3. Rebuild `vpp-plugin-dpdk` against DPAA-enabled DPDK
4. Replace upstream `vpp-plugin-dpdk` `.deb` in the ISO package pool

**Why this is the critical blocker:** The upstream `vpp-plugin-dpdk` has zero DPAA ELF symbols. The custom plugin exists but isn't in the CI pipeline.

**Build scripts that exist (not yet in CI):**
- `bin/build-dpdk.sh` — cross-compiles DPDK with DPAA PMD
- `bin/build-vpp-dpdk-plugin.sh` — rebuilds VPP DPDK plugin
- `bin/patch-vpp-dpaa-mempool.sh` — applies 7 VPP source patches
- `data/dpdk-portal-mmap.patch` — portal mmap for DPDK `process.c`

**Depends on:** Step 1 (validate plugin works before adding to CI)

### ~~Step 3~~ — ✅ DONE: VPP startup.conf DPAA Template

Patch 010 already handles everything needed:
- `no-pci` when only platform-bus (DPAA) NICs are configured
- Skips `dev` entries for `fsl_dpa` NICs (DPAA bus auto-discovers)
- PCI/platform-bus classification via `original_driver` injection
- `poll-sleep-usec` is user-configurable: `set vpp settings poll-sleep-usec 100`

### ~~Step 4~~ — ✅ N/A: U-Boot Bootargs for DPAA Portals

**Not needed.** Our mainline USDPAA driver (patches 0002/0003) uses `bman_portal_reserve()`/`qman_portal_reserve()` API to claim idle portals from the kernel's pool. The `bportals=s0 qportals=s0` bootargs are NXP SDK-specific — DPDK DPAA PMD source has zero references to them.

### Step 5 — 🟢 P2: End-to-End Hardware Validation

**What:** Deploy CI-built ISO with DPAA-enabled VPP on Mono Gateway and validate:
- VPP discovers DPAA1 SFP+ interfaces (eth3/eth4)
- 10G wire-speed forwarding between SFP+ ports
- Thermal stability under continuous poll mode
- Graceful rebind on `delete vpp settings interface ethX` + commit
- Jumbo MTU 9578 (vs 3290 AF_XDP limit)

**Depends on:** Steps 2 + 3

### Step 6 — 🟢 P3: Performance Benchmarks

**What:** Establish baselines and compare:

| Metric | AF_XDP baseline | DPAA PMD target |
|--------|----------------|-----------------|
| iperf3 TCP 1500MTU 4-stream | ~3.5 Gbps | >9 Gbps |
| iperf3 TCP 9000MTU 4-stream | N/A (MTU cap) | ~9.4 Gbps |
| 64B UDP PPS | ~4-5 Mpps | >8 Mpps |
| Max usable SFP+ MTU | 3290 | 9578 |
| CPU temp at 10G load | TBD | <65°C target |

### Step 7 — 🔵 P4: FIB/FIB6 Offload (Future)

Kernel → VPP route synchronization for hardware forwarding tables. Entirely future work.

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

**Note:** Unlike the original plan's C.2 (creating a separate USDPAA DTS variant), the current approach uses runtime unbind/rebind via `vpp.py` patch 010. No separate DTB needed — all ports start under kernel control and VPP dynamically claims configured ports.

---

## VyOS Integration Plumbing (Complete)

| Component | File | Status |
|-----------|------|--------|
| Platform-bus support in `vpp.py` | `data/vyos-1x-010-vpp-platform-bus.patch` | ✅ In CI |
| VPP stop rebind script | `auto-build.yml` → `/usr/local/bin/vpp-dpaa-rebind` | ✅ In CI |
| Systemd `ExecStopPost` drop-in | `auto-build.yml` → `/etc/systemd/system/vpp.service.d/dpaa-rebind.conf` | ✅ In CI |
| Fan thermal management | `data/scripts/fancontrol-setup.sh` + `fancontrol.conf` | ✅ In CI |
| Port name remapping | `data/scripts/fman-port-name` + udev rules + `.link` file | ✅ In CI |
| Upstream VPP packages | `vpp`, `vpp-plugin-core`, `vpp-plugin-dpdk` from VyOS repos | ✅ In ISO |

---

## Key Files

| File | Status | Purpose |
|------|--------|---------|
| `data/kernel-patches/9001-usdpaa-bman-qman-exports-and-driver.patch` | ✅ In CI | Combined kernel patch: exports + Kconfig + Makefile |
| `data/kernel-patches/fsl_usdpaa_mainline.c` | ✅ In CI | USDPAA chardev driver (1514 lines, 20 ioctls) |
| `data/kernel-patches/4002-hwmon-ina2xx-add-INA234-support.patch` | ✅ In CI | INA234 power sensor support |
| `data/kernel-patches/4003-sfp-rollball-phylink-einval-fallback.patch` | ✅ In CI | SFP rollball PHY workaround |
| `data/dpdk-portal-mmap.patch` | ✅ Exists | DPDK `process.c` portal mmap (not yet in CI build) |
| `bin/patch-vpp-dpaa-mempool.sh` | ✅ Exists | 7 VPP source patches for DPAA mempool/driver |
| `bin/build-dpdk.sh` | ✅ Exists | DPDK cross-compilation script (not yet in CI) |
| `bin/build-vpp-dpdk-plugin.sh` | ✅ Exists | VPP DPDK plugin rebuild script (not yet in CI) |
| `bin/build-usdpaa-mainline.sh` | ✅ Exists | USDPAA build helper |
| `data/scripts/test-vpp-dpaa-v1..v10.sh` | ✅ Exists | Progressive hardware test scripts |
| `data/vyos-1x-010-vpp-platform-bus.patch` | ✅ In CI | `vpp.py` platform-bus unbind/rebind |
| `plans/MAINLINE-PATCH-SPEC.md` | ✅ Exists | 6-patch design spec and symbol audit |
| `plans/USDPAA-IOCTL-SPEC.md` | ✅ Exists | Complete NXP ioctl ABI (20 ioctls, all structs) |

---

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Custom plugin fails on gateway** | Phase C blocked | Capture VPP logs + dmesg via serial; root causes #27-#29 fixes may need iteration |
| **CI/ISO VPP plugin ABI drift** | Plugin built on LXC doesn't match upstream VPP version in ISO | Pin VPP version; rebuild against exact upstream VPP source tag |
| **DPAA PMD thermal under continuous polling** | SoC overheats (87°C measured without mitigation) | `poll-sleep-usec 100` mandatory; fancontrol already in CI |
| ~~kexec double-boot with portal bootargs~~ | ~~Extra reboot cycle~~ | N/A — `bportals`/`qportals` not needed with mainline USDPAA portal reservation API |
| **Cross-compilation complexity on CI runner** | ARM64 DPDK/VPP build may be slow or fail | Time-box to 30 min; pre-build on LXC and commit binary if needed |
| **Upstream VPP update breaks patches** | VPP source patches target specific code locations | Pin to VPP release tag; rebase patches when VyOS updates VPP |

---

## Hardware Constraints

- LS1046A: 4× Cortex-A72 @ 1.8 GHz, 4 GB DDR4 (8 GB on Mono Gateway)
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
- [`plans/USDPAA-IOCTL-SPEC.md`](USDPAA-IOCTL-SPEC.md) — Complete NXP ioctl ABI (20 ioctls, all structs)
- [`PORTING.md`](../PORTING.md) — DPAA1 driver archaeology, kernel history
- [`UBOOT.md`](../UBOOT.md) — U-Boot memory map, DTB loading, `fw_setenv`
- [`plans/DEV-LOOP.md`](DEV-LOOP.md) — TFTP fast iteration loop
- [GitHub Issue #3](https://github.com/mihakralj/vyos-ls1046a-build/issues/3) — VPP DPAA1 PMD tracking issue