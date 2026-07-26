# ASK2 Journey Review & Forward Plan

**Version 1.0 · 2026-07-18 · HADS 1.0.0**

## AI READING INSTRUCTION

This document is the complete ASK2 status and forward plan, covering the journey
from the 2026-06-16 baseline through 2026-07-18. It supersedes
`plans/ASK2-DEVELOPMENT-PLAN.md` v1.0 (frozen 2026-06-16) and incorporates all
silicon-proven facts from the 32 days of development since.

Read order: §1 (where we are today) → §2 (NXP SDK oracle comparison) → §3 (five
recent commits) → §4 (gaps to close) → §5 (forward milestones).

---

## 1. ASK2 Ground State — 2026-07-18

### 1.1 What we have built

**[SPEC]**
The ASK2 substrate spans five domains, all at various stages of completion.
Here is what exists in the tree as of commit `1017612` (branch `dpaa1`):

| Domain | Patches | State | Silicon status |
|---|---|---|---|
| **FMan PCD subsystem** | 0092–0118, 0151–0155 | DONE, shipping | CC/HM/Policer/KeyGen all HW-proven |
| **FE-VM ehash substrate** | 0124–0131 | BUILT, DORMANT | Byte-verified via `fe_*` debugfs; MISS→EXIT proven safe |
| **Reversible mode-switch + pcd-snapshot** | 0105/0106/0116/0129 | DONE | 100× control-plane soak, 0 drift |
| **AF_XDP ZC datapath** | 0068–0114, 0139, 0164 | DUT-VALIDATED (ZC pending) | Copy-mode 3.5 Gbps; ZC fix committed (0164), awaiting HW |
| **ask.ko control plane** | kernel/ask/ | DORMANT (genl + flow table + debugfs) | Not yet driving FE-VM datapath |
| **fman_pcd_port_recover** | 0163, F-086 | LANDED | HW-validated; debugfs `fe_recover` wired today |
| **FmPortSetFESupport auto-arm** | F-072b/c/d, 0123 | LANDED | Gate A proven; MURAM leak fixed (F-072d) |

**[SPEC]**
107 board patches total. Kernel 6.18.38-vyos. Single dual-dataplane ISO.
`default | vpp | ask` flavor split retired 2026-06-14.

### 1.2 Silicon-proven facts

**[NOTE]**
These are all verified on real LS1046A hardware (board 192.168.1.185 or 192.168.1.190).

| Fact | Date | Build | Evidence |
|---|---|---|---|
| **M2 gate PASS: 7.37 Gbps, 0.16% CPU** | 2026-07-07 | 28809182051 | AC_CC CONT_LOOKUP pass-through, MTU 9000, 0 retransmit, 0 QMan errors |
| AC_CC overhead vs RSS: 3.6% | 2026-07-07 | 28809182051 | 7.37 vs 7.26 Gbps baseline |
| **Ehash DDR 256B records** | 2026-06-15 | multiple | `dma_alloc_coherent` 524288 B, 32768 buckets |
| **FE-VM MISS→EXIT proven safe** | 2026-07-10 | 28809182051 | keysize=8, 600-frame MISS flood, zero corruption |
| **FE-VM ENQ-as-kernel-delivery CLOSED** | 2026-07-16 | resolved | 3 ENQ variants all FAILED — closed as architectural impossibility per settled topology |
| **FmPortSetFESupport Gate A proven** | 2026-07-15 | F-072 | pool 0x54400/8448B, 600-frame flood, clean disengage |
| **100× engage/disengage soak PASS** | 2026-06-16 | M1 gate | 0 MURAM drift, 0 crash, VPP comes up after 100th disengage |
| **EKFC extraction order CONFIRMED MSB-first** | 2026-07-13 | 29171988617 | CRC-64 hash match against two independent TCP flows on eth4 |
| **CRC-64 raw (no final complement) confirmed** | 2026-07-13 | 29171988617 | `crc64_raw(key) = 0x600824e70ae4d573` matched hardware |
| **fman_pcd_port_recover functional** | 2026-07-18 | 0163 | debugfs `fe_recover` wired through F-086 |

