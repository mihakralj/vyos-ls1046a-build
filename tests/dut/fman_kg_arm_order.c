/* Host tests for the generated F-311 FE engage order (RCCB before AC_CC). */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
#define FM_KG_MAX_NUM_OF_SCHEMES 32
#define pr_info(...) ((void)0)
#define pr_warn(...) ((void)0)
#define pr_err(...) ((void)0)
#define EXPORT_SYMBOL_GPL(x)

#define PORT 0x10
#define NODE 0x57100u
#define FQID 0x200u

struct mutex { bool held; };
struct keygen_scheme {
	bool used, ekfc_only;
	u8 hw_port_id;
	u32 next_engine, cc_base_offset, cc_bits_sel, match_vector, ekfc, base_fqid;
};
struct fman_keygen { struct keygen_scheme schemes[FM_KG_MAX_NUM_OF_SCHEMES]; };
struct fman_port { int unused; };
struct fman { struct fman_keygen *keygen; struct fman_port *port; };
struct fman_pcd { struct fman *fman; struct mutex lock; };

/* Modelled silicon: the port's RCCB, CC_EN and each scheme's committed mode. */
static u32 rccb;
static bool cc_en;
static bool hw_accc[FM_KG_MAX_NUM_OF_SCHEMES];
static int steps, node_step, rccb_step, scheme_step, cc_clear_calls, setup_calls;
static int find_calls, vanish_on_find, fqid_change_on_find;
static bool fail_cc, fail_setup, setup_commits_before_fail, wants_v6;

/* The hazard: an AC_CC scheme on this port while RCCB is 0 dispatches every
 * frame into MURAM offset 0. Checked after every modelled hardware write. */
static void check_invariant(struct fman_keygen *kg)
{
	for (int i = 0; i < FM_KG_MAX_NUM_OF_SCHEMES; i++)
		if (kg->schemes[i].hw_port_id == PORT && hw_accc[i])
			assert(rccb != 0 && "AC_CC scheme live with RCCB 0");
}

static struct fman_keygen *model_kg;

static void mutex_lock(struct mutex *lock) { assert(!lock->held); lock->held = true; }
static void mutex_unlock(struct mutex *lock) { assert(lock->held); lock->held = false; }
static struct fman *fman_pcd_get_fman(struct fman_pcd *pcd) { return pcd->fman; }
static struct mutex *fman_pcd_get_lock(struct fman_pcd *pcd) { return &pcd->lock; }
static struct fman_port *fman_port_lookup_rx(struct fman *fm, u8 id) { return fm->port; }
static bool fman_pcd_fe_port_ekfc_only(struct fman_pcd *pcd, u8 port) { return true; }
static bool fman_pcd_port_wants_v6(struct fman_pcd *pcd, u8 port) { return wants_v6; }

static struct keygen_scheme *
kg_find_port_scheme(struct fman_keygen *kg, u8 port, u8 *id)
{
	find_calls++;
	if (vanish_on_find && find_calls == vanish_on_find)
		return NULL;
	for (int i = 0; i < FM_KG_MAX_NUM_OF_SCHEMES; i++)
		if (kg->schemes[i].used && kg->schemes[i].hw_port_id == port) {
			if (fqid_change_on_find && find_calls == fqid_change_on_find)
				kg->schemes[i].base_fqid++;
			*id = i;
			return &kg->schemes[i];
		}
	return NULL;
}

static int keygen_scheme_setup(struct fman_keygen *kg, u8 id, bool enable)
{
	assert(!(enable && kg->schemes[id].used));
	setup_calls++;
	if (!scheme_step)
		scheme_step = ++steps;
	if (fail_setup) {
		/* AR wait timeout: the commit may have landed anyway. */
		if (setup_commits_before_fail)
			hw_accc[id] = kg->schemes[id].next_engine == 3;
		check_invariant(kg);
		return -ETIMEDOUT;
	}
	hw_accc[id] = kg->schemes[id].next_engine == 3;
	kg->schemes[id].used = enable;
	check_invariant(kg);
	return 0;
}

static void fman_pcd_fe_node_set_miss_nia(struct fman_pcd *pcd, u32 node, u32 nia)
{
	assert(node == NODE && nia == FQID);
	node_step = ++steps;
}

static int fman_port_set_cc_base(struct fman_port *port, u32 base)
{
	rccb = base;		/* written before the SYNC, as in fman_port.c */
	if (base) {
		rccb_step = ++steps;
		if (fail_cc) {
			check_invariant(model_kg);
			return -ETIMEDOUT;
		}
		cc_en = true;
	} else {
		cc_clear_calls++;
		cc_en = false;
	}
	check_invariant(model_kg);
	return 0;
}

