# P0.6 — Microcode fix synthesis: FE-VM VLAN parse-geometry update routine

2026-09-07. Fix model per runbook §8: the inline 0x12/0x42 handlers mutate the
frame but never rewrite the per-task IC parse-result offset tail
(IC 0x31–0x3F, `frame_epilogue` w12133's consumption window), so the Pre-BMI
(0x1a) proof reads stale geometry and drops the frame (consumed, never
enqueued). Fix = a synthesized update routine placed in the free IRAM tail
(words 12851+, all `0xffffffff` post-`clear_iram`), called from both VLAN
handler exits instead of w11911, returning to w11911.

## Instruction templates (derived from proven blob exemplars)

| Op | Encoding | Exemplar |
|---|---|---|
| `membs.readz rD, [rA+off]` | `0x01200000 \| (D<<16) \| (A<<11) \| off` | `0120d09d` w11918 |
| `membs.write rS, [rA+off]` | `0x10000000 \| (S<<16) \| (A<<11) \| off` | `1000d09d` w11967 |
| `addlane8 rD = rD + imm` (byte, lane 3) | `0xF0400000 \| (D<<16) \| (D<<11) \| (3<<8) \| imm` | `f0431b01` w9482 |
| `xfer14.comp target` | `0xb3ff0000 \| disp14` (executes delay slot, jumps) | `b3ff097a` w9485 |
| fill | `0xffffffff` | w9114 |

−4 in byte arithmetic = `imm 0xFC`; +4 = `imm 0x04`.

## Field map (POP: −4; PUSH: +4)

etht_off 0x33 · etype_off 0x37 · ip_off[0] 0x3B · ip_off[1] 0x3C ·
l4_off 0x3E · nxthdr_off 0x3F (vlan_off pair collapse + l2r flags deferred
to v2 — single-tag flows; the shift is the load-bearing part).

## Routine (placed at word 0x3230 = 12848+2… final position TBD at link time)

Per field (3 words): `readz r0,[r25+off]` → `addlane8 r0 += imm` → `write r0`.

## Call-site patches

| Site | Word | Patch |
|---|---|---|
| STRIP exit | w9485 `b3ff097a` | → `b3ff0d27` (POP entry, word 12852; delay slot w9486 keeps r5=8) |
| INSERT fork exit | w9552 `b3ff0937` | → `b3ff0cf8` (PUSH entry, word 12872; delay slot w9553 keeps r5=4) |
| INSERT body exit | w9673 `b7ff08be` (plain xfer14, no delay slot) | → `b7ff0c7f` (PUSH entry) |

## FINAL routine table (0195, base = word 12852, after the single pad word 12851)

POP entry (12852): eth_off/etype_off/ip_off[0]/ip_off[1]/l4_off/nxthdr_off −4:
`0120d033 f04003fc 1000d033` `0120d037 f04003fc 1000d037`
`0120d03b f04003fc 1000d03b` `0120d03c f04003fc 1000d03c`
`0120d03e f04003fc 1000d03e` `0120d03f f04003fc 1000d03f`
`b3ff3c41 ffffffff` (return → w11911)

PUSH entry (12872): the same six triples with `f0400304` (+4), return
`b3ff3c2d ffffffff`.

Delivered via `fsl,firmware-extra` on the fman0 node in
board/dtb/mono-gateway-dk.dts (U-Boot owns the fman-firmware child node);
0195 streams it after the image + burst pad, substitutes the three
call-site words pre-stream (expect-guarded, fail-open), and verifies
the full extended image.

## Delivery (tooling work, next step)

The routine lies beyond the 12851-word image → qef-patch needs a GROW mode
(extend the DT firmware property by N words + bump the QEF header count),
then the existing 0117 re-stream carries it. The kexec oracle protocol and
post-boot md5 verification are unchanged.

## Oracle PASS gates (board)

1. pkt_count blows past 21 on the frozen-flow repro (both directions).
2. TX-FQ fq_probe frm_cnt rises (frames actually enqueued).
3. Wire: HELGA sink receives well-formed untagged Ethernet; .116 captures
   correct PUSH tags on the return direction.
Failure remainders: freeze persists → the geometry model is wrong, revert
(pristine reboot) and re-derive from a PR dump; freeze clears but wire
corrupt/checksum-bad → shift set wrong, refine field list.