# Performance Benchmarks — .185 ↔ .106 (Mono Gateway DK)
**Version 1.1.0** · 2026-08-01 · HADS 1.0.0

> **SUPERSEDED / historical results ledger.** This `.185 ↔ .106` benchmark
> record predates the current harness and the F-198…F-203 datapath work; `.106`
> is no longer an active harness endpoint. For the authoritative, reproducible
> procedure and current results (heidi → DUT `.185` → HELGA, mode-verified SW vs
> HW, MTU battery), use **`plans/ASK2-PERFORMANCE-TEST-HARNESS.md`**. Kept for
> the historical mainline/NXP-ASK/CC-pass-through numbers only.

---

## AI READING INSTRUCTION

Read `[SPEC]` and `[BUG]` blocks for authoritative facts.
Read `[NOTE]` only if additional context is needed.
`[?]` blocks are unverified — treat with lower confidence.
§1–§2 (environment, methodology) are fixed and shared by every dataplane report below. Each dataplane gets its own `##` section (§3 onward) with the same four-part structure (Build, Settings, Results, Notes) so runs are diffable at a glance. Add new dataplanes as new top-level sections; do not restructure existing ones.

---

## 1. TEST ENVIRONMENT (fixed — do not repeat per report)

**[SPEC]**
- Hardware: two identical **Mono Gateway Development Kit** boards, NXP QorIQ LS1046A. `SVR 0x87070010` rev 1.0 on both — confirmed identical silicon, not a speed-bin difference.
  - `.185` = `192.168.1.185`, SSH `vyos@192.168.1.185` (key `~/.ssh/vyos_key` or `~/.ssh/vyos_vanity`).
  - `.106` = `192.168.1.106`, SSH `vyos@192.168.1.106` (same keys). `.106` also hosts the NXP-supplied ASK/CDX production stack for other work — not engaged for these tests unless explicitly stated in a report's §Build.
- Connection: **direct DAC cross-connect, no switch in the path** (confirmed 2026-07-22 — the boards were previously patched through a heidi/Proxmox switch fabric for a different harness (`TRAFFIC-HARNESS.md`), but are now back-to-back SFP+ DACs).
  - `eth3` ↔ `eth3`: subnet `10.99.1.0/24` — `.185`=`10.99.1.185`, `.106`=`10.99.1.106`.
  - `eth4` ↔ `eth4`: subnet `10.99.2.0/24` — `.185`=`10.99.2.185`, `.106`=`10.99.2.106`.
  - Both links: SFP-H10GB-CU1M, 10G.
- Driver on both boards for these tests: `fsl_dpa` (mainline DPAA1 driver). `bus-info: 1a00000.fman` on all four ports.

**[NOTE]**
Do not assume the switch-fabric harness in `TRAFFIC-HARNESS.md` (CT201/CT202 LXCs on heidi) is reachable from `.185`/`.106` — it isn't, as of this cross-connect topology. That harness targets a third board (`mono`, `192.168.1.190`) which was unreachable (no route to host) as of 2026-07-22.

---

## 2. METHODOLOGY (fixed — do not repeat per report)

**[SPEC]**
- Tool: `iperf3 3.12`, present on both boards.
- Server: `iperf3 -s -p 5201 -D` (eth3-side test) and `iperf3 -s -p 5202 -D` (eth4-side test) — unbound to a specific IP, daemonized, persistent across the test session.
- Client: `iperf3 -c <dest-ip> -p <port> -P 4 -t 10` — **4 parallel streams, 10-second duration**, no `-u` (TCP only unless a report says otherwise).
- Each interface tested **independently** (eth3 alone, then eth4 alone — not simultaneously) and **both directions** (`.185→.106` and `.106→.185`), for 4 test runs per dataplane build.
- Before recording final numbers, verify and (if needed) equalize across both boards:
  1. CPU governor + frequency (`cat /sys/devices/system/cpu/cpu*/cpufreq/{scaling_governor,scaling_cur_freq,cpuinfo_max_freq}`).
  2. MTU (`cat /sys/class/net/ethN/mtu`) — target jumbo 9000 unless the report states otherwise.
  3. NIC offloads (`ethtool -k ethN` via full path `/sbin/ethtool`, VyOS's restricted shell doesn't have it on `$PATH`) — match all four ports on every non-`[fixed]` feature.
  4. qdisc quantum (`tc qdisc show dev ethN`) — must reflect the current MTU (`quantum` ≈ MTU+14); if stale from an earlier MTU, reset with `tc qdisc replace dev ethN root mq`.
  5. IRQ/QMan-portal-to-CPU affinity (`cat /proc/interrupts | grep -i portal`) — expect 1:1 portal-to-core pinning, balanced traffic counts.
