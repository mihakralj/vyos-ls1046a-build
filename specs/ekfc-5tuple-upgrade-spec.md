# EKFC 5-Tuple Upgrade — FMan KeyGen Flow-Key Architecture for ASK2

**Status:** Draft v1.0. 2026-07-10.
**Branch:** dpaa1
**Replaces:** ad-hoc sed patch `F-043` in `ci-setup-kernel.sh` (EKFC=0x00180006).
**Depends on:** `ask2-rewrite-spec.md` §13, `dpaa1-afxdp-modernization-spec.md` §5.2.

---

## 0. Purpose and scope

This spec defines the target FMan KeyGen extraction configuration for ASK2's
hardware offload path. It covers:

- Which EKFC register value to use and why
- The exact key buffer byte layout produced by the silicon
- How that layout maps to flow insertion in `ask.ko` and `fe_flow_add`
- The mandatory silicon-verification experiment that must precede production use
- The changes required across all affected files

It does **not** cover:

- The FE-VM ehash architecture (see `ask2-rewrite-spec.md` §13.5 and Qdrant
  entries tagged `FE-VM`, `ehash`, `en_exthash_node`)
- The kgse_hc (Hash Command) register — hash bucket computation is an orthogonal
  concern addressed separately once the extraction order is confirmed
- IPv6 flows — the field-width variability of IPSRC1/IPDST1 (4 bytes for IPv4,
  16 bytes for IPv6) makes IPv6 a separate design decision; this spec is IPv4-only

---

## 1. Background: the FMC vs EKFC distinction

The FMan KeyGen scheme has two independent extraction mechanisms that operate on
different hardware registers and produce different key buffer layouts:

**EKFC path (hardware known-fields):**
- Register: `kgse_ekfc` (offset 0x104 in `fman_kg_scheme_regs`)
- Each set bit enables extraction of one silicon-known protocol field
- The key buffer is assembled by the hard parser in an **immutable silicon-defined
  order** — the software cannot reorder the fields
- Used by: ASK2 dpaa1 branch (current), mainline DPAA1 RSS hashing
- Latency: minimum — the hard parser pre-populates all field offsets in the Parse
  Result; EKFC reads them directly with no per-field decode step

**GEC path (generic extract commands):**
- Registers: `kgse_gec[0..7]` (offsets 0x120–0x13C)
- Each GEC entry specifies an arbitrary byte offset + length within the frame or
  Parse Result, in software-declared order
- The key buffer is assembled in the order declared by the software
- Used by: FMC-configured schemes (NXP SDK `cdx_pcd.xml` + `dpa_app`)
- Latency: slightly higher — per-GEC-entry byte-range copy through the IC

The two mechanisms occupy different registers in the same scheme entry. They can
in principle coexist (EKFC bytes first, GEC bytes appended after), but this
combination is architecturally inadvisable:
- Key buffer length becomes a function of EKFC-extracted length + GEC-extracted
  length, with the EKFC prefix length varying by protocol (IPv4 vs IPv6)
- The NXP SDK never uses the combined mode for its primary classification path
- ASK2 does not need GEC; every field in the target 5-tuple is a hard-parser
  known field with a defined EKFC bit

**Decision:** ASK2 uses EKFC exclusively. GEC registers are left at zero.

---

## 2. Problem statement with the current EKFC=0x00180006

The current configuration (post F-043) extracts a 4-tuple:

```
EKFC = 0x00180006 = KG_SCH_KN_IPSRC1 | KG_SCH_KN_IPDST1 |
                     KG_SCH_KN_L4PSRC | KG_SCH_KN_L4PDST
```

Key buffer (12 bytes, descending-bit-position order):
```
[0-3]   SIP  (bit 20, IPSRC1)
[4-7]   DIP  (bit 19, IPDST1)
[8-9]   SPORT (bit 2, L4PSRC)
[10-11] DPORT (bit 1, L4PDST)
```

**Deficiency:** no IP protocol byte. A TCP flow and a UDP flow sharing the same
SIP:SPORT→DIP:DPORT 4-tuple produce an identical 12-byte key. The hardware treats
them as the same flow. In practice this collision is vanishingly rare on router
workloads, but it is architecturally incorrect and it differs from NXP's own
production-proven key format.

