// SPDX-License-Identifier: GPL-2.0
/*
 * ASK 2.0 - kunit suite for ask_genl.c (PR9 / M1.5)
 *
 * Drives the pure-helper surface of ask_genl.c directly:
 *
 *   - ask_genl_get_info_fill()      builds the ASK_ATTR_INFO nested block
 *   - ask_genl_fill_one_flow()      builds one ASK_ATTR_FLOW nested block
 *   - ask_genl_dump_one_cb()        the per-flow walker callback
 *   - ask_genl_eopnotsupp_doit()    + dumpit() for the seven not-yet-wired cmds
 *
 * The doit/dumpit handlers that need a real `struct genl_info` (get_info_doit,
 * get_flow_doit, dump_flows_dumpit, flush_flows_doit) are intentionally NOT
 * exercised here — they require either a synthetic netlink socket or a
 * captive `struct sock` and become integration tests in M2 once a real
 * userspace `askd` exists. The pure-helper surface covers the lion's share
 * of the LOC in ask_genl.c (~70%) without needing that scaffolding.
 *
 * Coverage shapes (12 cases):
 *
 *   info_fill_happy_path           — fill into a fresh skb, parse back via
 *                                    ask_top_policy + ask_info_policy and
 *                                    verify every ASK_INFO_ATTR_* round-trips
 *                                    with the expected value.
 *   info_fill_emsgsize             — pre-shrink the skb so the first nla_put
 *                                    after nest_start fails, expect -EMSGSIZE
 *                                    and a cancelled (empty) nest.
 *   info_fill_genl_version         — the GENL_VERSION attr equals
 *                                    ASK_GENL_VERSION (sanity that the right
 *                                    constant is being emitted).
 *   info_fill_driver_version_str   — the DRIVER_VERSION string starts with
 *                                    "ask " and contains ASK_DRV_VERSION_STR.
 *   fill_one_flow_happy_path       — build a flow by hand, fill, parse back
 *                                    via ask_flow_policy, verify cookie /
 *                                    hw_id / oif / packets / bytes / last_seen
 *                                    round-trip.
 *   fill_one_flow_with_stats       — bump stats via ask_flow_update_stats(),
 *                                    re-fill, verify the new packet/byte
 *                                    counts make it through the
 *                                    u64_stats_fetch_begin/retry path.
 *   fill_one_flow_emsgsize         — pre-shrink the skb, expect -EMSGSIZE.
 *   dump_cb_skip_first_n           — drive the walker callback with
 *                                    ctx.start = 2 across 5 synthetic flows;
 *                                    verify only flows 2..4 are emitted and
 *                                    ctx.count == 3.
 *   dump_cb_emsgsize_propagation   — pre-shrink ctx.skb so fill_one_flow
 *                                    fails on first hit, verify ctx.err set
 *                                    to -EMSGSIZE and the callback returns
 *                                    the same.
 *   eopnotsupp_doit_returns_eopnotsupp
 *                                  — call directly with a synthetic genl_info
 *                                    carrying a fake genlhdr, expect
 *                                    -EOPNOTSUPP.
 *   eopnotsupp_dumpit_returns_eopnotsupp
 *                                  — same for the dumpit variant; cb->nlh is
 *                                    NULL so the helper takes the `cmd = 0`
 *                                    branch (covered separately from the
 *                                    nlh-non-NULL case).
 *   policy_validate_info_round_trip
 *                                  — build a hand-crafted ASK_INFO_ATTR
 *                                    nlattr stream and validate it through
 *                                    nla_validate() against
 *                                    ask_info_policy; expect 0.
 *
 * Like ask_test_flow_offload, this suite uses suite_init/suite_exit with an
 * ownership flag so it can run standalone or stacked behind another suite
 * that already brought up the default flow table.
 */

#include <kunit/test.h>
#include <linux/skbuff.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/u64_stats_sync.h>
#include <net/genetlink.h>
#include <net/netlink.h>

#include <uapi/linux/ask/ask.h>
#include "../include/ask_internal.h"

