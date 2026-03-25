# VPP Setup Guide — Mono Gateway

Enable high-performance packet processing on the 10G SFP+ ports using VPP (Vector Packet Processing) with AF_XDP kernel bypass.

## Overview

By default, all five network interfaces (eth0–eth4) are managed by the Linux kernel. VPP is **off**. When enabled, VPP takes control of the 10G SFP+ ports (eth3, eth4) for high-speed forwarding while the kernel retains the 1G RJ45 ports (eth0–eth2) for management and routing.

| Interface | Without VPP | With VPP |
|-----------|------------|----------|
| eth0 (RJ45 Left) | Kernel | Kernel |
| eth1 (RJ45 Right) | Kernel | Kernel |
| eth2 (RJ45 Center) | Kernel | Kernel |
| eth3 (SFP+ Left) | Kernel (~3–5 Gbps) | **VPP** (~6–7 Gbps) |
| eth4 (SFP+ Right) | Kernel (~3–5 Gbps) | **VPP** (~6–7 Gbps) |

VPP processes packets in batches of 256, bypassing the per-packet `sk_buff` overhead that limits the kernel to ~3–5 Gbps on 10G interfaces.

## Prerequisites

The default configuration already includes the kernel settings VPP needs:

- **Hugepages:** 512 × 2MB (1024 MB) — pre-allocated via `system option kernel memory`
- **SFP+ MTU:** 3290 on eth3/eth4 — DPAA1 XDP maximum (pre-configured)
- **VPP packages:** Pre-installed (v25.10, AF_XDP plugin included)

No additional packages or kernel changes are required.

## Quick Start

Connect to the gateway via SSH or serial console, then:

```vyos
configure

# Assign SFP+ ports to VPP
set vpp settings interface eth3
set vpp settings interface eth4

# Required: allow platform-bus NICs (not PCI)
set vpp settings allow-unsupported-nics

# Thermal-safe polling (MANDATORY — prevents thermal shutdown)
set vpp settings poll-sleep-usec 100

# Resource allocation for ARM64
set vpp settings resource-allocation cpu-cores 1
set vpp settings resource-allocation memory main-heap-size 256M

commit
save
```

VPP starts automatically after commit. Verify with:

```bash
show vpp
```

## What Happens After Enabling VPP

1. **eth3 and eth4 move to VPP** — they disappear from `show interfaces` kernel view
2. **LCP tap mirrors appear** — `lcp-eth3` and `lcp-eth4` provide VyOS visibility into VPP ports
3. **VPP polls SFP+ ports** via AF_XDP sockets in native XDP mode (zero kernel overhead)
4. **Hugepages are consumed** — VPP uses ~416 MB (256M heap + 128M statseg + 32M buffers)

## Verify VPP Is Working

```bash
# VPP service status
show vpp

# Detailed interface status (via vppctl)
sudo vppctl show interface
sudo vppctl show hardware-interfaces

# Performance counters
sudo vppctl show runtime

# Linux Control Plane mirrors
sudo vppctl show lcp
```

Expected output from `show lcp`:
```
itf-pair: [0] vpp-eth3 tap4096 lcp-eth3 16 type tap
itf-pair: [1] vpp-eth4 tap4097 lcp-eth4 17 type tap
```

## Configuration Reference

### Interface Settings

```vyos
# Add an interface to VPP
set vpp settings interface eth3
set vpp settings interface eth4

# Remove an interface from VPP (returns to kernel)
delete vpp settings interface eth4
```

Only eth3 (SFP+ Left) and eth4 (SFP+ Right) should be assigned to VPP. The RJ45 ports (eth0–eth2) must stay with the kernel for VyOS management.

### CPU Allocation

```vyos
# Automatic: VPP picks cores (recommended)
set vpp settings resource-allocation cpu-cores 1

# Manual: pin VPP to specific cores
set vpp settings cpu main-core 0
set vpp settings cpu workers 0
```

The Mono Gateway has 4 Cortex-A72 cores. Recommended allocation:

| Cores for VPP | Cores for VyOS | Throughput | Use Case |
|---|---|---|---|
| 1 (main only) | 3 | ~4–5 Mpps | Default — thermal-safe, good for NAT/routing |
| 2 (main + 1 worker) | 2 | ~6–8 Mpps | High throughput — requires fan cooling |

### Memory Settings

```vyos
# Main heap (minimum 256M for embedded ARM64)
set vpp settings resource-allocation memory main-heap-size 256M

# Hugepages (already in default config — do NOT reduce below 512)
set system option kernel memory hugepage-size 2M hugepage-count 512
```

VPP needs ~416 MB of 2M hugepages:
- 256 MB — main heap
- 128 MB — stats segment
- 32 MB — packet buffers

