**Version 1.6.0 · 2026-07-26 · HADS 1.0.0**

## AI READING INSTRUCTION

**[SPEC]** This document is the live ASK2 code-review state, rewritten to be **priority-first**. It separates actionable open issues from already-fixed findings, and maps each open item to a concrete fix direction.

**[SPEC]** Scope reviewed: last-10-commit window ending at `04d3bb19`, prior `ask2-code-review` findings, and Qdrant historical diagnostics.

## 1. Prioritized issue list (actionable)

| Priority | Issue | Severity | Status | Why it is prioritized |
|---|---|---|---|---|
| ~~P0~~ | `FLUSH_FLOWS` clears SW table but can leave HW flow state (`2.8`) | HIGH | **FIXED in code** — silicon validation pending | Was the highest priority: leaked CC slots permanently exhausted the 32-key tree |
| P1 | Neighbour stale-MAC path still O(total flows) (`2.4`) | MEDIUM | PARTIALLY FIXED | Can become control-plane hot path under churn and large flow tables. **Note:** its `num_hw_backed==0` skip only works now that 2.8 stopped leaking that counter |
| P2 | No silicon validation for the post-fix stale-MAC rebuild branch **or** for 2.8's HW release | MEDIUM | OPEN (validation) | All fixes are code-complete but unproven on `.185`; 2.8 in particular can only be confirmed by observing `p->nkeys` return to baseline |

## 2. Open findings (detailed)

### 2.8 HIGH — `ASK_CMD_FLUSH_FLOWS` bypassed HW teardown — **FIXED in code, validation pending**

**[BUG]** `ASK_CMD_FLUSH_FLOWS` reported empty flow state while hardware cookies, CC slots and HM refs remained active.

**[SPEC]** Confirmed, and more damaging than first written. `ask_flow_flush()` unlinked entries straight out of the rhashtable walker and `call_rcu`'d them, never calling `ask_hw_flow_remove()`. Per HW-backed flow that leaked:

1. **The CC shadow slot** — `p->shadow[i].used` stayed true and `p->nkeys` was never decremented. This is what made it P0 rather than a tidy-up: `ask_hw_flow_insert()` refuses at `p->nkeys >= FMAN_CC_MAX_STATIC_KEYS` (**32**), so flushing 32 HW-backed flows permanently filled that port's CC tree. Every later insert returned `-ENOSPC` and fell back silently to the software path, with **no recovery short of a module reload** — from a command whose own `ask.yaml` doc string calls it "debug/recovery". The operator reaching for flush while troubleshooting was exactly who lost offload.
2. **The HM next-hop reference** — `fman_hm_nexthop_put()` never ran, so the MURAM node was never freed. MURAM is the scarce resource behind the known 327×-ENOMEM wall.
3. **The xarray cookie + kmem_cache object** — `ask_hw_cookie_free()` never ran.

Plus `t->num_hw_backed` was left permanently high, disabling the stale-MAC fast-path skip added for 2.4. **Severity split:** items 1-3 pre-date the 2026-07-26 review fixes; the `num_hw_backed` leak was a regression introduced *by* those fixes. Its effect was performance-only — an over-count causes a pointless walk, whereas an under-count would have skipped needed rebuilds.

**[SPEC] Fix as implemented.** Flush is now remove-**equivalent** via collect-then-replay:

- **Phase 1** collects up to `ASK_FLOW_FLUSH_BATCH` (32) cookies under `rhashtable_walk_start/stop` — no allocation, no sleeping.
- **Phase 2** replays them through the ordinary `ask_flow_remove()` outside the walker, which performs HW teardown and maintains both counters. One implementation of teardown ordering, not two.
- Repeats until the table drains, with a no-progress guard so a genl `doit` handler can never livelock.

**[NOTE]** The two-phase shape is **mandatory, not stylistic**: `rhashtable_walk_start()` opens an RCU read-side critical section and `ask_hw_flow_remove()` takes `h->lock`, a mutex that can sleep. Calling it from inside the walker would be a sleep-in-atomic — the same class of bug T-M6-3 had to fix in the netevent notifier.

