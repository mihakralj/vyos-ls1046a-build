/* Copyright 2012 Freescale Semiconductor Inc.
 * Copyright 2019 NXP
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *     * Redistributions of source code must retain the above copyright
 *	 notice, this list of conditions and the following disclaimer.
 *     * Redistributions in binary form must reproduce the above copyright
 *	 notice, this list of conditions and the following disclaimer in the
 *	 documentation and/or other materials provided with the distribution.
 *     * Neither the name of Freescale Semiconductor nor the
 *	 names of its contributors may be used to endorse or promote products
 *	 derived from this software without specific prior written permission.
 *
 *
 * ALTERNATIVELY, this software may be distributed under the terms of the
 * GNU General Public License ("GPL") as published by the Free Software
 * Foundation, either version 2 of that License or (at your option) any
 * later version.
 *
 * THIS SOFTWARE IS PROVIDED BY Freescale Semiconductor ``AS IS'' AND ANY
 * EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL Freescale Semiconductor BE LIABLE FOR ANY
 * DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
 * (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 * LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
 * ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 * SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#ifdef CONFIG_FSL_DPAA_ETH_DEBUG
#define pr_fmt(fmt) \
	KBUILD_MODNAME ": %s:%hu:%s() " fmt, \
	KBUILD_BASENAME".c", __LINE__, __func__
#else
#define pr_fmt(fmt) \
	KBUILD_MODNAME ": " fmt
#endif

#include <linux/init.h>
#include <linux/hashtable.h>
#include <linux/skbuff.h>
#include <linux/highmem.h>
#include <linux/fsl_bman.h>
#include <linux/moduleparam.h>
#include <net/sock.h>

#include "dpaa_eth.h"
#include "dpaa_eth_common.h"
#ifdef CONFIG_FSL_DPAA_1588
#include "dpaa_1588.h"
#endif
#ifdef CONFIG_FSL_DPAA_CEETM
#include "dpaa_eth_ceetm.h"
#endif
#if defined(CONFIG_IP_NF_CONNTRACK_MARK) || defined(CONFIG_NF_CONNTRACK_MARK)
#include "net/netfilter/nf_conntrack.h"
#endif // CONFIG_IP_NF_CONNTRACK_MARK ||  CONFIG_NF_CONNTRACK_MARK
#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD)
#include <uapi/linux/if_ether.h>
#include <uapi/linux/ppp_defs.h>
#include <uapi/linux/if_pppox.h>
#include <linux/if_vlan.h>
#include <net/xfrm.h>
#include <net/dst.h>
#endif

#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD)
static int dpaa_ipsec_compat_tx_queue;
module_param_named(ipsec_compat_tx_queue, dpaa_ipsec_compat_tx_queue,
		   int, 0644);
MODULE_PARM_DESC(ipsec_compat_tx_queue,
		 "DPAA TX queue for IPsec compat post-SEC frames (-1=CPU queue)");
#endif

#define DPAA_EXTRA_BUF_SIZE_4_SKB SMP_CACHE_BYTES + DPA_MAX_FD_OFFSET + \
		sizeof(struct skb_shared_info) +  128 

#ifndef EXCLUDE_FMAN_IPR_OFFLOAD
//added for IPR offload to FMAN
static dpaa_eth_bpool_replenish_hook_t dpaa_eth_bpool_replenish_hook;
#endif

/* registered function to get ceetm Fqs */
#if defined(CONFIG_CPE_FAST_PATH) || defined(CONFIG_FSL_DPAA_ASK_CEETM_TX_OWNER)
#define ASK_CEETM_DEFAULT_CLASS_QUEUE	0

static cdx_get_ceetm_egressfq ceetm_fqget_func;
static cdx_get_ceetm_dscp_fq ceetm_dscp_fqget_func;
/* This function registers two cdx functions, which are to get the egress fq. 
   One is based on channel and classqueue and another one is based on dscp. */
int dpa_register_ceetm_get_egress_fq(cdx_get_ceetm_egressfq egress_fq_func, cdx_get_ceetm_dscp_fq dscp_fq_func)
{
	ceetm_fqget_func = egress_fq_func;
	ceetm_dscp_fqget_func = dscp_fq_func;
	return 0;
}
EXPORT_SYMBOL(dpa_register_ceetm_get_egress_fq);
#endif

#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD)
cdx_get_ipsec_fq_hook_t cdx_get_ipsec_fq_hookfn;

int dpa_register_ipsec_fq_handler(cdx_get_ipsec_fq_hook_t hookfn)
{
       if (cdx_get_ipsec_fq_hookfn) {
               printk("%s::hook already registered\n", __FUNCTION__);
               return -1;
       }
       cdx_get_ipsec_fq_hookfn = hookfn;
       return 0;
}

EXPORT_SYMBOL(dpa_register_ipsec_fq_handler);

#endif

/* DMA map and add a page frag back into the bpool.
 * @vaddr fragment must have been allocated with netdev_alloc_frag(),
 * specifically for fitting into @dpa_bp.
 */
static void dpa_bp_recycle_frag(struct dpa_bp *dpa_bp, unsigned long vaddr,
				int *count_ptr)
{
	struct bm_buffer bmb;
	dma_addr_t addr;

	bmb.opaque = 0;

	addr = dma_map_single(dpa_bp->dev, (void *)vaddr, dpa_bp->size,
			      DMA_BIDIRECTIONAL);
	if (unlikely(dma_mapping_error(dpa_bp->dev, addr))) {
		dev_err(dpa_bp->dev, "DMA mapping failed");
		return;
	}

	bm_buffer_set64(&bmb, addr);

	while (bman_release(dpa_bp->pool, &bmb, 1, 0))
		cpu_relax();

	(*count_ptr)++;
}

// adding a new function which allocates memory for buffers and adds bman pool
int dpaa_bp_alloc_n_add_buffs(const struct dpa_bp *dpa_bp, uint32_t nbuffs, bool act_skb)
{
	struct bm_buffer bmb[8];
	char *new_buf, *ptr_buf;
	dma_addr_t addr;
	int ii,entries_in_bmb,buffs2fill;
	struct device *dev = dpa_bp->dev;
	struct sk_buff *skb = NULL, **skbh;
	uint32_t bufsize;

	if (act_skb)
		bufsize =  DPAA_EXTRA_BUF_SIZE_4_SKB + dpa_bp->size;
	else
		bufsize =  SMP_CACHE_BYTES + dpa_bp->size + DPA_MAX_FD_OFFSET + 128;

	/* aligning to SMP_CACHE_BYTES */
	bufsize = DPA_SKB_SIZE(bufsize);

	buffs2fill= 0;
	while (buffs2fill < nbuffs)
	{
		memset(bmb, 0, sizeof(struct bm_buffer) * 8);

		if ((nbuffs -   buffs2fill) < 8)
			entries_in_bmb = (nbuffs - buffs2fill);
		else
			entries_in_bmb = 8;
		for (ii= 0; ii<entries_in_bmb; ii++) {
			ptr_buf = (char *)kmalloc(bufsize, GFP_DMA | GFP_ATOMIC);

			if (unlikely(!ptr_buf))
			{
				pr_err("%s(%d) ii %d,  buffs2fill %d kmalloc failed \n",
						__FUNCTION__,__LINE__,ii, buffs2fill);
				goto handle_fail;
			}

			new_buf = ptr_buf;
			if (act_skb)
			{
				/* We'll prepend the skb back-pointer; can't use the DPA
				 * priv space, because FMan will overwrite it (from offset 0)
				 * if it ends up being the second, third, etc. fragment
				 * in a S/G frame.
				 *
				 * We only need enough space to store a pointer, but allocate
				 * an entire cacheline for performance reasons.
				 */
				skb = slab_build_skb(ptr_buf);
				if (unlikely(!skb)) {
					pr_err("%s(%d) ii %d , buffs2fill %d build_skb failed \n",
							__FUNCTION__,__LINE__, ii, buffs2fill);
					kfree(new_buf);
					goto handle_fail;
				}
				ptr_buf = PTR_ALIGN(ptr_buf, SMP_CACHE_BYTES) + SMP_CACHE_BYTES;
				skb_reserve(skb, (ptr_buf-new_buf));
				DPA_WRITE_SKB_PTR(skb, skbh, ptr_buf, -1);
			}
			else
				ptr_buf = PTR_ALIGN(ptr_buf /*+ SMP_CACHE_BYTES */, SMP_CACHE_BYTES);

			addr = dma_map_single(dev, ptr_buf, dpa_bp->size, DMA_BIDIRECTIONAL);
			if (unlikely(dma_mapping_error(dev, addr)))
			{
				pr_err("%s(%d) ii %d buffs2fill %d dma_mapping_error failed \n",
						__FUNCTION__,__LINE__,ii,buffs2fill);
				if (act_skb)
				{
					kfree_skb(skb);
				}
				kfree(new_buf);
				goto handle_fail;
			}
			bm_buffer_set64(&bmb[ii], addr);

			bmb[ii].bpid = cpu_to_be16(dpa_bp->bpid & 0xff);
		}
		while (unlikely(bman_release(dpa_bp->pool, bmb, entries_in_bmb, 0)))
			cpu_relax();
		buffs2fill+= entries_in_bmb;
		/*     printk("%s(%d) adrsses %lx %lx %lx %lx %lx %lx %lx %lx\n",
			   __FUNCTION__,__LINE__,bm_buffer_get64(&bmb[0]),bm_buffer_get64(&bmb[1]),
			   bm_buffer_get64(&bmb[2]),bm_buffer_get64(&bmb[3]),bm_buffer_get64(&bmb[4]),
			   bm_buffer_get64(&bmb[5]),bm_buffer_get64(&bmb[6]),bm_buffer_get64(&bmb[7])); */
	}
	/* printk("%s(%d) buff allocation and bman release success buffs2fill %d\n",
	   __FUNCTION__,__LINE__,buffs2fill); */
	return 0;

handle_fail:
	if (ii)
	{
		while (unlikely(bman_release(dpa_bp->pool, bmb, ii, 0)))
			cpu_relax();
	}
	return ii+buffs2fill;

}
EXPORT_SYMBOL(dpaa_bp_alloc_n_add_buffs);


static int _dpa_bp_add_8_bufs(const struct dpa_bp *dpa_bp)
{
	void *new_buf, *fman_buf;
	struct bm_buffer bmb[8];
	dma_addr_t addr;
	uint8_t i;
	struct device *dev = dpa_bp->dev;
	struct sk_buff *skb, **skbh;

	memset(bmb, 0, sizeof(struct bm_buffer) * 8);

	for (i = 0; i < 8; i++) {
		/* We'll prepend the skb back-pointer; can't use the DPA
		 * priv space, because FMan will overwrite it (from offset 0)
		 * if it ends up being the second, third, etc. fragment
		 * in a S/G frame.
		 *
		 * We only need enough space to store a pointer, but allocate
		 * an entire cacheline for performance reasons.
		 */
#ifdef FM_ERRATUM_A050385
		if (unlikely(fm_has_errata_a050385())) {
			struct page *new_page = alloc_page(GFP_ATOMIC);
			if (unlikely(!new_page))
				goto netdev_alloc_failed;
			new_buf = page_address(new_page);
		}
		else
#endif
		new_buf = netdev_alloc_frag(SMP_CACHE_BYTES + DPA_BP_RAW_SIZE);

		if (unlikely(!new_buf))
			goto netdev_alloc_failed;
		new_buf = PTR_ALIGN(new_buf, SMP_CACHE_BYTES);

		/* Apart from the buffer that will be used by the FMan, the
		 * skb also guarantees enough space to hold the backpointer
		 * in the headroom and the shared info at the end.
		 */
		skb = build_skb(new_buf,
				SMP_CACHE_BYTES + DPA_SKB_SIZE(dpa_bp->size) +
				SKB_DATA_ALIGN(sizeof(struct skb_shared_info)));
		if (unlikely(!skb)) {
			put_page(virt_to_head_page(new_buf));
			goto build_skb_failed;
		}

		/* Reserve SMP_CACHE_BYTES in the skb's headroom to store the
		 * backpointer. This area will not be synced to, or
		 * overwritten by, the FMan.
		 */
		skb_reserve(skb, SMP_CACHE_BYTES);

		/* We don't sync the first SMP_CACHE_BYTES of the buffer to
		 * the FMan. The skb backpointer is stored at the end of the
		 * reserved headroom. Otherwise it will be overwritten by the
		 * FMan.
		 * The buffer synced with the FMan starts right after the
		 * reserved headroom.
		 */
		fman_buf = new_buf + SMP_CACHE_BYTES;
		DPA_WRITE_SKB_PTR(skb, skbh, fman_buf, -1);

		addr = dma_map_single(dev, fman_buf,
				dpa_bp->size, DMA_BIDIRECTIONAL);
		if (unlikely(dma_mapping_error(dev, addr)))
			goto dma_map_failed;

		bm_buffer_set64(&bmb[i], addr);
	}

release_bufs:
	/* Release the buffers. In case bman is busy, keep trying
	 * until successful. bman_release() is guaranteed to succeed
	 * in a reasonable amount of time
	 */
	while (unlikely(bman_release(dpa_bp->pool, bmb, i, 0)))
		cpu_relax();
	return i;

dma_map_failed:
	kfree_skb(skb);

build_skb_failed:
netdev_alloc_failed:
	net_err_ratelimited("%s failed\n", __func__);
	WARN_ONCE(1, "Memory allocation failure on Rx\n");

	bm_buffer_set64(&bmb[i], 0);
	/* Avoid releasing a completely null buffer; bman_release() requires
	 * at least one buffer.
	 */
	if (likely(i))
		goto release_bufs;

	return 0;
}

