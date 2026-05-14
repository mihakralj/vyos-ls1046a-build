# Dev-Test Loop: Fast Iteration for VyOS LS1046A

> **Status:** ✅ WORKING (Cobalt 100 build host, verified 2026-05-14)
> **Goal:** Reduce dev-test cycle from ~60 to 90 min down to ~3 min (kernel) / ~30 s (DTB/config). Because waiting an hour to test a one-line config change is not engineering. It's penance.

## Network Topology

```
Cobalt 100 Azure ARM64 VM "arm64-runner" (Debian 12, native aarch64, 32 cores, 125 GB RAM)
  ├── /home/vyos/vyos-ls1046a-build/  ← this repo (build + edit here)
  ├── work/linux-6.18.28/             ← staged kernel tree
  ├── work/dev-tftp/                  ← local rsync staging area
  ├── native arm64 gcc + ccache
  ├── Azure NIC 10.0.0.4, Tailscale 100.125.95.22 (reaches LAN via subnet route)
  └── bin/dev-build.sh kernel|dtb|extract|iso-live → rsync → LXC 200

LXC 200 "vyos-builder" (Ubuntu 22.04, 192.168.1.137, on the LAN)
  ├── /srv/tftp/             → vmlinuz, mono-gw.dtb, initrd.img, filesystem.squashfs
  ├── tftpd-hpa on UDP/69    → serves vmlinuz/initrd/dtb to U-Boot
  └── python http.server :8080 → serves filesystem.squashfs to live-boot fetch=
     (NB: no toolchain, no kernel tree — pure serving relay)

Mono Gateway (LS1046A, 4× Cortex-A72, 8 GB DDR4)
  ├── fm1-mac5 (rightmost RJ45) → U-Boot TFTP, static IP 192.168.1.200
  ├── eth0 (left RJ45) MGMT 192.168.1.190 → SSH from the VM via Tailscale
  ├── eMMC: mmcblk0p3 = VyOS root
  └── U-Boot 2025.04: dev_boot → TFTP from 192.168.1.137 (LXC 200)
```

All development happens on the **Cobalt 100 ARM64 VM**, edited locally (VS Code Remote-SSH or local CLI). Build artefacts are rsync'd to LXC 200 over Tailscale; the board only ever talks to LXC 200 on the LAN. LXC 200 has been decommissioned as a build host — it serves files and nothing else.

Serial console to the Mono Gateway is via PuTTY/minicom (115200 8N1) from any machine with USB access, but most iteration cycles need only SSH to `vyos@192.168.1.190`.

## Verified Iteration Times

| Change Type | Before (CI+USB) | LXC 200 cross-build | **Cobalt 100 native** | Method |
|-------------|-----------------|---------------------|-----------------------|--------|
| Kernel config (`CONFIG_*`) | ~60 min | ~2 min  | **~30 s** | Native build + rsync → TFTP |
| Full kernel rebuild       | ~60 min | ~8 min  | **~2–3 min** | Native build + rsync → TFTP |
| DTS / DTB only            | ~60 min | ~30 s   | **~10 s** | `bin/ci-compile-mono-dtb.sh` → rsync |
| `config.boot.default`     | ~60 min | ~2 min  | **~2 min** | Edit + `add system image` |
| `vyos-1x` patch           | ~60 min | ~25 min | use CI    | `gh workflow run` (faster than local) |

## Quick Start

### 1. Prereqs on Cobalt 100 (already provisioned)

The Cobalt 100 VM ships with everything needed: `aarch64-linux-gnu-gcc`, native `gcc`, `make`, `ccache`, `dtc`, `xorriso`, `rsync`, `ssh`, `git`, `gh`, plus all VyOS-build apt deps. Workspace lives at `/home/vyos/vyos-ls1046a-build/`. `vyos-build/` is already checked out.

First-time SSH check to LXC 200:

```bash
ssh -i ~/.ssh/admin_key admin@192.168.1.137 'echo ok'
# admin@192.168.1.137 has passwordless sudo; rsync uses --rsync-path="sudo rsync"
```

### 2. Seed TFTP with initrd from last good ISO (one-time)

```bash
# On Cobalt 100, in the repo root:
bin/dev-build.sh iso-live                  # downloads newest GitHub release ISO,
                                           # extracts kernel/initrd/dtb/squashfs,
                                           # rsyncs to LXC 200:/srv/tftp/
```

### 3. Set up U-Boot dev_boot (one-time, from serial console)

Power on Mono Gateway, interrupt U-Boot (`Hit any key`), paste these lines:

