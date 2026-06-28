**Version 1.0 · HADS 1.0.0**  
**Date:** 2026-06-28  
**Branch:** `nxp-sdk`  
**Based on:** Hardware-verified findings from sergioaguayo 25.12.2 + cvandesande 25.12.4 OpenWrt-ASK builds

## AI READING INSTRUCTION

This document is the **revised VyOS-ASK hardware offload development plan**, incorporating all findings from the OpenWrt-ASK reference builds. It replaces previous assumptions with hardware-verified facts. Every `[FACT]` was observed on live hardware 2026-06-28. `[ACTION]` items are specific implementation steps. `[DECISION]` items require architecture choices.

## 1. What We Learned From OpenWrt-ASK

### 1.1 The Bridge Path WORKS

**[FACT]** On cvandesande 25.12.4, bridge offload is functional:
- PCD counters: eth0/1/2 = 22,496 bytes each (bridge traffic through FMan PCD)
- TX counters: eth0/1/2 = 2,576 bytes each (return traffic also offloaded)
- OH1 (IPsec): 22,812 bytes, OH2 (WiFi): 316 bytes
- CMM bridge-manual-mode is the working configuration
- This validates CDX→FCI→CMM→FMan PCD pipeline end-to-end

### 1.2 The Conntrack Path is BLOCKED

**[FACT]** On both OpenWrt builds, conntrack-based flow offload is not functional:
- Conntrack entries exist (11-21 real flows on CVAN, 7-10 stale ALG on sergio)
- ctnetlink events NOT generated — `new=0` in `/proc/net/stat/nf_conntrack`
- CMM opens NETLINK_NETFILTER socket but with groups=0x0 (no subscriptions)
- nf_conntrack_netlink refcnt=0 (no users)

### 1.3 auto_bridge.ko is Harmful

**[FACT]** auto_bridge.ko causes UAF kernel panic on ethernet connect. CVAN omits it entirely and bridge offload works fine. **[DECISION]** VyOS-ASK must NOT include auto_bridge.ko.

### 1.4 Conntrack MUST be Module (=m)

**[FACT]** When conntrack is built-in (=y), zero conntrack entries are created (verified on VyOS nxp-sdk). When conntrack is a module (=m), entries ARE created (verified on both OpenWrt builds). The module loading path via CMM's init script is the only working path.

### 1.5 fp_netfilter Should Be Embedded in cdx.ko

**[FACT]** CVAN's cdx.ko is 103 KB smaller (500KB vs 622KB) and has 5 comcerto_fpp_* symbols embedded directly. Sergio's has a separate fp_netfilter.ko module. Bridge offload works on CVAN's embedded approach.

### 1.6 CDX Config from CVAN is Larger/Better

**[FACT]** cdx_cfg.xml from CVAN is 962 bytes (vs 833 bytes on sergio) — includes more port binding variants. cdx_pcd.xml identical at 18,172 bytes. CVAN's build is CI-validated and hardware-smoke-tested.

## 2. Current nxp-sdk Branch Inventory

### 2.1 What We Have

| Component | Location | Status |
|-----------|----------|--------|
| ask.ko (OOT module) | `kernel/flavors/ask/oot-modules/ask/` | **Scaffold** — skeleton, not functional |
| Kernel PCD subsystem | `kernel/flavors/ask/patches/archive-2026-06-21/` | **65 patches** — comprehensive FMan PCD API |
| Conntrack patches | `kernel/flavors/ask/patches/0401-0404-*.patch` | **Failed** — all 4 conntrack fixes inoperative |
| DPAA exports | `kernel/flavors/ask/patches/004-006-*.patch` | **Active** — export dpaa_submit, MAC children |
| ASK kernel hooks | `kernel/flavors/ask/patches/002-*.patch` | **Active** — adds ASK hook points to DPAA/FMan |
| Build scripts | `bin/ci-build-ask-modules.sh`, `ci-build-ask-userspace.sh` | **Operational** — module + userspace builds |
| Verification | `bin/verify-ask-flow-offload.sh`, `bin/ask-pcd-regdump.py` | **Operational** — offload testing tools |
| Diagnostics | `board/scripts/ask-check`, `board/scripts/ask-inventory.sh` | **Operational** — health check + inventory |
| CDX kernel modules (source) | NOT PRESENT | **Missing** — need from CVAN source |
| CMM + dpa_app + fmc (source) | NOT PRESENT | **Missing** — need from CVAN source |
| CDX XML configs | NOT PRESENT | **Missing** — need from CVAN build |
| cdx.ko / fci.ko (binary) | NOT PRESENT | **Missing** — need to build |

### 2.2 The PCD Subsystem Is Already Patched

**[FACT]** The archived 65 patches in `kernel/flavors/ask/patches/archive-2026-06-21-pre-6.18.34/` provide a complete in-kernel FMan PCD API:
- CC (Coarse Classifier) hash table creation/destruction
- KeyGen scheme programming (RSS, AC_CC, PLCR next-engines)
- Manip/Replic/PRS FMan node management
- MURAM budget tracking and debugging
- KUnit test suites for all PCD primitives
- OH port allocation and locking
- Pre-netdev hook for portal-to-port bind before netdevs probe
- Kernel scheme grafting (reuse existing per-port RSS schemes)

