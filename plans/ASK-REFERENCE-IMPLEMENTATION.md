# VyOS LS1046A — NXP ASK Reference Implementation Plan
**Version 1.0.0 · HADS 1.0.0 · 2026-06-22**

## AI READING INSTRUCTION

`[SPEC]` blocks are verifiable facts and API contracts. `[NOTE]` blocks are rationale and narrative. `[BUG]` blocks are known defects with symptom+cause+fix. `[?]` marks unverified or inferred claims. This document is the source-of-truth for the VyOS NXP ASK reference implementation — superseding earlier ASK2-only planning where it differs on the legacy-deployment track.

---

## 0. Executive Summary

This plan defines a **two-track approach** to bring NXP ASK hardware offloading to VyOS on the Mono Gateway DK (LS1046A):

- **Track A (Legacy — ship first):** Build, package, and deploy the proven legacy NXP ASK stack (`cdx.ko` + `fci.ko` + `auto_bridge.ko` + `cmm` + `dpa_app`) on the **SDK kernel** (`patches/kernel/002-mono-gateway-ask-kernel_linux_6_12.patch`). This is a working, tested stack that has been deployed on OpenWRT and OPNsense. It uses the vendored NXP SDK DPAA1/FMan/QMan/BMan drivers (not mainline). ~6 weeks to first deployable ISO.

- **Track B (Modern — follow-on):** Continue executing the ASK2 rewrite (`specs/ask2-rewrite-spec.md`) targeting the mainline kernel with `ask.ko` as a clean, YNL-driven, kernel-only OOT module. Blocked on M2 (FE-VM working-store context, lf-5.4-only code). ~5 months to production.

Tracks A and B share the same FMan microcode (QEF 210.10.1), the same board, and the same physical CAAM/QMan/BMan hardware. They differ in their kernel driver substrate.

---

## 1. Source Catalog — What We Have

### 1.1 Primary Sources (all reviewed 2026-06-22)

| Source | Branch/Version | LOC | Status | Key Artifacts |
|--------|---------------|-----|--------|---------------|
| `we-are-mono/ASK` | `master` (31 commits) | ~120k | **Original reference** | 3 kmods + 3 userspace + 9 patch types |
| `we-are-mono/ASK` | `mt-6.12.y` (59 commits) | ~120k | **Kernel 6.12 refresh** | +Kconfig/Kbuild.mk in-tree integration, +iptables-extensions |
| `we-are-mono/ASK` | `mono-patched-openwrt` (16 commits) | ~120k | OpenWRT packaging variant | Versioned dirs: cdx-5.03.1, fci-9.00.12, cmm-17.03.1 |
| `we-are-mono/ASK` | `fix/security-hardening` (157 commits) | ~130k | **Most mature** | +meta-ask (Yocto harness), +tools/ (test suite), +ISSUES.md (25 architectural themes fixed) |
| `we-are-mono/OpenWRT-ASK` | `mono-25.12.0-rc2` (66k commits) | ~full OS | **Working integration** | ASK packaged as OpenWRT kernel packages (`ask-cdx`, `ask-fci`, `ask-auto-bridge`) |
| `we-are-mono/opnsense-deps` | `master` (72 commits) | ~15k | **FreeBSD port** | +ARCHITECTURE.md, +CMM.md, +pf_notify.ko (PF firewall bridge) |

### 1.2 Component Breakdown

