=> usb start
starting USB...
Bus usb@2f00000: Register 200017f NbrPorts 2
Starting the controller
USB XHCI 1.00
scanning bus usb@2f00000 for devices... 2 USB Device(s) found
       scanning usb for storage devices... 1 Storage Device(s) found
=> fatls usb 0:1 live
            ./
            ../
    22623   filesystem.packages
        6   filesystem.packages-remove
 526319616   filesystem.squashfs
 33277271   initrd.img
 33277271   initrd.img-6.6.128-vyos
       20   packages.txt
  9208868   vmlinuz
  9208868   vmlinuz-6.6.128-vyos

8 file(s), 2 dir(s)

=> setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/sda1 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 quiet"; fatload usb 0:1 ${kernel_addr_r} live/vmlinuz-6.6.128-vyos; fatload usb 0:1 ${fdt_addr_r} mono-gw.dtb; fatload usb 0:1 ${ramdisk_addr_r} live/initrd.img-6.6.128-vyos; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
9208868 bytes read in 238 ms (36.9 MiB/s)
94208 bytes read in 5 ms (18 MiB/s)
33277271 bytes read in 821 ms (38.7 MiB/s)
   Uncompressing Kernel Image to 0
## Flattened Device Tree blob at 88000000
   Booting using the fdt blob at 0x88000000
Working FDT set to 88000000
   Loading Ramdisk to f8c44000, end fac00557 ... OK
   Loading Device Tree to 00000000f8c1a000, end 00000000f8c43fff ... OK
Working FDT set to f8c1a000
PCIe1: pcie@3400000 Root Complex: no link
PCIe2: pcie@3500000 disabled
PCIe3: pcie@3600000 Root Complex: no link
WARNING failed to get smmu node: FDT_ERR_NOTFOUND
WARNING failed to get smmu node: FDT_ERR_NOTFOUND

Starting kernel ...

[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x410fd082]
[    0.000000] Linux version 6.6.128-vyos (root@b6670225f2db) (gcc (Debian 12.2.0-14+deb12u1) 12.2.0, GNU ld (GNU Binutils for Debian) 2.40) #1 SMP PREEMPT_DYNAMIC Sat Mar 21 04:43:28 UTC 2026
[    0.000000] KASLR enabled
[    0.000000] Machine model: Mono Gateway Development Kit
[    0.000000] earlycon: uart8250 at MMIO 0x00000000021c0500 (options '')
[    0.000000] printk: bootconsole [uart8250] enabled

Welcome to VyOS 2026.03.21-0419-rolling (current)!

[  OK  ] Created slice Slice /system/getty.
[  OK  ] Created slice Slice /system/modprobe.
[  OK  ] Created slice Slice /system/serial-getty.
[  OK  ] Created slice User and Session Slice.
[  OK  ] Started Dispatch Password …ts to Console Directory Watch.
[  OK  ] Started Forward Password R…uests to Wall Directory Watch.
[  OK  ] Set up automount Arbitrary…s File System Automount Point.
[  OK  ] Reached target Local Encrypted Volumes.
[  OK  ] Reached target Local Integrity Protected Volumes.
[  OK  ] Reached target Path Units.
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
[  OK  ] Started Journal Service.
[  OK  ] Finished Load Kernel Module dm_mod.
[  OK  ] Finished Load Kernel Module drm.
[  OK  ] Finished Load Kernel Module efi_pstore.
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
[  OK  ] Started Periodic ext4 Onli…ata Check for All Filesystems.
[  OK  ] Started Discard unused blocks once a week.
[  OK  ] Started Daily rotation of log files.
[  OK  ] Started Daily Cleanup of Temporary Directories.
[  OK  ] Reached target Timer Units.
[  OK  ] Listening on D-Bus System Message Bus Socket.
[  OK  ] Listening on Podman API Socket.
[  OK  ] Listening on UUID daemon activation socket.
[  OK  ] Reached target Socket Units.
[  OK  ] Finished live-config conta…oot process (late userspace)..
[  OK  ] Reached target Basic System.
         Starting Deferred execution scheduler...
         Starting Atop process accounting daemon...
[  OK  ] Started Regular background program processing daemon.
         Starting D-Bus System Message Bus...
         Starting Remove Stale Onli…t4 Metadata Check Snapshots...
         Starting FastNetMon - DoS/…Flow/Netflow/mirror support...
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
[   26.849768] vyos-router[852]: Starting VyOS router.
[  OK  ] Started FastNetMon - DoS/D… sFlow/Netflow/mirror support.
[  OK  ] Reached target Multi-User System.
[  OK  ] Reached target VyOS target.
         Starting Record Runlevel Change in UTMP...
