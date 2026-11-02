# ASK2 WireGuard plan: remaining work (software path and zero-skb fast path)

**2026-10-15 · dpaa1 · Status: open.** This is the single active WireGuard plan. The completed work, which covers the measurements, profiles, the P0/P1/P2a/P2b-1 builds and the gate results, has moved to the archive:

- `plans/archive/ask2-wireguard-plan.md`: software throughput. It covers the stage 0 baseline, stage -1 (lockdep debug kernel), patches 4011/056/057/4016, the retired GRO patch 4012, E1 `noqueue`, E2 `tx_lanes=3`, P2b-1 results and the conntrack-hook analysis.
- `plans/archive/ask2-wireguard-matrix-106-vs-185.md`: the 2026-10-12/13 nine-cell matrix (image 2243, before `tx_lanes`).
- `plans/archive/ASK2-WIREGUARD-FASTPATH-PLAN-2026-10-15.md`: the full fast-path design history. It includes P0 feasibility, P1 (gate G1 PASS, image 1943), P2a lab seal (about 465k packets/s, image 2243), the alternatives considered and the closed open items.

WireGuard is not a vendor-parity item: neither the vendor ASK nor ASK2 hardware-offloads it, and CAAM Era 8 has no ChaCha20-Poly1305. Its priority relative to ASK2 parity work is set in `plans/ASK2-MASTER-PLAN.md`.

## 1. Where things stand

| Item | State |
|---|---|
| Software path (patches 4011, 056, 057, 4016; `serial_cpus=14 tx_lanes=3` in `96-enable-services.chroot`) | Shipped. Lanes=3 matrix (image 0051): `.185` leads `.106` in 8 of 9 cells; 1T `up` 1.65 vs 1.38. |
| 1T `bidir` (1.44 vs `.106` 1.55) | The only losing cell. Root cause is the presence of the conntrack hooks: a flushed ruleset gives 1.61. There is no safe VyOS-semantics-preserving fix, so this is a configuration trade-off. |
| Fast path TX, P2b-1 (patch 4017, `wgfp_peer`) | Gate G2 PASS on image 0347: 2.62 Gbit/s on 4 flows, survives the 120 s rekey, no Invalid MAC. Limits: one peer only, flows steered by hand through debugfs `fe_flow`, TX only. |
| Fast path RX, offline-port reinjection, order restoration | Not started. |

## 2. Architecture (unchanged, kept for the remaining phases)

```mermaid
flowchart LR
    subgraph TX["TX: plaintext into the tunnel (P2b-1 done, P2b-2/3 open)"]
        A["LAN frame<br/>eth3 RX"] --> B["FMan parser + KeyGen<br/>46B inner 5-tuple"]
        B -->|"ehash HIT<br/>record = ENQ to peer FQ k"| C["QMan FQ k"]
        B -->|"MISS"| S["normal stack<br/>wg_xmit (learns the flow)"]
        C --> D["CPU worker<br/>in-place ChaCha20-Poly1305<br/>prepend Eth+IP+UDP+WG header"]
        D --> E["eth4 no-confirm TX FQ"]
    end
```

```mermaid
flowchart LR
    subgraph RX["RX: tunnel to plaintext (P3/P4 open)"]
        A2["outer UDP frame<br/>eth4 RX"] --> B2["FMan KeyGen<br/>outer UDP 5-tuple"]
        B2 -->|"ehash HIT"| C2["QMan RX FQ"]
        B2 -->|"MISS / handshake"| S2["normal stack"]
        C2 --> D2["CPU worker<br/>type, keypair, replay check,<br/>in-place decrypt"]
        D2 -->|"P3"| E2["build_skb + netif_receive_skb(wg0)"]
        D2 -->|"P4"| F2["offline-port input FQ<br/>FMan forwards inner packet"]
    end
```

The design rules still apply:

- The per-peer fast-path state lives inside the `wireguard` module, and `ask.ko` supplies only record insert/delete and FQ ids.
- Records are installed by the slow path, so a MISS stays safe.
- Each peer gets N FQs (start at 4) with flows assigned round-robin, which keeps per-flow order through QMan.
- Frames are processed in place on the BMan buffer.
- Any unexpected state punts to the stack.

## 3. Remaining phases, gates and kill criteria

