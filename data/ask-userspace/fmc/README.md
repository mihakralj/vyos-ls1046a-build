# fmc — Frame Manager Configuration Tool

Pre-built aarch64 binary and static library for the NXP FMC tool, used by
`dpa_app` to program FMan PCD (Packet Classification & Distribution) rules.

## Source

- **Repo:** https://github.com/nxp-qoriq/fmc
- **Tag:** `lf-6.18.2-1.0.0`
- **Commit:** `5b9f4b1`

## ASK Patch

`01-mono-ask-extensions.patch` — adds:

- Port ID (`portid`) field in port configuration output
- Shared scheme support with CC/HT node replication (required for CDX multi-port PCD)
- PPPoE `nextp` field fix (`NET_HEADER_FIELD_PPPoE_PID`)
- libxml2 2.13+ compatibility (`xmlError` const-correctness)

## Contents

| File | Description |
|------|-------------|
| `fmc` | ELF 64-bit aarch64 executable (dynamically linked, needs libxml2 + libstdc++) |
| `libfmc.a` | Static library for linking into `dpa_app` |
| `fmc.h` | Public API header |
| `01-mono-ask-extensions.patch` | ASK patch applied to source |

## Build

Cross-compiled on Debian 12 (x86_64) with `aarch64-linux-gnu-g++` 12.2.0-14:

```bash
git clone https://github.com/nxp-qoriq/fmc && cd fmc
git checkout lf-6.18.2-1.0.0
find . \( -name "*.cpp" -o -name "*.h" -o -name "*.c" \) -exec sed -i 's/\r$//' {} +
patch -p1 < 01-mono-ask-extensions.patch
cd source && make libfmc.a fmc \
  CC=aarch64-linux-gnu-gcc CXX=aarch64-linux-gnu-g++ AR=aarch64-linux-gnu-ar \
  MACHINE=ls1046 \
  FMD_USPACE_HEADER_PATH=<fmlib>/include/fmd \
  FMD_USPACE_LIB_PATH=<fmlib> \
  LIBXML2_HEADER_PATH=/usr/include/libxml2 \
  TCLAP_HEADER_PATH=/usr/include
```

## Dependencies

- **Build-time:** fmlib (static, from `data/ask-userspace/fmlib/`), libxml2-dev:arm64, tclap (header-only)
- **Runtime:** libxml2, libstdc++6 (both available in VyOS ISO)