# VPP on NXP LS1046A DPAA1 — AF_XDP Integration & HW Offload
**Version 0.2 (draft)** · 2026-05-31 · 2026-06-09 · HADS 1.0.0

---

## AI READING INSTRUCTION

Read `[SPEC]` and `[BUG]` blocks for authoritative facts.
Read `[NOTE]` only if additional context is needed.
`[?]` blocks are unverified — treat with lower confidence.

---

## 1. STATUS & SCOPE

**[SPEC]**
- Status: Draft v0.2, 2026-05-31. Supersedes v0.1 (native plugin proposal — REJECTED, preserved in §10 Appendix A).
- Target: VPP 25.10+ with `af_xdp` plugin, Linux 6.18+ (VyOS rolling), ARM64.
- Pre-requisite spec: `specs/dpaa1-afxdp-modernization-spec.md` — all datapath and HW offload APIs consumed here are defined there.

---

## 2. ARCHITECTURE

### 2.1 Design decision: AF_XDP, NOT a native plugin

**[SPEC]**
- VPP on LS1046A uses the kernel AF_XDP zero-copy datapath provided by the DPAA1 modernization driver.
- The v0.1 proposal (`fsl-dpaa1-um.ko` + userspace QMan/BMan portal ownership + userspace PCD) was REJECTED for three reasons:
  2. AF_XDP ZC already delivers the desired architecture. The DPAA1 spec's M1–M3-3 step 7 provides `ndo_xsk_wakeup`, XSK-backed BMan pool, per-CPU NAPI with qband mapping, and `fman_port_set_rx_bpool()` reprogram-WRITE — all DUT-validated. Total kernel-side: ~3 kLOC vs. ~12 kLOC for v0.1.
  3. Port exclusivity is the wrong model. AF_XDP creates sockets on kernel-owned netdevs — no unbind/rebind, no DT `fsl,userspace-managed` flag; the kernel retains full ownership of all FMan MACs (~3.5 Gbps AF_XDP today, targeting ≥7 Gbps with ZC).

**[BUG] RC#31 — userspace QBMan ownership kills all kernel interfaces**
- Symptom: all kernel DPAA1 interfaces are corrupted (confirmed on hardware 2026-04-03).
- Cause: QMan/BMan state is SoC-global; a userspace process reprogramming portal mappings and BMan buffer pools corrupts the kernel's DPAA1 stack identically to how DPDK's `dpaa_bus` probe did. Mixed kernel+userspace DPAA1 ownership is architecturally impossible without a SoC-level bifurcation that does not exist.
- Fix: use AF_XDP on kernel-owned netdevs; the userspace-portal-ownership model (v0.1) is rejected.

### 2.2 Component layout

**[SPEC]**
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

### 2.3 Per-flavor behavior

**[SPEC]**

| Flavor | VPP? | Datapath | HW Offload |
|---|---|---|---|
| default | No (VPP not shipped) | Kernel skbuf (improved by DPAA1 §5.2) | Via VyOS CLI (RPS, NETIF_F_HW_VLAN, tc/nftables) |
| vpp | Yes | AF_XDP ZC on SFP+ (eth3/eth4) | Via `set vpp settings hw-offload …` |
| ask | No (VPP not shipped) | Kernel skbuf + ASK2 CC fast path | Via ASK2 (nft flow offload, xfrm IPsec) |

---

## 3. DATAPATH

### 3.1 AF_XDP zero-copy RX/TX

**[SPEC]**
- VPP creates AF_XDP sockets in ZC mode on eth3/eth4. The kernel DPAA1 driver delivers frames directly into XSK UMEM chunks via BMan (FMan BMI → XSK pool BPID → UMEM).
- Consumption of DPAA1 modernization milestones:

