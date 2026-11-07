/* Host tests for the generated F-291 KeyGen arm mutation boundary. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

typedef uint8_t u8;
typedef uint32_t u32;
struct mutex { bool held; };
struct keygen_scheme { int unused; };
struct fman_keygen { struct keygen_scheme *slot; };
struct fman_port { int unused; };
struct fman { struct fman_keygen *keygen; };
struct fman_pcd { struct fman *fman; struct mutex lock; };

static struct fman *fman_pcd_get_fman(struct fman_pcd *pcd) { return pcd->fman; }
static struct mutex *fman_pcd_get_lock(struct fman_pcd *pcd) { return &pcd->lock; }
static void mutex_lock(struct mutex *lock) { assert(!lock->held); lock->held = true; }
static void mutex_unlock(struct mutex *lock) { assert(lock->held); lock->held = false; }
static struct keygen_scheme *kg_find_port_scheme(struct fman_keygen *keygen,
						 u8 port, u8 *id)
{
	return keygen->slot;
}

#include "kg_arm_prefix.h"

int main(void)
{
	struct keygen_scheme slot;
	struct fman_keygen keygen = { .slot = &slot };
	struct fman fm = { .keygen = &keygen };
	struct fman_pcd pcd = { .fman = &fm };
	bool started = true;
	u8 saved = 0;

	assert(fman_pcd_kg_port_arm_fe(NULL, 0x10, 1, &saved, 1, &started) == -EINVAL);
	assert(!started);
	assert(fman_pcd_kg_port_arm_fe(&pcd, 0x07, 1, &saved, 1, &started) == -EINVAL);
	assert(!started);
	keygen.slot = NULL;
	assert(fman_pcd_kg_port_arm_fe(&pcd, 0x10, 1, &saved, 1, &started) == -ENODEV);
	assert(!started && !pcd.lock.held);
	keygen.slot = &slot;
	assert(!fman_pcd_kg_port_arm_fe(&pcd, 0x10, 1, &saved, 1, &started));
	assert(started && pcd.lock.held);
	mutex_unlock(&pcd.lock);
	assert(fman_pcd_kg_port_arm_fe(&pcd, 0x10, 1, &saved, 1, NULL) == -EINVAL);
	puts("KG arm: pre-programming failures leave RX untouched");
	return 0;
}
