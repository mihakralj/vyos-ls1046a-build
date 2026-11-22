// SPDX-License-Identifier: GPL-2.0
/*
 * Host fault-injection tests for ASK2 Priority 1 policy revocation:
 *   1. G3 per-subsystem dirty/settled epochs (fib, dev, fdb) pause admission
 *      without cross-worker clobbering and reject stale snapshots.
 *   2. G3 non-OOM failure followed by clean ask_flow_policy_revoke() settles
 *      snapshotted G3 epochs so admission does NOT stay permanently wedged
 *      (Finding 2), while newer G3 events arriving mid-revoke remain dirty.
 *   3. Concurrent ask_policy_note_unresolved() during ask_flow_policy_revoke()
 *      prevents false RETIRED and preserves UNRESOLVED telemetry on re-entry
 *      (Finding 5).
 *   4. Repeated reconciliation from UNRESOLVED does NOT double-increment the
 *      nf_flow_table fence, lifts cleanly on transient recovery, stays stuck
 *      on unrecoverable orphan/identity_lost, and cleans up on module exit.
 *   5. Real ask_neigh_republish() post-install policy-race paths:
 *      clean rollback -> -EAGAIN; superseded -> -EINPROGRESS + UNRESOLVED +
 *      identity_lost; tombstone fail -> -EINPROGRESS + UNRESOLVED + id_lost
 *      (Finding 4).
 *   6. All four real SW match helpers (ask_devchg_sw_match, ask_fdbchg_sw_match,
 *      ask_fibchg_sw_match, ask_neigh_sw_match) including DNAT reply-src,
 *      VLAN vif dst_dev_rcu, FDB move/delete, and stale out.h_dest (Finding 6).
 */
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;

#define ETH_ALEN 6
#define U32_MAX UINT32_MAX
#define U64_MAX UINT64_MAX
#define NFPROTO_IPV4 2
#define NFPROTO_IPV6 10
#define ASK_FLOW_L3_IPV4 0
#define ASK_FLOW_L3_IPV6 1
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define EXPORT_SYMBOL_GPL(sym)
#define pr_info(...) do { } while (0)
#define pr_warn(...) do { warn_count++; } while (0)
#define pr_err(...) do { err_count++; } while (0)
#define pr_err_ratelimited(...) do { err_count++; } while (0)
#define rcu_read_lock() do { } while (0)
#define rcu_read_unlock() do { } while (0)

enum ask_revoke_state {
	ASK_REVOKE_IDLE = 0,
	ASK_REVOKE_FENCED = 1,
	ASK_REVOKE_RETIRED = 2,
	ASK_REVOKE_CONTAINED = 3,
	ASK_REVOKE_UNRESOLVED = 4,
};

enum ask_g3_subsys {
	ASK_G3_FIB = 0,
	ASK_G3_DEV = 1,
	ASK_G3_FDB = 2,
};

enum ask_hw_dir { ASK_HW_DIR_FWD = 0, ASK_HW_DIR_REV = 1 };
enum flow_offload_tuple_dir { FLOW_OFFLOAD_DIR_ORIGINAL = 0, FLOW_OFFLOAD_DIR_REPLY = 1, FLOW_OFFLOAD_DIR_MAX = 2 };
enum flow_offload_xmit_type {
	FLOW_OFFLOAD_XMIT_UNSPEC = 0,
	FLOW_OFFLOAD_XMIT_NEIGH,
	FLOW_OFFLOAD_XMIT_XFRM,
	FLOW_OFFLOAD_XMIT_DIRECT,
};

struct mutex { bool held; };
#define DEFINE_MUTEX(n) struct mutex n = { false }
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = true; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = false; }
#define lockdep_assert_held(m) assert((m)->held)

struct spinlock { bool held; };
#define DEFINE_SPINLOCK(n) struct spinlock n = { false }
static void spin_lock_bh(struct spinlock *l) { assert(!l->held); l->held = true; }
static void spin_unlock_bh(struct spinlock *l) { assert(l->held); l->held = false; }

typedef struct { int v; } atomic_t;
#define ATOMIC_INIT(x) { (x) }
static int atomic_read(const atomic_t *a) { return a->v; }

static bool ether_addr_equal(const u8 *a, const u8 *b) { return memcmp(a, b, ETH_ALEN) == 0; }
static bool is_zero_ether_addr(const u8 *a)
{
	return (a[0] | a[1] | a[2] | a[3] | a[4] | a[5]) == 0;
}

