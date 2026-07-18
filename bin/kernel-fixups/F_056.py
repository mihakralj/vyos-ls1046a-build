import re

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

# Find the pr_info line in fe_arm_engage
marker = '\tpr_info("fman_pcd fe_arm: port 0x%02x ENGAGED FE_ENTER=0x%lx (AC_CC)\\n",'
if marker not in src:
    print("### fman_pcd.c: F-055 marker not found")
else:
    new_code = (
        '\t/* F-055: MUX/Transition AD writes.  The 0146 context_build call\n'
        '\t * failed to apply (F-047 context drift).  Write the MUX chain\n'
        '\t * destination and Transition AD word 1 directly. */\n'
        '\t{\n'
        '\t\tstruct muram_info *muram = fman_get_muram(pcd->fman);\n'
        '\t\tif (muram) {\n'
        '\t\t\tstruct fman_pcd_fe_obj *enq;\n'
        '\t\t\tenq = list_first_entry_or_null(&pcd->fe_enq,\n'
        '\t\t\t\tstruct fman_pcd_fe_obj, node);\n'
        '\t\t\tif (enq && pcd->fe_mux_off) {\n'
        '\t\t\t\tvoid __iomem *mux =\n'
        '\t\t\t\t\tfman_muram_offset_to_vbase(muram,\n'
        '\t\t\t\t\t\tpcd->fe_mux_off);\n'
        '\t\t\t\tiowrite32be((u32)enq->muram_off,\n'
        '\t\t\t\t\tmux);\n'
        '\t\t\t}\n'
        '\t\t\tif (pcd->fe_transition_off && pcd->fe_exit_off) {\n'
        '\t\t\t\tvoid __iomem *trans =\n'
        '\t\t\t\t\tfman_muram_offset_to_vbase(muram,\n'
        '\t\t\t\t\t\tpcd->fe_transition_off);\n'
        '\t\t\t\tiowrite32be((u32)pcd->fe_exit_off,\n'
        '\t\t\t\t\t(u32 __iomem *)trans + 1);\n'
        '\t\t\t}\n'
        '\t\t}\n'
        '\t}\n'
        '\n'
        '\tpr_info("fman_pcd fe_arm: port 0x%02x ENGAGED FE_ENTER=0x%lx (AC_CC)\\n",'
    )
    src = src.replace(marker, new_code, 1)
    print("### fman_pcd.c: F-055 MUX/Transition AD writes inserted")

with open(path, "w") as f:
    f.write(src)
