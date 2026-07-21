# VPP AF_XDP Zero-Copy Full-Speed Plan

**Version 1.0.0 · 2026-07-21 · HADS 1.0.0**

## AI READING INSTRUCTION

This is the execution plan to drive the **VPP AF_XDP zero-copy (ZC) dataplane**
on the LS1046A DPAA1 board to its maximum sustainable throughput. It is scoped
to the AF_XDP overlay (state S2 in `plans/DUAL-DATAPLANE.md`) — the kernel keeps
the netdev, VPP runs XSK sockets on top. It is **not** the FMan hardware-offload
path (ASK2 AC_CC), which is tracked in `plans/ASK2-MASTER-PLAN.md`.

Read `[SPEC]` blocks for binding requirements, `[NOTE]` for rationale/history,
`[BUG]` for defects (symptom + cause + fix), `[?]` for unverified claims.
Numbered `##` sections are stable anchors. Where this plan disagrees with
`arch/fman-microcode-210-programming-reference.md` or the kernel source, those
win — update this plan.

Authoritative cross-references: `specs/dpaa1-afxdp-modernization-spec.md`
(AF_XDP kernel design), `plans/DUAL-DATAPLANE.md` (S0/S2 state machine),
`plans/VPP.md` (VPP native integration), AGENTS.md S5 (DPAA1 XDP invariants).

---

## 1. Objective and Success Gate

**[SPEC]** Goal: sustained, bidirectional VPP AF_XDP **zero-copy** forwarding on
eth3/eth4 (10G SFP+), measured by `iperf3` through the board, with the ZC RX
oracle `xsk_zc_rx_redirect` strictly increasing under load and TX ZC submits
(`xsk_tx_zc_submit`) strictly increasing.

**[SPEC]** Throughput targets (single VPP worker, one isolated A72 core):

| Gate | Metric | Threshold | Basis |
|------|--------|-----------|-------|
| G-ZC-1 | `xsk_zc_rx_redirect` under load | strictly increasing (≥1000/s) | functional RX ZC |
| G-ZC-2 | `xsk_tx_zc_submit` under load | strictly increasing | functional TX ZC |
| G-ZC-3 | iperf3 single-stream through board | ≥ 3.0 Gbps | AF_XDP single-core realistic |
| G-ZC-4 | iperf3 sustained 60 s | no collapse, 0 kernel copies | stability |
| G-ZC-5 (stretch) | iperf3 multi-stream, multi-queue | ≥ 6.0 Gbps | multi-XSK Option A |

**[NOTE]** The single-core AF_XDP ceiling on this hardware is ~3.5 Gbps
(AGENTS.md S5: "~3.5 Gbps on 10G SFP+"). VPP does the L3 forwarding in software
on the isolated poll-mode core; that core is the bottleneck, not the wire.
Line-rate 10G is **not** an AF_XDP goal — it belongs to the FMan hardware-offload
path (ASK2 AC_CC already reaches 9.62 Gbps, `plans/ASK2-MASTER-PLAN.md`).

**[NOTE]** Strategic decision point for the operator: if the requirement is
line-rate 10G forwarding, AF_XDP is the wrong tool and ASK2 AC_CC is the answer.
AF_XDP's value is a *general-purpose* VPP dataplane (NAT, ACL, tunnels, PPPoE)
at multi-Gbps, not raw forwarding speed. This plan assumes the former.

---

## 2. Current State (2026-07-21)

**[SPEC]** Board `.185`: `vyos-2026.07.21-0358-rolling`, kernel `6.18.38-vyos`,
VPP 25.10. Peer `.106` on `10.99.1.0/24` (eth3) and `10.99.2.0/24` (eth4),
10G SFP+ direct connect, link UP, native kernel iperf3 baseline **1.18 Gbps**.

**[SPEC]** Verified-working reference point: on **2026-06-10**, image
`2026.06.10-0124-rolling`, kernel **6.18.34-vyos**, the ZC RX oracle
`xsk_zc_rx_redirect` fired **0→7→8** on the DUT with patches 0102b + 0103f +
0103g. True zero-copy RX was HW-proven (FMan DMA'd echo-replies directly into
XSK UMEM, driver recovered `xdp_buff` via the 0103a sorted-DMA reverse-map,
`xdp_do_redirect` into XSKMAP, no `build_skb`). See qdrant
`dpaa1-true-zc-rx-oracle-fired-dut-validated`.

