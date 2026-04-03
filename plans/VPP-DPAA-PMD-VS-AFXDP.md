# DPAA1 DPDK PMD vs AF_XDP — Technical Assessment

> **Status (2026-04-03):** 🔴 **DPAA PMD BLOCKED by RC#31.** AF_XDP is the active production path.
> Hardware-confirmed 2026-04-03 on .153 and .157. This document provides the deep technical
> analysis for the architectural decision on whether to retain or remove DPAA PMD infrastructure.

---

## 1. Executive Summary

| Dimension | DPDK DPAA PMD | AF_XDP |
|-----------|--------------|--------|
| **Throughput** | Target 9.4 Gbps (10G line-rate) | ~3.5 Gbps measured |
| **Kernel coexistence** | 🔴 IMPOSSIBLE (RC#31) | ✅ Works natively |
| **Driver model** | Userspace bypass via /dev/mem + /dev/fsl-usdpaa | BPF socket on kernel netdev |
| **Unbind required** | Yes — fsl_dpa must be removed | No — kernel keeps netdev |
| **Management port safe** | 🔴 No — bus init kills ALL ports | ✅ Yes — only touches configured ports |
| **Maturity in our stack** | Plugin builds, never ran successfully end-to-end | Confirmed working on hardware |
| **Code footprint** | ~4,700 lines (kernel + CI + patches + shims) | ~80 lines (patch-010 driver routing) |
| **CI time impact** | +12-15 min (DPDK cross-compile + VPP plugin build) | Zero additional |
| **Kernel config impact** | STRICT_DEVMEM=n (security regression), USDPAA=y | None beyond existing XDP |
| **Zero-copy** | Yes (DPDK manages BMan buffer pools directly) | No (copy mode — ~1.3% overhead) |

---

## 2. Root Cause #31 — The Fundamental Blocker

### What Happens

When VPP starts with the DPDK plugin loaded and DPAA PMD active:

1. DPDK `rte_eal_init()` calls `rte_bus_scan()` → `dpaa_bus_scan()`
2. `dpaa_bus_scan()` opens `/dev/fsl-usdpaa` and enumerates ALL BMan portals and QMan portals
3. `rte_bus_probe()` calls `dpaa_bus_probe()` which:
   - Initializes **ALL** BMan buffer pool IDs globally (not just VPP's ports)
   - Initializes **ALL** QMan frame queue descriptors globally
   - Configures QMan portal stashing for **ALL** available portals
4. The kernel's BMan and QMan drivers are simultaneously managing the **same** hardware resources
5. Within seconds: BMan buffer pool corruption → QMan stall → FMan frame delivery failure
6. **ALL** FMan interfaces die — including eth0 (management RJ45), causing total loss of SSH/network

### Why Unbinding Doesn't Help

The naive fix is unbinding `fsl_dpa` from VPP-assigned ports (eth3/eth4) before DPDK starts. Our patch-010 implemented this via `_dpaa_unbind_ifaces()`. The problem:

- `dpaa_bus_probe()` operates at the **bus level**, not the port level
- It initializes the BMan and QMan **hardware blocks** which are shared across ALL ports
- Even with eth3/eth4 unbound from fsl_dpa, the bus-level init corrupts the BMan pools and QMan FQDs that eth0/eth1/eth2 are actively using
- The LS1046A has a single BMan and single QMan instance shared by all 10 MACs

### Hardware Confirmation

```
2026-04-03 .153: Configured VPP with DPAA PMD on eth3/eth4.
  VPP started → dpaa_bus probe → ALL 5 interfaces died within ~3 seconds.
  Device unreachable. Required power cycle.

2026-03-29 .128: Earlier test with standalone testpmd.
  Same result — management port death on dpaa_bus init.
```

### What Would Fix RC#31

Three theoretical paths, none currently implemented:

1. **DPDK `dpaa_bus` scoping** (~2000 LOC estimated change in DPDK):
   - Modify `dpaa_bus_probe()` to only initialize portals/pools explicitly reserved for userspace
   - Requires new portal reservation API (our kernel patches 0002/0003 provide this)
   - Requires DPDK to read a portal allocation map from `/dev/fsl-usdpaa` ioctls
   - No upstream DPDK maintainer interest (DPAA1 is legacy platform)

2. **FMD Shim + selective QMan/BMan init** (spec exists: `plans/FMD-SHIM-SPEC.md`):
   - Kernel module intercepts FMan configuration
   - Would allow scoping DPDK to only claim specific FMan ports
   - Still doesn't solve BMan/QMan global init problem
   - Estimated ~2000 LOC kernel module — not yet implemented

3. **All-DPDK mode** (impractical):
   - Put ALL 5 interfaces under DPDK/VPP control
   - No kernel networking at all — serial-only management
   - Breaks VyOS CLI, SSH management, monitoring, config sync
   - Only viable for dedicated forwarding appliances

---

## 3. AF_XDP — How It Works on DPAA1

### Architecture

```
┌─────────────────────────────────┐
│          VPP Process            │
│  ┌────────────────────────┐     │
│  │   af_xdp_plugin.so     │     │
│  │  xsk_socket__create()  │     │
│  └──────────┬─────────────┘     │
│             │ AF_XDP socket     │
│             │ (umem ring)       │
├─────────────┼───────────────────┤
│   Kernel    │                   │
│  ┌──────────▼─────────────┐     │
│  │ XDP BPF prog (native)  │     │ Mode 1 = XDP_ATTACHED_DRV
│  │ JIT-compiled on ARM64  │     │
│  └──────────┬─────────────┘     │
│  ┌──────────▼─────────────┐     │
│  │  fsl_dpa driver        │     │ Stays bound — no unbind
│  │  dpaa_xdp() hook       │     │
│  └──────────┬─────────────┘     │
│  ┌──────────▼─────────────┐     │
│  │  FMan → BMan → QMan    │     │ Hardware
│  └────────────────────────┘     │
└─────────────────────────────────┘
```

### Key Properties

1. **Native XDP mode confirmed**: `ip -j -d link show eth3` → `mode: 1` (XDP_ATTACHED_DRV)
2. **BPF JIT**: Program is JIT-compiled on ARM64 Cortex-A72
3. **Copy mode**: DPAA1 `fsl_dpa` does NOT implement `ndo_xsk_wakeup` → packets copied from BMan buffer pool to XDP umem. Overhead ~1.3% of memory bandwidth at 1500B MTU — negligible
4. **No bus-level init**: AF_XDP operates at the socket level on a specific netdev. Zero interaction with BMan/QMan hardware state. Other ports are completely unaffected
5. **Kernel retains control**: `fsl_dpa` stays bound, netdev exists, `ip link` works, `ethtool` works, we can set MTU, monitor link state — full visibility

### Constraints

| Constraint | Impact | Workaround |
|-----------|--------|------------|
| **MTU ≤ 3290** | No jumbo frames on VPP ports | Kernel RJ45 ports retain full 9578 MTU. SFP+ at 3290 is sufficient for most traffic |
| **Copy mode only** | ~10-15% throughput penalty vs zero-copy | Would need deep `fsl_dpa` kernel patches to add `ndo_xsk_wakeup` — not worth the effort |
| **No adaptive rx-mode** | `set interface rx-mode` fails with "unable to set" | Use `poll-sleep-usec 100` — mandatory anyway for thermal protection |
| **Single queue** | Cannot use RSS/multi-queue | VPP processes all packets on main thread. DPAA1 FMan doesn't expose per-queue XDP anyway |
| **xdp-dispatcher EACCES** | libxdp's multi-prog dispatcher fails BPF verifier | Cosmetic — VPP falls back to direct `xsk_def_prog` which works correctly in native mode |

### Measured Performance

- **AF_XDP single-queue**: ~3.5 Gbps (measured with VPP on hardware)
- **Kernel TCP baseline**: 6.52-6.71 Gbps (iperf3 `--bind-dev eth3`)
- **AF_XDP theoretical ceiling**: ~4-5 Gbps with optimized umem layout
- **DPAA1 wire-rate**: 9.4 Gbps (never achieved through VPP, only matches spec)

---

## 4. DPAA PMD Infrastructure Inventory

All code/config that exists solely for DPDK DPAA PMD support:

### Kernel Layer (built into every ISO)

| File | Lines | Purpose |
|------|-------|---------|
| `data/kernel-patches/fsl_usdpaa_mainline.c` | 1,514 | `/dev/fsl-usdpaa` + `/dev/fsl-usdpaa-irq` chardevs, 20 NXP-ABI-compatible ioctls |
| `data/kernel-patches/9001-usdpaa-bman-qman-exports-and-driver.patch` | 428 | BMan/QMan symbol exports, portal phys addr storage, reservation APIs, Kconfig |
| `data/kernel-config/ls1046a-usdpaa.config` | 5 | `CONFIG_FSL_USDPAA_MAINLINE=y`, disables `STRICT_DEVMEM` |
| **Subtotal** | **1,947** | |

### CI Build Layer (adds ~12-15 min to build time)

| File | Lines | Purpose |
|------|-------|---------|
| `bin/ci-build-dpdk-plugin.sh` | 313 | Cross-compiles DPDK 24.11 static, builds VPP dpdk_plugin.so with DPAA PMD |
| `data/dpdk-portal-mmap.patch` | 80 | DPDK `process.c` portal mmap after PORTAL_MAP ioctl |
| `data/strlcpy-shim.c` | 27 | BSD strlcpy/strlcat for glibc 2.36 target |
| `data/cmake/CMakeLists.txt` | 26 | Out-of-tree CMake for VPP DPDK plugin build |
| `data/hooks/97-dpaa-dpdk-plugin.chroot` | 21 | Live-build hook: deploys DPAA dpdk_plugin.so, installs binutils |
| **Subtotal** | **467** | |

### Documentation/Plans

| File | Lines | Purpose |
|------|-------|---------|
| `plans/DPAA1-DPDK-PMD.md` | ~160 | Original integration plan |
| `plans/MAINLINE-PATCH-SPEC.md` | 821 | Kernel patch design spec |
| `plans/USDPAA-IOCTL-SPEC.md` | ~1,800 | Complete 20-ioctl ABI spec |
| `plans/FMD-SHIM-SPEC.md` | 375 | FMD shim module design (not implemented) |
| **Subtotal** | **~3,156** | |

### Archive (already not in build, kept for reference)

| Directory | Purpose |
|-----------|---------|
| `archive/bin/build-dpdk*.sh` | Historical DPDK build scripts |
| `archive/data/test-portal-mmap.c` | Portal mmap test harness |
| `archive/data/startup.conf.*` | Historical VPP configs |
| `archive/plans/fsl_usdpaa.*` | NXP SDK reference source |

### Security Impact

The USDPAA kernel config disables `STRICT_DEVMEM`:
```
# CONFIG_STRICT_DEVMEM is not set
# CONFIG_IO_STRICT_DEVMEM is not set
```
This allows any root process to mmap arbitrary physical memory via `/dev/mem`. On a router/firewall, this is a security regression. AF_XDP requires no such kernel weakening.

---

## 5. What DPAA PMD Achieved (Before RC#31 Blocked It)

Despite being blocked, substantial infrastructure was built and validated:

| Milestone | Status | Evidence |
|-----------|--------|----------|
| Kernel USDPAA chardevs | ✅ Working | `/dev/fsl-usdpaa` (crw 10,257) confirmed on device |
| BMan/QMan symbol exports | ✅ Working | 4 kernel patches applied cleanly, symbols exported |
| Portal reservation API | ✅ Working | `bman_portal_reserve()`/`qman_portal_reserve()` callable |
| DPDK 24.11 cross-compile | ✅ Working | Static `libdpdk.a` with DPAA PMD built in CI |
| VPP dpdk_plugin.so | ✅ Working | 13MB, 1182 DPAA symbols, PMD constructor present |
| Plugin deployment to ISO | ✅ Working | Chroot hook 97 confirmed replacing upstream plugin |
| Portal mmap fix | ✅ Working | SIGSEGV resolved, VPP ran without crash |
| sysfs unbind path | ✅ Working | `_dpaa_find_platform_dev()` correctly walks parent→child |
| Constructor preservation | ✅ Working | `ld -r --whole-archive` fat archive approach |
| **End-to-end VPP+DPAA PMD** | 🔴 **FAILED** | RC#31 — bus init kills all interfaces |

---

## 6. Cost-Benefit Analysis

### If We KEEP DPAA PMD Infrastructure

**Benefits:**
- Ready to activate if RC#31 is ever fixed (DPDK code change or FMD shim)
- USDPAA chardevs could serve other DPAA1 userspace tools (testpmd already validated)
- `/dev/fsl-usdpaa` is a clean implementation that could be upstreamed

**Costs:**
- +12-15 min CI build time per ISO (DPDK cross-compile + VPP plugin link)
- 1,947 lines of kernel code in every boot (USDPAA module, even if unused)
- `STRICT_DEVMEM` disabled — security regression for a feature that can't be used
- chroot hook complexity (97-dpaa-dpdk-plugin.chroot replaces upstream plugin)
- Maintenance burden: DPDK version bumps, VPP API changes, ABI drift
- Mental overhead: two code paths (AF_XDP active, DPDK dormant) confuse future work
- binutils forced-install adds ~30MB to ISO for plugin diagnostics

### If We REMOVE DPAA PMD Infrastructure

**Benefits:**
- CI builds ~12-15 min faster
- `STRICT_DEVMEM=y` can be re-enabled (security improvement)
- Simpler codebase: one code path (AF_XDP), one driver model
- ~2,400 fewer lines of active code (kernel + CI)
- No chroot hook complexity
- No binutils forced-install
- Cleaner ISO: upstream dpdk_plugin.so (with its standard PCI drivers) is sufficient

**Costs:**
- If RC#31 is fixed someday, infrastructure must be rebuilt (~2-3 days of work)
- USDPAA chardevs no longer available for standalone testpmd experiments
- Psychological sunk-cost of extensive engineering effort on the PMD path

### If We ARCHIVE But Don't Build

**Middle ground:**
- Move DPAA PMD files to `archive/` (already have archive directory)
- Remove from CI build pipeline
- Re-enable `STRICT_DEVMEM`
- Keep kernel patches in archive for reference
- Zero CI time, zero security impact, code preserved for future

---

## 7. AF_XDP Performance Improvement Opportunities

Even staying on AF_XDP, there are potential throughput gains:

| Optimization | Estimated Gain | Effort | Status |
|-------------|---------------|--------|--------|
| XDP umem frame size tuning | +5-10% | Low | Not attempted |
| `poll-sleep-usec` optimization | +5-15% (latency vs throughput tradeoff) | Low | Currently fixed at 100µs |
| Kernel `fsl_dpa` zero-copy XDP support | +20-30% | High (~500 LOC kernel patch) | Would need `ndo_xsk_wakeup` impl |
| Multi-queue AF_XDP (if FMan supports it) | +50-100% | High | FMan has multi-queue but driver doesn't expose per-queue XDP |
| MTU 3290 — larger frames | +10% for bulk traffic | Zero | Already at max XDP MTU |
| VPP graph optimizations | +5-10% | Medium | Vector size, prefetch tuning |

Realistic AF_XDP ceiling with low-effort tuning: **~4-5 Gbps** (up from 3.5 Gbps).
With kernel zero-copy patch: **~5-6 Gbps** (matches kernel TCP baseline).

---

## 8. All-DPDK Mode — The Third Path

### Concept

Instead of mixed kernel+DPDK (blocked by RC#31), put **ALL 5 interfaces** under DPDK DPAA PMD. RC#31 doesn't apply because there are no kernel FMan interfaces to corrupt — DPDK owns everything. Management traffic (SSH, VyOS CLI, BGP, OSPF) flows through VPP's **LCP (Linux Control Plane)** plugin, which creates TAP interfaces mirroring each DPDK port back into Linux.

### Architecture

```
┌──────────────────────────────────────────────────┐
│                    Linux                          │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐   │
│  │tap-e0│ │tap-e1│ │tap-e2│ │tap-e3│ │tap-e4│   │  VyOS CLI, SSH, routing daemons
│  └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘   │
│     │ punt    │ punt    │ punt    │        │       │
├─────┼─────────┼─────────┼─────────┼────────┼──────┤
│     │ inject  │ inject  │ inject  │        │       │
│  ┌──▼───┐ ┌──▼───┐ ┌──▼───┐ ┌──▼───┐ ┌──▼───┐   │
│  │ lcp0 │ │ lcp1 │ │ lcp2 │ │ lcp3 │ │ lcp4 │   │  VPP LCP interfaces
│  └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘   │
│     │         │         │         │        │       │
│  ┌──▼───────────────────────────────────────────┐ │
│  │           VPP Packet Processing               │ │  L2/L3 forwarding, ACL, NAT
│  └──┬───┬───┬───┬───┬──────────────────────────┘ │
│  ┌──▼──┐│┌──▼──┐│┌──▼──┐ ┌──▼──┐ ┌──▼──┐        │
│  │DPAA ││ DPAA ││ DPAA │ │DPAA │ │DPAA │        │  DPDK DPAA PMD
│  │PMD  ││ PMD  ││ PMD  │ │PMD  │ │PMD  │        │
│  │eth0 ││ eth1 ││ eth2 │ │eth3 │ │eth4 │        │
│  └──┬──┘└──┬──┘└──┬───┘ └──┬──┘ └──┬──┘        │
├─────┼──────┼──────┼────────┼───────┼────────────┤
│  ┌──▼──────▼──────▼────────▼───────▼──┐          │
│  │        FMan Hardware                │          │  1G RJ45 + 10G SFP+
│  └────────────────────────────────────┘          │
└──────────────────────────────────────────────────┘
```

### Data Paths

| Traffic Type | Path | Performance |
|-------------|------|-------------|
| **Transit SFP+ (eth3↔eth4)** | FMan → DPDK PMD → VPP graph → DPDK PMD → FMan | **~9.4 Gbps** (wire-rate) |
| **Transit RJ45→SFP+** | FMan → DPDK PMD → VPP graph → DPDK PMD → FMan | **~1 Gbps** (RJ45 limited) |
| **Management SSH (to router)** | FMan → DPDK PMD → VPP → LCP punt → TAP → Linux | **Low latency** (~0.5ms extra) |
| **BGP/OSPF (from router)** | Linux → TAP → LCP inject → VPP → DPDK PMD → FMan | **Low bandwidth** (negligible overhead) |

### What Makes This Work

1. **RC#31 becomes irrelevant**: No kernel FMan interfaces exist. DPDK dpaa_bus can init everything freely
2. **Existing infrastructure is ready**: USDPAA chardevs, DPDK plugin, portal mmap — all validated
3. **VPP LCP is production-grade**: Used by major ISPs (Cisco, Comcast deployments)
4. **VyOS already has LCP awareness**: VPP integration creates TAP mirrors for assigned interfaces
5. **Serial console always works**: Even if VPP dies, UART is unaffected

### Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **VPP crash = total network outage** | ALL interfaces die until VPP restarts (~5-10s) | systemd `Restart=always`, watchdog timer |
| **VPP config error = locked out** | No SSH, serial-only recovery | Config validation before commit, IMX2 watchdog auto-reboot |
| **Thermal shutdown kills networking** | VPP poll-mode overheats → killed → no network | `poll-sleep-usec 100` + fancontrol (proven working), thermal trip kills VPP before hardware damage |
| **Boot window with no networking** | ~30-60s from power-on to VPP+LCP ready | Acceptable for embedded appliance; serial available throughout |
| **kexec reboots lose networking** | VPP stops during kexec → ~10s gap | Already happens with current VPP; managed-params self-healing works |
| **More hugepages needed** | 5 interfaces vs 2 under DPDK | Increase from 512 to 768 2M pages (1.5GB of 8GB) |

### Detailed Operational Walkthrough

#### Boot Sequence

```
T=0s    Power on → U-Boot → kernel boots from eMMC squashfs
T=~40s  Kernel up. fsl_dpa binds ALL dpaa-ethernet.N → eth0-eth4 exist as kernel netdevs
T=~45s  vyos-postinstall.service: writes /boot/vyos.env, fw_setenv (if needed)
T=~50s  vpp.service starts (Before=vyos-router.service):
          1. _dpaa_unbind_ifaces() unbinds ALL 5 dpaa-ethernet.N from fsl_dpa
             → eth0-eth4 kernel netdevs DISAPPEAR
          2. VPP process starts with DPDK DPAA PMD
             → dpaa_bus_probe() initializes ALL BMan/QMan (RC#31 is SAFE — no kernel FMan left)
             → DPDK discovers 5 FMan MACs
          3. VPP creates LCP TAP interfaces:
             tap-eth0 (mirrors DPDK eth0, MAC copied)
             tap-eth1 (mirrors DPDK eth1, MAC copied)
             tap-eth2 (mirrors DPDK eth2, MAC copied)
             tap-eth3 (mirrors DPDK eth3, MAC copied)
             tap-eth4 (mirrors DPDK eth4, MAC copied)
          4. Default punt rule: ALL unmatched traffic → LCP TAP → Linux
T=~55s  vyos-router.service starts:
          → VyOS reads config.boot
          → Applies IP addresses to tap-ethN interfaces (NOT original ethN — those are gone)
          → Applies firewall rules, NAT, DHCP, BGP, OSPF to tap interfaces
          → VPP-specific config: L3 forwarding rules for SFP+ ports
T=~60s  SSH available on tap-eth0 at 192.168.1.157
```

Key: During T=50-55s (~5 seconds), there is **no networking**. Serial console works throughout.

#### Interface Naming

VyOS config sees TAP interfaces, but they can be named to match original ethN:

| Hardware Port | DPDK Interface | LCP TAP | VyOS Config Name |
|--------------|---------------|---------|-----------------|
| Left RJ45 | dpaa-eth0 (DPDK) | tap-eth0 | `eth0` (via LCP naming) |
| Center RJ45 | dpaa-eth1 (DPDK) | tap-eth1 | `eth1` |
| Right RJ45 | dpaa-eth2 (DPDK) | tap-eth2 | `eth2` |
| Left SFP+ | dpaa-eth3 (DPDK) | tap-eth3 | `eth3` |
| Right SFP+ | dpaa-eth4 (DPDK) | tap-eth4 | `eth4` |

VPP's LCP plugin supports `lcp create <hw-iface> host-if <name>` — TAPs can be named `eth0`, `eth1`, etc. From VyOS CLI perspective, **nothing changes** — the user sees the same interface names.

#### Data Flow by Port Role

**RJ45 Management Ports (eth0/eth1/eth2) — "passthrough" to Linux:**

```
   Incoming packet on RJ45
          │
    FMan hardware receives
          │
    BMan buffer allocation
          │
    QMan enqueues to DPDK RX ring
          │
    DPDK DPAA PMD polls (VPP main loop)
          │
    VPP graph: classify → no VPP route match
          │
    VPP LCP punt → memcpy to TAP fd
          │
    Linux kernel: tap-eth0 → IP stack → VyOS
          │
    (SSH session, BGP update, DHCP reply, etc.)
```

For outgoing traffic (Linux → wire):
```
    Linux generates packet (SSH response, BGP, etc.)
          │
    Writes to tap-eth0
          │
    VPP LCP inject → VPP TX ring
          │
    DPDK DPAA PMD TX → QMan → FMan → wire
```

**Performance impact on RJ45**: Extra TAP copy adds ~50µs latency and ~5% CPU overhead. At 1 Gbps this is invisible — the RJ45 PHY is the bottleneck, not the TAP.

**SFP+ Forwarding Ports (eth3/eth4) — VPP wire-speed:**

```
   Incoming packet on SFP+ (10G)
          │
    FMan hardware receives
          │
    BMan buffer → QMan → DPDK PMD polls
          │
    VPP graph: classify → IP lookup in VPP FIB
          │
    ┌─────┴──────────────┐
    │                     │
    Match: forward        No match: punt to Linux
    via VPP L3 route      via LCP TAP (control plane)
    │                     │
    DPDK PMD TX           tap-eth3 → Linux
    → FMan → wire         (BGP, OSPF, ARP, ICMP)
    (~9.4 Gbps)           (low bandwidth)
```

**The selective part**: VPP only does high-speed forwarding for traffic matching VPP FIB entries. Everything else (ARP, ICMP, BGP, OSPF, SSH-to-router) is punted to Linux through the TAP. This is automatic — no per-packet classification needed. VPP's L3 FIB is synced from Linux routing table via LCP.

#### VyOS Configuration Example

```
# === Management ports — normal VyOS routing through LCP TAPs ===
set interfaces ethernet eth0 address 192.168.1.157/24
set interfaces ethernet eth0 description 'Management LAN'
set interfaces ethernet eth1 address 10.1.1.1/24
set interfaces ethernet eth2 address 10.2.2.1/24

# === SFP+ ports — VPP wire-speed forwarding + LCP control plane ===
set interfaces ethernet eth3 address 10.10.0.1/24
set interfaces ethernet eth3 mtu 9000
set interfaces ethernet eth4 address 10.20.0.1/24
set interfaces ethernet eth4 mtu 9000

# === VPP settings — ALL interfaces under DPDK, forwarding on SFP+ ===
set vpp settings mode all-dpdk
set vpp settings poll-sleep-usec 100
set vpp settings interface eth3
set vpp settings interface eth4

# === Normal VyOS features work on all ports ===
set firewall name WAN rule 10 action accept
set nat source rule 100 outbound-interface eth3
set protocols bgp neighbor 10.10.0.2
set service ssh listen-address 192.168.1.157
```

The `set vpp settings mode all-dpdk` flag is what triggers:
1. Unbind ALL fsl_dpa (not just configured interfaces)
2. Enable LCP plugin for TAP creation on ALL ports  
3. DPDK uses DPAA PMD for all ports (10G wire-speed capable)

The `set vpp settings interface eth3/eth4` specifies which ports get VPP L3 fast-path forwarding. Ports NOT listed (eth0/eth1/eth2) are pure LCP passthrough — Linux handles all their routing.

#### Failsafe Mechanism

If VPP fails to start (config error, crash, etc.):

```
vpp.service ExecStartPre:
  1. Unbind all fsl_dpa

vpp.service ExecStart:
  2. Start VPP → FAILS

vpp.service ExecStopPost (on failure):
  3. Rebind dpaa-ethernet.0 to fsl_dpa → eth0 kernel netdev restored
  4. Apply emergency IP: ip addr add 192.168.1.157/24 dev eth0
  5. SSH management restored on eth0 (kernel-direct, no VPP)
  6. Log error, alert via serial console
```

This ensures that even a completely broken VPP config doesn't permanently lock out SSH access.

### Implementation Estimate (~1-2 weeks)

1. **Patch-010 all-DPDK mode** (~200 LOC): Unbind ALL fsl_dpa before VPP, configure LCP TAPs
2. **startup.conf.j2 LCP section** (~50 LOC): Enable lcp_plugin.so, configure punt/inject, TAP naming
3. **Service ordering** (~20 LOC): VPP Before= VyOS router service
4. **Failsafe rebind** (~50 LOC): If VPP fails to start, rebind fsl_dpa to eth0 for emergency access
5. **TAP naming integration** (~30 LOC): Map LCP TAPs to ethN names for VyOS compatibility
6. **Hugepage tuning**: Increase default from 512 to 768 2M pages
7. **Testing**: Full regression on all 5 interfaces, management through LCP, crash recovery, failsafe

### Why This Changes the DPAA PMD Decision

The all-DPDK path makes the DPAA PMD infrastructure **strategically valuable** rather than dead code:
- Kernel patches (USDPAA, portal exports) → needed for DPDK DPAA PMD
- CI DPDK plugin build → needed for dpdk_plugin.so with DPAA PMD
- STRICT_DEVMEM disabled → needed for DPDK FMan register access
- All the "dormant" infrastructure becomes the foundation for 10G wire-speed

---

## 9. Recommendation (Updated)

**Keep DPAA PMD infrastructure dormant. AF_XDP is the current production path. All-DPDK+LCP is the future upgrade path to 10G wire-speed.**

Rationale:
1. AF_XDP works NOW at 3.5 Gbps — ship it as the safe default
2. All-DPDK+LCP is viable but needs ~1-2 weeks implementation + testing
3. DPAA PMD infrastructure is the prerequisite for all-DPDK, so don't archive it
4. The `STRICT_DEVMEM` security cost is acceptable IF the 10G path is actively pursued
5. CI time (+12-15 min) is a fair price for keeping the 10G option open

### Phased Approach

| Phase | Path | Throughput | Management | Status |
|-------|------|-----------|------------|--------|
| **Phase 1 (now)** | AF_XDP on SFP+ | ~3.5 Gbps | Kernel (always safe) | ✅ Working, needs device test |
| **Phase 2 (future)** | All-DPDK + LCP | ~9.4 Gbps | VPP LCP TAPs | Design ready, ~1-2 weeks impl |

### Decision Matrix

| If you need... | Choose... |
|----------------|-----------|
| Maximum reliability, safe management | AF_XDP (Phase 1) |
| 10G wire-speed, accept VPP dependency for mgmt | All-DPDK+LCP (Phase 2) |
| Per-deployment choice | Both — config flag `set vpp settings mode` |

---

## Appendix A: Timeline of DPAA PMD Engineering

| Date | Event |
|------|-------|
| 2026-03-15 | USDPAA mainline kernel patch series written (6 patches) |
| 2026-03-18 | DPDK 24.11 cross-compiled with DPAA PMD, testpmd validated |
| 2026-03-22 | VPP dpdk_plugin.so out-of-tree build chain established |
| 2026-03-25 | Portal mmap SIGSEGV diagnosed and fixed |
| 2026-03-29 | **RC#31 first observed** — testpmd kills management port |
| 2026-04-01 | Plugin deployment to ISO confirmed working |
| 2026-04-02 | Constructor preservation fix (fat archive), sysfs unbind fix |
| 2026-04-03 03:46 | Full VPP+DPAA PMD test on .153 — **RC#31 confirmed fatal** |
| 2026-04-03 16:00 | Decision point: AF_XDP declared production path |
| 2026-04-03 17:40 | Patch-010 rewritten from DPDK to AF_XDP |

## Appendix B: Files to Archive (If Option "Archive" Chosen)

```
# Move to archive/dpaa-pmd/
data/kernel-patches/fsl_usdpaa_mainline.c
data/kernel-patches/9001-usdpaa-bman-qman-exports-and-driver.patch
data/kernel-config/ls1046a-usdpaa.config
data/dpdk-portal-mmap.patch
data/strlcpy-shim.c
data/cmake/CMakeLists.txt
data/hooks/97-dpaa-dpdk-plugin.chroot
bin/ci-build-dpdk-plugin.sh

# Already in archive/
archive/plans/fsl_usdpaa.c
archive/plans/fsl_usdpaa.h

# Keep in plans/ as reference documentation
plans/DPAA1-DPDK-PMD.md
plans/MAINLINE-PATCH-SPEC.md
plans/USDPAA-IOCTL-SPEC.md
plans/FMD-SHIM-SPEC.md
```

## Appendix C: CI Pipeline Changes (If Archived)

1. Remove `ci-build-dpdk-plugin.sh` call from `auto-build.yml`
2. Remove `97-dpaa-dpdk-plugin.chroot` from hooks
3. Remove `ls1046a-usdpaa.config` from kernel config fragments
4. Re-enable in `ls1046a-board.config`:
   ```
   CONFIG_STRICT_DEVMEM=y
   CONFIG_IO_STRICT_DEVMEM=y
   ```
5. Remove `binutils` force-install (only needed for DPAA plugin diagnostics)
6. Upstream `vpp-plugin-dpdk` deb's dpdk_plugin.so remains (PCI driver support for non-DPAA platforms)
