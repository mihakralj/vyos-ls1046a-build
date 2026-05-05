# AUDIT-auto_bridge

**Module:** `ASK/auto_bridge/` — Automatic Bridging Module (ABM) kernel OOT module
**Origin:** Mindspeed Technologies (2007), Freescale (2015–2016), NXP (2017, 2021)
**License:** GPL-2.0+
**Audit lens:** D1..D18 / O1..O10
**Date:** refresh of `ASK-CODE-REVIEW.md §5` and `ASK-CODE-QUALITY.md §3`

---

## Findings summary (P0/P1/P2 table)

| ID | Severity | Class | File:Line | Title |
|---|---|---|---|---|
| AB-01 | **P0** | D6 / D4 | `ASK/auto_bridge/auto_bridge.c:540–544` | `memcpy` of user-supplied `nla_data(tb[L2FLOWA_IP_SRC])` into fixed `l2flow_temp.l3.saddr.all[16]` using attacker-controlled `nla_len(...)` — heap/stack overflow when payload > 16 bytes. Same issue for `IP_DST`. |
| AB-02 | **P0** | D12 | `ASK/auto_bridge/auto_bridge.c:487–569` | No `netlink_capable(skb, CAP_NET_ADMIN)` check on inbound `L2FLOW_MSG_ENTRY` messages — local user can drive `abm_l2flow_msg_handle()`. |
| AB-03 | **P1** | D6 / D17 | `ASK/auto_bridge/auto_bridge.c:621–624` | `abm_nl_init()` ignores `netlink_kernel_create()` return — `abm_nl` may be NULL; subsequent `netlink_has_listeners(abm_nl, ...)` (line 107, 169, 371, 460) and `abm_nl_exit()` `netlink_kernel_release(NULL)` then oops. Also: `abm_nl_init()` always returns 0. |
| AB-04 | **P1** | D17 | `ASK/auto_bridge/auto_bridge.c:1512–1547` | `abm_init()` failure paths leak resources — every `return rc` after `kabm_wq` / `l2flow_table` / `nl` / `proc` / `sysctl` / `nf_register_net_hooks` step does **not** unwind prior steps. |
| AB-05 | **P1** | D5 / D17 | `ASK/auto_bridge/auto_bridge.c:1149–1161` | `abm_l2flow_table_init()` returns -ENOMEM if `brroute_cache` fails, but the already-created `l2flow_cache` is **not** destroyed. |
| AB-06 | **P1** | D17 | `ASK/auto_bridge/auto_bridge.c:1555–1567` | `abm_exit()` calls `abm_proc_fini()` but does **not** call `abm_sysctl_fini()` — sysctl table leak on module unload. |
| AB-07 | **P1** | D11 | `ASK/auto_bridge/auto_bridge.c:135–147` | `abm_do_work_send_msg()` calls `rtnl_lock()` while holding `abm_lock` (spin_lock_bh) — `rtnl_lock()` is a mutex; **scheduling under spinlock**. |
| AB-08 | **P1** | D10 | `ASK/auto_bridge/auto_bridge.c:1109–1125` | `abm_l2flow_table_wait_timers()` busy-loops with `schedule()` and `goto test_list` — no synchronization with timer callbacks; entries may transition states while unlocked. |
| AB-09 | **P2** | D16 | `ASK/auto_bridge/auto_bridge_private.h:19–28` | Macro hygiene: `#define L2FLOW_HASH_TABLE_SIZE1024`, `#define ABM_DEFAULT_MAX_ENTRIES5000`, `#define L2FLOW_FL_NEEDS_UPDATE0x1` etc. — missing whitespace between identifier and value (D16). |
| AB-10 | **P2** | D16 / parse-hazard | `ASK/auto_bridge/auto_bridge.c:54–69` | Globals declared without space: `struct list_headl2flow_table[L2FLOW_HASH_TABLE_SIZE];`, `static struct sock*abm_nl = NULL;`, `static charabm_l3_filtering = 0;` etc. Compiles only because `head` / `*` separates the type-name; very brittle / hostile to grep. |
| AB-11 | **P2** | D14 | `ASK/auto_bridge/auto_bridge.c:864, 877` | `printk(KERN_DEBUG …)` should be `pr_debug` / `netdev_dbg`. |

