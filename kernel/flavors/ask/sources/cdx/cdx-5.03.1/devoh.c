/*
 *  Copyright 2014-2016 Freescale Semiconductor, Inc.
 *  Copyright 2017,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 */
 
/**     
 * @file                devoh.c     
 * @description         device management routines offline ports.
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/gfp.h>
#include <linux/slab.h>
#include <linux/fsl_qman.h>
#include <linux/fsl_bman.h>
#include <linux/netdevice.h>
#include <linux/spinlock.h>
#include <linux/fsl_oh_port.h>
#include "dpaa_eth_common.h"
#include "dpaa_eth.h"
#include "portdefs.h"
#include "layer2.h"
#include "cdx_ioctl.h"
#include "misc.h"
#include "mac.h"
#include "cdx.h"
#include "cdx_common.h"
#include "fe.h"
#include "control_pppoe.h"
#include "control_tunnel.h"
#include "control_ipv6.h"
#include "procfs.h"

//#define DEVOH_DEBUG	1

//fq and table desc infor for all oh ports described
struct oh_port_info {
	char name[64];
	uint32_t fm_idx;
	uint32_t flags; //fqid valid and tdesc valid bits
	void *td[MAX_MATCH_TABLES];//td for tables attached to this port
	uint32_t channel;
	struct oh_iface_info *ohinfo; //iface info from config
	struct dpa_fq *rx_dpa_fq;
	struct dpa_fq *err_dpa_fq;
	qman_cb_dqrr defa_rx; // app callback func for default rx
	qman_cb_dqrr err_rx; // app callback func for rx err
};

struct oh_port_type {
	char *name;
	uint32_t type;
};

static struct oh_port_type ohport_assign[] = 
{
	{"dpa-fman0-oh@3", PORT_TYPE_WIFI},
	{"dpa-fman0-oh@2", PORT_TYPE_IPSEC},
};
#define MAX_OH_PORT_ASSIGN	(sizeof(ohport_assign) / sizeof(struct oh_port_type))

static struct oh_port_info offline_port_info[MAX_FRAME_MANAGERS][MAX_OF_PORTS];

extern struct dpa_iface_info *dpa_interface_info;
extern spinlock_t dpa_devlist_lock;
extern int FM_PORT_SetOhPortOfne(uint32_t fmidx, uint32_t portidx, uint32_t nia_val);
static enum qman_cb_dqrr_result ofport_rx_defa(struct qman_portal *portal, struct qman_fq *fq,
		const struct qm_dqrr_entry *dq);

static enum qman_cb_dqrr_result ofport_rx_err(struct qman_portal *portal, struct qman_fq *fq,
		const struct qm_dqrr_entry *dq);
extern int FM_PORT_SetOhPortRda(uint32_t fmidx, uint32_t portidx, uint32_t val);

static void ipsec_ofport_diag_dump_sg(const char *tag, int idx,
		const struct qm_sg_entry *sg)
{
	printk_ratelimited(KERN_INFO
		"IPSEC_OHDBG: %s[%d] ext=%u final=%u len=%u bpid=%u off=%u addr=0x%llx\n",
		tag, idx, qm_sg_entry_get_ext(sg), qm_sg_entry_get_final(sg),
		qm_sg_entry_get_len(sg), qm_sg_entry_get_bpid(sg),
		qm_sg_entry_get_offset(sg),
		(unsigned long long)qm_sg_addr(sg));
}

static void ipsec_ofport_diag_dump_fd(const char *stage, struct qman_fq *fq,
		const struct qm_fd *fd)
{
	const char *fmt = "contig";

	switch (fd->format) {
	case qm_fd_sg:
		fmt = "sg";
		break;
	case qm_fd_compound:
		fmt = "compound";
		break;
	default:
		break;
	}

	printk_ratelimited(KERN_INFO
		"IPSEC_OHDBG: stage=%s fqid=0x%x fmt=%s(%u) st=0x%08x len=%u off=%u bpid=%u addr=0x%llx\n",
		stage, fq->fqid, fmt, fd->format, fd->status, fd->length20,
		fd->offset, fd->bpid, (unsigned long long)qm_fd_addr(fd));

	if (fd->format == qm_fd_compound) {
		struct qm_sg_entry *sgt;

		sgt = (struct qm_sg_entry *)phys_to_virt((uint64_t)qm_fd_addr(fd));
		if (!sgt)
			return;
		ipsec_ofport_diag_dump_sg("compound", 0, &sgt[0]);
		ipsec_ofport_diag_dump_sg("compound", 1, &sgt[1]);
	} else if (fd->format == qm_fd_sg) {
		struct qm_sg_entry *sgt;

		sgt = (struct qm_sg_entry *)phys_to_virt((uint64_t)qm_fd_addr(fd));
		if (!sgt)
			return;
		ipsec_ofport_diag_dump_sg("sg", 0, &sgt[0]);
		ipsec_ofport_diag_dump_sg("sg", 1, &sgt[1]);
		ipsec_ofport_diag_dump_sg("sg", 2, &sgt[2]);
	}
}

static void ipsec_ofport_log_pkt_identity(const char *stage, uint32_t fqid,
		const struct qm_fd *fd)
{
	const uint8_t *pkt;
	uint32_t len;
	uint32_t l3 = ETH_HLEN;
	uint16_t eth;
	uint16_t ppp_proto = 0;
	uint8_t ipver = 0;
	uint8_t proto = 0;
	uint16_t ipid = 0;
	uint32_t esp_spi = 0;
	uint32_t esp_seq = 0;
	uint16_t icmp_seq = 0;
	bool have_icmp_seq = false;
	bool have_esp = false;
	uint8_t ihl;

	if (!fd || fd->format != qm_fd_contig || !qm_fd_addr(fd) || !fd->length20)
		return;

	len = fd->length20;
	pkt = (const uint8_t *)(phys_to_virt((uint64_t)qm_fd_addr(fd)) +
			fd->offset);
	if (len < ETH_HLEN)
		return;

	eth = ((uint16_t)pkt[12] << 8) | pkt[13];

	while ((eth == 0x8100 || eth == 0x88a8) && (len >= (l3 + 4))) {
		eth = ((uint16_t)pkt[l3 + 2] << 8) | pkt[l3 + 3];
		l3 += 4;
	}

	if ((eth == 0x8864) && (len >= (l3 + 8))) {
		ppp_proto = ((uint16_t)pkt[l3 + 6] << 8) | pkt[l3 + 7];
		l3 += 8;
		if (ppp_proto == 0x0021)
			eth = 0x0800;
		else if (ppp_proto == 0x0057)
			eth = 0x86dd;
	}

	if ((eth == 0x0800) && (len >= (l3 + 20))) {
		ipver = pkt[l3] >> 4;
		ihl = (pkt[l3] & 0x0f) << 2;
		if ((ihl >= 20) && (len >= (l3 + ihl))) {
			proto = pkt[l3 + 9];
			ipid = ((uint16_t)pkt[l3 + 4] << 8) | pkt[l3 + 5];
			if ((proto == IPPROTO_ICMP) && (len >= (l3 + ihl + 8))) {
				icmp_seq = ((uint16_t)pkt[l3 + ihl + 6] << 8) |
						pkt[l3 + ihl + 7];
				have_icmp_seq = true;
			} else if ((proto == IPPROTO_ESP) &&
					(len >= (l3 + ihl + 8))) {
				esp_spi = ((uint32_t)pkt[l3 + ihl] << 24) |
					((uint32_t)pkt[l3 + ihl + 1] << 16) |
					((uint32_t)pkt[l3 + ihl + 2] << 8) |
					pkt[l3 + ihl + 3];
				esp_seq = ((uint32_t)pkt[l3 + ihl + 4] << 24) |
					((uint32_t)pkt[l3 + ihl + 5] << 16) |
					((uint32_t)pkt[l3 + ihl + 6] << 8) |
					pkt[l3 + ihl + 7];
				have_esp = true;
			}
		}
	}

	printk_ratelimited(KERN_INFO
		"IPSEC_OHDBG: stage=%s fqid=0x%x len=%u l3=%u eth=0x%04x ppp=0x%04x ipver=%u proto=%u id=0x%04x icmp_seq=%u esp_spi=0x%08x esp_seq=%u flags[i=%u e=%u]\n",
		stage, fqid, len, l3, eth, ppp_proto, ipver, proto, ipid,
		icmp_seq, esp_spi, esp_seq, have_icmp_seq, have_esp);
	print_hex_dump(KERN_INFO, "IPSEC_OHHEX: ", DUMP_PREFIX_OFFSET, 16, 1,
			pkt, len > 64 ? 64 : len, false);
}

static void ipsec_ofport_log_buf_views(const char *stage, uint32_t fqid,
		const struct qm_fd *fd)
{
	const uint8_t *base;
	const uint8_t *pkt;
	uint32_t ann_len;
	uint32_t pkt_len;

	if (!fd || fd->format != qm_fd_contig || !qm_fd_addr(fd))
		return;

	base = (const uint8_t *)phys_to_virt((uint64_t)qm_fd_addr(fd));
	pkt = base + fd->offset;
	ann_len = fd->offset > 64 ? 64 : fd->offset;
	pkt_len = fd->length20 > 64 ? 64 : fd->length20;

	printk_ratelimited(KERN_INFO
		"IPSEC_OHDBG: stage=%s fqid=0x%x base=0x%llx off=%u ann_len=%u pkt_len=%u\n",
		stage, fqid, (unsigned long long)qm_fd_addr(fd), fd->offset,
		ann_len, pkt_len);
	if (ann_len)
		print_hex_dump(KERN_INFO, "IPSEC_OHANN: ",
				DUMP_PREFIX_OFFSET, 16, 1, base, ann_len, false);
	if (pkt_len)
		print_hex_dump(KERN_INFO, "IPSEC_OHPKT: ",
				DUMP_PREFIX_OFFSET, 16, 1, pkt, pkt_len, false);
}

void *  get_oh_port_td(uint32_t fm_index, uint32_t port_idx, uint32_t type)
{

#ifdef DEVOH_DEBUG
	DPA_INFO("%s:get td idx %d for fman %d, port %d\n",
			__FUNCTION__, offline_port_info[fm_index][port_idx].td[type], fm_index, port_idx);
#endif
	return offline_port_info[fm_index][port_idx].td[type] ;

}


int get_ofport_fman_and_portindex(uint32_t fm_index, uint32_t handle, uint32_t* fm_idx, uint32_t* port_idx,
		uint32_t *portid)
{
	struct oh_port_info *info;
	info = &offline_port_info[fm_index][handle];

	*fm_idx = info->ohinfo->fman_idx;	
	*port_idx = info->ohinfo->port_idx;
	*portid = info->ohinfo->portid;

	return 0;
}

int get_ofport_portid(uint32_t fm_idx, uint32_t handle, uint32_t *portid)
{
	struct oh_port_info *info;

	if (fm_idx >= MAX_FRAME_MANAGERS) {
		DPA_ERROR("%s::invalid fman index\n", __FUNCTION__);
		return -1;	
	}
	if (handle >= MAX_OF_PORTS) {
		DPA_ERROR("%s::invalid ofport handle %d\n",
				__FUNCTION__, handle);
		return -1;	
	}
	info = &offline_port_info[fm_idx][handle];
	*portid = info->ohinfo->portid;
	return 0;
}

int get_ofport_info(uint32_t fm_idx, uint32_t handle, uint32_t *channel, void **td )
{
	struct oh_port_info *info;

	if (fm_idx >= MAX_FRAME_MANAGERS) {
		DPA_ERROR("%s::invalid fman index\n", __FUNCTION__);
		return -1;	
	}
	if (handle >= MAX_OF_PORTS) {
		DPA_ERROR("%s::invalid ofport handle %d\n",
				__FUNCTION__, handle);
		return -1;	
	}
	info = &offline_port_info[fm_idx][handle];
	if (info->flags & IN_USE) {
		uint32_t ii;

		*channel = info->channel;
		get_tableInfo_by_portid(fm_idx, info->ohinfo->portid, info->td, &info->flags); 
		for (ii = 0; ii < MAX_MATCH_TABLES; ii++) {
			if (info->flags & (1 << ii))
				*(td + ii) = info->td[ii];
			else
				*(td + ii) = NULL;
		}
		return 0;
	}
	DPA_ERROR("%s::ofport handle %d not in use\n",
			__FUNCTION__, handle);
	return -1;
}
/* This function returns max distributions configured for an OH port */ 
int get_ofport_max_dist(uint32_t fm_idx, uint32_t handle, uint32_t* max_dist)
{
	struct oh_port_info *info;

	if (handle >= MAX_OF_PORTS)
		return -1;

	info = &offline_port_info[fm_idx][handle];


	*max_dist = info->ohinfo->max_dist;
	return 0;
}

