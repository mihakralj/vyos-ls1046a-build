import sys

path = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
try:
    with open(path) as f: src = f.read()
except FileNotFoundError:
    print("### F-072 v7 file not found")
    sys.exit(0)

changes = 0

# Add externs
if "extern u64 fman_pcd_kg_hash" not in src:
    first_st = src.find("\nstatic ")
    if first_st > 0:
        src = (src[:first_st+1] +
               "extern u64 fman_pcd_kg_hash;\n"
               "extern unsigned int fman_pcd_hash_off;\n" +
               src[first_st+1:])
        changes += 1
        print("### dpaa_eth.c: F-072 v7 externs added")

# Add capture: only for eth4 (strcmp on net_dev->name)
if "fman_pcd_hash_off = " not in src:
    anchor = "fman_port_get_hash_result_offset"
    pos = src.find(anchor)
    if pos > 0:
        region = src[pos:pos+800]
        be32_pos = region.find("be32_to_cpu(*(__be32 *)")
        if be32_pos > 0:
            actual_eol = src.find('\n', pos + be32_pos)
            if actual_eol > pos + be32_pos:
                capture = (
                    '\n\t/* F-072 v7: capture hash for eth4 only */\n'
                    '\tif (!strcmp(net_dev->name, "eth4")) {\n'
                    '\t\tfman_pcd_hash_off = hash_offset;\n'
                    '\t\tfman_pcd_kg_hash = be64_to_cpu(*(__be64 *)(vaddr + hash_offset));\n'
                    '\t}')
                src = src[:actual_eol+1] + capture + src[actual_eol+1:]
                changes += 1
                print("### dpaa_eth.c: F-072 v7 eth4 strcmp capture")

if changes:
    with open(path, "w") as f: f.write(src)
    print(f"### dpaa_eth.c: F-072 v7 {changes} change(s) applied")
else:
    print("### dpaa_eth.c: F-072 v7 no changes")
