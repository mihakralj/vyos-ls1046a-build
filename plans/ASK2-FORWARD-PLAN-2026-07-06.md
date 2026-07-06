# ASK2 Forward Plan — 2026-07-06

**Status:** Course correction in progress. Architecture confirmed.
**Branch:** `dpaa1`  
**Latest CI build:** #28801091044 (`vyos-2026.07.06-1459-rolling`, CCBS scaffold deployed)  
**Board:** 192.168.1.185, kernel 6.18.36-vyos

---

## 1. Architecture Confirmation

### 1.1 Fork B + AC_CC is the correct path

| Evidence | Source |
|----------|--------|
| Vendor dpa_app programs 12 KG schemes all in AC_CC mode (`kgse_mode = 0x8X000006`) | `files/vendor-kgall.txt`, qdrant |
| CVAN 25.12.4 / vendor oracle confirms FE/ehash on main-port KG schemes | qdrant |
| Phase 1 proven: AC_CC engage works (scheme→CC(AC_CC), reversibility clean) | 2026-07-04/05/06 DUT tests |
| Fork A (CONT_LOOKUP exact-match) DEAD on 210.10.1 — frames park with zero latched fault | iter-49/50, qdrant |
| Fork B (FE/ehash) is the only configuration proven to flow on this silicon | ASK2-DEVELOPMENT-PLAN §1 |

### 1.2 CCBS vs AC_CC — resolved

