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
 *   echo "create <n>"  > /sys/kernel/debug/dpaa_wgfp/ctl   n global lab sinks on
 *                                  CPUs 0..n-1, and n workers per port later
 *   echo "touch <m>"   > .../ctl   0 none, 1 first and last 64 B, 2 read the
 *                                  whole frame, 3 read and write the whole frame
 *   echo "fanout <b>"  > .../ctl   RX records go through a per-port distributor
 *                                  that switches worker every b frames
 *                                  (default 0 = record targets one worker);
 *                                  applies to records installed afterwards
 *   echo "fanout <b> <m>" > .../ctl  as above, and only one batch in m goes to
 *                                  the worker on the distributor's CPU, which
 *                                  also does the distributing (0 = even
 *                                  round-robin, the default)
 *   echo "spread <0|1>" > .../ctl  RX records target a per-port ORDERED queue
 *                                  on the port's pool channel instead (after
 *                                  ASK exp/wireguard patch 187): QMan's order
 *                                  restoration point (QM_FQCTRL_ORP) lets every
 *                                  CPU decrypt a single tunnel's frames and
 *                                  puts them back in order at the egress
 *                                  enqueue, so no software distributor is
 *                                  needed and the inner flow is not reordered.
 *                                  Every dequeued frame owes the ORP exactly
 *                                  one enqueue: the frame itself, or a hole.
 *                                  Applies to records installed afterwards;
 *                                  takes precedence over "fanout".
 *   echo "orp_td <B>"  > .../ctl   ORP queue tail drop in bytes (64 KiB..16 MiB,
 *                                  default 6 MiB)
 *   echo "orp_stash <0|1>" > ...   QMan stashing on the ORP queue (default 0)
 *                                  Both apply when a port's ORP queue is
 *                                  created (once per boot), so set them before
 *                                  the first spread record.
 *   echo "prefetch <0|1|2>" > ...  prefetch each dequeued frame: 1 for read,
 *                                  2 for store (default 2); takes effect at once
 *   echo "prefetch_max <B>" > ...  prefetch at most B bytes per frame (0 = all)
 *   echo "threaded <0|1>" > ...    run WGFP NAPIs in pinned kernel threads
 *                                  (default 0); only before the first bind
 *   echo "reset"       > .../ctl   zero the counters
 *   echo "destroy"     > .../ctl   always refused: sinks are boot-lifetime
 *
 * Sink lifetime contract: sink FQs are never retired or destroyed.  Stale FE
 * records left by a failed delete keep steering into a live sink, whose
 * no-consumer path releases every frame to BMan, so no FQRN/ORL/drain path
 * is needed.  Queues are released only by reboot.
 *   cat .../stats                  fqid, cpu, frames, bytes, errors, polls, FQ state
 */

#include <linux/debugfs.h>
#include <linux/dma-mapping.h>
#include <linux/netdevice.h>
#include <linux/prefetch.h>
#include <linux/sched.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/fsl/dpaa_flow_offload.h>
#include <linux/fsl/dpaa_wgfp.h>
#include <linux/fsl/fman_pcd.h>
#include <linux/rcupdate.h>
#include <soc/fsl/qman.h>

#include "dpaa_eth.h"
#include "dpaa_fman_caps.h"
#include "fman.h"
#include "fman_port.h"
#include "mac.h"

/* Helpers living in dpaa_eth.c (file-local state there). */
struct device *dpaa_wgfp_dma_dev(u8 bpid);
bool dpaa_wgfp_fd_has_error(const struct qm_fd *fd);
void dpaa_wgfp_fd_release(const struct qm_fd *fd);
bool dpaa_buf_stale_check(dma_addr_t addr, u8 bpid, const char *where);

#define WGFP_MAX_SINKS	4	/* worker queues per ingress port */
#define WGFP_MAX_PORTS	8
/* Usable bytes of a DPAA RX buffer (dpaa_bp_size of the 4096-byte raw size). */
#define DPAA_WGFP_BUF_SIZE	3584
/* Same work queue as the kernel RX FQs (dpaa_assign_wq: RX_DEFAULT/RX_PCD = 6).
 * WQ3 sat in the medium tier above them: with all four sinks saturated
 * (P2a unpaced, 2026-11-02) QMan starved eth0 RX and SSH timed out (the
 * F-199 starvation class).
 */
#define WGFP_WQ		6
/* Sink tail drop: without it a sink the CPU cannot keep up with (P2a unpaced:
 * 853k pkt/s offered, 503k sealed) queues frames until the ingress port's
 * buffer pool (~2,270 buffers) runs dry, and every other flow on that port
 * loses with it.  FQ-local tail drop (QM_FQCTRL_TDE) is evaluated against the
 * FQD's own byte count, which the FE ENQUEUE path does update (F-199 A6-LOSS);
 * 512 KiB is about 350 MTU frames per sink, so four sinks stay well inside
 * the pool.  QMan rejects the enqueue and FMan returns the buffer.
 * 1 MiB since 2026-11-10: at 4 tunnels the CPUs saturate and 512 KiB sinks
 * tail-dropped 12.1% of eth3 upload frames (BMI rfdc, rodc 0; ~1.7M TCP
 * retransmits per 30 s run, .185 image 0030).  With the pool at 1280
 * buffers/CPU (~5,100 per port) four 1 MiB sinks (~2,800 frames) keep the
 * same margin.  Limit: a port that carries both a tunnel's ORP queue
 * (WGFP_ORP_TD_DEF) and plaintext sinks can still empty its pool at
 * saturation. */
#define WGFP_SINK_TD	(1024 * 1024)
#define WGFP_MAX_EGRESS	8

struct wgfp_port;

/* Frame/byte counts of a sink every CPU dequeues (the ORP queue): kept per
 * CPU so the hot path does not bounce one cache line between four cores. */
struct wgfp_sink_pc {
	u64 frames;
	u64 bytes;
};

struct wgfp_sink {
	struct qman_fq fq;
	struct napi_struct napi;
	struct qman_portal *portal;
	struct wgfp_port *port;	/* NULL for the legacy global set */
	int cpu;
	bool created;
	bool dist;		/* fan-out distributor, not a worker */
	bool orp;		/* ordered pool-channel queue (spread mode) */
	struct wgfp_sink_pc __percpu *pc;	/* ORP sink only */
	u64 frames;
	u64 bytes;
	u64 errs;
	u64 sg;
	u64 polls;
	u64 irqs;
	u64 sum;	/* defeats dead-code elimination of the touch loop */
};

/* One group of sink queues per ingress port.  A frame dequeued from a group's
 * queue therefore carries its ingress port in wgfp_frame.ingress_ifindex,
 * which the consumer needs to pick the right flow when equal tuples arrive on
 * different ports. */
struct wgfp_port {
	int ifindex;
	unsigned int nsinks;
	bool dist;
	atomic_t rr;		/* round-robin distributor cursor */
	struct wgfp_sink dist_sink;
	bool orp;
	/* kzalloc'd, not in this static array: with orp_stash QMan DMA-maps
	 * the qman_fq for context stashing, and the kernel image's .bss is
	 * not DMA-mappable on arm64 ("rejecting DMA map of vmalloc memory",
	 * .185 image 2345).  Boot lifetime, like every sink. */
	struct wgfp_sink *orp_sink;
	struct wgfp_sink sink[WGFP_MAX_SINKS];
};

