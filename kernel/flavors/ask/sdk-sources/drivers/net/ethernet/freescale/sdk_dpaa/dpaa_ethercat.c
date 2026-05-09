// SPDX-License-Identifier: (GPL-2.0 OR MIT)
/*
 * Copyright 2021-2023 NXP
 */

#include <linux/init.h>
#include <linux/module.h>
#include <linux/of_mdio.h>
#include <linux/of_net.h>
#include <linux/kthread.h>
#include <linux/io.h>
#include <linux/if_arp.h>	/* arp_hdr_len() */
#include <linux/if_vlan.h>	/* VLAN_HLEN */
#include <linux/icmp.h>		/* struct icmphdr */
#include <linux/ip.h>		/* struct iphdr */
#include <linux/ipv6.h>		/* struct ipv6hdr */
#include <linux/udp.h>		/* struct udphdr */
#include <linux/tcp.h>		/* struct tcphdr */
#include <linux/net.h>		/* net_ratelimit() */
#include <linux/if_ether.h>	/* ETH_P_IP and ETH_P_IPV6 */
#include <linux/highmem.h>
#include <linux/percpu.h>
#include <linux/dma-mapping.h>
#include <linux/fsl_bman.h>
#ifdef CONFIG_SOC_BUS
#include <linux/sys_soc.h>      /* soc_device_match */
#endif

#include "fsl_fman.h"
#include "fm_ext.h"
#include "fm_port_ext.h"

#include "mac.h"
#include "dpaa_eth.h"
#include "dpaa_eth_common.h"
#ifdef CONFIG_FSL_DPAA_DBG_LOOP
#include "dpaa_debugfs.h"
#endif /* CONFIG_FSL_DPAA_DBG_LOOP */

#define DPA_NAPI_WEIGHT		64

/* Valid checksum indication */
#define DPA_CSUM_VALID		0xFFFF

#define DPA_DESCRIPTION "FSL/NXP DPAA Ethercat driver"

MODULE_LICENSE("Dual BSD/GPL");

MODULE_DESCRIPTION(DPA_DESCRIPTION);

static u8 debug = -1;

/* This has to work in tandem with the DPA_CS_THRESHOLD_xxx values. */
static u16 tx_timeout = 1000;

static const char rtx[][3] = {
	[RX] = "RX",
	[TX] = "TX"
};

/* BM */

#define DPAA_ETH_MAX_PAD (L1_CACHE_BYTES * 8)

static u32 dpa_priv_bpid;
static u32 dpa_priv_cpuid;

#ifdef CONFIG_FSL_DPAA_DBG_LOOP
struct net_device *dpa_loop_netdevs[20];
#endif

#ifdef CONFIG_PM

static int dpaa_suspend(struct device *dev)
{
	struct net_device	*net_dev;
	struct dpa_priv_s	*priv;
	struct mac_device	*mac_dev;
	int			err = 0;

	net_dev = dev_get_drvdata(dev);

	if (net_dev->flags & IFF_UP) {
		priv = netdev_priv(net_dev);
		mac_dev = priv->mac_dev;

		if (priv->wol & DPAA_WOL_MAGIC) {
			err = priv->mac_dev->set_wol(mac_dev->port_dev[RX],
				priv->mac_dev->get_mac_handle(mac_dev), true);
			if (err) {
				netdev_err(net_dev, "set_wol() = %d\n", err);
				goto set_wol_failed;
			}
		}

		err = fm_port_suspend(mac_dev->port_dev[RX]);
		if (err) {
			netdev_err(net_dev, "fm_port_suspend(RX) = %d\n", err);
			goto rx_port_suspend_failed;
		}

		err = fm_port_suspend(mac_dev->port_dev[TX]);
		if (err) {
			netdev_err(net_dev, "fm_port_suspend(TX) = %d\n", err);
			goto tx_port_suspend_failed;
		}
	}

	return 0;

tx_port_suspend_failed:
	fm_port_resume(mac_dev->port_dev[RX]);
rx_port_suspend_failed:
	if (priv->wol & DPAA_WOL_MAGIC) {
		priv->mac_dev->set_wol(mac_dev->port_dev[RX],
			priv->mac_dev->get_mac_handle(mac_dev), false);
	}
set_wol_failed:
	return err;
}

static int dpaa_resume(struct device *dev)
{
	struct net_device	*net_dev;
	struct dpa_priv_s	*priv;
	struct mac_device	*mac_dev;
	int			err = 0;

	net_dev = dev_get_drvdata(dev);

	if (net_dev->flags & IFF_UP) {
		priv = netdev_priv(net_dev);
		mac_dev = priv->mac_dev;

		err = fm_mac_resume(mac_dev->get_mac_handle(mac_dev));
		if (err) {
			netdev_err(net_dev, "fm_mac_resume = %d\n", err);
			goto resume_failed;
		}

		err = fm_port_resume(mac_dev->port_dev[TX]);
		if (err) {
			netdev_err(net_dev, "fm_port_resume(TX) = %d\n", err);
			goto resume_failed;
		}

		err = fm_port_resume(mac_dev->port_dev[RX]);
		if (err) {
			netdev_err(net_dev, "fm_port_resume(RX) = %d\n", err);
			goto resume_failed;
		}

		if (priv->wol & DPAA_WOL_MAGIC) {
			err = priv->mac_dev->set_wol(mac_dev->port_dev[RX],
				priv->mac_dev->get_mac_handle(mac_dev), false);
			if (err) {
				netdev_err(net_dev, "set_wol() = %d\n", err);
				goto resume_failed;
			}
		}
	}

	return 0;

resume_failed:
	return err;
}

static const struct dev_pm_ops dpaa_pm_ops = {
	.suspend = dpaa_suspend,
	.resume = dpaa_resume,
};