The NXP ASK 1.x SDK (cdx_pcd.xml + fill_ehash_key_info in cdx_ehash.c) uses a
**13-byte 5-tuple**: SIP(4)+DIP(4)+PROTO(1)+SPORT(2)+DPORT(2). The protocol byte
(`ipv4.nextp`) is extracted via an FMC GEC entry, not via EKFC, because the SDK
uses the GEC path end-to-end. However, the same protocol byte is available in the
EKFC table as **bit 18 (KG_SCH_KN_PTYPE1, mask 0x00040000)**.

---

## 3. Target configuration: EKFC=0x001C0006

```
EKFC = 0x001C0006 = KG_SCH_KN_IPSRC1  (bit 20, 0x00100000)
                  | KG_SCH_KN_IPDST1  (bit 19, 0x00080000)
                  | KG_SCH_KN_PTYPE1  (bit 18, 0x00040000)
                  | KG_SCH_KN_L4PSRC  (bit  2, 0x00000004)
                  | KG_SCH_KN_L4PDST  (bit  1, 0x00000002)
```

**One bit added** relative to 0x00180006: bit 18 (KG_SCH_KN_PTYPE1), which
extracts the IP protocol/next-header byte from the first IP header (1 byte,
e.g. 0x06=TCP, 0x11=UDP, 0x2F=GRE).

### 3.1 Expected key buffer layout

The assembly order rule from the EKFC Qdrant entry is: **descending bit position**,
highest set bit first. For EKFC=0x001C0006 the set bits are 20, 19, 18, 2, 1:

```
Byte offset  Width  Field    EKFC bit  Value example (TCP, 10.99.1.106→10.99.2.200:5201)
-----------  -----  -------  --------  ------------------------------------------------
[0-3]          4B   IPSRC1   bit 20    0x0A 0x63 0x01 0x6A  (10.99.1.106)
[4-7]          4B   IPDST1   bit 19    0x0A 0x63 0x02 0xC8  (10.99.2.200)
[8]            1B   PTYPE1   bit 18    0x06                  (TCP=6)
[9-10]         2B   L4PSRC   bit  2    0xD6 0xD9             (55001)
[11-12]        2B   L4PDST   bit  1    0x14 0x51             (5201)
```

**Total: 13 bytes.** This is byte-for-byte identical to NXP's production-proven
`fill_ehash_key_info` output for the same packet, making the extraction result
interchangeable with SDK-generated keys.

### 3.2 Extraction order uncertainty and the mandatory verification experiment

**CRITICAL CAVEAT:** The Qdrant record contains conflicting evidence about the
silicon's extraction order:

- The `FMAN_KEYGEN_EKFC_LAYOUT` entry (from kernel source analysis, 2026-07-10)
  states **descending bit position** as the assembly rule.
- The 2026-07-10 deep analysis entry (`F-046`) argues for a **size-grouped** order
  (all 4-byte fields first, then smaller fields), based on the 2026-07-04 empirical
  HIT result with EKFC=0x00180206.

The two models produce **different byte layouts** when a 1-byte field (PTYPE1, bit
18) is interleaved between two 4-byte fields (IPSRC1 bit 20, IPDST1 bit 19):

| Order model | Layout |
|---|---|
| Descending bit position (kernel source analysis) | SIP(4)+DIP(4)+PROTO(1)+SPORT(2)+DPORT(2) = 13 bytes |
| Size-grouped (F-046 empirical hypothesis) | SIP(4)+DIP(4)+SPORT(2)+DPORT(2)+PROTO(1) = 13 bytes |

Both produce 13 bytes. The **byte at offset 8** is either `0x06` (PROTO, descending
model) or the high byte of SPORT (size-grouped model). These are observably
different for any TCP or UDP packet.

**The extraction order MUST be empirically verified before production use.**
See §6 for the verification experiment.

### 3.3 Why not use bit 18 (PTYPE1) as the only change

