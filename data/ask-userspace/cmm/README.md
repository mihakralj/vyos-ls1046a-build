# cmm — Connection Manager Module Daemon

## Overview

Userspace daemon that monitors Linux conntrack (nf_conntrack) for eligible flows and offloads them to the FMan hardware fast-path via the FCI kernel module. CMM is the bridge between Linux networking state and CDX hardware forwarding tables.

## Artifacts

| File | Size | Description |
|------|------|-------------|
| `cmm` | 388K | Daemon binary (aarch64, dynamically linked, stripped) |
| `libcmm.so` → `libcmm.so.0.0.0` | 77K | Shared library for CMM client API |
| `libcmm.a` | 20K | Static library for CMM client API |

## Runtime Dependencies

| Library | Package | Purpose |
|---------|---------|---------|
| `libfci.so.0` | ASK (data/ask-userspace/fci/) | FCI kernel module communication |
| `libcli.so.1.10` | ASK (data/ask-userspace/libcli/) | CLI interface for `cmm` command shell |
| `libnetfilter_conntrack.so.3` | ASK-patched (data/ask-userspace/libnetfilter-conntrack/) | Conntrack event monitoring with fast-path extensions |
| `libnfnetlink.so.0` | ASK-patched (data/ask-userspace/libnfnetlink/) | Netfilter netlink socket library |
| `libpcap.so.0.8` | System (`libpcap0.8`) | Packet capture for diagnostic mode |
| `libc.so.6` | System | Standard C library |

## Build Details

- **Source:** `ASK/cmm/` (autotools)
- **Cross-compiler:** `aarch64-linux-gnu-gcc` 12.2.0-14
- **Platform define:** `-DLS1043` (LS104x family)
- **Warning suppressions:** `-Wno-address-of-packed-member -Wno-stringop-truncation -Wno-use-after-free -Wno-unused-label`

### Build command

```bash
cd ASK/cmm
export CFLAGS="-DLS1043 -Wno-address-of-packed-member -Wno-stringop-truncation \
  -Wno-use-after-free -Wno-unused-label \
  -I$PWD/../../data/ask-userspace/libcli \
  -I$PWD/../../data/ask-userspace/fci \
  -I$PWD/../../data/ask-userspace/libnfnetlink/include \
  -I$PWD/../../data/ask-userspace/libnetfilter-conntrack/include -O2"
export PKG_CONFIG_PATH="$PWD/../../data/ask-userspace/libnfnetlink/pkgconfig:$PWD/../../data/ask-userspace/libnetfilter-conntrack/pkgconfig"
export PKG_CONFIG_LIBDIR="$PKG_CONFIG_PATH"
export LDFLAGS="-L$PWD/../../data/ask-userspace/libcli \
  -L$PWD/../../data/ask-userspace/fci \
  -L$PWD/../../data/ask-userspace/libnfnetlink \
  -L$PWD/../../data/ask-userspace/libnetfilter-conntrack"

./configure --host=aarch64-linux-gnu --build=x86_64-linux-gnu CC=aarch64-linux-gnu-gcc
make -j$(nproc)

# If autotools link fails (libtool cross-compile path issue), manual link:
cd src && aarch64-linux-gnu-gcc -o cmm *.o .libs/libcmm.a \
  -L../../data/ask-userspace/libcli \
  -L../../data/ask-userspace/fci \
  -L../../data/ask-userspace/libnfnetlink \
  -L../../data/ask-userspace/libnetfilter-conntrack \
  -lpthread -lfci -lcli -lnetfilter_conntrack -lnfnetlink -lpcap
```

### Build prerequisites

- `config.sub` / `config.guess` must be replaced with system copies for aarch64 support
- `src/version.h` must exist (auto-generated from git or manually created)
- `-DLS1043` required for `fpp_qm_reset_cmd_t` definition

### Source modifications for GCC 12

- `-Wno-address-of-packed-member`: packed struct pointer casts throughout
- `-Wno-stringop-truncation`: intentional `strncpy` without NUL terminator
- `-Wno-use-after-free`: false positive on conntrack object lifecycle
- `-Wno-unused-label`: `proceed_to_lro` label in `itf.c:734` (conditional compilation)

## Installation

```bash
# Binary
cp cmm /usr/local/sbin/cmm

# Libraries (all ASK .so files)
cp libfci.so.0.1 libcli.so.1.10.8 libcmm.so.0.0.0 \
   libnfnetlink.so.0.2.0 libnetfilter_conntrack.so.3.8.0 \
   /usr/local/lib/
ldconfig

# Config files
cp ASK/config/fastforward /etc/fastforward
cp ASK/config/cmm.service /etc/systemd/system/cmm.service
systemctl daemon-reload
```

## Configuration

- `/etc/fastforward` — Exclusion rules (FTP, SIP, PPTP bypass fast-path)
- CMM connects to FCI via netlink to offload flows
- Guarded by `ConditionPathExists=/dev/cdx_ctrl` in systemd service

## Runtime Architecture

```
nf_conntrack → CMM daemon → FCI netlink → fci.ko → CDX → FMan PCD
                  ↕
             /proc/fast_path (stats)
             libcli (CLI shell)
```

## Module Load Order

```
cdx.ko → dpa_app → fci.ko → auto_bridge.ko → cmm.service