AGENTS.md S0 applies to every phase that touches FMan: run at least three qdrant queries, cross-check against `arch/fman-microcode-210-programming-reference.md` and `specs/fman-keygen-flow-key-spec.md`, read back every silicon write, and cold boot before each experiment. Arm `softlockup_panic` and `panic_on_rcu_stall`, and never experiment on `eth0`.

### P2b-2. Automatic flow install

On the first `wg_xmit` for an inner flow whose peer, endpoint and keypair are valid and whose ingress port is ASK-engaged, the module asks `ask.ko` to insert the record. The record is removed on flow teardown, peer removal or disengage. This needs the registration API in section 4.

**Gate:** no debugfs steering. Four inner TCP flows reach at least 2.6 Gbit/s `up`, and `pcd-snapshot diff` is clean after disengage.

### P2b-3. Slow-path punt and AllowedIPs

The worker must hand these cases back to the stack by building an skb and calling the `wg_xmit` slow path, then withdraw the record:

- AllowedIPs mismatch;
- keypair near `REKEY_AFTER_*`;
- no neighbour;
- oversize frame;
- keepalive due;
- peer removed.

**Gate:** the upstream `tools/testing/selftests/wireguard/netns.sh` passes, endpoint roaming and a forced rekey keep the tunnel up, and there is no Invalid MAC on stock dell2.

### P2c. Multi-peer binding

`wgfp_peer` binds only the first peer. Bind per peer so that the 2T and 4T matrix cells can run on the fast path.

**Gate:** 2T and 4T `up` are at least the software lanes=3 results (1.72 and 1.91 Gbit/s), with no cross-peer seal.

### Before P2b-2: explain the P2a plateau (resolved 2026-11-02)

P2a plateaued at about 465k packets/s while every core showed 43% busy. **Resolved on `.185` (image `2026.10.31-0511-rolling`): the plateau is CPU-bound and the "43%/60% busy" figure is a harness artifact.** A 1 s `/proc/stat` time series on the DUT during an unpaced run (`PPS_LIST=0 SECS=20 TBLS=3 bin/testrig-wgfp-p2a.sh`) shows all four cores at 98–100% softirq for the whole sealing window. `bin/testrig-wgfp-p2a.sh` takes its CPU snapshot around the entire pktgen trial, including several seconds of setup with no traffic, which dilutes the figure. This run sealed 499–501k packets/s, all delivered, and the captured packets decrypt. That is about 12.8k cycles per packet at 4 × 1.6 GHz, matching the 13k model. A `perf record -a -g` profile of the window (static perf `/mnt/builds/wgfp/linux/tools/perf/perf` copied to `/tmp/perf` on the DUT) gives ChaCha20-Poly1305 about 60% (`chacha_4block_xor_neon` 43.5%, `poly1305_blocks_neon` 10.2%, `chacha_permute` 3.1%, the rest of the library about 3%), NEON begin/end and `fpsimd_flush_cpu_state` about 2.6%, `wgfp_lab_frame` (header build and pad) 4.9%, and DPAA RX/TX (`qman_enqueue` 2.3%, `__poll_portal_fast` 1.9%, `dpaa_wgfp_xmit` 1.4%, `wgfp_dqrr` 1.0%) about 7%. **The TX fast path is crypto-bound at about 500k packets/s (about 5.7 Gbit/s inner at 1420 bytes); later phases cannot add much per-packet cost.**

The same runs showed a second problem: with all four sinks saturated, SSH to the DUT timed out for the whole window. The sink FQs were on work queue 3 (medium tier), above the kernel RX FQs on work queue 6 (`dpaa_assign_wq`), so QMan starved eth0. `dpaa_wgfp.c` now puts the sinks on WQ6 (`WGFP_WQ`). **Verified 2026-11-02** on `.185` (image `2026.11.02-0440-rolling`, warm boot after install): an unpaced P2a run (`PPS_LIST=0 SECS=20 TBLS=3`) sealed 502,933 packets/s with 100% delivered and load average 8.4, and SSH to eth0 answered in about 1 s (idle: 1.3 s). Ping RTT stayed at the 34 ms baseline of the Tailscale path.

### P2b-2 implementation (2026-11-02, CI run 41389740748, board-tested on `.185`)