#define DPAA_PM_OPS (&dpaa_pm_ops)

#else /* CONFIG_PM */

#define DPAA_PM_OPS NULL

#endif /* CONFIG_PM */

/* Checks whether the checksum field in Parse Results array is valid
 * (equals 0xFFFF) and increments the .cse counter otherwise
 */
static inline void
dpa_csum_validation(const struct dpa_priv_s	*priv,
		    struct dpa_percpu_priv_s *percpu_priv,
		    const struct qm_fd *fd)
{
	dma_addr_t addr = qm_fd_addr(fd);
	struct dpa_bp *dpa_bp = priv->dpa_bp;
	void *frm = phys_to_virt(addr);
	fm_prs_result_t *parse_result;

	if (unlikely(!frm))
		return;

	dma_sync_single_for_cpu(dpa_bp->dev, addr, DPA_RX_PRIV_DATA_SIZE +
				DPA_PARSE_RESULTS_SIZE, DMA_BIDIRECTIONAL);

	parse_result = (fm_prs_result_t *)(frm + DPA_RX_PRIV_DATA_SIZE);

	if (parse_result->cksum != DPA_CSUM_VALID)
		percpu_priv->rx_errors.cse++;
}

static void _dpa_rx_error(struct net_device *net_dev,
			  const struct dpa_priv_s *priv,
			  struct dpa_percpu_priv_s *percpu_priv,
			  const struct qm_fd *fd,
			  u32 fqid)
{
	/* limit common, possibly innocuous Rx FIFO Overflow errors'
	 * interference with zero-loss convergence benchmark results.
	 */
	if (likely(fd->status & FM_FD_STAT_ERR_PHYSICAL))
		pr_warn_once("fsl-dpa: non-zero error counters in fman statistics (sysfs)\n");
	else
		if (netif_msg_hw(priv) && net_ratelimit())
			netdev_dbg(net_dev, "Err FD status = 0x%08x\n",
				   fd->status & FM_FD_STAT_RX_ERRORS);
#ifdef CONFIG_FSL_DPAA_HOOKS
	if (dpaa_eth_hooks.rx_error &&
	    dpaa_eth_hooks.rx_error(net_dev, fd, fqid) == DPAA_ETH_STOLEN)
		/* it's up to the hook to perform resource cleanup */
		return;
#endif
	percpu_priv->stats.rx_errors++;

	if (fd->status & FM_PORT_FRM_ERR_DMA)
		percpu_priv->rx_errors.dme++;
	if (fd->status & FM_PORT_FRM_ERR_PHYSICAL)
		percpu_priv->rx_errors.fpe++;
	if (fd->status & FM_PORT_FRM_ERR_SIZE)
		percpu_priv->rx_errors.fse++;
	if (fd->status & FM_PORT_FRM_ERR_PRS_HDR_ERR)
		percpu_priv->rx_errors.phe++;
	if (fd->status & FM_FD_STAT_L4CV)
		dpa_csum_validation(priv, percpu_priv, fd);

	dpa_fd_release(net_dev, fd);
}

static void _dpa_tx_error(struct net_device		*net_dev,
			  const struct dpa_priv_s	*priv,
			  struct dpa_percpu_priv_s	*percpu_priv,
			  const struct qm_fd		*fd,
			  u32				 fqid)
{
	struct sk_buff *skb;

	if (netif_msg_hw(priv) && net_ratelimit())
		netdev_warn(net_dev, "FD status = 0x%08x\n",
			    fd->status & FM_FD_STAT_TX_ERRORS);
#ifdef CONFIG_FSL_DPAA_HOOKS
	if (dpaa_eth_hooks.tx_error &&
	    dpaa_eth_hooks.tx_error(net_dev, fd, fqid) == DPAA_ETH_STOLEN)
		/* now the hook must ensure proper cleanup */
		return;
#endif
	percpu_priv->stats.tx_errors++;

	/* If we intended the buffers from this frame to go into the bpools
	 * when the FMan transmit was done, we need to put it in manually.
	 */
	if (fd->bpid != 0xff) {
		dpa_fd_release(net_dev, fd);
		return;
	}

	skb = _dpa_cleanup_tx_fd(priv, fd);
	if (!priv->ecdev)
		dev_kfree_skb(skb);
}

static void __hot _dpa_tx_conf(struct net_device	*net_dev,
			       const struct dpa_priv_s	*priv,
			       struct dpa_percpu_priv_s	*percpu_priv,
			       const struct qm_fd	*fd,
			       u32			 fqid)
{
	struct sk_buff	*skb;

	/* do we need the timestamp for the error frames? */

	if (unlikely(fd->status & FM_FD_STAT_TX_ERRORS) != 0) {
		if (netif_msg_hw(priv) && net_ratelimit())
			netdev_warn(net_dev, "FD status = 0x%08x\n",
				    fd->status & FM_FD_STAT_TX_ERRORS);

		percpu_priv->stats.tx_errors++;
	}

	/* hopefully we need not get the timestamp before the hook */
#ifdef CONFIG_FSL_DPAA_HOOKS
	if (dpaa_eth_hooks.tx_confirm &&
	    dpaa_eth_hooks.tx_confirm(net_dev, fd, fqid) == DPAA_ETH_STOLEN)
		/* it's the hook that must now perform cleanup */
		return;
#endif
	/* This might not perfectly reflect the reality, if the core dequeuing
	 * the Tx confirmation is different from the one that did the enqueue,
	 * but at least it'll show up in the total count.
	 */
	percpu_priv->tx_confirm++;

	skb = _dpa_cleanup_tx_fd(priv, fd);
	if (!priv->ecdev)
		dev_kfree_skb(skb);
}

