# ASK2 — Next Steps to Operational + Optimized (v1.0 GA)

**Date:** 2026-05-25
**Branch:** `ask20`
**Status:** Forward-looking roadmap (post Phase 4 hardware bring-up)
**Driver doc:** `plans/ASK2-COURSE-CORRECTION.md` (Phases 1–5 landed)
**Reference spec:** `specs/ask2-rewrite-spec.md` v1.3

---

## 0.1 BREAKTHROUGH (2026-05-25 night) — Path A scheme allocates but loses KG priority race

**Definitive forensic verdict, all prior H1/H2/H3/H5 hypotheses obsoleted.**

### What the post-burst regdump actually shows (fresh-boot run, 18M-packet iperf3)

| Scheme | KGSE_FQB | KGSE_SPC (live) | KGSE_CCBS | Owner | Verdict |
|---|---|---:|---|---|---|
| 0 | 0x080 | 4,239 | 0x00000000 | kernel (port 0x09, eth0 mgmt) | catches all eth0 traffic |
| 1 | 0x100 | 5,268 | 0x00000000 | kernel (port 0x0c, eth1) | catches all eth1 traffic |
| 2 | 0x180 | 4,214 | 0x00000000 | kernel (port 0x0d, eth2) | catches all eth2 traffic |
| 3 | 0x200 | **18,017,267** | 0x00000000 | kernel (port 0x10, eth3 SFP+) | **catches all iperf3 traffic** |
| 4 | 0x300 | **1,285,178** | 0x00000000 | kernel (port 0x11, eth4 SFP+) | **catches all return traffic** |
| 5 | 0x080 | **0** | **0x0004ac00** | ASK (port 0x09) | CC tree wired, never dispatched |
| 6 | 0x100 | **0** | **0x0004b900** | ASK (port 0x0c) | CC tree wired, never dispatched |
| 7 | 0x180 | **0** | **0x0004c600** | ASK (port 0x0d) | CC tree wired, never dispatched |
| 8 | 0x200 | **0** | **0x0004d300** | ASK (port 0x10) | CC tree wired, never dispatched |
| 9 | 0x300 | **0** | **0x0004e000** | ASK (port 0x11) | CC tree wired, never dispatched |

### Root cause (H6, supersedes H1/H2/H3/H5)

**Path A's `ask_pcd_install_hook()` allocates fresh KG schemes (5–9) and correctly
wires `KGSE_CCBS` to the ASK CC tree** (kgse_ccbs = 0x4ac00..0x4e000, non-zero ⇒ CC
tree bound). The install banner `INSTALLED — empty cc_v4_tcp + cc_v4_udp trees,
miss→FQ 0xN` accurately reflects silicon state.