[  OK  ] Finished Record Runlevel Change in UTMP.
[   29.939034] vyos-router[852]: Waiting for NICs to settle down: settled in 0sec..
[   29.952518] vyos-router[852]: could not generate DUID ... failed!
[   44.051654] vyos-router[852]: Mounting VyOS Config...done.
[  OK  ] Removed slice Slice /system/modprobe.
[  OK  ] Stopped target Local Encrypted Volumes.
[  OK  ] Stopped target Local Integrity Protected Volumes.
[  OK  ] Stopped target Timer Units.
[  OK  ] Stopped Periodic ext4 Onli…ata Check for All Filesystems.
[  OK  ] Stopped Discard unused blocks once a week.
[  OK  ] Stopped Daily rotation of log files.
[  OK  ] Stopped Daily Cleanup of Temporary Directories.
[  OK  ] Stopped target System Time Synchronized.
[  OK  ] Stopped target System Time Set.
[  OK  ] Stopped target Local Verity Protected Volumes.
[  OK  ] Stopped target VyOS target.
[  OK  ] Stopped target Multi-User System.
[  OK  ] Stopped target Login Prompts.
[  OK  ] Stopped target TLS tunnels…ices - per-config-file target.
         Stopping Deferred execution scheduler...
         Stopping Atop advanced performance monitor...
         Stopping chrony, an NTP client/server...
         Stopping Regular background program processing daemon...
         Stopping D-Bus System Message Bus...
         Stopping FastNetMon - DoS/…Flow/Netflow/mirror support...
         Stopping Entropy Daemon based on the HAVEGE algorithm...
         Stopping System Logging Service...
         Stopping Set Up Additional Binary Formats...
         Stopping Hostname Service...
         Stopping Locale Service...
         Stopping User Login Management...
         Stopping Load/Save Random Seed...
         Stopping Record System Boot/Shutdown in UTMP...
         Stopping LSB: Start vmtouch daemon...
[  OK  ] Stopped Entropy Daemon based on the HAVEGE algorithm.
[  OK  ] Stopped Regular background program processing daemon.
[  OK  ] Stopped D-Bus System Message Bus.
[  OK  ] Stopped User Login Management.
[  OK  ] Stopped Deferred execution scheduler.
[  OK  ] Stopped Atop advanced performance monitor.
[  OK  ] Stopped Getty on tty1.
[  OK  ] Stopped FastNetMon - DoS/D… sFlow/Netflow/mirror support.
[  OK  ] Stopped Locale Service.
[  OK  ] Stopped Hostname Service.
[  OK  ] Stopped Serial Getty on ttyS0.
[  OK  ] Stopped System Logging Service.
[  OK  ] Stopped chrony, an NTP client/server.
[  OK  ] Stopped Set Up Additional Binary Formats.
[  OK  ] Stopped Load/Save Random Seed.
[  OK  ] Stopped Record System Boot/Shutdown in UTMP.
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
         Stopping FRRouting...
[  OK  ] Stopped Create System Files and Directories.
[  OK  ] Stopped target Local File Systems.
         Unmounting /config...
         Unmounting /etc/cni/net.d...
         Unmounting /etc/frr/frr.conf...
         Unmounting /opt/vyatta/config/tmp/new_config_1452...
         Unmounting /run/credentials/systemd-sysctl.service...
         Unmounting /run/credentials/systemd-sysusers.service...
         Unmounting /run/credential…temd-tmpfiles-setup.service...
         Unmounting /run/credential…-tmpfiles-setup-dev.service...
         Unmounting /usr/lib/live/mount/medium...
         Unmounting /usr/lib/live/mount/overlay...
         Unmounting /usr/lib/live/m…/rootfs/filesystem.squashfs...
[  OK  ] Unmounted /config.
[  OK  ] Unmounted /etc/cni/net.d.
[  OK  ] Unmounted /etc/frr/frr.conf.
[  OK  ] Unmounted /opt/vyatta/config/tmp/new_config_1452.
[  OK  ] Unmounted /run/credentials/systemd-sysctl.service.
[  OK  ] Unmounted /run/credentials/systemd-sysusers.service.
[  OK  ] Unmounted /run/credentials…ystemd-tmpfiles-setup.service.
[  OK  ] Unmounted /run/credentials…md-tmpfiles-setup-dev.service.
[FAILED] Failed unmounting /usr/lib/live/mount/medium.
[  OK  ] Unmounted /usr/lib/live/mount/overlay.
[  OK  ] Unmounted /usr/lib/live/mount/rootfs/filesystem.squashfs.
         Unmounting /opt/vyatta/config...
[FAILED] Failed unmounting /opt/vyatta/config.
[  OK  ] Stopped FRRouting.
[  OK  ] Stopped target Basic System.
[  OK  ] Stopped target Path Units.
[  OK  ] Stopped Dispatch Password …ts to Console Directory Watch.
[  OK  ] Stopped Forward Password R…uests to Wall Directory Watch.
[  OK  ] Stopped target Slice Units.
[  OK  ] Removed slice User and Session Slice.
[  OK  ] Stopped target Socket Units.
[  OK  ] Closed D-Bus System Message Bus Socket.
[  OK  ] Closed Podman API Socket.
[  OK  ] Closed Syslog Socket.
[  OK  ] Closed UUID daemon activation socket.
         Unmounting /tmp...
         Unmounting /var/tmp...
