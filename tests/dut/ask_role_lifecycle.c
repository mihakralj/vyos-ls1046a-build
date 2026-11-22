// SPDX-License-Identifier: GPL-2.0
/*
 * Host harness for ASK port-role lifecycle: admin disengage and bridge
 * join/leave reconciliation. The role functions are extracted verbatim from
 * ask_hw.c / ask_bridge.c by tests/ask_role_lifecycle.py.
 */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint64_t u64;

#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define READ_ONCE(x) (x)
#define ASSERT_RTNL() ((void)0)
#define mutex_lock(l) ((void)(l))
#define mutex_unlock(l) ((void)(l))
#define ask_pr_info(...) ((void)0)
static int err_logs;
#define ask_pr_err(...) (err_logs++)
#define pr_err_ratelimited(...) (err_logs++)

#define ASK_FAM_V4 1
#define ASK_FAM_V6 2
#define ASK_HW_MAX_PORTS 4
#define NOTIFY_DONE 0
#define NOTIFY_OK 1
#define NETDEV_PRECHANGEUPPER 1
#define NETDEV_CHANGEUPPER 2
#define notifier_from_errno(e) (0x8000 | -(e))

enum fman_pcd_fe_profile {
	FMAN_PCD_FE_PROFILE_ROUTED,
	FMAN_PCD_FE_PROFILE_L2_DA,
	FMAN_PCD_FE_PROFILE_NR,
};

struct ask_hw_port {
	bool in_use, offload_engaged, fe_retained, teardown_in_progress;
	u8 port_id;
	enum fman_pcd_fe_profile engaged_profile;
};
struct ask_hw_pcd {
	int lock;
	struct ask_hw_port port[ASK_HW_MAX_PORTS];
};

static struct ask_hw_pcd pcd;
static bool ask_hw_ready = true;
static u8 fam[256];
static u64 ask_hw_role_policy_seq[256];
static bool ask_hw_role_admin_disabled[256];
static int quiesce_calls, quiesce_rc, bridge_quiesce_rc, disengage_rc;

static struct ask_hw_pcd *ask_hw_pcd_get(void) { return &pcd; }
static u8 ask_hw_port_family_get(u8 p) { return fam[p]; }
static void ask_hw_set_family_locked(u8 p, u8 f) { fam[p] = f; }
static void ask_hw_role_restore_family(struct ask_hw_pcd *h, u8 p, u8 f,
				       u64 seq) { }

static struct ask_hw_port *slot(u8 p)
{
	for (int i = 0; i < ASK_HW_MAX_PORTS; i++)
		if (pcd.port[i].in_use && pcd.port[i].port_id == p)
			return &pcd.port[i];
	return NULL;
}

static int ask_hw_offload_quiesce_port_owners(u8 p)
{
	quiesce_calls++;	/* the real one also runs ask_bridge_port_quiesce() */
	return bridge_quiesce_rc ? bridge_quiesce_rc : quiesce_rc;
}

static int ask_hw_offload_disengage(u8 p)
{
	struct ask_hw_port *s = slot(p);

	fam[p] = 0;
	if (!s || !s->offload_engaged)
		return 0;
	if (disengage_rc)
		return disengage_rc;
	s->offload_engaged = false;
	return 0;
}

/* Mirrors the policy/admin gates of the real engage path. */
static int ask_hw_offload_engage_profile(u8 p, enum fman_pcd_fe_profile prof,
					 bool from_flow, u8 f, u64 seq)
{
	struct ask_hw_port *s = slot(p);

	if (seq && ask_hw_role_admin_disabled[p])
		return -EACCES;
	if (seq && seq != ask_hw_role_policy_seq[p])
		return -ESTALE;
	if (!s)
		return -ENOSPC;
	if (s->fe_retained)
		return -ESHUTDOWN;
	if (s->offload_engaged && s->engaged_profile != prof)
		return -EBUSY;
	s->offload_engaged = true;
	s->engaged_profile = prof;
	fam[p] = f;
	return 0;
}

#include "role_hw.h"
#include "role_admin.h"

/* ---- bridge side ---- */
struct net_device {
	int ifindex;
	const char *name;
	bool is_bridge;
	struct net_device *master;
	u8 port;
};
struct netdev_notifier_info { struct net_device *dev; };
struct netdev_notifier_changeupper_info {
	struct netdev_notifier_info info;
	struct net_device *upper_dev;
	bool linking;
};
struct notifier_block { int dummy; };

