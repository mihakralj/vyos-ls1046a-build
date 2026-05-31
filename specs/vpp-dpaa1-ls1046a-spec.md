# VPP on NXP LS1046A DPAA1 — AF_XDP Integration & HW Offload

**Status:** Draft v0.2. 2026-05-31. Supersedes v0.1 (native plugin proposal — REJECTED, preserved in Appendix A).
**Target:** VPP 25.10+ with `af_xdp` plugin, Linux 6.18+ (VyOS rolling), ARM64.
**Pre-requisite spec:** `specs/dpaa1-afxdp-modernization-spec.md` — all datapath and HW offload APIs consumed by this spec are defined there.

---

## 1. Architecture

### 1.1 Design Decision: AF_XDP, NOT a Native Plugin

VPP on LS1046A uses the **kernel AF_XDP zero-copy datapath** provided by the DPAA1 modernization driver. The v0.1 proposal (`fsl-dpaa1-um.ko` + userspace QMan/BMan portal ownership + userspace PCD) was **REJECTED** for three reasons:

1. **RC#31 — userspace QBMan ownership kills all kernel interfaces.** QMan/BMan state is SoC-global. A userspace process reprogramming portal mappings and BMan buffer pools corrupts the kernel's DPAA1 stack identically to how DPDK's `dpaa_bus` probe did (confirmed on hardware, 2026-04-03). Mixed kernel+userspace DPAA1 ownership is architecturally impossible without a SoC-level bifurcation that does not exist.
2. **AF_XDP ZC already delivers the desired architecture.** The DPAA1 modernization spec's M1–M3-3 step 7 provides `ndo_xsk_wakeup`, XSK-backed BMan pool, per-CPU NAPI with qband mapping, and `fman_port_set_rx_bpool()` reprogram-WRITE — all DUT-validated on real LS1046A silicon. Total kernel-side code: ~3 kLOC. The v0.1 proposal would have required ~12 kLOC of new, untested code for the same result.
3. **Port exclusivity is the wrong model.** AF_XDP creates sockets on kernel-owned netdevs — no unbind/rebind, no DT `fsl,userspace-managed` flag. The kernel retains full ownership of all FMan MACs. This is the proven production model (~3.5 Gbps AF_XDP today, targeting ≥7 Gbps with ZC).

### 1.2 Component Layout

VPP on LS1046A uses only standard VPP components:

```
VPP process
├── af_xdp plugin (upstream)  ← creates AF_XDP ZC sockets on kernel netdevs
├── dpdk plugin (optional)    ← for management/profiling only; NO DPAA PMD
├── ipsec* plugins (optional) ← kernel xfrm offload via ASK2 (future)
└── standard graph nodes      ← ip4-lookup, ip6-lookup, etc.

Kernel (single binary, all flavors)
├── fsl_dpaa_eth.ko (built-in)         ← owns all FMan MACs
├── af_xdp_pool.ko (CONFIG_DPAA_AF_XDP_POOL=y)  ← XSK-backed BMan pool
├── CONFIG_FSL_FMAN_PCD=y              ← shared PCD: CC, HM, Policer
└── fman_cc_tree_*, fman_hm_node_*, fman_policer_* APIs
```

### 1.3 Per-Flavor Behavior

| Flavor | VPP? | Datapath | HW Offload |
|---|---|---|---|
| default | No (VPP not shipped) | Kernel skbuf (improved by DPAA1 §5.2) | Via VyOS CLI (RPS, NETIF_F_HW_VLAN, tc/nftables) |
| vpp | Yes | AF_XDP ZC on SFP+ (eth3/eth4) | Via `set vpp settings hw-offload …` |
| ask | No (VPP not shipped) | Kernel skbuf + ASK2 CC fast path | Via ASK2 (nft flow offload, xfrm IPsec) |

---

## 2. Datapath

### 2.1 AF_XDP Zero-Copy RX/TX

VPP creates AF_XDP sockets in ZC mode on eth3/eth4. The kernel DPAA1 driver delivers frames directly into XSK UMEM chunks via BMan (FMan BMI → XSK pool BPID → UMEM).

