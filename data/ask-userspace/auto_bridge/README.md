# auto_bridge.ko — Automatic Bridging Module (ABM)

## Overview

Kernel module that monitors Linux bridge port membership changes and notifies CDX of L2-offloadable flows. When a bridge port is added/removed, ABM updates CDX forwarding tables so bridged traffic can be hardware-accelerated by FMan.

## Artifacts

| File | Size | Description |
|------|------|-------------|
| `auto_bridge.ko` | 134K | Kernel module (aarch64, ELF64 relocatable) |

## Build Details

- **Source:** `ASK/auto_bridge/auto_bridge.c` (single-file module)
- **Cross-compiler:** `aarch64-linux-gnu-gcc` 12.2.0-14
- **Kernel:** 6.6.129 (SDK+ASK branch)
- **Platform:** `LS1043A` (`-DLS104X`)
- **Vermagic:** `6.6.129-00002-g7dda8caa07ad-dirty SMP preempt mod_unload modversions aarch64`

### Build command

```bash
cd ASK/auto_bridge && \
make CROSS_COMPILE=aarch64-linux-gnu- ARCH=arm64 PLATFORM=LS1043A \
  KERNEL_SOURCE=/opt/vyos-dev/linux all
```

### Prerequisites

- Kernel Module.symvers must contain bridge symbols from ASK kernel hooks patch (`003-ask-kernel-hooks.patch`):
  - `br_fdb_register_can_expire_cb` / `br_fdb_deregister_can_expire_cb`
  - `register_brevent_notifier` / `unregister_brevent_notifier`
  - `rtmsg_ifinfo` (exported via kernel modification)
- CDX symbols from `cdx.ko` Module.symvers must be merged into kernel Module.symvers
- Bridge module (`bridge.ko` or `CONFIG_BRIDGE=m`) must be built first

### Source modifications for kernel 6.6

- `auto_bridge.c:1385`: Changed `const struct ctl_table *ctl` → `struct ctl_table *ctl` (kernel 6.6 `proc_handler` signature)
- `auto_bridge.c:1500`: Added `__maybe_unused` to `abm_sysctl_fini` (dead code, unreferenced)

### Modpost warnings (benign)

```
WARNING: modpost: auto_bridge: section mismatch in reference: abm_init → auto_bridge_version (.init.data)
```

Non-init function references `__initdata` variable — cosmetic only, no runtime impact.

## Dependencies

- `bridge.ko` — Linux bridge module (runtime `depends: bridge`)
- `cdx.ko` — CDX core module (symbol dependency for flow table updates)

## Installation

```bash
cp auto_bridge.ko /lib/modules/$(uname -r)/extra/
depmod -a
```

## Load order

```
bridge → cdx → auto_bridge
```

Configured via `ASK/config/ask-modules.conf` → `/etc/modules-load.d/ask.conf`.