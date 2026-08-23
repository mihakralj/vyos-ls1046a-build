# How-to: Update firmware of the Mono Gateway Development Kit

The primary source of truth for the Mono firmware is the [Mono gateway development kit - flashing-firmware](https://docs.mono.si/gateway-development-kit/flashing-firmware). This documentation builds on this foundation, and addresses the quirks.

\*\*\* WARNING: ONLY UPDATE THE FIRMWARE ON ONE STORAGE DEVICE AT A TIME \*\*\*

\*\*\* WARNING: NEVER UPDATE THE DEVICE YOU USED TO BOOT \*\*\*

>**WARNING:** Failure to follow this guidance may result in the soft *'bricking'* of your device. Recovery from a *'bricked'* state requires either: additional hardware like a JTAG programmer, OR returning the device to Mono to rebuild. To avoid a bad outcome - **follow these two rules!**

---

# 1. Overview

Updating the firmware of your Mono Gateway Development Kit is ***not*** essential.

However, updating your firmware provides the significant benefits of Mono's continued firmware development. This includes: useful helper tools, functionality, bug-fixes, polish, and suppresses the verbose `INFO` logging seen at boot on the shipped firmware. For highlights, see: [§1.1](#11-Major-firmware-changes) 

The majority of the Mono firmware code is available and may be explored in the associated [meta-mono](https://github.com/we-are-mono/meta-mono/tree/master) repo, but excludes the (licensed NXP-proprietary) microcode injected at build-time. Final Mono firmware releases are available separately at [firmware.mono.si](https://firmware.mono.si/), see: [§2.4.4](#244-offline-update-requirements)

## 1.1 Major firmware changes

For the full detailed changelog, see: [we-are-mono/CHANGELOG.md](https://github.com/we-are-mono/meta-mono/blob/master/CHANGELOG.md). 

#### **Highlights include:**

- Addition of the `firmware` update validator & helper - [2026-03-30](https://github.com/we-are-mono/meta-mono/blob/master/CHANGELOG.md#2026-03-30--add-firmware-management-tool-and-calver-versioning)

	- Detects boot medium - [2026-04-11](https://github.com/we-are-mono/meta-mono/blob/master/CHANGELOG.md#2026-04-11--add-boot-medium-detection-and-simplify-firmware-update-tool)

	- Adds --usb PATH & auto RO mount - [2026-04-18](https://github.com/we-are-mono/meta-mono/blob/master/CHANGELOG.md#2026-04-18--add-usb-firmware-update-path)

	- Adds --preserve-env option to retain U-boot env args - [2026-04-18](https://github.com/we-are-mono/meta-mono/blob/master/CHANGELOG.md#2026-04-18--default-firmware-update-to-full-rewrite-add---preserve-env)

	- Adds --url arg for local HTTP/TFTP firmware staging - [2026-06-12](https://github.com/we-are-mono/meta-mono/blob/master/CHANGELOG.md#2026-04-18--add-usb-firmware-update-path)

- Reversion of the cosmetic `udev` port remapping - [2026-03-28](https://github.com/we-are-mono/meta-mono/blob/master/CHANGELOG.md#2026-03-28--remove-fman-ethernet-alias-ordering-patch-and-dt-aliases) - see also [HARDWARE.md](HARDWARE.md#3-network-port-layout)

- Enables Power-off/halt via GPIO functionality - [2026-04-15](https://github.com/we-are-mono/meta-mono/blob/master/CHANGELOG.md#2026-04-15--add-usb-storage-support-gpio-poweroff-and-fix-regressions)

- Adds: [Firmware signing](https://github.com/we-are-mono/meta-mono/blob/master/CHANGELOG.md#2026-03-30--add-optional-ecdsa-p-256-signing-for-firmware-images); [USB mass storage](https://github.com/we-are-mono/meta-mono/blob/master/CHANGELOG.md#2026-04-15--add-usb-storage-support-gpio-poweroff-and-fix-regressions); [IPv6 DNS](https://github.com/we-are-mono/meta-mono/blob/master/CHANGELOG.md#2026-06-13--security-review-follow-ups-supply-chain-pinning-cve-scanning); [vim-tiny + coloured PS1](https://github.com/we-are-mono/meta-mono/blob/master/CHANGELOG.md#2026-06-13--recovery-ux-polish-colored-prompt-vim-tiny-firmware-tool-output)

- Fixes: [Fix eMMC recovery](https://github.com/we-are-mono/meta-mono/blob/master/CHANGELOG.md#2026-03-28--split-u-boot-environment-for-qspi-and-emmc-boot-media); [Fix LED](https://github.com/we-are-mono/meta-mono/blob/master/CHANGELOG.md#2026-02-06--fix-package-name-collision-for-debapt-backends-5); [Fix FAT32 USB](https://github.com/we-are-mono/meta-mono/blob/master/CHANGELOG.md#2026-04-18--add-usb-firmware-update-path); [Fix RTC](https://github.com/we-are-mono/meta-mono/blob/master/CHANGELOG.md#2026-04-11--harden-firmware-security-fix-rtc-improve-build-quality)

---

# 2. Requirements

**Before updating the firmware – read this chapter carefully.**

This chapter covers what you will ***need***, and what you ***need to know*.**

The ***next*** chapter provides a walk-through of the update process itself.

## 2.1 Physical preparation

All methods to perform firmware updates will require access to the small dip-switch on the main PCB. To access this, first remove the 4x T10 Torx screws and gently remove the clear plastic lid. For a visual guide see: [Mono's disassembly instructions](https://docs.mono.si/gateway-development-kit/hardware-description#disassembly-instructions)

## 2.2 Firmware release files

The firmware files available in each release from [firmware.mono.si](https://firmware.mono.si/) are listed below, along with their intended usage.

>**NOTE:** Depending on how you intend to update your firmware (see: [§2.4](#24-firmware-update-requirements)), you may never interact with these files directly.

```bash
firmware-emmc-gateway-dk.bin		# 'eMMC' eSDHC firmware
firmware-emmc-gateway-dk.bin.sig	# Sig used to validate 'eMMC' firmware
firmware-qspi-gateway-dk.bin		# 'NOR' qspi flash firmware
firmware-qspi-gateway-dk.bin.sig	# Sig used to validate 'NOR' firmware
firmware-signing.pub				# Mono public signing key used in validation
uboot-emmc-gateway-dk.env			# Default 'eMMC' U-Boot environment variables
uboot-qspi-gateway-dk.env			# Default 'NOR' U-Boot environment variables
```

>**NOTE:** Firmware is now signed using the Mono ECDSA P-256 private key, and validated against the provided public key during the integrity-check of the `*.bin` firmware images.

### 2.2.1 Firmware structure

Firmware is not monolithic, and it is helpful to understanding both what is 'in' firmware, and how it is practically used. Taking the NOR firmware as an example, this has the below structure, where RO = Read-only, and RW = Read/write.

| Block | Offset (MB) | Offset(hex) | Component                            | RO/RW |
| ----- | ----------- | ----------- | ------------------------------------ | ----- |
| mtd0  | 0           | 0x000000    | Reset codeword (RCW)+ BL2 bootloader | RO    |
| mtd1  | 1           | 0x000000    | U-boot binary                        | RO    |
| mtd2  | 3           | 0x000000    | U-boot-env - environment variables   | RW    |
| mtd3  | 4           | 0x000000    | Fman microcode (v210.10.1)           | RO    |
| mtd4  | 5           | 0x000000    | Device tree (Recovery)               | RO    |
| mtd5  | 6           | 0x000000    | Backup/reserved                      | RO    |
| mtd6  | 10          | 0x000000    | Kernel+initramfs (Recovery)          | RO    |

**Succinctly:** 
- mtd0-2: Configures, initialises and boots the hardware
- mtd3: Unlocks the HW-offloading capabilities of the LS1046A SoC, see: [HW-OFFLOADING.md](HW-OFFLOADING.md)
- mtd4-6: Provides the 'Recovery Linux' ramfs environment

### 2.2.2 Board serial number

Serial numbers for Mono Gateway Development Kits units of the form `MT-R01A-0326-0XXXX` where `XXXX` indicates position in the initial 1000 units.

The serial number can be pulled from EEPROM using the following python snippet:

```python
sudo python3 - <<"EOF"
import os, fcntl
fd = os.open("/dev/i2c-3", os.O_RDWR)
fcntl.ioctl(fd, 0x0706, 0x50)
os.write(fd, bytes([0x00, 0x28]))
print(os.read(fd, 64).split(b"\x00")[0].decode())
EOF
```

Other methods for obtaining this info can be found in the official Mono docs, see: [getting started](https://docs.mono.si/gateway-development-kit/getting-started#reading-serial-number-and-mac-addresses-from-eeprom)

## 2.3 (All methods) Common requirements

**Have:**
- 1x USB-C cable (for serial console)
- 1x ethernet cable (to connect to an existing network / router / ISP gateway)
- (If required) Backed-up U-boot environment variables, see - [§2.4.1](#241-u-boot-environment-variables)

**Know:**
- How to get into the 'Recovery Linux' environment, see: [§2.3.1](#231-recovery-linux-101) or [getting started](https://docs.mono.si/gateway-development-kit/getting-started#first-boot)).
- How to use the serial console, see: [§2.3.2](#232-serial-console-101) or [getting started](https://docs.mono.si/gateway-development-kit/getting-started#first-boot)).
- Which port/interface you plugged the ethernet cable into - see [HARDWARE.md](HARDWARE.md#31-as-shipped-with-cosmetic-correction-applied)
- How the boot process works, see: [HARDWARE.md](HARDWARE.md#2-boot-chain)
- The private IP range used by your local connected network (e.g. IPv4 192.168.0.0/24, or 10.0.0.0/24 etc)
- The IP of your upstream router / modem (e.g. 192.168.0.1/24, or 10.0.0.1/24, etc)
- Your DNS server IP (only if different to your existing router e.g. a PiHole or simila, see - [§2.3.3](#233-custom-dns-101))
- How to debug network issues from the Linux CLI with `ip`, `ping` & `nslookup`

### 2.3.1 'Recovery Linux' 101

This is the small read-only linux environment that exists to enable firmware updates, and device recovery. It is part of the firmware image, as shown in [§2.2.1](#221-firmware-structure)

**To access the 'Recovery Linux':**

1) Power on
2) Wait until prompted
3) Press any key to interrupt boot
4) Type `run recovery` + press `Enter` 
5) Login as `root` (no password)

### 2.3.2 Serial console 101

On Windows, use [Putty](https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html): 
Select connection type `serial`, speed `11520`, and serial line `COM1`. NB: If in doubt, check Windows Device Manager for the correct COM# port number.

On Linux, open a terminal and use `tio`:
```bash
ls /dev/ttyUSB*				# Find the right ttyUSB device, usually /dev/ttyUSB0

tio /dev/ttyUSB0			# Opens the required serial port 
```

### 2.3.3 Custom DNS 101

If you have a custom DNS setup, e.g. using a PiHole at `192.168.1.254`, ensure you define this DNS server in `/etc/resolv.conf`. 

Additional nameservers should follow the format of any existing default entries
```bash
cat /etc/resolv.conf	# Check the format used in your firmware version

echo 'nameserver 192.168.1.254 pihole' >> /etc/resolv.conf		# Adds pihole DNS

nslookup google.com		# Check DNS resolution works
```

## 2.4 Firmware update requirements

There are four\* main methods to update the firmware of the Mono Gateway Development Kit:

This section will cover pre-requisites and requirements for each method. 

>**NOTE:** If performing the update for the first time you ***MUST*** use the 'Legacy' method. 

1) ***'Legacy'*** – (1st time) Manual updating via standard Linux CLI tools - `ip`, `curl`, `dd`, `flashcp`, see: [§2.4.2](#242-legacy-update-requirements)

2) ***'Normal'*** – (Recommended) Using the new `firmware` helper, and the latest [firmware.mono.si](https://firmware.mono.si/)  firmware, see: [§2.4.3](#243-normal-update-requirements)

3) ***'Offline'*** – (Advanced) Using the new `firmware` helper, with locally staged firmware - e.g. via USB, TFTP, local HTTP server, see: [§2.4.4](#244-offline-update-requirements)

4) ***'Mono Imager'*** – See: [mono-imager](https://github.com/HAHermsen/mono-imager)

>\***NOTE:** Semi-hosting via JTAG debugger provides a further option of last resort which is also used to enable the recovery of **'bricked'** (unbootable) devices. This is out of scope for this guide. If required, see other community resources: e.g. [moshevds/mono-gateway-uart-recovery](https://github.com/moshevds/mono-gateway-uart-recovery/tree/main) or seek help via the [Mono Discord](https://discord.com/invite/FGHJ3J5v5W). 

### 2.4.1 U-Boot environment variables

These are located within the firmware at the 3 MB offset (see [§2.2.1](#221-firmware-structure)), and direct what OS U-Boot boots into after the hardware is initialised and initial boot self-tests have completed.

>**WARNING: If you have installed an OS (OPNsense, OpenWRT, VyOS) that you want to keep, ensure you have backed-up your U-boot environment variables before proceeding. By default, the firmware update process will reset these to default. This may leave you unable to boot into a previous installed OS**

>NOTE: If you are updating firmware, AND intend to re-install your OS, OR install a new one, you do not need to save these variables.

There are two methods to save/retain the U-boot environment variables:

#### A)  Manually copy your current variables to a text editor 

>**NOTE:** If updating firmware for the 1st time and/or using the 'legacy' method, this is your only option

1) Power on
2) Interrupt boot to drop into the U-boot prompt
3) Enter `pri` + hit return
4) Select and copy the output to a text editor
5) Complete the firmware update process
6) Return to U-boot prompt, type `setenv` + return
7) Paste the variables form your text editor
8) Save by typing `saveenv` + return

#### B) Use the --preserve-env flag with the `firmware` helper using the 'Normal' method

```bash
firmware update --preserve-env			# Update firmware; retain U-Boot env vars
```

### 2.4.2 *'Legacy'* update requirements

>**NOTE:** **If performing the update for the first time - this is your only manual option**

The 'legacy' method requires some awareness of some common linux CLI tools, as outlined below:
```bash
ip link set up <device>									# Brings up link
ip link set up eth1										# e.g. for eth1

ip address add <ip address/CIDR> dev <device>			# Sets IP address
ip address add 10.0.0.200/24 dev eth1					# e.g. IP for eth1

ip route add default via <router IP> dev <device>		# Sets default route
ip route add default via 10.0.0.1 dev eth1				# e.g. route via eth1

ping 8.8.8.8											# Test ping internet
nslookup google.com										# Tests DNS lookup

curl -u <user>:<password> -O <file location>			# Downloads file as <user>
dd if=<image file> of=<device> bs=4096 skip=1 seek=1	# Writes image to device
```

You may note the `dd` command passes several additional arguments. These can be understood more clearly when read alongside the firmware structure shown in §2.2.1. 

```bash
...bs=4096 							# Write in 4kB chunks
...skip=1  							# Skip reading the first N input blocks
...seek=1 							# Skip writing the first N output blocks
```
In short, the `dd` command skips reading and writing to the first offset position. This is because the *'Reset codeword (RCW)+ BL2 bootloader'* located at the mtd0 / 0MB offset remains unchanged between firmware versions. The usable RCW configuration is effectively fixed by the implemented PCB design.

### 2.4.3 *'Normal'* Update requirements 

>**NOTE:** **When available - this is the recommended option.**

By default, Mono Gateway Development Kit firmware updates are performed using the `firmware` helper application. This downloads, authenticates and verifies the firmware files prior to applying the firmware update. 

No further preparation required - Move onto [§3](#3-the-firmware-update-process) to update your firmware.

### 2.4.4 *'Offline'* update requirements

>**NOTE:** This is ***not*** recommended, especially for the initial *'as-shipped'* firmware due to known issues (resolved in later firmware releases) with mounting FAT32 USB devices.

To prepare to perform an *'offline'* firmware update, you must: 

#### A) Obtain the firmware release files 

Mono firmware can be obtained from [firmware.mono.si](https://firmware.mono.si/), using username: `mono`, and utilising a MAC address of your device, in the form `xx:xx:xx:xx:xx:xx` as the password. 

>**NOTE:** MAC addresses are noted on a physical sticker on the base of the unit, or can be found via use of the `ip address show` command within the 'Recovery Linux' environment.

#### B) Stage firmware files in an accessible location

>**NOTE:** On the 'as-shipped' shipped firmware, known bugs will frustrate the use of USB drives. 

**USB drive:**
Move the firmware files onto a FAT32 formatted USB stick, either in the drive root or 'firmware/' folder. Insert the prepared USB drive into the middle USB-C slot on the Mono Gateway Development kit. For a visual reference, see: [Mono's Port Description](https://docs.mono.si/gateway-development-kit/hardware-description#port-description)

>**NOTE:** The USB data port is USB-C. A USB-A to USB-C adaptor may be required.

**Locally hosted web-server:**
Firmware can also staged on a local web server, and retrieved via the same `curl` approach used in the 'Legacy' method. Make note of the URL for the staging location.

With the firmware staged, move onto [§3](#3-the-firmware-update-process) to update your firmware.

---

# 3. The firmware update process

\*\*\* WARNING: ONLY UPDATE THE FIRMWARE ON ONE STORAGE DEVICE AT A TIME \*\*\*

\*\*\* WARNING: NEVER UPDATE THE DEVICE YOU USED TO BOOT \*\*\*

>**WARNING:** Failure to follow this guidance may result in the soft *'bricking'* of your device. Recovery from a *'bricked'* state requires either: additional hardware like a JTAG programmer, OR returning the device to Mono to rebuild. To avoid a bad outcome - **follow these two rules!

### Update methods

>**NOTE:** If performing the update for the first time you ***MUST*** use the 'Legacy' method. 

- Using `Mono-imager`, see: [§3.1](#31-using-mono-imager)

- ***'Legacy'*** method, see: [§3.2](#32-legacy-method)

- ***'Normal'*** method, see: [§3.3](#33-normal-method-using-firmware-helper)

- ***'Offline'*** method, see: [§3.4](#34-offline-method)

## 3.1 Using `Mono-imager`

This is the newest of the firmware update methods and aims to provide a streamlined and guided firmware update and OS installation process. **This should be your first choice in Firmware update methods**

Full documentation for using `mono-imager` can be found in the [mono-imager](https://github.com/HAHermsen/mono-imager) GitHub repo.

## 3.2 'Legacy' method

>**NOTE:** If performing the update for the first time you ***MUST*** use start here

**Before you start: - READ: [§2 Requirements](#2-requirements)**

Ensure you have all the information you will need to complete this process.

### START

>**STEP #1** Set the dip-switch on the PCB to 'NOR'
>**NOTE: (This is the default position) You are booting from NOR**

---

>**STEP #2** Power-on your device

---

>**STEP #3** When prompted, interrupt boot

---

>**STEP #4** Type `run recovery` + hit return

---

>**STEP #5** login as user `root` (no password)

---

>**STEP #6** Connect a network cable from your existing router to the Mono Gateway

---

>**STEP #7** Using the commands outlined in [§2.4.2](#242-legacy-update-requirements) enable the interface you have used to connect the Mono Gateway development kit, define an IP address, and default route.

```bash
# EXAMPLE: To config up eth1, with an IP of 10.0.0.200 with the 10.0.0.0/24 network, with your (existing router) at 10.0.0.1:

ip link set up eth1										# set eth1 admin up
ip address add 10.0.0.200/24 dev eth1					# add IP for eth1
ip route add default via 10.0.0.1 dev eth1				# add route via eth1

```

---

>**STEP #8** Test your connection:

```bash
ping 8.8.8.8											# Test ping internet
nslookup google.com										# Tests DNS lookup
```
---

>**STEP #9** Download firmware for eMMC, replacing `XX:XX:XX:XX:XX:XX` with your MAC (see sticker on base)
```bash
curl -ku mono:XX:XX:XX:XX:XX:XX -O https://firmware.mono.si/firmware-emmc-gateway-dk.bin 
```

---

>**STEP #10** Flash the eMMC firmware

>\*\* **WARNING: If you have installed an OS (OPNsense, OpenWRT, VyOS) that you want to keep, ensure you have backed-up your U-boot environment variables before proceeding. The next operation will reset these, and may render your existing OS unbootable** \*\*

```bash
dd if=firmware-emmc-gateway-dk.bin of=/dev/mmcblk0 bs=4096 skip=1 seek=1 
```

---

>**STEP #11** Flip the dip-switch on the PCB to 'eMMC'
>**NOTE: You will now boot from eMMC**

---

>**STEP #12** Reboot (from eMMC)

```bash
reboot
```

---

>**STEP #13** Test eMMC boot works

If it boots - eMMC flashed successfully, and you are 50% done. If you get no output, go back to STEP #1 and retry.

---

>**STEP #14** (now) Repeat steps #3 to #8 to setup your IP, test your connection and prepare to update the NOR flash.
>**NOTE:** You may note that the 'Recovery Linux' prompt looks different - this is expected

---

>**STEP #15** You can now use the new `firmware` helper to flash NOR

>\*\* **WARNING: If you have installed an OS (OPNsense, OpenWRT, VyOS) that you want to keep, ensure you have backed-up your U-boot environment variables before proceeding. The next operation will reset these, and may render your existing OS unbootable** \*\*

```bash
firmware update					# Update firmware; restore default U-Boot env vars

firmware update --preserve-env	# Update firmware; preserve U-Boot env vars

# Follow the prompts, and wait for the process to complete.
``````

\*\*\* DO NOT INTERRUPT THE PROCESS ONCE STARTED \*\*\*

---

>**STEP #16** Flip the dip-switch on the PCB back to 'NOR'
>**NOTE: You will now boot from NOR**

---

>**STEP #17** Reboot (from NOR)
```bash
reboot
```

---

>**STEP #18** Test NOR boot works

If it boots, then NOR flashed successfully. **You are 100% done.** 
If you get no output, go back to step STEP #11 and retry.

---
### FINISH!

---

## 3.3 'Normal' method (using `firmware` helper)

**Before you start: - READ: [§2 Requirements](#2-requirements)**

Ensure you have all the information you will need to complete the process.

### START

>**STEP #1** Set the dip-switch on the PCB to 'NOR'
>**NOTE: (This is the default position) You are booting from NOR**

---

>**STEP #2** Power-on your device

---

>**STEP #3** When prompted, interrupt boot

---

>**STEP #4** Type `run recovery` + hit return

---

>**STEP #5** login as user `root` (no password)

---

>**STEP #6** Connect a network cable from your existing router to the Mono Gateway

---

>**STEP #7** Using the commands outlined in 2.3.2 enable the interface you have used to connect the Mono Gateway development kit, define an IP address, and default route.

```bash
# EXAMPLE: To config up eth1, with an IP of 10.0.0.200 with the 10.0.0.0/24 network, with your (existing router) at 10.0.0.1:

ip link set up eth1										# set eth1 admin up
ip address add 10.0.0.200/24 dev eth1					# add IP for eth1
ip route add default via 10.0.0.1 dev eth1				# add route via eth1
```

---

>**STEP #8** Test your connection:

```bash
ping 8.8.8.8											# Test ping internet
nslookup google.com										# Tests DNS lookup
```
---

>**STEP #9** Use the `firmware` helper to flash eMMC

\*\* **WARNING: If you have installed an OS (OPNsense, OpenWRT, VyOS) that you want to keep, ensure you have backed-up your U-boot environment variables before proceeding. The next operation will reset these, and may render your existing OS unbootable** \*\*

```bash
firmware update					# Update firmware; restore default U-Boot env vars

firmware update --preserve-env	# Update firmware; preserve U-Boot env vars

# Follow the prompts, and wait for the process to complete.
```


---

>**STEP #10** Flip the dip-switch on the PCB to 'eMMC'
>**NOTE: You will now boot from eMMC**

---

>**STEP #11** Reboot (from eMMC)

```bash
reboot
```

---

>**STEP #12** Test eMMC boot works

If it boots - eMMC flashed successfully, and you are 50% done. If you get no output, go back to STEP #1 and retry.

---

>**STEP #13** (now) Repeat steps #3 to #8 to setup your IP, test your connection and prepare to update the NOR flash.
>**NOTE:** You may note that the 'Recovery Linux' prompt looks different - this is expected

---

>**STEP #14** You can now use the new `firmware` helper to flash NOR

```bash
firmware update					# Update firmware; restore default U-Boot env vars

firmware update --preserve-env	# Update firmware; preserve U-Boot env vars

# Follow the prompts, and wait for the process to complete.
```

\*\*\* DO NOT INTERRUPT THE PROCESS ONCE STARTED \*\*\*

---

>**STEP #15** Flip the dip-switch on the PCB back to 'NOR'
>**NOTE: You will now boot from NOR**

---

>**STEP #16** Reboot (from NOR)
```bash
reboot
```

---

>**STEP #17** Test NOR boot works

If it boots, then NOR flashed successfully. **You are 100% done.** 
If you get no output, go back to step STEP #11 and retry.

---
### FINISH!

---

## 3.4 'Offline' method

**Before you start: - READ: §2!** 

Ensure you have all the information you will need to complete the process.

This is a variation of the process shown in [§3.3](#33-normal-method-using-firmware-helper) with a singular modification in steps #9 + #14 which directs the `firmware` helper where to source the firmware `*.bin` files. 

>**NOTE:** For the full range of available options, run `firmware help`

```bash
# For firmware located on a FAT32 USB drive in / or /firmware/ use:
firmware update --usb

# For firmware located on a local webserver at URL use:
firmware update --url URL

# For firmware located locally at PATH [e.g. previously obtained via curl] use:
firmware update --from PATH
```



## 3.5 Troubleshooting

Some basic troubleshooting tips.

### 3.5.1 If ping fails

1) Check your network cable is actually in eth1... 

```bash
ip a show						# prints interface IPs, MACs, link state
```

If your configured interface, e.g. `eth1` shows `no-carrier` - you've have likely configured another interface by mistake, and not the one with the network cable. For why this may happen, see: [HARDWARE.md](HARDWARE.md#31-as-shipped-with-cosmetic-correction-applied). Fix by moving the cable to the correct interface, at which point the `no-carrier` next to your configured interface will no longer be observed in the output of `ip a show`.

2) Check any upstream firewall is not blocking/dropping traffic - if so, configure it accordingly.

### 3.5.2 If nslookup fails:

1) Ensure you have a working, accessible DNS server defined in `/etc/resolv.conf` and retry. For how to modify the local DNS configuration, see: [§2.3.3](FIRMWARE.md#233-custom-dns-101)

2) Check any upstream firewall is not blocking/dropping traffic - if so, configure it accordingly.