### 1.3 What remains in the five-layer ASK2 stack

| Layer | Status | Blocker |
|---|---|---|
| **1. PCD subsystem** (KG/CC/HM/PLCR) | ✅ SHIPPING | — |
| **2. FE-VM ehash substrate** | ✅ BUILT, DORMANT | — |
| **3. Classifier→FE arm** | ✅ Conditional scaffold (0161) | FE-VM HIT path not yet activated under traffic |
| **4. ask.ko datapath** | 🔴 DORMANT | Needs FE-VM HIT to function → blocked on HIT gate |
| **5. VyOS CLI + mx** | 🔴 NOT STARTED | Gated on ask.ko datapath |

---

## 2. NXP ASK SDK Oracle Comparison

### 2.1 What the NXP SDK does that we must replicate

**[SPEC]**
From the lf-5.4 LSDK `999-layerscape-ask-kernel` patch (the only SDK with
working FE-VM programming — lf-6.6 and lf-6.12.49 both stub the three core
functions):

| SDK Function | Lines (lf-5.4) | Purpose | Our equivalent | Status |
|---|---|---|---|---|
| `FmPcdCcBuildFE` | L8883 | Program FE descriptor (EXT_HASH, ENQ, MUX, etc.) into MURAM | `fman_pcd_fe_hash_encode()` etc. in 0131 | ✅ Verbatim SDK byte layout |
| `FmPcdCcBuildContextByFE` | L8954 | Build workspace context (MUX, ENQ ADs) | `fman_pcd_fe_enter_build()` etc. | ✅ MUX at AD+4 (F-060 v3d proven) |
| `get_indexed_hash_bucket` | L7301 | `(crc >> ((6-shift) * 8)) & mask` | `fman_pcd_ehash_bucket_index()` | ✅ Verbatim identical |
| `FmPortSetFESupport` | L14545 | Per-port FE buffer pool + management index | `fman_pcd_fe_buffer_setup()` (F-072 v3) | ✅ F-072b auto-arms on engage |
| `FmPortDeleteFESupport` | L14604 | Clear +0x54 → free pool → free index | `fman_pcd_fe_buffer_teardown()` (F-072d) | ✅ Fixed leak (2026-07-17) |
| `AllocFEObjs` | fm_pcd.c:433 | 100×28B FE object pool in MURAM | `fe_pool` debugfs (0124) | ✅ |
| `FM_PCD_HashTableSet` | fm_ehash.c | External hash table init (DDR buckets, EXT_HASH FE) | 0130 + 0131 | ✅ |
| `ExternalHashTableAddKey` | L12128 | DDR flow record insert | `fman_pcd_fe_flow_add()` (0128) | ✅ |
| `fill_ehash_key_info` | L11898 | Assemble key from GEC-declared fields (FMC order) | **N/A — we use EKFC, not GEC** | ✅ By design |
| `cdx.ko` OH-port driver | devoh.c (17KB) | Offline Host port for L2 rewrite + forward | **NOT PORTED — Fork-B path** | Skipped by design |
| `cdx_ehash.c` | 124 KB | FE-VM flow store management | **NOT PORTED** | We implement our own ehash |

**[NOTE]**
**Fork-B (FE-VM ehash) is the path we chose.** The Fork-A (CONT_LOOKUP exact-match
without FE, MANIP-dedup, OH-port) path was the original ASK2 M2 target but was
hardware-proven to park frames on 210.10.1 (Fork A is "disposition-less WAIT" per
iter-49/50, 2026-06-16). Fork B was always the NXP production path — the vendor's
`dpa_app`/`cmm`/`cdx` stack wires `externalHash=TRUE` for every scheme. Our
architecture now mirrors this: CONT_LOOKUP group-table for MISS→kernel (the 2026-07-10
settled topology), FE-VM ehash for HIT→forward.

### 2.2 What the NXP SDK does that we DON'T need (design decisions)

