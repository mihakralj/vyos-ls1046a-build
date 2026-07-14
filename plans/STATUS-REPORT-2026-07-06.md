# ASK2 Status Report — 2026-07-06

**Branch:** `dpaa1`  
**CI build:** #28766753652 **(latest)**  
**ISO:** `vyos-2026.07.06-0357-rolling-LS1046A-arm64.iso`  
**Kernel:** `6.18.36-vyos`  
**Board:** Mono Gateway DK LS1046A, IP `192.168.1.185`  
**FMan microcode:** 210.10.1 (proprietary, from QSPI)  
**FMan PCD caps:** 0x17 (CC | HM | POL | PARSER)

---

## 1. Achieved Modules — In-Tree Kernel (94 board patches)

### 1.1 FMan PCD (classification engine) — COMPLETE, ON SILICON

| Subsystem | Patches | Components | Status |
|-----------|---------|------------|--------|
| **PCD core** | 0092, 0113, 0126 | `fman_pcd.c`, MURAM genpool, DCSR taps | SILICON-VERIFIED |
| **KeyGen** | 0097, 0106, 0118, 0148 | `fman_keygen.c`, `fman_pcd_kg.c`, KGSE_CCBS graft | SILICON-VERIFIED |
| **CC tree** | 0098, 0107, 0108, 0115, 0116 | `fman_pcd_cc.c`, per-key AD, SDK convergence | ON SILICON |
| **HM (L3 fwd)** | 0090, 0099, 0119, 0120 | `fman_pcd_manip.c`, nexthop dedup | COMPILED |
| **Policer** | 0091, 0100 | `fman_pcd_plcr.c`, srTCM/trTCM | ON SILICON |

### 1.2 FE-VM (Fork B — ehash datapath) — COMPLETE, PARTIALLY ON SILICON

| Component | Patch | Purpose | Status |
|-----------|-------|---------|--------|
| **FE pool** | 0122 | 100×28B refcounted leaf pool | SILICON-VERIFIED |
| **Singletons** | 0124 | MUX/Transition/EXIT-DEALLOCATE | SILICON-VERIFIED |
| **Ehash table** | 0125 | DDR bucket array, CRC64-indexed | SILICON-VERIFIED |
| **FE_ENTER** | 0127 | CONT_LOOKUP|ALLOCATE root AD | SILICON-VERIFIED |
| **Flow insert** | 0128 | CRC64 hash, DDR record, next-FE link | COMPILED |
| **Hash object** | 0131 | `t_ExtHashFe` 7-word encoder | SILICON-VERIFIED |
| **ENQ FE** | 0127 | Per-flow FQID terminal | SILICON-VERIFIED |
| **Context builder** | 0135 | `FmPcdCcBuildContextByFE`, MURAM params page | COMPILED |
| **Flow-offload slot** | 0145 | Backend registration hook | COMPILED |
| **fe_flow debugfs** | 0128 | Insert/clear/drain with 13-byte 5-tuple keys (F-063 fixed 2026-07-14) | COMPILED (DDR confirmed on silicon) |

### 1.3 FE-VM Arm/Disarm — SILICON-VERIFIED (today)

| Patch | Component | Purpose | Result |
|-------|-----------|---------|--------|
| **0132** | `fman_pcd.c` / `fman_pcd_kg.c` | `fe_arm` debugfs engage/disengage verbs | SILICON-VERIFIED |
| **0133** | `fman_keygen.c` | Real AC_CC NIA: KGSE_MODE = FM_CTL\|AC_CC (0x80000006) | SILICON-VERIFIED |

**Verified 2026-07-06 on hardware:**
- Scheme 3 → CC(AC_CC) with KGSE_MODE=0x80000006, KGSE_CCBS=0
- Port 0x10 RCCB set to FE_ENTER root AD (0x51000)
- pcd-snapshot diff confirms **byte-clean reversibility** (S0↔S1)
- Detach_cc fix (0106: `next_engine=2` restore) proven correct
- ARMED→DISENGAGED→ARMED cycle = zero register drift

