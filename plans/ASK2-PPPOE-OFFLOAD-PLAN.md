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

### 3.3 Decap direction (PPPoE→LAN): implemented and SILICON-VALIDATED 2026-10-08

- **Kernel, F-260** (`bin/kernel-fixups/F_260.py`): a new L2-edit flag `FMAN_PCD_VLANF_PPPOE_STRIP` (bit 2 of `vlan_flags`) makes the inline record emitter take the VLAN front half `04 11 12` (12 with VID 0) and then emit `14 STRIP_PPPoE_HDR`. Its 4-byte stats pointer points at the owned 0216 scratch block, never 0. The record is `04 11 12 14 21 41 01` for IPv4 and `… 14 29 41 01` for IPv6, with NAT opcodes in place of `21`/`29` when the flow is NATed.
- **ask.ko:**
  - `ask_flow_cookie_pppoe()` replaces the step-0 guard. It returns 1 only for a decap direction: this tuple's only encap is `PPP_SES` with a non-zero session ID, and the other tuple has no PPPoE.
  - The encap direction, PPPoE over VLAN and session 0 return `-EOPNOTSUPP` and stay in software.
  - Decap flows were first gated by a runtime `ask.pppoe_offload` module parameter, then by a per-port `offload pppoe` CLI bit (§3.5, since removed). The final gate is the engaged-port rule plus the `ask.pppoe_offload` kill switch (§3.5).
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

**Board result** (`e8350d56`, image `2026.10.08-2126-rolling`, cold boot): **PASS.** `0x14 STRIP_PPPoE_HDR` executes cleanly on 210.10.1.
- **Regression with `pppoe_offload=N`:** all six routed combos in hardware, 9.14–9.38 Gbit/s unidir, 153–331 `rx_default_dqrr` calls per 10 s.
- **Decap, 20 Mbit/s TCP** (dell1 `10.99.50.1` → `pppoe10`/eth3 → eth4 → dell2):
  - conntrack `[HW_OFFLOAD]`.
  - New eth3 record `idx=29595`: 15,734 packets / 23.6 MB. It decodes exactly to the 50-byte key with session ID `0x000a`, the live session in `/proc/net/pppoe`.
  - The encap direction (ACKs) logged `REPLACE PPPoE flow not offloaded (T-M6-SP4 unsupported)` and stayed in software, as designed.
- **Decap, unlimited, 4 streams, 15 s:**
  - 746 Mbit/s, 1.32 GB.
  - `pppoe10` RX rose by only **8** packets, so the data direction bypassed the kernel PPPoE stack entirely.
  - A dell2 capture 6 s in: frames from DUT eth4's MAC, plain IPv4, TTL 63, TCP checksums correct, no PPPoE header.
  - The 613k `rx_default_dqrr` calls are the software ACK direction.
  - **746 Mbit/s is the rig's limit, not the DUT's.** dell1's concentrator runs userspace PPPoE (`pppd pty /usr/sbin/pppoe`, `pppoe-server` without `-k`). Measuring DUT decap throughput needs kernel-mode PPPoE on dell1 (`pppoe-server -k`).
- **Teardown:** when the flows ended, the decap records were deleted through the normal DESTROY path (the `tbl[3]` list was empty afterwards).
- **Session flap:** `disconnect`/`connect interface pppoe10` moved session ID `0x000a` → `0x0001`. A new flow's record `idx=21272` (13,894 packets, HIT) decodes to session ID **1**, not 10 or 0.
- **Health:** no kernel errors, bus errors, SYNC timeouts or Err FDs.
- **Still to do:**
  1. **Encap (LAN→PPPoE):** `43 INSERT_PPPoE_HDR` + INSERT_L2 with EtherType `0x8864` and the concentrator MAC, ENQ MTU = PPPoE MTU, and the ask.ko flush of PPPoE records on PPP netdev down/unregister.
  2. ~~Kernel-mode PPPoE on dell1 for a throughput number.~~ Done, see below.
  3. PPPoE over VLAN.
  4. Turning `pppoe_offload` on by default once encap is in.

**Throughput with a kernel-mode concentrator (2026-10-08).**
- **dell1 change:** the `ask2-pppoe-server.service` `ExecStart` now uses `pppoe-server -k -g /usr/lib/pppd/2.5.2/rp-pppoe.so …`. Without `-g` it looks for `/etc/ppp/plugins/rp-pppoe.so` and every session dies with "Couldn't load plugin".
- **Results,** 4 TCP streams for 15 s, dell1 → `pppoe10` → eth4 → dell2:

| `ask.pppoe_offload` | Throughput | DUT CPU | Flows `[HW_OFFLOAD]` |
|---|---|---|---|
| N (kernel software flowtable) | 3.99 Gbit/s | 40.4% softirq, 57.8% idle | 0 |
| Y (hardware decap) | **5.16 Gbit/s** | **2.0% softirq, 97.5% idle** | 4 |

