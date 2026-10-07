# ASK2 L2 Bridge HW Offload — Architecture Analysis & Recommendation

**Date:** 2026-10-07
**Branches reviewed:** `dpaa1` (ASK2), `nxp-sdk` (vendor ASK)
**Task:** T-M6-2 — analyze how to do HW-offloaded bridging "like smart switches do it"

---

## 1. Executive Summary

**The vendor ASK (nxp-sdk branch) already implements hardware-offloaded L2 bridging using the ehash (external hash table) mechanism — the same mechanism ASK2 uses for routed/NAT offload. The ASK2 bridge plan (dpaa1 branch) currently assumes a CC-tree approach, which is architecturally different from the vendor's proven implementation and has hit an unresolved silicon regression.**

**Recommendation:** Re-architect ASK2 bridge offload to use **ehash with a 15-byte L2 key** (`PORT_ID|DA|SA|ETYPE`), matching the vendor's `cdx_ethernet_cc` exactly. This reuses ASK2's proven 9+ Gbit/s ehash path, avoids the CC-tree's unresolved silicon issues (dual-delivery, wedge, cross-port regression), and matches what the silicon microcode is actually designed for.

---

## 2. Vendor ASK Approach (nxp-sdk branch)

### 2.1 Architecture Overview

The vendor ASK implements L2 bridging through a **userspace-driven ehash table** with kernel bridge hooks:

```
┌─────────────────────────────────────────────────────────────┐
│  Linux Bridge (kernel)                                       │
│  - Learning, ageing, STP, VLAN filtering, flooding           │
│  - FDB management (br_fdb.c)                                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ Kernel hooks (020-ask-bridge-hooks.patch)
                              │ - brevent_notifier chain (FDB updates)
                              │ - br_fdb_can_expire callback (ageing)
                              │ - skb->abm_ff flag (fast-forward marker)
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  CMM (userspace daemon)                                      │
│  - ffbridge.c: reads bridge FDB via ioctl                    │
│  - cmmBrToFF(): maps bridge routes to fast-forward           │
│  - Sends L2 flow entries to CDX via NETLINK_L2FLOW           │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ NETLINK_L2FLOW messages
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  CDX (kernel module)                                         │
│  - control_bridge.c: manages L2 flow hash table              │
│  - add_l2flow_to_hw(): installs into ehash                   │
│  - delete_l2br_entry_classif_table(): removes from ehash     │
│  - Timer-based ageing (L2Bridge_timeout, default 30s)        │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ ExternalHashTableAddKey/RemoveKey
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  FMan PCD — cdx_ethernet_cc (ehash table)                    │
│  - keysize=15: PORT_ID(1) + DA(6) + SA(6) + ETYPE(2)         │
│  - max=512 entries, shared across ports                      │
│  - aging=yes (hardware ageing support)                       │
│  - mask=0xff, hashshift=0                                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ KeyGen scheme: cdx_ethernet_dist
                              │ - Extracts: ethernet.dst, ethernet.src, ethernet.type
                              │ - AC_CC dispatch to ehash
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  FMan FE-VM (per-flow action record)                         │
│  - fill_bridge_actions() builds opcode chain:                │
│    [STRIP_ETH 0x11] → [STRIP_VLAN 0x12] → [INSERT_VLAN 0x42]│
│    → [INSERT_L2_HDR 0x41] → [ENQUEUE_PKT 0x01]              │
│  - For untagged bridging: just ENQUEUE_PKT (no HMTD)         │
│  - Target: egress port's TX FQ (no-confirm)                  │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Key Vendor Files

| File | Purpose |
|------|---------|
| `kernel/flavors/ask/patches/020-ask-bridge-hooks.patch` | Kernel bridge hooks: notifier chains, FDB callbacks, skb flags |
| `kernel/flavors/ask/sources/cdx/cdx-5.03.1/control_bridge.c` | CDX L2 flow management (572 lines) |
| `kernel/flavors/ask/sources/cdx/cdx-5.03.1/control_bridge.h` | L2Flow structures, command definitions |
| `kernel/flavors/ask/userspace/cmm/src/ffbridge.c` | CMM bridge-to-fast-forward mapping (336 lines) |
| `kernel/flavors/ask/userspace/cmm/src/ffbridge.h` | Bridge FDB entry structures |
| `release/ask-6.12.49/cdx_pcd.xml` | FMC policy: `cdx_ethernet_cc` (keysize=15), `cdx_ethernet_dist` |
| `kernel/flavors/ask/config/gateway-dk/cdx_cfg.xml` | Port policies (all ports use `cdx_ethernet_dist` as last distribution) |
| `/mnt/builds/ASK/auto_bridge/auto_bridge.c` + `auto_bridge_private.h` | A **second, separate** vendor mechanism — see §2.4 |

### 2.3 Vendor Key Design Decisions

1. **Ehash, not CC-tree**: The vendor NEVER uses CC-tree/match-table classification for ANYTHING. All 16 classification nodes in `cdx_pcd.xml` are `<hashtable external="yes">` — the external hash table mechanism.

2. **15-byte L2 key**: `PORT_ID(1) + DA(6) + SA(6) + ETYPE(2)` — matches the KeyGen extraction order and the ASK1 `union dpa_key` layout exactly.

3. **Kernel bridge hooks, not switchdev**: The vendor uses custom `brevent_notifier` chains and `br_fdb_can_expire` callbacks, not the mainline switchdev framework. This is because the vendor's kernel is 5.4/6.12-era with custom patches.

4. **Userspace-driven**: CMM (userspace daemon) reads the bridge FDB and pushes entries to CDX via netlink. CDX installs them into hardware. This is a **control-plane/userspace** model, not a kernel-native model.

5. **Silicon-proven**: Live .106 board scheme 11 (`0xe4000000` = `PORT_ID|MACDST|MACSRC|ETYPE`) carried **1,225,734 packets** — the busiest scheme on the board.

### 2.4 A second vendor mechanism: `auto_bridge.ko` (ABM) — pure L2 flow discovery

**Correction to the first pass of this analysis:** §2.1-2.3 above describe `cdx/control_bridge.c` + `cmm/ffbridge.c`'s `cmmBrToFF()`. Reading those functions directly shows `cmmBrToFF(struct RtEntry *route)` is called from `cmm/src/conntrack.c:921` and takes a **route** (an L3/NAT flow whose *egress* interface happens to be a bridge) — it resolves the bridge's internal FDB *once* at flow-install time so the egress L2 header (dst MAC, optional VLAN tag) can be baked into that routed flow's hardware record (`dpa_get_tx_info_by_itf()` in `devman.c` does the analogous VLAN-tag part). This is the vendor's equivalent of what ASK2's `ask_neigh.c` already does for routed next-hops — **not** the general "two hosts purely switched, no L3 involved" case the bridge plan actually targets.

That general case is `auto_bridge.ko`'s job, a **separate, 1771-line kernel module** (`MODULE_DESCRIPTION("Automatic Bridging Module (ABM)")`) with its own state machine and its own netlink family (`NETLINK_L2FLOW`), independent of `control_bridge.c`'s ioctl-driven table:

- Learns flows by hooking the kernel's own bridge notifier chain (a vendor-patched `brevent_notifier`, `BREVENT_FDB_UPDATE`/`BREVENT_PORT_DOWN`) plus an ebtables/netfilter-bridge hook (`nf_register_net_hooks(..., abm_ebt_ops, ...)`), **not** switchdev.
- Tracks each (src MAC, dst MAC, in-dev, out-dev) pair as an `l2flowTable` entry through an explicit **promotion ladder**: `L2FLOW_STATE_SEEN → CONFIRMED → {LINUX | FF} → DYING` (`auto_bridge_private.h`). A flow is **not** installed into hardware the instant a MAC is learned — it has to be re-observed/confirmed first.
- Sized deliberately larger in software than in hardware: `L2FLOW_HASH_TABLE_SIZE=1024` buckets, `ABM_DEFAULT_MAX_ENTRIES=5000` software-tracked flows, against only **512** hardware ehash slots (`cdx_ethernet_cc` max=512) — i.e. the vendor tracks ~10x more candidate flows than it can ever offload, and only promotes the ones worth it.
- Ages hardware-forwarded flows via a **callback**, not polling: `br_fdb_register_can_expire_cb(&abm_fdb_can_expire)` — the (patched) bridge asks ABM "is this MAC still fast-forwarded?" before expiring it, and ABM says no (`return 0`) if it's in `L2FLOW_STATE_FF`. A new `skb->abm_ff` field (added by `patches/kernel/020-ask-bridge-hooks.patch`) is set by the bridge's own forward path once a flow is confirmed, which is how the data path and ABM agree a flow is live.
- Talks to a userspace daemon over its own reliable netlink protocol (ack/retry, `l2flow_list_wait_for_ack`), which is presumably what eventually calls into `fill_bridge_actions()`/`add_l2flow_to_hw()` — **not independently confirmed from source in this pass**; flagged as `[INFERRED]`.
- Has a real security history worth not repeating (`ISSUES.md`): **C1** — a netlink attribute (`L2FLOWA_IP_SRC/DST`) trusted attacker-supplied `nla_len` into a fixed-size union (buffer overflow), fixed via `nla_policy` caps. **H11/H7-r** — `spin_lock` vs `spin_lock_bh` mismatches between a process-context callback (`abm_fdb_can_expire`, called from `br_fdb_cleanup`) and softirq-context callers of the same lock. **M6** — an unbounded `schedule()` spin on module-exit waiting for flow drain, fixed to a bounded 5 s wait. ASK2's current `ask_bridge.c` already uses `spin_lock_bh` consistently and has no unbounded waits, so it doesn't repeat H11/M6, but this is worth keeping in mind for B3's teardown path and any future netlink/genl attribute added for bridge observability (C1's class of bug).

---

## 3. ASK2 Current Approach (dpaa1 branch)

### 3.1 Architecture Overview (Planned)

The ASK2 bridge plan (`plans/ASK2-BRIDGE-OFFLOAD-PLAN.md`) assumes a **CC-tree approach**:

```
┌─────────────────────────────────────────────────────────────┐
│  Linux Bridge (kernel)                                       │
│  - Learning, ageing, STP, VLAN filtering, flooding           │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ switchdev notifiers (atomic + blocking)
                              │ - SWITCHDEV_FDB_ADD/DEL_TO_DEVICE
                              │ - SWITCHDEV_PORT_ATTR_SET (STP state)
                              │ - NETDEV_CHANGEUPPER (bridge join/leave)
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  ask_bridge.c (ask.ko)                                       │
│  - B0: switchdev notifier skeleton (logs only)               │
│  - B1: CC DA-match key builder (host shadow)                 │
│  - B3 (planned): FDB workqueue → CC tree rebuild             │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ fman_pcd_cc_static_install()
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  FMan PCD — per-port CC tree                                  │
│  - DA-match leaves: key = destination MAC                    │
│  - Action: plain BMI enqueue to egress port TX FQ            │
│  - CC miss row: FE_ENTER (miss_fe_off) → ehash routed path   │
│  - No HMTD for untagged bridging                             │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Current State (2026-10-07)