static enum qman_cb_dqrr_result
priv_rx_error_dqrr(struct qman_portal		*portal,
		   struct qman_fq		*fq,
		   const struct qm_dqrr_entry	*dq)
{
	struct net_device		*net_dev;
	struct dpa_priv_s		*priv;
	struct dpa_percpu_priv_s	*percpu_priv;
	int				*count_ptr;

	net_dev = ((struct dpa_fq *)fq)->net_dev;
	priv = netdev_priv(net_dev);

	percpu_priv = raw_cpu_ptr(priv->percpu_priv);
	count_ptr = raw_cpu_ptr(priv->percpu_count);

	if (unlikely(dpaa_eth_refill_bpools(priv->dpa_bp, count_ptr)))
		/* Unable to refill the buffer pool due to insufficient
		 * system memory. Just release the frame back into the pool,
		 * otherwise we'll soon end up with an empty buffer pool.
		 */
		dpa_fd_release(net_dev, &dq->fd);
	else
		_dpa_rx_error(net_dev, priv, percpu_priv, &dq->fd, fq->fqid);

	return qman_cb_dqrr_consume;
}

static enum qman_cb_dqrr_result __hot
priv_rx_default_dqrr(struct qman_portal		*portal,
		     struct qman_fq		*fq,
		     const struct qm_dqrr_entry	*dq)
{
	struct net_device		*net_dev;
	struct dpa_priv_s		*priv;
	struct dpa_percpu_priv_s	*percpu_priv;
	int                             *count_ptr;
	struct dpa_bp			*dpa_bp;

	net_dev = ((struct dpa_fq *)fq)->net_dev;
	priv = netdev_priv(net_dev);
	dpa_bp = priv->dpa_bp;

	/* IRQ handler, non-migratable; safe to use raw_cpu_ptr here */
	percpu_priv = raw_cpu_ptr(priv->percpu_priv);
	count_ptr = raw_cpu_ptr(priv->percpu_count);

	/* Vale of plenty: make sure we didn't run out of buffers */

	if (unlikely(dpaa_eth_refill_bpools(dpa_bp, count_ptr)))
		/* Unable to refill the buffer pool due to insufficient
		 * system memory. Just release the frame back into the pool,
		 * otherwise we'll soon end up with an empty buffer pool.
		 */
		dpa_fd_release(net_dev, &dq->fd);
	else
		_dpa_rx(net_dev, portal, priv, percpu_priv, &dq->fd, fq->fqid,
			count_ptr);

	return qman_cb_dqrr_consume;
}

static enum qman_cb_dqrr_result
priv_tx_conf_error_dqrr(struct qman_portal		*portal,
			struct qman_fq			*fq,
			const struct qm_dqrr_entry	*dq)
{
	struct net_device		*net_dev;
	struct dpa_priv_s		*priv;
	struct dpa_percpu_priv_s	*percpu_priv;

	net_dev = ((struct dpa_fq *)fq)->net_dev;
	priv = netdev_priv(net_dev);

	percpu_priv = raw_cpu_ptr(priv->percpu_priv);

	_dpa_tx_error(net_dev, priv, percpu_priv, &dq->fd, fq->fqid);

	return qman_cb_dqrr_consume;
}

static enum qman_cb_dqrr_result __hot
priv_tx_conf_default_dqrr(struct qman_portal		*portal,
			  struct qman_fq			*fq,
			  const struct qm_dqrr_entry	*dq)
{
	struct net_device		*net_dev;
	struct dpa_priv_s		*priv;
	struct dpa_percpu_priv_s	*percpu_priv;

	net_dev = ((struct dpa_fq *)fq)->net_dev;
	priv = netdev_priv(net_dev);

	/* Non-migratable context, safe to use raw_cpu_ptr */
	percpu_priv = raw_cpu_ptr(priv->percpu_priv);

	_dpa_tx_conf(net_dev, priv, percpu_priv, &dq->fd, fq->fqid);

	return qman_cb_dqrr_consume;
}

static void priv_ern(struct qman_portal	*portal,
		     struct qman_fq		*fq,
		     const struct qm_mr_entry	*msg)
{
	struct net_device	*net_dev;
	const struct dpa_priv_s	*priv;
	struct sk_buff *skb;
	struct dpa_percpu_priv_s	*percpu_priv;
	struct qm_fd fd = msg->ern.fd;

	net_dev = ((struct dpa_fq *)fq)->net_dev;
	priv = netdev_priv(net_dev);
	/* Non-migratable context, safe to use raw_cpu_ptr */
	percpu_priv = raw_cpu_ptr(priv->percpu_priv);

	percpu_priv->stats.tx_dropped++;
	percpu_priv->stats.tx_fifo_errors++;
	count_ern(percpu_priv, msg);

	/* If we intended this buffer to go into the pool
	 * when the FM was done, we need to put it in
	 * manually.
	 */
	if (msg->ern.fd.bpid != 0xff) {
		dpa_fd_release(net_dev, &fd);
		return;
	}

	skb = _dpa_cleanup_tx_fd(priv, &fd);
	dev_kfree_skb_any(skb);
}

static const struct dpa_fq_cbs_t private_fq_cbs = {
	.rx_defq = { .cb = { .dqrr = priv_rx_default_dqrr } },
	.tx_defq = { .cb = { .dqrr = priv_tx_conf_default_dqrr } },
	.rx_errq = { .cb = { .dqrr = priv_rx_error_dqrr } },
	.tx_errq = { .cb = { .dqrr = priv_tx_conf_error_dqrr } },
	.egress_ern = { .cb = { .ern = priv_ern } }
};

static int __cold dpa_eth_priv_start(struct net_device *net_dev)
{
	int err;
	struct dpa_priv_s *priv;

	priv = netdev_priv(net_dev);

	err = dpa_start(net_dev);

	return err;
}

