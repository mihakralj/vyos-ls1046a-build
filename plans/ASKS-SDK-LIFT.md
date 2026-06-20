# Plan: NXP SDK Lift to 6.18 Mainline Kernel

**Version 1.0.0 · 2026-06-19**
**Branch: `nxp_ask`**

## Objective

Lift the vendored NXP SDK driver stack (FMan PCD, DPAA Ethernet, QBMan portals)
from `nxp-imx/linux-imx` `lf-6.12.y` onto the VyOS mainline 6.18 kernel as
out-of-tree modules, port the mechanical API differences, and wire the Kconfig
switch so ASK hardware offload runs on the genuine SDK PCD chain.

## Prerequisites

- **Decision gate resolved:** this plan assumes the user selected Path A
  (full SDK lift) from `analysis/ASK-SDK-LIFT-TO-6.18.md` §11.
- **Reference sources:**
  - `analysis/ASK2-VENDORED-PORT-FILES.md` — file inventory (what to lift, what needs porting)
  - `analysis/ASK-SDK-LIFT-TO-6.18.md` — scoping analysis, FE-VM builder gap,
    mutual-exclusion constraints, effort/risk register
  - `nxp-imx/linux-imx` `lf-6.12.y` — source of SDK files
  - `we-are-mono/ASK` `fix/security-hardening` — ASK patches to apply on top

## Phase 1: File Lift (mechanical)

### 1.1 Stage SDK source trees under `kernel/flavors/ask/`

Copy from `nxp-imx/linux-imx` `lf-6.12.y` → `kernel/flavors/ask/sdk/`:

```
sdk/
├── fman/          ← drivers/net/ethernet/freescale/sdk_fman/  (104 files)
├── dpaa/          ← drivers/net/ethernet/freescale/sdk_dpaa/  (24 files)
├── qbman/         ← drivers/staging/fsl_qbman/                 (25 files, skip tests)
├── Kconfig        ← merged sdk_fman + sdk_dpaa + fsl_qbman Kconfig
├── Makefile       ← top-level Kbuild that recurses into fman/dpaa/qbman
├── include/
│   ├── linux/fsl_bman.h  ← from include/linux/
│   ├── linux/fsl_qman.h  ← from include/linux/
│   └── uapi/linux/fmd/   ← from include/uapi/linux/fmd/ (11 files)
└── xt_QOSMARK/    ← iptables-extensions from we-are-mono/ASK fix/security-hardening
```

