// SPDX-License-Identifier: GPL-2.0
/*
 * Host fault injection for F-289: a failed FE disengage must report its
 * error and keep the port's FE state, and engage must not re-arm after
 * failed teardown or reuse a retained pre-handoff arming table.
 */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

typedef uint8_t u8;
#define EXPORT_SYMBOL_GPL(sym)
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define pr_info(...) ((void)0)
#define pr_warn(...) ((void)0)
#define pr_err(...) ((void)0)

enum { FMAN_PCD_FE_PROFILE_ROUTED, FMAN_PCD_FE_PROFILE_L2 };
struct list_head { struct list_head *next, *prev; };
struct fman_pcd_fe_port { int unused; };
struct mutex { bool locked; };
struct fman_pcd {
	struct mutex fe_lock;
	unsigned long fe_port_armed[1];
	unsigned long fe_port_faulted[1];
	u8 fe_port_profile[0x28];
	struct list_head fe_ports;
	void *fe_arming_v4_table;
	int fe_refcount;
};
struct fman { struct fman_pcd *pcd; };

#ifdef FMAN_TEST_LIFECYCLE_LOCK
static void mutex_lock(struct mutex *lock)
{
	assert(!lock->locked);
	lock->locked = true;
}

static void mutex_unlock(struct mutex *lock)
{
	assert(lock->locked);
	lock->locked = false;
}
#endif

static unsigned int fe_disengage_retained;
static struct fman_pcd_fe_port port_entry;
static bool port_present;	/* fp on pcd->fe_ports */
static int drain_error;		/* result of the per-port ehash drain */
static unsigned int drains;

static bool test_bit(unsigned int nr, const unsigned long *map)
{
	return map[0] & (1UL << nr);
}

static void clear_bit(unsigned int nr, unsigned long *map)
{
	map[0] &= ~(1UL << nr);
}

static bool list_empty(const struct list_head *head)
{
	(void)head;
	return !port_present;
}

static struct fman_pcd *fman_get_pcd(struct fman *fm)
{
	return fm->pcd;
}

static struct fman_pcd_fe_port *fman_pcd_fe_port_find(struct fman_pcd *pcd,
						      u8 port)
{
	(void)pcd;
	assert(port == 0x10);
	return port_present ? &port_entry : NULL;
}

/* Model of F-269 fman_pcd_fe_port_del(): keeps fp on drain failure. */
static int fman_pcd_fe_port_del(struct fman_pcd *pcd, u8 port)
{
	(void)pcd;
	assert(port == 0x10);
	if (!port_present)
		return -ENOENT;
	drains++;
	if (drain_error)
		return drain_error;
	port_present = false;
	return 0;
}

/* Model of __fman_pcd_fe_arm_disengage(): disarm first, then delete. */
static int __fman_pcd_fe_arm_disengage(struct fman_pcd *pcd, unsigned int port)
{
	int err;

#ifdef FMAN_TEST_LIFECYCLE_LOCK
	assert(pcd->fe_lock.locked);
#endif
	clear_bit(port, pcd->fe_port_armed);
	err = fman_pcd_fe_port_del(pcd, (u8)port);
	return err && err != -ENOENT ? err : 0;
}

#include "fman_retained_body.h"

static void engage(struct fman_pcd *pcd)
{
	pcd->fe_port_armed[0] |= 1UL << 0x10;
	port_present = true;
	pcd->fe_port_profile[0x10] = FMAN_PCD_FE_PROFILE_L2;
}

int main(void)
{
	struct fman_pcd pcd = { .fe_refcount = 1 };
	struct fman fm = { .pcd = &pcd };

	/* Normal disengage: drained, profile reset, nothing retained. */
	engage(&pcd);
	assert(fman_pcd_fe_disengage(&fm, 0x10) == 0);
	assert(!pcd.fe_lock.locked);
	assert(!port_present && !test_bit(0x10, pcd.fe_port_armed));
	assert(pcd.fe_port_profile[0x10] == FMAN_PCD_FE_PROFILE_ROUTED);
	assert(fe_disengage_retained == 0);

	/* Drain timeout: error reported, port disarmed, state kept. */
	engage(&pcd);
	drain_error = -ETIMEDOUT;
	assert(fman_pcd_fe_disengage(&fm, 0x10) == -ETIMEDOUT);
	assert(!pcd.fe_lock.locked);
	assert(port_present && !test_bit(0x10, pcd.fe_port_armed));
	assert(pcd.fe_port_profile[0x10] == FMAN_PCD_FE_PROFILE_L2);
	assert(fe_disengage_retained == 1);

	/* Engage never retries an unresolved drain. -ESHUTDOWN is not
	 * -EBUSY, which ask.ko can map to "already armed". */
	drains = 0;
	assert(fman_pcd_fe_retained_check(&pcd, 0x10) == -ESHUTDOWN);
	assert(drains == 0 && port_present);

	/* An armed port with no pending handoff is never torn down by the gate. */
	pcd.fe_port_armed[0] |= 1UL << 0x10;
	assert(fman_pcd_fe_retained_check(&pcd, 0x10) == 0);
	assert(drains == 0 && port_present);
	clear_bit(0x10, pcd.fe_port_armed);

	/* A pre-handoff table blocks all arms, even without a port entry or
	 * when this port's armed bit is set. */
	port_present = false;
	pcd.fe_arming_v4_table = &pcd;
	assert(fman_pcd_fe_retained_check(&pcd, 0x10) == -ESHUTDOWN);
	pcd.fe_port_armed[0] |= 1UL << 0x10;
	assert(fman_pcd_fe_retained_check(&pcd, 0x10) == -ESHUTDOWN);
	pcd.fe_arming_v4_table = NULL;
	clear_bit(0x10, pcd.fe_port_armed);
	assert(fman_pcd_fe_retained_check(&pcd, 0x10) == 0);
	assert(drains == 0);

#ifdef FMAN_TEST_PORT_FAULT
	/* A failed programming operation is fenced even without a port entry. */
	pcd.fe_port_faulted[0] = 1UL << 0x10;
	assert(fman_pcd_fe_retained_check(&pcd, 0x10) == -ESHUTDOWN);
	pcd.fe_port_faulted[0] = 0;
#endif

	/* Argument checks never count as retained. */
	assert(fman_pcd_fe_disengage(&fm, 0x07) == -EINVAL);
	assert(fman_pcd_fe_disengage(&fm, 0x28) == -EINVAL);
	assert(fman_pcd_fe_disengage(NULL, 0x10) == -EINVAL);
	assert(fe_disengage_retained == 1);

	puts("OK: retained port and pre-handoff table prevent FE re-engagement");
	return 0;
}
