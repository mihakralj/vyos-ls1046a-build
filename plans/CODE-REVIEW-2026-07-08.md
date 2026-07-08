# ASK2/v5.22 Comprehensive Code Review
**2026-07-08 · dpaa1 branch · commit 6d8da13 · 92 board patches · 12 OOT sources · 22+ CI builds**

## AI Reading Instructions
This is a segmented review document. Each section addresses a specific subsystem.
Agents working on individual fixes should read only their assigned section(s) —
the baseline §Context provides the common reference. All findings are classified:

- 🔴 **CRITICAL** — data corruption, memory leak, kernel panic risk
- 🟠 **HIGH** — silent failure with user-visible impact, stack-corruption risk
- 🟡 **MEDIUM** — correctness issue, missing error handling, undocumented invariants
- 🟢 **LOW** — cosmetic, documentation, minor optimization

Each finding has a unique `F-XXX` identifier for cross-referencing.

---

## §1 Context — Architecture Baseline

### 1.1 Specs Version
- **ask2-rewrite-spec.md**: v1.8 (S0/S1/S2 states, reversibility contract, MURAM iomem only)
- **dpai1-afxdp-modernization-spec.md**: v5.22 (20 xsk_* counters, 64 BPID cap, 64 KiB MURAM)
- **DUAL-DATAPLANE.md**: v1.1 (flavor collapsed 2026-06-14, single ISO)
- **ASK2-DEVELOPMENT-PLAN.md**: v1.1.0 (Phases 0-4 DOM/IN-PROGRESS)
- **ASK2-FORWARD-PLAN-2026-07-06.md**: 2026-07-08 update (M1 PASS, M2 PASS, P4.1 COMPILED)

### 1.2 Patch Inventory
| Category | Count | Key ranges |
|----------|-------|------------|
| PCD subsystem (KeyGen, CC, HM, PLCR, FE-VM) | 26 | 0092-0150 |
| AF_XDP true-ZC datapath | 18 | 0070-0114 |
| CEETM egress shaping | 3 | 0111-0112, 0104b |
| tc offload / policer bridge | 4 | 0101-0109 |
| ASK2 flavor/mode scaffolding | 5 | 0068-0069a, 0121, 0129 |
| Platform fixes (SFP, INA, phylink) | 6 | 101, 4002-4009 |
| Flow offload backend | 1 | 0145 |
| CAAM QI share | 1 | 0134 |

### 1.3 OOT Module (ask.ko v2.1.0)
- 7 real files (~3,800 LOC), 6 stub files (~130 LOC), 36 EXPORT_SYMBOL_GPL
- 5 forward-declared externs against board-substrate (no header — stack-corruption risk, see F-004)
- Largest: ask_flow_offload.c (1,969 LOC) — deferred-insert + indr + neigh resolution
- ask_hw.c (1,043 LOC) — cookie xarray, QMan FQ, debugfs bridge, flow insert/remove

### 1.4 Critical Invariants (from specs)
1. **Reversibility Contract** (§3.5 DUAL-DATAPLANE): every forward write ships with verified inverse
2. **MURAM is iomem**: access exclusively via memset_io/memcpy_toio/writel/readl
3. **gen_pool does not zero**: callers must own zero-before-use
4. **64 KiB PCD MURAM reservation** — every leak permanently reduces capacity
5. **pcd-snapshot diff, not ping** — is the gate for reversibility verification
6. **BPID=0 is default pool** on FMan v3 (discard-after-TX for ENQ FE)
7. **Characterize new paths with pings, never floods** (watchdog-reset risk)

---

## §2 Kernel Board Patches — PCD Subsystem
**Reviewer: Kernel PCD subagent · Files: 0092-0150 · 26 patches**

### F-001 🔴 CRITICAL — 0132 MURAM allocation error truncated by u32
**File:** `0132-fman-pcd-fe-arm-debugfs.patch`, fman_pcd_fe_arm_write()  
**Issue:** `fman_pcd_muram_alloc()` returns `unsigned long` (64-bit). Failure returns `(unsigned long)-ENOMEM = 0xFFFFFFFFFFFFFFF4`. The return is stored in `u32 gro`, truncating to `0xFFFFFFF4`. The guard `if (gro && mto && ato)` sees non-zero and proceeds to use `0xFFFFFFF4` as MURAM offsets, writing to garbage locations.  
**Impact:** Silent memory corruption on any MURAM allocation failure during engage.  
**Fix:** Change `u32 gro, mto, ato;` to `unsigned long gro, mto, ato;` and guard with `if (!IS_ERR_VALUE(gro) && !IS_ERR_VALUE(mto) && !IS_ERR_VALUE(ato))`.