static struct net_device br = { .ifindex = 10, .name = "br0", .is_bridge = true };
static struct net_device eth = { .ifindex = 5, .name = "eth3", .port = 0x10 };
static u64 ask_bridge_role_generation_dummy;

static bool netif_is_bridge_master(struct net_device *d) { return d->is_bridge; }
static int ask_hw_dev_port(struct net_device *d, u8 *p)
{
	if (d->is_bridge)
		return -ENODEV;
	*p = d->port;
	return 0;
}
static enum fman_pcd_fe_profile ask_bridge_profile_for(struct net_device *b)
{
	return b ? FMAN_PCD_FE_PROFILE_L2_DA : FMAN_PCD_FE_PROFILE_ROUTED;
}
static enum fman_pcd_fe_profile ask_bridge_port_profile(struct net_device *d)
{
	return ask_bridge_profile_for(d->master);
}
static int ask_bridge_port_quiesce(u8 p) { return bridge_quiesce_rc; }
static void ask_bridge_kick(void) { }
static const char *netdev_name(struct net_device *d) { return d->name; }
static struct net_device *netdev_notifier_info_to_dev(void *ptr)
{
	return ((struct netdev_notifier_info *)ptr)->dev;
}
static struct net_device *__dev_get_by_index(void *net, int ifindex)
{
	return ifindex == eth.ifindex ? &eth : NULL;
}
static int init_net;

#include "role_bridge_state.h"
#include "role_bridge.h"

static void reset(void)
{
	memset(&pcd, 0, sizeof(pcd));
	memset(fam, 0, sizeof(fam));
	memset(ask_hw_role_policy_seq, 0, sizeof(ask_hw_role_policy_seq));
	memset(ask_hw_role_admin_disabled, 0, sizeof(ask_hw_role_admin_disabled));
	memset(ask_bridge_role, 0, sizeof(ask_bridge_role));
	quiesce_calls = quiesce_rc = bridge_quiesce_rc = disengage_rc = 0;
	err_logs = 0;
	eth.master = NULL;
	ask_hw_ready = true;
	(void)ask_bridge_role_generation_dummy;
}

static void engage(u8 p, enum fman_pcd_fe_profile prof)
{
	pcd.port[0] = (struct ask_hw_port){ .in_use = true, .port_id = p,
		.offload_engaged = true, .engaged_profile = prof };
	fam[p] = ASK_FAM_V4 | ASK_FAM_V6;
}

static int event(unsigned long ev, bool linking)
{
	struct netdev_notifier_changeupper_info info = {
		.info.dev = &eth, .upper_dev = &br, .linking = linking,
	};

	if (ev == NETDEV_CHANGEUPPER)
		eth.master = linking ? &br : NULL;
	return ask_bridge_netdev_notifier(NULL, ev, &info);
}

/* #1: a port VyOS admin-disabled (no offload leaves) joins a bridge. */
static void test_admin_disabled_join_not_vetoed(void)
{
	reset();
	assert(ask_hw_offload_disengage_admin(0x10) == 0);
	assert(event(NETDEV_PRECHANGEUPPER, true) == NOTIFY_OK);
	assert(event(NETDEV_PRECHANGEUPPER, false) == NOTIFY_OK);
	ask_hw_ready = false;	/* not ready: still no veto for an idle port */
	assert(event(NETDEV_PRECHANGEUPPER, true) == NOTIFY_OK);
	assert(!ask_bridge_role[0x10].pending);
}

/* #2: DISENGAGE of a never-engaged port must not withdraw anything. */
static void test_admin_disengage_idle_port_skips_quiesce(void)
{
	reset();
	quiesce_rc = -EBUSY;	/* e.g. an FE orphan on another port */
	assert(ask_hw_offload_disengage_admin(0x0d) == 0);
	assert(quiesce_calls == 0);
	assert(ask_hw_role_admin_disabled[0x0d]);

	reset();		/* an engaged port still quiesces first */
	engage(0x10, FMAN_PCD_FE_PROFILE_ROUTED);
	quiesce_rc = -EBUSY;
	assert(ask_hw_offload_disengage_admin(0x10) == -EBUSY);
	assert(quiesce_calls == 1);
	assert(fam[0x10] == 0);
}

