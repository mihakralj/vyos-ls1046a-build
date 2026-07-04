# ASK2 Core Modules — Progress Towards Modern HW Offload
**2026-07-04** · Branch: `puddle-cornet` · Kernel: `6.18.37-vyos` · Board: `192.168.1.185`

---

## Module Progress Summary

| # | Module | Patches | Status | ask-check |
|---|---|---|---|---|
| 1 | **FMan PCD Substrate** | `0092`–`0106`, `0116`, `0129` | ✅ COMPLETE, HW-PROVEN | §2: 8/8 OK |
| 2 | **FE-VM Dormant Chain** | `0122`–`0131` | ✅ COMPLETE, BYTE-VALIDATED | §4: 9/9 OK |
| 3 | **AC_CC Classifier Arm** | `0132`, `0133` | ✅ PROVEN (3 cycles, reversible) | §5: 2/2 OK |
| 4 | **FE-VM Context Builder** | `0135`, `0146` | ✅ PROVEN (FE-VM processing frames) | §6: 3/3 OK |
| 5 | **Flow Entry & Lookup** | `0128` | ✅ COMPILED, ⏳ HIT GATED | — |
| 6 | **Hardware Forwarding** | `0100`, `0120`, `0137` | ✅ COMPILED (policer HW-proven) | §7: 3/3 OK |
| 7 | **TX Confirm Bypass** | `0136` | ✅ COMPILED | §8: 1/1 OK |
| 8 | **ask.ko Control Plane** | `0145`, OOT | ✅ LOADED (genl, debugfs, backend) | §9: 5/6 OK |
| 9 | **ask_bridge.ko L2 Switchdev** | — | ❌ STUB (417 B) | §9: FAIL |
| 10 | **ask_xfrm.c HW IPsec** | `0134`–CAAM share | ❌ STUB (1 KB) | §10: 2/3 OK |
| 11 | **Operator CLI** | — | ❌ NOT STARTED | §11: FAIL |
| 12 | **Conntrack Integration** | — | ❌ NOT STARTED | — |

**Total: 29/32 OK, 3 FAIL** (genuine gaps: bridge stub, ESP stub, CLI)

---

## Detailed Module Status

### 1. FMan PCD Substrate ✅
**Patches:** `0092`, `0097`–`0100`, `0105`, `0106`, `0116`, `0129`

Silicon programming layer for LS1046A FMan1:
- **KeyGen:** scheme creation, port attach (CC/Policer/AC_CC arm), inverse teardown
- **Coarse Classification (CC):** static CC tree install, node add/remove, base query
- **Header Manipulation (HM):** header manipulation install, node install, next-hop get/put
- **Policer (PLCR):** profile install, FMPL block enable (BUG 3a FIXED, HW-validated)
- **MURAM:** 64 KiB reserved, budget tracking, high-water mark
- **Reversible mode switch:** `fman_pcd_offload_engage`/`_disengage`, register-exact, `pcd-snapshot` verifier (100× soak passed)

### 2. FE-VM Dormant Chain ✅
**Patches:** `0122`, `0124`, `0125`, `0127`, `0128`, `0130`, `0131`

Byte-validated against lf-5.4 SDK oracle (Phase 0, 2026-06-16):
- `fe_pool`: FE object pool (MURAM 2800 B, available=100)
- `fe_singletons`: MUX @0x4ac00, Transition @0x4ad00, Exit @0x4ae00
- `fe_ehash`: 256-bucket external hash, CRC64 (reflected ECMA-182 poly 0xC96C5795D7870F42), DDR buckets
- `fe_enq`: terminal ENQ FE (FQID 0x100 → `fe_enq build 100`)
- `fe_hashfe`: `t_ExtHashFe` @0x4b000, HIT→MUX, MISS→Exit
- `fe_enter`: FE_ENTER root AD @0x59200, ALLOCATE bit set, `pcAndOffsets=0xF6`
- `fe_flow`: LIFO head-insert flow records, CRC64 bucket index, DDR records
- 9 debugfs inspector nodes present

### 3. AC_CC Classifier Arm ✅ (Phase 1)
**Patches:** `0132`, `0133`

HW-proven on 210.10.1 ucode (3 full cycles, 2026-07-04):
- `0132`: `fe_arm` debugfs node (engage/disengage)
- `0133`: **REAL** AC_CC encoding (`KGSE_MODE 0x80000006`, `next_engine=3`)
- Corrects `0132` CCBS placebo (`KGSE_CCBS`) which was a no-op on this ucode
- dmesg: `port 0x10 FE-ARMED (kgse_mode=AC_CC fmbm_rccb=0x59200 kgse_ccbs=0)`
- Reversibility: 3× arm→disengage→teardown, MURAM 0, `pcd-snapshot diff` clean
- Port survives armed state (eth3 pings work, 0% loss on MISS)

### 4. FE-VM Context Builder ✅ (Phase 2)
**Patches:** `0135`, `0146`

