# P0 — FE-VM VLAN management-index leak: patch identification + oracle runbook

**2026-09-07 · Status: PATCH IDENTIFIED, EXPERIMENT PREPARED, NOT YET RUN ON SILICON**

This is the experiment plan for curing the FE-VM VLAN freeze (the retired
F-233/F-234 inline opcode path; frozen at exactly 5+tnums = 21 packets,
board-reproduced 3× 2026-08-25). The goal of P0 is narrow: prove which
instruction(s) leak, then prove a two-word patch fixes it. The long-term
delivery mechanism (per-boot IRAM re-stream via patch 0117) is out of P0
scope and already designed.

## 1. The patch candidates (identified, pristine-blob verified)

Blob: 210.10.1, 12,851 code words, pristine md5
`6f23090a3d5ae8b302ea41fd90a14d4d`
(source `/tmp/kilo/fman-ucode-exact.bin`, board mtd3 capture).

Both VLAN handlers run an identical semaphore-protected counter increment,
decoded field-level by `decomp/tools/fman-isa-xref.py`:

| Handler | Site | Word | Instruction | Meaning |
|---|---|---|---|---|
| STRIP_ALL_VLAN (0x12) | w9482 | `f0431b01` | `addlane8 r3 = r3 + 1` (lane 3, imm 1) | increment counter loaded from `[r4+8]` (w9479), written back w9483 |
| INSERT_VLAN_HDR (0x42) | w9549 | `f0431b01` | identical | identical |

The critical-section shape (both handlers, byte-identical):
`ld.sm [r4+0]` → `retry.sm` → `r3 = [r4+8]` → (r1 += r5; r0 += r31) →
`addlane8 r3 += 1` → `[r4+8] = r3` → `st.sm [r4+0]`, where `r4 = [r2+0]`
loaded at w9469 (r2 = dispatcher-preloaded, no producer in the decode
window — same pattern as the entry-gate r6).

**The chosen mutation:** zero the immediate — `f0431b01 → f0431b00`
(same instruction class, adds 0, counter never advances). Rationale: E-HM9
proved the balancing dealloc walk (w12667–12850) is never reached for an
armed frame, so the release side cannot be invoked; "don't consume what
nothing ever returns" is the minimal single-variable change. The E-VLAN-1/2
negative results do not conflict: those toggled the epilogue reset guards
(w9072/9246/9440/9492), never these increments.

**Precomputed patched blob** (qef-patch.py, trailer CRC recomputed):
`/tmp/kilo/p0-vlan-leak-fix.bin`, md5 `722d4f90e4acbb9eab117c2a1f346658`.
Two words changed, nothing else.

## 2. Experiment branch

`p0-fevm-vlan-leak` (local worktree `/tmp/kilo/p0-fevm`) =
`fevm-vlan-investigation` @ `64268521` (board patch 0174 "vendor ehash
model for routed VLAN HW offload" — the FE-VM inline emitter, retained for
this investigation) + cherry-picked `3a8a325f` (patch 0192, fe_buffer
5+tnums free-list hex dump — the read-only discriminator). Series conflict
resolved by inserting 0192 directly after 0174; 0175–0191 deliberately not
pulled. NOT pushed, NOT built, NOT deployed.

Arming surface on this branch: `echo Y > /sys/module/ask/parameters/vlan_offload`
+ VLAN vif as flowtable member (`ask_flow_offload.c:1314` gate). Same
harness as the 2026-08-25 F-233 sessions.

## 2a. Build & deployment state (2026-09-07)

- Branch `p0-fevm-vlan-leak` pushed; CI run 34100547766 SUCCESS.
- ISO `vyos-2026.09.07-0826-rolling-LS1046A-arm64.iso` (565,182,464 B)
  rsynced to lxc200 `/srv/tftp/iso/`; `latest.iso` and
  `latest.iso.minisig` both refreshed to it (also repaired the pre-existing
  stale pair 0211-iso/0131-minisig). Served minisig md5 verified identical
  to the versioned file (`b98ad7183eaad4bb3175409f6bd64a2d`).
- Operator install URL: `http://192.168.1.137:8080/iso/latest.iso`
  (`add system image` — agent never runs this per AGENTS.md).
