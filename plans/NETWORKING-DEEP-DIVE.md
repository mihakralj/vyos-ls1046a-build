# LS1046A Networking Architecture Deep Dive
**Version 1.1.0** · 2026-07-22 · HADS 1.0.0

---

## AI READING INSTRUCTION

Read `[SPEC]` and `[BUG]` blocks for authoritative facts.
Read `[NOTE]` only if additional context is needed.
`[?]` blocks are unverified — treat with lower confidence.

---

## 1. THE SILICON: HARDWARE ACCELERATION & FMAN

**[SPEC]**
- The NXP QorIQ LS1046A combines 4× Cortex-A72 cores (@ up to 1.8 GHz) with the Data Path Acceleration Architecture (DPAA1).
- Key hardware blocks:
  - **FMan (Frame Manager)**: Packet parsing, classification, KeyGen hash distribution, rate policing, and BMI DMA.
  - **BMan (Buffer Manager)**: Hardware buffer pool management (~15 ns atomic acquire/release).
  - **QMan (Queue Manager)**: 64K hardware Frame Queues with priority scheduling and WRED congestion management.
  - **CAAM (Crypto Acceleration)**: IPsec, AES, SHA offload via Job Rings and QI.

### 1.1 SoC Block Diagram

**[SPEC]**
```mermaid
graph TB
    subgraph "LS1046A SoC"
        subgraph "ARM Cores"
            A72["4× Cortex-A72<br/>@ 1.6 GHz"]
        end

        subgraph "DPAA1 Hardware Accelerators"
            FMAN["FMan<br/>(Frame Manager)"]
            BMAN["BMan<br/>(Buffer Manager)"]
            QMAN["QMan<br/>(Queue Manager)"]
            CAAM["CAAM<br/>(Crypto/IPsec)"]
        end

        subgraph "FMan Internals"
            PRS["Parser<br/>HW L2-L4 decode"]
            KG["KeyGen<br/>Hash / RSS"]
            PLCR["Policer<br/>Rate limiting"]
            BMI["BMI<br/>DMA engine"]
            CC["Coarse Classifier<br/>Flow tables"]
            MURAM["MURAM<br/>Shared scratchpad"]
            IRAM["IRAM<br/>Microcode CPU"]
        end

        subgraph "MACs (Physical Ports)"
            MAC2["mEMAC2<br/>eth2 — RJ45 1G"]
            MAC5["mEMAC5<br/>eth0 — RJ45 1G"]
            MAC6["mEMAC6<br/>eth1 — RJ45 1G"]
            MAC9["mEMAC9<br/>eth3 — SFP+ 10G"]
            MAC10["mEMAC10<br/>eth4 — SFP+ 10G"]
        end

        FMAN --> PRS
        PRS --> KG
        KG --> CC
        CC --> PLCR
        PLCR --> BMI
        BMI --> BMAN
        BMI --> QMAN
        QMAN --> A72
        A72 --> QMAN
        QMAN --> BMI
        BMI --> FMAN

        MAC2 & MAC5 & MAC6 & MAC9 & MAC10 --> FMAN
    end
```

### 1.2 Function Execution Matrix

**[SPEC]**

| Function | Execution Location | Operation Details |
|----------|-------------------|-------------------|
| **Ethernet MAC** | mEMAC Silicon | SerDes, PHY interface, CRC, flow control, pause frames. |
| **Frame Parser** | FMan Microcode (IRAM) | Walks L2→L3→L4 headers; generates Parse Result array. |
| **KeyGen (RSS)** | FMan Hardware | Computes hash over Parse Result fields; selects 1 of 128 HW Frame Queues. |
| **Coarse Classifier** | FMan Hardware | TCAM-like lookup against MURAM-stored flow rules. |
| **Policer** | FMan Hardware | Dual-rate token-bucket per-flow rate limiting (srTCM/trTCM). |
| **BMI (DMA)** | FMan Hardware | Transfers frame data between DRAM and FMan FIFO via BMan pools. |
| **BMan** | Dedicated HW Block | Manages 64 hardware buffer pools (~15 ns atomic acquire/release). |
| **QMan** | Dedicated HW Block | 64K Frame Queues with priority scheduling and WRED congestion avoidance. |
| **QMan Portals** | MMIO Registers | Per-CPU cache-inhibited portal regions for low-latency doorbells. |
| **CAAM** | Dedicated Engine | Crypto acceleration (AES, SHA, IPsec ESP/AH) via hardware Job Rings. |