[  OK  ] Stopped Apply Kernel Variables.
[  OK  ] Stopped Load Kernel Modules.
[  OK  ] Unmounted /tmp.
[  OK  ] Unmounted /var/tmp.
[  OK  ] Stopped target Preparation for Local File Systems.
[  OK  ] Stopped target Swaps.
[  OK  ] Reached target Unmount All Filesystems.
[  OK  ] Stopped Create Static Device Nodes in /dev.
[  OK  ] Stopped Create System Users.
[  OK  ] Stopped Remount Root and Kernel File Systems.
[  OK  ] Reached target System Shutdown.
[  OK  ] Reached target Late Shutdown Services.
         Starting Reboot via kexec...
[   68.839244] (sd-umount)[2362]: Failed to unmount /usr/lib/live/mount/medium: Device or resource busy
[   68.852654] systemd-shutdown[1]: Could not detach loopback /dev/loop0: Device or resource busy
[   68.936005] systemd-shutdown[1]: Failed to finalize file systems, loop devices, ignoring.
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x410fd082]
[    0.000000] Linux version 6.6.128-vyos (root@b6670225f2db) (gcc (Debian 12.2.0-14+deb12u1) 12.2.0, GNU ld (GNU Binutils for Debian) 2.40) #1 SMP PREEMPT_DYNAMIC Sat Mar 21 04:43:28 UTC 2026
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
[    0.000000] NUMA: NODE_DATA [mem 0x9fb7f21c0-0x9fb7f5fff]
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
[    0.000000] Memory: 7979600K/8321024K available (11776K kernel code, 2422K rwdata, 5220K rodata, 4608K init, 597K bss, 341424K reserved, 0K cma-reserved)
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
[    0.008403] Console: colour dummy device 80x25
[    0.012949] Calibrating delay loop (skipped), value calculated using timer frequency.. 50.00 BogoMIPS (lpj=25000)
[    0.023285] pid_max: default: 32768 minimum: 301
[    0.028091] Mount-cache hash table entries: 16384 (order: 5, 131072 bytes, linear)
[    0.035732] Mountpoint-cache hash table entries: 16384 (order: 5, 131072 bytes, linear)
[    0.044646] RCU Tasks: Setting shift to 2 and lim to 1 rcu_task_cb_adjust=1 rcu_task_cpu_ids=4.
[    0.053456] RCU Tasks Trace: Setting shift to 2 and lim to 1 rcu_task_cb_adjust=1 rcu_task_cpu_ids=4.
[    0.062839] rcu: Hierarchical SRCU implementation.
[    0.067661] rcu:     Max phase no-delay instances is 400.
[    0.073318] EFI services will not be available.
[    0.078015] smp: Bringing up secondary CPUs ...
[    0.082872] Detected PIPT I-cache on CPU1
[    0.082930] CPU1: Booted secondary processor 0x0000000001 [0x410fd082]
[    0.083240] Detected PIPT I-cache on CPU2
[    0.083273] CPU2: Booted secondary processor 0x0000000002 [0x410fd082]
[    0.083557] Detected PIPT I-cache on CPU3
[    0.083590] CPU3: Booted secondary processor 0x0000000003 [0x410fd082]
[    0.083631] smp: Brought up 1 node, 4 CPUs
[    0.119537] SMP: Total of 4 processors activated.
[    0.124268] CPU features: detected: 32-bit EL0 Support
[    0.129437] CPU features: detected: CRC32 instructions
[    0.134647] CPU: All CPU(s) started at EL2
[    0.138768] alternatives: applying system-wide alternatives
[    0.145191] devtmpfs: initialized
[    0.153283] clocksource: jiffies: mask: 0xffffffff max_cycles: 0xffffffff, max_idle_ns: 1911260446275000 ns
[    0.163104] futex hash table entries: 1024 (order: 4, 65536 bytes, linear)
[    0.170119] pinctrl core: initialized pinctrl subsystem
[    0.175769] Machine: Mono Gateway Development Kit
[    0.180502] SoC family: QorIQ LS1046A
[    0.184183] SoC ID: svr:0x87070010, Revision: 1.0
[    0.189126] DMI not present or invalid.
[    0.193215] NET: Registered PF_NETLINK/PF_ROUTE protocol family
[    0.199531] DMA: preallocated 1024 KiB GFP_KERNEL pool for atomic allocations
[    0.206844] DMA: preallocated 1024 KiB GFP_KERNEL|GFP_DMA pool for atomic allocations
[    0.214849] DMA: preallocated 1024 KiB GFP_KERNEL|GFP_DMA32 pool for atomic allocations
[    0.222952] audit: initializing netlink subsys (disabled)
[    0.228455] audit: type=2000 audit(0.042:1): state=initialized audit_enabled=0 res=1
[    0.228908] thermal_sys: Registered thermal governor 'fair_share'
[    0.236255] thermal_sys: Registered thermal governor 'bang_bang'
[    0.242386] thermal_sys: Registered thermal governor 'step_wise'
[    0.248429] thermal_sys: Registered thermal governor 'user_space'
[    0.254499] cpuidle: using governor ladder
[    0.264757] cpuidle: using governor menu
[    0.268791] hw-breakpoint: found 6 breakpoint and 4 watchpoint registers.
[    0.275670] ASID allocator initialised with 32768 entries
[    0.281278] Serial: AMBA PL011 UART driver
[    0.297739] Modules: 2G module region forced by RANDOMIZE_MODULE_REGION_FULL
[    0.304837] Modules: 0 pages in range for non-PLT usage
[    0.304840] Modules: 518080 pages in range for PLT usage
[    0.310557] HugeTLB: registered 1.00 GiB page size, pre-allocated 0 pages
[    0.322734] HugeTLB: 0 KiB vmemmap can be freed for a 1.00 GiB page
[    0.329040] HugeTLB: registered 32.0 MiB page size, pre-allocated 0 pages
[    0.335870] HugeTLB: 0 KiB vmemmap can be freed for a 32.0 MiB page
[    0.342176] HugeTLB: registered 2.00 MiB page size, pre-allocated 0 pages
[    0.349005] HugeTLB: 0 KiB vmemmap can be freed for a 2.00 MiB page
[    0.355310] HugeTLB: registered 64.0 KiB page size, pre-allocated 0 pages
[    0.362139] HugeTLB: 0 KiB vmemmap can be freed for a 64.0 KiB page
[    0.385483] raid6: neonx8   gen()  4816 MB/s
[    0.406814] raid6: neonx4   gen()  4694 MB/s
[    0.428149] raid6: neonx2   gen()  3892 MB/s
[    0.449485] raid6: neonx1   gen()  2802 MB/s
[    0.470819] raid6: int64x8  gen()  2696 MB/s
[    0.492148] raid6: int64x4  gen()  2638 MB/s
[    0.513479] raid6: int64x2  gen()  2560 MB/s
[    0.534815] raid6: int64x1  gen()  1957 MB/s
[    0.539109] raid6: using algorithm neonx8 gen() 4816 MB/s
[    0.561572] raid6: .... xor() 3399 MB/s, rmw enabled
[    0.566566] raid6: using neon recovery algorithm
[    0.571560] ACPI: Interpreter disabled.
[    0.576704] iommu: Default domain type: Translated
[    0.581527] iommu: DMA domain TLB invalidation policy: lazy mode
[    0.587753] usbcore: registered new interface driver usbfs
[    0.593289] usbcore: registered new interface driver hub
[    0.598649] usbcore: registered new device driver usb
[    0.603858] pps_core: LinuxPPS API ver. 1 registered
[    0.608853] pps_core: Software ver. 5.3.6 - Copyright 2005-2007 Rodolfo Giometti <giometti@linux.it>
[    0.618049] PTP clock support registered
[    0.622133] EDAC MC: Ver: 3.0.0
[    0.625622] scmi_core: SCMI protocol bus registered
[    0.631214] clocksource: Switched to clocksource arch_sys_counter
[    0.637651] pnp: PnP ACPI: disabled
[    0.644443] NET: Registered PF_INET protocol family
[    0.649543] IP idents hash table entries: 131072 (order: 8, 1048576 bytes, linear)
[    0.660325] tcp_listen_portaddr_hash hash table entries: 4096 (order: 4, 65536 bytes, linear)
[    0.668945] Table-perturb hash table entries: 65536 (order: 6, 262144 bytes, linear)
[    0.676750] TCP established hash table entries: 65536 (order: 7, 524288 bytes, linear)
[    0.684982] TCP bind hash table entries: 65536 (order: 9, 2097152 bytes, linear)
[    0.693679] TCP: Hash tables configured (established 65536 bind 65536)
[    0.700430] MPTCP token hash table entries: 8192 (order: 6, 196608 bytes, linear)
[    0.708116] UDP hash table entries: 4096 (order: 5, 131072 bytes, linear)
[    0.715048] UDP-Lite hash table entries: 4096 (order: 5, 131072 bytes, linear)
[    0.722499] NET: Registered PF_UNIX/PF_LOCAL protocol family
[    0.728220] NET: Registered PF_XDP protocol family
[    0.733048] PCI: CLS 0 bytes, default 64
[    0.737200] Trying to unpack rootfs image as initramfs...
[    0.745000] Initialise system trusted keyrings
[    0.749651] workingset: timestamp_bits=40 max_order=21 bucket_order=0
[    0.756523] xor: measuring software checksum speed
[    0.761784]    8regs           :  7685 MB/sec
[    0.766573]    32regs          :  8301 MB/sec
[    0.771452]    arm64_neon      :  6663 MB/sec
[    0.775837] xor: using function: 32regs (8301 MB/sec)
[    0.780926] async_tx: api initialized (async)
[    0.785315] Key type asymmetric registered
[    0.789443] Asymmetric key parser 'x509' registered
[    0.794410] Block layer SCSI generic (bsg) driver version 0.4 loaded (major 248)
[    0.801865] io scheduler mq-deadline registered
[    0.806430] io scheduler kyber registered
[    0.810515] io scheduler bfq registered
[    0.823019] shpchp: Standard Hot Plug PCI Controller Driver version: 0.4
[    0.834987] bman_ccsr: BMan BAR already configured
[    0.841205] bman_portal 508000000.bman-portal: Portal initialised, cpu 0
[    0.848162] bman_portal 508010000.bman-portal: Portal initialised, cpu 1
[    0.855074] bman_portal 508020000.bman-portal: Portal initialised, cpu 2
[    0.861995] bman_portal 508030000.bman-portal: Portal initialised, cpu 3
[    0.873137] qman_portal 500000000.qman-portal: Portal initialised, cpu 0
[    0.880064] qman_portal 500010000.qman-portal: Portal initialised, cpu 1
[    0.886981] qman_portal 500020000.qman-portal: Portal initialised, cpu 2
[    0.894264] qman_portal 500030000.qman-portal: Portal initialised, cpu 3
[    1.309283] Freeing initrd memory: 32496K
[   12.046488] Serial: 8250/16550 driver, 4 ports, IRQ sharing enabled
[   12.054174] printk: console [ttyS0] disabled
[   12.058625] 21c0500.serial: ttyS0 at MMIO 0x21c0500 (irq = 52, base_baud = 18750000) is a 16550A
[   12.067488] printk: console [ttyS0] enabled
[   12.067488] printk: console [ttyS0] enabled
[   12.075877] printk: bootconsole [uart8250] disabled
[   12.075877] printk: bootconsole [uart8250] disabled
[   12.086085] 21c0600.serial: ttyS1 at MMIO 0x21c0600 (irq = 52, base_baud = 18750000) is a 16550A
[   12.094951] serial serial0: tty port ttyS1 registered
[   12.194544] fsl_dpaa_mac 1ae2000.ethernet: FMan MEMAC
[   12.199615] fsl_dpaa_mac 1ae2000.ethernet: FMan MAC address: e8:f6:d7:00:16:01
[   12.207103] fsl_dpaa_mac 1ae8000.ethernet: FMan MEMAC
[   12.212160] fsl_dpaa_mac 1ae8000.ethernet: FMan MAC address: e8:f6:d7:00:15:ff
[   12.219578] fsl_dpaa_mac 1aea000.ethernet: FMan MEMAC
[   12.224634] fsl_dpaa_mac 1aea000.ethernet: FMan MAC address: e8:f6:d7:00:16:00
[   12.232048] fsl_dpaa_mac 1af0000.ethernet: FMan MEMAC
[   12.237103] fsl_dpaa_mac 1af0000.ethernet: FMan MAC address: e8:f6:d7:00:16:02
[   12.244526] fsl_dpaa_mac 1af2000.ethernet: FMan MEMAC
[   12.249581] fsl_dpaa_mac 1af2000.ethernet: FMan MAC address: e8:f6:d7:00:16:03
[   12.275503] fsl_dpaa_mac 1ae2000.ethernet eth0: Probed interface eth0
[   12.300843] fsl_dpaa_mac 1ae8000.ethernet eth1: Probed interface eth1
[   12.326306] fsl_dpaa_mac 1aea000.ethernet eth2: Probed interface eth2
[   12.351870] fsl_dpaa_mac 1af0000.ethernet eth3: Probed interface eth3
[   12.377688] fsl_dpaa_mac 1af2000.ethernet eth4: Probed interface eth4
[   12.384729] ptp_qoriq: device tree node missing required elements, try automatic configuration
[   12.394138] device-mapper: uevent: version 1.0.3
[   12.398860] device-mapper: ioctl: 4.48.0-ioctl (2023-03-01) initialised: dm-devel@redhat.com
[   12.407856] qoriq-cpufreq qoriq-cpufreq: Freescale QorIQ CPU frequency scaling driver
[   12.416141] ledtrig-cpu: registered to indicate activity on CPUs
[   12.422653] SMCCC: SOC_ID: ARCH_SOC_ID not implemented, skipping ....
[   12.429837] hw perfevents: enabled with armv8_cortex_a72 PMU driver, 7 counters available
[   12.438381] drop_monitor: Initializing network drop monitor service
[   12.444903] NET: Registered PF_INET6 protocol family
[   12.474953] Segment Routing with IPv6
[   12.478648] In-situ OAM (IOAM) with IPv6
[   12.482611] mip6: Mobile IPv6
[   12.485665] Key type dns_resolver registered
[   12.489951] mpls_gso: MPLS GSO support
[   12.497255] registered taskstats version 1
[   12.501454] Loading compiled-in X.509 certificates
[   12.519766] Loaded X.509 cert 'VyOS Networks build time autogenerated Kernel key: 80adeb2bc43a1974dd18bfb8d872a83e0e249f20'
[   12.543903] Loaded X.509 cert 'VyOS LS1046A Secure Boot CA: ed9ff86ac8d3dc1144144291a885ffd7bcd198db'
[   12.558610] clk: Disabling unused clocks
[   12.564136] Freeing unused kernel memory: 4608K
[   12.608077] Checked W+X mappings: passed, no W+X pages found
[   12.613755] Run /init as init process
Loading, please wait...
Starting systemd-udevd version 252.39-1~deb12u1
[   12.883617] fsl_dpaa_mac 1af0000.ethernet e5: renamed from eth3
[   12.909742] fsl_dpaa_mac 1ae8000.ethernet e3: renamed from eth1
[   12.913145] sdhci: Secure Digital Host Controller Interface driver
[   12.921903] sdhci: Copyright(c) Pierre Ossman
[   12.928390] sdhci-pltfm: SDHCI platform and OF driver helper
[   12.931860] fsl_dpaa_mac 1ae2000.ethernet e2: renamed from eth0
[   12.951946] fsl_dpaa_mac 1af2000.ethernet e6: renamed from eth4
[   12.966379] fsl_dpaa_mac 1aea000.ethernet e4: renamed from eth2
[   13.012744] mmc0: SDHCI controller on 1560000.mmc [1560000.mmc] using ADMA 64-bit
[   13.031856] xhci-hcd xhci-hcd.0.auto: xHCI Host Controller
[   13.037381] xhci-hcd xhci-hcd.0.auto: new USB bus registered, assigned bus number 1
[   13.045112] xhci-hcd xhci-hcd.0.auto: hcc params 0x0220f66d hci version 0x100 quirks 0x0000008002000810
[   13.054548] xhci-hcd xhci-hcd.0.auto: irq 60, io mem 0x02f00000
[   13.060563] xhci-hcd xhci-hcd.0.auto: xHCI Host Controller
[   13.066057] xhci-hcd xhci-hcd.0.auto: new USB bus registered, assigned bus number 2
[   13.073722] xhci-hcd xhci-hcd.0.auto: Host supports USB 3.0 SuperSpeed
[   13.080425] usb usb1: New USB device found, idVendor=1d6b, idProduct=0002, bcdDevice= 6.06
[   13.088711] usb usb1: New USB device strings: Mfr=3, Product=2, SerialNumber=1
[   13.095943] usb usb1: Product: xHCI Host Controller
[   13.100826] usb usb1: Manufacturer: Linux 6.6.128-vyos xhci-hcd
[   13.106751] usb usb1: SerialNumber: xhci-hcd.0.auto
[   13.111924] hub 1-0:1.0: USB hub found
[   13.115703] hub 1-0:1.0: 1 port detected
[   13.119704] mmc0: new HS200 MMC card at address 0001
[   13.120033] usb usb2: New USB device found, idVendor=1d6b, idProduct=0003, bcdDevice= 6.06
[   13.132951] usb usb2: New USB device strings: Mfr=3, Product=2, SerialNumber=1
[   13.140187] usb usb2: Product: xHCI Host Controller
[   13.140762] mmcblk0: mmc0:0001 0IM20E 29.6 GiB
[   13.145069] usb usb2: Manufacturer: Linux 6.6.128-vyos xhci-hcd
[   13.145072] usb usb2: SerialNumber: xhci-hcd.0.auto
[   13.145389] hub 2-0:1.0: USB hub found
[   13.154970]  mmcblk0: p1 p2 p3
[   13.155479] hub 2-0:1.0: 1 port detected
[   13.160768] mmcblk0boot0: mmc0:0001 0IM20E 31.5 MiB
[   13.176765] mmcblk0boot1: mmc0:0001 0IM20E 31.5 MiB
[   13.182356] mmcblk0rpmb: mmc0:0001 0IM20E 4.00 MiB, chardev (245:0)
Begin: Loading essential drivers ... done.
Begin: Running /scripts/init-premount ... done.
Begin: Mounting root file system ... [   13.358261] usb 1-1: new high-speed USB device number 2 using xhci-hcd
[   13.490891] usb 1-1: New USB device found, idVendor=0781, idProduct=5581, bcdDevice= 1.00
[   13.499087] usb 1-1: New USB device strings: Mfr=1, Product=2, SerialNumber=3
[   13.506227] usb 1-1: Product: Ultra
[   13.509716] usb 1-1: Manufacturer: SanDisk
[   13.513814] usb 1-1: SerialNumber: 4C531001600621100051
[   13.530304] SCSI subsystem initialized
[   13.537761] usb-storage 1-1:1.0: USB Mass Storage device detected
[   13.544130] scsi host0: usb-storage 1-1:1.0
[   13.548540] usbcore: registered new interface driver usb-storage
[   14.556170] scsi 0:0:0:0: Direct-Access     SanDisk  Ultra            1.00 PQ: 0 ANSI: 6
[   14.569538] sd 0:0:0:0: [sda] 121307136 512-byte logical blocks: (62.1 GB/57.8 GiB)
[   14.577553] sd 0:0:0:0: [sda] Write Protect is off
[   14.582708] sd 0:0:0:0: [sda] Write cache: disabled, read cache: enabled, doesn't support DPO or FUA
[   14.605457]  sda: sda1
[   14.607997] sd 0:0:0:0: [sda] Attached SCSI removable disk
[   15.526482] loop: module loaded
[   15.752900] loop0: detected capacity change from 0 to 1027968
[   15.778124] squashfs: version 4.0 (2009/01/31) Phillip Lougher
done.
Begin: Running /scripts/init-bottom ... done.
[   17.415258] systemd[1]: System time before build time, advancing clock.
[   17.511295] systemd[1]: Inserted module 'autofs4'
[   17.662322] systemd[1]: systemd 252.39-1~deb12u1 running in system mode (+PAM +AUDIT +SELINUX +APPARMOR +IMA +SMACK +SECCOMP +GCRYPT -GNUTLS +OPENSSL +ACL +BLKID +CURL +ELFUTILS +FIDO2 +IDN2 -IDN +IPTC +KMOD +LIBCRYPTSETUP +LIBFDISK +PCRE2 -PWQUALITY +P11KIT +QRENCODE +TPM2 +BZIP2 +LZ4 +XZ +ZLIB +ZSTD -BPF_FRAMEWORK -XKBCOMMON +UTMP +SYSVINIT default-hierarchy=unified)
[   17.695050] systemd[1]: Detected architecture arm64.