**Consumption of DPAA1 modernization milestones:**

| Capability | DPAA1 Milestone | VPP Benefit |
|---|---|---|
| `ndo_xsk_wakeup` | M1 (dut-validated) | VPP idle CPU < 10%. No `poll-sleep-usec` needed. |
| XSK pool attach/detach | M2 (dut-validated) | `bind(XDP_ZEROCOPY)` returns 0. 100× churn clean. |
| Per-CPU NAPI + qband mapping | M3-3 step 2 (dut-validated) | True queue_id identity. `xdpsock -q N` works. Cluster-aware pinning. |
| NAPI-hooked BMan refill | M3-3 step 4 (dut-validated) | 60 s hold: 0 IVCI / 0 BUG/WARN / 16 refill batches. |
| True ZC RX (reprogram-WRITE + Recover) | M3-3 step 7 sub-increment 4b (`0103b`, entry-gate-validated) | Crash-free, reversible. Productive oracle gated on BMI register effectiveness confirm + traffic steering. Not required for ≥7 Gbps gate-3. |
| Gate-3 capacity | M3-3 (option A+C validated) | 0% drop from driver; 5.57 Gbps aggregate TCP with softirq distributed. Consumer/methodology-bound, not driver-bound. |

### 2.2 Per-Worker Configuration

VPP workers 1–3 (CPU 1–3). VPP main thread on CPU 0. DPAA1 qband mapping (§5.2) distributes RX across QMan SWPs matching the VPP worker CPUs.

```conf
# /etc/vpp/startup.conf (VPP flavor only)
cpu {
    main-core 0
    corelist-workers 1-3
}

af_xdp {
    create eth3
    create eth4
}

# No dpdk {} block for DPAA1 paths.
# No unix { poll-sleep-usec 100 } — ndo_xsk_wakeup eliminates polling.
```

### 2.3 Jumbo Frame Limitation

AF_XDP ZC MTU is limited to 3290 (DPAA1 spec §6.1.1). This is a `fsl_dpaa_mac` XDP MTU cap — not a VPP limitation. Jumbo frames on kernel-managed RJ45 ports (eth0–eth2) retain full 9578 MTU.

---

## 3. HW Offload Consumption

All HW offloads are consumed through the shared DPAA1 modernization PCD APIs, exposed via VyOS CLI.

### 3.1 CC Steering (DPAA1 §5.4)

**API:** `fman_cc_tree_install/add_key/remove_key/destroy`
**VPP CLI:** `set vpp settings hw-offload classify rule <N> protocol <tcp|udp|vxlan> target-qband <0-3>`
**Behavior:** Installs static CC tree at `pcd_ops->install`. Rules direct matching flows to specific qbands. Static tree; `commit` rebuilds. No netdev flap.

### 3.2 HM Offload (DPAA1 §5.5)

**API:** `fman_hm_node_install/destroy/caps_supported`
**VPP CLI:** `set vpp settings hw-offload vlan-strip-on-ingress`
**Behavior:** FMan strips VLAN tags before frames reach the XSK socket. VPP sees untagged frames. Sub-100 ns HM cost vs. multi-µs software path.

### 3.3 Policer (DPAA1 §5.6)

**API:** `fman_policer_install/destroy/caps_supported`
**VPP CLI:** `set vpp settings hw-offload policer per-qband-limit <rate> per-flow-limit <rate>`
**Behavior:** srTCM/trTCM per-qband or per-flow ingress rate-limit. DoS protection and SLA enforcement in silicon.

### 3.4 CEETM (DPAA1 §5.7 — blocked on SDK forward-port)

**VPP CLI (future):** `set qos policy shaper hardware ceetm …`
**Status:** Blocked until `qman_ceetm.c` + `dpaa_eth_ceetm.c` are forward-ported from NXP LSDK. Once available, VPP and kernel traffic share the CEETM root qdisc.

---

## 4. VyOS CLI Integration

### 4.1 VPP Datapath Configuration

```
set vpp settings interface eth3
set vpp settings interface eth4
```

Triggers VPP startup via `vyos-1x-010-vpp-platform-bus.patch` (AF_XDP mode). No `fsl,userspace-managed` DT property needed. No kernel netdev unbind.

