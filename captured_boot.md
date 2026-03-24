Starting kernel ...

[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x410fd082]
[    0.000000] Linux version 6.6.128-vyos (root@3b36e06c53a4) (gcc (Debian 12.2.0-14+deb12u1) 12.2.0, GNU ld (GNU Binutils for Debian) 2.40) #1 SMP PREEMPT_DYNAMIC Tue Mar 24 02:47:49 UTC 2026
[    0.000000] KASLR enabled
[    0.000000] Machine model: Mono Gateway Development Kit
[    0.000000] earlycon: uart8250 at MMIO 0x00000000021c0500 (options '')
[    0.000000] printk: bootconsole [uart8250] enabled
[    0.000000] efi: UEFI not found.
[    0.000000] Reserved memory: created DMA memory pool at 0x00000009ff000000, size 16 MiB
[    0.000000] OF: reserved mem: initialized node bman-fbpr, compatible id shared-dma-pool
[    0.000000] OF: reserved mem: 0x00000009ff000000..0x00000009ffffffff (16384 KiB) nomap non-reusable bman-fbpr
[    0.000000] Reserved memory: created DMA memory pool at 0x00000009fe800000, size 8 MiB
[    0.000000] OF: reserved mem: initialized node qman-fqd, compatible id shared-dma-pool
[    0.000000] OF: reserved mem: 0x00000009fe800000..0x00000009feffffff (8192 KiB) nomap non-reusable qman-fqd
[    0.000000] Reserved memory: created DMA memory pool at 0x00000009fc000000, size 32 MiB
[    0.000000] OF: reserved mem: initialized node qman-pfdr, compatible id shared-dma-pool
[    0.000000] OF: reserved mem: 0x00000009fc000000..0x00000009fdffffff (32768 KiB) nomap non-reusable qman-pfdr
[    0.000000] NUMA: No NUMA configuration found
[    0.000000] NUMA: Faking a node at [mem 0x0000000080000000-0x00000009ffffffff]
[    0.000000] NUMA: NODE_DATA [mem 0x9fb7fd1c0-0x9fb800fff]
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
[    0.000000] Kernel command line: console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin fsl_dpaa_fman.fsl_fm_max_frm=9600 vyos-union=/boot/2026.03.24-0223-rolling
[    0.000000] Unknown kernel command line parameters "noautologin boot=live vyos-union=/boot/2026.03.24-0223-rolling", will be passed to user space.
[    0.000000] Dentry cache hash table entries: 1048576 (order: 11, 8388608 bytes, linear)
[    0.000000] Inode-cache hash table entries: 524288 (order: 10, 4194304 bytes, linear)
[    0.000000] Fallback order for Node 0: 0 
[    0.000000] Built 1 zonelists, mobility grouping on.  Total pages: 2047488
[    0.000000] Policy zone: Normal
[    0.000000] mem auto-init: stack:off, heap alloc:off, heap free:off
[    0.000000] software IO TLB: area num 4.
[    0.000000] software IO TLB: mapped [mem 0x00000000f4c28000-0x00000000f8c28000] (64MB)
[    0.000000] Memory: 7979588K/8321024K available (11840K kernel code, 2450K rwdata, 5256K rodata, 4608K init, 597K bss, 341436K reserved, 0K cma-reserved)
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
[    0.008432] Console: colour dummy device 80x25
[    0.013733] Calibrating delay loop (skipped), value calculated using timer frequency.. 50.00 BogoMIPS (lpj=25000)
[    0.024070] pid_max: default: 32768 minimum: 301
[    0.030751] Mount-cache hash table entries: 16384 (order: 5, 131072 bytes, linear)
[    0.038391] Mountpoint-cache hash table entries: 16384 (order: 5, 131072 bytes, linear)
[    0.048517] RCU Tasks: Setting shift to 2 and lim to 1 rcu_task_cb_adjust=1 rcu_task_cpu_ids=4.
[    0.057330] RCU Tasks Trace: Setting shift to 2 and lim to 1 rcu_task_cb_adjust=1 rcu_task_cpu_ids=4.
[    0.066730] rcu: Hierarchical SRCU implementation.
[    0.071552] rcu:     Max phase no-delay instances is 400.
[    0.077215] EFI services will not be available.
[    0.081935] smp: Bringing up secondary CPUs ...
[    0.086840] Detected PIPT I-cache on CPU1
[    0.086886] CPU1: Booted secondary processor 0x0000000001 [0x410fd082]
[    0.087240] Detected PIPT I-cache on CPU2
[    0.087273] CPU2: Booted secondary processor 0x0000000002 [0x410fd082]
[    0.087630] Detected PIPT I-cache on CPU3
[    0.087664] CPU3: Booted secondary processor 0x0000000003 [0x410fd082]
[    0.087709] smp: Brought up 1 node, 4 CPUs
[    0.123616] SMP: Total of 4 processors activated.
[    0.128348] CPU features: detected: 32-bit EL0 Support
[    0.133516] CPU features: detected: CRC32 instructions
[    0.138725] CPU: All CPU(s) started at EL2
[    0.142845] alternatives: applying system-wide alternatives
[    0.149431] devtmpfs: initialized
[    0.157643] clocksource: jiffies: mask: 0xffffffff max_cycles: 0xffffffff, max_idle_ns: 1911260446275000 ns
[    0.167459] futex hash table entries: 1024 (order: 4, 65536 bytes, linear)
[    0.174618] pinctrl core: initialized pinctrl subsystem
[    0.180218] Machine: Mono Gateway Development Kit
[    0.184951] SoC family: QorIQ LS1046A
[    0.188632] SoC ID: svr:0x87070010, Revision: 1.0
[    0.193601] DMI not present or invalid.
[    0.198049] NET: Registered PF_NETLINK/PF_ROUTE protocol family
[    0.204367] DMA: preallocated 1024 KiB GFP_KERNEL pool for atomic allocations
[    0.211678] DMA: preallocated 1024 KiB GFP_KERNEL|GFP_DMA pool for atomic allocations
[    0.219683] DMA: preallocated 1024 KiB GFP_KERNEL|GFP_DMA32 pool for atomic allocations
[    0.228293] audit: initializing netlink subsys (disabled)
[    0.233805] audit: type=2000 audit(0.046:1): state=initialized audit_enabled=0 res=1
[    0.234253] thermal_sys: Registered thermal governor 'fair_share'
[    0.241605] thermal_sys: Registered thermal governor 'bang_bang'
[    0.247736] thermal_sys: Registered thermal governor 'step_wise'
[    0.253779] thermal_sys: Registered thermal governor 'user_space'
[    0.259849] cpuidle: using governor ladder
[    0.270107] cpuidle: using governor menu
[    0.274130] hw-breakpoint: found 6 breakpoint and 4 watchpoint registers.
[    0.281008] ASID allocator initialised with 32768 entries
[    0.286981] Serial: AMBA PL011 UART driver
[    0.299105] Modules: 2G module region forced by RANDOMIZE_MODULE_REGION_FULL
[    0.306206] Modules: 0 pages in range for non-PLT usage
[    0.306209] Modules: 518064 pages in range for PLT usage
[    0.312037] HugeTLB: registered 1.00 GiB page size, pre-allocated 0 pages
[    0.324218] HugeTLB: 0 KiB vmemmap can be freed for a 1.00 GiB page
[    0.330525] HugeTLB: registered 32.0 MiB page size, pre-allocated 0 pages
[    0.337355] HugeTLB: 0 KiB vmemmap can be freed for a 32.0 MiB page
[    0.343661] HugeTLB: registered 2.00 MiB page size, pre-allocated 0 pages
[    0.350490] HugeTLB: 0 KiB vmemmap can be freed for a 2.00 MiB page
[    0.356796] HugeTLB: registered 64.0 KiB page size, pre-allocated 0 pages
[    0.363625] HugeTLB: 0 KiB vmemmap can be freed for a 64.0 KiB page
[    0.387982] raid6: neonx8   gen()  4816 MB/s
[    0.409314] raid6: neonx4   gen()  4705 MB/s
[    0.430649] raid6: neonx2   gen()  3905 MB/s
[    0.451984] raid6: neonx1   gen()  2825 MB/s
[    0.473316] raid6: int64x8  gen()  2696 MB/s
[    0.494648] raid6: int64x4  gen()  2638 MB/s
[    0.515975] raid6: int64x2  gen()  2559 MB/s
[    0.537310] raid6: int64x1  gen()  1957 MB/s
[    0.541606] raid6: using algorithm neonx8 gen() 4816 MB/s
[    0.564071] raid6: .... xor() 3380 MB/s, rmw enabled
[    0.569064] raid6: using neon recovery algorithm
[    0.573945] ACPI: Interpreter disabled.
[    0.579101] iommu: Default domain type: Translated
[    0.583924] iommu: DMA domain TLB invalidation policy: lazy mode
[    0.590236] usbcore: registered new interface driver usbfs
[    0.595774] usbcore: registered new interface driver hub
[    0.601134] usbcore: registered new device driver usb
[    0.606477] imx-i2c 2180000.i2c: can't get pinctrl, bus recovery not supported
[    0.613859] i2c i2c-0: IMX I2C adapter registered
[    0.618611] i2c i2c-0: using dma0chan16 (tx) and dma0chan17 (rx) for DMA transfers
[    0.626319] imx-i2c 2190000.i2c: can't get pinctrl, bus recovery not supported
[    0.633675] i2c i2c-1: IMX I2C adapter registered
[    0.638506] imx-i2c 21a0000.i2c: can't get pinctrl, bus recovery not supported
[    0.645882] i2c i2c-2: IMX I2C adapter registered
[    0.650719] imx-i2c 21b0000.i2c: can't get pinctrl, bus recovery not supported
[    0.658047] i2c i2c-3: IMX I2C adapter registered
[    0.662844] pps_core: LinuxPPS API ver. 1 registered
[    0.667839] pps_core: Software ver. 5.3.6 - Copyright 2005-2007 Rodolfo Giometti <giometti@linux.it>
[    0.677043] PTP clock support registered
[    0.681136] EDAC MC: Ver: 3.0.0
[    0.684566] scmi_core: SCMI protocol bus registered
[    0.690145] clocksource: Switched to clocksource arch_sys_counter
[    0.697623] pnp: PnP ACPI: disabled
[    0.705310] NET: Registered PF_INET protocol family
[    0.710428] IP idents hash table entries: 131072 (order: 8, 1048576 bytes, linear)
[    0.721840] tcp_listen_portaddr_hash hash table entries: 4096 (order: 4, 65536 bytes, linear)
[    0.730461] Table-perturb hash table entries: 65536 (order: 6, 262144 bytes, linear)
[    0.738265] TCP established hash table entries: 65536 (order: 7, 524288 bytes, linear)
[    0.746496] TCP bind hash table entries: 65536 (order: 9, 2097152 bytes, linear)
[    0.755107] TCP: Hash tables configured (established 65536 bind 65536)
[    0.762006] MPTCP token hash table entries: 8192 (order: 6, 196608 bytes, linear)
[    0.769768] UDP hash table entries: 4096 (order: 5, 131072 bytes, linear)
[    0.776699] UDP-Lite hash table entries: 4096 (order: 5, 131072 bytes, linear)
[    0.784228] NET: Registered PF_UNIX/PF_LOCAL protocol family
[    0.789941] NET: Registered PF_XDP protocol family
[    0.794769] PCI: CLS 0 bytes, default 64
[    0.798893] Trying to unpack rootfs image as initramfs...
[    0.806379] Initialise system trusted keyrings
[    0.811032] workingset: timestamp_bits=40 max_order=21 bucket_order=0
[    0.818498] xor: measuring software checksum speed
[    0.823766]    8regs           :  7648 MB/sec
[    0.828545]    32regs          :  8359 MB/sec
[    0.833427]    arm64_neon      :  6647 MB/sec
[    0.837811] xor: using function: 32regs (8359 MB/sec)
[    0.842898] async_tx: api initialized (async)
[    0.847286] Key type asymmetric registered
[    0.851410] Asymmetric key parser 'x509' registered
[    0.856398] Block layer SCSI generic (bsg) driver version 0.4 loaded (major 248)
[    0.863855] io scheduler mq-deadline registered
[    0.868420] io scheduler kyber registered
[    0.872563] io scheduler bfq registered
[    0.881479] shpchp: Standard Hot Plug PCI Controller Driver version: 0.4
[    0.901022] bman_portal 508000000.bman-portal: Portal initialised, cpu 0
[    0.907951] bman_portal 508010000.bman-portal: Portal initialised, cpu 1
[    0.914895] bman_portal 508020000.bman-portal: Portal initialised, cpu 2
[    0.921837] bman_portal 508030000.bman-portal: Portal initialised, cpu 3
[    0.929299] qman_portal 500000000.qman-portal: Portal initialised, cpu 0
[    0.936276] qman_portal 500010000.qman-portal: Portal initialised, cpu 1
[    0.943223] qman_portal 500020000.qman-portal: Portal initialised, cpu 2
[    0.950527] qman_portal 500030000.qman-portal: Portal initialised, cpu 3
[    0.959246] Serial: 8250/16550 driver, 4 ports, IRQ sharing enabled
[    0.967126] printk: console [ttyS0] disabled
[    0.971703] 21c0500.serial: ttyS0 at MMIO 0x21c0500 (irq = 56, base_baud = 18750000) is a 16550A
[    0.980578] printk: console [ttyS0] enabled
[    0.980578] printk: console [ttyS0] enabled
[    0.988975] printk: bootconsole [uart8250] disabled
[    0.988975] printk: bootconsole [uart8250] disabled
[    0.999508] 21c0600.serial: ttyS1 at MMIO 0x21c0600 (irq = 56, base_baud = 18750000) is a 16550A
[    1.008450] serial serial0: tty port ttyS1 registered
[    1.320855] Freeing initrd memory: 32500K
[    1.332564] Maxlinear Ethernet GPY115C 0x0000000001afd000:00: Firmware Version: 8.111 (0x886F)
[    1.348632] Maxlinear Ethernet GPY115C 0x0000000001afd000:01: Firmware Version: 8.111 (0x886F)
[    1.364507] Maxlinear Ethernet GPY115C 0x0000000001afd000:02: Firmware Version: 8.111 (0x886F)
[    1.441916] fsl_dpaa_mac 1ae2000.ethernet: FMan MEMAC
[    1.446979] fsl_dpaa_mac 1ae2000.ethernet: FMan MAC address: e8:f6:d7:00:16:01
[    1.454436] fsl_dpaa_mac 1ae8000.ethernet: FMan MEMAC
[    1.459491] fsl_dpaa_mac 1ae8000.ethernet: FMan MAC address: e8:f6:d7:00:15:ff
[    1.466908] fsl_dpaa_mac 1aea000.ethernet: FMan MEMAC
[    1.471964] fsl_dpaa_mac 1aea000.ethernet: FMan MAC address: e8:f6:d7:00:16:00
[    1.479384] fsl_dpaa_mac 1af0000.ethernet: FMan MEMAC
[    1.484439] fsl_dpaa_mac 1af0000.ethernet: FMan MAC address: e8:f6:d7:00:16:02
[    1.491853] fsl_dpaa_mac 1af2000.ethernet: FMan MEMAC
[    1.496908] fsl_dpaa_mac 1af2000.ethernet: FMan MAC address: e8:f6:d7:00:16:03
[    1.524078] fsl_dpaa_mac 1ae2000.ethernet eth0: Probed interface eth0
[    1.550565] fsl_dpaa_mac 1ae8000.ethernet eth1: Probed interface eth1
[    1.577410] fsl_dpaa_mac 1aea000.ethernet eth2: Probed interface eth2
[    1.604388] fsl_dpaa_mac 1af0000.ethernet eth3: Probed interface eth3
[    1.631447] fsl_dpaa_mac 1af2000.ethernet eth4: Probed interface eth4
[    1.638313] pca954x 0-0070: supply vdd not found, using dummy regulator
[    1.645756] i2c i2c-0: Added multiplexed i2c bus 4
[    1.650667] i2c i2c-0: Added multiplexed i2c bus 5
[    1.655561] i2c i2c-0: Added multiplexed i2c bus 6
[    1.660455] i2c i2c-0: Added multiplexed i2c bus 7
[    1.665253] pca954x 0-0070: registered 4 multiplexed busses for I2C switch pca9545
[    1.672880] pca954x 1-0070: supply vdd not found, using dummy regulator
[    1.680204] i2c i2c-1: Added multiplexed i2c bus 8
[    1.685060] i2c i2c-1: Added multiplexed i2c bus 9
[    1.689898] i2c i2c-1: Added multiplexed i2c bus 10
[    1.694834] i2c i2c-1: Added multiplexed i2c bus 11
[    1.699716] pca954x 1-0070: registered 4 multiplexed busses for I2C switch pca9545
[    1.707354] pca954x 2-0070: supply vdd not found, using dummy regulator
[    1.714795] i2c i2c-2: Added multiplexed i2c bus 12
[    1.719903] i2c i2c-2: Added multiplexed i2c bus 13
[    1.743375] rtc-pcf2127-i2c 14-0053: registered as rtc0
[    1.750932] rtc-pcf2127-i2c 14-0053: setting system clock to 2026-03-24T04:38:08 UTC (1774327088)
[    1.759842] i2c i2c-2: Added multiplexed i2c bus 14
[    1.764835] i2c i2c-2: Added multiplexed i2c bus 15
[    1.769720] pca954x 2-0070: registered 4 multiplexed busses for I2C switch pca9545
[    1.777490] ptp_qoriq: device tree node missing required elements, try automatic configuration
[    1.792587] device-mapper: uevent: version 1.0.3
[    1.797336] device-mapper: ioctl: 4.48.0-ioctl (2023-03-01) initialised: dm-devel@redhat.com
[    1.806279] qoriq-cpufreq qoriq-cpufreq: Freescale QorIQ CPU frequency scaling driver
[    1.814582] ledtrig-cpu: registered to indicate activity on CPUs
[    1.821044] SMCCC: SOC_ID: ARCH_SOC_ID not implemented, skipping ....
[    1.828228] hw perfevents: enabled with armv8_cortex_a72 PMU driver, 7 counters available
[    1.836730] drop_monitor: Initializing network drop monitor service
[    1.843697] NET: Registered PF_INET6 protocol family
[    1.869050] Segment Routing with IPv6
[    1.872752] In-situ OAM (IOAM) with IPv6
[    1.876728] mip6: Mobile IPv6
[    1.879772] Key type dns_resolver registered
[    1.884059] mpls_gso: MPLS GSO support
[    1.891597] registered taskstats version 1
[    1.895806] Loading compiled-in X.509 certificates
[    1.911661] Loaded X.509 cert 'VyOS Networks build time autogenerated Kernel key: f8a5e010d702c54c0086dac27d71a4064d35322e'
[    1.933742] Loaded X.509 cert 'VyOS LS1046A Secure Boot CA: ed9ff86ac8d3dc1144144291a885ffd7bcd198db'
[    1.949281] sfp sfp-xfi0: Host maximum power 3.0W
[    1.954483] sfp sfp-xfi1: Host maximum power 3.0W
[    1.959579] clk: Disabling unused clocks
[    1.965030] Freeing unused kernel memory: 4608K
[    2.008784] Checked W+X mappings: passed, no W+X pages found
[    2.014460] Run /init as init process
Loading, please wait...
Starting systemd-udevd version 252.39-1~deb12u1
[    2.280293] sdhci: Secure Digital Host Controller Interface driver
[    2.286509] sdhci: Copyright(c) Pierre Ossman
[    2.293739] sdhci-pltfm: SDHCI platform and OF driver helper
[    2.295996] sfp sfp-xfi1: module OEM              SFP-10G-SR       rev 02   sn CSY101NC2726     dc 231124  
[    2.309712] fsl_dpaa_mac 1aea000.ethernet e4: renamed from eth2
[    2.374194] mmc0: SDHCI controller on 1560000.esdhc [1560000.esdhc] using ADMA 64-bit
[    2.385186] sfp sfp-xfi0: module OEM              SFP-10G-T        rev 02   sn CSY101OB0963     dc 241012  
[    2.395828] fsl_dpaa_mac 1af2000.ethernet e6: renamed from eth4
[    2.402526] hwmon hwmon4: temp1_input not attached to any thermal zone
[    2.407797] fsl_dpaa_mac 1ae2000.ethernet e2: renamed from eth0
[    2.418014] xhci-hcd xhci-hcd.0.auto: xHCI Host Controller
[    2.419478] fsl_dpaa_mac 1ae8000.ethernet e3: renamed from eth1
[    2.423525] xhci-hcd xhci-hcd.0.auto: new USB bus registered, assigned bus number 1
[    2.430092] hwmon hwmon5: temp1_input not attached to any thermal zone
[    2.437163] xhci-hcd xhci-hcd.0.auto: hcc params 0x0220f66d hci version 0x100 quirks 0x0000008002000810
[    2.453068] xhci-hcd xhci-hcd.0.auto: irq 68, io mem 0x02f00000
[    2.459081] xhci-hcd xhci-hcd.0.auto: xHCI Host Controller
[    2.464575] xhci-hcd xhci-hcd.0.auto: new USB bus registered, assigned bus number 2
[    2.472245] xhci-hcd xhci-hcd.0.auto: Host supports USB 3.0 SuperSpeed
[    2.478982] usb usb1: New USB device found, idVendor=1d6b, idProduct=0002, bcdDevice= 6.06
[    2.487275] usb usb1: New USB device strings: Mfr=3, Product=2, SerialNumber=1
[    2.494504] usb usb1: Product: xHCI Host Controller
[    2.499386] usb usb1: Manufacturer: Linux 6.6.128-vyos xhci-hcd
[    2.505311] usb usb1: SerialNumber: xhci-hcd.0.auto
[    2.510698] fsl_dpaa_mac 1af0000.ethernet e5: renamed from eth3
[    2.510729] hub 1-0:1.0: USB hub found
[    2.520402] hub 1-0:1.0: 1 port detected
[    2.524710] usb usb2: New USB device found, idVendor=1d6b, idProduct=0003, bcdDevice= 6.06
[    2.532991] usb usb2: New USB device strings: Mfr=3, Product=2, SerialNumber=1
[    2.540222] usb usb2: Product: xHCI Host Controller
[    2.545103] usb usb2: Manufacturer: Linux 6.6.128-vyos xhci-hcd
[    2.551025] usb usb2: SerialNumber: xhci-hcd.0.auto
[    2.556225] hub 2-0:1.0: USB hub found
[    2.560001] hub 2-0:1.0: 1 port detected
[    2.566554] mmc0: new HS200 MMC card at address 0001
Begin: Loading essential drivers ... [    2.575791] mmcblk0: mmc0:0001 0IM20E 29.6 GiB
[    2.583945]  mmcblk0: p1 p2 p3
[    2.587633] mmcblk0boot0: mmc0:0001 0IM20E 31.5 MiB
[    2.593585] mmcblk0boot1: mmc0:0001 0IM20E 31.5 MiB
[    2.599368] mmcblk0rpmb: mmc0:0001 0IM20E 4.00 MiB, chardev (245:0)
done.
[    2.767157] usb 1-1: new high-speed USB device number 2 using xhci-hcd
[    2.899817] usb 1-1: New USB device found, idVendor=0781, idProduct=5581, bcdDevice= 1.00
[    2.908009] usb 1-1: New USB device strings: Mfr=1, Product=2, SerialNumber=3
[    2.915150] usb 1-1: Product: Ultra
[    2.918638] usb 1-1: Manufacturer: SanDisk
[    2.922735] usb 1-1: SerialNumber: 4C531001600621100051
[    2.939330] SCSI subsystem initialized
[    2.946805] usb-storage 1-1:1.0: USB Mass Storage device detected
[    2.953174] scsi host0: usb-storage 1-1:1.0
[    2.957706] usbcore: registered new interface driver usb-storage
[    3.982040] scsi 0:0:0:0: Direct-Access     SanDisk  Ultra            1.00 PQ: 0 ANSI: 6
[    3.994694] sd 0:0:0:0: [sda] 121307136 512-byte logical blocks: (62.1 GB/57.8 GiB)
[    4.003352] sd 0:0:0:0: [sda] Write Protect is off
[    4.008523] sd 0:0:0:0: [sda] Write cache: disabled, read cache: enabled, doesn't support DPO or FUA
[    4.032576]  sda: sda1
[    4.035112] sd 0:0:0:0: [sda] Attached SCSI removable disk
Begin: Running /scripts/init-premount ... done.
Begin: Mounting root file system ... [    8.222185] EXT4-fs (mmcblk0p3): mounted filesystem fa6ef6f2-1906-4b8d-952e-64329ef6dc1c ro with ordered data mode. Quota mode: disabled.
[    8.295058] loop: module loaded
[    8.322720] loop0: detected capacity change from 0 to 1025728
[    8.344147] squashfs: version 4.0 (2009/01/31) Phillip Lougher
Begin: Running /scripts/live-realpremount ... done.
Begin: Mounting "/live/medium//boot/2026.03.24-0223-rolling/2026.03.24-0223-rolling.squashfs" on "//2026.03.24-0223-rolling.squashfs" via "/dev/loop0" ... done.
mount: mounting /dev/mmcblk0boot0 on /live/persistence/ failed: No such device
mount: mounting /dev/mmcblk0boot1 on /live/persistence/ failed: No such device
mount: mounting /dev/mmcblk0p1 on /live/persistence/ failed: No such device
mount: mounting /dev/mmcblk0 on /live/persistence/ failed: No such device
mount: mounting /dev/mmcblk0 on /live/persistence/ failed: No such device
mount: mounting /dev/mmcblk0boot0 on /live/persistence/ failed: No such device
mount: mounting /dev/mmcblk0 on /live/persistence/ failed: No such device
mount: mounting /dev/mmcblk0boot1 on /live/persistence/ failed: No such device
[   14.973152] random: crng init done
[   15.163731] EXT4-fs (mmcblk0p3): re-mounted fa6ef6f2-1906-4b8d-952e-64329ef6dc1c r/w.
done.
Begin: Running /scripts/init-bottom ... done.
[   16.761256] systemd[1]: Inserted module 'autofs4'
[   16.900272] systemd[1]: systemd 252.39-1~deb12u1 running in system mode (+PAM +AUDIT +SELINUX +APPARMOR +IMA +SMACK +SECCOMP +GCRYPT -GNUTLS +OPENSSL +ACL +BLKID +CURL +ELFUTILS +FIDO2 +IDN2 -IDN +IPTC +KMOD +LIBCRYPTSETUP +LIBFDISK +PCRE2 -PWQUALITY +P11KIT +QRENCODE +TPM2 +BZIP2 +LZ4 +XZ +ZLIB +ZSTD -BPF_FRAMEWORK -XKBCOMMON +UTMP +SYSVINIT default-hierarchy=unified)
[   16.932989] systemd[1]: Detected architecture arm64.

