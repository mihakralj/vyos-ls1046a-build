# PR14x — surgical kernel-side `fman_pcd_manip_chain` primitive

> Status: **DESIGN** — not yet implemented
> Authored: 2026-05-18 after PR14v+PR14w M2 gate measured 5.13 Gbps / 49.82 % CPU on commit `0e6f3bc`
> Branch: `ask20`
> Successor to: PR14j (OH-port two-stage chain), PR14s (per-flow OH-pool), PR14v (per-port KG schemes), PR14w (per-resource ENOSPC counters)
> Qdrant tags: `ask2`, `pr14x`, `manip-chain-missing`, `oh-port-ceiling`, `m2-gate-design`

## TL;DR

PR14v + PR14w confirmed on hardware that the M2 gate fails because **only 6 of 16 concurrent iperf3 flows can be HW-offloaded** — one per Offline-Host port. The PR14s per-flow OH-port allocation model is structurally insufficient.

PR14x lifts the ceiling from "6 OH ports" to "255 CC keys" (already PR14r-validated) by exposing the MURAM HMCT walker (which today lives only inside `fman_pcd_oh.c::set_chain`) as a standalone `struct fman_pcd_manip_chain *` handle, then adding a new CC-key action arm `FMAN_PCD_ACTION_MANIPULATE_CHAIN` that points at it.

After PR14x the OH ports go unused for ASK2 v1.0 (they remain available for future IPsec re-inject in v1.1+). The PR14s OH-pool bookkeeping and the entire `ask_hw_pcd_bringup_oh` / `_teardown_oh` paths are deleted.

## Why this is the right architecture, not an interim hack

The required end-state dataflow is fixed by FMan v3 silicon:

```
ingress MAC → BMI parse → KG (PR14v) → CC (PR14r, 255 keys) →
per-flow MANIP chain (RMV + INSRT_GENERIC + IPV4_FORWARD [+ NAT44 + VLAN + IPsec]) →
egress MAC TX FQ
```

The MANIP step **must be per-flow** (each flow has its own next-hop MAC, eventually own NAT rewrite, own IPsec SA). The only question is where that per-flow chain physically attaches:

| attach point | n_manips per attach | attach points available | total HW flows |
|---|---|---|---|
| OH-port `set_chain()` (PR14j) | 4 | 6 (silicon-fixed) | **6** (single-state) |
| CC-key AD via `FMAN_PCD_ACTION_MANIPULATE` (today) | **1** | 255 (PR14r) | 1-stage only |
| CC-key AD via new `FMAN_PCD_ACTION_MANIPULATE_CHAIN` (PR14x) | 4 (same RM 8.11.4 limit) | 255 (PR14r) | **255** |

Every future ASK2 feature attaches to the same primitive:

- **NAT44 fast-path** → 4th MANIP stage (`FMAN_PCD_MANIP_NAT_V4`, already shipped in PR14d-body-2)
- **VLAN push/pop** → MANIP stage (`FMAN_PCD_MANIP_VLAN_PUSH`, already shipped in PR14d)
- **IPsec tunnel-mode offload** → chain ending in `FMAN_PCD_ACTION_FORWARD_CAAM` (union arm already present)
- **Per-flow stats / metering** → `FMAN_PCD_ACTION_POLICE` AD arm (already PR14e)

None of these require touching the CC tree shape, the KG schemes (PR14v), or `ask_hw.c`'s cookie/xarray logic.

## API delta — public header `include/linux/fsl/fman_pcd.h`