int alloc_offline_port(uint32_t fm_idx, uint32_t type, qman_cb_dqrr defa_rx, qman_cb_dqrr err_rx)
{
	uint32_t ii;
	struct oh_port_info *info;

	if (fm_idx >= MAX_FRAME_MANAGERS) {
		DPA_ERROR("%s::invalid fman index\n", __FUNCTION__);
		return -1;	
	}
	type &= PORT_TYPE_MASK;
	for (ii = 0; ii < MAX_OF_PORTS; ii++) {
		info = &offline_port_info[fm_idx][ii];
		if (info->flags & PORT_VALID) {
			if ((info->flags & PORT_TYPE_MASK) == type) {
				info->flags |= IN_USE;
				info->defa_rx = defa_rx;
				info->err_rx = err_rx;

				if (defa_rx)
				{
					info->rx_dpa_fq->fq_base.cb.dqrr = defa_rx;
				}
				if (err_rx)
				{
					info->err_dpa_fq->fq_base.cb.dqrr = err_rx;
				}
				return ii;
			}
		}
	}
	DPA_ERROR("%s::no free of ports\n", __FUNCTION__);
	return -1;
}

int release_offline_port(uint32_t fm_idx, int handle)
{
	struct oh_port_info *info;

	if (fm_idx >= MAX_FRAME_MANAGERS) {
		DPA_ERROR("%s::invalid fman index\n", __FUNCTION__);
		return -1;
	}
	if (handle >= MAX_OF_PORTS) {
		DPA_ERROR("%s::invalid port index\n", __FUNCTION__);
		return -1;
	}
	info = &offline_port_info[fm_idx][handle];
	if (info->flags & IN_USE) {
		info->flags &= ~IN_USE;
		info->defa_rx = NULL;
		info->err_rx = NULL;
		info->rx_dpa_fq->fq_base.cb.dqrr = ofport_rx_defa;
		info->err_dpa_fq->fq_base.cb.dqrr = ofport_rx_err;
		return 0;
	}	
	DPA_ERROR("%s::port was not in use\n", __FUNCTION__);
	return -1;
}