Welcome to VyOS 2026.03.24-0223-rolling (current)!

[   16.957780] systemd[1]: Hostname set to <vyos>.
[   16.998356] systemd[1]: memfd_create() called without MFD_EXEC or MFD_NOEXEC_SEAL set
[   17.822990] systemd[1]: multi-user.target: Wants dependency dropin /etc/systemd/system/multi-user.target.wants/vyos-postinstall.service is not a symlink, ignoring.
[   18.083078] systemd[1]: Queued start job for default target Multi-User System.
[   18.098565] systemd[1]: Created slice Slice /system/getty.
[  OK  ] Created slice Slice /system/getty.
[   18.113012] systemd[1]: Created slice Slice /system/modprobe.
[  OK  ] Created slice Slice /system/modprobe.
[   18.127964] systemd[1]: Created slice Slice /system/serial-getty.
[  OK  ] Created slice Slice /system/serial-getty.
[   18.142820] systemd[1]: Created slice User and Session Slice.
[  OK  ] Created slice User and Session Slice.
[   18.156312] systemd[1]: Started Dispatch Password Requests to Console Directory Watch.
[  OK  ] Started Dispatch Password …ts to Console Directory Watch.
[   18.174285] systemd[1]: Started Forward Password Requests to Wall Directory Watch.
[  OK  ] Started Forward Password R…uests to Wall Directory Watch.
[   18.191499] systemd[1]: Set up automount Arbitrary Executable File Formats File System Automount Point.
[  OK  ] Set up automount Arbitrary…s File System Automount Point.
[   18.210212] systemd[1]: Reached target Local Encrypted Volumes.
[  OK  ] Reached target Local Encrypted Volumes.
[   18.224209] systemd[1]: Reached target Local Integrity Protected Volumes.
[  OK  ] Reached target Local Integrity Protected Volumes.
[   18.240226] systemd[1]: Reached target Path Units.
[  OK  ] Reached target Path Units.
[   18.252208] systemd[1]: Reached target Remote File Systems.
[  OK  ] Reached target Remote File Systems.
[   18.266197] systemd[1]: Reached target Slice Units.
[  OK  ] Reached target Slice Units.
[   18.278216] systemd[1]: Reached target TLS tunnels for network services - per-config-file target.
[  OK  ] Reached target TLS tunnels…ices - per-config-file target.
[   18.296206] systemd[1]: Reached target Swaps.
[  OK  ] Reached target Swaps.
[   18.307209] systemd[1]: Reached target Local Verity Protected Volumes.
[  OK  ] Reached target Local Verity Protected Volumes.
[   18.323333] systemd[1]: Listening on initctl Compatibility Named Pipe.
[  OK  ] Listening on initctl Compatibility Named Pipe.
[   18.339514] systemd[1]: Listening on Journal Socket (/dev/log).
[  OK  ] Listening on Journal Socket (/dev/log).
[   18.353441] systemd[1]: Listening on Journal Socket.
[  OK  ] Listening on Journal Socket.
[   18.366687] systemd[1]: Listening on udev Control Socket.
[  OK  ] Listening on udev Control Socket.
[   18.380378] systemd[1]: Listening on udev Kernel Socket.
[  OK  ] Listening on udev Kernel Socket.
[   18.404329] systemd[1]: Mounting Huge Pages File System...
         Mounting Huge Pages File System...
