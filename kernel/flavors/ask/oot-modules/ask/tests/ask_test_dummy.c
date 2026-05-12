// SPDX-License-Identifier: GPL-2.0
/*
 * ASK 2.0 - dummy kunit test (PR4 / M0.4)
 *
 * The sole purpose of this file is to prove the harness works:
 *   1. modprobe ask_kunit
 *   2. dmesg shows "ok 1 - ask_dummy::sanity"
 *   3. CI's "kunit" step parses that line as a passing TAP record
 *
 * Once M1 lands real coverage, this dummy stays in place so a broken
 * harness (e.g. KUNIT linker hash collision, suite-array regression)
 * fails fast at "ok 1" instead of leaving the test runner thinking
 * everything is silently skipped.
 *
 * Do NOT delete or expand this file beyond the trivial sanity case.
 * Real subsystem tests belong in ask_test_<subsystem>.c siblings.
 */

#include <kunit/test.h>

static void ask_test_dummy_sanity(struct kunit *test)
{
KUNIT_EXPECT_EQ(test, 1 + 1, 2);
KUNIT_EXPECT_TRUE(test, true);
KUNIT_EXPECT_FALSE(test, false);
}

static struct kunit_case ask_dummy_cases[] = {
KUNIT_CASE(ask_test_dummy_sanity),
{}
};

struct kunit_suite ask_dummy_suite = {
.name = "ask_dummy",
.test_cases = ask_dummy_cases,
};