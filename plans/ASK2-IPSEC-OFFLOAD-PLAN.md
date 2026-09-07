# ASK2 IPsec ESP hardware offload — CAAM crypto acceleration through the T-M6-4 fast path

**2026-09-06 · dpaa1 · T-M6-4 / T-M6-IP1 / T-M6-IP2 · Status: DRAFT, NOT STARTED.** No code in this plan has been written or board-tested. `ask_xfrm.c`, `ask_caam.c`, and `ask_op.c` are lifecycle-only stubs today; `ask_hw.c` unconditionally rejects `ASK_ACT_TO_CAAM`/`ASK_ACT_TO_OP`; `CONFIG_CRYPTO_DEV_FSL_CAAM` is not set anywhere in this tree. This document sequences the work needed to close all of that, split into two independently shippable tiers.

## 0. The scope fork (read this before anything else)

"IPsec hardware offload" is two different projects that share silicon but not much code:

- **Tier 1 — crypto acceleration.** strongSwan/XFRM keeps running its existing in-kernel software datapath; only the AES/SHA/GCM math moves to CAAM via the Linux Crypto API. No FMan, no `ask.ko`, no CC-tree. Config + verification work only.
- **Tier 2 — full fast path (the actual T-M6-4 vision).** FMan classifies an ESP flow, hands the frame to CAAM over QI, CAAM re-injects through an O/H port back to the egress TX FQ — zero CPU packet touch, `NETIF_F_HW_ESP` advertised. This needs the O/H-port substrate (`plans/ASK2-VLAN-REARCH.md` §3 Option D) to mature past its current phase-1d state, a CC-tree ESP leaf, and a real XFRM ingest layer in `ask.ko`.

Tier 1 is a full, correct answer to "does IPsec use hardware crypto" and ships in days. Tier 2 is a silicon bring-up project on the scale of the VLAN re-architecture (May–August 2026) and should only be funded after Tier 1 is in and someone has measured that software-datapath overhead (not crypto math) is the actual bottleneck. **Recommendation: build Tier 1, gate on measurement, then decide on Tier 2.**

## 1. Current state (what already exists, verified in this tree)

- **Silicon capability** — LS1046A SEC/CAAM implements a full IPsec ESP PROTOCOL OPERATION with per-SA PDB (SPI, seq/ESN, IV/salt, anti-replay scorecard), DES/3DES-CBC/AES-CBC/AES-CTR/AES-CCM/AES-GCM, HMAC via MDHA split-key, NAT-T (RFC 3948), and DPOVRD per-frame override — see `arch/sec-caam.md` §3 and the LS1046A SEC Reference Manual (qdrant).
- **Kernel config gap** — `CONFIG_CRYPTO_DEV_FSL_CAAM` is **absent** from every fragment under `kernel/common/vyos-base/*.config` and `kernel/ask/kernel-config/*.config`. The CAAM driver is not built today. This is the actual current blocker for Tier 1, not a design question.
- **QI descriptor sharing** — `kernel/common/patches/board/0134-caam-qi-share.patch` adds `caam_qi_ext_consumer_register()`/`_release()` so an external FMan consumer can redirect a `caam_drv_ctx`'s response FQ to a caller-supplied sink FQID instead of the per-CPU crypto-API callback. Written for Tier 2; **never exercised against real traffic** in this tree.
- **O/H-port substrate** — patches `0175`–`0184` bring an FMan offline port from unbound to "armed and idle": device-tree bind (0175), register bring-up (0176), debugfs test harness (0177–0181, including the active-list double-add fix), input-FQ scheduling (0182, board-validated through phase 1d per `kernel/common/patches/board/series`), and an inject/capture/dump toolset (0183/0184) explicitly marked **"not yet board-tested."** No CC-tree rule or live frame has ever reached an O/H port. `plans/ASK2-VLAN-REARCH.md`'s 2026-09-02 addendum names this substrate as the thing "IPsec reinject" will eventually need.
- **`ask.ko` scaffolding** — `ASK_CAP_ESP_OFFLOAD` (`include/uapi/linux/ask/ask.h:226`) and `ASK_ACT_TO_CAAM`/`ASK_ACT_TO_OP` (`include/ask_internal.h`) are reserved bits. `ask_hw.c` (~line 1461) hard-rejects both action flags unconditionally today — the reservation is real, the wiring is not. `ask_xfrm_init/exit`, `ask_caam_init/exit`, `ask_op_init/exit` are lifecycle-only stubs (`ask_xfrm.c`, `ask_caam.c`, `ask_op.c`); the one functional entry point, `ask_xfrm_state_add()`, unconditionally returns `-EOPNOTSUPP`.
- **Reusable HM infra** — the HMTD/HMCT header-manipulation engine and NADEN CC-leaf chaining built for VLAN (`plans/ASK2-VLAN-REARCH.md` §2/§4) are silicon-validated and explicitly flagged there as reusable for "IPsec ESP encap" (`plans/OFFLOAD-CAPABILITY-PLAN.md` §1.7). Tier 2 does not need to invent this piece.
- **Binding rules already written** (`plans/ASK2-MASTER-PLAN.md` Phase M6-D, T-M6-4/IP1/IP2): XFRM `xfrmdev_ops` is the sole SA authority — never infer SAs from conntrack, never add a CMM-style shadow daemon; start with AES-CBC-SHA256 only; **GCM is refused for IPsec** (CAAM A24a erratum, master plan line ~296) until independently resolved; advertise `NETIF_F_HW_ESP` **last**, after every gate passes; keys must never reach debugfs, logs, support bundles, or persisted config outside XFRM itself.

