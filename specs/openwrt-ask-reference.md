**Version 1.0 · HADS 1.0.0**  
**Date:** 2026-06-28  
**Branch:** `nxp-sdk`  
**Source:** Live DUT running OpenWrt-ASK v25.12.2, kernel 6.12.74

## AI READING INSTRUCTION

This document is a reference dump of the OpenWrt-ASK system as it runs on the Mono Gateway DK. Every fact tagged `[SPEC]` was observed on the live board at 192.168.1.190 on 2026-06-28. `[NOTE]` entries provide context and analysis. `[BUG]` entries describe known issues.

## 1. System Identity

**[SPEC]** Board: Mono Gateway Development Kit (NXP LS1046A, 4× Cortex-A72, 8 GB RAM)  
**[SPEC]** Firmware: OpenWrt 25.12.2, r32802-f505120278 ("Dave's Guitar")  
**[SPEC]** Kernel: Linux 6.12.74 #0 SMP PREEMPT, aarch64, built 2026-03-25  
**[SPEC]** Toolchain: aarch64-openwrt-linux-musl-gcc (GCC 14.3.0), GNU ld (Binutils) 2.44  
**[SPEC]** Boot cmdline: `console=ttyS0,115200 rdinit=/sbin/init nf_conntrack.enable_hooks=1`  
**[SPEC]** Boot method: initramfs via USB (rdinit=/sbin/init), no root= parameter  
**[NOTE]** Built by sergioaguayo (sergioag/OpenWRT-ASK fork from we-are-mono/OpenWRT-ASK, mono-25.12.2 branch). Kernel config not embedded (CONFIG_IKCONFIG not set).

## 2. Kernel Configuration

**[SPEC]** `CONFIG_CPE_FAST_PATH=y` — Comcerto fast path enabled  
**[SPEC]** `CONFIG_NF_CONNTRACK=y` — Netfilter conntrack built-in (not module)  
**[NOTE]** `CONFIG_NF_CONNTRACK` must be built-in because VyOS and OpenWrt-ASK both use `=y`. Module variant `=m` has different init timing that may affect `enable_hooks` parameter processing.

## 3. ASK Kernel Modules

**[SPEC]** cdx.ko: 622,592 bytes, 1 user (fci). CDX (Connection Data eXchange) hardware flow table manager.  
**[SPEC]** fci.ko: 12,288 bytes, 5 users. Fastpath Control Interface — IPC between CMM and CDX.  
**[SPEC]** auto_bridge.ko: 40,960 bytes, 1 user. L2 bridge flow detection via ebtables hooks.  
**[BUG] auto_bridge UAF crash**: The `auto_bridge.ko` triggers a use-after-free kernel panic when Ethernet cables are connected and the OpenWrt bridge (`br-lan`) forwards traffic. Symptom: `BUG: scheduling while atomic: kworker/u16:X` → RCU stall → board freeze. Root cause: the `abm_ebt_hook` / `abm_l2flow_find` path accesses freed memory. OpenWrt-ASK ships `auto_bridge.ko` by default; users must disconnect Ethernet or disable the bridge to use the board stably. Same bug observed on our VyOS build.

## 4. dpa_app — FMan PCD Pre-Programming

**[SPEC]** Binary: `/usr/bin/dpa_app`, 1,180,141 bytes (1.18 MB)  
**[SPEC]** Config: `/etc/cdx_pcd.xml` (18,172 bytes), `/etc/cdx_cfg.xml` (833 bytes)  
**[SPEC]** Boot flow: `cdx_module_init` → `call_usermodehelper("/usr/bin/dpa_app", UMH_WAIT_PROC)` → `start_dpa_app successful` at T+10.88s  
**[SPEC]** dpa_app runs once at boot and exits. Not present in `ps` output.  
**[SPEC]** FMan PCD FQID stats (`/proc/fqid_stats/pcd/`) populated with eth0-eth4 + oh1 + oh2 — confirms dpa_app programmed KeyGen schemes and Coarse Classifier hash tables into FMan silicon.  
**[NOTE]** The PCD chain is pre-programmed with empty CC trees (hash tables allocated, no per-flow keys installed). Per-flow keys are expected to be installed by CMM when conntrack events fire. Since conntrack never creates new entries, the CC trees remain empty and all packets miss in hardware.

## 5. CMM — Connection Manager Daemon

**[SPEC]** Binary: `/usr/sbin/cmm`, 394,017 bytes (394 KB)  
**[SPEC]** Cmdline: `/usr/sbin/cmm -f /etc/config/fastforward`  
**[SPEC]** libnetfilter_conntrack symbols: **33 nfct symbols** in binary (statically linked)  
**[SPEC]** Open file descriptors (27 total):

| FD | Type | Purpose |
|----|------|---------|
| 0 | /dev/null | stdin |
| 1 | /dev/console | stdout |
| 2, 14 | pipe | internal IPC |
| 3-8 | socket | FCI/CDX ioctl channels |
| 10-13, 15-17 | socket | CDX control + netlink |
| 18 | /proc/4493/net/pppoe | PPPoE discovery |
| 19-25, 27 | socket | FCI data + CT netlink |
| 26 | /tmp/cmm.1430310949 | temp file |