Welcome to VyOS 2026.03.21-0419-rolling (current)!

[   17.724931] systemd[1]: Hostname set to <localhost.localdomain>.
[   17.748177] systemd[1]: Initializing machine ID from random generator.
[   17.786171] systemd[1]: memfd_create() called without MFD_EXEC or MFD_NOEXEC_SEAL set
[   18.905194] systemd[1]: Queued start job for default target Multi-User System.
[   18.920636] systemd[1]: Created slice Slice /system/getty.
[  OK  ] Created slice Slice /system/getty.
[   18.935066] systemd[1]: Created slice Slice /system/modprobe.
[  OK  ] Created slice Slice /system/modprobe.
[   18.950077] systemd[1]: Created slice Slice /system/serial-getty.
[  OK  ] Created slice Slice /system/serial-getty.
[   18.964884] systemd[1]: Created slice User and Session Slice.
[  OK  ] Created slice User and Session Slice.
[   18.978380] systemd[1]: Started Dispatch Password Requests to Console Directory Watch.
[  OK  ] Started Dispatch Password …ts to Console Directory Watch.
[   18.996356] systemd[1]: Started Forward Password Requests to Wall Directory Watch.
[  OK  ] Started Forward Password R…uests to Wall Directory Watch.
[   19.013555] systemd[1]: Set up automount Arbitrary Executable File Formats File System Automount Point.
[  OK  ] Set up automount Arbitrary…s File System Automount Point.
[   19.032289] systemd[1]: Reached target Local Encrypted Volumes.
[  OK  ] Reached target Local Encrypted Volumes.
[   19.046277] systemd[1]: Reached target Local Integrity Protected Volumes.
[  OK  ] Reached target Local Integrity Protected Volumes.
[   19.062291] systemd[1]: Reached target Path Units.
[  OK  ] Reached target Path Units.
[   19.074280] systemd[1]: Reached target Remote File Systems.
[  OK  ] Reached target Remote File Systems.
[   19.088267] systemd[1]: Reached target Slice Units.
[  OK  ] Reached target Slice Units.
[   19.100296] systemd[1]: Reached target TLS tunnels for network services - per-config-file target.
[  OK  ] Reached target TLS tunnels…ices - per-config-file target.
[   19.118276] systemd[1]: Reached target Swaps.
[  OK  ] Reached target Swaps.
[   19.129278] systemd[1]: Reached target Local Verity Protected Volumes.
[  OK  ] Reached target Local Verity Protected Volumes.
[   19.145404] systemd[1]: Listening on initctl Compatibility Named Pipe.
[  OK  ] Listening on initctl Compatibility Named Pipe.
[   19.161564] systemd[1]: Listening on Journal Socket (/dev/log).
[  OK  ] Listening on Journal Socket (/dev/log).
[   19.175515] systemd[1]: Listening on Journal Socket.
[  OK  ] Listening on Journal Socket.
[   19.188790] systemd[1]: Listening on udev Control Socket.
[  OK  ] Listening on udev Control Socket.
[   19.202452] systemd[1]: Listening on udev Kernel Socket.
[  OK  ] Listening on udev Kernel Socket.
[   19.226394] systemd[1]: Mounting Huge Pages File System...
         Mounting Huge Pages File System...