### F-002 🔴 CRITICAL — 0132 MURAM scaffold never freed on disengage
**File:** `0132-fman-pcd-fe-arm-debugfs.patch`, fman_pcd_fe_arm_engage()  
**Issue:** Every `engage` allocates 304 bytes MURAM (256 + 16 + 32) for CCBS group/match/AD tables. These are stored in local variables only — never saved to `struct fman_pcd`. `Disengage` at lines 178-180 calls `scheme_restore` but never frees the three allocations. After ~215 engage/disengage cycles the 64 KiB PCD reservation is exhausted.  
**Impact:** Reversibility contract violated. MURAM leak renders the M1 soak gate unreliable.  
**Fix:** Add `unsigned long fe_arm_off, fe_arm_match_off, fe_arm_ad_off;` to `struct fman_pcd`. Add `fman_pcd_fe_arm_free()` helper called from disengage that frees all three in reverse allocation order.

### F-003 🟠 HIGH — 0097 KG scheme double-failure leaves slot→used=false
**File:** `0097-fman-pcd-keygen.patch`, port_attach_policer() / port_attach_cc()  
**Issue:** Error path sets `slot->used = false`, then calls `keygen_scheme_setup()` to restore prior engine state. If restoration ALSO fails, `slot->used` stays false while the scheme is still bound to hardware. A subsequent `kg_alloc_scheme_id()` may re-allocate this scheme while it's still routing frames.  
**Impact:** Two consumers simultaneously owning one KG scheme → misrouted frames, potential watchdog reset.  
**Fix:** Save `slot->used` before any mutation, restore it on ALL error paths including double-failure. Use temporary `struct keygen_scheme` copy for the restore attempt.

### F-004 🟡 MEDIUM — 0097 kg_find_port_scheme finds any bound scheme, not specifically RSS
**File:** `0097-fman-pcd-keygen.patch`, kg_find_port_scheme() lines 823-836  
**Issue:** Returns lowest-id scheme bound to a port. If a higher-priority explicit-match scheme exists, policer/CC attach rewrites the wrong scheme. Currently works because only RSS is bound before these calls, but no API guard prevents misuse.  
**Fix:** Add assertion `WARN_ON(scheme->match_vector != 0)` or return `-EINVAL` if found scheme has non-zero `kgse_mv`.

### F-005 🟡 MEDIUM — 0124 ENQ FE word2=0 — undefined BPID semantics
**File:** `0124-fman-pcd-fe-vm-singletons.patch`, fman_pcd_fe_build() ENQ case  
**Issue:** The ENQ FE descriptor sets `w[1]=FQID` and `w[3]=next_fe_off` but leaves `w[2]=0`. On FMan v3, word2 carries BPID for buffer return after TX or context-pointer for per-flow stats. BPID=0 means "default pool" — which is correct for TX-bypass (no new buffer allocation) but has never been on-wire tested with the dormant pathway.  
**Fix:** Add comment documenting that word2=0 means "no per-FE context, default BPID (silicon uses port's pre-allocated RX buffer pool for enqueue)". Document in `fe_enq` debugfs show handler.

### F-006 🟡 MEDIUM — 0115 CC group-table LCL_MASK collides with match_off bit 23
**File:** `0115-fman-pcd-cc-sdk-convergent-bringup.patch`, cc_write_group0()  
**Issue:** Word1 = `(num_keys << 24) | CC_AD_W1_LCL_MASK | (t->match_off & 0xFFFFFF)`. `LCL_MASK=0x00800000` occupies bit 23. The match_off mask `0xFFFFFF` preserves all 24 bits including bit 23. If MURAM offset reaches ≥ 0x800000, LCL_MASK is corrupted. Currently safe on LS1046A (MURAM < 1MB).  
**Fix:** Mask match_off to 23 bits: `(t->match_off & 0x7FFFFF)`.

