# NXP ASK 6.12 Integration Plan for VyOS

**Version 2.0 · HADS 1.0.0**

## AI READING INSTRUCTION

This is a reference implementation plan based on 6 verified NXP ASK sources.
Read `plans/NXP-SDK-ASK-INTEGRATION.md` first for M0-M5 context, then this
document for the corrected approach. The architectural decisions in §5
override the original plan's assumptions.

## 1. Source Catalog

**[SPEC]** Six repositories/branches were cataloged as reference implementations:

| Source | Role | Status |
|--------|------|--------|
| `we-are-mono/ASK` (main) | Root ASK repo: cdx, fci, auto_bridge, cmm, dpa_app | Authoritative |
| `we-are-mono/ASK` (mt-6.12.y) | 6.12 refresh: Kbuild/Kconfig integration, ucode FM version check | 1 commit ahead |
| `we-are-mono/ASK` (mono-patched-openwrt) | OpenWRT feed source: versioned directories, musl compat | Standalone |
| `we-are-mono/ASK` (fix/security-hardening) | Security audit: 83 fixes, split patches (010-098), Yocto recipes, test suite | Latest |
| `we-are-mono/OpenWRT-ASK` | Full OpenWRT build: kernel patches 950+951, SDK DTB, kmod packages | **Working board** |
| `we-are-mono/opnsense-deps` | FreeBSD/OPNsense port: 13 kernel modules, Linux compat shims, ZFS image | Port reference |

**[NOTE]** OpenWRT-ASK is the only implementation **confirmed to produce a booting kernel** on the Mono Gateway DK with ASK offload active. OPNsense-deps is a FreeBSD port and provides architectural insight but uses a different OS. The remaining ASK branches are source trees — not standalone builds.

## 2. Kernel Architecture — What All Implementations Agree On

**[SPEC]** Every working NXP ASK implementation uses:

```
Kernel base:   NXP lf-6.12.49-2.2.0 (or kernel.org + 950-nxp-lsdk.patch)
ASK patch:     951-nxp-ask.patch (or equivalent 002-ask-kernel-hooks.patch)
DPAA drivers:  SDK DPAA (sdk_dpaa, sdk_fman, fsl_qbman) — NOT mainline
DTB:           mono-gateway-dk-sdk.dts with:
                 - BMan/QMan portal cell-index
                 - dpaa-bpool fsl,bpool-ethernet-cfg
                 - bootmem regions for SDK DPAA driver
                 - fman0-extended-args for CDX offline ports
                 - SDK FMan port compatible strings (dual: v3-port + 1g/10g-rx/tx)
Kernel config:  FSL_SDK_DPAA_ETH=y, CPE_FAST_PATH=y, FSL_DPAA_OFFLINE_PORTS=y
Rootfs:         ext4 (OpenWRT) or... experimentally determined for VyOS
```

**[NOTE]** OpenWRT uses `NR_CPUS=64` and `NR_CPUS=16` (different branches), while VyOS uses `NR_CPUS=4` for LS1046A. The ASK CDX module requires `DPAA_ETH_TX_QUEUES >= MAX_SCHEDULER_QUEUES = 16`. Without PFC, `DPAA_ETH_TX_QUEUES = NR_CPUS = 4` which is insufficient. OpenWRT's `NR_CPUS=64` naturally provides enough TX queues. Our fix (`CPE_FAST_PATH` branch in dpaa_eth.h setting `NR_CPUS * 4 = 16`) is correct.

## 3. Critical Differences — VyOS vs OpenWRT

**[SPEC]** Three architectural differences cause the VyOS integration to diverge:

| Aspect | OpenWRT-ASK | VyOS (target) | Impact |
|--------|------------|---------------|--------|
| **Rootfs** | ext4 (FIT image → kernel → ext4 root) | squashfs + overlay (initramfs boot) | ioremap deadlock at postcore_initcall_sync |
| **Module build** | In-kernel-tree kmod (Kconfig + Kbuild.mk) | Out-of-tree via ci-build-ask-modules.sh | Build isolation vs kernel integration |
| **Init system** | OpenWRT procd (START levels) | systemd (unit files) | Service ordering and module loading |

**[NOTE]** The ext4 vs squashfs difference is the **root cause of the soft-lockup** observed in our attempts to use the OpenWRT DTB. The `bman_init_early()` function at `postcore_initcall_sync` maps BMan CCSR via `ioremap_cache_ns()`. On ext4 (OpenWRT), this succeeds because early memory management is simpler. On VyOS's initramfs/overlay path, the ioremap conflicts with early initramfs memory reservations, producing a silent CPU soft-lockup before the serial console is initialized.

## 4. Patch Strategy

