# libnfnetlink — Pre-built aarch64 artifacts

| Field | Value |
|-------|-------|
| Source | https://git.netfilter.org/libnfnetlink |
| Version | 1.0.2 (commit `5fec628`) |
| ASK patch | `01-nxp-ask-nonblocking-heap-buffer.patch` (included, 190 lines) |
| Arch | aarch64 (ARM64) |
| Cross-compiler | `aarch64-linux-gnu-gcc (Debian 12.2.0-14) 12.2.0` |
| Used by | `libnetfilter-conntrack`, `cmm` (ASK userspace — see `plans/ASK-USERSPACE.md`) |

## Contents

| File / Dir | Description |
|------------|-------------|
| `libnfnetlink.so` → `.so.0` → `.so.0.2.0` | Shared library (aarch64 ELF64, 135K) |
| `include/libnfnetlink/` | Public headers (patched with nonblocking/heap buffer APIs) |
| `pkgconfig/libnfnetlink.pc` | pkg-config metadata |
| `01-nxp-ask-nonblocking-heap-buffer.patch` | ASK patch for reproducibility |

## ASK Patch Adds

- `nfnl_set_nonblocking_mode()` / `nfnl_unset_nonblocking_mode()` — non-blocking socket mode
- Heap-allocated receive buffer (prevents stack overflow with large netlink messages)
- Required for CMM daemon high-throughput netlink communication

## Rebuild

```bash
git clone --depth 1 https://git.netfilter.org/libnfnetlink /tmp/libnfnetlink
cd /tmp/libnfnetlink
patch --no-backup-if-mismatch -p1 < 01-nxp-ask-nonblocking-heap-buffer.patch
./autogen.sh
./configure --host=aarch64-linux-gnu --prefix=/usr/local
make
# Output: src/.libs/libnfnetlink.so.0.2.0
```

## Runtime dependency

No external dependencies beyond glibc.