- Board .185 pre-install state: eth0 mgmt UP, eth3/eth4 UP, config.boot
  already carries eth3.6/3.8/3.10/eth4.20 + flowtable FT01
  (eth3, eth4, vifs) `offload hardware`, ASK engaged on eth3/eth4 —
  all persists into the new image.
- Harness: peer .116 UP (eth3.10 = 10.99.10.116/24, VID 10 trunked on
  switch .50 te1/te7), HELGA 192.168.1.16 UP firewall-OFF (iperf3 sink
  10.99.2.16). ASK1 board .110 DOWN this session — .116 is the peer.

## 3. P0b — read-only discriminator (no microcode write)

Prerequisite: board running the `p0-fevm-vlan-leak` image (build TBD:
CI ISO or TFTP dev-build), cold boot, ASK engaged on eth3 (0x10)
sacrificial only, mgmt on eth0 untouched.

1. Baseline: `cat /sys/kernel/debug/fman_pcd/0/fe_buffer` → the 0192 dump
   line must show the pristine array for port 0x10:
   `idx[0x59100] cursor=4: 04 05 70 00 00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f ff`
2. Arm VLAN offload, create eth3.100 vif + flowtable membership, drive the
   routed VLAN flow (managed switch 192.168.1.50 VLAN100 trunk te1; peer
   `.110` eth3.100 or the `.116` namespace harness; few packets — the
   historical freeze needs no flood).
3. Wait for the freeze (fe_ehash_stats pkt_count stops at ~21, TX FQ
   frm_cnt=0). Re-read fe_buffer **during** the frozen state, 3×.
4. **PASS criterion (leak located):** the array dump moves off baseline —
   cursor advances, a free-list byte changes, or the counter byte 8+ ticks.
   **Negative result is still informative:** if the array is byte-identical
   during a frozen flow, the `[r4+8]` counter is NOT this array, and the
   21-ceiling resource lives elsewhere (per-tnum workspace / per-task IC)
   — do not proceed to P0c; re-target the probe first.

## 4. P0c — the mutation oracle

Same board state. Cold boot before the mutation (decomp ground rule §10.9).

1. `python3 decomp/tools/qef-patch.py --fdt /sys/firmware/fdt --set-word 9482=0xf0431b00 --set-word 9549=0xf0431b00 -o /boot/p0-fix.dtb`
2. `kexec -l /boot/vmlinuz --initrd=/boot/initrd.img --dtb=/boot/p0-fix.dtb --reuse-cmdline && kexec -e`
   (proven E1/E2 pipeline; 0117 re-streams on kexec boots; post-boot live
   blob md5 must equal `722d4f90e4acbb9eab117c2a1f346658`).
3. Re-arm VLAN, re-drive the flow. **PASS:** pkt_count sustains past 21
   (aim: 10k+ packets), wire capture shows the tag strip/insert still
   correct (frames arrive untagged at the sink), routed+NAT regression
   byte-identical, ErrFD=0, FMFP_PS no stall, fe_buffer array stays
   pristine.
4. **FAIL mode A (freeze persists):** the counter is not the freeze
   mechanism — abort, revert by plain reboot (pristine blob), record.
   **FAIL mode B (freeze clears but strip/insert breaks on wire):** the
   counter IS load-bearing for slot selection; fallback mutation = hijack
   a slack word in each handler body (e.g. the `ffffffff` fill words) to
   run a compensating `addlane8 r3 -= 1` on the handler-local path only —
   the shared w11911 exit must never be touched (routed frames converge
   there).

## 5. Safety (unchanged ground rules)

eth3 sacrificial, never eth0; few packets, never a flood (BUG-3b); cold
boot before silicon experiments; one variable per experiment; plain reboot
restores the pristine blob (nothing persists — kexec-only delivery). If
the board wedges: smart-plug power cycle.

## 6. What P0 buys

A verified two-word cure for the FE-VM VLAN freeze, deliverable at every
boot through the existing 0117 IRAM re-stream (word-diff table in our
kernel source, no SPI writes, no blob redistribution), plus a complete
NXP bug report for 210.10.1. Re-enabling the FE-VM VLAN path in production
remains a separate decision from curing it (HMTD Option A stays the
shipping VLAN path unless a vendor-style combined-chain capability
requires the FE-VM).