| Stage | Status | Notes |
|-------|--------|-------|
| B0 | **DONE** (2026-09-10) | switchdev notifier skeleton, logs only, no HW install |
| B1 | **DONE** (2026-09-15) | CC DA-match key builder + KUnit tests |
| B2 | **BLOCKED** | Silicon de-risk: cross-port CC+HMTD delivery regression |
| B3 | **GATED** | Production switchdev wiring (gated on B2 PASS) |
| B4 | **PLANNED** | Lifecycle + teardown |
| B5 | **PLANNED** | Matrix + productization |

### 3.3 The B2 Blocker

The B2 silicon de-risk experiment has hit a **regression**:

- The exact port/FQID combination (`eth3`/`0x10` → `eth4`/`0x2ba`) that was silicon-proven in August 2026 (R3b/R4b, ~55k pps sustained) now delivers **zero frames**
- FMan hardware counters prove the CC comparator matches and the AD enqueue succeeds (`fmqm_etfc` rises, `fmqm_dtfc` stays flat)
- The live hardware AD content is byte-perfect against software intent
- The frames get "enqueued but never dequeued" — silently stuck in the target FQID's hardware queue
- A reboot clears the stuck frames (volatile QMan state)
- **Root cause unknown**: either a code regression between `4e21e78f` and `dpaa1` tip, or a fundamental `AC 0x28` (`PRE_BMI_ENQ`) same-port-only limitation

### 3.4 ASK2 CC-Tree Silicon History

The CC-tree approach has a troubled silicon history on this project:

| Issue | Date | Status |
|-------|------|--------|
| `next_engine=2` dual-delivery | 2026-10-02 | Every frame both HW-classified AND delivered to software RX path |
| `next_engine=3` RX-deaf wedge | 2026-10-03 | Port goes deaf, requires cold power-cycle (4/4 reproductions) |
| CC-tree architecture "broken" | 2026-08-05 | Five vendor-verified register fixes all still produced RX-silence |
| Cross-port delivery regression | 2026-09-16 | Proven-working pairing now delivers zero frames |
| VLAN inline-opcode freeze | 2026-08-25 | FE-VM VLAN opcodes (0x12/0x42) freeze after ~21 frames |

---

## 4. Key Architectural Differences

