// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * af_xdp_pool_main.c - DPAA1 AF_XDP zero-copy pool flavor module (skeleton)
 *
 * M3.1 deliverable per specs/dpaa1-afxdp-modernization-spec.md sec 5.4.
 *
 * This module registers a dpaa_qmgmt_ops table with the fsl_dpa core driver
 * via the M0 flavor-ops abstraction (CONFIG_DPAA_FLAVOR_OPS).  At this
 * skeleton stage every callback returns -EOPNOTSUPP so behaviour with the
 * module loaded is byte-identical to behaviour with it absent: every XSK
 * setsockopt(XDP_BIND, XDP_ZEROCOPY) falls back to copy-mode exactly as
 * mainline does today.
 *
 * Subsequent patches (0074-0080) replace each stub with the real
 * implementation:
 *   0074 - xsk_wakeup        : NAPI kick (LANDED)
 *   0075a - xsk_pool_attach  : validation arms + LIODN accessor (LANDED)
 *   0075b - xsk_pool_attach  : DMA-map + BMan seed + RCU publish (LANDED)
 *   0076 - xsk_pool_detach   : FMan BMI quiescence
 *   0077 - rx_hook           : ZC RX with xdp_do_redirect
 *   0078 - (TX is core-driver, not a flavor hook) - strict backpressure
 *   0079 - xsk_set_rx_need_wakeup
 *   0080 - alloc_rx_fqs      : dedicated XSK BMan channels
 */