/* Add buffers/(pages) for Rx processing whenever bpool count falls below
 * REFILL_THRESHOLD.
 */
int dpaa_eth_refill_bpools(struct dpa_bp *dpa_bp, int *countptr, int threshold)
{
	int count = *countptr;
	int new_bufs;
	int percpu_buf_cfg_count = dpa_bp->config_count;

	if (unlikely(count <= (percpu_buf_cfg_count - threshold))) {
		do {
			new_bufs = _dpa_bp_add_8_bufs(dpa_bp);
			if (unlikely(!new_bufs)) {
				/* Avoid looping forever if we've temporarily
				 * run out of memory. We'll try again at the
				 * next NAPI cycle.
				 */
				break;
			}
			count += new_bufs;
		} while (count < percpu_buf_cfg_count);

		*countptr = count;
		if (unlikely(count < percpu_buf_cfg_count))
			return -ENOMEM;
	}

	return 0;
}
EXPORT_SYMBOL(dpaa_eth_refill_bpools);

/* Cleanup function for outgoing frame descriptors that were built on Tx path,
 * either contiguous frames or scatter/gather ones.
 * Skb freeing is not handled here.
 *
 * This function may be called on error paths in the Tx function, so guard
 * against cases when not all fd relevant fields were filled in.
 *
 * Return the skb backpointer, since for S/G frames the buffer containing it
 * gets freed here.
 */
struct sk_buff *_dpa_cleanup_tx_fd(const struct dpa_priv_s *priv,
	const struct qm_fd *fd)
{
	const struct qm_sg_entry *sgt;
	int i;
	struct dpa_bp *dpa_bp = priv->dpa_bp;
	dma_addr_t addr = qm_fd_addr(fd);
	dma_addr_t sg_addr;
	struct sk_buff **skbh;
	struct sk_buff *skb = NULL;
	const enum dma_data_direction dma_dir = DMA_TO_DEVICE;
	int nr_frags;
	int sg_len;
#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD)
	struct dpa_percpu_priv_s *ipsec_percpu_priv =
		raw_cpu_ptr(priv->percpu_priv);
#endif

	/* retrieve skb back pointer */
	DPA_READ_SKB_PTR(skb, skbh, phys_to_virt(addr), 0);

	/* IPsec compat-path frames have no associated skb.  The current CDX
	 * compat path leaves these as NULL and lets the successful FMan egress
	 * path own final buffer recycling.  Keep the marker branch for diagnostics
	 * around older/experimental builds, but current CDX should not hit it.
	 */
#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD)
	if (unlikely(skb == DPAA_IPSEC_COMPAT_TX_CONFIRM_RELEASE)) {
		ipsec_percpu_priv->ipsec_txconf_marker_release++;
		dpa_fd_release(NULL, fd);
		return NULL;
	}
#endif
	if (unlikely(!skb)) {
#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD)
		ipsec_percpu_priv->ipsec_txconf_null++;
		if (fd->bpid == 35)
			ipsec_percpu_priv->ipsec_txconf_bpid35++;
#endif
		return NULL;
	}

	if (unlikely(fd->format == qm_fd_sg)) {
		nr_frags = skb_shinfo(skb)->nr_frags;
		dma_unmap_single(dpa_bp->dev, addr,
				 dpa_fd_offset(fd) + DPA_SGT_SIZE,
				 dma_dir);

		/* The sgt buffer has been allocated with netdev_alloc_frag(),
		 * it's from lowmem.
		 */
		sgt = phys_to_virt(addr + dpa_fd_offset(fd));
#ifdef CONFIG_FSL_DPAA_1588
		if (priv->tsu && priv->tsu->valid &&
				priv->tsu->hwts_tx_en_ioctl)
			dpa_ptp_store_txstamp(priv, skb, (void *)skbh);
#endif
#ifdef CONFIG_FSL_DPAA_TS
		if (unlikely(priv->ts_tx_en &&
			skb_shinfo(skb)->tx_flags & SKBTX_HW_TSTAMP)) {
			struct skb_shared_hwtstamps shhwtstamps;

			dpa_get_ts(priv, TX, &shhwtstamps, (void *)skbh);
			skb_tstamp_tx(skb, &shhwtstamps);
		}
#endif /* CONFIG_FSL_DPAA_TS */

		/* sgt[0] is from lowmem, was dma_map_single()-ed */
		sg_addr = qm_sg_addr(&sgt[0]);
		sg_len = qm_sg_entry_get_len(&sgt[0]);
		dma_unmap_single(dpa_bp->dev, sg_addr, sg_len, dma_dir);

		/* remaining pages were mapped with dma_map_page() */
		for (i = 1; i <= nr_frags; i++) {
			DPA_BUG_ON(qm_sg_entry_get_ext(&sgt[i]));
			sg_addr = qm_sg_addr(&sgt[i]);
			sg_len = qm_sg_entry_get_len(&sgt[i]);
			dma_unmap_page(dpa_bp->dev, sg_addr, sg_len, dma_dir);
		}

		/* Free the page frag that we allocated on Tx */
		put_page(virt_to_head_page(sgt));
	} else {
		dma_unmap_single(dpa_bp->dev, addr,
				 skb_tail_pointer(skb) - (u8 *)skbh, dma_dir);
#ifdef CONFIG_FSL_DPAA_TS
		/* get the timestamp for non-SG frames */
#ifdef CONFIG_FSL_DPAA_1588
		if (priv->tsu && priv->tsu->valid &&
						priv->tsu->hwts_tx_en_ioctl)
			dpa_ptp_store_txstamp(priv, skb, (void *)skbh);
#endif
		if (unlikely(priv->ts_tx_en &&
				skb_shinfo(skb)->tx_flags & SKBTX_HW_TSTAMP)) {
			struct skb_shared_hwtstamps shhwtstamps;

			dpa_get_ts(priv, TX, &shhwtstamps, (void *)skbh);
			skb_tstamp_tx(skb, &shhwtstamps);
		}
#endif
	}

	return skb;
}
EXPORT_SYMBOL(_dpa_cleanup_tx_fd);

#ifndef CONFIG_FSL_DPAA_TS
bool dpa_skb_is_recyclable(struct sk_buff *skb)
{
	/* No recycling possible if skb buffer is kmalloc'ed  */
	if (skb->head_frag == 0)
		return false;

	/* or if it's an userspace buffer */
	if (skb_shinfo(skb)->flags & SKBFL_ZEROCOPY_ENABLE)
		return false;

	/* or if it's cloned or shared */
	if (skb_shared(skb) || skb_cloned(skb) ||
	    skb->fclone != SKB_FCLONE_UNAVAILABLE)
		return false;

	return true;
}
EXPORT_SYMBOL(dpa_skb_is_recyclable);

bool dpa_buf_is_recyclable(struct sk_buff *skb,
				  uint32_t min_size,
				  uint16_t min_offset,
				  unsigned char **new_buf_start)
{
	unsigned char *new;

	/* In order to recycle a buffer, the following conditions must be met:
	 * - buffer size no less than the buffer pool size
	 * - buffer size no higher than an upper limit (to avoid moving too much
	 *   system memory to the buffer pools)
	 * - buffer address aligned to cacheline bytes
	 * - offset of data from start of buffer no lower than a minimum value
	 * - offset of data from start of buffer no higher than a maximum value
	 * - the skb back-pointer is stored safely
	 */

	/* guarantee both the minimum size and the minimum data offset */
	new = min(skb_end_pointer(skb) - min_size, skb->data - min_offset);

	/* left align to the nearest cacheline */
	new = (unsigned char *)((unsigned long)new & ~(SMP_CACHE_BYTES - 1));

	/* Make sure there is enough space to store the skb back-pointer in
	 * the headroom, right before the start of the buffer.
	 *
	 * Guarantee that both maximum size and maximum data offsets aren't
	 * crossed.
	 */
	if (likely(new >= (skb->head + sizeof(void *)) &&
		   new >= (skb->data - DPA_MAX_FD_OFFSET) &&
		   skb_end_pointer(skb) - new <= DPA_RECYCLE_MAX_SIZE)) {
		*new_buf_start = new;
		return true;
	}

	return false;
}
EXPORT_SYMBOL(dpa_buf_is_recyclable);
#endif

/* Build a linear skb around the received buffer.
 * We are guaranteed there is enough room at the end of the data buffer to
 * accommodate the shared info area of the skb.
 */
struct sk_buff *__hot contig_fd_to_skb(const struct dpa_priv_s *priv,
					      const struct qm_fd *fd,
					      bool *use_gro, bool dcl4c_valid)
{
	dma_addr_t addr = qm_fd_addr(fd);
	ssize_t fd_off = dpa_fd_offset(fd);
	void *vaddr;
	const fm_prs_result_t *parse_results;
	struct sk_buff *skb = NULL, **skbh;

	vaddr = phys_to_virt(addr);
	DPA_BUG_ON(!IS_ALIGNED((unsigned long)vaddr, SMP_CACHE_BYTES));

	/* Retrieve the skb and adjust data and tail pointers, to make sure
	 * forwarded skbs will have enough space on Tx if extra headers
	 * are added.
	 */
	DPA_READ_SKB_PTR(skb, skbh, vaddr, -1);

#ifdef CONFIG_FSL_DPAA_ETH_JUMBO_FRAME
	/* When using jumbo Rx buffers, we risk having frames dropped due to
	 * the socket backlog reaching its maximum allowed size.
	 * Use the frame length for the skb truesize instead of the buffer
	 * size, as this is the size of the data that actually gets copied to
	 * userspace.
	 * The stack may increase the payload. In this case, it will want to
	 * warn us that the frame length is larger than the truesize. We
	 * bypass the warning.
	 */
	skb->truesize = SKB_TRUESIZE(dpa_fd_length(fd));
#endif

	DPA_BUG_ON(fd_off != priv->rx_headroom);
	skb_reserve(skb, fd_off);
	skb_put(skb, dpa_fd_length(fd));

	/* Peek at the parse results for csum validation */
	parse_results = (const fm_prs_result_t *)(vaddr +
				DPA_RX_PRIV_DATA_SIZE);
	_dpa_process_parse_results(parse_results, fd, skb, use_gro, dcl4c_valid);

#ifdef CONFIG_FSL_DPAA_1588
	if (priv->tsu && priv->tsu->valid && priv->tsu->hwts_rx_en_ioctl)
		dpa_ptp_store_rxstamp(priv, skb, vaddr);
#endif
#ifdef CONFIG_FSL_DPAA_TS
	if (priv->ts_rx_en)
		dpa_get_ts(priv, RX, skb_hwtstamps(skb), vaddr);
#endif /* CONFIG_FSL_DPAA_TS */

	return skb;
}
EXPORT_SYMBOL(contig_fd_to_skb);

/* Build an skb with the data of the first S/G entry in the linear portion and
 * the rest of the frame as skb fragments.
 *
 * The page fragment holding the S/G Table is recycled here.
 */