- `ethtool`/`tc`/`sysctl` all need the full binary path (`/sbin/ethtool`, `/sbin/tc`) when run through VyOS's `vbash` — the bare command isn't on `$PATH` and fails with `Invalid command: [x]`, which is silent/confusing under `2>/dev/null`.

**[NOTE]**
Run-to-run variance of a few hundred Mbit/s (roughly ±10%) between otherwise-identical repeated runs is normal on this link — don't over-interpret single-run deltas smaller than that as real regressions/improvements. Only trust a delta if it survives a rerun.

---

## 3. RESULTS — Mainline Baseline (kernel networking, no offload engine)

### 3.1 Build under test

**[SPEC]**
- Both boards booted image **`2026.07.08-1453-rolling`**, kernel **`6.18.37-vyos`**.
- No VPP, no ASK1 (NXP CDX/FCI/CMM/dpa_app), no ASK2 fast path engaged — plain Linux kernel forwarding/host-stack via the mainline `fsl_dpa` driver on both ends. This is the reference floor other dataplanes get compared against.
- No `isolcpus` in this image's boot `cmdline` on either board — all 4 CPUs equally available to the scheduler. (Contrast: the newer `2026.07.22-*` image carries `isolcpus=3`, reserved for VPP — see §3.4.)
- `HugePages_Total: 0` on both — no hugepages reserved by this image.

### 3.2 Settings applied

**[SPEC]**

| Setting | `.185` | `.106` |
|---|---|---|
| CPU governor | `performance` | `performance` |
| CPU freq (all 4 cores) | 1600000 (1.6GHz, pinned) | 1600000 (1.6GHz, pinned) |
| MTU (eth3/eth4) | 9000 | 9000 |
| rx/tx-checksumming | on | on |
| scatter-gather | on (eth4 required a manual `ethtool -K eth4 sg on` — shipped off by default, see §3.4) | on |
| generic-segmentation-offload | on | on |
| generic-receive-offload | on | on |
| receive-hashing (RSS) | on | on |
| tcp-segmentation-offload | off `[fixed]` — driver/hardware limit | off `[fixed]` |
| hw-tc-offload | off `[fixed]` — driver/hardware limit | off `[fixed]` |
| qdisc | `mq` + `fq_codel`, quantum `9014` (corrected — shipped stale at `1780`, see §3.4) | `mq` + `fq_codel`, quantum `9014` (corrected — shipped stale at `1514`) |
| TCP congestion control | `bbr` | `bbr` |
| `tcp_window_scaling` / `tcp_sack` | on / on | on / on |
| `net.core.rmem_max` / `wmem_max` | 2097152 / 4194304 | 2097152 / 4194304 |
| QMan portal → CPU affinity | 1:1, balanced (portal0→CPU0 … portal3→CPU3) | 1:1, balanced |
| Ring buffers / channels / RSS indirection | not exposed by `fsl_dpa`'s ethtool ops (`netlink error: Operation not supported`) — not tunable on this driver | same |

### 3.3 Test results

**[SPEC]**
Final numbers, after all §3.2 settings applied and matched:

| Interface | Direction | Throughput (receiver) | Retransmits |
|---|---|---|---|
| eth3 | `.185` → `.106` | **7.19 Gbit/s** | 144 |
| eth3 | `.106` → `.185` | **7.50 Gbit/s** | 0 |
| eth4 | `.185` → `.106` | **6.30 Gbit/s** | 0 |
| eth4 | `.106` → `.185` | **6.65 Gbit/s** | 0 |