#ifdef DEVOH_DEBUG
static void display_of_port_info(void) DPA_UNUSED;
static void display_of_port_info(void)
{
	uint32_t ii;
	uint32_t jj;
	struct oh_port_info *info;

	DPA_INFO("===================================================\n"
			"of port info \n"); 
	for (jj = 0; jj < MAX_FRAME_MANAGERS; jj++) {
		DPA_INFO("fm	\t%d\nmax of ports	\t%d\n", 
				jj, MAX_OF_PORTS);
		for (ii = 0; ii < MAX_OF_PORTS; ii++) {
			int kk;
			info = &offline_port_info[jj][ii];
			if (info->flags & PORT_VALID) {
				DPA_INFO("fman %d of port\t%d\n", jj, ii);
				DPA_INFO("defarx_fqid 	\t%x\n",
						info->ohinfo->fqinfo[RX_DEFA_FQ].fq_base);
				DPA_INFO("errrx_fqid 	\t%x\n",
						info->ohinfo->fqinfo[RX_ERR_FQ].fq_base);
				for( kk = 0; kk < MAX_MATCH_TABLES; kk++) {
					if (info->flags & (1 << kk))
						DPA_INFO("td for type %d	\t%d\n", 
								kk, info->td[kk]);
				}
				if (info->flags & IN_USE) 
					DPA_INFO("in use		\t\n");
			}
		}
	}
}
#else
#define display_of_port_info()
#endif

