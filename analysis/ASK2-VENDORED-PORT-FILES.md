# ASK2 Vendored-Port File Inventory

**Version 1.0.1 · 2026-06-19 · HADS 1.0.0**

> **Reconciliation note (v1.0.1):** this inventory's premise — that lifting the SDK
> stack flows the selective `en_exthash` FE path — is **contingent**, not proven. Per
> qdrant iter-190/192 (see `analysis/ASK-SDK-LIFT-TO-6.18.md` §2 `[BUG]` and
> `plans/ASKS-SDK-LIFT.md` §5.2), the FE-VM builder un-stub is NOT the gate0143/gate0144
> keystone: the working mono lf-6.12 ASK ships `FmPcdCcBuildContextByFE` stubbed and still
> forwards, so the compare-key deposit is ucode-internal at node-build time. The file
> inventory below is still accurate as a *parts list*; whether the assembled stack flows
> the enhanced node is the open `[?]` settled only by the Path-B differential dump.

## AI READING INSTRUCTION

This document inventories every file that would be lifted from
`nxp-imx/linux-imx` `lf-6.12.y` to implement the coherent vendored NXP SDK stack
(Path A from `analysis/ASK2-VS-NXP-GAP-ANALYSIS.md` §6) on the VyOS mainline
6.18 kernel. Each file is classified as LIFT-ONLY (no change needed),
NEEDS-PORT (requires modification for 6.18), or SKIP (not needed on target).
`[SPEC]` paragraphs list exact file paths and line numbers. `[NOTE]` paragraphs
explain rationale.

---

## 1. Scope

**[SPEC]** Three SDK driver trees are relevant, plus their public headers:

| Tree | Path in lf-6.12.y | LOC (estimate) | Purpose |
|------|-------------------|----------------|---------|
| sdk_fman | `drivers/net/ethernet/freescale/sdk_fman/` | ~55K | FMan PCD/port/MAC/KG/CC — the coherent PCD stack |
| sdk_dpaa | `drivers/net/ethernet/freescale/sdk_dpaa/` | ~35K | DPAA Ethernet netdev + CEETM + SG |
| sdk_qbman | `drivers/staging/fsl_qbman/` | ~20K | BMan/QMan portal drivers (staging) |
| fmd uapi | `include/uapi/linux/fmd/` | ~5K | FMan ioctl ABI |
| qbman headers | `include/linux/fsl_bman.h`, `include/linux/fsl_qman.h` | ~5K | QBMan public API |

**[SPEC]** These replace the mainline equivalents entirely (Kconfig guard:
`FSL_SDK_FMAN` depends on `!FSL_FMAN`, etc.). The VyOS kernel would disable
`CONFIG_FSL_FMAN`, `CONFIG_FSL_DPAA_ETH`, `CONFIG_FSL_DPAA`, `CONFIG_FSL_BMAN`,
`CONFIG_FSL_QMAN` and enable the SDK variants.

---

## 2. sdk_fman Files (104 files)

**[NOTE]** The sdk_fman tree is the coherent PCD stack that runs
`FM_PORT_SetPCD → BuildSchemeRegs → CcRootBuild → ExternalHashTableSet → AddKey`
end-to-end. This is the core of Path A — the only mechanism proven to prime the
ucode's KeyGen-extracted-key → FE-working-store handoff on the 210.10.1 ucode.

### 2.1 LIFT-ONLY — 71 files

These are pure PCD/MAC/port library code — no kernel API surface beyond
`iowrite32be`/`ioread32be` and MURAM/DDR management. They compile against any
6.x kernel without modification.

**[SPEC]**

