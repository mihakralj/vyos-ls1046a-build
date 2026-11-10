# Board boot diagnostics

This file is the triage table for common boot and runtime noise. Use it to avoid false rabbit holes.

## Ignore

- `smp_processor_id() in preemptible code: python3` — suppressed by the kernel config; usually cosmetic.
- `could not generate DUID ... failed!` — expected on a no-persistence live boot.
- `WARNING failed to get smmu node: FDT_ERR_NOTFOUND` — harmless when the DTB lacks SMMU.
- `PCIe: no link` / `disabled` — normal when no PCIe devices are present.
- `bridge: filtering via arp/ip/ip6tables is no longer available` — `br_netfilter` loads on demand.
- `nfct v1.4.7: netlink error: Invalid argument` — common first-boot TFTP noise.
- `binfmt_misc.mount` FAILED — expected on ARM64 targets.
- `can't get pinctrl, bus recovery not supported` — harmless if the DTB lacks pinctrl support.
- `sfp-xfi0: deferred probe pending` — normal while the SFP PHY is still coming up.

## Act on it

- `mount: /live/persistence/ failed: No such device` — on eMMC this is usually part of the expected persistence probe sequence; the success path is the `mmcblk0p3` mount.
- `/init: line 1365: can't open /tmp/custom_mounts.list` — usually means the live boot is still carrying stale `nopersistence` state; re-run the board boot script and check the bootargs.
- `Error -ENOENT ... 'net.core.default_qdisc=fq'` — build-time config error: `CONFIG_NET_SCH_FQ` was not set to `=y`.
- `fsl-fman-port ... incorrect qman-channel-id` — likely a non-fatal OH port issue, not a failure of the active MAC interfaces.
- `fsl_dpaa_fman` or `fsl_dpa` missing interfaces after boot — check the `CONFIG_*` values and ensure the DPAA stack is built-in.

## Driver and board-specific notes

- `eth0` is the SSH lifeline; treat any dataplane experiment as a hazard to the management path.
- `eth3` and `eth4` are the expected DPAA1 offload ports for testing.
- A warm reboot is not a clean silicon reset for BMI or MURAM; cold-boot before hardware experiments.
- Use the live board and the debug tree to verify any claim before changing code.