HW-proven on 210.10.1 ucode (2026-07-04):
- `0135`: `fman_pcd_fe_context_build()` — programs ENQ/MUX/Transition contexts into FE-VM working store
- `0146`: `fman_pcd_fe_build_contexts()` — calls context builder during `fe_arm engage`
- **Behavior change proven:** without context builder, pings pass through to kernel (0% loss). With context builder, pings are classified by FE-VM → MISS → Exit → dropped (100% loss). This confirms the FE-VM is actively processing frames.
- Alignment fix: `ioread32be((u32 __iomem *)fe + 1)` not `ioread32be(fe + 1)`

### 5. Flow Entry & Lookup ⏳ GATED on Extraction Template
**Patch:** `0128`

- Flow records in DDR (DMA-coherent via `0130`)
- 8-byte key format, CRC64 bucket indexing
- LIFO head-insert with collision chain walking
- **HIT verification blocked:** FMan KeyGen extraction template (which 8 bytes the hardware extracts from IPv4 packets) unknown. The default extraction uses KGSE_MV `0x00180006` (5-tuple: SIP|DIP|PROTO|SPORT|DPORT), but the byte extraction order and L4 field handling for ICMP (no ports) must be confirmed.

### 6. Hardware Forwarding ✅ COMPILED
**Patches:** `0100`, `0120`, `0137`

- **Policer** (`0100`): FMPL block enable (GCR `EN|STEN`), BUG 3a FIXED (policed ping 0% loss), HW-validated
- **MANIP chain** (`0137` v2): `fman_pcd_manip_chain_create` with HMAN_OC `0x34` fix, 3-manip chain support (strip-L2 + insert-L2 + ipv4-forward)
- **Next-hop rewrite cache** (`0120`): `fman_hm_nexthop_get`/`_put`, shared MANIP per adjacency, O(next-hops) not O(flows) MURAM consumption

### 7. TX Confirm Bypass ✅ COMPILED
**Patch:** `0136`

- `fman_port_set_silicon_hit_release_mode` — eliminates QMan DQR confirm softirq (~20% CPU floor)
- Combined with `0146` context builder: HIT frames go straight to egress FQ, no DQR round-trip

### 8. ask.ko Control Plane ✅ LOADED
**Patches:** `0145`, OOT `kernel/flavors/ask/oot-modules/ask/`

- **Loaded:** `ask.ko` v2.0.0, refcnt=0 (no ports engaged)
- **genl family:** registered (id 0x1e), version 1
- **debugfs:** `/sys/kernel/debug/ask/offload` control node present
- **Flow-offload backend** (`0145`): `dpaa_register_flow_offload_handler` → `dpaa_setup_tc`
- **Engage/disengage:** `ask_hw_offload_engage` uses M1 coarse mode switch (`fman_pcd_offload_engage`)
- ⏳ **Not yet wired to FE path** — still uses dead Fork-A exact-match path

### 9. ask_bridge.ko ❌ STUB (417 B)
**Target:** Phase 3, L2 switchdev offload
- ASK 1.x oracle proved bridge offload works WITHOUT `auto_bridge.ko`
- Small `ask_bridge.c` — register switchdev notifier, populate FMan PCD for L2 FDB
- Estimated: ~400 LOC

### 10. ask_xfrm.c HW IPsec ❌ STUB (1 KB)
**Patches:** `0134` (CAAM QI share)
- CAAM descriptor-sharing API landed (`caam_qi_ext_consumer_register`)
- 129 CAAM-backed algorithms in `/proc/crypto`
- ESP packet-mode offload NOT advertised on FMan netdevs
- **Implementation:** `xfrmdev_ops` (xdo_dev_state_add/delete), CAAM descriptor lifecycle
- **Algorithm:** Refuse GCM (wire-seq race), target `authenc(hmac(sha256),cbc(aes))`

### 11. Operator CLI ❌ NOT STARTED
**Target:** Phase 5
- `set system offload ask [interface ethN]`
- Op-mode `show offload ask flows` via `ynl --family ask`
- Commit-time validator: ASK↔VPP global mutual exclusion
- Estimated: ~1200 LOC VyOS Python

### 12. Conntrack Integration ❌ NOT STARTED
**Target:** Phase 2 measurement prerequisite
- ASK 1.x oracle finding: VyOS notrack blocks flow promotion
- `nf_flow_table` requires ASSURED state before FLOW_CLS_REPLACE delivery
- Install conntrack-touching rule before M2 gate measurement:
  ```nft
  nft add rule inet filter forward ct state established,related accept
  ```

---

## Critical Path

```
 1 ✅  2 ✅  3 ✅  4 ✅      5 ⏳       8 ⏳       6.2 ✅   7 ✅
 PCD → FE  → ARM → CTX  →  HIT     →  WIRE   →  M2 GATE
                          (key)      (ask.ko)   ≥2 Gbps
                                                ≤5% CPU

                                            ↓
                         9 ❌  10 ❌  11 ❌  12 ❌
                      BRIDGE  IPSEC  CLI   CONNTRACK
                      Phase3  Phase4 Phase5 Phase2-prereq
```

**Only 3 items remain on the silicon-critical path:**
1. HIT path verification (need KeyGen extraction template)
2. Wire ask.ko to FE path (Phase 2)
3. M2 gate measurement (≥2 Gbps, ≤5% CPU)

All downstream work (bridge, IPsec, CLI) parallelizes once Phase 2 passes.