**[SPEC]** Use the `fix/security-hardening` branch's **split 16-patch set** (010-098) instead of the monolithic 002-ask-kernel-hooks.patch:

| Patch | Subsystem | Description |
|-------|-----------|-------------|
| 010 | FMan/DPAA | FMan DPAA ehash enhancements |
| 020 | Bridge | Bridge fast-path hooks |
| 030 | IPv4/IPv6 | Forwarding path hooks |
| 040 | XFRM/IPsec | IPsec offload (xfrm_input/output hooks) |
| 050 | Netfilter | Conntrack offload (comcerto_fp, qosmark) |
| 060 | Netfilter | Netfilter QoS mark extensions |
| 070 | PPP | PPP hooks |
| 080 | Wireless | WEXT ndo_do_ioctl restore |
| 090-098 | QBMan/DPAA | KASAN sanitizer fixes, mutex annotations, slab fixes |

**[NOTE]** The split patches are individually smaller (each <100KB except 010 which is ~200KB), debuggable in isolation, and can be selectively applied. The monolithic 002 (17,900 lines) always falls back to git `--3way` merge against the NXP tree — producing non-deterministic results.

**[NOTE]** The 950-nxp-lsdk.patch (7.9MB) is NOT needed because our base is the NXP vendor tree (lf-6.12.49-2.2.0) which already has all SDK drivers in-tree. 950 exists to add SDK drivers TO the kernel.org tree before applying 951.

## 5. DTB Strategy

**[SPEC]** Two DTB options based on the ioremap deadlock behavior:

**Option A (safe):** Minimal DTB additions — portal cell-index + bpool-ethernet-cfg only. Omit bootmem and fman0-extended-args. This is what we attempted and should produce a booting kernel (same as the first build, plus QBMan portal init).

**Option B (full):** Use OpenWRT's mono-gateway-dk-sdk.dts with ALL additions (bootmem, extended-args, dual FMan compatibles). This is the proven DTB from OpenWRT. If the ioremap deadlock occurs, fix it at the kernel level:
- Patch `bman_init_early()` + `qman_init_early()` to use `device_initcall` instead of `postcore_initcall_sync` (defers HW init until after initramfs setup)
- This is what our patch 003 did — but it caused vmlinux link failures because of missing EXPORT_SYMBOL in the xfrm hooks

**[NOTE]** Option B is architecturally correct — it's what OpenWRT uses. The ioremap deadlock fix (initcall deferral) is a small, well-defined change. The vmlinux link failure was a separate bug (missing EXPORT_SYMBOL on `dpaa_submit_inb_pkt_to_SEC`) that needs its own fix.

## 6. Implementation Plan (Revised M1-M5)

### M1: Kernel Tree (no change from current)

**[SPEC]** Stage NXP kernel tree via `stage-kernel.sh --flavor ask`. Overlay SDK sources. Build kernel.

### M2: Kernel Patches (replace 002 with split patches)

**[SPEC]**
1. Fetch 010-098 patches from `we-are-mono/ASK` `fix/security-hardening` branch
2. Replace `kernel/flavors/ask/patches/002-ask-kernel-hooks.patch`
3. Add `Kbuild.mk` + `Kconfig` from `mt-6.12.y` for in-kernel-tree ASK module build (CONFIG_ASK_CDX/FCI/AUTO_BRIDGE)
4. Verify all patches apply cleanly against NXP tree (`git apply --check` each)
5. Handle the `postcore_initcall_sync` → `device_initcall` deferral as a patch (if using Option B DTB)

### M3: DTB Integration

**[SPEC]**
1. Start with Option A (minimal: cell-index + bpool-ethernet-cfg in mono-gateway-dk.dts)
2. Build and boot on board
3. If QBMan portals initialize and ethernet appears → Option A is sufficient
4. If QBMan still fails → Option B (full SDK DTB + initcall deferral patch)

### M4: Module Build (in-kernel-tree)

**[SPEC]**
1. Use `Kbuild.mk` + `Kconfig` to build cdx.ko, fci.ko, auto_bridge.ko as in-kernel-tree modules (CONFIG_ASK_CDX=m, etc.)
2. The modules are built during `make modules` (inside bindeb-pkg)
3. Remove the separate `ci-build-ask-modules.sh` — modules are produced as part of the kernel build
4. Package the modules as part of the kernel .debs or as separate kmod .debs

**[NOTE]** Building ASK modules in-kernel-tree eliminates the OOT build complexity and ensures Module.symvers consistency. The mt-6.12.y branch already has Kbuild.mk + Kconfig for exactly this purpose.

### M5: Userspace Build (no change from current — keep ci-build-ask-userspace.sh)

