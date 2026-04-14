# cdx.ko — ASK Core Kernel Module

Core CDX (Connection Data eXchange) kernel module for NXP ASK fast-path offload on DPAA1.

## Artifacts

| File | Description |
|------|-------------|
| `cdx.ko` | Kernel module (aarch64, 2.1 MB) |
| `Module.symvers` | Exported symbols (10 symbols — needed by fci.ko, auto_bridge.ko) |

## Build

Cross-compiled on Debian 12 x86_64 against SDK kernel 6.6.129 at `/opt/vyos-dev/linux`:

```bash
# Source: ASK/cdx/ (~40 .c files)
# Pre-create version.h (no .git in build dir)
echo '#define CDX_VERSION "mono-gw-ask-1.0"' > /tmp/cdx_build/version.h

cd /tmp/cdx_build
make CROSS_COMPILE=aarch64-linux-gnu- ARCH=arm64 KERNELDIR=/opt/vyos-dev/linux modules
```

- **42 object files** compiled with `-Werror` — zero errors
- One non-fatal modpost WARNING: section mismatch (`cdx_ctrl_deinit` in `.text` references `cdx_cmdhandler_exit` in `.exit.text`)
- Kbuild includes `ncsw_config.mk` from `sdk_fman/` for all NXP DPAA header paths
- Build flags: `-DDPA_IPSEC_OFFLOAD -DENDIAN_LITTLE -DGCC_TOOLCHAIN`

## What It Does

CDX is the kernel-side fast-path engine. It:

1. Creates `/dev/cdx_ctrl` chardev (major 239)
2. Launches `/usr/bin/dpa_app` via `call_usermodehelper` at init — dpa_app programs FMan PCD classification rules
3. Manages hardware hash tables for 5-tuple (TCP/UDP), ESP, PPPoE, and multicast flows
4. Receives flow offload commands from fci.ko (which gets them from cmm daemon)
5. Programs FMan exact-match hash entries so matching packets bypass the Linux network stack entirely

## Exported Symbols

```
comcerto_fpp_send_command        — FCI command dispatch (used by fci.ko)
comcerto_fpp_send_command_simple — Simplified FCI command
comcerto_fpp_send_command_atomic — Atomic FCI command (interrupt-safe)
comcerto_fpp_register_event_cb   — Register event callback
display_itf                      — Debug: display interface info
display_route_entry              — Debug: display route entry
display_ctentry                  — Debug: display conntrack entry
dpa_get_pcdhandle                — Get FMan PCD handle
dpa_get_fm_MURAM_handle          — Get FMan MURAM handle
display_SockEntries              — Debug: display socket entries
```

## Install

```bash
sudo cp cdx.ko /lib/modules/$(uname -r)/extra/
sudo depmod -a
```

## Runtime Dependencies

cdx.ko requires the full ASK stack to function without errors:

1. **ASK-enabled FMan microcode** (v210.10.1) — loaded by U-Boot from SPI flash
2. **SDK kernel** with `fsl_dpa`, `fsl_mac`, `fsl_qbman` drivers (built-in)
3. **`/usr/bin/dpa_app`** + `/etc/cdx_cfg.xml` + `/etc/cdx_pcd.xml` — FMan PCD configuration
4. **`fp_netfilter`** kernel module — conntrack hooks for flow tracking

### Known Init Failures Without Full Stack

When loaded standalone (without dpa_app and config XMLs):

| Message | Cause | Severity |
|---------|-------|----------|
| `start_dpa_app failed rc 65280` | `/usr/bin/dpa_app` not installed | Non-fatal (continues) |
| `cdx_init_frag_module failed` | BMan pool not configured | Non-fatal (continues) |
| `alloc_offline_port::no free of ports` | OH ports not probed (DTB issue) | Non-fatal |
| `dpa_ipsec start failed` | No OH port for SEC re-injection | Non-fatal |
| **Oops in `dpa_update_timestamp`** | Timer fires before cdx_info initialized | **Fatal** — NULL deref crash |

The timer crash occurs because `cdx_ctrl_timer` dereferences `cdx_info` members that are NULL when `dpa_app` hasn't configured FMan tables. In production, the correct boot sequence (via `ask-modules.conf` + `cmm.service`) prevents this.

## Code Quality Notes

- `class_create()` uses single-arg API — correct for kernel 6.4+
- `MODULE_LICENSE("GPL")` — proper license declaration
- `deinit_fn[]` callback array for orderly cleanup in module exit
- Ioctl number collision: `CDX_CTRL_DPA_CONNADD` and `CDX_CTRL_DPA_QOS_CONFIG_ADD` both use ioctl number 3 (NXP upstream issue, not blocking)
- Platform default is `LS1043A` in Makefile — functionally identical to LS1046A for DPAA1

## Kernel Compatibility

- **Built against:** 6.6.129-00002-g7dda8caa07ad (SDK kernel with NXP DPAA drivers)
- **Must match running kernel exactly** — module vermagic enforced