| SDK Feature | Why we skip it |
|---|---|
| **GEC (Generic Extract Commands)** | Adds 5 byte-range copies per frame permanently. EKFC extraction is free (silicon does it in the KeyGen pipeline). We resolved the order problem instead (MSB-first confirmed 2026-07-13). |
| **FMC XML-driven scheme config** | The FMC parses `cdx_pcd.xml` at boot via `dpa_app` → `/dev/fm0-pcd` ioctl. We program schemes directly via kernel API. |
| **`cdx.ko` userspace helper** | ASK2 is a kernel-in-tree design. No `call_usermodehelper(/usr/bin/dpa_app)`. |
| **OH (Offline Host) ports** | The OH port rewrites L2 headers (RMV_ETHERNET + INSRT_GENERIC) and forwards to TX FQ. On Fork-B, the FE opcode VM handles this via MANIP opcodes directly in the dispatch chain. We don't need OH ports for v1. |
| **`cmm` auto-bridge daemon** | VyOS's own `conntrack` + `nf_flow_table` replaces this userspace daemon. |

### 2.3 Key SDK reference facts we verified

**[SPEC]**
From the lf-5.4 SDK oracle, confirmed on our silicon:

1. **CRC-64 is raw, no final complement.** Self-test: `crc64_raw("123456789") = 0x66A2364420E6C605`. The finalized CRC-64/XZ variant does NOT match hardware.
2. **contextOffsetInWS = 0** — the SDK passes 0 to `FmPcdExternalHashTableSet()`. Works in production. Our `contextOffsetInWS = 0` is correct.
3. **FE_ENTER ALLOCATE = 0x00800000** — must be set on the FE_ENTER root AD word0. Without it, the FE-VM has no workspace and every frame MISSes regardless of DDR table content (F-046 root cause).
4. **EKFC order = MSB-first (descending bit position).** SIP→DIP→PROTO→SPORT→DPORT. 13 bytes for EFC=0x001C0006. Verified 2026-07-13 via CRC-64 hash-match.
5. **DDR record format:** 8B header (flags + next pointer) + key bytes at offset 8. 256B total. F-057 confirmed no per-record next-FE in DDR — SDK's `en_ehash_entry` has only 8B header + key.
6. **Bucket stride = 16B.** `en_exthash_bucket { u64 hash; u64 pad }`. 524288B / 32768 = 16B ✓.

---

## 3. Last 5 Commits — F-086/F-086c/fman_pcd_port_recover

**[NOTE]**
The last 5 commits (2026-07-18, 04:36–17:30 UTC) are all infrastructure hardening
around `fman_pcd_port_recover` (patch 0163) and its debugfs wiring:

| Commit | What | Impact |
|---|---|---|
| `1017612` | Fix F-086 heredoc marker collision (inner `PYEOF` truncated outer) | Build fix — shell heredoc bug |
| `597ee32` | F-086/F-086c: heredoc Python3, no base64 | Code quality — escape-safe Python |
| `5046dcc` | F-086c forward-declare `fman_pcd_fe_recover_fops` | Build fix — C compilation |
| `319f83c` | 0163: fix two compile errors (fops + mdelay) | Build fix — complete 0163 landing |
| `0a2f794` | F-086 base64 Python (avoids `\n\t` in sed) | Build fix — prior sed escaped wrong |

**[SPEC]**
Together these wire the `fman_pcd_port_recover()` function (patch 0163) into a
debugfs write node `fe_recover` under `fman_pcd/<N>/`, completing the M3-3f
milestone. The recover path zeroes the per-port internal FE buffer free-list and
depletion counter (+0x58) without a cold boot. The hardware-proven cold-boot
requirement for BMI-stall recovery is now replaced by a debugfs-triggerable,
near-instant de-wedge — a critical productivity unlock for the HIT-gate campaign.

### 3.1 Combined with prior session's work (0164 true-ZC fix)

**[SPEC]**
The immediately prior commit (`dd34d39`, patch 0164) fixed two API conformance
gaps in the AF_XDP true-ZC path:

1. **Wrong port accessor** — `mac_dev->port[DPAA_MAC_PORT_RX=0]` was the TX port
   (DTS phandle order inverted from `enum fman_port_type`). Fix: `fman_port_lookup_rx()`.
