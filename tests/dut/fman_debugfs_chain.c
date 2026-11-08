/* Host fault tests for debugfs mutations of a retained FE chain. */
#include <assert.h>
#include <ctype.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>

typedef uint8_t u8;
typedef uint32_t u32;
#define __user
#define __iomem
#define WARN_ON(value) (value)
#define pr_info(...) ((void)0)
#define pr_err(...) ((void)0)
#define pr_warn_ratelimited(...) ((void)0)
#define lockdep_assert_held(lock) assert((lock)->locked)

struct mutex { bool locked; };
struct muram_info { u32 words[64]; };
struct fman_pcd {
	struct mutex fe_lock;
	bool fe_vm_chain_built, fe_vm_chain_faulted;
	bool fe_obs_armed;
	unsigned long fe_port_armed[1], fe_port_faulted[1];
	unsigned long fe_hash_off, fe_obs_hit_off, fe_obs_miss_off;
	u32 fe_obs_saved_hit, fe_obs_saved_miss;
	void *fman;
	void *fe_arming_v4_table;
	int fe_refcount;
};
struct seq_file { void *private; };
struct file { void *private_data; };

static unsigned int mutations, unlocked_mutations, zero_offset_writes;
static struct muram_info muram;
static struct fman_pcd *io_owner;

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

static __attribute__((unused)) bool bitmap_empty(const unsigned long *map, unsigned int bits)
{
	assert(bits == 0x28);
	return !*map;
}

static int copy_from_user(char *dst, const char *src, size_t count)
{
	memcpy(dst, src, count);
	return 0;
}

static char *strim(char *text)
{
	char *end;

	while (isspace((unsigned char)*text))
		text++;
	end = text + strlen(text);
	while (end > text && isspace((unsigned char)end[-1]))
		*--end = '\0';
	return text;
}

static int mutate(struct fman_pcd *pcd)
{
	assert(pcd->fe_lock.locked);
	mutations++;
	return 0;
}

static int fman_pcd_fe_pool_alloc(struct fman_pcd *pcd) { return mutate(pcd); }
static void fman_pcd_fe_pool_free(struct fman_pcd *pcd)
{
	mutate(pcd);
	pcd->fe_hash_off = 0;
}
static int fman_pcd_fe_singletons_build(struct fman_pcd *pcd) { return mutate(pcd); }
static void fman_pcd_fe_singletons_free(struct fman_pcd *pcd)
{
	mutate(pcd);
	pcd->fe_hash_off = 0;
}
static int fman_pcd_fe_enq_build(struct fman_pcd *pcd) { return mutate(pcd); }
static void fman_pcd_fe_enq_free(struct fman_pcd *pcd) { mutate(pcd); }
static int fman_pcd_fe_enter_build(struct fman_pcd *pcd, unsigned long off)
{
	return mutate(pcd);
}
static void fman_pcd_fe_enter_free(struct fman_pcd *pcd) { mutate(pcd); }
static int fman_pcd_fe_hash_build(struct fman_pcd *pcd) { return mutate(pcd); }
static void fman_pcd_fe_hash_free(struct fman_pcd *pcd)
{
	mutate(pcd);
	pcd->fe_hash_off = 0;
}
static int fman_pcd_fe_obs_arm(struct fman_pcd *pcd, unsigned int port,
			       unsigned int hit, unsigned int miss)
{
	if (pcd->fe_obs_armed)
		return -EBUSY;
	mutate(pcd);
	pcd->fe_obs_armed = true;
	return 0;
}
static struct muram_info *fman_get_muram(void *fman) { return &muram; }
static unsigned long fman_muram_offset_to_vbase(struct muram_info *mem,
						unsigned long offset)
{
	assert(offset + 7 * sizeof(u32) <= sizeof(mem->words));
	return (unsigned long)((char *)mem->words + offset);
}
static u32 ioread32be(const u32 *addr) { return *addr; }
static void iowrite32be(u32 value, u32 *addr)
{
	mutate(io_owner);
	if (addr == &muram.words[5] || addr == &muram.words[6])
		zero_offset_writes++;
	*addr = value;
}
static void fman_pcd_fe_obs_free_one(struct fman_pcd *pcd, unsigned long off)
{
	mutate(pcd);
}
static int fman_pcd_fe_group_hex2bin(const char *hex, u8 *bin, int len)
{
	return 0;
}
static int fman_pcd_fe_group_build(struct fman_pcd *pcd, unsigned int fqid,
				   const u8 *key, const u8 *mask)
{
	return mutate(pcd);
}
static void fman_pcd_fe_group_free(struct fman_pcd *pcd) { mutate(pcd); }
static int fman_pcd_ehash_table_set(struct fman_pcd *pcd, unsigned int mask,
				    u8 key_size, u8 shift)
{
	return mutate(pcd);
}
static int fman_pcd_fe_kg_ekfc_reconfig(struct fman_pcd *pcd, u8 scheme, uint32_t ekfc)
{
	if (!pcd->fe_lock.locked)
		unlocked_mutations++;
	mutations++;
	return 0;
}

#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wsign-compare"
#include "debugfs_chain_body.h"
#pragma GCC diagnostic pop