**[BUG] ZC RX regression on 6.18.38.** Symptom: on current kernel `6.18.38-vyos`
`xsk_zc_rx_redirect` stays 0, `xsk_rx_branch` stays 0, VPP RX counters do not
increment; ping/iperf3 through VPP fail. Cause: **not yet root-caused** — the
productive datapath that fired on 6.18.34 no longer fires after four kernel
bumps (6.18.34 → 6.18.38) and patch-stack churn. Candidate causes in §4. Fix:
Phase 0 below. **Do not treat the "missing kallsyms symbols" observation as the
bug** — `dpaa_xsk_chunk_lookup`/`dpaa_xsk_chunk_head_from_dma` are `static` and
subject to dead-code elimination; their absence from `/proc/kallsyms` is
expected and is **not** a reliable oracle. The authoritative oracle is
`ethtool -S <netdev> | grep xsk_zc_rx_redirect`.

**[BUG] Copy-mode RX is a dead end on DPAA1.** Symptom: kernel receives 536K
packets on `defunct_eth3` but VPP XSK socket sees none; VPP RX counter frozen.
Cause: the DPAA1 XDP generic-redirect (`bpf_redirect_map` → XSKMAP copy path)
does not deliver into VPP's XSK socket. Fix: **abandon copy-mode**; pursue ZC
only. Copy-mode was only ever a diagnostic scaffold (qdrant
`blocker_b` 2026-05-28 explicitly warns "COPY-MODE redirect ... True ZC ...
requires follow-on patches"). Verified dead-end 2026-07-21.

**[SPEC]** Live board hacks applied 2026-07-21 that must be folded into patches
(not left as manual edits): (a) `vpp.py` force `mode='copy'` — to be replaced by
proper ZC selection; (b) `vpp.py` + `startup.conf.j2` LCP `default netns
default`; (c) static ARP workarounds. These are captured in qdrant
`vpp af_xdp copy-mode lcp netns` and enumerated in §6.

---

## 3. Architecture Recap (why ZC, not copy)

**[SPEC]** DPAA1 has no mainline XSK ZC support. The out-of-tree
`af_xdp_pool` module (patches 0070–0114) implements it by:

1. **Attach** (`af_xdp_pool_attach`): allocate a BMan pool with a fresh bpid,
   `xsk_pool_dma_map()` on `priv->rx_dma_dev`, seed the pool from XSK UMEM
   chunks, register a per-band `xdp_rxq_info` with `MEM_TYPE_XSK_BUFF_POOL`
   (0103g), and reprogram the FMan RX port's primary bpid
   (`fman_port_set_rx_bpool`, 0102) so silicon DMAs ingress frames **directly
   into XSK UMEM chunks**.
2. **RX** (`rx_default_dqrr` rx_hook, dispatched before the `dpaa_bpid2pool`
   NULL-guard by 0103f): recover the `xdp_buff` for the incoming `dma_addr_t`
   via the 0103a sorted reverse-map bsearch, `xsk_buff_set_size` +
   `xsk_buff_dma_sync_for_cpu`, then `xdp_do_redirect` into the XSKMAP.
3. **Refill** (napi): `xsk_buff_alloc_batch` → `bman_release` fresh chunks.
4. **Detach**: restore the kernel page-pool bpid, unregister rxq, free the pool.

**[SPEC]** The reprogram-WRITE (step 1 bpid flip) and the Recover-redirect
(step 2) are **inseparable**: firing the reprogram without a working Recover
DMAs silicon frames into XSK chunks that the kernel `build_skb` path then
mis-consumes → free-list corruption / soft-lockup (qdrant `0103-blocker`
§6.1.8). Any change that disables one must disable the other.

**[NOTE]** Because ZC bypasses the kernel netdev entirely on the ZC-armed band,
the LCP ARP problem (§6) is moot on that band — VPP owns the frames. LCP is only
needed for the control-plane (ARP/ND/routing sync) on non-ZC traffic.

---

## 4. Phase 0 — Restore ZC RX (regression fix) [BLOCKER]

**[SPEC]** Nothing else proceeds until `xsk_zc_rx_redirect` increments again on
6.18.38. This is a regression from a known-good state, so bisect against the
2026-06-10 delta rather than re-deriving.

### T-0.1 — Establish the correct oracle
- **[SPEC]** Stop using `grep kallsyms`. Use, on the ZC-armed netdev
  (`defunct_eth3` when VPP owns eth3):
  `ethtool -S defunct_eth3 | grep -E 'xsk_zc_rx_redirect|xsk_zc_recover_lookup|xsk_rx_branch|xsk_zc_rx_armed|xsk_pool_attach_ok'`.
- **[SPEC]** Reproduce the 2026-06-10 method that fired the oracle: bind a raw
  XSK probe with XSKMAP (`bin/dpaa1-xsk-bind-probe.py eth3 0 4096 --hold N
  --xskmap`) + DUT-side `ping -I eth3 <peer>` (echo-replies ingress on eth3, no
  peer cooperation needed). This isolates the kernel ZC datapath from VPP and
  from the copy/LCP layer. If the probe fires the oracle but VPP does not, the
  regression is in the VPP integration (Phase 3), not the kernel.

### T-0.2 — Confirm the kernel config gates the module
- **[SPEC]** Verify in the built `.config`: `CONFIG_XDP_SOCKETS=y`,
  `CONFIG_DPAA_AF_XDP_POOL` present (=y or =m), `CONFIG_BPF_SYSCALL=y`. Kconfig
  dep chain: `DPAA_AF_XDP_POOL depends on FSL_DPAA_ETH && DPAA_FLAVOR_OPS &&
  XDP_SOCKETS`. A silently-dropped `=m`→unset across the 6.18.38 bump would
  remove the whole datapath while the build still succeeds.

### T-0.3 — Verify patch application on 6.18.38 (no silent fuzz)
- **[SPEC]** CI hard-gates `git apply --3way` failures
  (`bin/ci-setup-kernel.sh` line ~1088 aborts). So 0103a/0103b/0103f/0103g
  *apply*. But `--3way` can merge into a **different** location on drifted
  source. Manually inspect the merged result in a throwaway build tree:
  confirm the rx_hook dispatch in `dpaa_eth.c::rx_default_dqrr` still sits
  **before** the `dpaa_bpid2pool(fd->bpid)` NULL-guard (0103f invariant). If the
  6.18.38 `rx_default_dqrr` was refactored, `--3way` may have reinserted the
  hook after the guard → frames dropped before the hook, oracle stuck at 0.
  This is the **single most likely root cause** and matches the exact 0103f bug
  signature from 2026-06-10.

### T-0.4 — Audit Layer-2 fixup second-writers
- **[BUG] Fixup/patch collision risk.** `F_100` and `F_103` both mutate
  `af_xdp_pool_main.c` **after** the patch stack (Layer 2, per AGENTS.md S9).
  `F_103` is SUPERSEDED/no-op but must be confirmed inert. `F_100`
  (attach-path instrumentation) must not overwrite or displace the productive
  reprogram-WRITE or the 0103g rxq registration. Run
  `bin/mutate.py --check` for both and diff the pre/post `af_xdp_pool_main.c`.
  Any fixup that mutates the productive body is a defect — fold it into the
  owning patch or delete it.

### T-0.5 — Verify 0103g rxq registration + reprogram gating
- **[SPEC]** 0103g registers a per-band `struct xdp_rxq_info` with
  `xdp_rxq_info_reg_mem_model(MEM_TYPE_XSK_BUFF_POOL, NULL)` and **gates the
  reprogram-WRITE on successful rxq registration** (failure → `xsk_pool_attach_fail++`,
  skip reprogram, no crash). Confirm on 6.18.38 that `xdp_rxq_info_reg()` still
  has the same signature and that `xsk_pool_set_rxq_info()` exists. A signature
  change here would make attach fail-soft (no ZC) rather than crash — exactly
  the "armed but oracle stuck at 0" symptom.

### T-0.6 — Cold-boot before each silicon test
- **[SPEC]** AGENTS.md S6 §10.9: a deaf port is accumulated BMI/MURAM
  corruption, not the commit under test. Warm reboot does not clear it. Every
  Phase-0 attach/detach experiment starts from a cold power-cycle
  (`restart-dut` skill). Record boot type per result.

**Phase 0 exit gate:** `bin/dpaa1-xsk-bind-probe.py eth3 0 4096 --hold 30
--xskmap` + `ping -I eth3 <peer>` drives `xsk_zc_rx_redirect` strictly upward on
a cold-booted 6.18.38 board. **G-ZC-1 met at kernel level.**

---

## 5. Phase 1 — Single-Queue ZC through VPP

**[SPEC]** Prove ZC end-to-end through VPP on **one** RX queue (qband 0), the
only band whose bpid can be reprogrammed (§7). This removes the multi-queue
variable while validating the VPP path.

### T-1.1 — Force single XSK queue
- **[SPEC]** Set VPP `rxq_num=1` so all RSS traffic that VPP consumes lands on
  qband 0. In VyOS: the `xdp_options num_rx_queues` path in `vpp.py`
  (`data/vyos-1x-010-vpp-platform-bus.patch`). Do **not** rely on F_104
  (get_channels=4) here — that is Phase 4.

### T-1.2 — Force ZC mode explicitly
- **[SPEC]** `xdp_iface_create` must be called with `mode='zero-copy'` (maps to
  api arg `mode=2`), **not** `auto` (which probes ZC then silently falls back to
  copy) and **not** the current board hack `mode='copy'`. Replace the
  2026-07-21 `mode='copy'` edit with a driver-aware selection: DPAA1
  (`original_driver == 'fsl_dpa'`) → `zero-copy`.

### T-1.3 — Enforce ZC MTU
- **[SPEC]** MTU ≤ **1766** before VPP bind (AGENTS.md S5; VPP frame_size=1792,
  needed=MTU+26). MTU must be set in a **separate commit before** adding the
  interface to VPP (qdrant `MTU must be set BEFORE VPP bind`). F_101 lowers
  `DPAA1_MIN_UMEM_CHUNK` 3840→1792 to accept VPP's 2048-byte chunks — keep it.

### T-1.4 — Traffic source constraint
- **[SPEC]** A DRV-mode XDP program on eth3 **hijacks local RX** — the board
  cannot both source and sink test traffic on the same ZC-armed port. Test
  traffic must be **peer-initiated** from `.106` (iperf3 client on peer →
  server behind VPP), or use a second interface. This is GAP 2 from 2026-06-10
  and the reason the oracle was proven functional but never throughput-tested.

### T-1.5 — Verify the datapath, not just link
- **[SPEC]** Under peer-initiated load, confirm simultaneously:
  `xsk_zc_rx_redirect` ↑, `xsk_zc_recover_lookup` ↑ (tracks redirect 1:1),
  `xsk_zc_rx_armed ≥ 1`, `FMBM_EBMPI[0]` shows the XSK bpid (via
  `board/scripts/pcd-snapshot` or 0102b dev_info readback), and VPP
  `show interface` RX counters increment. Kernel `build_skb` path must show
  **zero** RX on the ZC band.

**Phase 1 exit gate:** iperf3 peer→VPP→sink single-stream over one ZC queue,
`xsk_zc_rx_redirect` increasing, VPP RX incrementing. **G-ZC-1 through the full
VPP stack.**

---

## 6. Phase 2 — ZC TX + Control-Plane Integration

### T-2.1 — Enable and verify ZC TX
- **[BUG] TX ZC not submitting.** Symptom: `xsk_tx_zc_submit=0`,
  `xsk_tx_conf_zc=0` even when RX ZC works; TX falls to the `syscall required`
  copy path (1271 errors observed 2026-07-21). Cause: TX ZC path (patch 0085
  `dpaa-tx-zc-and-inflight-backpressure`) not exercised / not wired to VPP TX.
  Fix: verify VPP issues `sendto()`-less ZC TX (needs
  `XDP_USE_NEED_WAKEUP` handling, patch 0070 `xsk-wakeup`), confirm
  `xsk_tx_zc_submit` ↑ under bidirectional load. **G-ZC-2.**

### T-2.2 — Fix LCP netns (fold board hack into patch)
- **[BUG] LCP default netns unset.** Symptom: `lcp default netns '<unset>'`;
  `linux-cp/router: Failed to delete neighbor`; ARP not synced kernel↔VPP.
  Cause: `lcp_pair_add()` in `control_vpp.py` never passes `netns`; the config
  template omits `default netns`. Fix (fold the 2026-07-21 live edits into
  `data/vyos-1x-010-vpp-platform-bus.patch`): add `default netns default` to the
  `linux-cp { }` block in `startup.conf.j2`, and emit
  `lcp default netns default` as a CLI command in `vpp.py` `apply()` **before**
  `lcp_resync()`. Do **not** pass `netns` in the per-pair `lcp create` API
  (it returned `-12 ENOMEM`).

### T-2.3 — LCP is L3-only; do not expect L2/ARP bridging
- **[SPEC]** LCP `type tap` is an L3 tunnel — it does **not** forward L2 frames
  (ARP) between the physical port and the tap. For ZC-armed bands this is moot
  (VPP owns RX). For the control plane, VPP must answer/originate ARP itself
  (VPP has the interface IP via `lcp-sync`); the kernel tap is for routing-daemon
  sync only. Verify VPP `show ip neighbors` populates from VPP's own ARP, not
  from a kernel→tap bridge. Static ARP is a test crutch, **not** the shipping
  design.

### T-2.4 — Remove copy-mode fallback from shipping config
- **[SPEC]** Once ZC is stable, the `mode='copy'` board hack is deleted. DPAA1
  interfaces ship `mode='zero-copy'`. If ZC attach fails, the interface must
  **fail loudly** (interface down + log), not silently degrade to a broken
  copy-mode.

**Phase 2 exit gate:** bidirectional iperf3, both `xsk_zc_rx_redirect` and
`xsk_tx_zc_submit` increasing, control-plane ARP/routing stable, no static-ARP
crutches. **G-ZC-2 + G-ZC-4.**

---

## 7. Phase 3 — Multi-Queue ZC (stretch, for >3.5 Gbps)

**[BUG] Only qband 0 accepts the bpid reprogram.** Symptom: on 4-queue RSS,
`FMan RX-port BPID reprogram failed (-2 ENOENT)` for qbands 1–3; only qband 0
DMAs into XSK UMEM. 75% of RSS-distributed frames bypass XSK. Cause: the FMan
RX port exposes **one** primary bpid register (`FMBM_EBMPI[0]`); the current
`fman_port_set_rx_bpool` (0102) reprograms only that. Fix options, in preference
order:

- **[SPEC] Option A — per-qband external bpid.** Investigate whether the FMan
  BMI supports distinct bpids per RX FQ range via the external buffer pool
  registers `FMBM_EBMPI[0..7]` (0102b already reads all 8 back). If silicon
  allows one XSK bpid per qband, extend `fman_port_set_rx_bpool` to program the
  band's slot. Validate against
  `arch/fman-microcode-210-programming-reference.md` and the live NXP stack on
  `.106` before writing (AGENTS.md S1 three-refs rule).
- **[SPEC] Option B — RSS-collapse to queue0.** Reprogram the port's KG/RSS
  scheme so all VPP-destined traffic hashes to qband 0 (single XSK), and let
  `rxq_num=1`. Reuses the KG scheme-reprogram machinery from `0097`/`0104`
  (ingress-policer PLCR attach). Simplest, but caps ZC at one core's worth of
  traffic (~3.5 Gbps) — acceptable if G-ZC-3 is the real target.
- **[SPEC] Option C — accept the cap.** Ship single-queue ZC (~3.5 Gbps). Point
  users needing more at ASK2 AC_CC.

**[NOTE]** F_104 (get_channels → 4 combined channels) already makes VPP create 4
XSK sockets. Without Option A, three of them are attached to bands whose frames
never arrive in XSK UMEM — wasted UMEM and misleading `xsk_zc_rx_armed=4`. Do
**not** ship 4-queue advertising until Option A lands, or reduce to 1.

**[SPEC] Thermal constraint on multi-core.** Multi-queue only helps if VPP has
multiple worker cores to drain them. But AF_XDP poll-mode has no adaptive
rx-mode on DPAA1 (`set interface rx-mode` fails), so multiple poll-mode workers
run the cores hot → `HARDWARE PROTECTION shutdown` within ~30 min (AGENTS.md S5).
Mitigations: `poll-sleep-usec 100` (mandatory), and realistically **cap at one
worker core**. This means multi-queue ZC's practical benefit on this board is
limited; Option B (single queue, one hot core, `poll-sleep-usec 100`) is the
pragmatic shipping config.

**Phase 3 exit gate (stretch):** multi-stream iperf3 ≥ 6 Gbps **or** documented
decision to ship single-queue with the thermal rationale. **G-ZC-5 or explicit
waiver.**

---

## 8. Phase 4 — Throughput Tuning and CI Bake-In

### T-4.1 — Measure and profile
- **[SPEC]** iperf3 matrix: single/multi-stream, 60 s sustained, MTU 1766.
  Record Gbps, `xsk_*` deltas, VPP `show run` node cycles, CPU via `mpstat`,
  and thermals via `board/scripts/fan-check`. Compare against native-kernel
  1.18 Gbps baseline and the AF_XDP ~3.5 Gbps ceiling.

### T-4.2 — VPP knobs
- **[SPEC]** Tune `poll-sleep-usec` (thermal vs latency), `buffers-per-numa`,
  RX/TX ring sizes (`rxq_size`/`txq_size`), and UMEM chunk count. Single worker
  pinned to isolated core 3 (`cpu-cores 1`, `main-core 3`).

### T-4.3 — Fold all board hacks into version-controlled patches
- **[SPEC]** Everything applied live to `.185` on 2026-07-21 must land in
  `data/vyos-1x-010-vpp-platform-bus.patch` (ZC mode selection, LCP netns) and,
  if kernel-side, in the board patch series / a new count-gated fixup. No
  shipping behavior may depend on a manual SSH edit. Regenerate patches via
  `git diff --cached` and verify `git apply --3way --check` (AGENTS.md S9).

### T-4.4 — CI ISO + deploy + cold-boot validation
- **[SPEC]** Build via `self-hosted-build.yml`, deploy the ISO to lxc200 with
  the `build-image` skill, `add system image` on a **cold-booted** `.185`, and
  re-run the full G-ZC-1…G-ZC-4 gate. Record image tag, run id, kernel version,
  and boot type in qdrant.

**Phase 4 exit gate:** reproducible ≥3.0 Gbps single-stream ZC on a
freshly-installed CI ISO, all behavior patch-sourced, results in qdrant.
**G-ZC-3 + G-ZC-4 on shipping image.**

---

## 9. Risk Register

**[SPEC]**

| # | Risk | Likelihood | Mitigation |
|---|------|-----------|------------|
| R1 | 0103f hook-order lost to `--3way` drift on 6.18.38 | High | T-0.3 manual merge inspection; pin hook before NULL-guard |
| R2 | `CONFIG_DPAA_AF_XDP_POOL` silently unset | Medium | T-0.2 config assertion in CI |
| R3 | F_100/F_103 overwrite productive body | Medium | T-0.4 pre/post diff, fold or delete |
| R4 | `xdp_rxq_info`/`xsk_pool_set_rxq_info` API drift | Medium | T-0.5 signature check; fail-soft already gated |
| R5 | Multi-queue bpid reprogram unsolvable in silicon | Medium | Option B/C fallback to single queue |
| R6 | Thermal shutdown under multi-core poll | High | `poll-sleep-usec 100`, cap 1 worker |
| R7 | ZC crash (NULL rxq / free-list corruption) under flood | Medium | 0103g rxq gating + F_102 NULL-fq guard; **repro with pings, never floods, until cold-boot-safe** (AGENTS.md) |
| R8 | gen_pool double-free on double-engage | Low | engagement guard (existing TODO) |

---

## 10. Decision Log

**[NOTE]**
1. **ZC only; copy-mode abandoned.** DPAA1 XDP generic-redirect does not deliver
   to VPP XSK. Copy-mode was a diagnostic scaffold, never a product path.
2. **Regression-fix, not greenfield.** ZC RX was HW-validated 2026-06-10; Phase
   0 bisects the 6.18.34→6.18.38 delta rather than re-deriving the datapath.
3. **`xsk_zc_rx_redirect` is the oracle.** kallsyms grep of `static` helpers is
   not a valid signal.
4. **Single-queue is the pragmatic shipping config.** Multi-queue ZC is
   thermally and silicon-constrained; ~3.5 Gbps single core is the honest AF_XDP
   ceiling. >3.5 Gbps forwarding → ASK2 AC_CC, not AF_XDP.
5. **All board hacks must become patches** before any claim of "working."

---

## 11. Immediate Next Actions (ordered)

**[SPEC]**
1. Cold power-cycle `.185` (`restart-dut`).
2. T-0.1: run `dpaa1-xsk-bind-probe.py --xskmap` + local `ping -I eth3`; read
   `xsk_zc_rx_redirect`. If it fires → regression is in VPP integration (skip to
   Phase 1). If it stays 0 → kernel regression, continue.
3. T-0.3: inspect merged `rx_default_dqrr` in a build tree; confirm 0103f hook
   sits before the `dpaa_bpid2pool` NULL-guard on 6.18.38.
4. T-0.2 + T-0.4: assert `CONFIG_DPAA_AF_XDP_POOL`; diff F_100/F_103 effect on
   `af_xdp_pool_main.c`.
5. Report root cause, land the fix as a patch/fixup, rebuild, redeploy, re-gate.
