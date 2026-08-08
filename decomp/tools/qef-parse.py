#!/usr/bin/env python3
"""qef-parse.py — QorIQ Engine Firmware (QEF) container parser.

Parses FMan microcode blobs in the struct qe_firmware container format
(Documentation/powerpc/qe_firmware.rst, U-Boot drivers/soc/fsl/qe/qe.c).
Stdlib only; runs on the build host and on the board.

Subcommands:
  info        header + microcode-descriptor summary per blob (default)
  dump-words  hexdump code words (optionally a word range)
  dispatch    decode the 24-slot entry-point dispatch table at code offset 0
  crc         verify the 4-byte trailer integrity word (solved 2026-08-07:
              raw reflected CRC-32, init 0, xorout 0, over blob[:len-4]);
              --brute re-runs the scope x variant brute-force
"""
import argparse
import json
import struct
import sys
import zlib
from pathlib import Path

HDR_LEN = 124          # struct qe_firmware header up to first descriptor
DESC_LEN = 120         # one struct qe_microcode descriptor
D_IRAM_OFF = 100       # __be32 IRAM load offset (within descriptor)
D_COUNT_OFF = 104      # __be32 instruction word count
D_CODE_OFF = 108       # __be32 code byte offset within blob
D_VER_OFF = 112        # u8 major, minor, revision
TRAILER_LEN = 4


def parse_blob(path):
    """Parse one QEF blob. Returns a dict; raises ValueError on bad magic."""
    raw = Path(path).read_bytes()
    if len(raw) < HDR_LEN + DESC_LEN + TRAILER_LEN:
        raise ValueError(f"{path}: too small ({len(raw)} B)")
    length = struct.unpack(">I", raw[0:4])[0]
    if raw[4:7] != b"QEF" or raw[7] != 1:
        raise ValueError(f"{path}: not a QEF v1 blob")
    if length > len(raw):
        raise ValueError(f"{path}: length field {length} exceeds file {len(raw)}")
    blob = raw[:length]
    d = blob[HDR_LEN:HDR_LEN + DESC_LEN]
    iram_off, wcount, code_off = struct.unpack(
        ">III", d[D_COUNT_OFF - 4:D_VER_OFF])  # 100..112
    ver = tuple(d[D_VER_OFF:D_VER_OFF + 3])
    code = blob[code_off:code_off + wcount * 4]
    if len(code) != wcount * 4:
        raise ValueError(f"{path}: truncated code region")
    return {
        "path": str(path),
        "length": length,
        "id": blob[8:70].split(b"\0")[0].decode("ascii", "replace"),
        "layout_version": blob[7],
        "split_iram": blob[70],
        "section_count": blob[71],
        "soc_model": struct.unpack(">H", blob[72:74])[0],
        "iram_off": iram_off,
        "wcount": wcount,
        "code_off": code_off,
        "version": ver,
        "trailer": struct.unpack(">I", blob[-TRAILER_LEN:])[0],
        "code": code,
        "words": list(struct.unpack(f">{wcount}I", code)),
        "_blob": blob,
    }


def fmt(q):
    return (f"{q['path']}\n"
            f"  id        : {q['id']}\n"
            f"  length    : {q['length']} B (file {Path(q['path']).stat().st_size} B)\n"
            f"  soc_model : 0x{q['soc_model']:04x}   sections: {q['section_count']}"
            f"   split_IRAM: {q['split_iram']}\n"
            f"  version   : {q['version'][0]}.{q['version'][1]}.{q['version'][2]}\n"
            f"  iram_off  : 0x{q['iram_off']:08x}   code_off: {q['code_off']}"
            f"   wcount: {q['wcount']} ({q['wcount']*4} B)\n"
            f"  trailer   : 0x{q['trailer']:08x}")


def cmd_info(args):
    out = []
    for p in args.blobs:
        q = parse_blob(p)
        if args.json:
            out.append({k: v for k, v in q.items()
                        if k not in ("code", "words", "_blob")})
        else:
            print(fmt(q))
    if args.json:
        print(json.dumps(out, indent=1))


def cmd_dump_words(args):
    q = parse_blob(args.blob)
    lo = args.start or 0
    hi = min(args.end if args.end is not None else q["wcount"], q["wcount"])
    for i in range(lo, hi):
        w = q["words"][i]
        print(f"w{i:5d}  0x{i*4:05x}  {w:08x}")


def cmd_dispatch(args):
    q = parse_blob(args.blob)
    print(f"{args.blob}: {q['id']}")
    for i in range(24):
        br, pad = q["words"][i * 2], q["words"][i * 2 + 1]
        if br == 0xFFFFFFFF and pad == 0xFFFFFFFF:
            print(f"  slot {i:2d}: --")
            continue
        tgt = br & 0xFFFF
        note = ""
        if i == 0:
            note = f"version stamp 0x{pad:08x}"
        elif pad != 0xFFFFFFFF:
            note = f"pad=0x{pad:08x} (unusual)"
        print(f"  slot {i:2d}: 0x{br:08x} -> w{48 + tgt:5d}"
              f" (byte 0x{tgt*4 + 0xC0:05x})  {note}")


# --- trailer integrity-word analysis -------------------------------------