/* ORP window: orprws 5 = 1024 frames in flight; auto-advance (OA) past a frame
 * still owed once the window is exhausted; late-arrival window 3 so a delayed
 * frame is accepted late (out of order) instead of rejected.  Values from ASK
 * exp/wireguard (VWD_WG_ORP_WINDOW); RM ch. 3 "order restoration". */
#define WGFP_ORP_RWS		5
#define WGFP_ORP_OA		1
#define WGFP_ORP_OLWS		3
/* QMan can leave every ring of a portal full for ~200 us; ASK waits up to 5 ms
 * for room before giving a sequence number up. */
#define WGFP_ORP_WAIT_NS	(5 * NSEC_PER_MSEC)

/* The frame the running callback owes the ORP.  Per CPU: the callback runs in
 * softirq on the portal's own CPU and nothing interleaves with it. */
struct wgfp_orp_ctx {
	struct wgfp_sink *sink;
	u32 orp_fqid;
	u16 seq;
	bool owed;
};
static DEFINE_PER_CPU(struct wgfp_orp_ctx, wgfp_orp_ctx);

/* Each CPU's own NAPI for the pool-channel queue: any portal may dequeue it. */
struct wgfp_orp_napi {
	struct napi_struct napi;
	struct qman_portal *portal;
};
static DEFINE_PER_CPU(struct wgfp_orp_napi, wgfp_orp_napi);
static bool wgfp_orp_napi_ready;
/* threaded 1: WGFP's NAPIs run in kernel threads instead of softirq, so the
 * scheduler shares a saturated CPU with user space.  With every CPU in
 * softirq at 4 tunnels, SSH logins to the DUT failed (5 s timeout) for the
 * whole run while ICMP to eth0 still answered (.185, image 0403).  Each
 * thread polls its own CPU's QMan portal, which is not locked against other
 * CPUs, so it is pinned to that CPU before its NAPI is enabled; and the knob
 * is accepted only before WGFP has created any NAPI (once per boot, like
 * orp_td), so no NAPI ever runs threaded without being pinned. */
static bool wgfp_threaded;


static struct wgfp_sink wgfp_global[WGFP_MAX_SINKS];
static unsigned int wgfp_nglobal;
static struct wgfp_port wgfp_ports[WGFP_MAX_PORTS];
static unsigned int wgfp_nports;
static struct net_device *wgfp_ndev;

static void wgfp_napi_add(struct napi_struct *napi,
			  int (*poll)(struct napi_struct *, int), int cpu)
{
	netif_napi_add(wgfp_ndev, napi, poll);
	if (napi->thread && cpu >= 0)
		set_cpus_allowed_ptr(napi->thread, cpumask_of(cpu));
	napi_enable(napi);
}
static struct dentry *wgfp_dir;
static unsigned int wgfp_touch = 2;
static unsigned int wgfp_per_port;	/* worker queues per port */
/* RX fan-out batch: the distributor moves to the next worker every
 * wgfp_fanout frames (0 = no distributor).  Whole bursts stay on one worker,
 * so an inner flow is reordered only at batch boundaries.  Off by default:
 * on .185 (image 0436, 4 TCP flows down) fanout 32 gave 1.42 Gbit/s against
 * 1.93 with 0, because the reordering made the receivers ACK every segment
 * and the extra ACKs cost more sealing work than the spread saved. */
static unsigned int wgfp_fanout;
#define WGFP_FANOUT_MAX	1024
/* The distributor runs on CPU 0, next to worker 0, and re-enqueueing every
 * frame costs that CPU about a quarter of its time (.185, image 0540, one
 * tunnel at 3.4 Gbit/s), so an even split saturates CPU 0 first while CPUs
 * 1-3 have headroom.  wgfp_fanout_home = m gives worker 0 one batch in m. */
static unsigned int wgfp_fanout_home;
#define WGFP_FANOUT_HOME_MAX	64
/* Spread mode: RX records go to the port's ORP queue (default off). */
static bool wgfp_spread;
/* ORP queue settings, taken when a port's ORP queue is created (boot
 * lifetime, so set them before the first spread record): tail-drop bytes,
 * and QMan stashing of annotation, data and context lines as mainline
 * dpaa_eth sets on its RX queues (ASK exp/wireguard stashes its steer
 * queues too).  Defaults keep the original queue. */
/* 6 MiB (~4,300 full frames), sized against the RX pool: at 4 tunnels the
 * CPUs saturate and the queue fills.  With 640 buffers/CPU (~2,560 per port)
 * 3 MiB still tail-dropped 10.6% (eth4 BMI rfdc) and 4-8 MiB emptied the
 * pool instead (rodc 1.4-1.8%), .185 image 0030, 2026-11-10.  The pool is now
 * 1280 buffers/CPU (bin/ci-setup-kernel.sh), so 6 MiB fits with headroom. */
#define WGFP_ORP_TD_DEF		(6 * 1024 * 1024)
#define WGFP_ORP_TD_MIN		(64 * 1024)
#define WGFP_ORP_TD_MAX		(16 * 1024 * 1024)
static u32 wgfp_orp_td = WGFP_ORP_TD_DEF;
static bool wgfp_orp_stash;
/* Prefetch the whole frame when a sink dequeues it.  FMan DMAs it to DDR,
 * so the decrypt or seal otherwise takes its ~23 cache misses one at a time
 * (.185, image 2345, P6+spread 1T download: 26 L2 refills and ~18k cycles per
 * packet against ~9k for hot-cache ChaCha20-Poly1305).  1 = prefetch for
 * read, 2 = for store (the AEAD rewrites the buffer in place); 0 = off.
 * Default 2: on .106 (image 0011, P6 + spread) it took 1T download from
 * 3.74-3.77 to 4.59-4.61 Gbit/s (18.0k -> 14.6k cycles per packet), 4T
 * download from 3.63 to 4.59, and 1T upload from 3.94 to 4.25. */
static unsigned int wgfp_prefetch = 2;
/* Prefetch at most this many bytes of a frame (0 = all of it).  Issuing a
 * prefetch for every line stalls once the core's outstanding-miss slots are
 * full: wgfp_dqrr_frame rose from 2.7% to 9.4% of cycles with prefetch 2
 * (.185, image 0403, 1T upload); the A72 stream prefetcher may cover the
 * rest of the frame once the first lines are in flight. */
static unsigned int wgfp_prefetch_max;

static void wgfp_prefetch_frame(const u8 *p, u32 len)
{
	unsigned int mode = READ_ONCE(wgfp_prefetch);
	unsigned int max = READ_ONCE(wgfp_prefetch_max);
	u32 o;

	if (!mode)
		return;
	if (max && max < len)
		len = max;
	for (o = 0; o < len; o += L1_CACHE_BYTES) {
		if (mode == 2)
			prefetchw(p + o);
		else
			prefetch(p + o);
	}
}
/* Hole commands carry an empty descriptor; their rejections must not be freed
 * as frames, so they get their own tag queue. */