- **Bottleneck:** with hardware decap the DUT is nearly idle, so 5.16 Gbit/s is a limit of dell1, not of the DUT. The remaining DUT softirq is the ACK direction (LAN→PPPoE), which stays in software until encap.
  - **Cause (measured 2026-10-09):** dell1's mlx4 NIC cannot RSS-hash PPPoE frames (EtherType 0x8864), so every returning ACK lands on RX queue 0 and CPU0. TCP is ACK-clocked, so dell1's whole send path (software GSO into 1452-byte segments, software checksums because `ppp0` has no checksum or TSO offload, PPPoE transmit) runs in that one softirq. CPU0 was at 91.7% softirq and the other seven CPUs idle; rx0 took 1.02 M packets in 8 s and rx1–7 none. That is about 510 kpps, or 5.9 Gbit/s.
  - **Fix:** RPS on dell1's `enp1s0`, `echo fe > /sys/class/net/enp1s0/queues/rx-0/rps_cpus` (the flow dissector hashes on the inner flow). 5.94 → 9.36 Gbit/s (14 s `-P 8` probe), DUT at line rate. `bin/testrig-offload-quick.sh` now sets it for PPPoE cells and restores it on exit.
- **Counter note:** `pppoe10` RX rises by only ~8 packets in both modes, because the kernel software flowtable also bypasses the PPPoE netdev. The software/hardware discriminator is therefore CPU and conntrack `[HW_OFFLOAD]`, not netdev counters.

### 3.4 Encap direction (LAN→PPPoE): implemented 2026-10-08, silicon-validated 2026-10-09 (§3.6)

- **Kernel, F-261** (`bin/kernel-fixups/F_261.py`):
  - A new flag `FMAN_PCD_VLANF_PPPOE_INSERT` (bit 3), plus a `pppoe_sid` field in `fman_pcd_fe_flow_action` and `fman_pcd_vlan_params`.
  - Record: `04 11 12` (VID 0), TTL or NAT, then `43 INSERT_PPPoE_HDR {stats_ptr → owned scratch, (1<<28)|(1<<24)|session}`, then `41 INSERT_L2_HDR` with EtherType **0x8864**, then `01`. This follows the vendor `create_pppoe_ins_hm()`, where the microcode fills in the PPPoE length and the PPP protocol.
- **ask.ko:**
  - `ask_flow_cookie_pppoe()` now also returns `ASK_PPPOE_ENCAP`: this tuple has no encap, the other tuple's only encap is `PPP_SES` with a non-zero session, and this tuple transmits `FLOW_OFFLOAD_XMIT_DIRECT`. The concentrator MAC comes from `out.h_dest` and our port's MAC from `out.h_source`, because no neighbour table knows the concentrator.
  - `FLOW_ACTION_PPPOE_PUSH` is parsed into `key.pppoe_push_sid` and `ASK_VLANF_PPPOE_INSERT`. The replace path checks that the push session matches the tuple's session. It refuses PPPoE combined with VLAN pop/push, and refuses any push outside a classified encap flow.
  - Gated like decap (§3.5): the session's physical port is engaged and `ask.pppoe_offload` is on. KUnit coverage is extended.
- **Session-down flush.** An ask.ko netdev notifier watches for `ARPHRD_PPP` `NETDEV_DOWN`/`UNREGISTER`. It schedules work that walks the flow table and `ask_flow_remove_owned()`s every flow carrying a PPPoE flag, which hands those flows back to the kernel path. The kernel never tears these flows down itself, because their `iifidx` is never the PPP netdev. If the notifier fails to register, PPPoE offload is refused.
- **MTU.**
  - The ENQUEUE parameter `mtu` is the microcode's fragmentation threshold (vendor: `EN_EHASH_DISABLE_FRAG = 0xffff`). ASK2 has no fragmentation pool (`bpid 0`), so encap records write `0xffff` to stay out of the fragmentation path.
  - **There is no hardware MTU enforcement.** The vendor does it with `05 PREEMPTIVE_CHECKS` (OpMask FRAG) plus a fragmentation pool; ASK2 emits neither. An inner packet above the PPPoE MTU (1492) therefore leaves 8 bytes oversize and the concentrator drops it, where the software path would send ICMP fragmentation-needed. Until `05` is brought up, encap requires TCP MSS clamping on the PPPoE interface: `set interfaces pppoe pppoe10 ip adjust-mss clamp-mss-to-pmtu`. *(Superseded by §3.6, F-262: encap records now carry the vendor MTU check.)*