| Mechanism | next_engine | Root | Used in | Status |
|-----------|-------------|------|---------|--------|
| **CCBS graft** | 2 | KGSE_CCBS≠0 | ask20 Fork A (match-node CC trees) | ✅ Proven on ask20 (24M+ frames) — but Fork A is DEAD |
| **AC_CC dispatch** | 3 | RCCB (fmbm_rccb) | Vendor reference + our Phase 1 | ✅ Proven engages, reversibility clean — CORRECT for Fork B |
| **Our CCBS scaffold** | 2 | RCCB | Current build (#1459) | ❌ Wrong dispatch mode for Fork B FE/ehash |

**Decision: Revert to AC_CC mode** (next_engine=3). Keep the CONT_LOOKUP AD scaffold format (it's correct per RM 8.7.4.1).

### 1.3 Why the CCBS scaffold is the wrong dispatch for Fork B

The CCBS graft (ask20) routes frames through CC match-node trees → AD → action. Fork B (FE/ehash) routes frames through KG→CC→FE_ENTER→hashfe→ehash→flow→ENQ. These are different dispatch paths. The AC_CC mode is what triggers the CC engine to walk the tree from RCCB, which we set to the group table containing the CONT_LOOKUP AD. The CONT_LOOKUP AD then dispatches to FE_ENTER.

---

## 2. Current Blocker: Correct AC_CC dispatch with proper group table format

### 2.1 What we have (build #1459)
- next_engine=2 (CCBS) — **WRONG**, should be 3 (AC_CC)
- CONT_LOOKUP AD scaffold (group table + match table + AD table) — **CORRECT format**
- AD entries encode ENQ to FQ 0x200 — **to be verified**
- KGSE_CCBS=group_table_offset, RCCB also written

### 2.2 What we need
- next_engine=3 (AC_CC), KGSE_CCBS=0
- RCCB = group_table_offset (the CONT_LOOKUP AD location)
- KGSE_MODE = FM_CTL|AC_CC (0x80000006) — triggers CC engine dispatch
- CONT_LOOKUP AD scaffold — same as current format
- AD entries encode ENQ to a valid QMan FQ (verify FQ 0x200 works)

### 2.3 QMan errors — root cause confirmed
The old scaffold (simple flags+next_ptr group entry) decoded as RESULT_CF fqid=0 per RM 8.7.4.1, causing `ecir.fqid 0x0` QMan errors. The new CONT_LOOKUP AD format should fix this. The remaining error source may be:
- FQ 0x200 not valid for direct FE enqueue (needs per-CPU FQID)
- ERN (Error Recovery Notification) destination not configured
- Context builder (0135) not called — FE_ENTER may have uninitialized state

---

## 3. Immediate Actions (today)

### A1. Fix dispatch mode: AC_CC (next_engine=3) + CONT_LOOKUP AD scaffold
- **File:** `kernel/common/patches/board/0132-fman-pcd-fe-arm-debugfs.patch`
- **Change:** `slot->next_engine = 2` → `slot->next_engine = 3`
- **Change:** `slot->cc_bits_sel = fe_enter_off` → `slot->cc_bits_sel = 0`
- **Keep:** CONT_LOOKUP AD scaffold (256B group + 16B match + 32B AD)
- **Keep:** AD entries encoding ENQ to FQ 0x200
- **CI build → deploy → test on DUT**

### A2. Verify CONT_LOOKUP AD fixes QMan errors
- Arm with AC_CC + CONT_LOOKUP AD
- Check dmesg: QMan errors should NOT appear (no `ecir.fqid 0x0`)
- If errors persist → debug FQ encoding in AD entries

### A3. Traffic test: verify frames reach kernel
- With AC_CC armed + CONT_LOOKUP AD → ping lxc200 from DUT
- Frames should route: eth3 RX → KG(AC_CC) → CC engine → group table → CONT_LOOKUP → match(miss) → AD(ENQ 0x200) → QMan → kernel → response
- If ping works → ENQ FQ 0x200 is valid → **AC_CC dispatch PROVEN**

### A4. If ping fails → debug ENQ FQ
- Try different ENQ FQIDs (0x80, 0x100, 0x180, 0x300)
- Try per-CPU FQID (0x200 + CPU_offset)
- Try BMI direct enqueue NIA instead of QMan FQ

---

## 4. Phase 2 Completion (this week)

### P2.1 Flow HIT test
- Insert flow key (DPORT=0x1451 for TCP:5201)
- Arm with AC_CC + CONT_LOOKUP AD
- iperf3 from lxc200 → DUT → lxc201
- Verify flow HIT counter increments
- Verify throughput ≥ 2 Gbps

### P2.2 TX bypass (0136) integration
- Call `fman_port_set_silicon_hit_release_mode()` before arm
- HIT frames bypass QMan directly to TX confirmation
- Eliminates QMan enqueue bottleneck

### P2.3 M2 gate
- ≥ 2 Gbps iperf3 throughput
- ≤ 5% CPU (kernel + softirq)
- pcd-snapshot byte-clean reversibility
- ask-check: 29/32 OK

---

## 5. Phase 3–6 (2–4 weeks)

### Phase 3: ask.ko drives FE path + flow population
- Wire `ask_hw.c` engage → FE chain build + AC_CC arm
- Wire `ask_flow_offload.c` BIND/REPLACE/DESTROY → fe_flow insert/delete
- Flow types: IPv4 5-tuple, L2 bridge, multicast

### Phase 4: HW IPsec (CAAM QI)
- Port `0001-caam-qi-share-descriptors.patch` to common tree
- Wire `ask_xfrm.c` via `xfrmdev_ops`
- ESP SA program → CAAM job ring → FMan FQ egress

### Phase 5: Operator CLI + VPP mutual exclusion
- `set system offload ask` — engages ask.ko
- `set system offload ask` ↔ `set vpp settings` mutual exclusion
- `show system offload` — stats, flow table, HW counters

### Phase 6: Soak testing
- 24h iperf3 with periodic arm/disarm cycles
- 100× engage/disengage with pcd-snapshot byte-clean
- ASK+VPP runtime switch → verify mutual exclusion

---

## 6. Technical Debt

| Item | Priority | Effort |
|------|----------|--------|
| Remove 0148 KG debug log (pr_info every scheme setup) | Low | 1 line |
| CC scaffold MURAM leak (304B per engage, no free on disarm) | Medium | ~30 LOC |
| Context builder integration (0135 → arm engage, blocked by prototype warnings) | Medium | ~50 LOC |
| Remove 0133 AC_CC defines from keygen_scheme_setup (now unused with CCBS correction? No — AC_CC is correct) | N/A | Keep 0133 |
| Fix 0146 build_contexts for future use (Wmissing-prototypes, Wunused-function) | Low | ~20 LOC |
| SFP-10G-T PHY cold-boot workflow (destabilizes on BMI writes) | Known issue | Documented |

---

## 7. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| CONT_LOOKUP AD still causes QMan errors | Medium | Blocked M2 | Debug FQ encoding, try BMI direct enqueue |
| ENQ FQ 0x200 not valid for AC_CC dispatch | Medium | Frames dropped | Use TX bypass (0136), find valid per-CPU FQ |
| SFP-10G-T PHY destabilizes | High | Lost test time | Cold boot per test cycle |
| 0146 context builder needed but can't compile | Medium | FE_ENTER uninitialized | Fix prototype, embed call in 0132 |
| ask20 CCBS lesson misinterpreted | Low | Using wrong dispatch | Vendor reference confirms AC_CC is correct for Fork B |

