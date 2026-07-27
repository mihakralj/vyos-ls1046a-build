**Version 2.5.0 · 2026-07-27 · HADS 1.0.0**

## AI READING INSTRUCTION

**[SPEC]** This document is the live, priority-first ASK2 code review. Treat §2 as the actionable defect list, §3 as the detailed evidence and fix contract, §4 as incomplete-but-gated feature work, and §5 as closed historical findings.

**[SPEC]** Review baseline: repository HEAD `c2fe6011` (`fix(ask2): F-120 — make FLUSH_FLOWS remove-equivalent (HW teardown)`), the ten commits ending at that revision, the complete `kernel/ask/oot-modules/ask/` implementation, ASK UAPI/YNL surfaces, the VyOS CLI integration, relevant FMan patch/fixup code, `ASK2-MASTER-PLAN.md`, and Qdrant silicon findings through 2026-07-26.

**[SPEC]** Findings are limited to defects supported by current source plus an executable failure sequence or authoritative silicon evidence. Speculative teardown-locking and dedicated-FQ claims were excluded where module-unregister ordering or settled topology could plausibly make them safe.

## 1. Executive verdict

**[BUG] Production ASK CLI does not select the reviewed production kernel path.** `set interfaces ethernet eth<n> offload ask` invokes `vyos-offload-ask engage`, which deliberately arms the debugfs `CONT_LOOKUP` scaffold with `fe_enter_off=0`. It does not invoke `ASK_CMD_ENGAGE` or `ask_hw_offload_engage()`, the path that calls `fman_pcd_fe_engage()` and builds the FE-VM ehash chain. The flow path then ignores `fman_pcd_fe_flow_add()` failure and still allocates a cookie, sets `hw_backed`, increments `num_hw_backed`, and reports `offloaded=true`. On the shipping CLI path, a flow can therefore be reported as hardware-offloaded without a per-flow FE-VM record.

**[SPEC]** This invalidates the current “M7 complete” release claim until CR-001 and CR-003 are fixed and silicon-verified through the actual VyOS CLI. **Reflected in `plans/ASK2-MASTER-PLAN.md` v1.21.0**: §1.2 now carries a `[BUG]` qualifying M7 (the CLI *surface* stays DONE; the end-to-end offload claim does not), and CR-001 is tracked as defect **F-123**. The kernel API implementation is materially safer than the CLI-selected debugfs path, but it is not the path operators receive.

**[SPEC]** The review found three P0 release blockers, five P1 correctness/resource defects, and four P2 hardening or future-feature defects. No new evidence overturned the settled FE-VM topology, EKFC `0x001C0006`, 13-byte MSB-first key order, raw CRC-64, direct RCCB→FE_ENTER dispatch, or the F-120 collect-then-replay design.

## 2. Prioritized actionable findings