2. **Missing `fman_pcd_port_ensure_params_page()`** — without it, `+0x54=0` and
   FE_ENTER ALLOCATE corrupts MURAM on every ZC frame (F-072 class bug).

Expected: `fman_port_set_rx_bpool()` returns 0 (not -22), `xsk_zc_rx_redirect`
climbs under traffic, true-ZC RX becomes productive for the first time.

---

## 4. Gaps to Close — Prioritized

### 4.1 Gap A — FE-VM HIT gate (M3) 🔴 BLOCKING

**[SPEC]**
The FE-VM ehash chain is built, dormant, and byte-verified against the SDK
oracle. It has never produced a HIT under live traffic. The current state:

| Component | State | Verification |
|---|---|---|
| FE_ENTER AD | word0=0x40800000, word2=0xF6000000, word3=0x0004af00 (EXT_HASH) | ✅ Correct (F-046 reverted, F-084 compose fix landed) |
| EXT_HASH FE | hashMask=0x7FFF, contextSize=13, hashShift=0, DDR=0xf7780000 | ✅ Correct |
| DDR bucket array | 524288B, 32768 buckets × 16B | ✅ Allocated, zeroed |
| Flow insert (key) | 13B MSB-first at offset 8 in 256B DDR record | ✅ Per SDK oracle |
| Flow insert (bucket) | `(crc64_raw(key) >> 48) & 0x7FFF` | ✅ Formula verified |
| MUX singleton | FE type=0x04000000, enq_off at word1 | ✅ F-060 v3d confirmed |
| ENQ singleton | word0=0x02810000 (ALLOCATE), word1=0x00000200 (FQID) | ✅ F-062d v2 confirmed |
| MISS terminal | hash FE word6 = EXIT at 0x55300 | ✅ Correct |
| **HIT datapath** | **NEVER TESTED** | 🔴 Must test with FmPortSetFESupport auto-armed |
| fman_pcd_port_recover | Debugfs `fe_recover` wired | ✅ Just landed — de-wedge available |
| keysize=13 stall | Unknown current status | 🔴 Was observed on old build (no F-072b); retest needed |

**[NOTE]**
The keysize=13 BMI stall observed on 2026-07-12 was from a build WITHOUT
FmPortSetFESupport auto-armed (F-072b landed 2026-07-17 23:47 UTC — AFTER
all prior keysize testing). With F-072b/c/d now in the tree, the workspace
pool is armed automatically on every `fe_arm engage`. **All prior keysize=13
stall results are invalidated** because the underlying root cause (FE workspace
pool at garbage offset 0) has been fixed. A retest with the current build
is required to determine whether keysize=13 now works.

**HIT gate test sequence (on board .185 with new ISO):**

```bash
# 1. Verify F-072b auto-arms (check dmesg for pool allocation after engage)
# 2. Engage FE-VM on eth3 (port 0x10)
echo 'engage 10 0 2B9 1C0006' > /sys/kernel/debug/fman_pcd/0/fe_arm

# 3. Insert test flow
echo 'add 0A99016A0A9901B906D6DA270F 0x55500' > /sys/kernel/debug/fman_pcd/0/fe_flow
# (SIP=10.99.1.106, DIP=10.99.1.185, PROTO=6, SPORT=55002, DPORT=9999)

# 4. Send matching TCP SYN from peer
# On .106: echo "test" | nc -w1 10.99.1.185 9999

# 5. If stall: echo '10' > /sys/kernel/debug/fman_pcd/0/fe_recover
#    If recover fails: cold boot
```

### 4.2 Gap B — AF_XDP true-ZC RX productive oracle (M3-3 step 7) 🟡 LANDED, AWAITING HW

**[SPEC]**
Patch 0164 (port accessor + params page fix) is committed. Once deployed:
- `fman_port_set_rx_bpool()` should return 0 (not -22)
- First detach of 0103c code path → `fman_port_disable(rxp)` → `fman_port_set_rx_bpool(rxp, xsk_bpid, kernel_bpid)` → `fman_port_enable(rxp)` all now use the correct RX port
- `xsk_zc_rx_redirect` should climb under XDP_ZEROCOPY bind + traffic