[   18.418426] systemd[1]: Mounting POSIX Message Queue File System...
         Mounting POSIX Message Queue File System...
[   18.443391] systemd[1]: Mounting Kernel Debug File System...
         Mounting Kernel Debug File System...
[   18.458517] systemd[1]: Mounting Kernel Trace File System...
         Mounting Kernel Trace File System...
[   18.484488] systemd[1]: Starting Create List of Static Device Nodes...
         Starting Create List of Static Device Nodes...
[   18.501600] systemd[1]: Starting Load Kernel Module configfs...
         Starting Load Kernel Module configfs...
[   18.516704] systemd[1]: Starting Load Kernel Module dm_mod...
         Starting Load Kernel Module dm_mod...
[   18.542495] systemd[1]: Starting Load Kernel Module drm...
         Starting Load Kernel Module drm...
[   18.557630] systemd[1]: Starting Load Kernel Module efi_pstore...
         Starting Load Kernel Module efi_pstore...
[   18.580577] systemd[1]: Starting Load Kernel Module fuse...
         Starting Load Kernel Module fuse...
[   18.595609] systemd[1]: Starting Load Kernel Module loop...
         Starting Load Kernel Module loop...
[   18.607442] fuse: init (API version 7.39)
[   18.622522] systemd[1]: Starting Journal Service...
         Starting Journal Service...