Bit 18 is PTYPE1 = IP protocol/next-header from the **first** (outer) IP header.
For plain IPv4 TCP/UDP this is the correct field. For GRE-encapsulated or
IPsec-tunneled frames, the outer protocol byte would be 0x2F (GRE) or 0x32 (ESP),
and the inner TCP/UDP protocol byte would need PTYPE2 (bit 13). The current v1.0
scope is plain IPv4 TCP/UDP forwarding; PTYPE1 is correct for that scope.

---

## 4. EKFC register semantics reference

Source: `linux-kernel fman_keygen.c` (NXP copyright 2017, mainline),
confirmed via Qdrant entry `FMAN_KEYGEN_EKFC`.

### 4.1 Complete 32-bit field table

```
Bit  Mask        Constant              Width  Protocol field
---  ----------  --------------------  -----  ----------------------------------------
31   0x80000000  KG_SCH_KN_PORT_ID       1B   Ingress port ID
30   0x40000000  KG_SCH_KN_MACDST        6B   Ethernet destination MAC
29   0x20000000  KG_SCH_KN_MACSRC        6B   Ethernet source MAC
28   0x10000000  KG_SCH_KN_TCI1          2B   VLAN TCI #1 (outermost)
27   0x08000000  KG_SCH_KN_TCI2          2B   VLAN TCI #2 (QinQ inner)
26   0x04000000  KG_SCH_KN_ETYPE         2B   EtherType
25   0x02000000  KG_SCH_KN_PPPSID        2B   PPPoE Session ID
24   0x01000000  KG_SCH_KN_PPPID         2B   PPP Protocol ID
23   0x00800000  KG_SCH_KN_MPLS1         4B   MPLS label entry #1
22   0x00400000  KG_SCH_KN_MPLS2         4B   MPLS label entry #2
21   0x00200000  KG_SCH_KN_MPLS_LAST     4B   MPLS label entry (last)
20   0x00100000  KG_SCH_KN_IPSRC1        4B   IP source (outer), IPv4 or 16B IPv6
19   0x00080000  KG_SCH_KN_IPDST1        4B   IP destination (outer), IPv4 or 16B IPv6
18   0x00040000  KG_SCH_KN_PTYPE1        1B   IP protocol/next-header (outer)  ← NEW
17   0x00020000  KG_SCH_KN_IPTOS_TC1     1B   DSCP/ECN (outer)
16   0x00010000  KG_SCH_KN_IPV6FL1       3B   IPv6 flow label (outer)
15   0x00008000  KG_SCH_KN_IPSRC2        4B   IP source (inner tunnel)
14   0x00004000  KG_SCH_KN_IPDST2        4B   IP destination (inner tunnel)
13   0x00002000  KG_SCH_KN_PTYPE2        1B   IP protocol (inner tunnel)
12   0x00001000  KG_SCH_KN_IPTOS_TC2     1B   DSCP/ECN (inner tunnel)
11   0x00000800  KG_SCH_KN_IPV6FL2       3B   IPv6 flow label (inner tunnel)
10   0x00000400  KG_SCH_KN_GREPTYPE      2B   GRE protocol type
 9   0x00000200  KG_SCH_KN_IPSEC_SPI     4B   IPsec SPI (from ESP/AH)
 8   0x00000100  KG_SCH_KN_IPSEC_NH      1B   IPsec next header (AH only)
 7   0x00000080  KG_SCH_KN_IPPID         2B   IP fragment identification
6-3  (reserved — NO defined constants; setting these bits is undefined behaviour)
 2   0x00000004  KG_SCH_KN_L4PSRC        2B   L4 source port (TCP/UDP/SCTP)
 1   0x00000002  KG_SCH_KN_L4PDST        2B   L4 destination port (TCP/UDP/SCTP)
 0   0x00000001  KG_SCH_KN_TFLG          1B   TCP flags byte
```

### 4.2 EKDV default-value configuration

When a field is enabled in EKFC but the parsed frame does not contain it (e.g.
L4PSRC on a non-TCP packet), the hardware substitutes a default value from either
`kgse_dv0` or `kgse_dv1` as controlled by `kgse_ekdv`.

