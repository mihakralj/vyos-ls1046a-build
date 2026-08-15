"""F-196: read-only target-FQID resolver diagnostic.

F-195 proved that production flow actions now supply actual ingress FMan port
IDs, but F-193 still observed target_fqid=0 for both eth3/0x10 and eth4/0x11.
The resolver currently returns only the FM_CTL params-page default-FQID word.

This diagnostic leaves that return behavior unchanged.  It records the page
value and the live, same-port KeyGen scheme base FQID/hash range so a fallback
can be justified (or rejected) from DUT evidence rather than inferred from
source.  It performs no descriptor, MURAM, DDR, KeyGen-register, or packet
path write.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

marker = "F-196(resolve-fqid-diag)"
if marker in src:
    print("### F-196: resolver diagnostic already applied")
    sys.exit(1)

old = """\tstruct muram_info *muram;\n\tvoid __iomem *page;\n\n\tif (!fman)\n\t\treturn 0x200;\n\n\tport = fman_port_lookup_rx(fman, hw_port_id);\n\tif (!port)\n\t\treturn 0x200;\n\n\tparams_off = fman_port_get_params_page(port);\n\tif (!params_off)\n\t\treturn 0x200;\n\n\tmuram = fman_get_muram(fman);\n\tif (!muram)\n\t\treturn 0x200;\n\n\tpage = (void *)fman_muram_offset_to_vbase(muram, params_off);\n\tif (!page)\n\t\treturn 0x200;\n\n\treturn ioread32be((u32 __iomem *)((u8 __iomem *)page +\n\t\t\t\t\t  FMAN_PP_RX_DEFAULT_FQID_OFF));\n}\n"""
new = """\tstruct muram_info *muram;\n\tvoid __iomem *page;\n\tu32 params_fqid;\n\tunsigned int i;\n\n\tif (!fman)\n\t\treturn 0x200;\n\n\tport = fman_port_lookup_rx(fman, hw_port_id);\n\tif (!port)\n\t\treturn 0x200;\n\n\tparams_off = fman_port_get_params_page(port);\n\tif (!params_off)\n\t\treturn 0x200;\n\n\tmuram = fman_get_muram(fman);\n\tif (!muram)\n\t\treturn 0x200;\n\n\tpage = (void *)fman_muram_offset_to_vbase(muram, params_off);\n\tif (!page)\n\t\treturn 0x200;\n\n\tparams_fqid = ioread32be((u32 __iomem *)((u8 __iomem *)page +\n\t\t\t\t\t       FMAN_PP_RX_DEFAULT_FQID_OFF));\n\n\t/* F-196(resolve-fqid-diag): F-195 now supplies the true ingress\n\t * port, but a populated params page returns zero on the production\n\t * path.  Log the read-only candidate state before retaining exactly\n\t * the pre-F-196 return value.  The KeyGen scheme is the live source\n\t * that programs the port's RSS/PCD FQ range; no fallback is selected\n\t * here until the DUT confirms the mapping.\n\t */\n\tdev_info_ratelimited(fman_get_dev(fman),\n\t\t\t     "fe_flow: F-196 resolve hw_port=0x%02x params_off=0x%x params_fqid=0x%x\\n",\n\t\t\t     hw_port_id, params_off, params_fqid);\n\tif (fman->keygen) {\n\t\tfor (i = 0; i < FM_KG_MAX_NUM_OF_SCHEMES; i++) {\n\t\t\tstruct keygen_scheme *scheme = &fman->keygen->schemes[i];\n\n\t\t\tif (READ_ONCE(scheme->used) &&\n\t\t\t    READ_ONCE(scheme->hw_port_id) == hw_port_id)\n\t\t\t\tdev_info_ratelimited(fman_get_dev(fman),\n\t\t\t\t\t     "fe_flow: F-196 scheme=%u hw_port=0x%02x base_fqid=0x%x hash_fqs=0x%x hashing=%u next_engine=%u\\n",\n\t\t\t\t\t     i, hw_port_id, READ_ONCE(scheme->base_fqid),\n\t\t\t\t\t     READ_ONCE(scheme->hash_fqid_count),\n\t\t\t\t\t     READ_ONCE(scheme->use_hashing),\n\t\t\t\t\t     READ_ONCE(scheme->next_engine));\n\t\t}\n\t}\n\n\treturn params_fqid;\n}\n"""

if src.count(old) != 1:
    print(f"### F-196: FATAL: resolver anchor count is {src.count(old)}, expected 1")
    sys.exit(1)

with open(path, "w") as f:
    f.write(src.replace(old, new, 1))

print("### fman_pcd.c: F-196 read-only resolver diagnostics applied (1 block)")
