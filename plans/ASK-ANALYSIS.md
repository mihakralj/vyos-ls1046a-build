# NXP ASK (Application Solutions Kit) — Analysis for VPP Alternative

> **Status (2026-04-03):** 📋 **ANALYSIS ONLY.** Evaluating ASK as a potential replacement
> or complement to VPP for high-speed forwarding on the Mono Gateway LS1046A.
> Source: https://github.com/we-are-mono/ASK

---

## 1. What ASK Is

ASK provides **FMan hardware flow offloading** — the FMan silicon's built-in packet
classification engine forwards matching flows entirely in hardware, bypassing both
the Linux network stack AND any userspace data plane (VPP/DPDK).

```
WITHOUT ASK (current — all packets through Linux kernel):

  Wire → FMan → BMan → QMan → fsl_dpa → Linux IP stack → fsl_dpa → QMan → FMan → Wire
                                    ↑
                              Every packet traverses
                              the full kernel stack

WITH ASK (hardware offload for established flows):

  Wire → FMan classifier ──┬── Match: FMan forwards directly → Wire
                            │         (ZERO software involvement)
                            │
                            └── No match: → BMan → QMan → fsl_dpa → Linux → ...
                                           (new connections, control plane, management)
```

This is **hardware-level forwarding** — no CPU cycles consumed for offloaded flows.
Theoretical throughput: full FMan wire-rate (10G per interface, aggregate limited
by FMan internal bandwidth).

## 2. ASK Components

| Component | Type | LOC (est.) | Purpose |
|-----------|------|-----------|---------|
| **CDX** | Kernel module | ~15K | Core fast-path engine. Programs FMan hardware flow tables. Manages IPsec offload, QoS, CEETM |
| **CMM** | Userspace daemon | ~8K | Monitors `nf_conntrack`, offloads eligible flows to CDX. L3/L4/bridge/IPsec/PPPoE |
| **dpa_app** | Userspace tool | ~1K | Programs FMan classification rules via FMC library + XML policy files |
| **FCI** | Kernel module | ~2K | Communication channel between CDX kernel module and CMM |
| **auto_bridge** | Kernel module | ~1K | L2 bridge flow detection, notifies CDX of offloadable bridge flows |
| **libfci** | Userspace library | ~1K | CMM↔FCI communication API |
| **Kernel patch** | Patch | ~2K | Adds `CONFIG_CPE_FAST_PATH` hooks to `dpaa_eth`, CEETM, IPsec, conntrack |

**External dependencies** (not in ASK repo):
- **FMC** (FMan Configuration tool) — NXP userspace tool for programming FMan classifier
- **fmlib** — FMan userspace library
- **ASK-enabled FMan microcode v210.10.1** — proprietary NXP binary loaded by U-Boot

## 3. How ASK Avoids RC#31

ASK does not use DPDK at all. It operates entirely through the kernel's `fsl_dpa` driver:

| Aspect | DPDK DPAA PMD | ASK/CDX |
|--------|--------------|---------|
| BMan control | DPDK takes over ALL buffer pools | Kernel retains ALL pools; CDX reads pool IDs from kernel |
| QMan control | DPDK initializes ALL frame queues | Kernel retains ALL FQs; CDX uses kernel's existing FQs |
| FMan control | DPDK accesses FMan CCSR via `/dev/mem` | CDX programs via FMC library (ioctl to kernel fman driver) |
| Driver | fsl_dpa UNBOUND, DPDK PMD replaces it | fsl_dpa STAYS BOUND, CDX hooks into it |
| RC#31 | 🔴 Global bus init corrupts everything | ✅ No bus-level init — kernel stays in control |

**The fundamental difference**: ASK programs FMan's hardware classifier to create a
"fast path" for specific flows, while the kernel's `fsl_dpa` driver remains the owner
of all BMan/QMan/FMan resources. CDX just teaches FMan which packets to forward in
hardware vs which to deliver to the kernel.

## 4. ASK vs VPP — Feature Comparison