| Aspect | Vendor ASK (nxp-sdk) | ASK2 Plan (dpaa1) |
|--------|----------------------|-------------------|
| **Classification mechanism** | **Ehash** (external hash table) | **CC-tree** (match-table walker) |
| **Key format** | 15-byte `PORT_ID\|DA\|SA\|ETYPE` | DA-only or 15-byte (TBD) |
| **Action mechanism** | FE-VM opcodes in ehash record | CC leaf AD (plain enqueue or NADEN→HMTD) |
| **Control plane** | Userspace (CMM daemon) | Kernel-native (switchdev notifiers) |
| **Kernel integration** | Custom hooks (`brevent_notifier`) | Mainline switchdev framework |
| **Silicon proof** | **YES** — 1.2M+ packets on live board | **NO** — blocked on B2 regression |
| **Coexistence with routed** | Same ehash tables (different keys) | CC-miss → FE_ENTER → ehash |
| **Ageing** | Hardware (`aging=yes`) + software timer | Kernel bridge ageing only |
| **VLAN support** | Via FE-VM opcodes (0x12/0x42) | Via HMTD (T-M6-8, silicon-proven) |

### 4.1 The Critical Finding

**Qdrant finding (2026-10-03):** The vendor NEVER uses CC-tree/match-table classification for ANYTHING, including VLAN and L2 bridging. Reading `cdx_pcd.xml`'s full 525 lines, **every single one** of vendor's 16 declared classification nodes is `<hashtable external="yes">` — the external hash table mechanism.

This means:
1. The CC-tree path in ASK2 is exercising a **genuinely under-used, possibly barely-functional corner of the microcode**
2. The ehash path is the **well-trodden, silicon-proven** path (9+ Gbit/s routed, 1.2M+ packets L2 on vendor board)
3. The vendor's `cdx_ethernet_cc` proves that **L2 bridging via ehash works on this exact silicon**

4. **Decisive, ASK2-own-silicon confirmation (2026-10-02, qdrant):** this isn't just "the vendor prefers ehash" — ASK2's own CC-tree+HMTD VLAN path (`ask_vlan_cc.c`, more mature than the bridge CC-leaf harness) was kprobe-instrumented on `rx_default_dqrr` (the software RX dequeue callback) in a same-board, same-boot A/B: ehash/FE_ENTER-dispatched port-to-port traffic produced **197 hits for 8.77 GB** transferred (9.39 Gbit/s) — essentially zero software-RX touches — while CC-tree+HMTD-dispatched VLAN traffic produced **2,557,138 hits for only 2.64 GB** (2.82 Gbit/s) — **every single frame**, forward data and reverse ACKs alike, transited software RX. A same-day register dump found the BMI default/unclassified-frame FQID (`fmbm_rfqid`) stays live and valid on both ports even while the KeyGen/CC path is armed and matching — consistent with **dual delivery** (the CC-leaf path enqueues to both the hardware-selected FQ and the normal kernel path) rather than a clean, CPU-bypassing hardware-only forward. Toggling the VLAN CC-tree offload on/off produced statistically identical throughput, CPU/softirq profile, and kprobe rate. **Conclusion: on this silicon/driver combination, CC-tree+HMTD dispatch is not a CPU-bypass mechanism at all, independent of whether it reliably delivers frames** — it is strictly worse evidence than "the vendor doesn't use it," because it shows ASK2's *own* attempt at it, even in its most mature form, never achieved real hardware offload.

---

## 5. Smart Switch Bridging Patterns

Smart switches (and the Linux bridge) implement these key features:

### 5.1 Core Features

| Feature | Description | HW Offload Strategy |
|---------|-------------|---------------------|
| **MAC Learning** | Learn source MAC → ingress port mapping | Kernel learns, mirrors to HW |
| **FDB Ageing** | Expire stale entries (default 300s) | HW ageing or kernel-driven removal |
| **Known Unicast Forwarding** | Forward to known FDB entry | **HW fast path** (ehash/CC leaf) |
| **Unknown Unicast Flooding** | Flood to all forwarding ports | Software (kernel bridge) |
| **Broadcast/Multicast** | Flood or replicate to group members | Software (or HW multicast — T-M6-MC) |
| **STP/RSTP** | Spanning tree protocol | Software only (BPDUs never offloaded) |
| **VLAN Filtering** | 802.1Q tag push/pop/strip | HW via FE-VM opcodes or HMTD |
| **Port Isolation** | Prevent port-to-port forwarding | Software (bridge flags) |
| **MAC Limiting** | Max MACs per port | Software (bridge FDB limits) |

### 5.2 What Should Be Offloaded

Following the **fail-closed** principle (everything not explicitly offloaded stays in software):

**Offload (HW fast path):**
- Known unicast forwarding (DA match → egress port TX FQ)
- VLAN tag push/pop (if VLAN-aware bridging)
- Static FDB entries (no ageing concern)

**Keep in software:**
- BUM traffic (broadcast, unknown-unicast, multicast)
- Local termination (frames to bridge MAC)
- Control planes (STP BPDUs, LLDP, LACP, 802.1X)
- Learning/ageing/move decisions
- Non-forwarding port states (STP blocked/listening/learning)

### 5.3 Linux Switchdev Model

The mainline Linux switchdev framework provides:

