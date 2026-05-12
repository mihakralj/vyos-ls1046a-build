// SPDX-License-Identifier: GPL-2.0
/*
 * ASK 2.0 - kunit suite registration (PR4 / M0.4)
 *
 * The harness module ask_kunit.ko is built only when
 * CONFIG_NET_ASK_KUNIT_TEST is enabled. Loading it registers every
 * ASK kunit suite via kunit_test_suites(). Results are printed to
 * dmesg in KTAP format and surfaced through /sys/kernel/debug/kunit/
 * when the kernel is booted with kunit=enable.
 *
 * PR4 wires exactly one suite (ask_dummy_suite, defined in
 * ask_test_dummy.c). M1 PRs will append further suites here as new
 * subsystems land:
 *   - ask_genl_suite        (PR5)
 *   - ask_hostcmd_suite     (PR6, golden-hex tests for spec sec 12.5)
 *   - ask_flow_suite        (PR7, rhashtable RCU lookup/insert/remove)
 *   - ask_flow_offload_suite (PR8, flow_block_cb dispatch)
 *
 * Keep the suite array in declaration order so a missing/duplicated
 * registration fails to compile rather than silently dropping
 * coverage.
 */

#include <kunit/test.h>
#include <linux/module.h>

extern struct kunit_suite ask_dummy_suite;
extern struct kunit_suite ask_hostcmd_suite;
extern struct kunit_suite ask_flow_suite;
extern struct kunit_suite ask_flow_offload_suite;

/*
 * kunit_test_suites() is variadic: append further suites here as the
 * M1 PRs land them (ask_genl_suite, etc.). Order does not matter for
 * execution but stays canonical for readability.
 */
kunit_test_suites(&ask_dummy_suite, &ask_hostcmd_suite, &ask_flow_suite,
  &ask_flow_offload_suite);

MODULE_AUTHOR("ASK 2.0 contributors");
MODULE_DESCRIPTION("ASK 2.0 kunit test harness");
MODULE_LICENSE("GPL v2");