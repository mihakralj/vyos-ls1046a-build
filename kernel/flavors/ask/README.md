# ASK2 source tree — `kernel/flavors/ask/`

**Status:** scaffold only. Implementation tracked in
[`specs/ask2-rewrite-spec.md`](../../../specs/ask2-rewrite-spec.md)
(v1.8, 2026-06-14).

This directory is the **modern rewrite** of the NXP ASK fast-path for
LS1046A. It supersedes the legacy ASK 1.x stack (proprietary `cdx.ko`,
`auto_bridge.ko`, `cmm`, `dpa_app`, the 5797-line in-tree-hooks patch,
and the 266-file vendored NXP SDK FMan/QMan/BMan driver overlay) in
**entirety**. Everything ASK 1.x was deleted from this branch
(`ask20`) on 2026-05-12.

The brand "ASK" carries forward unchanged, but ASK2 is **no longer a
separate build flavor**. Per the single-image decision (2026-06-14,
`specs/dual-dataplane.md`), the contents of this tree are wired into the
**common** build — `ask.ko` ships in **every** image, dormant until
`set system offload ask` engages it at runtime. `kernel/flavors/ask/`
remains only the *source location* for ASK-specific patches and OOT code;
the directory name is historical and does not imply a `FLAVOR=ask` build
target. The 210-series FMan microcode (loaded by U-Boot from SPI flash on
every shipped Mono Gateway) is also unchanged — ASK2 sits on top of it
without touching the binary.

## What lives here

```
kernel/flavors/ask/
├── README.md                  # this file
├── kernel-config/             # ASK kernel config fragments (TBD)
├── manifest.json              # generated at CI time (TBD)
├── kernel.pin                 # 6.18.x LTS pin (TBD)
├── oot-modules/               # OOT kernel modules
│   ├── ask/                   # ~1500 LOC C — replaces cdx.ko (spec §4)
│   └── ask_bridge/            # ~400 LOC C  — replaces auto_bridge.ko (spec §5)
├── patches/                   # in-tree kernel patches
│   ├── 200-ask2-hooks.patch       # ~1500 lines (down from 5797) (spec §10)
│   └── 201-caam-qi-share.patch    # ~200 lines, candidate for upstream (spec §8.3)
└── userspace/                 # userspace daemons + library
    ├── askd/                  # ~6000 LOC C — replaces cmm (spec §6)
    ├── ask-load/              # ~1200 LOC C — replaces dpa_app (spec §9)
    └── libask_fci/            # ~800 LOC C  — replaces libfci (spec §7.4)
```

## ABI and config compatibility with ASK 1.x

Per spec §18, the following operator-facing surfaces are kept stable so
existing field configs, vendor tools, and the hardware microcode still
boot unchanged:

- `/etc/cdx_cfg.xml`, `/etc/cdx_pcd.xml`, `/etc/cdx_sp.xml` — same
  schemas, ingested by `ask-load` instead of `dpa_app`.
- `/dev/cdx_ctrl` — symlink to the new `/dev/ask_ctrl` chardev; the
  legacy ioctl numbers and structs work via a compat shim in
  `oot-modules/ask/ask_dev.c`.
- `libfci.so.1` SONAME — symlinked to `libask_fci.so.1`, which wraps
  the new generic-netlink protocol.
- `/etc/config/fastforward` — same ALG-exclusion list format,
  consumed by `askd`.

## What stays from the legacy stack

Nothing in source form. The only carryovers are the operator-visible
schemas and ABI surfaces above, plus the `xt_QOSMARK` and `xt_QOSCONNMARK`
match modules (verbatim copy into `200-ask2-hooks.patch` per spec §10.1).

## Implementation order

See [`specs/ask2-rewrite-spec.md`](../../../specs/ask2-rewrite-spec.md)
§19 for the agent-driven implementation cookbook. Acceptance gates are
in §15.5.