**[NOTE]** `ask_flow_table_destroy()`'s walker had the same missing-`num_hw_backed` shape and now balances it. It deliberately still does **not** call `ask_hw_flow_remove()`: it runs at module teardown where `ask_hw_pcd_teardown()` releases the whole PCD chain wholesale, and `rhashtable_free_and_destroy()` offers no sleepable context. A comment records that any new non-teardown caller must be reworked like flush was.

**[SPEC] Coverage.** KUnit `ask_flow_test_flush_is_remove_equivalent` drives 100 entries (>3 batches) through flush and asserts the table drains and both counters return to zero. It cannot prove the silicon release — the harness has no PCD — so that remains a board task (see P2).

### 2.4 P1 / MEDIUM — stale-MAC update remains O(total flows)

**[BUG]** Even after mitigations, the rebuild trigger still performs full-table walks whenever `num_hw_backed > 0`.

**[SPEC]** What is fixed already:
1. Early skip when `num_hw_backed == 0`.
2. Cheap `!hw_backed` reject in collector.
3. Neighbour queue coalescing and capping in `ask_neigh.c`.

**[SPEC]** What remains open:
1. No adjacency index keyed by `(oif, l3_proto, dst_ip)`.
2. Worst-case asymptotic path under churn is still linear in table size.

**[SPEC] Fix direction**
1. Add adjacency index (dst tuple -> cookie list/set).
2. Update index on insert/remove/rehash and use it in stale-MAC rebuild path.
3. Keep current walk as fallback behind debug flag until index is proven.

### P2 Validation gap — stale-MAC rebuild not yet silicon-proven

**[NOTE]** Post-fix logic is stronger, but live validation remains incomplete. This is not a new code defect; it is a release-readiness risk.

**[SPEC] Required validation**
1. Engage ASK on `.185`, install HW-backed flows, force neighbour MAC change, verify rebuild occurs and forwarding continues.
2. Exercise `FLUSH_FLOWS` before/after fix and confirm HW state + `num_hw_backed` converge to zero.
3. Capture `dump-flows`, relevant debugfs counters, and `pcd-snapshot` before/after transitions.

## 3. Closed findings summary

**[SPEC]** Previously reported defects now closed in code:
1. `2.1` `hw_flow_id` namespace collision between SW fallback IDs and real HW cookies — **FIXED**.
2. `2.2` stale-MAC rebuild admitting SW-only flows — **FIXED**.
3. `2.3` unbounded neighbour event queue — **FIXED**.
4. `2.5` stale comments asserting invalid runtime invariants — **FIXED**.
5. `2.6` fake `hw_flow_id` exported as if real HW cookie — **FIXED**.
6. `2.7` queue-bound comment mismatch — **FIXED**.

## 4. Evidence anchors

**[SPEC]** Open-item anchors re-verified 2026-07-26.

| Anchor | What it shows |
|---|---|
| `ask_genl.c:592,600` | `ASK_CMD_FLUSH_FLOWS` calls `ask_flow_flush()` directly |
| `ask_flow.c:521+` | flush unlinks SW entries only (no per-entry HW teardown path) |
| `ask_flow.c:359,428` | `num_hw_backed` maintained on insert/remove only |
| `ask_flow_offload.c:665` | neighbour fast-path depends on `num_hw_backed == 0` |

## 5. Decision-ready execution order

**[SPEC]**
1. ~~Implement 2.8~~ — **done** (collect-then-replay flush + counter maintenance + KUnit guard).
2. **Next: silicon validation** for 2.8 and the stale-MAC rebuild branch, in one board session. For 2.8 the decisive check is that `p->nkeys` / MURAM return to baseline after flushing HW-backed flows — a `dump-flows` that merely reads empty was exactly the false signal the old code gave.
3. Implement the adjacency index for 2.4 only if that session's profiling shows the walk is material with ASK engaged and a large flow table.
