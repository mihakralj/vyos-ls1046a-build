# ASK2 Offloading Capabilities — Silicon-Verified
**2026-08-05 · dpaa1 branch · Board B (.185) kernel 6.18.38-vyos · Version 2.1**

## AI READING INSTRUCTION

This document is the living inventory of silicon-verified offload capabilities for
the Mono Gateway DK LS1046A board (NXP 210.10.1 microcode). Every entry was
exercised on real hardware and the test traffic walked the FMan datapath.

> **⚠ STATUS CORRECTION (2026-08-05) — the two "shipping"/"DEAD END" [SPEC] lines below are
> SUPERSEDED.** Reality as of 2026-08-05: **no flow-classification dispatch path has a confirmed
> hardware HIT on this branch.** (1) FE-VM ehash is UN-RETIRED (F-163, commit `f212c701`): the
> deployed vendor `cdx.ko`'s production classification **is** external-hash; this branch's key
> builder was fixed to the vendor's 14-byte PORT_ID-prefixed `union dpa_key` (`EKFC 0x801C0006`).
> (2) "CC-tree shipping" was never implemented in `ask.ko` (CR-007, `dd364494`) and its `cc_test`
> harness is architecturally broken (F-159–F-162: five vendor-verified register fixes, RX-silent
> within 17–30 frames vs `.106` vendor stack's 400+; `cc_test` to be retired).
> (3) The F-163 live test was byte-correct but MISSed because the engage path never pointed the
> port at the built chain (F-165, commit `e4f23948`) — the corrected chain has never been genuinely
> exercised; retest = `plans/ASK2-MASTER-PLAN.md` T-M3-R. (4) M5's 10.259 Gbps is under
> mechanism-retraction review (most likely kernel `nf_flowtable`, tag `no-confirmed-hw-hit-ever`).
> §4's "RETIRED" column entries below should be read as "built and byte-verified, never dispatched
> a confirmed HIT, un-retired and under re-validation."

**[SPEC — 2026-08-01, SUPERSEDED above]** ~~Shipping HW-offload = CC-tree classification (top-N) + kernel SW
flowtable (tail) + manip-chain forwarding.~~ (Intended architecture; never implemented in `ask.ko`.)

**[SPEC — 2026-08-01, SUPERSEDED above]** ~~FE-VM ehash HIT path (Fork-B) = DEAD END / never worked (~1.5 Gbps
DDR ceiling), experimental/retired, NOT shipping.~~ (Un-retired F-163; ceiling unmeasured; no confirmed HIT but never genuinely tested — F-165.)

**[SPEC]** CC comparator reads KG-emitted bytes (patch 0108); old 0098 "could
NEVER match". EKFC MSB-first (SIP,DIP,PROTO,SPORT,DPORT). *(2026-08-05: F-161 realigned
`cc_pack_key()` to the board-confirmed live EKFC `0x00180006`; the F-163 ehash key is a different,
14-byte PORT_ID-prefixed format on `EKFC 0x801C0006` — do not conflate the two key builders.)*

**[SPEC]** CC-tree scales: 32-key software caps vs 255 HW keys/node, 64KiB MURAM
→ ~8 nodes → ~2000+ flows; long tail in kernel nf_flowtable. *(Arithmetic stands; mechanism has no
confirmed HIT and no wired insert path.)*

**[SPEC]** M3/M5 HIT "PASSED" were false positives (FQID 0x200 ambiguity). M2
7.37 Gbps real pass-through. Only real HIT: RCCB→FE_ENTER direct (2026-07-04).

---

## 1. Silicon Substrate

| Capability | Gate | Silicon Evidence |
|---|---|---|
| Proprietary 210.10.1 ucode loaded from SPI flash | `0117` IRAM_READY handshake | dmesg: "FM_CTL microcode 210.10.1 loaded (12851 words)" |
| FMan PCD subsystem initialized | `0092` PCD bring-up | dmesg: "fman_pcd: ready (64 KiB MURAM reserved at offset 0x4ac00)" |
| Gen-pool MURAM sub-allocator (64 KiB, 256 B granules) | `0126` | `muram_budget` debugfs: reserved=65536, used/free/high-water accurate to byte |
| Two 10G SFP+ ports (eth3/eth4) with DAC | `4003` SFP rollball EINVAL fallback | `ip link`: UP, LOWER_UP, 10000 Mbps full-duplex, inband/10gbase-r |
| Marvell 88X3310 PHY driver bound on SFP+ | `managed = "in-band-status"` | `ethtool`: Speed 10000Mb/s, Link detected yes |