- **Board test:**
  - **Setup:** cold boot. Engage eth3 with `offload ipv4`/`ipv6` (§3.5); PPPoE needs nothing more.
  - **First flow:** dell2 (LAN) → DUT eth4 → `pppoe10` → dell1 `10.99.50.1`, with MSS ≤ 1452 (`iperf -M 1400` or the clamp).
  - **Pass:**
    - eth4's record HITs.
    - dell1 captures (`tcpdump -e`) show EtherType 0x8864, the right session ID, a correct PPPoE length and PPP protocol `0x0021`, and TTL decremented.
    - The data arrives intact.
  - **Then:**
    - bidirectional throughput, now with both directions in hardware;
    - a session flap, where the journal must show "PPPoE session down, removed N PPPoE HW flow(s)" and a new flow must get the new session ID;
    - an oversize DF probe, to document the MTU behaviour.
  - **Wedge risk:** `0x43` has never executed on this board, and it grows the frame in front, so the 96-byte RX margin from patch 0217 applies.

### 3.5 Turning PPPoE offload on and off: automatic on an engaged port (granularity decision 2026-10-09)

PPPoE offload is no longer a per-port CLI leaf. It was `set interfaces ethernet ethN offload pppoe` (vyos-1x patch 053, 2026-10-08, genl `ASK_ATTR_PPPOE`, `ask_hw_port_pppoe[]`); the 2026-10-09 granularity decision removed it. Per-port engage (`offload ipv4|ipv6`) is the one mandatory granularity, per-family stays as the operator policy knob, and VLAN, NAT, PPPoE and bridge are automatic parts of an engaged port (spec `specs/ask2-vlan-cli-grammar.md` §9).

PPPoE offload runs on the PPPoE `source-interface` physical port, because the session's keys, tables and records all live there. Engaging that port is all it takes:

```
set interfaces ethernet eth3 offload ipv4
set interfaces ethernet eth3 offload ipv6
set interfaces pppoe pppoe10 ip adjust-mss clamp-mss-to-pmtu   # still advisable: not every flow gets the HW MTU check
```

- **vyos-1x patch 054** (`data/vyos-1x-054-offload-drop-vlan-pppoe-leaves.patch`) removes the `vlan` and `pppoe` leaves, the `set_ask_offload()` modifier arguments and the `interfaces_bridge.py` re-arm; migration `interfaces` 35-to-36 deletes both leaves from stored configs (the family leaves stay, so the port stays engaged). It also restores the F-222 MTU ceiling (1280-3600) check for every engaged port; patch 044 had split it off `_ask_on` so it only ran under a modifier leaf.
- **Helper:** `vyos-offload-ask family <mask>` sends only `port-id` and `family-mask`.
- **ask.ko:**
  - genl `ASK_ATTR_PPPOE` and `ask_hw_port_pppoe[]` are removed.
  - The gate is `ask_hw_pppoe_offload_armed_port(pid)`: the `pppoe_offload` module parameter (default 1) AND the session's physical port is engaged (family mask != 0). It is checked on the decap tuple's `iifidx` and the encap tuple's `out.ifidx`.
  - `pppoe_offload` is the global kill switch: a live 1 to 0 and every port disengage call `ask_flow_pppoe_flush()`, so no PPPoE record outlives its port or the switch.
  - `pppoe_encap_offload` stays removed.
- **Same change: vyos-1x stack rebased onto `rolling` `4b022646b` (2026-10-08).** Upstream added `verify_vpp_mtu()` (T9161) in `interfaces_ethernet.py` next to where patch 025 inserts `verify_ingress_policer()`. Patch 025 is regenerated to keep both functions. With it, the full stack 001–053 applies on today's `rolling` both with `--3way` and with plain `git apply`. Without it, every patch from 025 on conflicted or cascaded, and the next vyos-1x cache miss would have failed CI. The stack through 054 was re-verified on the same `rolling` on 2026-10-09.

### 3.6 Hardware MTU check: vendor `05 PREEMPTIVE_CHECKS` + fragmentation (F-262, 2026-10-08; revised and board-validated 2026-10-09)

**Root cause and final design (2026-10-09, verified on `.185`, supersedes the interim "no pool" revision below).** The microcode IP fragmenter failed because the vendor **advanced-offload RX-port triple was never applied**. ASK SDK `FM_PORT_SetPCD` (`fm_port.c:4843-4880`, `:5115-5118`) writes three values on every PCD-attached RX port, and the live vendor board reads them: params-page misc (page +0x40) `|= OFFLOAD_SUPPORT_EN 0x40000000` (vendor `0x40000100`, ours `0x00000100`), `FMBM_RCMNE` (+0x7c) `0x0e` (ours `0`), `FMBM_RFENE` (+0x70) `0x22` (ours `0x00d40000`). Without them, with `frag_options = 0x000c` and a real pool, the microcode emitted one fragment per oversize frame and the port's RX went permanently deaf after exactly 11 fragmented frames (cold boot only). Every "fragmentation is unusable" and "IPv6 silently dropped" observation in the interim findings below was taken without the triple.

Verified with the triple written live over `/dev/mem` (cold boot, eth3/eth4 armed; port 0x11 params page MURAM `0x4b000`, port 0x10 `0x56e00`), `frag_options = 0x000c`, ENQ bpid = the real pool (bpid 5):

