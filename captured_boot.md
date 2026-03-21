=> usb start
starting USB...
Bus usb@2f00000: Register 200017f NbrPorts 2
Starting the controller
USB XHCI 1.00
scanning bus usb@2f00000 for devices... 2 USB Device(s) found
       scanning usb for storage devices... 1 Storage Device(s) found
=> setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/sda1 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 quiet"
=> 
=> fatload usb 0:1 ${kernel_addr_r} live/vmlinuz-6.6.128-vyos
9210147 bytes read in 231 ms (38 MiB/s)
=> fatload usb 0:1 ${fdt_addr_r} mono-gw.dtb
94208 bytes read in 7 ms (12.8 MiB/s)
=> fatload usb 0:1 ${ramdisk_addr_r} live/initrd.img-6.6.128-vyos
33287447 bytes read in 810 ms (39.2 MiB/s)
=> fatload usb 0:1 ${ramdisk_addr_r} live/initrd.img-6.6.128-vyos
33287447 bytes read in 808 ms (39.3 MiB/s)
=> booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
   Uncompressing Kernel Image to 0
## Flattened Device Tree blob at 88000000
   Booting using the fdt blob at 0x88000000
Working FDT set to 88000000
   Loading Ramdisk to f8c42000, end fac00d17 ... OK
   Loading Device Tree to 00000000f8c18000, end 00000000f8c41fff ... OK
Working FDT set to f8c18000
PCIe1: pcie@3400000 Root Complex: no link
PCIe2: pcie@3500000 disabled
PCIe3: pcie@3600000 Root Complex: no link
WARNING failed to get smmu node: FDT_ERR_NOTFOUND
WARNING failed to get smmu node: FDT_ERR_NOTFOUND

Starting kernel ...

[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x410fd082]
[    0.000000] Linux version 6.6.128-vyos (root@f2ebfcb0518d) (gcc (Debian 12.2.0-14+deb12u1) 12.2.0, GNU ld (GNU Binutils for Debian) 2.40) #1 SMP PREEMPT_DYNAMIC Fri Mar 20 22:33:14 UTC 2026
[    0.000000] KASLR enabled
[    0.000000] Machine model: Mono Gateway Development Kit
[    0.000000] earlycon: uart8250 at MMIO 0x00000000021c0500 (options '')
[    0.000000] printk: bootconsole [uart8250] enabled

Welcome to VyOS 2026.03.20-2209-rolling (current)!

[  OK  ] Created slice Slice /system/getty.
[  OK  ] Created slice Slice /system/modprobe.
[  OK  ] Created slice Slice /system/serial-getty.
[  OK  ] Created slice User and Session Slice.
[  OK  ] Started Dispatch Password …ts to Console Directory Watch.
[  OK  ] Started Forward Password R…uests to Wall Directory Watch.
[  OK  ] Set up automount Arbitrary…s File System Automount Point.
[  OK  ] Reached target Local Encrypted Volumes.
[  OK  ] Reached target Local Integrity Protected Volumes.
[  OK  ] Reached target Remote File Systems.
[  OK  ] Reached target Slice Units.
[  OK  ] Reached target TLS tunnels…ices - per-config-file target.
[  OK  ] Reached target Swaps.
[  OK  ] Reached target Local Verity Protected Volumes.
[  OK  ] Listening on initctl Compatibility Named Pipe.
[  OK  ] Listening on Journal Socket (/dev/log).
[  OK  ] Listening on Journal Socket.
[  OK  ] Listening on udev Control Socket.
[  OK  ] Listening on udev Kernel Socket.
         Mounting Huge Pages File System...
         Mounting POSIX Message Queue File System...
         Mounting Kernel Debug File System...
         Mounting Kernel Trace File System...
         Starting Create List of Static Device Nodes...
         Starting Load Kernel Module configfs...
         Starting Load Kernel Module dm_mod...
         Starting Load Kernel Module drm...
         Starting Load Kernel Module efi_pstore...
         Starting Load Kernel Module fuse...
         Starting Load Kernel Module loop...
         Starting Journal Service...
         Starting Load Kernel Modules...
         Starting Remount Root and Kernel File Systems...
         Starting Coldplug All udev Devices...
[  OK  ] Mounted Huge Pages File System.
[  OK  ] Mounted POSIX Message Queue File System.
[  OK  ] Mounted Kernel Debug File System.
[  OK  ] Mounted Kernel Trace File System.
[  OK  ] Finished Create List of Static Device Nodes.
[  OK  ] Finished Load Kernel Module configfs.
[  OK  ] Finished Load Kernel Module dm_mod.
[  OK  ] Finished Load Kernel Module drm.
[  OK  ] Finished Load Kernel Module efi_pstore.
[  OK  ] Started Journal Service.
[  OK  ] Finished Load Kernel Module fuse.
[  OK  ] Finished Load Kernel Module loop.
[  OK  ] Finished Remount Root and Kernel File Systems.
         Mounting FUSE Control File System...
         Mounting Kernel Configuration File System...
         Starting Flush Journal to Persistent Storage...
         Starting Load/Save Random Seed...
         Starting Create System Users...
[  OK  ] Started VyOS commit daemon.
[  OK  ] Started VyOS configuration daemon.
[  OK  ] Started VyOS DNS configuration keeper.
[  OK  ] Finished Load Kernel Modules.
[  OK  ] Finished Coldplug All udev Devices.
[  OK  ] Mounted FUSE Control File System.
[  OK  ] Mounted Kernel Configuration File System.
[  OK  ] Finished Flush Journal to Persistent Storage.
[  OK  ] Finished Load/Save Random Seed.
[  OK  ] Finished Create System Users.
         Starting Apply Kernel Variables...
         Starting Create Static Device Nodes in /dev...
[  OK  ] Finished Create Static Device Nodes in /dev.
[  OK  ] Reached target Preparation for Local File Systems.
         Mounting /tmp...
         Mounting /var/tmp...
         Starting Rule-based Manage…for Device Events and Files...
[  OK  ] Finished Apply Kernel Variables.
[  OK  ] Mounted /tmp.
[  OK  ] Mounted /var/tmp.
[  OK  ] Reached target Local File Systems.
         Starting Set Up Additional Binary Formats...
         Starting Create System Files and Directories...
[  OK  ] Finished Create System Files and Directories.
         Starting Security Auditing Service...
[  OK  ] Started Entropy Daemon based on the HAVEGE algorithm.
         Starting live-config conta…t process (late userspace)....
[  OK  ] Started Rule-based Manager for Device Events and Files.
         Mounting Arbitrary Executable File Formats File System...
[  OK  ] Mounted Arbitrary Executable File Formats File System.
[  OK  ] Finished Set Up Additional Binary Formats.
[  OK  ] Started Security Auditing Service.
         Starting Record System Boot/Shutdown in UTMP...
