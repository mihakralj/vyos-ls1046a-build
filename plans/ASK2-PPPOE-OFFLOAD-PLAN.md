# ASK2 PPPoE hardware offload — recognition, fast-path reuse, and the soft-parser go/no-go

**2026-10-07 · dpaa1 · T-M6-SP1..SP4 / Gate A8 · Status: DRAFT; step 0 (fail-closed guard) implemented 2026-10-08, not board-validated; ONE CRITICAL BLOCKER UNRESOLVED.** The only PPPoE-specific code in this tree is the step-0 guard (§1a): `ask_flow_offload_replace()` refuses both directions of any flowtable flow with a PPPoE encap, so they stay on the kernel software path. That is safe, but every PPPoE-encapsulated frame (WAN-side traffic for the extremely common DSL/fiber-ISP PPPoE-client deployment) runs at full per-packet CPU cost today, zero hardware fast-path. This document sequences the work to close that gap, and states up front that Phase M6-C's own prerequisite (the FMan soft-parser execution unit) was investigated in depth this project (2026-09-04/05, `specs/ask2-soft-parser-lcv-scheme-select.md`) and left in a **genuinely unresolved, blocking state** — not a lookup gap, a live-silicon mystery. §2 is the most important section in this document; read it before estimating anything else here.

## 0. The scope fork (read this before anything else)

"PPPoE hardware offload" is three different projects of increasing depth, matching this project's existing Tier pattern (`ASK2-IPSEC-OFFLOAD-PLAN.md` §0):

- **Tier 1 — inner-flow fast-path reuse (the actual value for VyOS home/SMB routers).** The kernel's `pppoe`/`ppp_generic` netdev stack keeps doing PPPoE discovery, LCP/session negotiation, and encap/decap exactly as today — nothing about the PPP control plane moves to hardware. The only change: once a PPPoE session is UP, FMan's parser is taught to see *through* the PPPoE+PPP headers to the inner IPv4/IPv6 packet, so the **existing, already-shipping, already-silicon-proven** ehash flow-offload / NAT / routing fast path (`decomp/` FE-VM work, 7.3 Gbps unidirectional measured) can match and forward session data the same way it already does for un-encapsulated WAN traffic. This is the entire value proposition for a home-gateway/SMB router: PPPoE WAN links stop being a hardware-offload dead zone.
- **Tier 2 — vendor-depth PPPoE session table / relay.** NXP's real `cdx_sp.xml` mechanism (decoded in full below, §4) does not do what Tier 1 describes — it recognizes PPPoE, shifts the CC-tree base pointer (`$ccbase += 0x30`) into a dedicated **PPPoE relay hash table**, and dispatches non-LCP session traffic straight to the policer (`$nia=0x4C0000`), bypassing the normal IPv4/IPv6 CC-tree path entirely. This looks like a BRAS-style per-session bridging/policing feature (multiple concurrent PPPoE sessions multiplexed/policed by session ID), not a NAT/route fast path. ASK2 should **not** replicate this mechanism — `OFFLOAD-CAPABILITY-PLAN.md` §1.8 already recommends against it ("never load the vendor sequence unmodified… once the parser recognizes PPPoE, the forward is the lean inline path again"). Tier 2 is scoped out of this plan; revisit only if a concrete multi-session-bridging requirement appears (unlikely for a home/SMB CPE router, which normally runs exactly one PPPoE session on the WAN link).
- **Tier 3 — PPPoE control-plane acceleration (LCP, PADI/PADO/PADR/PADS).** Never a target. Discovery and link-control traffic is low-rate, latency-insensitive, and must stay on the kernel's `pppoe` state machine regardless of what else is built — this matches the vendor's own `cdx_sp.xml` behavior (`$FW[48:16]==0xc021` LCP frames are punted to the host unconditionally, never offloaded) and the master plan's explicit gate (`T-M6-SP4`: "discovery and LCP stay SW").

**This plan targets Tier 1 only.** Tier 1 is both the materially useful feature and the one gated by a single, well-defined open question (§2), rather than a large, open-ended vendor-parity undertaking.

## 1. Current state (verified in this tree)

- **Hard-parser native PPPoE/PPP recognition exists on this silicon** — not a soft-parser-only capability as earlier planning documents (`plans/archive/SOFT-PARSER-PPPOE.md`, written 2026-07-14) assumed. Confirmed two independent ways this session:
  - `specs/fman-keygen-flow-key-spec.md` §4.1: KeyGen EKFC bit 25 = `KG_SCH_KN_PPPSID` (PPPoE Session ID, 2B), bit 24 = `KG_SCH_KN_PPPID` (PPP protocol ID, 2B) — direct KeyGen extraction knobs for PPPoE/PPP fields, implying the hard parser already produces offsets/validity for these headers (KeyGen extraction codes operate on hard-parser-recognized header boundaries, not soft-parser output).
  - Vendor `GetPrsHdrNum()` slot table (qdrant, `fm_common.h`, already used by F-205's HXS work): `HEADER_TYPE_PPPoE = 3`, `HEADER_TYPE_PPP = 3` (same slot — PPPoE and PPP share an HXS stage) — a first-class hard-parser header type, same mechanism class as the already-silicon-proven IPv4 (slot 5) / IPv6 (slot 6) slots.
  - Reference-manual corroboration (qdrant, DPAA RM §5.8): "The hardware parser understands Ethernet II, 802.3/SNAP, VLAN, **PPPoE/PPP**, MPLS, IPv4, IPv6, tunneled IP, GREv0, Minimal Encapsulation, IPsec, and L4 TCP/UDP/SCTP/DCCP" — PPPoE/PPP listed alongside IPv4/IPv6 as a *hard*-parse protocol, not a soft-parser extension.
  - **What is genuinely unknown**: whether the hard parser, on recognizing PPPoE+PPP (IPCP/IP6CP-negotiated session, protocol ID `0x0021`/`0x0057`), *automatically continues parsing into the inner IPv4/IPv6 header* and populates `L3R`/`IPOffset_1`/the rest of the parse result for that inner header — the way it does for, say, a VLAN tag (parse continues past the tag transparently). If it does, KeyGen's existing GEC/EKFC extraction already has everything needed for Tier 1 and **no soft-parser code is needed at all**. If it does not (parsing stops at the PPP header, inner IP is opaque payload from the parser's point of view), Tier 1 genuinely needs the soft-parser `ccbase`-style re-dispatch mechanism — in which case it is blocked by §2. **This is the single highest-value, cheapest experiment to run before committing to either path — see §3.**