struct sk_buff *__hot sg_fd_to_skb(const struct dpa_priv_s *priv,
					  const struct qm_fd *fd, bool *use_gro,
					  int *count_ptr, bool dcl4c_valid)
{
	const struct qm_sg_entry *sgt;
	dma_addr_t addr = qm_fd_addr(fd);
	ssize_t fd_off = dpa_fd_offset(fd);
	dma_addr_t sg_addr;
	void *vaddr, *sg_vaddr;
	struct dpa_bp *dpa_bp;
	struct page *page, *head_page;
	int frag_offset, frag_len;
	int page_offset;
	int i;
	const fm_prs_result_t *parse_results;
	struct sk_buff *skb = NULL, *skb_tmp, **skbh;

	vaddr = phys_to_virt(addr);
	DPA_BUG_ON(!IS_ALIGNED((unsigned long)vaddr, SMP_CACHE_BYTES));

/*	dpa_bp = priv->dpa_bp; */
	dpa_bp =  dpa_bpid2pool(fd->bpid);
	/* Iterate through the SGT entries and add data buffers to the skb */
	sgt = vaddr + fd_off;
	for (i = 0; i < DPA_SGT_MAX_ENTRIES; i++) {
		/* Extension bit is not supported */
		DPA_BUG_ON(qm_sg_entry_get_ext(&sgt[i]));

		/* We use a single global Rx pool */
/*		DPA_BUG_ON(dpa_bp !=
			   dpa_bpid2pool(qm_sg_entry_get_bpid(&sgt[i])));
*/
		sg_addr = qm_sg_addr(&sgt[i]);
		sg_vaddr = phys_to_virt(sg_addr);
		DPA_BUG_ON(!IS_ALIGNED((unsigned long)sg_vaddr,
				SMP_CACHE_BYTES));

		dma_unmap_single(dpa_bp->dev, sg_addr, dpa_bp->size,
				 DMA_BIDIRECTIONAL);
		if (i == 0) {
			DPA_READ_SKB_PTR(skb, skbh, sg_vaddr, -1);
#ifdef CONFIG_FSL_DPAA_1588
			if (priv->tsu && priv->tsu->valid &&
			    priv->tsu->hwts_rx_en_ioctl)
				dpa_ptp_store_rxstamp(priv, skb, vaddr);
#endif
#ifdef CONFIG_FSL_DPAA_TS
			if (priv->ts_rx_en)
				dpa_get_ts(priv, RX, skb_hwtstamps(skb), vaddr);
#endif /* CONFIG_FSL_DPAA_TS */

			/* In the case of a SG frame, FMan stores the Internal
			 * Context in the buffer containing the sgt.
			 * Inspect the parse results before anything else.
			 */
			parse_results = (const fm_prs_result_t *)(vaddr +
						DPA_RX_PRIV_DATA_SIZE);
			_dpa_process_parse_results(parse_results, fd, skb,
						   use_gro, dcl4c_valid);

			/* Make sure forwarded skbs will have enough space
			 * on Tx, if extra headers are added.
			 */
			DPA_BUG_ON(fd_off != priv->rx_headroom);
			skb_reserve(skb, fd_off);
			skb_put(skb, qm_sg_entry_get_len(&sgt[i]));
		} else {
			/* Not the first S/G entry; all data from buffer will
			 * be added in an skb fragment; fragment index is offset
			 * by one since first S/G entry was incorporated in the
			 * linear part of the skb.
			 *
			 * Caution: 'page' may be a tail page.
			 */
			DPA_READ_SKB_PTR(skb_tmp, skbh, sg_vaddr, -1);
			page = virt_to_page(sg_vaddr);
			head_page = virt_to_head_page(sg_vaddr);

			/* Free (only) the skbuff shell because its data buffer
			 * is already a frag in the main skb.
			 */
			get_page(head_page);
			dev_kfree_skb(skb_tmp);

			/* Compute offset in (possibly tail) page */
			page_offset = ((unsigned long)sg_vaddr &
					(PAGE_SIZE - 1)) +
				(page_address(page) - page_address(head_page));
			/* page_offset only refers to the beginning of sgt[i];
			 * but the buffer itself may have an internal offset.
			 */
			frag_offset = qm_sg_entry_get_offset(&sgt[i]) +
					page_offset;
			frag_len = qm_sg_entry_get_len(&sgt[i]);
			/* skb_add_rx_frag() does no checking on the page; if
			 * we pass it a tail page, we'll end up with
			 * bad page accounting and eventually with segafults.
			 */
			skb_add_rx_frag(skb, i - 1, head_page, frag_offset,
				frag_len, dpa_bp->size);
		}
		/* Update the pool count for the current {cpu x bpool} */
		(*count_ptr)--;

		if (qm_sg_entry_get_final(&sgt[i]))
			break;
	}
	WARN_ONCE(i == DPA_SGT_MAX_ENTRIES, "No final bit on SGT\n");

	/* recycle the SGT fragment */
	DPA_BUG_ON(dpa_bp != dpa_bpid2pool(fd->bpid));
	dpa_bp_recycle_frag(dpa_bp, (unsigned long)vaddr, count_ptr);
	return skb;
}
EXPORT_SYMBOL(sg_fd_to_skb);

#ifdef CONFIG_FSL_DPAA_DBG_LOOP
static inline int dpa_skb_loop(const struct dpa_priv_s *priv,
		struct sk_buff *skb)
{
	if (unlikely(priv->loop_to < 0))
		return 0; /* loop disabled by default */

	skb_push(skb, ETH_HLEN); /* compensate for eth_type_trans */
	/* Save the current CPU ID in order to maintain core affinity */
	skb_set_queue_mapping(skb, raw_smp_processor_id());
	dpa_tx(skb, dpa_loop_netdevs[priv->loop_to]);

	return 1; /* Frame Tx on the selected interface */
}
#endif

#ifndef EXCLUDE_FMAN_IPR_OFFLOAD
void register_dpaa_eth_bpool_replenish_hook(dpaa_eth_bpool_replenish_hook_t func)
{
	dpaa_eth_bpool_replenish_hook = func;
}
EXPORT_SYMBOL(register_dpaa_eth_bpool_replenish_hook);
#endif

void __hot _dpa_rx(struct net_device *net_dev,
		struct qman_portal *portal,
		const struct dpa_priv_s *priv,
		struct dpa_percpu_priv_s *percpu_priv,
		const struct qm_fd *fd,
		u32 fqid,
		int *count_ptr)
{
	bool dcl4c_valid = !!(net_dev->features & NETIF_F_RXCSUM);
	bool use_gro = !!(net_dev->features & NETIF_F_GRO);
	struct dpa_bp *dpa_bp;
	struct sk_buff *skb;
	dma_addr_t addr = qm_fd_addr(fd);
	u32 fd_status = fd->status;
	unsigned int skb_len;
	struct rtnl_link_stats64 *percpu_stats = &percpu_priv->stats;

	if (unlikely(fd_status & FM_FD_STAT_RX_ERRORS) != 0) {
		if (netif_msg_hw(priv) && net_ratelimit())
			netdev_warn(net_dev, "_dpa_rx FD status = 0x%08x\n",
					fd_status & FM_FD_STAT_RX_ERRORS);

		percpu_stats->rx_errors++;
		goto _release_frame;
	}

	dpa_bp = priv->dpa_bp;
	DPA_BUG_ON(dpa_bp != dpa_bpid2pool(fd->bpid));

	/* prefetch the first 64 bytes of the frame or the SGT start */
	dma_unmap_single(dpa_bp->dev, addr, dpa_bp->size, DMA_BIDIRECTIONAL);
	prefetch(phys_to_virt(addr) + dpa_fd_offset(fd));

	/* The only FD types that we may receive are contig and S/G */
	DPA_BUG_ON((fd->format != qm_fd_contig) && (fd->format != qm_fd_sg));
#if 0
        {
                char *ptr;
                uint32_t ii;

                ptr = ((char *)phys_to_virt(addr) + 0x0);
                for (ii  = 0; ii < 0x70; ii++) {
                        if ((ii % 16) == 0)
                                printk("\n%02x ", *(ptr + ii));
                        else
                                printk("%02x ", *(ptr + ii));
                }
                printk("\n");
        }
	{
		uint32_t ccbase;
		uint64_t hashval;
		//get ccbase 
		ccbase  = *((uint32_t *)((char *)phys_to_virt(addr) + 0x18));
                printk("%s::ccbase %08x, fqid 0x%x\n", __FUNCTION__, cpu_to_be32(ccbase), fqid);
		hashval  = *((uint64_t *)((char *)phys_to_virt(addr) + 0x48));
                printk("%s::hashval %lx\n", __FUNCTION__, cpu_to_be64(hashval));
	 		
	}
#endif

	if (likely(fd->format == qm_fd_contig)) {
#ifdef CONFIG_FSL_DPAA_HOOKS
		/* Execute the Rx processing hook, if it exists. */
		if (dpaa_eth_hooks.rx_default &&
			dpaa_eth_hooks.rx_default((void *)fd, net_dev,
					fqid) == DPAA_ETH_STOLEN) {
			/* won't count the rx bytes in */
			return;
		}
#endif
#ifndef EXCLUDE_FMAN_IPR_OFFLOAD
		if (dpaa_eth_bpool_replenish_hook) {
			if (dpaa_eth_bpool_replenish_hook(net_dev, fd->bpid)) {
				/* could not replenish buffer, drop packet ?? */
				printk("%s::could not replenish buffer to pool\n",
						__FUNCTION__);
			}
		}
#endif
		skb = contig_fd_to_skb(priv, fd, &use_gro, dcl4c_valid);
	} else {
		skb = sg_fd_to_skb(priv, fd, &use_gro, count_ptr, dcl4c_valid);
		percpu_priv->rx_sg++;
	}

	/* Account for either the contig buffer or the SGT buffer (depending on
	 * which case we were in) having been removed from the pool.
	 */
	(*count_ptr)--;
	skb->protocol = eth_type_trans(skb, net_dev);

	skb_len = skb->len;

#ifdef CONFIG_FSL_DPAA_DBG_LOOP
	if (dpa_skb_loop(priv, skb)) {
		percpu_stats->rx_packets++;
		percpu_stats->rx_bytes += skb_len;
		return;
	}
#endif

	skb_record_rx_queue(skb, raw_smp_processor_id());

	if (use_gro) {
		const struct qman_portal_config *pc =
					qman_p_get_portal_config(portal);
		struct dpa_napi_portal *np = &percpu_priv->np[pc->index];

		np->p = portal;
		/* The stack doesn't report if the frame was dropped but it
		 * will increment rx_dropped automatically.
		 */
		napi_gro_receive(&np->napi, skb);
	} else if (unlikely(netif_receive_skb(skb) == NET_RX_DROP))
		return;

	percpu_stats->rx_packets++;
	percpu_stats->rx_bytes += skb_len;

	return;

_release_frame:
	dpa_fd_release(net_dev, fd);
}
EXPORT_SYMBOL(_dpa_rx);

int __hot skb_to_contig_fd(struct dpa_priv_s *priv,
			   struct sk_buff *skb, struct qm_fd *fd,
			   int *count_ptr, int *offset)
{
	struct sk_buff **skbh;
	dma_addr_t addr;
	struct dpa_bp *dpa_bp = priv->dpa_bp;
	struct net_device *net_dev = priv->net_dev;
	int err;
	enum dma_data_direction dma_dir;
	unsigned char *buffer_start;
	int dma_map_size;

#ifndef CONFIG_FSL_DPAA_TS
	/* Check recycling conditions; only if timestamp support is not
	 * enabled, otherwise we need the fd back on tx confirmation
	 */

	/* We can recycle the buffer if:
	 * - the pool is not full
	 * - the buffer meets the skb recycling conditions
	 * - the buffer meets our own (size, offset, align) conditions
	 */
	if (likely((*count_ptr < dpa_bp->target_count) &&
		   dpa_skb_is_recyclable(skb) &&
		   dpa_buf_is_recyclable(skb, dpa_bp->size,
					 priv->tx_headroom, &buffer_start))) {
		/* Buffer is recyclable; use the new start address
		 * and set fd parameters and DMA mapping direction
		 */
		fd->bpid = dpa_bp->bpid;
		DPA_BUG_ON(skb->data - buffer_start > DPA_MAX_FD_OFFSET);
		fd->offset = (uint16_t)(skb->data - buffer_start);
		dma_dir = DMA_BIDIRECTIONAL;
		dma_map_size = dpa_bp->size;

		/* Store the skb back-pointer before the start of the buffer.
		 * Otherwise it will be overwritten by the FMan.
		 */
		DPA_WRITE_SKB_PTR(skb, skbh, buffer_start, -1);
		*offset = skb_headroom(skb) - fd->offset;
	} else
#endif
	{
		/* Not recyclable.
		 * We are guaranteed to have at least tx_headroom bytes
		 * available, so just use that for offset.
		 */
		fd->bpid = 0xff;
		buffer_start = skb->data - priv->tx_headroom;
		fd->offset = priv->tx_headroom;
		dma_dir = DMA_TO_DEVICE;
		dma_map_size = skb_tail_pointer(skb) - buffer_start;

		/* The buffer will be Tx-confirmed, but the TxConf cb must
		 * necessarily look at our Tx private data to retrieve the
		 * skbuff. Store the back-pointer inside the buffer.
		 */
		DPA_WRITE_SKB_PTR(skb, skbh, buffer_start, 0);
	}

	/* Enable L3/L4 hardware checksum computation.
	 *
	 * We must do this before dma_map_single(DMA_TO_DEVICE), because we may
	 * need to write into the skb.
	 */
	err = dpa_enable_tx_csum(priv, skb, fd,
				 ((char *)skbh) + DPA_TX_PRIV_DATA_SIZE);
	if (unlikely(err < 0)) {
		if (netif_msg_tx_err(priv) && net_ratelimit())
			netdev_err(net_dev, "HW csum error: %d\n", err);
		return err;
	}

	/* Fill in the rest of the FD fields */
	fd->format = qm_fd_contig;
	fd->length20 = skb->len;
	fd->cmd |= FM_FD_CMD_FCO;

	/* Map the entire buffer size that may be seen by FMan, but no more */
	addr = dma_map_single(dpa_bp->dev, skbh, dma_map_size, dma_dir);
	if (unlikely(dma_mapping_error(dpa_bp->dev, addr))) {
		if (netif_msg_tx_err(priv) && net_ratelimit())
			netdev_err(net_dev, "dma_map_single() failed\n");
		return -EINVAL;
	}
	qm_fd_addr_set64(fd, addr);

	return 0;
}
EXPORT_SYMBOL(skb_to_contig_fd);

#ifdef FM_ERRATUM_A050385
/* Verify the conditions that trigger the A050385 errata:
 * - 4K memory address boundary crossings when the data/SG fragments aren't
 *   aligned to 256 bytes
 * - data and SG fragments that aren't aligned to 16 bytes
 * - SG fragments that aren't mod 16 bytes in size (except for the last
 *   fragment)
 */