[  OK  ] Finished Record System Boot/Shutdown in UTMP.
[  OK  ] Reached target System Initialization.
[  OK  ] Started ACPI Events Check.
[  OK  ] Started Periodic ext4 Onli…ata Check for All Filesystems.
[  OK  ] Started Discard unused blocks once a week.
[  OK  ] Started Daily rotation of log files.
[  OK  ] Started Daily Cleanup of Temporary Directories.
[  OK  ] Reached target Path Units.
[  OK  ] Reached target Timer Units.
[  OK  ] Listening on ACPID Listen Socket.
[  OK  ] Listening on D-Bus System Message Bus Socket.
[  OK  ] Listening on Podman API Socket.
[  OK  ] Listening on UUID daemon activation socket.
[  OK  ] Reached target Socket Units.
[  OK  ] Finished live-config conta…oot process (late userspace)..
[  OK  ] Reached target Basic System.
[  OK  ] Started ACPI event daemon.
         Starting Deferred execution scheduler...
         Starting Atop process accounting daemon...
[  OK  ] Started Regular background program processing daemon.
         Starting D-Bus System Message Bus...
         Starting Remove Stale Onli…t4 Metadata Check Snapshots...
         Starting FastNetMon - DoS/…Flow/Netflow/mirror support...
         Starting LSB: Load kernel image with kexec...
         Starting Podman API Service...
         Starting User Login Management...
         Starting LSB: Start vmtouch daemon...
         Starting Update GRUB loader configuration structure...
[  OK  ] Started Deferred execution scheduler.
[  OK  ] Finished Remove Stale Onli…ext4 Metadata Check Snapshots.
[  OK  ] Started Podman API Service.
[  OK  ] Finished Update GRUB loader configuration structure.
[  OK  ] Started VyOS Router.
         Starting Permit User Sessions...
[  OK  ] Started D-Bus System Message Bus.
[  OK  ] Started User Login Management.
[  OK  ] Finished Permit User Sessions.
[  OK  ] Started Getty on tty1.
[  OK  ] Started Serial Getty on ttyS0.
[  OK  ] Reached target Login Prompts.
[   26.768478] vyos-router[865]: Starting VyOS router.
[  OK  ] Started FastNetMon - DoS/D… sFlow/Netflow/mirror support.
[  OK  ] Reached target Multi-User System.
[  OK  ] Reached target VyOS target.
         Starting Record Runlevel Change in UTMP...
[  OK  ] Finished Record Runlevel Change in UTMP.
[   29.851375] vyos-router[865]: Waiting for NICs to settle down: settled in 0sec..
[   29.867204] vyos-router[865]: could not generate DUID ... failed!
[   44.722386] vyos-router[865]: Mounting VyOS Config...done.
[   52.597214] BUG: using smp_processor_id() in preemptible [00000000] code: python3/1424
[   52.650297] BUG: using smp_processor_id() in preemptible [00000000] code: python3/1424
[   52.705706] BUG: using smp_processor_id() in preemptible [00000000] code: python3/1424
[   52.761309] BUG: using smp_processor_id() in preemptible [00000000] code: python3/1424
[   52.817750] BUG: using smp_processor_id() in preemptible [00000000] code: python3/1424
[   57.515983] BUG: using smp_processor_id() in preemptible [00000000] code: python3/362
[   57.578998] BUG: using smp_processor_id() in preemptible [00000000] code: python3/362
[   57.666344] BUG: using smp_processor_id() in preemptible [00000000] code: python3/362
[   58.700005] BUG: using smp_processor_id() in preemptible [00000000] code: python3/362
[   58.758521] BUG: using smp_processor_id() in preemptible [00000000] code: python3/362
[   58.844123] BUG: using smp_processor_id() in preemptible [00000000] code: python3/362
[   59.828385] BUG: using smp_processor_id() in preemptible [00000000] code: python3/362
[   59.888597] BUG: using smp_processor_id() in preemptible [00000000] code: python3/362
[   59.975516] BUG: using smp_processor_id() in preemptible [00000000] code: python3/362
[   60.971408] BUG: using smp_processor_id() in preemptible [00000000] code: python3/362
[   61.031019] BUG: using smp_processor_id() in preemptible [00000000] code: python3/362
[   61.117220] BUG: using smp_processor_id() in preemptible [00000000] code: python3/362
[  OK  ] Removed slice Slice /system/modprobe.
[  OK  ] Stopped target Local Encrypted Volumes.
[  OK  ] Stopped target Local Integrity Protected Volumes.
[  OK  ] Stopped target Timer Units.
[  OK  ] Stopped Periodic ext4 Onli…ata Check for All Filesystems.
[  OK  ] Stopped Discard unused blocks once a week.
[  OK  ] Stopped Daily rotation of log files.
[  OK  ] Stopped Daily Cleanup of Temporary Directories.
[  OK  ] Stopped target Local Verity Protected Volumes.
[  OK  ] Stopped target VyOS target.
[  OK  ] Stopped target Multi-User System.
[  OK  ] Stopped target Login Prompts.
[  OK  ] Stopped target TLS tunnels…ices - per-config-file target.
         Stopping ACPI event daemon...
         Stopping Deferred execution scheduler...
         Stopping Atop advanced performance monitor...
         Stopping Regular background program processing daemon...
         Stopping D-Bus System Message Bus...
         Stopping FastNetMon - DoS/…Flow/Netflow/mirror support...
         Stopping LSB: Load kernel image with kexec...
         Stopping System Logging Service...
         Stopping Set Up Additional Binary Formats...
         Stopping Hostname Service...
         Stopping User Login Management...
         Stopping Load/Save Random Seed...
[  OK  ] Stopped Apply Kernel Variables.
[  OK  ] Stopped Load Kernel Modules.
         Stopping Record System Boot/Shutdown in UTMP...
         Stopping LSB: Start vmtouch daemon...
[  OK  ] Unmounted /run/credentials/systemd-sysctl.service.
[  OK  ] Stopped ACPI event daemon.
[  OK  ] Stopped Regular background program processing daemon.
[  OK  ] Stopped D-Bus System Message Bus.
[  OK  ] Stopped User Login Management.
[  OK  ] Stopped Deferred execution scheduler.
[  OK  ] Stopped Atop advanced performance monitor.
[  OK  ] Stopped Getty on tty1.
[  OK  ] Stopped FastNetMon - DoS/D… sFlow/Netflow/mirror support.
[  OK  ] Stopped Hostname Service.
[  OK  ] Stopped Serial Getty on ttyS0.
[  OK  ] Stopped System Logging Service.
[  OK  ] Stopped Set Up Additional Binary Formats.
[  OK  ] Stopped Load/Save Random Seed.
[  OK  ] Stopped Record System Boot/Shutdown in UTMP.
[  OK  ] Stopped LSB: Load kernel image with kexec.
[  OK  ] Removed slice Slice /system/getty.
[  OK  ] Removed slice Slice /system/serial-getty.
[  OK  ] Unset automount Arbitrary …s File System Automount Point.
         Stopping Atop process accounting daemon...
         Stopping Security Auditing Service...
         Stopping Permit User Sessions...
[  OK  ] Stopped Atop process accounting daemon.
[  OK  ] Stopped Security Auditing Service.
[  OK  ] Stopped LSB: Start vmtouch daemon.
[  OK  ] Stopped Permit User Sessions.
[  OK  ] Stopped target Network.
[  OK  ] Stopped target Remote File Systems.
[  OK  ] Stopped Create System Files and Directories.
         Stopping VyOS Router...
