# AGENTS.md (C2-compressed)

Agent guidance for this repo. Legend: → leads-to; ∵ because; ¬ not; w/ w/o with/without. Normative caps (MUST/NEVER) are binding. Code blocks, commands, paths, values verbatim.

## S1. Agent Memory (qdrant) — every task

qdrant MCP = authoritative persistent memory (diagnoses, root causes, failed attempts, gotchas).

1. Before non-trivial work: `qdrant-find`, several focused queries (symptoms, components, subsystems, file/patch names) BEFORE mass file reads. Hits = authoritative.
2. After new insight: `qdrant-store`, dense prose (semantic search; ¬bullet lists). Include symptom, root cause, fix (paths + patch numbers), verification, date, tags.
3. Prefer store over expanding this file. AGENTS.md = stable structural rules only.
4. Conflict: trust newer source (`metadata.date`).
5. Continuing a topic: `qdrant-find` first; prior insight may already solve it.
6. Before committing anything touching kernel/FMan PCD/FE-VM/ehash/KeyGen/AC_CC: `qdrant-find` components + approach; validate vs NXP docs, prior findings, settled topology. Qdrant wins unless new HW evidence.
7. Validate every step: after each diagnostic read/probe/result, `qdrant-find` cross-check. Do NOT fix w/o Qdrant agreement (∵ buffer layouts, register offsets, field names, silicon behavior wrong multiple times; Qdrant holds corrections).
8. Three refs before any FMan/PCD/KeyGen/ASK change (kernel patch, ci-setup-kernel.sh fixup, board script touching KeyGen/CC/FE-VM/ehash/AC_CC/BMI/MURAM/params page):
   - Qdrant NXP silicon docs: queries scoped to register/component/SDK fn (`FmPortSetFESupport`, `AllocFEObjs`, `FmPcdCcBuildFE`, `fmkg_pe_sp`). ASK 1.x SDK, esp. `999-layerscape-ask-kernel` patch = authoritative for encodings, layouts, init/teardown order.
   - `nxp-sdk` branch (`.kilo/worktrees/nxp-sdk/`): ported / attempted-reverted / never-touched status per SDK fn in `arch/fman-microcode-210-programming-reference.md`.
   - Live ASK on `.106` (`ssh root@192.168.1.106`): CDX/FCI/CMM/dpa_app production stack. Check `/proc/fqid_stats/pcd/`, `cat /sys/kernel/debug/fman/...`, `ask-check`.
   - Precedence: `.106` live > qdrant SDK docs > nxp-sdk branch. All agree = safe. Any disagree = halt, resolve first.

## S2. Documentation Style

