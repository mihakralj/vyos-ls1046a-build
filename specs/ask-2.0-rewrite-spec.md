# ASK 2.0 — Clean-Room Rewrite of the NXP ASK Fast-Path for VyOS on LS1046A

**Status:** Draft v0.6 (supersedes v0.5 in entirety). 2026-05-11.
**Target hardware:** NXP LS1046A Mono Gateway DK (Cortex-A72 ×4 @ 1.6 GHz, FMan v3L, QMan v3, BMan v3, CAAM SEC 5.4, 384 KB MURAM).
**Target software:** Linux mainline kernel 6.18 LTS, ARM64, VyOS rolling release, FLAVOR=ask.
**Target microcode:** NXP proprietary 210-series fine classifier ucode (LS1046 r1.0 variant), baked into Mono Gateway firmware and exposed to U-Boot via SPI flash on every shipped unit.
**Reference implementations** (functional, not code donors):
- NXP ASK source archived at `we-are-mono/ASK` branch `mt-6.12.y` — kernel modules `cdx/`, `fci/`, `auto_bridge/`; userspace `cmm/`, `dpa_app/`.
- `we-are-mono/opnsense-deps/fastpath/` — FreeBSD port of the same stack, useful cross-reference for protocol-level behaviour.
- NXP ASK whitepaper `ASKTHOFOPERMWP.pdf` — operational model description.
- NXP application notes AN4785 (IPsec on QorIQ), AN4760 (FMC), AN12714 (CAAM secure keys).
**License intent:** GPL-2.0-or-later kernel parts; GPL-2.0-or-later userspace daemons; LGPL-2.1-or-later for `libask_fci`; the 210 microcode binary keeps NXP's binary EULA terms (redistribution path: Mono ships it on board, ASK 2.0 build pipeline never touches it).
**Naming convention.**
- **ASK 1.x** — the proprietary NXP-LSDK-derived stack as it ships today: `cdx.ko`, `auto_bridge.ko`, `cmm`, `dpa_app`, `libfci`, the 5797-line in-tree-hooks patch, the vendored SDK FMan/QMan/BMan drivers.
- **ASK 2.0** — this spec: `ask.ko`, `ask_bridge.ko`, `askd`, `ask-load`, `libask_fci`, a ~1500-line in-tree-hooks patch, and a ~200-line patch to `drivers/crypto/caam/qi.c`.

The umbrella brand **ASK** and the `FLAVOR=ask` build target carry forward unchanged.

---

## 0. Document discipline (read this first if you're an agent)

This spec is intended to be consumed by both human engineers and AI coding agents (Cline, Claude Code, Cursor). Sections are tagged where useful:

- `[AGENT-IMPLEMENTABLE]` — sufficient context that a competent agent should produce a working first pass without human supervision. Effort estimates assume agent-driven implementation with human review.
- `[HUMAN-REQUIRED]` — work that cannot be agent-implemented because it depends on hardware probing, NDA-protected behaviour, commercial negotiation, or judgement calls on undocumented silicon. Agents may assist with paperwork and analysis but cannot produce the deliverable alone.
- `[MIXED]` — agent does the bulk, human does the verification step on real hardware before the deliverable is accepted.

Sections without a tag are reference material — read but don't act on them.

When this document says "the agent" it means whatever coding agent is driving the implementation in the current session, not Claude specifically.

---

## 1. What ASK is, what 210 ucode is, and why the rewrite

### 1.1 What ASK is

The **NXP Application Solutions Kit (ASK)** is a hardware-accelerated packet-processing stack for QorIQ Layerscape SoCs. On LS1046A it converts established conntrack flows into FMan-resident flow-table entries: the first packet of a connection traverses the kernel slow path, conntrack catches it, ASK programs FMan to recognise subsequent packets of the same flow, and from then on FMan parses, classifies, modifies (NAT, TTL), and forwards the frame directly via BMI without an A72 core ever touching it. NXP's published number for this on a Layerscape router workload is **CPU utilisation below 5%** during line-rate forwarding (whitepaper `ASKTHOFOPERMWP.pdf`).

The ASK stack as shipped today is built on three layers:

1. **The 210-series FMan microcode** — proprietary NXP firmware program loaded by U-Boot into FMan's RISC engines at boot. Different from the public 106/108 microcode families on `github.com/nxp-qoriq/qoriq-fm-ucode` in one architecturally significant way: it implements **dynamic flow tables with high-rate host commands**, where the public ucode implements **static PCD compiled from XML at init time**. This is the architectural split Miha framed correctly as "coarse vs fine classifier" — `108_4_9` is coarse PCD, `210.x.x` is fine per-flow classification with NAT-rewrite-per-flow context.
2. **The kernel-side fast-path engine (CDX) and bridge-FDB watcher (auto_bridge)** — out-of-tree GPL modules with a ~5800-line in-tree hooks patch that exposes conntrack, xfrm, bridge, and ppp events to the engine.
3. **The userspace decision engine (CMM) and policy loader (dpa_app)** — daemons that consume conntrack events and translate them to FMan host commands via a netlink channel (`libfci`).

