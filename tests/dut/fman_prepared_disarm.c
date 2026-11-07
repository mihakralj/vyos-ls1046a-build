/* Host fault and concurrency tests for F-291's prepared disarm boundary. */
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
#define pr_warn(...) ((void)0)
#define pr_warn_ratelimited(...) ((void)0)
#define pr_err(...) ((void)0)

struct mutex { pthread_mutex_t native; };
struct list_head { bool populated; };
struct fman_pcd_ehash_table {
	struct list_head node;
	struct list_head flows, quarantined_flows, quarantined_cu;
	bool poisoned, draining;
};
struct fman_pcd_fe_port { struct fman_pcd_ehash_table *table_v4; };
struct fman_pcd {
	struct mutex fe_lock;
	unsigned long fe_port_armed[1], fe_port_faulted[1];
	void *fe_arming_v4_table;
	struct list_head fe_ehash_tables;
	struct fman_pcd_ehash_table table, shared;
	struct fman_pcd_fe_port port;
	bool port_present;
};
struct fman { struct fman_pcd *pcd; };

static struct fman_pcd_ehash_table *tables[2];
static int core_calls, core_rc;

#define list_for_each_entry(pos, head, member) \
	for (unsigned int i = 0; \
	     (void)(head), i < 2 && ((pos) = tables[i], true); i++)

static void mutex_lock(struct mutex *lock)
{
	assert(!pthread_mutex_lock(&lock->native));
}

static void mutex_unlock(struct mutex *lock)
{
	assert(!pthread_mutex_unlock(&lock->native));
}

static struct fman_pcd *fman_get_pcd(struct fman *fm) { return fm->pcd; }
static bool list_empty(struct list_head *head) { return !head->populated; }
static bool test_bit(unsigned int bit, unsigned long *map) { return *map & (1UL << bit); }
static void set_bit(unsigned int bit, unsigned long *map) { *map |= 1UL << bit; }
static void clear_bit(unsigned int bit, unsigned long *map) { *map &= ~(1UL << bit); }
static struct fman_pcd_fe_port *fman_pcd_fe_port_find(struct fman_pcd *pcd, u8 port)
{
	assert(port == 0x10);
	return pcd->port_present ? &pcd->port : NULL;
}

static int __fman_pcd_fe_disengage_locked(struct fman *fm, u8 port)
{
	assert(pthread_mutex_trylock(&fm->pcd->fe_lock.native) == EBUSY);
	core_calls++;
	if (!core_rc)
		clear_bit(port, fm->pcd->fe_port_armed);
	return core_rc;
}

#include "prepared_disarm_body.h"

struct prep {
	struct fman_pcd *pcd;
	int calls, rc;
	bool wait;
};

static pthread_mutex_t coordination = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t changed = PTHREAD_COND_INITIALIZER;
static bool entered, writer_started, writer_done, proceed;
static int writer_rc, disarm_rc;

static int prepare(void *data)
{
	struct prep *p = data;

	assert(pthread_mutex_trylock(&p->pcd->fe_lock.native) == EBUSY);
	p->calls++;
	if (p->wait) {
		assert(!pthread_mutex_lock(&coordination));
		entered = true;
		assert(!pthread_cond_broadcast(&changed));
		while (!proceed)
			assert(!pthread_cond_wait(&changed, &coordination));
		assert(!pthread_mutex_unlock(&coordination));
	}
	return p->rc;
}

static void *disarm_thread(void *data)
{
	struct prep *p = data;
	struct fman fm = { .pcd = p->pcd };

	disarm_rc = fman_pcd_fe_disengage_prepared(&fm, 0x10, prepare, p);
	return NULL;
}

static void *publisher_thread(void *data)
{
	struct fman_pcd *pcd = data;

	assert(!pthread_mutex_lock(&coordination));
	while (!entered)
		assert(!pthread_cond_wait(&changed, &coordination));
	writer_started = true;
	assert(!pthread_cond_broadcast(&changed));
	assert(!pthread_mutex_unlock(&coordination));

	mutex_lock(&pcd->fe_lock);
	writer_rc = test_bit(0x10, pcd->fe_port_armed) ? 0 : -ENODEV;
	mutex_unlock(&pcd->fe_lock);

	assert(!pthread_mutex_lock(&coordination));
	writer_done = true;
	assert(!pthread_cond_broadcast(&changed));
	assert(!pthread_mutex_unlock(&coordination));
	return NULL;
}

int main(void)
{
	struct fman_pcd pcd = { 0 };
	struct fman fm = { .pcd = &pcd };
	struct prep p = { .pcd = &pcd };
	pthread_t disarm, writer;

	assert(!pthread_mutex_init(&pcd.fe_lock.native, NULL));
	pcd.port_present = true;
	pcd.port.table_v4 = &pcd.table;
	tables[0] = &pcd.table;
	tables[1] = &pcd.shared;
	set_bit(0x10, pcd.fe_port_armed);

	assert(fman_pcd_fe_disengage_prepared(NULL, 0x10, prepare, &p) == -EINVAL);
	assert(fman_pcd_fe_disengage_prepared(&fm, 0x10, NULL, &p) == -EINVAL);
	assert(fman_pcd_fe_disengage_prepared(&fm, 0x07, prepare, &p) == -EINVAL);
	pcd.shared.flows.populated = true;
	assert(fman_pcd_fe_disengage_prepared(&fm, 0x10, prepare, &p) == -EBUSY);
	assert(!p.calls && !core_calls && test_bit(0x10, pcd.fe_port_armed));
	pcd.shared.flows.populated = false;

	p.rc = -EIO;
	assert(fman_pcd_fe_disengage_prepared(&fm, 0x10, prepare, &p) == -EIO);
	assert(p.calls == 1 && !core_calls && test_bit(0x10, pcd.fe_port_armed));
	p.rc = 0;
	core_rc = -EBUSY;
	assert(fman_pcd_fe_disengage_prepared(&fm, 0x10, prepare, &p) == -EBUSY);
	assert(p.calls == 2 && core_calls == 1 && test_bit(0x10, pcd.fe_port_armed));
	core_rc = 0;

	p.wait = true;
	assert(!pthread_create(&disarm, NULL, disarm_thread, &p));
	assert(!pthread_create(&writer, NULL, publisher_thread, &pcd));
	assert(!pthread_mutex_lock(&coordination));
	while (!entered || !writer_started)
		assert(!pthread_cond_wait(&changed, &coordination));
	assert(!writer_done);
	proceed = true;
	assert(!pthread_cond_broadcast(&changed));
	assert(!pthread_mutex_unlock(&coordination));
	assert(!pthread_join(disarm, NULL));
	assert(!pthread_join(writer, NULL));
	assert(!disarm_rc && writer_done && writer_rc == -ENODEV);
	assert(p.calls == 3 && core_calls == 2);

	pcd.port_present = false;
	assert(fman_pcd_fe_disengage_prepared(&fm, 0x10, prepare, &p) == -ENODEV);
	assert(p.calls == 3 && core_calls == 2);
	assert(!pthread_mutex_destroy(&pcd.fe_lock.native));
	puts("FE prepared disarm: busy preserves state and blocks publishers");
	return 0;
}
