# FMan ehash flow-offload — the verified process (E23–E25, 2026-08-12)

How a frame goes from the wire to a kernel FQ through the FMan's enhanced
external-hash machine, as decoded and silicon-verified on the Mono Gateway
LS1046A (210.10.1 microcode, designated test DUT, kernel 6.18.44-vyos,
CI run 31634513313). Sources: Ghidra decode (E23), live register/counter/hash
measurements (E24/E25), vendor 999-patch / fm_ehash.c source. Items marked
`[VERIFIED]` were observed on silicon; `[OPEN]` is unverified/next-test.

Legend: RCCB = FMBM_RCCB (port BMI reg @ +0x34), rfpne = FMBM_RFPNE
(+0x28), spc = scheme packet counter (scheme window word 16, ACCUMULATING —
not read-clear), fqb = scheme's base FQID (word 5), mode = kgse_mode (word 0).

---

## Phase 0 — one-sentence model

The FMan classifies each RX frame by extracting a key (EKFC), hashing it
(CRC-64), indexing a DDR bucket array, and walking a chain of flow records
comparing keys; on a **match** the microcode executes the record's inline
opcode script (enqueue to the flow's target FQID) — the offload decision is
made in silicon. On **no match** it enqueues to the scheme's own base FQID
(`miss_action_type = ENQUE`, word3 = fqid). There is NO match-table walker;
the external-hash machine is the only CC dispatch path.

---

## Phase 1 — arming the FMan (software → silicon)

Debugfs verbs under `/sys/kernel/debug/fman_pcd/0/` (all `echo ... | sudo tee`):

1. `fe_port set 11` — register port 0x11 (eth4) in `pcd->fe_ports`.
2. `fe_ehash set fff 14 0` — create the DDR bucket table
   (`dma_alloc_coherent`, 4096 buckets × 16 B), record `table_dma`,
   `key_size=14`, `hash_shift=0`, `hash_mask_bits=12`.
3. `fe_pool get` — allocate the internal-buffer pool in MURAM
   (`pcd->fe_int_buf_off`, 32 KB + 256 B, 256 B aligned). Contents stay
   all-zero — the vendor's `FM_PCD_Open` also leaves them zero.
4. `fe_singletons build`, `fe_hashfe build`, `fe_enq build`, `fe_enter
   build` — legacy FE chain. **Not referenced by the node dispatch** (kept
   for continuity; harmless).
5. `fe_kg_ekfc set 4 801c0006` — program scheme 4's extraction config:
   PORT_ID|SIP|DIP|PROTO|SPORT|DPORT = 14 bytes; zeroes kgse_dv0/dv1 so
   PORT_ID extracts `0x00` for port 0x11.
6. `fe_arm engage 11 0 200` — `__fman_pcd_fe_arm_engage()`:

   a. **Node build** at `gro` (RCCB target), 16 B VARIANT B `en_exthash_node`
      [VERIFIED word-for-word live: `8e400000 fa110000 04d9080c 00000300`]:
      - word0 = `(2<<30)` miss_action_type=ENQUE | `(key_size<<24)` |
        `(4<<20)` table_type=external-DDR | `(hash_shift<<16)` (hbo) |
        `table_base_hi<<0`
      - word1 = `table_base_lo` (DDR bus addr of bucket array)
      - word2 = `(int_buf_pool_addr>>8)<<16` | `(0x80<<4)`
        (global_mem_offset) | `hash_mask_bits`
      - word3 = placeholder; committed by arm_fe (step 6c).
   b. **Scheme reprogram** (`keygen_scheme_setup`): mode = EN|AC_CC =
      `0x80000006` (next_engine=3, CCOBASE=0), KGSE_CCBS stays 0. The
      scheme keeps its fqb (`0x300` for eth4).
   c. **Miss fqid commit** (F-186): `fman_pcd_fe_node_set_miss_nia(pcd,
      fe_enter_off, slot->base_fqid)` writes word3 = `0x300` BEFORE the
      FMFP_EXTC SYNC in `fman_port_set_cc_base()`.
   d. **Port bind**: `fman_port_set_cc_base(port, node_off)` writes
      FMBM_RCCB = node MURAM offset (`0x56d00`), keeps rfpne =
      `0x00480200` (NIA_ENG_KG|NIA_KG_CC_EN).

   Armed-state readbacks [VERIFIED]: node @RCCB bytes, scheme4
   mode/ekfc/fqb/ccbs, RCCB=0x56d00, rfpne=0x00480200, FMFP_PS STL=0.

## Phase 2 — flow insertion (the DDR record)

`fe_flow add 0 <keyhex> <fqid>` → `fman_pcd_ehash_add_key`:

1. bucket index = `crc64_raw(key) >> 48 & mask` — `crc64_raw` is ECMA-182
   reflected poly `0xC96C5795D7870F42`, seed `~0`, NO final xor
   [VERIFIED: live KG hash matched `crc64_raw(00|SIP|DIP|6|SPORT|DPORT)`
   for ports 44445–44448; bucket 0x508 for the 44444 flow].
2. bucket head = `swab64(phys(record))` written to `table_base + idx*16`
   (en_exthash_bucket = {u64 h; u64 pad}).
3. record = `en_ehash_entry` (256 B) [VERIFIED byte-exact dump]:
   - flags u16@0 = `0x018a`: SET_OPC_OFFSET (opc>>2)<<6, SET_PARAM_OFFSET
     (param>>2), no STATS_EN.
   - key@8 (14 B), opcode script @24 = `ENQUEUE_PKT (0x01)`,
   - param @40 = {mtu u16be `0x05dc`, hdr_xpnd_sz `0`, bpid `0` [OPEN
     whether machine uses it], fqid u32be = target},
   - ctx DMA ptr @56 (F-182 relocation, past everything the machine walks).
   - FMFP_EXTC SYNC asserted after the bucket-head publish (F-177).

## Phase 3 — per-frame classification path (silicon)

[VERIFIED at each observable point on the designated test DUT]

1. **BMI → parser → KeyGen**: rfpne `0x00480200` routes RX to KG with CC
   enabled; parser runs first (rfne = HWP `0x00440000`).
2. **KG extraction**: EKFC `0x801c0006` extracts PORT_ID(1) + SIP(4) +
   DIP(4) + PROTO(1) + SPORT(2) + DPORT(2) = 14 B into the field
   extraction unit (transient; only the hash is retained).
3. **KG hash**: silicon CRC-64 over the extracted bytes; written to the
   frame IC `ctx[0xd048]` (machine's bucket source) and to the buffer
   headroom at `hash_result_offset = 264` (driver's hash_probe source)
   [VERIFIED: hash_probe == crc64_raw(key) exactly].
4. **Scheme select**: SI walk picks scheme 4 (`kgse_spc++` = classification
   happened; ACCUMULATING counter — read trends, not absolutes).
5. **AC_CC dispatch**: scheme mode `0x80000006` → FM_CTL action 0x06 (CC)
   → CC engine reads the AD at `RCCB + CCOBASE*16` (= RCCB, CCOBASE=0) =
   our 16 B node.
6. **Enhanced external-hash machine** (E23 census: AD-type >>30 @w1857 →
   br_tbl[0xf000]; type-1 CONT_LOOKUP):
   - parse node: key_size (AND-0x3f), table_hi (AND-0xff), mask_bits
     (AND-0xf), pool (>>16), table_base_lo → dmem[0xe000];
   - bucket_index from `ctx[0xd048]` (KG hash), >>48 & mask_bits;
   - DMA read bucket head from DDR (works [VERIFIED] — empty-bucket MISS
     proved the read);
   - walk the chain (`next` pointers) comparing record key@8 vs extracted
     key [single-record chain VERIFIED; multi-entry chain walk = OPEN].

## Phase 4 — discrimination: HIT vs MISS (the machine's two exits)

**HIT** (key match):
- machine executes the record's opcode script: ENQUEUE_PKT + param →
  frame enqueued to `param.fqid` via QMI → the target FQ's netdev NAPI
  dequeues → skb → kernel.
- [VERIFIED]: 44444 flow with record fqid 0x300 → `curl rc=7` (kernel
  RST), eth4 rx++, spc stable (single pass, no re-entry).

**MISS** (empty bucket or no key match):
- word3 fqid (miss_action_type=ENQUE) → direct enqueue to
  `slot->base_fqid` (own-port fqb, 0x300 for eth4) → kernel.
- [VERIFIED]: non-record flow (44448) with word3=0x300 → `curl rc=7`,
  spc stable at 2 per 2 SYNs.
- **CRITICAL**: the miss fqid MUST be the frame's own-port fqb.
  Cross-port fqb (0x200/eth3) delivers to eth3's FQ but the dpaa driver
  drops it (eth3 rx_dropped++ — the FD's buffer belongs to the frame's own
  BM pool). [VERIFIED]

**Banned forms (do NOT reintroduce)**:
- miss_action_type=NIA (word0 0b01) + KG-direct NIA word3: infinite
  re-entry loop (~4.5M classifications/sec, no hop limit, no stall;
  KG-direct to a foreign scheme → FM_FD_ERR_NO_SCHEME `0x00004000` →
  error FQ 0x291). [VERIFIED — this was F-185's shipped bug, fixed by
  F-186]
- RM-8.7.4.1 group table AD at RCCB: parses as garbage node (miss DONE,
  key_size 0, MURAM offset misread as DDR) → frames terminated with no
  disposition. [VERIFIED — F-183's failure]
- bare FE_ENTER at RCCB: node with table_base=0/pool=0 → pool-0
  workspace wait = silent stall. [VERIFIED — historical AC_CC stalls]

## Phase 5 — offload semantics

- The **match decision is made in silicon** — no per-packet software
  classification for offloaded flows. The record's target FQID selects the
  kernel netdev/FQ the frame lands on.
- Non-offloaded (MISS) traffic falls to the scheme's own fqb → the kernel
  software path (classification still runs; the hardware just enqueues).
- The FQ target must be within the frame's own port (buffer-pool context);
  flow records cannot steer across ports.

## Phase 6 — observation points (how each stage is confirmed)

| Stage | Observable | Signal |
|---|---|---|
| KG classified | scheme `kgse_spc` (w16) | increments per frame; **trend** climbing = loop |
| Extraction/hash | `hash_probe` (driver vaddr+264) | == `crc64_raw(key)` byte-exact |
| Node programmed | /dev/mem @ RCCB, 16 B | word0-3 as Phase 1a |
| Scheme mode | pcd-snapshot / AR window | mode 0x80000006, ccbs w3=0, ekfc, fqb |
| Port bind | pcd-snapshot port line | RCCB=node, rfpne 0x00480200 |
| Delivered to kernel | netdev rx_packets / `curl rc=7` (RST) | +1 per delivered SYN |
| Driver drop (cross-port) | netdev rx_dropped | >0, rx_packets flat |
| Error FQ delivery | netdev rx_errors + dmesg | "Err FD status = 0x00004000" (NO_SCHEME) |
| Stall | FMFP_PS[port] bit 0x00800000 | 1 = stalled |
| Loop | spc trend | sustained millions/sec |

## Open items (E26 matrix)

1. Multi-record **collision chain** walk in one bucket (never exercised).
2. **Writeback**: re-dump record after a HIT — does the machine write
   anything (aging/ts/stats) into the entry?
3. `param.bpid`: does the machine use it on the ENQUEUE_PKT path (bpid=0
   to own-port untested in E25)?
4. Second port (eth3/0x10): PORT_ID source + own-fqb end-to-end.
5. Second protocol: UDP (17); IPv6 (37/38-byte key, separate scheme/table).
6. Sustained modest rate (100→1000 pps): no loss, no spc drift, no wedge.
