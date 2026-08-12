# ASK2 Production Architecture & Course-Correction Plan

**Version 1.0 · 2026-08-11 · HADS 1.0.0**

## AI READING INSTRUCTION

**[SPEC]** = binding fact/requirement/contract. **[NOTE]** = rationale/history.
**[BUG]** = symptom+cause+fix. **[?]** = unverified. This plan is the
course-correction from the debugfs-driven bring-up architecture to a
production architecture: mainline-Linux control plane, NXP DPAA1 silicon
(FMan/BMan/QMan/SEC) used to its documented capability, **no debugfs in
production images**. It is grounded in three 2026-08-11 qdrant discoveries
(vendor `.106` arming/offload process; 999-patch HIT/PASS encoding decode;
ASK2↔vendor difference inventory) and the live `plans/ASK2-MASTER-PLAN.md`
status. Read those first if a claim here is unclear.

---

## 1. Why this plan exists

**[NOTE]** ASK2's data path is currently driven through **debugfs**
(`fe_arm`, `fe_flow`, `fe_pool`, `fe_ehash`, `fe_hash`, `fe_enq`,
`fe_enter`, `hash_probe`, `muram_budget` in the `fman_pcd` kernel patches;
plus `ask`'s own `offload` file). That was correct for silicon bring-up but
is wrong for production: debugfs is not a stable ABI, is not
permission/audit-gated as configuration, and VyOS config must never depend
on it. The vendor stack proves the correct shape — a daemon
(`cmm`) + char-device ioctl (`/dev/cdx_ctrl`) + a binary protocol (FCI),
with the PCD built once at boot by `dpa_app`. We do **not** clone that
daemon; we use mainline equivalents.

**[SPEC]** Hard constraints for the target architecture:
1. Mainline Linux control plane only (genl/YNL + netfilter flowtable).
2. NXP DPAA1 silicon does the data-plane work (FMan PCD classify, KeyGen,
   FE-VM ehash, BMan buffers, QMan FQs; SEC deferred to M6).
3. **debugfs is compiled out of production images** (`CONFIG_ASK_DEBUG_FS`
   and the `fman_pcd` `fe_*` surface both gated; present only in dev builds).
4. Reversibility is a hard gate (`pcd-snapshot` byte-exact diff), not "ping
   works".

---

## 2. Current state (honest baseline)

**[SPEC]** From `plans/ASK2-MASTER-PLAN.md` (do not re-derive):

| Gate | Status | Meaning |
|---|---|---|
| M2 | **DONE** | CONT_LOOKUP pass-through, MISS→kernel FQ steering, 7.37 Gbps @ 0.16% CPU. KG classification selects FQID; decisions stay in software. |
| M3 | **OPEN** | FE-VM ehash per-packet HIT gate. **Zero HIT across every hypothesis** (attempts 1–4). Attempt 5 = suspected wrong AD species at `FMBM_RCCB`. |
| M4 | BLOCKED | AF_XDP true-ZC RX. |
| M5 | DONE-throughput / **mechanism-unresolved** | 10.259 Gbps line-rate @ 0.16% CPU via SW flowtable + CC-tree (≤32 flows) + manip chain. **A throughput result, NOT ehash classification proof.** |
| M6 | UNBLOCKED | IPv6/bridge/IPsec. |
| M7 | DONE-surface | CLI wired; release claim gated by CR-001. |
| M8 | DONE | Soak, 87+ cycles. |

**[SPEC]** Control-plane reality today:
- `kernel/ask/uapi/ask.yaml` already defines the full genl surface:
  `get-info`, `get-muram`, `dump-flows`, `get-flow`, `dump-sas`,
  `flush-flows`, `flush-sas`, `set-policer`, `engage`, `disengage`
  (mcast groups `events`/`flows`/`sas`).
- `ask_genl.c` implements `ENGAGE`/`DISENGAGE`/`GET_INFO`/`DUMP_FLOWS`/
  `GET_FLOW`/`FLUSH_FLOWS`; `GET_MURAM`/`DUMP_SAS`/`FLUSH_SAS`/
  `SET_POLICER` are `-EOPNOTSUPP` stubs.
- The production engage path is **not** yet the genl path end-to-end; the
  working engage is via `fman_pcd` debugfs. That inversion is the primary
  course-correction.

---

## 3. Target architecture (detailed)

### 3.0 Silicon contract — use the NXP blocks as documented

**[SPEC]** FMan microcode 210.10.1, ASK package gate `ASK_UCODE_PACKAGE_NUMBER
= 209` (`IS_OFFLOAD_PACKAGE(num) = num==106 || num==108 || num>=209`). Our
board ships 210.10.1 → ASK-capable. This is the same firmware the vendor
`.106` reports (`cdx: FMAN firmware 210.10.1 - ASK supported`).

**[SPEC]** QMan FQID layout (decoded from `.106` `cdx_pcd.xml` + live
`/proc/fqid_stats`, verified): `FQID = (portid << 8) | dist_base`.
- Port IDs: eth0=1, eth1=4, eth2=5, eth3=6, eth4=7, oh1=9 (IPsec),
  oh2=10 (WiFi).
- Per-port RX: 11 distribution FQs at `(portid<<8)|{0x1000,0x1010,…,0x16B0}`
  + 128 ethernet FQs at `(portid<<8)|0x10000` (e.g. eth3 `0x1600..0x16B0` +
  `0x10600..0x1067F`) = 139 FQs.
- TX: 16 FQs/port, base `0x819B + port*0x10`, channel `0x801` (DC portal),
  Wq 3.
- RX distribution FQs → channel 9 (FMan Rx), Wq 5, "Prefer in cache".

**[SPEC]** KeyGen: 14-byte portid-prefixed key, EKFC `0x801C0006`,
`PORT_ID|SIP|DIP|PROTO|SPORT|DPORT`, PORT_ID=`0x00` for eth4/port `0x11`.
HW-confirmed via CRC-64 ×3. KG hash = fixed silicon CRC-64 (ECMA-182,
reflected poly `0xC96C5795D7870F42`), raw (no final complement). This is
**closed** — do not reopen.

### 3.1 Kernel data plane (ask.ko + fman_pcd patches)

**[SPEC]** PCD arming sequence (the vendor `dpa_app` order, ported to
mainline `fman_pcd`):
1. `FM_PCD_Open(USE_ENHANCED_EHASH)` equivalent — pre-allocate the global
   internal-buffer pool, global_mem area, ext-ts timers, and the singleton
   MUX/Transition/Exit FEs.
2. `FM_PCD_SetAdvancedOffloadSupport` — **set params-page misc bit
   `0x40000000` (`OFFLOAD_SUPPORT_EN`)**. Currently never set in ASK2
   (misc=`0x100` ALWAYS_ON only). **This is delta #3.**
3. `FmPortSetFESupport` (F-072) — internal FE buffer POOL + management INDEX
   in MURAM at params page `+0x54`/`+0x58`. Port-deafness root cause when
   absent.
4. KG schemes — `FmPcdKgSchemeSet` from the distribution set; bind per-port
   via `FmPcdKgBindPortToSchemes` (scheme-per-port vector), **not** a bare
   scheme AC_CC graft.
5. CC-tree root — **the ehash node is written directly into the CC-tree root
   AD slot** (`copy_td_to_ccbase()`), i.e. `FMBM_RCCB` = CC group-tree root
   whose leaf is the `en_exthash_node`. **Not** a bare `FE_ENTER` AD as root
   (that wedges the port — E-HM9/E-HM14). **This is delta #1.**
6. `RFPNE = 0x00480200` (`NIA_ENG_KG | NIA_KG_CC_EN`) — vendor value; ours is
   `0x00480304` (KG_DIRECT scheme 4). Reconcile to the vendor form.

**[SPEC]** ehash HIT encoding (the 999-patch decode — delta #2):
- Table AD = `en_exthash_node` (16 B CC-tree leaf):
  `w0 = table_base_hi:16 | hash_bytes_offset:2 | reserved:6 | key_size:6 |
  miss_action_type:2`; `w1 = table_base_lo` (40-bit DDR table phys);
  `w2 = global_mem_offset:12 | hash_mask_bits:4 | int_buf_pool_addr:16`;
  `w3 = nia` (or `fqid` for ENQUE).
- DDR flow record = `en_ehash_entry` (256 B; stats extend to 320 B):
  `[0..7]` chain header (flags:16 + next_entry 64-bit); `[8..]` key (offset
  8); `[8+key]` **16 B `t_ExtHashResult`**; `[256…]` stats block
  (`packet_count` u64, `packet_bytes` u64, `timestamp` u32, reserved,
  `timestamp_counter`).
- **`t_ExtHashResult`** = `contextAddr` (phys addr of the **per-flow MUX FE
  chain**) + `monitorAddr` (stats/aging counters).
- Per-key add: `BuildFEChainAndContextFromNextEngine` →
  `FmPcdCcBuildContextByFE(MUX)` → `ExternalHashBuildResult` →
  `ext_hash_add_key` writes **key + 16 B result** into the bucket.
- HIT walk: hash → bucket → compare key@8 → read `t_ExtHashResult` →
  dispatch to `contextAddr` MUX chain → update monitor (stats).
- MISS: `miss_action_type ∈ {DONE=0, NIA=1, ENQUE=2, DROP=3}`; MISS→KG =
  `NIA_ENG_KG | NIA_KG_DIRECT | scheme_id | NIA_KG_CC_EN`.

**[BUG] ASK2 record encoding is structurally wrong (the M3 gap).**
Symptom: zero ehash HIT across attempts 1–4. Cause: `0128` writes a single
u32 ENQ FE MURAM offset after the key (`fe_ptr_off = 8 + align8(keysize)`);
the vendor writes the 16 B `t_ExtHashResult` (context + monitor) and builds
a per-flow MUX chain. Fix: port the per-flow context build + result write
(§4 Phase 2).

**[SPEC]** Per-flow opcodes available in the FE chain (for HM on HIT):
`ENQUEUE_PKT 0x01`, `REPLICATE_PKT 0x02`, `ENQUEUE_ONLY 0x03`,
`UPDATE_ETH_RX_STATS 0x04`, `STRIP_ETH_HDR 0x11`, `STRIP_ALL_VLAN_HDRS 0x12`,
`UPDATE_TTL 0x21`, `UPDATE_SIP_V4 0x22`, `UPDATE_DIP_V4 0x24`,
`UPDATE_SPORT 0x31`, `UPDATE_DPORT 0x32`, `INSERT_L2_HDR 0x41`, …,
`UPDATE_GLOBAL_STATS 0x80`.

### 3.2 Flow learning — mainline netfilter flowtable (not a cmm clone)

**[SPEC]** Use `nft flowtable … flags offload` → `flow_indr_dev_register` →
`TC_SETUP_FT` in `ask_flow_offload.c`. Vendor `cmm` learns from conntrack via
nfnetlink and pushes `CMD_IPV4_SOCK_OPEN/UPDATE/CLOSE` over `/dev/cdx_ctrl`;
the mainline equivalent is the flowtable offload path.

**[BUG] Per-flow REPLACE never reaches ask (2026-05-17).** Symptom: flows
bind but per-flow HW programming never fires. Cause: binder type mismatch —
flowtable (FT) vs CLSACT_INGRESS. Fix: register for the FT offload type and
handle `FLOW_CLS_REPLACE` in the FT path. This is the flow-learning
course-correction (§4 Phase 3).

**[NOTE]** Only **forwarded transit** flows are offload candidates — INPUT
flows to the box are not (vendor `.106` confirms: iperf/SSH to the box get
`flow NOT found` / `conntrack not allowed`). ASK2 inherits this; the VyOS
fast-path use case is transit routing, so this is correct.

### 3.3 Control plane — genl/YNL only, no debugfs in production

**[SPEC]** The `ask` genl family (`ask.yaml` UAPI) is the **sole** production
control surface. Required commands and their status:

| Command | Purpose | Today | Target |
|---|---|---|---|
| `engage` (port-id) | arm port PCD + ehash | implemented, but real work is in debugfs | **sole engage path** |
| `disengage` (port-id) | reversible teardown | implemented | sole disengage path |
| `dump-flows` / `get-flow` | flow table read | implemented | keep |
| `flush-flows` | clear flows | implemented | keep |
| `get-info` | ucode version + caps | implemented | keep |
| `get-muram` | MURAM budget | stub | implement (replaces `muram_budget` debugfs) |
| `set-policer` | per-port policer | stub | implement |
| `dump-sas`/`flush-sas` | IPsec (M6) | stub | M6 |

**[SPEC]** debugfs gating:
- `ask.ko`: wrap `ask_debugfs.c` (`offload` file) in `#ifdef
  CONFIG_ASK_DEBUG_FS`.
- `fman_pcd` patches: wrap the entire `fe_*` debugfs surface
  (`fe_arm`/`fe_flow`/`fe_pool`/`fe_ehash`/`fe_hash`/`fe_enq`/`fe_enter`/
  `hash_probe`/`muram_budget`) in `#ifdef CONFIG_FMAN_PCD_DEBUG_FS`.
- Production kernel config: both **off**. Dev/bring-up kernel config: on.
- The genl `engage` path must be able to do everything the `fe_arm` debugfs
  path does today, or production cannot arm. This is the acceptance test for
  Phase 1.

### 3.4 VyOS integration

**[SPEC]** `set interfaces ethernet ethX offload ask` → VyOS configd → genl
`engage`/`disengage`. Per-interface ASK↔VPP mutex stays (one port can't be
both). `set system offload classify` remains deprecated (RSS+parser silent
defaults; ASK is the sole offload switch).

**[SPEC]** Reversibility gate: every engage/disengage cycle must pass
`pcd-snapshot capture`/`diff` byte-exact against the warm-S0 baseline.
`pcd-snapshot` mutates eth3 only — never eth0 (SSH lifeline).

---

## 4. Course-correcting plan (phases)

**[NOTE]** Ordered so each phase is independently verifiable and reversible.
Phases 1–3 are the course-correction; 4–6 are capability build-out. Do not
start Phase 2 silicon work without the qdrant gate (AGENTS.md S0).

### Phase 0 — Documentation (this plan's doc deltas)
- [ ] `specs/ask2-rewrite-spec.md`: add the binding requirement "production
      control surface = genl/YNL only; debugfs is `CONFIG_*_DEBUG_FS`-gated
      and absent from production images"; record the three silicon deltas.
- [ ] `arch/fman-fe-ehash.md`: update the HIT record to the 16 B
      `t_ExtHashResult` encoding (context+monitor), stats at +256.
- [ ] `plans/ASK2-MASTER-PLAN.md`: M3 attempt 5 = RCCB AD species +
      result-encoding port + `OFFLOAD_SUPPORT_EN`; mark NXP-106 oracle
      Phases A/B answered (qdrant), Phase C (gap list) live.
- [ ] `plans/NXP-106-DEEP-DIVE-PLAN.md`: mark Phases A/B done with qdrant
      pointers.

### Phase 1 — Control plane: genl sole surface, debugfs gated
- [ ] Implement `get-muram` genl (move `muram_budget` logic off debugfs).
- [ ] Implement `set-policer` genl.
- [ ] Make genl `engage`/`disengage` perform the **full** arm/teardown that
      `fe_arm` debugfs does today (PCD bring-up, KG bind, CC-root, ehash,
      params page). No debugfs dependency.
- [ ] Gate `ask.ko` debugfs behind `CONFIG_ASK_DEBUG_FS`; gate `fman_pcd`
      `fe_*` behind `CONFIG_FMAN_PCD_DEBUG_FS`.
- [ ] Production kernel config: both off. Verify `ask-check` passes and the
      VyOS CLI engages ASK with **no** `/sys/kernel/debug/ask*` or
      `fman_pcd` `fe_*` nodes present.
- **Validation:** cold-boot, engage via CLI only, `pcd-snapshot` diff clean,
  M2 throughput regression monitor green.

### Phase 2 — Silicon correctness (M3 attempt 5): the three deltas
**[SPEC]** Qdrant gate first (S0): query FMan PCD/FE/ehash/KG/CC before any
change; cross-check `arch/fman-microcode-210-programming-reference.md` and
`specs/fman-keygen-flow-key-spec.md`.
- [x] **Delta 1 — RCCB AD species:** DONE (F-185, 2026-08-12) — vendor
      VARIANT B `en_exthash_node` written at RCCB (`copy_td_to_ccbase`
      semantics), `RFPNE` reconciled to `0x00480200`. The RM-8.7.4.1
      group-AD form (F-183) parses as a garbage node — frames terminated
      with no disposition (E23/E24 root cause).
- [x] **Delta 2 — record-side HIT payload:** DONE differently — the
      F-181/F-182 inline opcode-script record (`ENQUEUE_PKT` +
      `en_ehash_enqueue_param` fqid) delivers; the 16 B `t_ExtHashResult` /
      per-flow MUX chain was proven UNNECESSARY (E25 HIT with the minimal
      record; E26 writeback confirms the machine reads it as-is).
- [x] **Delta 3 — params page:** PROVEN NOT A DISPATCH GATEKEEPER (E24) —
      the machine runs under AC_CC with `OFFLOAD_SUPPORT_EN` unset.
- [x] One variable per experiment; cold-boot before each; pings never floods.
- **Validation (M3 gate):** PASSED 2026-08-12 (E25/E26) — one controlled
      TCP flow on eth4 HITs through the record's target FQID (0x300) to the
      kernel, discriminated by the split-target test (miss fqid 0x200/eth3
      vs record fqid 0x300/eth4) + single-pass `kgse_spc` + bucket/chain/
      writeback verification. Full matrix: collision chain, per-key delete,
      UDP, ~780 pps sustained 0% loss, eth3/0x10 structural.

### Phase 3 — Flow learning: fix the flowtable REPLACE path
- [ ] Register ask for the FT offload type; handle `FLOW_CLS_REPLACE` in
      `ask_flow_offload.c` so per-flow HW programming fires.
- [ ] Confirm a forwarded transit flow (CT201↔CT202 through the board)
      inserts an ehash entry and HITs.
- **Validation:** `dump-flows` shows `offloaded=1` with rising
  packets/bytes; line-rate forwarding at low CPU.

### Phase 4 — Scale-out + stats/aging
- [ ] Move past the 32-flow CC-tree cap to ehash scale (mask sizing,
      `0x7fff` vs `0x0fff` ENOMEM tradeoff already known).
- [ ] Wire the +256 stats block + aging (timestamp counters) so
      `dump-flows` packets/bytes are HW-sourced.
- **Validation:** >32 concurrent flows all HIT; stats accurate vs wire.

### Phase 5 — Reversibility hardening
- [ ] Close the disengage residue: `fe_pool` stuck `engaged:YES`, ~8 KB
      MURAM residue, `put` hard-wedge, PR14z21 8213 B leak, F-122
      non-idempotent engage.
- **Validation:** 100× engage/disengage cycles, `pcd-snapshot` byte-exact
  after each, `muram_budget` returns to baseline.

### Phase 6 — Features (M6+, out of scope for the course-correction)
- IPsec (SEC), multicast replication, fragment reassembly, tunnels/PPPoE/
  VLAN, DSCP maps, aging-driven eviction. Each its own gate.

---

## 5. Risks

**[SPEC]**
- **Delta 1 is unproven** — the bare-root wedge (E-HM9/E-HM14) is strong
  evidence but the vendor-faithful root form has not been built in ASK2.
  Attempt 5 may still not HIT; if so, the fault is bracketed between KG
  classification completing and the ehash comparator stats becoming visible
  (use `kgse_spc` + `FMBM_RSTC` to narrow further).
- **Per-flow MUX context MURAM pressure** — each flow needs a context; watch
  `muram_budget` (PR14z21 leak history).
- **debugfs gating must not break bring-up** — keep a dev kernel config with
  the gates on; CI must build both.
- **Do not regress M2/M5** — the M2 regression monitor runs on every
  `fman_pcd.c`/`dpaa_eth.c` change.

## 6. Open questions

**[?]**
- Does the vendor-faithful CC-root form alone close M3, or is the
  record-side `t_ExtHashResult` also required for the first HIT? (Attempt 5
  should land Delta 1 first, alone, to isolate the variable.)
- Is `OFFLOAD_SUPPORT_EN` required for the ehash comparator to run at all, or
  only for stats/aging? (`.106` sets it; our params page never has.)
- Exact `t_FmExtHashBucket` (256 B inline) vs `en_exthash_bucket` (16 B
  chained) selection — which does the 210.10.1 ehash use for the external
  table? (999 source has both; pick per the external-hash path.)

---

## 7. References

- `plans/ASK2-MASTER-PLAN.md` — gate status (M2 DONE, M3 OPEN, M5
  mechanism-unresolved).
- `arch/fman-microcode-210-programming-reference.md` — register/FE contract.
- `arch/fman-fe-ehash.md` — FE-VM ehash silicon contract.
- `specs/fman-keygen-flow-key-spec.md` — flow-key formats (closed).
- `plans/DUAL-DATAPLANE.md` — S0/S1/S2 + CLI contract.
- `plans/NXP-106-DEEP-DIVE-PLAN.md` — vendor oracle.
- 999-patch source:
  `/home/vyos/ask-ref/ask/patches/kernel/999-layerscape-ask-kernel_linux_5_4_3_00_0.patch`
  (authoritative for the encodings in §3.1).
- qdrant (2026-08-11): vendor `.106` arming/offload process; HIT/PASS
  encoding decode; ASK2↔vendor difference inventory.
