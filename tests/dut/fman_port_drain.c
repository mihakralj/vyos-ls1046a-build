/* Host tests for the F-310 release-time FE port drain (fail closed). */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

typedef uint8_t u8;
#define pr_err(...) ((void)0)

struct list_head { int unused; };
struct fman_pcd_fe_port {
	struct list_head node;
	u8 port_id;
	bool deleted;
};
struct fman_pcd {
	struct list_head fe_ports;
	unsigned long fe_port_armed[1], fe_port_faulted[1];
};

#define NPORTS 3
static struct fman_pcd_fe_port ports[NPORTS];
static unsigned int nports, dels;
static int del_result;

/* Walk the live entries in order; deleted ones are skipped like list_del(). */
#define list_for_each_entry_safe(pos, n, head, member) \
	for (unsigned int i_ = 0; (void)(head), (void)(n), i_ < nports; i_++) \
		if (((pos) = &ports[i_])->deleted) {} else

static bool test_bit(unsigned int bit, unsigned long *map)
{
	return !!(*map & (1UL << (bit & 63)));
}

static int fman_pcd_fe_port_del(struct fman_pcd *pcd, u8 port_id)
{
	(void)pcd;
	dels++;
	if (del_result)
		return del_result;
	for (unsigned int i = 0; i < nports; i++)
		if (ports[i].port_id == port_id)
			ports[i].deleted = true;
	return 0;
}

#include "port_drain_body.h"

static void reset(struct fman_pcd *pcd)
{
	*pcd = (struct fman_pcd) { 0 };
	nports = NPORTS;
	for (unsigned int i = 0; i < NPORTS; i++)
		ports[i] = (struct fman_pcd_fe_port) { .port_id = 0x10 + i };
	dels = 0;
	del_result = 0;
}

int main(void)
{
	struct fman_pcd pcd;

	/* Empty list: nothing to do. */
	reset(&pcd);
	nports = 0;
	assert(!fman_pcd_fe_port_drain(&pcd) && !dels);

	/* Disarmed, unfaulted entries go through the checked port delete. */
	reset(&pcd);
	assert(!fman_pcd_fe_port_drain(&pcd));
	assert(dels == NPORTS);
	for (unsigned int i = 0; i < NPORTS; i++)
		assert(ports[i].deleted);

	/* An armed or faulted port stops the drain before any of its MURAM is
	 * touched; ports before it were deleted through the checked path. */
	for (int faulted = 0; faulted < 2; faulted++) {
		reset(&pcd);
		if (faulted)
			pcd.fe_port_faulted[0] |= 1UL << 0x11;
		else
			pcd.fe_port_armed[0] |= 1UL << 0x11;
		assert(fman_pcd_fe_port_drain(&pcd) == -EBUSY);
		assert(dels == 1 && ports[0].deleted);
		assert(!ports[1].deleted && !ports[2].deleted);
	}

	/* A failed port delete (e.g. teardown SYNC timeout) is returned. */
	reset(&pcd);
	del_result = -ETIMEDOUT;
	assert(fman_pcd_fe_port_drain(&pcd) == -ETIMEDOUT);
	assert(dels == 1 && !ports[0].deleted);

	puts("FE port drain: armed/faulted ports and failed deletes retain state at release");
	return 0;
}