bool a050385_check_skb(struct sk_buff *skb, struct dpa_priv_s *priv)
{
	skb_frag_t *frag;
	int i, nr_frags;

	nr_frags = skb_shinfo(skb)->nr_frags;

	/* Check if the linear data is 16 byte aligned */
	if ((uintptr_t)skb->data % 16)
		return true;

	/* Check if the needed headroom crosses a 4K address boundary without
	 * being 256 byte aligned
	 */
	if (CROSS_4K(skb->data - priv->tx_headroom, priv->tx_headroom) &&
	    (((uintptr_t)skb->data - priv->tx_headroom) % 256))
		return true;

	/* Check if the linear data crosses a 4K address boundary without
	 * being 256 byte aligned
	 */
	if (CROSS_4K(skb->data, skb_headlen(skb)) &&
	    ((uintptr_t)skb->data % 256))
		return true;

	/* When using Scatter/Gather, the linear data becomes the first
	 * fragment in the list and must follow the same restrictions as the
	 * other fragments.
	 *
	 * Check if the linear data is mod 16 bytes in size.
	 */
	if (nr_frags && (skb_headlen(skb) % 16))
		return true;

	/* Check the SG fragments. They must follow the same rules as the
	 * linear data with and additional restriction: they must be multiple
	 * of 16 bytes in size to account for the hardware carryover effect.
	 */
	for (i = 0; i < nr_frags; i++) {
		frag = &skb_shinfo(skb)->frags[i];

		/* Check if the fragment is a multiple of 16 bytes in size.
		 * The last fragment is exempt from this restriction.
		 */
		if ((i != (nr_frags - 1)) && (skb_frag_size(frag) % 16))
			return true;

		/* Check if the fragment is 16 byte aligned */
		if (skb_frag_off(frag) % 16)
			return true;

		/* Check if the fragment crosses a 4K address boundary. Since
		 * the alignment of previous fragments can influence the
		 * current fragment, checking for the 256 byte alignment
		 * isn't relevant.
		 */
		if (CROSS_4K(skb_frag_off(frag), skb_frag_size(frag)))
			return true;
	}

	return false;
}
EXPORT_SYMBOL(a050385_check_skb);

/* Realign the skb by copying its contents at the start of a newly allocated
 * page. Build a new skb around the new buffer and release the old one.
 * A performance drop should be expected.
 */
struct sk_buff *a050385_realign_skb(struct sk_buff *skb,
					   struct dpa_priv_s *priv)
{
	int trans_offset = skb_transport_offset(skb);
	int net_offset = skb_network_offset(skb);
	struct sk_buff *nskb = NULL;
	int nsize, headroom;
	struct page *npage;
	void *npage_addr;

	headroom = DPAA_A050385_HEADROOM;

	/* For the new skb we only need the old one's data (both non-paged and
	 * paged). We can skip the old tailroom.
	 *
	 * Make sure the skb_shinfo is cache-line aligned.
	 */
	nsize = SMP_CACHE_BYTES + DPA_SKB_SIZE(headroom + skb->len) +
		SKB_DATA_ALIGN(sizeof(struct skb_shared_info));

	/* Reserve enough memory to accommodate Jumbo frames */
	npage = alloc_pages(GFP_ATOMIC | __GFP_COMP, get_order(nsize));
	if (unlikely(!npage)) {
		WARN_ONCE(1, "Memory allocation failure\n");
		return NULL;
	}
	npage_addr = page_address(npage);

	nskb = build_skb(npage_addr, nsize);
	if (unlikely(!nskb))
		goto err;

	/* Reserve only the needed headroom in order to guarantee the data's
	 * alignment.
	 * Code borrowed and adapted from skb_copy().
	 */
	skb_reserve(nskb, headroom);
	skb_put(nskb, skb->len);
	if (skb_copy_bits(skb, 0, nskb->data, skb->len)) {
		WARN_ONCE(1, "skb parsing failure\n");
		goto err;
	}
	skb_copy_header(nskb, skb);

#ifdef CONFIG_FSL_DPAA_TS
	/* Copy relevant timestamp info from the old skb to the new */
	if (priv->ts_tx_en) {
		skb_shinfo(nskb)->tx_flags = skb_shinfo(skb)->tx_flags;
		skb_shinfo(nskb)->hwtstamps = skb_shinfo(skb)->hwtstamps;
		skb_shinfo(nskb)->tskey = skb_shinfo(skb)->tskey;
		if (skb->sk)
			skb_set_owner_w(nskb, skb->sk);
	}
#endif
	/* We move the headroom when we align it so we have to reset the
	 * network and transport header offsets relative to the new data
	 * pointer. The checksum offload relies on these offsets.
	 */
	skb_set_network_header(nskb, net_offset);
	skb_set_transport_header(nskb, trans_offset);

	return nskb;

err:
	if (nskb)
		dev_kfree_skb(nskb);
	put_page(npage);
	return NULL;
}
EXPORT_SYMBOL(a050385_realign_skb);
#endif

struct dpa_bp *sg_bpool_g;
EXPORT_SYMBOL(sg_bpool_g);

struct dpa_bp *skb_2bfreed_bpool_g; //if no recyclable skbs exist in skb fraglist, those should be freed back, SEC engine will add to this bman pool
EXPORT_SYMBOL(skb_2bfreed_bpool_g);

struct dpaa_ipsec_compound_reclaim_ctx {
	struct hlist_node node;
	dma_addr_t sg_addr;
	struct sk_buff *skb;
	u32 sgt_size;
};

static DEFINE_HASHTABLE(dpaa_ipsec_compound_reclaim_ht, 8);
static DEFINE_SPINLOCK(dpaa_ipsec_compound_reclaim_lock);

static int dpaa_ipsec_register_compound_reclaim_ctx(dma_addr_t sg_addr,
		struct sk_buff *skb, u32 sgt_size)
{
	struct dpaa_ipsec_compound_reclaim_ctx *ctx;
	unsigned long flags;

	ctx = kmalloc(sizeof(*ctx), GFP_ATOMIC);
	if (!ctx)
		return -ENOMEM;

	ctx->sg_addr = sg_addr;
	ctx->skb = skb;
	ctx->sgt_size = sgt_size;

	spin_lock_irqsave(&dpaa_ipsec_compound_reclaim_lock, flags);
	hash_add(dpaa_ipsec_compound_reclaim_ht, &ctx->node, (u64)sg_addr);
	spin_unlock_irqrestore(&dpaa_ipsec_compound_reclaim_lock, flags);
	return 0;
}

static void __maybe_unused dpaa_ipsec_cancel_compound_reclaim_ctx(dma_addr_t sg_addr)
{
	struct dpaa_ipsec_compound_reclaim_ctx *ctx;
	struct hlist_node *tmp;
	unsigned long flags;

	spin_lock_irqsave(&dpaa_ipsec_compound_reclaim_lock, flags);
	hash_for_each_possible_safe(dpaa_ipsec_compound_reclaim_ht, ctx, tmp,
			node, (u64)sg_addr) {
		if (ctx->sg_addr != sg_addr)
			continue;
		hash_del(&ctx->node);
		kfree(ctx);
		break;
	}
	spin_unlock_irqrestore(&dpaa_ipsec_compound_reclaim_lock, flags);
}

int dpaa_ipsec_release_compound_reclaim_ctx(const struct qm_fd *fd)
{
	struct dpaa_ipsec_compound_reclaim_ctx *ctx = NULL;
	struct hlist_node *tmp;
	struct dpa_bp *dpa_bp;
	struct bm_buffer bmb;
	unsigned long flags;
	dma_addr_t sg_addr;

	if (!fd || !qm_fd_addr(fd))
		return -EINVAL;

	sg_addr = qm_fd_addr(fd);

	spin_lock_irqsave(&dpaa_ipsec_compound_reclaim_lock, flags);
	hash_for_each_possible_safe(dpaa_ipsec_compound_reclaim_ht, ctx, tmp,
			node, (u64)sg_addr) {
		if (ctx->sg_addr != sg_addr)
			continue;
		hash_del(&ctx->node);
		break;
	}
	spin_unlock_irqrestore(&dpaa_ipsec_compound_reclaim_lock, flags);

	if (!ctx)
		return -ENOENT;

	dpa_bp = dpa_bpid2pool(fd->bpid);
	if (!dpa_bp) {
		kfree(ctx);
		return -EINVAL;
	}

	if (ctx->skb)
		kfree_skb(ctx->skb);

	dma_unmap_single(dpa_bp->dev, sg_addr, ctx->sgt_size, DMA_BIDIRECTIONAL);

	/* Sanitize buffer for safe recycling: set final on sgt[0] so the
	 * skb_2bfreed_bpool_g recycle walk finds a valid final entry and
	 * reads sgt[1].opaque as NULL (no stale skb pointer). */
	{
		struct qm_sg_entry *sgt = phys_to_virt(sg_addr);
		memset(sgt, 0, sizeof(*sgt) * 2);
		qm_sg_entry_set_final(&sgt[0], 1);
	}

	bmb.opaque = 0;
	bm_buffer_set64(&bmb, sg_addr);
	while (bman_release(dpa_bp->pool, &bmb, 1, 0))
		cpu_relax();

	kfree(ctx);
	return 0;
}
EXPORT_SYMBOL(dpaa_ipsec_release_compound_reclaim_ctx);

struct device* dpa_get_bp_device(void)
{
	if (skb_2bfreed_bpool_g)
		return skb_2bfreed_bpool_g->dev;
	else
		return NULL;
}

/* 
 *This functions unmaps all the mapped skb sg so far and stored in sgt.  
*/
static void dma_unmap_skb_sg_addrs(struct device *dev, struct sk_buff *skb,
			struct qm_sg_entry *sgt, int skb_list_frags)
{
	dma_addr_t addr;
	struct sk_buff *list_skb;
	struct sk_buff *cur_skb;
	const enum dma_data_direction dma_dir = DMA_TO_DEVICE;
	int sgt_idx = 0;
	int nr_frags = 0;


	for (cur_skb = skb, list_skb = skb_shinfo(skb)->frag_list; 
		(sgt_idx < skb_list_frags) && (cur_skb); )
	{
		addr = qm_sg_addr(&sgt[sgt_idx]);
		dma_unmap_single(dev, addr, qm_sg_entry_get_len(&sgt[sgt_idx]), dma_dir);
		sgt_idx++;

		nr_frags = skb_shinfo(cur_skb)->nr_frags;
		while(nr_frags)
		{
			addr = qm_sg_addr(&sgt[sgt_idx]);
			dma_unmap_page(dev, addr, qm_sg_entry_get_len(&sgt[sgt_idx]), dma_dir);
			nr_frags--;
			sgt_idx++;
		}
		cur_skb = list_skb;
		if (list_skb)
			list_skb = list_skb->next;
	}

	return;
}

/*
 * This inline function updates the sgt entry parameters.
 */
static inline void prepare_qm_sg_entry(struct qm_sg_entry *sgt, int sgt_idx, 
					unsigned int len, dma_addr_t addr)
{
	qm_sg_entry_set_ext(&sgt[sgt_idx], 0);
	qm_sg_entry_set_final(&sgt[sgt_idx], 0);
	/* fill the first entry with skb details */
	qm_sg_entry_set_bpid(&sgt[sgt_idx], 0xff);
	/* set the offset and addr pointers such that
	   in next alloc, that addr should point to skb*/
	qm_sg_entry_set_offset(&sgt[sgt_idx],0);
	qm_sg_entry_set_len(&sgt[sgt_idx], len);
	qm_sg_entry_set64(&sgt[sgt_idx], addr);

	return;
}

static int dpaa_ipsec_wrap_sg_fd_as_compound(struct device *dev,
		struct net_device *net_dev, struct qm_fd *fd,
		struct qm_sg_entry *sgt, dma_addr_t sg_addr,
		int input_entries, int sgt_size, struct sk_buff *skb)
{
	u32 req_len;
	u32 req_cmd;
	int total_entries;

	if (fd->format != qm_fd_sg)
		return -EINVAL;

	if (input_entries <= 0)
		return -EINVAL;

	total_entries = input_entries + 3;
	if ((sizeof(*sgt) * total_entries) > sgt_size) {
		netdev_err(net_dev,
			"IPSEC compound wrap needs %d SG entries, max is %d\n",
			total_entries, sgt_size / (int)sizeof(*sgt));
		return -E2BIG;
	}

	memmove(&sgt[2], &sgt[0], sizeof(*sgt) * (input_entries + 1));
	memset(&sgt[0], 0, sizeof(*sgt) * 2);

	qm_sg_entry_set_ext(&sgt[0], 0);
	qm_sg_entry_set_final(&sgt[0], 0);
	qm_sg_entry_set_bpid(&sgt[0], 0);
	qm_sg_entry_set_offset(&sgt[0], 0);
	qm_sg_entry_set_len(&sgt[0], 0);
	qm_sg_entry_set64(&sgt[0], 0);

	qm_sg_entry_set_ext(&sgt[1], 1);
	qm_sg_entry_set_final(&sgt[1], 1);
	qm_sg_entry_set_bpid(&sgt[1], 0);
	qm_sg_entry_set_offset(&sgt[1], 0);
	qm_sg_entry_set_len(&sgt[1], fd->length20);
	qm_sg_entry_set64(&sgt[1], sg_addr + (2 * sizeof(*sgt)));

	dma_sync_single_for_device(dev, sg_addr,
			sizeof(*sgt) * total_entries, DMA_TO_DEVICE);

	req_len = fd->length20;
	req_cmd = fd->cmd;
	qm_fd_addr_set64(fd, sg_addr);
	fd->format = qm_fd_compound;
	fd->cong_weight = req_len;
	fd->cmd = req_cmd;

	if (dpaa_ipsec_register_compound_reclaim_ctx(sg_addr, skb, sgt_size))
		return -ENOMEM;


	return 0;
}

