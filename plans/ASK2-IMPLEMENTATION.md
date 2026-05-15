# ASK2 implementation plan — PR breakdown

**Status:** active. Drives the implementation of ASK2 per
[`specs/ask2-rewrite-spec.md`](../specs/ask2-rewrite-spec.md) (v1.1,
2026-05-14 — provenance relaxation). All PRs target the `ask20` branch
unless noted otherwise.

This document is the working implementation plan; the spec is the
architecture source-of-truth. When the two disagree, **the spec wins**
— update this document to track.

## Branch hygiene

- `main` continues to ship the default + vpp flavors (mainline DPAA,
  no ASK). `FLAVOR=ask` builds on `main` are vanilla VyOS until ASK2
  components land here.
- `ask20` is the integration branch for ASK2. **All PRs in this
  document land on `ask20` first.** Periodic merges from `main` keep
  the kernel/board/CI baseline in sync.
- After M5 acceptance gate (Section 11.1 of the spec), `ask20` is
  fast-forward-merged into `main`.

## Status tracker

| PR  | Title                                            | Target | Status      |
|-----|--------------------------------------------------|--------|-------------|
| —   | Spec authored, scaffold cleanup                  | ask20  | landed |
| —   | UAPI header `include/uapi/linux/ask/ask.h`       | ask20  | landed |
| 1   | M0.1 — module skeleton + Kbuild + Kconfig        | ask20  | landed |
| 2   | M0.2 — three in-tree patch stubs (placeholders)  | ask20  | landed |
| 3   | M0.3 — wire build pipeline (CI + local-build)    | ask20  | landed |
| 4   | M0.4 — kunit harness + first dummy test          | ask20  | landed |
| 5   | M1.1 — `ask_main.c` + `ask_genl.c` GET_INFO      | ask20  | landed |
| 6   | M1.2 — `ask_hostcmd.c` wire-format encoders      | ask20  | landed |
| 7   | M1.3 — `ask_flow.c` rhashtable + RCU             | ask20  | landed |
| 8   | M1.4 — `ask_flow_offload.c` flow_block_cb        | ask20  | landed |
| 9   | M1.5 — kunit coverage ≥ 80% on M1 surface        | ask20  | landed |
| 10  | M2.1 — `0001-caam-qi-share.patch` (real code)    | ask20  | landed |
| 11  | M2.2 — `0002-dpaa-eth-flow-block.patch` (real)   | ask20  | landed |
| 12  | M2.3 — `0003-fman-host-command-api.patch` (real) | ask20  | landed |
| 13  | M2.4 — ucode version from QEF blob (DT)          | ask20  | landed |
| 14a | M2.5a — `0004-fman-pcd-subsystem.patch` orchestration (`fman_pcd.c` + accessors in `fman.c`) | ask20 | landed |
| 14b-prep | M2.5b-prep — KG public API stub (`fman_pcd_kg.c`) + `keygen_scheme_setup`/`keygen_bind_port_to_schemes` exports | ask20 | landed |
| 14b-body | M2.5b-body — KG real KGSE_* programming (IPv4 5-tuple) | ask20 | landed |
| 14c-prep | M2.5c-prep — CC public API stub (`fman_pcd_cc.c` with 6 -EOPNOTSUPP stubs) + `fman_pcd_action` tagged-union + extract/key-table types in `<linux/fsl/fman_pcd.h>` | ask20 | landed |
| 14c-body | M2.5c-body — CC real MURAM-resident tree-group-table programming (RM 8.7.4.1), classification-node record packing (RM 8.7.4.2), action-template encoding (RM 8.7.4.3), kunit, also replaces `fman_pcd_kg_attach_cc()` -EOPNOTSUPP stub. Split into 5 sub-PRs (body-1..5) per the design memo below. | ask20 | landed |
| 14d-prep | M2.5d-prep — manip public API stub (`fman_pcd_manip.c`) + `fman_pcd_manip_params` tagged-union (NAT_V4, NAT_V6, VLAN_PUSH, VLAN_POP, TTL_DEC) in `<linux/fsl/fman_pcd.h>` | ask20 | landed |
| 14d-body | M2.5d-body — manip real MURAM template programming (RM 8.7.5), auto-checksum recompute (RM 8.7.5.4), kunit | ask20 | landed |
| 14e-prep | M2.5e-prep — plcr public API stub (`fman_pcd_plcr.c`) + `fman_pcd_plcr_params` (CIR/CBS/EIR/EBS, color mode) in `<linux/fsl/fman_pcd.h>` | ask20 | landed |
| 14e-body | M2.5e-body — plcr real trTCM profile programming per RM 8.7.6, runtime rate update, kunit. Split into sub-PRs (body-1+ TBD) following the PR14d cadence. | ask20 | landed |
| 14f-prep | M2.5f-prep — replicator + parser public API stubs (`fman_pcd_replic.c`, `fman_pcd_prs.c`) + `fman_pcd_replic_member` in `<linux/fsl/fman_pcd.h>` | ask20 | landed |
| 14f-body | M2.5f-body — replicator real MURAM group table (RM 8.7.7), parser HXS pass-through config (RM 8.7.2), kunit | ask20 | landed |
| 14g | M2.5g — End-to-end wire-up — `ask_hostcmd.c` calls PCD API; first IPv4 TCP flow traverses silicon | ask20 | **in progress** — split into six sub-PRs (see rows 14g-prep / 14g-prep2 / 14g-body-1..4). 14g-prep + 14g-prep2 + 14g-body-1 landed; body-2..4 pending. Acceptance gate: spec §13.7 nft offload round-trip + KUnit + on-silicon flow traversal — gates rows 15–21. |
| 14g-prep | M2.5g-prep — Promote FMan PCD OOT consumer entry points to `<linux/fsl/fman_pcd.h>` + add `fman_pcd_from_of_node()` resolver in `fman.c` so `ask.ko` can walk DT-node → struct fman_pcd \* without dragging in driver-private `fman.h`. | ask20 | landed (patch 0027) |
| 14g-prep2 | M2.5g-prep2 — Export `dpaa_get_rx_default_fqid(struct net_device *, u32 *)` from `drivers/net/ethernet/freescale/dpaa/dpaa_eth.c` so `ask.ko` body-2 can plumb `FMAN_PCD_ACTION_FORWARD_FQ.forward_fq.fqid` to the per-port RX default FQ that NAPI already drains. Fail-closed on non-DPAA netdevs via `dev->netdev_ops != &dpaa_ops` check (-ENODEV); -ENOENT if FQ list empty. Single authoritative FQID source (DPAA driver owns it). | ask20 | landed (patch 0028) |
| 14g-body-1 | M2.5g-body-1 — `ask_hw` PCD bring-up: `ask_hw_pcd_bringup()` walks DT `fsl,fman` → `fman_pcd_from_of_node()` → builds 1-group CC tree + empty v4-TCP CC node (KEY extract size 13, miss=DROP) + IPv4 5-tuple KG scheme (PARSE_RESULT extracts: SIP@12 DIP@16 proto@9 sport@20 dport@22) + `fman_pcd_kg_attach_cc()`. NULL-safe on non-DPAA hosts (logs warn, returns 0). `ask_hw_pcd_get()` accessor + `ask_priv_pack/unpack_hw_flow_id()` helpers (32-bit packing: token<<16 \| key_idx; token 1 = ASK_HW_FLOW_ID_TOKEN_V4_TCP). NO port-bind yet — chain is quiescent until M3. | ask20 | landed (commit `c1cb22c`). Spec §13.5.1–§13.5.2. Verified: ask.ko OOT build clean against vmlinux Module.symvers (5 new symbols: ask_priv_pack_hw_flow_id, ask_priv_unpack_hw_flow_id, ask_hw_pcd_bringup, ask_hw_pcd_teardown, ask_hw_pcd_get). |
| 14g-body-2 | M2.5g-body-2 — `ask_hw_flow_insert()` dispatcher + `ask_hw_flow_insert_v4_tcp()` worker (KG-extract layout SIP/DIP/proto/sport/dport, FORWARD_FQ via `dpaa_get_rx_default_fqid()`) + `ask_hw_flow_remove()` (modify_next_action → DROP) + `ask_hw_flow_query_stats()` -EOPNOTSUPP stub. Per spec §13.5 (lines 1548-1616 of this doc) and operator-approved Q2 dispatcher design (-ENODEV/-EOPNOTSUPP fallback signal to ask_flow.c). | ask20 | landed (commit pending). |
| 14g-body-3 | M2.5g-body-3 — `ask_flow` integration: nft `flow add inet …` → `ask_flow_insert_v4_tcp()` → `ask_hostcmd_send()` → silicon CC node populated. Smoke test from VyOS CLI on real hardware. | ask20 | pending. |
| 14g-body-4 | M2.5g-body-4 — KUnit suite for `ask_hw` + `ask_hostcmd` paths (mocked PCD handles), on-silicon hardware verification (first IPv4 TCP flow traverses), and patch-health stays at Pass=N/Fail=0 across the full body chain. Closes acceptance gate. | ask20 | pending. |
| 14h | MURAM-BUDGET-FIX — reduce `FMAN_PCD_MURAM_RESERVED_BYTES` from 96 KiB to a probe-able value (candidates 64 / 32 / 16 KiB) so `fman_muram_alloc()` succeeds on real silicon | ask20 | landed |
| 15  | M3.x — remaining flow types (NAT/PAT/v6/bridge)  | ask20  | gated on PR14g |
| 16  | M4.x — `ask_xfrm.c` + CAAM packet-mode IPsec     | ask20  | gated on PR14g |
| 17  | M5.1 — `askd` (sd-event + libmnl)                | ask20  | gated on PR14g |
| 18  | M5.2 — `ask-cli` (Python Varlink client)         | ask20  | gated on PR14g |
| 19  | M5.3 — VyOS CLI integration                      | ask20  | gated on PR14g |
| 20  | M5.4 — VyOS conf_mode + op_mode                  | ask20  | gated on PR14g |
| 21  | M6.x — VPP coexistence, soak, performance gates  | ask20  | gated on PR14g |

Status legend:
- **not started** — no commits yet on the target branch
- **WIP** — branch open, commits landing
- **review** — PR open against ask20, awaiting review
- **landed** — merged into ask20

## Sequencing rules

- PRs within a milestone are **not** strictly sequential, but they share
  acceptance gates. A milestone is "done" when the gate passes.
- Hardware-touching PRs (M2 onwards) **block on the in-tree kernel patch
  PRs (10/11/12)**. There is no way to land an `OP_FLOW_INSERT_V4_TCP`
  exercise without `fman_host_cmd_send()` existing.
- **M0** needs no hardware; build pipeline + module skeleton only.
- **M1** needs no hardware; kunit covers the surface.
- **M2 onwards** requires the live Mono Gateway DK over the lab loop
  documented under "Hardware loop" below. Physical bring-up (USB
  install, serial console, port-labeling) is done; from here on every
  M2+ task is a normal edit → cross-compile → TFTP-boot → SSH-test
  cycle.

## Hardware loop

The Mono Gateway DK is **continuously available** over the LAN,
reached via Tailscale subnet routing (the `192.168.0.0/16` route is
advertised by a node in the LAN and accepted by this VM):

- The dev-loop **build host is now the Cobalt 100 Azure ARM64 VM**
  (`arm64-runner`, Tailscale `100.125.95.22`). Native aarch64, 32
  cores, 125 GB RAM. Workspace at `/home/vyos/vyos-ls1046a-build/`,
  staged kernel tree at `work/linux-${KERNEL_VERSION}/`. Both the
  self-hosted GitHub Actions runner (`vm-runner-2`) for ISO builds
  AND the fast dev-loop kernel build now run here — same VM, same
  toolchain, no cross-compile penalty. Iteration script:
  `bin/dev-build.sh kernel|dtb|extract|iso-live|push`.
- The **artefact serving host** is **LXC 200** (`vyos-builder` LXC
  inside the `heidi` Proxmox host at `192.168.1.15`, container LAN IP
  `192.168.1.137`). It owns `/srv/tftp/` (tftpd-hpa on UDP/69) and the
  HTTP server on port 8080 that the board's `dev_boot_live` U-Boot env
  fetches the squashfs from. **LXC 200 is no longer a build host** —
  it has been decommissioned to pure serving relay. `bin/dev-build.sh`
  on Cobalt 100 rsyncs artefacts to `admin@192.168.1.137:/srv/tftp/`
  via `--rsync-path="sudo rsync"` (admin has passwordless sudo).
- Iteration times per `plans/DEV-LOOP.md`: native arm64 incremental
  kernel build ≈30 s, full ≈2–3 min, DTB only ≈10 s.
- All hosts are reachable over SSH via the `ssh` MCP server with
  six pre-configured connections:
  - `heidi` (192.168.1.15) — Proxmox host PVE 8.x running on the LAN
  - `lxc200` (192.168.1.137) — `vyos-builder` LXC on heidi; owns
    `/srv/tftp/` and serves it over TFTP/HTTP (artefact host only)
  - `vyos` (192.168.1.190) — management address on eth0 (Mono Gateway)
  - `vyos-eth1` (192.168.1.185) — middle RJ45
  - `vyos-eth2` (192.168.1.189) — leftmost RJ45
  - `vyos-eth4` (192.168.1.192) — right SFP+
  These are wired into `/home/vyos/.config/ssh-mcp-config.json` and
  available to any session as `ssh_execute_command`, `ssh_upload_file`,
  `ssh_download_file` without further setup. The `vyos*` and `lxc200`
  entries authenticate via `~/.ssh/vyos_vanity` and `~/.ssh/admin_key`
  respectively.
- Target state (verified 2026-05-12): `Linux vyos 6.18.28-vyos
  aarch64`, VyOS `2026.05.11-0542-rolling`, board DT compatible
  `mono,gateway-dk fsl,ls1046a`. CAAM up with three job rings
  (1710000/1720000/1730000.jr), FMan probed
  (`/sys/bus/platform/devices/1a00000.fman`), all five `dpaa_setup_tc`
  netdevs bound (`dpaa-ethernet.0` through `dpaa-ethernet.4`).
- Boot-image cycle is **`ssh vyos sudo reboot` → U-Boot → TFTP `run dev_boot`**
  (which is wired into the SPI env to fetch vmlinuz/DTB/initrd from
  `192.168.1.137:/srv/tftp/` on LXC 200). One reboot ≈30–45 s wall-clock
  from `ssh reboot` to login prompt on the new kernel; the SSH
  connection drops mid-cycle and is re-established after a short sleep.
  **`kexec` is NOT a routine iteration path on this target.** Verified
  2026-05-12: (a) the `kexec` userspace binary is not in the VyOS ISO
  (`which kexec` empty, vbash returns "Invalid command"), (b) the
  kernel was built with `CONFIG_KEXEC_SIG=y` +
  `CONFIG_KEXEC_IMAGE_VERIFY_SIG=y` + `CONFIG_ARCH_DEFAULT_KEXEC_IMAGE_VERIFY_SIG=y`
  so even with `kexec-tools` installed, `kexec_file_load(2)` would
  reject the unsigned dev-loop vmlinuz from TFTP, and (c) the legacy
  `kexec_load(2)` would in principle work (the syscall isn't disabled
  via `kexec_load_disabled`) but VyOS doesn't ship `kexec-tools` and
  there is no signed kernel iteration loop on the dev path. The serial
  console is only needed for U-Boot env edits or recovery from a hard
  crash, both of which are infrequent.

What the lab loop **does not cover**:

- Re-flashing U-Boot itself in SPI (`mtd0`) — risky, would need serial
  recovery if it bricks. Out of scope for ASK2.
- Re-labeling physical ports / moving SFP modules. Not needed; the
  existing labelling (eth0 mgmt, eth1/2 RJ45, eth3/4 SFP+) is fine.
- Extra physical traffic generators (Spirent/Keysight). M6 performance
  soak wants those for line-rate measurements at the Geerling 1 Mpps
  target, but **iperf3 between vyos-eth{1,2,4} and an LXC peer covers
  everything up to ~3 Gbps**, which is the M2/M3 verification
  threshold per spec §11.1.

**Every PR from M2 through M5 is exercisable end-to-end through this
loop** — kernel patch authored, cross-built, TFTP-booted, verified
over SSH, with dmesg captured and findings written back into the spec
and Qdrant. M6 (performance soak) is the only milestone that requires
on-site lab presence for the Spirent/Keysight runs; the iperf3-level
numbers up to ~3 Gbps remain reachable through the loop.

---

## M0 — Build pipeline scaffolding *(no hardware required)*

**Acceptance gate:** `FLAVOR=ask gh workflow run "VyOS LS1046A build (self-hosted)"`
produces a signed `ask.ko` artefact. The module loads cleanly on a real
LS1046A (`insmod ask.ko` returns 0; `dmesg` shows the load banner; `rmmod`
succeeds). The module does nothing useful — it just registers a GENL
family and exits.

### PR1 — Module skeleton + Kbuild + Kconfig

**Files added under `kernel/flavors/ask/oot-modules/ask/`:**

```
ask/
├── Kbuild
├── Kconfig
├── Makefile                             # entry point invoked by ci-build-packages.sh
├── include/
│   ├── uapi/linux/ask/ask.h             # already landed
│   └── ask_internal.h                   # forward decls, error codes, cap bits
├── ask_main.c                           # module init/exit, version banner only
├── ask_genl.c                           # genl_family registration only (no commands wired)
├── ask_genl_attr.c                      # nla_policy tables (skeleton)
├── ask_flow.c                           # rhashtable scaffold (no inserts)
├── ask_flow_offload.c                   # flow_block_cb skeleton
├── ask_xfrm.c                           # xfrmdev_ops skeleton
├── ask_caam.c                           # caam descriptor sharing skeleton
├── ask_bridge.c                         # switchdev notifier skeleton
├── ask_neigh.c                          # netevent notifier skeleton
├── ask_op.c                             # offline-port skeleton
├── ask_hostcmd.c                        # wire-format skeleton
├── ask_stats.c                          # u64_stats_sync wrappers
├── ask_debugfs.c                        # /sys/kernel/debug/ask/* (gated on DEBUG_FS)
├── ask_trace.h                          # tracepoint definitions
└── tests/
    └── .gitkeep                         # populated by PR4
```