| Feature | VPP (AF_XDP) | VPP (All-DPDK+LCP) | ASK/CDX |
|---------|-------------|-------------------|---------|
| **Forwarding throughput** | ~3.5 Gbps | ~9.4 Gbps | ~9.4 Gbps (FMan hardware) |
| **Forwarding path** | Software (VPP graph) | Software (VPP graph) | Hardware (FMan silicon) |
| **CPU usage for forwarding** | High (poll-mode) | High (poll-mode) | **Zero** (hardware) |
| **Management port safety** | ✅ Kernel (safe) | ⚠️ Through VPP LCP TAPs | ✅ Kernel (safe) |
| **Thermal impact** | ⚠️ poll-sleep-usec needed | ⚠️ poll-sleep-usec needed | ✅ None (no poll-mode) |
| **L2 bridge offload** | ❌ Not in VPP graph | ❌ Would need VPP L2 config | ✅ auto_bridge module |
| **L3 IPv4 forwarding** | ✅ VPP FIB | ✅ VPP FIB | ✅ CDX via conntrack |
| **L3 IPv6 forwarding** | ✅ VPP FIB | ✅ VPP FIB | ✅ CDX via conntrack |
| **NAT offload** | ⚠️ VPP NAT plugin | ⚠️ VPP NAT plugin | ✅ CMM tracks NAT conntrack |
| **IPsec offload** | ❌ Not supported on DPAA1 | ❌ Not supported on DPAA1 | ✅ CAAM + FMan integration |
| **Firewall/ACL** | ✅ VPP ACL plugin | ✅ VPP ACL plugin | ⚠️ FMan classifier rules (limited) |
| **QoS/Traffic shaping** | ⚠️ VPP policer | ⚠️ VPP policer | ✅ CEETM hardware QoS |
| **PPPoE offload** | ❌ | ❌ | ✅ Built-in |
| **VyOS CLI integration** | ✅ `set vpp settings` | ✅ `set vpp settings` | ❌ Needs new VyOS integration |
| **First packet latency** | Low (immediate VPP graph) | Low (immediate VPP graph) | Higher (conntrack must establish first) |
| **Stateless forwarding** | ✅ Works for any packet | ✅ Works for any packet | ❌ Only established connections |
| **New connection handling** | VPP or Linux | VPP or Linux | **Linux only** (first packets always through kernel) |
| **Microcode dependency** | None | None | 🔴 Proprietary NXP FMan microcode v210.10.1 |
| **Kernel patch required** | Minimal (AF_XDP exists) | Our USDPAA patches | ~2K lines `CONFIG_CPE_FAST_PATH` |
| **Boot networking** | ✅ Always (kernel) | ⚠️ After VPP starts | ✅ Always (kernel) |

## 5. Critical Evaluation — Can ASK Replace VPP?

### What ASK Does Better

1. **Zero CPU forwarding**: Offloaded flows consume no CPU cycles. VPP poll-mode
   uses 100% of at least one core continuously.

2. **No thermal issues**: No poll-mode = no thermal shutdown risk. The fan/thermal
   management complexity goes away entirely for forwarding workloads.

3. **Kernel stays in control**: All interfaces remain as kernel netdevs. VyOS CLI
   for interfaces, firewall, routing, SSH — everything works normally.

4. **IPsec in hardware**: CAAM crypto engine + FMan offload = wire-speed IPsec.
   VPP on DPAA1 has no IPsec offload path.

5. **NAT offload via conntrack**: CMM watches `nf_conntrack` and offloads NAT'd
   connections. VPP NAT is software-only.

6. **No RC#31**: Fundamentally impossible — ASK works WITH the kernel driver.

### What ASK Does Worse

1. **Only established connections**: ASK offloads flows AFTER they're established
   in Linux conntrack. First packet(s) of every connection go through the full
   kernel stack. For short-lived connections (DNS, HTTP/3), offload benefit is limited.

