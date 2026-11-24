"""F-324: G5b full mixed-profile Bridge IRB via ETH HXS (slot 0) MAC-exclude dispatch.

Extends F-323 (G4 multi-table dispatch) so a single engaged DPAA port keeps
its primary row-0 50-byte ROUTED table (for IRB L3 forwarding, NAT, VLAN,
PPPoE, F-266 control/TTL punt, tc-flower, ntuple, and WGFP) while attaching a
secondary 16-byte L2_DA class table on ETH HXS (slot 0):

  - Built-in FMC-verified ETH HXS (slot 0) DA-MAC exclusion bytecode emitter
    (fman_pcd_g5b_build_eth_mac_code):
      WR0 = FW[0..47]                         (Ethernet DA, 6 bytes)
      for each mac in mac_exclude[0..cnt-1]:  (1..2 router MACs: br0 + port)
          JMP ret_hxs IF WR0 == mac           (keep default $nia -> row 0 ROUTED)
      RA[93:95] ($nia)   = 0x480300 | sid     (KeyGen direct -> L2_DA scheme)
      RA[77:79] ($hpnia) = 0x480300 | sid
      ret_hxs:
      CLM
      JMP RETURN_HXS                          (continue hard parser: VLAN TCI1/ETYPE!)
  - Fixed 9-word (3 x 16B parser slots) reservation so 1-MAC and 2-MAC updates
    fit in-place at the exact same prs_off and scheme_id without tearing down
    the L2_DA ehash table.
  - Exported fman_pcd_fe_class_update_macs() for atomic in-place MAC updates on
    NETDEV_CHANGEADDR (bridge MAC recalculation or admin MAC change), while
    preserving cls->prev_code (cold-boot zeros) for byte-exact S1->S0 detach.
  - Bidirectional mutual exclusion between ETH HXS (slot 0) and unconditional
    downstream G4 hooks (UDP slot 11, ESP slot 12) so downstream $nia writes
    cannot hijack L2 bridged frames into routed class tables.

Applies after F-323. Idempotent; count-gated anchors.
"""

import sys

PCD_H = "include/linux/fsl/fman_pcd.h"
PCD_C = "drivers/net/ethernet/freescale/fman/fman_pcd.c"

MARK = "F-324"

PCD_H_EDITS = [
    (
        "fman_pcd.h G5b mac_exclude constants and fields",
        "#define FMAN_PCD_FE_CLASS_CODE_MAX\t16\n"
        "struct fman_pcd_fe_class_cfg {\n"
        "\tu8   hxs_slot;\t\t\t/* parser HXS slot (0..15), e.g. 11=UDP, 12=IPsec */\n"
        "\tu8   key_size;\t\t\t/* 1..56 bytes */\n"
        "\tu32  hash_mask;\t\t\t/* power-of-2 minus 1, <= 0x7fff */\n"
        "\tu8   hash_shift;\t\t/* 0..3 */\n"
        "\tbool custom_gec;\t\t/* true: program gec[0..7] + ekfc; false: ekfc only */\n"
        "\tu32  ekfc;\n"
        "\tu32  gec[8];\n"
        "\tu8   code_words;\t\t/* 0 = built-in 4-word direct-dispatch; else 1..16 */\n"
        "\tu8   patch_word_idx;\t\t/* custom code word to OR scheme_id into; 0xff = none */\n"
        "\tu32  code[FMAN_PCD_FE_CLASS_CODE_MAX];\n"
        "};\n",
        "#define FMAN_PCD_FE_CLASS_CODE_MAX\t16\n"
        "#define FMAN_PCD_FE_CLASS_MAC_MAX\t2\n"
        "#define FMAN_PCD_FE_CLASS_MAC_WORDS\t9\n"
        "#define FMAN_PCD_FE_L2_DA_EKFC\t\t0x74000000U\n"
        "struct fman_pcd_fe_class_cfg {\n"
        "\tu8   hxs_slot;\t\t\t/* parser HXS slot (0..15), e.g. 0=ETH, 11=UDP, 12=IPsec */\n"
        "\tu8   key_size;\t\t\t/* 1..56 bytes */\n"
        "\tu32  hash_mask;\t\t\t/* power-of-2 minus 1, <= 0x7fff */\n"
        "\tu8   hash_shift;\t\t/* 0..3 */\n"
        "\tbool custom_gec;\t\t/* true: program gec[0..7] + ekfc; false: ekfc only */\n"
        "\tu32  ekfc;\n"
        "\tu32  gec[8];\n"
        "\tu8   code_words;\t\t/* 0 = built-in direct-dispatch/MAC-filter; else 1..16 */\n"
        "\tu8   patch_word_idx;\t\t/* custom code word to OR scheme_id into; 0xff = none */\n"
        "\tu32  code[FMAN_PCD_FE_CLASS_CODE_MAX];\n"
        "\t/* F-324 (G5b): ETH HXS (slot 0) DA-MAC exclusion filter (1..2 unicast MACs). */\n"
        "\tu8   mac_exclude_cnt;\n"
        "\tu8   mac_exclude[FMAN_PCD_FE_CLASS_MAC_MAX][6];\n"
        "};\n",
    ),
    (
        "fman_pcd.h fman_pcd_fe_class_update_macs decl",
        "int fman_pcd_fe_class_detach(struct fman *fm, u8 hw_port_id, u8 class_idx);\n",
        "int fman_pcd_fe_class_detach(struct fman *fm, u8 hw_port_id, u8 class_idx);\n"
        "int fman_pcd_fe_class_update_macs(struct fman *fm, u8 hw_port_id,\n"
        "\t\t\t\t  u8 class_idx, const u8 macs[][6],\n"
        "\t\t\t\t  u8 mac_cnt);\n",
    ),
]