---

## Module inventory

| File | LoC | Role |
|---|---:|---|
| `ASK/auto_bridge/auto_bridge.c` | 1572 | core ABM logic: l2flow hash tables, netlink handler, ebtables hooks, sysctl, /proc, retransmit work |
| `ASK/auto_bridge/auto_bridge_private.h` | 192 | private types (l2flow, l2flowTable, br_event_table), hash helpers, NLA_PUT compat macros |
| `ASK/auto_bridge/include/auto_bridge.h` | (~) | UAPI for userspace (CMM) — out of scope |

**Kernel-side ABI surface:**
- Netlink protocol: `NETLINK_L2FLOW` (raw), multicast group `L2FLOW_NL_GRP`.
- /proc node: `/proc/net/abm` (seq_file dump of l2flow table).
- sysctl: `/proc/sys/net/abm/abm_l3_filtering`, `abm_timeout_seen`, `abm_timeout_confirmed`, `abm_timeout_linux`, `abm_timeout_dying`, `abm_retransmit_delay`, `abm_max_entries`.
- nf_hooks: `NF_BR_FORWARD` (`NF_BR_PRI_LAST`), `NF_BR_POST_ROUTING` (`NF_BR_PRI_LAST - 1`).
- External symbols consumed: `register_brevent_notifier`, `unregister_brevent_notifier`, `br_fdb_register_can_expire_cb`, `br_fdb_deregister_can_expire_cb`.

No `module_param`, no chardev, no ioctl. Entry points: netlink + bridge notifier + ebtables hook + sysctl + /proc.

---

## Re-verification of prior findings

### Prior HIGH — "auto_bridge: NULL `dev` from `dev_get_by_name` not checked" (`ASK-CODE-QUALITY.md §3`)

**Prior cite:** `ASK/auto_bridge/auto_bridge.c:556-573` and `597-610` with snippet:

```c
dev = dev_get_by_name(&init_net, if_name);
br_addif(br_dev, dev);  // kernel oops
```

**Status:** **CLOSED — prior finding was fabricated.**

`grep -n "dev_get_by_name\|br_addif\|dev_ioctl" ASK/auto_bridge/auto_bridge.c` returns **no matches**. Lines 556–573 of the current file are inside `abm_nl_rcv_msg()` and contain `nla_get_u8` / `abm_l2flow_msg_handle()` — there is no `dev_get_by_name` anywhere in the module. The prior audit's snippet appears to describe a different module (possibly an older `cmm` userspace tool or a hypothetical proposal).

**However**, examining the actual netlink ingress reveals an even more serious defect (`AB-01` — unbounded `memcpy` from netlink attribute into fixed-size struct field) which the prior audit missed.

### Prior MED M17 — "Uses `dev_ioctl()` to add interfaces to bridge"

**Status:** **CLOSED — fabricated.** No `dev_ioctl` call exists. Bridge integration is via `register_brevent_notifier` (line 1543) + nf_hooks; not `dev_ioctl`.

### Prior MED M18 — "No filtering on interface type"

**Status:** **N/A.** ABM does not enumerate interfaces — it observes via nf_hooks per-skb. Filtering happens via ebtables priority and `abm_l3_filtering` sysctl. Reframed: not a defect.

### Prior MED M19 — "Module parameters `bridge_name` / `interface_prefix` writable at runtime"

**Status:** **CLOSED — fabricated.** No such module parameters exist (`grep module_param ASK/auto_bridge/` returns nothing). The actual sysctls (lines 1409–1459) are documented above and do not include `bridge_name` or `interface_prefix`.

---

## P0 findings

### AB-01 — Unbounded `memcpy` from netlink attribute into fixed l3 union

**File:** `ASK/auto_bridge/auto_bridge.c:540–544`.

```c
540:    if(tb[L2FLOWA_IP_SRC])
541:        memcpy(&l2flow_temp.l3.saddr.all, nla_data(tb[L2FLOWA_IP_SRC]),
542:               nla_len(tb[L2FLOWA_IP_SRC]));
543:
544:    if(tb[L2FLOWA_IP_DST])
545:        memcpy(&l2flow_temp.l3.daddr.all, nla_data(tb[L2FLOWA_IP_DST]),
546:               nla_len(tb[L2FLOWA_IP_DST]));
```

