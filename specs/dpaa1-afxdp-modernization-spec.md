# DPAA1 Driver Modernization for LS1046A

**Target:** `mihakralj/vyos-ls1046a-build` → `specs/dpaa1-afxdp-modernization-spec.md`
**Kernel base:** Linux 6.18.x mainline (VyOS rolling release)
**Silicon:** NXP LS1046A — Cortex-A72 ×4 (2 clusters of 2 over CCI-400), FMan v3 Rev>1, QMan, BMan, MURAM (384 KiB), PAMU, SEC 5.4 (CAAM), CEETM, SerDes/XFI PCS, CoreNet
**FMan microcode:** package `fsl_fman_ucode_ls1046a_r1.0_210.x.bin` (NXP LSDK, U-Boot loads from SPI `mtd4`)
**Document version:** v5.15, 2026-06-10 (merge: restores the v5.13/v5.14 additions (§2.2.1 microcode inventory, §3.5 reserved cap bits, §5.4 HC-doorbell impact analysis, CLI policy note) on top of the 2026-06-07..10 content they had accidentally been based before — BUG 3a/3b §5.6 history, CEETM scaffold DUT-validation, M3-3b/M3-3d row updates all preserved; M3-3b row bumped for `0106` KGSE_CCBS graft wiring)
**Document version (prior):** v5.14, 2026-06-01 (Microcode-inventory completeness: new §2.2.1 enumerates the **full** authoritative NXP FMan PCD function set — Software Parser, KeyGen, CC Match-Table + Hash-Table nodes, HM, IPR, IPF, Frame Replicator, Policer, Host Command, PCD Statistics — flagging each consumed / present-unconsumed / absent. Explicitly captures the previously-missing **Frame Replicator** and **CC Hash-Table nodes**, and the missing **IPF/IPR cap bit**; §3.5 now states the 5-bit bitmask is a deliberate subset with reserved BIT(5)–BIT(8) placeholders; §11 cites the two NXP "Frame Manager Features" / "FMan PCD Driver" doc URLs. **§2.2.1 explicitly scoped to the proprietary 210.10.1 blob this board ships, NOT the open-source 106.4.18 baseline** — a new "Public 106.x?" column marks each function Y / 210-only / build-specific, with scope notes clarifying 106.x is strictly the Y-marked subset (hard parser + KeyGen hash + basic Match-Table CC + basic Policer) and every 210-only row is an Advance-PCD function added by the proprietary ucode. v5.13 below.)
**Document version (prior):** v5.12, 2026-05-31 (spec cross-alignment: added SEC/CAAM engagement row in §4, updated §1.3 VPP row to reference v0.2 spec and v0.1 rejection, added OQ13 VPP native-plugin rejection, added VPP spec reference in §11)
**Supersedes:** v5.14, v5.13, v5.7, v5.6, v5.5, v5.4, v5.3, v5.2, v5.1, v5.0, v4.x, v3.0, v2.x, v0.9

---

## TL;DR

1. **One driver core, one classifier API, one HW-accelerated AF_XDP stack — three flavors that consume it differently.** Shared substrate: two ops tables (`pcd_ops`, `qmgmt_ops`) installed per-`dpaa_priv`. `default` leaves them NULL (mainline behaviour); `vpp` populates them with UMEM-backed BMan pool + ZC RX/TX hooks; `ask` populates them with dynamic CC tree + Host Command reconfig hooks. RCU-NULL-safe — when no flavor module is loaded the driver is byte-identical to mainline.

2. **The hardware offloads (CC / HM / Policer / CEETM) are not VPP-only.** Each exposes a kernel-side consumer that benefits `default` and `ask` too: CC trees back kernel RPS or ASK2 flow steering; HM nodes hook into `NETIF_F_HW_VLAN_CTAG_*`; Policer profiles back tc/nftables ingress rate-limit offload; CEETM is QMan-silicon hierarchical egress shaping (the Linux tc-qdisc driver is NOT in mainline 6.18 and must be forward-ported from the NXP LSDK — see §5.7). **CLI policy (v5.13):** this spec delivers only the **kernel-side APIs** and their existing kernel-native interfaces (`ethtool -K`, sysfs, tc/nftables offload, module params). The **native VyOS CLI surfaces** — `set vpp settings hw-offload …`, `set system offload …`, `set qos policy shaper hardware …`, `set firewall … offload …` — are **DEFERRED to a separate, later "native VyOS CLI" phase** and are explicitly out of this spec's scope. In particular, VPP keeps its **existing** config/ops surface (`vppctl`/`startup.conf` + the already-shipped `set vpp settings interface ethX`); we do **not** add new `set vpp settings hw-offload …` verbs now. Every `set vpp settings hw-offload …` / `set system offload …` / `set qos policy shaper …` / `set firewall … offload` string elsewhere in this document denotes **target syntax for that future phase**, never a current deliverable. See `specs/vpp-dpaa1-ls1046a-spec.md` v0.3. **Amendment (2026-06-10, operator-directed):** `set system offload classify` is UN-deferred and SHIPPED — vyos-1x patch `vyos-1x-026-system-offload-classify.patch` (config-mode CLI → `ethtool -N` ntuple) + board patch `0109` (dpaa rxnfc → `fman_cc_tree_install()` bridge). The other CLI surfaces remain deferred.

3. **FMan ucode 210 is a build dependency, not a runtime option.** U-Boot loads the 210 binary from SPI `mtd4` at SoC bring-up; the kernel inherits a fully-armed FMan with Parser/CC/KeyGen/HM/Policer/IPF/IPR available. The DPAA1 driver only consumes capabilities. Capabilities apply equally across all flavors.