```
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/HC/hc.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/HC/Makefile
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/MAC/fm_mac.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/MAC/fm_mac.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/MAC/fman_crc32.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/MAC/fman_crc32.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/MAC/fman_memac.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/MAC/fman_memac_mii_acc.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/MAC/memac.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/MAC/memac.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/MAC/memac_mii_acc.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/MAC/memac_mii_acc.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/MAC/Makefile
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Makefile
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/crc64.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fm_cc.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fm_cc.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fm_kg.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fm_kg.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fm_manip.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fm_manip.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fm_pcd.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fm_pcd.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fm_pcd_ipc.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fm_plcr.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fm_plcr.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fm_prs.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fm_prs.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fm_replic.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fm_replic.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fman_kg.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fman_prs.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/Makefile
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Port/fm_port.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Port/fm_port.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Port/fm_port_im.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Port/fman_port.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Port/Makefile
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Rtc/fm_rtc.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Rtc/fm_rtc.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Rtc/fman_rtc.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Rtc/Makefile
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/SP/fm_sp.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/SP/fm_sp.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/SP/fman_sp.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/SP/Makefile
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/fm.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/fm.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/fm_ipc.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/fm_muram.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/fman.c
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/inc/fm_common.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/inc/fm_hc.h
drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/inc/fm_sp_common.h
drivers/net/ethernet/freescale/sdk_fman/etc/error.c
drivers/net/ethernet/freescale/sdk_fman/etc/list.c
drivers/net/ethernet/freescale/sdk_fman/etc/memcpy.c
drivers/net/ethernet/freescale/sdk_fman/etc/mm.c
drivers/net/ethernet/freescale/sdk_fman/etc/mm.h
drivers/net/ethernet/freescale/sdk_fman/etc/sprint.c
drivers/net/ethernet/freescale/sdk_fman/etc/Makefile
drivers/net/ethernet/freescale/sdk_fman/src/inc/system/sys_ext.h
drivers/net/ethernet/freescale/sdk_fman/src/inc/system/sys_io_ext.h
drivers/net/ethernet/freescale/sdk_fman/src/inc/types_linux.h
drivers/net/ethernet/freescale/sdk_fman/src/system/sys_io.c
drivers/net/ethernet/freescale/sdk_fman/src/system/Makefile
drivers/net/ethernet/freescale/sdk_fman/src/xx/xx_arm_linux.c
drivers/net/ethernet/freescale/sdk_fman/src/xx/module_strings.c
drivers/net/ethernet/freescale/sdk_fman/src/xx/Makefile
drivers/net/ethernet/freescale/sdk_fman/src/Makefile
```

### 2.2 LIFT-ONLY — 26 header-only files

**[SPEC]** All public API headers. No kernel API surface.

```
drivers/net/ethernet/freescale/sdk_fman/inc/Peripherals/crc_mac_addr_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/Peripherals/dpaa_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/Peripherals/fm_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/Peripherals/fm_mac_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/Peripherals/fm_muram_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/Peripherals/fm_pcd_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/Peripherals/fm_port_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/Peripherals/fm_rtc_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/Peripherals/fm_vsp_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/Peripherals/mii_acc_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/core_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/ddr_std_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/debug_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/endian_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/enet_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/error_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/etc/list_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/etc/mem_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/etc/memcpy_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/etc/mm_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/etc/sprint_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/flib/fman_common.h
drivers/net/ethernet/freescale/sdk_fman/inc/flib/fsl_enet.h
drivers/net/ethernet/freescale/sdk_fman/inc/flib/fsl_fman.h
drivers/net/ethernet/freescale/sdk_fman/inc/flib/fsl_fman_kg.h
drivers/net/ethernet/freescale/sdk_fman/inc/flib/fsl_fman_memac.h
drivers/net/ethernet/freescale/sdk_fman/inc/flib/fsl_fman_memac_mii_acc.h
drivers/net/ethernet/freescale/sdk_fman/inc/flib/fsl_fman_port.h
drivers/net/ethernet/freescale/sdk_fman/inc/flib/fsl_fman_prs.h
drivers/net/ethernet/freescale/sdk_fman/inc/flib/fsl_fman_rtc.h
drivers/net/ethernet/freescale/sdk_fman/inc/flib/fsl_fman_sp.h
drivers/net/ethernet/freescale/sdk_fman/inc/integrations/LS1043/dpaa_integration_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/integrations/LS1043/part_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/integrations/LS1043/part_integration_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/math_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/ncsw_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/net_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/std_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/stdarg_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/stdlib_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/string_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/types_ext.h
drivers/net/ethernet/freescale/sdk_fman/inc/xx_common.h
drivers/net/ethernet/freescale/sdk_fman/inc/xx_ext.h
drivers/net/ethernet/freescale/sdk_fman/src/inc/xx/xx.h
drivers/net/ethernet/freescale/sdk_fman/src/inc/wrapper/lnxwrp_exp_sym.h
drivers/net/ethernet/freescale/sdk_fman/src/inc/wrapper/lnxwrp_fm_ext.h
drivers/net/ethernet/freescale/sdk_fman/src/inc/wrapper/lnxwrp_fsl_fman.h
```