[   18.637943] systemd[1]: Starting Load Kernel Modules...
         Starting Load Kernel Modules...
[   18.665678] systemd[1]: Starting Remount Root and Kernel File Systems...
         Starting Remount Root and Kernel File Systems...
[   18.682991] systemd[1]: Starting Coldplug All udev Devices...
         Starting Coldplug All udev Devices...
[   18.699795] systemd[1]: Mounted Huge Pages File System.
[  OK  ] Mounted Huge Pages File System.
[   18.714898] systemd[1]: Mounted POSIX Message Queue File System.
[  OK  ] Mounted POSIX Message Queue File System.
[   18.729554] systemd[1]: Mounted Kernel Debug File System.
[  OK  ] Mounted Kernel Debug File System.
[   18.744458] systemd[1]: Started Journal Service.
[  OK  ] Started Journal Service.
[  OK  ] Mounted Kernel Trace File System   18.764437] bridge: filtering via arp/ip/ip6tables is no longer available by default. Update your scripts to load br_netfilter if you need this.
m.
[   18.779970] Bridge firewalling registered
[  OK  ] Finished Create List of Static Device Nodes.
[  OK  ] Finished Load Kernel Module configfs.
[  OK  ] Finished Load Kernel Module dm_mod.
[  OK  ] Finished Load Kernel Module drm.
[  OK  ] Finished Load Kernel Module efi_pstore.
[  OK  ] Finished Load Kernel Module fuse.
[  OK  ] Finished Load Kernel Module loop.
[  OK  ] Finished Load Kernel Modules.
[  OK  ] Finished Remount Root and Kernel File Systems.
         Mounting FUSE Control File System...
         Mounting Kernel Configuration File System...
         Starting Flush Journal to Persistent Storage...
         Starting Load/Save Random Seed...
         Startin[   18.943074] systemd-journald[1077]: Received client request to flush runtime journal.
g Apply Kernel Variables...
         Starting Create System Users...
[  OK  ] Started VyOS commit daemon.
[  OK  ] Started VyOS configuration daemon.
[  OK  ] Started VyOS DNS configuration keeper.
[  OK  ] Finished Coldplug All udev Devices.
[  OK  ] Mounted FUSE Control File System.
[  OK  ] Mounted Kernel Configuration File System.
[  OK  ] Finished Load/Save Random Seed.
[  OK  ] Finished Create System Users.
[  OK  ] Finished Apply Kernel Variables.
         Starting Create Static Device Nodes in /dev...
[  OK  ] Finished Create Static Device Nodes in /dev.
[  OK  ] Reached target Preparation for Local File Systems.
         Mounting /tmp...
         Mounting /var/tmp...
         Starting Rule-based Manage…for Device Events and Files...
[  OK  ] Finished Flush Journal to Persistent Storage.
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
[  OK  ] Finished live-config conta…oot process (late userspace)..
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
[  OK  ] Reached target Basic System.
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
[  OK  ] Started Atop process accounting daemon.
[  OK  ] Started Atop advanced performance monitor.
[  OK  ] Started LSB: Start vmtouch daemon.
[  OK  ] Started LSB: Load kernel image with kexec.
[  OK  ] Finished Update GRUB loader configuration structure.
[  OK  ] Started VyOS Router.
         Starting Permit User Sessions...
[  OK  ] Finished Permit User Sessions.
[  OK  ] Started Getty on tty1.
[  OK  ] Started Serial Getty on ttyS0.
[  OK  ] Reached target Login Prompts.
[   27.991455] vyos-router[1291]: Starting VyOS router.
[  OK  ] Started FastNetMon - DoS/D… sFlow/Netflow/mirror support.
[  OK  ] Reached target Multi-User System.
[  OK  ] Reached target VyOS target.
         Starting Record Runlevel Change in UTMP...
[  OK  ] Finished Record Runlevel Change in UTMP.
[   32.075679] vyos-router[1291]: Waiting for NICs to settle down: settled in 0sec..
[   32.106551] vyos-router[1291]: could not generate DUID ... failed!
[   45.728155] vyos-router[1291]: Mounting VyOS Config...done.
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
         Stopping Deferred execution scheduler...
         Stopping Atop advanced performance monitor...
         Stopping Regular background program processing daemon...
         Stopping D-Bus System Message Bus...
         Stopping DHCP client on eth0...
         Stopping DHCP client on eth1...
         Stopping DHCP client on eth2...
         Stopping DHCP client on eth3...
         Stopping DHCP client on eth4...
         Stopping FastNetMon - DoS/…Flow/Netflow/mirror support...
         Stopping LSB: Load kernel image with kexec...
         Stopping System Logging Service...
         Stopping OpenBSD Secure Shell server...
         Stopping Set Up Additional Binary Formats...
         Stopping Hostname Service...
         Stopping User Login Management...
         Stopping Load/Save Random Seed...
[  OK  ] Stopped Apply Kernel Variables.
[  OK  ] Stopped Load Kernel Modules.
         Stopping Record System Boot/Shutdown in UTMP...
         Stopping LSB: Start vmtouch daemon...
         Stopping VyOS system udpate-check service...
[  OK  ] Unmounted /run/credentials/systemd-sysctl.service.
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
[  OK  ] Stopped OpenBSD Secure Shell server.
[  OK  ] Stopped VyOS system udpate-check service.
[  OK  ] Stopped Set Up Additional Binary Formats.
[  OK  ] Stopped Load/Save Random Seed.
[  OK  ] Stopped Record System Boot/Shutdown in UTMP.
[  OK  ] Removed slice Slice /system/getty.
[  OK  ] Removed slice Slice /system/serial-getty.
[  OK  ] Removed slice Slice /system/ssh.
[  OK  ] Unset automount Arbitrary …s File System Automount Point.
         Stopping Atop process accounting daemon...
         Stopping Security Auditing Service...
         Stopping Permit User Sessions...
[  OK  ] Stopped Atop process accounting daemon.
[  OK  ] Stopped LSB: Load kernel image with kexec.
[  OK  ] Stopped Security Auditing Service.
[  OK  ] Stopped DHCP client on eth3.
[  OK  ] Stopped LSB: Start vmtouch daemon.
[  OK  ] Stopped Permit User Sessions.
[  OK  ] Stopped DHCP client on eth2.
[  OK  ] Stopped DHCP client on eth1.
[  OK  ] Stopped target Network.
[  OK  ] Stopped target Remote File Systems.
[  OK  ] Stopped Create System Files and Directories.
[  OK  ] Unmounted /run/credentials…ystemd-tmpfiles-setup.service.
[  OK  ] Unmounted /opt/vyatta/config/tmp/new_config_1936.
[   75.963878] vyos-router[1291]:  migrate system configure.
[  OK  ] Stopped DHCP client on eth4.
[  OK  ] Stopped DHCP client on eth0.
[  OK  ] Removed slice Slice /system/dhclient.
         Stopping VyOS Router...
