// SPDX-License-Identifier: GPL-2.0
/*
 * Host fault injection for ASK's FE module lease and retained-owner paths.
 */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
#define U64_MAX UINT64_MAX
#define EXPORT_SYMBOL_GPL(sym)
#define THIS_MODULE NULL
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define ask_pr_info(...) ((void)0)
#define ask_pr_warn(...) ((void)0)
#define ask_pr_err(...) ((void)0)
#define ask_pr_dbg(...) ((void)0)
#define ASK_HW_MAX_PORTS 8
#define ASK_FAM_V4 1
#define ASK_FAM_V6 2

enum fman_pcd_fe_profile { FMAN_PCD_FE_PROFILE_ROUTED, FMAN_PCD_FE_PROFILE_L2_DA };
struct mutex { int held; };
struct fman { int unused; };
struct device { int refs; };
struct xarray { int unused; };
struct kmem_cache { int unused; };
struct ask_hw_flow_cookie { int unused; };
struct sk_buff { int unused; };
struct net_device { int unused; };
struct nlattr { u8 value; };
#define ASK_ATTR_PORT_ID 0
#define ASK_ATTR_FAMILY_MASK 1
struct genl_info { struct nlattr *attrs[2]; };

struct ask_hw_port {
	bool in_use;
	u8 port_id;
	bool offload_engaged;
	enum fman_pcd_fe_profile engaged_profile;
	u32 punt_mask;
	bool fe_retained;
	bool teardown_in_progress;
	bool module_pinned;
};

struct ask_hw_pcd {
	struct mutex lock;
	struct fman *fman;
	struct ask_hw_port port[ASK_HW_MAX_PORTS];
	struct xarray flow_cookies;
};

static struct ask_hw_pcd *ask_hw_pcd_inst;
static struct kmem_cache *ask_hw_cookie_cache;
static unsigned long ask_hw_port_l2[1];
static unsigned long ask_hw_enq_fe_off;
static bool ask_hw_ready;
static u8 families[64];
#define ask_hw_port_family families
static u64 ask_hw_family_seq = 1;
static u64 ask_hw_family_changed[64][2];
static bool ask_hw_family_seq_exhausted;
static struct fman fm;
static struct device fman_dev = { .refs = 1 };

static int disengage_rc, reserve_rc, engage_rc, punt_rc, vlan_rc;
static int disengages, engages, forgets, flushes;
static int punt_calls, vlan_teardowns, prepare_calls;
static int module_refs;
static int prewarms, bridge_kicks;
static bool rtnl_held;
static bool allow_module_get, check_forget_race, expect_unpublished;
static bool expect_disengage_unpublished;
static bool check_flush_family;
static u8 expected_flush_family;

static int ask_hw_offload_engage_profile(u8 port,
					 enum fman_pcd_fe_profile profile,
					 bool from_flow, u8 family);
int ask_hw_offload_disengage(u8 port);
void ask_hw_offload_set_family(u8 port, u8 family);

static struct ask_hw_pcd *ask_hw_pcd_get(void) { return ask_hw_pcd_inst; }
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = 1; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = 0; }
static void mutex_destroy(struct mutex *m) { assert(!m->held); }
static void kernel_param_lock(void *m) { (void)m; }
static void kernel_param_unlock(void *m) { (void)m; }
static void clear_bit(unsigned int nr, unsigned long *map) { map[0] &= ~(1UL << nr); }
static void set_bit(unsigned int nr, unsigned long *map) { map[0] |= 1UL << nr; }
static u8 ask_hw_port_family_get(u8 port) { return families[port]; }
static struct ask_hw_port *ask_hw_port_slot_get(struct ask_hw_pcd *h, u8 port)
{
	unsigned int i;