2. **Proprietary microcode dependency**: ASK requires NXP FMan microcode v210.10.1
   (the "ASK-enabled" variant). This is a proprietary binary not included in the
   repository. Without it, CDX cannot initialize and hardware offloading is unavailable.
   **We would need to verify we have or can obtain this microcode.**

3. **No VyOS CLI integration**: VPP has `set vpp settings` in VyOS. ASK has no
   VyOS integration — CMM/CDX are standalone daemons. Would need custom VyOS
   CLI nodes (`set system offload ...`?) and Python conf_mode scripts.

4. **Kernel patch compatibility**: The ASK patch targets kernel 6.12. Our VyOS
   kernel is 6.6. The patch would need porting — the `dpaa_eth` hooks API may
   differ between 6.6 and 6.12. The 5.4 patch also exists as reference.

5. **FMC/fmlib dependencies**: `dpa_app` needs FMC (FMan Configuration) tool and
   fmlib library. These are separate NXP packages that must be cross-compiled for
   ARM64 and integrated into the build.

6. **Less flexible than VPP**: VPP can do arbitrary packet processing (custom graphs,
   GTP/VXLAN tunnels, segment routing, etc.). ASK is limited to what FMan's hardware
   classifier supports: L2/L3 forwarding, NAT, IPsec, QoS, PPPoE.

7. **XML-based configuration**: FMan classification rules are defined in XML policy
   files (`cdx_cfg.xml`, `cdx_pcd.xml`). Changes require regenerating and reloading
   rules. Not as dynamic as VPP's API.

## 6. The Hybrid Path — ASK + VPP

The most interesting option: **use both**.

