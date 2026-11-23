// SPDX-License-Identifier: GPL-2.0
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int64_t atomic64_t;

#define NET_XMIT_SUCCESS 0
#define NET_XMIT_DROP 1
#define NET_XMIT_CN 2
#define ETH_HLEN 14
#define VLAN_HLEN 4
#define VLAN_ETH_HLEN 18
#define CHECKSUM_NONE 0
#define ETH_P_IP 0x0800
#define ETH_P_IPV6 0x86dd
#define ETH_P_8021Q 0x8100
#define ETH_P_8021AD 0x88a8
#define DMA_FROM_DEVICE 2
#define WGFP_MAX_EGRESS 8
#define WGFP_MAX_BPIDS 64
#define DPAA_WGFP_TX_HEADROOM 64
#define READ_ONCE(x) (x)
static u8 wgfp_bpid_need_sync[WGFP_MAX_BPIDS];
#define unlikely(x) (x)
#define likely(x) (x)
#define atomic64_inc(p) (++*(p))
#define dpaa_wgfp_stat_inc(p) atomic64_inc(p)
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
#define local_bh_disable() ((void)0)
#define local_bh_enable() ((void)0)
#define rcu_dereference(p) (p)
#define smp_load_acquire(p) (*(p))
#define this_cpu_ptr(p) (p)
#define ntohs(x) __builtin_bswap16(x)
#define EXPORT_SYMBOL_GPL(x)

typedef unsigned long dma_addr_t;
struct qm_fd { u8 bpid; };
struct wgfp_bman_rel {
	bool active;
	u8 bpid;
	int n;
	dma_addr_t addr[8];
};
static struct wgfp_bman_rel wgfp_bman_rel;
static void wgfp_bman_rel_flush(struct wgfp_bman_rel *rel) { rel->n = 0; }

struct dpaa_wgfp_tx_buf {
	void *buf;
	u32 buf_size;
	int ifindex;
	void *priv;
};

struct device { int unused; };
struct net_device {
	const char *name;
	int ifindex;
	bool running;
};
struct ethhdr {
	u8 h_dest[6], h_source[6];
	u16 h_proto;
};
struct vlan_hdr {
	u16 h_vlan_TCI;
	u16 h_vlan_encapsulated_proto;
};
struct iphdr {
	u8 version_ihl;
	u8 unused[19];
} __attribute__((packed));
#define ihl version_ihl
struct ipv6hdr {
	u8 bytes[40];
};
struct sk_buff {
	struct net_device *dev;
	u8 *data;
	u8 *mac;
	int network_header, transport_header;
	unsigned int len;
	u16 protocol;
	u8 ip_summed;
	u8 storage[4096];
};
struct dpaa_wgfp_frame {
	u8 *buf;
	unsigned long addr;
	struct device *dev;
	u32 buf_size;
	const void *fd;
};

static struct net_device *wgfp_eg_dev[WGFP_MAX_EGRESS];
static int wgfp_neg;
static atomic64_t wgfp_xmit_ok, wgfp_xmit_fail;
static atomic64_t wgfp_xmit_invalid_egress, wgfp_xmit_frame_wait_fail;
static atomic64_t wgfp_xmit_poll_wait_fail, wgfp_xmit_orp_wait_fail;
static atomic64_t wgfp_xmit_ern, wgfp_xmit_nomem, wgfp_xmit_qdisc_drop, wgfp_xmit_cn;
static int releases, submissions, consumes, alloc_fail, queue_result, frees;
static bool release_before_submit;
static int dma_syncs;

static void kfree_skb(struct sk_buff *skb)
{
	(void)skb;
	frees++;
}

static void dpaa_wgfp_fd_release(const void *fd)
{
	(void)fd;
	releases++;
}

static struct sk_buff *netdev_alloc_skb(struct net_device *dev, u32 len)
{
	static struct sk_buff skb;

	if (alloc_fail)
		return NULL;
	assert(len <= sizeof(skb.storage));
	memset(&skb, 0, sizeof(skb));
	skb.dev = dev;
	skb.data = skb.storage + 64;
	skb.mac = skb.data;
	return &skb;
}

static void skb_put_data(struct sk_buff *skb, const void *src, u32 len)
{
	memcpy(skb->data, src, len);
	skb->len = len;
}

static void skb_reset_mac_header(struct sk_buff *skb)
{
	skb->mac = skb->data;
}