void display_ohport_info(struct oh_iface_info *ohinfo)
{
#ifdef DEVOH_DEBUG
	uint32_t ii;

	DPA_INFO("fman_idx      \t%d\n", ohinfo->fman_idx);
	DPA_INFO("port_idx      \t%d\n", ohinfo->port_idx);
	DPA_INFO("channel_id    \t%d\n", ohinfo->channel_id);
	for (ii = 0; ii < MAX_FQ_TYPES; ii++) {
		switch(ii) {
			case TX_ERR_FQ:
				if (ohinfo->fqinfo[ii].num_fqs)
					DPA_INFO("TX_ERR_FQ     \t0x%x\n", ohinfo->fqinfo[ii].fq_base);
				break;
			case TX_CFM_FQ:
				if (ohinfo->fqinfo[ii].num_fqs)
					DPA_INFO("TX_CFM_FQ     \t0x%x\n", ohinfo->fqinfo[ii].fq_base);
				break;
			case RX_ERR_FQ:
				if (ohinfo->fqinfo[ii].num_fqs)
					DPA_INFO("RX_ERR_FQ     \t0x%x\n", ohinfo->fqinfo[ii].fq_base);
				break;
			case RX_DEFA_FQ:
				if (ohinfo->fqinfo[ii].num_fqs)
					DPA_INFO("RX_DEFA_FQ    \t0x%x\n", ohinfo->fqinfo[ii].fq_base);
				break;
		}
	}
	DPA_INFO("max_dist      \t%d\n", ohinfo->max_dist);
	if (ohinfo->max_dist) {
		struct cdx_dist_info *dist_info;
		DPA_INFO("PCD Fqs\n");
		dist_info = ohinfo->dist_info;
		for (ii = 0; ii < ohinfo->max_dist; ii++) {
			printk("fq_base         \t0x%x\n", dist_info->base_fqid);
			printk("fq_count        \t%d\n", dist_info->count);
			printk("dist_type       \t%d\n", dist_info->type);
			dist_info++;
		}
	}
#endif
}

