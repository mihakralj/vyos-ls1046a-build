# ASK2 source tree — `kernel/ask/`

**Status:** shipping. `ask.ko` is built into **every** image and reaches
10.259 Gbps line rate at 0.16% CPU on silicon. Execution plan:
[`plans/ASK2-MASTER-PLAN.md`](../../plans/ASK2-MASTER-PLAN.md).
Architecture index:
[`specs/ask2-rewrite-spec.md`](../../specs/ask2-rewrite-spec.md).
State machine + CLI contract:
[`plans/DUAL-DATAPLANE.md`](../../plans/DUAL-DATAPLANE.md).

This directory is the **modern rewrite** of the NXP ASK fast-path for
LS1046A. It supersedes the legacy ASK 1.x stack (proprietary `cdx.ko`,
`auto_bridge.ko`, `cmm`, `dpa_app`, the 5797-line in-tree-hooks patch,
and the 266-file vendored NXP SDK FMan/QMan/BMan driver overlay) in
**entirety**. Everything ASK 1.x was deleted on 2026-05-12.

ASK2 is **not a build variant**. The `default|ask|vpp` flavor split was
retired 2026-06-14 and the `FLAVOR` variable removed 2026-07-26; this tree
moved from `kernel/flavors/ask/` to `kernel/ask/` at the same time. `ask.ko`
ships in every image, dormant until
`set interfaces ethernet eth<n> offload ask` engages it per-interface at
runtime. The 210-series FMan microcode (loaded by U-Boot from SPI flash on
every shipped Mono Gateway) is unchanged — ASK2 sits on top of it without
touching the binary.

## What actually lives here

```
kernel/ask/
├── README.md                  # this file
├── kernel-config/
│   └── 90-kunit.config        # OPT-IN kunit fragment; NOT applied to
│                              # production builds (see stage-kernel.sh)
├── uapi/
│   └── ask.yaml               # YNL generic-netlink family spec; copied to
│                              # /usr/share/ynl/specs/ in the chroot and
│                              # driven by `ynl --family ask`
├── oot-modules/ask/           # ask.ko — the whole ASK2 control plane
│   ├── Kbuild Kconfig Makefile ci-build.sh
│   ├── ask_main.c             # module init/exit
│   ├── ask_genl.c             # generic-netlink family
│   ├── ask_genl_attr.c        # attribute encode/decode
│   ├── ask_hw.c               # FMan PCD engage/disengage, flow insert
│   ├── ask_flow.c             # flow table (rhashtable)
│   ├── ask_flow_offload.c     # nf_flow_table / TC_SETUP_FT backend
│   ├── ask_neigh.c            # arp_tbl + nd_tbl netevent notifier
│   ├── ask_bridge.c           # switchdev (stub — T-M6-2)
│   ├── ask_xfrm.c             # xfrmdev_ops IPsec (stub — T-M6-4)
│   ├── ask_caam.c ask_op.c ask_stats.c ask_debugfs.c
│   ├── ask_trace.h
│   ├── include/               # ask_internal.h, ask_fman_caps.h,
│   │                          # uapi/linux/ask/ask.h
│   └── tests/                 # kunit suites (CONFIG_NET_ASK_KUNIT_TEST)
├── patches/                   # frozen archives only — see patches/README.md
└── userspace/askd/            # placeholder; no daemon exists (see below)
```

The in-tree kernel side of ASK2 is **not** here — it lives in
[`kernel/common/patches/board/`](../common/patches/board/) (the FMan PCD
subsystem, KeyGen, CC, MANIP, Policer, FE-VM ehash: patches 0092–0164) plus
the `bin/kernel-fixups/F_*.py` source fixups.

## No userspace daemon

Earlier revisions of this document described `askd`, `ask-load`, and
`libask_fci` replacing `cmm`, `dpa_app`, and `libfci`, along with
`/dev/cdx_ctrl` and `/etc/cdx_*.xml` ABI-compat shims. **None of that was
built and none of it is planned.** ASK2's control plane is the kernel module
plus the VyOS CLI: configuration through
`set interfaces ethernet eth<n> offload ask`, observability through
`show interfaces ethernet eth<n> offload ask flows` (a thin `ynl` wrapper).
The `userspace/askd/` directory is a leftover placeholder.

## Implementation order

See [`plans/ASK2-MASTER-PLAN.md`](../../plans/ASK2-MASTER-PLAN.md) §4
(milestone chain) and §5 (live TODO list). Acceptance gates are in §4;
harness and gate mechanics in §7.