### F-007 🟢 LOW — 0097 scheme_destroy no used-check before teardown
**File:** `0097-fman-pcd-keygen.patch`, fman_pcd_kg_scheme_destroy()  
**Issue:** No `if (!slot->used) return;` guard. Double-free from same-thread re-entry silently tears down wrong scheme state.  
**Fix:** Add `if (WARN_ON(!keygen->schemes[scheme->id].used)) return;` as first line after lock.

### F-008 🟢 LOW — 0130 per-flow record overallocation
**File:** `0130-fman-pcd-fe-ehash-dma-coherent.patch`  
**Issue:** `dma_alloc_coherent` allocates 1 page (4K) per 256-byte flow record. Flows capped at 128 → 512 KiB wasted.  
**Fix (future):** Use `dma_pool_create("fe_flow_rec", dev, 256, 256, 0)` for aligned 256-byte allocations without per-record page overhead.

---

## §3 ASK OOT Module — ask.ko v2.1.0
**Reviewer: ASK OOT subagent · Files: 12 .c + 3 .h · ~4,500 LOC**

### F-009 🟠 HIGH — Extern forward declarations risk stack corruption
**File:** `ask_hw.c:75-81`, also `ask_flow_offload.c:78-79`  
**Issue:** Five functions declared as extern without `#include`:
```c
struct fman *fman_bind(struct device *dev);
struct fman_port *dpaa_get_rx_fman_port(struct net_device *dev);
u8 fman_port_get_id(struct fman_port *port);
int fman_port_set_silicon_hit_release_all(struct fman *fm, bool enable);
int dpaa_get_tx_fqid(struct net_device *dev, u32 queue, u32 *fqid);
```
Any mismatch with in-tree signatures (enum vs int, size_t vs int, const qualifier) causes silent stack corruption — no compiler warning from forward-decl-only resolution.  
**Impact:** Platform-stability risk on kernel upgrade.  
**Fix:** (a) Manually audit each signature against board patch exports (0121 for resolvers, 0104/0136 for port helpers, 0092 for fman_bind). (b) Add a CI check that diffs the forward-decls against the actual EXPORT_SYMBOL_GPL signatures from the extracted kernel headers. (c) Accept the "linux-headers don't ship driver headers" limitation but add a `BUILD_BUG_ON` compile-time check or at minimum an explicit `static_assert(sizeof(struct fman_port *) == sizeof(void *))`.

### F-010 🟠 HIGH — ask_hw_flow_insert cookie-alloc failure leaves orphaned CC key
**File:** `ask_hw.c:906-931` (out_rollback label)  
**Issue:** After successful `ask_hw_port_reinstall()` (CC tree installed with new key), `ask_hw_cookie_alloc()` fails with -ENOMEM. Rollback calls `fman_hm_nexthop_put()` releasing the HM handle — but the installed CC tree key still references the FREED HM handle. Additionally, if the second `port_reinstall` (rollback) fails, the CC tree has a key with no cookie tracking — permanently leaking the key slot.  
**Impact:** Dangling HM pointer in silicon + permanent CC key slot leak.  
**Fix:** Allocate cookie BEFORE calling port_reinstall. If reinstall fails, free cookie. If cookie alloc fails (after reinstall succeeds — extremely unlikely since cookie alloc is kmalloc), perform a best-effort reinstall without the new key.

### F-011 🟠 HIGH — debugfs_fe_write under h->lock — VFS operations can sleep
**File:** `ask_hw.c:509-529` (within ask_hw_offload_engage, ask_hw_offload_disengage)  
**Issue:** `filp_open()`/`kernel_write()`/`filp_close()` called while holding `h->lock`. VFS operations can sleep (path lookup, dentry creation if debugfs not mounted). Lock held over sleeping VFS calls blocks all other h->lock consumers (flow insert, cookie lookup) for the duration.  
**Impact:** Latency spike on flow insertion path during engage/disengage.  
**Fix:** Release `h->lock` before the VFS operations, re-acquire after. The `offload_engaged` flag and port-slot state are protected by the per-port flag check — the lock is only needed for the flag write at the end.