```c
/**
 * struct fman_pcd_manip_chain - opaque handle to a chained MANIP
 *                               walker resident in MURAM HMCT.
 *
 * Exposes the same MURAM-resident HMCT chain primitive that
 * fman_pcd_oh_port_set_chain() builds internally, as a
 * first-class handle usable by FMAN_PCD_ACTION_MANIPULATE_CHAIN.
 *
 * Lifetime is reference-counted: cc_encode_ad() takes a ref
 * when a CC-key action embeds the chain, ask_hw_flow_remove()
 * drops it via fman_pcd_manip_chain_destroy().
 */
struct fman_pcd_manip_chain;

/**
 * fman_pcd_manip_chain_create() - bundle 1..4 manips into one
 *                                 silicon-walkable chain head.
 * @pcd:     parent FMan PCD context.
 * @manips:  array of @n_manips manip handles (lifetime caller-owned;
 *           the chain keeps a ref on each).
 * @n_manips: 1..4 per RM 8.11.4.
 *
 * MURAM-allocates an HMCT chain head, encodes the per-stage
 * NIA pointers, returns the handle.  Internally calls the same
 * encode_hmct_chain() helper that fman_pcd_oh_port_set_chain()
 * uses (refactored to a shared static).
 *
 * Return: handle on success, ERR_PTR(-EINVAL) on bad args,
 *         ERR_PTR(-ENOMEM) on MURAM exhaustion, ERR_PTR(-ENOSPC)
 *         if HMCT slot pool depleted.
 */
struct fman_pcd_manip_chain *
fman_pcd_manip_chain_create(struct fman_pcd *pcd,
                            struct fman_pcd_manip * const *manips,
                            u8 n_manips);

/**
 * fman_pcd_manip_chain_destroy() - drop one ref on a chain handle.
 * @chain: handle from fman_pcd_manip_chain_create().  NULL-safe.
 *
 * The contained manips are NOT destroyed (caller-owned lifetime).
 * Frees the MURAM-resident HMCT head when refcount reaches 0.
 */
void fman_pcd_manip_chain_destroy(struct fman_pcd_manip_chain *chain);
```

Action union extension (in `enum fman_pcd_action_type` + `struct fman_pcd_action`):

```c
enum fman_pcd_action_type {
        FMAN_PCD_ACTION_DROP = 0,
        FMAN_PCD_ACTION_FORWARD_FQ,
        FMAN_PCD_ACTION_FORWARD_CAAM,
        FMAN_PCD_ACTION_REPLICATE,
        FMAN_PCD_ACTION_MANIPULATE,
        FMAN_PCD_ACTION_NEXT_CC_NODE,
        FMAN_PCD_ACTION_POLICE,
        FMAN_PCD_ACTION_MANIPULATE_CHAIN,   /* PR14x */
};

struct fman_pcd_action {
        enum fman_pcd_action_type type;
        union {
                /* ... existing arms ... */
                struct {
                        struct fman_pcd_manip_chain *chain;   /* PR14x */
                        u32 next_fqid;
                } manipulate_chain;
        };
};
```

## Implementation files

### Kernel patch `kernel/flavors/ask/patches/0035-fman-pcd-manip-chain.patch`

1. **`drivers/net/ethernet/freescale/fman/fman_pcd_manip.c`** (+~250 LOC)
   - New `struct fman_pcd_manip_chain` private to the TU: `{ kref, pcd, hmct_off, hmct_vbase, n_manips, manips[FMAN_PCD_OH_MAX_MANIPS=4] }`.
   - `fman_pcd_manip_chain_create()` body: validate args; `fman_pcd_muram_alloc(hmct_size = n_manips * sizeof(struct hmct_entry))`; for each contained manip, `kref_get(&manip->ref)` (manips already have refcount per PR14d body-1); encode HMCT entries per RM 8.7.5.4 (link i → i+1 via NIA = manip[i+1]->hmtd_off, last entry has NIA = 0 sentinel set by cc_encode_ad when consumed); return handle.
   - `fman_pcd_manip_chain_destroy()`: `kref_put_mutex(&chain->ref, free_chain, &pcd->lock)`; in `free_chain`, walk contained manips dropping refs, `fman_pcd_muram_free(chain->hmct_off, hmct_size)`, `kfree(chain)`.
   - Export both symbols via `EXPORT_SYMBOL_GPL`.

2. **`drivers/net/ethernet/freescale/fman/fman_pcd_oh.c`** (~-40 +20 LOC)
   - Refactor the inline HMCT encode block out of `fman_pcd_oh_port_set_chain` into a new static helper `fman_pcd_encode_hmct_chain(pcd, manips[], n, sink_fqid_or_zero, out_hmct_off)`. The OH-port path calls it with the real `sink_fqid`; the standalone chain path calls it with `0` and lets `cc_encode_ad()` (PR14x consumer) write the actual next_fqid into the chain's trailing AD slot at CC-key install time.