### 2.3 NEEDS-PORT — 3 files (kernel API breaks)

**[SPEC]** `src/wrapper/lnxwrp_fm.c:56` — `#include <asm/uaccess.h>` must become
`#include <linux/uaccess.h>`. Removed in 5.18.

**[SPEC]** `src/wrapper/lnxwrp_ioctls_fm_compat.c:54` — same `asm/uaccess` fix.

**[SPEC]** `src/xx/xx_arm_linux.c:69` — same `asm/uaccess` fix.

### 2.4 SKIP — 4 files

**[SPEC]** Not needed on the target (test scaffolding, KASAN unit-test helpers, build config that VyOS replaces):

```
drivers/net/ethernet/freescale/sdk_fman/ls1043_dflags.h        — build flags, VyOS uses own
drivers/net/ethernet/freescale/sdk_fman/ncsw_config.mk          — SDK build config, replaced by Kbuild
drivers/net/ethernet/freescale/sdk_fman/src/wrapper/fman_test.c  — unit test
drivers/net/ethernet/freescale/sdk_fman/src/wrapper/fsl_fman_test.h — test header
drivers/net/ethernet/freescale/sdk_fman/src/wrapper/lnxwrp_resources_ut.c   — KASAN test
drivers/net/ethernet/freescale/sdk_fman/src/wrapper/lnxwrp_resources_ut.h   — test header
drivers/net/ethernet/freescale/sdk_fman/src/wrapper/lnxwrp_resources_ut.make — test build
```

### 2.5 Kconfig + Makefile — 2 files (structural)

**[SPEC]**
```
drivers/net/ethernet/freescale/sdk_fman/Kconfig   — LIFT with parent integration
drivers/net/ethernet/freescale/sdk_fman/Makefile   — LIFT, needs Kbuild path adjustments
```

---

## 3. sdk_dpaa Files (24 files)

**[NOTE]** sdk_dpaa replaces mainline `fsl_dpa` + `fsl_dpaa_mac`. It provides
the netdev layer that handshakes with the SDK FMan PCD stack, plus the CEETM
QoS qdisc and SG (scatter-gather) Tx/Rx.

### 3.1 LIFT-ONLY — 14 files

**[SPEC]** Pure DPAA buffer management, MAC API glue, sysfs/debugfs helpers,
1588 PTP.

```
drivers/net/ethernet/freescale/sdk_dpaa/dpaa_1588.c
drivers/net/ethernet/freescale/sdk_dpaa/dpaa_1588.h
drivers/net/ethernet/freescale/sdk_dpaa/dpaa_debugfs.c
drivers/net/ethernet/freescale/sdk_dpaa/dpaa_debugfs.h
drivers/net/ethernet/freescale/sdk_dpaa/dpaa_eth_base.c
drivers/net/ethernet/freescale/sdk_dpaa/dpaa_eth_base.h
drivers/net/ethernet/freescale/sdk_dpaa/dpaa_eth_ceetm.c
drivers/net/ethernet/freescale/sdk_dpaa/dpaa_eth_ceetm.h
drivers/net/ethernet/freescale/sdk_dpaa/dpaa_eth_proxy.c
drivers/net/ethernet/freescale/sdk_dpaa/dpaa_eth_sysfs.c
drivers/net/ethernet/freescale/sdk_dpaa/dpaa_ethtool.c
drivers/net/ethernet/freescale/sdk_dpaa/dpaa_eth_trace.h
drivers/net/ethernet/freescale/sdk_dpaa/mac-api.c
drivers/net/ethernet/freescale/sdk_dpaa/offline_port.c
drivers/net/ethernet/freescale/sdk_dpaa/offline_port.h
```

