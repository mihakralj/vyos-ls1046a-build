# FMan Vendor Source Extraction — 2026-08-07

Systematic (not grep-driven) extraction of the vendor source this project's
ehash/EXT_HASH work depends on, done after a targeted-grep mistake earlier
the same session (misreading `t_FmPcdCcNodeExtHashInfo`/`ext_hash_add_key`
as the live mechanism when `ExternalHashTableAddKey`/`en_exthash_*` is the
one `cdx_ehash.c` actually calls — see qdrant tag `Phase-0-corrected`).
This doc captures what full-file reading of the real sources established,
distinguishing **confirmed** facts from **genuinely still open** questions.

## Sources used

| What | Where | Notes |
|---|---|---|
| `we-are-mono/ASK`, branch `mt-6.12.y`, commit `a211ea865379362058c6656b9c448e4a7050e93c` | `/home/vyos/kernel-ls1046a-build/reference/ASK-mt-6.12.y/` | Already mirrored locally (sibling repo `kernel-ls1046a-build`, see its `reference/PROVENANCE.md`). No fetch needed. |
| `nxp-qoriq/linux-extras`, commit `b9482121ae39ba7c297870670ecbfefb179af402` — `fm_kg.c` (3242 lines), `fm_cc.c` (7582 lines) | fetched via `gh api repos/nxp-qoriq/linux-extras/contents/...` | The pristine NXP SDK base these files patch against. Not locally mirrored; re-fetch if needed (small, fast). |
| `dpa_app/files/etc/cdx_pcd.xml` | `/home/vyos/kernel-ls1046a-build/reference/ASK-mt-6.12.y/dpa_app/files/etc/cdx_pcd.xml` | The real, declarative production PCD config — ground truth for what vendor's schemes/classifications actually specify. Already locally available; previously only partially read (earlier qdrant entry, 2026-07-13, covered keysize/mask/aging but not the `<combine>` element or full distribution/classification pairing). |
| `.106` live register dump (all 12 KeyGen schemes) | `fman-full-capture.py` output, this session | Cross-checked against the XML to identify which live scheme corresponds to which declared distribution. |

**What was NOT achieved**: a complete pristine copy of `fm_kg.c`/`fm_cc.c`
as customized by `we-are-mono/ASK` (the ASK patch is a *diff* against a base
LSDK tree not available to me — new files in the patch, like `fm_ehash.c`,
are complete; modified files, like `fm_kg.c`/`fm_cc.c`, are diff hunks
only). The `nxp-qoriq/linux-extras` fetch substitutes the pristine
*upstream* SDK base, which is sufficient for everything below since ASK's
own diff to `fm_kg.c` is only 38 lines (see §2) and touches nothing in
`fm_cc.c` relevant to key extraction.

## 1. `cdx_pcd.xml` — the real production distribution/classification pairing

Every IP-flow ehash classification is reached through a `<distribution>` →
`<action type="classification">` → `<classification>` chain (matches
AN4760 §5's documented model exactly). Full pair for TCP/IPv4:

```xml
<classification name="cdx_tcp4_cc" max="512" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0x7fff" hashshift="0" keysize="14" aging="yes"/>
  </key>
</classification>

<distribution name="cdx_tcp4_dist" shared="true">
  <protocols>
    <protocolref name="tcp"/>
  </protocols>
  <key>
    <fieldref name="ipv4.src" header_index="last"/>
    <fieldref name="ipv4.dst" header_index="last"/>
    <fieldref name="ipv4.nextp" header_index="last"/>
    <fieldref name="tcp.sport"/>
    <fieldref name="tcp.dport"/>
  </key>
  <queue count="1" base="0x1010"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <action type="classification" name="cdx_tcp4_cc"/>
</distribution>
```

`mask="0x7fff"` and `keysize="14"` are **byte-for-byte identical** to this
project's own `fe_ehash set 7fff 14 0` configuration. The 5 `<fieldref>`
fields (SIP, DIP, PROTO, SPORT, DPORT) sum to 13 bytes — matching this
project's own key content assumption exactly, MSB-first field order
matching XML declaration order (already established, UNKNOWN-1, 2026-07-13).

Every single one of the 15 distributions in this file (udp4/tcp4/udp6/
tcp6/esp4/esp6/mcast4/mcast6/pppoe/ethernet/tup3udp4/tup3tcp4/tup3udp6/
tup3tcp6/frag4/frag6) carries the **identical**
`<combine portid="true" offset="16" mask="0xF"/>` line, regardless of how
long or short that distribution's own key is (3-field 7-byte keys and
5-field 13-byte keys both get the exact same `offset="16" mask="0xF"`).
This uniformity — offset never scaling with key length — is itself
evidence against `<combine>` inserting a byte into the ehash comparison
key at a length-relative position (see §3).