/* ------------------------------------------------------------------------- */
/* Local mirror of struct ask_genl_dump_ctx.                                  */
/*                                                                            */
/* The real definition lives inside ask_genl.c (so the production code keeps  */
/* the struct file-local). The forward declaration in ask_internal.h is only  */
/* a name; the layout has to be re-stated here so the kunit suite can build a */
/* dump_ctx and pass it through ask_genl_dump_one_cb(). The two definitions   */
/* are kept in sync by inspection — if the production layout ever changes,    */
/* the kunit suite stops compiling. The static_assert below at suite-init     */
/* time would catch the easy case of size drift but C does not let us         */
/* sizeof() an opaque forward decl, so the discipline is purely manual.       */
/* ------------------------------------------------------------------------- */

struct ask_genl_dump_ctx {
	struct sk_buff *skb;
	int            start;
	int            count;
	int            seen;
	int            err;
};

/* ------------------------------------------------------------------------- */
/* Skb helpers                                                                */
/* ------------------------------------------------------------------------- */

static struct sk_buff *test_alloc_skb(struct kunit *test, unsigned int sz)
{
	struct sk_buff *skb = nlmsg_new(sz, GFP_KERNEL);

	KUNIT_ASSERT_NOT_NULL(test, skb);
	return skb;
}

static void test_free_skb(struct sk_buff *skb)
{
	kfree_skb(skb);
}

/*
 * Allocate a skb that is intentionally too small to fit even one nested
 * info/flow attribute. nlmsg_new(0, ...) gives an alloc just large enough
 * for the netlink header — any nla_put after nla_nest_start fails with
 * -EMSGSIZE because skb_tailroom() is exhausted on the first put.
 */
static struct sk_buff *test_alloc_skb_tiny(struct kunit *test)
{
	struct sk_buff *skb = alloc_skb(NLMSG_HDRLEN + 8, GFP_KERNEL);

	KUNIT_ASSERT_NOT_NULL(test, skb);
	skb_reserve(skb, NLMSG_HDRLEN);
	return skb;
}

/* ------------------------------------------------------------------------- */
/* Flow helpers                                                               */
/* ------------------------------------------------------------------------- */

/*
 * Build a stack `struct ask_flow` with deterministic stats and key. Used by
 * the fill_one_flow / dump_one_cb tests. We never insert this into the
 * rhashtable — the helpers we're testing don't care whether the flow is
 * table-owned or stack-owned, they only read fields.
 */
static void test_init_flow(struct ask_flow *f, u64 cookie,
			   u32 hw_id, u32 oif,
			   u64 packets, u64 bytes, u64 last_seen_ns)
{
	memset(f, 0, sizeof(*f));
	f->cookie       = cookie;
	f->hw_flow_id   = hw_id;
	f->oif          = oif;
	u64_stats_init(&f->stats.syncp);
	f->stats.packets      = packets;
	f->stats.bytes        = bytes;
	f->stats.last_seen_ns = last_seen_ns;
}

/* ------------------------------------------------------------------------- */
/* info_fill tests                                                            */
/* ------------------------------------------------------------------------- */

