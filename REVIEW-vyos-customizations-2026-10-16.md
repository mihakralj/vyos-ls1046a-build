# VyOS Customizations Review — code, performance, security, optimization opportunities

Date: 2026-10-16. Scope: read-only review, no code changed.
Repo state: branch with 43× `data/vyos-1x-*.patch` (~3650 lines total), 4× `data/vyos-build-*.patch`,
7 live-build hooks, `data/vyos-1x-files/` (op-mode + MCP server sources), `board/` scripts+systemd,
`kernel/ask/oot-modules/ask/` (~11k LOC, 14 .c + headers + 7 kunit files ~3.4k LOC),
`kernel/common/patches/board/` (98 patches, ~26.7k lines), `bin/kernel-fixups/` (55× F_*.py).

Largest vyos-1x patches: `006-install-image-reserve-gap` (307), `054-offload-drop-vlan-pppoe-leaves` (234),
`023-no-console-restart-if-unchanged` (182), `024-hw-offload-vlan-strip` (179),
`010-vpp-platform-bus` (178), `031-offload-ask-cli` (157), `041-mcp-server` (145).
Largest board patches: `0122-fman-pcd-fe-vm-chain` (2662), `0173-fman-pcd-fixup-fold` (2420),
`0169-fman-pcd-fe-obs-canary` (2343). Largest ask.ko files: `ask_flow_offload.c` (4399),
`ask_hw.c` (2239), `ask_genl.c` (900), `ask_bridge.c` (840).

## 1. Overall assessment

The customization set is unusually disciplined for a board-support + silicon-bring-up project:
per-file header comments carry dates, board IDs, root-cause narratives, and supersede-chains
(F-203→F-222, Fork-A removal, single-image collapse); fail-closed behavior is the norm
(`-EOPNOTSUPP`/`-EAGAIN` SW fallback, ConfigError on helper failure); concurrency is explicitly
documented (RCU/rhashtable in `ask_flow.c`, atomic-notifier deferral in `ask_neigh.c`,
`rtnl → ask_bridge_lock → h->lock → fe_lock` order in `ask_bridge.c`, CR-012 rtnl analysis in
`ask_hw.c`). Nothing below is a ship-stopper. Findings are ordered by value/effort.

## 2. ASK2 implementation (`kernel/ask/oot-modules/ask/`)

### 2.1 Code quality — strengths to preserve
- `ask_flow.c` (956): textbook RCU + rhashtable + `u64_stats_sync` + `call_rcu` free. Keep as reference.
- `ask_neigh.c` (285): bounded (1024) + coalesced event queue with counters — the right answer to
  notifier storms. Reuse this pattern elsewhere (see O-3).
- `ask_debugfs.c` (148): fully compiled out without `CONFIG_NET_ASK_DEBUG_FS`; Kconfig help
  explicitly marks it non-production. Good.
- `ask_fe_flow_insert()` (~2119–2290 in `ask_flow_offload.c`): validates key/fman/enq_off,
  re-checks NAT gates at publish time (fail-closed on param race), documents VLAN/MTU/RFC-8200
  trade-offs inline. This is the model for other publish points.

### 2.2 Findings

**A-1. `ask_flow_offload.c` REPLACE path is very large and multi-responsibility (perf + review cost).**
Single file 4399 lines; `ask_flow_offload_replace()` spans parse-match, parse-action, VLAN/PPPoE/NAT
gating, neigh resolve, pending-queue, FE insert. ~60 `pr_info_ratelimited` call sites in one file.
Recommend: split into `ask_parse.c` (match/action), `ask_resolve.c` (neigh/owning-dev/IIF),
`ask_pending.c` (queue + poll worker); keep `ask_flow_offload.c` as thin dispatch. No behavior
change; reduces rebase conflicts and makes the per-packet vs control-plane split auditable.
Effort: medium. Value: maintainability, faster reviews.

