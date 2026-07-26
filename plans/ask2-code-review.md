**Version 1.2.0 · 2026-07-26 · HADS 1.0.0**

## AI READING INSTRUCTION

**[SPEC]** This is a live ASK2 code-review document focused on performance, defensive coding patterns, and edge cases. v1.2.0 adds an independent **verification pass** against the tree at `e70dcbd3`: every v1.1.0 finding was re-derived from source rather than re-asserted. Each finding now carries a **Verdict** line. Two findings were re-scoped, two new findings were added, and the recommended fix for the `BLOCKER` was replaced with a smaller one.

**[SPEC]** Findings are ranked by release risk. `BLOCKER` means unacceptable for throughput-test ISO unless intentionally accepted. `HIGH` means should be fixed before broad tester rollout. `MEDIUM` means should be scheduled with explicit risk acceptance.

**[SPEC]** All line anchors in this document were re-checked against `e70dcbd3` on 2026-07-26 and are exact as of that commit. Re-verify after any edit to `ask_flow.c` / `ask_hw.c` / `ask_flow_offload.c`.

## 1. Scope reviewed (post-refactor re-review + verification)

**[SPEC]** Commits and code reviewed:
1. `e70dcbd3` — flavor removal / layout refactor (`kernel/flavors/ask/...` → `kernel/ask/...`)
2. `9fd54ef2` — T-M6-1 piece 4 (`nd_tbl`/IPv6 NDISC in `ask_neigh.c`)
3. `e3c15e46` — IPv6 parse + EtherType dispatch
4. `e7937244` — notifier ownership move + stale-MAC rebuild
5. Supporting runtime logic in `ask_flow.c`, `ask_hw.c`, `ask_flow_offload.c`, `ask_neigh.c`, `ask_genl.c`, `include/ask_internal.h`

**[SPEC]** The v1.1.0 §3 claim that `e70dcbd3` changed "paths/layout, not the runtime logic" is **independently confirmed**: `git diff 9fd54ef2 -M -- kernel/ask/oot-modules` reports 30 files renamed with exactly **1 insertion / 1 deletion**, that being a doc-comment path in `ask_op.c`. No finding below is an artefact of the refactor.

## 2. Findings

### 2.1 BLOCKER — `hw_flow_id` namespace collision between SW fallback IDs and real HW cookies

**[BUG] SW fallback teardown deletes an unrelated real HW-offloaded flow**

**Verdict: CONFIRMED.** Collision is not a wrap-around corner case — it happens on the *first* flow of each class.

**Symptom:** removing a SW-fallback flow silently tears down a different, valid HW flow's silicon state while that flow remains in the SW table believing it is offloaded.

**Cause:** two producers share one `u32` space, both starting at 1 and incrementing densely:

1. **SW fallback:** `atomic_set(&t->fake_hw_id_seq, 0)` at table init (`ask_flow.c:109`), then `hw_id = atomic_inc_return(&t->fake_hw_id_seq)` (`ask_flow.c:256`) → first value **1**.
2. **Real HW:** `xa_alloc(&h->flow_cookies, &cookie, entry, XA_LIMIT(1, U32_MAX), GFP_KERNEL)` (`ask_hw.c:336-337`), published via `*out_hw_id = cookie` (`ask_hw.c:1008`). `xa_alloc` returns the lowest free index → first value **1**.
3. **Teardown** calls `ask_hw_flow_remove(hw_id)` unconditionally (`ask_flow.c:396`), and that function resolves the argument as an xarray cookie: `ck = ask_hw_cookie_lookup(h, hw_flow_id)` (`ask_hw.c:1026`, `ask_hw.c:1038`). On a hit it drops the entry's CC shadow slot and puts its HM next-hop refcount.

**[SPEC]** The non-collision argument in the `ask_flow.c:240-254` comment rests on a packed-token model (`bit 31..16` = node token, `bit 15..0` = slot) that is **no longer the live representation**. The codebase says so itself at `ask_hw.c:779`:

```c
u32 ask_priv_pack_hw_flow_id(u16 node_token, u16 key_idx)
{
        /* Debug helper kept for ABI; xarray cookies are the live form. */
```

`ask_priv_pack/unpack_hw_flow_id()` are now debug-only; nothing on the insert or remove path packs or unpacks. The `TOKEN_NONE` "silently ignored" safety net the comment relies on therefore does not exist.

**Failure sequence:** flow A (v4 TCP) offloads → cookie `1`. Flow B (v4 ICMP, or any `-EOPNOTSUPP`/`-ENODEV` case) → fake id `1`. Flow B is destroyed → `ask_hw_flow_remove(1)` → xarray hit on **flow A** → flow A's CC slot and HM reference are released. Flow A keeps `hw_flow_id=1` in the SW table and is still reported as offloaded.

**[NOTE] Why M5/M7 did not catch it:** both gates drove v4 TCP traffic exclusively, so every flow took the HW path and the mixed HW/SW-fallback population this requires was never present.

**Fix direction:** see §4 — persist the HW-backed bit rather than splitting the numeric namespace.

### 2.2 HIGH — stale-MAC rebuild fires on SW-only flows, and routes into 2.1