	for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
		if (!h->port[i].in_use) {
			h->port[i].in_use = true;
			h->port[i].port_id = port;
			return &h->port[i];
		}
		if (h->port[i].port_id == port)
			return &h->port[i];
	}
	return NULL;
}
static bool try_module_get(void *module)
{
	(void)module;
	if (!allow_module_get)
		return false;
	module_refs++;
	return true;
}
static void module_put(void *module)
{
	(void)module;
	assert(module_refs > 0);
	module_refs--;
}
static int ask_hw_punt_apply(struct ask_hw_pcd *h, struct ask_hw_port *p, u32 m)
{
	(void)h;
	punt_calls++;
	if (punt_rc)
		return punt_rc;
	p->punt_mask = m;
	return 0;
}
static u32 ask_hw_punt_wanted(struct ask_hw_port *p) { (void)p; return 0; }
static int ask_vlan_cc_teardown_port(u8 port)
{
	(void)port;
	vlan_teardowns++;
	return vlan_rc;
}
static void ask_flow_pppoe_flush(void)
{
	if (check_flush_family)
		assert(families[0x10] == expected_flush_family);
	flushes++;
}

static void ask_bridge_forget_port(u8 port)
{
	assert(port == 0x10);
	assert(!ask_hw_pcd_inst || !ask_hw_pcd_inst->lock.held);
	assert(module_refs > 0);
	if (check_forget_race) {
		assert(ask_hw_offload_engage_profile(port,
			   FMAN_PCD_FE_PROFILE_ROUTED, false, ASK_FAM_V6) == -EAGAIN);
		ask_hw_offload_set_family(port, ASK_FAM_V6);
		assert(!families[port]);
	}
	forgets++;
}

static int fman_pcd_fe_disengage(struct fman *f, u8 port)
{
	assert(f == &fm && port == 0x10);
	assert(module_refs > 0);
	if (expect_disengage_unpublished)
		assert(!families[port]);
	disengages++;
	return disengage_rc;
}

static int fman_pcd_fe_disengage_prepared(struct fman *f, u8 port,
					  int (*prepare)(void *), void *data)
{
	int rc;

	if (reserve_rc)
		return reserve_rc;
	prepare_calls++;
	rc = prepare(data);
	if (rc)
		return rc;
	return fman_pcd_fe_disengage(f, port);
}

static int fman_pcd_fe_engage(struct fman *f, u8 port, u32 fqid)
{
	assert(f == &fm && port == 0x10 && fqid == 0);
	assert(module_refs > 0);
	if (expect_unpublished)
		assert(!families[port]);
	engages++;
	return engage_rc;
}

static int fman_pcd_fe_engage_profile(struct fman *f, u8 port, u32 fqid,
				      enum fman_pcd_fe_profile profile)
{
	(void)profile;
	return fman_pcd_fe_engage(f, port, fqid);
}

static unsigned long fman_pcd_fe_enq_get_offset(struct fman *f) { (void)f; return 0x40; }
static struct device *fman_get_dev(struct fman *f) { (void)f; return &fman_dev; }
static void put_device(struct device *d) { d->refs--; }
static void xa_destroy(struct xarray *xa) { (void)xa; }
static void *xa_erase(struct xarray *xa, unsigned long idx) { (void)xa; (void)idx; return NULL; }
static void kmem_cache_free(struct kmem_cache *c, void *p) { (void)c; free(p); }
static void kmem_cache_destroy(struct kmem_cache *c) { (void)c; }
static void kfree(void *p) { free(p); }
static u8 nla_get_u8(struct nlattr *attr) { return attr->value; }
static void rtnl_lock(void) { assert(!rtnl_held); rtnl_held = true; }
static void rtnl_unlock(void) { assert(rtnl_held); rtnl_held = false; }
static struct net_device *ask_hw_port_netdev(u8 port)
{
	assert(rtnl_held && port == 0x10);
	return NULL;
}
static enum fman_pcd_fe_profile ask_bridge_port_profile(struct net_device *dev)
{
	(void)dev;
	return FMAN_PCD_FE_PROFILE_L2_DA;
}
static void ask_hw_prewarm_egress_fq(u8 port)
{
	assert(!rtnl_held && port == 0x10);
	prewarms++;
}
static void ask_bridge_kick(void) { bridge_kicks++; }
/* No cookies survive to teardown in these scenarios. */
#define xa_for_each(xa, idx, entry) \
	for ((idx) = 0, (entry) = NULL; (void)(xa), (entry); )

#include "ask_hw_disengage_body.h"
#include "ask_genl_engage_body.h"

static struct ask_hw_pcd *new_pcd(void)
{
	struct ask_hw_pcd *h = calloc(1, sizeof(*h));