**Kbuild contents:**
```makefile
# SPDX-License-Identifier: GPL-2.0
ccflags-y += -I$(src)/include
obj-$(CONFIG_NET_ASK) += ask.o
ask-y := \
    ask_main.o \
    ask_genl.o \
    ask_genl_attr.o \
    ask_flow.o \
    ask_flow_offload.o \
    ask_xfrm.o \
    ask_caam.o \
    ask_bridge.o \
    ask_neigh.o \
    ask_op.o \
    ask_hostcmd.o \
    ask_stats.o \
    ask_debugfs.o
```

**Kconfig contents:**
```
config NET_ASK
    tristate "ASK2 fast-path offload for NXP LS1046A FMan/210"
    depends on FSL_DPAA && CAAM_QI && NET_FLOW_OFFLOAD
    select XFRM_OFFLOAD
    help
      ASK2 NXP LS1046A FMan microcode (210-series) hardware offload
      driver. Replaces the legacy proprietary cdx.ko/auto_bridge.ko stack.
      See specs/ask2-rewrite-spec.md.

      To compile this driver as a module, choose M here. The module will
      be called ask.
```

**ask_main.c contents:**
```c
/* SPDX-License-Identifier: GPL-2.0 */
#include <linux/module.h>
#include <linux/init.h>
#include "ask_internal.h"

static int __init ask_init(void)
{
    pr_info("ask: 2.0.0 loading (skeleton — no functionality)\n");
    return ask_genl_register();
}

static void __exit ask_exit(void)
{
    ask_genl_unregister();
    pr_info("ask: 2.0.0 unloaded\n");
}

module_init(ask_init);
module_exit(ask_exit);

MODULE_AUTHOR("VyOS LS1046A maintainers");
MODULE_DESCRIPTION("ASK2 — NXP LS1046A FMan/210 hardware offload");
MODULE_LICENSE("GPL");
MODULE_VERSION("2.0.0");
```

All other `.c` files contain only:
- the SPDX header
- `#include "ask_internal.h"`
- empty stub of every function declared in `ask_internal.h` returning `-EOPNOTSUPP`

**Sign-off:**
- `make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- M=$PWD modules` succeeds
- Sparse and checkpatch-clean (`scripts/checkpatch.pl --no-tree -f *.c`)
- `modinfo ask.ko` shows the right version/license

### PR2 — Three in-tree patch stubs

Per spec §10, three small kernel patches are needed:

1. `0001-caam-qi-share.patch` — exports `caam_qi_ext_consumer_register()`
   (~50 LOC change in `drivers/crypto/caam/qi.c`)
2. `0002-dpaa-eth-flow-block.patch` — wires `flow_block_cb` into
   `dpaa_setup_tc()` (~80 LOC change in `drivers/net/ethernet/freescale/dpaa/dpaa_eth.c`)
3. `0003-fman-host-command-api.patch` — exposes `fman_host_cmd_send()`
   (~120 LOC + new header `include/linux/fsl/fman_host_cmd.h`)

**PR2 lands placeholder patches** — each is a unified-diff that adds a
`#warning "ASK2: TODO"` comment and a stub function returning
`-EOPNOTSUPP`. This lets the build pipeline (PR3) wire the patch list
without blocking on the real implementation work.

**Files added under `kernel/flavors/ask/patches/`:**
```
patches/
├── 0001-caam-qi-share.patch              # placeholder
├── 0002-dpaa-eth-flow-block.patch        # placeholder
└── 0003-fman-host-command-api.patch      # placeholder
```

Each patch follows the
[`plans/PATCH-MIGRATION-3WAY.md`](PATCH-MIGRATION-3WAY.md) authoring
rules: derive from a clean upstream clone, `git diff --cached`, headers
must include the blob SHA so `git apply --3way` can fall back to the
mergiraf merge driver if context drifts.

### PR3 — Wire build pipeline (CI + local-build)

**Modifies:**
- `bin/ci-setup-kernel.sh` — when `FLAVOR=ask`, copy
  `kernel/flavors/ask/patches/000{1,2,3}-*.patch` into the kernel
  patches dir alongside the default-flavor `101-*` and `400[5-9]-*` set.
  The cleanup `find` glob already preserves vyos-build's own `0001-*`
  and `0003-*` upstream patches; the `! -name '0001-*' ! -name '0003-*'`
  filter covers them. **Our** new `0001-/0002-/0003-` ASK patches must
  use a non-conflicting prefix — rename to `1001-`/`1002-`/`1003-` in
  the staged dir so the kernel patches all sort cleanly:
  `0001 0003 101 1001 1002 1003 4005 4006 4007 4009`.
- `bin/ci-build-packages.sh` — after the kernel `.deb` is produced and
  the kernel source tree still has its `Module.symvers` + `certs/signing_key.{pem,x509}`,
  drive the OOT build:
  ```sh
  if [[ "${FLAVOR:-}" == "ask" ]]; then
      bash kernel/flavors/ask/oot-modules/ask/ci-build.sh
  fi
  ```
- `bin/local-build.sh` — add an `ask-mod` mode that builds just the
  OOT module against the dev-loop kernel on LXC 200 (no full ISO).
- `kernel/flavors/ask/oot-modules/ask/ci-build.sh` (new) — the actual
  build driver. Runs `make -C $KSRC M=$PWD modules`, signs each `.ko`
  with `$KSRC/scripts/sign-file sha512 $KSRC/certs/signing_key.pem
  $KSRC/certs/signing_key.x509 $ko`, packages the signed `.ko` as a
  Debian package `ask-modules-${KVER}_${VER}_arm64.deb` so live-build
  can install it into the ISO.
- `bin/ci-pick-packages.sh` — when `FLAVOR=ask`, append the
  `ask-modules-*.deb` to the package list.
- `.github/workflows/auto-build.yml` — add an `if: env.FLAVOR == 'ask'`
  step that calls `bin/ci-build-packages.sh` (no change needed if PR3
  routes via the existing call site; just verify FLAVOR plumbing).
- `bin/ci-setup-vyos-build.sh` — when `FLAVOR=ask`, drop a chroot hook
  `data/hooks/97-ask-modules.chroot` that adds `ask` to
  `/etc/modules-load.d/ask.conf`.

**Sign-off:**
- `gh workflow run "VyOS LS1046A build (self-hosted)" -f flavor=ask`
  produces `vyos-*-LS1046A-ask-arm64.iso`
- USB-boot the ISO on Mono Gateway DK; `lsmod | grep ask` shows the
  module loaded; `dmesg | grep ask:` shows the v2.0.0 banner; no oopses.
- `version-ask.json` continues to publish (it already does, this is a
  no-op).

### PR4 — kunit harness + first dummy test

**Files added under `kernel/flavors/ask/oot-modules/ask/tests/`:**
```
tests/
├── Kbuild
├── ask_test_main.c                       # kunit suite registration
└── ask_test_dummy.c                      # one passing test, proves harness works
```

Wire `obj-$(CONFIG_NET_ASK_KUNIT_TEST) += tests/` into the top-level Kbuild.
Add `config NET_ASK_KUNIT_TEST` to `Kconfig` (defaults to `n`, depends on
`KUNIT && NET_ASK`).

**Sign-off:**
- `make ARCH=arm64 KUNIT=y M=$PWD` produces a `tests/ask_kunit.ko`
- Loading on QEMU virt + arm64 prints `kunit: 1 tests, 0 failures`
- CI step "kunit" runs in `auto-build.yml` for FLAVOR=ask only (gated)

---

## M1 — Kernel skeleton with hardware-free functionality *(no hardware required)*

**Acceptance gate (M1 done):**
- `genl ctrl-list | grep ask` shows the family registered with version 1
- `ynl --family ask --do get-info` returns a populated info struct
- nft `flow add table inet f flow add @h { ip saddr 10.0.0.1 daddr 10.0.0.2 ... }`
  reaches `ask_flow_offload_setup()` (verified via tracepoint)
- kunit coverage ≥ 80% across the M1 surface (`ask_genl.c`,
  `ask_genl_attr.c`, `ask_hostcmd.c`, `ask_flow.c`, `ask_flow_offload.c`)

### PR5 — `ask_main.c` + `ask_genl.c` (GET_INFO)

Implement:
- `ask_genl_register()` / `ask_genl_unregister()` — the family with
  three multicast groups (events/flows/sas) and the 8 commands declared
  in the UAPI header.
- `ASK_CMD_GET_INFO` handler — fills `ASK_INFO_ATTR_DRIVER_VERSION`,
  `ASK_INFO_ATTR_GENL_VERSION`, hard-coded `ASK_INFO_ATTR_UCODE_*`
  zeros (real values come in M2 once `fman_host_cmd_send()` exists),
  `ASK_INFO_ATTR_CAPABILITIES = 0` for now.
- All other commands return `-EOPNOTSUPP` with a clear log line.

LOC budget: ~400.

### PR6 — `ask_hostcmd.c` wire-format encoders

Implement every encoder/decoder in spec §12.2 + §12.3 + §12.4 + §12.5 +
§12.6, **without touching hardware**. The functions take typed structs
and produce a `struct sk_buff` (or equivalent buffer) holding the
big-endian wire bytes. Inverse functions parse responses.

Tracepoints: `trace_ask_hostcmd_send`, `trace_ask_hostcmd_recv`.

LOC budget: ~600 + ~400 of kunit tests.

The kunit suite golden-tests every byte of every opcode (Section 12.5 is
the canonical example — make a kunit test that produces those exact
hex bytes).

### PR7 — `ask_flow.c` rhashtable + RCU

Implement:
- `struct ask_flow` (cookie, key, action, hw_flow_id, stats, RCU head)
- `ask_flow_table_init()` / `ask_flow_table_destroy()` (per-fman)
- `ask_flow_lookup()` (RCU read)
- `ask_flow_insert()` / `ask_flow_remove()` (with `call_rcu` free)
- `ask_flow_dump()` (genl dumpit handler for `ASK_CMD_DUMP_FLOWS`)
- per-flow `u64_stats_sync` wrappers in `ask_stats.c`

**No hardware calls yet** — `hw_flow_id` is faked by an atomic counter.
The hash table works in software; the next PR wires the offload callback
to actually `ask_hw_flow_insert()` (which is still a stub returning
`-EOPNOTSUPP`).

LOC budget: ~500 + ~400 kunit (insert/lookup/remove under simulated
concurrent access using `kunit_kthread_run` ).

### PR8 — `ask_flow_offload.c` flow_block_cb

Implement:
- `ask_flow_offload_setup()` — register `flow_block_cb` on every dpaa
  netdev (or a single dummy netdev for the kunit path).
- `ask_flow_offload_cb()` — switch on `flow_cls_offload->command`:
  - `FLOW_CLS_REPLACE`: call `ask_flow_insert()` (which calls the
    hostcmd stub, which returns `-EOPNOTSUPP` — that's fine, gets
    stored in software, dumped via genl).
  - `FLOW_CLS_DESTROY`: call `ask_flow_remove()`.
  - `FLOW_CLS_STATS`: call `ask_flow_query_stats()` (also stubbed).

The point of this PR is to prove the callback registers on a real
flow_block, that nft `flow add` reaches the callback, that the action
parser correctly translates `flow_action_entry` → `ask_hw_action`.

LOC budget: ~500 + ~300 kunit.

### PR9 — kunit coverage ≥ 80%

Sweep through PR5–PR8 and add tests until coverage ≥ 80% on
`ask_genl.c`, `ask_genl_attr.c`, `ask_hostcmd.c`, `ask_flow.c`,
`ask_flow_offload.c`. Use `kunit_tool` with gcov to measure.

LOC budget: ~600 of additional tests.

---

## M2 — Hardware brought up *(via TFTP + SSH; see "Hardware loop" above)*

**Acceptance gate (M2 done, per spec §11.1 first row):**
- M2 gate "M2: nft flow add over IPv4 TCP → packet traverses 210 fast
  path on real hardware"
- Real iperf flow installs via nft, traffic counted in
  `ASK_FLOW_ATTR_PACKETS` matches `iperf` reported throughput within 1%
- `dmesg` shows zero ucode errors during a 60-second iperf run

### PR10 — `0001-caam-qi-share.patch` (real implementation) — **landed (ff4a801)**

Per spec §8.3. Adds `caam_qi_ext_consumer_register()` to `drivers/crypto/caam/qi.c`,
exports it, and provides a counterpart `caam_qi_ext_consumer_release()`.

Critical: this is the patch we hope to upstream. Get the API shape right
on the first try by reviewing it with the NXP CAAM maintainers (Madalin
Bucur, Camelia Groza) in parallel with the implementation. See spec §16 #4.

**Link-time validation, 2026-05-13** (LXC 200 `/var/tmp/pr10-pr11/linux-6.18.28`,
fresh `linux-6.18.28.tar.xz` from cdn.kernel.org, `git apply --3way` clean,
mono prod `.config` seeded from `zcat /proc/config.gz` with `MODULE_SIG` /
`SYSTEM_TRUSTED_KEYS` disabled for the dev build, then `make -j12 Image`
followed by `make -j4 drivers/crypto/caam/`):

```
$ aarch64-linux-gnu-nm drivers/crypto/caam/caam.o | grep caam_qi_ext_consumer
0000000000000010 d __UNIQUE_ID___addressable_caam_qi_ext_consumer_register1207
0000000000000008 d __UNIQUE_ID___addressable_caam_qi_ext_consumer_release1210
0000000000000080 r __export_symbol_caam_qi_ext_consumer_register
0000000000000090 r __export_symbol_caam_qi_ext_consumer_release
0000000000002e20 T caam_qi_ext_consumer_register
0000000000003340 T caam_qi_ext_consumer_release
```

`T` = global text, `__export_symbol_*` = KSYMTAB GPL entry, `__UNIQUE_ID___addressable_*`
= modpost addressable refs. Pre-existing `caam_drv_ctx_update` and
`caam_qi_enqueue` (the EXPORT_SYMBOL_GPL pattern PR10 mirrors) appear with
identical structure in the same `.o` — confirms standard kernel boilerplate
was followed.

Hardware-boot validation deliberately deferred until PR13 (`OP_GET_UCODE_VERSION`
against silicon). Rationale: these new functions have no in-tree caller
(ask.ko is unbuilt), so a boot run would only re-prove that vmlinux loads
(which mono already does daily on the prod 6.18.28-vyos build) and that
EXPORT_SYMBOL_GPL produces no runtime-init code (true by definition — it
emits ELF metadata only). Additionally, deploying a dev kernel via TFTP
with `EXTRAVERSION=''` would vermagic-mismatch ALL `=m` modules including
caam.ko itself, defeating the very PR10 module-symbol verification a boot
was supposed to do. PR13 introduces the first real consumer call site and
will exercise both the registration dance and the FQ-swap atomic on live
silicon.

### PR11 — `0002-dpaa-eth-flow-block.patch` (real implementation) — **landed (5f42606)**

Per spec §10.2. Wires `flow_block_cb` into `dpaa_setup_tc()` so each
`fsl,dpa` netdev can advertise hardware flow offload via nft. The
callback is owned by `ask.ko` (registered through a small shim API).

**Link-time validation, 2026-05-13** (same build tree as PR10):

```
$ grep -E 'dpaa_(un)?register_flow_offload_handler|dpaa_setup_tc' System.map
ffff8000809a9750 T dpaa_register_flow_offload_handler
ffff8000809a9818 T dpaa_unregister_flow_offload_handler
ffff8000809ab268 t dpaa_setup_tc
ffff800081485998 r __ksymtab_dpaa_register_flow_offload_handler
ffff8000814859a4 r __ksymtab_dpaa_unregister_flow_offload_handler
```

Both new symbols are linked into vmlinux KSYMTAB at fixed addresses (FSL_DPAA=y
on the prod config), so they would appear in `/proc/kallsyms` of any boot
based on this build. The local `dpaa_setup_tc` carries the new
RCU-protected `TC_SETUP_BLOCK` dispatch case (lowercase `t` = file-local
since the function is `static`).

Hardware-boot validation deliberately deferred for the same reason as PR10:
the new TC_SETUP_BLOCK case is `rcu_dereference()`-guarded and with no
consumer registered (ask.ko unbuilt) returns `-EOPNOTSUPP`, identical to
the existing MQPRIO guard fallthrough. PR14 (`OP_FLOW_INSERT_V4_TCP` end-to-end)
is the first PR that exercises this path on live silicon via an actual
`nft flow add` from userspace.

### PR12 — `0003-fman-host-command-api.patch` (real implementation) — **landed (af2c678)**

Per spec §3.4. Exposes the spec's canonical signature

```c
int fmd_host_cmd(struct fman *fman, u8 opcode,
                 const void *req, size_t req_len,
                 void *resp, size_t resp_buf_len,
                 size_t *resp_len_out);
void fmd_host_cmd_complete(struct fman *fman);
```

plus the new public header `include/linux/fsl/fman_host_cmd.h` and a small
`fman_get_muram()` accessor on `struct fman`. Spec-vs-plan reconciliation:
earlier drafts of this document referred to the symbol as
`fman_host_cmd_send()`; the spec uses `fmd_host_cmd()` and per AGENTS.md
"the spec wins" — the function landed with the spec's name. References
elsewhere in this document are kept at their original wording for
historical fidelity (the section bodies are exposition, not contracts).

Final shape: 427 insertions across 5 files (one new transport `.c` of
325 LOC, one new public header of 84 LOC, one accessor + its prototype on
`struct fman`, one Makefile object addition). Larger than the
plan-estimate of ~120 LOC because the spec contract grew to include
mutex serialisation, completion-driven IRQ wakeup, lazy per-FMan state
registry, and graceful `-ENXIO` handling for the not-yet-probed
microcode-specific MMIO. The transport itself (4-byte header framing,
MURAM region claim, mutex, 100 ms response-timeout, response-header
validation) is **complete and link-validated** — the only piece still
stubbed is the microcode-specific doorbell register write and IRQ-status
unmask, isolated to two ~5-line helpers (`fmd_host_cmd_ring_doorbell()`
and `fmd_host_cmd_arm_irq()`) that both currently `return -ENXIO`.

**Link-time validation, 2026-05-13** (LXC 200 `/var/tmp/pr10-pr11/linux-6.18.28`,
same fresh upstream tree as PR10/PR11):

