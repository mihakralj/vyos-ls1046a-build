# ASK 2.0 — Clean-Room Rewrite of the NXP ASK Fast-Path for VyOS on LS1046A

**Status:** Draft v0.5 (renamed ASK 2.0, 2026-05-11) — supersedes v0.4 in entirety.
**Target hardware:** NXP LS1046A Mono Gateway DK (Cortex-A72 ×4, FMan v3L, QMan v3, BMan v3, CAAM SEC 5.4).
**Target software:** Linux mainline kernel 6.18.x, ARM64, VyOS 1.5+, FLAVOR=ask in this repo.
**Reference implementation (the "ASK 1.x cribsheet"):** the NXP ASK source archived in this repo at `ASK/` plus `data/kernel-patches/003-ask-kernel-hooks.patch` (the kernel-hooks patch absorbed from the formerly-separate kernel build repo's `mt-6.12.y` baseline; 5797 lines, 64 mainline files modified + 10 new files added).
**License intent:** GPL-2.0-or-later for kernel parts (matches NXP's ASK licensing); GPL-2.0-or-later for userspace daemons; LGPL-2.1-or-later for libfci.
**Naming convention.** "**ASK 1.x**" = the proprietary NXP-LSDK-derived stack as it ships today (`cdx.ko`, `auto_bridge.ko`, `cmm`, `dpa_app`, `libfci`, `003-ask-kernel-hooks.patch`). "**ASK 2.0**" = the clean-room replacement defined by this spec (`ask.ko`, `ask_bridge.ko`, `askd`, `ask-load`, `libask_fci`, `200-ask2-hooks.patch`). The umbrella brand "**ASK**" — and the FLAVOR=ask build target, the `kernel/flavors/ask/` tree, the `/* ASK-edit */` markers, the `ask-modules-load.service` unit — all stay; only the proprietary internals get renamed.
**What this spec is NOT:** a rewrite of `fsl_dpa`, `fsl_dpaa_mac`, `fsl_qbman`, mainline FMan, AF_XDP, tc-flower, CAAM xfrm, or DPDK PMD. Those belong to the **default flavor** of this repo and have separate specs/plans (`plans/NETWORKING-DEEP-DIVE.md`, `plans/VPP.md`, `plans/archive/MIGRATION-PLAN-6.18.md`). Anything below the FMan microcode horizon is out of scope.

---

## 0. What is ASK and why does it need a rewrite?

The **NXP Application Solutions Kit (ASK)** is a hardware-accelerated packet-processing stack for QorIQ Layerscape SoCs. On LS1046A it short-circuits established conntrack flows directly inside FMan — established TCP/UDP/ESP/PPPoE flows are matched in FMan's hash tables (CC nodes), parameters looked up in MURAM, and frames forwarded to the egress port via BMI without touching an A72 core. CMM (the userspace decision engine in ASK 1.x) watches `nf_conntrack` and pushes eligible flows into the FMan tables via the FCI netlink channel. Software stays authoritative; hardware accelerates the established-flow path.

The ASK 1.x stack as it ships today (FLAVOR=ask in this repo) is a **fork of NXP's LSDK**:

* `kernel/flavors/ask/sdk-sources/` — 266 vendored SDK files (`sdk_fman/`, `sdk_dpaa/`, `fsl_qbman/`, mEMAC bits, …) carrying 35 `/* ASK-edit (askN, …) */` markers. Frozen at the PR-7 single-repo absorption. Cannot be upstreamed. Kconfig-exclusive with mainline (`FSL_SDK_DPA depends on !FSL_DPAA`).
* `kernel/flavors/ask/patches/` — 16 patches against linux-6.6.137, ported piecemeal to 6.18.28.
* `kernel/flavors/ask/oot-modules/cdx/` — 15-file out-of-tree `cdx.ko` (the fast-path engine; programs FMan from kernel context, calls `dpa_app` via UMH at insmod, registers `/dev/cdx_ctrl`, talks to FCI via NETLINK_KEY=32).
* `kernel/flavors/ask/oot-modules/auto_bridge/auto_bridge.c` — single-file OOT module that watches bridge-FDB and notifies `cdx` of L2 flows eligible for offload.
* `data/kernel-patches/003-ask-kernel-hooks.patch` — the **6.12.y-derived** in-tree-hooks patch: 64 mainline files modified, 10 brand-new files added (`net/netfilter/comcerto_fp_netfilter.c`, `xt_qosmark.c`, `xt_qosconnmark.c`, `net/xfrm/ipsec_flow.{c,h}`, four UAPI headers, …). This is the kernel-side ABI the OOT modules depend on.
* `ASK/cmm/` — userspace daemon (60+ files; `cmm.c`, `conntrack.c`, `ffbridge.c`, `ffcontrol.c`, `forward_engine.c`, `keytrack.c`, `module_expt.c`, `module_icc.c`, `libcmm.c`, …).
* `ASK/dpa_app/` — userspace policy programmer (`dpa.c`, `main.c`, `testapp.c`).
* `ASK/fci/lib/` — `libfci.c` + `libfci.h` (LGPL).
* `ASK/patches/{fmc,fmlib}/01-mono-ask-extensions.patch` — patches against upstream `nxp-qoriq/fmlib` and `nxp-qoriq/fmc` that extend `t_FmPcdKgSchemeParams` (adds `bool shared`) and `t_FmPcdHashTableParams` (timestamp/IP-reassembly/shared-scheme fields). Required because `dpa_app` consumes those structs by value.

The replacement target — the **ASK 2.0** clean-room rewrite — must:

1. Reproduce the externally visible behaviour (same `/dev/cdx_ctrl` ioctl set, same `cdx_cfg.xml` and `cdx_pcd.xml` schemas, same NETLINK_KEY=32 protocol, same CMM CLI surface and `/etc/config/fastforward` format) so existing operator tooling works unchanged.
2. Be implemented from scratch against the LS1046A Reference Manual and FMan documentation, **not** by copying NXP's SDK source. Existing `ASK/` tree and `data/kernel-patches/003-ask-kernel-hooks.patch` are the **functional reference**, not a code donor.
3. Target mainline 6.18.x. No 6.6 backport. No SDK fork.
4. Keep depending on the **proprietary FMan microcode v210.10.1** (loaded by U-Boot from SPI NOR `mtd4`, injected into the DTB `fsl,fman-firmware` node before kernel handoff). The microcode is the silicon program that gives FMan its parse/classify/forward pipeline; it is not in the rewrite scope and cannot be replaced from a clean-room.
5. Coexist with the default-flavor mainline DPAA1 stack at the **flavor** level (Kconfig-exclusive, separate kernel build), not at runtime.

This spec defines (1)–(5).

---

## 1. Hardware reference — the parts that matter for ASK

### 1.1 The FMan classifier silicon

Authoritative source: LS1046A RM chapter 8 (DPAA), specifically §8.7 (parser+KeyGen), §8.8 (CC), §8.9 (Policer), §8.10 (BMI). Implementer must read these chapters before writing any ASK 2.0 code; this spec is a roadmap, not a substitute.

Each FMan port has, per RM:

* **Parser** — runs proprietary microcode on a dedicated RISC engine inside FMan (NOT on the A72). For the default flavor we use the standard ucode (`fsl_fman_ucode_ls1046_r1.0_106_4_18.bin`); for ASK we use **v210.10.1** (proprietary NXP binary — a different program for the same engine that adds the parse-result fields ASK needs and the BMI short-circuit path). Both feed into KeyGen.
* **KeyGen** — extracts a hash key from the parse result, hashes it (CRC), looks up a base FQID, and enqueues. Up to 128 FQ-distributions ("schemes") per port. Used by mainline mostly for RSS; used by ASK as the front-half of CC-table lookup.
* **Coarse Classifier (CC)** — the silicon block this whole spec is about. Per FMan, CC supports:
  * **Exact-match** nodes (TCAM-like) — up to 256 entries per node.
  * **Hash-table** nodes — chained `<num_sets>` × `<num_ways>` buckets in MURAM (and optionally DDR for "external hash"). The `cdx_pcd.xml` we ship hard-codes mask `0x7fff` for IPv4/IPv6 5-tuple tables (32K buckets) and `0xff` for narrower tables.
  * Per-key actions: enqueue-to-FQ (default), direct-forward-to-TX-port (the "fast path"), drop, policer redirect, modify-and-forward (NAT — used by 4RD/EtherIP).
* **Policer** — dual-rate token-bucket per port and per flow; ASK uses it for the exception-rate limiter (`CDX_EXPT_*_RATELIM`, see `ASK/dpa_app/dpa.c` `set_exptrate_policer_defaults`).
* **BMI / MURAM / IRAM** — DMA engine, shared scratchpad, microcode RAM; all sized fixed by silicon.

The ASK fast-path uses CC's "modify-and-forward" action: when a CC entry matches an established flow, FMan rewrites the L2 header with the cached next-hop MAC, decrements TTL, fixes the IP/L4 checksums, and enqueues directly to the egress port's TX FQ. The A72 never sees the packet.

### 1.2 MURAM — the hard limit

MURAM is shared FMan scratchpad. ASK's hash tables, KeyGen schemes, FCI port-to-policy map, parser config, FQ contexts, **and** the BMan fragment buffer pools used by CC for IP-reassembly all live in MURAM. **Exhaustion is the canonical Chain-2 production failure** (see §8). The ASK 2.0 rewrite must:

* Compute MURAM use up-front from `cdx_cfg.xml` + `cdx_pcd.xml`, fail loudly at policy-load time if oversubscribed, never silently truncate.
* Match the SDK's per-table MURAM accounting **byte for byte** (current tables: 16 of them, listed in §3.3) so existing `cdx_pcd.xml` policies parse-fit identically.

### 1.3 The Mono Gateway DK port map (already canonical in `AGENTS.md`)

| netdev | FMan MAC | DT unit-addr | physical port | `cdx_cfg.xml` portid |
|--------|----------|--------------|----------------|----------------------|
| `eth0` | mEMAC5   | `e8000`      | left RJ45 1G   | 4  |
| `eth1` | mEMAC6   | `ea000`      | center RJ45 1G | 5  |
| `eth2` | mEMAC2   | `e2000`      | right RJ45 1G  | 1  |
| `eth3` | mEMAC9   | `f0000`      | left SFP+ 10G  | 6  |
| `eth4` | mEMAC10  | `f2000`      | right SFP+ 10G | 7  |
| (offline, `fman0-oh@2`) | OP1 | — | IPsec offload bound | 8 |
| (offline, `fman0-oh@3`) | OP2 | — | WiFi/AP offload bound | 9 |

The **offline ports (OPs)** are FMan ports with no MAC: they ingest from one BMI queue and re-inject to another. ASK uses them for flows that need an extra parse pass after a software action: encrypted packets land on OP1 after CAAM, get re-parsed and CC-classified for forwarding; bridge-flooded packets go via OP2 for wireless ALG. The ASK 2.0 rewrite must support both OPs; `cdx_cfg.xml` already encodes them (`<port type="OFFLINE" .../>`).

The offline-port semantics is **the** thing that distinguishes ASK from any tc-flower offload story. tc-flower has no model for "re-inject a packet into the classifier from the egress side"; ASK does, because the LS1046A silicon does. This is non-negotiable for IPsec offload.

### 1.4 What ASK does NOT touch

* No PAMU programming (the standard DMA API handles addressing for kernel buffers; ASK doesn't expose userspace DMA).
* No CAAM JR ownership (mainline `caam_jr` keeps ownership; ASK uses CAAM via `xfrm` offload hooks defined in `data/kernel-patches/003-ask-kernel-hooks.patch::net/xfrm/ipsec_flow.{c,h}`).
* No mEMAC PHY/SFP/link control (mainline `fsl_dpaa_mac` + phylink for default flavor; SDK `fsl_mac` for ASK flavor — out of scope here).
* No DTB structural changes vs. the existing `data/dtb/mono-gateway-dk-sdk.dts` (16 RX/TX port nodes in SDK compatible format; the rewrite still needs SDK-format strings because it consumes the same SDK BMan/QMan portal layer).

---

## 2. Component inventory — ASK 1.x → ASK 2.0

The clean-room rewrite replaces the proprietary SDK pieces. Mainline-side changes are minimised; the kernel-hooks patch is rewritten as a **smaller** patch that hooks `flowtable` and `XFRM_OFFLOAD` instead of the legacy ASK ad-hoc hooks. Userspace daemons are rewritten in modern C with proper netlink (libmnl).

| Layer | ASK 1.x (NXP / SDK source today) | ASK 2.0 (clean-room replacement) | Naming notes |
|-------|----------------------------------|-----------------------------------|--------------|
| Kernel — fast-path engine | `kernel/flavors/ask/oot-modules/cdx/` (15 files, OOT module `cdx.ko`) | New OOT module **`ask.ko`** — same external API on `/dev/cdx_ctrl` for compat. | NOT `cdx`: mainline 6.18 already has `drivers/cdx/cdx.c` (AMD/Xilinx CDX bus, completely different thing). The userspace ABI symbol `/dev/cdx_ctrl` and `CDX_CTRL_*` ioctl names stay (compat); the **module name** changes to match the `kernel/flavors/ask/` directory and the FLAVOR=ask brand. |
| Kernel — bridge-FDB watcher | `kernel/flavors/ask/oot-modules/auto_bridge/auto_bridge.c` (single file) | **`ask_bridge.ko`** | OOT, GPL. |
| Kernel — netfilter ↔ userspace channel | `data/kernel-patches/003-ask-kernel-hooks.patch::net/netfilter/comcerto_fp_netfilter.c` + patches to `nf_conntrack_*` + `net/xfrm/ipsec_flow.{c,h}` + UAPI headers + `net/key/af_key.c` (to wire NETLINK_KEY=32 via `ask_fci_nlkey`) | A **smaller** in-tree-hooks patch (`kernel/flavors/ask/patches/200-ask2-hooks.patch`), backed by mainline **`flowtable`** for bridge offload and a new `XFRM_OFFLOAD` provider for IPsec. The legacy proto-32 NETLINK_KEY channel is **kept** for FCI (the userspace ABI dependency makes it cheaper to keep than to migrate cmm to `nf_tables` netlink). | New file lives under `net/netfilter/ask_fci.c` (already matches the existing `ask_fci_nlkey` symbol naming — minimal symbol churn). UAPI headers stay at `linux/netfilter/xt_QOSMARK.h`, `xt_QOSCONNMARK.h`. |
| Kernel — QOSMARK/QOSCONNMARK xt match/target | new files in `003-ask-kernel-hooks.patch` | Reused as-is (these are 200-line standalone xt modules; no scope to rewrite). | `xt_qosmark.ko`, `xt_qosconnmark.ko`. |
| Userspace — policy programmer | `ASK/dpa_app/` (`dpa.c`, `main.c`, `testapp.c`) | **`ask-load`** — clean-room rewrite. Same XML schemas in/out. | binary at `/usr/sbin/ask-load`. Spawned by `ask.ko` via `call_usermodehelper(UMH_WAIT_PROC)` at insmod (matches existing model, see `cmm.service` comment). |
| Userspace — connection manager | `ASK/cmm/` (60+ files) | **`askd`** — clean-room daemon. Same `fastforward` config format, same CLI. | `/usr/bin/askd`. systemd unit guarded by `ConditionPathExists=/dev/cdx_ctrl`. |
| Userspace — FCI library | `ASK/fci/lib/` (`libfci.c`, `libfci.h`) | **`libask_fci`** — clean-room. Header-compatible with `libfci.h`. | `/usr/lib/aarch64-linux-gnu/libask_fci.so.1`, symlinked as `libfci.so.1` for vendor-tool ABI compat. |
| Userspace — fmlib extensions | `ASK/patches/fmlib/01-mono-ask-extensions.patch` | Re-derived patch against current `github.com/nxp-qoriq/fmlib` head. Same struct extensions (`t_FmPcdKgSchemeParams::shared`, `t_FmPcdHashTableParams::{table_type,timeout_val,…}`); same byte layout. | `data/ask-userspace/fmlib/patches/01-ask2-extensions.patch`. |
| Userspace — fmc extensions | `ASK/patches/fmc/01-mono-ask-extensions.patch` | Re-derived patch against current `github.com/nxp-qoriq/fmc` head. | `data/ask-userspace/fmc/patches/01-ask2-extensions.patch`. |
| Userspace — libnetfilter-conntrack ASK extensions | (in `data/ask-userspace/libnetfilter-conntrack/`) | Re-derived: fast-path info attributes + QOSCONNMARK accessors. | unchanged file location, new patch name. |
| Userspace — libnfnetlink async heap | (in `data/ask-userspace/libnfnetlink/`) | Re-derived: non-blocking socket + heap-buffer mode for `askd` event burst handling. | unchanged. |
| Userspace — iptables QOSMARK target | `ASK/` patch series | Re-derived patch. | unchanged. |
| Userspace — iproute2 4RD/EtherIP | `ASK/` patch series | Re-derived patch. | unchanged. |
| Userspace — ppp ifindex | `ASK/` patch series | Re-derived patch. | unchanged. |
| Userspace — rp-pppoe CMM relay | `ASK/` patch series | Re-derived patch (now talks to `askd`). | unchanged. |

What survives unchanged from the proprietary side:

* The **FMan microcode v210.10.1** binary in U-Boot SPI flash. Out of scope.
* The **vendored SDK `sdk_fman` / `sdk_dpaa` / `fsl_qbman`** drivers under `kernel/flavors/ask/sdk-sources/`. ASK's CC programming goes through the SDK's `FM_PCD_*` API; rewriting that layer is a separate, much larger, project (it's the FMan v3L kernel driver — see `plans/archive/MIGRATION-PLAN-6.18.md`). For ASK 2.0, the SDK is the **substrate** the new modules sit on top of, exactly like in ASK 1.x. Default-flavor users get mainline `fsl_dpa`/`fsl_dpaa_mac` with no ASK at all.

What survives unchanged from the **operator-facing UAPI** (vendor-tool / config-file ABI compat):

* `/dev/cdx_ctrl` chardev path and **all** `CDX_CTRL_*` ioctl names, numbers, struct layouts.
* `cdx_cfg.xml`, `cdx_pcd.xml`, `cdx_sp.xml` schemas (and the existing files in `ASK/config/gateway-dk/` and `ASK/dpa_app/files/etc/`).
* `/etc/config/fastforward` (UCI-style ALG-exclusion list).
* NETLINK_KEY=32 (FCI) wire-format — `libfci.so.1` ABI preserved for any vendor tool that links it.
* `/etc/modules-load.d/ask-modules.conf`, `ask-modules-load.service` unit name, `/* ASK-edit */` source markers.

---

## 3. Kernel module spec — `ask.ko` (replaces `cdx.ko`)

### 3.1 Responsibilities

1. Probe the SDK FMan driver (`fsl_fman`) at `module_init`. If the FMan microcode signature does not match v210.10.1, **bail out cleanly** with `dev_warn_once` and never create `/dev/cdx_ctrl`. (This is what gates `cmm.service` / `askd.service` via `ConditionPathExists`.)
2. Spawn `/usr/sbin/ask-load` synchronously with `UMH_WAIT_PROC` to apply the policy from `cdx_cfg.xml` + `cdx_pcd.xml`. `ask-load` calls back into `ask.ko` via `CDX_CTRL_DPA_SET_PARAMS` ioctl with the compiled FMC model.
3. Build the BMan fragment buffer pools (one per OFFLINE port) used by CC IP-reassembly hash tables. Failure here is the canonical *"failed to locate eth bman pool"* message in the Chain-2 failure model (§8).
4. Register the FCI netlink protocol (NETLINK_KEY=32 — kept for ABI compat with existing `cmm`/`askd` clients) and the `nf_conntrack_event` notifier so the userspace daemon learns about new flows.
5. Expose the `/dev/cdx_ctrl` chardev with the existing ioctl numbering (see UAPI header below).
6. Implement IPsec offload as an `XFRM_OFFLOAD` provider that uses CAAM via the offline-port re-injection model (encrypted packet → CAAM JR → OFFLINE port OP1 → CC re-classify → forward). This replaces the legacy `INET_IPSEC_OFFLOAD=y` path that depends on the `xfrm_state` field layout from kernel 6.6 (which is broken on 6.18 — see `AGENTS.md` "Kconfig invariants" line `CONFIG_INET_IPSEC_OFFLOAD=n`).
7. Provide bridge L2-flow offload via the mainline **`flowtable`** infrastructure (`nf_flow_table`), invoked by `ask_bridge.ko` when the FDB learns a new MAC. This replaces the bespoke `auto_bridge` → CDX path with `flowtable` semantics that the mainline kernel already understands.

### 3.2 File layout (target)

```
kernel/flavors/ask/oot-modules/ask/
├── Kbuild
├── ask_main.c          # init/exit, /dev/cdx_ctrl chardev, UMH ask-load spawn
├── ask_dev.c           # FMan/PCD device handle plumbing (replaces cdx_dev.c)
├── ask_dpa.c           # DPA SET_PARAMS ioctl, port/dist/table ingest
├── ask_dpa_ipsec.c     # XFRM_OFFLOAD provider, CAAM submit/recv, OP1 re-inject
├── ask_qos.c           # exception-rate-limit policer setup, QOSMARK fastpath
├── ask_ehash.c         # external-hash CC operations (add/del/lookup)
├── ask_reassm.c        # IP-reassembly CC node config + BMan pool seed
├── ask_cmdhandler.c    # FCI protocol handler (NETLINK_KEY=32 receive path)
├── ask_ifstats.c       # per-flow byte/frame counters → libask_fci accessors
├── ask_mc_query.c      # multicast group lookup for CDX_CTRL ioctls
├── ask_timer.c         # IP-reassembly timeouts, conntrack-aging tick
├── ask_debug.c         # debugfs/seq_file under /sys/kernel/debug/ask/
└── ask_internal.h      # private types, NOT exposed to userspace

include/uapi/linux/ask/
├── cdx_ctrl.h          # /dev/cdx_ctrl ioctl numbers + structs (CDX_CTRL_*)
└── fci.h               # NETLINK_KEY=32 message format (FCI_CMD_*)
```

The `cdx_ctrl.h` UAPI is **byte-compatible** with the existing SDK header; only the implementation behind it changes. (No struct rename, no ioctl renumber — `CDX_CTRL_DPA_SET_PARAMS` stays `_IOW('C', 1, struct cdx_ctrl_set_dpa_params)`, etc. The exact constant values are pinned by the existing `dpa.c` in `ASK/dpa_app/dpa.c` — see §5.1.)

### 3.3 The 16 CC-table types (pinned by `cdx_pcd.xml`)

Verbatim from `ASK/dpa_app/dpa.c::table_params[]` and `ASK/dpa_app/files/etc/cdx_pcd.xml`. The ASK 2.0 implementation must support all 16 with the same MURAM footprint:

| Table name | Key size (B) | Mask | Purpose |
|------------|--------------|------|---------|
| `cdx_udp4`        | 14 | 0x7fff | IPv4 UDP 5-tuple |
| `cdx_tcp4`        | 14 | 0x7fff | IPv4 TCP 5-tuple |
| `cdx_udp6`        | 38 | 0x7fff | IPv6 UDP 5-tuple |
| `cdx_tcp6`        | 38 | 0x7fff | IPv6 TCP 5-tuple |
| `cdx_multicast4`  | 10 | 0x00ff | IPv4 multicast 3-tuple |
| `cdx_multicast6`  | 34 | 0x00ff | IPv6 multicast 3-tuple |
| `cdx_pppoe`       | 11 | 0x000f | PPPoE session relay |
| `cdx_ethernet`    | 15 | 0x00ff | L2 bridge offload |
| `cdx_esp4`        | 10 | 0x00ff | IPv4 ESP/SPI |
| `cdx_esp6`        | 22 | 0x00ff | IPv6 ESP/SPI |
| `cdx_tuple3udp4`  | 8  | 0x00ff | IPv4 UDP 3-tuple (RTP relay) |
| `cdx_tuple3tcp4`  | 8  | 0x000f | IPv4 TCP 3-tuple (RTP relay) |
| `cdx_tuple3udp6`  | 20 | 0x00ff | IPv6 UDP 3-tuple |
| `cdx_tuple3tcp6`  | 20 | 0x000f | IPv6 TCP 3-tuple |
| `cdx_frag4`       | 12 | 0x000f | IPv4 reassembly context |
| `cdx_frag6`       | 38 | 0x000f | IPv6 reassembly context |

`max="512"` per table (pinned in the XML). All are `shared="true"` (one CC node, all 9 ports point at it via the `combine portid="true" offset="16" mask="0xF"` directive). The `cdx_*` table names are **part of the operator-facing schema** (`cdx_pcd.xml`) and stay under their `cdx_*` names regardless of the kernel-side rename.

### 3.4 Module dependency chain (load order)

`/etc/modules-load.d/ask-modules.conf` already pins this order, kept in `ASK/config/ask-modules.conf` and re-published as the runtime config for ASK 2.0:

```
ask                     # was: cdx
ask_bridge              # was: auto_bridge
nf_conntrack
nf_conntrack_netlink
xt_conntrack
ask_fci                 # was: fci, in-tree built-in once hooks patch lands
```

The systemd oneshot `ask-modules-load.service` modprobes these in order. `cmm.service` (renamed `askd.service`) has `Requires=ask-modules-load.service` so it never starts against a half-initialised fast-path. This ordering is **load-bearing** — see Chain-1 failure model in §8.

### 3.5 Kconfig

```
config ASK
    tristate "NXP LS1046A ASK 2.0 Fast-Path Engine (clean-room)"
    depends on FSL_SDK_DPA
    depends on NETFILTER && NF_CONNTRACK && BRIDGE
    depends on XFRM
    select NET_KEY                  # NETLINK_KEY=32 plumbing in net/key
    select NF_FLOW_TABLE             # backs ask_bridge offload
    help
      Out-of-tree-buildable replacement for the proprietary NXP cdx.ko fast-
      path module (the kernel-side core of ASK 1.x). Programs FMan CC tables
      to short-circuit established conntrack flows in hardware. Requires
      ASK-enabled FMan microcode v210.10.1 in SPI flash; without that
      microcode the module bails out and /dev/cdx_ctrl is never created.

config ASK_BRIDGE
    tristate "ASK 2.0 bridge-FDB watcher"
    depends on ASK && BRIDGE
```

`MODULE_SIG_FORCE=y` (per `AGENTS.md`) means both modules must be signed by the in-tree key after build — wired into `bin/ci-setup-kernel-ask.sh`.

> **Naming sanity check.** `CONFIG_ASK` does not collide with anything in mainline 6.18 (`grep -r '^config ASK' linux/` returns nothing). The FLAVOR=ask brand at the build-system level and the `CONFIG_ASK` symbol at the kernel level are independently scoped — there is no Kbuild conflict.

---

## 4. Kernel hooks patch — `200-ask2-hooks.patch`

The legacy `data/kernel-patches/003-ask-kernel-hooks.patch` modifies 64 mainline files. The ASK 2.0 equivalent is **smaller and tighter**:

### 4.1 What stays (kept functional, rewritten cleanly)

| File | Why it stays | Diff size estimate |
|------|--------------|---------------------|
| `net/key/af_key.c` | NETLINK_KEY=32 plumbing — `ask_fci` registers as `pfkey_proto[NETLINK_KEY]`. `CONFIG_NET_KEY=y` is still mandatory. | ~30 lines |
| `include/net/xfrm.h`, `net/xfrm/xfrm_state.c`, `net/xfrm/xfrm_user.c` | One new XFRM_OFFLOAD provider hook per existing kernel infrastructure. Replaces 003's bespoke `ipsec_flow.{c,h}` (200 lines deleted). | ~80 lines added, 200 removed |
| `net/netfilter/Kconfig`, `net/netfilter/Makefile` | Wires `xt_QOSMARK`, `xt_QOSCONNMARK` (kept verbatim from 003 — small, self-contained). | unchanged from 003 |
| `include/uapi/linux/netfilter/xt_QOSMARK.h`, `xt_QOSCONNMARK.h` | UAPI for the QOSMARK extensions. Verbatim from 003. | unchanged |
| `net/netfilter/xt_qosmark.c`, `xt_qosconnmark.c` | The xt match/target. Verbatim from 003. | unchanged |

### 4.2 What gets DELETED vs `003-ask-kernel-hooks.patch`

| 003 patch artefact | Why deleted in ASK 2.0 |
|---------------------|-------------------------|
| `net/netfilter/comcerto_fp_netfilter.c` (new file, ~600 lines) | Replaced by mainline **`nf_flow_table`** (`flowtable`). `ask_bridge` populates a flowtable; the kernel does the bridge-fast-path. This collapses a NXP-bespoke 600-line patched file into ~50 lines of clean kernel-side glue. |
| All edits to `net/bridge/br_*.c` (8 files in 003) | Same — `flowtable` already has the bridge hooks mainline-side. |
| `net/xfrm/ipsec_flow.{c,h}` (new files, ~400 lines combined) | Replaced by an XFRM_OFFLOAD provider in `ask_dpa_ipsec.c` (OOT module). |
| All edits to `drivers/net/ppp/ppp_generic.c`, `pppoe.c`, `drivers/net/usb/usbnet.c` | These existed because the legacy hook needed each subsystem to call into `comcerto_fp_*`. With `flowtable` doing bridge work and `xfrm_user` doing IPsec, ppp/pppoe/usbnet need no patches. |
| Edits to `include/linux/{if_bridge,netdevice,poll,skbuff}.h`, `net/{ip,ip6_tunnel,netns/xfrm}.h` | Mostly extra fields used by the deleted `comcerto_fp_*`. Not needed once that file is gone. |
| `drivers/crypto/caam/pdb.h::__attribute__((packed))` additions | The ASK 1.x reason-of-record was ABI compat with NXP USDPAA userspace; ASK 2.0 uses `xfrm_user` pdb structures, so packing is irrelevant. |
| `Makefile :: KBUILD_EXTMOD` `SUBDIRS=` shim | Legacy `make SUBDIRS=...` syntax. Drop. |
| `tools/perf/.gitignore` edit | Unrelated drift. Drop. |

**Net:** ~5800 lines → estimated ~600 lines. 64 files → estimated 12 files. Most of the deletion is the bridge fast-path which mainline already has.

### 4.3 What stays out-of-tree

`ask.ko`, `ask_bridge.ko`, `xt_QOSMARK`/`xt_QOSCONNMARK` are all OOT-buildable. They depend only on the in-tree hooks patch and the SDK FMan API. (Long-term: in-tree the modules under `drivers/net/dpaa1-ask/` and submit upstream once the SDK→mainline FMan story is sorted. Out of v1 scope.)

---

## 5. Userspace — `/dev/cdx_ctrl` ABI and the policy XML schemas

The single hardest constraint on the rewrite: **existing `cdx_cfg.xml` and `cdx_pcd.xml` files must work unchanged.** Operators may have customised these per deployment; we cannot break their config.

### 5.1 `/dev/cdx_ctrl` ioctl set (pinned)

From `ASK/dpa_app/dpa.c` and the SDK `cdx_ioctl.h`. The ASK 2.0 UAPI header `include/uapi/linux/ask/cdx_ctrl.h` must keep these constant values:

* `CDX_CTRL_DPA_SET_PARAMS` (set FMan port/dist/table layout from compiled FMC model — called by `ask-load` as the last step of policy load).
* Per-flow add/del/lookup ioctls for each of the 16 table types (`IPV4_UDP_TABLE`, `IPV4_TCP_TABLE`, …, `IPV6_REASSM_TABLE`).
* IPsec SA add/del/replace ioctls (used by `ask_dpa_ipsec.c` from the kernel side; userspace `askd` proxies xfrm_user events to these).
* Multicast group add/del.
* Statistics fetch (per-table per-key byte/frame counters, used by `askd`'s `show flows` CLI).
* Exception-rate-limiter tuning (`CDX_EXPT_*_RATELIM`, `CDX_EXPT_RATELIM_MODE`).

The exact struct layout for `cdx_ctrl_set_dpa_params`, `cdx_fman_info`, `cdx_port_info`, `cdx_dist_info`, `cdx_ipr_info`, `table_info` is pinned by `ASK/dpa_app/dpa.c` lines 32–110. Translate verbatim into `include/uapi/linux/ask/cdx_ctrl.h`.

### 5.2 `cdx_cfg.xml` schema (port-to-policy map)

Pinned by `ASK/config/gateway-dk/cdx_cfg.xml`:

```xml
<cfgdata>
  <config>
    <engine name="fm0">
      <port type="1G|10G|OFFLINE" number="N" policy="<policy_name>" portid="N"/>
      ...
    </engine>
  </config>
</cfgdata>
```

Type values and policy refs: `1G | 10G | OFFLINE`. `portid` 1–9 (1=eth2, 4=eth0, 5=eth1, 6=eth3, 7=eth4, 8=OP1, 9=OP2). The Mono Gateway DK config in this repo is the canonical example; `ask-load` must parse it bit-identically.

### 5.3 `cdx_pcd.xml` schema (FMan PCD policy)

Pinned by `ASK/dpa_app/files/etc/cdx_pcd.xml`. The FMC library already parses this into `struct fmc_model_t`; the rewrite of `dpa_app` reuses the **same** FMC library (with the existing `01-mono-ask-extensions.patch` re-applied to upstream `nxp-qoriq/fmc`). The schema has three top-level sections:

* `<classification name="cdx_*_cc">` — a CC node (hash-table, key-size, mask).
* `<distribution name="cdx_*_dist">` — a KeyGen scheme (protocol, key fields, output FQID range, action — always "classification" pointing at a `cdx_*_cc`).
* `<policy name="cdx_ethport_N_policy">` — ordered list of distribution refs applied to a port. The `<dist_order>` decides match precedence at the port (ESP first, then UDP/TCP, then multicast, then 3-tuples, then PPPoE, finally generic ethernet — see line 218 in the file).

The ASK 2.0 rewrite **does not** re-implement the FMC compiler. It uses `fmc_compile()` from the patched upstream `fmc`. The single thing `ask-load` adds is the policy schema fixups for things FMC doesn't know (the `t_FmPcdHashTableParams::table_type` and IP-reassembly fields — see `set_reassembly_params()` in `dpa.c`), and the `CDX_CTRL_DPA_SET_PARAMS` ioctl call at the end.

### 5.4 `fastforward` config (ALG exclusion list)

Pinned by `ASK/config/fastforward`. UCI-style key-value, three default entries (FTP, SIP, PPTP). `askd` parses this at startup; flows on the listed ports/protocols are tagged "do-not-offload" in the conntrack→FCI handler.

### 5.5 NETLINK_KEY=32 (FCI) message format

The FCI channel is netlink protocol number 32 (NETLINK_KEY in `include/uapi/linux/netlink.h`). Multiplexed with PF_KEYv2 — see the `pfkey_proto[]` table in `net/key/af_key.c` and the `ask_fci_nlkey` registration. Message format:

```
struct fci_msg {
    __u16  cmd;        /* FCI_CMD_FLOW_ADD | FLOW_DEL | SA_ADD | … */
    __u16  flags;
    __u32  seq;
    __u32  table_type; /* one of the 16 IPV4_UDP_TABLE etc. */
    __u8   key[64];    /* table-specific, length = table->key_size */
    __u8   mask[64];
    /* TLV payload: action, next-hop MAC, egress portid, FQID, … */
};
```

Re-derived from `ASK/cmm/src/fpp.h` and `ASK/fci/lib/include/libfci.h`. The rewrite preserves byte layout; existing `cmm` (and the new `askd`) speak the same protocol. (We do not migrate to `nfnetlink` because libfci is widely linked into vendor tools; the cost-benefit doesn't favour migration.)

---

## 6. Userspace daemons — `ask-load` and `askd`

### 6.1 `ask-load` (replaces `dpa_app`)

* ~1500 LOC C (vs. `ASK/dpa_app/dpa.c` ~600 LOC + supporting files).
* Reads `/etc/cdx_cfg.xml`, `/etc/cdx_pcd.xml`, `/etc/cdx_sp.xml` (soft-parser), `/etc/fmc/config/hxs_pdl_v3.xml` (parser PDL).
* Calls `fmc_compile()` from libfmc (patched upstream `nxp-qoriq/fmc`), then `fmc_execute()` — same control flow as `dpa_app::dpa_init()`.
* Calls `FM_PCD_Open()` / `FM_PCD_Disable()` per FMan instance to put PCD into the configurable state before `fmc_execute()` runs `FM_PCD_SetAdvancedOffloadSupport()` (this dance is pinned by `set_fm_adv_options()` in `ASK/dpa_app/dpa.c`).
* Walks the compiled `fmc_model_t`, builds `struct cdx_ctrl_set_dpa_params`, calls `ioctl(/dev/cdx_ctrl, CDX_CTRL_DPA_SET_PARAMS, &params)`.
* Sets default exception-rate-limiter limits (`CDX_EXPT_ETH_DEFA_LIMIT = 195312` ≈ 100 Mbps; pinned by `dpa.c::set_exptrate_policer_defaults`).
* Spawned **once** by `ask.ko` at insmod via `call_usermodehelper(UMH_WAIT_PROC)` — there is no long-running `dpa_app`. After it exits, the FMan policy is live in hardware.

### 6.2 `askd` (replaces `cmm`)

* ~8 kLOC C (vs. `ASK/cmm/` ~25 kLOC across 60+ files; modern code with libmnl, GLib event loop, structured logging — the 3× LOC reduction is real).
* Inputs:
  * `nf_conntrack_event` via netlink (libnetfilter_conntrack with re-derived async/heap-buffer patches).
  * Bridge FDB events via `RTM_NEWNEIGH` rtnetlink (replaces `auto_bridge` daemon-side; the kernel module still does flowtable wiring).
  * xfrm SA events via `xfrm_user` rtnetlink.
  * `/proc/net/route`, `/proc/net/route6`, neighbor table for next-hop resolution.
  * `/etc/config/fastforward` for ALG-exclusion port/proto list.
  * libcli-based interactive CLI on a unix socket (`/var/run/askd.sock`).
* Outputs:
  * Flow ADD/DEL via `libask_fci` (NETLINK_KEY=32) → `ask_fci.ko` → `ask.ko::ask_cmdhandler.c` → CC table updates.
  * Per-flow stats periodically scraped from `/dev/cdx_ctrl` and exposed via `show flows` CLI.
* Decision engine:
  1. New conntrack EST event → look up next-hop in route + neighbor → if reachable via offload-eligible port (1G or 10G, not loopback/tun/wifi-software-bridge) and protocol is not in `fastforward` list → push CC entry.
  2. CT del event → drop CC entry.
  3. xfrm SA add → push IPsec SA to OFFLINE port OP1 via FCI `SA_ADD`.
  4. Bridge FDB add → push L2 entry to `cdx_ethernet` table.
  5. Periodic timer (1 s): scrape `/dev/cdx_ctrl` per-flow counters, refresh conntrack last-used so software side doesn't time out hardware-active flows ("bytes-back" — exactly what `cmm/src/conntrack.c` does today).
* No more `module_icc.c`/`module_expt.c` separation: one daemon, one event loop. The legacy modules existed for ICC asymmetric SoC support (Comcerto C2K dual-core stuff, irrelevant on LS1046A).

### 6.3 systemd integration

```
/lib/systemd/system/ask-modules-load.service     # oneshot: modprobe in order
/lib/systemd/system/askd.service                 # was cmm.service
/etc/modules-load.d/ask-modules.conf             # was ASK/config/ask-modules.conf
/etc/config/fastforward                          # unchanged from ASK/config/fastforward
/etc/cdx_cfg.xml                                 # unchanged from ASK/config/gateway-dk/
/etc/cdx_pcd.xml                                 # unchanged from ASK/dpa_app/files/etc/
```

`askd.service` Unit:
* `After=network.target ask-modules-load.service systemd-sysctl.service`
* `Requires=ask-modules-load.service`  ← propagation, not Wants= — see Chain-1
* `ConditionPathExists=/dev/cdx_ctrl`  ← microcode gate
* `ExecStart=/usr/bin/askd -f /etc/config/fastforward -n 131072`
* `Restart=on-failure RestartSec=5`

Mirrors `ASK/config/cmm.service` line for line; only the binary name and the daemon implementation change.

---

## 7. The 9 lib patches — re-derived against current upstream

Each one is a small, semantically minimal patch. All are re-authored against current upstream (not copied from `ASK/`); the only commitment is **same struct/enum byte layout** so binaries built against patched headers and binaries built against unpatched headers cannot be ABI-mixed (this is the Chain-2 silent-corruption failure mode).

| # | Upstream | Reason | Re-derived patch path | Notes |
|---|----------|--------|------------------------|-------|
| 1 | `github.com/nxp-qoriq/fmlib` | Add `t_FmPcdKgSchemeParams::shared`, `t_FmPcdHashTableParams::{table_type,timeout_val,timeout_fqid,max_frags,min_frag_size,max_sessions}`, IP-reassembly enums | `data/ask-userspace/fmlib/patches/01-ask2-extensions.patch` | Built by `bin/ci-build-fmlib.sh`. Bit-identical struct layout vs `ASK/patches/fmlib/01-mono-ask-extensions.patch`. |
| 2 | `github.com/nxp-qoriq/fmc` | Port-ID output, shared scheme/CC node replication, PPPoE field fix, libxml2 2.13+ compat | `data/ask-userspace/fmc/patches/01-ask2-extensions.patch` | Built by `bin/ci-build-fmc.sh`. |
| 3 | `libnetfilter-conntrack` (Debian/upstream) | Fast-path info attributes (`CTA_FP_INFO_*`) + QOSCONNMARK accessors | `data/ask-userspace/libnetfilter-conntrack/patches/01-ask2-extensions.patch` | Used by `askd`. |
| 4 | `libnfnetlink` | Non-blocking socket + heap-buffer mode for burst event handling | `data/ask-userspace/libnfnetlink/patches/01-ask2-async-heap.patch` | Used by `askd`. |
| 5 | `iptables` | QOSMARK / QOSCONNMARK target+match userspace half | upstream patch series, parked under `data/ask-userspace/iptables/` | Mirrors kernel-side `xt_QOSMARK.ko` / `xt_QOSCONNMARK.ko`. |
| 6 | `iproute2` | EtherIP + 4RD (4over6 RD) tunnel types | `data/ask-userspace/iproute2/patches/01-etherip-4rd.patch` | The kernel side is supplied by mainline `ip6_tunnel.c` (NPT support is in mainline already in 6.18). |
| 7 | `ppp` | Tunnel ifindex propagation to PPP daemons (used so the offload engine can rewrite L2 on PPP-encapped flows) | `data/ask-userspace/ppp/patches/01-ask2-ifindex.patch` | |
| 8 | `rp-pppoe` | CMM (now ASKD) relay mode | `data/ask-userspace/rp-pppoe/patches/01-ask2-relay.patch` | Renamed sysfs hook from `/sys/class/cmm` → `/sys/class/askd` (compat symlink kept). |
| 9 | `accel-ppp-ng` | (NEW vs. legacy ASK 1.x) IPoE/L2TP integration with ASKD for PPPoE-over-bridge offload | `data/ask-userspace/accel-ppp-ng/patches/01-ask2-bridge-handoff.patch` | Optional; only built if FLAVOR=ask + accel-ppp-ng is enabled. |

Re-derivation procedure (per `AGENTS.md` "Patches are applied with `git apply --3way`" rule):

1. Clone upstream at the version pinned in CI (`bin/ci-build-fmlib.sh` already pins `lf-6.18.2-1.0.0` for fmlib/fmc — keep that tag).
2. Apply the corresponding `ASK/patches/{fmlib,fmc}/01-mono-ask-extensions.patch` as `git am` to a scratch branch, tag baseline.
3. Manually re-author the same change with cleaner comments and verify struct sizes with `pahole`/`offsetof()` static-asserts in `dpa_app` → `ask-load`.
4. `git diff --cached > $REPO/data/ask-userspace/<lib>/patches/01-ask2-extensions.patch`.
5. `git apply --3way --check` from CI to confirm.
6. The `pahole`-comparable static-assert is the **Chain-2 oracle**: if `sizeof(t_FmPcdKgSchemeParams)` differs between `ask-load`'s view and the linked libfm.a's view, build fails before runtime SIGSEGV.

---

## 8. Test oracles — the two-chain failure model

This is informative for the rewrite (test it against these, on real hardware, before declaring v1).

### 8.1 Chain 1 — kernel-side

**Symptom (ASK 1.x today):** `cmm.service` failed (`status=255`).
**Root cause:** `NETLINK_KEY=32` not registered → `ask_fci_nlkey` / `ask_fci` returns `EPROTONOSUPPORT` → CMM/ASKD opens the FCI socket and immediately `connect()` fails.
**Diagnose:** `/proc/config.gz | grep CONFIG_NET_KEY`, `/proc/net/netlink | awk '$1 == "32"'`, `lsmod | grep ask_fci`.
**Fix on FLAVOR=ask:** `CONFIG_NET_KEY=y` + `CONFIG_ASK=y` (or `=m` with `ask-modules-load.service` ordering). The ASK 2.0 rewrite must **fail loudly** at `ask.ko` insmod if NET_KEY isn't built in (`pr_err_once` then `return -EPROTONOSUPPORT` from `ask_fci_init`).
**Oracle:** integration test boots, asserts `awk '$1 == "32" {found=1} END {exit !found}' /proc/net/netlink`.

### 8.2 Chain 2 — userspace-side

**Symptoms (ASK 1.x today):** `dpa_app rc=65280`, `cdx_module_init::start_dpa_app failed rc 11`, `cdx_create_fragment_bufpool::failed to locate eth bman pool`, `cdx_module_init::dpa_ipsec start failed`. SIGSEGV in `__memset_aarch64` DC ZVA loop (C++ destructor cascade in `dpa_app::main`).
**Root cause options:**
* **(a) MURAM exhaustion** — too-large `cdx_pcd.xml` + reassembly tables + parser config don't fit. FMC's `fmc_execute` returns failure; `dpa_app` segfaults later because the FMC model is in an inconsistent state.
* **(b) Patched-struct ABI mismatch** — `dpa_app` compiled against patched fmlib headers (`t_FmPcdHashTableParams` extended) linked against an un-patched `libfm.a`. `dpa.c` line 704 uses `sizeof(t_FmPcdHashTableParams)` as a byte-loop bound; the loop now overruns the actual library struct, corrupting the heap.

**Fix today:** `bin/ci-build-fmlib.sh` + `bin/ci-build-fmc.sh` rebuild from patched upstream in CI; prebuilt archives in `data/ask-userspace/{fmlib,fmc}/lib*.a` are emergency fallback only.

**Oracle for ASK 2.0:**
* `ask-load` carries **static asserts** at compile time:
  ```c
  _Static_assert(sizeof(t_FmPcdKgSchemeParams) == 0x???, "fmlib ABI mismatch");
  _Static_assert(sizeof(t_FmPcdHashTableParams) == 0x???, "fmlib ABI mismatch");
  ```
  If the linker pulls in the wrong libfm, the build fails — never the runtime.
* `ask.ko` reports MURAM allocation per-table in `/sys/kernel/debug/ask/muram` — operators see exhaustion before the SIGSEGV.
* Integration test pushes a deliberately oversized `cdx_pcd.xml` (e.g. mask `0xffff` on every table) and asserts `ask-load` returns non-zero **and** `dmesg | grep -F 'MURAM exhausted'` is present.

These two chains have been the **only** ASK production failure modes observed across the absorbed kernel-build history (frozen at FLAVOR=ask import). ASK 2.0 passes v1 acceptance only if both chains are reproducible-by-construction (oracle exists) and recoverable-by-construction (clean error messages, not SIGSEGV).

---

## 9. Build-and-CI flow (FLAVOR=ask)

Single-repo, single-workflow model from `AGENTS.md` "Single-repo absorption (May 2026)" stays:

* Trigger: `gh workflow run "VyOS LS1046A build (self-hosted)" -F flavor=ask`.
* Kernel staging: `bin/ci-stage-kernel.sh` → `bin/ci-consume-ask-kernel.sh` (existing; pulls 6.18.x kernel + applies the SDK + the new `200-ask2-hooks.patch`).
* OOT modules: `bin/ci-build-ask-userspace.sh` (existing) extended to compile `ask.ko` + `ask_bridge.ko` from `kernel/flavors/ask/oot-modules/ask/` + `ask_bridge/`. Sign with `$KSRC/scripts/sign-file sha512 $KSRC/certs/signing_key.pem $KSRC/certs/signing_key.x509` per `AGENTS.md` `MODULE_SIG_FORCE=y` rule.
* Userspace libs: `bin/ci-build-fmlib.sh`, `bin/ci-build-fmc.sh` rebuilt against re-derived patches.
* Userspace daemons: new scripts `bin/ci-build-ask-load.sh` + `bin/ci-build-askd.sh` build the clean-room replacements.
* ISO assembly: `bin/ci-build-iso.sh` + chroot hook `data/hooks/97-ask-userspace.chroot` install askd, ask-load, libask_fci, the systemd units, and `/etc/cdx_*.xml`.
* Patch-health: `kernel/common/scripts/patch-health.sh --flavor ask` runs across the 16 absorbed patches; the new `200-ask2-hooks.patch` adds one entry → invariant becomes `Pass: 18 Fail: 0 0 SDK conflicts`.
* Verification: `bin/ci-verify-ask-iso.sh` (existing) extended to check that the ISO has `/usr/sbin/ask-load`, `/usr/bin/askd`, signed `ask.ko` and `ask_bridge.ko`.

`AGENTS.md` ASK-edit marker discipline does **not** apply to the ASK 2.0 modules (no NXP SDK lineage). It does still apply to anything that touches `kernel/flavors/ask/sdk-sources/`. CI audit (`grep -rn 'ASK-edit' kernel/flavors/ask/sdk-sources/` count must stay 35) is unchanged.

---

## 10. Migration path — how we get there from today's ASK 1.x

Five phases, each independently verifiable on hardware:

| Phase | Deliverable | Acceptance test |
|-------|-------------|------------------|
| **P1** | `200-ask2-hooks.patch` written, replaces `003-ask-kernel-hooks.patch`. Existing OOT `cdx`/`auto_bridge` rebuilt against it (no rewrite yet). | Kernel boots; `cdx.ko` insmods; `cmm` runs; `iperf3` over offloaded flow shows ≥4 Gbps (matching today's measured ASK SW flow offload baseline). |
| **P2** | `ask.ko` clean-room — replaces `cdx.ko`. UAPI byte-compatible. Old `cmm` daemon unchanged. | Phase-1 test passes against new module. `dmesg \| grep ask` shows the new module banner. `cdx.ko` is uninstalled from the rootfs. |
| **P3** | `ask_bridge.ko` clean-room replaces `auto_bridge.ko`, backed by mainline `flowtable`. | Bridge two RJ45 ports; iperf3 single flow saturates 1G; offloaded flow visible in `bridge -s fdb show` AND in `cat /proc/net/nf_flowtable`. |
| **P4** | `ask-load` and `askd` clean-room replace `dpa_app` and `cmm`. `libask_fci` replaces `libfci`. Existing patched fmlib/fmc reused. | `cmm` and `dpa_app` removed from rootfs. Existing `cdx_cfg.xml`/`cdx_pcd.xml` unchanged. Phase-1 test passes. CLI `show flows` returns same data as legacy CMM CLI. |
| **P5** | XFRM_OFFLOAD provider replaces `INET_IPSEC_OFFLOAD=y` path. `CONFIG_INET_IPSEC_OFFLOAD=n` becomes safe. | IPsec tunnel comes up; AES-GCM-128 1500 B throughput ≥ today's measurement; SA replace under load doesn't drop frames. |

After P5: `kernel/flavors/ask/sdk-sources/` still carries the SDK FMan/QMan/BMan drivers (the FMan v3L kernel driver itself is not part of this rewrite). All 35 ASK-edit markers there are unchanged. ASK 2.0 shrinks the **proprietary** ASK SDK kernel-hooks-and-modules surface from "5800-line patch + 15-file OOT cdx + auto_bridge" to "~600-line in-tree patch + ~3000-line clean-room OOT module pair", and the userspace from "25 kLOC cmm + 600-LOC dpa_app + libfci" to "~10 kLOC askd + ~1500-LOC ask-load + libask_fci".

Total calendar: ~9 months for two engineers (one kernel, one userspace), assuming P1 lands first as it unblocks the smaller-patch story and lets us measure the legacy stack against the new kernel hooks.

---

## 11. Open questions

1. ~~**ucode v210.10.1 redistribution.**~~ **RESOLVED.** The microcode is on the board's SPI NOR (`mtd4`); U-Boot loads it and patches the DTB `fsl,fman-firmware` node before kernel handoff. The U-Boot image we ship already redistributes the binary blob (legal sign-off was completed in the now-archived kernel build repo's release cycle). No `request_firmware()`, no `/lib/firmware/` file, no kernel licensing surface. ASK 2.0 inherits the existing redistribution path unchanged.
2. ~~**Should `ask_fci` be in-tree or OOT?**~~ **RESOLVED — OOT, signed, same pattern as `ask.ko` and `ask_bridge.ko`.** Lives at `kernel/flavors/ask/oot-modules/ask_fci/`, built by `bin/ci-build-ask-userspace.sh` against the in-tree hooks patch's exported symbols (`net/key/af_key.c::pfkey_register`, `net/netfilter/nf_conntrack_*` notifier hooks). Signed by `$KSRC/scripts/sign-file sha512 …` per `AGENTS.md` `MODULE_SIG_FORCE=y` rule. The in-tree-built-in option was rejected because (a) it forces a `200-ask2-hooks.patch` edit per kernel rebase for what is logically a leaf module, (b) it diverges from the discipline `ask.ko`/`ask_bridge.ko` follow, (c) it would block `ask_fci` from being unloaded for diagnostics. Module load order in `/etc/modules-load.d/ask-modules.conf` (already shown in §3.4) places `ask_fci` last, so `ask.ko` registers `/dev/cdx_ctrl` and the FCI handler chain on its own; `ask_fci.ko` then attaches as the NETLINK_KEY=32 protocol handler via `pfkey_register(NETLINK_KEY, …)`.
3. **Drop NETLINK_KEY=32 in v2?** `nfnetlink` would be more idiomatic. But that breaks libfci's ABI for any external vendor tool that links it. Decision: keep NETLINK_KEY=32 forever for compat. Add an `nfnetlink` parallel surface in v2 if anyone asks.
4. **Module name `ask` vs `ask2` vs `dpaa1-ask`.** Avoiding collision with mainline `drivers/cdx/`. Working name `ask` (matches the `kernel/flavors/ask/` directory and the FLAVOR=ask brand; `CONFIG_ASK` does not collide with anything in mainline 6.18). Open to `dpaa1-ask` if upstream submission ever happens. `ask2` was rejected because the `/2` suffix would leak the version into every `lsmod` line and every `dmesg` banner forever.
5. **CC entry eviction policy.** ASK 1.x uses LRU per-CC-node. Mainline `flowtable` uses GC at fixed intervals. We need to reproduce LRU for the CC tables (the flow keep-alive in `askd` depends on it) AND honour the mainline flowtable GC for the bridge path. Two policies, one per offload pathway.
6. **Per-OFFLINE-port BMan pool sizing.** Today the SDK hard-codes 2048 buffers per OP. The Mono Gateway DK has 2 OPs → 4096 buffers reserved out of the BMan pool. If we add an OP (e.g. for traffic-shaping post-process), we hit BMan exhaustion. Make the count `cdx_cfg.xml`-driven.
7. **`vwd_fast_path_enable` sysfs hook in `cmm.service`.** The legacy `ExecStartPre` writes `/sys/class/vwd/vwd0/vwd_fast_path_enable`. `vwd` is a wireless module not present on this board. The hook is dead code on Mono Gateway DK. Drop in `askd.service`.

---

**End of v0.5 (ASK 2.0 rename). Out of scope for any future revision of this spec:** rewriting `fsl_dpa`, `fsl_dpaa_mac`, `fsl_qbman`, mainline FMan, AF_XDP, tc-flower offload, CAAM xfrm, or DPDK PMD. Those belong to **default-flavor** plans elsewhere in this repo.