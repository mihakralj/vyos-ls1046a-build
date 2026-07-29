import sys
path = "drivers/net/ethernet/freescale/fman/fman_pcd_kg.c"
with open(path) as f:
    src = f.read()

old = ('\tif (rxport)\n'
       '\t\t(void)fman_port_set_cc_base(rxport, 0);\n'
       '\t(void)fman_pcd_kg_port_detach_cc(pcd, hw_port_id);')
new = ('\tif (rxport) {\n'
       '\t\tu32 pp_off;\n'
       '\t\t(void)fman_port_set_cc_base(rxport, 0);\n'
       '\t\tpp_off = fman_port_get_params_page(rxport);\n'
       '\t\tif (pp_off) {\n'
       '\t\t\t(void)fman_port_set_params_page(rxport, 0, NULL);\n'
       '\t\t\tfman_pcd_muram_free(pcd, pp_off, 256);\n'
       '\t\t}\n'
       '\t}\n'
       '\t(void)fman_pcd_kg_port_detach_cc(pcd, hw_port_id);')
if old in src:
    src = src.replace(old, new, 1)
    with open(path, "w") as f:
        f.write(src)
    print("### fman_pcd_kg.c: params page freed on disarm (M2-4)")
else:
    print("### fman_pcd_kg.c: pattern not found (already fixed?)")