## 2. Tier 1 — CAAM crypto acceleration (software XFRM, hardware math)

No FMan/ASK2 changes. Sequence:

1. **Kernel config.** Add to `kernel/common/vyos-base/40-crypto.config`: `CRYPTO_DEV_FSL_CAAM=y`, `CRYPTO_DEV_FSL_CAAM_JR=y`, `CRYPTO_DEV_FSL_CAAM_CRYPTO_API=y` (pulls `CRYPTO_AEAD`/`CRYPTO_AUTHENC`/`CRYPTO_SKCIPHER`/`CRYPTO_LIB_DES`/`CRYPTO_XTS`), `CRYPTO_DEV_FSL_CAAM_RNG_API=y`. Leave `CRYPTO_DEV_FSL_CAAM_QI` and `CRYPTO_DEV_FSL_CAAM_AHASH_API` out for now — QI is Tier 2 scope, ahash isn't needed for ESP's AEAD/authenc path.
2. **Boot-time correctness gate** (`arch/sec-caam.md` §4, non-negotiable, these are silent-corruption footguns not soft warnings): RNG4 self-instantiates at boot (dmesg) and `/proc/crypto` self-tests pass for every registered `*-caam` transform; `SCFG_SNPCNFGCR[SECRDSNP/SECWRSNP]` and `MCFGR[ARCACHE/AWCACHE]` snoop bits are set (without them, CAAM DMA is cache-incoherent with the A72 cores and silently produces wrong plaintext/ciphertext rather than an error); `SCFG_QOS1/QOS2` weights for the SEC port raised off their lowest-priority POR default (perf, not correctness, but cheap to fix alongside the snoop bits).
3. **Verify registration and priority.** `authenc(hmac(sha256),cbc(aes))` (and friends) appear in `/proc/crypto` with `driver: *-caam`; confirm via the `priority` field that CAAM's registration outranks the software fallback rather than assuming it — XFRM/charon picks whichever transform the crypto API hands it highest-priority, this is not configurable per-SA.
4. **Confirm strongSwan picks it up.** VyOS's existing `charon` + `kernel-netlink` + in-kernel `esp4`/`esp6` stack needs no VyOS-side change — it already goes through the kernel Crypto API. Bring up a tunnel and confirm.
5. **Gate.** Tunnel up; `ip -s xfrm state` byte counters climbing; `/sys/kernel/debug/caam/ctl` `ib_bytes_decrypted`/`ob_bytes_encrypted` climbing in lockstep; CPU load / throughput measured against a software-only baseline (same tunnel, same traffic, CAAM config reverted); rekey and reboot clean.

**Decision point after Tier 1:** if the measured CPU savings and throughput already satisfy the requirement, stop here. Only proceed to Tier 2 if there's a measured need for zero-CPU-touch ESP forwarding specifically (e.g. site-to-site throughput bound by the software forwarding path, not by crypto math).

## 3. Tier 2 — the T-M6-4 fast path (FMan → CAAM → FMan, zero CPU touch)

### 3a. Phase 1 — land the XFRM/CAAM stubs for real (still no O/H port)

6. Implement `ask_xfrm_state_add/delete/update` as `xdo_dev_state_*` hooks per the master plan's binding rule: XFRM owns SA lifetime; `ask.ko` only mirrors an offloadable SA into a CAAM shared descriptor + PDB. A failed mirror (unsupported algorithm, resource shortage, readback failure) MUST return the framework's normal fallback error before the SA is marked `in_hw` — same discipline as every other ASK2 capability (`plans/ASK2-MASTER-PLAN.md` §4.6.1 rule 5).
7. Implement `ask_caam_init`/`ask_caam_exit` to allocate a `caam_drv_ctx` per offloaded SA via the existing `caamalg_qi.c` path — this needs no new CAAM-side descriptor code, only the QI-share hooks from step 9.
8. **T-M6-IP1 suite matrix.** AES-CBC-SHA256 only, first landing. Reject GCM/AES-CCM at SA-install time — this is the pre-existing binding refusal for the CAAM A24a erratum, not a new decision to make here.
9. **Board-validate `0134-caam-qi-share.patch` for the first time.** Its `caam_qi_ext_consumer_register()`/`_release()` swap-and-drain dance has never run against live traffic. This is now the actual critical-path item — everything in Phase 2 assumes it works. De-risk it standalone (e.g. redirect a CAAM QI response FQ to a debugfs-owned sink FQ and confirm frames land there, mirroring the O/H-port `capture_init` pattern from patch 0183) before building anything on top of it.
10. **Gate (subset of T-M6-IP2).** SA add/delete/update against a real strongSwan peer (not a synthetic descriptor test); rekey overlap; expiry; sequence/anti-replay correctness; confirm no key material reaches debugfs, dmesg, or logs.