```
NXP ASK Offload Stack
├── Kernel Modules (OOT, ~48 KLOC)
│   ├── cdx.ko          (44,900 lines)  Core flow offload engine — hardware flow tables, IPsec, QoS, NAT
│   ├── fci.ko          (1,374 lines)   Fast Control Interface — netlink IPC between CMM daemon and kernel FPP
│   └── auto_bridge.ko  (1,831 lines)   L2 bridge flow detection — netfilter hook + netlink broadcast
├── Userspace (~43 KLOC)
│   ├── cmm             (42,759 lines)  Connection Manager daemon — monitors conntrack, offloads flows to FPP
│   ├── dpa_app         PCD config loader — parses XML, programs FMan via fmc ioctl
│   └── fmc             FMan Config tool — XML→hardware compiler (links fmlib + libxml2)
├── Kernel Patches (~44 KLOC)
│   ├── 002-…-6_12.patch (17,900 lines, 138 files)  SDK DPAA1/FMan/QMan/BMan driver tree + ASK hooks
│   └── 999-…-5_4.patch  (25,798 lines, 162 files)  Foundation LSDK port (DTS, CAAM, xfrm, bridge accel)
├── Library Patches
│   ├── fmlib: FM timestamp APIs, shared KeyGen scheme param, hash table reassembly fields
│   ├── fmc: portid, shared schemes, CC/HT node replication, libxml2 compat
│   ├── libnfnetlink 1.0.2: non-blocking mode, heap buffer instead of stack VLA
│   ├── libnetfilter-conntrack 1.1.0/1.1.1: 12 Comcerto FP attributes, QOSCONNMARK, nfct_clear()
│   ├── ppp: ifindex arg to ip-up/ip-down scripts
│   └── rp-pppoe: CMM IPC for PPPoE relay offload, PID file, SIGTERM cleanup
├── iptables Extensions (mt-6.12.y only)
│   ├── libxt_QOSMARK.so: 64-bit qosmark target (set/and/or/xor)
│   ├── libxt_QOSCONNMARK.so: 64-bit qosconnmark target (set/save/restore + connection tracking)
│   ├── libxt_qosmark.so: 64-bit qosmark match
│   └── libxt_qosconnmark.so: 64-bit qosconnmark match
└── Config
    ├── Kconfig: menuconfig NXP_ASK → ASK_CDX/ASK_FCI/ASK_AUTO_BRIDGE
    ├── Kbuild.mk: obj-$(CONFIG_ASK_CDX) += cdx/ etc.
    ├── config/kernel/defconfig: Full LS1046A + ASK kernel config
    ├── config/gateway-dk/cdx_cfg.xml: FMan port mapping for Mono Gateway DK
    ├── config/cmm.service: systemd unit (guarded by /dev/cdx_ctrl)
    └── dpa_app/files/etc/cdx_pcd.xml: Packet classification rules for FMan hash tables
```

### 1.3 Data Flow (Legacy Architecture)

```
Wire → FMan RX Port
  → Parser (extract L3/L4 headers)
  → KeyGen hash (5-tuple)
  → CC Hash Table lookup
    ├─ HIT → CDX opcodes (NAT/TTL/cksum/MAC rewrite) → FMan TX Port → Wire (LINE RATE, zero CPU)
    └─ MISS → Default RX FQID → Linux stack → conntrack → CMM offloads → FPP → 210 ucode inserts flow
```

**Control plane loop:** Linux conntrack (netfilter) → CMM daemon monitors via netlink → CMM resolves routes/neighbors/SA → CMM→FCI→CDX→FMan ucode programs hardware flow → subsequent packets hit in silicon.

**IPC channels:**
- CMM → CDX: FCI ioctl (chardev `/dev/cdx_ctrl`)
- CDX → CMM: FCI netlink multicast (`NETLINK_FF`)
- conntrack → CMM: rtnetlink (`NETLINK_NFLOG` + `NETLINK_ROUTE`)
- ABM → CMM: netlink broadcast (`NETLINK_L2FLOW=33`)
- dpa_app → CDX: `/dev/cdx_ctrl` ioctl `CDX_CTRL_DPA_SET_PARAMS`
- dpa_app → FMan: `/dev/fman` chardev ioctl (PCD XML → hardware)
- CMM → IPsec: `NETLINK_KEY` (xfrm SA add/del/expire monitoring)

---

## 2. Track A — Legacy NXP ASK on VyOS (Ship First)

**[SPEC]** Track A deploys the proven `we-are-mono/ASK` `fix/security-hardening` stack on a VyOS LS1046A image. The kernel is the **SDK kernel** (not mainline DPAA1) — it carries the vendored `sdk_dpaa`, `sdk_fman`, and `fsl_qbman` drivers from the NXP Layerscape SDK. This is the same kernel used by OpenWRT-ASK and the same driver tree used by the security-hardening test suite.