- **Safety was only half correct before 2026-10-08; see §1a.** The action parser in `ask_flow_offload.c` rejects `FLOW_ACTION_PPPOE_PUSH` (its `default:` arm), but the kernel emits that action only on the direction that egresses the PPPoE link. The direction that ingresses it carries no PPPoE information at all, so it was accepted. The standing rule (`ASK2-MASTER-PLAN.md` §4.6.1 rule 5, reject rather than guess when a capability isn't built) is now enforced for both directions by the step-0 guard.
- **Opcode numbers (corrected 2026-10-08).** The vendor FE-VM PPPoE opcodes are `STRIP_PPPoE_HDR 0x14`, `INSERT_PPPoE_HDR 0x43` and `REPLACE_PPPOE_HDR 0x45` (`/mnt/builds/ASK/patches/kernel/010-ask-fman-dpaa-ehash.patch:10090-10107`). The archived `0x24` claim is wrong: `0x24` is `DIP_V4` in ASK2's NAT set. `STRIP_ETH_HDR 0x11` is no longer missing: patch 0215 emits it in production VLAN records, behind `0x04` (it wedges the RX port if it is opcode 0). See §1b.
- **Vendor's real mechanism, fully decoded** (qdrant, 2026-08-17/08-19, from literal `specs/reference/nxp-ask-fmc/cdx_sp.xml`, 194 lines): `pppoeschema` (`prevproto=pppoe`, lines 2-25) — `$FW[48:16]==0xc021` (LCP control frame) → `$HPNIA=0x500002`, host-enqueue, `end_parse`. Otherwise (session data): `$NIA=0x4C0000` (policer engine dispatch), `$ccbase += 0x30` (a **hardcoded, FMC-assigned** offset into the PPPoE relay hash table built by `cdx_pcd.xml`'s table layout — the XML's own comment warns this offset must be re-checked if any new classification table is added upstream of it), `$shimr=0x40`, `end_parse`. **This confirms Tier 2's shape precisely**: the vendor never re-enters normal IPv4/IPv6 CC-tree classification for PPPoE session data at all — it diverts to a parallel relay/policer structure. Tier 1 (this plan) deliberately does **not** replicate `$ccbase += 0x30` or the `0x4C0000`/`0x500002` NIA literals; those are vendor-topology-specific and explicitly flagged as non-relocatable without full remapping (`ASK2-MASTER-PLAN.md` Phase M6-C "NEVER" rule).

## 1a. Step 0: the fail-closed guard (implemented 2026-10-08)

The 2026-10-08 source review (qdrant, "PPPoE implementation prep") found the gap above in linux 6.18.48:

- `nf_flow_rule_match()` (`net/netfilter/nf_flow_table_offload.c:121-143`) adds a match only for `ETH_P_8021Q` encaps. A tuple whose ingress came over PPPoE (`encap[i].proto == ETH_P_PPP_SES`) gets a plain inner-IP match with `meta.ingress_ifindex` set to the physical port (`nf_flow_table_path.c:107-131` walks `DEV_PATH_PPPOE` down to it). Nothing in the match carries the session ID, PPP protocol or concentrator MAC.
- `nf_flow_rule_route_common()` (`:715-733`) emits `FLOW_ACTION_PPPOE_PUSH` only for the direction that egresses PPPoE. There is no ingress pop action.
- `flow_offload_rule_add()` accepts the flow when either direction offloads.

So the PPPoE→LAN half used to reach `ask_fe_flow_insert()` as an ordinary 46-byte key in the physical port's table. Whether it could ever HIT depends on the §3 parser question. If the hard parser does expose the inner IP, frames would match an unqualified key and be rebuilt with plain L2. This was a conditional hazard and was never observed on the board.

The fix is `ask_flow_cookie_is_pppoe()` in `ask_flow_offload.c`, checked at the top of `ask_flow_offload_replace()`. The REPLACE cookie is `&flow->tuplehash[dir].tuple`, the same pointer `ask_z11_other_src_v4()` already dereferences behind a `virt_addr_valid()` guard. If either tuple has a `PPP_SES` encap within `encap_num`, the REPLACE returns `-EOPNOTSUPP` and both directions stay in software. KUnit case `ask_flow_offload_test_pppoe_cookie` covers plain, VLAN-only, VLAN-then-PPPoE on the other tuple only, and an `encap_num` bound. This guard stays in place until the session-aware key and per-direction PPPoE strip/insert are built and gated (§4 Phase D). Lifting it is the last step of the feature, not the first.

Board-validated 2026-10-08 on `.185` (image `2026.10.08-1643-rolling`, kernel `6.18.55-vyos`, commit `a2890a36`, warm boot after install). The existing flowtable lists only eth3/eth3.10/eth4/eth4.20; `pppoe10` does not need to be listed, because the kernel walks the PPPoE device path down to eth3. Forwarded traffic ran dell2 `10.99.2.113` → DUT eth4 → `pppoe10` → dell1 `ppp0` `10.99.50.1`. Temporary routes made this path symmetric: on dell2, `10.99.50.1/32 via 10.99.2.185`; on dell1, `ip rule from 10.99.50.1 to 10.99.2.0/24 lookup 150` with `10.99.2.0/24 dev ppp0` in table 150. Both were removed afterwards.

- **PPPoE flow:** iperf3 TCP `-P 2 -b 400M` for 10 s gave 800 Mbit/s with 29 retransmits. Every REPLACE logged `REPLACE PPPoE flow not offloaded (T-M6-SP4)`, conntrack showed no `[HW_OFFLOAD]` for 10.99.50.1, and `pppoe10` TX grew by about 1.04 GB, so the traffic went through the kernel PPPoE path.
- **Regression check:** a plain flow from dell2 to `10.99.1.112` over eth4→eth3, with the same setup, gave 3 `[HW_OFFLOAD]` conntrack entries and 6 `hw_insert OK` lines, with zero PPPoE rejects. It ran at 8.70 Gbit/s.
- **Health:** no new kernel errors, and the session stayed up.

## 1b. Vendor alignment (reviewed 2026-10-08 against the vendor source)

Sources: `/mnt/builds/ASK/cdx/cdx_ehash.c`, `/mnt/builds/ASK/dpa_app/files/etc/cdx_pcd.xml` and `cdx_sp.xml`, and the opcode table in `/mnt/builds/ASK/patches/kernel/010-ask-fman-dpaa-ehash.patch`.

**The vendor has two separate PPPoE mechanisms.**

1. **Routed/NAT (conntrack) path, `fill_actions()`.** A PPPoE session flow goes into the same per-port `tcp4`/`udp4` ehash table as any other flow (`get_table_type()`), with the same key: port ID plus the inner 5-tuple (`fill_key_info()`). The `cdx_pcd.xml` distributions extract it with `header_index="last"`. The key carries no session ID and no PPPoE marker. The opcode chain is:
   - PPPoE→LAN (decap): `05 PREEMPTIVE_CHECKS`, `04 UPDATE_ETH_RX_STATS`, `11 STRIP_ETH_HDR`, `12 STRIP_ALL_VLAN_HDRS` (always emitted), `14 STRIP_PPPoE_HDR {be32 stats_ptr}`, then NAT or `21` TTL, `41 INSERT_L2_HDR`, `01 ENQUEUE_PKT`.
   - LAN→PPPoE (encap): the same front half, then NAT/TTL, `43 INSERT_PPPoE_HDR {be32 stats_ptr, be32 (1<<28)|(1<<24)|(0<<16)|session_id}`, optional `42`, then `41 INSERT_L2_HDR` with EtherType `0x8864` and the concentrator MAC as destination, then `01`. The microcode computes the per-packet PPPoE length field; nothing static could, and HMCD has no PPPoE command.
2. **Relay path (Tier 2, out of scope).** `cdx_pppoe_dist` keys on {Ethernet source, EtherType, session ID} plus port into `cdx_pppoe_cc` (11-byte key), with `REPLACE_PPPOE_HDR 0x45`. `cdx_sp.xml`'s `pppoeschema` ends parsing for every non-LCP session frame and diverts it there (`$ccbase += 0x30`, `$nia=0x4C0000`). So while that soft parser runs, session frames never reach the `tcp4`/`udp4` distributions and the routed path cannot HIT. Whether the vendor's routed path is reachable in a deployment depends on which of the two actually runs on the hardware; that is unproven.

**What this means for ASK2.**

- **Tier 1 is the vendor's routed path.** It uses the same key class and the same opcode chain. ASK2's production VLAN path is already the vendor-exact inline chain (0209, 0215-0217: `05/04 11 12 21 42 41 01` at line rate), so PPPoE should be built the same way: insert `14` on decap, `43` on encap. Do not build a CC/HMTD PPPoE path. It cannot produce the PPPoE length field, and VLAN has already moved off CC/HMTD.
- **The vendor design assumes the hard parser exposes the inner IP.** The routed key can only match a PPPoE frame if the hard parser continues past PPPoE+PPP into the inner IP header, because the soft parser would have diverted the frame instead. That is a strong prior for a positive §3 result, but it is still a prior; F-258 measures it.
- **Required beyond the vendor, kept as ASK2 gates:**
  - **Session isolation.** The vendor key cannot tell a plain frame from a PPPoE frame with the same port and 5-tuple, so a plain frame would get `14` applied to it. ASK2 should classify PPPoE session frames into their own KG scheme and table (selected through the PPPoE/PPP parser slot, HXS 3), or put the session ID in the key. Choose after the probe.
  - **Teardown.** The kernel will not tear the flows down. `nf_flow_table_path.c:212` sets the flow's `iifidx` to the physical port, so `NETDEV_DOWN` on `pppoe10` (`nft_flow_offload.c:237-246`, `nf_flow_table_core.c:719-731`) never matches. ask.ko must flush its own PPPoE records on `NETDEV_DOWN`/`UNREGISTER` of the PPP netdev before any session reconnect can be accepted.
  - **MTU.** On encap records, the ENQUEUE MTU field must be the PPPoE MTU (1492 by default), not the physical port's, so oversize frames punt to software.
- **Order of work:** F-258 probe → classification and isolation choice → decap direction first (lift the step-0 guard only for the PPPoE→LAN tuple, chain `04 11 12 14 21 41 01`) → teardown flush → encap (`43`) → the §4 Phase D gates. The soft parser (§2, Phases A–C) is needed only if the probe is negative.

## 2. The critical blocker: the soft-parser execution unit is unverified on this silicon, after genuinely thorough investigation

This is not a restatement of "soft-parser work is unstarted" — it is a specific, already-attempted, still-open negative result that must be resolved (or routed around, §3) before any soft-parser-dependent PPPoE work (`T-M6-SP1`/`SP2`, and by extension `SP4`) can proceed. Full detail in `specs/ask2-soft-parser-lcv-scheme-select.md` §6a-6q; summary:

1. **Every static, register-level, byte-for-byte comparison against the real vendor driver source now matches.** Code-RAM base address (`0x01ac7000`), per-header entry table, `pmda[].ssa` trigger-bit encoding and value (`0x00000420` for IPv6 slot 6), the global parser soft-sequence execution-unit enable bit (`FMPR_RPIMAC`, correctly located at `FM_MM_PRS+0x844` after an initial addressing bug in `F_246.py` was found and fixed), and the real bytecode ISA (ground-truth NXP FMC Soft Parser Assembler source, `/tmp/kilo/fmc/source/spa/fm_sp_private.h` etc., independently cross-verified) — all confirmed correct, and confirmed to match the real, production `.116` reference board's live register and bytecode state byte-for-byte (§6p).
2. **A verified-correct, independently-encoded 8-byte bytecode sequence was loaded, armed, and triggered under every condition this project could construct**: on HXS slot 6 (the documented IPv6 entry, §6i/6j — silent), on HXS slot 0 (the ETH catch-all slot independently proven via a *different* signal, the 2026-08-19 per-slot LCV sweep, to activate on literally every transit frame — also silent, §6l), and with the global `FMPR_RPIMAC` execution-unit enable bit correctly set (§6o — also silent). Each test used a clean, distinguishable write target (first an LCV bit, found methodologically flawed; then a Parse-Result magic byte, methodologically clean) and left the board healthy (0 RX errors) throughout every attempt.
3. **The `.116` reference board — real NXP production hardware running the real vendor stack — was independently instrumented** (a vermagic-matched diagnostic kernel module, read-only kprobes) and found to have the identical final register/bytecode state this project's own model predicts, confirming the *model* is right. But the one signal that would prove *execution actually happens* (observing a real `FM_PCD_PrsLoadSw`/enable call fire on `.116`) could not be captured, because FMan hardware state on that board survives a warm (software) `reboot` — the vendor's own `dpa_app` appears to skip re-issuing the load/enable sequence when nothing has changed since the last genuine power-cycle, and `.116` has no remote power control to force one (§6q).

**Net position**: this project has done everything short of a genuine cold power-cycle of a real working reference board under live kprobe instrumentation, and still cannot show the soft-parser execution unit producing any observable effect on `.185`. This could mean the mechanism needs something beyond what static vendor-source comparison reveals (an undocumented errata/model requirement, or a hardware state only latched correctly within `fmc_execute()`'s single atomic transaction rather than this project's piecemeal debugfs writes), or it could mean `.185`'s specific board/firmware/microcode combination genuinely cannot run soft-parser code for a reason not yet found. **Do not re-attempt the register-comparison approach** — it is exhausted (§6n/6o's own conclusion). The only productive next moves on this specific thread are: (a) get physical/remote-power access to `.116` for one real cold boot with `fmtrace.ko` pre-staged (settles the question directly, on real hardware); or (b) treat soft-parser as blocked indefinitely and route around it entirely, which is what §3 proposes for PPPoE specifically.