static int __cold dpa_eth_priv_stop(struct net_device *net_dev)
{
	int _errno;
	struct dpa_priv_s *priv;

	_errno = dpa_stop(net_dev);
	/* Allow NAPI to consume any frame still in the Rx/TxConfirm
	 * ingress queues. This is to avoid a race between the current
	 * context and ksoftirqd which could leave NAPI disabled while
	 * in fact there's still Rx traffic to be processed.
	 */
	usleep_range(5000, 10000);

	priv = netdev_priv(net_dev);

	return _errno;
}

static const struct net_device_ops dpa_private_ops = {
	.ndo_open = dpa_eth_priv_start,
	.ndo_start_xmit = dpa_tx,
	.ndo_stop = dpa_eth_priv_stop,
	.ndo_tx_timeout = dpa_timeout,
	.ndo_get_stats64 = dpa_get_stats64,
	.ndo_set_mac_address = dpa_set_mac_address,
	.ndo_validate_addr = eth_validate_addr,
#ifdef CONFIG_FMAN_PFC
	.ndo_select_queue = dpa_select_queue,
#endif
	.ndo_set_rx_mode = dpa_set_rx_mode,
	.ndo_init = dpa_ndo_init,
	.ndo_set_features = dpa_set_features,
	.ndo_fix_features = dpa_fix_features,
	.ndo_do_ioctl = dpa_ioctl,
};

typedef int (*ec_dpaa_receive_cb)(void *pecdev, const void *data, size_t size);
typedef int (*ec_dpaa_link_cb)(void *pecdev, uint8_t link);
typedef int (*ec_dpaa_close_cb)(void *pecdev);

static ec_dpaa_receive_cb ec_dpaa_recv_func;
static ec_dpaa_close_cb ec_dpaa_close_func;
static ec_dpaa_link_cb ec_dpaa_link_func;

int ec_dpaa_receive_data(void *pecdev, const void *data, size_t size)
{
	int ret = 0;

	if (ec_dpaa_recv_func)
		ret = ec_dpaa_recv_func(pecdev, data, size);

	return ret;
}

int ec_dpaa_set_func_cb(ec_dpaa_receive_cb recv, ec_dpaa_link_cb link, ec_dpaa_close_cb close)
{
	ec_dpaa_recv_func = recv;
	ec_dpaa_link_func = link;
	ec_dpaa_close_func = close;

	return 0;
}
EXPORT_SYMBOL(ec_dpaa_set_func_cb);

struct module *ec_dpaa_get_module(void)
{
	struct module *m = THIS_MODULE;
	return m;
}
EXPORT_SYMBOL(ec_dpaa_get_module);

#define MAX_EC_DPAA_NETDEV_CNT (16)
static int ec_dpaa_netdev_cnt;
static struct net_device *ec_dpaa_netdev[MAX_EC_DPAA_NETDEV_CNT];

struct net_device *ec_dpaa_get_netdev(int idx)
{
	if (idx >= MAX_EC_DPAA_NETDEV_CNT)
		return NULL;

	if (idx >= ec_dpaa_netdev_cnt)
		return NULL;

	return ec_dpaa_netdev[idx];
}
EXPORT_SYMBOL(ec_dpaa_get_netdev);

int ec_dpaa_set_ecdev(int idx, void *ecdev)
{
	struct net_device *net_dev = NULL;
	struct dpa_priv_s *priv = NULL;

	net_dev = ec_dpaa_get_netdev(idx);
	if (net_dev) {
		priv = netdev_priv(net_dev);
		priv->ecdev = ecdev;
	}

	return 0;
}
EXPORT_SYMBOL(ec_dpaa_set_ecdev);

void *ec_dpaa_get_ecdev(int idx)
{
	struct net_device *net_dev = NULL;
	struct dpa_priv_s *priv = NULL;
	void *ecdev = NULL;

	net_dev = ec_dpaa_get_netdev(idx);
	if (net_dev) {
		priv = netdev_priv(net_dev);
		ecdev = priv->ecdev;
	}

	return ecdev;
}
EXPORT_SYMBOL(ec_dpaa_get_ecdev);

void ec_dpaa_poll(struct net_device *net_dev)
{
	struct dpa_priv_s *priv = netdev_priv(net_dev);
	u8 link = net_dev->phydev->state;

	qman_p_poll_dqrr(priv->p, DPA_NAPI_WEIGHT);

	if (ec_dpaa_link_func)
		ec_dpaa_link_func(priv->ecdev, link);
}
EXPORT_SYMBOL(ec_dpaa_poll);

int dpa_unregister_ethercat(struct net_device *net_dev)
{
	struct dpa_priv_s *priv = netdev_priv(net_dev);

	if (priv->ecdev) {
		if (ec_dpaa_close_func)
			ec_dpaa_close_func(priv->ecdev);
		return 0;
	}

	return -1;
}
EXPORT_SYMBOL(dpa_unregister_ethercat);

static int dpa_ethercat_netdev_init(struct net_device *net_dev,
				    const u8 *mac_addr,
				    uint16_t tx_timeout)
{
	struct dpa_priv_s *priv = netdev_priv(net_dev);

	net_dev->priv_flags |= IFF_LIVE_ADDR_CHANGE;

	net_dev->features |= net_dev->hw_features;
	net_dev->vlan_features = net_dev->features;

	memcpy(net_dev->perm_addr, mac_addr, net_dev->addr_len);
	memcpy(net_dev->dev_addr, mac_addr, net_dev->addr_len);

	net_dev->ethtool_ops = &dpa_ethtool_ops;

	net_dev->needed_headroom = priv->tx_headroom;
	net_dev->watchdog_timeo = msecs_to_jiffies(tx_timeout);

	if (ec_dpaa_netdev_cnt < MAX_EC_DPAA_NETDEV_CNT)
		ec_dpaa_netdev[ec_dpaa_netdev_cnt++] = net_dev;

	return 0;
}