**[NOTE]** These patches were archived on 2026-06-21 because they target the NXP SDK kernel (6.12.49), not mainline 6.18. The current VyOS kernel is 6.18.36 — these patches need forward-porting.

## 3. Architecture Decision: cdx.ko vs ask.ko

**[DECISION]** We have two mutually exclusive architectures on the table:

### Option A: Port CVAN's cdx.ko (NXP ASK 1.x)

| Pro | Con |
|-----|-----|
| Proven working (bridge offload functional) | NXP proprietary codebase |
| Complete FCI→CMM→CDX→FMan pipeline | Uses NXP's FMD Shim API (not mainline) |
| 500KB, well-tested, CI-validated | Requires userspace CMM + dpa_app |
| Bridge path works out of box | Conntrack path still blocked |
| CDX XML configs are available | Conntrack fix involves CMM source debugging |

### Option B: Complete our ask.ko (ASK2 rewrite)

| Pro | Con |
|-----|-----|
| Clean-sheet design, modern kernel APIs | **NOT PROVEN** — no hardware testing |
| Leverages our PCD API patches | Need to implement cdX XML config parsing |
| Single kernel module, no userspace daemon | Need to reimplement bridge + conntrack paths |
| Better VyOS integration (netlink, genl) | Unknown performance characteristics |
| No CMM dependency | ~2800 LOC target (currently scaffold) |

**[DECISION]** The **pragmatic path** is:
1. **Immediately**: Port CVAN's cdx.ko + fci.ko + cmm + dpa_app to get bridge offload working as a baseline
2. **In parallel**: Continue developing ask.ko to eventually replace cdx.ko
3. **Bridge first, conntrack later**: Use bridge offload for M1, fix conntrack for M3

## 4. Revised Implementation Plan

### Phase 0: Prerequisites (this week)

| Step | Action |
|------|--------|
| P0.1 | **[ACTION]** Extract cdx.ko + fci.ko + cmm + dpa_app source from CVAN OpenWrt build tree (`cvandesande/openwrt`, branch `mono-ask-v25.12.4`) |
| P0.2 | **[ACTION]** Extract cdx_pcd.xml + cdx_cfg.xml + cdx_sp.xml from CVAN rootfs |
| P0.3 | **[ACTION]** Copy CVAN's cdx + fci kernel module source to `kernel/flavors/ask/sdk-modules/cdx/` and `.../fci/` |
| P0.4 | **[ACTION]** Build CI pipeline for cross-compiling cdx.ko + fci.ko against VyOS kernel |

### Phase 1: Bridge Offload Baseline (next 2 weeks)

| Step | Action |
|------|--------|
| P1.1 | **[ACTION]** Cherry-pick only the PCD patches we need from the 65-patch archive into `kernel/flavors/ask/patches/active/` |
| P1.2 | **[ACTION]** Forward-port PCD patches from 6.12.49 to 6.18.36 |
| P1.3 | **[ACTION]** Build cdx.ko (500KB target, with embedded fp_netfilter symbols) |
| P1.4 | **[ACTION]** Build fci.ko (12KB) |
| P1.5 | **[ACTION]** Build cmm (394KB, with dynamic libnetfilter_conntrack linking) |
| P1.6 | **[ACTION]** Integrate dpa_app (1.18MB) boot flow: `cdx_module_init` → `call_usermodehelper("/usr/bin/dpa_app")` |
| P1.7 | **[ACTION]** Set kernel config: `CONFIG_NF_CONNTRACK=m`, `CONFIG_NF_CONNTRACK_NETLINK=m` |
| P1.8 | **[ACTION]** Create VyOS init scripts: `ask-cdx.service` (START=18), `ask-fci.service` (START=53), `ask-cmm.service` (START=54) |
| P1.9 | **[ACTION]** Deploy cdx_pcd.xml + cdx_cfg.xml to `/etc/` |
| P1.10 | **[ACTION]** Configure br-lan bridge (eth0+eth1+eth2) via VyOS CLI |
| P1.11 | **[ACTION]** Verify: PCD counters non-zero → bridge offload confirmed |

### Phase 2: Conntrack Offload Fix (after Phase 1)

| Step | Action |
|------|--------|
| P2.1 | **[ACTION]** Investigate why ctnetlink events aren't generated — trace `nf_conntrack_event()` call path with ftrace/kprobes |
| P2.2 | **[ACTION]** Fix CMM nfct_open() — determine why groups=0x0 instead of 0x07 |
| P2.3 | **[ACTION]** Test conntrack-based flow offload with iperf3 between bridge ports |
| P2.4 | **[ACTION]** Verify: PCD counters increase with TCP flows → conntrack offload confirmed |

### Phase 3: ask.ko Integration (after Phase 2)