## 3. The cheap test that could make §2 irrelevant for PPPoE: hard-parser-only inner-IP exposure

Nothing in this project's existing planning documents (`ASK2-MASTER-PLAN.md` Phase M6-C, `OFFLOAD-CAPABILITY-PLAN.md` §1.8, the archived `SOFT-PARSER-PPPOE.md`) considered this angle — all of them inherited the vendor's own soft-parser-based mechanism as the assumed implementation path. But §1's finding (PPPoE/PPP is a *hard*-parser header type on this silicon, slot 3, with dedicated KeyGen extraction knobs) means there is a real chance the soft-parser is not required for Tier 1 at all.

**Proposed first step, before any soft-parser-dependent work begins:** a parser probe. It is **not** read-only or zero-risk as the first draft of this section said, and the existing tools cannot run it on the current rig as-is:

- `F_239` captures only eth1 RX when `hash_offset >= 0x28`, while the PPPoE session runs on eth3.
- `F_241` probe3 writes RICP and installs/detaches KG/CC for about 300 ms. Its header records about 1500 RX errors from an earlier wide-window attempt.

**Chosen method (2026-10-08): F-258.** `bin/kernel-fixups/F_258.py` widens F-239's predicate to also match any port's contiguous frame with EtherType `0x8864`. The probe then runs on the existing eth3 rig, still passive and read-only. It is safe because PPPoE frames always MISS today (step-0 guard), so they reach `rx_default_dqrr`. Read it with `sudo cat /sys/kernel/debug/fman_pcd/0/probe2`; the dump shows the 32-byte parse result, the timestamp, the KG hash and the first ~128 bytes of the frame. Keep eth1 idle while reading, since it shares the latch. A cold boot is preferred (AGENTS S6 §10.9) but not required for a read-only capture. The steps:

1. Generate a real PPPoE session on the sacrificial test port (or capture naturally-occurring PPPoE traffic if a WAN-side PPPoE session is available in the lab) carrying a plain TCP or UDP payload over IPv4 or IPv6.
2. Capture the full 32-byte Parse Result (`struct fman_prs_result`) for such a frame via the existing `probe2`/`probe3` tooling.
3. Decode `L3R`/`L4R`/the header-offset fields exactly as already done for every other protocol this project has probed (IPv4, IPv6, VLAN, bridge L2). The question has one of three answers:
   - **The parser reports a valid L3 offset pointing past the PPPoE+PPP headers, at the real inner IPv4/IPv6 header, with correct `L3R` family bits.** This is the best case: the hard parser already de-encapsulates PPPoE/PPP transparently (the same way it does for a VLAN tag), and KeyGen's existing GEC/EKFC extraction can almost certainly be pointed at the inner header with no soft-parser code at all. It sidesteps §2, but it does not mean the existing path can be reused unchanged. Still required: a key bound to the session (session ID plus a peer/underlay discriminator, because session-ID reuse across reconnects must MISS), synchronous invalidation of hardware records on session teardown/reconnect, per-direction FE actions that strip and insert the PPPoE header, and PPP-netdev support in `ask_hw_resolve_iif_port()`/`resolve_oif_fqid()` (`ask_hw.c`, which today handle only DPAA ports and VLANs).
   - **The parser stops at the PPP header; the inner IP is opaque payload (no L3R set, or L3R reflects "unknown").** This is the case the vendor's own `cdx_sp.xml` mechanism implies (why would NXP write a soft-parser PPPoE schema at all if the hard parser already did this?) — in which case Tier 1 for this silicon genuinely needs either the soft-parser `ccbase`-slide mechanism (blocked on §2) or some other re-dispatch trick not yet identified.
   - **Something unexpected** (e.g. the parser reports PPPoE recognized at slot 3 but garbage/stale offsets beyond it, a silicon quirk analogous to several already found this project) — would need its own forensics pass, same discipline as every other register-level investigation in this codebase.
4. **Gate before writing any code**: this probe must be run and its result recorded (qdrant + this document) before committing to either the soft-parser-dependent path (§4, Phase A) or a hard-parser-only path (a new, not-yet-written Phase, shorter than §4 if the first bullet above holds).

