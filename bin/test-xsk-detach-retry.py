#!/usr/bin/env python3
"""Check the AF_XDP detach retry ownership contract after F-275 is applied."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def fail(message):
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


if len(sys.argv) != 2:
    fail("usage: test-xsk-detach-retry.py <af_xdp_pool_main.c>")

header = Path("include/net/xsk_buff_pool.h").read_text()
core = Path("net/xdp/xsk_buff_pool.c").read_text()
xsk_source = Path("net/xdp/xsk.c").read_text()
dpaa = Path(sys.argv[1]).read_text()

if "struct delayed_work work;" not in header:
    fail("XSK pool does not use delayed work")
if "int xp_clear_dev(struct xsk_buff_pool *pool);" not in header:
    fail("xp_clear_dev does not expose retry status")
if "INIT_DELAYED_WORK(&pool->work, xp_release_deferred);" not in core:
    fail("XSK pool retry work is not initialized at allocation")
if "schedule_delayed_work(&pool->work, 0);" not in core:
    fail("final pool release does not enter the deferred worker")
if "if (xp_disable_drv_zc(pool) == -EAGAIN)" not in core:
    fail("attach rollback does not preserve state on a retryable detach")
rollback_start = core.index("err_unreg_xsk:")
rollback_end = core.index("err_unreg_pool:", rollback_start)
if "return -EAGAIN;" not in core[rollback_start:rollback_end]:
    fail("failed-bind rollback does not propagate deferred cleanup to its caller")
if xsk_source.count("F-275: defer failed-bind cleanup to pool worker") != 2:
    fail("failed-bind callers may destroy a pool while detach retry is pending")

clear_start = core.index("int xp_clear_dev(struct xsk_buff_pool *pool)")
clear_end = core.index("static void xp_release_deferred", clear_start)
clear = core[clear_start:clear_end]
retry = clear.find("if (err == -EAGAIN)")
clear_pool = clear.find("xsk_clear_pool_at_qid")
drop_netdev = clear.find("dev_put(netdev)")
if min(retry, clear_pool, drop_netdev) < 0 or not retry < clear_pool < drop_netdev:
    fail("retryable detach can clear the XSK association or netdev reference")
if "schedule_delayed_work(&pool->work, HZ);" not in clear:
    fail("retryable detach is not rescheduled")
release_start = core.index("static void xp_release_deferred(struct work_struct *work)\n{")
release_end = core.index("\nvoid xp_get_pool", release_start)
release = core[release_start:release_end]
defer = release.find("err == -EAGAIN || refcount_read(&pool->users)")
destroy_queue = release.find("xskq_destroy(pool->fq)")
destroy_pool = release.find("xp_destroy(pool)")
if min(defer, destroy_queue, destroy_pool) < 0 or not defer < destroy_queue < destroy_pool:
    fail("deferred release can destroy a pool after a retryable detach")

detach_start = dpaa.index("static int af_xdp_pool_xsk_pool_detach")
detach_end = dpaa.index("\n}\n", detach_start)
detach = dpaa[detach_start:detach_end]
# The core pool worker retains the pool across -EAGAIN, so the driver keeps
# no pending-pool pointer; it must instead fail closed before freeing anything.
disable = detach.find("fman_port_disable(rxp)")
restore = detach.find("fman_port_set_rx_bpool(")
quiesce = detach.find("port->quiesced = true")
drain = detach.find("af_xdp_pool_drain_bman(port->bpool")
live_check = detach.find("if (live) {")
unpublish = detach.find("smp_store_release(&priv->xsk_port, NULL)")
dma_unmap = detach.find("xsk_pool_dma_unmap(port->pool")
free_bpool = detach.find("bman_free_pool(port->bpool)")
if min(disable, restore, quiesce, drain, live_check, unpublish, dma_unmap, free_bpool) < 0:
    fail("detach is missing a required quiesce or cleanup operation")
if not disable < restore < quiesce < drain < live_check < unpublish < dma_unmap < free_bpool:
    fail("detach releases or hides pool state before the port is quiet and drained")
if detach[:unpublish].count("return -EAGAIN;") < 4:
    fail("detach does not defer on port, BPID, drain or outstanding-buffer failure")
helper_start = dpaa.index("static int af_xdp_pool_drain_bman(")
helper = dpaa[helper_start:dpaa.index("\n}\n", helper_start)]
if "-ENOMEM" not in helper or "-ETIMEDOUT" in helper:
    fail("BMan drain must treat -ENOMEM as empty and other errors as retryable")

if not shutil.which("cc"):
    fail("host C compiler not found")


def extract(start, end):
    begin = core.index(start)
    finish = core.index(end, begin)
    return core[begin:finish]


functions = "\n".join(
    (
        extract("static int xp_disable_drv_zc(", "\n#define NETDEV_XDP_ACT_ZC"),
        extract("int xp_clear_dev(", "\nstatic void xp_release_deferred"),
        extract(
            "static void xp_release_deferred(struct work_struct *work)\n{",
            "\nvoid xp_get_pool",
        ),
        extract("bool xp_put_pool(", "\nstatic struct xsk_dma_map"),
    )
)

harness = r"""
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef uint16_t u16;
typedef int refcount_t;
struct xsk_buff_pool;
struct work_struct { int unused; };
struct delayed_work { struct work_struct work; };
struct xdp_umem { bool zc; };
struct netdev_bpf {
	int command;
	struct {
		struct xsk_buff_pool *pool;
		u16 queue_id;
	} xsk;
};
struct net_device;
struct net_device_ops {
	int (*ndo_bpf)(struct net_device *, struct netdev_bpf *);
};
struct net_device {
	struct net_device_ops *netdev_ops;
	unsigned int refs;
};
struct xsk_buff_pool {
	struct net_device *netdev;
	struct xdp_umem *umem;
	u16 queue_id;
	struct delayed_work work;
	refcount_t users;
	void *fq;
	void *cq;
};