### 3.2 NEEDS-PORT — 5 files

**[BUG] `dpaa_eth.c:715` — `netif_napi_add()` 2-arg form.** The 2-argument
`netif_napi_add(net_dev, &napi, poll_fn)` was deprecated in 6.1 and may be
removed by 6.18. Must change to `netif_napi_add_weight(net_dev, &napi, poll_fn,
NAPI_POLL_WEIGHT)`.

**[BUG] `dpaa_eth_sg.c:797,805` — `skb_frag_off()` → `skb_frag_offset()`.**
Renamed in kernel 6.14. Will compile-error on 6.18. Mechanical rename.

**[BUG] `dpaa_eth_sg.c:980` — `skb_frag_page()` deprecation.** The `netmem`
refactoring (6.14+) changed frag internal representation. The usage is an
assertion (`DPA_BUG_ON(!skb_frag_page(frag))`) — compile-verify, may need
`skb_frag_netmem()` wrapper.

**[BUG] `dpaa_eth_sg.c:981` — `skb_frag_dma_map()` signature change.**
The `netmem` refactoring may have changed the parameter order or type of the
first argument. Compile-verify.

**[BUG] `dpaa_eth_common.c:1673` — `vlan_eth_hdr()` → `skb_vlan_eth_hdr()`.**
Deprecated in 6.11. Mechanical rename. Also update the `#include` comment at
line 45.

### 3.3 FUNCTIONAL GAP — No `ndo_change_mtu`

**[SPEC]** `dpaa_eth.c:677-693` `dpa_private_ops` has no `.ndo_change_mtu`
callback. The SDK directly sets `net_dev->mtu = init_mtu`
(`dpaa_eth_common.c:275`) but provides no runtime MTU change handler. On 6.18
this means `ip link set mtu` returns `-EOPNOTSUPP`. Not a compile failure, but
a functional regression from the mainline `fsl_dpa` driver which supports MTU
changes through phylink. A stub `ndo_change_mtu` that rejects non-default sizes
would be needed.

### 3.4 MAC driver — 3 files (LIFT-ONLY)

**[SPEC]**
```
drivers/net/ethernet/freescale/sdk_dpaa/mac.c   — LIFT-ONLY (uses phylink_interface_max_speed only)
drivers/net/ethernet/freescale/sdk_dpaa/mac.h   — LIFT-ONLY
drivers/net/ethernet/freescale/sdk_dpaa/dpaa_eth.h   — LIFT-ONLY
drivers/net/ethernet/freescale/sdk_dpaa/dpaa_eth_common.c   — LIFT-ONLY (except vlan_eth_hdr above)
drivers/net/ethernet/freescale/sdk_dpaa/dpaa_eth_common.h   — LIFT-ONLY
```

### 3.5 Kconfig + Makefile — 2 files (structural)

**[SPEC]**
```
drivers/net/ethernet/freescale/sdk_dpaa/Kconfig   — LIFT with parent integration
drivers/net/ethernet/freescale/sdk_dpaa/Makefile   — LIFT, needs Kbuild path adjustments
```

---

## 4. sdk_qbman Files (31 files) — drivers/staging/fsl_qbman/

**[NOTE]** The SDK QBMan portal drivers (`qman_driver.c`, `bman_driver.c`,
`fsl_usdpaa.c`) replace mainline `drivers/soc/fsl/qbman/`. Their core register
poke logic is timing-invariant — the QMan/BMan portal registers have not
changed.

### 4.1 LIFT-ONLY — 25 files