static int dpa_private_netdev_init(struct net_device *net_dev)
{
	int i;
	struct dpa_priv_s *priv = netdev_priv(net_dev);
	struct dpa_percpu_priv_s *percpu_priv;
	const u8 *mac_addr;

	/* Although we access another CPU's private data here
	 * we do it at initialization so it is safe
	 */
	for_each_possible_cpu(i) {
		percpu_priv = per_cpu_ptr(priv->percpu_priv, i);
		percpu_priv->net_dev = net_dev;
	}

	net_dev->netdev_ops = &dpa_private_ops;
	mac_addr = priv->mac_dev->addr;

	net_dev->mem_start = priv->mac_dev->res->start;
	net_dev->mem_end = priv->mac_dev->res->end;

	/* Configure the maximum MTU according to the FMan's MAXFRM */
	net_dev->min_mtu = ETH_MIN_MTU;
	net_dev->max_mtu = dpa_get_max_mtu();

	net_dev->hw_features |= (NETIF_F_IP_CSUM | NETIF_F_IPV6_CSUM |
		NETIF_F_LLTX);

	/* Advertise S/G and HIGHDMA support for private interfaces */
	net_dev->hw_features |= NETIF_F_SG | NETIF_F_HIGHDMA;
	/* Recent kernels enable GSO automatically, if
	 * we declare NETIF_F_SG. For conformity, we'll
	 * still declare GSO explicitly.
	 */
	net_dev->features |= NETIF_F_GSO;

	/* Advertise GRO support */
	net_dev->features |= NETIF_F_GRO;

	return dpa_ethercat_netdev_init(net_dev, mac_addr, tx_timeout);
}

static struct dpa_bp * __cold
dpa_priv_bp_probe(struct device *dev)
{
	struct dpa_bp *dpa_bp;

	dpa_bp = devm_kzalloc(dev, sizeof(*dpa_bp), GFP_KERNEL);
	if (unlikely(!dpa_bp)) {
		dev_err(dev, "devm_kzalloc() failed\n");
		return ERR_PTR(-ENOMEM);
	}

	dpa_bp->target_count = CONFIG_FSL_DPAA_ETH_MAX_BUF_COUNT;

	dpa_bp->free_buf_cb = _dpa_bp_free_pf;

	return dpa_bp;
}

/* Place all ingress FQs (Rx Default, Rx Error, PCD FQs) in a dedicated CGR.
 * We won't be sending congestion notifications to FMan; for now, we just use
 * this CGR to generate enqueue rejections to FMan in order to drop the frames
 * before they reach our ingress queues and eat up memory.
 */
static int dpaa_eth_priv_ingress_cgr_init(struct dpa_priv_s *priv)
{
	struct qm_mcc_initcgr initcgr;
	u32 cs_th;
	int err;

	err = qman_alloc_cgrid(&priv->ingress_cgr.cgrid);
	if (err < 0) {
		pr_err("Error %d allocating CGR ID\n", err);
		goto out_error;
	}

	/* Enable CS TD, but disable Congestion State Change Notifications. */
	memset(&initcgr, 0, sizeof(initcgr));
	initcgr.we_mask = QM_CGR_WE_CS_THRES;
	initcgr.cgr.cscn_en = QM_CGR_EN;
	cs_th = CONFIG_FSL_DPAA_INGRESS_CS_THRESHOLD;
	qm_cgr_cs_thres_set64(&initcgr.cgr.cs_thres, cs_th, 1);

	initcgr.we_mask |= QM_CGR_WE_CSTD_EN;
	initcgr.cgr.cstd_en = QM_CGR_EN;

	/* This is actually a hack, because this CGR will be associated with
	 * our affine SWP. However, we'll place our ingress FQs in it.
	 */
	err = qman_create_cgr(&priv->ingress_cgr, QMAN_CGR_FLAG_USE_INIT,
			      &initcgr);
	if (err < 0) {
		pr_err("Error %d creating ingress CGR with ID %d\n", err,
		       priv->ingress_cgr.cgrid);
		qman_release_cgrid(priv->ingress_cgr.cgrid);
		goto out_error;
	}
	pr_debug("Created ingress CGR %d for netdev with hwaddr %pM\n",
		 priv->ingress_cgr.cgrid, priv->mac_dev->addr);

	/* struct qman_cgr allows special cgrid values (i.e. outside the 0..255
	 * range), but we have no common initialization path between the
	 * different variants of the DPAA Eth driver, so we do it here rather
	 * than modifying every other variant than "private Eth".
	 */
	priv->use_ingress_cgr = true;

out_error:
	return err;
}

static void dpa_priv_bp_seed(struct net_device *net_dev)
{
	struct dpa_priv_s *priv = netdev_priv(net_dev);
	struct dpa_bp *dpa_bp = priv->dpa_bp;
	int i;

	/* Give each CPU an allotment of buffers */
	for_each_possible_cpu(i) {
		/* Although we access another CPU's counters here
		 * we do it at boot time so it is safe
		 */
		int *count_ptr = per_cpu_ptr(priv->percpu_count, i);

		dpaa_eth_refill_bpools(dpa_bp, count_ptr);
	}
}

static const struct of_device_id dpa_match[];

#ifdef CONFIG_FSL_DPAA_DBG_LOOP
static int dpa_new_loop_id(void)
{
	static int if_id;

	return if_id++;
}
#endif

extern int dpa_bp_create(struct net_device *net_dev, struct dpa_bp *dpa_bp,
			 size_t count);

