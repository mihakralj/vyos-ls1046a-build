// SPDX-License-Identifier: GPL-2.0
/*
 * WireGuard fast path lab, phase P1 (plans/ASK2-WIREGUARD-FASTPATH-PLAN.md).
 *
 * Question answered here: how many frames per second can the CPUs drain from
 * QMan frame queues WITHOUT building an sk_buff, when an FMan flow record
 * (ehash, `fe_flow add <tbl> <key> <fqid>`) enqueues matching frames straight
 * to a software-owned queue?  The fast path can never go faster than this.
 *
 * One sink frame queue per CPU, on that CPU's affine (dedicated) channel, so a
 * flow pinned to a queue is dequeued in order on a single portal.  The
 * consumer follows the dpaa_eth pattern exactly: the portal interrupt handler
 * calls the callback with sched_napi == true, the callback masks the DQRR
 * interrupt and schedules the sink's NAPI, and the NAPI poll runs
 * qman_p_poll_dqrr(), which calls the callback again with sched_napi == false
 * to process the frame.  Another driver's NAPI polling the same portal may
 * dequeue our frames too; that is why the callback never relies on being in
 * "its own" NAPI context.
 *
 * Per frame: sync the buffer for the CPU, optionally touch it (see
 * "touch"), sync it back to the device and return it to its BMan pool.  No
 * sk_buff, no copy.
 *
 * Control (debugfs, default off, nothing happens until "create"):
 *   echo "create <n>"  > /sys/kernel/debug/dpaa_wgfp/ctl   n sink FQs on CPUs 0..n-1
 *   echo "touch <m>"   > .../ctl   0 none, 1 first and last 64 B, 2 read the
 *                                  whole frame, 3 read and write the whole frame
 *   echo "reset"       > .../ctl   zero the counters
 *   echo "destroy"     > .../ctl   retire and free the queues
 *   cat .../stats                  fqid, cpu, frames, bytes, errors, polls, FQ state
 */

#include <linux/debugfs.h>
#include <linux/dma-mapping.h>
#include <linux/netdevice.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/fsl/dpaa_flow_offload.h>
#include <linux/fsl/dpaa_wgfp.h>
#include <linux/rcupdate.h>
#include <soc/fsl/qman.h>

/* Helpers living in dpaa_eth.c (file-local state there). */
struct device *dpaa_wgfp_dma_dev(u8 bpid);
bool dpaa_wgfp_fd_has_error(const struct qm_fd *fd);
void dpaa_wgfp_fd_release(const struct qm_fd *fd);

#define WGFP_MAX_SINKS	4
/* Usable bytes of a DPAA RX buffer (dpaa_bp_size of the 4096-byte raw size). */
#define DPAA_WGFP_BUF_SIZE	3584
#define WGFP_WQ		3

struct wgfp_sink {
	struct qman_fq fq;
	struct napi_struct napi;
	struct qman_portal *portal;
	int cpu;
	bool created;
	bool napi_added;
	u64 frames;
	u64 bytes;
	u64 errs;
	u64 sg;
	u64 polls;
	u64 irqs;
	u64 sum;	/* defeats dead-code elimination of the touch loop */
};

static struct wgfp_sink wgfp_sinks[WGFP_MAX_SINKS];
static struct net_device *wgfp_ndev;
static struct dentry *wgfp_dir;
static unsigned int wgfp_touch = 2;
static unsigned int wgfp_nsinks;
static DEFINE_MUTEX(wgfp_lock);

/* Consumer hook (the wireguard module) and the egress frame queue. */
static const struct dpaa_wgfp_ops __rcu *wgfp_ops;
static void *wgfp_ops_priv;
static struct qman_fq wgfp_txfq;
static bool wgfp_tx_ready;
static atomic64_t wgfp_xmit_ok = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_fail = ATOMIC64_INIT(0);

static void wgfp_touch_frame(struct wgfp_sink *s, u8 *p, u32 len)
{
	u64 sum = 0;
	u32 i;

	switch (wgfp_touch) {
	case 0:
		return;
	case 1:
		for (i = 0; i < 64 / 8 && i * 8 + 8 <= len; i++)
			sum += ((const u64 *)p)[i];
		if (len > 64)
			for (i = 0; i < 64 / 8; i++)
				sum += ((const u64 *)(p + len - 64))[i];
		break;
	case 2:
		for (i = 0; i + 8 <= len; i += 8)
			sum += *(const u64 *)(p + i);
		break;
	default:
		for (i = 0; i + 8 <= len; i += 8) {
			u64 *q = (u64 *)(p + i);

			*q ^= 0x5a5a5a5a5a5a5a5aULL;
			sum += *q;
		}
		break;
	}
	s->sum += sum;
}

