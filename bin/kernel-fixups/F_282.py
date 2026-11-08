"""F-282: lab-only ehash SYNC timeout injection (default off).

F-269's timeout quarantine/recovery and F-271's retry/wedge counters cannot be
exercised on silicon because no real FMFP_EXTC[INV0] timeout can be provoked.
Add a root-writable countdown, /sys/module/fsl_dpaa_fman/parameters/
ehash_sync_inject. While it is non-zero, each SYNC attempt still runs the
real INV0 handshake to completion and is then reported as timed out,
decrementing the countdown and ehash_sync_injected (0444). Real completion
means the reported "timeout" is always conservative: the quarantined records
and generations are genuinely unreferenced, so no injected outcome can free
memory a walker still holds.

Writing 1 makes the next attempt recover on retry (timeouts+1, recovered+1);
writing 3 (FMAN_EHASH_SYNC_TRIES) wedges one operation and poisons its table
until a later un-injected SYNC (F-269 recover). Zero = production behaviour.

After F-273 and after the test-ehash-delete harness, which models the
un-instrumented helper. Idempotent; count-gated anchors.
"""

import sys

PATH = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
MARK = "F-282"

A_OLD = "module_param_named(ehash_sync_wedged, ehash_sync_wedged, uint, 0444);\n"
A_NEW = A_OLD + (
    "/* F-282: lab-only countdown of SYNC attempts to report as timed out\n"
    " * after the real INV0 handshake completes. 0 (default) = off. */\n"
    "static unsigned int ehash_sync_inject;\n"
    "static unsigned int ehash_sync_injected;\n"
    "module_param_named(ehash_sync_inject, ehash_sync_inject, uint, 0600);\n"
    "module_param_named(ehash_sync_injected, ehash_sync_injected, uint, 0444);\n"
)

B_OLD = (
    "\t\t*extc = v;\n"
    "\t\tif (!(v & inv0)) {\n"
    "\t\t\tif (attempt)\n"
    "\t\t\t\tehash_sync_recovered++;\n"
    "\t\t\treturn 0;\n"
    "\t\t}\n"
    "\t\tehash_sync_timeouts++;\n"
)
B_NEW = (
    "\t\t*extc = v;\n"
    "\t\tif (!(v & inv0) && READ_ONCE(ehash_sync_inject)) {\n"
    "\t\t\t/* F-282: the SYNC completed; report it as timed out. */\n"
    "\t\t\tWRITE_ONCE(ehash_sync_inject, ehash_sync_inject - 1);\n"
    "\t\t\tehash_sync_injected++;\n"
    "\t\t\tpr_warn(\"fman_pcd: F-282 injected ehash SYNC timeout (attempt %u, %u left)\\n\",\n"
    "\t\t\t\tattempt + 1, ehash_sync_inject);\n"
    "\t\t\tehash_sync_timeouts++;\n"
    "\t\t\tcontinue;\n"
    "\t\t}\n"
    "\t\tif (!(v & inv0)) {\n"
    "\t\t\tif (attempt)\n"
    "\t\t\t\tehash_sync_recovered++;\n"
    "\t\t\treturn 0;\n"
    "\t\t}\n"
    "\t\tehash_sync_timeouts++;\n"
)

try:
    with open(PATH) as f:
        s = f.read()
except FileNotFoundError:
    print(f"### F-282: FATAL: {PATH} not found")
    sys.exit(1)

if MARK in s:
    if s.count("ehash_sync_injected++;") != 1:
        print("### F-282: FATAL: marker present but injection hook missing")
        sys.exit(1)
    print("### F-282: already applied")
    sys.exit(0)

for name, old in (("param", A_OLD), ("sync loop", B_OLD)):
    n = s.count(old)
    if n != 1:
        print(f"### F-282: FATAL: {name} anchor found {n} times (expected 1)")
        sys.exit(1)

s = s.replace(A_OLD, A_NEW, 1).replace(B_OLD, B_NEW, 1)
with open(PATH, "w") as f:
    f.write(s)
print("### F-282: lab ehash SYNC timeout injection (default off)")