int dpa_add_oh_if(char *name)
{
	struct dpa_iface_info *iface_info;
	struct fman_offline_port_info info;
	uint32_t fman_idx;
	uint32_t port_idx;
	uint8_t oh_iface_name[8]="";

#if 0//def DEVOH_DEBUG
	DPA_INFO("%s::ADDING OHPORT INFO for %s\n", __func__, name);
#endif

	if (sscanf(name, "dpa-fman%d-oh@%d", &fman_idx,
				&port_idx) != 2) {
		DPA_ERROR("%s::invalid name %s\n", __FUNCTION__, name);
		return FAILURE;
	}
	strncpy(&info.port_name[0], name, IF_NAME_SIZE);
	info.port_name[IF_NAME_SIZE - 1] = '\0';

	if (sprintf(oh_iface_name, "oh%d", port_idx-1) < 0) {
		DPA_ERROR("%s::invalid port_idx %u\n", __FUNCTION__, port_idx);
		return FAILURE;
	}

	if (oh_port_driver_get_port_info(&info)) {
		DPA_ERROR("%s::oh_port_driver_get_port_info failed\n", __FUNCTION__);
		return FAILURE;
	}
	//ethernet/physical iface type
	iface_info = (struct dpa_iface_info *)
		kzalloc(sizeof(struct dpa_iface_info), GFP_KERNEL);
	if (!iface_info) {
		DPA_ERROR("%s::no mem for eth dev info size %d\n",
				__FUNCTION__,
				(uint32_t)sizeof(struct dpa_iface_info));
		return FAILURE;
	}
	memset(iface_info, 0, sizeof(struct dpa_iface_info));
	strncpy(&iface_info->name[0], name, IF_NAME_SIZE);
	iface_info->name[IF_NAME_SIZE - 1] = '\0';

	iface_info->if_flags = IF_TYPE_OFPORT;	
	iface_info->oh_info.channel_id = info.channel_id;
	iface_info->oh_info.fman_idx = fman_idx;
	iface_info->oh_info.port_idx = (port_idx - 1);
	iface_info->oh_info.fqinfo[RX_ERR_FQ].fq_base = info.err_fqid;
	iface_info->oh_info.fqinfo[RX_ERR_FQ].num_fqs = 1;
	iface_info->oh_info.fqinfo[RX_DEFA_FQ].fq_base = info.default_fqid;
	iface_info->oh_info.fqinfo[RX_DEFA_FQ].num_fqs = 1;
	//get info from config
	if (get_dpa_oh_iface_info(&iface_info->oh_info, name)) {
		DPA_ERROR("%s::get_dpa_oh_iface_info failed %s\n",
				__FUNCTION__, name);
		goto err_ret;
	}
	if (cdx_create_dir_in_procfs(&iface_info->pcd_proc_entry, oh_iface_name, PCD_DIR)) {
		DPA_ERROR("%s:: create pcd proc entry failed %s\n", 
				__FUNCTION__, name);
		goto err_ret;
	}

	if (cdx_create_dir_in_procfs(&iface_info->rx_proc_entry, oh_iface_name, RX_DIR)) {
		DPA_ERROR("%s:: create pcd proc entry failed %s\n", 
				__FUNCTION__, name);
		goto err_ret;
	}

	if (cdx_create_dir_in_procfs(&iface_info->tx_proc_entry, oh_iface_name, TX_DIR)) {
		DPA_ERROR("%s:: create pcd proc entry failed %s\n", 
				__FUNCTION__, name);
		goto err_ret;
	}

	//add to list
	if (dpa_add_port_to_list(iface_info)) {
		DPA_ERROR("%s::dpa_add_port_to_list failed\n", 
				__FUNCTION__); 
		goto err_ret;
	}
#ifdef DEVOH_DEBUG
	display_iface_info(iface_info);
#endif
	return SUCCESS;
err_ret:
	kfree(iface_info);
	return FAILURE;
}

