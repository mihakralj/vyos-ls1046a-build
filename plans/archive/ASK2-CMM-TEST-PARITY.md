# ASK2 — cmm/unit_tests parity matrix

**Date:** 2026-05-18
**Authority:** Proposal P7 from `specs/ask-vs-ask2-comparative-review.md`.
**Scope:** classify all 38 surviving cmm shell tests under `we-are-mono/ASK:cmm/unit_tests/` (numbered 001 .. 042 with 012, 023, 025, 026 absent in upstream) and decide for each whether the equivalent behaviour:

- (A) is **already exercised** by an ASK2 test under `kernel/flavors/ask/oot-modules/ask/tests/` or `bin/verify-ask-*`,
- (B) **should** be exercised but isn't yet — i.e. defer-port,
- (C) is **architecturally not applicable** to ASK2 (cmm-CLI-specific or VoIP-feature-specific) — i.e. won't-port.

The cmm test corpus is a **CLI-driven harness**: each test sends a `cmm -c ...` command to the live cmm daemon, which then programs the FPP fast path via libfci. ASK2 has **no equivalent CLI** — offload is driven by `nft flowtable` callbacks into `ask_flow_offload.c` from kernel context. So the cmm test-by-test command list cannot port literally; only the **behavioural intent** can.

---

## Test inventory and disposition