| Capability | DPAA1 Milestone | VPP Benefit |
|---|---|---|
| `ndo_xsk_wakeup` | M1 (dut-validated) | VPP idle CPU < 10%. No `poll-sleep-usec` needed. |
| XSK pool attach/detach | M2 (dut-validated) | `bind(XDP_ZEROCOPY)` returns 0. 100× churn clean. |
| Per-CPU NAPI + qband mapping | M3-3 step 2 (dut-validated) | True queue_id identity. `xdpsock -q N` works. Cluster-aware pinning. |
| NAPI-hooked BMan refill | M3-3 step 4 (dut-validated) | 60 s hold: 0 IVCI / 0 BUG/WARN / 16 refill batches. |
| True ZC RX (reprogram-WRITE + Recover) | M3-3 step 7 sub-increment 4b (`0103b`, entry-gate-validated) | Crash-free, reversible. Productive oracle gated on BMI register effectiveness confirm + traffic steering. Not required for ≥7 Gbps gate-3. |
| Gate-3 capacity | M3-3 (option A+C validated) | 0% drop from driver; 5.57 Gbps aggregate TCP with softirq distributed. Consumer/methodology-bound, not driver-bound. |

### 3.2 Per-worker configuration

**[SPEC]**
- VPP workers 1–3 (CPU 1–3); VPP main thread on CPU 0. DPAA1 qband mapping (§5.2 of the DPAA1 spec) distributes RX across QMan SWPs matching the VPP worker CPUs.
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

### 3.3 Jumbo frame limitation

**[SPEC]**
- AF_XDP ZC MTU is limited to 3290 (DPAA1 spec §6.1.1) — a `fsl_dpaa_mac` XDP MTU cap, not a VPP limitation.
- Jumbo frames on kernel-managed RJ45 ports (eth0–eth2) retain full 9578 MTU.

---

## 4. HW OFFLOAD CONSUMPTION

**[SPEC]**
All HW offloads are consumed through the shared DPAA1 modernization PCD APIs, exposed via VyOS CLI.

### 4.1 CC steering (DPAA1 §5.4)

**[SPEC]**
- API: `fman_cc_tree_install/add_key/remove_key/destroy`
- VPP CLI: `set vpp settings hw-offload classify rule <N> protocol <tcp|udp|vxlan> target-qband <0-3>`
- Behavior: installs a static CC tree at `pcd_ops->install`; rules direct matching flows to specific qbands; static tree, `commit` rebuilds, no netdev flap.

### 4.2 HM offload (DPAA1 §5.5)

**[SPEC]**
- API: `fman_hm_node_install/destroy/caps_supported`
- VPP CLI: `set vpp settings hw-offload vlan-strip-on-ingress`
- Behavior: FMan strips VLAN tags before frames reach the XSK socket; VPP sees untagged frames. Sub-100 ns HM cost vs. multi-µs software path.

### 4.3 Policer (DPAA1 §5.6)

**[SPEC]**
- API: `fman_policer_install/destroy/caps_supported`
- VPP CLI: `set vpp settings hw-offload policer per-qband-limit <rate> per-flow-limit <rate>`
- Behavior: srTCM/trTCM per-qband or per-flow ingress rate-limit; DoS protection and SLA enforcement in silicon.

### 4.4 CEETM (DPAA1 §5.7 — blocked on SDK forward-port)

**[SPEC]**
- VPP CLI (future): `set qos policy shaper hardware ceetm …`
- Status: blocked until `qman_ceetm.c` + `dpaa_eth_ceetm.c` are forward-ported from NXP LSDK. Once available, VPP and kernel traffic share the CEETM root qdisc.

---

## 5. VyOS CLI INTEGRATION

### 5.1 VPP datapath configuration

**[SPEC]**
```
set vpp settings interface eth3
set vpp settings interface eth4
```
- Triggers VPP startup via `vyos-1x-010-vpp-platform-bus.patch` (AF_XDP mode). No `fsl,userspace-managed` DT property; no kernel netdev unbind.

### 5.2 HW offload configuration

**[SPEC]**
```
set vpp settings hw-offload vlan-strip-on-ingress
set vpp settings hw-offload classify rule 10 protocol tcp target-qband 0
set vpp settings hw-offload policer per-qband-limit 2500000000
```
- VyOS validator enforces CEETM ↔ VPP-internal shaping mutual exclusion per-port (DPAA1 spec §7.4).