| ID | Priority | Severity | Finding | Status |
|---|---|---:|---|---|
| CR-001 | P0 | CRITICAL | Production control path migrated to YNL, but engage/disengage remains non-reversible on silicon and offload ownership still needs end-to-end proof | PARTIAL |
| CR-002 | P0 | HIGH | FE-VM key serialization reverses TCP/UDP port bytes on little-endian ARM64 | **FIXED** `4a1c9e2` — shared builder + silicon-vector KUnit gate |
| CR-003 | P0 | HIGH | VyOS commit-path error handling is broken and fail-open: integer return code is treated as stderr, missing/unsupported paths silently succeed, helper teardown errors are swallowed | **PARTIAL** — the `AttributeError` crash is fixed (F-121, `b5998f33`); the fail-open half (no `ConfigError`, silent unsupported paths, `\|\| true` teardown) is still OPEN |
| CR-004 | P1 | HIGH | Stale-MAC remove-then-reinsert can resurrect a destroyed flow or permanently lose tracking after reinsertion failure | PARTIAL |
| CR-005 | P1 | HIGH | `num_hw_backed == 0` stale-MAC shortcut has a lost-event race with an in-flight hardware insert | **FIXED** |
| CR-006 | P1 | HIGH | `ask.yaml` does not describe the active `get-info` wire format and omits engage/disengage operations | **FIXED** |
| CR-007 | P1 | MEDIUM | Removed Fork-A programming still imposes a false 32-flow cap and allocates unused HM/shadow state | PARTIAL |
| CR-008 | P1 | MEDIUM | ASK retains the `fman_bind()` device reference for the module lifetime without releasing it | **FIXED** |
| CR-009 | P2 | MEDIUM | F-120 flush can stop partially complete after one concurrently removed batch | **FIXED** — completion now requires an empty collection; bounded stall guard |
| CR-010 | P2 | MEDIUM | `ask_flow_insert()` performs an RCU-protected rhashtable lookup without an RCU read-side critical section | **FIXED** — precheck wrapped, retained as a hint only |
| CR-011 | P2 | LOW | Authoritative comments and KUnit tests still encode disproven fake-ID and `-EAGAIN` contracts | PARTIAL |
| CR-012 | P2 | HIGH when enabled | XFRM add returns success without programming hardware; currently unreachable but unsafe to expose | **FIXED-GATED** |
| CR-013 | P0 | HIGH | FE-VM engage leaks 304 B of PCD MURAM per failed attempt (monotonic, reboot-only reclaim) and fragments the arena; ehash `int_buf` never released | **PARTIAL** — leak fixed (`F_125.py`); `int_buf` release still OPEN |

## 3. Detailed findings

### 3.1 CR-001 P0 — production path switched to YNL, but end-to-end ownership/reversibility remains unproven

**[BUG] Symptom (current, 2026-07-27).** After switching CLI control to YNL engage/disengage, both `.106` and `.185` still drift after a successful engage+disengage cycle (`pcd-snapshot` non-clean: KG/BMI state changes, MURAM delta), so the production path is still not release-safe.

**[SPEC] Root cause.**

1. `board/scripts/vyos-offload-ask` now calls `ynl --family ask --do engage|disengage` with `port-id`.
2. Boards `.106` and `.185` now expose YNL ops `engage`/`disengage`; transport no longer depends on debugfs writes.
3. Even with successful netlink return (`null` replies, rc 0/0), `pcd-snapshot diff` reports non-reversible drift on both DUTs (KG scheme[4], BMI `rfpne/rccb`, MURAM used change), so kernel disengage does not restore baseline.
4. `ynl --do get-info` decode still fails (`driver-version` decode mismatch), indicating remaining ABI/schema/runtime mismatch in the shipped board environment.

**[NOTE] 2026-07-27 update.** The old helper-path defect is code-fixed (YNL path in repo and hot-patched on both DUTs), but the new silicon blocker is disengage/revert correctness in the kernel/FMan path, not CLI transport.

**[SPEC] Additional lifetime defect.** `ask_fe_flow_insert()` is unconditional: it does not verify that the ingress port is engaged, does not bind the record to a per-port engagement generation, and uses the module-global cached `ask_hw_enq_fe_off`. Disengage tears down the FE chain but does not clear that cached offset. A later replace can therefore attempt to program an offset belonging to a freed or rebuilt MURAM object.

**[SPEC] Required fix.**

1. Keep CLI on YNL only (done in code); no production debugfs control writes.
2. Fix kernel disengage/revert semantics so engage+disengage is byte-clean under `pcd-snapshot` on both DUTs.
3. Keep FE-record insertion transactional and ownership publication gated on successful FE install/readback.
4. Couple flows to engagement generation and invalidate cached FE offsets on disengage.

**[SPEC] Acceptance gate.** On both `.106` and `.185`, run three consecutive YNL engage/disengage cycles with zero `pcd-snapshot` drift first; only then run FE/HIT flow install/remove proofs.

### 3.2 CR-002 P0 — FE-VM key serialization reverses transport ports

**[BUG] Symptom.** A flow with source port `44444` (`0xAD9C`) and destination port `55555` (`0xD903`) is serialized as `9CAD 03D9` on the LS1046A’s little-endian ARM64 kernel, so it cannot match the silicon EKFC key `... 06 AD9C D903`.