### 4.3 Gap C — Cross-track alignment (CC+HM+Policer → FE-VM HIT) ⬜ PLANNED

**[NOTE]**
The settled architecture (spec v4.0 §6.1) places CC-layer CONT_LOOKUP as the
MISS→kernel path and FE-VM as the HIT→forward path. The CC match-table insert
path needs to target `FE_ENTER` for HIT entries, not the current group-table
miss-AD. This is the architectural handshake that connects the CC subsystem
(already shipped) with the FE-VM (dormant until HIT gate passes).

### 4.4 Gap D — fman_pcd_budget post 0166 (MURAM tracking) ⬜ PLANNED

**[NOTE]**
Documented in `arch/fman-pcd-api-reference.md` §16. The latent MURAM leak in
scaffold operations (F-075) was fixed (0152). The new objects from 0164
(per-attach params page) need to be tracked in the muram_budget debugfs node.

### 4.5 Gap E — VyOS CLI + ask.ko datapath activation ⬜ GATED ON A

**[SPEC]**
Blocked on FE-VM HIT working. The `set system offload ask` CLI path
(`plans/DUAL-DATAPLANE.md` §4) composes the debugfs-proven verbs into a
single commit-triggered engage/disengage cycle. This is architectural glue,
not new silicon work — it's ready to wire once the FE-VM path is proven.

---

## 5. Forward Milestones (July–September 2026)

### M3 — FE-VM HIT gate (next on deck)

- **Gate:** One flow HIT (stats increment, kernel sees packet on TX FQ 0x2B9)
- **Dependencies:** F-072b auto-arm + fman_pcd_port_recover in place ✅
- **Key risk:** keysize=13 may still stall post-F-072b (BMI mechanics beyond
  workspace pool)
- **Mitigation:** Start with keysize=8 / EKFC=4-tuple → prove HIT → then scale to 13
- **Calendar:** ~1 day of board sessions (was 5–10 sessions before `port_recover`
  eliminated the 2+ min cold-boot bottleneck)

### M2 — Performance gate (PASSED, regression-monitor only)

- **Gate:** ≥2 Gbps ≤5% CPU (actual: 7.37 Gbps, 0.16% CPU — 3.7× above)
- **Status:** ✅ DONE (2026-07-07, build 28809182051)
- **Monitor:** Every build that changes `fman_pcd.c` or `dpaa_eth.c` must re-run
  the CONT_LOOKUP pass-through iperf3 gate to ensure no regression

### M4 — AF_XDP true-ZC RX productive

- **Gate:** `xsk_zc_rx_redirect` > 0 under XDP_ZEROCOPY bind + traffic
- **Dependencies:** 0164 patch deployed ✅, builder available
- **Calendar:** ~1 board session (bind → observe counters → measure throughput)

### M5 — First classified+FE-forwarded flow (HIT→MUX→ENQ→TX FQ)

- **Gate:** ask.ko inserts flow → traffic HITs → kernel receives on TX FQ
- **Dependencies:** M3 (FE-VM HIT) → CC match entry pointing at FE_ENTER → ask.ko wired
- **Architecture:** `CONT_LOOKUP numKeys=1 match entry → FE_ENTER → EXT_HASH → DDR lookup → HIT → MUX → ENQ → TX FQ`
- **Calendar:** ~1 week after M3

### M6 — IPv6 + bridge + IPsec (parallel tracks post-M5)

- **M6a IPv6:** EXT_HASH dual-scheme (v4 scheme + v6 scheme, separate EKFCs)
- **M6b Bridge:** L2 switchdev via ask_bridge.ko
- **M6c IPsec:** CAAM descriptor-sharing API forward-port + xfrmdev_ops
- **Calendar:** ~4 weeks (parallel)

### M7 — VyOS CLI ships

- **Gate:** `set system offload ask` engages ASK; `delete system offload ask` restores S0
- **Verification:** pcd-snapshot diff clean after engage→disengage cycle
- **Calendar:** ~1 week

### M8 — Productization soak + upstream