---

## 2. FMan KeyGen — Flow Classification Steering

| Capability | Gate | Silicon Evidence |
|---|---|---|
| RSS KeyGen schemes 0-4 active at boot (mainline) | — | `pcd-snapshot`: 5 EN schemes, nia=0x02 (RSS), distinct FQBs |
| Scheme 3 switched RSS→AC_CC on engage | `0106`/`0129` KGSE graft | `pcd-snapshot`: scheme[3] CC(AC_CC) fqb=0x00000200 |
| Scheme 3 restored AC_CC→RSS on disengage | `0106` detach | `pcd-snapshot`: back to nia=0x02 |
| AC_CC dispatch confirmed on silicon | `0107` cc_test harness | dmesg: "FMBM_RCCB bound to 0x4ac00, KG CC-dispatched" |
| 100× S0↔S1 mode-switch soak (reversible) | M1 gate | `pcd-snapshot diff`: register state byte-identical to warm-S0' baseline |
| CC comparator reads KG-emitted bytes (MSB-first EKFC) | `0108` | Comparator matches SIP,DIP,PROTO,SPORT,DPORT in silicon order |
| EKFC extraction order confirmed MSB-first | 2026-07-13 HW measurement | CRC-64 raw match on two independent TCP flows on eth4 |

---

## 3. CC-Tree Classification — Intended HW Offload Path (not wired; harness broken)

**[SPEC — updated 2026-08-05]** This was headed "Shipping HW Offload Path." Reality: CC-tree
classification (top-N flows) + kernel nf_flowtable (tail) + manip-chain forwarding is the
*intended* architecture — it is **not implemented in `ask.ko`** (CR-007 deleted the insert path)
and its `cc_test` hardware harness is **architecturally broken** (F-159–F-162: five vendor-verified
register fixes, every install RX-silent within 17–30 frames, reboot-required; `.106` vendor stack
classified 400+ frames in the same session). `cc_test` is to be retired; replacement informed by
`plans/NXP-106-DEEP-DIVE-PLAN.md` Phase A/C.

| Capability | Gate | Silicon Evidence |
|---|---|---|
| CC pass-through (M2 gate) | `0098`/`0108` | 7.37 Gbps @ 0.16% CPU — real pass-through, no false positive (MISS→kernel; **not offload**) |
| "CC-tree + nf_flowtable" (M5 gate) | `0108`/`0116` | 10.259 Gbps @ 0.16% CPU — ⚠ mechanism unresolved (most likely kernel `nf_flowtable` SW) |
| NXP cdx.ko manip-chain forwarding | — | 8.58 Gbps via opcode/manip chain (driven by external-hash classification per F-163) |
| CC tree static install (group+match+AD tables) | `0098` | `cc_test` readback: "port 0x10: 1 keys, group=0x4ac00 match=0x4ad00 ad=0x4ae00" — ⚠ byte-correct but RX-silent (F-159–F-162) |
| CC-tree scale: 255 HW keys/node, 64KiB MURAM | `0126` | ~8 nodes → ~2000+ flows in HW; long tail in kernel nf_flowtable (arithmetic; no confirmed HIT) |
| FM_CTL params page (256 B, FMBM_RGPR, errdisc) | `0116` | dmesg: "FM_CTL params page at MURAM off 0x4af00 (errdisc 0x012ee0e8)" |
| Per-port FE support — FE internal-buffer pool + mgmt free-list | M2-§4 | `fe_port`: "port 0x10 pool 0x4b000/8448 B mgmt 0x4ac00/21 B" |
| Params page +0x54/+0x58 FE management words | M2-§4 | `iowrite32be` to `page_v + 0x54` (mgmt index), `+ 0x58` (depl cnt=0) |
| MURAM zero-before-use (memset_io for gen_pool allocs) | F-040 | `memset_io` on gro (256 B) + ato (32 B); verified by CI + no KASAN faults |