### F-012 🟡 MEDIUM — P4.1 resolve_oif_fqid always returns dedicated FQ regardless of egress port
**File:** `ask_hw.c:782-785`  
**Issue:** `ask_hw_resolve_oif_fqid()` returns `h->dedicated_fq.fqid` whenever `dedicated_fq_ready` is true, ignoring the caller's `oif` ifindex. The dedicated FQ is scheduled on channel 0x801 (FMan MAC10/eth4 TX DC portal). If a flow's actual egress port is eth3 (channel 0x800), frames would route to the WRONG FMan TX port.  
**Impact:** Wrong-port routing for multi-port setups. Currently not visible because the only test uses eth4→eth4 DAC cross-connect.  
**Fix:** Either (a) allocate one dedicated FQ per egress port, indexed by channel, or (b) make the dedicated FQ use the FMan shared DC portal channel (all MACs share same channel on this SoC), or (c) document that P4.1 is single-port-only and fall back to `dpaa_get_tx_fqid()` when `oif` maps to a different port.

### F-013 🟡 MEDIUM — ask_hw_offload_disengage misses FE-VM teardown that userspace script does
**File:** `ask_hw.c:579-610` vs `vyos-offload-ask:64-77`  
**Issue:** `ask_hw_offload_disengage()` calls `fman_pcd_offload_disengage()` which only handles the CC-tree ungraft. It does NOT clean up the FE-VM pipeline (fe_arm→disengage, fe_flow→clear, fe_enter→clear, fe_enq→clear, fe_hashfe→clear, fe_ehash→clear, fe_singletons→clear, fe_pool→put). The userspace script `vyos-offload-ask disengage` does all of these.  
**Impact:** If the kernel-only path (`echo disengage > /sys/kernel/debug/ask/offload`) is used instead of the userspace script, the FE-VM structures leak in silicon — next engage finds stale FE objects.  
**Fix:** Either (a) implement full FE-VM teardown inside `ask_hw_offload_disengage()` using the exported `fman_pcd_fe_*` APIs (once available), or (b) add a WARN in the kernel path and document that the userspace script is required.

### F-014 🟡 MEDIUM — port_reinstall creates zero-CC-tree window for in-flight frames
**File:** `ask_hw.c:714-742`  
**Issue:** `ask_hw_port_reinstall()` calls `fman_cc_tree_destroy()` THEN `fman_cc_tree_install()`. There is a window where no CC tree is bound to the port's KG scheme. In-flight frames arriving during this window fall through to the RSS default path — which is the intended behavior (no frame loss). However, if a frame was mid-classification when the tree was destroyed, the silicon may walk a torn MURAM structure.  
**Impact:** Low probability of watchdog reset under heavy traffic during CC-tree rebuild.  
**Fix:** Document that reinstall is safe because the KGSE_CCBS gate is cleared by `tree_destroy` before MURAM is freed (confirmed by FMan v3 RM: CCBS clear gates entry into CC tree). Add a comment referencing RM §8.7.x.

### F-015 🟡 MEDIUM — flow_indr race on module unload
**File:** `ask_main.c:99-113` + `ask_flow_offload.c:1851-1914`  
**Issue:** `ask_flow_offload_exit()` calls `flow_indr_dev_unregister()` + cleanup. If a `flow_indr_setup_cb()` callback fires concurrently (from a different CPU), it may enter a partially-torn-down flow table or HW state. The `flow_indr_dev_unregister()` API only guarantees no NEW registrations — not that in-flight callbacks have completed.  
**Impact:** Race-condition crash on `rmmod ask`.  
**Fix:** Add `synchronize_rcu()` or completion before tearing down the subsystems the callback depends on.

### F-016 🟢 LOW — ask_flow_pending poll timer runs on empty list
**File:** `ask_flow_offload.c:729-730`  
**Issue:** `schedule_delayed_work()` every 100ms even when `ask_flow_pending_list` is empty. Timer IRQ overhead on idle system.  
**Fix:** Only re-arm timer when list is non-empty; re-arm from enqueue if timer was stopped.