int __hot skb_fraglist_to_sg_fd(struct device *dev, struct net_device *net_dev , struct sk_buff *skb, struct qm_fd *fd, u32 fd_cmd)
{
	dma_addr_t addr, sg_addr;
	struct sk_buff *list_skb;
	struct sk_buff *cur_skb;
	int  sgt_size;
	int err;
	struct qm_sg_entry *sgt;
	struct bm_buffer bmb;
	int sgt_index = 0, nr_frags = 0;
	bool wrap_as_compound = !!fd_cmd;
	const enum dma_data_direction dma_dir = DMA_TO_DEVICE;
	int dma_map_size;
	skb_frag_t *frag;

	// calculate the number of fragments
	// check if any of skb is not recyclable
	nr_frags = 1 + skb_shinfo(skb)->nr_frags;
	skb_walk_frags(skb, list_skb)
	{
		nr_frags += 1 + skb_shinfo(list_skb)->nr_frags;
	}

	/* The below condition covers nr_frags + 1(This is to store skb address in opaque variable at the end.)*/
	if (nr_frags > (DPA_SGT_MAX_ENTRIES - (wrap_as_compound ? 2 : 0)))
	{
		/* TODO linearize */
		pr_err("%s()::%d number of skb frags %d crossed the MAX SGT entries%d", __func__, __LINE__, nr_frags, DPA_SGT_MAX_ENTRIES);
		return -1;
	}

	fd->format = qm_fd_sg;
	/* allocate an SG buffer from SG BMAN POOL
	   if any entries in skb_2bfreed_bpool_g, acquire and free skb and use the SG buffer */
	if (bman_acquire(skb_2bfreed_bpool_g->pool, &bmb, 1, 0) == 1)
	{
		sgt =  (struct qm_sg_entry *)phys_to_virt(bmb.addr);
		while (!qm_sg_entry_get_final(&sgt[sgt_index]))
			sgt_index++;
		addr = sgt[sgt_index+1].opaque;
		if (addr) /* free the old skb */
		{
			list_skb =  (struct sk_buff *)(addr);
			kfree_skb(list_skb);
		}
		sgt[sgt_index+1].opaque = 0; /* set to 0*/
	}
	else if (bman_acquire(sg_bpool_g->pool, &bmb, 1, 0) == 1)
	{
		sgt =  (struct qm_sg_entry *)phys_to_virt(bmb.addr);
	} 
	else
	{
		printk("%s(%d) bman_acquire of SG bman buffer failed\n",
				__FUNCTION__,__LINE__);
		return -1;
	}


	/* net_crit_ratelimited("%s(%d) nr_frags %d, MAX frags %d \n", __FUNCTION__,__LINE__,nr_frags,DPA_SGT_MAX_ENTRIES);*/
	/* Reserve space for the opaque skb slot, plus two extra entries when wrapping as a compound FD. */
	sgt_size = sizeof(struct qm_sg_entry) *
		(nr_frags + 1 + (wrap_as_compound ? 2 : 0));


	/* memset to 0s of size sgt_size*/
	memset(sgt, 0, sgt_size);

	

	sgt_index = 0;
	for (cur_skb = skb, list_skb = skb_shinfo(skb)->frag_list; cur_skb;)
	{
		dma_map_size = skb_headlen(cur_skb);
		addr = dma_map_single(dev, cur_skb->data, dma_map_size, dma_dir);
		if (unlikely(dma_mapping_error(dev, addr))) {
			if (net_ratelimit())
				netdev_err(net_dev, "skb_fraglist_to_sg_fd : 2.DMA mappping error");
			err = -EINVAL;
			dma_unmap_skb_sg_addrs(dev, skb, sgt, sgt_index);
			bman_release(sg_bpool_g->pool, &bmb, 1, 0);
			return -1;
		}
		prepare_qm_sg_entry(sgt, sgt_index, dma_map_size, addr);
		sgt_index++;
		
		nr_frags = skb_shinfo(cur_skb)->nr_frags;
		frag = skb_shinfo(cur_skb)->frags;
		while(nr_frags)
		{
			addr = skb_frag_dma_map(dev, frag, 0, skb_frag_size(frag), dma_dir);
			if (unlikely(dma_mapping_error(dev, addr))) {
				if (net_ratelimit())
					netdev_err(net_dev, "skb_fraglist_to_sg_fd : 2.DMA mappping error");
				dma_unmap_skb_sg_addrs(dev, skb, sgt, sgt_index);
				err = -EINVAL;
				bman_release(sg_bpool_g->pool, &bmb, 1, 0);
				return -1;
			}
			prepare_qm_sg_entry(sgt, sgt_index, skb_frag_size(frag), addr);
			frag++;
			nr_frags--;
			sgt_index++;
		}
		cur_skb = list_skb;
		if (list_skb)
			list_skb = list_skb->next;
	}

	qm_sg_entry_set_final(&sgt[sgt_index-1], 1);

	addr = (dma_addr_t) (skb);
	sgt[sgt_index].opaque = addr;

	sg_addr = dma_map_single(dev, sgt, sgt_size, dma_dir);
	if (unlikely(dma_mapping_error(dev, sg_addr))) {
		/*if (netif_msg_tx_err(priv) && net_ratelimit())*/
		dma_unmap_skb_sg_addrs(dev, skb, sgt, sgt_index);
		if (net_ratelimit())
			netdev_err(net_dev, "skb_fraglist_to_sg_fd : 2.DMA mappping error");
		err = -EINVAL;
		bman_release(sg_bpool_g->pool, &bmb, 1, 0);
		return -1;
	}
	qm_fd_addr_set64(fd, sg_addr);
	fd->bpid = skb_2bfreed_bpool_g->bpid; /*sg_bpool_g->bpid;*/
	fd->cmd = fd_cmd;
	fd->length20 = skb->len;
	fd->offset = 0;

	if (wrap_as_compound) {
		err = dpaa_ipsec_wrap_sg_fd_as_compound(dev, net_dev, fd, sgt,
				sg_addr, sgt_index, sgt_size, skb);
		if (unlikely(err < 0)) {
			dma_unmap_single(dev, sg_addr, sgt_size, dma_dir);
			dma_unmap_skb_sg_addrs(dev, skb, sgt, sgt_index);
			bman_release(sg_bpool_g->pool, &bmb, 1, 0);
			return -1;
		}
	}

	/*net_crit_ratelimited("%s(%d) sgt_index %d, addr %p \n",__FUNCTION__, __LINE__,sgt_index,(void *)(sgt[sgt_index].opaque)); */
	/* percpu_priv->tx_returned++; */
	return 0;
}

EXPORT_SYMBOL(skb_fraglist_to_sg_fd);
int __hot skb_to_sg_fd(struct dpa_priv_s *priv,
		       struct sk_buff *skb, struct qm_fd *fd)
{
	struct dpa_bp *dpa_bp = priv->dpa_bp;
	dma_addr_t addr;
	dma_addr_t sg_addr;
	struct sk_buff **skbh;
	struct net_device *net_dev = priv->net_dev;
	int sg_len, sgt_size;
	int err;

	struct qm_sg_entry *sgt;
	void *sgt_buf;
	skb_frag_t *frag;
	int i = 0, j = 0;
	int nr_frags;
	const enum dma_data_direction dma_dir = DMA_TO_DEVICE;

	nr_frags = skb_shinfo(skb)->nr_frags;
	fd->format = qm_fd_sg;

	/* The FMan reads 256 bytes from the start of the SGT regardless of
	 * its size. In accordance, we reserve the same amount of memory as
	 * well.
	 */
	sgt_size = DPA_SGT_SIZE;

	/* Get a page frag to store the SGTable, or a full page if the errata
	 * is in place and we need to avoid crossing a 4k boundary.
	 */
#ifdef FM_ERRATUM_A050385
	if (unlikely(fm_has_errata_a050385())) {
		struct page *new_page = alloc_page(GFP_ATOMIC);

		if (unlikely(!new_page))
			return -ENOMEM;
		sgt_buf = page_address(new_page);
	}
	else
#endif
		sgt_buf = netdev_alloc_frag(priv->tx_headroom + sgt_size);
	if (unlikely(!sgt_buf)) {
		dev_err(dpa_bp->dev, "netdev_alloc_frag() failed\n");
		return -ENOMEM;
	}

	/* it seems that the memory allocator does not zero the allocated mem */
	memset(sgt_buf, 0, priv->tx_headroom + sgt_size);

	/* Enable L3/L4 hardware checksum computation.
	 *
	 * We must do this before dma_map_single(DMA_TO_DEVICE), because we may
	 * need to write into the skb.
	 */
	err = dpa_enable_tx_csum(priv, skb, fd,
				 sgt_buf + DPA_TX_PRIV_DATA_SIZE);
	if (unlikely(err < 0)) {
		if (netif_msg_tx_err(priv) && net_ratelimit())
			netdev_err(net_dev, "HW csum error: %d\n", err);
		goto csum_failed;
	}

	/* Assign the data from skb->data to the first SG list entry */
	sgt = (struct qm_sg_entry *)(sgt_buf + priv->tx_headroom);
	sg_len = skb_headlen(skb);
	qm_sg_entry_set_bpid(&sgt[0], 0xff);
	qm_sg_entry_set_offset(&sgt[0], 0);
	qm_sg_entry_set_len(&sgt[0], sg_len);
	qm_sg_entry_set_ext(&sgt[0], 0);
	qm_sg_entry_set_final(&sgt[0], 0);

	addr = dma_map_single(dpa_bp->dev, skb->data, sg_len, dma_dir);
	if (unlikely(dma_mapping_error(dpa_bp->dev, addr))) {
		dev_err(dpa_bp->dev, "DMA mapping failed");
		err = -EINVAL;
		goto sg0_map_failed;
	}

	qm_sg_entry_set64(&sgt[0], addr);

	/* populate the rest of SGT entries */
	for (i = 1; i <= nr_frags; i++) {
		frag = &skb_shinfo(skb)->frags[i - 1];
		qm_sg_entry_set_bpid(&sgt[i], 0xff);
		qm_sg_entry_set_offset(&sgt[i], 0);
		qm_sg_entry_set_len(&sgt[i], skb_frag_size(frag));
		qm_sg_entry_set_ext(&sgt[i], 0);

		if (i == nr_frags)
			qm_sg_entry_set_final(&sgt[i], 1);
		else
			qm_sg_entry_set_final(&sgt[i], 0);

		DPA_BUG_ON(!skb_frag_page(frag));
		addr = skb_frag_dma_map(dpa_bp->dev, frag, 0,
					skb_frag_size(frag), dma_dir);
		if (unlikely(dma_mapping_error(dpa_bp->dev, addr))) {
			dev_err(dpa_bp->dev, "DMA mapping failed");
			err = -EINVAL;
			goto sg_map_failed;
		}

		/* keep the offset in the address */
		qm_sg_entry_set64(&sgt[i], addr);
	}

	fd->length20 = skb->len;
	fd->offset = priv->tx_headroom;

	/* DMA map the SGT page
	 *
	 * It's safe to store the skb back-pointer inside the buffer since
	 * S/G frames are non-recyclable.
	 */
	DPA_WRITE_SKB_PTR(skb, skbh, sgt_buf, 0);
	addr = dma_map_single(dpa_bp->dev, sgt_buf,
			      priv->tx_headroom + sgt_size,
			      dma_dir);

	if (unlikely(dma_mapping_error(dpa_bp->dev, addr))) {
		dev_err(dpa_bp->dev, "DMA mapping failed");
		err = -EINVAL;
		goto sgt_map_failed;
	}

	qm_fd_addr_set64(fd, addr);
	fd->bpid = 0xff;
	fd->cmd |= FM_FD_CMD_FCO;

	return 0;

sgt_map_failed:
sg_map_failed:
	for (j = 0; j < i; j++) {
		sg_addr = qm_sg_addr(&sgt[j]);
		dma_unmap_page(dpa_bp->dev, sg_addr,
			       qm_sg_entry_get_len(&sgt[j]), dma_dir);
	}
sg0_map_failed:
csum_failed:
	put_page(virt_to_head_page(sgt_buf));

	return err;
}
EXPORT_SYMBOL(skb_to_sg_fd);

#define EMAC_QUEUENUM_MASK 0xff