- `dpaa_wgfp.c` (fsl_dpa): `dpaa_wgfp_flow_add/del/packets()` and `dpaa_wgfp_port_ok()`. They build the F-269 routed key (`0x40|SIP|DIP|..|proto@41|sport@42|dport@44|VID 0|SID 0`, 50 bytes) and call `fman_pcd_fe_flow_add()` with `rx_fqid` = a sink FQID (the same record shape as the ethtool-ntuple steering, F-241) on the ingress port's per-port table. The sink is `hash % sinks`. `dpaa_wgfp_register()` creates the sinks when there are none. There is no `ask.ko` dependency; `ask.ko` already fails closed for flows whose egress is `wg0`, so it never installs a conflicting record.
- `fastpath.c`: `wg_fastpath_learn()`, called from `wg_xmit()` (patch `4018`) for the bound peer. It learns inner IPv4 TCP/UDP flows from a physical DPAA ingress whose conntrack entry is `ESTABLISHED`/`ESTABLISHED_REPLY`, not NAT'ed, without a master and without a helper. It never learns from a SYN. Records are installed by a workqueue, because the record API sleeps. FIN/RST (punted by `tcp_ctl_punt`) withdraws the record, five 2 s aging periods without silicon hits withdraw it, and `wgfp_peer off` withdraws all of them. Failed installs are held for 30 s to avoid retry storms. The `wgfp_peer` read-out adds `flows`, `installs`, `install_fail`, `withdraws` and `decline_withdraws`.
- Seal path fixes: the inner TTL is now decremented (`ip_decrease_ttl`; the lab and P2b-1 forwarded with the TTL unchanged), and TTL ≤ 1 or a dead peer declines. **Any decline of a learned flow withdraws its record** (`wgfp_withdraw_frame()`). Declined frames are dropped by the sink, so an oversized DF flow would otherwise black-hole after install. With the withdraw it falls back to the stack, which handles PMTU. A real punt is still P2b-3.
- Known limits: only flows learned from a packet that still carries a conntrack entry. A flow already in the software flowtable has none; it is tracked from its first established packet, and later packets only count toward admission. A fast-pathed flow skips firewall changes until withdrawn, as an offloaded flowtable flow does. The flow table takes one global spinlock per slow-path packet of the bound peer. The `wgdev` reference held by peer mode blocks deleting `wg0` while bound (P2b-3).
- Harness: `AUTO=1 bin/testrig-wgfp-p2b.sh` skips the debugfs steering and checks that `wgfp_peer off` leaves no records in `fe_flow`.
- **Board result 2026-11-02** (`.185`, image `2026.11.02-0440-rolling`, warm boot after install, tunnel from `KEEP=1 bin/testrig-wireguard.sh setup wg-v4`): `AUTO=1 DUR=20` gave 2.465 Gbit/s up (flows 0.29 / 0.26 / 0.99 / 0.93), 4,491,662 sealed, `install_fail=0`, `decline_withdraws=0`, and `fe_flow` empty after `off`. Install, aging and teardown work. The flow table learns, installs and withdraws on its own. The record's silicon counter (`fe_ehash_stats pkt_count`) tracks `sealed` packet for packet, so aging keeps a busy flow installed for its whole life (0.5 s on-board poll). The 8 installs / 4 withdraws were iperf3's 4 control connections (5 packets each), which got records and aged out after 10 s. **Gate miss cause:** sink choice. The 4 control connections took sinks 0 and 1 (5 frames each), and all 4 data flows hashed onto sinks 2 and 3 (`hash % sinks`), so two cores sealed everything.
- **Fix (not yet built):** `fastpath.c` now tracks a flow from its first conntrack-proven packet but installs a record only after `WGFP_ADMIT` = 32 slow-path packets. Later packets need no ct, because once the software flowtable has the flow its packets reach `wg_xmit()` without one (seen: conntrack `[OFFLOAD]` for the inner flow). A tracked flow that does not reach the threshold within 10 s is forgotten, so mice never take a record or a sink. Sinks are assigned round-robin per install instead of by tuple hash. This replaces the "learned only at the packet that triggers `flow add`" limit below.

### P3. RX fast path with skb reinjection (gate G3)

Install an outer-UDP record per peer endpoint that ENQs to a set of peer RX FQs. The worker:

1. validates the message type;
2. finds the keypair by receiver index;
3. checks the replay counter;
4. decrypts in place and strips the outer headers;
5. calls `build_skb` on the same buffer and `netif_receive_skb` on `wg0`.

Handshake and cookie messages pass to the stack unchanged.

