// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - kunit suite for ask_flow_offload.c (PR8 / M1.4)
 *
 * Drives the FLOW_CLS_REPLACE / DESTROY / STATS dispatcher in
 * ask_flow_offload_setup_tc_block_cb() directly, bypassing the
 * block-bind dance (which requires a real netdev with a setup_tc
 * ndo — that path arrives with the in-tree dpaa patch in PR11).
 *
 * Each test hand-builds a `struct flow_cls_offload` with a small
 * `struct flow_rule` containing exactly the dissector keys + action
 * entries the dispatcher needs to consume. The dissector key bitmap
 * (`flow_dissector.used_keys`) is the source of truth for
 * `flow_rule_match_key()`, so we set it explicitly per case.
 *
 * Coverage shapes:
 *
 *   1. REPLACE happy path  — minimal v4/TCP key + REDIRECT action →
 *                             ask_flow_insert() round-trips, lookup
 *                             succeeds, hw_id non-zero.
 *   2. DESTROY round-trip   — REPLACE then DESTROY then lookup is
 *                             NULL.
 *   3. Double DESTROY       — DESTROY of an absent cookie returns 0
 *                             (ENOENT swallowed per spec).
 *   4. STATS round-trip     — REPLACE, bump stats via
 *                             ask_flow_update_stats(), STATS callback
 *                             fills flow_cls_offload->stats.
 *   5. REPLACE idempotent   — second REPLACE for same cookie returns
 *                             0 (EEXIST swallowed).
 *   6. Strict actions       — ETH MANGLE accepted (L2 rewrite is implemented);
 *                             NAT MANGLE/ADD and VLAN PUSH/POP rejected with
 *                             -EOPNOTSUPP and nothing inserted (T-M6-A2).
 *   7. Bad action           — unsupported action_id → -EOPNOTSUPP,
 *                             nothing inserted.
 *   8. Missing redirect     — REPLACE with no oif → -EOPNOTSUPP.
 *   9. IPv6 rejected        — n_proto=ETH_P_IPV6 → -EOPNOTSUPP.
 *
 * The suite uses ->suite_init / ->suite_exit to bring up and tear
 * down the default ask_flow_table — neither ask.ko's module_init nor
 * ask_flow_offload_init() runs in the kunit harness, but the
 * dispatcher needs a populated default table to insert into.
 *
 * Because every case shares the default table, each test cleans up
 * after itself by calling DESTROY on every cookie it inserted; the
 * suite_exit also flushes whatever may have leaked.
 */

#include <kunit/test.h>
#include <linux/etherdevice.h>
#include <linux/in.h>
#include <linux/ip.h>
#include <linux/list.h>
#include <linux/netdevice.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <net/flow_offload.h>
#include <net/pkt_cls.h>

#include "../include/ask_internal.h"

/* ------------------------------------------------------------------------- */
/* Test rule scratch                                                          */
/*                                                                            */
/* `struct flow_rule` is variable-sized (action.entries[] is a flex array)    */
/* and `struct flow_match` points into it via the dissector. We allocate one  */
/* scratch object per test and free it at the end.                            */
/*                                                                            */
/* The flow_dissector keys we populate (BASIC, IPV4_ADDRS, PORTS) live in     */
/* well-known offsets inside a packed `struct ask_test_match_data` we hand    */
/* the dispatcher; flow_dissector_init_keys() in upstream is overkill for     */
/* this purpose so we just set ->used_keys directly and place the structs at  */
/* hand-picked offsets.                                                       */
/* ------------------------------------------------------------------------- */

struct ask_test_match_data {
struct flow_dissector_key_basic basic;
struct flow_dissector_key_ipv4_addrs ipv4;
struct flow_dissector_key_ports ports;
};

struct ask_test_rule {
struct flow_rule *rule;
struct flow_dissector dissector;
struct ask_test_match_data key_data;
struct ask_test_match_data mask_data;
};

/*
 * Allocate a flow_rule with `nactions` action entries pre-zeroed.
 * Caller fills rule->action.entries[i] before invoking the dispatcher.
 */
static struct ask_test_rule *test_rule_alloc(struct kunit *test,
     unsigned int nactions)
{
struct ask_test_rule *r;

r = kunit_kzalloc(test, sizeof(*r), GFP_KERNEL);
KUNIT_ASSERT_NOT_NULL(test, r);

r->rule = kunit_kzalloc(test,
sizeof(struct flow_rule) +
nactions * sizeof(struct flow_action_entry),
GFP_KERNEL);
KUNIT_ASSERT_NOT_NULL(test, r->rule);
r->rule->action.num_entries = nactions;
r->rule->match.dissector = &r->dissector;
r->rule->match.key  = (void *)&r->key_data;
r->rule->match.mask = (void *)&r->mask_data;
return r;
}

/*
 * Wire the BASIC + IPV4_ADDRS + PORTS keys into the dissector so
 * flow_rule_match_key() finds them. flow_rule_match_basic() etc. read
 * key/mask at fixed offsets named by the dissector's offset table.
 */
