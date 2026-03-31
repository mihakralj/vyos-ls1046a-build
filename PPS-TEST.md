# PPS Benchmark Test — LS1046A Mono Gateway

Small-frame packets-per-second (PPS) forwarding test to measure dataplane performance across kernel, VPP, and (future) ASK hardware offload.

## Theory

The classic router benchmark: 64-byte Ethernet frames at line rate maximize PPS stress because per-packet overhead dominates over payload. Every lookup, queue operation, and DMA descriptor costs the same regardless of packet size.

| Link Speed | Wire-rate PPS (64B frames) | Calculation |
|-----------|---------------------------|-------------|
| 1 Gbps   | 1,488,095 pps             | 10⁹ / (84 × 8) |
| 10 Gbps  | 14,880,952 pps            | 10¹⁰ / (84 × 8) |

84 bytes = 64-byte frame + 8-byte preamble + 12-byte IFG (inter-frame gap).

## Hardware Under Test

**LS1046A Mono Gateway Development Kit**
- 4× Cortex-A72 @ 1.8 GHz
- 3× 1G RJ45 (GPY115C PHY, kernel-managed: eth0, eth1, eth2)
- 2× 10G SFP+ (FMan MAC9/MAC10, VPP or kernel: eth3, eth4)
- FMan packet engine with hardware classification/policer
- DPAA1: BMan (buffer manager) + QMan (queue manager) + FMan (frame manager)

## Test Topology

```
┌──────────────────────┐         ┌──────────────────────────────┐
│   Traffic Generator  │         │     DUT (Mono Gateway)       │
│                      │         │                              │
│   Port A (TX) ───────┼── 10G ──┼── eth3 (ingress)            │
│                      │  SFP+   │       │ forwarding           │
│   Port B (RX) ◄──────┼── 10G ──┼── eth4 (egress)             │
│                      │  SFP+   │                              │
│                      │         │   eth0 ── management (SSH)   │
└──────────────────────┘         └──────────────────────────────┘
```

- Direct connections (no switch) for accurate PPS measurement
- Management via eth0 (1G RJ45) — separate from test path
- SFP-10G-T copper or SFP-10G-SR fiber modules

## Test Modes

### Mode 1: Kernel Forwarding (baseline)

Linux kernel IP forwarding between eth3 ↔ eth4. This is the slowest path — every packet traverses the full network stack (netfilter, routing table, conntrack).

**Expected: ~200K–500K pps** (64-byte frames, Cortex-A72 with DPAA1 NAPI)

```bash
# On DUT — enable IP forwarding
configure
set interfaces ethernet eth3 address 10.99.1.1/24
set interfaces ethernet eth4 address 10.99.2.1/24
set system ip disable-forwarding  # delete this if present
commit

# Verify
sysctl net.ipv4.ip_forward  # must be 1
```

### Mode 2: VPP Software Forwarding

VPP with AF_XDP (current) or DPDK DPAA1 PMD (after DPDK plugin build succeeds). VPP's vector packet processing batches up to 256 packets per graph-walk, amortizing per-packet overhead.

**Expected AF_XDP: ~1–3 Mpps** (single worker, poll-sleep-usec 100)
**Expected DPDK PMD: ~3–8 Mpps** (FMan hardware DMA, zero kernel involvement)

```bash
# On DUT — configure VPP
configure
set vpp settings interface eth3
set vpp settings interface eth4
set vpp settings poll-sleep-usec 100    # MANDATORY: thermal protection
set vpp settings cpu-cores 2            # main thread + 1 worker
commit

# VPP bridge domain (L2 forwarding, lowest overhead)
vppctl create bridge-domain 1
vppctl set interface l2 bridge TenGigabitEthernet3 1
vppctl set interface l2 bridge TenGigabitEthernet4 1
vppctl set interface state TenGigabitEthernet3 up
vppctl set interface state TenGigabitEthernet4 up
```

### Mode 3: ASK Hardware Offload (future)

NXP Application Solutions Kit — FMan classifies and forwards packets entirely in silicon. CPU sees zero packets for offloaded flows. Requires proprietary FMan microcode v210.10.1 + CDX kernel module + CMM daemon.

**Expected: ~14.88 Mpps** (wire-rate, zero CPU, hardware limitation is FMan clock)

This mode is not yet implemented. See integration analysis in the repo.

## Running the Test

### Step 1: Set up IP addressing

On DUT (Mono Gateway):
```bash
# Kernel mode
configure
set interfaces ethernet eth3 address 10.99.1.1/24
set interfaces ethernet eth4 address 10.99.2.1/24
commit
```

On Generator:
```bash
# Port A (connects to DUT eth3)
ip addr add 10.99.1.2/24 dev enp1s0
ip link set enp1s0 up

# Port B (connects to DUT eth4) — needs route back
ip addr add 10.99.2.2/24 dev enp2s0
ip link set enp2s0 up

# Route for return traffic (DUT forwards 10.99.1.0/24 → 10.99.2.0/24)
ip route add 10.99.2.0/24 via 10.99.2.1 dev enp2s0
```

