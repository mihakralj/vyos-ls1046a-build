# FMan Microcode — open-source `106` vs proprietary `210.10.1`

**Source:** NXP `qoriq-fm-ucode` repo readme (github.com/nxp-qoriq/qoriq-fm-ucode) · kernel.org DPAA
driver doc · LS1046A DPAA RM §5.9–5.12 · live DTB/flash decode on the board (192.168.1.190) (2026-06-11).
**This doc exists because the FMan microcode silently decides whether the fine-grain Coarse-Classifier
(CC) path described in [`fman-pcd.md`](fman-pcd.md) is even reachable.** Every PCD doc assumes a
specific microcode is loaded; this is where that assumption is made explicit, detected, and bounded.

The FMan does not have fixed-function classification logic. Its **FMan Controller** is a QE-derived
32-bit RISC engine that executes a **microcode (ucode)** image loaded at boot. That image is what
implements the Parser HXS dispatch, the KeyGen→Classifier hand-off, and the Custom/Coarse-Classifier
(CC) exact-match walk. **No ucode loaded (or the wrong one) → no CC offload**, regardless of how
correct the `fman_pcd_*.c` programming is.

---

## 1. TL;DR — the one-screen answer

| Question | Answer |
|---|---|
| What runs on **our board**? | **Proprietary `210.10.1`** QEF blob, U-Boot-injected into the DTB. |
| What is the open-source alternative? | **`106.4.18`** (`fsl_fman_ucode_ls1046_r1.0_106_4_18.bin`). |
| Is **"160"** an LS1046A ucode? | **No.** `160` is the **P1023** open-source major. "Open-source 160" for LS1046A is a **misnomer for `106`**. |
| Coarse vs fine — which is which? | **KeyGen (KG) = hash-RSS = coarse**; **Coarse-Classifier (CC) = exact-match = the "fine" classifier** (NXP's name is inverted — see §4). |
| Does mainline program CC? | **Never.** Mainline DPAA only does KG-RSS. CC is programmed solely by our `fman_pcd_*.c` patches. |
| Do both ucode families *support* CC? | Yes — both `106` (IPACC) and `210.x` silicon-support CC/HM/Policer. The gate is **which blob is loaded + executing**, not the family. |
| How is it detected? | QEF header decode (`firmware-check` §4) + kernel caps gate (patch `0086a`, `major>=210 → 0x17`). |
| What is the fallback if it is missing? | Graceful: board boots **mainline KG-RSS only**, no CC caps, `0117` `dev_warn`. There is **no** `request_firmware()` /lib/firmware fallback — by design. |

---

## 2. The QEF container (what is actually on flash)

The microcode is a **QorIQ Engine Firmware (QEF)** container (`struct qe_firmware`). The first 76 bytes
are the header you decode to identify a blob:

| Bytes | Field | Our board (`210.10.1`) |
|---|---|---|
| `0x00–0x03` | be32 total length | `51652` |
| `0x04–0x06` | magic | `"QEF"` |
| `0x07` | layout version | — |
| `0x08–0x45` | `id[62]` null-terminated string | `"Microcode version 210.10.1 for LS1043 r1.0"` |
| `0x46` | split-IRAM flag | `0` |
| `0x47` | microcode count | `1` |
| `0x48–0x49` | be16 SoC model code | `0x0413` (proprietary 210) · `0x0416` = open-source 106 |
| `0x4A / 0x4B` | SoC rev major / minor | — |
| entry `+112` | u8×3 version major/minor/rev | `d2 0a 01` = **210.10.1** |

> The **"for LS1043 r1.0"** label in the id string is **cosmetic and correct** for this board —
> LS1043A and LS1046A share the same FMan v3 silicon. Do not "fix" it.

Our board's blob: `51652` bytes, md5 `6f23090a3d5ae8b302ea41fd90a14d4d`,
sha256 `5f3ed8d32b8659aafd8912d5d9920306350cae7a85884d81859152b9723eff0d`, `wcount=12851`
(51404 code bytes), `code_off=244`. It lives at the **head of QSPI `mtd3` "fman-ucode"**
(flash offset `0x400000`).

> ⚠ **Partition numbering shifts between builds** — `mtd3` here was `mtd4` on older images (the same
> trap that hit `fw_env.config` mtd2-vs-mtd3). **Always confirm with `cat /proc/mtd`** before reading
> raw flash. The 1 MiB at `mtd4` "recovery-dtb" is an FDT, **not** the ucode — a backup taken from the
> wrong partition once produced a 39880-byte FDT masquerading as ucode.

---

## 3. The two families and their version scheme

NXP open-source ucode versioning (from the `qoriq-fm-ucode` readme):

- **First number = Primary Major = feature family.** `106` = **IPACC**, `107` = DSAR + partial IPACC,
  `108` = NG-CAPWAP + FE + IPACC. The open-source **`106` IPACC package explicitly includes** Custom
  Classification (CC), Independent-Mode (IM), Host-Commands (HC), IPv4/6 Frag (IPF), Reassembly (IPR),
  IPsec, and Header-Manip (HM). *(This corrects an earlier wrong assumption that "open-source = no CC".)*
- **Second number = HW rev.** `.1` FMANv2 no-SW-DMA-sem · `.2` FMANv2 w/sem · `.3` FMANv3 Rev1 ·
  `.4` FMANv3 > Rev1. LS1046A r1.0 ⇒ `106.4.18`; stock NXP RDB U-Boot prints
  `"Uploading microcode version 106.4.18"`.
- **`210.x` is proprietary** — a newer NXP release **not in the public repo** (`210 ≫ 108`). It is the
  blob U-Boot injects on this board. `210.10.1` does **not** implement the CEV doorbell / REV events
  (relevant to the FMan event-IRQ discussion in [`soc-integration.md`](soc-integration.md) §4) and
  lacks the **HC host-command doorbell** — so our CC approach uses **direct KG→`FM_CTL|AC_CC` dispatch
  + result-AD enqueue**, never the HC doorbell.

| Blob | Family | Source | On our board? |
|---|---|---|---|
| `fsl_fman_ucode_ls1046_r1.0_106_4_18.bin` | open-source 106 (IPACC) | public `qoriq-fm-ucode` | no (alternative) |
| `Microcode version 210.10.1 for LS1043 r1.0` | proprietary 210.x | NXP factory (SPI `mtd3`) | **yes** |
| `fsl_fman_ucode_p1023_r1.1_160_0_18.bin` | open-source 160 — **P1023 SoC** | public | **no** — wrong SoC, the "160" misnomer |

---

## 4. Coarse vs fine — the terminology trap

NXP's PCD has **two distinct steering mechanisms** whose "coarse/fine" naming is inverted relative to
the networking meaning:

```mermaid
flowchart TD
    PR["Parse Result<br/>(CPID, LCV, offsets)"] --> KG
    KG["KeyGen (KG)<br/>build key, CRC-64 hash"] -->|hash spreads flows<br/>across a SET of FQs| RSS["COARSE = hash-RSS<br/>statistical distribution<br/>(mainline default)"]
    KG -->|match_vector scheme<br/>steers into a tree| CC["Coarse Classifier (CC)<br/>EXACT-MATCH table in MURAM"]
    CC -->|one 5-tuple to one action| FINE["FINE = deterministic<br/>per-flow steering<br/>(our fman_pcd patches only)"]
```

- **KeyGen (KG)** hashes a key and spreads flows across a *set* of frame queues → **statistical /
  COARSE**. This is what mainline DPAA programs by default (kernel.org doc: *"enables RSS … distribute
  traffic on 128 hardware frame queues using a hash on IP v4/v6 src/dst and L4 src/dst ports"*).
- **Coarse Classifier (CC)** — despite NXP's name — is the **exact-match lookup tree** = deterministic
  per-flow steering = the **user's "fine classifier"**. NXP named it "coarse" relative to the *Parser*
  (which inspects headers byte-by-byte); from a flow-granularity view it is the fine one.

**Mainline DPAA never programs CC.** So the practical split is: **mainline / open-source datapath =
coarse hash-RSS only**; **fine exact-match CC = our `fman_pcd_*.c` patches + a loaded, executing
ucode**. The ucode family (106 vs 210) does not change this — the ucode just has to be present so the
`FM_CTL AC_CC` handler exists for the CC walk.

---

## 5. Capability divergence

Two independent dimensions: **which ucode is loaded** and **which driver path programs the PCD**.

| Capability | mainline-default path | open-source `106.4.18` | proprietary `210.10.1` (our board) |
|---|---|---|---|
| Parser (L2–L4 HXS) | ✅ | ✅ | ✅ |
| KeyGen hash-RSS (coarse) | ✅ (the only thing it uses) | ✅ | ✅ |
| Coarse-Classifier exact-match (fine) | ❌ never programmed | ✅ silicon-supported | ✅ **what we drive** |
| Header-Manip (NAT/TTL/cksum) | ❌ | ✅ | ✅ |
| Policer (RFC-2698/4115) | ✅ (via tc, limited) | ✅ | ✅ |
| HC host-command doorbell | n/a | ✅ | ❌ (we use direct `AC_CC` dispatch) |
| CEV doorbell / REV events | n/a | varies | ❌ (affects FMan event-IRQ wiring) |
| Kernel CC caps gate (`0086a`) | `0` | `0` (over-conservative — see note) | **`0x17`** = CC\|HM\|POL\|PARSER |

> **Note on the caps gate.** Patch `0086a` `dpaa_fman_caps_from_id()` grants caps `0x17` only for
> `major >= 210`, returning `0` for `106`. This is **over-conservative** (106 *does* have CC per the
> NXP readme) but **harmless for us** — our board is `210.10.1`, so the gate correctly returns `0x17`
> and CC programming proceeds. *(Fork C — swapping to `106.4.18` — would require lifting this gate;
> see [`fman-fe-ehash.md`](fman-fe-ehash.md) §8.2.)*
>
> **Caveat on the "✅ exact-match" cells.** The ✅ marks mean the **silicon** supports CC, not that
> *classic* (FE-less) exact-match dispatch flows on the **loaded blob**. Empirically, the executing
> `210.10.1` AC_CC handler **requires** FE/ehash globals and **parks** on classic exact-match CC
> (iter-33; [`fman-fe-ehash.md`](fman-fe-ehash.md) §8.1). Whether `106.4.18` flows classic exact-match
> **without** FE/ehash is the open question gating Fork C (§8.2) — a single board experiment settles it.

---

## 6. Detection — which ucode is live?

```bash
# Human-readable, one shot (decodes id string, length, soc-model, version, md5):
firmware-check                # see its section 4

# Decode the QEF header off the U-Boot-injected DTB copy (no root, always present if loaded):
od -An -tx1 -N76 /proc/device-tree/soc/fman@1a00000/fman-firmware

# Decode off raw flash (needs root; CONFIRM the partition first — numbering shifts!):
cat /proc/mtd                 # find the "fman-ucode" partition (mtd3 on current builds)
sudo od -An -tx1 -N76 /dev/mtd3
```

The kernel's own verdict is the DT node + the caps gate:

- **DT node present** `/soc/fman@1a00000/fman-firmware` with property `fsl,firmware` ⇒ U-Boot injected
  a blob. Patch `0086a`'s `dpaa_fman_get_caps()` parses its id string `"Microcode version <maj>"`;
  `<maj> >= 210` ⇒ caps `0x17`.
- **`id` string SoC-model byte** `0x0413` ⇒ proprietary 210; `0x0416` ⇒ open-source 106.

---

## 7. Load path and the fallback

```mermaid
flowchart LR
    F["QSPI mtd3 head<br/>offset 0x400000<br/>QEF blob"] --> UB["U-Boot:<br/>read to RAM, validate QEF header"]
    UB -->|valid| UP["upload to FMan IRAM<br/>+ fdt_fixup_fman_firmware()"]
    UP --> DT["kernel DTB node<br/>/soc/fman@1a00000/fman-firmware<br/>property fsl,firmware"]
    DT --> K["fman driver reads blob<br/>from DT (NOT request_firmware)"]
    K --> CI["mainline fman_init() clear_iram()<br/>WIPES the FM_CTL ucode"]
    CI --> P117["patch 0117 load_fman_ctrl_code()<br/>re-streams DT blob into IRAM<br/>+ verify + IRAM_READY"]
    UB -->|invalid / not a QEF| FB["U-Boot: 'Data at ... is not a firmware'<br/>NO DT injection"]
    FB --> NOCC["kernel: DT node absent<br/>0117 dev_warn (non-fatal)<br/>caps=0 → mainline KG-RSS only"]
```

- **U-Boot owns the load.** It reads the QEF from QSPI `0x400000` into a RAM buffer, validates the
  header, uploads to FMan IRAM, and `fdt_fixup_fman_firmware()` **injects the blob into the kernel
  DTB**. The `fman_ucode` env var is a **volatile boot-computed RAM address** — **never `saveenv`** it.
- **The kernel never calls `request_firmware()`** and there are **no `/lib/firmware/fsl*` files**. The
  load path is correct by construction: we get whatever U-Boot put in the DTB = `210.10.1`. *(There is
  zero `request_firmware`/`.bin`/lib-firmware code in any kernel patch — such strings are docs only.)*
- **`clear_iram` bug + patch `0117` (the reload "fallback within the happy path").** Mainline
  `fman_init()` calls `clear_iram()`, which wipes the U-Boot-uploaded **FM_CTL** microcode, and
  mainline **never reloads it** — so the `AC_CC` handler vanishes and CC dispatch silently dies.
  Patch `0117` `load_fman_ctrl_code()` runs right after `clear_iram` in the pre-enable window:
  re-reads the DT QEF, streams the code words via IRAM auto-increment (`IRAM_IADD_AIE`), full verify
  readback, then `IRAM_READY` — replicating SDK `LoadFmanCtrlCode`. **Non-fatal `dev_warn` if the DT
  node is absent.**
- **Graceful degradation fallback.** If `mtd3` holds garbage/an FDT instead of a QEF, U-Boot prints
  `"Fman1: Data at <addr> is not a firmware"`, skips injection, and the board **still boots** —
  the DT node is absent, `0086a` returns caps `0`, `0117` `dev_warn`s, and the datapath falls back to
  **mainline KG-RSS** with **no CC offload**. There is intentionally **no second firmware source**.

---

## 8. Operational invariant

> **Work with `210`, never request an open-source `106`.** Patch `0117` MUST load the DTB-injected
> blob (proprietary `210.10.1`) and MUST NOT `request_firmware()` a `106` blob from `/lib/firmware`.
> Verified: no such code path exists. The load is correct by construction.

If you ever need to restore the factory ucode, the true QEF is the 51652-byte
`210.10.1` blob at SPI offset `0x400000` (md5 `6f23090a…`) — **not** the `mtd4` recovery-DTB.

---

## 9. Current status / known gap

The microcode load path is HW-proven (DTB decode + `0117` GATE-1 pass). The remaining wall is **not**
the ucode load: with `210.10.1` loaded and executing, the `AC_CC` accelerator **is** dispatched on the
first CC frame (the parked FM_CTL task holds the correctly-extracted L4 key), but the port **stalls on
that first frame** (`FMFP_PS` → `0x80800000`, **STL** bit8; parked task `ts[4]=0x81000000`, **PRK**)
**before** any result-AD enqueue/discard fires — the **M3-3b structural wall**.

The root cause is now understood (qdrant `iter-33` 2026-06-12 + `ASK2 ehash/FE architecture root cause`
2026-06-13): the `210.10.1` AC_CC handler **dereferences Forwarding-Engine global structures on dispatch**
(FE object pool, internal FE buffer pool, MUX/TRANSITION-FE singletons, `FE_ENTER` root AD) that the
vendor stack pre-builds via `USE_ENHANCED_EHASH=1` but mainline MURAM lacks — so classic exact-match CC
parks the FM_CTL task waiting on resources that were never allocated. **Classic exact-match CC cannot
work on this loaded blob without the FE/ehash init protocol.** The byte-level init contract and the
resulting **Fork B (reproduce FE/ehash on 210.10.1) vs Fork C (swap to `106.4.18` + classic CC)**
decision live in [`fman-fe-ehash.md`](fman-fe-ehash.md) §8, tracked in
[`../specs/dpaa1-afxdp-modernization-spec.md`](../specs/dpaa1-afxdp-modernization-spec.md) §5.6 and the
ASK2 CC-steering work. This doc only guarantees: *the right ucode is on the board and the kernel sees it.*

---

## 10. Cross-references

- [`fman-pcd.md`](fman-pcd.md) — the Parser/KeyGen/**CC**/Policer/Manip detail this ucode dispatches.
- [`fman.md`](fman.md) §8 — FMan Controller, Independent Mode, the "210 ucode" in the block context.
- [`muram.md`](muram.md) — where the CC exact-match tables the ucode walks physically live.
- [`soc-integration.md`](soc-integration.md) §4 — the CEV/REV event-IRQ discrepancy caused by `210.10.1`.
- [`software-stack-ask.md`](software-stack-ask.md) — SDK FMC vs kernel `fman_pcd_*.c` PCD programming.
- [`../specs/ask2-rewrite-spec.md`](../specs/ask2-rewrite-spec.md) §13 — `fman_pcd` subsystem;
  [`../specs/dpaa1-afxdp-modernization-spec.md`](../specs/dpaa1-afxdp-modernization-spec.md) §5.6 — CC stall.

*Maintainers: this is the single source of truth for the 106-vs-210 distinction. If a sibling doc
names a microcode, it should link here rather than restate the version facts.*