## 2. `fm_kg.c` — `we-are-mono/ASK`'s own patch (38 lines, complete)

Confirmed: `we-are-mono/ASK`'s customization of `fm_kg.c` is tiny — a
`printk` debug line, and:

```c
+	//bmr
+	knownTmp |= KG_SCH_KN_PORT_ID;
	p_SchemeRegs->kgse_ekfc = knownTmp;
```

inserted unconditionally, right before the final `kgse_ekfc` assignment in
`BuildSchemeRegs()`, with **no guard, no conditional** — every KeyGen scheme
this vendor builds gets `PORT_ID` OR'd into its EKFC, regardless of what the
XML actually declared for that specific scheme. This explains why `.106`'s
live scheme 11 EKFC (`0xe4000000`) has bit 31 set even though scheme 11
turns out to be an Ethernet/L2 classification scheme, not an IP 5-tuple one
— the PORT_ID bit is global, not evidence of a deliberate per-scheme design
choice. (This *also* means an earlier hypothesis floated this session —
that vendor's live EKFC being L2-only proves a fundamental
EKFC-for-dispatch-only / GEC-for-match two-layer architecture — is weaker
than it first looked; scheme 11 is very plausibly just the L2/bridging
distribution, and other schemes carry the real 5-tuple EKFC. See §4.)

## 3. `fm_kg.c` (pristine NXP base) — `BuildSchemeRegs()`, confirmed mechanics

Read in full (not grepped). Key findings:

- **EKFC (`knownTmp`) and GEC (`kgse_gec[]`) are NOT parallel/competing
  extraction mechanisms for the same key.** For each declared XML field,
  the code first checks whether a `KG_SCH_KN_*` "known field" bitmask
  exists for it (`GetKnownProtMask()`); if one exists, it's OR'd into
  `knownTmp` (→ `kgse_ekfc`). GEC is used **only** as the fallback for
  fields with *no* known-field bit (`e_FM_PCD_EXTRACT_NON_HDR`,
  arbitrary-offset "extract by header", etc.) — i.e. GEC is supplementary,
  not an alternate path for fields EKFC already covers. Since IPSRC1,
  IPDST1, PTYPE1, L4PSRC, L4PDST, and PORT_ID **all** have defined known-field
  bits, a standard 5-tuple-plus-portid distribution produces a pure-EKFC
  scheme with `kgse_gec[]` unused for those fields — consistent with, not
  contradictory to, this project's EKFC-only approach.
- Every scheme on `.106` shows the **identical** `kgse_gec0=0x880fa000`
  regardless of its own EKFC/purpose (L2-only scheme 11, 3-tuple schemes
  6/7, 5-tuple-on-"header2" schemes 2/3 all show the same GEC0). A
  per-scheme, per-flow-content GEC value would vary with the scheme's
  actual fields; a uniform value across unrelated schemes points to GEC0
  being consumed by something common to every scheme (declared identically
  across all 15 XML distributions, most likely the `<combine>` element —
  see §5) rather than being part of the flow's own comparison key.
- **Known-field extraction order** (`GetKnownFieldId()`, `orderedArray`
  sort): confirmed, ascending-ID = descending-bit-position = MSB-first.
  `PORT_ID` (bit 31) gets **ID 0**, the lowest ID, so if it participates in
  a known-field-ordered key buffer at all, it sorts **first** — consistent
  with this project's assumption of `portid` as byte 0. (This confirms the
  *ordering rule*, not that PORT_ID necessarily lands in the ehash
  comparison key via this path at all — see §5.)

