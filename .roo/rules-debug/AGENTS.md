# Debug Mode Rules (Non-Obvious Only)

- **kexec double-boot:** VyOS live ISO boots twice — first boot loads kexec service, shuts down, reboots into same kernel. Interface names may change (eth0→e2) on second boot if `net.ifnames=0` is lost. Check dmesg for `renamed from eth` lines.
- **FMan uses `dev_dbg()`:** Successful FMan probe produces ZERO dmesg output. Enable dynamic debug: `echo "file fman.c +p" > /sys/kernel/debug/dynamic_debug/control`
- **Deferred probes are silent:** Check `/sys/kernel/debug/devices_deferred` for "missing pcs" — means `CONFIG_FSL_XGMAC_MDIO` is disabled
- **`smp_processor_id()` BUG spam:** Suppressed via `# CONFIG_DEBUG_PREEMPT is not set` in defconfig. If seen on older builds: cosmetic only, PREEMPT_DYNAMIC on Cortex-A72. Ignore.
- **`could not generate DUID` failure:** Expected on live boot without persistence — no stable machine-id for DHCPv6
- **Serial console is `ttyS0` at 115200:** Not `ttyAMA0`. Earlycon: `earlycon=uart8250,mmio,0x21c0500`
- **No PCIe, no SMMU:** "PCIe: no link" and "failed to get smmu node" are normal for this board — no PCIe devices exist, DTB lacks SMMU nodes
- **OpenWrt SSH:** `root@192.168.1.234` (not 192.168.1.1). Default `root` with no password
- **U-Boot environment:** Stored in SPI flash `mtd3`. Read from Linux with `fw_printenv` (needs `/etc/fw_env.config` pointing at mtd3)
- **eMMC debug:** `dmesg | grep -iE 'mmc|esdhc|mmcblk'` — `mmcblk0` must appear. If not, `CONFIG_MMC_SDHCI_OF_ESDHC=y` is missing
- **Network debug sequence:** `ls /sys/bus/platform/drivers/fsl-fman/` then `ls /sys/bus/platform/drivers/dpaa-ethernet/` then `ip link show`