### 4.2 HW Offload Configuration

```
set vpp settings hw-offload vlan-strip-on-ingress
set vpp settings hw-offload classify rule 10 protocol tcp target-qband 0
set vpp settings hw-offload policer per-qband-limit 2500000000
```

VyOS validator enforces CEETM ↔ VPP-internal shaping mutual exclusion per-port (DPAA1 spec §7.4).

---

## 5. Performance Targets

Performance targets are inherited from DPAA1 modernization spec §8.3. VPP does not add new constraints.

| Test | DPAA1 Spec Target | VPP Relevance |
|---|---|---|
| 1500B single-flow fwd | ≥ 7 Gbps | M3-3 gate-3 (flavor-agnostic; VPP `af_xdp` plugin is a valid XSK producer) |
| 1500B 4-flow 4-core | ≥ 9 Gbps | VPP 3-worker configuration |
| 64B unidirectional 1 qband | ≥ 1.5 Mpps | VPP small-packet forwarding |
| Idle CPU with ZC + need_wakeup | < 10% | VPP worker threads not busy-spinning |

---

## 6. VPP Plugin Changes (None Required)

No new VPP plugin is needed. The upstream `af_xdp` plugin works as-is. No DPDK DPAA PMD. No `libdpaa1_um.so`. No char devices. No custom kernel module.

### 6.1 What We Do NOT Build

- `fsl-dpaa1-um.ko` — REJECTED. Kernel module with userspace DPAA1 portal ownership.
- `libdpaa1_um.so` — REJECTED. Userspace QMan/BMan/FD library.
- `dpaa1_plugin.so` — REJECTED. Custom VPP I/O plugin.
- `/dev/dpaa1/*` char devices — REJECTED. Portal mmap from userspace.
- PAMU programming for userspace DMA — REJECTED. PAMU is PPC-only (DPAA1 spec §4.6).
- Microcode 106.4.18 — WRONG. Mono Gateway ships ucode 210.10.1.
- `fsl,userspace-managed` DT property — UNNECESSARY. AF_XDP coexists with kernel netdev ownership.

---

## 7. Open Questions

1. **VPP `af_xdp` plugin multi-queue.** The upstream VPP `af_xdp` plugin creates one XSK socket per queue. Does it support `xdpsock -q N` semantics (bind specific queue)? Needed for per-worker qband affinity.
2. **VPP buffer pool integration with UMEM.** VPP's internal buffer allocator vs. AF_XDP UMEM chunks. Start with separate pools; evaluate unified pool in v2.
3. **A050385 erratum interaction with VPP headroom.** VPP defaults `XDP_PACKET_HEADROOM = 256`, which satisfies the ≥64 B UMEM headroom requirement (DPAA1 spec §6.1.5).

---

## Appendix A: Rejected v0.1 Native Plugin Proposal (Historical Record)

**This appendix is historical only. The v0.1 proposal was REJECTED on 2026-05-31.**

The v0.1 spec proposed a `fsl-dpaa1-um.ko` kernel module + userspace library +
VPP plugin stack (~12 kLOC) that would own QMan/BMan portals from userspace,
program FMan PCD via a custom ioctl interface, and manage hugepage-backed buffer
pools independently from the kernel. Key rejection reasons:

1. RC#31: userspace QBMan reprogramming kills all kernel FMan interfaces globally.
2. AF_XDP ZC already delivers the architecture with 25% of the code and proven DUT stability.
3. Port exclusivity breaks VyOS's kernel-managed management ports.
4. PAMU programming is architecturally impossible on arm64 LS1046A (DPAA1 spec §4.6).

[Original v0.1 text follows, verbatim.]

---

## 1. Scope

A VPP input/output plugin that drives the LS1046A DPAA1 datapath directly, without DPDK, USDPAA, or NXP's `fmlib`/`fmc`. The plugin owns QMan and BMan portals from userspace, configures FMan ports and PCD via a small kernel helper, and feeds packets into VPP's vector graph using the native buffer format.

[... rest of original v0.1 content preserved in git history ...]

---

**End of v0.1 spec.**