/* F-211 v6 block: compiled, exercised once for lock balance. */
static int fman_port_disable(struct fman_port *port) { return 0; }
static int fman_port_enable(struct fman_port *port) { return 0; }
static int keygen_bind_port_to_schemes(struct fman_keygen *kg, u8 id, bool bind) { return 0; }
static int keygen_cls_plan0_passall(struct fman_keygen *kg, u8 port) { return 0; }
static int fman_port_set_lcv_split(struct fman_port *port, u32 a, u32 b, u32 c) { return 0; }
static void fman_pcd_fe_v6node_set_miss_nia(struct fman_pcd *pcd, u32 node, u32 nia) { }

#include "kg_arm_full.h"

static struct fman_keygen kg;
static struct fman_port port;
static struct fman fm;
static struct fman_pcd pcd;

static void reset(void)
{
	memset(&kg, 0, sizeof(kg));
	kg.schemes[3] = (struct keygen_scheme) {
		.used = true, .hw_port_id = PORT, .next_engine = 2,
		.ekfc = 0x00180006, .base_fqid = FQID,
	};
	fm = (struct fman) { .keygen = &kg, .port = &port };
	pcd = (struct fman_pcd) { .fman = &fm };
	model_kg = &kg;
	rccb = 0;
	cc_en = false;
	memset(hw_accc, 0, sizeof(hw_accc));
	steps = node_step = rccb_step = scheme_step = cc_clear_calls = setup_calls = 0;
	find_calls = vanish_on_find = fqid_change_on_find = 0;
	fail_cc = fail_setup = setup_commits_before_fail = wants_v6 = false;
}

static int arm(bool *started, u8 *saved)
{
	*started = false;
	*saved = 0xff;
	return fman_pcd_kg_port_arm_fe(&pcd, PORT, NODE, saved, 0x801C0006, started);
}

int main(void)
{
	bool started;
	u8 saved;

	/* Success: node, then RCCB (+SYNC/CC_EN), then the AC_CC scheme. */
	reset();
	assert(!arm(&started, &saved));
	assert(started && !pcd.lock.held);
	assert(node_step && node_step < rccb_step && rccb_step < scheme_step);
	assert(rccb == NODE && cc_en && hw_accc[3] && !cc_clear_calls);
	assert(kg.schemes[3].used && kg.schemes[3].next_engine == 3 &&
	       kg.schemes[3].ekfc == 0x801C0006 && saved == 2);

	/* No RX port or no scheme: nothing touched, RX left alone. */
	reset();
	fm.port = NULL;
	assert(arm(&started, &saved) == -ENODEV);
	assert(!started && !steps && !pcd.lock.held && !rccb);
	reset();
	kg.schemes[3].used = false;
	assert(arm(&started, &saved) == -ENODEV);
	assert(!started && !steps && !pcd.lock.held && !rccb);

	/* First-port SYNC fails after the RCCB write: scheme never touched,
	 * RCCB cleared again. */
	reset();
	fail_cc = true;
	assert(arm(&started, &saved) == -ETIMEDOUT);
	assert(started && !setup_calls && !hw_accc[3] && !rccb && cc_clear_calls == 1);
	assert(kg.schemes[3].next_engine == 2 && kg.schemes[3].ekfc == 0x00180006);
	assert(!pcd.lock.held);

	/* Scheme vanished or its FQID changed after the miss FQID went into the
	 * node: refuse before AC_CC, RCCB cleared. */
	reset();
	vanish_on_find = 2;
	assert(arm(&started, &saved) == -ENODEV);
	assert(started && !setup_calls && !rccb && cc_clear_calls == 1 && !pcd.lock.held);
	reset();
	fqid_change_on_find = 2;
	assert(arm(&started, &saved) == -EBUSY);
	assert(started && !setup_calls && !rccb && cc_clear_calls == 1 && !pcd.lock.held);
	assert(kg.schemes[3].next_engine == 2);

	/* AC_CC scheme write fails, with or without the commit landing: RCCB
	 * stays at the node (fail closed), software slot restored. */
	for (int landed = 0; landed < 2; landed++) {
		reset();
		fail_setup = true;
		setup_commits_before_fail = landed;
		assert(arm(&started, &saved) == -ETIMEDOUT);
		assert(started && rccb == NODE && !cc_clear_calls && !pcd.lock.held);
		assert(kg.schemes[3].used && kg.schemes[3].next_engine == 2 &&
		       kg.schemes[3].ekfc == 0x00180006);
		assert(hw_accc[3] == landed);
	}

	/* v6 arm after the v4 scheme is live: lock balance and invariant. */
	reset();
	wants_v6 = true;
	assert(!arm(&started, &saved));
	assert(!pcd.lock.held && rccb == NODE && hw_accc[3]);

	puts("FE arm: F-311 RCCB+SYNC+CC_EN before AC_CC; failed scheme write keeps RCCB");
	return 0;
}
