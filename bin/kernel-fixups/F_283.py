"""F-283: opt-in, read-only timing telemetry for real ehash SYNC attempts."""

import sys

MARK = "F-283"
PATH = "drivers/net/ethernet/freescale/fman/fman_pcd.c"

INCLUDE_OLD = "#include <linux/kernel.h>\n"
INCLUDE_NEW = "#include <linux/kernel.h>\n#include <linux/ktime.h>\n"

PARAM_OLD = (
    "module_param_named(ehash_sync_injected, ehash_sync_injected, uint, 0444);\n"
)
PARAM_NEW = PARAM_OLD + r'''
/* F-283: opt-in timing diagnostics; these counters do not affect SYNC. */
static bool ehash_sync_diag;
static unsigned int ehash_sync_diag_attempts;
static unsigned int ehash_sync_diag_completed;
static unsigned int ehash_sync_diag_pending;
static unsigned int ehash_sync_diag_max_polls;
static unsigned int ehash_sync_diag_last_extc;
static unsigned int ehash_sync_diag_last_pending_extc;
static unsigned long long ehash_sync_diag_max_ns;
static unsigned long long ehash_sync_diag_lt_1us;
static unsigned long long ehash_sync_diag_lt_10us;
static unsigned long long ehash_sync_diag_lt_100us;
static unsigned long long ehash_sync_diag_lt_1ms;
static unsigned long long ehash_sync_diag_lt_10ms;
static unsigned long long ehash_sync_diag_lt_100ms;
static unsigned long long ehash_sync_diag_ge_100ms;
module_param_named(ehash_sync_diag_enable, ehash_sync_diag, bool, 0600);
module_param_named(ehash_sync_diag_attempts, ehash_sync_diag_attempts, uint, 0444);
module_param_named(ehash_sync_diag_completed, ehash_sync_diag_completed, uint, 0444);
module_param_named(ehash_sync_diag_pending, ehash_sync_diag_pending, uint, 0444);
module_param_named(ehash_sync_diag_max_polls, ehash_sync_diag_max_polls, uint, 0444);
module_param_named(ehash_sync_diag_last_extc, ehash_sync_diag_last_extc, uint, 0444);
module_param_named(ehash_sync_diag_last_pending_extc, ehash_sync_diag_last_pending_extc, uint, 0444);
module_param_named(ehash_sync_diag_max_ns, ehash_sync_diag_max_ns, ullong, 0444);
module_param_named(ehash_sync_diag_lt_1us, ehash_sync_diag_lt_1us, ullong, 0444);
module_param_named(ehash_sync_diag_lt_10us, ehash_sync_diag_lt_10us, ullong, 0444);
module_param_named(ehash_sync_diag_lt_100us, ehash_sync_diag_lt_100us, ullong, 0444);
module_param_named(ehash_sync_diag_lt_1ms, ehash_sync_diag_lt_1ms, ullong, 0444);
module_param_named(ehash_sync_diag_lt_10ms, ehash_sync_diag_lt_10ms, ullong, 0444);
module_param_named(ehash_sync_diag_lt_100ms, ehash_sync_diag_lt_100ms, ullong, 0444);
module_param_named(ehash_sync_diag_ge_100ms, ehash_sync_diag_ge_100ms, ullong, 0444);

static void fman_pcd_ehash_sync_diag_record(bool enabled, u64 started_ns,
					     unsigned int polls, u32 extc)
{
	u64 elapsed_ns;

	if (!enabled)
		return;

	elapsed_ns = ktime_get_ns() - started_ns;
	ehash_sync_diag_attempts++;
	ehash_sync_diag_last_extc = extc;
	if (extc & 0x80000000U)
		ehash_sync_diag_pending++;
	else
		ehash_sync_diag_completed++;
	if (extc & 0x80000000U)
		ehash_sync_diag_last_pending_extc = extc;
	if (polls > ehash_sync_diag_max_polls)
		ehash_sync_diag_max_polls = polls;
	if (elapsed_ns > ehash_sync_diag_max_ns)
		ehash_sync_diag_max_ns = elapsed_ns;
	if (elapsed_ns < 1000)
		ehash_sync_diag_lt_1us++;
	else if (elapsed_ns < 10000)
		ehash_sync_diag_lt_10us++;
	else if (elapsed_ns < 100000)
		ehash_sync_diag_lt_100us++;
	else if (elapsed_ns < 1000000)
		ehash_sync_diag_lt_1ms++;
	else if (elapsed_ns < 10000000)
		ehash_sync_diag_lt_10ms++;
	else if (elapsed_ns < 100000000)
		ehash_sync_diag_lt_100ms++;
	else
		ehash_sync_diag_ge_100ms++;
}
'''

LOOP_OLD = (
    "\tfor (attempt = 0; attempt < FMAN_EHASH_SYNC_TRIES; attempt++) {\n"
    "\t\tdma_wmb();\n"
)
LOOP_NEW = (
    "\tfor (attempt = 0; attempt < FMAN_EHASH_SYNC_TRIES; attempt++) {\n"
    "\t\tbool diag = READ_ONCE(ehash_sync_diag);\n"
    "\t\tu64 started_ns = diag ? ktime_get_ns() : 0;\n"
    "\n"
    "\t\tdma_wmb();\n"
)

RESULT_OLD = "\t\t*extc = v;\n"
RESULT_NEW = (
    "\t\t*extc = v;\n"
    "\t\tif (diag)\n"
    "\t\t\tfman_pcd_ehash_sync_diag_record(true, started_ns,\n"
    "\t\t\t\t\t\t i == 100000U ? i : i + 1U, v);\n"
)

try:
    with open(PATH) as f:
        source = f.read()
except FileNotFoundError:
    print(f"### F-283: FATAL: {PATH} not found")
    sys.exit(1)

if MARK in source:
    print("### F-283: already applied")
    sys.exit(0)

anchors = (
    (INCLUDE_OLD, 1, "ktime include"),
    (PARAM_OLD, 1, "F-282 parameters"),
    (LOOP_OLD, 1, "SYNC attempt loop"),
    (RESULT_OLD, 1, "SYNC attempt result"),
)
for anchor, expected, name in anchors:
    count = source.count(anchor)
    if count != expected:
        print(f"### F-283: FATAL: {name} anchor found {count} times (expected {expected})")
        sys.exit(1)

source = source.replace(INCLUDE_OLD, INCLUDE_NEW, 1)
source = source.replace(PARAM_OLD, PARAM_NEW, 1)
source = source.replace(LOOP_OLD, LOOP_NEW, 1)
source = source.replace(RESULT_OLD, RESULT_NEW, 1)

with open(PATH, "w") as f:
    f.write(source)
print("### F-283: opt-in ehash SYNC latency and pending-state telemetry")
