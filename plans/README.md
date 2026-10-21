# plans/

Live planning, operational and reference documents. Retired documents live in
[`archive/`](archive/README.md), each with a `<name>.archive-note.md` saying
what replaced it. `plans/` holds live documents only. New ASK2 planning goes
into the master plan, not into new plan files.

Last consolidated: 2026-10-15. Completed ASK2 work and old result tables are
archived; the master plan is the only task queue. Full feature parity is
still open, independently of routed/NAT/VLAN throughput parity.

## ASK2 hardware offload

| Document | Owns |
|---|---|
| [`ASK2-MASTER-PLAN.md`](ASK2-MASTER-PLAN.md) | **The authoritative task list:** missing vendor features, dependencies, acceptance, consolidation and defects. Absorbs the rewrite plan's pending work. Start here. |
| [`ASK2-PRIORITIZED-TASKS.md`](ASK2-PRIORITIZED-TASKS.md) | Ordered P0–P10 view of the master plan with status, gates and rig constraints (snapshot 2026-10-21; subordinate to the master plan). |
| [`ASK2-VENDOR-PARITY.md`](ASK2-VENDOR-PARITY.md) | Current comparison scope, paired 10×10 method, evidence still required and verdict contract. Old single-run and A6 tables are archived, not current acceptance. |
| [`DUAL-DATAPLANE.md`](DUAL-DATAPLANE.md) | Binding runtime contract: S0/S1/S2 state machine, `offload ipv4\|ipv6` CLI, ASK↔VPP mutex, teardown checklist, open contract obligations. History in `archive/`. |
| [`OFFLOAD-CAPABILITY-PLAN.md`](OFFLOAD-CAPABILITY-PLAN.md) | Remaining capabilities only: vendor mechanism vs recommended ASK2 mechanism; shipped capabilities summarised, details in `archive/`. |
| [`ASK2-BRIDGE-OFFLOAD-PLAN.md`](ASK2-BRIDGE-OFFLOAD-PLAN.md) | Remaining bridge work: B7b, mixed-role/multi-table dispatch, IRB, current-image repeats and safety/resource gates. B0–B6/B7a history is archived. |
| [`ASK2-PPPOE-OFFLOAD-PLAN.md`](ASK2-PPPOE-OFFLOAD-PLAN.md) | Remaining PPPoE acceptance, 10,000-cycle churn, fragmentation/exhaustion, over-VLAN/relay gaps and IPv6 fragment-ID limitation. |
| [`ASK2-IPSEC-OFFLOAD-PLAN.md`](ASK2-IPSEC-OFFLOAD-PLAN.md) | IPsec ESP offload via CAAM (T-M6-4). Tier 1 substrate verified 2026-10-16 (CAAM built in, selected, self-tests pass); tunnel gate and the Tier 2 fast path not started. |
| [`ASK2-SURICATA-BYPASS-PLAN.md`](ASK2-SURICATA-BYPASS-PLAN.md) | Suricata + ASK2 design only: the native VyOS service is IDS-only (AF_PACKET); approval/revocation/fail-closed contract required before any code. |
| [`CC-ACL-OFFLOAD-PLAN.md`](CC-ACL-OFFLOAD-PLAN.md) | Static ACL backend: settled facts (no CC walker; `0194` ntuple on ehash verified) and the open Phase 2 drop-ACL work. |
| [`ASK2-PERFORMANCE-TEST-HARNESS.md`](ASK2-PERFORMANCE-TEST-HARNESS.md) | Throughput and CPU measurement method; current rig is `bin/testrig-combo-matrix.sh` (dell1–DUT–dell2). |
| [`ASK2-WIREGUARD-FASTPATH-PLAN.md`](ASK2-WIREGUARD-FASTPATH-PLAN.md) | The single active WireGuard plan (remaining work only): software-path open items (matrix re-run on the shipping image, persistent `noqueue`, netns selftest), fast-path P2b-2/P2b-3/P2c, P3 RX, P4 offline-port reinjection, P5 order restoration, with gates and kill criteria. Completed software stages, matrix and P0-P2b-1 results are in `archive/`. |

## VPP dataplane

| Document | Owns |
|---|---|
| [`VPP.md`](VPP.md) | Single-image VPP / AF_XDP dataplane: current state and configuration. |
| [`VPP-AFXDP-ZC-FULLSPEED.md`](VPP-AFXDP-ZC-FULLSPEED.md) | AF_XDP zero-copy full-speed plan (open; current task IDs in master §4.5). |

## Build, patches, operations

| Document | Owns |
|---|---|
| [`ASK-ISO-BUILD-AND-INSTALL.md`](ASK-ISO-BUILD-AND-INSTALL.md) | Building (CI preferred, §5a deploy recipe), publishing to lxc200, installing on the board. |
| [`DEV-LOOP.md`](DEV-LOOP.md) | Local TFTP dev loop. Throwaway experiments only; board images come from CI. |
| [`TA-2026-07-18-002-patch-architecture.md`](TA-2026-07-18-002-patch-architecture.md) | Patch architecture authority: the three layers, the canonical branch, risk tiers, §17 tripwires. |
| [`CHANGELOG.md`](CHANGELOG.md) | Manual changelog (newest first). |

## Platform reference

| Document | Owns |
|---|---|
| [`BOOT-PROCESS.md`](BOOT-PROCESS.md) | Boot chain and U-Boot reference. |
| [`FIRMWARE.md`](FIRMWARE.md) | Firmware update guide. |
| [`LED-DAEMON.md`](LED-DAEMON.md) | Status LED control (LP5812). |
| [`NETWORKING-DEEP-DIVE.md`](NETWORKING-DEEP-DIVE.md) | LS1046A FMan, QBMan, portal and driver architecture. |
| [`PORTING.md`](PORTING.md) | Porting VyOS ARM64 to the LS1046A: drivers, DPAA1, device tree. |