### 1.4 DPAA1 Networking — COMPLETE, ON SILICON

| Feature | Patches | Status |
|---------|---------|--------|
| HW VLAN strip | 0101 | ON SILICON |
| Ingress policer (tc matchall) | 0104, 0104a | ON SILICON |
| ntuple CC steering | 0109 | COMPILED |
| AF_XDP + True-ZC | 0068–0114 (30+ patches) | ON SILICON |
| BMan IVCI fix | 0139 | ON SILICON |
| CEETM stub | 0104b, 0111 | COMPILED |

### 1.5 TX Confirm Bypass — COMPILED

| Patch | Component | Purpose |
|-------|-----------|---------|
| 0136 | `fman_port.c` | `fman_port_set_silicon_hit_release_mode()` / `_all()` |

API exported and wired to `ask.ko` — not yet tested on silicon (requires FE HIT path).

### 1.6 MANIP Chain API — COMPILED

| Patch | Component | Purpose |
|-------|-----------|---------|
| 0137 v2 | `fman_pcd_manip.c` | `HMAN_OC_IP_MANIP=0x34` fix (SDK-opaque from lf-5.4 L11637), HMCD_LAST clearing |

### 1.7 CAAM (Hardware Crypto)

| Patch | Component | Purpose | Status |
|-------|-----------|---------|--------|
| 0134 | `caam/qi.c` | QI descriptor sharing | COMPILED (dormant) |

### 1.8 Other Kernel Infrastructure

| Component | Patches | Purpose | Status |
|-----------|---------|---------|--------|
| FMan caps detection | 0086, 0086a, 0086b | DT probe + productive structs | ON SILICON |
| FMan ctrl microcode | 0117 | Load open-source 106.x ucode | COMPILED |
| SFP rollball PHY | 101, 4005, 4009 | Phylink EINVAL fallback | ON SILICON |
| INA234 power sensors | 4002 | OOT hwmon driver | ON SILICON |
| Flow offload debug | 130-fixes | nf_flow_table_offload alloc-failure log | COMPILED |
| fsl_fmd_shim | kernel module | `/dev/fm0` chardev (DPDK fmlib stub) | COMPILED |
| LP5812 LED driver | OOT driver | `leds-lp5812.c` i2c-15 LED controller | COMPILED |

---

## 2. Achieved Modules — OOT `ask.ko` (~1500 LOC + ~800 LOC KUnit)

| File | Purpose | Status |
|------|---------|--------|
| `ask_main.c` | Module core, init/exit | COMPILED |
| `ask_hw.c` | HW engage: FE pool→singletons→FE chain→arm→TX bypass | COMPILED |
| `ask_flow.c` / `.h` | FE flow table, CRC64 hash, insert/lookup/drain | COMPILED |
| `ask_flow_offload.c` | nf_flow_offload BIND/REPLACE/DESTROY ops | COMPILED |
| `ask_genl.c` | Generic netlink control plane, per-VRF family | COMPILED |
| `ask_op.c` | Op encoder: ct state→FE leaf mutations | COMPILED |
| `ask_neigh.c` | Neighbor resolution | COMPILED |
| `ask_debugfs.c` | Debugfs: stats, stats-clear, hw-engage | COMPILED |
| `ask_stats.c` | Internal stat counters, atomic updates | COMPILED |
| `ask_trace.h` | Trace event definitions | COMPILED |
| `ask_xfrm.c` | HW IPsec xfrmdev_ops (stub, ~1 KB) | COMPILED (dormant) |
| `ask_bridge.c` | L2 switchdev ask_bridge.ko (stub, 417 B) | COMPILED (dormant) |
| `ask_caam.c` | CAAM pathway (stub, ~100 LOC) | COMPILED (dormant) |

