# ASK2 IPsec ESP hardware offload — CAAM crypto acceleration through the T-M6-4 fast path

**Updated 2026-10-16 · dpaa1 · T-M6-4 / T-M6-IP1 / T-M6-IP2 · Tier 1 substrate VERIFIED on the running image; tunnel gate and Tier 2 fast path NOT STARTED.** `ask_xfrm.c`, `ask_caam.c`, and `ask_op.c` are lifecycle stubs, not ESP offload. This document owns the IPsec runbook; task priority and acceptance belong to `ASK2-MASTER-PLAN.md` §4.6.4. The 2026-09-06 inventory claimed CAAM was not built; that was wrong (see §1). The rest of the patch inventory is still dated and must be re-verified before Tier 2 work.

The original draft is preserved in `archive/ASK2-IPSEC-OFFLOAD-PLAN-2026-10-15.md`. Its CC-leaf classification, transport-mode parity requirement and per-feature CLI proposal are superseded. Full vendor parity requires the Tier 2 fast path, not just CAAM crypto use.

## 0. The scope fork (read this before anything else)

"IPsec hardware offload" is two different projects that share silicon but not much code:

- **Tier 1 — crypto acceleration.** strongSwan/XFRM keeps running its existing in-kernel software datapath; only the AES/SHA/GCM math moves to CAAM via the Linux Crypto API. No FMan, no `ask.ko`, no CC-tree. Config + verification work only.
- **Tier 2 — full fast path (T-M6-4).** Class-specific ESP ehash records hand frames through the owned O/H/CAAM path to the resolved egress TX FQ, with XFRM ingestion in `ask.ko`. Multi-table dispatch is required before ESP coexists with routed/L2 classes on one port. No CC-tree ESP leaf or retired VLAN topology is an assumed prerequisite. Advertise `NETIF_F_HW_ESP` only after all gates pass.

Tier 1 verifies crypto acceleration independently and establishes a measured baseline. It does not close vendor ESP fast-path parity. Tier 2 remains required for that objective; the measurement gate determines engineering priorities, not whether to call Tier 1 full parity. No completion-time estimate is established.

## 1. Substrate audit before implementation

The CAAM build and boot-time items were re-verified on 2026-10-16 against
the running image on `.185` (`6.18.55-vyos`). The O/H-port, QI-share and
`ask.ko` items are still the dated 2026-09-06 inventory; re-verify them
against the applied stack before selecting Tier 2 work.