#ifdef CONFIG_CPE_FAST_PATH
static int pfe_eth_get_queuenum( struct sk_buff *skb )
{

#if defined(CONFIG_IP_NF_CONNTRACK_MARK) || defined(CONFIG_NF_CONNTRACK_MARK)
	/* Use conntrack mark (if conntrack exists) */
	if (skb->_nfct) {
		enum ip_conntrack_info cinfo;
		struct nf_conn *ct;

		ct = nf_ct_get(skb, &cinfo);
		if (ct) {
			u_int64_t markval;

			markval = ct->qosconnmark;
			if (cinfo >= IP_CT_IS_REPLY) {
				if (markval & ((uint64_t)1 << 63))
					markval >>= 32;
               else
					markval = 0;
			}
			markval &= 0x7fffffff;
			return markval;

		}
	}
#endif
	/* use  packet mark (if any) */
	if (skb->mark) {
		return (skb->mark & EMAC_QUEUENUM_MASK);
	}
	/* These are packets through control path, assign highest priority */
	return (QOS_DEFAULT_QUEUE);
}
#endif /*endif for CONFIG_CPE_FAST_PATH */

#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD)
unsigned int ipsec_offload_pkt_cnt;
void print_ipsec_offload_pkt_count(void)
{
	printk("%s:: Ipsec offload slow path packet count = %d\n",__func__,ipsec_offload_pkt_cnt);
	ipsec_offload_pkt_cnt = 0;
}
EXPORT_SYMBOL(print_ipsec_offload_pkt_count);
#endif

#ifdef CONFIG_XFRM
#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD)

#define ETH_HDR_SIZE            14
#define VLAN_HDR_SIZE           4
#define PPPOE_HDR_SIZE          8
#define ETHHDR_MACEND_OFFSET    12

#define DPOVRD_ENABLE		0x80000000

unsigned char* dpa_get_skb_nh(struct sk_buff* skb, unsigned short *l3_proto, unsigned short* l3_offset)
{
	unsigned short type_id;
	unsigned short offset = ETH_HDR_SIZE;
	type_id = *((unsigned short*) (skb->data + offset - 2));
	while (1)
	{
		switch (type_id)
		{
			case htons(ETH_P_PPP_SES):
				offset += PPPOE_HDR_SIZE;
				type_id = *(unsigned short*)(skb->data + offset - 2);

				if (type_id == htons(PPP_IP))
					type_id = ntohs(ETH_P_IP);
				else if (type_id == htons(PPP_IPV6))
					type_id = ntohs(ETH_P_IPV6);
				break;
			case htons(ETH_P_8021Q):
				offset += VLAN_HDR_SIZE;
				type_id = *(unsigned short*) (skb->data + offset - 2);
				break;
			case htons(ETH_P_IP):
			case htons(ETH_P_IPV6):
				*l3_proto = type_id;
				*l3_offset = offset;
				return (skb->data + offset);
			default:
				*l3_proto = type_id;
				*l3_offset = offset;
				return NULL;
		}
	}
}

#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD)
static bool dpaa_ipsec_ipv4_tuple(struct sk_buff *skb,
				       __be32 *saddr, __be32 *daddr)
{
	unsigned short l3_proto = 0, l3_offset = 0;
	unsigned char *skb_nh;
	struct iphdr *iph;

	skb_nh = dpa_get_skb_nh(skb, &l3_proto, &l3_offset);
	if (!skb_nh || l3_proto != htons(ETH_P_IP))
		return false;

	iph = (struct iphdr *)skb_nh;
	*saddr = iph->saddr;
	*daddr = iph->daddr;
	return true;
}

static struct net_device *dpaa_ipsec_real_dpa_dev(struct net_device *net_dev)
{
	int depth = 0;

	while (net_dev && is_vlan_dev(net_dev) && depth++ < 8)
		net_dev = vlan_dev_real_dev(net_dev);

	if (!net_dev || net_dev->type != ARPHRD_ETHER)
		return NULL;

	if (!dpa_is_private_netdev(net_dev))
		return NULL;

	return net_dev;
}

static struct sec_path *dpaa_recover_secpath_from_dst(struct sk_buff *skb)
{
	struct sec_path *sp;
	struct dst_entry *dst;
	struct xfrm_state *x;

	sp = skb_sec_path(skb);
	if (sp && sp->len)
		return sp;

	if (!skb->ipsec_offload)
		return NULL;

	dst = skb_dst(skb);
	if (!dst || !dst->xfrm)
		return NULL;

	x = dst->xfrm;
	sp = secpath_set(skb);
	if (!sp)
		return NULL;

	if (sp->len && sp->xvec[sp->len - 1] == x)
		return sp;

	if (sp->len == XFRM_MAX_DEPTH)
		return NULL;

	sp->olen++;
	sp->xvec[sp->len++] = x;
	xfrm_state_hold(x);


	return sp;
}

#endif

int __hot dpaa_submit_outb_pkt_to_SEC(struct sk_buff *skb,
				      struct net_device *net_dev,
				      struct dpa_bp *dpa_bp)
{
	struct xfrm_state *x;
	struct dpa_priv_s *priv = netdev_priv(net_dev);
	struct dpa_percpu_priv_s *percpu_priv =
		raw_cpu_ptr(priv->percpu_priv);
	struct qman_fq *egress_fq = NULL;
	struct qm_fd		 fd;
	int err = 0,ii;
	int new_ethhdr_end, old_ethhdr_end;
	unsigned int sg_entries = 0;
	unsigned short l3_proto, l3_offset;
	unsigned char* skb_nh ;
	struct sk_buff *list_skb;
	struct sec_path *sp;
	unsigned int dpovrd = 0;
	u32 sa_handle = 0;
	unsigned int sp_len = 0;
	__be32 ipsec_saddr = 0, ipsec_daddr = 0;
	bool have_ipsec_tuple;
	const char *fail_reason = "unknown";

	have_ipsec_tuple = dpaa_ipsec_ipv4_tuple(skb, &ipsec_saddr,
						    &ipsec_daddr);
	sp = skb_sec_path(skb);
#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD)
	if (unlikely((!sp || !sp->len) && skb->ipsec_offload))
		sp = dpaa_recover_secpath_from_dst(skb);
#endif
	if (sp)
		sp_len = sp->len;

	sg_entries = 1 + skb_shinfo(skb)->nr_frags;
	skb_walk_frags(skb, list_skb)
		sg_entries += 1 + skb_shinfo(list_skb)->nr_frags;
	percpu_priv->ipsec_out_sg_entries += sg_entries;

	if (skb_is_gso(skb))
		percpu_priv->ipsec_out_gso++;
	if (skb_is_nonlinear(skb))
		percpu_priv->ipsec_out_nonlinear++;
	if (skb_shinfo(skb)->frag_list)
		percpu_priv->ipsec_out_fraglist++;

	if (skb->ipsec_offload && skb->ipsec_sa_handle) {
		sa_handle = skb->ipsec_sa_handle;
	} else if (sp && sp->len) {
		x = sp->xvec[0];
		sa_handle = x->handle;
	}

	if (unlikely(!cdx_get_ipsec_fq_hookfn)) {
		fail_reason = "missing-hook";
		percpu_priv->ipsec_out_no_hook++;
		goto sec_submit_failed;
	}

	if (unlikely(!sa_handle)) {
		fail_reason = "missing-sa-handle";
		percpu_priv->ipsec_out_no_sa++;
		goto sec_submit_failed;
	}

	if ((cdx_get_ipsec_fq_hookfn) && (sa_handle))
	{
		egress_fq = cdx_get_ipsec_fq_hookfn(sa_handle);

		/* when a packet is submitting to SEC engine,
		use the below code */
		if (!egress_fq)
		{
			fail_reason = "fq-miss";
			percpu_priv->ipsec_out_fq_miss++;
			goto sec_submit_failed;	
		}
		/*
		* For packets with L2 headers other than ethernet header, L2 headers are
		* stripped off and only ethernet header is maintained. These L2 headers
		* will be added again in offline port classification table entry
		* after SEC processing.
		*/

		skb_nh = dpa_get_skb_nh(skb, &l3_proto, &l3_offset);

		if (skb_nh)
		{
			if(l3_offset != ETH_HDR_SIZE)
			{
				percpu_priv->ipsec_out_l2_strip++;
				new_ethhdr_end = l3_offset - 2  ; /*l3 offset - protocol(2bytes) */
				old_ethhdr_end = ETHHDR_MACEND_OFFSET ;
				while(old_ethhdr_end >= 0)
					skb->data[new_ethhdr_end--] = skb->data[old_ethhdr_end--];

				*((unsigned short*) (skb->data + l3_offset - 2) ) = l3_proto;
				skb->data = skb->data + l3_offset - ETH_HDR_SIZE;
				skb->len = skb->len - l3_offset + ETH_HDR_SIZE;
			}
			if ((*skb_nh >> 4) == IPVERSION) /* Extracting ip version from network header */
			{
				dpovrd = IPPROTO_IPIP; /* ESP trailer NH type. Here traffic is IPv4 */
			}
			else
			{
				dpovrd = IPPROTO_IPV6; /* ESP trailer NH type. Here traffic is IPv4 */
			}
			dpovrd |= DPOVRD_ENABLE; /* OVRD enable */
		}


		err =  skb_fraglist_to_sg_fd(dpa_bp->dev, net_dev, skb, &fd, dpovrd);
		if (unlikely(err < 0))
		{
			fail_reason = "fd-build-fail";
			percpu_priv->ipsec_out_fd_fail++;
			goto sec_submit_failed;
		}

		if (fd.format == qm_fd_compound)
			percpu_priv->ipsec_out_compound++;

		for (ii = 0; ii < 100000; ii++) {
			err = qman_enqueue(egress_fq, &fd, 0);
			if (err != -EBUSY)
				break;
		}
		if (ii) {
			percpu_priv->ipsec_out_enqueue_busy += ii;
			if (ii > 1024 || err == -EBUSY)
				percpu_priv->ipsec_out_enqueue_busy_high++;
		}

		if (unlikely(err < 0)) {
			struct bm_buffer bmb;
			dma_addr_t addr;

			fail_reason = "enqueue-fail";
			percpu_priv->ipsec_out_enqueue_fail++;
			/* put the buffer back to sg pool */
			addr = qm_fd_addr(&fd);
			if (fd.format == qm_fd_compound)
				dpaa_ipsec_cancel_compound_reclaim_ctx(addr);
			bm_buffer_set64(&bmb, addr);
			while (bman_release(sg_bpool_g->pool, &bmb, 1, 0))
				cpu_relax();
			/* release skb and retuen */
			goto sec_submit_failed;
		}
		/* sucessful transmit to CAAM */
		percpu_priv->ipsec_out_ok++;
		netif_trans_update(net_dev);
		ipsec_offload_pkt_cnt++;
		return 0;
	}
sec_submit_failed:
	if (have_ipsec_tuple)
		net_info_ratelimited("%s: ASK-IPSEC: outbound SEC submit failed reason=%s offload=%u skb_sagd=0x%x selected_sagd=0x%x sp_len=%u flow=%pI4->%pI4\n",
				     net_dev->name, fail_reason,
				     skb->ipsec_offload, skb->ipsec_sa_handle,
				     sa_handle, sp_len, &ipsec_saddr,
				     &ipsec_daddr);
	else
		net_info_ratelimited("%s: ASK-IPSEC: outbound SEC submit failed reason=%s offload=%u skb_sagd=0x%x selected_sagd=0x%x sp_len=%u\n",
				     net_dev->name, fail_reason,
				     skb->ipsec_offload, skb->ipsec_sa_handle,
				     sa_handle, sp_len);
	dev_kfree_skb(skb);
	return -1;

}

EXPORT_SYMBOL(dpaa_submit_outb_pkt_to_SEC);

int __hot dpaa_ipsec_xmit_compat_fd(struct net_device *net_dev,
				    struct qm_fd *fd)
{
	struct dpa_priv_s *priv;
	struct dpa_percpu_priv_s *percpu_priv;
	struct qman_fq *egress_fq;
	struct qman_fq *conf_fq;
	unsigned int queue;
	int err;

	if (!net_dev || !fd || !qm_fd_addr(fd) || !fd->length20)
		return -EINVAL;
	if (fd->format != qm_fd_contig && fd->format != qm_fd_sg)
		return -EINVAL;
	if (!dpa_is_private_netdev(net_dev))
		return -ENODEV;

	priv = netdev_priv(net_dev);
	if (!priv || !priv->percpu_priv)
		return -ENODEV;

	if (dpaa_ipsec_compat_tx_queue < 0)
		queue = raw_smp_processor_id() % DPAA_ETH_TX_QUEUES;
	else
		queue = dpaa_ipsec_compat_tx_queue % DPAA_ETH_TX_QUEUES;
#if defined(CONFIG_CPE_FAST_PATH) || defined(CONFIG_FSL_DPAA_ASK_CEETM_TX_OWNER)
	if (priv->ceetm_en) {
		if (!ceetm_fqget_func || !priv->qm_ctx)
			return -ENODEV;

		egress_fq = ceetm_fqget_func(priv->qm_ctx, 0,
					     ASK_CEETM_DEFAULT_CLASS_QUEUE, 0);
		if (!egress_fq)
			return -ENODEV;
	} else
#endif
	egress_fq = priv->egress_fqs[queue];
	conf_fq = priv->conf_fqs[queue];
	if (!egress_fq || !conf_fq)
		return -ENODEV;

