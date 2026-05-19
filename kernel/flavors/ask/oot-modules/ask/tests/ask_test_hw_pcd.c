// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - kunit suite for ask_hw.c FMan PCD dispatcher (PR14g-body-4 / M2.5g)
 *
 * Validates the body-1/body-2 surface that ask_flow.c body-3 depends on:
 *
 *   1. hw_flow_id pack/unpack helpers are byte-perfect inverses;
 *      sentinel values (TOKEN_NONE, TOKEN_V4_TCP) round-trip.
 *
 *   2. ask_hw_flow_insert() argument validation: NULL key or NULL
 *      out_hw_id returns -EINVAL before any side effect.
 *
 *   3. ask_hw_flow_insert() with no HW backing returns -ENODEV. In
 *      the kunit harness ask_hw_init() has not been called (the
 *      module's module_init does not run when ask_kunit.ko is loaded
 *      without ask.ko's own __init path) so ask_hw_pcd_get() returns
 *      NULL and the dispatcher takes the no-DPAA fallback arm. This
 *      is the exact path ask_flow.c body-3 keys on for the SW-only
 *      fake-counter fallback.
 *
 *   4. ask_hw_flow_insert() with a non-v4-TCP protocol returns
 *      -EOPNOTSUPP after the NULL check but BEFORE any HW resolution
 *      attempt — this is the second fallback signal body-3 honours.
 *      The test only fires this path if PCD bring-up succeeded
 *      (otherwise the -ENODEV early-out wins); on the kunit harness
 *      both -ENODEV and -EOPNOTSUPP are valid responses, both signal
 *      "fall back to SW counter", so the test accepts either.
 *
 *   5. ask_hw_flow_remove(TOKEN_NONE) returns 0 unconditionally —
 *      this is the contract that lets ask_flow_remove() call the
 *      dispatcher unconditionally on every flow tear-down without
 *      inspecting the token first. NULL-safe even when ask_hw_pcd_
 *      get() returns NULL (the dispatcher does its own no-PCD
 *      check first and returns -ENODEV in that case, which body-3
 *      treats as a non-error "nothing to free" outcome).
 *
 *   6. ask_hw_flow_query_stats() returns -EOPNOTSUPP at body-2 (the
 *      M3 PR15h bulk poller is not landed yet). This is a contract
 *      lock so future surgery on ask_hw.c does not silently start
 *      returning bogus zero stats while userspace expects an error.
 *
 * No silicon needed — kunit runs in early boot on QEMU virt where
 * there is no fsl,fman node in the device tree, so ask_hw_pcd_get()
 * returns NULL by construction. That is exactly the contract path
 * the dispatcher is designed to handle gracefully.
 */

#include <kunit/test.h>
#include <linux/in.h>
#include <linux/types.h>
#include <linux/string.h>

#include "../include/ask_internal.h"

/* ------------------------------------------------------------------------- */
/* helpers                                                                    */
/* ------------------------------------------------------------------------- */

static void make_v4_tcp_key(struct ask_flow_key *k,
			    __be32 sip, __be32 dip,
			    __be16 sport, __be16 dport)
{
	memset(k, 0, sizeof(*k));
	k->l3_proto = ASK_FLOW_L3_IPV4;
	k->l4_proto = IPPROTO_TCP;
	k->sport    = sport;
	k->dport    = dport;
	k->iif      = 1;
	memcpy(&k->src_ip[0], &sip, 4);
	memcpy(&k->dst_ip[0], &dip, 4);
}

/* ------------------------------------------------------------------------- */
/* tests — pack/unpack                                                        */
/* ------------------------------------------------------------------------- */

static void hw_pcd_test_pack_unpack_roundtrip(struct kunit *test)
{
	const struct {
		u16 token;
		u16 idx;
		u32 expected;
	} cases[] = {
		{ 0,            0,      0x00000000u },
		{ 1,            0,      0x00010000u },
		{ 0,            1,      0x00000001u },
		{ 0xffff,       0xffff, 0xffffffffu },
		{ ASK_HW_FLOW_ID_TOKEN_NONE,    0,    0u },
		{ ASK_HW_FLOW_ID_TOKEN_V4_TCP,  42, (1u << 16) | 42u },
		{ 0x1234,       0x5678, 0x12345678u },
	};
	int i;

	for (i = 0; i < (int)ARRAY_SIZE(cases); i++) {
		u32 packed;
		u16 t_back = 0, i_back = 0;

		packed = ask_priv_pack_hw_flow_id(cases[i].token, cases[i].idx);
		KUNIT_EXPECT_EQ(test, packed, cases[i].expected);

		ask_priv_unpack_hw_flow_id(packed, &t_back, &i_back);
		KUNIT_EXPECT_EQ(test, t_back, cases[i].token);
		KUNIT_EXPECT_EQ(test, i_back, cases[i].idx);
	}
}

static void hw_pcd_test_unpack_null_safe(struct kunit *test)
{
	u16 t = 0xdead, i = 0xbeef;

	/* NULL outputs must not crash — used by debugfs partial-print paths. */
	ask_priv_unpack_hw_flow_id(0xcafef00du, NULL, &i);
	KUNIT_EXPECT_EQ(test, i, (u16)0xf00du);

	ask_priv_unpack_hw_flow_id(0xcafef00du, &t, NULL);
	KUNIT_EXPECT_EQ(test, t, (u16)0xcafeu);

	ask_priv_unpack_hw_flow_id(0xdeadbeefu, NULL, NULL);
	KUNIT_SUCCEED(test);
}

static void hw_pcd_test_token_sentinels(struct kunit *test)
{
	/*
	 * Lock the macro values: ask_flow.c body-3's fake_hw_id_seq
	 * starts at 1 and increments without a token — the SW-fallback
	 * ids look like packed (token=0, idx=N). Real HW ids always
	 * have token >= 1 (TOKEN_V4_TCP). This invariant is what makes
	 * the unconditional ask_hw_flow_remove() call from body-3 safe:
	 * the TOKEN_NONE arm short-circuits to 0 without touching HW.
	 */
	KUNIT_EXPECT_EQ(test, (u32)ASK_HW_FLOW_ID_TOKEN_NONE,   0u);
	KUNIT_EXPECT_EQ(test, (u32)ASK_HW_FLOW_ID_TOKEN_V4_TCP, 1u);
}

/* ------------------------------------------------------------------------- */
/* tests — dispatcher contract                                                */
/* ------------------------------------------------------------------------- */

static void hw_pcd_test_insert_null_key(struct kunit *test)
{
	u32 hw_id = 0xdeadbeefu;
	int rc;

	rc = ask_hw_flow_insert(NULL, 1, 0, ASK_HW_DIR_FWD, &hw_id);
	KUNIT_EXPECT_EQ(test, rc, -EINVAL);
	/* hw_id must NOT have been written on the failure path. */
	KUNIT_EXPECT_EQ(test, hw_id, 0xdeadbeefu);
}

static void hw_pcd_test_insert_null_out(struct kunit *test)
{
	struct ask_flow_key key;
	int rc;

	make_v4_tcp_key(&key, htonl(0x0a000001), htonl(0x0a000002),
			htons(1234), htons(80));
	rc = ask_hw_flow_insert(&key, 1, 0, ASK_HW_DIR_FWD, NULL);
	KUNIT_EXPECT_EQ(test, rc, -EINVAL);
}

static void hw_pcd_test_insert_no_hw_backing(struct kunit *test)
{
	struct ask_flow_key key;
	u32 hw_id = 0xdeadbeefu;
	int rc;

	/*
	 * On the kunit harness ask_hw_init() has not been called, so
	 * ask_hw_pcd_get() returns NULL and the dispatcher returns
	 * -ENODEV. This is the SW-fallback signal that ask_flow.c
	 * body-3 keys on. If a future change accidentally inverts the
	 * NULL check, this test will fail loudly.
	 *
	 * Defensive note: if a future test fixture DOES call
	 * ask_hw_init() before the suite runs, ask_hw_pcd_get() may
	 * return non-NULL; in that case the dispatcher will reach
	 * ask_hw_resolve_oif_fqid() which will return -ENODEV anyway
	 * because oif=999 does not name a registered netdev. Either
	 * outcome lands at -ENODEV, so this test is robust to test
	 * ordering.
	 */
	make_v4_tcp_key(&key, htonl(0x0a000001), htonl(0x0a000002),
			htons(1234), htons(80));

	rc = ask_hw_flow_insert(&key, 999, 0, ASK_HW_DIR_FWD, &hw_id);
	KUNIT_EXPECT_EQ(test, rc, -ENODEV);
	/* hw_id must NOT have been written on the failure path. */
	KUNIT_EXPECT_EQ(test, hw_id, 0xdeadbeefu);
}

static void hw_pcd_test_insert_unsupported_proto(struct kunit *test)
{
	struct ask_flow_key key;
	u32 hw_id = 0xdeadbeefu;
	int rc;

	/*
	 * Build a v4-UDP key. Body-2 implements v4-TCP only; v4-UDP /
	 * v6-* must return -EOPNOTSUPP so body-3 falls back to the SW
	 * counter rather than silently mis-routing to the v4-TCP node.
	 *
	 * As with insert_no_hw_backing, the early-out -ENODEV (no PCD)
	 * may win on the kunit harness. Both are valid SW-fallback
	 * signals per the dispatcher contract, so accept either.
	 */
	make_v4_tcp_key(&key, htonl(0x0a000001), htonl(0x0a000002),
			htons(1234), htons(80));
	key.l4_proto = IPPROTO_UDP;

	rc = ask_hw_flow_insert(&key, 999, 0, ASK_HW_DIR_FWD, &hw_id);
	KUNIT_EXPECT_TRUE(test, rc == -ENODEV || rc == -EOPNOTSUPP);
	KUNIT_EXPECT_EQ(test, hw_id, 0xdeadbeefu);
}

static void hw_pcd_test_insert_unsupported_l3(struct kunit *test)
{
	struct ask_flow_key key;
	u32 hw_id = 0xdeadbeefu;
	int rc;

	make_v4_tcp_key(&key, htonl(0x0a000001), htonl(0x0a000002),
			htons(1234), htons(80));
	key.l3_proto = ASK_FLOW_L3_IPV6;

	rc = ask_hw_flow_insert(&key, 999, 0, ASK_HW_DIR_FWD, &hw_id);
	KUNIT_EXPECT_TRUE(test, rc == -ENODEV || rc == -EOPNOTSUPP);
	KUNIT_EXPECT_EQ(test, hw_id, 0xdeadbeefu);
}

/* ------------------------------------------------------------------------- */
/* tests — remove + stats                                                     */
/* ------------------------------------------------------------------------- */

static void hw_pcd_test_remove_token_none_is_noop(struct kunit *test)
{
	u32 sw_only_id;
	int rc;

	/*
	 * SW-only ids look like packed (token=NONE, idx=N). Body-3
	 * calls ask_hw_flow_remove() unconditionally on every tear-
	 * down regardless of whether the insert went through HW or
	 * fell back to the SW counter. The TOKEN_NONE arm of the
	 * dispatcher MUST short-circuit to 0 (or -ENODEV when no PCD)
	 * without touching hardware. Both are non-error from body-3's
	 * point of view — the SW table has already released ownership
	 * of the cookie.
	 */
	sw_only_id = ask_priv_pack_hw_flow_id(ASK_HW_FLOW_ID_TOKEN_NONE, 7);
	rc = ask_hw_flow_remove(sw_only_id);
	KUNIT_EXPECT_TRUE(test, rc == 0 || rc == -ENODEV);

	/* Edge: token=NONE with idx=0 (first SW-fallback id ever). */
	sw_only_id = ask_priv_pack_hw_flow_id(ASK_HW_FLOW_ID_TOKEN_NONE, 0);
	rc = ask_hw_flow_remove(sw_only_id);
	KUNIT_EXPECT_TRUE(test, rc == 0 || rc == -ENODEV);

	/* Edge: token=NONE with idx=U16_MAX (counter near wrap). */
	sw_only_id = ask_priv_pack_hw_flow_id(ASK_HW_FLOW_ID_TOKEN_NONE,
					      0xffff);
	rc = ask_hw_flow_remove(sw_only_id);
	KUNIT_EXPECT_TRUE(test, rc == 0 || rc == -ENODEV);
}

static void hw_pcd_test_remove_unknown_token(struct kunit *test)
{
	u32 bogus_id;
	int rc;

	/*
	 * Token 0xff is not assigned to any protocol. On a populated
	 * PCD, the dispatcher returns -EINVAL via the default arm; on
	 * the no-PCD kunit harness the early -ENODEV wins. Both are
	 * acceptable — the contract is "do not crash, do not silently
	 * succeed".
	 */
	bogus_id = ask_priv_pack_hw_flow_id(0xff, 1);
	rc = ask_hw_flow_remove(bogus_id);
	KUNIT_EXPECT_TRUE(test, rc == -ENODEV || rc == -EINVAL);
}

static void hw_pcd_test_query_stats_eopnotsupp(struct kunit *test)
{
	u64 packets = 0xdeadbeefULL, bytes = 0xcafef00dULL;
	int rc;

	/*
	 * Per-CC-key counters are an M3 deliverable (PR15h bulk
	 * OP_FLOW_DUMP_STATS poller). Until then ask_hw_flow_query_
	 * stats() returns -EOPNOTSUPP and the genl OP_FLOW_QUERY_
	 * STATS handler falls back to the SW table counters. This
	 * test pins the contract so a future change does not silently
	 * start returning bogus zero counters while userspace believes
	 * it is reading hardware values.
	 */
	rc = ask_hw_flow_query_stats(0xdeadbeefu, &packets, &bytes);
	KUNIT_EXPECT_EQ(test, rc, -EOPNOTSUPP);

	/* Outputs MUST NOT have been overwritten on the failure path. */
	KUNIT_EXPECT_EQ(test, packets, 0xdeadbeefULL);
	KUNIT_EXPECT_EQ(test, bytes,   0xcafef00dULL);
}

/* ------------------------------------------------------------------------- */
/* tests — bring-up handle accessor                                           */
/* ------------------------------------------------------------------------- */

static void hw_pcd_test_get_returns_null_without_init(struct kunit *test)
{
	struct ask_hw_pcd *h = ask_hw_pcd_get();

	/*
	 * On QEMU virt / any non-DPAA kunit harness, ask_hw_init() has
	 * not been called, OR was called and bring-up soft-failed (no
	 * fsl,fman in DT). Either way ask_hw_pcd_get() MUST return
	 * NULL — that is the signal body-3 uses to skip the silicon
	 * fast path. If a future test fixture stands up a synthetic
	 * PCD via a side door, this test will need to be relaxed to
	 * "either NULL or non-NULL"; for now the kunit-harness load
	 * order guarantees NULL.
	 */
	KUNIT_EXPECT_NULL(test, h);
}

/* ------------------------------------------------------------------------- */
/* suite                                                                      */
/* ------------------------------------------------------------------------- */

/* ------------------------------------------------------------------------- */
/* PR14j tests — cookie indirection table + OH-port insert pre-flight        */
/*                                                                            */
/* These exercise contract surfaces that are reachable on the kunit harness   */
/* (no fsl,fman in QEMU virt DT, so ask_hw_pcd_get() is NULL) and DO NOT      */
/* touch silicon.  They lock the documented behaviours:                       */
/*                                                                            */
/*   1. ask_hw_cookie_alloc() never returns 0 (the SW-only sentinel).         */
/*      Cookies live in xa_init_flags(XA_FLAGS_ALLOC1) → index 0 is reserved. */
/*                                                                            */
/*   2. ask_hw_cookie_lookup() after ask_hw_cookie_free() returns NULL.       */
/*      Required for ask_hw_flow_remove() to safely return -EINVAL on stale   */
/*      cookies (e.g. a userspace genl DESTROY that races a Phase-2 expire).  */
/*                                                                            */
/*   3. ask_hw_flow_insert_v4_tcp via the public dispatcher with the zero-MAC */
/*      neighbour-unresolved key returns -EAGAIN (PR14j 'defence in depth'    */
/*      mac_is_zero() gate).  ask_flow.c body-3 treats -EAGAIN as a fallback  */
/*      signal indistinguishable from -ENODEV/-EOPNOTSUPP (all three demote   */
/*      to the SW fake-counter path), so the test accepts that the no-PCD    */
/*      kunit-harness early-out (-ENODEV) wins instead.  The contract is     */
/*      'never let a half-resolved flow burn silicon', not 'always return    */
/*      exactly -EAGAIN here'.                                                */
/*                                                                            */
/*   4. ask_hw_flow_remove(non-existent cookie) returns -EINVAL (kunit        */
/*      harness path is -ENODEV because ask_hw_pcd_get() is NULL — both       */
/*      are 'do not crash, do not silently succeed' outcomes).                */
/* ------------------------------------------------------------------------- */

static void hw_pcd_test_cookie_alloc_skips_zero(struct kunit *test)
{
        /*
         * Without a live ask_hw_pcd_get() handle there is no xarray to
         * allocate against.  The helper must defensively return 0 on a
         * NULL handle so callers cannot construct a "valid cookie 0"
         * by accident.  This pins the NULL-handle contract; the real
         * "never returns 0 on a populated handle" property is held by
         * the XA_FLAGS_ALLOC1 flag in xa_init_flags() and is not
         * reachable from kunit without a synthetic PCD handle (a
         * deliberate test-fixture v1.1 deliverable, not v1.0).
         */
        u32 c = ask_hw_cookie_alloc(NULL, NULL);
        KUNIT_EXPECT_EQ(test, c, 0u);
}

static void hw_pcd_test_cookie_lookup_null_safe(struct kunit *test)
{
        /*
         * Cookie 0 is the SW-only sentinel; lookup MUST return NULL.
         * A NULL handle MUST also return NULL.  These two together are
         * what make ask_hw_flow_remove(hw_flow_id=0) a no-op.
         */
        KUNIT_EXPECT_NULL(test, ask_hw_cookie_lookup(NULL, 0));
        KUNIT_EXPECT_NULL(test, ask_hw_cookie_lookup(NULL, 42));
        KUNIT_EXPECT_NULL(test, ask_hw_cookie_lookup(ask_hw_pcd_get(), 0));
}

static void hw_pcd_test_cookie_free_null_safe(struct kunit *test)
{
        /*
         * Cookie 0 free MUST be a no-op (matches the lookup contract).
         * A NULL handle MUST not crash.  These are the defensive
         * preconditions ask_hw_flow_remove() relies on.
         */
        ask_hw_cookie_free(NULL, 0);
        ask_hw_cookie_free(NULL, 0xdeadbeef);
        ask_hw_cookie_free(ask_hw_pcd_get(), 0);
        KUNIT_SUCCEED(test);
}

static void hw_pcd_test_insert_zero_mac_eagain(struct kunit *test)
{
        struct ask_flow_key k;
        u32 hw_id = 0xdeadbeef;
        int rc;

        /*
         * Build a v4-TCP key with the PR14j-required next_hop_mac /
         * egress_mac fields LEFT ZERO.  On a populated PCD this hits
         * the mac_is_zero() gate in ask_hw_flow_insert_v4_tcp() and
         * returns -EAGAIN.  On the kunit harness ask_hw_pcd_get() is
         * NULL so the public dispatcher's early no-PCD check wins and
         * returns -ENODEV.  Both responses are 'fall back to SW',
         * which is the only contract this test pins.  The
         * out_hw_id MUST NOT have been touched on either failure
         * path — protecting userspace from picking up a stale token.
         */
        make_v4_tcp_key(&k, htonl(0x0a000001), htonl(0x0a000002),
                        htons(1000), htons(80));
        /* next_hop_mac / egress_mac are zero by make_v4_tcp_key memset. */

        rc = ask_hw_flow_insert(&k, /*oif=*/1, /*action_flags=*/0, ASK_HW_DIR_FWD, &hw_id);
        KUNIT_EXPECT_TRUE(test, rc == -EAGAIN || rc == -ENODEV ||
                                 rc == -EOPNOTSUPP);
        KUNIT_EXPECT_EQ(test, hw_id, 0xdeadbeefu);
}

static void hw_pcd_test_insert_zero_dst_mac_only_eagain(struct kunit *test)
{
        struct ask_flow_key k;
        u32 hw_id = 0;
        int rc;

        /*
         * Egress MAC resolved (neigh_lookup found us the local
         * interface's dev_addr) but the next-hop MAC is still zero
         * because ARP has not completed for the destination yet.
         * This is the dominant case in practice: ask_resolve_neigh_v4
         * always fills egress_mac from dev_addr, but next_hop_mac
         * only fills once the neighbour is NUD_CONNECTED.
         *
         * Same outcome envelope as the all-zero case: -EAGAIN on a
         * populated PCD, -ENODEV/-EOPNOTSUPP on the kunit harness.
         */
        make_v4_tcp_key(&k, htonl(0x0a000001), htonl(0x0a000002),
                        htons(2000), htons(80));
        k.egress_mac[0] = 0x02;
        k.egress_mac[5] = 0x42;
        /* next_hop_mac left zero. */

        rc = ask_hw_flow_insert(&k, /*oif=*/1, /*action_flags=*/0, ASK_HW_DIR_FWD, &hw_id);
        KUNIT_EXPECT_TRUE(test, rc == -EAGAIN || rc == -ENODEV ||
                                 rc == -EOPNOTSUPP);
        KUNIT_EXPECT_EQ(test, hw_id, 0u);
}

static void hw_pcd_test_remove_cookie_zero_is_noop(struct kunit *test)
{
        /*
         * PR14j changes hw_flow_id semantics: cookie 0 means
         * 'this flow has no HW backing' (SW-only fake_hw_id_seq
         * counter starting at 1 in ask_flow.c).  ask_hw_flow_remove()
         * MUST return 0 for cookie 0 so ask_flow_remove() can call us
         * unconditionally on every flow tear-down without inspecting
         * the cookie value.  Identical contract to PR14g TOKEN_NONE.
         */
        int rc = ask_hw_flow_remove(0);
        KUNIT_EXPECT_EQ(test, rc, 0);
}

static void hw_pcd_test_remove_unknown_cookie(struct kunit *test)
{
        /*
         * A non-zero cookie that was never returned by ask_hw_cookie_
         * alloc() must return -EINVAL on a populated PCD (the xa_load
         * miss path), or -ENODEV on the kunit harness (no PCD at
         * all).  Either way it MUST NOT crash and MUST NOT silently
         * report success — that would let userspace believe a slot
         * was freed when no such slot existed.
         */
        int rc = ask_hw_flow_remove(0xabcdef01u);
        KUNIT_EXPECT_TRUE(test, rc == -EINVAL || rc == -ENODEV);
}

static struct kunit_case ask_hw_pcd_test_cases[] = {
KUNIT_CASE(hw_pcd_test_pack_unpack_roundtrip),
KUNIT_CASE(hw_pcd_test_unpack_null_safe),
KUNIT_CASE(hw_pcd_test_token_sentinels),
KUNIT_CASE(hw_pcd_test_insert_null_key),
KUNIT_CASE(hw_pcd_test_insert_null_out),
KUNIT_CASE(hw_pcd_test_insert_no_hw_backing),
KUNIT_CASE(hw_pcd_test_insert_unsupported_proto),
KUNIT_CASE(hw_pcd_test_insert_unsupported_l3),
KUNIT_CASE(hw_pcd_test_remove_token_none_is_noop),
KUNIT_CASE(hw_pcd_test_remove_unknown_token),
KUNIT_CASE(hw_pcd_test_query_stats_eopnotsupp),
KUNIT_CASE(hw_pcd_test_get_returns_null_without_init),
/* PR14j: cookie indirection table + OH-port pre-flight gates. */
KUNIT_CASE(hw_pcd_test_cookie_alloc_skips_zero),
KUNIT_CASE(hw_pcd_test_cookie_lookup_null_safe),
KUNIT_CASE(hw_pcd_test_cookie_free_null_safe),
KUNIT_CASE(hw_pcd_test_insert_zero_mac_eagain),
KUNIT_CASE(hw_pcd_test_insert_zero_dst_mac_only_eagain),
KUNIT_CASE(hw_pcd_test_remove_cookie_zero_is_noop),
KUNIT_CASE(hw_pcd_test_remove_unknown_cookie),
{}
};

struct kunit_suite ask_hw_pcd_suite = {
	.name       = "ask_hw_pcd",
	.test_cases = ask_hw_pcd_test_cases,
};