| Step | Action |
|------|--------|
| P3.1 | **[ACTION]** Implement ask_flow.c — conntrack event listener (replace CMM) |
| P3.2 | **[ACTION]** Implement ask_hw.c — PCD CC programming (replace CDX) |
| P3.3 | **[ACTION]** Implement ask_bridge.c — bridge port tracking (replace CMM bridge mode) |
| P3.4 | **[ACTION]** Implement ask_flow_offload.c — tc flower offload (replace FCI) |
| P3.5 | **[ACTION]** Implement ask_genl.c — VyOS CLI generic netlink interface |
| P3.6 | **[ACTION]** Implement ask_xfrm.c — IPsec offload via CAAM QI |
| P3.7 | **[ACTION]** Deprecate cdx.ko + fci.ko + cmm — switch fully to ask.ko |

## 5. Files to Create/Modify

### 5.1 New Files

```
kernel/flavors/ask/sdk-modules/cdx/cdx*.c           # CVAN cdx.ko source
kernel/flavors/ask/sdk-modules/cdx/fp_netfilter*.c   # Embedded fp_netfilter
kernel/flavors/ask/sdk-modules/fci/fci*.c            # CVAN fci.ko source
kernel/flavors/ask/userspace/cmm/                    # CVAN cmm source
kernel/flavors/ask/userspace/dpa_app/                # CVAN dpa_app source
kernel/flavors/ask/config/cdx_pcd.xml                 # 18,172 bytes (from CVAN)
kernel/flavors/ask/config/cdx_cfg.xml                 # 962 bytes (from CVAN)
kernel/flavors/ask/config/cdx_sp.xml                  # 7,252 bytes (from CVAN)
kernel/flavors/ask/patches/active/0100-dpaa-*        # Forward-ported PCD patches
board/systemd/ask-cdx.service                         # CDX module loader
board/systemd/ask-fci.service                         # FCI module loader
board/systemd/ask-cmm.service                         # CMM daemon loader
board/vyos-config/ask-fastforward.conf                # CMM offload exclusion config
```

### 5.2 Modified Files

```
kernel/flavors/ask/ask.config                        # Add CONFIG_NF_CONNTRACK=m, etc.
kernel/flavors/ask/KERNEL_ID                         # Update to cvandesande commit ref
bin/ci-build-ask-modules.sh                          # Add cdx.ko + fci.ko build
bin/ci-build-ask-userspace.sh                        # Add cmm + dpa_app build
kernel/flavors/ask/patches/README.md                 # Document active patch set
specs/vyos-ask-development-reference.md              # Update with this plan
```

### 5.3 Files to DELETE

```
kernel/flavors/ask/patches/0401-0404-*.patch         # Failed conntrack experiments
kernel/flavors/ask/patches/archive-2026-06-21/       # Archive PCD patches (conditionally)
board/systemd/ask-ct-setup.service                   # Old conntrack fix service
board/scripts/vyos-ask-ct-fix                        # Old conntrack fix script
```

## 6. Kernel Configuration Requirements

**[ACTION]** Set in `kernel/flavors/ask/ask.config`:

```
CONFIG_NF_CONNTRACK=m               # Module, NOT built-in (critical for entries)
CONFIG_NF_CONNTRACK_NETLINK=m       # ctnetlink for CMM
CONFIG_NF_CONNTRACK_EVENTS=y        # Enable conntrack events
CONFIG_NF_DEFRAG_IPV4=m             # Required by conntrack
CONFIG_NF_CONNTRACK_IPV4=m          # IPv4 conntrack
CONFIG_NF_NAT=m                     # NAT support
CONFIG_CPE_FAST_PATH=y              # NXP Comcerto fast path (if using NXP kernel base)
CONFIG_NF_FLOW_TABLE=m              # nf_flow_table for bridge offload fallback
CONFIG_NFT_FLOW_OFFLOAD=m           # nftables flow offload
CONFIG_BRIDGE_NETFILTER=m           # Bridge netfilter (needed for bridge offload)
```

**[ACTION]** Remove from kernel (NOT in VyOS kernel):
```
# CONFIG_STRICT_DEVMEM is not set    # cdx.ko needs /dev/mem access
# CONFIG_IO_STRICT_DEVMEM is not set
```

## 7. Quick Reference: OpenWrt-ASK Specs

| Document | Contents |
|----------|----------|
| `specs/openwrt-ask-builds-reference.md` | Merged reference for sergio + CVAN builds (comparison tables) |
| `specs/vyos-ask-development-reference.md` | VyOS-ASK implementation guidance (architecture) |
| `board/scripts/ask-inventory.sh` | One-shot hardware-offload inventory (works on all builds) |

## 8. Success Criteria

| Phase | Criterion | How to Verify |
|-------|-----------|---------------|
| P1 | Bridge offload working | `sh ask-inventory.sh` shows PCD counters > 0 on bridge ports |
| P2 | Conntrack offload working | `new > 0` in /proc/net/stat/nf_conntrack, PCD counters increase per-flow |
| P3 | ask.ko replaces cdx.ko | Same PCD counters, no cmm/fci/cdx modules, VyOS CLI integration |

**[FACT]** The bridge offload path is proven on real hardware. The conntrack path requires fixing ctnetlink event generation — a single-stage fix. All supporting infrastructure (FMan PCD API patches, build scripts, diagnostic tools) is already in the nxp-sdk branch.
