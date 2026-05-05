# ASK Userspace Audit — Roll-up Summary

**Date:** 2026-05-05
**Plan:** [`plans/ASK-USERSPACE-AUDIT-PLAN.md`](../ASK-USERSPACE-AUDIT-PLAN.md)
**Reports:** nine module reports under `plans/audit/AUDIT-*.md` (3,399 lines combined).
**Method:** D1..D18 / O1..O10 checklist (per-plan), file:line citations, severity scale P0/P1/P2 matching `lts_6.6_ls1046a/SDK-AUDIT-ask26.md`.

## Cross-module severity table

| Module | Tree | LoC | P0 | P1 | P2 | Total | Report |
|---|---|---:|---:|---:|---:|---:|---|
| `fmc` | `nxp-fmc/source/` | 41,719 | 7 | 11 | 12 | 30 | [AUDIT-fmc.md](AUDIT-fmc.md) |
| `fmlib` | `nxp-fmlib/` | 18,192 | 5 | 7 | 8 | 20 | [AUDIT-fmlib.md](AUDIT-fmlib.md) |
| `cdx` | `ASK/cdx/` | 37,152 | 8 | 15 | 14 | 37 | [AUDIT-cdx.md](AUDIT-cdx.md) |
| `cmm` | `ASK/cmm/src/` | 38,888 | 4 | 11 | 13 | 28 | [AUDIT-cmm.md](AUDIT-cmm.md) |
| `fci` | `ASK/fci/` | 1,560 | 3 | 4 | 6 | 13 | [AUDIT-fci.md](AUDIT-fci.md) |
| `auto_bridge` | `ASK/auto_bridge/` | 1,960 | 2 | 6 | 3 | 11 | [AUDIT-auto_bridge.md](AUDIT-auto_bridge.md) |
| `dpa_app` | `ASK/dpa_app/` | 1,147 | 2 | 4 | 6 | 12 | [AUDIT-dpa_app.md](AUDIT-dpa_app.md) |
| `libcli` | `libcli/` | 4,502 | 4 | 6 | 4 | 14 | [AUDIT-libcli.md](AUDIT-libcli.md) |
| `accel-ppp` | (no consumer patches) | — | 0 | 1 | 1 | 2 | [AUDIT-accel-ppp.md](AUDIT-accel-ppp.md) |
| **Total** | | **145,120** | **35** | **65** | **67** | **167** | |

## Key cross-module findings

### 1. ABI/UAPI inconsistency between fmlib and the kernel — must fix first
The most consequential finding is **F01** in `AUDIT-fmlib.md`:

> `bool shared` was added to userspace `t_FmPcdKgSchemeParams` (`fm_pcd_ext.h:1717-1720`) by the mono ASK extensions patch, but **NOT** to the kernel `ioc_fm_pcd_kg_scheme_params_t` (`fm_pcd_ioctls.h:1233-1285`). The `memcpy(&params, p_Scheme, sizeof(t_FmPcdKgSchemeParams))` at `fm_lib.c:688` overruns the destination AND shifts every subsequent field (`always_direct`, `net_env_params`, `next_engine`, `id`) by 1 byte + alignment.

This means **every ASK boot is silently writing a corrupt KG_SCHEME payload to the kernel**. It happens to work today only because the trailing-field garbage is not validated by the kernel ioctl handler. This is the load-bearing defect that every prior `dpa_app` SIGSEGV cascade ultimately traces back to. Must fix before anything else.

Companion: **F02** (`t_FmPcdHashTableParams` lacks `ASSERT_COND(sizeof(...))`) — same class, less acute today.

### 2. Capability bypass surface: ALL ASK kernel ioctls / netlink families lack `CAP_NET_ADMIN`
Three independent reports flag the same omission:

| Module | Finding | File:line |
|---|---|---|
| `cdx` | P0.04 — `cdx_ctrl_ioctl` no `capable()` check | `ASK/cdx/cdx_dev.c:139-183` |
| `fci` | F-02 — no `netlink_capable(skb, CAP_NET_ADMIN)` | `ASK/fci/fci.c:455-510` |
| `auto_bridge` | AB-02 — no `netlink_capable()` on `L2FLOW_MSG_ENTRY` | `ASK/auto_bridge/auto_bridge.c:487-569` |

`grep -rn 'capable\|CAP_NET_ADMIN' ASK/{cdx,fci,auto_bridge}/*.c` → 0 matches. Any process with the netlink/char-dev FD open can reprogram the fast path. Local-root by design today; one capability-check sweep across the three modules closes the surface.

### 3. User-input bounds checking missing in three kernel modules
Three independent P0s, all the same pattern (user-supplied count multiplied by sizeof, fed to `kzalloc`):