```c
// FDB events (atomic notifier — must defer to workqueue)
SWITCHDEV_FDB_ADD_TO_DEVICE
SWITCHDEV_FDB_DEL_TO_DEVICE

// Port attributes (blocking notifier — can sleep)
SWITCHDEV_PORT_ATTR_SET  // STP state, bridge flags, ageing time

// Bridge join/leave (netdevice notifier)
NETDEV_CHANGEUPPER

// Offload acknowledgment
SWITCHDEV_FDB_OFFLOADED  // set .offloaded = true after HW install
```

**ASK2 already implements this correctly in B0** (`ask_bridge.c`). The switchdev integration is not the problem — the **hardware classification mechanism** is.

---

## 6. Recommended Architecture for ASK2

### 6.1 Core Recommendation: Switch from CC-Tree to Ehash

**Replace the CC-tree DA-match approach with an ehash-based L2 table**, matching the vendor's `cdx_ethernet_cc` exactly:

```
┌─────────────────────────────────────────────────────────────┐
│  Linux Bridge (kernel) — AUTHORITATIVE                       │
│  - Learning, ageing, STP, VLAN filtering, flooding           │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ switchdev notifiers (B0 skeleton — already done)
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  ask_bridge.c (ask.ko)                                       │
│  - FDB workqueue (coalesced + bounded)                       │
│  - Admission filter: skip is_local, locked; static-only v1   │
│  - Per-port L2 ehash table shadow                            │
│  - ask_l2_flow_insert/remove() → ehash record                │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ fman_pcd_ehash_add_key() / fman_pcd_ehash_remove_key()
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  FMan PCD — L2 ehash table (per-port or shared)              │
│  - Key: 15 bytes = PORT_ID(1) + DA(6) + SA(6) + ETYPE(2)     │
│  - Same key format as vendor cdx_ethernet_cc                 │
│  - Same hash: CRC-64 (ECMA-182, reflected)                   │
│  - Bucket index: fman_pcd_ehash_bucket_index()               │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ KeyGen scheme: L2 extraction (DA+SA+ETYPE)
                              │ - EKFC: 0xe4000000 (PORT_ID|MACDST|MACSRC|ETYPE)
                              │ - AC_CC dispatch to ehash (CCOBASE)
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  FMan FE-VM — per-flow action record (144 bytes)             │
│  - For untagged bridging: ENQUEUE_PKT (0x01) only            │
│  - For VLAN-aware: [STRIP_VLAN 0x12] → [INSERT_VLAN 0x42]    │
│    → [INSERT_L2_HDR 0x41] → [ENQUEUE_PKT 0x01]              │
│  - Target: egress port's no-confirm TX FQ                    │
│  - Stats: per-flow pkt/byte counters (record +256/+264)      │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 Why Ehash Over CC-Tree

| Factor | Ehash (Recommended) | CC-Tree (Current Plan) |
|--------|---------------------|------------------------|
| **Silicon proof** | ✅ Vendor: 1.2M+ packets; ASK2: 9+ Gbit/s routed | ❌ Blocked on B2 regression |
| **Microcode maturity** | ✅ Well-trodden path | ❌ Under-used corner |
| **Coexistence** | ✅ Same mechanism as routed/NAT | ⚠️ CC-miss→FE_ENTER chain (fragile) |
| **Per-flow stats** | ✅ Hardware counters at record +256/+264 | ❌ No per-key counters on this silicon |
| **Ageing** | ✅ Hardware `aging=yes` support | ❌ Software-only |
| **VLAN support** | ✅ FE-VM opcodes (0x12/0x42) — same as routed VLAN | ⚠️ HMTD chain (T-M6-8, proven but complex) |
| **Key flexibility** | ✅ 15-byte composite key | ⚠️ CC comparator window unresolved |
| **Implementation effort** | ✅ Reuses ask_fe_flow_insert() path | ❌ New CC-tree builder (B1 done, B2 blocked) |

### 6.3 Key Design Decisions

#### 6.3.1 Key Format

Use the **vendor's 15-byte composite key**:

```
Byte 0:      PORT_ID (hardware port ID, e.g. 0x10 for eth3)
Bytes 1-6:   Destination MAC (DA)
Bytes 7-12:  Source MAC (SA)
Bytes 13-14: EtherType (big-endian)
```

This matches:
- Vendor `cdx_ethernet_cc` keysize=15
- Vendor `union dpa_key` layout (`portid` byte 0 + `ether_key`)
- ASK2's proven 14-byte routed key format (PORT_ID + 5-tuple)
- KeyGen EKFC `0xe4000000` (PORT_ID|MACDST|MACSRC|ETYPE)

**For the first cut, DA-only matching (6 bytes) is also viable** — the ehash table can use a shorter key with a mask. But the 15-byte composite is the vendor-proven format.

#### 6.3.2 KeyGen Scheme

The L2 scheme needs:
- **EKFC**: `0xe4000000` = `PORT_ID(bit 31) | MACDST(bit 30) | MACSRC(bit 29) | ETYPE(bit 26)`
- **kgse_mode**: `0x80000006` (AC_CC, CCOBASE=0) — same as routed schemes
- **kgse_mv**: `0` (match-all, family-neutral) — same as the F-246 family-neutral scheme
- **kgse_hc**: hashShift=0, symmetric=false, mask=0x7fff — same as routed

**Critical constraint from ASK2 findings:** A second match-all (`kgse_mv=0`) KG scheme is **impossible** — scheme selection is a first-match SI-walk, and (per §6.3.5) CCOBASE selects an ehash table **per scheme**, not per key field, so one scheme cannot feed two differently-keyed tables either. A first draft of this analysis proposed working around this by sharing one EKFC's extraction between an L3 and an L2 "sub-key" via ehash masking — **this does not hold up**: a table's hash/bucket index is computed over that table's own declared `keysize`, so "mask a shared key to select which fields match" cannot make one hash computation serve two different keyed lookups (an L3 5-tuple lookup and an L2 DA/SA/ETYPE lookup) from the same extraction. That would still require two tables, and therefore — by the constraint already stated — two schemes.

**Corrected resolution: scope v1 to ports that are bridge-only.** The conflict above only exists when a *single port* needs a routed scheme **and** an L2 scheme armed **simultaneously**. A port that is purely an L2 bridge member (no L3/IP role of its own — the common "access/trunk switch port" case, exactly what real switches look like) only ever needs the **one** L2 scheme, so there is no second-scheme conflict to resolve. This mirrors how physical deployments already work: a bridge's L3 role (an IRB/SVI with an IP address) lives at the bridge/VLAN-interface level, not on the physical member port, so a member port doesn't usually need to be both a routed scheme *and* an L2 scheme target at once. **Explicitly out of scope for v1** (falls back to software): a single physical port that must simultaneously do hardware-routed L3 for some traffic and hardware-bridged L2 for other traffic. This is the same shape of trade-off the original CC-tree design already accepted (one scheme per port), just resolved per-port instead of attempting to multiplex one scheme.

#### 6.3.3 Ehash Table Configuration

```c
struct fman_pcd_ehash_table_params l2_table = {
    .size = 512,              // max entries (vendor uses 512)
    .keysize = 15,            // PORT_ID|DA|SA|ETYPE
    .hash_shift = 0,
    .hash_mask = 0x7fff,      // same as routed
    .aging = true,            // hardware ageing
    .stats = true,            // per-flow byte/frame counters
};
```

#### 6.3.4 FE-VM Action Record

For **untagged bridging** (simplest case):
```
Opcode chain: [ENQUEUE_PKT 0x01]
Params: target_fqid = egress port's no-confirm TX FQ
        mtu = 0xffff (no fragmentation)
        stats_ptr = per-flow stats location
