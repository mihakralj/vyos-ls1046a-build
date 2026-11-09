/* Host-side mocks for the actual WGFP sink creation and debugfs control code. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>

typedef unsigned long long u64;

#define __user
#define loff_t long long
#define WGFP_MAX_SINKS 4
#define WGFP_MAX_PORTS 8
#define WGFP_WQ 6
#define WGFP_SINK_TD (512 * 1024)
#define QMAN_FQ_FLAG_DYNAMIC_FQID 1
#define QMAN_FQ_FLAG_NO_ENQUEUE 2
#define QMAN_FQ_FLAG_NO_MODIFY 3
#define QMAN_INITFQ_FLAG_SCHED 1
#define QM_INITFQ_WE_FQCTRL 1
#define QM_INITFQ_WE_DESTWQ 2
#define QM_INITFQ_WE_TDTHRESH 4
#define QM_FQCTRL_PREFERINCACHE 1
#define QM_FQCTRL_TDE 2
#define cpu_to_be16(x) (x)
#define WARN_ON_ONCE(x) (x)
#define WRITE_ONCE(x, value) ((x) = (value))
#define READ_ONCE(x) (x)
#define pr_err(...) fake_log(__VA_ARGS__)
#define pr_info(...) fake_log(__VA_ARGS__)
#define pr_warn_ratelimited(...) fake_log(__VA_ARGS__)
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define ERR_PTR(e) ((void *)(long)(e))
#define PTR_ERR(p) ((long)(p))
#define IS_ERR(p) ((long)(p) < 0 && (long)(p) >= -4095)
#define atomic_set(p, v) (*(p) = (v))

struct qman_portal;
union qm_mr_entry;
struct qman_fq {
	struct {
		int (*dqrr)(void);
		void (*ern)(struct qman_portal *, struct qman_fq *,
			    const union qm_mr_entry *);
	} cb;
	unsigned int fqid;
};
struct napi_struct { int unused; };
struct wgfp_port;
struct wgfp_sink {
	struct qman_fq fq;
	struct napi_struct napi;
	struct wgfp_port *port;
	int cpu;
	bool created;
	bool dist;
	bool napi_added;
	uint64_t frames, bytes, errs, sg, polls, irqs, sum;
};
struct wgfp_port {
	int ifindex;
	unsigned int nsinks;
	bool dist;
	int rr;
	struct wgfp_sink dist_sink;
	struct wgfp_sink sink[WGFP_MAX_SINKS];
};
struct net_device {
	int ifindex;
	const char *name;
};
struct qm_mcc_initfq {
	unsigned short we_mask;
	struct { unsigned short fq_ctrl; } fqd;
};
struct cpumask { int unused; };
struct file { int unused; };

static struct wgfp_sink wgfp_global[WGFP_MAX_SINKS];
static unsigned int wgfp_nglobal;
static struct wgfp_port wgfp_ports[WGFP_MAX_PORTS];
static unsigned int wgfp_nports;
static unsigned int wgfp_touch = 2, wgfp_per_port;
static unsigned int wgfp_fanout = 32;
#define WGFP_FANOUT_MAX 1024
static unsigned int wgfp_fanout_home;
#define WGFP_FANOUT_HOME_MAX 64
static bool wgfp_pool_failed;
static struct qman_fq wgfp_rx_ern_fq;
static bool wgfp_rx_ern_ready;
static int wgfp_lock, wgfp_ndev;
static int create_calls, init_calls, destroyed, napi_enabled;
static int fail_create, fail_init, fail_taildrop, offline_cpu = -1;
static struct cpumask affine;

static void fake_log(const char *fmt, ...) { (void)fmt; }
static int wgfp_dqrr(void) { return 0; }
static void wgfp_rx_ern_cb(struct qman_portal *p, struct qman_fq *f,
			   const union qm_mr_entry *m) { }
static int wgfp_poll(struct napi_struct *napi, int budget) { return budget; }
static void mutex_lock(int *lock) { (void)lock; }
static void mutex_unlock(int *lock) { (void)lock; }
static int copy_from_user(char *dst, const char *src, size_t n)
{
	memcpy(dst, src, n);
	return 0;
}
static const struct cpumask *qman_affine_cpus(void) { return &affine; }
static bool cpumask_test_cpu(unsigned int cpu, const struct cpumask *mask) { return true; }
static bool cpu_online(unsigned int cpu) { return (int)cpu != offline_cpu; }
static int num_online_cpus(void) { return 4; }
static int qman_affine_channel(int cpu) { return cpu + 1; }
static void qm_fqd_set_destwq(void *fqd, int channel, int wq) {}
static int qm_fqd_set_taildrop(void *fqd, int size, int round) { return fail_taildrop; }
static int qman_create_fq(unsigned int fqid, unsigned int flags, struct qman_fq *fq)
{
	create_calls++;
	if (create_calls == fail_create)
		return -ENOSPC;
	fq->fqid = 100 + create_calls;
	return 0;
}
static unsigned int qman_fq_fqid(struct qman_fq *fq) { return fq->fqid; }
static void netif_napi_add(int dev, struct napi_struct *napi,
			   int (*poll)(struct napi_struct *, int)) {}
static void napi_enable(struct napi_struct *napi) { napi_enabled++; }
static int qman_init_fq(struct qman_fq *fq, unsigned int flags, struct qm_mcc_initfq *opts)
{
	init_calls++;
	return init_calls == fail_init ? -ETIMEDOUT : 0;
}

/* The old implementation called this on partially initialized or live FQs. */
static void __attribute__((unused)) wgfp_sink_free(struct wgfp_sink *s)
{
	if (s->created) {
		destroyed++;
		s->created = false;
	}
}

