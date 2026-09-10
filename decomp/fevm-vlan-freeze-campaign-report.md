# FE-VM VLAN inline-path freeze — complete campaign report

**2026-09-07 → 2026-09-08 · Board .185, image lineage 0826 → 1901 (p0-fevm-vlan-leak
branch)
· This document is the consolidated record of the entire P0 investigation.**

The bug: the FMan's FE-VM (microcode 210.10.1, 12,851 words, md5
`6f23090a3d5ae8b302ea41fd90a14d4d`) silently kills hardware-offloaded
VLAN flows that use the *inline* ehash opcode path (0x12 STRIP_ALL_VLAN /
0x42 INSERT_VLAN_HDR). Records freeze at ≤21 HITs (the 5+tnums shape,
reproduced 3× historically and ~10× this campaign); the engine keeps
consuming every frame — HIT counters and the FPM per-task counters tick on
retransmits — but **nothing is ever enqueued to any TX FQ**
(`fq_probe frm_cnt=0`, producer-side). TCP handshakes complete because the
early packets ride the software path before `HW_OFFLOAD` latches. Plain
routed and NAT flows on the identical dual-lane 46-byte-key records sustain
(40 Mbit/s, 17,000+ HITs, zero retransmits). Production is unaffected:
shipping VLAN forwarding uses Option A (CC-leaf → NADEN → HMTD).

---

## 1. The delivery mechanism (why a microcode fix is shippable at all)

The blob arrives via U-Boot's SPI injection, but every cold boot the
mainline `fman_init()` calls `clear_iram()` and our board patch 0117
(`load_fman_ctrl_code()`) re-streams the whole image from the DT
`fsl,firmware` property into IRAM with a full verify pass. That gives us a
per-boot, host-owned full-write window. The campaign's oracle used the
equivalent fast path: `qef-patch.py` word substitutions on the live DTB +
kexec (0117 re-streams on kexec), every round md5-verified in IRAM after
boot (rounds: `722d4f90`, `db1f39cb`, `1b9cec17`, `8863d1a2`). No SPI
writes, no redistributed blobs — the production form is a word-diff table
plus a streamed tail (0195), gated on expect-values, fail-open.

## 2. The falsification chain