**[SPEC] Root cause.** `struct ask_flow_key::sport` and `dport` are `__be16`, but `ask_fe_flow_insert()` and `ask_fe_flow_remove()` split them with integer shifts:

```c
key_bytes.bytes[9]  = key->sport >> 8;
key_bytes.bytes[10] = key->sport & 0xff;
```

**[SPEC]** On little-endian ARM64, the numeric value of an in-memory `__be16` containing bytes `AD 9C` is `0x9CAD`; shifting it emits the bytes backwards. Add and delete agree with each other, which can hide the defect in software-only tests, but neither agrees with the hardware-extracted key.

**[SPEC]** Qdrant’s silicon-verified key is `0a63026a0a6302b906ad9cd903`: SIP, DIP, protocol, source port and destination port in wire order. This is consistent with the settled MSB-first extraction contract.

**[SPEC] FIXED.** Confirmed by inspection: `sport`/`dport` are `__be16` (wire order in memory), so `(v >> 8)` reads them as native integers and emits the bytes reversed on this little-endian ARM64 kernel. Insert and delete shared the fault, which is precisely why software-only tests agreed. Replaced both open-coded serialisers with one `ask_fe_build_key()` that `memcpy`s the `__be16` bytes, exposed for tests via `ask_internal.h`. `ASK_FE_KEY_SIZE` replaces the bare `13`.

**[SPEC] Acceptance gate — code half DONE.** `ask_flow_offload_test_fe_key_wire_order` asserts the full 13 bytes equal `0a63026a 0a6302b9 06 ad9c d903` and additionally asserts `k[9] != k[10]` and `k[11] != k[12]`, so a future byte-swap regression cannot pass by palindromic symmetry. **Silicon half still OPEN:** proving the same key appears in `fe_flow` and takes a HIT requires the FE-VM path to be reachable, which CR-001 currently prevents through the shipping CLI.

### 3.3 CR-003 P0 — VyOS configuration is fail-open and raises the wrong exception on helper failure

**[BUG] Symptom.** A failed ASK helper can either crash the commit path with `AttributeError` or be silently accepted as successful.

**[SPEC] Root cause.**

1. VyOS `Interface._popen()` returns `(stdout, integer_return_code)`.
2. `set_ask_offload()` assigns the second value to `err` and calls `err.strip()` when it is nonzero.
3. Even after correcting the type, the method only prints selected helper text instead of raising `ConfigError`, so the configuration can commit while hardware remains unchanged.
4. Missing helper binaries and unsupported interfaces return silently.
5. The helper treats required `fe_port set` failure as a warning, suppresses both disengage writes with `|| true`, and its hit-disengage/flow-clear teardown commands similarly hide failures.

**[SPEC] Required fix.** Use an API that returns stdout, stderr and integer status unambiguously; reject unsupported ports during `verify()`; raise `ConfigError` on every nonzero engage/disengage result; treat the required FE pool arm as fatal; and make teardown report partial failure rather than printing unconditional success.

**[SPEC] Acceptance gate.** Inject failures at helper missing, engage, FE pool arm and disengage stages. Each must abort commit with the original kernel/helper error, leave configuration and hardware state aligned, and never raise a Python type error.

### 3.4 CR-004 P1 — stale-MAC rebuild is not atomic with flow destruction

**[BUG] Symptom.** A neighbour update can bring back a flow after nftables destroyed it, or can delete a tracked flow permanently when reinsertion fails.

**[SPEC] Root cause.** `ask_flow_neigh_mac_changed()` snapshots a flow key, calls `ask_flow_remove(cookie)`, then calls `ask_flow_insert(cookie, rebuilt_key)`.

**[SPEC] Destruction race.**

1. Neighbour work collects cookie `C`.
2. nftables destroys `C`.
3. Neighbour work ignores `-ENOENT` from its remove and inserts `C` again.

**[SPEC]** A second ordering is also unsafe: neighbour work removes `C`; nftables destroy observes `-ENOENT` and completes; neighbour work then reinserts `C`. Both resurrect a flow after its authoritative owner removed it.