```
┌─────────────────────────────────────────────────────────┐
│                    Linux Kernel                          │
│  ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐     │
│  │ eth0  │ │ eth1  │ │ eth2  │ │ eth3  │ │ eth4  │     │  All kernel netdevs (fsl_dpa)
│  └───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘     │
│      │         │         │         │         │           │
│  ┌───▼─────────▼─────────▼─────────▼─────────▼───┐      │
│  │              CDX Fast-Path Engine               │      │  Hardware flow offload
│  │  (conntrack → FMan classifier → wire-speed)     │      │
│  └───┬─────────────────────────────────────────────┘      │
│      │ Flows not offloaded                                │
│  ┌───▼───────────────────────────────────────────┐        │
│  │          Linux IP Stack / VyOS Routing          │        │  Normal routing, firewall
│  └───┬───────────────────────────────────────────┘        │
│      │ VPP AF_XDP for additional processing               │
│  ┌───▼───────────────────────────────────────────┐        │
│  │     VPP (AF_XDP) — complex packet processing    │        │  Optional: custom graphs
│  └─────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

In this model:
- **ASK/CDX handles 90%+ of traffic** in FMan hardware (established L3/L4 flows, NAT, IPsec)
- **Linux kernel handles new connections**, control plane, management (SSH, BGP, OSPF)
- **VPP (optional) handles special workloads** that neither FMan nor Linux do well (DPI, custom classifiers, GTP tunnels, etc.)
- **All interfaces stay as kernel netdevs** — VyOS works perfectly
- **No RC#31** — no DPDK, no bus conflict
- **Zero thermal concern** for standard forwarding (FMan hardware, not poll-mode)
- VPP AF_XDP can be layered on top for specific ports/flows that need software processing

## 7. Implementation Assessment

### What We Need

1. **FMan microcode v210.10.1**: Check if this is already on the Mono Gateway's
   SPI flash, or if we need to obtain and flash it. This is the hard dependency.

2. **Port kernel patch 6.12→6.6**: The `002-mono-gateway-ask-kernel_linux_6_12.patch`
   adds `CONFIG_CPE_FAST_PATH` hooks to `dpaa_eth`. The 5.4 patch exists as reference.
   Our kernel is 6.6 — `dpaa_eth` API is between these two. Estimated effort: ~1-2 days.

3. **Build CDX/FCI/auto_bridge kernel modules**: Out-of-tree modules, need cross-compile
   for ARM64 against our 6.6 kernel headers. The Makefile infrastructure exists in ASK.

4. **Build CMM daemon + dpa_app**: Userspace programs, straightforward cross-compile.
   CMM needs libfci, libnetfilter-conntrack, libnfnetlink (patched versions in ASK).

5. **Build FMC + fmlib**: External NXP tools. Need to find ARM64 cross-compile recipes.
   These may already exist in the NXP LSDK.

6. **Write XML policy files**: `cdx_cfg.xml` and `cdx_pcd.xml` for our specific port
   layout (3x RJ45 1G + 2x SFP+ 10G). ASK repo may have reference configs.

7. **VyOS integration**: Create `set system offload` CLI nodes, service files for
   CMM daemon, module loading. ~200-400 LOC estimated.

### Effort Estimate

| Task | Effort | Risk |
|------|--------|------|
| Verify/obtain ASK FMan microcode | 1 day | 🔴 Blocker if not available |
| Port kernel patch 6.12→6.6 | 1-2 days | Medium (API changes) |
| Cross-compile ASK kernel modules | 1 day | Low |
| Cross-compile CMM + dpa_app + deps | 1-2 days | Medium (fmlib/FMC deps) |
| Write FMan XML policy files | 1 day | Medium (need FMan expertise) |
| VyOS CLI integration | 2-3 days | Low |
| Integration testing | 2-3 days | Medium |
| **Total** | **~1.5-2 weeks** | **Blocked by microcode** |

## 8. Recommendation

### Path Comparison (Updated)

| Path | Throughput | CPU Usage | Thermal | Complexity | Timeline | Blocker |
|------|-----------|-----------|---------|------------|----------|---------|
| **AF_XDP (Phase 1)** | 3.5 Gbps | High (poll) | ⚠️ Needs fan | Low | ✅ Now | None |
| **All-DPDK+LCP** | 9.4 Gbps | High (poll) | ⚠️ Needs fan | Medium | 1-2 weeks | None |
| **ASK/CDX** | 9.4 Gbps | **Zero** | ✅ None | Medium-High | 1.5-2 weeks | 🔴 Microcode |
| **ASK + VPP hybrid** | 9.4 Gbps + software | Minimal | ✅ Mostly none | High | 2-3 weeks | 🔴 Microcode |

### Decision Tree

```
Do we have ASK FMan microcode v210.10.1?
  │
  ├── YES → ASK/CDX is the superior path:
  │         - Wire-speed forwarding with zero CPU
  │         - No thermal issues
  │         - Kernel stays in control (no RC#31)
  │         - IPsec + NAT + QoS in hardware
  │         - VPP optional for advanced use cases
  │
  └── NO → Is it obtainable from NXP/Mono?
            │
            ├── YES (with timeframe) → Pursue ASK, keep AF_XDP as interim
            │
            └── NO → All-DPDK+LCP is the best available path
                     - 9.4 Gbps with VPP poll-mode
                     - Thermal management required
                     - VPP crash = temporary network outage
```

### Immediate Next Step

**Check the Mono Gateway's SPI flash for the FMan microcode version.** On a running device:

```bash
# Check FMan microcode version loaded by U-Boot
cat /sys/class/firmware/fsl_fman-0/firmware_rev 2>/dev/null || \
  dmesg | grep -i "fman.*microcode\|fman.*firmware"

# Check U-Boot env for microcode reference  
fw_printenv | grep -i fman
```

If the ASK-enabled microcode (v210.10.1) is already present, ASK becomes the
clear winner. If not, we need to determine if Mono has access to it from NXP.

---

## 9. Using ASK Primitives to Enable DPAA PMD + VPP (Solving RC#31)

### The Core Problem (Recap)

RC#31: DPDK's `dpaa_bus_probe()` does a **global** BMan/QMan initialization that
overwrites kernel-managed state for ALL ports, killing management interfaces.

### The Key Insight

ASK's CDX module knows how to **program FMan's hardware classifier** to route
traffic from specific ports to specific QMan frame queues. It also knows how to
**create and manage BMan buffer pools** independently of the kernel's default pools.

What if CDX acts as the **traffic splitter at the hardware level**:
- SFP+ traffic → dedicated DPDK buffer pools and frame queues
- RJ45 traffic → kernel's existing buffer pools and frame queues

Then DPDK doesn't need to do global BMan/QMan init at all — CDX has
already prepared isolated resources for it.

### Architecture: CDX as Bus Orchestrator

```
BOOT (T=0 to T=50s):
  All 5 ports owned by kernel via fsl_dpa (normal)

VPP CONFIGURED (T=50s):
  ┌──────────────────────────────────────────────────┐
  │ Step 1: CDX kernel module loaded                  │
  │   - Reads current BMan pool layout from kernel    │
  │   - Creates NEW dedicated BMan pools for DPDK     │
  │     (separate pool IDs, separate buffers)          │
  │   - Creates NEW QMan frame queues for SFP+ ports   │
  │     (separate FQIDs, not overlapping kernel FQIDs) │
  ├──────────────────────────────────────────────────┤
  │ Step 2: dpa_app programs FMan classifier           │
  │   - MAC9 (eth3 SFP+): route RX to DPDK FQs        │
  │   - MAC10 (eth4 SFP+): route RX to DPDK FQs       │
  │   - MAC2/5/6 (RJ45): UNCHANGED, still to kernel FQs│
  │   - FMan now splits traffic at the HARDWARE level   │
  ├──────────────────────────────────────────────────┤
  │ Step 3: Unbind fsl_dpa from SFP+ only              │
  │   - echo dpaa-ethernet.3 > fsl_dpa/unbind          │
  │   - echo dpaa-ethernet.4 > fsl_dpa/unbind          │
  │   - RJ45 ports stay bound to fsl_dpa               │
  ├──────────────────────────────────────────────────┤
  │ Step 4: VPP starts with MODIFIED dpaa_bus           │
  │   - dpaa_bus reads CDX-prepared portal/pool/FQ map  │
  │   - Attaches ONLY to DPDK-dedicated resources       │
  │   - Does NOT touch kernel's BMan pools or QMan FQs  │
  │   - No global init = no RC#31                       │
  └──────────────────────────────────────────────────┘

RESULT:
  eth0/eth1/eth2 (RJ45) → kernel fsl_dpa → Linux IP stack (UNTOUCHED)
  eth3/eth4 (SFP+) → CDX-routed → DPDK pools → VPP (WIRE SPEED)
```

### Which ASK Primitives Are Needed

| Primitive | From | What It Does for Us |
|-----------|------|-------------------|
| **CDX dpaa_eth hooks** | Kernel patch | Exposes BMan pool IDs and QMan FQ creation API to CDX module |
| **BMan pool management** | CDX kernel module | Creates dedicated buffer pools for DPDK, isolated from kernel pools |
| **QMan FQ management** | CDX kernel module | Creates dedicated frame queues for DPDK-bound ports |
| **FMan classifier programming** | dpa_app + FMC | Routes SFP+ MAC traffic to DPDK FQs instead of kernel FQs |
| **FCI interface** | FCI kernel module | Allows VPP startup script to query CDX for prepared resource map |

We do NOT need:
- CMM (connection tracking offload — that's for ASK's own flow offload, not for VPP)
- auto_bridge (L2 bridge detection — not relevant to VPP port split)
- Full ASK flow offload logic (CDX's hash tables, conntrack tracking, etc.)

### What We'd Need to Build/Modify

**1. Minimal CDX "port splitter" module** (~500 LOC estimated):
   - Strip CDX down to ONLY the BMan/QMan resource management code
   - Remove flow offload, IPsec, QoS, conntrack — not needed
   - Keep: BMan pool creation, QMan FQ creation, FMan classifier hooks
   - Export a simple API: `cdx_prepare_dpdk_port(mac_id, &pool_id, &fqid)`

**2. FMan classifier rules** (XML policy + dpa_app):
   - Route MAC9/MAC10 (SFP+) RX traffic to DPDK-dedicated FQs
   - Keep MAC2/5/6 (RJ45) routing unchanged
   - This is the key split — happens in FMan hardware, before any software touches packets

**3. Modified DPDK dpaa_bus** (~200 LOC DPDK patch):
   - Read the CDX-prepared portal/pool/FQ assignments via `/dev/fsl-usdpaa` ioctls
   - Skip global BMan/QMan init (the RC#31 trigger)
   - Only initialize the resources CDX has allocated for DPDK
   - This is a MUCH simpler DPDK patch than full "bus scoping" because CDX handles the complexity

**4. VPP startup integration** (~100 LOC):
   - Before VPP starts: load CDX module, run dpa_app
   - CDX prepares resources, dpa_app programs FMan
   - Then VPP starts with DPDK attaching to pre-prepared resources

### Why This Works Where Raw DPDK Scoping Fails

Option 1 from Section 2 (DPDK bus scoping) requires DPDK to understand BMan/QMan
internals well enough to partition them. That's ~2000 LOC of DPDK changes and deep
QBMan expertise.

The CDX approach flips this: **CDX already understands BMan/QMan** (it's the whole
point of the ASK kernel patch). We leverage that expertise and only ask DPDK to
"use what CDX prepared" rather than "figure out partitioning yourself."

```
Without CDX:
  DPDK must learn: "which pools are kernel's? which FQs are safe to touch?"
  → 2000 LOC DPDK changes, fragile, deep QBMan expertise needed

With CDX:
  CDX says: "here are your pools (IDs 8-11), your FQs (0x400-0x4FF), your portals (6-9)"
  DPDK says: "ok, I'll only use those"
  → 200 LOC DPDK patch, robust, CDX handles the hard part
```

### Effort Estimate (CDX-Assisted DPAA PMD)

| Task | Effort | Risk |
|------|--------|------|
| Verify ASK microcode availability | 1 day | 🔴 Blocker |
| Port CDX kernel hooks to 6.6 (minimal subset) | 2-3 days | Medium |
| Build minimal "port splitter" CDX module | 2-3 days | Medium |
| FMan classifier rules for port split | 1 day | Low (dpa_app reference exists) |
| DPDK dpaa_bus "use prepared resources" patch | 2-3 days | Medium |
| VPP startup integration | 1 day | Low |
| Integration testing on hardware | 2-3 days | Medium |
| **Total** | **~2-3 weeks** | **Blocked by microcode** |

### Comparison: CDX-Assisted vs All-DPDK+LCP

| Dimension | CDX-Assisted (mixed mode) | All-DPDK+LCP |
|-----------|--------------------------|-------------|
| SFP+ throughput | ~9.4 Gbps (DPDK PMD) | ~9.4 Gbps (DPDK PMD) |
| Management safety | ✅ Kernel (always safe) | ⚠️ Through VPP LCP TAPs |
| VPP crash impact | Only SFP+ ports affected | ALL ports down |
| Boot networking | ✅ Always | ⚠️ After VPP starts |
| Thermal | ⚠️ VPP poll-mode on SFP+ | ⚠️ VPP poll-mode on all |
| CPU usage | VPP only for SFP+ traffic | VPP for ALL traffic |
| Complexity | Higher (CDX + DPDK patch) | Lower (standard VPP LCP) |
| Microcode dependency | 🔴 Yes | ✅ No |
| DPDK patch needed | Yes (~200 LOC) | No |

**CDX-assisted is architecturally superior** (management always safe, less CPU, VPP
crash only affects SFP+) but has the microcode dependency and more implementation
complexity. All-DPDK+LCP is simpler but less resilient.

### This Is What the FMD Shim Was Supposed to Be

The `plans/FMD-SHIM-SPEC.md` spec proposed a new kernel module to intercept FMan
configuration. The CDX approach is essentially that concept, but instead of writing
a new module from scratch, we use NXP's proven CDX code that already knows how to
manage FMan/BMan/QMan resources.

CDX IS the FMD shim — but battle-tested, maintained, and with a 6.12 kernel port
already available.
