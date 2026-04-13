# High-Performance VyOS on Mono Gateway: VPP + DPAA1 Acceleration

How to push 10 Gbps through a $400 ARM64 box. Technical documentation for wire-speed packet processing on the NXP LS1046A Mono Gateway Development Kit using VyOS with VPP (Vector Packet Processing) and AF_XDP kernel bypass.

## Current Status: ✅ VyOS Native VPP — WORKING

VPP is available through VyOS's native `set vpp` CLI on the Mono Gateway, providing AF_XDP kernel bypass on the 10G SFP+ ports (eth3, eth4). The kernel retains direct control of the 1G RJ45 management ports (eth0 through eth2). This was achieved by patching VyOS's VPP integration layer (`vyos-1x-010-vpp-platform-bus.patch`) to support DPAA1 platform-bus NICs. One patch, four files, zero vendor forks.

**VPP is off by default.** Users enable it through the VyOS configurator:

```bash
configure
set vpp settings interface eth3
set vpp settings interface eth4
set vpp settings poll-sleep-usec 100
set vpp settings allow-unsupported-nics
set vpp settings resource-allocation cpu-cores 1
set vpp settings resource-allocation memory main-heap-size 256M
commit
save
```

**Verified on device:**
- VPP v25.10.0-48, AF_XDP driver on eth3/eth4
- LCP tap mirrors (lcp-eth3, lcp-eth4) for VyOS management visibility
- `poll-sleep-usec 100` — thermal-safe on passive-cooled board (55°C with fan)
- `dpdk_plugin.so` disabled, `af_xdp_plugin.so` enabled
- Hugepages: 512×2MB (1024MB) pre-allocated in default config

---

## Why VPP Changes Everything

### The Kernel Bottleneck

Standard Linux networking processes packets one at a time. Each packet triggers an interrupt, allocates an `sk_buff`, traverses the networking stack (netfilter, routing, bridging), and exits. On a quad-core A72 at 1.8 GHz, this hits an "Interrupt Storm" ceiling around **3 to 5 Gbps**: the CPUs spend more time context-switching than forwarding.

### The VPP Solution

VPP (Vector Packet Processing) from fd.io bypasses the Linux kernel entirely:

1. **Batch Processing:** Instead of one packet per interrupt, VPP processes "vectors" of up to 256 packets per graph-walk cycle, staying in L1/L2 cache.
2. **Kernel Bypass:** Uses AF_XDP sockets to receive/transmit packets directly from NIC queues. Interrupt overhead drops to near zero.
3. **Linux Control Plane:** LCP plugin creates tap mirror interfaces so VyOS can still see and manage the VPP-controlled ports. The split-brain problem, solved.

