/* Host fault tests for generated F-291 partial FE engagement. */
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;

#define FMAN_PCD_FE_PROFILE_ROUTED 0
#define FMAN_PCD_FE_PROFILE_NR 2
#define pr_info(...) ((void)0)
#define pr_warn(...) ((void)0)
#define pr_err(...) ((void)0)

enum fman_pcd_fe_profile { ROUTED, BRIDGE };
struct mutex { pthread_mutex_t native; };
struct fman_port { int unused; };
struct fman_pcd;
struct fman { struct fman_pcd *pcd; struct fman_port *port; };
struct fman_pcd {
	struct fman *fman;
	struct mutex fe_lock;
	unsigned long fe_port_armed[1], fe_port_faulted[1];
	enum fman_pcd_fe_profile fe_port_profile[0x28];
	bool fe_vm_chain_built, fe_vm_chain_faulted, fe_obs_armed;
	int fe_ehash_tables, fe_enq, fe_refcount;
	u32 fe_enq_fqid;
	void *fe_arming_v4_table, *port_table;
};
static const struct { u32 ekfc; unsigned int key_size; }
fman_pcd_fe_profiles[FMAN_PCD_FE_PROFILE_NR] = {
	{ 1, 14 }, { 2, 14 },
};

enum failure { NONE, PARAMS, CHAIN_BEFORE_TABLE, CHAIN_AFTER_TABLE,
	       WORKSPACE, ADVANCED };
static enum failure failure;
static bool stop_fails, lose_port;
static int owner, stop_calls, arm_calls, workspace_calls, advanced_calls;
static int params_calls, chain_calls;
static struct fman_pcd *active_pcd;
static bool mutex_initialized, pause_arm, arm_entered, resume_arm;
static pthread_mutex_t pause_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t pause_cond = PTHREAD_COND_INITIALIZER;

static __attribute__((unused)) void mutex_lock(struct mutex *lock)
{
	assert(!pthread_mutex_lock(&lock->native));
}

static __attribute__((unused)) void mutex_unlock(struct mutex *lock)
{
	assert(!pthread_mutex_unlock(&lock->native));
}

static void assert_locked(struct fman_pcd *pcd)
{
	int err = pthread_mutex_trylock(&pcd->fe_lock.native);

	if (!err)
		assert(!pthread_mutex_unlock(&pcd->fe_lock.native));
	assert(err == EBUSY);
}

static bool test_bit(unsigned int bit, const unsigned long *map)
{
	return !!(*map & (1UL << bit));
}
static void set_bit(unsigned int bit, unsigned long *map) { *map |= 1UL << bit; }
static bool list_empty(const int *list) { return !*list; }
static struct fman *fman_pcd_get_fman(struct fman_pcd *pcd) { return pcd->fman; }
static struct fman_pcd *fman_get_pcd(struct fman *fm) { return fm->pcd; }
static struct fman_port *fman_port_lookup_rx(struct fman *fm, u8 id)
{
	return fm->port;
}
static int fman_port_disable(struct fman_port *port)
{
	assert_locked(active_pcd);
	stop_calls++;
	return stop_fails ? -EBUSY : 0;
}
static int fman_pcd_fe_retained_check(struct fman_pcd *pcd, u8 id)
{
	assert_locked(pcd);
	return test_bit(id, pcd->fe_port_faulted) ? -ESHUTDOWN : 0;
}
static unsigned int fman_pcd_resolve_miss_fqid(struct fman_pcd *pcd, u8 id)
{
	return 0x200;
}
static int fman_pcd_port_ensure_params_page(struct fman_pcd *pcd,
					     struct fman_port *port)
{
	params_calls++;
	return failure == PARAMS ? -ENOMEM : 0;
}
static int __fman_pcd_fe_build_vm_chain(struct fman_pcd *pcd)
{
	assert_locked(pcd);
	chain_calls++;
	if (failure == CHAIN_BEFORE_TABLE)
		return -ENOMEM;
	pcd->fe_refcount = 1;
	pcd->fe_ehash_tables = 1;
	if (failure == CHAIN_AFTER_TABLE)
		return -ENOMEM;
	pcd->fe_enq = 1;
	return 0;
}
static int fman_pcd_fe_port_set(struct fman_pcd *pcd, u8 id);
static void fman_pcd_fe_fault_arm(struct fman_pcd *pcd, u8 id,
				  bool kg_programming_started);