---

## 6. PERFORMANCE TARGETS

**[SPEC]**
Inherited from DPAA1 modernization spec §8.3; VPP adds no new constraints.

| Test | DPAA1 Spec Target | VPP Relevance |
|---|---|---|
| 1500B single-flow fwd | ≥ 7 Gbps | M3-3 gate-3 (flavor-agnostic; VPP `af_xdp` plugin is a valid XSK producer) |
| 1500B 4-flow 4-core | ≥ 9 Gbps | VPP 3-worker configuration |
| 64B unidirectional 1 qband | ≥ 1.5 Mpps | VPP small-packet forwarding |
| Idle CPU with ZC + need_wakeup | < 10% | VPP worker threads not busy-spinning |

---

## 7. VPP PLUGIN CHANGES (NONE REQUIRED)

**[SPEC]**
- No new VPP plugin is needed. The upstream `af_xdp` plugin works as-is. No DPDK DPAA PMD, no `libdpaa1_um.so`, no char devices, no custom kernel module.

### 7.1 What we do NOT build

**[SPEC]**
- `fsl-dpaa1-um.ko` — REJECTED. Kernel module with userspace DPAA1 portal ownership.
- `libdpaa1_um.so` — REJECTED. Userspace QMan/BMan/FD library.
- `dpaa1_plugin.so` — REJECTED. Custom VPP I/O plugin.
- `/dev/dpaa1/*` char devices — REJECTED. Portal mmap from userspace.
- PAMU programming for userspace DMA — REJECTED. PAMU is PPC-only (DPAA1 spec §4.6).
- Microcode 106.4.18 — WRONG. Mono Gateway ships ucode 210.10.1.
- `fsl,userspace-managed` DT property — UNNECESSARY. AF_XDP coexists with kernel netdev ownership.

---

## 8. OPEN QUESTIONS

**[?]**
1. VPP `af_xdp` plugin multi-queue: the upstream plugin creates one XSK socket per queue. Does it support `xdpsock -q N` semantics (bind specific queue)? Needed for per-worker qband affinity.
2. VPP buffer pool integration with UMEM: VPP's internal buffer allocator vs. AF_XDP UMEM chunks. Start with separate pools; evaluate unified pool in v2.
3. A050385 erratum interaction with VPP headroom: VPP defaults `XDP_PACKET_HEADROOM = 256`, which satisfies the ≥64 B UMEM headroom requirement (DPAA1 spec §6.1.5).

---

## 9. (reserved)

**[NOTE]**
No section 9 in the source; numbering continues at the appendix below.

---

## 10. APPENDIX A: REJECTED v0.1 NATIVE PLUGIN PROPOSAL (HISTORICAL RECORD)

**[NOTE]**
This appendix is historical only. The v0.1 proposal was REJECTED on 2026-05-31. The v0.1 spec proposed a `fsl-dpaa1-um.ko` kernel module + userspace library + VPP plugin stack (~12 kLOC) that would own QMan/BMan portals from userspace, program FMan PCD via a custom ioctl interface, and manage hugepage-backed buffer pools independently from the kernel.

**[SPEC]**
Key rejection reasons:
1. RC#31: userspace QBMan reprogramming kills all kernel FMan interfaces globally.
2. AF_XDP ZC already delivers the architecture with 25% of the code and proven DUT stability.
3. Port exclusivity breaks VyOS's kernel-managed management ports.
4. PAMU programming is architecturally impossible on arm64 LS1046A (DPAA1 spec §4.6).

**[NOTE]**
Original v0.1 text follows, verbatim.

### 10.1 (v0.1) Scope

**[NOTE]**
A VPP input/output plugin that drives the LS1046A DPAA1 datapath directly, without DPDK, USDPAA, or NXP's `fmlib`/`fmc`. The plugin owns QMan and BMan portals from userspace, configures FMan ports and PCD via a small kernel helper, and feeds packets into VPP's vector graph using the native buffer format.

**[NOTE]**
[... rest of original v0.1 content preserved in git history ...]

**[NOTE]**
End of v0.1 spec.