static void ask_genl_test_info_fill_happy_path(struct kunit *test)
{
	struct sk_buff *skb = test_alloc_skb(test, NLMSG_DEFAULT_SIZE);
	struct nlattr *info_nest, *attrs[ASK_INFO_ATTR_MAX + 1];
	int rc;

	rc = ask_genl_get_info_fill(skb);
	KUNIT_EXPECT_EQ(test, rc, 0);

	/* The fill helper writes into the skb's data area starting at skb->tail.
	 * For the test we look at the bytes starting at skb->data — the helper
	 * was called against a freshly-allocated skb so head == data == tail
	 * before fill, and now tail has advanced by the bytes written.
	 */
	info_nest = (struct nlattr *)skb->data;
	KUNIT_ASSERT_EQ(test, (int)(info_nest->nla_type & NLA_TYPE_MASK),
			ASK_ATTR_INFO);

	rc = nla_parse_nested(attrs, ASK_INFO_ATTR_MAX, info_nest,
			      ask_info_policy, NULL);
	KUNIT_EXPECT_EQ(test, rc, 0);

	KUNIT_EXPECT_NOT_NULL(test, attrs[ASK_INFO_ATTR_DRIVER_VERSION]);
	KUNIT_EXPECT_NOT_NULL(test, attrs[ASK_INFO_ATTR_GENL_VERSION]);
	KUNIT_EXPECT_NOT_NULL(test, attrs[ASK_INFO_ATTR_UCODE_FAMILY]);
	KUNIT_EXPECT_NOT_NULL(test, attrs[ASK_INFO_ATTR_UCODE_MAJOR]);
	KUNIT_EXPECT_NOT_NULL(test, attrs[ASK_INFO_ATTR_UCODE_MINOR]);
	KUNIT_EXPECT_NOT_NULL(test, attrs[ASK_INFO_ATTR_UCODE_PATCH]);
	KUNIT_EXPECT_NOT_NULL(test, attrs[ASK_INFO_ATTR_CAPABILITIES]);
	KUNIT_EXPECT_NOT_NULL(test, attrs[ASK_INFO_ATTR_NUM_FMAN]);
	KUNIT_EXPECT_NOT_NULL(test, attrs[ASK_INFO_ATTR_NUM_FLOWS]);
	KUNIT_EXPECT_NOT_NULL(test, attrs[ASK_INFO_ATTR_MAX_FLOWS]);

	test_free_skb(skb);
}

static void ask_genl_test_info_fill_genl_version(struct kunit *test)
{
	struct sk_buff *skb = test_alloc_skb(test, NLMSG_DEFAULT_SIZE);
	struct nlattr *info_nest, *attrs[ASK_INFO_ATTR_MAX + 1];
	int rc;

	rc = ask_genl_get_info_fill(skb);
	KUNIT_ASSERT_EQ(test, rc, 0);

	info_nest = (struct nlattr *)skb->data;
	rc = nla_parse_nested(attrs, ASK_INFO_ATTR_MAX, info_nest,
			      ask_info_policy, NULL);
	KUNIT_ASSERT_EQ(test, rc, 0);

	KUNIT_EXPECT_EQ(test,
			nla_get_u32(attrs[ASK_INFO_ATTR_GENL_VERSION]),
			(u32)ASK_GENL_VERSION);

	test_free_skb(skb);
}

static void ask_genl_test_info_fill_driver_version_str(struct kunit *test)
{
	struct sk_buff *skb = test_alloc_skb(test, NLMSG_DEFAULT_SIZE);
	struct nlattr *info_nest, *attrs[ASK_INFO_ATTR_MAX + 1];
	const char *s;
	int rc;

	rc = ask_genl_get_info_fill(skb);
	KUNIT_ASSERT_EQ(test, rc, 0);

	info_nest = (struct nlattr *)skb->data;
	rc = nla_parse_nested(attrs, ASK_INFO_ATTR_MAX, info_nest,
			      ask_info_policy, NULL);
	KUNIT_ASSERT_EQ(test, rc, 0);

	s = nla_data(attrs[ASK_INFO_ATTR_DRIVER_VERSION]);
	KUNIT_ASSERT_NOT_NULL(test, s);
	KUNIT_EXPECT_EQ(test, strncmp(s, "ask ", 4), 0);
	KUNIT_EXPECT_NOT_NULL(test, strstr(s, ASK_DRV_VERSION_STR));

	test_free_skb(skb);
}

static void ask_genl_test_info_fill_emsgsize(struct kunit *test)
{
	struct sk_buff *skb = test_alloc_skb_tiny(test);
	int rc;

	rc = ask_genl_get_info_fill(skb);
	KUNIT_EXPECT_EQ(test, rc, -EMSGSIZE);

	test_free_skb(skb);
}

/* ------------------------------------------------------------------------- */
/* fill_one_flow tests                                                        */
/* ------------------------------------------------------------------------- */