- **Silicon capability** — LS1046A SEC/CAAM implements a full IPsec ESP PROTOCOL OPERATION with per-SA PDB (SPI, seq/ESN, IV/salt, anti-replay scorecard), DES/3DES-CBC/AES-CBC/AES-CTR/AES-CCM/AES-GCM, HMAC via MDHA split-key, NAT-T (RFC 3948), and DPOVRD per-frame override — see `arch/sec-caam.md` §3 and the LS1046A SEC Reference Manual (qdrant).
- **Kernel config (corrected 2026-10-16)** — the earlier "CAAM is absent" finding only looked at the config fragments. `bin/ci-setup-kernel.sh` (~lines 918–930) forces `CRYPTO_DEV_FSL_CAAM`, `_COMMON`, `_JR`, `_CRYPTO_API_DESC` and `_AHASH_API_DESC` to `=y` after the fragments are merged. Until 2026-10-16 it also set `CRYPTO_DEV_FSL_CAAM_QI`, which does not exist in 6.18 and was silently ignored; QI is built through `CRYPTO_DEV_FSL_CAAM_CRYPTO_API_QI`, which defaulted to `y` because `CRYPTO_API` and `FSL_DPAA` are set. Tier 2 depends on QI (patch 0134 lives in `qi.c`), so the bogus line was replaced by explicit `CRYPTO_API=y` and `CRYPTO_API_QI=y` pins (no change to the resolved config); `kernel/common/vyos-base/40-crypto.config` itself still has no CAAM lines. The running image's resolved config has the whole stack built in: `CAAM`, `JR`, `CRYPTO_API`, `CRYPTO_API_QI`, `AHASH_API`, `PKC_API`, `RNG_API`, `PRNG_API` all `=y`, `RINGSIZE=9`, `DEBUG`/`INTC`/`RNG_TEST` off. `INET_ESP`, `INET6_ESP` and their `_OFFLOAD` variants are `=m`; `XFRM_OFFLOAD=y`. CAAM is not a Tier 1 blocker.
- **QI descriptor sharing** — `kernel/common/patches/board/0134-caam-qi-share.patch` adds `caam_qi_ext_consumer_register()`/`_release()` so an external FMan consumer can redirect a `caam_drv_ctx`'s response FQ to a caller-supplied sink FQID instead of the per-CPU crypto-API callback. Written for Tier 2; **never exercised against real traffic** in this tree.
- **O/H-port substrate** — patches `0175`–`0184` bring an FMan offline port from unbound to "armed and idle": device-tree bind (0175), register bring-up (0176), debugfs test harness (0177–0181, including the active-list double-add fix), input-FQ scheduling (0182, board-validated through phase 1d per `kernel/common/patches/board/series`), and an inject/capture/dump toolset (0183/0184) explicitly marked **"not yet board-tested."** No CC-tree rule or live frame has ever reached an O/H port. `plans/archive/ASK2-VLAN-REARCH.md`'s 2026-09-02 addendum names this substrate as the thing "IPsec reinject" will eventually need.
- **`ask.ko` scaffolding** — `ASK_CAP_ESP_OFFLOAD` (`include/uapi/linux/ask/ask.h:226`) and `ASK_ACT_TO_CAAM`/`ASK_ACT_TO_OP` (`include/ask_internal.h`) are reserved bits. `ask_hw.c` (~line 1461) hard-rejects both action flags unconditionally today — the reservation is real, the wiring is not. `ask_xfrm_init/exit`, `ask_caam_init/exit`, `ask_op_init/exit` are lifecycle-only stubs (`ask_xfrm.c`, `ask_caam.c`, `ask_op.c`); the one functional entry point, `ask_xfrm_state_add()`, unconditionally returns `-EOPNOTSUPP`.
- **Reusable HM infra** — the HMTD/HMCT header-manipulation engine and NADEN CC-leaf chaining built for VLAN (`plans/archive/ASK2-VLAN-REARCH.md` §2/§4) are silicon-validated and explicitly flagged there as reusable for "IPsec ESP encap" (`plans/OFFLOAD-CAPABILITY-PLAN.md` §1.7). Tier 2 does not need to invent this piece.
- **Binding rules** (`plans/ASK2-MASTER-PLAN.md` §2/§4.6.4): XFRM `xfrmdev_ops` is the sole SA authority; never infer SAs from conntrack or add a shadow daemon. Start AES-CBC-SHA256; preserve the CAAM A24a GCM refusal until independently resolved. Advertise `NETIF_F_HW_ESP` last. Never expose keys in diagnostics or persist them outside XFRM.

## 2. Tier 1 — CAAM crypto acceleration (software XFRM, hardware math)

No FMan/ASK2 changes. Steps 1–3 were completed on 2026-10-16 (no build change was needed). Steps 4–5 were run on 2026-10-17; only reboot-clean remains open.

1. **Kernel config — DONE (already present).** See §1. If the config is ever moved from `ci-setup-kernel.sh` into a fragment, keep `CRYPTO_API_QI`: it is built today and Tier 2 needs it.
2. **Boot-time correctness gate — verified 2026-10-16 on `.185`, except QoS** (`arch/sec-caam.md` §4; these are silent-corruption footguns, not soft warnings):
   - Every CAAM entry in `/proc/crypto` that `caam-check` inspects reports `selftest : passed`. `rng_current = rng-caam`, and `/dev/hwrng` returns data.
   - `SCFG_SNPCNFGCR` (`0x015701A4`) = `0xc0f3e0c0`: `SECRDSNP` and `SECWRSNP` (bits 31/30) are set by firmware.
   - CAAM `MCFGR` (`0x01700004`) = `0x40012305`: ARCACHE = 2, AWCACHE = 3 (cacheable). Re-read both registers after any U-Boot/RCW change.
   - Open: `SCFG_QOS1` = `0x02800000`, `SCFG_QOS2` = `0x03800000`. The SEC master's field has not been decoded against the reference manual. This affects performance, not correctness; decode it before the throughput comparison in step 5.
   - The RNG4 instantiation dmesg line was no longer in the ring buffer on a long-running boot. Capture it on the next cold boot.