| Round | Change | Result |
|---|---|---|
| P0b | Freeze repro + 0192 free-list dump | Reproduced (records dead at 21/19/19/13/13/9); the host-visible 5+tnums free-list array is **byte-identical to pristine** during the freeze → the exhausted resource is engine-internal |
| P0c | w9482/w9549 counter increments `f0431b01→f0431b00` | Freeze persists |
| P0c2 | Both VLAN handler critical sections (w9477–9484, w9544–9551, 16 words) NOPed | Freeze persists; strip/push still function (handshakes complete) — the sections are dead/no-op code on the real path; the w9460 fork is the real STRIP exit |
| P0c3 | w11965 unit16 checksum retry NOPed | Freeze persists |
| P0c4 | ENQUEUE counter increments w9397/w9417 zeroed | Freeze persists |
| E-VLAN-1/2 (prior) | Epilogue reset guards toggled | Freeze persists (and the site w9492 turned out to be a dead duplicate epilogue, w9487–9500) |
| 0194 | Full record-byte readback | **Record is byte-perfect** per the vendor cdx model (opcodes `[12 21 41 01]`/`[21 42 41 01]`, VID10, `op_flags=0`, DSCP 0, L2 `0x4000000e` + correct MACs + `0x0800`, MTU `0x5dc`, FQIDs `0x2bb`/`0x2ba` matching fq_probe) — the emitter is exonerated; the failure is interpreter-internal |
| 0193 | Engine-state reader (FPM `ts[128]`, IMEM `idata`) | Tasks park in ENQUEUE-action state `0x81000006` whose counter field still ticks — not parked, not faulted (all error registers 0); IMEM `idata` is **address-indexed, no fetch mirror** (E-HM17's "stuck read" was iadd parked at word 1) |
| 0196 | FE-pool context dump | The pool is transient, not an IC mirror — the engine IC is not host-observable through it |
| v1 (0195) | ±4 offset-tail routine + both real exits (w9460 fork + w9485) | Freeze persists |
| v2 (0195) | + guarded vlan_off collapse | Freeze persists — and the collapse semantics were later proven *inverted* (§4) |

## 3. The vendor-reference fact

Qdrant 2026-09-01: the working vendor DUT (.116, ASK 1.0) does **not**
forward routed VLAN through inline FE-VM ehash opcodes — it provisions
dedicated Offline-Host ports with HM queues. The inline 0x12/0x42
route-to-go path is a vendor-untested corner of 210.10.1. The executor
machinery (record flags → OPC_OFFSET/PARAM_OFFSET decode, action setup,
epilogue `ehash_default_classifier_result` with the `IC[0x16]&0xf==2` +
`IC[0xb8]` FQID → `task.set_fqid` → redispatch to Pre-BMI 0x1a) is
byte-identical to the vendor's documented disassembly — unimpaired.

## 4. The ground-truth captures (probe2/F-239, finally live)

The capture road was long: the F-239 port needed the anchor moved past the
`vaddr = phys_to_virt(addr)` assignment (the F-216 hash block reads the
unassigned stack slot on this tree — every prior "idle" was the
uninitialized-pointer guard), the fixup made idempotent-correct against the
CI's committed post-patches state, and the buffer re-gated to eth3. The
first real captures (image 1901):

**Untagged routed TCP** (window base vaddr+0xE0, frame at +0x48):
```
+000: 10 00 80 00 80 00 2d 00 14 51 ff ff ff ff ff ff
+016: ff ff 17 00 ff ff ff 0c ff ff ff 0e 0e ff 22 42
+032: <kg-hash 16b>  +048: e8 f6 d7 00 16 02 | e8 f6 d7 00 16 ac | 0800 | 45 00 …
```

**Tagged VID-10 ARP**:
```
+000: 10 00 c0 80 00 00 00 00 08 06 0e 9d ff ff ff ff
+016: ff ff ff 00 ff 0e 0e 10 ff ff ff ff ff ff ff 12
+032: <kg-hash>  +048: … 16 02 | … 16 ac | 81 00 00 0a | 08 06 …
```

The decode that follows (offsets frame-relative, base +0x48):

| Convention | Value | Consequence |
|---|---|---|
| `l2r` VLAN flag | `0x8000` (0 tags) → `0xc080` (1 tag) = **0x4080** | POP clears, PUSH sets `0x4080` |
| `vlan_off[0]` alias | **0x0c on untagged frames** (the ethertype position) — not zeroed | v2's "collapse to 0" was the opposite of the silicon convention |
| Invalid sentinel | **0xFF** (not 0x00) for absent headers/offsets | v2's `= 0` collapse wrote the wrong sentinel |
| Offsets | tagged: etype 0x10, L3-class 0x12…; untagged: L3 0x0e, L4 0x22, nxthdr_off 0x42 | −4 (POP) / +4 (PUSH) on every L3-class offset ≥ the tag |
| `nxthdr` | inner EtherType for tagged (0x0806), L4-related for untagged TCP | Follows the payload, not the tag |

## 5. The v3 routine (derived from §4, ready to ship)

POP entry (strip): clear `l2r` 0x4080 (hi `&0xBF`, lo `&0x7F` byte-ANDs);
`vlan_off[0] ← 0x0c` (the alias), `vlan_off[1] ← 0xFF` (the real sentinel);
guarded −4 on `etype_off 0x37`, `ip_off[0..1] 0x3B/3C`, `l4_off 0x3E`,
`nxthdr_off 0x3F` where the field is neither 0x00 nor 0xFF. PUSH entry: the
mirror (+4, `l2r |= 0x4080`, `vlan_off[0] ← 0x0c`, `vlan_off[1] ← old[0]+4`).
Same delivery: `fsl,firmware-extra` property on the fman0 node, streamed by
0117 after the burst pad (words 12852+), three expect-guarded call-site
patches (w9460, w9552, w9673 → the two entries), full extended verify.

## 6. The CI/process debt accumulated (must be absorbed on dpaa1/main)

1. `KERNEL_VERSION` env pin: `ci-build-packages.sh` resolved the kernel
   version from vyos-build's fman defaults.toml directly, ignoring the env —
   the upstream 6.18.48→6.18.50 bump mid-day desynced the kernel deb and
   the live-build package list. Fixed on the branch in
   `ci-build-packages.sh`, `ci-compile-mono-dtb.sh`,
   `ci-setup-vyos-build.sh` (defaults.toml kernel_version pin).
2. The kernel-cache re-clone path ran `rm -rf` on the tmpfs *mountpoint* —
   it destroyed the persistent 5.1 GB kernel cache (recoverable via
   bootstrap; the patch files remain the source of truth) and left the
   runner with a shallow cache whose `patch -p1` fuzz chain diverged the
   tree enough to break a correctly generated 0193 patch.
3. DTB compile sparse-clones the pinned tag; the tag publication raced one
   build (transient).
4. The runner's broker died without recovery (SocketException at job
   boundary) — jobs queue forever until `systemctl restart` of the runner
   service.
