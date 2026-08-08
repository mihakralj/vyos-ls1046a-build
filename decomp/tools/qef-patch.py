#!/usr/bin/env python3
"""qef-patch.py — patch an FMan QEF blob (standalone or inside a DTB).

Every mutation is CRC-safe: the 4-byte trailer is recomputed after patching
(raw reflected CRC-32, poly 0xEDB88320, init 0, no final complement, over
blob[:length-4] — solved 2026-08-07, see decomp/01-container.md).

Modes:
  qef-patch.py BLOB --set-word N=0xVALUE ... -o OUT.bin
  qef-patch.py --fdt BOARD.dtb --set-word N=0xVALUE ... -o OUT.dtb
  qef-patch.py BLOB --set-header-byte OFF=0xXX ... -o OUT.bin
  qef-patch.py BLOB --set-id "string" -o OUT.bin   (id field, NUL-padded)

Patches apply to the FIRST code section (210.10.1 has exactly one).
--set-word indices are code-word indices (0-based from code_offset).
The tool refuses to write if the pre-patch trailer CRC does not verify.
"""
import argparse
import struct
import sys
from pathlib import Path


def _crc_table():
    t = []
    for n in range(256):
        c = n
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
        t.append(c)
    return t


_TAB = _crc_table()


def trailer_crc(blob):
    crc = 0
    for b in blob[:len(blob) - 4]:
        crc = _TAB[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc & 0xFFFFFFFF


def find_qef(buf):
    """Locate a QEF blob inside a larger buffer (e.g. DTB). Returns
    (start, length) or None. Validates magic + trailer CRC."""
    pos = 0
    while True:
        i = buf.find(b"QEF\x01", pos)
        if i < 0:
            return None
        if i >= 4:
            start = i - 4
            length = struct.unpack(">I", buf[start:i])[0]
            if 0x100 < length <= len(buf) - start:
                blob = bytes(buf[start:start + length])
                if struct.unpack(">I", blob[-4:])[0] == trailer_crc(blob):
                    return start, length
        pos = i + 1


def parse_qef(blob):
    length = struct.unpack(">I", blob[0:4])[0]
    d = blob[124:244]
    wcount, code_off = struct.unpack(">II", d[104:112])
    return length, code_off, wcount


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--fdt", action="store_true",
                    help="input is a DTB containing the blob as a property")
    ap.add_argument("--set-word", action="append", default=[],
                    metavar="N=0xVALUE", help="patch code word N")
    ap.add_argument("--set-header-byte", action="append", default=[],
                    metavar="OFF=0xXX", help="patch header byte at blob offset")
    ap.add_argument("--set-id", metavar="STRING",
                    help="replace the id[62] string (NUL-padded, truncated)")
    args = ap.parse_args()

    buf = bytearray(Path(args.input).read_bytes())
    if args.fdt:
        loc = find_qef(bytes(buf))
        if loc is None:
            sys.exit("no CRC-valid QEF blob found in DTB")
        start, length = loc
        blob = bytearray(buf[start:start + length])
        print(f"found QEF blob at DTB offset 0x{start:x}, length {length}")
    else:
        blob = buf
        length, code_off, wcount = parse_qef(blob)
        if blob[4:7] != b"QEF" or blob[7] != 1:
            sys.exit("not a QEF v1 blob")
        stored = struct.unpack(">I", blob[-4:])[0]
        if stored != trailer_crc(blob):
            sys.exit(f"pre-patch trailer CRC mismatch "
                     f"(stored 0x{stored:08x}, calc 0x{trailer_crc(blob):08x}) — refusing")
        start = 0

    length, code_off, wcount = parse_qef(blob)
    print(f"blob: {length} B, code_off {code_off}, {wcount} words, "
          f"trailer CRC OK")

    changes = []
    for spec in args.set_word:
        n, v = spec.split("=", 1)
        n, v = int(n, 0), int(v, 0)
        if not 0 <= n < wcount:
            sys.exit(f"word index {n} out of range (0..{wcount - 1})")
        off = code_off + n * 4
        old = struct.unpack(">I", blob[off:off + 4])[0]
        blob[off:off + 4] = struct.pack(">I", v & 0xFFFFFFFF)
        changes.append(f"  word w{n} (@0x{off:x}): 0x{old:08x} -> 0x{v & 0xFFFFFFFF:08x}")
    for spec in args.set_header_byte:
        off, v = spec.split("=", 1)
        off, v = int(off, 0), int(v, 0)
        if not 0 <= off < code_off:
            sys.exit(f"header offset {off} out of range (0..{code_off - 1})")
        changes.append(f"  header byte @{off:#x}: 0x{blob[off]:02x} -> 0x{v:02x}")
        blob[off] = v
    if args.set_id is not None:
        s = args.set_id.encode()[:61]
        old = bytes(blob[8:70]).split(b"\0")[0]
        blob[8:70] = s + b"\0" * (62 - len(s))
        changes.append(f"  id: {old!r} -> {s!r}")
    if not changes:
        sys.exit("no patches specified")

    # recompute trailer
    blob[-4:] = struct.pack(">I", trailer_crc(blob))
    for c in changes:
        print(c)
    print(f"trailer recomputed: 0x{struct.unpack('>I', blob[-4:])[0]:08x}")

    if args.fdt:
        buf[start:start + length] = blob
        # sanity: re-find and re-verify inside the output buffer
        if find_qef(bytes(buf)) != (start, length):
            sys.exit("post-patch DTB re-verify failed — not writing")
        Path(args.out).write_bytes(bytes(buf))
    else:
        Path(args.out).write_bytes(bytes(blob))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
