"""F-290: serialize and fence the KeyGen indirect action window.

An action that times out may still complete later.  Keep the indirect window
unavailable until reboot rather than allowing a subsequent caller to overwrite
its staging registers.  This does not prove FE teardown or walker quiescence.
"""

import sys

KG = "drivers/net/ethernet/freescale/fman/fman_keygen.c"
INTERNAL = "drivers/net/ethernet/freescale/fman/fman_keygen_internal.h"

READY_AND_WAIT = """\
/*
 * The caller holds indirect_lock, or owns the unpublished init instance.
 * A foreign outstanding action is as uncertain as one of our own timeouts.
 */
static int keygen_indirect_ready(struct fman_keygen *keygen)
{
\tif (keygen->indirect_faulted)
\t\treturn -ESHUTDOWN;
\tif (ioread32be(&keygen->keygen_regs->fmkg_ar) & FM_KG_KGAR_GO) {
\t\tkeygen->indirect_faulted = true;
\t\tpr_err("KeyGen action already outstanding; indirect window fenced\\n");
\t\treturn -EBUSY;
\t}
\treturn 0;
}

static int keygen_write_ar_wait(struct fman_keygen *keygen, u32 fmkg_ar)
{
\tstruct fman_kg_regs __iomem *regs = keygen->keygen_regs;
\tunsigned int i;

\tiowrite32be(fmkg_ar, &regs->fmkg_ar);
\tfor (i = 0; i < 100000; i++) {
\t\tfmkg_ar = ioread32be(&regs->fmkg_ar);
\t\tif (!(fmkg_ar & FM_KG_KGAR_GO))
\t\t\tbreak;
\t\tudelay(1);
\t}
\tif (fmkg_ar & FM_KG_KGAR_GO) {
\t\tkeygen->indirect_faulted = true;
\t\tpr_err("KeyGen action timed out; indirect window fenced\\n");
\t\treturn -ETIMEDOUT;
\t}
\tif (fmkg_ar & FM_KG_KGAR_ERR) {
\t\tkeygen->indirect_faulted = true;
\t\tpr_err("KeyGen action failed; indirect window fenced\\n");
\t\treturn -EIO;
\t}
\treturn 0;
}
"""

BIND = """\
int keygen_bind_port_to_schemes(struct fman_keygen *keygen,
\t\t\t       u8 scheme_id, bool bind)
{
\tstruct fman_kg_regs __iomem *regs = keygen->keygen_regs;
\tstruct keygen_scheme *scheme;
\tu32 ar_reg, schemes_vector;
\tint err;

\tmutex_lock(&keygen->indirect_lock);
\terr = keygen_indirect_ready(keygen);
\tif (err)
\t\tgoto out;
\tscheme = get_scheme(keygen, scheme_id);
\tif (!scheme || !scheme->used) {
\t\tpr_err("Cannot bind port to an invalid scheme\\n");
\t\terr = -EINVAL;
\t\tgoto out;
\t}
\tschemes_vector = 1U << (31 - scheme_id);
\tar_reg = build_ar_bind_scheme(scheme->hw_port_id, false);
\terr = keygen_write_ar_wait(keygen, ar_reg);
\tif (err)
\t\tgoto out;
\tkeygen_write_sp(regs, schemes_vector, bind);
\tar_reg = build_ar_bind_scheme(scheme->hw_port_id, true);
\terr = keygen_write_ar_wait(keygen, ar_reg);
out:
\tmutex_unlock(&keygen->indirect_lock);
\treturn err;
}
"""