static enum qman_cb_dqrr_result wgfp_dqrr(struct qman_portal *portal,
					  struct qman_fq *fq,
					  const struct qm_dqrr_entry *dq,
					  bool sched_napi)
{
	struct wgfp_sink *s = container_of(fq, struct wgfp_sink, fq);
	const struct qm_fd *fd = &dq->fd;
	const struct dpaa_wgfp_ops *ops;
	struct device *dev;
	dma_addr_t addr;
	u32 off, len;
	u8 *p;

	if (sched_napi) {
		/* Same hand-off as dpaa_eth_napi_schedule(). */
		qman_p_irqsource_remove(portal, QM_PIRQ_DQRI);
		s->portal = portal;
		s->irqs++;
		napi_schedule(&s->napi);
		return qman_cb_dqrr_stop;
	}

	if (unlikely(dpaa_wgfp_fd_has_error(fd))) {
		s->errs++;
		dpaa_wgfp_fd_release(fd);
		return qman_cb_dqrr_consume;
	}
	if (unlikely(qm_fd_get_format(fd) != qm_fd_contig)) {
		/* Scatter/gather frames are out of scope for the lab. */
		s->sg++;
		dpaa_wgfp_fd_release(fd);
		return qman_cb_dqrr_consume;
	}

	addr = qm_fd_addr(fd);
	off = qm_fd_get_offset(fd);
	len = qm_fd_get_length(fd);
	dev = dpaa_wgfp_dma_dev(fd->bpid);
	if (unlikely(!dev)) {
		s->errs++;
		return qman_cb_dqrr_consume;
	}

	p = (u8 *)phys_to_virt(addr) + off;
	dma_sync_single_for_cpu(dev, addr + off, len, DMA_FROM_DEVICE);

	rcu_read_lock();
	ops = rcu_dereference(wgfp_ops);
	if (ops) {
		struct dpaa_wgfp_frame f = {
			.buf = (u8 *)phys_to_virt(addr),
			.addr = addr,
			.dev = dev,
			.off = off,
			.len = len,
			.buf_size = DPAA_WGFP_BUF_SIZE,
			.fd = (void *)fd,
		};

		if (ops->frame(wgfp_ops_priv, &f)) {
			rcu_read_unlock();
			s->frames++;
			s->bytes += len;
			return qman_cb_dqrr_consume;
		}
	}
	rcu_read_unlock();

	wgfp_touch_frame(s, p, len);
	dma_sync_single_for_device(dev, addr + off, len, DMA_FROM_DEVICE);

	s->frames++;
	s->bytes += len;
	dpaa_wgfp_fd_release(fd);
	return qman_cb_dqrr_consume;
}

static int wgfp_poll(struct napi_struct *napi, int budget)
{
	struct wgfp_sink *s = container_of(napi, struct wgfp_sink, napi);
	int cleaned;

	s->polls++;
	cleaned = qman_p_poll_dqrr(s->portal, budget);
	if (cleaned < budget) {
		napi_complete_done(napi, cleaned);
		qman_p_irqsource_add(s->portal, QM_PIRQ_DQRI);
	}
	return cleaned;
}

static void wgfp_sink_free(struct wgfp_sink *s)
{
	int err;

	if (!s->created)
		return;
	err = qman_retire_fq(&s->fq, NULL);
	if (err < 0)
		pr_err("dpaa_wgfp: qman_retire_fq(%u) = %d\n",
		       qman_fq_fqid(&s->fq), err);
	err = qman_oos_fq(&s->fq);
	if (err < 0)
		pr_err("dpaa_wgfp: qman_oos_fq(%u) = %d\n",
		       qman_fq_fqid(&s->fq), err);
	qman_destroy_fq(&s->fq);
	if (s->napi_added) {
		napi_disable(&s->napi);
		netif_napi_del(&s->napi);
		s->napi_added = false;
	}
	s->created = false;
}

