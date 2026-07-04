# ASK2 Modern Kernel Networking Compliance Review
**2026-07-04** · HADS 1.0.0

---

## AI READING INSTRUCTION

Read `[SPEC]` and `[BUG]` blocks for authoritative facts.
Read `[NOTE]` only if additional context is needed.

---

## 1. Compliance Matrix

**[SPEC]**
The following table captures ASK2's alignment with modern Linux kernel HW offload
practices as of kernel 6.18. Reference drivers: mlx5, nfp, netdevsim.

| Practice | Reference | ASK2 Status | Note |
|---|---|---|---|
| `flow_block_cb` API | `flow_block_cb_setup_simple()`, `ndo_setup_tc` | ✅ `dpaa_register_flow_offload_handler` (0145) | Standard path; handler registered in `dpaa_setup_tc` |
| `nf_flow_table` integration | `nf_flow_offload_work_hw`, `FLOW_CLS_REPLACE` | ✅ Handles REPLACE/DESTROY/STATS | Flow promotion requires ASSURED state |
| SW fallback | `flow_block_cb.release` callback | ✅ BUG 3b fix (0104) | TC block cleanup before flush |
| ethtool offload flags | `NETIF_F_HW_TC`, `NETIF_F_HW_ESP` | ✅ `NETIF_F_HW_TC` (0104a) | ESP not yet advertised (stub) |
| `ethtool -k` reporting | `ethtool -k eth0` reports capabilities | ✅ `hw-tc-offload: on [fixed]` | Per AGENTS: toggleable, default-off |
| RCU-protected flows | `rcu_read_lock`, `flow_block_cb_incref` | ✅ | XArray-backed flow table |
| Conntrack ASSURED gate | `nf_ct_is_established()` before offload | ⚠️ Rule installed on DUT | VyOS notrack was blocker; fixed with ct rule |
| genl family | `genl_register_family`, YNL-compatible | ✅ `ask` family (id 0x1e) | Standard netlink attributes |
| devlink health | `devlink_health_reporter_create()` | ❌ Not implemented | Phase 6 polish — non-blocking for M2 gate |

---

## 2. Flow Block Callback Lifecycle

**[SPEC]**
ASK2 adheres to the standard flow_block_cb lifecycle:

```
dpaa_setup_tc(FLOW_BLOCK_BIND)
    → flow_block_cb_alloc(ask_flow_setup_tc_block_cb)
    → flow_block_cb_add(cb, ...)
    → [block bound; driver sees REPLACE events]

FLOW_CLS_REPLACE arrives:
    → ask_flow_setup_tc_block_cb(cb, TC_CLSFLOWER_REPLACE)
    → parse match → ask_flow_key
    → parse action → egress netdev + MAC
    → ask_flow_insert(key, cookie)        ← CURRENTLY cc_tree_insert (Fork-A)
    →                                                 NEEDS fe_flow_add (Fork-B)

FLOW_CLS_DESTROY arrives:
    → ask_flow_remove(table, cookie)
    → cc_tree_remove / fe_flow_clear

dpaa_setup_tc(FLOW_BLOCK_UNBIND):
    → flow_block_cb_free(cb) → release callback
    → port cleanup, disarm, teardown
```

**[BUG] Fork-A exact-match path is DEAD on 210.10.1 ucode**
Symptom: `CONT_LOOKUP` exact-match dispatches with no fault — frames silently park.
Cause: 210.10.1 ucode does not supply terminal disposition for bare exact-match CC leaf nodes.
Fix: Switch `ask_hw_offload_engage` from `fman_pcd_offload_engage` (0129 M1 coarse) to `fman_pcd_kg_port_arm_fe` (0133 FE arm) and `ask_flow_insert` from `fman_cc_tree_install` to `fman_pcd_fe_flow_add` (Fork-B FE/eHash path).

---

## 3. Conntrack Integration

**[SPEC]**
nf_flow_table hardware offload requires:
1. Flow reaches ASSURED state (bidirectional traffic seen)
2. `nf_flow_offload_work_hw` → `flow_offload_add` → driver's `flow_block_cb`
3. Driver programs hardware → returns 0 → flow tagged `[HW_OFFLOAD]`

**[SPEC]**
VyOS default notrack configuration blocks step 1. Installed fix on DUT:
```nft
table inet filter {
    chain forward {
        type filter hook forward priority 0; policy accept;
        ct state established,related accept   ← ensures conntrack tracking
    }
}
```

