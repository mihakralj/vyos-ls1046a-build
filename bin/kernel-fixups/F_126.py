"""F-126: instrument every early return in __fman_pcd_fe_arm_engage().

DIAGNOSTIC ONLY — remove once F-125 is closed.

Why this exists. On ISO 2026.07.27-0501 (which verifiably contains both F-125
changes — build 30238617398 logged "2 change(s) applied") the symptom is
unchanged on both DUTs:

    used 52282 -> 52586 -> 52890 -> 53194     (+304 per failed engage)
    ask: hw: fman_pcd_fe_engage port 0x10 failed: -12

Evidence from dmesg narrows the failing return but does not identify it:

  * "FM_CTL params page at MURAM off 0x58f00" prints, so
    fman_pcd_port_ensure_params_page() SUCCEEDED;
  * "fe_arm: port 0x10 ENGAGED" never prints, so __fman_pcd_fe_arm_engage()
    is what returns the -12;
  * F-125's own "FE_ENTER scaffold alloc failed" pr_warn never fires, so all
    three scaffold allocations SUCCEEDED — part 1 of F-125 is not the path;
  * F_097's "ABORTED — verify failed" never fires, so its verify gate is not
    the early return either;
  * no "F-072 port 0x10 FE buffer pool" line, while port 0x11 logs one with an
    8192-byte pool — so the failure lands before that point.

So the -12 comes from an early return between the scaffold allocation and the
F-072 pool line, and that path still strands the 304-byte scaffold. F-125
guarded the fman_pcd_kg_port_arm_fe() return, which is evidently NOT the
return being taken.

Three inferences in a row have now failed to name it. This stops inferring:
every early return in the function announces itself with a unique tag, so one
board cycle identifies the exact site instead of a fourth guess.

Output to look for:

    fman_pcd F-126: arm_engage port 0x10 early-return #<n> rc=<err>

Then map <n> to the site listed in the build log ("F-126: tagged return #<n>
at ...") and fix that path in F_125.py.

Disposition: TEMPORARY. Delete this fixup and its manifest entry when F-125
closes; it adds a pr_err to every failure path and is not shipping-quality.
"""

import sys, os, re

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-126: fman_pcd.c not found — skipping")
    sys.exit(0)

src = open(pcd_c).read()

if "F-126: arm_engage port" in src:
    print("### F-126: instrumentation already present")
    sys.exit(0)

# ── Locate the function body ──────────────────────────────────────────
sig = "static int __fman_pcd_fe_arm_engage(struct fman_pcd *pcd,"
i = src.find(sig)
if i == -1:
    print("### F-126: ERROR — __fman_pcd_fe_arm_engage not found")
    sys.exit(1)

# Body runs from the first '{' after the signature to the matching brace that
# closes at column 0 (kernel style puts the function's closing brace there).
body_start = src.index("{", i)
end = src.find("\n}\n", body_start)
if end == -1:
    print("### F-126: ERROR — could not find end of function")
    sys.exit(1)
body = src[body_start:end]

# ── Tag every early return ────────────────────────────────────────────
# Matches "return <expr>;" on its own line. Skips a bare "return 0;" that is
# the success path (we still tag non-zero returns of any form).
ret_re = re.compile(r'(?m)^([ \t]+)return[ \t]+([^;\n]+);[ \t]*$')

sites = []
counter = [0]

def tag(m):
    indent, expr = m.group(1), m.group(2).strip()
    if expr == "0":
        return m.group(0)          # success path — leave alone
    counter[0] += 1
    n = counter[0]
    line_no = body[:m.start()].count("\n")
    sites.append((n, expr, line_no))
    return (
        f'{indent}do {{\n'
        f'{indent}\tpr_err("fman_pcd F-126: arm_engage port 0x%02x early-return #{n} rc=%d\\n",\n'
        f'{indent}\t       (unsigned int)port_id, (int)({expr}));\n'
        f'{indent}\treturn {expr};\n'
        f'{indent}}} while (0);'
    )

new_body = ret_re.sub(tag, body)

if not sites:
    print("### F-126: ERROR — no early returns matched; refusing to no-op silently")
    sys.exit(1)

src = src[:body_start] + new_body + src[end:]
open(pcd_c, "w").write(src)

for n, expr, ln in sites:
    print("### F-126: tagged return #%d at body line %d -> 'return %s;'" % (n, ln, expr))
print("### F-126: instrumented %d early return(s) in __fman_pcd_fe_arm_engage" % len(sites))