| Probe (PPPoE encap leg, 1493-byte IP packet, 1492 MTU) | Result |
|---|---|
| IPv4 DF clear, 3 × 5 frames | 15/15 delivered, 2 fragments per frame |
| IPv4 DF clear × 100 | 100/100 delivered; dell1 `IpReasmOKs` +100; `v4_frames` 100, `v4_frags` 200 |
| IPv4 DF set × 100 | 0 delivered by hardware: host punt, `IcmpOutDestUnreachs` +100, dell2 `IcmpInDestUnreachs` +100 (PMTUD intact) |
| IPv6 × 100 | 100/100 delivered, `Ip6ReasmOKs` +100, 2 fragments per frame (1510 B + 83 B), no Packet Too Big |

The port stayed alive through 315 fragmented frames, MURAM `used` stayed flat at 52922, `alloc_fail` stayed 0, and dmesg was clean. The earlier "FE index leak" hypothesis was wrong. The triple does **not** cure the VLAN FE-leak freeze (E1, 2026-10-04, `ASK2-REWRITE-PLAN.md`); it is required for IP fragmentation. With the triple applied and `frag_options = 0x0004` (no `BPID_ENABLE`) IPv4 DF-clear oversize reaches the host and the kernel fragments it (5/5 delivered), but IPv6 oversize is still silently dropped (`v6_frames` 5, `alloc_fail` 5, none delivered, no Packet Too Big). So the hardware can never send an IPv6 Packet Too Big: IPv6 across an MTU decrease is either hardware-fragmented (vendor behaviour, `cdx_ehash.c`: `frag_options 0x000c`, 2048 × 1500 pool; RFC 8200 5 non-compliant) or kept in software.

**Final F-262 (`bin/kernel-fixups/F_262.py`).** Pool restored (`fman_pcd_frag_pool_bpid()`, 2048 × 2 KiB, created on first use, never freed), frag-info `frag_options = 0x000c`, ENQ bpid = the pool, and a new `fman_port_adv_offload()` that applies the triple when a port engages and restores the saved values (reverse order) when it disengages. It reads every register back and fails the engage on mismatch. The restore is what keeps the S1→S0 `pcd-snapshot` gate clean (it compares `RFENE` and `RCMNE`). The shipped result: IPv4 DF-clear is fragmented in hardware, IPv4 DF-set is punted to the host (ICMP frag-needed), and IPv6 that needs the check is fragmented in hardware (no Packet Too Big). That is the default since the 2026-10-09 policy inversion (vendor parity); the global ask.ko parameter `ipv6_hw_frag=0` keeps those flows in software for RFC 8200 (a per-port CLI leaf existed for a few hours on 2026-10-09 and was dropped by the granularity decision). Not yet validated on a CI image: throughput of all cells with the triple on every engaged port, pool exhaustion, and a long soak.

**Interim board result and revision (2026-10-09, image `2026.10.08-2320-rolling`, kernel `6.18.55-vyos`, DUT `.185`; SUPERSEDED by the root cause above, kept as the record of what was measured without the triple).** The design below was built as written and tested. `05` and the MTU field work. Without the RX-port triple the vendor's hardware fragmentation gave an unusable result, so this interim revision of F-262 dropped the fragmentation pool:

- **Records.** Encap records carry `05` first with parameter `38 03`, and ENQ `mtu=0x05d4` (1492), word2 = the frag-info block at MURAM `0x54300` (32 B). Decoded from DDR on the live board, record layout as in the table below.
- **Hardware fragmentation looked unusable (`frag_options` = `0x000c`, `BPID_ENABLE` set, pool bpid 5, no RX-port triple; cause found later, see above).** An oversize DF-clear frame produced exactly one fragment (the first, at the MTU) and no second one, so the datagram was lost. The datagram is lost, and no `Err FD` is raised. The cause was the missing RX-port triple (see the root cause above).
- **Fix, validated live.** With `BPID_ENABLE` clear (`frag_options = 0x0004`, `OPT_COUNTER_EN` only) and ENQ `bpid = 0`, the microcode no longer touches the frame: it reaches the host path. The kernel then does what a router must do:
  - DF clear: the host fragments (`IpFragOKs`/`IpFragCreates` rise) and the datagram is delivered whole.
  - DF set: the host sends ICMP fragmentation-needed (`IcmpOutDestUnreachs` rises), and the sender's route cache shows `mtu 1492`, so PMTUD works. This answers the open silicon unknown in step 4: the frame is **not** swallowed into an error queue.
