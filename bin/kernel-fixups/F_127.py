"""F-127: instrument every early return in fman_pcd_fe_engage().

DIAGNOSTIC ONLY — remove once F-125 is closed.

On ISO 2026.07.27-0530 (which verifiably contains F-125 and F-126 in the
compiled kernel), the genl engage path fails with -12:

    ask: hw: fman_pcd_fe_engage port 0x10 failed: -12

But F-126's tags (which instrument __fman_pcd_fe_arm_engage) never fire.
This means the -12 comes from fman_pcd_fe_engage() BEFORE it calls
__fman_pcd_fe_arm_engage().  The wrapper function has its own early
returns (port lookup, params page allocation, F-107 -EBUSY guard) that
F-126 does not cover.

This fixup tags every early return in fman_pcd_fe_engage() with a unique
pr_err, same pattern as F-126.  One board cycle names the exact site.

Output to look for:

    fman_pcd F-127: fe_engage port 0x10 early-return #<n> rc=<err>

Disposition: TEMPORARY. Delete when F-125 closes.
"""

import sys, os, re

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-127: fman_pcd.c not found — skipping")
    sys.exit(0)

src = open(pcd_c).read()

if "F-127: fe_engage port" in src:
    print("### F-127: instrumentation already present")
    sys.exit(0)

# ── Locate the function body ──────────────────────────────────────────
# F-157 (2026-08-01) extended the signature to add `u32 enq_fqid` (R1:
# dedicated TX FQ as the HIT ENQ target).  Accept both the pre-F-157
# 2-arg and post-F-157 3-arg forms so this diagnostic keeps working
# regardless of order.
sig = "int fman_pcd_fe_engage(struct fman *fm, u8 hw_port_id)"
i = src.find(sig)
if i == -1:
    # 3-arg form: "int fman_pcd_fe_engage(struct fman *fm, u8 hw_port_id,\n..."
    m3 = re.search(
        r"int fman_pcd_fe_engage\(struct fman \*fm, u8 hw_port_id,\s*\n\s*u32 enq_fqid\)",
        src)
    if m3:
        i = m3.start()
        sig = m3.group(0)
        print("### F-127: matched post-F-157 3-arg fe_engage signature")
if i == -1:
    print("### F-127: ERROR — fman_pcd_fe_engage not found")
    sys.exit(1)

body_start = src.index("{", i)
end = src.find("\n}\n", body_start)
if end == -1:
    print("### F-127: ERROR — could not find end of function")
    sys.exit(1)
body = src[body_start:end]

# ── Tag every early return ────────────────────────────────────────────
ret_re = re.compile(r'(?m)^([ \t]+)return[ \t]+([^;\n]+);[ \t]*$')

sites = []
counter = [0]

def tag(m):
    indent, expr = m.group(1), m.group(2).strip()
    if expr == "0":
        return m.group(0)
    counter[0] += 1
    n = counter[0]
    line_no = body[:m.start()].count("\n")
    sites.append((n, expr, line_no))
    return (
        f'{indent}do {{\n'
        f'{indent}\tpr_err("fman_pcd F-127: fe_engage port 0x%02x early-return #%d rc=%d\\n",\n'
        f'{indent}\t       (unsigned int)hw_port_id, {n}, (int)({expr}));\n'
        f'{indent}\treturn {expr};\n'
        f'{indent}}} while (0);'
    )

new_body = ret_re.sub(tag, body)

if not sites:
    print("### F-127: ERROR — no early returns matched; refusing to no-op silently")
    sys.exit(1)

src = src[:body_start] + new_body + src[end:]
open(pcd_c, "w").write(src)

for n, expr, ln in sites:
    print("### F-127: tagged return #%d at body line %d -> 'return %s;'" % (n, ln, expr))
print("### F-127: instrumented %d early return(s) in fman_pcd_fe_engage" % len(sites))