static inline void dpa_setup_ingress(const struct dpa_priv_s *priv,
				     struct dpa_fq *fq,
				     const struct qman_fq *template)
{
	fq->fq_base = *template;
	fq->net_dev = priv->net_dev;

	fq->flags = QMAN_FQ_FLAG_NO_ENQUEUE;
	fq->channel = priv->channel;
}

static inline void dpa_setup_egress(const struct dpa_priv_s *priv,
				    struct dpa_fq *fq,
				    struct fm_port *port,
				    const struct qman_fq *template)
{
	fq->fq_base = *template;
	fq->net_dev = priv->net_dev;

	if (port) {
		fq->flags = QMAN_FQ_FLAG_TO_DCPORTAL;
		fq->channel = (u16)fm_get_tx_port_channel(port);
	} else {
		fq->flags = QMAN_FQ_FLAG_NO_MODIFY;
	}
}

static void dpa_fq_setup_ethercat(struct dpa_priv_s *priv, const struct dpa_fq_cbs_t *fq_cbs,
				  struct fm_port *tx_port)
{
	struct dpa_fq *fq;
	u16 portals[NR_CPUS];
	int cpu, portal_cnt = 0, num_portals = 0;
	u32 pcd_fqid, pcd_fqid_hi_prio;
	const cpumask_t *affine_cpus = qman_affine_cpus();
	int egress_cnt = 0, conf_cnt = 0;

	/* Prepare for PCD FQs init */
	for_each_cpu(cpu, affine_cpus)
		portals[num_portals++] = priv->ethercat_channel;

	if (num_portals == 0)
		dev_err(priv->net_dev->dev.parent,
			"No Qman software (affine) channels found");

	pcd_fqid = (priv->mac_dev) ?
		DPAA_ETH_PCD_FQ_BASE(priv->mac_dev->res->start) : 0;
	pcd_fqid_hi_prio = (priv->mac_dev) ?
		DPAA_ETH_PCD_FQ_HI_PRIO_BASE(priv->mac_dev->res->start) : 0;

	/* Initialize each FQ in the list */
	list_for_each_entry(fq, &priv->dpa_fq_list, list) {
		switch (fq->fq_type) {
		case FQ_TYPE_RX_DEFAULT:
			WARN_ON(!priv->mac_dev);
			dpa_setup_ingress(priv, fq, &fq_cbs->rx_defq);
			break;
		case FQ_TYPE_RX_ERROR:
			WARN_ON(!priv->mac_dev);
			dpa_setup_ingress(priv, fq, &fq_cbs->rx_errq);
			break;
		case FQ_TYPE_RX_PCD:
			/* For MACless we can't have dynamic Rx queues */
			WARN_ON(!priv->mac_dev && !fq->fqid);
			dpa_setup_ingress(priv, fq, &fq_cbs->rx_defq);
			if (!fq->fqid)
				fq->fqid = pcd_fqid++;
			fq->channel = portals[portal_cnt];
			portal_cnt = (portal_cnt + 1) % num_portals;
			break;
		case FQ_TYPE_RX_PCD_HI_PRIO:
			/* For MACless we can't have dynamic Hi Pri Rx queues */
			WARN_ON(!priv->mac_dev && !fq->fqid);
			dpa_setup_ingress(priv, fq, &fq_cbs->rx_defq);
			if (!fq->fqid)
				fq->fqid = pcd_fqid_hi_prio++;
			fq->channel = portals[portal_cnt];
			portal_cnt = (portal_cnt + 1) % num_portals;
			break;
		case FQ_TYPE_TX:
			dpa_setup_egress(priv, fq, tx_port,
					 &fq_cbs->egress_ern);
			/* If we have more Tx queues than the number of cores,
			 * just ignore the extra ones.
			 */
			if (egress_cnt < DPAA_ETH_TX_QUEUES)
				priv->egress_fqs[egress_cnt++] = &fq->fq_base;
			break;
		case FQ_TYPE_TX_CONFIRM:
			WARN_ON(!priv->mac_dev);
			dpa_setup_ingress(priv, fq, &fq_cbs->tx_defq);
			break;
		case FQ_TYPE_TX_CONF_MQ:
			WARN_ON(!priv->mac_dev);
			dpa_setup_ingress(priv, fq, &fq_cbs->tx_defq);
			priv->conf_fqs[conf_cnt++] = &fq->fq_base;
			break;
		case FQ_TYPE_TX_ERROR:
			WARN_ON(!priv->mac_dev);
			dpa_setup_ingress(priv, fq, &fq_cbs->tx_errq);
			break;
		default:
			dev_warn(priv->net_dev->dev.parent,
				 "Unknown FQ type detected!\n");
			break;
		}
	}

	/* The number of Tx queues may be smaller than the number of cores, if
	 * the Tx queue range is specified in the device tree instead of being
	 * dynamically allocated.
	 * Make sure all CPUs receive a corresponding Tx queue.
	 */
	while (egress_cnt < DPAA_ETH_TX_QUEUES) {
		list_for_each_entry(fq, &priv->dpa_fq_list, list) {
			if (fq->fq_type != FQ_TYPE_TX)
				continue;
			priv->egress_fqs[egress_cnt++] = &fq->fq_base;
			if (egress_cnt == DPAA_ETH_TX_QUEUES)
				break;
		}
	}
}