PCD_C_EDITS = [
    (
        "struct fman_pcd_fe_class mac_filter fields",
        "\tu32  prev_code[FMAN_PCD_FE_CLASS_CODE_MAX];\n"
        "\tstruct fman_pcd_ehash_table *table;\n"
        "};\n",
        "\tu32  prev_code[FMAN_PCD_FE_CLASS_CODE_MAX];\n"
        "\tstruct fman_pcd_ehash_table *table;\n"
        "\tbool mac_filter;\t\t/* F-324: ETH HXS DA-MAC exclusion class */\n"
        "\tu8   mac_exclude_cnt;\n"
        "\tu8   mac_exclude[FMAN_PCD_FE_CLASS_MAC_MAX][6];\n"
        "};\n",
    ),
    (
        "G5b ETH-HXS MAC-exclude bytecode builder",
        "static int fman_pcd_fe_class_write_node(struct muram_info *muram,\n",
        "/* F-324 (G5b): validate 1..2 non-zero unicast router MACs for ETH HXS filter. */\n"
        "static bool fman_pcd_g5b_valid_macs(const u8 macs[][6], u8 mac_cnt)\n"
        "{\n"
        "\tu8 i;\n"
        "\n"
        "\tif (!macs || mac_cnt == 0 || mac_cnt > FMAN_PCD_FE_CLASS_MAC_MAX)\n"
        "\t\treturn false;\n"
        "\tfor (i = 0; i < mac_cnt; i++) {\n"
        "\t\tconst u8 *m = macs[i];\n"
        "\n"
        "\t\tif (m[0] & 0x01U)\n"
        "\t\t\treturn false;\n"
        "\t\tif ((m[0] | m[1] | m[2] | m[3] | m[4] | m[5]) == 0)\n"
        "\t\t\treturn false;\n"
        "\t}\n"
        "\treturn true;\n"
        "}\n"
        "\n"
        "/*\n"
        " * F-324 (G5b): synthesize and relocate the FMC-verified ETH HXS (slot 0)\n"
        " * DA-MAC exclusion program at @prs_off for secondary KeyGen @scheme_id:\n"
        " *   WR0 = FW[0..47]\n"
        " *   for each mac in macs[0..mac_cnt-1]:\n"
        " *     JMP ret_hxs IF WR0 == mac\n"
        " *   RA[93:95] = 0x480300 | scheme_id   ($nia)\n"
        " *   RA[77:79] = 0x480300 | scheme_id   ($hpnia)\n"
        " *   ret_hxs:\n"
        " *   CLM\n"
        " *   JMP RETURN_HXS\n"
        " *\n"
        " * Always pads to FMAN_PCD_FE_CLASS_MAC_WORDS (9 u32 words = 3 parser slots)\n"
        " * so in-place 1<->2 MAC updates reuse the exact same prs_off/prev_code.\n"
        " */\n"
        "static int fman_pcd_g5b_build_eth_mac_code(u16 prs_off, u8 scheme_id,\n"
        "\t\t\t\t\t   const u8 macs[][6], u8 mac_cnt,\n"
        "\t\t\t\t\t   u32 code[FMAN_PCD_FE_CLASS_MAC_WORDS])\n"
        "{\n"
        "\tu16 hw[FMAN_PCD_FE_CLASS_MAC_WORDS * 2];\n"
        "\tu64 vals[FMAN_PCD_FE_CLASS_MAC_MAX];\n"
        "\tu8 nimms[FMAN_PCD_FE_CLASS_MAC_MAX];\n"
        "\tu32 base_pc, pos, ret_pc;\n"
        "\tunsigned int idx = 0, i;\n"
        "\n"
        "\tif (!code || !fman_pcd_g5b_valid_macs(macs, mac_cnt))\n"
        "\t\treturn -EINVAL;\n"
        "\tif (prs_off < FMAN_PCD_G4_PRS_BASE || prs_off >= FMAN_PCD_G4_PRS_END ||\n"
        "\t    (prs_off & 1U) || scheme_id >= 32U)\n"
        "\t\treturn -EINVAL;\n"
        "\n"
        "\tbase_pc = prs_off / 2U;\n"
        "\tpos = 1U;\t/* WR0 = FW[0..47] */\n"
        "\tfor (i = 0; i < mac_cnt; i++) {\n"
        "\t\tu64 v = ((u64)macs[i][0] << 40) | ((u64)macs[i][1] << 32) |\n"
        "\t\t\t((u64)macs[i][2] << 24) | ((u64)macs[i][3] << 16) |\n"
        "\t\t\t((u64)macs[i][4] << 8)  |  (u64)macs[i][5];\n"
        "\t\tu8 n = v < 0x10000ULL ? 1U : (v < 0x100000000ULL ? 2U : 3U);\n"
        "\n"
        "\t\tvals[i] = v;\n"
        "\t\tnimms[i] = n;\n"
        "\t\tpos += 1U + n;\n"
        "\t}\n"
        "\tpos += 6U;\t/* RA[93:95] (3 hw) + RA[77:79] (3 hw) */\n"
        "\tret_pc = base_pc + pos;\n"
        "\tif (ret_pc + 2U > 0x3FEU || pos + 2U > ARRAY_SIZE(hw))\n"
        "\t\treturn -EINVAL;\n"
        "\n"
        "\tmemset(hw, 0, sizeof(hw));\n"
        "\thw[idx++] = 0x97DEU;\t/* LOAD_BITS_FW_TO_WR WR0, FW[0..47] */\n"
        "\tfor (i = 0; i < mac_cnt; i++) {\n"
        "\t\tu64 v = vals[i];\n"
        "\t\tu8 n = nimms[i];\n"
        "\n"
        "\t\t/* COMPARE_WR0_TO_IV: op=0 (==), iv_word_size=(n-1), pc=ret_pc */\n"
        "\t\thw[idx++] = (u16)(0x4000U | ((u16)(n - 1U) << 10) | (ret_pc & 0x3FFU));\n"
        "\t\thw[idx++] = (u16)(v & 0xFFFFU);\n"
        "\t\tif (n >= 2U)\n"
        "\t\t\thw[idx++] = (u16)((v >> 16) & 0xFFFFU);\n"
        "\t\tif (n >= 3U)\n"
        "\t\t\thw[idx++] = (u16)((v >> 32) & 0xFFFFU);\n"
        "\t}\n"
        "\t/* STORE_IV_TO_RA RA[93:95] ($nia) = 0x480300 | scheme_id */\n"
        "\thw[idx++] = 0x095FU;\n"
        "\thw[idx++] = (u16)(0x0300U | (scheme_id & 0x1FU));\n"
        "\thw[idx++] = 0x0048U;\n"
        "\t/* STORE_IV_TO_RA RA[77:79] ($hpnia) = 0x480300 | scheme_id */\n"
        "\thw[idx++] = 0x094FU;\n"
        "\thw[idx++] = (u16)(0x0300U | (scheme_id & 0x1FU));\n"
        "\thw[idx++] = 0x0048U;\n"
        "\t/* ret_hxs: CLM + JMP RETURN_HXS */\n"
        "\thw[idx++] = 0x0001U;\n"
        "\thw[idx++] = 0x1BFEU;\n"
        "\n"
        "\tfor (i = 0; i < FMAN_PCD_FE_CLASS_MAC_WORDS; i++)\n"
        "\t\tcode[i] = ((u32)hw[i * 2U] << 16) | (u32)hw[i * 2U + 1U];\n"
        "\treturn 0;\n"
        "}\n"
        "\n"
        "static int fman_pcd_fe_class_write_node(struct muram_info *muram,\n",
    ),
    (
        "class_attach validate mac_exclude_cnt and code_words",
        "\tif (cfg->code_words > 0 && cfg->patch_word_idx != 0xFF &&\n"
        "\t    cfg->patch_word_idx >= cfg->code_words)\n"
        "\t\treturn -EINVAL;\n"
        "\n"
        "\tpcd = fman_get_pcd(fm);\n"
        "\tmuram = fman_get_muram(fm);\n"
        "\trxport = fman_port_lookup_rx(fm, hw_port_id);\n"
        "\tif (!pcd || !muram)\n"
        "\t\treturn -ENXIO;\n"
        "\tif (!rxport)\n"
        "\t\treturn -ENODEV;\n"
        "\n"
        "\tcode_words = cfg->code_words ? cfg->code_words : 4;\n",
        "\tif (cfg->code_words > 0 && cfg->patch_word_idx != 0xFF &&\n"
        "\t    cfg->patch_word_idx >= cfg->code_words)\n"
        "\t\treturn -EINVAL;\n"
        "\tif (cfg->mac_exclude_cnt > 0) {\n"
        "\t\tif (cfg->hxs_slot != 0 || cfg->code_words != 0 ||\n"
        "\t\t    !fman_pcd_g5b_valid_macs(cfg->mac_exclude,\n"
        "\t\t\t\t\t     cfg->mac_exclude_cnt))\n"
        "\t\t\treturn -EINVAL;\n"
        "\t}\n"
        "\n"
        "\tpcd = fman_get_pcd(fm);\n"
        "\tmuram = fman_get_muram(fm);\n"
        "\trxport = fman_port_lookup_rx(fm, hw_port_id);\n"
        "\tif (!pcd || !muram)\n"
        "\t\treturn -ENXIO;\n"
        "\tif (!rxport)\n"
        "\t\treturn -ENODEV;\n"
        "\n"
        "\tcode_words = cfg->mac_exclude_cnt ? FMAN_PCD_FE_CLASS_MAC_WORDS :\n"
        "\t\t     (cfg->code_words ? cfg->code_words : 4);\n",
    ),
    (
        "class_attach mutual exclusion ETH slot 0 vs UDP/ESP slots 11/12",
        "\t\tif (fp->classes[c].active) {\n"
        "\t\t\tif (fp->classes[c].hxs_slot == cfg->hxs_slot) {\n"
        "\t\t\t\terr = -EBUSY;\n"
        "\t\t\t\tgoto out_unlock;\n"
        "\t\t\t}\n"
        "\t\t} else if (free_c < 0) {\n",
        "\t\tif (fp->classes[c].active) {\n"
        "\t\t\tu8 cur_hxs = fp->classes[c].hxs_slot;\n"
        "\n"
        "\t\t\t/* F-324: ETH HXS (slot 0) RETURN_HXS dispatch must not\n"
        "\t\t\t * coexist with unconditional downstream $nia overrides\n"
        "\t\t\t * (UDP slot 11 / ESP slot 12) on the same port. */\n"
        "\t\t\tif (cur_hxs == cfg->hxs_slot ||\n"
        "\t\t\t    (cfg->hxs_slot == 0 && (cur_hxs == 11 || cur_hxs == 12)) ||\n"
        "\t\t\t    ((cfg->hxs_slot == 11 || cfg->hxs_slot == 12) && cur_hxs == 0)) {\n"
        "\t\t\t\terr = -EBUSY;\n"
        "\t\t\t\tgoto out_unlock;\n"
        "\t\t\t}\n"
        "\t\t} else if (free_c < 0) {\n",
    ),
    (
        "class_attach emit ETH MAC-exclude bytecode",
        "\t/* 4. Prepare soft-parser bytecode (silicon-proven 4-word direct-dispatch\n"
        "\t *    or caller custom words with optional scheme-ID patch). */\n"
        "\tif (cfg->code_words == 0) {\n"
        "\t\tcode[0] = 0x095F0300U | (scheme_id & 0x1FU);\n"
        "\t\tcode[1] = 0x00480001U;\n"
        "\t\tcode[2] = 0x1BFF0001U;\n"
        "\t\tcode[3] = 0x1BFE0000U;\n"
        "\t} else {\n",
        "\t/* 4. Prepare soft-parser bytecode (F-324 ETH MAC-exclude filter,\n"
        "\t *    4-word direct-dispatch, or caller custom words). */\n"
        "\tif (cfg->mac_exclude_cnt > 0) {\n"
        "\t\terr = fman_pcd_g5b_build_eth_mac_code(prs_off, scheme_id,\n"
        "\t\t\t\t\t\t      cfg->mac_exclude,\n"
        "\t\t\t\t\t\t      cfg->mac_exclude_cnt,\n"
        "\t\t\t\t\t\t      code);\n"
        "\t\tif (err)\n"
        "\t\t\tgoto err_del_scheme;\n"
        "\t} else if (cfg->code_words == 0) {\n"
        "\t\tcode[0] = 0x095F0300U | (scheme_id & 0x1FU);\n"
        "\t\tcode[1] = 0x00480001U;\n"
        "\t\tcode[2] = 0x1BFF0001U;\n"
        "\t\tcode[3] = 0x1BFE0000U;\n"
        "\t} else {\n",
    ),
    (
        "class_attach record mac_filter state",
        "\tmemcpy(cls->prev_code, prev_code, sizeof(u32) * code_words);\n"
        "\tcls->table = t;\n"
        "\tcls->active = true;\n",
        "\tmemcpy(cls->prev_code, prev_code, sizeof(u32) * code_words);\n"
        "\tcls->mac_filter = cfg->mac_exclude_cnt > 0;\n"
        "\tcls->mac_exclude_cnt = cfg->mac_exclude_cnt;\n"
        "\tif (cfg->mac_exclude_cnt > 0)\n"
        "\t\tmemcpy(cls->mac_exclude, cfg->mac_exclude,\n"
        "\t\t       sizeof(cls->mac_exclude[0]) * cfg->mac_exclude_cnt);\n"
        "\tcls->table = t;\n"
        "\tcls->active = true;\n",
    ),
    (
        "fman_pcd_fe_class_update_macs implementation",
        "EXPORT_SYMBOL_GPL(fman_pcd_fe_class_detach);\n",
        "EXPORT_SYMBOL_GPL(fman_pcd_fe_class_detach);\n"
        "\n"
        "/*\n"
        " * F-324 (G5b): atomically update the excluded router MAC(s) on an active\n"
        " * ETH HXS (slot 0) class without detaching the L2_DA table or disturbing\n"
        " * cls->prev_code (cold-boot zeros restored on detach).\n"
        " */\n"
        "int fman_pcd_fe_class_update_macs(struct fman *fm, u8 hw_port_id,\n"
        "\t\t\t\t  u8 class_idx, const u8 macs[][6],\n"
        "\t\t\t\t  u8 mac_cnt)\n"
        "{\n"
        "\tstruct fman_pcd *pcd;\n"
        "\tstruct fman_pcd_fe_port *fp;\n"
        "\tstruct fman_pcd_fe_class *cls;\n"
        "\tu32 code[FMAN_PCD_FE_CLASS_MAC_WORDS];\n"
        "\tu32 old_code[FMAN_PCD_FE_CLASS_MAC_WORDS];\n"
        "\tint err;\n"
        "\n"
        "\tif (!fm || !fm->hwp_regs || class_idx == 0 ||\n"
        "\t    class_idx > FMAN_PCD_FE_MAX_CLASSES ||\n"
        "\t    !fman_pcd_g5b_valid_macs(macs, mac_cnt))\n"
        "\t\treturn -EINVAL;\n"
        "\tpcd = fman_get_pcd(fm);\n"
        "\tif (!pcd)\n"
        "\t\treturn -ENXIO;\n"
        "\n"
        "\tmutex_lock(&pcd->fe_lock);\n"
        "\tif (test_bit(hw_port_id, pcd->fe_port_faulted)) {\n"
        "\t\terr = -ESHUTDOWN;\n"
        "\t\tgoto out_unlock;\n"
        "\t}\n"
        "\tfp = fman_pcd_fe_port_find(pcd, hw_port_id);\n"
        "\tif (!fp || !fp->classes[class_idx - 1].active) {\n"
        "\t\terr = -ENOENT;\n"
        "\t\tgoto out_unlock;\n"
        "\t}\n"
        "\tcls = &fp->classes[class_idx - 1];\n"
        "\tif (!cls->mac_filter || cls->hxs_slot != 0 ||\n"
        "\t    cls->code_words != FMAN_PCD_FE_CLASS_MAC_WORDS) {\n"
        "\t\terr = -EINVAL;\n"
        "\t\tgoto out_unlock;\n"
        "\t}\n"
        "\tif (cls->mac_exclude_cnt == mac_cnt &&\n"
        "\t    !memcmp(cls->mac_exclude, macs,\n"
        "\t\t    sizeof(cls->mac_exclude[0]) * mac_cnt)) {\n"
        "\t\terr = 0;\n"
        "\t\tgoto out_unlock;\n"
        "\t}\n"
        "\terr = fman_pcd_g5b_build_eth_mac_code(cls->prs_off, cls->scheme_id,\n"
        "\t\t\t\t\t      macs, mac_cnt, code);\n"
        "\tif (err)\n"
        "\t\tgoto out_unlock;\n"
        "\n"
        "\tmutex_lock(&fman_pcd_punt_lock);\n"
        "\t/* F-324: pass local old_code so a readback mismatch rolls back live\n"
        "\t * parser RAM inside the same PEN=0 window, while preserving\n"
        "\t * cls->prev_code (cold-boot zeros) for S1->S0 detach. */\n"
        "\terr = fman_pcd_prs_write(fm, cls->prs_off, code, old_code,\n"
        "\t\t\t\t cls->code_words);\n"
        "\tmutex_unlock(&fman_pcd_punt_lock);\n"
        "\tif (!err) {\n"
        "\t\tcls->mac_exclude_cnt = mac_cnt;\n"
        "\t\tmemset(cls->mac_exclude, 0, sizeof(cls->mac_exclude));\n"
        "\t\tmemcpy(cls->mac_exclude, macs,\n"
        "\t\t       sizeof(cls->mac_exclude[0]) * mac_cnt);\n"
        "\t} else if (err != -ETIMEDOUT) {\n"
        "\t\tset_bit(hw_port_id, pcd->fe_port_faulted);\n"
        "\t\tpr_err(\"fman_pcd: F-324 port 0x%02x class %u MAC update failed (%d); port faulted\\n\",\n"
        "\t\t       hw_port_id, class_idx, err);\n"
        "\t}\n"
        "out_unlock:\n"
        "\tmutex_unlock(&pcd->fe_lock);\n"
        "\treturn err;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_pcd_fe_class_update_macs);\n",
    ),
]


def apply_one(path: str, edits: list[tuple[str, str, str]]) -> None:
    try:
        with open(path) as f:
            s = f.read()
    except FileNotFoundError:
        print(f"### {MARK}: FATAL: {path} not found")
        sys.exit(1)

    if MARK in s:
        for name, _old, new in edits:
            if new not in s:
                print(f"### {MARK}: FATAL: marker present in {path} but '{name}' missing")
                sys.exit(1)
        print(f"### {MARK}: {path} already applied")
        return

    for name, old, _new in edits:
        n = s.count(old)
        if n != 1:
            print(f"### {MARK}: FATAL: {path} anchor '{name}' found {n} times (expected 1)")
            sys.exit(1)

    for _name, old, new in edits:
        s = s.replace(old, new, 1)

    with open(path, "w") as f:
        f.write(s)
    print(f"### {MARK}: patched {path} ({len(edits)} anchor(s))")


apply_one(PCD_H, PCD_H_EDITS)
apply_one(PCD_C, PCD_C_EDITS)