---

## 4. FE-VM ehash HIT Path — UN-RETIRED 2026-08-05 (F-163), under re-validation

**[SPEC — updated 2026-08-05]** Fork-B FE-VM ehash was called a DEAD END here until F-163
established it is the vendor's real production classification mechanism (deployed `cdx.ko`,
`ExternalHashTableAddKey()`). The "~1.5 Gbps DDR ceiling" is an unmeasured theoretical bound.
All entries below are **built and byte-verified on silicon, but none has ever dispatched a
confirmed HIT** — and the F-165 finding (engage-path scaffold overwrite) means the corrected
chain has never been genuinely exercised. The only real HIT was RCCB→FE_ENTER direct (2026-07-04).
M3/M5 HIT "PASSED" were false positives (FQID 0x200 ambiguity). Retest: T-M3-R.

| Capability | Gate | Silicon Evidence | Status |
|---|---|---|---|
| FE pool (16 × 28 B) MURAM allocation | `0122`/`0124` | `fe_pool` debugfs: get/put cycles, gen_pool used returns to baseline | BUILT, no HIT |
| FE singletons (MUX 8 B, TRANSITION 8 B, EXIT 4 B) | `0124` byte-assembled | `fe_singletons` debugfs readback: MUX=0x04000000, EXIT=0x03800000 | BUILT, no HIT |
| `t_ExtHashFe` (28 B, DDR table addr, MUX/MISS links) | `0131` | `fe_hashfe` readback: 7 words match oracle §5 byte table | BUILT, no HIT |
| FE_ENTER root AD (16 B, pcAndOffsets=0xF6, NIA_ORDER_RESTOR) | `0127` | `fe_enter` readback: 40800000 00000000 000000f6 00000010 | BUILT, no HIT |
| ENQ FE (16 B, NIA+FQID 0x2b9 = dedicated TX FQ) | `0127`/P4.1 | `fe_enq` readback: 02010000 000002b9 00000000 00000000 | BUILT, no HIT |
| DDR ehash table (mask=0x7FFF, 524 KB, DMA-coherent) | `0125`/`0130` | dmesg: "ehash table mask 0x7fff keysize 13 ii 15 size 524288 DDR=0xf7780000" | BUILT, no HIT |
| CRC64 flow insertion into DDR buckets | `0128` | `fe_flow` readback: bucket=0x273d rec=0xfa403000, key=`<hex>` verified | BUILT, no HIT |
| FE-VM arm engages (BMI CC root → FE_ENTER) | `0132` D9-B | `fe_arm`: "fe_pool engaged: YES, FE_ENTER root AD: 0x59200" | BUILT, no HIT |
| Port survives sustained FE-VM traffic | M3-3b fix | 50+ pings, zero STL stall, no fault latched | BUILT, no HIT |
| EXIT singleton deallocateBuffer frees BMI FIFO | M2 gate | `FMFP_PS[STL]` never set; `fmdmsr=0`; all FMan fault registers clean | BUILT, no HIT |
| MURAM returns to 0 bytes on disengage | F-002 fix | 3 engage/disengage cycles: used returns to 0 (±0 B) | BUILT, no HIT |
| 14-byte PORT_ID-prefixed key (vendor `union dpa_key` format) | F-163 (2026-08-05) | `hash_fe` contextSize-1=0x0d; `EKFC 0x801C0006` live on scheme 4; key bytes exact | BUILT, byte-correct, MISS explained by F-165 |
| Explicit-target engage honored (no scaffold overwrite) | F-165 (2026-08-05) | `fmbm_rccb` live read = caller's FE_ENTER offset (was scaffold `gro` pre-fix) | FIXED, retest pending |
| RCCB→FE_ENTER direct HIT (only real HIT) | 2026-07-04 | Single flow matched, no DDR ehash involved | EXPERIMENTAL |

---

## 5. PCD Infrastructure — Policing, Scheduling