### F-017 🟢 LOW — Missing M3 eviction for indefinitely-pending flows
**File:** `ask_flow_offload.c:1288-1292` (TODO comment)  
**Issue:** Flows stuck in pending queue (ARP never resolves) have no aging policy — permanent memory leak.  
**Fix:** Add 30-second eviction timer per the TODO comment.

### F-018 🟢 LOW — fake_hw_id_seq overflow on 2^31 SW flows
**File:** `ask_flow.c:245`  
**Issue:** `atomic_inc_return` on `u32` signed wraps at 2^31 into negative, at 2^32 wraps to 0 (the "no-HW" sentinel).  
**Fix:** `atomic_inc_return % FMAN_CC_MAX_STATIC_KEYS` + 1 offset.

---

## §4 CI/Build Scripts & Userspace Tools
**Reviewer: CI/Userspace subagent · Files: 8 scripts · 6 tools**

### F-019 🟠 HIGH — fan-pid I2C write failure: no thermal escalation
**File:** `board/scripts/fan-pid`  
**Issue:** `i2c_write_pwm()` logs `LOG_WARNING` on failure and returns without escalating. If the I2C bus develops a persistent fault (chip removal, bus wedge), the daemon continues forever at the last successful PWM value. A stuck-low PWM under load will overheat the SoC.  
**Impact:** Thermal hardware protection shutdown after ~30 min under load.  
**Fix:** Track consecutive failures. After N consecutive failures (3), force PWM=MAX via the degraded sysfs path as best-effort failsafe, then `sys.exit(1)` so systemd restarts the daemon.

### F-020 🟠 HIGH — fan-pid missing /dev/i2c-* exits without PWM=MAX
**File:** `board/scripts/fan-pid`, main() lines 566-570  
**Issue:** If `find_emc2305_i2c()` fails, `main()` returns 1 BEFORE the `finally` block — no PWM=MAX is written. Systemd restarts the daemon, which fails again. If PWM was previously low, SoC has zero cooling during restart loop.  
**Impact:** Thermal runaway during daemon startup failure.  
**Fix:** Write PWM=MAX via sysfs path as best-effort failsafe before returning from the except block.

### F-021 🟡 MEDIUM — CI OVFQ two-step sed fragile
**File:** `bin/ci-setup-kernel.sh`, lines 1158-1169  
**Issue:** OVFQ set via two sed passes: `0x1e…→0x9e…` then `0x9e…→0x9a…`. If a future patch introduces the intermediate constant, the second sed silently corrupts unrelated code.  
**Fix:** Single sed: `s/0x1e00000080000000ULL/0x9a00000080000000ULL/`.

### F-022 🟡 MEDIUM — CI header snapshot silently skips missing files
**File:** `bin/ci-setup-kernel.sh`, lines 1368-1382  
**Issue:** `qman.h`/`bman.h` copies guarded by `if [ -f … ]` but produce zero output on missing. The downstream OOT build fails with obscure "fatal error: soc/fsl/qman.h: No such file or directory".  
**Fix:** Add `echo "WARNING … not found, snapshot incomplete"` on each missing file.

### F-023 🟡 MEDIUM — vyos-offload-ask FE_ENTER fallback to hardcoded MURAM offset
**File:** `board/scripts/vyos-offload-ask`, lines 54-58  
**Issue:** `grep` for `FE_ENTER root AD:` in fe_arm falls back to hardcoded `0x59200`. This offset is an artifact of a specific gen_pool sequence. A different patch application order or kernel version could shift it.  
**Fix:** Log a warning on fallback. Prefer reading the offset from a dedicated debugfs node if one exists.

### F-024 🟡 MEDIUM — vyos-offload-ask dmesg‑based FQID detection silently falls back
**File:** `board/scripts/vyos-offload-ask`, lines 42-49  
**Issue:** If dmesg buffer wraps or is cleared, the `grep` for "dedicated TX FQ" returns empty, and the script silently uses default FQ 0x200. No warning.  
**Fix:** Log `"ASK offload: P4.1 dedicated FQ not found in dmesg, using default FQ 0x200"` when falling back. Alternative: read FQID from `/proc/ask/fqid` or a dedicated `/sys/kernel/debug/ask/dedicated_fqid` node.