`l2flow_temp.l3.saddr.all` is `u32 all[4]` (16 bytes — see `auto_bridge_private.h:60–64`). `nla_len(...)` is attacker-controlled (no `nla_policy` is passed to `nlmsg_parse` at line 501 — the policy argument is `NULL`). A crafted `L2FLOWA_IP_SRC` attribute with payload `> 16` bytes overflows `l2flow_temp` on the kernel stack of `abm_nl_rcv_msg()`.

`l2flow_temp` is a stack local; overflow corrupts the saved frame, return address, and adjacent locals (`l2flow_msg`, `tb[]`).

**Fix #1 (minimal):** clamp the copy:

```c
size_t cp = min_t(size_t, nla_len(tb[L2FLOWA_IP_SRC]),
                  sizeof(l2flow_temp.l3.saddr.all));
memcpy(&l2flow_temp.l3.saddr.all, nla_data(tb[L2FLOWA_IP_SRC]), cp);
```

**Fix #2 (correct):** declare a `nla_policy` array and pass to `nlmsg_parse`:

```c
static const struct nla_policy l2flow_policy[L2FLOWA_MAX + 1] = {
    [L2FLOWA_IP_SRC]   = { .len = 16 },
    [L2FLOWA_IP_DST]   = { .len = 16 },
    [L2FLOWA_SVLAN_TAG]= { .type = NLA_U16 },
    /* ... */
};
err = nlmsg_parse(nlh, sizeof(*l2flow_msg), tb, L2FLOWA_MAX, l2flow_policy, NULL);
```

**Routing:** kernel-side patch in `ASK/auto_bridge/auto_bridge.c`.

### AB-02 — Missing capability check on netlink ingress

**File:** `ASK/auto_bridge/auto_bridge.c:487–569`.

`abm_nl_rcv_msg()` runs on every inbound L2FLOW message without checking `netlink_capable(skb, CAP_NET_ADMIN)`. Combined with **AB-01**, an unprivileged local user achieves a kernel stack overflow.

**Fix:** at top of `abm_nl_rcv_msg()`:

```c
if (!netlink_capable(skb, CAP_NET_ADMIN))
    return -EPERM;
```

---

## P1 findings

### AB-03 — `netlink_kernel_create()` return ignored in `abm_nl_init`

**File:** `ASK/auto_bridge/auto_bridge.c:615–624`.

```c
615: static int abm_nl_init(void)
616: {
617:    struct netlink_kernel_cfg cfg = { ... };
621:    abm_nl = netlink_kernel_create(&init_net, NETLINK_L2FLOW, &cfg);
622:
623:    return 0;
624: }
```

If `netlink_kernel_create()` returns NULL (e.g. protocol already registered), `abm_nl` stays NULL. Later, `netlink_has_listeners(abm_nl, ...)` at lines 107, 169, 371, 460 is called with NULL — kernel oops.

`abm_nl_exit()` at line 627 calls `netlink_kernel_release(abm_nl)` which also oopses on NULL.

**Fix:** `if (!abm_nl) return -ENOMEM;` and propagate to `abm_init()`.

### AB-04 — `abm_init()` failure paths leak resources

**File:** `ASK/auto_bridge/auto_bridge.c:1512–1547`.

After `create_singlethread_workqueue` (line 1517), `abm_l2flow_table_init` (line 1522), `br_fdb_register_can_expire_cb` (1526), `abm_nl_init` (1527), `abm_proc_init` (1531), `abm_sysctl_init` (1535), `nf_register_net_hooks` (1539), each failure path is `return rc;` without unwinding.

E.g. if `abm_proc_init()` fails, the workqueue, l2flow caches, fdb callback registration, and netlink socket all leak.

**Fix:** convert to `goto err_<step>` ladder.

### AB-05 — `l2flow_cache` leak when `brroute_cache` fails

**File:** `ASK/auto_bridge/auto_bridge.c:1149–1161`.

