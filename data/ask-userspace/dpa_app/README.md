# dpa_app — DPAA Application (ASK FMan PCD Configurator)

Pre-built aarch64 binary for the NXP ASK `dpa_app` utility.

## What It Does

`dpa_app` is a one-shot userspace program invoked by `cdx.ko` (via `call_usermodehelper`) at module load time. It:

1. Opens `/dev/cdx_ctrl` (created by `cdx.ko`)
2. Reads FMan configuration from `/etc/cdx_cfg.xml` (port-to-policy mapping)
3. Reads packet classification rules from `/etc/cdx_pcd.xml` (hash tables for flow offload)
4. Reads soft parser rules from `/etc/cdx_sp.xml` (NetPDL custom protocol handlers)
5. Programs the FMan PCD hardware via `/dev/fm0-pcd` ioctl interface
6. Exits — not a daemon

Without `dpa_app`, the FMan hardware has no classification/distribution rules and cannot offload flows.

## Artifacts

| File | Size | Description |
|------|------|-------------|
| `dpa_app` | 1.5M | ELF 64-bit aarch64 binary |
| `cdx_ioctl.h` | 11K | CDX ioctl header (build dependency for Step 4: cdx.ko) |
| `etc/cdx_cfg.xml` | 750B | Generic FMan port config (6×1G + 1×10G + 2×OH) |
| `etc/cdx_cfg_mono_gw.xml` | 602B | **Mono Gateway** port config (3×1G + 2×10G + 2×OH) |
| `etc/cdx_pcd.xml` | 18K | Packet classification: UDP/TCP/ESP/multicast/PPPoE/ethernet hash tables |
| `etc/cdx_sp.xml` | 7.1K | Soft parser: PPPoE relay, TTL/hop-limit drop, TCP FIN/RST/SYN to host |

## Installation

```bash
# Binary
install -m 755 dpa_app /usr/local/bin/dpa_app

# Config files (use cdx_cfg_mono_gw.xml for Mono Gateway hardware)
install -m 644 etc/cdx_cfg_mono_gw.xml /etc/cdx_cfg.xml
install -m 644 etc/cdx_pcd.xml /etc/cdx_pcd.xml
install -m 644 etc/cdx_sp.xml /etc/cdx_sp.xml
```

## Runtime Dependencies

- `/dev/cdx_ctrl` — created by `cdx.ko` (Step 4)
- `/dev/fm0-pcd` — created by SDK FMan driver with ASK-enabled microcode
- `libxml2.so.2` — XML config parsing
- `libstdc++.so.6` — C++ runtime (from libfmc)
- `libcrypt.so.1` — libcli dependency

## Build Details

| Field | Value |
|-------|-------|
| Source | `ASK/dpa_app/` (3 files: main.c, dpa.c, testapp.c) |
| Compiler | `aarch64-linux-gnu-gcc` 12.2.0-14 (Debian) |
| CFLAGS | `-DDPAA_DEBUG_ENABLE -DNCSW_LINUX` |
| Static libs | `libfmc.a` (3.2M), `libfm.a` (116K), `libcli.a` (66K) |
| Dynamic libs | libxml2, libstdc++, libcrypt, libpthread |
| Key flag | `-DNCSW_LINUX` selects `types_linux.h` (without it, build fails looking for `types_bb_gcc.h`) |

### Build Command

```bash
cd /tmp/dpa_app_build
# Copy sources
cp ASK/dpa_app/{main,dpa,testapp}.c .
cp ASK/cdx/cdx_ioctl.h .
cp data/ask-userspace/fmc/fmc.h .

# Compile
aarch64-linux-gnu-gcc -DDPAA_DEBUG_ENABLE -DNCSW_LINUX \
  -I. \
  -Idata/ask-userspace/fmlib/include/fmd \
  -Idata/ask-userspace/fmlib/include/fmd/Peripherals \
  -Idata/ask-userspace/fmlib/include/fmd/integrations \
  -Idata/ask-userspace/libcli \
  -c main.c dpa.c testapp.c

# Link
aarch64-linux-gnu-gcc -o dpa_app main.o dpa.o testapp.o \
  data/ask-userspace/fmc/libfmc.a \
  data/ask-userspace/fmlib/libfm.a \
  data/ask-userspace/libcli/libcli.a \
  -lxml2 -lstdc++ -lpthread -lcrypt
```

## Config File Notes

### cdx_cfg_mono_gw.xml vs cdx_cfg.xml

- `cdx_cfg.xml` — generic LS1046A layout with 6×1G ports (numbers 0–5). Does NOT match Mono Gateway hardware.
- `cdx_cfg_mono_gw.xml` — **use this** for Mono Gateway. Maps only the 3 RJ45 (1G ports 1,4,5) and 2 SFP+ (10G ports 0,1) plus 2 offline ports. Port IDs match the CDX internal numbering.

### cdx_pcd.xml

Defines 16 classification hash tables and 18 distribution schemes covering:
- IPv4/IPv6 TCP/UDP 5-tuple flows
- ESP (IPsec) flows by SPI
- Multicast groups
- PPPoE sessions
- L2 ethernet bridging
- 3-tuple (dst+proto+dport) for server-side offload
- IPv4/IPv6 fragment reassembly

All hash tables use `shared="true"` — the same table instance is reused across all port policies.

### cdx_sp.xml (Soft Parser)

NetPDL custom protocol handlers loaded into FMan's soft parser:
- **PPPoE**: Separates control (LCP) from session packets; session packets go to classification, control goes to host
- **IPv4/IPv6**: Drops packets with TTL/hop-limit ≤1 (hardware TTL enforcement)
- **TCP**: Sends FIN/RST/SYN packets to host (connection state changes must reach Linux stack)
- **UDP**: Detects ESP-in-UDP (NAT-T, port 4500) and sends ISAKMP packets to host
- **Ethernet (offline ports)**: Corrects L3 header parsing for packets re-injected via offline ports

## Tested

- **Device**: Mono Gateway (192.168.1.189), VyOS 6.6.129 SDK kernel
- **ldd**: All dynamic dependencies resolved
- **Run**: `dpa_init:unable to open dev /dev/cdx_ctrl` — **expected** (cdx.ko not yet loaded, Step 4)