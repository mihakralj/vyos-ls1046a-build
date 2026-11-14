/* Host behavior check for the generated RX/OH CC-base SYNC gate. */
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

typedef uint32_t u32;
#define FMAN_PORT_TYPE_RX 1
#define FMAN_PORT_TYPE_OH 2
#define NIA_ENG_BMI 0x00500000U
#define NIA_BMI_AC_ENQ_FRAME 0x00000002U
#define NIA_ENG_HWK 0x00480000U
#define NIA_KG_CC_EN 0x00000200U
#define dev_warn(...) ((void)0)
#define dev_info(...) ((void)0)
#define dev_err(...) ((void)0)

struct fman {
	pthread_mutex_t cc_port_lock;
	atomic_uint engaged, syncs;
};
struct bmi_regs {
	struct { u32 fmbm_rccb, fmbm_rfpne; } rx;
	struct { u32 fmbm_occb, fmbm_ofpne; } oh;
};
struct fman_port {
	int port_type;
	void *dev;
	struct fman *fm;
	struct bmi_regs *bmi_regs;
	bool cc_engaged;
};

static pthread_barrier_t first_port_barrier;
static atomic_bool force_first_port_race;
static _Thread_local unsigned int gate_depth;

static inline void mutex_lock(pthread_mutex_t *lock)
{
	assert(!pthread_mutex_lock(lock));
	gate_depth++;
}

static inline void mutex_unlock(pthread_mutex_t *lock)
{
	assert(gate_depth);
	gate_depth--;
	assert(!pthread_mutex_unlock(lock));
}

static void iowrite32be(u32 value, u32 *address) { *address = value; }
static u32 ioread32be(u32 *address) { return *address; }
static __attribute__((unused)) void udelay(unsigned int delay) { }
static unsigned int fman_cc_engaged_get(struct fman *fm)
{
	unsigned int value = atomic_load(&fm->engaged);

	if (atomic_load(&force_first_port_race) && !gate_depth)
		pthread_barrier_wait(&first_port_barrier);
	return value;
}
static void fman_cc_engaged_inc(struct fman *fm) { atomic_fetch_add(&fm->engaged, 1); }
static void fman_cc_engaged_dec(struct fman *fm)
{
	if (atomic_load(&fm->engaged))
		atomic_fetch_sub(&fm->engaged, 1);
}
#ifndef FMAN_F293
static void fman_set_fpm_extc(struct fman *fm, u32 value)
{
	assert(value == 0x80000000U);
	atomic_fetch_add(&fm->syncs, 1);
}
static u32 fman_get_fpm_extc(struct fman *fm) { return 0; }
#else
/* T5 (post-F-293): the gate calls the serialized, fail-closed INV0 helper.
 * Every SYNC must be issued under cc_port_lock with no engaged port. */
static atomic_bool sync_fail;
static int fman_fpm_extc_sync(struct fman *fm, u32 *value, unsigned int *polls)
{
	assert(gate_depth && "first-port SYNC outside cc_port_lock");
	assert(!atomic_load(&fm->engaged) && "first-port SYNC with a port engaged");
	*value = 0;
	*polls = 1;
	if (atomic_load(&sync_fail)) {
		*value = 0x80000000U;
		return -ETIMEDOUT;
	}
	atomic_fetch_add(&fm->syncs, 1);
	return 0;
}
#endif

#include "fman_cc_sync_body.h"

