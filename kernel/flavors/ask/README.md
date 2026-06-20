# ASK2 source tree — `kernel/flavors/ask/`

**Status:** **parked, not built.** The contents are real (a consolidated
`ask.ko` OOT module, a 46-patch in-tree PCD series `0002`–`0065`, an `askd`
userspace daemon, the genl uapi) but **nothing in the single collapsed build
stages them yet** — after the 2026-06-14 flavor collapse the `FLAVOR=ask`
patch-staging block was removed from `bin/ci-setup-kernel.sh` (commit `6896b0e`).
Re-wiring is gated on the **canonical PCD-layer decision** in
[`plans/ASK-MAINLINE-SDK-COMPAT.md`](../../../plans/ASK-MAINLINE-SDK-COMPAT.md)
§4b: the `patches/` PCD series and the shipping board PCD cluster
(`kernel/common/patches/board/0086`–`0119`) are **two mutually-exclusive
implementations of the same net-new `fman_pcd*.c` files**, not an additive
stack. Implementation architecture: [`specs/ask2-rewrite-spec.md`](../../../specs/ask2-rewrite-spec.md)
(v1.8, 2026-06-14).

This directory is the **modern rewrite** of the NXP ASK fast-path for
LS1046A. It supersedes the legacy ASK 1.x stack (proprietary `cdx.ko`,
`auto_bridge.ko`, `cmm`, `dpa_app`, the 5797-line in-tree-hooks patch,
and the vendored NXP SDK FMan/QMan/BMan driver overlay) in **entirety**.
The ASK 1.x source was deleted from this branch lineage. The
`patches/vendored-ask/` subdir (the legacy ASK 1.x in-tree-hooks `010`–`100`
series, targeting the deleted `sdk_dpaa`/`sdk_fman` trees) was **removed
2026-06-19** per the compat plan §4b DROP disposition. The only remaining
legacy residue is `patches/archive-grafted-2026-05-24/` — superseded graft
experiments, **ignored** (left in place, wired into no build).

The brand "ASK" carries forward unchanged, but ASK2 is **no longer a
separate build flavor**. Per the single-image decision (2026-06-14,
`plans/DUAL-DATAPLANE.md`), once re-wired the contents of this tree are
built into the **common** image — `ask.ko` would ship in **every** image,
dormant until `set system offload ask` engages it at runtime.
`kernel/flavors/ask/` remains only the *source location*; the directory
name is historical and does not imply a `FLAVOR=ask` build target.

## What actually lives here (on-disk, 2026-06-19)

```
kernel/flavors/ask/
├── README.md                  # this file
├── kernel-config/
│   ├── 90-kunit.config        # KUnit fragment for the PCD/ASK self-tests
│   └── .gitkeep
├── oot-modules/ask/           # the consolidated ask.ko (single module)
│   ├── ask_main.c ask_bridge.c ask_flow.c ask_flow_offload.c
│   ├── ask_hw.c ask_caam.c ask_xfrm.c ask_neigh.c ask_op.c
│   ├── ask_stats.c ask_debugfs.c ask_genl.c ask_genl_attr.c
│   ├── include/ask_internal.h
│   ├── include/uapi/linux/ask/ask.h   # genl uapi (the ASK binding ABI)
│   ├── tests/                 # in-module KUnit suites
│   ├── Kbuild Kconfig Makefile ci-build.sh README.md
├── patches/                   # in-tree kernel patch series (see patches/README.md)
│   ├── 0002-…0065-*.patch     # 46 productive PCD patches (NOT placeholders)
│   └── archive-grafted-2026-05-24/   # superseded graft experiments (ignore)
├── uapi/ask.yaml              # genl protocol definition (codegen source)
└── userspace/askd/            # askd control-plane daemon
```

(`ask_bridge` is the `ask_bridge.c` translation unit **inside** the single
`ask.ko`, not a separate module — the spec's notional `ask_bridge/` directory
was never materialised as a distinct build target.)

## ABI and config compatibility with ASK 1.x

Per spec §18, the following operator-facing surfaces are kept stable so
existing field configs, vendor tools, and the hardware microcode still
boot unchanged:

- `/etc/cdx_cfg.xml`, `/etc/cdx_pcd.xml`, `/etc/cdx_sp.xml` — same
  schemas, ingested by the new loader instead of `dpa_app`.
- `/dev/cdx_ctrl` — compat symlink to the new `/dev/ask_ctrl` chardev.
- `libfci.so.1` SONAME — symlinked to the new genl-wrapping library.
- `/etc/config/fastforward` — same ALG-exclusion list format, consumed
  by `askd`.

The load-bearing ASK binding contract is the **generic-netlink** uapi
(`oot-modules/ask/include/uapi/linux/ask/ask.h` + `uapi/ask.yaml`), NOT
the SDK `fmd` ioctl ABI (which belonged to the rejected SDK-lift and was
dropped in `6896b0e`).

## Implementation order

See [`specs/ask2-rewrite-spec.md`](../../../specs/ask2-rewrite-spec.md)
§19 for the agent-driven implementation cookbook and §15.5 for acceptance
gates. The immediate blocker is the canonical PCD-layer decision
(compat plan §4b / §7).