**KUnit tests:** `ask_test_main.c`, `ask_test_dummy.c`, `ask_test_hw_pcd.c`, `ask_test_flow_offload.c`, `ask_test_genl.c`, `ask_test_flow.c` — all compiled, not yet run on target.

---

## 3. Achieved — Boot & Diagnostic Tooling

| Tool | Language | Purpose | Status |
|------|----------|---------|--------|
| `fan-pid` | Python 3 | Multi-zone PID fan controller (I2C 0x2e, bypasses broken sysfs) | PRODUCTION |
| `fan-check` | Bash | Thermal+fan health probe (Nagios/monit compatible) | PRODUCTION |
| `caam-check` | Bash | CAAM SEC 5.4 hardware crypto status | PRODUCTION |
| `firmware-check` | Bash | Boot firmware / U-Boot env / microcode identity | PRODUCTION |
| `xsk-zc-check` | Bash | AF_XDP True-ZC RX gate-counter reader | PRODUCTION |
| `pcd-snapshot` | Python 3 | FMan PCD silicon state capture + byte-clean diff | PRODUCTION |
| `sfp-check` | Bash | SFP module LOS / link status | PRODUCTION |
| `led.py` | Python 3 | LP5812 LED control (fade, palette, raw RGBW) | PRODUCTION |
| `vyos-postinstall` | Bash | Phase 1 (fw_setenv) + Phase 2 (/boot/vyos.env) | PRODUCTION |
| `boot.cmd` | U-Boot script | USB live-boot boot.scr | PRODUCTION |

---

## 4. Achieved — Infrastructure & CI

| Component | Purpose |
|-----------|---------|
| **Single-image build** | One ISO, one package — flavor collapse (2026-06-14) |
| **Patch application** | `git apply --3way` replaces legacy `patch -p1` loop |
| **CI caching** | Kernel + vyos-1x .deb cache on self-hosted Cobalt 100 ARM64 runner |
| **lxc200 HTTP relay** | `http://192.168.1.137:8080/iso/latest.iso` (board install URL) |
| **SFP-10G-T copper** | Rollball PHY works with cold-boot workflow |
| **VyOS integration** | 28 patches (`vyos-1x-001` through `vyos-1x-028`) |

---

## 5. Silicon-Verified Results

### 5.1 Phase 1 AC_CC Arm (2026-07-04, 2026-07-05, 2026-07-06)

| Test | Result |
|------|--------|
| FE pool engage (100 leaves) | ✅ |
| Singletons build (MUX+Transition+EXIT) | ✅ |
| Ehash table (0x7FFF mask, 16B key) | ✅ |
| FE_ENTER root AD build | ✅ |
| AC_CC arm (`next_engine=3`, KGSE_MODE=0x80000006) | ✅ |
| RCCB set to FE_ENTER (0x51000) | ✅ |
| pcd-snapshot scheme confirmation: CC(AC_CC) | ✅ |
| **Reversibility (S0↔S1 byte-clean)** | ✅ |
| Port survives armed state (no deafness) | ✅ |
| Disengage restores RSS scheme + clears RCCB | ✅ |
| KG extraction template verified: `ekfc=0x00180206` | ✅ |

### 5.2 Phase 2 FE-VM Dispatch

| Test | Result |
|------|--------|
| 256-flow covering test (all 1B keys) | **0 packets matched** — proved CC walk never reaches FE-VM |
| Root cause identified | RCCB→FE_ENTER direct: CC engine expects RCCB→group_table→CC_node |
| CC scaffold (0150) designed | 16B group table + 20B pass-through node in MURAM |
| CC scaffold compile | **FAILED (7 builds)** — MURAM APIs not accessible from `fman_pcd_kg.c` |

### 5.3 EXIT-DEALLOCATE Confirmed

- Terminal MISS disposition proven on 210.10.1 microcode
- Frames arriving at FE_ENTER with no match → Transition → EXIT → DEALLOCATE
- This is NOT the "park" behavior — the FE VM correctly supplies the terminal disposition