static void test_rule_set_v4_tcp(struct ask_test_rule *r,
 __be32 src, __be32 dst,
 __be16 sport, __be16 dport)
{
struct flow_dissector *d = &r->dissector;

d->used_keys =
BIT_ULL(FLOW_DISSECTOR_KEY_BASIC) |
BIT_ULL(FLOW_DISSECTOR_KEY_IPV4_ADDRS) |
BIT_ULL(FLOW_DISSECTOR_KEY_PORTS);

d->offset[FLOW_DISSECTOR_KEY_BASIC] =
offsetof(struct ask_test_match_data, basic);
d->offset[FLOW_DISSECTOR_KEY_IPV4_ADDRS] =
offsetof(struct ask_test_match_data, ipv4);
d->offset[FLOW_DISSECTOR_KEY_PORTS] =
offsetof(struct ask_test_match_data, ports);

r->key_data.basic.n_proto = htons(ETH_P_IP);
r->key_data.basic.ip_proto = IPPROTO_TCP;
r->key_data.ipv4.src = src;
r->key_data.ipv4.dst = dst;
r->key_data.ports.src = sport;
r->key_data.ports.dst = dport;
}

/* IPv6 marker — only n_proto matters; the dispatcher rejects before
 * reading any v6 addresses, so we don't bother populating them.
 */
static void test_rule_set_v6_marker(struct ask_test_rule *r)
{
struct flow_dissector *d = &r->dissector;

d->used_keys = BIT_ULL(FLOW_DISSECTOR_KEY_BASIC);
d->offset[FLOW_DISSECTOR_KEY_BASIC] =
offsetof(struct ask_test_match_data, basic);
r->key_data.basic.n_proto = htons(ETH_P_IPV6);
r->key_data.basic.ip_proto = IPPROTO_TCP;
}

/*
 * The dispatcher reads `act->dev->ifindex`. We don't want the cost of
 * a real netdev allocation, so we hand a tiny stub net_device with
 * just the ifindex field set. The dispatcher never dereferences any
 * other ndo on the returned dev.
 */
static struct net_device *test_stub_netdev(struct kunit *test, int ifindex)
{
struct net_device *dev;

dev = kunit_kzalloc(test, sizeof(*dev), GFP_KERNEL);
KUNIT_ASSERT_NOT_NULL(test, dev);
dev->ifindex = ifindex;
return dev;
}

/* Build a FLOW_CLS_OFFLOAD with the requested command and rule. */
static struct flow_cls_offload *test_cls_alloc(struct kunit *test,
       enum flow_cls_command cmd,
       unsigned long cookie,
       struct ask_test_rule *r)
{
struct flow_cls_offload *f;

f = kunit_kzalloc(test, sizeof(*f), GFP_KERNEL);
KUNIT_ASSERT_NOT_NULL(test, f);
f->command = cmd;
f->cookie  = cookie;
f->rule    = r ? r->rule : NULL;
return f;
}

/* Convenience: drive the dispatcher and return its rc. */
static int dispatch(struct flow_cls_offload *f)
{
return ask_flow_offload_setup_tc_block_cb(TC_SETUP_CLSFLOWER, f, NULL);
}

/* Helper to remove a known cookie via the dispatcher (for cleanup). */
static void destroy_cookie(unsigned long cookie)
{
struct flow_cls_offload f = {
.command = FLOW_CLS_DESTROY,
.cookie  = cookie,
};

(void)ask_flow_offload_setup_tc_block_cb(TC_SETUP_CLSFLOWER,
 &f, NULL);
}

/* ------------------------------------------------------------------------- */
/* tests                                                                      */
/* ------------------------------------------------------------------------- */

static void ask_flow_offload_test_replace_minimal(struct kunit *test)
{
struct ask_test_rule *r = test_rule_alloc(test, 1);
struct net_device *oif = test_stub_netdev(test, 17);
struct flow_cls_offload *f;
struct ask_flow_table *t = ask_flow_default_table();
struct ask_flow *fl;
int rc;

KUNIT_ASSERT_NOT_NULL(test, t);

test_rule_set_v4_tcp(r, htonl(0x0a000001), htonl(0x0a000002),
     htons(1234), htons(80));
r->rule->action.entries[0].id = FLOW_ACTION_REDIRECT;
r->rule->action.entries[0].dev = oif;

f = test_cls_alloc(test, FLOW_CLS_REPLACE, 0xCAFE01, r);
rc = dispatch(f);
KUNIT_EXPECT_EQ(test, rc, 0);

fl = ask_flow_lookup(t, 0xCAFE01);
KUNIT_EXPECT_NOT_NULL(test, fl);
if (fl) {
KUNIT_EXPECT_EQ(test, (int)fl->oif, 17);
KUNIT_EXPECT_NE(test, fl->hw_flow_id, 0u);
}

destroy_cookie(0xCAFE01);
}

