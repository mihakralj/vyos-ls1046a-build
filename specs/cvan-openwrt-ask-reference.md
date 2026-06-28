**Version 1.0 · HADS 1.0.0**  
**Date:** 2026-06-28  
**Branch:** `nxp-sdk`  
**Source:** Live DUT at 192.168.1.190 running cvandesande Mono OpenWrt v25.12.4 from eMMC p2  
**Access:** SSH root@192.168.1.190 (password `vyos`), serial via 192.168.1.16:5555

## AI READING INSTRUCTION

This document is the authoritative reference for the cvandesande Mono OpenWrt v25.12.4 ASK build running on the Mono Gateway DK. Every fact tagged `[SPEC]` was observed on the live board on 2026-06-28. `[NOTE]` entries provide context and analysis. `[BUG]` entries describe known issues.

## Quick Inventory Script

```bash
wget -qO- https://raw.githubusercontent.com/mihakralj/vyos-ls1046a-build/nxp-sdk/board/scripts/ask-inventory.sh | sh
```

## 1. System Identity

**[SPEC]** Build: Mono OpenWrt v25.12.4-mono1 (cvandesande/mono-ask-v25.12.4-r1)  
**[SPEC]** Kernel: Linux 6.12.87 #0 SMP PREEMPT, aarch64, GCC 14.3.0, musl, Binutils 2.44  
**[SPEC]** Boot cmdline: `console=ttyS0,115200 root=/dev/mmcblk0p2 rootwait rw`  
**[SPEC]** Boot method: U-Boot `bootm` via FIT image (`/boot/kernel.itb`) from eMMC p2  
**[SPEC]** Rootfs: ext4 on /dev/mmcblk0p2 (256 MB), VyOS preserved on /dev/mmcblk0p3  
**[NOTE]** Built 2026-05-13 from commit `b05e6eebcc90`. Published 2026-05-15. 51 ASK commits, 8 OSS commits. Includes SELinux (permissive), CEETM egress shaping, IPsec SEC offload.

## 2. Kernel Configuration

**[SPEC]** `CONFIG_NF_CONNTRACK=m` — nf_conntrack loaded as module via `/etc/modules.d/nf-conntrack`  
**[SPEC]** `CONFIG_NF_CONNTRACK_NETLINK=m` — loaded via `/etc/modules.d/nf-conntrack-netlink`, refcnt=0 (unused)  
**[SPEC]** QBMan: `Qman ver:0a01,03,02,01`  
**[SPEC]** BMan: `Bman ver:0a02,02,01`  
**[SPEC]** FMan ucode: 210.10.1 (proprietary, PCD-capable)  
**[SPEC]** FMan PCD: `FM_PCD_Init::ext timers 4, muram offset 0x3f508`  

## 3. ASK Kernel Modules

**[SPEC]** cdx.ko: 500,064 bytes (488 KB), vermagic: 6.12.87 SMP preempt mod_unload modversions, refcnt=1 (fci)  
**[SPEC]** fci.ko: 12,288 bytes, refcnt=0 but loaded, depends on cdx  
**[SPEC]** auto_bridge.ko: **NOT PRESENT** — no L2 ebtables interception. This means eth3/eth4 can be brought UP without crashing.  

**[NOTE]** cdx.ko is **103 KB smaller** than the sergioaguayo build (500KB vs 622KB). The cdx.ko in this build includes `comcerto_fpp_*` symbols (fp_netfilter equivalent) embedded directly — no separate `fp_netfilter.ko` module. The 5 comcerto_fpp_* symbols: `comcerto_fpp_send_command`, `_simple`, `_atomic`, `_workqueue`, `_register_event_cb` — all inside cdx.ko.

**[SPEC]** cdx.ko module parameter: `sec_congestion=0` (only parameter)  
**[SPEC]** No fp_netfilter module or hook registration in dmesg  

## 4. Userspace Components

### 4.1 cmm — Connection Manager Daemon

**[SPEC]** Binary: `/usr/sbin/cmm`, 393,961 bytes (385 KB)  
**[SPEC]** PID: 3465  
**[SPEC]** Cmdline: `/usr/sbin/cmm -f /etc/config/fastforward`  
**[SPEC]** Start log: `cmmBridgeInit: Bridge is started in manual mode`  
**[SPEC]** Libraries: dynamically linked — libfci.so.0, libcmm.so.0, libcli.so.1.10, libpcap.so.1, libnetfilter_conntrack.so.3, libnfnetlink.so.0, libmnl.so.0  
**[BUG] CMM ctnetlink NOT CONNECTED**: nf_conntrack_netlink module refcnt=0. CMM has no NETLINK_NETFILTER socket. All CMM netlink sockets are protocol 0 (NETLINK_ROUTE). The nfct_open() call either failed or was never made. Root cause TBD — may be musl compatibility, compile-time disable, or runtime failure.

