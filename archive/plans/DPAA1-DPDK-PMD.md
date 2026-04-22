# DPAA1 DPDK PMD Integration Plan: 10G Wire-Speed VPP on Mono Gateway

> **Status (2026-04-03):** 🔶 **TWO CRITICAL BUGS FIXED, BUILD IN PROGRESS.** Build [#23932476016](https://github.com/mihakralj/vyos-ls1046a-build/actions/runs/23932476016) (commit `70ad6a9`) fixes: (1) sysfs unbind path — `_dpaa_find_platform_dev()` navigated wrong device tree, (2) DPDK constructor preservation — GROUP linker script silently dropped `RTE_REGISTER_BUS`/`RTE_PMD_REGISTER` constructors, leaving DPDK with zero registered buses. Portal mmap SIGSEGV already fixed in `d821973`. VPP starts, plugin loads, no crash — but showed zero interfaces due to bugs 1+2. Kernel 10G baseline established: **6.52–6.71 Gbps TCP** via `iperf3 --bind-dev eth3`.
>
> **Goal:** Replace AF_XDP (~3.5 Gbps measured, 3290 MTU cap) with DPAA1 DPDK Poll Mode Driver for full 10G line rate (~9.4 Gbps, full jumbo 9578 MTU).
>
> **Key insight:** Both the kernel USDPAA driver and the DPDK DPAA1 PMD are **mainline**. No NXP forks needed anywhere in the stack. Our combined kernel patch `9001` + `fsl_usdpaa_mainline.c` provide the kernel-side USDPAA ABI. DPDK 24.11 mainline with DPAA enabled provides the userspace PMD. VPP plugin is built out-of-tree in CI via `bin/ci-build-dpdk-plugin.sh`.

---

## Achievement Status

### Phase A: Kernel Patches — ✅ COMPLETE
### Phase B: DPDK Build & Standalone Validation — ✅ COMPLETE
### Phase C: VPP DPDK Plugin Build — ✅ COMPLETE
### Phase D: CI/ISO Integration — ✅ COMPLETE
### Phase E: Hardware Validation — 🔶 IN PROGRESS

| Item | Status | Details |
|------|--------|---------|
| E.1: Portal mmap fix | ✅ Done | Patch `d821973` — VPP starts without SIGSEGV |
| E.2: Plugin loads in VPP | ✅ Done | 13MB dpdk_plugin.so loads, VPP runs |
| E.3: sysfs unbind fix | ✅ Done | Walk `/sys/class/net/<iface>/device/` (commit `70ad6a9`) |
| E.4: DPDK constructor preservation | ✅ Done | `ld -r --whole-archive` fat archive (commit `70ad6a9`) |
| E.5: Kernel 10G baseline | ✅ Done | **6.52–6.71 Gbps** TCP via `iperf3 --bind-dev eth3` |
| E.6: VPP discovers DPAA interfaces | ❌ **Awaiting build** | Build #23932476016 has both fixes |
| E.7: DPAA PMD throughput | ❌ Not started | Target: ≥6.5 Gbps |
| E.8: Jumbo MTU 9578 | ❌ Not started | vs 3290 AF_XDP limit |
| E.9: Thermal stability | ❌ Not started | `poll-sleep-usec 100` |
| E.10: Graceful rebind | ❌ Not started | Kernel regains port on VPP stop |

---

## Root Causes Log (37 total)

| # | Root Cause | Fix | Phase |
|---|-----------|-----|-------|
| 1–18 | Kernel build/boot/portal/DMA issues | See git history | A/B |
| 21–34 | VPP plugin build/link/deploy issues | See git history | C/D |
| **35** | **Portal mmap patch malformed hunk headers** | **Regenerated from DPDK v24.11 source (`d821973`)** | **E** |
| **36** | **sysfs unbind: `net/` on parent `fsl_dpaa_mac`, not child `dpaa-ethernet.N`** | **Walk `/sys/class/net/<iface>/device/` for child (`70ad6a9`)** | **E** |
| **37** | **GROUP linker script drops `__attribute__((constructor))` bus/PMD registrations** | **`ld -r --whole-archive` fat archive preserves all constructors (`70ad6a9`)** | **E** |

### Root Cause #36 Detail

Two drivers per FMan MAC: `fsl_dpaa_mac` (parent, MAC control) and `fsl_dpa` (child `dpaa-ethernet.N`, netdev). The netdev's sysfs `device` link points to the parent. Old code checked `/sys/bus/platform/drivers/fsl_dpa/dpaa-ethernet.N/net/<iface>` — path doesn't exist because `net/` is on the parent. Fix: lookup via `/sys/class/net/<iface>/device/dpaa-ethernet.*`.

### Root Cause #37 Detail

DPDK self-registers buses and PMDs via `RTE_REGISTER_BUS(dpaa_bus,...)` / `RTE_PMD_REGISTER(net_dpaa,...)` which create `__attribute__((constructor))` functions. A GROUP linker script only pulls `.o` files satisfying unresolved references — since VPP never directly calls `dpaa_bus` symbols, those `.o` files (with constructors) are dropped. Result: zero registered buses, zero device scan, zero interfaces. Fix: `ld -r --whole-archive` merges all `.o` into one relocatable object; since `rte_eal_init` is referenced, the entire object (all constructors) is included.

---

## Remaining Steps

### Step 1 — 🔴 P0: Verify Interface Discovery
Deploy build #23932476016. Check `/run/vpp-dpaa-unbound.json`, `journalctl -u vpp` for DPAA bus scan, `vppctl show interface`.

### Step 2 — 🟡 P1: Root Cause #31 Assessment
Test if `dpaa_bus` init disrupts kernel RJ45 ports. DTS limits `fsl,dpaa` to SFP+ only — should be safe.

### Step 3 — 🟢 P2: Performance Validation
iperf3 TCP ≥6.5 Gbps, jumbo MTU 9578, thermal soak with `poll-sleep-usec 100`.

### Step 4 — 🔵 P3: Graceful Port Handoff
Verify `delete vpp settings interface ethX` + commit restores kernel via `dpaa-rebind.conf`.

### Step 5 — 🔵 P4: FMD Shim for RSS (Future)
Multi-queue spec at `plans/FMD-SHIM-SPEC.md`. Enables multi-worker VPP (2× throughput target).

---

## Architecture

```
AF_XDP:   Wire → FMan → kernel eth → AF_XDP socket → VPP → AF_XDP → kernel → FMan → Wire
DPAA PMD: Wire → FMan → BMan pool → DPDK DPAA PMD → VPP → DPDK PMD → BMan → FMan → Wire
```

| Aspect | AF_XDP | DPAA PMD |
|--------|--------|----------|
| SFP+ MTU | **3290** | **9578** |
| Buffer copy | copy-mode | zero-copy |
| VPP plugin | `af_xdp_plugin` | `dpdk_plugin` |
| Peak throughput | ~3.5 Gbps | ~9.4+ Gbps target |

---

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|------------|
| Root Cause #31: dpaa_bus corrupts kernel interfaces | No mixed mode | DTS limits to SFP+; AF_XDP fallback |
| Upstream VPP API breaks patches | Build fails | Robust awk patching; version checks |
| Thermal under polling | SoC overheats | `poll-sleep-usec 100`; fancontrol |
| Static linking bloat | >15MB plugin | Acceptable — self-contained |

## See Also

- [`VPP.md`](../VPP.md), [`VPP-SETUP.md`](../VPP-SETUP.md), [`MAINLINE-PATCH-SPEC.md`](MAINLINE-PATCH-SPEC.md), [`USDPAA-IOCTL-SPEC.md`](USDPAA-IOCTL-SPEC.md), [`FMD-SHIM-SPEC.md`](FMD-SHIM-SPEC.md)