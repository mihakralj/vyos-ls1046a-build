/* Host fault tests for generated F-290 KeyGen indirect-window functions. */
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
#define __iomem
#define GFP_KERNEL 0
#define FMAN_MAX_NUM_OF_HW_PORTS 64
#define FM_KG_MAX_NUM_OF_SCHEMES 32
#define FM_KG_NUM_OF_GENERIC_REGS 8
#define FM_KG_KGAR_NUM_SHIFT 16
#define FM_KG_KGAR_GO 0x80000000U
#define FM_KG_KGAR_READ 0x40000000U
#define FM_KG_KGAR_WRITE 0U
#define FM_KG_KGAR_ERR 0x20000000U
#define FM_KG_KGAR_SEL_SCHEME_ENTRY 0U
#define FM_KG_KGAR_SCM_WSEL_UPDATE_CNT 0x00008000U
#define FM_KG_KGAR_SEL_CLS_PLAN_ENTRY 0x01000000U
#define FM_KG_KGAR_SEL_PORT_ENTRY 0x02000000U
#define FM_KG_KGAR_SEL_PORT_WSEL_SP 0x00008000U
#define FM_KG_KGAR_SEL_PORT_WSEL_CPP 0x00004000U
#define FM_KG_KGAR_CLS_PLAN_WSEL_SHIFT 8
#define FM_KG_KGGCR_EN 0x80000000U
#define FM_EX_KG_DOUBLE_ECC 0x80000000U
#define FM_EX_KG_KEYSIZE_OVERFLOW 0x40000000U
#define ENQUEUE_KG_DFLT_NIA 0x00500002U
#define DUMMY_PORT_ID 0
#define pr_err(...) ((void)0)
#define pr_info(...) ((void)0)
#define kzalloc(size, flags) calloc(1, (size))
#define kfree(ptr) free(ptr)

struct mutex { pthread_mutex_t value; };
static void mutex_init(struct mutex *lock)
{
	assert(!pthread_mutex_init(&lock->value, NULL));
}
static void mutex_lock(struct mutex *lock)
{
	assert(!pthread_mutex_lock(&lock->value));
}
static void mutex_unlock(struct mutex *lock)
{
	assert(!pthread_mutex_unlock(&lock->value));
}

#include "keygen_types.h"

enum action_mode { NORMAL, STUCK, ERROR, DELAYED };
static struct fman_kg_regs regs;
static enum action_mode mode;
static bool pending, staging;
static pthread_t staging_thread;
static unsigned int action_writes, window_writes, polls, release_after, fail_at;

static void iowrite32be(u32 value, u32 *address)
{
	uintptr_t ptr = (uintptr_t)address;
	uintptr_t first = (uintptr_t)&regs.fmkg_indirect[0];
	uintptr_t last = (uintptr_t)&regs.fmkg_indirect[63];

	if (address == &regs.fmkg_ar) {
		if (staging) {
			assert(pthread_equal(staging_thread, pthread_self()));
			staging = false;
		}
		action_writes++;
		pending = true;
		polls = 0;
	} else if (ptr >= first && ptr < last) {
		if (!staging) {
			staging = true;
			staging_thread = pthread_self();
		} else {
			assert(pthread_equal(staging_thread, pthread_self()));
		}
		window_writes++;
		sched_yield();
	}
	*address = value;
}

static u32 ioread32be(u32 *address)
{
	if (address == &regs.fmkg_ar && pending) {
		polls++;
		if ((mode == STUCK && action_writes >= fail_at) ||
		    (mode == DELAYED && polls < release_after))
			return FM_KG_KGAR_GO;
		pending = false;
		return mode == ERROR && action_writes >= fail_at ? FM_KG_KGAR_ERR : 0;
	}
	return *address & (address == &regs.fmkg_ar ? ~FM_KG_KGAR_GO : ~0U);
}

static void udelay(unsigned int n) { (void)n; }

#include "keygen_body.h"

static void reset_hardware(enum action_mode next_mode)
{
	memset(&regs, 0, sizeof(regs));
	mode = next_mode;
	pending = staging = false;
	action_writes = window_writes = polls = release_after = 0;
	fail_at = 1;
}

static void init_keygen(struct fman_keygen *kg)
{
	memset(kg, 0, sizeof(*kg));
	kg->keygen_regs = &regs;
	mutex_init(&kg->indirect_lock);
	kg->schemes[3].used = true;
	kg->schemes[3].hw_port_id = 0x10;
}

