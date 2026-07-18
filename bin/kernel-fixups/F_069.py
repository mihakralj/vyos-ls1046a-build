import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

def assert_one(old, label):
    n = src.count(old)
    if n != 1:
        print(f"FATAL: F-069 {label}: expected 1 match, got {n}", file=sys.stderr)
        sys.exit(1)

changed = 0

old = 'unsigned long mux_off,\n\t\t\t\t    unsigned long miss_off)'
new = ('unsigned long mux_off,\n\t\t\t\t    unsigned long miss_off,\n'
       '\t\t\t\t    unsigned long miss_res_off)')
assert_one(old, "signature")
src = src.replace(old, new, 1); changed += 1
print("### F-069: signature +miss_res_off")

old = 'iowrite32be(0, fe + 4);\t\t\t\t\t/* missResult */'
new = 'iowrite32be((u32)miss_res_off, fe + 4);\t\t/* missResult -> t_ExtHashResult */'
assert_one(old, "w4")
src = src.replace(old, new, 1); changed += 1
print("### F-069: w4 = miss_res_off")

anchor = 'unsigned long fe_hash_off;\t/* sec.5 t_ExtHashFe FE-hash object */'
new_fields = ('\n\tvoid *miss_ctx;\t\t\t/* F-069: 256B DDR miss context */\n'
              '\tdma_addr_t miss_ctx_phys;\t\t/* F-069: physical addr */\n'
              '\tunsigned long miss_res_off;\t\t/* F-069: MURAM result */')
assert_one(anchor, "struct fields")
src = src.replace(anchor, anchor + '\n' + new_fields, 1); changed += 1
print("### F-069: struct fields added")

old = ('fman_pcd_fe_hash_encode(muram, obj->muram_off, t,\n'
       '\t\t\t\tpcd->fe_mux_off, pcd->fe_exit_off);')
new = ('fman_pcd_fe_hash_encode(muram, obj->muram_off, t,\n'
       '\t\t\t\tpcd->fe_mux_off, pcd->fe_exit_off, pcd->miss_res_off);')
assert_one(old, "call site")
src = src.replace(old, new, 1); changed += 1
print("### F-069: call site updated")

encode_anchor = 'fman_pcd_fe_hash_encode(muram, obj->muram_off, t,\n\t\t\t\tpcd->fe_mux_off, pcd->fe_exit_off, pcd->miss_res_off);'
assert_one(encode_anchor, "encode call")
alloc = (
    '\t/* F-069: allocate 256B DDR miss context + 16B MURAM t_ExtHashResult */\n'
    '\tpcd->miss_ctx = dma_alloc_coherent(t->dev, 256, &pcd->miss_ctx_phys, GFP_KERNEL);\n'
    '\tif (!pcd->miss_ctx)\n'
    '\t\treturn -ENOMEM;\n'
    '\tpcd->miss_res_off = fman_pcd_muram_alloc(pcd, 16);\n'
    '\tif (IS_ERR_VALUE(pcd->miss_res_off)) {\n'
    '\t\tdma_free_coherent(t->dev, 256, pcd->miss_ctx, pcd->miss_ctx_phys);\n'
    '\t\tpcd->miss_ctx = NULL;\n'
    '\t\treturn -ENOMEM;\n'
    '\t}\n'
    '\t/* Write t_ExtHashResult: {LIODN=0|phys_hi, phys_lo, 0, 0} */\n'
    '\t{\n'
    '\t\tvoid __iomem *mr = (void __iomem *)fman_muram_offset_to_vbase(muram, pcd->miss_res_off);\n'
    '\t\tu32 plo = (u32)(pcd->miss_ctx_phys & 0xFFFFFFFF);\n'
    '\t\tu32 phi = (u32)((pcd->miss_ctx_phys >> 32) & 0xFFFF);\n'
    '\t\tiowrite32be(phi, mr + 0);\n'
    '\t\tiowrite32be(plo, mr + 4);\n'
    '\t\tiowrite32be(0, mr + 8);\n'
    '\t\tiowrite32be(0, mr + 12);\n'
    '\t}\n'
    '\t'
)
src = src.replace(encode_anchor, alloc + encode_anchor, 1); changed += 1
print("### F-069: DDR miss context + MURAM result allocated")
print("### F-069: NOTE — free on teardown not yet implemented (acceptable DDR leak)")

with open(path, "w") as f:
    f.write(src)
print(f"### F-069: applied {changed} changes")