[  OK  ] Unmounted /run/credentials…ystemd-tmpfiles-setup.service.
[   70.503167] vyos-router[2189]: Stopping VyOS router:.
[   70.512609] vyos-router[2189]: Un-mounting VyOS Config...
[   70.519542] vyos-router[2217]: umount: /opt/vyatta/config: target is busy.
[   70.528334] vyos-router[2189]: failed.
         Stopping FRRouting...
[  OK  ] Stopped FRRouting.
[  OK  ] Stopped VyOS Router.
[  OK  ] Stopped target Basic System.
[  OK  ] Stopped target Local File Systems.
[  OK  ] Stopped target Path Units.
[  OK  ] Stopped Dispatch Password …ts to Console Directory Watch.
[  OK  ] Stopped Forward Password R…uests to Wall Directory Watch.
[  OK  ] Stopped target Slice Units.
[  OK  ] Removed slice User and Session Slice.
[  OK  ] Stopped target Socket Units.
[  OK  ] Stopped target System Time Synchronized.
[  OK  ] Stopped target System Time Set.
[  OK  ] Closed ACPID Listen Socket.
[  OK  ] Closed D-Bus System Message Bus Socket.
[  OK  ] Closed Podman API Socket.
[  OK  ] Closed Syslog Socket.
[  OK  ] Closed UUID daemon activation socket.
         Unmounting /config...
         Unmounting /etc/cni/net.d...
         Unmounting /etc/frr/frr.conf...
         Unmounting /opt/vyatta/config/tmp/new_config_1464...
         Unmounting /run/credentials/systemd-sysusers.service...
         Unmounting /run/credential…-tmpfiles-setup-dev.service...
         Unmounting /tmp...
         Unmounting /usr/lib/live/mount/medium...
         Unmounting /usr/lib/live/mount/overlay...
         Unmounting /usr/lib/live/m…/rootfs/filesystem.squashfs...
[  OK  ] Unmounted /config.
[  OK  ] Unmounted /etc/cni/net.d.
[  OK  ] Unmounted /etc/frr/frr.conf.
[  OK  ] Unmounted /run/credentials/systemd-sysusers.service.
[  OK  ] Unmounted /run/credentials…md-tmpfiles-setup-dev.service.
[  OK  ] Unmounted /tmp.
[FAILED] Failed unmounting /usr/lib/live/mount/medium.
[  OK  ] Unmounted /usr/lib/live/mount/overlay.
[  OK  ] Unmounted /usr/lib/live/mount/rootfs/filesystem.squashfs.
[  OK  ] Unmounted /opt/vyatta/config/tmp/new_config_1464.
         Unmounting /opt/vyatta/config...
[FAILED] Failed unmounting /opt/vyatta/config.
[  OK  ] Stopped target Preparation for Local File Systems.
[  OK  ] Stopped target Swaps.
[  OK  ] Reached target Unmount All Filesystems.
[  OK  ] Stopped Create Static Device Nodes in /dev.
[  OK  ] Stopped Create System Users.
[  OK  ] Stopped Remount Root and Kernel File Systems.
[  OK  ] Reached target System Shutdown.
[  OK  ] Reached target Late Shutdown Services.
         Starting Reboot via kexec...
