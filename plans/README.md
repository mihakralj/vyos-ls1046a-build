# plans/

Live planning, operational and reference documents. Retired documents live in
[`archive/`](archive/README.md), each with a `<name>.archive-note.md` saying
what replaced it. `plans/` holds live documents only. New ASK2 planning goes
into the master plan, not into new plan files.

Last consolidated: 2026-10-09.

## ASK2 hardware offload

| Document | Owns |
|---|---|
| [`ASK2-MASTER-PLAN.md`](ASK2-MASTER-PLAN.md) | **The authoritative ASK2 execution plan:** baseline, open milestones M3–M8, open task IDs and gates, open defects, binding silicon facts, live reference register (§8). Open work only; achieved history is in [`archive/ASK2-MASTER-PLAN-2026-10-09.md`](archive/ASK2-MASTER-PLAN-2026-10-09.md). Start here. |
| [`ASK2-REWRITE-PLAN.md`](ASK2-REWRITE-PLAN.md) | Vendor-parity plan, open work only: churn gate (reopened), open board checklist, Phase 2 consolidation, Phase 3/4 features, vendor reference records and test method. Achieved history: [`archive/ASK2-REWRITE-PLAN-2026-10-09.md`](archive/ASK2-REWRITE-PLAN-2026-10-09.md). |
| [`ASK2-VS-VENDOR-THROUGHPUT.md`](ASK2-VS-VENDOR-THROUGHPUT.md) | A6 scoreboard: ASK2 vs vendor NXP ASK routed throughput and retransmits (unidir and bidir tables). |
| [`DUAL-DATAPLANE.md`](DUAL-DATAPLANE.md) | S0/S1/S2 state machine, per-interface CLI contract, reversibility contract. |
| [`OFFLOAD-CAPABILITY-PLAN.md`](OFFLOAD-CAPABILITY-PLAN.md) | Per capability: vendor mechanism vs ASK2 mechanism, with build steps. |
| [`ASK2-BRIDGE-OFFLOAD-PLAN.md`](ASK2-BRIDGE-OFFLOAD-PLAN.md) | L2 bridge offload (T-M6-2); open regression as of 2026-09-16. |
| [`ASK2-IPSEC-OFFLOAD-PLAN.md`](ASK2-IPSEC-OFFLOAD-PLAN.md) | IPsec ESP offload via CAAM (T-M6-4); draft, not started. |
| [`CC-ACL-OFFLOAD-PLAN.md`](CC-ACL-OFFLOAD-PLAN.md) | ACL / ntuple / tc-flower backend; verdict that the CC match walker is absent in 210.10.1. |
| [`ASK2-PERFORMANCE-TEST-HARNESS.md`](ASK2-PERFORMANCE-TEST-HARNESS.md) | Throughput and CPU measurement method; current rig is `bin/testrig-combo-matrix.sh` (dell1–DUT–dell2). |

## VPP dataplane

| Document | Owns |
|---|---|
| [`VPP.md`](VPP.md) | Single-image VPP / AF_XDP dataplane: current state and configuration. |
| [`VPP-AFXDP-ZC-FULLSPEED.md`](VPP-AFXDP-ZC-FULLSPEED.md) | AF_XDP zero-copy full-speed plan (open). |

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