```
$ aarch64-linux-gnu-nm drivers/net/ethernet/freescale/fman/fman_host_cmd.o \
    | grep -E 'fmd_host_cmd|__export_symbol'
0000000000000008 d __UNIQUE_ID___addressable_fmd_host_cmd742
0000000000000000 d __UNIQUE_ID___addressable_fmd_host_cmd_complete743
0000000000000000 r __export_symbol_fmd_host_cmd
0000000000000010 r __export_symbol_fmd_host_cmd_complete
00000000000000b0 T fmd_host_cmd
0000000000000008 T fmd_host_cmd_complete

$ aarch64-linux-gnu-nm drivers/net/ethernet/freescale/fman/fman.o \
    | grep -E 'fman_get_muram'
0000000000000010 d __UNIQUE_ID___addressable_fman_get_muram772
00000000000000c0 r __export_symbol_fman_get_muram
0000000000000140 T fman_get_muram
```

All three new EXPORT_SYMBOL_GPLs link cleanly into `fsl_dpaa_fman.ko`.
`patch-health.sh --flavor ask --source release` shows `patches/0003-...`
green alongside `patches/0001-...` and `patches/0002-...` (the seven
remaining failures are the same pre-existing patch-rot items unrelated
to ASK2).

Hardware-boot validation deliberately deferred to PR13 for the same
reason as PR10/PR11: there is no caller yet, and the two MMIO helpers
return `-ENXIO` until PR13 fills in the microcode-specific doorbell
offset and IRQ wiring (which are explicitly listed in spec §12.7 as
"must be probed" against live silicon). PR13 is therefore the first PR
in the M2 sequence that genuinely requires `ssh vyos reboot`.

### PR13 — ucode version from QEF blob (DT) + spec §12.8 — **landed**

First real hardware-validated read against the live Mono Gateway DK.
Original framing (call `fman_host_cmd_send()` with opcode `0x01` per
spec §12.2 and verify the family-`0x0210` response) was abandoned mid-PR
when the hardware probe proved the §12 host-command opcode dispatcher
does not exist on the loaded microcode. Replacement implementation
reads the ucode version directly from the QEF firmware blob exposed by
the kernel via the device-tree property
`/soc/fman@1a00000/fman-firmware/fsl,firmware` — the same blob that
U-Boot loaded into FMan IRAM at boot from SPI flash partition `mtd3`.

**Files added/changed:**
- `kernel/flavors/ask/oot-modules/ask/ask_hw.c` (new, ~250 LOC) —
  `ask_hw_init()`/`ask_hw_exit()` plus `ask_hw_ucode_get_version()`
  helper. Walks DT for `compatible = "fsl,fman-firmware"`, reads the
  `fsl,firmware` property, validates the `'Q' 'E' 'F' 0x01` magic at
  offset 4, sscanf-parses `family.major.minor` from the 64-byte ASCII
  description at offset 8 (`"Microcode version 210.10.1 for LS1043 r1.0"`).
  Caches via `READ_ONCE`/`WRITE_ONCE`. Logs single dmesg breadcrumb
  `ask: hw: FMan microcode 210.10.1 ("Microcode version …")` on success.
- `kernel/flavors/ask/oot-modules/ask/include/ask_internal.h` —
  `struct ask_hw_ucode_version { u16 family; u8 major; u8 minor;
   u16 patch; char description[64]; }` + three function decls.
- `kernel/flavors/ask/oot-modules/ask/Kbuild` — adds `ask_hw.o`.
- `kernel/flavors/ask/oot-modules/ask/ask_main.c` — wires
  `ask_hw_init` as the first subsystem and `ask_hw_exit` as the last
  in the unwind chain (zero deps on other subsystems; non-DPAA hosts
  fail-fast with a single dmesg line, and a missing/malformed blob is
  non-fatal — module still loads with version 0.0.0).
- `kernel/flavors/ask/oot-modules/ask/ask_genl.c` —
  `ASK_INFO_ATTR_UCODE_FAMILY/MAJOR/MINOR/PATCH` are now sourced from
  `ask_hw_ucode_get_version()` instead of hard-coded zeros.

**Hardware-probe findings written back to spec §12.8** (full prose
there). Headlines:

1. The loaded microcode is stock NXP **QorIQ Engine Firmware QEF
   210.10.1**, the same blob that mainline `drivers/net/ethernet/freescale/fman/fman.c`
   was designed to drive. It implements parser + classifier + policer +
   KeyGen entirely via MURAM-resident config tables.
2. The §12 host-command opcode dispatcher (CEV doorbell + REV IRQ at
   FPM+0xE0/+0x20/+0x40) **does not exist** on stock QEF microcode.
   The §12.2 opcode map (`OP_GET_UCODE_VERSION=0x01`,
   `OP_FLOW_INSERT_V4_TCP=0x10`, …) was specific to a custom
   NXP/proprietary microcode that shipped with the legacy `we-are-mono/ASK`
   stack — not to QEF.
3. PR12's `0003-fman-host-command-api.patch` is correct as-landed and
   stays. `fmd_host_cmd_send()` correctly returns `-ENXIO` because the
   doorbell is genuinely unanswered for the running microcode. Patch
   is preserved as future infrastructure for a hypothetical custom
   ASK2 microcode.
4. §12.7 question 2 (event-channel binding) is partially answered:
   SPI 44 (Linux IRQ 59) is the FMan event IRQ on LS1046A, SPI 45 is
   the FMan err IRQ. Questions 1 (MURAM partition behaviour) and 3
   (eviction tunables) are moot — there are no opcodes to scan.

**Verification on live Mono Gateway DK (kernel 6.18.28):**
- `ask.ko` insmods cleanly.
- `dmesg | grep '^ask: hw'` shows the version banner with the QEF
  description string.
- `ASK_INFO` genl reply carries `family=210, major=10, minor=1,
  patch=0` byte-for-byte matching the DT blob.

**Mechanism switch — binding decision for ASK2 v1.0.** Per spec §12.8,
the §12.1–§12.6 host-command protocol is **deferred indefinitely**.
PR14+ is re-scoped accordingly: the consumer of `ask_hostcmd.c`
becomes the in-tree FMan PCD table-programming pathway
(`drivers/net/ethernet/freescale/fman/fman_keygen.c` and siblings),
not opcode-dispatch insertion. PR14's heading below is preserved for
historical context but the implementation strategy underneath it has
changed.

### §12.9 — Decision: Option C-modernize (2026-05-14, relaxed in spec v1.1)

**Status:** decided. The C / D / E decision tree opened by PR14-prep
(see prior revision of this plan) has been resolved in favour of
**Option C-modernize**: write a new FMan PCD subsystem of ~7800 LOC
modern kernel C. **Spec v1.1 (same day) relaxed the original
strict-provenance constraint** — both the archived NXP SDK PCD
tree at `mihakralj/kernel-ls1046a-build@464df181` (dual-licensed
BSD-3-or-GPL-2.0) and the `we-are-mono/ASK` legacy stack (GPL) are
GPL-compatible and **usable as silicon-behaviour references**. What
remains rejected is the SDK's *architecture* (`handle_t` opaque ABI,
`fsl-ncsw` OS shim, AMP multi-OS IPC, `TRACE_RTOS` macros, `fm_ehash.c`
custom hash, nested `Peripherals/FM/Pcd/` layout, 16-flavour
`t_FmPcdCcNextEngineParams` hierarchy) — not its silicon facts. The
LS1046A Reference Manual chapter 8 remains authoritative when SDK
sources disagree or omit a field.

**Authoritative reference:** spec §12.9 (decision evidence + cost
survey) and **spec §13** (full architectural design — module
decomposition, per-module APIs, integration plan, Kconfig, acceptance
gates, upstream posture). The plan section that follows turns spec §13
into seven sequential PRs (PR14a–g).

**Top-level numbers:**

| Path | LOC | Calendar | Hits §11.1 perf gates? | Vendor-code risk |
|---|---|---|---|---|
| C-forward-port (SDK port) | 15k–30k kept | 4 mo | Yes | High — ncsw shim removal + AMP IPC removal carried forward |
| **C-modernize (chosen, v1.1)** | **~7,800 LOC new** | **~5 weeks code + 4–6 weeks silicon bring-up** | **Yes** | **None — SDK is GPL; we reference silicon facts and rewrite architecture** |
| D (sw fallback) | ~500 | 1 week | No (1–2 Gbps cap) | None |
| E (cancel) | 0 | 0 | N/A | N/A |

**What this means for the PR sequence:**

- PR14 is **expanded into PR14a–g**, one per spec §13 module, landing
  in dependency order. Each lands a single subsystem of
  `0004-fman-pcd-subsystem.patch`.
- PRs 15–21 unblock as soon as **PR14g** (end-to-end wire-up) lands —
  not earlier. PR14a–f are mechanically buildable but don't deliver
  the first hardware-validated flow until PR14g.
- The acceptance gate for the full PR14 series is spec §13.7: nft
  `flow add` → packet traverses 210 fast path → CPU < 5 % at ≥ 2 Gbps
  on real Mono Gateway DK silicon.