struct net { int id; };
static struct net init_net = { .id = 1 };
struct net_device { int ifindex; };
struct dst_entry { struct net_device *dev; bool valid; };
static struct net_device *dst_dev_rcu(const struct dst_entry *dst) { return dst ? dst->dev : NULL; }

struct in_addr { u8 bytes[4]; };
struct in6_addr { u8 bytes[16]; };
struct flow_offload_tuple {
	union { struct in_addr src_v4; struct in6_addr src_v6; };
	union { struct in_addr dst_v4; struct in6_addr dst_v6; };
	int iifidx;
	u8 l3proto;
	u8 xmit_type;
	struct dst_entry *dst_cache;
	union {
		struct { u32 ifidx; };
		struct { u32 ifidx; u32 hw_ifidx; u8 h_source[ETH_ALEN]; u8 h_dest[ETH_ALEN]; } out;
	};
};
struct flow_offload_tuple_rhash { struct flow_offload_tuple tuple; };
struct flow_offload { struct flow_offload_tuple_rhash tuplehash[FLOW_OFFLOAD_DIR_MAX]; };
static bool nf_flow_dst_check(struct flow_offload_tuple *t)
{
	return !t->dst_cache || t->dst_cache->valid;
}

struct ask_flow_key { u8 l3_proto; u32 nh_ifindex; u8 nh_ip[16]; };
struct ask_flow {
	u64 cookie;
	u64 fe_token;
	bool hw_backed;
	bool present;
};
struct ask_flow_table {
	atomic_t num_hw_backed;
	atomic_t num_flows;
};
static struct ask_flow_table default_table;
static struct ask_flow_table *ask_flow_default_table(void) { return &default_table; }

static DEFINE_MUTEX(ask_flow_lifecycle_lock);
static unsigned int warn_count, err_count;
static int nf_fence_depth;
static unsigned int sync_rcu_calls;
static unsigned int pending_drain_calls;
static unsigned int mock_ft_unresolved;
static int mock_ft_rc;
static int mock_sweep_rc;
static bool mock_sweep_identity_lost;
static unsigned int mock_orphan_count;
static int mock_wgfp_revoke_rc;
static unsigned int wgfp_revoke_calls;
static bool inject_concurrent_unresolved_during_sweep;
static bool inject_new_g3_during_sweep;
static u8 observed_state_during_sweep;

/* G3 snapshot state used by real ask_*_sw_match helpers. */
struct ask_fdbchg { u8 addr[ETH_ALEN]; int ifindex; };
struct ask_fibchg_pfx { u8 l3_proto; u8 plen; u8 addr[16]; };
static int ask_devchg_snap[4];
static unsigned int ask_devchg_snap_n;
static bool ask_devchg_snap_all;
static struct ask_fdbchg ask_fdbchg_snap[4];
static unsigned int ask_fdbchg_snap_n;
static bool ask_fdbchg_snap_all;
static struct ask_fibchg_pfx ask_fibchg_snap[4];
static unsigned int ask_fibchg_snap_n;
static bool ask_fibchg_snap_all;

static bool ask_devchg_hit(u32 ifindex)
{
	unsigned int i;

	if (!ifindex)
		return false;
	for (i = 0; i < ask_devchg_snap_n; i++)
		if ((u32)ask_devchg_snap[i] == ifindex)
			return true;
	return false;
}

static bool ask_fibchg_pfx_match(const struct ask_fibchg_pfx *p, u8 l3,
				 const u8 *addr)
{
	unsigned int bytes = p->plen / 8, bits = p->plen % 8;

	if (p->l3_proto != l3)
		return false;
	if (memcmp(p->addr, addr, bytes))
		return false;
	if (bits) {
		u8 mask = (u8)(0xff << (8 - bits));
		if ((p->addr[bytes] ^ addr[bytes]) & mask)
			return false;
	}
	return true;
}

