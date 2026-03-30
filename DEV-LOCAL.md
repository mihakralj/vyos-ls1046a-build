# Local Dev Build — VyOS LS1046A (Mono Gateway DK)

Fast iteration loop for kernel, DTB, and VyOS package changes without waiting for GitHub CI.
Reduces a one-hour CI cycle to **~2 minutes** for incremental kernel changes.

> **Status:** ✅ WORKING (verified 2026-03-30)

---

## Network Topology

```
LXC 200 "vyos-builder" (Ubuntu 22.04, 192.168.1.137)
  ├── VS Code Remote / SSH    ← edit + build here
  ├── /srv/tftp/              ← vmlinuz · mono-gw.dtb · initrd.img (tftpd-hpa)
  ├── /opt/vyos-dev/          ← linux-6.6.y · vyos-build · vyos-ls1046a-build
  └── aarch64-linux-gnu-gcc 12+ (cross-toolchain)

Mono Gateway DK (LS1046A, 4× Cortex-A72, 8 GB DDR4)
  ├── RJ45 rightmost (fm1-mac5) ← U-Boot TFTP, static IP 192.168.1.200
  ├── eMMC mmcblk0p3            ← installed VyOS root (ext4)
  └── U-Boot 2025.04            ← dev_boot → TFTP from LXC 200
```

All development happens directly on **LXC 200**. SSH in or use VS Code Remote-SSH.
Serial console to the Mono Gateway is via PuTTY/minicom (115200 8N1) from any machine with USB access.

---

## Iteration Times

| Change | Method | Time |
|--------|--------|------|
| Kernel `CONFIG_*` | Incremental cross-compile → TFTP | **~2 min** |
| Full kernel rebuild | From-scratch cross-compile → TFTP | **~8 min** |
| DTS / DTB only | `dtc` compile → TFTP | **~30 sec** |
| `config.boot.default` | SSH into device, edit in place | **~1 min** |
| `vyos-1x` patch | Docker binfmt build | **~20 min** |

---

## Step 1 — One-time LXC setup

On the Proxmox host, create and provision LXC 200:

```bash
# Create LXC 200 (Ubuntu 22.04, 12 cores, 16 GB RAM, 80 GB disk)
sudo pct create 200 local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst \
  --hostname vyos-builder --cores 12 --memory 16384 --rootfs local-lvm:80 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.1.137/24,gw=192.168.1.1 \
  --unprivileged 0 --features nesting=1
sudo pct start 200

# Install toolchain and TFTP inside LXC 200
sudo pct exec 200 -- bash -c '
  apt-get update -qq
  apt-get install -y \
    git gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu \
    make bc libssl-dev flex bison libelf-dev \
    wget curl p7zip-full device-tree-compiler \
    tftpd-hpa docker.io
  # Start TFTP server
  mkdir -p /srv/tftp
  chmod 777 /srv/tftp
  sed -i "s|TFTP_DIRECTORY=.*|TFTP_DIRECTORY=/srv/tftp|" /etc/default/tftpd-hpa
  systemctl enable --now tftpd-hpa
  # Docker for binfmt builds
  systemctl enable --now docker
  mkdir -p /opt/vyos-dev
'
```

> **binfmt (for `vyos1x` mode only):** Install QEMU user-static on the **Proxmox host**, not inside LXC:
> ```bash
> apt-get install -y qemu-user-static
> ```

---

## Step 2 — Seed TFTP with initrd from a release ISO (one-time)

The initrd is large (~30 MB) and changes rarely. Extract it once from any recent release, then only replace `vmlinuz` + `mono-gw.dtb` during kernel iterations.

```bash
# On LXC 200:
cd /opt/vyos-dev
LATEST=$(curl -s https://api.github.com/repos/mihakralj/vyos-ls1046a-build/releases/latest \
         | grep -oP '"tag_name": "\K[^"]+')
wget "https://github.com/mihakralj/vyos-ls1046a-build/releases/download/${LATEST}/vyos-${LATEST}-LS1046A-arm64.iso"

# Extract kernel, initrd, and DTB into TFTP root
./build-local.sh extract vyos-*.iso
```

After this, `/srv/tftp/` should contain:

```
vmlinuz      (~27 MB)
initrd.img   (~30 MB)
mono-gw.dtb  (~94 KB)
```

> **Re-extract initrd** any time you rebuild `vyos-1x` or change the squashfs.
> For kernel-only changes, re-extract is **not needed** — `vmlinuz` and `mono-gw.dtb`
> are the only files that change.

---

## Step 3 — Set up U-Boot `dev_boot` (one-time per board)

Connect to the Mono Gateway serial console (115200 8N1). Power-cycle the board and press any key during U-Boot countdown to stop auto-boot.

### Check the installed VyOS image name first

```
# In U-Boot:
ext4ls mmc 0:3 /boot
```

Note the image directory name (e.g., `2026.03.25-0531-rolling`). You need it for `vyos-union=`.

### Set TFTP boot variables

Paste these lines at the U-Boot prompt. Each `setenv` line is kept under 500 chars to fit `CONFIG_SYS_CBSIZE`:

```
setenv ethact fm1-mac5
setenv serverip 192.168.1.137
setenv ipaddr 192.168.1.200
setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin fsl_dpaa_fman.fsl_fm_max_frm=9600 hugepagesz=2M hugepages=512 panic=60 vyos-union=/boot/2026.03.25-0531-rolling"
setenv dev_boot 'tftp 0xa0000000 vmlinuz; tftp ${fdt_addr_r} mono-gw.dtb; tftp 0xb0000000 initrd.img; booti 0xa0000000 0xb0000000:${filesize} ${fdt_addr_r}'
saveenv
```

> **`ethact fm1-mac5`** forces U-Boot to use the rightmost RJ45 port for TFTP.
> Without this, TFTP may default to an SFP port (`fm1-mac9`) which has no U-Boot
> driver for copper 10G SFP modules.

> **`vyos-union=/boot/<IMAGE>`** must match the installed image on eMMC.
> Update it after every `add system image` with:
> ```
> setenv bootargs "... vyos-union=/boot/NEW_IMAGE_NAME_HERE"
> saveenv
> ```
> Or check the current value: `printenv bootargs`

> **Never use `0x90000000` for DTB.** That address is `kernel_comp_addr_r` — the kernel
> decompression scratch space. The kernel decompresses from `0xa0000000` → `0x0` using
> `0x90000000` as scratch, overwriting any DTB there.
> Always use `${fdt_addr_r}` = `0x88000000`.

---

## Step 4 — The fast iteration cycle

### 4a. Edit code on LXC 200

SSH into LXC 200 (or use VS Code Remote-SSH) and make your kernel config or DTS changes directly:

```bash
ssh root@192.168.1.137
cd /opt/vyos-dev/vyos-ls1046a-build
# edit files...
```

### 4b. Build kernel

```bash
cd /opt/vyos-dev && ./build-local.sh kernel
```

### 4c. Boot the device via TFTP

From the serial console (115200 8N1), power-cycle the Mono Gateway. Stop U-Boot, then:

```
run dev_boot
```

Or set `bootcmd=run dev_boot` temporarily to skip the interrupt step during heavy iteration:

```
setenv bootcmd 'run dev_boot'
# (Don't saveenv — this change is volatile, resets on power cycle if not saved)
```

**Expected timing:**
- ~3s — U-Boot TFTP transfers complete
- ~26s — VyOS live-boot reaches multi-user
- ~82s — VyOS login prompt (if no kexec double-boot triggered)

---

## `build-local.sh` Reference

Run from inside LXC 200 at `/opt/vyos-dev/`:

```bash
./build-local.sh <mode> [args]
```

| Mode | What it does | Typical time |
|------|-------------|-------------|
| `kernel` | Clone/update repos → apply patches → configure → build Image + DTB → copy to `/srv/tftp/` | 2 min (incr) / 8 min (full) |
| `dtb` | Recompile DTB only → copy to `/srv/tftp/` | ~30 sec |
| `extract [iso]` | Extract vmlinuz+initrd+DTB from ISO → `/srv/tftp/` | ~30 sec |
| `vyos1x` | Rebuild `vyos-1x` .deb via Docker binfmt | ~20 min |
| `iso` | Full unsigned ISO via Docker | ~25 min |

### Paths managed

