# Flavor: `ask`

NXP ASK fast-path kernel build for LS1046A. Layered on top of `kernel/common/` per [INTEGRATION-PLAN §3.4](../../../plans/INTEGRATION-PLAN.md). Verbatim absorption of the `kernel-ls1046a-build` producer (current pin: `kernel-6.6.137-ask41` patch set, now applied against `linux-6.18.28`).

## Inputs

- **Kernel base:** `linux-6.18.28` (from `versions.lock` once wired in PR 3+).
- **Patches applied** (in order):
  1. `kernel/common/patches/vyos/*.patch` — 2 patches (linkstate, perf .deb).
  2. `kernel/common/patches/board/*.patch` — 0 patches (reserved 100..199).
  3. `kernel/common/patches/fixes/*.patch` — 4 patches (093, 094, 095, 120).
  4. `kernel/flavors/ask/patches/ask/*.patch` — **7 patches** (ASK fast-path: fman/dpaa ehash, bridge hooks, cpe fast-path Kconfig, ipv4/ipv6 fwd, netfilter qosmark, ppp hooks, wext core restore).
  5. `kernel/flavors/ask/patches/fixes/*.patch` — **3 patches** (097 FCI nlkey narrow gate, 102 ioremap_cache_ns shim, 110 SDK Makefile KASAN-off).
- **SDK source drop:** `kernel/flavors/ask/sdk-sources/` — **266 files** (lf-6.6.y SDK overlay forward-ported via `nxp-qoriq/linux ask-6.6-port` @ `6d0b77e999047`, with **35 ASK-edit-marked files** carrying ask26..ask41 direct edits).
- **OOT modules:** `kernel/flavors/ask/oot-modules/{cdx,fci,auto_bridge,iptables-extensions}/`.
- **Userspace patches:** `kernel/flavors/ask/userspace-patches/{ppp,rp-pppoe}/`.

Total kernel patch count: **16** (verified by `kernel/common/scripts/patch-health.sh --flavor ask`).

## Driver stack

NXP SDK drivers (NOT mainline DPAA):

| Symbol | Path | Mainline equivalent (NOT used) |
|---|---|---|
| `CONFIG_FSL_SDK_FMAN=y` | `drivers/net/ethernet/freescale/sdk_fman/` | `fman/` (`CONFIG_FSL_FMAN`) |
| `CONFIG_FSL_SDK_DPAA_ETH=y` | `drivers/net/ethernet/freescale/sdk_dpaa/` | `dpaa/` (`CONFIG_FSL_DPAA_ETH`) |
| `CONFIG_FSL_SDK_DPA=y` | `drivers/staging/fsl_qbman/` | `drivers/soc/fsl/qbman/` |

Required for the ASK userspace stack (`dpa_app`, `fmc`, `cmm`, `cdx`).

## Defconfig invariants

- `CONFIG_NET_KEY=y` (load-bearing — see [AGENTS.md "Reference-Aligned Defconfig Invariants"](../../../AGENTS.md)).
- `CONFIG_INET_IPSEC_OFFLOAD=n` (xfrm fields missing on 6.6.y; will need re-evaluation once 6.18.x baseline is stable).
- `CONFIG_CPE_FAST_PATH=y` (ASK fast-path master gate).
- `CONFIG_ASK_FCI_NLKEY=y` (narrow-gate that registers `NETLINK_KEY=32` proto without enabling the broken IPsec offload data path).

## Two-chain failure model

Symptoms split into two **independent** chains — see [AGENTS.md](../../../AGENTS.md) for the full diagnostic checklist:

- **Chain 1** (kernel-side, this repo): `cmm process running [FAILED]` → check `socket(AF_NETLINK, SOCK_RAW, NETLINK_KEY=32) = -EPROTONOSUPPORT`.
- **Chain 2** (userspace-side, NOT this repo): `dpa_app applied PCD configuration (failed rc=65280)`. Sub-trigger A: MURAM exhaustion (consumer `cdx_pcd.xml` + `fmc` rebuild needed). Sub-trigger B: ARM64 oops in `copy_td_to_ccbase+0x68` — fixed at producer ask39 by gating EHASH cast on `externalHash`.

## Validate locally

```bash
bash kernel/common/scripts/patch-health.sh --flavor ask
```

Expected: `Pass: 16  Fail: 0` and `no SDK file conflicts (266 files to install)`.

## ASK-edit audit surface

```bash
grep -rn 'ASK-edit' kernel/flavors/ask/sdk-sources/
```

Should return entries from 35 distinct files spanning ask26..ask41 direct SDK source edits (per the policy shift documented in [AGENTS.md "ask26 direct SDK source edits"](../../../AGENTS.md)).

## Status

PR 1+2 (scaffold + verbatim absorption) — **done**. Green build deferred to PR 3+ (after `default` flavor lands first per the user's "default → ask → vpp" sequence).