static void nf_flow_table_fence_begin(void) { nf_fence_depth++; }
static void nf_flow_table_fence_end(void)
{
	assert(nf_fence_depth > 0);
	nf_fence_depth--;
}
static void synchronize_rcu(void)
{
	assert(!ask_flow_lifecycle_lock.held);
	sync_rcu_calls++;
}
static int nf_flow_table_revoke_all(struct net *net, bool wait_hw,
				    unsigned int *unresolved_out)
{
	(void)net;
	assert(!ask_flow_lifecycle_lock.held);
	assert(wait_hw);
	if (unresolved_out)
		*unresolved_out = mock_ft_unresolved;
	return mock_ft_rc;
}
static void ask_flow_pending_drain_all(void)
{
	pending_drain_calls++;
}
static unsigned int ask_fe_orphan_count_get(void)
{
	return mock_orphan_count;
}
static int dpaa_wgfp_policy_revoke(void)
{
	assert(!ask_flow_lifecycle_lock.held);
	wgfp_revoke_calls++;
	return mock_wgfp_revoke_rc;
}

static void ask_policy_note_unresolved(u32 count, bool identity_lost);
static u64 ask_policy_mark_g3_dirty(enum ask_g3_subsys subsys);
static void ask_policy_get_telemetry(u64 *epoch, u8 *state, u32 *unresolved);

static int ask_flow_policy_sweep_hw(bool *identity_lost)
{
	u8 st;

	assert(!ask_flow_lifecycle_lock.held);
	ask_policy_get_telemetry(NULL, &st, NULL);
	observed_state_during_sweep = st;
	if (inject_concurrent_unresolved_during_sweep)
		ask_policy_note_unresolved(1, false);
	if (inject_new_g3_during_sweep)
		(void)ask_policy_mark_g3_dirty(ASK_G3_FIB);
	if (identity_lost)
		*identity_lost = mock_sweep_identity_lost;
	return mock_sweep_rc;
}

#include "ask_policy_state_body.h"
#include "ask_sw_match_body.h"

/* Mocks to drive real ask_neigh_republish() post-install policy/neigh races. */
static struct ask_flow mock_flow;
static bool mock_bump_epoch_in_fe_install;
static bool mock_bump_neigh_epoch_in_fe_install;
static u64 mock_neigh_epoch = 1;
static bool mock_tombstone_ok = true;
static int mock_withdraw_rc;
static bool mock_withdraw_superseded;
static unsigned int mock_gen_releases;

static bool ask_neigh_epoch_current(int ifindex, u8 l3_proto, const u8 *ip, u64 epoch)
{
	(void)ifindex; (void)l3_proto; (void)ip;
	return epoch == mock_neigh_epoch;
}
static int ask_neigh_republish_fenced(struct ask_flow_table *t, u64 cookie,
				      const struct ask_flow_key *key, u32 oif,
				      u32 action_flags, enum ask_hw_dir dir,
				      u64 generation, u64 policy_epoch,
				      u64 neigh_epoch, bool check_neigh);

static int ask_flow_insert_owned_identified(struct ask_flow_table *t, u64 cookie,
					    const struct ask_flow_key *key,
					    u32 oif, u32 action_flags,
					    enum ask_hw_dir dir, u64 gen,
					    u32 *hw_id, u64 *fe_token)
{
	(void)t; (void)key; (void)oif; (void)action_flags; (void)dir; (void)gen;
	mock_flow.cookie = cookie;
	mock_flow.fe_token = 77;
	mock_flow.hw_backed = true;
	mock_flow.present = true;
	*hw_id = 1;
	*fe_token = 77;
	return 0;
}
static struct ask_flow *ask_flow_lookup(struct ask_flow_table *t, u64 cookie)
{
	(void)t;
	return (mock_flow.present && mock_flow.cookie == cookie) ? &mock_flow : NULL;
}
static bool ask_flow_gen_is_current(struct ask_flow_table *t, u64 c, u64 g)
{
	(void)t; (void)c; (void)g;
	return true;
}
static bool ask_flow_token_is_current(struct ask_flow_table *t, u64 c, u64 tok)
{
	(void)t; (void)c;
	return mock_flow.present && mock_flow.fe_token == tok;
}
static int ask_flow_remove_owned_identified(struct ask_flow_table *t, u64 c, u64 g, u64 tok)
{
	(void)t; (void)c; (void)g; (void)tok;
	mock_flow.present = false;
	return 0;
}
static int ask_flow_fe_install(const struct ask_flow_key *key, u32 hw_id,
			       const struct net_device *egress_dev, u64 cookie,
			       u64 fe_token)
{
	(void)key; (void)hw_id; (void)egress_dev; (void)cookie; (void)fe_token;
	if (mock_bump_epoch_in_fe_install)
		ask_policy_bump_epoch();
	if (mock_bump_neigh_epoch_in_fe_install)
		mock_neigh_epoch++;
	return 0;
}
static bool ask_flow_gen_tombstone_if_current(struct ask_flow_table *t, u64 c, u64 g)
{
	(void)t; (void)c; (void)g;
	return mock_tombstone_ok;
}
static void ask_flow_gen_release(struct ask_flow_table *t, u64 c, u64 g)
{
	(void)t; (void)c; (void)g;
	mock_gen_releases++;
}
static int ask_flow_withdraw_owned(struct ask_flow_table *t, u64 c, u64 g, u64 tok,
				   const struct ask_flow_key *key, bool hw_backed,
				   const char *what, bool *superseded)
{
	(void)t; (void)c; (void)g; (void)tok; (void)key; (void)hw_backed; (void)what;
	if (superseded)
		*superseded = mock_withdraw_superseded;
	if (!mock_withdraw_rc)
		mock_flow.present = false;
	return mock_withdraw_rc;
}