```
setenv ethact fm1-mac5
setenv serverip 192.168.1.137
setenv ipaddr 192.168.1.200
setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin fsl_dpaa_fman.fsl_fm_max_frm=9600 hugepagesz=2M hugepages=512 panic=60 vyos-union=/boot/2026.03.25-0531-rolling"
setenv dev_boot 'tftp 0xa0000000 vmlinuz; tftp ${fdt_addr_r} mono-gw.dtb; tftp 0xb0000000 initrd.img; booti 0xa0000000 0xb0000000:${filesize} ${fdt_addr_r}'
saveenv
```

> **Note:** `ethact fm1-mac5` forces U-Boot to use the rightmost RJ45 port for TFTP.
> Without this, U-Boot may default to an SFP port (fm1-mac9) which cannot do TFTP
> with copper SFP-10G-T modules (no U-Boot RTL8261 driver).

> **Critical:** `vyos-union=/boot/<IMAGE>` must match the installed VyOS image on eMMC.
> Check with: `ls mmc 0:3 /boot/` in U-Boot or `show system image` in VyOS.
> Update after `add system image` with: `setenv bootargs "... vyos-union=/boot/NEW_IMAGE_NAME"` then `saveenv`.

> **Warning:** DTB must use `${fdt_addr_r}` (0x88000000), NOT `0x90000000`.
> `0x90000000` is `kernel_comp_addr_r` — the kernel decompression workspace.
> The kernel decompresses from `0xa0000000` to `0x0` using `0x90000000` as scratch,
> corrupting any DTB loaded there → `ERROR: Did not find a cmdline Flattened Device Tree`.

### 4. Dev iteration cycle (the fast path)

```bash
# On Cobalt 100, in the repo root:
bin/dev-build.sh kernel    # stage + native build + rsync to LXC 200 TFTP
# or:
bin/dev-build.sh dtb       # DTB only (~10 s)
```

```bash
# Trigger a reboot over SSH (no serial console needed for routine cycles):
ssh vyos sudo reboot
# U-Boot SPI env is pre-set to `run dev_boot` at power-on, so the board
# TFTPs the new vmlinuz/DTB/initrd from LXC 200 automatically.
# ~30–45 s wall-clock to login prompt on the new kernel.
```

## TFTP Live Boot (no USB, no eMMC required)

> **Status:** ✅ WORKING (verified 2026-04-18)
> **Use when:** iterating on full ISO changes (vyos-1x patches, package selection, initramfs, config defaults) without flashing USB or touching eMMC.

`dev_boot` above mounts the squashfs from eMMC (`vyos-union=/boot/<IMAGE>`) — so it still depends on an `install image` having happened once, and you cannot test changes to the squashfs itself. `dev_boot_live` fixes that: kernel+initrd come via TFTP, the squashfs streams over HTTP into tmpfs at initrd time. Exactly the same boot path as USB live boot, but over the network.

### 1. Deploy the live artifacts (from Cobalt 100)

```bash
cd /home/vyos/vyos-ls1046a-build
bin/dev-build.sh iso-live                          # auto-downloads the newest GitHub release
# or pass an explicit ISO path to use a local build:
bin/dev-build.sh iso-live /tmp/vyos-<version>-LS1046A-arm64.iso
```

With no argument, `iso-live` queries `gh release view --repo mihakralj/vyos-ls1046a-build` for the newest `*-LS1046A-arm64.iso` asset and downloads it to `/tmp/` on the Cobalt VM (skipping if the same file is already there — matched by name). This guarantees you are testing the exact ISO that was just published to GitHub.

It then extracts `live/filesystem.squashfs` (≈515 MB), `live/vmlinuz`, `live/initrd.img`, and `/mono-gw.dtb` from the ISO into `work/dev-tftp/` on the Cobalt VM, and rsyncs them to `admin@192.168.1.137:/srv/tftp/` over Tailscale (using `--rsync-path="sudo rsync"` because /srv/tftp is root-owned).

LXC 200 runs a Python `http.server` on port 8080 in the background, serving `/srv/tftp/filesystem.squashfs` over HTTP — live-boot's `fetch=` does not support TFTP. That HTTP server stays put across migrations; only the artefact source changes.

Verify:

```bash
curl -sI http://192.168.1.137:8080/filesystem.squashfs | head -3
# HTTP/1.0 200 OK
# Content-Length: 539877376
```

### 2. Set up `dev_boot_live` U-Boot env (one-time, from serial console)