```
/opt/vyos-dev/
  linux-6.6.y/           ← kernel source (auto-cloned from vyos/vyos-linux-kernel)
  vyos-build/            ← config fragments + build.py (auto-cloned from vyos/vyos-build)
  vyos-ls1046a-build/    ← this repo (auto-cloned/updated for patches + DTS)
  build-local.sh         ← this script

/srv/tftp/
  vmlinuz                ← ARM64 kernel Image (raw, not uImage)
  mono-gw.dtb            ← compiled DTB for Mono Gateway
  initrd.img             ← VyOS initramfs (extracted from ISO, rarely changes)
```

---

## Kernel Config Changes

### How config is assembled

The script merges configs in this order:

```
1. vyos_defconfig                    ← base (from vyos-build repo)
2. config/*.config (7 fragments)     ← VyOS additions (squashfs, netfilter, etc.)
3. make olddefconfig                 ← resolve conflicts
4. scripts/config --set-val X y     ← LS1046A overrides (force =y)
5. make olddefconfig                 ← final resolve
```

### Why `--set-val` and not `--enable`

VyOS fragments set many drivers to `=m`. **`scripts/config --enable X` will NOT upgrade `=m` to `=y`.**
You must use `scripts/config --set-val X y` to force built-in.

This is critical for TFTP boot: there is no module loader in the initramfs, so any driver needed at boot must be `=y`.

### Drivers that MUST be `=y`

| Subsystem | Config symbols | Why |
|-----------|---------------|-----|
| DPAA1 networking | `FSL_FMAN`, `FSL_DPAA`, `FSL_DPAA_ETH`, `FSL_BMAN`, `FSL_QMAN`, `FSL_PAMU` | FMan needs early init before rootfs |
| DPAA1 MDIO | `FSL_XGMAC_MDIO` | Without it, all MACs defer with "missing pcs" → no interfaces |
| CPU frequency | `QORIQ_CPUFREQ` | Module loads after `clk_disable_unused` → CPU stuck at 700 MHz |
| eMMC | `MMC_SDHCI_OF_ESDHC` | Required to mount eMMC squashfs as live root |
| Filesystems | `SQUASHFS`, `OVERLAY_FS`, `EXT4_FS` | Live-boot overlay stack |
| Block | `BLK_DEV_LOOP`, `BLK_DEV_DM` | Loop device for squashfs |

### Adding a new `CONFIG_*`

Add `scripts/config --set-val CONFIG_FOO y` to the `prepare_config()` function in `build-local.sh`.
Then add the same line to the `printf` block in `.github/workflows/auto-build.yml` to ensure CI matches.

---

## DTB-Only Changes

If you only changed `data/dtb/mono-gateway-dk.dts`:

```bash
# On LXC 200:
cd /opt/vyos-dev && ./build-local.sh dtb
```

```
# On Mono Gateway serial (U-Boot):
run dev_boot
```

Total cycle: ~35 seconds.

> **If DTS compilation fails:** The script automatically falls back to the pre-built
> `data/dtb/mono-gw.dtb`. Check the DTS for incompatible thermal-zone paths
> (must match `fsl-ls1046a.dtsi` in kernel 6.6).

---

## Kernel Patches

The script applies two patches and one source file copy on top of the VyOS kernel tree:

| File | Purpose |
|------|---------|
| `data/kernel-patches/4002-hwmon-ina2xx-add-INA234-support.patch` | INA234 power sensors (I2C, 8 sensors on board) |
| `data/kernel-patches/9001-usdpaa-bman-qman-exports-and-driver.patch` | BMan/QMan symbol exports + `CONFIG_FSL_USDPAA_MAINLINE` Kconfig/Makefile |
| `data/kernel-patches/fsl_usdpaa_mainline.c` | `/dev/fsl-usdpaa` chardev driver (1453 lines, NXP ABI-compatible) |

Patches are idempotent — the script checks if each is already applied before patching.
The `.c` file copy is skipped if the destination already matches.

---

## vyos-1x Changes

When you modify a `data/vyos-1x-*.patch` file:

```bash
# On LXC 200:
cd /opt/vyos-dev && ./build-local.sh vyos1x
```

This produces `vyos-1x_<version>_arm64.deb`. To test on the live device without a full ISO:

