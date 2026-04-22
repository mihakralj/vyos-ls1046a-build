# fci.ko + libfci — Fast Control Interface

FCI kernel module and userspace library for ASK fast-path offload on DPAA1.

## Artifacts

| File | Description |
|------|-------------|
| `fci.ko` | Kernel module (aarch64, 85 KB) |
| `fci.h` | Kernel module header (FCI message structures) |
| `libfci.so` → `libfci.so.0.1` | Userspace shared library (aarch64, 69 KB) |
| `libfci.h` | Userspace API header |

## Build

### fci.ko (kernel module)

Cross-compiled on Debian 12 x86_64 against SDK kernel 6.6.129:

```bash
cd ASK/fci
make CROSS_COMPILE=aarch64-linux-gnu- BOARD_ARCH=arm64 \
     KERNEL_SOURCE=/opt/vyos-dev/linux \
     KBUILD_EXTRA_SYMBOLS=/path/to/cdx/Module.symvers \
     modules
```

### libfci.so (userspace library)

Pre-built from `ASK/fci/lib/` autotools project. Used by cmm daemon to communicate with fci.ko via netlink.

## What It Does

FCI is the control-plane interface between userspace (cmm daemon) and the kernel fast-path engine (cdx.ko):

1. Creates a **netlink socket** (protocol `NETLINK_FF`) for bidirectional communication
2. Receives flow offload commands from cmm via netlink
3. Dispatches commands to cdx.ko via `comcerto_fpp_send_command()`
4. Sends events (flow expiry, stats) back to cmm via netlink multicast

### Data Flow

```
cmm daemon → libfci.so → netlink → fci.ko → cdx.ko → FMan hardware
```

## Dependencies

- **cdx.ko** must be loaded first (provides `comcerto_fpp_send_command` and `comcerto_fpp_register_event_cb`)
- Module load order in `ask-modules.conf`: `cdx` → `fci`

## Install

```bash
# Kernel module
sudo cp fci.ko /lib/modules/$(uname -r)/extra/
sudo depmod -a

# Userspace library (for cmm)
sudo cp libfci.so.0.1 /usr/lib/aarch64-linux-gnu/
sudo ln -sf libfci.so.0.1 /usr/lib/aarch64-linux-gnu/libfci.so.0
sudo ln -sf libfci.so.0 /usr/lib/aarch64-linux-gnu/libfci.so
sudo ldconfig
```

## Kernel Compatibility

- **Built against:** 6.6.129-00002-g7dda8caa07ad (SDK kernel with NXP DPAA drivers)
- **Depends:** cdx module
- **Must match running kernel exactly** — module vermagic enforced