[   72.568647] (sd-umount)[2384]: Failed to unmount /usr/lib/live/mount/medium: Device or resource busy
[   72.584838] systemd-shutdown[1]: Could not detach loopback /dev/loop0: Device or resource busy
[   72.677826] systemd-shutdown[1]: Failed to finalize file systems, loop devices, ignoring.
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x410fd082]
[    0.000000] Linux version 6.6.128-vyos (root@f2ebfcb0518d) (gcc (Debian 12.2.0-14+deb12u1) 12.2.0, GNU ld (GNU Binutils for Debian) 2.40) #1 SMP PREEMPT_DYNAMIC Fri Mar 20 22:33:14 UTC 2026
[    0.000000] KASLR enabled
[    0.000000] random: crng init done
[    0.000000] Machine model: Mono Gateway Development Kit
[    0.000000] earlycon: uart8250 at MMIO 0x00000000021c0500 (options '')
[    0.000000] printk: bootconsole [uart8250] enabled
[    0.000000] efi: UEFI not found.
[    0.000000] OF: reserved mem: 0x00000009fc000000..0x00000009fdffffff (32768 KiB) nomap non-reusable qman-pfdr
[    0.000000] OF: reserved mem: 0x00000009fe800000..0x00000009feffffff (8192 KiB) nomap non-reusable qman-fqd
[    0.000000] OF: reserved mem: initialized node bman-fbpr, compatible id fsl,bman-fbpr
[    0.000000] OF: reserved mem: 0x00000009ff000000..0x00000009ffffffff (16384 KiB) nomap non-reusable bman-fbpr
[    0.000000] NUMA: No NUMA configuration found
[    0.000000] NUMA: Faking a node at [mem 0x0000000080000000-0x00000009ffffffff]
[    0.000000] NUMA: NODE_DATA [mem 0x9fb7f31c0-0x9fb7f6fff]
[    0.000000] Zone ranges:
[    0.000000]   DMA      [mem 0x0000000080000000-0x00000000ffffffff]
[    0.000000]   DMA32    empty
[    0.000000]   Normal   [mem 0x0000000100000000-0x00000009ffffffff]
[    0.000000] Movable zone start for each node
[    0.000000] Early memory node ranges
[    0.000000]   node   0: [mem 0x0000000080000000-0x00000000fbdfffff]
[    0.000000]   node   0: [mem 0x0000000880000000-0x00000009fbffffff]
[    0.000000]   node   0: [mem 0x00000009fc000000-0x00000009fdffffff]
[    0.000000]   node   0: [mem 0x00000009fe000000-0x00000009fe7fffff]
[    0.000000]   node   0: [mem 0x00000009fe800000-0x00000009ffffffff]
[    0.000000] Initmem setup node 0 [mem 0x0000000080000000-0x00000009ffffffff]
[    0.000000] On node 0, zone Normal: 16896 pages in unavailable ranges
[    0.000000] psci: probing for conduit method from DT.
[    0.000000] psci: PSCIv1.1 detected in firmware.
[    0.000000] psci: Using standard PSCI v0.2 function IDs
[    0.000000] psci: MIGRATE_INFO_TYPE not supported.
[    0.000000] psci: SMC Calling Convention v1.5
[    0.000000] percpu: Embedded 30 pages/cpu s83688 r8192 d31000 u122880
[    0.000000] Detected PIPT I-cache on CPU0
[    0.000000] CPU features: detected: Spectre-v2
[    0.000000] CPU features: detected: Spectre-v3a
[    0.000000] CPU features: detected: Spectre-BHB
[    0.000000] CPU features: kernel page table isolation forced ON by KASLR
[    0.000000] CPU features: detected: Kernel page table isolation (KPTI)
[    0.000000] CPU features: detected: ARM erratum 1742098
[    0.000000] CPU features: detected: ARM errata 1165522, 1319367, or 1530923
[    0.000000] alternatives: applying boot alternatives
[    0.000000] Kernel command line: console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/sda1 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0
[    0.000000] Unknown kernel command line parameters "components noeject nopersistence noautologin nonetworking boot=live live-media=/dev/sda1 union=overlay", will be passed to user space.
[    0.000000] Dentry cache hash table entries: 1048576 (order: 11, 8388608 bytes, linear)
[    0.000000] Inode-cache hash table entries: 524288 (order: 10, 4194304 bytes, linear)
[    0.000000] Fallback order for Node 0: 0 
[    0.000000] Built 1 zonelists, mobility grouping on.  Total pages: 2047488
[    0.000000] Policy zone: Normal
[    0.000000] mem auto-init: stack:off, heap alloc:off, heap free:off
[    0.000000] software IO TLB: area num 4.
[    0.000000] software IO TLB: mapped [mem 0x00000000f7e00000-0x00000000fbe00000] (64MB)
[    0.000000] Memory: 7979660K/8321024K available (11776K kernel code, 2422K rwdata, 5216K rodata, 4544K init, 597K bss, 341364K reserved, 0K cma-reserved)
[    0.000000] SLUB: HWalign=64, Order=0-3, MinObjects=0, CPUs=4, Nodes=1
[    0.000000] Dynamic Preempt: none
[    0.000000] rcu: Preemptible hierarchical RCU implementation.
[    0.000000] rcu:     RCU restricting CPUs from NR_CPUS=256 to nr_cpu_ids=4.
[    0.000000]  Trampoline variant of Tasks RCU enabled.
[    0.000000]  Tracing variant of Tasks RCU enabled.
[    0.000000] rcu: RCU calculated value of scheduler-enlistment delay is 100 jiffies.
[    0.000000] rcu: Adjusting geometry for rcu_fanout_leaf=16, nr_cpu_ids=4
[    0.000000] NR_IRQS: 64, nr_irqs: 64, preallocated irqs: 0
[    0.000000] GIC: Adjusting CPU interface base to 0x000000000142f000
[    0.000000] Root IRQ handler: gic_handle_irq
[    0.000000] GIC: Using split EOI/Deactivate mode
[    0.000000] rcu: srcu_init: Setting srcu_struct sizes based on contention.
[    0.000000] arch_timer: cp15 timer(s) running at 25.00MHz (phys).
[    0.000000] clocksource: arch_sys_counter: mask: 0xffffffffffffff max_cycles: 0x5c40939b5, max_idle_ns: 440795202646 ns
[    0.000000] sched_clock: 56 bits at 25MHz, resolution 40ns, wraps every 4398046511100ns
[    0.008816] Console: colour dummy device 80x25
[    0.013463] Calibrating delay loop (skipped), value calculated using timer frequency.. 50.00 BogoMIPS (lpj=25000)
[    0.023817] pid_max: default: 32768 minimum: 301
[    0.028833] Mount-cache hash table entries: 16384 (order: 5, 131072 bytes, linear)
[    0.036499] Mountpoint-cache hash table entries: 16384 (order: 5, 131072 bytes, linear)
[    0.046492] RCU Tasks: Setting shift to 2 and lim to 1 rcu_task_cb_adjust=1 rcu_task_cpu_ids=4.
[    0.055379] RCU Tasks Trace: Setting shift to 2 and lim to 1 rcu_task_cb_adjust=1 rcu_task_cpu_ids=4.
[    0.064923] rcu: Hierarchical SRCU implementation.
[    0.069760] rcu:     Max phase no-delay instances is 400.
[    0.076014] EFI services will not be available.
[    0.080886] smp: Bringing up secondary CPUs ...
[    0.086126] Detected PIPT I-cache on CPU1
[    0.086224] CPU1: Booted secondary processor 0x0000000001 [0x410fd082]
[    0.086957] Detected PIPT I-cache on CPU2
[    0.087033] CPU2: Booted secondary processor 0x0000000002 [0x410fd082]
[    0.087733] Detected PIPT I-cache on CPU3
[    0.087810] CPU3: Booted secondary processor 0x0000000003 [0x410fd082]
[    0.087931] smp: Brought up 1 node, 4 CPUs
[    0.123896] SMP: Total of 4 processors activated.
[    0.128638] CPU features: detected: 32-bit EL0 Support
[    0.133817] CPU features: detected: CRC32 instructions
[    0.139086] CPU: All CPU(s) started at EL2
[    0.143221] alternatives: applying system-wide alternatives
[    0.150397] devtmpfs: initialized
[    0.167140] clocksource: jiffies: mask: 0xffffffff max_cycles: 0xffffffff, max_idle_ns: 1911260446275000 ns
[    0.176981] futex hash table entries: 1024 (order: 4, 65536 bytes, linear)
[    0.184104] pinctrl core: initialized pinctrl subsystem
[    0.190294] Machine: Mono Gateway Development Kit
[    0.195041] SoC family: QorIQ LS1046A
[    0.198732] SoC ID: svr:0x87070010, Revision: 1.0
[    0.204018] DMI not present or invalid.
[    0.208411] NET: Registered PF_NETLINK/PF_ROUTE protocol family
[    0.215194] DMA: preallocated 1024 KiB GFP_KERNEL pool for atomic allocations
[    0.222628] DMA: preallocated 1024 KiB GFP_KERNEL|GFP_DMA pool for atomic allocations
[    0.230750] DMA: preallocated 1024 KiB GFP_KERNEL|GFP_DMA32 pool for atomic allocations
[    0.238949] audit: initializing netlink subsys (disabled)
[    0.244556] audit: type=2000 audit(0.055:1): state=initialized audit_enabled=0 res=1
[    0.245692] thermal_sys: Registered thermal governor 'fair_share'
[    0.252372] thermal_sys: Registered thermal governor 'bang_bang'
[    0.258515] thermal_sys: Registered thermal governor 'step_wise'
[    0.264568] thermal_sys: Registered thermal governor 'user_space'
[    0.270681] cpuidle: using governor ladder
[    0.280979] cpuidle: using governor menu
[    0.285151] hw-breakpoint: found 6 breakpoint and 4 watchpoint registers.
[    0.292117] ASID allocator initialised with 32768 entries
[    0.298036] Serial: AMBA PL011 UART driver
[    0.340538] Modules: 2G module region forced by RANDOMIZE_MODULE_REGION_FULL
[    0.347652] Modules: 0 pages in range for non-PLT usage
[    0.347658] Modules: 518096 pages in range for PLT usage
[    0.353898] HugeTLB: registered 1.00 GiB page size, pre-allocated 0 pages
[    0.366098] HugeTLB: 0 KiB vmemmap can be freed for a 1.00 GiB page
[    0.372417] HugeTLB: registered 32.0 MiB page size, pre-allocated 0 pages
[    0.379257] HugeTLB: 0 KiB vmemmap can be freed for a 32.0 MiB page
[    0.385575] HugeTLB: registered 2.00 MiB page size, pre-allocated 0 pages
[    0.392415] HugeTLB: 0 KiB vmemmap can be freed for a 2.00 MiB page
[    0.398732] HugeTLB: registered 64.0 KiB page size, pre-allocated 0 pages
[    0.405571] HugeTLB: 0 KiB vmemmap can be freed for a 64.0 KiB page
[    0.430007] raid6: neonx8   gen()  2056 MB/s
[    0.451423] raid6: neonx4   gen()  2006 MB/s
[    0.472824] raid6: neonx2   gen()  1671 MB/s
[    0.494235] raid6: neonx1   gen()  1215 MB/s
[    0.515650] raid6: int64x8  gen()  1182 MB/s
[    0.537059] raid6: int64x4  gen()  1151 MB/s
[    0.558461] raid6: int64x2  gen()  1119 MB/s
[    0.579870] raid6: int64x1  gen()   855 MB/s
[    0.584173] raid6: using algorithm neonx8 gen() 2056 MB/s
[    0.606711] raid6: .... xor() 1446 MB/s, rmw enabled
[    0.611714] raid6: using neon recovery algorithm
[    0.617257] ACPI: Interpreter disabled.
[    0.624780] iommu: Default domain type: Translated
[    0.629617] iommu: DMA domain TLB invalidation policy: lazy mode
[    0.636127] usbcore: registered new interface driver usbfs
[    0.641708] usbcore: registered new interface driver hub
[    0.647107] usbcore: registered new device driver usb
[    0.652567] pps_core: LinuxPPS API ver. 1 registered
[    0.657576] pps_core: Software ver. 5.3.6 - Copyright 2005-2007 Rodolfo Giometti <giometti@linux.it>
[    0.666793] PTP clock support registered
[    0.671071] EDAC MC: Ver: 3.0.0
[    0.675192] scmi_core: SCMI protocol bus registered
[    0.681815] clocksource: Switched to clocksource arch_sys_counter
[    0.688764] pnp: PnP ACPI: disabled
[    0.701549] NET: Registered PF_INET protocol family
[    0.706784] IP idents hash table entries: 131072 (order: 8, 1048576 bytes, linear)
[    0.721676] tcp_listen_portaddr_hash hash table entries: 4096 (order: 4, 65536 bytes, linear)
[    0.730358] Table-perturb hash table entries: 65536 (order: 6, 262144 bytes, linear)
[    0.738185] TCP established hash table entries: 65536 (order: 7, 524288 bytes, linear)
[    0.746755] TCP bind hash table entries: 65536 (order: 9, 2097152 bytes, linear)
[    0.756408] TCP: Hash tables configured (established 65536 bind 65536)
[    0.763304] MPTCP token hash table entries: 8192 (order: 6, 196608 bytes, linear)
[    0.771064] UDP hash table entries: 4096 (order: 5, 131072 bytes, linear)
[    0.778141] UDP-Lite hash table entries: 4096 (order: 5, 131072 bytes, linear)
[    0.785825] NET: Registered PF_UNIX/PF_LOCAL protocol family
[    0.791575] NET: Registered PF_XDP protocol family
[    0.796423] PCI: CLS 0 bytes, default 64
[    0.800674] Trying to unpack rootfs image as initramfs...
[    0.810897] Initialise system trusted keyrings
[    0.815572] workingset: timestamp_bits=40 max_order=21 bucket_order=0
[    0.822778] xor: measuring software checksum speed
[    0.828618]    8regs           :  3353 MB/sec
[    0.833914]    32regs          :  3642 MB/sec
[    0.839545]    arm64_neon      :  2652 MB/sec
[    0.843938] xor: using function: 32regs (3642 MB/sec)
[    0.849037] async_tx: api initialized (async)
[    0.853442] Key type asymmetric registered
[    0.857576] Asymmetric key parser 'x509' registered
[    0.862569] Block layer SCSI generic (bsg) driver version 0.4 loaded (major 248)
[    0.870043] io scheduler mq-deadline registered
[    0.874615] io scheduler kyber registered
[    0.878718] io scheduler bfq registered
[    0.903533] shpchp: Standard Hot Plug PCI Controller Driver version: 0.4
[    0.926286] bman_ccsr: BMan BAR already configured
[    0.933350] bman_portal 508000000.bman-portal: Portal initialised, cpu 0
[    0.940421] bman_portal 508010000.bman-portal: Portal initialised, cpu 1
[    0.947468] bman_portal 508020000.bman-portal: Portal initialised, cpu 2
[    0.954516] bman_portal 508030000.bman-portal: Portal initialised, cpu 3
[    0.967266] qman_portal 500000000.qman-portal: Portal initialised, cpu 0
[    0.974325] qman_portal 500010000.qman-portal: Portal initialised, cpu 1
[    0.981378] qman_portal 500020000.qman-portal: Portal initialised, cpu 2
[    0.988875] qman_portal 500030000.qman-portal: Portal initialised, cpu 3
[    1.978532] Freeing initrd memory: 32504K
[   12.195435] Serial: 8250/16550 driver, 4 ports, IRQ sharing enabled
[   12.205713] printk: console [ttyS0] disabled
[   12.210418] 21c0500.serial: ttyS0 at MMIO 0x21c0500 (irq = 52, base_baud = 18750000) is a 16550A
[   12.219322] printk: console [ttyS0] enabled
[   12.219322] printk: console [ttyS0] enabled
[   12.227735] printk: bootconsole [uart8250] disabled
[   12.227735] printk: bootconsole [uart8250] disabled
[   12.238745] 21c0600.serial: ttyS1 at MMIO 0x21c0600 (irq = 52, base_baud = 18750000) is a 16550A
[   12.247742] serial serial0: tty port ttyS1 registered
[   12.426557] fsl_dpaa_mac 1ae2000.ethernet: FMan MEMAC
[   12.431649] fsl_dpaa_mac 1ae2000.ethernet: FMan MAC address: e8:f6:d7:00:16:01
[   12.439587] fsl_dpaa_mac 1ae8000.ethernet: FMan MEMAC
[   12.444663] fsl_dpaa_mac 1ae8000.ethernet: FMan MAC address: e8:f6:d7:00:15:ff
[   12.452498] fsl_dpaa_mac 1aea000.ethernet: FMan MEMAC
[   12.457573] fsl_dpaa_mac 1aea000.ethernet: FMan MAC address: e8:f6:d7:00:16:00
[   12.465406] fsl_dpaa_mac 1af0000.ethernet: FMan MEMAC
[   12.470480] fsl_dpaa_mac 1af0000.ethernet: FMan MAC address: e8:f6:d7:00:16:02
[   12.478382] fsl_dpaa_mac 1af2000.ethernet: FMan MEMAC
[   12.483467] fsl_dpaa_mac 1af2000.ethernet: FMan MAC address: e8:f6:d7:00:16:03
[   12.534462] fsl_dpaa_mac 1ae2000.ethernet eth0: Probed interface eth0
[   12.584719] fsl_dpaa_mac 1ae8000.ethernet eth1: Probed interface eth1
[   12.635260] fsl_dpaa_mac 1aea000.ethernet eth2: Probed interface eth2
[   12.686079] fsl_dpaa_mac 1af0000.ethernet eth3: Probed interface eth3
[   12.737899] fsl_dpaa_mac 1af2000.ethernet eth4: Probed interface eth4
[   12.745501] ptp_qoriq: device tree node missing required elements, try automatic configuration
[   12.756096] device-mapper: uevent: version 1.0.3
[   12.760977] device-mapper: ioctl: 4.48.0-ioctl (2023-03-01) initialised: dm-devel@redhat.com
[   12.771160] ledtrig-cpu: registered to indicate activity on CPUs
[   12.778683] SMCCC: SOC_ID: ARCH_SOC_ID not implemented, skipping ....
[   12.787236] hw perfevents: enabled with armv8_cortex_a72 PMU driver, 7 counters available
[   12.796334] drop_monitor: Initializing network drop monitor service
[   12.803155] NET: Registered PF_INET6 protocol family
[   12.856154] Segment Routing with IPv6
[   12.859889] In-situ OAM (IOAM) with IPv6
[   12.863915] mip6: Mobile IPv6
[   12.867057] Key type dns_resolver registered
[   12.871380] mpls_gso: MPLS GSO support
[   12.884955] registered taskstats version 1
[   12.889268] Loading compiled-in X.509 certificates
[   12.920051] Loaded X.509 cert 'VyOS Networks build time autogenerated Kernel key: b7f0a1d67d56aa945a944832fa5aba150048efed'
[   12.956613] Loaded X.509 cert 'VyOS LS1046A Secure Boot CA: ed9ff86ac8d3dc1144144291a885ffd7bcd198db'
[   12.981242] clk: Disabling unused clocks
[   12.987621] Freeing unused kernel memory: 4544K
[   13.079712] Checked W+X mappings: passed, no W+X pages found
[   13.085413] Run /init as init process
Loading, please wait...
Starting systemd-udevd version 252.39-1~deb12u1
[   13.654926] sdhci: Secure Digital Host Controller Interface driver
[   13.661164] sdhci: Copyright(c) Pierre Ossman
[   13.671755] sdhci-pltfm: SDHCI platform and OF driver helper
[   13.727213] fsl_dpaa_mac 1ae8000.ethernet e3: renamed from eth1
[   13.745159] fsl_dpaa_mac 1af2000.ethernet e6: renamed from eth4
[   13.818328] fsl_dpaa_mac 1af0000.ethernet e5: renamed from eth3
[   13.834177] fsl_dpaa_mac 1ae2000.ethernet e2: renamed from eth0
[   13.859247] mmc0: SDHCI controller on 1560000.mmc [1560000.mmc] using ADMA 64-bit
[   13.895510] fsl_dpaa_mac 1aea000.ethernet e4: renamed from eth2
[   13.940900] mmc0: new HS200 MMC card at address 0001
[   13.954130] mmcblk0: mmc0:0001 0IM20E 29.6 GiB
[   13.965023] xhci-hcd xhci-hcd.0.auto: xHCI Host Controller
[   13.965577]  mmcblk0: p1 p2 p3
[   13.970560] xhci-hcd xhci-hcd.0.auto: new USB bus registered, assigned bus number 1
[   13.974600] mmcblk0boot0: mmc0:0001 0IM20E 31.5 MiB
[   13.981377] xhci-hcd xhci-hcd.0.auto: hcc params 0x0220f66d hci version 0x100 quirks 0x0000008002000810
[   13.987829] mmcblk0boot1: mmc0:0001 0IM20E 31.5 MiB
[   13.995643] xhci-hcd xhci-hcd.0.auto: irq 60, io mem 0x02f00000
[   14.002008] mmcblk0rpmb: mmc0:0001 0IM20E 4.00 MiB, chardev (245:0)
[   14.006619] xhci-hcd xhci-hcd.0.auto: xHCI Host Controller
[   14.018195] xhci-hcd xhci-hcd.0.auto: new USB bus registered, assigned bus number 2
[   14.025889] xhci-hcd xhci-hcd.0.auto: Host supports USB 3.0 SuperSpeed
[   14.032837] usb usb1: New USB device found, idVendor=1d6b, idProduct=0002, bcdDevice= 6.06
[   14.041157] usb usb1: New USB device strings: Mfr=3, Product=2, SerialNumber=1
[   14.048408] usb usb1: Product: xHCI Host Controller
[   14.053306] usb usb1: Manufacturer: Linux 6.6.128-vyos xhci-hcd
[   14.059242] usb usb1: SerialNumber: xhci-hcd.0.auto
[   14.064870] hub 1-0:1.0: USB hub found
[   14.068732] hub 1-0:1.0: 1 port detected
[   14.073709] usb usb2: New USB device found, idVendor=1d6b, idProduct=0003, bcdDevice= 6.06
[   14.082021] usb usb2: New USB device strings: Mfr=3, Product=2, SerialNumber=1
[   14.089267] usb usb2: Product: xHCI Host Controller
[   14.094169] usb usb2: Manufacturer: Linux 6.6.128-vyos xhci-hcd
[   14.100116] usb usb2: SerialNumber: xhci-hcd.0.auto
[   14.105763] hub 2-0:1.0: USB hub found
[   14.109596] hub 2-0:1.0: 1 port detected
Begin: Loading essential drivers ... done.
Begin: Running /scripts/init-premount ... done.
Begin: Mounting root file system ... [   14.312836] usb 1-1: new high-speed USB device number 2 using xhci-hcd
[   14.445716] usb 1-1: New USB device found, idVendor=0781, idProduct=5581, bcdDevice= 1.00
[   14.453943] usb 1-1: New USB device strings: Mfr=1, Product=2, SerialNumber=3
[   14.461101] usb 1-1: Product:  SanDisk 3.2Gen1
[   14.465561] usb 1-1: Manufacturer:  USB
[   14.469413] usb 1-1: SerialNumber: 04018b1d82474c414895f765bcb5514cd3af5e1c9432ec412a34839eb5b8eccfb9cd000000000000000000002e4284a2ff105e188155810713b01e85
[   14.509009] SCSI subsystem initialized
[   14.521114] usb-storage 1-1:1.0: USB Mass Storage device detected
[   14.527820] scsi host0: usb-storage 1-1:1.0
[   14.532492] usbcore: registered new interface driver usb-storage
[   15.587998] scsi 0:0:0:0: Direct-Access      USB      SanDisk 3.2Gen1 1.00 PQ: 0 ANSI: 6
[   15.609666] sd 0:0:0:0: [sda] 240328704 512-byte logical blocks: (123 GB/115 GiB)
[   15.618153] sd 0:0:0:0: [sda] Write Protect is off
[   15.623417] sd 0:0:0:0: [sda] Write cache: disabled, read cache: enabled, doesn't support DPO or FUA
[   15.652018]  sda: sda1
[   15.654827] sd 0:0:0:0: [sda] Attached SCSI removable disk
[   16.624079] loop: module loaded
[   17.079161] loop0: detected capacity change from 0 to 1028024
[   17.121294] squashfs: version 4.0 (2009/01/31) Phillip Lougher
Begin: Running /scripts/live-realpremount ... done.
Begin: Mounting "/live/medium/live/filesystem.squashfs" on "//filesystem.squashfs" via "/dev/loop0" ... done.
done.
Begin: Running /scripts/init-bottom ... done.
[   20.931113] systemd[1]: System time before build time, advancing clock.
[   21.133544] systemd[1]: Inserted module 'autofs4'
[   21.452756] systemd[1]: systemd 252.39-1~deb12u1 running in system mode (+PAM +AUDIT +SELINUX +APPARMOR +IMA +SMACK +SECCOMP +GCRYPT -GNUTLS +OPENSSL +ACL +BLKID +CURL +ELFUTILS +FIDO2 +IDN2 -IDN +IPTC +KMOD +LIBCRYPTSETUP +LIBFDISK +PCRE2 -PWQUALITY +P11KIT +QRENCODE +TPM2 +BZIP2 +LZ4 +XZ +ZLIB +ZSTD -BPF_FRAMEWORK -XKBCOMMON +UTMP +SYSVINIT default-hierarchy=unified)
[   21.485544] systemd[1]: Detected architecture arm64.