## 7. FINAL VERDICT (2026-09-07, end of P0 campaign)

Five mutation rounds (P0c/P0c2/P0c3/P0c4 + P0b observation) plus the 0194
full-record readback converge on a terminal negative for the inline
approach:

1. **Freeze reproduced and characterized**: records die at <=21 HITs; the
   engine keeps processing every frame (FPM task-status counters tick per
   retransmit) yet NOTHING is enqueued to any TX FQ (fq_probe frm_cnt=0,
   producer-side); handshakes complete because they ride the SW path
   before HW_OFFLOAD latches.
2. **Mutated every patchable surface, all falsified**: VLAN handler
   critical sections (16 words), their counter increments, the ENQUEUE
   handler's two counter increments, the unit16 continuation retry, the
   epilogue reset guards (E-VLAN-1/2) — freeze persists byte-for-byte.
   The IMEM idata port is address-indexed (E-HM17 "stuck PC" reading was
   coincidence); the FPM task-status array shows ENQUEUE-action state
   0x81000006 whose counter field still ticks (not a park).
3. **0194 record readback — the emitter is EXONERATED**: the frozen
   records' full bytes (key + opcodes + params) match the vendor model:
   POP=[12 21 41 01] PUSH=[21 42 41 01], VID 10 present, op_flags=0,
   DSCP=0, L2 param 0x4000000e + correct MACs + 0x0800 ethertype, MTU
   0x5dc, enqueue FQIDs 0x2bb/0x2ba matching fq_probe. Not an
   emitter/layout bug; not host-side fixable.
4. **Vendor reference (qdrant 2026-09-01)**: ASK 1.0 on the .116
   reference DUT does NOT forward routed VLAN via inline FE-VM ehash
   opcodes — it provisions dedicated Offline-Host ports with HM queues.
   The inline 0x12/0x42 route-to-go path is a vendor-untested corner of
   210.10.1, genuinely broken for bulk forwarding at the interpreter
   level. A perfectly formed record is consumed and its enqueue handoff
   fails only for chains containing 0x12/0x42.

CONCLUSION: the FE-VM inline VLAN path in microcode 210.10.1 is closed
as unrepairable within project rules (EULA, kexec-only delivery, no SPI).
The production architecture (Option A: CC-leaf -> NADEN -> HMTD —
silicon-proven 273K+ packets, 1.2–1.4 Gbit/s sustained 2026-09-03)
remains the correct, shipped solution. P0's durable value: complete
failure-mode characterization for the NXP bug report, the 0193/0194
diagnostic readers (engine task-status + full-record dump), and the
verified boot-time microcode patch delivery design (0117 word-diff
table).

CI-side debt accumulated during P0 (throwaway-branch fixes that the
mainline SHOULD absorb): honoring KERNEL_VERSION env in
ci-build-packages.sh, pinning defaults.toml kernel_version in
ci-setup-vyos-build.sh, the kernel-cache rm-rf-mountpoint destruction
bug, and the shallow-cache p1-fuzz patch-application drift discipline.

## 8. MICROCODE-FIX RESUMPTION (2026-09-07, post-verdict)

The verdict in §7 concluded "interpreter internals, unrepairable". The user
directed resumption with a microcode change. The vendor .asm alignment
(en-exthash-lookup.asm, 804 lines) vs the blob established:

1. The blob's executor = byte-identical to the vendor's documented lookup/
   setup/opcode-fetch machinery: record flags BE16 encodes
   OPC_OFFSET=((flags>>6)&0x1f)<<2 and PARAM_OFFSET=(flags&0x3f)<<2
   (blob w8659-8682 == .asm ehash_record_entry/_action_setup/
   derive_opcode_cursor). Emitter = conformant (0194 readback).
2. The epilogue (blob w9435-9448 == .asm ehash_default_classifier_result):
   EndNIA=0x1a (Pre-BMI Prepare-to-Enqueue), kind nibble IC[0x16]&0xf==2
   + private_result IC[0xb8]!=0 => task.set_fqid + clear + redispatch.
   The STRIP handler carries a DEAD duplicate epilogue (w9487-9500) that
   E-VLAN-1/2's site w9492 patched in vain — the live path always exits
   via w11911 -> loop -> ENQUEUE -> w9435-9448.