**[NOTE]**
ASK 1.x oracle (nxp-sdk) had the same VyOS notrack blocker — and it was a
recurring issue across multiple ASK1 bring-up sessions. The ct-touching rule
MUST be installed before any M2 gate measurement, and the future `set system
offload ask` CLI should install it as part of the engage path.

---

## 4. Key Gaps to Close

**[SPEC]**
Priority-ordered gaps between current ask.ko and operational FE-path offload:

| # | Gap | Impact | Effort |
|---|---|---|---|
| 1 | `ask_hw_offload_engage` uses M1 coarse (dead) | No offload engages | Add FE arm to engage path |
| 2 | `ask_hw_offload_disengage` uses M1 coarse | No clean teardown | Add FE disarm + chain teardown |
| 3 | `ask_flow_insert` uses `fman_cc_tree_install` | Flow insert fails | Switch to `fe_flow_add` via ehash API |
| 4 | No dormant chain build in engage | FE-VM doesn't start | Add `fe_pool → fe_singletons → ... → fe_enter` as part of engage |
| 5 | `ask_neigh.c` is stub | No L2 header rewrite | Wire `register_netevent_notifier` for NUD_VALID, cache MAC for next-hop |
| 6 | `ask_bridge.c` is stub (417 B) | No L2 switchdev | Phase 3 |
| 7 | `ask_xfrm.c` is stub (1 KB) | No HW IPsec | Phase 4 |

**[SPEC]**
Gap #1-#4 can be closed in a single `ask_hw.c` rewire: replace
`fman_pcd_offload_engage` with `fman_pcd_kg_port_arm_fe`, add dormant-chain
build, and switch flow insert from CC tree to FE ehash. This is the Phase 2
M2 gate blocking item.

---

## 5. ask_hw.c Rewire Plan (Phase 2 M2 gate)

**[SPEC]**
The minimum viable change to wire ask.ko to the proven FE path:

```
ask_hw_offload_engage(hw_port_id):
  (1) Build dormant FE chain via PCD-internal calls:
      fman_pcd_fe_pool_get(fm)
      fman_pcd_fe_singletons_build(fm)
      fman_pcd_fe_ehash_create(fm, 0xFF, 8, 0)
      fman_pcd_fe_enq_build(fm, fqid)
      fman_pcd_fe_hashfe_create(fm)
      fman_pcd_fe_enter_create(fm)
  (2) Arm via 0133:
      fman_pcd_kg_port_arm_fe(pcd, port_id, fe_enter_off, &saved_engine)
  (3) The context builder (0146) runs automatically inside step (2)
  (4) Port is now classifying → FE-VM → ehash lookup → HIT/MISS

ask_hw_offload_disengage(hw_port_id):
  (1) Disarm: fman_pcd_kg_port_disarm_fe(pcd, port_id)
  (2) Teardown chain (reverse order of build)
```

**[SPEC]**
For the dormant chain build, ask.ko needs access to the `struct fman_pcd`
pointer and the FE builder functions. These are already exported:
- `fman_pcd_kg_port_arm_fe` → `EXPORT_SYMBOL_GPL` (0133)
- `fman_pcd_fe_context_build` → `EXPORT_SYMBOL_GPL` (0135)

The FE builder functions (`fman_pcd_fe_pool_get`, etc.) are internal to
`fman_pcd.c` (not exported). The engage path must either:
- Export the individual builders (new patches), or
- Use the `fe_arm` debugfs interface (kernel debugfs write from OOT module), or
- Have ask_hw.c call the full engage sequence through the fe_arm debugfs handler

The debugfs `fe_arm` write handler already calls `fman_pcd_fe_build_contexts`
internally (0146), so engaging through debugfs gives the full context build.

---

## 6. Next Actions

**[SPEC]**
Immediate execution plan from this review:

1. **Connect `ask_debugfs_offload_write` to the FE path** — currently the
   debugfs `offload` node calls the M1 coarse switch. Wire it to call the
   full FE arm sequence (chain build + context + arm) via the `fe_arm` API.
2. **Export `fman_pcd_fe_build_chain`** — a new board patch (0147) that
   exposes the dormant chain build as a single exported function OOT modules
   can call.
3. **Test end-to-end via ask.ko debugfs** — write "engage_fe" to the debugfs
   `offload` node, verify FE-ARMED in dmesg, ping to confirm HIT.
4. **Wire `ask_flow_insert` to `fe_flow_add`** — connect TC_CLSFLOWER_REPLACE
   to PCD ehash flow insertion using the now-known L4+DIP key extraction
   template.
