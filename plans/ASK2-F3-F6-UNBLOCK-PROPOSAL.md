# ASK2 F3/F6 Unblock Proposal

**Date:** 2026-07-09  
**Branch:** dpaa1  
**Author:** Agent Session  
**Status:** DRAFT - Requires Evaluation

---

## Executive Summary

**CORRECTION (2026-07-09 21:50 UTC):** This proposal's factual foundation was one build behind. Cross-reference against Qdrant memories reveals:

1. **F6 engage/disengage is NOT universally broken** - it PASSED on ISO 2026.07.09-1516-rolling (commit 1f2dfc5, CI run 29028822691) with byte-clean reversibility
2. **F3 is NOT a kernel API gap** - the fixes are already committed (8d37d54 + 0b196d1); the real blocker is no traffic harness to drive conntrack to ASSURED state
3. **The "port goes deaf" symptom is a regression** introduced between ISO 1516 (passing) and ISO 2008 (failing), likely in commit 4300071 (TX bypass)
4. **Option A partially exists** as patch 0148 (chain builder exports, 135 lines) from 2026-07-04 on puddle-cornet branch

**Revised Approach:**
1. **First (30 min):** Bisect the regression by testing intermediate build 29038564474 (commit 4300071, TX bypass)
2. **If 4300071 is the regression:** Fix the TX bypass disengage path
3. **If not:** Investigate a109a70 (userspace) and f79c66d (genl commands)
4. **For Option A:** Resurrect/dedupe patch 0148 instead of authoring from scratch
5. **Drop Option C entirely:** F3 is a traffic harness problem, not a code problem

**Original Summary (STALE):**

This proposal addresses two critical blockers preventing ASK2 Phase 2 validation:

1. **F3 (nft flowtable offload):** ~~Kernel 6.18.38 nft flowtable implementation never invokes the offload setup callback~~ → CORRECTED: Fixes already committed, blocker is traffic harness
2. **F6 (throughput testing):** ~~Both available engage/disengage paths are broken~~ → CORRECTED: Debugfs path passed on ISO 1516, regression in ISO 2008

**Estimated Effort:** 30 min bisect + 2-4 hours fix (depending on root cause)

---

## Current State Assessment

### Completed Work

| Task | Status | Commit/Build |
|------|--------|--------------|
| F2: CI build + deploy | ✅ Complete | Build 29046830412, ISO 2026.07.09-2008-rolling |
| Fix vyos-offload-ask 12→16 byte keys | ✅ Complete | Commit a109a70 |
| Add genl engage/disengage commands | ✅ Complete | Commit f79c66d |
| DUT deployment | ✅ Complete | .185 running new image |

### Blocker Status

| Blocker | Impact | Root Cause |
|---------|--------|------------|
| F3: nft flowtable | Cannot test nft offload path | Kernel API gap - setup callback never invoked |
| F6: throughput test | Cannot validate ASK2 performance | No working engage/disengage path |

---

## Blocker Analysis

### F3: nft Flowtable Offload - CORRECTED

**Original Claim (STALE):** "Kernel API gap - setup callback never invoked"

**Correction:** The fixes are already committed:
- Commit 8d37d54: Added CONFIG_NF_FLOW_TABLE_OFFLOAD
- Commit 0b196d1: Added TC_SETUP_FT case to dpaa_setup_tc() via sed injection

**Real Blocker:** No traffic harness to drive conntrack to ASSURED state. Per Qdrant memory from today's Phase 2 test:

> "nft flowtable with flags offload created successfully, nf_flow_table_offload_setup symbol exists, flow_indr_dev_register is called at ask.ko init. BIND never fires because offload setup only triggers when a conntrack flow reaches ASSURED, and there are no traffic peers on 10.99.1.0/24 or 10.99.2.0/24. No bidirectional flows possible."

**Evidence:** The kprobe evidence (0 hits on setup) is consistent with "no ASSURED flows," not with an API gap.

**Solution:** Build a traffic harness on 10.99.1.0/24 to drive conntrack to ASSURED. This is NOT a code problem.

**Recommendation:** Drop Option C entirely. F3 investigation would burn 4-7 hours rediscovering this.

---

### F6: Throughput Testing - CORRECTED

**Original Claim (STALE):** "Both available engage/disengage paths are broken"