| Capability | Gate | Silicon Evidence |
|---|---|---|
| HW ingress policer (tc matchall → FMan PLCR) | `0097`/`0100`/`0104` | `tc -s filter`: `in_hw` flag; `FMPL_GCR`=0xC0500002 (EN\|STEN); TPC increments |
| PLCR block enable (master EN+STEN) on first profile commit | `0100` plcr_enable_block() | Live `/dev/mem` RMW: GCR 0x00500002→0xC0500002, policed ping 100%→0% loss |
| Policer attach/detach reversible (no scheme leak) | `0104` release callback | delete→re-apply: filter empties, ping 5/5 0% loss, eth3 alive |

---

## 6. Kernel Datapath — AF_XDP, tc Offload, DPAA1

| Capability | Gate | Silicon Evidence |
|---|---|---|
| Dual 10G iperf3 (DAC, 4-stream) | — | eth3: 7.63 Gbps, eth4: 6.48 Gbps, zero retransmissions |
| DPAA1 AF_XDP true-ZC eligibility gates | `0070`-`0114` | `xsk-zc-check`: sub-increment-4 entry verdict reachable (spec §6.1.12/13) |
| XDP queue_index fixed (FQID→0 for XSKMAP) | `patch-dpaa-xdp-queue-index.py` | XDP_REDIRECT resolves to queue 0 |
| tc HW offload (NETIF_F_HW_TC default-off, toggleable) | `0104a` | `ethtool -k`: hw-tc-offload on [fixed], toggleable after VLAN-strip patch |
| VLAN HW strip offload (RX VLAN extraction) | `0101` | `ethtool -k`: rx-vlan-hw-parse on |
| DPAA1 `fsl_dpaa_fman.fsl_fm_max_frm=9600` (jumbo) | `0104b` | MTU 9578 (RJ45), 3290 (SFP+ XDP max) |
| SCH_FQ qdisc built-in (not module) | config fix | `sysctl net.core.default_qdisc=fq` no ENOENT at boot |

---

## 7. Platform Integration

| Capability | Gate | Silicon Evidence |
|---|---|---|
| Fan PID controller (multi-zone PI + max-policy) | `fan-pid` daemon | `fan-check`: all 5 zones nominal, PWM ~51, RPM ~1700, no thermal-protection shutdown |
| EMC2305 PWM via /dev/i2c (kernel sysfs bug bypassed) | `fan-pid` | Direct I2C_SLAVE_FORCE write to register 0x30 → fan RPM tracks PWM linearly |
| LP5812 RGBW status LED (palette + fade) | `led.py` | `led 17` → LED color transitions with 200 ms linear fade |
| CAAM SEC 5.4 hardware crypto (Job Rings, RNG) | `caam-check` | `/proc/crypto`: caamalg, caamhash, caamrng; `/dev/hwrng` = caam-rng |
| INA234 power sensors (8× via pca9545 I2C mux) | `4002` | `hwmon` devices bind: `ti,ina234` of_match (Kconfig: `CONFIG_SENSORS_INA2XX=y`) |
| U-Boot env via `fw_setenv` (`/dev/mtd2`, 0x2000, 0x1000) | `fw_env.config` | `fw_printenv`: all vars readable/writable; `vyos`/`usb_vyos`/`bootcmd` block correct |
| QSPI NOR flash (64 MB, 9 partitions) | `CONFIG_SPI_FSL_QUADSPI=y` | `/proc/mtd`: 9 devices; `mtd2` = uboot-env (1 MB) |
| IMX2 WDT hardware watchdog | `CONFIG_IMX2_WDT=y` | `/sys/class/watchdog/watchdog0` present at boot |
| Kernel kexec (mainline 6.6+ QBMan fix) | `CONFIG_KEXEC=y` | `systemctl kexec`: board reboots into new kernel; managed-params self-healing works |
| CAAM Job Rings available for ASK2 CAAM-QI share | `0134` | `caam-check` §7: CDX↔SEC FQ wiring health probe (dormant until ASK engage) |

---

## 8. Build & Deployment Pipeline