static void test_stuck_and_late_completion(void)
{
	struct fman_keygen kg;
	struct fman_kg_scheme_regs scheme = { 0 };
	unsigned int actions, writes;

	reset_hardware(STUCK);
	init_keygen(&kg);
	assert(keygen_write_scheme(&kg, 3, &scheme, true) == -ETIMEDOUT);
	assert(kg.indirect_faulted && pending && polls == 100000);
	actions = action_writes;
	writes = window_writes;
	pending = false; /* A late completion cannot reopen the fenced window. */
	mode = NORMAL;
	assert(keygen_write_scheme(&kg, 3, &scheme, true) == -ESHUTDOWN);
	assert(keygen_bind_port_to_schemes(&kg, 3, false) == -ESHUTDOWN);
	assert(keygen_cls_plan0_passall(&kg, 0x10) == -ESHUTDOWN);
	assert(actions == action_writes && writes == window_writes);
}

static void test_error_and_foreign_action(void)
{
	struct fman_keygen kg;
	struct fman_kg_scheme_regs scheme = { 0 };
	unsigned int writes;

	reset_hardware(ERROR);
	init_keygen(&kg);
	assert(keygen_bind_port_to_schemes(&kg, 3, false) == -EIO);
	assert(kg.indirect_faulted && action_writes == 1 && !window_writes);
	assert(keygen_write_scheme(&kg, 3, &scheme, true) == -ESHUTDOWN);
	assert(action_writes == 1 && !window_writes);

	reset_hardware(STUCK);
	init_keygen(&kg);
	fail_at = 2;
	assert(keygen_bind_port_to_schemes(&kg, 3, false) == -ETIMEDOUT);
	assert(kg.indirect_faulted && action_writes == 2 && window_writes == 1);
	writes = window_writes;
	assert(keygen_cls_plan0_passall(&kg, 0x10) == -ESHUTDOWN);
	assert(action_writes == 2 && window_writes == writes);

	reset_hardware(STUCK);
	init_keygen(&kg);
	fail_at = 0;
	pending = true;
	assert(keygen_write_scheme(&kg, 3, &scheme, true) == -EBUSY);
	assert(kg.indirect_faulted && !action_writes && !window_writes);
}

static void test_boundary_and_init(void)
{
	struct fman_keygen kg, *initialized;
	struct fman_kg_scheme_regs scheme = { 0 };

	reset_hardware(DELAYED);
	init_keygen(&kg);
	release_after = 100000;
	assert(!keygen_write_scheme(&kg, 3, &scheme, true));
	assert(!kg.indirect_faulted && polls == 100000);

	reset_hardware(NORMAL);
	initialized = keygen_init(&regs);
	assert(initialized && action_writes == 2 * FMAN_MAX_NUM_OF_HW_PORTS);
	assert(regs.fmkg_gcr & FM_KG_KGGCR_EN);
	free(initialized);

	reset_hardware(STUCK);
	assert(!keygen_init(&regs));
	assert(action_writes == 1 && polls == 100000);

	reset_hardware(STUCK);
	pending = true;
	fail_at = 0;
	assert(!keygen_init(&regs));
	assert(!action_writes && !window_writes);
}

static void test_cls_plan_once(void)
{
	struct fman_keygen kg;

	reset_hardware(NORMAL);
	init_keygen(&kg);
	assert(!keygen_cls_plan0_passall(&kg, 0x10));
	assert(kg.cls_plan0_passall && action_writes == 2 && window_writes == 8);
	assert(!keygen_cls_plan0_passall(&kg, 0x11));
	assert(action_writes == 2 && window_writes == 8);
}

static void *concurrent_writer(void *arg)
{
	struct fman_keygen *kg = arg;
	struct fman_kg_scheme_regs scheme = { .kgse_mode = 1 };
	int i;

	for (i = 0; i < 250; i++) {
		assert(!keygen_write_scheme(kg, 3, &scheme, true));
		assert(!keygen_bind_port_to_schemes(kg, 3, false));
	}
	return NULL;
}

static void test_serialization(void)
{
	struct fman_keygen kg;
	pthread_t threads[4];
	int i;

	reset_hardware(NORMAL);
	init_keygen(&kg);
	for (i = 0; i < 4; i++)
		assert(!pthread_create(&threads[i], NULL, concurrent_writer, &kg));
	for (i = 0; i < 4; i++)
		assert(!pthread_join(threads[i], NULL));
	assert(action_writes == 4 * 250 * 3 && !staging);
}

int main(void)
{
	test_stuck_and_late_completion();
	test_error_and_foreign_action();
	test_boundary_and_init();
	test_cls_plan_once();
	test_serialization();
	puts("KeyGen window: timeout fencing, init, and concurrent access passed");
	return 0;
}