## 4. `.106` live KeyGen scheme table (all 12 schemes, this session)

| Scheme | `kgse_ekfc` | Decode | `kgse_mode` | `kgse_spc` (live pkts) |
|---|---|---|---|---|
| 0 | `0x800c0200` | PORT_ID\|IPTOS_TC1\|IPSEC_NH... | `0x8b000006` | 0 |
| 1 | `0x800c0200` | (same as 0) | `0x8a000006` | 0 |
| 2 | `0x8000e006` | PORT_ID\|IPSRC2\|IPDST2\|PTYPE2\|L4PSRC\|L4PDST | `0x89000006` | 316,206 |
| 3 | `0x8000e006` | (same as 2) | `0x88000006` | 394,151 |
| 4 | `0x8000c086` | PORT_ID\|IPSRC2\|IPDST2\|IPSEC_NH | `0x87000006` | 0 |
| 5 | `0x8000c086` | (same as 4) | `0x86000006` | 0 |
| 6 | `0x801c0000` | PORT_ID\|IPSRC1\|IPDST1\|PTYPE1 (3-tuple, no ports) | `0x85000006` | 5,441 |
| 7 | `0x801c0000` | (same as 6) | `0x84000006` | 148,551 |
| 8 | `0x80006002` | PORT_ID\|IPDST2\|PTYPE2\|L4PDST | `0x83000006` | 316,206 |
| 9 | `0x80004082` | PORT_ID\|IPDST2\|IPSEC_NH\|IPPID | `0x82000006` | 0 |
| 10 | `0xa6000000` | PORT_ID\|MACSRC\|TCI1\|ETYPE | `0x81000006` | 0 |
| 11 | `0xe4000000` | PORT_ID\|MACDST\|MACSRC\|ETYPE | `0x80000006` | 1,225,734 |

All 12 confirm `kgse_mode` bit `0x00000006` = `AC_CC` (matches this
project's own AC_CC encoding exactly) with the scheme-select bits in the
top byte differing per scheme. Schemes 2/3 (using `IPSRC2`/`IPDST2` —
"header 2", i.e. the *inner/tunneled* header fields, not `IPSRC1`/`IPDST1`)
carry real, substantial live traffic (316K–394K packets) — these are very
plausibly the actual per-flow 5-tuple-equivalent ehash schemes for this
port's traffic mix, using tunnel-inner fields rather than the outer header
(worth checking whether `.106`'s live traffic on this port is genuinely
tunneled, or whether "header 2" is simply how this particular parse profile
labels the single IP header present). Scheme 11 (L2 fields, most traffic of
all) is very plausibly the primary bridging/L2-classification scheme for
this port, not the IP-flow ehash path at all.

**Not yet done**: determining definitively which scheme(s) correspond to
which of `cdx_pcd.xml`'s 15 distributions by name (would need to correlate
`kgse_fqb` base FQIDs against the XML's declared `<queue base="0x10N0"/>`
values, not yet attempted).

## 5. OPEN QUESTION — `<combine portid="true" offset="16" mask="0xF"/>`

**This is not resolved and should not be treated as resolved.** It is the
single most consequential remaining unknown for this project's ehash key
format, because it directly bears on whether `F-163`'s "PORT_ID as byte 0
of a 14-byte key" model is correct.

What's confirmed:
- `<combine>` is a **distribution**-level element (KeyGen scheme dispatch
  stage), not a classification/hashtable-level element. `fm_cc.c` (the
  CC-tree/ehash-table-node source) has zero references to `combine` or
  `portid` anywhere in its 7582 lines.
- It's declared identically (`offset="16" mask="0xF"`) regardless of the
  distribution's own key length (3-field 7-byte keys and 5-field 13-byte
  keys get the same values) — inconsistent with "insert portid at the next
  free byte after the declared key fields."
- AN4760's documented 5-stage KeyGen pipeline (Build Key → Hash → Shift&Mask
  → **Logical OR with "OR Data Vector"** → Base Addition) explicitly
  includes an OR-into-result stage separate from the raw key-building stage,
  which AN4760 says exists in silicon even where unused in its own worked
  examples. `<combine>`'s shape (a bit-masked value ORed in at a fixed
  offset, uniform across distributions) matches this stage far better than
  it matches "prepend a byte to the extracted key."

