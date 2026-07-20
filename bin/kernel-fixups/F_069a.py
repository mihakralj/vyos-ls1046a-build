import sys

path = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
try:
    with open(path) as f: src = f.read()
except FileNotFoundError:
    print("### dpaa_eth.c: F-069a v9 file not found")
    sys.exit(0)

changes = 0

# 1. extern declarations (definition lives in build-kernel.sh fixup)
if "extern void *fman_pcd_ic_buf_base" not in src:
    first_st = src.find("\nstatic ")
    if first_st > 0:
        src = (src[:first_st+1] +
               "extern void *fman_pcd_ic_vaddr;\n" +
               "extern void *fman_pcd_ic_buf_base;\n" +
               src[first_st+1:])
        changes += 1
        print("### dpaa_eth.c: F-069a v9 externs added")

# 2. Capture buf_base from dpaa_bp->vaddr (try multiple variable names)
captured = "fman_pcd_ic_buf_base = "
if captured not in src:
    anchor = "phys_to_virt(addr)"
    pos = src.find(anchor)
    if pos > 0:
        eol = src.find('\n', pos)
        # Search for buffer-pool variable near the vaddr assignment
        # Try: dpaa_bp, bp, dpaa_bp_ptr, priv->bp
        section = src[pos-300:pos+800]
        bp_var = None
        for var in ['dpaa_bp->vaddr', 'bp->vaddr', 'dbp->vaddr']:
            if var in section:
                bp_var = var
                break
        if bp_var:
            capture = ('\n\n\t/* F-069a v9: capture buffer base + frame-data vaddr */\n'
                       f'\tfman_pcd_ic_vaddr = vaddr;\n'
                       f'\tfman_pcd_ic_buf_base = {bp_var};')
            src = src[:eol+1] + capture + src[eol+1:]
            changes += 1
            print(f"### dpaa_eth.c: F-069a v9 buf_base capture using {bp_var}")
        else:
            capture = ('\n\n\t/* F-069a v9: capture frame-data vaddr (no bp var found) */\n'
                       '\tfman_pcd_ic_vaddr = vaddr;')
            src = src[:eol+1] + capture + src[eol+1:]
            changes += 1
            print("### dpaa_eth.c: F-069a v9 vaddr capture only (no bp var)")
    else:
        print("### dpaa_eth.c: F-069a v9 phys_to_virt anchor not found")

if changes:
    with open(path, "w") as f: f.write(src)
    print(f"### dpaa_eth.c: F-069a v9 {changes} change(s) applied")
else:
    print("### dpaa_eth.c: F-069a v9 no changes")
