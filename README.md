[![VyOS LS1046A build](https://github.com/mihakralj/vyos-ls1046a-build/actions/workflows/self-hosted-build.yml/badge.svg)](https://github.com/mihakralj/vyos-ls1046a-build/actions/workflows/self-hosted-build.yml)

# VyOS for Mono Gateway Development Kit (NXP LS1046A)

This repo builds VyOS for the aarch64 [Mono Gateway Development Kit](https://docs.mono.si/gateway-development-kit/hardware-description) based on latest [VyOS 1.5.x. 'rolling' release](https://vyos.net/get/nightly-builds/). New [builds](https://github.com/mihakralj/vyos-ls1046a-build/releases) are released each Friday 01:00 UTC.

**This is the first and only VyOS build for bare-metal aarch64 networking hardware targeting support for both ASIC HW-offload *and* VPP.**

**The hardware earns the effort.** The Mono Gateway Development Kit is build around the [NXP LS1046A](https://www.nxp.com/docs/en/data-sheet/LS1046A.pdf) SoC - four Cortex-A72 cores at 1.6 GHz, with a hardware ASIC alongside which chews through packets before the CPU even notices they've arrived. The Mono Gateway Development Kit further adds 8 GB of ECC DDR4, three RJ45 ports, and two SFP+ cages - an ideal package for HW-offloaded, wire-speed networking on aarch64.

**Nothing in this class exists for use as a home router,** and that gap is an opportunity. Historically, NXP sold the LS1046A SoC to telecoms carriers and switch vendors, alongside a generalised [Application Solutions Kit (ASK)](https://www.nxp.com/design/design-center/software/embedded-software/software-for-industrial-networking/gateway-ask:VORTIQA-ASK) to control the HW-offload network accelerator functions. In developing the Gateway Development Kit, Mono purchased and with permission, [released the ASK source](https://github.com/we-are-mono/ASK) under GPL v2.0. 

The LS1046A SoC uses the Data Path Acceleration Architecture (DPAA1), which defines the ASIC functions, and how they interoperate. 

**VyOS enables pushing this hardware to its full potential now that aarch64 is becoming a first-class citizen in `1.5.x`.**  This repo documents the development of DPAA1/ASK HW-offloading rebuilt to modern standards as `ASK2`, and the compliment this can provide to Vector Packet Processing (VPP) on this HW.

## Overview & Getting Started

| **I want to...**                 | **Go to...**                                                                                                        |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| **Use VyOS** on the Mono Gateway | **[INSTALL.md](INSTALL.md)**: Start here                                                                            |
| **Understand the HW**            | [HARDWARE.md](HARDWARE.md): Physical HW, boot-chain and known quirks                                                |
| **Update the Firmware**          | [FIRMWARE.md](FIRMWARE.md): A *'how-to'* guide                                                                      |
| **Control HW & diagnose issues** | [HWCTL.md](HWCTL.md):  Control the main LEDs with `led` & diagnose issues with the seven built-in `*-check` scripts |
| **Understand HW-Offloading**     | [HW-OFFLOADING.md](HW-OFFLOADING.md): Overview of the DPAA1/ASK network architecture                                |
| **Manage VyOS via a web UI**     | [VYMANAGER.md](VYMANAGER.md): Manage VyOS using the Vymanager SDN controller & web GUI                              |
| **See how this started**         | [STARTING-GATE.md](STARTING-GATE.md): Getting mainline VyOS to work (at all)                                        |
| **See what's changed**           | [plans/CHANGELOG.md](plans/CHANGELOG.md): Per-build changelog                                                       |

> Review the [open issues](https://github.com/mihakralj/vyos-ls1046a-build/issues) before installing. Some limitations are permanent hardware constraints. Better to know before you're three hours into a rack installation.
## Architecture & Design

The design specs and deep-dives behind the build. Start here to understand *how* it works, not just how to run it. Plans and Specs utilise [HADS](https://github.com/catcam/hads) to structure information.

| Document                                                                           | What's inside                                                                                                                                                                                                                   |
| ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [specs/dpaa1-afxdp-modernization-spec.md](specs/dpaa1-afxdp-modernization-spec.md) | **DPAA1 AF_XDP driver modernization** — the flavor-ops abstraction, XSK-backed BMan pools, per-CPU NAPI on dedicated QMan channels, the four FMan HW offloads (CC / HM / Policer / CEETM), and the per-milestone status tracker |
| [plans/NETWORKING-DEEP-DIVE.md](plans/NETWORKING-DEEP-DIVE.md)                     | **DPAA1 networking internals** — FMan architecture, QBMan portal allocation, the three-driver split (`fsl_dpaa_mac` / `fsl_dpa` / `fsl_dpaa_eth`), and how packets flow before the CPU sees them                                |
| [specs/dual-dataplane.md](specs/dual-dataplane.md)                                 | **Single-image dual-dataplane model** — one ISO ships every datapath; the silicon mode state machine (mainline/RSS ↔ ASK offload, with VPP as an AF_XDP overlay), runtime switching, and the reversibility contract             |
| [plans/ASK2-MASTER-PLAN.md](plans/ASK2-MASTER-PLAN.md)                             | **Understand work towards ASK2** — what ASK 1.x did right, what's changing in ASK2, and why                                                                                                                                     |
| [specs/ask2-rewrite-spec.md](specs/ask2-rewrite-spec.md)                           | **ASK2 hardware accelerator** — the modern in-tree rewrite of the FMan/QMan offload engine: `ask.ko`, the PCD subsystem, and independent per-interface IPv4/IPv6 controls (`set interfaces ethernet eth offload ipv4 / ipv6`    |
| [specs/vpp-dpaa1-ls1046a-spec.md](specs/vpp-dpaa1-ls1046a-spec.md)                 | **VPP AF_XDP overlay** — kernel-bypass dataplane on the 10G SFP+ ports, thermal constraints, and the kernel↔VPP coexistence model                                                                                               |
| [plans/PORTING.md](plans/PORTING.md)                                               | **Porting postmortem** — driver archaeology, the boot-flow rework, and what broke (and why) bringing mainline VyOS up on the LS1046A                                                                                            |

## Build and Release Assets

Built on demand via GitHub Actions (`workflow_dispatch` — trigger "VyOS LS1046A build (self-hosted)"; no schedule).

| File                          | Description                                                                                                                                    |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `*-LS1046A-arm64.iso`         | **Hybrid ISO** — boot from USB (`dd if=...iso of=/dev/sdX bs=4M`) for live install, or `add system image <url>` to upgrade an installed system |
| `*-LS1046A-arm64.iso.minisig` | ISO signature ([verify key](data/vyos-ls1046a.minisign.pub))                                                                                   |
| `vyos-packages.tar`           | Built kernel + vyos-1x `.deb` packages                                                                                                         |

## What This Build Actually Delivers

This is, as far as anyone can tell, the only VyOS build targeting bare-metal ARM64 networking hardware with HW offload and VPP. Some highlights from the wreckage:

- **10G SFP+ with VPP kernel bypass.** AF_XDP on eth3/eth4 polls at 2.47M packets/sec. The stock kernel path grinds through ~3-5 Gbps on those same ports. Not a typo.
- **CAAM hardware crypto.** IPsec AES-GCM offload via 3 Job Rings at ~2-3 Gbps encrypted throughput. WireGuard (ChaCha20-Poly1305) cannot use CAAM: it runs on ARM64 NEON SIMD instead, topping out around 1 Gbps. Pick your poison.
- **DPAA1 Frame Manager.** Five-port hardware packet engine handles parsing, core distribution, and buffer management before the CPU sees a single byte. Jumbo frames at 9578 MTU on RJ45. SFP+ ports cap at 3290 MTU under AF_XDP (DPAA1 XDP hard limit on `fsl_dpaa_mac`).
- **PTP hardware timestamping.** Nanosecond precision via `ptp_qoriq` on `/dev/ptp0`.
- **U-Boot direct boot.** `vyos.env` on the ext4 partition selects the active image. No GRUB, no OOM, no overhead. Image upgrades write the file automatically.
- **~80s cold boot to login prompt.** Single boot, no kexec double-reboot. `CONFIG_DEBUG_PREEMPT` suppressed saves ~20s of cosmetic scheduler spam.
- **HW-accelerated AF_XDP datapath.** The DPAA1 driver is modernized into a single shared kernel binary with `ndo_xsk_wakeup`, XSK-backed BMan pools, per-CPU NAPI on dedicated QMan channels, and FMan HW offloads (CC / HM / Policer / CEETM). The earlier DPDK DPAA PMD path was abandoned (RC#31: `dpaa_bus` probe globally disrupts kernel FMan interfaces); AF_XDP is the production kernel+VPP coexistence path. Design and milestone status: [specs/dpaa1-afxdp-modernization-spec.md](specs/dpaa1-afxdp-modernization-spec.md).
- **FMan hardware routing offload (ASK2).** Routed IPv4 **and** IPv6 TCP/UDP unicast flows are steered through the Frame Manager's coarse classifier and FE opcode engine — TTL/hop-limit decrement and L2 rebuild happen in hardware, the CPU is bypassed for established flows, and nftables conntrack stays authoritative. Enable per interface and per family: `set interfaces ethernet eth<n> offload ipv4` / `offload ipv6`. ~7.3 Gbit/s unidirectional and ~12.9 Gbit/s bidirectional per 10G port at near-zero CPU; anything the engine can't represent (ICMP, fragments, NAT/VLAN, unresolved neighbours) falls back to software forwarding automatically. Design: [specs/ask2-rewrite-spec.md](specs/ask2-rewrite-spec.md).

## Why VyOS?

**Zone-based firewall with nftables.** Interfaces join zones; policy applies per zone-pair. `set firewall zone DMZ from LAN firewall name LAN-DMZ-v4` replaces dozens of per-interface rules that multiply combinatorially as you add ports. Anyone who has managed large iptables rule sets knows exactly what that cost feels like.

**VPP userspace dataplane.** VyOS 1.5 ships VPP (Vector Packet Processing): a kernel-bypass data plane that batches 256 packets per poll cycle, reads hardware queues directly, and treats the Frame Manager as a co-processor. Critically, VPP is not all-or-nothing: only interfaces explicitly assigned to VPP use the VPP forwarding path. eth3/eth4 (10G SFP+) go to VPP; eth0-eth2 (RJ45) stay with the kernel for management and routing. VPP is off by default. See [plans/VPP.md](plans/VPP.md). The CAAM crypto engine provides 128 hardware algorithms for IPsec AES-GCM offload at ~2-3 Gbps encrypted.

**Native containerization via Podman.** Containers are first-class config-tree citizens: image, network, environment variables, volumes, ports, memory limits, all defined with `set container name ...` and committed alongside routing and firewall config. Run AdGuard, a monitoring agent, or a DDNS updater directly on the router. Roll it back if you regret it.

**CLI-first, text-based config.** The entire router state lives in `config.boot`: hierarchical, human-readable, diffable. A diff between two VyOS configs reads like plain English. No GUI-only system can match that at 2 AM when something is on fire.

**Transactional atomic commits.** Stage multiple changes, review with `compare`, apply with `commit`. If commit fails, nothing changes. `commit-confirm` provides automatic rollback on a timer. The number of network outages this design has prevented is unquantifiable. Use it.

**Full FRRouting integration.** BGP, OSPF, IS-IS, BFD, MPLS, VXLAN, segment routing, PIM: all in the config tree with proper dependency resolution at commit time. Full stack, no glue scripts, no surprises.

## License

VyOS sources are GPL-2.0. ARM64 builder image from [huihuimoe/vyos-arm64-build](https://github.com/huihuimoe/vyos-arm64-build). Hardware documentation from [mono-gateway-docs](https://github.com/we-are-mono/docs/tree/master).