"""F-096: Call fman_pcd_fe_build_contexts() during fe_arm engage.

Patch 0146 defines fman_pcd_fe_build_contexts() but the call site
was lost when F-091/F-092 modified __fman_pcd_fe_arm_engage().
Without the working-store context, the FE-VM MUX cannot read its
next-FE pointer → FE-VM parks on first frame under load.

This fixup inserts the context build call in __fman_pcd_fe_arm_engage
before the "ENGAGED" log message, as originally intended by 0146.

Disposition: fold-into 0146/0158
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-096: fman_pcd.c not found")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# ── 1. Insert context build call before the ENGAGED log ──

# Find the ENGAGED log message in __fman_pcd_fe_arm_engage
engaged_log = 'pr_info("fman_pcd fe_arm: port 0x%02x ENGAGED'

if engaged_log not in src:
    # Try variant
    engaged_log = 'pr_info("fman_pcd fe_arm: port 0x%02x ENGAGED FE_ENTER=0x%lx (AC_CC)\\n"'
if engaged_log not in src:
    engaged_log = 'pr_info("fman_pcd fe_arm: port 0x%02x ENGAGED (AC_CC)\\n"'

if engaged_log not in src:
    print("### F-096: ENGAGED log message not found")
else:
    context_call = """/* F-096 / 0146: Build FE-VM runtime contexts so MUX can
\t * read its next-FE pointer from the working store.
\t * Without this, the FE-VM parks on first frame under load.
\t */
\tfman_pcd_fe_build_contexts(pcd);

\t"""
    src = src.replace(engaged_log, context_call + engaged_log, 1)
    changes += 1
    print("### F-096: inserted fman_pcd_fe_build_contexts() call before ENGAGED log")

# ── 2. Remove __maybe_unused from fman_pcd_fe_build_contexts ──
# F-085 added __maybe_unused because the function was defined but never
# called. Now that F-096 adds the call, remove the attribute.

unused = "static __maybe_unused void fman_pcd_fe_build_contexts"
if unused in src:
    used = "static void fman_pcd_fe_build_contexts"
    src = src.replace(unused, used, 1)
    changes += 1
    print("### F-096: removed __maybe_unused from fman_pcd_fe_build_contexts (now called)")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-096: {changes} change(s) applied")
else:
    print("### F-096: no changes applied")