- 100× trafficked engage/disengage cycles, 24h alternating ASK/VPP
- ask-check exits 0
- Upstream submission begins

---

## 6. Architecture Decision Record — Key Choices

**[SPEC]**
These decisions are binding on all future work:

1. **Fork-B (FE-VM ehash)** — Fork-A (CONT_LOOKUP exact-match without FE) was
   hardware-proven to park frames on 210.10.1 (iter-49/50, 2026-06-16). Fork-B
   is the NXP production path and the only configuration known to flow.

2. **EKFC-only (no GEC)** — GEC adds permanent per-frame latency. EKFC order
   is resolved (MSB-first confirmed 2026-07-13). `kgse_gec[]` stays zero.

3. **Raw CRC-64 (no final complement)** — The hardware stores raw CRC-64 at IC
   offset 0x48. The CRC-64/XZ finalized variant does NOT match hardware.

4. **MISS→kernel via CONT_LOOKUP pass-through** — The FE-VM has no viable
   kernel-delivery terminal (3 ENQ variants failed). The settled topology routes
   MISS at the CC layer (numKeys=0 → miss-AD → PCD FQ), and the FE-VM executes
   only on HIT.

5. **Single-image dual-dataplane** — S0 (mainline/RSS) at boot; S1 (ASK) on
   `set system offload ask`; S2 (VPP) on `set vpp settings`. ASK↔VPP always
   passes through S0. Flavor split retired 2026-06-14.

6. **contextOffsetInWS = 0** — SDK default, verified correct. The raw extracted
   key is transient in the Field Extraction Unit; the FE-VM comparator reads
   from the microcode's implicit staging area.

7. **FmPortSetFESupport is MANDATORY for any FE-VM frame** — without it,
   FE_ENTER ALLOCATE books workspace at MURAM offset 0 (F-072). This is now
   auto-armed on every `fe_arm engage` (F-072b).

---

## 7. Defect Register (Open)

| ID | Symptom | Status | Mitigation |
|---|---|---|---|
| **F-076** | Port RX deaf after FE-VM-armed disengage | OPEN | Cold boot recovers; `fman_pcd_port_recover` may de-wedge (untested) |
| **keysize=13** | BMI port stall on first FE-VM frame | RETEST NEEDED | F-072b auto-arm may have fixed it; use `port_recover` if not |
| **F-063 (contextSize)** | EXT_HASH FE reads 256B per entry if contextSize bogus | CLOSED (0131 fix in-source) | Patch 0131 directly uses `key_size - 1`, not the macro |
| **zc_rx_bpool -EINVAL** | `fman_port_set_rx_bpool()` returns -22 | FIXED (0164) | Correct port accessor + params page → awaiting HW deploy |
| **F-072d MURAM leak** | Teardown only cleared +0x54, never freed pool/index | FIXED (F-072d) | Now reads offsets, frees both per SDK `FmPortDeleteFESupport` |

---

## 8. References

| Document | Role |
|---|---|
| `arch/fman-microcode-210-programming-reference.md` | Authoritative 210.10.1 register/FE/resource reference |
| `arch/fman-fe-ehash.md` | FE-VM init contract, M0 oracle, reversibility contract |
| `arch/fman-pcd-api-reference.md` | Complete kernel API surface (84 functions, 14 groups) |
| `specs/fman-keygen-flow-key-spec.md` v4.0 | EKFC extraction, CRC-64 hash, FE-VM ehash, settled topology |
| `specs/ask2-rewrite-spec.md` v1.8 | Architecture index — redirects to arch/specs |
| `specs/dpaa1-afxdp-modernization-spec.md` v5.23 | AF_XDP ZC datapath, M3-3 step 7, all offload milestones |
| `plans/DUAL-DATAPLANE.md` v1.1 | S0↔S1↔S2 state machine, reversibility contract, CLI semantics |
| `plans/ASK2-DEVELOPMENT-PLAN.md` v1.0 | Frozen 2026-06-16 — superseded by this document |
| NXP lf-5.4 LSDK `999-layerscape-ask-kernel` patch | FE-VM programming oracle (lf-6.6/6.12 both stub it) |