The cmdline is a **byte-for-byte mirror** of the USB `boot.cmd` in the repo, with exactly two substitutions:

| USB (`boot.cmd`) | TFTP live (`dev_boot_live`) |
|------------------|-----------------------------|
| `usb start; fatload usb 0:2 …; usb stop` | `tftp …` (same file contents, same memory addresses) |
| *(squashfs found on the USB medium by live-boot)* | `fetch=http://192.168.1.137:8080/filesystem.squashfs` (squashfs comes from HTTP instead) |

Everything else — `rootdelay=10`, `usbcore.autosuspend=-1`, `components noeject nopersistence noautologin nonetworking union=overlay`, `fsl_dpaa_fman.fsl_fm_max_frm=9600` — is **identical** so the live-boot initramfs, vyos-router, and every service run the same code path as a real USB boot. This is the whole point: you test USB boot behaviour over TFTP.

```
setenv dev_boot_live 'tftp ${kernel_addr_r} vmlinuz; tftp ${fdt_addr_r} mono-gw.dtb; tftp ${ramdisk_addr_r} initrd.img; setenv bootargs console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live rootdelay=10 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 fsl_dpaa_fman.fsl_fm_max_frm=9600 usbcore.autosuspend=-1 fetch=http://192.168.1.137:8080/filesystem.squashfs; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'
saveenv
```

The three files pulled over TFTP (`vmlinuz`, `initrd.img`, `mono-gw.dtb`) and the squashfs pulled over HTTP are **the exact same bytes** that live on the ISO and on a `dd`'d USB stick — `bin/dev-build.sh iso-live` extracts them directly from `live/` inside the ISO via `xorriso` without modification. No synthesis, no repackaging.

> **Why HTTP not TFTP for the squashfs?** live-boot's `fetch=` supports only `http://`, `ftp://`, and `file:` — not TFTP. Also, TFTP is UDP block-by-block — 515 MB over 512-byte blocks would be painful. HTTP on GbE pulls the squashfs in ~5–10 s.
>
> **Why no `panic=60`?** `boot.cmd` does not set it and we want bit-identical behaviour. If your config.boot adds new MANAGED_PARAMS, add them to both `boot.cmd` and `dev_boot_live` together.

### 3. Boot

```
run dev_boot_live
```

Boot sequence (same code paths as USB, different medium):

1. U-Boot pulls `vmlinuz` (10 MB) + `mono-gw.dtb` (35 KB) + `initrd.img` (32 MB) over TFTP → ~3 s (USB `fatload` ≈ 2 s — comparable)
2. `booti` decompresses and jumps into the kernel — **same kernel binary as USB**
3. Kernel mounts `initrd.img`, runs live-boot init — **same initrd as USB**
4. live-boot sees `fetch=` instead of a block-device medium → pulls the 515 MB squashfs over HTTP into `/run/live/medium/` → ~8 s on GbE (USB reads it lazily from sda)
5. Overlay mounts over tmpfs — **same squashfs contents as USB**, same `/etc`, same vyos-router, same `config.boot.default`
6. systemd reaches `multi-user.target`, login prompt on ttyS0 — **same services, same timing**

Anything you would see on a real USB boot you will see here: vyos-router messages, migration scripts, DPAA1 probe order, PHY/SFP detection, fan thermal binding, systemd unit failures, everything. The only differences visible in the boot log are the early TFTP/HTTP fetch lines and the absence of `usb-storage`/`sda` probes.

Total: similar to USB live boot (~90 s including DPAA1 init), but every iteration is:

```bash
# After CI publishes a new release (watch it with `gh run watch`):
bin/dev-build.sh iso-live
# Power-cycle the board (or `ssh vyos sudo reboot`), interrupt U-Boot:
run dev_boot_live
```

No USB flashing, no `install image`, no `add system image`, no manually downloading the ISO. Pure network boot, fetching the latest release straight from GitHub.

### When to use which

| Scenario | Use |
|----------|-----|
| Kernel / DTB / kernel config change | `dev_boot` (squashfs unchanged, boots in ~26 s + kexec) |
| ISO content change (vyos-1x patch, package list, initramfs, config.boot.default) | `dev_boot_live` (always picks up latest squashfs) |
| Post-install behaviour / `install image` / eMMC boot path | USB stick + manual `install image` |

### Limitations

