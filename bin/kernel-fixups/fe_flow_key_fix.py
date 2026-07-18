import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
try:
    with open(path) as f:
        src = f.read()
except FileNotFoundError:
    print("### fman_pcd.c: fe_flow fix - file not found")
    sys.exit(0)

changes = 0

# Find the fman_pcd_fe_flow_show function
anchor = "static int fman_pcd_fe_flow_show"
if anchor not in src:
    print("### fman_pcd.c: fe_flow fix - function not found")
    sys.exit(0)

# Find the function body
pos = src.find(anchor)
func_start = src.find("{", pos)
func_end = src.find("\n}\n", func_start)

if func_start < 0 or func_end < 0:
    print("### fman_pcd.c: fe_flow fix - function body not found")
    sys.exit(0)

func_body = src[func_start:func_end+2]

# 1. Add key pointer after r declaration
old_r = "const u8 *r = flow->record;"
new_r = "const u8 *r = flow->record;\n\t\t\tconst u8 *key = r + FMAN_EHASH_FLOW_KEY_OFF;"
if old_r in func_body and "const u8 *key" not in func_body:
    func_body = func_body.replace(old_r, new_r, 1)
    changes += 1
    print("### fman_pcd.c: fe_flow fix - added key pointer")

# 2. Change loop from 16 to flow->key_size
old_loop = "for (i = 0; i < 16; i++)"
new_loop = "for (i = 0; i < flow->key_size; i++)"
if old_loop in func_body:
    func_body = func_body.replace(old_loop, new_loop, 1)
    changes += 1
    print("### fman_pcd.c: fe_flow fix - changed loop to flow->key_size")

# 3. Change r[i] to key[i]
old_print = 'seq_printf(s, "%02x", r[i]);'
new_print = 'seq_printf(s, "%02x", key[i]);'
if old_print in func_body:
    func_body = func_body.replace(old_print, new_print, 1)
    changes += 1
    print("### fman_pcd.c: fe_flow fix - changed r[i] to key[i]")

if changes > 0:
    src = src[:func_start] + func_body + src[func_end+2:]
    with open(path, "w") as f:
        f.write(src)
    print(f"### fman_pcd.c: fe_flow fix applied ({changes} changes)")
else:
    print("### fman_pcd.c: fe_flow fix - no changes needed (already applied?)")
