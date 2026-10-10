// SPDX-License-Identifier: GPL-2.0+
/* Exercise the loaded driver's real receive callback with private buffers.
 * An invalid EasyMesh station drops after skb detachment, before delivery.
 * No radio, live interface, firmware state or packet filter is changed. */
#include <linux/module.h>
#include <linux/kprobes.h>
#include "mlinux/moal_main.h"

static int __init rx_test_init(void)
{
	struct kprobe probe = { .symbol_name = "moal_recv_packet" };
	mlan_status (*receive)(t_void *, pmlan_buffer);
	moal_handle *handle = NULL;
	moal_private *priv = NULL;
	int err = register_kprobe(&probe);
	unsigned int i;

	if (err)
		return err;
	receive = (void *)probe.addr;
	unregister_kprobe(&probe);
	err = -ENOMEM;
	handle = kvzalloc(sizeof(*handle), GFP_KERNEL);
	priv = kzalloc(sizeof(*priv), GFP_KERNEL);
	if (!handle || !priv)
		goto out;
	priv->vlan_sta_list[0] = kzalloc(sizeof(*priv->vlan_sta_list[0]), GFP_KERNEL);
	if (!priv->vlan_sta_list[0])
		goto out;
	handle->priv[0] = priv;
	priv->phandle = handle;
	for (i = 0; i < 64; i++) {
		struct sk_buff *skb = alloc_skb(sizeof(mlan_buffer) + 64, GFP_KERNEL);
		mlan_buffer *buf;
		mlan_status status;

		if (!skb)
			goto out;
		skb_reserve(skb, sizeof(mlan_buffer));
		buf = (void *)skb->head;
		memset(buf, 0, sizeof(*buf));
		buf->pdesc = skb;
		buf->pbuf = skb->data;
		buf->data_len = ETH_HLEN + 20;
		buf->flags = MLAN_BUF_FLAG_EASYMESH;
		buf->priority = 1U << 24; /* station 1 exists but is not valid */
		memset(skb->data, 0, buf->data_len);
		((struct ethhdr *)skb->data)->h_proto = htons(ETH_P_IP);
		atomic_inc(&handle->mbufalloc_count);
		status = receive(handle, buf);
		/* The callback consumed both skb and buf; never touch either. */
		if (status != MLAN_STATUS_PENDING || atomic_read(&handle->mbufalloc_count) ||
		    priv->stats.rx_dropped != i + 1) {
			err = -EINVAL;
			goto out;
		}
	}
	err = 0;
	pr_info("mwifiex RX cleanup: 64 detached EasyMesh drops passed\n");
out:
	if (priv)
		kfree(priv->vlan_sta_list[0]);
	kfree(priv);
	kvfree(handle);
	return err;
}

static void __exit rx_test_exit(void) {}
module_init(rx_test_init);
module_exit(rx_test_exit);
MODULE_LICENSE("GPL");