#define XDP_SETUP_XSK_POOL 1
#define HZ 100
#define ASSERT_RTNL() do { } while (0)
#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))
#define to_delayed_work(w) container_of(w, struct delayed_work, work)
#define pr_warn_ratelimited(...) do { } while (0)

static int driver_result;
static unsigned int driver_calls, association_clears, dev_puts;
static unsigned int delayed_schedules, queue_destroys, umem_puts, pool_destroys;
static struct net_device_ops mock_ops;

static int mock_ndo_bpf(struct net_device *dev, struct netdev_bpf *bpf)
{
	(void)dev;
	assert(bpf->command == XDP_SETUP_XSK_POOL);
	assert(bpf->xsk.pool == NULL);
	driver_calls++;
	return driver_result;
}

static void netdev_lock_ops(struct net_device *dev) { (void)dev; }
static void netdev_unlock_ops(struct net_device *dev) { (void)dev; }
static void xsk_clear_pool_at_qid(struct net_device *dev, u16 qid)
{
	(void)dev; (void)qid; association_clears++;
}
static void dev_put(struct net_device *dev) { dev->refs--; dev_puts++; }
static void schedule_delayed_work(struct delayed_work *work, unsigned long delay)
{
	(void)work; (void)delay; delayed_schedules++;
}
static void rtnl_lock(void) { }
static void rtnl_unlock(void) { }
static void xskq_destroy(void *queue) { (void)queue; queue_destroys++; }
static void xdp_put_umem(struct xdp_umem *umem, bool defer)
{
	(void)umem; (void)defer; umem_puts++;
}
static void xp_destroy(struct xsk_buff_pool *pool)
{
	(void)pool; pool_destroys++;
}
static bool refcount_dec_and_test(refcount_t *users)
{
	return --*users == 0;
}
static int refcount_read(refcount_t *users) { return *users; }

__FUNCTIONS__

static void reset(struct xsk_buff_pool *pool, struct net_device *dev,
		  struct xdp_umem *umem)
{
	*pool = (struct xsk_buff_pool) {
		.netdev = dev, .umem = umem, .queue_id = 2,
		.users = 0, .fq = (void *)1, .cq = (void *)2,
	};
	*dev = (struct net_device) {
		.netdev_ops = &mock_ops,
		.refs = 1,
	};
	mock_ops.ndo_bpf = mock_ndo_bpf;
	*umem = (struct xdp_umem){ .zc = true };
	driver_result = 0;
	driver_calls = association_clears = dev_puts = 0;
	delayed_schedules = queue_destroys = umem_puts = pool_destroys = 0;
}

int main(void)
{
	struct xsk_buff_pool pool;
	struct net_device dev;
	struct xdp_umem umem;

	reset(&pool, &dev, &umem);
	driver_result = -EAGAIN;
	assert(xp_clear_dev(&pool) == -EAGAIN);
	assert(pool.netdev == &dev && dev.refs == 1);
	assert(!association_clears && !dev_puts && delayed_schedules == 1);
	driver_result = 0;
	assert(xp_clear_dev(&pool) == 0);
	assert(!pool.netdev && dev.refs == 0);
	assert(association_clears == 1 && dev_puts == 1);

	reset(&pool, &dev, &umem);
	driver_result = -EAGAIN;
	xp_release_deferred(&pool.work.work);
	assert(pool.netdev == &dev && dev.refs == 1);
	assert(!queue_destroys && !umem_puts && !pool_destroys);
	assert(delayed_schedules == 1);
	driver_result = 0;
	xp_release_deferred(&pool.work.work);
	assert(!pool.netdev && dev.refs == 0);
	assert(queue_destroys == 2 && umem_puts == 1 && pool_destroys == 1);

	reset(&pool, &dev, &umem);
	pool.users = 1;
	assert(xp_put_pool(&pool));
	assert(!pool.users && delayed_schedules == 1);
	assert(!pool_destroys);

	return 0;
}
"""

with tempfile.TemporaryDirectory(prefix="xsk-detach-test-") as tmp:
    c_file = Path(tmp) / "test.c"
    binary = Path(tmp) / "test"
    c_file.write_text(harness.replace("__FUNCTIONS__", functions))
    result = subprocess.run(
        ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", str(c_file), "-o", str(binary)],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        fail(f"host harness compile failed:\n{result.stderr}")
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    if result.returncode:
        fail(f"host timeout/retry case failed:\n{result.stderr}")

print("PASS: XSK association, UMEM/DMA lifetime, and netdev reference are retained on -EAGAIN")
