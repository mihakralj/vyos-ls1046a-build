**Version 2.0 · HADS 1.0.0**  
**Date:** 2026-06-28  
**Branch:** `nxp-sdk`  
**Source:** Live DUT at 192.168.1.190 running OpenWrt-ASK v25.12.2 via initramfs  
**Access:** SSH root@192.168.1.190 (password `vyos`), serial via 192.168.1.16:5555

## AI READING INSTRUCTION

This document is the authoritative reference dump of the NXP ASK 1.x SDK stack running on the Mono Gateway DK. Every fact tagged `[SPEC]` was observed on the live board on 2026-06-28. `[NOTE]` entries provide context and analysis. `[BUG]` entries describe known issues.

## 1. System Identity

**[SPEC]** Board: Mono Gateway Development Kit (NXP LS1046A, 4× Cortex-A72, 8 GB RAM)  
**[SPEC]** DT model: `Mono Gateway Development Kit`  
**[SPEC]** Firmware: OpenWrt 25.12.2, r32802-f505120278 ("Dave's Guitar")  
**[SPEC]** Kernel: Linux 6.12.74 #0 SMP PREEMPT, aarch64, built 2026-03-25  
**[SPEC]** Toolchain: aarch64-openwrt-linux-musl-gcc (GCC 14.3.0), GNU ld (Binutils) 2.44  
**[SPEC]** Boot cmdline: `console=ttyS0,115200 rdinit=/sbin/init nf_conntrack.enable_hooks=1`  
**[SPEC]** Boot method: initramfs via USB (rdinit=/sbin/init), no root= parameter  
**[NOTE]** Built by sergioaguayo (sergioag/OpenWRT-ASK fork from we-are-mono/OpenWRT-ASK, mono-25.12.2 branch). Kernel config not embedded (CONFIG_IKCONFIG not set).

## 2. Kernel Configuration

**[SPEC]** `CONFIG_CPE_FAST_PATH=y` — Comcerto fast path enabled  
**[SPEC]** `CONFIG_NF_CONNTRACK=y` — Netfilter conntrack built-in (not module)  
**[SPEC]** QBMan version: `Qman ver:0a01,03,02,01`  
**[SPEC]** BMan version: `Bman ver:0a02,02,01`  
**[SPEC]** RESERVED-MEM: bman-fbpr 16 MiB, qman-fqd 8 MiB, qman-pfdr 32 MiB  
**[NOTE]** `CONFIG_NF_CONNTRACK` must be built-in because VyOS and OpenWrt-ASK both use `=y`. Module variant `=m` has different init timing that may affect `enable_hooks` parameter processing.

## 3. FMan Microcode

**[SPEC]** Version: **210.10.1** (proprietary, PCD-capable, package ≥209 required)  
**[SPEC]** Dmesg: `FMan-Controller code (ver 210.10.1) (0xd20a01)`  
**[SPEC]** PCD Init: `FM_PCD_Init::ext timers 4, muram offset 0x3f508, InternalBufMgmtMuramArea 0x40200 size 0x8100`  
**[SPEC]** Stored in SPI flash mtd3 ("fman-ucode", 0x400000-0x500000 = 1 MiB)  
**[NOTE]** The "for LS1043 r1.0" label on the proprietary 210.10.1 blob is cosmetic — this microcode is correct for LS1046A.

## 4. ASK Kernel Modules (3 loaded)

**[SPEC]** cdx.ko: 622,592 bytes, 0 dependents. CDX (Connection Data eXchange) hardware flow table manager. Manages FMan PCD Coarse Classifier (CC) trees, KeyGen schemes, OH ports. Exposes `/dev/cdx_ctrl` (240:0).  
**[SPEC]** fci.ko: 12,288 bytes, 1 dependent (cdx). Fastpath Control Interface — CMM↔CDX IPC via chardev ioctl + ring buffer. 5 internal consumers.  
**[SPEC]** auto_bridge.ko: 40,960 bytes, 1 dependent on cdx/fci. L2 bridge flow detection via ebtables BROUTING hooks.  

