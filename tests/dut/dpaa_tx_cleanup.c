// SPDX-License-Identifier: GPL-2.0
/* Host DMA model for the extracted dpaa_cleanup_tx_fd(). */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint64_t dma_addr_t;
typedef uint64_t u64;

enum dma_data_direction { DMA_BIDIRECTIONAL, DMA_TO_DEVICE, DMA_FROM_DEVICE, DMA_NONE };
enum { qm_fd_contig, qm_fd_sg };

#define DPAA_SGT_SIZE 256
#define DPAA_SGT_MAX_ENTRIES 16
#define FSL_DPAA_BPID_INV 0xff
#define SKBTX_HW_TSTAMP 1
#define TX 0
#define unlikely(x) (x)
#define WARN_ON(x) (x)

struct device { int unused; };
struct net_device {
	struct { struct device *parent; } dev;
	const char *name;
};
struct mac_device { void *port[1]; };
struct dpaa_priv {
	struct net_device *net_dev;
	struct device *tx_dma_dev;
	struct mac_device *mac_dev;
	bool tx_tstamp;
};
struct skb_shared_info { unsigned int tx_flags; };
struct sk_buff { struct skb_shared_info info; };
struct xdp_frame { int unused; };
struct dpaa_eth_swbp {
	struct sk_buff *skb;
	struct xdp_frame *xdpf;
};
struct skb_shared_hwtstamps { u64 hwtstamp; };
struct qm_fd {
	dma_addr_t addr;
	unsigned int off, len, format;
	uint8_t bpid;
};
struct qm_sg_entry {
	dma_addr_t addr;
	unsigned int len;
	bool final, ext;
};

static int single_unmaps, page_unmaps, sg_accesses, xdp_returns, frees;
static int timestamps, warnings;
static void *fman_pcd_ic_vaddr;

#define net_warn_ratelimited(...) (++warnings)
#define dev_warn(...) (++warnings)
#define skb_shinfo(skb) (&(skb)->info)

static dma_addr_t qm_fd_addr(const struct qm_fd *fd) { return fd->addr; }
static unsigned int qm_fd_get_format(const struct qm_fd *fd) { return fd->format; }
static unsigned int qm_fd_get_offset(const struct qm_fd *fd) { return fd->off; }
static unsigned int qm_fd_get_length(const struct qm_fd *fd) { return fd->len; }
static void *phys_to_virt(dma_addr_t addr) { return (void *)(uintptr_t)addr; }
static dma_addr_t qm_sg_addr(const struct qm_sg_entry *sg)
{
	sg_accesses++;
	return sg->addr;
}
static unsigned int qm_sg_entry_get_len(const struct qm_sg_entry *sg)
{
	sg_accesses++;
	return sg->len;
}
static bool qm_sg_entry_is_final(const struct qm_sg_entry *sg)
{
	sg_accesses++;
	return sg->final;
}
static bool qm_sg_entry_is_ext(const struct qm_sg_entry *sg)
{
	sg_accesses++;
	return sg->ext;
}
static void dma_unmap_single(struct device *dev, dma_addr_t addr,
			     unsigned int len, enum dma_data_direction dir)
{
	single_unmaps++;
}
static void dma_unmap_page(struct device *dev, dma_addr_t addr,
			   unsigned int len, enum dma_data_direction dir)
{
	page_unmaps++;
}
static void xdp_return_frame(struct xdp_frame *frame) { xdp_returns++; }
static int fman_port_get_tstamp(void *port, void *vaddr, u64 *ns)
{
	*ns = 123;
	return 0;
}
static u64 ns_to_ktime(u64 ns) { return ns; }
static void skb_tstamp_tx(struct sk_buff *skb, struct skb_shared_hwtstamps *ts)
{
	if (ts->hwtstamp == 123)
		timestamps++;
}
static void free_pages(unsigned long addr, unsigned int order) { frees++; }

#include "dpaa_tx_cleanup_body.h"

static int run_case(const char *name, bool foreign, bool sg, bool xdp)
{
	union {
		uint64_t align;
		unsigned char bytes[256];
	} buffer = { 0 };
	struct device dev = { 0 };
	struct net_device netdev = { .dev.parent = &dev, .name = "test" };
	struct mac_device mac = { 0 };
	struct dpaa_priv priv = {
		.net_dev = &netdev, .tx_dma_dev = &dev,
		.mac_dev = &mac, .tx_tstamp = true,
	};
	struct sk_buff skb = { .info.tx_flags = SKBTX_HW_TSTAMP };
	struct xdp_frame xdpf = { 0 };
	struct dpaa_eth_swbp *swbp = (void *)buffer.bytes;
	struct qm_sg_entry *sgt = (void *)(buffer.bytes + 64);
	struct qm_fd fd = {
		.addr = (uintptr_t)buffer.bytes, .off = 64, .len = 100,
		.format = sg ? qm_fd_sg : qm_fd_contig,
		.bpid = foreign ? 7 : FSL_DPAA_BPID_INV,
	};
	struct sk_buff *result;
	bool ok;

	swbp->skb = xdp ? NULL : &skb;
	swbp->xdpf = &xdpf;
	sgt[0].addr = 0x1000;
	sgt[0].len = 100;
	sgt[0].final = true;
	single_unmaps = page_unmaps = sg_accesses = xdp_returns = frees = 0;
	timestamps = warnings = 0;
	fman_pcd_ic_vaddr = &dev;

	result = dpaa_cleanup_tx_fd(&priv, &fd, true);
	if (foreign) {
		ok = !result && !single_unmaps && !page_unmaps && !sg_accesses &&
		     !xdp_returns && !frees && !timestamps && warnings &&
		     fman_pcd_ic_vaddr == &dev;
	} else {
		ok = result == (xdp ? NULL : &skb) && single_unmaps == 1 &&
		     page_unmaps == !!sg && xdp_returns == !!xdp &&
		     frees == (sg && !xdp) && timestamps == !xdp && !warnings &&
		     fman_pcd_ic_vaddr == &dev;
	}
	printf("%s: %s (single=%d page=%d sg=%d ic_published=%d)\n", name,
	       ok ? "PASS" : "FAIL", single_unmaps, page_unmaps, sg_accesses,
	       fman_pcd_ic_vaddr == buffer.bytes);
	return ok ? 0 : 1;
}

int main(void)
{
	int failures = 0;

	failures += run_case("foreign contiguous", true, false, false);
	failures += run_case("foreign scatter/gather", true, true, false);
	failures += run_case("kernel skb", false, false, false);
	failures += run_case("kernel XDP", false, false, true);
	failures += run_case("kernel scatter/gather skb", false, true, false);
	return failures ? 1 : 0;
}