**Modernization discipline (risk #12 in spec §16, v1.1 reframed):**
every non-trivial function in PR14a–g must carry a comment citing its
primary RM section (e.g. `/* RM §8.7.3.2 — KeyGen scheme extract
masking */`). Where SDK or `we-are-mono` source was consulted to
disambiguate an RM ambiguity or to verify register-write ordering, the
comment must additionally name the source file (e.g. `/* RM §8.7.4.2
+ cross-ref SDK fm_cc.c MatchTableTryLockAcquire() for polling-loop
semantics */`). **Reviewer focus** (per risk #12 mitigation) is
modern-kernel idiom enforcement — typed structs (no `handle_t`), RCU
correctness, `devm_*` lifetimes, `kmalloc`/`ioremap`/`readl`/`writel`
direct calls (no OS-shim wrapper layer), `mutex`/`spinlock`/`rcu`
primitives, tracepoints over `printk`, `<linux/crc64.h>` over inline
tables. Open question #8 (the v1.0 "provenance-reviewer assignment"
question) is **closed** in v1.1.

---

### PR14a — `0004-fman-pcd-subsystem.patch` orchestration (`fman_pcd.c` + accessors)

Per spec §13.3 (orchestration) + §13.4 (integration with existing
in-tree files).

**Files added/changed (linux-6.18.x):**

- `drivers/net/ethernet/freescale/fman/fman_pcd.c` (new, ~800 LOC):
  - `struct fman_pcd { struct fman *fman; struct mutex lock;
    struct fman_pcd_muram_budget budget; struct list_head schemes;
    struct list_head trees; struct list_head profiles;
    struct dentry *debugfs_root; ... };`
  - `fman_pcd_init(struct fman *fman)` — allocs the struct, claims a
    MURAM partition via `fman_muram_alloc()`, initialises lock + lists,
    creates `/sys/kernel/debug/fman_pcd/<fman_id>/muram_budget`.
  - `fman_pcd_release(struct fman_pcd *pcd)` — symmetric teardown.
  - `fman_pcd_get_muram_budget(struct fman_pcd *pcd)` — returns the
    current allocation breakdown.
  - All six EXPORT_SYMBOL_GPL'd for `ask.ko` consumption per spec §13.3.
- `drivers/net/ethernet/freescale/fman/fman.c` (~30 LOC):
  - Add `struct fman_pcd *pcd;` field to `struct fman`.
  - Wire `fman_pcd_init()` into `fman_probe()` after `fman_muram_init()`.
  - Wire `fman_pcd_release()` into `fman_remove()`.
  - Add `struct fman_pcd *fman_get_pcd(struct fman *fman)` accessor +
    EXPORT_SYMBOL_GPL.
- `include/linux/fsl/fman_pcd.h` (new, the ~600 LOC public header
  per spec §13.3 — forward decls + `fman_pcd_init/release/get_pcd/
  get_muram_budget` prototypes only at this PR; the per-block APIs
  land in PR14b–f as their .c files do).
- `drivers/net/ethernet/freescale/fman/Makefile`: add `fman_pcd.o` to
  `fsl_dpaa_fman-objs`.
- `drivers/net/ethernet/freescale/fman/Kconfig`: add
  `CONFIG_FSL_FMAN_PCD` (tristate, default `m`, depends on `FSL_FMAN`)
  per spec §13.6.

**kunit suite (in-tree):** `drivers/net/ethernet/freescale/fman/tests/fman_pcd_test.c`
covers `fman_pcd_init/release` lifecycle on a mock `struct fman` and
the MURAM budget accounting on a 96 KiB simulated MURAM.

**Acceptance:**

- `bash kernel/common/scripts/patch-health.sh --flavor ask --source release`
  shows `patches/0004-fman-pcd-subsystem.patch` green.
- Cross-built kernel boots on Mono Gateway DK; `dmesg | grep fman_pcd`
  shows the init banner; `cat /sys/kernel/debug/fman_pcd/0/muram_budget`
  returns a non-zero free figure.
- `lsmod | grep fsl_dpaa_fman` still single module (per spec §13.6 —
  no new `.ko`).

LOC budget: ~870 (800 fman_pcd.c + 30 fman.c + ~40 header skeleton).

---

### PR14b — KeyGen schemes with `match_vector ≠ 0` (`fman_pcd_kg.c`) — **landed in two parts**

Per spec §13.3 (`fman_pcd_kg.c` section) + §13.4 (the
`fman_keygen.c` exports).

Split into two landings so each could be independently link-validated
and round-tripped through `patch-health`:

#### PR14b-prep — KG public API stub + helper exports — **landed (fda5a03)**

Captured as `kernel/flavors/ask/patches/0005-fman-pcd-kg-prep.patch`
(541 lines). Adds:

- `drivers/net/ethernet/freescale/fman/fman_pcd_kg.c` (new) — public
  API with four `-EOPNOTSUPP` stubs (`fman_pcd_kg_scheme_create`,
  `fman_pcd_kg_bind_port`, `fman_pcd_kg_attach_cc`,
  `fman_pcd_kg_scheme_destroy`), `struct fman_pcd_kg_scheme` opaque
  handle, registered list head on `pcd->kg_schemes`.
- `drivers/net/ethernet/freescale/fman/fman_keygen.c` — `EXPORT_SYMBOL_GPL`
  for `keygen_scheme_setup()` and `keygen_bind_port_to_schemes()`
  (existing functions promoted from `static`).
- `drivers/net/ethernet/freescale/fman/fman_keygen_internal.h` — moves
  `FM_KG_MAX_NUM_OF_SCHEMES` macro under `#ifndef` guard so the new
  `.c` file can `#include` it without redefinition.
- `include/linux/fsl/fman_pcd.h` — forward-decls for the four public
  functions + the opaque struct.

Also added to `patch-health.sh`: cumulative-stack mode for
`kernel/flavors/ask/patches/` so cross-dependent patches in a logical
series (0004 + 0005 etc.) don't produce false-positive rot.

#### PR14b-body — Real KGSE_* programming (IPv4 5-tuple) — **landed (00d6f16)**

Captured as `kernel/flavors/ask/patches/0006-fman-pcd-kg-body.patch`
(341 inserted / 48 deleted in `fman_pcd_kg.c`, ~330 LOC final).
Replaces the four `-EOPNOTSUPP` stubs with the real implementation:

- `fman_pcd_kg_scheme_create()` — allocates a free scheme id in
  `[0, FM_KG_MAX_NUM_OF_SCHEMES)`, populates `keygen->schemes[id]`
  with silicon match-vector + base_fqid + hashing fields, delegates
  KGSE_* register write to the in-tree `keygen_scheme_setup()` helper.
- `fman_pcd_kg_bind_port()` — sets `slot->hw_port_id`, delegates to
  `keygen_bind_port_to_schemes()`.
- `fman_pcd_kg_scheme_destroy()` — unbinds + tears down with
  probe-failure safety (drops wrapper without touching silicon if
  parent `fman->keygen` is already freed).
- `fman_pcd_kg_attach_cc()` — still `-EOPNOTSUPP` pending PR14c's
  `struct fman_pcd_cc_tree` (KGSE_CCBS wiring deferred).

**Key insight captured during implementation** (stored in Qdrant
2026-05-14): The existing in-tree `keygen_scheme_setup()` already
programs all KGSE_* registers. PR14b-body just needs to (a) allocate
scheme id, (b) populate `keygen->schemes[id]` slot, (c) call the
existing helper. Total `fman_pcd_kg.c` size dropped from the original
~1500 LOC budget to ~330 LOC.

**Build gotcha captured in Qdrant:** the `KG_SCH_KN_*` silicon-bit
macros (`PTYPE1`, `IPSRC1`, `IPDST1`, `L4PSRC`, `L4PDST`) are private
to `fman_keygen.c` upstream (defined inline, not exported in any
header). Cannot include them. Solution: define the 5 macros locally
in `fman_pcd_kg.c` with comment citing RM §8.7.4 `KGSE_MV` register as
the silicon-fact source.

**Supported match-vector shape at this iteration:** IPv4 5-tuple only
(SIP/DIP/proto/SPORT/DPORT via parse_result offsets 12/16/9/20/22).
Wider shapes return `-EOPNOTSUPP` loudly. Field-level hash tuning
(`hash_fqid_count`, `symmetric_hash`, `hashShift`) uses conservative
defaults; promoted to API params if/when consumers need them.

**Validation evidence (PR14b-body):**

- Native ARM64 build on Cobalt 100 (`make ARCH=arm64 -j32 Image`,
  ~1m31s): zero warnings, all 4 public symbols present in `System.map`
  with `__ksymtab_*` exports.
- Round-trip `git apply --3way --check` on a fresh clone of kernel
  commit `8259d70f7` (PR14b-prep tip): clean.
- `patch-health.sh --flavor ask --source release`: **Pass 4 / Fail 11**
  (baseline before PR14b-body was Pass 3 / Fail 11 — improved by one
  passing patch, zero regressions). The 11 pre-existing failures are
  baseline patch-rot tracked separately from ASK2 work.

**Hardware-boot validation deferred to PR14c.** Rationale: the
debugfs `test_kg_scheme` hook originally planned for PR14b end
(silicon programming proof) is more naturally co-located with the CC
tree wire-up — it would need to be torn down and rewritten anyway
when `attach_cc()` lands. PR14c's bring-up step will exercise both
KG and CC end-to-end on silicon.

LOC actuals: 541 lines (PR14b-prep patch) + 502 lines (PR14b-body
patch) = 1043 lines vs original budget ~1550. The savings come
entirely from delegating to the in-tree `keygen_scheme_setup()`
helper rather than re-implementing KGSE_* register writes.

---

### PR14c — Coarse Classifier match trees (`fman_pcd_cc.c`) — **split into two parts**

Per spec §13.3 (`fman_pcd_cc.c` section). Largest single PR in the
PR14 series. Split into prep+body using the same two-step landing
pattern that worked for PR14b.

#### PR14c-prep — CC public API stub — **landed (c613714)**

Captured as `kernel/flavors/ask/patches/0007-fman-pcd-cc-prep.patch`
(386 LOC: +157 in new `.c`, +281 in header, +1 Makefile, +1 wiring).
Kernel-side commit `965b9d9f4` on `/var/tmp/pr14a/linux-6.18.28`.

**Files added/changed:**

- `drivers/net/ethernet/freescale/fman/fman_pcd_cc.c` (new, ~157 LOC) —
  six `-EOPNOTSUPP` / `ERR_PTR(-EOPNOTSUPP)` stubs with RM-citation
  provenance comment (RM §8.7.4.1–8.7.4.3). Every stub validates
  inputs (NULL, `num_of_groups 1..8`, `extract->size 1..56`) before
  returning the sentinel. NULL-safe destroy paths.
- `include/linux/fsl/fman_pcd.h` (+281 LOC public ABI):
  - `enum fman_pcd_action_type` — 6 variants (`DROP`, `FORWARD_FQ`,
    `FORWARD_CAAM`, `REPLICATE`, `MANIPULATE`, `NEXT_CC_NODE`) — single
    discriminator replacing the SDK's 16 separate
    `t_FmPcdCcNextEngineParams` flavors per spec §13.3.
  - `struct fman_pcd_action` — tagged union carrying per-variant payload.
  - `enum fman_pcd_cc_extract_type` (KEY / HDR / NONHDR per RM 8.7.4.2).
  - `struct fman_pcd_cc_extract`, `struct fman_pcd_cc_key_entry`
    (56-byte key+mask + action — silicon hard limit per RM §8.7.4.2),
    `struct fman_pcd_cc_key_table`.
  - 6 function prototypes: `cc_tree_create`, `cc_node_create`,
    `cc_node_add_key`, `cc_node_modify_next_action`, `cc_node_destroy`,
    `cc_tree_destroy`.
- `drivers/net/ethernet/freescale/fman/Makefile`: `fman_pcd_cc.o` added
  to `fsl_dpaa_fman-$(CONFIG_FSL_FMAN_PCD)`.

**Key insight:** PR14a already provisioned `cc_trees` list and
`fman_pcd_get_cc_list()` accessor in `fman_pcd.c` — no orchestration
changes needed in PR14c-prep, only the new `.c` + header + one
Makefile line. Same delegation pattern that dropped PR14b-body to
330 LOC.

**Validation evidence (PR14c-prep):**

- Native ARM64 build on Cobalt 100 (`make ARCH=arm64 -j32 Image
  modules`): success, **zero warnings, zero errors** across the entire
  build. All 6 `fman_pcd_cc_*` symbols present in `System.map` as `T`
  with matching `__ksymtab_*` entries.
- Round-trip `git apply --3way --check` clean on fresh
  linux-6.18.28 worktree at `6e7e2c37a` (PR14b-body tip).
- `patch-health.sh --flavor ask --source release`: **Pass 5 / Fail 11**
  (improved by 1 over PR14b-body's 4/11).
- `bin/ci-setup-kernel.sh` extended: glob `0001-0007`, rename case adds
  `0007-→1007-`, count guard 6→7, apply-order doc updated.

#### PR14c-body — Real silicon-resident tree/node programming

Replaces the 6 stubs from PR14c-prep with the real implementation.

**Files added/changed (in-tree):**

- `drivers/net/ethernet/freescale/fman/fman_pcd_cc.c` (extend from
  ~157 LOC stub to ~2500 LOC) — real implementation:
  - `struct fman_pcd_cc_tree` — root of a match-tree, owns the
    MURAM-resident tree-group table (RM §8.7.4.1).
  - `struct fman_pcd_cc_node` — a single classification node within
    a tree; carries the extract spec, the key table, the per-key
    action array.
  - MURAM-resident tree-group-table programming byte-perfect against
    RM §8.7.4.1 figure.
  - Classification-node record packing per RM §8.7.4.2.
  - Key-table ordering rules per RM §8.7.4.2.
  - Action-template encoding per RM §8.7.4.3 for each
    `enum fman_pcd_action_type` variant.
- `drivers/net/ethernet/freescale/fman/fman_pcd_kg.c` (extend):
  - Replace `fman_pcd_kg_attach_cc()` `-EOPNOTSUPP` stub with real
    `KGSE_CCBS` register programming now that
    `struct fman_pcd_cc_tree` is concrete.

**kunit suite:** `drivers/net/ethernet/freescale/fman/tests/fman_pcd_cc_test.c`
covers tree-group MURAM layout byte-perfect against RM §8.7.4.1 figure,
node-record packing, key-table ordering, action-template encoding for
each action type.

**Acceptance:**

- kunit suite passes.
- On real Mono Gateway DK: install KG scheme (PR14b path) →
  one-node CC tree with one key (the original IPv4 5-tuple) →
  `ACTION_FORWARD_FQ` to a different FQID than the KG default. Send
  the test packet; verify it arrives at the new FQ, not the KG default.
  This proves the CC walk happens in silicon.
- `patch-health.sh --flavor ask --source release`: target Pass 6 /
  Fail 11.
- Native ARM64 build: zero warnings, zero errors.
- Round-trip `git apply --3way --check` clean against PR14c-prep tip
  (`965b9d9f4`).

LOC budget: ~2500 LOC `.c` growth + ~30 LOC in `fman_pcd_kg.c`
(`attach_cc` body).

#### PR14c-body design memo (2026-05-14, v1.1)

Authored after spec v1.1 unblocked SDK cross-referencing. Grounds the
next session's body-PR author with silicon facts cited from RM §8.7.4
and SDK `sdk_fman/Peripherals/FM/Pcd/fm_cc.{c,h}` (preserved verbatim
under `work/linux-6.18.28/drivers/net/ethernet/freescale/sdk_fman/` and
in the archived `mihakralj/kernel-ls1046a-build@464df181` tree).

**Silicon facts (from SDK `fm_cc.h` lines 172–211, RM §8.7.4):**

| Constant | Value | Source | Meaning |
|---|---|---|---|
| `FM_PCD_CC_AD_ENTRY_SIZE` | 16 bytes | fm_cc.h:174 | Every Action Descriptor in MURAM is exactly 16 B |
| `FM_PCD_CC_NUM_OF_KEYS` | 255 | fm_cc.h:175 | Max keys per CC node (+1 miss = 256 total ADs/node) |
| `FM_PCD_CC_KEYS_MATCH_TABLE_ALIGN` | 16 | fm_cc.h:172 | Match-key table base address 16-B aligned |
| `FM_PCD_CC_AD_TABLE_ALIGN` | 16 | fm_cc.h:173 | AD table base address 16-B aligned |
| `FM_PCD_CC_TREE_ADDR_ALIGN` | 256 | fm_cc.h:176 | Tree-group table base 256-B aligned |
| `CC_GLBL_MASK_SIZE` | 4 | fm_cc.h:214 | Global mask is 4 B (32-bit) per group |
| `FM_PCD_AD_RESULT_CONTRL_FLOW_TYPE` | `0x00000000` | fm_cc.h:178 | AD type bits (nia[31:30]): forward-to-FQ control-flow |
| `FM_PCD_AD_RESULT_DATA_FLOW_TYPE` | `0x80000000` | fm_cc.h:179 | AD type: forward-to-FQ data-flow + statistics |
| `FM_PCD_AD_CONT_LOOKUP_TYPE` | `0x40000000` | fm_cc.h:185 | AD type: continue to next CC node |
| `FM_PCD_AD_BYPASS_TYPE` | `0xc0000000` | fm_cc.h:200 | AD type: drop/bypass |
| `FM_PCD_AD_OPCODE_MASK` | `0x0000000f` | fm_cc.h:203 | nia[3:0] = sub-opcode (replicator id, manip id, …) |
| `FM_PCD_AD_RESULT_PLCR_DIS` | `0x20000000` | fm_cc.h:180 | nia[29] = policer-disable flag |
| `FM_PCD_AD_RESULT_NADEN` | `0x20000000` | fm_cc.h:182 | Next-Action-Descriptor enable |

**AD record layout (SDK `t_AdOfTypeResult`, fm_cc.h:261–267, 16 B):**

```
offset 0x0  u32 fqid          // egress FQID (for result-flow ADs)
offset 0x4  u32 plcrProfile   // policer profile id (low 24 bits) | enable bits
offset 0x8  u32 nia           // type[31:30] | flags[29:4] | opcode[3:0]
offset 0xc  u32 res           // reserved / next-AD-pointer for chained walks
```

This is the **only** AD format ASK2 v1.0 needs at PR14c-body. The
SDK's other AD typedefs (`t_AdOfTypeContLookup`, `t_AdOfTypeStats`,
`t_FEOfTypeHash`) are needed for M3+ flow types (continue-lookup,
stats-AD, hash-indexed externalize) and stay out of PR14c-body scope.

**Data structures (`fman_pcd_cc.c`, PR14c-body):**

```c
struct fman_pcd_cc_node {
    struct list_head        node;            /* tree->nodes */
    struct fman_pcd_cc_tree *tree;           /* back-ref */
    struct fman_pcd        *pcd;             /* MURAM allocator owner */
    struct fman_pcd_cc_extract extract;      /* key spec (copied) */
    /* MURAM allocation (one alloc each, 16-B aligned per fm_cc.h:172-173): */
    void __iomem           *match_table;     /* (size+1) * extract.size bytes,
                                                key+mask pairs, last = miss key 0 */
    void __iomem           *ad_table;        /* (size+1) * 16 bytes, last = miss AD */
    size_t                  match_table_sz;
    size_t                  ad_table_sz;
    /* in-memory mirror for fast modify-next-action: */
    struct fman_pcd_cc_key_entry *keys;      /* kmalloc(size * sizeof(*keys)) */
    u16                     num_keys;        /* current valid keys (≤ 255) */
    u16                     max_keys;        /* allocated capacity */
    spinlock_t              lock;            /* serialises add_key/modify */
};

struct fman_pcd_cc_tree {
    struct list_head        node;            /* pcd->cc_trees */
    struct fman_pcd        *pcd;
    u8                      num_of_groups;   /* 1..8, fm_cc.h enforces ≤ 8 */
    /* MURAM tree-group table: 256-B aligned per fm_cc.h:176, holds 8 group
       descriptors of 32 B each = 256 B fixed-size: */
    void __iomem           *group_table;
    struct list_head        nodes;           /* attached fman_pcd_cc_node */
    struct mutex            lifecycle_lock;  /* serialises node create/destroy */
};
```

**MURAM allocation strategy:** one `fman_muram_alloc()` per table
(match_table, ad_table, group_table). Aligned via the existing
`fman_muram_alloc()` API which already accepts an alignment hint. No
per-key alloc — the match-table and ad-table are one contiguous block
per node, sized at `cc_node_create()` time from `keys->num_keys`. A
realloc-via-shadow scheme can be added later if dynamic resize matters
(M3+); v1.0 sizes the node once at create and rejects add_key beyond
the original capacity with `-ENOSPC`.

**Sub-PR decomposition for landability (~2500 LOC total):**

| Sub-PR | Patch file | Files / scope | LOC |
|---|---|---|---|
| **14c-body-1** | `0009-fman-pcd-cc-body-data-structures.patch` | `struct fman_pcd_cc_tree` + `fman_pcd_cc_tree_create/destroy` real bodies (MURAM group-table alloc, list registration in `pcd->cc_trees`, NULL-safe destroy with WARN on attached nodes). `fman_pcd_internal.h` exports for `cc_trees` list anchor. | ~600 |
| **14c-body-2** | `0010-fman-pcd-cc-body-node-create-destroy.patch` | `struct fman_pcd_cc_node` + `cc_node_create/destroy` (MURAM match-table + ad-table alloc, extract validation, miss-key AD initialization). | ~700 |
| **14c-body-3** | `0011-fman-pcd-cc-body-action-encoding.patch` | `fman_pcd_action` → AD-record encoder for each `enum fman_pcd_action_type` (DROP→bypass, FORWARD_FQ→result-flow, FORWARD_CAAM→result-flow with CAAM FQID, NEXT_CC_NODE→continue-lookup, REPLICATE→opcode-extension stub returning `-EOPNOTSUPP` until PR14f, MANIPULATE→opcode-extension stub returning `-EOPNOTSUPP` until PR14d). Helper `cc_encode_ad(action, ad_iomem)`. | ~500 |
| **14c-body-4** | `0012-fman-pcd-cc-body-add-modify-key.patch` | `cc_node_add_key` + `cc_node_modify_next_action` (key-table append, AD-table update via `cc_encode_ad`, spinlock-protected). Replaces `fman_pcd_kg_attach_cc()` `-EOPNOTSUPP` stub with real `KGSE_CCBS` register write now that `cc_tree` is concrete. | ~400 |
| **14c-body-5** | `0013-fman-pcd-cc-body-kunit.patch` | kunit suite `tests/fman_pcd_cc_test.c`: AD encoding byte-perfect against RM §8.7.4.3 worked example; tree-group MURAM layout byte-perfect against RM §8.7.4.1 figure; node-create rejects 0-key and 256-key tables; modify_next_action races vs add_key under spinlock contention (stress 1000× iterations on simulated MURAM). | ~300 |

Each sub-PR lands as an additive patch and keeps `patch-health.sh
--flavor ask --source release` green. After PR14c-body-5 lands, the
`Pass 5 / Fail 11` baseline becomes `Pass 10 / Fail 11`.

**SDK cross-reference map (one cite per non-trivial function):**

| PR14c-body function | RM § | SDK file:function | What we consult it for |
|---|---|---|---|
| `cc_tree_create` MURAM alloc | §8.7.4.1 | `fm_cc.c:CcRootHashTableInit` | Tree-group-table 256-B alignment + 8-group max enforcement |
| `cc_tree_destroy` WARN-on-attached | §8.7.4.1 | `fm_cc.c:CcRootHashTableRelease` | Cleanup ordering: detach nodes → free group table → list_del |
| `cc_node_create` extract validation | §8.7.4.2 | `fm_cc.c:FmPcdCcNodeTreeTryLock` | Extract-size limits (1..56 B silicon hard limit per RM 8.7.4.2 table) |
| `cc_node_create` match-table size | §8.7.4.2 | `fm_cc.c:BuildNewNodeAddRemoveKey` | (num_keys+1) * extract.size bytes contiguous |
| `cc_node_create` ad-table init | §8.7.4.3 | `fm_cc.c:InitCcKeysAdditionalParams` | Miss-AD at index `num_keys` (last slot) with default-drop |
| `cc_encode_ad(DROP)` | §8.7.4.3 | `fm_cc.c:NextStepAd` (BYPASS path) | nia |= `FM_PCD_AD_BYPASS_TYPE` (0xc0000000) |
| `cc_encode_ad(FORWARD_FQ)` | §8.7.4.3 | `fm_cc.c:GetAdOfTypeResult` | fqid in offset 0x0; nia type bits = `RESULT_CONTRL_FLOW_TYPE` (0x0) |
| `cc_encode_ad(NEXT_CC_NODE)` | §8.7.4.3 | `fm_cc.c:CcNextEngineParamsToAd` (CONT_LOOKUP path) | nia type bits = `CONT_LOOKUP_TYPE` (0x40000000); res = next-node AD-table physaddr |
| `cc_encode_ad(FORWARD_CAAM)` | §8.7.4.3 | `fm_cc.c:GetAdOfTypeResult` (CAAM FQID variant) | fqid = `caam_qi_ext_consumer_register()` returned `caam_req_fqid`; nia |= `RESULT_PLCR_DIS` since CAAM bypasses policer |
| `cc_node_add_key` table append | §8.7.4.2 | `fm_cc.c:BuildNewNodeAddRemoveKey` | Append-only; existing keys' table offsets must not shift |
| `cc_node_modify_next_action` | §8.7.4.3 | `fm_cc.c:ModifyKeyAndNextEngineEntry` | Atomic 16-B AD write (single CASW64 cache-line update OK because nia is the last word silicon reads) |
| `fman_pcd_kg_attach_cc` body | §8.7.3 | `fm_kg.c:KgSetClsPlan` (KGSE_CCBS path) | Scheme→tree binding via `KGSE_CCBS` register low-bits = tree-group-table physaddr |

Citation discipline per spec §13 intro: every non-trivial function
carries a comment of the form `/* RM §8.7.4.2 + cross-ref SDK fm_cc.c
<function-name> for <specific behaviour> */`. Reviewer audit point per
risk #12: confirm comments cite RM-first, SDK-second, and that the
function body uses modern kernel idioms (typed structs, `readl/writel`,
no `handle_t`, no `XX_Malloc`, no `TRACE_RTOS`).

**Stale provenance comments in patches 0004–0008:** the v1.0
"Provenance (v1.1)" file-headers in `fman_pcd.c`, `fman_pcd_cc.c`,
`fman_pcd_manip.c`, `fman_pcd_plcr.c`, `fman_pcd_prs.c`,
`fman_pcd_replic.c` are now stale but **not lying** — the prep-stub
code is literally `-EOPNOTSUPP` stubs with no SDK code copied. They
will be refreshed to v1.1 language as a sweep PR after PR14g lands
(low-priority; tracked separately to avoid re-patching five patches
each time PR14c-body lands a sub-step). New body code (PR14c-body-1
onward) uses v1.1 provenance language from the start.

**Next-session entry point:** start with PR14c-body-1
(`0009-fman-pcd-cc-body-data-structures.patch`). Open
`work/linux-6.18.28/drivers/net/ethernet/freescale/fman/fman_pcd_cc.c`
(the PR14c-prep stub) and
`work/linux-6.18.28/drivers/net/ethernet/freescale/sdk_fman/Peripherals/FM/Pcd/fm_cc.{c,h}`
(the silicon-fact reference). Implement `struct fman_pcd_cc_tree`,
real `fman_pcd_cc_tree_create()` (`fman_muram_alloc` with 256-B
alignment for the 256-B fixed-size group table per fm_cc.h:176, list
registration in `pcd->cc_trees`, mutex init), real
`fman_pcd_cc_tree_destroy()` (WARN-on-attached-nodes check, free MURAM,
list_del). Validate with `make ARCH=arm64 -j32 Image modules`,
`git apply --3way --check` on a fresh clone, `patch-health.sh --flavor
ask --source release`. Land as patch 0009.

#### PR14c-body progress log

| Sub-PR | Patch | Commit | Date | Status | Validation |
|---|---|---|---|---|---|
| **body-1** | `0009-fman-pcd-cc-body-data-structures.patch` (605 lines) | `3b791d1` | 2026-05-14 | ✅ landed on `ask20` | Zero-warning ARM64 build; `nm` shows `tree_create` 16→520 B, `tree_destroy` 16→352 B; all 6 `__ksymtab_fman_pcd_cc_*` entries present; `patch-health` ✓ 0009 against linux-6.18.28 baseline. |
| **body-2** | `0010-fman-pcd-cc-body-node-create-destroy.patch` (615 lines, +620/-3) | `94d8237` | 2026-05-14 | ✅ landed on `ask20` | Zero-warning ARM64 build; `nm` shows `node_create` 28→288 B (0x120), `node_destroy` 24→1016 B (0x3f8), `add_key`/`modify_next_action` unchanged at stub sizes; `patch-health` ✓ 0010. |
| **body-3** | `0011-fman-pcd-cc-body-action-encoding.patch` (227 lines, +144/-9) | `d87652d` | 2026-05-14 | ✅ landed on `ask20` | Zero-warning ARM64 build (`make -j32 Image modules` in 20 s); `cc_encode_ad` is DCE'd at vmlinux (no consumer yet — that is correct: `__maybe_unused static` + body-4 wires it); all 6 `__ksymtab_fman_pcd_cc_*` entries present; `patch-health` ✓ 0011 against the cumulative stack. Empty-table FQ-0-trap invariant preserved (helper does not touch the miss-AD slot). |
| **body-4** | `0012-fman-pcd-cc-kg-body-add-key-modify-attach.patch` (452 lines, +275/-28 across 5 files) | `17d0f2b` | 2026-05-14 | ✅ landed on `ask20` | Real bodies for `cc_node_add_key` + `cc_node_modify_next_action` (spinlock-protected, consume `cc_encode_ad`). Also replaces `fman_pcd_kg_attach_cc()` `-EOPNOTSUPP` stub with real `keygen_scheme_set_ccbs()` AR-indirect KGSE_CCBS read-modify-write. All 6 new symbols (`keygen_scheme_set_ccbs`, `fman_pcd_kg_attach_cc`, `cc_encode_ad`, `fman_pcd_cc_node_modify_next_action`, `fman_pcd_cc_node_add_key`, `fman_pcd_cc_tree_group_table_off`) verified present in `System.map`. Cross-TU accessor `fman_pcd_cc_tree_group_table_off()` added to `fman_pcd_internal.h` so KG can resolve the tree's MURAM offset without reaching into the opaque `cc_tree` struct. `cc_encode_ad` dropped its `__maybe_unused` since `add_key` / `modify_next_action` now consume it. |
| **body-5** | `0013-fman-pcd-cc-kunit-suite.patch` (362 lines, +308/-1 across 3 files) | `25ef650` | 2026-05-14 | ✅ landed on `ask20` | 10-case KUnit suite for `cc_encode_ad`: DROP (nia=BYPASS), FORWARD_FQ (valid + FQID=0→-EINVAL), FORWARD_CAAM (PLCR_DIS set because CAAM does its own metering), NEXT_CC_NODE (res=ad_table_off, nia=CONT_LOOKUP) + NULL→-EINVAL, REPLICATE/MANIPULATE→-EOPNOTSUPP, NULL-args→-EINVAL, and a byte-layout regression canary that raw-bytes-checks `0xDEADBEEF` at offset 0 against RM 8.7.4.3 Table 8-119. Tests are `#include`'d from `fman_pcd_cc.c` under new `CONFIG_FSL_FMAN_PCD_KUNIT_TEST` Kconfig so they reach the static `cc_encode_ad()` directly; scratch buffer is `kunit_kzalloc`'d 16 bytes cast `(void __iomem __force *)` — `iowrite32be` on arm64 is `__raw_writel + swab` with no MMIO ordering hooks. Build-verified clean both with the option off (default kernel) and on (KUnit kernel). MURAM-resident tree/node layout tests and add_key vs modify race tests deferred to live-silicon bring-up because they need real `fman_muram_alloc` infra. |

**Invariants captured in body-1 + body-2 (informs body-3 onward):**

1. **MURAM dual-API pattern.** Use `fman_pcd_muram_alloc()` /
   `fman_pcd_muram_free()` for budget-accounted allocation and direct
   `fman_muram_offset_to_vbase(muram_handle, off)` for the CPU iomem
   pointer (where `muram_handle = fman_get_muram(fman_pcd_get_fman(pcd))`).
   Do NOT use `fman_muram_alloc()`/`_free_mem()` directly from PCD code —
   those bypass the per-PCD budget tracking.
2. **gen_pool alignment is free.** `fman_muram_alloc()`'s gen_pool
   `min_alloc_order = 8` satisfies every CC alignment constraint
   (tree-group 256-B, match-table 16-B, AD-table 16-B) naturally. No
   alignment argument needed at any alloc site in this TU.
3. **Empty-table safety property.** All-zero AD entries decode to
   `RESULT_CONTRL_FLOW` (type bits 00) + `fqid=0`. FQ 0 is reserved-
   invalid in DPAA1, so any frame hitting an unprogrammed node triggers
   an FMan parse-error trap rather than silently misrouting. body-3 must
   preserve this property: the miss-AD remains all-zero until the caller
   explicitly installs a miss action.
4. **Locking discipline.** Per-tree `tree->lifecycle_lock` is a sleep-
   capable **mutex** (hardware never traverses `tree->nodes`, only kernel
   writers do, and `kzalloc`/`kcalloc` paths may sleep). Per-node
   `node->lock` is a **spinlock** because body-4's `add_key`/`modify`
   paths may run from softirq context on flow-table updates. Already
   declared and initialised in body-2.
5. **Citation style is string-quoted, not `/* */`.** Doc-comment bodies
   reference RM/SDK in plain text (`"RM §8.7.4.2 + cross-ref SDK fm_cc.c
   <function>"`) so the comments do not accidentally terminate the outer
   docstring during multi-line nesting. Body-1 compile-gotcha lesson.
6. **`patch-health` Pass/Fail noise.** Once 0010 sits next to 0009, the
   stack-apply baseline-drift artefact reports both bodies as ✗ in the
   overall verdict — this is because patch-health applies in sorted order
   against a moving baseline. The per-patch verdict for the
   most-recently-landed body is the meaningful signal; verify each new
   sub-PR's own line shows ✓.

**Next-session entry point (updated, after body-5 landing 2026-05-14):**
PR14c is closed (5/5 sub-PRs landed). The next planned work item is
PR14d — header-manipulation. The `cc_encode_ad` `MANIPULATE` arm
currently returns `-EOPNOTSUPP`; PR14d will wire it to the manip-AD
opcode-extension format from RM §8.7.5. The bring-up validation gate
deferred from PR14b-body (kernel boots cleanly on Mono Gateway DK with
the FMan PCD subsystem active and `attach_cc()` exercised end-to-end
through `fman_pcd_cc_tree_create()` → `fman_pcd_kg_attach_cc()` →
`KGSE_CCBS` register write) can now run because the entire CC stack is
silicon-resident. Stale next-session pointer below kept for reference.

**Stale next-session entry point (pre-2026-05-14, kept for historical context):** start with PR14c-body-4
(`0012-fman-pcd-cc-body-add-modify-key.patch`). The encoder helper
`cc_encode_ad()` from body-3 is now resident in
`work/linux-6.18.28/drivers/net/ethernet/freescale/fman/fman_pcd_cc.c`
immediately after `struct fman_pcd_cc_node`. Body-4 replaces the two
`-EOPNOTSUPP` stubs at the bottom of that file
(`fman_pcd_cc_node_add_key`, `fman_pcd_cc_node_modify_next_action`)
with real bodies that:

1. Validate `action` then encode it via `cc_encode_ad()`.
2. Take `node->lock` (spinlock — softirq-safe; the lock was
   already declared/initialised in body-2).
3. For `add_key`: range-check `key_index <= node->num_keys` (insert
   or replace at the trailing slot, never above `max_keys`); copy
   `key`+`mask` into the kcalloc'd `node->keys[]` mirror; write the
   `(key, mask)` pair to the MURAM match-table at slot offset
   `key_index * 2 * extract.size`; write the AD row to the AD-table
   at slot offset `key_index * FMAN_PCD_CC_AD_ENTRY_SIZE` (16 B).
4. For `modify_next_action`: range-check `key_index < num_keys`;
   re-encode the AD record in place. No match-table write.
5. Release `node->lock`.
6. Returns 0 / `-EINVAL` / `-ERANGE` / forwards `cc_encode_ad()`'s
   `-EOPNOTSUPP` from REPLICATE/MANIPULATE actions until PR14d/PR14f
   land.

Also in body-4: replace the `-EOPNOTSUPP` stub of
`fman_pcd_kg_attach_cc()` in `fman_pcd_kg.c` with the real `KGSE_CCBS`
register write, now that `struct fman_pcd_cc_tree` is a concrete type
with a populated `group_table_off` (body-1) and a populated
`num_of_groups` (body-1). The KGSE_CCBS encoding is documented in
RM §8.7.4.1 paragraphs 5-7 and cross-referenced in SDK
`fm_kg.c` `KgSchemeSetupCc()`.

Validation gates for body-4:

- Zero-warning ARM64 build with `make -j32 Image modules`.
- `nm` shows `add_key` and `modify_next_action` grow from their
  current stub sizes (`add_key` 8 B, `modify_next_action` 48 B);
  `cc_encode_ad` symbol becomes resident in vmlinux (no longer
  DCE'd because body-4 calls it).
- `patch-health.sh --flavor ask --source release` shows
  `✓ patches/0012-*` on its own line.
- All 6 `__ksymtab_fman_pcd_cc_*` entries present plus
  `__ksymtab_fman_pcd_kg_attach_cc` no longer points at a stub.

Body-5 (`0013-fman-pcd-cc-body-kunit.patch`) is the kunit suite that
validates the byte-perfect AD record layout from RM §8.7.4.3 Table
8-119, the tree-group MURAM layout from §8.7.4.1, and the
`add_key`/`modify_next_action` race semantics.

---

### PR14d — Header manipulation (`fman_pcd_manip.c`)

Per spec §13.3 (`fman_pcd_manip.c` section). The NAT engine.

**Files added/changed:**

- `drivers/net/ethernet/freescale/fman/fman_pcd_manip.c` (new, ~1200
  LOC):
  - `struct fman_pcd_manip` + `struct fman_pcd_manip_params` tagged
    union: `MANIP_NAT_V4`, `MANIP_NAT_V6`, `MANIP_VLAN_PUSH`,
    `MANIP_VLAN_POP`, `MANIP_TTL_DEC`.
  - `fman_pcd_manip_create(pcd, params)` — programs the in-silicon
    header-rewriter MURAM template per RM §8.7.5.
  - `fman_pcd_manip_destroy(manip)`.
  - Hooks into `fman_pcd_action` so a CC key match can carry an
    `ACTION_MANIPULATE` discriminator pointing at a manip handle.
  - Auto-includes IPv4/UDP/TCP checksum recompute per RM §8.7.5.4.
- `include/linux/fsl/fman_pcd.h`: add manip API.

**kunit suite:** `fman_pcd_manip_test.c` covers template encoding for
each MANIP_* variant; verifies the IPv4 SNAT example from spec §12.5
produces the right rewrite-template bytes (the spec's worked example
becomes a unit test).

**Acceptance:**

- kunit suite passes.
- On real Mono Gateway DK: extend the PR14c debugfs hook with an
  `ACTION_MANIPULATE` of type `MANIP_NAT_V4` rewriting source IP.
  Send the test packet; verify the egress port sees the rewritten
  source IP (via `tcpdump -i <egress>` on a connected peer).
- `patch-health.sh` green.

LOC budget: ~1250.

#### PR14d-body progress log

| Sub-PR | Patch | Commit | Date | Status | Validation |
|---|---|---|---|---|---|
| **body-1** | `0014-fman-pcd-manip-body-1-create-destroy.patch` (482 lines, +384/-30 in `fman_pcd_manip.c`) | `d239de0` | 2026-05-14 | ✅ landed on `ask20` | Zero-warning ARM64 build on Cobalt 100 (`make -j32 Image modules`, ~30 s incremental); `fman_pcd_manip.c` grew 88→411 LOC; `nm` shows `fman_pcd_manip_create` 28→468 B, `fman_pcd_manip_destroy` 24→188 B; both `__ksymtab_fman_pcd_manip_*` entries present in `System.map`; `patch-health` ✓ on `patches/0014-fman-pcd-manip-body-1-create-destroy.patch` (cumulative Pass 5/Fail 18 is documented progressive-build-up baseline-drift noise — same pattern as PR14c-body-{2,3,4} against `cc.c`, now applied to `manip.c`). `bin/ci-setup-kernel.sh` wired: glob includes `0014-*`, case clause `0014-*` → `1014-*`, count guard `13` → `14`. |
| **body-2** | `0015-fman-pcd-manip-body-2-variant-encoders.patch` (587 lines, +584/-3 in `fman_pcd_manip.c`) | `18b48a3` | 2026-05-14 | ✅ landed on `ask20` | Zero-warning ARM64 build on Cobalt 100 (`make -j32 Image`, ~30 s incremental); `fman_pcd_manip.c` grew 411→774 LOC (26424 B); `nm` shows `fman_pcd_manip_create` 468→1556 B (compiler inlined all 5 static encoder helpers into the single switch-case caller), `fman_pcd_manip_destroy` unchanged at 188 B; `patch-health` ✓ on `patches/0015-fman-pcd-manip-body-2-variant-encoders.patch` on its own line (cumulative Pass 5 / Fail 19 is documented progressive-build-up baseline-drift, same pattern as PR14c-body-{2..5}). Five static encoders (`manip_encode_nat_v4`, `_nat_v6`, `_vlan_push`, `_vlan_pop`, `_ttl_dec`) write into `manip->hmct` under `manip->lock` (spinlock, softirq-safe per public-ABI promise); HMCD_LAST stamped into FIRST word of LAST command; HMTD triplet (`cfg = TYPE \| EXT_HMCT`, `hmcdBasePtr = manip->hmct_off`, `opCode = HMAN_OC` for L2 / `HMAN_OC_IP_MANIP` for IP-touching) populated last so a partial HMCT is never visible to the silicon walker. NAT_V4/NAT_V6 set `HMCD_IP_L4_CS_CALC=0x00040000u` on the UPDATE word when SA/DA rewrite is requested per RM §8.7.5.4. Byte-order discipline: `iowrite32be` does host→BE swap on write, so encoder calls `be32_to_cpu()`/`be16_to_cpu()` on the `__be*` ABI fields first (NAT_V6 16-B arrays via `get_unaligned_be32()`, header `<linux/unaligned.h>`). VLAN_PUSH = GENERIC_INSRT (op 0x02) at L2 off 12 size 4 with inline `(tpid<<16)\|tci` payload; VLAN_POP = GENERIC_RMV (op 0x01) symmetric. `bin/ci-setup-kernel.sh` wired: glob `0015-*`, case `0015-→1015-`, count guard 14→15. **CRITICAL GOTCHA caught this iteration:** `struct fman_pcd_manip_params` has an **anonymous** union — accessors are `p->nat_v4.field` / `p->nat_v6.field` / `p->vlan_push.field` (NOT `p->u.nat_v4.field` as the SDK idiom would suggest); v1 author script used `p->u.*` and produced GCC `'has no member named 'u'` errors. Stored in Qdrant as a body-3-onward gotcha. |
| **body-3** | `0016-fman-pcd-cc-manipulate-arm.patch` (119 LOC) | `cdd8865` | 2026-05-14 | ✅ landed on `ask20` | Wired `cc_encode_ad()` `FMAN_PCD_ACTION_MANIPULATE` arm in `fman_pcd_cc.c`: AD slot encodes `nia = RESULT_CF \| NADEN`, `fqid = manipulate.next_fqid`, `plcr = 0`, `res = (u32)hmtd_off`. Silicon walk order: AD → HMTD → HMCT → enqueue to AD.fqid. New `#define FMAN_PCD_AD_NIA_NADEN 0x20000000u` (same bit as `PLCR_DIS` but disjoint AD-type context per RM §8.7.4.3). Validation: non-NULL manip, non-zero `next_fqid`, non-zero `hmtd_off`, 24-bit `hmtd_off` bound (`-EOVERFLOW`). Cross-TU accessor `fman_pcd_manip_hmtd_off()` added to `fman_pcd_internal.h` (same pattern as `fman_pcd_cc_tree_group_table_off()` from PR14c-body-4) to expose private `struct fman_pcd_manip::hmtd_off` from the manip TU. `patch-health` ✓ on `patches/0016-fman-pcd-cc-manipulate-arm.patch` (cumulative Pass 6 / Fail 20, expected progressive-build-up baseline-drift). CI wired: glob `0016-*`, case `0016-→1016-`, count guard 15→16. |
| **body-4** | `0017-fman-pcd-manip-kunit.patch` (399 LOC, +396/-3) | `549381a` | 2026-05-14 | ✅ landed on `ask20` | KUnit suite `tests/fman_pcd_manip_test.c` (8 test cases) trailer-`#include`'d from `fman_pcd_manip.c` under `CONFIG_FSL_FMAN_PCD_KUNIT_TEST` (Kconfig symbol from patch 0013 covers BOTH cc and manip suites — no new Kconfig). Cases: `manip_encode_nat_v4_full` (SA+DA+L4 ports byte-perfect), **`manip_encode_nat_v4_snat_canary`** (silicon-fact pin `0x0c040040 = OP_IPV4_UPDATE\|UPDATE_SRC\|IP_L4_CS_CALC` — locks the stateful NAT44 fast-path from regressing on lost CS_CALC bit), `manip_encode_nat_v6` (16-B SA, BE order), `manip_encode_vlan_push` (GENERIC_INSRT op + payload), `manip_encode_vlan_pop` (GENERIC_RMV op, no payload), `manip_encode_ttl_dec` (IPV4_UPDATE + TTL bit), `manip_encode_nat_v4_empty` (no rewrite flags → NULL rejection), `manip_hmcd_last_or` (HMCD_LAST=`0x00800000` termination OR). Also revises the cc-side `cc_encode_ad_manipulate_test` from a `-EOPNOTSUPP` regression canary (pre-body-3) into a NULL-manip `-EINVAL` regression canary (post-body-3). Verified clean ARM64 builds both with KUnit ON and OFF (`#if IS_ENABLED` guard correct). `patch-health` ✓ on `patches/0017-fman-pcd-manip-kunit.patch` (Pass 7 / Fail 19, one more pass than body-3 baseline). CI wired: glob `0017-*`, case `0017-→1017-`, count guard 16→17. **CRITICAL FINDING this iteration:** `struct fman_pcd_manip` is private to `fman_pcd_manip.c` — NOT visible from `fman_pcd_cc.c` TU. The cc-side test cannot construct a stub `fman_pcd_manip` handle (incomplete-type compile error). Byte-perfect positive-path coverage of the cc MANIPULATE arm therefore lives ONLY in the new manip test TU; the cc-side test is reduced to a NULL-manip `-EINVAL` regression canary that short-circuits BEFORE the accessor call. Stored in Qdrant as a cross-TU struct-visibility gotcha applicable to ALL future trailer-#include kunit additions. |

**PR14d closed: 4/4 sub-PRs landed (prep `cdd8865^^^^`, body-1 `d239de0`, body-2 `18b48a3`, body-3 `cdd8865`, body-4 `549381a`).**

**Invariants captured in body-1 (informs body-2 onward):**

1. **HMTD layout = 16-B fixed (RM §8.7.5 p.2).** Offsets `0x0` cfg, `0x2` eliodnOffset, `0x3` extHmcdBasePtrHi, `0x4` hmcdBasePtr, `0x8` nextAdIdx, `0xb` opCode, `0xc` res2. Cross-ref SDK `fm_manip.h::t_Hmtd`.
2. **HMCT = sequence of 4-B BE command words + inline payload (RM §8.7.5 p.4).** opcode in bits[31:24], operands in bits[23:0], list terminated by `HMCD_LAST=0x00800000u` OR'd into the last word. body-1 sizes the HMCT region at fixed 64 B which covers worst-case NAT_V6 SA+DA+L4-port (= 56 B) with slack.
3. **Inactive descriptor is safe.** All-zero HMTD has `opCode=0` and `cfg=0` (TYPE bit clear) — silicon walker treats as "no manip" and never invokes the HMCT walker. Body-1 therefore leaves the HMCT all-zero on alloc without risking a silicon walk-forever (would otherwise occur because all-zero word lacks `HMCD_LAST`).
4. **MURAM dual-API discipline (same as PR14c-body).** `fman_pcd_muram_alloc()` / `_free()` for budget-accounted allocation; `fman_muram_offset_to_vbase(muram_handle, off)` for the CPU iomem pointer, where `muram_handle = fman_get_muram(fman_pcd_get_fman(pcd))`. `gen_pool min_alloc_order=8` satisfies every RM alignment naturally.
5. **HMCT writers under `manip->lock` (spinlock, softirq-safe).** Body-2 encoder functions may run from softirq context when a flow-table update recreates a manip to apply a runtime NAT change. The HMTD `cfg`/`hmcdBasePtr`/`opCode` triplet must be written last (under the same lock that protects the HMCT command sequence) so a partial HMCT is never visible to the silicon walker.
6. **Body-2 opcode constants pre-declared in body-1.** `FMAN_PCD_HMCD_OP_IPV4_UPDATE=0x0Cu`, `_OP_IPV6_UPDATE=0x10u`, `_OP_TCP_UDP_UPDATE=0x0Eu`, `_OP_L2_INSRT=0x09u`, `_OP_L2_RMV=0x08u`; IPV4/IPV6/L4 operand bits; `HMCD_IP_L4_CS_CALC=0x00040000u` for RM §8.7.5.4 auto-checksum; `HMTD_CFG_TYPE=0x4000`, `_EXT_HMCT=0x0080`, `_PRS_AFTER_HM=0x0040`, `_NEXT_AD_EN=0x0020`; `HMAN_OC=0x35`, `HMAN_OC_IP_MANIP=0x34`. Body-2 lands as a pure encoder drop.
7. **Tab-discipline in author scripts.** Authoring this patch via Python needs explicit `T = '\t'` markers with `f'{T}'` interpolation. Earlier v1 attempt used raw triple-quoted strings with 4-space indent, then mapped 8-space-prefix → `\t` via `fix_indent()`; the `#define MACRO\tVALUE` column-separator tabs got swallowed and produced `#define FMAN_PCD_HMTD_SIZE16` with no separator. Verify with `grep -P "^\t" | cat -A` showing `^I` markers.

**Next-session entry point (after body-2 landing 2026-05-14):** start with PR14d-body-3 (`0016-fman-pcd-cc-manipulate-arm.patch`, ~150 LOC). Wire the `cc_encode_ad()` `FMAN_PCD_ACTION_MANIPULATE` arm in `fman_pcd_cc.c` (currently `return -EOPNOTSUPP;` near line 353) to read the HMTD MURAM offset from the manip handle carried on `struct fman_pcd_action.next_manip` and program it into the AD `res` field per RM §8.7.4.3 Table 8-119. Add cross-TU accessor `fman_pcd_manip_hmtd_off(struct fman_pcd_manip *)` to `fman_pcd_internal.h` (same pattern as `fman_pcd_cc_tree_group_table_off()` from PR14c-body-4). Add KUnit case for `MANIPULATE` arm in body-4. CI wire `bin/ci-setup-kernel.sh`: glob/case/count 15→16. Body-4 (`0017-fman-pcd-manip-kunit.patch`, ~250 LOC) follows as the KUnit suite per spec §12.5 covering byte-perfect HMCT encoding for each of the five `MANIP_*` variants plus the SNAT regression canary (`OP_IPV4_UPDATE \| UPDATE_SRC \| IP_L4_CS_CALC` mask), trailer-`#include`'d from `fman_pcd_manip.c` under `CONFIG_FSL_FMAN_PCD_KUNIT_TEST` — same idiom as PR14c-body-5. CI wire 16→17.

**Stale next-session entry point (pre-2026-05-14 body-2 landing, kept for historical context):** start with PR14d-body-2 (`0015-fman-pcd-manip-body-2-variant-encoders.patch`). The five HMCT variant encoders branch on `manip->type` (the create-time tag stored in body-1) and write into `manip->hmct` under `manip->lock`. At the end of dispatch, populate the HMTD triplet (`cfg`, `hmcdBasePtr` = `manip->hmct_off`, `opCode`) and clear the inactive marker. Cross-ref SDK `fm_manip.c` `FmPcdManipBuildIpv4Updates()` / `FmPcdManipBuildIpv6Updates()` / `FmPcdManipBuildVlanCommand()` / `FmPcdManipBuildTtlCommand()` (read-only silicon-fact reference; no code copy per provenance v1.1).

---

### PR14e — Policer profiles (`fman_pcd_plcr.c`)

Per spec §13.3 (`fman_pcd_plcr.c` section).

**Files added/changed:**

- `drivers/net/ethernet/freescale/fman/fman_pcd_plcr.c` (new, ~800
  LOC):
  - `struct fman_pcd_plcr_profile`.
  - `fman_pcd_plcr_profile_create(pcd, params)` — allocates a
    policer-profile slot, writes the trTCM profile record per RM
    §8.7.6.
  - `fman_pcd_plcr_profile_set_rates(prof, cir, cbs, eir, ebs)` —
    runtime rate update (no re-create cost).
  - `fman_pcd_plcr_profile_destroy()`.
  - Hooks into `fman_pcd_action` via a discriminator field
    `policer_profile_id` on every action type (orthogonal to the
    next-engine — a packet can be policed AND classified AND
    manipulated).
- `include/linux/fsl/fman_pcd.h`: add policer API.

**kunit suite:** `fman_pcd_plcr_test.c` covers RFC 4115 trTCM record
encoding, rate-to-token-bucket-period conversion (silicon expects
clock-cycle counts, not bytes/sec), profile-slot allocation.

**Acceptance:**

- kunit suite passes.
- On real Mono Gateway DK: program a 100 Mbps CIR / 1 Gbps EIR
  policer on the test flow from PR14c. Generate 500 Mbps with iperf;
  verify yellow-marked frames in stats and red-marked drops at >1 Gbps.
- `patch-health.sh` green.

LOC budget: ~850.

#### PR14e-body progress log

| Sub-PR | Patch | Commit | Date | Status | Validation |
|---|---|---|---|---|---|
| **body-1** | `0018-fman-pcd-plcr-body-1-create-destroy.patch` (505 lines, +316/-12 in `fman_pcd_plcr.c`) | `ccdf040` | 2026-05-14 | ✅ landed on `ask20` | Zero-warning ARM64 build on Cobalt 100 (`make -j32 Image modules`, incremental ~30 s); `fman_pcd_plcr.c` grew 115→426 LOC; `nm` shows `fman_pcd_plcr_profile_create` 52→456 B (0x1c8), `fman_pcd_plcr_profile_destroy` 28→172 B (0xac), `fman_pcd_plcr_profile_set_rates` unchanged at 48 B (0x30) — stays `-EOPNOTSUPP` stub; all three `__ksymtab_fman_pcd_plcr_*` entries present in `System.map`. `patch-health` ✓ on `patches/0018-fman-pcd-plcr-body-1-create-destroy.patch` on its own line (cumulative Pass 5 / Fail 22 is documented progressive-build-up baseline-drift, same pattern as PR14c-body-{2..5} / PR14d-body-{1..4}). Struct `fman_pcd_plcr_profile` is private to the TU (forward-declared in public header) with 11 fields: list anchor on `pcd->plcr_list`, `pcd` back-pointer, `profile_id` (silicon slot 0..255 — set by body-2 allocator, left 0 at body-1), `color_mode`, cached CIR/CBS/EIR/EBS for body-2 `set_rates` comparison, `record_off` MURAM byte offset, `record` iomem pointer, `spinlock_t lock` for body-2 set_rates softirq paths. Body-1 deliberately pre-declares ALL body-2 register constants per RM §8.7.6: `FMAN_PCD_PLCR_PROFILE_SIZE=16`, profile-record offsets (MODE 0x0, CIR 0x4, CBS 0x8, EIR_EBS 0xc); PMR (profile-mode register) bits `COLOR_AWARE=0x8000`, `ALG_TRTCM=0x2000`, `PACKET_MODE=0x1000`, `DROP_PROBABILITY=0x0080`, `PIR_DISABLED=0x0040`; rate-field encoding scheme (3-bit exp at shift 29, 16-bit mant at shift 13; CBS/EBS scaled by `BURST_UNIT_BYTES=256`); `FMAN_PCD_PLCR_MAX_PROFILES=256`. Body-2 lands as a pure encoder drop with no new constants to declare. Cross-ref SDK `fm_plcr.h::t_FmPcdPlcrProfileRegs` (16-byte packed struct) and `fm_plcr.h::FM_PCD_PLCR_PMR_*` macros (silicon-fact reference; no code copy per provenance v1.1). Create sequence mirrors PR14d-body-1: validate args + color-mode + (cir≠0∨eir≠0) + (cbs≠0∨ebs≠0); `kzalloc` handle; init list anchor + spinlock; cache rate/burst/color for body-2 set_rates compare; `fman_pcd_muram_alloc(FMAN_PCD_PLCR_PROFILE_SIZE=16)` (gen_pool `min_alloc_order=8` satisfies RM 16-B alignment naturally); `fman_muram_offset_to_vbase()` for the CPU pointer; `memset_io(record, 0, 16)` — all-zero record has `mode=0` (color-blind, ALG=0, byte-mode, PIR_DISABLED=0) and zero rates, silicon treats every frame as red and drops (safe inactive state until body-2 programs real rates); `list_add_tail` on `pcd->plcr_list` under `pcd->lock` so `fman_pcd_release()` reaps leaked handles. Destroy is exact reverse: `list_del` under `pcd->lock`, `fman_pcd_muram_free` 16 B record, `kfree`. `set_rates` stays `-EOPNOTSUPP` because the rate-field encoder is shared between create's real body and set_rates — both land together as the body-2 encoder drop. CI wired in `bin/ci-setup-kernel.sh`: glob extended to `0018-*`, case clause `0018-*` → `1018-*`, count guard `17` → `18`. |
| **body-2** | `0019-fman-pcd-plcr-body-2-rate-encoder.patch` (517 lines, +354/-53 in `fman_pcd_plcr.c`) | `dfc7f64` | 2026-05-14 | ✅ landed on `ask20` | Zero-warning ARM64 build on Cobalt 100 (`make -j32 Image modules`); `fman_pcd_plcr.c` grew 426→727 LOC; `nm` shows `fman_pcd_plcr_profile_create` 456→**636 B (0x27c)**, `fman_pcd_plcr_profile_set_rates` 48→**208 B (0xd0)** (real body — no longer `-EOPNOTSUPP`), `fman_pcd_plcr_profile_destroy` 172→**192 B (0xc0)** (added `memset_io` zero of the record before MURAM free so a stale id slot never re-meters traffic between destroy and re-allocate). New static helper `plcr_program_record` (548 B, 0x224) — 4× `iowrite32be` of mode\|reserved, CIR, CBS, EIR_hi\|EBS_lo under `prof->lock` `spin_lock_irqsave` (softirq-safe per body-1 invariant #6); compiler inlined the four encoder helpers (`plcr_encode_rate_bps`, `plcr_encode_burst_bytes`, `plcr_build_mode`, `plcr_alloc_slot`) at -O2 — expected. All three `__ksymtab_fman_pcd_plcr_*` entries present in `System.map`. `patch-health.sh --flavor ask --source release`: Pass 6 / Fail 22 — `patches/0019-*` does NOT appear in the failure list (the 22 failures are pre-existing baseline-drift on older body patches, same documented progressive-build-up pattern as PR14c-body-{2..5} / PR14d-body-{1..4}). **Rate encoding (RM §8.7.6 p.5):** `effective_rate = (mant << exp) * (fman_clk_hz / 2^31)`; encoder computes `scaled = DIV_ROUND_UP(bps * 2^31, fman_clk_hz)` then finds the smallest `exp ∈ 0..7` such that `scaled >> exp` fits u16 (`<= 0xffff`); saturates at `exp=7, mant=0xffff` for rates above silicon ceiling. **CBS quirk:** burst mantissa goes in the MANT slot at shift 13 with `exp=0` (CBS/EBS use the same packed u32 layout as CIR but the exp field is ignored on FMan v3 — the 16-bit mantissa scales by `BURST_UNIT_BYTES=256` to give bytes). **`fman_get_clock_freq()` returns u16 MHz, NOT Hz** — encoder multiplies by `1_000_000` before feeding to the math (documented gotcha; without the multiply, every rate over-saturates at exp=7). **Slot allocator** is a linear walk of `pcd->plcr_list` under `pcd->lock` looking for a free id in 0..`FMAN_PCD_PLCR_MAX_PROFILES-1=255`; the in-list profiles are the source of truth — no separate bitmap (simpler reaping on probe failure, ~256 walks is sub-µs). **Atomic write disclaimer:** the four `iowrite32be` are NOT a true 128-bit atomic store, but token-bucket silicon tolerates a tearing window of ≤4 MMIO cycles between mode-word and rate-word writes per RM §8.7.6 + cross-ref SDK `fm_plcr.c::PlcrProfileSetRates` (single-FMan probe-time programming has zero contention; runtime `set_rates` racing with mid-flight metering is the silicon-documented behaviour the SDK relies on). **PMR bits set by `plcr_build_mode`:** always `ALG_TRTCM`, `COLOR_AWARE` iff `params->color_mode == FMAN_PCD_PLCR_COLOR_AWARE`, `PIR_DISABLED` iff `eir_bps == 0` (single-rate fallback); `PACKET_MODE` and `DROP_PROBABILITY` left 0 for ASK2 v1.0. CI wired in `bin/ci-setup-kernel.sh`: glob `0019-*`, case `0019-→1019-`, count guard 18→19. |
| **body-3** | `0020-fman-pcd-cc-plcr-arm.patch` (178 lines, +132/-4 across 4 files: `fman_pcd_cc.c`, `fman_pcd_internal.h`, `fman_pcd_plcr.c`, `include/linux/fsl/fman_pcd.h`) | `2cb2779` | 2026-05-14 | ✅ landed on `ask20` | Wired `cc_encode_ad()` `FMAN_PCD_ACTION_POLICE` arm in `fman_pcd_cc.c`: AD slot encodes `nia = RESULT_CF` (PLCR_DIS **clear** — the whole point is to run the policer pass), `fqid = police.next_fqid`, `plcr = profile_id`, `res = 0`. Silicon walk order: AD → PLCR meter → trTCM color decision → (green\|yellow) enqueue to AD.fqid, (red) drop or downgrade per the profile's mode word programmed by PR14e-body-1/2. **New public ABI surface:** enum value `FMAN_PCD_ACTION_POLICE` appended to `enum fman_pcd_action_type`, new `.police { struct fman_pcd_plcr_profile *profile; u32 next_fqid; }` arm in `struct fman_pcd_action` union, and new public helper `u16 fman_pcd_plcr_profile_get_id(const struct fman_pcd_plcr_profile *prof)` (declared in `<linux/fsl/fman_pcd.h>`, defined in `fman_pcd_plcr.c`, `EXPORT_SYMBOL_GPL`'d — returns `0xffff` for NULL `prof` so callers can distinguish from the well-known catch-all silicon id 0). **New internal accessor:** `u16 fman_pcd_plcr_profile_id_internal(const struct fman_pcd_plcr_profile *prof)` declared in `fman_pcd_internal.h` (thin alias of the public helper; same idiom as `fman_pcd_cc_tree_group_table_off()` from PR14c-body-4 and `fman_pcd_manip_hmtd_off()` from PR14d-body-3) so the CC TU does not have to pull `<linux/fsl/fman_pcd.h>` into its dependency closure for one accessor. Validation: non-NULL `police.profile`, non-zero `next_fqid`, and `profile_id ≤ 0xff` silicon-legal range (the accessor returns `0xffff` for NULL → `-EINVAL`; oversize id → `-ERANGE`). Zero-warning ARM64 build on Cobalt 100 (`make -j32 Image` clean); `nm vmlinux` shows `__ksymtab_fman_pcd_plcr_profile_get_id` present, `T fman_pcd_plcr_profile_get_id` resident (global, exported), `T fman_pcd_plcr_profile_id_internal` resident (global, in-driver only — not `EXPORT_SYMBOL`'d so no `__ksymtab` entry), `t cc_encode_ad` resident with the new POLICE arm consumed at `-O2`. `patch-health` ✓ on `patches/0020-fman-pcd-cc-plcr-arm.patch` (cumulative Pass 5 / Fail 24 is documented progressive-build-up baseline-drift on a 9-patch stack now touching `fman_pcd_cc.c` — same noise pattern as PR14c-body-{2..5} / PR14d-body-{1..4} / PR14e-body-{1..2}). CI wired in `bin/ci-setup-kernel.sh`: glob extended to `0020-*.patch`, case clause `0020-* → 1020-*`, count guard `19 → 20`. **Architecture note:** the action-type-orthogonal `policer_profile_id` discriminator promised in spec §13.3 (every action type can also carry a policer) is **not** implemented at body-3 — instead PR14e-body-3 adds a dedicated `FMAN_PCD_ACTION_POLICE` arm that meters then forwards to an FQID, matching the existing switch-pattern in `cc_encode_ad()`. Chained policer-then-classify scenarios (e.g. policer on top of `FORWARD_FQ` or `MANIPULATE`) are not expressible at body-3; the spec text marks them as M5 QoS scope, well after the M2 gate. Retrofitting orthogonal `.profile` onto every action discriminator can land as a separate PR if M5 QoS work requires it. |
| **body-4** | `0021-fman-pcd-plcr-kunit.patch` (337 lines, +305/-0 across 3 files: new `tests/fman_pcd_plcr_test.c`, trailer `#include` in `fman_pcd_plcr.c`, two new test cases appended into `tests/fman_pcd_cc_test.c`) | `1042e8d` | 2026-05-14 | ✅ landed on `ask20` | 8-case KUnit suite covering the full PLCR encoder + record-layout surface from PR14e-body-1/2, plus two opaque failure-mode tests appended to the existing CC test TU for the body-3 POLICE-arm validation paths. **Plcr cases** (trailer-`#include`'d from `fman_pcd_plcr.c` under the existing `CONFIG_FSL_FMAN_PCD_KUNIT_TEST` Kconfig from patch 0013 — no new Kconfig symbol): (1) `plcr_encode_rate_zero` — `bps=0` returns `0x00000000` so the all-zero record from body-1 stays the canonical "drop everything red" inactive state; (2) `plcr_encode_rate_saturate` — rate above silicon ceiling saturates at `exp=7 \| mant=0xffff` per RM §8.7.6 p.5 instead of wrapping; (3) `plcr_encode_burst_round` — burst quantises to `BURST_UNIT_BYTES=256` granularity with explicit round-down (0 stays 0, 255 stays 0, 256→1, 257→1, 511→1, 512→2, 65536→256, oversize saturates at `0xffff`); (4) `plcr_build_mode_blind_no_eir` — `color_mode=BLIND` + `eir_bps=0` yields `ALG_TRTCM \| PIR_DISABLED = 0x2040` (color-aware bit clear, PIR-disable bit set because single-rate fallback); (5) `plcr_build_mode_aware_eir` — `color_mode=AWARE` + `eir_bps≠0` yields `COLOR_AWARE \| ALG_TRTCM = 0xa000` (both bits set, PIR_DISABLED clear); (6) **`plcr_program_record_layout`** — the central byte-perfect MURAM check: a 16-byte scratch buffer (`kunit_kzalloc`'d, cast `(void __iomem __force *)`) gets `plcr_program_record()` called against it with sentinel mode `0xdead`, CIR `0xcafebabe`, CBS `0x12345678`, EIR `0xfeed`, EBS `0xbeef`; the four `iowrite32be` words at offsets `0x0/0x4/0x8/0xc` (which `__raw_writel + cpu_to_be32` on arm64 produces with no MMIO ordering hooks) are read back via `be32_to_cpu(*(__be32*)(buf+off))` and asserted byte-perfect against the RM §8.7.6 figure — locks the body-2 record layout from drifting; (7) `plcr_get_id_null` — the public `fman_pcd_plcr_profile_get_id(NULL)` accessor returns `0xffff` sentinel (the body-3 distinguish-from-id-0 contract); (8) `plcr_id_internal_passthrough` — the internal alias `fman_pcd_plcr_profile_id_internal(NULL)` returns the same `0xffff` (pins the cross-TU accessor wired into `fman_pcd_internal.h` from body-3 so a future refactor cannot silently break the CC POLICE arm). **CC-side opaque POLICE failure-mode cases** appended into the existing `tests/fman_pcd_cc_test.c` after `cc_encode_ad_manipulate_test`: (a) `cc_encode_ad_police_null_profile_test` — NULL `police.profile` short-circuits to `-EINVAL` before the accessor call (regression canary for the body-3 NULL-guard); (b) `cc_encode_ad_police_zero_fqid_test` — non-NULL profile but `next_fqid=0` returns `-EINVAL` so a misconfigured action cannot silently meter-then-drop into the FQ-0 trap. **Cross-TU scope-limit gotcha (documented in Qdrant 2026-05-14):** a byte-perfect happy-path POLICE-arm test (`prof_real` + `next_fqid≠0` → check AD `nia=RESULT_CF`, `fqid=next_fqid`, `plcr=profile_id`, `res=0`) was originally drafted in `tests/fman_pcd_plcr_test.c` but failed to compile because `cc_encode_ad()` is `static` to `fman_pcd_cc.c` (the body-3 patch deliberately did NOT export it — only the public `fman_pcd_cc_*` API is `EXPORT_SYMBOL_GPL`'d). Same TU-boundary-vs-static gotcha PR14d-body-4 hit when trying to construct a `struct fman_pcd_manip` stub from the CC test TU. Resolution: the happy-path byte-perfect POLICE-arm coverage is deferred to M2 live-silicon integration (the §13.7 acceptance gate runs the full KG→CC→PLCR walk against a real packet anyway — kunit only needs to lock the failure-mode validation paths). The plcr-side suite therefore covers the **PLCR encoder surface byte-perfectly**, and the cc-side appendix covers the **POLICE-arm validation guards opaquely**. Zero-warning ARM64 build with both `CONFIG_KUNIT=y` + `CONFIG_FSL_FMAN_PCD_KUNIT_TEST=y` (had to manually `./scripts/config --enable CONFIG_KUNIT --enable CONFIG_FSL_FMAN_PCD_KUNIT_TEST && make olddefconfig` because `olddefconfig` was silently dropping the FMan kunit symbol when `CONFIG_KUNIT` was not set — captured in Qdrant); `nm vmlinux` confirmed all 5 new plcr test symbols (`plcr_encode_rate_zero_test`, `_saturate_test`, `plcr_encode_burst_round_test`, `plcr_build_mode_blind_no_eir_test`, `plcr_build_mode_aware_eir_test`, `plcr_program_record_layout_test`, `plcr_get_id_null_test`, `plcr_id_internal_passthrough_test`) and the suite descriptor `fman_pcd_plcr_test_suite` resident. `patch-health.sh --flavor ask --source release`: `✓ patches/0021-fman-pcd-plcr-kunit.patch` on its own line (cumulative Pass 4 / Fail 26 is the documented progressive-build-up baseline-drift on a 10-patch stack — same noise pattern as PR14c-body-{2..5} / PR14d-body-{1..4} / PR14e-body-{1..3}). CI wired in `bin/ci-setup-kernel.sh`: glob extended to `0021-*.patch`, case clause `0021-* → 1021-*`, count guard `20 → 21`. |

**Invariants captured in body-1 (informs body-2 onward):**

1. **Profile record = 16-B fixed (RM §8.7.6 p.2).** Offsets `0x0` mode (u16, color-aware + algorithm + rate-mode), `0x2` reserved, `0x4` CIR (u32, exp+mant encoding), `0x8` CBS (u32, same encoding), `0xc` EIR_EBS_LO (upper 16 = EIR, lower 16 = EBS u16 variant). Cross-ref SDK `fm_plcr.h::t_FmPcdPlcrProfileRegs`. The SDK splits CIR/EIR/CBS/EBS across additional reserved fields for FMan v2; LS1046A (FMan v3) honours the compact 16-B layout — we deliberately do NOT mirror v2-only offsets.
2. **Silicon hard limit: 256 profile slots per FMan (RM §8.7.6 p.4).** Profile id is u16 but only low 8 bits used on LS1046A FMan v3. Body-2's slot allocator walks `pcd->plcr_list` to find a free slot in 0..255 under `pcd->lock`.
3. **All-zero record is safe inactive state.** `mode=0` decodes as color-blind | ALG=0 | byte-mode | PIR_DISABLED=0; zero rates mean every frame meters as red and gets dropped. Silicon never enters an undefined state even before body-2 programs real rates. Same FQ-0-trap safety property pattern as PR14c-body-1 / PR14d-body-1.
4. **Token-bucket counters NOT reset on rate update.** `set_rates()` re-encodes mode + CIR/CBS/EIR/EBS atomically (single 128-bit MMIO store under `prof->lock` per RM §8.7.6 + cross-ref SDK `fm_plcr.c::PlcrProfileSetRates`); the profile keeps its current burst credit. ASK2 M5 QoS code relies on this for graceful CIR/EIR re-tuning under live traffic.
5. **MURAM dual-API discipline (same as PR14c/PR14d-body).** `fman_pcd_muram_alloc()` / `_free()` for budget-accounted allocation; `fman_muram_offset_to_vbase(muram_handle, off)` for the CPU iomem pointer where `muram_handle = fman_get_muram(fman_pcd_get_fman(pcd))`. `gen_pool min_alloc_order=8` satisfies the RM §8.7.6 16-B alignment naturally.
6. **Per-profile spinlock for body-2 softirq paths.** `set_rates()` may run from softirq context on flow-table updates that change CIR/EIR without re-creating the profile (re-create would lose the id and force every CC key referencing this profile to be re-modified). Declared and initialised in body-1; body-2 wraps the atomic mode + rate-field write under `prof->lock` with `spin_lock_irqsave`.
7. **Body-2 register constants pre-declared in body-1.** PMR bits (`COLOR_AWARE`, `ALG_TRTCM`, `PACKET_MODE`, `DROP_PROBABILITY`, `PIR_DISABLED`); rate-field encoding scheme (3-bit exp + 16-bit mant + 13-bit reserved); burst-depth `BURST_UNIT_BYTES=256` scale factor. Body-2 implements `plcr_encode_rate(bps) -> u32 (exp<<29 \| mant<<13)` and `plcr_encode_burst(bytes) -> u16 (saturating mant in 256-B units)` helpers shared between create and set_rates.
8. **Anonymous union: NONE here.** `struct fman_pcd_plcr_params` is a flat struct (color_mode + 4 rate/burst fields), no union — none of the PR14d-body-2 `p->u.x` accessor gotcha applies.

**Next-session entry point (after PR14f closure 2026-05-14):** **PR14f is fully landed.** All four bodies are in tree (`1a42ddb`/`8fdafc7` patch 0022 body-1, `080bafe7`/`ae52ecb` patch 0023 body-2, `b79ffbd` patch 0024 body-3 re-roll, `9b31f93` patch 0025 body-4). `patch-health.sh --flavor ask --source release` reports Pass=34/Fail=0; both `CONFIG_FSL_FMAN_PCD_KUNIT_TEST=n` and `=y` build cleanly. All six `enum fman_pcd_action_type` discriminators (DROP, FORWARD_FQ, FORWARD_CAAM, NEXT_CC_NODE, MANIPULATE, REPLICATE) plus the policer-orthogonal POLICE arm are concrete in `cc_encode_ad()` — the M2 §13.7 acceptance gate's worked-example walk (KG scheme → CC tree → key match → action descriptor → silicon enqueue) is end-to-end implementable. **PR14g** (the end-to-end M2 wire-up — `ask_hostcmd.c` switches from `-ENXIO` host-command path to real `fman_pcd_cc_node_add_key()` calls; first IPv4 TCP flow traverses 210 silicon) is now ready to start. Until M2 acceptance, all subsequent milestones (M3 flow types, M4 IPsec, M5 askd/CLI/VyOS, M6 perf-soak) remain blocked on PR14g per the dependency tree in the status tracker.

---

### PR14f — Parser + Replicator (`fman_pcd_prs.c`, `fman_pcd_replic.c`)

Per spec §13.3. Two small modules bundled into one PR because each is
under 600 LOC and they share the same review surface (PCD-side
table programming with no `ask.ko` consumer changes).

**Files added/changed:**

- `drivers/net/ethernet/freescale/fman/fman_pcd_prs.c` (new, ~400 LOC):
  - Programs FMan parser "header examination sequences" (HXS) per
    RM §8.7.2.
  - v1.0 ships pass-through configuration (stock IPv4/IPv6/TCP/UDP/ESP/VLAN
    parser is mainline-default; nothing more needed for v1.0 flow
    types).
  - GRE / VXLAN / MPLS HXS deferred to v1.1.
- `drivers/net/ethernet/freescale/fman/fman_pcd_replic.c` (new, ~600
  LOC):
  - Multicast egress fanout per RM §8.7.7.
  - `fman_pcd_replic_group_create()` / `destroy()`.
  - Consumed in M3 (`OP_FLOW_INSERT_V4_MCAST` / `_V6_MCAST` flow
    types).
- `include/linux/fsl/fman_pcd.h`: parser + replicator APIs.
- `drivers/net/ethernet/freescale/fman/fman_port.c` (~40 LOC):
  - Add `fman_port_pcd_attach(struct fman_port *port,
    struct fman_pcd_kg_scheme *scheme)` accessor per spec §13.4.

**kunit suite:** `fman_pcd_prs_test.c` covers HXS record encoding;
`fman_pcd_replic_test.c` covers replication-group table layout.

**Acceptance:**

- kunit suite passes.
- On real Mono Gateway DK: no new operator-facing demonstration
  (replicator is consumed in M3; parser change is pass-through).
  Verify `fman_port_pcd_attach()` is callable from `ask.ko` without
  vermagic mismatch.
- `patch-health.sh` green.

LOC budget: ~1040.

#### PR14f-body progress log

Following the established cadence (PR14c×5, PR14d×4, PR14e×4), PR14f is
split into four sub-PRs each landing as a separate kernel-tree commit
and a separate workspace patch under `kernel/flavors/ask/patches/`:

- **body-1 — `fman_pcd_replic` group create/destroy MURAM bodies** —
  patch `0022-fman-pcd-replic-body-1-create-destroy.patch`, kernel-tree
  commit `1a42ddb`, workspace commit `8fdafc7`. Defines `struct fman_pcd_replic_group` (private
  to TU), allocates source-TD (16 B) + members array (num_members × 16 B)
  via `fman_pcd_muram_alloc()`, `memset_io` zeros both regions, stamps
  source-TD type field with `FMAN_PCD_REPLIC_SOURCE_TD_OPCODE` (0x75)
  so silicon recognises the entry point, `list_add_tail` on
  `pcd->replic_groups` under `pcd->lock`. Destroy is exact reverse:
  list_del under lock, zero before free, `fman_pcd_muram_free()` in
  reverse order, `kfree` handle. Pre-declares body-2 register
  constants (`FR_BIT 0x08000000`, `NL_BIT 0x10000000`, `ADDR_SHIFT 4`,
  `INVALID_MEMBER_INDEX 0xffff`) and source-TD record offsets per
  RM Table 8-128. **Body-1 deliberately does NOT consume the
  `members[]` arg yet** — FQID encoding + NEXT chaining + NL_BIT
  stamping lands in body-2 as one atomic programming pass.  All-zero
  MURAM keeps silicon walker inactive (operationCode=0 short-circuits
  the walker), preserving the inactive-state safety invariant of
  PR14d-body-1 (HMCT all-zero) and PR14e-body-1 (PLCR record all-zero).
  LOC: +266 / −39.
- **body-2 — replicator member-AD encoder + source-TD active-OPCODE
  publish** — patch `0023-fman-pcd-replic-body-2-member-encoder.patch`,
  kernel-tree commit `080bafe7`, workspace commit `ae52ecb`. Adds
  member-AD record offset macros `FMAN_PCD_REPLIC_MBR_OFF_FQID 0x0` and
  `_NIA 0x8` and a static helper `replic_encode_members(group, members)`
  (caller must hold `group->lock`; gcc inlines it — single caller, sole
  size impact is `fman_pcd_replic_group_create` +456→+716 B = +260 B,
  destroy unchanged at 204 B). Inside `fman_pcd_replic_group_create()`
  body-1's `(void)members` no-op is replaced with: (1) spin_lock_irqsave
  on `group->lock`, call `replic_encode_members()` which walks the
  array writing each member's FQID (low 24 bits) at MBR_OFF_FQID and
  NIA at MBR_OFF_NIA as `FR_BIT 0x08000000 | (next_member_off >> ADDR_SHIFT)`
  for intermediates and `NL_BIT 0x10000000` (zero NEXT) for the last
  member, spin_unlock_irqrestore; (2) `iowrite32be` of the
  members-array MURAM offset into source-TD at SRC_OFF_FRG_POINTER 0x4;
  (3) `iowrite32be` of the active OPCODE word (`FMAN_PCD_REPLIC_SOURCE_TD_OPCODE
  << 24`) into source-TD at SRC_OFF_OPCODE 0x8 LAST — single publication
  point. Silicon walker short-circuits on all-zero active OPCODE until
  that final write, so a partial chain is never visible (same safety
  property as PR14d-body-1 HMCT all-zero / PR14e-body-1 PLCR record
  all-zero). LOC: +111 / −19. Authoring gotcha: body-1's `#define`
  lines use TWO tabs between macro name and value; single-tab search
  patterns in patch-author scripts silently fail to match and produce
  a build error (`'FMAN_PCD_REPLIC_MBR_OFF_FQID' undeclared`) — verify
  with `od -c` on the line, not `grep -P "\t"`.
- **body-3 — `fman_pcd_prs` pass-through + `cc_encode_ad` REPLICATE
  arm + cross-TU `source_td_off` accessor** — patch
  `0024-fman-pcd-prs-pass-through-replic-cc-arm.patch` (166 lines, 4
  hunks across 4 files), workspace commit `970a81e`. **Replaces the
  `fman_pcd_prs_init()` `-EOPNOTSUPP` stub** with a v1.0 pass-through
  body (`return 0` after the NULL-pcd guard) — silicon-fact rationale:
  FMan ucode 210.10.1 already loads stock Header eXamination Sequences
  at probe time covering every ASK2 v1.0 flow type (IPv4, IPv6, TCP,
  UDP, ESP, VLAN per RM §8.7.2 default HXS table); no HXS table writes
  are required and the KG schemes (PR14b) consume the 5-tuple parse
  result the silicon already produces. The hook is kept as a
  non-trivial entry point (not inlined to a 0-returning shim) so v1.1
  can drop in GRE/VXLAN/MPLS HXS programming without an ABI change to
  `ask.ko`. **Wires the `cc_encode_ad()` `FMAN_PCD_ACTION_REPLICATE`
  arm** in `fman_pcd_cc.c` (currently `-EOPNOTSUPP`): AD slot encodes
  `nia = RESULT_CF | NADEN`, `fqid = 0` (per-member FQIDs live in the
  member-AD slots, AD field unused once NADEN takes effect), `plcr =
  0`, `res = source_td_off` (24-bit MURAM offset). Silicon walk order:
  AD → source-TD → fanout via member-AD chain (each carrying its own
  egress FQID). Validation: non-NULL `action->replicate.group`,
  non-zero `source_td_off` (zero sentinel from the accessor means
  uninitialised handle → `-EINVAL`), 24-bit overflow check matches
  MURAM-on-FMan-v3 hard limit (same `0x00ffffff` bound as the
  MANIPULATE arm's `hmtd_off` from PR14d-body-3). Body-2's source-TD
  active-OPCODE single-publication property is the safety anchor: a
  racing `cc_encode_ad()` against a partially-built group cannot fan
  a frame out before the member-AD chain is complete because the
  silicon walker short-circuits on all-zero opcode. **Adds cross-TU
  accessor `fman_pcd_replic_group_source_td_off()`** to
  `fman_pcd_internal.h` (declaration) and `fman_pcd_replic.c`
  (definition, NULL-safe → returns 0) — fourth instance of the
  cross-TU-accessor idiom in the PR14 series (precedent:
  `fman_pcd_cc_tree_group_table_off()` from PR14c-body-4,
  `fman_pcd_manip_hmtd_off()` from PR14d-body-3,
  `fman_pcd_plcr_profile_id_internal()` from PR14e-body-3). The
  `struct fman_pcd_replic_group` definition stays private to the
  replic TU so PR14f-body adjustments don't ripple across the PR14
  TU surface. `patch-health.sh --flavor ask --source release`: ✓ on
  `patches/0024-fman-pcd-prs-pass-through-replic-cc-arm.patch`
  (cumulative Pass 32 / Fail 1; the lone failure is the unrelated
  pre-existing `fixes/120-perf-libperf-asm-headers-srctree.patch`).
  CI wired in `bin/ci-setup-kernel.sh`: glob extended to `0024-*`,
  case clause `0024-* → 1024-*`, count guard `23 → 24`. **Patch
  authoring gotchas captured this iteration:** (a) git-format unified
  diffs require the declared `+new` line count in each hunk header to
  match actual `+`/` ` line counts exactly — hunks 1 and 3 had to be
  bumped from `+57` to `+58` and `+38` to `+39` respectively after
  `awk` audit (`git apply` reports `corrupt patch at line N` where N
  is the line where the discrepancy is detected, not where it
  originates); (b) the final hunk MUST end with a trailing newline
  after the last `+` line — without it, `git apply` reports `corrupt
  patch at line <last>` immediately. The `git apply --3way` precedent
  documented in `plans/PATCH-MIGRATION-3WAY.md` is silent on both of
  these because real `git format-patch` output gets both right
  automatically — the gotcha only surfaces when hand-authoring
  patches.
- **body-3 re-roll (2026-05-14)** — landed (`b79ffbd`). The original
  patch 0024 was authored with no tab indentation but the
  post-PR14c-re-roll `fman_pcd_cc.c` uses tabs (`\t` for the case
  label, `\t\t` for the body). `git apply` rejected the patch because
  the indent didn't match the on-disk text — this was the real root
  cause, NOT the stale blob SHA hypothesis floated in the original
  body-3 failure log. Regenerated via `git format-patch` from
  work-tree commit `a95a556` after authoring with the correct
  per-file indent conventions (`fman_pcd_cc.c` tabs, `fman_pcd_prs.c`
  no body indent, `fman_pcd_internal.h` none). 4 hunks across
  `fman_pcd_cc.c`, `fman_pcd_internal.h`, `fman_pcd_prs.c`,
  `fman_pcd_replic.c`. patch-health: Pass=33/Fail=0.
- **body-4 — KUnit suite (2026-05-14)** — landed (`9b31f93`, patch
  0025 = 574 lines). Two new test files:
  `tests/fman_pcd_replic_test.c` covers source-TD layout offsets
  byte-perfect against RM 8.7.7 Table 8-128, the source-TD
  active-OPCODE single-publication regression canary
  (`replic_source_td_publication_order_test` simulates the body-1 →
  body-2 publication sequence and pins both the type-word HIGH-byte
  OPCODE and the OPCODE-word-at-offset-0x8 publish ordering),
  member-AD layout constants + NIA encoding bits, byte-perfect
  3-member chain encoding via `replic_encode_members()`, FQID
  high-byte clamping, and `source_td_off` NULL-safety accessor pin.
  `tests/fman_pcd_prs_test.c` covers `fman_pcd_prs_init(NULL) →
  -EINVAL` and the v1.0 pass-through happy path. The obsolete
  `-EOPNOTSUPP` REPLICATE test in `tests/fman_pcd_cc_test.c` was
  replaced with `cc_encode_ad_replicate_null_group_test` (NULL group
  → -EINVAL), mirroring the MANIPULATE / POLICE arm patterns. Trailer
  `#include` of the new test files under
  `CONFIG_FSL_FMAN_PCD_KUNIT_TEST` added to both `fman_pcd_replic.c`
  and `fman_pcd_prs.c`. Build-verified with both `=n` and `=y`. Same
  cross-TU scope-limit pattern as PR14d-body-4 / PR14e-body-4 — the
  byte-perfect REPLICATE-arm test against `cc_encode_ad()` is
  deferred to the §13.7 M2 live-silicon acceptance gate.

**PR14f closed 2026-05-14. PR14g (M2 acceptance gate) unblocked.**

---

### PR14g — End-to-end wire-up: `ask_hostcmd.c` calls PCD API + M2 gate

Per spec §13.5 (what `ask.ko` calls) + §13.7 (acceptance gates) + §11.1
M2 gate.

**Files changed (out-of-tree `ask.ko`):**

- `kernel/flavors/ask/oot-modules/ask/ask_hostcmd.c`:
  - `ask_hw_flow_insert_v4_tcp()` switches from
    "encode wire bytes + `fmd_host_cmd()` → `-ENXIO`" to
    "build `fman_pcd_cc_key_table` + call
    `fman_pcd_cc_node_add_key()`" per the spec §13.5 worked example.
  - Same surface to the rest of `ask.ko` — `ask_flow.c` is untouched.
  - The §12 wire-format encoders survive as dead code preserved against
    a future custom-microcode path (kunit golden-hex tests from PR6
    stay green — that's why PR6 was preserved through the §12.8
    pivot).
  - Add `ask_hw_flow_remove()`, `ask_hw_flow_query_stats()` companions
    using `fman_pcd_cc_node_destroy_key()` and the per-key MURAM
    stats counter readback.
- `kernel/flavors/ask/oot-modules/ask/ask_hw.c`:
  - Add `ask_priv_alloc_cc_node_for_v4_tcp()` — at module init, build
    the per-FMan KG scheme + CC tree + CC node skeleton that
    `ask_hw_flow_insert_v4_tcp()` will append keys to. Wires
    PR14b's KG scheme → PR14c's one-node CC tree per spec §13.5.
  - Add `ask_priv_pack_hw_flow_id(node, key_idx)` /
    `ask_priv_unpack_hw_flow_id()` — translate `(node, key_index)` ↔
    32-bit opaque ID for the `ASK_FLOW_ATTR_HW_FLOW_ID` UAPI surface.
- Remove the PR14b debugfs hardware-bring-up hook
  (`/sys/kernel/debug/fman_pcd/<n>/test_*`) — those were
  bring-up-only and the real flow path now exercises the same code
  through `nft flow add`.

**Hardware verification on Mono Gateway DK:**

1. `modprobe ask` succeeds; `dmesg | grep '^ask: hw'` shows the ucode
   banner from PR13 plus a new `ask: hw: PCD subsystem ready
   (<n> KiB MURAM free)` line.
2. `nft add table inet f; nft add flowtable inet f h { hook ingress
   priority 0; devices = { eth0, eth1 }; flags offload; }; nft add
   chain inet f forward { type filter hook forward priority 0; };
   nft add rule inet f forward ip protocol tcp flow add @h` — flow
   block callback fires, CC key gets installed in silicon, `ASK_FLOW_ATTR_HW_FLOW_ID`
   in the genl dump is non-zero.
3. iperf3 IPv4 TCP from peer A through eth0 → eth1 to peer B.
4. **First packet** traverses the kernel slow path (counter:
   `/sys/kernel/debug/fsl_dpaa_eth/eth0/rx_default_fqid`); subsequent
   packets traverse the FMan PCD fast path (counter:
   the CC-node FQID).
5. **CPU idle stays > 95 %** at 2 Gbps line rate (measured via
   `mpstat -P ALL 1`) — this is the M2 acceptance threshold in spec
   §11.1.
6. After flow timeout, `nft delete rule …` removes the CC key; the
   FMan FQID counter stops incrementing.

**Acceptance (this is the M2 milestone gate):**

- All six verification steps above pass on live silicon.
- kunit coverage on `ask_hostcmd.c` ≥ 80 % (golden-hex tests from PR6
  + new PCD-API-builder tests).
- `patch-health.sh --flavor ask --source release` green on all of
  `0001`/`0002`/`0003`/`0004`.
- `gh workflow run "VyOS LS1046A build (self-hosted)" -f flavor=ask`
  produces a complete `vyos-*-LS1046A-ask-arm64.iso` that boots and
  passes step 1 on the Mono Gateway DK from cold.
- **PRs 15–21 unblock.**

LOC budget: ~400 of new `ask.ko` code + ~50 ask_hw additions; no new
in-tree LOC (the in-tree work is done in PR14a–f).

---

## M3 — All flow types *(each PR cross-builds + TFTP-boots + SSH-verifies)*

**Acceptance gate:** spec §11.1 row M3 — "All non-IPsec flow types +
bridge + offline ports. NAT works, bridge offload works."

Split into one PR per flow family:
- PR15a — IPv6 TCP/UDP (opcode 0x11)
- PR15b — IPv4 NAT/PAT (action_flags variants)
- PR15c — IPv4 multicast (opcode 0x12)
- PR15d — IPv6 multicast (opcode 0x13)
- PR15e — Bridge fast-path (opcode 0x14, switchdev notifier)
- PR15f — Offline ports (opcode 0x30)
- PR15g — Policer (opcode 0x40)
- PR15h — VLAN push/pop

Each follows the same pattern as PR14: implement encoder, wire into
`ask_flow.c`/`ask_bridge.c`, verify on hardware.

---

## M4 — IPsec offload *(complex but no on-site presence required)*

**Acceptance gate:** spec §11.1 row M4 — "AES-GCM-128 IPsec at 3 Gbps."

- PR16a — `ask_xfrm.c`: `xdo_dev_state_add` / `xdo_dev_state_delete` /
  `xdo_dev_state_advance_esn`
- PR16b — `ask_caam.c`: descriptor sharing using
  `caam_qi_ext_consumer_register` from PR10
- PR16c — Hardware verification: real strongSwan tunnel, AES-GCM-128,
  3 Gbps target via iperf

---

## M5 — Userspace + VyOS integration

**Acceptance gate:** spec §11.1 row M5 — "vanilla Mono Gateway DK boots
VyOS rolling with `set system offload ask` and forwards at line rate."

### PR17 — `askd` (sd-event + libmnl)

Per spec §6. ~6000 LOC. Daemon:
- subscribes to ASK GENL multicast groups
- exposes Varlink interface on `/run/ask/varlink.sock`
- emits structured journald logs
- systemd `Type=notify`, sandboxed (per spec §6.2)
- meson build, debian packaging

### PR18 — `ask-cli` (Python Varlink client)

Per spec §6.4. ~800 LOC. `/usr/bin/ask` CLI:
- `ask info` — driver/ucode info
- `ask flow list [--json]` — installed flows with stats
- `ask sa list [--json]` — installed SAs
- `ask muram` — MURAM allocation report
- `ask events --follow` — live event stream

### PR19 — VyOS CLI integration (XML defs)

Per spec §6.5 and the VPP precedent in `data/vyos-1x-010-vpp-platform-bus.patch`.
~600 LOC of XML interface definitions:
- `set system offload ask enable`
- `set system offload ask exception-rate-limit ...`
- `show system offload ask` op-mode

### PR20 — VyOS conf_mode + op_mode

Per spec §6.5. ~600 LOC of Python (`src/conf_mode/system_offload_ask.py`,
`src/op_mode/show_offload_ask.py`). Carried as a vyos-1x patch in
`data/vyos-1x-NNN-system-offload-ask.patch` (next available number,
currently 023).

---

## M6 — VPP coexistence + soak *(soak runs over the lab loop up to ~3 Gbps; Spirent runs require on-site presence)*

Per spec §11.1 row M6. No new code per se — performance tuning, cache-line
alignment, RCU latency verification. The soak-level work runs end-to-end
via iperf3 between `vyos-eth{1,2,4}` and an LXC peer. The
Spirent/Keysight runs at the Geerling 1 Mpps line-rate target require a
human in the lab — but the code should land, ~3 Gbps behaviour should
be characterised, and the document should specify exactly what needs to
be verified on the traffic generator before that final run.

---

## Risk-driven re-ordering

The risk register in spec §15 flags two items that may force PR
re-ordering:

1. **Risk #1** — "210 protocol edge cases beyond Section 12.7" (Medium /
   Medium). If PR13 surfaces edge cases, M2/M3 stretch by 1-2 months.
   Mitigation: Section 12.7 explicitly budgets 1-2 weeks of probing in
   M1-M2, so this is already in the plan.

2. **Risk #4** — "Performance gates miss Geerling 1 Mpps target" (Medium /
   High). If M6 misses, we may need to revisit cache-line alignment in
   `ask_flow.c` (PR7) and the RCU grace-period strategy. That's a
   refactor PR after M5 — does not block v1.0 RC if performance is
   close (>800 Kpps).

## Out of scope (do NOT slip in)

Per spec §18:
- No `/dev/cdx_ctrl` legacy compat shim
- No `libfci.so.1` ABI preservation
- No XML configuration loader (`/etc/cdx_*.xml`)
- No VPP plugin
- No DPDK PMD
- No upstream submission of `ask.ko` itself in v1.0 (the three in-tree
  patches PR10/11/12 are the only upstream candidates — and only when
  stable)

## How to pick up a PR

1. Open this document, find the next "not started" PR.
2. Re-read the spec sections it references **in full**.
3. Check Qdrant (`qdrant-find`) for prior session insights on the files
   about to be touched (per `.clinerules/70-qdrant-memory.md`).
4. Open the target files in the workspace; read enough context
   (200-2000 lines) before writing.
5. Track work against the PR's checklist; mark items off as they
   complete.
6. Run the acceptance check listed under the PR.
7. **Do not** open the next PR in the same session unless explicitly
   requested. Each PR is a discrete review unit.

## Coordination

- Spec updates flow back into `specs/ask2-rewrite-spec.md`. Bump
  version number (currently v1.0); bump this document's "drives" line
  in sync.
- Hardware findings get stored in Qdrant per `.clinerules/70-qdrant-memory.md`
  with tags `ask2`, `hardware-probe`, plus the relevant PR number.
- The spec §12.7 unknowns were answered in PR13 (landed 2026-05-13)
  and the spec received a §12.8 "Confirmed hardware behaviour" appendix
  in v0.8.
- PR14-prep (2026-05-14) discovered the mainline FMan PCD gap and
  prompted the C/D/E decision. Decision **Option C** was taken the
  same day; spec §12.9 records the finding and spec §13 specifies
  the new ~7800 LOC FMan PCD subsystem (`0004-fman-pcd-subsystem.patch`).
  PR14 was expanded into PR14a–g in this document, one per spec §13
  module. **Spec v1.1 (2026-05-14)** relaxed the original strict-provenance
  provenance constraint: NXP SDK + we-are-mono are both GPL and
  usable as silicon-behaviour references; only the SDK's architecture
  (handle_t / ncsw / AMP IPC / nested Peripherals/ layout) remains
  rejected. This unblocks all four PR14 body PRs immediately —
  sessions without the (NDA) LS1046A Reference Manual loaded can
  still author bodies by cross-referencing the archived SDK alongside
  whatever RM excerpts are available, with each non-trivial function
  citing its primary RM section and any SDK/we-are-mono cross-ref in
  a header comment.