**[SPEC]**
```
drivers/staging/fsl_qbman/bman_config.c
drivers/staging/fsl_qbman/bman_debugfs.c
drivers/staging/fsl_qbman/bman_driver.c
drivers/staging/fsl_qbman/bman_high.c
drivers/staging/fsl_qbman/bman_low.h
drivers/staging/fsl_qbman/bman_private.h
drivers/staging/fsl_qbman/dpa_alloc.c
drivers/staging/fsl_qbman/dpa_sys.h
drivers/staging/fsl_qbman/dpa_sys_arm.h
drivers/staging/fsl_qbman/dpa_sys_arm64.h
drivers/staging/fsl_qbman/fsl_usdpaa.c
drivers/staging/fsl_qbman/fsl_usdpaa_irq.c
drivers/staging/fsl_qbman/qbman_driver.c
drivers/staging/fsl_qbman/qman_config.c
drivers/staging/fsl_qbman/qman_debugfs.c
drivers/staging/fsl_qbman/qman_driver.c
drivers/staging/fsl_qbman/qman_high.c
drivers/staging/fsl_qbman/qman_low.h
drivers/staging/fsl_qbman/qman_private.h
drivers/staging/fsl_qbman/qman_utility.c
```

### 4.2 SKIP — 6 files (test scaffolding)

**[SPEC]**
```
drivers/staging/fsl_qbman/bman_test.c
drivers/staging/fsl_qbman/bman_test.h
drivers/staging/fsl_qbman/bman_test_high.c
drivers/staging/fsl_qbman/bman_test_thresh.c
drivers/staging/fsl_qbman/qman_test.c
drivers/staging/fsl_qbman/qman_test.h
drivers/staging/fsl_qbman/qman_test_high.c
drivers/staging/fsl_qbman/qman_test_hotpotato.c
```

### 4.3 Kconfig + Makefile — 2 files (structural)

**[SPEC]**
```
drivers/staging/fsl_qbman/Kconfig   — LIFT with parent integration
drivers/staging/fsl_qbman/Makefile   — LIFT
```

---

## 5. Public Header Files

### 5.1 LIFT-ONLY — fmd uapi (11 files)

**[SPEC]** FMan ioctl ABI — must be preserved for CDX/fci userspace
compatibility.

```
include/uapi/linux/fmd/Kbuild
include/uapi/linux/fmd/Peripherals/Kbuild
include/uapi/linux/fmd/Peripherals/fm_ioctls.h
include/uapi/linux/fmd/Peripherals/fm_pcd_ioctls.h
include/uapi/linux/fmd/Peripherals/fm_port_ioctls.h
include/uapi/linux/fmd/Peripherals/fm_test_ioctls.h
include/uapi/linux/fmd/integrations/Kbuild
include/uapi/linux/fmd/integrations/integration_ioctls.h
include/uapi/linux/fmd/ioctls.h
include/uapi/linux/fmd/net_ioctls.h
```

### 5.2 LIFT-ONLY — QBMan public headers (2 files)

**[SPEC]**
```
include/linux/fsl_bman.h   — LIFT-ONLY (name does not collide with mainline's <soc/fsl/bman.h>)
include/linux/fsl_qman.h   — LIFT-ONLY (name does not collide with mainline's <soc/fsl/qman.h>)
```

---

## 6. ASK Patches That Must Be Applied on Top

**[NOTE]** Once the SDK drivers are lifted and ported, the ASK kernel patches
— which are the *raison d'être* for Path A — must be applied. The
`fix/security-hardening` branch of `we-are-mono/ASK` has the split series.

**[SPEC]** Patch application order and target files:

| Patch | Purpose | Key SDK target files |
|-------|---------|---------------------|
| `010-ask-fman-dpaa-ehash.patch` | FMan/DPAA ehash + build wiring | `sdk_fman/Pcd/fm_cc.c`, `sdk_dpaa/dpaa_eth*.c`, `hc.c` |
| `020-ask-bridge-hooks.patch` | Bridge fast-path flow detection | Bridge/auto_bridge integration |
| `030-ask-ipv4-ipv6-forwarding.patch` | IPv4/IPv6 forwarding offload hooks | `sdk_dpaa` forwarding path |
| `040-ask-xfrm-ipsec-offload.patch` | XFRM IPsec CAAM offload hooks | `drivers/crypto/caam/pdb.h`, `net/xfrm/*` |
| `050-ask-conntrack-offload.patch` | Conntrack offload extensions | `net/netfilter/nf_conntrack_*.c` |
| `060-ask-netfilter-qosmark.patch` | QOSMARK/QOSCONNMARK targets | `net/netfilter/` |
| `070-ask-ppp-hooks.patch` | PPP offload hooks | PPP subsystem |
| `080-098` patches | Defensive fixes (KASAN, mutex, lockdep) | `sdk_dpaa`, `sdk_fman`, `netlink`, `xfrm` |

**[NOTE]** These patches were written against lf-6.12. They will need the same
6.12→6.18 rebase as the SDK files. The IPsec offload patch (040) touches files
outside the SDK tree (`net/xfrm/*`, `include/net/xfrm.h`) — those are upstream
kernel files whose API surface is even more volatile across releases.

---

## 7. Kconfig Integration

**[SPEC]** The parent Kconfig files that need `source` lines added:

```
drivers/net/ethernet/freescale/Kconfig  — add source "drivers/net/ethernet/freescale/sdk_fman/Kconfig"
drivers/net/ethernet/freescale/Kconfig  — add source "drivers/net/ethernet/freescale/sdk_dpaa/Kconfig"
drivers/staging/Kconfig                 — add source "drivers/staging/fsl_qbman/Kconfig"
drivers/soc/fsl/Kconfig                 — ensure no CONFIG_FSL_QMAN/CONFIG_FSL_BMAN collision
```

**[SPEC]** The VyOS kernel config (`kernel/common/kernel-config/`) must swap:

| Disable (mainline) | Enable (SDK) |
|--------------------|--------------|
| `CONFIG_FSL_DPAA=y` | `# CONFIG_FSL_DPAA is not set` |
| `CONFIG_FSL_DPAA_ETH=y` | `# CONFIG_FSL_DPAA_ETH is not set` |
| `CONFIG_FSL_FMAN=y` | `# CONFIG_FSL_FMAN is not set` |
| `CONFIG_FSL_BMAN=y` | `# CONFIG_FSL_BMAN is not set` |
| `CONFIG_FSL_QMAN=y` | `# CONFIG_FSL_QMAN is not set` |
| — | `CONFIG_FSL_SDK_DPA=y` |
| — | `CONFIG_FSL_SDK_DPAA_ETH=y` |
| — | `CONFIG_FSL_SDK_FMAN=y` |
| — | `CONFIG_FSL_SDK_BMAN=y` |
| — | `CONFIG_FSL_SDK_QMAN=y` |

---

## 8. Summary

**[SPEC]**

| Category | Count | Effort |
|----------|-------|--------|
| LIFT-ONLY | ~155 files | Copy only |
| NEEDS-PORT (mechanical) | 6 files, 8 lines changed | ~1 hour |
| NEEDS-PORT (verify) | 3 sites (`skb_frag_page`, `skb_frag_dma_map`, `netif_napi_add`) | ~2 hours |
| FUNCTIONAL GAP | `ndo_change_mtu` missing | ~1 day |
| STRUCTURAL | Kconfig swap + parent integration | ~1 hour |
| ASK PATCH REBASE | 10 patches, ~25 files, 6.12→6.18 | ~3–5 days |
| **Total** | **~175 files** | **~1 week** |

**[NOTE]** The mechanical port work (renamed APIs, header fixes) is straightforward.
The real cost is the ASK patch rebase from 6.12 to 6.18 — the patches touch
upstream kernel subsystems (xfrm, conntrack, bridge, PPP) whose internal APIs
change significantly between releases. This is the same rebase burden that the
ASK 1.x stack historically carried: every kernel bump required re-validating
every hook point. ASK2 was designed to avoid this by being a minimal mainline
driver (`ask.ko` ~2800 LOC) — Path A re-accepts the full rebase cost.
