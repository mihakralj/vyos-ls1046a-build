# Technical Finding: Incomplete Function Inventory

**Version 1.0.0 · 2026-07-18 · HADS 1.0.0**

## AI READING INSTRUCTION

Read `**[SPEC]**` and `**[BUG]**` blocks for authoritative facts.
Read `**[NOTE]**` for rationale and history.
This document is a static analysis audit of the ASK2 codebase against the
`arch/fman-pcd-api-reference.md` v3.0.0 API contract. Each finding includes
symptom, evidence, impact, and fix. The Prioritized Remediation Plan in §5
ranks findings by cost-to-value ratio.

---

**Document ID:** TF-2026-07-18-001
**Repository:** `mihakralj/vyos-ls1046a-build`
**Branch:** `dpaa1`
**Commit:** doc origin `73e39e9` → current `dpaa1` HEAD `159752e` (2026-07-18). **The P1–P3 closure series `4493ce8`→`9970745` was reset OUT of the branch after a CI cascade — see §1.2.**
**Scope:** All stubbed, incomplete, or type-drifted functions across `kernel/flavors/ask/oot-modules/ask/` and `kernel/common/patches/board/`
**Reference specs:** `arch/fman-pcd-api-reference.md` v3.0.0, `specs/ask2-rewrite-spec.md`, `arch/fman-microcode-210-programming-reference.md`
**Methodology:** Static analysis of source and patch text, cross-reference against v3.0.0 API contract, symbol-presence grep across `EXPORT_SYMBOL_GPL` surface.
**Progress:** **0/23 resolved on `dpaa1` HEAD `159752e`.** The full P1–P3 closure was implemented on 2026-07-18 (commits `4493ce8`→`9970745`) but every CI build failed and the series was reset off the branch (§1.2); re-land in progress behind `bin/test-fixups.sh`. Progress: ░░░░░░ 0.0%

---

## 1. Executive Summary

**[SPEC]** Twenty-three findings across six categories. Nine are hard blockers for shipped features (IPsec offload, VyOS op-mode integration, `NETIF_F_HW_ESP` advertisement). Eight are spec/code drift where the v3.0.0 API mandates a signature or symbol the tree does not carry. Six are intentional deferrals correctly marked, listed for auditor visibility.

**[BUG] F-01** `ask_xfrm_state_add` returns success without installing an SA, which becomes silent packet loss the moment `NETIF_F_HW_ESP` is advertised.

### 1.1 Severity Definitions

| Severity | Definition |
|---|---|
| **CRITICAL** | Correctness fault that produces silent packet loss or data corruption when the surrounding feature is enabled. Ship blocker. |
| **HIGH** | Missing implementation of a documented API contract. Feature is unusable but fails safely. |
| **MEDIUM** | Signature drift or missing observability. Feature works but violates a spec rule (T1/T2/T8/T9/T10) or hides state. |
| **LOW** | Documented deferral. Listed for tracking, no action required this cycle. |

### 1.2 Current Branch State — P1–P3 closure reset out (2026-07-18)