```

For **VLAN-aware bridging** (later increment):
```
Opcode chain: [STRIP_ALL_VLAN 0x12] → [INSERT_VLAN_HDR 0x42]
              → [INSERT_L2_HDR 0x41] → [ENQUEUE_PKT 0x01]
```

This is **identical** to the vendor's `fill_bridge_actions()` and reuses ASK2's proven VLAN opcode emitters (F-233, silicon-validated).

#### 6.3.5 Coexistence with Routed/NAT

The ehash approach makes coexistence **trivial**:

- **Routed flows**: ehash record with L3 key (14-byte `PORT_ID|SIP|DIP|PROTO|SPORT|DPORT`), on ports armed with the routed scheme
- **Bridge flows**: ehash record with L2 key (15-byte `PORT_ID|DA|SA|ETYPE`), on ports armed with the L2 scheme instead (§6.3.2) — **a given port runs one or the other in v1, not both**
- **Separate tables, separate schemes, per port role** — not a shared/masked single table (§6.3.2 correction)
- **Miss handling**: ehash miss → KG default FQ → kernel (bridge floods or routes)

No CC-miss→FE_ENTER chain needed. No `miss_fe_off` wiring. No CC-tree rebuild under FDB churn.

### 6.4 Implementation Roadmap (Revised B0-B5)

| Stage | Description | Gate |
|-------|-------------|------|
| **B0** | ✅ DONE — switchdev notifier skeleton (logs only) | Build clean |
| **B1** | ✅ DONE — CC DA-match builder (superseded by ehash approach) | KUnit pass |
| **B1'** | **NEW** — L2 ehash key builder + FE-VM action emitter | KUnit vectors for 15-byte key + ENQUEUE_PKT record |
| **B2'** | **NEW** — Silicon de-risk: hand-arm L2 ehash entry on sacrificial port, prove HW forward + routed coexistence | Single flow HW forward + routed still HITs |
| **B3** | ask.ko production switchdev wiring (FDB workqueue → ehash insert/remove) | Two-port bridge, known-unicast HW forward, BUM stays SW |
| **B4** | Lifecycle + teardown (FDB del/flush, STP state change, port down, module unload) | Forward+inverse, pcd-snapshot clean |
| **B5** | Matrix + productization (learn/move/delete/age, multi-port, churn, performance) | Full acceptance contract |

### 6.5 What to Keep from Current Work

- **B0 switchdev skeleton** (`ask_bridge.c`): ✅ Keep — the notifier chains, workqueue, coalescing, and admission filter are all correct and reusable
- **B1 CC DA-match builder** (`0204`/`0205` patches): ❌ Supersede — replace with ehash key builder
- **Patches `0202`/`0204`/`0205`/`0206`/`0207`/`0208`**: ❌ Supersede — the CC-tree-specific patches become dormant
- **KUnit test infrastructure**: ✅ Keep — the test patterns apply to ehash key builder too

### 6.6 What to Add

1. **L2 ehash key builder** (`cc_pack_key_l2` equivalent for ehash):
   - Pack 15-byte `PORT_ID|DA|SA|ETYPE` key
   - Compute CRC-64 hash for bucket index
   - KUnit vectors matching vendor `cdx_ethernet_cc`

2. **L2 FE-VM action emitter**:
   - `ENQUEUE_PKT` (0x01) with target FQID
   - Reuse existing `ask_fe_flow_insert()` infrastructure
   - VLAN opcodes (0x12/0x42) for later increment

3. **Per-port L2 ehash table management**:
   - Create/destroy ehash table per bridge-member port
   - Shadow FDB in host memory
   - Insert/remove records on FDB events

4. **KeyGen scheme extension**:
   - Add L2 extraction fields to the per-port scheme's EKFC
   - Or create a dedicated L2 scheme (if EKFC space allows)

---

## 7. Risk Analysis

### 7.1 Risks of Ehash Approach

| Risk | Mitigation |
|------|------------|
| EKFC can't hold both L2 and L3 fields | Use separate ehash tables with separate per-port KeyGen schemes (§6.3.2) — do not attempt a shared/masked single table |
| L2 scheme conflicts with routed scheme on the same port | Scope v1 to bridge-only ports (no simultaneous route+bridge scheme on one port); a port needing both stays software until a later increment addresses it |
| Ehash table exhaustion | 512 entries per table; fail-closed to SW bridge past cap |
| Ageing of HW-forwarded flows | Offload static entries only in v1; add HW hit-counter → kernel FDB refresh in v2 |
| Non-IP frames through ehash | Base case is safest (no HMTD, no PAHM); test with ARP, IPv6, unknown ethertypes |

### 7.2 Risks of Staying with CC-Tree

| Risk | Status |
|------|--------|
| B2 regression unresolved | **ACTIVE BLOCKER** — cross-port delivery delivers zero frames |
| `next_engine=2` dual-delivery | Known issue — every frame also delivered to software |
| `next_engine=3` RX-deaf wedge | Known issue — requires cold power-cycle |
| CC-tree architecture fragility | Confirmed twice (Aug + Oct 2026), six weeks apart |
| No per-key HW counters | Architectural limitation on this silicon |
| Whole-tree rebuild under FDB churn | Measured 65% CPU at lower throughput (VLAN case) |

---

## 8. Comparison with Other Switch Architectures

### 8.1 DPAA2 Switch (dpaa2-switch.c)

The DPAA2 switch driver (closest Freescale shape) uses:
- **DPSW (DPAA2 Switch Object)** — hardware switch with FDB learning in silicon
- **MC firmware** manages the switch object
- **Switchdev** for FDB offload

DPAA2 has a **dedicated switch hardware block** (DPSW). LS1046A's FMan does not — it uses the **parser + KeyGen + ehash/CC** pipeline. The ehash approach is the LS1046A equivalent of DPAA2's FDB.

### 8.2 Other Switchdev Drivers

| Driver | Mechanism | Relevance |
|--------|-----------|-----------|
| `am65-cpsw-switchdev.c` | ALE (Address Lookup Engine) table | Compact switchdev example; ALE is a CAM-based FDB |
| `adin1110.c` | SPI switch with FDB table | Smallest two-chain example |
| `mscc_felix` | Sparx5 switch ASIC | TC flower offload, not pure L2 |
| `mtk_eth_soc` | PPE (Packet Processing Engine) | Ralink/MediaTek PPE — similar ehash concept |

All of these use a **hardware FDB table** (CAM or hash) — the ehash approach is the LS1046A equivalent.

---

## 9. Conclusion

The vendor ASK proves that **L2 bridge hardware offload works on LS1046A silicon using the ehash mechanism** — the same mechanism ASK2 already uses for routed/NAT at 9+ Gbit/s. The ASK2 bridge plan's CC-tree approach is architecturally different, has a troubled silicon history, and is currently blocked on an unresolved regression.

**Recommended path forward:**

1. **Keep B0** (switchdev skeleton) — it's correct and reusable
2. **Replace B1-B2** (CC-tree builder + silicon de-risk) with **ehash-based L2 offload**
3. **Reuse ASK2's proven ehash infrastructure** (`ask_fe_flow_insert()`, CRC-64, per-port tables)
4. **Match the vendor's 15-byte key format** (`PORT_ID|DA|SA|ETYPE`) — silicon-proven
5. **Start with untagged bridging** (ENQUEUE_PKT only, no HMTD) — simplest possible path
6. **Add VLAN-aware bridging later** (reuse F-233 VLAN opcodes)
7. **Offload static FDB entries only in v1** (no ageing concern); add dynamic entry ageing-refresh in v2

This approach:
- ✅ Reuses silicon-proven mechanisms (ehash, CRC-64, FE-VM ENQUEUE_PKT)
- ✅ Matches vendor's production-proven configuration
- ✅ Avoids the CC-tree's unresolved silicon issues
- ✅ Provides per-flow HW counters (record +256/+264)
- ✅ Supports hardware ageing (`aging=yes`)
- ✅ Scales to VLAN-aware bridging via existing VLAN opcode emitters
- ✅ Maintains kernel bridge authority (switchdev, fail-closed)

---

## 10. References

- `plans/ASK2-BRIDGE-OFFLOAD-PLAN.md` — Current ASK2 bridge plan (CC-tree approach)
- `plans/ASK2-MASTER-PLAN.md` §4.6.5 — T-M6-2 master task
- `arch/fman-vendor-source-extraction-2026-08-07.md` — Vendor L2 scheme evidence (1.2M packets)
- `specs/reference/nxp-ask-fmc/cdx_pcd.xml` — Vendor FMC policy (cdx_ethernet_cc)
- `kernel/flavors/ask/sources/cdx/cdx-5.03.1/control_bridge.c` — Vendor L2 flow management
- `kernel/flavors/ask/sources/cdx/cdx-5.03.1/cdx_ehash.c` — Vendor `add_l2flow_to_hw()`, `fill_bridge_actions()`
- `kernel/flavors/ask/patches/020-ask-bridge-hooks.patch` — Vendor kernel bridge hooks
- `kernel/ask/oot-modules/ask/ask_bridge.c` — ASK2 B0 switchdev skeleton
- `kernel/ask/oot-modules/ask/ask_vlan_cc.c` — ASK2 VLAN CC-tree implementation (proven)
- `kernel/ask/oot-modules/ask/ask_flow_offload.c` — ASK2 ehash flow insert (proven 9+ Gbit/s)
- `/mnt/builds/ASK/auto_bridge/auto_bridge.c` + `auto_bridge_private.h` — vendor ABM pure-L2 flow discovery/promotion ladder (§2.4)
- `/mnt/builds/ASK/cmm/src/ffbridge.c` (`cmmBrToFF()`), `/mnt/builds/ASK/cdx/control_bridge.c` — vendor routed-flow-via-bridge L2 resolution (distinct from ABM, §2.4)
- `/mnt/builds/ASK/ISSUES.md` — vendor ABM security history (C1, H11/H7-r, M6)
- `plans/ASK2-REWRITE-PLAN.md` §1, §6 Phase 1 — the ehash/FE-VM VLAN fix this recommendation mirrors (2026-10-04/06)
- Qdrant: "vendor NEVER uses CC-tree for ANYTHING" (2026-10-03)
- Qdrant: "ASK2 CC-tree/AC_CC dispatch reconciliation" (2026-10-03)
- Qdrant: "ASK2 bridge offload regression" (2026-09-16)
- Qdrant: "ASK2 VLAN CC-tree+HMTD throughput ceiling — DECISIVE QUANTIFIED EVIDENCE" (2026-10-02) — the `rx_default_dqrr` kprobe A/B (§4.1 point 4)
- Qdrant: "ASK2 BRIDGE L2 OFFLOAD — ARCHITECTURE SYNTHESIS" (tags `ask2-bridge-offload`, `cc-tree-vs-ehash`, 2026-10-07) — this analysis's own prior synthesis pass