3. The .asm has NO post-chain parse-result restore block — the chain's
   length-shift (0x12/0x42) leaves the parser's saved geometry
   (PR fragments saved to fe_state at IC[0xd4], COND at IC[0xc0], PR
   copies r20/r22 from IC[0x30]/[0x38]) stale; the BMI's 0x1a
   pre-enqueue re-proof fails (PRS geometry mismatch, c.f. the 0x8020
   EXTRACTION|PRS_HDR_ERR rationale in the 2026-08-26 rearch record);
   the frame is consumed and dropped before any TX FQ — matching
   frm_cnt=0 exactly. Routed never shifts => never hits it.

FIX MODEL: synthesize a parse-geometry update into the 0x12 and 0x42
handler bodies (updating the affected fe_state/PR fields before the
w11911 exit), using the slack ffffffff fill words in each handler body
(STRIP tail w9501, etc.) with a branch patch — replacement-only, no
insertion (absolute branches forbid re-linking).

OPEN RESEARCH ITEM before writing the patch: WHICH fe_state/PRS fields
the 0x1a routine checks. Plan: a host-side differential dump of the
per-tnum FE workspace (heap-string MURAM region) during a frozen VLAN
flow vs an active routed flow via a new kernel-side debugfs dumper
(0195) — the workspace is host-visible MURAM, so the chain's field
divergence is observable without microcode writes. Then synthesize the
geometry update against the diff.

## 9. GROUND-TRUTH CAPTURES (2026-09-08, probe2/F-239, dev: eth3)

The post-vaddr probe2 capture finally produced live frames — the parser's
actual annotation conventions, up to now guessed:

UNTAGGED (routed TCP, iperf3 10.99.1.116->10.99.2.16):
```
+000: 10 00 80 00 80 00 2d 00 14 51 ff ff ff ff ff ff
+016: ff ff 17 00 ff ff ff 0c ff ff ff 0e 0e ff 22 42
+032: <kg-hash 16b> | +048: frame: e8 f6 d7 00 16 02 | e8 f6 d7 00 16 ac | 0800 | 45 00 ...
```
TAGGED (VID 10, ARP):
```
+000: 10 00 c0 80 00 00 00 00 08 06 0e 9d ff ff ff ff
+016: ff ff ff 00 ff 0e 0e 10 ff ff ff ff ff ff ff 12
+032: <kg-hash> | +048: ... 16 02 | ... 16 ac | 81 00 00 0a | 08 06 ...
```
DECODED CONVENTIONS (the authoritative):
- Frame at window +0x48; parse offsets are frame-relative.
- l2r = 0x8000 untagged; 0xc080 with ONE tag => VLAN flag bits = 0x4080.
- vlan_off[0] = ALIASED to the ethertype position (0x0c) on untagged
  frames -- NOT zeroed. The parser's invalid sentinel = 0xFF (not 0x00).
- Tagged: etype 0x10, everything L3-class +4; untagged: L3 0x0e,
  L4 0x22, nxthdr_off 0x42 (the TCP options end).
- Prior v2's "vlan_off[] = 0" collapse was the opposite of the silicon
  convention (and the 0-vs-0xFF sentinel wrong) -- a material part of
  why v1/v2 read as inert on the board.
- v3 derives from these two templates: POP -> the untagged template,
  PUSH -> the tagged template.

## 10. v3 ROUTINE DERIVATION (in progress, 2026-09-08)

POP (strip): l2r clear 0x4080 (the hi &0xBF, lo &0x7F); vlan_off[0] <- 0x0c
(alias), vlan_off[1] <- 0xFF (invalid); guarded -4 on etype_off/ip_off[0]/[1]/
l4_off/nxthdr_off where the field != 0x00 and != 0xFF (the two invalid encodings).
PUSH (insert): the mirror (+4, l2r |= 0x4080, vlan_off[0] <- 0x0c,
vlan_off[1] <- old vlan_off[0] + 4).