Default value wiring used by mainline `fman_keygen.c`:
- IPSRC1, IPDST1: `kgse_dv0 = 0x0A0A0A0A` (controlled by bits [19:18] in kgse_ekdv)
- L4PSRC, L4PDST: `kgse_dv1 = 0x0B0B0B0B` (controlled by bits [9:8] in kgse_ekdv)
- PTYPE1: no separate default-value slot is defined in `fman_keygen.c`. When PTYPE1
  is absent (e.g. non-IP frame), the hardware behaviour is silicon-defined. ASK2
  does not attempt to offload non-IP frames, so this case does not arise.

### 4.3 Register write protocol

KGSE registers are indirect. The write sequence is:

1. Populate all `kgse_*` fields in the in-memory `fman_kg_scheme_regs` struct
2. Build KGAR: `FM_KG_KGAR_GO | FM_KG_KGAR_WRITE | FM_KG_KGAR_SEL_SCHEME_ENTRY
   | (port_id) | (scheme_id << 16)`
3. `iowrite32be(kgar, &kgr->kgar)` — triggers the indirect write
4. Poll `kgar` until `FM_KG_KGAR_GO` clears (hardware busy-clears it)
5. Check `FM_KG_KGAR_ERR` — if set, the write was rejected (scheme in use or
   invalid parameters)

This protocol is handled by `keygen_scheme_setup()` in `fman_keygen.c`. ASK2 does
not call this function directly for the scheme-EKFC change; it uses the
`fman_pcd_kg_scheme_create()` path from the board PCD patches, or the sed
injection into `fman_keygen.c` during the build (current approach).

---

## 5. Impact analysis: what changes

### 5.1 `fman_keygen.c` — EKFC value

**Current (F-043 sed injection in `ci-setup-kernel.sh`):**
```c
scheme_regs.kgse_ekfc = 0x00180006;  /* 4-tuple: IPSRC1|IPDST1|L4PSRC|L4PDST */
```

**Target:**
```c
scheme_regs.kgse_ekfc = 0x001C0006;  /* 5-tuple: IPSRC1|IPDST1|PTYPE1|L4PSRC|L4PDST */
```

Single-line change. The sed replacement in `ci-setup-kernel.sh` changes from
`0x00180006` to `0x001C0006`. No structural change to the driver.

All 5 KG schemes (0–4) receive the same EKFC; they all participate in RSS
distribution for the same traffic class. Changing EKFC identically on all schemes
ensures consistent extraction regardless of which scheme handles a given packet.

### 5.2 Flow key size: 12 bytes → 13 bytes

**Current:** `fe_ehash set 0x7fff 12 0` (mask=0x7fff, keysize=12, hashshift=0)

**Target:** `fe_ehash set 0x7fff 13 0` (mask=0x7fff, keysize=13, hashshift=0)

Changes required:
- `fman_pcd_fe_arm_engage()` or wherever the ehash is configured: change
  keysize from 12 to 13
- `fe_ehash` debugfs command in the engage flow: change `12` to `13`
- `vyos-offload-ask` script: update `${#key}` validation from `-ne 24` (12 hex
  bytes = 24 chars) to `-ne 26` (13 hex bytes = 26 chars)
- `ask_hw.c` (if present): change key buffer size constant from 12 to 13

### 5.3 Flow insertion key format

The 13-byte key passed to `fe_flow_add` or `fman_pcd_ehash_insert()` must be
constructed as:

```
[0-3]   SIP in big-endian network byte order
[4-7]   DIP in big-endian network byte order
[8]     IP protocol byte (0x06=TCP, 0x11=UDP, 0x2F=GRE)
[9-10]  L4 source port in big-endian network byte order
[11-12] L4 destination port in big-endian network byte order
```

**IF** the F-046 size-grouped extraction hypothesis is correct instead, the layout
would be:

```
[0-3]   SIP
[4-7]   DIP
[8-9]   L4 source port   (2-byte fields grouped before 1-byte fields)
[10-11] L4 destination port
[12]    IP protocol byte
```

