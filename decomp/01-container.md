# Phase 1 — Container Tooling (QEF parse)

**Status: DONE (2026-08-07)** — `decomp/tools/qef-parse.py` committed
(subcommands `info` / `dump-words` / `dispatch` / `crc`), trailer integrity
word solved and verified across all 24 corpus blobs.

## Goal

A committed, correct-by-construction parser that normalizes every corpus
blob into `(metadata, words[])` and validates container integrity. Everything
downstream consumes its output.

## Container layout (verified on 106/108/210 — see findings 2026-08-07/08-06)

| Bytes | Field | 210.10.1 value |
|---|---|---|
| `0x00–0x03` | `__be32 length` (incl. everything, incl. trailer) | 51652 |
| `0x04–0x06` | magic `"QEF"` | ✓ |
| `0x07` | layout version | 1 |
| `0x08–0x45` | `id[62]` NUL-padded | `"Microcode version 210.10.1 for LS1043 r1.0"` |
| `0x46` | `split_IRAM` | 0 |
| `0x47` | `count` (microcode sections) | 1 |
| `0x48–0x49` | `__be16 soc_model` | `0x0413` (cosmetic — proven inert) |
| `0x4A–0x7B` | extended/reserved header fields | per `qe_firmware.rst` |
| `124–243` | first `struct qe_microcode` (120 B) | single entry |
| desc `+100` | `__be32 iram_offset` | 0 |
| desc `+104` | `__be32 wcount` (u32 words) | 12851 |
| desc `+108` | `__be32 code_offset` (byte off in blob) | 244 |
| desc `+112` | `u8 major, minor, revision` | `210, 10, 1` |
| `244 … length-4` | code (BE u32 words) | 51,404 B |
| `length-4 … length` | trailer integrity word | **unresolved variant** |

Reference: `Documentation/powerpc/qe_firmware.rst` (kernel tree) +
U-Boot `drivers/soc/fsl/qe/qe.c` / `drivers/net/fm/fm.c`
(`qe_upload_firmware` / `fm_upload_ucode`).

## Deliverable: `decomp/tools/qef-parse.py`

Requirements:
1. Parse + structurally validate every blob in a directory; emit JSON
   metadata + raw code words (hex/bin) per blob. — **done**
2. Subcommands: `info`, `dump-words`, `dispatch` (decode the 24-slot table),
   `crc` (integrity-word analysis). — **done**
3. Close the trailer question (below). — **done**
4. Unit-testable offline; no board access needed. — **done** (stdlib only)

## Trailer integrity word — SOLVED (2026-08-07)

Raw table-driven CRC-32, **reflected IEEE poly `0xEDB88320`, init
`0x00000000`, NO final complement**, over `blob[0 : length-4]`. Verified
against stored trailers on **all 24 corpus blobs** (210 + 23 public) —
`qef-parse.py crc` prints per-blob OK. This is the U-Boot
`crc32_no_comp(0, buf, len)` style; the `qe_firmware.rst` description
(`crc32(-1, blob, length-4) ^ -1`, i.e. zlib CRC-32) does **not** apply to
FMan blobs. The failed first-pass brute-force (zlib + 6 variants × 7 scopes)
and the winning combination are recorded in findings.md.

### Historical note (superseded)

The trailer was originally assumed to be zlib CRC-32 over `blob[:-4]` per
`qe_firmware.rst`; that mismatched on all tiers (210: stored `0x961eb941` vs
calc `0x2bd707ca`). A scope × variant brute-force (4 polys × reflected/MSB ×
init/xorout ∈ {0, FFFFFFFF} × word-swapped feeds × 2 scopes) found the
solution above; the winning combination must — and did — solve identically on
every tier. U-Boot cross-check (`qe_upload_firmware` validation path) no
longer needed.

## Method notes

- BE u32 word stream; `wcount*4` bytes from `code_offset`; assert
  `code_offset + wcount*4 + 4 == length` (holds on all three tiers).
- Keep the parser dependency-free (stdlib `struct`/`zlib`) — it will also run
  on the board.
