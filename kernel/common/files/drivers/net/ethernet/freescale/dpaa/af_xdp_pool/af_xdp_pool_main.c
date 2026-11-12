// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * af_xdp_pool_main.c - DPAA1 AF_XDP zero-copy pool flavor module
 *
 * Registers a dpaa_qmgmt_ops table with the fsl_dpa core driver
 * (CONFIG_DPAA_FLAVOR_OPS).  While an XSK pool is attached, FMan's RX
 * BMI is reprogrammed to DMA into a dedicated BMan pool that is filled
 * from the XSK fill ring, and received frames are redirected into the
 * XSK RX ring without a copy.
 *
 * Buffer ownership.  The BMI pool is port-wide, so one XSK pool owns the
 * whole port while attached (a second attach fails with -EBUSY).  Every
 * XSK chunk that is released to BMan is first entered in a cookie map
 * (BMan buffer address -> struct xdp_buff *); exactly one of the
 * following then removes it and returns the chunk to the XSK pool or to
 * user space:
 *   - the RX hook (frame received; redirected, copied to an skb, or freed),
 *   - the RX error hook (frame discarded by FMan with this pool's bpid),
 *   - the detach drain (chunk still sitting in BMan).
 * A buffer that fails an invariant is counted and left allocated (leaked
 * until the pool is destroyed) rather than handed back while hardware may
 * still write to it.
 *
 * Geometry.  FMan writes its prefix at the buffer start and the frame at
 * buffer + priv->rx_headroom.  The BMan buffer address is therefore
 * dma(xdp->data) - rx_headroom, which lies inside the XSK headroom, and
 * the BMI buffer size is rx_headroom + frame_len, so FMan never writes
 * outside the chunk.
 *
 * Locking.  XSK pool allocation, xsk_buff_free() and the redirect into
 * the RX ring are not safe against each other on different CPUs, and the
 * RX FQs are spread over all CPUs.  port->lock serialises every one of
 * them.  It is taken with plain spin_lock() in NAPI context (the hook
 * runs only there) and spin_lock_bh() from process context.
 */

#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/mm.h>
#include <linux/types.h>
#include <linux/errno.h>
#include <linux/printk.h>
#include <linux/delay.h>
#include <linux/spinlock.h>
#include <linux/dma-mapping.h>
#include <linux/netdevice.h>
#include <linux/etherdevice.h>
#include <linux/if_vlan.h>
#include <linux/rtnetlink.h>
#include <linux/debugfs.h>
#include <linux/seq_file.h>
#include <linux/filter.h>
#include <net/xdp.h>
#include <net/xdp_sock_drv.h>
#include <net/xsk_buff_pool.h>
#include <soc/fsl/bman.h>
#include <soc/fsl/qman.h>

#include "dpaa_flavor.h"
#include "dpaa_eth.h"
#include "../../fman/fman.h"
#include "../../fman/fman_port.h"
#include "../../fman/mac.h"
#include <linux/fsl/fman_pcd.h>

/*
 * mac_dev->port[] is indexed by dpaa_eth.c's file-local
 * `enum port_type { RX = 0, TX = 1 }` (DTS fsl,fman-ports phandle order),
 * which is the INVERSE of `enum fman_port_type { FMAN_PORT_TYPE_TX = 0,
 * FMAN_PORT_TYPE_RX = 1 }` from fman.h.  dpaa_eth.c's enum is .c-private,
 * so mirror its RX index locally.
 */
#define DPAA_MAC_PORT_RX 0

/* Mirror of dpaa_eth.c FM_FD_STAT_RX_ERRORS. */
#define AFX_FD_RX_ERRORS						\
	(FM_FD_ERR_DMA | FM_FD_ERR_PHYSICAL | FM_FD_ERR_SIZE |		\
	 FM_FD_ERR_CLS_DISCARD | FM_FD_ERR_EXTRACTION |			\
	 FM_FD_ERR_NO_SCHEME | FM_FD_ERR_PRS_TIMEOUT |			\
	 FM_FD_ERR_PRS_ILL_INSTRUCT | FM_FD_ERR_PRS_HDR_ERR)

/* FMan DMAs into 16-byte aligned buffers; the address fits an FD (40 bit). */
#define AFX_BUF_ALIGN		16
#define AFX_ADDR_LIMIT		(1ULL << 40)
#define AFX_TABLE_BITS		14
#define AFX_FILL_BUDGET		256
#define AFX_SETTLE_TRIES	200

/* dpaa_eth.c-private: FD bpid meaning "no BMan buffer", and the FD command
 * bits FMan reads for the confirmation: FCO plus the 24-bit confirmation FQID.
 */
#define AFX_BPID_INV		0xff
#define AFX_FD_CMD_FQID_MASK	0x00ffffff

/* AFX_MAP_BEGIN: plain C, extracted and exercised by tests/dpaa_xsk_zc.py */

/*
 * Open-addressed cookie map with linear probing and backshift deletion.
 * A slot is occupied iff val != NULL.  The table holds at least twice
 * max_live slots, so a probe always terminates.
 */
struct afx_slot {
	u64 key;
	void *val;
};

struct afx_map {
	struct afx_slot *slot;
	u32 mask;
	u32 shift;	/* 64 - log2(table size) */
	u32 live;
	u32 max_live;
};

static inline u32 afx_home(const struct afx_map *m, u64 key)
{
	return (u32)(((key >> 4) * 0x9E3779B97F4A7C15ULL) >> m->shift);
}

static inline bool afx_cookie_ok(u64 cookie)
{
	return cookie && !(cookie & (AFX_BUF_ALIGN - 1)) &&
	       cookie < AFX_ADDR_LIMIT;
}

static int afx_map_insert(struct afx_map *m, u64 key, void *val)
{
	u32 i;

	if (m->live >= m->max_live)
		return -ENOSPC;
	for (i = afx_home(m, key); m->slot[i].val; i = (i + 1) & m->mask)
		if (m->slot[i].key == key)
			return -EEXIST;
	m->slot[i].key = key;
	m->slot[i].val = val;
	m->live++;
	return 0;
}

static void *afx_map_erase(struct afx_map *m, u64 key)
{
	u32 i, j, k;
	void *val;

	for (i = afx_home(m, key); m->slot[i].val; i = (i + 1) & m->mask)
		if (m->slot[i].key == key)
			break;
	if (!m->slot[i].val)
		return NULL;

	val = m->slot[i].val;
	for (j = (i + 1) & m->mask; m->slot[j].val; j = (j + 1) & m->mask) {
		k = afx_home(m, m->slot[j].key);
		/* j stays put when its home k lies cyclically in (i, j] */
		if (i <= j ? (i < k && k <= j) : (i < k || k <= j))
			continue;
		m->slot[i] = m->slot[j];
		i = j;
	}
	m->slot[i].key = 0;
	m->slot[i].val = NULL;
	m->live--;
	return val;
}
/* AFX_MAP_END */

struct afx_stats {
	u64 rx_miss;		/* FD with the XSK bpid but no map entry */
	u64 rx_bad_fd;		/* wrong format/offset/length/status */
	u64 rx_quiesced;	/* arrived after detach started */
	u64 rx_pass;		/* copied to an skb for the stack */
	u64 rx_pass_fail;	/* skb copy failed; frame dropped */
	u64 rx_redirect_fail;	/* xdp_do_redirect() error; frame dropped */
	u64 rx_drop;		/* DROP/ABORTED/other XDP verdicts */
	u64 err_release;	/* error-FQ FD returned to the pool */
	u64 tx_err;		/* Tx error/ERN FD completed to the XSK ring */
	u64 fill_batches;
	u64 fill_bad_cookie;	/* misaligned or out-of-range, left allocated */
	u64 fill_dup;		/* cookie already live, left allocated */
	u64 fill_release_fail;	/* bman_release() failed, chunk freed */
};

/*
 * Dedicated Tx confirmation FQ of the attached qband.  Every ZC Tx FD names
 * this FQID in its command word, so a confirmation reaching afx_txconf_dqrr()
 * is by construction an XSK chunk; no other traffic is ever dispatched to it.
 */
struct afx_txconf {
	struct qman_fq fq;
	struct dpaa_priv *priv;
	u16 qid;
};

struct af_xdp_port {
	spinlock_t lock;
	struct afx_txconf txc;
	struct afx_map map;
	struct xsk_buff_pool *pool;
	struct bman_pool *bpool;
	u16 qid;
	u8 bpid;
	bool quiesced;		/* detach started: free, never redirect */
	u16 rx_off;		/* FD offset FMan applies */
	u32 data_max;		/* largest frame one buffer holds */
	struct afx_stats st;
};

struct qman_fq;
struct qm_dqrr_entry;
struct dpaa_napi_portal;
struct list_head;

static int af_xdp_pool_alloc_rx_fqs(struct dpaa_priv *priv,
				    struct list_head *list)
{
	return -EOPNOTSUPP;
}

static struct fman_port *afx_rx_port(struct dpaa_priv *priv)
{
	struct fman *fm;

	if (!priv->mac_dev || !priv->mac_dev->port[DPAA_MAC_PORT_RX])
		return NULL;
	fm = fman_bind(priv->mac_dev->fman_dev);
	return fm ? fman_port_lookup_rx(fm, fman_port_get_id(
			priv->mac_dev->port[DPAA_MAC_PORT_RX])) : NULL;
}

/* Return one Tx chunk to user space: advance the completion ring, free the slot. */
static void afx_tx_complete(struct dpaa_priv *priv, u16 q)
{
	struct xsk_buff_pool *pool;

	rcu_read_lock();
	pool = rcu_dereference(priv->xsk_pool[q]);
	if (pool)
		xsk_tx_completed(pool, 1);
	rcu_read_unlock();
	atomic_dec(&priv->xsk_tx_inflight[q]);
}

/*
 * Confirmation FD of a ZC Tx frame.  Runs on the CPU that owns the qband
 * (the FQ sits on that CPU's portal channel), the same context that
 * submits, so each qband's completion ring has a single producer.  Mirrors
 * dpaa_eth.c's dpaa_eth_napi_schedule(): from the portal interrupt only
 * schedule NAPI, the entry is dequeued again from the poll loop.
 */
static enum qman_cb_dqrr_result afx_txconf_dqrr(struct qman_portal *portal,
						struct qman_fq *fq,
						const struct qm_dqrr_entry *dq,
						bool sched_napi)
{
	struct afx_txconf *c = container_of(fq, struct afx_txconf, fq);
	struct dpaa_priv *priv = c->priv;
	struct dpaa_percpu_priv *pcpu = this_cpu_ptr(priv->percpu_priv);

	if (sched_napi) {
		qman_p_irqsource_remove(portal, QM_PIRQ_DQRI);
		pcpu->np.p = portal;
		napi_schedule(&pcpu->np.napi);
		pcpu->in_interrupt++;
		return qman_cb_dqrr_stop;
	}

	afx_tx_complete(priv, c->qid);
	priv->xsk_tx_conf_zc++;
	return qman_cb_dqrr_consume;
}

static int afx_txconf_create(struct dpaa_priv *priv, struct af_xdp_port *port,
			     u16 qid, int cpu)
{
	struct afx_txconf *c = &port->txc;
	struct qm_mcc_initfq initfq;
	int err;

	c->priv = priv;
	c->qid = qid;
	c->fq.cb.dqrr = afx_txconf_dqrr;

	err = qman_create_fq(0, QMAN_FQ_FLAG_NO_ENQUEUE |
			     QMAN_FQ_FLAG_DYNAMIC_FQID, &c->fq);
	if (err)
		return err;

	memset(&initfq, 0, sizeof(initfq));
	initfq.we_mask = cpu_to_be16(QM_INITFQ_WE_FQCTRL | QM_INITFQ_WE_DESTWQ);
	initfq.fqd.fq_ctrl = cpu_to_be16(QM_FQCTRL_PREFERINCACHE);
	/* Same work queue as the kernel's confirmation FQs. */
	qm_fqd_set_destwq(&initfq.fqd, qman_affine_channel(cpu), 1);

	err = qman_init_fq(&c->fq, QMAN_INITFQ_FLAG_SCHED, &initfq);
	if (err < 0) {
		qman_destroy_fq(&c->fq);
		return err;
	}
	return 0;
}

/* Only called with nothing in flight, so the FQ is empty. */
static void afx_txconf_destroy(struct dpaa_priv *priv, struct afx_txconf *c)
{
	struct device *dev = priv->net_dev->dev.parent;
	int err, i;

	err = qman_retire_fq(&c->fq, NULL);
	if (err < 0)
		dev_warn(dev, "af_xdp_pool: retire Tx confirm FQ %u: %d\n",
			 c->fq.fqid, err);
	/* 1: retirement pending until the portal sees the FQRN. */
	for (i = 0; err >= 0 && i < 100 &&
	     c->fq.state != qman_fq_state_retired; i++)
		msleep(10);
	err = qman_oos_fq(&c->fq);
	if (err < 0)
		dev_warn(dev, "af_xdp_pool: OOS Tx confirm FQ %u: %d\n",
			 c->fq.fqid, err);
	qman_destroy_fq(&c->fq);
}

/*
 * Move chunks from the XSK fill ring into BMan.  Each chunk is entered in
 * the map BEFORE bman_release(), because FMan may use it the moment it is
 * released.  Called with port->lock held.
 */
static void afx_fill_locked(struct dpaa_priv *priv, struct af_xdp_port *port)
{
	struct xdp_buff *xdps[32], *ok[32];
	struct bm_buffer bmbs[32];
	u32 budget = AFX_FILL_BUDGET;
	bool failed = false;

	lockdep_assert_held(&port->lock);

	while (budget && !port->quiesced && !failed) {
		u32 want = min3(32u, budget, port->map.max_live - port->map.live);
		u32 n, i, m = 0, off;

		if (!want)
			break;
		n = xsk_buff_alloc_batch(port->pool, xdps, want);
		if (!n)
			break;
		budget -= n;

		for (i = 0; i < n; i++) {
			u64 cookie = (u64)xsk_buff_xdp_get_dma(xdps[i]) -
				     port->rx_off;

			if (!afx_cookie_ok(cookie)) {
				port->st.fill_bad_cookie++;
				continue;
			}
			if (afx_map_insert(&port->map, cookie, xdps[i])) {
				port->st.fill_dup++;
				continue;
			}
			/* bman_release() copies the whole entry into the
			 * RCR, so start from zero.
			 */
			bmbs[m].data = 0;
			bm_buffer_set64(&bmbs[m], cookie);
			ok[m++] = xdps[i];
		}

		/* The RCR verb carries 1..8 buffers. */
		for (off = 0; off < m; off += 8) {
			u32 cnt = min_t(u32, 8, m - off);

			if (!bman_release(port->bpool, &bmbs[off], cnt))
				continue;
			for (i = off; i < off + cnt; i++) {
				afx_map_erase(&port->map,
					      bm_buffer_get64(&bmbs[i]));
				xsk_buff_free(ok[i]);
			}
			port->st.fill_release_fail++;
			failed = true;
		}
		if (m)
			priv->xsk_bman_refill_batches++;
		port->st.fill_batches++;
	}
}

/*
 * RX hook, called from rx_default_dqrr() in NAPI context.  Consumes every
 * FD that carries the XSK pool's bpid, whatever its content: such a buffer
 * belongs to the XSK pool and must never reach the kernel page-pool path.
 */
static bool af_xdp_pool_rx_hook(struct dpaa_priv *priv,
				struct qman_fq *fq,
				const struct qm_dqrr_entry *dq)
{
	const struct qm_fd *fd = &dq->fd;
	struct dpaa_percpu_priv *pcpu;
	struct af_xdp_port *port;
	struct sk_buff *skb = NULL;
	struct bpf_prog *prog;
	struct xdp_buff *xdp;
	u32 len;
	int act;

	/* rx_default_dqrr() defers hard-IRQ deliveries to NAPI first, so the
	 * XSK flush below is only ever reached from softirq.
	 */
	if (WARN_ON_ONCE(in_hardirq()))
		return false;

	port = smp_load_acquire(&priv->xsk_port);
	if (!port || fd->bpid != port->bpid)
		return false;

	spin_lock(&port->lock);
	priv->xsk_zc_eligible++;

	xdp = afx_map_erase(&port->map, qm_fd_addr(fd));
	if (unlikely(!xdp)) {
		port->st.rx_miss++;
		dev_warn_once(priv->net_dev->dev.parent,
			      "af_xdp_pool: FD 0x%llx has no XSK buffer; dropped\n",
			      (u64)qm_fd_addr(fd));
		goto out;
	}
	priv->xsk_zc_rx_recovered++;

	len = qm_fd_get_length(fd);
	if (unlikely(port->quiesced)) {
		port->st.rx_quiesced++;
		xsk_buff_free(xdp);
		goto out;
	}
	if (unlikely(qm_fd_get_format(fd) != qm_fd_contig ||
		     qm_fd_get_offset(fd) != port->rx_off ||
		     len > port->data_max ||
		     (be32_to_cpu(fd->status) & AFX_FD_RX_ERRORS))) {
		port->st.rx_bad_fd++;
		xsk_buff_free(xdp);
		goto out;
	}

	xsk_buff_set_size(xdp, len);
	xsk_buff_dma_sync_for_cpu(xdp);

	prog = READ_ONCE(priv->xdp_prog);
	act = XDP_PASS;
	if (prog) {
		struct bpf_net_context __bpf_net_ctx, *bpf_net_ctx;

		bpf_net_ctx = bpf_net_ctx_set(&__bpf_net_ctx);
		act = bpf_prog_run_xdp(prog, xdp);
		if (act == XDP_REDIRECT) {
			if (!xdp_do_redirect(priv->net_dev, xdp, prog)) {
				priv->xsk_zc_rx_redirect++;
				/* xdp_do_redirect() only queues the
				 * descriptor; xdp_do_flush() publishes it
				 * to user space.  Per frame, because the
				 * bpf_net_context ends with this hook.
				 */
				xdp_do_flush();
			} else {
				port->st.rx_redirect_fail++;
				xsk_buff_free(xdp);
			}
		}
		if (bpf_net_ctx)
			bpf_net_ctx_clear(bpf_net_ctx);
	}

	switch (act) {
	case XDP_REDIRECT:
		break;
	case XDP_PASS:
		/* Port-wide pool: Linux traffic arrives in XSK buffers too,
		 * so hand the stack a copy and return the chunk.  Not
		 * xdp_build_skb_from_zc(): it places skb_shared_info at
		 * frame_sz, which a full-size frame at this headroom overruns.
		 */
		pcpu = this_cpu_ptr(priv->percpu_priv);
		skb = napi_alloc_skb(&pcpu->np.napi, len);
		if (skb) {
			skb_put_data(skb, xdp->data, len);
			xsk_buff_free(xdp);
			port->st.rx_pass++;
		} else {
			port->st.rx_pass_fail++;
			xsk_buff_free(xdp);
		}
		break;
	default:
		port->st.rx_drop++;
		xsk_buff_free(xdp);
		break;
	}
out:
	spin_unlock(&port->lock);

	if (skb) {
		skb->protocol = eth_type_trans(skb, priv->net_dev);
		pcpu->stats.rx_packets++;
		pcpu->stats.rx_bytes += skb->len;
		napi_gro_receive(&pcpu->np.napi, skb);
	}
	return true;
}

/* RX error FQ: an FD FMan discarded still carries its XSK buffer. */
static bool af_xdp_pool_rx_release(struct dpaa_priv *priv,
				   const struct qm_fd *fd)
{
	struct af_xdp_port *port = smp_load_acquire(&priv->xsk_port);
	struct xdp_buff *xdp;

	if (!port || fd->bpid != port->bpid)
		return false;

	spin_lock(&port->lock);
	xdp = afx_map_erase(&port->map, qm_fd_addr(fd));
	if (xdp) {
		xsk_buff_free(xdp);
		port->st.err_release++;
	} else {
		port->st.rx_miss++;
	}
	spin_unlock(&port->lock);
	return true;
}

/*
 * af_xdp_pool_xsk_pool_attach - install an AF_XDP zero-copy pool on the port
 *
 * Validates the pool geometry against the RX buffer layout, maps the UMEM,
 * allocates the dedicated BMan pool and the cookie map, registers the XSK
 * rxq, then reprograms the FMan RX port to DMA into the XSK pool.  Any
 * failure unwinds everything and returns an error, so the XSK core never
 * believes a pool is zero-copy when the port is not reprogrammed.
 */
static int af_xdp_pool_xsk_pool_attach(struct dpaa_priv *priv,
				       struct xsk_buff_pool *pool,
				       u16 queue_id)
{
	struct net_device *ndev;
	struct af_xdp_port *port;
	struct fman_port *rxp;
	u32 rx_off, data_max, bsize;
	u32 slots = 1U << AFX_TABLE_BITS;
	int ret, target_cpu;

	if (!priv || !pool)
		return -EINVAL;

	ndev = priv->net_dev;
	rx_off = priv->rx_headroom;

	if (queue_id >= priv->xsk_max_qbands ||
	    queue_id >= DPAA1_XSK_MAX_QBANDS) {
		priv->xsk_pool_attach_fail++;
		return -EINVAL;
	}
	if (priv->xsk_port) {
		priv->xsk_pool_attach_fail++;
		return -EBUSY;
	}

	/* FMan writes the buffer prefix and the frame at rx_off from the
	 * buffer start; both must fit in the headroom plus frame area.
	 */
	if (xsk_pool_get_headroom(pool) < rx_off ||
	    xsk_pool_get_headroom(pool) < priv->tx_headroom) {
		priv->xsk_headroom_reject++;
		priv->xsk_pool_attach_fail++;
		return -EINVAL;
	}

	bsize = ALIGN_DOWN(rx_off + pool->frame_len, AFX_BUF_ALIGN);
	data_max = bsize - rx_off;
	if (bsize > U16_MAX ||
	    max_t(u32, ndev->mtu, ETH_DATA_LEN) + ETH_HLEN + ETH_FCS_LEN +
	    2 * VLAN_HLEN > data_max) {
		priv->xsk_mtu_reject++;
		priv->xsk_pool_attach_fail++;
		return -EINVAL;
	}

	rxp = afx_rx_port(priv);
	if (!rxp || !priv->dpaa_bp) {
		priv->xsk_pamu_window_fail++;
		priv->xsk_pool_attach_fail++;
		return -ENODEV;
	}

	port = kzalloc(sizeof(*port), GFP_KERNEL);
	if (!port) {
		priv->xsk_pool_attach_fail++;
		return -ENOMEM;
	}
	spin_lock_init(&port->lock);
	port->map.slot = kvcalloc(slots, sizeof(*port->map.slot), GFP_KERNEL);
	if (!port->map.slot) {
		ret = -ENOMEM;
		goto err_port;
	}
	port->map.mask = slots - 1;
	port->map.shift = 64 - AFX_TABLE_BITS;
	port->map.max_live = DPAA1_XSK_INITIAL_SEED;
	port->pool = pool;
	port->qid = queue_id;
	port->rx_off = rx_off;
	port->data_max = data_max;

	/* The PAMU is bypassed on this SoC; dma-direct gives the CoreNet
	 * coherent addresses FMan BMI writes to.
	 */
	WARN_ON(!priv->rx_dma_dev);
	ret = xsk_pool_dma_map(pool, priv->rx_dma_dev, DMA_ATTR_SKIP_CPU_SYNC);
	if (ret) {
		priv->xsk_dma_map_fail++;
		ret = -EFAULT;
		goto err_map;
	}

	port->bpool = bman_new_pool();
	if (!port->bpool) {
		ret = -ENOMEM;
		goto err_dma;
	}
	port->bpid = bman_get_bpid(port->bpool);

	/* xsk_buff_alloc() leaves xdp->rxq NULL; the redirect path
	 * dereferences it.
	 */
	ret = xdp_rxq_info_reg(&priv->xsk_zc_rxq[queue_id], ndev, queue_id, 0);
	if (ret)
		goto err_bpool;
	ret = xdp_rxq_info_reg_mem_model(&priv->xsk_zc_rxq[queue_id],
					 MEM_TYPE_XSK_BUFF_POOL, NULL);
	if (ret)
		goto err_rxq;
	xsk_pool_set_rxq_info(pool, &priv->xsk_zc_rxq[queue_id]);

	/* The qband's Tx is submitted and confirmed on this CPU. */
	target_cpu = (int)queue_id % max(num_online_cpus(), 1U);
	atomic_set(&priv->xsk_tx_inflight[queue_id], 0);
	ret = afx_txconf_create(priv, port, queue_id, target_cpu);
	if (ret)
		goto err_rxq;

	priv->xsk_bpid[queue_id] = port->bpid;
	priv->xsk_bpool[queue_id] = port->bpool;
	priv->xsk_max_frm = data_max;
	rcu_assign_pointer(priv->xsk_pool[queue_id], pool);
	/* Pairs with smp_load_acquire() in the RX paths. */
	smp_store_release(&priv->xsk_port, port);

	if (xsk_uses_need_wakeup(pool))
		xsk_set_rx_need_wakeup(pool);

	{
		struct dpaa_percpu_priv *pcpu;

		pcpu = per_cpu_ptr(priv->percpu_priv, target_cpu);
		WRITE_ONCE(priv->qmap[queue_id].napi, &pcpu->np.napi);
		WRITE_ONCE(priv->qmap[queue_id].cpu, target_cpu);
	}

	{
		struct fman *fm = fman_bind(priv->mac_dev->fman_dev);
		struct fman_pcd *pcd = fm ? fman_get_pcd(fm) : NULL;

		/* A valid per-port FM_CTL params page, or FE_ENTER books
		 * workspace at MURAM offset 0 on every frame (F-072).
		 */
		if (pcd)
			(void)fman_pcd_port_ensure_params_page(pcd, rxp);
	}

	/* The BMI must be quiet while its pool registers are rewritten. */
	ret = fman_port_disable(rxp);
	if (!ret)
		ret = fman_port_set_rx_bpool(rxp, (u8)priv->dpaa_bp->bpid,
					     port->bpid, (u16)bsize);
	/* F-274: a drain timeout (-EBUSY) skips the reprogram; the port must
	 * come back up either way.
	 */
	fman_port_enable(rxp);
	if (ret) {
		dev_warn(ndev->dev.parent,
			 "af_xdp_pool: RX port BPID reprogram failed (%d); no zero-copy\n",
			 ret);
		goto err_unpublish;
	}

	spin_lock_bh(&port->lock);
	afx_fill_locked(priv, port);
	spin_unlock_bh(&port->lock);

	priv->xsk_zc_rx_armed++;
	priv->xsk_pool_attach_ok++;
	dev_info(ndev->dev.parent,
		 "af_xdp_pool: RX port on XSK bpid %u (buffer %u, offset %u, max frame %u)\n",
		 port->bpid, bsize, rx_off, data_max);
	return 0;

err_unpublish:
	smp_store_release(&priv->xsk_port, NULL);
	rcu_assign_pointer(priv->xsk_pool[queue_id], NULL);
	priv->xsk_max_frm = 0;
	priv->xsk_bpid[queue_id] = 0;
	priv->xsk_bpool[queue_id] = NULL;
	synchronize_net();
	afx_txconf_destroy(priv, &port->txc);
err_rxq:
	xdp_rxq_info_unreg(&priv->xsk_zc_rxq[queue_id]);
err_bpool:
	bman_free_pool(port->bpool);
err_dma:
	xsk_pool_dma_unmap(pool, DMA_ATTR_SKIP_CPU_SYNC);
err_map:
	kvfree(port->map.slot);
err_port:
	kfree(port);
	priv->xsk_pool_attach_fail++;
	return ret;
}

/*
 * Drain every buffer BMan still holds for the pool, passing each buffer
 * address to @release.  bman_acquire() returns -ENOMEM unless it yields
 * exactly the requested count, so an empty or short pool is reported as an
 * error, never as 0.  Mirror dpaa_bp_drain(): retry singly after a short
 * eight-buffer acquire and treat -ENOMEM on a single acquire as empty.
 * -ETIMEDOUT or an exhausted budget is not proof of an empty pool and is
 * retried.
 */
static int af_xdp_pool_drain_bman(struct bman_pool *bpool,
				  void (*release)(void *ctx, u64 addr),
				  void *ctx)
{
	struct bm_buffer bmbs[8];
	u8 num = 8;
	int loops, n, i;

	for (loops = 0; loops < 16384; loops++) {
		n = bman_acquire(bpool, bmbs, num);
		if (n == -ENOMEM) {
			if (num == 8) {
				num = 1;
				continue;
			}
			return 0;
		}
		if (n < 0)
			return -EAGAIN;
		for (i = 0; i < n; i++)
			release(ctx, bm_buffer_get64(&bmbs[i]));
	}
	return -EAGAIN;
}

/* A chunk still in BMan goes back to the XSK pool. */
static void afx_drain_release(void *ctx, u64 addr)
{
	struct af_xdp_port *port = ctx;
	struct xdp_buff *xdp;

	spin_lock_bh(&port->lock);
	xdp = afx_map_erase(&port->map, addr);
	if (xdp)
		xsk_buff_free(xdp);
	else
		port->st.rx_miss++;
	spin_unlock_bh(&port->lock);
}

/*
 * af_xdp_pool_xsk_pool_detach - tear down the zero-copy pool
 *
 *  1. Disable the RX port and restore the kernel BPID and buffer size.
 *     Either failing returns -EAGAIN with the pool intact; the XSK core
 *     retries.  No new frame enters an XSK buffer after this.
 *  2. Mark the port quiesced, so the hook frees instead of redirecting and
 *     the refill stops.
 *  3. Settle: drain BMan and wait for FDs already queued in QMan to reach
 *     the hook (still published), until the map is empty.  A chunk can be
 *     in only one of those places.  A drain that cannot complete returns
 *     -EAGAIN and is resumed by the retry.
 *  4. Unpublish and wait out NAPI readers, then release the DMA mapping,
 *     the BMan pool and the map.
 *  5. Re-enable the RX port for the kernel pool.
 */
static int af_xdp_pool_xsk_pool_detach(struct dpaa_priv *priv, u16 queue_id)
{
	struct af_xdp_port *port;
	struct fman_port *rxp;
	int ret, tries;
	u32 live;

	if (!priv)
		return -EINVAL;
	if (queue_id >= DPAA1_XSK_MAX_QBANDS ||
	    queue_id >= priv->xsk_max_qbands)
		return -EINVAL;

	port = priv->xsk_port;
	if (!port || port->qid != queue_id)
		return -ENOENT;

	rxp = afx_rx_port(priv);

	if (!port->quiesced) {
		if (!rxp) {
			priv->xsk_pool_detach_timeout++;
			return -EAGAIN;
		}
		ret = fman_port_disable(rxp);
		if (ret) {
			priv->xsk_pool_detach_timeout++;
			dev_warn_ratelimited(priv->net_dev->dev.parent,
					     "af_xdp_pool: RX port disable failed (%d); deferring detach\n",
					     ret);
			return -EAGAIN;
		}
		ret = fman_port_set_rx_bpool(rxp, port->bpid,
					     (u8)priv->dpaa_bp->bpid,
					     (u16)priv->dpaa_bp->size);
		if (ret) {
			priv->xsk_pool_detach_timeout++;
			dev_warn_ratelimited(priv->net_dev->dev.parent,
					     "af_xdp_pool: BPID restore failed (%d); deferring detach\n",
					     ret);
			return -EAGAIN;
		}
		spin_lock_bh(&port->lock);
		port->quiesced = true;
		spin_unlock_bh(&port->lock);
	}

	for (tries = 0; tries < AFX_SETTLE_TRIES; tries++) {
		ret = af_xdp_pool_drain_bman(port->bpool, afx_drain_release,
					     port);
		spin_lock_bh(&port->lock);
		live = port->map.live;
		spin_unlock_bh(&port->lock);
		if (!ret && !live)
			break;
		msleep(10);
	}
	if (ret) {
		priv->xsk_pool_detach_timeout++;
		dev_warn_ratelimited(priv->net_dev->dev.parent,
				     "af_xdp_pool: BMan drain incomplete; deferring detach\n");
		return -EAGAIN;
	}
	if (live) {
		/* Chunks still queued in QMan name this BPID and UMEM; keep
		 * everything and let the core retry.
		 */
		priv->xsk_pool_detach_timeout++;
		dev_warn_ratelimited(priv->net_dev->dev.parent,
				     "af_xdp_pool: %u XSK buffers still in QMan; deferring detach\n",
				     live);
		return -EAGAIN;
	}

	/* Quiesced: no new Tx is submitted once running NAPI passes have
	 * ended.  Every submitted chunk must be confirmed before the pool and
	 * the confirmation FQ go away.
	 */
	synchronize_net();
	for (tries = 0; tries < AFX_SETTLE_TRIES; tries++) {
		if (!atomic_read(&priv->xsk_tx_inflight[queue_id]))
			break;
		msleep(10);
	}
	if (atomic_read(&priv->xsk_tx_inflight[queue_id])) {
		priv->xsk_pool_detach_timeout++;
		dev_warn_ratelimited(priv->net_dev->dev.parent,
				     "af_xdp_pool: %d Tx chunks still in flight; deferring detach\n",
				     atomic_read(&priv->xsk_tx_inflight[queue_id]));
		return -EAGAIN;
	}

	smp_store_release(&priv->xsk_port, NULL);
	rcu_assign_pointer(priv->xsk_pool[queue_id], NULL);
	priv->xsk_max_frm = 0;
	synchronize_net();

	afx_txconf_destroy(priv, &port->txc);
	if (xdp_rxq_info_is_reg(&priv->xsk_zc_rxq[queue_id]))
		xdp_rxq_info_unreg(&priv->xsk_zc_rxq[queue_id]);
	xsk_pool_dma_unmap(port->pool, DMA_ATTR_SKIP_CPU_SYNC);
	bman_free_pool(port->bpool);
	priv->xsk_bpool[queue_id] = NULL;
	priv->xsk_bpid[queue_id] = 0;
	kvfree(port->map.slot);
	kfree(port);

	if (rxp && fman_port_enable(rxp))
		priv->xsk_pool_detach_timeout++;

	priv->xsk_pool_detach_ok++;
	return 0;
}

/*
 * af_xdp_pool_xsk_wakeup - kick the NAPI/QMan portal for an XSK queue
 *
 * Patch 0074 (Phase 1 hot-path wake gate, spec sec 5.2):
 *   For XDP_WAKEUP_RX, resolve the NAPI associated with the qband and
 *   schedule it via napi_schedule_irqoff() so the dpaa_eth_poll() loop
 *   re-runs and drains any pending DQRR entries into the XSK fill ring.
 *
 *   The qmap[].napi slot is populated by the flavor module's future
 *   xsk_pool_attach (patch 0075) once the dedicated XSK BMan channel
 *   and its NAPI context have been brought up.  Until that lands, the
 *   slot reads NULL and we return -ENOENT so the XSK core sees a clean
 *   error (no NAPI bound on this qband) instead of NULL-deref'ing in
 *   the IRQ-off scheduler path.  XDP_WAKEUP_TX handling lands with
 *   patch 0078 (qman_p_irqsource_add poke of the egress portal).
 */
static int af_xdp_pool_xsk_wakeup(struct dpaa_priv *priv, u32 queue_id,
  u32 flags)
{
struct napi_struct *napi;

if (!priv)
return -EINVAL;
if (queue_id >= DPAA1_XSK_MAX_QBANDS)
return -EINVAL;

napi = READ_ONCE(priv->qmap[queue_id].napi);
if (!napi)
return -ENOENT;

if (flags & XDP_WAKEUP_RX) {
if (!napi_if_scheduled_mark_missed(napi))
napi_schedule_irqoff(napi);
}

/* XDP_WAKEUP_TX path arrives with patch 0078. */
return 0;
}

/*
 * af_xdp_pool_xsk_set_rx_need_wakeup - arm the XSK rx-need-wakeup flag
 *
 * Called from dpaa_eth_poll() AFTER napi_complete_done() and BEFORE
 * re-arming the QMan DQRR interrupt (see dpaa_eth.c patch 0072).
 *
 * For each bound xsk_pool[qband] that requested XDP_USE_NEED_WAKEUP at
 * bind time (xsk_uses_need_wakeup(pool) == true), assert the rx
 * need-wakeup flag.  Userspace sees the flag via
 * xsk_ring_prod__needs_wakeup() on the fill ring and issues
 * sendto(MSG_DONTWAIT) which lands in af_xdp_pool_xsk_wakeup() and
 * schedules the NAPI captured in priv->qmap[queue_id].napi.
 *
 * The matching xsk_clear_rx_need_wakeup() lives in the RX hot-path
 * branch (later M3-3 subpatch) where it is cleared the moment we
 * dequeue any descriptor for a given qband.  Until that lands the
 * flag stays asserted across consecutive idle polls — which is
 * correct: userspace knows the driver is idle and that an explicit
 * wakeup is required to resume RX servicing.
 *
 * @np is the per-CPU NAPI portal that just completed its poll; the
 * spec calls for arming the flag on any pool routed to this portal.
 * Phase 3a maps every FQID to qband 0 (dpaa_fq_to_qband stub), so
 * any portal completing a poll arms ALL bound qbands.  Once the
 * flavor module wires the real qband→portal map (dedicated XSK
 * BMan channels), this iteration narrows to the portal's qbands.
 *
 * Spec sec 5.2 final paragraph + sec 5.4 RX path step 5.
 */
static void af_xdp_pool_xsk_set_rx_need_wakeup(struct dpaa_priv *priv,
       struct dpaa_napi_portal *np)
{
struct xsk_buff_pool *pool;
u16 q;

if (!priv)
return;

for (q = 0; q < priv->xsk_max_qbands && q < DPAA1_XSK_MAX_QBANDS; q++) {
pool = rcu_dereference(priv->xsk_pool[q]);
if (!pool)
continue;
if (!xsk_uses_need_wakeup(pool))
continue;
xsk_set_rx_need_wakeup(pool);
}
}

/*
 * af_xdp_pool_napi_refill - top up BMan from the XSK fill ring
 *
 * Called from dpaa_eth_poll() on every completion, on any CPU.  The port
 * lock is taken with a trylock: if another CPU is already refilling, this
 * one skips and the next poll tries again.
 */
static void af_xdp_pool_napi_refill(struct dpaa_priv *priv)
{
	struct af_xdp_port *port = smp_load_acquire(&priv->xsk_port);

	if (!port || !spin_trylock(&port->lock))
		return;
	afx_fill_locked(priv, port);
	spin_unlock(&port->lock);
}

/*
 * af_xdp_pool_napi_tx_zc - drain the XSK Tx ring into the DPAA1 egress FQs
 *
 * Called from dpaa_eth_poll() in the rcu_read_lock() region at NAPI
 * completion.  Only the CPU that owns the attached qband submits, so the
 * Tx ring has a single consumer.  Per call:
 *   1. If inflight >= DPAA1_XSK_TX_MAX_INFLIGHT, count backpressure, stop.
 *   2. Take up to min(MAX_INFLIGHT - inflight, 64) descriptors.
 *   3. Per descriptor build a contiguous FD at the UMEM chunk and enqueue
 *      it on this CPU's egress FQ.
 *
 * FD ownership.  bpid is FSL_DPAA_BPID_INV, so FMan has no BMan pool to
 * release the chunk to, and FCO plus the dedicated confirmation FQID makes
 * FMan enqueue the confirmation to afx_txconf_dqrr() instead.  That, and
 * only that, completes the chunk (xsk_tx_completed() + inflight--).  A real
 * bpid here would let FMan deallocate the chunk into that pool with no
 * confirmation at all.
 */
static void af_xdp_pool_napi_tx_zc(struct dpaa_priv *priv)
{
	struct xsk_buff_pool *pool;
	struct af_xdp_port *port;
	struct qman_fq *egress_fq;
	struct xdp_desc *descs;
	struct qm_fd fd;
	u32 budget, n, i, submitted;
	int inflight, j, err;
	u16 q;

	if (!priv)
		return;

	port = smp_load_acquire(&priv->xsk_port);
	if (!port || READ_ONCE(port->quiesced))
		return;
	q = port->qid;
	if (q >= priv->xsk_max_qbands || q >= DPAA1_XSK_MAX_QBANDS)
		return;
	pool = rcu_dereference(priv->xsk_pool[q]);
	if (!pool)
		return;

	/* Only the qband-owner CPU drains this qband's Tx ring, keeping it
	 * single-consumer; confirmations arrive on the same CPU.
	 */
	if (raw_smp_processor_id() != READ_ONCE(priv->qmap[q].cpu))
		return;

	inflight = atomic_read(&priv->xsk_tx_inflight[q]);
	if (inflight >= DPAA1_XSK_TX_MAX_INFLIGHT) {
		priv->xsk_tx_backpressure++;
		return;
	}

	budget = min_t(u32, DPAA1_XSK_TX_MAX_INFLIGHT - inflight, 64);
	n = xsk_tx_peek_release_desc_batch(pool, budget);
	if (!n)
		return;

	egress_fq = priv->egress_fqs[raw_smp_processor_id() %
				     dpaa_max_num_txqs()];
	descs = pool->tx_descs;
	submitted = 0;
	for (i = 0; i < n; i++) {
		memset(&fd, 0, sizeof(fd));
		qm_fd_addr_set64(&fd, xsk_buff_raw_get_dma(pool, descs[i].addr));
		qm_fd_set_contig(&fd, 0, (int)descs[i].len);
		fd.bpid = AFX_BPID_INV;
		fd.cmd = cpu_to_be32(FM_FD_CMD_FCO |
				     (qman_fq_fqid(&port->txc.fq) &
				      AFX_FD_CMD_FQID_MASK));

		/* DPAA_ENQUEUE_RETRIES is private to dpaa_eth.c. */
		err = -EBUSY;
		for (j = 0; j < 100000; j++) {
			err = qman_enqueue(egress_fq, &fd);
			if (err != -EBUSY)
				break;
		}
		if (err) {
			/* The ring slots are already consumed; complete the
			 * rest unsent so the chunks return to user space.
			 */
			priv->xsk_tx_backpressure++;
			xsk_tx_completed(pool, n - i);
			break;
		}
		atomic_inc(&priv->xsk_tx_inflight[q]);
		submitted++;
	}

	if (submitted)
		priv->xsk_tx_zc_submit++;
}

/*
 * af_xdp_pool_xsk_set_tx_need_wakeup - arm XSK tx-need-wakeup flag
 *
 * M3-3 step 5 (patch 0085, spec sec 6.1.4). Called from dpaa_eth_poll()
 * tail right after napi_tx_zc, same RCU region.
 *
 * For each bound xsk_pool[qband] that requested XDP_USE_NEED_WAKEUP at
 * bind time, assert tx need-wakeup. Userspace observes via
 * xsk_ring_prod__needs_wakeup() and issues sendto(MSG_DONTWAIT) which
 * lands in af_xdp_pool_xsk_wakeup() and schedules NAPI.
 *
 * Currently set unconditionally per bound qband. Future refinement
 * (M3-3 step 6+) may clear when inflight falls below
 * DPAA1_XSK_TX_LOW_WATER (512).
 */
static void af_xdp_pool_xsk_set_tx_need_wakeup(struct dpaa_priv *priv,
       struct dpaa_napi_portal *np)
{
struct xsk_buff_pool *pool;
u16 q;

if (!priv)
return;

for (q = 0; q < priv->xsk_max_qbands && q < DPAA1_XSK_MAX_QBANDS; q++) {
pool = rcu_dereference(priv->xsk_pool[q]);
if (!pool)
continue;
if (!xsk_uses_need_wakeup(pool))
continue;
xsk_set_tx_need_wakeup(pool);
}
}

/* True if @addr lies in a page of the attached UMEM. */
static bool afx_addr_in_umem(struct xsk_buff_pool *pool, u64 addr)
{
	u32 i;

	for (i = 0; i < pool->dma_pages_cnt; i++)
		if ((pool->dma_pages[i] & PAGE_MASK) == (addr & PAGE_MASK))
			return true;
	return false;
}

/*
 * af_xdp_pool_tx_err_zc - claim a ZC Tx FD that reached an error path
 *
 * Called from dpaa_tx_error() and the egress ERN handler, which would
 * otherwise treat the FD address as an skb buffer.  Such an FD is ours if
 * it carries our confirmation FQID; the UMEM address check covers an FD
 * whose command word was rewritten on the way.  The chunk is completed to
 * the XSK ring exactly as a confirmation would.  Runs in NAPI/portal
 * context with rcu_read_lock() held; both paths are error-only.
 */
static bool af_xdp_pool_tx_err_zc(struct dpaa_priv *priv,
				  const struct qm_fd *fd)
{
	struct af_xdp_port *port;
	struct xsk_buff_pool *pool;
	u16 q;

	if (!priv || !fd || fd->bpid != AFX_BPID_INV)
		return false;
	port = smp_load_acquire(&priv->xsk_port);
	if (!port)
		return false;
	q = port->qid;
	pool = rcu_dereference(priv->xsk_pool[q]);
	if (!pool)
		return false;

	if ((be32_to_cpu(fd->cmd) & AFX_FD_CMD_FQID_MASK) !=
	    qman_fq_fqid(&port->txc.fq) &&
	    !afx_addr_in_umem(pool, qm_fd_addr(fd)))
		return false;

	port->st.tx_err++;
	afx_tx_complete(priv, q);
	return true;
}

/* ---------- Ops tables ---------- */

static const struct dpaa_qmgmt_ops af_xdp_pool_qmgmt_ops = {
.alloc_rx_fqs           = af_xdp_pool_alloc_rx_fqs,
.rx_hook                = af_xdp_pool_rx_hook,
.rx_release             = af_xdp_pool_rx_release,
.xsk_pool_attach        = af_xdp_pool_xsk_pool_attach,
.xsk_pool_detach        = af_xdp_pool_xsk_pool_detach,
.xsk_wakeup             = af_xdp_pool_xsk_wakeup,
.xsk_set_rx_need_wakeup = af_xdp_pool_xsk_set_rx_need_wakeup,
.napi_refill            = af_xdp_pool_napi_refill,
.napi_tx_zc             = af_xdp_pool_napi_tx_zc,
.xsk_set_tx_need_wakeup = af_xdp_pool_xsk_set_tx_need_wakeup,
.tx_err_zc              = af_xdp_pool_tx_err_zc,
};

/* ---------- debugfs: /sys/kernel/debug/af_xdp_pool/qmap ----------
 *
 * Patch 0082 (M3-3 step 2b, spec sec 5.2).  Renders priv->qmap[] for
 * every DPAA1 netdev so step 2a's per-CPU NAPI distribution is
 * user-visible without requiring kgdb or a crash dump.  Output format
 * (one block per netdev, sorted by ifindex):
 *
 *     iface=eth3 ifindex=4 xsk_max_qbands=4
 *       qband 0  fqid_base=0x00000000  swp_id=0  cpu=0  napi=ffff800082b35a80
 *       qband 1  fqid_base=0x00000000  swp_id=0  cpu=1  napi=ffff800082b35d80
 *       qband 2  fqid_base=0x00000000  swp_id=0  cpu=2  napi=ffff800082b36080
 *       qband 3  fqid_base=0x00000000  swp_id=0  cpu=3  napi=ffff800082b36380
 *
 * The napi pointer stride between qbands matches the per-CPU offset
 * stride of priv->percpu_priv on a 4-CPU LS1046A: that is the
 * verification gate for step 2a.  fqid_base / swp_id remain 0 until
 * the dedicated QMan dispatch channel work (step 3) populates them.
 *
 * RCU read-side: walk dev_base under rtnl_lock + RCU.  We only deref
 * priv->qmap[] which is stable for the netdev's lifetime once the
 * flavor module attaches.  The atomic_read of xsk_tx_inflight[] is
 * read-only and self-consistent.
 *
 * Identification of DPAA1 netdevs without a public is_dpaa_priv()
 * helper: a DPAA1 netdev's ->dev.parent points at the fsl_dpaa_mac
 * platform device (per AGENTS.md "DPAA1 driver split": two drivers
 * cooperate — fsl_dpaa_mac owns the MAC, fsl_dpa registers the netdev
 * and owns netdev_priv as struct dpaa_priv).  So the predicate is
 * "parent device driver name == fsl_dpaa_mac".  The original
 * "fsl_dpa" guess was wrong — that driver name belongs to the child
 * dpaa-ethernet.N platform device, which is NOT what ndev->dev.parent
 * points to on LS1046A 6.18.x.  Verified on DUT 2026-05-27.
 */
static struct dentry *af_xdp_pool_debugfs_root;

static bool af_xdp_pool_is_dpaa1_netdev(const struct net_device *ndev)
{
const struct device *dev;
const struct device_driver *drv;

if (!ndev)
return false;
dev = ndev->dev.parent;
if (!dev)
return false;
drv = dev->driver;
if (!drv || !drv->name)
return false;
return strcmp(drv->name, "fsl_dpaa_mac") == 0;
}

static int af_xdp_pool_qmap_show(struct seq_file *m, void *v)
{
struct net_device *ndev;
struct net *net = &init_net;
struct dpaa_priv *priv;
unsigned int q;
int qband_count;

rtnl_lock();
for_each_netdev(net, ndev) {
if (!af_xdp_pool_is_dpaa1_netdev(ndev))
continue;
priv = netdev_priv(ndev);
if (!priv)
continue;

qband_count = (int)priv->xsk_max_qbands;
if (qband_count <= 0 || qband_count > DPAA1_XSK_MAX_QBANDS)
qband_count = DPAA1_XSK_MAX_QBANDS;

seq_printf(m, "iface=%s ifindex=%d xsk_max_qbands=%u\n",
   ndev->name, ndev->ifindex, priv->xsk_max_qbands);
for (q = 0; q < (unsigned int)qband_count; q++) {
seq_printf(m,
   "  qband %u  fqid_base=0x%08x  swp_id=%u  cpu=%d  napi=%p  xsk_tx_inflight=%d\n",
   q,
   priv->qmap[q].fqid_base,
   (unsigned int)priv->qmap[q].swp_id,
   priv->qmap[q].cpu,
   priv->qmap[q].napi,
   atomic_read(&priv->xsk_tx_inflight[q]));
}
seq_putc(m, '\n');
}
rtnl_unlock();
return 0;
}

DEFINE_SHOW_ATTRIBUTE(af_xdp_pool_qmap);

/* /sys/kernel/debug/af_xdp_pool/zc_stats: per-port RX zero-copy counters */
static int af_xdp_pool_zc_stats_show(struct seq_file *m, void *v)
{
	struct net_device *ndev;
	struct dpaa_priv *priv;
	struct af_xdp_port *port;

	rtnl_lock();
	for_each_netdev(&init_net, ndev) {
		if (!af_xdp_pool_is_dpaa1_netdev(ndev))
			continue;
		priv = netdev_priv(ndev);
		port = smp_load_acquire(&priv->xsk_port);
		if (!port)
			continue;
		spin_lock_bh(&port->lock);
		seq_printf(m,
			   "iface=%s bpid=%u live=%u max_live=%u data_max=%u quiesced=%d\n"
			   "  rx_miss=%llu rx_bad_fd=%llu rx_quiesced=%llu rx_pass=%llu rx_pass_fail=%llu\n"
			   "  rx_redirect_fail=%llu rx_drop=%llu err_release=%llu\n"
			   "  fill_batches=%llu fill_bad_cookie=%llu fill_dup=%llu fill_release_fail=%llu\n",
			   ndev->name, port->bpid, port->map.live,
			   port->map.max_live, port->data_max, port->quiesced,
			   port->st.rx_miss, port->st.rx_bad_fd,
			   port->st.rx_quiesced, port->st.rx_pass,
			   port->st.rx_pass_fail, port->st.rx_redirect_fail,
			   port->st.rx_drop, port->st.err_release,
			   port->st.fill_batches, port->st.fill_bad_cookie,
			   port->st.fill_dup, port->st.fill_release_fail);
		spin_unlock_bh(&port->lock);
	}
	rtnl_unlock();
	return 0;
}

DEFINE_SHOW_ATTRIBUTE(af_xdp_pool_zc_stats);

static void af_xdp_pool_debugfs_init(void)
{
af_xdp_pool_debugfs_root = debugfs_create_dir("af_xdp_pool", NULL);
if (IS_ERR(af_xdp_pool_debugfs_root)) {
af_xdp_pool_debugfs_root = NULL;
pr_warn("af_xdp_pool: debugfs root create failed; qmap node unavailable\n");
return;
}
debugfs_create_file("qmap", 0444, af_xdp_pool_debugfs_root,
    NULL, &af_xdp_pool_qmap_fops);
debugfs_create_file("zc_stats", 0444, af_xdp_pool_debugfs_root,
    NULL, &af_xdp_pool_zc_stats_fops);
}

static void af_xdp_pool_debugfs_exit(void)
{
debugfs_remove_recursive(af_xdp_pool_debugfs_root);
af_xdp_pool_debugfs_root = NULL;
}

/* ---------- Module init / exit ---------- */

static int __init af_xdp_pool_init(void)
{
int err;

err = dpaa_register_flavor_ops(NULL, &af_xdp_pool_qmgmt_ops);
if (err) {
pr_err("af_xdp_pool: dpaa_register_flavor_ops() failed: %d\n", err);
return err;
}

af_xdp_pool_debugfs_init();

pr_info("af_xdp_pool: registered\n");
return 0;
}

static void __exit af_xdp_pool_exit(void)
{
af_xdp_pool_debugfs_exit();
dpaa_unregister_flavor_ops();
pr_info("af_xdp_pool: unregistered\n");
}

module_init(af_xdp_pool_init);
module_exit(af_xdp_pool_exit);

MODULE_AUTHOR("VyOS LS1046A maintainers");
MODULE_DESCRIPTION("DPAA1 AF_XDP zero-copy pool flavor module");
MODULE_LICENSE("GPL v2");