typedef ssize_t (*writer)(struct file *, const char *, size_t, loff_t *);
static const struct {
	const char *name;
	writer write;
	const char *command;
} cases[] = {
	{ "pool get", fman_pcd_fe_pool_write, "get" },
	{ "pool put", fman_pcd_fe_pool_write, "put" },
	{ "singletons build", fman_pcd_fe_singletons_write, "build" },
	{ "singletons clear", fman_pcd_fe_singletons_write, "clear" },
	{ "enq build", fman_pcd_fe_enq_write, "build" },
	{ "enq clear", fman_pcd_fe_enq_write, "clear" },
	{ "enter build", fman_pcd_fe_enter_write, "build" },
	{ "enter clear", fman_pcd_fe_enter_write, "clear" },
	{ "hashfe build", fman_pcd_fe_hashfe_write, "build" },
	{ "hashfe clear", fman_pcd_fe_hashfe_write, "clear" },
	{ "observer arm", fman_pcd_fe_obs_write, "arm 10 1 2" },
	{ "observer disarm", fman_pcd_fe_obs_write, "disarm 10" },
	{ "group build", fman_pcd_fe_group_write, "build 1" },
	{ "group clear", fman_pcd_fe_group_write, "clear" },
	{ "ehash set", fman_pcd_ehash_write, "set ff 14 0" },
	{ "kg ekfc set", fman_pcd_fe_kg_ekfc_write, "set 4 801c0006" },
};

int main(void)
{
	unsigned int failures = 0;

	for (unsigned int state = 0; state < 8; state++) {
		for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
			struct fman_pcd pcd = { .fe_refcount = 1, .fe_hash_off = 0x40 };
			struct seq_file seq = { .private = &pcd };
			struct file file = { .private_data = &seq };
			bool observer_disarm = !strcmp(cases[i].name, "observer disarm");
			bool chain_tracked = (state > 0 && state < 6) || state == 7;
			loff_t off = 0;
			ssize_t ret;

			pcd.fe_vm_chain_built = state == 1 || state == 7;
			pcd.fe_vm_chain_faulted = state == 2;
			pcd.fe_port_armed[0] = state == 3;
			pcd.fe_port_faulted[0] = state == 4;
			pcd.fe_arming_v4_table = state == 5 ? &pcd : NULL;
			pcd.fe_obs_armed = state == 6 || state == 7;
			io_owner = &pcd;
			mutations = unlocked_mutations = zero_offset_writes = 0;
			ret = cases[i].write(&file, cases[i].command,
					     strlen(cases[i].command), &off);
			if ((chain_tracked &&
			     (ret != -ESHUTDOWN || mutations ||
			      pcd.fe_refcount != 1 ||
			      (state == 7 && !pcd.fe_obs_armed))) ||
			    (state == 6 && observer_disarm &&
			     (ret != (ssize_t)strlen(cases[i].command) ||
			      mutations != 4 || pcd.fe_obs_armed)) ||
			    (state == 6 && !observer_disarm &&
			     (ret != -EBUSY || mutations ||
			      pcd.fe_refcount != 1 || !pcd.fe_obs_armed ||
			      pcd.fe_hash_off != 0x40)) ||
			    (!state && ret != (ssize_t)strlen(cases[i].command)) ||
			    unlocked_mutations || zero_offset_writes || pcd.fe_lock.locked) {
				fprintf(stderr, "%s state %u: ret=%zd mutations=%u unlocked=%u refs=%d\n",
					cases[i].name, state, ret, mutations,
					unlocked_mutations, pcd.fe_refcount);
				failures++;
			}
		}
		}
		for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
			struct fman_pcd pcd = {
				.fe_refcount = 1, .fe_hash_off = 0x40, .fe_obs_armed = true,
				.fe_obs_saved_hit = 0x1234, .fe_obs_saved_miss = 0x5678,
			};
			struct seq_file seq = { .private = &pcd };
			struct file file = { .private_data = &seq };
			loff_t off = 0;
			ssize_t ret;

			if (strcmp(cases[i].name, "pool put") &&
			    strcmp(cases[i].name, "singletons clear") &&
			    strcmp(cases[i].name, "hashfe clear"))
				continue;
			memset(&muram, 0, sizeof(muram));
			mutations = zero_offset_writes = 0;
			io_owner = &pcd;
			ret = cases[i].write(&file, cases[i].command,
					     strlen(cases[i].command), &off);
			off = 0;
			if (fman_pcd_fe_obs_write(&file, "disarm 10", 9, &off) != 9 ||
			    ret != -EBUSY || zero_offset_writes ||
			    muram.words[0x40 / sizeof(u32) + 5] != 0x1234 ||
			    muram.words[0x40 / sizeof(u32) + 6] != 0x5678) {
				fprintf(stderr, "%s followed by disarm: ret=%zd zero-offset writes=%u\n",
					cases[i].name, ret, zero_offset_writes);
				failures++;
			}
		}
		for (unsigned int missing = 0; missing < 2; missing++) {
			struct fman_pcd pcd = {
				.fe_refcount = missing ? 1 : 0,
				.fe_hash_off = missing ? 0 : 0x40,
				.fe_obs_armed = true,
			};
			struct seq_file seq = { .private = &pcd };
			struct file file = { .private_data = &seq };
			loff_t off = 0;
			ssize_t ret;

			mutations = zero_offset_writes = 0;
			io_owner = &pcd;
			ret = fman_pcd_fe_obs_write(&file, "disarm 10", 9, &off);
			if (ret != -ESHUTDOWN || mutations || !pcd.fe_obs_armed ||
			    pcd.fe_lock.locked) {
				fprintf(stderr, "disarm without %s: ret=%zd writes=%u\n",
					missing ? "hash" : "pool", ret, mutations);
				failures++;
			}
		}
	if (failures)
		return 1;
	puts("FE debugfs chain: tracked owners and armed observers block mutations");
	return 0;
}