[   76.265988] vyos-router[3641]: Stopping VyOS router:.
[   76.273290] vyos-router[3641]: Un-mounting VyOS Config...done.
[  OK  ] Unmounted opt-vyatta-config.mount.
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
[  OK  ] Closed D-Bus System Message Bus Socket.
[  OK  ] Closed Podman API Socket.
[  OK  ] Closed Syslog Socket.
[  OK  ] Closed UUID daemon activation socket.
         Unmounting /boot/grub...
         Unmounting /config...
         Unmounting /etc/cni/net.d...
         Unmounting /etc/frr/frr.conf...
         Unmounting /run/credentials/systemd-sysusers.service...
         Unmounting /run/credential…-tmpfiles-setup-dev.service...
         Unmounting /tmp...
         Unmounting /usr/lib/live/mount/overlay...
         Unmounting /usr/lib/live/m…026.03.24-0223-rolling/grub...
         Unmounting /usr/lib/live/m…03.24-0223-rolling.squashfs...
[  OK  ] Unmounted /boot/grub.
[  OK  ] Unmounted /config.
[  OK  ] Unmounted /etc/cni/net.d.
[  OK  ] Unmounted /etc/frr/frr.conf.
[  OK  ] Unmounted /run/credentials/systemd-sysusers.service.
[  OK  ] Unmounted /run/credentials…md-tmpfiles-setup-dev.service.
[  OK  ] Unmounted /tmp.
[  OK  ] Unmounted /usr/lib/live/mount/overlay.
[  OK  ] Unmounted /usr/lib/live/mo…/2026.03.24-0223-rolling/grub.
[  OK  ] Unmounted /usr/lib/live/mo…6.03.24-0223-rolling.squashfs.
[  OK  ] Stopped target Swaps.
         Unmounting boot.mount...
         Unmounting /usr/lib/live/mount/persistence...
[  OK  ] Unmounted boot.mount.
[FAILED] Failed unmounting /usr/lib/live/mount/persistence.
[  OK  ] Stopped target Preparation for Local File Systems.
[  OK  ] Reached target Unmount All Filesystems.
[  OK  ] Stopped Create Static Device Nodes in /dev.
[  OK  ] Stopped Create System Users.
[  OK  ] Stopped Remount Root and Kernel File Systems.
[  OK  ] Reached target System Shutdown.
[  OK  ] Reached target Late Shutdown Services.
         Starting Reboot via kexec...