**A-2. Logging volume on control-plane paths.**
Dozens of `pr_info_ratelimited` in REPLACE/DESTROY/pending-poll/neigh-rebuild.
Ratelimit still emits under churn (ARP scan, conntrack churn) and several carry MACs/IPs
(`%pM`, `%pI4`) at INFO. Recommend: demote steady-state successes to `pr_debug`/`trace_printk`
(`ask_trace.h` already exists for this), keep failures/warnings; scrub or hash PII-ish fields
at INFO. Effort: small. Value: log noise, dmesg pressure, minor privacy.

**A-3. Duplicate port-resolver shims in two translation units.**
`ask_flow_offload.c:89-115` and `ask_hw.c:71-90` each re-declare
`dpaa_get_rx_fman_port` / `fman_port_get_id` (+ `fman_bind`, `dpaa_get_tx_fqid` in ask_hw.c)
with a "mirrors the identical shim" comment. Recommend: single `ask_port_resolve.c` exporting
`ask_dpaa_get_fman_port_id()` (+ VLAN-vif → real-dev handling now only in one copy).
Effort: small. Value: drift risk (the VLAN fix exists in only one copy today).

**A-4. `ports[64]` / `ask_hw_port_family[64]` indexed by raw port id without a bounds assert at use.**
`ask_vlan_cc.c:35` (`ports[64]`), `ask_hw.c:109` (`ask_hw_port_family[64]`). Port ids are u8
(debugfs caps at 0xff; `port_id_dec` allows full range). Recommend: `#define ASK_MAX_HW_PORT 64`
+ `BUILD_BUG_ON`/`WARN_ON_ONCE(port >= ASK_MAX...)` at each index site, or size by 256 like
`ask_hw_port_l2`. Effort: trivial. Value: robustness.

**A-5. `ask_debugfs.c` sscanf parsing is hand-rolled.**
`l2add/l2del/engage` via overlapping `sscanf` + `mac_pton` + range checks. Correct but fragile
(`%15s` vs `IFNAMSIZ`, hex `%x` without `0x` enforcement). Since this surface is already
compile-gated out of production, recommend: no hardening needed, but add a comment pointing
future verbs at the genl path instead of extending sscanf. Effort: trivial.

**A-6. Kunit coverage is good but uneven.**
~3352 lines of tests (`ask_test_flow`, `ask_test_genl`, `ask_test_hw_pcd`, `ask_test_flow_offload`,
`ask_test_bridge`) — strong for table/genl. The highest-risk logic (REPLACE parser branches for
VLAN/PPPoE/NAT/dual-lane key build `ask_fe_build_key_dual`, stale-MAC rebuild) deserves
table-driven unit tests with golden byte-vectors for each key variant (v4/v6/VLAN/PPPoE),
since key-layout bugs are silent misforwards. Effort: medium. Value: regression safety.

## 3. Performance opportunities

**P-1. REPLACE does synchronous neigh + route resolution inline; pending-queue + 100 ms poll is the backstop.**
`ask_resolve_neigh_v4/v6`, `ask_resolve_owning_dev`, generation/dedup logic all run in the
`FLOW_CLS_REPLACE` call path. Under SYN-flood/ARP-churn this serializes flow setup and the
`ASK_FLOW_PENDING_MAX 4096` + poll worker (`ask_flow_pending_poll_fn`, 100 ms) becomes the
throughput limiter. Recommend: (a) measure REPLACE latency percentiles under load;
(b) consider batching/dedup earlier (cookie generation already exists — extend first-arrival
latch coverage); (c) make poll interval adaptive (back off when idle, tighten under pending
pressure) instead of fixed 100 ms. Effort: medium. Value: flow-setup pps.

**P-2. Stats path already RCU-safe (`ask_flow_offload_stats_cb` under `rcu_read_lock`, xa_load +
atomic64 only) — keep it that way.** Any future per-flow counter addition must not allocate
(`ask_port_stats_add` GFP_KERNEL note at `ask_flow_offload.c:3836` is correctly outside the
locked region). Recommend: add a `/* NO-ALLOC under rcu_read_lock */` comment convention or a
sparse/lockdep assertion if feasible. Effort: trivial.