**[SPEC]** FastForward config (`/etc/config/fastforward`): Excludes FTP (tcp:21), SIP (udp:5060), PPTP (tcp:1723) from offload. Logging commented out.

## 6. Comcerto Fastpath Hooks

**[SPEC]** `fp_netfilter: hooks registered successfully` at T+3.33s (dmesg)  
**[SPEC]** `fp_netfilter_pre_routing` fires **continuously** during network traffic, with callback suppression at ~5-second intervals. Typical rate: 14-27 callbacks suppressed per interval.  
**[SPEC]** `ct: ifindex changed` and `ct: iif changed` messages fire at high frequency during DHCP and ping traffic. This proves the comcerto `fp_info[dir]` metadata snapshot is functional.  
**[NOTE]** On our VyOS build, `fp_netfilter_pre_routing` only fired at boot (10 callbacks suppressed once), then went silent. OpenWrt-ASK's hooks are genuinely active. The difference may be due to `nf_conntrack.enable_hooks=1` on the cmdline, or a kernel config/build differences.

## 7. Conntrack Status

**[SPEC]** `/proc/net/stat/nf_conntrack` (all 4 CPUs):
```
entries=7, clashres=0, found=0, new=0, insert=0, insert_failed=0
```
**[SPEC]** 7 stale conntrack entries from ALG helper expectations (FTP/SIP/TFTP/PPTP/H323 modules) and boot-time DHCP/ping traffic.  
**[SPEC]** `new=0, insert=0` — **no new conntrack entries are ever created**, regardless of traffic volume.  
**[NOTE]** This matches exactly our VyOS observation. The `enable_hooks=1` cmdline parameter is consumed by the kernel but does not result in functional hook registration at the `nf_ct_netns_get()` level. The hooks appear registered (fp_netfilter fires) but `nf_conntrack_in()` returns NF_ACCEPT without creating entries.

**[BUG] Deaf conntrack — Kernel conntrack never creates entries**: Despite `CONFIG_NF_CONNTRACK=y`, `nf_conntrack.enable_hooks=1` on cmdline, and `fp_netfilter` hooks firing actively, the kernel's `nf_conntrack_in()` function returns `NF_ACCEPT` for every packet without calling `init_conntrack()` (verified via kprobe on our VyOS build: 38k calls, 0 to `init_conntrack`). Root cause is in `nf_conntrack_standalone.c` where `static bool enable_hooks` (default false) gates `nf_ct_netns_get(NFPROTO_INET)`. The cmdline parameter `nf_conntrack.enable_hooks=1` is recognized by the kernel but the hooks remain unregistered. Patch 0401 (changing default to `true` at source level) registers the hooks but `nf_conntrack_in` still returns early. Affects ALL NXP SDK kernel versions (6.12.49 and 6.12.74).

## 8. Comparison — VyOS vs OpenWrt-ASK

| Metric | VyOS (nxp-sdk, 6.12.49) | OpenWrt-ASK (6.12.74) |
|--------|--------------------------|------------------------|
| dpa_app | 1.18 MB, real binary | 1.18 MB, real binary |
| CMM nfct symbols | 44 | 33 |
| cdx.ko size | 622 KB | 622 KB |
| conntrack entries | 0 | 7 (stale) |
| conntrack new | 0 | 0 |
| conntrack insert | 0 | 0 |
| fp_netfilter activity | Boot only | **Continuous** |
| enable_hooks cmdline | Consumed, no effect | Consumed, no effect |

**[NOTE]** The key difference is fp_netfilter activity. On OpenWrt-ASK it fires continuously with `ct: ifindex changed` messages. On VyOS it only fired at boot. This suggests OpenWrt-ASK has a slightly different kernel build that allows the comcerto hooks to interact with existing conntrack entries, even though new entries are still not created. The fp_netfilter hooks read `ct->fp_info[dir]` metadata from existing conntrack entries — the 7 stale entries provide something to read. Since VyOS has 0 entries, the hooks have nothing to process.

## 9. Operational Workarounds

**[NOTE]** The board is unstable with Ethernet connected due to `auto_bridge.ko` UAF bug. Workaround:
1. Boot without Ethernet cables
2. Via serial: `ip link set eth0 nomaster; ip link set eth1 nomaster; ip link set eth2 nomaster; ip link del br-lan`
3. Assign IP directly: `ip addr add 192.168.100.1/24 dev eth0; ip link set eth0 up`
4. Connect Ethernet — board can now be reached via SSH

**[NOTE]** OpenWrt-ASK does not run SSH by default. Use telnet or serial for initial access.

## 10. Files

| File | Size | Purpose |
|------|------|---------|
| `/usr/bin/dpa_app` | 1.18 MB | FMan PCD boot-time programmer |
| `/usr/sbin/cmm` | 394 KB | Connection Manager daemon |
| `/etc/cdx_pcd.xml` | 18 KB | PCD hash table distributions (16 tables) |
| `/etc/cdx_cfg.xml` | 833 bytes | Port-to-policy binding |
| `/etc/config/fastforward` | ~500 bytes | CMM offload exclusion list |
| `/proc/fqid_stats/pcd/` | dir | FMan PCD per-port FQID counters |