**BUT** the in-tree `dpaa_eth` driver already created its own KG schemes (0–4) at
MAC probe (before ask.ko's `fs_initcall` hook fires) and bound them to the same
hwports. FMan KG arbitration on a per-port scheme conflict resolves by **lowest
scheme ID wins** (RM §8.7.4 + DPAA SDK fm_kg.c: scheme_id is also the priority
index in `fmkg_pe_sp`). Scheme 3 (kernel) beats scheme 8 (ASK) on port 0x10.

Evidence:
- ASK schemes 5–9: `kgse_spc = 0` ⇒ silicon never dispatches a frame to them
- Kernel schemes 3,4: `kgse_spc = 18M / 1.28M` ⇒ silicon dispatches everything here
- Both sets have `EN=1` and the SAME `KGSE_FQB` per port ⇒ both eligible, but only
  one runs

### Why the install banner is misleading

The Path A pre-netdev hook fires from inside `fman_port_init()`. At that point
mainline's `keygen_port_hashing_init()` has ALREADY run for this port — the kernel
scheme is already bound to `fmkg_pe_sp`. ASK's `fman_pcd_kg_bind_port()` ADDS its
scheme to `fmkg_pe_sp` (bitwise OR) rather than REPLACING the kernel's binding.
Net result: TWO schemes bound, kernel wins because of lower ID.

This contradicts the patch 0044 header claim that "the netdev will come up
downstream of an already-active PCD chain" — the hook fires AFTER `keygen_port_hashing_init`
ran, not before. The `pre_netdev_hook` invocation in `fman_port.c` line 1490+ branches
on `err == 0` (hook claimed) vs `err == -ENOENT` (hook declined); on -ENOENT the
default keygen runs — **but `ask_pcd_install_hook()` returns 0 (claim) yet ALSO
falls through into the default keygen path** because the install function does not
actually displace the kernel's pre-existing scheme.

Re-reading `fman_port_init()` from patch 0044 (lines 1485-1518): the hook is
invoked WHERE the default `keygen_port_hashing_init()` would have run, with an
either/or branch. Claim → skip default. Decline → run default. So if ASK
claimed port 0x10, mainline keygen for port 0x10 was skipped, and ASK is the only
KG scheme on that port. But the regdump shows BOTH schemes 3 (kernel) AND 8 (ASK)
have KGSE_FQB matching port 0x10's base — meaning the kernel ALREADY allocated
scheme 3 for port 0x10 BEFORE Path A's hook fired (probably during a prior boot
phase via `dpaa_eth_probe → keygen_port_hashing_init` directly, not through the
hooked `fman_port_init` path).

### What the fix has to do

**Option A — true graft (resurrect the archived 0042 model):** locate kernel
scheme 3 by walking `fmkg_pe_sp` for port 0x10, write its KGSE_CCBS to point at
the ASK CC tree, drop the ASK-allocated scheme (5–9). Pros: zero conflict,
kernel's parser/extract recipe is reused. Cons: reverts the architectural pivot.

**Option B — kernel scheme unbind:** after ASK install, call
`keygen_scheme_unbind_port()` on the kernel scheme so only ASK's scheme is left
in `fmkg_pe_sp`. Pros: keeps Path A scheme allocation. Cons: kernel loses its
RSS hashing if we don't re-emulate it in the ASK CC tree's miss-action.

**Option C — scheme ID swap:** request specific scheme IDs (0–4) at
`fman_pcd_kg_scheme_create()` time and let the kernel get the higher IDs.
Pros: minimal code change. Cons: depends on hard-coded ordering at probe time
(kernel runs first); allocator likely returns first-free which gives lowest.

**Recommended:** Option A (true graft). It's what the archived 0042 patch did,
it's what the FMan SDK has always done, and Path A's only real architectural
benefit (pre-netdev install) is preserved — we just change WHAT we install (graft
the kernel scheme to our CC tree, instead of allocating a new scheme).

### Action items (replaces the entire former §0 and Tier-2 H1/H2/H3 set)