### F-025 🟡 MEDIUM — pcd-snapshot `ppc` register not in volatile exclusion set
**File:** `board/scripts/pcd-snapshot`, line 106 SCHEME_VOLATILE_IDX  
**Issue:** `NAMES_SCHEME[7]` is `ppc` (Per-Port Packet Counter), a statistics register that increments with traffic. Only `spc` (index 16) is excluded from diff. If traffic runs during M1 soak, `ppc` drift causes false-positive diff failure.  
**Fix:** Add `NAMES_SCHEME.index("ppc")` to `SCHEME_VOLATILE_IDX`.

### F-026 🟢 LOW — CI duplicate DPAA_FQ_TD sed
**File:** `bin/ci-setup-kernel.sh`, lines 1176-1184  
**Issue:** Lines 1176-1180 and 1182-1184 are byte-identical duplicate sed blocks for taildrop threshold.  
**Fix:** Remove second copy.

### F-027 🟢 LOW — CI vyos-1x cache hash incomplete
**File:** `bin/ci-build-packages.sh`, line 166  
**Issue:** `PATCH_HASH` only globs `data/vyos-1x-*.patch`. If a patch with a different prefix modifies vyos-1x (e.g., `data/vyos-build-*.patch`), the cache key doesn't change → false cache hit → stale .deb shipped.  
**Fix:** Include the full patch directory or enumerate known prefixes.

### F-028 🟢 LOW — 4009 patch subject typo: "SFP-10G-SR" → "SFP-10G-T"
**File:** `kernel/common/patches/board/4009-sfp-oem-rollball-quirk.patch`  
**Issue:** Subject line says SR (Short Reach fiber) but the module is a copper 10GBASE-T rollball. The code correctly matches `"SFP-10G-T"` — the subject line is misleading to human readers.  
**Fix:** Correct subject line.

---

## §5 AF_XDP / DPAA1 Networking
**Reviewer: AF_XDP subagent · Files: 0070-0114 · 18 patches**

### F-029 🔴 CRITICAL — Attach seed loop missing bm_buffer_set_bpid() — same bug as patch 0139
**File:** `0075b-dpaa-af-xdp-pool-attach-bman-seed-rcu.patch`, af_xdp_pool_xsk_pool_attach()  
**Issue:** Patch 0139 fixed the NAPI refill path by adding `bm_buffer_set_bpid(&bmbs[i], bman_get_bpid(bpool))` to prevent BMan IVCI errors. The attach-time seed loop in 0075b/0084 does the same `bm_buffer_set64()` + `bman_release()` pattern but NEVER calls `bm_buffer_set_bpid()`. BMan's `bman_release()` sets BPID only on slot 0 of each BUFCOUNT chunk. Slots 1..N-1 carry bpid=0 from the pre-zeroed `bmbs[]` array → BMan fires `ErrInt: IVCI` for every non-slot-0 buffer. Only ~1/8 of seed buffers reach the pool.  
**Impact:** AF_XDP ZC attach starts with critically undersized or empty BMan pool — throughput degraded or attach fails.  
**Fix:** Add `bm_buffer_set_bpid(&bmbs[i], bman_get_bpid(bpool))` to the seed loop, identical to the 0139 fix.

### F-030 🟡 MEDIUM — xsk_tx_inflight[] not zeroed on attach; underflow possible
**File:** `0075b` + `0085`, af_xdp_pool_xsk_pool_attach() + dpaa_napi_tx_zc()  
**Issue:** After detach→reattach, `xsk_tx_inflight[]` carries residual count from previous session. Also, in `tx_conf_zc` the `atomic_dec` can underflow below zero — backpressure check `if (inflight >= MAX_INFLIGHT)` passes for negative values → unbounded submits.  
**Fix:** (a) `atomic_set(…, 0)` in attach. (b) Use `atomic_add_unless(…, -1, 0)` in tx_conf_zc.

### F-031 🟢 LOW — 0109 lock order not documented for ask.ko forward-reference
**File:** `0109-dpaa-ethtool-ntuple-cc-steering-bridge.patch`  
**Issue:** The lock order is RTNL → fman_pcd_lock (from ethtool entry points). The spec describes an ask.ko flow-insert path calling into the same CC API. If ask.ko acquires fman_pcd_lock first then tries RTNL (for netdev ops), deadlock. This is not a current bug (ask.ko CC integration pending) but the invariant needs documentation.  
**Fix:** Add comment in ASK2-DEVELOPMENT-PLAN.md §4.5: "Lock order: RTNL → fman_pcd_lock. ask.ko MUST NOT take RTNL after fman_pcd_lock."