**The correct layout is determined by §6 verification experiment before any
code change is committed.**

### 5.4 CRC64 bucket computation

The software-side CRC64 (used to compute the ehash bucket index) operates over the
raw key bytes. Changing key size from 12 to 13 bytes changes the CRC64 output.
This is correct and expected — the software computation must match what the silicon
hash engine computes over the same 13-byte buffer. No other change is needed to the
CRC64 code itself.

**Open issue:** the kgse_hc (Hash Command) register governs whether the silicon uses
the same CRC64 polynomial as the software implementation. This is the live blocking
issue from the F-043/F-045/F-046 session (2026-07-09/10). The kgse_hc mismatch
must be resolved independently of this EKFC upgrade. This spec does not close that
issue — it defines the correct EKFC target. The two issues are orthogonal.

### 5.5 `ask.ko` flow key construction in `ask_hw.c`

The existing `struct ask_hw_flow_key_v4` carries separate fields for src_ip,
dst_ip, src_port, dst_port. A `proto` field must be added:

```c
struct ask_hw_flow_key_v4 {
    __be32  src_ip;
    __be32  dst_ip;
    u8      proto;       /* NEW: IP protocol byte, e.g. IPPROTO_TCP=6 */
    __be16  src_port;
    __be16  dst_port;
};
```

The serialiser that builds the 13-byte wire key from this struct writes the fields
in the extraction order determined by §6:

```c
/* Assuming descending-bit-position order (to be confirmed by §6): */
static void ask_hw_build_key_v4(const struct ask_hw_flow_key_v4 *k, u8 *buf)
{
    memcpy(buf + 0, &k->src_ip,   4);  /* IPSRC1  bit 20 */
    memcpy(buf + 4, &k->dst_ip,   4);  /* IPDST1  bit 19 */
    buf[8] = k->proto;                  /* PTYPE1  bit 18 */
    memcpy(buf + 9,  &k->src_port, 2); /* L4PSRC  bit  2 */
    memcpy(buf + 11, &k->dst_port, 2); /* L4PDST  bit  1 */
}
```

The conntrack offload path that populates `ask_hw_flow_key_v4` from an
`nf_flow_tuple` already has `proto` available via `tuple->l4proto` (u8). No
conntrack-layer change is required.

---

## 6. Mandatory verification experiment

The extraction order of PTYPE1 (bit 18, 1-byte field) relative to the 4-byte
fields IPSRC1/IPDST1 and the 2-byte fields L4PSRC/L4PDST is **not confirmed by
production silicon runs** as of 2026-07-10. The F-046 size-grouping hypothesis
and the kernel-source descending-order claim predict different byte layouts.

**This experiment must pass before any code consuming the 13-byte key is committed.**

### 6.1 Experiment E-EKFC-1: single-bucket mask test with PTYPE1

**Precondition:** DUT running a build with EKFC=0x001C0006 (13-byte keys),
engage working, FE-VM armed on eth3, kgse_hc issue resolved (bucket computation
matches silicon).

**Method:**

```sh
# Step 1: set mask=0x0000 (single bucket, all flows land in bucket 0)
echo "set 0x0000 13 0" > /sys/kernel/debug/fman_pcd/fe_ehash

# Step 2: insert a test flow using the descending-bit-position layout
# For TCP flow: SIP=10.99.1.106, DIP=10.99.2.200, PROTO=6, SPORT=55001, DPORT=5201
# Descending layout: 0A63016A 0A6302C8 06 D6D9 1451
echo "add 0A63016A0A6302C806D6D91451" > /sys/kernel/debug/fman_pcd/fe_flow

# Step 3: send 5 TCP packets from 10.99.1.106:55001 to 10.99.2.200:5201
# (fixed source port via socat or iperf3 with --cport)
socat TCP-CONNECT:10.99.2.200:5201,sourceport=55001,reuseport - < /dev/null

# Step 4: check HIT counter
cat /sys/kernel/debug/fman_pcd/fe_stats
```

**Expected if descending-bit-position order is correct:**
HIT counter increments. Flow matches.