**Gate G3:** `down` reaches at least 2.6 Gbit/s on four flows (software today: about 1.58), and the replay-window and out-of-order netns tests pass. **Kill:** below 2.2 Gbit/s. **Effort:** 2 to 3 weeks; it shares the keypair and replay code with P2b.

### P4. Offline-port reinjection (shared with IPsec Tier 2) (gate G4)

Enqueue the decrypted frame to the offline port's input FQ so FMan forwards the inner packet. The work starts by board-validating inject/capture patches 0183/0184, which are untested.

**Gate G4:** injected frames reach the LAN egress with correct L2 rewrite and TTL, with no FE leak and a clean `pcd-snapshot`. **Kill/defer:** if the offline-port FE path wedges or leaks, stop at P3. Decide on P4 jointly with `plans/ASK2-IPSEC-OFFLOAD-PLAN.md`, because both need this substrate. **Effort:** 4 to 8 weeks; this is the highest-risk phase.

### P5. Order restoration for a single inner flow (gate G5)

Do this only if a named workload needs more than about 1.5 Gbit/s per inner flow. Add `qman_enqueue_orp()` (about 40 lines in `qman.c`/`qman.h`; mainline 6.18.55 already has the `seqnum`, `orpc` and `AVOIDBLOCK` fields). Use an input FQ with ORP window 256 (less than the 8128 replay window), and count `ORPWINDOW_LATE/EARLY` rejects as drops.

**Gate G5:** one inner TCP flow reaches at least 2.2 Gbit/s with no extra reorder retransmits. **Kill:** a reject rate above 0.1% or any stall.

## 4. Cross-cutting work

- **Registration API between `wireguard` and `ask.ko`:** record insert/delete by key, FQ allocation and port lookup. It follows the ASK2 reversibility contract: every insert has an inverse, and `pcd-snapshot diff` is clean after disengage. P2b-2 and P3 both need it.
- **Security review of `fastpath.c`:**
  - no key material in debugfs or logs;
  - counter handling identical to `send.c`;
  - every upstream WireGuard security fix reviewed against the module patch, which is a permanent carry cost.
- **Harness:**
  - add a single-flow cell to `bin/testrig-wgfp-p2b.sh` and `bin/testrig-wireguard-multi.sh`;
  - add per-run FQ and record counters to the CSV;
  - make `bin/testrig-wireguard.sh`, and the other `testrig-*` scripts, refuse or loudly flag a DUT kernel with `PROVE_LOCKING`.
- **Release hygiene:** keep `latest.iso` on a production image, and name KUnit builds so they cannot be mistaken for releases.

## 5. Open items

Software path:

- [ ] Re-run the nine-cell matrix (`bin/wg-matrix.sh 185`) on the current shipping image (`tx_lanes=3`, 0551), and include the 5-rep vs 12-rep `.106` reference question for 1T `bidir`.
- [ ] Decide on a persistent form of `noqueue` for an unshaped WireGuard WAN port (E1 gave +7.9% on 1T `up`, +4% on `bidir`). Candidates: a udev rule or boot hook, or `IFF_NO_QUEUE` handling in `fsl_dpa`. This is a product decision.
- [ ] Run the upstream `netns.sh` selftest against `tx_lanes=3`; only a two-namespace veth smoke test has been done.
- [x] eth4 bidirectional RPS A/B on the current image (2026-11-02, `.185` image `2026.10.31-0511-rolling`, `bin/wg-rps-ab.sh` run on dell2, `PORTS=eth4 CELLS=wg-v4 DIRS="up down bidir" REPS=3`, masks eth3:eth4, RFS off in every non-default variant; csv `~/testrig/out/wgrps-*-185-eth4.csv` on dell2). Means in Gbit/s, up / down / bidir: default `e:e` 1.79 / 1.58 / 1.52 (repeat at the end: 1.79 / 1.61 / 1.53); vendorlike `2:1` 1.92 / 1.54 / 1.83; `2:4` 1.86 / 1.59 / 1.52; `4:2` 1.79 / 1.61 / 1.53; `e:1` 1.88 / 1.54 / 1.75 (bidir n=2, one rep returned 0 from a harness glitch). **The bidir gain comes only from steering the encrypted-leg port (eth4 here) to CPU0.** Per-port single-CPU masks that spare CPU0 gain nothing. `down` loses 2–3% with that steering and roughly triples retransmits (one core at 99.5%). Not shipped as a default: it is specific to whichever port carries the tunnel, it puts that port's software-fallback RX on CPU0 (the management-starvation case from 2026-08-19 that the `0xe` mask exists to prevent), and it caps that port's software forwarding at one core. If wanted, it belongs behind a per-interface knob, not a global default.
- [ ] eth3 at 10 reps (optional; the eth4 result above already decides the default question).
- [ ] Determine whether the 287k to 383k retransmits per run on `.106` come from WireGuard reordering or from dell-side loss.