- **Interim revised F-262 (SUPERSEDED by the final F-262 above; CI run `37873740487`, ISO `2026.10.09-0216-rolling`, shipped this).** No fragmentation pool and no `fman_pcd_frag_pool_bpid()`. The 32-byte frag-info block stays, initialised with `frag_options = 0x0004`. ENQ `bpid` = 0, `mtu` = `egress_mtu`, word2 = `fe_frag_off`. The emitter returns `-EOPNOTSUPP` only if the L2 fields or `fe_frag_off` are missing, and ask.ko then keeps the flow in software. The CI image `2320` still carries the pool version; for board runs on it, re-apply `frag_options = 0x0004` after each cold boot with a `/dev/mem` write to `0x1A00000 + 0x54300` (word at +0, big endian `0x00040000`). The block's MURAM offset is printed by the dmesg line at engage.
- **Stability probes with `frag_options = 0x0004` (cold boot, eth3 and eth4 only).**
  - Probe 1, 45 s, two concurrent flows from dell2 (400 fitting 1000-byte datagrams to warm the record, then 400 × 1472-byte datagrams that are 8 bytes oversize on the PPPoE leg): DF clear delivered 399/400, DF set delivered 0/400 and drew ICMP fragmentation-needed. The DUT sent 407 `IcmpOutDestUnreachs` and made 399 reassemblable datagrams (`IpFragOKs` +399, `IpFragCreates` +798).
  - The one missing DF-clear datagram is the only frame the frag-info counters saw (`v4_frames=1`, `alloc_fail=1`).
  - Probe 2, a fresh flow, DF clear only: 300/300 delivered and the counters did not move, so that loss was a one-off transient on the first oversize frame, not a rate.
  - Throughout: no `Err FD`/timeout/bus-error messages, 0 RX errors on eth3/eth4, no RX-deaf port, MURAM `used` flat at 52922, and all flows aged out afterwards (`total flows: 0`).
- **IPv6, measured 2026-10-09 (image `2320`, experimental ask.ko with the IPv6 gate removed, cold boot): the microcode does not punt oversize IPv6 to the host.** Flow dell2 `fd99:2::113` to dell1 `fd99:50::1` (UDP, `IPV6_MTU_DISCOVER` = PROBE so the sender cannot cache a PMTU), 100 datagrams of 1452 bytes (1500-byte packets) into a record with the `05` check (`mtu 0x05d4`). Four variants gave the same result:

  | Variant | `v6_frames` | `alloc_fail` | PTB sent | Delivered |
  |---|---|---|---|---|
  | `frag_options` 0x0004, ENQ bpid 5 (pool) | +100 | +148 | 0 | 0 |
  | `frag_options` 0x0004, bpid 0 | +100 | +135 | 0 | 0 |
  | same, `05` OpMask 0x03 (DFBIT_HONOR) | +100 | +133 | 0 | not checked (server idled out; counters are the evidence) |
  | `frag_options` 0x0024 (DF action 0x20), OpMask 0x01 | +100 | +122 | 0 | 0 |

  The frame always enters the IPv6 fragmenter (`v6_frames` counts every oversize frame, regardless of `BPID_ENABLE`, DFBIT_HONOR or the DF action bits, unlike IPv4 where `v4_frames` stays 0), finds no fragment buffer (`alloc_fail` rises, `v6_frags` stays 0) and drops the frame silently. `Err FD` and RX errors stayed 0, `Icmp6OutPktTooBigs` stayed 0, MURAM `used` stayed flat at 52922. Allowing `egress_mtu` for IPv6 would therefore black-hole every oversize IPv6 packet and break IPv6 PMTUD. ask.ko at that time returned `-EOPNOTSUPP` for an IPv6 flow that needs the check, so it stayed in software and the kernel sent Packet Too Big. A real fragment pool plus the triple makes the hardware fragment IPv6, which a router must not do (RFC 8200 5). **Superseded:** these four variants were taken without the RX-port triple. With the triple and the vendor configuration (`0x000c`, real pool) the microcode fragments IPv6 in hardware (100/100 delivered); with the triple and `0x0004` it still drops, so it never sends Packet Too Big. **Policy (inverted 2026-10-09):** IPv6 across an MTU decrease is hardware-fragmented by default (vendor parity, no Packet Too Big, RFC 8200 4.5 non-compliant); `ask.ipv6_hw_frag=0` keeps those flows in software with correct PMTUD (global kill switch, no per-port CLI; a live 1 to 0 flushes the IPv6 records that carry the MTU check).
- **TCP MSS clamping** on the PPPoE interface stays recommended, so TCP never has to fragment.

**Findings made on the board while running the quick protocol (all fixed in the tree; none is in image `2320`):**

1. `board/scripts/vyos-offload-ask` dropped its 4th argument, so `offload pppoe` armed the helper without the PPPoE bit (the engage line showed `pppoe=0` on eth3).
2. `ask_flow_cookie_pppoe()` used `out.ifidx` (the PPP netdev) where the physical port is `out.hw_ifidx`, so the encap port gate looked at the wrong device.
3. **Session-down flush leaked silicon records.** `ask_pppoe_flush_fn()` removed the software flow entries only. The later nft DESTROY callback then finds no entry (`-ENOENT`) and returns before `ask_fe_flow_remove()`, so the ehash records stayed in silicon: after a session flap, 9 flows were still listed two minutes later, and a decoded record carried SID 5 while the live session was SID 6. A stale encap record would forward into the dead session with the old SID and MAC, and each one leaks a DDR record. The 2126 flap test missed it because its flows had already ended and DESTROY had run first. Fix: `struct ask_pppoe_victim` carries the flow key and the flush calls `ask_fe_flow_remove(&v->key)` after `ask_flow_remove_owned()`. Re-run with the fix (4-stream iperf3 while `pppd` was killed on dell1): 10 flows to 0 within 2 s, MURAM `used` flat at 52922, RX alive, 0 errors, and the redial (new SID) worked.