static void ask_flow_offload_test_destroy_round_trip(struct kunit *test)
{
struct ask_test_rule *r = test_rule_alloc(test, 1);
struct net_device *oif = test_stub_netdev(test, 18);
struct flow_cls_offload *f;
struct ask_flow_table *t = ask_flow_default_table();
int rc;

KUNIT_ASSERT_NOT_NULL(test, t);

test_rule_set_v4_tcp(r, htonl(0x0a000003), htonl(0x0a000004),
     htons(5555), htons(443));
r->rule->action.entries[0].id = FLOW_ACTION_REDIRECT;
r->rule->action.entries[0].dev = oif;

f = test_cls_alloc(test, FLOW_CLS_REPLACE, 0xCAFE02, r);
rc = dispatch(f);
KUNIT_ASSERT_EQ(test, rc, 0);

f = test_cls_alloc(test, FLOW_CLS_DESTROY, 0xCAFE02, NULL);
rc = dispatch(f);
KUNIT_EXPECT_EQ(test, rc, 0);

KUNIT_EXPECT_NULL(test, ask_flow_lookup(t, 0xCAFE02));
}

static void ask_flow_offload_test_double_destroy_swallowed(struct kunit *test)
{
struct flow_cls_offload *f;
int rc;

f = test_cls_alloc(test, FLOW_CLS_DESTROY, 0xDEADBEEF, NULL);
rc = dispatch(f);
KUNIT_EXPECT_EQ(test, rc, 0);

/* and again — still no error */
rc = dispatch(f);
KUNIT_EXPECT_EQ(test, rc, 0);
}

static void ask_flow_offload_test_stats_round_trip(struct kunit *test)
{
struct ask_test_rule *r = test_rule_alloc(test, 1);
struct net_device *oif = test_stub_netdev(test, 19);
struct flow_cls_offload *f;
struct ask_flow_table *t = ask_flow_default_table();
struct ask_flow *fl;
int rc;

KUNIT_ASSERT_NOT_NULL(test, t);

test_rule_set_v4_tcp(r, htonl(0x0a000005), htonl(0x0a000006),
     htons(7777), htons(8080));
r->rule->action.entries[0].id = FLOW_ACTION_REDIRECT;
r->rule->action.entries[0].dev = oif;

f = test_cls_alloc(test, FLOW_CLS_REPLACE, 0xCAFE03, r);
rc = dispatch(f);
KUNIT_ASSERT_EQ(test, rc, 0);

fl = ask_flow_lookup(t, 0xCAFE03);
KUNIT_ASSERT_NOT_NULL(test, fl);
ask_flow_update_stats(fl, 100, 14000);

f = test_cls_alloc(test, FLOW_CLS_STATS, 0xCAFE03, NULL);
rc = dispatch(f);
KUNIT_EXPECT_EQ(test, rc, 0);
KUNIT_EXPECT_EQ(test, f->stats.pkts, 100ULL);
KUNIT_EXPECT_EQ(test, f->stats.bytes, 14000ULL);

destroy_cookie(0xCAFE03);
}

static void ask_flow_offload_test_replace_idempotent(struct kunit *test)
{
struct ask_test_rule *r = test_rule_alloc(test, 1);
struct net_device *oif = test_stub_netdev(test, 20);
struct flow_cls_offload *f;
int rc;

test_rule_set_v4_tcp(r, htonl(0x0a000007), htonl(0x0a000008),
     htons(9999), htons(53));
r->rule->action.entries[0].id = FLOW_ACTION_REDIRECT;
r->rule->action.entries[0].dev = oif;

f = test_cls_alloc(test, FLOW_CLS_REPLACE, 0xCAFE04, r);
rc = dispatch(f);
KUNIT_ASSERT_EQ(test, rc, 0);

/* second REPLACE for same cookie — EEXIST swallowed */
rc = dispatch(f);
KUNIT_EXPECT_EQ(test, rc, 0);

destroy_cookie(0xCAFE04);
}

static void ask_flow_offload_test_action_unknown(struct kunit *test)
{
struct ask_test_rule *r = test_rule_alloc(test, 1);
struct flow_cls_offload *f;
struct ask_flow_table *t = ask_flow_default_table();
int rc;

KUNIT_ASSERT_NOT_NULL(test, t);

test_rule_set_v4_tcp(r, htonl(0x0a000009), htonl(0x0a00000a),
     htons(1111), htons(2222));
/* A bare MANGLE with htype UNSPEC (0) is not an ETH L2 rewrite, so
 * T-M6-A2 rejects it (and there is no REDIRECT either). */
r->rule->action.entries[0].id = FLOW_ACTION_MANGLE;

f = test_cls_alloc(test, FLOW_CLS_REPLACE, 0xCAFE05, r);
rc = dispatch(f);
KUNIT_EXPECT_EQ(test, rc, -EOPNOTSUPP);
KUNIT_EXPECT_NULL(test, ask_flow_lookup(t, 0xCAFE05));
}

/*
 * T-M6-A2: an ETH-type MANGLE is the next-hop L2 rewrite that the FE-VM
 * INSERT_L2_HDR already performs, so it MUST be accepted (paired with a
 * REDIRECT the flow offloads normally). This is the working IPv4 path and
 * proves the strict-acceptance change did not regress it.
 */