	percpu_priv = raw_cpu_ptr(priv->percpu_priv);
	err = dpa_xmit(priv, &percpu_priv->stats, fd, egress_fq, conf_fq);
	if (unlikely(err < 0))
		return err;

	netif_trans_update(net_dev);
	return 0;
}
EXPORT_SYMBOL(dpaa_ipsec_xmit_compat_fd);

/* This function is invoked from xfrm_input.c to submit IPSEC packets
   to SEC engine, if the corresponding SA is programmed in ucode
*/
int dpaa_submit_inb_pkt_to_SEC(struct sk_buff *skb, uint16_t sagd)
{
	struct qman_fq *sec_fq = NULL;
	struct dpa_priv_s	*priv = NULL;
	struct dpa_percpu_priv_s *percpu_priv = NULL;
	struct qm_fd		 fd;
	int err, ii;
	uint8_t tmp_len = 0, data[ETH_HLEN],ipvsn;
	struct net_device *netdev;
	struct device* dev;
	struct net_device *real_dev = NULL;
	int iif;

	if (!cdx_get_ipsec_fq_hookfn) {
		net_warn_ratelimited("ASK-IPSEC: inbound SEC submit failed reason=missing-hook sagd=0x%x\n",
				     sagd);
		return -1;
	}

	if (cdx_get_ipsec_fq_hookfn)
	{
		/*net_crit_ratelimited("%s(%d) x->handle %d, skb->data %p, machdr %p\n",
		  __FUNCTION__,__LINE__, sagd,skb->data, skb_mac_header(skb)); */
		/* Get the SEC FQID corresponding to sagd */
		sec_fq = cdx_get_ipsec_fq_hookfn(sagd);

		/*if no SEC FQID found, return non-zero to return pkt to linux*/
		if (!sec_fq || !(skb->dev))
		{
			real_dev = dpaa_ipsec_real_dpa_dev(skb->dev);
			if (!sec_fq && real_dev) {
				priv = netdev_priv(real_dev);
				percpu_priv = raw_cpu_ptr(priv->percpu_priv);
				percpu_priv->ipsec_in_no_fq++;
				priv = NULL;
				percpu_priv = NULL;
			}
			net_warn_ratelimited("ASK-IPSEC: inbound SEC submit failed reason=%s sagd=0x%x dev=%s\n",
					     sec_fq ? "missing-skb-dev" : "fq-miss",
					     sagd,
					     skb->dev ? skb->dev->name : "-");
			return -1; /* give packet to linux */
		}
#ifdef UNIT_TEST
		{
			skb->dev->type = ARPHRD_NONE;
			skb->mac_len = 0;
		}
#endif
		if (skb->dev->type == ARPHRD_ETHER) 
		{
			/* Get the corresponding physical device info, if the skb->dev corresponds to vlan*/
			real_dev = dpaa_ipsec_real_dpa_dev(skb->dev);
			if (!real_dev) {
				net_warn_ratelimited("ASK-IPSEC: inbound SEC submit failed reason=ether-not-dpa sagd=0x%x dev=%s type=%u\n",
						     sagd, skb->dev->name,
						     skb->dev->type);
				return -1;
			}
			priv = netdev_priv(real_dev);
			/* SEC shared descriptor designed to take packet including MAC hdr */
			/* modify skb->data to point to MAC hdr */
			tmp_len = (skb->data - skb_mac_header(skb));
			skb->len += tmp_len;
			skb->data = skb_mac_header(skb);
		}
		else if (skb->dev->type == ARPHRD_PPP)
		{
			/* Get the corresponding physica device info */
#if IS_ENABLED(CONFIG_NF_CONNTRACK)
			iif = skb->layerscape_underlying_iif ?
				skb->layerscape_underlying_iif : skb->skb_iif;
#else
			iif = skb->skb_iif;
#endif
			netdev = __dev_get_by_index(dev_net(skb->dev), iif);
			if (!netdev)
			{
				net_warn_ratelimited("ASK-IPSEC: inbound SEC submit failed reason=iif-miss sagd=0x%x iif=%d dev=%s\n",
						     sagd, iif, skb->dev->name);
				return -1;
			}
			real_dev = dpaa_ipsec_real_dpa_dev(netdev);
			if (!real_dev) {
				net_warn_ratelimited("ASK-IPSEC: inbound SEC submit failed reason=ppp-iif-not-dpa sagd=0x%x iif=%d dev=%s resolved=%s type=%u\n",
						     sagd, iif, skb->dev->name,
						     netdev->name, netdev->type);
				return -1;
			}
			if (skb_headroom(skb) < ETH_HLEN + PPPOE_SES_HLEN) {
				net_warn_ratelimited("ASK-IPSEC: inbound SEC submit failed reason=ppp-headroom sagd=0x%x dev=%s headroom=%u need=%u\n",
						     sagd, skb->dev->name,
						     skb_headroom(skb),
						     ETH_HLEN + PPPOE_SES_HLEN);
				return -1;
			}
			priv = netdev_priv(real_dev);
			/* observation in case of PPPoE skb_mac_header is pointing to IP header*/
			/* get the IP version*/
			/*printk("%s(%d), MAC header ptr: \n",
			  __FUNCTION__,__LINE__);
			  display_buf_data(&skb->head[skb->mac_header] ,16);*/
			ipvsn = ((skb->head[skb->mac_header]) & 0xf0) >> 4;
			/*printk(KERN_INFO "iif_dev :%s, ifindex %d, ipvsn %d\n", netdev->name, netdev->ifindex, ipvsn);*/
			/* SEC shared descriptor designed to take packet including MAC hdr */
			/* modify skb->data to point to MAC hdr */
			tmp_len = (skb->data - skb_mac_header(skb) + ETH_HLEN);
			skb->len += tmp_len;
			skb->data = skb_mac_header(skb) - ETH_HLEN;
			/* Copy MAC hdr to the skb->data */
			/* have a backup data in case of failure */
			memcpy(data, skb->data, ETH_HLEN);
			/* copying 8 bytes and then 4 bytes from the backup , as there is overlap*/
			memcpy(skb->data, skb->data-PPPOE_SES_HLEN, PPPOE_SES_HLEN);
			memcpy(skb->data+PPPOE_SES_HLEN, data, 4);
			/* setting IPv4/IPv6 ethernet protocol value */
			if (ipvsn == DPAA_IP_VERSION_4)
			{
				skb->data[12] = 0x08;
				skb->data[13] = 0x00;
			}
			else /* IP v6 */
			{
				skb->data[12] = 0x86;
				skb->data[13] = 0xdd;
			}
			/*printk("%s(%d), modified header : \n",
			  __FUNCTION__,__LINE__);
			  display_buf_data(skb->data,16);*/
		}
		if (!priv) {
			net_warn_ratelimited("ASK-IPSEC: inbound SEC submit failed reason=no-priv sagd=0x%x dev=%s\n",
					     sagd, skb->dev ? skb->dev->name : "-");
			return -1;
		}
		dev = priv->dpa_bp->dev;
		netdev = priv->net_dev;
		percpu_priv = raw_cpu_ptr(priv->percpu_priv);
		/*FIXME: skb->dev->type other than ARPHRD_ETHER and ARPHRD_PPP priv is uninitialized. Here else case should add to address it. */
		/*net_crit_ratelimited("%s(%d) x->handle %d, skb->data %p, machdr %p, len %d, skb->dev %p\n",
			__FUNCTION__,__LINE__, sagd, skb->data, skb_mac_header(skb),skb->len, skb->dev); */
		err =  skb_fraglist_to_sg_fd(dev, netdev, skb, &fd, 0);
		if (unlikely(err < 0))
		{
			percpu_priv->ipsec_in_fd_fail++;
			net_info_ratelimited("%s: ASK-IPSEC: inbound SEC submit failed reason=fd-build sagd=0x%x dev=%s\n",
					     netdev->name, sagd,
					     skb->dev ? skb->dev->name : "-");
			if (skb->dev->type == ARPHRD_PPP)
				memcpy(skb->data, data, ETH_HLEN);

			skb->len -= tmp_len;
			skb->data += tmp_len;
			return -1; /* give packet to linux */
		}

		for (ii = 0; ii < 100000; ii++) {
			err = qman_enqueue(sec_fq, &fd, 0);
			if (err != -EBUSY)
				break;
		}

		if (unlikely(err < 0)) {
			struct bm_buffer bmb;
			dma_addr_t addr;

			percpu_priv->ipsec_in_enqueue_fail++;
			net_info_ratelimited("%s: ASK-IPSEC: inbound SEC submit failed reason=enqueue sagd=0x%x err=%d\n",
					     netdev->name, sagd, err);
			/* put the buffer back to sg pool */
			addr = qm_fd_addr(&fd);
			bm_buffer_set64(&bmb, addr);
			while (bman_release(sg_bpool_g->pool, &bmb, 1, 0))
				cpu_relax();
			if (skb->dev->type == ARPHRD_PPP)
				memcpy(skb->data, data, ETH_HLEN);
			skb->len -= tmp_len;
			skb->data += tmp_len;
			return -1; /* give packet to linux */
		}
		if (priv)
		{
			percpu_priv->ipsec_in_ok++;
			percpu_priv->tx_caam_dec++;
		}
	}
	return 0;
}
EXPORT_SYMBOL(dpaa_submit_inb_pkt_to_SEC);
#endif
#endif

#ifdef CONFIG_CPE_FAST_PATH
#define CHANNEL_BIT_POSITION 24

int cpe_fp_tx(struct sk_buff *skb, struct net_device *net_dev)
{
	struct dpa_priv_s	*priv;
	struct dpa_percpu_priv_s *percpu_priv;
	struct sec_path *sp = NULL;
	struct xfrm_state *sec_x = NULL;
	int channel_id;
	int queuenum;
	int markval,ff=0;
	struct qman_fq *egress_fq, *conf_fq;
	uint8_t get_ceetm_cm_egress_fq;
	uint8_t dscp;
	unsigned char* skb_nh ;
	uint16_t l3_proto = 0, l3_offset = 0;

	priv = netdev_priv(net_dev);
	percpu_priv = raw_cpu_ptr(priv->percpu_priv);
#ifdef CONFIG_XFRM
#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD)
	sp = skb_sec_path(skb);
	if (sp && sp->len)
		sec_x = sp->xvec[0];

	/* Inbound exception-return packets carry a sec_path for policy
	 * context, but must not be resubmitted to SEC on LAN egress. */
	if (!skb->ipsec_offload && sec_x && sec_x->dir == XFRM_SA_DIR_IN)
		goto no_sec_submit;

	if (skb->ipsec_offload || (sp && sp->len)) {
		if (!dpaa_submit_outb_pkt_to_SEC(skb, net_dev, priv->dpa_bp))
			percpu_priv->tx_caam_enc++;
		return NETDEV_TX_OK;
	}
#endif
#endif
no_sec_submit:
	if (priv->ceetm_en) {
		/* markval also using to know qosconnmark is present for this packet. */
		/* If markval is 0 assuming qosconnmark is not present for this packet(connection). */
		markval = pfe_eth_get_queuenum(skb);
		queuenum = markval & 0xf;
		channel_id = (markval >> CHANNEL_BIT_POSITION) & 0xf;
		egress_fq = NULL;
		get_ceetm_cm_egress_fq = 1; /* This becomes 0 when qosconnark(markval is 0) is not present or 
						can't find egress fq for dscp */
		/* markval is 0 means qosconnmark is not configured, so getting dscp from skb. */
		/* using dscp get the egress fq. */
		if (!markval) {
			skb_nh = dpa_get_skb_nh(skb, &l3_proto, &l3_offset);
			if (skb_nh)
			{
				/* skb_nh is non NULL means either it is pointing to ipv4 or ipv6 header only */
				if (l3_proto ==  htons(ETH_P_IP))
				{
					/* Getting the DSCP value from IPv4 header*/
					dscp = (((struct iphdr *)skb_nh)->tos) >> 2; /* tos 8 bit size, DSCP 6 bit value, shifting 2 bits right to get actual DSCP value*/
				}
				else
				{
					 /* Getting the DSCP value from IPv6 header*/
					dscp = ((((struct ipv6hdr *)skb_nh)->priority) << 2) /* priority is 4 bit size, shifting 2 bits left to give space for flow_lbl[0] 2 bits */
						+ ((((struct ipv6hdr *)skb_nh)->flow_lbl[0]) >> 6); /* flow_lbl[0] is 8 bit size, DSCP 2 bits are here, so shifting 6 bits right*/
				}
				egress_fq = ceetm_dscp_fqget_func(priv->qm_ctx, dscp);
				if (egress_fq)
					get_ceetm_cm_egress_fq = 0;
			}
		}
		if (get_ceetm_cm_egress_fq)
		{
			if (ceetm_fqget_func) {
				egress_fq = ceetm_fqget_func(priv->qm_ctx, 
					channel_id,queuenum,ff);
			}
			if (!egress_fq) {
				printk(KERN_CRIT "%s::unable to get ceetm fq, mark %08x registered func %p"
						"dropping packet\n",
						__FUNCTION__, markval , ceetm_fqget_func);
				dev_kfree_skb(skb);
				return NETDEV_TX_OK;
			}
		}
	}  else
	{
		queuenum = dpa_get_queue_mapping(skb);
		if (unlikely(queuenum >= DPAA_ETH_TX_QUEUES))
			queuenum = queuenum % DPAA_ETH_TX_QUEUES;
		egress_fq = priv->egress_fqs[queuenum];
	}
	conf_fq = priv->conf_fqs[queuenum  & (DPAA_ETH_TX_QUEUES-1)];
	return dpa_tx_extended(skb, net_dev, egress_fq, conf_fq);
}
#endif

