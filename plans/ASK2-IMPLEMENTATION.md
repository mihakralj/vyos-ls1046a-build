# ASK2 implementation plan — PR breakdown

**Status:** active. Drives the implementation of ASK2 per
[`specs/ask2-rewrite-spec.md`](../specs/ask2-rewrite-spec.md) (v0.7,
2026-05-12). All PRs target the `ask20` branch unless noted otherwise.

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
| 12  | M2.3 — `0003-fman-host-command-api.patch` (real) | ask20  | not started |
| 13  | M2.4 — `OP_GET_UCODE_VERSION` against silicon    | ask20  | not started |
| 14  | M2.5 — `OP_FLOW_INSERT_V4_TCP` end-to-end        | ask20  | not started |
| 15  | M3.x — remaining flow types (NAT/PAT/v6/bridge)  | ask20  | not started |
| 16  | M4.x — `ask_xfrm.c` + CAAM packet-mode IPsec     | ask20  | not started |
| 17  | M5.1 — `askd` (sd-event + libmnl)                | ask20  | not started |
| 18  | M5.2 — `ask-cli` (Python Varlink client)         | ask20  | not started |
| 19  | M5.3 — VyOS CLI integration                      | ask20  | not started |
| 20  | M5.4 — VyOS conf_mode + op_mode                  | ask20  | not started |
| 21  | M6.x — VPP coexistence, soak, performance gates  | ask20  | not started |

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

- The dev-loop **build host** is **LXC 200** (`vyos-builder` LXC inside
  the `heidi` Proxmox host at `192.168.1.15`, container LAN IP
  `192.168.1.137`), which owns `/srv/tftp/` and the cross-toolchain
  tree under `/home/vyos/kernel-ls1046a-build/work/linux-*`. Reach it
  over SSH (`ssh lxc200`) and run the cross-build remotely.
- The **CI host** is the **Cobalt 100 Azure ARM64 VM** (`arm64-runner`,
  Tailscale `100.125.95.22`), which runs the self-hosted GitHub Actions
  runner (`vm-runner-2`) for ISO builds. It is **not** used for the
  dev-loop kernel build.
- Iteration times per `plans/DEV-LOOP.md`: incremental kernel
  cross-build ≈2 min, full ≈8 min, DTB only ≈30 s.
- All hosts are reachable over SSH via the `ssh` MCP server with
  six pre-configured connections:
  - `heidi` (192.168.1.15) — Proxmox host PVE 8.x running on the LAN
  - `lxc200` (192.168.1.137) — `vyos-builder` LXC on heidi; owns
    `/srv/tftp/` and the cross-build tree (dev-loop build host)
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
- Boot-image cycle is **`ssh vyos reboot` → U-Boot → TFTP `run dev_boot`**
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

### PR12 — `0003-fman-host-command-api.patch` (real implementation)

Per spec §10.3. Exposes `fman_host_cmd_send(struct fman *, void *cmd_buf,
size_t len, void *resp_buf, size_t *resp_len)` — synchronous host-command
transport on top of the FMan IRQ + MURAM doorbell mechanism. ~120 LOC
including the new header `include/linux/fsl/fman_host_cmd.h`.

### PR13 — `OP_GET_UCODE_VERSION` against silicon

First real hardware test. Replace the hard-coded zeros in
`ASK_INFO_ATTR_UCODE_*` with a call to `ask_hw_ucode_get_version()`,
which uses `fman_host_cmd_send()` with opcode `0x01` per spec §12.2.
Verify the response matches family `0x0210` against the live Mono
Gateway DK.

This PR also probes the spec §12.7 unknowns:
- exact MURAM partition behaviour
- event channel binding (IRQ number)
- eviction policy tunables (scan opcode space 0x90-0x9F)

Findings get written back into the spec — this is the only PR that
expects the spec to be updated based on real hardware behaviour.

### PR14 — `OP_FLOW_INSERT_V4_TCP` end-to-end

Implement `ask_hw_flow_insert_v4_tcp()` (real implementation, calling
`fman_host_cmd_send()` with opcode `0x10` per spec §12.5). Wire it into
`ask_flow_insert()` so a real nft flow add results in a real opcode 0x10
on the wire. Verify with iperf.

**M2 acceptance gate runs here.**

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
  version number (currently v0.7); bump this document's "drives" line
  in sync.
- Hardware findings get stored in Qdrant per `.clinerules/70-qdrant-memory.md`
  with tags `ask2`, `hardware-probe`, plus the relevant PR number.
- The spec §12.7 unknowns get answered in PR13 and the spec gets a §12.8
  "Confirmed hardware behaviour" appendix.