static struct qman_fq wgfp_hole_fq;
static bool wgfp_hole_ready;
static DEFINE_PER_CPU(atomic64_t, wgfp_orp_frames);
static atomic64_t wgfp_orp_holes = ATOMIC64_INIT(0);
static atomic64_t wgfp_orp_wait_fail = ATOMIC64_INIT(0);
static atomic64_t wgfp_orp_hole_ern = ATOMIC64_INIT(0);
static void wgfp_hole_ern_cb(struct qman_portal *portal, struct qman_fq *fq,
			     const union qm_mr_entry *msg);
static bool wgfp_pool_failed;

static inline void wgfp_sink_count(struct wgfp_sink *s, u32 len)
{
	if (s->pc) {
		struct wgfp_sink_pc *pc = this_cpu_ptr(s->pc);

		pc->frames++;
		pc->bytes += len;
		return;
	}
	s->frames++;
	s->bytes += len;
}
static DEFINE_MUTEX(wgfp_lock);

/* Consumer hook (the wireguard module) and the egress frame queue. */
static const struct dpaa_wgfp_ops __rcu *wgfp_ops;
static void *wgfp_ops_priv;
/* The tag FQ is reserved for the lifetime of this built-in driver, independent
 * of the port-owned egress FQs, so late ERNs always reach our callback.
 * Readers index the egress FQIDs locklessly after wgfp_neg is published. */
static struct qman_fq wgfp_ern_fq;
static bool wgfp_ern_fq_ready;
/* Separate tag queue for the RX fan-out so its rejections do not look like
 * transmit failures in the soak gates. */
static struct qman_fq wgfp_rx_ern_fq;
static bool wgfp_rx_ern_ready;
static atomic64_t wgfp_rx_enq_fail = ATOMIC64_INIT(0);
static atomic64_t wgfp_rx_ern = ATOMIC64_INIT(0);
static void wgfp_rx_ern_cb(struct qman_portal *portal, struct qman_fq *fq,
			   const union qm_mr_entry *msg);
static u32 wgfp_eg_fqid[WGFP_MAX_EGRESS];
static int wgfp_eg_ifindex[WGFP_MAX_EGRESS];
static int wgfp_neg;
static DEFINE_PER_CPU(atomic64_t, wgfp_xmit_ok);
static atomic64_t wgfp_xmit_fail = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_ern = ATOMIC64_INIT(0);

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

/* Worker for distributor batch b of a port with n workers, where worker
 * `home` shares the distributor's CPU: with m >= 2 every m-th batch goes to
 * home and the rest go round-robin over the others; m < 2, or a home that is
 * not one of the workers, gives the plain round-robin b % n. */
static unsigned int wgfp_fanout_pick(unsigned int b, unsigned int n,
				     unsigned int home, unsigned int m)
{
	unsigned int j;

	if (n < 2 || m < 2 || home >= n)
		return b % n;
	if (b % m == 0)
		return home;
	/* b - (b / m + 1) earlier batches in [0, b] did not go home */
	j = (b - b / m - 1) % (n - 1);
	return j < home ? j : j + 1;
}

static enum qman_cb_dqrr_result wgfp_dqrr_frame(struct qman_portal *portal,
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
	/* 4022 bad-page hunt: a stale pool entry is reported and leaked */
	if (unlikely(dpaa_buf_stale_check(addr, fd->bpid, "wgfp"))) {
		s->errs++;
		return qman_cb_dqrr_consume;
	}
	off = qm_fd_get_offset(fd);
	len = qm_fd_get_length(fd);
	dev = dpaa_wgfp_dma_dev(fd->bpid);
	if (unlikely(!dev)) {
		s->errs++;
		return qman_cb_dqrr_consume;
	}

	/* Fan-out distributor: one outer flow lands on one FQ, but decrypting it
	 * on one CPU caps download.  Re-enqueue the untouched buffer round-robin
	 * to this port's workers; each worker is still tied to one ingress port,
	 * so provenance survives the extra hop. */
	if (s->dist) {
		struct wgfp_port *p = s->port;
		struct wgfp_sink *dst;
		struct qm_fd nfd;
		unsigned int i;

		if (unlikely(!p || !p->nsinks))
			goto release;
		i = wgfp_fanout_pick((unsigned int)atomic_inc_return(&p->rr) /
				     max_t(unsigned int, READ_ONCE(wgfp_fanout), 1),
				     p->nsinks, s->cpu,
				     READ_ONCE(wgfp_fanout_home));
		dst = &p->sink[i];
		qm_fd_clear_fd(&nfd);
		nfd.bpid = fd->bpid;
		qm_fd_addr_set64(&nfd, addr);
		qm_fd_set_contig(&nfd, off, len);
		if (!READ_ONCE(wgfp_rx_ern_ready) ||
		    qman_enqueue_fqid(&wgfp_rx_ern_fq,
				      qman_fq_fqid(&dst->fq), &nfd)) {
			atomic64_inc(&wgfp_rx_enq_fail);
			goto release;
		}
		wgfp_sink_count(s, len);
		return qman_cb_dqrr_consume;
	}

	p = (u8 *)phys_to_virt(addr) + off;
	dma_sync_single_for_cpu(dev, addr + off, len, DMA_FROM_DEVICE);
	wgfp_prefetch_frame(p, len);

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
			.ingress_ifindex = s->port ? s->port->ifindex : 0,
			.fd = (void *)fd,
		};

		if (ops->frame(wgfp_ops_priv, &f)) {
			rcu_read_unlock();
			wgfp_sink_count(s, len);
			return qman_cb_dqrr_consume;
		}
	}
	rcu_read_unlock();

	wgfp_touch_frame(s, p, len);
	dma_sync_single_for_device(dev, addr + off, len, DMA_FROM_DEVICE);

	wgfp_sink_count(s, len);
release:
	dpaa_wgfp_fd_release(fd);
	return qman_cb_dqrr_consume;
}

/* Give the ORP the sequence number of a frame that is going nowhere. */
static void wgfp_orp_hole(struct wgfp_orp_ctx *c)
{
	struct qm_fd fd;
	ktime_t end = ktime_add_ns(ktime_get(), WGFP_ORP_WAIT_NS);

	qm_fd_clear_fd(&fd);
	c->owed = false;
	do {
		if (!qman_enqueue_orp_fqid(&wgfp_hole_fq, 0, c->orp_fqid, c->seq,
					   true, &fd)) {
			atomic64_inc(&wgfp_orp_holes);
			return;
		}
		cpu_relax();
	} while (ktime_before(ktime_get(), end));
	/* A lost hole stalls later frames until the window auto-advances. */
	atomic64_inc(&wgfp_orp_wait_fail);
}