```bash
# Copy .deb to device
scp vyos-1x_*.deb vyos@192.168.1.200:/tmp/

# On device (as root):
dpkg -i /tmp/vyos-1x_*.deb
systemctl restart vyos-configd
find / -name __pycache__ -path '*/vyos/*' -exec rm -rf {} + 2>/dev/null
```

> **Docker binfmt note:** `qemu-user-static` must be installed on the **Proxmox host**,
> not inside the LXC. Unprivileged LXC containers cannot register binfmt interpreters.

---

## U-Boot Memory Map (Reference)

| U-Boot variable | Address | Contents |
|-----------------|---------|----------|
| `kernel_addr_r` | `0x82000000` | Not used for dev_boot |
| `fdt_addr_r` | `0x88000000` | DTB (`mono-gw.dtb`) |
| `ramdisk_addr_r` | `0x88080000` | Not used for dev_boot |
| `load_addr` | `0xa0000000` | Kernel Image (`vmlinuz`) for dev_boot |
| `kernel_comp_addr_r` | `0x90000000` | ⚠️ Kernel decompression scratch — NEVER use for DTB |

`dev_boot` uses custom addresses:
- Kernel → `0xa0000000`
- DTB    → `${fdt_addr_r}` = `0x88000000`
- Initrd → `0xb0000000` (explicit, avoids overlap at 512KB past FDT)

---

## Boot Flow (TFTP dev_boot)

```
U-Boot: run dev_boot
  │
  ├─ tftp 0xa0000000 vmlinuz        ← 27 MB kernel (from LXC 200 TFTP)
  ├─ tftp 0x88000000 mono-gw.dtb    ← 94 KB DTB
  ├─ tftp 0xb0000000 initrd.img     ← 30 MB initrd (LAST — captures ${filesize})
  └─ booti 0xa0000000 0xb0000000:${filesize} 0x88000000
       │
       ▼ ~3s
  Kernel 6.6.x boots (earlycon on ttyS0)
  eMMC probes → mmcblk0p3 found
  live-boot initramfs:
    ├─ boot=live → activates live-boot
    └─ vyos-union=/boot/<IMAGE> → overlays squashfs from eMMC p3
       │
       ▼ ~26s
  systemd multi-user.target
  VyOS configuration loads
       │
       ▼ ~82s
  VyOS login prompt
```

> **kexec double-boot:** If `panic=60` in bootargs does not match `config.boot.default`,
> `system_option.py` triggers a kexec reboot (~70s penalty). Ensure `hugepagesz=2M hugepages=512 panic=60`
> is in the `dev_boot` bootargs. Hugepages are required if VPP is configured in `config.boot.default`.

---

## Bootargs Checklist

Every `dev_boot` session must include all of these:

| Parameter | Value | Why |
|-----------|-------|-----|
| `console=` | `ttyS0,115200` | Serial console (8250 UART) |
| `earlycon=` | `uart8250,mmio,0x21c0500` | Pre-initramfs serial output |
| `boot=live` | — | Activates live-boot initramfs scripts (mandatory) |
| `rootdelay=5` | — | Wait for eMMC enumeration |
| `noautologin` | — | Don't auto-login on serial |
| `net.ifnames=0` | — | Predictable eth0/eth1/… names |
| `fsl_dpaa_fman.fsl_fm_max_frm=9600` | — | Jumbo frames. Module name must be exact — wrong name = no effect |
| `panic=60` | — | Matches `config.boot.default`; prevents kexec double-boot |
| `hugepagesz=2M hugepages=512` | — | Required if VPP configured; prevents kexec double-boot |
| `vyos-union=/boot/<IMAGE>` | Match eMMC image name | Points to squashfs overlay on eMMC p3 |

> **Missing `boot=live` or `vyos-union=`** drops to initramfs BusyBox shell with no error message.

---

## Updating `vyos-union` After `add system image`

The `vyos-union=` parameter must always match the **currently active** VyOS image on eMMC. After upgrading:

```
# In VyOS:
show system image
# Note the new image name

# In U-Boot (power-cycle, stop boot):
setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin fsl_dpaa_fman.fsl_fm_max_frm=9600 hugepagesz=2M hugepages=512 panic=60 vyos-union=/boot/NEW_IMAGE_NAME"
saveenv
```

