/*
 * Keep the exact Stage 2 wired class buildable without pulling in deferred
 * WiFi, IPsec, or CEETM ownership from the broader vendor stack.
 */

#include <linux/kernel.h>
#include <linux/string.h>

#include <dpaa_eth.h>
#include <dpaa_eth_common.h>
#include <mac.h>

#include "misc.h"
#include "cdx.h"
#include "control_ipv4.h"
#include "portdefs.h"
#include "module_qm.h"

struct dpa_bp;
int dpaa_get_vap_fwd_fq(uint16_t vap_id, uint32_t *fqid, uint32_t hash);
int dpaa_get_wifi_dev(uint16_t vap_id, void **netdev);
int dpaa_get_wifi_ohport_handle(uint32_t *oh_handle);
int vwd_is_no_l2_itf_device(struct net_device *dev);
void drain_tx_bp_pool(struct dpa_bp *bp);
void *M_ipsec_sa_cache_lookup_by_h(U16 handle);

#if !defined(ENABLE_EGRESS_QOS)
struct qman_fq *cdx_get_txfq(struct eth_iface_info *eth_info, void *info)
{
	union ctentry_qosmark *qosmark = (union ctentry_qosmark *)info;
	uint32_t quenum;

	if (!eth_info || !qosmark)
		return NULL;

	quenum = qosmark->queue & (DPAA_FWD_TX_QUEUES - 1);
	return &eth_info->fwd_tx_fqinfo[quenum];
}

int cdx_get_tx_dscp_fq_map(struct eth_iface_info *eth_info,
		uint8_t *is_dscp_fq_map, void *info)
{
	(void)eth_info;
	(void)info;

	if (is_dscp_fq_map)
		*is_dscp_fq_map = 0;

	return SUCCESS;
}

int reset_all_dscp_fq_map_ff(cdx_dscp_fqid_t *muram_dscp_fqid_map)
{
	if (!muram_dscp_fqid_map)
		return FAILURE;

	memset(muram_dscp_fqid_map, 0, sizeof(*muram_dscp_fqid_map));
	return SUCCESS;
}

int reset_dscp_fq_map_ff(cdx_dscp_fqid_t *muram_dscp_fqid_map, uint8_t dscp)
{
	if (!muram_dscp_fqid_map || dscp >= MAX_DSCP)
		return FAILURE;

	muram_dscp_fqid_map->fqid[dscp] = 0;
	return SUCCESS;
}
#endif

#if !defined(WIFI_ENABLE)
int dpaa_get_vap_fwd_fq(uint16_t vap_id, uint32_t *fqid, uint32_t hash)
{
	(void)vap_id;
	(void)hash;

	if (fqid)
		*fqid = 0;

	return FAILURE;
}

int dpaa_get_wifi_dev(uint16_t vap_id, void **netdev)
{
	(void)vap_id;
	if (netdev)
		*netdev = NULL;
	return FAILURE;
}

int dpaa_get_wifi_ohport_handle(uint32_t *oh_handle)
{
	if (oh_handle)
		*oh_handle = 0;
	return FAILURE;
}

int vwd_is_no_l2_itf_device(struct net_device *dev)
{
	(void)dev;
	return 0;
}

void drain_tx_bp_pool(struct dpa_bp *bp)
{
	(void)bp;
}
#endif

#if !defined(DPA_IPSEC_OFFLOAD)
void *M_ipsec_sa_cache_lookup_by_h(U16 handle)
{
	(void)handle;
	return NULL;
}
#endif
