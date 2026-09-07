#!/usr/bin/env python3
"""generate-sleigh.py — Automated SLEIGH Processor Specification Generator for FMan RISC.

Consumes the 201 canonical instruction definitions from arch/fman-instruction-table.html
and synthesizes a complete, valid Ghidra SLEIGH specification (.slaspec).
"""

import html as htmlmod
import os
import re
import sys

DEFAULT_HTML = "/mnt/builds/vyos-ls1046a-build/arch/fman-instruction-table.html"
DEFAULT_OUT = "/mnt/builds/vyos-ls1046a-build/decomp/ghidra/fman-risc/data/languages/fman-risc.slaspec"

FIELD_RE = re.compile(
    r'<span class="bits">([^<]+)</span>\s*<span class="segment (\w+)">([^<]*)</span>'
)


def load_table(path):
    html = open(path, encoding="utf-8", errors="replace").read()
    rows = re.split(r"<tr[^>]*>", html)
    out = []
    for row in rows:
        m = re.search(r'"mnemonic"><code>([^<]+)</code>', row)
        v = re.search(r"value 0x([0-9a-fA-F]+)", row)
        mk = re.search(r"mask&nbsp; 0x([0-9a-fA-F]+)", row)
        d = re.search(r'class="description">([^<]*)', row)
        pc = re.search(r'class="pseudocode"><code>(.*?)</code></td>', row, re.S)
        fields = FIELD_RE.findall(row)
        if m and v and mk:
            mnemonic = m.group(1).strip()
            pseudo = (
                htmlmod.unescape(re.sub("<[^<]+?>", "", pc.group(1))).strip()
                if pc
                else ""
            )
            has_delay_slot = (
                "execute(pc + 1)" in pseudo
                or "execute pc+1" in pseudo
                or mnemonic.endswith(".comp")
            )
            out.append(
                {
                    "mn": mnemonic,
                    "val": int(v.group(1), 16),
                    "mask": int(mk.group(1), 16),
                    "desc": htmlmod.unescape(d.group(1)).strip() if d else "",
                    "pseudo": pseudo,
                    "fields": fields,
                    "delay_slot": has_delay_slot,
                }
            )
    return out


def slice_name(hi, lo, signed=False):
    s = "s" if signed else ""
    return f"f_{hi}_{lo}_{s}" if signed else f"f_{hi}_{lo}"