[   78.677227] (sd-umount)[3809]: Failed to unmount /usr/lib/live/mount/persistence: Device or resource busy
[   78.691147] systemd-shutdown[1]: Could not detach loopback /dev/loop0: Device or resource busy
[   78.781692] systemd-shutdown[1]: Failed to finalize file systems, loop devices, ignoring.
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x410fd082]
[    0.000000] Linux version 6.6.128-vyos (root@3b36e06c53a4) (gcc (Debian 12.2.0-14+deb12u1) 12.2.0, GNU ld (GNU Binutils for Debian) 2.40) #1 SMP PREEMPT_DYNAMIC Tue Mar 24 02:47:49 UTC 2026
[    0.000000] KASLR enabled
[    0.000000] random: crng init done
[    0.000000] Machine model: Mono Gateway Development Kit
[    0.000000] earlycon: uart8250 at MMIO 0x00000000021c0500 (options '')
[    0.000000] printk: bootconsole [uart8250] enabled
[    0.000000] efi: UEFI not found.
[    0.000000] Reserved memory: created DMA memory pool at 0x00000009ff000000, size 16 MiB
[    0.000000] OF: reserved mem: initialized node bman-fbpr, compatible id shared-dma-pool
[    0.000000] OF: reserved mem: 0x00000009ff000000..0x00000009ffffffff (16384 KiB) nomap non-reusable bman-fbpr
[    0.000000] Reserved memory: created DMA memory pool at 0x00000009fe800000, size 8 MiB
[    0.000000] OF: reserved mem: initialized node qman-fqd, compatible id shared-dma-pool
[    0.000000] OF: reserved mem: 0x00000009fe800000..0x00000009feffffff (8192 KiB) nomap non-reusable qman-fqd
[    0.000000] Reserved memory: created DMA memory pool at 0x00000009fc000000, size 32 MiB
[    0.000000] OF: reserved mem: initialized node qman-pfdr, compatible id shared-dma-pool
[    0.000000] OF: reserved mem: 0x00000009fc000000..0x00000009fdffffff (32768 KiB) nomap non-reusable qman-pfdr
[    0.000000] NUMA: No NUMA configuration found
[    0.000000] NUMA: Faking a node at [mem 0x0000000080000000-0x00000009ffffffff]
[    0.000000] NUMA: NODE_DATA [mem 0x9fb7fd1c0-0x9fb800fff]
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
[    0.000000] Kernel command line: console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin fsl_dpaa_fman.fsl_fm_max_frm=9600 vyos-union=/boot/2026.03.24-0223-rolling panic=60
[    0.000000] Unknown kernel command line parameters "noautologin boot=live vyos-union=/boot/2026.03.24-0223-rolling", will be passed to user space.
[    0.000000] Dentry cache hash table entries: 1048576 (order: 11, 8388608 bytes, linear)
[    0.000000] Inode-cache hash table entries: 524288 (order: 10, 4194304 bytes, linear)
[    0.000000] Fallback order for Node 0: 0 
[    0.000000] Built 1 zonelists, mobility grouping on.  Total pages: 2047488
[    0.000000] Policy zone: Normal
[    0.000000] mem auto-init: stack:off, heap alloc:off, heap free:off
[    0.000000] software IO TLB: area num 4.
[    0.000000] software IO TLB: mapped [mem 0x00000000f7e00000-0x00000000fbe00000] (64MB)
[    0.000000] Memory: 7979584K/8321024K available (11840K kernel code, 2450K rwdata, 5256K rodata, 4608K init, 597K bss, 341440K reserved, 0K cma-reserved)
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
[    0.008365] Console: colour dummy device 80x25
[    0.012913] Calibrating delay loop (skipped), value calculated using timer frequency.. 50.00 BogoMIPS (lpj=25000)
[    0.023248] pid_max: default: 32768 minimum: 301
[    0.028055] Mount-cache hash table entries: 16384 (order: 5, 131072 bytes, linear)
[    0.035696] Mountpoint-cache hash table entries: 16384 (order: 5, 131072 bytes, linear)
[    0.044609] RCU Tasks: Setting shift to 2 and lim to 1 rcu_task_cb_adjust=1 rcu_task_cpu_ids=4.
[    0.053419] RCU Tasks Trace: Setting shift to 2 and lim to 1 rcu_task_cb_adjust=1 rcu_task_cpu_ids=4.
[    0.062801] rcu: Hierarchical SRCU implementation.
[    0.067623] rcu:     Max phase no-delay instances is 400.
[    0.073243] EFI services will not be available.
[    0.077936] smp: Bringing up secondary CPUs ...
[    0.082796] Detected PIPT I-cache on CPU1
[    0.082853] CPU1: Booted secondary processor 0x0000000001 [0x410fd082]
[    0.083158] Detected PIPT I-cache on CPU2
[    0.083192] CPU2: Booted secondary processor 0x0000000002 [0x410fd082]
[    0.083474] Detected PIPT I-cache on CPU3
[    0.083508] CPU3: Booted secondary processor 0x0000000003 [0x410fd082]
[    0.083548] smp: Brought up 1 node, 4 CPUs
[    0.119455] SMP: Total of 4 processors activated.
[    0.124186] CPU features: detected: 32-bit EL0 Support
[    0.129354] CPU features: detected: CRC32 instructions
[    0.134566] CPU: All CPU(s) started at EL2
[    0.138687] alternatives: applying system-wide alternatives
[    0.145117] devtmpfs: initialized
[    0.152779] clocksource: jiffies: mask: 0xffffffff max_cycles: 0xffffffff, max_idle_ns: 1911260446275000 ns
[    0.162597] futex hash table entries: 1024 (order: 4, 65536 bytes, linear)
[    0.169612] pinctrl core: initialized pinctrl subsystem
[    0.175198] Machine: Mono Gateway Development Kit
[    0.179932] SoC family: QorIQ LS1046A
[    0.183612] SoC ID: svr:0x87070010, Revision: 1.0
[    0.188562] DMI not present or invalid.
[    0.192648] NET: Registered PF_NETLINK/PF_ROUTE protocol family
[    0.198952] DMA: preallocated 1024 KiB GFP_KERNEL pool for atomic allocations
[    0.206264] DMA: preallocated 1024 KiB GFP_KERNEL|GFP_DMA pool for atomic allocations
[    0.214269] DMA: preallocated 1024 KiB GFP_KERNEL|GFP_DMA32 pool for atomic allocations
[    0.222370] audit: initializing netlink subsys (disabled)
[    0.227874] audit: type=2000 audit(0.042:1): state=initialized audit_enabled=0 res=1
[    0.228288] thermal_sys: Registered thermal governor 'fair_share'
[    0.235674] thermal_sys: Registered thermal governor 'bang_bang'
[    0.241805] thermal_sys: Registered thermal governor 'step_wise'
[    0.247848] thermal_sys: Registered thermal governor 'user_space'
[    0.253928] cpuidle: using governor ladder
[    0.264188] cpuidle: using governor menu
[    0.268210] hw-breakpoint: found 6 breakpoint and 4 watchpoint registers.
[    0.275089] ASID allocator initialised with 32768 entries
[    0.280702] Serial: AMBA PL011 UART driver
[    0.292544] Modules: 2G module region forced by RANDOMIZE_MODULE_REGION_FULL
[    0.299641] Modules: 0 pages in range for non-PLT usage
[    0.299644] Modules: 518064 pages in range for PLT usage
[    0.305340] HugeTLB: registered 1.00 GiB page size, pre-allocated 0 pages
[    0.317522] HugeTLB: 0 KiB vmemmap can be freed for a 1.00 GiB page
[    0.323829] HugeTLB: registered 32.0 MiB page size, pre-allocated 0 pages
[    0.330659] HugeTLB: 0 KiB vmemmap can be freed for a 32.0 MiB page
[    0.336965] HugeTLB: registered 2.00 MiB page size, pre-allocated 0 pages
[    0.343794] HugeTLB: 0 KiB vmemmap can be freed for a 2.00 MiB page
[    0.350099] HugeTLB: registered 64.0 KiB page size, pre-allocated 0 pages
[    0.356929] HugeTLB: 0 KiB vmemmap can be freed for a 64.0 KiB page
[    0.380273] raid6: neonx8   gen()  4812 MB/s
[    0.401606] raid6: neonx4   gen()  4691 MB/s
[    0.422935] raid6: neonx2   gen()  3889 MB/s
[    0.444269] raid6: neonx1   gen()  2800 MB/s
[    0.465601] raid6: int64x8  gen()  2696 MB/s
[    0.486931] raid6: int64x4  gen()  2638 MB/s
[    0.508265] raid6: int64x2  gen()  2560 MB/s
[    0.529604] raid6: int64x1  gen()  1957 MB/s
[    0.533897] raid6: using algorithm neonx8 gen() 4812 MB/s
[    0.556361] raid6: .... xor() 3386 MB/s, rmw enabled
[    0.561354] raid6: using neon recovery algorithm
[    0.566219] ACPI: Interpreter disabled.
[    0.571232] iommu: Default domain type: Translated
[    0.576055] iommu: DMA domain TLB invalidation policy: lazy mode
[    0.582279] usbcore: registered new interface driver usbfs
[    0.587815] usbcore: registered new interface driver hub
[    0.593175] usbcore: registered new device driver usb
[    0.598500] imx-i2c 2180000.i2c: can't get pinctrl, bus recovery not supported
[    0.605880] i2c i2c-0: IMX I2C adapter registered
[    0.610633] i2c i2c-0: using dma0chan16 (tx) and dma0chan17 (rx) for DMA transfers
[    0.618343] imx-i2c 2190000.i2c: can't get pinctrl, bus recovery not supported
[    0.625698] i2c i2c-1: IMX I2C adapter registered
[    0.630522] imx-i2c 21a0000.i2c: can't get pinctrl, bus recovery not supported
[    0.637894] i2c i2c-2: IMX I2C adapter registered
[    0.642723] imx-i2c 21b0000.i2c: can't get pinctrl, bus recovery not supported
[    0.650043] i2c i2c-3: IMX I2C adapter registered
[    0.654839] pps_core: LinuxPPS API ver. 1 registered
[    0.659833] pps_core: Software ver. 5.3.6 - Copyright 2005-2007 Rodolfo Giometti <giometti@linux.it>
[    0.669036] PTP clock support registered
[    0.673118] EDAC MC: Ver: 3.0.0
[    0.676526] scmi_core: SCMI protocol bus registered
[    0.682066] clocksource: Switched to clocksource arch_sys_counter
[    0.688513] pnp: PnP ACPI: disabled
[    0.695401] NET: Registered PF_INET protocol family
[    0.700505] IP idents hash table entries: 131072 (order: 8, 1048576 bytes, linear)
[    0.711279] tcp_listen_portaddr_hash hash table entries: 4096 (order: 4, 65536 bytes, linear)
[    0.719899] Table-perturb hash table entries: 65536 (order: 6, 262144 bytes, linear)
[    0.727704] TCP established hash table entries: 65536 (order: 7, 524288 bytes, linear)
[    0.735937] TCP bind hash table entries: 65536 (order: 9, 2097152 bytes, linear)
[    0.744624] TCP: Hash tables configured (established 65536 bind 65536)
[    0.751379] MPTCP token hash table entries: 8192 (order: 6, 196608 bytes, linear)
[    0.759089] UDP hash table entries: 4096 (order: 5, 131072 bytes, linear)
[    0.766020] UDP-Lite hash table entries: 4096 (order: 5, 131072 bytes, linear)
[    0.773478] NET: Registered PF_UNIX/PF_LOCAL protocol family
[    0.779196] NET: Registered PF_XDP protocol family
[    0.784024] PCI: CLS 0 bytes, default 64
[    0.788194] Trying to unpack rootfs image as initramfs...
[    0.795969] Initialise system trusted keyrings
[    0.800634] workingset: timestamp_bits=40 max_order=21 bucket_order=0
[    0.807502] xor: measuring software checksum speed
[    0.812765]    8regs           :  7667 MB/sec
[    0.817548]    32regs          :  8350 MB/sec
[    0.822429]    arm64_neon      :  6661 MB/sec
[    0.826814] xor: using function: 32regs (8350 MB/sec)
[    0.831901] async_tx: api initialized (async)
[    0.836293] Key type asymmetric registered
[    0.840418] Asymmetric key parser 'x509' registered
[    0.845383] Block layer SCSI generic (bsg) driver version 0.4 loaded (major 248)
[    0.852840] io scheduler mq-deadline registered
[    0.857405] io scheduler kyber registered
[    0.861476] io scheduler bfq registered
[    0.871447] shpchp: Standard Hot Plug PCI Controller Driver version: 0.4
[    0.881325] bman_ccsr: BMan BAR already configured
[    0.887444] bman_portal 508000000.bman-portal: Portal initialised, cpu 0
[    0.894361] bman_portal 508010000.bman-portal: Portal initialised, cpu 1
[    0.901279] bman_portal 508020000.bman-portal: Portal initialised, cpu 2
[    0.908213] bman_portal 508030000.bman-portal: Portal initialised, cpu 3
[    0.919032] qman_portal 500000000.qman-portal: Portal initialised, cpu 0
[    0.925970] qman_portal 500010000.qman-portal: Portal initialised, cpu 1
[    0.932894] qman_portal 500020000.qman-portal: Portal initialised, cpu 2
[    0.940176] qman_portal 500030000.qman-portal: Portal initialised, cpu 3
[    1.359082] Freeing initrd memory: 32500K
[   12.091264] Serial: 8250/16550 driver, 4 ports, IRQ sharing enabled
[   12.098833] printk: console [ttyS0] disabled
[   12.103286] 21c0500.serial: ttyS0 at MMIO 0x21c0500 (irq = 56, base_baud = 18750000) is a 16550A
[   12.112148] printk: console [ttyS0] enabled
[   12.112148] printk: console [ttyS0] enabled
[   12.120541] printk: bootconsole [uart8250] disabled
[   12.120541] printk: bootconsole [uart8250] disabled
[   12.130739] 21c0600.serial: ttyS1 at MMIO 0x21c0600 (irq = 56, base_baud = 18750000) is a 16550A
[   12.139605] serial serial0: tty port ttyS1 registered
[   12.158103] Maxlinear Ethernet GPY115C 0x0000000001afd000:00: Firmware Version: 8.111 (0x886F)
[   12.175278] Maxlinear Ethernet GPY115C 0x0000000001afd000:01: Firmware Version: 8.111 (0x886F)
[   12.192277] Maxlinear Ethernet GPY115C 0x0000000001afd000:02: Firmware Version: 8.111 (0x886F)
[   12.279525] fsl_dpaa_mac 1ae2000.ethernet: FMan MEMAC
[   12.284589] fsl_dpaa_mac 1ae2000.ethernet: FMan MAC address: e8:f6:d7:00:16:01
[   12.292041] fsl_dpaa_mac 1ae8000.ethernet: FMan MEMAC
[   12.297097] fsl_dpaa_mac 1ae8000.ethernet: FMan MAC address: e8:f6:d7:00:15:ff
[   12.304507] fsl_dpaa_mac 1aea000.ethernet: FMan MEMAC
[   12.309562] fsl_dpaa_mac 1aea000.ethernet: FMan MAC address: e8:f6:d7:00:16:00
[   12.316973] fsl_dpaa_mac 1af0000.ethernet: FMan MEMAC
[   12.322027] fsl_dpaa_mac 1af0000.ethernet: FMan MAC address: e8:f6:d7:00:16:02
[   12.329440] fsl_dpaa_mac 1af2000.ethernet: FMan MEMAC
[   12.334495] fsl_dpaa_mac 1af2000.ethernet: FMan MAC address: e8:f6:d7:00:16:03
[   12.360589] fsl_dpaa_mac 1ae2000.ethernet eth0: Probed interface eth0
[   12.386091] fsl_dpaa_mac 1ae8000.ethernet eth1: Probed interface eth1
[   12.411894] fsl_dpaa_mac 1aea000.ethernet eth2: Probed interface eth2
[   12.437734] fsl_dpaa_mac 1af0000.ethernet eth3: Probed interface eth3
[   12.463772] fsl_dpaa_mac 1af2000.ethernet eth4: Probed interface eth4
[   12.470602] pca954x 0-0070: supply vdd not found, using dummy regulator
[   12.478101] i2c i2c-0: Added multiplexed i2c bus 4
[   12.482994] i2c i2c-0: Added multiplexed i2c bus 5
[   12.487880] i2c i2c-0: Added multiplexed i2c bus 6
[   12.492772] i2c i2c-0: Added multiplexed i2c bus 7
[   12.497569] pca954x 0-0070: registered 4 multiplexed busses for I2C switch pca9545
[   12.505204] pca954x 1-0070: supply vdd not found, using dummy regulator
[   12.512521] i2c i2c-1: Added multiplexed i2c bus 8
[   12.517373] i2c i2c-1: Added multiplexed i2c bus 9
[   12.522210] i2c i2c-1: Added multiplexed i2c bus 10
[   12.527138] i2c i2c-1: Added multiplexed i2c bus 11
[   12.532022] pca954x 1-0070: registered 4 multiplexed busses for I2C switch pca9545
[   12.539666] pca954x 2-0070: supply vdd not found, using dummy regulator
[   12.547144] i2c i2c-2: Added multiplexed i2c bus 12
[   12.552243] i2c i2c-2: Added multiplexed i2c bus 13
[   12.575652] rtc-pcf2127-i2c 14-0053: registered as rtc0
[   12.583210] rtc-pcf2127-i2c 14-0053: setting system clock to 2026-03-24T04:39:41 UTC (1774327181)
[   12.592121] i2c i2c-2: Added multiplexed i2c bus 14
[   12.597114] i2c i2c-2: Added multiplexed i2c bus 15
[   12.601998] pca954x 2-0070: registered 4 multiplexed busses for I2C switch pca9545
[   12.609773] ptp_qoriq: device tree node missing required elements, try automatic configuration
[   12.624861] device-mapper: uevent: version 1.0.3
[   12.629572] device-mapper: ioctl: 4.48.0-ioctl (2023-03-01) initialised: dm-devel@redhat.com
[   12.638621] qoriq-cpufreq qoriq-cpufreq: Freescale QorIQ CPU frequency scaling driver
[   12.646938] ledtrig-cpu: registered to indicate activity on CPUs
[   12.653391] SMCCC: SOC_ID: ARCH_SOC_ID not implemented, skipping ....
[   12.660551] hw perfevents: enabled with armv8_cortex_a72 PMU driver, 7 counters available
[   12.669043] drop_monitor: Initializing network drop monitor service
[   12.675583] NET: Registered PF_INET6 protocol family
[   12.705604] Segment Routing with IPv6
[   12.709320] In-situ OAM (IOAM) with IPv6
[   12.713294] mip6: Mobile IPv6
[   12.716341] Key type dns_resolver registered
[   12.720631] mpls_gso: MPLS GSO support
[   12.727954] registered taskstats version 1
[   12.732170] Loading compiled-in X.509 certificates
[   12.750371] Loaded X.509 cert 'VyOS Networks build time autogenerated Kernel key: f8a5e010d702c54c0086dac27d71a4064d35322e'
[   12.774586] Loaded X.509 cert 'VyOS LS1046A Secure Boot CA: ed9ff86ac8d3dc1144144291a885ffd7bcd198db'
[   12.789748] sfp sfp-xfi0: Host maximum power 3.0W
[   12.794951] sfp sfp-xfi1: Host maximum power 3.0W
[   12.800111] clk: Disabling unused clocks
[   12.805563] Freeing unused kernel memory: 4608K
[   12.877843] Checked W+X mappings: passed, no W+X pages found
[   12.883524] Run /init as init process
Loading, please wait...
Starting systemd-udevd version 252.39-1~deb12u1
[   13.121881] sfp sfp-xfi0: module OEM              SFP-10G-T        rev 02   sn CSY101OB0963     dc 241012  
[   13.206948] sfp sfp-xfi1: module OEM              SFP-10G-SR       rev 02   sn CSY101NC2726     dc 231124  
[   13.217276] fsl_dpaa_mac 1aea000.ethernet e4: renamed from eth2
[   13.228046] fsl_dpaa_mac 1af2000.ethernet e6: renamed from eth4
[   13.242048] fsl_dpaa_mac 1af0000.ethernet e5: renamed from eth3
[   13.243555] sdhci: Secure Digital Host Controller Interface driver
[   13.246277] hwmon hwmon4: temp1_input not attached to any thermal zone
[   13.260729] sdhci: Copyright(c) Pierre Ossman
[   13.274165] fsl_dpaa_mac 1ae2000.ethernet e2: renamed from eth0
[   13.276236] sdhci-pltfm: SDHCI platform and OF driver helper
[   13.294684] fsl_dpaa_mac 1ae8000.ethernet e3: renamed from eth1
[   13.301313] hwmon hwmon5: temp1_input not attached to any thermal zone
[   13.342216] mmc0: SDHCI controller on 1560000.esdhc [1560000.esdhc] using ADMA 64-bit
[   13.352421] xhci-hcd xhci-hcd.0.auto: xHCI Host Controller
[   13.357934] xhci-hcd xhci-hcd.0.auto: new USB bus registered, assigned bus number 1
[   13.365664] xhci-hcd xhci-hcd.0.auto: hcc params 0x0220f66d hci version 0x100 quirks 0x0000008002000810
[   13.375089] xhci-hcd xhci-hcd.0.auto: irq 67, io mem 0x02f00000
[   13.381094] xhci-hcd xhci-hcd.0.auto: xHCI Host Controller
[   13.386590] xhci-hcd xhci-hcd.0.auto: new USB bus registered, assigned bus number 2
[   13.394255] xhci-hcd xhci-hcd.0.auto: Host supports USB 3.0 SuperSpeed
[   13.400987] usb usb1: New USB device found, idVendor=1d6b, idProduct=0002, bcdDevice= 6.06
[   13.409267] usb usb1: New USB device strings: Mfr=3, Product=2, SerialNumber=1
[   13.416494] usb usb1: Product: xHCI Host Controller
[   13.421374] usb usb1: Manufacturer: Linux 6.6.128-vyos xhci-hcd
[   13.427297] usb usb1: SerialNumber: xhci-hcd.0.auto
[   13.432458] hub 1-0:1.0: USB hub found
[   13.436240] hub 1-0:1.0: 1 port detected
[   13.440531] usb usb2: New USB device found, idVendor=1d6b, idProduct=0003, bcdDevice= 6.06
[   13.448808] usb usb2: New USB device strings: Mfr=3, Product=2, SerialNumber=1
[   13.456036] usb usb2: Product: xHCI Host Controller
[   13.460915] usb usb2: Manufacturer: Linux 6.6.128-vyos xhci-hcd
[   13.466837] usb usb2: SerialNumber: xhci-hcd.0.auto
[   13.467033] mmc0: new HS200 MMC card at address 0001
[   13.471958] hub 2-0:1.0: USB hub found
[   13.480452] hub 2-0:1.0: 1 port detected
[   13.485151] mmcblk0: mmc0:0001 0IM20E 29.6 GiB
[   13.493942]  mmcblk0: p1 p2 p3
[   13.497498] mmcblk0boot0: mmc0:0001 0IM20E 31.5 MiB
[   13.503173] mmcblk0boot1: mmc0:0001 0IM20E 31.5 MiB
[   13.508977] mmcblk0rpmb: mmc0:0001 0IM20E 4.00 MiB, chardev (245:0)
Begin: Loading essential drivers ... done.
[   13.677085] usb 1-1: new high-speed USB device number 2 using xhci-hcd
[   13.809724] usb 1-1: New USB device found, idVendor=0781, idProduct=5581, bcdDevice= 1.00
[   13.817916] usb 1-1: New USB device strings: Mfr=1, Product=2, SerialNumber=3
[   13.825063] usb 1-1: Product: Ultra
[   13.828552] usb 1-1: Manufacturer: SanDisk
[   13.832648] usb 1-1: SerialNumber: 4C531001600621100051
[   13.850372] SCSI subsystem initialized
[   13.858210] usb-storage 1-1:1.0: USB Mass Storage device detected
[   13.864588] scsi host0: usb-storage 1-1:1.0
[   13.868981] usbcore: registered new interface driver usb-storage
[   14.923011] scsi 0:0:0:0: Direct-Access     SanDisk  Ultra            1.00 PQ: 0 ANSI: 6
[   14.935949] sd 0:0:0:0: [sda] 121307136 512-byte logical blocks: (62.1 GB/57.8 GiB)
[   14.944588] sd 0:0:0:0: [sda] Write Protect is off
[   14.949741] sd 0:0:0:0: [sda] Write cache: disabled, read cache: enabled, doesn't support DPO or FUA
[   14.973603]  sda: sda1
[   14.976150] sd 0:0:0:0: [sda] Attached SCSI removable disk
Begin: Running /scripts/init-premount ... done.
Begin: Mounting root file system ... [   19.259674] EXT4-fs (mmcblk0p3): mounted filesystem fa6ef6f2-1906-4b8d-952e-64329ef6dc1c ro with ordered data mode. Quota mode: disabled.
[   19.334741] loop: module loaded
[   19.366123] loop0: detected capacity change from 0 to 1025728
[   19.387492] squashfs: version 4.0 (2009/01/31) Phillip Lougher
Begin: Running /scripts/live-realpremount ... done.
Begin: Mounting "/live/medium//boot/2026.03.24-0223-rolling/2026.03.24-0223-rolling.squashfs" on "//2026.03.24-0223-rolling.squashfs" via "/dev/loop0" ... done.
mount: mounting /dev/mmcblk0boot0 on /live/persistence/ failed: No such device
mount: mounting /dev/mmcblk0boot1 on /live/persistence/ failed: No such device
mount: mounting /dev/mmcblk0p1 on /live/persistence/ failed: No such device
mount: mounting /dev/mmcblk0 on /live/persistence/ failed: No such device
mount: mounting /dev/mmcblk0 on /live/persistence/ failed: No such device
mount: mounting /dev/mmcblk0boot0 on /live/persistence/ failed: No such device
mount: mounting /dev/mmcblk0 on /live/persistence/ failed: No such device
[   26.280695] EXT4-fs (mmcblk0p3): re-mounted fa6ef6f2-1906-4b8d-952e-64329ef6dc1c r/w.
done.
Begin: Running /scripts/init-bottom ... done.
[   27.906325] systemd[1]: Inserted module 'autofs4'
[   28.049599] systemd[1]: systemd 252.39-1~deb12u1 running in system mode (+PAM +AUDIT +SELINUX +APPARMOR +IMA +SMACK +SECCOMP +GCRYPT -GNUTLS +OPENSSL +ACL +BLKID +CURL +ELFUTILS +FIDO2 +IDN2 -IDN +IPTC +KMOD +LIBCRYPTSETUP +LIBFDISK +PCRE2 -PWQUALITY +P11KIT +QRENCODE +TPM2 +BZIP2 +LZ4 +XZ +ZLIB +ZSTD -BPF_FRAMEWORK -XKBCOMMON +UTMP +SYSVINIT default-hierarchy=unified)
[   28.082323] systemd[1]: Detected architecture arm64.