**[SPEC]** CMM netlink sockets (from /proc/net/netlink): All protocol 0 (NETLINK_ROUTE), PID 3465. No protocol 12 (NETLINK_NETFILTER) sockets.  
**[SPEC]** CMM open FDs: 27 total — 18 sockets (CDX/FCI IPC), 2 pipes, /dev/null, /dev/console, /proc/pppoe, /tmp temp file  
**[SPEC]** CMM FCI traffic: Sent=58, Received=58 (boot-time setup messages only, no flow installs)  

### 4.2 dpa_app — FMan PCD Boot-Time Programmer

**[SPEC]** Binary: `/usr/bin/dpa_app`, 1,180,141 bytes (1.15 MB)  
**[SPEC]** Boot: `cdx_module_init::start_dpa_app successful` at T+11.46s  
**[SPEC]** CDX configs at `/usr/share/ask-dpa-app/`: cdx_pcd.xml, cdx_cfg.xml, cdx_cfg_dgw.xml, cdx_cfg_ls1046_rdb.xml, cdx_cfg_rgw.xml, cdx_sp.xml  
**[SPEC]** Configs NOT at `/etc/` — must be copied for dpa_app runtime use  

### 4.3 fmc — FMan Configuration Tool

**[SPEC]** Binary: `/usr/bin/fmc`, 1,246,781 bytes (1.22 MB)  

## 5. Libraries

**[SPEC]** All libraries present at `/usr/lib/`:  
- libfci.so.0.0.0: 65,435 bytes  
- libcmm.so.0.0.0: 65,539 bytes  
- libnetfilter_conntrack.so.3.8.0: 129,827 bytes  
- libnfnetlink.so.0.2.0: 65,506 bytes  
- libcli.so.1.10: CLI helper library  
- libmnl.so.0: Minimal Netlink library  
- libpcap.so.1: Packet capture library  

**[NOTE]** Earlier inventory showing libraries "MISSING" was a path error — the script looked in `/usr/lib/` subdirectories but the files are directly in `/usr/lib/`.

## 6. Conntrack State

**[SPEC]** nf_conntrack: module loaded (94,208 bytes, 8 consumers including nft_chain_nat, nft_flow_offload, nft_ct, nf_nat, nf_flow_table, nf_conntrack_netlink)  
**[SPEC]** nf_conntrack_netlink: module loaded (36,864 bytes, refcnt=0 — no users)  
**[SPEC]** nf_conntrack_events: 2 (all events enabled)  
**[SPEC]** Conntrack entries: 9 (real traffic — DNS queries, SSH connections)  
**[SPEC]** `/proc/net/stat/nf_conntrack` (all CPUs): entries=0x12 (18), new=0, insert=0, insert_failed=0 (note: entries fluctuate 9-21 depending on timeout)  
**[SPEC]** SSH conntrack entries: 1 ESTABLISHED + 6 TIME_WAIT (from multiple SSH sessions during investigation)  

**[BUG] Conntrack entries exist but ctnetlink events NOT generated**: Kernel conntrack creates entries in the hash table (visible in `/proc/net/nf_conntrack`), and `nf_conntrack_events=2`, but `new=0` in `/proc/net/stat/nf_conntrack`. The `new` counter tracks ctnetlink NEW events, not hash table insertions. ctnetlink events are not being generated despite events being enabled — either the nf_conntrack_netlink module needs to be connected (refcnt>0) for events to fire, or there's a kernel-level gating.

**[NOTE]** The `CMM init script` does `insmod nf_conntrack_netlink` but CMM never calls `nfct_open()`. The init script loads the module but the daemon doesn't use it. This is consistent across both sergioaguayo and CVAN builds.

## 7. Boot Sequence

```
T+0.000  Kernel 6.12.87 starts
T+2.391  FM_Config: FMan-Controller code ver 210.10.1
T+2.420  FM_PCD_Init ext timers=4
T+2.467  fsl_mac: 5 MEMAC devices probed (e2000, e8000, ea000, f0000, f2000)
T+2.530  fsl_dpa: DPAA Ethernet driver — 5 netdevs
T+2.622  fsl_oh: 2 OH ports (oh@2 IPsec, oh@3 WiFi)
T+11.15  cdx_module_init
T+11.16  start_dpa_app::calling dpa_app
T+11.46  cdx_module_init::start_dpa_app successful
T+11.50  cdx_dpaa_ingress_cgr_init (policer congestion groups)
```

**[NOTE]** No fp_netfilter hook registration message in this build (unlike sergioaguayo where `fp_netfilter: hooks registered successfully` appeared at T+3.33).

## 8. FCI / CDX State

**[SPEC]** /proc/fci: Sent=58, Received=58 — FCI IPC operational at boot setup  
**[SPEC]** CDX ioctls: cdx_ctrl chardev at 242:0  
**[SPEC]** PCD FQID stats: All per-port counters = 0 bytes (no hardware flow hits)  
**[SPEC]** PCD ports: eth0, eth1, eth2, oh1, oh2 (no eth3/eth4 in PCD — config issue?)  

## 9. Interface State