**P-3. `pr_info_ratelimited` in REPLACE/DESTROY success paths costs under load.**
Format-string + MAC/IP formatting on every flow setup/teardown even when ratelimited output is
suppressed (arguments are still evaluated). Demoting to `pr_debug` (compiled out or dynamic)
removes this from the hot control path. Effort: small (same change as A-2).

**P-4. WireGuard `tx_lanes` (board patch 4016) defaults to 1 = stock behavior; validated uplift unknown.**
Single tunnel ~1.2 Gbit/s CPU-bound on one lane. The knob exists (`wireguard.tx_lanes` 1–4) but
board validation needs a CI image. Recommend: schedule the lane-scaling measurement
(1/2/3/4 lanes × 1/2/4 tunnels) and record in the plan; if validated, consider default 2–3 on
4-core LS1046A. Effort: test time. Value: potentially +30–50% WG throughput.

**P-5. DPAA RX buffer order is settled (F-222 reverted F-203 order-1 → order-0) — do not revisit
without a fragmentation-aware allocator.** The AGENTS.md correction record documents the
`dev_alloc_pages()` failure + RX-deaf wedge at ~800 Mbit/s. Any future MTU/jumb work must keep
single-buffer contiguity (FE path validated only on it) AND order-0 allocation. Effort: none
(guard). Value: avoid re-introducing a known wedge.

**P-6. VPP AF_XDP path pins `rx_queue_size/tx_queue_size 4096`, single RX queue (patch 010).**
Fine for SFP+ 10G today. If VPP-side throughput stalls, next knobs: queue count vs CPU mapping,
`poll-sleep-usec`, and the custom `xdp_redirect.o` BPF (patch 032) — profile before tuning.
Effort: measurement only for now.

## 4. Security review

**S-1. Generic-netlink permissions: all ops `GENL_UNS_ADMIN_PERM` — verify intent.**
`ask_genl.c:113-171`: every command (including ENGAGE/DISENGAGE/SET_POLICER/FLUSH) allows
unprivileged userspace (`GENL_UNS_ADMIN_PERM` = no `CAP_NET_ADMIN` required). If deliberate
(for vyos-operator UX without root), document it at the family definition and ensure each
doit re-validates port/family ranges (most do: port ≤ 0xff, family mask, policer u32 ranges).
If not deliberate, switch mutating ops to `GENL_ADMIN_PERM`. Effort: small. Value: privilege
boundary clarity. This is the single most important security check in this report.

**S-2. `vyos-offload-ask` runs `echo ... > /sys/kernel/debug/fman_pcd/...` as root via config commit.**
`ethernet.py:set_ask_offload()` shells to `/usr/local/bin/vyos-offload-ask` with a hardcoded
port map (`eth0:0x0c … eth4:0x11`). Findings: (a) `set -e` WITHOUT `-u`/`pipefail` (see S-5);
(b) debugfs control writes from a commit path bypass the "genl-only production surface" intent —
production `engage/disengage/status` already use YNL/genl, but `hit-engage/flow-add/flow-del`
verbs still write debugfs nodes; ensure the commit path can never reach those verbs (it passes
only `family <mask>` today — confirm by code: `VERB` dispatch allows `flow-add <key_hex>`
with unvalidated hex length → oversized `echo` to `fe_flow`; restrict to an allowlist when
invoked non-interactively, or split the script into `vyos-offload-ask` (genl only) and a
separate `ask-hit-test` dev tool). (c) `port_id_dec` uses `$((PORT))` arithmetic — safe, but
validate `--port` against the five known ports instead of any 0x00–0xff. Effort: small–medium.

**S-3. `pcd-snapshot` opens `/dev/mem` O_RDWR|O_SYNC + PROT_WRITE mmap.**
Read-only intent but writable mapping; runs as root, gated by a root check. Recommend:
`O_RDONLY` + `PROT_READ` (it only captures/diffs state), which also makes accidental writes
impossible. Effort: trivial. Value: least privilege.

