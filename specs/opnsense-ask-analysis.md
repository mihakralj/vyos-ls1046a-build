# OPNsense ASK Comparative Analysis — VyOS Linux Port Implications

**Version 1.0 · HADS 1.0.0**
**Date:** 2026-06-28
**Branch:** `nxp-sdk`
**Prerequisite Specs:** arch/opnsense-ask-architecture.md, specs/vyos-ask-development-reference.md

## AI READING INSTRUCTION

This spec analyzes the FreeBSD/OPNsense ASK 1.x offload stack (mono-gateway-26.1.6.pkg)
and identifies every structural difference from the VyOS Linux port. Each `[DIFF]`
documents a gap that must be bridged. `[VERIFIED]` marks aspects we have confirmed
working on our Linux build.

## 1. Component Equivalence Matrix

**[SPEC]** Component-by-component comparison between OPNsense (working) and VyOS Linux (in progress):

| Component | OPNsense (FreeBSD) | VyOS Linux | Status |
|---|---|---|---|
| Flow table manager | cdx.ko (259 KB, FreeBSD ELF) | cdx.ko (309 KB, Linux ELF) | `[VERIFIED]` loads, /dev/cdx_ctrl works |
| CMM↔CDX IPC | fci.ko (13 KB) | fci.ko (16 KB) | `[VERIFIED]` loads, /proc/fci 0 errors |
| L2 bridge detection | auto_bridge.ko (20 KB) | **REMOVED** (UAF crash) | `[GAP]` must fix or replace |
| Firewall state → CMM | pf_notify.ko (18 KB, /dev/pfnotify) | nf_conntrack_netlink (netlink) | `[GAP]` conntrack new=0 |
| Connection manager | cmm (114 KB, FreeBSD ELF) | cmm (1.87 MB, glibc Linux) | `[VERIFIED]` runs, bridge-manual-mode |
| PCD config loader | dpa_app (1.8 MB) | dpa_app (1.36 MB) | `[VERIFIED]` start_dpa_app successful |
| FMan config tool | fmc (1.9 MB) | fmc (built, same version) | `[VERIFIED]` FMC programs PCD |
| Fan control | fand | fan-pid (Python 3) | `[VERIFIED]` working |
| LED control | lp5812.ko | lp5812.ko (same source) | `[VERIFIED]` working |
| SFP LED | sfpled.ko | **not ported** | `[LOW PRIORITY]` cosmetic |

## 2. The Conntrack Deadlock — Root Cause Analysis

**[SPEC]** The Linux CMM binary expects flow events from THREE sources:

| Source | Netlink protocol | Linux CMM socket | Status on DUT |
|---|---|---|---|
| Conntrack events | NETLINK_NETFILTER (12) | groups=0x0 | **DEAF** — nfct_open with zero subscriptions |
| Bridge port events | NETLINK_ROUTE (0) | groups=0x4 | Working — tracks neighbor ARP |
| L2 flow events | NETLINK_L2FLOW (33) | socket() fails | "Protocol not supported" — triggers MANUAL fallback |
| FCI commands | NETLINK_FF (30) | groups=0x1 | **WORKING** — heartbeat rc=0 every 30s |

**[SPEC]** OPNsense CMM event sources:

| Source | Mechanism | OPNsense CMM |
|---|---|---|
| PF state changes | `/dev/pfnotify` (chardev) | **PRIMARY** — all flow events |
| Bridge port events | PF/ifconfig hooks | Working |
| FCI commands | FCI chardev | Working |

**[NOTE]** The key architectural difference: OPNsense uses a SINGLE event source
(`/dev/pfnotify`) driven by the PF firewall. Linux CMM relies on THREE separate
netlink sockets, two of which (conntrack, L2FLOW) are non-functional on our build.

**[DIFF]** `pf_notify.ko` exports:
- `pf_find_state_byid` — looks up PF state table entries
- A ring buffer for state change notifications
- `/dev/pfnotify` chardev for userspace reads

The Linux equivalent would need to:
1. Hook `nf_conntrack`'s `gc_work` or `nf_ct_destroy` callback chain
2. Buffer NEW/UPDATE/DESTROY events in a kernel ring buffer
3. Expose a `/dev/nfnotify` chardev for CMM to read
4. OR fix the existing `nf_conntrack_netlink` groups subscription

## 3. auto_bridge.ko — Linux UAF vs FreeBSD Stability

**[SPEC]** OPNsense includes `auto_bridge.ko` (20 KB) and it works on FreeBSD.
Our Linux build REMOVED it after repeated UAF crashes.

**[NOTE]** The UAF crash on Linux (commit `edd7750`, since reverted) was triggered by
the L2 flow timer/netlink path: `auto_bridge` hooks ebtables BROUTING, captures skb
metadata, and creates an L2 flow entry with a timer callback that accesses the skb
after it may have been freed.

**[DIFF]** Possible reasons FreeBSD auto_bridge does NOT crash:
1. FreeBSD's `mbuf` (not `sk_buff`) has different lifetime semantics
2. FreeBSD ebtables-equivalent (`ng_bridge`?) has different hook ordering
3. The FreeBSD version may use `m_dup` or refcounting instead of raw pointer capture
4. Netlink on FreeBSD is routed through a different subsystem

**[GUIDANCE]** Three options for VyOS Linux:
1. **Fix auto_bridge UAF**: Add `skb_get()`/`skb_put()` refcounting, verify timer
   callback doesn't access freed skb