def _crc_reflected(data, init, xorout, poly=0xEDB88320):
    table = getattr(_crc_reflected, "_t", None)
    if table is None:
        table = []
        for n in range(256):
            c = n
            for _ in range(8):
                c = (c >> 1) ^ poly if c & 1 else c >> 1
            table.append(c)
        _crc_reflected._t = table
    crc = init
    for b in data:
        crc = table[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return (crc ^ xorout) & 0xFFFFFFFF


def _crc_msb(data, init, xorout, poly=0x04C11DB7):
    table = getattr(_crc_msb, "_t", None)
    if table is None:
        table = []
        for n in range(256):
            c = n << 24
            for _ in range(8):
                c = ((c << 1) ^ poly) & 0xFFFFFFFF if c & 0x80000000 \
                    else (c << 1) & 0xFFFFFFFF
            table.append(c)
        _crc_msb._t = table
    crc = init
    for b in data:
        crc = table[((crc >> 24) ^ b) & 0xFF] ^ ((crc << 8) & 0xFFFFFFFF)
    return (crc ^ xorout) & 0xFFFFFFFF


CRC_VARIANTS = {
    "CRC-32/ISO-HDLC (zlib)": lambda d: zlib.crc32(d) & 0xFFFFFFFF,
    "CRC-32/JAMCRC":          lambda d: _crc_reflected(d, 0xFFFFFFFF, 0),
    "CRC-32/XFER":            lambda d: _crc_reflected(d, 0, 0xFFFFFFFF),
    "CRC-32/BZIP2":           lambda d: _crc_msb(d, 0xFFFFFFFF, 0xFFFFFFFF),
    "CRC-32/MPEG-2":          lambda d: _crc_msb(d, 0xFFFFFFFF, 0),
    "CRC-32/POSIX(cksum)":    lambda d: _crc_msb(d, 0, 0xFFFFFFFF),
    "CRC-32/BASE91-C":        lambda d: _crc_msb(d, 0, 0),
}

# FMan trailer integrity word — SOLVED 2026-08-07 (empirical, all tiers):
# raw table-driven CRC-32, reflected IEEE poly 0xEDB88320, init 0, NO final
# complement, over blob[0 : length-4].  (The qe_firmware.rst description
# "crc32(-1, blob, length-4) ^ -1" does NOT apply to FMan blobs.)
def trailer_crc(blob):
    return _crc_reflected(blob[:len(blob) - TRAILER_LEN], 0, 0)


def _ranges(q):
    blob, co, clen = q["_blob"], q["code_off"], q["wcount"] * 4
    n = len(blob)
    return {
        "blob[0:len-4]":      blob[:n - 4],
        "blob[4:len-4]":      blob[4:n - 4],
        "blob[8:len-4]":      blob[8:n - 4],
        "blob[124:len-4]":    blob[HDR_LEN:n - 4],
        "code only":          blob[co:co + clen],
        "blob, trailer=0":    blob[:n - 4] + b"\0\0\0\0",
        "blob[0:len-4]+len":  blob[:n - 4] + struct.pack(">I", n),
    }


def cmd_crc(args):
    qs = [parse_blob(p) for p in args.blobs]
    if not args.brute:
        rc = 0
        for q in qs:
            calc = trailer_crc(q["_blob"])
            ok = calc == q["trailer"]
            rc |= not ok
            print(f"{q['path']}: trailer 0x{q['trailer']:08x} "
                  f"calc 0x{calc:08x}  {'OK' if ok else 'MISMATCH'}")
        sys.exit(rc)
    if len(qs) < 2:
        sys.exit("crc --brute needs >=2 blobs for cross-check")
    print(f"{'variant':26s} {'range':18s} " +
          " ".join(f"{Path(q['path']).name[:20]:>20s}" for q in qs))
    hits = []
    for vname, vf in CRC_VARIANTS.items():
        # evaluate on smallest blob's range set for speed, then confirm
        for rname in _ranges(qs[0]):
            vals = [vf(_ranges(q)[rname]) for q in qs]
            ok = [v == q["trailer"] for v, q in zip(vals, qs)]
            if any(ok):
                hits.append((vname, rname, vals, ok))
    if not hits:
        print("no (variant, range) combination matches the trailer on any blob")
        return
    for vname, rname, vals, ok in hits:
        mark = " ".join(f"{v:>19x} {'OK' if o else 'X '}" for v, o in zip(vals, ok))
        print(f"{vname:26s} {rname:18s} {mark}")
    consistent = [h for h in hits if all(h[3])]
    print("\nCONSISTENT SOLUTION:", *[(v, r) for v, r, _, _ in consistent]
          or "none — trailer is not a plain CRC-32 of any tested scope")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info")
    p.add_argument("blobs", nargs="+")
    p.add_argument("--json", action="store_true")
    p.set_defaults(f=cmd_info)
    p = sub.add_parser("dump-words")
    p.add_argument("blob")
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--end", type=int, default=None)
    p.set_defaults(f=cmd_dump_words)
    p = sub.add_parser("dispatch")
    p.add_argument("blob")
    p.set_defaults(f=cmd_dispatch)
    p = sub.add_parser("crc")
    p.add_argument("blobs", nargs="+")
    p.add_argument("--brute", action="store_true",
                   help="brute-force scope x variant instead of verifying")
    p.set_defaults(f=cmd_crc)
    args = ap.parse_args()
    args.f(args)


if __name__ == "__main__":
    main()
