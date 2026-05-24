# ASK2 Course-Correction Plan

**Date:** 2026-05-24
**Branch:** `ask20`
**Status:** Active execution plan
**Driver doc:** `plans/ASK2-MODERN-ARCHITECTURE-REVIEW.md` (2026-05-24)
**Companion doc:** `plans/ASK-VS-ASK2-COMPARATIVE-REVIEW.md` (Path A approved 2026-05-23)
**Target spec revision:** `specs/ask2-rewrite-spec.md` v1.2 → **v1.3**

---

## 0. Why this plan exists

The architecture review (`ASK2-MODERN-ARCHITECTURE-REVIEW.md`) concludes that once we adopt **Path A — boot-time PCD installation via a pre-`register_netdev()` hook** (already half-landed as patches `0044-fman-pcd-pre-netdev-hook.patch`, `0049-ask-fs_initcall.patch`, `0050-fman-pcd-cc-wire-group-table-and-miss-ad.patch`, `0051-fman-keygen-revert-pr14z15-nia-rmw.patch`, `0053-dpaa-noconfirm-offload-tx-fq.patch`), **roughly half of the v1.2 spec is dead weight**. The current ask20 tree carries:

- 28+ in-tree kernel patches (`0026` through `0053`) — many of which were authored to make the **graft model** work and are now superfluous under Path A.
- A 16-file `oot-modules/ask/` source tree including `ask_hostcmd.c` (the §12 wire-format opcode encoders) which is **dead code** — no consumer, no future microcode, retained "in case".
- An OH-port subsystem (`fman_pcd_oh.c` + DT bindings + MANIP-via-OH-AD-chain encoders, patches 0032–0038) carrying ~2200 LOC that exists **only** because the graft model couldn't safely mutate the RX-port BMI to attach an inline MANIP chain to a CC key's action.
- An `askd` daemon, `ask-cli` Python tool, and `libask_fci.so.1` ABI shim referenced in `AGENTS.md` but **not yet implemented** — and per the review, **should never be implemented**.

The result is a **~25 000 LOC architecture target** that the review proposes to shrink to **~12 700 LOC (49% reduction)** with **equal performance gates** and **strictly better recoverability** (no graft → no wedge).

This document is the concrete, ordered execution plan to land that reduction.

---

## 1. Guiding principle

> **ASK2 owns the FMan PCD chain from boot. It never grafts onto live silicon. It never restores state. `dpaa_eth` co-exists by being downstream of the PCD chain, not upstream of it. Operator UX is delivered through existing mainline Linux tools (`nft`, `ip xfrm`, `ynl`, `node_exporter`) — not through a vendor daemon.**

Every step below is a consequence of taking that sentence seriously.

---

## 2. Phased execution

Five phases. Each phase is independently testable. Phases 1–2 are **documentation + bookkeeping** (low risk, high clarity gain). Phases 3–5 are **code deletion + structural changes** (higher risk, must be guarded by patch-health and on-silicon M2 measurement).

### Phase 1 — Spec & AGENTS.md reconciliation (≤ 1 day, no code changes)

Goal: make the documentation consistent with where Path A actually lands us, so subsequent code deletions are uncontroversial.

- [ ] **1.1** Bump `specs/ask2-rewrite-spec.md` v1.2 → **v1.3**. Rewrite the v1.3 status block to summarize: *graft model abandoned, OH-port subsystem scoped out of v1.0 L3-forward path, §12 wire-format layer deleted, `askd`/`ask-cli`/`libask_fci.so.1`/`ask-load` removed from scope.*
- [ ] **1.2** Amend spec **§3.2** as the review directs:
  - Replace *"The kernel netdev retains full ownership of the RX path; ASK2 attaches a CC tree downstream of the mainline-allocated KG scheme."*
  - With *"ASK2 owns the FMan PCD chain (KG schemes 3+4, CC trees, MANIP chains) from boot. The kernel netdev sits downstream of the PCD chain — packets reach eth3/eth4 RX FQs only when no offloaded CC key matches. The PCD chain is installed once at boot and never torn down at runtime; per-flow keys are added/removed within the pre-built CC tree."*