static int wgfp_sink_create(struct wgfp_sink *s, int cpu)
{
	struct qm_mcc_initfq opts;
	int err;

	memset(s, 0, sizeof(*s));
	s->cpu = cpu;
	s->fq.cb.dqrr = wgfp_dqrr;

	err = qman_create_fq(0, QMAN_FQ_FLAG_DYNAMIC_FQID |
			     QMAN_FQ_FLAG_NO_ENQUEUE, &s->fq);
	if (err)
		return err;
	s->created = true;

	netif_napi_add(wgfp_ndev, &s->napi, wgfp_poll);
	napi_enable(&s->napi);
	s->napi_added = true;

	memset(&opts, 0, sizeof(opts));
	opts.we_mask = cpu_to_be16(QM_INITFQ_WE_FQCTRL | QM_INITFQ_WE_DESTWQ);
	opts.fqd.fq_ctrl = cpu_to_be16(QM_FQCTRL_PREFERINCACHE);
	qm_fqd_set_destwq(&opts.fqd, qman_affine_channel(cpu), WGFP_WQ);
	err = qman_init_fq(&s->fq, QMAN_INITFQ_FLAG_SCHED, &opts);
	if (err) {
		pr_err("dpaa_wgfp: qman_init_fq(%u) = %d\n",
		       qman_fq_fqid(&s->fq), err);
		wgfp_sink_free(s);
		return err;
	}
	return 0;
}

static int wgfp_destroy_all(void)
{
	unsigned int i;

	for (i = 0; i < WGFP_MAX_SINKS; i++)
		wgfp_sink_free(&wgfp_sinks[i]);
	wgfp_nsinks = 0;
	return 0;
}

static int wgfp_create_all(unsigned int n)
{
	const struct cpumask *affine = qman_affine_cpus();
	unsigned int i;
	int err;

	if (wgfp_nsinks)
		return -EBUSY;
	if (!n || n > WGFP_MAX_SINKS)
		return -EINVAL;
	for (i = 0; i < n; i++)
		if (!cpumask_test_cpu(i, affine) || !cpu_online(i))
			return -ENODEV;

	for (i = 0; i < n; i++) {
		err = wgfp_sink_create(&wgfp_sinks[i], i);
		if (err) {
			wgfp_destroy_all();
			return err;
		}
		pr_info("dpaa_wgfp: sink fqid=0x%x on cpu %u\n",
			qman_fq_fqid(&wgfp_sinks[i].fq), i);
	}
	wgfp_nsinks = n;
	return 0;
}