The Mono Gateway hardware ships with the 210 ucode in SPI flash. U-Boot loads it before kernel handoff and patches the DTB `fsl,fman-firmware` node so the kernel sees it as available firmware. **Every shipped Mono Gateway has 210 available at boot.** ASK 2.0 inherits this loading path unchanged — the rewrite never touches the microcode, never includes it in any image artefact, and never depends on its source (which doesn't exist outside NXP).

### 1.2 Why a rewrite

ASK 1.x has four practical problems:

1. **Kernel-version lock-in.** The proprietary CDX/auto_bridge modules and the 5800-line hooks patch were authored against Linux 5.4 and forward-ported piecemeal to 6.6. Mainline 6.18 LTS (released 2025-11-30, supported until December 2027) has materially different netfilter, xfrm, and bridge APIs. Carrying the legacy patch forward to 6.18 is a porting exercise that approaches the cost of a rewrite.
2. **Patch-surface fragility.** The 5800-line patch touches 64 mainline files including `net/bridge/`, `net/netfilter/`, `net/xfrm/`, `drivers/crypto/caam/`, and `drivers/net/ppp/`. Every mainline kernel update risks rejection in 3-way merge. Mono's `mt-6.12.y` branch is already deviating from upstream in ways that complicate VyOS rolling-release cadence.
3. **Vendored SDK drivers.** ASK 1.x carries 266 files of vendored `sdk_fman/` and `sdk_dpaa/` source (FMan v3L kernel driver, QMan portal, BMan portal). These are Kconfig-exclusive with mainline `fsl_dpa`/`fsl_dpaa_mac` — you cannot build a kernel that supports both flavors. The VyOS default flavor wants mainline DPAA1.
4. **No clean integration with VyOS modern facilities.** VPP in VyOS 1.5 LTS, `flowtable` as a software safety net, `XFRM_OFFLOAD` for non-fast-path crypto, generic netlink as the policy-channel idiom — none of these slot cleanly into ASK 1.x because the 1.x design predates them.

ASK 2.0 keeps every byte of value from the proprietary stack — **the 210 microcode, the dynamic flow-table model, the offline-port re-inject pattern for IPsec, the CMM decision-engine algorithms** — and re-implements the kernel-and-userspace plumbing against modern Linux. The microcode does the work; everything above it gets rewritten clean.

### 1.3 What this spec is NOT

- Not a rewrite of `fsl_dpaa1` mainline driver, FMan v3L kernel-side init, QMan/BMan portal management. Those belong to the default VyOS flavor's networking stack.
- Not a rewrite of `drivers/crypto/caam/` mainline driver. ASK 2.0 reuses it directly. See Section 8.
- Not a rewrite of the 210 microcode. The microcode is the silicon program; it cannot be re-implemented from a clean room without an internal NXP FMan ISA reference that nobody outside NXP has.
- Not a VPP plugin. VPP is a separate dataplane running on dedicated cores; ASK 2.0 and VPP coexist through documented kernel-userspace boundaries (Section 11), not through code merging.
- Not a tc-flower offload upstreaming project. tc-flower upstreaming for DPAA1 is a multi-year mainline conversation; ASK 2.0 v1.0 ships with a generic-netlink side-band channel and revisits upstream later (Section 14).

---

## 2. Hardware reference

Authoritative source: LS1046A Reference Manual chapter 8 (DPAA), specifically §8.7 (parser+KeyGen), §8.8 (CC), §8.9 (Policer), §8.10 (BMI). The implementer or agent MUST read these sections before writing code touching FMan programming; this spec is a roadmap, not a substitute. The RM is NDA-only — ask Miha or Mono for access.

### 2.1 FMan with 210 ucode

Each FMan port has, per the RM:

- **Parser** — runs proprietary microcode on a dedicated RISC engine inside FMan (NOT on the A72). The 210 ucode replaces the parser+classifier program from public 108 with a fine-grained per-flow classification engine. Both feed into KeyGen.
- **KeyGen** — extracts a hash key from the parse result, hashes it, looks up a base FQID. Under 108 this is used for RSS-style distribution; under 210 it's the front-half of flow-table lookup. 32 KeyGen schemes per FMan (silicon limit).
- **Flow Tables (210-specific)** — what 108 calls "Coarse Classifier" nodes, 210 manages as runtime-mutable per-flow tables. Entries are inserted, modified, and evicted by host commands. Per-entry actions: **modify-and-forward** (rewrite L2 + IP + L4 headers via stored action template, decrement TTL, fix checksums, enqueue to egress TX FQ); enqueue-to-CAAM-FQ for IPsec; drop; policer redirect.
- **Policer** — dual-rate token-bucket per port and per flow. ASK uses it for exception-rate limiting on the slow-path lift.
- **BMI / MURAM / IRAM** — DMA engine, shared scratchpad, microcode RAM. MURAM is **384 KB total** on LS1046A (verified from mainline DTSI `qoriq-fman3-0.dtsi`: `muram@0 { reg = <0x0 0x60000>; }`). After accounting for FIFOs and frame internal contexts, the available CC/flow-table partition is approximately **64-96 KB**, giving 500-750 hardware flow entries at ~128 bytes per entry. Operators routinely run more than this many simultaneous connections; the spec assumes a software flowtable fallback for overflow (Section 6.7).

### 2.2 MURAM partitioning under 210

Under 108, MURAM allocation is decided at FMC compile time from XML. Under 210, allocation is decided at boot when ASK 2.0 initialises the flow tables. The agent implementing `ask.ko::ask_dpaa.c` must:

- Read MURAM size from device tree (do not hardcode 384 KB; future SoCs may differ).
- Subtract reservations for FIFOs (per RM §8.10) — typical 240 KB on a 4-port + 2-OP config.
- Partition the remainder across 8-12 flow tables per `cdx_pcd.xml`-equivalent config.
- Refuse to boot loudly with a structured `dev_err` if the partition is oversubscribed. `dmesg | grep -F 'ASK MURAM exhausted'` is the operator-facing signal.
- Expose per-table allocation under `debugfs` at `/sys/kernel/debug/ask/muram/{table_name}`.

### 2.3 CAAM SEC 5.4

- 4 Job Rings (`caam_jr0` through `caam_jr3`).
- Queue Interface (QI) — the path ASK 2.0 uses. CAAM dequeues from QMan, processes the crypto descriptor, enqueues back to QMan. No CPU touch on the data path.
- Mainline driver: `drivers/crypto/caam/`, maintained by Pankaj Gupta and Gaurav Jain at NXP. ASK 2.0 reuses this driver unmodified except for one ~200-line patch exposing in-kernel descriptor-share API.
- Performance baseline: kernel IPsec without ASK on LS1046A hits ~750 Mbps AES-CBC-SHA256 with `caam_jr`, ~2 Gbps with `caam_qi`. With ASK 2.0 + 210 routing ESP frames directly to CAAM QI without CPU mediation, the realistic target is **3-5 Gbps AES-GCM-128 at 1024 B**.

### 2.4 Mono Gateway DK port map

| netdev | FMan MAC | DT unit-addr | physical port | `cdx_cfg.xml` portid |
|--------|----------|--------------|----------------|----------------------|
| `eth0` | mEMAC5   | `e8000`      | left RJ45 1G   | 4  |
| `eth1` | mEMAC6   | `ea000`      | center RJ45 1G | 5  |
| `eth2` | mEMAC2   | `e2000`      | right RJ45 1G  | 1  |
| `eth3` | mEMAC9   | `f0000`      | left SFP+ 10G  | 6  |
| `eth4` | mEMAC10  | `f2000`      | right SFP+ 10G | 7  |
| (offline, `fman0-oh@2`) | OP1 | — | IPsec offload bound | 8 |
| (offline, `fman0-oh@3`) | OP2 | — | WiFi/AP offload bound | 9 |

### 2.5 Offline Ports

An OP is a hardware FMan port with no MAC. It dequeues from one QMan frame queue and re-enqueues to another after a re-parse pass. The architecturally significant property is **the re-parse on enqueue**, which is what makes IPsec decryption fast-path-able:

```
ESP ingress on eth3 (10G SFP+)
    → FMan parses outer IP, classifies via 210 flow table (matched on dst_ip + SPI)
    → flow action: enqueue to CAAM QI Rx FQ
CAAM SEC 5.4
    → dequeues from QMan, runs decryption descriptor (loaded by ASK at SA add time)
    → enqueues decrypted frame to OP1 ingress FQ
FMan OP1
    → re-parses now-decrypted frame (sees inner IP+TCP/UDP 5-tuple)
    → 210 flow table lookup on inner 5-tuple → modify-and-forward action
    → enqueues frame to egress TX FQ on eth0
A72 cores: untouched
```

This pattern has no equivalent in mainline tc-flower or `flowtable` because mainline assumes single-pass classification per frame. The 210 ucode + OP combination is what makes ASK genuinely superior to anything achievable with only mainline facilities on this silicon.

---

## 3. Architecture overview

### 3.1 The three planes

ASK 2.0 establishes a clean three-plane architecture on a 4-core Mono Gateway DK:

| Plane | Hardware location | Owns | Cores |
|---|---|---|---|
| **Dataplane fast** | FMan with 210 ucode + CAAM QI | Established 5-tuple flows, NAT, IPsec ESP, L2 bridge | none (silicon) |
| **Dataplane slow** | Linux kernel netfilter, conntrack | First-packet, control-plane traffic, slow-path lift | 0-1 |
| **Dataplane programmable** | VPP (optional) | CGNAT, SR-MPLS, complex ACLs, plugin features | 2-3 |

A single chain of responsibility runs through all three:

```
        ┌─────────────────────────────────────────────┐
        │  strongSwan / FRR / nft / iproute2 (control)│  cores 0-1
        └────────────┬────────────────────────────────┘
                     │ XFRM / netlink / netfilter
        ┌────────────▼────────────────────────────────┐
        │  Linux kernel networking stack               │  cores 0-1
        │  (conntrack, nf_flowtable software fallback) │
        └────────────┬────────────────────────────────┘
                     │ conntrack notifier, xfrm notifier
        ┌────────────▼────────────────────────────────┐
        │  askd (userspace decision engine)            │  core 0
        └────────────┬────────────────────────────────┘
                     │ generic netlink (family "ask")
        ┌────────────▼────────────────────────────────┐
        │  ask.ko + ask_bridge.ko (OOT kernel modules) │  kernel context
        │   ↓ in-kernel API ↓                           │
        │  drivers/crypto/caam/caam_qi.ko (mainline)   │
        └────────────┬────────────────────────────────┘
                     │ FMan host commands + CAAM descriptors
        ┌────────────▼────────────────────────────────┐
        │  FMan with 210 ucode + CAAM SEC 5.4 (silicon)│
        └─────────────────────────────────────────────┘

        Parallel and optional, on cores 2-3:
        ┌─────────────────────────────────────────────┐
        │  VPP (with Linux-CP and memif handoff)       │
        └─────────────────────────────────────────────┘
```

### 3.2 The bus that ties it together: generic netlink family `ask`

Everything that's not silicon talks through one generic netlink family. This replaces the legacy `NETLINK_KEY=32` channel from ASK 1.x. The protocol carries both unicast commands (userspace→kernel→hardware) and multicast events (hardware→kernel→userspace).

Schema in Section 7.

### 3.3 The five user-visible artefacts

1. **`ask.ko`** — kernel module, ~1500 LOC C, owns `/dev/ask_ctrl` chardev (kept under that path for vendor-tool ABI compatibility with `/dev/cdx_ctrl`; symlinked at boot) and registers genl family `ask`. Programs FMan 210 flow tables via FMC API. Implements CAAM xfrm offload glue.
2. **`ask_bridge.ko`** — kernel module, ~400 LOC C, watches bridge FDB events via `RTM_NEWNEIGH`, forwards L2 flow promotions to `ask.ko` via in-kernel API.
3. **`askd`** — userspace daemon, ~6000 LOC C, consumes conntrack/xfrm/bridge events from kernel, applies policy (ALG exclusion list, flow eligibility), pushes flow inserts to `ask.ko` via genl. Replaces `cmm` + part of `dpa_app`.
4. **`ask-load`** — userspace one-shot, ~1200 LOC C, runs at module insmod via UMH. Reads `cdx_cfg.xml` + `cdx_pcd.xml`, compiles via patched upstream `fmc` library, pushes compiled FMC model to `ask.ko` via genl. Replaces `dpa_app`.
5. **`libask_fci`** — userspace library, ~800 LOC C, header-compatible with legacy `libfci.h` so vendor tools that link `libfci.so.1` continue to work. Wraps the genl protocol.

Plus invisible artefacts:

6. **`200-ask2-hooks.patch`** — in-tree hooks patch against linux-6.18.x, ~1500 lines (down from 5797). Adds genl family registration scaffolding, xt_QOSMARK and xt_QOSCONNMARK matches (verbatim from legacy), and minor xfrm hooks.
7. **`201-caam-qi-share.patch`** — in-tree patch against `drivers/crypto/caam/qi.c`, ~200 lines. Exposes `caam_qi_register_external_consumer()` so `ask.ko` can share CAAM descriptors with FMan flow-table actions. Candidate for upstream.

---

## 4. Component spec — `ask.ko`

### 4.1 Responsibilities `[AGENT-IMPLEMENTABLE]`

1. At `module_init`:
   - Verify FMan microcode is the 210 family by probing the host-command opcode space. Bail with `pr_err_once` and `return -ENODEV` if it isn't.
   - Open FMan PCD handle via SDK FMan driver API.
   - Spawn `/usr/sbin/ask-load` via `call_usermodehelper(UMH_WAIT_PROC)`.
   - Register genl family `ask` with command and event operations defined in Section 7.
   - Register conntrack event notifier (`nf_conntrack_register_notifier`).
   - Register xfrm state event notifier (`xfrm_register_km`).
   - Register bridge FDB notifier (`register_netevent_notifier`).
   - Create `/dev/ask_ctrl` chardev with legacy ioctl set for ABI compatibility (see Section 7.4).
   - Create `/sys/kernel/debug/ask/` with `muram`, `flows`, `stats`, `events` files.
2. At runtime:
   - Translate conntrack events → flow-table inserts via FMC API on the path described in Section 6.4.
   - Translate xfrm SA events → CAAM descriptor registration + flow-table ESP-SPI inserts via the path in Section 8.
   - Multicast hardware-initiated events (flow eviction, table-full, ucode errors) to userspace via genl multicast group `flow_events`.
3. At `module_exit`:
   - Flush all flow tables via FMC API.
   - Free CAAM descriptors.
   - Unregister everything in reverse init order.

### 4.2 File layout `[AGENT-IMPLEMENTABLE]`

```
kernel/flavors/ask/oot-modules/ask/
├── Kbuild
├── ask_main.c          # module init/exit, genl registration, UMH ask-load spawn
├── ask_genl.c          # generic netlink family operations
├── ask_dev.c           # /dev/ask_ctrl chardev, legacy ioctl compat
├── ask_dpaa.c          # FMan PCD handle, FMC model ingest, MURAM accounting
├── ask_flowtable.c     # 210 flow table add/del/query operations
├── ask_xfrm.c          # xfrm notifier → CAAM descriptor → ESP flow insert
├── ask_caam.c          # in-kernel API calls into drivers/crypto/caam/qi.c
├── ask_conntrack.c     # nf_conntrack notifier → flow promotion logic
├── ask_bridge_api.c    # in-kernel API consumed by ask_bridge.ko
├── ask_op.c            # offline port wiring for IPsec re-inject and bridge flood
├── ask_qos.c           # exception-rate-limit policer, QOSMARK integration
├── ask_stats.c         # per-flow byte/frame counters, per-CPU aggregation
├── ask_debug.c         # debugfs files
├── ask_internal.h      # private types, NOT exposed to userspace
└── ask_uapi.h          # internal mirror of include/uapi/linux/ask/

include/uapi/linux/ask/
├── ask.h               # genl protocol definitions
└── ask_ctrl.h          # /dev/ask_ctrl legacy ioctl numbers + structs
```

### 4.3 Module dependency chain (load order) `[AGENT-IMPLEMENTABLE]`

Boot-time order in `/etc/modules-load.d/ask-modules.conf`:

```
nf_conntrack
nf_conntrack_netlink
xfrm_user
ask                     # was: cdx
ask_bridge              # was: auto_bridge
```

systemd oneshot `ask-modules-load.service` modprobes in order. `askd.service` has `Requires=ask-modules-load.service` and `ConditionPathExists=/dev/ask_ctrl`.

### 4.4 Kconfig `[AGENT-IMPLEMENTABLE]`

```
config ASK
    tristate "NXP LS1046A ASK 2.0 Fast-Path Engine (clean-room)"
    depends on FSL_SDK_DPA || FSL_DPAA
    depends on NETFILTER && NF_CONNTRACK && BRIDGE
    depends on XFRM
    depends on CRYPTO_DEV_FSL_CAAM_QI
    select GENERIC_NETLINK_CAPABILITY
    help
      Out-of-tree-buildable replacement for the proprietary NXP cdx.ko fast-
      path module. Requires NXP 210-family FMan microcode loaded at boot;
      without it the module bails out and /dev/ask_ctrl is never created.

config ASK_BRIDGE
    tristate "ASK 2.0 bridge-FDB watcher"
    depends on ASK && BRIDGE
```

`CONFIG_ASK` does not collide with anything in mainline 6.18 — verified via `grep -r '^config ASK' linux-6.18/`. Default-flavor builds set `CONFIG_ASK=n`; ASK-flavor builds set `CONFIG_ASK=m` (out-of-tree from `kernel/flavors/ask/oot-modules/ask/`).

### 4.5 The 210 host-command protocol `[HUMAN-REQUIRED]`

This is the single hardest part of the implementation and the part no agent can do unsupervised. The 210 ucode exposes a host-command opcode space that the kernel module sends commands through via the FMan I/O block. The opcodes for "install flow", "remove flow", "query flow stats", "register SA descriptor" are not publicly documented.

The reference for what bytes go on the wire is the legacy `we-are-mono/ASK/cdx/` source. Section 12 describes the reverse-engineering procedure. Plan **2 weeks of black-box probing per major feature** (Section 12.3 enumerates them).

The agent's role here is supporting: build the test harness, write the dmesg-correlating probe script, automate the test matrix. The judgement call about what each opcode means and how to validate it must be made by a human looking at real hardware behaviour.

---

## 5. Component spec — `ask_bridge.ko`

### 5.1 Responsibilities `[AGENT-IMPLEMENTABLE]`

1. At `module_init`: register `register_netevent_notifier` for `NETEVENT_NEIGH_UPDATE` and bridge FDB notifier.
2. On bridge FDB add: extract `(bridge_ifindex, MAC, port_ifindex)`, call into `ask.ko` via in-kernel API `ask_bridge_promote_l2_flow()` to install L2 flow-table entry under 210.
3. On bridge FDB del: call `ask_bridge_evict_l2_flow()`.
4. At `module_exit`: unregister, flush bridge entries via `ask.ko` API.

### 5.2 Why a separate module

Could be in `ask.ko`. Separating it makes the bridge path independently loadable and unloadable for diagnostics, and matches the legacy split that operators are familiar with from ASK 1.x.

### 5.3 File layout `[AGENT-IMPLEMENTABLE]`

```
kernel/flavors/ask/oot-modules/ask_bridge/
├── Kbuild
├── ask_bridge_main.c   # init/exit, notifier registration
├── ask_bridge_fdb.c    # FDB event handling
└── ask_bridge.h
```

---

## 6. Component spec — userspace daemon `askd`

### 6.1 Replaces `cmm` (legacy) plus the runtime parts of `dpa_app`

`cmm` was ~25k LOC across 60+ files because it carried ICC asymmetric-SoC support (irrelevant on LS1046A) and FreeBSD/Linux build abstraction (we only need Linux). `askd` targets **~6000 LOC C** with libmnl for netlink, glib for event loop, structured logging via syslog.

### 6.2 Inputs `[AGENT-IMPLEMENTABLE]`

- `nf_conntrack_event` via netlink — libnetfilter_conntrack with the existing async/heap-buffer extension patch.
- Bridge FDB events via `RTM_NEWNEIGH` rtnetlink.
- xfrm SA events via xfrm_user rtnetlink.
- `/proc/net/route`, `/proc/net/route6`, `/proc/net/ipv6_neigh` for next-hop resolution.
- `/etc/config/fastforward` for ALG-exclusion list (FTP, SIP, PPTP by default).
- VPP control socket (`/var/run/vpp/cli.sock`) for promotion handoff decisions — optional, only consulted if VPP is running.
- CLI on `/var/run/askd.sock` via libcli.

### 6.3 Outputs `[AGENT-IMPLEMENTABLE]`

- genl messages to `ask.ko` for flow ADD/DEL/QUERY.
- VPP memif setup for flows promoted to VPP CGNAT plane (optional).
- syslog records and Prometheus exporter on TCP `/metrics`.

### 6.4 Decision logic `[AGENT-IMPLEMENTABLE]`

For each new `conntrack EST` event:

```
1. Parse: extract proto, src/dst/sport/dport, mark, zone
2. Filter: if proto+port in fastforward exclusion list → skip
3. Resolve: look up next-hop for dst via /proc/net/route + neigh table
   - if next-hop is on a non-offloadable interface (loopback, tun, software bridge, wireguard) → skip
   - if next-hop neigh entry is INCOMPLETE → re-queue, retry on next tick
4. Optionally promote to VPP: if flow matches a VPP-promotion ACL → set up memif handoff, return
5. Build flow descriptor:
   - 5-tuple from conntrack
   - egress port_ifindex + egress MAC from neigh resolution
   - action template: rewrite_src_mac, rewrite_dst_mac, decrement_ttl, [NAT rewrite if conntrack has it]
6. Send ASK_CMD_FLOW_ADD via genl
7. On successful ACK: mark conntrack entry with custom mark bit IPS_HW_OFFLOAD
```

For each `conntrack DESTROY` event: send `ASK_CMD_FLOW_DEL` for the matching flow descriptor.

For each periodic tick (1 Hz default, configurable): query `ASK_CMD_FLOW_QUERY` for all installed flows, update conntrack last-used time so software-side conntrack doesn't time out hardware-active flows. This is the "bytes-back" pattern.

### 6.5 xfrm event handling `[AGENT-IMPLEMENTABLE]`

For each new `xfrm SA add`:
- Send `ASK_CMD_SA_ADD` with SA params (SPI, dst, src, AEAD algorithm, key material, replay window).
- `ask.ko` registers a CAAM descriptor and inserts an ESP-SPI flow-table entry.

For each `xfrm SA del`: send `ASK_CMD_SA_DEL`.

The 210 ucode handles the data-path entirely; askd does not touch ESP frames at runtime.

### 6.6 File layout `[AGENT-IMPLEMENTABLE]`

```
ask/userspace/askd/
├── Makefile
├── src/
│   ├── main.c              # event loop, signal handling, daemonisation
│   ├── conntrack.c         # libnetfilter_conntrack consumer
│   ├── xfrm_events.c       # xfrm_user consumer
│   ├── bridge_events.c     # rtnetlink RTM_NEWNEIGH consumer
│   ├── neigh.c             # /proc/net/route + neigh resolution
│   ├── policy.c            # fastforward ALG exclusion list parser
│   ├── flow_descriptor.c   # build flow add/del payload
│   ├── ask_genl.c          # libmnl wrapper for ask genl family
│   ├── vpp_handoff.c       # optional memif setup for VPP promotion
│   ├── cli.c               # libcli interactive CLI
│   ├── prometheus.c        # /metrics HTTP exporter
│   ├── log.c               # structured syslog
│   └── config.c            # parse /etc/config/fastforward
├── include/askd/
│   └── askd.h
└── tests/
    ├── test_neigh.c
    ├── test_flow_descriptor.c
    └── test_policy.c
```

### 6.7 Software flowtable fallback `[AGENT-IMPLEMENTABLE]`

When `ASK_CMD_FLOW_ADD` returns `-ENOSPC` (MURAM exhausted or flow table full):
- Set up an `nf_flow_table` software-mode entry for the flow instead.
- Mark the conntrack entry with `IPS_OFFLOAD` (software bypass) rather than `IPS_HW_OFFLOAD`.
- Subsequent packets traverse the kernel software fastpath via `nf_flow_offload_ip_hook()`. Not as fast as hardware, but bypasses conntrack lookup per packet.

This is the safety net. Without it, MURAM exhaustion causes the box to drop new connections.

### 6.8 systemd integration `[AGENT-IMPLEMENTABLE]`

```ini
# /lib/systemd/system/askd.service
[Unit]
Description=ASK 2.0 connection manager
After=network.target ask-modules-load.service systemd-sysctl.service
Requires=ask-modules-load.service
ConditionPathExists=/dev/ask_ctrl

[Service]
Type=notify
ExecStart=/usr/bin/askd --config /etc/config/fastforward --no-fork
Restart=on-failure
RestartSec=5
LimitNOFILE=131072

[Install]
WantedBy=multi-user.target
```

---

## 7. The generic netlink protocol `[AGENT-IMPLEMENTABLE]`

### 7.1 Family definition

```c
/* include/uapi/linux/ask/ask.h */
#define ASK_GENL_NAME    "ask"
#define ASK_GENL_VERSION 1

enum ask_cmd {
    ASK_CMD_UNSPEC,

    /* Userspace → kernel */
    ASK_CMD_TABLE_LIST,        /* dump all configured flow tables */
    ASK_CMD_TABLE_FLUSH,       /* remove all entries from a table */
    ASK_CMD_TABLE_STATS,       /* per-table allocation, MURAM use */

    ASK_CMD_FLOW_ADD,          /* install a flow entry */
    ASK_CMD_FLOW_DEL,
    ASK_CMD_FLOW_QUERY,
    ASK_CMD_FLOW_DUMP,         /* enumerate all flows in a table */

    ASK_CMD_SA_ADD,            /* install an IPsec SA via 210 + CAAM */
    ASK_CMD_SA_DEL,
    ASK_CMD_SA_QUERY,
    ASK_CMD_SA_DUMP,

    ASK_CMD_LOAD_PCD,          /* called by ask-load: push compiled FMC model */
    ASK_CMD_GET_MURAM_FREE,    /* operator visibility */

    /* Kernel → userspace (multicast) */
    ASK_EVENT_FLOW_INSTALLED,
    ASK_EVENT_FLOW_EVICTED,    /* 210 evicted under pressure */
    ASK_EVENT_TABLE_FULL,
    ASK_EVENT_UCODE_ERROR,
    ASK_EVENT_SA_INSTALLED,
    ASK_EVENT_SA_EXPIRED,      /* xfrm hard-limit reached */

    __ASK_CMD_MAX,
};
#define ASK_CMD_MAX (__ASK_CMD_MAX - 1)

enum ask_mcast_group {
    ASK_MCAST_FLOW_EVENTS,
    ASK_MCAST_SA_EVENTS,
    ASK_MCAST_HW_EVENTS,       /* table-full, ucode errors */
};
```

### 7.2 Attributes

```c
enum ask_attr {
    ASK_ATTR_UNSPEC,

    /* Identifiers */
    ASK_ATTR_TABLE_ID,         /* u8: 0..15 */
    ASK_ATTR_FLOW_KEY,         /* nested: see ask_flow_key_attr */
    ASK_ATTR_FLOW_ACTION,      /* nested: see ask_flow_action_attr */
    ASK_ATTR_SA_KEY,           /* nested: SPI + dst */
    ASK_ATTR_SA_PARAMS,        /* nested: AEAD alg + key material */

    /* Stats */
    ASK_ATTR_BYTES,            /* u64 */
    ASK_ATTR_PACKETS,          /* u64 */
    ASK_ATTR_LAST_USED,        /* u64 ns */

    /* Errors */
    ASK_ATTR_ERROR_CODE,       /* s32 */
    ASK_ATTR_ERROR_STRING,     /* nul-terminated string */

    __ASK_ATTR_MAX,
};
#define ASK_ATTR_MAX (__ASK_ATTR_MAX - 1)

enum ask_flow_key_attr {
    ASK_KEY_UNSPEC,
    ASK_KEY_L3_PROTO,          /* u16: ETH_P_IP / ETH_P_IPV6 */
    ASK_KEY_L4_PROTO,          /* u8:  IPPROTO_TCP / UDP / ESP */
    ASK_KEY_SRC_IP,            /* binary, length depends on l3_proto */
    ASK_KEY_DST_IP,
    ASK_KEY_SPORT,             /* u16 NBO */
    ASK_KEY_DPORT,
    ASK_KEY_SPI,               /* u32, for ESP flows */
    ASK_KEY_VLAN_ID,           /* u16 */
    ASK_KEY_INPUT_IFINDEX,     /* u32 */
    __ASK_KEY_MAX,
};

enum ask_flow_action_attr {
    ASK_ACTION_UNSPEC,
    ASK_ACTION_OUTPUT_IFINDEX, /* u32: egress port */
    ASK_ACTION_REWRITE_SRC_MAC,/* binary 6 bytes */
    ASK_ACTION_REWRITE_DST_MAC,
    ASK_ACTION_REWRITE_SRC_IP, /* SNAT */
    ASK_ACTION_REWRITE_DST_IP, /* DNAT */
    ASK_ACTION_REWRITE_SPORT,  /* PAT */
    ASK_ACTION_REWRITE_DPORT,
    ASK_ACTION_VLAN_PUSH,      /* u16 vid */
    ASK_ACTION_VLAN_POP,       /* flag */
    ASK_ACTION_TTL_DEC,        /* flag, default true */
    ASK_ACTION_TO_CAAM_FQ,     /* u32 FQID, for ESP→CAAM redirect */
    ASK_ACTION_TO_OFFLINE_PORT,/* u8 OP number, for post-CAAM re-inject */
    __ASK_ACTION_MAX,
};
```

### 7.3 Capability gating

Every command op requires `GENL_ADMIN_PERM` (CAP_NET_ADMIN). Verified in `ask_genl.c::ask_genl_ops[]` flags.

### 7.4 Legacy `/dev/ask_ctrl` ioctl compatibility

For binary ABI compatibility with vendor tools that hardcode the legacy `/dev/cdx_ctrl` ioctl numbers:

- Symlink `/dev/cdx_ctrl` → `/dev/ask_ctrl` at module init.
- Implement the original `CDX_CTRL_DPA_SET_PARAMS` and per-table flow add/del ioctls as a compatibility shim that internally translates to genl. ~300 lines of code in `ask_dev.c`.
- Mark these as `deprecated` in kernel log on first use.
- Existing `libfci.so.1` continues to work; new code uses `libask_fci.so.1` (which wraps genl directly).

---

## 8. Component spec — IPsec offload via CAAM reuse

### 8.1 Architecture `[AGENT-IMPLEMENTABLE]`

The full picture of what happens at SA add time:

```
1. strongSwan or VyOS IKE daemon negotiates SA, calls `ip xfrm state add ... offload packet dev eth3`
2. Kernel xfrm_state.c calls xdo_dev_state_add() — but no CAAM driver implements this today
3. Workaround for v1.0: askd watches xfrm_user RTM_NEWSA, intercepts the add event
4. askd sends ASK_CMD_SA_ADD via genl to ask.ko
5. ask.ko::ask_xfrm.c:
   a. Calls caam_qi_aead_setkey() in drivers/crypto/caam/caamalg_qi.c — gets back a CAAM descriptor + FQID
   b. Configures CAAM's QI for this descriptor via in-kernel API exposed by 201-caam-qi-share.patch
   c. Calls FMC API to insert a flow-table entry: match=(dst_ip, ESP, spi) action=(to_caam_fq=FQID)
   d. Configures OP1 to re-inject CAAM output frames into the regular flow-table lookup pipeline
6. From this point: ESP frames arrive at eth3, FMan routes them to CAAM FQID, CAAM decrypts, OP1 re-classifies, FMan forwards. Zero CPU touch.
```

At packet time: nothing happens in software. The 210 ucode + CAAM QI handle everything.

At SA delete time: reverse the install in the same order. Free the CAAM descriptor via `caam_qi_aead_release()`.

### 8.2 What we DON'T write

- ❌ A CAAM packet-mode xfrmdev_ops driver. Mainline doesn't have one and we don't need one. xfrm packet-mode requires `xdo_dev_state_add`/`xdo_dev_policy_add` and CAAM doesn't expose those. Instead, we intercept SA events at the xfrm_user notifier layer (in `askd`) and program FMan + CAAM directly. The end-user CLI looks identical (`ip xfrm state add ... offload ...`); only the implementation path differs.
- ❌ Crypto descriptors. Mainline `caamalg_qi.c` already constructs them correctly for every supported AEAD/cipher.
- ❌ ESP encapsulation/decapsulation logic. The 210 ucode handles ESP framing; CAAM handles the AEAD operation; mainline xfrm provides the SA state for replay window etc. but doesn't touch the data path once the SA is installed.

### 8.3 The one in-tree patch `[AGENT-IMPLEMENTABLE WITH HUMAN REVIEW]`

`201-caam-qi-share.patch`, ~200 lines against `drivers/crypto/caam/qi.c`:

```c
/*
 * Expose CAAM descriptor + FQID to external in-kernel consumers (FMan ASK fast-path).
 * The mainline driver assumes only kernel crypto API callers use the QI path; the patch
 * adds a registration interface for external consumers that want to enqueue from
 * a different engine.
 */
int caam_qi_register_external_consumer(struct caam_drv_ctx *ctx,
                                        u32 *out_rx_fqid, u32 *out_tx_fqid);
void caam_qi_unregister_external_consumer(struct caam_drv_ctx *ctx);
EXPORT_SYMBOL_GPL(caam_qi_register_external_consumer);
EXPORT_SYMBOL_GPL(caam_qi_unregister_external_consumer);
```

Propose upstream after v1.0 ships. Precedent exists: mlx5 does the equivalent for ConnectX-7 sharing crypto contexts between RDMA and Ethernet paths.

### 8.4 Supported AEAD algorithms in v1.0 `[AGENT-IMPLEMENTABLE]`

In priority order:
1. AES-GCM-128 (rfc4106)
2. AES-GCM-256
3. AES-CBC + HMAC-SHA256 (legacy, still common in enterprise VPN)
4. ChaCha20-Poly1305 — only if mainline `caamalg_qi.c` already supports it; check `/proc/crypto` first

Defer to v1.1:
- AES-CBC + HMAC-SHA1 (deprecated but operators still ask)
- 3DES (obsolete; only if a customer specifically needs it)
- ESN (extended sequence numbers)

### 8.5 Tunnel vs transport mode `[AGENT-IMPLEMENTABLE]`

- Tunnel mode is the common case (site-to-site VPN). The outer IP is the ESP envelope, the 210 flow-table key is `(outer_dst, ESP, SPI)`.
- Transport mode (rare on a router but supported). Same flow-table key structure; the difference is the inner-frame action template doesn't strip an outer IP header.

Both modes work the same way in v1.0 from the ASK side. Verify on hardware before claiming both pass acceptance.

---

## 9. Component spec — `ask-load` and policy XML schemas

### 9.1 `ask-load` responsibilities `[AGENT-IMPLEMENTABLE]`

- Run once at `ask.ko` insmod via UMH.
- Read `/etc/cdx_cfg.xml`, `/etc/cdx_pcd.xml`, `/etc/cdx_sp.xml`, `/etc/fmc/config/hxs_pdl_v3.xml`.
- Call `fmc_compile()` from patched upstream `fmc` library.
- Call `FM_PCD_Open()` / `FM_PCD_Disable()` per FMan to put PCD into the configurable state.
- Walk compiled `fmc_model_t`, build `ASK_CMD_LOAD_PCD` genl payload.
- Send to `ask.ko`. On success, exit 0; on failure, exit non-zero with structured stderr.

### 9.2 XML schemas (operator-visible, kept stable for ABI compat) `[REFERENCE]`

`cdx_cfg.xml` — port-to-policy map:
```xml
<cfgdata>
  <config>
    <engine name="fm0">
      <port type="1G|10G|OFFLINE" number="N" policy="<policy_name>" portid="N"/>
    </engine>
  </config>
</cfgdata>
```

`cdx_pcd.xml` — FMan PCD policy with three top-level sections:
- `<classification>` — flow-table definition (key fields, mask, table type, action template)
- `<distribution>` — KeyGen scheme (protocol, hash key, output FQID, target classification)
- `<policy>` — ordered list of distributions applied to a port

Both schemas are inherited from ASK 1.x unchanged. Operators with existing config files boot ASK 2.0 with no migration required.

### 9.3 Required FMC library patches `[AGENT-IMPLEMENTABLE]`

Re-derived against current upstream `nxp-qoriq/fmc` and `nxp-qoriq/fmlib`:

- `data/ask-userspace/fmlib/patches/01-ask2-extensions.patch` — adds `t_FmPcdKgSchemeParams::shared`, `t_FmPcdHashTableParams::{table_type, timeout_val, timeout_fqid, max_frags, min_frag_size, max_sessions}`, IP-reassembly enums. Bit-identical struct layout vs. the legacy patch.
- `data/ask-userspace/fmc/patches/01-ask2-extensions.patch` — port-ID output, shared scheme/CC node replication, PPPoE field fix, libxml2 2.13+ compat.

Build pipeline: `bin/ci-build-fmlib.sh` and `bin/ci-build-fmc.sh` run in CI, produce signed `.deb` packages, install during ISO assembly.

Static-assert chain in `ask-load`:
```c
_Static_assert(sizeof(t_FmPcdKgSchemeParams) == EXPECTED_KG_SCHEME_SIZE,
               "fmlib ABI mismatch — rebuild fmlib with ask2-extensions patch");
_Static_assert(sizeof(t_FmPcdHashTableParams) == EXPECTED_HASH_TABLE_SIZE,
               "fmlib ABI mismatch — rebuild fmlib with ask2-extensions patch");
```

If the wrong libfm is linked, the build fails — never the runtime. This pins the v0.5 "Chain-2 SIGSEGV" failure mode.

### 9.4 `ask-load` file layout `[AGENT-IMPLEMENTABLE]`

```
ask/userspace/ask-load/
├── Makefile
├── src/
│   ├── main.c              # argv parsing, daemonised one-shot
│   ├── xml_parse.c         # cdx_cfg.xml, cdx_pcd.xml ingest via libxml2
│   ├── fmc_invoke.c        # fmc_compile() + FM_PCD_* calls
│   ├── genl_send.c         # ASK_CMD_LOAD_PCD payload construction
│   ├── exptrate.c          # default exception-rate-limit policer values
│   └── static_asserts.c
└── tests/
    └── test_xml_parse.c
```

---

## 10. Kernel hooks patch — `200-ask2-hooks.patch`

### 10.1 Scope `[AGENT-IMPLEMENTABLE WITH HUMAN REVIEW]`

A ~1500-line in-tree patch against linux-6.18.x. Target file count: ~12 files (down from 64 in legacy).

What stays from legacy:
- `xt_QOSMARK` and `xt_QOSCONNMARK` match modules — verbatim copy, ~200 lines each.
- `net/key/af_key.c` — minor scaffolding for `pfkey_register()` call from `ask.ko`. ~20 lines.

What gets added:
- `net/netfilter/ask_genl_glue.c` — generic-netlink family scaffold that `ask.ko` registers against. ~150 lines.
- `include/uapi/linux/ask/ask.h`, `ask_ctrl.h` — public UAPI headers. ~300 lines.
- Minor xfrm hook for SA-add event interception (alternative would be to add an xfrm notifier in `ask.ko` only, no patch needed; choose at implementation time based on whether mainline notifier infrastructure is sufficient).

What's DELETED vs legacy:
- All 600 lines of `comcerto_fp_netfilter.c` — replaced by mainline `nf_flow_table` + genl.
- All bridge `net/bridge/br_*.c` edits — `nf_flow_table` already has bridge hooks.
- All `net/xfrm/ipsec_flow.{c,h}` — replaced by askd's xfrm_user consumer.
- All edits to `drivers/net/ppp/`, `drivers/net/usb/usbnet.c`, `net/ip6_tunnel.c` — not needed since we don't tap subsystem code paths anymore.
- All edits to `include/linux/skbuff.h`, `if_bridge.h`, `netdevice.h`, `poll.h` — those were extra fields for the deleted `comcerto_fp_*` code.

### 10.2 Patch maintenance discipline `[AGENT-IMPLEMENTABLE]`

- Apply with `git apply --3way`.
- CI gate: `kernel/common/scripts/patch-health.sh --flavor ask` must report `Pass: 2 Fail: 0` (this patch + the CAAM share patch).
- Every mainline kernel bump: re-run apply, regenerate diff against the new base, commit with `kernel: rebase 200-ask2-hooks against 6.18.x`.
- Goal: this patch dies entirely if mainline ever accepts `ndo_setup_tc` for DPAA1 + a genl-style interface for non-tc-flower offloads. That's a v2.0 project.

---

## 11. VPP coexistence `[AGENT-IMPLEMENTABLE WITH HUMAN REVIEW]`

### 11.1 Architecture

VyOS 1.5 LTS already integrates VPP with per-interface placement, Linux-CP for control-plane handoff, and shared-physical-NIC traffic via memif. ASK 2.0 piggybacks on this integration:

- `set interfaces ethernet ethN offload ask` — port is ASK-managed, kernel netdev sees control-plane traffic only.
- `set interfaces ethernet ethN offload vpp` — port is VPP-managed via DPDK `dpaa` PMD.
- Default (neither set) — port is plain kernel netdev with mainline `dpaa_eth`.

For **flow promotion from ASK to VPP** (when a flow needs complex processing ASK can't do, like CGNAT with port pool management):

```
askd:
  on conntrack EST event:
    if (flow matches VPP-promotion ACL):
      send ASK_CMD_FLOW_ADD with ASK_ACTION_OUTPUT_IFINDEX = memif_to_vpp
      VPP receives the frame via memif on its dedicated cores
      VPP processes (CGNAT, SR, complex ACL)
      VPP sends back to a different FMan port or directly via memif
    else:
      send ASK_CMD_FLOW_ADD with ASK_ACTION_OUTPUT_IFINDEX = ethN (direct egress)
```

memif latency on A72 is in the low-µs range. The promotion overhead is constant regardless of flow size, so high-bandwidth flows pay it only once.

### 11.2 What VPP CANNOT do on this hardware

The DPDK `dpaa` PMD does not expose FMan flow-table modify-and-forward as an `rte_flow` action. So VPP cannot install hardware fast-path flows itself. VPP runs its forwarding decisions in software, on dedicated A72 cores, at ~8-12 Gbps for 64 B IP forwarding (extrapolated from FD.io CSIT N1/Neoverse numbers; A72 is older and slower).

This is why **VPP-only (no ASK) loses ~50% of the 64 B headroom**. The ASK path stays the right choice for plain forwarding; VPP is a specialised second-tier engine for things ASK can't do.

### 11.3 Three deployment patterns supported in v1.0

| Pattern | When to use | How configured |
|---|---|---|
| ASK-only | Pure routing/NAT, no CGNAT, no SR-MPLS, low VPN count | `set system offload ask enable` |
| VPP-only | Specialised CGNAT/SR appliance, no need for kernel features | `set vpp enable; set vpp interface eth*` |
| ASK + VPP hybrid | Mixed workload — most flows direct, some need VPP processing | both enabled; `set system offload ask promote vpp` ACL |

### 11.4 VPP plugin (out of scope for v1.0)

A dedicated VPP plugin that talks to `ask.ko` over genl is a future possibility. It would let VPP push hardware-offload flows directly without going through askd. But it requires VPP-side memif management and a clean protocol boundary that doesn't exist yet. Defer to v1.1.

---

## 12. The 210 host-command reverse-engineering process `[HUMAN-REQUIRED, AGENT-ASSISTED]`

### 12.1 What this section covers

This is the only section where engineering judgement on real hardware drives every decision. An agent can write test harnesses, parse dmesg, automate the test matrix, and propose hypotheses. A human must decide what each opcode does and whether the implementation is correct.

### 12.2 The reference

The legacy `we-are-mono/ASK/cdx/` source (branch `mt-6.12.y`) is the functional reference. The clean-room rewrite procedure:

1. Pick one feature (e.g., "install IPv4 UDP 5-tuple flow").
2. Read the legacy code's host-command sequence for that feature.
3. Document the byte-level wire format: opcode, key encoding, action encoding, response format.
4. Write a small probe: open `/dev/cdx_ctrl` on a vanilla Mono Gateway running legacy ASK, install a flow, capture the host-command exchange via dynamic tracing.
5. Verify documented behaviour matches captured behaviour.
6. Implement the same behaviour in `ask.ko::ask_flowtable.c` using FMC API calls.
7. Test on Mono Gateway DK: install flow, verify packets traverse the fast path, verify CPU stays idle.
8. Update v0.6.x of this spec with documented opcode behaviour.

### 12.3 Feature matrix (rough order)

| # | Feature | Estimated probe weeks |
|---|---|---|
| 1 | IPv4 UDP 5-tuple flow add/del/query | 1-2 |
| 2 | IPv4 TCP 5-tuple flow add/del/query | 0.5 (likely same opcode space) |
| 3 | IPv6 UDP/TCP 5-tuple | 1 |
| 4 | IPv4 NAT rewrite (SNAT + DNAT + PAT) | 2 |
| 5 | L2 bridge flow | 1 |
| 6 | ESP IPv4 transport mode | 2 |
| 7 | ESP IPv4 tunnel mode | 1 |
| 8 | ESP IPv6 | 1 |
| 9 | Multicast 3-tuple | 1 |
| 10 | PPPoE relay | 1 |
| 11 | Flow eviction events (hardware → host) | 2 |
| 12 | Stats query (per-flow byte/packet counters) | 0.5 |

Total raw probe time: ~14 weeks. With agent assistance for harness building and analysis, this compresses to **8-10 weeks of calendar time** if Miha + one collaborator work in parallel.

### 12.4 Open question: NXP cooperation

Even with a paid 210 license, the host-command protocol may not be in any document Mono received from NXP. Worth asking Tomaž directly:
- Does Mono have an NXP-supplied "ASK Programmer's Reference Manual" or equivalent?
- Are there NXP internal headers (`fpp_*.h`, `cdx_cmd_codes.h`) in the licensed source that document opcodes?
- Can Mono ask NXP for the opcode list under NDA extension to Miha personally?

If yes to any: probe time drops by half. If no: 8-10 weeks remains the estimate.

---

## 13. VyOS integration `[AGENT-IMPLEMENTABLE]`

### 13.1 CLI surface

Follow the VPP precedent. Add to `vyos-1x/interface-definitions/`:

- `system-offload-ask.xml.in` — top-level enable/configure for ASK
- `interfaces-ethernet.xml.in` — add `offload ask` and `offload vpp` leaf

Example operator config:
```
set system offload ask
set system offload ask max-flows 750
set system offload ask flowtable-overflow software
set system offload ask exclude-alg ftp sip pptp

set interfaces ethernet eth0 offload ask
set interfaces ethernet eth1 offload ask
set interfaces ethernet eth2 offload ask
set interfaces ethernet eth3 offload ask
set interfaces ethernet eth4 offload ask

set system offload ask promote vpp acl 100
```

### 13.2 conf_mode scripts

```
vyos-1x/src/conf_mode/
├── system_offload_ask.py         # writes /etc/config/fastforward, reloads askd
└── interfaces_ethernet.py        # adds offload-ask config to per-port policy
```

`system_offload_ask.py` talks to `askd` over `/var/run/askd.sock` for live reconfig. Falls back to a service restart if live reconfig fails.

### 13.3 Operational commands

```
show offload ask flows                  # list installed flows
show offload ask flows table cdx_tcp4   # filter by table
show offload ask stats                  # aggregate counters
show offload ask muram                  # MURAM allocation
show offload ask events                 # recent eviction/error events
clear offload ask flows                 # flush all flows
restart offload ask                     # systemctl restart askd
monitor offload ask events              # live tail
```

### 13.4 op_mode scripts

```
vyos-1x/src/op_mode/
└── offload_ask.py                       # implements show/clear/restart/monitor
```

---

## 14. Upstream strategy (long-term, post-v1.0)

### 14.1 What we'd want to upstream

1. `201-caam-qi-share.patch` — small, defensible, has mlx5 precedent. **High priority for v1.0 + 3 months.**
2. Generic netlink family `ask` — once stable, propose as `vy_ask` or `qoriq_ask` to maintainers. Medium priority for v1.5.
3. tc-flower offload for DPAA1 `dpaa_eth` driver — this is the big one. Would allow ASK 2.0 to ride on `flowtable` HW offload eventually. **Multi-year effort, depends on NXP cooperation.** Track separately.

### 14.2 What stays out-of-tree forever

- `ask.ko` and `ask_bridge.ko` themselves. They depend on FMan-specific FMC API surface that's not exposed to generic netdev offload infrastructure.
- The 210 ucode. Binary blob, not in scope to upstream.
- `askd` and `ask-load`. Userspace, no upstreaming concept.

---

## 15. Effort estimation with agentic implementation

### 15.1 The agentic multiplier

Conventional estimation for clean-room C-language kernel + userspace rewrites uses **80-120 LOC/engineer-day** for kernel code, **150-250 LOC/engineer-day** for userspace daemons. Beast Mode style agents (Cline + Sonnet/Opus, with a well-scoped spec like this one) raise these to **400-800 LOC/engineer-day** for code with strong precedent (genl protocol handlers, ioctl shims, xml parsing, libcli CLIs) and **200-400 LOC/engineer-day** for code with weak precedent (FMan driver glue, undocumented opcode handling).

The agentic speedup applies to **writing** code. It does NOT apply to:
- Hardware probing (Section 12 reverse engineering)
- Mainline kernel patch review cycles
- NXP commercial negotiation
- Real-hardware verification time
- Acceptance gate testing on the Spirent / Keysight test harness

### 15.2 Re-baselined LOC and effort

| Component | LOC estimate | Precedent strength | Agentic eng-days |
|---|---|---|---|
| `200-ask2-hooks.patch` | 1500 | strong (xt modules verbatim, genl glue boilerplate) | 4 |
| `201-caam-qi-share.patch` | 200 | strong (mainline pattern) | 2 |
| `ask.ko` kernel module | 5000 | mixed (genl strong, FMC API mixed, opcode handling weak) | 25 |
| `ask_bridge.ko` | 400 | strong | 2 |
| `askd` userspace daemon | 6000 | strong (netlink consumer + glib loop is well-trodden) | 20 |
| `ask-load` userspace | 1200 | strong | 5 |
| `libask_fci` library | 800 | strong | 3 |
| fmlib + fmc re-patch | 200 | strong (re-derivation, not authoring) | 2 |
| VyOS CLI XML + conf_mode + op_mode | 1500 | strong (VyOS plugins are well-documented) | 5 |
| Build pipeline (ci-build-ask*, ci-verify-ask-iso) | 500 | strong | 3 |
| Test harness (kunit + userspace pytest) | 2000 | strong | 8 |
| Documentation (operator manual, dev guide) | n/a | strong | 6 |
| **Subtotal: agent-implementable engineering days** | **~19000 LOC** | | **85 eng-days** |

### 15.3 Human-only work

| Activity | Calendar weeks | Reason |
|---|---|---|
| 210 opcode reverse engineering (Section 12) | 8-10 | Hardware probing, judgement |
| Mainline kernel patch upstream review | 12-16 | Out of our control; runs in parallel |
| Hardware verification at each milestone | 8 | Real-board test loops |
| Performance gate runs (Geerling-style 1 Mpps HTTP) | 4 | Test harness time |
| NXP/Mono commercial conversations | 2-4 | Variable |
| Code review and integration | 6 | Human judgement on agent output |
| **Subtotal: human-only calendar weeks (sequential where dependent)** | | **~24 weeks** |

### 15.4 Realistic total schedule

With **two engineers** (one kernel-focused, one userspace + VyOS integration), running agent-driven implementation with structured human review:

- **Months 1-2**: Architectural scaffolding (ask.ko skeleton, genl family, first flow type). Agent writes; human probes 210 opcodes for IPv4 UDP/TCP. M2 acceptance gate: kernel boots with `ask.ko` loaded, one flow type works end-to-end.
- **Months 3-4**: All non-IPsec flow types + ask_bridge + askd skeleton. Agent writes; human probes 210 for NAT and bridge. M4 acceptance gate: full forwarding + NAT at ASK 1.x parity.
- **Month 5**: CAAM xfrm offload (single largest individual feature). Agent writes glue; human verifies on real CAAM. M5 acceptance gate: AES-GCM-128 IPsec at 3 Gbps.
- **Month 6**: VyOS CLI integration, ask-load XML compatibility, libask_fci ABI compatibility. Mostly agent work with strong precedent. M6 acceptance gate: vanilla Mono Gateway DK boots a VyOS rolling image with `set system offload ask`.
- **Month 7**: VPP coexistence, software flowtable fallback, debugfs and stats. M7 acceptance gate: 3-plane architecture demo (ASK fast + kernel slow + VPP).
- **Month 8**: Performance gate runs at acceptance levels (Section 15.5). Bug fixes. Documentation.
- **Month 9**: Release candidate, soak testing, v1.0 ship.

**Total: 9 months, 2 engineers.** Down from the v0.5 estimate of "9 months for 2 engineers" but with much higher confidence — the saved CAAM-driver work and the agentic speedup buy back the schedule that the 210 reverse-engineering work consumes.

If only one engineer is available: **14-16 months calendar time** because the kernel and userspace tracks can't fully parallelise.

If Mono provides NXP internals reducing opcode probe time to 4 weeks: **7 months, 2 engineers**.

### 15.5 Acceptance gates (the GO/NO-GO targets)

**Functional gates** (must all pass for v1.0 GA):
- Plain IPv4 + IPv6 forwarding via 5-tuple flow tables.
- IPv4 NAT (SNAT, DNAT, PAT) with conntrack sync.
- L2 bridge flow offload.
- IPsec ESP IPv4 tunnel mode + AES-GCM-128.
- Existing `cdx_cfg.xml` and `cdx_pcd.xml` from `we-are-mono/ASK/config/gateway-dk/` boot unchanged.
- `/dev/cdx_ctrl` ioctl ABI compatibility with vendor tools that link `libfci.so.1`.
- VyOS CLI: `set system offload ask` produces a working config.

**Performance gates** (Jeff Geerling / STH-style measurement on Mono Gateway DK):
- **GO** if all of: ≥14 Gbps bidirectional at 512 B IPv4 forwarding; ≥1 Mpps real HTTP through-traffic; ≥3 Gbps AES-GCM-128 at 1024 B; <20% CPU on 4 cores at line rate.
- **NO-GO** if any of those slip by more than 30%.

**Quality gates**:
- All kernel modules signed by ASK in-tree signing key per `MODULE_SIG_FORCE=y`.
- `patch-health.sh` reports Pass: 2 Fail: 0 SDK conflicts: 0.
- Code coverage ≥70% for `askd` (measured via gcov).
- No regressions in the VyOS smoke test suite when running with FLAVOR=default.

---

## 16. Risk register

| # | Risk | Probability | Impact | Mitigation |
|---|---|---|---|---|
| 1 | 210 opcode probing exceeds 10 weeks | Medium | High | Get NXP internals via Mono early; partial-feature release if needed |
| 2 | Mono's 210 build doesn't expose every feature CDX 1.x had | Low | High | Audit feature parity at M2; descope features that are gone |
| 3 | `201-caam-qi-share` rejected upstream | Low | Low | Keep as OOT patch indefinitely; mainline acceptance not on critical path |
| 4 | Mainline `caam_qi.ko` API surface insufficient for descriptor share | Medium | Medium | Extend the in-tree patch; worst case is +400 LOC |
| 5 | Kernel 6.18 LTS gets superseded mid-project | Low | Medium | Track 6.18.x LTS until 2027; if VyOS moves, rebase |
| 6 | VyOS Inc. drops VPP from default | Low | Low | Option A (ASK-only) is the fallback; supported in spec |
| 7 | Performance gate misses Geerling 1 Mpps | Medium | High | Hardware-specific tuning; ucode-policy review; OP plumbing verification |
| 8 | Agent-generated code carries subtle bugs | High | Medium | Test coverage gate ≥70%; mandatory human review of every PR |
| 9 | LOC estimates wrong by 2× | Medium | Medium | Padding built into 9-month schedule; revisit at M2 |
| 10 | Microcode redistribution legal question | Low | Low | Resolved: 210 ships on board, never in our image |
| 11 | Mainline kernel API churn in 6.18.x point releases | Medium | Low | Pin to LTS, only update at release boundary |
| 12 | VPP+memif latency unacceptable for some flows | Medium | Low | Make VPP promotion opt-in per-ACL; default is direct ASK forwarding |

---

## 17. Open questions (for human resolution)

1. **NXP internals access** — does Mono have an ASK Programmer's Reference Manual that documents 210 opcodes? Ask Tomaž directly. Resolves Risk #1.
2. **210 feature parity** — does Mono's 210 build support every CDX 1.x feature (4o6 tunneling, EtherIP, IP fragmentation)? Audit early; descope where needed.
3. **VyOS rolling vs LTS target** — v1.0 GA against rolling makes sense for early adopters; 1.6 LTS (probably mid-2026) is the long-term home. Confirm with VyOS Inc.
4. **askd unix socket vs DBus** — current spec says `/var/run/askd.sock` libcli-style. VyOS Inc. is migrating some daemons to DBus. Check `vyos-1x` direction.
5. **Test hardware availability** — running the performance gates requires a Spirent or Keysight CyPerf rig. Patrick Kennedy at STH ran the original Geerling test on Keysight; can we get access? Otherwise use software traffic generators with documented caveats.
6. **Per-CPU stats aggregation cadence** — 1 Hz default in v0.6 spec; consider configurable per-flow refresh for high-frequency operators.
7. **Multi-FMan support** — LS1046A has one FMan, but the spec should anticipate LS1046A in dual-FMan configurations (LS1048A, future SoCs). Out of scope for v1.0; flag for v2.0.
8. **CAAM Job Ring fallback** — when CAAM QI is busy or unconfigured, can we fall back to caam_jr? Adds complexity; defer to v1.1 unless ops team asks.

---

## 18. Naming, paths, and ABI compatibility map

For operators upgrading from ASK 1.x or running tools linked against legacy `libfci`:

| Legacy path/symbol | ASK 2.0 path/symbol | Compat strategy |
|---|---|---|
| `/dev/cdx_ctrl` | `/dev/ask_ctrl` | Symlink at module load |
| `CDX_CTRL_*` ioctls | same numbers, same structs | Shim in `ask_dev.c` |
| `libfci.so.1` | `libask_fci.so.1` | Symlink + dlsym compat |
| `cmm.service` | `askd.service` | systemd alias |
| `cmmctl` | `askctl` | symlink + wrapper |
| `/etc/config/fastforward` | unchanged | Same format |
| `/etc/cdx_cfg.xml` | unchanged | Same schema |
| `/etc/cdx_pcd.xml` | unchanged | Same schema |
| `/etc/modules-load.d/cmm-modules.conf` | `ask-modules.conf` | Migration on first boot |
| `cdx.ko` symbol exports | `ask.ko` symbol exports | KABI: not preserved; vendor tools must rebuild |
| `auto_bridge.ko` | `ask_bridge.ko` | Same as above |
| `NETLINK_KEY=32` channel | generic netlink family `ask` | `libask_fci` translates |
| `/* ASK-edit */` source markers | unchanged | Only present in SDK sources, not in ASK 2.0 code |

---

## 19. Implementation order (cookbook for agents)

For an agent picking up this spec cold:

1. **Read Sections 1, 2, 3, 4, 7 in full.** Understand the architecture and the genl protocol.
2. **Create the file layout.** All directories, empty `Kbuild` files, empty `Makefile`s. Commit.
3. **Implement `ask.ko::ask_main.c` + `ask_genl.c`.** Make the module load, register the genl family, accept the dummy `ASK_CMD_TABLE_LIST` command and return an empty dump. Test with `genl ask dump`. Don't touch FMan yet.
4. **Implement `ask_dev.c`** — `/dev/ask_ctrl` chardev with stub ioctls. Test with `cat /dev/ask_ctrl`.
5. **Implement `askd` skeleton.** Just the event loop, conntrack consumer, genl sender. Verify it sees conntrack events and sends `ASK_CMD_FLOW_ADD` (which `ask.ko` returns `-ENOSYS` to for now).
6. **Implement `ask-load`.** XML parsing, fmc invocation, `ASK_CMD_LOAD_PCD` payload construction. Verify `ask.ko` receives and parses the model correctly.
7. **Now the FMan work** — `ask_dpaa.c`, `ask_flowtable.c`. This is where Section 12 (the 210 opcode probing) begins. Pause agent-driven work here; switch to human-led probing.
8. **Once IPv4 UDP flow add/del/query works**, validate on hardware: install a flow manually via genl, send packets, verify they bypass conntrack and traverse the fast path.
9. **Iterate through the Section 12 feature matrix** in priority order.
10. **CAAM xfrm offload** is the largest standalone milestone. Allocate Month 5 to it.
11. **VyOS CLI integration** comes near the end — there's no point making the CLI pretty before the engine works.

---

## 20. Glossary

- **210 ucode** — NXP proprietary FMan microcode family implementing fine-grained per-flow classification. Licensed; baked into Mono Gateway firmware.
- **108 ucode** — Public NXP FMan microcode family implementing coarse static classification. Free on github.com/nxp-qoriq/qoriq-fm-ucode.
- **ASK** — NXP Application Solutions Kit. The commercial stack that turns FMan from a packet-distribution engine into a flow-termination engine.
- **CC** — Coarse Classifier. The FMan silicon block used by 108 ucode. With 210, it's repurposed as dynamic per-flow tables.
- **CDX** — the legacy kernel fast-path engine in ASK 1.x. Replaced by `ask.ko` in 2.0.
- **CMM** — Connection Management Module. Legacy userspace daemon. Replaced by `askd` in 2.0.
- **FCI** — Fast-path Control Interface. Legacy netlink channel between CMM and CDX. Replaced by genl family `ask` in 2.0.
- **FMan** — Frame Manager. The packet-processing silicon block on LS1046A.
- **FMC** — Frame Manager Configuration tool. Userspace XML-to-PCD compiler. Reused in 2.0 via patched upstream.
- **OP** — Offline Port. FMan port without a MAC; used for re-injection after CAAM decryption.
- **PCD** — Parse-Classify-Distribute. FMan's silicon-level packet pipeline.
- **QI** — Queue Interface. CAAM's QMan-integrated path. The fast crypto path on LS1046A.

---

**End of v0.6.** This spec supersedes v0.5 in entirety. Out of scope for any future revision: rewriting `fsl_dpaa1`, mainline FMan, `caam_qi.ko`, VPP itself, or anything below the FMan microcode horizon. Those belong elsewhere in the repo.