3. **Registration and priority — verified 2026-10-16.** The kernel uses the highest-priority implementation, and charon cannot choose per SA. On the running image the Job Ring driver wins every common ESP transform. Each is shown below as JR, then QI, then software:

   | Transform | Job Ring (wins) | QI | Software |
   |---|---|---|---|
   | `authenc(hmac(sha256),cbc(aes))` | `-caam` 3000 | `-caam-qi` 2000 | — |
   | `authenc(hmac(sha1),cbc(aes))` | `-caam` 3000 | `-caam-qi` 2000 | — |
   | `rfc4106(gcm(aes))` | `-caam` 3000 | `-caam-qi` 2000 | `-ce` 300 |
   | `gcm(aes)` | `-caam` 3000 | `-caam-qi` 2000 | `-ce` 300 |
   | `cbc(aes)` | `-caam` 3000 | `-caam-qi` 2000 | `-ce` 300 |
   | `hmac(sha256)` | `-caam` 3000 | — | `-lib` 300 |

   `caam-check` section 5b now prints this table and fails if any CAAM entry's self-test result is anything other than `passed` (or `unknown`). Note that GCM is CAAM-accelerated in Tier 1. The A24a GCM refusal in step 8 applies only to the Tier 2 FMan-driven protocol descriptor path.