**[BUG] auto_bridge UAF crash**: The `auto_bridge.ko` triggers a use-after-free kernel panic when Ethernet cables are connected and traffic flows across interfaces. Symptom: `BUG: scheduling while atomic: kworker/u16:X` → RCU stall → board freeze. Root cause: the `abm_ebt_hook` / `abm_l2flow_find` path accesses freed memory. OpenWrt-ASK ships `auto_bridge.ko` by default; workaround is to keep non-management interfaces DOWN. Same bug observed on our VyOS build.

**[SPEC]** fp_netfilter: hooks registered at T+3.33s, fire continuously when traffic crosses interfaces. Callback suppression pattern: ~20-34 callbacks per ~6-second interval.

## 5. Userspace Components

### 5.1 cmm — Connection Manager Daemon

**[SPEC]** Binary: `/usr/sbin/cmm`, 394,017 bytes (394 KB)  
**[SPEC]** Cmdline: `/usr/sbin/cmm -f /etc/config/fastforward`  
**[SPEC]** PID: 4493, running  
**[SPEC]** libnetfilter_conntrack: dynamically linked (`libnetfilter_conntrack.so.3.8.0`), symbol count via readelf: 0 (production build, symbols in shared library, not binary)  
**[SPEC]** Open file descriptors (28 total):

| FD | Type | Purpose |
|----|------|---------|
| 0 | /dev/null | stdin |
| 1 | /dev/console | stdout |
| 2, 14 | pipe:[2931] | internal IPC |
| 3-9 | socket:[4738-4745] | FCI/CDX ioctl channels |
| 10-13 | socket:[4746-4749] | CDX control |
| 15-17 | socket:[4750-4752] | CDX data |
| 18 | /proc/4493/net/pppoe | PPPoE discovery |
| 19-25 | socket:[5682-5695] | FCI data + netlink |
| 26 | /tmp/cmm.1430310949 | temp file |
| 27 | socket:[5696] | additional netlink |

**[SPEC]** FastForward config (`/etc/config/fastforward`): Excludes FTP (tcp:21), SIP (udp:5060), PPTP (tcp:1723) from offload. Logging commented out.

### 5.2 dpa_app — FMan PCD Boot-Time Programmer

**[SPEC]** Binary: `/usr/bin/dpa_app`, 1,180,141 bytes (1.18 MB)  
**[SPEC]** Wrapper: `/usr/bin/dpa_app_wrapper` (shell script with strace capture to `/tmp/dpa_app.log`)  
**[SPEC]** Boot flow: `cdx_module_init` → `call_usermodehelper("/usr/bin/dpa_app", UMH_WAIT_PROC)` → `start_dpa_app successful` at T+10.88s  
**[SPEC]** dpa_app runs once at boot and exits. Not present in `ps` output.  
**[SPEC]** References: `/etc/fmc/config/hxs_pdl_v3.xml` (FMan hardware abstraction), 16 distribution types (cdx_ipv4multicast_dist, cdx_ipv6multicast_dist, cdx_ipv4frag_dist, cdx_ipv6frag_dist, etc.)

### 5.3 fmc — FMan Configuration Tool

**[SPEC]** Binary: `/usr/bin/fmc`, 1,246,789 bytes (1.25 MB)  
**[SPEC]** Usage: `fmc <pdl_input_file> <pcd_input_file>`  
**[SPEC]** Called internally by dpa_app (linked via libfm-arm.a)  
**[SPEC]** Config directory: `/etc/fmc/config/` containing `hxs_pdl_v3.xml`, `cfgdata.xsd`, `netpcd.xsd`

## 6. Libraries (5)

**[SPEC]** libfci.so.0.0.0: 65,535 bytes — CDX↔CMM IPC. Exports: `fci_open`, `fci_register_cb`, `fci_close`, `fci_cmd`, `fci_write`  
**[SPEC]** libcmm.so.0.0.0: 65,539 bytes — CMM internal shared library. Exports: `cmm_open`, `cmm_close`, `cmm_send`, `cmm_recv`  
**[SPEC]** libnetfilter_conntrack.so.3.8.0: 129,827 bytes — Netfilter conntrack userspace API. Exports: `nfct_build_conntrack`, `nfct_parse_conntrack`, `__snprintf_conntrack`, etc.  
**[SPEC]** libnfnetlink.so.0.2.0: 65,506 bytes — Netfilter netlink transport (version mark: `NFNETLINK_1.0.1`)  
**[SPEC]** fmlib (libfm-arm.a): static, linked into fmc and dpa_app — FMan configuration library  

