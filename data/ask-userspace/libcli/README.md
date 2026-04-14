# libcli — Pre-built aarch64 artifacts

| Field | Value |
|-------|-------|
| Source | https://github.com/dparrish/libcli |
| Version | 1.10.8 |
| Commit | `dcfd3b7` (Merge pull request #92 — staging-1.10.8) |
| Arch | aarch64 (ARM64) |
| Cross-compiler | `aarch64-linux-gnu-gcc (Debian 12.2.0-14) 12.2.0` |
| ASK patch | None required |
| Used by | `dpa_app`, `cmm` (ASK userspace — see `plans/ASK-USERSPACE.md`) |

## Contents

| File | Description |
|------|-------------|
| `libcli.h` | Public header (include in consumers) |
| `libcli.a` | Static library (link with `-lcli`) |
| `libcli.so` → `libcli.so.1.10` → `libcli.so.1.10.8` | Shared library |

## Rebuild

```bash
git clone --depth 1 https://github.com/dparrish/libcli.git /tmp/libcli
cd /tmp/libcli
make CC=aarch64-linux-gnu-gcc AR=aarch64-linux-gnu-ar TESTS=0
# Outputs: libcli.so.1.10.8, libcli.a, libcli.h
```

## Runtime dependency

`libcli.so` links against `libcrypt.so` (part of glibc — present in the VyOS rootfs).