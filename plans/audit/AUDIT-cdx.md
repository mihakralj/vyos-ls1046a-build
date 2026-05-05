# AUDIT-cdx

**Module:** ASK kernel OOT module (`/root/vyos-ls1046a-build/ASK/cdx/`)
**Source size:** 43 `.c` TUs, **37,152 LoC** of C (44,270 in plan includes headers + generated `.mod.c`).
**Audit lens:** D1..D18 + O1..O10 from `plans/ASK-USERSPACE-AUDIT-PLAN.md`.
**Prior audit re-verified:** `ask-ls1046a-6.6/ASK-CODE-REVIEW.md §1` (C1..C10, M1..M4).

---

## Findings summary

| Severity | Count |
|----------|------:|
| **P0** (kernel oops / heap corruption / privilege bypass) | **8** |
| **P1** (resource leak, race, atomic-context sleep, security spec) | **15** |
| **P2** (defensive hardening, ABI hygiene, optimization) | **14** |
| **Total** | **37** |

Optimization candidates (O1..O10): **9** consolidated items at the bottom.

---

## Module inventory

| Item | Value |
|------|-------|
| TU count | 43 `.c` files |
| Header count | ~30 `.h` |
| Total C LoC | 37,152 |
| Hottest TUs | `cdx_ehash.c` (4,175), `devman.c` (3,013), `cdx_dpa_ipsec.c` (2,742), `dpa_ipsec.c` (~3.4k), `dpa_wifi.c` (~3.3k), `dpa_cfg.c` (1,146) |
| Build artifact | `cdx.ko` |
| Userspace ABI | char dev `/dev/cdx_ctrl` (`CDX_CTRL_CDEVNAME`), sysfs class `cdx_ctrl_class`, `unlocked_ioctl` + `compat_ioctl` |
| ioctl families | `CDX_CTRL_DPA_SET_PARAMS`, `CDX_CTRL_DPA_CONNADD`, `CDX_CTRL_DPA_GET_MURAM_DATA`, `CDX_CTRL_DPA_QOS_CONFIG_ADD`, `CDX_CTRL_DPA_ADD_MCAST_GROUP/_MEMBER/_TABLE_ENTRY` (`cdx_dev.c:142-179`); wifi `vap_cmd_s` (`dpa_wifi.c:3101`) |
| Netlink | none in `cdx` (FCI module owns netlink) |
| Sysctl | none |
| Exported symbols | 11: `comcerto_fpp_send_command{,_simple,_atomic}`, `comcerto_fpp_register_event_cb`, `cdx_wifi_rx_fastpath`, `dpa_get_pcdhandle`, `dpa_get_fm_MURAM_handle`, `display_itf`, `display_route_entry`, `display_ctentry`, `display_SockEntries` |
| Origin tags | Mindspeed Comcerto 2007–2010 → Freescale 2014–2016 → NXP 2017–2021 |

---

## Re-verification of prior findings (`ASK-CODE-REVIEW.md §1`)