**[SPEC] Reinsertion failure.** If removal succeeds but insertion returns `-ENOMEM`, `-ENOSPC` or another error, the code only logs. The flow disappears from tracking and hardware and is not queued for retry.

**[SPEC] Required fix.** Rebuild under a lifecycle mechanism that distinguishes active, destroying and rebuilding states. A destroy must set a tombstone/generation that prevents replay. Failed active-flow rebuilds should enter the existing deferred-insert mechanism rather than disappear.

**[SPEC] Acceptance gate.** KUnit race tests must cover destroy-before-remove, destroy-between-remove-and-insert, and reinsertion failure. No ordering may resurrect a destroyed cookie or lose a still-authoritative flow.

### 3.5 CR-005 P1 — stale-MAC fast-path can lose the only neighbour-change event

**[BUG] Symptom.** A newly inserted hardware flow can retain the old next-hop MAC indefinitely even though the neighbour update notifier ran.

**[SPEC] Failure sequence.**

1. Flow replace resolves and stores the old neighbour MAC but has not yet published/incremented `num_hw_backed`.
2. Neighbour work observes `num_hw_backed == 0` and returns without walking.
3. Flow insert completes with the old MAC and increments the counter.
4. No further neighbour event is required to occur, so the stale action remains.

**[SPEC]** The comment that “the next event picks it up” is not a correctness guarantee. The counter is safe as a performance hint only when insertion and neighbour generations are synchronized.

**[SPEC] Required fix.** Track a neighbour generation in the resolved adjacency and revalidate it before publishing the hardware flow, or remove the zero-counter shortcut until an adjacency index provides synchronized ownership.

**[SPEC] FIXED.** The zero-counter fast-return was removed from `ask_flow_neigh_mac_changed()`. The handler now always walks with the existing `hw_backed` filter, so neighbour updates are no longer dropped by an advisory `num_hw_backed` race.

### 3.6 CR-006 P1 — YNL schema and live generic-netlink ABI disagree

**[BUG] Symptom.** A client generated from `kernel/ask/uapi/ask.yaml` can fail to decode or mislabel `get-info`, and cannot invoke the kernel’s engage/disengage handlers.

**[SPEC] Root cause.**

1. `ask.h` and `ask_genl.c` emit a nested `ASK_ATTR_INFO` containing ten positional attributes: driver version, genl version, separate ucode fields, capabilities, FMan count and flow count.
2. `ask.yaml` declares only four `info` attributes and models ucode as one nested `binary` struct.
3. The schema omits `ASK_CMD_ENGAGE`, `ASK_CMD_DISENGAGE` and the required `ASK_ATTR_PORT_ID`, even though the UAPI enum and live handlers implement them.

**[SPEC] Required fix.** Make `ask.yaml` the canonical ABI description, align all numeric IDs and nesting with `ask.h`, generate validation artifacts in CI, and route the production CLI through the generated interface.

**[SPEC] FIXED.** `kernel/ask/uapi/ask.yaml` now matches `ask.h`/`ask_genl.c` for `get-info`, `get-muram`, flow/SA/event/policer attrs, and includes `engage`/`disengage` with top-level `port-id`.

### 3.7 CR-007 P1 — dead Fork-A bookkeeping caps the FE-VM path at 32 flows

**[BUG] Symptom.** The FE-VM ehash design supports far more than 32 records, but `ask_hw_flow_insert()` returns `-ENOSPC` when `p->nkeys` reaches `FMAN_CC_MAX_STATIC_KEYS` (32).

**[SPEC] Root cause.** Fix C1 removed the Fork-A `ask_hw_port_reinstall()` programming path, but retained its fixed shadow array, `nkeys` limit, HM next-hop allocation and key construction. No live CC entry consumes that shadow key or HM handle; the actual per-flow path is `fman_pcd_fe_flow_add()`.