**S-4. MCP server (`041` + `data/vyos-1x-files/src/services/api/mcp/`): dormant-by-default, mode-aware —
good design; verify three things.** (a) `read-only` must hide write tools server-side (patch
header claims enforcement, not advertisement — re-verify after each upstream rebase since this
is a pure-context patch). (b) `troubleshoot_internet_connectivity` / `analyze_security_posture`
prompts must not leak config values into logs beyond the existing redaction (`redaction.py`
exists — confirm all new tools route through it). (c) nginx proxy prefix `/mcp/` — confirm auth
middleware applies equally to the new mount (no unauthenticated bypass via trailing-slash or
prefix mismatch). Effort: small audit. Value: new network-reachable surface.

**S-5. Shell hygiene: `set -e` without `-u`/`-o pipefail` in board scripts and most `bin/*.sh`.**
`vyos-offload-ask` (`set -e`), `ask-check` (`set -u` only), `pcd-snapshot`/`fan-pid` (no set flags
— python, fine). `bin/*.sh` audit: ~20 scripts lack `set -euo pipefail` (list in evidence).
Unset-variable and swallowed-pipeline failures are the classic silent-misconfig class.
Recommend: add `set -euo pipefail` (+ `IFS=$'\n\t'` where word-splitting matters) to every
`bin/*.sh` and `board/scripts/*` shell file; shellcheck in CI (`patch-rot-check.yml` pattern).
Effort: small, mechanical. Value: fail-closed builds and commits.

**S-6. `show_ask_offload.py`: command construction + exception scope.**
`rc_cmd(f'{YNL} --family {SPEC_FAMILY} --dump dump-flows --output-json')` — constants only,
not injectable (interface name never enters the shell; only used for sysfs/ifindex compare).
Good. Minor: bare `except RuntimeError` in `main` + `int(off)` without try (malformed ynl
output → unhandled `ValueError` traceback to operator). Wrap parse in try/except and print a
clean error. Effort: trivial.

**S-7. Conntrack `notrack`/`untracked-accept` rules (056/057) widen the untracked surface.**
WireGuard UDP ports + `ct state untracked accept` in PREROUTING/OUTPUT/HELPER chains.
Intended (avoid conntrack for offloaded/WG traffic), but any `notrack` rule is a firewall-bypass
primitive if the port set is wrong. Recommend: (a) assert `wireguard_ports` is non-empty and
sourced from live config (empty → rule omitted, already templated); (b) add a comment noting
that firewall rules relying on conntrack state will not see notracked packets; (c) verify
`057`'s dependency addition (`interfaces_wireguard → conntrack`) forces correct ordering.
Effort: small (mostly documentation + one test).

**S-8. Module parameters as a commit path (`055`: `write_file(ask_offload_delay, ...)`).**
Writing `/sys/module/ask/parameters/offload_delay_ms` from `firewall.py:apply()` is a global,
non-atomic knob set on every firewall commit; concurrent commits could interleave.
Low risk in practice (single config daemon), but prefer a genl attribute over a module param
for per-commit state, or at least validate range in Python before write (XML validator covers
CLI; direct calls to `apply()` bypass it). Effort: small.

## 5. VyOS integration (vyos-1x patches) observations

- **031 (ASK CLI)**: per-family `offload ipv4|ipv6` + VPP mutual exclusion in both directions
  (`interfaces_ethernet.verify_offload` + `vpp.verify`) — correct. Forced-RPS-on-when-ASK-engaged
  (CPU spread 1–3) is a thoughtful 4-core concession; record the interaction with an explicit
  `offload.rps` unset (operator surprise: RPS on despite no knob). Hardcoded `port_map` in
  `ethernet.py` duplicates DT truth — derive from the same resolver ask.ko uses, or generate
  from DT at build time, so a DT renumber cannot silently skip a port (`port is None → return`
  silently does nothing today; at minimum log a Warning).
- **038 (state-policy shadow guard)**: fail-closed commit error with remediation text — exemplary.
  Consider also covering `related` + `invalid` interplay in the message (already partially).
- **052 (hw-tc reapply), 025 (ingress-policer), 024 (VLAN strip), 034 (hide LRO/TSO)**:
  small, hardware-honest (TSO/LRO `[fixed] off` — impossible on DPAA1). No issues found.