Welcome to VyOS 2026.03.20-2209-rolling (current)!

[   21.539255] systemd[1]: Hostname set to <localhost.localdomain>.
[   21.584948] systemd[1]: Initializing machine ID from random generator.
[   21.660898] systemd[1]: memfd_create() called without MFD_EXEC or MFD_NOEXEC_SEAL set
[   24.253850] systemd[1]: Queued start job for default target Multi-User System.
[   24.273899] systemd[1]: Created slice Slice /system/getty.
[  OK  ] Created slice Slice /system/getty.
[   24.289949] systemd[1]: Created slice Slice /system/modprobe.
[  OK  ] Created slice Slice /system/modprobe.
[   24.305979] systemd[1]: Created slice Slice /system/serial-getty.
[  OK  ] Created slice Slice /system/serial-getty.
[   24.321565] systemd[1]: Created slice User and Session Slice.
[  OK  ] Created slice User and Session Slice.
[   24.336243] systemd[1]: Started Dispatch Password Requests to Console Directory Watch.
[  OK  ] Started Dispatch Password …ts to Console Directory Watch.
[   24.354175] systemd[1]: Started Forward Password Requests to Wall Directory Watch.
[  OK  ] Started Forward Password R…uests to Wall Directory Watch.
[   24.372627] systemd[1]: Set up automount Arbitrary Executable File Formats File System Automount Point.
[  OK  ] Set up automount Arbitrary…s File System Automount Point.
[   24.391982] systemd[1]: Reached target Local Encrypted Volumes.
[  OK  ] Reached target Local Encrypted Volumes.
[   24.405969] systemd[1]: Reached target Local Integrity Protected Volumes.
[  OK  ] Reached target Local Integrity Protected Volumes.
[   24.423007] systemd[1]: Reached target Remote File Systems.
[  OK  ] Reached target Remote File Systems.
[   24.436935] systemd[1]: Reached target Slice Units.
[  OK  ] Reached target Slice Units.
[   24.448971] systemd[1]: Reached target TLS tunnels for network services - per-config-file target.
[  OK  ] Reached target TLS tunnels…ices - per-config-file target.
[   24.467970] systemd[1]: Reached target Swaps.
[  OK  ] Reached target Swaps.
[   24.478971] systemd[1]: Reached target Local Verity Protected Volumes.
[  OK  ] Reached target Local Verity Protected Volumes.
[   24.495296] systemd[1]: Listening on initctl Compatibility Named Pipe.
[  OK  ] Listening on initctl Compatibility Named Pipe.
[   24.512669] systemd[1]: Listening on Journal Socket (/dev/log).
[  OK  ] Listening on Journal Socket (/dev/log).
[   24.527520] systemd[1]: Listening on Journal Socket.
[  OK  ] Listening on Journal Socket.
[   24.542333] systemd[1]: Listening on udev Control Socket.
[  OK  ] Listening on udev Control Socket.
[   24.556391] systemd[1]: Listening on udev Kernel Socket.
[  OK  ] Listening on udev Kernel Socket.
[   24.585220] systemd[1]: Mounting Huge Pages File System...
         Mounting Huge Pages File System...