[   19.240485] systemd[1]: Mounting POSIX Message Queue File System...
         Mounting POSIX Message Queue File System...
[   19.257477] systemd[1]: Mounting Kernel Debug File System...
         Mounting Kernel Debug File System...
[   19.279417] systemd[1]: Mounting Kernel Trace File System...
         Mounting Kernel Trace File System...
[   19.296778] systemd[1]: Starting Create List of Static Device Nodes...
         Starting Create List of Static Device Nodes...
[   19.321565] systemd[1]: Starting Load Kernel Module configfs...
         Starting Load Kernel Module configfs...
[   19.337745] systemd[1]: Starting Load Kernel Module dm_mod...
         Starting Load Kernel Module dm_mod...
[   19.360552] systemd[1]: Starting Load Kernel Module drm...
         Starting Load Kernel Module drm...
[   19.376555] systemd[1]: Starting Load Kernel Module efi_pstore...
         Starting Load Kernel Module efi_pstore...
[   19.402569] systemd[1]: Starting Load Kernel Module fuse...
         Starting Load Kernel Module fuse...
[   19.417645] systemd[1]: Starting Load Kernel Module loop...
         Starting Load Kernel Module loop...
[   19.439130] fuse: init (API version 7.39)
[   19.444644] systemd[1]: Starting Journal Service...
         Starting Journal Service...