**[BUG] UNRESOLVED CONTRADICTION with `ASK2-MASTER-PLAN.md` §5 T-M6-5 Part 1.** That section's strategic verdict rests on the premise that flow *matching* is via CC-tree "hard-capped at `FMAN_CC_MAX_STATIC_KEYS = 32`", and uses that ceiling to justify the FE-VM ehash scale path. CR-007 says the shadow is dead bookkeeping and nothing programs a per-flow CC key, which would make the 32 cap an artefact rather than a silicon classifier limit. **Both cannot be true.** The observable consequences are identical either way — `-ENOSPC` at 32, and F-120's leak reaching it — so no fix here is blocked, but the *justification* for the ehash scale path differs. Resolve by reading `ask_hw_flow_insert()` against live CC-tree state on silicon **before** planning T-M6-5 Part 3. Flagged symmetrically in the master plan; neither document should be treated as settled on this point.

**[SPEC] Impact.**

1. A dead data structure imposes the obsolete CC-tree scale ceiling on the ehash path.
2. Every flow consumes HM/MURAM resources that the FE record does not reference.
3. F-120’s historical “CC slot leak” was real relative to this bookkeeping, but the slot is no longer a programmed per-flow CC key. The master plan and diagnostics should not describe the 32-slot shadow as the active classifier.

**[SPEC] Required fix.** Delete the dead Fork-A shadow/HM path physically, make successful FE records the hardware ownership object, and derive capacity from the ehash allocator rather than `FMAN_CC_MAX_STATIC_KEYS`.

**[NOTE] PARTIAL.** The hard `-ENOSPC` gate at 32 shadow keys was removed. Flow insertion no longer fails when no shadow slot is free; slot metadata is now optional and `cc_handle` is zero when no slot exists. HM/shadow bookkeeping is still present and should be removed fully in a follow-up.

### 3.8 CR-008 P1 — `fman_bind()` reference is never released

**[BUG] Symptom.** Each successful ASK hardware bring-up retains one device reference until reboot, including across a module unload/reload cycle.

**[SPEC] Root cause.** Linux v6.18 implements:

```c
struct fman *fman_bind(struct device *fm_dev)
{
	return dev_get_drvdata(get_device(fm_dev));
}
```

**[SPEC]** `ask_hw_pcd_bringup()` correctly releases the temporary platform-device reference obtained by `of_find_device_by_node()`, but the separate reference acquired inside `fman_bind()` is not released by `ask_hw_pcd_teardown()`. No `fman_unbind()` helper exists in the current API.

**[SPEC] Required fix.** Add/use a symmetric public unbind helper or retain the bound `struct device *` explicitly and call `put_device()` exactly once during teardown and every post-bind failure unwind.

**[SPEC] FIXED.** `ask_hw_pcd_teardown()` now balances the bind reference with `put_device(fman_get_dev(h->fman))`.

### 3.9 CR-009 P2 — F-120 flush can return with flows still present

**[BUG] Symptom.** `ASK_CMD_FLUSH_FLOWS` can report success after stopping with a non-empty table.

**[SPEC] Failure sequence.** Flush collects a non-empty batch, concurrent destroy removes every cookie in that batch, each replayed `ask_flow_remove()` returns `-ENOENT`, `freed` remains zero, and the no-progress guard breaks even if other flows remain.

**[SPEC] FIXED.** Confirmed: `if (!freed) break` treated a fully-raced batch as completion, so flush could return success with the table non-empty. Completion is now proven only by a collection yielding zero cookies; zero-progress passes are counted and bounded by `ASK_FLOW_FLUSH_MAX_STALLS` (8) with a warning, so a pathological race cannot spin a genl `doit` handler.

**[NOTE]** The collect-then-replay shape remains correct and mandatory because hardware removal can sleep and cannot run inside the rhashtable walker’s RCU critical section.

### 3.10 CR-010 P2 — duplicate precheck lacks required RCU protection

**[BUG]** `ask_flow_insert()` calls `ask_flow_lookup()` as a duplicate fast-path without `rcu_read_lock()`, while the remove and stats callers correctly protect the same `rhashtable_lookup_fast()` operation.

**[SPEC] FIXED.** Confirmed: `ask_genl.c:571`, `ask_flow_offload.c:1484` and `:1747` all wrap `ask_flow_lookup()` in `rcu_read_lock()`; the F-112 precheck did not. Kept as the allocation optimisation it was intended to be, now wrapped, with a comment recording that it is only a hint and `rhashtable_lookup_insert_fast()` stays the arbiter.