---

## Troubleshooting

### TFTP transfer fails / timeout

```bash
# Check LXC 200 is reachable from U-Boot:
ping 192.168.1.137

# Check tftpd-hpa on LXC 200:
systemctl status tftpd-hpa && ls -lh /srv/tftp/

# Ensure ethact is set to the correct port:
printenv ethact
# Must be fm1-mac5 (rightmost RJ45). SFP ports have no copper U-Boot driver.
```

### "Wrong Ramdisk Image Format"

Initrd was not loaded last. `${filesize}` captured the DTB size instead of the initrd size.
Ensure `dev_boot` loads `initrd.img` as the **last** `tftp` command.

### "ERROR: Did not find a cmdline Flattened Device Tree"

DTB was likely loaded at `0x90000000` (`kernel_comp_addr_r`). The kernel decompresses from
`0xa0000000` → `0x0` using `0x90000000` as scratch, destroying the DTB.
Verify: `tftp ${fdt_addr_r} mono-gw.dtb` (uses `0x88000000`).

### VyOS drops to initramfs BusyBox

Missing or incorrect bootarg. Check:
1. `boot=live` is present
2. `vyos-union=/boot/<IMAGE>` matches actual image directory on eMMC (`ext4ls mmc 0:3 /boot`)
3. eMMC is accessible: `ext4ls mmc 0:3 /boot`

### kexec double-boot (login prompt takes ~150s instead of 82s)

`system_option.py` sees a mismatch between `/proc/cmdline` and `config.boot` managed params.
- Verify `panic=60` is in bootargs
- If VPP is in `config.boot.default`: add `hugepagesz=2M hugepages=512` to bootargs
- Run `dmesg | grep kexec` to confirm

### CPU runs at 700 MHz instead of 1800 MHz

`CONFIG_QORIQ_CPUFREQ=m` — the module loads after `clk_disable_unused` (T+12s) releases the PLL.
The built-in driver claims PLLs first. Check: `grep QORIQ_CPUFREQ /boot/config-$(uname -r)`.
Fix: ensure `scripts/config --set-val QORIQ_CPUFREQ y` is in `prepare_config()`.

### No network interfaces after boot

One or more DPAA1 drivers built as `=m`. Check `dmesg | grep fman`.
All of `FSL_FMAN`, `FSL_DPAA`, `FSL_DPAA_ETH`, `FSL_BMAN`, `FSL_QMAN`, `FSL_PAMU` must be `=y`.

### eth2 (center RJ45) never gets link

`CONFIG_MAXLINEAR_GPHY` is missing or `=m`. The GPY115C PHY (ID `0x67C9DF10`) requires
the `mxl-gpy.c` driver for SGMII auto-negotiation. Generic PHY fails silently.

---

## Relationship to CI

The local dev loop is a **parallel fast-iteration path**, not a replacement for GitHub CI.

| | Local dev loop | GitHub CI |
|--|----------------|-----------|
| Speed | ~2 min (kernel) | ~60 min |
| Signed | ❌ unsigned | ✅ MOK.key + minisign |
| Releases | ❌ no | ✅ GitHub Releases |
| Use for | Testing config changes | Production releases |

When a change is verified locally, commit and push. CI produces the signed, released ISO.

```bash
# Trigger a CI build manually after verifying locally:
gh workflow run "VyOS LS1046A build" --ref main
```

---

## Files

| File | Purpose |
|------|---------|
| `bin/build-local.sh` | Build script — kernel, dtb, extract, vyos1x, iso modes |
| `DEV-LOCAL.md` | This document |
| `plans/DEV-LOOP.md` | Architecture notes and verified timing results |
| `data/dtb/mono-gateway-dk.dts` | Custom DTS (SFP nodes, thermal, ethernet aliases) |
| `data/dtb/mono-gw.dtb` | Pre-built DTB fallback (used if DTS compilation fails) |
| `data/kernel-patches/` | LS1046A-specific kernel patches |
| `data/config.boot.default` | Default VyOS config baked into ISO |
| `.github/workflows/auto-build.yml` | CI workflow — kernel config is the source of truth |