**Expected if size-grouped order is correct:**
HIT counter does not increment (MISS). Then try size-grouped layout:

```sh
# Size-grouped layout: SIP(4)+DIP(4)+SPORT(2)+DPORT(2)+PROTO(1)
# 0A63016A 0A6302C8 D6D9 1451 06
echo "add 0A63016A0A6302C8D6D914510" > /sys/kernel/debug/fman_pcd/fe_flow
# (Send same traffic, check HIT)
```

### 6.2 What the experiment resolves

| Outcome | Conclusion | Action |
|---|---|---|
| First key (descending) HITs | Extraction order = descending bit position | Use layout in §3.1; commit code with that serialiser |
| Second key (size-grouped) HITs | Extraction order = size-grouped | Revise §3.1 to size-grouped layout; update serialiser |
| Neither HITs | Extraction order unknown; some other issue | Check kgse_hc hash mismatch; re-examine microcode 210.10.1 EKFC behaviour |

### 6.3 Prerequisite: kgse_hc resolution

The mask=0x0000 (single-bucket) test eliminates hash bucket mismatch as a confound.
With mask=0x0000, the silicon hashes the extracted key, ANDs the result with 0x0000,
and always lands in bucket 0. Software-side CRC64 similarly produces bucket 0 after
ANDing with 0x0000. This means the E-EKFC-1 experiment is **independent of the
kgse_hc polynomial mismatch issue** — it purely tests key content, not bucket
computation. The mask=0x0000 trick was the original E1 candidate identified in the
2026-07-10 ASK2 flow-HIT oracle deep-dive Qdrant entry and is the correct isolation
technique here.

---

## 7. Implementation checklist

The following changes are gated on passing E-EKFC-1 (§6).

### 7.1 Build system (immediate, single-line)

```sh
# ci-setup-kernel.sh: change EKFC sed replacement
# FROM:
sed -i 's/= DEFAULT_HASH_KEY_EXTRACT_FIELDS/= 0x00180006/' \
    $KERNEL_SRC/drivers/net/ethernet/freescale/fman/fman_keygen.c
# TO:
sed -i 's/= DEFAULT_HASH_KEY_EXTRACT_FIELDS/= 0x001C0006/' \
    $KERNEL_SRC/drivers/net/ethernet/freescale/fman/fman_keygen.c
```

### 7.2 FE chain configuration (keysize 12→13)

In `fman_pcd_fe_arm_engage()` (patch 0148/0150 or equivalent):

```c
/* Change ehash keysize from 12 to 13 */
fe_ehash_configure(pcd, .mask = 0x7fff, .keysize = 13, .hashshift = 0);
```

In the `vyos-offload-ask` engage script:
```sh
echo "set 0x7fff 13 0" > ${DEBUGFS}/fe_ehash  # was 12
```

### 7.3 Flow key validation (24→26 hex chars)

```sh
# vyos-offload-ask flow-add validation
if [ ${#key} -ne 26 ]; then  # was -ne 24
    echo "ERROR: key must be 13 bytes (26 hex chars), got ${#key}/2 bytes"
    exit 1
fi
```

### 7.4 `ask_hw.c` key structure and serialiser

```c
/* Add proto field to ask_hw_flow_key_v4 */
struct ask_hw_flow_key_v4 {
    __be32  src_ip;
    __be32  dst_ip;
    u8      proto;      /* IP protocol: IPPROTO_TCP=6, IPPROTO_UDP=17 */
    __be16  src_port;
    __be16  dst_port;
};

#define ASK_HW_FLOW_KEY_V4_SIZE  13  /* bytes; was 12 */

/* Update serialiser based on E-EKFC-1 result (see §6) */
static void ask_hw_serialize_key_v4(const struct ask_hw_flow_key_v4 *k,
                                     u8 buf[ASK_HW_FLOW_KEY_V4_SIZE])
{
    /* Layout for EKFC=0x001C0006, descending-bit-position order
     * (CONFIRM via E-EKFC-1 before deploying): */
    put_unaligned_be32(be32_to_cpu(k->src_ip),   buf + 0);
    put_unaligned_be32(be32_to_cpu(k->dst_ip),   buf + 4);
    buf[8] = k->proto;
    put_unaligned_be16(be16_to_cpu(k->src_port), buf + 9);
    put_unaligned_be16(be16_to_cpu(k->dst_port), buf + 11);
}
```