[   24.603298] systemd[1]: Mounting POSIX Message Queue File System...
         Mounting POSIX Message Queue File System...
[   24.634181] systemd[1]: Mounting Kernel Debug File System...
         Mounting Kernel Debug File System...
[   24.652160] systemd[1]: Mounting Kernel Trace File System...
         Mounting Kernel Trace File System...
[   24.685548] systemd[1]: Starting Create List of Static Device Nodes...
         Starting Create List of Static Device Nodes...
[   24.706522] systemd[1]: Starting Load Kernel Module configfs...
         Starting Load Kernel Module configfs...
[   24.725516] systemd[1]: Starting Load Kernel Module dm_mod...
         Starting Load Kernel Module dm_mod...
[   24.743505] systemd[1]: Starting Load Kernel Module drm...
         Starting Load Kernel Module drm...
[   24.767477] systemd[1]: Starting Load Kernel Module efi_pstore...
         Starting Load Kernel Module efi_pstore...
[   24.798512] systemd[1]: Starting Load Kernel Module fuse...
         Starting Load Kernel Module fuse...
[   24.816637] systemd[1]: Starting Load Kernel Module loop...
         Starting Load Kernel Module loop...
[   24.846528] systemd[1]: Starting Journal Service...
         Starting Journal Service...
[   24.867128] fuse: init (API version 7.39)
[   24.873508] systemd[1]: Starting Load Kernel Modules...
         Starting Load Kernel Modules...