**Throughput, 30 s quick protocol, final state (`ask.ko` srcversion `F5AADE10C3AD930F078393C` on image `2320`, `frag_options` unchanged; CSV `/mnt/builds/ask2-review/oracle/quick-20261009-0127.csv`):**

| Cell | Gbit/s | Verdict |
|---|---|---|
| unicast v4 / v6 | 9.38 / 9.24 | HW / HW |
| NAT44 / NAT66 | 9.34 / 9.27 | HW / HW |
| VLAN-VLAN v4 / v6 | 9.32 / 9.18 | HW / HW |
| PPPoE decap (down) v4 / v6 | 5.76 / 5.74 | HW / HW (dell1 RX-queue-0 limit, see §3.3; software control 3.98 / 3.74) |
| PPPoE encap (up) v4 | 9.19 | HW (software control 3.60) |
| PPPoE encap (up) v6 | 4.35 | PARTIAL (ratio 0.46), measured with the old policy (IPv6 that needs the MTU check in software, control 3.58); expected HW by default with the final F-262 and the inverted policy (not yet measured); PARTIAL again with `ipv6_hw_frag=0` |

**Re-run with RPS on dell1 (same image and `ask.ko`, CSV `quick-20261009-rps.csv`, `bin/testrig-offload-quick.sh` now enables RPS for PPPoE cells):**

| Cell | Gbit/s | Verdict |
|---|---|---|
| PPPoE decap (down) v4 / v6 | 9.35 / 9.22 | HW / HW (ratio 1.18 / 1.02) |
| PPPoE encap (up) v4 / v6 | 9.20 / 4.36 | HW / PARTIAL by design (ratio 1.14 / 0.46) |
| NAT44 over PPPoE | 9.33 | HW (ratio 1.14) |

So PPPoE decap and encap v4 and NAT over PPPoE all run at line rate; the 5.76 figures above were the dell1 limit. After the re-run: 0 `Err FD`, MURAM `used` flat at 52922, RPS restored to 0.

Combinations (once, CSV `quick-20261009-0134.csv`): NAT44 over PPPoE (masquerade on `pppoe10`) 9.08 Gbit/s HW; VLAN-VLAN NAT44 (masquerade on `eth4.20`) 9.34 Gbit/s HW. PPPoE over VLAN is verified by code only: `ask_flow_cookie_pppoe()` sees two encap entries, `ask_tuple_only_pppoe` requires one, so the flow falls through to `-EOPNOTSUPP` and stays in software. It was not run on the board. After the runs: 124 M packets, 0 RX errors, 0 `Err FD`, MURAM `used` flat.

**What the vendor does.** `cdx_ehash.c fill_actions()` puts `05 PREEMPTIVE_CHECKS_ON_PKT` first in every routed record (multicast too). Bridge and PPPoE-relay records use ENQUEUE mtu `0xffff`, and IPsec-to-SEC records set `FRAG_DISABLE`; none of those carry the check. The pieces:

- `seal_preemptive_checks_hm()` fills the 8-byte `en_ehash_preempt_op`. Its `mtu_offset` byte is the distance from the `05` parameter to the ENQUEUE parameter, whose first field is the MTU. Its `OpMask` is `PREEMPT_TX_VALIDATE 0x01`, plus `PREEMPT_DFBIT_HONOR 0x02` for IPv4.
- `create_enque_hm()` writes the ENQUEUE parameter:
  - `mtu` = the route MTU;
  - `bpid` = the fragmentation pool;
  - `word2` = the MURAM offset of a `cdx_ucode_frag_info_t` block.
- `cdx_init_frag_module()` initialises that block:
  - `frag_options = BPID_ENABLE 0x08 | OPT_COUNTER_EN 0x04` (DF action `0x00`, "error");
  - counters 0;
  - `v6_identification` 1.
- `cdx_create_fragment_bufpool()` seeds a dedicated BMan pool of 2048 buffers.

The live vendor record on `.106` (2026-10-04) shows `05` param `38 03 00…`, ENQ mtu `0x05dc` and word2 `0x00049540`.

**What ASK2 had.** No `05`, ENQ mtu 1500, bpid 0 and word2 0. Without `05` the mtu field is inert: the 2026-08-17 MTU battery forwarded 2500-byte frames through mtu-1500 records. A `05` with word2 0 would aim the microcode's frag-info reads at MURAM 0, which is the DMA CAM. So `05` is only ever emitted together with a real block. A vendor-exact `05` prefix already executed at line rate on `.185` during the 2026-10-05 VLAN bisection.