1. ✅ **DONE 2026-05-25** — `kernel/flavors/ask/patches/0065-fman-pcd-graft-kernel-scheme.patch` authored. Exports `fman_pcd_kg_lookup_port_scheme` / `fman_pcd_kg_graft_cc` / `fman_pcd_kg_ungraft_cc` (KGSE_CCBS-only RMW; `KGSE_MODE` intentionally untouched per USDPAA reference and the 0043 disproof / 0051 revert). Stats +344 / -1, 4 files. `patch-health.sh --flavor ask` → Pass=63 Fail=0. `bin/ci-setup-kernel.sh` glob+rename+`ASK_PATCH_COUNT=51` updated. `kernel/flavors/ask/patches/README.md` documents the patch.
2. ✅ **DONE (prior session)** — `ask_pcd_install_hook()` rewritten in `kernel/flavors/ask/oot-modules/ask/ask_hw.c` to call `lookup_port_scheme` + `graft_cc` instead of `scheme_create` + `bind_port`. Dead `ask_hw_kg_params_fill()` static removed.
3. ✅ **DONE** — `fman_pcd_kg_lookup_port_scheme(pcd, hwport_id, &sid, &base_fqid)` is wired into 0065 (added by the patch alongside `graft_cc` / `ungraft_cc` — the archived 0042 helper that `ask_hw_port_bind` previously used was archived along with the rest of 0042; 0065 supplies the fresh ABI surface).
4. **PENDING HARDWARE** — Re-run harness: expect `kgse_ccbs` non-zero on schemes 3 + 4 (the kernel's schemes), `kgse_spc` on schemes 3 + 4 still climbing (KG still dispatches), schemes 5–9 ABSENT from regdump (ASK no longer creates schemes), kernel-net CPU collapses, CC tree consults happen. User-driven: `bin/local-build.sh ask` → deploy to DUT 192.168.1.190 → `bash bin/m2-dut-prep.sh && bash bin/verify-ask-flow-offload.sh`.

### Items that survive verification this session

- ✅ harness mpstat parser is correct (fix from prior session held — sums sys+irq+soft)
- ✅ ask_flow_insert / ask_hw_flow_insert / cc_node_add_key install path is healthy: 10 hw_ids 0x01..0x0a installed, 60 dedup hits, 70 echo-skips, ZERO error paths
- ✅ dedup classification is correct: 70 REPLACE callbacks / ~6 unique cookies / each cookie 12× delivery (6 per direction) reconciles cleanly with iperf3 -P 8 + flowtable churn
- ✅ MURAM accounting: avail=70400 B post Path A install (no -ENOMEM mid-burst)
- ❌ `fmbm_rfrc` reads 0 even though KG counts 19M packets — confirmed register offset bug in regdump tool, **tangential**; kernel KGSE_SPC tells the truth

---

## 0. Where we are right now (2026-05-25 evening, post Tier-1 execution) — SUPERSEDED BY §0.1

| Surface | State | Evidence |
|---|---|---|
| Path A boot-time PCD install | ✅ working | dmesg `install_now: claimed=5 declined=0 failed=0`; banners precede `register_netdev` |
| MURAM accounting | ✅ instrumented + verdict baked-in | patches 0062 (free bogus 64 KiB), 0063 (gen_pool CP1–CP4), 0064 (log wording + postinstall budget verdict) — `avail=70400 B` post-Path-A, hypothesis (a) `POOL-ALREADY-TIGHT` confirmed |
| Per-flow `chain_create` -ENOMEM | ✅ no longer reproduces | 0 failures in the 2026-05-25 run vs. 327 in 2026-05-24 |
| `bin/verify-ask-flow-offload.sh` CPU parser | ✅ **fixed this session** | Tier-1.1 landed: now sums `%sys+%irq+%soft` (was excluding `%irq`). Real CPU now visible. The "sysstat 12.x column drift" hypothesis was **wrong** — column layout was correct; the bug was the metric formula missing `%irq` |
| M2 throughput gate (≥ 2 Gbps) | ✅ pass | 6.853 Gbps measured (post-parser-fix run) |
| M2 hard gate CPU (≤ 5 %) | ❌ **FAIL** | **37.57 %** kernel-net CPU (sys=0.71% irq=0.00% soft=37.11%). Silicon not engaged in forwarding path |
| Silicon programming (`add_key` call site) | ✅ wired & succeeds | dmesg `ask: flow: hw_insert OK cookie=X oif=N hw_id=0xNN` — 10 hw_ids 0x01..0x0a installed at T=7052s. Tier-2.1 hypothesis (missing call site) **DISPROVEN** |
| FMan packet ingress (`fmbm_rfrc`) | ❌ **= 0 on eth3 + eth4** | Smoking gun: BMI Rx Frame Counter at `0x090204` (eth3) and `0x091204` (eth4) = 0 during/after iperf3 — **zero packets reach FMan post-parser dispatch**. Traffic bypasses FMan entirely |
| Dedup logic correctness | ❌ suspicious | Second iperf3 burst at T=7082s shows EVERY REPLACE skipped with `egress-side echo` message; either stale CC keys from T=7052 not cleaned up, or dedup misclassifies all arrivals |
| DUT post-test stability | ✅ no hang this run | 30 s iperf3 + immediate regdump + 10 min interactive shell — no unresponsive period |
| 10-cycle stress | ⏸ blocked | pointless until `fmbm_rfrc` actually counts |
| v1.1 scope (IPsec OH-port, ask-vpp-promote) | ⏸ deferred | per spec v1.3 §3.4, not in v1.0 GA |

**Single-sentence verdict (revised):** The harness is now honest and tells us the truth:
silicon is being programmed (10 hw_ids installed at T=7052s), but the FMan BMI Rx Frame
Counter on both SFP+ ports stays at 0 during traffic — packets reach the kernel netdev
without ever entering the FMan PCD chain. The bottleneck is upstream of the CC tree,
not in the CC tree. v1.0 GA blocker is now narrowly scoped to the FMan-ingress path.

### New Tier-2 hypotheses (replacing the original §2 content)

The original Tier 2 ("re-introduce missing `fman_pcd_cc_node_add_key()`") is **NO LONGER
RELEVANT** — that path works. The new Tier 2 must investigate why packets don't reach
the FMan post-parser dispatch:

**H1 — DPAA SDK Rx FQ is upstream of KG.** The `dpaa_eth` driver allocates its own
Rx FQ pool and binds it to the BMI Rx FIFO via `fmbm_rfqid` (= 0x6e on eth3, 0x292 on
eth4 per the regdump). The BMI Rx FIFO drains into that default FQ *before* the post-
parser dispatch fires (KG → CC). On read of the regdump, the **default-FQ path is
active** (rfqid populated, rfne/rfpne pointing at HWK = scheme 4), but `fmbm_rfrc` =
0 suggests the BMI Rx FIFO itself sees no frames. This needs the mainline DPAA RX
FQ counter (in `dpaa_eth_rx_napi_poll()`) compared against the on-wire packet count.

**H2 — dedup-on-second-burst false-skip.** dmesg at T=7082s shows every REPLACE
hitting `REPLACE skip egress-side echo` even on cookie variants that should be
fresh. Inspect `ask_flow_offload.c` dedup state — likely the cookie hash table is
still populated from T=7052 (TCP TIME_WAIT keeps connection state in nf_conntrack
for 120 s post-FIN; cookies survive that long). New connections from a fresh
iperf3 SHOULD get fresh cookies, but the dedup might be matching on (src_ip,
dst_ip) tuple ignoring sport so all 8 streams of the second burst collide with
the first burst's stale entries.

**H3 — XGMII MAC → BMI Rx routing.** Frames may be coming in but BMI Rx FIFO is
not the right offset to measure them — they could be reaching the FMan via a
different MAC path. Check `fmbm_rgmcfg` (general MAC config) for the actual MAC
that owns the SFP+ XGMII connection vs. the 10G MAC node we're reading.

Recommended next-session order:
1. Repeat the iperf3 run with a **fresh boot** (`reboot now`, wait, `bash bin/m2-dut-prep.sh`, immediate iperf3) → clean state, no stale dedup.
2. Read `fmbm_rfrc` BEFORE and AFTER the burst — if delta > 0 then H1 is real and we need the SDK driver's Rx FQ counter; if delta still = 0 then H3 is real and we're reading the wrong offset.
3. Add `ethtool -S eth3 | grep dpa_rx_packets` pre/post — if dpaa packets-rx counter advances but `fmbm_rfrc` doesn't, that proves the FMan port is being bypassed at the driver level (H1 confirmed).
**Single-sentence verdict:** Path A is structurally landed, M2 hard gate is met by throughput,
but we cannot tell whether the silicon fast path is engaged or whether we're running an
~7 Gbps SW-only forwarder. Every Tier 1 item below exists to answer that one question.


---

## 1. Tier 1 — Make M2 verifiable (BLOCKING for v1.0 GA)

**Goal:** turn the current "6.947 Gbps, CPU = ¯\\_(ツ)_/¯" result into a defensible
PASS/FAIL against spec §11.1. Three concrete deliverables, all small.

### 1.1 Fix `bin/verify-ask-flow-offload.sh` mpstat parser  ★ critical path

**Problem.** Script's awk parses `mpstat -P ALL 1` assuming sysstat 11.x column layout
(`%usr %nice %sys %iowait %irq %soft %steal %guest %gnice %idle`). sysstat 12.x on
the DUT emits an extra leading `CPU` column shift plus a renamed `%irq`→`%hi`, and the
parser silently reads `0.00` from columns that contain the timestamp.

**Deliverable.** Replace the awk-by-column parser with a header-driven one
(read line 3, build `colname → colidx` map, dereference `%sys` + `%soft` + `%irq`
+ `%idle` by name). ~15 LOC. Add a `--mpstat-debug` flag that dumps the raw header
and the first data row for forensics. Re-run from agent host:

```bash
ssh vyos /tmp/verify-ask-flow-offload.sh --mpstat-debug
```

Cross-check by reading `/proc/stat` deltas in the same script as a second source of
truth (idle/sys/soft jiffies before vs. after the 30 s iperf3). Hard-fail if the
two methods disagree by more than 5 percentage points.

**Acceptance gate.** Re-run gives a real CPU number with the same throughput.

### 1.2 Silicon-engagement audit  ★ critical path

**Problem.** We do not currently know whether REPLACE→`add_key` path is wired all the
way to `fman_pcd_cc_node_add_key()`. The 2026-05-25 run had `chain_create` called
exactly once, which is suspicious — for an iperf3 -P 8 run with 8 distinct 5-tuples
we'd expect 8 cookie REPLACE events. Either (a) cookies arrive after the iperf3 burst
ends so we never measured under load, or (b) `flow_block_cb` REPLACE path early-returns
before reaching the silicon call.

**Deliverable A — code audit.** Re-read `ask_flow_offload.c` REPLACE callback against
spec §3.6 v1.3 + course-correction §2.4.5. Grep:

```bash
grep -rn 'fman_pcd_cc_node_add_key\|fman_pcd_cc_node_remove_key' \
  kernel/flavors/ask/oot-modules/ask/ work/linux-*/drivers/net/ethernet/freescale/dpaa/ask/
```

Trace every call site upward to the entry point. If `add_key` is gated behind a
`#ifdef` or a runtime check that's off, this is the bug.

**Deliverable B — hw-counter probe.** Wrap the iperf3 inside the harness with:

```bash
python3 bin/ask-pcd-regdump.py --history --tag pre  > /tmp/ask-pcd-pre.json
iperf3 ...
python3 bin/ask-pcd-regdump.py --history --tag post > /tmp/ask-pcd-post.json
python3 -c "<delta script>"   # ΔKGSE_SPC, ΔCC_TREE_HITS per scheme
```

Add the regdump invocations to `bin/verify-ask-flow-offload.sh`. Print the deltas at
the end. If CC-tree `pkt_match_count` ≈ flow_count × packets_per_flow → silicon
engaged; if it stays at zero → SW forwarding only, even though throughput is high.

**Deliverable C — synthetic single-flow probe.** Add `bin/verify-ask-flow-single.sh`
that runs **one** TCP iperf3 stream for 60 s with `nft` flowtable enabled and watches
`ask-pcd-regdump.py --history` every second. Single flow → single 5-tuple → at most
one `add_key` → trivial to observe in real time.

**Acceptance gate.** One of two outcomes, both publishable:
- **PASS:** ΔCC_TREE_HITS ≥ 99 % of expected packet count → silicon engaged; M2 stretch is purely a perf-tuning problem.
- **FAIL:** ΔCC_TREE_HITS = 0 → cookie path is not programming silicon; Tier 2.1 becomes the blocker.

### 1.3 Post-iperf3 hang root-cause

**Problem.** DUT became unresponsive ~6 min after iperf3 completed on 2026-05-25.
No panic, no oops, no thermal trip in journal. Auto-recovered after ~14 min.

**Deliverable.** Three cheap probes added to `bin/verify-ask-flow-offload.sh`:

1. **Pre/post `dmesg --since '1 minute ago'`** at the end of the run. Capture into
   the artifact tarball.
2. **Background `tail -F /var/log/kern.log` over ssh** during the entire run + 10 min
   afterward; pipe to an artifact file.
3. **Pre/post `ip -s -s link show eth3` and `eth4`** plus `cat /proc/interrupts |
   grep fman` — any TX-conf FQ leak or QMan portal stuck count should show up here.

If we see it again with these probes in place, the captured state should be
sufficient to triage without needing a second reproduction.

**Acceptance gate.** Either (a) one clean iperf3 + 30-min cool-down without a hang
proves it was a one-off, or (b) the probes catch it in the act and yield a JIRA-style
ticket with a root cause hypothesis.

**Tier 1 deliverable count:** 3 small patches (one harness fix, one harness instrumentation
extension, one diagnostic script). Estimated effort: 1 engineer-day. **Unblocks the v1.0 GA decision.**

---

## 2. Tier 2 — If Tier 1.2 reveals silicon is NOT engaged

This tier is only relevant if **§1.2 Deliverable B** comes back with
`ΔCC_TREE_HITS = 0`. If silicon IS engaged, skip this tier entirely.

### 2.1 Locate the missing `fman_pcd_cc_node_add_key()` call site

**Investigation steps:**
1. `git log --all --grep='add_key' -- kernel/flavors/ask/` → which patch was supposed
   to wire REPLACE → `add_key`?
2. `git show <patch>:ask_flow_offload.c` vs. current tree → was the call site
   removed during a refactor?
3. Compare against `plans/PR14z11-...` design doc — is the cookie indirection
   table holding callbacks that never fire on the silicon side?

### 2.2 Re-introduce the silicon programming call

**Likely fix shape** (working from spec v1.3 §3.6.2 + course-correction §2.4.2):

```c
/* In ask_flow_offload.c REPLACE handler, AFTER constructing chain: */
hw_id = fman_pcd_cc_node_add_key(cc_v4_tcp,
                                  &key_5tuple, &mask_5tuple,
                                  &action_forward_fq_with_manip);
if (hw_id < 0) {
    /* fall back to SW + log; do NOT silently drop */
    pr_warn_ratelimited("ask: cc_add_key failed: %d, flow %u handled in SW\n",
                        hw_id, cookie);
    return 0;  /* SW path is still correct */
}
xa_store(&ask_flow_cookies, cookie, ERR_PTR(hw_id), GFP_ATOMIC);
```

**Acceptance gate.** Re-run §1.2 probe — `ΔCC_TREE_HITS` should match
packet-on-wire count.

### 2.3 Verify Risk #1 (RM §8.7.3.4 inline-MANIP behavior)

Once §2.2 lands and we see CC-tree hits, the next question is whether the
action atom actually performs the L2 rewrite + IPv4 TTL/checksum update + FQ
enqueue, or whether it dispatches to a kernel-RX FQ on first match (which
would show in `pkt_match_count` but still leave the kernel handling forwarding).

**Probe:** `ethtool -S eth3 | grep dpa_rx_packets` before/after. If silicon is
forwarding directly to TX, the kernel RX counter on the ingress port stays flat
while the TX counter on the egress port matches packet-on-wire. Spec says
`FORWARD_FQ_WITH_MANIP` does the full thing; this would confirm or refute.

**Deferred fallback:** if Risk #1 turns out real, restore OH-port indirection
from `archived/0032-0038` as v1.1 contingency (already scoped in
course-correction §6).

**Tier 2 effort:** 2–3 engineer-days IF triggered.

---

## 3. Tier 3 — MURAM headroom optimization

**Goal:** even though hypothesis (a) `POOL-ALREADY-TIGHT` is confirmed
(`avail=70400 B / 393216 B` after Path A boot install), this is only a problem
because some future code path may legitimately need >70 KiB of MURAM for
per-flow chains. Current cookie-callback model uses ~0 B per flow (chain is
built once and re-used), so Tier 3 is **not blocking** for v1.0. It's a
hardening item.

### 3.1 Reduce boot-time CC-tree footprint

**Current cost:** Path A creates `cc_v4_tcp_in` + `cc_v4_udp_in` per port × 5 ports
= 10 CC trees × ~31 KiB each (255-key capacity at `cc_node_capacity` per PR14r)
≈ 310 KiB. That's almost the entire 384 KiB pool.

**Optimization options, ordered by surgical invasiveness:**

| Option | Saved | Risk | Effort |
|---|---:|---|---|
| (a) Drop UDP tree on ports that won't see UDP offload (RJ45 mgmt eth0/eth1/eth2) | ~93 KiB | low — mgmt ports don't offload | 0.5 day |
| (b) Reduce `cc_node_capacity` from 255 → 64 keys | ~225 KiB | medium — need to confirm typical concurrent-flow count on real traffic | 1 day + measurement |
| (c) Shared CC tree across ports (one tree, port-id matched as part of key) | ~250 KiB | high — significant restructuring of `ask_pcd_install()` | 3+ days |

**Recommendation:** land (a) cheaply now as patch 0065, defer (b) until we have
a real flow-count distribution from production traffic, leave (c) on the v1.1
backlog.

### 3.2 Reserve headroom for per-flow chains

Once §3.1 lands, leftover pool should be ~280 KiB instead of 70 KiB. That gives
~350 per-flow chains worth of headroom (≈ 800 B/chain). Document this as the
official "concurrent offloaded flow ceiling" in spec v1.4.

**Acceptance gate.** Repeat the `[VERDICT: ...]` postinstall log line shows
`avail >= 280 KiB → [budget-OK]`.

**Tier 3 effort:** 0.5–1 engineer-day for (a) alone; full sweep 4 days.

---

## 4. Tier 4 — v1.0 GA polish (only after Tiers 1–3 settle)

These are "house in order" items that gate the `tag v1.0` decision but don't
require new architectural thinking.

### 4.1 Update spec to v1.4

- Bake §11.1 perf gate result (whatever Tier 1 reveals) into spec as authoritative.
- Add §16 Risk #13 verdict outcome from patch 0064.
- Document the MURAM headroom number from §3.1.
- Document the cookie-callback REPLACE/DESTROY contract (currently lives only
  in `ASK2-COURSE-CORRECTION.md §5.3` — promote to spec).

### 4.2 Patch stack consolidation

The post-0044 stack has accumulated 8 incremental fixes (0060, 0061, 0062, 0063,
0064 + any from Tier 1–3). Before v1.0 GA, consolidate where it makes sense:

- 0062 + 0063 + 0064 are all MURAM-instrumentation-adjacent — could fold into
  one "fman_pcd: MURAM accounting and verdict tagging" patch.
- 0044 + 0060 are both Path A scaffolding — could fold into one
  "Path A install + retroactive callback API" patch.

Trade-off: bisect granularity vs. patch-stack readability. Recommend keeping
fold to ≤ 2 mergers; don't over-consolidate.

### 4.3 Patch-health weekly run + Qdrant memory consolidation

- Verify `.github/workflows/patch-rot-check.yml` is still green against
  linux-6.18.x weekly snapshots.
- Audit Qdrant for stale ASK 1.x entries (per `archived/AGENTS.md` legacy notes)
  that contradict v1.3 reality; mark obsolete.

### 4.4 INSTALL.md update for ASK flavor

The current `INSTALL.md` is default-flavor-only. Add an "ASK2 flavor" appendix
documenting `gh workflow run "VyOS LS1046A build (self-hosted)" -f flavor=ask`
and what dmesg banners to expect on a healthy boot.

**Tier 4 effort:** 1 engineer-day.

---

## 5. Tier 5 — v1.1 scope (post-GA, do NOT pull into v1.0)

Hard-listed for traceability; out of scope for the "operational + optimized"
v1.0 GA decision.

| Item | Source | Notes |
|---|---|---|
| IPsec OH-port re-inject | spec §13.2 v1.3 | Restore `archived/0032,0034,0036,0038` selectively for IPsec only; L3 fastpath stays on inline MANIP |
| `ask-vpp-promote` oneshot | spec §6 v1.3 | ~600 LOC userspace; transitions a flow from ask.ko HW-offload to VPP AF_XDP when VPP picks up the port |
| Persistent flow stats | spec §11.2 | `node_exporter` textfile collector reading `/sys/kernel/debug/ask/flows.json` |
| Per-flow QoS via MANIP rate-limit | RM §8.7.3.6 | only if a customer actually asks |

---

## 6. Tier 6 — v1.2+ scope (deep future)

For roadmap completeness:

- IPv6 5-tuple offload (M3 in spec)
- `nf_flow_table` bridge HW-offload (M4)
- Multicast / fragmentation offload (M6)
- IPSec SA distribution across multiple CAAM JRs (already partially landed via
  `0001-caam-qi-share`; activate when IPsec OH-port lands)

---

## 7. Recommended next-session execution order

Strictly linear, no parallelism — each step's outcome informs the next:

1. **§1.1** Fix mpstat parser (1–2 hours) → re-run harness, get a real CPU number.
2. **§1.2 Deliverable B** Add regdump pre/post hooks (1 hour) → re-run harness.
3. **Decision point:**
   - If CPU < 5 % AND ΔCC_TREE_HITS ≈ packet-count → **M2 PASSED, jump to §4**.
   - If CPU < 5 % AND ΔCC_TREE_HITS = 0 → impossible, file bug.
   - If CPU > 5 % AND ΔCC_TREE_HITS ≈ packet-count → silicon works but tuning issue; investigate %soft sources.
   - If CPU > 5 % AND ΔCC_TREE_HITS = 0 → **go to Tier 2**.
4. **§1.3** Hang probes (in parallel with above — just instrument the harness).
5. **§3.1(a)** UDP tree drop on mgmt ports — quick win regardless of M2 outcome.
6. **§4** v1.0 GA polish.

**Earliest plausible v1.0 GA tag:** 2026-06-01 if §1.2 returns PASS;
2026-06-15 if Tier 2 is triggered.

---

## 8. Acceptance gates summary

The v1.0 GA tag requires ALL of:

- [ ] `bin/verify-ask-flow-offload.sh` reports real (non-zero, non-bogus) CPU numbers
- [ ] `ΔCC_TREE_HITS` matches packet-on-wire count on a single-flow probe
- [ ] M2 hard gate: throughput ≥ 2 Gbps AND kernel-net CPU ≤ 5 % (spec §11.1)
- [ ] 10-cycle nft flow add/del stress: zero wedge, zero reboot
- [ ] No unexplained post-iperf3 DUT hangs in 3 consecutive runs
- [ ] Patch-health green on linux-6.18.x weekly snapshot
- [ ] `INSTALL.md` covers `flavor=ask` install path
- [ ] Spec v1.4 published with all numeric gates baked in

M2 stretch gate (≥ 7 Gbps + < 5 % CPU) is **nice-to-have**, not blocking. The hard gate
is the GA condition.

---

## 9. References

- `plans/ASK2-COURSE-CORRECTION.md` — Phases 1–5 driver doc; §5.3 has the
  current MURAM-blocker disposition.
- `specs/ask2-modern-architecture-review.md` — Path A architectural rationale.
- `specs/ask2-rewrite-spec.md` v1.3 — current authoritative spec.
- `kernel/flavors/ask/patches/0060-0064` — landed remediation stack.
- `bin/verify-ask-flow-offload.sh`, `bin/m2-dut-prep.sh`, `bin/ask-pcd-regdump.py` —
  M2 harness toolkit.
- Qdrant entries tagged `ASK2`, `path-A`, `MURAM`, `cookie-callback`, `m2-gate`.