int get_oh_port_pcd_fqinfo(uint32_t fm_idx, uint32_t handle, uint32_t type,
		uint32_t *pfqid, uint32_t *count) 
{
	uint32_t ii;
	struct oh_iface_info *iface_info;
	struct cdx_dist_info *dist;
	struct oh_port_info *info;

	if (fm_idx >= MAX_FRAME_MANAGERS) {
		DPA_ERROR("%s::invalid fman index\n", __FUNCTION__);
		return -1;
	}
	if (handle >= MAX_OF_PORTS) {
		DPA_ERROR("%s::invalid ofport handle %d\n",
				__FUNCTION__, handle);
		return -1;
	}
	info = &offline_port_info[fm_idx][handle];
	if (!(info->flags & IN_USE)) {
		DPA_ERROR("%s::ofport handle %d not in use\n",
				__FUNCTION__, handle);
		return -1;
	}
	iface_info = info->ohinfo;	
	dist = iface_info->dist_info;
	for (ii = 0; ii < iface_info->max_dist; ii++) {
		if (dist->type == type) {
			*pfqid = dist->base_fqid;
			*count = dist->count;
		}
		dist++;
	}
	return 0;
}


static enum qman_cb_dqrr_result ofport_rx_defa(struct qman_portal *portal, struct qman_fq *fq,
		const struct qm_dqrr_entry *dq)
{

	const struct qm_fd *fd;
	uint8_t *ptr;
	uint32_t len;

	len = dq->fd.length20;

	fd = &dq->fd;
	ipsec_ofport_diag_dump_fd("defa", fq, fd);
	ipsec_ofport_log_pkt_identity("defa-pkt", fq->fqid, fd);
	ipsec_ofport_log_buf_views("defa-buf", fq->fqid, fd);
	printk("%s::fqid %x(%d), bpid %d, len %d, offset %d  addr %llx status: %x\n", __FUNCTION__,
			dq->fqid, dq->fqid, dq->fd.bpid, dq->fd.length20,
			dq->fd.offset, (uint64_t)dq->fd.addr, dq->fd.status);
	if(len)
	{	
		ptr = (uint8_t *)(phys_to_virt((uint64_t)dq->fd.addr));
		printk("Dispalying parse result:\n");
		display_buff_data(ptr, 0x70);
		ptr = (uint8_t *)(phys_to_virt((uint64_t)dq->fd.addr) + dq->fd.offset);
		printk("Displaying the packet: \n");
		display_buff_data(ptr, len);
	}	
	if (dq->fd.bpid) {
		if (fd->format != qm_fd_sg) {
			struct bm_buffer bmb;
			struct dpa_bp *dpa_bp;
			dpa_bp = dpa_bpid2pool(fd->bpid);
			if (dpa_bp) {
				printk_ratelimited(KERN_INFO
					"IPSEC_OHDBG: stage=defa-release fqid=0x%x bpid=%u addr=0x%llx\n",
					fq->fqid, fd->bpid,
					(unsigned long long)fd->addr);
				printk(KERN_CRIT "%s::releasing buffer to pool %d\n", 
						__FUNCTION__, fd->bpid);
				memset(&bmb, 0, sizeof(struct bm_buffer));
				bm_buffer_set64(&bmb, dq->fd.addr);
				while (bman_release(dpa_bp->pool, &bmb, 1, 0))
					cpu_relax();
			} else {
				/* Pool not registered (e.g. SEC output pool).
				 * Release by bpid directly, matching the
				 * fallback in dpa_fd_release(). */
				printk_ratelimited(KERN_INFO
					"IPSEC_OHDBG: stage=defa-release-by-bpid fqid=0x%x bpid=%u addr=0x%llx\n",
					fq->fqid, fd->bpid,
					(unsigned long long)fd->addr);
				memset(&bmb, 0, sizeof(struct bm_buffer));
				bm_buffer_set64(&bmb, dq->fd.addr);
				while (bman_release_by_bpid(fd->bpid, &bmb, 1))
					cpu_relax();
			}
		} else {
			printk(KERN_CRIT "%s::cannot handle sg buffers now\n", __FUNCTION__);
		}
	}
	return qman_cb_dqrr_consume;
}