### 3.11 CR-011 P2 — tests and comments preserve obsolete ownership contracts

**[BUG]** `include/ask_internal.h` still states that fake IDs have `ASK_HW_TOKEN_NONE` and may be removed unconditionally. `tests/ask_test_hw_pcd.c` repeats that model and also claims `-EAGAIN` demotes to software fallback.

**[SPEC]** Current `ask_flow.c` instead tracks explicit `hw_backed` ownership, prevents fake-ID/HW-cookie collision, and preserves deferred `-EAGAIN` semantics. Stale executable documentation makes regression toward the disproven contract more likely.

**[SPEC] Required fix.** Rewrite the comments and tests around the current ownership bit, cookie namespace and deferred-insert behavior. Add negative assertions proving a synthetic ID never enters `ask_hw_flow_remove()`.

**[NOTE] PARTIAL.** Core contract comments were updated for cookie-based IDs (`hw_flow_id == 0` is SW-only) and stale packed-token wording was removed from KUnit narrative comments. Additional behavioral KUnit assertions are still needed.

### 3.12 CR-012 P2 — XFRM add is success-shaped without hardware programming

**[BUG]** `ask_xfrm_state_add()` returns success while no SA is programmed. If `xfrmdev_ops` and `NETIF_F_HW_ESP` are later exposed without replacing this body, the XFRM core may send packets to a nonexistent offload path.

**[SPEC]** This is not an active packet-loss defect today because ASK does not register the required XFRM device operations or advertise `NETIF_F_HW_ESP`.

**[SPEC] Required gate.** Until real CAAM/QI SA programming, rollback and lifetime handling exist, return `-EOPNOTSUPP` and keep all capability bits disabled. Add a feature-enable test that refuses registration while the stub remains.

**[SPEC] FIXED-GATED.** `ask_xfrm_state_add()` now unconditionally returns `-EOPNOTSUPP` (fail-closed) until real SA programming exists.

### 3.13 CR-013 P0 — engage leaks MURAM per failed attempt and fragments the arena

**[BUG] Symptom.** Measured on `.185` **and** `.106` (ISO `2026.07.27-0255`), byte-identically: `used=52282 free=13254` after one port engages, `port 0x11 ENGAGED` then `fman_pcd_fe_engage port 0x10 failed: -12`. Every subsequent failed engage leaks exactly **304 bytes**, monotonically (51514 → 51818 → 52122), reclaimable only by reboot.

**[SPEC] The arena is NOT undersized.** Pristine post-init baseline is 43253 of 65536 and one engage costs 9029, so two ports need **61311 of 65536** — they fit by total bytes and fail on *placement*. The 33280-byte `int_buf` at `0x4c100` splits the arena into a ~5376-byte head and a ~26880-byte tail.

**[SPEC] Root cause — not what it looked like.** `__fman_pcd_fe_arm_disengage()` **already** calls `fman_pcd_fe_arm_free_scaffold()`, so a successful engage/disengage is clean. The leak is on the *failure* path: `__fman_pcd_fe_arm_engage()` allocates the FE_ENTER scaffold (gro 256 + mto 16 + ato 32 = **304**, exactly the measured leak), and when `fman_pcd_kg_port_arm_fe()` fails it returns with `pcd->fe_scaffold_*` still populated. The next attempt re-enters the `fe_enter_off == 0` path and **overwrites** those fields, orphaning the triple permanently. Stranded triples sit mid-arena, which is the fragmentation: a post-disengage state of 43253 used / 22283 free failed a fresh engage that the byte-identical cold-boot state satisfied.

**[SPEC] FIXED (leak half) — `bin/kernel-fixups/F_125.py`.** Releases the scaffold on the `kg_port_arm_fe` failure path, guarded on `fe_armed_port` so a failure alongside an already-armed port cannot pull it out from under; and unwinds a partial 3-way allocation, which previously stranded whichever of the three succeeded. Reuses the existing helper — no second one added. Idempotent; `fman_pcd.c` compiles clean with no new warnings; fixup gate 42/42.