CLS_PLAN = """\
int keygen_cls_plan0_passall(struct fman_keygen *keygen, u8 hw_port_id)
{
\tstruct fman_kg_regs __iomem *regs;
\tu32 ar, rv;
\tint i, err;

\tif (!keygen || !keygen->keygen_regs)
\t\treturn -EINVAL;
\tmutex_lock(&keygen->indirect_lock);
\terr = keygen_indirect_ready(keygen);
\t/* Rewriting group 0 with another port live wedges that port (F-214). */
\tif (err || keygen->cls_plan0_passall)
\t\tgoto out;
\tregs = keygen->keygen_regs;
\tfor (i = 0; i < 8; i++)
\t\tiowrite32be(0xffffffff, &regs->fmkg_indirect[i]);
\tar = FM_KG_KGAR_GO | FM_KG_KGAR_WRITE |
\t     FM_KG_KGAR_SEL_CLS_PLAN_ENTRY |
\t     ((u32)0 << FM_KG_KGAR_NUM_SHIFT) |
\t     ((u32)0xff << FM_KG_KGAR_CLS_PLAN_WSEL_SHIFT) |
\t     (u32)hw_port_id;
\terr = keygen_write_ar_wait(keygen, ar);
\tif (err)
\t\tgoto out;
\tar = FM_KG_KGAR_GO | FM_KG_KGAR_READ |
\t     FM_KG_KGAR_SEL_CLS_PLAN_ENTRY |
\t     ((u32)0 << FM_KG_KGAR_NUM_SHIFT) |
\t     ((u32)0xff << FM_KG_KGAR_CLS_PLAN_WSEL_SHIFT) |
\t     (u32)hw_port_id;
\terr = keygen_write_ar_wait(keygen, ar);
\tif (err)
\t\tgoto out;
\trv = ioread32be(&regs->fmkg_indirect[0]);
\tif (rv != 0xffffffff) {
\t\tkeygen->indirect_faulted = true;
\t\tpr_err("KeyGen cls-plan0 readback 0x%08x != 0xffffffff; indirect window fenced\\n",
\t\t       rv);
\t\terr = -EIO;
\t\tgoto out;
\t}
\tkeygen->cls_plan0_passall = true;
\tpr_info("fman_keygen: F-214 cls-plan group0 pass-all set once (via port 0x%02x)\\n",
\t\thw_port_id);
out:
\tmutex_unlock(&keygen->indirect_lock);
\treturn err;
}
"""


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise ValueError(f"{label}: found {count} anchors, expected 1")
    return text.replace(old, new, 1)


def replace_function(text, start, end, new, label):
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError(f"{label}: missing or duplicate function boundary")
    first = text.index(start)
    last = text.index(end, first)
    return text[:first] + new + text[last:]