**[BUG] The entire P1–P3 remediation series was implemented, CI-failed, and reset off `dpaa1`.** On 2026-07-18 the Priority-1/2/3 fixes (§5) were committed as `4493ce8` (F-08 `fman_pcd_fe_verify`) → `ec61aa2` (P1: F-08/F-09/F-10/F-11/F-12/F-15) → `cb2ebbd` (P2: F-093/F-13/F-16/F-17/F-18/F-19 + type hygiene) → `28ed22d` (P3: F-01/F-02/F-07/F-20/F-21/F-22/F-23) → `9970745` (F-13 `sh`-init CI fix). Every CI run of the series failed in a cascade of escape-sequence bugs inside the `bin/ci-setup-kernel.sh` REPLACEMENT / base64-Python / heredoc fixup blocks that inject the C changes into the kernel tree: F-11 and F-12 Python `SyntaxError` (raw `\n`/`\t` in triple-quoted strings), F-088 forward-declaration + heredoc-marker collisions, a bash `syntax error` at line 449 (`\n` in a comment), the F093PY `\n`-inside-triple-quote fault, and finally `NameError: name 'sh' is not defined` in the F-13 edit (`sh` used before assignment). The nearest-green kernel still tripped the OOT-module builder guard `FATAL: …/linux-6.18.38/certs/signing_key.pem missing — MODULE_SIG_KEYS broken?` because `kernel/flavors/ask/oot-modules/ask/ci-build.sh:67` only switches to the headers-snapshot tree when `Module.symvers` is absent, not when only `certs/signing_key.pem` was wiped. The branch was reset to `73e39e9` (this doc's origin) to recover; `159752e` then added `bin/test-fixups.sh`, which decodes the REPLACEMENT block and every base64 Python blob and runs `bash -n` + `compile()` on them BEFORE any push — the missing pre-flight gate whose absence produced the cascade.

**[NOTE]** The orphaned closure commits (`4493ce8`, `ec61aa2`, `cb2ebbd`, `28ed22d`, `9970745`) are NOT in the branch but remain reachable via `git reflog` / `git cherry-pick` (not yet garbage-collected). The design and code for every P1–P3 finding are therefore recoverable — the reset unwound the *landing*, not the *work*. Re-land discipline: run `bin/test-fixups.sh` and confirm it passes before every push; land the OOT-builder snapshot-fallback broadening (switch to the snapshot whenever the source tree is missing ANY of `Module.symvers` / `scripts/sign-file` / `certs/signing_key.pem`, not only `Module.symvers`) in the same series so the `signing_key.pem` FATAL cannot recur.

**[SPEC]** Because the series was reset out, the authoritative status of every finding on `dpaa1` HEAD `159752e` is **open** — including F-08, which §2 previously marked resolved via `4493ce8`. Treat every row in §2 as fully open until the re-land is confirmed green in CI and merged into `dpaa1`. Section-level statuses in §3 that read "RESOLVED" describe the orphaned implementation, not the current branch.

---

## 2. Findings Summary Table

| ID | Sev | Title | Location | Status |
|---|---|---|---|---|
| F-01 | CRIT | `ask_xfrm_state_add` returns 0 without installing SA | `ask_xfrm.c:12` | [ ] |
| F-02 | HIGH | `ask_caam.c` is 21-line init/exit stub | `ask_caam.c` | [ ] |
| F-03 | HIGH | `ask_neigh.c` is 21-line init/exit stub | `ask_neigh.c` | [ ] |
| F-04 | HIGH | `ask_op.c` is 21-line init/exit stub | `ask_op.c` | [ ] |
| F-05 | HIGH | `ask_stats.c` is 21-line init/exit stub | `ask_stats.c` | [ ] |
| F-06 | HIGH | `ask_bridge.c` is 21-line init/exit stub | `ask_bridge.c` | [ ] |
| F-07 | HIGH | `xdo_dev_state_delete` callback does not exist | `ask_xfrm.c` | [ ] |
| F-08 | HIGH | `fman_pcd_fe_verify` absent from tree | spec §10.1, §17 | [ ] (impl `4493ce8`, reset out — §1.2) |
| F-09 | HIGH | `dpaa_get_rx_default_fqid` absent from tree | spec §14.2 | [ ] |
| F-10 | HIGH | `dpaa_get_rx_pcd_fqid_range` absent from tree | spec §14.2 | [ ] |
| F-11 | MED | `fman_pcd_fe_flow_add` uses `unsigned long enq_off` | `0153-fman-pcd-fe-engage-api.patch:72` | [ ] |
| F-12 | MED | `fman_pcd_fe_context_build` writes DDR through `void __iomem *` | `0135-fman-pcd-fe-context-build.patch:44` | [ ] |
| F-13 | MED | `fman_pcd_kg_unbind_port` missing `port_id` parameter | `0151-fman-pcd-kg-unbind-detach.patch:20` | [ ] |
| F-14 | MED | `fman_port_lookup_rx` typed `void *fm` | `fman_port.c` per spec §12 | [ ] |
| F-15 | MED | Hardcoded `tx_fqid = 0x200` in FE-VM compose path | `0158-fman-pcd-fqid-resolution-compose.patch:69` | [ ] |
| F-16 | MED | `fman_pcd_kg_scheme_counter_read` absent | spec §6 | [ ] |
| F-17 | MED | `fman_cc_key_stats_get` absent | spec §7, audit row 5a | [ ] |
| F-18 | MED | `fman_policer_counters_get` absent | spec §9, audit row 8a | [ ] |
| F-19 | MED | `ASK_CMD_GET_MURAM` returns `-EOPNOTSUPP` | `ask_genl.c:112` | [ ] |
| F-20 | MED | `ASK_CMD_SET_POLICER` returns `-EOPNOTSUPP` | `ask_genl.c:144` | [ ] |
| F-21 | MED | `ASK_CMD_DUMP_SAS` returns `-EOPNOTSUPP` | `ask_genl.c:129` | [ ] |
| F-22 | MED | `ASK_CMD_FLUSH_SAS` returns `-EOPNOTSUPP` | `ask_genl.c:141` | [ ] |
| F-23 | LOW | `fman_pcd_fe_flow_stats_get` reserved, not declared | spec §10.1, audit row 9a | [ ] |

---

## 3. Detailed Findings

### F-01 (CRITICAL): `ask_xfrm_state_add` returns success without installing SA

**[BUG]**

**Location:** `kernel/flavors/ask/oot-modules/ask/ask_xfrm.c:12-25`

**Symptom:** For any SA that is not `rfc4106(gcm(aes))`, the function returns `0`, which the mainline xfrm stack interprets as "SA installed in hardware."

**Evidence:**

```c
int ask_xfrm_state_add(struct xfrm_state *x)
{
    if (x->aead && !strcmp(x->aead->alg_name, "rfc4106(gcm(aes))"))
        return -EOPNOTSUPP;
    return 0; /* Stub implementation for other algos */
}
```

No CAAM Job Ring descriptor is built, no PDB is written, no SPI is programmed into a FMan CC entry, no CAAM RX/TX FQ is bound.

**Impact:** The only reason production traffic is not being silently dropped today is that `NETIF_F_HW_ESP` is not advertised in `dpaa_eth`'s `hw_features`. The moment any patch flips that capability bit while this function still returns `0`, every ESP packet on any AES-CBC-SHA256 SA is marked `XFRM_HW_OFFLOAD` by the stack, handed to the driver, and dropped. No error, no counter movement, no dmesg line.

**Fix:** Land the real body simultaneously with the `NETIF_F_HW_ESP` advertise. The real body must (1) allocate a Shared Descriptor in DDR-coherent memory, (2) write the PDB from `struct xfrm_state`, (3) allocate CAAM RX/TX FQs via QMan portal allocator, (4) call `caam_qi_ext_consumer_register` (implementation already present in `0134`, currently dormant), (5) insert an FMan CC entry keyed on the SPI whose action is enqueue-to-CAAM-RX-FQ.

**Related:** F-02, F-07, F-21, F-22.

---

### F-02 (HIGH): `ask_caam.c` is 21-line init/exit stub

**[SPEC]**

**Location:** `kernel/flavors/ask/oot-modules/ask/ask_caam.c`

**Symptom:** Both `ask_caam_init()` and `ask_caam_exit()` are `pr_debug("caam: {init,exit} (stub)")` and return `0`.

**Impact:** The CAAM QI plumbing at `0134-caam-qi-share.patch` implements `caam_qi_ext_consumer_register` and `_release` with 82 lines of real FQ-swap-and-drain, but nothing binds a `struct caam_drv_ctx` from `ask.ko` to a shared descriptor. IPsec offload cannot dispatch.

**Fix:** Land in the same series as F-01. Allocate one `caam_drv_ctx` per SA lifecycle, hold it in a `struct ask_sa` alongside the FMan CC handle, tear both down in `xdo_dev_state_delete`.

**Related:** F-01, F-07.

---

### F-03 (HIGH): `ask_neigh.c` is 21-line init/exit stub

**[SPEC]**

**Location:** `kernel/flavors/ask/oot-modules/ask/ask_neigh.c`

**Symptom:** Init/exit prints only.

**Impact:** The HM nexthop dedup path at `0120-fman-pcd-hm-nexthop-dedup.patch` caches `src_mac` and `dst_mac` in the HMCT chain. Neigh state transitions (`NUD_STALE` → `NUD_REACHABLE`, `NUD_FAILED`, entry replacement) do not propagate to the HMCT chain, so a flow can silicon-forward with a stale L2 header after ARP churn. In lab conditions with static ARP this never triggers; in production it produces intermittent black-holing that resolves on flow re-installation.

**Fix:** Register a `struct notifier_block` on `NETEVENT_NEIGH_UPDATE`, walk the `fman_hm_nexthop_cache` (already present in `0120`), rebuild affected HMCT entries or mark them for lazy rebuild.

---

### F-04 (HIGH): `ask_op.c` is 21-line init/exit stub

**[SPEC]**

**Location:** `kernel/flavors/ask/oot-modules/ask/ask_op.c`

**Symptom:** Init/exit prints only. No genl handler wires here; the VyOS op-mode CLI has no kernel-side receiver except the debugfs escape hatch.

**Impact:** VyOS `set system offload ask` cannot engage. The only path to `ask_hw_offload_engage` is `echo "engage 0x10" > /sys/kernel/debug/ask/offload`, which is bring-up ergonomics, not shipped op-mode.

**Fix:** Land the op-mode netlink attribute set and validator. Compose against existing `ask_hw_offload_engage`/`_disengage` (already real in `ask_hw.c`).

---

### F-05 (HIGH): `ask_stats.c` is 21-line init/exit stub

**[SPEC]**

**Location:** `kernel/flavors/ask/oot-modules/ask/ask_stats.c`

**Symptom:** No stats collection.

**Impact:** `tc -s` on any offloaded flow reports zeros for silicon-forwarded packets, because the software counter does not increment on the HIT path (which never touches `dpaa_eth_napi_poll`), and no reader reads the FMan CC per-key counters silicon is already computing. Also blocks M4 acceptance measurement.

**Fix:** Wire a 1 Hz workqueue that reads per-CC-key counters via F-17 and per-flow-context counters via `fman_pcd_fe_flow_stats_get` (F-23, when it lands), applies them via `ask_flow_update_stats`.

---

### F-06 (HIGH): `ask_bridge.c` is 21-line init/exit stub

**[SPEC]**

**Location:** `kernel/flavors/ask/oot-modules/ask/ask_bridge.c`

**Symptom:** Init/exit prints only. Design work not started.

**Impact:** Blocks M3 hardware bridge offload gate. No kernel-side registration of `switchdev` ops.

**Fix:** M3 milestone work per `specs/ask2-rewrite-spec.md`. Not blocking M2 or M4.

---

### F-07 (HIGH): `xdo_dev_state_delete` callback does not exist

**[SPEC]**

**Location:** `kernel/flavors/ask/oot-modules/ask/ask_xfrm.c`

**Symptom:** No `ask_xfrm_state_delete` function declared or defined. `xdo_dev_state_delete` in the future `struct xfrmdev_ops` will point at NULL.

**Impact:** Even if F-01 gets a real body, SA teardown will leak DDR-coherent memory, CAAM descriptors, and QMan FQs at rekey and expiry rate.

**Fix:** Land alongside F-01. Free every resource F-01 allocates, in reverse order, with proper RCU synchronize before the QMan FQ retire (in-flight CAAM responses must drain).

---

### F-08 (HIGH — [ ] OPEN, impl reset out): `fman_pcd_fe_verify` implemented then orphaned

**[SPEC]** Status on `dpaa1` HEAD `159752e`: **OPEN.** The implementation described below landed in commit `4493ce8` but was reset off the branch (§1.2); `grep fman_pcd_fe_verify bin/ci-setup-kernel.sh` returns zero hits in the current tree. The account below documents the orphaned implementation for re-land.

**[SPEC]**

**[SPEC]**

**Location:** `bin/ci-setup-kernel.sh` F-088 fixup (heredoc Python3, injects into `drivers/net/ethernet/freescale/fman/fman_pcd.c`). Commit `4493ce8` (2026-07-18).

**Resolved:** The function, engage-path call, debugfs node, and fops are all injected by the F-088 pre-build fixup. Five injection steps: (1) `fman_pcd_fe_verify_internal()` body before `fman_pcd_init`, (2) C2 readback gate call in `__fman_pcd_fe_arm_engage` before the KG arm, (3) `fe_verify` debugfs write node, (4) forward declaration + write handler, (5) `file_operations` struct. Walks seven descriptor types against the §17.1–§17.7 tables. Returns `0` (all verified) or `-EPROTO` with dmesg naming offset/expected/actual and the §17 table row. Use: `echo 10 > /sys/kernel/debug/fman_pcd/0/fe_verify` for manual validation; engage calls it automatically before arming the port.

**Impact:** Rule C4 (arm-time invariant sweep) is spec text only. The last three board sessions (2026-07-14, 2026-07-15, 2026-07-17) were spent varying parameters downstream of one invalid descriptor word that a 20-line readback would have flagged in milliseconds. F-072 through F-079 in the defect register are all conditions this function detects statically.

**Fix:** Land as a self-contained function that walks group AD, miss-AD, EXT_HASH words, each ENQ FE, EXIT singleton, FE_ENTER, and params page `+0x54`/`+0x58` against `§17.1` through `§17.6` tables. Refuse `fman_pcd_fe_engage` with `-EPROTO` on any mismatch. Expose as debugfs `fe_verify` for board sessions.

**[NOTE]** Highest return on investment in this document. One board session saved pays for it in engineer-hours.

---

### F-09 (HIGH): `dpaa_get_rx_default_fqid` absent from tree

**[SPEC]**

**Location:** Specified in `arch/fman-pcd-api-reference.md` v3.0.0 §14.2. Zero hits across `kernel/common/patches/board/*.patch`.

**Impact:** Rule T8 (FQID direction validated at API boundary) is spec text only. Currently the RX default FQID is scraped by shell from `/sys/class/net/*/fqids` at engage time, or, in `0158`, read from params page `+0x0C` via an inline helper local to `fman_pcd.c`. Both are workarounds; neither is available to other builders (F-15).

**Fix:** Promote `fman_pcd_resolve_miss_fqid` (already in `0158:19-47`) to a `dpaa_get_rx_default_fqid(net_dev)` export, retype return to `fman_fqid_t` with direction tag `FMAN_FQ_DIR_RX_KERNEL`.

---

### F-10 (HIGH): `dpaa_get_rx_pcd_fqid_range` absent from tree

**[SPEC]**

**Location:** Specified in `arch/fman-pcd-api-reference.md` v3.0.0 §14.2. Zero hits.

**Impact:** The kernel-polled PCD FQ range (eth4: 768-895 per the fqids listing) is the correct target for AC_CC HIT-to-kernel dispatch. Today it is unreferenced in code. Any patch that needs to spread ingress-classified traffic across the PCD range has no accessor.

**Fix:** Add alongside F-09, walking the netdev priv's `channel[]` array for the PCD FQ range base and count.

---

### F-11 (MEDIUM): `fman_pcd_fe_flow_add` uses `unsigned long enq_off`

**[SPEC]**

**Location:** `kernel/common/patches/board/0153-fman-pcd-fe-engage-api.patch:72`

**Evidence:**

```c
int fman_pcd_fe_flow_add(struct fman *fm, u8 hw_port_id,
                         const u8 *key, u8 key_size, unsigned long enq_off)
```

**Impact:** Spec §10.1 mandates `const struct fman_pcd_fe_flow_action *action`. The current signature carries a MURAM offset per flow, which the F-057 closure removed from the DDR record layout. Callers cannot express egress FQ direction (rule T8), HM handle, policer profile, or (future) CAAM SA index. Blocks IPsec offload from riding the flow-add path.

**Fix:** Retype to `const struct fman_pcd_fe_flow_action *action`. Materialize the action as the flow's result/context pair per §17.5. Two callers to update.

---

### F-12 (MEDIUM): `fman_pcd_fe_context_build` writes DDR through `void __iomem *`

**[SPEC]**

**Location:** `kernel/common/patches/board/0135-fman-pcd-fe-context-build.patch:44`

**Evidence:**

```c
int fman_pcd_fe_context_build(void __iomem *ctx, u16 offset, ...)
```

**Impact:** Rule T10 violation. The FE workspace context lives in DDR (hardware DMA-loads it into the frame workspace), not iomem. Writing DDR through `void __iomem *` with `iowrite32be` is the root of the descriptor-as-context defect family (F-058 in the defect register). Sparse cannot catch the type mismatch because `void __iomem *` accepts anything.

**Fix:** Retype to `struct fman_ddr_region *ctx`. Body uses `cpu_to_be32` CPU stores at `ctx->cpu + offset`, not iowrite. One caller path (`0146-fman-pcd-fe-context-build-integration.patch`) to update in the same series.

---

### F-13 (MEDIUM): `fman_pcd_kg_unbind_port` missing `port_id` parameter

**[SPEC]**

**Location:** `kernel/common/patches/board/0151-fman-pcd-kg-unbind-detach.patch:20`

**Evidence:**

```c
int fman_pcd_kg_unbind_port(struct fman_pcd_kg_scheme *scheme)
/* spec: fman_pcd_kg_unbind_port(struct fman_pcd_kg_scheme *scheme, u8 port_id) */
```

**Impact:** A scheme can be bound to multiple ports (lowest-scheme-id-wins arbitration per RM 8.7.4). The current signature unbinds all of them, which is not the inverse of `bind_port(scheme, port_id)`. C1 rule violated (inverse must match the forward).

**Fix:** Add `u8 port_id` parameter, RMW `fmkg_pe_sp[hwport]` to clear only that port's bit.

---

### F-14 (MEDIUM): `fman_port_lookup_rx` typed `void *fm`

**[SPEC]**

**Location:** `drivers/net/ethernet/freescale/fman/fman_port.c` per spec §12 as-built note

**Impact:** Rule T1 violation. `void *` in a typed API blocks sparse from catching wrong-type arguments.

**Fix:** Retype to `struct fman *fm`. One-line change, plus forward-declaration in the header.

---

### F-15 (MEDIUM): Hardcoded `tx_fqid = 0x200` in FE-VM compose path

**[SPEC]**

**Location:** `kernel/common/patches/board/0158-fman-pcd-fqid-resolution-compose.patch:69`

**Evidence:**

```c
const u32 tx_fqid       = 0x200;  /* TODO: dedicated offload TX FQ */
```

**Impact:** Rule T8 violation. The value `0x200` is eth3-era and does not resolve to a valid TX-side FQ on eth4. Any FE-VM flow that reaches this compose path egresses to a stale FQ, silently blackholes.

**Fix:** Consume `dpaa_get_tx_fqid` (present per spec §14.2) or a dedicated offload TX FQ once F-09 lands. Same TODO comment references the fix.

---

### F-16 (MEDIUM): `fman_pcd_kg_scheme_counter_read` absent

**[SPEC]**

**Location:** Specified in `arch/fman-pcd-api-reference.md` v3.0.0 §6. Zero hits.

**Impact:** No live-traffic diagnostic for "is this scheme actually receiving frames." Consumes audit row 3a; the counter (`KGSE_SPC`) is already maintained by silicon.

**Fix:** Read via the FMKG_AR indirect protocol (`arch/fman-microcode-210-programming-reference.md` §4.6, §4.1). Roughly 30 lines.

---

### F-17 (MEDIUM): `fman_cc_key_stats_get` absent

**[SPEC]**

**Location:** Specified in `arch/fman-pcd-api-reference.md` v3.0.0 §7. Zero hits.

**Impact:** Consumes audit row 5a. Without it, conntrack byte/packet accounting re-counts what silicon has already counted, wasting CPU on the hot path.

**Fix:** Statistics-AD encoding must be transcribed from the DPAA RM into spec §17 in the same series. Includes a C2 readback verify.

---

### F-18 (MEDIUM): `fman_policer_counters_get` absent

**[SPEC]**

**Location:** Specified in `arch/fman-pcd-api-reference.md` v3.0.0 §9. Zero hits.

**Impact:** Consumes audit row 8a. `tc -s` on a working meter reports zeros for green/yellow/red frames because nothing reads the FMPL profile counters.

**Fix:** Read via `FMPL_PAR` indirect protocol. Refuse with `-ENODATA` if `FMPL_GCR.STEN` is somehow clear.

---

### F-19 (MEDIUM): `ASK_CMD_GET_MURAM` returns `-EOPNOTSUPP`

**[SPEC]**

**Location:** `kernel/flavors/ask/oot-modules/ask/ask_genl.c:108-112`

**Symptom:** Wired to `ask_genl_eopnotsupp_doit` in the `small_ops` table.

**Impact:** Userspace cannot query MURAM budget. Prerequisite for M4 acceptance measurement, R4 leak detection from operator side.

**Fix:** Call `fman_pcd_get_muram_budget` and serialize into netlink attributes. Approximately 30 lines.

---

### F-20 (MEDIUM): `ASK_CMD_SET_POLICER` returns `-EOPNOTSUPP`

**[SPEC]**

**Location:** `kernel/flavors/ask/oot-modules/ask/ask_genl.c:141-148`

**Impact:** Policer is reachable only via `tc` matchall path today. No genl route for the ASK-native operator flow.

**Fix:** Parse policy attributes, call `fman_policer_install`. Approximately 40 lines.

---

### F-21 (MEDIUM): `ASK_CMD_DUMP_SAS` returns `-EOPNOTSUPP`

**[SPEC]**

**Location:** `kernel/flavors/ask/oot-modules/ask/ask_genl.c:126-129`

**Impact:** Blocked by F-01. Cannot dump SAs that do not exist.

**Fix:** Land after F-01. Walks an `ask_sa` list, serializes each into netlink.

---

### F-22 (MEDIUM): `ASK_CMD_FLUSH_SAS` returns `-EOPNOTSUPP`

**[SPEC]**

**Location:** `kernel/flavors/ask/oot-modules/ask/ask_genl.c:138-141`

**Impact:** Blocked by F-01.

**Fix:** Land after F-01 and F-07. Iterate the `ask_sa` list, call the SA delete path for each.

---

### F-23 (LOW): `fman_pcd_fe_flow_stats_get` reserved, not declared

**[NOTE]**

**Location:** Specified in `arch/fman-pcd-api-reference.md` v3.0.0 §10.1 with `[?]` marker. Zero hits.

**Impact:** Reserved for M4 monitoring milestone. Signature is fixed in spec so the conntrack-sync consumer can be written against it. No blocker today because M4 has not started.

**Fix:** Land the header declaration returning `-ENOSYS` in the M4 base patch so callers can compile against it. Real body ships with the monitor-buffer DDR allocation extension to flow-add/del.

---

## 4. Intentional No-Ops Correctly Marked (Auditor Note)

**[NOTE]** For completeness so future review does not flag these:

- `FLOW_ACTION_MANGLE` treated as no-op in `ask_flow_offload.c:907`. Header rewrite fidelity deferred; removing breaks REDIRECT chaining.
- `FLOW_ACTION_ADD` treated as no-op in `ask_flow_offload.c:908`. Same rationale.
- `FLOW_ACTION_TUNNEL_ENCAP` / `_DECAP` treated as no-ops in `ask_flow_offload.c:902-905`. Prevents future kernel from regressing to silent SW fallback.
- The 15 sites in `ask_flow_offload.c` returning `-EOPNOTSUPP` for IPv6 keys, missing 5-tuple ports, wrong `binder_type`, unresolved neigh, and QMan queue-full backpressure. These are the API boundary, not stubs.
- `caam_qi_ext_consumer_register` at `0134` has a real 82-line body. Dormant only because no caller exists (F-02). Do not confuse "dormant" with "stubbed."
- `fman_cc_*` / `fman_hm_*` / `fman_policer_*` families in `dpaa_fman_caps.c`. Historical `0086` / `0090` / `0091` patches installed `-ENOTSUPP` stubs; productive bodies landed in `0086b` / `0090a` / `0091a` plus `0098` / `0099` / `0100`. Patch filenames still contain "stub"; the code does not.

---

## 5. Prioritized Remediation Plan

**[SPEC]** Ranked by cost-to-value ratio and dependency ordering.

### Priority 1 — Before next board session

| Step | Finding | What | LOC ~ |
|---|---|---|---|
| 1 | F-08 | `fman_pcd_fe_verify` — highest ROI, one board session saved pays for it | ~150 |
| 2 | F-09+F-10+F-15 | `dpaa_get_rx_default_fqid` + `_pcd_fqid_range` + remove `tx_fqid=0x200` | ~120 |
| 3 | F-11 | `fman_pcd_fe_flow_add` retype to `const struct fman_pcd_fe_flow_action *` | ~60 |
| 4 | F-12 | `fman_pcd_fe_context_build` retype to `struct fman_ddr_region *` | ~40 |

### Priority 2 — Before M2 GA

| Step | Finding | What | LOC ~ |
|---|---|---|---|
| 5 | F-05 | `ask_stats.c` real body. Prerequisite for M4 acceptance measurement | ~80 |
| 6 | F-19 | `ASK_CMD_GET_MURAM`. Cheap observability win | ~30 |
| 7 | F-16, F-17, F-18 | Counter readers. Consume free silicon-side counters | ~90 |
| 8 | F-13 | `fman_pcd_kg_unbind_port` add `port_id` | ~20 |
| 9 | F-14 | `fman_port_lookup_rx` retype | ~5 |

### Priority 3 — M4 IPsec landing series (must ship together)

| Step | Finding | What |
|---|---|---|
| 10 | F-01 | `ask_xfrm_state_add` real body |
| 11 | F-07 | `ask_xfrm_state_delete` new callback |
| 12 | F-02 | `ask_caam.c` real body wiring to `caam_qi_ext_consumer_register` |
| 13 | F-23 | `fman_pcd_fe_flow_stats_get` declaration with `-ENOSYS` |
| 14 | F-21 | `ASK_CMD_DUMP_SAS` real body |
| 15 | F-22 | `ASK_CMD_FLUSH_SAS` real body |
| 16 | F-20 | `ASK_CMD_SET_POLICER` real body |
| 17 | — | `NETIF_F_HW_ESP` advertise (last, gated on all of the above) |

### Priority 4 — M3 milestone

| Step | Finding | What |
|---|---|---|
| 18 | F-03 | `ask_neigh.c` real body |
| 19 | F-04 | `ask_op.c` real body |
| 20 | F-06 | `ask_bridge.c` real body |

---

## 6. Sign-Off

**[NOTE]** Findings F-08, F-09, F-10, F-15, F-11, and F-12 close three of the last five silicon incidents at compile or arm time and cost fewer than 400 lines total. Recommend landing as a single series before further FE-VM board testing.

**[SPEC]** The `arch/fman-pcd-api-reference.md` v3.0.0, `plans/ASK2-JOURNEY-REVIEW-2026-07-18.md`, and the `plans/DUAL-DATAPLANE.md` v1.2 milestone tracker should be cross-referenced against this inventory to ensure no finding contradicts a released gate claim.