/* #3: a failed leave proceeds in the kernel; ASK must reconcile later. */
static void test_failed_leave_reconciles(void)
{
	reset();
	engage(0x10, FMAN_PCD_FE_PROFILE_L2_DA);
	eth.master = &br;
	bridge_quiesce_rc = -EBUSY;
	event(NETDEV_PRECHANGEUPPER, false);	/* result ignored by the kernel */
	event(NETDEV_CHANGEUPPER, false);
	assert(ask_bridge_role[0x10].pending);

	bridge_quiesce_rc = 0;
	ask_bridge_reconcile_roles();
	assert(!ask_bridge_role[0x10].pending);
	assert(slot(0x10)->offload_engaged);
	assert(slot(0x10)->engaged_profile == FMAN_PCD_FE_PROFILE_ROUTED);
	assert(fam[0x10] == (ASK_FAM_V4 | ASK_FAM_V6));
}

/* #3: a join vetoed after the family was closed must reopen it on transient
 * failure, but keep admission closed on terminal -ESTALE (identity_lost). */
static void test_vetoed_join_restores_family(void)
{
	reset();
	engage(0x10, FMAN_PCD_FE_PROFILE_ROUTED);
	quiesce_rc = -EBUSY;
	assert(event(NETDEV_PRECHANGEUPPER, true) != NOTIFY_OK);
	assert(fam[0x10] == 0);
	assert(ask_bridge_role[0x10].pending);

	quiesce_rc = 0;
	ask_bridge_reconcile_roles();	/* membership unchanged: ROUTED again */
	assert(!ask_bridge_role[0x10].pending);
	assert(slot(0x10)->engaged_profile == FMAN_PCD_FE_PROFILE_ROUTED);
	assert(fam[0x10] == (ASK_FAM_V4 | ASK_FAM_V6));

	/* Terminal -ESTALE during join prepare must NOT schedule reconcile to reopen fam. */
	reset();
	engage(0x10, FMAN_PCD_FE_PROFILE_ROUTED);
	quiesce_rc = -ESTALE;
	assert(event(NETDEV_PRECHANGEUPPER, true) != NOTIFY_OK);
	assert(fam[0x10] == 0);
	assert(!ask_bridge_role[0x10].pending);
	quiesce_rc = 0;
	ask_bridge_reconcile_roles();
	assert(fam[0x10] == 0);

	/* External notifier veto after PRECHANGEUPPER detached (no CHANGEUPPER):
	 * reconcile must restore original ROUTED profile and family. */
	reset();
	engage(0x10, FMAN_PCD_FE_PROFILE_ROUTED);
	assert(event(NETDEV_PRECHANGEUPPER, true) == NOTIFY_OK);
	assert(!slot(0x10)->offload_engaged && fam[0x10] == 0);
	assert(ask_bridge_role[0x10].pending && !ask_bridge_role[0x10].needs_prepare);
	ask_bridge_reconcile_roles();	/* eth.master still NULL */
	assert(!ask_bridge_role[0x10].pending);
	assert(slot(0x10)->offload_engaged);
	assert(slot(0x10)->engaged_profile == FMAN_PCD_FE_PROFILE_ROUTED);
	assert(fam[0x10] == (ASK_FAM_V4 | ASK_FAM_V6));

	/* Failed disengage_admin leaves port armed as ROUTED: bridge join must veto. */
	reset();
	engage(0x10, FMAN_PCD_FE_PROFILE_ROUTED);
	quiesce_rc = -EBUSY;
	assert(ask_hw_offload_disengage_admin(0x10) == -EBUSY);
	assert(slot(0x10)->offload_engaged && ask_hw_role_admin_disabled[0x10]);
	quiesce_rc = 0;
	assert(event(NETDEV_PRECHANGEUPPER, true) == notifier_from_errno(-EACCES));
	assert(!ask_bridge_role[0x10].pending);
}

/* #4: a commit made obsolete by newer policy is dropped, not retried. */
static void test_obsolete_reconcile_is_dropped(void)
{
	reset();
	engage(0x10, FMAN_PCD_FE_PROFILE_ROUTED);
	assert(event(NETDEV_PRECHANGEUPPER, true) == NOTIFY_OK);
	assert(ask_bridge_role[0x10].pending);
	++ask_hw_role_policy_seq[0x10];	/* operator genl ENGAGE in between */
	for (int i = 0; i < 100; i++)
		ask_bridge_reconcile_roles();
	assert(!ask_bridge_role[0x10].pending);
	assert(err_logs <= 1);
}

int main(void)
{
	test_admin_disabled_join_not_vetoed();
	test_admin_disengage_idle_port_skips_quiesce();
	test_failed_leave_reconciles();
	test_vetoed_join_restores_family();
	test_obsolete_reconcile_is_dropped();
	puts("ask_role_lifecycle: ok");
	return 0;
}