### F-032 ✅ VERIFIED — No other issues found in AF_XDP ZC chain
- BMan pool lifetime: matched create/destroy on all paths ✓
- BPID exhaustion: 25/64 max, safe ✓
- DMA device: 0088 fix correct, no remaining `mac_dev->dev` call sites ✓
- BMI quiescence barriers: 0076 sequence correct ✓
- TX ZC TxConf inflight: atomic_t correct for current single-session lifetime ✓
- fman_port_set_rx_bpool persistent-table fix: correct ✓
- CEETM patches: truly dormant, zero boot overhead ✓
- NETIF_F_HW_TC: zero performance impact when off ✓

---

## §6 Kernel Configs & DTS
**Reviewer: AF_XDP subagent + DTS review · Files: config fragments + mono-gateway-dk.dts**

### F-033 ✅ VERIFIED — All =y requirements present
| Symbol | Fragment | Verified |
|--------|----------|----------|
| CONFIG_I2C_CHARDEV=y | 02-i2c-gpio.config | ✓ |
| CONFIG_QORIQ_THERMAL=y | 01-extras.config | ✓ |
| CONFIG_QORIQ_CPUFREQ=y | 08-dpaa1.config | ✓ |
| CONFIG_IMX2_WDT=y | 07-watchdog.config | ✓ |
| CONFIG_SPI_FSL_QUADSPI=y | 08-dpaa1.config | ✓ |
| CONFIG_FSL_FMAN/DPAA/BMAN/QMAN=y | 08-dpaa1.config | ✓ |
| CONFIG_DPAA_AF_XDP_POOL=y | 08-dpaa1.config | ✓ |
| # CONFIG_STRICT_DEVMEM is not set | 01-extras.config | ✓ |
| CONFIG_NET_SCH_FQ=y | 04-network-perf.config + ci-setup-kernel.sh sed | ✓ |