#include "ask_neigh_republish_body.h"

static void reset_all(void)
{
	warn_count = err_count = 0;
	nf_fence_depth = 0;
	sync_rcu_calls = 0;
	pending_drain_calls = 0;
	mock_ft_unresolved = 0;
	mock_ft_rc = 0;
	mock_sweep_rc = 0;
	mock_sweep_identity_lost = false;
	mock_orphan_count = 0;
	mock_wgfp_revoke_rc = 0;
	wgfp_revoke_calls = 0;
	inject_concurrent_unresolved_during_sweep = false;
	inject_new_g3_during_sweep = false;
	observed_state_during_sweep = ASK_REVOKE_IDLE;
	default_table.num_hw_backed.v = 0;
	default_table.num_flows.v = 0;

	ask_policy_epoch = 1;
	ask_policy_epoch_exhausted = false;
	ask_policy_fence_depth = 0;
	ask_policy_revoke_state = ASK_REVOKE_IDLE;
	ask_policy_unresolved_count = 0;
	ask_policy_unresolved_seq = 0;
	ask_policy_identity_lost = false;
	ask_policy_nf_fence_held = false;
	ask_policy_self_fence_held = false;
	ask_g3_fib_epoch = 0;
	ask_g3_fib_settled = 0;
	ask_g3_dev_epoch = 0;
	ask_g3_dev_settled = 0;
	ask_g3_fdb_epoch = 0;
	ask_g3_fdb_settled = 0;

	memset(&mock_flow, 0, sizeof(mock_flow));
	mock_bump_epoch_in_fe_install = false;
	mock_bump_neigh_epoch_in_fe_install = false;
	mock_neigh_epoch = 1;
	mock_tombstone_ok = true;
	mock_withdraw_rc = 0;
	mock_withdraw_superseded = false;
	mock_gen_releases = 0;
}

static void test_g3_independent_epochs_and_reconcile(void)
{
	u64 e0, e1, fib_ep, dev_ep;

	reset_all();
	assert(ask_policy_epoch_snapshot(&e0) == 0 && e0 == 1);
	assert(ask_policy_epoch_is_current(e0));

	/* FIB and DEV events overlap; settling DEV must NOT reopen admission before FIB settles. */
	fib_ep = ask_policy_mark_g3_dirty(ASK_G3_FIB);
	dev_ep = ask_policy_mark_g3_dirty(ASK_G3_DEV);
	assert(fib_ep == 1 && dev_ep == 1);
	assert(!ask_policy_epoch_is_current(e0));
	assert(ask_policy_epoch_snapshot(&e1) == -EAGAIN);

	ask_policy_mark_g3_settled(ASK_G3_DEV, dev_ep);
	assert(ask_policy_epoch_snapshot(&e1) == -EAGAIN);

	/* Finding 2: non-OOM G3 failure marks UNRESOLVED and leaves fib dirty.
	 * A clean global ask_flow_policy_revoke() MUST settle the snapshotted
	 * G3 epoch so admission is not permanently wedged! */
	ask_policy_note_unresolved(1, false);
	assert(ask_policy_epoch_snapshot(&e1) == -EBUSY);
	assert(ask_flow_policy_revoke() == 0);
	assert(observed_state_during_sweep == ASK_REVOKE_UNRESOLVED); /* Finding 5: no FENCED clobber */
	assert(ask_policy_epoch_snapshot(&e1) == 0);
	assert(e1 > e0);

	/* A NEW G3 event arriving *during* revoke must remain dirty after revoke returns! */
	inject_new_g3_during_sweep = true;
	assert(ask_flow_policy_revoke() == 0);
	inject_new_g3_during_sweep = false;
	assert(ask_policy_epoch_snapshot(&e1) == -EAGAIN);
}