static int __fman_pcd_fe_arm_engage(struct fman_pcd *pcd, unsigned int id,
				    unsigned long enter, unsigned int miss,
				    u32 ekfc)
{
	assert_locked(pcd);
	if (pause_arm) {
		assert(!pthread_mutex_lock(&pause_lock));
		arm_entered = true;
		assert(!pthread_cond_broadcast(&pause_cond));
		while (!resume_arm)
			assert(!pthread_cond_wait(&pause_cond, &pause_lock));
		assert(!pthread_mutex_unlock(&pause_lock));
	}
	arm_calls++;
	pcd->fe_arming_v4_table = &owner;
#ifdef FMAN_WORKSPACE_PRE_KG
	{
		int err = fman_pcd_fe_port_set(pcd, (u8)id);

		if (err) {
			fman_pcd_fe_fault_arm(pcd, (u8)id, false);
			return err;
		}
	}
#endif
	set_bit(id, pcd->fe_port_armed);
	return 0;
}
static int fman_pcd_fe_port_set(struct fman_pcd *pcd, u8 id)
{
	assert_locked(pcd);
	workspace_calls++;
	if (failure == WORKSPACE) {
		if (lose_port)
			pcd->fman->port = NULL;
		return -ENOMEM;
	}
	pcd->port_table = pcd->fe_arming_v4_table;
	pcd->fe_arming_v4_table = NULL;
	return 0;
}
static int fman_pcd_fe_adv_offload(struct fman_pcd *pcd, u8 id, bool on)
{
	assert_locked(pcd);
	assert(on);
	advanced_calls++;
	return failure == ADVANCED ? -EIO : 0;
}

#include "arm_failure_body.h"

static void reset(struct fman *fm, struct fman_port *port, struct fman_pcd *pcd)
{
	if (mutex_initialized)
		assert(!pthread_mutex_destroy(&pcd->fe_lock.native));
	memset(pcd, 0, sizeof(*pcd));
	assert(!pthread_mutex_init(&pcd->fe_lock.native, NULL));
	mutex_initialized = true;
	active_pcd = pcd;
	*fm = (struct fman) { .pcd = pcd, .port = port };
	pcd->fman = fm;
	failure = NONE;
	stop_fails = lose_port = false;
	stop_calls = arm_calls = workspace_calls = advanced_calls = 0;
	params_calls = chain_calls = 0;
}

static void *engage_thread(void *arg)
{
	struct fman *fm = arg;

	assert(!fman_pcd_fe_engage_profile(fm, 0x10, 0, ROUTED));
	return NULL;
}

