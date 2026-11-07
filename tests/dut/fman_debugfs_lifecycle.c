/* Host fault tests for debugfs FE lifecycle admission. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <sys/types.h>

#define __user
#define FMAN_FPM_EXTC_INV0 0x80000000U
#define FMAN_FPM_EXTC_POLL_MAX 100000U
#define BITS_PER_LONG (sizeof(unsigned long) * 8)
#define pr_warn_ratelimited(...) ((void)0)
#define dev_info(dev, ...) ((void)(dev))
#define dev_warn(dev, ...) ((void)(dev))

typedef unsigned char u8;
typedef unsigned int u32;
struct mutex { bool locked; };
struct fman { int unused; };
struct fman_pcd_fe_port { void *table_v4; };
struct fman_pcd {
	struct mutex fe_lock;
	struct fman *fman;
	unsigned long fe_port_armed[2], fe_port_faulted[2];
	struct fman_pcd_fe_port *ports[0x28];
	unsigned long fe_root_ad_off;
	unsigned int fe_refcount, fe_miss_fqid;
	bool fe_vm_chain_faulted;
	void *fe_arming_v4_table;
};
struct seq_file { void *private; };
struct file { void *private_data; };

static unsigned int mutation_calls;
static unsigned int register_reads;
static bool copy_fails;
static u32 extc_value;
static char show_output[1024];
static size_t show_length;

static bool test_bit(unsigned int bit, const unsigned long *bitmap)
{
	return bitmap[bit / BITS_PER_LONG] & (1UL << (bit % BITS_PER_LONG));
}

static void set_bit(unsigned int bit, unsigned long *bitmap)
{
	bitmap[bit / BITS_PER_LONG] |= 1UL << (bit % BITS_PER_LONG);
}

static __attribute__((unused)) bool
bitmap_empty(const unsigned long *bitmap, unsigned int bits)
{
	unsigned int i;

	for (i = 0; i < bits; i++)
		if (test_bit(i, bitmap))
			return false;
	return true;
}

static __attribute__((unused)) struct fman_pcd_fe_port *
fman_pcd_fe_port_find(struct fman_pcd *pcd, u8 port)
{
	return pcd->ports[port];
}

static __attribute__((unused)) struct fman *
fman_pcd_get_fman(struct fman_pcd *pcd) { return pcd->fman; }
static __attribute__((unused)) void *fman_get_dev(struct fman *fman) { return NULL; }
static u32 fman_get_fpm_extc(struct fman *fman)
{
	register_reads++;
	return extc_value;
}
static __attribute__((unused)) void fman_set_fpm_extc(struct fman *fman, u32 value)
{
	mutation_calls++;
}
static __attribute__((unused)) void udelay(unsigned int us) { }

static void seq_printf(struct seq_file *seq, const char *format, ...)
{
	va_list ap;
	int n;

	va_start(ap, format);
	n = vsnprintf(show_output + show_length, sizeof(show_output) - show_length,
		      format, ap);
	va_end(ap);
	assert(n >= 0 && (size_t)n < sizeof(show_output) - show_length);
	show_length += (size_t)n;
}

static void seq_puts(struct seq_file *seq, const char *text)
{
	seq_printf(seq, "%s", text);
}

static void seq_putc(struct seq_file *seq, char c)
{
	seq_printf(seq, "%c", c);
}

static __attribute__((unused)) void mutex_lock(struct mutex *lock)
{
	assert(!lock->locked);
	lock->locked = true;
}

static __attribute__((unused)) void mutex_unlock(struct mutex *lock)
{
	assert(lock->locked);
	lock->locked = false;
}

static int copy_from_user(char *dst, const char *src, size_t count)
{
	if (copy_fails)
		return 1;
	memcpy(dst, src, count);
	return 0;
}

static __attribute__((unused)) int
fman_pcd_fe_port_set(struct fman_pcd *pcd, u8 port)
{
	assert(pcd->fe_lock.locked);
	mutation_calls++;
	return 0;
}

static __attribute__((unused)) int
fman_pcd_fe_port_del(struct fman_pcd *pcd, u8 port)
{
	assert(pcd->fe_lock.locked);
	mutation_calls++;
	return 0;
}

static __attribute__((unused)) int
fman_pcd_fe_arm_engage(struct fman_pcd *pcd, const char *buf)
{
	assert(pcd->fe_lock.locked);
	mutation_calls++;
	return 0;
}

static __attribute__((unused)) int
fman_pcd_fe_arm_disengage(struct fman_pcd *pcd, const char *buf)
{
	assert(pcd->fe_lock.locked);
	mutation_calls++;
	return 0;
}

#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wsign-compare"
#include "debugfs_lifecycle_body.h"
#pragma GCC diagnostic pop

int main(void)
{
	struct fman fman = {};
	struct fman_pcd pcd = { .fman = &fman };
	struct fman_pcd_fe_port port = {};
	struct seq_file seq = { .private = &pcd };
	struct file file = { .private_data = &seq };
	loff_t off = 0;
	const char *commands[] = { "set 10", "del 10", "engage 10 123", "disengage 10" };
	size_t i;

	for (i = 0; i < sizeof(commands) / sizeof(commands[0]); i++) {
		ssize_t ret = i < 2 ?
			fman_pcd_fe_port_write(&file, commands[i], strlen(commands[i]), &off) :
			fman_pcd_fe_arm_write(&file, commands[i], strlen(commands[i]), &off);

		assert(ret == -EOPNOTSUPP);
		assert(!mutation_calls && !pcd.fe_lock.locked);
	}
	assert(fman_pcd_fe_extc_write(&file, "sync", 4, &off) == -EOPNOTSUPP);
	assert(!mutation_calls);
	extc_value = FMAN_FPM_EXTC_INV0;
	assert(!fman_pcd_fe_extc_show(&seq, NULL));
	assert(register_reads == 1 && !mutation_calls);
	assert(strstr(show_output, "fmfp_extc: 0x80000000 (INV0=1)\n"));
	assert(strstr(show_output, "sync writes disabled"));
	show_length = 0;
	show_output[0] = '\0';
	assert(!fman_pcd_fe_arm_show(&seq, NULL));
	assert(strstr(show_output, "Armed ports: (none)\n"));
	assert(strstr(show_output, "Faulted ports: (none)\n"));
	assert(strstr(show_output, "FE fault/staging: no\n"));

	pcd.ports[0x11] = &port;
	show_length = 0;
	show_output[0] = '\0';
	assert(!fman_pcd_fe_arm_show(&seq, NULL));
	assert(strstr(show_output, "FE fault/staging: yes\n"));
	set_bit(0x11, pcd.fe_port_armed);
	show_length = 0;
	show_output[0] = '\0';
	assert(!fman_pcd_fe_arm_show(&seq, NULL));
	assert(strstr(show_output, "FE fault/staging: yes\n"));
	port.table_v4 = &pcd;
	show_length = 0;
	show_output[0] = '\0';
	assert(!fman_pcd_fe_arm_show(&seq, NULL));
	assert(strstr(show_output, "FE fault/staging: no\n"));
	pcd.ports[0x11] = NULL;
	show_length = 0;
	show_output[0] = '\0';
	assert(!fman_pcd_fe_arm_show(&seq, NULL));
	assert(strstr(show_output, "FE fault/staging: yes\n"));
	memset(pcd.fe_port_armed, 0, sizeof(pcd.fe_port_armed));

	set_bit(0x1f, pcd.fe_port_armed);
	set_bit(0x20, pcd.fe_port_armed);
	set_bit(0x27, pcd.fe_port_armed);
	set_bit(0x10, pcd.fe_port_faulted);
	set_bit(0x27, pcd.fe_port_faulted);
	show_length = 0;
	show_output[0] = '\0';
	assert(!fman_pcd_fe_arm_show(&seq, NULL));
	assert(strstr(show_output, "Armed ports: 0x1f 0x20 0x27\n"));
	assert(strstr(show_output, "Faulted ports: 0x10 0x27\n"));
	assert(strstr(show_output, "FE fault/staging: yes\n"));
	assert(!pcd.fe_lock.locked);

	memset(pcd.fe_port_faulted, 0, sizeof(pcd.fe_port_faulted));
	pcd.fe_vm_chain_faulted = true;
	show_length = 0;
	show_output[0] = '\0';
	assert(!fman_pcd_fe_arm_show(&seq, NULL));
	assert(strstr(show_output, "Faulted ports: (none)\n"));
	assert(strstr(show_output, "FE fault/staging: yes\n"));
	pcd.fe_vm_chain_faulted = false;
	pcd.fe_arming_v4_table = &pcd;
	show_length = 0;
	show_output[0] = '\0';
	assert(!fman_pcd_fe_arm_show(&seq, NULL));
	assert(strstr(show_output, "FE fault/staging: yes\n"));
	pcd.fe_arming_v4_table = NULL;
	assert(fman_pcd_fe_port_write(&file, "bad", 3, &off) == -EINVAL);
	assert(fman_pcd_fe_arm_write(&file, "bad", 3, &off) == -EINVAL);
	assert(fman_pcd_fe_extc_write(&file, "bad", 3, &off) == -EINVAL);
	assert(fman_pcd_fe_port_write(&file, "set 10", 0, &off) == -EINVAL);
	assert(fman_pcd_fe_arm_write(&file, "engage 10", 64, &off) == -EINVAL);
	assert(fman_pcd_fe_extc_write(&file, "sync", 0, &off) == -EINVAL);
	assert(fman_pcd_fe_extc_write(&file, "sync", 16, &off) == -EINVAL);
	copy_fails = true;
	assert(fman_pcd_fe_port_write(&file, "set 10", 6, &off) == -EFAULT);
	assert(fman_pcd_fe_arm_write(&file, "engage 10", 9, &off) == -EFAULT);
	assert(fman_pcd_fe_extc_write(&file, "sync", 4, &off) == -EFAULT);
	copy_fails = false;
	seq.private = NULL;
	assert(fman_pcd_fe_port_write(&file, "set 10", 6, &off) == -EINVAL);
	assert(fman_pcd_fe_arm_write(&file, "engage 10", 9, &off) == -EINVAL);
	assert(fman_pcd_fe_extc_write(&file, "sync", 4, &off) == -EINVAL);
	file.private_data = NULL;
	assert(fman_pcd_fe_port_write(&file, "set 10", 6, &off) == -EINVAL);
	assert(fman_pcd_fe_arm_write(&file, "engage 10", 9, &off) == -EINVAL);
	assert(fman_pcd_fe_extc_write(&file, "sync", 4, &off) == -EINVAL);
	assert(!mutation_calls);
	puts("FE debugfs lifecycle: mutations refused before touching owners");
	return 0;
}