static void test_revoke_clean_and_concurrent_unresolved(void)
{
	u64 epoch, snap;
	u8 state;
	u32 unresolved;

	reset_all();
	assert(ask_flow_policy_revoke() == 0);
	ask_policy_get_telemetry(&epoch, &state, &unresolved);
	assert(epoch == 2 && state == ASK_REVOKE_RETIRED && unresolved == 0);
	assert(nf_fence_depth == 0 && ask_policy_fence_depth == 0);
	assert(sync_rcu_calls == 1 && pending_drain_calls == 1);

	/* Finding 5: concurrent note_unresolved during sweep must refuse RETIRED. */
	inject_concurrent_unresolved_during_sweep = true;
	assert(ask_flow_policy_revoke() == -EBUSY);
	inject_concurrent_unresolved_during_sweep = false;
	ask_policy_get_telemetry(&epoch, &state, &unresolved);
	assert(state == ASK_REVOKE_UNRESOLVED && unresolved >= 1);
	assert(nf_fence_depth == 1 && ask_policy_fence_depth == 1);

	/* Second attempt while still unsettled must NOT double-increment fence. */
	mock_ft_unresolved = 2;
	mock_ft_rc = -EBUSY;
	assert(ask_flow_policy_revoke() == -EBUSY);
	assert(observed_state_during_sweep == ASK_REVOKE_UNRESOLVED);
	assert(nf_fence_depth == 1 && ask_policy_fence_depth == 1);

	/* Recovery clears transient unresolved and lifts fence to 0. */
	mock_ft_unresolved = 0;
	mock_ft_rc = 0;
	/* WGFP revoke failure must also hold UNRESOLVED and fence. */
	mock_wgfp_revoke_rc = -EBUSY;
	assert(ask_flow_policy_revoke() == -EBUSY);
	ask_policy_get_telemetry(&epoch, &state, &unresolved);
	assert(state == ASK_REVOKE_UNRESOLVED && unresolved >= 1 && nf_fence_depth == 1);
	mock_wgfp_revoke_rc = 0;

	assert(ask_flow_policy_revoke() == 0);
	ask_policy_get_telemetry(&epoch, &state, &unresolved);
	assert(state == ASK_REVOKE_RETIRED && unresolved == 0);
	assert(nf_fence_depth == 0 && ask_policy_fence_depth == 0);
	assert(ask_policy_epoch_snapshot(&snap) == 0 && snap == epoch);
}

static void test_republish_post_install_policy_races(void)
{
	struct ask_flow_key key = { .l3_proto = ASK_FLOW_L3_IPV4 };
	u64 ep, snap;
	u8 state;
	u32 unresolved;

	/* Clean policy-race rollback -> returns -EAGAIN, releases gen, stays healthy. */
	reset_all();
	assert(ask_policy_epoch_snapshot(&ep) == 0);
	mock_bump_epoch_in_fe_install = true;
	mutex_lock(&ask_flow_lifecycle_lock);
	assert(ask_neigh_republish(&default_table, 42, &key, 4, 0,
				   ASK_HW_DIR_FWD, 1, ep) == -EAGAIN);
	mutex_unlock(&ask_flow_lifecycle_lock);
	assert(mock_gen_releases == 1 && !mock_flow.present);
	assert(ask_policy_epoch_snapshot(&snap) == 0);

	/* Stage 3 (#6): neigh-epoch race during ask_neigh_republish_fenced() rolls back cleanly. */
	reset_all();
	key.nh_ifindex = 5;
	assert(ask_policy_epoch_snapshot(&ep) == 0);
	mock_bump_neigh_epoch_in_fe_install = true;
	mutex_lock(&ask_flow_lifecycle_lock);
	assert(ask_neigh_republish_fenced(&default_table, 42, &key, 4, 0,
					  ASK_HW_DIR_FWD, 1, ep,
					  mock_neigh_epoch, true) == -EAGAIN);
	mutex_unlock(&ask_flow_lifecycle_lock);
	assert(mock_gen_releases == 1 && !mock_flow.present);
	assert(ask_policy_epoch_snapshot(&snap) == 0);

	/* Finding 4: FE superseded during policy-race rollback -> -EINPROGRESS + UNRESOLVED + sticky id_lost. */
	reset_all();
	assert(ask_policy_epoch_snapshot(&ep) == 0);
	mock_bump_epoch_in_fe_install = true;
	mock_withdraw_superseded = true;
	mutex_lock(&ask_flow_lifecycle_lock);
	assert(ask_neigh_republish(&default_table, 42, &key, 4, 0,
				   ASK_HW_DIR_FWD, 1, ep) == -EINPROGRESS);
	mutex_unlock(&ask_flow_lifecycle_lock);
	assert(mock_gen_releases == 0);
	ask_policy_get_telemetry(NULL, &state, &unresolved);
	assert(state == ASK_REVOKE_UNRESOLVED && unresolved == 1);
	assert(ask_policy_identity_lost);

	/* Finding 4: tombstone failure on policy race -> -EINPROGRESS + UNRESOLVED + sticky id_lost. */
	reset_all();
	assert(ask_policy_epoch_snapshot(&ep) == 0);
	mock_bump_epoch_in_fe_install = true;
	mock_tombstone_ok = false;
	mutex_lock(&ask_flow_lifecycle_lock);
	assert(ask_neigh_republish(&default_table, 42, &key, 4, 0,
				   ASK_HW_DIR_FWD, 1, ep) == -EINPROGRESS);
	mutex_unlock(&ask_flow_lifecycle_lock);
	ask_policy_get_telemetry(NULL, &state, &unresolved);
	assert(state == ASK_REVOKE_UNRESOLVED && unresolved == 1);
	assert(ask_policy_identity_lost);
}