static void ask_flow_offload_test_action_mangle_eth_accepted(struct kunit *test)
{
struct ask_test_rule *r = test_rule_alloc(test, 2);
struct net_device *oif = test_stub_netdev(test, 30);
struct flow_cls_offload *f;
struct ask_flow_table *t = ask_flow_default_table();
int rc;

KUNIT_ASSERT_NOT_NULL(test, t);

test_rule_set_v4_tcp(r, htonl(0x0a000030), htonl(0x0a000031),
     htons(1000), htons(2000));
r->rule->action.entries[0].id = FLOW_ACTION_MANGLE;
r->rule->action.entries[0].mangle.htype = FLOW_ACT_MANGLE_HDR_TYPE_ETH;
r->rule->action.entries[1].id = FLOW_ACTION_REDIRECT;
r->rule->action.entries[1].dev = oif;

f = test_cls_alloc(test, FLOW_CLS_REPLACE, 0xCAFE30, r);
rc = dispatch(f);
KUNIT_EXPECT_EQ(test, rc, 0);
KUNIT_EXPECT_NOT_NULL(test, ask_flow_lookup(t, 0xCAFE30));

destroy_cookie(0xCAFE30);
}

/*
 * T-M6-A2: an IP4-type MANGLE is a NAT rewrite the HW record does not apply.
 * It MUST be rejected -EOPNOTSUPP (fall to SW) and MUST NOT publish a flow,
 * even though a valid REDIRECT is present.
 */
static void ask_flow_offload_test_action_mangle_nat_rejected(struct kunit *test)
{
struct ask_test_rule *r = test_rule_alloc(test, 2);
struct net_device *oif = test_stub_netdev(test, 31);
struct flow_cls_offload *f;
struct ask_flow_table *t = ask_flow_default_table();
int rc;

KUNIT_ASSERT_NOT_NULL(test, t);

test_rule_set_v4_tcp(r, htonl(0x0a000032), htonl(0x0a000033),
     htons(1000), htons(2000));
r->rule->action.entries[0].id = FLOW_ACTION_MANGLE;
r->rule->action.entries[0].mangle.htype = FLOW_ACT_MANGLE_HDR_TYPE_IP4;
r->rule->action.entries[1].id = FLOW_ACTION_REDIRECT;
r->rule->action.entries[1].dev = oif;

f = test_cls_alloc(test, FLOW_CLS_REPLACE, 0xCAFE31, r);
rc = dispatch(f);
KUNIT_EXPECT_EQ(test, rc, -EOPNOTSUPP);
KUNIT_EXPECT_NULL(test, ask_flow_lookup(t, 0xCAFE31));
}

/*
 * T-M6-A2: FLOW_ACTION_ADD (NAT field increment) is not applied in HW and
 * MUST be rejected, never published.
 */
static void ask_flow_offload_test_action_add_rejected(struct kunit *test)
{
struct ask_test_rule *r = test_rule_alloc(test, 2);
struct net_device *oif = test_stub_netdev(test, 32);
struct flow_cls_offload *f;
struct ask_flow_table *t = ask_flow_default_table();
int rc;

KUNIT_ASSERT_NOT_NULL(test, t);

test_rule_set_v4_tcp(r, htonl(0x0a000034), htonl(0x0a000035),
     htons(1000), htons(2000));
r->rule->action.entries[0].id = FLOW_ACTION_ADD;
r->rule->action.entries[1].id = FLOW_ACTION_REDIRECT;
r->rule->action.entries[1].dev = oif;

f = test_cls_alloc(test, FLOW_CLS_REPLACE, 0xCAFE32, r);
rc = dispatch(f);
KUNIT_EXPECT_EQ(test, rc, -EOPNOTSUPP);
KUNIT_EXPECT_NULL(test, ask_flow_lookup(t, 0xCAFE32));
}

/*
 * T-M6-A2: VLAN push/pop are not built into the HW record (deferred T-M6-8).
 * Previously they set ignored action_flags bits and offloaded without the
 * tag op; now they MUST be rejected and never published.
 */
static void ask_flow_offload_test_action_vlan_rejected(struct kunit *test)
{
struct ask_test_rule *r = test_rule_alloc(test, 2);
struct net_device *oif = test_stub_netdev(test, 33);
struct flow_cls_offload *f;
struct ask_flow_table *t = ask_flow_default_table();
int rc;

KUNIT_ASSERT_NOT_NULL(test, t);

test_rule_set_v4_tcp(r, htonl(0x0a000036), htonl(0x0a000037),
     htons(1000), htons(2000));
r->rule->action.entries[0].id = FLOW_ACTION_VLAN_PUSH;
r->rule->action.entries[1].id = FLOW_ACTION_REDIRECT;
r->rule->action.entries[1].dev = oif;

f = test_cls_alloc(test, FLOW_CLS_REPLACE, 0xCAFE33, r);
rc = dispatch(f);
KUNIT_EXPECT_EQ(test, rc, -EOPNOTSUPP);
KUNIT_EXPECT_NULL(test, ask_flow_lookup(t, 0xCAFE33));

/* VLAN_POP likewise */
r->rule->action.entries[0].id = FLOW_ACTION_VLAN_POP;
f = test_cls_alloc(test, FLOW_CLS_REPLACE, 0xCAFE34, r);
rc = dispatch(f);
KUNIT_EXPECT_EQ(test, rc, -EOPNOTSUPP);
KUNIT_EXPECT_NULL(test, ask_flow_lookup(t, 0xCAFE34));
}