- [ ] **1.3** Delete spec **§12** in full (the host-command opcode protocol chapter). Preserve a ~1-page *§2.x "FMan 210 microcode hardware note"* that captures only the surviving facts: package version gate, `OP_GET_UCODE_VERSION` lives in the QEF blob magic at SPI mtd3, no opcode-dispatch microcode exists or is planned.
- [ ] **1.4** Delete spec **§3.4** ("The 210 host-command interface (in kernel)").
- [ ] **1.5** Update spec **§13.2** module decomposition:
  - Remove `fman_pcd_oh.c` (~800 LOC) from the v1.0 budget. Note it stays as **deferred to v1.1 for IPsec re-inject only**.
  - Reduce `fman_pcd_manip.c` from 1600 → 1200 LOC (drop the OH-AD-chain encoder path; keep the three new MANIP tags but invoke them inline from CC-key action atoms).
  - Add new module `fman_pcd_cc.c` action type `FORWARD_FQ_WITH_MANIP` (~150 LOC).
- [ ] **1.6** Update spec **§15.1 LOC table** to v1.3 numbers from the review (`ask.ko` 3700→2800, patch 0004 10 000→5 500, askd 4000→0, ask-cli 800→0, tests 2700→1600, docs 1500→1000, **new** patch 0005 0→150).
- [ ] **1.7** Update spec **§19 "What we don't do"** to list, *explicitly*: no `/dev/cdx_ctrl` (incl. no symlink), no `libfci.so.1` ABI, no `/etc/cdx_*.xml`, no `/etc/config/fastforward` toggle, no `askd` daemon in v1.0, no `ask-cli` Python tool (use `ynl --family ask`), no `ask-load` early-init binary.
- [ ] **1.8** Update spec **§11.1** perf-gate narrative to reflect that the M2 row is now achievable via **inline MANIP on CC key action** (the silicon primitive RM §8.7.3.4 + SDK `e_FM_PCD_CC_KEY_FLAG_DO_MANIP_BEFORE_NE`), not via OH-port indirection.
- [ ] **1.9** Update **`AGENTS.md`** ASK2 component-LOC list to match the v1.3 numbers. Remove the `/dev/cdx_ctrl`, `libfci.so.1`, `/etc/cdx_*.xml`, `/etc/config/fastforward` "ABI compatibility surfaces" sentence — it directly contradicts spec §19 and is the single most misleading line in the file.
- [ ] **1.10** Update **`plans/ASK2-IMPLEMENTATION.md`** tracker:
  - Mark **PR14h, PR14i, PR14j, PR14k, PR14l, PR14n, PR14u, PR14x** rows as **deferred to v1.1 (OH-port re-inject for IPsec only)**.
  - Mark **PR14z13, PR14z14, PR14z15, PR14z18** as **archived (graft model abandoned, Path A supersedes)**.
  - Mark **PR14y, PR14z2, PR14z9, PR14z10, PR14z11** as **archived (deferred-insert / cookie-recovery / dual-ifindex bookkeeping all subsumed by Path A's at-boot install)**.
  - Add new rows **PR15 (Phase 3)**, **PR16 (Phase 4)**, **PR17 (Phase 5)** per this document.
- [ ] **1.11** Author a single commit `docs(ask2): v1.3 spec reconciliation — Path A + delete §12 + drop OH-port from v1.0` that touches only `specs/ask2-rewrite-spec.md`, `AGENTS.md`, `plans/ASK2-IMPLEMENTATION.md`, and adds this `plans/ASK2-COURSE-CORRECTION.md`. Do **not** touch any code in this commit.

Exit gate: `git log --stat -1` shows only doc files; `patch-health.sh` clean (no code touched); spec/AGENTS.md grep for "graft", "OH-port v1.0", "askd", "cdx_ctrl", "libfci.so.1" returns no stale references.

### Phase 2 — Patch-stack audit & archive (≤ 1 day, no behaviour change)

Goal: every patch in `kernel/flavors/ask/patches/` is classified as **keep / archive / supersede**, and the archive moves are landed *before* any code is deleted, so a bisect across the boundary is possible.

- [ ] **2.1** For each patch `0026` … `0053`, write its disposition into the patch's leading comment block AND into `kernel/flavors/ask/patches/README.md`:

  | Patch | Subject | Disposition (v1.3) |
  |---|---|---|
  | 0026 | fman-pcd-muram-budget-fix | **KEEP** — silicon fact, independent of model |
  | 0027 | fman-pcd-public-handle-helpers | **KEEP** — needed by ask.ko regardless |
  | 0028 | dpaa-export-rx-default-fqid | **KEEP** — needed for CC miss-action target |
  | 0029 | dpaa-eth-advertise-hw-tc | **KEEP** — flow_block_offload wiring |
  | 0030 | dpaa-export-fman-port-id | **KEEP** |
  | 0031 | dpaa-export-tx-fqid | **KEEP** — needed for FORWARD_FQ_WITH_MANIP target |
  | 0032 | fman-pcd-oh-port | **ARCHIVE** (v1.1 IPsec re-inject only) |
  | 0033 | fman-pcd-manip-v1.2-oh-port-primitives | **PARTIAL** — split into MANIP tags (keep) + OH-AD encoder (archive) |
  | 0034 | fman-pcd-oh-port-claim-lock-split | **ARCHIVE** |
  | 0035 | fman-pcd-cc-node-empty-default-capacity | **KEEP** — Path A also wants pre-built empty CC tree |
  | 0036 | fman-pcd-manip-chain | **ARCHIVE** — chained-MANIP-via-OH path superseded by inline CC-key MANIP |
  | 0037 | fman-pcd-manip-hmct-used-v12-encoders | **PARTIAL** — keep MANIP encoders; archive HMCT-on-OH wiring |
  | 0038 | fman-pcd-manip-chain-bytes-used-accessor | **ARCHIVE** |
  | 0039 | dpaa-export-rx-fman-port | **KEEP** |
  | 0040 | fman-port-id-use-bmi-hwport | **KEEP** |
  | 0041 | fman-pcd-kg-bind-port-widen-hwport-range | **KEEP** |
  | 0042 | fman-pcd-kg-graft-cc | **ARCHIVE** — graft API; Path A doesn't graft |
  | 0043 | fman-pcd-kg-graft-mode-nia | **ARCHIVE** (already reverted by 0051) |
  | 0044 | fman-pcd-pre-netdev-hook | **KEEP** — this *is* Path A |
  | 0045 | fman-pcd-debug-regdump | **KEEP** — inspection surface; non-functional |
  | 0046 | fman-pcd-cc-node-remove-key | **KEEP** — needed for flow_block_cb del |
  | 0047 | ask-in-tree-skeleton | **KEEP** — Path A wants ask built-in (initcall ordering) |
  | 0048 | ask-in-tree-source-migration | **KEEP** |
  | 0049 | ask-fs_initcall | **KEEP** — Path A ordering primitive |
  | 0050 | fman-pcd-cc-wire-group-table-and-miss-ad | **KEEP** — needed for empty CC tree miss action |
  | 0051 | fman-keygen-revert-pr14z15-nia-rmw | **KEEP** (revert of dead 0043) |
  | 0052 | uapi-ask-spdx-syscall-note | **KEEP** |
  | 0053 | dpaa-noconfirm-offload-tx-fq | **KEEP** — TX-conf fast-path elision needed for M2 perf |
- [ ] **2.2** Move all **ARCHIVE** patches to `kernel/flavors/ask/patches/archived/`. Update `bin/ci-setup-kernel.sh` `ASK_PATCH_COUNT` accordingly. Do **not** delete archived patches — they remain in tree for one release cycle as a bisect anchor.
- [ ] **2.3** Split the **PARTIAL** patches (0033, 0037) into two patches each: one with the keepable MANIP tag encoders, one with the OH-AD-specific wiring (the OH-AD half goes to `archived/`).
- [ ] **2.4** Run `bash scripts/patch-health.sh --source release --flavor ask` and confirm `Pass=N Fail=0` with the reduced patch set.
- [ ] **2.5** Single commit: `build(ask): archive OH-port + graft patches per v1.3 spec`.

Exit gate: archived patches do not apply to the source tree at build time; `patch-health.sh` green; reduced `ASK_PATCH_COUNT` reflected in CI script; ISO still builds (CI smoke run).

### Phase 3 — `ask.ko` shrink: delete §12 wire-format + dead encoders (~1 day code, 1 day verify)

Goal: delete the dead host-command opcode layer from `oot-modules/ask/` (or, equivalently, from the in-tree `drivers/net/.../ask/` after the 0047/0048 migration). This is **pure deletion** with no functional change.

- [ ] **3.1** Delete `ask_hostcmd.c` and `tests/ask_hostcmd_test.c`. Strip `#include "ask_hostcmd.h"` and any `ask_hw_ucode_send_*` / `fmd_host_cmd_*` calls from `ask_hw.c`, `ask_main.c`, `ask_flow.c`.
- [ ] **3.2** Re-implement the public surface used elsewhere in `ask.ko` (`ask_hw_flow_insert_v4_tcp`, `ask_hw_flow_remove`, `ask_hw_ucode_get_version`) so the function names and signatures stay, but the bodies call **directly** into `fman_pcd_cc_node_add_key()` / `fman_pcd_cc_node_remove_key()` (and read the QEF magic from the device tree for `_get_version`). No wire format ever encoded.
- [ ] **3.3** Update `Kbuild` to drop `ask_hostcmd.o`. Update the in-tree `Makefile` (if 0047 has landed) to match.
- [ ] **3.4** Verify with `nm ask.ko | grep -c hostcmd` returning 0 and `objdump -d ask.ko | grep -c fmd_host_cmd` returning 0.
- [ ] **3.5** Delete patch `0003-fman-host-command-api.patch` from the patch stack (it lives under `kernel/common/patches/` if it was promoted out of flavor scope; otherwise under `kernel/flavors/ask/patches/`). Move to `archived/`.
- [ ] **3.6** `patch-health.sh` green. CI ISO build green. On-silicon: `dmesg | grep ask` should show no regression in module init banner.
- [ ] **3.7** Commit: `ask(v1.3): delete §12 host-command opcode layer (dead code)`.

Exit gate: `ask.ko` loads, `ask_main.ko` init logs unchanged, no symbol named `*hostcmd*` or `*fmd_host_cmd*` survives, **and** the M2 perf measurement is unchanged from pre-Phase-3 (this phase is supposed to be a pure no-op behaviourally).

### Phase 4 — Inline `FORWARD_FQ_WITH_MANIP` action + Path A boot install (3–5 days code, 2 days hardware bring-up)

Goal: implement the **architectural win** of the review — replace the OH-port two-stage classify→re-inject pipeline with a **single CC-key action that carries an inline MANIP chain reference**. This is the real PR15.

- [ ] **4.1** ~~Add new `FORWARD_FQ_WITH_MANIP` action enum~~ — **OBSOLETED 2026-05-24**. Investigation of the landed patch stack found that **patch `0016-fman-pcd-cc-manipulate-arm.patch` already encodes RM §8.7.3.4 semantics into the existing `FMAN_PCD_ACTION_MANIPULATE` arm**: `cc_encode_ad()` writes `nia = RESULT_CF | NADEN`, `fqid = action.manipulate.next_fqid`, `res = manip->hmtd_off`. Silicon walker order AD → HMTD → HMCT → enqueue to `AD.fqid` IS exactly `FORWARD_FQ_WITH_MANIP`. No new enum needed; no patch `0005` to author. **Action for this step:** restore the three archived patches that supply the chain primitive + L2-rewrite MANIP encoders that the existing arm consumes:
  - **`archive-grafted-2026-05-24/0036-fman-pcd-manip-chain.patch`** — `fman_pcd_manip_chain_create([m1, m2, m3], N)` returns ONE manip handle whose HMCT is the memcpy concatenation of the N source HMCTs (HMCD_LAST cleared on intermediates, set on final). Restore as `kernel/flavors/ask/patches/0057-fman-pcd-manip-chain.patch` (next free slot after 0056). EXPORT_SYMBOL_GPL'd `_create` + `_destroy`. ~370 LOC, no OH-port references — restore as-is.
  - **`archive-grafted-2026-05-24/0033-fman-pcd-manip-v1.2-oh-port-primitives-RMV-INSRT-only.patch`** — split per Phase 2 §2.3: keep the `MANIP_RMV_ETHERNET` + `MANIP_INSRT_GENERIC` + `MANIP_FIELD_UPDATE_IPV4_FORWARD` enum extensions and their HMCT byte-encoders; drop any OH-port AD-chain wiring. New patch slot `0058-fman-pcd-manip-l2-rewrite-encoders.patch`.
  - **`archive-grafted-2026-05-24/0037-fman-pcd-manip-hmct-used-v12-encoders-RMV-INSRT-only.patch`** — same PARTIAL split: keep the HMCT bytes-used accounting for the three new encoders (required by `chain_create`'s memcpy arithmetic); drop OH-AD references. New slot `0059-fman-pcd-manip-hmct-bytes-used.patch`.
  - Net kernel-side LOC: ~600 added across three patches (all surgical restores from `archive-grafted-2026-05-24/`), no new enum, no new public-ABI surface beyond what was already audited at archive time.
- [ ] **4.2** Refactor `ask_flow_offload.c` REPLACE handler. Per-flow construction:
  ```c
  /* Build three short-lived MANIPs for this flow. */
  m_rmv  = fman_pcd_manip_create(pcd, &(struct fman_pcd_manip_params){
      .type = FMAN_PCD_MANIP_RMV_ETHERNET });
  m_insrt = fman_pcd_manip_create(pcd, &(struct fman_pcd_manip_params){
      .type = FMAN_PCD_MANIP_INSRT_GENERIC,
      .insrt_generic = { .offset = 0, .size = 14, .data = new_eth_hdr } });
  m_ipv4 = fman_pcd_manip_create(pcd, &(struct fman_pcd_manip_params){
      .type = FMAN_PCD_MANIP_FIELD_UPDATE_IPV4_FORWARD });
  /* Concatenate into one HMCT. */
  chain = fman_pcd_manip_chain_create(pcd,
      (struct fman_pcd_manip *[]){ m_rmv, m_insrt, m_ipv4 }, 3);
  /* Single action atom carries fqid + manip handle. */
  action = (struct fman_pcd_action){
      .type = FMAN_PCD_ACTION_MANIPULATE,
      .manipulate = { .manip = chain, .next_fqid = egress_tx_fqid } };
  hw_id = fman_pcd_cc_node_add_key(cc_v4_tcp, key, mask, &action);
  ```
  DESTROY handler calls `fman_pcd_cc_node_remove_key(cc_v4_tcp, hw_id)` then `fman_pcd_manip_chain_destroy(chain)` then per-source `fman_pcd_manip_destroy()` for `m_rmv`/`m_insrt`/`m_ipv4`. (`chain_create` memcpies bytes; source manips are independently destroyable per Qdrant memo on PR14x.)
- [ ] **4.3** Replace the in-`ask.ko` graft logic (`ask_hw_port_bind`, `ask_hw_pcd_build_chain`) with **`ask_pcd_install()`** that runs from the 0044 pre-`register_netdev` hook:
  - Claims KG schemes 3 + 4 (writes `KGSE_MODE.NIA = FM_CTL`, `KGSE_CCBS = group_table_idx`, `KGSE_EKFC = DEFAULT_HASH_KEY_EXTRACT_FIELDS`, `KGSE_FQB = base_fqid_for_miss`).
  - Creates empty `cc_v4_tcp_in` and `cc_v4_udp_in` CC trees with `miss_action = FORWARD_FQ(kernel_rx_default_fqid)` and `num_keys = 0`.
  - Returns `0` from the hook — `dpaa_eth_probe` proceeds to `register_netdev` with the PCD chain already live.
  - The hook fires **before** `dpaa_eth` writes `KGSE_MODE = BMI_DIRECT_ENQUEUE`, so there is no race.
- [ ] **4.4** Delete `ask_hw_port_bind`, `ask_hw_port_unbind`, `ask_hw_pcd_build_chain` from `ask_hw.c`. Net deletion ~600 LOC.
- [ ] **4.5** `ask_flow_offload.c` ADD/DELETE callbacks become near-trivial: build the action, call add_key / remove_key, store the returned cookie in the xarray, return. No graft, no ungraft, no deferred-insert queue, no cookie-recovery hack.
- [ ] **4.6** Delete `ask_neigh.c`'s deferred-resolve logic and queue — once Path A pre-builds the CC tree, neigh resolution happens **before** flow add (kernel's `nf_flow_offload_route` has already populated the next-hop MAC into the `flow_offload_tuple`). Net ~200 LOC saved.
- [ ] **4.7** Build, `patch-health.sh` green, CI ISO green.
- [ ] **4.8** **Hardware bring-up**:
  1. Flash ISO to mono DUT (192.168.1.190).
  2. `dmesg | grep -E 'ask:|fman_pcd:'` — verify `ask: pcd install: schemes 3+4 claimed, cc_v4_tcp_in/udp_in trees ready (0 keys), miss=FORWARD_FQ(kernel)` appears **before** any `dpaa_eth ... eth3` register banner.
  3. Idle measurement: `bin/ask-pcd-regdump.py --history 10` confirms KGSE_SPC counts on schemes 3/4 (silicon walking empty trees), no MURAM allocation churn.
  4. `bash bin/m2-dut-prep.sh && bash bin/verify-ask-flow-offload.sh`.
  5. **M2 perf gate target**: ≥ 2 Gbps throughput AND ≤ 5% kernel-net CPU at iperf3 -P 8 30s baseline. **Stretch target** (the real review claim): ≥ 7 Gbps + < 5% CPU.
- [ ] **4.9** If M2 gate fails: capture full regdump + dmesg + ethtool -S, file as a follow-up bug, **do not roll back Phase 4**. Path A is structurally correct regardless of M2 numbers; if the silicon primitive `FORWARD_FQ_WITH_MANIP` doesn't behave as RM §8.7.3.4 documents, fall back to OH-port indirection from `archived/` patches as a **v1.1 follow-up** (Risk #1 in the review).
- [ ] **4.10** Commit: `ask(v1.3): Path A boot-time PCD install + inline FORWARD_FQ_WITH_MANIP action`.

Exit gate: ISO boots, `ask.ko` installs PCD chain before `register_netdev`, M2 gate passes OR a single clear regression note is filed; the graft-related dmesg lines (`PR14z13 graft active`, `port 0xNN dir N → scheme_id=…`) are **gone**.

### Phase 5 — Delete `askd`/`ask-cli`/`libask_fci.so.1` budget; commit to `ynl` + `nft` (≤ 1 day, doc-only) ✅ landed 2026-05-24

Goal: lock in the deletion of the userspace daemon, Python CLI, and FCI compat library from the project's roadmap. These are **not** in the current tree, so this is doc-only — but the doc reconciliation is the gating step that prevents them being re-spawned in a future planning round.

- [x] **5.1** Spec §6 rewritten as a v1.3 "Removed" stub mapping each former askd/ask-cli responsibility to its mainline-tool replacement (nft, ynl, in-kernel timer, node_exporter, ask-vpp-promote oneshot deferred to v1.1). Landed in Phase 3 commit `91a44a2`.
- [x] **5.2** AGENTS.md ASK2 LOC budget line updated in Phase 1 commit `aef5a11` — `askd`/`ask-cli`/`ask-load`/`libask_fci.so.1` removed; "Until ASK2 components land" now reads `(ask.ko ~2800 LOC in-tree, plus patch 0004 ~5500 LOC across drivers/net/ethernet/freescale/fman/)`.
- [x] **5.3** `ask.yaml` shipped at `kernel/flavors/ask/uapi/ask.yaml` — full YNL schema (genetlink-legacy, 8 operations, 3 mcast groups, 7 attribute-sets, 2 typed definitions). When the ask.ko series upstreams, file lands at `Documentation/netlink/specs/ask.yaml`.
- [x] **5.4** Spec §3.6 "Operator UX (v1.3)" added — three-tool table (`nft`/`ynl`/`node_exporter`) with one worked example per tool, plus an explicit "no askd, no ask-cli, no libask_fci.so.1" footer. (Numbered §3.6 rather than §3.5 because §3.5 was already taken by the Path A probe sequence.)
- [x] **5.5** Commit: `docs(ask2): v1.3 Phase 5 — ship ask.yaml YNL schema + §3.6 Operator UX`.

Exit gate: `grep -rn 'askd\|ask-cli\|libask_fci\|libfci.so.1\|cdx_ctrl' specs/ AGENTS.md plans/ASK2-*.md` returns only historical-context mentions (clearly tagged as such) — no live "shall implement" sentences.

---

## 3. LOC budget — current state vs target

Numbers from the architecture review §4, reconciled against the actual `kernel/flavors/ask/` tree as it stands today (2026-05-24).

| Component | v1.2 target | v1.3 target | Current in tree | Action |
|---|---|---|---|---|
| `ask.ko` (in-tree after 0047/0048) | 3700 | **2800** | ~3900 (with `ask_hostcmd.c` + `ask_neigh.c` deferred-resolve + graft logic) | Phase 3 drops ~600, Phase 4 drops ~800 = ~2500 land |
| `0001-caam-qi-share` | 150 | 150 | landed | — |
| `0002-dpaa-eth-flow-block` | 300 | 300 | landed (0029) | — |
| `0003-fman-host-command-api` | 200 | **0** | landed but unreferenced | Phase 3 archive |
| `0004-fman-pcd-subsystem` (PCD core) | 10 000 | **5 500** | ~9 800 (with OH-port + manip-chain encoders) | Phase 2 archive ~4 500, leaves ~5 300 |
| `0005-dpaa-eth-pcd-pre-register-hook` | 0 | **150** | landed as 0044 | — |
| `askd` daemon | 4000 | **0** | 0 (never written) | Phase 5 doc-lock |
| `ask-cli` Python | 800 | **0** | 0 (never written) | Phase 5 doc-lock |
| VyOS CLI integration | 1200 | 800 | 0 (deferred) | Phase 5 schedule |
| Build pipeline | 600 | 400 | ~500 | small trim |
| Test suite | 2700 | 1600 | ~1900 | Phase 3 drops hostcmd tests |
| Documentation | 1500 | 1000 | ~2200 (spec + plans) | Phase 1 trims |
| **Total** | **~24 950** | **~12 700** | **~18 600** | **target ~12 700** |

Delta to land: **~5 900 LOC of deletions** across Phases 2–4. About one engineer-week of edit work plus the hardware bring-up cycle.

---

## 4. Risks and mitigations

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| R1 | `FORWARD_FQ_WITH_MANIP` on inline CC-key action doesn't work as RM §8.7.3.4 documents | Low-medium | OH-port patches stay in `archived/`; ~1 day to restore as v1.1 fallback. Cost bounded. |
| R2 | Pre-`register_netdev` hook (Path A) breaks `dpaa_eth` probe on default flavor | Low | Hook is Kconfig-gated `FSL_DPAA_PCD_PRE_INIT_HOOK=y` only when `CONFIG_ASK=y`. Default flavor builds with hook absent. |
| R3 | Empty CC tree adds idle CPU on schemes 3/4 (review Risk #3) | Low | Measure at Phase 4 step 4.8.3 with `ask-pcd-regdump.py --history`. RM says zero-key walk = one MURAM read + miss; ~10 ns/packet. |
| R4 | Deletion of `ask_neigh.c` removes neigh-update handling needed for late-arrival flows | Medium | Path A pre-installs CC tree → flow_offload subsystem already resolves neigh before `flow_block_cb` REPLACE fires. Verified path is: `nf_flow_offload_route` → `flow_offload_dst_xfrm` → `dev_fill_metadata_dst` → MAC populated → flow_block_cb. No daemon needed. |
| R5 | Spec rewrite (Phase 1) takes longer than estimated due to scope creep | Medium | Hard ceiling: Phase 1 is a single PR. Stop at the §3.2/§3.4/§12/§13.2/§15.1/§19 edits the review explicitly names; defer other tidying to a v1.4. |
| R6 | M2 perf gate still fails after Phase 4 due to a non-CC-action bottleneck (e.g. TX-conf still confirms every frame despite 0053) | Medium | Phase 4 step 4.9 is non-rollback: file follow-up bug, M2-perf becomes a separate workstream. Path A is correct independent of perf. |
| R7 | We later need an `askd`-shaped userspace process for something we haven't anticipated | Low | Phase 5 doc-lock is reversible. Re-add `ask-vpp-promote` in v1.1 if a real VPP-hybrid user surfaces. |

---

## 5. Test gates (no phase exits until these pass)

For every phase:

- [ ] `bash scripts/patch-health.sh --source release --flavor ask` → `Pass=N Fail=0`.
- [ ] `bash scripts/patch-health.sh --source release --flavor default` → unchanged from pre-phase baseline (proves we didn't break the default flavor).
- [ ] `bash bin/local-build.sh ask` → ISO builds clean.
- [ ] `gh workflow run "VyOS LS1046A build (self-hosted)" --ref ask20` → CI green.

For Phase 4 specifically:

- [ ] ISO flashed to mono DUT, boots to login banner < 90 s.
- [ ] `dmesg | grep ask:` shows `pcd install: schemes 3+4 claimed` BEFORE first `dpaa_eth … eth3` register banner.
- [ ] `dmesg | grep -i graft` returns empty (PR14z13/z15/z18 lines are gone).
- [ ] `ask-pcd-regdump.py` shows KGSE_SPC counting on schemes 3 + 4 at idle.
- [ ] `bin/verify-ask-flow-offload.sh` → throughput ≥ 2 Gbps AND CPU ≤ 5% (M2 hard gate from spec §11.1).
- [ ] Stretch: throughput ≥ 7 Gbps AND CPU < 5% (the review's claim for inline-MANIP CC-key action).
- [ ] 10-cycle stress (nft flow add → measure → nft flow del → measure SW-only → repeat) with **zero** silicon wedge, **zero** reboot needed.

---

## 6. Out-of-scope for this course-correction

The following are deliberately **not** addressed here and remain on the v1.1 / future roadmap:

- IPsec re-inject via OH-port (`oh@d4000` only). When this lands, restore patches 0032/0034/0036/0038 from `archived/` selectively for the IPsec path only — the L3 forward path stays on inline MANIP.
- VPP hybrid handoff (`ask-vpp-promote`, ~600 LOC oneshot).
- v6 (IPv6 5-tuple offload) — currently the spec covers it but no PR series exists; defer until M3.
- `nf_flow_table` bridge HW-offload — review §5 keeps it. M4 work, not blocked by anything here.
- Multicast / fragmentation offload — M6.

---

## 7. Entry point for the next session

After this document lands, the next ASK2 work session should:

1. Run `qdrant-find "ASK2 course-correction Path A Phase 1"` to recover any insights stored during Phase-1 execution.
2. Open `plans/ASK2-COURSE-CORRECTION.md` (this file).
3. Find the first unchecked `- [ ]` in §2 and execute it.
4. After each phase completes, store a single dense `qdrant-store` entry summarizing: which patches archived, which LOC deleted, which dmesg banners changed, what M2 measurement was observed.
5. Update §3 LOC table's "Current in tree" column after each phase.

The single most important commit to make first is **§2.1.11** — the v1.3 spec reconciliation. Everything else flows from that document being correct.

---

## 8. References

- `plans/ASK2-MODERN-ARCHITECTURE-REVIEW.md` (2026-05-24) — the driver document this plan executes.
- `plans/ASK-VS-ASK2-COMPARATIVE-REVIEW.md` (2026-05-23) — Path A justification.
- `plans/PR14z19-PATH-A-DESIGN.md` (2026-05-23) — concrete hook design that Phase 4 builds on.
- `specs/ask2-rewrite-spec.md` v1.2 — the document Phase 1 revises to v1.3.
- `kernel/flavors/ask/patches/` — the patch stack Phase 2 audits and archives.
- `kernel/flavors/ask/oot-modules/ask/` — the source tree Phases 3 and 4 shrink.
- Qdrant memories tagged `ASK2`, `PR14z*`, `m2-gate`, `path-A`, `pre-netdev-hook`, `fman-pcd`.