**[SPEC] STILL OPEN.** The ehash table and its 33280-byte `int_buf` are still held with **zero** ports engaged (`refcount=1`). Separate allocation site, deliberately a separate change so the two are independently revertible. Releasing it on last disengage returns 33280 bytes and is the strongest candidate for F-124's `pcd-snapshot` MURAM delta.

**[NOTE] Ruled out:** shrinking `FMAN_EHASH_INT_BUF_POOL_SIZE` (`256 * 128`). It is a vendor/LSDK constant — 256 is almost certainly concurrent FE-VM contexts — so shrinking it caps in-flight frames and would surface as drops under exactly the load the M5 10.259 Gbps gate measures. Also note the plan's documented `0x7fff → 0x0fff` mask mitigation **cannot** help here: `int_buf` size is mask-independent; the mask only sizes the 512 KiB DDR table.

**[SPEC] Acceptance gate.** Cold boot, then three engage/disengage cycles per port with `muram_budget` returning to the 43253 baseline each time and zero `pcd-snapshot` drift. **Diagnostic hazard:** probing this consumes MURAM irreversibly until the `int_buf` half also lands — budget attempts and cold-boot between measurement runs.

## 4. Incomplete features that are not active defects

**[SPEC]** These surfaces remain planned work and must stay capability-gated:

| Surface | Current state | Safety requirement |
|---|---|---|
| IPv6 flow hardware insertion | Parser/notifier plumbing partly landed; hardware replace rejects unsupported cases | Do not mark v6 flows offloaded until separate 37-byte scheme/table is implemented |
| Bridge/switchdev | `ask_bridge.c` is a stub | Do not register switchdev behavior or bridge capability |
| IPsec/CAAM | XFRM and CAAM bodies are incomplete | Keep `NETIF_F_HW_ESP` and xfrmdev registration absent |
| Per-flow hardware counters | Dump fields exist but silicon HIT accounting is incomplete | Report zero/unknown explicitly; do not label software counters as silicon counters |
| AF_XDP true-ZC RX | Kernel datapath work exists; VPP/XSKMAP integration remains blocked | Keep shipping verdict dormant until fill-ring and redirect gates pass |

## 5. Closed historical findings

**[SPEC]** The following prior review findings are fixed in current code and remain closed:

1. Hardware-cookie and synthetic-ID namespace collision: explicit `hw_backed` ownership plus collision protection landed in `04d3bb19`.
2. Stale-MAC collector admitting software-only flows: collector now rejects `!hw_backed`.
3. Unbounded neighbour-event queue: cap and coalescing landed.
4. `offloaded` observability ambiguity: UAPI now exports an explicit ownership attribute, although CR-001 shows the producer currently sets it before FE-record success.
5. F-120 direct SW-only flush: `c2fe6011` routes flush through ordinary remove in batches and balances counters.
6. Flow-table destroy counter imbalance: teardown now balances `num_hw_backed`.
7. Sleep-in-atomic neighbour handling: notifier work is deferred to process context.
8. Whole-table FE delete on ordinary flow removal: F-117 added per-key ehash unlink; silicon collision-chain validation remains separate from this code review.

**[NOTE]** F-120 is code-fixed but not fully closed for release: CR-009 (the narrower concurrent-completion race) is now also fixed, but board validation must still prove hardware/MURAM convergence — tracked as **T-M6-6** in the master plan. The decisive check is `p->nkeys`/MURAM returning to baseline; an empty `dump-flows` is the exact false signal the broken code produced.

## 6. Evidence anchors