static void skb_set_network_header(struct sk_buff *skb, int off)
{
	skb->network_header = off;
}

static void skb_set_transport_header(struct sk_buff *skb, int off)
{
	skb->transport_header = off;
}

static const struct ethhdr *eth_hdr(const struct sk_buff *skb)
{
	return (const struct ethhdr *)skb->mac;
}

static void dma_sync_single_for_device(struct device *dev, unsigned long addr,
				       u32 len, int direction)
{
	assert(dev && addr && len == 256 && direction == DMA_FROM_DEVICE);
	dma_syncs++;
}

static bool netif_running(const struct net_device *dev)
{
	return dev->running;
}

static int dev_queue_xmit(struct sk_buff *skb)
{
	assert(skb->dev);
	release_before_submit = releases == 1;
	submissions++;
	consumes++;
	return queue_result;
}

static int settles;
static void dpaa_wgfp_orp_settle(void)
{
	settles++;
}

/* Force fallback to the owned-skb path in this harness (WGFP TX slot path is
 * tested separately in tests/dut/wgfp_tx_slots.c). */
static bool dpaa_wgfp_tx_alloc(int egress, int expected_ifindex, u32 max_len,
			       struct dpaa_wgfp_tx_buf *out)
{
	(void)egress; (void)expected_ifindex; (void)max_len; (void)out;
	return false;
}

static void dpaa_wgfp_tx_submit(struct dpaa_wgfp_frame *f,
				struct dpaa_wgfp_tx_buf *tx, u32 off, u32 len)
{
	(void)f; (void)tx; (void)off; (void)len;
	assert(0 && "unexpected tx_submit when tx_alloc returns false");
}

struct sk_buff *dpaa_wgfp_alloc_xmit_skb(int egress, int expected_ifindex,
					 u32 total_len);
void dpaa_wgfp_xmit_skb(struct dpaa_wgfp_frame *f, int egress,
			int expected_ifindex, struct sk_buff *skb);

#include "wgfp_xmit_body.h"

static void reset(void)
{
	memset(wgfp_eg_dev, 0, sizeof(wgfp_eg_dev));
	wgfp_neg = releases = submissions = consumes = alloc_fail = queue_result = frees = 0;
	release_before_submit = false;
	settles = 0;
	dma_syncs = 0;
	wgfp_xmit_ok = wgfp_xmit_fail = wgfp_xmit_invalid_egress = 0;
	wgfp_xmit_frame_wait_fail = wgfp_xmit_poll_wait_fail = 0;
	wgfp_xmit_orp_wait_fail = wgfp_xmit_ern = 0;
	wgfp_xmit_nomem = wgfp_xmit_qdisc_drop = wgfp_xmit_cn = 0;
}

static void prepare_frame(struct dpaa_wgfp_frame *frame, u8 *buffer)
{
	struct ethhdr *eth = (struct ethhdr *)buffer;

	memset(buffer, 0xa5, 256);
	memset(eth->h_dest, 1, 6);
	memset(eth->h_source, 2, 6);
	eth->h_proto = ntohs(ETH_P_IP);
	frame->buf = buffer;
	frame->addr = (unsigned long)buffer;
	frame->dev = (struct device *)(buffer + 128);
	frame->buf_size = 256;
	frame->fd = buffer;
}