static void test_sw_match_helpers(void)
{
	struct net_device vif = { .ifindex = 30 };
	struct dst_entry dst_vif = { .dev = &vif, .valid = true };
	struct flow_offload f = {};
	const u8 mac_old[ETH_ALEN] = { 0x02, 0x00, 0x00, 0x00, 0x00, 0x01 };
	const u8 mac_new[ETH_ALEN] = { 0x02, 0x00, 0x00, 0x00, 0x00, 0x02 };

	/* devchg_sw_match: matches VLAN vif via dst_dev_rcu even when iifidx/hw_ifidx are physical. */
	ask_devchg_snap_all = false;
	ask_devchg_snap_n = 1;
	ask_devchg_snap[0] = 30;
	f.tuplehash[0].tuple.iifidx = 3;
	f.tuplehash[0].tuple.xmit_type = FLOW_OFFLOAD_XMIT_DIRECT;
	f.tuplehash[0].tuple.out.ifidx = 4;
	f.tuplehash[0].tuple.out.hw_ifidx = 4;
	f.tuplehash[0].tuple.dst_cache = &dst_vif;
	assert(ask_devchg_sw_match(&f, NULL));
	ask_devchg_snap[0] = 99;
	assert(!ask_devchg_sw_match(&f, NULL));

	/* fdbchg_sw_match: matches moved/deleted MAC, skips same-port relearn. */
	ask_fdbchg_snap_all = false;
	ask_fdbchg_snap_n = 1;
	memcpy(ask_fdbchg_snap[0].addr, mac_old, ETH_ALEN);
	ask_fdbchg_snap[0].ifindex = 4; /* same port */
	memcpy(f.tuplehash[0].tuple.out.h_dest, mac_old, ETH_ALEN);
	assert(!ask_fdbchg_sw_match(&f, NULL));
	ask_fdbchg_snap[0].ifindex = 5; /* moved to port 5 */
	assert(ask_fdbchg_sw_match(&f, NULL));

	/* fibchg_sw_match: matches DNAT post-translation dest stored in reply tuple's src_v4! */
	ask_fibchg_snap_all = false;
	ask_fibchg_snap_n = 1;
	ask_fibchg_snap[0].l3_proto = ASK_FLOW_L3_IPV4;
	ask_fibchg_snap[0].plen = 24;
	ask_fibchg_snap[0].addr[0] = 10;
	ask_fibchg_snap[0].addr[1] = 99;
	ask_fibchg_snap[0].addr[2] = 2;
	f.tuplehash[0].tuple.l3proto = NFPROTO_IPV4;
	f.tuplehash[1].tuple.l3proto = NFPROTO_IPV4;
	f.tuplehash[1].tuple.src_v4.bytes[0] = 10;
	f.tuplehash[1].tuple.src_v4.bytes[1] = 99;
	f.tuplehash[1].tuple.src_v4.bytes[2] = 2;
	f.tuplehash[1].tuple.src_v4.bytes[3] = 113;
	assert(ask_fibchg_sw_match(&f, NULL));

	/* neigh_sw_match: matches stale old_mac on vif, ignores already-updated MAC
	 * and unrelated hosts with a different MAC on the same interface (#7);
	 * falls back fail-closed on overflow and matches direct dst_ip for v4/v6. */
	{
		const u8 mac_other[ETH_ALEN] = { 0x02, 0x00, 0x00, 0x00, 0x00, 0x99 };
		const u8 old_macs[1][ETH_ALEN] = { { 0x02, 0x00, 0x00, 0x00, 0x00, 0x01 } };
		const u8 ip4_hit[4] = { 10, 99, 2, 113 };
		const u8 ip4_miss[4] = { 10, 99, 2, 200 };
		const u8 ip6_hit[16] = { 0xfd, 0x99, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x13 };
		struct ask_neigh_sw_ctx ctx = {
			.ifindex = 30,
			.new_mac = mac_new,
			.old_macs = old_macs,
			.n_old_macs = 1,
		};
		memcpy(f.tuplehash[0].tuple.out.h_dest, mac_old, ETH_ALEN);
		assert(ask_neigh_sw_match(&f, &ctx));
		memcpy(f.tuplehash[0].tuple.out.h_dest, mac_other, ETH_ALEN);
		assert(!ask_neigh_sw_match(&f, &ctx));
		/* Overflow falls back fail-closed on the interface. */
		ctx.old_mac_overflow = true;
		assert(ask_neigh_sw_match(&f, &ctx));
		ctx.old_mac_overflow = false;
		/* Direct IPv4 dst_ip match works even when old_macs doesn't list mac_other. */
		memcpy(f.tuplehash[0].tuple.dst_v4.bytes, ip4_hit, 4);
		ctx.l3_proto = ASK_FLOW_L3_IPV4;
		ctx.dst_ip = ip4_miss;
		assert(!ask_neigh_sw_match(&f, &ctx));
		ctx.dst_ip = ip4_hit;
		assert(ask_neigh_sw_match(&f, &ctx));
		/* Direct IPv6 dst_ip match. */
		f.tuplehash[0].tuple.l3proto = NFPROTO_IPV6;
		memcpy(f.tuplehash[0].tuple.dst_v6.bytes, ip6_hit, 16);
		ctx.l3_proto = ASK_FLOW_L3_IPV6;
		ctx.dst_ip = ip6_hit;
		assert(ask_neigh_sw_match(&f, &ctx));
		/* Already-updated MAC is always skipped even on overflow. */
		ctx.old_mac_overflow = true;
		memcpy(f.tuplehash[0].tuple.out.h_dest, mac_new, ETH_ALEN);
		assert(!ask_neigh_sw_match(&f, &ctx));
	}
}