| ID | Subject | Current file:line | Status |
|----|---------|-------------------|--------|
| **C1** | `dpa_test.c` `kzalloc(sizeof*num_conn,0)` user-controlled multiplication | `dpa_test.c:80–82, 91` | **STILL OPEN** — `add_conn.num_conn` from `copy_from_user(&add_conn,args,…)` (`:73`) flows unchecked into `kzalloc(sizeof(struct test_conn_info)*add_conn.num_conn,0)` and again into `copy_from_user(conn_info,…,sizeof*num_conn)`. No `array_size()`, no upper bound. |
| **C2** | `kzalloc(…, 0)` (== `GFP_NOWAIT`) on hot paths | `cdx_ehash.c:849, 1053, 1323, 1547, 3267, 3731, 3884`; `cdx_ifstats.c:98`; `cdx_reassm.c:238, 311`; `devman.c:2024, 2181, 2267, 2352, 2605, 2808`; `devoh.c:339, 539`; `dpa_cfg.c:195, 301, 380, 646`; `dpa_control_mc.c:341, 347, 440, 1157, 1163, 1184, 1190`; `dpa_ipsec.c:1009, 1045, 1093`; `dpa_test.c:82, 98, 103`; `dpa_wifi.c:1830, 2300, 2411`; `cdx_dpa_ipsec.c:2479` | **STILL OPEN, BROADER** — 35 sites. Two extra sites pass `1` (legacy alias for `GFP_ATOMIC` numeric value, ill-typed): `dpa_ipsec.c:568`, `dpa_wifi.c:2174`. |
| **C3** | `entry->ct->td` deref with no `entry->ct` NULL check | `cdx_ehash.c:484` | **STILL OPEN** — `if (!entry) return FAILURE;` then `ExternalHashTableDeleteKey(entry->ct->td, …)`. Mirrors at `:498-505` (`ct = entry->hw_entry.ct; … ExternalHashTableDeleteKey(ct->td …)` without `ct` NULL check). |
| **C4** | SEC dq status not checked in IPsec exception path | `dpa_ipsec.c:242` | **STILL OPEN** — `/* check SEC errors here */` is still a placeholder; `dq->fd.status` only printed in `#ifdef DPA_IPSEC_DEBUG1` (`:245`). ICV-failed frames re-injected via `netif_receive_skb` (`:411`). |
| **C5** | `xfrm_state_lookup_byhandle` ref leak on drop paths | `dpa_ipsec.c:303 → 363, 391, 422, 426` | **PARTIALLY OPEN** — once `sp->xvec[0] = x` (`:386`) the skb owns the ref, so the post-`sp` `pkt_drop` is correct. But `:363` (`dpaa_eth_refill_bpools` fail) jumps to `pkt_drop` *before* `sp` is allocated; on that path `x` is leaked. Same hazard if `skb_ext_add` returns NULL (`:382`). |
| **C6** | `dev_get_by_name` refcount leak in `devman.c` | `devman.c:498, 572, 595` | **STILL OPEN** — early returns `FAILURE` after `dev_get_by_name` succeeds (`num_pools > MAX_PORT_BMAN_POOLS`, `dpa_get_tx_chnl_info` fail) without `dev_put`. Mirror at `:2920+`. |
| **C7** | `sa_id_name[8]` overflow via `sprintf("0x%x",handle)` for u32 | `dpa_ipsec.c:653` (decl), `:705` (use) | **STILL OPEN** — buffer 8 B; `"0x" + 8 hex + NUL = 11`. Stack overflow for handles > 0xFFFF. |
| **C8** | `EHASH_IPV6_FLOW(1<<11)` function-like-macro typo | `cdx_ehash.c:69` | **CLOSED / FALSE-POSITIVE** — `cat -A cdx_ehash.c:69` shows a TAB (`^I`) between identifier and value, so cpp parses as `#define EHASH_IPV6_FLOW (1<<11)`. Prior audit mistook tab for missing space. Recommend single-space normalization for clarity. |
| **C9** | Unbounded hash collision walk | `cdx_ehash.c` (`ExternalHashTable*` opaque API) | **OPEN — UNVERIFIED at this layer** — chain length not enforced inside `cdx_ehash.c`; depends on producer's `en_exthash_*`. Flag for FM-driver review. |
| **C10** | No `capable(CAP_NET_ADMIN)` on `/dev/cdx_ctrl` ioctl | `cdx_dev.c:139–183`; `dpa_wifi.c:3094+` | **STILL OPEN** — `grep -n 'capable\|CAP_NET_ADMIN\|CAP_SYS_ADMIN' ASK/cdx/*.c` → 0 matches. |
| **M1** | Per-call `dev_get_by_name()` in interface enumeration | `devman.c:498, 981, 2324, 2920` | **STILL OPEN** |
| **M2** | `register_cdx_deinit_func` silent overflow | `cdx_main.c:23-32` | **STILL OPEN** — `printk` only; returns `void`; caller cannot detect failure. |
| **M3** | Linear command dispatch `FCODE_TO_EVENT` | `cdx_cmdhandler.c:14-77` | **STILL OPEN** — 22-way `switch`. |
| **M4** | L2 bridge MAC/name validation | `control_bridge.c:362-363` | **RECLASSIFIED → P1** — `strcpy` of userspace-sourced ifname, see P1.05. |