**SKIP from lift:**
- `fman_test.c`, `fsl_fman_test.h`, `lnxwrp_resources_ut.*` (test scaffolding)
- `bman_test*.c`, `qman_test*.c` (QBMan test scaffolding)
- `ls1043_dflags.h`, `ncsw_config.mk` (NXP build internals)
- `dpaa_eth_proxy.c` (proxy netdev — ASK doesn't use it)
- `dpaa_1588.c` / `dpaa_1588.h` (PTP — not needed for ASK)

**Files to stage: ~170 → ~160 after skips**

### 1.2 Stage ASK patches

Copy from `we-are-mono/ASK` `fix/security-hardening`:
```
kernel/flavors/ask/patches/ask/
├── 010-ask-fman-dpaa-ehash.patch
├── 020-ask-bridge-hooks.patch
├── 030-ask-ipv4-ipv6-forwarding.patch
├── 040-ask-xfrm-ipsec-offload.patch
├── 050-ask-conntrack-offload.patch
├── 060-ask-netfilter-qosmark.patch
├── 070-ask-ppp-hooks.patch
├── 080-wext-core-restore-ndo_do_ioctl.patch
├── 090-qbman-dpa_alloc-preallocate-nodes.patch
├── 091-sdk_dpaa-dpa_get_channel-use-mutex.patch
├── 092-sdk_fman-FmPcdLockTryLockAll-nest-annotation.patch
├── 093-netlink-name-L2FLOW-cb-mutex.patch
├── 094-sdk-fman-dpaa-qbman-kasan-sanitize-off.patch
├── 095-sdk_fman-iomem-mem-ops.patch
├── 096-sdk_fman-mac-hash-alloc-null-check.patch
├── 097-xfrm-trans-queue-force-dst-refcount.patch
└── 098-sdk_dpaa-bp-alloc-slab-build-skb.patch
```

## Phase 2: Mechanical API Port (6.12→6.18)

### 2.1 Fixes applied directly to lifted files

| File | Change | Line |
|------|--------|------|
| `sdk/fman/src/wrapper/lnxwrp_fm.c` | `#include <asm/uaccess.h>` → `<linux/uaccess.h>` | L56 |
| `sdk/fman/src/wrapper/lnxwrp_ioctls_fm_compat.c` | Same | L54 |
| `sdk/fman/src/xx/xx_arm_linux.c` | Same | L69 |
| `sdk/dpaa/dpaa_eth.c` | `netif_napi_add(net_dev, &np, poll)` → `netif_napi_add_weight(net_dev, &np, poll, NAPI_POLL_WEIGHT)` | L715-716 |
| `sdk/dpaa/dpaa_eth_sg.c` | `skb_frag_off(frag)` → `skb_frag_offset(frag)` | L797, L805 |
| `sdk/dpaa/dpaa_eth_common.c` | `vlan_eth_hdr(skb)` → `skb_vlan_eth_hdr(skb)` | L1673 |

### 2.2 Compile-verify items (may need further changes on 6.18)

| File | Item | Risk |
|------|------|------|
| `sdk/dpaa/dpaa_eth_sg.c:980` | `skb_frag_page()` — netmem refactoring | Low (assertion only) |
| `sdk/dpaa/dpaa_eth_sg.c:981` | `skb_frag_dma_map()` — API signature | Medium |
| `sdk/dpaa/mac.c:273` | `phylink_interface_max_speed()` — still valid in 6.18 | Low |

### 2.3 Build system

Create `kernel/flavors/ask/sdk/Makefile` that builds the three sub-trees
as a single `sdk_fman.ko` + `sdk_dpaa.ko` + `sdk_qbman.ko` OOT module set
against the kernel tree at `$(KSRC)` (the `ask-kernel-snapshot/ksrc` symlink).

Include paths (from `ncsw_config.mk`):
```
-I$(src)/fman/inc
-I$(src)/fman/inc/Peripherals
-I$(src)/fman/inc/flib
-I$(src)/fman/inc/integrations/LS1043
-I$(src)/fman/src/inc
-I$(src)/fman/src/inc/wrapper
-I$(src)/fman/src/inc/system
```

## Phase 3: Kconfig Swap

### 3.1 Kernel config changes

Document the swap in a config fragment at `kernel/flavors/ask/kernel-config/10-sdk-swap.config`:

```
# Swap mainline DPAA for SDK DPAA
# CONFIG_FSL_FMAN is not set
# CONFIG_FSL_DPAA_ETH is not set
# CONFIG_FSL_DPAA is not set
# CONFIG_FSL_BMAN is not set
# CONFIG_FSL_QMAN is not set
CONFIG_FSL_SDK_FMAN=y
CONFIG_FSL_SDK_DPAA_ETH=y
CONFIG_FSL_SDK_DPA=y
CONFIG_FSL_SDK_BMAN=y
CONFIG_FSL_SDK_QMAN=y
```

### 3.2 CI wiring

Update `bin/ci-setup-kernel.sh`:
- Add a block that, when building a SDK-lift kernel, overlays `kernel/flavors/ask/sdk/` onto the kernel tree as an in-tree driver (OR builds it as OOT after the kernel build)
- Disable the existing mainline-DPAA board patches (0086–0144) when SDK is active
- Wire `CONFIG_FSL_SDK_FMAN=y` etc.

## Phase 4: DTS (Board Device Tree)

### 4.1 SDK-format port nodes

Create `kernel/flavors/ask/dts/mono-gateway-dk-sdk.dts` that overrides
all 16 FMan port compatible strings from mainline format to SDK format:

```
mainline: fsl,fman-v3-port-rx  →  SDK: fsl,fman-port-1g-rx  or fsl,fman-port-10g-rx
mainline: fsl,fman-v3-port-tx  →  SDK: fsl,fman-port-1g-tx  or fsl,fman-port-10g-tx
```

Also set `status = "okay"` on MAC9 (eth3 SFP+) and MAC10 (eth4 SFP+),
and add the `fsl,dpaa` container node the SDK's `dpaa_eth.c` probe uses
(compatible `"fsl,dpa-ethernet"`).

## Phase 5: ASK Patch Rebase

### 5.1 Apply 010-098 patches in order

Each patch must be rebased from 6.12 to 6.18. The patches touch:

| Patches | Subsystem | Rebase complexity |
|---------|-----------|-------------------|
| 010 | sdk_fman + sdk_dpaa (PCD, ehash, KG, CC) | **Low** — SDK-local files |
| 020 | Bridge fast-path hooks | **Medium** — `net/bridge/` API drift |
| 030 | IPv4/IPv6 forwarding | **Medium** — `net/ipv{4,6}/` |
| 040 | XFRM IPsec CAAM offload | **High** — `net/xfrm/`, `crypto/caam/` volatile |
| 050 | Conntrack offload | **High** — `net/netfilter/` volatile |
| 060 | QOSMARK/QOSCONNMARK | **Medium** — targets/schedulers |
| 070 | PPP hooks | **Low** — `net/ppp/` |
| 080-098 | Defensive fixes | **Low** — mostly SDK-local |

### 5.2 Phase 5B: FE-VM builder un-stub (critical for flowing FE path)

Per `analysis/ASK-SDK-LIFT-TO-6.18.md` §2, the lf-6.12 mono patch stubs both
FE builders. The real bodies exist in `ask-kernel-5.4.patch`:

- `FmPcdCcBuildFE` (lf-5.4 def @line 8882) — emits 28-byte FE opcode struct
- `FmPcdCcBuildContextByFE` (lf-5.4 def @line 8953) — writes per-task FE working store (HM table copy, ENQ fqid/ppid, **MUX next-FE phys offset**, TRANSITION next-AD phys offset)

These ~150 LOC must be back-ported from lf-5.4 into the SDK tree after
the patch series is applied. The MUX case (`ctx[0] = phys(NextFE) - physicalMuramBase`)
is the critical deposit that gate0143/gate0144 proved missing.

## Phase 6: Build & Verify

### 6.1 Local build

```bash
# On Cobalt 100 ARM64 VM:
cd kernel/flavors/ask/sdk
make -C /path/to/6.18-kernel M=$PWD modules LOCALVERSION=-vyos
```

### 6.2 Board verification sequence

1. Build kernel with SDK drivers in-tree (or OOT with signing)
2. Deploy via TFTP dev-loop (`bin/dev-build.sh`)
3. Verify boot: SDK FMan probes, `fsl_dpa` netdevs appear with correct port order
4. Load ASK patches: verify `cdx.ko` / `fci.ko` load and `/dev/cdx_ctrl` appears
5. Flow test: program a minimal `en_exthash` table, verify selective HW offload forwards frames

## Phase 7: CI Integration

### 7.1 Build workflow changes

- Add SDK source staging to `bin/ci-setup-kernel.sh`
- Wire `CONFIG_FSL_SDK_FMAN=y` etc. in kernel config fragments
- Remove mainline-DPAA board patches 0086–0144 (they target mainline `fman/` not SDK)
- Sign SDK modules with the persistent key (existing `ci-setup-kernel.sh` L1077-1243)

### 7.2 ISO hook

Update `data/hooks/97-ask-modules.chroot` to also handle `sdk_fman.ko`/`sdk_dpaa.ko` module loading if they're built OOT.

## Dependencies & Order

```
Phase 1 (file lift) ──────┐
                           ├──→ Phase 2 (mechanical API port)
                           │         │
Phase 3 (Kconfig swap) ────┘         │
                                     ├──→ Phase 5 (patch rebase)
Phase 4 (DTS) ──────────────────────┘         │
                                               ├──→ Phase 5B (FE un-stub)
                                               │         │
                                               │         ├──→ Phase 6 (build & verify)
                                               │         │         │
                                               │         │         └──→ Phase 7 (CI)
                                               │         │
                                               └─────────┘
```

Phases 1–3 can be done in parallel. Phase 4 is independent.
Phase 5 depends on 1+2 (files must exist and compile before patches apply).
Phase 5B depends on 5 (patches must be applied before un-stubbing FE builders).
Phase 6 is the integration gate. Phase 7 is the productionization tail.

## Risk Watch

| # | Risk | Mitigation |
|---|------|------------|
| R2 | SDK↔mainline DPAA mutual exclusion | Accept — this is a different kernel config, not a runtime mode |
| R3 | QBMan portal conflict | SDK staging `fsl_qbman` replaces mainline `soc/fsl/qbman` |
| R4 | Class 3/4 API churn 6.12→6.18 | Gated by Phase 2 compile-verify; if deep breakage found, escalate to full Class 3/4 rewrite |
| R5 | MURAM exhaustion | Trim `cdx_pcd.xml` tables (per `analysis/ASK-SDK-LIFT-TO-6.18.md` R5) |
| R9 | Userspace producer (`dpa_app`/`fmc`) | Defer to Phase 5+; initial verification uses kernel/debugfs to program EKFC schemes |

## Reference Documents

- `analysis/ASK2-VENDORED-PORT-FILES.md` — file-level inventory, lift/port classification
- `analysis/ASK-SDK-LIFT-TO-6.18.md` — scoping analysis, FE gap, mutual-exclusion, risk register
- `analysis/ASK2-VS-NXP-GAP-ANALYSIS.md` — why register-domain approach failed, Path A/B/D decision
- `specs/ask2-rewrite-spec.md` — Option B mainline-rewrite mission (the sanctioned path)
- `plans/DUAL-DATAPLANE.md` — single-image state machine (broken by this plan's R2)