static void ask_flow_offload_test_action_no_redirect(struct kunit *test)
{
struct ask_test_rule *r = test_rule_alloc(test, 1);
struct flow_cls_offload *f;
struct ask_flow_table *t = ask_flow_default_table();
int rc;

KUNIT_ASSERT_NOT_NULL(test, t);

test_rule_set_v4_tcp(r, htonl(0x0a00000b), htonl(0x0a00000c),
     htons(3333), htons(4444));
/* ACCEPT alone — no oif, dispatcher must reject */
r->rule->action.entries[0].id = FLOW_ACTION_ACCEPT;

f = test_cls_alloc(test, FLOW_CLS_REPLACE, 0xCAFE06, r);
rc = dispatch(f);
KUNIT_EXPECT_EQ(test, rc, -EOPNOTSUPP);
KUNIT_EXPECT_NULL(test, ask_flow_lookup(t, 0xCAFE06));
}

static void ask_flow_offload_test_ipv6_rejected(struct kunit *test)
{
struct ask_test_rule *r = test_rule_alloc(test, 1);
struct net_device *oif = test_stub_netdev(test, 21);
struct flow_cls_offload *f;
struct ask_flow_table *t = ask_flow_default_table();
int rc;

KUNIT_ASSERT_NOT_NULL(test, t);

test_rule_set_v6_marker(r);
r->rule->action.entries[0].id = FLOW_ACTION_REDIRECT;
r->rule->action.entries[0].dev = oif;

f = test_cls_alloc(test, FLOW_CLS_REPLACE, 0xCAFE07, r);
rc = dispatch(f);
KUNIT_EXPECT_EQ(test, rc, -EOPNOTSUPP);
KUNIT_EXPECT_NULL(test, ask_flow_lookup(t, 0xCAFE07));
}

/* ------------------------------------------------------------------------- */
/* suite lifecycle                                                            */
/*                                                                            */
/* ask.ko's module_init is not invoked in the kunit harness, so we manually   */
/* bring up the default flow table here. ask_flow_init() is NOT itself       */
/* idempotent (it would re-init the rhashtable in place), so we gate on a    */
/* file-static flag so this suite can run standalone or stacked behind        */
/* another suite that already brought it up. The matching exit path drops     */
/* the table only if we were the first to bring it up.                        */
/* ------------------------------------------------------------------------- */

static bool ask_flow_offload_owns_default_table;

static int ask_flow_offload_suite_init(struct kunit_suite *suite)
{
int rc;

if (ask_flow_default_table())
return 0;
rc = ask_flow_init();
if (rc)
return rc;
ask_flow_offload_owns_default_table = true;
return 0;
}

static void ask_flow_offload_suite_exit(struct kunit_suite *suite)
{
if (!ask_flow_offload_owns_default_table)
return;
ask_flow_exit();
ask_flow_offload_owns_default_table = false;
}

/* ------------------------------------------------------------------------- */
/* suite                                                                      */
/* ------------------------------------------------------------------------- */

/* ------------------------------------------------------------------------- */
/* PR14j tests — direction classifier + deferred-bind contract                */
/*                                                                            */
/* These pin two PR14j architectural decisions:                               */
/*                                                                            */
/*   A. ask_flow_offload_classify_dir() is a pure of_node walk.  It MUST be   */
/*      NULL-safe (return ASK_DIR_UNKNOWN on NULL dev) and MUST return        */
/*      ASK_DIR_UNKNOWN for any net_device whose dev.parent->of_node does     */
/*      not carry the DPAA1 MAC compatibles / phandle properties (e.g. lo,   */
/*      the dummy device that ask_test_flow_offload_setup() borrows, vmnet   */
/*      style virtio-net netdevs, etc.).  This guards us against a future    */
/*      change that promotes the helper from logging-only back to making a   */
/*      binding decision: it must keep returning UNKNOWN for non-DPAA paths. */
/*                                                                            */
/*   B. FLOW_BLOCK_BIND no longer calls ask_hw_port_bind().  On the kunit    */
/*      harness ask_hw_pcd_get() is NULL so any accidental call would       */
/*      return -ENODEV without crashing — but the contract we want to lock  */
/*      is "BIND succeeds even when REPLACE later cannot bind silicon".     */
/*      We exercise BIND on a dummy netdev (no FMan port id) and assert the */
/*      BIND path returns 0 regardless.                                      */
/* ------------------------------------------------------------------------- */

static void ask_flow_offload_test_classify_dir_null(struct kunit *test)
{
        KUNIT_EXPECT_EQ(test, ask_flow_offload_classify_dir(NULL),
                        ASK_DIR_UNKNOWN);
}

static void ask_flow_offload_test_classify_dir_non_dpaa(struct kunit *test)
{
        struct net_device *lo;

        /*
         * Loopback has a dev.parent->of_node of NULL on most arches.
         * The helper MUST gracefully return UNKNOWN rather than walk
         * into a NULL.  init_net's loopback is always present.
         */
        lo = dev_get_by_name(&init_net, "lo");
        if (!lo) {
                kunit_skip(test, "loopback not present in this test ns");
                return;
        }

        KUNIT_EXPECT_EQ(test, ask_flow_offload_classify_dir(lo),
                        ASK_DIR_UNKNOWN);
        dev_put(lo);
}