**[SPEC]** eth0: UP, 192.168.1.190/16, removed from br-lan for direct SSH access  
**[SPEC]** eth1-eth4: DOWN (no-auto_bridge allows bringing UP without crash)  
**[SPEC]** br-lan: UP but with no active ports (eth0 nomaster'd), 192.168.1.1/24  
**[SPEC]** eth4: Has static WAN IP 192.168.2.1/24 (OpenWrt default WAN config)  

## 10. Startup Priorities

**[SPEC]** /etc/init.d/cdx: START=18, STOP=90 — loads cdx.ko via insmod  
**[SPEC]** /etc/init.d/fci: START=53, STOP=47 — loads fci.ko, checks /proc/fppmode  
**[SPEC]** /etc/init.d/cmm: START=54, STOP=46 — enables VWD fastpath, loads nf_conntrack modules, starts cmm daemon  

## 11. Configuration

**[SPEC]** /etc/config/fastforward: Excludes FTP:21/TCP, SIP:5060/UDP, PPTP:1723/TCP  
**[SPEC]** /etc/sysctl.d/11-nf-conntrack.conf: acct=1, checksum=0, tcp_timeout_established=7440, udp_timeout=60  
**[SPEC]** No /etc/cdx_pcd.xml or /etc/cdx_cfg.xml (configs at /usr/share/ask-dpa-app/)  

## 12. Kernel Modules Directory

**[SPEC]** 112 modules at `/lib/modules/6.12.87/`  
**[SPEC]** USB modules: usb-common.ko, usbcore.ko, xhci-hcd.ko, xhci-plat-hcd.ko, xhci-pci.ko, usb-storage.ko  
**[SPEC]** No scsi_mod/sd_mod modules (built-in to kernel)  
**[SPEC]** ASK modules: cdx.ko (500KB), fci.ko (12KB) — NO auto_bridge.ko  

## 13. Comparison — Sergio vs CVAN

| Metric | Sergio (25.12.2) | CVAN (25.12.4) |
|--------|------------------|-----------------|
| Kernel | 6.12.74 | 6.12.87 |
| cdx.ko | 622,592 bytes | 500,064 bytes |
| fci.ko | 12,288 bytes | 12,288 bytes |
| auto_bridge.ko | 40,960 bytes, loaded | **NOT PRESENT** |
| fp_netfilter | Separate module, hooks registered | Embedded in cdx.ko (5 symbols) |
| Conntrack entries | 10 (stale ALG) | 9-21 (real traffic) |
| Conntrack new | 0 | 0 |
| Conntrack insert_failed | 4 | 0 |
| CMM PID | 4493 | 3465 |
| CMM FCI messages | 304 sent/recv | 58 sent/recv |
| CMM ctnetlink | NOT CONNECTED | NOT CONNECTED |
| CDX configs location | /etc/ | /usr/share/ask-dpa-app/ |
| USB bootable | Yes (initramfs) | No (modules not built-in) |
| SELinux | No | Yes (permissive) |
| Libraries | All present | All present |
| SSH | Password vyos | Password vyos |

## 14. Root Cause Summary

**[SPEC]** The ASK hardware offload pipeline is blocked at the **conntrack→CMM** stage on ALL tested builds:

1. **Conntrack entries ARE created** — kernel `nf_conntrack_in()` works correctly, entries visible in `/proc/net/nf_conntrack`
2. **ctnetlink events NOT generated** — `new=0` in `/proc/net/stat/nf_conntrack` despite `nf_conntrack_events=2`  
3. **CMM never connects to ctnetlink** — nf_conntrack_netlink refcnt=0, no NETLINK_NETFILTER sockets
4. **CMM starts in bridge manual mode** — "Bridge is started in manual mode" suggests it expects manual bridge configuration, not auto-detection via conntrack
5. **CMM HAS conntrack support** — dynamically links libnetfilter_conntrack.so.3, has libcli.so (which contains CLI commands for conntrack management)
6. **FCI↔CDX IPC works** — boot-time setup messages flow (58 sent/recv)
7. **PCD programmed but empty** — dpa_app pre-programs 16 CC hash tables via cdx_pcd.xml, but no per-flow keys installed
8. **Zero hardware offload** — PCD FQID counters all zero

**[NOTE]** The key insight: CMM is compiled with conntrack support (linked dynamically), but it doesn't open the ctnetlink socket at runtime. This could be because:
- CMM's bridge-manual-mode skips conntrack initialization
- CMM's nfct_open() fails silently (musl/libc issue?)
- CMM's event source selection is compile-time gated differently in this build
- The `fastforward` config doesn't trigger conntrack monitoring

## 15. Key Differences from Sergio Build

1. **No auto_bridge.ko** — SFP+ ports can be brought UP safely
2. **Smaller CDX** (500KB vs 622KB) — fp_netfilter symbols embedded, not separate module
3. **Real conntrack entries** (DNS + SSH traffic, not stale ALG helpers)
4. **SELinux** present (permissive mode)
5. **CEETM egress shaping** support
6. **IPsec SEC offload** plumbing
7. **51 ASK-specific commits** with bug fixes and hardening
