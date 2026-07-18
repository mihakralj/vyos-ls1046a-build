import sys
path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

old = ('\tparams_off = fman_port_get_params_page(port);\n'
       '\tif (!params_off)\n'
       '\t\treturn -ENXIO;')
new = ('\tparams_off = fman_port_get_params_page(port);\n'
       '\tif (!params_off) {\n'
       '\t\tint _err = fman_pcd_port_ensure_params_page(pcd, port);\n'
       '\t\tif (_err)\n'
       '\t\t\treturn _err;\n'
       '\t\tparams_off = fman_port_get_params_page(port);\n'
       '\t\tif (!params_off)\n'
       '\t\t\treturn -ENXIO;\n'
       '\t}')
if old in src:
    src = src.replace(old, new, 1)
    with open(path, "w") as f:
        f.write(src)
    print("### fman_pcd.c: fe_port_set lazy params page alloc (M2-4)")
else:
    print("### fman_pcd.c: pattern not found (already fixed?)")
