**Version 1.0.0 · 2026-07-26 · HADS 1.0.0**

## AI READING INSTRUCTION

**[SPEC]** This is a live ASK2 code-review document focused on performance, defensive coding patterns, and edge cases. It is scoped to recent ASK2 runtime changes (`9fd54ef2`, `e3c15e46`, `e7937244`) and surrounding hot-path code they interact with (`ask_flow.c`, `ask_hw.c`, `ask_flow_offload.c`, `ask_neigh.c`, `ask_internal.h`).

**[SPEC]** Findings are ranked by release risk. `BLOCKER` means unacceptable for throughput-test ISO unless intentionally accepted. `HIGH` means should be fixed before broad tester rollout. `MEDIUM` means should be scheduled with explicit risk acceptance.

## 1. Scope reviewed

**[SPEC]** Commits reviewed in detail:
1. `9fd54ef2` — T-M6-1 piece 4 (`nd_tbl` support in `ask_neigh.c`)
2. `5c00d930` — CI patch-rot gate repair (context only; not dataplane runtime)
3. `e3c15e46` — IPv6 parse + EtherType dispatch
4. `e7937244` — notifier ownership move + stale-MAC rebuild
5. Supporting logic in `ask_flow.c` / `ask_hw.c` used by those paths

## 2. Findings

### 2.1 BLOCKER — `hw_flow_id` namespace collision between SW fallback IDs and real HW cookies

**[BUG] SW fallback ID can delete unrelated real HW flow**

**Symptom:** flow teardown/rebuild of a SW-fallback flow can remove a different, real HW-offloaded flow.

**Cause:** two producers share the same `u32 hw_flow_id` namespace:
1. SW fallback IDs are generated from `atomic_inc_return(&t->fake_hw_id_seq)` (starts at 1) in `ask_flow.c:256`.
2. Real HW cookies are allocated from `xa_alloc(... XA_LIMIT(1, U32_MAX) ...)` in `ask_hw.c:336`.
3. Teardown always calls `ask_hw_flow_remove(hw_flow_id)` (`ask_flow.c:376`), and `ask_hw_flow_remove()` treats `hw_flow_id` as xarray key (`ask_hw.c:1026`, lookup at `ask_hw.c:1038`).

This means SW IDs and HW IDs are numerically overlapping by construction.

**Fix direction:** split namespaces explicitly (e.g., high-bit tagging for SW IDs, or reserve disjoint numeric range), and gate `ask_hw_flow_remove()` by ID class before xarray lookup.

### 2.2 HIGH — IPv6 stale-MAC rebuild path currently rewrites SW-only flows

**[BUG] New `nd_tbl` event handling causes unnecessary remove+insert churn for unsupported IPv6 HW path**

**Symptom:** IPv6 neighbour updates can trigger rebuild operations for flows that are not HW-offloaded, producing avoidable churn/logging and increasing chance of hitting the ID-collision blocker above.

**Cause:**
1. IPv6 flows are parsed (`e3c15e46`) but HW insert is intentionally rejected (`ask_hw.c:911-913` returns `-EOPNOTSUPP`).
2. `ask_flow_insert()` still assigns non-zero SW fallback IDs on `-EOPNOTSUPP` (`ask_flow.c:256-258`).
3. `ask_neigh_mac_collect()` treats `f->hw_flow_id != 0` as "HW-backed" (`ask_flow_offload.c:596`), so SW-only IPv6 entries qualify and are rebuilt on `nd_tbl` events (`ask_flow_offload.c:626+`).

**Fix direction:** classify true HW-backed flows independently from non-zero ID (e.g., explicit `is_hw_backed` flag in `struct ask_flow`, or verifiable ID-tag check after fixing 2.1).

### 2.3 MEDIUM — Neighbour event queue is unbounded

**[BUG] No memory/backpressure guard on queued neighbour events**

**Symptom:** neighbour-update storms can grow in-memory queue without bound.

**Cause:** `ask_neigh.c` uses a global list queue (`ask_neigh_ev_list`) and unconditionally `list_add_tail()`s events (`ask_neigh.c:57`, `ask_neigh.c:156`) with no cap, dedup, or drop policy.

**Fix direction:** add bounded queue policy (max entries + drop counter), and optionally coalesce by `(ifindex, l3_proto, dst_ip)` before enqueue.

## 3. Performance/defensive notes (non-blocking but important)

**[NOTE]** The notifier deferral model in `ask_neigh.c` is directionally correct (atomic notifier -> workqueue process context) and closes the historical sleep-in-atomic pattern.

**[NOTE]** After `9fd54ef2`, address-family discrimination in stale-MAC walk (`l3_proto` check before length-aware memcmp) is a good defensive correction against v4/v6 prefix collisions.

## 4. Immediate review-to-fix order

**[SPEC]** Recommended implementation order:
1. Fix 2.1 ID namespace separation (`BLOCKER`).
2. Fix 2.2 HW-backed classification so IPv6 SW flows are not rebuilt.
3. Add queue bounds/coalescing for `ask_neigh_ev_list`.
4. Re-run targeted teardown/rebuild stress on mixed IPv4(HW)+IPv6(SW) flow sets.

## 5. Evidence anchors

**[SPEC]** Code anchors used:
1. `kernel/ask/oot-modules/ask/ask_flow.c:256, 376`
2. `kernel/ask/oot-modules/ask/ask_hw.c:336, 1026, 1038`
3. `kernel/ask/oot-modules/ask/ask_flow_offload.c:596, 626, 644`
4. `kernel/ask/oot-modules/ask/ask_neigh.c:57, 156`
5. `kernel/ask/oot-modules/ask/ask_hw.c:911-913`