### 1.3 FMan Microcode Variants

**[SPEC]**
- Microcode is loaded from SPI flash (`mtd3` `fman-ucode` @ `0x400000`) and injected into the device tree by U-Boot.
- Open-Source Baseline (`106.4.18`): Performs L2–L4 classification and KeyGen RSS distribution; always enqueues to QMan.
- Proprietary Production Microcode (`v210.10.1`): Adds exact-match Coarse Classifier support and direct FMan port-to-port forwarding (FE-VM / ehash path), short-circuiting QMan and CPU.

---

## 2. KERNEL DATAPLANE & DPAA1 DRIVER

### 2.1 Kernel Frame Path

**[SPEC]**
```mermaid
sequenceDiagram
    participant Wire as "Wire (SFP+ / RJ45)"
    participant FMan as "FMan HW"
    participant BMan as "BMan HW"
    participant QMan as "QMan Portal"
    participant NAPI as "NAPI poll()<br/>(fsl_dpa driver)"
    participant NetStack as "Linux IP Stack"

    Wire->>FMan: Frame arrives
    FMan->>FMan: Parser → KeyGen → hash → select FQ
    FMan->>BMan: Acquire buffer from pool
    FMan->>QMan: Enqueue frame descriptor to RX FQ

    Note over QMan: IRQ or poll wakes CPU
    QMan->>NAPI: Dequeue frame descriptor
    NAPI->>NAPI: Build sk_buff from BMan buffer
    NAPI->>NetStack: netif_receive_skb()
    NetStack->>NetStack: ip_rcv → routing → netfilter → ip_forward
    NetStack->>NAPI: dev_queue_xmit(skb)
    NAPI->>QMan: Enqueue to TX FQ
    QMan->>FMan: TX scheduling → DMA → MAC → Wire
    FMan->>BMan: Release TX buffer back to pool
```

### 2.2 Per-Packet Cost Analysis

**[SPEC]**

| Processing Step | Estimated CPU Overhead | Cache & Memory Impact |
|-----------------|------------------------|-----------------------|
| QMan portal dequeue | ~50 ns | Cache-inhibited MMIO read |
| `sk_buff` allocation & setup | ~80 ns | L1/L2 cache allocation |
| `netif_receive_skb()` / GRO | ~100 ns | Protocol demux & NAPI bookkeeping |
| Netfilter / conntrack | ~200–500 ns | Connection tracking hash walk |
| IP routing lookup (FIB) | ~50–100 ns | Radix tree traversal |
| `ip_forward` + TTL / checksum | ~30 ns | Header modification |
| `dev_queue_xmit()` / qdisc | ~100 ns | Queue discipline processing |
| QMan portal enqueue | ~50 ns | Cache-inhibited MMIO write |
| BMan buffer release | ~15 ns | Hardware command execution |
| **Total Per-Packet** | **~700–1100 ns** | **Heavy L2 cache churn** |

---

## 3. VPP & AF_XDP INTEGRATION

### 3.1 Architecture Overview

**[SPEC]**
- VPP operates as a vector-based userspace forwarding engine processing batches of up to 256 packets.
- Interfaces hand off from kernel `fsl_dpa` to VPP via AF_XDP sockets using shared UMEM rings.
- Linux Control Plane (LCP) TAP interfaces mirror control packets (ARP, BGP, SSH) back to Linux.