[   19.459995] systemd[1]: Starting Load Kernel Modules...
         Starting Load Kernel Modules...
[   19.474872] systemd[1]: Starting Remount Root and Kernel File Systems...
         Starting Remount Root and Kernel File Systems...
[   19.501525] systemd[1]: Starting Coldplug All udev Devices...
         Starting Coldplug All udev Devices...
[   19.520091] systemd[1]: Mounted Huge Pages File System.
[  OK  ] Mounted Huge Pages File System.
[   19.533667] systemd[1]: Mounted POSIX Message Queue File System.
[  OK  ] Mounted POSIX Message Queue File System.
[   19.551155] systemd[1]: Started Journal Service.
[  OK  ] Started Journal Service.
[  OK  ] Mounted Kernel Debug File System.
[  OK  ] Mounted Kernel Trace File System.
[  OK  ] Finished Create List of Static Device Nodes.
[  OK  ] Finished Load Kernel Module configfs.
[  OK  ] Finished Load Kernel Module dm_mod.
[   19.609600] bridge: filtering via arp/ip/ip6tables is no longer available by default. Update your scripts to load br_netfilter if you need this.
[  OK     19.625370] Bridge firewalling registered
0m] Finished Load Kernel Module drm.
[  OK  ] Finished Load Kernel Module efi_pstore.
[  OK  ] Finished Load Kernel Module fuse.
[  OK  ] Finished Load Kernel Module loop.
[  OK  ] Finished Remount Root and Kernel File Systems.
[  OK  ] Finished Load Kernel Modules.
         Mounting FUSE Control File System...
         Mounting Kernel Configuration File System...
         Starting Flush Journal to Persistent Storage...
         Starting Load/Save Random Seed...