## 7. Device Nodes

**[SPEC]** `/dev/cdx_ctrl` (240:0) — CDX control ioctl interface  
**[SPEC]** `/dev/fm0` (246:0) — FMan 0 root  
**[SPEC]** `/dev/fm0-pcd` (246:1) — PCD configuration  
**[SPEC]** `/dev/fm0-port-oh{0-5}` (246:2-7) — 6 Offline parsing ports  
**[SPEC]** `/dev/fm0-port-rx{0-7}` (246:8-15) — 8 RX port FQID bindings  
**[SPEC]** `/dev/fm0-port-tx{0-7}` (246:16-23) — 8 TX port FQID bindings  

## 8. Configuration Files

| File | Size | Purpose |
|------|------|---------|
| `/etc/cdx_pcd.xml` | 18,172 | 16 CC tree distributions |
| `/etc/cdx_cfg.xml` | 833 | Port→policy binding (5 eth + 2 OH) |
| `/etc/cdx_sp.xml` | 7,252 | Service plane config |
| `/etc/cdx_cfg_dgw.xml` | 676 | Gateway board config variant |
| `/etc/cdx_cfg_ls1046_rdb.xml` | 677 | LS1046 RDB config variant |
| `/etc/cdx_cfg_rgw.xml` | 602 | Router gateway config variant |
| `/etc/config/fastforward` | ~500 | CMM offload exclusion list |
| `/etc/fmc/config/hxs_pdl_v3.xml` | — | FMC hardware abstraction PDL |
| `/etc/init.d/cdx` | 270 | CDX module loader |
| `/etc/init.d/cmm` | 2,597 | CMM service init |
| `/etc/init.d/fci` | — | FCI module loader |

## 9. FMan PCD / FQID Statistics

**[SPEC]** `/proc/fqid_stats/pcd/` — 7 entries (eth0-eth4, oh1, oh2) — PCD programmed but all per-port counter files = 0 bytes (no hardware flow hits)  
**[SPEC]** `/proc/fqid_stats/rx/` — RX port stats with entries  
**[SPEC]** `/proc/fqid_stats/tx/` — TX port stats with entries  
**[SPEC]** `/proc/fqid_stats/sa/` — Statistics area  
**[NOTE]** The PCD chain is pre-programmed with empty CC trees (hash tables allocated, no per-flow keys installed). Per-flow keys are expected to be installed by CMM when conntrack events fire. Since conntrack never creates new entries, the CC trees remain empty and all packets miss in hardware.

## 10. Conntrack Status

**[SPEC]** `/proc/net/stat/nf_conntrack` (all 4 CPUs): `entries=10` (0xa), all other counters = 0

| Metric | Value |
|--------|-------|
| entries | 10 (stale ALG expectations + boot-time broadcasts) |
| clashres | 0 |
| found | 0 |
| new | **0** |
| insert | **0** |
| insert_failed | 0 |
| drop, early_drop, icmp_error | 0 |
| expect_new, expect_create, expect_delete | 0 |

**[SPEC]** Sysctl conntrack parameters:
- `nf_conntrack_max = 262,144`
- `nf_conntrack_count = 10`
- `nf_conntrack_buckets = 262,144`
- `nf_conntrack_events = 2` (enabled)
- `nf_conntrack_acct = 1`
- `nf_conntrack_checksum = 0` (disabled — typical for hardware offload)
- `nf_conntrack_tcp_be_liberal = 1`
- `nf_conntrack_tcp_loose = 1`
- `nf_conntrack_expect_max = 4,096`

**[BUG] Deaf conntrack — Kernel conntrack never creates entries**: Despite `CONFIG_NF_CONNTRACK=y`, `nf_conntrack.enable_hooks=1` on cmdline, and `fp_netfilter` hooks firing actively, `new=0, insert=0` — no new conntrack entries are ever created regardless of traffic volume. The `enable_hooks` cmdline parameter is consumed by the kernel but does not lead to functional hook registration at the `nf_ct_netns_get()` level. The 10 existing entries are stale from ALG helper expectations (FTP/SIP/TFTP/PPTP/H323) and boot-time broadcast traffic (DHCP, mDNS). Affects ALL NXP SDK kernel versions (6.12.49 and 6.12.74).