> **Important:** Use `hugepage-count`, not `hugepage-number`. The wrong keyword silently fails.

### Thermal Protection

```vyos
# MANDATORY on passive-cooled boards
set vpp settings poll-sleep-usec 100
```

Without `poll-sleep-usec`, VPP polls the NIC at maximum rate (2.66M loops/sec), driving the SoC to **91°C** and triggering thermal shutdown within 30 minutes. With `poll-sleep-usec 100`, VPP sleeps 100µs between polls — temperatures stay below 55°C with the fan running.

The fan controller (`fancontrol.service`) starts automatically and manages the EMC2305 PWM fan based on SoC temperature.

## Disabling VPP

```vyos
configure
delete vpp
commit
save
```

After commit, eth3 and eth4 return to kernel control. A reboot is recommended to fully release hugepages.

## Troubleshooting

### VPP won't start — "Not enough free memory"

Hugepages are not allocated. Check:

```bash
grep Huge /proc/meminfo
```

Expected: `HugePages_Total: 512`. If zero, ensure the hugepage config is set and **reboot** (hugepages require a reboot to take effect):

```vyos
configure
set system option kernel memory hugepage-size 2M hugepage-count 512
commit
save
# Reboot required
sudo reboot
```

### VPP starts but interfaces show "down"

The SFP+ module may not be inserted, or the link partner is down. Check:

```bash
sudo vppctl show interface
sudo vppctl show hardware-interfaces
```

If the link says "down" but the SFP+ module is inserted, wait up to 17 minutes for copper SFP-10G-T modules (RTL8261 rollball PHY has a slow negotiation).

### "Configuration error" on boot

The VPP configuration may have invalid parameters. Check the boot log:

```bash
cat /tmp/vyos-config-status
journalctl -u vyos-configd --no-pager | grep -i vpp
```

Common causes:
- `hugepage-count` is too low (need ≥ 512 for 256M heap)
- Missing `allow-unsupported-nics` (required for DPAA1 platform-bus NICs)
- `main-heap-size` below 256M

### XDP dispatcher EACCES warnings in VPP log

```
libbpf: prog 'xdp_dispatcher': failed to load: -13
```

This is **cosmetic** — ignore it. VPP falls back to loading `xsk_def_prog` directly in native XDP mode. No performance impact. The xdp-dispatcher is only needed for multi-program XDP (not used by VPP).

### Temperature warnings or thermal shutdown

VPP poll mode generates significant heat. Ensure:

1. `poll-sleep-usec 100` is set (check: `show vpp`)
2. Fan is running: `systemctl status fancontrol`
3. Check temperature: `cat /sys/class/thermal/thermal_zone3/temp` (divide by 1000 for °C)

If the fan isn't running, start it manually:

```bash
# Find EMC2305 hwmon device
ls /sys/class/hwmon/hwmon*/name | xargs grep emc2305

# Set fan to maximum (emergency)
sudo sh -c 'echo 255 > /sys/class/hwmon/hwmonN/pwm1'  # Replace N
```

## Runtime Monitoring

### VPP Performance

```bash
# Real-time loop rate and packet counters
sudo vppctl show runtime

# Packet trace (capture 100 packets)
sudo vppctl trace add af_xdp-input 100
sudo vppctl show trace

# Error counters
sudo vppctl show errors

# Clear all counters
sudo vppctl clear runtime
```

### Temperature Monitoring

```bash
# SoC temperature (millidegrees — divide by 1000)
cat /sys/class/thermal/thermal_zone3/temp

# Fan RPM
cat /sys/class/hwmon/hwmon*/fan1_input 2>/dev/null

# Fan control status
systemctl status fancontrol
```

## Hardware Constraints

| Constraint | Value | Impact |
|---|---|---|
| SFP+ MTU under XDP | 3290 max | No jumbo frames on VPP ports (RJ45 ports keep 9578 MTU) |
| SFP+ module type | 10G only | 1G SFP modules not supported (hardware limitation) |
| Copper SFP-10G-T link time | Up to 17 min | RTL8261 rollball PHY — normal, not a failure |
| IPsec crypto offload | AES-GCM via CAAM (~2–3 Gbps) | WireGuard (ChaCha20) runs on CPU (~1 Gbps) |
| AF_XDP mode | Copy mode (native XDP) | Zero-copy not available on DPAA1 — ~1.3% overhead at 1500B MTU |

## Further Reading

- [VPP.md](VPP.md) — Technical deep-dive: architecture, patch details, DPAA1 PMD roadmap
- [VyOS VPP Documentation](https://docs.vyos.io/en/latest/vpp/description.html) — Upstream VyOS VPP reference
- [fd.io VPP](https://fd.io/vppproject/) — VPP project home
