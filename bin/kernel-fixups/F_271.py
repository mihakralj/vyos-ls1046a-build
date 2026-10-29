"""F-271 (R7 step 1, 2026-10-29): retry the ehash SYNC and count timeouts.

fman_pcd_ehash_sync() (F-254/F-269) gave up on the first 100 ms INV0 timeout
and the caller leaked the record (F-256). Re-arm FMFP_EXTC[INV0] up to three
times before declaring the FMan wedged, and export ehash_sync_timeouts,
ehash_sync_recovered and ehash_sync_wedged as read-only module parameters
(/sys/module/fsl_dpaa_fman/parameters/, shown by ask-check) so the churn
tests can tell a transient stall from a wedge.

Must run after F-269 (the shared sync helper). Idempotent; exits non-zero
unless the anchor matches exactly once.
"""

import sys

MARK = "F-271"
PATH = "drivers/net/ethernet/freescale/fman/fman_pcd.c"

OLD = '/*\n * F-254 SYNC (RM S5.12.14.1): FMFP_EXTC[INV0] waits out lookups that may\n * still hold a pointer the CPU just unlinked. -ETIMEDOUT (F-256) means a\n * stuck task may still reference it: the caller keeps it.\n */\nstatic int fman_pcd_ehash_sync(struct fman_pcd_ehash_table *t, u32 *extc)\n{\n\tstruct fman *fman = t->pcd ? fman_pcd_get_fman(t->pcd) : NULL;\n\tconst u32 inv0 = 0x80000000U;\n\tunsigned int i;\n\tu32 v = 0;\n\n\t*extc = 0;\n\tdma_wmb();\n\tif (!fman)\n\t\treturn 0;\n\tfman_set_fpm_extc(fman, inv0);\n\tfor (i = 0; i < 100000U; i++) {\n\t\tv = fman_get_fpm_extc(fman);\n\t\tif (!(v & inv0))\n\t\t\tbreak;\n\t\tudelay(1);\n\t}\n\t*extc = v;\n\treturn (v & inv0) ? -ETIMEDOUT : 0;\n}\n'
NEW = '/*\n * F-254 SYNC (RM S5.12.14.1): FMFP_EXTC[INV0] waits out lookups that may\n * still hold a pointer the CPU just unlinked. -ETIMEDOUT (F-256) means a\n * stuck task may still reference it: the caller keeps it.\n *\n * F-271 (R7 step 1): a timed-out SYNC is re-armed up to FMAN_EHASH_SYNC_TRIES\n * times before the FMan is declared wedged. Counters (called under fe_lock)\n * are read-only module parameters: ehash_sync_timeouts (attempts that timed\n * out), ehash_sync_recovered (SYNCs that succeeded on a retry),\n * ehash_sync_wedged (SYNCs that exhausted every attempt).\n */\n#define FMAN_EHASH_SYNC_TRIES 3\nstatic unsigned int ehash_sync_timeouts;\nstatic unsigned int ehash_sync_recovered;\nstatic unsigned int ehash_sync_wedged;\nmodule_param_named(ehash_sync_timeouts, ehash_sync_timeouts, uint, 0444);\nmodule_param_named(ehash_sync_recovered, ehash_sync_recovered, uint, 0444);\nmodule_param_named(ehash_sync_wedged, ehash_sync_wedged, uint, 0444);\n\nstatic int fman_pcd_ehash_sync(struct fman_pcd_ehash_table *t, u32 *extc)\n{\n\tstruct fman *fman = t->pcd ? fman_pcd_get_fman(t->pcd) : NULL;\n\tconst u32 inv0 = 0x80000000U;\n\tunsigned int i, attempt;\n\tu32 v = 0;\n\n\t*extc = 0;\n\tif (!fman) {\n\t\tdma_wmb();\n\t\treturn 0;\n\t}\n\tfor (attempt = 0; attempt < FMAN_EHASH_SYNC_TRIES; attempt++) {\n\t\tdma_wmb();\n\t\tfman_set_fpm_extc(fman, inv0);\n\t\tfor (i = 0; i < 100000U; i++) {\n\t\t\tv = fman_get_fpm_extc(fman);\n\t\t\tif (!(v & inv0))\n\t\t\t\tbreak;\n\t\t\tudelay(1);\n\t\t}\n\t\t*extc = v;\n\t\tif (!(v & inv0)) {\n\t\t\tif (attempt)\n\t\t\t\tehash_sync_recovered++;\n\t\t\treturn 0;\n\t\t}\n\t\tehash_sync_timeouts++;\n\t}\n\tehash_sync_wedged++;\n\treturn -ETIMEDOUT;\n}\n'

try:
    with open(PATH) as f:
        s = f.read()
except FileNotFoundError:
    print(f"### F-271: FATAL: {PATH} not found")
    sys.exit(1)

if MARK in s:
    print("### F-271: already applied")
    sys.exit(0)

n = s.count(OLD)
if n != 1:
    print(f"### F-271: FATAL: anchor in {PATH} found {n} times (expected 1)")
    sys.exit(1)

with open(PATH, "w") as f:
    f.write(s.replace(OLD, NEW))
print("### F-271: ehash SYNC retries (3 attempts) with timeout/recovered/wedged counters")