This is a half-day-scale investigation (probe/decode only, no new kernel infrastructure), dramatically cheaper than either resolving §2 or building the full soft-parser loader/compiler pipeline (`T-M6-SP1`/`SP2`), and should be the literal next action on this feature — not the phased plan below, which assumes (conservatively, and per the master plan's existing task breakdown) that soft-parser involvement will be needed.

### 3.1 Result (2026-10-08): POSITIVE, the hard parser exposes the inner IP

Measured with F-258 on `.185` (image `2026.10.08-1735-rolling`, kernel `6.18.55-vyos`, commit `09b5127b`), on frames arriving on eth3 through the `pppoe10` session. Parse result offsets are bytes into the 32-byte `struct fman_prs_result`; the frame starts at probe offset +48.

| Frame | `l2r` | `l3r` | `l4r` | `nxthdr` | `etype_off` | `pppoe_off` | `ip_off[0..1]` | `l4_off` | `nxthdr_off` |
|---|---|---|---|---|---|---|---|---|---|
| LCP echo (PPP `c021`) | `0x8880` | `0x0000` | `0x00` | `0xc021` | `0x0c` | `0x0e` | `ff ff` | `ff` | `0x16` |
| ICMP in IPv4 (PPP `0021`) | `0x8800` | `0x8080` | `0x00` | `0x0001` | `0x0c` | `0x0e` | `16 16` | `ff` | `0x2a` |
| TCP in IPv4 (PPP `0021`) | `0x8800` | `0x8000` | `0x2d` | — | `0x0c` | `0x0e` | `16 16` | `0x2a` | `0x4a` |

- The parser recognises PPPoE (`pppoe_off = 0x0e`) and continues through PPP protocol `0x0021` into the inner IPv4 header at frame+22 (`ip_off = 0x16`; the frame bytes there are `45 b8`) and into TCP at frame+42 (`l4_off = 0x2a`; source port `00 16`). For LCP it stops with `nxthdr = 0xc021`, so LCP frames have no L3 and cannot match a flow record.
- **The KeyGen key is the production key.** The KG hash captured for the TCP frame (`0xe4a5f1ea44b82104`) equals `crc64_raw` of the 46-byte dual-lane key from `ask_fe_build_key_dual()` for the inner 5-tuple (`0x40`, 10.99.50.1 → 10.99.50.15, TCP, 22 → 52898). So on this silicon a PPPoE session frame produces exactly the key a plain frame with the same 5-tuple produces.

Consequences:
1. **The soft parser is not needed for Tier 1.** §2 and Phases A–C are off the PPPoE critical path. They stay relevant only for TTL punt and tunnels.
2. **The step-0 hazard was real, not conditional.** Before `a2890a36`, a PPPoE→LAN record keyed on the inner 5-tuple would have HIT and been forwarded without stripping PPPoE. The guard was necessary.
3. **Isolation is now a concrete design decision (§1b).** The key cannot tell PPPoE frames from plain frames. The only difference visible to the parser is `l2r` (`0x88xx` for PPPoE, `0x80xx` for plain Ethernet, and different again for VLAN). The options are listed in §3.2.

### 3.2 Isolation: chosen design (2026-10-08) — the hardware key carries the kernel's flow identity

The kernel flowtable identifies a flow by ingress port + encapsulation (VLAN IDs, PPPoE session) + inner 5-tuple. The per-port table already gives the port and the 46-byte dual-lane key the 5-tuple, so the missing piece is the encapsulation. That gap caused the PPPoE collision measured in §3.1, and the same latent bug exists for VLAN: a VLAN-pop record could be hit by an untagged frame with the same 5-tuple. Fixing it once, generically, closes both.

**F-259** (`bin/kernel-fixups/F_259.py`, with the ask.ko changes in the same commit):
- The routed key grows from 46 to **50 bytes** by appending two GEC extractions on the ehash FE scheme only (`next_engine == 3`):
  - `gec[6] = 0x81FF0500`: `KG_SCH_GEN_VLAN1`, validated, header +0 (the VLAN header starts at the TCI), 2 bytes, **unmasked**, giving the full outer TCI in key `[46..47]`. ask.ko writes PCP = DEI = 0, so priority-marked tagged frames MISS and stay in software. To mask PCP/DEI in hardware instead, use the `kgse_bmch`/`kgse_bmcl` bit-mask commands (follow-up).
  - `gec[7] = 0x81FF0802`: `KG_SCH_GEN_PPP`, validated, header +2, 2 bytes, giving the PPPoE session ID in key `[48..49]`.

  Validated codes substitute the zeroed default register when the header is absent, so plain frames carry 0/0. That is the same mechanism that zero-fills the absent IPv4/IPv6 lane (silicon-proven). A header code's base is the first byte after the EtherType that announced it, which is **not** the parse-result offset for VLAN (`vlan_off` points at the TPID).

**Silicon results, first build (`62cb81d9`, image `2026.10.08-1847-rolling`, warm boot after install):**
- **Plain flows:** port↔port v4/v6 HIT at 9.39/9.24 Gbit/s, with 331 `rx_default_dqrr` calls per 10 s run. So the 50-byte key with a `00 00 00 00` tail matches, and both new GECs yield 0 for plain frames.
- **PPPoE:** the probe2 KG hash of a PPPoE TCP frame (`0x5a40bb5250ea9773`) equals `crc64_raw` of the 50-byte key with session ID `0x0007` at `[48..49]`. The PPP code at +2 is correct.
- **VLAN, first attempt wrong:** with `gec[6] = 0x810F0502` (+2), tagged flows all MISSed (about 3.15M `rx_default_dqrr` calls per 10 s; vlan↔port 3.6, vlan↔vlan 2.7 Gbit/s). The installed record's bucket decoded to VID `0x000a`, so the record side was right. Candidate stats records for one fixed UDP tuple (`10.99.10.112:47001 → 10.99.2.113:47002`, `tbl[3]`): `0x0800` took 20/20 packets; `0x000a`, `0x0100`, `0x0000` and `0x8100` took 0. +2 therefore reads the inner EtherType, and the fix is offset 0 (`0x810F0500`).

**Second build (`85223395`, image `2026.10.08-1910-rolling`, cold boot):**
- port↔port v4/v6 and vlan→port v4/v6 are all in hardware (145–162 `rx_default_dqrr` calls per 10 s; 9.36/9.25/9.35/8.39 Gbit/s).
- vlan↔vlan still had 1.26M/1.49M software calls per 10 s. eth3's VLAN-10 records HIT (4.6M packets); eth4's VLAN-20 ACK record (bucket decoded to VID `0x0014`) took 0.
- A BTF kprobe on `netif_receive_skb` logged the eth4 VLAN-20 probe frames' `skb->hash` (top 32 bits of the KG CRC-64) as `0x015C749F`. The only 4-byte key tail matching it is `00 04 00 00`.
- So the GEC mask byte `0x0F` was applied to the **last** byte of the 2-byte extraction (`0x14` → `0x04`), not the first. VID 10 (`0x0a`) had survived the mask unchanged.
- Fix: no mask (`0x81FF0500`).

**Third build (`33c9c606`, image `2026.10.08-2003-rolling`): ALL F-259 ACCEPTANCE GATES PASS.**

| Combo | Unidir Gbit/s | `rx_default_dqrr` / 10 s | Bidir Gbit/s | `rx_default_dqrr` / 10 s |
|---|---|---|---|---|
| port↔port v4 | 9.38 | 163 | 17.4 | 316 |
| port↔port v6 | 9.25 / 9.24 (one 7.38 outlier, still HW) | 143–163 | — | — |
| vlan→port v4 | 9.35 | 162 | — | — |
| vlan→port v6 | 9.23 | 322 | — | — |
| vlan↔vlan v4 | 9.35 | 194 | 16.3 | 166 |
| vlan↔vlan v6 | 9.21 | 234 | 16.7 | 169 |

Every combo is in hardware. Bidir is above the 2026-10-05 ASK2 baseline (port↔port 15.5, vlan↔vlan 15.7) and at vendor level (16.8 / 16.9).

- **Isolation (gate 4).** Two stats records on eth3's table for the same UDP 5-tuple (`10.99.10.112:47011 → 10.99.2.113:47012`): A keyed VID 10, B keyed untagged. dell1 sent that tuple raw on `enp1s0`, 10 frames tagged VLAN 10 and 7 untagged; its NIC counted exactly 17 TX. Round 2: A +10 (110-byte frames only), B +7 (106-byte frames only). Round 1 was 8/7 (start-up loss, no cross-matching). Before F-259 both frames had the same key.
- **Health.** No kernel errors, bus errors or SYNC timeouts; the only pattern hit is the boot-time ramoops reserved-memory line.
- **Not yet covered.** The A6/churn soak with the 50-byte key, and a priority-marked (PCP ≠ 0) tagged flow, which is expected to stay in software.
- Why not the parse-result `l2r` byte: GEC code `0x20` (parse result) emits 0 in AC_CC mode (qdrant 2026-09-03), which is why F-243 moved the family byte to a frame-header code.
- **One size constant.** `FMAN_PCD_FE_ROUTED_KEY_SIZE` (50) in `include/linux/fsl/fman_pcd.h` sizes the ROUTED profile, the default ehash table and the 0194/0198 ACL key buffers. ask.ko's `ASK_FE_KEY_SIZE_DUAL` `static_assert`s against it, so the two can't drift.
- **ask.ko.** `ask_fe_build_key_dual()` writes `vlan_ingress_vid & VLAN_VID_MASK` and the new `pppoe_sid` (0 while the step-0 guard stands) big-endian at `[46..49]`. KUnit case `ask_flow_offload_test_fe_key_l2_context`.
- **Record layout.** Opcodes move from +56 to +60. The worst-case parameter end (NAT66 + VLAN translate) is +184, under the +256 stats block.
- **Behaviour change.** ethtool/tc-flower ACL records keep 0/0 in the new bytes, so they match untagged, non-PPPoE frames only.
- **Rejected alternatives.**
  - (a) The vendor key: incomplete, and leaves a spoofed plain frame running a strip opcode.
  - (c) A separate PPPoE KG scheme and table: this project already chose one unified key over per-family schemes (F-224 replaced the planned separate v6 scheme) because LCV scheme selection proved fragile (F-205/F-212).

**Silicon acceptance (before any PPPoE record is built):**
1. Plain v4/v6 routed flows still HIT at line rate. The probe2/F-258 KG hash of a plain frame equals `crc64_raw` of the 50-byte key with `00 00 00 00` tail.
2. A tagged flow's KG hash matches the key with its VID at `[46..47]`, and VLAN-pop flows still HIT. If the hash comes out wrong, the VLAN1 offset base is the suspect (TCI at header +0 instead of +2).
3. A PPPoE frame's KG hash matches the key with its session ID at `[48..49]`.
4. Isolation: a plain frame with a tagged flow's 5-tuple MISSes.
5. The A6/churn regression gates are unchanged.

### 3.3 Decap direction (PPPoE→LAN): implemented 2026-10-08, not yet board-tested

- **Kernel, F-260** (`bin/kernel-fixups/F_260.py`): a new L2-edit flag `FMAN_PCD_VLANF_PPPOE_STRIP` (bit 2 of `vlan_flags`) makes the inline record emitter take the VLAN front half `04 11 12` (12 with VID 0) and then emit `14 STRIP_PPPoE_HDR`. Its 4-byte stats pointer points at the owned 0216 scratch block, never 0. The record is `04 11 12 14 21 41 01` for IPv4 and `… 14 29 41 01` for IPv6, with NAT opcodes in place of `21`/`29` when the flow is NATed.
- **ask.ko:**
  - `ask_flow_cookie_pppoe()` replaces the step-0 guard. It returns 1 only for a decap direction: this tuple's only encap is `PPP_SES` with a non-zero session ID, and the other tuple has no PPPoE.
  - The encap direction, PPPoE over VLAN and session 0 return `-EOPNOTSUPP` and stay in software.
  - Decap flows are offloaded only with `ask.pppoe_offload=Y`; it is a runtime toggle, off by default. Turn it on with `echo Y | sudo tee /sys/module/ask/parameters/pppoe_offload`.
  - For a decap flow the replace path sets `key.pppoe_sid`, forces `vlan_ingress_vid = 0` and sets `ASK_VLANF_PPPOE_STRIP`. The VLAN per-port gate now applies only to POP/PUSH.
  - `static_assert`s tie the flag value and key size to the kernel's.
  - KUnit: `ask_flow_offload_test_pppoe_cookie`, rewritten for the classifier.
- **Teardown is deliberately deferred to the encap step.** A decap record carries nothing session-specific apart from its key, which includes the session ID. After a reconnect, new-session frames therefore MISS. If a session ID happens to be reused, the old record still strips the PPPoE header and forwards to the same LAN next hop, which is correct. Stale decap records only linger until the kernel flow times out and DESTROY removes them. The encap record (`43`, session ID and concentrator MAC inside) is the one that must be flushed on `pppoe10` down.
- **Board test:**
  - **Traffic:** a forwarded dell1 `10.99.50.1` (`ppp0`) → DUT `pppoe10`/eth3 → eth4 → dell2 `10.99.2.113` flow. It needs the temporary routes from §1a: on dell2, `10.99.50.1/32 via 10.99.2.185`; on dell1, rule `from 10.99.50.1 to 10.99.2.0/24 lookup 150` with `10.99.2.0/24 dev ppp0`.
  - **Order:** cold boot, then first one flow at low rate with `pppoe_offload=Y`.
  - **Pass:** the eth3 record's `pkt_count` climbs, kernel RX on the data direction stays flat, and dell2 receives intact frames, checked with `tcpdump` (no PPPoE header left, TTL decremented).
  - **Then:** throughput, then session flap.
  - **Wedge risk:** `0x14` has never executed on this board. If eth3 goes RX-deaf, recovery is a cold power cycle.

## 3a. Test rig: live PPPoE session now stood up and verified (2026-10-07)

The standard dell1/dell2 ↔ DUT throughput rig (`ASK2-PERFORMANCE-TEST-HARNESS.md`) had no PPPoE capability — it only drives raw routed/VLAN IPv4 combos. This gap is closed: **dell1 now runs a real software PPPoE access concentrator on its DUT-facing link, and the DUT runs a real PPPoE client session against it**, giving this feature a working, repeatable test rig before any offload code exists.

**Setup (dell1, `admin@192.168.1.112`, interface `enp1s0` — the same link that carries `10.99.1.0/24` to DUT eth3):**

- Package: `pppoe` (Roaring Penguin rp-pppoe 4.0, Debian `pppoe` package — provides `pppoe-server`).
- `/etc/ppp/pppoe-server-options`: `require-chap`, `noccp`, `novj`/`novjccomp`, `nobsdcomp`, `nodeflate`, `mtu 1492`, `mru 1492`, `lcp-echo-interval 10`/`lcp-echo-failure 3`, `ms-dns 8.8.8.8`. (Do **not** add `login` — it forces PAM/system-account PAP and conflicts with `require-chap` + `chap-secrets`.)
- `/etc/ppp/chap-secrets`: one test credential, `"asktest" * "asktest123" *`.
- Persistent systemd unit `/etc/systemd/system/ask2-pppoe-server.service` (`Type=forking`, `PIDFile=/var/run/pppoe-server.pid`, `Restart=on-failure`), **enabled and running** — survives dell1 reboot:
  ```
  ExecStart=/usr/sbin/pppoe-server -I enp1s0 -L 10.99.50.1 -R 10.99.50.10 -N 10 -C ASK2-TESTRIG -X /var/run/pppoe-server.pid -O /etc/ppp/pppoe-server-options
  ```
  Pool `10.99.50.0/24` is deliberately distinct from the `10.99.1.0/24` and `10.99.101.0/24` addresses already assigned directly to eth3 for the routed/VLAN combo matrix, so this coexists without touching that harness.

**Setup (DUT, `vyos@192.168.1.185`), via the standard vbash-script-over-SCP method (interactive `configure` hangs on this board per the project's own operating rules):**

```
set interfaces pppoe pppoe10 source-interface eth3
set interfaces pppoe pppoe10 authentication user 'asktest'
set interfaces pppoe pppoe10 authentication password 'asktest123'
set interfaces pppoe pppoe10 no-peer-dns
set interfaces pppoe pppoe10 no-default-route
set interfaces pppoe pppoe10 mtu 1492
```

Note the current VyOS schema is `interfaces pppoe <name>` as its own top-level interface type with `source-interface`, **not** the older `interfaces ethernet eth3 pppoe <unit>` nesting — the latter is rejected (`is not valid`) on this tree's VyOS version.

**Verified results (2026-10-07, image `2026.10.07-1743-rolling`):**

- Session establishes cleanly: DUT `pppoe10` gets `10.99.50.10/32` peer `10.99.50.1/32`, confirmed both via `show interfaces pppoe` and a live `pppd` process (`call pppoe10`) on the DUT.
- ICMP and real TCP traffic both pass end-to-end: `iperf` (TCP) through the tunnel sustained **~1.0–1.07 Gbit/s** over an 8 s run. That traffic terminates on the DUT and on dell1, so it is local PPPoE traffic, not forwarded LAN→DUT→PPPoE flowtable traffic, and it is **not** a pre-offload baseline. A real baseline needs a routed/NAT endpoint behind the DUT (dell2), IPv4 and IPv6, both directions, PPPoE-over-VLAN, and session churn including same-session-ID and peer changes, with kernel-RX and wire-byte proof.
- **Reconnect/churn behavior confirmed working**: killing the concentrator process (dell1-side) and restarting it drops the session; the DUT's `pppd` (`persist`, `holdoff 30`) does **not** need any manual intervention — it automatically re-dials and re-establishes a fresh session within the 30 s holdoff window, with a new `pppoe10` interface instance (`renamed from ppp0` observed twice in DUT `dmesg`). This is a first real data point for the "session churn/reconnect correctness" gate flagged in §5 as more important than throughput — worth deliberately exercising harder (rapid flap, concentrator-side session kill while traffic is in flight) once real offload state exists to check for stale hardware state across a reconnect.
- The existing `ask` neighbor-resolution code already logs activity against the `pppoe10` netdevice (`ask: neigh: resolved dev=pppoe10 ifindex=... dst_ip=0.0.0.0`), confirming ASK2's device-agnostic plumbing sees the interface today even with PPPoE encap/offload itself not implemented — consistent with §1's "already safely rejects" finding.

**Current state left in place:** the dell1 concentrator service and the DUT `pppoe10` client config are both left configured and running (not torn down) so this is ready for immediate reuse — install a fresh image, the DUT config persists via `save`, and dell1's systemd unit auto-starts. To temporarily disable without deleting config: `sudo systemctl stop ask2-pppoe-server.service` on dell1, or `set interfaces pppoe pppoe10 disable` on the DUT.

This closes the open question from the prior session ("will our testing gear allow us to test PPPoE hw offload?") — **yes**, and it now does, with zero new hardware and a reusable, documented, persistent setup.

## 4. Phased implementation plan (if §3 confirms soft-parser involvement is required)

This section inherits and elaborates `ASK2-MASTER-PLAN.md` Phase M6-C's existing task IDs (`T-M6-SP1`..`SP4`) and `ASK2-REWRITE-PLAN.md`'s "Gate: A8" — it does not replace them, it is the detailed execution plan for `T-M6-SP4` specifically, built on top of the shared soft-parser infra `SP1`-`SP3` already scope.

### Phase A — resolve §2 (prerequisite, shared with TTL-punt/tunnel work, not PPPoE-specific)
- Obtain a genuine cold power-cycle capability for `.116` (remote smart plug, matching `.185`'s Hubitat setup, or scheduled physical access) and capture `FM_PCD_PrsLoadSw`/enable-sequence kprobe output from true power-on, settling whether the vendor's own mechanism only works because of some state never exercised by this project's piecemeal debugfs test sequence.
- Alternative/parallel: review whether `fmc_execute()`'s *atomic* transaction (disable PCD globally → load everything, every port's `pmda[].ssa`, soft-parser code, and the parser-level enable bit, in one sequence → re-enable) differs in a way a single combined kernel commit (vs. this project's separate, time-spaced debugfs writes) could test cheaply — i.e. try one more combined-write experiment on `.185` before concluding `.116` access is strictly required.
- **Gate**: either a confirmed-working soft-parser hook (any protocol, not necessarily PPPoE — the IPv6-LCV PoC this was originally built for is an equally valid proof) observed live, or a documented, specific root cause for why it cannot work on this board/firmware. Without one of these two outcomes, do not proceed past this phase for any soft-parser-dependent capability, PPPoE included.

### Phase B — T-M6-SP1/SP2: compiler artifact + typed API (shared infra, not PPPoE-specific)
- Locate/build the vendor NetPDL→soft-sequence compiler path (FMC/fmlib) and compile `cdx_sp.xml` (or a minimal ASK2-authored replacement NetPDL source covering only what Tier 1 needs — likely preferable, since the vendor file's other six schemas (OH Ethernet correction, 6-in-4 re-dispatch, UDP/TCP control-plane punts, ESP steering) are out of scope here and each carries its own relocation risk).
- Add `fman_pcd_prs_load()`/`_readback()`/`_attach_port()`/`_detach_port()`/`_free()` as real, production (not `cc_test` debugfs scratch) kernel API, building on the now-validated-correct addressing/encoding from the `F_243`-`F_246` investigation (`specs/ask2-soft-parser-lcv-scheme-select.md` §6g/6k/6o) but as owned, lifecycle-managed infrastructure rather than a diagnostic PoC.
- **Gate** (verbatim from master plan): compiler output equals live programmed bytes; every source schema has a mapped byte range; dormant load/readback byte-exact; attach/detach restores the full parser register/MURAM baseline; malformed image rejected before write.

### Phase C — T-M6-SP3: minimal safety sequence (shared infra — do this before PPPoE specifically)
- Port only the TTL/hop-limit-punt and TCP SYN/FIN/RST-punt schemas from `cdx_sp.xml` first, as the master plan already specifies — this both validates the new infra on something smaller than PPPoE and delivers independent value (conntrack-visible connection setup/teardown even for hardware-offloaded flows, correct TTL-expired ICMP generation) regardless of whether PPPoE ships.
- **Gate**: TTL 0/1 and hop-limit 0/1 reach the kernel ICMP path; TCP SYN/FIN/RST remain visible to conntrack; established data may still offload; no semantic change for traffic that doesn't hit these conditions.

### Phase D — T-M6-SP4: PPPoE recognition and inner-flow exposure
- Author a minimal NetPDL (or hand-assembled bytecode, following the exact ground-truth encoding process `F_243`/`F_244` already validated) `pppoeschema`-equivalent that:
  - Punts PPPoE discovery (PADI/PADO/PADR/PADS, `ethertype 0x8863`) and LCP/session-control frames (`ethertype 0x8864`, PPP protocol `0xc021`/`0x8021`/`0x8057` — LCP/IPCP/IP6CP) unconditionally to the kernel path, matching the vendor's own `$FW[48:16]==0xc021` check, generalized to cover IPCP/IP6CP as well (the vendor file only checked LCP; Tier 1 needs session *establishment* visible too, not just link-control).
  - For session data frames (PPP protocol `0x0021` IPv4 or `0x0057` IPv6): re-expose the inner header to KeyGen/CC-tree classification. **Do not replicate `$ccbase += 0x30`/the vendor relay table** (note that the vendor also has a second, routed mechanism: `cdx_ehash.c` `fill_actions()` builds PPPoE strip/insert around NAT/TTL and the L2 rebuild, separately from `fill_pppoe_relay_actions()`; its relay key is ingress port + Ethernet source + EtherType `0x8864` + session ID, not session ID alone. Whether the vendor's routed branch is reachable in deployment is unproven) (§0, Tier 2 explicitly out of scope) — instead, the target mechanism is re-pointing the classification base at the inner IP header so the *existing* IPv4/IPv6 KG scheme + CC-tree + FE-VM record path runs unmodified, exactly as `OFFLOAD-CAPABILITY-PLAN.md` §1.8 already recommends ("the forward is the lean inline path again"). The precise soft-parser instruction(s) to achieve this (likely an `L3` field/offset override rather than a `ccbase` add, since ASK2 doesn't use the vendor's CC-tree layout) is new design work, not yet scoped at the bytecode level — do this only after Phase A/B/C are solid, since it is the one genuinely novel piece.
  - Session-ID binding: a PPPoE session only becomes a routable netdev (`ppp0`) after LCP/IPCP negotiation completes in the kernel; the HW hook therefore needs *some* way to know a given PPPoE session ID currently corresponds to an active, NAT/route-eligible session before it's safe to fast-path its data frames — otherwise a session ID reused (session renumbering is common on reconnect) could stale-match. The vendor's `KG_SCH_KN_PPPSID` extraction knob (§1) is the natural mechanism: include PPPoE session ID as a KeyGen-extracted, scheme-matched field alongside the inner 5-tuple, so a stale/wrong-session key simply misses (safe) rather than matching wrong state.
- Extend `ask_flow_offload.c`'s flow-action handling (currently an unconditional reject at line ~1427-1431) to accept PPPoE-push/pop-implying flows once the above is built and verified, following the exact same "typed, exhaustively-matched action set, reject anything unrecognized" discipline already used for the ETH/IP4/IP6/TCP/UDP mangle cases immediately preceding it in that function.
- **Gate** (verbatim from master plan, this is the authoritative acceptance bar): discovery and LCP stay SW; session TCP/UDP route/NAT traffic offloads through the existing ehash fast path; session reconnect and session-ID change handled correctly (no stale-session cross-match); MTU/MRU correctness (PPPoE's 6-byte header + 2-byte PPP protocol field, 8 bytes in total, reduces usable MTU from 1500 to 1492 — the HW record must not silently forward over-MTU encapsulated frames); clean teardown (session end tears down the HW-side binding, not just the kernel netdev); unsupported-PPP-protocol fallback (anything other than IPCP-negotiated IPv4/IPv6 session data, e.g. a vendor-proprietary PPP NCP, falls back to kernel SW cleanly); and a 10,000-session-cycle churn test (reusing this project's existing `churn.sh`-class silicon-validation harness, extended to drive repeated PPPoE connect/reconnect/disconnect cycles rather than only VLAN/port/family combinations) with zero stalls, zero misforwards, and stable MURAM budget.

## 5. Risks and open questions specific to PPPoE (beyond the shared soft-parser risk in §2)

- **MTU accounting.** PPPoE's 6-byte header plus 2-byte PPP protocol ID (8 bytes in total, `include/uapi/linux/if_pppox.h`) shrinks the effective payload MTU versus the underlying Ethernet MTU (standard PPPoE MTU is 1492 against a 1500-byte Ethernet link). Any HW record touching inner-IP fields for a PPPoE-encapsulated flow must respect the PPPoE-adjusted MTU, not the physical port's. This project's existing DPAA1 XDP/AF_XDP MTU ceiling (3290 bytes, per AGENTS.md) and VPP's ~3304-byte ceiling are unrelated numeric constraints that happen to already be well above standard Ethernet MTU, so this is a correctness concern (don't forward an oversized frame), not a capacity one.
- **Session churn correctness is the most important gate, not throughput.** A home/SMB router's PPPoE WAN link reconnects periodically (ISP-forced disconnects, DHCP/PPP lease renewal, modem resets are common) — far more frequently than, say, a VLAN configuration changing. Any HW-side PPPoE session binding that doesn't tear down and rebind cleanly on reconnect, or that cross-matches a new session reusing an old session ID, is a user-visible, intermittent routing-failure bug, worse than simply not offloading at all. This is why the master plan's own gate list puts "reconnect/session-ID change" ahead of raw throughput measurement.
- **Multiple concurrent PPPoE sessions.** A home/SMB CPE normally runs exactly one PPPoE WAN session. If VyOS's broader use cases ever require multiple concurrent PPPoE sessions (e.g. a BRAS-adjacent role), that drifts toward Tier 2 territory (§0) and should be re-scoped as a separate, explicitly-justified project rather than silently expanding this one.
- **Interaction with existing opcode work.** `STRIP_ETH_HDR 0x11` already ships in VLAN records (0215), with its ordering constraint (never opcode 0). PPPoE records reuse the same builder front half (`04 11 12`) and add only `14`/`43`. Those two have never run on this board, so bring each up the way 0215 was: live record bisection on one flow with traffic paused across the write, after a cold boot.

## 6. Relationship to existing planning documents

- `plans/ASK2-MASTER-PLAN.md` Phase M6-C / `T-M6-SP1`..`SP4` — this document is the detailed execution plan for that phase's PPPoE-specific task (`SP4`), and should be read as elaborating, not superseding, the master plan's task list and "MUST"/"NEVER" rules.
- `plans/ASK2-REWRITE-PLAN.md` "Gate: A8" (line ~1443) and its PPPoE feature-parity table row (line 653) — same feature, different plan document's tracking scheme; both should be updated to cross-reference this document once it's reviewed, rather than maintaining three independent descriptions of the same unimplemented feature.
- `plans/OFFLOAD-CAPABILITY-PLAN.md` §1.8 — this document's recommendation ("lean — the only new heavy piece is the owned soft-parser arena") is adopted verbatim as this plan's Tier 1 scope; §0 above explains why Tier 2 (vendor relay-table depth) is explicitly rejected.
- `plans/archive/SOFT-PARSER-PPPOE.md` — superseded by this document for the PPPoE-specific content; its general soft-parser motivation (hard parser recognizes "only 16 L2-L4 protocol headers" and PPPoE needs an extension) is corrected by this document's §1 finding that PPPoE/PPP *is* one of those 16 hard-parser header types (slot 3) — the archived document's framing of PPPoE as inherently soft-parser-only was an unverified assumption, not a confirmed fact, and §3's cheap test should be run before accepting it.
- `specs/ask2-soft-parser-lcv-scheme-select.md` — the authoritative, detailed record of the soft-parser investigation summarized in §2 above; consult it directly for exact register addresses, bytecode encodings, and the full chronological investigation (§6a-6q) if Phase A (resolving the blocker) is picked up.
- `specs/fman-keygen-flow-key-spec.md` §4.1 — source for the `KG_SCH_KN_PPPSID`/`KG_SCH_KN_PPPID` extraction-knob facts in §1.