### 3b. Phase 2 — O/H-port re-injection substrate

11. Finish board-validating phases 1f/1g (`inject`/`capture`/`dump`, patches 0183/0184) — this was left untested by the VLAN work and is now IPsec's blocker, not VLAN's.
12. Build a CC-tree leaf matching an ESP flow (dest IP + SPI, since ESP has no ports to key on) that routes to the O/H port's input FQ. Reuse the HMTD/NADEN infra proven for VLAN rather than inventing a parallel mechanism (`plans/ASK2-VLAN-REARCH.md` §2/§4; the reuse is explicitly anticipated in `plans/OFFLOAD-CAPABILITY-PLAN.md` §1.7 and §2 item 2).
13. Wire the loop: O/H port dequeue → CAAM request FQ (the sink FQID returned by the now-validated `caam_qi_ext_consumer_register()` from Phase 1) → CAAM response → a second CC/re-inject step back onto the egress no-confirm TX FQ. This closed loop does not exist as running code anywhere in the tree yet; the patch-series comment on 0175 ("proving ground for the same OH-port primitive IPsec ESP offload needs") is aspiration, not implementation.
14. **Gate.** Single SA, unidirectional, fixed 5-tuple-equivalent (dest IP + SPI), on a sacrificial port — same de-risk discipline as `plans/ASK2-VLAN-REARCH.md` §"De-risk experiments before R4": prove the mechanism on a throwaway port before touching a production port's KG scheme.

### 3c. Phase 3 — production gate and CLI

15. Full T-M6-IP2 matrix: bidirectional; transport + tunnel mode; ESN; NAT-T control/data separation; PMTU/fragment policy; rekey while armed; peer interoperability against a non-ASK2 IPsec stack; negative auth/replay tests; crash/reboot leaves no stale descriptor or key material; disable/disengage falls back to software cleanly with no packet loss window beyond the switchover itself.
16. Advertise `NETIF_F_HW_ESP` only after every gate in step 15 passes — the master plan is explicit this is the last step, not a milestone marker to flip early for visibility.
17. Per-port/global arming CLI mirroring the VLAN pattern (`set interfaces ethernet ethN offload ipsec`, default-off at first ship, `ASK_CAP_ESP_OFFLOAD` advertised only while armed) — this repo's established convention for every new offload, not IPsec-specific.

## 4. Open questions to resolve before Phase 2 starts

- Whether CC-tree ESP classification can coexist on a port already running CC-tree VLAN classification and/or ehash routed/NAT — VLAN's own R4c-pre de-risk (CC-miss→FE_ENTER) answers the VLAN+routed case but ESP+VLAN+routed on the same port is a new combination, not yet analyzed.
- Whether the O/H port's re-inject step needs its own second CC lookup (post-decrypt classification) or can enqueue directly to a resolved egress TX FQ carried in the original CC leaf's context — affects whether Phase 2 needs one CC tree or two.
- IPv6 ESP (`esp6`) scope and timing relative to the IPv4-only Tier 2 first landing, matching how IPv6 trailed IPv4 for every other ASK2 capability.

## 5. Provenance

- Silicon capability: LS1046A SEC Reference Manual Ch. 9 (qdrant), `arch/sec-caam.md`.
- QI descriptor sharing: `kernel/common/patches/board/0134-caam-qi-share.patch`.
- O/H-port substrate status: `kernel/common/patches/board/series` (patches 0175–0184 and their commentary).
- Reusable HM/NADEN infra: `plans/ASK2-VLAN-REARCH.md` §2, §4, §7.
- Capability comparison and mechanism recommendation: `decomp/vendor-vs-ask2-offloads.md` ("Capabilities the VENDOR has that ASK2 does NOT"), `plans/OFFLOAD-CAPABILITY-PLAN.md` §1.7.
- Binding control-plane rules, task IDs, GCM refusal: `plans/ASK2-MASTER-PLAN.md` §4.6.1, §4.6.3 (IPsec ESP row), Phase M6-D (T-M6-4/IP1/IP2).
- Stub state: `kernel/ask/oot-modules/ask/ask_xfrm.c`, `ask_caam.c`, `ask_op.c`, `ask_hw.c` (~line 1461), `include/uapi/linux/ask/ask.h:226`.
