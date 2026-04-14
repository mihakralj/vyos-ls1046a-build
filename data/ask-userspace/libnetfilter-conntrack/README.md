# libnetfilter-conntrack — Patched for ASK Fast-Path

Pre-built aarch64 shared library with NXP ASK Comcerto fast-path extensions.
Used by `cmm` (Connection Manager Module) to read/write fast-path offload
attributes from conntrack entries via netlink.

## Source

- **Repo:** https://git.netfilter.org/libnetfilter_conntrack
- **Tag:** `libnetfilter_conntrack-1.0.9`
- **Commit:** `7a8e1e2`

## ASK Patch

`01-nxp-ask-comcerto-fp-extensions.patch` (700 lines, 15 files) — adds:

- `IPS_PERMANENT` / `IPS_DPI_ALLOWED` connection status bits
- 10 MB socket receive buffer for high-volume connection tracking
- Comcerto fast-path info struct per direction (`ifindex`, `iif`, `mark`,
  `underlying_iif`, `underlying_vlan_id`, `xfrm_handle[4]`)
- `CTA_LAYERSCAPE_FP_ORIG` / `CTA_LAYERSCAPE_FP_REPLY` nested netlink attributes
- `CTA_QOSCONNMARK` (64-bit QoS connection mark)
- `nfct_clear()` API for object reuse without reallocation
- Full getter/setter/copy/compare/snprintf support for all new attributes

## Contents

| File | Description |
|------|-------------|
| `libnetfilter_conntrack.so.3.8.0` | ELF 64-bit aarch64 shared library (602K) |
| `libnetfilter_conntrack.so.3` | SONAME symlink |
| `libnetfilter_conntrack.so` | Development symlink |
| `include/libnetfilter_conntrack/` | Public API headers (patched) |
| `pkgconfig/libnetfilter_conntrack.pc` | pkg-config file |
| `01-nxp-ask-comcerto-fp-extensions.patch` | ASK patch applied to source |

## Build

Cross-compiled on Debian 12 (x86_64) with `aarch64-linux-gnu-gcc` 12.2.0-14:

```bash
git clone https://git.netfilter.org/libnetfilter_conntrack && cd libnetfilter_conntrack
git checkout libnetfilter_conntrack-1.0.9
patch -p1 < 01-nxp-ask-comcerto-fp-extensions.patch
autoreconf -fi
LIBNFNETLINK_CFLAGS="-I<libnfnetlink>/include" \
LIBNFNETLINK_LIBS="-L<libnfnetlink> -lnfnetlink" \
LIBMNL_CFLAGS="-I/usr/include" \
LIBMNL_LIBS="-L/usr/lib/aarch64-linux-gnu -lmnl" \
./configure --host=aarch64-linux-gnu CC=aarch64-linux-gnu-gcc
make -j$(nproc) && make install DESTDIR=<staging>
```

## Dependencies

- **Build-time:** libnfnetlink (patched, from `data/ask-userspace/libnfnetlink/`), libmnl-dev:arm64
- **Runtime:** libnfnetlink.so (patched), libmnl.so (standard Debian)