// SPDX-License-Identifier: GPL-2.0
/* Host fault model for the source-extracted QMan CGR update wrapper. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint16_t u16;

#define CGR_NUM 256
#define dev_err(...) ((void)0)
#define dev_dbg(...) ((void)0)

struct qman_cgr {
	unsigned int cgrid;
	u16 chan;
	int portal_cpu;
};

struct qm_mcc_initcgr {
	int value;
};

struct portal_config {
	u16 channel;
};

struct qman_portal {
	struct portal_config *config;
	int cgr_lock;
};

static struct qman_portal portals[2];
static struct portal_config configs[2];
static unsigned char qman_cgr_cpus[CGR_NUM];
static int current_cpu, preempt_count, portal_refs, dispatch_calls;
static int dispatch_error, modify_calls, modify_result;
static struct qman_cgr *modified_cgr;
static struct qm_mcc_initcgr *modified_opts;

static void preempt_disable(void) { preempt_count++; }
static void preempt_enable(void) { assert(preempt_count > 0); preempt_count--; }
static int smp_processor_id(void) { return current_cpu; }

static struct qman_portal *get_affine_portal(void)
{
	portal_refs++;
	return &portals[current_cpu];
}

static void put_affine_portal(void)
{
	assert(portal_refs > 0);
	portal_refs--;
}

static void lock(int *value) { assert(!*value); *value = 1; }
static void unlock(int *value) { assert(*value); *value = 0; }
#define raw_spin_lock_irqsave(lockp, flags) do { (flags) = 0; lock(lockp); } while (0)
#define raw_spin_unlock_irqrestore(lockp, flags) do { (void)(flags); unlock(lockp); } while (0)

static int qm_modify_cgr(struct qman_cgr *cgr, unsigned int flags,
			 struct qm_mcc_initcgr *opts)
{
	assert(flags == 0);
	modify_calls++;
	modified_cgr = cgr;
	modified_opts = opts;
	return modify_result;
}

static int smp_call_function_single(int cpu, void (*fn)(void *),
				    void *arg, bool wait)
{
	int saved = current_cpu;

	assert(preempt_count && wait);
	dispatch_calls++;
	if (dispatch_error)
		return dispatch_error;
	current_cpu = cpu;
	fn(arg);
	current_cpu = saved;
	return 0;
}

#include "qman_cgr_update_body.h"

static void reset(struct qman_cgr *cgr, int owner)
{
	memset(portals, 0, sizeof(portals));
	memset(configs, 0, sizeof(configs));
	memset(qman_cgr_cpus, 0, sizeof(qman_cgr_cpus));
	configs[0].channel = 3;
	configs[1].channel = 3;
	portals[0].config = &configs[0];
	portals[1].config = &configs[1];
	cgr->cgrid = 7;
	cgr->chan = 3;
	cgr->portal_cpu = owner;
	qman_cgr_cpus[cgr->cgrid] = owner;
	current_cpu = preempt_count = portal_refs = dispatch_calls = 0;
	dispatch_error = modify_calls = modify_result = 0;
	modified_cgr = NULL;
	modified_opts = NULL;
}

static void finished(void)
{
	assert(!preempt_count && !portal_refs && !current_cpu);
	assert(!portals[0].cgr_lock && !portals[1].cgr_lock);
}

int main(void)
{
	struct qman_cgr cgr;
	struct qm_mcc_initcgr opts = { .value = 42 };
	int ret;

	reset(&cgr, 0);
	assert(qman_update_cgr_safe(&cgr, &opts) == 0);
	assert(!dispatch_calls && modify_calls == 1);
	assert(modified_cgr == &cgr && modified_opts == &opts);
	finished();

	reset(&cgr, 0);
	modify_result = -ETIMEDOUT;
	assert(qman_update_cgr_safe(&cgr, &opts) == -ETIMEDOUT);
	assert(!dispatch_calls && modify_calls == 1);
	finished();

	reset(&cgr, 1);
	assert(qman_update_cgr_safe(&cgr, &opts) == 0);
	assert(dispatch_calls == 1 && modify_calls == 1);
	assert(modified_cgr == &cgr && modified_opts == &opts);
	finished();

	reset(&cgr, 1);
	modify_result = -EIO;
	assert(qman_update_cgr_safe(&cgr, &opts) == -EIO);
	assert(dispatch_calls == 1 && modify_calls == 1);
	finished();

	reset(&cgr, 1);
	dispatch_error = -ENXIO;
	ret = qman_update_cgr_safe(&cgr, &opts);
	if (ret != -ENXIO) {
		fprintf(stderr, "offline dispatch returned %d, expected -ENXIO\n", ret);
		return 1;
	}
	assert(dispatch_calls == 1 && !modify_calls);
	finished();

	reset(&cgr, 1);
	dispatch_error = -ENOMEM;
	assert(qman_update_cgr_safe(&cgr, &opts) == -ENOMEM);
	assert(dispatch_calls == 1 && !modify_calls);
	finished();

	reset(&cgr, 1);
	configs[1].channel = 4;
	assert(qman_update_cgr_safe(&cgr, &opts) == -EINVAL);
	assert(dispatch_calls == 1 && !modify_calls);
	finished();

	puts("QMan CGR update: local, remote and dispatch-error cases passed");
	return 0;
}