/*
 * CR-002 hard gate: the FE-VM EKFC key must come out byte-for-byte equal to
 * what the KeyGen extracts. Reference vector is silicon-verified (Qdrant
 * 2026-07-13, hardware CRC-64 match on a live flow):
 *
 *   10.99.2.106:44444 -> 10.99.2.185:55555 proto 6
 *   0a63026a 0a6302b9 06 ad9c d903
 *
 * The bug this pins: sport/dport are __be16, and the old builder emitted them
 * with (v >> 8) / (v & 0xff). On little-endian ARM64 that reads the __be16 as
 * a native integer, so wire bytes AD 9C came out as 9C AD. Insert and delete
 * shared the error and therefore agreed with each other — only a comparison
 * against the real extracted key exposes it, which is exactly what this does.
 *
 * F-188 (2026-08-12): PORT_ID is byte 0 but its production comparison
 * value is 0x00 (the scheme's zeroed dv default), NOT the raw hw port id.
 * This value and the MSB-first field order are silicon-confirmed by E25/E26.
 */
static void ask_flow_offload_test_fe_key_wire_order(struct kunit *test)
{
static const u8 expect[ASK_FE_KEY_SIZE] = {
0x00,                     /* PORT_ID (zeroed scheme default, F-188) */
0x0a, 0x63, 0x02, 0x6a,   /* SIP  10.99.2.106 */
0x0a, 0x63, 0x02, 0xb9,   /* DIP  10.99.2.185 */
0x06,                     /* PROTO TCP        */
0xad, 0x9c,               /* SPORT 44444      */
0xd9, 0x03,               /* DPORT 55555      */
};
struct ask_flow_key key;
u8 k[ASK_FE_KEY_SIZE];

memset(&key, 0, sizeof(key));
key.l3_proto = ASK_FLOW_L3_IPV4;
key.l4_proto = IPPROTO_TCP;
key.port_id = 0x11;
key.src_ip[0] = 0x0a; key.src_ip[1] = 0x63;
key.src_ip[2] = 0x02; key.src_ip[3] = 0x6a;
key.dst_ip[0] = 0x0a; key.dst_ip[1] = 0x63;
key.dst_ip[2] = 0x02; key.dst_ip[3] = 0xb9;
key.sport = htons(44444);
key.dport = htons(55555);

ask_fe_build_key(&key, k);
KUNIT_EXPECT_MEMEQ(test, k, expect, ASK_FE_KEY_SIZE);
KUNIT_EXPECT_EQ(test, k[0], (u8)0x00); /* raw key.port_id=0x11 is ignored */

/*
 * Ports must be non-palindromic for this to mean anything: assert the two
 * bytes differ, so a future byte-swap regression cannot pass by symmetry.
 */
KUNIT_EXPECT_NE(test, k[10], k[11]);
KUNIT_EXPECT_NE(test, k[12], k[13]);
}

/*
 * T-M6-1 Phase 1: pin the 38-byte IPv6 FE key layout so the v6 ehash table
 * (F-140, key_size=38) and ask_fe_build_key_v6() can never diverge:
 * PORT_ID(0x00) | SIP(16) | DIP(16) | PROTO | SPORT | DPORT, MSB-first.
 */
static void ask_flow_offload_test_fe_key_v6_wire_order(struct kunit *test)
{
static const u8 expect[ASK_FE_KEY_SIZE_V6] = {
0x00,                                           /* PORT_ID (F-188 zeroed) */
0x20,0x01,0x0d,0xb8,0x00,0x00,0x00,0x00,        /* SIP 2001:db8::1 */
0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x01,
0x20,0x01,0x0d,0xb8,0x00,0x00,0x00,0x00,        /* DIP 2001:db8::2 */
0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x02,
0x06,                                           /* PROTO TCP */
0xad,0x9c,                                      /* SPORT 44444 */
0xd9,0x03,                                      /* DPORT 55555 */
};
struct ask_flow_key key;
u8 k[ASK_FE_KEY_SIZE_V6];

KUNIT_EXPECT_EQ(test, (int)ASK_FE_KEY_SIZE_V6, 38);

memset(&key, 0, sizeof(key));
key.l3_proto = ASK_FLOW_L3_IPV6;
key.l4_proto = IPPROTO_TCP;
key.port_id  = 0x11;   /* raw hw id must be ignored, byte 0 stays 0x00 */
key.src_ip[0]=0x20; key.src_ip[1]=0x01; key.src_ip[2]=0x0d; key.src_ip[3]=0xb8;
key.src_ip[15]=0x01;
key.dst_ip[0]=0x20; key.dst_ip[1]=0x01; key.dst_ip[2]=0x0d; key.dst_ip[3]=0xb8;
key.dst_ip[15]=0x02;
key.sport = htons(44444);
key.dport = htons(55555);

ask_fe_build_key_v6(&key, k);
KUNIT_EXPECT_MEMEQ(test, k, expect, ASK_FE_KEY_SIZE_V6);
KUNIT_EXPECT_EQ(test, k[0], (u8)0x00);
KUNIT_EXPECT_NE(test, k[34], k[35]);   /* sport non-palindromic */
KUNIT_EXPECT_NE(test, k[36], k[37]);   /* dport non-palindromic */
}

