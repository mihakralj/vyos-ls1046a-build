# OPNsense ASK Offload Architecture

**Version 1.0 · HADS 1.0.0**
**Date:** 2026-06-28
**Source:** https://opnsense.mono.si/ — mono-gateway-26.1.6.pkg deep review

## AI READING INSTRUCTION

This document catalogs the FreeBSD/OPNsense ASK 1.x offload stack as recovered from
the `mono-gateway-26.1.6.pkg` (1.8 MB, SHA256 da7a08ff) installed on OPNsense 26.1.5
for the Mono Gateway DK (NXP LS1046A). Every `[SPEC]` fact was verified via binary
inspection and `/+MANIFEST` cross-reference. `[NOTE]` provides architecture analysis.
`[GUIDANCE]` prescribes VyOS-ASK implementation decisions.

## 1. Component Inventory

**[SPEC]** The `mono-gateway` package contains 34 files (6.1 MB uncompressed):

| Path | Size | Role |
|------|------|------|
| `boot/modules/cdx.ko` | 259 KB | CDX flow table manager (FreeBSD ELF relocatable) |
| `boot/modules/fci.ko` | 13 KB | CMM↔CDX IPC (FPP_CMD protocol) |
| `boot/modules/auto_bridge.ko` | 20 KB | L2 bridge flow detection (ebtables hooks) |
| `boot/modules/pf_notify.ko` | 18 KB | PF firewall state notification → CMM |
| `boot/modules/sfpled.ko` | 15 KB | SFP cage LED control |
| `boot/modules/lp5812.ko` | 14 KB | LP5812 RGBW LED controller |
| `boot/modules/emc2302.ko` | 13 KB | EMC2305 PWM fan controller |
| `boot/modules/ina2xx.ko` | 13 KB | INA234 power sensor (with ina234 OF match) |
| `boot/modules/pcf2131.ko` | 13 KB | PCF2131 RTC |
| `boot/modules/caam.ko` | 88 KB | CAAM hardware crypto |
| `boot/modules/tmp431.ko` | 13 KB | TMP431 temperature sensor |
| `boot/modules/mwifiex.ko` | 1.5 MB | NXP 88W9098 WiFi (optional) |
| `boot/modules/dpaa_wifi.ko` | 29 KB | DPAA WiFi offload driver |
| `usr/local/sbin/cmm` | 114 KB | Connection manager daemon (FreeBSD ELF) |
| `usr/local/sbin/cmmctl` | — | CMM control CLI |
| `usr/local/sbin/dpa_app` | 1.8 MB | PCD classification rule loader |
| `usr/local/sbin/fmc` | 1.9 MB | FMan configuration tool |
| `usr/local/sbin/fand` | — | Multi-zone PID fan controller |
| `etc/cdx_cfg.xml` | 833 B | Port→policy binding (5 phys + 2 OH) |
| `etc/cdx_pcd.xml` | 18.2 KB | 16 CC trees, 18 distributions, 9 policies |
| `etc/cdx_sp.xml` | 8.9 KB | NetPDL soft-parser schema (PPPoE, Eth, IPv4/6, UDP/TCP, ESP) |
| `etc/fmc/config/hxs_pdl_v3.xml` | — | FMC PDL v3 hardware parser config |

**[SPEC]** Module loading order (from `01-mono-modules`):
```
cdx → auto_bridge → pf_notify → fci → lp5812 → sfpled → emc2302 → ina2xx → pcf2131 → tmp431 → cryptodev → caam
```

**[NOTE]** `pf_notify.ko` is loaded AFTER `auto_bridge.ko` and BEFORE `fci.ko`. This
ordering is intentional: auto_bridge hooks ebtables first, pf_notify subscribes to PF
state events second, and fci provides the CDX IPC channel last. CMM requires cdx +
fci both loaded before starting.

## 2. pf_notify.ko — The Critical FreeBSD Bypass

**[SPEC]** `pf_notify.ko` is the FreeBSD equivalent of Linux's `nf_conntrack_netlink`.
It creates `/dev/pfnotify` as a character device. CMM opens this device and receives
PF firewall state change notifications directly — bypassing the conntrack subsystem
entirely.

**[SPEC]** Strings extracted from `pf_notify.ko`:
- `PF state change notification`
- `PF state counter update misses (state gone)`
- `PF state counter updates from CDX`
- `pf_find_state_byid`
- `pf_notify: loaded (ring_size=%d)`
- `pf_notify: failed to create /dev/%s`
- Module dependencies: `pf_notify_depend_on_kernel`, `pf_notify_depend_on_pf`

**[SPEC]** CMM references to pf_notify (from the FreeBSD cmm binary):
- `is pf_notify.ko loaded?`
- `will re-offload if PF state alive`
- `/dev/pfnotify`
- `cmm[%s]: conn: /dev/pfnotify closed`