**[BUG] Non-HW-backed flows qualify for rebuild and their teardown corrupts a real flow**

**Verdict: CONFIRMED, but v1.1.0 mis-scoped it twice.** Corrections below.

**Correction 1 — not IPv6-specific.** The HW gate rejects *any* non-TCP/UDP v4 flow as well:

```c
if (key->l3_proto != ASK_FLOW_L3_IPV4 ||
    (key->l4_proto != IPPROTO_TCP && key->l4_proto != IPPROTO_UDP))
        return -EOPNOTSUPP;                      /* ask_hw.c:911-913 */
```

So a v4 ICMP flow plus a v4 `arp_tbl` event reproduces this today, and did so **before** piece 4. Piece 4 (`9fd54ef2`) opened the `nd_tbl`/IPv6 route into the same defect; it did not create it.

**Correction 2 — not merely "churn".** The rebuild path calls `ask_flow_remove()` → `ask_hw_flow_remove(fake_id)` (`ask_flow_offload.c:626+`), which lands directly in 2.1. The consequence is silent corruption of an unrelated flow, not wasted control-plane cycles. 2.2 is an *amplifier* of 2.1: it converts a teardown-only hazard into one that also triggers on routine neighbour churn.

**Cause:** `ask_neigh_mac_collect()` uses `f->hw_flow_id == 0` as its not-HW-backed predicate (`ask_flow_offload.c:596`). Every SW-fallback flow has a non-zero fake id, so all of them pass.

**Fix direction:** §4 item 1 fixes 2.1 and 2.2 in one change.

### 2.3 MEDIUM — neighbour event queue in `ask_neigh.c` is unbounded

**[BUG] No queue cap, coalescing, or backpressure for netevent bursts**

**Verdict: CONFIRMED.**

**Symptom:** neighbour-update storms grow memory without bound; the only backstop is `GFP_ATOMIC` allocation failure.

**Cause:** global list (`ask_neigh.c:57`) with unconditional `list_add_tail()` (`ask_neigh.c:156`). No cap, no dedup keyed on `(ifindex, l3_proto, dst_ip)`.

**[NOTE]** A bounded-queue precedent already exists in-tree — the deferred-insert pending queue (`ASK_FLOW_PENDING_MAX`, `ask_flow_offload.c:297`) with an overflow counter (`ask_flow_offload.c:366`) and a cap check (`ask_flow_offload.c:437`). Copy that shape; see 2.7 before trusting its comment.

**Fix direction:** bounded queue with drop/coalesce counters keyed by `(ifindex, l3_proto, dst_ip)`.

### 2.4 MEDIUM — per-event stale-MAC handling is O(total flows)

**[BUG] Event hot path scales linearly with the full flow table**

**Verdict: CONFIRMED.**

**Cause:** every event performs a full table walk before any targeted rebuild — `ask_flow_walk(t, ask_neigh_mac_collect, &ctx)` (`ask_flow_offload.c:649`).

**[NOTE]** Compounds with 2.3: unbounded events × full walk per event ⇒ O(events × flows) control-plane cost under a neighbour storm.

**Fix direction:** indexed lookup on `(oif, l3_proto, dst_ip)`, or an adjacency→cookies map maintained at flow insert/remove.

### 2.5 MEDIUM — stated invariants in comments are stale vs the runtime model

**[BUG] Comments assert safety properties the current code does not have**

**Verdict: CONFIRMED — more instances than v1.1.0 cited.**

1. `ask_flow.c:240-254` — asserts SW fake ids "never collide with a real (token >= 1, slot < 65536) packed id", and that a wrap would at worst "misroute through `ask_hw_flow_remove()` (which the `TOKEN_NONE` arm silently ignores)". Both clauses describe the retired packed-token model (§2.1).
2. `ask_flow.c:325-328` — asserts "`ask_hw_flow_remove()` is NULL-safe on a `TOKEN_NONE` id, so the SW-fallback path's call here is a harmless no-op". Doubly wrong: there is no `TOKEN_NONE` arm on the live path, **and** that call is guarded by `if (hw_inserted)` so the SW-fallback path never reaches it.

**Fix direction:** rewrite both sites to describe the xarray-cookie model once §4 item 1 lands; add a KUnit case asserting that a SW-fallback teardown cannot resolve a live HW cookie.

### 2.6 MEDIUM — fake `hw_flow_id`s are exported to userspace as if real *(new in v1.2.0)*

**[BUG] `show ... offload ask flows` renders SW-fallback counters indistinguishably from HW cookies**

**Verdict: NEW — not in v1.1.0.**

**Cause:** `nla_put_u32(skb, ASK_FLOW_ATTR_HW_FLOW_ID, f->hw_flow_id)` (`ask_genl.c:385`) emits the field unconditionally. The header comment at `ask_genl.c:355` still labels it "fake counter for PR7, real in PR14". An operator cannot distinguish an offloaded flow from a SW-fallback one, and two flows can display the same id.

**Fix direction:** once the HW-backed bit exists (§4 item 1), either emit `hw-flow-id` only for HW-backed flows or add an explicit `offloaded` boolean to `ask.yaml`, and render it in `show_ask_offload.py`. Note `ask.yaml` is a durable `Documentation/netlink/specs` contract — additive only.