static int dpaa_ethercat_probe(struct platform_device *_of_dev)
{
	int err = 0, i;
	struct device *dev;
	struct device_node *dpa_node;
	struct dpa_bp *dpa_bp;
	size_t count = 1;
	struct net_device *net_dev = NULL;
	struct dpa_priv_s *priv = NULL;
	struct dpa_percpu_priv_s *percpu_priv;
	struct fm_port_fqs port_fqs;
	struct dpa_buffer_layout_s *buf_layout = NULL;
	struct mac_device *mac_dev;
	u32 last_cpu = 0;

	dev = &_of_dev->dev;

	dpa_node = dev->of_node;

	if (!of_device_is_available(dpa_node))
		return -ENODEV;

	/* Get the buffer pools assigned to this interface;
	 * run only once the default pool probing code
	 */

	err = of_property_read_u32(dpa_node, "fsl,bpid", &dpa_priv_bpid);
	if (err) {
		dev_err(dev, "Cannot find buffer pool ID in the device tree\n");
		return -EINVAL;
	}

	last_cpu = qman_get_affine_last_cpu();
	err = of_property_read_u32(dpa_node, "fsl,cpuid", &dpa_priv_cpuid);
	if (err) {
		dev_warn(dev, "Cannot find cpuid in the device tree, using the last core\n");
		dpa_priv_cpuid = last_cpu;
	} else if (dpa_priv_cpuid > last_cpu) {
		dev_warn(dev, "Invalid cpuid in the device tree, using the last core\n");
		dpa_priv_cpuid = last_cpu;
	}

	dpa_bp = (dpa_bpid2pool(dpa_priv_bpid)) ? :
		dpa_priv_bp_probe(dev);
	if (IS_ERR(dpa_bp))
		return PTR_ERR(dpa_bp);
	if (dpa_bp->bpid == 0)
		dpa_bp->bpid = dpa_priv_bpid;

	/* Allocate this early, so we can store relevant information in
	 * the private area (needed by 1588 code in dpa_mac_probe)
	 */
	net_dev = alloc_etherdev_mq(sizeof(*priv), DPAA_ETH_TX_QUEUES);
	if (!net_dev) {
		dev_err(dev, "alloc_etherdev_mq() failed\n");
		goto alloc_etherdev_mq_failed;
	}

	/* Do this here, so we can be verbose early */
	SET_NETDEV_DEV(net_dev, dev);
	dev_set_drvdata(dev, net_dev);

	priv = netdev_priv(net_dev);
	priv->net_dev = net_dev;
	strcpy(priv->if_type, "private");

	priv->msg_enable = netif_msg_init(debug, -1);

#ifdef CONFIG_FSL_DPAA_DBG_LOOP
	priv->loop_id = dpa_new_loop_id();
	priv->loop_to = -1; /* disabled by default */
	dpa_loop_netdevs[priv->loop_id] = net_dev;
#endif

	mac_dev = dpa_mac_probe(_of_dev);
	if (IS_ERR(mac_dev) || !mac_dev) {
		err = PTR_ERR(mac_dev);
		goto mac_probe_failed;
	}

	/* We have physical ports, so we need to establish
	 * the buffer layout.
	 */
	buf_layout = devm_kzalloc(dev, 2 * sizeof(*buf_layout),
				  GFP_KERNEL);
	if (!buf_layout) {
		dev_err(dev, "devm_kzalloc() failed\n");
		goto alloc_failed;
	}
	dpa_set_buffers_layout(mac_dev, buf_layout);

	/* For private ports, need to compute the size of the default
	 * buffer pool, based on FMan port buffer layout;also update
	 * the maximum buffer size for private ports if necessary
	 */
	dpa_bp->size = dpa_bp_size(&buf_layout[RX]);

#ifdef CONFIG_FSL_DPAA_ETH_JUMBO_FRAME
	/* We only want to use jumbo frame optimization if we actually have
	 * L2 MAX FRM set for jumbo frames as well.
	 */
	if (fm_get_max_frm() < 9600)
		dev_warn(dev,
			 "Invalid configuration: if jumbo frames support is on, FSL_FM_MAX_FRAME_SIZE should be set to 9600\n");
#endif

	INIT_LIST_HEAD(&priv->dpa_fq_list);

	memset(&port_fqs, 0, sizeof(port_fqs));

	err = dpa_fq_probe_mac(dev, &priv->dpa_fq_list, &port_fqs, true, RX);
	if (!err)
		err = dpa_fq_probe_mac(dev, &priv->dpa_fq_list,
				       &port_fqs, true, TX);

	if (err < 0)
		goto fq_probe_failed;

	/* bp init */

	err = dpa_bp_create(net_dev, dpa_bp, count);

	if (err < 0)
		goto bp_create_failed;

	priv->mac_dev = mac_dev;
	priv->ethercat_channel = (u16)qman_affine_channel_ethercat(dpa_priv_cpuid);
	priv->channel = priv->ethercat_channel;
	priv->p = qman_get_affine_portal_ethercat(dpa_priv_cpuid);

	dpa_fq_setup_ethercat(priv, &private_fq_cbs, priv->mac_dev->port_dev[TX]);

	/* Create a congestion group for this netdev, with
	 * dynamically-allocated CGR ID.
	 * Must be executed after probing the MAC, but before
	 * assigning the egress FQs to the CGRs.
	 */
	err = dpaa_eth_cgr_init(priv);
	if (err < 0) {
		dev_err(dev, "Error initializing CGR\n");
		goto tx_cgr_init_failed;
	}
	err = dpaa_eth_priv_ingress_cgr_init(priv);
	if (err < 0) {
		dev_err(dev, "Error initializing ingress CGR\n");
		goto rx_cgr_init_failed;
	}

	/* Add the FQs to the interface, and make them active */
	err = dpa_fqs_init(dev,  &priv->dpa_fq_list, false);
	if (err < 0)
		goto fq_alloc_failed;

	priv->buf_layout = buf_layout;
	priv->tx_headroom = dpa_get_headroom(&priv->buf_layout[TX]);
	priv->rx_headroom = dpa_get_headroom(&priv->buf_layout[RX]);

	/* All real interfaces need their ports initialized */
	dpaa_eth_init_ports(mac_dev, dpa_bp, count, &port_fqs,
			    buf_layout, dev);

#ifdef CONFIG_FMAN_PFC
	for (i = 0; i < CONFIG_FMAN_PFC_COS_COUNT; i++) {
		err = fm_port_set_pfc_priorities_mapping_to_qman_wq(mac_dev->port_dev[TX], i, i);
		if (unlikely(err != 0)) {
			dev_err(dev, "Error maping PFC %u to WQ %u\n", i, i);
			goto pfc_mapping_failed;
		}
	}
#endif

	priv->percpu_priv = devm_alloc_percpu(dev, *priv->percpu_priv);

	if (!priv->percpu_priv) {
		dev_err(dev, "devm_alloc_percpu() failed\n");
		err = -ENOMEM;
		goto alloc_percpu_failed;
	}

	for_each_possible_cpu(i) {
		percpu_priv = per_cpu_ptr(priv->percpu_priv, i);
		memset(percpu_priv, 0, sizeof(*percpu_priv));
	}

	priv->percpu_count = devm_alloc_percpu(dev, *priv->percpu_count);
	if (!priv->percpu_count) {
		dev_err(dev, "devm_alloc_percpu() failed\n");
		err = -ENOMEM;
		goto alloc_percpu_failed;
	}

	for_each_possible_cpu(i) {
		int *percpu_count = per_cpu_ptr(priv->percpu_count, i);
		*percpu_count = 0;
	}

	err = dpa_private_netdev_init(net_dev);

	dpa_priv_bp_seed(net_dev);

	if (err < 0)
		goto netdev_init_failed;

	pr_info("Ethercat port:%02hx:%02hx:%02hx:%02hx:%02hx:%02hx bpid:%d cpu:%d\n",
		mac_dev->addr[0], mac_dev->addr[1], mac_dev->addr[2],
		mac_dev->addr[3], mac_dev->addr[4], mac_dev->addr[5],
		dpa_priv_bpid, dpa_priv_cpuid);

#ifdef CONFIG_PM
	device_set_wakeup_capable(dev, true);
#endif

	pr_info("fsl_dpa: Probed interface %s\n", net_dev->name);

	return 0;

netdev_init_failed:

alloc_percpu_failed:
#ifdef CONFIG_FMAN_PFC
pfc_mapping_failed:
#endif
	dpa_fq_free(dev, &priv->dpa_fq_list);
fq_alloc_failed:
	qman_delete_cgr_safe(&priv->ingress_cgr);
	qman_release_cgrid(priv->ingress_cgr.cgrid);
rx_cgr_init_failed:
	qman_delete_cgr_safe(&priv->cgr_data.cgr);
	qman_release_cgrid(priv->cgr_data.cgr.cgrid);
tx_cgr_init_failed:

	dpa_bp_free(priv);
bp_create_failed:
fq_probe_failed:
alloc_failed:
mac_probe_failed:
	dev_set_drvdata(dev, NULL);
	free_netdev(net_dev);
alloc_etherdev_mq_failed:
	if (atomic_read(&dpa_bp->refs) == 0)
		devm_kfree(dev, dpa_bp);

	return err;
}