---

## P0 findings

### P0.01 — User-controlled `num_conn` * sizeof multiplication, then mismatched copy
**File:line:** `dpa_test.c:73-91` (re-verifies C1)
**Defect class:** D4, D9, D2.
```c
copy_from_user(&add_conn, (void *)args, sizeof(struct add_conn_info));
…
conn_info = kzalloc((sizeof(struct test_conn_info) * add_conn.num_conn), 0);
…
copy_from_user(conn_info, add_conn.conn_info,
               (sizeof(struct test_conn_info) * add_conn.num_conn));
```
**Recommendation:** cap `add_conn.num_conn` at a sane max (e.g. 4096); use `array_size()`/`kvmalloc_array(num_conn, sizeof…, GFP_KERNEL)`. Gate ioctl on `capable(CAP_NET_ADMIN)`.
**Fix routing:** **cdx in-tree**.

### P0.02 — User-controlled `num_fmans` * sizeof in set_dpa_params
**File:line:** `dpa_cfg.c:639-647`
**Defect class:** D4, D9, D2.
```c
copy_from_user(&params, args, sizeof(params));
mem_size = sizeof(struct cdx_fman_info) * params.num_fmans;
fman_info = kzalloc(mem_size, 0);
```
LS1046A has exactly one FMan; cap at `MAX_FMANS=8`. Use `kvmalloc_array(num_fmans, sizeof…, GFP_KERNEL|__GFP_ZERO)`.
**Fix routing:** **cdx in-tree**.

### P0.03 — `entry->ct` NULL deref in delete paths
**File:line:** `cdx_ehash.c:484-491` and mirror `:498-505`
**Defect class:** D6.
```c
if (!entry) return FAILURE;
if (ExternalHashTableDeleteKey(entry->ct->td,
        entry->ct->index, entry->ct->handle)) { … }
```
**Recommendation:** `if (!entry || !entry->ct) return FAILURE;`.
**Fix routing:** **cdx in-tree**.

### P0.04 — No capability check on `/dev/cdx_ctrl` ioctl
**File:line:** `cdx_dev.c:139-183` (`cdx_ctrl_ioctl`)
**Defect class:** D12.
```c
long cdx_ctrl_ioctl(struct file *filp, unsigned int cmd, unsigned long args) {
    switch (cmd) {
    case CDX_CTRL_DPA_SET_PARAMS:    retval = cdx_ioc_set_dpa_params(args); break;
    case CDX_CTRL_DPA_CONNADD:       retval = cdx_ioc_dpa_connadd(args);    break;
    …
    }
}
```
**Recommendation:** add `if (!capable(CAP_NET_ADMIN)) return -EPERM;` at top, and same in wifi `vap_cmd` ioctl.
**Fix routing:** **cdx in-tree**.

### P0.05 — SEC ICV/auth status never validated
**File:line:** `dpa_ipsec.c:242-260, 411`
**Defect class:** D-class N/A (security spec defect).
The QMan dequeue from the SEC exception FQ is the path used when CAAM rejects an ESP packet (decrypt error, ICV mismatch, replay window). Code reads `dq->fd.status` only inside `#ifdef DPA_IPSEC_DEBUG1`. Frame is then re-injected to `netif_receive_skb()` at `:411`. Tampered ciphertext failing ICV is delivered to host stack as good.
**Recommendation:** decode `dq->fd.status & FM_FD_ERR_*`; on any SEC-error bit set, drop with per-SA counter + `goto rel_fd`.
**Fix routing:** **cdx in-tree**; if status bit constants are not in consumer-accessible header → **producer fixes/** hoist.

### P0.06 — `xfrm_state` ref leak on bpool refill failure
**File:line:** `dpa_ipsec.c:303 → 363`
**Defect class:** D5, D17.
```c
if ((x = xfrm_state_lookup_byhandle(dev_net(net_dev), sagd_pkt)) == NULL)
    goto rel_fd;
…
if (unlikely(dpaa_eth_refill_bpools(…)))
    goto pkt_drop;          /* x ref held, not released */
