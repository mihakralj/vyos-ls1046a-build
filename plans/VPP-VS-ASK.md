# VPP vs ASK: Two Ways to Stop Drowning Packets on a $400 ARM64 Box

Picture Linux networking as a factory assembly line. Every packet is a widget. One worker picks it up, inspects it, stamps it, routes it to the next station, stamps it again, routes it again. The worker's name is `sk_buff`. He is thorough. He is slow. At 3 to 5 Gbps the line backs up, the foreman (SoftIRQ) starts screaming, and all four A72 cores are sweating through their heatsinks doing nothing but context-switching. This is the "Interrupt Storm" ceiling and it is as fun as it sounds.

The LS1046A Mono Gateway has 10G SFP+ ports. The kernel can see them. The kernel cannot keep up with them. Something has to give.

Two architecturally opposite solutions exist: **VPP** (Vector Packet Processing) and **ASK** (NXP Application Solutions Kit). Both push packets faster. They could not be more different about *who does the pushing*.

---

## VPP: Smarter Software Doing the Same Job Faster

VPP's pitch is batching. Instead of one packet triggering one interrupt triggering one `sk_buff` traversing one full kernel stack, VPP collects up to 256 packets into a "vector," walks them through a processing graph *together*, and stays in L1/L2 cache the whole time. Cache stays warm, branch predictor stays happy, CPUs stop thrashing.

The other trick: AF_XDP sockets. VPP builds an AF_XDP socket directly on top of the kernel's `fsl_dpa` netdev driver. The kernel remains bound. No unbinding, no DPDK bus takeover. VPP just reaches past the network stack via a ring buffer and grabs packets before netfilter can sneeze on them. Measured throughput at 1500B MTU: ~3.5 Gbps on the Mono Gateway's A72 cores.

The catch: VPP is poll-mode. One core spins in a tight loop, continuously asking the NIC "got anything? got anything? got anything?" at roughly 10 million queries per second. This is great for latency. This is terrible for thermals. Without `poll-sleep-usec 100` and an active fan, the SoC climbs to 87 degrees C within 30 minutes. The thermal shutdown trips at 95 degrees C. The hardware was designed for a telco closet with airflow. It was not designed to impersonate a space heater.

VyOS integrates VPP natively via `set vpp settings`. Patch `vyos-1x-010` lowered the minimum core count from 4 to 2 and heap minimum from 1GB to 256MB. The default config pre-allocates 512x2MB hugepages (1,024MB total: 256M heap + 128M statseg + 32M buffers). The Linux Control Plane (LCP) plugin mirrors eth3/eth4 as tap devices so VyOS can still see and manage VPP-controlled ports. Management on eth0 through eth2 never leaves kernel space. Two data planes, one box, zero drama.

---

## ASK: Let the Hardware Do What Hardware Was Built For

ASK takes a fundamentally different position: the FMan (Frame Manager) silicon already contains a hardware classifier engine. That engine can examine a packet's 5-tuple, look up an established flow in a hardware table, and forward it directly from ingress wire to egress wire. Zero software involvement. Zero CPU cycles. Zero thermals.

The architecture is a two-lane highway:

```
Wire --> FMan classifier --+-- Match (established flow): wire --> wire, ~0ns CPU
                           |
                           +-- No match (new connection): --> kernel --> Linux conntrack
```

CMM, a userspace daemon, watches `nf_conntrack`. When Linux establishes a connection, CMM tells CDX (the kernel module) to program FMan's hardware tables to forward that flow in silicon. Every subsequent packet in that flow never sees a CPU. NAT? Programmed into hardware via conntrack offload. IPsec? CAAM crypto engine + FMan integration. QoS? CEETM hardware queuing. PPPoE? Built in. The features VPP handles in software poll-loops, ASK handles in silicon at wire speed.

