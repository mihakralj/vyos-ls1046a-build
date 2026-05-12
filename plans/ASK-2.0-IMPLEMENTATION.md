# ASK 2.0 implementation plan — PR breakdown

**Status:** active. Drives the implementation of ASK 2.0 per
[`specs/ask-2.0-rewrite-spec.md`](../specs/ask-2.0-rewrite-spec.md) (v0.7,
2026-05-12). All PRs target the `ask20` branch unless noted otherwise.

This document is the operator-facing plan; the spec is the architecture
source-of-truth. When the two disagree, **the spec wins** — update this
document to track.

## Branch hygiene

- `main` continues to ship the default + vpp flavors (mainline DPAA,
  no ASK). `FLAVOR=ask` builds on `main` are vanilla VyOS until ASK 2.0
  components land here.
- `ask20` is the integration branch for ASK 2.0. **All PRs in this
  document land on `ask20` first.** Periodic merges from `main` keep
  the kernel/board/CI baseline in sync.
- After M5 acceptance gate (Section 11.1 of the spec), `ask20` is
  fast-forward-merged into `main`.

## Status tracker

| PR  | Title                                            | Target | Owner | Status      |
|-----|--------------------------------------------------|--------|-------|-------------|
| —   | Spec authored, scaffold cleanup                  | ask20  | —     | landed (commit 27c3624) |
| —   | UAPI header `include/uapi/linux/ask/ask.h`       | ask20  | agent | landed (in PR1, commit 03f0d38) |
| 1   | M0.1 — module skeleton + Kbuild + Kconfig        | ask20  | agent | landed (commit 03f0d38, 2026-05-12) |
| 2   | M0.2 — three in-tree patch stubs (placeholders)  | ask20  | agent | landed (pending commit, 2026-05-12) |
| 3   | M0.3 — wire build pipeline (CI + local-build)    | ask20  | agent | landed (pending commit, 2026-05-12) |
| 4   | M0.4 — kunit harness + first dummy test          | ask20  | agent | landed (pending commit, 2026-05-12) |
| 5   | M1.1 — `ask_main.c` + `ask_genl.c` GET_INFO      | ask20  | agent | landed (pending commit, 2026-05-12) |
| 6   | M1.2 — `ask_hostcmd.c` wire-format encoders      | ask20  | agent | landed (pending commit, 2026-05-12) |
| 7   | M1.3 — `ask_flow.c` rhashtable + RCU             | ask20  | agent | landed (pending commit, 2026-05-12) |
| 8   | M1.4 — `ask_flow_offload.c` flow_block_cb        | ask20  | agent | landed (pending commit, 2026-05-12) |
| 9   | M1.5 — kunit coverage ≥ 80% on M1 surface        | ask20  | agent | not started |
| 10  | M2.1 — `0001-caam-qi-share.patch` (real code)    | ask20  | human | not started |
| 11  | M2.2 — `0002-dpaa-eth-flow-block.patch` (real)   | ask20  | human | not started |
| 12  | M2.3 — `0003-fman-host-command-api.patch` (real) | ask20  | human | not started |
| 13  | M2.4 — `OP_GET_UCODE_VERSION` against silicon    | ask20  | human | not started |
| 14  | M2.5 — `OP_FLOW_INSERT_V4_TCP` end-to-end        | ask20  | human | not started |
| 15  | M3.x — remaining flow types (NAT/PAT/v6/bridge/op)| ask20  | mixed | not started |
| 16  | M4.x — `ask_xfrm.c` + CAAM packet-mode IPsec     | ask20  | mixed | not started |
| 17  | M5.1 — `askd` (sd-event + libmnl)                | ask20  | agent | not started |
| 18  | M5.2 — `ask-cli` (Python Varlink client)         | ask20  | agent | not started |
| 19  | M5.3 — VyOS CLI integration                      | ask20  | agent | not started |
| 20  | M5.4 — VyOS conf_mode + op_mode                  | ask20  | agent | not started |
| 21  | M6.x — VPP coexistence, soak, performance gates  | ask20  | human | not started |

Status legend:
- **not started** — no commits yet on the target branch
- **WIP** — branch open, commits landing
- **review** — PR open against ask20, awaiting review
- **landed** — merged into ask20

## Sequencing rules

- PRs within a milestone are **not** strictly sequential, but they share
  acceptance gates. A milestone is "done" when the gate passes.
- Hardware-touching PRs (M2 onwards) **block on the in-tree kernel patch
  PRs (10/11/12)**. The agent cannot land a `OP_FLOW_INSERT_V4_TCP`
  exercise without `fman_host_cmd_send()` existing.