#ifdef CONFIG_FSL_DPAA_ASK_CEETM_TX_OWNER
static void ask_ceetm_tx_drop(struct dpa_priv_s *priv, struct sk_buff *skb)
{
	struct dpa_percpu_priv_s *percpu_priv;

	percpu_priv = raw_cpu_ptr(priv->percpu_priv);
	percpu_priv->ask_ceetm_tx_drops++;
	percpu_priv->stats.tx_errors++;
	percpu_priv->stats.tx_dropped++;
	dev_kfree_skb(skb);
}

static int ask_ceetm_tx(struct sk_buff *skb, struct net_device *net_dev)
{
	struct dpa_priv_s *priv = netdev_priv(net_dev);
	struct qman_fq *egress_fq, *conf_fq;
	int class_queue = ASK_CEETM_DEFAULT_CLASS_QUEUE;
	int tx_queue;

	tx_queue = dpa_get_queue_mapping(skb);
	if (unlikely(tx_queue >= DPAA_ETH_TX_QUEUES))
		tx_queue = tx_queue % DPAA_ETH_TX_QUEUES;

	if (!priv->ceetm_en) {
		egress_fq = priv->egress_fqs[tx_queue];
		goto enqueue;
	}

	if (!ceetm_fqget_func || !priv->qm_ctx) {
		if (net_ratelimit())
			netdev_err(net_dev, "ASK CEETM TX owner missing FQ callback/context\n");
		ask_ceetm_tx_drop(priv, skb);
		return NETDEV_TX_OK;
	}

	/* Linux TX has no CDX QoS mark; use the default CEETM class queue. */
	egress_fq = ceetm_fqget_func(priv->qm_ctx, 0, class_queue, 0);
	if (!egress_fq) {
		if (net_ratelimit())
			netdev_err(net_dev, "ASK CEETM TX owner could not resolve class queue %d\n",
				   class_queue);
		ask_ceetm_tx_drop(priv, skb);
		return NETDEV_TX_OK;
	}

enqueue:
	conf_fq = priv->conf_fqs[tx_queue & (DPAA_ETH_TX_QUEUES - 1)];
	return dpa_tx_extended(skb, net_dev, egress_fq, conf_fq);
}
#endif

static inline void skb_reset_truesize(struct sk_buff *skb, unsigned int size)
{
	size -= SKB_DATA_ALIGN(sizeof(struct skb_shared_info));
	skb->truesize = SKB_TRUESIZE(size);

	return;
}

static inline void dpaa_skb_recycle(struct sk_buff *skb)
{
	struct skb_shared_info *shinfo;
	u8 head_frag = skb->head_frag;

	skb_release_head_state(skb);

	shinfo = skb_shinfo(skb);
	memset(shinfo, 0, offsetof(struct skb_shared_info, dataref));
	atomic_set(&shinfo->dataref, 1);

	memset(skb, 0, offsetof(struct sk_buff, tail));
	skb->data = skb->head + NET_SKB_PAD;
	skb->head_frag = head_frag;
	skb_reset_tail_pointer(skb);
}

int __hot dpa_tx(struct sk_buff *skb, struct net_device *net_dev)
{
	struct dpa_priv_s       *priv;
	int queue_mapping = dpa_get_queue_mapping(skb);
	struct qman_fq *egress_fq, *conf_fq;

	priv = netdev_priv(net_dev);

#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD)
	if (skb->ipsec_offload || skb_sec_path(skb)) {
		struct dpa_percpu_priv_s *percpu_priv =
			raw_cpu_ptr(priv->percpu_priv);

		percpu_priv->ipsec_tx_entry++;
	}

	if (skb->ipsec_offload) {
		struct dpa_percpu_priv_s *percpu_priv =
			raw_cpu_ptr(priv->percpu_priv);

		percpu_priv->ipsec_tx_direct++;
		if (!dpaa_submit_outb_pkt_to_SEC(skb, net_dev, priv->dpa_bp))
			percpu_priv->tx_caam_enc++;
		return NETDEV_TX_OK;
	}
#endif

#ifdef CONFIG_FSL_DPAA_HOOKS
	/* If there is a Tx hook, run it. */
	if (dpaa_eth_hooks.tx &&
		dpaa_eth_hooks.tx(skb, net_dev) == DPAA_ETH_STOLEN)
		/* won't update any Tx stats */
		return NETDEV_TX_OK;
#endif

#ifdef CONFIG_CPE_FAST_PATH
	return cpe_fp_tx(skb, net_dev);
#endif

#ifdef CONFIG_FSL_DPAA_ASK_CEETM_TX_OWNER
	if (priv->ceetm_en)
		return ask_ceetm_tx(skb, net_dev);
#endif

#ifdef CONFIG_FSL_DPAA_CEETM
	if (priv->ceetm_en)
		return ceetm_tx(skb, net_dev);
#endif

	if (unlikely(queue_mapping >= DPAA_ETH_TX_QUEUES))
		queue_mapping = queue_mapping % DPAA_ETH_TX_QUEUES;

	egress_fq = priv->egress_fqs[queue_mapping];
	conf_fq = priv->conf_fqs[queue_mapping];

	return dpa_tx_extended(skb, net_dev, egress_fq, conf_fq);
}

int __hot dpa_tx_extended(struct sk_buff *skb, struct net_device *net_dev,
		struct qman_fq *egress_fq, struct qman_fq *conf_fq)
{
	struct dpa_priv_s	*priv;
	struct qm_fd		 fd;
	struct dpa_percpu_priv_s *percpu_priv;
	struct rtnl_link_stats64 *percpu_stats;
	int err = 0;
	bool nonlinear, skb_changed, skb_need_wa;
	int *countptr, offset = 0;
	struct sk_buff *nskb;
	struct netdev_queue *txq;
	int txq_id = skb_get_queue_mapping(skb);

	/* Flags to help optimize the A050385 errata restriction checks.
	 *
	 * First flag marks if the skb changed between the first A050385 check
	 * and the moment it's converted to an FD.
	 *
	 * The second flag marks if the skb needs to be realigned in order to
	 * avoid the errata.
	 *
	 * The flags should have minimal impact on platforms not impacted by
	 * the errata.
	 */
	skb_changed = false;
	skb_need_wa = false;

	priv = netdev_priv(net_dev);
	/* Non-migratable context, safe to use raw_cpu_ptr */
	percpu_priv = raw_cpu_ptr(priv->percpu_priv);
	percpu_stats = &percpu_priv->stats;
	countptr = raw_cpu_ptr(priv->percpu_count);

	clear_fd(&fd);

#ifdef FM_ERRATUM_A050385
	if (unlikely(fm_has_errata_a050385()) && a050385_check_skb(skb, priv))
		skb_need_wa = true;
#endif

	nonlinear = skb_is_nonlinear(skb);

#ifdef CONFIG_FSL_DPAA_1588
	if (priv->tsu && priv->tsu->valid && priv->tsu->hwts_tx_en_ioctl)
		fd.cmd |= FM_FD_CMD_UPD;
#endif
#ifdef CONFIG_FSL_DPAA_TS
	if (unlikely(priv->ts_tx_en &&
			skb_shinfo(skb)->tx_flags & SKBTX_HW_TSTAMP))
		fd.cmd |= FM_FD_CMD_UPD;
	skb_shinfo(skb)->tx_flags |= SKBTX_IN_PROGRESS;
#endif /* CONFIG_FSL_DPAA_TS */


	/* MAX_SKB_FRAGS is larger than our DPA_SGT_MAX_ENTRIES; make sure
	 * we don't feed FMan with more fragments than it supports.
	 * Btw, we're using the first sgt entry to store the linear part of
	 * the skb, so we're one extra frag short.
	 */
	if (nonlinear && !skb_need_wa &&
	    likely(skb_shinfo(skb)->nr_frags < DPA_SGT_MAX_ENTRIES)) {
		/* Just create a S/G fd based on the skb */
		err = skb_to_sg_fd(priv, skb, &fd);
		percpu_priv->tx_frag_skbuffs++;
	} else {
		/* Make sure we have enough headroom to accommodate private
		 * data, parse results, etc. Normally this shouldn't happen if
		 * we're here via the standard kernel stack.
		 */
		if (unlikely(skb_headroom(skb) < priv->tx_headroom)) {
			struct sk_buff *skb_new;

			skb_new = skb_realloc_headroom(skb, priv->tx_headroom);
			if (unlikely(!skb_new)) {
				dev_kfree_skb(skb);
				percpu_stats->tx_errors++;
				return NETDEV_TX_OK;
			}

			/* propagate the skb ownership information */
			if (skb->sk)
				skb_set_owner_w(skb_new, skb->sk);

			dev_kfree_skb(skb);
			skb = skb_new;
			skb_changed = true;
		}

		/* We're going to store the skb backpointer at the beginning
		 * of the data buffer, so we need a privately owned skb
		 *
		 * Under the A050385 errata, we are going to have a privately
		 * owned skb after realigning the current one, so no point in
		 * copying it here in that case.
		 */

		/* Code borrowed from skb_unshare(). */
		if (skb_cloned(skb) && !skb_need_wa) {
			nskb = skb_copy(skb, GFP_ATOMIC);
			kfree_skb(skb);
			skb = nskb;
			skb_changed = true;

			/* skb_copy() has now linearized the skbuff. */
		} else if (unlikely(nonlinear) && !skb_need_wa) {
			/* We are here because the egress skb contains
			 * more fragments than we support. In this case,
			 * we have no choice but to linearize it ourselves.
			 */
#ifdef FM_ERRATUM_A050385
			/* No point in linearizing the skb now if we are going
			 * to realign and linearize it again further down due
			 * to the A050385 errata
			 */
			if (unlikely(fm_has_errata_a050385()))
				skb_need_wa = true;
			else
#endif
				err = __skb_linearize(skb);
		}
		if (unlikely(!skb || err < 0))
			/* Common out-of-memory error path */
			goto enomem;

#ifdef FM_ERRATUM_A050385
		/* Verify the skb a second time if it has been updated since
		 * the previous check
		 */
		if (unlikely(fm_has_errata_a050385()) && skb_changed &&
		    a050385_check_skb(skb, priv))
			skb_need_wa = true;

		if (unlikely(fm_has_errata_a050385()) && skb_need_wa) {
			nskb = a050385_realign_skb(skb, priv);
			if (!nskb)
				goto skb_to_fd_failed;
			dev_kfree_skb(skb);
			skb = nskb;
		}
#endif

		err = skb_to_contig_fd(priv, skb, &fd, countptr, &offset);
	}
	if (unlikely(err < 0))
		goto skb_to_fd_failed;

	if (fd.bpid != 0xff) {
		dpaa_skb_recycle(skb);
		/* dpaa_skb_recycle() reserves NET_SKB_PAD as skb headroom,
		 * but we need the skb to look as if returned by build_skb().
		 * We need to manually adjust the tailptr as well.
		 */
		/* skb_reset_truesize() resets the skb truesize, to avoid 
		 * skb truesize gets increment continuously when the same 
		 * skb used for next fragments.
		 */
		skb_reset_truesize(skb, SMP_CACHE_BYTES + DPA_SKB_SIZE(priv->dpa_bp->size) +
				SKB_DATA_ALIGN(sizeof(struct skb_shared_info)));
		skb->data = skb->head + offset;
		skb_reset_tail_pointer(skb);

		(*countptr)++;
		percpu_priv->tx_returned++;
	}
	if (unlikely(dpa_xmit(priv, percpu_stats, &fd, egress_fq, conf_fq) < 0))
		goto xmit_failed;

	/* LLTX forces us to update our own jiffies for each netdev queue.
	 * Use the queue mapping registered in the skb.
	 */
	txq = netdev_get_tx_queue(net_dev, txq_id);
	txq->trans_start = jiffies;
	return NETDEV_TX_OK;

xmit_failed:
	if (fd.bpid != 0xff) {
		(*countptr)--;
		percpu_priv->tx_returned--;
		dpa_fd_release(net_dev, &fd);
		percpu_stats->tx_errors++;
		return NETDEV_TX_OK;
	}
	_dpa_cleanup_tx_fd(priv, &fd);
skb_to_fd_failed:
enomem:
	percpu_stats->tx_errors++;
	dev_kfree_skb(skb);
	return NETDEV_TX_OK;
}
EXPORT_SYMBOL(dpa_tx_extended);