| Capability | Gate | Silicon Evidence |
|---|---|---|
| Single ISO build (no flavor — kernel + VPP + dormant ASK in one image) | ci-setup | `vyos-<version>-LS1046A-arm64.iso` produced, deployed to lxc200 |
| Hybrid ISO (isohybrid: ISO9660 + MBR + FAT32) | `ci-build-iso.sh` | `dd` to USB → U-Boot `fatload usb 0:2` boots live session |
| `add system image <url>` → eMMC install | `vyos-postinstall` + `grub.py` | `/boot/vyos.env` written; U-Boot `vyos` variable boots eMMC kernel |
| Post-patch Python fixers (base64-encoded, zero escape collision) | ci-setup-kernel.sh | 5 CI iterations converged; AGENTS.md rule documented |
| GitHub Actions CI (self-hosted Azure Cobalt 100 ARM64 VM) | `self-hosted-build.yml` | ~7 min warm-cache build; ISO artifact deployed via rsync to lxc200 |
| ISO published to lxc200 HTTP relay | `rsync` → `/srv/tftp/iso/latest.iso` | Board `add system image http://192.168.1.137:8080/iso/latest.iso` |

---

## 9. Pending — Designed, Not Yet Silicon-Gated

| Capability | Blocked On |
|---|---|
| Full per-port `FmPortSetFESupport` (FE buffer pool wired to ucode) | `fman_port_lookup_rx` port_id mismatch (fixed — needs CI deploy + verify) |
| ASK2 `ask.ko` full datapath engage (xfrm/flow offload) | M3 gate + `flow_block_offload` wiring |
| VPP AF_XDP overlay on S0 | S2 switch (S0→S2 tested; full VPP+ASK mutual exclusion pending) |
| Per-flow statistics (byte/frame counters in DDR) | M3 forwarding verified first |
| HM (Header Manipulation) FE for NAT/fragmentation | M3 substrate complete, HM patch authored (0120) — dormant |
| ASK2 userspace daemon (YNL family + flow promotion) | Dependent on ask.ko readiness |

---

## 10. Key Test Results

| Test | Date | Result |
|---|---|---|
| 100× S0↔S1 soak (pcd-snapshot clean) | 2026-06-15 | PASS — 0 diffs, MURAM used=0 |
| M3-3b fault-capture (iter-50) | 2026-06-16 | All FMan fault regs clean — Fork A dead, Fork B confirmed |
| CC pass-through (M2 gate) | 2026-07-04 | PASS — 7.37 Gbps @ 0.16% CPU, real pass-through |
| "CC-tree + nf_flowtable" (M5 gate) | 2026-07-09 | PASS — 10.259 Gbps @ 0.16% CPU ⚠ mechanism unresolved (most likely kernel `nf_flowtable` SW; no HW classification confirmed) |
| NXP cdx.ko manip-chain forwarding | 2026-07-09 | PASS — 8.58 Gbps via opcode/manip chain |
| RCCB→FE_ENTER direct HIT (only real HIT) | 2026-07-04 | PASS — single flow matched, no DDR ehash |
| FE-VM EXIT singleton no-stall (50+ pings) | 2026-07-09 | PASS — 0% loss, no STL, no fault (built, no HIT — see §4) |
| F-002 MURAM leak fixed (3 engage/disengage cycles) | 2026-07-09 | PASS — used returns to 0 B (±0) (built, no HIT — see §4) |
| F-040 memset_io zeroing (gen_pool) | 2026-07-09 | PASS — CI build #28981979429, HW-verified |
| Dual-DAC iperf3 (4-stream, 10 s) | 2026-07-09 | eth3: 7.63 Gbps, eth4: 6.54 Gbps, 0 retrans |
| PLCR HW policer (100%→0% loss) | 2026-06-09 | PASS — FMPL_GCR EN\|STEN, TPC increments |
| Policer → delete → re-apply (no scheme leak) | 2026-06-09 | PASS — ping 5/5 0% loss, eth3 alive |
| EKFC extraction order MSB-first confirmed | 2026-07-13 | PASS — CRC-64 raw match on two independent TCP flows |
| M3/M5 HIT false positives identified | 2026-07-09 | FQID 0x200 ambiguity — (2026-08-05: neither CC-tree nor ehash has a confirmed HIT; see top banner) |