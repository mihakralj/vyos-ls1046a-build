/* Host fault tests for generated F-291 FE and KeyGen disengagement. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
#define FM_KG_MAX_NUM_OF_SCHEMES 32
#define pr_info(...) ((void)0)
#define pr_warn(...) ((void)0)
#define pr_warn_ratelimited(...) ((void)0)
#define pr_err(...) ((void)0)

struct mutex { bool held; };
struct keygen_scheme {
	bool used;
	u8 hw_port_id;
	unsigned int cc_base_offset, next_engine, match_vector, cc_bits_sel, ekfc;
};
struct fman_keygen { struct keygen_scheme schemes[FM_KG_MAX_NUM_OF_SCHEMES]; };
struct fman_port { int unused; };
struct fman { struct fman_keygen *keygen; struct fman_port *port; };
struct list_head { bool populated; };
struct fman_pcd_ehash_table {
	struct list_head node;
	struct list_head flows, quarantined_flows, quarantined_cu;
	bool poisoned, draining;
};
struct fman_pcd_fe_port { struct fman_pcd_ehash_table *table_v4; };
struct fman_pcd {
	struct fman *fman;
	struct mutex lock;
	unsigned long fe_port_armed[1], fe_port_faulted[1];
	void *fe_arming_v4_table;
	struct list_head fe_ehash_tables;
	struct fman_pcd_ehash_table table, shared_table, other_table;
	struct fman_pcd_fe_port port;
	bool port_present;
};

static struct fman_pcd_ehash_table *tables[3];
static unsigned int table_count;
#define list_for_each_entry(pos, head, member) \
	for (unsigned int i = 0; \
	     (void)(head), i < table_count && ((pos) = tables[i], true); i++)

enum failure {
	NONE, BIND, WIDEN, DISABLE, LCV, CC_BASE, READBACK, DETACH,
	ADVANCED, TABLE, PORT, PORT_ABSENT, STOP_RX
};
static enum failure failure;
static int setup_calls, bind_calls, lcv_calls, cc_calls, verify_calls;
static int advanced_calls, table_calls, port_calls, released, disable_calls;

static void mutex_lock(struct mutex *lock) { assert(!lock->held); lock->held = true; }
static void mutex_unlock(struct mutex *lock) { assert(lock->held); lock->held = false; }
static struct fman *fman_pcd_get_fman(struct fman_pcd *pcd) { return pcd->fman; }
static struct mutex *fman_pcd_get_lock(struct fman_pcd *pcd) { return &pcd->lock; }
static struct fman_port *fman_port_lookup_rx(struct fman *fm, u8 id) { return fm->port; }
static int fman_port_disable(struct fman_port *port)
{
	disable_calls++;
	return failure == STOP_RX ? -EBUSY : 0;
}
static struct keygen_scheme *
kg_find_port_scheme(struct fman_keygen *kg, u8 port, u8 *id)
{
	int i;

	for (i = 0; i < FM_KG_MAX_NUM_OF_SCHEMES; i++)
		if (kg->schemes[i].used && kg->schemes[i].hw_port_id == port) {
			*id = i;
			return &kg->schemes[i];
		}
	return NULL;
}
static int keygen_bind_port_to_schemes(struct fman_keygen *kg, u8 id, bool bind)
{
	bind_calls++;
	return failure == BIND ? -EIO : 0;
}
static int keygen_scheme_setup(struct fman_keygen *kg, u8 id, bool enable)
{
	setup_calls++;
	if ((failure == WIDEN && setup_calls == 1) ||
	    (failure == DISABLE && setup_calls == 2) ||
	    (failure == DETACH && setup_calls == 3))
		return -EIO;
	kg->schemes[id].used = enable;
	return 0;
}
static int fman_port_clear_lcv_split(struct fman_port *port)
{
	lcv_calls++;
	return failure == LCV ? -ETIMEDOUT : 0;
}
static int fman_port_set_cc_base(struct fman_port *port, unsigned int base)
{
	cc_calls++;
	assert(!base);
	return failure == CC_BASE ? -EIO : 0;
}
static void fman_port_clear_kg_direct_scheme(struct fman_port *port) { }
static int fman_port_verify_fe_detached(struct fman_port *port)
{
	verify_calls++;
	return failure == READBACK ? -EIO : 0;
}
static bool test_bit(unsigned int bit, unsigned long *map) { return !!(*map & (1UL << bit)); }
static __attribute__((unused)) bool list_empty(struct list_head *head) { return !head->populated; }
static void set_bit(unsigned int bit, unsigned long *map) { *map |= 1UL << bit; }
static void clear_bit(unsigned int bit, unsigned long *map) { *map &= ~(1UL << bit); }
static void fsleep(unsigned long usec) { }
static __attribute__((unused)) struct fman_pcd_fe_port *
fman_pcd_fe_port_find(struct fman_pcd *pcd, u8 port)
{
	assert(port == 0x10);
	return pcd->port_present ? &pcd->port : NULL;
}
static int fman_pcd_fe_adv_offload(struct fman_pcd *pcd, u8 port, bool on)
{
	assert(!on);
	advanced_calls++;
	return failure == ADVANCED ? -EIO : 0;
}
static __attribute__((unused)) int
fman_pcd_ehash_table_free(struct fman_pcd *pcd, void *table)
{
	table_calls++;
	return failure == TABLE ? -EIO : 0;
}
static int fman_pcd_fe_port_del(struct fman_pcd *pcd, u8 port)
{
	port_calls++;
	if (failure == PORT_ABSENT)
		return -ENOENT;
	if (failure == TABLE || failure == PORT)
		return -EIO;
	released++;
	return 0;
}

#include "disarm_body.h"

static void reset(struct fman_keygen *kg, struct fman_port *port,
		  struct fman *fm, struct fman_pcd *pcd)
{
	memset(kg, 0, sizeof(*kg));
	kg->schemes[0] = (struct keygen_scheme) {
		.used = true, .hw_port_id = 0x10,
		.match_vector = 0x40000000U, .next_engine = 3, .ekfc = 5,
	};
	kg->schemes[1] = (struct keygen_scheme) {
		.used = true, .hw_port_id = 0x10,
		.cc_base_offset = 1, .next_engine = 3, .ekfc = 7,
	};
	*fm = (struct fman) { .keygen = kg, .port = port };
	*pcd = (struct fman_pcd) { .fman = fm, .port_present = true };
	pcd->port.table_v4 = &pcd->table;
	tables[0] = &pcd->table;
	tables[1] = &pcd->shared_table;
	tables[2] = &pcd->other_table;
	table_count = 3;
	set_bit(0x10, pcd->fe_port_armed);
	setup_calls = bind_calls = lcv_calls = cc_calls = verify_calls = 0;
	advanced_calls = table_calls = port_calls = released = disable_calls = 0;
}

int main(void)
{
	struct fman_keygen kg;
	struct fman_port port;
	struct fman fm;
	struct fman_pcd pcd;
	enum failure stage;

	reset(&kg, &port, &fm, &pcd);
	clear_bit(0x10, pcd.fe_port_armed);
	pcd.port_present = false;
	failure = NONE;
	assert(!__fman_pcd_fe_arm_disengage(&pcd, 0x10));
	assert(!setup_calls && !bind_calls && !cc_calls && !advanced_calls &&
	       !table_calls && !port_calls);

	reset(&kg, &port, &fm, &pcd);
	clear_bit(0x10, pcd.fe_port_armed);
	assert(__fman_pcd_fe_arm_disengage(&pcd, 0x10) == -ESHUTDOWN);
	assert(!setup_calls && !port_calls);

	reset(&kg, &port, &fm, &pcd);
	pcd.fe_arming_v4_table = &kg;
	assert(__fman_pcd_fe_arm_disengage(&pcd, 0x10) == -ESHUTDOWN);
	assert(test_bit(0x10, pcd.fe_port_armed) &&
	       pcd.fe_arming_v4_table == &kg);
	assert(!setup_calls && !port_calls && !table_calls);

	for (int table_missing = 0; table_missing < 2; table_missing++) {
		reset(&kg, &port, &fm, &pcd);
		if (table_missing)
			pcd.port.table_v4 = NULL;
		else
			pcd.port_present = false;
		assert(__fman_pcd_fe_arm_disengage(&pcd, 0x10) == -ESHUTDOWN);
		assert(test_bit(0x10, pcd.fe_port_faulted));
		assert(!setup_calls && !port_calls);
	}

	for (int busy_state = 0; busy_state < 5; busy_state++) {
		reset(&kg, &port, &fm, &pcd);
		switch (busy_state) {
		case 0: pcd.table.flows.populated = true; break;
		case 1: pcd.table.quarantined_flows.populated = true; break;
		case 2: pcd.table.quarantined_cu.populated = true; break;
		case 3: pcd.table.poisoned = true; break;
		case 4: pcd.table.draining = true; break;
		}
		failure = NONE;
		assert(__fman_pcd_fe_arm_disengage(&pcd, 0x10) == -EBUSY);
		assert(test_bit(0x10, pcd.fe_port_armed));
		assert(!test_bit(0x10, pcd.fe_port_faulted));
		assert(pcd.port.table_v4 == &pcd.table);
		assert(!setup_calls && !bind_calls && !cc_calls && !advanced_calls &&
		       !port_calls && !table_calls && !released);
	}

	/* A nonzero table index can refer to shared or another port's table;
	 * its records and uncertain generations have no ingress identity. */
	for (unsigned int index = 1; index < table_count; index++) {
		for (int busy_state = 0; busy_state < 5; busy_state++) {
			struct fman_pcd_ehash_table *t;

			reset(&kg, &port, &fm, &pcd);
			t = tables[index];
			switch (busy_state) {
			case 0: t->flows.populated = true; break;
			case 1: t->quarantined_flows.populated = true; break;
			case 2: t->quarantined_cu.populated = true; break;
			case 3: t->poisoned = true; break;
			case 4: t->draining = true; break;
			}
			failure = NONE;
			assert(__fman_pcd_fe_arm_disengage(&pcd, 0x10) == -EBUSY);
			assert(test_bit(0x10, pcd.fe_port_armed));
			assert(!test_bit(0x10, pcd.fe_port_faulted));
			assert(!setup_calls && !cc_calls && !port_calls && !released);
		}
	}

	reset(&kg, &port, &fm, &pcd);
	tables[0] = &pcd.shared_table;
	tables[1] = &pcd.other_table;
	table_count = 2;
	failure = NONE;
	assert(__fman_pcd_fe_arm_disengage(&pcd, 0x10) == -ESHUTDOWN);
	assert(test_bit(0x10, pcd.fe_port_armed));
	assert(test_bit(0x10, pcd.fe_port_faulted));
	assert(!setup_calls && !cc_calls && !port_calls && !released);

	for (stage = BIND; stage <= PORT_ABSENT; stage++) {
		int calls, rc;

		reset(&kg, &port, &fm, &pcd);
		failure = stage;
		rc = __fman_pcd_fe_arm_disengage(&pcd, 0x10);
		assert(rc < 0 && !released && test_bit(0x10, pcd.fe_port_faulted));
		assert(!test_bit(0x10, pcd.fe_port_armed));
		if (stage == WIDEN)
			assert(kg.schemes[0].used &&
			       kg.schemes[0].match_vector == 0x40000000U);
		if (stage == DISABLE)
			assert(kg.schemes[1].used && kg.schemes[1].cc_base_offset == 1);
		if (stage == DETACH)
			assert(kg.schemes[0].used && kg.schemes[0].next_engine == 3);
		calls = setup_calls + bind_calls + lcv_calls + cc_calls +
			verify_calls + advanced_calls + table_calls + port_calls;
		assert(__fman_pcd_fe_arm_disengage(&pcd, 0x10) == -ESHUTDOWN);
		assert(calls == setup_calls + bind_calls + lcv_calls + cc_calls +
			verify_calls + advanced_calls + table_calls + port_calls);
	}

	reset(&kg, &port, &fm, &pcd);
	failure = NONE;
	assert(!__fman_pcd_fe_arm_disengage(&pcd, 0x10));
	assert(released == 1 && !pcd.fe_arming_v4_table && !pcd.fe_port_faulted[0]);
	assert(kg.schemes[0].used && !kg.schemes[0].next_engine);
	assert(!kg.schemes[1].used);

	reset(&kg, &port, &fm, &pcd);
	fm.port = NULL;
	assert(__fman_pcd_fe_arm_disengage(&pcd, 0x10) == -ENODEV);
	assert(!setup_calls && !released && test_bit(0x10, pcd.fe_port_faulted));

	for (stage = NONE; stage <= STOP_RX; stage += STOP_RX) {
		reset(&kg, &port, &fm, &pcd);
		pcd.fe_arming_v4_table = &kg;
		failure = stage;
		fman_pcd_fe_fault_arm(&pcd, 0x10, true);
		assert(disable_calls == 1 && !released);
		assert(pcd.fe_arming_v4_table == &kg);
		assert(test_bit(0x10, pcd.fe_port_faulted));
	}
	reset(&kg, &port, &fm, &pcd);
	fm.port = NULL;
	fman_pcd_fe_fault_arm(&pcd, 0x10, true);
	assert(!disable_calls && !released && test_bit(0x10, pcd.fe_port_faulted));
	reset(&kg, &port, &fm, &pcd);
	fman_pcd_fe_fault_arm(&pcd, 0x10, false);
	assert(!disable_calls && !released && test_bit(0x10, pcd.fe_port_faulted));
	puts("FE disarm: lower-layer failures retain state and fence retries");
	return 0;
}