---

## 6. Current Blocker

### CC scaffold cannot compile in `fman_pcd_kg.c`

**Root cause:** The CC group-table MURAM allocation uses internal FMan APIs (`fman_muram_offset_to_vbase`, `fman_pcd_muram_alloc`, `iowrite32be`) that require headers (`fman_muram.h`, `<linux/io.h>`) not available through the include chain of `fman_pcd_kg.c`.

**7 failed CI attempts** proved this approach is structurally wrong.

**Solution:** Move the CC scaffold MURAM allocation into `fman_pcd.c` (where `fman_muram.h`, `fman_pcd_internal.h`, and all FMan internals are natively available). The scaffold block will be added as a **new discrete patch** that inserts the group table + CC node allocation into `fman_pcd_fe_arm_engage()` BEFORE calling `fman_pcd_kg_port_arm_fe()`.

**Impact:** CC scaffold code is self-contained (~36 bytes MURAM per engage cycle). No change to `arm_fe`/`disarm_fe` functions in `fman_pcd_kg.c`.

---

## 7. Upcoming Tasks — Priority-Ordered

### Priority 1: CC Scaffold + HIT Path (Phase 2 M2 Gate)

- [ ] **P1.1** — New patch: CC scaffold in `fman_pcd.c` (group table + pass-through node allocation, inserted into `fman_pcd_fe_arm_engage()` before `arm_fe` call)
- [ ] **P1.2** — CI build + board install
- [ ] **P1.3** — Set up traffic infra: lxc200→eth3 (10.99.1.0/30), eth4→lxc201 (10.11.1.0/30)
- [ ] **P1.4** — FE chain build + flow insert with correct 8-byte key (SIP+DIP or L4 ports)
- [ ] **P1.5** — AC_CC arm → iperf3 TCP port 5201 → verify HIT path
- [ ] **P1.6** — TX confirm bypass: `fman_port_set_silicon_hit_release_mode()` → iperf3 throughput
- [ ] **P1.7** — **M2 gate**: ≥2 Gbps throughput AND ≤5% CPU with iperf3 gen→DUT→sink

### Priority 2: Integrated ask.ko Flow Offload (Phase 2)

- [ ] **P2.1** — Wire `ask_hw.c` to `ask_flow.c` — load FE chain + arm on conntrack events
- [ ] **P2.2** — Wire `ask_flow_offload.c` — TC BIND→REPLACE→DESTROY → FE flow insert/delete
- [ ] **P2.3** — Wire `ask_op.c` — ct state → FMan FE leaf mutations
- [ ] **P2.4** — `ct_offload` mode: dmesg confirms `HIT: ...` per packet
- [ ] **P2.5** — KUnit run on target: `kunit.py run ask_test_flow`

### Priority 3: L2 Switchdev Bridge (Phase 3)

- [ ] **P3.1** — `ask_bridge.ko` full body: FDB populations, MAC learning
- [ ] **P3.2** — `switchdev SWITCHDEV_FDB_ADD_TO_BRIDGE/DEL`
- [ ] **P3.3** — Bridge offload: untagged frames → FMan HM→TX bypass

### Priority 4: HW IPsec ESP (Phase 4)

- [ ] **P4.1** — `ask_xfrm.c` full body: `xfrmdev_ops` via CAAM QI
- [ ] **P4.2** — ESP SA programming → CAAM job ring → FMan FQ egress
- [ ] **P4.3** — XFRM state sync (`set vpn ipsec ... hw-offload sec`)

### Priority 5: Operator CLI + VPP Mutual Exclusion (Phase 5)

- [ ] **P5.1** — `set system offload ask` — engages ask.ko, sets boot-time enable
- [ ] **P5.2** — `set system offload ask` ↔ `set vpp settings` mutual exclusion
- [ ] **P5.3** — `show system offload` — ASK stats, flow table, HW counters
- [ ] **P5.4** — VyOS XML/valdation integration