int main(void)
{
	struct fman fm;
	struct fman_port port;
	struct fman_pcd pcd;
	pthread_t thread;

	reset(&fm, &port, &pcd);
	assert(!fman_pcd_fe_engage_profile(&fm, 0x10, 0, ROUTED));
	assert(arm_calls == 1 && workspace_calls == 1 && advanced_calls == 1);
	assert(!stop_calls && !pcd.fe_port_faulted[0] && pcd.port_table == &owner);

	reset(&fm, &port, &pcd);
	failure = PARAMS;
	assert(fman_pcd_fe_engage_profile(&fm, 0x10, 0, ROUTED) == -ENOMEM);
	assert(!arm_calls && !stop_calls && !pcd.fe_port_faulted[0]);

	reset(&fm, &port, &pcd);
	failure = WORKSPACE;
	assert(fman_pcd_fe_engage_profile(&fm, 0x10, 0, ROUTED) == -ENOMEM);
#ifdef FMAN_WORKSPACE_PRE_KG
	assert(!stop_calls && !advanced_calls && pcd.fe_arming_v4_table == &owner);
#else
	assert(stop_calls == 1 && !advanced_calls && pcd.fe_arming_v4_table == &owner);
#endif
	assert(test_bit(0x10, pcd.fe_port_faulted));
	assert(fman_pcd_fe_engage_profile(&fm, 0x10, 0, ROUTED) == -ESHUTDOWN);
	assert(arm_calls == 1);

	reset(&fm, &port, &pcd);
	failure = ADVANCED;
	stop_fails = true;
	assert(fman_pcd_fe_engage_profile(&fm, 0x10, 0, ROUTED) == -EIO);
	assert(stop_calls == 1 && pcd.port_table == &owner);
	assert(test_bit(0x10, pcd.fe_port_faulted));

	reset(&fm, &port, &pcd);
	failure = WORKSPACE;
	lose_port = true;
	assert(fman_pcd_fe_engage_profile(&fm, 0x10, 0, ROUTED) == -ENOMEM);
	assert(!stop_calls && pcd.fe_arming_v4_table == &owner);
	assert(test_bit(0x10, pcd.fe_port_faulted));

	for (int scenario = CHAIN_AFTER_TABLE; scenario >= CHAIN_BEFORE_TABLE;
	     scenario--) {
		reset(&fm, &port, &pcd);
		failure = scenario;
		assert(fman_pcd_fe_engage_profile(&fm, 0x10, 0, ROUTED) == -ENOMEM);
		assert(chain_calls == 1 && !arm_calls && !advanced_calls);
		failure = NONE;
		assert(fman_pcd_fe_engage_profile(&fm, 0x10, 0, ROUTED) == -ESHUTDOWN);
		assert(fman_pcd_fe_engage_profile(&fm, 0x11, 0, ROUTED) == -ESHUTDOWN);
		assert(chain_calls == 1 && !arm_calls && !advanced_calls);
	}

	reset(&fm, &port, &pcd);
	pcd.fe_ehash_tables = 1;
	assert(fman_pcd_fe_engage_profile(&fm, 0x10, 0, ROUTED) == -ESHUTDOWN);
	assert(!chain_calls && !params_calls && !arm_calls && !advanced_calls);

	for (int armed = 0; armed < 2; armed++) {
		reset(&fm, &port, &pcd);
		pcd.fe_vm_chain_built = true;
		pcd.fe_refcount = pcd.fe_ehash_tables = 1;
		if (armed)
			set_bit(0x10, pcd.fe_port_armed);
		assert(fman_pcd_fe_engage_profile(&fm, 0x10, 0, ROUTED) ==
		       -ESHUTDOWN);
		assert(pcd.fe_vm_chain_faulted && !chain_calls && !params_calls &&
		       !arm_calls && !advanced_calls);
		assert(fman_pcd_fe_engage_profile(&fm, 0x11, 0, ROUTED) ==
		       -ESHUTDOWN);
	}

	reset(&fm, &port, &pcd);
	pcd.fe_refcount = 1;
	assert(fman_pcd_fe_engage_profile(&fm, 0x10, 0, ROUTED) == -ESHUTDOWN);
	assert(pcd.fe_vm_chain_faulted && !chain_calls && !params_calls &&
	       !arm_calls && !advanced_calls);
	assert(fman_pcd_fe_engage_profile(&fm, 0x11, 0, ROUTED) == -ESHUTDOWN);

	reset(&fm, &port, &pcd);
	pcd.fe_obs_armed = true;
	assert(fman_pcd_fe_engage_profile(&fm, 0x10, 0, ROUTED) == -EBUSY);
	assert(!pcd.fe_vm_chain_faulted && !chain_calls && !params_calls &&
	       !arm_calls && !advanced_calls);

	/* Flow publication takes fe_lock and must wait for the full arm. */
	reset(&fm, &port, &pcd);
	pause_arm = true;
	arm_entered = resume_arm = false;
	assert(!pthread_create(&thread, NULL, engage_thread, &fm));
	assert(!pthread_mutex_lock(&pause_lock));
	while (!arm_entered)
		assert(!pthread_cond_wait(&pause_cond, &pause_lock));
	assert_locked(&pcd);
	resume_arm = true;
	assert(!pthread_cond_broadcast(&pause_cond));
	assert(!pthread_mutex_unlock(&pause_lock));
	assert(!pthread_join(thread, NULL));
	pause_arm = false;
	assert(!pthread_mutex_destroy(&pcd.fe_lock.native));
	puts("FE arm: partial setup stops RX and retains owners on failure");
	return 0;
}
