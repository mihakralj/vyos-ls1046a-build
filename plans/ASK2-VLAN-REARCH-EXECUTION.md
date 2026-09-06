# ASK2 VLAN Re-arch — Execution Plan (IPv6 VLAN offload)

Supersedes the draft dated 2026-09-02. Written 2026-09-06 against the
verified silicon facts of the same day. The governing verdict (see
`decomp/findings.md` entries 10–25 and `plans/CC-ACL-OFFLOAD-PLAN.md`
§0.5): the RM-style CC match-table walker does not exist in 210.10.1;
the only content-matching substrate is the enhanced external-hash
machine, now fully live-verified (family bytes 0x40/0x60, dual-lane
46-byte keys, per-port tables, content gating, two-port isolation).

## 1. Verified substrate (build on this, nothing else)

- Record key = 46-byte dual lane, byte-exact: `family | v4-lane(8) |
  v6-src(16) | v6-dst(16) | proto(1) | sport(2) | dport(2)`, family =
  0x40 (v4) / 0x60 (v6), both live-captured from the per-task IC.
- Records live in the per-port tables (tbl[3] = eth3, tbl[2] = eth4),
  CRC-64-bucketed, `keys_equal(ic->key, record+8, ic->ks)` via
  keycmp.run. Content gating proven (16 HITs matched / 0 mismatched),
  two-port isolation proven (25 vs unchanged).
- The record's HIT action = the FE-VM opcode script
  (PARAM_OFFSET/OPC_OFFSET from the flags), the interpreter
  (`decomp/out/02-fe-vm-action-interpreter.c`, w8628..w10262) implements
  ENQUEUE_PKT, INSERT_L2_HDR, **VLAN_STRIP, VLAN_INSERT** (0x11/0x12/
  0x42 handlers, disassembled 2026-08-25, w9451–w9520), NAT/rewrites.
- The HMTD engine (the CC-leaf's `NADEN|EXTENDED` NIA + `hm>>4`) is
  NOT reachable from the FE record path — the record's param area has
  no hm field. The HMTD stays available only via the controller ADs
  (the dead CC-tree path) or the OH-port design below.

## 2. The action-side fork (the real decision)

The classification side is solved. The VLAN edit on an ehash HIT has
two live options:

### Fork A — FE-VM VLAN opcodes in the record script — **RETIRED (2026-09-06 correction)**

Record opcode chain = `VLAN_STRIP/VLAN_INSERT + ENQUEUE_PKT(rule FQID)`.
**This fork is closed, not merely risky.** The oracle was already run
(`decomp/fe-action-interpreter.md`): E-VLAN-1/E-VLAN-2 patched the
`[0xd0b8]` epilogue guard both directions via qef-patch→kexec — both
NEGATIVE, the management-index hypothesis FALSIFIED. The ~20-packet
freeze on the FE-VM VLAN opcode path is unrooted, and the project
retired that path architecturally on 2026-08-26, pivoting production
VLAN to the CC-leaf→HMTD (silicon-validated R4c). `ask_fe_flow_insert()`
rejects VLAN intent so the interpreter path cannot be re-entered.
Do not resurrect it.

### Fork B — OH-port + HMTD (the blueprint's Option D, deferred)

Classification in the ehash; the HIT enqueues to the internal offline
port (patch 0175/0176, unfinished); the OH port runs the HMTD edit
with PAHM and re-enqueues to the wire. The genuine per-flow VLAN
(multi-VLAN-per-port correct). A substantially larger workstream
(the OH-port PCD bringup) — scheduled after the Option A-v6 parity
work, not before.

### Fork C — Option A-v6 CC-tree parity (RECOMMENDED NOW)

The blueprint's own choice, re-validated by the session: a dedicated
CC-tree for the v6 VLAN keys (the 40-byte layout per RM §5.12) with
the CC-leaf→HMTD action — **the exact mechanism the v4 VLAN ships
today**, uniform application included. It delivers v6 parity with the
current v4 behavior (single-VLAN-per-port correct; the multi-VLAN
caveat is identical to the v4 path's today), at the lowest risk, with
zero dependence on the falsified FE-VM path or the unfinished OH port.
The blueprint's four steps apply as written (ask_vlan_cc v6 key fill,
cc_pack_key_v6 40B, the KG scheme, the gate opening).

**Recommendation:** Fork C now (the v6 parity), Fork B later (the
genuine per-flow VLAN).

## 3. Host-side implementation (Fork A, after the oracle)

1. `ask_vlan_cc.c`: `ask_vlan_cc_flow_add()` builds the 46-byte dual
   key (the existing `ask_fe_build_key_dual()` path, family per the
   L3 version) + the record opcode script `VLAN_STRIP/VLAN_INSERT +
   ENQUEUE_PKT(port RX fqid or the rule's)` in the per-port table via
   `fman_pcd_fe_flow_add()` (rx_fqid = F-241).
2. `ask_flow_offload.c`: open the insert gates (the `-EOPNOTSUPP` for
   `l3_proto==IPV6` becomes family-aware); the v4 VLAN path migrates
   off the CC tree in the same change (eliminating the multi-VLAN-
   per-port collision risk the uniform-application behavior carries).
3. The CC-tree VLAN machinery (`cc_pack_key_dual*`, the V6-2
   installers) stays dormant until the migration proves out, then
   retires.

## 4. Verification matrix (the harness is standing)

- The v4 VLAN regression first: the eth3.6/eth3.8 hardware-offload
  config on the DUT, the helga/owrt peers, the stats-flagged HIT
  counters, the FQID differential.
- The v6 VLAN matrix: the temp-v6 technique (fd99:1::/64 pairs) with
  the 0x60-family records; ICMPv6 + the UDP-v6 tuples; the same
  HIT/mismatch counters.
- The Fork-A freeze probe: the ~20-packet sustained-VLAN soak with the
  [0xd0b8] readback.