The caller (conntrack flow promotion path) sets `k->proto` from
`tuple->l4proto` (already available as u8 in `nf_flow_tuple`).

### 7.5 CRC64 key buffer

The `fman_pcd_crc64(key, keylen)` call in the bucket-computation path:

```c
/* Change keylen from 12 to 13 */
bucket = fman_pcd_crc64(flow_key, 13) >> (64 - hash_bits);
```

This is the only change needed. The CRC64 implementation itself (ECMA-182
reflected poly, seed ~0ULL) is unchanged.

### 7.6 kunit test updates

Tests in `tests/ask_flow_test.c` that use hardcoded 12-byte key fixtures:

- Update all `ASK_HW_FLOW_KEY_V4_SIZE` references from 12 to 13
- Update golden-hex key fixtures to include the protocol byte at offset 8
- Add a test case: `test_key_proto_differentiates_tcp_udp` — inserts a TCP flow
  and a UDP flow with the same 4-tuple; verifies they hash to different keys
  (they will, since the proto byte differs) and that lookup returns distinct flows

### 7.7 Debugfs `fe_flow_add` documentation update

Update the in-kernel debugfs help text for `fe_flow`:

```
# fe_flow add <26-hex-char-key>
# Key format for EKFC=0x001C0006 (IPv4 5-tuple):
#   [0-3]  SIP  (4 bytes big-endian)
#   [4-7]  DIP  (4 bytes big-endian)
#   [8]    PROTO (1 byte: 0x06=TCP 0x11=UDP)
#   [9-10] SPORT (2 bytes big-endian)
#   [11-12] DPORT (2 bytes big-endian)
# Example TCP 10.99.1.106:55001 → 10.99.2.200:5201:
#   0A63016A 0A6302C8 06 D6D9 1451  → add 0A63016A0A6302C806D6D91451
```

---

## 8. Relationship to the kgse_hc (hash command) open issue

The `kgse_hc` register controls how the KeyGen hardware computes the 64-bit hash
over the extracted key buffer. The hash value determines the ehash bucket index on
the silicon side. If `kgse_hc` is not aligned with the software CRC64 computation,
flow insertions land in wrong buckets and never match — the root symptom of the
current F-043/F-045/F-046 blocking issue.

This spec does not fix the kgse_hc problem. The EKFC upgrade is independent:
it changes what bytes are extracted, not how they are hashed. The two issues must
both be fixed for end-to-end flow HITs to work:

1. **kgse_hc alignment** (currently blocked): ensure silicon hash polynomial
   matches `fman_pcd_crc64` — likely requires reading back kgse_hc from the scheme
   registers and either matching it in software or patching the kernel scheme setup
   to write a known value.

2. **EKFC upgrade** (this spec): change extraction to 5-tuple via EKFC=0x001C0006,
   verified by E-EKFC-1 (mask=0 experiment which is immune to kgse_hc mismatch).

The recommended sequencing:
1. First resolve kgse_hc (or use mask=0 to sidestep it for E-EKFC-1)
2. Run E-EKFC-1 to confirm extraction order
3. Implement §7 changes
4. Re-run full flow-HIT test with mask=0x7fff

---

## 9. Performance impact assessment

### 9.1 Extraction overhead

Adding one EKFC bit (PTYPE1, bit 18) adds **zero marginal latency**. The hard
parser has already parsed the IP protocol byte and recorded its offset in the Parse
Result when it processed the IP header. The EKFC hardware reads it from the PR with
the same memory operation that fetches IPSRC1 and IPDST1. The key buffer is one
byte longer (13 vs 12), which increases the CRC64 input by 1 byte — negligible.