static ssize_t wgfp_ctl_write(struct file *file, const char __user *ubuf,
			      size_t count, loff_t *ppos)
{
	unsigned int n;
	char buf[48];
	int err = 0;

	if (!count || count >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\0';

	mutex_lock(&wgfp_lock);
	if (sscanf(buf, "create %u", &n) == 1) {
		err = wgfp_create_all(n);
	} else if (sscanf(buf, "touch %u", &n) == 1) {
		if (n > 3)
			err = -EINVAL;
		else
			wgfp_touch = n;
	} else if (!strncmp(buf, "reset", 5)) {
		unsigned int i;

		for (i = 0; i < wgfp_nsinks; i++) {
			struct wgfp_sink *s = &wgfp_sinks[i];

			s->frames = s->bytes = s->errs = s->sg = 0;
			s->polls = s->irqs = s->sum = 0;
		}
	} else if (!strncmp(buf, "destroy", 7)) {
		err = wgfp_destroy_all();
	} else {
		err = -EINVAL;
	}
	mutex_unlock(&wgfp_lock);
	return err ? err : count;
}

static const struct file_operations wgfp_ctl_fops = {
	.owner	= THIS_MODULE,
	.write	= wgfp_ctl_write,
	.llseek	= noop_llseek,
};

static int wgfp_stats_show(struct seq_file *m, void *unused)
{
	unsigned int i;

	mutex_lock(&wgfp_lock);
	seq_printf(m, "touch=%u sinks=%u ops=%d tx=%d xmit_ok=%llu xmit_fail=%llu\n",
		   wgfp_touch, wgfp_nsinks, !!rcu_access_pointer(wgfp_ops),
		   wgfp_tx_ready, (u64)atomic64_read(&wgfp_xmit_ok),
		   (u64)atomic64_read(&wgfp_xmit_fail));
	for (i = 0; i < wgfp_nsinks; i++) {
		struct wgfp_sink *s = &wgfp_sinks[i];
		struct qm_mcr_queryfq_np np;
		int err;

		memset(&np, 0, sizeof(np));
		err = qman_query_fq_np(&s->fq, &np);
		seq_printf(m,
			   "sink%u cpu=%d fqid=0x%x frames=%llu bytes=%llu errs=%llu sg=%llu irqs=%llu polls=%llu",
			   i, s->cpu, qman_fq_fqid(&s->fq), s->frames,
			   s->bytes, s->errs, s->sg, s->irqs, s->polls);
		if (!err)
			seq_printf(m, " state=0x%02x frm_cnt=%u",
				   np.state & QM_MCR_NP_STATE_MASK,
				   qm_mcr_np_get(&np, frm_cnt));
		seq_putc(m, '\n');
	}
	mutex_unlock(&wgfp_lock);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(wgfp_stats);

int dpaa_wgfp_register(const struct dpaa_wgfp_ops *ops, void *priv)
{
	int ret = 0;

	mutex_lock(&wgfp_lock);
	if (rcu_access_pointer(wgfp_ops)) {
		ret = -EBUSY;
	} else {
		wgfp_ops_priv = priv;
		rcu_assign_pointer(wgfp_ops, ops);
	}
	mutex_unlock(&wgfp_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_register);

void dpaa_wgfp_unregister(void)
{
	mutex_lock(&wgfp_lock);
	rcu_assign_pointer(wgfp_ops, NULL);
	mutex_unlock(&wgfp_lock);
	synchronize_rcu();
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_unregister);

int dpaa_wgfp_set_egress(struct net_device *dev)
{
	u32 fqid;
	int err;

	err = dpaa_alloc_offload_tx_fq(dev, &fqid);
	if (err)
		return err;
	mutex_lock(&wgfp_lock);
	memset(&wgfp_txfq, 0, sizeof(wgfp_txfq));
	wgfp_txfq.fqid = fqid;
	wgfp_tx_ready = true;
	mutex_unlock(&wgfp_lock);
	pr_info("dpaa_wgfp: egress %s no-confirm tx fqid=0x%x\n", dev->name, fqid);
	return 0;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_set_egress);

void dpaa_wgfp_drop(struct dpaa_wgfp_frame *f)
{
	dpaa_wgfp_fd_release(f->fd);
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_drop);

void dpaa_wgfp_xmit(struct dpaa_wgfp_frame *f, u32 new_off, u32 new_len)
{
	struct qm_fd fd = *(const struct qm_fd *)f->fd;
	int tries = 8;

	if (unlikely(!wgfp_tx_ready)) {
		atomic64_inc(&wgfp_xmit_fail);
		dpaa_wgfp_fd_release(f->fd);
		return;
	}
	qm_fd_clear_fd(&fd);
	fd.bpid = ((const struct qm_fd *)f->fd)->bpid;
	qm_fd_addr_set64(&fd, f->addr);
	qm_fd_set_contig(&fd, new_off, new_len);
	dma_sync_single_for_device(f->dev, f->addr + new_off, new_len,
				   DMA_TO_DEVICE);
	while (tries--) {
		if (!qman_enqueue(&wgfp_txfq, &fd)) {
			atomic64_inc(&wgfp_xmit_ok);
			return;
		}
		cpu_relax();
	}
	atomic64_inc(&wgfp_xmit_fail);
	dpaa_wgfp_fd_release(f->fd);
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_xmit);

static int __init dpaa_wgfp_init(void)
{
	wgfp_ndev = alloc_netdev_dummy(0);
	if (!wgfp_ndev)
		return -ENOMEM;
	wgfp_dir = debugfs_create_dir("dpaa_wgfp", NULL);
	debugfs_create_file("ctl", 0200, wgfp_dir, NULL, &wgfp_ctl_fops);
	debugfs_create_file("stats", 0444, wgfp_dir, NULL, &wgfp_stats_fops);
	return 0;
}
/* Part of the built-in fsl_dpa object; dpaa_eth.c owns module_init(). */
late_initcall(dpaa_wgfp_init);