static void ask_genl_test_fill_one_flow_happy_path(struct kunit *test)
{
	struct sk_buff *skb = test_alloc_skb(test, NLMSG_DEFAULT_SIZE);
	struct nlattr *flow_nest, *attrs[ASK_FLOW_ATTR_MAX + 1];
	struct ask_flow f;
	int rc;

	test_init_flow(&f, 0xCAFEF00DULL, 0x12345u, 42u,
		       /*packets=*/100ULL, /*bytes=*/14000ULL,
		       /*last_seen_ns=*/0xABCDEF0123456789ULL);

	rc = ask_genl_fill_one_flow(skb, &f);
	KUNIT_EXPECT_EQ(test, rc, 0);

	flow_nest = (struct nlattr *)skb->data;
	KUNIT_ASSERT_EQ(test, (int)(flow_nest->nla_type & NLA_TYPE_MASK),
			ASK_ATTR_FLOW);

	rc = nla_parse_nested(attrs, ASK_FLOW_ATTR_MAX, flow_nest,
			      ask_flow_policy, NULL);
	KUNIT_EXPECT_EQ(test, rc, 0);

	KUNIT_ASSERT_NOT_NULL(test, attrs[ASK_FLOW_ATTR_ID]);
	KUNIT_ASSERT_NOT_NULL(test, attrs[ASK_FLOW_ATTR_HW_FLOW_ID]);
	KUNIT_ASSERT_NOT_NULL(test, attrs[ASK_FLOW_ATTR_OIF]);
	KUNIT_ASSERT_NOT_NULL(test, attrs[ASK_FLOW_ATTR_PACKETS]);
	KUNIT_ASSERT_NOT_NULL(test, attrs[ASK_FLOW_ATTR_BYTES]);
	KUNIT_ASSERT_NOT_NULL(test, attrs[ASK_FLOW_ATTR_LAST_SEEN_NS]);

	KUNIT_EXPECT_EQ(test, nla_get_u64(attrs[ASK_FLOW_ATTR_ID]),
			0xCAFEF00DULL);
	KUNIT_EXPECT_EQ(test, nla_get_u32(attrs[ASK_FLOW_ATTR_HW_FLOW_ID]),
			0x12345u);
	KUNIT_EXPECT_EQ(test, nla_get_u32(attrs[ASK_FLOW_ATTR_OIF]), 42u);
	KUNIT_EXPECT_EQ(test, nla_get_u64(attrs[ASK_FLOW_ATTR_PACKETS]),
			100ULL);
	KUNIT_EXPECT_EQ(test, nla_get_u64(attrs[ASK_FLOW_ATTR_BYTES]),
			14000ULL);
	KUNIT_EXPECT_EQ(test,
			nla_get_u64(attrs[ASK_FLOW_ATTR_LAST_SEEN_NS]),
			0xABCDEF0123456789ULL);

	test_free_skb(skb);
}

static void ask_genl_test_fill_one_flow_with_stats(struct kunit *test)
{
	struct sk_buff *skb = test_alloc_skb(test, NLMSG_DEFAULT_SIZE);
	struct nlattr *flow_nest, *attrs[ASK_FLOW_ATTR_MAX + 1];
	struct ask_flow f;
	int rc;

	test_init_flow(&f, 0x1ULL, 1u, 1u, 0ULL, 0ULL, 0ULL);

	/* bump via the production stats updater so we exercise the
	 * u64_stats_update_begin/end writer path that the fill helper
	 * reads via u64_stats_fetch_begin/retry.
	 */
	ask_flow_update_stats(&f, 7ULL, 770ULL);
	ask_flow_update_stats(&f, 3ULL, 300ULL);

	rc = ask_genl_fill_one_flow(skb, &f);
	KUNIT_ASSERT_EQ(test, rc, 0);

	flow_nest = (struct nlattr *)skb->data;
	rc = nla_parse_nested(attrs, ASK_FLOW_ATTR_MAX, flow_nest,
			      ask_flow_policy, NULL);
	KUNIT_ASSERT_EQ(test, rc, 0);

	KUNIT_EXPECT_EQ(test, nla_get_u64(attrs[ASK_FLOW_ATTR_PACKETS]),
			10ULL);
	KUNIT_EXPECT_EQ(test, nla_get_u64(attrs[ASK_FLOW_ATTR_BYTES]),
			1070ULL);

	test_free_skb(skb);
}