Welcome to VyOS 2026.03.24-0223-rolling (current)!

[   28.107718] systemd[1]: Hostname set to <vyos>.
[   28.149629] systemd[1]: memfd_create() called without MFD_EXEC or MFD_NOEXEC_SEAL set
[   29.043391] systemd[1]: multi-user.target: Wants dependency dropin /etc/systemd/system/multi-user.target.wants/vyos-postinstall.service is not a symlink, ignoring.
[   29.308591] systemd[1]: Queued start job for default target Multi-User System.
[   29.324561] systemd[1]: Created slice Slice /system/getty.
[  OK  ] Created slice Slice /system/getty.
[   29.338922] systemd[1]: Created slice Slice /system/modprobe.
[  OK  ] Created slice Slice /system/modprobe.
[   29.353895] systemd[1]: Created slice Slice /system/serial-getty.
[  OK  ] Created slice Slice /system/serial-getty.
[   29.368734] systemd[1]: Created slice User and Session Slice.
[  OK  ] Created slice User and Session Slice.
[   29.382234] systemd[1]: Started Dispatch Password Requests to Console Directory Watch.
[  OK  ] Started Dispatch Password …ts to Console Directory Watch.
[   29.400210] systemd[1]: Started Forward Password Requests to Wall Directory Watch.
[  OK  ] Started Forward Password R…uests to Wall Directory Watch.
[   29.417439] systemd[1]: Set up automount Arbitrary Executable File Formats File System Automount Point.
[  OK  ] Set up automount Arbitrary…s File System Automount Point.
[   29.436135] systemd[1]: Reached target Local Encrypted Volumes.
[  OK  ] Reached target Local Encrypted Volumes.
[   29.450136] systemd[1]: Reached target Local Integrity Protected Volumes.
[  OK  ] Reached target Local Integrity Protected Volumes.
[   29.466134] systemd[1]: Reached target Path Units.
[  OK  ] Reached target Path Units.
[   29.478129] systemd[1]: Reached target Remote File Systems.
[  OK  ] Reached target Remote File Systems.
[   29.492119] systemd[1]: Reached target Slice Units.
[  OK  ] Reached target Slice Units.
[   29.504134] systemd[1]: Reached target TLS tunnels for network services - per-config-file target.
[  OK  ] Reached target TLS tunnels…ices - per-config-file target.
[   29.522127] systemd[1]: Reached target Swaps.
[  OK  ] Reached target Swaps.
[   29.533129] systemd[1]: Reached target Local Verity Protected Volumes.
[  OK  ] Reached target Local Verity Protected Volumes.
[   29.549265] systemd[1]: Listening on initctl Compatibility Named Pipe.
[  OK  ] Listening on initctl Compatibility Named Pipe.
[   29.565468] systemd[1]: Listening on Journal Socket (/dev/log).
[  OK  ] Listening on Journal Socket (/dev/log).
[   29.579360] systemd[1]: Listening on Journal Socket.
[  OK  ] Listening on Journal Socket.
[   29.592655] systemd[1]: Listening on udev Control Socket.
[  OK  ] Listening on udev Control Socket.
[   29.606299] systemd[1]: Listening on udev Kernel Socket.
[  OK  ] Listening on udev Kernel Socket.
[   29.630241] systemd[1]: Mounting Huge Pages File System...
         Mounting Huge Pages File System...