/*
 * T-M6-A1: pin the canonical-intent lowering contract. The plain IPv4-unicast
 * intent (REDIRECT + TTL_DEC + ETH L2 rewrite) MUST lower to oif = the egress
 * ifindex and action_flags = 0 — the exact pre-A1 values — so the stored flow
 * and FE record stay byte-identical. An intent with no REDIRECT lowers to
 * -EOPNOTSUPP.
 */
static void ask_flow_offload_test_intent_lower_ipv4(struct kunit *test)
{
struct ask_flow_intent in = { .owner = 0xABCD };
u32 oif = 0, flags = 0xdeadbeef;
int rc;

KUNIT_ASSERT_EQ(test, ask_intent_add(&in, ASK_ACTION_REDIRECT, 42), 0);
KUNIT_ASSERT_EQ(test, ask_intent_add(&in, ASK_ACTION_TTL_DEC, 0), 0);
KUNIT_ASSERT_EQ(test, ask_intent_add(&in, ASK_ACTION_L2_REWRITE, 0), 0);

rc = ask_intent_lower(&in, &oif, &flags);
KUNIT_EXPECT_EQ(test, rc, 0);
KUNIT_EXPECT_EQ(test, oif, 42u);
/* Byte-identity anchor: the IPv4 path stored action_flags == 0 pre-A1. */
KUNIT_EXPECT_EQ(test, flags, 0u);
}

static void ask_flow_offload_test_intent_lower_no_redirect(struct kunit *test)
{
struct ask_flow_intent in = { .owner = 0xABCE };
u32 oif = 7, flags = 7;
int rc;

/* TTL_DEC/L2_REWRITE only, no egress → not offloadable. */
KUNIT_ASSERT_EQ(test, ask_intent_add(&in, ASK_ACTION_TTL_DEC, 0), 0);
rc = ask_intent_lower(&in, &oif, &flags);
KUNIT_EXPECT_EQ(test, rc, -EOPNOTSUPP);
}

static void ask_flow_offload_test_intent_add_overflow(struct kunit *test)
{
struct ask_flow_intent in = { 0 };
int i, rc = 0;

for (i = 0; i < ASK_INTENT_MAX_ACTIONS; i++)
KUNIT_EXPECT_EQ(test, ask_intent_add(&in, ASK_ACTION_TTL_DEC, 0), 0);
/* One past the cap must fail rather than overrun the array. */
rc = ask_intent_add(&in, ASK_ACTION_TTL_DEC, 0);
KUNIT_EXPECT_EQ(test, rc, -E2BIG);
KUNIT_EXPECT_EQ(test, (int)in.n_actions, ASK_INTENT_MAX_ACTIONS);
}

/*
 * T-M6-7.0: ask_intent_add_nat() stores the translated value in the right
 * union arm per action type and validates address length.
 */
static void ask_flow_offload_test_intent_add_nat(struct kunit *test)
{
struct ask_flow_intent in = { .owner = 0xCAFE70 };
u8 v4[4] = { 203, 0, 113, 7 };
u8 v6[16] = { 0x20,0x01,0x0d,0xb8,0,0,0,0,0,0,0,0,0,0,0,1 };
int rc;

/* SNAT v4 address lands in nat.addr[0..3]. */
rc = ask_intent_add_nat(&in, ASK_ACTION_NAT_SRC, v4, 4, 0);
KUNIT_EXPECT_EQ(test, rc, 0);
KUNIT_EXPECT_EQ(test, in.actions[0].type, ASK_ACTION_NAT_SRC);
KUNIT_EXPECT_EQ(test, memcmp(in.actions[0].nat.addr, v4, 4), 0);

/* DNAT v6 address lands in nat.addr[0..15]. */
rc = ask_intent_add_nat(&in, ASK_ACTION_NAT_DST, v6, 16, 0);
KUNIT_EXPECT_EQ(test, rc, 0);
KUNIT_EXPECT_EQ(test, memcmp(in.actions[1].nat.addr, v6, 16), 0);

/* NAPT sport lands in nat.port. */
rc = ask_intent_add_nat(&in, ASK_ACTION_NAPT_SPORT, NULL, 0, htons(5060));
KUNIT_EXPECT_EQ(test, rc, 0);
KUNIT_EXPECT_EQ(test, in.actions[2].nat.port, htons(5060));

/* Bad address length is rejected. */
rc = ask_intent_add_nat(&in, ASK_ACTION_NAT_SRC, v4, 5, 0);
KUNIT_EXPECT_EQ(test, rc, -EINVAL);
/* Non-NAT type via the NAT helper is rejected. */
rc = ask_intent_add_nat(&in, ASK_ACTION_REDIRECT, NULL, 0, 0);
KUNIT_EXPECT_EQ(test, rc, -EINVAL);
}