static void test_unrecoverable_orphan_exhaustion_and_exit(void)
{
	u64 snap;
	u8 state;
	u32 unresolved;

	reset_all();
	mock_orphan_count = 1;
	assert(ask_flow_policy_revoke() == -EBUSY);
	ask_policy_get_telemetry(NULL, &state, &unresolved);
	assert(state == ASK_REVOKE_UNRESOLVED && unresolved >= 1 && nf_fence_depth == 1);

	mock_orphan_count = 0;
	mock_sweep_identity_lost = true;
	assert(ask_flow_policy_revoke() == -EBUSY);
	mock_sweep_identity_lost = false;
	assert(ask_flow_policy_revoke() == -EBUSY);
	ask_policy_revoke_exit();
	assert(nf_fence_depth == 0 && ask_policy_fence_depth == 0);

	reset_all();
	ask_policy_epoch = U64_MAX;
	ask_policy_bump_epoch();
	assert(ask_policy_epoch_exhausted);
	assert(ask_policy_epoch_snapshot(&snap) == -EOVERFLOW);
	assert(!ask_policy_epoch_is_current(U64_MAX));
}

int main(void)
{
	test_g3_independent_epochs_and_reconcile();
	test_revoke_clean_and_concurrent_unresolved();
	test_republish_post_install_policy_races();
	test_sw_match_helpers();
	test_unrecoverable_orphan_exhaustion_and_exit();
	puts("ask_policy_revocation: OK (5/5 suites)");
	return 0;
}