3. **`drivers/net/ethernet/freescale/fman/fman_pcd_cc.c`** (+~50 LOC)
   - Extend `cc_encode_ad()` with a new arm for `FMAN_PCD_ACTION_MANIPULATE_CHAIN`:
     - `AD.nia = RESULT_CF | NADEN | chain->hmct_off` (same encoding as the existing MANIPULATE arm, only the head pointer is the chain head rather than a single manip's HMTD).
     - `AD.fqid = action->manipulate_chain.next_fqid`.
     - `kref_get(&action->manipulate_chain.chain->ref)` so the chain outlives the CC key.
   - Symmetric `kref_put()` in the CC-key tombstone / destroy path.

4. **`drivers/net/ethernet/freescale/fman/Kconfig`** — no change.

5. **`drivers/net/ethernet/freescale/fman/tests/fman_pcd_manip_test.c`** (+~120 LOC)
   - New KUnit case `manip_chain_3stage_walk`: create 3 manips (RMV_ETHERNET, INSRT_GENERIC stub, IPV4_FORWARD), `fman_pcd_manip_chain_create(pcd, [m1,m2,m3], 3)`, install on a stub CC key with `MANIPULATE_CHAIN{chain, sink_fqid=0xdead}`, verify AD reads back with the expected NIA chain-head offset and trailing AD slot has `fqid=0xdead`.
   - New KUnit case `manip_chain_lifetime_refcount`: create chain, install on 2 CC keys, destroy chain handle (chain stays alive on keys' refs), tombstone both keys, verify chain MURAM is finally freed.
   - New KUnit case `manip_chain_invalid_n_zero`: `_create(pcd, [], 0)` returns `-EINVAL`. `_create(pcd, manips, 5)` returns `-EINVAL`.

### ASK patch `kernel/flavors/ask/oot-modules/ask/ask_hw.c` (PR14x-ask, separate commit on top of 0e6f3bc)

1. Delete:
   - `ASK_HW_NUM_OH_PORTS`, `oh_pool[]`, `oh_pool_count`, `oh_alloc_bitmap`.
   - `ASK_HW_V4_TCP_OH_IDX`.
   - `ask_hw_pcd_bringup_oh()` and `ask_hw_pcd_teardown_oh()` and their err-path labels in `_insert_v4_tcp`.
   - The `ask.enable_oh_chain` module parameter.
   - PR14w `enospc_oh_pool` counter (path no longer exists).

2. Keep:
   - Per-port KG schemes (PR14v: `v4_tcp_binds[ASK_HW_V4_TCP_MAX_BINDS=4]`, `kg_params_v4_tcp`, `ask_hw_port_bind`).
   - Shared `m_v4_rmv` and `m_v4_ipv4` MANIPs (now referenced by per-flow chains).
   - Cookie xarray indirection (`ask_hw_cookie_alloc/_lookup/_free`).
   - PR14r CC node cap-255 sizing.
   - PR14w `enospc_cc_keys` counter.
   - PR14t unknown-cookie-absorb behaviour.

3. Add:
   - PR14w `enospc_chain_muram` counter (replaces `enospc_oh_pool` at the same source-line slot).
   - `struct ask_hw_flow_cookie::manip_chain` field (replaces `oh_idx + oh_owned`).

4. Rewrite `ask_hw_flow_insert_v4_tcp` body:

```c
/* Build per-flow MANIP_INSRT_GENERIC (same as PR14s). */
ck.m_insrt = fman_pcd_manip_create(h->pcd, &insrt_params);
if (IS_ERR_OR_NULL(ck.m_insrt))
        return rc;

/* PR14x: bundle the 3-stage chain into one CC-key-attachable handle. */
{
        struct fman_pcd_manip *stages[3] = {
                h->m_v4_rmv,    /* shared */
                ck.m_insrt,     /* per-flow */
                h->m_v4_ipv4,   /* shared */
        };
        ck.manip_chain = fman_pcd_manip_chain_create(h->pcd, stages, 3);
        if (IS_ERR_OR_NULL(ck.manip_chain)) {
                rc = ck.manip_chain ? PTR_ERR(ck.manip_chain) : -ENOMEM;
                if (rc == -ENOSPC || rc == -ENOMEM) {
                        mutex_lock(&h->lock);
                        h->enospc_chain_muram++;
                        mutex_unlock(&h->lock);
                        pr_info_ratelimited("ask: hw: insert: ENOSPC at "
                                "manip_chain_create — MURAM HMCT exhausted; "
                                "flow stays in SW\n");
                }
                goto err_free_insrt;
        }
}

/* Install ingress CC key with action = MANIPULATE_CHAIN. */
memset(&entry, 0, sizeof(entry));
/* ... 5-tuple key bytes as before ... */
act = &entry.action;
act->type                       = FMAN_PCD_ACTION_MANIPULATE_CHAIN;
act->manipulate_chain.chain     = ck.manip_chain;
act->manipulate_chain.next_fqid = peer_tx_fqid;

mutex_lock(&h->lock);
slot = fman_pcd_cc_node_add_key(h->cc_v4_tcp, &entry);
if (slot == -ENOSPC)
        h->enospc_cc_keys++;
mutex_unlock(&h->lock);
if (slot < 0) {
        rc = slot;
        goto err_destroy_chain;
}

/* ... snapshot cookie ... */
return 0;

err_destroy_chain:
        fman_pcd_manip_chain_destroy(ck.manip_chain);
err_free_insrt:
        fman_pcd_manip_destroy(ck.m_insrt);
        return rc;
```

5. Rewrite `ask_hw_flow_remove`:

```c
mutex_lock(&h->lock);
(void)fman_pcd_cc_node_modify_next_action(ck->cc_node, ck->key_idx,
                                          &drop);
mutex_unlock(&h->lock);

/* PR14x: chain destroy drops the kref the CC key took at install. */
if (ck->manip_chain)
        fman_pcd_manip_chain_destroy(ck->manip_chain);
if (ck->m_insrt)
        fman_pcd_manip_destroy(ck->m_insrt);
```

6. Banner update in `ask_hw_pcd_build_chain` final `ask_pr_info`:
   `"hw: FMan PCD chain up (PR14x: CC node + KG recipe + chained-manip ready; OH ports unused in v1.0)"`

## Wiring

- `bin/ci-setup-kernel.sh`: extend `ASK_PATCH_COUNT` 34 → 35, add `0035-*` to glob, add `0035-* → 1035-` rename in the case clause.
- `kernel/common/scripts/patch-health.sh`: no change (it auto-discovers patches via the glob).
- No changes needed to `bin/ci-setup-vyos1x.sh`, `bin/ci-setup-vyos-build.sh`, `bin/ci-build-iso.sh`, or any `data/` patches.

## Test plan

1. **Local native build:** `cd work/linux-6.18.29 && git reset --hard <pre-0035> && git apply --3way kernel/flavors/ask/patches/0035-fman-pcd-manip-chain.patch && make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j32 drivers/net/ethernet/freescale/fman/` — zero warnings, vmlinux links, all 3 new `__ksymtab_fman_pcd_manip_chain_*` (well, 2: create + destroy) present.

2. **KUnit:** `make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- M=drivers/net/ethernet/freescale/fman/tests -j32` then `./tools/testing/kunit/kunit.py run --kernel_arch arm64 --cross_compile aarch64-linux-gnu- fman_pcd_manip` — all manip_chain_* cases PASS.

3. **OOT build of ask.ko against the patched tree:** `cd kernel/flavors/ask/oot-modules/ask && make -C $KSRC M=$PWD modules` — links cleanly with `fman_pcd_manip_chain_create`/`_destroy` resolved against patched vmlinux exports.

4. **patch-health:** `bash kernel/common/scripts/patch-health.sh --flavor ask --source release` — patches/0035 green; no regression in pre-existing pass count.

5. **CI:** dispatch `self-hosted-build.yml`, produce `vyos-...-LS1046A-ask-arm64.iso`.

6. **Deploy:** install on mono via `add system image`, reboot, confirm dmesg shows `"hw: FMan PCD chain up (PR14x: ... chained-manip ready ...)"` and no `OH-port pool ready` line.

7. **M2 retest:**
   ```bash
   bash bin/m2-dut-prep.sh
   bash bin/verify-ask-flow-offload.sh
   ssh vyos sudo dmesg | grep -E 'enospc|hw_insert OK|MANIPULATE_CHAIN'
   ```
   Expected: throughput ≥ 2 Gbps (re-confirms PR14g body-2/3), CPU ≤ 5 % net, dmesg shows `"hw_insert OK"` for all 16 concurrent cookies, no `enospc_*` increments.

8. **Stress:** `iperf3 -P 32 -t 120` — confirms 32-flow workload still HW-offloads (32 ≤ 127 unique 5-tuples post-dedupe). At 32 the PR14w counters should still be all zero.

9. **Boundary:** create > 255 unique CC-key 5-tuples synthetically (`for i in $(seq 1 300); do nc -z 10.99.1.2 $((10000+i)) ...`); confirm `enospc_cc_keys` increments and excess flows fall to SW (this is the PR15 second-CC-node trigger, not a PR14x failure).

## Risks and mitigations

| risk | mitigation |
|---|---|
| Refcount inversion: chain frees a contained manip while another chain still holds it | The contained manips are NOT owned by the chain — only kref'd. Destroying the chain `kref_put`s them; only the last drop (from `fman_pcd_manip_destroy()` on `m_v4_rmv` in `ask_hw_pcd_teardown`) actually frees them. PR14d already wires the kref discipline correctly. |
| HMCT MURAM pool exhaustion under flow churn | Each chain costs `n_manips * sizeof(hmct_entry) = 4 * 16 = 64 B`. 255 chains = 16 KiB of MURAM, well within the 384 KiB FMan budget. PR14w `enospc_chain_muram` counter will surface this if hit. |
| `cc_encode_ad()` kref_get on action template that the caller frees synchronously | Caller (`ask_hw_flow_insert_v4_tcp`) stores `chain` in the cookie BEFORE `cc_node_add_key` returns, so the cookie's stored chain pointer is the long-lived reference. The action template on the stack is consumed-only. Pattern matches the existing MANIPULATE arm in PR14d. |
| Existing OH-port code path leaks after PR14x deletes its caller | `fman_pcd_oh_port_set_chain` / `_claim` / `_release` exports stay (used by future IPsec re-inject in v1.1+). They become dormant in ASK2 v1.0, not deleted. |
| Patch-stack baseline drift on `git apply --3way` | All preceding ASK patches are git-format unified diffs with stable blob SHAs (per AGENTS.md patch discipline). 0035 inserts new symbols and a new action-union arm — no overlap with preceding hunks. Pre-flight check: `git apply --3way --check kernel/flavors/ask/patches/0035-*.patch`. |
| KUnit test infrastructure for `fman_pcd_manip` requires a stubbed `struct fman_pcd` | Existing PR14d-body-4 KUnit cases already constructed this stub. Reuse `tests/fman_pcd_test_helpers.c` (introduced by patch 0017). |

## Estimated effort

- Kernel patch authoring + KUnit: **~4 h focused work**.
- ASK module rewrite: **~1.5 h** (mostly deletion).
- Patch-health + native build verify: **~30 min**.
- CI roundtrip: **~22 min** (matches the PR14v+PR14w build observed 2026-05-18).
- Deploy + M2 retest: **~10 min** (boot + iperf3 + dmesg inspect).
- Qdrant store of outcome: **~5 min**.

Total: **~6.5 h end-to-end, single CI roundtrip**.

## Operator decision points

None blocking — path-(i) is the explicit operator decision recorded 2026-05-18.

## Open questions to resolve during implementation

1. **Should `m_v4_rmv` and `m_v4_ipv4` become refcounted at PR14d's body-1 level, or does PR14x add the refcount knob?** Decision: PR14d-body-1 already kref'd them (per Qdrant entry `pr14d` and pr14d body-1 patch 0014). PR14x's `fman_pcd_manip_chain_create` calls `kref_get` on each contained manip; `_destroy` calls `kref_put`. No PR14d revision needed.

2. **Does the existing OH-port `set_chain()` code path become a thin wrapper around the new `_chain_create()` + a single-OH-AD-slot publish, or stay independent?** Decision: stay independent for v1.0 so PR14x is purely additive on the OH-port side (no behavioural risk to the dormant OH-port path). Refactor optional in v1.1.

3. **Is `FMAN_PCD_ACTION_MANIPULATE_CHAIN` worth a separate enum or could the existing `MANIPULATE` arm be extended with an n_manips field?** Decision: separate enum value. The existing arm is on-the-wire-compat with PR14d body-3 (HMTD-pointer encoding). Mixing arms in the same union member would force `cc_encode_ad()` to introspect a side-channel field, which the union pattern explicitly forbids.

## Next action

When operator confirms "proceed with PR14x", begin with the kernel patch:

```bash
cd work/linux-6.18.29
git checkout -b pr14x-fman-pcd-manip-chain
# 1. edit drivers/net/ethernet/freescale/fman/fman_pcd_manip.c
# 2. edit drivers/net/ethernet/freescale/fman/fman_pcd_cc.c (cc_encode_ad arm)
# 3. edit drivers/net/ethernet/freescale/fman/fman_pcd_oh.c (refactor encoder helper out)
# 4. edit include/linux/fsl/fman_pcd.h (action union + symbol decls)
# 5. add drivers/net/ethernet/freescale/fman/tests/fman_pcd_manip_chain_test.c (or extend existing tests/fman_pcd_manip_test.c)
# 6. make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j32 drivers/net/ethernet/freescale/fman/
# 7. git diff --cached > /home/vyos/vyos-ls1046a-build/kernel/flavors/ask/patches/0035-fman-pcd-manip-chain.patch
# 8. cd /home/vyos/vyos-ls1046a-build && bash kernel/common/scripts/patch-health.sh --flavor ask --source release
```

Then PR14x-ask in a separate commit on the build repo.