**[NOTE]** This is the fundamental architectural difference between OPNsense and Linux
ASK implementations:

```
FreeBSD/OPNsense (WORKING):
  PF firewall → /dev/pfnotify → CMM → FCI → CDX → FMan PCD

Linux/VyOS (BLOCKED):
  nf_conntrack → ctnetlink → CMM → FCI → CDX → FMan PCD
  [BLOCKED: conntrack new=0, CMM receives no events]
```

**[GUIDANCE]** For VyOS-ASK, the conntrack pipeline must be fixed OR a Linux equivalent
of `pf_notify.ko` must be created. Options:
1. Fix conntrack: ensure `nf_conntrack_events=2` + `nf_conntrack_netlink` groups are
   correctly subscribed
2. Create `nf_notify.ko`: a Linux kernel module that creates `/dev/nfnotify` and
   forwards conntrack NEW/UPDATE/DESTROY events directly without netlink
3. Port `pf_notify.ko` to Linux: difficult due to FreeBSD PF API dependencies

## 3. PCD Configuration

**[SPEC]** The OPNsense `cdx_pcd.xml` (525 lines) defines:

### 3.1 CC Hash Tables (16 tables)

All tables use `shared="true"`, `statistics="byteframe"`, `external="yes"` hash:

| Name | Max | Mask | Keysize | Aging |
|------|-----|------|---------|-------|
| cdx_udp4_cc | 512 | 0x7fff | 14 | yes |
| cdx_tcp4_cc | 512 | 0x7fff | 14 | yes |
| cdx_udp6_cc | 512 | 0x7fff | 38 | yes |
| cdx_tcp6_cc | 512 | 0x7fff | 38 | yes |
| cdx_esp4_cc | 512 | 0xff | 10 | yes |
| cdx_esp6_cc | 512 | 0xff | 22 | yes |
| cdx_multicast4_cc | 512 | 0xff | 10 | no |
| cdx_multicast6_cc | 512 | 0xff | 34 | no |
| cdx_ethernet_cc | 512 | 0xff | 15 | yes |
| cdx_pppoe_cc | 512 | 0xf | 11 | yes |
| cdx_tuple3udp4_cc | 512 | 0xff | 8 | yes |
| cdx_tuple3tcp4_cc | 512 | 0xf | 8 | yes |
| cdx_tuple3udp6_cc | 512 | 0xff | 20 | yes |
| cdx_tuple3tcp6_cc | 512 | 0xf | 20 | yes |
| cdx_frag4_cc | 512 | 0xf | 12 | no |
| cdx_frag6_cc | 512 | 0xf | 38 | no |

### 3.2 Distributions (18 total)

Each distribution binds protocol→key fields→FQ base→CC table:

| Distribution | Key fields | FQ base |
|---|---|---|
| cdx_udp4_dist | ipv4.{src,dst,nextp} + udp.{sport,dport} | 0x1000 |
| cdx_tcp4_dist | ipv4.{src,dst,nextp} + tcp.{sport,dport} | 0x1010 |
| cdx_udp6_dist | ipv6.{src,dst,nexthdr} + udp.{sport,dport} | 0x1020 |
| cdx_tcp6_dist | ipv6.{src,dst,nexthdr} + tcp.{sport,dport} | 0x1030 |
| cdx_ipv4multicast_dist | ipv4.{src,dst,nextp} | 0x1040 |
| cdx_ipv6multicast_dist | ipv6.{src,dst,nexthdr} | 0x1050 |
| cdx_esp4_dist | ipv4.{dst,nextp} + ipsec_esp.spi | 0x1060 |
| cdx_esp6_dist | ipv6.{dst,nexthdr} + ipsec_esp.spi | 0x1070 |
| cdx_pppoe_dist | ethernet.{src,type} + pppoe.session_ID | 0x1080 |
| cdx_tup3udp4_dist | ipv4.{dst,nextp} + udp.dport | 0x1090 |
| cdx_tup3tcp4_dist | ipv4.{dst,nextp} + tcp.dport | 0x10a0 |
| cdx_tup3udp6_dist | ipv6.{dst,nexthdr} + udp.dport | 0x10b0 |
| cdx_tup3tcp6_dist | ipv6.{dst,nexthdr} + tcp.dport | 0x10c0 |
| cdx_ipv4frag_dist | ipv4.{src,dst,nextp} | 0x10d0 |
| cdx_ipv6frag_dist | ipv6.{src,dst,nexthdr} | 0x10e0 |
| cdx_ethernet_dist | ethernet.{dst,src,type} | 0x10000 (128 Qs!) |

