# Traffic Harness — DUT SFP+ acceptance-gate generator

**Date:** 2026-06-08
**Status:** Live and validated. Supersedes the "10.99.1.2 undocumented / not SSH-able" note in `plans/COMPLETION-PLAN.md` §6.

This is the controllable traffic generator that unblocks every remaining functional
acceptance gate (DPAA1 M3-3b/3c/3d/3e wire tests, gate-3 ≥7 Gbps, VPP HW benchmark,
ASK2 M2 ≥7 Gbps @ ≤5% CPU). It already exists — two purpose-built Proxmox LXCs on
**heidi** sitting one per DUT SFP+ subnet, with the DUT as their L3 gateway.

## Topology

```mermaid
flowchart LR
    subgraph HEIDI["heidi (Proxmox, 192.168.1.15) — root via ssh heidi"]
        CT201["CT201 lxc201<br/>10.99.1.2/30<br/>gw 10.99.1.1<br/>iperf3"]
        CT202["CT202 lxc202<br/>10.11.1.2/29<br/>gw 10.11.1.1<br/>iperf3"]
        VMBR0["vmbr0 (flat L2, untagged)"]
        NIC["enp35s0f1<br/>Intel ixgbe 10G<br/>63 SR-IOV VFs free"]
        CT201 --- VMBR0
        CT202 --- VMBR0
        VMBR0 --- NIC
    end
    NIC ===|10G| SW["10G switch fabric"]
    SW ===|SFP+| ETH3["DUT eth3<br/>10.99.1.1/30"]
    SW ===|SFP+| ETH4["DUT eth4<br/>10.11.1.1/29"]
    subgraph DUT["DUT mono (LS1046A, ssh mono / 192.168.1.190)"]
        ETH3 --> ROUTE["kernel ip_forward=1"] --> ETH4
    end
```

Both LXCs share one flat untagged bridge (`vmbr0` → `enp35s0f1`, 10G, jumbo to MTU
9000). Because CT201 and CT202 are in **different /30 subnets** whose gateway is the
DUT, all CT201↔CT202 traffic is **forced through the DUT as a router**
(eth3 → ip_forward → eth4). Verified: `ping` shows `TTL=63` (one hop) and 0% loss.

## Access

| Node | How | Notes |
|---|---|---|
| heidi host | `ssh heidi` (admin, `~/.ssh/admin_key`), `sudo` OK | Proxmox VE 8, kernel 6.8 |
| CT201 (eth3 peer) | `ssh heidi 'sudo pct exec 201 -- <cmd>'` | Debian 12, iperf3 preinstalled |
| CT202 (eth4 peer) | `ssh heidi 'sudo pct exec 202 -- <cmd>'` | Debian 12, iperf3 preinstalled |
| DUT | `ssh mono` (vyos, `~/.ssh/vyos_vanity`) | eth3=10.99.1.1, eth4=10.11.1.1 |

## Quick start — routed forwarding test (eth3 → DUT → eth4)

```bash
# Start iperf3 server on the eth4 peer
ssh heidi 'sudo pct exec 202 -- sh -c "pkill iperf3; iperf3 -s -D"'

# Drive load from the eth3 peer (8 TCP streams, through the DUT)
ssh heidi 'sudo pct exec 201 -- iperf3 -c 10.11.1.2 -P 8 -t 30'

# UDP at a target rate (for policer-cap / red-drop and clean throughput)
ssh heidi 'sudo pct exec 201 -- iperf3 -c 10.11.1.2 -u -b 9G -l 1400 -t 30'
```

**Baseline measured 2026-06-08** (default-flavor image, plain kernel L3 forwarding,
no offload): **4.14 Gbit/s** @ 8 TCP streams. This is the software-routing floor; the
offloaded flavors (ASK2 M2, DPAA1 CC/CEETM) are what we measure against the ≥7 Gbps
gate from here.

## Capability per gate

| Gate | Method on this harness |
|---|---|
| Gate-3 ≥7 Gbps literal | multi-stream iperf3 (`-P`), or UDP `-b`; for true wire-rate use TRex on an SR-IOV VF (below) |
| M3-3d policer throughput cap | UDP `iperf3 -u -b 9G` offered into a policed flow; watch DUT red-drop counters |
| ASK2 M2 (≥7 Gbps @ ≤5% CPU) | CT201→DUT→CT202 forwarding load while sampling DUT kernel-net CPU |
| VPP flavor benchmark | same forwarding path with eth3/eth4 assigned to VPP (MTU ≤3290 on AF_XDP) |
| M3-3c HM 802.1Q strip/insert | **needs tagged frames** — bridge is untagged; use `scapy`/`trafgen`/TRex to push 802.1Q (iperf3 cannot tag) |

## Wire-rate / 802.1Q upgrade path (deferred, not yet built)

For a clean ≥7–10 Gbps stateless source and precise 802.1Q generation, `enp35s0f1`
exposes **63 SR-IOV VFs** (`sriov_totalvfs=63`, currently `0`). Plan when needed:

1. `echo N | sudo tee /sys/class/net/enp35s0f1/device/sriov_numvfs` (N≥1).
2. Optionally pin a VLAN: `sudo ip link set enp35s0f1 vf 0 vlan <id>`.
3. Pass the VF into a dedicated LXC/VM (`hostpci`/`pct` device passthrough), bind to
   `vfio-pci`, set up hugepages, run **TRex** or **DPDK-pktgen**.

This keeps the PF (and the existing CT201/CT202 + host fabric) on the kernel — do
**not** bind the whole `enp35s0f1` PF to DPDK, that would drop vmbr0 and the harness.

## Do-not-disturb

- **`main` (192.168.1.2)** is the production LAN/WAN gateway — never use it as a
  generator; its 10G ports carry live `192.168.1.0/16` + WAN.
- **`backup` (192.168.1.3)** has a 10G leg (`eth1`, 10.11.1.3) on the eth4 subnet and a
  free SFP+ cage (`eth3`, down) — usable as an optional second endpoint, but single-CPU
  iperf3 won't reach line rate alone.