- Mermaid, ¬ASCII art, for all .md diagrams (```` ```mermaid ```` fences).
- HADS 1.0.0 for all `specs/`, `plans/`, `arch/`, code-documenting `README.md`: `**Version … · HADS 1.0.0**` header, `## AI READING INSTRUCTION` block, numbered `##` sections, paragraphs tagged `**[SPEC]**` (facts/reqs/contracts), `**[NOTE]**` (rationale/history), `**[BUG] Title**` (symptom+cause+fix, all 3), `**[?]**` (unverified). Use `hads-convert` skill. Convert IN PLACE (precedent: `plans/UBOOT.md`, `plans/VPP.md`), on-touch w/ normal edits, ¬bulk pass.
- Data-loss guard: narrative → `[NOTE]` intact; only verifiable facts → `[SPEC]`. NEVER condense correctness-critical prose (register layouts, MURAM offsets, reversibility contract, risk registers, M3-3b/FE-VM findings). Preserve every command, address, register value, patch number, date, qdrant anchor verbatim. Dropped fact = regression.
- Exceptions (stay prose): `README.md`, `INSTALL.md` (human entry, Bryson voice), `vyos_sshkey.md` (credential), `plans/archive/**` (frozen; convert only if un-archived), `AGENTS.md`.

## S3. ASK2 (rewrite-in-progress) — single-image runtime offload

Legacy ASK 1.x deleted on `ask20` branch: vendored SDK FMan/QMan/BMan overlay; `cdx.ko`/`auto_bridge.ko`/`cmm`/`dpa_app`/`libfci`; 5797-line in-tree-hooks patch; `/* ASK-edit (askN, …) */` markers; `data/ask-userspace/`; `ci-build-fmc.sh`/`ci-build-fmlib.sh`/`ci-setup-kernel-ask.sh`/`ci-consume-ask-kernel.sh`; `kernel/flavors/ask/{sdk-sources,oot-modules,patches,userspace-patches}`; `mono-gateway-dk-sdk.dts`; SDK portal DTSIs; `ASK_KERNEL_TAG` input. Refs: `plans/ASK2-MASTER-PLAN.md` (THE authoritative execution plan: M2–M8 gates, live TODO; supersedes all archived ASK2 plans), `specs/ask2-rewrite-spec.md` (v1.10 architecture index), `plans/DUAL-DATAPLANE.md` (single image; boot = mainline/RSS S0; ASK engages **per interface** on `set interfaces ethernet eth<n> offload ask`; VPP = AF_XDP overlay on S0; ASK↔VPP **per-interface** mutex — one port can't be both, other ports free; `set system offload classify` CLI deprecated — mechanism kept, RSS+parser silent defaults, ASK sole offload switch).

Until ASK2 v1.3 lands (`ask.ko` ~2800 LOC at `drivers/net/ethernet/freescale/dpaa/ask/` + `0004-fman-pcd-subsystem.patch` ~5500 LOC in `fman/`):

- ONE ISO/package: `vyos-<version>-LS1046A-arm64.iso`. Multi-flavor (`default|ask|vpp`) retired 2026-06-14. Dataplane selected at runtime, no build-time choice.
- `kernel/flavors/ask/` = scaffold only (`.gitkeep`s + README pointer), wired unconditionally (no FLAVOR gate), contributes nothing yet. `97-ask-modules` hook kept wired for clean drop-in.
- Image ships vanilla VyOS for ASK dataplane. Kernel tracked via `vyos-build/data/defaults.toml` → `kernel/common/scripts/sync-kernel-version.sh`; currently `linux-6.18.28`.
- One `version.json` feed; `version-{default,ask,vpp}.json` = identical aliases (see S7).

ASK 1.x history (askN trail to ask6, 35 ASK-edit markers, `cmm.service`/`dpa_app rc=65280`/MURAM-exhaustion failure model): frozen archived `mihakralj/kernel-ls1046a-build` repo AGENTS.md.

## S4. Project / CI / shared VM

Two build paths:
1. CI (only CI path): `self-hosted-build.yml` ("VyOS LS1046A build (self-hosted)"), `workflow_dispatch`. Azure ARM64 VM start → build on self-hosted runner → deallocate. Warm ~5–10 min. ALWAYS use for CI.
2. Local dev loop: `bin/dev-build.sh` native on Cobalt 100 (32-core aarch64) → rsync to LXC 200:/srv/tftp/ for board TFTP. ~30 s incremental, ~2–3 min full. See `plans/DEV-LOOP.md`.

`auto-build.yml` ("reusable") = `workflow_call` only, no `workflow_dispatch`, invoked only by `self-hosted-build.yml`. Do NOT re-add `workflow_dispatch:` (re-enables hosted `ubuntu-24.04-arm` builds, burns Actions minutes). All build logic lives in `auto-build.yml`; wrapper = VM lifecycle only.

Shared Cobalt 100 VM: only this repo drives it (kernel repo absorbed May 2026). Idle-deallocator on the VM, ¬CI: workflows only `az vm start` (idempotent, never `az vm deallocate`); systemd `idle-deallocate.timer` (10 min threshold, 2 min checks for `Runner.Worker`) self-deallocates. Cost ≈ $0.20/day. Runners: `vm-runner-2` active; `vm-runner-1` dormant-registered (archived repo).

Hard rules:
1. NEVER re-add a stop-vm/`az vm deallocate` step (2026-05-06 production race: kernel build cancelled @1m12s during ISO build; still races the daemon).
2. NEVER block on external repo runs in build scripts (none remain).
3. Namespace fixed disk paths on `${RUNNER_NAME}` or live under `${GITHUB_WORKSPACE}`; bare `/build/...`/`/work/...` contends w/ dormant runner's `_work`.

Ref: archived repo `.clinerules/01-shared-vm-runtime.md` (daemon, units, Managed Identity, playbook).

## S5. Critical Non-Obvious Rules

- No auto-commit/push: NEVER commit/push w/o explicit user request. Stage + present for review.
- VyOS config: no comments inside `{}` blocks (`//`, `/* */` → parse fail); top-level only.
- Branch: `main` only (¬`master`). Never feature branches.
- Kernel symbols: verify vs Kconfig; invalid = silently ignored (`CONFIG_SERIAL_8250_OF` ∄; correct = `CONFIG_SERIAL_OF_PLATFORM`).
- `CONFIG_FSL_XGMAC_MDIO=y` required for FMan; w/o: all MACs defer "missing pcs", zero interfaces.
- Driver split: `fsl_dpaa_mac` (MAC/PHY/link, PHYLINK) + `fsl_dpa` (netdev, eth0-ethN, platform devs `dpaa-ethernet.N`). `fsl_dpaa_eth` = different driver, zero devices here. Don't confuse the 3.
- Kernel↔VPP handoff = AF_XDP: no unbind; kernel keeps netdev; VPP sockets on top. `vyos-1x-010` routes `fsl_dpa` → `driver='xdp'` (¬`'dpdk'`). ~3.5 Gbps on 10G SFP+.
- RC#31 (BLOCKED): DPDK `dpaa_bus` probe (`rte_bus_probe()`) inits ALL BMan pools + QMan FQs globally → kills kernel ifaces (eth0 mgmt) in seconds. HW-confirmed 2026-04-03, 2026-03-29. Unbind necessary ¬sufficient (shared QBMan state corrupted). Mixed DPAA-PMD+kernel impossible; all-DPDK impractical (serial-only). Fix needs DPDK scoping `dpaa_bus` init per portal/FQ.
- All ports boot kernel-owned; only `set vpp settings interface ethX` hands to VPP on apply/reboot.
- Entire DPAA1 stack `=y` ¬`=m` (FMAN/DPAA/BMAN/QMAN/PAMU); `=m` → late init, zero interfaces, silent.
- `CONFIG_NR_CPUS=4` (4× A72; VyOS default 256). Historical: SDK `sdk_dpaa` sized TX FQ arrays by NR_CPUS → soft lockups; mainline differs but 4 still correct, saves per-CPU mem.
- `CONFIG_QORIQ_CPUFREQ=y` ¬`=m` (module after clock cleanup T+12s → locked 700 MHz; built-in claims PLLs → 1600 MHz).
- U-Boot: initrd loads LAST (`${filesize}` = initrd size).
- `booti` ramdisk = `${ramdisk_addr_r}:${filesize}` (colon+size) else "Wrong Ramdisk Image Format".
- DTB @ `${fdt_addr_r}` (0x88000000), NEVER 0x90000000 (= `kernel_comp_addr_r` scratch; decompress 0xa0000000→0x0 via 0x90000000 destroys DTB → `ERROR: Did not find a cmdline Flattened Device Tree`).
- Boot = `booti` only; `bootefi`+GRUB permanently OOMs (DPAA1 reserved-memory nodes). No EFI path. Upgrades write `/boot/vyos.env`; no `fw_setenv` after initial setup.
- `/boot/vyos.env` = boot image selector (one line `vyos_image=<name>`; U-Boot `ext4load` + `env import -t`; written by patched `grub.set_default()` on install/upgrade/set-default). Never hand-edit except recovery.
- eMMC after `install image`: GPT; 32MiB firmware reserve; p1 BIOS boot 1MiB@32MiB; p2 EFI 256MiB FAT32@33MiB (GRUB, unused); p3 ext4 root @~289MiB. OpenWrt destroyed. All beyond firmware boundary → NXP re-flash non-destructive. Install from USB live session.
- Hybrid ISO: valid ISO9660 AND MBR disk. MBR in System Area bytes 440-511; FAT32 (~100MB: boot.scr/vmlinuz/initrd/DTB) appended. p1 type 0x17 @sector 0 = ISO data; p2 type 0x0C = FAT32. Write: `dd if=vyos.iso of=/dev/sdX bs=4M`. U-Boot `fatload usb 0:2` boots, live-boot mounts ISO9660 for squashfs. `add system image` loop-mounts same ISO (PVD @32768 untouched). One artifact, two boot paths.
- USB FAT32 = `0:2` explicit; auto `0:0` fails ("Can't set block device"). eMMC = `ext4load mmc 0:3`.
- kexec double-boot: `system_option.py` diffs `/proc/cmdline` vs `MANAGED_PARAMS` (hugepages, panic, mitigations…) pre-config_status → `kexec -l` + `systemctl kexec`. U-Boot bootargs MUST carry all managed params matching config.boot defaults; fix = `panic=60` in bootargs + `vyos-postinstall` UBOOT_BOOTARGS_TAIL. Hugepages ¬default; added by `set vpp settings` → one-time kexec. `kexec-load.service`/`kexec.service` NOT masked (6.6 QBMan fix `bman_requires_cleanup()` in `drivers/soc/fsl/qbman/` allows kexec on DPAA1; issue #7 resolved).
- `is_live_boot()` broken on U-Boot: checks `BOOT_IMAGE=/boot/`|`/live/` in cmdline; `booti` sets neither → always True → blocks `add system image`. Fix `vyos-1x-009` adds `vyos-union=/boot/` fallback; `vyos-postinstall` prepends `BOOT_IMAGE=/boot/<IMAGE>/vmlinuz`.
- DPAA1 XDP max MTU = 3290 (hard `fsl_dpaa_mac` limit); `xsk_socket__create()` EINVAL above. VPP SFP+ max frame ~3304 (3290+14), ¬jumbo. Kernel RJ45 keeps 9578.
- VPP via native CLI `set vpp settings …` (¬custom systemd). Default: no ports assigned. `set vpp settings interface eth3|eth4`; removal releases socket, kernel retains. MTU ≤3290 on AF_XDP ports.
- VPP hugepages ~416MB 2M (256M heap + 128M statseg + 32M buffers); dynamic: `set vpp settings` → `hugepage-size 2M hugepage-count 512` → one-time kexec. Insufficient → "Not enough free memory to start VPP!".
- Syntax: `hugepage-count` NOT `hugepage-number` (wrong keyword = silent fail).
- SSH interactive `vbash -c 'source /opt/vyatta/etc/functions/script-template; configure; set ...; commit'` HANGS. Workaround: write vbash script (`vyatta-cfg-cmd-wrapper` cmds), SCP, execute; kill stale vbash + restart configd on locks. (For board channels see rule below: `#!/bin/vbash` sourcing script-template preferred.)
- `/sys/class/net/eth3/device` → PARENT `fsl_dpaa_mac` (¬child `dpaa-ethernet.N`); `net/` lives on parent. Find child for unbind: walk `device/` for `dpaa-ethernet.*` (`vyos-1x-010` `_dpaa_find_platform_dev()` must).
- DPDK GROUP linker script drops constructors (`RTE_REGISTER_BUS`/`RTE_PMD_REGISTER` self-contained → GROUP pulls only unresolved-ref .o) → zero buses/scan/ifaces. Fix: `ld -r --whole-archive` fat relocatable object.
- `accel-ppp-ng`: upstream ARM64 mirror lacks it; VyOS `build-accel-ppp-ng.sh` ALWAYS fails ARM64 (needs VPP source build). Our `ci-build-accel-ppp.sh`: daemon + `ipoe.ko`/`vlan_mon.ko` from `accel-ppp/accel-ppp-ng` commit `3e30d9b`, no VPP plugin. Needs `libpcre2-dev` (PCRE2, ¬`libpcre3-dev`). Runs from `ci-build-packages.sh` post-kernel while `$KSRC` exists. Kmod fail → daemon-only. Non-fatal in caller, but ISO fails on unmet `vyos-1x` dep if no .deb.
- OOT modules MUST be signed (`CONFIG_MODULE_SIG_FORCE=y`; auto keys `certs/signing_key.{pem,x509}`): `$KSRC/scripts/sign-file sha512 $KSRC/certs/signing_key.pem $KSRC/certs/signing_key.x509 module.ko` AFTER build, BEFORE tree cleanup. Pre-built modules can't be signed → always fail.
- `LOCALVERSION=-vyos` MANDATORY on EVERY kernel build path (CI `bindeb-pkg`, dev-loop `make Image`, OOT `make -C $KSRC M=$PWD LOCALVERSION=-vyos modules`; default in `kernel/common/scripts/build-kernel.sh:42`). Missing → vermagic `6.18.31+` vs board `-vyos` → `Invalid module format`/`exec format error`. Verify: `cat $KSRC/include/config/kernel.release` = `<KVER>-vyos`; `strings module.ko | grep ^vermagic`.
- binutils: `apt-mark manual binutils` in `97-dpaa-dpdk-plugin.chroot` (live-build autoremoves even `--custom-package` installs; else no `nm`/`objdump`/`readelf` on target).
- After patching VyOS Python live: `systemctl restart vyos-configd` AND `find / -name __pycache__ -path '*/vyos/*' -exec rm -rf {} +` (configd caches pyc).
- VPP thermal MANDATORY: poll-mode → `HARDWARE PROTECTION shutdown (Temperature too high)` on `ddr-controller` (zone0) + `core-cluster` (zone3) w/in ~30 min idle. `set vpp settings poll-sleep-usec 100` required. AF_XDP has no adaptive rx-mode (`set interface rx-mode` fails "unable to set"); only fix = no workers (`cpu-cores 1`).
- EMC2305 DTS cooling-maps unbound (no `cdev*` in sysfs). Workaround: `fan-pid` daemon (see S11) replaces lm-sensors `fancontrol` (pkg not installed; `fancontrol.service` masked in `data/hooks/98-fancontrol.chroot` so two PWM writers never race). Zones ddr/serdes/fman/cluster/sec q2s; setpoints ddr 65/80, serdes 70/85, fman 70/87, cluster 65/85, sec 65/85; kp 4–6, ki 0.20–0.30; EMA α=0.4; deadband 3; PWM floor ~51 (~1700 RPM, EMC2305 quantization); force-MAX @≥crit; `LOG_CRIT` @≥100°C; SIGTERM → PWM=255.
- Kernel 6.18 emc2305 sysfs PWM broken; `fan-pid` writes reg 0x30 over /dev/i2c directly. Verified 2026-05-11, 6.18.28-vyos, hwmon3 = i2c-7 @0x2e: sysfs write 0<N<255 "succeeds", reverts to 255 in ~1 s; chip reg 0x30 stays 0xFF; fan pinned ~8700 RPM; only 0/255 reach chip. Chip fine (FAN_CONFIG1 0x32 bit7=0, direct-PWM, FSC off) — kernel sub-MAX write path bug. Direct write 51→0x30 via `/dev/i2c-7` `I2C_SLAVE_FORCE` (ioctl 0x0706) → ~1500 RPM. Fix: `find_emc2305_i2c()` walks `/sys/bus/i2c/devices/<bus>-002e` for `emc2305` link, opens `/dev/i2c-<bus>`, `I2C_SLAVE_FORCE` (plain `I2C_SLAVE` = EBUSY, kernel holds it), `os.write(fd, bytes([0x30, value]))`. Tach reads stay sysfs (`fan1_input`). Daemon `modprobe i2c-dev` at start (no lm-sensors → no modules-load drop-in). Legacy `write_pwm(path,value)` = REFERENCE ONLY stub, never call. Regression symptom: `fan-check` shows `pwm1=255 (100%)` while journal shows `pwm=51`.
- SFP+ cages (eth3/eth4) = 10G-only; 1G modules fail "unsupported SFP module: no common interface modes" (no serdes PHY provider in DTB → `memac_supports()` allows DTS mode only, 10GBASER post-xgmii).
- SFP-10G-T rollball (RTL8261): link immediately w/ patch 4003 + LOS GPIO. Carrier needs 10GBASE-T peer; 1G-only switch → LOS permanently HIGH.
- Patch `4003-sfp-rollball-phylink-einval-fallback.patch`: `sfp_add_phy()` → `phylink_attach_phy()` = `-EINVAL` (PHY on INBAND MAC rejected) → w/o patch `SFP_S_FAIL`, no link. Patch: `-EINVAL` → non-fatal proceed-w/o-PHY (like `-ENODEV` retry exhaustion); in-band 10GBASE-R sync then detects carrier. SR-EEPROM modules (10Gtek ASF-10G-T) unaffected (rollball probe skipped).
- SFP `los-gpios` MUST exist w/ `GPIO_ACTIVE_HIGH` (GPIO2 pins 9/11). No-link → LOS HIGH → `wait_los`; link → LOW → `link_up` w/ carrier. W/o it: races to link_up; phylink in-band PCS polling broken on FMan 10G in 6.6.130+ → permanent no-carrier.
- SFP `tx-disable-gpios` = `GPIO_ACTIVE_LOW` (board has hardware inverter to cage TX_DISABLE). `ACTIVE_HIGH` → TX disabled forever. Pins: sfp-xfi0/eth3 = gpio2 pin 14 (global 590, gpiochip2 line 14); sfp-xfi1/eth4 = gpio2 pin 13 (global 589, line 13).
- DTS thermal path = `/thermal-zones/core-cluster/trips` (¬`cluster-thermal`) per 6.6 `fsl-ls1046a.dtsi`; wrong → DTB compile fail → silent SDK-DTB fallback (no SFP nodes).
- SDK port compatibles (SDK-DTB context): `fm_port_driver` matches only `fsl,fman-port-{1g,10g}-{rx,tx}`; mainline uses `fsl,fman-v3-port-{rx,tx}` → `fm_port_probe()` never runs → `dev_get_drvdata(port@XXXXX) failed` -22 → zero ifaces. `mono-gateway-dk-sdk.dts` overrode all 16 port nodes (6×1G+2×10G, RX+TX).
- `phy-connection-type` for 10G = `"xgmii"` ¬`"10gbase-r"` (fallback assigns `xfi_pcs`; 10gbase-r → `sgmii_pcs` → broken link).
- 10G MACs `status = "okay"` (`ethernet@f0000` MAC9, `ethernet@f2000` MAC10) so eth3/eth4 exist at boot; `disabled` = limbo. `fsl,dpaa` container = DPDK-userspace only; kernel ignores.
- Port order (patch `vyos-1x-027`): probe order = DT unit-address (e2000, e8000, ea000, f0000, f2000) ≠ physical; systemd renames to e2-e6 pre-VyOS. Old squashfs rename layer (10-fman-port-order.rules + fman-port-name + 00-fman.link) deleted 2026-05-15 (inert: initramfs renamed first). Fix: `vyos_net_name` no-hw-id fallback (`on_boot_event`) DT-aware on `fsl,ls1046a`; `get_fman_predefined()` maps `of_node` → canonical name. config.boot `hw-id` ALWAYS wins; patch affects fresh installs/live only. Pre-027 installs: fix once by hand (`set interfaces ethernet ethN hw-id <mac>` swap + reboot; lab board done 2026-06-10). Physical map (via DT local-mac-address): eth0=left RJ45 MAC5/e8000/`16:00`; eth1=center MAC6/ea000/`16:01`; eth2=right MAC2/e2000/`15:ff`; eth3=left SFP+ MAC9/f0000/`16:02`; eth4=right SFP+ MAC10/f2000/`16:03`.
- DPDK DPAA PMD needs `CONFIG_STRICT_DEVMEM` + `CONFIG_IO_STRICT_DEVMEM` both `is not set` (`fman_init()` mmaps CCSR via `/dev/mem`; else EPERM "FMAN driver init failed").
- RJ45 PHYs = Maxlinear GPY115C (ID `0x67C9DF10`); `CONFIG_MAXLINEAR_GPHY=y` (`mxl-gpy.c`). Generic PHY → SGMII AN re-trigger fails → eth2 never links (GPY2xx: AN only on speed *change*; driver works around).
- DTS must match nix ref: `compatible = "mono,gateway-dk", "fsl,ls1046a"` + ethernet aliases; canonical `nix/pkgs/kernel/dts/mono-gateway-dk.dts`.
- INA234 needs OOT patch (old "native since 6.10" claim WRONG; verified absent 6.18.x). 8× INA234 behind pca9545 (buses 12/13, addr 0x40–0x43); w/o match all `power_sensor@4x` unbound (`-ENODEV`). Patch `kernel/common/patches/board/4002-hwmon-ina2xx-add-ina234-support.patch`: `ina234` enum, per-chip config, `ti,ina234` of_match + i2c_device_id, Kconfig text. Register-compat INA226, different scaling. 6.18 formula `(regval >> bus_voltage_shift) * bus_voltage_lsb`; 12-bit result in bits[15:4] → `bus_voltage_shift=4`, `bus_voltage_lsb=1600` µV (1.6 mV). Do NOT reuse parked 6.6 `lsb=25600` (matched old `(regval*lsb)>>shift`; 16× over-read on 6.18). Power coeff 32 (INA226: 25), `calibration_value=2048`, `shunt_div=400`, `config_default=INA226_CONFIG_DEFAULT`, `has_alerts=true` (6.18 registers via `devm_hwmon_device_register_with_info`; NO `ina226_group`/`data->groups[]` hunk). `CONFIG_SENSORS_INA2XX=y` forced in `kernel/common/kernel-config/00-board.config` (defconfig `=m`).
- FMan firmware: U-Boot injects from SPI `mtd4` into DTB pre-boot; no `request_firmware()`, no `/lib/firmware/`.
- Builder image: `ghcr.io/huihuimoe/vyos-arm64-build/vyos-builder:current-arm64` — do NOT fork/rebuild.
- OpenWrt live device: `root@192.168.1.234` (¬192.168.1.1).
- Board access (installed VyOS), two channels: serial `plink -serial COM11 -sercfg 115200,8,n,1,N` (Windows agent workstation, local COM11, drive interactively in async shell) AND SSH `ssh -i ~/.ssh/vyos_key vyos@192.168.1.190` (lands eth0 mgmt). SSH for normal verification; serial for U-Boot/recovery, full-boot watch, interactive `add system image` (live `vyos@vyos:~$` when up). Console echoes CR as `^M`; `$`/`?` can mangle — keep serial cmds simple. (Historical TCP-serial relay `192.168.1.16:5555` retired.) Non-interactive config on either channel: `#!/bin/vbash` script sourcing `/opt/vyatta/etc/functions/script-template` (NOT `vbash -c`, NOT bare `vyatta-cfg-cmd-wrapper` — validator-env gotcha).
- Git on Windows: `core.filemode=false`.
- Don't push during builds (workflow updates `version.json` → conflicts; `git pull --rebase` if hit).
- NEVER `install image` from installed system (USB-live only; repartitions eMMC, expects `/usr/lib/live/mount/medium/live/filesystem.squashfs`; from eMMC DESTROYS install). Use `add system image <url>`.
- Image deployment = USER's task. Build-image skill publishes ISO to lxc200 + emits exact `add system image <url>`. Agent must NOT run installer over SSH (vbash loop hangs on duplicate names; `yes | run add system image` risks deleting active squashfs → unbootable). Provide URL only.
- NEVER `rm -rf /boot/<image>` on a running board (unlinks root backing; survives until reboot, then USB/TFTP recovery required).
- DPAA1 XDP queue_index bug: `xdp_rxq_info_reg()` passes `dpaa_fq->fqid` (≥32768); XSKMAP `max_entries` 1024 → `bpf_redirect_map()` always fails → XDP_PASS → AF_XDP RX 0 pkts. Fix `patch-dpaa-xdp-queue-index.py`: fqid→0 (DPAA reports 1 combined channel; VPP single XSK).
- Offloads: TSO/LRO hardware-impossible (`[fixed]` off); max = `gro gso sg rfs rps`; never attempt TSO. `hw-tc-offload` NO LONGER fixed: `0104a-dpaa-netdev-advertise-hw-tc.patch` adds `NETIF_F_HW_TC` (toggleable, default-off); auto-enabled by `set interfaces ethernet ethN ingress-policer` (`vyos-1x-025`) → FMan HW ingress-policer (matchall→police, `skip_sw`/`in_hw`). HW-verified 2026-06-09 (2026.06.08-2355, 6.18.34-vyos): `in_hw`, no `-22`. Steering FIXED 2026-06-09 (2026.06.09-0522, run `27185670881`): `0097`+`0104` reprogram the port's EXISTING RSS scheme in-place to next-engine=PLCR (`fman_pcd_kg_port_attach_policer`/`_detach_policer`, `kg_find_port_scheme()`), ¬parallel catch-all (frames bypassed). Proven: eth3 RX BMI `0x10` → scheme 3 MODE `0xc04c0000`[PLCR]; FMPL profile-0 PEMODE `0xd0013000`, PEGNIA/PEYNIA `0x80500002`[ENQUEUE], PERNIA `0x805000c1`[DISCARD], PECIR/PECBS/PECTS correct.
  - BUG 3a FIXED+HW-VALIDATED. Symptom: 100% policed loss, all G/Y/R counters 0. NOT profile addressing — 3 theories DISPROVEN on HW (`NIA_PLCR_ABSOLUTE` mode `0xc04c8000`; per-port `FMBM_RPP`; RELATIVE+`FMPL_PMR` — the last rebooted the board under traffic). Real cause: FMan1 Policer block FMPL (CCSR `0x01AC0000`) boots w/ `FMPL_GCR` (reg `0x000`) master `EN` (`0x80000000`) AND `STEN` (`0x40000000`) clear (live `0x00500002`) → whole block disabled → frames drop pre-meter. Proven: live `/dev/mem` `FMPL_GCR ← 0xC0500002` → 100%→0% loss. Fix in patch `0100`: `plcr_enable_block(pcd)` RMW `gcr |= EN|STEN` preserving DEFNIA (→`0xC0500002`) after `plcr_commit_profile()`; scheme stays RELATIVE `0xc04c0000`. Cold-boot validated, no manual write (image `vyos-2026.06.09-2032-rolling`, run `27233990716`, commit `1a48948`, kernel `6.18.34-vyos`): `set … ingress-policer bandwidth 1gbit` → GCR auto `0xC0500002`, TPC increments, ping `10.99.1.2` ×8 → 0% loss. Old note "OR `NIA_PLCR_ABSOLUTE` into mode at `fman_keygen.c:537`" = DISPROVEN theory, do NOT reintroduce. (`tc police` sw counters stay 0 — fully offloaded; FMPL `TPC`/`GCR`/`RPC` authoritative; BMI RX stats read-clearing/unreliable.)
  - BUG 3b: non-revert half FIXED (A, kernel `0104`: block-cb via `flow_block_cb_alloc(... release ...)` → release reverts scheme + destroys profile on block-unbind; closes `__tcf_block_put()` running `tcf_block_offload_unbind()` before `tcf_block_flush_all_chains()` so `TC_CLSMATCHALL_DESTROY` never reached driver. B, `vyos-1x-025`: idempotent `tc filter del … pref {pref}` BEFORE `super().update()`). Verified: delete → filter empty, policer detached, ping 5/5, delete→re-apply clean. OPEN: iperf3 flood-crash half untested (reverted `FMPL_PMR` build watchdog-reset under policed flood; stuck-PLCR scheme survives warm reset → needs serial capture + cold power-cycle). Repro policer w/ a few pings, NEVER a flood. Remaining measurement: wire-level throughput cap (needs §8 traffic harness).
- Jumbo module param: `fsl_dpaa_fman.fsl_fm_max_frm=9600` (KBUILD_MODNAME = `fsl_dpaa_fman`, ¬`fman`; wrong name silently no-ops, MTU stays 1500).
- Watchdog = IMX2 WDT @`0x2ad0000` (`"fsl,ls1046a-wdt", "fsl,imx21-wdt"`): `CONFIG_IMX2_WDT=y` (`imx2_wdt.c`); ¬SP805/SBSA. Node in `fsl-ls1046a.dtsi`, always present; w/ driver → `/sys/class/watchdog/watchdog0`.
- QSPI: `CONFIG_SPI_FSL_QUADSPI=y` else no `/dev/mtd*`, no `fw_setenv`. 64MB NOR, 9 partitions; env = `/dev/mtd2` "uboot-env" (1MB, 4KB erase); `CONFIG_ENV_SIZE` 0x2000 (8KB). Numbering changed (was mtd3) — verify `cat /proc/mtd`.
- `libubootenv-tool` `fw_env.config` legacy format `Device Offset Env_size Sector_size`: `/etc/fw_env.config` → `/dev/mtd2 0x0 0x2000 0x1000` (CRC brute-force confirmed on HW; mtd3 was wrong, fixed 2026-04-03).
- `vyos-postinstall` Phase 1: forced (`force=1`) from `install image` (detected via `--root`) — always rewrites `vyos`/`usb_vyos`/`bootcmd`. From boot service (no `--root`): skip if `fw_printenv -n vyos` contains "vyos.env". Boot order written: USB first (`usb start; if fatload usb 0:2 ${load_addr} boot.scr; then source ${load_addr}; fi`) → eMMC (`run vyos`) → SPI recovery. Manual U-Boot setup (INSTALL.md Step 4) = fallback. Phase 2 writes `/boot/vyos.env` every boot to sync running image.
- Fresh-format first boot may print `Failed to load '/boot/vyos.env'` → SPI recovery (U-Boot ext4 first-access quirk; file IS correct). Just `reboot`; second boot fine. Not a bug.
- Migration scripts assume GRUB (e.g. `system/31-to-32` T8375: `disk.find_persistence()` + `CFG_VYOS_VARS` `['console_type']` → KeyError → "Configuration error" → hostname `localhost.localdomain`). Fix `vyos-1x-017`: try/except + `os.path.exists()` + `.get()`. WATCH new migration scripts w/ `vars_read()` + `['key']` — same treatment needed.
- `add system image` MUST re-assert QSPI `bootcmd` (boot-order trap): USB `install_image()` calls `vyos-postinstall --root` (patch `006`) → eMMC-first. Upgrade `add_image()` historically only copied DTBs + `grub.set_default()` (vyos.env via `011`) — never touched `bootcmd` → hand-edited dev bootcmd (`run ask_boot || run vyos || run recovery`, `dev_boot`/tftp) kept booting stale TFTP kernel on every upgrade. Observed 2026-06-13 (192.168.1.190: TFTP `6.12.49` + `0206`-era squashfs while eMMC had `6.18.34-vyos`). Two-layer fix: (1) `vyos-1x-028-add-image-uboot-env.patch`: `add_image()` runs `f'{postinstall} --root {root_dir} {image_name}'` (guard: `postinstall.exists() and Path('/dev/mtd2').exists()`) right after DTB-copy loop → FIRST reboot boots new eMMC image. (2) `vyos-postinstall` boot-service self-heal: Phase 1 idempotency (inside the `grep -q "vyos.env"` block) also compares live `fw_printenv -n bootcmd` vs canonical `UBOOT_BOOTCMD='run usb_vyos || run vyos || run recovery'` (single source of truth), force-refresh on mismatch → self-heal on SECOND boot. Live emergency: `fw_setenv bootcmd 'run usb_vyos || run vyos || run recovery'`. Patch 028 applies via pure context (`index` blob irrelevant; vyos-1x built from `commit_id = "rolling"` → `git apply --3way` falls back to direct context). Anchor = SECOND duplicate DTB-copy loop (006+011 each add one) + unique `# unmount an ISO and cleanup` / `cleanup([str(iso_path)])`.

## S6. FMan KeyGen / ASK2 Silicon Rules

Authoritative: `specs/fman-keygen-flow-key-spec.md` v2.0 (2026-07-10).

### Architecture (settled)
- EKFC only, no GEC: `kgse_gec[]` stays 0. SDK uses FMC/GEC declared order (SIP,DIP,PROTO,SPORT,DPORT); ASK2 gets silicon's fixed EKFC order. Same 5 fields, 13 bytes, different byte order. Do NOT "fix" ASK2 layout to match SDK. (§1.3–1.4)
- RCCB → FE_ENTER direct = correct dispatch. No CC group table/node/match table (F-044, F-047 removed). OQ4 (CC-hop clobbers hash) moot — no hop. (§5)
- `kgse_hc` ≠ hash-algorithm selector. KG hash = fixed silicon CRC-64 (ECMA-182, reflected poly `0xC96C5795D7870F42`); `kgse_hc` only configures FQID distribution (shift/symmetric/mask). No Toeplitz anywhere. Do NOT read back `kgse_hc`. (§4.3)
- ASK2 CRC64 settings (in place): `hashShift = 0`, `symmetric = false` (direction-distinct, conntrack-friendly), `mask = 0x7fff`. `fman_pcd_crc64()` + `fman_pcd_ehash_bucket_index()` verbatim-identical to ASK1 `get_indexed_hash_bucket()`. (§4.3)

### Target EKFC
- Target = `0x001C0006` (adds PTYPE1 bit 18 → 5-tuple): `IPSRC1|IPDST1|PTYPE1|L4PSRC|L4PDST`, 13 bytes. 4-tuple `0x00180006` aliases TCP/UDP sharing IP:port = silent misforwarding. (§2–3)
- IPsec SPI bit 9 MUST NOT be set on non-IPsec schemes (no SPI offset → random bytes → unpredictable key; F-043 origin). (§4.1)
- PTYPE1 has no EKDV default-value slot (§4.2). Guard: reject `proto == 0` at flow insert (§10.6). Non-IP never reaches FE path (IPv4-indication gated); leak-through `proto=0` → deterministic 0x00, matches nothing.

### Extraction order: SETTLED 2026-07-13
- CONFIRMED MSB-first: SIP → DIP → PROTO → SPORT → DPORT; 13 bytes @EKFC=0x001C0006. HW-verified (board 192.168.1.185, 6.18.38-vyos, ISO 2026.07.13-1938-rolling) via CRC-64 match on two independent TCP flows on eth4. Ascending-bit and size-grouped models DISPROVEN.
- HW KG hash = RAW CRC-64, no final complement. Silicon stores `crc64_raw(key)` @IC offset 0x48 (seed `~0ULL`, NO final `~crc` XOR). CRC-64/XZ finalized variant does NOT match. Verified: `crc64_raw(SIP|DIP|6|0xAD9C|0xD903) = 0x600824e70ae4d573` = captured hash; `crc64_xz` ≠. Use raw for ehash keys + bucket indices.
- Repeatable methodology: mainline RSS on eth4 (KG in RX path, `receive-hashing: on`, `FMBM_RFPNE` HWK), no ASK engage; eth4-only capture via `strcmp(net_dev->name, "eth4")` in `rx_default_dqrr`; controlled TCP SYN `.106:portA → .185:portB` distinct nonzero fields; read `hash_probe` debugfs; compare `crc64_raw()` over 13-byte key in confirmed order.
- IPv6 = separate KG scheme + separate ehash table (16B addrs → 37-byte key). Design must not preclude. (§12)

### Immediate required actions (order-independent)
- ~~Revert F-046~~: restore `word0 = 0x40800000` (ALLOCATE) on FE_ENTER AD. F-046 stripped it speculatively vs the only config that ever HIT. ALLOCATE allocates the FE workspace holding extracted key + KG hash. (§5.4)
- Delete scaffold block PHYSICALLY (¬`if (0)`): allocated 304 B/engage from gen_pool, never freed, wrote over active FMan structures → MURAM corruption, `ecir.fqid=0x0` storms, SFP+ link lock. Every MISS predates F-047. (§5.5, §10.7)
- Delete dead code, never disable (`if (0)` survives rebases, re-enables by accident; git holds history). (§10.7)

### Defensive requirements (defect-traceable)
- Never write MURAM at unowned offset (F-047 origin): only addresses from `fman_muram_alloc()` for this object, offset < size. (§10.1)
- Readback every unreporting silicon write: after programming FE descriptor/KGSE, read back + compare; fail engage on mismatch. (§10.2)
- Key length from ONE constant: kernel exports key_len via debugfs, shell reads it; no literal byte counts (origin: `${#key} -ne 24` gate survived 13-byte change, silently blocked inserts). (§10.3)
- Build that can't verify its key layout MUST refuse engage: `fe_arm_engage()` → `-EPROTO` unless `fman_pcd_key_selftest()` passed since boot; override `fman_pcd.force_unverified=1` experiments only. (§10.4)
- `keysize` MUST equal full extracted length (`ehash->keysize != pcd->key_len_v4` = checked error; truncation w/ addresses last = wrong flow, ¬coarser). Coarser flows: change EKFC, ¬keysize. (§10.5)
- Never change known-good on a hypothesis (F-046 origin): need contradicting observation or A/B measurement. (§10.8)
- Always cold-boot before silicon experiments (deaf-port = accumulated BMI corruption, ¬commit; warm reboot doesn't clear BMI/MURAM). Record boot type per result. (§10.9)
- One variable per experiment (2026-07-04 HIT had 4 simultaneous candidate keys). One key, one flow, one packet class. (§10.10)

### Flow-HIT failure candidates (ranked, spec §13; only E2 workspace dump §11-Step-3 discriminates)
1. Scaffold MURAM corruption — UNTESTED (every MISS predates F-047).
2. keysize < extracted length truncating IPs — never considered pre-v2.0.
3. F-046 ALLOCATE strip — UNTESTED.
4. ~~Extraction order~~ — CLOSED 2026-07-13 (MSB-first confirmed).
5. CC-hop hash clobber — CLOSED (no hop post-F-044).
6. CRC64/kgse_hc mismatch — CLOSED (§4.3 identical).
7. Bucket/entry struct layout — CLOSED (verbatim SDK match).
8. contextOffsetInWS — CLOSED (both 0).

## S7. Local Dev Loop Rules

- Single CI workflow: edits to build logic (kernel config, patches, ISO recipe) go in `auto-build.yml`; `self-hosted-build.yml` = VM lifecycle + dispatch only. Do NOT add `workflow_dispatch:` to `auto-build.yml` (see S4).
- Kernel config appended, ¬replaced: new `CONFIG_*` at END of workflow `printf` block (`vyos_defconfig` upstream; ours appended post-checkout).
- `scripts/config --enable` does NOT upgrade `=m`→`=y`; use `scripts/config --set-val X y` (critical for TFTP boot, no modules).
- VyOS kernel needs 7 config fragments from `vyos-build/scripts/package-build/linux-kernel/config/*.config` cat'd into `.config` after defconfig copy (else SQUASHFS/OVERLAY_FS/netfilter missing).
- `boot=live` REQUIRED in bootargs even installed (initramfs squashfs-overlay dependency).
- `vyos-union=/boot/<IMAGE>` → squashfs on eMMC p3; must match `show system image` name.
- TFTP bootargs MUST include `panic=60` (MANAGED_PARAMS match; else first-boot kexec). Hugepages ¬needed unless VPP in config.boot.default.
- TFTP `vmlinuz` = gzip `Image.gz` (¬raw `Image`): `make Image.gz ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-`; `cp arch/arm64/boot/Image.gz /srv/tftp/vmlinuz` (~10MB vs ~25MB; `booti` decompresses; raw may fail boot / wrong `${filesize}`).
- ISO deployment invariant: every successful CI ISO → lxc200 (`admin@192.168.1.137` via Tailscale, key `~/.ssh/admin_key`) at `/srv/tftp/iso/<versioned>.iso`; refresh symlinks `sudo ln -sfn <versioned>.iso /srv/tftp/iso/latest.iso && sudo ln -sfn <versioned>.iso /srv/tftp/iso/latest-vpp.iso` (canonical + back-compat). HTTP server (`python3 -m http.server 8080 --directory /srv/tftp`, persistent since 2026-05-15) → operator URL **`http://192.168.1.137:8080/iso/latest.iso`** for `add system image` (versioned URL retained for pinning; legacy `latest-vpp.iso` alias). `.minisig` sidecar uploaded alongside. Mechanism: `rsync` w/ `--rsync-path='sudo rsync'` + explicit `-e "ssh -i ~/.ssh/admin_key"` (agent shells lack `~/.ssh/config`); SSH MCP `ssh_upload_file` TIMES OUT on 575+ MB — rsync mandatory. Do NOT conflate w/ TFTP live-boot artefacts (`vmlinuz`/`initrd.img`/`mono-gw.dtb`/`*.squashfs` via `bin/dev-build.sh iso-live` → `/srv/tftp/` directly, ¬`/srv/tftp/iso/`); both coexist.
- `mono-gateway-dk.dts` fails on mainline 6.6 (thermal path); fallback = pre-built `board/dtb/mono-gw.dtb`.
- Separate `make Image` from `make dtbs` (broken DTS kills whole dtbs target; build DTS separately w/ `|| true`).
- binfmt-support on Proxmox HOST ¬LXC (`qemu-user-static` won't register in unprivileged LXC).
- No loop mount in LXC → `7z` for ISO extraction.
- Patch numbering: `data/vyos-1x-NNN-*.patch` / `data/vyos-build-NNN-*.patch`, 3-digit sequential w/ gaps; next free number; applied in filesystem sort order.
- Patches applied w/ `git apply --3way --whitespace=nowarn`, NEVER raw `patch`. All `.patch` in `data/` + `kernel/` are git-format (`diff --git`, `index` blob SHAs); `--3way` anchors on blob SHAs, real 3-way on drift (¬silent fuzz). NEVER reintroduce `patch -p1` or `--no-backup-if-mismatch`. New patch: clone target upstream at patched version, edit, `git diff --cached > <bucket>/<NNN>-<slug>.patch`, verify `git apply --3way --check`. Mergiraf wired as merge driver via `.gitattributes` dropped at clone-time by each `bin/ci-*.sh` (AST-aware C/Python/JSON/YAML/TOML/XML). Rot monitored weekly: `.github/workflows/patch-rot-check.yml` (watch Actions warnings). Visual diff review MANDATORY: `--3way` catches drift, NOT malformed hunk arithmetic that applies but writes wrong content (cf. archived repo ask13→14 silent truncation).
- config.boot.default: NO comments in blocks (top-level only).
- Console = `ttyS0` ¬`ttyAMA0` (workflow seds 2 upstream files); new serial refs use ttyS0.
- All DPAA1 configs `=y`, never `=m` (early init pre-rootfs).
- CAAM (SEC 5.4): for userspace crypto (cryptodev/openssl-engine) full stack `CONFIG_CRYPTO_DEV_FSL_CAAM=y`, `_JR=y`, `_COMMON=y`, `_CRYPTO_API_DESC=y`, `_AHASH_API_DESC=y`. Legacy ASK1 kernel IPsec offload (`dpaa_submit_{inb,outb}_pkt_to_SEC()`) deleted; ASK2 re-architects outside kernel; `CONFIG_INET_IPSEC_OFFLOAD` NOT required.
- update-check feed: ONE canonical `version.json` at root of `main` (`system update-check url …/main/version.json`). `version-{default,ask,vpp}.json` = byte-identical aliases for fielded pre-2026-06-14 installs — do NOT delete (board w/ baked alias URL would silently abort `add system image latest`). All four CI-managed by publish job in `auto-build.yml` (writes canonical, copies verbatim) — never hand-edit (clobbered next run). ISO filename flavor-neutral; one ISO per `release/<tag>`. Default configs (`board/vyos-config/config.boot.{default,dhcp,full}`) carry literal `…/main/version.json`; no per-flavor rewrite (removed w/ flavor collapse).
- DTB → `board/dtb/` (copied to `includes.binary/`, lands at ISO root).
- `MOK.key` = secret; only `MOK.pem` in repo; private key from `${{ secrets.MOK_KEY }}`.
- `vyos-postinstall` board-gated: checks `/proc/device-tree/compatible` for `fsl,ls1046a`, exits early otherwise; safe in every ISO. Phase behavior: see S5.

## S8. DPAA1 DPDK PMD (REMOVED)

Abandoned 2026-04-03 (RC#31). Analysis: `plans/VPP-DPAA-PMD-VS-AFXDP.md`. Production = AF_XDP via `vyos-1x-010-vpp-platform-bus.patch` (~3.5 Gbps 10G SFP+). All DPDK/USDPAA infra deleted 2026-04-28 (was `archive/dpaa-pmd/`).

## S9. Workflow-Specific Gotchas

- `reftree.cache`: required vyos-1x blob missing upstream — copy from `data/reftree.cache`.
- Makefile copyright hack: `sed -i 's/all: clean copyright/all: clean/'` (target fails in CI).
- Only 2 packages built from source: `linux-kernel`, `vyos-1x`; rest upstream.
- `rm -rf packages/linux-headers-*` pre-ISO (runner space).
- Secure Boot: MOK.pem/MOK.key module signing; minisign ISO signing; `grub-efi-arm64-signed` + `shim-signed` included.
- Trigger model: only `self-hosted-build.yml` dispatchable (no push/schedule). Builds on demand: `gh workflow run "VyOS LS1046A build (self-hosted)"`.
- vyos-1x MUST produce a .deb — silent substitution = #1 install-breaker. vyos-build `build.py` swallows sub-package failures (`try/except` "Failed to build package X … ignoring"). If `dpkg-buildpackage` fails (e.g. upstream `Makefile:106` `PYTHONPATH=python/ python3 -m nose2 -v`; `test_check_port_availability` probes 127.0.0.1:8080, fails when runner has leftover lighttpd/accel-ppp-ng bound) → no `vyos-1x_*_arm64.deb` → `ci-pick-packages.sh` stages nothing → live-build pulls STOCK vyos-1x → ISO ships unpatched `image_installer.py`. Board symptom: `add system image` writes `/boot/<image>/` w/ only vmlinuz+initrd+squashfs, NO `mono-gw.dtb` (patches 006+011 add the DTB copy loop) → bootcmd falls to SPI recovery, install unbootable until manual U-Boot fix. Guards: (1) `bin/ci-setup-vyos1x.sh` pre_build_hook ends `sed -i 's|^\tPYTHONPATH=python/ python3 -m nose2.*|\ttrue|' Makefile` (keeps `python3 -m compileall` line); (2) `bin/ci-build-packages.sh` cache-populate hard-fails `exit 1` + `::error::` if `../vyos-1x_*_arm64.deb` empty. Verify CI logs: "Failed to build package vyos-1x" ABSENT AND `### Cached N vyos-1x .deb(s)` PRESENT (N≥1). Verify board: `unsquashfs -l` + `grep -E '\.dtb|mmcblk|fw_setenv|vyos\.env' usr/libexec/vyos/op_mode/image_installer.py` — zero hits = substitution. Diagnosed run `26142046765`, 2026-05-20.
- CI caches (warm wall ~15→~7 min): (1) `ci-build-packages.sh` caches `vyos-1x_*_arm64.deb` under `${RUNNER_TOOL_CACHE:-/tmp}/vyos-1x-cache/`, key `vyos-1x_<UPSTREAM_SHA12>_<PATCH_HASH16>` (`UPSTREAM_SHA` from `commit_id = "..."` in `vyos-build/scripts/package-build/vyos-1x/package.toml`; `PATCH_HASH` = `sha256(cat data/vyos-1x-*.patch) | cut -c1-16`). HIT replays .deb to `package-build/` (dpkg output dir, parent of `vyos-1x/`), skips `./build.py` (~6 min). Patch-glob hash intentionally order-sensitive (upstream applies `sorted(patch_dir.glob('*'))`; rename/add/delete legitimately invalidates). 14-day mtime GC each run. Persistent runner disk carries cache. (2) `ci-setup-vyos-build.sh` rewrites `defaults.toml` `squashfs_compression_type` from `"xz -Xbcj x86 -b 256k -always-use-fragments -no-recovery"` (single-thread, x86 BCJ useless on ARM64) → `"zstd -b 1M -Xcompression-level 22"` (parallel; ~3–4× faster mksquashfs; ~5–8% size). Passes verbatim via `lb config --chroot-squashfs-compression-type` through Jinja2 template in `vyos-build/scripts/image-build/build-vyos-image` (no upstream `auto/config`; override at defaults.toml). Do NOT delete the `if grep -q '^squashfs_compression_type'` idempotency guard.
- Boot optimizations: `acpid.{service,socket,path}` masked (`99-mask-services.chroot`, ~2s). kexec units NOT masked (see S5). SysV `/etc/init.d/kexec{,-load}` removed (sysv-generator duplicates bypass systemd; old `ln -sf /dev/null` in includes.chroot broken — live-build dereferences absolute symlinks → empty files). `CONFIG_DEBUG_PREEMPT` off saves ~20s. Installed boot ~82s to login.
- `nopersistence`: REQUIRED for TFTP/USB live-boot, REMOVED for eMMC (commit `c689b96e`). eMMC path uses `vyos-union=/boot/<image>` + live-boot persistence branch (`find_persistence_media()` → `get_custom_mounts()` → `activate_custom_mounts()`) to bind-mount `/config`,`/home`,`/opt`…; w/ `nopersistence` overlay upper = tmpfs → `commit save` to RAM, reboot wipes. So `UBOOT_BOOTARGS_TAIL` has NO `nopersistence`. TFTP (`bin/dev-build.sh`) + USB (`board/scripts/boot.cmd`) KEEP it (no `vyos-union=`, no ext4 backing). ¬MANAGED_PARAMS → no kexec risk. Old `91-strip-persistence-prober.chroot` (stubbed `find_persistence_media()` → broke eMMC persistence) removed in c689b96e. Reintroducing `nopersistence` on eMMC loses `/config/config.boot` across reboots.
- Console verbosity: `loglevel=4 systemd.show_status=true` ¬`quiet` (quiet = loglevel 4 + systemd silent → ~30s dead air on serial: kernel quiet + systemd quiet + 16s `live-config.service`). Full kernel chatter: drop `loglevel=4` from `UBOOT_BOOTARGS_TAIL` in `vyos-postinstall`.
- `data/hooks/*.chroot` do NOT auto-apply: staging only. `bin/ci-setup-vyos-build.sh` MUST `cp data/hooks/<name>.chroot "$HOOKS/<name>.chroot" && chmod +x` (`$HOOKS=vyos-build/data/live-build-config/hooks/live`); live-build runs only what's physically in `hooks/live/`. Wired: 92-livescripts-defensive-mount-list, 94-vbash-vyatta-env, 95-vyos-hostname, 96-enable-services, 97-ask-modules (unconditional), 98-fancontrol, 99-mask-services. New hook = update `data/hooks/` AND `ci-setup-vyos-build.sh` SAME commit. Forgot-symptom: hook silent; `gh run view <id> --log | grep <hook-name>` empty.
- `configure` SIGABRT on ttyS0 w/o primed env: `/etc/bash_completion.d/vyatta-cfg` early-returns @line 38 unless `_OFR_CONFIGURE=ok`; op-mode `configure` sets it + `newgrp vyattacfg` → fresh vbash login; profile chain re-sources vyatta-cfg; line 126 `eval "$(my_cli_shell_api getSessionEnv $$)"` fills `VYATTA_TEMP_CONFIG_DIR`/`VYATTA_CONFIG_TMP`/`VYATTA_CHANGES_ONLY_DIR`; line 1157 `vyatta_cli_shell_api setupSession`. Reliable over SSH-PTY; sporadic on ttyS0: `setupSession` aborts `std::out_of_range` `basic_string::erase: __pos (…-1) > this->size() (0)` / "Failed to set up config session" (= `UnionfsCstore::setupSession()` `work_string.erase(find_last_of("/"))` on empty `work_root` ∵ empty getSessionEnv eval — serial-path startup race). Fix: `data/hooks/94-vbash-vyatta-env.chroot` drops `/etc/profile.d/zz-vyatta-cfg-env.sh` force-sourcing vyatta-cfg w/ `_OFR_CONFIGURE=ok` on every interactive vbash login; `configure`'s `newgrp` inherits via `$PPID` lookup in `vyatta_configure()`. Do NOT patch upstream vyatta-cfg (1200-line rebase burden; additive drop-in).
  - LOGIN RACE (fixed 2026-06-11): drop-in MUST gate on `/tmp/vyos-config-status` before sourcing. History: getty's upstream `ExecStartPre=vyos-config SERIAL` gate was cleared (`zz-ls1046a-nodevbind.conf`, hook 96) → prompt ~T+28s vs config tmpfs ~T+45s, boot commit ~T+74s; login in window → setupSession fail → vyatta-cfg ~line 1158 `builtin exit 1` KILLS sourced login shell (hostname `localhost`, soft-lock; `|| true` can't catch builtin exit). Drop-in now: bounded wait 120 s w/ visible "Waiting for VyOS boot configuration to complete..." for `/tmp/vyos-config-status` + `-w /opt/vyatta/config/tmp` guard; on timeout skip priming (¬die). HW-verified 2026-06-11.
  - GETTY GATE RESTORED bounded (2026-06-11, hook 96): `zz-ls1046a-nodevbind.conf` re-adds `ExecStartPre` waiting (bounded 180 s, `TimeoutStartSec=240`) for same marker → prompt only AFTER boot commit (~T+74s), no log-over-prompt. Upstream wait UNBOUNDED; we bound so hard vyos-router crash still yields prompt ~T+200s on serial-only board. Do NOT gate getty on `multi-user.target`: `vyos-router.service` = `Type=simple`+`RemainAfterExit=yes`, active ~80 ms in, target reached ~T+20s (pre-config); `After=multi-user.target` on a getty = ordering cycle via `Before=getty.target`. Marker file = ONLY true completion signal. Profile-side 120 s wait kept as net (router crash, SSH pre-marker, emergency). HW-verified 2026-06-11 (ISO 2026.06.11-1500-rolling): getty start T+63s waiting, multi-user T+88s (43 s pre-config → target-gating would misfire), prompt T+131s at commit completion, auto-login clean.
- "Failed to set up config session" ALT cause = permissions (distinct from env case): if `cli-shell-api getSessionEnv $$` SUCCEEDS (valid `VYATTA_TEMP_CONFIG_DIR`) but `setupSession` rc=1, no `/opt/vyatta/config/tmp/new_config_<pid>`: `/opt/vyatta/config/tmp` is `root:root` 775 ¬`root:vyattacfg` setgid. Upstream `vyos-router` (~line 588) mounts tmpfs `mode=775` (no setgid), `chgrp vyattacfg` top-level only → `tmp/`+`active/` inherit creator gid (root here) → `vyos` (group vyattacfg, ¬root) denied mkdir → unionfs-fuse `Failed to open .../changes_only_<pid>/: No such file or directory` → rc=1. Fix: hook 96 installs `ls1046a-config-perms.service` oneshot (enabled multi-user.target.wants) forcing `/opt/vyatta/config{,/tmp,/active}` group vyattacfg + setgid (2775) every boot. MUST be `After=vyos-router.service` standalone unit, NOT `ExecStartPost=` drop-in: proven racy 2026-05-30 (192.168.1.190, ISO 2026.05.30-0515): ExecStartPost fired 05:35:39 `cannot access '/opt/vyatta/config/tmp'` while tmpfs mounted 16 s later 05:35:55 (async stage completes after ExecStart returns). Separate `After=` oneshot alone STILL insufficient (proven 2026-06-11): router active ~80 ms, tmpfs ~17 s later, AND `/opt/vyatta/config/tmp` exists in squashfs UNDERLAY → existence poll passes instantly, chmods land on underlay pre-mount. Unit's ExecStart polls **`mountpoint -q /opt/vyatta/config`** (+ `-d tmp/`, up to 240 s, `TimeoutStartSec=300`) before chgrp/chmod — only reliable gate. Hook also `rm -f`s superseded `vyos-router.service.d/zz-ls1046a-config-perms.conf`. Live remediation: `sudo chgrp vyattacfg /opt/vyatta/config/tmp /opt/vyatta/config/active && sudo chmod 2775 /opt/vyatta/config/tmp /opt/vyatta/config/active`. Distinguish: getSessionEnv OK + `ls -ld /opt/vyatta/config/tmp` = `root root` → permissions case. Diagnosed 2026-05-29 (ISO 2026.05.29-2226, 6.18.33-vyos); race correction 2026-05-30.
- `CONFIG_NET_SCH_FQ=y` invariant: base arm64 defconfig `=m`; sysctl very-early pass writes `net.core.default_qdisc=fq` before `systemd-modules-load` → `-ENOENT`, silent `pfifo_fast`. Fragment `kernel/common/vyos-base/10-networking.config` forces `=y` (registered pre-sysctl). NO runtime workaround (ordering hacks, modprobe hooks) — build-time only.
- Patching architecture (authoritative: `plans/TA-2026-07-18-002-patch-architecture.md` v1.3 + `plans/patching-improvement-plan.md` v1.3, live at `9f67b56`). Three layers + CI: **Layer 1** = patch stack (103 `.patch`, 260-line series, `git apply --3way` with per-patch commits, intent-driven `# SKIP` skip-ledger). **Layer 2** = F-0xx fixup layer (17 active `bin/kernel-fixups/*.py`, all count-gated via `bin/mutate.py`; zero bare `sed -i` on kernel C remain). **Layer 3** = downstream shims (build-kernel.sh injection). **CI** = persistent git clone (`~/kernel-git-cache/linux/`, branch `vyos-6.18.38-dpaa1`, 106 commits), canonical-bootstrap.sh, weekly rot canary, mergiraf `.gitattributes`, count-gated fixup gate (`bin/test-fixups.sh`), round-trip commit-count gate in `auto-build.yml`.
- Target architecture (tree-canonical, Phase 1→2 migration in progress): persist the git repo CI constructs (branch `vyos-6.18.38-dpaa1` at `~/kernel-git-cache/linux/` bootstrapped by `bin/canonical-bootstrap.sh`), make it the source of truth, generate patches from commits, fold every fixup into its owning commit via `git commit --fixup` + `rebase -i --autosquash`. Patch files become a generated export of the branch, not hand-edited source. `bin/kernel-roundtrip.sh` provides non-destructive `verify` (export → tempdir, compare) and `export` (format-patch with `Patch-Name:` trailers). CI round-trip gate verifies commit count vs series.
- Layer 2 fixup rules (interim, until tree-canonical migration dissolves the entire REPLACEMENT block): (1) **ZERO bare `sed -i` on kernel C** — use count-gated `bin/mutate.py` with `expected=N` (hard-fail on mismatch), `expected=-1` (optional), `expected=0` (expect-none), or `once` mode. `--check` dry-run in CI. (2) Count==1 rule: assertion must be honest; every mutation counts its anchor occurrences and asserts the expected count. NF-10 (SFP rename 0 matches) and NF-11 (cast count 1→29) were caught by count-gating — each bare `sed` was a latent defect. (3) Fixups mutate the derived tree AFTER the `kernel post-patches` commit → invisible to patch stack + rot canary. Every fixup is a second writer against derived state — this is the root architectural disease. (4) Placement: INSIDE the REPLACEMENT Python triple-quoted string in `ci-setup-kernel.sh` (after `git commit "kernel post-patches"`, before compile). `cat >> build-kernel.sh` APPEND lands after compile = never effective. (5) **DELETIONS**: do NOT structurally delete individual `if ... fi` blocks — the REPLACEMENT block has accumulated if/fi imbalances from 30+ commits (`fifi` structural debt, IP-003 §10). Use no-op substitution (`:` comments) to disable zombie fixups while preserving structural balance. Durable fix: tree-canonical migration dissolves the entire block into commit history atomically. (6) Escape collisions: base64-encoded Python fixers preferred (no escapes/quotes/tabs in base64 charset). Simple single-line sed (no tabs/newlines) OK. NEVER hand-edit `.patch` hunks (corrupts line counts → cascading CI failures).
- §17 silicon-encoding tripwires (live at `9f67b56`): (1) Compile-time `static_assert` in `fman-pcd-fe-static-asserts.h` (11 guards: FE type constants, descriptor sizes, NIA encodings, ehash mask; NIA guards `#ifdef`-disabled until constants move to shared header). (2) KUnit CI-time `fman_pcd_fe_test.c` (8 cases, `CONFIG_FSL_FMAN_PCD_KUNIT_TEST=y`). (3) Arm-time `fe_verify` debugfs MURAM readback (already existed). Three tripwires before silicon make the ENQ-regression class structurally impossible.
- Patch-type policy (from TA §6.4): Tier A (~70 patches, new files/subsystems) near-zero rebase risk. Tier B (~5, static-demotions/exports) convert to Coccinelle semantic patches. Tier C (~35, edits to `dpaa_eth.c`/`fman_port.c`/`fman_keygen.c`) human review required at every kernel bump. Series metadata: `Risk-Tier` + `Upstream-Status` in series comments only (NOT inside `.patch` files — breaks `git apply`). In commit trailers: `Patch-Name:` for stable identity across round-trips.
- Binding P-rules (from IP-003): P1 = zero bare `sed -i` on kernel C (enforced by count-gating). P6 = metadata lives in commit trailers. P7 = name stability via `Patch-Name:` trailer. P8 = patches generated only from trees fully described by the stack (NF-02 root cause: patches 0159–0162 were generated from fixup-mutated tree, stack-incompatible from birth).

## S10. Boot Diagnostics (ignore / act table)

| Message | Verdict |
|---|---|
| `smp_processor_id() in preemptible code: python3` | Suppressed via `# CONFIG_DEBUG_PREEMPT is not set`; cosmetic on older builds (PREEMPT_DYNAMIC, A72) |
| `could not generate DUID ... failed!` | Expected live-boot w/o persistence (no stable machine-id) |
| `WARNING failed to get smmu node: FDT_ERR_NOTFOUND` | DTB lacks SMMU/IOMMU. Harmless |
| `PCIe: no link` / `disabled` | No PCIe devices. Normal |
| `bridge: filtering via arp/ip/ip6tables is no longer available` | `br_netfilter` loads on demand |
| `nfct v1.4.7: netlink error: Invalid argument` | TFTP dev-boot conntrack helper; cosmetic, first boot only |
| `binfmt_misc.mount` FAILED | Expected on ARM64 target |
| `mount: /live/persistence/ failed: No such device` | eMMC (post c689b96e): prober runs as intended; per-candidate fails (`mmcblk0boot0/1`, `mmcblk0p1`…) normal, only `mmcblk0p3` succeeds. TFTP/USB: `nopersistence` skips prober, msg absent |
| `/init: line 1365: can't open /tmp/custom_mounts.list` | eMMC current: NOT expected (list populated). Seen → U-Boot env still has `nopersistence` (re-run `vyos-postinstall`) OR initrd built w/o hook 92 (`gh run view <id> --log \| grep 92-livescripts-defensive-mount-list`). TFTP/USB: hook 92 pre-creates empty list → no-op |
| `Error -ENOENT ... 'net.core.default_qdisc=fq'` | `CONFIG_NET_SCH_FQ` ended `=m`. Verify `10-networking.config` has `=y` post `stage-kernel.sh --flavor <flavor>` AND `ci-setup-kernel.sh` seds `13-net-sched.config` to `=y` (vyos-build merge runs after ours, else overrides back) |
| `sfp-xfi0: deferred probe pending` | SFP waits for PHY driver; resolves post-boot |
| `can't get pinctrl, bus recovery not supported` | I2C pinctrl absent in DTB. Harmless |

## S11. Files

| File | Purpose |
|---|---|
| `.github/workflows/self-hosted-build.yml` | CI entry (`workflow_dispatch` only): VM up → calls auto-build.yml reusable → deallocate |
| `.github/workflows/auto-build.yml` | Reusable (`workflow_call` only, NO dispatch): all build logic (kernel cfg, patches, packages, ISO, publish) |
| `README.md` / `INSTALL.md` / `PORTING.md` / `UBOOT.md` | Overview / 11-step install / driver+DPAA1 deep analysis / U-Boot console ref (memory map, clock tree, MTD) |
| `captured_boot.md` / `CHANGELOG.md` | USB-live boot log (2026.03.21-0419) / manual changelog (¬CI-overwritten) |
| `board/vyos-config/config.boot.default` (+`.dhcp`) | Baked default config (NO comments in blocks) / DHCP variant |
| `board/dtb/mono-gw.dtb` / `mono-gateway-dk.dts` | Prebuilt DTB (from live OpenWrt, 94KB) / DTS source (aliases + SFP nodes, compiled in kernel build) |
| `board/scripts/vyos-postinstall` | Phase 1 `fw_setenv` vyos/usb_vyos/bootcmd (forced on install via `--root`; idempotent on boot); Phase 2 `/boot/vyos.env` sync each boot |
| `board/scripts/fw_env.config` | fw_printenv/setenv config (`/dev/mtd2`); first install only |
| `board/scripts/fan-pid` | Multi-zone PID fan ctrl (Py3 stdlib): per-zone PI + max-policy + EMA + deadband + force-MAX; boot startup whistle (`play_startup_whistle()`, 6× pulses) absorbed from legacy boot-complete-notify → ONE writer of pwm1. → `/usr/local/bin/fan-pid`. Details S5 |
| `board/scripts/led.py` | LP5812 RGBW LED ctl (i2c-15 @0x6c), flag-free CLI → `/usr/local/bin/led`. Forms: palette idx (`led 17`); `R G B W`; `R G B` (W=0); 8-hex `RRGGBBWW` (`led '#33003300'`); 6-hex (W=0; bare 6-decimal = palette; `#` forces hex); `off`; no-args prints `R G B W  #RRGGBBWW`. Disambiguation: 8-hex > 6-hex (`#` or non-decimal digit) > palette. Linear PWM fade; constants in-file: `FADE_MS = 200`, `FADE_FPS = 50`, `PALETTE` = 32× RRGGBBWW. No config file/state/knobs (edit+reinstall to retune). Enforces `trigger=none` pre-write. Spec `plans/LED-DAEMON.md` Part 1 |
| `board/systemd/fan-pid.service` / `.tmpfiles` | `Type=simple`, `Restart=on-failure`, `Conflicts=fancontrol.service`, sandboxed (`ProtectSystem=strict`, `ReadWritePaths=/sys/class/hwmon`, `PrivateNetwork=yes`) / tmpfiles wants-symlink (squashfs idiom) |
| `board/scripts/fan-check` | Thermal+fan reporter: 5 zones [COOL/WARM/HOT/CRIT], PWM+RPM, daemon health, fancontrol-conflict detect. Exit 0/¬0 (monit-able) → `/usr/local/bin/fan-check` |
| `board/scripts/caam-check` | CAAM SEC 5.4 status: DT `/proc/device-tree/soc/crypto@*`; drivers (`caam`,`caam_jr` mandatory; `caamalg/caamhash/caamrng/caampkc` optional); JR count `/sys/bus/platform/drivers/caam_jr/*`; dmesg; `/proc/crypto` caam algos; hwrng (`rng_current`=`caam-rng`, 16B root read); §7 CDX↔SEC FQ wiring (self-skips until ASK engaged; documents `cdx_module_init::start_dpa_app failed rc 11` / `locate eth bman pool` cascade). Exit 0/¬0 → `/usr/local/bin/caam-check` (unconditional) |
| `board/scripts/xsk-zc-check` | AF_XDP true-ZC RX gate reader: 20 `xsk_*` ethtool counters on eth3/eth4; gates `xsk_zc_eligible`(0093)/`xsk_zc_rx_armed`(0094)/`xsk_fill_guard_block`(0095)/`xsk_zc_rx_recovered`(0096); verdicts dormant (all 0, expected shipping) / ZC-armed (armed ∧ fill_guard==0 → precond (1)+(2) MET) / fault (fill_guard>0 ∨ attach-DMA error → reprogram WRITE stays disabled). Spec §6.1.12/§6.1.13. Exit 0/1/2 → `/usr/local/bin/xsk-zc-check` (unconditional) |
| `board/scripts/firmware-check` | Firmware/microcode inventory: DT model + fsl-guts SVR/rev; U-Boot version (`/chosen/u-boot,version`) vs QSPI `uboot` partition string; `/proc/mtd` map + fingerprints (RCW/PBL mtd0, env CRC mtd2, QEF header mtd3, recovery-DTB FDT mtd4, gzip recovery kernel mtd6); deep QEF decode of DT-injected FMan ucode (id, length, layout ver, split-IRAM, SoC code, md5, proprietary-210.x vs open-106.x; "for LS1043 r1.0" label on 210.10.1 = cosmetic, CORRECT here) vs mtd3 copy vs kernel `FMan PCD caps` line; boot env vars + targets (vyos/usb_vyos/recovery/dev_boot*); `/boot/vyos.env` (via `/usr/lib/live/mount/persistence/boot/vyos.env`) vs running `vyos-union=`. sudo for flash reads (unpriv skips). Exit 0/1/2 → `/usr/local/bin/firmware-check` (unconditional) |
| `board/scripts/pcd-snapshot` | ASK2 reversible-switch GATE (Py3 stdlib): capture+diff FMan1 PCD state the S0↔S1 switch mutates (`plans/DUAL-DATAPLANE.md` M1). Reads `/dev/mem` (FMAN_BASE `0x1A00000`, BE u32): (1) KG schemes 0–31 via AR (`KG+0x1FC` GO\|READ poll, 24 words from `KG+0x100`) RSS vs AC_CC; (2) per-port BMI bind (`fmbm_rfpne` 0x28 / `rccb` 0x34 / params-page-ptr, window 0x00–0x7C) 6×1G+2×10G RX; (3) CC-tree/FM_CTL params MURAM `0x5AC00` ×0x80 words; (4) `/sys/kernel/debug/fman_pcd/0/muram_budget` (`used` MUST return to baseline post S1→S0; else PR14z21 327×-ENOMEM leak). Subcmds `capture [-o FILE] [--show]`, `diff [ref]`, `show`. Diff-excluded volatiles: KG global `tpc` (0x28), per-scheme `kgse_spc` (word 16), MURAM `high_water` (noted ¬failed); rest byte-exact. Exit 0/1/2. Board-validated clean S0 2026-06-15 (6.18.34-vyos): schemes 0–4 RSS, all RX `rfpne=0x00480000 rccb=0`, `used=0`. → `/usr/local/bin/pcd-snapshot` (unconditional). **Mutate eth3 only — never eth0 (SSH lifeline)** |
| `data/reftree.cache` | vyos-1x build artifact missing upstream; copy manually |
| `data/vyos-1x-*.patch` | 24 vyos-1x patches (console, vyshim timeout, podman, install gap, eMMC default, U-Boot live-detect, VPP platform-bus, vyos.env boot, MOTD, hide live disk, migration 31→32, HW-VLAN strip, ingress-policer, system-offload classify (026 — CLI deprecated 2026-07-19, mechanism kept as silent default), port order, add_image U-Boot env (028), …) |
| `data/vyos-build-*.patch` | 2 vyos-build patches (vim link, no sbsign) |
| `board/mok/MOK.pem` / `data/vyos-ls1046a.minisign.pub` | Secure-Boot cert / ISO sig pubkey |
| `version.json` | Canonical update feed (CI-managed); `version-{default,ask,vpp}.json` identical aliases |
| `bin/dev-build.sh` | Cobalt 100 dev loop: `kernel`/`dtb`/`extract`/`iso-live` + rsync → LXC 200:/srv/tftp/; reuses `ci-stage-kernel.sh` + `ci-compile-mono-dtb.sh` verbatim |
| `bin/local-build.sh` | Full ISO orchestrator mirroring CI (+ `ask-mod` OOT mode); LXC 200 now TFTP/HTTP relay only |
| `plans/VPP.md` / `plans/DEV-LOOP.md` | VPP native integration (AF_XDP eth3/eth4, thermal, PMD roadmap) / dev-loop architecture |
| `specs/ask2-rewrite-spec.md` / `plans/DUAL-DATAPLANE.md` | ASK2 spec v1.7 (authoritative) / dataplane state machine (S0↔S1, S2 overlay), reversibility contract, M0–M8 |
| `kernel/flavors/ask/README.md` | Scaffold pointer (ASK 1.x artifacts deleted on ask20) |
| `arch/fman-microcode-210-programming-reference.md` | Complete 210.10.1 programming reference (registers, FE types, opcodes, ceilings, invariants); supersedes scattered facts in fman-pcd.md / fman-fe-ehash.md / keygen spec / afxdp spec; §12 = 3 unknowns + silicon methodology |
| `plans/NETWORKING-DEEP-DIVE.md` | FMan/QBMan/portal/driver-split deep dive |
| `board/scripts/fman-port-name`, `10-fman-port-order.rules`, `00-fman.link` | Legacy rename layer (see S5 port-order rule; deleted from image 2026-05-15, sources retained) |
| `board/scripts/10-emc2305-fan-pid.rules` | Udev: start `fan-pid.service` on `ACTION=="bind", SUBSYSTEM=="i2c", DRIVER=="emc2305"` (driver name = stable invariant). Defends multi-user vs i2c-probe race (2026-05-11: unit `ConditionPathExistsGlob=/sys/bus/i2c/drivers/emc2305/*-002e` dropped silently pre-bind; systemd never re-evaluates Condition*) |
| `bin/ci-setup-kernel.sh` | Kernel cfg overrides, patch staging, build-kernel.sh injection (REPLACEMENT block with count-gated fixups + §17 tripwires) |
| `bin/mutate.py` | Count-gated mutation helper: `expected=N` hard-fail, `expected=-1` optional, `expected=0` expect-none, `--check` dry-run, `once` mode. ZERO bare `sed -i` on kernel C enforced by CI gate |
| `bin/kernel-fixups/` | 17 active fixup `.py` scripts (Layer 2); each count-gated; disposition tracked in manifest. Phase 2 target: fold into commits, delete |
| `bin/kernel-roundtrip.sh` | Non-destructive patch round-trip: `verify` exports to tempdir + compares; `export` uses Patch-Name trailers to format-patch. CI round-trip commit-count gate in auto-build.yml |
| `bin/canonical-bootstrap.sh` | One-time bootstrap of `vyos-6.18.38-dpaa1` branch (106 commits) from git clone → apply series. Uses persistent `~/kernel-git-cache/linux/` |
| `bin/clone-kernel.sh` | Persistent shallow git clone at `~/kernel-git-cache/linux/` surviving `actions/checkout` cleanup; symlink into vyos-build package dir |
| `bin/test-fixups.sh` | CI gate: 4 checks — fixup execution, count-assertion, manifest accuracy, no-zombie. Must pass before kernel build |
| `kernel/common/files/fman-pcd-fe-static-asserts.h` | §17 Tripwire 1: 11 compile-time `static_assert` guards (FE types, sizes, NIA encodings, ehash mask). Injected by F-089 |
| `kernel/common/files/fman_pcd_fe_test.c` | §17 Tripwire 2: 8 KUnit test cases (`CONFIG_FSL_FMAN_PCD_KUNIT_TEST=y`) for descriptor encodings. Injected by F-089 |
| `.github/workflows/patch-rot-check.yml` | Weekly canary (Mon 06:00 UTC): `git apply --3way --check` on series; `::warning` per drifted patch + end-of-run counter summary |
| `plans/TA-2026-07-18-002-patch-architecture.md` | Patch architecture v1.3 — 3-layer analysis, tool evaluation, tree-canonical migration plan, risk-tier taxonomy, §17 tripwire architecture |
| `plans/patching-improvement-plan.md` | IP-003 v1.3 — Phase 0/R/2a scorecard, NF-01–NF-11 findings, count-gating validation, REPLACEMENT block structural debt, binding P-rules |
| `bin/ci-setup-vyos1x.sh` / `bin/ci-setup-vyos-build.sh` | vyos-1x patches + reftree / vyos-build patches + live-build ARM64 |
| `bin/ci-build-packages.sh` / `bin/ci-build-accel-ppp.sh` / `bin/ci-build-iso.sh` | Kernel+vyos-1x builds / accel-ppp-ng fallback (S5) / ISO + isohybrid (FAT32+MBR append) |
| `data/kernel-config/` | Config fragments (ls1046a-board, dpaa1, fmd-shim, i2c-gpio, leds, sfp, usb, watchdog); `ls1046a-leds.config`: `NEW_LEDS, LEDS_CLASS, LEDS_CLASS_MULTICOLOR, LEDS_GPIO, LEDS_LP5812, LEDS_TRIGGERS, LEDS_TRIGGER_NETDEV` |
| `data/kernel-patches/patch-dpaa-xdp-queue-index.py` | fqid→0 in `xdp_rxq_info_reg()` (S5); injected by ci-setup-kernel.sh |
| `kernel/common/files/fsl_fmd_shim.c` | `/dev/fm0*` chardev skeleton (GET_API_VERSION only, dormant); injected |
| `kernel/common/files/lp5812/` | TI LP5812 OOT LED driver (`leds-lp5812.{c,h}`) → `drivers/leds/lp5812/`; injected |
| `data/hooks/92-…mount-list.chroot` | Patches `activate_custom_mounts()` (`/lib/live/boot/9990-misc-helpers.sh`) to pre-create empty list (TFTP/USB `nopersistence` paths); idempotent; replaces deleted 91 hook; ¬touches `find_persistence_media()` |
| `data/hooks/98-fancontrol.chroot` | Installs `libatomic1`; masks `fancontrol.service`; ¬installs fancontrol pkg |
| `data/hooks/99-mask-services.chroot` | Masks acpid; removes SysV kexec scripts |

## S12. Commands

```bash
# === CI (production releases) ===
# Only ONE dispatchable workflow exists. `auto-build.yml` is reusable-only
# (no workflow_dispatch trigger) and cannot be launched standalone.
gh workflow run "VyOS LS1046A build (self-hosted)" --ref main

# Check build status
gh run list --limit 3

# Push triggers nothing — workflow_dispatch only
git push  # then manually trigger build

# === Local dev loop (fast iteration) ===
# The agent runs on Cobalt 100 (arm64-runner) and ALSO builds here — native
# aarch64, 32 cores. LXC 200 (192.168.1.137) is now just the TFTP/HTTP relay
# the board's U-Boot fetches from. `bin/dev-build.sh` rsyncs artefacts to
# admin@192.168.1.137:/srv/tftp/ via the `lxc200` SSH MCP entry over the
# Tailscale 192.168.0.0/16 subnet route.
bin/dev-build.sh kernel    # ~30 s incremental / ~2–3 min full + rsync
bin/dev-build.sh dtb       # ~10 s
bin/dev-build.sh iso-live  # extract+push full live-boot artefacts

# Trigger the board reboot from anywhere:
ssh vyos sudo reboot       # board picks up new TFTP artefacts on next dev_boot

# Serial console (PuTTY 115200 8N1) only needed for U-Boot env edits / recovery.

# === Patch architecture (tree-canonical) ===
# Bootstrap the canonical branch (one-time; CI already has this)
bin/canonical-bootstrap.sh

# Non-destructive round-trip check (verify exported patches match in-tree)
bin/kernel-roundtrip.sh verify

# Export patches from canonical branch (format-patch with Patch-Name trailers)
bin/kernel-roundtrip.sh export

# Count-gated mutation dry-run (verify a fixup without applying it)
bin/mutate.py --check --expected=1 'pattern' 'replacement' path/to/file.c
```