| Finding | Source anchors |
|---|---|
| CR-001 | `data/vyos-1x-031-offload-ask-cli.patch:set_ask_offload`; `board/scripts/vyos-offload-ask`; `ask_genl.c:ask_cmd_engage/disengage`; `ask_hw.c:ask_hw_offload_engage/disengage`; dual-DUT retest 2026-07-27 (.106/.185) |
| CR-002 | `ask_flow_offload.c:ask_fe_flow_insert`, `ask_fe_flow_remove`; Qdrant verified key `0a63026a0a6302b906ad9cd903` |
| CR-003 | `data/vyos-1x-031-offload-ask-cli.patch:set_ask_offload`; helper `engage`, `disengage`, `hit_disengage`, `flow_clear` |
| CR-004/005 | `ask_flow_offload.c:ask_flow_neigh_mac_changed`; `ask_flow.c:ask_flow_insert`, `ask_flow_remove` |
| CR-006 | `kernel/ask/uapi/ask.yaml`; `include/uapi/linux/ask/ask.h`; `ask_genl.c:ask_cmd_get_info`, engage/disengage ops |
| CR-007 | `ask_hw.c:ask_hw_flow_insert`, Fix C1 comments, `FMAN_CC_MAX_STATIC_KEYS` guard |
| CR-008 | Linux v6.18 `fman.c:fman_bind`; `ask_hw.c:ask_hw_pcd_bringup`, `ask_hw_pcd_teardown` (`put_device(fman_get_dev(...))`) |
| CR-009 | `ask_flow.c:ask_flow_flush`, `if (!freed) break` |
| CR-010 | `ask_flow.c:ask_flow_lookup`, duplicate precheck in `ask_flow_insert` |
| CR-011 | `include/ask_internal.h` hardware-ID contract; `tests/ask_test_hw_pcd.c` |
| CR-012 | `ask_xfrm.c:ask_xfrm_state_add`; absence of xfrmdev registration and `NETIF_F_HW_ESP` |

## 7. Validation status and limits

**[SPEC]** Source validation completed:

1. All ASK module, UAPI, CLI/helper and relevant FMan composition paths were traced at HEAD `c2fe6011`.
2. Recent commits were reconciled against the previous review and master-plan status.
3. Qdrant findings were checked for the FE key, production scaffold behavior, FE-VM HIT topology, teardown history and FMan ownership model.
4. Upstream Linux v6.18 `fman_bind()` was checked directly and confirmed to acquire a device reference with `get_device()`.

**[NOTE]** A local build against the host’s stock 6.1 headers is not a valid ASK source gate because those headers do not contain the downstream `linux/fsl/fman_pcd.h` API. No source regression was inferred from that environment mismatch.

**[SPEC]** Silicon validation is still required for CR-001/002 after repair, stale-MAC race closure, F-120 hardware convergence, per-key collision-chain delete, and repeated two-port engage/disengage.

## 8. Required execution order

**[SPEC]**

1. Fix CR-001 and CR-003 together: one production control plane, generic netlink/YNL only, fail-closed configuration, and no debugfs writes from VyOS commit.
2. Fix CR-002 before the first production FE-record validation; its exact 13-byte KUnit vector is a hard gate.
3. Make FE insertion transactional: record success must precede `hw_backed`, with full rollback and engagement-generation checks.
4. Finish CR-004 lifecycle/tombstone closure before declaring stale-MAC handling complete.
5. Align `ask.yaml` with the live ABI and generate the userspace client used by step 1.
6. Delete dead Fork-A bookkeeping, remove the artificial 32-flow cap, and release the FMan reference.
7. Close CR-009/010/011 with focused KUnit coverage.
8. Run a cold-boot silicon session through the actual VyOS CLI and update `ASK2-MASTER-PLAN.md` only after the acceptance evidence is captured.

**[NOTE] Progress 2026-07-27.** Failed dual-DUT validation was first caused by image provenance: run `30227073161` shipped remote SHA `2f32b637` (pre-YNL helper), so both DUTs booted the old debugfs `CONT_LOOKUP` helper. Hotpatching current `vyos-offload-ask` + `ask.yaml` switched both DUTs to YNL control and restored byte-clean `pcd-snapshot` reversibility. Remaining blocker is engage return-code convergence under kernel API (`-EBUSY` already-armed on port 0x10 and `-ENOMEM` chain-build on 0x11), addressed in `ask_hw_offload_engage()/disengage()` idempotence hardening pending fresh ISO validation.