static enum qman_cb_dqrr_result wgfp_dqrr(struct qman_portal *portal,
					  struct qman_fq *fq,
					  const struct qm_dqrr_entry *dq,
					  bool sched_napi)
{
	struct wgfp_sink *s = container_of(fq, struct wgfp_sink, fq);
	struct wgfp_orp_ctx *c;
	enum qman_cb_dqrr_result r;

	if (likely(!s->orp))
		return wgfp_dqrr_frame(portal, fq, dq, sched_napi);

	if (sched_napi) {
		struct wgfp_orp_napi *n = this_cpu_ptr(&wgfp_orp_napi);

		qman_p_irqsource_remove(portal, QM_PIRQ_DQRI);
		n->portal = portal;
		s->irqs++;
		napi_schedule(&n->napi);
		return qman_cb_dqrr_stop;
	}
	if (unlikely(!(dq->stat & QM_DQRR_STAT_FD_VALID)))
		return qman_cb_dqrr_consume;

	c = this_cpu_ptr(&wgfp_orp_ctx);
	c->sink = s;
	c->orp_fqid = qman_fq_fqid(fq);
	c->seq = be16_to_cpu(dq->seqnum);
	c->owed = true;
	dpaa_wgfp_stat_inc(&wgfp_orp_frames);
	r = wgfp_dqrr_frame(portal, fq, dq, false);
	/* Whatever path the frame took, its place in the sequence is settled. */
	if (c->owed)
		wgfp_orp_hole(c);
	return r;
}