### 2.7 LOW — documented queue bound is 16× the actual value *(new in v1.2.0)*

**[BUG] `ASK_FLOW_PENDING_MAX` comment and definition disagree**

**Verdict: NEW — not in v1.1.0.**

`ask_flow_offload.c:270` documents "Bounded: `ASK_FLOW_PENDING_MAX` = 256"; `ask_flow_offload.c:297` defines it as **4096**. Same class as 2.5, and directly relevant to 2.3 because this is the code one would copy when adding a cap to the neigh queue.

**Fix direction:** one-line comment correction; decide deliberately whether 4096 is the intended bound.

## 3. Re-review delta vs previous pass

**[SPEC]** All five v1.1.0 findings survive verification. `e70dcbd3` changed paths and layout only — confirmed by diff, not assumed (§1).

**[SPEC]** Two re-scopes: 2.2 is **not** IPv6-specific (v4 non-TCP/UDP reproduces it, pre-dating piece 4) and is **not** cosmetic churn (it routes into the 2.1 corruption). Piece 4's `l3_proto` + length-aware `memcmp` correctly prevents a v4 event from matching a v6 flow on a 4-byte prefix collision; that hardening is sound and unrelated to the defect.

**[SPEC]** Two additions: 2.6 (userspace exposure of fake ids) and 2.7 (bound comment mismatch).

**[SPEC]** One anchor correction: v1.1.0 cited `ask_flow_offload.c:647` for the table walk; the walk is at **649** (647 is `INIT_LIST_HEAD`).

## 4. Immediate fix order

**[SPEC]** Item 1 replaces v1.1.0's "split ID namespaces" recommendation with a smaller change that closes 2.1 and 2.2 together.

1. **Persist the HW-backed bit** (closes `BLOCKER` 2.1 + `HIGH` 2.2). `ask_flow_insert()` already computes `bool hw_inserted` (`ask_flow.c:188`, set at `ask_flow.c:252`, used at `ask_flow.c:328`) and then **discards it** — it is a local, absent from `struct ask_flow` (`include/ask_internal.h:497-513`). Persist it as `bool hw_backed`, then:
   - gate `ask_hw_flow_remove()` in `ask_flow_remove()` (`ask_flow.c:396`) on it;
   - replace the `f->hw_flow_id == 0` predicate in `ask_neigh_mac_collect()` (`ask_flow_offload.c:596`) with it.

   Preferred over namespace splitting because it removes all reliance on numeric id semantics instead of adding a second numeric convention to reason about, and it needs no change to the xarray allocator or the fake counter.
2. **Bound + coalesce `ask_neigh_ev_list`** (2.3), following the `ASK_FLOW_PENDING_MAX` shape.
3. **Reduce full-table-walk frequency/cost** (2.4) via an `(oif, l3_proto, dst_ip)` index.
4. **Stop exporting fake ids** (2.6) — additive `ask.yaml` change plus op-mode rendering.
5. **Correct the stale comments and add the KUnit guard** (2.5, 2.7) to lock the invariants item 1 establishes.

## 5. Evidence anchors

**[SPEC]** Exact as of `e70dcbd3`, re-verified 2026-07-26. All paths under `kernel/ask/oot-modules/ask/`.

| Anchor | What it shows |
|---|---|
| `ask_flow.c:109` | `atomic_set(&t->fake_hw_id_seq, 0)` — SW ids begin at 1 |
| `ask_flow.c:188, 252, 328` | `hw_inserted` computed, set, used — never persisted |
| `ask_flow.c:240-254` | stale non-collision comment (2.5) |
| `ask_flow.c:256` | SW fallback id allocation |
| `ask_flow.c:325-328` | stale `TOKEN_NONE` no-op comment (2.5) |
| `ask_flow.c:396` | unconditional `ask_hw_flow_remove(hw_id)` on teardown |
| `ask_hw.c:336-337` | `xa_alloc(... XA_LIMIT(1, U32_MAX) ...)` — HW ids begin at 1 |
| `ask_hw.c:779` | "Debug helper kept for ABI; xarray cookies are the live form" |
| `ask_hw.c:911-913` | `-EOPNOTSUPP` for IPv6 **and** non-TCP/UDP v4 |
| `ask_hw.c:1008` | `*out_hw_id = cookie` |
| `ask_hw.c:1026, 1038` | remove resolves the id as an xarray cookie |
| `ask_flow_offload.c:270 vs 297` | documented bound 256 vs actual 4096 (2.7) |
| `ask_flow_offload.c:366, 437` | bounded-queue precedent for 2.3 |
| `ask_flow_offload.c:596` | `f->hw_flow_id == 0` HW-backed predicate (2.2) |
| `ask_flow_offload.c:649` | full-table walk per event (2.4) |
| `ask_genl.c:355, 385` | fake ids exported to userspace (2.6) |
| `ask_neigh.c:57, 156` | unbounded event queue (2.3) |
| `include/ask_internal.h:497-513` | `struct ask_flow` — no HW-backed field |
