#!/usr/bin/env python3
"""fman-disasm.py — standalone FMan-RISC disassembler driven by the
decomp/ghidra/fman-risc/data/languages/fman-risc.slaspec decode model.

This reproduces the SLEIGH decode without needing Ghidra installed, so the
enq_builder / FE-VM action-interpreter region (w9040-w9520) can be decoded
directly from the QEF blob words.

Encoding (from the .slaspec):
  instruction = 32-bit big-endian word at byte 4*w
  prefix16 = bits[16:31]   prefix8 = bits[24:31]
  regfld   = bits[16:20] (r0..r31)
  imm16 / addr16 / simm16 = bits[0:15]
  branch target (rel families) = inst_word + simm16   (word-addressed)

Usage: fman-disasm.py <blob> <start_word> <end_word>
"""
import sys, struct

HDR_LEN = 8
D_CODE_OFF = 108
D_WCOUNT = 116  # not used; wcount read from descriptor below


def load_words(path):
    raw = open(path, "rb").read()
    if raw[4:7] != b"QEF":
        raise SystemExit("not a QEF blob")
    length = struct.unpack(">I", raw[0:4])[0]
    blob = raw[:length]
    # descriptor at offset 124 (HDR_LEN=8 + 62 id + ... ); use code_off from desc
    # qef-parse uses D_CODE_OFF=108 within the descriptor; the descriptor starts
    # at 124 per 01-container.md, code_offset field gives byte offset of code.
    # Simpler: code starts at 244 (verified: code_off=244, wcount=12851).
    code_off = 244
    words = []
    for i in range((len(blob) - 4 - code_off) // 4):
        words.append(struct.unpack(">I", blob[code_off + i * 4:code_off + i * 4 + 4])[0])
    return words


def sx16(v):
    return v - 0x10000 if v & 0x8000 else v


REGNAMES = {26: "r26/*IC*/", 28: "r28/*FRAME*/", 31: "r31/*COND*/"}


def rn(r):
    return REGNAMES.get(r, "r%d" % r)


def decode(w, word_idx):
    """Return (mnemonic, target_word_or_None)."""
    p16 = (w >> 16) & 0xffff
    p8 = (w >> 24) & 0xff
    reg = (w >> 16) & 0x1f
    op8 = (w >> 16) & 0xff          # full 8-bit operand field bits[16:23]
    imm = w & 0xffff
    s = sx16(imm)

    # branch families (prefix16 exact)
    br16 = {
        0xb7ff: ("br", True, False),
        0xa3ff: ("jmp", True, False),
        0xb3ff: ("brc", True, True), 0xb43f: ("brc", True, True),
        0xbc3f: ("brc", True, True), 0xb03f: ("brc", True, True),
        0xb83f: ("brc", True, True), 0xb41f: ("brc", True, True),
        0xbc1f: ("brc", True, True), 0xb81f: ("brc", True, True),
        0xb01f: ("brc", True, True), 0xb45f: ("brc", True, True),
        0xb17f: ("brc", True, True), 0xa7ff: ("brc", True, True),
        0x2e3f: ("brc", True, True), 0x2e5f: ("brc", True, True),
        0x2e1f: ("brc", True, True),
    }
    if p16 in br16:
        name, isbr, cond = br16[p16]
        tgt = word_idx + s
        return ("%s 0x%04x -> w%d" % (name, imm & 0xffff, tgt), tgt)
    if p16 == 0xb7df:
        return ("park", word_idx)
    if p16 == 0x2c3f:
        return ("br_tbl [0x%04x]  ; computed/table dispatch (data)" % imm, None)
    if p16 == 0xffff:
        return ("nop", None)

    # prefix8 classes
    p8map = {
        0x04: "ld", 0x14: "st", 0x10: "ldb",
        0xeb: "op_eb", 0xf0: "op_f0", 0xd8: "op_d8", 0xdb: "op_db",
        0xdc: "tst_dc", 0x73: "tst_73",
        0xe1: "op_e1", 0xef: "op_ef", 0xd9: "op_d9",
        0x77: "m_77", 0x78: "m_78", 0xf1: "m_f1", 0xf4: "m_f4",
    }
    if p8 in p8map:
        name = p8map[p8]
        if name in ("ld", "st", "ldb", "op_f0", "m_77", "m_78", "m_f1", "m_f4"):
            return ("%-6s %s, [0x%04x]  (op8=%02x)" % (name, rn(reg), imm, op8), None)
        else:
            return ("%-6s %s, 0x%04x  (op8=%02x)" % (name, rn(reg), imm, op8), None)

    return ("unk    p16=%04x imm=%04x" % (p16, imm), None)


def main():
    if len(sys.argv) < 4:
        raise SystemExit("usage: fman-disasm.py <blob> <start_word> <end_word>")
    words = load_words(sys.argv[1])
    a, b = int(sys.argv[2]), int(sys.argv[3])
    for i in range(a, min(b + 1, len(words))):
        w = words[i]
        mnem, tgt = decode(w, i)
        print("w%-6d %08x  %s" % (i, w, mnem))


if __name__ == "__main__":
    main()