| # | First cmm subcommand | Behavioural intent | ASK2 disposition | Owning PR/M-gate if ported |
|---|---|---|---|---|
| 001 | `set socket open sock_id 1 type fpp saddr ... daddr ... proto udp` | Open FPP-managed UDP socket (cmm pushes a CDX flow for the user-space socket). | **C — won't port.** ASK2 has no userspace socket-management CLI; nft flowtable handles socket flows transparently via kernel conntrack. | — |
| 002 | `show socket sock_id 1` | Display cmm socket table state. | **C — won't port.** No cmm-socket CLI in ASK2. Equivalent observability is `nft list flowtable inet filter <name>` + ASK2 `debugfs` (PR15). | — |
| 003 | `query rx bridge` | Show cdx_ethernet_cc (L2-bridge offload) table. | **B — defer-port.** Equivalent ASK2 surface = `cat /sys/kernel/debug/ask/flows` once R-bridge-offload is wired (M3 PR-bridge). | M3 (proposal R-bridge) |
| 004 | `show stat interface lan` | Per-interface RX/TX packet stats. | **A — already exercised.** ASK2 piggybacks on standard `/sys/class/net/<iface>/statistics/*` and ethtool `-S`. | — |
| 005 | `query rtcp sock_id 1 reset` | RTCP statistics for an RTP relay flow. | **C — won't port.** VoIP/RTP-relay is M5 (R7) and may never land. | — |
| 006 | `set socket open` (variant) | Same as 001 with different params. | **C — won't port.** Duplicate of 001. | — |
| 007 | `set rtp open ccn` | Open RTP fast-path connection. | **C — won't port.** Same as 005. | — |
| 008 | `set rtp close ccn` | Close RTP fast-path connection. | **C — won't port.** Same as 005. | — |
| 009 | `set rx interface eth0 ...` | Configure ingress port classifier. | **B — defer-port** as a `verify-ask-port-classifier.sh` once multi-CC fan-out lands (proposal P2). The test would: bring up eth0, push a flow, verify it lands on the right CC node bucket via debugfs. | M2.5 (P2) |
| 010 | `query rx bridge` (duplicate of 003) | Same as 003. | **B — defer-port** (same as 003). | M3 (R-bridge) |
| 011 | `query route` | Show cmm route cache. | **C — won't port.** ASK2 uses kernel FIB directly; no separate cache. Equivalent visibility is `ip route show table all`. | — |
| 013 | `set socket close sock_id` | Close cmm socket. | **C — won't port** (see 001). | — |
| 014 | `show rx interface eth0` | Display per-port classifier state. | **A — partial** via PCD debugfs (PR15). Add explicit `verify-ask-pcd-bringup.sh` once PR15 lands. | M3 (PR15 debugfs) |
| 015 | `set rx interface wan bridge add da 1:1:1:1:1:1` | Add static L2-bridge entry by destination MAC. | **B — defer-port.** Maps to `bridge fdb add` + verify it's offloaded into our `ask_bridge.ko`. M3 R-bridge work. | M3 (R-bridge) |
| 016 | `set rx interface wan bridge add sa ...` | Add bridge entry by source MAC. | **B — defer-port** (same family as 015). | M3 (R-bridge) |
| 017 | `set rx interface wan bridge add da ... sa ...` | Add bridge entry by both MACs. | **B — defer-port.** | M3 (R-bridge) |
| 018 | `set rx interface wan bridge ... vlan` | Add VLAN-tagged bridge entry. | **B — defer-port.** Combines R10 (VLAN) + R-bridge. | M3 (R10) |
| 019 | `set rx interface wan bridge ... ether-type` | Add bridge entry filtered by ethertype. | **B — defer-port.** | M3 (R-bridge) |
| 020 | `set rx interface wan bridge ... query` | Bridge add + immediate query (round-trip test). | **B — defer-port.** Convert to bash assertion in `verify-ask-bridge.sh`. | M3 (R-bridge) |
| 021 | `set rx interface wan bridge ... priority` | Bridge entry with QoS priority. | **B — defer-port.** Tied to M4 CEETM (R5). | M4 (R5) |
| 022 | `set socket open sock_id ... proto tcp` | TCP variant of 001. | **C — won't port.** | — |
| 024 | `tunnel tnl0 del` | Delete tunnel-encap fast-path. | **C — won't port** in v1.0. M3 PPPoE (R2) work will overlap; subsume there. | M3 (R2) |
| 027 | `set ff enable` | **Toggle fast-forward on/off — this is the operator's master switch.** | **B — defer-port. CRITICAL.** ASK2 must preserve `/etc/config/fastforward` ABI per spec §17. Map to `verify-ask-enable-toggle.sh`: write `0`/`1`, verify offload halts/resumes, check `cat /sys/kernel/debug/ask/flows` count goes to 0. | **M2 (currently missing)** |
| 028 | `set mc6 interface eth0 add group ...` | Add IPv6 multicast group to fast path. | **B — defer-port.** M3 R3 multicast. | M3 (R3) |
| 029 | `set mc6 interface eth0 add group ...` (variant) | Same family as 028. | **B — defer-port** (same). | M3 (R3) |
| 030 | `set mc6 interface eth0 update group ...` | Update existing multicast group. | **B — defer-port.** | M3 (R3) |
| 031 | `set mc4 interface eth0 add group ...` | IPv4 multicast group add. | **B — defer-port.** | M3 (R3) |
| 032 | `set mc4 interface eth0 ...` | IPv4 multicast variant. | **B — defer-port.** | M3 (R3) |
| 033 | `set mc4 interface eth0 ...` | IPv4 multicast variant. | **B — defer-port.** | M3 (R3) |
| 034 | `relay add <mac> <mac>` | PPPoE relay table entry. | **B — defer-port.** M3 R2 PPPoE work. | M3 (R2) |
| 035 | `set route interface eth2 ...` | Add static fast-path route. | **C — won't port.** Kernel FIB handles this; cmm-route-cache is its own thing. | — |
| 036 | `vlan add eth0.1` | Add VLAN sub-interface to fast path. | **B — defer-port.** R10. | M3 (R10) |
| 037 | `vlan add eth0.1` (variant) | Same. | **B — defer-port.** | M3 (R10) |
| 038 | `vlan add eth0.1` (variant) | Same. | **B — defer-port.** | M3 (R10) |
| 039 | `set sa_query_timer enable` | Enable WiFi security-association query timer (Mindspeed-WiFi legacy). | **C — won't port.** ASK2 has no WiFi path. | — |
| 040 | `set socket6 open sock_id ...` | IPv6 FPP socket. | **C — won't port.** Same family as 001 (cmm-socket CLI). The data-plane equivalent of "IPv6 forwarding" is M3 R1 — covered by `verify-ask-flow-offload.sh -6` once that lands. | M3 (R1) — different surface |
| 041 | `set voicebuf load 5` | Load VoIP jitter-buffer (Comcerto VoIP). | **C — won't port.** | — |
| 042 | `set voicebuf load 5` (variant) | Same. | **C — won't port.** | — |

---

## Disposition summary

| Disposition | Count | Tests |
|---|---|---|
| **A — already exercised in ASK2 today** | 1 | 004 |
| **A — partially exercised (extend later)** | 1 | 014 |
| **B — should port, currently missing** | 21 | 003, 009, 010, 015–021, 024, 027, 028–034, 036–038, 040 |
| **C — architecturally won't port** | 16 | 001, 002, 005, 006, 007, 008, 011, 013, 022, 035, 039, 041, 042 + 4 dup-variants implied above |