### Step 2: Start DUT measurement

```bash
# Copy script to DUT
scp data/scripts/pps-bench.sh vyos@10.99.1.1:/tmp/

# On DUT — kernel mode, 60 seconds, with CPU breakdown
chmod +x /tmp/pps-bench.sh
/tmp/pps-bench.sh -i eth3 -o eth4 -d 60 -c

# On DUT — VPP mode
/tmp/pps-bench.sh -i eth3 -o eth4 -d 60 -v
```

### Step 3: Start traffic generator

```bash
# Copy script to generator machine
# Single thread, 64-byte, 30 seconds, wire rate
sudo ./pps-gen.sh -i enp1s0 -d 10.99.1.1 -m $(get DUT eth3 MAC) -t 60

# 4 threads for higher PPS
sudo ./pps-gen.sh -i enp1s0 -d 10.99.1.1 -m XX:XX:XX:XX:XX:XX -t 60 -n 4
```

### Alternative: iperf3 (lower PPS but simpler)

If kernel pktgen is unavailable:

```bash
# On DUT — start iperf3 server on egress side
iperf3 -s -B 10.99.2.1

# On Generator — UDP blast, 64-byte payloads (18B payload + 46B headers = 64B frame)
iperf3 -c 10.99.1.1 -u -b 0 -l 18 -t 60 --get-server-output
```

iperf3 typically generates ~200K–800K pps from a single core (socket overhead limits it). Use pktgen for accurate line-rate testing.

## Interpreting Results

### Sample output (kernel mode)

```
═══════════════════════════════════════════════════════════════
  PPS Benchmark Results — kernel mode
═══════════════════════════════════════════════════════════════

  Ingress (eth3):
    Average RX:    423.5 Kpps
    Peak RX:       451.2 Kpps
    Line rate:     14.88 Mpps  (peak = 3.0% of line rate)
    Drops:         12847

  Egress (eth4):
    Average TX:    398.1 Kpps
    Peak TX:       425.8 Kpps
    Line rate:     14.88 Mpps  (peak = 2.8% of line rate)
    Drops:         0

  Forwarding ratio:  94.0%  (egress TX / ingress RX)
  Duration:          60s  (60 samples)
```

### Key metrics

| Metric | What it means |
|--------|--------------|
| **Peak RX PPS** | Maximum ingress rate the NIC accepted |
| **Peak TX PPS** | Maximum forwarding rate achieved |
| **Forwarding ratio** | TX/RX — 100% = zero-loss forwarding |
| **Drops** | Packets lost in forwarding path |
| **% of line rate** | How close to theoretical maximum |
| **CPU%** | Total CPU utilization during forwarding |

### Expected performance comparison

| Mode | Expected PPS (64B) | CPU Usage | Notes |
|------|-------------------|-----------|-------|
| Kernel forwarding | 200K–500K | 80–100% (1 core) | netfilter + conntrack overhead |
| Kernel (no conntrack) | 400K–800K | 80–100% (1 core) | `set firewall state-policy disable` |
| VPP AF_XDP | 1–3 Mpps | 100% (1 worker) | poll-sleep-usec reduces to ~50% idle |
| VPP DPDK PMD | 3–8 Mpps | 100% (1 worker) | FMan DMA, no kernel involvement |
| ASK hw offload | ~14.88 Mpps | ~0% | FMan silicon, wire-rate |

## Packet Size Sweep

For a complete characterization, test multiple sizes:

```bash
# On generator — run each size for 30s
for size in 64 128 256 512 1024 1518; do
  echo "=== Testing ${size}B frames ==="
  sudo ./pps-gen.sh -i enp1s0 -d 10.99.1.1 -m XX:XX:XX:XX:XX:XX -t 30 -p $size
  sleep 5
done

# On DUT — single long measurement
/tmp/pps-bench.sh -i eth3 -o eth4 -d 240
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| 0 pps on ingress | Link down or wrong MAC | Check `ethtool eth3`, verify SFP modules |
| RX ok but TX = 0 | No forwarding route | Check `ip route`, `sysctl ip_forward` |
| Very low PPS (~10K) | Conntrack/firewall overhead | Disable stateful firewall for benchmark |
| Drops on ingress | Ring buffer overflow | `ethtool -G eth3 rx 4096` |
| CPU 100% but low PPS | Single-queue bottleneck | Check `cat /proc/interrupts \| grep fman` |
| VPP shows 0 counters | Wrong interface name | `vppctl show interface` to list VPP names |
| SFP no link for 17min | Rollball PHY negotiation | Normal for SFP-10G-T copper — wait |

## Files

| Script | Location | Purpose |
|--------|----------|---------|
| `pps-bench.sh` | `data/scripts/pps-bench.sh` | DUT-side PPS measurement (kernel + VPP) |
| `pps-gen.sh` | `data/scripts/pps-gen.sh` | Generator-side traffic (Linux pktgen) |