**F-262 (kernel, `bin/kernel-fixups/F_262.py`):**

- **The request.** `u16 egress_mtu` is added to `fman_pcd_fe_flow_action` and `fman_pcd_vlan_params`; 0 means no check, and the record stays byte-identical.
- **The frag-info block.** 32 bytes appended to the owned, refcounted FE internal-buffer MURAM reservation, after the 0216 stats scratch (`pcd->fe_frag_off`). It is initialised like the vendor's (`frag_options = 0x000c`). It lives and dies with the engage lifecycle, so the S1→S0 MURAM baseline still returns to zero.
- **The fragmentation pool (restored in the final F-262, 2026-10-09; the interim revision had removed it).** `fman_pcd_frag_pool_bpid()` creates a dedicated BMan pool of 2048 × 2 KiB buffers (order-0 pages, `DMA_BIDIRECTIONAL` on the FMan device). It is created on the first record that needs it and never freed, because hardware may hold its buffers at any time.
- **The emitter**, for an L2/TX record with `egress_mtu` set:
  - puts `05` first, ahead of the `04 11 12` front half (opcode 0 is not `11`);
  - seals its parameter once the ENQUEUE offset is known;
  - writes ENQ `mtu` = `egress_mtu`, `bpid` = the pool, and `word2` = `fe_frag_off`.
- **The RX-port triple.** `fman_port_adv_offload()` (`fman_port.c`) writes params-page misc `|= 0x40000000`, `RCMNE = 0x0e`, `RFENE = 0x22` in that order when `fman_pcd_fe_engage_profile()` arms a port, reads all three back (mismatch rolls back and fails the engage with `-EIO`), and restores the saved values in reverse in `__fman_pcd_fe_arm_disengage()`.
- **Fail-closed.** If any piece is missing it returns an error, and ask.ko keeps the flow in software.

**ask.ko.**

- The replace path reads the flowtable tuple's `mtu`, which `flow_offload_fill_route()` takes from the egress dst. It sets `key.egress_mtu` only when that MTU is below the true ingress port's MTU.
- So LAN 1500 → PPPoE 1492 gets the check. Port↔port 1500/1500, PPPoE decap, and VLAN flows of equal MTU do not, and their records are unchanged.
- An IPv6 flow that needs the check is hardware-fragmented by default (module parameter `ipv6_hw_frag`, default on; clearing it keeps such flows in software). Measured 2026-10-09 (see §3.6): the microcode enters its IPv6 fragmenter for every oversize frame whatever `frag_options`, DFBIT_HONOR or the DF action bits say; with the RX-port triple and a pool it fragments in hardware, otherwise it drops silently, and it never sends Packet Too Big. Hardware IPv6 across an MTU decrease therefore hides PMTU (no Packet Too Big; non-compliant, RFC 8200 4.5), which is why the `ipv6_hw_frag` kill switch exists; with it cleared the kernel sends Packet Too Big.

**Encap record layout with the check.** For a 50-byte key the opcode list is at +60 and parameters start at +76:

| Offset | Opcode | Parameter |
|---|---|---|
| +76 | `05` | 8 bytes: `mtu_off`, `0x03` |
| +84 | `04` | 4 bytes |
| +88 | `12` | 12 bytes |
| +100 | `21` | 4 bytes |
| +104 | `43` | 8 bytes |
| +112 | `41` | 20 bytes |
| +132 | `01` | ENQ: mtu `0x05d4`, bpid, fqid, stats, word2 |

That gives opcodes `05 04 11 12 21 43 41 01` and `mtu_off` = 132 − 76 = `0x38`. TCP MSS clamping on the PPPoE interface stays recommended, so TCP never needs fragmenting.

**Board test (after CI, cold boot, eth3/eth4 only):**

1. **Record.** Bring up an encap flow (dell2 → eth4 → pppoe10 → dell1). Check:
   - dmesg has `F-262 frag pool bpid N … frag info @MURAM 0x…` and, per engaged port, `fman_port: advanced offload on (misc 0x40000100 rcmne 0x0000000e rfene 0x00000022)`;
   - a `/dev/mem` dump of the eth4 record has the layout above, with bpid N and word2 = that offset;
   - after disengage, `advanced offload off` and the BMI/params-page values are back to `0x100 / 0 / 0x00d40000` (`pcd-snapshot diff` clean).
   - **Regression check:** a plain eth3↔eth4 record has no `05` and ENQ mtu 1500.