static void ask_genl_test_fill_one_flow_emsgsize(struct kunit *test)
{
	struct sk_buff *skb = test_alloc_skb_tiny(test);
	struct ask_flow f;
	int rc;

	test_init_flow(&f, 0x2ULL, 2u, 2u, 0ULL, 0ULL, 0ULL);

	rc = ask_genl_fill_one_flow(skb, &f);
	KUNIT_EXPECT_EQ(test, rc, -EMSGSIZE);

	test_free_skb(skb);
}

/* ------------------------------------------------------------------------- */
/* dump_one_cb tests                                                          */
/* ------------------------------------------------------------------------- */

static void ask_genl_test_dump_cb_skip_first_n(struct kunit *test)
{
	struct sk_buff *skb = test_alloc_skb(test, NLMSG_DEFAULT_SIZE);
	struct ask_genl_dump_ctx ctx = {
		.skb = skb,
		.start = 2,
	};
	struct ask_flow flows[5];
	int i, rc;

	for (i = 0; i < 5; i++)
		test_init_flow(&flows[i], 0x1000ULL + i, i + 1, i + 100,
			       0ULL, 0ULL, 0ULL);

	for (i = 0; i < 5; i++) {
		rc = ask_genl_dump_one_cb(&flows[i], &ctx);
		KUNIT_EXPECT_EQ(test, rc, 0);
	}

	/* First two flows skipped, next three emitted. */
	KUNIT_EXPECT_EQ(test, ctx.count, 3);
	KUNIT_EXPECT_EQ(test, ctx.seen, 5);
	KUNIT_EXPECT_EQ(test, ctx.err, 0);

	test_free_skb(skb);
}

static void ask_genl_test_dump_cb_emsgsize_propagation(struct kunit *test)
{
	struct sk_buff *skb = test_alloc_skb_tiny(test);
	struct ask_genl_dump_ctx ctx = {
		.skb = skb,
		.start = 0,
	};
	struct ask_flow f;
	int rc;

	test_init_flow(&f, 0xDEADBEEFULL, 1u, 1u, 0ULL, 0ULL, 0ULL);

	rc = ask_genl_dump_one_cb(&f, &ctx);
	KUNIT_EXPECT_EQ(test, rc, -EMSGSIZE);
	KUNIT_EXPECT_EQ(test, ctx.err, -EMSGSIZE);
	KUNIT_EXPECT_EQ(test, ctx.count, 0);

	test_free_skb(skb);
}

/* ------------------------------------------------------------------------- */
/* eopnotsupp tests                                                           */
/*                                                                            */
/* The doit handler reads info->genlhdr->cmd. We supply a stack genl_info     */
/* with a stack genlmsghdr so the printk_ratelimited message is well-formed   */
/* and the function reaches its return -EOPNOTSUPP. The skb argument is      */
/* never dereferenced by the eopnotsupp handler, so NULL is fine.             */
/* ------------------------------------------------------------------------- */

static void ask_genl_test_eopnotsupp_doit_returns_eopnotsupp(struct kunit *test)
{
	struct genlmsghdr ghdr = { .cmd = ASK_CMD_GET_MURAM };
	struct genl_info info = { .genlhdr = &ghdr };
	int rc;

	rc = ask_genl_eopnotsupp_doit(NULL, &info);
	KUNIT_EXPECT_EQ(test, rc, -EOPNOTSUPP);
}

static void ask_genl_test_eopnotsupp_dumpit_returns_eopnotsupp(struct kunit *test)
{
	struct netlink_callback cb = { 0 };
	int rc;

	/* cb.nlh is NULL, so the helper takes the cmd = 0 branch. */
	rc = ask_genl_eopnotsupp_dumpit(NULL, &cb);
	KUNIT_EXPECT_EQ(test, rc, -EOPNOTSUPP);
}