What that would mean if true: `<combine portid offset=16 mask=0xF>`
configures the **FQID/hash-result OR-vector stage**, not the raw extracted
key bytes at all — meaning the live-packet-side key the EXT_HASH FE-VM
microcode compares against DDR might never include a PORT_ID byte the way
this project assembles it, and the actual ehash comparison key would be the
plain 13 bytes (SIP\|DIP\|PROTO\|SPORT\|DPORT), with `keysize="14"`'s extra
byte needing a different explanation (DDR-side alignment padding, a
reserved/flags byte, etc.) than "portid prepended."

What's confirmed on the *other* side (supporting F-163's model as-is):
`cdx_ehash.c`'s `fill_key_info()` (`we-are-mono/ASK`, examined earlier this
session and previously) explicitly builds `struct ipv4_tcpudp_key` in
software with `portid` as the literal first field before calling
`ExternalHashTableAddKey()` — i.e. **the DDR-side table-insertion key
genuinely does carry portid as byte 0**, built entirely in CMM userspace
code, with no KeyGen/`<combine>` involvement at all on the insert side.

**The genuine open question is therefore narrower than "is portid in the
key"**: it's whether the *live-packet-side* extraction (whatever the
EXT_HASH FE-VM microcode uses to build its comparison key from an
in-flight frame, for comparison against what's already sitting in DDR)
independently reconstructs the same portid-prefixed 14-byte layout — via
KeyGen/EKFC (this project's assumption) — or via some other, not-yet-located
mechanism. Neither `fm_kg.c` nor `fm_cc.c` (both now fully read) show a
live-packet extraction path that obviously matches CMM's insert-side
software construction. This may live in `fm_port.c`'s `SetPcd()` (not yet
read in full this session) or may genuinely be the KeyGen/EKFC path after
all — **not yet distinguished**.

**Recommended next step** (unchanged from the session's prior "Candidate A"
plan, now sharpened): the hardware CRC-64 match technique that closed
UNKNOWN-1 for the old 13-byte key (2026-07-13) needs to be re-run for
`EKFC=0x801C0006` specifically — but this doc's finding adds a second thing
to check in that same test: capture the KG hash for a controlled frame with
`EKFC=0x001C0006` (**no** PORT_ID bit) and compare against the *same*
frame's hash with `EKFC=0x801C0006`. If the two hashes differ in exactly
the way consistent with a 13-vs-14-byte MSB-first key, that's strong
evidence PORT_ID genuinely reaches the KG extraction stage as this project
assumes. If they're identical, that's strong evidence PORT_ID never reaches
the raw extracted-key buffer via EKFC at all — pointing squarely at the
`<combine>`/OR-vector theory instead, and meaning `F-163`'s key format
needs revisiting.

## Cross-references

- Full XML: `/home/vyos/kernel-ls1046a-build/reference/ASK-mt-6.12.y/dpa_app/files/etc/cdx_pcd.xml` (locally available, 525 lines, no fetch needed).
- `fm_kg.c` (`we-are-mono/ASK` diff, 38 lines): `/home/vyos/kernel-ls1046a-build/reference/ASK-mt-6.12.y/patches/kernel/002-mono-gateway-ask-kernel_linux_6_12.patch`, search `Peripherals/FM/Pcd/fm_kg.c`.
- `fm_ehash.c`/`fm_eh_types.h`/`fm_ehash.h` (complete new files, `we-are-mono/ASK`): same patch file, search each filename — already cross-referenced against this project's own implementation in `arch/fman-microcode-210-programming-reference.md` §10 (bit-exact match confirmed, 2026-08-07).
- `arch/fman-microcode-210-programming-reference.md` §4.3 (EKFC bit table), §7.2 (EXT_HASH FE layout), §10 (DDR ehash flow store) — this doc supplements, does not replace, those sections.
- qdrant tags: `task-26-status`, `vendor-EKFC-L2-fields-not-5-tuple`, `combine-portid-open-question` (this entry), `2026-08-07`.