All distributions use `combine portid="true" offset="16" mask="0xF"` for port-indexed
CC lookup. The ethernet distribution gets 128 queues (base 0x10000) — the largest
allocation.

### 3.3 Port Policies (9 total)

All 9 policies share the identical distribution order (IPsec first, then L4, then L3,
then L2 fallback):

```
esp4 → esp6 → udp4 → tcp4 → udp6 → tcp6 → multicast4 → multicast6 →
tuple3udp4 → tuple3udp6 → pppoe → ethernet
```

Frag distributions (`cdx_ipv4frag_dist`, `cdx_ipv6frag_dist`) are COMMENTED OUT in
all policies — fragment reassembly is done in software.

**[NOTE]** The ethernet distribution is the catch-all — it's the LAST distribution
in every policy, meaning it matches only when no L3/L4 distribution caught the frame.
Its 128 queues (vs. 1 for all others) suggest it's the bridge offload path.

### 3.4 Soft Parser Configuration (cdx_sp.xml)

**[SPEC]** The `cdx_sp.xml` (185 lines) defines 6 NetPDL protocol handlers:

| Protocol | Key behavior |
|---|---|
| pppoeschema | PPPoE LCP packets → enqueue directly; data → return to HXS |
| ethernetschema | OH port (logicalportid ≥ 9): re-parse inner IP header from offset 112 |
| ipv4schema | Drop TTL≤1; exit on multicast; redirect ipv4.nextp==0x29 → ipv6 |
| ipv6schema | Drop hop≤1; exit on multicast |
| udpschema | UDP-encapsulated ESP (port 4500): IKE NATT → enqueue; set L3R flags |
| tcpschema | TCP SYN/FIN/RST (flags & 7) → enqueue directly; set L3R flags |

## 4. CMM Integration

**[SPEC]** The OPNsense CMM run control script (`rc.d/cmm`):
- Requires: `NETWORKING pf dpa_app` — dpa_app MUST complete PCD programming before CMM starts
- Flags: `-d 1` (debug level 1)
- Checks that `cdx.ko` and `fci.ko` are loaded via `kldstat -q -m`
- Restart trigger: `actions_cmm.conf` restarts CMM on interface changes

**[SPEC]** CMM daemon flags (from binary strings):
- `-d level` — debug level (default 0)
- `-D path` — deny-rule config file (default `/usr/local/etc/cmm_deny.conf`)
- `-f` — foreground mode
- `-p pidfile` — PID file path

**[NOTE]** The OPNsense CMM binary is 114 KB (FreeBSD ELF, not stripped). This is 16×
smaller than the Linux cmm binary (1.87 MB) because:
1. It uses FreeBSD libc directly (no glibc/musl compat layer)
2. It links dynamically to `/libexec/ld-elf.so.1` (FreeBSD runtime linker)
3. It does NOT include `libnetfilter_conntrack` (uses `/dev/pfnotify` instead)
4. It does NOT include `libfci` as a separate library (likely statically linked)

## 5. Kernel Module Signing

**[SPEC]** All 13 OPNsense kernel modules are FreeBSD `.ko` ELF shared objects
(not Linux `.ko` relocatables). They use FreeBSD's module metadata system
(`_mod_metadata_md_*`, `__set_modmetadata_set_sym__*`) instead of Linux's
`module_init()`/`module_exit()`.

**[NOTE]** This means OPNsense modules cannot be directly used on Linux. The source
code (from `we-are-mono/ASK`) is the common ancestor — it must be compiled
separately for each OS.

## 6. Boot Integration

**[SPEC]** The early boot hook (`01-mono-modules`) runs at `early` syshook stage
(before networking). The kernel update hook (`20-kernel-update`) runs at `stop`
stage and handles DTB migration from `/boot/dtb/freescale/` to `/boot/msdos/dtb/`
for U-Boot's FAT partition.

**[SPEC]** U-Boot boot flow on OPNsense:
```
bootcmd='run opnsense || run recovery'
```
The `opnsense` U-Boot variable loads kernel from FAT partition (`/boot/msdos/kernel.img`),
then boots with the Gateway DTB.

## 7. Reference URLs

| Resource | URL |
|---|---|
| Package repo | https://opnsense.mono.si/FreeBSD:14:aarch64/26.1/mono/ |
| mono-gateway pkg | https://opnsense.mono.si/FreeBSD:14:aarch64/26.1/mono/All/mono-gateway-26.1.6.pkg |
| Full OPNsense image | https://opnsense.mono.si/releases/26.1/OPNsense-26.1.5-arm-aarch64-GATEWAY.img.bz2 |
| Experimental cdx.ko | https://opnsense.mono.si/experimental/cdx.ko |
| Installation guide | https://opnsense.mono.si/releases/26.1/ |