[   19.757530] systemd-journald[348]: Received client request to flush runtime journal.
         Starting Apply Kernel Variables...
         Starting Create System Users...
[  OK  ] Started VyOS commit daemon.
[  OK  ] Started VyOS configuration daemon.
[  OK  ] Started VyOS DNS configuration keeper.
[  OK  ] Finished Coldplug All udev Devices.
[  OK  ] Mounted FUSE Control File System.
[  OK  ] Mounted Kernel Configuration File System.
[  OK  ] Finished Flush Journal to Persistent Storage.
[  OK  ] Finished Load/Save Random Seed.
[  OK  ] Finished Create System Users.
[  OK  ] Finished Apply Kernel Variables.
         Starting Create Static Device Nodes in /dev...
[  OK  ] Finished Create Static Device Nodes in /dev.
[  OK  ] Reached target Preparation for Local File Systems.
         Mounting /tmp...
         Mounting /var/tmp...
         Starting Rule-based Manage…for Device Events and Files...
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
[  OK  ] Started Periodic ext4 Onli…ata Check for All Filesystems.
[  OK  ] Started Discard unused blocks once a week.
[  OK  ] Started Daily rotation of log files.
[  OK  ] Started Daily Cleanup of Temporary Directories.
[  OK  ] Reached target Timer Units.
[  OK  ] Listening on D-Bus System Message Bus Socket.
[  OK  ] Listening on Podman API Socket.
[  OK  ] Listening on UUID daemon activation socket.
[  OK  ] Reached target Socket Units.
[  OK  ] Finished live-config conta…oot process (late userspace)..
[  OK  ] Reached target Basic System.
         Starting Deferred execution scheduler...
         Starting Atop process accounting daemon...