## 11. Boot Sequence (from dmesg)

```
T+0.014  BMan ver:0a02,02,01
T+0.016  QMan ver:0a01,03,02,01
T+2.377  SPI flash: "fman-ucode" partition at 0x400000-0x500000
T+2.517  FMan-Controller code (ver 210.10.1) (0xd20a01)
T+2.546  FM_PCD_Init ext timers=4, muram offset=0x3f508
T+2.580  fsl_mac: FSL FMan MAC API — 5 MEMAC devices probed (MACs at e2000, e8000, ea000, f0000, f2000)
T+2.754  fsl_oh: FSL FMan Offline Parsing port driver — OH port@83000 (no buffer pool, fragmentation disabled)
T+3.332  fp_netfilter: hooks registered successfully
T+10.88  cdx_module_init::start_dpa_app successful
```

**[NOTE]** The OH (Offline Host) ports at oh@2/oh@3 are allocated for IPsec (OH1) and WiFi (OH2) offload. oh@2 has no buffer pool — fragmentation not enabled.

## 12. Interface State

| Port | DT Node | MAC | Status | Function |
|------|---------|-----|--------|----------|
| eth0 | MAC5/e8000 | e8:f6:d7:00:15:ff | UP, 192.168.1.190/16 | Management RJ45 (left) |
| eth1 | MAC6/ea000 | e8:f6:d7:00:16:00 | DOWN | RJ45 center |
| eth2 | MAC2/e2000 | e8:f6:d7:00:16:01 | DOWN | RJ45 right |
| eth3 | MAC9/f0000 | e8:f6:d7:00:16:02 | DOWN | SFP+ left (10G) |
| eth4 | MAC10/f2000 | e8:f6:d7:00:16:03 | DOWN | SFP+ right (10G) |

**[NOTE]** Ports eth1-eth4 are intentionally DOWN to avoid `auto_bridge.ko` UAF crash. The board has no bridge device — `auto_bridge.ko` hooks at ebtables BROUTING and intercepts L2 forwarding between all interfaces even without an explicit bridge.

## 13. APK Package Inventory (ASK-related)

**[SPEC]** Package manager: APK (Alpine Package Keeper), not opkg  
**[SPEC]** ASK packages installed:

| Package | Contents |
|---------|----------|
| kmod-ask-cdx | `/etc/init.d/cdx`, `cdx.ko` |
| kmod-ask-fci | `fci.ko` |
| kmod-ask-auto-bridge | `auto_bridge.ko` |
| ask-cmm | `/etc/config/fastforward`, `/etc/init.d/cmm`, `/usr/sbin/cmm`, `libcmm.so` |
| ask-dpa-app | `/etc/cdx_*.xml`, `/usr/bin/dpa_app`, `/usr/bin/dpa_app_wrapper` |
| fmc | `/usr/bin/fmc`, `/etc/fmc/config/*` |
| fmlib | `libfm-arm.a` (static) |
| libfci | `libfci.so` |
| kmod-nft-offload | `nft_flow_offload.ko` |

## 14. SSH Access

**[SPEC]** SSH server: dropbear (PID 8710, `/usr/sbin/dropbear -F -P /var/run/dropbear.main.pid -p 22 -K 300 -T 3`)  
**[SPEC]** Also installed: OpenSSH sshd (`/usr/sbin/sshd`, 524,804 bytes) — not running  
**[SPEC]** Firewall: nftables fw4 (INPUT chain: policy drop, rule order is critical)  
**[SPEC]** SSH fix: `nft insert rule inet fw4 input handle 258 tcp dport 22 accept` — inserts before `jump handle_reject`  
**[NOTE]** Dropbear's authorized_keys is at `/etc/dropbear/authorized_keys`. The RSA host key (3072-bit) is embedded in the initramfs. Password auth: `PasswordAuth on`, `RootPasswordAuth on`.

## 15. Comparison — VyOS vs OpenWrt-ASK