Fast path:

- [ ] Explain the P2a plateau (see section 3).
- [ ] Determine the behaviour of a full sink FQ (no tail-drop configured; risk of an invisible A6-LOSS-style backlog) and localise the 5% loss at line rate (RX buffer pool of about 2,270 buffers; BMI discard counters are disabled on this image).
- [ ] Confirm the RX buffer headroom and tailroom on a live FD dump from `.185` (the F-203/F-222 order-0 buffer patches change buffer sizing).
- [ ] Find how pool-channel FQs are created in `dpaa_eth` (`dpaa_fq_setup`) so the per-peer FQ sets can reuse the helpers, and whether the portal count limits K.
- [ ] Fix the O(N^2) flow-stats refresh and check FE wedge history before multi-peer deployment.
- [ ] Check the `qman_enqueue_orp` verb and ORP-field encoding against LS1046A RM Chapter 3 (qdrant) before writing the P5 helper.
- [ ] Choose IPv6 outer UDP checksum handling: the FMan TX checksum command or a software sum.
- [ ] Decide whether to share the neighbour/route cache with `ask_neigh.c`.

## 6. Risks still open

| Risk | Phase | Containment |
|---|---|---|
| FE wedge or FE-index leak on new record shapes | P2b-2, P3, P4 | Existing record shape; one port at a time; cold boot; panic-on-lockup; `pcd-snapshot` diff |
| Full sink FQ backs up into the FE | P2b-2, P3 | Size the FQ and congestion group before enabling; explicit overload test |
| BMan depletion or double release | P2b to P4 | Release only through `dpaa_fd_release`; 10-minute soak with depletion counters |
| Divergence from upstream WireGuard semantics (rekey, roaming, timers, cookies) | P2b-3, P3 | Punt to the stack on any doubt; netns selftest on every change; slow path stays authoritative |
| Memory-bandwidth ceiling near 4 Gbit/s | P2c to P4 | Measure before quoting targets |
| No fast path for a peer behind a non-engaged port; ASK and VPP mutually exclusive per port | product | Documented; the fast path is an add-on for ASK-engaged ports |

## 7. Decision points

1. **Before P2b-2:** resolve the P2a plateau question.
2. **Before P3:** P2b-2 and P2b-3 must pass their gates, because P3 reuses the keypair, replay and registration code.
3. **Before P4:** decide jointly with the IPsec plan whether to build the offline-port reinjection substrate.
4. **Before P5:** go ahead only for a named single-flow workload.
5. **At any kill criterion:** stop that phase, keep everything already shipped and re-plan.

## 8. References

- Archived history: `plans/archive/ask2-wireguard-plan.md`, `plans/archive/ask2-wireguard-matrix-106-vs-185.md`, `plans/archive/ASK2-WIREGUARD-FASTPATH-PLAN-2026-10-15.md`.
- Related plans: `plans/ASK2-MASTER-PLAN.md`, `plans/ASK2-IPSEC-OFFLOAD-PLAN.md`, `plans/ASK2-PERFORMANCE-TEST-HARNESS.md`.
- Harnesses: `bin/testrig-wireguard.sh`, `bin/testrig-wireguard-multi.sh`, `bin/wg-matrix.sh`, `bin/testrig-wgfp-p1.sh`, `bin/testrig-wgfp-p2a.sh`, `bin/testrig-wgfp-p2b.sh`.
- Patches: board series 4011, 4013 to 4017; vyos-1x 056, 057; offline-port substrate 0175 to 0184; `0153-fman-pcd-fe-engage-api.patch`.
- Architecture references: `arch/fman-fe-ehash.md`, `arch/fman-microcode-210-programming-reference.md`, `specs/fman-keygen-flow-key-spec.md`, `specs/ask2-ipv6-dual-lane-key-design.md`.