2. **Port pf_notify approach**: Create `/dev/nfnotify` chardev, bypass auto_bridge
3. **Embrace manual bridge mode**: Accept that CMM bridge-manual-mode works (proven),
   rely on Linux kernel bridge + STP for bridge membership tracking

## 4. PCD Configuration Equivalence

**[VERIFIED]** Our TFTP-stashed `cdx_pcd.xml` is the same file as the OPNsense version
(18.2 KB, 525 lines). The PCD classification tables are identical — 16 CC trees,
18 distributions, 9 policies.

**[VERIFIED]** Our `cdx_cfg.xml` (port→policy binding) matches the OPNsense version
(833 B, 7 ports: 5 physical + 2 offline OH).

**[SPEC]** The OPNsense `cdx_sp.xml` (soft-parser config, 185 lines) was NOT present
in our TFTP stash. This file defines NetPDL protocol handlers for PPPoE, Ethernet OH
port header re-parsing, TTL/hop-limit filtering, UDP-encapsulated ESP detection, and
TCP flag filtering. **Without `cdx_sp.xml`, the soft parser operates with default
behavior** — this may cause incorrect classification of PPPoE-encapsulated traffic
or ESP-in-UDP packets on OH ports.

**[GUIDANCE]** Deploy `cdx_sp.xml` to `/etc/cdx_sp.xml` alongside `cdx_pcd.xml` and
`cdx_cfg.xml` to match the OPNsense PCD configuration exactly.

## 5. Module Loading Order

**[SPEC]** OPNsense loading order (verified from `01-mono-modules`):
```
cdx → auto_bridge → pf_notify → fci
```

**[SPEC]** VyOS Linux current loading order:
```
cdx → fci
```
(auto_bridge removed, no pf_notify equivalent)

**[DIFF]** The OPNsense order puts the event sources (auto_bridge, pf_notify) BEFORE
the IPC channel (fci). This ensures CMM can receive events immediately when fci
connects cdx.

**[GUIDANCE]** If auto_bridge is fixed, change VyOS loading order to:
```
cdx → auto_bridge → fci
```

## 6. CMM Configuration Differences

**[SPEC]** OPNsense CMM:
- Config: `/usr/local/etc/cmm_deny.conf` (deny-rule config file)
- Debug: `-d 1`
- PID file: `/var/run/cmm.pid`
- Requires: `pf` (PF firewall) and `dpa_app` services running

**[SPEC]** VyOS Linux CMM:
- Config: `/etc/config/fastforward` (fastforward config)
- Run: `/usr/local/bin/cmm -f /etc/config/fastforward`
- No deny-rule config file
- Manual bridge mode (NETLINK_L2FLOW socket fails)

**[DIFF]** The Linux CMM uses a fastforward config format (FTP bypass rules, etc.).
The OPNsense CMM uses a deny-rule config for similar purposes.

## 7. Verified Working Pipeline Components

**[VERIFIED]** The following components are confirmed working on our VyOS Linux build
(2026-06-28, kernel 6.12.49-gdf24f9428e38-dirty):

| Step | Component | Evidence |
|---|---|---|
| M1 | Kernel boots with CONFIG_BRIDGE=y | `ip link add br0 type bridge` succeeds |
| M2 | cdx.ko loads | `/dev/cdx_ctrl` created, bridge_init done |
| M3 | fci.ko loads | `/proc/fci` shows 0 errors |
| M4 | dpa_app runs | `start_dpa_app successful` in dmesg |
| M5 | FMC programs PCD | 16 CC trees, 18 distributions pushed |
| M6 | CMM starts | PID persists, bridge-manual-mode |
| M7 | CMM detects bridges | `__cmmGetBridges::77: br0 is a bridge` |
| M8 | CDX bridge commands | `fcode=0x0011 rc=0` on port add |
| M9 | FCI heartbeat | `fcode=0x0e09 rc=0` every 30s |
| M10 | CMM neighbor tracking | ARP events on 192.168.1.1/2 |

**[BLOCKED]** The following are NOT yet working:

| Gap | Component | Blocker |
|---|---|---|
| G1 | Conntrack events → CMM | conntrack new=0, nfct groups=0x0 |
| G2 | L2 flow events | NETLINK_L2FLOW (proto 33) not registered |
| G3 | auto_bridge.ko | UAF crash on L2 flow timer |
| G4 | Bridge offload verification | Network loop risk without STP |

## 8. Recommendations

**[GUIDANCE]** Priority order for closing the gaps:

1. **Fix conntrack events (G1)** — this unblocks the primary flow offload path.
   Investigate: is `nf_conntrack_netlink` loaded? Are `nf_conntrack_events=2`?
   Does CMM subscribe to the correct conntrack groups?

2. **Create nf_notify.ko (G2+G3)** — a single kernel module that:
   - Hooks `nf_conntrack` lifecycle callbacks
   - Creates `/dev/nfnotify` chardev
   - Replaces both `auto_bridge.ko` and `nf_conntrack_netlink`

3. **Bridge offload with STP (G4)** — after G1 or G2 is fixed:
   - Create bridge with `stp_state 1`
   - Add isolated ports (physically disconnected from the switch loop)
   - Monitor PCD counters via ethtool

4. **Deploy cdx_sp.xml** — copy the OPNsense soft-parser config for complete PCD
   behavior parity.
