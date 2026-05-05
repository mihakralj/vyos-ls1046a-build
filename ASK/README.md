# NXP ASK (Application Solutions Kit) for LS1046A

Hardware-accelerated packet processing for NXP Layerscape LS1046A (and LS1043A) processors. This repository contains the kernel modules, userspace daemons, build patches, and configuration files needed to enable DPAA fast-path offloading.

## Overview

Together with the ASK-enabled FMAN microcode (a proprietary NXP binary, not included), this repository provides everything needed to turn any Linux-based distribution or routing system -- Debian, OpenWrt, VyOS, or any custom build -- into a hardware-offloading-capable router on LS1046A/LS1043A platforms.

## Prerequisites

Hardware offloading requires the **ASK-enabled FMAN microcode** (v210.10.1) to be loaded by U-Boot before Linux boots. This is a proprietary NXP binary not included in this repository. Without it, the standard FMAN driver operates normally but CDX will not initialize and hardware offloading of ethernet packets will not be available.

## Components

### Kernel Modules

| Module | Description |
|--------|-------------|
| **cdx** | Control Data Exchange -- core fast-path offload engine. Manages hardware flow tables, IPsec offload, and QoS via DPAA/FMAN. Calls `dpa_app` at load time to configure FMAN classification rules. |
| **fci** | Fast-path Control Interface -- communication channel between the CDX kernel module and the CMM userspace daemon. Depends on CDX. |
| **auto_bridge** | Automatic L2 bridge flow detection. Monitors bridge ports and notifies CDX of flows eligible for hardware offload. Supports VLAN filtering. |

### Userspace

| Component | Description |
|-----------|-------------|
| **dpa_app** | Data Path Acceleration application. Reads XML policy files (`cdx_cfg.xml`, `cdx_pcd.xml`) and programs FMAN packet classification rules into hardware via the FMC library. Called automatically by CDX at module load. |
| **cmm** | Connection Manager Module daemon. Monitors Linux netfilter connection tracking and offloads eligible flows to the CDX fast path. Manages L3/L4 forwarding, IPsec, PPPoE, and bridge offload state. |
| **fci/lib** (libfci) | Userspace library for communicating with the FCI kernel module. Used by CMM. |

### Patches

Patches for the Linux kernel and several userspace libraries to add ASK support:

| Target | Patch | Purpose |
|--------|-------|---------|
| **kernel** | `002-mono-gateway-ask-kernel_linux_6_12.patch` | DPAA/FMAN driver enhancements, IPsec hardware offload (IPv4/IPv6), netfilter QoS marking (QOSMARK/QOSCONNMARK), bridge fast-path, QBMan NAPI, 4RD/EtherIP tunnel support |
| **fmlib** | `01-mono-ask-extensions.patch` | Timestamp, hash table, IP reassembly, and shared scheme support for the NXP Frame Manager library |
| **fmc** | `01-mono-ask-extensions.patch` | Port ID output, shared scheme/CC node replication, PPPoE field fix, libxml2 2.13+ compatibility |
| **libnetfilter-conntrack** | `01-nxp-ask-comcerto-fp-extensions.patch` | Fast-path info attributes and QoS connmark support for CMM |
| **libnfnetlink** | `01-nxp-ask-nonblocking-heap-buffer.patch` | Non-blocking socket mode and heap buffer management for CMM |
| **iptables** | `001-qosmark-extensions.patch` | QOSMARK/QOSCONNMARK target and match extensions |
| **iproute2** | `01-nxp-ask-etherip-4rd.patch` | EtherIP and 4RD (4over6 Rapid Deployment) tunnel support |
| **ppp** | `01-nxp-ask-ifindex.patch` | Tunnel interface index support for PPP offload |
| **rp-pppoe** | `01-nxp-ask-cmm-relay.patch` | CMM-aware PPPoE relay |

### Configuration

| File | Description |
|------|-------------|
| `config/ask-modules.conf` | Kernel module load order for systemd (`cdx`, `auto_bridge`, `nf_conntrack`, `nf_conntrack_netlink`, `xt_conntrack`, `fci`) |
| `config/cmm.service` | Systemd service unit for CMM. Guarded by `ConditionPathExists=/dev/cdx_ctrl` -- will not start without ASK FMAN microcode. |
| `config/fastforward` | CMM traffic exclusion rules. Protocols requiring ALG (FTP, SIP, PPTP) bypass the fast path. |
| `config/gateway-dk/cdx_cfg.xml` | FMAN port-to-policy mapping for the Mono Gateway Development Kit (3x 1GbE, 2x 10GbE, 2x offline ports for IPsec/WiFi offload) |
| `config/kernel/defconfig` | Minimal kernel defconfig for LS1046A with ASK support |
| `dpa_app/files/etc/cdx_pcd.xml` | Packet Classification Database -- hash-based flow classification rules for UDP/TCP/ESP/multicast/PPPoE traffic |

## How It Works

1. U-Boot loads ASK-enabled FMAN microcode (v210.10.1) from NOR flash
2. Linux boots with the ASK kernel patch applied
3. `systemd-modules-load` loads CDX, which calls `dpa_app` to program FMAN classification rules
4. FCI and auto_bridge modules load after CDX
5. CMM daemon starts, monitors `nf_conntrack` for new connections
6. When a connection is established and eligible, CMM offloads it to CDX
7. Subsequent packets for that flow are processed entirely in FMAN hardware, bypassing the Linux network stack

## External Dependencies

Built from separate repositories (not included here):

| Component | Repository | Purpose |
|-----------|-----------|---------|
| **fmlib** | [nxp-qoriq/fmlib](https://github.com/nxp-qoriq/fmlib) | Frame Manager userspace library (static) |
| **fmc** | [nxp-qoriq/fmc](https://github.com/nxp-qoriq/fmc) | Frame Manager Configuration tool and library |
| **libcli** | [dparrish/libcli](https://github.com/dparrish/libcli) | Cisco-style CLI library used by CMM and dpa_app |

## License

Kernel modules and ASK components are licensed under GPL-2.0+. See individual `COPYING.GPL` files in each component directory.