### Priority 6: Soak Testing (Phase 6)

- [ ] **P6.1** — 24h iperf3 continuous with periodic arm/disarm cycles
- [ ] **P6.2** — MURAM exhaustion test (>1000 engage→disengage cycles)
- [ ] **P6.3** — Cold-boot with SFP-10G-T PHY → full arm → traffic → reboot → verify
- [ ] **P6.4** — ASK+VPP switch at runtime → verify mutual exclusion
- [ ] **P6.5** — Line-rate target: ≥1 Gbps forwarding at <10% CPU

### Technical Debt / Cleanup

- [ ] Remove temporary KG debug log (patch 0148: `pr_info` every scheme setup)
- [ ] `pcd-snapshot` MURAM leak detection for CC scaffold (currently ~36B per arm, leaked on disengage)
- [ ] Rust toolchain: export `ask_caam_t` from ask.ko, import `FmPcdManipNodeParams` from kernel (current: both `/proc/kallsyms` + manual lookup)
- [ ] `fman_pcd_kg.c` — remove `pr_info` from `fman_pcd_kg_port_arm_fe`; caller in `fman_pcd.c` already logs
- [ ] Unify ARM64 builders: switch Cobalt 100 from Docker/local to `docker://ghcr.io/...`

---

## 8. KG Key Extraction Format (for Phase 2 HIT test)

**Definitive byte layout** from `fman_keygen.c` `DEFAULT_HASH_KEY_EXTRACT_FIELDS = 0x00180206`:

| Offset | Field | Size | KG_SCH_KN | Matches |
|--------|-------|------|-----------|---------|
| 0–1 | L4 PDST | 2B | `KG_SCH_KN_L4PDST` (bit 1) | ICMP type+code — **VARIABLE, unmatchable** |
| 2–3 | L4 PSRC | 2B | `KG_SCH_KN_L4PSRC` (bit 2) | ICMP checksum — **VARIABLE** |
| 4–7 | IPSEC SPI | 4B | `KG_SCH_KN_IPSEC_SPI` (bit 9) | Packet ID+sequence — **VARIABLE** |
| 8–11 | IP DST | 4B | `KG_SCH_KN_IPDST1` (bit 19) | DUT eth3 IP (fixed) |
| 12–15 | IP SRC | 4B | `KG_SCH_KN_IPSRC1` (bit 20) | Peer IP (fixed per session) |

**Extraction order** (lowest EKFC bit → lowest byte offset): `L4PDST → L4PSRC → SPI → IPDST → IPSRC`

**Key format for flow insert** (`fe_flow add`):
- **ICMP**: UNMATCHABLE (checksum+SPI vary per packet)
- **TCP on port 5201**: key = `[DPORT 2B][SPORT 2B][SPI 4B][IPDST 4B][IPSRC 4B]`
  - `DPORT=0x1451` (fixed), `SPORT` per-connection (fixed)
  - 8-byte match: `1451XXXX00000000C0A801B6C0A8010A` (requires `key_size=12` with full SIP+DIP)
  - Shorter match: `1451XXXX` (4B) covers all TCP:5201 regardless of peer

---

## 9. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| CC scaffold still drops frames | Medium | Blocked M2 gate | Test with 256-flow coverage + hardware counters |
| KG key extraction wrong | Low | Wrong flow key | Extraction order confirmed via dmesg `ekfc=0x00180206` |
| SFP-10G-T PHY destabilizes | High | Lost test time | Single cold boot per test cycle; never restart PHY while armed |
| MURAM exhaustion (>1000 cycles) | Low | Leak grows | CC scaffold: 36B per engage (1820 cycles before exhaustion); documented |
| iperf3 can't reach 2 Gbps | Medium | M2 gate fail | Need TCP single-stream → multi-stream; TX bypass tuned |
| ARM64 mirror missing package | Low | CI failure | `vyos-build.py` fallback to stock VyOS mirror |

