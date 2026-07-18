import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
try:
    with open(path) as f: src = f.read()
except FileNotFoundError:
    print("### F-071 v18 file not found")
    sys.exit(0)

changes = 0

# 1. Add globals (just hash + hash_off)
if "fman_pcd_kg_hash" not in src:
    first_st = src.find("\nstatic ")
    if first_st > 0:
        src = (src[:first_st+1] +
               "u64 fman_pcd_kg_hash;\n"
               "unsigned int fman_pcd_hash_off;\nvoid *fman_pcd_ic_vaddr;\n" +
               src[first_st+1:])
        changes += 1
        print("### fman_pcd.c: F-071 v18 globals added")

# 2. Add hash_probe_show function
if "static int fman_pcd_hash_probe_show" not in src:
    anchor = "static int fman_pcd_ic_probe_show"
    pos = src.find(anchor)
    if pos > 0:
        show_func = (
            "static int fman_pcd_hash_probe_show(struct seq_file *m, void *v)\n"
            "{\n"
            "\tif (!fman_pcd_hash_off) {\n"
            "\t\tseq_puts(m, \"idle (no eth4 frame captured)\\n\");\n"
            "\t\treturn 0;\n"
            "\t}\n"
            "\tseq_printf(m, \"hash_off=%u captured=%016llx\\n\",\n"
            "\t\tfman_pcd_hash_off, fman_pcd_kg_hash);\n"
            "\treturn 0;\n"
            "}\n\n")
        src = src[:pos] + show_func + src[pos:]
        changes += 1
        print("### fman_pcd.c: F-071 v18 hash_probe_show created")

# 3. Register hash_probe debugfs
if 'debugfs_create_file("hash_probe"' not in src:
    fe_anchor = 'debugfs_create_file("fe_probe"'
    fe_pos = src.find(fe_anchor)
    if fe_pos > 0:
        semi = src.find(';', fe_pos)
        if semi > 0:
            eol = src.find('\n', semi)
            if eol > 0:
                reg_line = (
                    '\n'
                    '\tif (!debugfs_create_file("hash_probe", 0444, pcd->debugfs_dir, pcd,\n'
                    '\t\t\t       &fman_pcd_hash_probe_fops))\n'
                    '\t\tpr_warn("%s: error creating hash_probe\\n", __func__);\n')
                src = src[:eol+1] + reg_line + src[eol+1:]
                changes += 1
                print("### fman_pcd.c: F-071 v18 hash_probe debugfs registered")

# 4. DEFINE_SHOW_ATTRIBUTE after hash_probe_show function
if 'DEFINE_SHOW_ATTRIBUTE(fman_pcd_hash_probe)' not in src:
    func_marker = "static int fman_pcd_hash_probe_show"
    func_pos = src.find(func_marker)
    if func_pos > 0:
        brace_count = 0
        func_end = func_pos
        for i in range(src.find('{', func_pos), len(src)):
            if src[i] == '{': brace_count += 1
            elif src[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    func_end = i + 1
                    break
        src = (src[:func_end] +
               '\nDEFINE_SHOW_ATTRIBUTE(fman_pcd_hash_probe);\n' +
               src[func_end:])
        changes += 1
        print("### fman_pcd.c: F-071 v18 hash_probe fops defined")

if changes:
    with open(path, "w") as f: f.write(src)
    print(f"### fman_pcd.c: F-071 v18 {changes} change(s) applied")
else:
    print("### fman_pcd.c: F-071 v18 no changes")
