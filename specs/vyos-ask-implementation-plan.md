**Version 2.0 · HADS 1.0.0**  
**Date:** 2026-06-28  
**Branch:** `nxp-sdk`  
**Scope:** High-fidelity NXP ASK 1.x native SDK port to VyOS (NOT ASK2 rewrite)  
**Status:** Kernel compiles — SDK overlay + patches applied cleanly, `Image` produced  
**Based on:** Hardware-verified findings from sergioaguayo 25.12.2 + cvandesande 25.12.4 builds

## 0. Architecture: cvandesande's Clean SDK Graft

**[SPEC]** The cvandesande OpenWrt build uses a layered SDK graft approach:

```
target/linux/layerscape/
  files/              ← SDK source overlay (228 files, 5.7 MB)
    drivers/net/ethernet/freescale/sdk_dpaa/
    drivers/net/ethernet/freescale/sdk_fman/
    drivers/staging/fsl_qbman/
    include/           ← kernel-level SDK headers
  patches-6.12/
    720-725            ← small, focused ASK patches (not monolithic)
```

**[SPEC]** Our `nxp-sdk` branch mirrors this exactly:

```
kernel/flavors/ask/
  sdk-sources/         ← cvandesande's files/ (228 files)
  patches/             ← cvandesande's 720-725 + our 005-006
    active: 005, 006, 721, 723, 725
    missing: 722 (PPPoE hunk fails — fix later)
    missing: 724 (xfrm changes fail — fix later)
    archive: 002, 004, 0401-0404, 720 (redundant/failed)
```

**[SPEC]** Kernel staging verified 2026-06-28:
```bash
FLAVOR=ask bash kernel/common/scripts/stage-kernel.sh --flavor ask
# → 8 patches applied cleanly, .config written (7541 lines)
cd work/linux-6.12.49 && make -j$(nproc) ARCH=arm64 Image
# → vmlinux linked, Image produced (39 MB)
```

**[NOTE]** `stage-kernel.sh` does: (1) overlay sdk-sources/ onto kernel tree, (2) apply common/board/vyos patches, (3) apply flavor-specific patches. This matches cvandesande's OpenWrt build flow exactly.

## 1. What We Learned From OpenWrt-ASK

### 1.1 Bridge Offload WORKS

**[FACT]** On cvandesande 25.12.4, bridge offload is functional:
- PCD counters: eth0/1/2 = 22,496 bytes each
- CMM bridge-manual-mode is the working configuration
- Validates CDX→FCI→CMM→FMan PCD pipeline end-to-end

### 1.2 Conntrack as Module

**[FACT]** CONFIG_NF_CONNTRACK=m required for entries to exist. Built-in (=y) produces zero entries. CMM init script must `insmod nf_conntrack`.

### 1.3 No auto_bridge.ko

**[FACT]** Not needed for bridge offload. Causes UAF crash.

### 1.4 fp_netfilter in cdx.ko

**[FACT]** CVAN embeds 5 comcerto_fpp_* symbols in cdx.ko (500 KB). No separate fp_netfilter.ko module.

## 2. Revised Implementation Plan

### Phase 0: SDK Source + Kernel Build ✅ COMPLETE

| Step | Action | Status |
|------|--------|--------|
| P0.1 | Clone cvandesande/openwrt mono-ask, extract `files/` to sdk-sources/ | ✅ 228 files, 5.7 MB |
| P0.2 | Curate patches: import 720-725 from cvandesande, keep 005-006, drop 002/004/0401-0404 | ✅ 5 active patches |
| P0.3 | `stage-kernel.sh --flavor ask` — overlay SDK + apply patches | ✅ 8 patches applied cleanly |
| P0.4 | `make -j$(nproc) ARCH=arm64 Image` — native ARM64 build | ✅ 39 MB Image produced |
| P0.5 | Deploy kernel to lxc200 TFTP, test boot on DUT | ⬜ Pending |

### Phase 1: Kernel Test on DUT (next)

| Step | Action |
|------|--------|
| P1.1 | TFTP boot DUT with built kernel + mono-gw.dtb + initrd.img |
| P1.2 | Verify SDK DPAA1 ports probe (sdk_dpaa, sdk_fman, fsl_dpa) |
| P1.3 | Verify networking works (all 5 eth ports, DHCP on eth0) |
| P1.4 | Check dmesg for ASK hooks (conntrack metadata, bridge handoff) |