### F-034 ✅ VERIFIED — DTS fixed-link deletion compiles successfully
**File:** `board/dtb/mono-gateway-dk.dts`  
**Status:** /delete-node/ fixed-link added to fm1_mac9 and fm1_mac10, tested in CI (build #28959878556 DTB compilation PASS). Correct ordering (properties before subnode deletion).

### F-035 ✅ VERIFIED — Phylink patches complementary (101 + 4005)
- Patch 101 (sfp.c): -EINVAL→non-fatal conversion for rollball PHY probe
- Patch 4005 (phylink.c): force link_state.link=true when SFP bus present
- Both operate on different files, different stages of SFP→MAC bringup
- Both required for eth3 copper SFP to achieve link on kernel 6.18

### F-036 ✅ VERIFIED — OEM SFP quirk covers both module batches
- Quirk matches on "OEM" + "SFP-10G-T" EEPROM strings
- Both CSY101NC2722 and CSY101NC2727 variants carry same strings
- Calls `sfp_fixup_fs_10gt` → `sfp_fixup_10gbaset_30m` + `sfp_fixup_rollball_wait4s`

---

## §7 Build Pipeline Structural
**Reviewer: CI subagent · Files: auto-build.yml, ci-*.sh**

### F-037 🟢 LOW — Artifact retention undocumented
**File:** `.github/workflows/auto-build.yml`, line 253  
**Issue:** `retention-days: 15` not documented in AGENTS.md or anywhere. Builds expire silently.  
**Fix:** Add note in AGENTS.md under "Workflow-Specific Gotchas".

### F-038 🟢 LOW — MOK.key cleanup after build
**File:** `.github/workflows/auto-build.yml`  
**Issue:** MOK.key written during build, no `shred`/`rm` cleanup. Key remains on runner between builds.  
**Fix:** Add cleanup step: `shred -u` on the key file after the sign step.

### F-039 🟢 LOW — DTB compilation include-path limitation
**File:** `bin/ci-compile-mono-dtb.sh`  
**Issue:** Sparse checkout only includes `arch/arm64/boot/dts/freescale` and `include/dt-bindings`. If `fsl-ls1046a.dtsi` includes from outside these paths, CPP fails with confusing error.  
**Fix:** Document expected include dependency tree in the script header.

---

## §8 Performance & Optimization Opportunities

### P-001 🟢 LOW — dma_pool for flow records (30× memory savings)
**File:** `0130-fman-pcd-fe-ehash-dma-coherent.patch`  
**Issue:** `dma_alloc_coherent(PAGE_SIZE)` per 256-byte flow record wastes 3.75K/record.  
**Fix:** Replace with `dma_pool_create("fe_flow_rec", dev, 256, 256, 0)`.

### P-002 🟢 LOW — debugfs filehandle caching per engage
**File:** `ask_hw.c:487-507`  
**Issue:** `filp_open()` every write to debugfs (6 per engage).  
**Fix:** Open once at `ask_hw_init()`, cache file handles in `struct ask_hw_pcd`.

### P-003 🟢 LOW — CC port_reinstall is O(n²) on each key insert
**File:** `ask_hw.c:714-742`  
**Issue:** `port_reinstall()` destroys and rebuilds the entire CC static tree on every key insert. With 32 keys this is acceptable (1 tree × 32 keys), but the rebuild-via-install pattern forces O(n²) when adding keys one at a time.  
**Fix (stage C):** Use `fman_cc_tree_add_key()` to insert individual keys without full rebuild.

### P-004 🟢 LOW — fe_enq build word2=0 BPID lacks BMan pool for buffer recycling
**File:** `0124-fman-pcd-fe-vm-singletons.patch`  
**Issue:** BPID=0 means "discard after TX" — every forwarded frame's buffer is discarded and a new one allocated from the port's RX pool on the next cycle. For sustained throughput, dedicating a small BMan pool (16-32 buffers) and setting word2 to that BPID would enable buffer recycling.  
**Fix (future):** BMan pool allocation in P4.1a, BPID in ENQ AD word2.

---

## §9 Summary

### By severity

| Severity | Count | IDs |
|----------|-------|-----|
| 🔴 CRITICAL | 3 | F-001 (MURAM u32 trunc), F-002 (MURAM leak), F-029 (bman IVCI seed) |
| 🟠 HIGH | 6 | F-003 (scheme slot leak), F-009 (extern sig mismatch), F-010 (orphaned CC key), F-011 (lock over VFS), F-019 (fan fail escalation), F-020 (fan fail PWM exit) |
| 🟡 MEDIUM | 12 | F-004-F-006, F-012-F-015, F-021-F-025, F-030 |
| 🟢 LOW | 15 | F-007-F-008, F-016-F-018, F-026-F-028, F-031, F-037-F-039, P-001-P-004 |
| ✅ VERIFIED | 5 | F-032-F-036 |

### By subsystem

| Subsystem | Critical | High | Medium | Low |
|-----------|----------|------|--------|-----|
| PCD Patches | 2 | 1 | 3 | 2 |
| ask.ko OOT | — | 3 | 3 | 3 |
| CI/Userspace | — | 2 | 5 | 5 |
| AF_XDP Net | 1 | — | 1 | 1 |
| Configs/DTS | — | — | — | 4 |

### Immediate action items (this sprint)

1. **F-001 + F-002** (0132 patch rewrite): Fix MURAM truncation bug and add scaffold free on disengage. These violate the reversibility contract.
2. **F-029** (0075b patch fix): Add bm_buffer_set_bpid() to attach seed loop — same bug fix as 0139. AF_XDP ZC is broken without this.
3. **F-019 + F-020** (fan-pid hardening): Add thermal failsafe escalation and PWM=MAX on i2c init failure.
4. **F-009** (extern audit): Verify all 5 forward-declared externs match in-tree signatures byte-for-byte.

### Deferred items (next sprint)

5. F-003, F-010, F-011, F-013 (ask.ko and KG scheme hardening)
6. F-021, F-022 (CI resilience)
7. F-023, F-024, F-025 (userspace tool hardening)

---

*End of review. 39 findings across 6 subsystems. Generated from 4 parallel subagent reviews + manual DTS/config verification.*