4. **Confirm strongSwan uses it.** VyOS's existing `charon` + `kernel-netlink` + in-kernel `esp4`/`esp6` stack needs no VyOS-side change, because it already goes through the kernel Crypto API. Bring up a tunnel against a lab peer (dell1/dell2) with `aes256-sha256` and separately `aes256gcm16`.
5. **Gate.**
   - Tunnel up, and the `ip -s xfrm state` byte counters climb.
   - The CAAM controller debugfs counters (`caam-check` prints them as root) move with traffic. On 2026-10-16 the idle board showed `rq_dequeued 250` and zero for the encrypt/decrypt byte counters. JR-driven ESP moves all of them, including `ob_*`/`ib_*` (verified 2026-10-17, see the result below).
   - Measure CPU load and throughput against a software-only baseline: the same tunnel and traffic with CAAM excluded. Exclude it by blacklisting the `-caam` drivers through `crypto_user`/`cryptomgr`, or by booting a kernel with CAAM disabled. Do not use a build that drops QI.
   - Rekey and reboot clean.

   **Result, 2026-10-17 (`.185`, kernel 6.18-vyos, ASK2 dataplane idle on eth4):** steps 4–5 passed except reboot-clean.
   - Setup: VyOS ships strongSwan 6.0.7 (`/usr/sbin/swanctl`; it is not on the `vyos` user's PATH). dell2 `.113` received strongSwan 6.1.0 (`strongswan-swanctl charon-systemd libstrongswan-standard-plugins`). The service is now disabled there, but the package is still installed. The runtime-only IKEv2 PSK tunnel ran `10.99.2.185/32[tcp/5299]` ↔ `10.99.2.113/32[tcp]` with IKE `aes256-sha256-modp2048`, `rekey_time=0`, initiated from dell2. No VyOS config was changed. Traffic was iperf3 from dell2, 4 streams × 20 s: fwd means dell2→DUT (the DUT decrypts) and rev (`-R`) means the DUT encrypts.
   - Counter proof: the CAAM debugfs `rq_dequeued`, `ob_rq_encrypted`/`ib_rq_decrypted` and `ob_bytes_encrypted`/`ib_bytes_decrypted` counters all move in lockstep with `ip -s xfrm state`. For example, CBC rev showed XFRM out 1.77 GB against an `ob_bytes_encrypted` delta of 1.75 GB. JR interrupts `1720000.jr`/`1730000.jr` rose by about 1M per run. The concern that the `ob_*`/`ib_*` counters stay at 0 outside protocol-descriptor mode is therefore wrong; JR ESP increments them, so they can be used as gates.
   - Software baseline method: load `crypto_user`. Use `CRYPTO_MSG_UPDATEALG` to drop `echainiv-authenc-hmac-sha256-cbc-aes-caam` and `rfc4106-gcm-aes-caam` to 100 and their `-caam-qi` variants to 90. Then instantiate `echainiv(authenc(hmac-sha256-lib,cbc-aes-ce))` (priority 3300) and `seqiv(rfc4106-gcm-aes-ce)` through an AF_ALG bind. The explicit instantiation is required because the demoted CAAM entry still matches by name, so the template is never built. Every CAAM counter stayed flat (±1) across both software runs. To restore: delete the three software instances (`CRYPTO_MSG_DELALG`, refcnt 1) and set the priorities back to 3000/2000. UPDATEALG had removed the dependent `seqiv(rfc4106-gcm-aes-caam)` instance, so re-instantiate it. Finally, diff the `/proc/crypto` driver/priority list against a pre-test snapshot; the result was identical. Any leftover software instance would silently beat CAAM until reboot.

   | Suite | Engine | fwd Gbit/s (retx, DUT busy) | rev Gbit/s (retx, DUT busy) |
   |---|---|---|---|
   | AES-CBC-256 / HMAC-SHA2-256 | CAAM JR | 0.436 (32, 49.8%) | 0.614 (3, 51.9%) |
   | AES-CBC-256 / HMAC-SHA2-256 | software (CE) | 0.515 (2374, 33.2%) | 0.330 (107, 32.8%) |
   | AES-GCM-16-256 | CAAM JR | 0.380 (19, 55.4%) | 0.599 (2, 51.3%) |
   | AES-GCM-16-256 | software (CE) | 0.575 (5873, 34.5%) | 0.429 (255, 35.7%) |
   | AES-GCM-16-256 (after restore, with rekey) | CAAM JR | 0.372 (0, 57.3%) | 0.606 (2, 50.3%) |

   - Interpretation: these runs are host-terminated, so they include the DUT TCP stack and iperf3 and are not a gateway-forwarding measurement. "DUT busy" is aggregate across four cores. JR CAAM gives 1.4–1.9× on encrypt but is slower than ARMv8-CE software on decrypt, and costs more CPU in both directions (about 1M JR completion IRQs per run). The per-request Job Ring round trip dominates at MTU-sized packets. Tier 1 is therefore a correctness and offload-plumbing proof, not a performance win, and the Tier 2 (FMan→SEC, no per-packet CPU) case stands.
   - Rekey: `swanctl --rekey --child t1gcm` 8 s into a CAAM GCM run completed with 0 retransmits for that run. The new CHILD_SA was installed and stayed on CAAM.
   - Not done: reboot-clean, a gateway-forwarding (routed, not host-terminated) measurement, IPv6, and the SEC QoS decode from step 2.

**Measurement point after Tier 1:** publish crypto-use proof, throughput and CPU against software on the same rig. This can be an independent delivery, but the full-parity objective remains open until Tier 2 passes.

## 3. Tier 2 — the T-M6-4 fast path (FMan → CAAM → FMan, zero CPU touch)

### 3a. Phase 1 — land the XFRM/CAAM stubs for real (still no O/H port)

6. Implement `ask_xfrm_state_add/delete/update` as `xdo_dev_state_*` hooks: XFRM owns SA lifetime; `ask.ko` mirrors an offloadable SA into a CAAM shared descriptor + PDB. Unsupported algorithms, resource shortage or failed readback return framework fallback before `in_hw` (`plans/ASK2-MASTER-PLAN.md` §4.6.1).
7. Implement `ask_caam_init`/`ask_caam_exit` to allocate a `caam_drv_ctx` per offloaded SA via the existing `caamalg_qi.c` path — this needs no new CAAM-side descriptor code, only the QI-share hooks from step 9.
8. **T-M6-IP1 suite matrix.** AES-CBC-SHA256 only, first landing. Reject GCM/AES-CCM at SA-install time — this is the pre-existing binding refusal for the CAAM A24a erratum, not a new decision to make here.
9. **Board-validate `0134-caam-qi-share.patch` for the first time.** Its `caam_qi_ext_consumer_register()`/`_release()` swap-and-drain dance has never run against live traffic. This is now the actual critical-path item — everything in Phase 2 assumes it works. De-risk it standalone (e.g. redirect a CAAM QI response FQ to a debugfs-owned sink FQ and confirm frames land there, mirroring the O/H-port `capture_init` pattern from patch 0183) before building anything on top of it.
10. **Gate (subset of T-M6-IP2).** SA add/delete/update against a real strongSwan peer (not a synthetic descriptor test); rekey overlap; expiry; sequence/anti-replay correctness; confirm no key material reaches debugfs, dmesg, or logs.

### 3b. Phase 2 — O/H-port re-injection substrate

11. Finish board-validating phases 1f/1g (`inject`/`capture`/`dump`, patches 0183/0184) — this was left untested by the VLAN work and is now IPsec's blocker, not VLAN's.
12. Define a typed ESP match/key (destination IP + SPI, direction/family and required isolation) and class-specific ehash record. Cross-check the vendor, Qdrant and current silicon specs before choosing the extraction and FE→SEC action. Resolve multi-table dispatch for coexistence; reject unsupported cases before publication.
13. Prove the owned loop: FMan/OH → CAAM request → CAAM response → resolved egress TX FQ or required post-decrypt classification. Audit the QI-share callback/FQ contract before wiring; names in the historical draft are not proof of the correct direction. No CC lookup is assumed. Each handoff needs bounded resources, readback, inverse and software fallback.
14. **Gate.** Single SA, unidirectional, fixed 5-tuple-equivalent (dest IP + SPI), on a sacrificial port — same de-risk discipline as `plans/archive/ASK2-VLAN-REARCH.md` §"De-risk experiments before R4": prove the mechanism on a throwaway port before touching a production port's KG scheme.

### 3c. Phase 3 — production gate and CLI

15. Full T-M6-IP2 matrix: bidirectional IPv4/IPv6 tunnel mode; ESN/anti-replay; NAT-T control/data separation; SA lifetime/expiry and rekey overlap; PMTU/encrypted-fragment policy; peer interoperability; negative auth/replay tests; crash/reboot with no stale descriptors or keys; disable/disengage software fallback. Transport mode is optional breadth, not vendor parity on LS104x. Extend the supported-suite matrix deliberately, retaining unsafe-suite fallback; SEC rate policing is a separate parity gate.
16. Advertise `NETIF_F_HW_ESP` only after every gate in step 15 passes — the master plan is explicit this is the last step, not a milestone marker to flip early for visibility.
17. Use existing per-port/per-family `offload ipv4|ipv6` engagement plus XFRM eligibility; no `offload ipsec` leaf. Any global safety switch and initial enablement policy require an explicit decision consistent with the master plan. Advertise no ESP capability before hardware acceptance.

## 4. Open questions to resolve before Phase 2 starts

- Multi-table ESP/routed/L2 precedence, miss delivery and independent SA/flow lifetime on one port. The current single-profile design does not provide this.
- Post-decrypt reclassification versus direct resolved TX disposition, including inbound policy/firewall enforcement and owned resources. Do not encode either as a presumed second CC lookup.
- IPv6 ESP (`esp6`) scope and timing relative to the IPv4-only Tier 2 first landing, matching how IPv6 trailed IPv4 for every other ASK2 capability.

## 5. Provenance

- Silicon capability: LS1046A SEC Reference Manual Ch. 9 (qdrant), `arch/sec-caam.md`.
- QI descriptor sharing: `kernel/common/patches/board/0134-caam-qi-share.patch`.
- O/H-port substrate status: `kernel/common/patches/board/series` (patches 0175–0184 and their commentary).
- Reusable HM/NADEN infra: `plans/archive/ASK2-VLAN-REARCH.md` §2, §4, §7.
- Capability comparison and mechanism recommendation: `decomp/vendor-vs-ask2-offloads.md` ("Capabilities the VENDOR has that ASK2 does NOT"), `plans/OFFLOAD-CAPABILITY-PLAN.md` §1.7.
- Binding control-plane rules, task IDs and GCM refusal: `plans/ASK2-MASTER-PLAN.md` §2/§4.6.1/§4.6.4 (T-M6-4/IP1/IP2).
- Tier 1 live evidence (2026-10-16): `board/scripts/caam-check` sections 5 and 5b on `.185`, `/proc/config.gz`, and `/dev/mem` reads of `SCFG_SNPCNFGCR`/`MCFGR`.
- Stub state: `kernel/ask/oot-modules/ask/ask_xfrm.c`, `ask_caam.c`, `ask_op.c`, `ask_hw.c` (~line 1461), `include/uapi/linux/ask/ask.h:226`.