Progression during tuning (same build, same test, showing the effect of each fix — useful reference for what's worth checking on future dataplanes):

| Stage | eth3 `.185→.106` | eth3 `.106→.185` | eth4 `.185→.106` | eth4 `.106→.185` |
|---|---|---|---|---|
| Cold-boot, jumbo MTU already set, default offloads/qdisc | 7.58 Gbit/s | 7.09 Gbit/s | 5.83 Gbit/s | 7.86 Gbit/s |
| + `eth4` scatter-gather/GSO/GRO enabled on `.185` (was off) | — | — | 6.95 Gbit/s | 6.84 Gbit/s |
| + qdisc quantum corrected to match jumbo MTU (all 4 ports) | 7.19 Gbit/s | 7.50 Gbit/s | 6.30 Gbit/s | 6.65 Gbit/s |

**[NOTE]**
Single-stream iperf3 (no `-P` flag) on this baseline yields ~1.18–1.30 Gbit/s — the kernel host-stack single-core ceiling. Multi-stream (`-P 4`) with RSS distributes across cores and reaches the numbers above. This single-stream floor is the reference for any single-flow offload comparison.

### 3.4 Notes / related findings

**[NOTE]**
- eth4 shipped with `scatter-gather`/`generic-segmentation-offload`/`generic-receive-offload` **off** on `.185` while eth3 and both of `.106`'s ports had them **on** — an inherited leftover from earlier AF_XDP/VPP experimentation on that port, not a hardware difference. Fixed live with `ethtool -K eth4 sg on gso on gro on`; not yet persisted to a boot-time config (will reset on reboot — re-check when repeating this test on a fresh boot).
- `fq_codel`'s `quantum` is computed once when the qdisc attaches to the interface and does **not** auto-update if MTU is changed afterward — both boards were still running quantum sized for an earlier, smaller MTU (`.185`: 1780 ≈ old 1766 MTU epoch from prior AF_XDP work; `.106`: 1514 ≈ standard 1500 MTU) despite the device MTU already reading 9000. `tc qdisc replace dev ethN root mq` forces recalculation from the live MTU.
- eth3 consistently outperforms eth4 by a few hundred Mbit/s to ~1 Gbit/s across repeated runs, in both directions. Not yet root-caused — candidate for investigation if squeezing this baseline further becomes a priority (possible candidates: physical DAC cable/port quality, which QMan portal/CPU each interface's queue lands on, PCB trace/SFP+ cage differences). Low priority relative to the offload-engine comparisons this report exists to support.
- **Separate, more significant finding on CPU clocking — does not affect this report's numbers, but will affect the next one:** the newer `2026.07.22-*` image (kernel `6.18.38-vyos`, the one carrying this project's own U-Boot rather than NXP's stock one) has `.185`'s CPU permanently capped at 700MHz instead of 1.6GHz — an 2.3x deficit that alone would explain sub-1Gbit/s results if that image is used for a future dataplane report without the fix below. Root cause and fix are in `BOOT-PROCESS.md` §9 and `kernel/common/patches/board/4010-clk-qoriq-ls1046a-cmux-full-range.patch` (committed `e58d8e2`, not yet deployed as of this report). **Before benchmarking any dataplane on a `2026.07.xx`-or-later image, confirm `cpuinfo_max_freq` reads `1600000`, not `700000`, per §2 step 1 — otherwise the numbers are measuring the CPU bug, not the dataplane.**
- VPP confirmed fully absent on `.185` during this test: no process, `vpp.service` disabled/inactive, no config block, no XDP programs attached to any interface, no `defunct_*` interfaces, zero hugepages reserved, no leftover BPF objects on disk.

---

## 4. RESULTS — NXP ASK

### 4.1 Build under test

**[SPEC]**
- Both boards booted image **`2026.07.02-2130-rolling`**, kernel **`6.12.49-vyos`** (NXP SDK-era kernel, distinct from the mainline `6.18.x` line used in §3).
- `ask-check` confirms the full NXP ASK offload stack loaded and operational on both boards: `sdk_fman`/`sdk_dpaa`/`sdk_qbman` bound, BMan/QMan bound, FMan microcode v210.10.1 + PCD initialised, `cdx.ko`/`fci.ko`/`auto_bridge.ko` loaded, `cmm` (`ls1046a-ask.service`) and `dpa_app` running, `cdx_ctrl_timer` kernel thread active. Summary both boards: **39/41 OK, 2 WARN (eth1/eth2 down — unused ports, expected), 0 FAIL**.

**[NOTE] — methodology caveat, read before trusting these numbers as "ASK offload throughput"**
The NXP ASK/CDX fast path (`cdx.ko` + FMan PCD hash-table classification) is a **forwarding/transit accelerator**: it hardware-classifies and re-routes packets *between ports* without CPU involvement. It is not in play for locally-terminated traffic — i.e. an `iperf3` client/server socket running on the same box whose NIC received the packet. This §4 test is `.185`↔`.106` **host-to-host**, exactly like §3's mainline baseline, so it is *not* exercising CDX's forwarding acceleration at all. What it actually measures is the older 6.12.49 SDK kernel/driver's plain host-stack performance, plus whatever background CPU tax `cmm`/`cdx_ctrl_timer`/`dpa_app` impose just by being resident. To measure the actual offload benefit, a third host must drive traffic *through* `.185` or `.106` as a forwarding router (the `TRAFFIC-HARNESS.md` CT201/CT202 pattern, or equivalent) — that is a separate test to run later, not this one.

### 4.2 Settings applied

**[SPEC]**
Starting state on load was *not* pre-tuned — every item below needed active correction to reach parity with the §2 methodology checklist:

| Setting | `.185` as loaded | `.106` as loaded | Action taken |
|---|---|---|---|
| CPU governor | `conservative` | `performance` | Set `.185` to `performance` via `scaling_governor` |
| CPU freq | 800–1400MHz (unpinned) | 1600000 (pinned) | `.185` now 1600000 pinned, both boards matched |
| MTU (eth3/eth4) | 1766 | 1500 | Both raised to 9000; `ask-check` re-run and confirmed still OPERATIONAL post-change |
| scatter-gather / GSO / GRO | eth3 on, **eth4 off** | on, on | `.185` eth4 fixed live (`ethtool -K eth4 sg on gso on gro on`) — same leftover-AF_XDP pattern as §3, recurring because it's not yet persisted anywhere |
| rx-checksumming | `off [requested on]`, all 4 ports both boards | same | **Could not be changed** — `ethtool -K rx on` rejected on every port ("Could not change any device features"). Genuine driver limitation of this SDK build, not an asymmetry — differs from §3's mainline driver, where it was `on [fixed]`. |
| receive-hashing (RSS) | `off [fixed]` | `off [fixed]` | Not available on this SDK driver at all (mainline baseline had it `on`) — noted, not fixable |
| qdisc quantum | 1780 (matched then-current 1766 MTU) | 1514 (matched then-current 1500 MTU) | Reset via `tc qdisc replace dev ethN root mq` *after* the MTU change, both boards now `9014` |
| `isolcpus` | none | none | No action needed |
| QMan portal → CPU affinity | 1:1, balanced | 1:1, balanced | No action needed |

### 4.3 Test results

**[SPEC]**

| Interface | Direction | Throughput (receiver) | Retransmits |
|---|---|---|---|
| eth3 | `.185` → `.106` | **2.72 Gbit/s** | 4 |
| eth3 | `.106` → `.185` | **3.40 Gbit/s** | 0 |
| eth4 | `.185` → `.106` | **2.60 Gbit/s** | 1 |
| eth4 | `.106` → `.185` | **3.50 Gbit/s** | 0 |

### 4.4 Notes / related findings

**[NOTE]**
- All four legs land well below the §3 mainline baseline (2.6–3.5 Gbit/s here vs 6.3–7.5 Gbit/s in §3), despite CPU/MTU/offloads/qdisc all matched per the checklist. Given the §4.1 caveat, this is consistent with the older 6.12.49 SDK kernel/driver simply being slower at host-terminated socket I/O than the mainline 6.18.x driver — not a reflection of ASK's forwarding-offload capability, which this test doesn't exercise.
- Consistent directional asymmetry on **both** interfaces: `.185→.106` is ~20–25% slower than `.106→.185` (2.72 vs 3.40 on eth3; 2.60 vs 3.50 on eth4). Not yet root-caused. Candidates worth checking next: per-board CPU time consumed by `cmm`/`cdx_ctrl_timer`/`dpa_app` during the test (not measured this round), and whether `rx-checksumming` being stuck off pushes checksum cost onto a particular direction's CPU asymmetrically.
- The same eth4-scatter-gather-off pattern recurred on `.185` independently of §3's finding (this is a different boot image) — worth checking whether something in the general `.185` provisioning/first-boot path disables SG on eth4 specifically, rather than treating it as a one-off leftover each time.

**[BUG] Directional throughput asymmetry — receive-side single-core bottleneck (RSS unavailable)**
- Symptom: `.185→.106` consistently ~20–25% slower than `.106→.185` on both interfaces (2.72–2.87 vs 3.40–3.42 Gbit/s on eth3, similarly on eth4), despite identical settings on both ends.
- Cause: confirmed via live `mpstat -P ALL 1` capture on both ends during matched test runs. The **sender is always near-idle**; the **receiver** funnels all RX softirq work (NAPI polling + software TCP checksum, since `rx-checksumming` is also stuck off — see §4.2) onto a **single CPU core**, because `receive-hashing` (RSS) is `off [fixed]` on this SDK driver — there is no per-flow steering across the 4 available QMan portals for a single test's traffic, even though all 4 portals are architecturally capable and IRQ-pinned 1:1 to a CPU each (confirmed via `/proc/interrupts`, cumulative counts non-trivial on all 4 portals across this session). `.106`'s receiving core (CPU2) hit **100% softirq, fully saturated**, while `.185`'s receiving core (CPU3) averaged **69% softirq with real headroom** under the equivalent load — that saturation gap directly explains why `.106`-receiving throughput is lower than `.185`-receiving.
- Fix: none available at the config level — this is a driver capability gap (missing RSS/multi-queue RX steering in the NXP SDK 6.12.49 `fsl_dpa` driver), not a misconfiguration. §3's mainline driver has `receive-hashing: on` and correspondingly much higher, more core-balanced throughput. Not something to chase further within this ASK build; relevant context for interpreting §7's cross-build comparison.

---

## 5. RESULTS — VPP / AF_XDP (pending)

**[?]** Not yet run against this methodology. Prior VPP AF_XDP work on `.185` is documented in `ASK2-MASTER-PLAN.md` and `VPP-AFXDP-ZC-FULLSPEED.md` but wasn't measured with this iperf3 protocol (4 streams, both directions, both interfaces, matched offloads/qdisc). Note `isolcpus=3` is present on VPP-capable images — account for its effect on which core the iperf3/test process can run on relative to QMan portal 3's pinned IRQ (see §3.1 note).

---

## 6. RESULTS — ASK2 (pending)

**[?]** Not yet run. ASK2 is mid-development on the `dpaa1` branch (see `ASK2-MASTER-PLAN.md`) — benchmark once a milestone gate is reached, not mid-debug.

---

## 7. VERIFIED OFFLOAD RESULTS — SHIPPING PATH

**[SPEC]**
These are the canonical, verified throughput numbers for the offload datapaths that actually work. All measured on the dual-board DAC cross-connect harness (§1), iperf3 methodology (§2), MTU 9000, P4 streams, 10-second duration.

### 7.1 CC Pass-Through (M2) — 2026-07-07

**[SPEC]**
- Build: kernel `6.18.37-vyos`, ASK2 M2 gate. FMan PCD CC tree configured as pass-through (no classification — all frames forwarded via CC group table default action).
- Datapath: FMan hardware forwarding, no CPU involvement in the fast path.
- Result: **7.37 Gbit/s** @ **0.16% CPU**.
- Significance: first verified FMan hardware forwarding throughput on this kernel. Proves the CC tree infrastructure (scheme, group table, default action) is functional and the FMan can sustain near-wire-rate forwarding without CPU.

### 7.2 CC-Tree + Kernel nf_flowtable (M5) — 2026-07-24

**[SPEC]**
- Build: kernel `6.18.38-vyos`, ASK2 M5 gate. FMan PCD CC tree classifies flows; kernel `nf_flowtable` software fast-path forwards matched flows.
- Datapath: FMan hardware classification → kernel software flowtable forwarding (no FE-VM ehash HIT path — see §8).
- Result: **10.259 Gbit/s** @ **0.16% CPU**.
- Significance: line-rate 10G forwarding achieved. The combination of hardware classification + kernel software flowtable forwarding saturates the 10G SFP+ link. This is the current shipping-path ceiling.

### 7.3 NXP cdx.ko (Vendor Reference) — 2026-07-02

**[SPEC]**
- Build: kernel `6.12.49-vyos`, NXP ASK 1.x production stack. `cdx.ko` opcode/manip chain forwarding.
- Datapath: FMan PCD hash-table classification → CDX opcode chain → hardware forwarding between ports.
- Result: **8.58 Gbit/s** (forwarding-mode, traffic transiting the board between two external endpoints).
- Significance: vendor reference for FMan hardware forwarding throughput. Establishes the silicon ceiling for PCD-based forwarding on this platform.

### 7.4 Summary Matrix

| Offload Path | Date | Kernel | Throughput | CPU | Notes |
|---|---|---|---|---|---|
| CC Pass-Through (M2) | 2026-07-07 | 6.18.37-vyos | **7.37 Gbit/s** | 0.16% | FMan hardware forwarding, no CPU |
| "CC-Tree + nf_flowtable" (M5) | 2026-07-24 | 6.18.38-vyos | **10.259 Gbit/s** | 0.16% | ⚠ mechanism unresolved (2026-08-04): most likely kernel `nf_flowtable` SW forwarding; no HW classification confirmed |
| NXP cdx.ko | 2026-07-02 | 6.12.49-vyos | **8.58 Gbit/s** | — | Vendor reference; external-hash classification + opcode/manip chain (F-163) |

---

## 8. FE-VM EHASH HIT PATH — retired 08-01, UN-RETIRED 08-05 (F-163), re-validation pending

**[SPEC — updated 2026-08-05]**
The FE-VM ehash HIT path (FMan Frame Extension → Virtual Machine → exact-match hash table lookup → hardware forward on HIT) **has never produced a working HIT on silicon** — but the 08-01 "architecturally retired" verdict is reversed: F-163 (2026-08-05) established the deployed vendor `cdx.ko`'s production classification **is** external-hash, and F-165 (2026-08-05) showed every prior arm test pointed the port at an empty scaffold, so the corrected chain (14-byte PORT_ID key, `EKFC 0x801C0006`) has never been genuinely exercised. All prior throughput claims associated with this path are **false positives** or **projections**, not measured results. Re-validation = `plans/ASK2-MASTER-PLAN.md` T-M3-R.

**[NOTE]**
- The ~1.5 Gbit/s DDR ceiling was a **projection** based on DDR bandwidth estimates for the ehash lookup path, not a measured throughput number. The path never reached a state where a real iperf3 measurement could be taken. *(Still true 2026-08-05 — and still unmeasured against the vendor's real external-hash traffic.)*
- M3/M5 "HIT gate PASSED" throughput claims were **false positives**: the test harness was measuring kernel software forwarding (the fallback path), not FE-VM ehash HIT. The HIT gate never actually fired on silicon.
- ~~The FE-VM ehash path is architecturally retired from the ASK2 shipping path. The CC-tree + kernel nf_flowtable combination (§7.2) achieves line-rate 10G without it.~~ **(2026-08-05: un-retired — F-163. And §7.2's own mechanism is unresolved: most likely kernel `nf_flowtable` SW forwarding, no HW classification confirmed — qdrant tag `no-confirmed-hw-hit-ever`.)** Any validation must start cold-boot from scratch (silicon-experiment rule).
- See `specs/fman-keygen-flow-key-spec.md` §13 for the ranked failure-candidate list and `plans/ASK2-MASTER-PLAN.md` for the re-litigated architecture status.

---

## 9. CROSS-BUILD COMPARISON

**[SPEC]**

| Dataplane | Kernel | eth3 avg (both dir.) | eth4 avg (both dir.) | Notes |
|---|---|---|---|---|
| Mainline Baseline (§3) | 6.18.37-vyos | 7.35 Gbit/s | 6.48 Gbit/s | Host-to-host, no offload engine engaged |
| NXP ASK (§4) | 6.12.49-vyos | 3.06 Gbit/s | 3.05 Gbit/s | Host-to-host — **does not exercise CDX forwarding offload**, see §4.1 caveat. Not a fair comparison to §3 for "offload value"; only valid as a same-methodology host-stack comparison. |
| CC Pass-Through (§7.1) | 6.18.37-vyos | **7.37 Gbit/s** | — | FMan hardware forwarding, 0.16% CPU |
| "CC-Tree + nf_flowtable" (§7.2) | 6.18.38-vyos | **10.259 Gbit/s** | — | ⚠ mechanism unresolved — most likely kernel `nf_flowtable`, no HW classification confirmed (2026-08-04) |
| NXP cdx.ko (§7.3) | 6.12.49-vyos | **8.58 Gbit/s** | — | Forwarding-mode, vendor reference |
| VPP / AF_XDP (§5) | — | pending | pending | |
| ASK2 (§6) | — | pending | pending | |
| FE-VM ehash HIT (§8) | — | **no HIT ever** | — | Never produced a working HIT; prior claims were false positives; un-retired 2026-08-05 (F-163), retest pending (F-165/T-M3-R) |

**[?]** A true ASK-offload-vs-baseline comparison requires a forwarding-mode test (traffic transiting `.185`/`.106` between two other endpoints) — not yet run for the CC-tree paths. The CC Pass-Through and CC-Tree + nf_flowtable numbers above are host-to-host (like §3/§4); the cdx.ko number is forwarding-mode. Add a §10 "Forwarding-mode results" section when that harness is available for the CC-tree paths, rather than conflating it with the host-to-host numbers above.