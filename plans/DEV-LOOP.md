# Dev-Test Loop — Fast Iteration for VyOS LS1046A

> **Status:** ✅ WORKING (verified 2026-03-22, build #7)
> **Goal:** Reduce dev-test cycle from ~60–90 min to ~10 min (kernel) / ~2 min (DTB/config).

## Network Topology

```
helga (Windows workstation)
  ├── VS Code + git (editing build-scripts)
  ├── Serial USB → Mono Gateway (PuTTY 115200 8N1, U-Boot + VyOS console)
  └── SSH → heidi (admin@192.168.1.15)

heidi (Proxmox AMD64 — Ryzen 7 5700X, 16T, 128GB RAM, 192.168.1.15)
  └── LXC 200 "vyos-builder" (192.168.1.137, 12 cores, 16GB RAM, 80GB disk)
        ├── /srv/tftp/         → vmlinuz (27MB), mono-gw.dtb (92KB), initrd.img (32MB)
        ├── /opt/vyos-dev/     → linux-6.6.y source, vyos-build, build-scripts
        ├── aarch64-linux-gnu-gcc 12.2.0 (cross-toolchain)
        └── tftpd-hpa on port 69

Mono Gateway (LS1046A, 4× Cortex-A72, 8GB DDR4)
  ├── fm1-mac5 (rightmost RJ45) → U-Boot TFTP, static IP 192.168.1.200
  ├── eMMC: mmcblk0p3 = VyOS root (image: 2026.03.22-0432-rolling)
  └── U-Boot 2025.04: dev_boot → TFTP from LXC 200
```

## Verified Iteration Times

| Change Type | Before (CI+USB) | After (local) | Method |
|-------------|-----------------|---------------|--------|
| Kernel config (`CONFIG_*`) | ~60 min | **~2 min** (incremental) | Cross-compile on heidi → TFTP boot |
| Full kernel rebuild | ~60 min | **~8 min** | From-scratch cross-compile |
| DTS / DTB only | ~60 min | **~30 sec** | `dtc` compile → TFTP |
| `config.boot.default` | ~60 min | **~2 min** | Edit on eMMC via SSH |
| `vyos-1x` patch | ~60 min | **~25 min** | Docker binfmt build |

## Quick Start

### 1. Provision heidi (one-time)

From helga PowerShell:

```powershell
scp bin/setup-heidi.sh admin@heidi:/tmp/
ssh admin@heidi "sudo bash /tmp/setup-heidi.sh"
```

Creates **LXC 200** with cross-toolchain, TFTP, Docker, kernel source, and vyos-build.

### 2. Seed TFTP with initrd from last good ISO (one-time)

```bash
# SSH into LXC 200
ssh -J admin@192.168.1.15 root@192.168.1.137

cd /opt/vyos-dev
wget https://github.com/mihakralj/vyos-ls1046a-build/releases/latest/download/vyos-2026.03.22-0432-rolling-LS1046A-arm64.iso
./build-local.sh extract *.iso
```

### 3. Set up U-Boot dev_boot (one-time, from helga serial console)

Power on Mono Gateway, interrupt U-Boot (`Hit any key`), paste these lines:

```
setenv ethact fm1-mac5
setenv serverip 192.168.1.137
setenv ipaddr 192.168.1.200
setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin vyos-union=/boot/2026.03.22-0432-rolling"
setenv dev_boot 'tftp 0xa0000000 vmlinuz; tftp 0x90000000 mono-gw.dtb; tftp 0xb0000000 initrd.img; booti 0xa0000000 0xb0000000:${filesize} 0x90000000'
saveenv
```

> **Note:** `ethact fm1-mac5` forces U-Boot to use the leftmost RJ45 port for TFTP.
> Without this, U-Boot may default to an SFP port (fm1-mac9) which cannot do TFTP
> with copper SFP-10G-T modules (no U-Boot RTL8261 driver).

> **Critical:** `vyos-union=/boot/<IMAGE>` must match the installed VyOS image on eMMC.
> Check with: `ls mmc 0:3 /boot/` in U-Boot or `show system image` in VyOS.
> Update after `add system image` with: `setenv bootargs "... vyos-union=/boot/NEW_IMAGE_NAME"` then `saveenv`.

### 4. Dev iteration cycle (the fast path)

```powershell
# From helga: edit build-local.sh in VS Code, then deploy + build:
scp bin/build-local.sh admin@heidi:/tmp/ ; ssh admin@heidi "sudo pct push 200 /tmp/build-local.sh /opt/vyos-dev/build-local.sh && sudo pct exec 200 -- chmod +x /opt/vyos-dev/build-local.sh && sudo pct exec 200 -- bash -c 'cd /opt/vyos-dev && ./build-local.sh kernel 2>&1'"
```

```
# From helga serial console (PuTTY 115200 8N1):
# Power-cycle Mono Gateway or type 'reboot' in VyOS
# Interrupt U-Boot → type:
run dev_boot
# Wait ~26s for first VyOS boot → kexec → ~82s for final login prompt
```

## Boot Flow (TFTP dev)

```
U-Boot
  └── run dev_boot
        ├── tftp 0xa0000000 vmlinuz      (TFTP kernel from LXC 200)
        ├── tftp 0x90000000 mono-gw.dtb  (TFTP DTB)
        ├── tftp 0xb0000000 initrd.img   (TFTP initrd, loaded LAST for ${filesize})
        └── booti 0xa0000000 0xb0000000:${filesize} 0x90000000
              │
              ├── [T+0 → T+26s] TFTP kernel 6.6.129 boots
              │   ├── eMMC probes (mmcblk0 p1 p2 p3) at T+1.8s
              │   ├── FMan MACs eth0-eth4 at T+1.5s
              │   ├── squashfs mounted via loop0 at T+7.8s
              │   ├── systemd multi-user at T+17s
              │   └── VyOS Router starts at T+26s
              │
              ├── [T+26 → T+121s] live-boot kexec double-boot
              │   └── vyos-router reaches kexec.target → reboots
              │
              └── [T+121 → T+200s] eMMC production kernel 6.6.128-vyos
                  ├── Full driver stack (modules available)
                  ├── "Configuration success"
                  └── VyOS login prompt
```

> **Note:** The kexec double-boot is normal for `boot=live`. The TFTP kernel runs
> for ~120s (enough to verify all hardware probes), then kexec hands off to the
> eMMC production kernel which completes config migration. For kernel config
> testing, the first boot's dmesg is what matters.

## Build Modes

| Command | What it does | Time |
|---------|-------------|------|
| `build-local.sh kernel` | Cross-compile kernel + DTB → `/srv/tftp/` | ~2 min (incr) / ~8 min (full) |
| `build-local.sh dtb` | Compile DTB only → `/srv/tftp/` | ~5 sec |
| `build-local.sh extract [iso]` | Extract vmlinuz+initrd+DTB from ISO → TFTP | ~30 sec |
| `build-local.sh vyos1x` | Rebuild vyos-1x .deb via Docker binfmt | ~20 min |
| `build-local.sh iso` | Full ISO build (placeholder — use CI for now) | ~25 min |

## Kernel Config: Key Lessons

### Fragment Merging (Critical)

VyOS kernel builds require merging **7 config fragments** from
`vyos-build/scripts/package-build/linux-kernel/config/*.config` on top of `vyos_defconfig`.
Without these fragments, SQUASHFS, OVERLAY_FS, FUSE_FS, and 200+ netfilter rules are missing.

```bash
# build-local.sh does this automatically:
cp vyos_defconfig .config
cat *.config >> .config          # Append all fragments
make olddefconfig                # Resolve conflicts
scripts/config --set-val X y     # Force LS1046A overrides
```

### `--set-val` vs `--enable` (Critical)

**`scripts/config --enable X` does NOT upgrade `=m` to `=y`.**
Fragments set many configs to `=m` (module). For TFTP boot without modules,
you MUST use `scripts/config --set-val X y` to force built-in.

Subsystems that MUST be `=y` for TFTP boot:

| Category | Configs |
|----------|---------|
| Filesystems | SQUASHFS, SQUASHFS_XZ, OVERLAY_FS, EXT4_FS, FUSE_FS, JBD2 |
| Block | BLK_DEV_LOOP, BLK_DEV_DM |
| eMMC | MMC, MMC_BLOCK, MMC_SDHCI, MMC_SDHCI_PLTFM, MMC_SDHCI_OF_ESDHC |
| DPAA1 | FSL_FMAN, FSL_DPAA, FSL_DPAA_ETH, FSL_BMAN, FSL_QMAN, FSL_PAMU |
| Netfilter | NF_CONNTRACK, NF_TABLES, NFT_CT, NFT_NAT, NFT_MASQ + 25 more |

### VyOS Boot Arguments

VyOS uses `boot=live` even on installed eMMC systems. The required bootargs:

```
console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0
boot=live rootdelay=5 noautologin vyos-union=/boot/<IMAGE_NAME>
```

- `boot=live` — triggers live-boot initramfs scripts (NOT optional)
- `vyos-union=/boot/<IMAGE>` — points to squashfs on eMMC partition 3
- `rootdelay=5` — wait for eMMC to enumerate before mounting
- `noautologin` — don't auto-login on serial console

## Expected Boot Messages (Ignore These)

| Message | Meaning |
|---------|---------|
| `nfct v1.4.7: netlink error: Invalid argument` | Conntrack helper setup — cosmetic, first boot only |
| `could not generate DUID ... failed!` | No stable machine-id on live boot |
| `PCIe: no link / disabled` | No PCIe devices on board |
| `WARNING failed to get smmu node` | DTB lacks SMMU nodes |
| `binfmt_misc.mount` FAILED | No binfmt support needed on target |
| `mount: /live/persistence/ failed` | Non-persistence partitions probed and rejected |
| `sfp-xfi0: deferred probe pending` | SFP ports wait for PHY initialization |
| `can't get pinctrl, bus recovery not supported` | I2C pinctrl not in DTB — harmless |

## Files

| File | Purpose |
|------|---------|
| [`bin/setup-heidi.sh`](../bin/setup-heidi.sh) | One-time: creates LXC 200 on Proxmox, installs everything |
| [`bin/build-local.sh`](../bin/build-local.sh) | Fast build: `kernel`, `dtb`, `extract`, `vyos1x`, `iso` modes |
| [`plans/DEV-LOOP.md`](DEV-LOOP.md) | This document |

## Constraints Preserved

- **DPAA1 `=y`:** All 5 DPAA1 layers forced built-in (never `=m`)
- **`booti` only:** Same `booti` command, initrd loaded last for `${filesize}`
- **`boot=live` + `vyos-union=`:** Required in bootargs for VyOS squashfs overlay
- **`auto-build.yml` unchanged:** GitHub CI remains the signed release pipeline
- **Static IP for U-Boot:** `ipaddr=192.168.1.200`, `serverip=192.168.1.137` (no DHCP in U-Boot TFTP)

## GitHub Actions: Still Used For

- Production releases (signed ISO + minisig)
- Weekly automated builds (cron Friday 01:00 UTC)
- Changelog generation from upstream vyos-1x / vyos-build

The local dev loop is a **parallel fast-iteration path**, not a replacement for CI.