5. `/dev/mem` read+mmap of FMan CCSR = EFAULT/EACCES on 6.18.48 (the whole
   SoC /proc/iomem listing degenerates) — host register pokes must go
   through kernel debugfs; 0193/0194/0196/F-239/style probes are the
   pattern.
6. Hubitat smart-plug API returns empty HTTP replies while still executing
   toggles — verify by ping, retry ON aggressively, never trust curl 52.
7. `ic_probe` debugfs = a live landmine (kernel panic + auto-reboot on
   read).

## 7. The standing conclusion

The inline FE-VM VLAN path on 210.10.1 is consumptively broken for bulk
forwarding; with the ground-truth captures the *most likely* residual
mechanism is the parse-geometry contract (the offsets/l2r/vlan_off the
Pre-BMI proof validates), and the v3 routine is the first fix derived from
silicon-measured conventions rather than guesswork. Whether v3 closes the
freeze or the proof reads the frame-side HWA instead, the campaign has
converted the bug from an uncharacterizable freeze into a fully
instrumented, reproducible, falsifiable research subject — with a complete
NXP-report package and a verified boot-time patch-delivery design,
irrespective of the final verdict.

## 8. The 2026-09-09/10 resolution arc (v7→v8→v9): root causes found, frames delivered

The campaign closed. Three successive one-word-class fixes, each derived from
an instrument invented the same day, took the path from "freeze at ~21
packets, zero delivery" to "handshake completes, frames deliver end-to-end."

### 8.1 The live-bisection instrument (no image rebuilds)

`fe_flow` debugfs verb adds [01]-only records natively (46-byte dual-lane
key, opc_off=56, param_off=72); `/dev/mem` (STRICT_DEVMEM off) rewrites the
opcode chain + params per variant; an iptables DROP rule on the DUT forces
TCP SYN retransmits as probes; conntrack reveals each probe's ephemeral
sport; the fqid arg targets any FQ. Success signals: `fe_ehash_stats`
pkt_count (+1/HIT), task-status park check (static `0x81000006` vs the
cycling task), qman `ErrInt: Invalid Enqueue Queue` (the misalignment
fingerprint). FMan-direct egress bypasses kernel packet taps — but the WIRE
sees everything: redirecting the record fqid to `0x2bb` (eth3's offload TX
FQ) egresses processed frames toward `.116` where `tcpdump -i eth3` captures
the exact bytes.

### 8.2 Root cause #1: the params-cursor protocol (v7/v8)

The opcode loop bottom (w11912/w11914) advances r17 by 1 AND r2 by r5 every
iteration. Each handler sets r5 = its param-block size via its EXIT-BRANCH
DELAY SLOT: STRIP early-exit w9485→w9486 `li16 r5,8`; INSERT fork-exit
w9552→w9553 `li16 r5,4`; the STRIP fork w9460 delay slot sets r3=4 NOT r5
(stale); the INSERT full-exit w9673 has no delay slot (stale). The emitter
(fman_pcd.c) lays params assuming POP=12/TTL=4/PUSH=8/L2=20/ENQ=16 — the
vendor TTL and L2 advances already matched; only the two handlers our
routines replace were mismatched (STRIP 0-or-8, INSERT 4, stale-r5
variance). Every fix round since v1 inherited these misalignments: with the
emitter's records, the INSERT read the emitter's pad ZEROS as num_hdrs=0 —
the degenerate INSERT that parked the task. That was the historical freeze.