```mermaid
graph LR
    subgraph "Hardware Ports"
        SFP3["eth3 SFP+ 10G"]
        SFP4["eth4 SFP+ 10G"]
        RJ0["eth0 RJ45 1G"]
    end

    subgraph "Kernel Space"
        K_DPA3["fsl_dpa (defunct_eth3)"]
        K_DPA4["fsl_dpa (defunct_eth4)"]
        STACK["Linux IP Stack"]
    end

    subgraph "VPP (Userspace)"
        AFXDP3["AF_XDP socket eth3"]
        AFXDP4["AF_XDP socket eth4"]
        GRAPH["VPP Graph Processing"]
        LCP3["LCP TAP eth3"]
        LCP4["LCP TAP eth4"]
    end

    SFP3 --> K_DPA3
    SFP4 --> K_DPA4
    K_DPA3 -.->|XDP redirect| AFXDP3
    K_DPA4 -.->|XDP redirect| AFXDP4
    AFXDP3 & AFXDP4 --> GRAPH --> AFXDP3 & AFXDP4
    GRAPH <-->|punt / inject| LCP3 & LCP4
    LCP3 & LCP4 <--> STACK
```

### 3.2 Performance Metrics on Mono Gateway

**[SPEC]**

| Metric | Measured Parameter | Hardware Value |
|--------|-------------------|----------------|
| **AF_XDP RX/TX** | Functional state | Verified working (0% loss, 0.5ms RTT) |
| **Throughput** | Maximum single-core rate | ~3.5 Gbps |
| **CPU Utilization** | Core allocation | 1 core @ 100% (polling mode) |
| **Memory Footprint** | Reserved pages | ~512 MB hugepages (2M pages) |
| **Thermal Budget** | Steady-state requirement | Requires `poll-sleep-usec 100` to prevent thermal shutdown |
| **Maximum MTU** | XDP hardware cap | 3290 bytes (DPAA1 XDP hardware limit) |

---

## 4. ASK2 HARDWARE OFFLOAD ENGINE

### 4.1 Offload Flow Architecture

**[SPEC]**
```mermaid
sequenceDiagram
    participant Wire as "Wire"
    participant FMan as "FMan HW"
    participant CC as "Coarse Classifier"
    participant QMan as "QMan"
    participant Kernel as "Linux Kernel"
    participant ASK as "ask.ko Engine"

    Note over Wire,ASK: Phase 1: Connection Setup (Slow Path)
    Wire->>FMan: Initial SYN packet
    FMan->>CC: Classify → MISS
    CC->>QMan: Enqueue to default RX FQ
    QMan->>Kernel: Kernel processes & establishes conntrack

    Note over Wire,ASK: Phase 2: Offload Arming
    ASK->>FMan: Install FE-VM / ehash flow entry & TX bypass

    Note over Wire,ASK: Phase 3: Hardware Offloaded (Fast Path)
    Wire->>FMan: Subsequent data packet
    FMan->>CC: Classify → HIT!
    CC->>FMan: Direct BMI forward to TX port
    FMan->>Wire: Line-rate egress (0 CPU cycles)
```

---

## 5. ARCHITECTURAL COMPARISON MATRIX

**[SPEC]**

| Dimension | Linux Kernel | VPP (AF_XDP) | ASK2 (`ask.ko`) |
|-----------|--------------|--------------|-----------------|
| **Forwarding Engine** | CPU (kernel IP stack) | CPU (VPP graph) | FMan Silicon (CC/FE-VM) |
| **Per-Packet CPU Cost** | ~700–1100 ns | ~250–400 ns | **0 ns** (offloaded) |
| **10G Throughput (1500B)** | ~3.6 Gbps | ~3.5 Gbps | **~9.4 Gbps** (line rate) |
| **10G Throughput (64B)** | ~650 Mbps | ~1.5 Gbps | **~9.4 Gbps** (line rate) |
| **Dedicated CPU Cores** | 0 (interrupts) | 1 (poll-mode) | **0** |
| **Thermal Overhead** | Low | High (requires sleep tuning) | **Low** (same as idle) |
| **Memory Footprint** | ~0 extra | ~512 MB hugepages | ~20 MB |
| **VyOS CLI Status** | Native | `set vpp` | `set interfaces ethX offload ask` |