- **027 (port order), 047 (resolve-pending-match)**: DT-aware naming; keep the `hw-id` always-wins
  contract and the fresh-install-only scope in mind when touching either.
- **006 (install-image reserve gap, 307 lines, largest vyos-1x patch)**: partition-layout change —
  highest blast radius of the vyos-1x set. Deserves a dedicated re-review on every rebase
  (it touches the installer gap math); ensure eMMC vs USB-live paths both covered by tests.
- **041 (MCP, 145 lines patch + full server tree in vyos-1x-files)**: see S-4. Keep the
  dormant-by-default + mode-aware-auth invariants pinned by tests, not just comments.

## 6. Board/kernel substrate observations

- **98 board patches / ~26.7k lines + 55 fixups + 453-line series with inline root-cause prose**:
  the series file is effectively a lab notebook — valuable. Risk: `series` is now the only index;
  consider generating a machine-readable manifest (patch → subsystem → Risk-Tier) from the
  existing `Upstream-Status`/`Risk-Tier` trailers for CI gating (Tier C = human review on bump).
- **0173 (fixup-fold, 2420 lines) + F_*.py layer**: the fold direction is right (fixups → owning
  commits). Until complete, the REPLACEMENT-block structural debt noted in AGENTS.md persists:
  any edit to `ci-setup-kernel.sh`'s fixup block needs the `mutate.py --check` dry-run.
- **Debug/observability patches (0169 canary, 0148 EKFC log, 0113 DCSR taps, 0107 CC harness)**:
  confirm each is `CONFIG_FMAN_PCD_DEBUG_FS`-gated or otherwise compiled out of production;
  0170 added the Kconfig gate — audit that all debug writers honor it (spot-check 0169/0148).
- **WireGuard lab patches (4011 cpu-mask, 4013 sink-FQs, 4015 fastpath-lab, 4016 tx-lanes)**:
  clearly marked lab/Risk-Tier C. Ensure none is in the production series path by default
  (4016 defaults to stock behavior — good pattern; apply it to 4011/4013/4015 if not already).

## 7. Prioritized opportunity list

| # | Item | Area | Effort | Value |
|---|------|------|--------|-------|
| 1 | S-1 genl perm audit (`GENL_UNS_ADMIN_PERM` on mutating ops) | security | S | high |
| 2 | S-2 split `vyos-offload-ask` prod vs hit-test verbs; allowlist `--port` | security | S–M | high |
| 3 | S-3 `pcd-snapshot` read-only `/dev/mem` | security | trivial | med |
| 4 | A-3 single port-resolver module | code | S | med |
| 5 | A-2/P-3 demote success logs to debug/trace | perf/noise | S | med |
| 6 | S-5 `set -euo pipefail` + shellcheck CI | robustness | S | med |
| 7 | A-1 split REPLACE into parse/resolve/pending modules | code | M | med |
| 8 | A-6 golden-vector key-build tests | testing | M | high |
| 9 | P-1 measure + bound REPLACE latency; adaptive poll | perf | M | med |
| 10 | S-4 MCP re-verify after each rebase (auth, redaction, prefix) | security | S | med |
| 11 | S-7 document `notrack` implications; test port-set sourcing | security | S | med |
| 12 | 031: derive port_map from DT; warn on unknown port | code | S | low–med |
| 13 | S-6 harden `show_ask_offload.py` parse errors | robustness | trivial | low |
| 14 | S-8 validate `offload_delay_ms` write; consider genl attr | robustness | S | low |
| 15 | P-4 WireGuard lane-scaling measurement campaign | perf | test | med–high |
| 16 | Tier-C manifest gating in CI from series trailers | process | S–M | med |

## 8. What was explicitly NOT changed

At the original review time, no source, patch, config, or documentation file
was modified. All findings reference `file:line` or patch names for follow-up.
Suggested next step: pick items from §7, one variable per experiment per the
silicon rules (AGENTS.md §S6-10.10), cold-boot before silicon measurements
(§S6-10.9), and keep qdrant as the gate for any FMan register change (§S0).

## 9. Triage disposition (2026-10-18)