…
sp = skb_ext_add(skb, SKB_EXT_SEC_PATH);
if (!sp) goto pkt_drop;     /* x ref held; sp not yet assigned */
sp->xvec[0] = x;            /* ref ownership transferred here */
```
**Recommendation:** call `xfrm_state_put(x);` on every drop path between successful lookup and `sp->xvec[0] = x`.
**Fix routing:** **cdx in-tree**.

### P0.07 — `dev_get_by_name` refcount leak (devman set/lookup)
**File:line:** `devman.c:498, 572, 595` (re-verifies C6); mirror at `:2920+`
**Defect class:** D5.
```c
device = dev_get_by_name(&init_net, name);
…
if (eth_info->num_pools > MAX_PORT_BMAN_POOLS) {
    DPA_ERROR(…);
    return FAILURE;     /* missing dev_put(device) */
}
…
if (dpa_get_tx_chnl_info(…)) {
    DPA_ERROR(…);
    return FAILURE;     /* missing dev_put(device) */
}
```
**Recommendation:** single `out_put:` exit with `dev_put(device)`.
**Fix routing:** **cdx in-tree**.

### P0.08 — `sprintf("0x%x", handle)` to `sa_id_name[8]`
**File:line:** `dpa_ipsec.c:653, 705` (re-verifies C7)
**Defect class:** D7.
**Recommendation:** widen to `[12]`, switch to `snprintf(sa_id_name, sizeof sa_id_name, "0x%x", handle)`.
**Fix routing:** **cdx in-tree**.

---

## P1 findings

### P1.01 — `dev_get_by_name` invoked under `dpa_devlist_lock` spinlock
**File:line:** `devman.c:976 → 981`
**Defect class:** D11.
```c
spin_lock(&dpa_devlist_lock);
…
device = dev_get_by_name(&init_net, iface_info->name);   /* may sleep */
```
Same shape at `devman.c:2920+`. `dev_get_by_name` uses RCU/RTNL paths and `might_sleep`.
**Recommendation:** drop spinlock before `dev_get_by_name`; or convert to mutex (this lock guards a long-lived global list, no IRQ context).
**Fix routing:** **cdx in-tree**.

### P1.02 — 35+ `kzalloc(…, 0)` sites — silent OOM under pressure
**File:line:** see C2 row.
**Defect class:** D2.
`0 == GFP_NOWAIT` (no `__GFP_RECLAIM`). Hot-path flow-add silently fails `-ENOMEM` and forwarding falls back to slow path with no observability.
**Recommendation:** mechanical sweep `kzalloc(…, 0) → kzalloc(…, GFP_KERNEL)` (or `GFP_ATOMIC` for IRQ/softirq sites). Two `kzalloc(…, 1)` sites (`dpa_ipsec.c:568`, `dpa_wifi.c:2174`) → replace numeric `1` with `GFP_ATOMIC`.
**Fix routing:** **cdx in-tree** — coccinelle.

### P1.03 — `register_cdx_deinit_func` overflow → silent leak on rmmod
**File:line:** `cdx_main.c:23-32`
**Defect class:** D17.
```c
if (init_level == MAX_CDX_INIT_FUNCTIONS) {
    printk("%s::cant register…\n", __FUNCTION__);
    return;
}
```
Returns `void`; callers cannot detect failure → on rmmod, missed deinit leaves devices/classes registered.
**Recommendation:** convert to `int` return; bubble up; abort module load on overflow.
**Fix routing:** **cdx in-tree**.

### P1.04 — `start_dpa_app` leaks `argv` on `call_usermodehelper_setup` failure
**File:line:** `cdx_main.c:88-100`
```c
char **argv = kmalloc(sizeof(char *[3]), GFP_KERNEL);
if (!argv) return -ENOMEM;
…
info = call_usermodehelper_setup(…, cdx_free_modprobe_argv, NULL);
if (info) { retval = call_usermodehelper_exec(info, …); }
return retval;             /* info==NULL → argv never freed */
```
**Defect class:** D17.
**Recommendation:** `if (!info) { kfree(argv); return -ENOMEM; }`.
**Fix routing:** **cdx in-tree**.

### P1.05 — Unbounded `strcpy` of userspace-sourced bridge ifname
**File:line:** `control_bridge.c:362-363`
```c
strcpy(&l2flow_entry->out_ifname[0], pcmd->output_name);
strcpy(&l2flow_entry->in_ifname[0], pcmd->input_name);
```
`pcmd` arrives from the FPP command channel (ultimately FCI/userspace). If `output_name` is exactly `IFNAMSIZ` long without NUL, overrun.
**Defect class:** D7.
**Recommendation:** `strscpy(dst, src, sizeof dst)`. Mirror sweep: `cdx_mc_query.c:90, 258`; `control_ipv4.c:1652, 1661`; `control_pppoe.c:500-506`; `control_tunnel.c:877`; `control_vlan.c:295, 296, 436, 437`; `dpa_cfg.c:342`.
**Fix routing:** **cdx in-tree**.

### P1.06 — `sprintf` to fixed-size buffers (no overflow check)
**File:line:** `devoh.c:328` (`oh_iface_name[8]` with `"oh%d"`), `:591` (`port_info->name` with `"dpa-fman%d-oh@%d"`); `cdx_ehash.c:3165-3203` `/proc` dump uses `sprintf(buf+tot_len, …)` with no remaining-size accounting; `dpa_wifi.c:453-467` likewise.
**Defect class:** D7.
**Recommendation:** convert all to `snprintf(dst, sizeof(dst) - tot_len, …)` (or `seq_printf` for `/proc`).
**Fix routing:** **cdx in-tree**.

### P1.07 — `bp_count` cast to signed `int` then bound-checked
**File:line:** `devman.c:570-583`
**Defect class:** D4.
```c
eth_info->num_pools = (int)priv->bp_count;
if (eth_info->num_pools > MAX_PORT_BMAN_POOLS) { return FAILURE; }
```
Order of check is correct, but missing `dev_put` (P0.07). The signed cast hides any negative count from a compiler-detected anomaly. Tighten by typing `num_pools` `unsigned`.
**Recommendation:** `unsigned`, plus invariant comment.
**Fix routing:** **cdx in-tree**.

### P1.08 — `cdx_ctrl_timer_init` partial-init resource asymmetry
**File:line:** `cdx_timer.c:184-205`
**Defect class:** D17.
- `kthread_create` succeeds, `kmalloc(timer_inner_wheel)` fails → kthread leaked (no `kthread_stop`).
- inner OK, outer fails → inner leaked.
- `error:` label calls `register_cdx_deinit_func(cdx_ctrl_timer_exit)` **even on failure** → later double-free / NULL deref.
**Recommendation:** distinct error labels; only register the deinit func on `rc == 0`.
**Fix routing:** **cdx in-tree**.

### P1.09 — `cdx_ipsec_delete_fp_hash_entry` returns 0 from `void` function
**File:line:** `cdx_dpa_ipsec.c:300-308`
```c
static void cdx_ipsec_delete_fp_hash_entry(PSAEntry pSA)
{
    …
    if ((pSA->ct) && (pSA->ct->handle)) {
        …
        return 0;    /* `void` returning a value */
    }
}
```
**Defect class:** compile hygiene; suppressed by Makefile `-Wno-*`.
**Recommendation:** strip `0`; remove any `-Wno-return-type`.
**Fix routing:** **cdx in-tree**.

### P1.10 — `release_cfg_info` cleanup symmetry on success path
**File:line:** `dpa_cfg.c:631-687, ~744+`
**Defect class:** D17.
The save/restore pattern at `:631-678` correctly avoids `kfree(userspace_ptr)` on error paths. Verify the **success** path at `:744+` calls `kfree(uspace_portinfo); kfree(uspace_tblinfo);` (the inspection window stops at `:687`). If missing → 2 small kernel-mem leaks per ioctl.
**Recommendation:** confirm and add if absent; static-test with malformed `params`.
**Fix routing:** **cdx in-tree**.

### P1.11 — Insertion-path `info` may leak on subsequent alloc failure
**File:line:** `cdx_dpa_ipsec.c:2479-2495`; `cdx_ehash.c:849, 1053, 1323, 1547`
**Defect class:** D17.
```c
info = kzalloc(sizeof(struct ins_entry_info), 0);
if (!info) return FAILURE;
…
sa->ct = kzalloc(sizeof(struct hw_ct), GFP_KERNEL);
if (!sa->ct) goto err_ret;     /* does err_ret kfree(info)? */
```
**Recommendation:** confirm `err_ret:` `kfree(info)` is reached on every path; the inspected window doesn't show the label.
**Fix routing:** **cdx in-tree**.

### P1.12 — IPsec descriptor key allocation chain — leak audit
**File:line:** `cdx_dpa_ipsec.c:405-462`
**Defect class:** D17.
Sequential `kzalloc(GFP_KERNEL)` for cipher key, auth key, auth split-key, extra-cmds, descbuf. Each error path calls `cdx_ipsec_sec_sa_context_free(pdpa_sec_context)`. Verify that helper frees every previously-allocated field; missing field → silent leak per failed SA install.
**Recommendation:** code-walk `cdx_ipsec_sec_sa_context_free()`; consider `devm_*` allocations.
**Fix routing:** **cdx in-tree**.

### P1.13 — `cdx_ctrl_open` single-open gate is racy in pattern
**File:line:** `cdx_dev.c:55-62`
**Defect class:** D10 (subtle).
```c
if (!atomic_dec_and_test(&cdx_ctrl_open_count)) {
    atomic_inc(&cdx_ctrl_open_count);
    return -EBUSY;
}
```
Functionally correct (count starts at 1, single-open) but a transient `0 → -1 → 0` is observable to other threads.
**Recommendation:** `atomic_cmpxchg(&open, 0, 1)` clean pattern; `atomic_set(0)` in release.
**Fix routing:** **cdx in-tree**.

### P1.14 — `event_cb` plain pointer write/read across CPUs
**File:line:** `cdx_cmdhandler.c:393-404`
```c
ctrl->event_cb = event_cb;     /* plain store */
```
Read on the IRQ/dequeue path (FCI consumer). On ARM64, no `WRITE_ONCE`/`smp_store_release`/`READ_ONCE` → torn-pointer hazard.
**Defect class:** D10.
**Recommendation:** `smp_store_release` + `smp_load_acquire` (or `rcu_assign_pointer`/`rcu_dereference`); add an `unregister` API.
**Fix routing:** **cdx in-tree** + mirror in **fci**.

### P1.15 — `compat_ioctl` is a no-op stub
**File:line:** `cdx_dev.c:184-189`
```c
long cdx_ctrl_compat_ioctl(struct file *filp, unsigned int cmd, unsigned long args)
{
    DPA_INFO("%s::\n", __FUNCTION__);
    return 0;       /* every 32-bit ioctl silently "succeeds" */
}
```
**Defect class:** D18.
**Recommendation:** either route to `cdx_ctrl_ioctl` (struct layouts must be 32/64-bit-clean) or remove the field so the kernel returns `-ENOIOCTLCMD` for 32-bit callers.
**Fix routing:** **cdx in-tree**.

---

## P2 findings

### P2.01 — Linear command dispatcher `FCODE_TO_EVENT`
**File:line:** `cdx_cmdhandler.c:14-77`
**D-class / O-class:** O1.
22-way `switch` on `(fcode & 0xFF00) >> 8`.
**Recommendation:** static `[FC_MAX] = { [FC_RX] = EVENT_PKT_RX, … }` lookup.

### P2.02 — `gCmdProcTable[eventid]` upper-bound not checked
**File:line:** `cdx_cmdhandler.c:94`
**D-class:** D6 / D-class array bounds.
`eventid` checked `>= 0` only; if `EVENT_MAX` ever shrinks vs. enum returned by `FCODE_TO_EVENT`, OOB read.
**Recommendation:** add `eventid < EVENT_MAX`.

### P2.03 — `comcerto_fpp_send_command` holds one global mutex across every dispatch
**File:line:** `cdx_cmdhandler.c:227-238`
**O-class:** O3.
A single `ctrl->mutex` serialises all commands. Per-event-class lock or RCU-read for snapshot ioctls would lift contention.

### P2.04 — Mutex acquired on workqueue worker while holding spinlock semantics
**File:line:** `cdx_cmdhandler.c:280-310`
**Defect class:** D11 (latent — current ordering is OK only because spinlock is dropped before mutex).
Lock chain `spin_lock_irqsave → unlock → mutex_lock` is hard to audit without `lockdep_assert_held`. Add lockdep annotations.

### P2.05 — `u16 rbuf[128]` (256 B) on stack across multiple call sites
**File:line:** `cdx_cmdhandler.c:262, 281, 391`
**O-class:** O2.
Combined call-stack consumption ~512 B. Move to per-CPU buffer or one-time kmalloc.

### P2.06 — `__read_mostly` missing on init-only globals
**File:line:** `cdx_cmdhandler.c:12` (`gCmdProcTable[EVENT_MAX]`); `cdx_main.c:20-21` (`init_level`, `deinit_fn[]`); `dpa_cfg.c` (`fman_info`, `num_fmans`)
**O-class:** O7.

### P2.07 — Hot-path `kzalloc` of `struct ins_entry_info`
**File:line:** `cdx_ehash.c:849, 1053, 1323, 1547, 3731, 3884`; `cdx_dpa_ipsec.c:2479`
**O-class:** O2.
Use `KMEM_CACHE(ins_entry_info, 0)` slab cache.

### P2.08 — Hot-path `kzalloc` of `struct hw_ct`
**File:line:** `cdx_ehash.c:935, 1108, 1379, 1557`; `cdx_dpa_ipsec.c:2489`
**O-class:** O2.
Second slab cache.

### P2.09 — `dev_kfree_skb` on NAPI-context drop path
**File:line:** `dpa_ipsec.c:425-426` (`pkt_drop:`)
**O-class:** O4.
Use `napi_consume_skb(skb, 64)` — frees through the per-CPU skb cache.

### P2.10 — `prefetch()` missing on Rx batch loops
**File:line:** `dpa_ipsec.c:333-337`; `cdx_ehash.c` insertion loops; `dpa_wifi.c` Rx
**O-class:** O5.
`prefetch(next_dq);` at top of each batch iteration.

### P2.11 — `dpa_interface_info` linear walks
**File:line:** `devman.c:980+, :1199+, :1454+, :1701+`
**O-class:** O6.
Convert to RCU-protected hashtable keyed on name and ifindex.

### P2.12 — `dpa_devlist_lock` is a spinlock around long ops
**File:line:** `devman.c:603, 868, 976, 1076, 1199, 1454, 1701`; `cdx_ifstats.c:66, 107, 128, 160, 168`
**O-class:** O3.
Convert to mutex (process-context only) or RCU read-side for lookups.

### P2.13 — `cdx_ctrl_compat_ioctl` ABI hazard
Already covered in P1.15.

### P2.14 — IOCTL command numbers — verify `_IOR/_IOW` size encoding
**File:line:** `cdx_ioctl.h` (not re-read in this audit window)
**Defect class:** D18.
**Recommendation:** confirm every `CDX_CTRL_DPA_*` macro uses `_IOR/_IOW(magic, nr, type)` so struct edits force the ioctl number to change. Add `static_assert(sizeof(struct cdx_ctrl_set_dpa_params) == EXPECTED)` in a header included by both producer and consumer.

---

## Optimization candidates (consolidated O1..O10)

| ID | Class | Site | Win |
|----|-------|------|-----|
| O1 | linear dispatch | `cdx_cmdhandler.c:14-77` | sparse table; smaller icache, predictable branch |
| O2 | per-call kmalloc | `cdx_ehash.c` insertion paths (`ins_entry_info`, `hw_ct`) | KMEM_CACHE per type |
| O3 | coarse mutex/spinlock | `cdx_cmdhandler.c:233`; `devman.c:dpa_devlist_lock` | per-table lock or RCU-read |
| O4 | `dev_kfree_skb` in NAPI | `dpa_ipsec.c:425` | `napi_consume_skb` |
| O5 | missing prefetch | Rx loops `dpa_ipsec.c`, `dpa_wifi.c`, `cdx_ehash.c` | `prefetch(next_dq)` |
| O6 | linked-list lookup | `devman.c` interface search by name | hashtable |
| O7 | `__read_mostly` missing | init-time globals | cache-line hygiene |
| O8 | n/a (no XML in cdx) | — | — |
| O9 | n/a (no userspace ring in cdx) | — | — |
| O10 | `strlen` in loop | not observed | clean |

---

## Recommendations / fix routing

1. **Immediate (P0 sweep, single PR against `ASK/cdx/`):**
   - P0.01 + P0.02 — `array_size()` / `kvmalloc_array()` for every user-counter-driven `kzalloc`. Files: `dpa_test.c`, `dpa_cfg.c`.
   - P0.03 — `entry->ct` NULL guards (`cdx_ehash.c:484, 498`).
   - P0.04 — `capable(CAP_NET_ADMIN)` at top of `cdx_ctrl_ioctl` (`cdx_dev.c:139`) and wifi `vap_cmd` ioctl.
   - P0.05 — SEC `dq->fd.status` decode in `dpa_ipsec.c:242`. If status-bit constants missing → **producer fixes/** hoist.
   - P0.06 — `xfrm_state_put(x)` on the two leak paths in `dpa_ipsec.c:363, 391`.
   - P0.07 — single `out_put:` exit in `devman.c` get-eth-iface (`:498-606`) and mirror at `:2920+`.
   - P0.08 — `snprintf` + widen `sa_id_name` in `dpa_ipsec.c:653, 705`.

2. **Short-term (P1 sweep, second PR):**
   - Mechanical `kzalloc(…, 0) → kzalloc(…, GFP_KERNEL)` sweep across all 35 sites (coccinelle).
   - Lock semantics: drop `spin_lock(&dpa_devlist_lock)` before `dev_get_by_name`, or convert to mutex (P1.01 + P2.12).
   - `register_cdx_deinit_func` returns `int`; propagate failure (P1.03).
   - `strcpy → strscpy` sweep (`control_*.c`, `cdx_mc_query.c`, `dpa_cfg.c`, P1.05).
   - `start_dpa_app` argv leak (P1.04).
   - Timer init partial-init recovery (P1.08).
   - `compat_ioctl` policy decision (P1.15).
   - `event_cb` smp_store_release/READ_ONCE (P1.14).

3. **Medium-term (hardening + optimization):**
   - Slab caches for `ins_entry_info`, `hw_ct` (P2.07, P2.08).
   - `napi_consume_skb` on every NAPI-context drop path (P2.09).
   - Replace command dispatcher with indexed table (P2.01).
   - RCU-read-side for `dpa_interface_info` walks (P2.11, P2.12).
   - `__read_mostly` annotations (P2.06).
   - ABI: confirm `_IOR/_IOW` size encoding + add `static_assert` (P2.14).

4. **Hoist candidates to `/root/lts_6.6_ls1046a` (producer):**
   - SEC ICV-failure status decoding constants (if absent in producer's `dpaa_ipsec.h`) — couples to P0.05.
   - `xfrm_state_lookup_byhandle` is a producer-side extern (`dpa_ipsec.c:130`); review producer for ref-count semantics.

5. **Mono-extension / consumer-only:**
   - All capability checks (P0.04), user-input bounding (P0.01, P0.02), GFP-flag sweep, refcount-leak fixes — stay in `ASK/cdx/`.

---

*Report generated against current `/root/vyos-ls1046a-build/ASK/cdx/` tree. Line numbers verified via `grep -n` and `sed -n`. Findings without explicit terminal line numbers (P1.10, P1.11, P1.12, P2.14) are flagged for follow-up review of indicated function bodies (label tail not in the inspected window).*