The critical architectural difference from DPDK (and why ASK sidesteps RC#31 entirely): ASK works *with* `fsl_dpa`, not instead of it. The kernel retains full ownership of every BMan buffer pool and QMan frame queue. CDX just teaches FMan's classifier which flows to shortcut. There is no global bus initialization, no overwriting of kernel-managed state, no management interface suicide. Every eth0 through eth4 stays as a normal kernel netdev. VyOS routing, firewall, SSH, BGP: everything works as if ASK doesn't exist, until a flow hits the hardware fast path.

The catch here is proprietary FMan microcode v210.10.1. Without the ASK-enabled variant already in the Mono Gateway's SPI flash, CDX cannot initialize. This is not a software problem. It is a "call NXP" problem. The entire ASK path blocks on one binary blob.

---

## The Minimum Requirements Problem (Or: Mono's Production Arithmetic)

Mono wants to ship with 2GB RAM and a 2-core CPU. Let's do the math without flinching.

**VPP on 2 cores needs:**

| Component | RAM Required | Notes |
|---|---|---|
| VPP Hugepages | 1,024 MB | 512x2MB mandatory |
| DPAA1 Hardware | 512 MB | BMan/QMan/FMan reserved-memory in DTB |
| VyOS Control Plane | 1,500 MB | Debian, FRR, SSH, CLI, SNMP |
| **Total** | **~3,036 MB** | Against a 2,048MB budget |

That's 988MB short. The arithmetic does not apologize.

Cores are similarly tight. VPP needs 1 core reserved for its main thread (`cpu-cores 1`). VyOS needs its other core for the control plane, FRR, SSH, configd, and everything else a router does. There is no headroom for VPP worker threads. Without workers, forwarding stays on the main core at ~4 to 5 Mpps: respectable, but nowhere near the 10G SFP+ ports' potential. The performance table in [VPP.md](VPP.md) is honest: 1 worker on a dedicated core projects to 6 to 8 Mpps. Zero workers means leaving half that on the table.

And the thermal reality: poll-mode on a 2-core chip means VPP's polling thread competes with VyOS for both cores. `poll-sleep-usec 100` is non-negotiable, and a fan is not optional equipment: it is a prerequisite for surviving a production forwarding load longer than a coffee break.

**ASK on 2 cores:**

| Component | RAM Required | Notes |
|---|---|---|
| DPAA1 Hardware | 512 MB | Identical: hardware reserved memory |
| VyOS Control Plane | 1,500 MB | Identical: same Debian base |
| CDX + CMM | ~50 MB | Kernel module + userspace daemon |
| **Total** | **~2,062 MB** | Marginally fits. Barely. With sweaty palms. |

ASK fits the 2GB budget where VPP cannot, because ASK doesn't need hugepages. The forwarding path is hardware. CPUs can sit nearly idle while the silicon handles 10G at wire speed. A 2-core box running ASK barely breathes during a 9.4 Gbps forwarding load. A 2-core box running VPP approaches thermal throttling during the same test.

---

## The Honest Scoreboard

| Dimension | VPP (AF_XDP) | ASK/CDX |
|---|---|---|
| Throughput | ~3.5 Gbps | ~9.4 Gbps |
| CPU usage at line rate | 1 core pinned 100% | Near zero |
| 2GB RAM viability | No (3GB needed) | Barely yes |
| 2-core viability | Constrained, no workers | Yes, comfortably |
| Thermal concern | Real, fan mandatory | None |
| VyOS CLI integration | Native (`set vpp`) | Zero (needs custom work) |
| First-packet latency | Immediate | Slow (kernel handles first packet) |
| Short-lived flows (DNS) | Full speed | No benefit (never offloaded) |
| IPsec offload | Software only | CAAM + FMan hardware |
| Hard blocker | None (working today) | Proprietary FMan microcode |
| Status on Mono Gateway | Working in production | Analysis only |

VPP is the pragmatist's choice: it works right now, it integrates cleanly, and 3.5 Gbps is still roughly 3.5x what a stock Linux kernel squeezes through these SFP+ ports. ASK is the engineer's dream: hardware-accelerated forwarding with zero CPU cost, the right architecture for constrained production hardware, blocked by a proprietary microcode blob sitting somewhere in NXP's supply chain.

The Mono Gateway production unit's real question is not "VPP or ASK." It's whether that FMan microcode is already in SPI flash. One `dmesg | grep -i "fman.*microcode"` on a live board decides the entire production architecture. The silicon is ready. The kernel drivers are ready. The software is written. The bottleneck is a binary nobody wrote themselves and everybody needs.

Architecture is trade-off. VPP says: give me CPU, I'll give you speed. ASK says: give me microcode, I'll give you speed for free. On 2GB/2-core hardware, "for free" is not luxury. It's survival.

---

## See Also

- [VPP.md](VPP.md): Full VPP technical documentation and implementation roadmap
- [VPP-SETUP.md](VPP-SETUP.md): Step-by-step VPP configuration guide
- [plans/ASK-ANALYSIS.md](plans/ASK-ANALYSIS.md): Detailed ASK/CDX technical analysis
- [plans/VPP-DPAA-PMD-VS-AFXDP.md](plans/VPP-DPAA-PMD-VS-AFXDP.md): DPAA1 PMD vs AF_XDP assessment
