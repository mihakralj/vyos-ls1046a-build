# Stage-2: FE-VM VLAN fix — frame-HWA annotation rewrite routine (derived)

**2026-09-08. Follows the v3 round, which falsified the IC-mirror hypothesis:
a convention-correct IC parse-result update is inert, so the Pre-BMI (0x1a)
proof validates the frame-side Hardware Annotation (the 48-byte headroom
`[16 B reserved][32 B parse result]` in the RX buffer) — the bytes probe2
captured directly.**

## 1. The addressing model (decoded, not assumed)

The INSERT handler's base arithmetic pins the buffer lineage:

```asm
w9519  sub32 r4 = r4 - r0
w9520  add32 r5 = r28 + r0        ; frame data base = window base + FD offset
w9522  addlane8 r28 = r5 + 0      ; window reg tracks the (re)adjusted base
w9523  memb.read r4 = [r5+1]      ; TCI hi — data-base addressing
w9526  memb.write r1 -> [r5+1]    ; the L2 rebuild writes
```

`r28` is the frame-window register (the `.asm`'s `framewin r28, r28, 1`
maintains it), and the probe2 capture located the PR at the buffer offset
`0xE0` (the window base `vaddr+0xE0` where `vaddr = phys_to_virt(fd_addr)`).
Therefore the HWA PR is reachable with **plain positive offsets off r28**:
`l2r @ +0xE2/E3`, `vlan_off @ +0xF5/F6`, `etype_off @ +0xF7`,
`ip_off @ +0xFB/FC`, `l4_off @ +0xFE`, `nxthdr_off @ +0xFF` — all within
the 11-bit offset field, using the same proven instruction classes as v3.

## 2. The field transformation (from the two ground-truth templates)

| HWA byte | POP (strip outer) | PUSH (insert outer) |
|---|---|---|
| `l2r` hi/lo (+0xE2/E3) | `&= ~0x4080` (hi `&0xBF`, lo `&0x7F`) | `+= 0x4080` (hi `+0x40`, lo `+0x80`) |
| `vlan_off[0]` (+0xF5) | `= 0x0c` (the ethertype alias) | `= 0x0c` |
| `vlan_off[1]` (+0xF6) | `= 0xFF` (the parser's invalid sentinel) | `= 0xFF` |
| `etype_off`/`ip_off[0..1]`/`l4_off`/`nxthdr_off` | guarded `-4` (skip 0x00/0xFF) | guarded `+4` (skip 0x00/0xFF) |

The `l2r` byte-adds use the carry-free lane arithmetic (the untagged
`0x8000 → 0xC080` under the `+0x40/+0x80` and the reverse under the
`&0xBF/&0x7F` masks) — exact per the captures.

## 3. The word tables (40 words per direction)

```
POP2:  0120e0e2 f00003bf 1000e0e2
       0120e0e3 f000037f 1000e0e3
       ebc0000c 1000e0f5
       ebc000ff 1000e0f6
       0120e0f7 b43f0005 e5c000ff b43f0003 f04003fc 1000e0f7
       0120e0fb b43f0005 e5c000ff b43f0003 f04003fc 1000e0fb
       0120e0fc b43f0005 e5c000ff b43f0003 f04003fc 1000e0fc
       0120e0fe b43f0005 e5c000ff b43f0003 f04003fc 1000e0fe
       0120e0ff b43f0005 e5c000ff b43f0003 f04003fc 1000e0ff

PUSH2:  the mirror with f0400340/f0400380 (l2r), f0400304 (shifts),
       the same vlan_off constants.
```

Returns (`xfer14.comp → w11911`) and the entry/displacement math computed
at the layout step, exactly as the v3.

## 4. Open items (flagged for the oracle round)

1. `r28` at the routine's entry: the w9522 keeps `r28` tracking the
   adjusted base through *some* paths; the STRIP path's `r28` behavior at
   the patched exits is the one remaining register-state question. The
   oracle's wire checks distinguish the cases cheaply (offset-shifted
   PR write = still valid frame handling or the inert run).
2. The FD length is decremented by the handlers already (w9466-9468);
   the HWA carries no separate length — out of the scope.
3. Multi-tag flows remain out of the scope (single-tag convention per
   the captures).

## 5. Delivery

Identical machinery to the 0195 tail: the word tables join the
`fsl,firmware-extra` stream (or a second property), the three expect-guarded
call-site patches route the handler exits into the entries, the extended
verify covers the whole image. No new capabilities needed.
## v8/v9 outcome (2026-09-09/10) — supersedes the open items

1. ~~The fork's r28 entry value~~ — CONFIRMED: r28 = raw (the unchanged
   entry window base) at the fork; the completing-path derivation held.
2. ~~Delay-slot register state~~ — CONFIRMED: the delay slots set r5 (the
   params advance: STRIP w9486 li16 r5,8; INSERT w9553 li16 r5,4), consumed
   by the loop bottom w11914 `add32 r2, r2, r5`. The v8 protocol: both
   routines advance r2 internally (+12/+8) and set r5=0 before return.
3. ~~Physical edit vs metadata~~ — RESOLVED: the physical gap was the 2-byte
   TCI write (the strip memmove and the insert tag write never execute on
   the completing path). v9 adds the TCI write to the PUSH (6 words, sourced
   from the record's vlanhdr params). The H2 anchor (r28 = raw-4 at the PUSH
   entry) CONFIRMED by delivery — the TCI landed at frame+14/15 exactly.
4. The POP's HWA l2r clear is LOAD-BEARING (proven by the no-STRIP control:
   tagged l2r → no egress at all). The v6-era "HWA transforms inert" verdict
   was wrong for the l2r specifically.
5. The delta fixup (IC[0xd4] += 4) is REMOVED in v9 — it shifted the INSERT's
   r0 = r4 - IC[0xd4] anchor arithmetic (the PUSH writes landing 4-below-HWA
   = the recurring "inert" pattern).
6. Silicon verdict: the handshake COMPLETES (first ever); remaining defect =
   the sustain wedge (one task parks at the pre-BMI action-6 wait when the
   large data packets start; system-wide; NOT pool exhaustion). See the
   campaign report §8 and fevm-working-mechanics.md §10.