static enum qman_cb_dqrr_result ofport_rx_err(struct qman_portal *portal, struct qman_fq *fq,
		const struct qm_dqrr_entry *dq) 
{
	const struct qm_fd *fd;
	struct dpa_bp *dpa_bp;
	struct bm_buffer bmb;
	uint8_t *ptr;
	uint32_t len;

	len = dq->fd.length20;
	fd = &dq->fd;
	ipsec_ofport_diag_dump_fd("err", fq, fd);
	ipsec_ofport_log_pkt_identity("err-pkt", fq->fqid, fd);
	ipsec_ofport_log_buf_views("err-buf", fq->fqid, fd);
	printk("%s::fqid %x(%d), bpid %d status %08x, len %d(0x%x), format %s\n", __FUNCTION__,
			fq->fqid, fq->fqid, fd->bpid, fd->status,len,len,
			(fd->format == qm_fd_sg) ? "SGlist" : "simple");
	if(len)	
	{	
		ptr = (uint8_t *)(phys_to_virt((uint64_t)dq->fd.addr));
		printk("Dispalying parse result:\n");
		display_buff_data(ptr, 0x70);
		if (fd->format != qm_fd_sg)
		{
			ptr = (uint8_t *)(phys_to_virt((uint64_t)dq->fd.addr) + dq->fd.offset);
			printk("Displaying the packet: \n");
			display_buff_data(ptr, len);
		}
		else
		{
			printk("have to print data in SG case\n");
		}
	}	
	if (dq->fd.bpid) {
		if (fd->format != qm_fd_sg) {
			dpa_bp = dpa_bpid2pool(fd->bpid);
			if (dpa_bp) {
				printk(KERN_CRIT "%s::releasing buffer to pool %d\n", 
						__FUNCTION__, fd->bpid);
				memset(&bmb, 0, sizeof(struct bm_buffer));
				bm_buffer_set64(&bmb, dq->fd.addr);
				while (bman_release(dpa_bp->pool, &bmb, 1, 0))
					cpu_relax();
			} else {
				/* Pool not registered (e.g. SEC output pool).
				 * Release by bpid directly, matching the
				 * fallback in dpa_fd_release(). */
				printk_ratelimited(KERN_INFO
					"IPSEC_OHDBG: stage=err-release-by-bpid fqid=0x%x bpid=%u addr=0x%llx\n",
					fq->fqid, fd->bpid,
					(unsigned long long)fd->addr);
				memset(&bmb, 0, sizeof(struct bm_buffer));
				bm_buffer_set64(&bmb, dq->fd.addr);
				while (bman_release_by_bpid(fd->bpid, &bmb, 1))
					cpu_relax();
			}
		} else {
			printk(KERN_CRIT "%s::freeing sg buffers now\n", __FUNCTION__);
			dpa_fd_release(NULL, fd);
		}
	}
	return qman_cb_dqrr_consume;

}