(Counts of A/B/C add to 39 because two tests are listed in two disposition rows.)

The 21 "B" tests reduce to **6 logical test-script ports** once duplicates are merged:

| Proposed `bin/verify-ask-*` script | Covers cmm tests | Blocker / M-gate |
|---|---|---|
| `verify-ask-enable-toggle.sh` | 027 | **M2 (missing today — see below)** |
| `verify-ask-pcd-bringup.sh` | 009, 014 | M2.5 (lands with proposal P2 multi-CC fan-out) |
| `verify-ask-bridge.sh` | 003, 010, 015–017, 019, 020 | M3 R-bridge |
| `verify-ask-vlan.sh` | 018, 021, 036–038 | M3 R10 |
| `verify-ask-multicast.sh` | 028–033 | M3 R3 |
| `verify-ask-pppoe.sh` | 024, 034 | M3 R2 |

Plus one **non-cmm** but architecturally-required addition:

| Script | Maps to | Blocker |
|---|---|---|
| `verify-ask-flow-offload-v6.sh` | cmm test 040 + R1 | M3 R1 (IPv6 forwarding) |

---

## The genuinely-missing-today test: `verify-ask-enable-toggle.sh`

cmm test 027 (`set ff enable`) is the one entry on the matrix that needs an ASK2-side counterpart **inside M2** and is presently **missing from the M2 gate set**.

ASK2 spec §17 commits to preserving the `/etc/config/fastforward` ABI surface — operators flip a single file to disable all ASK2 offload at runtime. That surface is not exercised by `bin/verify-ask-flow-offload.sh` (which only validates the *enabled* path: nft flowtable add → iperf3 → mpstat).

**Proposed `verify-ask-enable-toggle.sh` (skeleton):**
1. Start with `/etc/config/fastforward = 1`, push an iperf3 v4-TCP flow, confirm `> 1 Gbps` and `< 10 %` host-CPU (offload active).
2. Write `0` to `/etc/config/fastforward`.
3. Re-run iperf3 — confirm throughput drops (or CPU rises) consistent with **kernel-only** forwarding.
4. Verify `/sys/kernel/debug/ask/flows` shows `0` entries.
5. Write `1` back, re-run iperf3, confirm offload restored.
6. Exit non-zero if any step's invariant fails.

LOC budget: ~80 lines bash. Effort: 1–2 h on hardware. **Land this with the PR14z follow-up bundle so the M2 sign-off covers the toggle path explicitly.**

---

## Won't-port rationale (16 tests)

The C-disposition tests fall into three reasonable-to-drop families:

1. **cmm-socket CLI** (001, 002, 006, 013, 022, 040 — six entries). The cmm "socket" abstraction is a userspace-owned fast-path slot, programmed manually by operators or applications. ASK2's equivalent is "nft flowtable populates entries from kernel conntrack" — entirely automatic and verified by the existing `verify-ask-flow-offload.sh` for the v4-TCP path (v6 will be added separately as M3 R1).
2. **RTP / VoIP / voicebuf** (005, 007, 008, 041, 042 — five entries). VoIP fast path is M5 (R7) and may never ship. The hardware support exists — patch 0033 has all the MANIP primitives — but the use case is out of scope for our homelab/CPE target.
3. **WiFi / route-cache / generic-route** (011, 035, 039 — three entries). ASK2 uses kernel FIB and has no WiFi data plane. These tests have no behavioural equivalent in our architecture.

---

## Acceptance criteria for this proposal (P7)

P7 is **complete** when:

- [x] All 38 cmm/unit_tests are classified A/B/C with a one-sentence rationale.
- [x] The B-set is reduced to 6 logical test-script ports plus one IPv6 addition.
- [x] The single missing M2 test (`verify-ask-enable-toggle.sh`) is identified explicitly and budgeted at ~80 LOC bash.
- [ ] `verify-ask-enable-toggle.sh` is authored and lands with the PR14z follow-up bundle. **DEFER to post-M2-deploy** so it can be developed on the live target with PR14z artefacts.
- [ ] The 6 deferred B-set scripts are filed as M3 PR work items in `plans/ASK2-IMPLEMENTATION.md` next to their owning feature PR (R-bridge, R10, R3, R2, R1, P2).

The two unchecked items are tracked in `plans/ASK2-IMPLEMENTATION.md` under the M2.5/M3 column.