### Phase 2: ASK Modules (cdx.ko + fci.ko)

| Step | Action |
|------|--------|
| P2.1 | Extract cdx.ko + fci.ko source from cvandesande build into `sources/cdx/` and `sources/fci/` |
| P2.2 | Build OOT modules against staged kernel tree |
| P2.3 | Package cdx.ko + fci.ko as .deb for VyOS ISO |
| P2.4 | Create VyOS init scripts: ask-cdx.service (START=18), ask-fci.service (START=53) |

### Phase 3: ASK Userspace (cmm + dpa_app + fmc)

| Step | Action |
|------|--------|
| P3.1 | Extract cmm + dpa_app + fmc source from cvandesande build |
| P3.2 | Build userspace binaries with VyOS dependencies |
| P3.3 | Package userspace .debs |
| P3.4 | Create ask-cmm.service (START=54), dpa_app call_usermodehelper flow |
| P3.5 | Deploy cdx_pcd.xml + cdx_cfg.xml + cdx_sp.xml to /etc/ |

### Phase 4: Bridge Offload Verification

| Step | Action |
|------|--------|
| P4.1 | Configure br-lan bridge (eth0+eth1+eth2) via VyOS CLI |
| P4.2 | Load cdx.ko → fci.ko → start cmm |
| P4.3 | Verify dpa_app successful (T+11s in dmesg) |
| P4.4 | Generate cross-port traffic (ping, iperf3 between bridge ports) |
| P4.5 | Verify PCD counters non-zero → bridge offload confirmed |

### Phase 5: Conntrack Offload (future)

| Step | Action |
|------|--------|
| P5.1 | Fix ctnetlink event generation (new>0) |
| P5.2 | Fix CMM nfct_open() to subscribe with groups=0x07 |
| P5.3 | Verify per-flow PCD counter increments with iperf3

## 5. Files to Create/Modify

### 5.1 New Files

```
kernel/flavors/ask/sources/cdx/          # CVAN cdx.ko source (with fp_netfilter)
kernel/flavors/ask/sources/fci/          # CVAN fci.ko source
kernel/flavors/ask/userspace/cmm/        # CVAN cmm source
kernel/flavors/ask/userspace/dpa_app/    # CVAN dpa_app source
kernel/flavors/ask/userspace/fmc/        # CVAN fmc source
kernel/flavors/ask/config/cdx_pcd.xml    # 18,172 bytes (from CVAN)
kernel/flavors/ask/config/cdx_cfg.xml    # 962 bytes (from CVAN)
kernel/flavors/ask/config/cdx_sp.xml     # 7,252 bytes (from CVAN)
board/systemd/ask-cdx.service            # CDX module loader (START=18)
board/systemd/ask-fci.service            # FCI module loader (START=53)
board/systemd/ask-cmm.service            # CMM daemon loader (START=54)
board/systemd/ask-dpa-app.service        # dpa_app boot-time programmer
board/vyos-config/ask-fastforward.conf   # CMM offload exclusion
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

### 5.3 Files Already Removed (2026-06-28)

```
kernel/flavors/ask/oot-modules/       # ask.ko scaffold (moved to ask20 branch)
kernel/flavors/ask/userspace/askd/    # askd scaffold (moved to ask20 branch)
kernel/flavors/ask/patches/0401-0404-*.patch  # Failed conntrack experiments
board/systemd/ask-ct-setup.service    # Old conntrack fix service
board/scripts/vyos-ask-ct-fix         # Old conntrack fix script
specs/ask2-rewrite-spec.md           # ASK2 spec (moved to ask20 branch)

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

**[FACT]** The bridge offload path is proven on real hardware (CVAN 25.12.4). The conntrack path requires fixing ctnetlink event generation — a single-stage fix. All supporting infrastructure (FMan PCD API patches, build scripts, diagnostic tools) is already in the nxp-sdk branch. The ASK2 rewrite (`ask.ko` single in-tree module) is a separate effort on the `ask20` branch.