- M0 is **fully agent-driven, no hardware needed.**
- M1 is **agent-driven with kunit; no hardware needed.**
- M2 onwards requires the live Mono Gateway DK in the lab.

---

## M0 — Build pipeline scaffolding *(agent-only, no hardware)*

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
    tristate "ASK 2.0 fast-path offload for NXP LS1046A FMan/210"
    depends on FSL_DPAA && CAAM_QI && NET_FLOW_OFFLOAD
    select XFRM_OFFLOAD
    help
      ASK 2.0 NXP LS1046A FMan microcode (210-series) hardware offload
      driver. Replaces the legacy proprietary cdx.ko/auto_bridge.ko stack.
      See specs/ask-2.0-rewrite-spec.md.

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
MODULE_DESCRIPTION("ASK 2.0 — NXP LS1046A FMan/210 hardware offload");
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
`#warning "ASK 2.0: TODO"` comment and a stub function returning
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

## M1 — Kernel skeleton with hardware-free functionality *(agent-only, no hardware)*

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

## M2 — Hardware brought up *(human-driven, requires Mono Gateway DK)*

**Acceptance gate (M2 done, per spec §11.1 first row):**
- M2 gate "M2: nft flow add over IPv4 TCP → packet traverses 210 fast
  path on real hardware"
- Real iperf flow installs via nft, traffic counted in
  `ASK_FLOW_ATTR_PACKETS` matches `iperf` reported throughput within 1%
- `dmesg` shows zero ucode errors during a 60-second iperf run

### PR10 — `0001-caam-qi-share.patch` (real implementation)

Per spec §8.3. Adds `caam_qi_ext_consumer_register()` to `drivers/crypto/caam/qi.c`,
exports it, and provides a counterpart `caam_qi_ext_consumer_unregister()`.

Critical: this is the patch we hope to upstream. Get the API shape right
on the first try by reviewing it with NXP CAAM maintainers (Madalin Bucur,
Camelia Groza) in parallel with the implementation. See spec §16 #4.

### PR11 — `0002-dpaa-eth-flow-block.patch` (real implementation)

Per spec §10.2. Wires `flow_block_cb` into `dpaa_setup_tc()` so each
`fsl,dpa` netdev can advertise hardware flow offload via nft. The
callback is owned by `ask.ko` (registered through a small shim API).

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

## M3 — All flow types *(mixed: agent-implementable but each needs hardware verify)*

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

## M4 — IPsec offload *(human-driven, complex)*

**Acceptance gate:** spec §11.1 row M4 — "AES-GCM-128 IPsec at 3 Gbps."

- PR16a — `ask_xfrm.c`: `xdo_dev_state_add` / `xdo_dev_state_delete` /
  `xdo_dev_state_advance_esn`
- PR16b — `ask_caam.c`: descriptor sharing using
  `caam_qi_ext_consumer_register` from PR10
- PR16c — Hardware verification: real strongSwan tunnel, AES-GCM-128,
  3 Gbps target via iperf

---

## M5 — Userspace + VyOS integration *(agent-driven)*

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

## M6 — VPP coexistence + soak *(human-driven)*

Per spec §11.1 row M6. No new code per se — performance tuning, cache-line
alignment, RCU latency verification, the actual Spirent/Keysight runs
against the gate values in §11.1.

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

## How an agent picks up a PR

1. Open this document, find the "not started" PR you want to claim.
2. Re-read the spec sections it references **in full**.
3. Query Qdrant (`qdrant-find`) for any prior session insights on the
   files you're about to touch (per `.clinerules/70-qdrant-memory.md`).
4. Open the target files in the workspace; read enough context (200-2000
   lines) before writing.
5. Update `task_progress` with the PR's checklist on the first tool
   call. Mark items off as you go.
6. Run the acceptance check listed under the PR.
7. **Do not** open the next PR in the same session unless the user
   explicitly asks. Each PR is a discrete review unit.

## Coordination

- Spec updates flow back into `specs/ask-2.0-rewrite-spec.md`. Bump
  version number (currently v0.7); bump this document's "drives" line
  in sync.
- Hardware findings get stored in Qdrant per `.clinerules/70-qdrant-memory.md`
  with tags `ask-2.0`, `hardware-probe`, plus the relevant PR number.
- The spec §12.7 unknowns get answered in PR13 and the spec gets a §12.8
  "Confirmed hardware behaviour" appendix.