//routine to create all FQs required by distribution in xml file
int cdxdrv_create_of_fqs(struct dpa_iface_info *dpa_oh_iface_info)
{
	uint32_t ii;
	struct dpa_fq *dpa_fq;
	struct oh_port_info *port_info;
	struct oh_iface_info *iface_info = &(dpa_oh_iface_info->oh_info);

	port_info = &offline_port_info[iface_info->fman_idx][iface_info->port_idx];
	//create default FQ, err FQ
	for (ii = 0; ii < 2; ii++) {
		struct qman_fq *fq;

		dpa_fq = kzalloc(sizeof(struct dpa_fq), GFP_KERNEL);
		if (!dpa_fq) {
			DPA_ERROR("%s::unable to alloc mem for defa or err fqid\n",
					__FUNCTION__);
			port_info->rx_dpa_fq = NULL;
			port_info->err_dpa_fq = NULL;
			return -1;
		}
		memset(dpa_fq, 0, sizeof(struct dpa_fq));
		//use channel and wq the same as any other ethernet port
		if (cdx_copy_eth_rx_channel_info(iface_info->fman_idx, dpa_fq)) {
			DPA_ERROR("%s::cdx_copy_eth_rx_channel_info failed\n",
					__FUNCTION__);
			port_info->rx_dpa_fq = NULL;
			port_info->err_dpa_fq = NULL;
			kfree(dpa_fq);
			return -1;
		}
		//get the fqid from dts copied value
		//set callback functions and que type
		fq = &dpa_fq->fq_base;
		if (!ii) {
			dpa_fq->fqid = iface_info->fqinfo[RX_DEFA_FQ].fq_base;
			dpa_fq->fq_type = FQ_TYPE_RX_DEFAULT;
			fq->cb.dqrr = ofport_rx_defa;
			port_info->rx_dpa_fq = dpa_fq;
		} else {
			dpa_fq->fqid = iface_info->fqinfo[RX_ERR_FQ].fq_base;
			dpa_fq->fq_type = FQ_TYPE_RX_ERROR;
			port_info->err_dpa_fq = dpa_fq;
			fq->cb.dqrr = ofport_rx_err;
		}
		//create FQ
		if (cdx_create_fq(dpa_fq, 0, dpa_oh_iface_info->pcd_proc_entry)) {
			DPA_ERROR("%s::cdx_create_fq failed for fqid %d\n",
					__FUNCTION__, dpa_fq->fqid);
			port_info->rx_dpa_fq = NULL;
			port_info->err_dpa_fq = NULL;
			kfree(dpa_fq);
			return -1;
		}
		add_pcd_fq_info(dpa_fq);
#ifdef DEVOH_DEBUG
		DPA_INFO("%s::%d, fqid 0x%x created chnl 0x%x\n",
				__FUNCTION__, ii, dpa_fq->fqid, dpa_fq->channel);
#endif
	}
	//add fqid information into of port list
	port_info->fm_idx = iface_info->fman_idx; 		
	port_info->ohinfo = iface_info; 		
	port_info->channel = iface_info->channel_id;
	//save name
	sprintf(&port_info->name[0], 
			"dpa-fman%d-oh@%d", iface_info->fman_idx, (iface_info->port_idx + 1));
	//assign port to Wifi/ipsec etc based on user config
	for (ii = 0; ii < MAX_OH_PORT_ASSIGN; ii++) {
		if (strcmp(ohport_assign[ii].name, &port_info->name[0]) == 0) {
			port_info->flags |= ohport_assign[ii].type;
#ifdef DEVOH_DEBUG
			DPA_INFO("%s::port %s, type %x\n", __FUNCTION__,
					port_info->name, ohport_assign[ii].type);
#endif
			break;
		}
	} 		
	offline_port_info[iface_info->fman_idx][iface_info->port_idx].flags |=
		(OF_FQID_VALID | PORT_VALID);
	return 0;
}

int ohport_set_ofne(uint32_t handle, uint32_t nia_val)
{
	uint32_t fm_idx;
	uint32_t port_idx;
	uint32_t portid;

	if (get_ofport_fman_and_portindex(0, handle, &fm_idx, &port_idx, &portid))
		return -1;
	if (FM_PORT_SetOhPortOfne(fm_idx, port_idx, nia_val))
		return -1;
	return 0;
}

int ohport_set_dma(uint32_t handle, uint32_t val)
{
	uint32_t fm_idx;
	uint32_t port_idx;
	uint32_t portid;

	if (get_ofport_fman_and_portindex(0, handle, &fm_idx, &port_idx, &portid))
		return -1;
	if (FM_PORT_SetOhPortRda(fm_idx, port_idx, val))
		return -1;
	return 0;
}

int dpaa_is_oh_port(uint32_t portid)
{
	uint32_t fm_idx = 0,ii;
	struct oh_port_info *info;

	for (ii = 0; ii < MAX_OF_PORTS; ii++) {
		info = &offline_port_info[fm_idx][ii];
		if ((info->flags & PORT_VALID) && (info->flags & IN_USE)) {
			if(info->ohinfo->portid == portid)
				return 1;
		}
	}
	return 0;
}