static void test_transitions(void)
{
	struct fman fm = { .cc_port_lock = PTHREAD_MUTEX_INITIALIZER };
	struct bmi_regs first_regs = { .rx.fmbm_rfpne = NIA_ENG_HWK };
	struct bmi_regs second_regs = { .rx.fmbm_rfpne = NIA_ENG_HWK };
	struct bmi_regs idle_regs = { .rx.fmbm_rfpne = NIA_ENG_HWK };
	struct bmi_regs oh_regs = { 0 };
	struct fman_port first = {
		.port_type = FMAN_PORT_TYPE_RX, .fm = &fm, .bmi_regs = &first_regs,
	};
	struct fman_port second = {
		.port_type = FMAN_PORT_TYPE_RX, .fm = &fm, .bmi_regs = &second_regs,
	};
	struct fman_port idle = {
		.port_type = FMAN_PORT_TYPE_RX, .fm = &fm, .bmi_regs = &idle_regs,
	};
	struct fman_port oh = {
		.port_type = FMAN_PORT_TYPE_OH, .fm = &fm, .bmi_regs = &oh_regs,
	};
	struct fman_port missing = {
		.port_type = FMAN_PORT_TYPE_RX, .bmi_regs = &idle_regs,
	};

	assert(fman_port_set_cc_base(NULL, 0x100) == -EINVAL);
	assert(fman_port_set_cc_base(&missing, 0x100) == -ENODEV);
	assert(fman_port_set_oh_cc_base(&first, 0x100) == -EINVAL);
	assert(!fman_port_set_cc_base(&first, 0x100));
	assert(fm.engaged == 1 && fm.syncs == 1);
	assert(first_regs.rx.fmbm_rccb == 0x100);
	assert(first_regs.rx.fmbm_rfpne & NIA_KG_CC_EN);

	assert(!fman_port_set_cc_base(&idle, 0));
	assert(fm.engaged == 1 && fm.syncs == 1);
	assert(!fman_port_set_cc_base(&second, 0x200));
	assert(fm.engaged == 2 && fm.syncs == 1);
	assert(second_regs.rx.fmbm_rccb == 0x200);
	assert(second_regs.rx.fmbm_rfpne & NIA_KG_CC_EN);

	assert(!fman_port_set_cc_base(&first, 0x300));
	assert(fm.engaged == 2 && fm.syncs == 1);
	assert(!fman_port_set_cc_base(&first, 0));
	assert(fm.engaged == 1 && fm.syncs == 1);
	assert(!first_regs.rx.fmbm_rccb);
	assert(!(first_regs.rx.fmbm_rfpne & NIA_KG_CC_EN));
	assert(!fman_port_set_cc_base(&first, 0));
	assert(fm.engaged == 1 && fm.syncs == 1);
	assert(!fman_port_set_oh_cc_base(&oh, 0x300));
	assert(fm.engaged == 2 && fm.syncs == 1);
	assert(!fman_port_set_cc_base(&second, 0));
	assert(!fman_port_set_oh_cc_base(&oh, 0));
	assert(!fm.engaged && fm.syncs == 1);
	assert(!fman_port_set_cc_base(&first, 0x400));
	assert(fm.engaged == 1 && fm.syncs == 2);
	assert(!fman_port_set_cc_base(&first, 0));
	assert(!pthread_mutex_destroy(&fm.cc_port_lock));
}

static void *arm_port(void *arg)
{
	struct fman_port *port = arg;
	int err;

	err = port->port_type == FMAN_PORT_TYPE_RX ?
		fman_port_set_cc_base(port, 0x100) :
		fman_port_set_oh_cc_base(port, 0x200);
	assert(!err);
	return NULL;
}

static void test_concurrent_first_ports(void)
{
	struct fman fm = { .cc_port_lock = PTHREAD_MUTEX_INITIALIZER };
	struct bmi_regs rx_regs = { .rx.fmbm_rfpne = NIA_ENG_HWK };
	struct bmi_regs oh_regs = { 0 };
	struct fman_port rx = {
		.port_type = FMAN_PORT_TYPE_RX, .fm = &fm, .bmi_regs = &rx_regs,
	};
	struct fman_port oh = {
		.port_type = FMAN_PORT_TYPE_OH, .fm = &fm, .bmi_regs = &oh_regs,
	};
	pthread_t rx_thread, oh_thread;

	assert(!pthread_barrier_init(&first_port_barrier, NULL, 2));
	atomic_store(&force_first_port_race, true);
	assert(!pthread_create(&rx_thread, NULL, arm_port, &rx));
	assert(!pthread_create(&oh_thread, NULL, arm_port, &oh));
	assert(!pthread_join(rx_thread, NULL));
	assert(!pthread_join(oh_thread, NULL));
	atomic_store(&force_first_port_race, false);
	assert(fm.engaged == 2 && fm.syncs == 1);
	assert(!fman_port_set_cc_base(&rx, 0));
	assert(!fman_port_set_oh_cc_base(&oh, 0));
	assert(!fm.engaged);
	assert(!pthread_barrier_destroy(&first_port_barrier));
	assert(!pthread_mutex_destroy(&fm.cc_port_lock));
}

#ifdef FMAN_F293
/* T5: a refused/timed-out first-port SYNC changes no accounting, does not
 * enable CC dispatch and releases the lock (RCCB was written; F-311's caller
 * clears it). A later arm can still be the first port. */
