# VPP DPAA1 Acceleration Specification for NXP LS1046A (ASK2 Architecture)

> **Status: DRAFT v0.5 — design intent, partially aspirational (2026-07-19).**
> The shipped VPP dataplane is `set vpp settings interface ethX` over unmodified
> upstream `af_xdp` (per `plans/VPP.md` + AGENTS.md S5) — Sections 1–3 describe
> that reality. **Sections 4, 6.1 (`vpp-options`), and 7 describe the
> `ask_cp.so` control-plane plugin, which is NOT YET IMPLEMENTED** (no source
> exists; it is the "v3 hybrid flow-level mode" of `plans/DUAL-DATAPLANE.md`).
> Where this document's CLI schema or state machine conflicts with the settled
> contracts, the settled contracts win: the per-interface offload CLI is
> `set interfaces ethernet eth<n> offload ask`; the silicon state machine is
> `plans/DUAL-DATAPLANE.md` S0↔S1↔S2 (per-interface). Kernel-side AF_XDP design
> authority: `specs/dpaa1-afxdp-modernization-spec.md`.

This document defines the architecture, design boundaries, and integration interfaces for running the Vector Packet Processing (VPP) engine on NXP QorIQ LS1046A (DPAA1) hardware within VyOS. This specification supersedes all legacy out-of-tree userspace SDK (USDPAA) and proprietary DPDK Poll Mode Driver (PMD) approaches in favor of the modernized **ASK2 (`ask.ko`) dual-dataplane architecture**.

---

## 1. Executive Summary & Architectural Paradigm Shift

Legacy DPAA1 acceleration models rely on userspace drivers (such as `dpaa_bus` or custom VPP C-plugins) that seize exclusive ownership of NXP Queue Manager (QMan) and Buffer Manager (BMan) hardware portals. In a routing OS like VyOS, this legacy model introduces severe architectural failures:
* **Global Hardware Lockout:** Binding hardware portals to userspace detaches network interfaces from the Linux kernel, preventing seamless sharing of 1G RJ45 management interfaces (`eth0`–`eth2`) with mainline routing daemons (FRRouting).
* **Memory Pool Contention:** Recreating buffer management inside VPP fragments system RAM and competes directly with Linux page allocators.
* **Continuous ABI Drift:** Out-of-tree VPP device plugins break across upstream VPP releases due to changes in vector polling loops and `vnet_buffer_opaque` structures.

The **ASK2 architecture** resolves these issues by establishing a clean separation of concerns:
1. **The Linux Kernel** retains ownership of the DPAA1 hardware portals via a modernized network device driver with native `AF_XDP` (XDP Socket) zero-copy support.
2. **The `ask.ko` Kernel Module** acts as the direct hardware orchestrator for NXP Frame Manager (FMan) ASICs, Coarse Classification (CC) tables, and CAAM crypto job rings.
3. **VPP** operates as a high-speed userspace dataplane consumer via its **unmodified upstream `af_xdp` plugin**, supplemented by a lightweight control-plane bridge plugin (`ask_cp.so`).

---

## 2. High-Level Architecture

```
+-----------------------------------------------------------------+
|                       VyOS Control Plane                        |
|        (CLI Shell / Python Commit Daemon / vpp_papi)            |
+-----------------------------------------------------------------+
          |                                       |
          | (Binary API / vpp_papi)               | (Netlink IPC)
          v                                       v
+-----------------------------------+   +-------------------------+
|     VPP Userspace Engine          |   |  Linux Kernel Space     |
|                                   |   |                         |
|  +-----------------------------+  |   |  +-------------------+  |
|  | Upstream af_xdp Plugin      |  |   |  | ask.ko Accelerator|  |
|  | (Zero-Copy Packet I/O)      |  |   |  | (FMan/QBMan/CC)   |  |
|  +-----------------------------+  |   |  +-------------------+  |
|                 ^                 |   |            ^            |
|                 | (XSK / UMEM)    |   |            | (HW Offload|
|  +-----------------------------+  |   |  +-------------------+  |
|  | ask_cp.so Control Bridge    |=====>|  | Modernized DPAA1  |  |
|  | (Metadata & Flow Steering)  |  |   |  | Netdev Driver     |  |
|  +-----------------------------+  |   |  +-------------------+  |
+-----------------------------------+   +------------|------------+
                                                     |
=====================================================v=============
           NXP LS1046A Silicon (FMan / QBMan / BMan / CAAM)
```

---

## 3. Dataplane I/O: Zero-Copy AF_XDP

VPP does not execute proprietary polling loops against NXP hardware portals. All high-speed packet I/O is handled through VPP's native `af_xdp` device driver plugin communicating with the modernized DPAA1 kernel network driver.