static int wgfp_orp_poll(struct napi_struct *napi, int budget)
{
	struct wgfp_orp_napi *n = container_of(napi, struct wgfp_orp_napi, napi);
	int cleaned;

	cleaned = qman_p_poll_dqrr(n->portal, budget);
	if (cleaned < budget) {
		napi_complete_done(napi, cleaned);
		qman_p_irqsource_add(n->portal, QM_PIRQ_DQRI);
	}
	return cleaned;
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

static void wgfp_hole_ern_cb(struct qman_portal *portal, struct qman_fq *fq,
			     const union qm_mr_entry *msg)
{
	/* Empty descriptor: nothing to free; QMan skips the place on its own
	 * once the window moves on. */
	atomic64_inc(&wgfp_orp_hole_ern);
	pr_warn_ratelimited("dpaa_wgfp: ORP hole %u of fqid 0x%x rejected (rc 0x%02x)\n",
			    be16_to_cpu(msg->ern.seqnum), (u32)qm_fqid_get(&msg->ern),
			    msg->ern.rc);
}

static int wgfp_orp_sink_create(struct wgfp_sink *s, struct wgfp_port *port,
				u16 channel)
{
	struct qm_mcc_initfq opts;
	int err;

	if (WARN_ON_ONCE(s->created))
		return -EBUSY;
	memset(s, 0, sizeof(*s));
	s->pc = alloc_percpu(struct wgfp_sink_pc);
	if (!s->pc)
		return -ENOMEM;
	s->cpu = -1;
	s->port = port;
	s->orp = true;
	s->fq.cb.dqrr = wgfp_dqrr;

	memset(&opts, 0, sizeof(opts));
	opts.we_mask = cpu_to_be16(QM_INITFQ_WE_FQCTRL | QM_INITFQ_WE_DESTWQ |
				   QM_INITFQ_WE_TDTHRESH | QM_INITFQ_WE_ORPC);
	opts.fqd.fq_ctrl = cpu_to_be16(QM_FQCTRL_PREFERINCACHE | QM_FQCTRL_TDE |
				       QM_FQCTRL_ORP | QM_FQCTRL_AVOIDBLOCK);
	opts.fqd.orpc = QM_FQD_ORPC(WGFP_ORP_RWS, WGFP_ORP_OA, WGFP_ORP_OLWS);
	qm_fqd_set_destwq(&opts.fqd, channel, WGFP_WQ);
	if (READ_ONCE(wgfp_orp_stash)) {
		opts.we_mask |= cpu_to_be16(QM_INITFQ_WE_CONTEXTA);
		opts.fqd.fq_ctrl |= cpu_to_be16(QM_FQCTRL_CTXASTASHING);
		opts.fqd.context_a.stashing.exclusive =
			QM_STASHING_EXCL_DATA | QM_STASHING_EXCL_CTX |
			QM_STASHING_EXCL_ANNOTATION;
		qm_fqd_set_stashing(&opts.fqd, 1, 2,
				    DIV_ROUND_UP(sizeof(struct qman_fq), 64));
	}
	err = qm_fqd_set_taildrop(&opts.fqd, READ_ONCE(wgfp_orp_td), 1);
	if (err >= 0)
		err = qman_create_fq(0, QMAN_FQ_FLAG_DYNAMIC_FQID |
				     QMAN_FQ_FLAG_NO_ENQUEUE, &s->fq);
	if (err) {
		free_percpu(s->pc);
		s->pc = NULL;
		return err < 0 ? err : -EINVAL;
	}
	s->created = true;
	err = qman_init_fq(&s->fq, QMAN_INITFQ_FLAG_SCHED, &opts);
	if (err)
		pr_err("dpaa_wgfp: qman_init_fq(%u) ORP = %d\n",
		       qman_fq_fqid(&s->fq), err);
	return err;
}

static int wgfp_port_orp_locked(struct wgfp_port *p, struct net_device *in)
{
	const struct cpumask *affine = qman_affine_cpus();
	struct dpaa_priv *priv = netdev_priv(in);
	unsigned int cpu;
	int err;

	if (p->orp)
		return 0;
	if (!READ_ONCE(wgfp_hole_ready)) {
		wgfp_hole_fq.cb.ern = wgfp_hole_ern_cb;
		err = qman_create_fq(0, QMAN_FQ_FLAG_DYNAMIC_FQID |
				     QMAN_FQ_FLAG_NO_MODIFY, &wgfp_hole_fq);
		if (err) {
			pr_err("dpaa_wgfp: allocate hole tag queue: %d\n", err);
			return err;
		}
		WRITE_ONCE(wgfp_hole_ready, true);
	}
	if (!wgfp_orp_napi_ready) {
		for_each_cpu(cpu, affine) {
			struct wgfp_orp_napi *n = &per_cpu(wgfp_orp_napi, cpu);

			wgfp_napi_add(&n->napi, wgfp_orp_poll, cpu);
		}
		wgfp_orp_napi_ready = true;
	}
	if (!p->orp_sink) {
		p->orp_sink = kzalloc(sizeof(*p->orp_sink), GFP_KERNEL);
		if (!p->orp_sink)
			return -ENOMEM;
	}
	err = wgfp_orp_sink_create(p->orp_sink, p, priv->channel);
	if (err) {
		WRITE_ONCE(wgfp_pool_failed, true);
		pr_err("dpaa_wgfp: ORP queue setup failed; retaining allocated queues until reboot\n");
		return err;
	}
	p->orp = true;
	pr_info("dpaa_wgfp: port ifindex %d ORP queue fqid=0x%x channel 0x%x\n",
		p->ifindex, qman_fq_fqid(&p->orp_sink->fq), priv->channel);
	return 0;
}

static int wgfp_sink_create(struct wgfp_sink *s, struct wgfp_port *port,
			    int cpu, bool dist)
{
	struct qm_mcc_initfq opts;
	int err;

	if (WARN_ON_ONCE(s->created))
		return -EBUSY;
	memset(s, 0, sizeof(*s));
	s->cpu = cpu;
	s->port = port;
	s->dist = dist;
	s->fq.cb.dqrr = wgfp_dqrr;

	memset(&opts, 0, sizeof(opts));
	opts.we_mask = cpu_to_be16(QM_INITFQ_WE_FQCTRL | QM_INITFQ_WE_DESTWQ |
				   QM_INITFQ_WE_TDTHRESH);
	opts.fqd.fq_ctrl = cpu_to_be16(QM_FQCTRL_PREFERINCACHE | QM_FQCTRL_TDE);
	qm_fqd_set_destwq(&opts.fqd, qman_affine_channel(cpu), WGFP_WQ);
	err = qm_fqd_set_taildrop(&opts.fqd, WGFP_SINK_TD, 1);
	if (err < 0)
		return err;

	/* Workers also accept software enqueues from the fan-out distributor;
	 * the distributor is FMan-only, like the old sinks. */
	err = qman_create_fq(0, QMAN_FQ_FLAG_DYNAMIC_FQID |
			     (dist ? QMAN_FQ_FLAG_NO_ENQUEUE : 0), &s->fq);
	if (err)
		return err;
	s->created = true;

	wgfp_napi_add(&s->napi, wgfp_poll, s->cpu);
	err = qman_init_fq(&s->fq, QMAN_INITFQ_FLAG_SCHED, &opts);
	if (err)
		pr_err("dpaa_wgfp: qman_init_fq(%u) = %d\n",
		       qman_fq_fqid(&s->fq), err);
	return err;
}

/* The port's ordered queue, on its pool channel (dequeued by every affine
 * portal).  AVOIDBLOCK hands each frame to any portal with room instead of
 * parking the active queue on one portal's ring. */
static int wgfp_global_create(unsigned int n)
{
	const struct cpumask *affine = qman_affine_cpus();
	unsigned int i;
	int err;

	if (wgfp_pool_failed)
		return -EIO;
	if (wgfp_nglobal)
		return -EBUSY;
	if (!n || n > WGFP_MAX_SINKS)
		return -EINVAL;
	for (i = 0; i < n; i++)
		if (!cpumask_test_cpu(i, affine) || !cpu_online(i))
			return -ENODEV;

	for (i = 0; i < n; i++) {
		err = wgfp_sink_create(&wgfp_global[i], NULL, i, false);
		if (err) {
			/* A timed-out init may still have scheduled its FQ. */
			WRITE_ONCE(wgfp_pool_failed, true);
			pr_err("dpaa_wgfp: sink setup failed; retaining allocated queues until reboot\n");
			return err;
		}
		pr_info("dpaa_wgfp: global sink fqid=0x%x on cpu %u\n",
			qman_fq_fqid(&wgfp_global[i].fq), i);
	}
	wgfp_nglobal = n;
	return 0;
}

/* Per-port workers.  Created on the first flow record for the port, in
 * process context (dpaa_wgfp_flow_add()). */
static struct wgfp_port *wgfp_port_find_locked(int ifindex)
{
	unsigned int i;

	for (i = 0; i < wgfp_nports; i++)
		if (wgfp_ports[i].ifindex == ifindex)
			return &wgfp_ports[i];
	return NULL;
}

static int wgfp_port_create(struct wgfp_port *p, unsigned int n)
{
	const struct cpumask *affine = qman_affine_cpus();
	unsigned int i;
	int err;

	memset(p, 0, sizeof(*p));
	for (i = 0; i < n; i++)
		if (!cpumask_test_cpu(i, affine) || !cpu_online(i))
			return -ENODEV;
	for (i = 0; i < n; i++) {
		err = wgfp_sink_create(&p->sink[i], p, i, false);
		if (err) {
			WRITE_ONCE(wgfp_pool_failed, true);
			pr_err("dpaa_wgfp: port sink setup failed; retaining allocated queues until reboot\n");
			return err;
		}
	}
	atomic_set(&p->rr, 0);
	p->nsinks = n;
	return 0;
}

static struct wgfp_port *wgfp_port_get_locked(struct net_device *in)
{
	struct wgfp_port *p;
	unsigned int n;
	int err;

	p = wgfp_port_find_locked(in->ifindex);
	if (p)
		return p;
	if (wgfp_pool_failed)
		return ERR_PTR(-EIO);
	if (wgfp_nports == WGFP_MAX_PORTS)
		return ERR_PTR(-ENOSPC);
	n = wgfp_per_port ? min_t(unsigned int, wgfp_per_port, WGFP_MAX_SINKS) :
			    min_t(unsigned int, WGFP_MAX_SINKS, num_online_cpus());
	if (!n)
		return ERR_PTR(-ENODEV);
	p = &wgfp_ports[wgfp_nports];
	err = wgfp_port_create(p, n);
	if (err) {
		WRITE_ONCE(wgfp_pool_failed, true);
		return ERR_PTR(err);
	}
	p->ifindex = in->ifindex;
	wgfp_nports++;
	pr_info("dpaa_wgfp: %s: %u worker sinks\n", in->name, n);
	return p;
}

static int wgfp_port_dist_locked(struct wgfp_port *p)
{
	int err;

	if (p->dist)
		return 0;
	if (!READ_ONCE(wgfp_rx_ern_ready)) {
		wgfp_rx_ern_fq.cb.ern = wgfp_rx_ern_cb;
		err = qman_create_fq(0, QMAN_FQ_FLAG_DYNAMIC_FQID |
				     QMAN_FQ_FLAG_NO_MODIFY, &wgfp_rx_ern_fq);
		if (err) {
			pr_err("dpaa_wgfp: allocate RX tag queue: %d\n", err);
			return err;
		}
		WRITE_ONCE(wgfp_rx_ern_ready, true);
	}
	err = wgfp_sink_create(&p->dist_sink, p, 0, true);
	if (err) {
		WRITE_ONCE(wgfp_pool_failed, true);
		pr_err("dpaa_wgfp: RX distributor setup failed; retaining allocated queues until reboot\n");
		return err;
	}
	p->dist = true;
	pr_info("dpaa_wgfp: port ifindex %d distributor fqid=0x%x\n",
		p->ifindex, qman_fq_fqid(&p->dist_sink.fq));
	return 0;
}

static ssize_t wgfp_ctl_write(struct file *file, const char __user *ubuf,
			      size_t count, loff_t *ppos)
{
	unsigned int n, home;
	char buf[48];
	int err = 0;

	if (!count || count >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\0';

	mutex_lock(&wgfp_lock);
	if (sscanf(buf, "create %u", &n) == 1) {
		if (wgfp_nports || (n && wgfp_per_port && wgfp_per_port != n)) {
			err = -EBUSY;
		} else {
			err = n ? wgfp_global_create(n) : 0;
			if (!err && n)
				wgfp_per_port = n;
		}
	} else if (sscanf(buf, "fanout %u %u", &n, &home) == 2) {
		if (n > WGFP_FANOUT_MAX || home == 1 ||
		    home > WGFP_FANOUT_HOME_MAX) {
			err = -EINVAL;
		} else {
			WRITE_ONCE(wgfp_fanout, n);
			WRITE_ONCE(wgfp_fanout_home, home);
		}
	} else if (sscanf(buf, "fanout %u", &n) == 1) {
		if (n > WGFP_FANOUT_MAX)
			err = -EINVAL;
		else
			WRITE_ONCE(wgfp_fanout, n);
	} else if (sscanf(buf, "spread %u", &n) == 1) {
		if (n > 1)
			err = -EINVAL;
		else
			WRITE_ONCE(wgfp_spread, n);
	} else if (sscanf(buf, "orp_td %u", &n) == 1) {
		if (n < WGFP_ORP_TD_MIN || n > WGFP_ORP_TD_MAX)
			err = -EINVAL;
		else
			WRITE_ONCE(wgfp_orp_td, n);
	} else if (sscanf(buf, "threaded %u", &n) == 1) {
		if (n > 1)
			err = -EINVAL;
		else if (wgfp_nglobal || wgfp_nports || wgfp_orp_napi_ready)
			err = -EBUSY;	/* NAPIs exist: set it before the first bind */
		else
			err = dev_set_threaded(wgfp_ndev, n ?
					       NETDEV_NAPI_THREADED_ENABLED :
					       NETDEV_NAPI_THREADED_DISABLED);
		if (!err)
			WRITE_ONCE(wgfp_threaded, n);
	} else if (sscanf(buf, "prefetch_max %u", &n) == 1) {
		if (n > 65536)
			err = -EINVAL;
		else
			WRITE_ONCE(wgfp_prefetch_max, n);
	} else if (sscanf(buf, "prefetch %u", &n) == 1) {
		if (n > 2)
			err = -EINVAL;
		else
			WRITE_ONCE(wgfp_prefetch, n);
	} else if (sscanf(buf, "orp_stash %u", &n) == 1) {
		if (n > 1)
			err = -EINVAL;
		else
			WRITE_ONCE(wgfp_orp_stash, n);
	} else if (sscanf(buf, "touch %u", &n) == 1) {
		if (n > 3)
			err = -EINVAL;
		else
			wgfp_touch = n;
	} else if (!strncmp(buf, "reset", 5)) {
		unsigned int i, j;
		int cpu;

		for (i = 0; i < wgfp_nglobal; i++) {
			struct wgfp_sink *s = &wgfp_global[i];

			s->frames = s->bytes = s->errs = s->sg = 0;
			s->polls = s->irqs = s->sum = 0;
		}
		for (i = 0; i < wgfp_nports; i++) {
			struct wgfp_port *p = &wgfp_ports[i];

			for (j = 0; j < p->nsinks; j++) {
				struct wgfp_sink *s = &p->sink[j];

				s->frames = s->bytes = s->errs = s->sg = 0;
				s->polls = s->irqs = s->sum = 0;
			}
			if (p->dist) {
				struct wgfp_sink *s = &p->dist_sink;

				s->frames = s->bytes = s->errs = s->sg = 0;
				s->polls = s->irqs = s->sum = 0;
			}
		}
		for (i = 0; i < wgfp_nports; i++) {
			struct wgfp_port *p = &wgfp_ports[i];
			struct wgfp_sink *o = p->orp_sink;

			if (!o || !o->pc)
				continue;
			for_each_possible_cpu(cpu)
				*per_cpu_ptr(o->pc, cpu) = (struct wgfp_sink_pc){};
			o->errs = o->sg = 0;
			o->polls = o->irqs = 0;
		}
		for_each_possible_cpu(cpu)
			atomic64_set(per_cpu_ptr(&wgfp_orp_frames, cpu), 0);
		atomic64_set(&wgfp_orp_holes, 0);
		atomic64_set(&wgfp_orp_wait_fail, 0);
		atomic64_set(&wgfp_orp_hole_ern, 0);
	} else if (!strncmp(buf, "destroy", 7)) {
		pr_warn_ratelimited("dpaa_wgfp: sinks are boot-lifetime; reboot to release queues\n");
		err = -EOPNOTSUPP;
	} else {
		err = -EINVAL;
	}
	mutex_unlock(&wgfp_lock);
	if (err)
		return err;
	return count;
}

static const struct file_operations wgfp_ctl_fops = {
	.owner	= THIS_MODULE,
	.write	= wgfp_ctl_write,
	.llseek	= noop_llseek,
};

static void wgfp_sink_show(struct seq_file *m, unsigned int idx,
			   struct wgfp_sink *s, const char *kind, int ifindex)
{
	struct qm_mcr_queryfq_np np;
	u64 frames = s->frames, bytes = s->bytes;
	int err, cpu;

	if (s->pc) {
		for_each_possible_cpu(cpu) {
			frames += per_cpu_ptr(s->pc, cpu)->frames;
			bytes += per_cpu_ptr(s->pc, cpu)->bytes;
		}
	}
	memset(&np, 0, sizeof(np));
	err = qman_query_fq_np(&s->fq, &np);
	seq_printf(m,
		   "sink%u port=%d kind=%s cpu=%d fqid=0x%x frames=%llu bytes=%llu errs=%llu sg=%llu irqs=%llu polls=%llu",
		   idx, ifindex, kind, s->cpu, qman_fq_fqid(&s->fq), frames,
		   bytes, s->errs, s->sg, s->irqs, s->polls);
	if (!err) {
		/* The management response counters are big-endian (verified on
		 * the DUT: byte_cnt / frm_cnt equals the fixed frame size only
		 * after a 32-bit swap); qm_mcr_np_get() does not convert.
		 * Raw words stay for cross-checking.
		 */
		seq_printf(m, " state=0x%02x frm_cnt=%u byte_cnt=%u frm_cnt_raw=0x%08x byte_cnt_raw=0x%08x",
			   np.state & QM_MCR_NP_STATE_MASK,
			   be32_to_cpu(np.frm_cnt) & qm_mcr_frm_cnt_mask,
			   be32_to_cpu(np.byte_cnt),
			   np.frm_cnt, np.byte_cnt);
	} else {
		seq_printf(m, " query_err=%d", err);
	}
	seq_putc(m, '\n');
}

static int wgfp_stats_show(struct seq_file *m, void *unused)
{
	unsigned int i, j, idx = 0, total;

	mutex_lock(&wgfp_lock);
	total = wgfp_nglobal;
	for (i = 0; i < wgfp_nports; i++)
		total += wgfp_ports[i].nsinks + !!wgfp_ports[i].dist +
			 !!wgfp_ports[i].orp;
	seq_printf(m, "touch=%u sinks=%u per_port=%u fanout=%u fanout_home=%u ports=%u global=%u pool_failed=%d ops=%d egress=%d xmit_ok=%llu xmit_fail=%llu xmit_ern=%llu rx_enq_fail=%llu rx_ern=%llu spread=%d orp_td=%u orp_stash=%d prefetch=%u prefetch_max=%u threaded=%d orp_frames=%llu orp_holes=%llu orp_wait_fail=%llu orp_hole_ern=%llu frm_cnt_encoding=host\n",
		   wgfp_touch, total, wgfp_per_port, READ_ONCE(wgfp_fanout),
		   READ_ONCE(wgfp_fanout_home),
		   wgfp_nports, wgfp_nglobal,
		   READ_ONCE(wgfp_pool_failed),
		   !!rcu_access_pointer(wgfp_ops),
		   READ_ONCE(wgfp_neg), dpaa_wgfp_stat_sum(&wgfp_xmit_ok),
		   (u64)atomic64_read(&wgfp_xmit_fail),
		   (u64)atomic64_read(&wgfp_xmit_ern),
		   (u64)atomic64_read(&wgfp_rx_enq_fail),
		   (u64)atomic64_read(&wgfp_rx_ern),
		   READ_ONCE(wgfp_spread), READ_ONCE(wgfp_orp_td),
		   READ_ONCE(wgfp_orp_stash), READ_ONCE(wgfp_prefetch),
		   READ_ONCE(wgfp_prefetch_max), READ_ONCE(wgfp_threaded),
		   dpaa_wgfp_stat_sum(&wgfp_orp_frames),
		   (u64)atomic64_read(&wgfp_orp_holes),
		   (u64)atomic64_read(&wgfp_orp_wait_fail),
		   (u64)atomic64_read(&wgfp_orp_hole_ern));
	for (i = 0; i < wgfp_nglobal; i++)
		wgfp_sink_show(m, idx++, &wgfp_global[i], "global", 0);
	for (i = 0; i < wgfp_nports; i++) {
		struct wgfp_port *p = &wgfp_ports[i];

		for (j = 0; j < p->nsinks; j++)
			wgfp_sink_show(m, idx++, &p->sink[j], "worker",
				       p->ifindex);
		if (p->dist)
			wgfp_sink_show(m, idx++, &p->dist_sink, "dist",
				       p->ifindex);
		if (p->orp)
			wgfp_sink_show(m, idx++, p->orp_sink, "orp",
				       p->ifindex);
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
		/* Sink groups are created per ingress port on the first record
		 * (wgfp_port_get_locked()), so the consumers' flow tables are
		 * the only thing that needs to exist here. */
		if (!wgfp_per_port)
			wgfp_per_port = min_t(unsigned int, WGFP_MAX_SINKS,
					      num_online_cpus());
		if (!wgfp_per_port)
			ret = -ENODEV;
		if (!ret) {
			wgfp_ops_priv = priv;
			rcu_assign_pointer(wgfp_ops, ops);
		}
	}
	mutex_unlock(&wgfp_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_register);

void dpaa_wgfp_unregister(void)
{
	/*
	 * Hold wgfp_lock across the grace period: readers load wgfp_ops and
	 * wgfp_ops_priv separately, so a register racing in before old readers
	 * finish would pair the old ops with the new priv.
	 */
	mutex_lock(&wgfp_lock);
	rcu_assign_pointer(wgfp_ops, NULL);
	synchronize_rcu();
	mutex_unlock(&wgfp_lock);
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_unregister);

static void wgfp_egress_ern(struct qman_portal *portal, struct qman_fq *fq,
			    const union qm_mr_entry *msg)
{
	atomic64_inc(&wgfp_xmit_ern);
	atomic64_inc(&wgfp_xmit_fail);
	dpaa_wgfp_fd_release(&msg->ern.fd);
}

static void wgfp_rx_ern_cb(struct qman_portal *portal, struct qman_fq *fq,
			   const union qm_mr_entry *msg)
{
	atomic64_inc(&wgfp_rx_ern);
	dpaa_wgfp_fd_release(&msg->ern.fd);
}

int dpaa_wgfp_egress(struct net_device *dev)
{
	int i, n, ret;
	u32 fqid;

	if (!dpaa_get_rx_fman_port(dev))	/* also the "is a DPAA port" test */
		return -ENODEV;
	mutex_lock(&wgfp_lock);
	n = wgfp_neg;
	for (i = 0; i < n; i++)
		if (wgfp_eg_ifindex[i] == dev->ifindex) {
			ret = i;
			goto out;
		}
	ret = -ENOSPC;
	if (n == WGFP_MAX_EGRESS)
		goto out;
	if (!wgfp_ern_fq_ready) {
		wgfp_ern_fq.cb.ern = wgfp_egress_ern;
		ret = qman_create_fq(0, QMAN_FQ_FLAG_DYNAMIC_FQID |
				     QMAN_FQ_FLAG_NO_MODIFY, &wgfp_ern_fq);
		if (ret) {
			pr_err("dpaa_wgfp: allocate ERN tag: %d\n", ret);
			goto out;
		}
		wgfp_ern_fq_ready = true;
	}
	ret = dpaa_alloc_offload_tx_fq(dev, &fqid);
	if (ret)
		goto out;
	wgfp_eg_fqid[n] = fqid;
	wgfp_eg_ifindex[n] = dev->ifindex;
	smp_store_release(&wgfp_neg, n + 1);
	pr_info("dpaa_wgfp: egress %u = %s, no-confirm tx fqid=0x%x\n", n,
		dev->name, fqid);
	ret = n;
out:
	mutex_unlock(&wgfp_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_egress);

int dpaa_wgfp_egress_find(const struct net_device *dev)
{
	int i, n = smp_load_acquire(&wgfp_neg);

	for (i = 0; i < n; i++)
		if (wgfp_eg_ifindex[i] == dev->ifindex)
			return i;
	return -ENOENT;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_egress_find);

/*
 * P2b-2: flow records that steer an inner flow to a sink, on the ingress
 * port's own per-port ehash table (the table ASK2 arms on engage).  Same
 * record shape as the ethtool-ntuple steering (dpaa_ethtool.c, F-241):
 * routed key, plain ENQUEUE_PKT to rx_fqid.  The caller owns the record's
 * lifetime; every add needs a matching del.
 */
static int wgfp_fm_port(struct net_device *dev, struct fman **fm, u8 *port_id,
			int *key_size)
{
	struct fman_port *port = dpaa_get_rx_fman_port(dev);
	struct dpaa_priv *priv;
	int ks;

	if (!port)
		return -ENODEV;
	priv = netdev_priv(dev);
	*fm = fman_bind(priv->mac_dev->fman_dev);
	if (!*fm)
		return -ENODEV;
	*port_id = fman_port_get_id(port);
	ks = fman_pcd_fe_ehash_keysize(*fm, *port_id);
	if (ks < 0)
		return ks;
	if (ks != FMAN_PCD_FE_ROUTED_KEY_SIZE)
		return -EOPNOTSUPP;
	*key_size = ks;
	return 0;
}

/* F-269 routed key (50 bytes): family 0x40 | SIP @1 | DIP @5 (v4 lane) for
 * IPv4, family 0x60 | zero v4 lane | SIP6 @9 | DIP6 @25 for IPv6; then
 * proto @41 | sport @42 | dport @44 | VID @46 (0, untagged) | PPPoE SID @48 (0). */
static void wgfp_key(const struct dpaa_wgfp_tuple *t, u8 *key)
{
	memset(key, 0, FMAN_PCD_FE_ROUTED_KEY_SIZE);
	if (t->family == AF_INET6) {
		/* dual-lane: zero v4 lane, src6 @9, dst6 @25 (qdrant 2026-09-06) */
		key[0] = 0x60;
		memcpy(key + 9, &t->saddr6, 16);
		memcpy(key + 25, &t->daddr6, 16);
	} else {
		key[0] = 0x40;
		memcpy(key + 1, &t->saddr, 4);
		memcpy(key + 5, &t->daddr, 4);
	}
	key[41] = t->proto;
	memcpy(key + 42, &t->sport, 2);
	memcpy(key + 44, &t->dport, 2);
}

/* Offload family policy, provided by ask.ko (see dpaa_wgfp.h). */
static dpaa_wgfp_family_fn __rcu wgfp_family_fn;

void dpaa_wgfp_set_family_policy(dpaa_wgfp_family_fn fn)
{
	rcu_assign_pointer(wgfp_family_fn, fn);
	if (!fn)
		synchronize_rcu();
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_set_family_policy);

bool dpaa_wgfp_family_ok(struct net_device *dev, u8 family)
{
	dpaa_wgfp_family_fn fn;
	u8 want, mask = 0;

	want = family == AF_INET ? DPAA_WGFP_FAM_V4 :
	       family == AF_INET6 ? DPAA_WGFP_FAM_V6 : 0;
	if (!dev || !want)
		return false;
	rcu_read_lock();
	fn = rcu_dereference(wgfp_family_fn);
	if (fn)
		mask = fn(dev);
	rcu_read_unlock();
	return mask & want;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_family_ok);

bool dpaa_wgfp_port_ok(struct net_device *dev)
{
	struct fman *fm;
	u8 port_id;
	int ks;

	return !wgfp_fm_port(dev, &fm, &port_id, &ks);
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_port_ok);

int dpaa_wgfp_flow_add(struct net_device *in, const struct dpaa_wgfp_tuple *t,
		       u32 hash, bool rx)
{
	struct fman_pcd_fe_flow_action action;
	struct wgfp_sink *sink;
	struct wgfp_port *port;
	struct fman *fm;
	u8 port_id;
	int ks, err;

	/* the ingress port's `offload ipv4|ipv6` admits the inner family */
	if (!dpaa_wgfp_family_ok(in, t->family))
		return -EOPNOTSUPP;
	err = wgfp_fm_port(in, &fm, &port_id, &ks);
	if (err)
		return err;
	might_sleep();
	mutex_lock(&wgfp_lock);
	port = wgfp_port_get_locked(in);
	if (IS_ERR(port)) {
		mutex_unlock(&wgfp_lock);
		return PTR_ERR(port);
	}
	/* One outer flow can be decrypted on several CPUs: point its RX record
	 * at the port's distributor, which round-robins to these workers. */
	if (rx && wgfp_spread && !wgfp_port_orp_locked(port, in))
		sink = port->orp_sink;
	else if (rx && wgfp_fanout && !wgfp_port_dist_locked(port))
		sink = &port->dist_sink;
	else
		sink = &port->sink[hash % port->nsinks];
	memset(&action, 0, sizeof(action));
	wgfp_key(t, action.key);
	action.key_size = ks;
	action.table_idx = 0;
	action.rx_fqid = qman_fq_fqid(&sink->fq);
	action.flags = FMAN_PCD_FE_OWNER_WGFP;	/* F-280 */
	mutex_unlock(&wgfp_lock);
	err = fman_pcd_fe_flow_add(fm, port_id, &action);
	return err ? err : 0;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_flow_add);

int dpaa_wgfp_flow_del(struct net_device *in, const struct dpaa_wgfp_tuple *t)
{
	u8 key[FMAN_PCD_FE_ROUTED_KEY_SIZE];
	struct fman *fm;
	unsigned int wedged;
	u8 port_id;
	int ks, err;

	err = wgfp_fm_port(in, &fm, &port_id, &ks);
	if (err)
		return err;
	wgfp_key(t, key);
	wedged = fman_pcd_ehash_sync_wedged_count();
	err = fman_pcd_fe_flow_del_owner(fm, port_id, key, ks,
					 FMAN_PCD_FE_OWNER_WGFP);
	/* A delete reports its own SYNC timeout. Also fail closed if any SYNC
	 * wedged during this operation, even if the wedge was unrelated.
	 */
	if (!err && fman_pcd_ehash_sync_wedged_count() != wedged)
		return -ETIMEDOUT;
	return err;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_flow_del);

int dpaa_wgfp_flow_packets(struct net_device *in,
			   const struct dpaa_wgfp_tuple *t, u64 *packets)
{
	u8 key[FMAN_PCD_FE_ROUTED_KEY_SIZE];
	struct fman *fm;
	u64 bytes;
	u8 port_id;
	int ks, err;

	err = wgfp_fm_port(in, &fm, &port_id, &ks);
	if (err)
		return err;
	wgfp_key(t, key);
	return fman_pcd_fe_flow_get_stats(fm, port_id, key, ks, packets, &bytes,
					  NULL);
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_flow_packets);

void dpaa_wgfp_drop(struct dpaa_wgfp_frame *f)
{
	/* An ORP frame's sequence number is given up by the callback's settle. */
	dpaa_wgfp_fd_release(f->fd);
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_drop);

/* Send the frame its ORP slot owes; false when this CPU owes nothing. */
static bool wgfp_orp_xmit(struct dpaa_wgfp_frame *f, int egress,
			  const struct qm_fd *fd)
{
	struct wgfp_orp_ctx *c = this_cpu_ptr(&wgfp_orp_ctx);
	ktime_t end;

	if (likely(!c->owed))
		return false;
	end = ktime_add_ns(ktime_get(), WGFP_ORP_WAIT_NS);
	do {
		if (!qman_enqueue_orp_fqid(&wgfp_ern_fq, wgfp_eg_fqid[egress],
					   c->orp_fqid, c->seq, false, fd)) {
			c->owed = false;
			dpaa_wgfp_stat_inc(&wgfp_xmit_ok);
			return true;
		}
		cpu_relax();
	} while (ktime_before(ktime_get(), end));
	/* Nothing enqueued: the settle step fills the hole. */
	atomic64_inc(&wgfp_orp_wait_fail);
	atomic64_inc(&wgfp_xmit_fail);
	dpaa_wgfp_fd_release(f->fd);
	return true;
}

void dpaa_wgfp_xmit(struct dpaa_wgfp_frame *f, int egress, u32 new_off,
		    u32 new_len)
{
	struct qm_fd fd = *(const struct qm_fd *)f->fd;
	int tries = 8;

	if (unlikely(egress < 0 || egress >= smp_load_acquire(&wgfp_neg))) {
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
	if (unlikely(wgfp_orp_xmit(f, egress, &fd)))
		return;
	while (tries--) {
		if (!qman_enqueue_fqid(&wgfp_ern_fq, wgfp_eg_fqid[egress], &fd)) {
			dpaa_wgfp_stat_inc(&wgfp_xmit_ok);
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