#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/types.h>
#include <linux/errno.h>
#include <linux/printk.h>
#include <linux/dma-mapping.h>
#include <linux/netdevice.h>
#include <linux/rtnetlink.h>
#include <linux/debugfs.h>
#include <linux/seq_file.h>
#include <linux/bsearch.h>
#include <linux/sort.h>
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
 * FMAN_PORT_TYPE_RX = 1 }` from fman.h.  Indexing port[] with the
 * fman_port_type enum selects the TX port, so fman_port_set_rx_bpool()
 * rejects it with -EINVAL (port_type != FMAN_PORT_TYPE_RX) and true-ZC
 * RX never arms.  dpaa_eth.c's enum is .c-private, so mirror its RX index
 * locally.  Matches mainline dpaa_eth.c usage `mac_dev->port[RX]`.
 */
#define DPAA_MAC_PORT_RX 0

struct qman_fq;
struct qm_dqrr_entry;
struct xsk_buff_pool;
struct dpaa_napi_portal;
struct list_head;

/* ---------- Stub callbacks: every entry returns -EOPNOTSUPP ---------- */

static int af_xdp_pool_alloc_rx_fqs(struct dpaa_priv *priv,
struct list_head *list)
{
return -EOPNOTSUPP;
}

/*
 * af_xdp_pool_xsk_pool_attach - install an AF_XDP zero-copy pool on an RX queue
 *
 * Patch 0075a (Phase 2 step 1, spec sec 5.3):
 *
 *   This is an INTERMEDIATE deliverable.  Step 1 (validate constraints)
 *   is implemented in full.  The function then returns -EOPNOTSUPP so
 *   the kernel XSK core continues to fall back to copy-mode exactly as
 *   it does today (M2 acceptance gate semantics, verified in the patch
 *   0071 DUT run).  Behaviour is byte-identical to pre-0075a; what
 *   0075a buys us is:
 *
 *     * the new fman_port_get_liodn() accessor
 *       (drivers/net/ethernet/freescale/fman/fman_port.c) exported,
 *       statically verifiable, and exercised at every XSK attach as a
 *       DTS sanity check;
 *     * every fail-closed validation arm with its corresponding
 *       priv->xsk_*_reject counter bump.
 *
 *   Patch 0075b will add steps 2 - 7 (xsk_pool_dma_map, dedicated BMan
 *   bpid allocation, 8192-chunk seed loop, MEM_TYPE_XSK_BUFF_POOL rxq
 *   registration, RCU publish into priv->xsk_pool[]).  At that point
 *   this function returns 0 and the XSK core promotes the socket into
 *   true zero-copy mode.
 *
 *   Note on PAMU (spec sec 4.6): the spec assumed a per-attach
 *   fsl_pamu_window_create()/_destroy() API.  Mainline 6.18.31's
 *   drivers/iommu/fsl_pamu.c is gated by Kconfig dependency
 *   "PPC_E500MC || (COMPILE_TEST && PPC)" and does not build on arm64.
 *   On arm64 LS1046A the PAMU runs in firmware-programmed bypass and
 *   the FMan masters DMA directly to physical RAM via dma-direct -
 *   xsk_pool_dma_map() in 0075b will be sufficient.  No per-window
 *   PAMU programming is required.  This will be reflected in the spec
 *   v4.2 update.
 *
 *   Hard constraints (fail closed, no copy-mode silent demotion):
 *     - queue_id < priv->xsk_max_qbands (DPAA1_XSK_MAX_QBANDS = 4)
 *     - dev->mtu <= 3290 (DPAA1 BP slot size cap, spec sec 1.2)
 *     - xsk_pool_get_rx_frame_size(pool) >= DPAA1_MIN_UMEM_CHUNK (3840)
 *     - xsk_pool_get_headroom(pool)      >= priv->tx_headroom
 *     - non-zero LIODN resolved from the RX fman_port (DTS sanity)
 *
 *   The first-chunk DPAA_FD_DATA_ALIGNMENT (64 B) probe is deferred to
 *   patch 0075b because it requires xsk_buff_alloc_batch() which only
 *   makes sense once we own the pool (post-attach).
 */
/*
 * dpaa_xsk_chunk_record / dpaa_xsk_chunk_lookup (patch 0103a infra,
 * productive in 0103b)
 *
 * Recover sw-ring reverse-map helpers, keyed by the XSK pool head-index
 * (xskb - pool->heads).  The head-index is stable for the pool's
 * lifetime and directly indexes the flat priv->xsk_chunk_map[band][]
 * array allocated at attach.  _record() runs at FILL-release time
 * (preserving the xdp_buff handle the BMan-release path otherwise
 * discards); _lookup() runs at RX after dpaa_xsk_chunk_head_from_dma()
 * recovers the head-index from the bare chunk DMA cookie.
 *
 * 0103b makes these productive: _record() is called from the attach
 * seed loop + napi_refill, _lookup() from af_xdp_pool_recover_xdp() in
 * the RX hot path.
 */
static void dpaa_xsk_chunk_record(struct dpaa_priv *priv, u16 band,
  u32 chunk_idx, struct xdp_buff *xdp)
{
if (unlikely(!priv->xsk_chunk_map[band] ||
     chunk_idx >= priv->xsk_chunk_map_len[band]))
return;
priv->xsk_chunk_map[band][chunk_idx] = xdp;
}

static struct xdp_buff *dpaa_xsk_chunk_lookup(struct dpaa_priv *priv,
      u16 band, u32 chunk_idx)
{
struct xdp_buff *xdp;

if (unlikely(!priv->xsk_chunk_map[band] ||
     chunk_idx >= priv->xsk_chunk_map_len[band]))
return NULL;
xdp = priv->xsk_chunk_map[band][chunk_idx];
if (xdp)
priv->xsk_zc_recover_lookup++;
return xdp;
}

/*
 * dpaa_xsk_chunk_head_index - head-index of an xdp_buff in its pool
 *
 * Every handle returned by xsk_buff_alloc_batch() is the address of a
 * struct xdp_buff embedded in pool->heads[].  The head-index is the
 * canonical, allocation-stable key for the reverse map and for the
 * dma->idx sorted lookup built at attach.
 */
static u32 dpaa_xsk_chunk_head_index(struct xsk_buff_pool *pool,
     struct xdp_buff *xdp)
{
struct xdp_buff_xsk *xskb =
container_of(xdp, struct xdp_buff_xsk, xdp);

return (u32)(xskb - pool->heads);
}

/*
 * dpaa_xsk_dma_cmp / dpaa_xsk_dma_swap - sort/bsearch glue for the
 * per-band dma_addr_t -> head-index index built at attach.
 *
 * xsk_chunk_dma[band] is kept sorted ascending by dma_addr_t; the
 * parallel xsk_chunk_dma_idx[band] holds the matching head-index.  The
 * swap callback keeps the two arrays in lockstep during sort().
 */
struct dpaa_xsk_dma_sort_ctx {
dma_addr_t *dma;
u32        *idx;
};

static int dpaa_xsk_dma_cmp(const void *a, const void *b)
{
dma_addr_t da = *(const dma_addr_t *)a;
dma_addr_t db = *(const dma_addr_t *)b;

if (da < db)
return -1;
if (da > db)
return 1;
return 0;
}

/*
 * dpaa_xsk_chunk_head_from_dma - recover head-index from a chunk DMA
 *
 * O(log n) bsearch over the sorted xsk_chunk_dma[band] array built at
 * attach.  Correct for BOTH aligned-contiguous and fragmented UMEMs --
 * no subtract+shift contiguity assumption (spec sec 6.1.8 crash
 * class).  Returns true and sets *head_idx on a hit; false on miss
 * (the DMA cookie did not originate from this band's XSK pool, e.g. a
 * kernel page-pool FD that slipped past the bpid recognition test --
 * the caller must then fall back to the skbuf path, never redirect).
 */
static bool dpaa_xsk_chunk_head_from_dma(struct dpaa_priv *priv, u16 band,
 dma_addr_t dma, u32 *head_idx)
{
dma_addr_t *base = priv->xsk_chunk_dma[band];
u32 cnt = priv->xsk_chunk_dma_cnt[band];
dma_addr_t *hit;

if (unlikely(!base || !cnt || !priv->xsk_chunk_dma_idx[band]))
return false;

hit = bsearch(&dma, base, cnt, sizeof(*base), dpaa_xsk_dma_cmp);
	if (unlikely(!hit)) {
		static int _f115_n;
		if (_f115_n < 8) {
			_f115_n++;
			pr_err("F-115 recover-miss: fd_dma=0x%llx band=%u cnt=%u base[0]=0x%llx base[cnt-1]=0x%llx delta0=%lld\n",
			       (unsigned long long)dma, band, cnt,
			       (unsigned long long)base[0],
			       (unsigned long long)base[cnt-1],
			       (long long)((s64)dma - (s64)base[0]));
		}
	}
if (!hit)
return false;

*head_idx = priv->xsk_chunk_dma_idx[band][hit - base];
return true;
}

/*
 * dpaa_xsk_build_dma_index - build the sorted dma->head-index lookup
 *
 * Called once at attach AFTER xsk_pool_dma_map(), when every
 * pool->heads[i].dma is populated and stable for the pool's lifetime.
 * Allocates xsk_chunk_dma[band] + xsk_chunk_dma_idx[band] sized to
 * pool->heads_cnt, fills them from the heads array, and sorts ascending
 * by DMA cookie so dpaa_xsk_chunk_head_from_dma() can bsearch.
 *
 * Failure is non-fatal: on -ENOMEM the arrays stay NULL and
 * xsk_chunk_dma_cnt stays 0, so dpaa_xsk_chunk_head_from_dma() returns
 * false for every cookie and the RX path falls back to the skbuf path
 * (copy-mode-equivalent).  Freed in detach.
 */
static void dpaa_xsk_build_dma_index(struct dpaa_priv *priv, u16 band,
     struct xsk_buff_pool *pool)
{
struct dpaa_xsk_dma_sort_ctx ctx;
u32 cnt = pool->heads_cnt;
u32 i;

priv->xsk_chunk_dma[band]     = NULL;
priv->xsk_chunk_dma_idx[band] = NULL;
priv->xsk_chunk_dma_cnt[band] = 0;

if (!cnt)
return;

priv->xsk_chunk_dma[band] =
kmalloc_array(cnt, sizeof(dma_addr_t), GFP_KERNEL);
priv->xsk_chunk_dma_idx[band] =
kmalloc_array(cnt, sizeof(u32), GFP_KERNEL);
if (!priv->xsk_chunk_dma[band] || !priv->xsk_chunk_dma_idx[band]) {
kfree(priv->xsk_chunk_dma[band]);
kfree(priv->xsk_chunk_dma_idx[band]);
priv->xsk_chunk_dma[band]     = NULL;
priv->xsk_chunk_dma_idx[band] = NULL;
return;
}

dev_info_ratelimited(priv->net_dev->dev.parent,

	"F-115 build: band=%u cnt=%u pool_headroom=%u first_head_dma=0x%llx\n",

	band, cnt, pool->headroom,

	(unsigned long long)pool->heads[0].dma);


for (i = 0; i < cnt; i++) {
priv->xsk_chunk_dma[band][i]     = xsk_buff_xdp_get_dma(&pool->heads[i].xdp); /* F-115 v4: use xsk_buff_xdp_get_dma to get real DMA address */
priv->xsk_chunk_dma_idx[band][i] = i;
}

/* Sort both arrays in lockstep, keyed by DMA cookie. */
ctx.dma = priv->xsk_chunk_dma[band];
ctx.idx = priv->xsk_chunk_dma_idx[band];
{
/* Insertion sort keeping idx[] in lockstep -- heads_cnt is a
 * few thousand at most (8192 seed cap), one-shot at attach, so
 * O(n^2) worst case is acceptable and avoids a custom sort()
 * swap closure (sort()'s swap callback cannot capture ctx).
 */
u32 a, b;

for (a = 1; a < cnt; a++) {
dma_addr_t kd = ctx.dma[a];
u32 ki = ctx.idx[a];

b = a;
while (b > 0 && ctx.dma[b - 1] > kd) {
ctx.dma[b] = ctx.dma[b - 1];
ctx.idx[b] = ctx.idx[b - 1];
b--;
}
ctx.dma[b] = kd;
ctx.idx[b] = ki;
}
}

priv->xsk_chunk_dma_cnt[band] = cnt;
}

/*
 * af_xdp_pool_rx_hook - true-ZC RX Recover + redirect (patch 0103b)
 *
 * Called from rx_default_dqrr() (dpaa_eth.c) for every ingress FD on a
 * netdev whose qmgmt_ops are bound to this flavor module.  Returns true
 * to CONSUME the FD (the core RX path then skips dma_unmap_page +
 * build_skb entirely); false to let the FD travel the unchanged
 * mainline skbuf path.
 *
 * True-ZC RX (spec sec 6.1.7, three mechanisms):
 *   1. Recognise -- the FD's bpid equals priv->xsk_bpid[band], i.e. FMan
 *      DMA'd it into a chunk from the dedicated XSK BMan pool (only true
 *      AFTER the attach-time reprogram-WRITE points the FMan RX port at
 *      that pool; until then no FD ever carries the XSK bpid and this
 *      hook returns false for every frame -- byte-identical to mainline).
 *   2. Recover -- turn the bare chunk DMA cookie (qm_fd_addr(fd)) back
 *      into the originating struct xdp_buff via the 0103a reverse map:
 *      dpaa_xsk_chunk_head_from_dma() bsearches the sorted dma->head-index
 *      array built at attach, then dpaa_xsk_chunk_lookup() reads the
 *      xdp_buff* recorded at FILL-release time.
 *   3. reProgram -- done at attach (the FMan RX-port BPID swap); not here.
 *
 * On a successful Recover we set the xdp_buff length to the FD frame
 * length, sync the DMA region for CPU access, run the netdev's XDP prog
 * (if any), and xdp_do_redirect() into the XSKMAP.  Any miss at any step
 * (no reverse-map hit, no XDP prog, redirect failure) falls back to the
 * skbuf path by returning false -- NEVER free or double-handle the chunk.
 *
 * Hardware-risk note (spec sec 6.1.8): this hook is the consumer half of
 * the INSEPARABLE 0103b pair.  It MUST be wired together with the
 * attach-time reprogram-WRITE: firing the reprogram without this hook
 * would let FMan DMA into XSK chunks that build_skb() then consumes as
 * page-pool pages -> xp_alloc free-list corruption / soft-lockup.
 */
static bool af_xdp_pool_rx_hook(struct dpaa_priv *priv,
struct qman_fq *fq,
const struct qm_dqrr_entry *dq)
{
const struct qm_fd *fd = &dq->fd;
struct xsk_buff_pool *pool;
struct bpf_prog *prog;
struct xdp_buff *xdp;
dma_addr_t addr;
u32 head_idx;
u32 len;
u16 band;
int act;
bool consumed = false;

	/* 0110: the 0103f dispatch in rx_default_dqrr defers hard-IRQ
	 * deliveries to NAPI before calling us, so we only ever run in
	 * NAPI (softirq) context where xdp_do_redirect()/xdp_do_flush()
	 * are legal.  If a future refactor breaks that invariant, bail
	 * (frame falls through to the mainline path) rather than corrupt
	 * the xsk flush list from hard IRQ (observed dual-CPU Oops in
	 * __xsk_map_flush on LS1046A).
	 */
	if (WARN_ON_ONCE(in_hardirq()))
		return false;

if (!priv)
return false;

band = dpaa_fq_to_qband(priv, fq->fqid);
if (band >= DPAA1_XSK_MAX_QBANDS)
return false;

/* Recognise (mechanism 1): only FDs FMan DMA'd into the dedicated
 * XSK BMan pool carry our bpid.  Dormant until the attach-time
 * reprogram-WRITE runs.
 */
if (fd->bpid != priv->xsk_bpid[band])
return false;

/* 0114: recognition oracle.  Counts every FD FMan DMA-d into the
 * dedicated XSK BMan pool, at the productive dispatch point: this
 * hook consumes ZC frames before rx_default_dqrr reaches the old
 * 0093 observational probe, so that increment was dead once 0103f/
 * 0110 made dispatch-first the order.  Climbs in lock-step with
 * xsk_zc_rx_recovered / xsk_zc_rx_redirect when the full
 * Recover+redirect path succeeds.  Spec sec 6.1.10 / 6.1.19.
 */
priv->xsk_zc_eligible++;

rcu_read_lock();
pool = rcu_dereference(priv->xsk_pool[band]);
if (!pool)
goto out_unlock;

/* Recover (mechanism 2): bare DMA cookie -> head-index -> xdp_buff. */
addr = qm_fd_addr(fd);
if (!dpaa_xsk_chunk_head_from_dma(priv, band, addr, &head_idx))
goto out_unlock;
xdp = dpaa_xsk_chunk_lookup(priv, band, head_idx);
if (!xdp)
goto out_unlock;

/* 0114: Recover oracle (relocated from 0096-s now-dead site). */
priv->xsk_zc_rx_recovered++;

/* Hand the chunk to the XDP/XSK core. */
len = qm_fd_get_length(fd);
xsk_buff_set_size(xdp, len);
xsk_buff_dma_sync_for_cpu(xdp);

prog = READ_ONCE(priv->xdp_prog);
if (!prog) {
/* No XDP program attached: there is no XSKMAP to redirect
 * into, so we cannot zero-copy this frame.  Fall back to the
 * skbuf path (return false) -- the chunk stays owned by the
 * XSK pool free list and will be re-released on the next
 * FILL cycle.  This matches the 0096 read-side observation
 * that Recover is a no-op without a consumer program.
 */
goto out_unlock;
}

{
		struct bpf_net_context __bpf_net_ctx, *bpf_net_ctx;

		bpf_net_ctx = bpf_net_ctx_set(&__bpf_net_ctx);
		act = bpf_prog_run_xdp(prog, xdp);
		switch (act) {
		case XDP_REDIRECT:
			if (xdp_do_redirect(priv->net_dev, xdp, prog) == 0) {
				priv->xsk_zc_rx_redirect++;
				consumed = true;
				/* xdp_do_redirect() into an XSKMAP only queues
				 * the descriptor (xskq_prod_reserve_desc); the
				 * producer pointer is published by xsk_flush()
				 * which only runs from xdp_do_flush() walking
				 * this bpf_net_context's flush list.  We tear
				 * the context down on hook exit, so flush here
				 * or the frame never becomes visible to user
				 * space (RX ring producer never advances).
				 * Per-frame flush is the correctness baseline;
				 * NAPI-level batching needs qmgmt_ops plumbing
				 * for the portal's napi struct (np->xdp_act).
				 */
				xdp_do_flush();
			}
			break;
		case XDP_DROP:
			consumed = true;
			break;
		case XDP_PASS:
		default:
			break;
		}
		if (bpf_net_ctx)
			bpf_net_ctx_clear(bpf_net_ctx);
	}

out_unlock:
	rcu_read_unlock();
	return consumed;
}

static int af_xdp_pool_xsk_pool_attach(struct dpaa_priv *priv,
       struct xsk_buff_pool *pool,
       u16 queue_id)
{
struct net_device *ndev;
struct fman_port *rxp;
u32 frame_size, headroom;
struct bman_pool *bpool;
int ret;
u32 seeded, batch, n, i;

pr_err("ZCBIND: af_xdp_pool_attach ENTER pool=%px qid=%u\n", pool, queue_id);
	if (!priv || !pool)
return -EINVAL;

ndev = priv->net_dev;

/* Step 1: validate constraints, fail closed. */
if (queue_id >= priv->xsk_max_qbands ||
    queue_id >= DPAA1_XSK_MAX_QBANDS) {
priv->xsk_pool_attach_fail++;
return -EINVAL;
}

if (ndev->mtu > 3290) {
priv->xsk_mtu_reject++;
priv->xsk_pool_attach_fail++;
return -EINVAL;
}

frame_size = xsk_pool_get_rx_frame_size(pool);
if (frame_size < DPAA1_MIN_UMEM_CHUNK) {
priv->xsk_align_reject++;
priv->xsk_pool_attach_fail++;
return -EINVAL;
}

headroom = xsk_pool_get_headroom(pool);
if (headroom < priv->tx_headroom) {
priv->xsk_headroom_reject++;
priv->xsk_pool_attach_fail++;
return -EINVAL;
}

/*
 * fman_port + mac_dev sanity checks: prove the RX FMan port is bound to
 * its parent FMan.  The LIODN sanity arm that used to follow was removed
 * by patch 0075c: on arm64 LS1046A the Mono Gateway DT carries no
 * fsl,liodn properties on FMan ports, the fsl_pamu driver is PPC-only,
 * and the value was never consumed in our DMA path (xsk_pool_dma_map()
 * via dma-direct in step 2 below is sufficient on the CoreNet-coherent
 * fabric).
 */
if (!priv->mac_dev) {
priv->xsk_pamu_window_fail++;
priv->xsk_pool_attach_fail++;
return -ENODEV;
}

{
	struct fman *_fm = fman_bind(priv->mac_dev->fman_dev);
	rxp = _fm ? fman_port_lookup_rx(_fm, fman_port_get_id(
		priv->mac_dev->port[DPAA_MAC_PORT_RX])) : NULL;
}
if (!rxp) {
priv->xsk_pamu_window_fail++;
priv->xsk_pool_attach_fail++;
return -ENODEV;
}

	/*
	 * Step 2: DMA-map the entire UMEM region.
	 *
	 * On arm64 LS1046A the PAMU is in U-Boot-programmed bypass and the
	 * fsl_pamu driver is PPC-only (Kconfig: depends on PPC_E500MC).
	 * dma_map_*() goes through the dma-direct path and produces
	 * CoreNet-coherent identity-mapped physical addresses that FMan BMI
	 * can write directly.  DMA_ATTR_SKIP_CPU_SYNC omits the per-operation
	 * cache-management barriers on the coherent CoreNet fabric.
	 *
	 * Failure here is a hard error (misconfigured IOMMU or OOM for the
	 * DMA-mapping slab).  Do not fall back to copy-mode silently.
	 */
	WARN_ON(!priv->rx_dma_dev);
ret = xsk_pool_dma_map(pool, priv->rx_dma_dev,
			       DMA_ATTR_SKIP_CPU_SYNC);
	if (ret) {
		pr_err("ZCBIND: af_xdp_pool_attach FAIL: xsk_pool_dma_map ret=%d\n", ret);
		priv->xsk_dma_map_fail++;
		priv->xsk_pool_attach_fail++;
		return -EFAULT;
	}

	/*
	 * Step 3: Allocate a dedicated BMan pool ID (BPID) for this XSK
	 * queue band.  One BPID per qband; FLAVOR=vpp reserves max 4 per
	 * netdev (max 8 for eth3+eth4 dual-SFP+), well within the 64-BPID
	 * LS1046A budget.
	 */
	bpool = bman_new_pool();
	if (!bpool) {
		pr_err("ZCBIND: af_xdp_pool_attach FAIL: bman_new_pool ENOMEM\n");
		ret = -ENOMEM;
		priv->xsk_pool_attach_fail++;
		goto err_unmap;
	}
	priv->xsk_bpid[queue_id]  = bman_get_bpid(bpool);
	priv->xsk_bpool[queue_id] = bpool;

	/* 0103a: allocate the DORMANT Recover reverse-map for this qband.
	 * One xdp_buff* slot per initial-seed chunk.  Failure is non-fatal
	 * -- the map stays NULL and _record/_lookup early-return, leaving
	 * the datapath on the unchanged skbuf path (exactly 0102).  Freed
	 * in detach.
	 */
	priv->xsk_chunk_map[queue_id] =
		kcalloc(DPAA1_XSK_INITIAL_SEED, sizeof(struct xdp_buff *),
			GFP_KERNEL);
	priv->xsk_chunk_map_len[queue_id] =
		priv->xsk_chunk_map[queue_id] ? DPAA1_XSK_INITIAL_SEED : 0;

	/*
	 * Step 4: Seed the new BMan pool from the XSK fill ring.
	 *
	 * DPAA1_XSK_INITIAL_SEED = 8192 (spec sec 5.3): at 7 Gbps / 1500 B
	 * = 583 kpps the 2048-buffer old default drains in ~3.5 ms; 8192
	 * spans ~14 ms, enough headroom for the NAPI refill loop (patch 0077)
	 * to keep up under scheduling jitter.  Seed in batches of 32 so
	 * xsk_buff_alloc_batch() stays well within the XSK ring budget.
	 *
	 * A partial seed (< DPAA1_XSK_INITIAL_SEED / 2) is non-fatal; the
	 * xsk_bman_seed_short counter documents it and patch 0077's
	 * NAPI-hooked refill will top up on the next poll.
	 */
	seeded = 0;
	batch = DPAA1_XSK_INITIAL_SEED;
	while (seeded < batch) {
		struct xdp_buff *handles[32];
		struct bm_buffer bmbs[32];
		u32 want = min_t(u32, 32u, batch - seeded);

		n = xsk_buff_alloc_batch(pool, handles, want);
		if (!n)
			break;
		for (i = 0; i < n; i++) {
			dma_addr_t dma = xsk_buff_xdp_get_dma(handles[i]);
			/* Zero bmbs[i].data first: bman_release() memcpies entries
			 * 1..num-1 including their bpid bits into the RCR. Stack
			 * garbage in bpid produces BMan 'ErrInt: Invalid Command
			 * Verb'. Mirrors mainline dpaa_eth.c::dpaa_bp_add_8_bufs().
			 * Patch 0087.
			 */
			bmbs[i].data = 0;
			bm_buffer_set64(&bmbs[i], dma);
			/* 0103b: preserve the xdp_buff handle the BMan-release
			 * path would otherwise discard, keyed by head-index so
			 * the RX hook can Recover it from the bare DMA cookie.
			 */
			dpaa_xsk_chunk_record(priv, queue_id,
					      dpaa_xsk_chunk_head_index(pool,
								handles[i]),
					      handles[i]);
		}
		/* BMan RCR verb byte BUFCOUNT_MASK = 0x0f, HW-valid range 1..8
		 * only (bman.c:746 DPAA_ASSERT). Mirror the mainline dpaa_eth.c
		 * refill contract (8 at a time). Spec M3-3 step 6, patch 0086.
		 */
		{
			u32 chunk_off;
			for (chunk_off = 0; chunk_off < n; chunk_off += 8) {
				u8 chunk = (u8)min_t(u32, 8u, n - chunk_off);
				bman_release(bpool, &bmbs[chunk_off], chunk);
			}
		}
		seeded += n;
	}
	if (seeded < DPAA1_XSK_INITIAL_SEED / 2)
		priv->xsk_bman_seed_short++;

	/*
	 * Step 5 (xdp_rxq_info_reg_mem_model): Deferred to patch 0077.
	 *
	 * Registering MEM_TYPE_XSK_BUFF_POOL on the per-FQ xdp_rxq_info
	 * requires FQ context (struct dpaa_fq *).  The flavor module only
	 * receives dpaa_priv * here; the FQ context is available in the
	 * rx_hook path (patch 0077).  Per-FQ mem-model registration will
	 * be wired in 0077's bind path once the dedicated XSK BMan channel
	 * and NAPI context are brought up and the FQ pointer is known.
	 *
	 * Step 6: RCU-publish.
	 *
	 * After this store is visible to other CPUs, the NAPI RX hot-path
	 * (dpaa_eth_poll -> af_xdp_pool_rx_hook, patch 0077) will branch
	 * into the zero-copy RX path for this qband.  The ordering guarantee:
	 * rxq mem-model registered, BMan pool seeded, LIODN validated — THEN
	 * expose the pool pointer.
	 *
	 * Symmetrically, detach (patch 0076) will RCU-null this pointer and
	 * call synchronize_net() before touching any DMA mappings or BMan
	 * pool state.
	 */
rcu_assign_pointer(priv->xsk_pool[queue_id], pool);

	/* M3-3 step 7 sub-increment 5 (0103c): pre-arm the NEED_WAKEUP
	 * flag at attach time.  xsk_set_rx_need_wakeup() normally arms
	 * it in dpaa_eth_poll() under `if (cleaned < budget)`, but a
	 * flooded DQRR never enters that branch, so cached_need_wakeup
	 * stays 0 forever, xsk_sendmsg() skips xsk_wakeup(), and the
	 * ndo_xsk_wakeup → dpaa_eth_poll() → napi_refill chain never
	 * fires.  Without refill, the BMan XSK pool stays empty and
	 * the reprogrammed FMan port drops all ingress at hardware
	 * (circular deadlock).  Pre-arming here guarantees the first
	 * userspace sendto(MSG_DONTWAIT) always reaches xsk_wakeup.
	 */
	if (xsk_uses_need_wakeup(pool))
		xsk_set_rx_need_wakeup(pool);

/* 0103b: build the sorted dma_addr_t -> head-index lookup now that
 * the pool is DMA-mapped (every pool->heads[i].dma is populated and
 * stable for the pool's lifetime).  Non-fatal on -ENOMEM (the RX
 * hook then falls back to the skbuf path for every FD).  Freed in
 * detach.  Built BEFORE the reprogram-WRITE below so the reverse map
 * is ready the instant FMan starts DMAing into XSK chunks.
 */
dpaa_xsk_build_dma_index(priv, queue_id, pool);

/*
 * M3-3 step 1: bind a real NAPI context to this qband so
 * af_xdp_pool_xsk_wakeup() can schedule it on the userspace
 * sendto(MSG_DONTWAIT) wakeup path.  Until dedicated per-qband
 * BMan channels + per-qband NAPI contexts land (later M3-3
 * subpatch), DPAA1 has one NAPI per online CPU and any of them
 * will service this qband's RX FQs.  Pick the BSP CPU's NAPI
 * (cpu 0) — it always exists, and napi_schedule_irqoff() is
 * cross-CPU safe.  qmap[].cpu/.swp_id/.fqid_base remain 0 for
 * now; the cluster-aware pin lands with the dedicated channel
 * subpatch.  Spec sec 5.4 "Queue mapping correctness" item 3-5.
 */
{
struct dpaa_percpu_priv *pcpu;
int target_cpu;
int n_online = num_online_cpus();

if (unlikely(n_online <= 0))
target_cpu = 0;
else
target_cpu = (int)queue_id % n_online;

pcpu = per_cpu_ptr(priv->percpu_priv, target_cpu);
WRITE_ONCE(priv->qmap[queue_id].napi, &pcpu->np.napi);
WRITE_ONCE(priv->qmap[queue_id].cpu, target_cpu);
}

pr_err("ZCBIND: af_xdp_pool_attach OK pool=%px bpid=%u\n", pool, priv->xsk_bpid[queue_id]);
	priv->xsk_pool_attach_ok++;

/*
 * M3-3 step 7 sub-increment 2 (patch 0094): true-ZC RX arm
 * observability -- the READ side of mechanism 3 (reprogram the
 * FMan RX port's primary BPID), with NO datapath change.
 *
 * priv->xsk_bpid[queue_id] is the dedicated BPID just allocated
 * by bman_new_pool() (step 3 above) and seeded with XSK UMEM
 * chunks (step 4). priv->dpaa_bp->bpid is the kernel page-pool
 * BPID that the FMan RX port is CURRENTLY programmed to DMA into.
 *
 * For true ZC, sub-increment 4 will reprogram the FMan RX port's
 * primary pool from dpaa_bp->bpid to xsk_bpid[queue_id] (the
 * hardware-risky MURAM write -- spec sec 6.1.8 records three DUT
 * crashes from under-load XSK RX, so the write stays out of this
 * patch). Here we only OBSERVE that the two BPIDs differ, i.e.
 * the dedicated XSK pool got its own BPID and the reprogram would
 * have a meaningful, distinct target. We bump xsk_zc_rx_armed and
 * log the current-vs-target pair so the reprogram precondition is
 * verifiable from userspace (ethtool -S | grep xsk_zc_rx_armed)
 * BEFORE any FMan write touches live hardware.
 *
 * On default/vpp this path is unreached (no XDP_ZEROCOPY bind);
 * even under a future ZC consumer FMan keeps DMAing into the
 * kernel page pool, so copy-mode RX (patch 0089) is preserved
 * bit-for-bit until sub-increment 4 lands the reprogram.
 */
if (priv->dpaa_bp &&
    priv->xsk_bpid[queue_id] != (int)priv->dpaa_bp->bpid) {
priv->xsk_zc_rx_armed++;
dev_info(priv->net_dev->dev.parent,
 "af_xdp_pool: qband %u armed for true-ZC RX (xsk_bpid=%d, fman rx port primary bpid=%u)\n",
 queue_id, priv->xsk_bpid[queue_id],
 priv->dpaa_bp->bpid);

/* 0103g: register the per-band ZC rxq BEFORE the reprogram-WRITE.
 * The Recovered xdp_buffs are handed to bpf_prog_run_xdp() /
 * xdp_do_redirect(), whose XSK receive path (xsk_rcv_check)
 * dereferences xdp->rxq->dev.  xsk_buff_alloc() leaves xdp.rxq
 * NULL (drivers own that field), so without this registration the
 * first Recovered frame NULL-derefs in __xsk_map_redirect
 * (HW-captured serial Oops, 2026-06-09: pc __xsk_map_redirect,
 * lr xdp_do_redirect, af_xdp_pool_rx_hook in the call trace).
 * i40e idiom: reg + MEM_TYPE_XSK_BUFF_POOL mem model +
 * xsk_pool_set_rxq_info() stamping xdp.rxq into every pool buffer
 * once, so the hot rx_hook needs no per-frame assignment.  The
 * reprogram-WRITE below is gated on success -- no rxq, no ZC.
 */
{
int rqret;

rqret = xdp_rxq_info_reg(&priv->xsk_zc_rxq[queue_id],
 priv->net_dev, queue_id, 0);
if (!rqret) {
rqret = xdp_rxq_info_reg_mem_model(&priv->xsk_zc_rxq[queue_id],
   MEM_TYPE_XSK_BUFF_POOL, NULL);
if (rqret)
xdp_rxq_info_unreg(&priv->xsk_zc_rxq[queue_id]);
}
if (rqret) {
priv->xsk_pool_attach_fail++;
dev_warn(priv->net_dev->dev.parent,
 "af_xdp_pool: qband %u ZC rxq register failed (%d); true-ZC RX disabled for this band\n",
 queue_id, rqret);
rxp = NULL; /* gate: skip the reprogram-WRITE */
} else {
xsk_pool_set_rxq_info(pool, &priv->xsk_zc_rxq[queue_id]);
}
}

/*
 * 0103b reprogram-WRITE (mechanism 3, the hardware-risky half).
 * Point the live FMan RX port's primary BMan pool from the kernel
 * page-pool BPID (priv->dpaa_bp->bpid) at the dedicated XSK pool
 * BPID (priv->xsk_bpid[queue_id]) so FMan DMAs ingress frames
 * directly into XSK UMEM chunks.  fman_port_set_rx_bpool() rewrites
 * the BMI FMBM_REBM bpool register (0102 exported it); its contract
 * REQUIRES the RX port be disabled across the write, so we bracket
 * it with fman_port_disable()/fman_port_enable() (already exported,
 * fman_port.h:121/123).
 *
 * This is the INSEPARABLE partner of af_xdp_pool_rx_hook(): once
 * this write lands, FDs start carrying priv->xsk_bpid[queue_id] and
 * the rx_hook Recovers + redirects them into the XSKMAP.  The
 * 0103a reverse map (dpaa_xsk_build_dma_index above + the seed-loop
 * records) is already populated, so every chunk FMan DMAs into is
 * resolvable back to its xdp_buff.  On failure we roll the port
 * back to enabled (it keeps the kernel page-pool BPID) and bump
 * xsk_pool_attach_fail -- the band stays armed-but-not-reprogrammed
 * (xsk_zc_eligible will stay 0, the post-write success oracle).
 */
if (rxp) {
int wret;
struct fman *_fm2 = fman_bind(priv->mac_dev->fman_dev);
struct fman_pcd *_pcd = _fm2 ? fman_get_pcd(_fm2) : NULL;

/* Ensure per-port FM_CTL params page (+0x54) is valid before the
 * BPID reprogram — without it FE_ENTER ALLOCATE books workspace
 * at MURAM offset 0, corrupting MURAM on every ZC frame (F-072).
 */
if (_pcd)
	(void)fman_pcd_port_ensure_params_page(_pcd, rxp);

wret = fman_port_disable(rxp);
if (!wret) {
wret = fman_port_set_rx_bpool(rxp,
       (u8)priv->dpaa_bp->bpid,
       (u8)priv->xsk_bpid[queue_id]);
fman_port_enable(rxp);
}
if (wret) {
priv->xsk_pool_attach_fail++;
dev_warn(priv->net_dev->dev.parent,
 "af_xdp_pool: qband %u FMan RX-port BPID reprogram failed (%d); leaving kernel page-pool BPID, true-ZC RX disabled for this band\n",
 queue_id, wret);
} else {
dev_info(priv->net_dev->dev.parent,
 "af_xdp_pool: qband %u FMan RX port reprogrammed to XSK BPID %d -- true-ZC RX live\n",
 queue_id, priv->xsk_bpid[queue_id]);
}
}
}

return 0;

err_unmap:
	xsk_pool_dma_unmap(pool, DMA_ATTR_SKIP_CPU_SYNC);
	return ret;
}

/*
 * af_xdp_pool_xsk_pool_detach - tear down the AF_XDP zero-copy pool on an RX queue
 *
 * Patch 0076 (Phase 2 step 3, spec sec 5.3): the symmetric counterpart of
 * 0075b's xsk_pool_attach.  Executes the BMI quiescence sequence so the
 * FMan RX port has fully stopped dequeuing from the dedicated XSK BMan
 * pool BEFORE we tear that pool down or release the DMA mapping.
 *
 *  1.  rcu_assign_pointer(priv->xsk_pool[queue_id], NULL)
 *      Hide the pool pointer from new RX hot-path readers.
 *
 *  2.  synchronize_net()
 *      Wait for every in-flight NAPI poll on every CPU to finish
 *      dereferencing the old pointer.  After this returns, no new
 *      RX completion can deliver into the doomed BMan pool.
 *
 *  3.  fman_port_disable(rxp)
 *      Stop the FMan BMI from acquiring further buffers from BMan and
 *      from enqueuing frames into QMan.  Hardware quiescence point.
 *
 *  4.  DQRR drain (deferred to patch 0077/0080).
 *      The flavor module does not yet own dedicated XSK BMan channels
 *      and per-FQ NAPI contexts; once 0080 binds them, this step
 *      becomes "drain the channel's DQRR ring" via the standard
 *      qman_p_poll_dqrr() loop until empty.
 *
 *  5.  qman_retire_fq() per XSK RX FQ (deferred to patch 0080).
 *      Same dependency as step 4 — the flavor module does not yet own
 *      a list of per-qband FQs.  Without dedicated XSK FQs the only
 *      FQs feeding this qband are the default RX FQs, which are
 *      core-driver-owned and must not be retired here.
 *
 *  6.  BMan drain loop.
 *      Acquire every remaining bm_buffer from the dedicated pool in
 *      batches of 8 until the pool returns empty (bman_acquire <= 0).
 *      A bounded retry budget (16384 iterations) guards against an
 *      undisableable BMI continuing to refill the pool; on budget
 *      exhaustion we bump xsk_pool_detach_timeout and continue the
 *      teardown (do not leak the bpid).
 *
 *      Spec note (sec 5.3): the spec also calls for xsk_buff_free() on
 *      each drained buffer to return XSK chunks to the pool's free
 *      list.  That requires keeping a parallel xdp_buff* array per
 *      qband alongside the bm_buffer DMA addrs, which is a non-trivial
 *      memory tax on every attach (DPAA1_XSK_INITIAL_SEED = 8192
 *      pointers * 4 qbands * 2 SFP+ netdevs = 256 KB).  It is omitted
 *      from this patch on the explicit assumption that detach happens
 *      at XDP_UNBIND / socket close time, at which point the XSK core
 *      tears down the UMEM unconditionally — every alloc-batch'd chunk
 *      is reclaimed by UMEM teardown regardless of whether we called
 *      xsk_buff_free() first.  This is the same pattern the mainline
 *      AF_XDP examples use: bounded-buffer leak per detach is fine if
 *      the socket is closing, which is the only path that triggers
 *      detach in Phase 2.  If we later support BIND -> UNBIND -> BIND
 *      on the same UMEM (rebind without socket close) we will need to
 *      revisit this — track xsk_pool_detach_ok cumulative growth in
 *      ethtool stats to detect such a regression.
 *
 *  7.  xsk_pool_dma_unmap(pool, DMA_ATTR_SKIP_CPU_SYNC)
 *      Release the UMEM DMA mapping that 0075b's xsk_pool_dma_map()
 *      installed.  Mirrored attribute (skip CPU sync) for the
 *      CoreNet-coherent fabric.
 *
 *  8.  bman_free_pool() + clear xsk_bpool[]/xsk_bpid[].
 *      Release the dedicated BPID back to BMan.  Subsequent attach on
 *      this qband will allocate a fresh BPID via bman_new_pool().
 *
 *  9.  fman_port_enable(rxp)
 *      Bring the RX port back up.  Default RX FQs (core-driver-owned)
 *      can resume.  Symmetric to step 3.
 *
 * If any step fails, log the failure (counter bump) but continue the
 * teardown sequence — leaving a partially-disabled FMan port or an
 * orphaned BPID is worse than completing teardown with a counter
 * incremented.  This is the same fail-closed-but-keep-going pattern
 * used in the core dpaa_eth tear-down paths (dpaa_remove, dpaa_stop).
 */
static int af_xdp_pool_xsk_pool_detach(struct dpaa_priv *priv, u16 queue_id)
{
struct xsk_buff_pool *pool;
struct bman_pool *bpool;
struct fman_port *rxp;
struct bm_buffer bmbs[8];
int drained_total = 0;
int loops = 0;
int n;
int ret;

if (!priv)
return -EINVAL;
if (queue_id >= DPAA1_XSK_MAX_QBANDS ||
    queue_id >= priv->xsk_max_qbands)
return -EINVAL;

/*
 * Resolve the pool pointer we're going to detach BEFORE we null it
 * out, so the xsk_pool_dma_unmap() at step 7 has a valid argument.
 *
 * Serialization: the XSK core invokes the pool_detach netdev op from
 * xp_disable_drv_zc() under the per-netdev XDP mutex (see
 * net/xdp/xsk_buff_pool.c) — concurrent attach/detach on the same
 * queue_id cannot race here.  RCU readers in the NAPI hot-path
 * (patch 0077) are handled by the rcu_assign_pointer(NULL) +
 * synchronize_net() pair below.  rcu_dereference_check(1) suppresses
 * lockdep's RCU read-lock warning since we don't expose the XSK
 * core's mutex to lockdep here.
 */
pool = rcu_dereference_check(priv->xsk_pool[queue_id], 1);
if (!pool)
return -ENOENT;

bpool = priv->xsk_bpool[queue_id];

if (!priv->mac_dev || !priv->mac_dev->port[DPAA_MAC_PORT_RX]) {
/* Should be impossible if attach succeeded.  Bail without
 * touching FMan but still tear down the rest. */
rxp = NULL;
} else {
struct fman *_fm = fman_bind(priv->mac_dev->fman_dev);
rxp = _fm ? fman_port_lookup_rx(_fm, fman_port_get_id(
	priv->mac_dev->port[DPAA_MAC_PORT_RX])) : NULL;
}

/* Step 1: hide the pool pointer from new readers. */
rcu_assign_pointer(priv->xsk_pool[queue_id], NULL);

/* Step 2: wait for in-flight NAPI polls to drop their references. */
synchronize_net();

/* Step 3: stop the FMan BMI for this RX port. */
	if (rxp) {
		ret = fman_port_disable(rxp);
		if (ret) {
			/* Continue teardown even on failure — leaving an
			 * orphaned BPID is worse than a counter bump.
			 */
			priv->xsk_pool_detach_timeout++;
		}

		/* 0103g: tear down the per-band ZC rxq.  The pool pointer was
 * RCU-cleared earlier in detach, so the rx_hook can no longer
 * reach Recovered frames referencing this rxq.
 */
if (xdp_rxq_info_is_reg(&priv->xsk_zc_rxq[queue_id]))
xdp_rxq_info_unreg(&priv->xsk_zc_rxq[queue_id]);

/* M3-3 step 7 sub-increment 6 (0103c part 2): restore the
		 * FMan RX port's primary BPID from the XSK pool back to
		 * the kernel page-pool BPID BEFORE we free bman_new_pool()'s
		 * handle and BEFORE we re-enable the port.  Without this,
		 * the port stays at the now-freed XSK BPID after detach,
		 * FMan DMAs into an orphaned pool, kernel default FQs get
		 * no buffers, and subsequent attach fails with
		 * "old_bpid 3 not present in RX pool table" because the
		 * HW register still holds the stale XSK BPID value.
		 *
		 * The port is disabled (BMI paused) across the write, so
		 * this is atomic from the hardware's perspective.
		 */
		if (priv->dpaa_bp && bpool) {
			u32 kernel_bpid = priv->dpaa_bp->bpid;
			int wret;

			wret = fman_port_set_rx_bpool(rxp,
					priv->xsk_bpid[queue_id],
					kernel_bpid);
			if (wret)
				dev_warn(priv->net_dev->dev.parent,
					"af_xdp_pool: qband %u BPID restore %d -> %u failed (%d); subsequent attach will need cold reboot\n",
					queue_id,
					priv->xsk_bpid[queue_id],
					kernel_bpid, wret);
			else
				dev_info(priv->net_dev->dev.parent,
					"af_xdp_pool: qband %u RX port BPID restored %d -> %u\n",
					queue_id,
					priv->xsk_bpid[queue_id],
					kernel_bpid);
		}
	}

/*
 * Steps 4 + 5 (DQRR drain, qman_retire_fq): deferred to patch 0080
 * which adds dedicated XSK BMan channels and per-qband FQ ownership.
 * With the FMan port disabled (step 3) no new completions can flow,
 * so any in-flight DQRR entries on default RX FQs will be drained
 * by the core driver's existing NAPI loop after this detach returns.
 */

/*
 * Step 6: drain the dedicated BMan pool.
 *
 * Loop bound = 16384 iterations (each iteration drains <= 8 buffers,
 * upper-bounded total = 131072 buffers — 16x the initial seed of
 * 8192 to tolerate refill activity that may have been in flight when
 * step 3 disabled the port).
 */
if (bpool) {
while (loops++ < 16384) {
n = bman_acquire(bpool, bmbs, 8);
if (n <= 0)
break;
drained_total += n;
}
if (loops >= 16384) {
/* BMan pool refused to drain.  Bump the timeout counter
 * and proceed — bman_free_pool() will likely fail too,
 * but we MUST keep going to avoid an undeletable BPID. */
priv->xsk_pool_detach_timeout++;
}
}

/* Step 7: release the UMEM DMA mapping. */
xsk_pool_dma_unmap(pool, DMA_ATTR_SKIP_CPU_SYNC);

/* Step 8: free the BMan pool and clear the per-qband slots. */
if (bpool)
bman_free_pool(bpool);
priv->xsk_bpool[queue_id] = NULL;
priv->xsk_bpid[queue_id]  = 0;

/* 0103a: free the Recover reverse-map. */
kfree(priv->xsk_chunk_map[queue_id]);
priv->xsk_chunk_map[queue_id]     = NULL;
priv->xsk_chunk_map_len[queue_id] = 0;

/* 0103b: free the sorted dma -> head-index lookup. */
kfree(priv->xsk_chunk_dma[queue_id]);
kfree(priv->xsk_chunk_dma_idx[queue_id]);
priv->xsk_chunk_dma[queue_id]     = NULL;
priv->xsk_chunk_dma_idx[queue_id] = NULL;
priv->xsk_chunk_dma_cnt[queue_id] = 0;

/* Step 9: re-enable the FMan RX port for default (non-XSK) FQs. */
if (rxp) {
ret = fman_port_enable(rxp);
if (ret) {
/* RX port stuck disabled.  Bump the timeout counter; the
 * caller (XSK core) will get success anyway because the
 * pool is now detached.  The next pool_attach on this
 * netdev will fail at fman_port_get_liodn(). */
priv->xsk_pool_detach_timeout++;
}
}

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
 * af_xdp_pool_napi_refill - top up BMan pool from XSK fill ring
 *
 * M3-3 step 4 (patch 0084, spec sec 6.1.3). Called from
 * dpaa_eth_poll() tail inside the same rcu_read_lock() region as
 * xsk_set_rx_need_wakeup, AFTER napi_complete_done() and BEFORE
 * the QMan DQRR interrupt is re-armed.
 *
 * For every bound qband (priv->xsk_bpool[qband] non-NULL), measure
 * BMan depth. If below DPAA1_XSK_REFILL_THRESHOLD (256), allocate
 * a batch of free chunks from the XSK fill ring via
 * xsk_buff_alloc_batch() and release them into the BMan pool. If
 * the BMan pool is critically drained (below DPAA1_XSK_MIN_DEPTH =
 * 64), bump priv->xsk_bman_starve and use a larger batch (256 vs
 * 32) to recover faster.
 *
 * The 256-entry handles[] / bmbs[] stack arrays bound the per-call
 * cost to a single cache-line-friendly burst. bman_release()
 * internally serialises 8 at a time, so the burst size is a
 * scheduling rather than a hardware constraint.
 *
 * Strictly NAPI-context. v2 single-CPU pinning: only the CPU that
 * owns the qband (priv->qmap[q].cpu, wired by M3-3 step 2a) runs
 * the xsk_buff_alloc_batch + bman_release body for that qband.
 * Other CPUs short-circuit via the early-return below. This makes
 * each qband's XSK fill ring single-writer (no cursor race) and
 * eliminates the BMan "Invalid Command Verb" hang observed with v1.
 *
 * No additional locking required:
 *   - priv->xsk_bpool[] is mutated only at attach/detach time,
 *     which is rtnl-serialised; the dpaa_eth_poll() reader sees
 *     a stable pointer.
 *   - priv->xsk_pool[] is RCU-protected; rcu_dereference inside
 *     the caller's read-section.
 *   - bman_release() is NAPI-safe per soc/fsl/bman.h.
 *   - priv->qmap[q].cpu is stable for the netdev's lifetime once
 *     xsk_pool_attach() has run (M3-3 step 2a invariant).
 *
 * Note: the public BMan API does NOT expose a pool depth query,
 * so depth-driven gating (DPAA1_XSK_REFILL_THRESHOLD / MIN_DEPTH)
 * is not used here. Refill is unconditional, naturally rate-
 * limited by the XSK fill ring -- xsk_buff_alloc_batch() returns
 * 0 when userspace has not deposited any free chunks, in which
 * case we skip bman_release() and the counter does not increment.
 */
static void af_xdp_pool_napi_refill(struct dpaa_priv *priv)
{
struct xdp_buff *handles[32];
struct bm_buffer bmbs[32];
struct xsk_buff_pool *pool;
struct bman_pool *bpool;
bool dup;
u32 n, i;
u16 q;

if (!priv)
return;

for (q = 0; q < priv->xsk_max_qbands && q < DPAA1_XSK_MAX_QBANDS; q++) {
bpool = priv->xsk_bpool[q];
if (!bpool)
continue;
pool = rcu_dereference(priv->xsk_pool[q]);
if (!pool)
continue;

/* v2: single-CPU pin. Only the qband-owner CPU drives this
 * qband's XSK fill-ring -> BMan refill. Other CPUs skip,
 * making each qband's fill ring single-producer.
 */
if (raw_smp_processor_id() != priv->qmap[q].cpu)
continue;

n = xsk_buff_alloc_batch(pool, handles, ARRAY_SIZE(handles));
dup = false;
for (i = 0; i < n; i++) {
dma_addr_t dma = xsk_buff_xdp_get_dma(handles[i]);
u32 j;

/* M3-3 step 7 sub-increment 3 GATE (patch 0095): FILL-ring
 * double-release guard. Scan the addresses already filled in
 * THIS batch for the same 64-bit DMA cookie. A duplicate means
 * the XSK FILL ring's single-producer cursor is inconsistent
 * for this fill cycle (spec sec 6.1.8 root cause): the same
 * UMEM chunk was deposited twice. Releasing it into BMan twice
 * is a double-free that, once sub-increment 4 makes FMan DMA
 * directly into XSK chunks, lets FMan overwrite a chunk
 * userspace still believes is free. Block the whole round
 * (drop > corrupt) and let the next NAPI tail refill cleanly.
 */
for (j = 0; j < i; j++) {
if (bm_buffer_get64(&bmbs[j]) == (u64)dma) {
dup = true;
break;
}
}
/* Zero bmbs[i].data first; see attach-time seed loop comment.
 * Patch 0087.
 */
bmbs[i].data = 0;
bm_buffer_set64(&bmbs[i], dma);
bm_buffer_set_bpid(&bmbs[i], bman_get_bpid(bpool));
/* 0103b: record-on-FILL so the RX hook can Recover this
 * chunk's xdp_buff from its bare DMA cookie.  Keyed by
 * head-index (stable for the pool's lifetime).
 */
dpaa_xsk_chunk_record(priv, q,
      dpaa_xsk_chunk_head_index(pool, handles[i]),
      handles[i]);
if (dup)
break;
}
if (dup) {
priv->xsk_fill_guard_block++;
dev_warn_ratelimited(priv->net_dev->dev.parent,
 "af_xdp_pool: qband %u FILL-ring duplicate chunk detected; round dropped (xsk_fill_guard_block), sub-increment-4 reprogram must stay disabled\n",
 q);
} else if (n) {
/* Chunk bman_release() in 8-buffer batches; HW BUFCOUNT field is
 * 4 bits and rejects num > 8 as Invalid Command Verb. XSK-side
 * counter still bumps once per non-empty alloc batch. Mirror of
 * attach-time seed loop. Spec M3-3 step 6, patch 0086.
 */
{
u32 chunk_off;
for (chunk_off = 0; chunk_off < n; chunk_off += 8) {
u8 chunk = (u8)min_t(u32, 8u, n - chunk_off);
bman_release(bpool, &bmbs[chunk_off], chunk);
}
}
priv->xsk_bman_refill_batches++;
}
}
}

/*
 * af_xdp_pool_napi_tx_zc - drain XSK Tx ring into DPAA1 egress FQs
 *
 * M3-3 step 5 (patch 0085, spec sec 6.1.4). Called from
 * dpaa_eth_poll() tail inside the same rcu_read_lock() region as
 * napi_refill, AFTER napi_complete_done() and BEFORE the QMan
 * DQRR interrupt is re-armed.
 *
 * Per bound qband (priv->xsk_bpool[qband] non-NULL):
 *   1. atomic_read(xsk_tx_inflight[qband]). If >= DPAA1_XSK_TX_MAX_INFLIGHT
 *      (1024) -> bump xsk_tx_backpressure and skip.
 *   2. budget = min(MAX_INFLIGHT - inflight, 64).
 *   3. n = xsk_tx_peek_release_desc_batch(pool, budget).
 *   4. For each pool->tx_descs[i] in [0..n):
 *        - dma = xsk_buff_raw_get_dma(pool, d.addr)
 *        - Build qm_fd: contig 0/len, addr=dma, bpid=xsk_bpid[q]
 *          (the bpid is our TxConf claim marker; FCO NOT set --
 *          the XSK UMEM chunk is NOT BMan-owned).
 *        - egress_fq = priv->egress_fqs[raw_smp_processor_id() %
 *          dpaa_max_num_txqs()].
 *        - qman_enqueue retry loop (DPAA_ENQUEUE_RETRIES on -EBUSY).
 *          Hard fail: bump xsk_tx_backpressure, break inner loop.
 *        - Success: atomic_inc(xsk_tx_inflight[q]), submitted++.
 *   5. If submitted > 0: priv->xsk_tx_zc_submit++.
 *
 * Inflight decrement happens in af_xdp_pool_tx_conf_zc(). xsk_tx_completed(
 * pool, 1) is called there per claimed FD.
 *
 * NAPI-context. xsk_tx_inflight[] is atomic_t to be lockless against the
 * TxConf path which may run on a different CPU.
 */
static void af_xdp_pool_napi_tx_zc(struct dpaa_priv *priv)
{
struct xsk_buff_pool *pool;
struct qman_fq *egress_fq;
struct xdp_desc *descs;
struct qm_fd fd;
dma_addr_t dma;
u32 budget, n, i, submitted;
int inflight, j, err;
u16 q;

if (!priv)
return;

for (q = 0; q < priv->xsk_max_qbands && q < DPAA1_XSK_MAX_QBANDS; q++) {
if (!priv->xsk_bpool[q])
continue;
pool = rcu_dereference(priv->xsk_pool[q]);
if (!pool)
continue;

/* v2: single-CPU pin. Only the qband-owner CPU drains this
 * qband's XSK Tx ring -> QMan enqueue. Other CPUs skip,
 * making each qband's Tx ring single-consumer (matches the
 * XSK SPSC cursor contract). Mirror of the 0084v2 fix on the
 * fill-ring side; same race, same one-line fix. The TxConf
 * side (af_xdp_pool_tx_conf_zc) is intentionally NOT pinned
 * — its bpid-claim scheme is designed for cross-CPU demux.
 */
if (raw_smp_processor_id() != priv->qmap[q].cpu)
continue;

inflight = atomic_read(&priv->xsk_tx_inflight[q]);
if (inflight >= DPAA1_XSK_TX_MAX_INFLIGHT) {
priv->xsk_tx_backpressure++;
continue;
}

budget = (u32)(DPAA1_XSK_TX_MAX_INFLIGHT - inflight);
if (budget > 64)
budget = 64;

n = xsk_tx_peek_release_desc_batch(pool, budget);
if (!n)
continue;

descs = pool->tx_descs;
submitted = 0;
for (i = 0; i < n; i++) {
dma = xsk_buff_raw_get_dma(pool, descs[i].addr);

memset(&fd, 0, sizeof(fd));
qm_fd_addr_set64(&fd, dma);
qm_fd_set_contig(&fd, 0, (int)descs[i].len);
fd.bpid = priv->xsk_bpid[q];
/* No FM_FD_CMD_FCO: XSK UMEM chunk MUST NOT be released
 * to BMan. Release goes through xsk_tx_completed() in
 * the TxConf hook.
 */

egress_fq = priv->egress_fqs[raw_smp_processor_id() %
     dpaa_max_num_txqs()];

err = -EBUSY;
/* DPAA_ENQUEUE_RETRIES is a #define private to dpaa_eth.c
 * (value 100000); inline the same constant here rather than
 * exporting it through dpaa_eth.h. The value is a stable upper
 * bound on QMan EQCR slot-refill latency under congestion --
 * it has not changed since the driver was upstreamed.
 */
for (j = 0; j < 100000; j++) {
err = qman_enqueue(egress_fq, &fd);
if (err != -EBUSY)
break;
}
if (err) {
priv->xsk_tx_backpressure++;
break;
}
atomic_inc(&priv->xsk_tx_inflight[q]);
submitted++;
}

if (submitted)
priv->xsk_tx_zc_submit++;
}
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

/*
 * af_xdp_pool_tx_conf_zc - claim XSK-bpid TxConf FDs
 *
 * M3-3 step 5 (patch 0085, spec sec 6.1.4 round-trip closure).
 * Called from dpaa_tx_conf() BEFORE the skb-cleanup path. FD's bpid is
 * our claim marker (napi_tx_zc set fd.bpid = priv->xsk_bpid[q]).
 *
 * Returns true if FD claimed (caller MUST NOT run skb cleanup); false
 * otherwise.
 *
 * Per claimed FD:
 *   - atomic_dec(xsk_tx_inflight[q]) to free a budget slot.
 *   - xsk_tx_completed(pool, 1) to advance userspace CQ head one slot.
 *   - priv->xsk_tx_conf_zc++ for observability.
 *
 * If pool detached between submit and confirm (XDP_UNBIND while in
 * flight), skip xsk_tx_completed() but still atomic_dec -- inflight
 * MUST drop or the next attach starts polluted.
 */
static bool af_xdp_pool_tx_conf_zc(struct dpaa_priv *priv,
   const struct qm_fd *fd)
{
struct xsk_buff_pool *pool;
u8 bpid;
u16 q;

if (!priv || !fd)
return false;

bpid = fd->bpid;
for (q = 0; q < priv->xsk_max_qbands && q < DPAA1_XSK_MAX_QBANDS; q++) {
if (!priv->xsk_bpool[q])
continue;
if (priv->xsk_bpid[q] != bpid)
continue;

atomic_dec(&priv->xsk_tx_inflight[q]);
pool = rcu_dereference(priv->xsk_pool[q]);
if (pool)
xsk_tx_completed(pool, 1);
priv->xsk_tx_conf_zc++;
return true;
}
return false;
}

/* ---------- Ops tables ---------- */

static const struct dpaa_qmgmt_ops af_xdp_pool_qmgmt_ops = {
.alloc_rx_fqs           = af_xdp_pool_alloc_rx_fqs,
.rx_hook                = af_xdp_pool_rx_hook,
.xsk_pool_attach        = af_xdp_pool_xsk_pool_attach,
.xsk_pool_detach        = af_xdp_pool_xsk_pool_detach,
.xsk_wakeup             = af_xdp_pool_xsk_wakeup,
.xsk_set_rx_need_wakeup = af_xdp_pool_xsk_set_rx_need_wakeup,
.napi_refill            = af_xdp_pool_napi_refill,
.napi_tx_zc             = af_xdp_pool_napi_tx_zc,
.xsk_set_tx_need_wakeup = af_xdp_pool_xsk_set_tx_need_wakeup,
.tx_conf_zc             = af_xdp_pool_tx_conf_zc,
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

pr_info("af_xdp_pool: registered (skeleton, all callbacks stubbed -EOPNOTSUPP)\n");
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
MODULE_DESCRIPTION("DPAA1 AF_XDP zero-copy pool flavor module (skeleton)");
MODULE_LICENSE("GPL v2");