#include "wgfp_sink_body.h"

static void reset(void)
{
	memset(wgfp_global, 0, sizeof(wgfp_global));
	memset(wgfp_ports, 0, sizeof(wgfp_ports));
	wgfp_nglobal = 0;
	wgfp_nports = 0;
	wgfp_per_port = 0;
	wgfp_pool_failed = false;
	create_calls = init_calls = destroyed = napi_enabled = 0;
	fail_create = fail_init = fail_taildrop = 0;
	offline_cpu = -1;
}

static ssize_t control(const char *cmd)
{
	loff_t offset = 0;

	return wgfp_ctl_write(NULL, cmd, strlen(cmd), &offset);
}

static void successful_pool_cannot_be_destroyed(void)
{
	reset();
	assert(wgfp_global_create(2) == 0);
	assert(wgfp_nglobal == 2 && create_calls == 2 && init_calls == 2);
	assert(control("destroy") == -EOPNOTSUPP);
	assert(wgfp_nglobal == 2 && wgfp_global[0].created && wgfp_global[1].created);
	assert(destroyed == 0 && napi_enabled == 2);
	assert(wgfp_global_create(2) == -EBUSY);
}

static void failed_init_quarantines_the_pool(void)
{
	reset();
	fail_init = 2;
	assert(wgfp_global_create(4) == -ETIMEDOUT);
	assert(wgfp_pool_failed && wgfp_nglobal == 0);
	assert(wgfp_global[0].created && wgfp_global[1].created);
	assert(create_calls == 2 && init_calls == 2 && napi_enabled == 2);
	assert(destroyed == 0);
	assert(wgfp_global_create(4) == -EIO && create_calls == 2);
	assert(control("destroy") == -EOPNOTSUPP && destroyed == 0);
}

static void failed_allocation_keeps_previously_created_sinks(void)
{
	reset();
	fail_create = 2;
	assert(wgfp_global_create(4) == -ENOSPC);
	assert(wgfp_pool_failed && wgfp_global[0].created && !wgfp_global[1].created);
	assert(destroyed == 0 && napi_enabled == 1);
	assert(wgfp_global_create(1) == -EIO && create_calls == 2);
}

static void invalid_requests_do_not_poison_the_pool(void)
{
	reset();
	assert(wgfp_global_create(0) == -EINVAL);
	assert(wgfp_global_create(5) == -EINVAL);
	offline_cpu = 1;
	assert(wgfp_global_create(2) == -ENODEV);
	assert(!wgfp_pool_failed && create_calls == 0);
	offline_cpu = -1;
	assert(wgfp_global_create(2) == 0);
}

static void per_port_workers_carry_their_port(void)
{
	struct net_device eth3 = { .ifindex = 3, .name = "eth3" };
	struct net_device eth4 = { .ifindex = 4, .name = "eth4" };
	struct wgfp_port *p3, *p4;

	reset();
	assert(control("create 2") > 0);
	assert(wgfp_per_port == 2);
	p3 = wgfp_port_get_locked(&eth3);
	assert(!IS_ERR(p3) && wgfp_nports == 1);
	assert(p3->nsinks == 2 && p3->ifindex == 3);
	assert(p3->sink[0].port == p3 && p3->sink[1].port == p3);
	assert(!p3->sink[0].dist);
	assert(wgfp_port_get_locked(&eth3) == p3 && wgfp_nports == 1);
	p4 = wgfp_port_get_locked(&eth4);
	assert(!IS_ERR(p4) && wgfp_nports == 2 && p4->ifindex == 4);
	assert(p3->sink[0].fq.fqid != p4->sink[0].fq.fqid);
	assert(wgfp_port_dist_locked(p3) == 0 && p3->dist);
	assert(p3->dist_sink.dist && p3->dist_sink.port == p3);
	assert(wgfp_port_dist_locked(p3) == 0 && create_calls == 8);
}

int main(void)
{
	successful_pool_cannot_be_destroyed();
	failed_init_quarantines_the_pool();
	failed_allocation_keeps_previously_created_sinks();
	invalid_requests_do_not_poison_the_pool();
	per_port_workers_carry_their_port();
	assert(control("fanout 0") > 0 && wgfp_fanout == 0);
	assert(control("fanout 64") > 0 && wgfp_fanout == 64);
	assert(control("fanout 1025") == -EINVAL && wgfp_fanout == 64);
	assert(control("fanout 32 5") > 0 && wgfp_fanout == 32 && wgfp_fanout_home == 5);
	assert(control("fanout 16") > 0 && wgfp_fanout == 16 && wgfp_fanout_home == 5);
	assert(control("fanout 16 1") == -EINVAL && wgfp_fanout_home == 5);
	assert(control("fanout 16 65") == -EINVAL && wgfp_fanout_home == 5);
	assert(control("fanout 2000 4") == -EINVAL && wgfp_fanout == 16);
	assert(control("fanout 32 0") > 0 && wgfp_fanout_home == 0);
	return 0;
}