**[SPEC]**
1. Keep current userspace build script — it works correctly
2. Build cmm, dpa_app, fmc from `we-are-mono/ASK` at `mt-6.12.y`
3. Package as `ask-userspace-<KVER>_arm64.deb`

### M6: ISO Packaging (no change from current — adjustments per M4)

### M7: Board Validation

**[SPEC]**
1. Verify kernel boots with console output (no soft-lockup)
2. Verify QBMan portals initialize (no "No BMan portals available!")
3. Verify ethernet interfaces appear
4. Verify `cdx.ko` loads (cdx_module_init → start_dpa_app successful)
5. Verify `/dev/cdx_ctrl` appears
6. Verify `cmm.service` starts
7. Verify conntrack offload (check `/proc/cdx/entries`)

## 7. Known Bugs to Fix (from our CI debugging)

**[BUG] fetch_state_write exit code 10**
Symptom: stage-kernel.sh exits 10 after kernel extraction.
Cause: fetch_state_write returns 10 on "new" state, which propagates through exit "$STATE_RC".
Fix: Always exit 0 from fetch-kernel-nxp.sh. (Already fixed in our branch.)

**[BUG] certs/x509.genkey missing**
Symptom: openssl fails with "no such file: certs/x509.genkey".
Cause: The file is auto-generated by make, not present in pristine tree.
Fix: Create certs/x509.genkey before calling openssl. (Already fixed in our branch.)

**[BUG] dpaa_submit_inb_pkt_to_SEC undefined at vmlinux link**
Symptom: vmlinux link fails with "undefined reference to dpaa_submit_inb_pkt_to_SEC".
Cause: The ASK patch adds extern declaration + call sites in net/xfrm/xfrm_input.c but does NOT export the symbol from dpaa_eth_sg.c where it's defined. The function is in a separate .o file (dpaa_eth_sg.o) from xfrm_input.o.
Fix: Add EXPORT_SYMBOL(dpaa_submit_inb_pkt_to_SEC) to dpaa_eth_sg.c. (In our patch set, not yet applied.)

**[BUG] CONFIG_IMX_MBOX causes module link failure**
Symptom: pm_system_irq_wakeup undefined in imx-mailbox.ko.
Cause: NXP 6.12 kernel doesn't export this symbol; not needed on LS1046A.
Fix: # CONFIG_IMX_MBOX is not set in ask.config. (Already fixed.)

**[BUG] CONFIG_XEN/CONFIG_VHOST build failure**
Symptom: vhost/xen.c fails to compile on NXP 6.12.
Cause: vhost struct members differ from what xen.c expects.
Fix: # CONFIG_XEN is not set, # CONFIG_VHOST is not set in ask.config. (Already fixed.)

**[BUG] dpa_app installed to /usr/sbin instead of /usr/bin**
Symptom: cdx_module_init::start_dpa_app failed rc -2 (ENOENT).
Cause: cdx_main.c start_dpa_app() hardcodes path "/usr/bin/dpa_app".
Fix: Install dpa_app to /usr/bin in ci-build-ask-userspace.sh. (Already fixed.)

## 8. Current Branch State

**[SPEC]** The `nxp-sdk` branch at commit d1a26d5d has:
- ✅ M0: Branch infrastructure, kernel staging
- ✅ M1: NXP kernel tree with SDK drivers, ASK patch 002 applied
- ✅ M2: OOT module build script (ci-build-ask-modules.sh) — to be replaced per §6 M4
- ✅ M3: Userspace build script (ci-build-ask-userspace.sh)
- ✅ M4: ISO packaging pipeline
- ❌ M5: Board validation — blocked by DTB/initcall issues
- ✅ Pipeline fixes: fetch_state_write, certs/x509.genkey, IMX_MBOX, XEN/VHOST
- ⚠️ DTB: portal cell-index + bpool-ethernet-cfg in mono-gateway-dk.dts (Option A, not yet tested on board)
- ❌ vmlinux link: EXPORT_SYMBOL(dpaa_submit_inb_pkt_to_SEC) not yet applied (fails when building with initcall deferral)

**[NOTE]** The CI pipeline itself has been broken by the reset—force-push cycle. The `certs/x509.genkey` fix needs to be in the branch for the build to succeed past the stage-kernel step. The last successful CI build that produced a bootable ISO was run 27923606673 (commit 5868a69).

## 9. Recommended Next Actions

**[SPEC]**
1. Fix CI pipeline: ensure certs/x509.genkey + fetch_state_write fixes are on branch
2. Verify CI build succeeds with the minimal DTB (Option A)
3. Deploy ISO to board, boot, verify QBMan portals initialize
4. If QBMan works → proceed to M7 board validation
5. If QBMan fails → move to Option B (full SDK DTB + initcall deferral + EXPORT_SYMBOL fix)