- The Mono Gateway must reach LXC 200 at 192.168.1.137:8080 on the rightmost RJ45 (fm1-mac5) before live-boot runs. If the network cable is unplugged, `fetch=` hangs in initramfs with `wget: download timed out`.
- tmpfs uses RAM. 515 MB squashfs + overlay scratch fits comfortably in 8 GB DDR4, but don't try `apt install` of gigabytes of packages — you'll run out of tmpfs.
- `nonetworking` is set (matches USB) so vyos-router will not configure interfaces. Remove `nonetworking` from the cmdline if you need networking to come up automatically.

## Boot Flow (TFTP dev)

```
U-Boot
  └── run dev_boot
        ├── tftp 0xa0000000 vmlinuz      (TFTP kernel from LXC 200 /srv/tftp/)
        ├── tftp ${fdt_addr_r} mono-gw.dtb  (TFTP DTB to 0x88000000)
        ├── tftp 0xb0000000 initrd.img   (TFTP initrd, loaded LAST for ${filesize})
        └── booti 0xa0000000 0xb0000000:${filesize} ${fdt_addr_r}
              │
              ├── [T+0 → T+26s] TFTP kernel 6.6.129 boots
              │   ├── eMMC probes (mmcblk0 p1 p2 p3) at T+1.8s
              │   ├── FMan MACs eth0-eth4 at T+1.5s
              │   ├── squashfs mounted via loop0 at T+7.8s
              │   ├── systemd multi-user at T+17s
              │   └── VyOS Router starts at T+26s
              │
              └── [T+26 → T+82s] Configuration + login prompt
                  ├── Full driver stack (modules available)
                  ├── "Configuration success"
                  └── VyOS login prompt
```

> **Note:** If bootargs are missing `hugepagesz=2M hugepages=512 panic=60`,
> `system_option.py` detects the mismatch with `config.boot.default` options
> and triggers a kexec reboot (adding ~70s). Always keep U-Boot bootargs in
> sync with any `MANAGED_PARAMS` in config.boot (hugepages, panic, mitigations,
> etc.). See [`system_option.py:generate_cmdline_for_kexec()`](https://github.com/vyos/vyos-1x/blob/current/src/conf_mode/system_option.py) for the full list.

## Build Modes

All modes run on **Cobalt 100** and rsync the result to LXC 200:/srv/tftp/.

| Command | What it does | Time (Cobalt 100 native) |
|---------|-------------|------|
| `bin/dev-build.sh kernel` | Stage + build kernel Image + DTB → push to TFTP | ~30 s incr / ~2–3 min full |
| `bin/dev-build.sh dtb` | Compile `board/dtb/mono-gw.dtb` only → push to TFTP | ~10 s |
| `bin/dev-build.sh extract [iso]` | Extract vmlinuz+initrd+DTB from ISO → push to TFTP | ~10 s |
| `bin/dev-build.sh iso-live [iso]` | Extract kernel+initrd+DTB+squashfs from ISO → push | ~20 s |
| `bin/dev-build.sh push` | Re-rsync `work/dev-tftp/` to LXC 200 (no rebuild) | ~5 s |

All modes accept `FLAVOR=default|ask|vpp` in the env. Full ISO builds remain on CI (`gh workflow run "VyOS LS1046A build (self-hosted)"`) — they only take ~7 min warm.

## Kernel Config: Key Lessons

### Fragment Merging (Critical)

VyOS kernel builds require merging **7 config fragments** from
`vyos-build/scripts/package-build/linux-kernel/config/*.config` on top of `vyos_defconfig`.
Without these fragments, SQUASHFS, OVERLAY_FS, FUSE_FS, and 200+ netfilter rules are missing.

```bash
# bin/dev-build.sh wires this automatically (via bin/ci-stage-kernel.sh +
# kernel/common/scripts/stage-kernel.sh):
cp vyos_defconfig .config
cat *.config >> .config          # Append all fragments
make olddefconfig                # Resolve conflicts
scripts/config --set-val X y     # Force LS1046A overrides
```

### `--set-val` vs `--enable` (Critical)

**`scripts/config --enable X` does NOT upgrade `=m` to `=y`.** This is the single most frustrating `scripts/config` behavior. Fragments set many configs to `=m` (module). For TFTP boot without modules, you MUST use `scripts/config --set-val X y` to force built-in.

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
| [`bin/dev-build.sh`](../bin/dev-build.sh) | Cobalt 100 dev loop: `kernel`, `dtb`, `extract`, `iso-live`, `push` modes |
| [`bin/local-build.sh`](../bin/local-build.sh) | Full ISO build orchestrator (mirrors CI) |
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

The local dev loop is a **parallel fast-iteration path**, not a replacement for CI. CI produces signed releases. The dev loop produces answers in two minutes.