### 3.1 UMEM to BMan Pool Mapping
* On high-speed 10GbE SFP+ interfaces (`eth3`/`eth4`), VPP initializes an AF_XDP socket (`XSK`) and registers a User Memory (`UMEM`) area.
* The modernized DPAA1 kernel driver maps VPP's UMEM memory frames directly into hardware BMan buffer pools.
* When FMan receives a frame from the physical PHY, it DMAs the packet payload directly into a UMEM frame and pushes the Frame Descriptor to a QMan channel polled by the XSK NAPI loop.
* VPP worker threads consume these buffers via zero-copy (ZC) RX queues, eliminating software data copies entirely.

### 3.2 Line-Rate Performance Targets
By avoiding buffer copies and userspace QMan locking, the four Cortex-A72 cores on the LS1046A achieve line-rate forwarding on 10GbE interfaces (~2.47 Mpps per port for 64-byte frames) using standard VPP vector sizes (up to 256 packets per cycle).

---

## 4. The `ask_cp.so` Control-Plane Bridge Plugin

To bridge VPP's software forwarding graph with the hardware capabilities of the NXP silicon, VPP loads `ask_cp.so`—a specialized out-of-tree control-plane and metadata plugin. This plugin does not perform packet I/O; instead, it optimizes the graph and manages silicon offloading via Netlink IPC to `ask.ko`.

### 4.1 Hardware Parse Result (PR) Consumption
The NXP FMan hardware parser evaluates L2, L3, and L4 headers at line rate before packet delivery.
* **XDP Metadata Extraction:** The modernized DPAA1 kernel driver prepends FMan's hardware Parse Result (PR) into the XDP metadata area directly ahead of the packet payload.
* **Graph Optimization:** `ask_cp.so` registers a feature arc node immediately after `af-xdp-input`. This node reads the hardware-calculated L3/L4 byte offsets and checksum validation flags from the XDP metadata, instantly populating `vnet_buffer(b)->l2_hdr_offset` and `l3_hdr_offset`.
* **CPU Cycle Savings:** Software parsing nodes (`ethernet-input`, `ip4-input-check`, `ip6-input-check`) are bypassed entirely for valid frames, routing vectors straight to `ip4-lookup` or `ip6-lookup`.

### 4.2 Coarse Classification (CC) Flow Steering
Active, persistent traffic flows (such as established NAT sessions, IPsec tunnels, or BGP-routed elephant flows) are offloaded from VPP software execution into NXP silicon.
* **Flow Threshold Monitoring:** `ask_cp.so` monitors active forwarding tables. When a flow exceeds a configurable packet-per-second threshold, the plugin initiates a hardware offload request.
* **Silicon Execution:** The plugin transmits the 5-tuple matching key and action (e.g., encapsulation, MAC rewrite, egress port) to `ask.ko` via Netlink.
* **Hardware Switching:** `ask.ko` programs FMan's hardware Coarse Classification (CC) tables. Subsequent frames matching this 5-tuple are switched in the FMan ASIC and redirected to the egress physical port without waking VPP worker threads or consuming CPU cycles.

### 4.3 Hardware Policer & QoS Mirroring
To defend VPP worker threads against volumetric DDoS attacks and line-rate bursts, QoS policing is pushed to ingress hardware.
* **QoS API Interception:** `ask_cp.so` intercepts VPP QoS and policer definitions.
* **Hardware Token Buckets:** The plugin translates software rate-limiting rules into FMan hardware token-bucket profiles via `ask.ko`.
* **Silicon Drop & Coloring:** Traffic exceeding configured CIR/PIR thresholds is dropped or DSCP-remarked by the FMan ASIC before consuming BMan buffers or QMan descriptors.

### 4.4 Plugin Binary API (`.api`) Schema
The plugin exposes the following API definitions for external control:

```c
syntax = "default";

import "vnet/interface_types.api";

/** \brief Enable/disable ask_cp hardware acceleration on an interface
    @param client_index - opaque cookie to identify the sender
    @param context - sender context, to match reply w/ request
    @param sw_if_index - target interface
    @param enable_hw_parser - read FMan Parse Results from XDP hints
    @param enable_flow_offload - enable automatic CC silicon offloading
    @param flow_offload_threshold_pps - packet rate to trigger CC offload
*/
autoreply define ask_cp_interface_enable_disable {
    u32 client_index;
    u32 context;
    vl_api_interface_index_t sw_if_index;
    bool enable_hw_parser;
    bool enable_flow_offload;
    u32 flow_offload_threshold_pps;
};

/** \brief Dump active ask_cp silicon offloaded flows */
define ask_cp_flow_dump {
    u32 client_index;
    u32 context;
    vl_api_interface_index_t sw_if_index;
};

define ask_cp_flow_details {
    u32 context;
    u32 src_ip;
    u32 dst_ip;
    u16 src_port;
    u16 dst_port;
    u8 protocol;
    u64 hardware_packets;
    u64 hardware_bytes;
};
```