This is a source-level triage, not a remediation pass. No finding below justifies
a broad refactor or production behavior change without its own scoped task.

### Stale, already covered, or not a current defect

- **S-1 — stale finding.** Every current `ask_genl_small_ops` entry uses
  `GENL_ADMIN_PERM` (`ask_genl.c:113-171`), not `GENL_UNS_ADMIN_PERM`.
- **S-3 — false positive as phrased.** `pcd-snapshot` writes the KeyGen Action
  Register to trigger indirect reads (`pcd-snapshot:158-170`, `ar_wait()`); the
  writable mapping is required by the snapshot algorithm. A separate
  passive-read-only tool would be a different task.
- **A-4 — no unchecked access found.** `ask_vlan_cc.c` checks `port_id` against
  `ARRAY_SIZE(ports)` before indexing in add, delete, teardown and stats paths;
  `ask_hw_port_family` has bounds-checked get/set helpers.
- **A-5 — no change warranted.** The parser belongs to the debugfs-only
  developer surface; do not add more verbs there.
- **A-6 — substantially covered.** `ask_test_flow_offload.c` already pins
  golden IPv4/IPv6 dual-key vectors, VLAN/PPPoE context, and zero-filled lanes.
  Any additional tests should target lifecycle/rebuild behavior, not duplicate
  these byte-layout vectors.
- **P-2/P-5 — retain the existing invariant.** Stats remain allocation-free
  under the RCU read section; order-0 RX buffers remain mandatory.
- **S-8 — low priority, validator present.** The CLI leaf restricts the delay
  to `0-60000`; direct Python invocation can bypass that validator, but no
  unsafe value is currently reachable through the supported CLI.

### Valid follow-up, not a ship-stopper

- **A-1/A-3:** maintainability work only. Do not split modules or move resolver
  APIs during Phase 2 consolidation.
- **A-2/P-3:** defer log demotion/scrubbing to a small, measured change; preserve
  failure and warning visibility.
- **P-1:** measure REPLACE latency percentiles before changing batching or the
  pending poll interval. The lab rig was already running unrelated traffic, so
  no measurement was started.
- **P-4:** the WireGuard RPS A/B was not run because the shared rig was busy.
- **P-6:** no VPP tuning before the current libxdp image and zero-copy RX gate
  are operator-validated.
- **S-2:** production config invokes only `family <mask>` via the fixed board
  port map (`vyos-1x-031`); debugfs HIT/flow verbs are not reachable from that
  commit path. Keep the helper boundary explicit and harden its developer-only
  `--port`/flow arguments in a separate task.
- **S-4:** source inspection confirms authentication wraps the `/mcp/` mount,
  read-only mode restricts tool exposure/calls, and configuration resources
  and `show configuration` use the shared redactor. This is not a deployed
  end-to-end auth/prefix test.
- **S-5:** do not apply `set -euo pipefail` mechanically to every script; first
  audit traps, intentional best-effort commands and word-splitting contracts.
- **S-6:** malformed YNL JSON/attributes can raise an uncaught `ValueError` in
  `show_ask_offload.py`; low-severity operator-output hardening.
- **S-7:** the WireGuard notrack rules are generated only from configured,
  enabled listen ports, with an explicit opt-out and a conntrack dependency.
  Preserve the documented firewall-state implication and add a focused
  render/order test if this path changes.
- **VyOS integration notes:** the hardcoded port map is board-specific and
  currently matches the LS1046A mapping; partition-installer and MCP changes
  remain separate high-blast-radius rebase audits.
- **Patch metadata manifest:** useful process improvement, but not required
  for current correctness; schedule after the active feature gates.

### Phase 2 prerequisite discovered

The proposed deletion of `ask_vlan_cc.c` is not safe against the current
checkout: it remains in Kbuild and is called by the VLAN-edit flow replace
path, generic-netlink stats path and port teardown path. This contradicts the
Phase 2 plan's “dead code” premise and the later inline-FE VLAN notes. The
master plan now records this stop condition. Resolve the active implementation
and silicon/image discrepancy before deleting or folding this code.