The 7.34 Gbps HIT path measured in the 2026-07-07 benchmark (OVFQ=1, MTU 9000,
EKFC=0x00180006, 12-byte keys) is **not expected to change** with EKFC=0x001C0006
and 13-byte keys. Performance is dominated by DDR table latency and FQ scheduling,
not by the extraction path.

### 9.2 Ehash table footprint

Ehash bucket entries are compared up to `keysize` bytes. Changing from 12 to 13
bytes adds 1 byte to each comparison. The en_ehash_entry struct (16 bytes: 8 bytes
key + 8 bytes flags/next) does not change. The table size (512 KB, 32768 buckets,
16 B per bucket) does not change. Memory footprint is unchanged.

### 9.3 GEC alternative assessment

For completeness: using GEC instead of EKFC to extract PTYPE1 would:
- Add ~5% latency to key extraction (GEC requires an additional per-field
  byte-range copy through the IC pipeline)
- Allow arbitrary byte-order declaration (software-defined order)
- Eliminate the extraction-order uncertainty of §3.2

The GEC alternative is **rejected** on performance grounds and architectural
grounds (ASK2 committed to EKFC-only in §1). The extraction-order uncertainty is
resolvable by the E-EKFC-1 experiment with zero performance cost.

---

## 10. IPv6 notes (out of scope for this spec)

When IPSRC1 (bit 20) or IPDST1 (bit 19) is set in EKFC and the frame carries
an IPv6 header, the silicon extracts **16 bytes** per field instead of 4 bytes.
The total key buffer size for an IPv6 5-tuple with EKFC=0x001C0006 would be:
16 + 16 + 1 + 2 + 2 = **37 bytes**.

This creates a fundamental problem: if the same KG scheme handles both IPv4 and
IPv6 frames, the key buffer length is **protocol-dependent**. The ehash table is
configured with a fixed `keysize`; a 13-byte ehash cannot correctly classify
a 37-byte IPv6 key.

The correct approach for IPv6 is:
- A separate KG scheme with EKFC=0x001C0006 and keysize=37 (or a reduced key
  using only the lower 32 bits of each IPv6 address, making it 4+4+1+2+2=13)
- A separate CC dispatch path keyed on EtherType (0x86DD for IPv6 vs 0x0800
  for IPv4) that routes IPv6 frames to the IPv6-specific scheme

IPv6 offload design is deferred and not covered by this spec.

---

## 11. Open questions

1. **E-EKFC-1 result** — which extraction order does PTYPE1 follow on the
   LS1046A FMan v3 with microcode 210.10.1? Descending-bit-position or
   size-grouped? Answer determines the key serialiser layout in §5.5.

2. **PTYPE1 default value** — what does the silicon write to the key buffer at
   offset 8 when EKFC has PTYPE1 set but the frame is non-IP (e.g. ARP,
   EtherType 0x0806)? The EKDV register does not define a default slot for
   PTYPE1 in mainline `fman_keygen.c`. ASK2 does not offload non-IP frames, so
   this is a robustness question, not a correctness question.

3. **kgse_hc resolution timeline** — the E-EKFC-1 mask=0 experiment can be run
   independently. Full flow HITs with mask=0x7fff require kgse_hc to be resolved
   first.

---

## 12. Summary of changes

| File | Change | Size |
|---|---|---|
| `bin/ci-setup-kernel.sh` | sed: `0x00180006` → `0x001C0006` | 1 line |
| `fman_pcd_fe_arm_engage()` | keysize: 12 → 13 in ehash configure call | 1 line |
| `vyos-offload-ask` (engage script) | `fe_ehash set ... 13 ...` and key length check 24→26 | 2 lines |
| `ask_hw.c` | Add `u8 proto` to `ask_hw_flow_key_v4`; update serialiser; constant 12→13 | ~15 lines |
| `fman_pcd_crc64` callsite | keylen 12→13 | 1 line |
| `tests/ask_flow_test.c` | Update fixtures; add tcp/udp differentiation test | ~30 lines |
| Debugfs help text | Document 13-byte format | ~10 lines |

**Total delta: ~60 lines.** All changes are contingent on E-EKFC-1 confirming the
extraction order.

---

**End of spec v1.0, 2026-07-10.**