```c
1149:    l2flow_cache = kmem_cache_create("l2flow_cache",
1150:        sizeof(struct l2flowTable), 0, 0, NULL);
1151:    if (!l2flow_cache)
1152:        return -ENOMEM;
1153:
1154:    brroute_cache = kmem_cache_create("brroute_cache",
1155:        sizeof(struct br_event_table), 0, 0, NULL);
1156:    if (!brroute_cache)
1157:        return -ENOMEM;
```

On `brroute_cache` failure, `l2flow_cache` is leaked and never destroyed (module unload only calls `kmem_cache_destroy` if `abm_l2flow_table_init()` returned 0 — see `abm_l2flow_table_exit` line 1169–1175).

**Fix:** `if (!brroute_cache) { kmem_cache_destroy(l2flow_cache); return -ENOMEM; }`.

### AB-06 — `abm_exit()` does not unregister sysctl

**File:** `ASK/auto_bridge/auto_bridge.c:1555–1567`.

```c
1555: static void abm_exit(void)
1556: {
1557:    printk(KERN_DEBUG "Exiting Automatic bridging module \n");
1558:    unregister_brevent_notifier(&abm_br_notifier);
1559:    cancel_work_sync(&abm_work_send_msg);
1560:    cancel_delayed_work_sync(&abm_work_retransmit);
1561:    destroy_workqueue(kabm_wq);
1562:    nf_unregister_net_hooks(&init_net, abm_ebt_ops, ARRAY_SIZE(abm_ebt_ops));
1563:    abm_nl_exit();
1564:    br_fdb_deregister_can_expire_cb();
1565:    abm_l2flow_table_exit();
1566:    abm_proc_fini();
1567: }
```

Missing call to `abm_sysctl_fini()` (defined at line 1500, marked `__maybe_unused`). Sysctl table leaks on module unload — and on next insmod the sysctl entries collide.

**Fix:** add `abm_sysctl_fini();` to `abm_exit()`.

### AB-07 — `rtnl_lock()` while holding `abm_lock` spinlock

**File:** `ASK/auto_bridge/auto_bridge.c:110, 137–147, 149`.

```c
110:    spin_lock_bh(&abm_lock);
...
137:    list_for_each_safe(entry, tmp, &bridge_list_rtevent){
138:        brtable_entry = container_of(entry, struct br_event_table, list_rtevent);
139:        if (brtable_entry->brdev)
140:        {
141:            rtnl_lock();             /* mutex_lock — sleeps! */
142:            rtmsg_ifinfo(RTM_NEWLINK, brtable_entry->brdev, 0, GFP_ATOMIC, 0, NULL);
143:            rtnl_unlock();
144:        }
145:        list_del(&brtable_entry->list_rtevent);
146:        kmem_cache_free(brroute_cache, brtable_entry);
147:    }
148:
149:    spin_unlock_bh(&abm_lock);
```

`rtnl_lock()` is `mutex_lock(&rtnl_mutex)` — sleeps. Calling it under `spin_lock_bh()` is a sleeping-in-atomic-context bug. With `CONFIG_DEBUG_ATOMIC_SLEEP=y`, this triggers a `BUG: scheduling while atomic`.

**Fix:** drop `abm_lock`, splice the `bridge_list_rtevent` to a local list under the lock, then iterate without the lock holding `rtnl_lock` separately.

### AB-08 — Busy-wait `schedule()` loop with no synchronization

**File:** `ASK/auto_bridge/auto_bridge.c:1109–1125`.

```c
1109: static __inline void abm_l2flow_table_wait_timers(void)
1110: {
1111:    int i, empty;
1112: test_list:
1113:    empty = 1;
1114:    for(i = 0; i < L2FLOW_HASH_TABLE_SIZE; i++)
1115:        if(!list_empty(&l2flow_table[i])){
1116:            empty = 0;
1117:            break;
1118:        }
1119:
1120:    if(empty)
1121:        return;
1122:    else{
1123:        schedule();
1124:    }goto test_list;
1125: }
```

Two issues:
1. `list_empty()` is read **without** holding `abm_lock` — race with timer callbacks mutating the list (data race / torn pointer).
2. Pure-spin "yield" loop — should use `wait_event` or `del_timer_sync` per-entry.

**Fix:** ensure `abm_l2flow_table_flush()` calls `del_timer_sync()` so no timer can fire after exit; remove `wait_timers()` entirely.

---

## P2 findings

### AB-09 — Macro hygiene: missing whitespace