[   24.897466] systemd[1]: Starting Remount Root and Kernel File Systems...
         Starting Remount Root and Kernel File Systems...
[   24.924485] systemd[1]: Starting Coldplug All udev Devices...
         Starting Coldplug All udev Devices...
[   24.958438] systemd[1]: Mounted Huge Pages File System.
[  OK  ] Mounted Huge Pages File System.
[   24.972500] systemd[1]: Mounted POSIX Message Queue File System.
[  OK  ] Mounted POSIX Message Queue File System.
[   24.988599] systemd[1]: Mounted Kernel Debug File System.
[  OK  ] Mounted Kernel Debug File System.
[   25.002562] systemd[1]: Mounted Kernel Trace File System.
[  OK  ] Mounted Kernel Trace File System.
[   25.021527] systemd[1]: Finished Create List of Static Device Nodes.
[  OK  ] Finished Create List of Static Device Nodes.
[   25.037902] systemd[1]: modprobe@configfs.service: Deactivated successfully.
[   25.045883] systemd[1]: Finished Load Kernel Module configfs.
[  OK  ] Finished Load Kernel Module configfs.
[   25.062675] systemd[1]: modprobe@dm_mod.service: Deactivated successfully.
[   25.070611] systemd[1]: Finished Load Kernel Module dm_mod.
[  OK  ] Finished Load Kernel Module dm_mod.
[   25.087589] systemd[1]: modprobe@drm.service: Deactivated successfully.
[   25.095154] systemd[1]: Finished Load Kernel Module drm.
[  OK  ] Finished Load Kernel Module drm.
[   25.111583] systemd[1]: modprobe@efi_pstore.service: Deactivated successfully.
[   25.119751] systemd[1]: Finished Load Kernel Module efi_pstore.
[  OK  ] Finished Load Kernel Module efi_pstore.
[   25.134558] systemd[1]: Started Journal Service.
[  OK  ] Started Journal Service.
[  OK  ] Finished Load Kernel Module fuse.
[  OK  ] Finished Load Kernel Module loop.
[  OK  ] Finished Remount Root and Kernel File Systems.
[   25.182691] bridge: filtering via arp/ip/ip6tables is no longer available by default. Update your scripts to load br_netfilter if you need this.
[   25.201169] Bridge firewalling registered
         Mounting FUSE Control File System...
         Mounting Kernel Configuration File System...
         Starting Flush Journal to Persistent Storage...
         Starting Load/Save Random Seed...