	assert(h);
	h->fman = &fm;
	h->port[0].in_use = true;
	h->port[0].port_id = 0x10;
	h->port[0].engaged_profile = FMAN_PCD_FE_PROFILE_ROUTED;
	ask_hw_pcd_inst = h;
	return h;
}

static void reset(void)
{
	disengage_rc = reserve_rc = engage_rc = punt_rc = vlan_rc = 0;
	disengages = engages = forgets = flushes = 0;
	punt_calls = vlan_teardowns = prepare_calls = 0;
	module_refs = 0;
	prewarms = bridge_kicks = 0;
	rtnl_held = false;
	allow_module_get = ask_hw_ready = true;
	check_forget_race = expect_unpublished = false;
	expect_disengage_unpublished = false;
	check_flush_family = false;
	expected_flush_family = 0;
	families[0x10] = 0;
	ask_hw_family_seq = 1;
	memset(ask_hw_family_changed, 0, sizeof(ask_hw_family_changed));
	ask_hw_family_seq_exhausted = false;
	ask_hw_port_l2[0] = 0;
	fman_dev.refs = 1;
}

static void discard_retained_fixture(struct ask_hw_pcd *h)
{
	assert(ask_hw_pcd_inst == h && module_refs == 1);
	ask_hw_pcd_inst = NULL;
	free(h);
}