Fix: both routines advance r2 internally (POP `addi16 r2,+12` = e842000c,
PUSH `addi16 r2,+8` = e8420008) and set `li16 r5,0` (ebc50000) before their
returns — deterministic advances regardless of vendor exit path. Result: the
chain executes end-to-end, the ENQ reads the valid fqid (no IEQ), frames
egress. Register-safety: r0-only clobbers + the deliberate r2/r5; the one
downstream r5 read (w12116) is locally rebound at w12112 (r5 = r0<<28) —
zero unbound consumers.

### 8.3 Root cause #2: the physical TCI write (v9)

The v8 egress wire capture: [L2-rewritten MACs ✓][ORIGINAL VID10 tag ✗][IP
untouched ✓]. The L2 handler's MAC rewrite works; nothing ever writes the
tag TCI — the strip's memmove (dead section w9461-84) and the insert's tag
write (w9523-26) never execute on our path. The no-STRIP control
([21 42 41 01], l2r stays tagged) produced NO egress at all — proving the
POP's HWA l2r clear is LOAD-BEARING (it steers the vendor INSERT onto the
completing path). HELGA's RX silence fully explained: VID10-tagged frames
toward a VID20 vSwitch = dropped at the vSwitch.

Fix: the PUSH routine gains 6 words before its r2/r5/return, sourcing the
TCI from the record's own vlanhdr params (no hardcoded VID):
`04001004` memw.read r0,[r2+4]; `dbc0c01d` lsr32i r0,24; `1000e122`
memb.write r0,[r28+0x122] (frame+14); `04001004` memw.read r0,[r2+4];
`dbc0801d` lsr32i r0,16; `1000e123` memb.write r0,[r28+0x123] (frame+15).
The H2 anchor (r28 = raw-4 at the PUSH entry, frame = raw+0x110) confirmed
correct with zero calibration. Also removed: the v6-era delta fixup
(IC[0xd4] += 4) — a phantom-epilogue fix that shifted the INSERT's anchor
arithmetic (the recurring "inert writes" pattern).

### 8.4 The v9 verdict and the remaining sustain wedge

The emitter's own records, iperf3 UDP flow .116→HELGA: the TCP control
handshake COMPLETED — `ESTABLISHED`, the first time in the campaign. Forward
record climbed 12→21, reply 5→11, zero IEQ, bidirectional delivery, egress
frames verified complete ([rewritten MACs][VID20 TCI][untouched IP]).

The remaining defect: ONE task parks at 81000006 (pre-BMI action-6 wait) and
wedges the pipeline SYSTEM-WIDE (a second flow's control connection breaks
instantly). fe_pool: 11 available + 1 enqueued — NOT pool exhaustion. The
correlation: 78-byte control frames deliver fine; the stall hits when the
~1470-byte UDP data packets start. Sustain problem, not correctness.

Hypothesis space (next campaign): (a) the pre-BMI checksum/DMA op for large
frames (the small-frame ops complete; a large-frame op never does — suspect
the DMA length/window metadata); (b) the unit12 checksum-unit wedge (the
parked task's op holds the unit; the RFC-1624 folds skipped by the w9552
redirect may leave the unit's accounting inconsistent); (c) the L2 handler's
second checksum group (w9585-9625) wedging on large frames. Diagnostics:
parked-task frozen-frame capture (muram_hex), TCP-only flow sustain, and the
v8-delta-0 vs v9-delta-−4 asymmetry probe.

### 8.5 The protocol contract (final, for any future fix round)

POP routine: [HWA transforms 40w — the l2r clear is load-bearing][length
fixup -4 (IC[0xc0], vendor-verbatim)][r2 += 12][r5 = 0][return → w11911].
PUSH routine: [H2 transforms 40w][TCI write 6w from params][r2 += 8][r5 =
0][return → w11911]. All displacements position-derived; the scripted
ALL-PASS verification gate caught three hand-arithmetic slips across v7-v9
before they could ship (including a return landing at w11910 instead of
w11911 — harmless but sloppy). The vendor TTL/L2/ENQ handlers need no
changes: their r5 delay-slot advances (4/20/terminal) already match the
emitter's layout.