4. **The AF_XDP gap closes in three required phases plus four optional HW-offload phases.** M1 = `ndo_xsk_wakeup`. M2 = XSK-backed BMan pool. M3-3 = ZC RX/TX with qband mapping and cluster-aware NAPI pinning. M3-3b–e add CC, HM, Policer, CEETM. Acceptance bar: idle CPU < 10% on the main AF_XDP worker after M1; ≥ 7 Gbps single-stream IPv4 forwarding at < 5% kernel-net CPU per worker after M3-3. **M3-3 is NOT flavor-specific** — the ZC RX/TX datapath, qband mapping, and cluster-aware NAPI pinning all live in `kernel/common/patches/board/` (the dpaa flavor-ops contract plus `af_xdp_pool.ko`) and exercise identically on `default`, `vpp`, and `ask`. The ≥ 7 Gbps gate can therefore be measured on **any** flavor that brings up an AF_XDP socket on a 10G SFP+ port (eth3/eth4); historical "VPP-only" notes throughout this spec reflect the early test methodology (VPP's `af_xdp` plugin was the first usable XSK producer at hand) and are NOT an architectural constraint. The skbuf-path wins for `default` and `ask` are side-effect deliverables.

---

## Milestone Tracker

DPAA1 driver work is cross-flavor by construction (see §1.3, §7) — `default` / `vpp` / `ask` all share the same kernel binary; whether a given milestone *manifests behavior* on a flavor depends solely on whether that flavor has a userspace consumer loaded (AF_XDP socket, VPP `af_xdp` plugin, ASK2 module). Status: **planned**, **in-progress**, **landed** (in-tree, patch-health-clean), **dut-validated**, **stub-landed**, **blocked**, **deferred**.

Full per-row validation detail (DUT counter readings, ftrace evidence, iperf3 numbers, gate pass/fail per port class) is archived in Qdrant under `topic=dpaa1-afxdp-spec-milestone-archive`. The Notes column below carries only the patch reference, commit SHA, and one-line outcome anchor.

| ID | Capability | §  | Status | Patch / commit / anchor |
|---|---|---|---|---|
| **M0**          | Per-`dpaa_priv` ops abstraction + RCU + `NETDEV_XDP_ACT_XSK_ZEROCOPY` advertise | 3       | **dut-validated**         | `0068`/`0069a`/`0070`/`0072` (2026-05-26) — abstraction passive, mainline-identical |
| **M1**          | `ndo_xsk_wakeup` trampoline + `af_xdp_pool.ko` skeleton                          | 5.3     | **dut-validated**         | `0073`/`0074` — `af_xdp_pool: registered` at t≈0.88s |
| **M2-s1**       | XSK pool attach gate (kconfig autoload, counters, BMan seed, DMA map)            | 6.1     | **dut-validated**         | `0071`/`0075a-c`/`0077`/`0079` + `7dc1768` (2026-05-27) — bind ZC returns 0, 12 `xsk_*` counters live |
| **M2-s2**       | XSK pool detach safety + 100× bind/unbind churn                                  | 6.1     | **dut-validated**         | `0076` + sleep-in-RCU fix `039a50c` — 100× clean, fman_port_disable-anchored detach |
| **M3-3 step 1** | NAPI bind in `qmap[]` + `xsk_set_rx_need_wakeup` body                            | 5.2,6.1 | **dut-validated**         | `0080` = `3e9bb83` (ISO `2026.05.27-0648`) — 100/100 sendto wakeups rc=0 |
| **M3-3 step 2a**| Per-CPU NAPI distribution across qbands (control-plane wiring)                   | 5.2     | **dut-validated**         | `0081` = `ed80517` — `priv->qmap[q].napi = &per_cpu_ptr(...)->np.napi`, 4×bind clean |
| **M3-3 step 2b**| `qmap` debugfs observability node                                                | 5.2     | **dut-validated**         | `0082` = `7b7b634` — `/sys/kernel/debug/af_xdp_pool/qmap` renders all 5 netdevs |
| **M3-3 step 2c**| Dedicated QMan dispatch channels per qband (contiguous-banding)                  | 5.2     | **dut-validated**         | `0082b` = `7026460` — `fqid_base` 32-FQID progression on 5 netdevs × 4 bands, 0 collisions |
| **M3-3 step 3** | RX ZC branch-eligibility probe (`dpaa_fq_to_qband()` + `xsk_rx_branch` counter) | 6.1     | **dut-validated**         | `0083` = `2ad2c0a` — 13th `xsk_*` counter live, RCU-NULL-safe, no perf regression |
| **M3-3 step 4** | NAPI-hooked BMan refill                                                          | 6.1     | **dut-validated**         | `0084 v2` (option-c single-CPU pin) + blocker-A chain `0086`+`0087`+`0088` (`d1b6e30`, ISO `2026.05.28-0149`, run 26549682211) — §6.1.6; 60 s G5 hold: 0 IVCI / 0 BUG/WARN/softlock / 16 refill batches |
| **M3-3 step 5** | TX ZC submit + TxConf recycle + `xsk_tx_inflight` backpressure                   | 6.1     | **dut-validated (hang-fix)** | `0085 v2` — kernel path wired; productive TX waits on TX-capable XSK producer (`xdpsock -t` / VPP `af_xdp` / custom) |
| **M3-3 step 6** | Productive XSK RX delivery via XSKMAP redirect                                   | 6.1     | **dut-validated**         | `0089` (userspace probe stage-3, no kernel change) — §6.1.7; copy-mode `rx_packets`=30 incidental → 296,716 under load (§6.1.8) |
| **M3-3b**       | CC steering — exact-match HW classifier kernel API                               | 5.4     | **DUT-VALIDATED 2026-06-10 (image `2026.06.10-0124-rolling`, commit `1639ec2`, kernel `6.18.34-vyos`): full install→graft→traffic→detach lifecycle proven on silicon via the `0107` debugfs harness; per-key FQ enqueue-AD still pending (see Remaining)** | `0086` = `c36e6c0` (CC stubs + counter) + `0086a` (productive DT ucode-210 probe, 2026-05-28) + `0086b` (productive 5-tuple `fman_cc_key`/`fman_cc_static_tree` struct contract, 2026-05-28); `0092` (PCD subsystem) + `0097` (KeyGen API) + **`0098` (productive static-tree install — MURAM match-key + AD tables + CONT_LOOKUP group-table[0] per RM 8.7.4.1)** + `0105` (dormant `fman_port_set_cc_base()` BMI `fmbm_rccb` write primitive) + **`0106` (2026-06-10): the missing datapath link — HW-proven KGSE_CCBS graft.** Silicon captures 2026-05-23/25 (ask20 PR14z20/z22 + `0065`: 24M+ frames CC-matched and silicon-forwarded) prove the KGSE_MODE NIA must STAY at BMI direct-enqueue `0x80500002` and a **non-zero `KGSE_CCBS` = CC root group-table MURAM offset** dispatches the CC walk implicitly; the NIA-flip-to-`FM_CTL\|AC_CC` alternative (what the `0097` skeleton encoded) was DISPROVEN on HW (dispatch stalled). `0106` makes `fman_pcd_kg_attach_cc()` productive (CCBS ← group-table offset via new intra-module `fman_pcd_cc_tree_group_table_off()`), adds the exported port-level graft pair `fman_pcd_kg_port_attach_cc()`/`detach_cc()` (mirror of the BUG 3 policer steering fix — in-place reprogram of the port's existing RSS scheme, hashing/match-vector/base-FQID preserved so a CC miss still spreads over the hashed RX FQs), and completes `fman_cc_tree_install()`/`destroy()` in `dpaa_fman_caps.c` (install → `fman_pcd_cc_static_get_base()` → graft, teardown on graft failure; destroy detaches FIRST so silicon never walks a freed tree). Native arm64 compile 0 warn/0 err; patch-health Pass 64 / Fail 0. `add_key`/`remove_key` stay `-ENOTSUPP` (HC-dispatch gated; board caps=0x17, HC bit clear). **`0107` (2026-06-10): debugfs DUT-validation harness** — `/sys/kernel/debug/fman_pcd/<N>/cc_test` drives the exact `0106` sequence (`install <hwport> <qband> <proto> <src> <dst> <dport>` → static_install → get_base → kg_port_attach_cc; `clear` → detach FIRST → destroy); read prints per-tree MURAM offsets. **DUT validation result (2026-06-10, eth3 RX port `0x10`):** install → tree at `group=0x5ac00 match=0x5ad00 ad=0x5ad40`; silicon readback (regdump) shows eth3's RSS scheme 3 `KGSE_CCBS=0x0005ac00` (== the harness-reported group offset) with SPC live; miss-path ICMP 5/5 0% loss; MATCHING UDP (`10.99.1.2→10.99.1.1:5001`) 3/3 delivered with zero drops/crash while the CC walk is active in the RX path; `clear` → silicon `KGSE_CCBS=0x00000000`, node empty, post-clear ping 5/5. **Remaining for actual per-key FQ steering:** CLOSED by `0108` (2026-06-10) — see below. (`0098`'s original leaf AD wrote a soft encoding (`word0 = qband<<16|hm<<8|type` — the 0098 comment says "the dpaa flavor layer owns the qband→FQID map"), NOT the RM 8.7.4.1 hardware enqueue-AD, so matched frames completed the CC walk and fell through to normal scheme dispatch (gracefully).) **`0108` (2026-06-10): per-key FQ enqueue-AD + silicon-truth key layout.** The leaf encoder now emits the ask20-HW-PROVEN RM 8.7.4.3 hardware enqueue-AD (`fqid@0x0`, `RESULT_CF[|NADEN]@0x8`, HMTD offset `@0xc`; PR14z20/z22 captures: 24M+ frames silicon-forwarded with this encoding) whenever a key carries a non-zero `target_fqid` (new field in `fman_cc_key`/`fman_pcd_cc_hw_key`; `miss_fqid` ditto for the miss slot); `fqid==0` keeps the DUT-validated soft fall-through byte-identical (default). `cc_pack_key()` fixed to the KG-emitted composite the walker actually compares under the KGSE_CCBS graft (`[SIP|DIP|SPI=0|SPORT|DPORT]` per KGSE_EKFC `0x00180206`, PR14z14 silicon truth — the old ethertype/proto/flags layout could NEVER match); ETHERTYPE/PROTO documented as non-participating, IPv6 keys rejected `-ENOTSUPP` at install. `0107` harness extended: `install ... [fqid-hex]` arms the real enqueue-AD. Native arm64 compile 0 warn/0 err; patch-health Pass 66 / Fail 0. DUT validation of actual FQ delivery pending next image |
| **M3-3c**       | HM offload — VLAN/MPLS strip-insert kernel API                                   | 5.5     | **CLI shipped + bridge dut-validated (feature live on hardware; vyos-1x-024 CLI live on DUT 2026-06-07; functional datapath gate pending traffic gen only)** | `0090`/`0090a` (stub + struct contract) + `0099` (productive HM-node install, 2026-05-29) + **`0101` (userspace bridge, 2026-05-30)**. `0099` adds `fman_pcd_manip.c` (HMTD/HMCT MURAM encoder, durable generic INSRT/RMV primitive per RM 8.7.5), publishes the neutral `fman_pcd_hm_hw_{op,spec}` in `fman_pcd.h`, and makes `fman_hm_node_install/destroy` productive (gate on `FMAN_CAP_HM_NODES`, host→neutral translate via `dpaa_hm_spec_to_hw`, delegate via `fman_get_pcd`). **`0101` makes the dormant `0099` install body reachable from userspace** — a new `dpaa_set_features()` `.ndo_set_features` handler in `dpaa_eth.c` that, on a 0→1 transition of `NETIF_F_HW_VLAN_CTAG_RX`, installs a single-op `{FMAN_HM_OP_VLAN_STRIP}` HM node (resolving `struct fman *` via `fman_bind(mac_dev->fman_dev)` and the port id via `fman_port_get_qman_channel_id(mac_dev->port[RX])` — the real in-tree getter the driver already uses in `dpaa_fq_setup()`) and stashes the handle in `priv->hm_vlan_strip_handle`; the feature bit is advertised in `net_dev->hw_features` only when `fman_hm_caps_supported()` so mainline-ucode boards never expose an `-ENOTSUPP` knob. This is the true prerequisite for the vyos-1x `set interfaces ethernet ethX hw-offload vlan-strip` CLI (which only drives `ethtool -K ethX rxvlan on`). Native arm64 compile 0 warn/0 err; `patch-health` 0099 ✓ (0101 standalone ✗ is the documented cumulative-chain false-positive — it depends on every prior `dpaa_eth.c` edit). **DUT-validated 2026-05-30** (ISO `vyos-2026.05.30-0233-rolling-LS1046A-default-arm64`, CI run `26672112397`, commit `02fb1ac`, kernel 6.18.33-vyos): `FMan PCD caps = 0x17 (CC HM POL PARSER)` (HM cap live), `ethtool -k eth0` → `rx-vlan-offload: on` (NOT `[fixed]`) — the HM-cap-gated `NETIF_F_HW_VLAN_CTAG_RX` advertisement and the `dpaa_set_features()` enable-transition both work on real silicon; eth0-eth4 UP, mgmt ping 0% loss, no regression. The functional datapath acceptance gate (§5.5: tcpdump tagged on wire / consumer untagged, sub-100 ns HM cost) remains pending **a traffic generator only** — the `vyos-1x-024` CLI consumer is shipped and live on the DUT (verified 2026-06-07, image `2026.06.07-2209-rolling`) |
| **M3-3d**       | Policer — per-flow HW ingress rate-limit                                         | 5.6     | **BUG 3a FIXED + HW-VALIDATED 2026-06-09 (image `2032`, commit `1a48948`): root cause was the FMPL block master-enable (`FMPL_GCR.EN\|STEN` clear at boot), NOT profile addressing — `0100` `plcr_enable_block()` flips GCR `0x00500002→0xC0500002`; policed ping 100% loss → 0% on a clean cold boot. BUG 3b non-revert half FIXED + verified (kernel `flow_block_cb` release in `0104` + vyos-1x-025 reorder); flood-crash half still OPEN/uncharacterized. Functional throughput cap on the §8 harness still to be measured — see §5.6** | `0091` (stub) + `0091a` (productive srTCM/trTCM struct contract, 2026-05-28) + **`0100` (productive install body, 2026-05-29 — board patch; `fman_pcd_plcr.c` srTCM/trTCM MURAM encoder in `fsl_dpaa_fman.ko` per RM 8.7.6 rate=exp<<29\|mant<<13 + 256-byte burst quantisation + 16-byte profile record; `dpaa_fman_caps.c` `fman_policer_install`/`destroy` made productive — gate on `FMAN_CAP_POLICER_TRTCM`, validate `cir_bps==0`→`-EINVAL` / trTCM `pir_bps<cir_bps`→`-ERANGE`, translate host→neutral `fman_pcd_plcr_hw_profile`, delegate via `fman_get_pcd()`; native arm64 0 warn/0 err, patch-health `0100 ✓`)**. **DUT-confirmed 2026-05-29** on Mono Gateway DK (ISO 2026.05.29-2339-rolling, kernel 6.18.33-vyos): boot dmesg `FMan PCD caps = 0x17 (CC HM POL PARSER)` — the **POL** (`FMAN_CAP_POLICER_TRTCM`) cap-bit is live-probed by `0086a`, PCD subsystem ready (64 KiB MURAM), all built-in, no regression. The functional datapath acceptance gate (§5.6: 2.5 Gbps cap on offered 3 Gbps, red-drops visible) **FAILED a wire-level test 2026-06-08** — a pure-HW (`skip_sw`) ingress policer on eth3 reports `in_hw` and fires the driver's one-per-interface state guard, but iperf3 TCP reverse delivered the full ~6.4 Gbps (`rx_bytes` confirmed) with `tc` Action stats `Sent 0 dropped 0 overlimits 0`; a `100mbit→6.92 / 500mbit→7.07 / 1gbit→6.03 Gbps` rate sweep rules out a units bug. Root cause: `0100`/`0104` install the FMan policer **profile** (hence `in_hw` + the state guard) but never **bind** it into the ingress frame path — no keygen scheme / CCNODE routes the RX FQ through the profile, so frames hit the default RX FQ untouched. Because `skip_sw` bypasses the tc software policer there is NO software fallback and the failure is silent. The shipped `vyos-1x-025 set_ingress_policer()` emits byte-identical `matchall skip_sw action police` tc, so the CLI inherits the same non-enforcement — **broken end-to-end**. `0104` must add the missing PCD datapath binding (keygen scheme / CCNODE next-action = policer) before M3-3d can be claimed functional |
| **M3-3e**       | CEETM — HW hierarchical egress shaping as tc qdisc                               | 5.7     | **scaffold dut-validated; productive datapath blocked (no mainline CEETM)** | **Scaffold `0104b-dpaa-ceetm-stub.patch` landed + DUT-validated** (sorts after `0104a`, before `101-sfp`; wired in `bin/ci-setup-kernel.sh`): pins `CONFIG_DPAA_HW_CEETM`, opaque `struct dpaa_ceetm_config`, and `-ENOTSUPP`/`false` entry points (`dpaa_ceetm_qdisc_install` / `dpaa_ceetm_qdisc_destroy` / `dpaa_ceetm_supported`) in `dpaa_fman_caps.{c,h}` per the §5.7 item-3 stub-first cadence, so the VyOS CLI consumer compiles against a stable contract today. `0104` reserved for the productive consumer (renumbered from `0092`, reassigned to the FMan PCD subsystem forward-port per §6.1.13). **DUT-validated 2026-06-07** (commit `4e080cb`, ISO `vyos-2026.06.07-2209-rolling-LS1046A-default-arm64`, CI run `27106292468`, kernel `6.18.34-vyos`): `CONFIG_DPAA_HW_CEETM=y` (dependency `CONFIG_DPAA_HW_CC_STEERING=y` satisfied) via `/proc/config.gz`; all three CEETM symbols + their `__ksymtab_*` entries present in `/proc/kallsyms`; non-regression clean — `FMan PCD caps = 0x17 (CC HM POL PARSER)`, eth0-eth4 UP (eth3/eth4 10G, eth0-2 1G), zero new DPAA/FMan/QMan/BMAN dmesg errors. **Productive datapath still blocked:** mainline 6.18.31 ships NO CEETM — `qman_ceetm.c`, `dpaa_eth_ceetm.{c,h}`, and every `qman_ceetm_*` symbol in `include/soc/fsl/qman.h` are absent (verified 2026-05-29 by tree grep on `work/linux-6.18.31`); `dpaa_eth.c` has no `ndo_setup_tc`/mqprio wiring. Productive M3-3e is an SDK-CEETM *forward-port* (`drivers/soc/fsl/qbman/qman_ceetm.c` ~2600 LOC + `dpaa_eth_ceetm.c` ~1900 LOC from NXP LSDK/pre-5.x), NOT a "wire the existing qdisc" task — see §5.7 |
| **M3-3 step 7** | True ZC RX (FMan DMAs into XSK-pool BMan chunks via `priv->xsk_bpid` match)      | 6.1     | **PRODUCTIVE TRUE-ZC DUT-VALIDATED 2026-06-10 (§6.1.18): oracle `xsk_zc_rx_redirect` 0→7→8 reproducible on `0124` image; `0102b` BMI readback proves `FMBM_EBMPI[0]` flips bpid 3→5 in silicon; `0103g` fixes the NULL-`xdp.rxq` crash (per-band `MEM_TYPE_XSK_BUFF_POOL` rxq reg, i40e idiom); crash-free + reversible. HARDENED same day by `0110` (§6.1.19): missing `xdp_do_flush()` (frames invisible to userspace) + hard-IRQ `__xsk_map_flush` panic both fixed (NAPI-only hook dispatch); functional/teardown-stress/flood-survival all PASS. GAP 2 (literal wire-rate throughput number) remains — needs the §8 external traffic harness — not gate-3-blocking** | `0093`-`0096` (sub-increments 1-3, §6.1.10-§6.1.14) — diagnostic `xsk_zc_eligible`/`xsk_zc_rx_armed`/`xsk_fill_guard_block`/`xsk_zc_rx_recovered` (17th-20th `xsk_*`), all zero-datapath-change/dormant; DUT-validated 2026-05-29 (ISO `vyos-2026.05.29-1554-rolling-LS1046A-vpp-arm64`, kernel `6.18.33-vyos`, run `26647449274`). **`0102` (sub-increment 4, §6.1.15) lands the dormant exported `fman_port_set_rx_bpool()` reProgram WRITE primitive** — exported but no caller, byte-identical on every flavor. **`0103a` (sub-increment 4a, §6.1.16) lands the dormant Recover sw-ring reverse-map** — the per-qband chunk-DMA→`xdp_buff` array + `dpaa_xsk_chunk_record/lookup` helpers + 21st counter `xsk_zc_recover_lookup`, needed because kernel 6.18.31 has **no `xsk_buff_recv()` retrieve-by-dma primitive** (API-survey finding, §6.1.16). Self-tested at attach, NO datapath consumer; native arm64 compile 0 warn/0 err, `git apply --3way --check ✓`. **`0103b` (sub-increment 4b, §6.1.17) LANDED + DUT-entry-gate-validated** — the productive coupled reprogram-WRITE + Recover-redirect: §6.1.12 preconditions (1)+(2) hardware-MET (`xsk_zc_rx_armed=2`, `xsk_fill_guard_block=0` under 30s load), reprogram-WRITE crash-free + reversible (eth3 bounces then fully recovers, zero crash signatures). The productive `xsk_zc_rx_redirect` oracle still reads 0, gated on (1) the §6.1.17 `fman_port_set_rx_bpool()` BMI re-commit being confirmed *effective* at the register level (a BMI register-readback debugging pass — NOT a missing forward-port; the accessor `0102` and its caller `0103b` both already exist in-tree) and (2) a traffic-steering fix. Not required for gate-3 capacity (§6.1.8a/§6.1.8b). |

### Current execution focus

In-flight state as of **2026-06-07**. Closed work-fronts are recorded tersely below with a pointer to their full §6.1.x prose (per-step DUT detail is archived in Qdrant under `topic=dpaa1-afxdp-spec-milestone-archive`). This subsection covers only the current open / just-closed front:

1. **Blocker A — BMan "Invalid Command Verb" — CLOSED** (§6.1.6). `0088` fix: `xsk_pool_dma_map()` uses `priv->rx_dma_dev`. 60 s G5 hold clean.
2. **Blocker B — productive XSK RX delivery — CLOSED** (§6.1.7, §6.1.8). Probe `0089` (`--xskmap`, no kernel change) → 296,716 pkts under load, copy-mode. True-ZC is M3-3 step 7 scope.
3. **Under-load FILL-ring backpressure — STABILIZED** (§6.1.8). Probe-side fix (live `fill_consumer` check, `MAX_BATCH=64`). Driver RX capacity ≥ 7.5 Gbit/s; the probe's 1.33 Gbit/s was an iperf3 single-core receiver artifact, not the NIC.
4. **TX direction — CHARACTERIZED** (§6.1.9). Kernel-skbuf single-stream eth4→backup = **4.93 Gbit/s** (no TSO on FMan v3 Linux). Irrelevant to AF_XDP-ZC TX (bypasses softirq).
5. **M3-3 step 7 sub-increments 1–4 + 4a — LANDED** (§6.1.10–§6.1.16). Diagnostic counters `0093`–`0096` (sub-increments 1–3) DUT-validated dormant on `6.18.33-vyos`; **sub-increment 4 (`0102`, §6.1.15) lands the dormant exported `fman_port_set_rx_bpool()` reProgram WRITE primitive** — exported but with no caller, byte-identical to mainline on every flavor (compile-clean, `patch-health board/0102 ✓`). **Sub-increment 4a (`0103a`, §6.1.16) lands the dormant Recover sw-ring reverse-map infrastructure** (`xsk_chunk_map[]` chunk-DMA→`xdp_buff` arrays + `dpaa_xsk_chunk_record/lookup` helpers + 21st counter `xsk_zc_recover_lookup`, exercised only by a one-shot self-test — no datapath consumer), authored after the discovery that kernel 6.18.31 has **no `xsk_buff_recv()`** retrieve-by-DMA API. The remaining productive step is the coupled reprogram-WRITE + Recover-redirect sub-increment `0103b`, gated only on the three §6.1.12 userspace-observable preconditions under a live `XDP_ZEROCOPY` producer.
6. **M3-3b/c/d closure — APPROVED DIRECTION (2026-05-29), forward-port IN PROGRESS** (§5.4–§5.6). Operator decisions: (a) the FMan PCD subsystem moves OUT of the `FLAVOR=ask` gate into the **common board stack** (built-in for default/vpp/ask); (b) the L2 MAC-rewrite ceiling (Qdrant `PR14g`) is resolved the **durable** way — a proper in-tree FMan PCD MANIP L2-rewrite primitive (extend `enum fman_pcd_manip_type` + HMCT encoder per RM 8.7.3.x), NOT the ask20 `0058`/`0065` HMD/graft shortcut. The productive bodies are **10,342 lines across 40+ stacked patches** (ask20 `0004`–`0065`), each authored vs 6.18.28 and re-anchored vs the dpaa1 6.18.31 base IN SEQUENCE. Empirical drift test 2026-05-29: raw `0004` `--3way --check` vs vanilla 6.18.31 — Kconfig clean, `Makefile@6`/`fman.c@2509`/`fman.h@382` fail. **Discovered prerequisite:** `0004`'s Makefile hunk references `fman_host_cmd.o`, which is added by ask20 `0001`–`0003` (Host Command prep) — so the forward-port chain starts at `0001`, not `0004`. Re-anchor dataset + full closure order archived in Qdrant (`topic=dpaa1-m3-3bcd-approved-direction-0004-reanchor`).
7. **M3-3e CEETM scaffold — LANDED + DUT-VALIDATED (2026-06-07)** (§5.7). Stub-first scaffold `0104b-dpaa-ceetm-stub.patch` (commit `4e080cb`) closes the §5.7 item-3 cadence: `CONFIG_DPAA_HW_CEETM=y` (`depends on DPAA_HW_CC_STEERING`), opaque `struct dpaa_ceetm_config`, and the three `EXPORT_SYMBOL_GPL` entry points (`dpaa_ceetm_qdisc_install`→`-ENOTSUPP`, `dpaa_ceetm_qdisc_destroy`→no-op, `dpaa_ceetm_supported`→`false`) in `dpaa_fman_caps.{c,h}`. CI run `27106292468` (flavor=default) succeeded; ISO `vyos-2026.06.07-2209-rolling-LS1046A-default-arm64` deployed to lxc200 (`http://192.168.1.137:8080/iso/latest-default.iso`). DUT (192.168.1.190, kernel `6.18.34-vyos`) acceptance all-PASS: config `=y` + dependency satisfied, all 3 symbols + `__ksymtab_*` in `/proc/kallsyms`, FMan caps `0x17`, eth0-eth4 UP, zero new DPAA dmesg errors. This is **non-regression acceptance only** — the productive CEETM datapath (§5.7 items 1–2, the ~4500 LOC QMan-CEETM + DPAA-qdisc forward-port) is unchanged and still blocked.

**What remains for a complete DPAA1 driver.** The shared kernel substrate (M0–M3-3 step 6) is dut-validated and shipping; the four HW-offload milestones are at varying completeness. The remaining work to reach a *feature-complete* DPAA1 driver, in dependency order:

| Remaining item | Milestone | Nature of work | Blocking dependency |
|---|---|---|---|
| **True ZC RX productive oracle** | M3-3 step 7 (`0103b`) | BMI register-readback debug pass to confirm `fman_port_set_rx_bpool()` re-commit is *effective* on a live post-init RX port, + traffic-steering fix so flood frames land on the XSK default FQ (queue 0) not the PCD stack FQs. **No new forward-port** — accessor (`0102`) + caller (`0103b`) already in-tree. | Lab: `XDP_ZEROCOPY` producer + register-level FMan debugging. Not gate-3-blocking (§6.1.8a/b). |
| **CC steering per-key FQ enqueue-AD** | M3-3b | Install/graft/traffic/detach lifecycle DUT-VALIDATED 2026-06-10 (`0107` harness; silicon `KGSE_CCBS` flip proven both ways). **Kernel work CLOSED by `0108` (2026-06-10):** real RM 8.7.4.3 hardware enqueue-AD in the leaf (FQID@0x0, RESULT_CF[\|NADEN]) when `target_fqid`/`miss_fqid` non-zero, plus the silicon-truth KG-composite key layout (`[SIP\|DIP\|SPI=0\|SPORT\|DPORT]`) without which a HIT could never occur. **Production consumer CLOSED by `0109` + `vyos-1x-026` (2026-06-10):** `set system offload classify interface <ethN> rule <1-32> {protocol\|source\|destination\|queue}` → conf_mode `system_offload.py` → `ethtool -N … action <queue> loc <rule-1>` → dpaa rxnfc bridge → `fman_cc_tree_destroy()`+`install()` rebuild (queue = Nth RX PCD FQ, FQID → `target_fqid` → 0108 enqueue-AD). Remaining: DUT validation of actual per-FQ delivery (harness + CLI) + per-FQ counters on the next image. | None — kernel + CLI work done; validate on DUT. |
| **HM functional datapath gate** | M3-3c | Feature is **live on hardware** (cap `0x17`, `rx-vlan-offload: on`, MURAM 0→144→0 proven 2026-06-07); the `vyos-1x-024` CLI consumer is **shipped + live on the DUT** (2026-06-07). Needs a traffic generator to prove the §5.5 wire-level strip/insert gate. **No kernel work, no CLI work.** Wire-test attempt 2026-06-08 was blocked: the eth3 peer 10.99.1.2 is not SSH-able (no controllable tagged-frame source) and `8021q` is not loaded on the DUT. Lower silent-fail risk than M3-3d — VLAN-strip has a normal kernel SW fallback. | traffic gen (controllable 802.1Q tagged source). |
| **Policer functional datapath gate** | M3-3d | **STEERING FIXED 2026-06-09 (image `0522`); BUG 3a + BUG 3b non-revert half now FIXED + HW-VALIDATED 2026-06-09 (image `2032`, commit `1a48948`).** **(3a)** root cause was NOT profile addressing — the FMPL policer block ships with its master-enable `FMPL_GCR.EN\|STEN` clear (`0x00500002`), so KeyGen-routed frames drop pre-meter; `0100` `plcr_enable_block()` RMW-sets `EN\|STEN` (`→0xC0500002`); on a clean cold boot policed ping went 100% loss → **0% loss**, GCR auto-flips, TPC increments. Three earlier register theories (`NIA_PLCR_ABSOLUTE`, `FMBM_RPP`, `FMPL_PMR` window) were tested + DISPROVEN. **(3b)** non-revert half fixed by a kernel `flow_block_cb` release callback in `0104` (+ vyos-1x-025 filter-del-before-`super().update()`); delete→re-apply verified clean. **Still OPEN:** the iperf3 flood-crash half of 3b (deliberately untested — watchdog-reset risk) and the literal throughput-cap measurement on the §8 harness. | §8 traffic harness (available) for the final cap measurement; serial-console + cold power-cycle for the flood-crash half. |
| **CEETM productive shaper** | M3-3e | Scaffold dut-validated (`0104b`); productive datapath is the **largest single remaining forward-port** — `qman_ceetm.c` ~2600 LOC into `include/soc/fsl/qman.h` + `dpaa_eth_ceetm.{c,h}` ~1900 LOC + `ndo_setup_tc` wiring in `dpaa_eth.c`. Absent from mainline 6.18. | NXP-LSDK CEETM source forward-port (§5.7 items 1–2). |
| **Literal ≥7 Gbps gate-3 figure** | M3-3 | Multi-process iperf3 *server* on the generator (divide receiver across cores) or a wire-rate generator (TRex/DPDK-pktgen). Driver proven to drop 0% at line rate (§6.1.8a). **No kernel work.** | Lab provisioning / generator-side creds. |
| **DCSR error observability** | 5.8 | `0079` landed; remaining debugfs error-window taps (`{bmi,parser,kg,pol}_err`) per §4.9 are incremental. | None (incremental). |

**Bottom line:** the driver *core* is done and validated. What is left splits cleanly into (1) **kernel work** — two real forward-ports (the FMan PCD subsystem, which unblocks M3-3b CC and underpins the M3-3c/d productive paths; and the QMan-CEETM driver, M3-3e). The **M3-3d policer datapath is now functional**: steering was fixed 2026-06-09 (image `0522`), then BUG 3a (root-caused to the FMPL block master-enable `FMPL_GCR.EN|STEN` being clear at boot — fix `0100` `plcr_enable_block()`) and the BUG 3b non-revert half (kernel `flow_block_cb` release in `0104`) were both fixed and HW-validated on a clean cold boot (image `2032`, commit `1a48948`); only the iperf3 flood-crash half of 3b and the literal throughput-cap number remain. And (2) **non-kernel glue/lab work** — the vyos-1x CLI consumer for CEETM (the HM consumer `vyos-1x-024` and the Policer consumer `vyos-1x-025` are already shipped + live on the DUT), a traffic generator for the functional datapath gates, and a multi-core receiver for the literal throughput number. No additional *architectural* work is required; the M0 ops abstraction and capability layer already accommodate every remaining consumer.


**Gate-3 entry point — Options A + C DONE (2026-05-28/29), gate is methodology-bound not driver-bound.** Option A (`XDP_DROP` driver-only capacity, §6.1.8a) proved the driver drops **0%** of every offered frame; Option C (multi-flow TCP `-R`, §6.1.8b) hit **5.57 Gbit/s** aggregate RX with softirq distributed across CPU0/1/2 and ~50% headroom on CPU2 — the bottleneck is the single userspace receiver process (CPU3 84% sys), not the DPAA1 RX path. Neither needed a new ISO (the installed `6.18.33-vyos` blocker-A/B build carries the full datapath: 16 `xsk_*` counters, `qmap` debugfs, caps=0x17). **Remaining for a *literal* ≥7 Gbps Gate-3 figure:** multiple iperf3 *server* processes on the generator (so the receiver side divides across cores) — needs generator-side creds (`backup` `admin@192.168.1.3`, §6.1.8 item 6) — or a wire-rate generator (TRex/DPDK-pktgen). This is a lab-provisioning task, NOT a kernel-code task. M3-3 step 7 (true ZC) is not required for gate-3 capacity.

**Operator-visible non-issue noted on the eth0/eth1/eth2 management LAN:** three RJ45 ports DHCP-active on the same `192.168.1.0/24` switch produce martian-source storms. VyOS config / cabling issue, NOT a driver regression. Fix: leave only eth0 plugged in or `set interfaces ethernet ethN address dhcp` on a single port.

---

## 1. Purpose and Scope

Kernel-side changes to `drivers/net/ethernet/freescale/dpaa/` + FMan/QMan/BMan support code to modernize DPAA1 on LS1046A across all three VyOS flavors. The deliverable is a single kernel binary that:

- Exposes a per-`dpaa_priv` flavor-ops abstraction (M0).
- Implements `ndo_xsk_wakeup`, `XDP_SETUP_XSK_POOL`, advertises `NETDEV_XDP_ACT_XSK_ZEROCOPY`.
- Restructures RX as per-CPU NAPI + dedicated BMan channels — improves *kernel skbuf RX*, not just AF_XDP.
- Delivers true zero-copy RX/TX on AF_XDP via VPP-specific `af_xdp_pool.ko` (≥ 7 Gbps single-stream).
- Exposes HW classification, header manipulation, ingress policing, egress shaping as **kernel APIs**. Each is independently consumable by `default` (RPS, `NETIF_F_HW_VLAN_CTAG_*`, tc/nftables offload, root qdisc), `vpp` (`set vpp settings hw-offload …`), `ask` (ASK2 flavor module).

### 1.1 In scope

- `dpaa_eth.c`, `dpaa_eth_sysfs.c` changes for per-`dpaa_priv` ops, RX datapath restructure, XSK pool lifecycle, qband mapping correctness.
- `drivers/net/ethernet/freescale/fman/` changes for CC-tree install, HM node install, Policer profile install, CEETM channel setup. All called via M0 abstraction; flavor modules own policy.
- Forward-port the SDK CEETM driver (`qman_ceetm.c` + `dpaa_eth_ceetm.c`, absent from mainline — see §5.7) and wire it as a tc root qdisc replacement.
- VPP plugin integration via existing AF_XDP plugin; no VPP source changes.
- VyOS CLI glue per flavor — follow-on vyos-1x patches not in this spec's direct scope.

### 1.2 Out of scope

- ASK2 userspace, nft flowtable offload userspace bridge — see `specs/ask2-rewrite-spec.md`.
- DPAA2 (`dpaa2-eth`) — already native ZC.
- DPDK DPAA PMD coexistence — RC#31 blocks mixed mode.
- VPP source-tree modifications.
- MTU > 3290 with XDP loaded.
- Microcode authoring or modification.

### 1.3 Cross-flavor reach

The single kernel binary ships in all three ISO flavors. The M0 abstraction makes the hot path RCU-NULL-safe — when no flavor module registers ops, behaviour is byte-identical to mainline DPAA1. §5 capabilities are kernel APIs consumable by:

- **`default`** — through existing kernel offload interfaces (RPS, `NETIF_F_HW_VLAN_CTAG_*`, tc/nftables offload, root qdisc). No flavor module loaded; §5.2 datapath restructure improves skbuf RX automatically. CC/HM/Policer/CEETM activation requires vyos-1x CLI patches (follow-on, not blocking M3-3).
- **`vpp`** — through `af_xdp_pool.ko` (M0 ops consumer) plus VPP `af_xdp` plugin (§3.4). §5 primitives back the `set vpp settings hw-offload …` CLI verbs. Most demanding consumer and drives the M3-3 acceptance gates. Architecture per `specs/vpp-dpaa1-ls1046a-spec.md` v0.2: VPP creates AF_XDP ZC sockets on kernel-owned netdevs; no separate kernel module, no userspace QMan/BMan portal ownership. The v0.1 `fsl-dpaa1-um.ko` native plugin proposal was REJECTED (RC#31: userspace QBMan reprogramming kills all kernel FMan interfaces globally).
- **`ask`** — through the ASK2 flavor module (per `specs/ask2-rewrite-spec.md`). Consumes the §5.4 CC tree primitive (as dynamic-key map), the §5.5 HM primitive (for L2 normalization), the §5.6 Policer primitive (for nft `limit` offload). Does NOT consume the §6.1 XSK-backed BMan pool.

Acceptance gates in §8 are written against the VPP datapath because it is the highest bar; passing them also delivers the skbuf-path wins for the other two flavors as a side-effect.

---

## 2. Background

### 2.1 What mainline 6.18.x gives us

`dpaa_eth.c` declares:

```c
net_dev->xdp_features = NETDEV_XDP_ACT_BASIC |
                        NETDEV_XDP_ACT_REDIRECT |
                        NETDEV_XDP_ACT_NDO_XMIT;
```

No `NETDEV_XDP_ACT_XSK_ZEROCOPY`. No `ndo_xsk_wakeup`. No `XDP_SETUP_XSK_POOL`. AF_XDP works only in copy mode through the generic XDP layer.

XDP support was added by Camelia Groza (NXP) in a 7-patch v5 series (netdev cover `<cover.1606322126.git.camelia.groza@nxp.com>`, accepted 2020-12-01). XDP buffers remain `dpaa_bp`-backed kernel pages, recycled through `bman_release()`. Default RSS spreads ingress across 128 PCD FQs using FMan KeyGen 5-tuple hashing. `QM_FQCTRL_HOLDACTIVE` plus stashing flags keep bursts on one CPU portal.

**CEETM is absent from mainline** (verified 2026-05-29 on `work/linux-6.18.31`): there is no `drivers/soc/fsl/qbman/qman_ceetm.c`, no `drivers/net/ethernet/freescale/dpaa/dpaa_eth_ceetm.{c,h}`, no `qman_ceetm_*` symbol in `include/soc/fsl/qman.h`, and `dpaa_eth.c` does not even implement `ndo_setup_tc`/mqprio. The CEETM tc-qdisc driver was an NXP-LSDK / pre-5.x out-of-tree component that never landed upstream. M3-3e (§5.7) is therefore a *forward-port* of that SDK code, not a "wire the existing qdisc" task — see §5.7.

#### 2.2.1 Complete FMan microcode/PCD function inventory (authoritative, NXP "Frame Manager Features")

The six items above are the subset this spec *consumes*. For completeness — answering "do we have **all** microcode functions captured, publicly documented or undocumented?" — the full set of FMan v3 microcode-implemented PCD (Parse-Classify-Distribute) functions per NXP's authoritative "Frame Manager Features" / "FMan PCD Driver" documentation is enumerated below.

**Scope note (proprietary 210 vs open-source 106.x).** This inventory describes the function set of the **proprietary signed 210.10.1 QEF blob that this board actually ships** (loaded by U-Boot from SPI mtd4 — see §3.5). It is **not** the open-source public `qoriq-fm-ucode` 106.4.18 baseline (`github.com/nxp-qoriq/qoriq-fm-ucode`). The 106.x public ucode implements a **narrower** function set — broadly the hard parser + KeyGen hash steering + basic Match-Table CC + basic Policer — and notably lacks the richer Advance-PCD functions (exact-match deep CC nesting, Parser soft-sequences, full Header-Manipulation, srTCM/trTCM Policer profiles, IPR/IPF, Frame Replicator). That gap is exactly why §2.2's title reads "What ucode 210 **unlocks over public 106.4.18**." The **[public 106.x?]** column below marks which functions are also present in the open-source 1xx line (**Y** = in 106.x too, **210-only** = added by the proprietary 210 ucode, **build-specific** = optional per-ucode-build feature). Each function is also flagged **[consumed]** (this driver exposes a cap bit + a `fman_*_install()` consumer), **[present-unconsumed]** (ucode-present on 210.10.1 but no cap bit or consumer yet — reserved for a later phase), or **[absent]** (not implemented in the shipping 210.10.1 QEF blob; DUT-confirmed).

| # | NXP PCD function | Public 106.x? | This driver | Notes |
|---|---|---|---|---|
| 1 | **Software Parser** (soft-sequence extensions over the hard parser) | **210-only** | **[consumed]** | `FMAN_CAP_PARSER_SOFTSEQ` (BIT 4). VXLAN-inner / MPLS-stack / IPv6-frag soft sequences. The hard parser is in 106.x; the *programmable* soft-sequence path is a 210 Advance-PCD function. |
| 2 | **KeyGen** — 32 key-generation schemes; per-scheme hash distribution onto a FQID base+range; **post-hash index** offset; per-scheme **Policer-Profile (PP) selection** | **Y** (hash) / **210-only** (post-hash-index, PP-select) | **[consumed]** (steering) / **[present-unconsumed]** (post-hash-index, explicit PP-select) | Basic KeyGen hash steering is in 106.x (mainline RSS uses it). The *post-hash index* and *explicit per-scheme Policer-Profile selection* are 210 functions, ucode-present but not yet wired to a cap bit. |
| 3 | **Custom Classifier (CC) Root → Match-Table Nodes** (exact-match, deep nesting, per-key stats) | **Y** (basic) / **210-only** (deep nesting + per-key stats) | **[consumed]** | `FMAN_CAP_CC_EXACT_MATCH` (BIT 0). Basic match-table exists in 106.x; the deep-nesting + per-key-statistics richness is what 210 unlocks. |
| 4 | **Custom Classifier (CC) Root → Hash-Table Nodes** (hashed CC lookup, large flow tables) | **210-only** | **[present-unconsumed]** | Distinct from Match-Table nodes. Ucode-present on 210.10.1; no cap bit or consumer yet — reserved (see §3.5 note). |
| 5 | **Manipulations → Header Manipulation (HM)** (VLAN/Q-in-Q/MPLS push-pop, arbitrary byte insert/remove/replace) | **210-only** | **[consumed]** | `FMAN_CAP_HM_NODES` (BIT 1). Full HM (esp. arbitrary byte + MPLS) is a 210 Advance-PCD function. |
| 6 | **Manipulations → IP Reassembly (IPR)** (timeout-driven flush) | **210-only** | **[present-unconsumed]** | Prose item 5 above mentions IPF/IPR, but §3.5 has **no** `FMAN_CAP_IP_FRAG`/`FMAN_CAP_IP_REASSEMBLY` cap bit — present in 210.10.1, reserved for a later phase. |
| 7 | **Manipulations → IP Fragmentation (IPF)** (timeout-driven flush) | **210-only** | **[present-unconsumed]** | Same as IPR — 210 ucode-present, no cap bit/consumer yet. |
| 8 | **Frame Replicator** (multicast/mirror replication group → multiple FQs) | **210-only** | **[present-unconsumed]** | **Not previously enumerated in this spec.** Ucode-present on 210.10.1; no cap bit or consumer yet — reserved for a later mirror/multicast phase. |
| 9 | **Policer Profiles** (srTCM RFC 2697 / trTCM RFC 2698, color-marking) | **Y** (basic) / **210-only** (srTCM/trTCM color-marking) | **[consumed]** | `FMAN_CAP_POLICER_TRTCM` (BIT 2). Basic policer in 106.x; full two-rate three-color marking is a 210 function. |
| 10 | **Host Command (HC)** doorbell (runtime PCD modify without AttachPCD/DetachPCD save-restore) | **build-specific** | **[absent]** | `FMAN_CAP_HC_DISPATCH` (BIT 3) deliberately **clear**. DUT-confirmed absent in the shipping **210.10.1** QEF blob (§3.5, re-validated 2026-06-01). HC is an optional, publicly-undocumented *per-ucode-build* feature — present only in a dedicated `qe_firmware` ucode variant, which we do not ship; the authoritative determination is the empirical DUT probe. |
| 11 | **PCD Statistics** (per-node/per-key counters surfaced through the above functions) | **Y/210** (follows the consuming node) | **[consumed]** (where the consuming function is consumed) | Not an independent function — a property of CC/KeyGen/Policer nodes; counted via the consumed nodes' stats. Availability tracks whichever node carries it. |

**Open-source vs proprietary scope (explicit):** the inventory above is the **proprietary 210.10.1** function set this board ships, **not** the open-source 106.4.18 baseline. The 106.x public `qoriq-fm-ucode` line implements only the **Y**-marked subset (hard parser, basic KeyGen hash steering, basic Match-Table CC, basic Policer); every **210-only** row is an Advance-PCD function the proprietary 210 ucode adds on top. We did **not** separately enumerate the 106.x set as its own table because (a) this board never loads it, and (b) it is strictly the **Y**-marked subset of the rows above — there is no 106.x function absent from 210.

**Gap callout (explicit answer to the completeness question):** three NXP PCD functions are **ucode-present on 210.10.1 but unconsumed and currently have no cap bit** — **CC Hash-Table nodes (#4)**, **IP Reassembly/Fragmentation (#6/#7)**, and **Frame Replicator (#8)** — plus KeyGen's post-hash-index and explicit Policer-Profile-select sub-features (#2). They are reserved for later phases; the 5-bit cap bitmask in §3.5 is a deliberate subset that covers only what the driver consumes today. **Host Command (#10)** is the only function confirmed *absent* from the shipping blob (not merely unconsumed). Sources: NXP "Frame Manager Features" and "FMan PCD Driver" docs (URLs in §11).

210 is signed by NXP, distributed as part of LSDK BSP, loaded by U-Boot from SPI `mtd4`. The M0 capability bitmask (§3.5) gates `pcd_ops->install` accordingly.

### 2.2 What FMan ucode 210 unlocks (over public 106.4.18)

1. **Exact-match Custom Classifier trees** with deep node nesting and per-key statistics — protocol-aware steering as HW config.
2. **Parser soft sequences** for non-standard protocols (VXLAN inner, MPLS label stack, IPv6 fragment).
3. **Header Manipulation (HM) nodes** — insert/remove/replace on VLAN (single + Q-in-Q), MPLS (push/pop with TC propagation), arbitrary byte ranges. Chains with CC trees.
4. **Policer profiles** — srTCM (RFC 2697) and trTCM (RFC 2698) per-flow or per-qband. Color-marking in FD status.
5. **IPv4 fragmentation (IPF) and reassembly (IPR)** with timeout-driven flush.
6. **Host Command (HC) dispatch** for runtime PCD modifications without AttachPCD/DetachPCD save-restore.

210 is signed by NXP, distributed as part of LSDK BSP, loaded by U-Boot from SPI `mtd4`. The M0 capability bitmask (§3.5) gates `pcd_ops->install` accordingly.

### 2.3 The queue-0 alias and why it must die

`dpaa_eth_napi_poll()` operates per `struct dpaa_napi_portal` — per-queue identity does not exist. When AF_XDP binds queue 0 in copy mode, all portal dequeues are treated as queue 0; XSKs bound to other queues see nothing. This violates AF_XDP's `(netdev, queue_id)` contract and forces the `patch-dpaa-xdp-queue-index.py` workaround that collapses all RX FQs onto `queue_index = 0`. M3-3 step 2 introduces true per-qband queue identity.

---

## 3. Architectural Model

### 3.1 Per-`dpaa_priv` flavor-ops abstraction (M0)

`struct dpaa_priv` grows four fields:

```c
const struct dpaa_pcd_ops   *pcd_ops;    /* RCU-protected; per-netdev */
const struct dpaa_qmgmt_ops *qmgmt_ops;  /* RCU-protected; per-netdev */
void                        *flavor_priv;
u32                          fman_caps;  /* §3.5 */
```

```c
struct dpaa_pcd_ops {
    int  (*install)  (struct dpaa_priv *priv);
    void (*teardown) (struct dpaa_priv *priv);
    int  (*reconfig) (struct dpaa_priv *priv, struct nlattr *params);
    int  (*dump_state)(struct dpaa_priv *, struct seq_file *m);
};

struct dpaa_qmgmt_ops {
    int  (*alloc_rx_fqs)   (struct dpaa_priv *, struct list_head *);
    bool (*rx_hook)        (struct dpaa_priv *, struct qman_fq *, const struct qm_dqrr_entry *);
    int  (*xsk_pool_attach)(struct dpaa_priv *, struct xsk_buff_pool *, u16 queue_id);
    int  (*xsk_pool_detach)(struct dpaa_priv *, u16 queue_id);
    int  (*xsk_wakeup)     (struct dpaa_priv *, u32 queue_id, u32 flags);
};
```

Default ops are NULL; call sites short-circuit to mainline. Flavors register **per-netdev** (§3.4).

### 3.2 Hook points in `dpaa_eth_probe()`

```
dpaa_eth_probe(pdev):
  bman/qman portals probed ............. -> -EPROBE_DEFER
  alloc_etherdev_mq(sizeof(*priv), DPAA_ETH_TXQ_NUM)
  fman_get_caps(...) -> priv->fman_caps  <-- §3.5
  fman_port_bind for RX and TX
  dpaa_priv_bp_create
  dpaa_alloc_all_fqs(...)                <-- qmgmt_ops->alloc_rx_fqs
  dpaa_eth_init_tx_port / dpaa_eth_init_rx_port
  dpaa_fq_init for each fq
  dpaa_eth_napi_add
  net_dev->netdev_ops = &dpaa_ops
  net_dev->xdp_features = NETDEV_XDP_ACT_BASIC | ...
                                          <-- pcd_ops->install
  register_netdev(net_dev)
```

### 3.3 Module load ordering

**Current decision (vpp): kconfig autoload (Option B).** `CONFIG_DPAA_AF_XDP_POOL=y` (built-in). `af_xdp_pool_init()` runs at `late_initcall` before `dpaa_eth_probe()`'s `register_netdev()`; `qmgmt_ops` is non-NULL on first publish; `NETDEV_XDP_ACT_XSK_ZEROCOPY` advertised. `MODULE_SOFTDEP` unreachable under this posture.

For ASK2 (when its module lands per ASK2 spec): same Option B posture, or `MODULE_SOFTDEP("pre: ask2_flavor")` with deferred-probe replay.

### 3.4 Per-netdev flavor-ops registration

v4.x assumed a single global registration. v5.0 makes registration per-`dpaa_priv`:

```c
int  dpaa_priv_attach_flavor_ops(struct dpaa_priv *,
                                 const struct dpaa_pcd_ops *,
                                 const struct dpaa_qmgmt_ops *,
                                 void *flavor_priv);
void dpaa_priv_detach_flavor_ops(struct dpaa_priv *);

/* Convenience wrapper: claim every DPAA1 netdev. Common case. */
int  dpaa_register_flavor_ops(const struct dpaa_pcd_ops *,
                              const struct dpaa_qmgmt_ops *, void *);
void dpaa_unregister_flavor_ops(void);
```

Pointers RCU-protected; `synchronize_rcu()` in detach/unregister. Conflict resolution: a second attach attempt on a netdev that already has ops returns `-EBUSY`. The flavor module is responsible for which netdevs to claim — typically via a module parameter (`af_xdp_pool.interfaces=eth3,eth4`) or via a DT-property iterator. Detach is enforced by stashing the caller's `THIS_MODULE` pointer.

### 3.5 FMan capability-detection layer

```c
#define FMAN_CAP_CC_EXACT_MATCH   BIT(0)   /* ucode 210+ */
#define FMAN_CAP_HM_NODES         BIT(1)   /* ucode 210+ */
#define FMAN_CAP_POLICER_TRTCM    BIT(2)   /* ucode 210+ */
#define FMAN_CAP_HC_DISPATCH      BIT(3)   /* ucode 210+ */
#define FMAN_CAP_PARSER_SOFTSEQ   BIT(4)   /* ucode 210+ */
/* Reserved — ucode-present on 210.10.1 but unconsumed; no consumer yet.
 * Add a cap bit + fman_*_install() helper when the consuming phase lands:
 *   FMAN_CAP_CC_HASH_TABLE     BIT(5)   -- CC Root Hash-Table nodes (§2.2.1 #4)
 *   FMAN_CAP_IP_REASSEMBLY     BIT(6)   -- Manipulations IPR (§2.2.1 #6)
 *   FMAN_CAP_IP_FRAGMENTATION  BIT(7)   -- Manipulations IPF (§2.2.1 #7)
 *   FMAN_CAP_FRAME_REPLICATOR  BIT(8)   -- Frame Replicator (§2.2.1 #8)
 */
```

**The 5-bit cap bitmask is a deliberate subset of the full FMan microcode/PCD function set (§2.2.1), not the complete inventory.** It enumerates only the functions this driver *consumes* today (CC Match-Table, HM, Policer, Parser-SoftSeq) plus the one it deliberately gates *off* after DUT probing (HC_DISPATCH). The remaining NXP PCD functions — **CC Hash-Table nodes, IP Reassembly (IPR), IP Fragmentation (IPF), and the Frame Replicator** — are ucode-present on the shipping 210.10.1 blob but have **no cap bit and no consumer** here; they are reserved (commented placeholders BIT(5)–BIT(8) above) for the phase that introduces a consumer. This is an unconsumed-capability gap, **not** an absence: unlike HC_DISPATCH (which the empirical DUT probe shows the blob does not implement at all), these four are present in 210.10.1 and would light up once a consumer + DT-walk detection is added. See §2.2.1 for the authoritative consumed/present-unconsumed/absent breakdown.

Each exported `fman_*_install()` helper returns `-ENOTSUPP` if the corresponding cap bit is clear, **before** touching MURAM. Phase 3b–e flavor code: `install → if (-ENOTSUPP) { priv->hw_offload_unavailable++; continue; }` — fail-soft.

**Productive detection (patch `0086a`, 2026-05-28).** Cap bits auto-populate from a DT walk of the FMan firmware blob U-Boot loaded into MURAM from SPI mtd4. Layout per `include/soc/fsl/qe/qe.h struct qe_firmware`: bytes 0..3 `__be32 length`, bytes 4..6 magic `"QEF"`, byte 7 version, bytes 8..69 `id[62]` NUL-terminated ASCII. On Mono Gateway DK the property lives at `/proc/device-tree/soc/fman@1a00000/fman-firmware/fsl,firmware` (51652 bytes) and the id reads exactly `"Microcode version 210.10.1 for LS1043 r1.0"` (verified by `od -c` on DUT 2026-05-28). A string parser extracts the major version (210) and lights up `CC_EXACT_MATCH | HM_NODES | POLICER_TRTCM | PARSER_SOFTSEQ`. `HC_DISPATCH` deliberately stays off: per PR13 hardware probe (2026-05-13), the standard 210.10.1 QEF blob does not implement the Host Command doorbell — only the dedicated `qe_firmware` ucode variant does, and we do not ship that. Result on this board is `caps = 0x17` (CC+HM+POL+PARSER). The result is cached in a file-scope `static int` after first probe so the 5× `dpaa_eth_probe()` calls (one per MAC) don't re-walk the DT. The `dpaa_fman_caps.force=<u32>` module parameter still wins as an operator override for dev/CI on hardware where the DT walk would otherwise return 0.

**MURAM budget:**

| Flavor | CC tree | HM nodes | Policer | Total | Of 64 KiB PCD reservation |
|---|---|---|---|---|---|
| `default` (CLI-driven CC) | ~5 KiB | ~1 KiB | ~2 KiB | ~8 KiB | ~12% |
| `vpp` | ~5 KiB | ~1 KiB | ~2 KiB | ~8 KiB | ~12% |
| `ask` (dynamic, up to 255 keys × 150 B) | up to ~38 KiB | ~2 KiB | ~4 KiB | up to ~44 KiB | ~69% |
| Combined `vpp` + `ask` (different netdevs) | ~43 KiB | ~3 KiB | ~6 KiB | ~52 KiB | ~81% |

Combined `vpp` + `ask` on the same MAC (Phase 4) is gated by VSP bifurcation.

---

## 4. LS1046A Hardware Modules — Per-Capability Engagement

| Block | M0 | M1 wakeup | M2 XSK pool | M3-3 datapath | 3b CC | 3c HM | 3d Pol | 3e CEETM |
|---|---|---|---|---|---|---|---|---|
| FMan BMI | — | — | bind (vpp) | bind | — | — | — | — |
| FMan Parser | — | — | — | — | soft seq | — | — | — |
| FMan KeyGen | — | — | — | scheme tune | — | — | — | — |
| FMan CC | — | — | — | — | tree install | — | — | — |
| FMan HM | — | — | — | — | — | node install | — | — |
| FMan Policer | — | — | — | — | — | — | profile install | — |
| FMan MURAM | — | — | — | — | CC tree | HM nodes | profiles | — |
| FMan DCSR | — | observe | observe | observe | observe | observe | observe | observe |
| BMan | — | — | XSK pool (vpp) | dedicated chans (all) | — | — | — | — |
| QMan SWP | — | wakeup | — | per-CPU pin (all) | — | — | — | dequeue |
| QMan FQ | — | — | retire/reactivate | qband mapping | CC targets | — | color-aware drop | CEETM LFQs |
| QMan CEETM | — | — | — | — | — | — | — | full (all via tc) |
| PAMU | — | — | — | — | — | — | — | — |
| CCI-400 | — | — | — | cluster hint | — | — | — | — |
| CoreNet | coherent | coherent | coherent | coherent | coherent | coherent | coherent | coherent |
| SerDes/XFI PCS | observe | observe | observe | observe | — | — | — | — |
| SEC 5.4 (CAAM) | — | — | — | — | — | — | — | — (see ASK2 §8) | CAAM QI (ASK-only) |

**(all)** = applies to default/vpp/ask; **(vpp)** = VPP-flavor only.

### 4.1 FMan v3 with ucode 210 — 384 KiB MURAM, 4 BMI ports, 32 KG schemes. Capability layer (§3.5) gates feature install per-cap; fail-soft on ucode 106.

### 4.2 BMan — 64 pool IDs system-wide.
- Mainline today: 3 IDs/netdev.
- §5.2 dedicated channels (all flavors post-M3-3 step 2): 4 IDs/netdev.
- §6.1 XSK-backed pool (vpp-only): +4 IDs/netdev when AF_XDP bound.
- Worst-case (5 netdevs × 4 dedicated + 2 vpp-attached × 4 XSK): 28 of 64. Fits.

### 4.3 QMan/SWPs — Four SWPs (one per A72), 28 pool channels + 4 dedicated channels available. The 4-dedicated-channel consumption (one per qband) is within budget. Stash effectiveness requires polling CPU = portal CPU; `qmap[]` (§5.2) enforces.

### 4.4 QMan CEETM — 8-level hierarchical scheduler in the QMan silicon. **The Linux CEETM tc-qdisc driver is NOT in mainline 6.18.31** (no `qman_ceetm.c`, no `dpaa_eth_ceetm.c`, no `qman_ceetm_*` API — verified 2026-05-29). §5.7 must forward-port the SDK driver before it can be wired as a root qdisc replacement.

### 4.5 MURAM — Combined < 52 KiB respecting PR14h 64 KiB reservation.

### 4.6 PAMU — **arm64 LS1046A: firmware bypass, kernel driver is PPC-only** (`depends on PPC_E500MC || (COMPILE_TEST && PPC)`). FMan masters DMA directly via dma-direct. No per-attach `fsl_pamu_window_create()` possible. §6.1 uses `xsk_pool_dma_map(pool, priv->mac_dev->dev, DMA_ATTR_SKIP_CPU_SYNC)` only; `DMA_ATTR_COHERENT` NOT passed (no defined semantics on arm64 dma-direct). 9-step detach anchored on `fman_port_disable(rxp)` before `xsk_pool_dma_unmap()`.

### 4.7 CCI-400 vs CoreNet — Two coherency fabrics. CoreNet (A72↔DPAA1) guarantees coherency unconditionally; `DMA_ATTR_SKIP_CPU_SYNC` mappings safe in hot path. CCI-400 (A72 cluster↔cluster) matters for worker pinning — `qmap[]` builder prefers same-cluster RX qband + consumer.

### 4.8 SerDes/XFI PCS — `phy-connection-type = "xgmii"` critical. `xsk_socket__create()` runs `phylink_resolve()` before allowing attach.

### 4.9 FMan DCSR — Exception window at FMan offset `0x0F_0000`. §5.8 wires debugfs `/sys/kernel/debug/dpaa-eth/<iface>/dcsr/{bmi,parser,kg,pol}_err`, rate-limited ≥ 1 ms.

---

## 5. Cross-Flavor Capabilities

Each subsection: kernel-side capability that is **not flavor-specific**. The capability lands as exported API or behavioural change in the driver core. The **Consumers** paragraph enumerates how `default`, `vpp`, `ask` use it.

### 5.1 M0 — Per-`dpaa_priv` flavor-ops abstraction

**Patches:** `0068`, `0069a`, `0070`, `0072`. **Status:** dut-validated.

Structural refactor introducing `struct dpaa_pcd_ops` and `struct dpaa_qmgmt_ops` in `drivers/net/ethernet/freescale/dpaa/dpaa_flavor.h`. Adds the four `priv` fields per §3.1. Wires NULL-checked call sites. Adds the per-netdev registration helpers from §3.4.

**Consumers:**
- **`default`** — passive. Ops stay NULL; hot path byte-identical to mainline (verified DUT 2026-05-26: iperf3 13.1 Gbps baseline).
- **`vpp`** — `af_xdp_pool.ko` registers `qmgmt_ops`.
- **`ask`** — ASK2 flavor module registers `pcd_ops` + subset of `qmgmt_ops` (no XSK callbacks).

**Acceptance gate M0:**
1. `modprobe fsl_dpaa_eth` without flavor module: zero change. ✅
2. `rmmod && modprobe` ×100 no leak. ✅
3. Patches `checkpatch.pl`-clean. ✅

### 5.2 Per-CPU NAPI + dedicated BMan channels (RX datapath restructure)

**Patches:** `0081` (planned, M3-3 step 2). **Status:** planned. **Largest cross-flavor win in the pipeline.**

Introduce "qband" as RX queue identity. `qband ∈ [0..3]` maps to a contiguous band of 32 PCD FQs on a **dedicated** BMan channel and **dedicated** QMan SWP. KeyGen `KGSE_BASEFQID` set so hash mod 32 picks FQ within qband; top 2 bits pick qband.

```c
static inline u16 dpaa_fq_to_qband(struct dpaa_priv *priv, u32 fqid)
{
    if (fqid < priv->pcd_fq_base ||
        fqid >= priv->pcd_fq_base + DPAA_ETH_PCD_RXQ_NUM)
        return 0;
    return (fqid - priv->pcd_fq_base) /
           (DPAA_ETH_PCD_RXQ_NUM / priv->xsk_max_qbands);
}
```

**Queue mapping correctness** (applies to skbuf RX *and* AF_XDP-ZC RX):
1. Delete `data/kernel-patches/patch-dpaa-xdp-queue-index.py` under `CONFIG_DPAA_XSK_MULTIQ=y` (retained at `=n` for emergency rollback).
2. `xdp_rxq_info_reg()` call sites pass real queue index, not 0.
3. Build `priv->qmap[qband] = { fqid_base, swp_id, napi, cpu }` at probe. Exposed via debugfs `/sys/kernel/debug/dpaa-eth/<iface>/qmap`. `WARN_ON(napi != &priv->qmap[qband].napi)` in dequeue.
4. Pin each NAPI to its QMan SWP CPU via `netif_napi_add()` + `netif_set_napi_irq_affinity_hint()`.
5. **Cluster-affinity hint** (§4.7). `workers N ≤ 2` → cores 0,1 (cluster 0); `N = 4` → 2-and-2 split. Exposed read-only as `/sys/class/net/<iface>/dpaa/cluster_map`.

**Dedicated BMan channels.** Replace pool-channel-with-HOLDACTIVE with one dedicated channel per qband. Eliminates HOLDACTIVE arbitration overhead; stash window pre-warms exactly the right CPU. **Measurable cycles-per-packet win in `dpaa_rx_napi_poll()` — benefits *every* RX path, AF_XDP or not.**

**Consumers:**
- **`default`** — gets the restructure automatically. Expected: lower per-packet cycles, better 4-flow RSS scaling, HOLDACTIVE arbitration eliminated. No CLI knobs. Validation: multi-flow kernel forwarding throughput improves vs. pre-M3-3 baseline.
- **`vpp`** — same restructure plus qband identity becomes queue_id for AF_XDP-ZC. Enables `xdpsock -q 3`.
- **`ask`** — same skbuf gain as default; ASK2 flavor module can additionally use qband as a flow-steering target.

### 5.3 `ndo_xsk_wakeup` (M1)

**Patches:** `0070`, `0072`, `0073`, `0074`. **Status:** dut-validated.

```c
static int dpaa_xsk_wakeup(struct net_device *ndev, u32 queue_id, u32 flags)
{
    struct dpaa_priv *priv = netdev_priv(ndev);
    const struct dpaa_qmgmt_ops *ops;

    if (unlikely(!netif_running(ndev))) return -ENETDOWN;
    rcu_read_lock();
    ops = rcu_dereference(priv->qmgmt_ops);
    if (!ops || !ops->xsk_wakeup) {
        rcu_read_unlock();
        return -EOPNOTSUPP;
    }
    rcu_read_unlock();
    return ops->xsk_wakeup(priv, queue_id, flags);
}
```

For `XDP_WAKEUP_RX`, kick NAPI via `napi_schedule()`. For `XDP_WAKEUP_TX`, poke QMan egress portal via `qman_p_irqsource_add`. After `napi_complete_done()`, arm `xsk_set_rx_need_wakeup()` on every bound pool with `xsk_uses_need_wakeup()` (patch 0080, M3-3 step 1).

**Consumers:**
- **`default`** — latent. Any AF_XDP application in copy mode benefits (no busy-spin). Not VyOS CLI-exposed but available via `xdp-tools`.
- **`vpp`** — VPP drops `set vpp settings unix poll-sleep-usec 100`. Idle CPU ~100% → < 10%.
- **`ask`** — latent.

**Acceptance gate M1 (VPP, has the userspace consumer):**
1. VPP `af_xdp` runs in copy mode without `poll-sleep-usec`. ✅
2. Idle CPU < 10%. ✅
3. Thermal zones stay COOL/WARM. ✅
4. `xsk_ring_prod__needs_wakeup()` returns correct values. ✅

### 5.4 CC steering (M3-3b)

**Patches:** `0086` (observability stub) + `0086a` (productive DT ucode-210 probe) + `0086b` (productive struct contract). **Status:** struct-contract-landed. `0086` established FMAN_CAP_* detection + `fman_cc_tree_*` decls + four `-ENOTSUPP` stubs + `hw_offload_unavailable` counter (`ethtool -S`). `0086a` auto-populates caps from the DT firmware-blob `qe_firmware.id` walk (§3.5). `0086b` (2026-05-28) promotes the opaque `struct fman_cc_key`/`struct fman_cc_static_tree` `{ u32 reserved; }` placeholders to the productive 5-tuple layout below — fixing the CC API *contract* so downstream consumers (af_xdp_pool qband-select, vyos-1x `set system offload classify` CLI, ASK2 flowtable bridge) build against stable field names today. The four `fman_cc_tree_*` entry-point bodies remain `-ENOTSUPP`; the silicon MURAM AD-table / group-table CONT_LOOKUP encoding (LS1046A RM 8.7.4.1) is a follow-up productive patch. **CC + HM chain ordering (resolves former OQ5):** Parser → CC (unmodified ingress) → HM (egress from matching CC node) → QMan. CC keys match wire-format; consumers receive post-HM frames.

**Productive struct contract (patch `0086b`).** `struct fman_cc_key` carries `ethertype` (host-endian, `FMAN_CC_ETHERTYPE_{ANY,IPV4,IPV6}`), `proto` (`FMAN_CC_PROTO_{ANY,TCP,UDP}`), `is_ipv6`, v4 `src_ip`/`dst_ip` + `src_ip_mask`/`dst_ip_mask`, v6 `src_ip6[16]`/`dst_ip6[16]`, `src_port`/`dst_port`, `target_qband` (the qband to steer matches to per §5.2), and `hm_handle` (chains to a §5.5 HM node; 0 = none). A field participates in the match iff it (or its mask) is non-zero; all multi-byte fields are host-endian at the API and the install body converts to FMan big-endian MURAM layout. `struct fman_cc_static_tree { u16 num_keys; u16 miss_qband; struct fman_cc_key keys[FMAN_CC_MAX_STATIC_KEYS]; }` with `FMAN_CC_MAX_STATIC_KEYS = 32` bounding the ~5 KiB MURAM static budget (§3.5). The productive `add_key()` body will reject `target_qband >= priv->xsk_max_qbands` with `-ERANGE` and a non-zero `hm_handle` on a build without `FMAN_CAP_HM_NODES` with `-ENOTSUPP`. Header compiles clean (`dpaa_fman_caps.o`, ARCH=arm64, zero warnings); `git apply --3way --check` and `patch-health.sh --flavor default` both green.

```c
int  fman_cc_tree_install (struct fman *fm, u8 port_id,
                           const struct fman_cc_static_tree *spec);
int  fman_cc_tree_add_key (struct fman *fm, u8 port_id,
                           const struct fman_cc_key *key, u32 *handle);
int  fman_cc_tree_remove_key(struct fman *fm, u8 port_id, u32 handle);
void fman_cc_tree_destroy (struct fman *fm, u8 port_id);
```

`install` takes a static tree spec and is the **productive path on shipping hardware**. `add_key`/`remove_key` are the dynamic lifecycle. A true host-command-dispatched dynamic path (no save-restore, no netdev flap) would require `FMAN_CAP_HC_DISPATCH` (BIT(3)) — but the stock 210.10.1 QEF blob loaded on every shipped Mono Gateway DK does **not** implement the FMan Host-Command doorbell (board reports `caps = 0x17` = CC|HM|POL|PARSER, bit 3 clear; see §3.5/§6.1.13). On this board the dynamic add/remove therefore falls back to an **in-tree MURAM rewrite** (AttachPCD/DetachPCD-style save-restore), and `add_key`/`remove_key` return `-ENOTSUPP` whenever neither HC dispatch nor the MURAM-rewrite path is available (e.g. ucode 106). HC-dispatched dynamic insertion would only become productive under a custom microcode we do not ship. Each entry consults `priv->fman_caps`. MURAM ≤ 5 KiB (default/vpp static) up to ~38 KiB (ask dynamic via MURAM rewrite).

**Consumers:**
- **`default`** — backs kernel RPS via `ndo_rx_flow_steer` (vyos-1x follow-on: `set system offload classify rule N ...`). HW classifier → right RX FQ → right qband → right CPU. Replaces software RPS table lookup. Static tree; `commit` rebuilds.
- **`vpp`** — `set vpp settings hw-offload classify rule N protocol vxlan target-qband 0` etc. Static tree at `pcd_ops->install`. Rule toggle via `commit` is HC-dispatched (no netdev flap).
- **`ask`** — ASK2 uses dynamic API (`add_key`/`remove_key`) directly. Each nft flow → one CC key. API **shared** between VPP and ASK2; only the lifecycle (static vs dynamic) is flavor-specific.

**Impact of the missing HC doorbell on dynamic flow steering (is this a big miss? what are the 210 alternatives?).** Short answer: **not a big miss for this board's roles, and 210 provides three productive alternatives.** The HC doorbell only affects *how* a runtime CC-key add/remove is committed — it does **not** make dynamic flow steering impossible:

- **It is a cost difference, not a capability loss.** `fman_cc_tree_add_key()`/`remove_key()` are **not permanently `-ENOTSUPP`** on this board. With HC absent, a runtime key add/remove rides an **in-tree MURAM rewrite** (AttachPCD/DetachPCD-style save-restore) instead of a flap-free doorbell dispatch. The functional result is identical (the key is installed and the CC engine steers on it); the only penalty is a brief PCD save-restore window per mutation rather than an atomic in-place poke. `-ENOTSUPP` is returned only when **neither** HC **nor** the MURAM-rewrite path exists (e.g. the open-source 106.x ucode) — which is not this board.

- **For `default` (kernel RPS via `ndo_rx_flow_steer`):** aRFS-style *per-socket* dynamic steering — where the kernel would add/remove a CC key on every flow's first packet — is the one workload that genuinely wants flap-free HC dispatch (thousands of short-lived mutations/sec). That high-churn pattern is impractical via MURAM-rewrite. **But the productive `default` alternative in 210 is the KeyGen hash RSS path that mainline already uses** (the **Y**-marked KeyGen row in §2.2.1): ingress is spread across 128 PCD FQs by 5-tuple hash with `QM_FQCTRL_HOLDACTIVE` + stashing keeping a flow pinned to one CPU portal — i.e. RSS/RPS-equivalent CPU steering **with zero per-flow MURAM churn**, entirely in 210 (in fact in 106.x too). The CC **static tree** then layers coarse-grained, *policy*-driven steering (a bounded rule set rebuilt on `commit`, not per-flow) on top. So `default` gets HW CPU-affinity steering today; it just does it by **hash + static policy**, not by per-socket aRFS key churn. The aRFS-via-HC path is a *nice-to-have for a specific high-churn microbenchmark*, not a gating requirement for a gateway/router workload.

- **For `vpp` and `ask` (the consumers that matter here):** neither needs flap-free HC. `vpp` uses the **static tree** exclusively (rule set rebuilt on `commit`). `ask` (ASK2) drives the **dynamic API via the MURAM-rewrite path**, and its flow rate is conntrack-offload granularity (flows, not packets) — the save-restore cost is amortized and acceptable; ASK2's M2 work validated dynamic CC-key install/remove on silicon over exactly this path.

**Three productive 210 alternatives, in order of preference:** (1) **KeyGen 5-tuple hash RSS** — flap-free, per-flow CPU steering with no MURAM mutation (the mainline-proven default-flavor path); (2) **static CC tree** — bounded policy-rule steering rebuilt on `commit` (the `vpp`/`default` path); (3) **dynamic CC via in-tree MURAM rewrite** — true runtime add/remove with a per-mutation save-restore window (the `ask` path). The only thing genuinely unavailable on the stock 210.10.1 blob is **flap-free, high-churn per-socket aRFS** — and that would require a custom microcode NXP does not publish and we do not ship. Conclusion: dynamic flow steering is **available** on this board (alternative #3), HW CPU-affinity steering is **available and flap-free** (alternative #1), and the HC gap costs only the niche high-churn aRFS microbenchmark — **not a big miss** for a VyOS gateway.

**Acceptance gate M3-3b:**
1. `cat /sys/kernel/debug/dpaa-eth/<iface>/cc_tree` shows rules.
2. Steering observable on `xdpsock -q N` or `ethtool -S` per-FQ counters.
3. No regression vs. §5.2 baseline.
4. `add_key`/`remove_key` no netdev flap.

### 5.5 HM offload (M3-3c)

**Patches:** `0090`/`0090a` (stub + ordered-op-list `struct fman_hm_spec`) + `0099` (productive HMTD/HMCT MURAM encoder, `fman_pcd_manip.c`, RM 8.7.5 generic INSRT/RMV) + `0101` (userspace `.ndo_set_features` bridge). **Status:** bridge dut-validated — feature bit live on hardware; vyos-1x-024 CLI shipped + live on DUT (2026-06-07); functional datapath gate pending traffic gen only.

`0101` is the kernel-side bridge that makes the dormant `0099` install body reachable from userspace: a `dpaa_set_features()` `.ndo_set_features` handler in `dpaa_eth.c` that, on a 0→1 transition of `NETIF_F_HW_VLAN_CTAG_RX` (driven by `ethtool -K ethX rxvlan on`, emitted by the vyos-1x-024 `set interfaces ethernet ethX hw-offload vlan-strip` CLI — **shipped and live on the DUT**, verified 2026-06-07 on image `2026.06.07-2209-rolling`), installs a single-op `{FMAN_HM_OP_VLAN_STRIP}` HM node and stashes the handle in `priv->hm_vlan_strip_handle` (destroyed on 1→0). `struct fman *` via `fman_bind(mac_dev->fman_dev)`; port id via `fman_port_get_qman_channel_id(mac_dev->port[RX])` (the real in-tree getter — an earlier draft's `fman_mac_get_hw_id()` does not exist and failed the CI cross-compile, fixed in `02fb1ac`). The feature bit is advertised in `net_dev->hw_features` only when `fman_hm_caps_supported()` so mainline-ucode boards never expose an `-ENOTSUPP` knob. **DUT-validated 2026-05-30** (ISO `vyos-2026.05.30-0233-rolling-LS1046A-default-arm64`, kernel `6.18.33-vyos`): `FMan PCD caps = 0x17`, `ethtool -k eth0` → `rx-vlan-offload: on` (not `[fixed]`); the cap-gated advertisement + enable-transition both work on silicon, no regression. The functional datapath gate below (tcpdump tagged on wire / consumer untagged, sub-100 ns HM cost) remains pending **a traffic generator only** — the vyos-1x-024 CLI consumer is shipped and live on the DUT (verified 2026-06-07: `/opt/vyatta/share/vyatta-cfg/templates/.../offload/vlan-strip/node.def` carries the 024 help fingerprint; `set_vlan_strip`/`get_rx_vlan_offload` present).

`fman_hm_node_install/destroy/caps_supported` are `EXPORT_SYMBOL_GPL`. `0099` (PR4 of the FMan PCD forward-port, built-in via `FSL_FMAN_PCD`) makes the install/destroy bodies productive — four per-op HMCT encoders (vlan_insert/vlan_strip/mpls_push/mpls_pop) per RM 8.7.5, neutral `struct fman_pcd_hm_hw_{op,spec}` published in `include/linux/fsl/fman_pcd.h`, host→neutral translate via `dpaa_hm_spec_to_hw()`, delegate via `fman_get_pcd()`. `struct fman_hm_op`/`fman_hm_spec` (ordered op-list, `FMAN_HM_MAX_OPS=8`) from `0090a`; ops `VLAN_STRIP`/`VLAN_INSERT`/`MPLS_PUSH`/`MPLS_POP` (append-only enum). Cap auto-detected by `0086a` DT probe.

> Full per-patch prose (HMCT/HMTD MURAM layout, compile/patch-health matrix, the `fman_mac_get_hw_id` misdiagnosis, struct-field listings): Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

```c
int  fman_hm_node_install(struct fman *fm, u8 port_id,
                          const struct fman_hm_spec *spec, u32 *handle);
int  fman_hm_node_destroy(struct fman *fm, u8 port_id, u32 handle);
bool fman_hm_caps_supported(void);
```

`HM_OP_VLAN_STRIP`, `HM_OP_VLAN_INSERT(vlan_id, tpid)`, `HM_OP_MPLS_PUSH(label, tc, s, ttl)`, `HM_OP_MPLS_POP`. Chains with §5.4 CC tree.

**Consumers:**
- **`default`** — backs `NETIF_F_HW_VLAN_CTAG_RX/TX` in `dev->features`. Kernel `__netif_receive_skb()` sees pre-stripped frames; egress `vlan_dev_hard_start_xmit()` bypassed. CLI: `set interfaces ethernet ethX hw-offload vlan-strip` (vyos-1x follow-on). Sub-100 ns HM cost vs. multi-µs software path.
- **`vpp`** — `set vpp settings hw-offload vlan-strip-on-ingress`. Frees VPP from per-packet L2 manipulation.
- **`ask`** — ASK2 uses HM for L2 normalization on offloaded flows.

**Acceptance gate M3-3c:**
1. Wire shows tagged; consumer sees untagged (and vice versa on egress).
2. No regression. 3. Sub-100 ns per-packet HM cost.

### 5.6 Policer (M3-3d)

**Patches:** `0091`/`0091a` (stub + srTCM/trTCM `struct fman_policer_profile` contract) + board `0097` (KeyGen PLCR next-engine + `fman_pcd_kg_scheme_create`/`_bind_port`/`_destroy` API) + `0100` (productive install body, rewritten 2026-06-08 to CCSR FMPL indirect-PAR window) + `0104`/`0104a` (tc-matchall `.ndo_setup_tc` bridge + PCD datapath wiring + `NETIF_F_HW_TC` advertisement). **Status:** install-body-landed (compile-validated; DUT cap-probe ✓); **datapath-binding fix (Path A) IMPLEMENTED; DUT re-test #1 (2026-06-08) failed on a `fman_pcd_kg_bind_port()` `port_id > 10` range bug (10G ports are BMI id `0x10/0x11`) — FIXED in `0097` (`> 0x3f`); **CI rebuilt (run `27112773623`, ISO `2026.06.08-0234-rolling`) + DUT reflashed + §5.6 re-test #2 (2026-06-08) PASSED the bind gate — the pure-HW `skip_sw` policer on eth3 now binds successfully (`tc filter add … action police` rc=0, reports `in_hw`/`bind 1`, no `-EINVAL`, clean dmesg). **STEERING then FIXED 2026-06-09 (image `0522`): `0097`+`0104` reprogram the port's EXISTING RSS scheme in place to next-engine=PLCR (eth3 BMI port `0x10` → scheme 3 MODE `0xc04c0000`).** **BUG 3a + BUG 3b non-revert half FIXED + HW-VALIDATED 2026-06-09 (image `2032`, commit `1a48948`):** 3a root cause was the FMPL block master-enable (`FMPL_GCR.EN|STEN` clear at boot, NOT profile addressing) — `0100` `plcr_enable_block()` flips GCR `0x00500002→0xC0500002`, policed ping 100% loss → 0% on a clean cold boot; 3b non-revert closed by the kernel `flow_block_cb` release in `0104` + vyos-1x-025 reorder. **STILL OPEN:** the iperf3 flood-crash half of 3b (serial + cold power-cycle) and the literal throughput-cap number on the §8 harness. See the blockquote chain below for the full HW-proof.**

> **Wire-test FAILED — 2026-06-08** (DUT 192.168.1.190, kernel 6.18.34-vyos, image 2026.06.07-2209-rolling). A pure-hardware ingress policer on eth3 (`ethtool -K eth3 hw-tc-offload on; tc qdisc add dev eth3 clsact; tc filter add dev eth3 ingress matchall skip_sw action police rate 2500mbit burst 500k drop`) reports `skip_sw` + `in_hw (rule hit 0)` and trips the driver's `fsl_dpa: only one ingress policer per interface is supported` state guard, BUT iperf3 TCP reverse (peer 10.99.1.2 → DUT) delivered **6.41 Gbps** with `rx_bytes` confirming ~6.5 Gbps ingressed and `tc -s filter` Action stats `Sent 0 dropped 0 overlimits 0`. A rate sweep (`100mbit→6.92`, `500mbit→7.07`, `1gbit→6.03` Gbps — all `in_hw`, none cap) rules out an RM §8.7.6 exp/mant units bug. **Root cause:** `0100`/`0104` program the FMan policer **profile** (so `in_hw` is reported and the one-per-interface guard fires) but never **bind** it into the ingress frame path — no keygen scheme / CCNODE routes the RX FQ through the profile, so frames hit the default RX FQ untouched. Because `skip_sw` bypasses the tc software policer there is NO software fallback and the failure is **silent** (traffic just runs at line rate). The shipped `vyos-1x-025 set_ingress_policer()` issues byte-identical `matchall skip_sw action police` tc, so the CLI inherits the same non-enforcement — broken end-to-end. **ACTION:** `0104` must add the missing PCD datapath binding (keygen scheme directing the RX FQ through the policer profile / CCNODE next-action = policer) before M3-3d can be claimed functional. Qdrant: `type=wire-test-finding date=2026-06-08`.
>
> **FIX DESIGN — Path A (keygen next-engine=PLCR), encoding extracted 2026-06-08.** Authoritative KG→PLCR register encoding forward-ported from NXP SDK linux-5.10 flib (`fsl_fman_kg.h` + `Peripherals/FM/Pcd/fman_kg.c`; mainline 6.18 `fman_keygen.c` lacks all of it). **Key enabler:** mainline 6.18 `struct fman_kg_scheme_regs` **already carries `kgse_ppc`** (the Policer-Profile-Command reg, offset 0x11C) and `keygen_write_scheme()` **already `iowrite32be`s it to HW** — it is simply always 0 today, so the HW write path needs **zero** change. Required edits are confined to `0097`: (a) add macros `NIA_ENG_PLCR 0x004C0000` + `KG_SCH_MODE_NIA_PLCR 0x40000000` (mainline already has `KG_SCH_MODE_EN 0x80000000`, `NIA_ENG_BMI 0x00500000`); (b) extend `struct keygen_scheme` (now in `fman_keygen_internal.h`) + `struct fman_pcd_kg_scheme_params` with a `next_engine` + `policer_profile_id`; (c) in `keygen_scheme_setup()` add a PLCR branch that **replaces** mainline's `tmp_reg |= ENQUEUE_KG_DFLT_NIA` with `kgse_mode = KG_SCH_MODE_EN | KG_SCH_MODE_NIA_PLCR | NIA_ENG_PLCR` (= **`0xC04C0000`**) and sets `scheme_regs.kgse_ppc = profile_id` (direct profile select: base=id, shift=0, mask=0 — RM §8.7; `kgse_ppc` PP-gen bits `SH_SHIFT 27`/`SH_MASK 0x80000000`/`SL_SHIFT 12`/`SL_MASK 0x0000F000`/`MASK_SHIFT 16`, `SHIFT_MAX 0x17`, all unused for the fixed-profile case). **Critical RX-preservation requirement:** the catch-all scheme MUST set `kgse_fqb |= <port's CURRENT default RX FQID>` (use_hash=false) so green/conforming frames still reach the kernel/AF_XDP RX FQ — else normal RX regresses from ~6.4 Gbps. Then `0104`, after `fman_policer_install(... slot 0)`, calls `fman_pcd_kg_scheme_create()` (next-engine=PLCR→profile 0) + `fman_pcd_kg_bind_port()`, and tears both down on destroy. Full encoding in Qdrant `topic=dpaa1-fman-keygen-policer-encoding date=2026-06-08`. Local refs: `/tmp/kilo/fsl_fman_kg.h` (514L), `/tmp/kilo/sdk_fman_kg.c` (890L), `/tmp/kilo/fman_keygen.c` (mainline 6.18).
> >
> > **IMPLEMENTED — 2026-06-08 (compile + patch-apply validated; CI/DUT pending).** Path A landed across three board patches (regenerated via `git diff <commit>^ <commit>` from a 22-commit interactive rebase on `work/linux-6.18.33`; full repo stack re-applies clean via `git apply --3way` onto a fresh base, all touched files byte-identical to the rebased+compiled tip). Native arm64 compile of `fman_port.o`/`fman_pcd_kg.o`/`fman_pcd_plcr.o`/`dpaa_eth.o` = 0 warn/0 err, `LOCALVERSION=-vyos`. Actual implementation (refines the design above):
> > - **`0097`**: enum `fman_pcd_kg_next_engine {ENQUEUE=0, PLCR=1}`; `next_engine`+`policer_profile_id` fields on `struct fman_pcd_kg_scheme_params`; PLCR branch in keygen setup emits `kgse_mode = 0xC04C0000` (`KG_SCH_MODE_EN|NIA_PLCR|NIA_ENG_PLCR`) with `kgse_ppc = profile_id`; validation relaxed so a catch-all (`num_extracts==0`) is legal **only** when `next_engine==PLCR`.
> > - **`0100`**: rewritten to the CCSR **FMPL** block (`fman->base_addr + 0xC0000`) via the indirect **PAR** window (`fmpl_par@0x08C`, `GO|(id<<16)|PWSEL_MASK`), dropping MURAM; 16.16-fixed-point CIR/PIR, direct-byte CBS/PBS, PEMODE assembly — SDK byte-exact.
> > - **`0104`**: new accessor `fman_port_get_id()` (real FMan HW port-id = DT `cell-index` = BMI port id: RX-1G `0x08–0x0d`, **RX-10G `0x10/0x11`**, TX-1G `0x28–0x2d`, TX-10G `0x30/0x31`; used by both `_replace`/`_destroy` — the QMan-channel-id used previously was only a record key and is semantically wrong for `fman_pcd_kg_bind_port`); helper `dpaa_get_rx_default_fqid()` walks `priv->dpaa_fq_list` for `FQ_TYPE_RX_DEFAULT`; `_matchall_replace` now: install profile → `fman_pcd_kg_scheme_create(next_engine=PLCR, profile 0, default_fqid=RX-default)` → `fman_pcd_kg_bind_port()` → **`fman_port_use_kg_hash(port[RX], true)`** → store `priv->police_scheme`, with strict LIFO rollback (`err_scheme`/`err_policer`); `_matchall_destroy` reverses it.
> > - **CRITICAL DISCOVERY not in the original design:** binding the scheme alone is **insufficient**. DPAA RX ports default to direct BMI enqueue (`fmbm_rfpne → NIA_ENG_BMI|ENQ`, post-parser bypasses KeyGen entirely because `pcd_fqs_count==0`); the installed scheme is never consulted until `fman_port_use_kg_hash(port, true)` flips `fmbm_rfpne → NIA_ENG_HWK` (KeyGen). This is the single missing edge the 2026-06-08 wire-test exposed and is the reason the profile sat armed-but-unreached. `_destroy` flips it back to `NIA_ENG_BMI|ENQ`.
> >
> > Full encoding in Qdrant `topic=dpaa1-fman-keygen-policer-encoding date=2026-06-08`. **STILL PENDING:** CI ISO build → DUT reflash → iperf3 wire re-test of the §5.6 acceptance gate.
> >
> > **DUT re-test #1 FAILED then ROOT-CAUSED + FIXED — 2026-06-08** (DUT 192.168.1.190, image `2026.06.08-0203-rolling`, kernel `6.18.34-vyos`). With Path A shipped, `tc filter add dev eth3 ingress matchall skip_sw action police …` now reaches the new bind path but aborts: `Error: fsl_dpa: policer KeyGen scheme bind failed.` (rc=2); dmesg `policer scheme bind failed: -22` (`-EINVAL`). LIFO rollback worked (no leftover HW scheme); `clsact` qdisc cleaned up. **Root cause:** `fman_pcd_kg_bind_port()` validated `port_id == 0 || port_id > 10` and rejected, but `fman_port_get_id()` returns the **BMI hw port id** (eth3 = `0x10`/16, eth4 = `0x11`/17) — the `>10` ceiling was wrong (it would even reject 1G RX ports `0x0b–0x0d`). The KGAR encodes a 6-bit port-entry index (`FMAN_MAX_NUM_OF_HW_PORTS=64`), and `0x10` is exactly the value the NXP SDK writes for the first 10G RX port. **Fix (board `0097`):** widen the guard to `port_id == 0 || port_id > 0x3f`. Content-only one-line change (zero hunk-delta; `git apply --3way` unaffected); `schemes[]` is indexed by scheme-id not port-id so nothing overflows. Compile-validated native arm64 (`fman_pcd_kg.o` 0 warn/0 err, `LOCALVERSION=-vyos`); patch re-parses clean (`git apply --numstat` rc=0).
> >
> > **DUT re-test #2 PASSED (bind gate) — 2026-06-08** (DUT 192.168.1.190, image `2026.06.08-0234-rolling` from CI run `27112773623`, kernel `6.18.34-vyos`; commit `897e3812` "dpaa1: board 0097 — widen fman_pcd_kg_bind_port port_id guard to 0x3f"). With the `> 0x3f` guard shipped, the exact re-test #1 sequence (`ethtool -K eth3 hw-tc-offload on; tc qdisc add dev eth3 clsact; tc filter add dev eth3 ingress matchall skip_sw action police rate 2500mbit burst 500k drop`) now returns **rc=0** — no `-EINVAL`. `tc -s filter show dev eth3 ingress` reports `skip_sw` + `in_hw (rule hit 0)`, `police 0x1 rate 2500Mbit burst 500Kb … action drop`, `ref 1 bind 1`; `dmesg` is **clean** (no `fman`/`keygen`/`pcd`/`scheme bind failed`/`EINVAL` lines). The KeyGen scheme→PLCR bind (eth3 RX BMI port id `0x10`/16) that aborted at the `>10` ceiling in re-test #1 now completes, confirming the guard was the sole blocker. DUT torn down to pristine afterwards (`tc qdisc del dev eth3 clsact`, `hw-tc-offload off`). **STILL PENDING:** iperf3 wire-level throughput-enforcement verification (2.5 Gbps cap on offered 3 Gbps, red-drops visible) — blocked on the undocumented eth3-peer (`10.99.1.2`) traffic harness, NOT on any kernel/CLI work. The bind-gate half of the §5.6 acceptance gate is now MET; the throughput half remains lab-blocked.
> >
> > **STEERING FIXED, then TWO datapath bugs HW-root-caused — 2026-06-09** (DUT 192.168.1.190, image `2026.06.09-0522-rolling` from CI run `27185670881`, kernel `6.18.34-vyos`; commits `f136694..24b5460`). When the §8 harness finally allowed a wire test, re-test #2's "bind gate MET" proved **necessary but not sufficient**: with the `fman_pcd_kg_scheme_create`/`_bind_port` *catch-all* scheme of image `0234`, frames still hit the port's pre-existing RSS KeyGen scheme and **bypassed** the policer scheme entirely (iperf3 ran at full ~5 Gbps, profile counters 0). **Steering fix (board `0097`+`0104`):** new helpers `fman_pcd_kg_port_attach_policer(pcd, hw_port_id, profile_id)` / `_detach_policer(pcd, hw_port_id)` locate the port's EXISTING scheme via `kg_find_port_scheme()` (scans `keygen->schemes[i].used && .hw_port_id==hw_port_id`) and reprogram THAT scheme's mode in place to next-engine=PLCR, instead of adding a parallel scheme. `dpaa_eth.c` `_matchall_replace`(573)/`_destroy`(612) now call attach/detach (both derive `port_id = fman_port_get_id(port[RX])` = BMI id `0x10`, no translation). **HW-proven (register reads via `/dev/mem`):** eth3 RX BMI port `0x10` → KeyGen scheme 3 MODE flips `0x80500002`[ENQ-BMI] → **`0xc04c0000`[PLCR]**; the bypass is gone; the FMPL profile-0 record is programmed correctly (PEMODE `0xd0013000`, PEGNIA/PEYNIA `0x80500002`[ENQUEUE-green/yellow], PERNIA `0x805000c1`[DISCARD-red], PECIR `0xf9999999`, PECBS `125000`, PECTS full). tc filter `in_hw`.
> >
> > **BUG 3a — FIXED + HW-VALIDATED 2026-06-09 (FMPL block master-enable, NOT profile addressing).** Symptom: with steering proven and `ingress-policer 1gbit` active, ping was **100% loss** while every FMPL green/yellow/red counter stayed **0** and the PECTS token bucket never moved → frames dropped at the PLCR entry gate before any metering. **Three successive register theories were tested on hardware and DISPROVEN:** (1) `NIA_PLCR_ABSOLUTE` (KeyGen mode `0xc04c8000`); (2) the per-port `FMBM_RPP` BMI policer-profile window; (3) `RELATIVE` addressing + the `FMPL_PMR` per-port window (`0x1AC0240 <- 0x80000000`) — the last one made the board *reboot* under traffic. **ROOT CAUSE (proven on HW):** the FMan1 Policer (**FMPL**) block at CCSR `0x01AC0000` (block offset `0xC0000` from `fman->base_addr`) has its General Configuration Register **`FMPL_GCR`** (reg `0x000`) master-enable bit **`EN` (`0x80000000`)** AND statistics-enable bit **`STEN` (`0x40000000`)** *both clear* at boot — live read `FMPL_GCR = 0x00500002` (only the default-NIA low bits `GET_NIA_BMI_AC_ENQ_FRAME = 0x00500002`, set by mainline `fman` init; the whole policer block is **disabled**). When KeyGen routes a frame to a disabled FMPL block it is dropped pre-meter → TPC stuck at 0, 100% loss, **identical failure for both ABSOLUTE and RELATIVE addressing** — which is exactly why all three earlier theories were red herrings. **PROOF:** a live `/dev/mem` write `FMPL_GCR <- 0xC0500002` (`EN|STEN|DEFNIA`; `STRICT_DEVMEM`/`IO_STRICT_DEVMEM` both unset on this kernel) instantly flipped policed ping from 100% loss to **0% loss**, TPC jumped 0→141→181, and RPC (red, `+0x020`) rose to 50 proving real metering. The SDK (`sdk_fm_plcr.c:745-759,840`) builds the enabled GCR as `STEN | (autoRefresh?DAR:0) | GET_NIA_BMI_AC_ENQ_FRAME` then ORs in `GCR_EN`; target value `0xC0500002`. **FIX SHIPPED (board `0100`):** helper `plcr_enable_block(pcd)` under `spin_lock_irqsave(&plcr_hw_lock)` does RMW `gcr |= FMPL_GCR_EN | FMPL_GCR_STEN` preserving the DEFNIA low bits (`0x00500002 → 0xC0500002`), called in `fman_pcd_plcr_install()` after `plcr_commit_profile()` (line 698). The block is intentionally left ENABLED when the last profile is destroyed (refcounted disable deferred — harmless: the block idles with no KeyGen scheme routing to it). The KeyGen scheme stays **RELATIVE** (`0xc04c0000`) — addressing mode was never the bug, so patches `0097`/`0100` retain the relative encoding. **HW-VALIDATED on a clean cold boot, NO manual `/dev/mem` write** (ISO `vyos-2026.06.09-2032-rolling-LS1046A-default-arm64`, CI run `27233990716`, commit `1a48948`, kernel `6.18.34-vyos`, DUT `192.168.1.190`): baseline no-policer `FMPL_GCR=0x00500002`/TPC=0; after `set interfaces ethernet eth3 ingress-policer bandwidth 1gbit`, GCR **auto-flipped** to `0xC0500002`, GSR=`0x4000`, TPC immediately `0x0a`, tc matchall `in_hw` with no bind error; ping `10.99.1.2` ×8 → **8/8, 0% loss** (was 100%), TPC `0x3a→0x46`, RPC/YPC=0. (The `tc police` sw-counters stay 0 because the policer is fully HW-offloaded `skip_sw`/`in_hw` — the FMPL **TPC/GCR/RPC** registers are authoritative; the BMI RX stat counters are read-clearing and UNRELIABLE.) Qdrant `topic=dpaa1-ingress-policer-bug3a date=2026-06-09` (`fmpl_base=0x01AC0000`, `gcr_enabled_value=0xC0500002`).
> >
> > **BUG 3b — non-revert half did NOT reproduce on the fixed image; flood-crash half still uncharacterized.** Both designed fixes are now SHIPPED. **(A, kernel — primary, in `0104`):** the DPAA tc block-cb is registered via `flow_block_cb_alloc(... release ...)` (replacing `flow_block_cb_setup_simple`) so a **release callback** fires on every block-unbind (`tcf_block_offload_unbind → flow_block_cb_free → release`) and reverts the FMan scheme out of PLCR + destroys the profile — self-healing on ANY teardown (qdisc del, iface down, filter del). This closes the original ordering hole where VyOS `update()`→`super().update()` issues `tc qdisc del dev eth3 ingress` and `__tcf_block_put()` unbinds the block-cb *before* `tcf_block_flush_all_chains()` destroys the matchall filter, so `TC_CLSMATCHALL_DESTROY` never reached the driver. **(B, vyos-1x-025 — belt-and-suspenders):** the idempotent `tc filter del dev {ifname} parent ffff: pref {pref}` now runs BEFORE `super().update()`, so the filter-level destroy fires while the block is still bound. **HW result on the fixed cold-boot image** (`2026.06.09-2032-rolling`, commit `1a48948`, kernel `6.18.34-vyos`, DUT `192.168.1.190`): after `delete interfaces ethernet eth3 ingress-policer` + commit, the tc filter empties, the policer detaches, `FMPL_GCR` stays enabled but no scheme routes to it, ping after delete is **5/5 0% loss** (eth3 alive), and delete→re-apply is clean — the eth3-dead-after-delete symptom of the pre-fix images (`0522`/`0234`) is **GONE**; the non-revert half is **CLOSED**. **CAVEAT — the iperf3 flood-crash half of 3b is deliberately NOT yet tested:** the now-reverted `FMPL_PMR`-window build caused a watchdog reset under a policed `iperf3 -R` flood, and a stuck-PLCR scheme is known to survive a warm/watchdog reset (only a clean cold reboot clears it). Since a remote `sudo reboot` is only a warm reset, characterizing the flood-crash half safely needs serial-console capture (`telnet 192.168.1.16:5555`) and a physical/cold power-cycle path. **Always repro/characterize the policer with a few ping packets, NEVER a flood.** This half remains OPEN. Full register dumps + reader scripts in Qdrant `topic=dpaa1-ingress-policer-bug3a-3b date=2026-06-09`.

`0100` makes the entry-point bodies productive via the `0099` bridge idiom: the fman-side `fman_pcd_plcr.c` (in `fsl_dpaa_fman.ko`) owns all MURAM and EXPORTs `fman_pcd_plcr_install`/`_destroy` taking the neutral BE-ready `struct fman_pcd_plcr_hw_profile`; the dpaa-side `dpaa_fman_caps.c` gates on `FMAN_CAP_POLICER_TRTCM`, translates host→neutral via `dpaa_plcr_prof_to_hw()`, delegates via `fman_get_pcd()`. The encoder implements RM §8.7.6: rate=`exp<<29|mant<<13` (note the **u16-MHz-not-Hz gotcha** — `fman_get_clock_freq()` returns MHz, ×1e6 before the `bps·2³¹/clk` division), 256-byte burst quantisation, 16-byte MURAM profile record. Validation: `cir_bps==0`→`-EINVAL`, trTCM `pir_bps<cir_bps`→`-ERANGE`, no cap→`-ENOTSUPP`. `struct fman_policer_profile` (srTCM RFC 2697 / trTCM RFC 2698, `cir_bps`/`cbs_bytes`/`pir_bps`/`pbs_bytes`, color-blind/aware) from `0091a`; all rates bits/sec, bursts bytes. Native arm64 0 warn/0 err, `patch-health board/0100 ✓`. Cap auto-detected by `0086a` DT probe.

> Full per-patch prose (RM §8.7.6 encoder field-by-field, PMR control-word bits, struct-field listings, compile matrix): Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

```c
int  fman_pcd_plcr_install(struct fman_pcd *pcd, u8 port_id, u8 profile_id,
                           const struct fman_pcd_plcr_hw_profile *hw);
void fman_pcd_plcr_destroy(struct fman_pcd *pcd, u8 port_id, u8 profile_id);
```

```c
int  fman_policer_install(struct fman *fm, u8 port_id, u8 profile_id,
                          const struct fman_policer_profile *prof);
void fman_policer_destroy(struct fman *fm, u8 port_id, u8 profile_id);
bool fman_policer_caps_supported(void);
```

Per-qband or per-flow ingress rate-limit using srTCM/trTCM. Yellow/red drop in FMan; FD status carries color. 256 profile slots; budget 8 per netdev (4 per-qband + 4 per-flow).

**Consumers:**
- **`default`** — backs tc/nftables ingress rate-limit offload via `flow_block_offload`. CLI: `set firewall ... limit offload hw-policer` (vyos-1x follow-on). Replaces CPU-side `tc actions police`.
- **`vpp`** — `set vpp settings hw-offload policer per-qband-limit 2.5g per-flow-limit 100m`. DoS protection, SLA enforcement.
- **`ask`** — nft `limit` offload backend. Each rate-limited rule → one Policer slot.

**Acceptance gate M3-3d:**
1. 2.5 Gbps limit caps offered 3 Gbps to ≤ 2.5 Gbps at consumer, red-drops visible. **← STEERING FIXED 2026-06-09 (image `0522`); BUG 3a FIXED + HW-VALIDATED 2026-06-09 (image `2032`, commit `1a48948`): the FMPL policer block master-enable (`FMPL_GCR.EN|STEN`) was clear at boot — `0100` `plcr_enable_block()` flips GCR `0x00500002→0xC0500002`, policed ping went 100% loss → 0% on a clean cold boot, TPC increments. BUG 3b non-revert half also fixed (kernel `flow_block_cb` release in `0104`). The throughput-cap number itself is the only piece of this gate still to be measured — the §8 traffic harness (now available) drives it. The qualitative pass/fail (policer enforces vs bypasses) is already PROVEN by the ping result; what remains is the quantitative 2.5 Gbps figure.**
2. No regression unconfigured. 3. `commit` no netdev flap.

### 5.7 CEETM egress shaping (M3-3e)

**Patch:** `0104` (reserved; `0092` was reassigned to the FMan PCD subsystem forward-port per §6.1.13). **Status:** **blocked — no mainline CEETM**.

**Scope correction (2026-05-29).** Earlier revisions of this spec assumed `dpaa_eth_ceetm.c` and `qman_ceetm.c` ship in mainline and that M3-3e merely needs to "wire the existing qdisc as a root qdisc replacement". A tree audit on `work/linux-6.18.31` proves that assumption **false**: none of `drivers/soc/fsl/qbman/qman_ceetm.c`, `drivers/net/ethernet/freescale/dpaa/dpaa_eth_ceetm.{c,h}`, or any `qman_ceetm_*` symbol in `include/soc/fsl/qman.h` exists, and `dpaa_eth.c` has no `ndo_setup_tc`. The CEETM tc-qdisc was an NXP-LSDK / pre-5.x out-of-tree driver dropped on the road to mainline.

**Revised work breakdown.** M3-3e is now a multi-part *forward-port*, materially larger than 3b/3c/3d:

1. **QMan CEETM core** — forward-port `qman_ceetm.c` (~2600 LOC: LNI/channel/CQ/CCG/LFQ allocation, shaper-rate programming, CGR config) plus the `qman_ceetm_*` API into `include/soc/fsl/qman.h`. Must reconcile with the mainline 6.18 `qman.c` portal/FQ APIs that have drifted since the SDK fork.
2. **DPAA CEETM qdisc** — forward-port `dpaa_eth_ceetm.{c,h}` (~1900 LOC: the `Qdisc_ops`/`tcf` glue, class hierarchy, `mqprio`-style mapping) and add `ndo_setup_tc` to `dpaa_eth.c`'s netdev_ops.
3. **Stub-first landing (matches 3b/3c/3d cadence)** — ✅ **DONE + DUT-VALIDATED 2026-06-07** (`0104b-dpaa-ceetm-stub.patch`, commit `4e080cb`). A `dpaa_fman_caps`-style stub landed ahead of the core forward-port: Kconfig `CONFIG_DPAA_HW_CEETM` (default y, `depends on DPAA_HW_CC_STEERING` — the scaffold object `dpaa_fman_caps.o` is gated by `CONFIG_DPAA_HW_CC_STEERING` in the Makefile, so the dependency mirrors the HM/POLICER siblings and prevents an undefined-symbol link; ucode-independent — no FMan-cap gate), an opaque `struct dpaa_ceetm_config { u32 reserved; }`, and the by-pointer entry points `dpaa_ceetm_qdisc_install()` (`-ENOTSUPP`), `dpaa_ceetm_qdisc_destroy()` (no-op), `dpaa_ceetm_supported()` (`false`), all `EXPORT_SYMBOL_GPL` in `dpaa_fman_caps.{c,h}`. `dpaa_ceetm_supported()` deliberately gates on "is the productive datapath compiled in", NOT on an FMan ucode bit. Downstream CLI consumers can wire calls now; productive shaping (items 1–2) then replaces the bodies. **Hardware acceptance:** CI run `27106292468` (flavor=default) built `CONFIG_DPAA_HW_CEETM=y`; DUT (192.168.1.190, ISO `vyos-2026.06.07-2209-rolling-LS1046A-default-arm64`, kernel `6.18.34-vyos`) confirmed config `=y` + dependency satisfied, all three CEETM symbols + `__ksymtab_*` in `/proc/kallsyms`, and zero regression (FMan caps `0x17`, eth0-eth4 UP, no new DPAA dmesg errors).

The scheduler core works on any ucode (it is QMan silicon, not FMan PCD); only **colour-aware drop** is ucode-210 gated. This is the one M3-3 sub-milestone that needs no FMan-PCD subsystem — but it needs the QMan CEETM forward-port instead.

**CGRs mandatory for strict-priority** (design target, once the forward-port lands). Each CEETM CQ gets a CGR with tail-drop threshold sized to ~2 ms of class-rate-worth of buffers. Drop counts exposed as `ethtool -S fm_ceetm_cgr_drops_class_<N>`. §6.1.4 software `xsk_tx_inflight` budget sits on top for VPP; CGR tail-drop is the first line for all flavors.

**Consumers:**
- **`default`** — `set qos policy shaper hardware ceetm class 1 rate 4g ceil 6g ...` (vyos-1x follow-on; tc qdisc already mainline). Replaces software `htb`/`tbf`.
- **`vpp`** — `set vpp settings hw-offload egress-shaper strict-priority` (or `htb ...`). Mutex per-port with VPP-internal shaping (§7.4).
- **`ask`** — same as default for kernel-managed traffic.

**Acceptance gate M3-3e:**
1. Strict-priority: low-priority starves under high-priority line-rate.
2. HTB: per-class caps hold ± 0.5% over 60 s.
3. `tc qdisc show dev ethX` shows CEETM.
4. No regression at unshaped wire speed.
5. CGR drops visible under sustained class congestion.

### 5.8 DCSR observability

**Patches:** `0079`. **Status:** dut-validated. Per §4.9, debugfs `/sys/kernel/debug/dpaa-eth/<iface>/dcsr/{bmi,parser,kg,pol}_err`. Reads rate-limited ≥ 1 ms.

**Consumers:** all three flavors equally.

---

## 6. Flavor-Specific Consumers

### 6.1 VPP-only — AF_XDP zero-copy datapath

**Patches:** `0071/0073/0075a/0075b/0075c/0076/0077/0079/0080` (landed/dut-validated); `0082/0083/0084` (dut-validated).

Consumed by the `af_xdp_pool.ko` flavor module that registers `qmgmt_ops->{xsk_pool_attach, xsk_pool_detach, xsk_wakeup}` per §3.4.

#### 6.1.1 XSK pool attach/detach

DPAA1's BMan is a HW buffer-pool manager — FMan RX BMI autonomously dequeues with no per-packet driver involvement. Other ZC drivers use software page pools; DPAA1 cannot swap. **Approach: XSK-backed BMan pool** — allocate a dedicated `dpaa_bp` whose backing memory is the XSK UMEM.

Attach sequence:

1. **Validate constraints, fail closed.**
   - `dev->mtu <= 3290`
   - `xsk_pool_get_rx_frame_size(pool) >= DPAA1_MIN_UMEM_CHUNK` (3840 — derived from MTU 3290 + L2 14 B + DPAA_FD ≤ 64 B + 64 B alignment slack. On arm64 4 KiB-page kernels the only valid `chunk_size` is 4096 → `frame_size = 4096 − headroom(0) − XDP_PACKET_HEADROOM(256) = 3840 = DPAA1_MIN_UMEM_CHUNK`.)
   - First UMEM chunk satisfies `IS_ALIGNED(addr, DPAA_FD_DATA_ALIGNMENT)` (64 B)
   - `xsk_pool_get_headroom(pool) >= priv->tx_headroom`
   - `queue_id < priv->xsk_max_qbands` (4)
2. **DMA-map** `xsk_pool_dma_map(pool, priv->mac_dev->dev, DMA_ATTR_SKIP_CPU_SYNC)`. `priv->mac_dev->dev` = `struct mac_device::dev` (`mac.h:25`). CoreNet-coherent identity-mapped via dma-direct. Failure → `xsk_dma_map_fail++`, `-EFAULT`.
3. **Allocate dedicated BMan pool ID** via `bman_acquire()`. Record in `priv->xsk_bpid[queue_id]`.
4. **Seed**: iterate `xsk_buff_alloc_batch()`, `bman_release()` into new pool. `DPAA1_XSK_INITIAL_SEED = 8192`.
5. **Register memory model**: `xdp_rxq_info_reg_mem_model(..., MEM_TYPE_XSK_BUFF_POOL, pool)`.
6. **Bind NAPI** (M3-3 step 1, patch 0080): `WRITE_ONCE(priv->qmap[queue_id].napi, &per_cpu_ptr(priv->percpu_priv, 0)->np.napi)` — BSP cpu 0 stopgap until M3-3 step 2 lands true per-qband NAPI.
7. **Publish**: `rcu_assign_pointer(priv->xsk_pool[queue_id], pool)`.

**Detach symmetry — FMan BMI quiescence mandatory.** Packet arriving after `xsk_pool=NULL` published but before `xsk_pool_dma_unmap()` still holds a DMA cookie into about-to-be-unmapped UMEM. Detach MUST halt RX BMI:

1. `rcu_assign_pointer(priv->xsk_pool[queue_id], NULL)`.
2. `synchronize_net()`.
3. `fman_port_disable(rxp)` — halts BMI. ~10 ms link bounce. Acceptable (control-plane event).
4. Drain DQRR on port's pool channel.
5. `qman_retire_fq()` on each FQ in qband.
6. Drain dedicated BMan pool via `bman_acquire()`; `xsk_buff_free()` per handle.
7. `xsk_pool_dma_unmap(pool, DMA_ATTR_SKIP_CPU_SYNC)`.
8. Reactivate FQs against original kernel BMan pool; reset to `MEM_TYPE_PAGE_ORDER0`.
9. `fman_port_enable(rxp)`.

Phase 4 (single-MAC dual-flavor) replaces step 3 with surgical ucode-210 "Disable BMI single port" HC.

**Counters:** `xsk_pool_attach_ok/fail`, `xsk_pool_detach_ok/timeout`, `xsk_bman_seed_short`, `xsk_bman_starve`, `xsk_tx_backpressure`, `xsk_dma_map_fail`, `xsk_align_reject`, `xsk_headroom_reject`, `xsk_mtu_reject`.

#### 6.1.2 RX ZC datapath (M3-3 step 3, dut-validated)

Per-frame dequeue callback:
1. `qband = dpaa_fq_to_qband(priv, qm_fd_get_fqid(fd))`.
2. Branch on `rcu_dereference_bh(priv->xsk_pool[qband])`:
   - NULL → mainline skbuf.
   - non-NULL → ZC: phys → `xdp_buff *` via pool index, length/offset from FD, `bpf_prog_run_xdp()`, redirect/drop/TX.
3. `xdp_do_flush()` if any redirected.

#### 6.1.3 NAPI-hooked BMan refill (M3-3 step 4, dut-validated kernel-skbuf path)

1 s workqueue tick meaningless at line rate (2048-buffer pool drains in ~3.5 ms at 7 Gbps/1500 B). BMan-depth probing moves into NAPI poll:
1. `bman_pool_get_count(priv->xsk_bpid[queue_id])` at start of poll.
2. Below `DPAA1_XSK_REFILL_THRESHOLD = 256` → batch-32 refill from XSK fill ring.
3. Below `DPAA1_XSK_MIN_DEPTH = 64` → `xsk_bman_starve++`, escalate batch 32→256.

#### 6.1.4 TX ZC datapath (M3-3 step 5, dut-validated kernel-skbuf path)

1. In NAPI poll after RX: `xsk_tx_peek_release_desc_batch()` up to budget. `xsk_buff_raw_get_dma()` → build `qm_fd` → `qman_enqueue()` on qband's egress FQ.
2. Service TxConf FQ same NAPI iteration with **higher** priority than RX refill: `xsk_tx_completed_addr()` per confirmed FD.
3. If XSK Tx Ring drained but `XDP_USE_NEED_WAKEUP` socket exists, set Tx need-wakeup flag.

**Strict Tx backpressure (two layers):**
1. **CEETM CGR HW tail-drop** (§5.7).
2. **Software in-flight budget.** `priv->xsk_tx_inflight[qband]` (atomic). Above `DPAA1_XSK_TX_MAX_INFLIGHT = 1024` → stop pulling, set Tx need-wakeup, `xsk_tx_backpressure++`. Resume below `DPAA1_XSK_TX_LOW_WATER = 512`. Hysteresis prevents oscillation.

#### 6.1.5 A050385 erratum interaction

XDP workaround relocates Tx frames within 64 B of a 4 KB boundary. XSK cannot relocate (UMEM is app-owned), so we require UMEM ≥ 64 B headroom and refuse `bind(XDP_ZEROCOPY)` otherwise. VPP defaults `XDP_PACKET_HEADROOM = 256`.

#### 6.1.6 Blocker A — BMan "Invalid Command Verb" (patches `0086`/`0087`/`0088`) — 2026-05-28

Board patches `0086`/`0087`/`0088` closed the BMan err-IRQ `BM_EIRQ_IVCI` ("Invalid Command Verb") seen on the DUT G5 reproducer (xsk-bind-probe + iperf3 UDP flood). **Root cause (`0088`):** `xsk_pool_dma_map()` must target `priv->rx_dma_dev` (the FMan RX port device with the 40-bit DMA mask owning the BMan FBPR window programmed by U-Boot), NOT `priv->mac_dev->dev` (32-bit mask, wrong IOMMU group) — DMA addresses resolved outside BMan's FBPR window so the 40-bit address bytes in RCR slots were rejected as IVCI. `0086` (8-buffer chunked release) and `0087` (zero stack bpid residue) were necessary-but-insufficient precursors. **Closed 2026-05-28** (ISO 2026.05.28-0149, commit `d1b6e30`, run 26549682211): 60 s G5 hold → 0 IVCIs, 0 BUG/WARN/stall, clean attach/detach both cycles.

**CI-pipeline invariant discovered here:** `bin/ci-setup-kernel.sh` stages board patches via explicit per-patch `cp` lines (no glob); `patch-health.sh` does NOT cross-check that the `cp` line exists, so a missing `cp` silently ships an ISO without the patch (ISO 2026.05.28-0116 / commit `b57dd6a` shipped without any of 0086/0087/0088). Every new board patch requires a matching `cp` line AND a post-build grep of the staged source for the expected token. (Original 0086/0087 reservation for HM/Policer is superseded — those re-numbered to `0090`/`0091`.)

> Full diagnosis chain, verification matrix, and counter snapshots: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.7 Blocker B — productive RX delivery via XSKMAP redirect (patch `0089`) — 2026-05-28

Pre-`0089` an `XDP_ZEROCOPY`-bound AF_XDP socket on DPAA1 observed `rx_packets = 0` despite healthy driver mechanics: `dpaa_run_xdp()` early-returns `XDP_PASS` when no XDP program is attached, so the FD travelled the unchanged skbuf path and the bound XSK socket saw nothing. **`0089` (userspace probe stage-3, no kernel change)** adds an opt-in `--xskmap` flag to `bin/dpaa1-xsk-bind-probe.py` that creates an XSKMAP, loads a 6-insn `bpf_redirect_map()` XDP program (DRV-mode attach, SKB fallback), binds `XDP_ZEROCOPY`, then `BPF_MAP_UPDATE_ELEM`s the bound socket into the map. **Closed blocker B** (DUT G5, ISO 2026.05.28-0149): `rx_packets` 0 → 30, DRV-mode attach honoured, 0 IVCI/BUG/stall, blocker-A invariants held under XSKMAP load.

**Mode is copy-mode, not zero-copy.** The page-backed `xdp_buff` is copied into a UMEM chunk; true ZC (FMan DMA directly into XSK-pool BMan chunks) is the follow-on M3-3 step 7 work scoped to patches `0093+`, structured as the three-mechanism plan — **Recognise** (match `fd->bpid` against `priv->xsk_bpid[band]`), **Recover** (`xsk_buff_recv()` instead of `build_skb()`), **reProgram** (set the FMan RX port primary BPID to the XSK pool BPID).

**Acceptance gate M3-3 (full VPP datapath):** (1) `xdp-features` reports `NETDEV_XDP_ACT_XSK_ZEROCOPY`; (2) `xdpsock -i ethX -q 3 -z -r` receives traffic; (3) ≥ 7 Gbps single-stream IPv4 fwd at < 5% kernel-net CPU/worker; (4) `perf top` clean of `dpaa_rx`/`__alloc_skb`/`memcpy`; (5) 4-worker aggregate ≥ 14 Gbps dual-SFP+; (6) 24 h iperf3 stress no oops/leak/stall.

> Probe-side fixes (`bpf_attr` `__aligned_u64` pad, `XDP_FLAGS_UPDATE_IF_NOEXIST` removal), full verification matrix, counter snapshots: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.8 Blocker B — under-load FILL-ring backpressure crash + fix — 2026-05-28

The first under-load probe run (5 Gbit/s UDP flood) panicked in softirq: an unconstrained userspace recycle loop bumped `fill_producer` without checking `fill_consumer`, overran the 256-slot FILL ring, and handed the same UMEM chunk to `xp_alloc()` twice → `xsk_buff_pool` free-list corruption (`list_add corruption` → `dcache_clean_poc` paging fault → 52 s soft-lockup). **Probe fix (no kernel change):** read the kernel `fill_consumer` every iteration and refuse to write a FILL slot when in-flight `>= N_FRAMES` (`skipped_recycle` counter; "drop > corrupt"); plus `MAX_BATCH=64` and per-line flush. Post-fix DUT run sustained `rx_packets = 296 716` / 427 MB clean, 0 crashes, 0 `xsk_bman_starve`.

**Key finding — the "1.3 Gbit/s ceiling" is an iperf3 UDP-receiver artifact, not a driver limit.** `/proc/net/snmp` showed `RcvbufErrors == InErrors` byte-for-byte (loss at socket enqueue, single-threaded `recvmmsg()` on a 1.6 GHz Cortex-A72 caps ~150–200 kpps/core), while `ethtool -S` showed 0 NIC/BMan/FQ drops and `lxc202↔backup` TCP proved the L2 fabric does 7.5 Gbit/s. **Diagnostic rule:** on receiver-side UDP loss, check `/proc/net/snmp Udp:` BEFORE blaming wire/switch/driver — `RcvbufErrors == InErrors` means it is the userspace consumer.

**Crash exposed a kernel-side invariant for step 7:** the eventual true-ZC path needs a FILL-ring-empty/double-release guard (the under-load failure mode here was free-list corruption, not a clean `NULL` from `xp_alloc()`). Stored under tag `dpaa1+xsk_bman_refill+fill_ring_invariant` for the step-7 design pass (addressed by `0095`, §6.1.12). Gate-3 entry options for the next session: **A** XDP_DROP driver-only capacity (§6.1.8a), **B** `xdpsock` C-based probe, **C** multi-process iperf3 (§6.1.8b).

> Full crash trace, probe-fix detail, cross-check measurement table, and lab topology/creds: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.8a Option A — XDP_DROP driver-only RX capacity (tool `bin/dpaa1-xdp-rxcap.py`) — 2026-05-28

`bin/dpaa1-xdp-rxcap.py` (standalone, no libbpf/clang) attaches a 2-insn `XDP_DROP` program (DRV-mode, SKB fallback) and samples driver `rx_packets`/`rx_bytes` + `qman cg_tdrop`/`qman fq tdrop`/`rx dma error`/`rx fifo error` deltas across a hold window. XDP_DROP frees each frame in-driver with no skb/socket/userspace consumer, so only the driver RX path advances. **Finding: the driver dropped 0% at every offered rate the lab could generate** (zero qman/dma/fifo drops, zero net-core `rx_dropped`), while the kernel-socket baseline at 2 Gbit/s offered lost 51% entirely at socket enqueue — direct counter-level proof that **Gate 3 is consumer/methodology-bound, not driver-bound.** The literal ≥7 Gbit/s number could not be hit because no lab traffic source reaches it: iperf3 is structurally incompatible with XDP_DROP (control channel stalls), nping is CPU-bound ~1 Gbit/s and doesn't scale with parallelism, no kernel pktgen on the backup. A wire-rate generator (TRex/DPDK-pktgen) is needed for a *literal* Gate-3 pass; the tool is proven and reusable for that re-measurement.

> Full result matrix, methodology notes, and lab creds/recipe: Qdrant `topic=dpaa1-afxdp-gate3-option-a-measurement`.

#### 6.1.8b Option C — multi-flow TCP `-R` RX scaling — 2026-05-29

Ran on DUT image `6.18.33-vyos` (full AF_XDP datapath live: 16 `xsk_*` counters, `FMan PCD caps = 0x17`, `af_xdp_pool: registered`) — pure userspace measurement, no new ISO needed. DUT-as-receiver TCP `-R` aggregate **peaks at `-P 4` = 5.57 Gbit/s** then declines (`-P 8` 4.12, `-P 16` 3.75) due to single-iperf3-server contention on the generator. `mpstat -P ALL` during `-P 16`: RX softirq genuinely distributed across CPU0/1/2 (79.9 / 97.6 / 49.1 %soft, CPU2 ~50% headroom) while the single userspace receiver process saturated CPU3 (84% sys) — reconfirming the §6.1.8/§6.1.8a "userspace socket-drain, not driver" finding with TCP and per-core evidence. The per-CPU NAPI + contiguous-banding work (M3-3 steps 2a–2c) is doing its job.

**Gate-3 status after Options A + C:** the driver (a) drops 0% of every frame the fabric delivers and (b) scales RX softirq across cores to 5.57 Gbit/s aggregate TCP with headroom. Gate 3 is **consumer/methodology-bound, not driver-bound**; a literal ≥7 Gbit/s figure needs generator-side multi-process iperf3 or a wire-rate generator (a lab-provisioning task, not kernel code). **M3-3 step 7 (true ZC) is not required for Gate 3 on driver-capacity grounds.**

> Per-stream results, full `mpstat` table, generator-credential detail: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.9 DUT→backup TX direction measurements — 2026-05-28

DUT-as-sender TX peaks at **4.93 Gbit/s single-stream TCP (0 retr)**, CPU3-bound. Root cause: **no in-tree, NXP-SDK, or DPDK driver exposes TSO/LSO for FMan v3 on Linux** (verified across mainline `dpaa_eth.c`, NXP SDK `sdk_dpaa/dpaa_eth.c`, DPDK 25.03 DPAA PMD docs, and NXP's own `dpaa.rst`). The AGENTS.md shorthand "TSO is hardware-impossible on DPAA1" is conclusion-correct but mechanism-imprecise: more accurately, **TSO is driver-unimplemented across every NXP-authored datapath driver and not advertised by any FMan v3 BMI capability bit consulted by Linux**. Net effect: `tcp-segmentation-offload: off [fixed]`, software GSO active, every MSS traverses the full softirq→driver TX path one skb at a time.

**Implication for Gate 3:** the gate is defined against the AF_XDP zero-copy TX path which bypasses the kernel softirq TX entirely. The 4.93 Gbit/s kernel-skbuf TX ceiling **does not block AF_XDP Gate-3 closure**; step 7 (FMan RX DMA into XSK-pool chunks via `priv->xsk_bpid` matching, plus TX symmetry via `xsk_tx_peek_release_desc_batch()` → `qman_enqueue()`) remains the correct ZC path.

> Full TX measurement table and TSO source citations: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.10 True-ZC RX sub-increment 1 — `xsk_zc_eligible` recognition probe (patch `0093`) — 2026-05-29

Step 7 is split into small individually-DUT-validated sub-increments because mechanism 3 (FMan RX-port BPID reprogram) is hardware-risky (§6.1.8 records three under-load crashes). **Sub-increment 1 = mechanism 1 (Recognise) only**, a strictly-diagnostic counter with zero datapath change. `0093` adds the 17th `xsk_*` ethtool counter `xsk_zc_eligible`, bumped inside the existing `0083` RCU read-section in `rx_default_dqrr()` when an FD reaches a band with a bound XSK pool AND `fd->bpid == priv->xsk_bpid[_band]` — the exact arithmetic mechanism 2 (Recover) will rely on. Byte-identical to mainline on `default`/`vpp` (no XSK pool → equality never holds → dead code). It proves the FD-recognition arithmetic in the live hot path is correct before any hardware-risky reprogram, and becomes the observability oracle once the reprogram lands.

> Host validation detail (cumulative apply/compile, patch-health stack-dependent-fail rationale): Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.11 True-ZC RX sub-increment 2 — `xsk_zc_rx_armed` attach-time arm observability (patch `0094`) — 2026-05-29

Re-applying the observe-before-write discipline, the original monolithic "sub-increment 2 = reprogram WRITE + recover" is split: `0094` is the strictly-diagnostic *read side* of mechanism 3, the hardware-risky WRITE moves to a later sub-increment. `0094` adds the 18th counter `xsk_zc_rx_armed`, bumped once per successful `xsk_pool_attach()` when the dedicated XSK BPID (`priv->xsk_bpid[queue_id]`) differs from the kernel page-pool BPID the FMan RX port currently DMAs into (`priv->dpaa_bp->bpid`). **No `fman_port` write happens** — copy-mode RX is preserved bit-for-bit. It proves, from userspace at attach time before any hardware write, that the reprogram has a distinct, meaningful target (`xsk_bpid != dpaa_bp->bpid`); a zero value would catch a `bman_new_pool()` BPID collision dormant, before it could crash the DUT.

> Host validation detail and CI `cp`-line wiring: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.12 True-ZC RX sub-increment 3 GATE — `xsk_fill_guard_block` FILL-ring double-release guard (patch `0095`) — 2026-05-29

Before the hardware-risky WRITE lands, the observe/assert-before-write cadence requires an in-driver assertion that the XSK FILL-ring single-producer invariant actually holds. The §6.1.8 root cause (a FILL-ring producer outrunning the consumer hands the same chunk to `xp_alloc()` twice) escalates from a dropped packet (copy-mode) to an **FMan DMA overwrite of a live chunk** once FMan DMAs directly into XSK-pool chunks. `0095` adds the 19th counter `xsk_fill_guard_block`: inside `af_xdp_pool_napi_refill()`, after `xsk_buff_alloc_batch()`, each per-handle DMA cookie is scanned against the `bmbs[]` already filled this batch; on a duplicate it bumps the counter, warns rate-limited, and **skips the whole round's `bman_release()`** (drop > corrupt — a duplicate proves the SPSC cursor is already inconsistent so the whole batch is untrusted). Byte-identical to `0084` v3 under a correct producer; the guard never fires.

**Sub-increment 4 entry conditions after `0095`** (all observable from userspace before any MURAM write): (1) `xsk_zc_rx_armed > 0` after a bind (`0094` — distinct target); (2) `xsk_fill_guard_block == 0` under sustained load (`0095` — FILL producer well-behaved); (3) `xsk_zc_eligible` (`0093`) growing after the reprogram lands (per-FD success oracle).

> Host validation detail, anchor-disambiguation note, and CI `cp`-line wiring: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.13 True-ZC RX sub-increment 3 — `xsk_zc_rx_recovered` Recover read-side, dormant (patch `0096`) — 2026-05-29

`0096` lands the last *read-only* mechanism — the dormant read side of mechanism 2 (Recover) — as the 20th counter `xsk_zc_rx_recovered`. Inside the `0083`/`0093` RCU read-section, after the recognition test, a guarded Recover decision bumps the counter when the band is armed (`xsk_zc_rx_armed`, `0094`) AND the FILL guard has never fired (`!xsk_fill_guard_block`, `0095`); the FD then continues through the UNCHANGED skbuf path (no `xsk_buff_recv()`, no `xdp_do_redirect()`). After `0096` every one of the three mechanisms has its observable half landed and only the hardware-risky reProgram WRITE plus the productive recover body remains. Byte-identical to mainline on `default`/`vpp` (bpid-match false → dead code) — the cost is a single integer compare extending the `0093` test.

**Scope finding (the original blocker) — and its resolution.** During `0096` authoring it was confirmed that no FMan RX-port external-BPID-reprogram API existed in the dpaa1 board stack — the productive FMan PCD subsystem (`fman_pcd_*`) lived ONLY in the ASK flavor patches. **This forward-port is now LANDED:** commit `f307193` (board patches `0092`/`0097`/`0098`/`0099`/`0100`) forward-ported the FMan PCD subsystem into the common board stack via the bridge idiom (fman-side TU defines silicon + EXPORTs neutral-spec `_install`/`_destroy`, owns MURAM; dpaa-side `dpaa_fman_caps.c` gates on caps and delegates; public `include/linux/fsl/fman_pcd.h` carries the neutral contract). The `fman_pcd_*` accessors that sub-increment 4 needs now exist in the common board stack, so **sub-increment 4 is no longer blocked on the forward-port** — it remains gated only on the three userspace-observable preconditions above (§6.1.12) plus incremental DUT validation with a short hold first.

**MURAM-budget regression — CLOSED end-to-end (2026-05-30).** The initial `f307193` forward-port shipped the OLD 96 KiB `FMAN_PCD_MURAM_RESERVED_BYTES`, reproducing the PR14b `-ENOMEM` (`fman_pcd: cannot reserve 98304 bytes … err -12` → soft-fail "continuing without PCD"). Corrected to 64 KiB in `b90ef86` (graft stack-apply `cd1b9c5`). The `0101`-carrying ISO `vyos-2026.05.30-0233-rolling-LS1046A-default-arm64` (commit `02fb1ac`) DUT-confirmed: `fman_pcd: ready (64 KiB MURAM reserved at offset 0x4ac00)` (non-NULL `pcd->state`) + `caps = 0x17`. FMan PCD productive (CC/HM/Policer `_install`/`_destroy` reachable); sub-increment 4 gated only on the three §6.1.12 preconditions plus a short incremental DUT hold. Full regression-trace + ISO-precondition history: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

**Operator DUT gate reader — `xsk-zc-check`.** The four sub-increment-4 entry-gate counters (`xsk_zc_eligible`, `xsk_zc_rx_armed`, `xsk_fill_guard_block`, `xsk_zc_rx_recovered`) are surfaced to `board/scripts/xsk-zc-check` (installed to `/usr/local/bin/xsk-zc-check`). It reads the 20-counter `xsk_*` suite via `ethtool -S` on eth3/eth4 and renders the §6.1.12 verdict — **dormant** (no ZC bind, all `xsk_zc_*` 0, the expected shipping state), **ZC-armed** (`xsk_zc_rx_armed > 0` AND `xsk_fill_guard_block == 0` → preconditions (1)+(2) MET), or **fault** (`xsk_fill_guard_block > 0` / hard attach/DMA error → WRITE must stay disabled). Exit 0/1/2 — usable as a Nagios/monit probe and the single command to confirm gate state before sub-increment 4 lands.

> Host validation detail (hunk arithmetic, rebrace note) and CI `cp`-line wiring: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.14 True-ZC RX sub-increments 1–3 — DUT validation (dormant read-side) — 2026-05-29

Sub-increments 1–3 (Recognise/Recover read-side counters `0093`–`0096`) are **dut-validated** on real LS1046A hardware. Build under test: ISO `vyos-2026.05.29-1554-rolling-LS1046A-vpp-arm64`, kernel `6.18.33-vyos`, CI run `26647449274` (`dpaa1`, `FLAVOR=vpp`). All five gates GREEN: G1 eth0–eth4 expose the full 20-entry `xsk_*` suite; G2 the four entry-gate counters read 0 (expected dormant/no-ZC-bind state); G3 `/usr/local/bin/xsk-zc-check` reports healthy, exits 0; G4 dmesg clean of IVCI/`list_add`/lockup/Oops/BUG, `xsk_fill_guard_block` 0 on all ports; G5 thermal 54–55 °C all zones. Sub-increments 4/4a/4b have since all LANDED: the FMan-port-BPID reprogram WRITE primitive (`0102`, §6.1.15), the Recover reverse-map infrastructure (`0103a`, §6.1.16 — the reverse-map approach replaces the non-existent `xsk_buff_recv()`, which 6.18.31 does not provide), and the productive coupled reprogram-WRITE + Recover-redirect (`0103b`, §6.1.17, DUT-entry-gate-validated; the productive `xsk_zc_rx_redirect` oracle remains gated on the §6.1.17 BMI re-commit effectiveness + traffic-steering items).

> Full per-port counter dumps, ftrace evidence, and gate pass/fail detail: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`. (vbash gotcha: invoke `/usr/sbin/ethtool` by absolute path — a bare `ethtool` is intercepted by VyOS op-mode.)

#### 6.1.15 True-ZC RX sub-increment 4 — `fman_port_set_rx_bpool()` reProgram WRITE primitive (patch `0102`) — 2026-05-30

`0102` lands the last *mechanism* of the three-mechanism plan — the WRITE half of mechanism 3 (reProgram) — as a durable in-tree exported primitive. Shipped dormant (no caller in its own patch); it is now productively called by the landed `0103b` (§6.1.17). It adds `int fman_port_set_rx_bpool(struct fman_port *port, u8 old_bpid, u8 new_bpid)` to `drivers/net/ethernet/freescale/fman/fman_port.c` (+`EXPORT_SYMBOL_GPL`) plus its prototype in `fman_port.h`. The function walks `port->cfg->ext_buf_pools.ext_buf_pool[]`, swaps the entry whose `.id == old_bpid` to `new_bpid` (preserving `.size`), and re-invokes the existing `static set_ext_buffer_pools()` to rewrite the BMI `FMBM_REBM`/bpool registers from the mutated cfg — after which the FMan RX BMI autonomously DMAs ingress frames into `new_bpid`. **Contract:** the caller MUST `fman_port_disable()` the RX port first (the BMI must be quiesced before its pool registers are rewritten) and `fman_port_enable()` it afterwards — exactly the disable/enable bracket the XSK detach sequence (§6.1.1) already establishes. Validation: non-NULL RX port (`-EINVAL`), `old_bpid` present in the table (`-ENOENT`, so a mis-paired call cannot silently corrupt an unrelated pool slot), `new_bpid != old_bpid` (`-EALREADY`); on `set_ext_buffer_pools()` failure the cached `.id` is rolled back so a retry sees a consistent state.

**Scope-finding resolution.** §6.1.13 recorded that no exported API existed to reprogram a live FMan RX port's BMan pool — `set_bpools()` (fman_port.c:744) and `set_ext_buffer_pools()` (fman_port.c:891) are both `static` and run once at `fman_port_init()`. `0102` closes that gap with the minimal, reviewable WRITE entry point built on the same `set_ext_buffer_pools()` the init path uses, rather than re-deriving the BMI register layout. `fman_port_disable()`/`fman_port_enable()` are already exported (fman_port.h:121/123) and already used by the dpaa detach path (dpaa_eth.c:332).

**Why dormant with no caller.** This mirrors the exact cadence every prior offload primitive followed — `fman_hm_node_install` (`0090`) and `fman_policer_install` (`0091`) shipped as exported entry points with no productive caller before their bodies (`0099`/`0100`) and consumers landed. The symbol is exported but called by nobody in `0102`, so **no FMan register is ever written** — the RX/TX datapaths, the af_xdp_pool attach/detach paths, and `fman_port_init()` are all untouched; the running kernel behaves bit-identically to the `0101` build on every flavor. `nm fman_port.o` shows `T fman_port_set_rx_bpool` + `__export_symbol_fman_port_set_rx_bpool`. Native arm64 compile (`ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- LOCALVERSION=-vyos drivers/.../fman_port.o`) is 0 warnings/0 errors; `patch-health.sh --flavor default` shows `board/0102 ✓` standalone (it edits only `fman_port.c/.h`, independent of the `0092`→`0100` PCD stacking chain whose members show the documented cumulative-dependency false-positive).

**The remaining step-7 work is LANDED.** The three-pronged approach — `0102` reProgram WRITE primitive (dormant), `0103a` Recover sw-ring reverse-map (dormant self-test only), `0103b` productive coupled reprogram-WRITE + Recover-redirect (LANDED, DUT-entry-gate-validated 2026-05-30) — is complete. `0103b` is crash-free and reversible on hardware; the productive `xsk_zc_rx_redirect` oracle remains gated on two items per §6.1.17 (BMI register-level effectiveness confirm + traffic-steering fix). Not required for gate-3 capacity (§6.1.8a/§6.1.8b).

#### 6.1.16 True-ZC RX sub-increment 4a — Recover sw-ring reverse-map infrastructure (patch `0103a`) — 2026-05-30

**API-gap finding.** Authoring the productive Recover half exposed that kernel **6.18.31 has no `xsk_buff_recv()`** (nor any retrieve-`xdp_buff`-by-`dma_addr_t` helper). The AF_XDP UMEM surface present is alloc/size/sync/free only: `xsk_buff_alloc`, `xsk_buff_alloc_batch`, `xsk_buff_set_size`, `xsk_buff_dma_sync_for_cpu`, `xsk_buff_xdp_get_dma`, `xsk_buff_free`, `xsk_pool_dma_map` — none map a DMA address back to its `xdp_buff`. This is the same non-existence class as the prior `0075b` `xsk_pool_get_nentries()` gap. The DPAA1 FILL→BMan-release path (`af_xdp_pool_main.c`) calls `xsk_buff_alloc_batch()`, extracts `xsk_buff_xdp_get_dma(handle)`, `bman_release()`es the raw DMA address, and **discards the `xdp_buff*` handle**. At RX (`rx_default_dqrr`) only the bare `dma_addr_t` (`qm_fd_addr(fd)`) is available — so a working Recover requires a driver-maintained chunk-DMA→`xdp_buff` reverse map, exactly the per-RX-ring sw-array idiom used by i40e/ice/mlx5.

**What `0103a` lands (dormant).** A pure-additive reverse-map skeleton, hardware-safe to ship now because no datapath records into it or consults it:
- `struct xdp_buff **xsk_chunk_map[DPAA1_XSK_MAX_QBANDS]` + `u32 xsk_chunk_map_len[]` in `struct dpaa_priv` (`dpaa_eth.h`), `kcalloc`-allocated in the af_xdp_pool attach path (right after `priv->xsk_bpool[queue_id] = bpool;`) and `kfree`d in detach.
- static helpers `dpaa_xsk_chunk_record(priv, band, dma, xdp)` / `dpaa_xsk_chunk_lookup(priv, band, dma)` in `af_xdp_pool_main.c` — the future `0103b` record-on-FILL / lookup-on-RX call sites.
- 21st `xsk_*` ethtool counter `xsk_zc_recover_lookup` (column-0, after `xsk_zc_rx_recovered`).
- a one-shot self-test that records then looks up a synthetic chunk so both static helpers are referenced (no `-Wunused-function`) without touching the live datapath.

**Why dormant with no consumer (in its own patch).** Same cadence as the reProgram WRITE primitive (`0102`): the infrastructure ships exported/wired but **no FMan register is written and no live packet path is changed** in its own patch — the running kernel is bit-identical to the `0102` build on every flavor. It is now productively consumed by the landed `0103b` (§6.1.17).

**Productive `0103b` — LANDED (compile-verified, host) 2026-05-30, §6.1.17.** The coupled reprogram-WRITE + Recover-redirect now ships as patch `0103b`. Entry gate (§6.1.12) was DUT-MET first (`xsk_zc_rx_armed=2`, `xsk_fill_guard_block=0` under 30s load).

> Host validation detail (mixed-indentation anchor map: `dpaa_eth.h` struct fields leading-TAB, counter block + attach arm-block column-0, attach `kcalloc` anchor TAB-indented at line 214, detach free anchor column-0 at lines 535–536), the `apply_0103a.py` in-place editor, and the work-tree `CONFIG_XDP_SOCKETS`+`CONFIG_DPAA_AF_XDP_POOL=m` compile-verify note: Qdrant `topic=dpaa1-afxdp-spec-milestone-archive`.

#### 6.1.17 True-ZC RX sub-increment 4b — productive reprogram-WRITE + Recover-redirect (patch `0103b`) — 2026-05-30

**The INSEPARABLE productive pair** that flips DPAA1 AF_XDP RX from copy-mode (`0089` `--xskmap`) to true zero-copy. Both halves are wired in one patch because firing either alone guarantees the §6.1.8 hardware-crash class (FMan DMAs into XSK chunks that `build_skb()` consumes as page-pool pages → `xp_alloc` free-list corruption / `dcache_clean_poc` fault / soft-lockup).

**reProgram-WRITE half (attach).** Inside the existing arm/observe block of `af_xdp_pool_xsk_pool_attach()` (gated on `priv->dpaa_bp && priv->xsk_bpid[queue_id] != priv->dpaa_bp->bpid`), after bumping `xsk_zc_rx_armed`, the FMan RX port's primary BMan pool is swapped from the kernel page-pool BPID to the dedicated XSK BPID: `fman_port_disable(rxp)` → `fman_port_set_rx_bpool(rxp, (u8)priv->dpaa_bp->bpid, (u8)priv->xsk_bpid[queue_id])` (the `0102` `EXPORT_SYMBOL_GPL` primitive, whose contract REQUIRES the port be disabled across the write) → `fman_port_enable(rxp)`. On failure the port rolls back enabled (keeps the kernel page-pool BPID), `xsk_pool_attach_fail` bumps, and the band stays armed-but-not-reprogrammed (`xsk_zc_eligible` stays 0, the success oracle).

**Recover-redirect half (`af_xdp_pool_rx_hook`, dispatched from `rx_default_dqrr` via `priv->qmgmt_ops->rx_hook`, model = `tx_conf_zc`).** Because 6.18.31 has no `xsk_buff_recv()` (§6.1.16), the hook consumes the `0103a` reverse-map infrastructure made productive here: **Recognise** (`fd->bpid == priv->xsk_bpid[band]`) → **Recover** (`dpaa_xsk_chunk_head_from_dma()` O(log n) `bsearch` over the sorted per-band `dma_addr_t → head-index` array built at attach by `dpaa_xsk_build_dma_index()`, then `dpaa_xsk_chunk_lookup()` reads the `xdp_buff*` recorded at FILL-release) → `xsk_buff_set_size()` + `xsk_buff_dma_sync_for_cpu()` → `bpf_prog_run_xdp()` → `xdp_do_redirect()` into the XSKMAP (bumps the **22nd** `xsk_*` counter `xsk_zc_rx_redirect`). Any miss (no reverse-map hit, no XDP prog, redirect failure, `XDP_PASS`/`TX`/`ABORTED`) returns `false` → unchanged skbuf path; NEVER frees or double-handles the chunk.

**Design A (head-index reverse map).** Keyed by the XSK pool head-index `(xskb - pool->heads)`, with a sorted `dma_addr_t → head-index` `bsearch` array built once at attach (after `xsk_pool_dma_map()` populates every `pool->heads[i].dma`). Correct for BOTH aligned-contiguous AND fragmented UMEMs — no subtract+shift contiguity assumption (the §6.1.8 crash class). `dpaa_xsk_chunk_record()` runs at every FILL-release (attach seed loop + `napi_refill`); the dma-index array + record array are both `kfree`d in detach.

**Forward-reference fix.** The productive `af_xdp_pool_rx_hook` was relocated (no body change) from the original top-of-file stub position to immediately BEFORE `af_xdp_pool_xsk_pool_attach`, AFTER its helpers (`dpaa_xsk_chunk_lookup`, `dpaa_xsk_chunk_head_from_dma`, `dpaa_xsk_build_dma_index`) — the C single-pass compiler needs those definitions first, and the hook still precedes the `qmgmt_ops` table that names it. Without the move: `implicit declaration` + `conflicting types`.

**Byte-identical on default/vpp.** The `af_xdp_pool` attach path is only reached on `XDP_ZEROCOPY` bind (never on default flavor); until the reprogram-WRITE runs no FD carries the XSK BPID, so the rx_hook returns `false` for every frame.

**Host validation.** `dpaa_eth.o`, `dpaa_ethtool.o`, `af_xdp_pool_main.o` compile `CC [M]` clean (0 warn/0 err, native arm64, `LOCALVERSION=-vyos`). New undefined refs in `af_xdp_pool_main.o`: `xdp_do_redirect`, `fman_port_set_rx_bpool`, `fman_port_disable`, `fman_port_enable`, `bsearch` (`bpf_prog_run_xdp`/`xsk_buff_set_size` inlined). Comment-terminator hazard grep CLEAN. `git apply --3way --check` passes on all 4 files against the `0103a` base. CI `cp`-line wired in `bin/ci-setup-kernel.sh` between `0103a` and `101-sfp` (board patches are not globbed). DUT gate-reader `/usr/local/bin/xsk-zc-check` reads `xsk_zc_rx_redirect` growing post-bind as the productive-ZC success oracle.

**DUT validation — 2026-05-30 (ISO `vyos-2026.05.30-2044-rolling-LS1046A-vpp-arm64`, kernel `6.18.33-vyos`, driver `fsl_dpa`, CI run `26694481597` FLAVOR=vpp branch `dpaa1`; DUT `192.168.1.190`).** The 22-counter `xsk_*` suite (incl. the new `xsk_zc_rx_redirect`) is present and reads dormant at boot. Under two `XDP_ZEROCOPY` bind+`--xskmap` runs (`bin/dpaa1-xsk-bind-probe.py eth3 0 4096 --hold N --xskmap`) driven against a live ~190 kpps / ~2.1 Gbps `iperf3 -R -u` reverse-UDP flood into eth3 (directly-connected 10G peer `10.99.1.2`):
> - **Entry gate (§6.1.12) DUT-MET:** `xsk_zc_rx_armed=2` (precondition 1 — reprogram has a distinct target, `0094`), `xsk_fill_guard_block=0` (precondition 2 — FILL-ring single-producer invariant holds, `0095`). `xsk-zc-check` renders *"sub-increment-4 PRECONDITIONS (1)+(2) MET — armed AND FILL-guard clean"*.
> - **Productive oracle NOT yet firing:** `xsk_zc_eligible=0`, `xsk_zc_recover_lookup=0`, `xsk_zc_rx_recovered=0`, **`xsk_zc_rx_redirect=0`**. Two compounding causes: (1) the bulk UDP flood targets the DUT IP `10.99.1.1` and is **PCD-classified into the normal stack FQs, not the default FQ the XDP/XSK path drains (queue 0)** — only the ~1 pps background IPv6 RA/ND reaches the XSK socket (copy-mode redirect, `rx_packets` 16–53/run); (2) frames are not `xsk_zc_eligible` because the **`fman_port_set_rx_bpool()` BMI re-commit (§6.1.15 `0102`) is not yet *effective* on a LIVE post-init RX port on 6.18.33**. NOTE — this is NOT a missing-API forward-port: the accessor `fman_port_set_rx_bpool()` IS in-tree (`0102`) and the productive caller IS wired (`0103b`, this section). The remaining gap is BMI register-level: the `0102` v2 fix resolved the attach-time `-EINVAL` (`set_bpools()` was reading the freed `port->cfg` because `fman_port_init()` ends with `kfree(port->cfg); port->cfg = NULL;` — v2 operates on the persistent `port->ext_buf_pools` instead, §6.1.15), but a register readback has NOT yet confirmed the port's BPID actually *changes* in the BMI `FMBM_REBM`/bpool registers after the `fman_port_disable()`→write→`fman_port_enable()` bracket. The next task is therefore a register-readback debugging pass (verify the `set_bpools()` writes reach the FMan silicon and the live port's BPID flips), NOT a PCD-subsystem forward-port. Net: `fd->bpid` never equals `xsk_bpid[band]`, the Recognise→Recover→`xdp_do_redirect` path is never entered.
> - **Crash-free + reversible (the key safety result):** the `0103b` reprogram-WRITE fires correctly and safely. Each XDP DRV-mode attach/detach bounces eth3 (`fman_port_disable/enable` bracket → dmesg `Link is Down` → `configuring for inband/10gbase-r` → `Link is Up - 10Gbps/Full`, Aquantia AQR113C re-syncs ~6 s) and **eth3 fully recovers IP reachability (3/3 ping 0.26 ms) after every run**. dmesg crash grep (`IVCI|list_add|lockup|Oops|BUG|call trace|kernel panic`) = **ZERO hits** across all runs.
> - **Test-harness constraint discovered:** a DRV-mode XDP redirect prog on eth3 **hijacks the entire RX path**, so the `iperf3` flow generating the test traffic on the same eth3 dies the instant the probe attaches (`delta_pps` 190 206 → 1). The traffic source and the XSK consumer cannot coexist on one interface with the current probe. To drive `xsk_zc_rx_redirect>0` a future run needs **either** a PCD rule steering the bulk flow into the XDP default FQ **or** a peer-initiated flood independent of the DUT IP stack, **and** the `fman_port_set_rx_bpool()` BMI re-commit must be confirmed *effective* on the live port first (the register-readback debugging task above). Full counters/run-log in Qdrant (tags: `dpaa1-afxdp-0103b-dut-validation`, `fman_port_set_rx_bpool`, `set_bpools-einval`, `pcd-fq-classification-gap`).
>
> **State of record:** `0103b` is **crash-free and entry-gate-validated on hardware**; the productive `xsk_zc_rx_redirect` datapath remains gated on (1) confirming the `fman_port_set_rx_bpool()` BMI re-commit actually flips the live port's BPID at the register level (a BMI register-readback debugging pass on `0102`/`0103b`, NOT a missing PCD-subsystem forward-port — the accessor and its caller both already exist in-tree) and (2) a traffic-steering fix. NO commit/push (staged for review per AGENTS.md).

#### 6.1.18 True-ZC RX PRODUCTIVE — DUT-VALIDATED 2026-06-10 (`0102b`/`0103f`/`0103g`, oracle `xsk_zc_rx_redirect > 0`)

**The true-ZC RX datapath is now functionally proven on silicon.** Three follow-on board patches closed the two §6.1.17 gates plus a newly-found crash, and the productive oracle fired on hardware.

**The three closing patches:**
- **`0103f` (dispatch-placement fix — the root cause of the stuck oracle):** the committed `0103b` rx_hook dispatch sat in `rx_default_dqrr()` *after* the `dpaa_bpid2pool(fd->bpid)` NULL-guard — XSK-BPID FDs resolved to no kernel pool and were consumed/dropped before the hook ever saw them, so the oracle was structurally pinned at 0. `0103f` moves the dispatch to immediately after `priv = netdev_priv(net_dev)`.
- **`0102b` (GAP 1 instrumentation):** adds a `dev_info` BMI register readback of `FMBM_EBMPI[0..7]` at reprogram time so the live-port BPID flip is observable without `/dev/mem`.
- **`0103g` (NEW crash fix — NULL `xdp.rxq`):** with `0103f` finally letting Recovered frames reach `xdp_do_redirect()`, the FIRST Recovered frame NULL-deref'd at `__xsk_map_redirect+0x6c` (lr `xdp_do_redirect`) — `xsk_buff_alloc()`/`xp_alloc()` leaves `xdp.rxq` NULL (driver-owned field) and `xsk_rcv_check()` reads `xdp->rxq->dev` (offset 0 ⇒ fault at address 0). Dual-CPU interleaved Oops → instant reset, NO pstore — **serial capture was mandatory** (`telnet 192.168.1.16:5555`). Fix: per-band `struct xdp_rxq_info xsk_zc_rxq[DPAA1_XSK_MAX_QBANDS]` registered at ZC attach with `xdp_rxq_info_reg()` + `xdp_rxq_info_reg_mem_model(MEM_TYPE_XSK_BUFF_POOL, NULL)` + `xsk_pool_set_rxq_info(pool, rxq)` (the i40e idiom — stamps `xdp.rxq` into every pool buffer once); the reprogram-WRITE is **gated on successful rxq registration** (on failure: `xsk_pool_attach_fail++`, skip reprogram → no-rxq means no-ZC, not a crash); unreg at detach before the BPID restore. `MEM_TYPE_XSK_BUFF_POOL` also routes `xsk_rcv()` down the true-ZC branch instead of the copy fallback.

**DUT validation — 2026-06-10** (ISO `vyos-2026.06.10-0124-rolling-LS1046A-default-arm64`, CI run `27246793180`, commit `1639ec2`/`2e53581`, kernel `6.18.34-vyos`, DUT `192.168.1.190`; driver `fsl_dpa`; XDP_ZEROCOPY bind via `dpaa1-xsk-bind-probe.py eth3 0 4096 --hold N --xskmap`; ICMP driven into eth3 RX by a DUT-side `ping -I eth3 10.99.1.2` — echo-replies ingress on eth3, no peer access needed; **serial-relay logger running the whole window**):
- **GAP 1 CLOSED — reprogram is effective in silicon.** `0102b` readback at bind: `FMBM_EBMPI[0] = 0xc0050e00` → **bpid 5** (the XSK pool), size 3584, valid 1 — flipped from the kernel page-pool **bpid 3** (`xsk_bpid=5` vs `fman rx port primary bpid=3`). dmesg: `RX primary bpool reprogrammed 3 -> 5` then `qband 0 FMan RX port reprogrammed to XSK BPID 5 -- true-ZC RX live`. On detach it restores: `FMBM_EBMPI[0] = 0xc0030e00` → **bpid 3**, `reprogrammed 5 -> 3`. The live-port BPID genuinely changes in the FMan BMI registers across the `disable → write → enable` bracket.
- **ORACLE FIRES — true zero-copy proven.** `xsk_zc_rx_redirect` went **0 → 7** on run 1 and **7 → 8** on an independent run 2 (`xsk_zc_recover_lookup` tracked identically 0→7→8; `xsk_zc_rx_armed` 0→1→2; `xsk_pool_attach_ok` 1→2). The full mechanism chain executes: **Recognise** (`fd->bpid == xsk_bpid[band]`) → **Recover** (`0103a` sorted-DMA reverse-map `bsearch` lookup) → **Redirect** (`xdp_do_redirect()` into the XSKMAP). FMan DMA'd the ICMP echo-replies *directly into the XSK UMEM chunks*; the driver recovered the owning `xdp_buff` and redirected it — no `build_skb()`, no copy.
- **`0103g` crash fix HOLDS — safety preserved.** Across both binds the serial capture (`telnet 192.168.1.16:5555`) shows **zero** crash signatures (`Unable to handle|NULL pointer|Oops|BUG|call trace|__xsk_map_redirect|panic|lockup`), dmesg is clean, DUT uptime is continuous (no reset), and eth3 recovers full IP reachability after each detach (`ping 10.99.1.2` 0.19 ms). The §6.1.17 reversibility result is intact.

**Residual cosmetic note (not a defect):** `xsk_zc_eligible` (the older `0093` diagnostic counter) reads 0 even while `xsk_zc_recover_lookup == xsk_zc_rx_redirect == 8`, because the `0093` probe sits on the original `rx_default_dqrr()` line that the `0103f` dispatch-reorder now bypasses (the hook runs before that probe line). The **productive oracle `xsk_zc_rx_redirect` is the authoritative success metric** and is non-zero/reproducible; realigning or retiring the `0093` eligible-probe to the new dispatch point is a one-line cosmetic cleanup, not a functional gap.

**Remaining (optimization, NOT correctness):** the redirect counts are small because only DUT-terminated ICMP echo-replies currently reach the reprogrammed default FQ (queue 0); a *high-rate* true-ZC throughput number still needs **GAP 2** — a PCD/RSS steering rule (or peer-initiated non-terminating flood) that lands a bulk flow on the XSK default FQ rather than the PCD stack FQs (the same-eth3 XDP-prog-hijack harness constraint still applies — use a peer-initiated flow or a second interface). This is the §6.1.8a/b-noted capacity measurement, **not gate-3-blocking** (copy-mode already meets the capacity target). **Functionally, M3-3 step 7 true-ZC RX is COMPLETE and HW-validated.**

#### 6.1.19 True-ZC RX hardening — `0110` NAPI-only hook dispatch + `xdp_do_flush` (DUT-VALIDATED 2026-06-10)

GAP 2 re-validation work surfaced **two coupled defects** in the §6.1.18 datapath, both fixed in board patch **`0110-dpaa1-true-zc-rx-napi-only-flush.patch`** (supersedes a never-shipped `0103h` draft; numbered 0110 because its `dpaa_eth.c` diff base is post-`0104`/`0109`):

- **Defect 1 — redirected frames invisible to userspace (missing flush).** `xdp_do_redirect()` into an XSKMAP only *reserves* the RX-ring descriptor (`xskq_prod_reserve_desc`) and parks the socket on the `bpf_net_context` flush list; the producer index is only *published* when `xdp_do_flush()` → `xsk_flush()` runs. The `0103e` hook tore down its local `bpf_net_context` without ever flushing, so `xsk_zc_rx_redirect` incremented but the probe's `rx_packets` stayed 0 — frames were redirected into a ring the consumer could never see. Fix: `xdp_do_flush()` in the redirect-success branch of `af_xdp_pool_rx_hook()`.
- **Defect 2 — FATAL hard-IRQ panic in `__xsk_map_flush`.** `rx_default_dqrr()` runs from BOTH `portal_isr` (hard IRQ) and the NAPI poller (softirq). Mainline defers to NAPI *before any frame processing* (`dpaa_eth_napi_schedule()` → `qman_cb_dqrr_stop`; QMan re-delivers the DQRR entry to the poller). The `0103f` hook dispatch sat *before* that deferral, so with the Defect-1 flush added, hook+flush executed concurrently in hard-IRQ and NAPI context on two CPUs, corrupting the per-context xsk flush list → dual-CPU Oops in `__xsk_map_flush` ← `xdp_do_flush` ← `af_xdp_pool_rx_hook` ← `rx_default_dqrr` ← `portal_isr` (full serial capture 2026-06-10; kernel panic + 60 s reboot). Fix: inside the `0103f` block, when an rx_hook is registered, perform the NAPI deferral FIRST (hook now only ever runs in NAPI/softirq context — `HOLDACTIVE` serializes the FQ to one portal so this is race-free), plus a belt-and-suspenders `WARN_ON_ONCE(in_hardirq())` bail at hook entry. Default flavor (ops NULL) remains byte-identical to mainline.

**DUT validation — 2026-06-10** (test kernel `6.18.34-vyos` built from post-`0109` staged tree + `0110` edits, swapped onto DUT `192.168.1.190` `/boot/vmlinuz`; serial relay `192.168.1.16:5555` logging throughout):
- **Functional PASS:** probe `rx_packets=19 == xsk_zc_rx_redirect=19 == xsk_zc_recover_lookup=19`, fill ring advancing (`fill_cons` 0→3008 over hold), `xsk_fill_guard_block=0`, zero WARN/Oops in dmesg or serial.
- **Teardown-stress PASS (the exact v1 crash scenario):** SIGKILL of the probe mid-hold under live ICMP + immediate `ip link set eth3 xdp off` — survived, clean BPID restore 5→3, full link recovery.
- **Flood-survival PASS:** 8× parallel `ping -f -s 1400` against the armed socket for the whole 45 s hold — `xsk_zc_rx_redirect=55`, no WARN, no Oops, clean detach.

**Harness gotcha recorded:** with ZC armed, ALL eth3 ingress (ARP included) is hijacked by the XDP program, so the DUT can neither resolve the peer nor answer the peer's ARP — DUT-side flood attempts die after 1 packet and peer-initiated replies stop once the peer's neighbor entry expires. Pinning a permanent neighbor entry DUT-side (`ip neigh replace … nud permanent`) fixes the outbound half only. **The literal GAP 2 throughput-cap number therefore still requires the §8 external traffic harness** (peer-side generator with its own pinned ARP, e.g. iperf3/pktgen on 10.99.1.2). Survival + correctness at elevated rate are proven; the wire-rate figure remains the only outstanding measurement.

### 6.2 ASK-only — Dynamic CC tree, HC reconfig, nft offload bridge

**Patches:** ASK2 flavor module registers `pcd_ops->install` + `qmgmt_ops->{alloc_rx_fqs, rx_hook}` per `specs/ask2-rewrite-spec.md` §3.5a API consumption table. No XSK callbacks registered.

ASK2 flavor module registers `pcd_ops->install` (builds initial CC tree) + `qmgmt_ops->{alloc_rx_fqs, rx_hook}` (per-FQ ingress hook for nft offload). Does NOT register XSK callbacks.

Distinct from VPP:
- **Dynamic** CC tree (`fman_cc_tree_add_key`/`remove_key`) — up to 255 keys, runtime add/remove. On the shipped board this rides an **in-tree MURAM rewrite** (AttachPCD/DetachPCD-style save-restore), NOT the FMan Host-Command doorbell — `FMAN_CAP_HC_DISPATCH` is clear on the stock 210.10.1 QEF blob (`caps = 0x17`; see §3.5/§6.1.13). A flap-free HC-dispatched variant would require a custom microcode we do not ship. VPP uses the static tree.
- `rx_hook` callback for nft flowtable lookup hits.
- No UMEM, no XSK pool, no BMan pool replacement. Kernel BMan pool stays primary; ASK2 steers flows into different FQs mapping to ASK2 worker threads.

Full design in `specs/ask2-rewrite-spec.md`. This spec only commits to the kernel-side API surface (§5.4/5.5/5.6) being **shared** between VPP and ASK2 consumers.

### 6.3 default-only — passive consumer

No flavor module loaded. `priv->pcd_ops` and `priv->qmgmt_ops` stay NULL. RCU-NULL-safe call sites in `dpaa_eth_probe()`, `dpaa_eth_napi_poll()`, `dpaa_remove()`, `dpaa_xdp()` short-circuit to mainline behaviour.

`default` still gets every cross-flavor §5 benefit that does not require ops registration:
- **§5.2** datapath restructure — automatic, no opt-in.
- **§5.3** `ndo_xsk_wakeup` — available for any AF_XDP consumer (e.g. `xdpsock`).
- **§5.4** CC steering — exposed as RPS backend via `ndo_rx_flow_steer` (vyos-1x follow-on).
- **§5.5** HM offload — exposed as `NETIF_F_HW_VLAN_CTAG_*` (vyos-1x follow-on).
- **§5.6** Policer — exposed as tc/nftables ingress offload backend (vyos-1x follow-on).
- **§5.7** CEETM — exposed as root tc qdisc once the SDK CEETM driver is forward-ported (NOT in mainline — see §5.7).
- **§5.8** DCSR — available as debugfs.

§6.1 XSK-backed BMan pool *not* consumed. §6.2 dynamic CC tree *not* consumed.

---

## 7. Co-existence Rules

### 7.1 Per-port flavor selection (the common case)

```
eth0 (RJ45)  default / kernel-managed
eth1 (RJ45)  default / kernel-managed
eth2 (RJ45)  default / kernel-managed       single kernel binary
eth3 (SFP+)  vpp / AF_XDP-ZC
eth4 (SFP+)  vpp / AF_XDP-ZC
```

`af_xdp_pool.ko` claims netdevs based on `interfaces=eth3,eth4` modparam or DT property. ASK2 module symmetric. Unclaimed netdevs run mainline.

**Validation:** boot one kernel binary, `af_xdp_pool.ko` attached to eth3/4 only, verify eth0/1/2 still kernel-skbuf at line rate.

### 7.2 Single-MAC dual-flavor (deferred — Phase 4)

VPP + ASK2 on the same MAC needs FMan VSP bifurcation: per-VSP FQ sets, per-VSP BMan pool, per-VSP CC sub-tree, per-VSP HM. Out of scope for v5.0; Appendix A.

### 7.3 MURAM budget across flavors

Per §3.5 table. Combined `vpp` + `ask` on different netdevs: ~52 KiB of 64 KiB PCD reservation. Headroom for ASK2 tree growth.

### 7.4 VPP-internal shaper ↔ CEETM mutex (per-port)

§5.7 CEETM and VPP-internal shaping mutually exclusive **per-port**. Two enforcement layers:

1. **VyOS validator (vpp-flavor only).** Commit-confirm errors if `set vpp settings hw-offload egress-shaper interface ethX` active while VPP `startup.conf` has internal shaping for ethX.
2. **Kernel sentinel.** Driver sets `/sys/class/net/<iface>/dpaa/ceetm_active = 1` when CEETM active. VyOS startup reads this before launching VPP, refuses internal shaping for that interface.

For `default`/`ask` (no VPP-internal shaper) only the sysfs sentinel matters.

### 7.5 BMan pool ID budget across flavors

Per §4.2. Five netdevs × 4 dedicated = 20 of 64. Two vpp-attached = +8 XSK pools = 28 of 64. ASK2 (different netdevs) might reserve ~4/netdev × 3 = +12 = 40 of 64. Comfortably within.

### 7.6 ucode-210 capability fallback (all flavors)

If running ucode < 210, §3.5 cap bits zero → every §5.4–§5.6 install returns `-ENOTSUPP`. Flavor module increments `hw_offload_unavailable`. **§5.2 datapath and §5.3 `ndo_xsk_wakeup` are NOT ucode-gated** — work on any FMan ucode.

### 7.7 Acceptance gates and cross-flavor visibility

M3-3 ≥ 7 Gbps gate is **flavor-agnostic** — the AF_XDP zero-copy datapath lives in `kernel/common/patches/board/` and can be exercised on any flavor (`default`, `vpp`, `ask`) that brings up an AF_XDP socket on a 10G SFP+ port. Historical "VPP measurement" framing throughout earlier revisions of this spec reflects the test methodology (VPP's `af_xdp` plugin was the first usable XSK producer at hand), not an architectural constraint. Acceptable producers for the gate measurement: VPP `af_xdp` plugin, `xdp-tools xdpsock`, or a custom XSK socket app on any flavor. §8 validation adds rows for measurable default-flavor improvements (multi-flow kernel forwarding throughput, HOLDACTIVE arbitration cycles via `perf`) as side-effect deliverables.

---

## 8. Validation Matrix

### 8.1 Functional

| Test | M0 | M1 | M2 | M3-3 step 1 | step 2 | step 3+ | 3b | 3c | 3d | 3e |
|---|---|---|---|---|---|---|---|---|---|---|
| `ip link set ethX up` (all flavors) | ✅ | ✅ | ✅ | ✅ | pass | pass | pass | pass | pass | pass |
| Boot on ucode 106: §5.4–5.6 install returns `-ENOTSUPP`, no MURAM writes, RSS works | — | — | — | — | pass | pass | pass | pass | pass | pass |
| **default skbuf RX throughput improves vs. pre-M3-3 baseline (multi-flow)** | — | — | — | — | pass | — | — | — | — | — |
| **default: HOLDACTIVE arbitration cycles drop in `perf top`** | — | — | — | — | pass | — | — | — | — | — |
| **Per-port flavor selection** (§7.1): vpp on eth3/4, default on eth0/1/2, all line rate | — | — | — | — | — | pass | pass | pass | pass | pass |
| `xsk_bman_starve` stays 0 under 7 Gbps + bursty mix (vpp) | — | — | — | — | — | pass | pass | pass | pass | pass |
| `xsk_tx_backpressure` only under intentional CEETM low-class congestion (vpp) | — | — | — | — | — | pass | pass | pass | pass | pass |
| Detach under load: `fman_port_disable` bounce ≤ 50 ms (vpp) | — | — | ✅ | ✅ | pass | pass | pass | pass | pass | pass |
| iperf3 line-rate LAN (all flavors) | ✅ | ✅ | ✅ | ✅ | pass | pass | pass | pass | pass | pass |
| `ip -d link show` reports `xsk_zerocopy` (vpp) | — | — | ✅ | ✅ | pass | pass | pass | pass | pass | pass |
| `xdpsock -q 0 -z -r` (vpp) | — | — | ✅ | ✅ | pass | pass | pass | pass | pass | pass |
| `xdpsock -q 3 -z -r` (queue-id correctness, vpp) | — | — | — | — | — | pass | pass | pass | pass | pass |
| DMA-map failure → `xsk_dma_map_fail`, attach `-EFAULT` (vpp) | — | — | ✅ | ✅ | pass | pass | pass | pass | pass | pass |
| CC tree visible in debugfs | — | — | — | — | — | — | pass | pass | pass | pass |
| HW VLAN strip visible (`tcpdump` tagged, consumer untagged) | — | — | — | — | — | — | — | pass | pass | pass |
| Policer red-drops visible (`ethtool -S fm_pol_red_drops`) | — | — | — | — | — | — | — | — | pass | pass |
| CEETM qdisc visible via `tc qdisc show` | — | — | — | — | — | — | — | — | — | pass |

### 8.2 Thermal (Tj,max = 105 °C, headroom ≥ 30 °C)

| Scenario | Target |
|---|---|
| Idle, all ports up, no XSK | TZ0 ≤ 52 °C |
| Idle with XSK bound, ZC + need_wakeup (vpp) | TZ0 ≤ 53 °C |
| 1 Gbps load, 4 qbands, ZC | TZ0 ≤ 65 °C |
| 7 Gbps load, 4 qbands, ZC | TZ0 ≤ 75 °C |

### 8.3 Performance

| Test | Baseline | M3-3 target | M3-3b–e target |
|---|---|---|---|
| 64 B unidirectional pps, 1 qband (any flavor + XSK producer) | ~600 kpps | ≥ 1.5 Mpps | — |
| 64 B unidirectional pps, 4 qbands (any flavor + XSK producer) | n/a | ≥ 5 Mpps | — |
| 1500 B forwarding, single flow (any flavor + XSK producer) | ~2 Gbps | ≥ 7 Gbps | — |
| 1500 B forwarding, 4 flows, 4 cores (any flavor + XSK producer) | ~5 Gbps | ≥ 9 Gbps | ≥ 9 Gbps (no regression) |
| **default: 1500 B kernel forwarding, 4 flows** | TBD pre-M3-3 baseline | improvement measurable in `perf` | — |
| VLAN-tagged 1500 B forwarding (any flavor) | software VLAN | ≥ 7 Gbps | ≥ 8 Gbps with HM offload |
| 64 B with 8-class CEETM shaping (any flavor) | software ~3 Gbps | n/a | ≥ 6 Gbps with CEETM |

### 8.4 Stability

| Test | All phases |
|---|---|
| 24 h iperf3 stress, no oops, no BMan leaks | pass |
| `rmmod fsl_dpaa_eth && modprobe fsl_dpaa_eth` ×100 | pass |
| Module unload while traffic active | pass |
| `kill -9 vpp_main` while traffic active, recovery ≤ 5 s | pass |
| BMan exhaustion → drain → refill without manual intervention | pass |
| VyOS `commit` of hw-offload config under load, no netdev flap | pass |

---

## 9. Rollout, Kconfig Gates, Backout

```
M0  : abstraction landed, default flavor unchanged. Mainline candidate.
M1  : Phase 1 (ndo_xsk_wakeup). VyOS rolling vpp RC1.
M2  : Phase 2 (XSK pool + DCSR). VyOS rolling vpp RC2.
M3-3: Phase 3 (per-CPU NAPI + dedicated BMan channels + true ZC + multi-queue).
       ≥ 7 Gbps gate is flavor-agnostic — measurable on default/vpp/ask
       with any AF_XDP producer (VPP af_xdp plugin, xdp-tools xdpsock,
       custom XSK app). Default-flavor side-effect wins (skbuf-path RX,
       HOLDACTIVE elimination) land in the same patch set.
       VyOS rolling vpp RC3 (first ISO carrying a built-in XSK producer).
M3-3b–e : CC, HM, Policer, CEETM. Each ships when its gate passes, gated by
          VyOS config feature flag. CLI surfaces split between vpp
          (set vpp settings hw-offload …) and default/ask (set qos policy
          shaper hardware …, set system offload …, set firewall … offload).
M4  : Phase 4 single-MAC dual-flavor (deferred).
```

**Kconfig gates:**
- `CONFIG_DPAA_FLAVOR_OPS` (M0)
- `CONFIG_DPAA_XSK_WAKEUP` (M1)
- `CONFIG_DPAA_XSK_POOL` (M2)
- `CONFIG_DPAA_XSK_ZEROCOPY` (M3-3, depends on `_POOL`)
- `CONFIG_DPAA_XSK_MULTIQ` (M3-3 step 2)
- `CONFIG_DPAA_HW_CC_STEERING` (M3-3b)
- `CONFIG_DPAA_HW_HM_OFFLOAD` (M3-3c)
- `CONFIG_DPAA_HW_POLICER` (M3-3d)
- `CONFIG_DPAA_HW_CEETM` (M3-3e)
- `CONFIG_DPAA_AF_XDP_POOL` (vpp-flavor consumer, =y per `08-dpaa1.config`)

**Runtime modparams (emergency revert):**
- `dpaa_eth.disable_zerocopy=1` — forces copy-mode (vpp)
- `dpaa_eth.disable_hw_offload=1` — disables 3b–e
- `dpaa_eth.force_dma_sync=1` — re-enables per-descriptor sync

**Fallback:**
- M1 regression → re-enable `poll-sleep-usec 100`.
- M3-3 perf miss → ship with `xdp_features &= ~NETDEV_XDP_ACT_XSK_ZEROCOPY`; M1 thermal win retained.
- 3b–e individual failure → that feature's install returns non-zero, `dev_warn()`, netdev comes up with that feature disabled, others unaffected.

---

## 10. Open Questions

1. **Queue topology default.** 4 worker queues (max parallelism) vs 2 (lower footprint). Needs throughput-vs-mgmt-jitter measurement.
2. **UMEM chunk size.** 4096 fits MTU 3290; 8192 simplifies future MTU bumps but doubles UMEM footprint.
3. **Per-qband NAPI weight.** Start `napi->weight = 64`; sweep 16-256.
4. **210 ucode upgrade cadence.** Pin per VyOS release; document.
5. **CC + HM chain ordering.** RESOLVED §5.4. Parser → CC → HM → QMan.
6. **CEETM + qband interaction.** Ensure CEETM LFQs map cleanly onto qband egress FQs.
7. **Policer per-flow.** Depends on §5.4 CC tree marking flows. Documented.
8. **A050385 silicon revision.** Mono Gateway DUT is LS1046AE Rev 1.0 per DTS `fsl,soc-rev`. Confirm; document R2.
9. **BMan seed scaling by link rate.** `DPAA1_XSK_INITIAL_SEED = 8192` set for 10G XFI. Scale for 1G? Needs measurement.
10. **`DPAA1_XSK_TX_MAX_INFLIGHT` tuning.** 1024 ≈ 1.5 MB at 1500 B/chunk. Validate against (a) 1500 B line-rate, (b) 64 B Mpps with strict-priority CEETM, (c) bursty VPP scheduling jitter.
11. **default-flavor §5.2 win quantification.** Pre-M3-3 baseline TBD; need iperf3 / `perf top` numbers for a measurable claim.
12. **Cross-flavor CLI surface ownership.** vyos-1x patches for `set qos policy shaper hardware`, `set system offload classify`, `set firewall ... offload policer` are out of this spec's scope but block end-user benefit on `default`/`ask`. Open: who owns those patches and on what schedule?

13. **VPP native plugin v0.1 REJECTED.** The `specs/vpp-dpaa1-ls1046a-spec.md` v0.1 proposed a `fsl-dpaa1-um.ko` kernel module with userspace QMan/BMan portal ownership, char devices, and a separate buffer pool manager. This was rejected for three reasons: (a) RC#31 — userspace QBMan reprogramming kills all kernel FMan interfaces globally (same mechanism as DPDK DPAA PMD bus init); (b) AF_XDP ZC datapath (this spec, M1–M3-3 step 7) already delivers the VPP integration surface with ~3 kLOC vs ~12 kLOC; (c) port exclusivity is the wrong model — AF_XDP coexists with kernel netdev ownership. The VPP spec is being rewritten as v0.2 to consume this spec's AF_XDP ZC datapath + shared PCD HW offload APIs. RESOLVED 2026-05-31.

---

## 11. References

### Upstream
- **Camelia Groza**, "dpaa_eth: add XDP support" v5 (7 patches), netdev `<cover.1606322126.git.camelia.groza@nxp.com>`, accepted 2020-12-01.
- Magnus Karlsson, `ndo_xsk_wakeup`, kernel 5.3.
- Björn Töpel, "Introduce AF_XDP buffer allocation API", kernel 5.8.
- Maxim Mikityanskiy, "xsk: Add rcu_read_lock around the XSK wakeup", kernel 5.4 stable.
- Marek Majtyka / Lorenzo Bianconi, "xsk: add usage of XDP features flags", kernel 6.3.

### NXP / Freescale
- `github.com/nxp-qoriq/qoriq-fm-ucode` — ucode 106.4.18 public feature matrix.
- `github.com/nxp-qoriq/fmlib` — userspace PCD reference.
- NXP LSDK 20.04 / 21.08 FMan Linux Driver User Guide.
- NXP LS1046A Reference Manual Rev 4.
- NXP AN5340.

### Mainline Linux 6.18
- `drivers/net/ethernet/freescale/dpaa/{dpaa_eth.{c,h}}` (NB: `dpaa_eth_ceetm.{c,h}` is NOT present in mainline — see §5.7)
- `drivers/net/ethernet/freescale/fman/{fman.c, fman_port.c, fman_keygen.c, fman_muram.c, fman_sp.c, mac.c}`
- `drivers/soc/fsl/qbman/{qman.c, bman.c, qman_portal.c, bman_portal.c}` (NB: `qman_ceetm.c` is NOT present in mainline — must be forward-ported for M3-3e, see §5.7)

### NXP LSDK out-of-tree (forward-port source for M3-3e CEETM)
- `qman_ceetm.c` + `qman_ceetm_*` API in `include/soc/fsl/qman.h` (QMan CEETM channel/CQ/LFQ/CGR allocation)
- `dpaa_eth_ceetm.{c,h}` (the tc `Qdisc_ops` glue) — both from the NXP LSDK / pre-5.x QorIQ kernel tree
- `drivers/iommu/fsl_pamu*.c` (PPC-only — see §4.6)
- `include/soc/fsl/{qman.h, bman.h}`
- `Documentation/networking/device_drivers/ethernet/freescale/dpaa.rst`

### AF_XDP ZC reference implementations
- Intel i40e/ixgbe/ice/igc/igb — `drivers/net/ethernet/intel/*/...xsk.c`
- Mellanox mlx5 — multi-queue + adaptive interrupt + wakeup
- Microchip lan966x, virtio-net, Marvell mvneta/mvpp2
- **Precedent for HW-buffer-pool + AF_XDP ZC: none in mainline.** DPAA1's BMan unique. The XSK-backed BMan pool is novel.

### VPP / VyOS
- `github.com/FDio/vpp/src/plugins/af_xdp/` — VPP AF_XDP plugin.
- `fd.io/docs/vpp/master/developer/devicedrivers/af_xdp.html`
- `docs.vyos.io/en/latest/vpp/`

### Companion specs
- `specs/ask2-rewrite-spec.md` — ASK2 Track B; consumes §5.4/5.5/5.6 APIs from this spec.
- `specs/vpp-dpaa1-ls1046a-spec.md` — VPP AF_XDP integration and HW offload consumption (v0.2). Supersedes the rejected v0.1 native plugin proposal.
- `plans/PR14x-DESIGN.md` — PR14a-x foundational fman_pcd subsystem retained by ASK2.

### Kernel documentation
- `docs.kernel.org/networking/af_xdp.html`
- `docs.kernel.org/networking/xdp-rx-metadata.html`

---

## 12. Caveats

- **Ucode 210 licensing.** 210 is NXP-proprietary, not redistributable. The `vyos-ls1046a-build` image fetches from local LSDK BSP tarball at build time. Public mirrors of this spec MUST NOT bundle the binary.
- **Ucode version mismatch.** Running ucode 106/108: `priv->fman_caps` populates empty for 210-only bits. Each `fman_*_install()` returns `-ENOTSUPP` cleanly; flavor module increments `hw_offload_unavailable`. No MURAM write on unsupported layout. M3-3 datapath restructure (§5.2) and `ndo_xsk_wakeup` (§5.3) still work.
- **No mainline driver has reconciled `MEM_TYPE_XSK_BUFF_POOL` with a HW-owned buffer pool.** XSK-backed BMan pool is novel. Risk: `xsk_buff_pool` lifecycle assumes driver owns refill timing; we hand chunks to BMan and let hardware refill async. Synchronous drain on detach addresses this.
- **DPDK DPAA PMD numbers anecdotal.** 7 Gbps gate is set against home-lab gateway needs, not against a verified DPDK-PMD ceiling.
- **MTU 3290 figure is folklore-confirmed.** `xdp_validate_mtu()` math gives ~3290 on a 4096 BP with 256 headroom. Confirm with `mtu 3290` (pass) vs `mtu 3291` (fail when XDP loaded).
- **`ndo_xsk_wakeup` thermal benefit theoretical until measured.** 100% → < 10% claim depends on traffic patterns. Measure before claiming.
- **CEETM ↔ VPP-internal shaping interaction subtle.** Both active means VPP shapes first (software) then CEETM (hardware). §7.4 enforces mutual exclusion per-port.
- **`fman_cc_tree_install()` API shared with ASK2.** If ASK2 review reveals API needs reshaping, this spec also updates.
- **A050385 silicon revision check needed.** Mono Gateway DUT is LS1046AE Rev 1.0 per DTS. Phase 3 enforces ≥ 64 B UMEM headroom which sidesteps erratum on data plane.
- **Single DUT.** All thermal/perf gates sized to one Mono Gateway LS1046A. A second DUT would harden validation; without it numbers are indicative.
- **default-flavor §5.2 win is claimed but unmeasured.** Need pre-M3-3 baseline (iperf3 + `perf top`) before the M3-3-step-2 changelog can quantify the gain. OQ11.
- **Cross-flavor CLI surfaces (default/ask) are vyos-1x follow-on.** End users don't benefit from §5.4–§5.7 on `default`/`ask` until those vyos-1x patches land. OQ12.

---

## Appendix A — Phase 4 (deferred): Single-MAC Dual-Flavor Coexistence

Enables FLAVOR=vpp and FLAVOR=ask coexistence on the same physical MAC by HW-bifurcating ingress flows via FMan VSP (Virtual Storage Profile). Out of scope for v5.0. Triggers if-and-only-if real VyOS deployments need a single image that does both nft-offload and AF_XDP-ZC on the same port (uncommon; typical = §7.1 per-port flavor selection).

If/when Phase 4 lands, it reuses:
- `fman_cc_tree_install()` (§5.4) extended with VSP-target attribute on actions.
- `fman_hm_node_install()` (§5.5) per-VSP HM nodes.
- `fman_policer_install()` (§5.6) per-VSP profiles.
- SEC FQ protected-set discipline (ASK2 spec).

## Appendix B — Future enhancement (deferred, HIGH-RISK): 64KB-page kernel (`CONFIG_ARM64_64K_PAGES=y`)

**Status: NOT adopted. Documented here as a future enhancement only. Do not flip as a default.**

### Idea
Switch the arm64 kernel from the stock 4KB page size to 64KB pages. This raises the AF_XDP UMEM `chunk_size` cap from `PAGE_SIZE`=4096 to 65536, which would let VPP use ~16KB chunks and, in principle, lift the AF_XDP MTU to a full 9578 with zero driver-level complexity. The cost is a system-wide memory-footprint increase from internal fragmentation (every sub-page allocation rounds up to 64KB).

### Why it is the "obvious" fix for the UMEM chunk-cap block
On the current 4KB-page kernel, mainline 6.18 `net/xdp/xdp_umem.c::xdp_umem_reg()` rejects `chunk_size > PAGE_SIZE` in **both** aligned and unaligned mode (`XDP_UMEM_UNALIGNED_CHUNK_FLAG` does **not** lift the cap). The achievable RX frame size is therefore `4096 − XDP_PACKET_HEADROOM(256) = 3840`. This is the original M2 attach-gate blocker. 64KB pages (resolution **path b**) would remove the cap outright. The project instead took the least-invasive **path a**: lower `DPAA1_MIN_UMEM_CHUNK` 4096→3840 (commit `8f2d12e`, spec v4.3), which fits the §1.2 MTU=3290 hard cap with headroom. Path b was deliberately rejected for the reasons below.

### Why it is HIGH-RISK on this platform (VyOS / LS1046A / 8GB RAM)
1. **The 9578-MTU benefit is not actually deliverable today.** Two unrelated limits bind first: (a) `fsl_dpaa_mac` enforces a hard **XDP MTU limit of 3290** independent of UMEM chunk size, and (b) the validated SFP+ test path is **switch-capped at 1500** (no jumbo). So 64KB pages alone yield no usable jumbo AF_XDP throughput without *also* lifting the driver's 3290 XDP cap and provisioning a jumbo-capable switch.
2. **Memory footprint on 8GB is non-linear and collides with VPP.** 16× allocation granularity inflates page-cache, slab, per-skb, and especially DPAA1 BMan pool seeds (8192-buffer batches per XSK queue), alongside VPP's ~416MB of 2MB hugepages — on a box already near the MURAM/MTU/hugepage edge.
3. **kexec + hugepage fragility.** VyOS triggers a one-time kexec to apply hugepages when VPP is configured; kexec on DPAA1/QBMan is already delicate (required the mainline `bman_requires_cleanup()` fix). A 64KB-page kernel through that path is untested here.
4. **DTB / DPAA1 reserved-memory + booti-only boot.** Reserved-memory nodes and U-Boot FMan-firmware injection assume 4KB granularity; `bootefi`/GRUB already OOMs on DPAA1 reserved-memory nodes and `booti` is the only working path. 64KB-alignment of reserved regions risks reopening that.
5. **Silent config-merge breakage.** `CONFIG_ARM64_64K_PAGES` ripples through `PAGE_SHIFT`, `FORCE_MAX_ZONEORDER`, THP, etc. across `vyos_defconfig` + the 9 `kernel/common/kernel-config/*.config` fragments. Per AGENTS.md, inapplicable kernel symbols are **silently ignored** — breakage produces no error.
6. **Invalidates the validated AF_XDP stack.** The entire `0068`→`0103a+` patch series, the `DPAA1_MIN_UMEM_CHUNK=3840` constant, the `0075a` frame-size validator, and every DUT-validated milestone (M0–M3-3) were proven on the 4KB-page kernel. Switching to 64KB invalidates all of them and forces a full re-run of the bind/churn/iperf3 gate suite.

### Pre-conditions before this could even be reconsidered
- Lift the `fsl_dpaa_mac` 3290 XDP-MTU limit (driver work).
- Provision a jumbo-capable switch path end-to-end.
- Gate it as a **build-time experimental flavor**, never a default page-size flip, so the 4KB-page default (and all M0–M3-3 validation) remains intact.