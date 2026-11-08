// SPDX-License-Identifier: GPL-2.0
/* Host fault injection for generated FMan teardown debugfs handlers. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>

typedef uint8_t u8;
#define __user
#define pr_info(...) ((void)0)
#define pr_warn(...) ((void)0)
#define pr_warn_ratelimited(...) ((void)0)
#define lockdep_assert_held(lock) assert((lock)->locked)
#define bitmap_empty(map, bits) (!(map)[0])

struct mutex { bool locked; };
struct fman_pcd {
	struct mutex fe_lock;
	bool fe_vm_chain_built, fe_vm_chain_faulted, fe_obs_armed;
	unsigned long fe_port_armed[1], fe_port_faulted[1];
	void *fe_arming_v4_table;
};
struct file { void *private_data; };
struct seq_file { void *private; };

static int disengage_error, recover_error, set_error;
static unsigned int disengages, recoveries, sets;
static bool copy_fails;

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

static int copy_from_user(char *dst, const char *src, size_t n)
{
	if (copy_fails)
		return 1;
	memcpy(dst, src, n);
	return 0;
}

static __attribute__((unused)) int
__fman_pcd_fe_arm_disengage(struct fman_pcd *pcd, u8 port)
{
#ifdef FMAN_TEST_LIFECYCLE_LOCK
	assert(pcd->fe_lock.locked);
#else
	(void)pcd;
#endif
	assert(port == 0x10);
	disengages++;
	return disengage_error;
}

#ifdef FMAN_TEST_LIFECYCLE_LOCK
#define RECOVER_FN __fman_pcd_port_recover_locked
#else
#define RECOVER_FN fman_pcd_port_recover
#endif
static __attribute__((unused)) int RECOVER_FN(struct fman_pcd *pcd, u8 port)
{
#ifdef FMAN_TEST_LIFECYCLE_LOCK
	assert(pcd->fe_lock.locked);
#else
	(void)pcd;
#endif
	assert(port == 0x10);
	recoveries++;
	return recover_error;
}

static int fman_pcd_ehash_table_set(struct fman_pcd *pcd, unsigned int mask,
				    u8 key_size, u8 hash_shift)
{
	assert(pcd->fe_lock.locked);
	assert(mask == 0x20 && key_size == 14 && hash_shift == 0);
	sets++;
	return set_error;
}

#include "fman_teardown_body.h"

static ssize_t run_full(struct file *file, const char *command)
{
	loff_t offset = 0;

	return fman_pcd_fe_disengage_full_write(file, command,
						strlen(command), &offset);
}

static ssize_t run_ehash(struct file *file, const char *command)
{
	loff_t offset = 0;

	return fman_pcd_ehash_write(file, command, strlen(command), &offset);
}

int main(void)
{
#ifndef FMAN_TEST_RECOVERY_BLOCKED
	static const int errors[] = { -EBUSY, -EIO, -ETIMEDOUT };
#endif
	struct fman_pcd pcd = {};
	struct file file = { .private_data = &pcd };
	struct seq_file seq = { .private = &pcd };
	struct file ehash_file = { .private_data = &seq };

#ifdef FMAN_TEST_RECOVERY_BLOCKED
	struct file empty = {};

	disengages = recoveries = 0;
	assert(fman_pcd_port_recover(NULL, 0x10) == -EINVAL);
	assert(fman_pcd_port_recover(&pcd, 0x07) == -EINVAL);
	assert(fman_pcd_port_recover(&pcd, 0x28) == -EINVAL);
	assert(fman_pcd_port_recover(&pcd, 0x10) == -EOPNOTSUPP);
	assert(recoveries == 0 && !pcd.fe_lock.locked);
	assert(run_full(&empty, "10") == -EINVAL);
	assert(run_full(&file, "10") == -EOPNOTSUPP);
	assert(disengages == 0 && recoveries == 0 && !pcd.fe_lock.locked);
#else
	for (unsigned int i = 0; i < sizeof(errors) / sizeof(errors[0]); i++) {
		disengage_error = errors[i];
		disengages = recoveries = 0;
		assert(run_full(&file, "10") == errors[i]);
		assert(disengages == 1 && recoveries == 0 && !pcd.fe_lock.locked);
	}

	disengage_error = 0;
	recover_error = -EBUSY;
	disengages = recoveries = 0;
	assert(run_full(&file, "10") == -EBUSY);
	assert(disengages == 1 && recoveries == 1 && !pcd.fe_lock.locked);

	recover_error = 0;
	disengages = recoveries = 0;
	assert(run_full(&file, "10") == 2);
	assert(disengages == 1 && recoveries == 1 && !pcd.fe_lock.locked);
#endif

	disengages = recoveries = 0;
	assert(run_full(&file, "07") == -EINVAL);
	copy_fails = true;
	assert(run_full(&file, "10") == -EFAULT);
	assert(disengages == 0 && recoveries == 0);
	copy_fails = false;

	sets = 0;
	assert(run_ehash(&ehash_file, "clear") == -EOPNOTSUPP);
	assert(sets == 0 && !pcd.fe_lock.locked);
	set_error = -EBUSY;
	assert(run_ehash(&ehash_file, "set 20 14 0") == -EBUSY);
	set_error = 0;
	assert(run_ehash(&ehash_file, "set 20 14 0") == 11);
	assert(sets == 2 && !pcd.fe_lock.locked);
#ifdef FMAN_TEST_DEBUG_CHAIN_GUARD
	pcd.fe_vm_chain_faulted = true;
	assert(run_ehash(&ehash_file, "set 20 14 0") == -ESHUTDOWN);
	assert(sets == 2 && !pcd.fe_lock.locked);
	pcd.fe_vm_chain_faulted = false;
	pcd.fe_obs_armed = true;
	assert(run_ehash(&ehash_file, "set 20 14 0") == -EBUSY);
	assert(sets == 2 && !pcd.fe_lock.locked);
	pcd.fe_obs_armed = false;
#endif
	assert(run_ehash(&ehash_file, "invalid") == -EINVAL);
	copy_fails = true;
	assert(run_ehash(&ehash_file, "clear") == -EFAULT);
	assert(sets == 2 && !pcd.fe_lock.locked);

#ifdef FMAN_TEST_RECOVERY_BLOCKED
	puts("OK: unsafe recovery and full disengage are disabled before mutation");
#else
	puts("OK: full disengage preserves errors; debugfs table clear is disabled");
#endif
	return 0;
}
