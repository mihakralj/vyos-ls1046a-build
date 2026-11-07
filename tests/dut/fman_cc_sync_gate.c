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
static void udelay(unsigned int delay) { }
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
static void fman_set_fpm_extc(struct fman *fm, u32 value)
{
	assert(value == 0x80000000U);
	atomic_fetch_add(&fm->syncs, 1);
}
static u32 fman_get_fpm_extc(struct fman *fm) { return 0; }

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

int main(void)
{
	test_transitions();
	test_concurrent_first_ports();
	puts("RX/OH CC SYNC: port transitions and concurrent first-port gate passed");
	return 0;
}