### 2.1 What Track A delivers

- Hardware-accelerated IPv4/IPv6 forwarding (5-tuple flows in FMan silicon)
- Hardware NAT (SNAT/DNAT/PAT via FMan header manipulation opcodes)
- Hardware L2 bridge offload (auto_bridge detects flows, CDX programs FMan)
- IPsec offload via CAAM SEC 5.4 (AES-CBC+HMAC-SHA256, AES-CTR at 2.58 Gbps; GCM refused per A24)
- QoS (CEETM egress shaping, WBFQ, DSCP→FQID mapping, ingress policing)
- PPPoE session/relay offload with hardware acceleration
- Tunnel offload (4o6, 6o4, IPIP, GRE)
- Multicast offload (IPv4 IGMP + IPv6 MLDv2)
- RTP/RTCP relay with hardware packet manipulation
- Per-flow statistics via MURAM counters

### 2.2 Build Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Step 1: Stage SDK Kernel Source                                             │
│   - Checkout linux-6.12.y (upstream LTS)                                    │
│   - Apply patches/kernel/002-mono-gateway-ask-kernel_linux_6_12.patch        │
│     → 138 files: sdk_dpaa/ sdk_fman/ fsl_qbman/ + bridge/ppp/xfrm hooks    │
│   - Apply kernel/common/patches/board/* (mainline-style: DTS, SFP, INA234,  │
│     FMan PCD 0092-0100, DPAA1 AF_XDP, policer, CAAM, watchdog, etc.)        │
│   - Merge kernel config: defconfig + ls1046a-*.config + vyos-base/*        │
│     + CONFIG_NXP_ASK=y CONFIG_ASK_CDX=y CONFIG_ASK_FCI=y                    │
│     CONFIG_ASK_AUTO_BRIDGE=y CONFIG_FSL_SDK_DPAA_ETH=y                       │
│     CONFIG_FSL_SDK_FMAN=y CONFIG_FSL_QBMAN=y                                  │
│   - Build kernel with bindeb-pkg → linux-image-*.deb                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ Step 2: Build OOT Kernel Modules                                            │
│   - Clone we-are-mono/ASK at fix/security-hardening (or mt-6.12.y)          │
│   - Build cdx.ko against staged kernel (LOCALVERSION=-vyos)                 │
│   - Build fci.ko (depends on cdx/Module.symvers)                            │
│   - Build auto_bridge.ko                                                    │
│   - Sign all modules with in-tree signing key (MODULE_SIG_FORCE=y)          │
│   - Package as vyos-ls1046a-ask-modules_*.deb                               │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ Step 3: Build Userspace Dependencies                                        │
│   - Clone NXP fmlib + fmc (tag: lf-6.12.49-2.2.0)                          │
│   - Apply ASK extension patches                                             │
│   - Cross-compile libfm-arm.a (static) + fmc binary + dpa_app binary        │
│   - Build libfci (static, single .c file)                                   │
│   - Fetch + patch + cross-compile:                                          │
│     libnfnetlink 1.0.2 (non-blocking + heap buffer)                         │
│     libnetfilter-conntrack 1.1.1 (Comcerto FP extensions)                   │
│   - Build cmm daemon → links libfci + libnfnetlink + libnetfilter-conntrack │
│   - Build cmmctl binary (command-line control tool)                         │
│   - Build iptables extensions: libxt_QOSMARK.so, libxt_QOSCONNMARK.so, etc. │
│   - Package as vyos-ls1046a-ask-userspace_*.deb                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ Step 4: ISO Assembly                                                        │
│   - Standard vyos-build live-build pipeline                                 │
│   - hooks/97-ask-modules.chroot: copies .ko files → /lib/modules/.../extra │
│   - hooks/98-ask-userspace.chroot: copies cmm, dpa_app, fmc, cmmctl,        │
│     libfci.so, iptables extensions → /usr/local/{sbin,lib,lib/xtables}      │
│   - hooks/99-ask-config.chroot: copies cdx_cfg.xml, cdx_pcd.xml,            │
│     cdx_sp.xml, hxs_pdl_v3.xml → /etc/cdx/                                 │
│   - board/scripts/ask-activate.sh: runtime engage script                    │
│     (modprobe cdx fci auto_bridge → dpa_app → cmm start)                     │
│   - board/systemd/ask.service: one-shot that calls ask-activate.sh          │
│     (enabled in multi-user.target.wants when offload is configured)         │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.3 VyOS CLI Integration (Minimal v1)

```
set system offload ask
# This is the global enable. Commits ask.ko loading + dpa_app + cmm.

show offload ask flows
show offload ask stats
show offload ask muram
show offload ask info

# Advanced (wraps cmmctl):
show offload ask qos
show offload ask bridge
show offload ask tunnel
show offload ask ipsec
```

**Implementation:**
- `vyos-1x` conf_mode script `system_offload_ask.py`:
  - Commit: modprobe cdx fci auto_bridge → `/usr/local/sbin/dpa_app /etc/cdx/cdx_pcd.xml /etc/cdx/cdx_cfg.xml /etc/cdx/cdx_sp.xml` → `systemctl start cmm`
  - Delete: `systemctl stop cmm` → `rmmod auto_bridge fci cdx`
- `vyos-1x` op_mode script `offload_ask.py`: wraps `cmmctl` and `cat /proc/fqid_stats/*`
- Validator: mutual exclusion with `set vpp settings` (same §3.2 rule as dual-dataplane spec)

### 2.4 Security Audit Application

**[SPEC]** The `fix/security-hardening` branch's ISSUES.md contains 44 issues (2 gating, 9 critical, 10 high, 15 medium, 8 low), **all fixed with commit hashes**. We must apply the security-hardening branch as-is (not the master branch) to get these fixes. The critical fixes include:

- **G1:** CAP_NET_ADMIN gate on `/dev/cdx_ctrl` ioctl (commit `815a0ca`)
- **C1:** nla_len bounded in auto_bridge netlink handler (commit `815a0ca`)
- **C2:** FCI message length validation (commit `815a0ca` + `0a8a5f6`)
- **C3:** Reassembly num_entries bounded against pool size (commit `815a0ca`)
- **C5:** IP reassembly deinit properly stops kthread + frees FQs (commits `815a0ca` + `b5a7bf8` + `78ac2af`)
- **A1a–A1e:** Validator-table pattern applied to all 14 control_*.c files (12 commits)
- **A2:** Concurrency documentation + `__must_hold()` annotations (commit `d99bb62`)
- **A3a–A3e:** Error path unwind cascades across init paths (4 commits)
- **A23:** IPsec buffer pool seeded with actual buffers (was BPDERR silent fail)
- **A25:** AES-128-CTR support (missing nonce trim + CTR PDB writes)
- **A24:** GCM refused at SA install (cross-DECO seq-number dupes)

### 2.5 Risk Assessment — Track A

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| SDK kernel diverges from mainline DPAA1 | Certain | Medium | SDK + mainline DPAA1 are Kconfig-exclusive; they never load simultaneously. Track A kernel = SDK-only FMan path. |
| Kernel version lock (6.12.x LTS) | Medium | Low | 6.12 is supported until Dec 2026; Track B aims to replace Track A within that window |
| 210 ucode availability | Resolved | — | Already on our SPI flash; no redistribution |
| MURAM exhaustion (same PR14z21 wall) | High | Medium | The vendor XML (`cdx_pcd.xml`) asks for 16×512-key tables; we will ship a trimmed config with bounded cardinality |
| CAAM GCM wire-sequence-number dupes (A24) | Certain | Low | Refused at SA install; falls through to kernel software GCM |
| `ipsec_bp` BPDERR (A23 fix missing from master) | High | High | Must use fix/security-hardening branch which has this fix |
| .ko signing with `MODULE_SIG_FORCE=y` | Certain | Low | Sign with in-tree key as part of build pipeline |

### 2.6 Track A Timeline

| Week | Deliverable |
|------|-------------|
| 1 | Clone + patch SDK kernel; build + boot on board; verify netdevs |
| 2 | Build cdx+fci+auto_bridge OOT; sign; insmod test |
| 3 | Build userspace (fmlib→fmc→dpa_app→cmm); test cmm starts |
| 4 | Integrate into VyOS build pipeline; produce first ASK-capable ISO |
| 5 | VyOS CLI (conf_mode + op_mode); mutual-exclusion validator |
| 6 | Board soak: boot→engage→iperf3→disengage→VPP→reboot×10 |

---

## 3. Track B — ASK2 Modern Rewrite (Follow-On)

**[SPEC]** Track B continues executing `specs/ask2-rewrite-spec.md` (v1.7) and `specs/dual-dataplane.md` (v1.1), targeting a kernel-only, YNL-driven architecture on the **mainline** DPAA1 kernel. The key advantage: one kernel image supports both ASK and VPP without a full kernel rebuild.

### 3.1 Current Status (2026-06-22)

| Milestone | Status | Blockers |
|-----------|--------|----------|
| **M0** — Vendor oracle | **SATISFIED** | `arch/fman-fe-ehash.md` complete. Board PCD 0092/0097-0100/0104/0116/0117 merged |
| **M1** — Reversible mode switch | **HW-PROVEN** | 100× S0↔S1 soak clean (control-plane only). `pcd-snapshot` tool CI-green |
| **M2** — HW classification | **BLOCKED** | FE-VM working-store context (`FmPcdCcBuildContextByFE`) is lf-5.4-only stub. Fork B scaffold (`0122`–`0133`) builds on board but parks under traffic (VERDICT D). |
| **M3** — HW forwarding | **DEPENDS on M2** | FORWARD_FQ_WITH_MANIP inline CC-action path opens once classification flows |
| **M4** — HW NAT | Blocked on M2 | Field-update manips in the forward chain |
| **M5** — HW IPsec | **Parallel** | CAAM QI descriptor sharing (PR10 landed). xfrmdev_ops packet-mode offload. |
| **M6** — HW frag/reasm | Blocked on M2 | FMan reassembly contexts |
| **M7** — Productization | Future | VyOS CLI + soak |
| **M8** — Per-port coexistence | Stretch | ASK + VPP simultaneously on different ports |

### 3.2 The M2 Blocker — Source-Availability Wall

**[BUG] FE-VM working-store context (`FmPcdCcBuildContextByFE`)**
- **Symptom:** Fork B FE-VM scaffold (`0122`–`0133`) builds and arms on board, but the port parks under traffic with zero fault latched (`fmdmsr=0`, `fmfp_ee` unchanged, `decceh=0`). Identical to iter-26–50 bare exact-match stall.
- **Cause:** The FE VM's MUX singleton reads its next-FE pointer from a per-task working-store context that `FmPcdCcBuildContextByFE` populates. That function is a **stub in lf-6.6.y archive and lf-6.12.49 mono port** — the real body exists **only** in the lf-5.4 LSDK (`we-are-mono/ASK` `999-…patch`, `FmPcdCcBuildContextByFE` at L8954).
- **Fix options:**
  1. Extract the working-store population body from lf-5.4 LSDK (genuine NXP source, GPL-2.0) — **preferred**, but requires understanding the working-store layout
  2. Instrument the working-store population iteratively on silicon (slice-2 approach: populate fields one at a time until the port flows)
  3. Wait for Track A deployment to mature — the legacy CDX already implements this on the SDK kernel, giving us a live reference

### 3.3 What Track B can do in parallel with Track A

| Work Item | Can run now? | Effort |
|-----------|-------------|--------|
| ask.ko skeleton (YNL family + genl ops) | Yes — no hardware needed | 5 eng-days |
| CAAM descriptor sharing (xfrmdev_ops) | Yes — PR10 landed | 3 eng-days |
| dpaa_flavor_ops integration (pcd_ops->install) | Yes — PR11/PR12 landed | 1 eng-day |
| VyOS CLI for ASK2 (set system offload ask + YNL op-mode) | Yes — spec exists | 2 eng-days |
| M2 classification (FE-VM working-store) | No — blocked | Unknown |
| M3 forwarding (FORWARD_FQ_WITH_MANIP) | Blocked on M2 | ~5 eng-days |
| M4 NAT (manip chains) | Blocked on M2 | ~3 eng-days |

---

## 4. Hybrid Mode — Track A API → Track B Migration

**[NOTE]** Once Track B M2 is unblocked, we migrate from Track A (SDK kernel + legacy kmods) to Track B (mainline kernel + ask.ko) while preserving the operator-facing CLI. The migration path:

```
Track A                        Track B
─────────────────────────      ─────────────────────────
cdx.ko   (flow engine)    →    ask.ko (flow_block + xfrmdev_ops + YNL)
fci.ko   (IPC channel)    →    DELETED (YNL genl family replaces it)
auto_bridge.ko            →    ask_bridge.ko (switchdev, not netfilter)
cmm      (daemon, 42k)    →    DELETED (kernel nf_flow_table replaces it)
dpa_app  (XML→FMan)       →    DELETED (Path A pcd_ops->install replaces it)
fmc      (XML parser)     →    DELETED (no XML config in ASK2)
libfci   (ABI shim)       →    DELETED (FORBIDDEN per spec §19)
```

Operator CLI unchanged:
```
set system offload ask          # Works on both Track A and Track B
show offload ask flows          # Track A: wraps cmmctl; Track B: wraps ynl
show offload ask stats          # Same
```

Migration trigger: single commit that replaces the Track A kernel .deb with the Track B kernel .deb, drops `cdx.ko`/`fci.ko`/`auto_bridge.ko`/`cmm`/`dpa_app`/`fmc` packages, and ships `ask.ko`/`ask_bridge.ko` instead. No config change needed on the board.

---

## 5. VyOS Build Integration (Common to Both Tracks)

### 5.1 Repository Layout Additions

```
vyos-ls1046a-build/
├── kernel/
│   ├── common/                         # existing mainline kernel
│   └── sdk/                            # NEW: SDK kernel for Track A
│       ├── patches/
│       │   ├── 002-mono-gateway-ask-kernel_linux_6_12.patch
│       │   ├── 999-layerscape-ask-kernel_linux_5_4_3_00_0.patch
│       │   └── 090-098 sanitizer fixes
│       └── config/
│           └── ask-defconfig           # SDK kernel defconfig with ASK
├── ask/                                # NEW: ASK build root
│   ├── modules/                        # Track A: cdx + fci + auto_bridge source
│   │   ├── cdx/                        # from we-are-mono/ASK@fix/security-hardening
│   │   ├── fci/
│   │   └── auto_bridge/
│   ├── userspace/                      # Track A: cmm + dpa_app + fmc + libs
│   │   ├── cmm/
│   │   ├── dpa_app/
│   │   ├── fmc/
│   │   ├── fmlib/
│   │   └── cmmctl/
│   ├── config/                         # Track A: XML + service files
│   │   ├── cdx_cfg.xml
│   │   ├── cdx_pcd.xml
│   │   ├── cdx_sp.xml
│   │   ├── hxs_pdl_v3.xml
│   │   ├── fastforward
│   │   ├── cmm.service
│   │   └── ask-activate.sh
│   └── patches/                        # Library patches (unchanged from upstream ASK)
│       ├── fmlib/
│       ├── fmc/
│       ├── libnfnetlink/
│       ├── libnetfilter-conntrack/
│       ├── ppp/
│       └── rp-pppoe/
├── data/
│   ├── vyos-1x-NNN-ask-legacy.patch    # NEW: VyOS CLI integration for Track A
│   └── hooks/
│       ├── 97-ask-modules.chroot
│       ├── 98-ask-userspace.chroot
│       └── 99-ask-config.chroot
├── board/
│   └── scripts/
│       ├── ask-activate.sh             # Engage script (Track A)
│       └── ask-check                   # Health check (both tracks)
└── bin/
    ├── ci-setup-kernel-ask.sh          # NEW: SDK kernel staging
    ├── ci-build-ask-modules.sh         # NEW: OOT module build
    ├── ci-build-ask-userspace.sh       # NEW: userspace build
    └── ci-build-iso.sh                 # modified: include ASK hooks
```

### 5.2 CI Pipeline Changes

```yaml
# auto-build.yml additions:

jobs:
  build:
    steps:
      # Existing steps (kernel, packages, ISO)...

      - name: Stage SDK Kernel (ASK Track A)
        run: bin/ci-setup-kernel-ask.sh

      - name: Build ASK OOT Modules (Track A)
        run: bin/ci-build-ask-modules.sh

      - name: Build ASK Userspace (Track A)
        run: bin/ci-build-ask-userspace.sh

      - name: Build ISO (includes ASK)
        run: bin/ci-build-iso.sh
```

### 5.3 Runtime Configuration

**[SPEC]** At runtime, the operator configures ASK through standard VyOS CLI:

```sh
# Track A (legacy):
set system offload ask
# → loads cdx+fci+auto_bridge, runs dpa_app, starts cmm
# → hardware forwarding engaged

# Track B (modern, future):
set system offload ask
# → loads ask.ko via dpaa_register_flavor_ops
# → hardware forwarding engaged via nf_flow_table + xfrmdev_ops
```

**Module load order (Track A):**
```
cdx → fci (depends on cdx/Module.symvers) → auto_bridge
```

**Service start order (Track A):**
```
modprobe cdx fci auto_bridge
→ /usr/local/sbin/dpa_app /etc/cdx/cdx_pcd.xml /etc/cdx/cdx_cfg.xml /etc/cdx/cdx_sp.xml
→ systemctl start cmm (guarded by ConditionPathExists=/dev/cdx_ctrl)
```

### 5.4 The ask-activate.sh Engage Script (Track A)

```sh
#!/bin/bash
# board/scripts/ask-activate.sh — engage the legacy NXP ASK offload stack
# Idempotent. Called by ask.service on boot, and by vyos-postinstall if
# ask is configured.

set -euo pipefail

# 1. Load kernel modules
modprobe cdx
modprobe fci
modprobe auto_bridge

# 2. Wait for /dev/cdx_ctrl to appear
for i in $(seq 1 30); do
    [ -c /dev/cdx_ctrl ] && break
    sleep 1
done

# 3. Program FMan PCD (classification pipeline)
/usr/local/sbin/dpa_app \
    /etc/cdx/cdx_pcd.xml \
    /etc/cdx/cdx_cfg.xml \
    /etc/cdx/cdx_sp.xml

# 4. Start Connection Manager
systemctl start cmm
```

---

## 6. Key Design Decisions

### 6.1 SDK Kernel vs Mainline

| Aspect | SDK Kernel (Track A) | Mainline Kernel (Track B) |
|--------|---------------------|--------------------------|
| FMan driver | sdk_fman (vendor) | fman.c (mainline) |
| DPAA driver | sdk_dpaa (vendor) | fsl_dpa (mainline) |
| QBMan driver | fsl_qbman (staging) | fsl_qbman (mainline 6.6+) |
| AF_XDP support | No | Yes (~3.5 Gbps on 10G) |
| DPDK PMD support | Yes (SDK path) | No (RC#31 bus-init kills kernel interfaces) |
| VPP coexistence | No (exclusive) | Yes (S0 shared state, §9.2 dual-dataplane) |
| Upstream alignment | Low | High |
| ASK stack | Proven (OpenWRT, OPNsense) | In development (M2 blocked) |

**Decision:** Ship Track A first (proven), continue Track B development (modern). The SDK kernel and mainline kernel are built as **separate kernel .deb packages** — the operator chooses at install time. Both kernels share the same VyOS userspace, the same DTB, and the same U-Boot.

### 6.2 Why Not Just Finish Track B First?

Track B is blocked on a source-availability wall: the FE-VM working-store context (`FmPcdCcBuildContextByFE`) exists as a complete body **only** in the ancient lf-5.4 LSDK. The lf-6.6.y archive and lf-6.12.49 mono port both stub it. 50+ clean-room iterations on silicon have not closed this gap. Track A deploys the entire working stack — including the FE-VM core — *today*, because the SDK kernel's `sdk_fman` driver tree includes the complete FE-VM implementation.

### 6.3 Licensing

All NXP ASK components are GPL-2.0+. VyOS is GPL-2.0+. The 210 microcode is a proprietary NXP binary (not redistributed — it's already on our SPI flash). No licensing conflict.

---

## 7. Risks and Mitigations

| # | Risk | Track | Mitigation |
|---|------|-------|------------|
| 1 | SDK kernel + mainline patches conflict | A | Build SDK kernel separately; apply board patches only to mainline |
| 2 | `ipsec_bp` BPDERR (A23 fix missing in master) | A | Use fix/security-hardening branch which has the fix |
| 3 | CMM conntrack monitoring unstable under VyOS firewall | A | VyOS uses nftables/conntrack; CMM monitors the same conntrack table — no conflict |
| 4 | 210 ucode incompatibility with new FMan microcode features | Both | U-Boot loads ucode from SPI; pin to 210.10.1 |
| 5 | MURAM exhaustion at scale (>750 flows) | Both | Bounded flow count; DDR-backed ehash buckets for Track B; trimmed config for Track A |
| 6 | Track A kernel diverges too far to merge back | A | Track B is the merge target; Track A is explicitly a stepping stone |
| 7 | FE-VM working-store never cracked | B | Track A is the fallback; the legacy CDX already implements this correctly on SDK kernel |

---

## 8. Acceptance Criteria

### Track A (Legacy Shippable)
- [ ] SDK kernel builds, boots on board, all 5 netdevs appear
- [ ] `modprobe cdx fci auto_bridge` loads cleanly (no -ENOENT, no unresolved symbols)
- [ ] `dpa_app` runs with rc=0 (FMan PCD programmed)
- [ ] `cmm` starts and shows zero offloaded flows (conntrack empty)
- [ ] Install a single flow (iperf3 TCP → ESTABLISHED in conntrack → cmm offloads)
- [ ] `cmmctl stat conn` shows offloaded flow
- [ ] iperf3 throughput > 8 Gbps on 10G SFP+ (hardware path)
- [ ] `systemctl stop cmm; rmmod auto_bridge fci cdx` clean
- [ ] `set system offload ask` + `set vpp settings` mutual-exclusion validator
- [ ] 24h soak: no kernel oops, no MURAM leak, CMM daemon stable

### Track B (Modern Remaining)
- [ ] M2: FE-VM working-store context populated → classification flows on board
- [ ] M3: FORWARD_FQ_WITH_MANIP inline CC-action → routed IPv4 at ≥ 2 Gbps with < 5% CPU
- [ ] M4: SNAT/DNAT field-update manips in the forward chain
- [ ] M5: AES-CBC+HMAC-SHA256 IPsec offload via CAAM QI at ≥ 3 Gbps
- [ ] M7: Full VyOS CLI + mutual-exclusion validator + ask-check diagnostics

---

## 9. Cross-References

| For... | See... |
|--------|--------|
| Full ASK2 modern architecture | `specs/ask2-rewrite-spec.md` (v1.7, 1394 lines) |
| Dual-dataplane mode switch | `specs/dual-dataplane.md` (v1.1, 227 lines) |
| FE/ehash M0 oracle | `arch/fman-fe-ehash.md` (400 lines) |
| M2 blocker details | qdrant `ASK2 D9-B iter-37 FINAL on-silicon result 2026-06-17` |
| Security audit (44 issues fixed) | `we-are-mono/ASK` `fix/security-hardening` `ISSUES.md` |
| OpenWRT ASK package integration | `we-are-mono/OpenWRT-ASK` `package/kernel/ask-cdx` |
| FreeBSD/OPNsense port architecture | `we-are-mono/opnsense-deps` `ARCHITECTURE.md` |
| cmmctl full command reference | `we-are-mono/opnsense-deps` `CMM.md` |
| Legacy ASK 1.x deletion history | This repo's `AGENTS.md` (ASK2 rewrite-in-progress section) |
