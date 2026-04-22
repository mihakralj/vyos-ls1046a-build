# fmlib — Pre-built aarch64 artifacts

| Field | Value |
|-------|-------|
| Source | https://github.com/nxp-qoriq/fmlib |
| Version | `lf-6.18.2-1.0.0` (commit `7a58eca`) |
| ASK patch | `01-mono-ask-extensions.patch` (included, 180 lines) |
| Arch | aarch64 (ARM64) |
| Cross-compiler | `aarch64-linux-gnu-gcc (Debian 12.2.0-14) 12.2.0` |
| Kernel headers | `nxp-linux/include/uapi/linux/fmd/` (NXP SDK FMan ioctl headers) |
| Used by | `fmc`, `dpa_app` (ASK userspace — see `plans/ASK-USERSPACE.md`) |

## Contents

| File / Dir | Description |
|------------|-------------|
| `libfm.a` | Static library (aarch64 ELF64, 32K) — link with `-lfm` |
| `include/fmd/` | Public headers (Peripherals, integrations) — patched with ASK extensions |
| `01-mono-ask-extensions.patch` | ASK patch: timestamp, hash table, IP reassembly, shared scheme support |

## ASK Patch Adds

- `FM_ReadTimeStamp()` / `FM_GetTimeStampIncrementPerUsec()` — timestamp reading
- Hash table type enumeration + extended `t_FmPcdHashTableParams`
- `FM_PCD_Get_Sch_handle()` — scheme handle access
- `shared` field in `t_FmPcdKgSchemeParams`

## Rebuild

```bash
git clone --depth 1 https://github.com/nxp-qoriq/fmlib.git /tmp/fmlib
cd /tmp/fmlib
patch --no-backup-if-mismatch -p1 < 01-mono-ask-extensions.patch
make libfm-arm.a CROSS_COMPILE=aarch64-linux-gnu- KERNEL_SRC=<path-to-nxp-linux>
# Output: libfm-arm.a (rename to libfm.a)
```

## Build dependency

Requires NXP SDK kernel headers (`include/uapi/linux/fmd/`) for FMan ioctl definitions (`fm_ioctls.h`, `fm_pcd_ioctls.h`). These come from `nxp-linux` (NXP LSDK kernel fork).