def generate_sleigh(table, out_path):
    all_slices = set()
    for e in table:
        for bits, kind, label in e["fields"]:
            if ".." in bits:
                hi, lo = [int(x) for x in bits.split("..")]
            else:
                hi = lo = int(bits)
            all_slices.add((hi, lo))
            if "target" in label or "signed" in label or "displacement" in label:
                all_slices.add((hi, lo, True))

    # Fields used as immediates that alias attached register slices need a
    # separate unattached token field (e.g. shift_u5 at bits 15-11).
    imm_aliases = set()
    for e in table:
        for bits, kind, label in e["fields"]:
            if "immediate" in label and ".." in bits:
                hi, lo = [int(x) for x in bits.split("..")]
                if (hi, lo) in {(20, 16), (15, 11), (10, 6)}:
                    imm_aliases.add((hi, lo))

    lines = []
    lines.append("# fman-risc.slaspec — NXP FMan v3 Controller Microcode RISC ISA")
    lines.append("# Automatically generated from arch/fman-instruction-table.html (201 canonical forms).")
    lines.append("# Target: Ghidra 11.x SLEIGH Compiler.\n")
    lines.append("define endian=big;")
    lines.append("define alignment=4;\n")
    lines.append("# Address spaces")
    lines.append("define space code     type=ram_space       size=4  default;")
    lines.append("define space register type=register_space  size=4;")
    lines.append("define space dmem     type=ram_space       size=2;\n")
    lines.append("# Register file (32 general registers + special registers)")
    lines.append("define register offset=0x00 size=4")
    lines.append("  [ r0  r1  r2  r3  r4  r5  r6  r7  r8  r9  r10 r11 r12 r13 r14 r15")
    lines.append("    r16 r17 r18 r19 r20 r21 r22 r23 r24 r25 r26 r27 r28 r29 r30 r31 ];")
    lines.append("define register offset=0x100 size=4 [ pc sp cc ];")
    lines.append("define register offset=0x110 size=1 [ Z N C P ];\n")

    # Tokens
    lines.append("# Instruction token definitions")
    lines.append("define token instr(32)")
    sorted_slices = sorted(list(all_slices), key=lambda x: (x[0], x[1]), reverse=True)
    for sl in sorted_slices:
        if len(sl) == 3:
            hi, lo, signed = sl
            lines.append(f"    {slice_name(hi, lo, True):<14s} = ({lo},{hi}) signed")
        else:
            hi, lo = sl
            lines.append(f"    {slice_name(hi, lo):<14s} = ({lo},{hi})")
    for hi, lo in sorted(imm_aliases, key=lambda x: (x[0], x[1]), reverse=True):
        lines.append(f"    {slice_name(hi, lo) + '_imm':<14s} = ({lo},{hi})")
    lines.append(";\n")

    # Attach registers to 5-bit register slices
    reg_slices = [s for s in sorted_slices if len(s) == 2 and s[0] - s[1] + 1 == 5]
    if reg_slices:
        att_str = " ".join(slice_name(s[0], s[1]) for s in reg_slices if s in [(20, 16), (15, 11), (10, 6)])
        lines.append(f"# Register attachments for standard 5-bit register fields")
        lines.append(f"attach variables [ {att_str} ]")
        lines.append("  [ r0  r1  r2  r3  r4  r5  r6  r7  r8  r9  r10 r11 r12 r13 r14 r15")
        lines.append("    r16 r17 r18 r19 r20 r21 r22 r23 r24 r25 r26 r27 r28 r29 r30 r31 ];\n")

    # Branch helper subrules
    lines.append("# Sub-rules for branch targets")
    lines.append("rel16_dest: rel is f_15_0_s [ rel = inst_start + f_15_0_s * 4; ] { export *:1 rel; }")
    lines.append("rel14_dest: rel is f_13_0_s [ rel = inst_start + f_13_0_s * 4; ] { export *:1 rel; }\n")

    # Pcodeops
    lines.append("# Black-box and special pipeline pcodeops")
    pcodeops = [
        "fman_bitfield_xform", "fman_cckey_emit", "fman_cckey_prepare",
        "fman_csum_accum", "fman_csum_init", "fman_csum_result", "fman_csum_setup",
        "fman_dma_read", "fman_dma_write", "fman_keycmp_run",
        "fman_ld_sm", "fman_pipeline_setup", "fman_st_sm",
        "fman_task_boundary", "fman_task_complete", "fman_task_handoff",
        "fman_task_redispatch", "fman_task_set_end_nia", "fman_task_set_fqid",
        "fman_taskctx_load2", "fman_tnum_alloc", "fman_task_info",
        "fman_unit_config", "fman_unit_read0", "fman_unit_read32", "fman_unit_submit"
    ]
    for op in sorted(pcodeops):
        lines.append(f"define pcodeop {op};")
    lines.append("\n# Instruction constructors\n")

    seen_signatures = set()

    for idx, e in enumerate(table):
        mn = e["mn"]
        if mn == "fill":
            continue
        sleigh_mn = mn.replace(".", "_")
        has_delay_slot = e["delay_slot"]
        pseudo = e["pseudo"]

        pat_parts = []
        op_parts = []
        semantics = []

        is_branch = False
        target_token = None

        for bits, kind, label in e["fields"]:
            if ".." in bits:
                hi, lo = [int(x) for x in bits.split("..")]
            else:
                hi = lo = int(bits)

            if kind == "fixed":
                val = int(label.split()[1], 2)
                pat_parts.append(f"{slice_name(hi, lo)}=0x{val:x}")
            else:
                name = label.split(":")[0].strip()
                if "target" in name or "displacement" in name:
                    is_branch = True
                    if hi - lo + 1 == 16:
                        pat_parts.append("rel16_dest")
                        op_parts.append("rel16_dest")
                        target_token = "rel16_dest"
                    else:
                        pat_parts.append("rel14_dest")
                        op_parts.append("rel14_dest")
                        target_token = "rel14_dest"
                else:
                    sl = slice_name(hi, lo)
                    if "immediate" in label and (hi, lo) in imm_aliases:
                        sl += "_imm"
                    pat_parts.append(sl)
                    op_parts.append(sl)

        pat_str = " & ".join(pat_parts)
        ops_str = " ".join(op_parts)

        # Semantics synthesis
        if mn in ("ret", "ret.comp"):
            semantics.append("return [r30];")
        elif mn in ("indirect", "indirect.comp", "indirect.m5", "indirect.m5.comp"):
            semantics.append("goto [f_15_11];")
        elif mn in ("call", "call.comp"):
            semantics.append(f"call {target_token};")
        elif mn in ("cbrz14", "cbrz14.comp"):
            semantics.append(f"if (Z != 0) goto {target_token};")
        elif mn in ("cbrnz14", "cbrnz14.comp"):
            semantics.append(f"if (Z == 0) goto {target_token};")
        elif mn in ("cbrn14",):
            semantics.append(f"if (N != 0) goto {target_token};")
        elif mn in ("cbrnn14.comp",):
            semantics.append(f"if (N == 0) goto {target_token};")
        elif mn in ("cbrc14",):
            semantics.append(f"if (C != 0) goto {target_token};")
        elif mn in ("cbrnc14",):
            semantics.append(f"if (C == 0) goto {target_token};")
        elif mn.startswith("brbitclr"):
            semantics.append(f"local b:4 = 0x1f:4 - (f_25_21:4); if ((((f_20_16 >> b) & 1:4) == 0:4)) goto {target_token};")
        elif mn.startswith("brbitset"):
            semantics.append(f"local b:4 = 0x1f:4 - (f_25_21:4); if ((((f_20_16 >> b) & 1:4) != 0:4)) goto {target_token};")
        elif mn.startswith("brctrl0"):
            semantics.append(f"if (cc != 0) goto {target_token};")
        elif mn.startswith("brnotctrl0"):
            semantics.append(f"if (cc == 0) goto {target_token};")
        elif is_branch and target_token:
            semantics.append(f"goto {target_token};")
        elif mn == "fill":
            semantics.append("goto inst_start;")
        elif mn == "task.complete":
            semantics.append("fman_task_complete(); return [r30];")
        elif mn == "task.redispatch":
            semantics.append("fman_task_redispatch(); return [r30];")
        elif mn == "task.handoff":
            semantics.append("fman_task_handoff(); return [r30];")
        elif mn == "task.boundary":
            semantics.append("fman_task_boundary(f_20_16, f_15_11);")
        elif mn == "task.set_fqid":
            semantics.append("fman_task_set_fqid(f_20_16, f_15_11);")
        elif mn == "task.set_end_nia":
            semantics.append("fman_task_set_end_nia(f_20_16, f_15_11);")
        elif mn == "task.info":
            semantics.append("f_20_16 = fman_task_info();")
        elif mn == "tnum.alloc":
            semantics.append("f_20_16 = fman_tnum_alloc();")
        elif mn == "taskctx.load2":
            semantics.append("fman_taskctx_load2(f_20_16);")
        elif mn == "li16":
            semantics.append("f_20_16 = f_15_0:4;")
        elif mn == "lihi16":
            semantics.append("f_20_16 = (f_15_0:4) << 16;")
        elif mn == "addi16":
            semantics.append("f_20_16 = f_20_16 + (f_15_0:4);")
        elif mn == "addhi16":
            semantics.append("f_20_16 = f_20_16 + ((f_15_0:4) << 16);")
        elif mn == "subi16":
            semantics.append("f_20_16 = f_20_16 - (f_15_0:4);")
        elif mn == "andi16":
            semantics.append("f_20_16 = f_20_16 & (f_15_0:4);")
        elif mn == "andhi16":
            semantics.append("f_20_16 = f_20_16 & ((f_15_0:4) << 16);")
        elif mn == "ori16":
            semantics.append("f_20_16 = f_20_16 | (f_15_0:4);")
        elif mn == "orhi16":
            semantics.append("f_20_16 = f_20_16 | ((f_15_0:4) << 16);")
        elif mn == "xori16":
            semantics.append("f_20_16 = f_20_16 ^ (f_15_0:4);")
        elif mn == "not32":
            semantics.append("f_20_16 = ~f_20_16;")
        elif mn == "not32.r10":
            semantics.append("r10 = ~r10;")
        elif mn == "not32.r9":
            semantics.append("r9 = ~r9;")
        elif mn == "add32":
            semantics.append("f_10_6 = f_20_16 + f_15_11;")
        elif mn == "sub32":
            semantics.append("f_10_6 = f_20_16 - f_15_11;")
        elif mn == "and32":
            semantics.append("f_10_6 = f_20_16 & f_15_11;")
        elif mn == "or32":
            semantics.append("f_10_6 = f_20_16 | f_15_11;")
        elif mn == "xor32":
            semantics.append("f_10_6 = f_20_16 ^ f_15_11;")
        elif mn == "lsl32":
            semantics.append("f_10_6 = f_20_16 << (f_15_11 & 0x1f:4);")
        elif mn == "lsl32i":
            semantics.append("f_10_6 = f_20_16 << f_15_11_imm;")
        elif mn == "lsr32":
            semantics.append("f_10_6 = f_20_16 >> (f_15_11 & 0x1f:4);")
        elif mn == "lsr32i":
            semantics.append("f_10_6 = f_20_16 >> f_15_11_imm;")
        elif mn == "asr32":
            semantics.append("f_10_6 = f_20_16 s>> (f_15_11 & 0x1f:4);")
        elif mn == "asr32i":
            semantics.append("f_10_6 = f_20_16 s>> f_15_11_imm;")
        elif mn == "cmp32":
            # r31 is the condition register. The real unit publishes ONE bit
            # selected by raw_10_6 (selector S -> r31[31-S]); model the four
            # most-used selectors 0-3 (ult/eq/sign/eq|sign) as always
            # refreshed - approximation, but makes br* on r31[31:28] decode.
            semantics.extend([
                "local lt:4 = 0:4;",
                "if (f_20_16 < f_15_11) goto <lt_set>;",
                "goto <eq_chk>;",
                "<lt_set> lt = 0x80000000:4;",
                "<eq_chk> local eq:4 = 0:4;",
                "if (f_20_16 == f_15_11) goto <eq_set>;",
                "goto <sig_chk>;",
                "<eq_set> eq = 0x40000000:4;",
                "<sig_chk> if ((f_20_16 - f_15_11) s< 0:4) goto <sig_set>;",
                "goto <publish>;",
                "<sig_set> lt = 0x20000000:4;",
                "<publish> r31 = (r31 & 0x0fffffff:4) | lt | eq;",
            ])
        elif mn == "cmpi16":
            semantics.extend([
                "Z = (f_20_16 == (f_15_0:4));",
                "N = ((f_20_16 - (f_15_0:4)) s< 0:4);",
                "C = (f_20_16 < (f_15_0:4));",
            ])
        elif mn == "cmpeqi16":
            semantics.append("Z = (f_20_16 == (f_15_0:4));")
        elif mn == "test32":
            semantics.append("Z = (r2 == 0:4);")
        elif mn == "testand32":
            semantics.append("Z = ((f_20_16 & f_15_11) == 0:4);")
        elif mn == "testor32":
            semantics.append("Z = ((f_20_16 | f_15_11) == 0:4);")
        elif mn == "testxor32":
            semantics.append("Z = ((f_20_16 ^ f_15_11) == 0:4);")
        elif mn == "tstandi16":
            semantics.append("Z = ((f_20_16 & (f_15_0:4)) == 0:4);")
        elif mn == "tstandhi16":
            semantics.append("Z = ((f_20_16 & ((f_15_0:4) << 16)) == 0:4);")
        elif mn == "tstori16":
            semantics.extend([
                "local o:4 = f_20_16 | (f_15_0:4);",
                "C = 0:1;",
                "Z = (o == 0:4);",
                "N = (o s< 0:4);",
            ])
        elif mn == "tstaddi16":
            semantics.extend([
                "local s:4 = f_20_16 + (f_15_0:4);",
                "C = (s < f_20_16);",
                "Z = (s == 0:4);",
                "N = (s s< 0:4);",
            ])
        elif mn == "tstimm16":
            semantics.append("Z = (f_15_0 == 0:2);")
        elif mn == "andi16z":
            semantics.append("r0 = r0 & (f_15_0:4); Z = (r0 == 0:4);")
        elif mn.startswith("memw.readz"):
            semantics.extend([
                "local addr:2 = f_10_0;", "addr = addr + f_15_11[0,16];",
                "f_20_16 = *[dmem]:4 addr;", "Z = (f_20_16 == 0:4);",
            ])
        elif mn.startswith("memw.read"):
            semantics.extend([
                "local addr:2 = f_10_0;", "addr = addr + f_15_11[0,16];",
                "f_20_16 = *[dmem]:4 addr;",
            ])
        elif mn.startswith("memw.write"):
            semantics.extend([
                "local addr:2 = f_10_0;", "addr = addr + f_15_11[0,16];",
                "*[dmem]:4 addr = f_20_16;",
            ])
        elif mn.startswith("memh.read"):
            semantics.extend([
                "local addr:2 = f_10_0;", "addr = addr + f_15_11[0,16];",
                "f_20_16 = zext(*[dmem]:2 addr);",
            ])
        elif mn.startswith("memhu.readz"):
            semantics.extend([
                "local addr:2 = f_10_0;", "addr = addr + f_15_11[0,16];",
                "f_20_16 = zext(*[dmem]:2 addr);", "Z = (f_20_16 == 0:4);",
            ])
        elif mn.startswith("memh.write"):
            semantics.extend([
                "local addr:2 = f_10_0;", "addr = addr + f_15_11[0,16];",
                "*[dmem]:2 addr = f_20_16[0,16];",
            ])
        elif mn.startswith("memb.read"):
            semantics.extend([
                "local addr:2 = f_10_0;", "addr = addr + f_15_11[0,16];",
                "f_20_16 = zext(*[dmem]:1 addr);",
            ])
        elif mn.startswith("membu.readz"):
            semantics.extend([
                "local addr:2 = f_10_0;", "addr = addr + f_15_11[0,16];",
                "f_20_16 = zext(*[dmem]:1 addr);", "Z = (f_20_16 == 0:4);",
            ])
        elif mn.startswith("membs.readz"):
            semantics.extend([
                "local addr:2 = f_10_0;", "addr = addr + f_15_11[0,16];",
                "f_20_16 = sext(*[dmem]:1 addr);", "Z = (f_20_16 == 0:4);",
            ])
        elif mn.startswith("memb.write"):
            semantics.extend([
                "local addr:2 = f_10_0;", "addr = addr + f_15_11[0,16];",
                "*[dmem]:1 addr = f_20_16[0,8];",
            ])
        elif mn.startswith("memd.read"):
            semantics.extend([
                "local addr:2 = f_10_0;", "addr = addr + f_15_11[0,16];",
                "f_20_16 = *[dmem]:4 addr;",
            ])
        elif mn.startswith("memd.write"):
            semantics.extend([
                "local addr:2 = f_10_0;", "addr = addr + f_15_11[0,16];",
                "*[dmem]:4 addr = f_20_16;",
            ])
        elif mn.startswith("clz32"):
            semantics.append("r7 = lzcount(r1);")
        elif mn.startswith("dma.read256") or mn.startswith("dma.read64"):
            semantics.extend([
                "local src:2 = f_20_16[0,16];",
                "local dst:2 = f_15_11[0,16];",
                "*[dmem]:4 (dst + 0) = *[dmem]:4 (src + 0);",
                "*[dmem]:4 (dst + 4) = *[dmem]:4 (src + 4);",
                "*[dmem]:4 (dst + 8) = *[dmem]:4 (src + 8);",
                "*[dmem]:4 (dst + 12) = *[dmem]:4 (src + 12);",
                "fman_dma_read();",
            ])
        elif mn == "dma.read8":
            semantics.extend([
                "local src:2 = f_20_16[0,16];",
                "local dst:2 = f_15_11[0,16];",
                "*[dmem]:4 (dst + 0) = *[dmem]:4 (src + 0);",
                "*[dmem]:4 (dst + 4) = *[dmem]:4 (src + 4);",
                "fman_dma_read();",
            ])
        elif mn == "dma.read8.muramimm":
            semantics.extend([
                "local src:2 = f_20_16[0,16];",
                "local dst:2 = f_10_0;",
                "*[dmem]:4 (dst + 0) = *[dmem]:4 (src + 0);",
                "*[dmem]:4 (dst + 4) = *[dmem]:4 (src + 4);",
                "fman_dma_read();",
            ])
        elif mn.startswith("dma.write"):
            semantics.extend([
                "local dst:2 = f_20_16[0,16];",
                "local src:2 = f_15_11[0,16];",
                "*[dmem]:4 (dst + 0) = *[dmem]:4 (src + 0);",
                "*[dmem]:4 (dst + 4) = *[dmem]:4 (src + 4);",
                "fman_dma_write();",
            ])
        elif "dma" in mn:
            semantics.append("fman_dma_read();")
        elif mn.startswith("csum"):
            semantics.append("fman_csum_accum();")
        elif mn.startswith("keycmp"):
            semantics.append("r0 = fman_keycmp_run();")
        else:
            semantics.append("")

        sem_str = " ".join(semantics)
        delay_stmt = "delayslot(1); " if has_delay_slot else ""

        # Format constructor
        sig = f":{sleigh_mn} {ops_str} is {pat_str}"
        if sig not in seen_signatures:
            seen_signatures.add(sig)
            lines.append(f"{sig} {{ {delay_stmt}{sem_str} }}")

    # Catch-all
    lines.append("\n# Catch-all opcode (least specific)")
    lines.append(":unk f_31_0 is f_31_0 { }")
    lines.append(":nop is f_31_0=0xffffffff { }")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Generated SLEIGH specification written to: {out_path} ({len(table)} instructions)")


def main():
    table = load_table(DEFAULT_HTML)
    generate_sleigh(table, DEFAULT_OUT)


if __name__ == "__main__":
    main()