| Metric | VyOS (nxp-sdk, 6.12.49) | OpenWrt-ASK (6.12.74) |
|--------|--------------------------|------------------------|
| dpa_app | 1,180,141 bytes | 1,180,141 bytes |
| cmm | 394,017 bytes | 394,017 bytes |
| fmc | — | 1,246,789 bytes |
| cdx.ko | 622,592 bytes | 622,592 bytes |
| fci.ko | 12,288 bytes | 12,288 bytes |
| auto_bridge.ko | 40,960 bytes | 40,960 bytes |
| FMan ucode | 210.10.1 | 210.10.1 |
| conntrack entries | 0 | 10 (stale) |
| conntrack new | 0 | 0 |
| conntrack insert | 0 | 0 |
| fp_netfilter activity | Boot only | Continuous (~20-34 cb/6s) |
| enable_hooks cmdline | Consumed, no effect | Consumed, no effect |
| SSH server | OpenSSH | dropbear |
| Firewall | iptables | nftables fw4 |

**[NOTE]** The key difference is fp_netfilter activity. On OpenWrt-ASK, continuous broadcast traffic on the /16 subnet triggers constant `ct: ifindex changed` / `ct: iif changed` messages from the 10 stale conntrack entries. On VyOS with 0 entries, hooks have nothing to read. The underlying conntrack deafness is identical on both.

## 16. Operational Workarounds

**[NOTE]** The board is unstable with Ethernet connected due to `auto_bridge.ko` UAF bug. Workaround:
1. Boot without Ethernet cables
2. Via serial: `ip link set eth1 down; ip link set eth2 down; ip link set eth3 down; ip link set eth4 down`
3. Assign IP directly: `ip addr add 192.168.1.190/16 dev eth0`
4. Connect Ethernet — board can now be reached via SSH

**[NOTE]** nftables fw4 INPUT chain has `policy drop` with `jump handle_reject` before catch-all rules. Any new accept rules must be inserted BEFORE the handle_reject jump to take effect.

## 17. All ASK-Related Files (complete find)

```
/usr/sbin/cmm                         — Connection Manager daemon
/usr/bin/dpa_app                      — FMan PCD boot-time programmer
/usr/bin/dpa_app_wrapper              — Debug wrapper with strace
/usr/bin/fmc                          — FMan Configuration tool
/usr/lib/libfci.so.0.0.0              — CDX↔CMM IPC library
/usr/lib/libcmm.so.0.0.0              — CMM internal library
/usr/lib/libnetfilter_conntrack.so.3.8.0 — Netfilter conntrack API
/usr/lib/libnfnetlink.so.0.2.0        — Netfilter netlink transport
/lib/modules/6.12.74/cdx.ko           — Hardware flow table manager
/lib/modules/6.12.74/fci.ko           — Fastpath Control Interface
/lib/modules/6.12.74/nft_flow_offload.ko — nftables flow offload
/lib/modules/6.12.74/auto_bridge.ko   — L2 bridge flow detection
/etc/cdx_pcd.xml                      — PCD hash table distributions (16 tables)
/etc/cdx_cfg.xml                      — Port→policy binding
/etc/cdx_sp.xml                       — Service plane config
/etc/cdx_cfg_dgw.xml                  — Gateway board config
/etc/cdx_cfg_ls1046_rdb.xml           — LS1046 RDB config
/etc/cdx_cfg_rgw.xml                  — Router gateway config
/etc/config/fastforward               — CMM offload exclusion
/etc/fmc/config/hxs_pdl_v3.xml        — FMC hardware abstraction
/etc/init.d/cdx                       — CDX module init
/etc/init.d/cmm                       — CMM service init
/etc/init.d/fci                       — FCI module init
/sbin/askfirst                        — ASK early-init helper
/dev/cdx_ctrl                         — CDX ioctl chardev (240:0)
/dev/fm0                              — FMan root (246:0)
/dev/fm0-pcd                          — PCD config (246:1)
/dev/fm0-port-oh{0-5}                 — OH ports (246:2-7)
/dev/fm0-port-rx{0-7}                 — RX ports (246:8-15)
/dev/fm0-port-tx{0-7}                 — TX ports (246:16-23)
```