int main(void)
{
	struct net_device dev = { .name = "eth3", .ifindex = 7, .running = true };
	struct dpaa_wgfp_frame frame;
	struct sk_buff *skb;
	u8 buffer[256];

	reset();
	memset(wgfp_bpid_need_sync, 1, sizeof(wgfp_bpid_need_sync));
	prepare_frame(&frame, buffer);
	wgfp_eg_dev[0] = &dev;
	wgfp_neg = 1;
	dpaa_wgfp_xmit(&frame, 0, 0, 64);
	assert(releases == 1 && submissions == 1 && consumes == 1);
	assert(release_before_submit && wgfp_xmit_ok == 1 && settles == 1);
	assert(dma_syncs == 1);

	/* Coherent BPID skips redundant dma_sync_single_for_device */
	reset();
	memset(wgfp_bpid_need_sync, 0, sizeof(wgfp_bpid_need_sync));
	prepare_frame(&frame, buffer);
	wgfp_eg_dev[0] = &dev;
	wgfp_neg = 1;
	dpaa_wgfp_xmit(&frame, 0, 0, 64);
	assert(releases == 1 && submissions == 1 && consumes == 1);
	assert(dma_syncs == 0);
	memset(wgfp_bpid_need_sync, 1, sizeof(wgfp_bpid_need_sync));

	reset();
	prepare_frame(&frame, buffer);
	wgfp_eg_dev[0] = &dev;
	wgfp_neg = 1;
	alloc_fail = 1;
	dpaa_wgfp_xmit(&frame, 0, 0, 64);
	assert(releases == 1 && submissions == 0 && consumes == 0);
	assert(wgfp_xmit_fail == 1 && wgfp_xmit_nomem == 1 && settles == 1);
	assert(dma_syncs == 1);

	reset();
	prepare_frame(&frame, buffer);
	wgfp_eg_dev[0] = &dev;
	wgfp_neg = 1;
	dev.running = false;
	dpaa_wgfp_xmit(&frame, 0, 0, 64);
	assert(releases == 1 && submissions == 0 && wgfp_xmit_fail == 1);
	assert(wgfp_xmit_invalid_egress == 1);
	dev.running = true;

	reset();
	prepare_frame(&frame, buffer);
	dpaa_wgfp_xmit(&frame, 0, 0, 64);
	assert(releases == 1 && submissions == 0 && wgfp_xmit_invalid_egress == 1);

	reset();
	prepare_frame(&frame, buffer);
	dpaa_wgfp_xmit(&frame, 0, 255, 2);
	assert(releases == 1 && submissions == 0 && wgfp_xmit_invalid_egress == 1);

	/* NET_XMIT_DROP: qdisc drop, NOT invalid_egress */
	reset();
	prepare_frame(&frame, buffer);
	wgfp_eg_dev[0] = &dev;
	wgfp_neg = 1;
	queue_result = NET_XMIT_DROP;
	dpaa_wgfp_xmit(&frame, 0, 0, 64);
	assert(releases == 1 && submissions == 1 && consumes == 1);
	assert(wgfp_xmit_ok == 0 && wgfp_xmit_fail == 1);
	assert(wgfp_xmit_qdisc_drop == 1 && wgfp_xmit_invalid_egress == 0);

	/* NET_XMIT_CN: enqueued with congestion notice -> xmit_ok + xmit_cn */
	reset();
	prepare_frame(&frame, buffer);
	wgfp_eg_dev[0] = &dev;
	wgfp_neg = 1;
	queue_result = NET_XMIT_CN;
	dpaa_wgfp_xmit(&frame, 0, 0, 64);
	assert(releases == 1 && submissions == 1 && consumes == 1);
	assert(wgfp_xmit_ok == 1 && wgfp_xmit_cn == 1 && wgfp_xmit_fail == 0);

	/* dpaa_wgfp_alloc_xmit_skb + dpaa_wgfp_xmit_skb with ifindex check */
	reset();
	prepare_frame(&frame, buffer);
	wgfp_eg_dev[0] = &dev;
	wgfp_neg = 1;
	assert(dpaa_wgfp_alloc_xmit_skb(0, 99, 64) == NULL);
	assert(wgfp_xmit_invalid_egress == 1 && wgfp_xmit_fail == 1);

	reset();
	prepare_frame(&frame, buffer);
	wgfp_eg_dev[0] = &dev;
	wgfp_neg = 1;
	skb = dpaa_wgfp_alloc_xmit_skb(0, 7, 64);
	assert(skb != NULL);
	skb_put_data(skb, buffer, 64);
	dpaa_wgfp_xmit_skb(&frame, 0, 7, skb);
	assert(releases == 1 && submissions == 1 && wgfp_xmit_ok == 1 && frees == 0);

	/* ifindex mismatch at submit time frees skb and releases frame */
	reset();
	prepare_frame(&frame, buffer);
	wgfp_eg_dev[0] = &dev;
	wgfp_neg = 1;
	skb = dpaa_wgfp_alloc_xmit_skb(0, 7, 64);
	assert(skb != NULL);
	skb_put_data(skb, buffer, 64);
	dpaa_wgfp_xmit_skb(&frame, 0, 88, skb);
	assert(frees == 1 && releases == 1 && submissions == 0);
	assert(wgfp_xmit_invalid_egress == 1 && wgfp_xmit_fail == 1);

	puts("WGFP skb TX ownership: OK");
	return 0;
}