**Correction:** The debugfs path PASSED on ISO 2026.07.09-1516-rolling (commit 1f2dfc5):

> "Phase 2 engage/disengage test PASSED on DUT 192.168.1.185, branch dpaa1, commit 1f2dfc5, kernel 6.18.38, ISO 2026.07.09-1516-rolling (CI run 29028822691). Byte-clean reversibility: FE_ENTER root 0x56100→0x0, MURAM 42773→0, rccb restored to 0x00000000. Idempotency verified, double-engage guarded by p->offload_engaged."

**The "port goes deaf" symptom is a REGRESSION** introduced between ISO 1516 (passing) and ISO 2008 (failing).

**Delta between 1516 and 2008:**
1. Commit 4300071: F5 TX bypass (fman_port_set_silicon_hit_release_all)
2. Commit a109a70: 12→16 byte key validation (userspace only, no kernel change)
3. Commit f79c66d: genl engage/disengage commands (calls same ask_hw_offload_engage())

**Likely Culprit:** Commit 4300071 (TX bypass) is the only kernel change and the most likely cause.

**M2-§4 Fixes Already Present:** The three root causes fixed today (params page free, lookup_rx port_id=0, pool count 100→16) are in BOTH ISOs (they're ancestors of 1f2dfc5). So the regression is NOT a re-emergence of those bugs.

**Historical Rhyme:** PR14z18 (2026-05-23) documented the identical symptom family: "port permanently wedged after ungraft until reboot, suspected FMBM_RFPNE NIA / MV slot / leaked MURAM." The params-page leak is consistent with that unfinished investigation, but the fix is already in both ISOs.

**Immediate Action:** Bisect by testing intermediate build 29038564474 (commit 4300071, TX bypass only).

---

## Immediate Action: Bisect the Regression (30 min)

**Goal:** Identify which commit between 1516 and 2008 introduced the "port goes deaf" regression.

**Test Matrix:**

| Build | Commit | Key Change | Status |
|-------|--------|------------|--------|
| 29028822691 (1516) | 1f2dfc5 | Baseline | ✅ PASSED |
| 29038564474 (1752) | 4300071 | TX bypass | ❓ TEST NEXT |
| 29046830412 (2008) | f79c66d | + genl commands | ❌ FAILED |

**Procedure:**
1. Deploy intermediate ISO (2026.07.09-1752-rolling) to DUT
2. Test engage/disengage cycle
3. If FAIL: regression is in 4300071 (TX bypass) → fix disengage path
4. If PASS: regression is in a109a70 or f79c66d → investigate further

**Expected Outcome:** 4300071 is the most likely culprit (only kernel change in delta).

---

## Proposed Solutions (REVISED)

### Option A: Export FE-VM Builder APIs via Patch 0148 Resurrection (RECOMMENDED)

**Approach:** Resurrect and dedupe patch 0148 (chain builder exports, 135 lines) from 2026-07-04 on puddle-cornet branch, then rewire `ask_hw_offload_engage()` to use the exported APIs.

**Why Resurrect 0148:**
- Patch 0148 already exports the chain builder APIs (exactly what we need)
- It failed CI (run 28718101371) due to duplicate code from overlapping insertions
- Patch 0146 (context builder) is already exported and silicon-verified
- Starting from 0148 saves 2-3 hours vs greenfield implementation

**Advantages:**
- Clean, maintainable solution
- Eliminates debugfs bridge entirely
- Properly manages BMI port state
- Follows kernel module API best practices
- Unblocks F6 testing with confidence

**Disadvantages:**
- Requires deduplication effort (1-2 hours)
- Need to verify 0148 still applies cleanly to current codebase

**Implementation Plan:**

#### Step 1: Resurrect Patch 0148 (1 hour)

```bash
# Checkout puddle-cornet branch
git checkout puddle-cornet

# Find patch 0148
find kernel/common/patches/board -name "*0148*"

# Copy to dpaa1 branch
git checkout dpaa1
cp <path-to-0148> kernel/common/patches/board/

# Check for conflicts with current patches
ls kernel/common/patches/board/ | grep -E "014[0-9]|015[0-9]"
```

**Deliverable:** Patch 0148 copied to dpaa1 branch, conflicts identified.

#### Step 2: Deduplicate and Fix (1-2 hours)

Review patch 0148 for:
- Duplicate EXPORT_SYMBOL declarations
- Overlapping function definitions
- Conflicts with M2-§4 fixes (params page, lookup_rx, pool count)

Apply fixes:
- Remove duplicate exports
- Resolve function signature conflicts
- Ensure compatibility with current ask_hw.c

**Deliverable:** Clean, deduplicated patch 0148.

#### Step 3: Rewire ask_hw_offload_engage() (1 hour)

Replace debugfs bridge with direct API calls from patch 0148:

```c
int ask_hw_offload_engage(u8 hw_port_id)
{
    struct ask_hw_pcd *h = ask_hw_pcd_get();
    struct ask_hw_port *p;
    int rc;

    if (!h)
        return -ENODEV;

    mutex_lock(&h->lock);

    p = ask_hw_port_slot_get(h, hw_port_id);
    if (!p) {
        rc = -ENOSPC;
        goto out_unlock;
    }
    if (p->offload_engaged) {
        rc = 0;  // idempotent
        goto out_unlock;
    }

    // Build FE-VM pipeline using exported APIs from patch 0148
    rc = fman_pcd_fe_pool_get(h->fman->pcd, hw_port_id, 3);
    if (rc)
        goto out_unlock;

    rc = fman_pcd_fe_singletons_build(h->fman->pcd, hw_port_id);
    if (rc)
        goto out_pool_put;

    rc = fman_pcd_fe_ehash_set(h->fman->pcd, hw_port_id, 0x7FFF, 16, 0);
    if (rc)
        goto out_singletons_clear;

    rc = fman_pcd_fe_hashfe_build(h->fman->pcd, hw_port_id);
    if (rc)
        goto out_ehash_clear;

    rc = fman_pcd_fe_enq_build(h->fman->pcd, hw_port_id, 0x200);
    if (rc)
        goto out_hashfe_clear;

    rc = fman_pcd_fe_enter_build(h->fman->pcd, hw_port_id);
    if (rc)
        goto out_enq_clear;

    rc = fman_pcd_fe_arm(h->fman->pcd, hw_port_id, 0x59200);
    if (rc)
        goto out_enter_clear;

    // TX bypass (from commit 4300071) - CRITICAL: include this!
    fman_port_set_silicon_hit_release_all(h->fman, true);

    p->offload_engaged = true;
    ask_pr_info("hw: offload ENGAGED on port 0x%02x (S0->S1)\n", hw_port_id);

    mutex_unlock(&h->lock);
    return 0;

out_enter_clear:
    fman_pcd_fe_enter_clear(h->fman->pcd, hw_port_id);
out_enq_clear:
    fman_pcd_fe_enq_clear(h->fman->pcd, hw_port_id);
out_hashfe_clear:
    fman_pcd_fe_hashfe_clear(h->fman->pcd, hw_port_id);
out_ehash_clear:
    fman_pcd_fe_ehash_clear(h->fman->pcd, hw_port_id);
out_singletons_clear:
    fman_pcd_fe_singletons_clear(h->fman->pcd, hw_port_id);
out_pool_put:
    fman_pcd_fe_pool_put(h->fman->pcd, hw_port_id);
out_unlock:
    mutex_unlock(&h->lock);
    return rc;
}
```

**Key improvements:**
- Proper error handling with rollback
- No debugfs dependency
- Clean API usage from patch 0148
- **Includes TX bypass** (fman_port_set_silicon_hit_release_all) - this was missing from the original proposal!

**Deliverable:** Updated `ask_hw.c` with proper API usage.

#### Step 4: Test and Validate (1 hour)

1. Build kernel with patch 0148
2. Build ask.ko module
3. Deploy to DUT
4. Test genl engage/disengage
5. Verify BMI port state is properly restored
6. Run F6 throughput test

**Success criteria:**
- Genl engage/disengage works without errors
- Port remains functional after disengage (no reboot required)
- F6 throughput test achieves spec gate: **≥2 Gbps / ≤5% CPU** (7 Gbps is stretch target)

**Deliverable:** Test results and validation report.

---

### Option B: Fix TX Bypass Regression (If Bisect Confirms 4300071)

**Approach:** If the bisect confirms commit 4300071 (TX bypass) is the regression, fix the disengage path to properly restore TX state.

**Advantages:**
- Targeted fix for the specific regression
- Faster than Option A (1-2 hours vs 4-6 hours)
- No kernel patches required (just fix ask_hw.c)

**Disadvantages:**
- Still relies on debugfs bridge (not ideal for production)
- Doesn't address the long-term API export need

**Implementation Plan:**

#### Step 1: Analyze TX Bypass Disengage (30 min)

Review commit 4300071 to understand:
- What `fman_port_set_silicon_hit_release_all(h->fman, true)` does
- What `fman_port_set_silicon_hit_release_all(h->fman, false)` should restore
- Whether the restore is complete

Check dmesg for errors during disengage:
```bash
sudo dmesg | grep -E "fman_port|silicon_hit_release"
```

**Deliverable:** Root cause analysis of TX bypass disengage failure.

#### Step 2: Fix Disengage Path (1 hour)

Based on analysis, fix the disengage path in `ask_hw_offload_disengage()`:

```c
void ask_hw_offload_disengage(u8 hw_port_id)
{
    struct ask_hw_pcd *h = ask_hw_pcd_get();
    struct ask_hw_port *p;

    if (!h)
        return;

    mutex_lock(&h->lock);

    p = ask_hw_port_slot_get(h, hw_port_id);
    if (!p || !p->offload_engaged) {
        mutex_unlock(&h->lock);
        return;
    }

    // Disarm FE-VM via debugfs bridge
    debugfs_fe_write("fe_arm", "disengage", 9);
    debugfs_fe_write("fe_enter", "clear", 5);
    debugfs_fe_write("fe_enq", "clear", 5);
    debugfs_fe_write("fe_hashfe", "clear", 5);
    debugfs_fe_write("fe_ehash", "clear", 5);
    debugfs_fe_write("fe_singletons", "clear", 5);
    debugfs_fe_write("fe_pool", "put", 3);

    // Disengage CC tree via API
    fman_pcd_offload_disengage(h->fman, hw_port_id);

    // Reverse TX bypass (from commit 4300071)
    fman_port_set_silicon_hit_release_all(h->fman, false);

    // NEW: Verify port state is restored
    // Check if BMI port registers are back to S0 state
    // If not, add explicit restore logic here

    p->offload_engaged = false;
    mutex_unlock(&h->lock);
    ask_pr_info("hw: offload DISENGAGED on port 0x%02x (S1->S0)\n", hw_port_id);
}
```

**Key additions:**
- Verification that port state is restored
- Explicit restore logic if needed

#### Step 3: Test and Validate (30 min)

1. Deploy fix to DUT
2. Test engage/disengage cycle
3. Verify port remains functional
4. Run F6 throughput test

**Success criteria:**
- Engage/disengage works without errors
- Port remains functional after disengage (no reboot required)
- F6 throughput test achieves spec gate: **≥2 Gbps / ≤5% CPU**

**Deliverable:** Updated `ask_hw.c` and test results.

---

### Option C: Investigate F3 (nft flowtable) - DROPPED

**Status:** DROPPED per revised recommendation.

**Rationale:**
- F3 is NOT a kernel API gap (fixes already committed)
- Real blocker is traffic harness, not code
- Investigation would burn 4-7 hours rediscovering this
- F6 should be prioritized (validates ASK2 performance)

**When to revisit:**
- After F6 is unblocked and validated
- When traffic harness is available
- If nft integration becomes a hard requirement

---

## Risk Assessment (REVISED)

### Option A: Resurrect Patch 0148

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Patch 0148 has unresolvable conflicts | Low | High | Review puddle-cornet branch history for context |
| Deduplication introduces new bugs | Medium | High | Careful code review, test each API individually |
| Exported APIs break other kernel code | Low | High | Use EXPORT_SYMBOL_GPL, test with other modules |
| BMI state still not properly managed | Low | High | M2-§4 fixes already address this, verify in testing |
| Kernel patch rejected by upstream | High | Low | Keep as out-of-tree patch, document rationale |

### Option B: Fix TX Bypass Regression

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Bisect shows regression is NOT in 4300071 | Low | Medium | Test intermediate build first, adjust plan |
| TX bypass disengage has hidden side effects | Medium | High | Add verification checks, compare S0 state |
| Fix doesn't address all corruption paths | Low | High | Comprehensive testing, multiple engage/disengage cycles |
| Debugfs bridge remains a maintenance burden | High | Medium | Plan to migrate to Option A later |

### Option C: Investigate F3 - DROPPED

**Status:** DROPPED. Risk assessment no longer applicable.

**Rationale:** F3 is a traffic harness problem, not a code problem. Investigation would waste 4-7 hours.

---

## Decision Points (REVISED)

### Decision 1: Bisect First (MANDATORY)

**Question:** Should we bisect the regression before choosing Option A or B?

**Answer:** YES - this is mandatory per revised recommendation.

**Rationale:**
- The "port goes deaf" symptom is a regression, not a longstanding defect
- Debugfs path PASSED on ISO 1516, FAILED on ISO 2008
- Bisect will identify the exact commit (likely 4300071 TX bypass)
- Saves time by targeting the fix instead of guessing

**Action:** Test intermediate build 29038564474 (commit 4300071) immediately.

### Decision 2: Option A vs Option B (After Bisect)

**Question:** Should we resurrect patch 0148 (Option A) or fix the TX bypass regression (Option B)?

**Recommendation:** Depends on bisect result:
- **If 4300071 is the regression:** Option B (faster, targeted fix)
- **If regression is elsewhere:** Option A (cleaner long-term solution)

**Rationale:**
- Option B is faster (1-2 hours) if the regression is isolated to TX bypass
- Option A is cleaner (4-6 hours) but requires more effort
- Both options are viable; choose based on bisect result

**When to choose Option A regardless:**
- If we want to eliminate debugfs bridge entirely
- If we anticipate needing more FE-VM APIs in the future
- If patch 0148 resurrects cleanly (low dedup effort)

### Decision 3: F3 vs F6 Priority (RESOLVED)

**Question:** Should we investigate F3 (nft flowtable) before or after F6 (throughput test)?

**Answer:** F6 first, F3 dropped entirely.

**Rationale:**
- F3 is NOT a kernel API gap (fixes already committed)
- F3 blocker is traffic harness, not code
- F6 validates ASK2 performance (core value proposition)
- F3 investigation would waste 4-7 hours

**When to revisit F3:**
- After F6 is unblocked and validated
- When traffic harness is available
- If nft integration becomes a hard requirement

### Decision 4: Scope of FE-VM API Export (If Option A)

**Question:** Should we export all FE-VM builder functions or create a higher-level API?

**Recommendation:** Use patch 0148 as-is (individual builder functions).

**Rationale:**
- Patch 0148 already exports individual functions
- More flexible (can be composed in different ways)
- Follows existing kernel patterns (e.g., netfilter APIs)
- Saves time vs designing a new high-level API

**Alternative:** Create `fman_pcd_fe_vm_engage(port_id, config)` and `fman_pcd_fe_vm_disengage(port_id)`

**When to choose higher-level API:**
- If patch 0148 has unresolvable conflicts
- If we want to enforce a specific usage pattern
- If individual functions are too granular

---

## Implementation Timeline (REVISED)

### Phase 0: Bisect the Regression (30 min) - MANDATORY FIRST STEP

| Step | Duration | Deliverable |
|------|----------|-------------|
| Deploy intermediate ISO (1752) | 10 min | DUT running ISO 2026.07.09-1752-rolling |
| Test engage/disengage | 10 min | Pass/fail result |
| Analyze result | 10 min | Root cause identified |
| **Total** | **30 min** | **Regression commit confirmed** |

### Option A Timeline (If Bisect Points Away from 4300071)

| Phase | Duration | Deliverable |
|-------|----------|-------------|
| Step 1: Resurrect patch 0148 | 1 hour | Patch copied to dpaa1, conflicts identified |
| Step 2: Deduplicate and fix | 1-2 hours | Clean, working patch 0148 |
| Step 3: Rewire engage | 1 hour | Updated ask_hw.c with API calls |
| Step 4: Test and validate | 1 hour | Test results |
| **Total** | **4-5 hours** | **F6 unblocked** |

### Option B Timeline (If Bisect Confirms 4300071)

| Phase | Duration | Deliverable |
|-------|----------|-------------|
| Step 1: Analyze TX bypass disengage | 30 min | Root cause analysis |
| Step 2: Fix disengage path | 1 hour | Updated ask_hw.c |
| Step 3: Test and validate | 30 min | Test results |
| **Total** | **2 hours** | **F6 unblocked** |

### Option C Timeline - DROPPED

**Status:** DROPPED per revised recommendation. F3 is a traffic harness problem.

---

## Success Criteria (REVISED)

### F6 Success Criteria (CORRECTED)

**Spec Gate (MUST PASS):**
1. **Engage/disengage works reliably**
   - No errors during engage/disengage
   - Port remains functional after disengage
   - No reboot required to recover

2. **Throughput meets spec gate**
   - **≥2 Gbps throughput** (spec gate, not 7 Gbps!)
   - **≤5% CPU utilization** (spec gate)
   - 0 packet loss
   - 0 retransmits

3. **Hardware offload is active**
   - `pcd-snapshot` shows FE-VM pipeline is engaged
   - `fe_flow` shows flows are inserted
   - `ethtool -S` shows hardware counters incrementing

**Stretch Target (NICE TO HAVE):**
- ≥7 Gbps throughput (stretch target, not spec gate)
- Historical data: SW flowtable path tops out at 6.3-6.9 Gbps at 19-70% CPU
- ≤5% CPU is only achievable on the HW path

**Correction:** The original proposal stated "≥7 Gbps / ≤5% CPU" as the target. Per Qdrant memory, the spec M2 gate is ≥2 Gbps + ≤5% CPU, with 7 Gbps as stretch. The proposal should not present 7 Gbps as the pass bar.

### F3 Success Criteria (Future - DROPPED FOR NOW)

**Status:** DROPPED per revised recommendation. F3 is a traffic harness problem.

**When to revisit:**
1. **nft flowtable triggers offload**
   - `nft add flowtable ... flags offload` works
   - Setup callback is invoked
   - Flows are offloaded to hardware

2. **Forwarding continues to work**
   - Traffic not matching offloaded flows still forwards
   - No packet loss for non-offloaded traffic

3. **Integration with ASK2**
   - Offloaded flows use ASK2 hardware path
   - Performance matches manual fe_flow insertion

---

## Conclusion (REVISED)

**Recommended Path:** Bisect first, then choose Option A or B based on result

**Rationale:**
- The "port goes deaf" symptom is a regression, not a longstanding defect
- Debugfs path PASSED on ISO 1516, FAILED on ISO 2008
- Bisect will identify the exact commit (likely 4300071 TX bypass)
- Saves time by targeting the fix instead of guessing

**Revised Next Steps:**
1. **MANDATORY:** Run Test 4 (bisect regression) - 30 min
2. If 4300071 is the regression: proceed with Option B (fix TX bypass) - 2 hours
3. If regression is elsewhere: proceed with Option A (resurrect patch 0148) - 4-5 hours
4. Test and validate
5. Proceed to F6 throughput testing

**Key Corrections from Original Proposal:**
- F3 is NOT a kernel API gap (fixes already committed) - DROPPED
- F6 engage/disengage is NOT universally broken - regression in ISO 2008
- Option A partially exists as patch 0148 - resurrect, don't author from scratch
- F6 pass bar is ≥2 Gbps / ≤5% CPU (spec gate), not ≥7 Gbps
- 16-byte key derivation is a required step (commit a109a70 changed from 12 to 16 bytes)

**Fallback Plan:**
- If bisect is inconclusive, proceed with Option A (cleaner long-term)
- If Option A encounters unexpected complexity, switch to Option B
- If time is critical, implement Option B first, then Option A later

---

## Appendix A: Technical Details

### FE-VM Pipeline Components

1. **fe_pool**: Memory pool for FE objects
2. **fe_singletons**: Singleton FE objects (shared across flows)
3. **fe_ehash**: External hash table for flow lookup
4. **fe_hashfe**: Hash frontend (hash function configuration)
5. **fe_enq**: Enqueue configuration (target FQ for HIT flows)
6. **fe_enter**: Entry point configuration (where packets enter FE-VM)
7. **fe_arm**: Arm/disarm the FE-VM pipeline

### KeyGen Scheme States

1. **RSS_HASH**: Default state, packets distributed by RSS hash
2. **AC_CC**: ASK2 state, packets classified by CC tree
3. **Transition**: RSS_HASH → AC_CC (engage), AC_CC → RSS_HASH (disengage)

### BMI Port State

BMI (Buffer Manager Interface) manages packet buffers and queues. Key state:
- Port configuration (MTU, pause frames, etc.)
- Queue configuration (FQ IDs, scheduling)
- Statistics counters

**Corruption symptoms:**
- Link UP but no packets received
- Statistics show 0 RX packets
- Only reboot recovers

---

## Appendix B: Test Procedures

### Test 1: Engage/Disengage Cycle

```bash
# Baseline
ping -c 5 10.99.1.106
# Expected: 0% packet loss

# Engage
sudo vyos-offload-ask engage eth3
# Expected: Success message

# Disengage
sudo vyos-offload-ask disengage eth3
# Expected: Success message

# Verify
ping -c 5 10.99.1.106
# Expected: 0% packet loss (no reboot required)
```

### Test 2: F6 Throughput (CORRECTED)

**Step 2a: Derive flow key (RESOLVED 2026-07-13)**

Two paths use different EKFC values and key widths. The earlier "lowest-bit-first" hypothesis (L4PDST-first) was disproven by CRC-64 hash-match on hardware.

**CC tree path** (EKFC=0x00180206, 16 bytes): SIP(4B)+DIP(4B)+SPI(4B)+SPORT(2B)+DPORT(2B). SPI is always zero for plain IP. This is the `cc_pack_key()` layout from patch 0108.

**ehash path** (EKFC=0x001C0006, 13 bytes): SIP(4B)+DIP(4B)+PROTO(1B)+SPORT(2B)+DPORT(2B). This is the FE-VM flow-insert path using `fman_pcd_crc64_raw()`. Extraction order is MSB-first (descending EKFC bit position), confirmed by CRC-64 hash-match against two independent TCP flows on 2026-07-13.

**DDR flow record layout:** 8-byte header (flags+next_ptr) at offset 0, key bytes at offset 8, ENQ FE MURAM offset after aligned key region. For 13-byte keys: DDR record header=8B, key at +8, ENQ FE ptr at +24.

**Step 2b: Setup forwarder topology**

```bash
bash /tmp/setup_forwarder.sh
```

**Step 2c: Baseline throughput (no ASK)**

```bash
iperf3 -c 10.99.3.106 -B 10.99.1.185 --port 5201 -t 5 -P 4
# Expected: ~6.5 Gbps (SW path baseline)
```

**Step 2d: Engage ASK**

```bash
sudo vyos-offload-ask engage eth3
# Expected: Success, no errors
```

**Step 2e: Insert flows with 16-byte key**

```bash
# Use the 16-byte key derived in Step 2a
sudo vyos-offload-ask flow-add <16-byte-key> 2b9
# Expected: Success, flow inserted
```

**Step 2f: Throughput with ASK**

```bash
iperf3 -c 10.99.3.106 -B 10.99.1.185 --port 5201 -t 5 -P 4
# Expected: ≥2 Gbps (spec gate), ≤5% CPU
# Stretch: ≥7 Gbps
```

**Step 2g: Disengage**

```bash
sudo vyos-offload-ask disengage eth3
# Expected: Success, port remains functional
```

### Test 3: Genl Commands

```bash
# Test engage
sudo python3 /tmp/test_genl_engage.py engage 0x10
# Expected: SUCCESS message

# Verify
sudo dmesg | grep "ask:.*genl:.*engage"
# Expected: "genl: engaged port 0x10"

# Test disengage
sudo python3 /tmp/test_genl_engage.py disengage 0x10
# Expected: SUCCESS message

# Verify port functional
ping -c 5 10.99.1.106
# Expected: 0% packet loss
```

### Test 4: Bisect Regression (NEW - MANDATORY FIRST)

**Step 4a: Deploy intermediate ISO**

```bash
# Download intermediate build (commit 4300071, TX bypass only)
gh run download 29038564474 --dir /tmp/intermediate

# Deploy to DUT
# (Use standard deployment procedure)
```

**Step 4b: Test engage/disengage**

```bash
# Run Test 1 (engage/disengage cycle)
# Record: PASS or FAIL
```

**Step 4c: Analyze result**

- **If FAIL:** Regression is in commit 4300071 (TX bypass) → proceed with Option B
- **If PASS:** Regression is in a109a70 or f79c66d → investigate further

**Deliverable:** Confirmed regression commit.

---

**End of Proposal**