int main(void)
{
	struct ask_hw_pcd *h;
	struct nlattr port = { .value = 0x10 };
	struct nlattr family = { .value = ASK_FAM_V4 };
	struct genl_info info = { .attrs = { &port, &family } };

	/* Init and module-unload windows never enter the FMan API. */
	reset();
	h = new_pcd();
	ask_hw_ready = false;
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					     false, ASK_FAM_V4) == -EAGAIN);
	ask_hw_ready = true;
	allow_module_get = false;
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					     false, ASK_FAM_V4) == -ENODEV);
	assert(!engages && !module_refs && !families[0x10]);
	allow_module_get = true;

	/* Publish the requested family only after successful hardware engage. */
	expect_unpublished = true;
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					     false, ASK_FAM_V4) == 0);
	expect_unpublished = false;
	assert(h->port[0].offload_engaged && h->port[0].module_pinned);
	assert(engages == 1 && module_refs == 1 && families[0x10] == ASK_FAM_V4);
	check_flush_family = true;
	expected_flush_family = ASK_FAM_V6;
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					     false, ASK_FAM_V6) == 0);
	assert(engages == 1 && module_refs == 1 && families[0x10] == ASK_FAM_V6 &&
	       flushes == 1);
	ask_hw_offload_set_family(0x10, ASK_FAM_V6);
	assert(flushes == 1);
	expected_flush_family = ASK_FAM_V4;
	ask_hw_offload_set_family(0x10, ASK_FAM_V4);
	assert(families[0x10] == ASK_FAM_V4 && flushes == 2);

	/* A busy FE owner must leave every dependent ASK state unchanged. */
	h->port[0].punt_mask = 0x5;
	ask_hw_port_l2[0] = 1UL << 0x10;
	reserve_rc = -EBUSY;
	{
		int old_punts = punt_calls;
		int old_flushes = flushes;

		assert(ask_hw_offload_disengage(0x10) == -EBUSY);
		assert(h->port[0].offload_engaged && !h->port[0].fe_retained);
		assert(h->port[0].module_pinned && module_refs == 1);
		assert(families[0x10] == ASK_FAM_V4 && h->port[0].punt_mask == 0x5);
		assert(ask_hw_port_l2[0] == (1UL << 0x10));
		assert(punt_calls == old_punts && !vlan_teardowns && !prepare_calls);
		assert(!disengages && !forgets && flushes == old_flushes);
	}
	reserve_rc = 0;

	/* Re-engage and public family updates are fenced through bridge cleanup. */
	ask_hw_port_l2[0] = 1UL << 0x10;
	check_forget_race = true;
	expected_flush_family = 0;
	assert(ask_hw_offload_disengage(0x10) == 0);
	assert(!h->port[0].offload_engaged && !h->port[0].fe_retained);
	assert(forgets == 1 && flushes == 4 && !ask_hw_port_l2[0] && !module_refs);
	assert(!h->port[0].teardown_in_progress && !h->port[0].module_pinned);
	ask_hw_pcd_teardown();
	assert(fman_dev.refs == 0 && !ask_hw_pcd_inst);

	/* An engage error may leave hardware state: retain the module and stop
	 * both new arms and teardown, without pretending rollback succeeded. */
	reset();
	h = new_pcd();
	engage_rc = -ETIMEDOUT;
	expect_unpublished = true;
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					     false, ASK_FAM_V4) == -ETIMEDOUT);
	expect_unpublished = false;
	assert(h->port[0].fe_retained && h->port[0].module_pinned);
	assert(!h->port[0].offload_engaged && module_refs == 1);
	assert(!families[0x10]);
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					     false, ASK_FAM_V6) == -ESHUTDOWN);
	assert(ask_hw_offload_engage_role(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					  true, ASK_FAM_V6) == -ESHUTDOWN);
	ask_hw_offload_set_family(0x10, ASK_FAM_V6);
	assert(!families[0x10]);
	assert(ask_hw_offload_disengage(0x10) == -ESHUTDOWN);
	ask_hw_pcd_teardown();
	assert(!disengages && ask_hw_pcd_inst == h && fman_dev.refs == 1);
	discard_retained_fixture(h);

	/* A failed disengage must retain owners and resources; family removal
	 * still queues asynchronous PPPoE withdrawal. */
	reset();
	h = new_pcd();
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					     false, 0) == 0);
	disengage_rc = -ETIMEDOUT;
	assert(ask_hw_offload_disengage(0x10) == -ETIMEDOUT);
	assert(!h->port[0].offload_engaged && h->port[0].fe_retained);
	assert(!forgets && flushes == 1 && module_refs == 1 && !h->lock.held);
	assert(ask_hw_offload_disengage(0x10) == -ESHUTDOWN);
	assert(disengages == 1);
	ask_hw_pcd_teardown();
	assert(ask_hw_pcd_inst == h && fman_dev.refs == 1);
	assert(ask_hw_offload_disengage(0x11) == 0);
	discard_retained_fixture(h);

	/* Only a busy result before preparation is retryable. */
	reset();
	h = new_pcd();
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					     false, ASK_FAM_V4) == 0);
	disengage_rc = -EBUSY;
	assert(ask_hw_offload_disengage(0x10) == -EBUSY);
	assert(prepare_calls == 1 && disengages == 1 && vlan_teardowns == 1);
	assert(!families[0x10] && !h->port[0].offload_engaged);
	assert(h->port[0].fe_retained && module_refs == 1);
	assert(!forgets && flushes == 1);
	discard_retained_fixture(h);

	/* An uncertain preflight fault is fenced even without a callback. */
	reset();
	h = new_pcd();
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					     false, ASK_FAM_V4) == 0);
	reserve_rc = -ESHUTDOWN;
	assert(ask_hw_offload_disengage(0x10) == -ESHUTDOWN);
	assert(!prepare_calls && !disengages && !vlan_teardowns);
	assert(!families[0x10] && h->port[0].fe_retained && module_refs == 1 &&
	       flushes == 1);
	discard_retained_fixture(h);

	/* A failed punt detach must not proceed into CC or FE teardown. */
	reset();
	h = new_pcd();
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					     false, ASK_FAM_V4) == 0);
	punt_rc = -ETIMEDOUT;
	assert(ask_hw_offload_disengage(0x10) == -ETIMEDOUT);
	assert(prepare_calls == 1 && !disengages && !vlan_teardowns);
	assert(!families[0x10] && h->port[0].fe_retained && module_refs == 1);
	assert(!forgets && flushes == 1);
	discard_retained_fixture(h);

	/* Legacy CC owners remain pinned if their teardown cannot be proved. */
	reset();
	h = new_pcd();
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_L2_DA,
					     false, ASK_FAM_V4) == 0);
	assert(ask_hw_port_l2[0] == (1UL << 0x10));
	vlan_rc = -EOPNOTSUPP;
	assert(ask_hw_offload_disengage(0x10) == -EOPNOTSUPP);
	assert(prepare_calls == 1 && vlan_teardowns == 1 && !disengages);
	assert(!families[0x10] && h->port[0].fe_retained && module_refs == 1);
	assert(ask_hw_port_l2[0] == (1UL << 0x10));
	assert(!forgets && flushes == 1);
	assert(ask_hw_offload_disengage(0x10) == -ESHUTDOWN);
	ask_hw_pcd_teardown();
	assert(ask_hw_pcd_inst == h && fman_dev.refs == 1);
	discard_retained_fixture(h);

	/* Netlink failures never publish the requested family, including a
	 * subsequent attempt against a retained port. */
	reset();
	h = new_pcd();
	engage_rc = -ETIMEDOUT;
	expect_unpublished = true;
	assert(ask_genl_engage_doit(NULL, &info) == -ETIMEDOUT);
	expect_unpublished = false;
	assert(!families[0x10] && !rtnl_held && !prewarms && !bridge_kicks);
	family.value = ASK_FAM_V6;
	assert(ask_genl_engage_doit(NULL, &info) == -ESHUTDOWN);
	assert(!families[0x10] && !prewarms && !bridge_kicks);
	discard_retained_fixture(h);

	/* A valid netlink request publishes the exact family after arm. */
	reset();
	h = new_pcd();
	expect_unpublished = true;
	assert(ask_genl_engage_doit(NULL, &info) == 0);
	expect_unpublished = false;
	assert(families[0x10] == ASK_FAM_V6 && prewarms == 1 && bridge_kicks == 1);
	assert(ask_hw_offload_disengage(0x10) == 0);
	ask_hw_pcd_teardown();

	reset();
	h = new_pcd();
	h->port[0].teardown_in_progress = true;
	assert(ask_genl_engage_doit(NULL, &info) == -EAGAIN);
	assert(!families[0x10] && !prewarms && !bridge_kicks);
	h->port[0].teardown_in_progress = false;
	ask_hw_pcd_teardown();

	/* Re-engage closes admission throughout the disarm/re-arm window. */
	reset();
	h = new_pcd();
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					     false, ASK_FAM_V4) == 0);
	reserve_rc = -EBUSY;
	assert(ask_hw_fe_reengage(0x10) == -EBUSY);
	assert(families[0x10] == ASK_FAM_V4 && h->port[0].offload_engaged);
	assert(!h->port[0].fe_retained && !disengages && !prepare_calls);
	reserve_rc = 0;
	expect_disengage_unpublished = expect_unpublished = true;
	assert(ask_hw_fe_reengage(0x10) == 0);
	expect_disengage_unpublished = expect_unpublished = false;
	assert(families[0x10] == ASK_FAM_V4 && h->port[0].offload_engaged &&
	       flushes == 1);
	assert(ask_hw_offload_disengage(0x10) == 0);
	ask_hw_pcd_teardown();

	/* VLAN's re-engage path must stop immediately on teardown failure. */
	reset();
	h = new_pcd();
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					     false, 0) == 0);
	disengage_rc = -ETIMEDOUT;
	expect_disengage_unpublished = true;
	assert(ask_hw_fe_reengage(0x10) == -ETIMEDOUT);
	assert(!h->port[0].offload_engaged && h->port[0].fe_retained);
	assert(engages == 1 && module_refs == 1 && !families[0x10]);
	discard_retained_fixture(h);

	/* A failed second arm after a clean disarm is retained too. */
	reset();
	h = new_pcd();
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					     false, 0) == 0);
	engage_rc = -EIO;
	expect_disengage_unpublished = expect_unpublished = true;
	assert(ask_hw_fe_reengage(0x10) == -EIO);
	assert(engages == 2 && h->port[0].fe_retained && module_refs == 1 &&
	       !families[0x10]);
	discard_retained_fixture(h);

	/* Saturation is reported before changing the requested family or FE. */
	reset();
	h = new_pcd();
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					     false, ASK_FAM_V4) == 0);
	ask_hw_family_seq = U64_MAX;
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					     false, ASK_FAM_V6) == -EOVERFLOW);
	assert(families[0x10] == ASK_FAM_V4 && engages == 1);
	assert(ask_hw_fe_reengage(0x10) == -EOVERFLOW && !disengages);
	ask_hw_offload_set_family(0x10, 0);
	assert(ask_hw_family_seq_exhausted);
	assert(ask_hw_offload_engage_profile(0x10, FMAN_PCD_FE_PROFILE_ROUTED,
					     false, ASK_FAM_V4) == -EOVERFLOW);
	discard_retained_fixture(h);

	puts("OK: ASK pins FE owners and fences failed or concurrent teardown");
	return 0;
}