static void test_sync_timeout(void)
{
	struct fman fm = { .cc_port_lock = PTHREAD_MUTEX_INITIALIZER };
	struct bmi_regs rx_regs = { .rx.fmbm_rfpne = NIA_ENG_HWK };
	struct bmi_regs oh_regs = { 0 };
	struct fman_port rx = {
		.port_type = FMAN_PORT_TYPE_RX, .fm = &fm, .bmi_regs = &rx_regs,
	};
	struct fman_port oh = {
		.port_type = FMAN_PORT_TYPE_OH, .fm = &fm, .bmi_regs = &oh_regs,
	};

	atomic_store(&sync_fail, true);
	assert(fman_port_set_cc_base(&rx, 0x100) == -ETIMEDOUT);
	assert(!fm.engaged && !rx.cc_engaged && !gate_depth);
	assert(!(rx_regs.rx.fmbm_rfpne & NIA_KG_CC_EN));
	assert(fman_port_set_oh_cc_base(&oh, 0x200) == -ETIMEDOUT);
	assert(!fm.engaged && !oh.cc_engaged && !gate_depth);
	assert(oh_regs.oh.fmbm_ofpne != (NIA_ENG_HWK | NIA_KG_CC_EN));
	/* Clearing after the failure is a no-op for the accounting. */
	assert(!fman_port_set_cc_base(&rx, 0));
	assert(!fm.engaged && !rx_regs.rx.fmbm_rccb);
	atomic_store(&sync_fail, false);
	assert(!fman_port_set_cc_base(&rx, 0x100));
	assert(fm.engaged == 1 && fm.syncs == 1 && rx.cc_engaged);
	assert(!fman_port_set_cc_base(&rx, 0));
	assert(!fm.engaged);
	assert(!pthread_mutex_destroy(&fm.cc_port_lock));
}

/* T5: concurrent arm/disarm churn on different ports. The SYNC mock asserts
 * lock held and no engaged port on every first-port SYNC; afterwards the
 * counter equals the number of engaged ports. With an anchor port engaged
 * throughout, the churn never issues a SYNC (F-215: never with a sibling). */
#define CHURN 20000
struct churn { struct fman_port *port; bool oh; };

static void *churn_port(void *arg)
{
	struct churn *c = arg;

	for (int i = 0; i < CHURN; i++) {
		u32 base = (i & 1) ? 0 : 0x100 + (u32)i;
		int err = c->oh ? fman_port_set_oh_cc_base(c->port, base) :
				  fman_port_set_cc_base(c->port, base);

		assert(!err);
	}
	return NULL;
}

static void test_concurrent_arm_disarm(bool anchored)
{
	struct fman fm = { .cc_port_lock = PTHREAD_MUTEX_INITIALIZER };
	struct bmi_regs regs[4] = {
		{ .rx = { .fmbm_rfpne = NIA_ENG_HWK } },
		{ .rx = { .fmbm_rfpne = NIA_ENG_HWK } },
		{ .rx = { .fmbm_rfpne = NIA_ENG_HWK } },
		{ .oh = { 0, 0 } },
	};
	struct fman_port ports[4];
	struct churn churns[3];
	pthread_t threads[3];
	unsigned int flags = 0;

	for (int i = 0; i < 4; i++)
		ports[i] = (struct fman_port) {
			.port_type = i == 3 ? FMAN_PORT_TYPE_OH : FMAN_PORT_TYPE_RX,
			.fm = &fm, .bmi_regs = &regs[i],
		};
	if (anchored)
		assert(!fman_port_set_cc_base(&ports[0], 0x80));
	for (int i = 0; i < 3; i++) {
		churns[i] = (struct churn) { .port = &ports[i + 1], .oh = i == 2 };
		assert(!pthread_create(&threads[i], NULL, churn_port, &churns[i]));
	}
	for (int i = 0; i < 3; i++)
		assert(!pthread_join(threads[i], NULL));
	for (int i = 0; i < 4; i++)
		flags += ports[i].cc_engaged;
	assert(fm.engaged == flags && fm.engaged == (anchored ? 1U : 0U));
	if (anchored)
		assert(fm.syncs == 1);
	else
		assert(fm.syncs >= 1);
	if (anchored)
		assert(!fman_port_set_cc_base(&ports[0], 0));
	assert(!fm.engaged);
	assert(!pthread_mutex_destroy(&fm.cc_port_lock));
}
#endif

int main(void)
{
	test_transitions();
	test_concurrent_first_ports();
#ifdef FMAN_F293
	test_sync_timeout();
	test_concurrent_arm_disarm(true);
	test_concurrent_arm_disarm(false);
	puts("RX/OH CC SYNC: T5 timeout accounting and concurrent arm/disarm churn passed");
#endif
	puts("RX/OH CC SYNC: port transitions and concurrent first-port gate passed");
	return 0;
}