[   29.644547] systemd[1]: Mounting POSIX Message Queue File System...
         Mounting POSIX Message Queue File System...
[   29.661647] systemd[1]: Mounting Kernel Debug File System...
         Mounting Kernel Debug File System...
[   29.686303] systemd[1]: Mounting Kernel Trace File System...
         Mounting Kernel Trace File System...
[   29.703914] systemd[1]: Starting Create List of Static Device Nodes...
         Starting Create List of Static Device Nodes...
[   29.729514] systemd[1]: Starting Load Kernel Module configfs...
         Starting Load Kernel Module configfs...
[   29.744986] systemd[1]: Starting Load Kernel Module dm_mod...
         Starting Load Kernel Module dm_mod...
[   29.760905] systemd[1]: Starting Load Kernel Module drm...
         Starting Load Kernel Module drm...
[   29.785465] systemd[1]: Starting Load Kernel Module efi_pstore...
         Starting Load Kernel Module efi_pstore...
[   29.803914] systemd[1]: Starting Load Kernel Module fuse...
         Starting Load Kernel Module fuse...
[   29.825476] systemd[1]: Starting Load Kernel Module loop...
         Starting Load Kernel Module loop...
[   29.838872] fuse: init (API version 7.39)
[   29.857510] systemd[1]: Starting Journal Service...
         Starting Journal Service...
[   29.873202] systemd[1]: Starting Load Kernel Modules...
         Starting Load Kernel Modules...
[   29.898611] systemd[1]: Starting Remount Root and Kernel File Systems...
         Starting Remount Root and Kernel File Systems...
[   29.921591] systemd[1]: Starting Coldplug All udev Devices...
         Starting Coldplug All udev Devices...
[   29.943192] systemd[1]: Mounted Huge Pages File System.
[  OK  ] Mounted Huge Pages File System.
[   29.960921] systemd[1]: Mounted POSIX Message Queue File System.
[  OK  ] Mounted POSIX Message Queue File System.
[   29.977519] systemd[1]: Mounted Kernel Debug File System.
[  OK  ] Mounted Kernel Debug File System.
[   29.990010] bridge: filtering via arp/ip/ip6tables is no longer available by default. Update your scripts to load br_netfilter if you need this.
[   30.003356] systemd[1]: Started Journal Service.
[  OK     30.008299] Bridge firewalling registered
0m] Started Journal Service.
[  OK  ] Mounted Kernel Trace File System.
[  OK  ] Finished Create List of Static Device Nodes.
[  OK  ] Finished Load Kernel Module configfs.
[  OK  ] Finished Load Kernel Module dm_mod.
[  OK  ] Finished Load Kernel Module drm.
[  OK  ] Finished Load Kernel Module efi_pstore.
[  OK  ] Finished Load Kernel Module fuse.
[  OK  ] Finished Load Kernel Module loop.
[  OK  ] Finished Load Kernel Modules.
[  OK  ] Finished Remount Root and Kernel File Systems.
         Mounting FUSE Control File System...
         Mounting Kernel Configuration File System...
         Starting Flush Journal to Persistent Storage...
         Starting Load/Save Random Seed...
         Startin[   30.190451] systemd-journald[1078]: Received client request to flush runtime journal.
g Apply Kernel Variables...
         Starting Create System Users...
[  OK  ] Started VyOS commit daemon.
[  OK  ] Started VyOS configuration daemon.
[  OK  ] Started VyOS DNS configuration keeper.
[  OK  ] Finished Coldplug All udev Devices.
[  OK  ] Mounted FUSE Control File System.
[  OK  ] Mounted Kernel Configuration File System.
[  OK  ] Finished Load/Save Random Seed.
[  OK  ] Finished Create System Users.
[  OK  ] Finished Apply Kernel Variables.
         Starting Create Static Device Nodes in /dev...
[  OK  ] Finished Create Static Device Nodes in /dev.
[  OK  ] Reached target Preparation for Local File Systems.
         Mounting /tmp...
         Mounting /var/tmp...
         Starting Rule-based Manage…for Device Events and Files...
[  OK  ] Finished Flush Journal to Persistent Storage.
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
[  OK  ] Finished live-config conta…oot process (late userspace)..
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
[  OK  ] Reached target Basic System.
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
[  OK  ] Started Atop process accounting daemon.
[  OK  ] Started Atop advanced performance monitor.
[  OK  ] Started LSB: Start vmtouch daemon.
[  OK  ] Started LSB: Load kernel image with kexec.
[  OK  ] Started D-Bus System Message Bus.
[  OK  ] Started User Login Management.
[  OK  ] Finished Update GRUB loader configuration structure.
[  OK  ] Started VyOS Router.
         Starting Permit User Sessions...
[  OK  ] Finished Permit User Sessions.
[  OK  ] Started Getty on tty1.
[  OK  ] Started Serial Getty on ttyS0.
[  OK  ] Reached target Login Prompts.
[   39.405654] vyos-router[1293]: Starting VyOS router.
[  OK  ] Started FastNetMon - DoS/D… sFlow/Netflow/mirror support.
[  OK  ] Reached target Multi-User System.
[  OK  ] Reached target VyOS target.
         Starting Record Runlevel Change in UTMP...
[  OK  ] Finished Record Runlevel Change in UTMP.
[   43.330472] vyos-router[1293]: Waiting for NICs to settle down: settled in 0sec..
[   43.348971] vyos-router[1293]: could not generate DUID ... failed!
[   57.390460] vyos-router[1293]: Mounting VyOS Config...done.
[   90.584263] vyos-router[1293]:  migrate system configure.
[   91.106178] vyos-config[1297]: Configuration error

** ARM64 rolling build of VyOS for NXP LS1046A (Mono Gateway) **

vyos login: 