2. **Fits.** 1400-byte UDP: HIT, delivered intact.
3. **Fragment.** 1500-byte IPv4 with DF clear (`ping -M dont -s 1472`). Expect two fragments on dell1 (`tcpdump -e`, EtherType 0x8864, valid PPPoE lengths). The frag-info counters at FMan MURAM + `fe_frag_off` should rise: +8 v4 frames, +16 v4 fragments, +4 allocation failures stays 0. The frag pool must not drain, so repeat a few thousand times. **Result 2026-10-09: with the RX-port triple, 100/100 DF-clear datagrams delivered as 2 fragments each (see the root cause above); without it, one fragment per frame.**
4. **DF.** 1500-byte IPv4 with DF set (`ping -M do -s 1472`). This is unmeasured: with DF action "error" the frame may land in the port error FQ (`Err FD status`, a PMTUD black hole) or reach the host, which would send ICMP fragmentation-needed. Record which. If it is a black hole, the frag-info `frag_options` DF bits (0x10 ignore, 0x20 don't fragment) and the `05` OpMask are the calibration points. Try them live through `/dev/mem` before changing code. **Result 2026-10-09: not a black hole.** The DF-set frame reaches the host, which sends ICMP fragmentation-needed (400/400 with `0x0004`; 100/100 with the triple and `0x000c`; sender route cache `mtu 1492`). No DF-bit calibration was needed.
5. **Stability.** 60 s of mixed sizes at rate, with no RX-deaf port and no `Err FD` growth. Fragmentation produces S/G frames on the no-confirm TX FQ, which has never been exercised. **Result 2026-10-09: passed for 315 fragmented frames with the triple (no RX-deaf port, MURAM flat, `alloc_fail` 0) and with host fragmentation (two probes, interim result). Pool exhaustion and a long soak on the final F-262 are still to run.**
6. **Throughput (30 s quick protocol).** With eth3 (the PPPoE source interface) engaged by `offload ipv4`/`ipv6` (no PPPoE-specific leaf exists any more), run `bin/testrig-offload-quick.sh` (`ASK2-REWRITE-PLAN.md` §8). Pass: the four PPPoE cells report HW and beat the software control in §3a (decap v4/v6 3.98/3.74, encap v4/v6 3.60/3.58 Gbit/s). The other six cells must match the `2126` baseline (9.2-9.4 Gbit/s, HW). `pppoe-up-v6` at 1500 to 1492 should now be HW by default (IPv6 that needs the MTU check is fragmented in hardware). Re-run it once with `ask.ipv6_hw_frag=0` (`echo 0 > /sys/module/ask/parameters/ipv6_hw_frag`): PARTIAL or SW there is then correct, not a failure. Also check that all six non-PPPoE cells keep their baseline now that the triple is on every engaged port.
7. **Complex combinations (once per image, not a matrix):** NAT44 over PPPoE (LAN to PPPoE WAN with masquerade on `pppoe10`); VLAN-VLAN NAT44; PPPoE over VLAN (must stay software, `-EOPNOTSUPP`, still forwarding correctly); and a session flap during an encap flow (the session-down notifier must flush the records).

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

**Rig update 2026-10-08 — IPv6 over PPPoE and session-flap-proof routes** (for `bin/testrig-offload-quick.sh`, the PPPoE v4 and v6 cells):

- **dell1 concentrator:** `+ipv6` appended to `/etc/ppp/pppoe-server-options`, so IPv6CP negotiates next to IPCP.
- **dell1 hooks (rebuilt on every session up, so a flap or reconnect needs no manual routing):**
  - `/etc/ppp/ip-up.d/ask2-rig`: `ip route replace 10.99.2.0/24 dev $PPP_IFACE table 150`, plus rule `10900 from 10.99.50.1 to 10.99.2.0/24 lookup 150` if absent.
  - `/etc/ppp/ipv6-up.d/ask2-rig`: `ip -6 addr replace fd99:50::1/64 dev $PPP_IFACE nodad`, route `fd99:2::/64 dev $PPP_IFACE table 150`, rule `10900 from fd99:50::1 to fd99:2::/64 lookup 150`.
- **dell2:** `ip -6 route replace fd99:50::/64 via fd99:2::185 dev enp2s0` (runtime only; re-apply after a dell2 reboot, like the v4 route `10.99.50.1 via 10.99.2.185`).
- **DUT (persistent, saved):** `set interfaces pppoe pppoe10 ipv6 address autoconf` (drives `+ipv6 ipv6cp-use-ipaddr` in the pppd peer file) and `set protocols static route6 fd99:50::/64 interface pppoe10`. The DUT needs no global address on `pppoe10`, only the route.
- **Verified (2026-10-08, image `2026.10.08-2126-rolling`):** IPCP and IPV6CP both up, `ping -6` dell1 `fd99:50::1` ↔ dell2 `fd99:2::113` through the DUT in both directions, session ID 1.
- **Software control** (30 s, 8 streams, `ask.pppoe_offload=N`; no record in `fe_ehash_stats`): decap v4 3.98, v6 3.74 Gbit/s; encap v4 3.60, v6 3.58 Gbit/s, at 55–75% DUT busy (about 50–70% softirq). This is the floor the hardware paths must beat.

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
