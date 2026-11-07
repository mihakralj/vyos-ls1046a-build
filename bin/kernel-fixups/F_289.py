"""F-289: report failed FE disengage and fence retained state.

F-269 can retain a port table, or an arming table not yet handed to a port,
when its drain fails. Report the error and refuse re-engagement; do not retry
destruction on the assumption that KeyGen was safely disarmed. Use
-ESHUTDOWN, not -EBUSY, because ASK may treat -EBUSY as already armed.

This containment does not establish hardware quiescence or parent-device
teardown safety. After F-288.
"""

import sys

PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
HDR = "include/linux/fsl/fman_pcd.h"

HELPER = (
    "/*\n"
    " * F-289: a failed rollback can leave a table before port handoff, or\n"
    " * a failed disengage can leave an unarmed port entry. Neither proves\n"
    " * hardware detachment; refuse re-arm without retrying destruction.\n"
    " */\n"
    "static int fman_pcd_fe_retained_check(struct fman_pcd *pcd, u8 port_id)\n"
    "{\n"
    "\tif (pcd->fe_arming_v4_table ||\n"
    "\t    (!test_bit(port_id, pcd->fe_port_armed) &&\n"
    "\t     fman_pcd_fe_port_find(pcd, port_id))) {\n"
    "\t\tpr_warn(\"fman_pcd: FE port 0x%02x has unresolved state; engage refused\\n\",\n"
    "\t\t\tport_id);\n"
    "\t\treturn -ESHUTDOWN;\n"
    "\t}\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
)

DISENGAGE_OLD = (
    "void fman_pcd_fe_disengage(struct fman *fm, u8 hw_port_id)\n"
    "{\n"
    "\tstruct fman_pcd *pcd;\n"
    "\n"
    "\tif (!fm)\n"
    "\t\treturn;\n"
    "\tpcd = fman_get_pcd(fm);\n"
    "\tif (!pcd)\n"
    "\t\treturn;\n"
    "\n"
    "\t__fman_pcd_fe_arm_disengage(pcd, hw_port_id);\n"
)
DISENGAGE_NEW = (
    "int fman_pcd_fe_disengage(struct fman *fm, u8 hw_port_id)\n"
    "{\n"
    "\tstruct fman_pcd *pcd;\n"
    "\tint err;\n"
    "\n"
    "\tif (!fm)\n"
    "\t\treturn -EINVAL;\n"
    "\tpcd = fman_get_pcd(fm);\n"
    "\tif (!pcd)\n"
    "\t\treturn -ENXIO;\n"
    "\tif (hw_port_id < 0x08 || hw_port_id >= 0x28)\n"
    "\t\treturn -EINVAL;\n"
    "\n"
    "\t/* F-289: a failed teardown does not establish that KG is disarmed. */\n"
    "\terr = __fman_pcd_fe_arm_disengage(pcd, hw_port_id);\n"
    "\tif (err) {\n"
    "\t\tfe_disengage_retained++;\n"
    "\t\tpr_err(\"fman_pcd: FE disengage port 0x%02x incomplete (%d): FE state retained\\n\",\n"
    "\t\t       hw_port_id, err);\n"
    "\t\treturn err;\n"
    "\t}\n"
)

ENGAGE_ANCHOR = "\t/* F-107/F-122: idempotent engage"

# (path, old, new, expected occurrences of old)
EDITS = [
    (PCD,
     "module_param_named(ehash_poison_clears, ehash_poison_clears, uint, 0444);\n",
     "module_param_named(ehash_poison_clears, ehash_poison_clears, uint, 0444);\n"
     "/* F-289: FE disengages that left the port's FE state retained. */\n"
     "static unsigned int fe_disengage_retained;\n"
     "module_param_named(fe_disengage_retained, fe_disengage_retained, uint, 0444);\n",
     1),
    (PCD,
     "/* ---- FE-VM engage/flow API for ask.ko (0153) ---- */\n",
     HELPER + "/* ---- FE-VM engage/flow API for ask.ko (0153) ---- */\n",
     1),
    (PCD,
     ENGAGE_ANCHOR,
     "\terr = fman_pcd_fe_retained_check(pcd, hw_port_id);\t/* F-289 */\n"
     "\tif (err)\n"
     "\t\treturn err;\n" + ENGAGE_ANCHOR,
     1),
    (PCD, DISENGAGE_OLD, DISENGAGE_NEW, 1),
    (PCD,
     "\tpr_info(\"fman_pcd: FE disengage port 0x%02x\\n\", hw_port_id);\n"
     "}\n"
     "EXPORT_SYMBOL_GPL(fman_pcd_fe_disengage);\n",
     "\tpr_info(\"fman_pcd: FE disengage port 0x%02x\\n\", hw_port_id);\n"
     "\treturn 0;\n"
     "}\n"
     "EXPORT_SYMBOL_GPL(fman_pcd_fe_disengage);\n",
     1),
    (HDR,
     "void fman_pcd_fe_disengage(struct fman *fm, u8 hw_port_id);\n",
     "int fman_pcd_fe_disengage(struct fman *fm, u8 hw_port_id);\t/* F-289 */\n",
     1),
]


def main():
    files = {}
    for path in (PCD, HDR):
        try:
            with open(path) as stream:
                files[path] = stream.read()
        except FileNotFoundError:
            print(f"### F-289: FATAL: {path} not found")
            return 1
    pcd, hdr = files[PCD], files[HDR]
    if "F-289" in pcd or "F-289" in hdr:
        if (pcd.count("F-289") == 4 and "fe_disengage_retained, uint" in pcd
                and "int fman_pcd_fe_disengage(" in hdr):
            print("### F-289: already applied")
            return 0
        print("### F-289: FATAL: partially applied tree")
        return 1
    for i, (path, old, new, expected) in enumerate(EDITS):
        count = files[path].count(old)
        if count != expected:
            print(f"### F-289: FATAL: anchor {i} found {count} times (expected {expected})")
            return 1
        files[path] = files[path].replace(old, new)
    for path, text in files.items():
        with open(path, "w") as stream:
            stream.write(text)
    print("### F-289: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