static const struct of_device_id dpa_match[] = {
	{
		.compatible	= "fsl,dpa-ethercat"
	},
	{}
};
MODULE_DEVICE_TABLE(of, dpa_match);

static struct platform_driver dpa_driver = {
	.driver = {
		.name		= KBUILD_MODNAME "-ethercat",
		.of_match_table	= dpa_match,
		.owner		= THIS_MODULE,
		.pm		= DPAA_PM_OPS,
	},
	.probe		= dpaa_ethercat_probe,
	.remove		= dpa_remove
};

static int __init __cold dpa_ethercat_load(void)
{
	int	 _errno;

	pr_info(DPA_DESCRIPTION "\n");

#ifdef CONFIG_FSL_DPAA_DBG_LOOP
	dpa_debugfs_module_init();
#endif /* CONFIG_FSL_DPAA_DBG_LOOP */

	/* initialise dpaa_eth mirror values */
	dpa_rx_extra_headroom = fm_get_rx_extra_headroom();
	dpa_max_frm = fm_get_max_frm();
	dpa_num_cpus = num_possible_cpus();

#ifdef CONFIG_FSL_DPAA_DBG_LOOP
	memset(dpa_loop_netdevs, 0, sizeof(dpa_loop_netdevs));
#endif

	_errno = platform_driver_register(&dpa_driver);
	if (unlikely(_errno < 0)) {
		pr_err(KBUILD_MODNAME
			": %s:%hu:%s(): platform_driver_register() = %d\n",
			KBUILD_BASENAME ".c", __LINE__, __func__, _errno);
	}

	pr_debug(KBUILD_MODNAME ": %s:%s() ->\n",
		 KBUILD_BASENAME ".c", __func__);

	return _errno;
}
module_init(dpa_ethercat_load);

static void __exit __cold dpa_ethercat_unload(void)
{
	pr_debug(KBUILD_MODNAME ": -> %s:%s()\n",
		 KBUILD_BASENAME ".c", __func__);

	platform_driver_unregister(&dpa_driver);

#ifdef CONFIG_FSL_DPAA_DBG_LOOP
	dpa_debugfs_module_exit();
#endif /* CONFIG_FSL_DPAA_DBG_LOOP */

	/* Only one channel is used and needs to be relased after all
	 * interfaces are removed
	 */
	dpa_release_channel();

	pr_debug(KBUILD_MODNAME ": %s:%s() ->\n",
		 KBUILD_BASENAME ".c", __func__);
}
module_exit(dpa_ethercat_unload);