**File:** `ASK/auto_bridge/auto_bridge_private.h:19–28`.

```c
#define L2FLOW_HASH_TABLE_SIZE1024
#define L2FLOW_HASH_BY_MAC_TABLE_SIZE 128
#define ABM_DEFAULT_MAX_ENTRIES5000
#define L2FLOW_FL_NEEDS_UPDATE0x1
#define L2FLOW_FL_DEAD0x2
#define L2FLOW_FL_WAIT_ACK0x4
#define L2FLOW_FL_PENDING_MSG0x8
```

Missing space between identifier and value. Same as fci F-11 — class **D16**. Compiles, but hostile to grep / parsers.

### AB-10 — Identifier whitespace damage in declarations

**File:** `ASK/auto_bridge/auto_bridge.c:54–69`. Globals like `struct list_headl2flow_table[...]`, `static struct sock*abm_nl`, `static charabm_l3_filtering`, `static unsigned intabm_max_entries`. Compiles only because the declarator (`*`, `[]`) provides a token boundary. Should be reformatted.

### AB-11 — Legacy `printk` usage

**File:** `ASK/auto_bridge/auto_bridge.c:864, 877` (inside `abm_build_l2flow`) — `printk(KERN_DEBUG …)` should be `pr_debug` / `netdev_dbg`. Also the `ABM_PRINT(KERN_ERR, …)` macro at `auto_bridge_private.h:132` lacks `pr_fmt` integration.

---

## Optimization candidates

| # | Class | File:Line | Notes |
|---|---|---|---|
| O-ab-1 | O3 | `auto_bridge.c:992, 1059` | `abm_ebt_hook` takes `spin_lock(&abm_lock)` per skb. Hot path. Convert to RCU read-side for lookup, fall back to spin_lock_bh only on insert/update. |
| O-ab-2 | O6 | `auto_bridge.c:646–660` | Linked-list bucket walk in `abm_l2flow_find()` — bound iterations or replace with `rhashtable`. Hash table is sized 1024 buckets for 5000 entries (default `abm_max_entries`) → average chain length ~5; worst-case under collision attack unbounded. |
| O-ab-3 | O7 | `auto_bridge.c:63–70` | Globals `l2flow_cache`, `brroute_cache`, `abm_nl`, `abm_l3_filtering`, `abm_max_entries`, `abm_retransmit_time` — most are write-once/read-many; should be `__read_mostly`. (Note: `__read_mostly` is in comment form only — `static struct kmem_cache *l2flow_cache /*__read_mostly*/;`.) Same for `l2flow_timeouts[]` (line 77) and `abm_ebt_ops[]` (line 1064). |
| O-ab-4 | O5 | `auto_bridge.c:961–1062` | `abm_ebt_hook` Rx batch could use `prefetch(skb->data + skb->mac_header)` before `eth_hdr` deref — minor. |

---

## Recommendations / fix routing

| Finding | Routing | Patch target |
|---|---|---|
| AB-01, AB-02 | **Kernel-side** (`ASK/auto_bridge/auto_bridge.c`) | Single hardening patch: capability check + nla_policy with `.len=16` for IP_SRC/IP_DST + clamped `memcpy` defense-in-depth. |
| AB-03 | Kernel-side | Capture `netlink_kernel_create` return; propagate -ENOMEM. |
| AB-04, AB-05, AB-06 | Kernel-side | Init/exit symmetry rewrite — add goto-ladder + `abm_sysctl_fini` call. |
| AB-07 | Kernel-side | Splice `bridge_list_rtevent` to local list under lock; drop lock; iterate with `rtnl_lock` per entry. |
| AB-08 | Kernel-side | Replace busy-wait with `del_timer_sync` per entry in `abm_l2flow_del`. |
| AB-09, AB-10, AB-11 | Janitorial | Reformat once. |

**Suggested merge order:**
1. AB-01 + AB-02 (heap/stack overflow + capability — security).
2. AB-03 (NULL deref hazard).
3. AB-07 (spinlock-mutex inversion — DEBUG_ATOMIC_SLEEP failure).
4. AB-04 + AB-05 + AB-06 (init/exit cleanup).
5. AB-08 (busy-wait race).
6. Janitorial.