---

## 5. Runtime State Machine & Reversibility

> **Superseded by `plans/DUAL-DATAPLANE.md` §2.1** — the authoritative silicon
> state machine is S0 (mainline/RSS) ↔ S1 (ASK) ↔ S2 (VPP overlay), **per
> interface** (one port cannot be both ASK and VPP; other ports free). The
> 3-state table below is the original VPP-centric framing, retained for the
> `ask_cp.so` design context; do not implement against it.

| State | VPP Engine | Interface Control | Hardware & Driver State |
|---|---|---|---|
| **0: Dormant** | Stopped / Unloaded | Mainline Linux Kernel | Standard DPAA1 netdev driver active; RSS NAPI distributes frames to Linux network stack. |
| **1: Kernel Offload** | Stopped / Unloaded | Mainline Linux Kernel | `ask.ko` active on the offloaded port(s); FE-VM ehash offloads flows (nftables flowtable hooks). |
| **2: VPP Overlay** | Active (`af_xdp`) | VPP on the assigned port(s), Linux elsewhere | BMan pools mapped to XSK UMEM; `ask_cp.so` (future) manages FMan CC flow steering and XDP hints. |

---

## 6. VyOS Control-Plane Integration

The integration into VyOS abstracts all low-level VPP and Netlink operations behind standard VyOS configuration nodes.

### 6.1 CLI Configuration Schema

> **ASPIRATIONAL — the `vpp-options` subtree below does not exist.** Settled
> shipped CLIs: VPP assignment is `set vpp settings interface ethX`
> (`plans/VPP.md`); ASK offload is `set interfaces ethernet eth<n> offload ask`
> (per-interface, `plans/DUAL-DATAPLANE.md` §3). The `vpp-options` schema is the
> design intent for the future `ask_cp.so` plugin and is shown for that
> implementation's reference only:

```
# FUTURE (ask_cp.so, not implemented):
set interfaces ethernet eth3 vpp-options offload-mode 'ask-xdp'
set interfaces ethernet eth3 vpp-options hw-parser-metadata 'enable'
set interfaces ethernet eth3 vpp-options flow-steering 'enable'
set interfaces ethernet eth3 vpp-options flow-steering-threshold '15000'
set interfaces ethernet eth3 vpp-options rx-queues '2'
```

### 6.2 Commit Orchestration (`vpp_papi` Backend)
During a VyOS configuration `commit`, the configuration backend executes a structured sequence:
1. **Validation:** Verifies that the target interface is a supported 10G SFP+ port (`eth3` or `eth4`) and that `ask.ko` is loaded in the kernel.
2. **AF_XDP Binding:** Connects to VPP via `vpp_papi` and calls `af_xdp_create` to attach the target netdev with zero-copy mode explicitly requested (`mode 2`).
3. **Bridge Activation:** Invokes `ask_cp_interface_enable_disable` via `vpp_papi` to activate hardware parser metadata extraction and CC flow steering thresholds.
4. **Interface Up:** Brings the interface administratively up within the VPP forwarding graph (`sw_interface_set_flags`).

---

## 7. Observability & Debugging

The plugin must register interactive commands with the VPP debug console (`vppctl`) to provide deep visibility into hardware-software boundaries.

### 7.1 Interface & Metadata Inspection
Verifies that UMEM zero-copy mapping is active and shows the ratio of packets successfully using FMan Parse Results versus falling back to software parsing:
```
vppctl show ask-cp interfaces
```
*Expected Output:*
```
Interface eth3 (sw_if_index 1):
  Mode: AF_XDP Zero-Copy (UMEM backed by BMan Pool 4)
  Hardware Parser Hints: ENABLED
    - HW Parsed Packets: 14,892,104 (99.8%)
    - SW Fallback Packets: 29,102 (0.2% - fragmented/unsupported L4)
  Flow Steering: ENABLED (Threshold: 15,000 pps)
```

### 7.2 Silicon Flow Table Dump
Displays all active flows currently offloaded from VPP software execution into the NXP FMan Coarse Classification ASIC:
```
vppctl show ask-cp flows
```
*Expected Output:*
```
Active Silicon Offloaded Flows (FMan CC Table):
[0] 10.100.0.10:443 -> 192.168.1.50:52104 (TCP) | Egress: eth4 | HW Pkts: 8,402,110 | HW Rate: 42,100 pps
[1] 10.100.0.12:5001 -> 192.168.1.51:5001 (UDP)  | Egress: eth4 | HW Pkts: 1,102,400 | HW Rate: 18,400 pps
Total Offloaded Flows: 2 | Total Silicon Switching Rate: 60,500 pps
```

### 7.3 Hardware Policer Telemetry
Displays token-bucket drop statistics collected directly from the FMan hardware policers:
```
vppctl show ask-cp policers
```