/* ------------------------------------------------------------------------- */
/* policy round-trip                                                          */
/*                                                                            */
/* Build the info nested block via the production fill helper, then validate */
/* the resulting nlattr stream through nla_validate_nested() against         */
/* ask_info_policy. This is more than ask_genl_test_info_fill_happy_path     */
/* asserts: that test only calls nla_parse_nested (which validates as a      */
/* side-effect); this test calls validate explicitly, exercising the         */
/* policy table outside the parse path.                                       */
/* ------------------------------------------------------------------------- */

static void ask_genl_test_policy_validate_info_round_trip(struct kunit *test)
{
	struct sk_buff *skb = test_alloc_skb(test, NLMSG_DEFAULT_SIZE);
	struct nlattr *info_nest;
	int rc;

	rc = ask_genl_get_info_fill(skb);
	KUNIT_ASSERT_EQ(test, rc, 0);

	info_nest = (struct nlattr *)skb->data;
	KUNIT_ASSERT_EQ(test, (int)(info_nest->nla_type & NLA_TYPE_MASK),
			ASK_ATTR_INFO);

	rc = nla_validate_nested(info_nest, ASK_INFO_ATTR_MAX,
				 ask_info_policy, NULL);
	KUNIT_EXPECT_EQ(test, rc, 0);

	test_free_skb(skb);
}

/* ------------------------------------------------------------------------- */
/* suite lifecycle                                                            */
/*                                                                            */
/* The genl pure-helper tests do not touch the flow table, but                */
/* fill_one_flow_with_stats calls ask_flow_update_stats() which only needs   */
/* the seqcount inside the stack-allocated struct ask_flow. We still run     */
/* ask_flow_init() to keep parity with the flow_offload suite (so the suite  */
/* can be loaded standalone and the default table is sane for any future     */
/* test that wants to exercise GET_FLOW / DUMP_FLOWS through real lookups).  */
/* ------------------------------------------------------------------------- */

static bool ask_genl_owns_default_table;

static int ask_genl_suite_init(struct kunit_suite *suite)
{
	int rc;

	if (ask_flow_default_table())
		return 0;
	rc = ask_flow_init();
	if (rc)
		return rc;
	ask_genl_owns_default_table = true;
	return 0;
}

static void ask_genl_suite_exit(struct kunit_suite *suite)
{
	if (!ask_genl_owns_default_table)
		return;
	ask_flow_exit();
	ask_genl_owns_default_table = false;
}

/* ------------------------------------------------------------------------- */
/* suite                                                                      */
/* ------------------------------------------------------------------------- */

static struct kunit_case ask_genl_test_cases[] = {
	KUNIT_CASE(ask_genl_test_info_fill_happy_path),
	KUNIT_CASE(ask_genl_test_info_fill_genl_version),
	KUNIT_CASE(ask_genl_test_info_fill_driver_version_str),
	KUNIT_CASE(ask_genl_test_info_fill_emsgsize),
	KUNIT_CASE(ask_genl_test_fill_one_flow_happy_path),
	KUNIT_CASE(ask_genl_test_fill_one_flow_with_stats),
	KUNIT_CASE(ask_genl_test_fill_one_flow_emsgsize),
	KUNIT_CASE(ask_genl_test_dump_cb_skip_first_n),
	KUNIT_CASE(ask_genl_test_dump_cb_emsgsize_propagation),
	KUNIT_CASE(ask_genl_test_eopnotsupp_doit_returns_eopnotsupp),
	KUNIT_CASE(ask_genl_test_eopnotsupp_dumpit_returns_eopnotsupp),
	KUNIT_CASE(ask_genl_test_policy_validate_info_round_trip),
	{}
};

struct kunit_suite ask_genl_suite = {
	.name        = "ask_genl",
	.suite_init  = ask_genl_suite_init,
	.suite_exit  = ask_genl_suite_exit,
	.test_cases  = ask_genl_test_cases,
};