[  OK  ] Started Regular background program processing daemon.
         Starting D-Bus System Message Bus...
         Starting Remove Stale Onli…t4 Metadata Check Snapshots...
         Starting FastNetMon - DoS/…Flow/Netflow/mirror support...
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
[  OK  ] Finished Permit User Sessions.
[  OK  ] Started Getty on tty1.
[  OK    OK  ] Reached target Login Prompts.
[  OK  ] Started D-Bus System Message Bus.
[  OK  ] Started User Login Management.
[   38.092931] vyos-router[856]: Starting VyOS router.
[  OK  ] Started FastNetMon - DoS/D… sFlow/Netflow/mirror support.
[  OK  ] Reached target Multi-User System.
[  OK  ] Reached target VyOS target.
         Starting Record Runlevel Change in UTMP...
[  OK  ] Finished Record Runlevel Change in UTMP.
[   41.231915] vyos-router[856]: Waiting for NICs to settle down: settled in 0sec..
[   41.247296] vyos-router[856]: could not generate DUID ... failed!
[   56.389318] vyos-router[856]: Mounting VyOS Config...done.
[   81.284448] vyos-router[856]:  migrate system configure.
[   81.809709] vyos-config[861]: Configuration success

Welcome to VyOS - vyos ttyS0

vyos login: 