/*
 * T-M6-7.7: IPv4 NAT is shipping/default-on, so an IPv4 NAT intent lowers
 * successfully to the ASK_ACT_NAT_SRC flag (a valid REDIRECT is present, so
 * this exercises the NAT path specifically, not a missing egress).
 */
static void ask_flow_offload_test_intent_lower_nat_v4(struct kunit *test)
{
struct ask_flow_key k = { .l3_proto = ASK_FLOW_L3_IPV4 };
struct ask_flow_intent in = { .owner = 0xABCF, .match = &k };
u8 v4[4] = { 198, 51, 100, 9 };
u32 oif = 0, flags = 0;
int rc;

KUNIT_ASSERT_EQ(test, ask_intent_add(&in, ASK_ACTION_REDIRECT, 42), 0);
KUNIT_ASSERT_EQ(test, ask_intent_add(&in, ASK_ACTION_TTL_DEC, 0), 0);
KUNIT_ASSERT_EQ(test,
	ask_intent_add_nat(&in, ASK_ACTION_NAT_SRC, v4, 4, 0), 0);

rc = ask_intent_lower(&in, &oif, &flags);
KUNIT_EXPECT_EQ(test, rc, 0);
KUNIT_EXPECT_EQ(test, oif, 42u);
KUNIT_EXPECT_TRUE(test, (flags & ASK_ACT_NAT_SRC) != 0);
}

/*
 * T-M6-7.8: IPv6 NAT66 is shipping/default-on (silicon-validated S0-S3), so an
 * IPv6 NAT intent lowers successfully to the ASK_ACT_NAT_SRC flag, mirroring
 * nat44. A valid REDIRECT is present, so this exercises the NAT66 path.
 */
static void ask_flow_offload_test_intent_lower_nat66(struct kunit *test)
{
struct ask_flow_key k = { .l3_proto = ASK_FLOW_L3_IPV6 };
struct ask_flow_intent in = { .owner = 0xABD0, .match = &k };
u8 v6[16] = { 0x20,0x01,0x0d,0xb8,0,0,0,0,0,0,0,0,0,0,0,1 };
u32 oif = 0, flags = 0;
int rc;

KUNIT_ASSERT_EQ(test, ask_intent_add(&in, ASK_ACTION_REDIRECT, 42), 0);
KUNIT_ASSERT_EQ(test, ask_intent_add(&in, ASK_ACTION_TTL_DEC, 0), 0);
KUNIT_ASSERT_EQ(test,
	ask_intent_add_nat(&in, ASK_ACTION_NAT_SRC, v6, 16, 0), 0);

rc = ask_intent_lower(&in, &oif, &flags);
KUNIT_EXPECT_EQ(test, rc, 0);
KUNIT_EXPECT_EQ(test, oif, 42u);
KUNIT_EXPECT_TRUE(test, (flags & ASK_ACT_NAT_SRC) != 0);
}

static struct kunit_case ask_flow_offload_test_cases[] = {
KUNIT_CASE(ask_flow_offload_test_fe_key_wire_order),
KUNIT_CASE(ask_flow_offload_test_fe_key_v6_wire_order),
KUNIT_CASE(ask_flow_offload_test_intent_lower_ipv4),
KUNIT_CASE(ask_flow_offload_test_intent_lower_no_redirect),
KUNIT_CASE(ask_flow_offload_test_intent_add_overflow),
KUNIT_CASE(ask_flow_offload_test_intent_add_nat),
KUNIT_CASE(ask_flow_offload_test_intent_lower_nat_v4),
KUNIT_CASE(ask_flow_offload_test_intent_lower_nat66),
KUNIT_CASE(ask_flow_offload_test_replace_minimal),
KUNIT_CASE(ask_flow_offload_test_destroy_round_trip),
KUNIT_CASE(ask_flow_offload_test_double_destroy_swallowed),
KUNIT_CASE(ask_flow_offload_test_stats_round_trip),
KUNIT_CASE(ask_flow_offload_test_replace_idempotent),
KUNIT_CASE(ask_flow_offload_test_action_unknown),
KUNIT_CASE(ask_flow_offload_test_action_mangle_eth_accepted),
KUNIT_CASE(ask_flow_offload_test_action_mangle_nat_rejected),
KUNIT_CASE(ask_flow_offload_test_action_add_rejected),
KUNIT_CASE(ask_flow_offload_test_action_vlan_rejected),
KUNIT_CASE(ask_flow_offload_test_action_no_redirect),
KUNIT_CASE(ask_flow_offload_test_ipv6_rejected),
/* PR14j: direction classifier null-safety + non-DPAA fallthrough. */
KUNIT_CASE(ask_flow_offload_test_classify_dir_null),
KUNIT_CASE(ask_flow_offload_test_classify_dir_non_dpaa),
{}
};

struct kunit_suite ask_flow_offload_suite = {
.name        = "ask_flow_offload",
.suite_init  = ask_flow_offload_suite_init,
.suite_exit  = ask_flow_offload_suite_exit,
.test_cases  = ask_flow_offload_test_cases,
};