[   25.316353] systemd-journald[347]: Received client request to flush runtime journal.
         Starting Create System Users...
[  OK  ] Started VyOS commit daemon.
[  OK  ] Started VyOS configuration daemon.
[  OK  ] Started VyOS DNS configuration keeper.
[  OK  ] Finished Load Kernel Modules.
[  OK  ] Mounted FUSE Control File System.
[  OK  ] Mounted Kernel Configuration File System.
[  OK  ] Finished Flush Journal to Persistent Storage.
[  OK  ] Finished Load/Save Random Seed.
         Starting Apply Kernel Variables...
[  OK  ] Finished Create System Users.
         Starting Create Static Device Nodes in /dev...
[  OK  ] Finished Coldplug All udev Devices.
[  OK  ] Finished Create Static Device Nodes in /dev.
[  OK  ] Reached target Preparation for Local File Systems.
         Mounting /tmp...
         Mounting /var/tmp...
         Starting Rule-based Manage…for Device Events and Files...
[  OK  ] Mounted /tmp.
[  OK  ] Finished Apply Kernel Variables.
[  OK  ] Mounted /var/tmp.
[  OK  ] Reached target Local File Systems.
         Starting Set Up Additional Binary Formats...
         Starting Create System Files and Directories...
         Mounting Arbitrary Executable File Formats File System...
[  OK  ] Mounted Arbitrary Executable File Formats File System.
[  OK  ] Finished Create System Files and Directories.
         Starting Security Auditing Service...
[  OK  ] Started Entropy Daemon based on the HAVEGE algorithm.
         Starting live-config conta…t process (late userspace)....
[  OK  ] Finished Set Up Additional Binary Formats.
[  OK  ] Started Rule-based Manager for Device Events and Files.
[  OK  ] Started Security Auditing Service.
         Starting Record System Boot/Shutdown in UTMP...
[  OK  ] Finished Record System Boot/Shutdown in UTMP.
[  OK  ] Reached target System Initialization.
[  OK  ] Started ACPI Events Check.
[  OK  ] Started Periodic ext4 Onli…ata Check for All Filesystems.
[  OK  ] Started Discard unused blocks once a week.
[  OK  ] Started Daily rotation of log files.
[  OK  ] Started Daily Cleanup of Temporary Directories.
[  OK  ] Reached target Path Units.
[  OK  ] Reached target Timer Units.
[  OK  ] Listening on ACPID Listen Socket.
[  OK  ] Listening on D-Bus System Message Bus Socket.
[  OK  ] Listening on Podman API Socket.
[  OK  ] Listening on UUID daemon activation socket.
[  OK  ] Reached target Socket Units.
[  OK  ] Finished live-config conta…oot process (late userspace)..
[  OK  ] Reached target Basic System.
[  OK  ] Started ACPI event daemon.
         Starting Deferred execution scheduler...
         Starting Atop process accounting daemon...
[  OK  ] Started Regular background program processing daemon.
         Starting D-Bus System Message Bus...
         Starting Remove Stale Onli…t4 Metadata Check Snapshots...
         Starting FastNetMon - DoS/…Flow/Netflow/mirror support...
         Starting LSB: Load kernel image with kexec...
         Starting Podman API Service...
         Starting User Login Management...
         Starting LSB: Start vmtouch daemon...
         Starting Update GRUB loader configuration structure...
[  OK  ] Started Deferred execution scheduler.
[  OK  ] Finished Remove Stale Onli…ext4 Metadata Check Snapshots.
[  OK  ] Started Podman API Service.
[  OK  ] Finished Update GRUB loader configuration structure.
[  OK  ] Started LSB: Load kernel image with kexec.
[  OK  ] Started VyOS Router.
         Starting Permit User Sessions...
[  OK  ] Finished Permit User Sessions.
[  OK  ] Started Getty on tty1.
[  OK  ] Started Serial Getty on ttyS0.
[  OK  ] Reached target Login Prompts.
[   61.486680] vyos-router[863]: Starting VyOS router.
[  OK  ] Started D-Bus System Message Bus.
[  OK  ] Started User Login Management.
[  OK  ] Started FastNetMon - DoS/D… sFlow/Netflow/mirror support.
[  OK  ] Reached target Multi-User System.
[  OK  ] Reached target VyOS target.
         Starting Record Runlevel Change in UTMP...
[  OK  ] Finished Record Runlevel Change in UTMP.
[   68.518974] vyos-router[863]: Waiting for NICs to settle down: settled in 0sec..
[   68.549920] vyos-router[863]: could not generate DUID ... failed!
[   99.098886] vyos-router[863]: Mounting VyOS Config...done.
[  115.570542] BUG: using smp_processor_id() in preemptible [00000000] code: python3/1463
[  115.661277] BUG: using smp_processor_id() in preemptible [00000000] code: python3/1463
[  115.748552] BUG: using smp_processor_id() in preemptible [00000000] code: python3/1463
[  115.837173] BUG: using smp_processor_id() in preemptible [00000000] code: python3/1463
[  115.926590] BUG: using smp_processor_id() in preemptible [00000000] code: python3/1463
[  125.254328] BUG: using smp_processor_id() in preemptible [00000000] code: python3/360
[  125.361530] BUG: using smp_processor_id() in preemptible [00000000] code: python3/360
[  125.509379] BUG: using smp_processor_id() in preemptible [00000000] code: python3/360
[  127.596003] BUG: using smp_processor_id() in preemptible [00000000] code: python3/360
[  127.695971] BUG: using smp_processor_id() in preemptible [00000000] code: python3/360
[  127.841743] BUG: using smp_processor_id() in preemptible [00000000] code: python3/360
[  129.834460] BUG: using smp_processor_id() in preemptible [00000000] code: python3/360
[  129.935840] BUG: using smp_processor_id() in preemptible [00000000] code: python3/360
[  130.082498] BUG: using smp_processor_id() in preemptible [00000000] code: python3/360
[  132.080055] BUG: using smp_processor_id() in preemptible [00000000] code: python3/360
[  132.179604] BUG: using smp_processor_id() in preemptible [00000000] code: python3/360
[  132.327029] BUG: using smp_processor_id() in preemptible [00000000] code: python3/360
[  134.299557] BUG: using smp_processor_id() in preemptible [00000000] code: python3/360
[  134.400991] BUG: using smp_processor_id() in preemptible [00000000] code: python3/360
[  134.548230] BUG: using smp_processor_id() in preemptible [00000000] code: python3/360
[  152.321690] vyos-config[2381]: Configuration success
[  152.434838] vyos-router[863]:  migrate system configure.

Welcome to VyOS - vyos ttyS0

vyos login: vyos
Password: 
Welcome to VyOS!

   ┌── ┐
   . VyOS 2026.03.20-2209-rolling
   └ ──┘  current

 * Documentation:  https://docs.vyos.io/en/latest
 * Project news:   https://blog.vyos.io
 * Bug reports:    https://vyos.dev

You can change this banner using "set system login banner post-login" command.

VyOS is a free software distribution that includes multiple components,
you can check individual component licenses under /usr/share/doc/*/copyright

vyos@vyos:~$