[Read more about VPP implementation in VyOS](https://docs.vyos.io/en/latest/vpp/description.html)

### Performance Comparison (Estimated)

| Metric | Standard VyOS (Kernel) | VyOS + VPP (AF_XDP) | VyOS + VPP (DPAA1 PMD) |
|--------|----------------------|---------------------|------------------------|
| IPv4 Routing (1500B MTU) | 3–5 Gbps | **6–7 Gbps** | **9.4+ Gbps** |
| Small Packet (64B) PPS | ~2 Mpps | ~4–5 Mpps | **8–12 Mpps** |
| 10G SFP+ Utilization | Partial (CPU-bound) | ~70% | **Full line rate** |
| IPsec AES-GCM (CAAM) | ~1.2 Gbps | ~2 Gbps | **2.5+ Gbps** (CAAM) |
| CPU during 10G forwarding | 100% SoftIRQ | **<50%** (poll mode) | **<30%** |

### Why This Outperforms Alternatives

| OS / Engine | IPv4 (1500B) | 64B PPS | 10G SFP+ | Why |
|-------------|-------------|---------|----------|-----|
| **OPNsense** (FreeBSD) | ~1.5 Gbps | ~0.8 Mpps | ❌ Chokes | Immature FreeBSD DPAA1 driver, hardening overhead |
| **OpenWrt** (Linux kernel) | ~4.5 Gbps | ~2.0 Mpps | ⚠️ Partial | Good drivers but `sk_buff` per-packet overhead |
| **VyOS + VPP** (AF_XDP) | 6–7 Gbps | 4–5 Mpps | ⚠️ ~70% | Kernel bypass via XDP, vector batching |
| **VyOS + VPP** (DPAA PMD) | 9.4+ Gbps | 10+ Mpps | ✅ Line rate | Full hardware offload, zero-copy |

### Real-World Benchmarks (x86 Reference)

These numbers come from [Andree Toonk's VPP benchmarks](https://toonk.io/kernel-bypass-networking-with-fd-io-and-vpp/) on an x86 server (Packet.com n2.xlarge.x86, 10GbE Intel NICs). They validate our estimates and show how VPP scales with cores:

**Layer 3 Forwarding (no NAT):**

| Scenario | VPP Cores | VPP Throughput | Linux Kernel Cores | Efficiency Ratio |
|----------|-----------|----------------|-------------------|------------------|
| Unidirectional 10k flows | 2 | 14 Mpps (line rate) | — | — |
| Bidirectional 28 Mpps | 3 | 14 Mpps/direction | 26 | **9× fewer cores** |

**NAT (SNAT with connection tracking):**

| Scenario | VPP Cores | VPP Throughput | iptables Cores | Efficiency Ratio |
|----------|-----------|----------------|---------------|------------------|
| 16k sessions | 2 | 3.2 Mpps | 29 | **14.5× fewer cores** |
| Line-rate NAT | 12 | 14 Mpps | — | — |

**Projected LS1046A Performance** (4× A72 @ 1.8 GHz, ~60% per-core IPC vs Xeon):

| Config | Forwarding | NAT | Notes |
|--------|-----------|-----|-------|
| VPP main-core only (current) | ~4–5 Mpps | ~2 Mpps | Thermal-safe, no workers |
| 1 VPP worker (core 1) | ~6–8 Mpps | ~3 Mpps | Requires active fan cooling |
| 2 VPP workers (cores 2–3) | ~8 Mpps | ~3 Mpps | Control plane on cores 0–1 |
| + DPAA1 hardware offload | **10G line rate** | **10G line rate** | Future: FMan pre-classifies, BMan zero-copy |

---

## How We Made VyOS Native VPP Work

### The Original Problem

VyOS rolling includes a VPP dataplane integration with CLI commands (`set vpp`). It was designed for x86 servers with PCI NICs. Three hard blockers prevented it from working on the Mono Gateway:

| Blocker | VyOS's Assumption | Mono Gateway Reality | Our Fix (Patch 010) |
|---------|------------------|---------------------|---------------------|
| **NIC detection** | PCI ID whitelist (ConnectX-5/6, E810, Virtio, ENA) | DPAA1 is SoC memory-mapped — no PCI IDs | Auto-detect `fsl_dpa` driver → AF_XDP mode |
| **DPDK-only driver** | Only DPDK PMDs supported | No NXP DPAA PMD in VyOS binary | Enable `af_xdp_plugin`, disable `dpdk_plugin` |
| **Resource minimums** | 4 CPUs, 1GB heap minimum | 4-core A72 with thermal constraints | Lower to 2 CPUs, 256MB heap |

### The Solution: `vyos-1x-010-vpp-platform-bus.patch`

This patch modifies 4 files in the VyOS `vyos-1x` repository to add platform-bus NIC support:

**1. `src/conf_mode/vpp.py`** — Interface detection
- Added `platform_bus_xdp_drivers` list: `fsl_dpa`, `cpsw`, `am65-cpsw-nuss`, `ravb`, `rswitch`
- When an interface uses a platform-bus driver (no PCI ID), VPP automatically uses AF_XDP instead of DPDK
- Removed SSE4.2 x86-only check that blocked ARM64

**2. `data/templates/vpp/startup.conf.j2`** — Configuration template
- Added `af_xdp { enable }` plugin stanza when XDP interfaces detected
- Made `dpdk { ... }` block conditional — only emitted when DPDK interfaces exist
- Without DPDK interfaces, `dpdk_plugin.so` is disabled entirely

**3. `python/vyos/vpp/config_verify.py`** — Validation
- Lowered `main_heap_size` minimum from 1GB to 256MB (embedded ARM64 has limited hugepages)

**4. `python/vyos/vpp/config_resource_checks/resource_defaults.py`** — Resource limits
- `min_cpus`: 4 → 2 (Mono Gateway has 4 cores but needs headroom for VyOS)
- `reserved_cpu_cores`: 2 → 1 (allows VPP to run on a 2-core minimum)

### VyOS CLI Configuration

All VPP configuration is managed through VyOS's native CLI — no manual `vppctl` or systemd services needed:

```bash
# Enter configuration mode
configure

# Enable VPP on SFP+ interfaces
set vpp settings interface eth3
set vpp settings interface eth4

# Thermal-safe polling (MANDATORY for passive cooling)
set vpp settings poll-sleep-usec 100

# Allow non-PCI NICs
set vpp settings allow-unsupported-nics

# Resource allocation for embedded ARM64
set vpp settings resource-allocation cpu-cores 1
set vpp settings resource-allocation memory main-heap-size 256M

# Hugepages (required — VPP needs ~416MB: 256M heap + 128M statseg + 32M buffers)
set system option kernel memory hugepage-size 2M hugepage-count 512

# SFP+ MTU (DPAA1 XDP maximum is 3290)
set interfaces ethernet eth3 mtu 3290
set interfaces ethernet eth4 mtu 3290

# Commit and save
commit
save
```

### Lessons Learned

| Discovery | Detail |
|-----------|--------|
| **SSH vbash hangs** | Interactive `vbash -c 'source /opt/vyatta/etc/functions/script-template; ...'` via SSH hangs indefinitely. Solution: write a vbash script with `vyatta-cfg-cmd-wrapper` commands, SCP it, execute it |
| **Hugepage syntax** | VyOS uses `hugepage-count` not `hugepage-number` — the wrong keyword silently fails |
| **Configd caching** | After patching Python files on a live system, must restart configd AND clear `__pycache__` directories |
| **Stale vbash sessions** | Multiple competing configure sessions cause locks — `kill -9` all stale vbash processes, restart configd |
| **VPP memory requirement** | VPP needs ~416MB of 2M hugepages minimum (256M heap + 128M statseg + 32M buffers). Default 148×2M (296MB) is NOT enough — error: "Not enough free memory to start VPP!" |
| **XDP dispatcher EACCES** | libxdp's `xdp-dispatcher.o` fails to load (`bpf_xdp_adjust_tail` rejected by BPF verifier). VPP falls back to `xsk_def_prog` which loads successfully in **native XDP mode** (mode 1 = `XDP_ATTACHED_DRV`). This is cosmetic — no performance impact. Zero-copy AF_XDP is unavailable (DPAA1 driver lacks `ndo_xsk_wakeup`), so copy-mode is used (~1.3% overhead at 1500B MTU) |

---

## The Architecture: Kernel vs Userspace Data Planes

```mermaid
flowchart TD
  subgraph VyOS["VyOS Control Plane — CLI · Config · FRR · SSH · SNMP"]
    direction LR
    subgraph KDP["Kernel Data Path<br/>(eth0–eth2, 1G RJ45)"]
      K1["sk_buff<br/>netfilter<br/>per-pkt IRQ<br/>~3–5 Gbps max"]
    end
    subgraph VDP["VPP Data Path<br/>(eth3/SFP1, eth4/SFP2)"]
      V1["Vector batch 256 pkts<br/>AF_XDP · poll · reduced IRQ<br/>LCP tap mirrors<br/>Hugepages buffer pool<br/>~6–7 Gbps"]
    end
  end

  subgraph HW["DPAA1 Hardware Subsystem"]
    PAMU["PAMU<br/>IOMMU"] --> BMAN["BMAN<br/>Buffers"]
    PAMU --> QMAN["QMAN<br/>Queues"]
    BMAN --> FMAN["FMAN<br/>Frame Manager"]
    QMAN --> FMAN
    FMAN --> CAAM["CAAM<br/>Crypto Accelerator"]
  end

  KDP --> HW
  VDP --> HW

  style KDP fill:#48a,stroke:#333,color:#fff
  style VDP fill:#4a9,stroke:#333,color:#fff
  style HW fill:#555,stroke:#333,color:#fff
  style PAMU fill:#666,stroke:#333,color:#fff
  style BMAN fill:#48a,stroke:#333,color:#fff
  style QMAN fill:#48a,stroke:#333,color:#fff
  style FMAN fill:#2a6,stroke:#333,color:#fff
  style CAAM fill:#a84,stroke:#333,color:#fff
```

**Key Design Decision:** Kernel retains the 1G RJ45 management ports (eth0 through eth2). VPP claims the 10G SFP+ ports (eth3, eth4) via AF_XDP for high-speed forwarding. LCP (Linux Control Plane) plugin creates tap mirror interfaces so VyOS can still see these ports. Two data planes, one box. VyOS management stays functional while VPP handles the hot path.

### Port Assignment

| Interface | Owner | Purpose | Speed | MTU |
|-----------|-------|---------|-------|-----|
| eth0 (leftmost RJ45) | **Kernel** | VyOS management | 1G | 9578 (jumbo) |
| eth1 (rightmost RJ45) | **Kernel** | VyOS management | 1G | 9578 (jumbo) |
| eth2 (center RJ45) | **Kernel** | VyOS management | 1G | 9578 (jumbo) |
| eth3 (SFP+ left) | **VPP** (AF_XDP) | High-speed data plane | 10G | 3290 (XDP max) |
| eth4 (SFP+ right) | **VPP** (AF_XDP) | High-speed data plane | 10G | 3290 (XDP max) |
| lcp-eth3 (tap4096) | **LCP mirror** | VyOS visibility into VPP | — | — |
| lcp-eth4 (tap4097) | **LCP mirror** | VyOS visibility into VPP | — | — |

---

## Memory Budget

8 GB DDR4 ECC provides generous headroom:

| Component | RAM | Notes |
|-----------|-----|-------|
| VPP Hugepages | 1,024 MB | 512×2MB pages (heap + statseg + buffers) |
| DPAA1 Hardware | 512 MB | BMan/QMan portals, FMan reserved-memory |
| VyOS Control Plane | 1,500 MB | Debian OS, CLI, FRR, SSH, SNMP |
| **Available / Free** | **~5,000 MB** | Containers, BGP tables, caching |

**Verdict:** Memory is not a bottleneck. Even a full BGP Internet routing table (~2–4 GB) can coexist with the VPP data plane.

---

## Thermal Management & Fan Control

### The Problem

The Mono Gateway DK uses passive cooling (heatsink only) with an optional EMC2305 PWM fan controller. Without active cooling, VPP poll-mode drives the SoC to **87 degrees C** (thermal shutdown at 95). Even with `workers 0` and `poll-sleep-usec 100`, temperatures stay dangerously high without a fan. The SoC was not designed for continuous polling. It was designed for interrupt-driven workloads in a telco closet with airflow.

### The Fix

The DTS defines thermal trip points and cooling-maps for automatic fan control, but the **thermal-cooling framework binding is broken** — the EMC2305 cooling device registers but never gets bound to the thermal zone's cooling-maps. Root cause: phandle resolution issue in the emc2305 driver's interaction with thermal OF.

**Workaround:** The standard Linux `fancontrol` daemon polls temperature and sets PWM directly via sysfs. Installed via chroot hook during ISO build, configured at `/etc/fancontrol`. It works. It's not elegant. It keeps the silicon alive.

### Temperature Impact

| Configuration | Temperature | Risk |
|---|---|---|
| Fan off, VPP workers 0 + poll-sleep-usec 100 | 87°C | ⚠️ Near thermal shutdown |
| Fan PWM=51 (~1700 RPM), VPP workers 0 | 43–55°C | ✅ Safe |
| Fan PWM=255 (~8700 RPM), VPP workers 0 | 43°C | ✅ Safe but loud |
| Fan off, VPP worker thread | 91°C+ | ❌ THERMAL SHUTDOWN in <30 min |

### Fan Control PWM Curve

| SoC Temperature | PWM Value | Fan Speed | Noise |
|---|---|---|---|
| < 45°C | 0 | Off | Silent |
| 45–54°C | 51 | ~1700 RPM | Quiet |
| 55–64°C | 76 | ~3000 RPM | Moderate |
| 65–74°C | 102 | ~8600 RPM | Loud |
| ≥ 75°C | 255 | ~8700 RPM | Full blast |

### Manual Fan Control

```bash
# Find EMC2305 hwmon device
ls /sys/class/hwmon/hwmon*/name | xargs grep emc2305

# Set fan to maximum (emergency cooling)
sudo sh -c 'echo 255 > /sys/class/hwmon/hwmon3/pwm1'

# Set fan to quiet mode
sudo sh -c 'echo 51 > /sys/class/hwmon/hwmon3/pwm1'

# Turn fan off
sudo sh -c 'echo 0 > /sys/class/hwmon/hwmon3/pwm1'

# Check current fan RPM and temperature
cat /sys/class/hwmon/hwmon3/fan1_input
cat /sys/class/thermal/thermal_zone3/temp

# Check fan control service
systemctl status fancontrol.service
journalctl -u fancontrol.service --no-pager -n 10
```

### Hardware Details

- **Controller:** Microchip EMC2305, I2C address 0x2E on PCA9545 mux channel 3
- **Kernel driver:** `CONFIG_SENSORS_EMC2305=y` (hwmon3)
- **Fan headers:** 2× 4-pin PWM (Fan 1 populated, Fan 2 optional)
- **PWM quantization:** EMC2305 rounds to discrete steps (minimum effective: ~51)
- **Tachometer:** Fan 1 reads RPM via tach input; Fan 2 available for second fan

---

## VPP CLI Reference

### VyOS Configuration Commands

```bash
# === VyOS Native VPP Management (preferred) ===

# Show VPP configuration
show vpp

# Enter configure mode
configure

# Add/remove VPP interfaces
set vpp settings interface eth3
set vpp settings interface eth4
delete vpp settings interface eth4

# Set poll sleep (thermal safety)
set vpp settings poll-sleep-usec 100

# Resource allocation
set vpp settings resource-allocation cpu-cores 1
set vpp settings resource-allocation memory main-heap-size 256M

# Hugepages (must match VPP requirements)
set system option kernel memory hugepage-size 2M hugepage-count 512

# Commit and save
commit
save
```

### VPP Runtime Commands (vppctl)

All `vppctl` commands require `sudo`. VPP CLI socket: `/run/vpp/cli.sock`.

```bash
# === Service Status ===
systemctl status vpp.service
systemctl status fancontrol.service

# === Interface Status ===
sudo vppctl show interface               # All VPP interfaces with counters
sudo vppctl show interface vpp-eth3       # Single interface details
sudo vppctl show hardware-interfaces      # Hardware-level info (driver, queues)
sudo vppctl show interface addr           # Interface addresses

# === Linux Control Plane (LCP) ===
sudo vppctl show lcp
# Expected:
#   itf-pair: [0] vpp-eth3 tap4096 lcp-eth3 16 type tap
#   itf-pair: [1] vpp-eth4 tap4097 lcp-eth4 17 type tap

# === Performance & Runtime ===
sudo vppctl show runtime                  # loops/sec, vector rates, per-node timing
sudo vppctl clear runtime                 # Clear counters
sudo vppctl show threads                  # Thread info (should show only vpp_main)
sudo vppctl show buffers                  # Buffer usage

# === Packet Tracing ===
sudo vppctl trace add af_xdp-input 100    # Trace 100 packets
sudo vppctl show trace                    # View packet traces
sudo vppctl show errors                   # Error counters

# === Version & Plugins ===
sudo vppctl show version                  # VPP version
sudo vppctl show plugins                  # Loaded plugins

# === Crypto ===
sudo vppctl show crypto engines           # Available engines
sudo vppctl show crypto handlers          # Algorithm → engine mapping

# === Logs ===
journalctl -u vpp.service --no-pager -n 50
sudo cat /var/log/vpp/vpp.log
```

---

## Implementation Roadmap

### Milestone 1: AF_XDP via VyOS Native CLI ✅ COMPLETE

- [x] Verify VyOS 1.5 VPP package supports AF_XDP on ARM64 — VPP v25.10.0-48, 89 plugins
- [x] Patch VyOS `vpp.py` for platform-bus NIC detection (`vyos-1x-010-vpp-platform-bus.patch`)
- [x] Configure VPP via `set vpp` CLI — AF_XDP on eth3/eth4, poll-sleep-usec 100
- [x] LCP mirror interfaces created (lcp-eth3, lcp-eth4 tap devices) for VyOS visibility
- [x] Validate kernel stays functional for management (eth0–eth2) — split-plane confirmed
- [x] Hugepages pre-allocated in ISO default config (512×2MB = 1024MB)
- [x] VPP off by default — users enable via `set vpp settings` in VyOS configurator
- [x] SFP+ MTU set to 3290 (DPAA1 XDP maximum) in default config
- [ ] Benchmark: target 6–7 Gbps on 10G interfaces — needs iperf3 with SFP+ link partner

**Key discoveries:**
- DPAA1 XDP max MTU = 3290 — `fsl_dpaa_mac` hard limit, max frame ~3304 bytes
- 8 plugins sufficient: af_xdp, linux_cp, linux_nl, ping, acl, nat44_ei, cnat, crypto_sw_scheduler
- VPP requires ~416MB 2M hugepages minimum (256M heap + 128M statseg + 32M buffers)
- `ssh vbash` interactive sessions HANG — must use script + `vyatta-cfg-cmd-wrapper`

### Milestone 2: L2/L3 Routing via VPP ⬜ PLANNED

- [ ] Configure L2 bridge between vpp-eth3 ↔ vpp-eth4
- [ ] Configure L3 routing (IP forwarding between VPP interfaces)
- [ ] Test NAT via VPP (SNAT/DNAT connection tracking)
- [ ] Validate ACL rules on VPP interfaces
- [ ] Benchmark L2/L3 forwarding throughput

### Milestone 3: Worker Thread Optimization ⬜ PLANNED

- [ ] Re-enable VPP worker threads with fan cooling (43°C headroom at PWM=51)
- [ ] Test `cpu-cores 2` with workers on cores 1–2
- [ ] Benchmark improvement: main-only (~4 Mpps) vs 1 worker (~6–8 Mpps)
- [ ] Profile thermal under traffic load with workers enabled
- [x] ~~Investigate XDP dispatcher EACCES~~ — **RESOLVED**: Already running native XDP (mode 1). EACCES is cosmetic (libxdp dispatcher, not needed). Copy-mode AF_XDP with ~1.3% overhead at 1500B MTU

### Milestone 4: Crypto Acceleration ⬜ PLANNED

- [ ] Enable CAAM crypto plugin in VPP
- [ ] Benchmark IPsec AES-GCM throughput with CAAM offload (~2–3 Gbps via 3 Job Rings)
- [ ] Benchmark WireGuard throughput (ChaCha20-Poly1305, CPU-only via NEON SIMD, ~1 Gbps)
- [ ] Target: 2.5+ Gbps IPsec encrypted tunnel throughput

### Milestone 5: DPAA1 PMD (Full Line Rate) ⬛ ARCHIVED

> **Archived 2026-04-03:** DPDK DPAA1 PMD blocked by RC#31 — `dpaa_bus` probe kills all kernel FMan interfaces globally. Infrastructure moved to `archive/dpaa-pmd/`. See [VPP-DPAA-PMD-VS-AFXDP.md](plans/VPP-DPAA-PMD-VS-AFXDP.md) for analysis. Current production path: AF_XDP (~3.5 Gbps on 10G SFP+). **ASK hardware flow offload is being evaluated as the alternative path to line rate** — see [ASK-ANALYSIS.md](plans/ASK-ANALYSIS.md).

- [x] Clean mainline USDPAA kernel module (6 patches, 1453 lines)
- [x] Build DPDK 24.11 with DPAA PMD (cross-compiled, shared libs)
- [x] Create USDPAA DTB variant for Mono Gateway
- [ ] ~~VPP DPDK plugin integration~~ — blocked by RC#31
- [ ] ~~Build FMlib + FMC from NXP LSDK~~ — blocked by RC#31
- [ ] ~~Benchmark: target 9.4+ Gbps~~ — blocked by RC#31

---

## Future: DPAA1 PMD Path (10G Line Rate) — ARCHIVED

> **Note:** This section is preserved for reference. The DPAA1 PMD approach was archived on 2026-04-03 due to RC#31 (see above). All code moved to `archive/dpaa-pmd/` with restoration guide at `archive/dpaa-pmd/RESTORE.md`.

The current AF_XDP approach achieves ~3.5 Gbps on 10G SFP+. The DPAA1 PMD (Poll Mode Driver) path was intended to provide hardware-level packet processing for full 10G wire-speed, but is blocked by a fundamental bus initialization conflict (RC#31).

### The Four Components Required

DPAA1 is not a standard PCI NIC. Getting VPP to talk to it at full speed requires four components beyond AF_XDP:

```mermaid
flowchart TD
  VPP["④ VPP<br/>Vector Packet Processor<br/>nxp-qoriq/vpp"] --> DPDK["③ DPDK + DPAA PMD<br/>Poll Mode Driver<br/>nxp-qoriq/dpdk"]
  DPDK --> FMC["② FMC + FMlib<br/>Frame Manager Configurator<br/>Queue policy &amp; RSS hash"]
  DPDK --> USDPAA["① USDPAA<br/>Kernel Module<br/>/dev/fsl-usdpaa"]
  FMC --> USDPAA
  USDPAA --> HW["DPAA1 Hardware<br/>FMAN · BMAN · QMAN · PAMU"]

  style VPP fill:#4a9,stroke:#333,color:#fff
  style DPDK fill:#48a,stroke:#333,color:#fff
  style FMC fill:#2a6,stroke:#333,color:#fff
  style USDPAA fill:#a84,stroke:#333,color:#fff
  style HW fill:#555,stroke:#333,color:#fff
```

### Component 1: USDPAA Kernel Module

**What:** Userspace DPAA — a kernel module that creates `/dev/fsl-usdpaa` and `/dev/fsl-usdpaa-irq` device nodes allowing VPP to access DPAA1 hardware queues directly from userspace.

**Status:** ✅ **COMPLETE** — Clean mainline rewrite (1453 lines, 6 kernel patches). Running on device as `CONFIG_FSL_USDPAA_MAINLINE=y`. NXP ioctl ABI preserved for DPDK binary compatibility.

**Why it matters:** Without USDPAA, VPP cannot "grab" the Frame Manager's hardware queues. The kernel's `fsl_dpaa_eth` driver must release the 10G interfaces, and USDPAA provides the bridge for VPP to claim them.

**Source:** [`data/kernel-patches/fsl_usdpaa_mainline.c`](data/kernel-patches/fsl_usdpaa_mainline.c) — see [DPAA1-DPDK-PMD.md](plans/DPAA1-DPDK-PMD.md) and [USDPAA-IOCTL-SPEC.md §13](plans/USDPAA-IOCTL-SPEC.md#13-mainline-implementation-status-2026-03-28)

### Component 2: FMC Tool + Policy Files

**What:** Frame Manager Configuration — a userspace tool that programs the DPAA1 hardware's packet distribution policy before VPP starts.

**Artifacts needed:**
- `fmc` binary → `/usr/local/bin/fmc`
- `libfmd.so` → `/usr/local/lib/`
- `config.xml` — defines the 5 MACs (3× SGMII, 2× XFI) for the LS1046A
- `policy.xml` — defines RSS hash and frame distribution across A72 cores

**Source:** `github.com/nxp-qoriq/fmc` branch `lf-6.6.3-1.0.0`, depends on `github.com/nxp-qoriq/fmlib`

### Component 3: NXP DPDK with DPAA PMD

**What:** DPDK compiled with the NXP DPAA1 Poll Mode Driver — the piece that lets VPP poll the hardware queues instead of relying on interrupts.

**Build flags:**
```
CONFIG_RTE_LIBRTE_DPAA_PMD=y
CONFIG_RTE_LIBRTE_DPAA_BUS=y
```

**Source:** `github.com/nxp-qoriq/dpdk` branch `lf-6.6.36-2.1.0`

### Component 4: NXP VPP with DPAA Platform

**What:** VPP recompiled against NXP's DPDK fork with DPAA platform support.

**Build:**
```bash
make V=0 PLATFORM=dpaa TAG=dpaa vpp-package-deb
```

**Source:** `github.com/nxp-qoriq/vpp` branch `lf-6.6.36-2.1.0`

### The Kernel Fork Decision

The current VyOS build uses the upstream VyOS kernel (mainline 6.6). USDPAA was originally only in the NXP kernel fork.

| Option | Approach | Pro | Con | Status |
|--------|----------|-----|-----|--------|
| **A** | Patch VyOS kernel with USDPAA module | Stays on VyOS kernel cadence | Must maintain patch across updates | ✅ **ACTIVE** |
| **B** | Use NXP kernel fork entirely | Full NXP support OOB | Diverges from VyOS upstream | ❌ Rejected |
| **C** | AF_XDP only (current fallback) | Zero kernel changes | ~30% performance penalty | ✅ Working fallback |

**Current: Option A** — Clean mainline USDPAA rewrite (6 patches) running on VyOS kernel 6.6. The NXP fork approach (Option B) was [evaluated and rejected](plans/DPAA1-DPDK-PMD.md#-achievement-status-2026-03-27) for code quality reasons. AF_XDP (Option C) remains as a working fallback.

### Software Dependency Chain

All NXP-specific packages sourced from `github.com/nxp-qoriq`. **Critical:** use kernel branch `lf-6.6.3-1.0.0` (NOT `lf-6.6.36-2.1.0` which has a memory bug).

```mermaid
flowchart TD
  CC["1. Cross-compiler<br/>Linaro GCC 12.3.1"] --> SSL["2. OpenSSL 3.4.0<br/>host + target"]
  SSL --> KRN["3. Kernel<br/>NXP fork lf-6.6.3-1.0.0<br/>USDPAA DTB + modules"]
  SSL --> ACLIB["4. AArch64cryptolib<br/>ARM hardware crypto"]
  KRN --> FMLIB["5. FMlib<br/>Frame Manager library"]
  FMLIB --> FMC["6. FMC<br/>Frame Manager Configurator"]
  KRN --> DPDK["7. DPDK<br/>NXP fork lf-6.6.36-2.1.0<br/>platform=dpaa"]
  ACLIB --> DPDK
  DPDK --> VPP["8. VPP<br/>NXP fork lf-6.6.36-2.1.0<br/>PLATFORM=dpaa"]

  style CC fill:#555,stroke:#333,color:#fff
  style SSL fill:#555,stroke:#333,color:#fff
  style KRN fill:#48a,stroke:#333,color:#fff
  style ACLIB fill:#555,stroke:#333,color:#fff
  style FMLIB fill:#2a6,stroke:#333,color:#fff
  style FMC fill:#2a6,stroke:#333,color:#fff
  style DPDK fill:#4a9,stroke:#333,color:#fff
  style VPP fill:#4a9,stroke:#333,color:#fff
```

### NXP LSDK Resources

| Resource | URL | Notes |
|----------|-----|-------|
| Flexbuild | `github.com/NXP/flexbuild` | Main LSDK build tool |
| NXP Kernel | `github.com/nxp-qoriq/linux` | Branch: `lf-6.6.3-1.0.0` |
| FMlib | `github.com/nxp-qoriq/fmlib` | Branch: `lf-6.6.3-1.0.0` |
| FMC | `github.com/nxp-qoriq/fmc` | Branch: `lf-6.6.3-1.0.0` |
| DPDK | `github.com/nxp-qoriq/dpdk` | Branch: `lf-6.6.36-2.1.0` |
| VPP | `github.com/nxp-qoriq/vpp` | Branch: `lf-6.6.36-2.1.0` |
| AArch64cryptolib | `github.com/ARM-software/AArch64cryptolib` | ARM hardware crypto lib |
| Mono Gateway Docs | `github.com/we-are-mono/meta-mono` | Official hardware docs |
| Mono Gateway DTS | `gist.github.com/tomazzaman/c66ef587...` | SFP GPIO/I2C pinout |
| Mono Discord | `discord.gg/FGHJ3J5v5W` | Community support |

---

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|------------|
| ~~XDP dispatcher EACCES~~ | ~~Reduced AF_XDP performance~~ **RESOLVED** — cosmetic only | VPP already runs native XDP (mode 1). libxdp dispatcher fails but `xsk_def_prog` loads fine. No zero-copy (DPAA1 lacks `ndo_xsk_wakeup`), copy-mode overhead ~1.3% at 1500B |
| VPP thermal shutdown (poll mode) | Hardware protection reboot | `poll-sleep-usec 100` + fancontrol.service (DEPLOYED) |
| DPAA1 MTU limit 3290 under XDP | No jumbo frames on VPP ports | RJ45 ports retain full 9578 MTU; design constraint |
| USDPAA incompatible with VyOS kernel | Blocks full DPAA PMD path | AF_XDP already works as fallback |
| NXP DPDK fork too old for VPP 25.x | Build failures | Pin to NXP's VPP fork version |
| Hugepage exhaustion | VPP fails to start | Default config reserves 512×2MB (1024MB), validated working |
| VyOS upstream changes VPP integration | Patch 010 may conflict | Monitor vyos-1x `vpp.py` for changes, rebase patch |

---

## See Also

- [PORTING.md](PORTING.md) — Kernel-space fixes and driver archaeology
- [INSTALL.md](INSTALL.md) — Installation guide for standard VyOS
- [UBOOT.md](UBOOT.md) — U-Boot reference and memory map
- [README.md](README.md) — Project overview