def main():
    try:
        with open(KG) as stream:
            kg = stream.read()
        with open(INTERNAL) as stream:
            internal = stream.read()
    except FileNotFoundError as exc:
        print(f"### F-290: FATAL: {exc}")
        return 1

    marker = "F-290: indirect action window"
    if marker in internal:
        if (internal.count(marker) == 1 and
                "static int keygen_indirect_ready(" in kg and
                "KeyGen action timed out; indirect window fenced" in kg and
                "goto err_indirect;" in kg):
            print("### F-290: already applied")
            return 0
        print("### F-290: FATAL: partially applied tree")
        return 1
    try:
        internal = replace_once(
            internal, "#include <linux/io.h>\n",
            "#include <linux/io.h>\n#include <linux/mutex.h>\n", "mutex include")
        internal = replace_once(
            internal, "\tstruct fman_kg_regs __iomem *keygen_regs;\n",
            "\tstruct fman_kg_regs __iomem *keygen_regs;\n"
            "\t/* F-290: indirect action window is fenced after uncertainty. */\n"
            "\tstruct mutex indirect_lock;\n"
            "\tbool indirect_faulted;\n", "KeyGen state")
        kg = replace_once(kg, "#include <linux/slab.h>\n",
                          "#include <linux/delay.h>\n#include <linux/slab.h>\n",
                          "delay include")
        kg = replace_function(
            kg, "static int keygen_write_ar_wait(",
            "\n/* build_ar_scheme", READY_AND_WAIT, "action wait")
        kg = replace_function(
            kg, "int keygen_bind_port_to_schemes(",
            "EXPORT_SYMBOL_GPL(keygen_bind_port_to_schemes);",
            BIND, "port bind")
        kg = replace_function(
            kg, "int keygen_cls_plan0_passall(",
            "EXPORT_SYMBOL_GPL(keygen_cls_plan0_passall);",
            CLS_PLAN, "classification plan")
        kg = replace_once(
            kg,
            "static int keygen_write_scheme(struct fman_kg_regs __iomem *regs, u8 scheme_id,\n"
            "\t\t\t       struct fman_kg_scheme_regs *scheme_regs,\n"
            "\t\t\t\tbool update_counter)\n"
            "{\n\tu32 ar_reg;\n\tint err, i;\n\n",
            "static int keygen_write_scheme(struct fman_keygen *keygen, u8 scheme_id,\n"
            "\t\t\t       struct fman_kg_scheme_regs *scheme_regs,\n"
            "\t\t\t       bool update_counter)\n"
            "{\n\tstruct fman_kg_regs __iomem *regs = keygen->keygen_regs;\n"
            "\tu32 ar_reg;\n\tint err, i;\n\n"
            "\tmutex_lock(&keygen->indirect_lock);\n"
            "\terr = keygen_indirect_ready(keygen);\n"
            "\tif (err)\n\t\tgoto out;\n\n", "scheme window entry")
        kg = replace_once(
            kg,
            "\terr = keygen_write_ar_wait(regs, ar_reg);\n"
            "\tif (err != 0) {\n"
            '\t\tpr_err("Writing Action Register failed\\n");\n'
            "\t\treturn err;\n\t}\n\n\treturn err;\n}\n",
            "\terr = keygen_write_ar_wait(keygen, ar_reg);\n"
            "out:\n\tmutex_unlock(&keygen->indirect_lock);\n"
            "\treturn err;\n}\n", "scheme window exit")
        kg = replace_once(
            kg,
            "\tstruct fman_kg_regs __iomem *keygen_regs = keygen->keygen_regs;\n"
            "\tstruct fman_kg_scheme_regs scheme_regs;\n",
            "\tstruct fman_kg_scheme_regs scheme_regs;\n", "scheme setup state")
        kg = replace_once(
            kg, "keygen_write_scheme(keygen_regs, scheme_id, &scheme_regs, true)",
            "keygen_write_scheme(keygen, scheme_id, &scheme_regs, true)",
            "scheme setup call")
        kg = replace_once(
            kg, "\tkeygen->keygen_regs = keygen_regs;\n",
            "\tkeygen->keygen_regs = keygen_regs;\n"
            "\tmutex_init(&keygen->indirect_lock);\n"
            "\tif (keygen_indirect_ready(keygen))\n"
            "\t\tgoto err_indirect;\n", "init mutex")
        kg = replace_once(
            kg, "\tu32 ar;\n\tint i;\n\n"
            "\t/* Allocate memory for KeyGen driver */\n",
            "\tu32 ar;\n\tint i = 0;\n\n"
            "\t/* Allocate memory for KeyGen driver */\n", "init error index")
        kg = replace_once(
            kg,
            "\t\t/* Clear all pe sp schemes registers */\n"
            "\t\tkeygen_write_sp(keygen_regs, 0xffffffff, false);\n"
            "\t\tar = build_ar_bind_scheme(i, true);\n"
            "\t\tkeygen_write_ar_wait(keygen_regs, ar);\n\n"
            "\t\t/* Clear all pe cpp classification plans registers */\n"
            "\t\tkeygen_write_cpp(keygen_regs, 0);\n"
            "\t\tar = build_ar_bind_cls_plan(i, true);\n"
            "\t\tkeygen_write_ar_wait(keygen_regs, ar);\n",
            "\t\tif (keygen_indirect_ready(keygen))\n"
            "\t\t\tgoto err_indirect;\n"
            "\t\tkeygen_write_sp(keygen_regs, 0xffffffff, false);\n"
            "\t\tar = build_ar_bind_scheme(i, true);\n"
            "\t\tif (keygen_write_ar_wait(keygen, ar))\n"
            "\t\t\tgoto err_indirect;\n\n"
            "\t\tif (keygen_indirect_ready(keygen))\n"
            "\t\t\tgoto err_indirect;\n"
            "\t\tkeygen_write_cpp(keygen_regs, 0);\n"
            "\t\tar = build_ar_bind_cls_plan(i, true);\n"
            "\t\tif (keygen_write_ar_wait(keygen, ar))\n"
            "\t\t\tgoto err_indirect;\n", "init actions")
        kg = replace_once(
            kg, "\treturn keygen;\n}\nEXPORT_SYMBOL(keygen_init);\n",
            "\treturn keygen;\n\nerr_indirect:\n"
            '\tpr_err("KeyGen init failed at port %d; refusing initialization\\n", i);\n'
            "\tkfree(keygen);\n\treturn NULL;\n}\nEXPORT_SYMBOL(keygen_init);\n",
            "init error exit")
    except ValueError as exc:
        print(f"### F-290: FATAL: {exc}")
        return 1
    for path, text in ((KG, kg), (INTERNAL, internal)):
        with open(path, "w") as stream:
            stream.write(text)
    print("### F-290: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