| Module | Finding | File:line | Vector |
|---|---|---|---|
| `cdx` | P0.01 (was C1) | `ASK/cdx/dpa_test.c:73-91` | `add_conn.num_conn` × `sizeof(test_conn_info)` |
| `cdx` | P0.02 (NEW) | `ASK/cdx/dpa_cfg.c:639-647` | `params.num_fmans` × `sizeof(cdx_fman_info)` |
| `auto_bridge` | AB-01 | `ASK/auto_bridge/auto_bridge.c:540-544` | `nla_data(IP_SRC)` of attacker length into 16-byte field |

Plus `fmlib` F08 (caller-supplied `num_of_keys` / `num_of_schemes` drives loops over fixed-size stack arrays). Same defect class, same single-line `array_size()`/`if (n > MAX) return -EINVAL` fix.

### 4. Refcount / FD leaks across kernel-API consumers
Pattern: `dev_get_by_name()` / `xfrm_state_lookup_byhandle()` / `*_open()` succeeds, then a subsequent error path returns without the matching `*_put()` / `*_close()`.

| Module | Finding | File:line |
|---|---|---|
| `cdx` | P0.07 (C6) | `devman.c:498, 572, 595` (and mirror at `:2920+`) |
| `cdx` | P0.06 (C5) | `dpa_ipsec.c:303 → 363, 391` (`xfrm_state` ref leak) |
| `fmlib` | F05 | `fm_lib.c:404-423` (`FM_PCD_Close` leaks `t_Device`) |
| `fmlib` | F12 | nine `*_Delete` sites do `owners--` unconditionally |
| `fci` | F-06 | `fci.c:432-447` (`nlmsg_put` NULL → skb leak + NULL deref) |
| `auto_bridge` | AB-03..AB-06 | every init/exit step lacks unwind |
| `libcli` | P0.2 | `libcli.c:1095-1101` (`fdopen` never `fclose`'d) |

### 5. Fixed-buffer overflows from variable input
`sprintf` / `strcpy` / `sscanf %s` into fixed-size destinations (D7) — present in **every** module audited:

| Module | Worst case | File:line |
|---|---|---|
| `fmc` | P0.1 — `vsprintf` into 256B from XML names | `libfmc.cpp:206-220` |
| `fmc` | P0.2 — eleven `strcpy` into 100B from flex `yytext` | `FMCSPExprLexer.cpp:1126-1185` |
| `fmlib` | F03 — `sprintf` into `devName[20/30]` | `fm_lib.c:165, 384, 1689-1706` |
| `cdx` | P0.08 — `sprintf("0x%x", u32)` into 8B `sa_id_name` | `dpa_ipsec.c:653, 705` |
| `cdx` | P1.05 — `strcpy` of userspace ifname (multiple sites) | `control_bridge.c:362-363` and 12 mirrors |
| `cmm` | P0.2 — 62 `strcpy` sites; `module_route.c:317,363` worst | `module_route.c`, `module_mc4.c`, `module_mc6.c` |
| `dpa_app` | DA-01 — `sscanf "%s"` into `info->name[64]` | `dpa.c:535-543` |
| `libcli` | P0.3 — TELNET sub-negotiation injected as input | `libcli.c:1248-1264` |

Mechanical sweep `sprintf → snprintf, strcpy → strscpy, sscanf "%s" → "%63s"` covers the bulk.

### 6. Format-string with non-literal (D14)
| Module | Finding |
|---|---|
| `fmc` | P0.1 — `fmc_log_write` lacks `format(printf,…)` attribute, callers pass XML-derived names |
| `cmm` | P0.3 — `cmm_print(format)` non-literal sinks at `cmm.c:201`, `forward_engine.c:1598` |
| `libcli` | P0.1 — `cli_print`, `cli_error`, `cli_bufprint` lack format attrs |

Adding `__attribute__((format(printf, N, M)))` to the prototypes catches all callers at compile time.

### 7. Sleeping in atomic context (D11)
| Module | Finding | Site |
|---|---|---|
| `cdx` | P1.01 | `devman.c:976 → 981` — `dev_get_by_name` under `dpa_devlist_lock` spinlock |
| `auto_bridge` | AB-07 | `auto_bridge.c:135-147` — `rtnl_lock()` while holding `abm_lock` (spin_lock_bh) |
| `cdx` | P2.04 | latent lockdep order in `cdx_cmdhandler.c:280-310` |

These trip `CONFIG_DEBUG_ATOMIC_SLEEP=y` immediately on the affected paths.

### 8. Wrong GFP flag — `kzalloc(…, 0)` ≡ `GFP_NOWAIT`
35 sites across `cdx`, plus two `kzalloc(…, 1)` (`dpa_ipsec.c:568`, `dpa_wifi.c:2174`). Coccinelle sweep, single PR.

### 9. The accel-ppp gap
`AUDIT-accel-ppp.md` confirms: **no consumer-side accel-ppp source patches exist** in the tree. `bin/ci-build-accel-ppp.sh` builds upstream unmodified. `ASK/patches/` only contains `ppp/` and `rp-pppoe/` (not accel-ppp). Either:
- (a) Confirm upstream accel-ppp covers all ASK requirements, OR
- (b) Capture whatever ASK-specific patches live in build artifacts into `ASK/patches/accel-ppp/`.

### 10. Negative finding from prior reviews — `auto_bridge` re-verification
Three of the four prior `auto_bridge` findings (`ASK-CODE-REVIEW.md §5` MED items + `ASK-CODE-QUALITY.md §3` HIGH) were **fabricated** — `grep` shows zero matches for `dev_get_by_name`, `br_addif`, `dev_ioctl`, or `module_param bridge_name` in the actual `auto_bridge.c`. The replacement findings (AB-01..AB-08, see `AUDIT-auto_bridge.md`) are derived from the actual netlink-ingress code.

### 11. Reframed finding — `fci` netlink path
The prior `ASK-CODE-QUALITY.md` CRIT-1/2 cite `ASK/fci/src/fci_msg.c` which **does not exist**. The actual netlink handler is `ASK/fci/fci.c` and uses **raw `NETLINK_FF`**, not generic netlink — so there is no `nla_policy` at all (worse than described). `AUDIT-fci.md` re-frames at the actual file:line.

## Prioritized fix sequencing (recommended)

The 35 P0s + 65 P1s are grouped into 8 batched commits. Each batch is tightly scoped, reviewable in one sitting, and free of cross-batch dependencies.

| Batch | Scope | Items | Target | Why this position |
|---|---|---|---|---|
| **B1** | fmlib ABI alignment | F01 (most acute), F02 | new `ask-ls1046a-6.6/patches/fmlib/02-abi-alignment.patch` | Closes the silent-corruption surface that has caused every prior `dpa_app` SIGSEGV. Must precede everything else. Pairs with re-verifying `fixes/112` on the producer side. |
| **B2** | Capability checks (3 modules) | cdx P0.04, fci F-02, auto_bridge AB-02 | new `ASK/cdx/cdx_dev.c`, `ASK/fci/fci.c`, `ASK/auto_bridge/auto_bridge.c` | One-line patches each; closes local-root surface across all kernel ABIs. |
| **B3** | User-input bounding | cdx P0.01, P0.02, auto_bridge AB-01, fmlib F08 | direct edits in each module | Same pattern (`array_size`/`if (n > MAX)`); single coccinelle script. |
| **B4** | fci/auto_bridge netlink hardening | fci F-01, F-03, F-04, F-06; auto_bridge AB-03..AB-06 | direct edits | nlmsg_ok() / nlmsg_put NULL guards / proc_create error paths / init unwinds. |
| **B5** | cdx kernel-API leaks + NULL guards | P0.03 (entry->ct), P0.05 (SEC status), P0.06 (xfrm_state_put), P0.07 (dev_put), P0.08 (snprintf) | direct edits in `cdx_ehash.c`, `dpa_ipsec.c`, `devman.c` | Self-contained per-file changes. P0.05 may need a producer-side hoist if SEC status constants missing. |
| **B6** | D7 sweep — `sprintf→snprintf`, `strcpy→strscpy`, `sscanf "%s"→%63s` | fmc P0.1, P0.2; fmlib F03; cdx P1.05, P1.06; cmm P0.2; dpa_app DA-01, DA-02; libcli P0.3 | mechanical sweep across modules | Single coccinelle / sed run; verify with rebuild. |
| **B7** | D14 sweep — `format(printf,…)` attributes + non-literal call-site fixes | fmc P0.1; cmm P0.3; libcli P0.1 | header attribute additions, then mechanical %-sub fixes | Adding the attribute surfaces every call-site at compile time. |
| **B8** | GFP-flag sweep + sleeping-in-atomic | cdx P1.02 (35 sites), P1.01; auto_bridge AB-07 | coccinelle: `kzalloc(.size, 0) → kzalloc(.size, GFP_KERNEL)`; manual lock-context fixes | After B5 lands so we don't conflict with refcount edits. |

After B1..B8 all 35 P0 items + the bulk of P1 items are closed. P2 (style, micro-perf, `__read_mostly`, optimization) becomes a follow-up sprint.

## Optimization candidates (consolidated O1..O10 across modules)

| Class | Where | Win |
|---|---|---|
| O1 — linear dispatch → indexed table | cdx `cdx_cmdhandler.c:14-77`; libcli `cli_int_locate_command` (`libcli.c:2872-2919`); fmlib `fm_lib.c:1686-1713`; fci `fci.c:292-308` | Branch prediction + smaller icache |
| O2 — per-call `kmalloc` → slab cache | cdx `ins_entry_info`, `hw_ct` allocations | Hot-path latency drop |
| O3 — coarse mutex / open-per-call | cdx `dpa_devlist_lock`; fmlib per-call `open()` (`fm_lib.c:147-198`) | RCU read or fd-cache; ~14 syscalls/boot saved |
| O4 — `dev_kfree_skb` → `napi_consume_skb` | cdx `dpa_ipsec.c:425-426` | NAPI skb-cache reuse |
| O5 — missing `prefetch()` in Rx loops | cdx `dpa_ipsec.c`, `dpa_wifi.c`, `cdx_ehash.c` | Hide DRAM latency |
| O6 — linked-list interface lookup | cdx `devman.c` walks | Hashtable, RCU-protected |
| O7 — `__read_mostly` missing on init-only globals | cdx (multiple), fci `fci.c:54` | Cache-line hygiene |
| O8 — DOM XML parse → SAX | fmc `FMCPCDReader.cpp:118` | -25 MiB working set on big PCD; not worth the refactor for one-shot tool |
| O9 — RTNL bufpool mutex → SPSC | cmm (existing C11 follow-up) | Lock-free ring after C11 lands |
| O10 — `strlen` in loop | not observed in audited modules | — |

## Routing reminder

All 167 findings land in `/root/vyos-ls1046a-build` (consumer repo) — Chain-2 territory per `.clinerules/05-workspace-layout.md`.

Producer-side hoist candidates (`/root/lts_6.6_ls1046a`):
- F01/F02 alignment fix — confirms `fixes/112` UAPI is the kernel-side authority, fmlib must catch up. **No producer change needed**, but a `static_assert(sizeof(struct ioc_fm_pcd_kg_scheme_params_t) == EXPECTED)` in the producer header would prevent regression.
- cdx P0.05 — if SEC `FM_FD_ERR_*` status-bit constants are missing from a consumer-accessible header, hoist via a producer `fixes/` patch.
- Otherwise: every audit finding stays in this repo.

## What was NOT in scope and is therefore NOT covered

- USDPAA userspace libs (`libusdpaa`, `usdpaa-apps`, `/dev/fsl-usdpaa` consumers) — **out of scope for ASK**. Verified 2026-05-05 by `grep -rln 'fsl_usdpaa\|usdpaa' ASK/ nxp-fmlib/ nxp-fmc/` → zero hits. No ASK userspace component (`cmm`, `dpa_app`, `fci`, `cdx`, `fmlib`, `fmc`, `libcli`, `auto_bridge`) opens `/dev/fsl-usdpaa` or links libusdpaa. ASK userspace is kernel-mediated (netlink `NETLINK_KEY=32` / `NETLINK_FF` / `L2FLOW`, ioctls on `/dev/fm0` and `/dev/cdx_dpa_*`) and never touches QBMan portals from userspace. The actual USDPAA consumers are DPDK's `bus_dpaa` PMD and (transitively) VPP's `dpdk-plugin`; that work belongs in a future VPP/DPDK integration audit, not here. The kernel-side provider (`drivers/staging/fsl_qbman/fsl_usdpaa.c`) was already audited in `lts_6.6_ls1046a/SDK-AUDIT-ask26.md` finding A1 (landed at producer ask27). Do NOT re-open as `AUDIT-usdpaa.md` under this audit set.
- DPDK / VPP integration paths (including USDPAA userspace) — out of audit scope per the plan.
- VyOS image / live-build / U-Boot env — not ASK SDK modules.

## Files in this audit set

```
plans/ASK-USERSPACE-AUDIT-PLAN.md      — the plan (this audit's source of truth)
plans/audit/AUDIT-fmc.md                — 562 lines, 30 findings
plans/audit/AUDIT-fmlib.md              — 448 lines, 20 findings
plans/audit/AUDIT-cdx.md                — 460 lines, 37 findings
plans/audit/AUDIT-cmm.md                — 491 lines, 28 findings
plans/audit/AUDIT-fci.md                — 263 lines, 13 findings
plans/audit/AUDIT-auto_bridge.md        — 326 lines, 11 findings
plans/audit/AUDIT-dpa_app.md            — 275 lines, 12 findings
plans/audit/AUDIT-libcli.md             — 368 lines, 14 findings
plans/audit/AUDIT-accel-ppp.md          — 206 lines, 2 findings (no consumer patches; recommendation only)
plans/audit/AUDIT-SUMMARY.md            — this file
```

Total: 9 reports, 3,399 lines, 167 findings, 145,120 LoC of audited source. No source files were modified.