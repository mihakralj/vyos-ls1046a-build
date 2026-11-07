"""F-291: propagate FE disarm errors and fence uncertain port state.

An FE workspace and its owner must precede KeyGen dispatch.  A failed
lower-layer operation must not be reported as a successful FE port release.
This does not establish walker quiescence, owner withdrawal, or parent-device
lifetime.
Serialize FE lifecycle operations with flow publication, and refuse production
flow publication without an armed port and an owned per-port table.
Fail an arm before dispatch if its per-port table cannot be allocated.
Disable the unsafe diagnostic recovery and full-disengage paths until their
workspace ownership and hardware exclusion contracts are established.
Refuse debugfs FE port and arm mutations until their ownership is established.
Refuse debugfs INV0 SYNC writes until the global request is coordinated.
Conservatively refuse port disarm while any table has live or uncertain owners.
Retain the workspace if its params-page owner or detach readback is uncertain.
Retain an incomplete shared FE chain and refuse every later engage.
Refuse an unowned FE pool and a built chain missing its ENQ object.
Fence debugfs mutations while tracked or retained FE owners exist.
Keep an armed diagnostic observer's pool and hash object intact until disarm.
"""

import sys

FM = "drivers/net/ethernet/freescale/fman/"
KG = FM + "fman_pcd_kg.c"
PCD = FM + "fman_pcd.c"
PORT = FM + "fman_port.c"
PORT_H = FM + "fman_port.h"
HDR = "include/linux/fsl/fman_pcd.h"

DEBUG_CHAIN_HELPER = """\
/* F-291: fe_lock protects both this check and the diagnostic mutation. */
static bool fman_pcd_fe_debug_mutation_blocked_locked(struct fman_pcd *pcd)
{
\tlockdep_assert_held(&pcd->fe_lock);
\tif (pcd->fe_vm_chain_built || pcd->fe_vm_chain_faulted ||
\t    pcd->fe_arming_v4_table ||
\t    !bitmap_empty(pcd->fe_port_armed, 0x28) ||
\t    !bitmap_empty(pcd->fe_port_faulted, 0x28)) {
\t\tpr_warn_ratelimited("fman_pcd: debugfs FE mutation refused; chain ownership retained\\n");
\t\treturn true;
\t}
\treturn false;
}

"""

DEBUG_CHAIN_GUARD = """\
\tif (fman_pcd_fe_debug_mutation_blocked_locked(pcd)) {
\t\tmutex_unlock(&pcd->fe_lock);
\t\treturn -ESHUTDOWN;
\t}
"""

DEBUG_OBSERVER_GUARD = """\
\tif (pcd->fe_obs_armed) {
\t\tpr_warn_ratelimited("fman_pcd: debugfs FE mutation refused; observer active\\n");
\t\tmutex_unlock(&pcd->fe_lock);
\t\treturn -EBUSY;
\t}
"""

OBS_DISARM_PREFIX = """\
\tif (!pcd->fe_obs_armed)
\t\treturn 0;
\tif (!muram)
\t\treturn -EINVAL;

"""

OBS_DISARM_OWNER_GUARD = """\
\tif (!pcd->fe_refcount || !pcd->fe_hash_off) {
\t\tpr_err("fman_pcd: observer pool or hash missing; disarm refused\\n");
\t\treturn -ESHUTDOWN;
\t}

"""

DEBUG_CHAIN_WRITERS = (
    "fman_pcd_fe_singletons_write", "fman_pcd_fe_enq_write",
    "fman_pcd_fe_enter_write", "fman_pcd_fe_hashfe_write",
    "fman_pcd_fe_obs_write", "fman_pcd_fe_group_write",
    "fman_pcd_ehash_write", "fman_pcd_fe_flow_write",
)

POOL_GET = """\
static int fman_pcd_fe_pool_get_locked(struct fman_pcd *pcd)
{
\tint err;

\tif (pcd->fe_refcount == 0) {
\t\terr = fman_pcd_fe_pool_alloc(pcd);
\t\tif (err)
\t\t\treturn err;
\t}
\tpcd->fe_refcount++;
\treturn 0;
}

static int fman_pcd_fe_pool_get(struct fman_pcd *pcd)
{
\tint err;

\tmutex_lock(&pcd->fe_lock);
\tif (fman_pcd_fe_debug_mutation_blocked_locked(pcd)) {
\t\terr = -ESHUTDOWN;
\t} else if (pcd->fe_obs_armed) {
\t\tpr_warn_ratelimited("fman_pcd: debugfs FE mutation refused; observer active\\n");
\t\terr = -EBUSY;
\t} else {
\t\terr = fman_pcd_fe_pool_get_locked(pcd);
\t}
\tmutex_unlock(&pcd->fe_lock);
\treturn err;
}

"""

POOL_PUT = """\
static int fman_pcd_fe_pool_put(struct fman_pcd *pcd)
{
\tint err = 0;

\tmutex_lock(&pcd->fe_lock);
\tif (fman_pcd_fe_debug_mutation_blocked_locked(pcd)) {
\t\terr = -ESHUTDOWN;
\t} else if (pcd->fe_obs_armed) {
\t\tpr_warn_ratelimited("fman_pcd: debugfs FE mutation refused; observer active\\n");
\t\terr = -EBUSY;
\t} else if (WARN_ON(pcd->fe_refcount == 0)) {
\t\terr = -EINVAL;
\t} else if (--pcd->fe_refcount == 0) {
\t\tfman_pcd_fe_pool_free(pcd);
\t}
\tmutex_unlock(&pcd->fe_lock);
\treturn err;
}

"""

ENGAGE_WRAPPER = """\
int fman_pcd_fe_engage_profile(struct fman *fm, u8 hw_port_id,
\t\t\t       u32 enq_fqid, enum fman_pcd_fe_profile profile)
{
\tstruct fman_pcd *pcd;
\tint err;

\tif (!fm || hw_port_id < 0x08 || hw_port_id >= 0x28 ||
\t    profile >= FMAN_PCD_FE_PROFILE_NR)
\t\treturn -EINVAL;
\tpcd = fman_get_pcd(fm);
\tif (!pcd)
\t\treturn -ENXIO;
\tmutex_lock(&pcd->fe_lock);
\terr = __fman_pcd_fe_engage_profile_locked(fm, hw_port_id, enq_fqid,
\t\t\t\t\t\t    profile);
\tmutex_unlock(&pcd->fe_lock);
\treturn err;
}
"""

DISENGAGE_WRAPPER = """\
int fman_pcd_fe_disengage(struct fman *fm, u8 hw_port_id)
{
\tstruct fman_pcd *pcd;
\tint err;

\tif (!fm)
\t\treturn -EINVAL;
\tpcd = fman_get_pcd(fm);
\tif (!pcd)
\t\treturn -ENXIO;
\tif (hw_port_id < 0x08 || hw_port_id >= 0x28)
\t\treturn -EINVAL;
\tmutex_lock(&pcd->fe_lock);
\terr = __fman_pcd_fe_disengage_locked(fm, hw_port_id);
\tmutex_unlock(&pcd->fe_lock);
\treturn err;
}
"""

DISENGAGE_PREPARED = """\
/*
 * The callback runs under fe_lock.  It must not call an FE API or take a
 * lock whose holder can enter an FE API.  A preflight error leaves it uncalled.
 */
int fman_pcd_fe_disengage_prepared(struct fman *fm, u8 hw_port_id,
\t\t\t\t    int (*prepare)(void *), void *data)
{
\tstruct fman_pcd *pcd;
\tint err;

\tif (!fm || !prepare || hw_port_id < 0x08 || hw_port_id >= 0x28)
\t\treturn -EINVAL;
\tpcd = fman_get_pcd(fm);
\tif (!pcd)
\t\treturn -ENXIO;
\tmutex_lock(&pcd->fe_lock);
\terr = fman_pcd_fe_disarm_preflight_locked(pcd, hw_port_id);
\tif (err)
\t\tgoto out;
\tif (!test_bit(hw_port_id, pcd->fe_port_armed)) {
\t\terr = -ENODEV;
\t\tgoto out;
\t}
\terr = prepare(data);
\tif (!err)
\t\terr = __fman_pcd_fe_disengage_locked(fm, hw_port_id);
out:
\tmutex_unlock(&pcd->fe_lock);
\treturn err;
}
EXPORT_SYMBOL_GPL(fman_pcd_fe_disengage_prepared);

"""

RECOVERY_DISABLED = """\
/* F-291: recovery disabled until workspace ownership and quiescence are proven. */
int fman_pcd_port_recover(struct fman_pcd *pcd, u8 port_id)
{
\tif (!pcd || port_id < 0x08 || port_id >= 0x28)
\t\treturn -EINVAL;
\tpr_warn_ratelimited("fman_pcd: FE recovery disabled for port 0x%02x; cold boot required\\n",
\t\t\t    port_id);
\treturn -EOPNOTSUPP;
}

"""

FULL_DISENGAGE_DISABLED = """\
/* F-291: do not partially disengage before refusing unsafe recovery. */
static ssize_t fman_pcd_fe_disengage_full_write(struct file *filp,
    const char __user *buf, size_t count, loff_t *offp)
{
    char kbuf[16];
    unsigned int port_id;

    if (!filp->private_data)
        return -EINVAL;
    if (count >= sizeof(kbuf))
        count = sizeof(kbuf) - 1;
    if (copy_from_user(kbuf, buf, count))
        return -EFAULT;
    kbuf[count] = '\\0';
    if (sscanf(kbuf, "%x", &port_id) != 1 ||
        port_id < 0x08 || port_id >= 0x28)
        return -EINVAL;
    pr_warn_ratelimited("fman_pcd: full FE disengage disabled for port 0x%02x\\n",
                        port_id);
    return -EOPNOTSUPP;
}

"""

DEBUG_PORT_DISABLED = """\
/* F-291: debugfs FE port mutations disabled until ownership is proven. */
static ssize_t fman_pcd_fe_port_write(struct file *file,
\t\t\t\t      const char __user *ubuf,
\t\t\t\t      size_t count, loff_t *ppos)
{
\tstruct seq_file *s = file->private_data;
\tchar buf[32];
\tu8 port_id;

\tif (!s || !s->private || !count || count >= sizeof(buf))
\t\treturn -EINVAL;
\tif (copy_from_user(buf, ubuf, count))
\t\treturn -EFAULT;
\tbuf[count] = '\\0';
\tif (sscanf(buf, "set %hhx", &port_id) != 1 &&
\t    sscanf(buf, "del %hhx", &port_id) != 1)
\t\treturn -EINVAL;
\tpr_warn_ratelimited("fman_pcd: debugfs FE port changes disabled; ownership unresolved\\n");
\treturn -EOPNOTSUPP;
}

"""

DEBUG_ARM_DISABLED = """\
/* F-291: debugfs FE arm mutations disabled until ownership is proven. */
static ssize_t fman_pcd_fe_arm_write(struct file *file,
\t\t\t\t     const char __user *ubuf,
\t\t\t\t     size_t count, loff_t *ppos)
{
\tstruct seq_file *s = file->private_data;
\tchar buf[64];

\tif (!s || !s->private || !count || count >= sizeof(buf))
\t\treturn -EINVAL;
\tif (copy_from_user(buf, ubuf, count))
\t\treturn -EFAULT;
\tbuf[count] = '\\0';
\tif (strncmp(buf, "engage ", 7) && strncmp(buf, "disengage ", 10))
\t\treturn -EINVAL;
\tpr_warn_ratelimited("fman_pcd: debugfs FE arm changes disabled; ownership unresolved\\n");
\treturn -EOPNOTSUPP;
}
"""

DEBUG_ARM_SHOW = """\
static int fman_pcd_fe_arm_show(struct seq_file *s, void *unused)
{
\tstruct fman_pcd *pcd = s->private;
\tstruct fman_pcd_fe_port *fp;
\tbool any = false, unresolved;
\tint i;

\tmutex_lock(&pcd->fe_lock);
\tseq_printf(s, "fe_pool engaged:  %s\\n",
\t\t   pcd->fe_refcount ? "YES" : "NO");
\tseq_printf(s, "FE_ENTER root AD: 0x%lx\\n", pcd->fe_root_ad_off);
\tseq_printf(s, "MISS FQID: 0x%x\\n", pcd->fe_miss_fqid);
\tseq_puts(s, "Armed ports:");
\tfor (i = 0; i < 0x28; i++) {
\t\tif (test_bit(i, pcd->fe_port_armed)) {
\t\t\tseq_printf(s, " 0x%02x", i);
\t\t\tany = true;
\t\t}
\t}
\tif (!any)
\t\tseq_puts(s, " (none)");
\tseq_putc(s, '\\n');
\tseq_puts(s, "Faulted ports:");
\tany = false;
\tfor (i = 0; i < 0x28; i++) {
\t\tif (test_bit(i, pcd->fe_port_faulted)) {
\t\t\tseq_printf(s, " 0x%02x", i);
\t\t\tany = true;
\t\t}
\t}
\tif (!any)
\t\tseq_puts(s, " (none)");
\tseq_putc(s, '\\n');
\tunresolved = pcd->fe_vm_chain_faulted || pcd->fe_arming_v4_table ||
\t\t     !bitmap_empty(pcd->fe_port_faulted, 0x28);
\tfor (i = 0; i < 0x28; i++) {
\t\tbool armed = test_bit(i, pcd->fe_port_armed);

\t\tfp = fman_pcd_fe_port_find(pcd, (u8)i);
\t\tif ((fp && !armed) || (armed && (!fp || !fp->table_v4)))
\t\t\tunresolved = true;
\t}
\tseq_printf(s, "FE fault/staging: %s\\n",
\t\t   unresolved ? "yes" : "no");
\tmutex_unlock(&pcd->fe_lock);
\tseq_puts(s, "debugfs FE arm changes disabled: ownership unresolved\\n");
\treturn 0;
}

"""

DEBUG_EXTC_DISABLED = """\
/* F-291: debugfs INV0 sync disabled until global ownership is proven. */
static ssize_t fman_pcd_fe_extc_write(struct file *file,
\t\t\t\t      const char __user *ubuf,
\t\t\t\t      size_t count, loff_t *ppos)
{
\tstruct seq_file *s = file->private_data;
\tchar buf[16];

\tif (!s || !s->private || !count || count >= sizeof(buf))
\t\treturn -EINVAL;
\tif (copy_from_user(buf, ubuf, count))
\t\treturn -EFAULT;
\tbuf[count] = '\\0';
\tif (strncmp(buf, "sync", 4))
\t\treturn -EINVAL;
\tpr_warn_ratelimited("fman_pcd: debugfs INV0 sync disabled; global ownership unresolved\\n");
\treturn -EOPNOTSUPP;
}

"""

ENQ_CONTEXT_DESCRIPTOR_GUARD = """\
\t/* The HIT context is built in the separate per-flow DMA buffer by
\t * fman_pcd_ehash_add_key().  enq->muram_off points at the ENQ FE
\t * descriptor, whose word 3 is the next-FE offset; do not write context
\t * words into that descriptor.
\t */
"""

VM_CHAIN_PRECHECK = """\
\t/* F-291: a partial global chain has no safely reusable owner. */
\tif (pcd->fe_vm_chain_faulted) {
\t\tpr_err("fman_pcd: incomplete FE chain retained; engage refused\\n");
\t\treturn -ESHUTDOWN;
\t}
\tif (pcd->fe_vm_chain_built && list_empty(&pcd->fe_enq)) {
\t\tpcd->fe_vm_chain_faulted = true;
\t\tpr_err("fman_pcd: built FE chain has no ENQ; state retained\\n");
\t\treturn -ESHUTDOWN;
\t}
\tif (pcd->fe_obs_armed) {
\t\tpr_err("fman_pcd: diagnostic observer active; engage refused\\n");
\t\treturn -EBUSY;
\t}
\tif (!pcd->fe_vm_chain_built &&
\t    (pcd->fe_refcount || !list_empty(&pcd->fe_ehash_tables))) {
\t\tpcd->fe_vm_chain_faulted = true;
\t\tpr_err("fman_pcd: unowned FE pool or table retained; engage refused\\n");
\t\treturn -ESHUTDOWN;
\t}
"""

VM_CHAIN_BUILD = """\
\tif (!pcd->fe_vm_chain_built) {
\t\terr = __fman_pcd_fe_build_vm_chain(pcd);
\t\tif (err) {
\t\t\tpcd->fe_vm_chain_faulted = true;
\t\t\tpr_err("fman_pcd: FE VM chain build failed: %d; state retained\\n", err);
\t\t\treturn err;
\t\t}
\t\tpcd->fe_vm_chain_built = true;
\t}
"""

PORT_SET = """\
#define FMAN_PCD_FE_PARAMS_PAGE_SIZE 256

static int fman_pcd_fe_port_set(struct fman_pcd *pcd, u8 port_id)
{
\tstruct fman *fman = fman_pcd_get_fman(pcd);
\tstruct muram_info *muram = fman_get_muram(pcd->fman);
\tstruct fman_pcd_fe_port *fp;
\tstruct fman_port *port;
\tunsigned long pool_raw_off, aligned_off, mgmt_off;
\tsize_t pool_size, pool_raw_size, mgmt_size;
\tvoid __iomem *pool_v, *mgmt_v, *page_v;
\tu32 params_off;
\tu8 total_tnums, i;
\tint err;

\tif (!muram)
\t\treturn -ENXIO;
\tif (fman_pcd_fe_port_find(pcd, port_id))
\t\treturn -EEXIST;
\t/* F-291: only the port which staged the table may publish it. */
\tif (!pcd->fe_arming_v4_table || pcd->fe_arming_port_id != port_id ||
\t    !pcd->fe_scaffold_gro || !pcd->fe_scaffold_mto ||
\t    !pcd->fe_scaffold_ato)
\t\treturn -ESHUTDOWN;

\tport = fman_port_lookup_rx(fman, port_id);
\tif (!port)
\t\treturn -ENODEV;
\tparams_off = fman_port_get_params_page(port);
\tif (!params_off) {
\t\terr = fman_pcd_port_ensure_params_page(pcd, port);
\t\tif (err)
\t\t\treturn err;
\t\tparams_off = fman_port_get_params_page(port);
\t\tif (!params_off)
\t\t\treturn -ENXIO;
\t}
\tif (!pcd->muram_pool ||
\t    !gen_pool_has_addr(pcd->muram_pool, params_off, FMAN_PCD_FE_PARAMS_PAGE_SIZE))
\t\treturn -EIO;
\tpage_v = (void __iomem *)(void *)fman_muram_offset_to_vbase(muram, params_off);
\tif (!page_v)
\t\treturn -ENXIO;
\tif (ioread32be((u8 __iomem *)page_v + FMAN_PP_FE_MGMT_IDX_OFF)) {
\t\tpr_err("fman_pcd: FE port 0x%02x already has a workspace index\\n", port_id);
\t\treturn -EBUSY;
\t}
\ttotal_tnums = fman_port_get_total_tnums(port);
\tif (!total_tnums)
\t\treturn -EINVAL;
\tfp = kzalloc(sizeof(*fp), GFP_KERNEL);
\tif (!fp)
\t\treturn -ENOMEM;

\tpool_size = (size_t)total_tnums * FMAN_PCD_BMI_FIFO_UNITS * 2;
\tpool_raw_size = pool_size + FMAN_PCD_BMI_FIFO_UNITS;
\tpool_raw_off = fman_pcd_muram_alloc(pcd, pool_raw_size);
\tif (IS_ERR_VALUE(pool_raw_off)) {
\t\terr = (int)pool_raw_off;
\t\tgoto err_fp;
\t}
\taligned_off = (pool_raw_off + (FMAN_PCD_BMI_FIFO_UNITS - 1)) &
\t\t      ~(unsigned long)(FMAN_PCD_BMI_FIFO_UNITS - 1);
\tpool_v = (void __iomem *)(void *)fman_muram_offset_to_vbase(muram, aligned_off);
\tif (!pool_v || (aligned_off & ~0xFFFFFFUL)) {
\t\terr = -EIO;
\t\tgoto err_pool;
\t}
\tmemset_io(pool_v, 0, pool_size);

\tmgmt_size = 5 + total_tnums;
\tmgmt_off = fman_pcd_muram_alloc(pcd, mgmt_size);
\tif (IS_ERR_VALUE(mgmt_off)) {
\t\terr = (int)mgmt_off;
\t\tgoto err_pool;
\t}
\tmgmt_v = (void __iomem *)(void *)fman_muram_offset_to_vbase(muram, mgmt_off);
\tif (!mgmt_v || (u64)mgmt_off > 0xFFFFFFFFULL) {
\t\terr = -EIO;
\t\tgoto err_mgmt;
\t}

\t/* F-291: workspace owner precedes page publication. */
\tfp->port_id = port_id;
\tfp->pool_raw_off = pool_raw_off;
\tfp->pool_raw_size = pool_raw_size;
\tfp->mgmt_off = mgmt_off;
\tfp->mgmt_size = mgmt_size;
\tfp->scaffold_gro = pcd->fe_scaffold_gro;
\tfp->scaffold_mto = pcd->fe_scaffold_mto;
\tfp->scaffold_ato = pcd->fe_scaffold_ato;
\tfp->table_v4 = pcd->fe_arming_v4_table;
\tlist_add_tail(&fp->node, &pcd->fe_ports);
\tpcd->fe_scaffold_gro = 0;
\tpcd->fe_scaffold_mto = 0;
\tpcd->fe_scaffold_ato = 0;
\tpcd->fe_arming_v4_table = NULL;
\tpcd->fe_arming_port_id = 0;

\t/* The index is cursor(4), 24-bit pool offset, slots and terminator. */
\tiowrite32be((u32)aligned_off, mgmt_v);
\tiowrite8(4, mgmt_v);
\tfor (i = 0; i < total_tnums; i++)
\t\tiowrite8(i, (u8 __iomem *)mgmt_v + 4 + i);
\tiowrite8(0xFF, (u8 __iomem *)mgmt_v + 4 + total_tnums);
\tif (ioread32be(mgmt_v) != (0x04000000U | (u32)aligned_off))
\t\tgoto err_owned;
\tfor (i = 0; i < total_tnums; i++)
\t\tif (ioread8((u8 __iomem *)mgmt_v + 4 + i) != i)
\t\t\tgoto err_owned;
\tif (ioread8((u8 __iomem *)mgmt_v + 4 + total_tnums) != 0xFF)
\t\tgoto err_owned;

\tiowrite32be(0, (u8 __iomem *)page_v + FMAN_PP_FE_DEPL_CNT_OFF);
\tif (ioread32be((u8 __iomem *)page_v + FMAN_PP_FE_DEPL_CNT_OFF))
\t\tgoto err_owned;
\tiowrite32be((u32)mgmt_off, (u8 __iomem *)page_v + FMAN_PP_FE_MGMT_IDX_OFF);
\tif (ioread32be((u8 __iomem *)page_v + FMAN_PP_FE_MGMT_IDX_OFF) != mgmt_off)
\t\tgoto err_owned;

\tdev_info(fman_get_dev(pcd->fman),
\t\t "fman_pcd: FE support on port 0x%x (tnums %u, pool 0x%lx/%zu B, mgmt 0x%lx/%zu B)\\n",
\t\t port_id, total_tnums, aligned_off, pool_size, mgmt_off, mgmt_size);
\treturn 0;

err_owned:
\tpr_err("fman_pcd: FE port 0x%02x workspace readback failed; owner retained\\n",
\t       port_id);
\treturn -EIO;
err_mgmt:
\tfman_pcd_muram_free(pcd, mgmt_off, mgmt_size);
err_pool:
\tfman_pcd_muram_free(pcd, pool_raw_off, pool_raw_size);
err_fp:
\tkfree(fp);
\treturn err;
}

/* F-269 fail-closed teardown callers */
"""

PORT_DEL = """\
static int fman_pcd_fe_port_del(struct fman_pcd *pcd, u8 port_id)
{
\tstruct muram_info *muram = fman_get_muram(pcd->fman);
\tstruct fman *fman = fman_pcd_get_fman(pcd);
\tstruct fman_pcd_fe_port *fp;
\tstruct fman_port *port;
\tvoid __iomem *page_v;
\tu32 params_off;
\tint err;

\tfp = fman_pcd_fe_port_find(pcd, port_id);
\tif (!fp)
\t\treturn -ENOENT;
\tif (!muram || !fman) {
\t\tpr_err("fman_pcd: FE port 0x%02x has no MURAM or FMan; workspace retained\\n",
\t\t       port_id);
\t\treturn -ENXIO;
\t}
\tport = fman_port_lookup_rx(fman, port_id);
\tif (!port) {
\t\tpr_err("fman_pcd: FE port 0x%02x missing; workspace retained\\n", port_id);
\t\treturn -ENODEV;
\t}
\tparams_off = fman_port_get_params_page(port);
\tif (!params_off || !fp->mgmt_off) {
\t\tpr_err("fman_pcd: FE port 0x%02x lacks its workspace page or index\\n",
\t\t       port_id);
\t\treturn -ENXIO;
\t}
\t/* F-291: reject cached params pages outside the current pool. */
\tif (!pcd->muram_pool ||
\t    !gen_pool_has_addr(pcd->muram_pool, params_off, FMAN_PCD_FE_PARAMS_PAGE_SIZE)) {
\t\tpr_err("fman_pcd: FE port 0x%02x params page outside MURAM pool\\n", port_id);
\t\treturn -EIO;
\t}
\tpage_v = (void __iomem *)(void *)fman_muram_offset_to_vbase(muram, params_off);
\tif (!page_v || ioread32be((u8 __iomem *)page_v + FMAN_PP_FE_MGMT_IDX_OFF) !=
\t\t       fp->mgmt_off) {
\t\tpr_err("fman_pcd: FE port 0x%02x workspace owner mismatch; state retained\\n",
\t\t       port_id);
\t\treturn -EIO;
\t}
\tif (fp->table_v4) {
\t\terr = fman_pcd_ehash_table_free(pcd, fp->table_v4);
\t\tif (err)
\t\t\treturn err;
\t\tfp->table_v4 = NULL;
\t}

\t/*
\t * Inverse of set(): unhook the ctrl-params page management index
\t * (+0x54 = 0) FIRST so the FE stops dereferencing the free-list,
\t * then free mgmt, then the pool.  Never free silicon-referenced
\t * MURAM before unpublishing it from the page.
\t */
\t/* F-291: verify the workspace pointer is unpublished before freeing it. */
\tiowrite32be(0, (u8 __iomem *)page_v + FMAN_PP_FE_MGMT_IDX_OFF);
\tif (ioread32be((u8 __iomem *)page_v + FMAN_PP_FE_MGMT_IDX_OFF)) {
\t\tpr_err("fman_pcd: FE port 0x%02x index detach readback failed\\n", port_id);
\t\treturn -EIO;
\t}
\tiowrite32be(0, (u8 __iomem *)page_v + FMAN_PP_FE_DEPL_CNT_OFF);
\tif (ioread32be((u8 __iomem *)page_v + FMAN_PP_FE_DEPL_CNT_OFF)) {
\t\tpr_err("fman_pcd: FE port 0x%02x depletion reset readback failed\\n", port_id);
\t\treturn -EIO;
\t}

\tfman_pcd_muram_free(pcd, fp->mgmt_off, fp->mgmt_size);
\tfman_pcd_muram_free(pcd, fp->pool_raw_off, fp->pool_raw_size);
\t/* F-139: Free per-port CONT_LOOKUP scaffold (gro 256 + mto 64 + ato 32 = 352 B).
\t * Formerly a singleton in pcd->fe_scaffold_* that was overwritten by the
\t * second port's engage, orphaning the first port's scaffold permanently.
\t */
\tif (fp->scaffold_ato)
\t\tfman_pcd_muram_free(pcd, fp->scaffold_ato, 32);
\tif (fp->scaffold_mto)
\t\tfman_pcd_muram_free(pcd, fp->scaffold_mto, 64);
\tif (fp->scaffold_gro)
\t\tfman_pcd_muram_free(pcd, fp->scaffold_gro, 256);
\tlist_del(&fp->node);
\tdev_info(fman_get_dev(pcd->fman),
\t\t "fman_pcd: FE support removed on port 0x%x\\n", port_id);
\tkfree(fp);
\treturn 0;
}

"""

FLOW_READY = """\
/* F-291: caller holds fe_lock across this check and publication. */
static int fman_pcd_fe_flow_ready_locked(struct fman_pcd *pcd, u8 port_id)
{
\tstruct fman_pcd_fe_port *fp;

\tif (port_id < 0x08 || port_id >= 0x28)
\t\treturn -EINVAL;
\tif (test_bit(port_id, pcd->fe_port_faulted))
\t\treturn -ESHUTDOWN;
\tif (!test_bit(port_id, pcd->fe_port_armed))
\t\treturn -ENODEV;
\tfp = fman_pcd_fe_port_find(pcd, port_id);
\tif (!fp || !fp->table_v4)
\t\treturn -ENODEV;
\treturn 0;
}

"""

PORT_TABLE_ALLOC = """\
\t\t\t\t\tstruct fman_pcd_ehash_table *et;

\t\t\t\t\t/* F-291: per-port table allocation must succeed before dispatch. */
\t\t\t\t\terr = fman_pcd_ehash_table_set(pcd, 0x7FFF,
\t\t\t\t\t\tfman_pcd_fe_profiles[fman_pcd_fe_port_profile(pcd, (u8)port_id)].key_size,
\t\t\t\t\t\t0);
\t\t\t\t\tif (err) {
\t\t\t\t\t\tfman_pcd_muram_free(pcd, gro, 256);
\t\t\t\t\t\tfman_pcd_muram_free(pcd, mto, 64);
\t\t\t\t\t\tfman_pcd_muram_free(pcd, ato, 32);
\t\t\t\t\t\tpr_warn("fman_pcd fe_arm: port 0x%02x per-port table allocation failed: %d\\n",
\t\t\t\t\t\t\tport_id, err);
\t\t\t\t\t\treturn err;
\t\t\t\t\t}
\t\t\t\t\tpcd->fe_arming_v4_table =
\t\t\t\t\t\tlist_last_entry(&pcd->fe_ehash_tables,
\t\t\t\t\t\t\tstruct fman_pcd_ehash_table, node);
\t\t\t\t\tpcd->fe_arming_port_id = (u8)port_id;
\t\t\t\t\tet = pcd->fe_arming_v4_table;
\t\t\t\t\tdev_info(fman_get_dev(pcd->fman),
\t\t\t\t\t\t "fman_pcd: F-220 per-port v4 table for port 0x%02x DDR %pad\\n",
\t\t\t\t\t\t port_id, &et->table_dma);

"""

STOP = """\
static int stop_port_hwp(struct fman_port *port)
{
\tstruct fman_port_hwp_regs __iomem *regs = port->hwp_regs;
\tint i;

\tiowrite32be(HWP_HXS_PCAC_PSTOP, &regs->fmpr_pcac);
\tfor (i = 0; i < 100; i++) {
\t\tif (!(ioread32be(&regs->fmpr_pcac) & HWP_HXS_PCAC_PSTAT))
\t\t\treturn 0;
\t\tudelay(10);
\t}
\tpr_err("Timeout stopping HW Parser\\n");
\treturn -ETIMEDOUT;
}

"""

START = """\
/*
 * Clearing PSTOP restarts the port parser. PSTAT reports an active parse
 * (0 = idle) and does not acknowledge a restart, so an idle port reads 0
 * here and polling it can only time out.
 */
static int start_port_hwp(struct fman_port *port)
{
\tiowrite32be(0, &port->hwp_regs->fmpr_pcac);
\treturn 0;
}

"""

CLEAR_LCV = """\
int fman_port_clear_lcv_split(struct fman_port *port)
{
\tstruct fman_port_hwp_regs __iomem *regs;
\tint i, err, start_err;

\tif (!port || port->port_type != FMAN_PORT_TYPE_RX || !port->hwp_regs)
\t\treturn -EINVAL;
\tregs = port->hwp_regs;
\terr = stop_port_hwp(port);
\tif (err)
\t\treturn err;
\tfor (i = 0; i < HWP_HXS_COUNT; i++)
\t\tiowrite32be(0xffffffff, &regs->pmda[i].lcv);
\tfor (i = 0; i < HWP_HXS_COUNT; i++) {
\t\tif (ioread32be(&regs->pmda[i].lcv) != 0xffffffff) {
\t\t\tdev_err(port->dev, "fman_port: LCV clear readback mismatch at %d\\n", i);
\t\t\terr = -EIO;
\t\t\tbreak;
\t\t}
\t}
\tstart_err = start_port_hwp(port);
\treturn start_err ? start_err : err;
}
"""

VERIFY_DETACH = """\

/* Read back the RX routing fields changed by the FE disarm path. */
int fman_port_verify_fe_detached(struct fman_port *port)
{
\tu32 rccb, rfpne;

\tif (!port || port->port_type != FMAN_PORT_TYPE_RX)
\t\treturn -EINVAL;
\trccb = ioread32be(&port->bmi_regs->rx.fmbm_rccb);
\trfpne = ioread32be(&port->bmi_regs->rx.fmbm_rfpne);
\tif (rccb || ((rfpne & 0x007c0000U) == NIA_ENG_HWK &&
\t\t     (rfpne & (NIA_KG_CC_EN | NIA_KG_DIRECT | 0x1fU)))) {
\t\tdev_err(port->dev, "fman_port: FE disarm readback rccb=0x%08x rfpne=0x%08x\\n",
\t\t\trccb, rfpne);
\t\treturn -EIO;
\t}
\treturn 0;
}
EXPORT_SYMBOL_GPL(fman_port_verify_fe_detached);
"""

FAULT_ARM = """\
/*
 * A failed arm may have made FE dispatch live. Stop admitting RX work where
 * possible, but retain every allocation: a port stop is not a walker barrier.
 */
static void fman_pcd_fe_fault_arm(struct fman_pcd *pcd, u8 port_id,
\t\t\t\t  bool kg_programming_started)
{
\tstruct fman *fman;
\tstruct fman_port *rxport;
\tint err;

\tset_bit(port_id, pcd->fe_port_faulted);
\tif (!kg_programming_started) {
\t\tpr_err("fman_pcd: FE arm failed before KG programming on port 0x%02x; state retained\\n",
\t\t       port_id);
\t\treturn;
\t}
\tfman = fman_pcd_get_fman(pcd);
\trxport = fman ? fman_port_lookup_rx(fman, port_id) : NULL;
\tif (!rxport) {
\t\tpr_err("fman_pcd: failed FE arm on port 0x%02x; RX port unavailable, hardware may remain active\\n",
\t\t       port_id);
\t\treturn;
\t}
\terr = fman_port_disable(rxport);
\tif (err)
\t\tpr_err("fman_pcd: failed FE arm on port 0x%02x; RX stop uncertain (%d)\\n",
\t\t       port_id, err);
}

"""

DETACH_CC = """\
int fman_pcd_kg_port_detach_cc(struct fman_pcd *pcd, u8 hw_port_id)
{
\tstruct fman_keygen *keygen;
\tstruct keygen_scheme *slot, saved;
\tstruct fman *fman;
\tstruct mutex *lock;
\tu8 id;
\tint err;

\tif (!pcd || hw_port_id < 0x08 || hw_port_id >= 0x28)
\t\treturn -EINVAL;
\tfman = fman_pcd_get_fman(pcd);
\tif (!fman || !fman->keygen)
\t\treturn -ENXIO;
\tkeygen = fman->keygen;
\tlock = fman_pcd_get_lock(pcd);
\tmutex_lock(lock);
\tslot = kg_find_port_scheme(keygen, hw_port_id, &id);
\tif (!slot) {
\t\tmutex_unlock(lock);
\t\treturn 0;
\t}
\tsaved = *slot;
\tslot->next_engine = 0;
\tslot->cc_base_offset = 0;
\tslot->cc_bits_sel = 0;
\tslot->used = false;
\terr = keygen_scheme_setup(keygen, id, true);
\tif (err)
\t\t*slot = saved;
\tmutex_unlock(lock);
\treturn err;
}
"""

DISARM_KG = """\
int fman_pcd_kg_port_disarm_fe(struct fman_pcd *pcd, u8 hw_port_id,
\t\t\t       u8 saved_engine)
{
\tstruct fman_keygen *keygen;
\tstruct fman_port *rxport;
\tstruct fman *fman;
\tstruct mutex *lock;
\tbool v6_torn_down = false;
\tint i, err = 0;
\tu8 id;

\tif (!pcd)
\t\treturn -EINVAL;
\tfman = fman_pcd_get_fman(pcd);
\tif (!fman || !fman->keygen)
\t\treturn -ENXIO;
\trxport = fman_port_lookup_rx(fman, hw_port_id);
\tif (!rxport)
\t\treturn -ENODEV;
\tkeygen = fman->keygen;
\tlock = fman_pcd_get_lock(pcd);
\tmutex_lock(lock);
\tfor (i = 0; i < FM_KG_MAX_NUM_OF_SCHEMES; i++) {
\t\tstruct keygen_scheme *s = &keygen->schemes[i];
\t\tstruct keygen_scheme saved;
\t\tbool clone;

\t\tif (!s->used || s->hw_port_id != hw_port_id)
\t\t\tcontinue;
\t\tclone = (s->cc_base_offset == 1 && s->next_engine == 3) ||
\t\t\t(s->match_vector == 0x20000000U);
\t\tif (clone) {
\t\t\terr = keygen_bind_port_to_schemes(keygen, (u8)i, false);
\t\t\tif (err)
\t\t\t\tbreak;
\t\t\tsaved = *s;
\t\t\ts->match_vector = 0;
\t\t\ts->cc_base_offset = 0;
\t\t\ts->cc_bits_sel = 0;
\t\t\ts->ekfc = 0;
\t\t\ts->next_engine = 0;
\t\t\ts->used = true;
\t\t\terr = keygen_scheme_setup(keygen, (u8)i, false);
\t\t\tif (err) {
\t\t\t\t*s = saved;
\t\t\t\tbreak;
\t\t\t}
\t\t\ts->hw_port_id = 0;
\t\t\ts->used = false;
\t\t\tv6_torn_down = true;
\t\t} else if (s->match_vector) {
\t\t\tsaved = *s;
\t\t\ts->match_vector = 0;
\t\t\ts->used = false;
\t\t\terr = keygen_scheme_setup(keygen, (u8)i, true);
\t\t\tif (err) {
\t\t\t\t*s = saved;
\t\t\t\tbreak;
\t\t\t}
\t\t\tv6_torn_down = true;
\t\t}
\t}
\tif (!err && !kg_find_port_scheme(keygen, hw_port_id, &id))
\t\terr = -ENODEV;
\tmutex_unlock(lock);
\tif (err)
\t\treturn err;
\tif (v6_torn_down) {
\t\terr = fman_port_clear_lcv_split(rxport);
\t\tif (err)
\t\t\treturn err;
\t}
\terr = fman_port_set_cc_base(rxport, 0);
\tif (err)
\t\treturn err;
\tfman_port_clear_kg_direct_scheme(rxport);
\terr = fman_port_verify_fe_detached(rxport);
\tif (err)
\t\treturn err;
\treturn fman_pcd_kg_port_detach_cc(pcd, hw_port_id);
}
"""

DISARM_FE = """\
static int fman_pcd_fe_disarm_preflight_locked(struct fman_pcd *pcd,
\t\t\t\t\t      unsigned int port_id)
{
\tstruct fman_pcd_fe_port *fp;
\tstruct fman_pcd_ehash_table *table;
\tbool owner_listed = false;

\tif (port_id < 0x08 || port_id >= 0x28)
\t\treturn -EINVAL;
\tif (test_bit(port_id, pcd->fe_port_faulted))
\t\treturn -ESHUTDOWN;
\tif (pcd->fe_arming_v4_table) {
\t\tpr_warn("fman_pcd: FE disarm refused while staging owner is unresolved\\n");
\t\treturn -ESHUTDOWN;
\t}
\t/* F-291: disarm requires a verified per-port owner. */
\tfp = fman_pcd_fe_port_find(pcd, (u8)port_id);
\tif (!test_bit(port_id, pcd->fe_port_armed)) {
\t\tif (fp) {
\t\t\tpr_warn("fman_pcd: unarmed port 0x%02x retains FE state\\n",
\t\t\t\tport_id);
\t\t\treturn -ESHUTDOWN;
\t\t}
\t\treturn 0;
\t}
\tif (!fp || !fp->table_v4) {
\t\tset_bit(port_id, pcd->fe_port_faulted);
\t\tpr_err("fman_pcd: armed port 0x%02x lacks its FE owner; state retained\\n",
\t\t       port_id);
\t\treturn -ESHUTDOWN;
\t}
\t/* F-291: conservatively require every ehash table to be idle. */
\tlist_for_each_entry(table, &pcd->fe_ehash_tables, node) {
\t\tif (table == fp->table_v4)
\t\t\towner_listed = true;
\t\tif (!list_empty(&table->flows) ||
\t\t    !list_empty(&table->quarantined_flows) ||
\t\t    !list_empty(&table->quarantined_cu) ||
\t\t    table->poisoned || table->draining) {
\t\t\tpr_warn_ratelimited("fman_pcd: FE disarm port 0x%02x has live or uncertain ehash owners\\n",
\t\t\t\t\t    port_id);
\t\t\treturn -EBUSY;
\t\t}
\t}
\tif (!owner_listed) {
\t\tset_bit(port_id, pcd->fe_port_faulted);
\t\tpr_err("fman_pcd: FE port 0x%02x table not registered; state retained\\n",
\t\t       port_id);
\t\treturn -ESHUTDOWN;
\t}
\treturn 0;
}

static int __fman_pcd_fe_arm_disengage(struct fman_pcd *pcd,
\t\t\t\t       unsigned int port_id)
{
\tint err;

\terr = fman_pcd_fe_disarm_preflight_locked(pcd, port_id);
\tif (err)
\t\treturn err;
\tif (!test_bit(port_id, pcd->fe_port_armed))
\t\treturn 0;
\tclear_bit(port_id, pcd->fe_port_armed);
\terr = fman_pcd_kg_port_disarm_fe(pcd, (u8)port_id, 0);
\tif (err)
\t\tgoto fault;
\tfsleep(5000);
\terr = fman_pcd_fe_adv_offload(pcd, (u8)port_id, false);
\tif (err)
\t\tgoto fault;
\terr = fman_pcd_fe_port_del(pcd, (u8)port_id);
\tif (err)
\t\tgoto fault;
\tpr_info("fman_pcd fe_arm: port 0x%02x DISENGAGED\\n", port_id);
\treturn 0;
fault:
\tset_bit(port_id, pcd->fe_port_faulted);
\tpr_err("fman_pcd fe_arm: port 0x%02x disarm uncertain (%d); state retained\\n",
\t       port_id, err);
\treturn err;
}

"""


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise ValueError(f"{label}: found {count} anchors, expected 1")
    return text.replace(old, new, 1)


def replace_function(text, start, end, new, label):
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError(f"{label}: missing or duplicate function boundary")
    first = text.index(start)
    last = text.index(end, first)
    return text[:first] + new + text[last:]


def guard_debugfs_writer(text, name):
    start = f"static ssize_t {name}("
    if text.count(start) != 1:
        raise ValueError(f"{name}: missing or duplicate function")
    first = text.index(start)
    last = text.index("\n}\n", first) + len("\n}\n")
    guard = DEBUG_CHAIN_GUARD
    if name != "fman_pcd_fe_obs_write":
        guard += DEBUG_OBSERVER_GUARD
    body = replace_once(
        text[first:last], "\tmutex_lock(&pcd->fe_lock);\n",
        "\tmutex_lock(&pcd->fe_lock);\n" + guard, name)
    return text[:first] + body + text[last:]


def debugfs_writer_guarded(text, name):
    start = f"static ssize_t {name}("
    if text.count(start) != 1:
        return False
    first = text.index(start)
    last = text.find("\n}\n", first)
    if last < 0:
        return False
    body = text[first:last]
    guard = DEBUG_CHAIN_GUARD
    if name != "fman_pcd_fe_obs_write":
        guard += DEBUG_OBSERVER_GUARD
    return body.count("\tmutex_lock(&pcd->fe_lock);\n" + guard) == 1


def main():
    paths = (KG, PCD, PORT, PORT_H, HDR)
    try:
        files = {path: open(path).read() for path in paths}
    except FileNotFoundError as exc:
        print(f"### F-291: FATAL: {exc}")
        return 1
    marker = "F-291: failed FE programming cannot be retried safely"
    if marker in files[PCD]:
        if (files[PCD].count(marker) == 1 and
                "int fman_pcd_kg_port_disarm_fe(" in files[HDR] and
                "int fman_port_verify_fe_detached(" in files[PORT] and
                "F-291: caller holds fe_lock across this check and publication."
                in files[PCD] and
                "F-291: recovery disabled until workspace ownership and quiescence are proven."
                in files[PCD] and
                "F-291: do not partially disengage before refusing unsafe recovery."
                in files[PCD] and
                "F-291: per-port table allocation must succeed before dispatch."
                in files[PCD] and
                "F-291: disarm requires a verified per-port owner." in files[PCD] and
                "F-291: conservatively require every ehash table to be idle."
                in files[PCD] and
                "fman_pcd_fe_disengage_prepared(" in files[PCD] and
                "fman_pcd_fe_disengage_prepared(" in files[HDR] and
                "F-291: verify the workspace pointer is unpublished before freeing it."
                in files[PCD] and
                "F-291: workspace owner precedes page publication." in files[PCD] and
                "u8 fe_arming_port_id;" in files[PCD] and
                "F-291: reject cached params pages outside the current pool."
                in files[PCD] and
                "F-291: debugfs FE port mutations disabled until ownership is proven."
                in files[PCD] and
                "F-291: debugfs FE arm mutations disabled until ownership is proven."
                in files[PCD] and
                files[PCD].count(DEBUG_ARM_SHOW) == 1 and
                "enq->muram_off points at the ENQ FE" in files[PCD] and
                files[PCD].count(VM_CHAIN_PRECHECK) == 1 and
                files[PCD].count(VM_CHAIN_BUILD) == 1 and
                "bool fe_vm_chain_faulted;" in files[PCD] and
                files[PCD].count(DEBUG_CHAIN_HELPER) == 1 and
                files[PCD].count(DEBUG_CHAIN_GUARD) == len(DEBUG_CHAIN_WRITERS) + 1 and
                files[PCD].count(DEBUG_OBSERVER_GUARD) == len(DEBUG_CHAIN_WRITERS) and
                files[PCD].count(OBS_DISARM_PREFIX + OBS_DISARM_OWNER_GUARD) == 1 and
                all(debugfs_writer_guarded(files[PCD], name) for name in
                    (*DEBUG_CHAIN_WRITERS, "fman_pcd_fe_kg_ekfc_write")) and
                files[PCD].count(POOL_GET) == 1 and
                files[PCD].count(POOL_PUT) == 1 and
                "\t\terr = fman_pcd_fe_pool_put(pcd);\n"
                "\t\tif (err)\n\t\t\treturn err;\n" in files[PCD] and
                (files[PCD].count(DEBUG_EXTC_DISABLED) == 1 or
                 # F-293 replaces this body with its own refusal.
                 (files[PCD].count(DEBUG_EXTC_DISABLED.split("\n", 1)[0]) == 1 and
                  files[PCD].count("fe_extc SYNC writes disabled until INV0 "
                                   "ownership is proven") == 1)) and
                "sync writes disabled: global INV0 ownership unresolved" in files[PCD] and
                "fman_pcd: FE arm has no MURAM; engage refused" in files[PCD] and
                "__fman_pcd_fe_engage_profile_locked(" in files[PCD] and
                "__fman_pcd_fe_disengage_locked(" in files[PCD]):
            print("### F-291: already applied")
            return 0
        print("### F-291: FATAL: partially applied tree")
        return 1
    try:
        files[PORT] = replace_function(
            files[PORT], "static void stop_port_hwp(",
            "static void start_port_hwp(", STOP, "stop parser")
        files[PORT] = replace_function(
            files[PORT], "static void start_port_hwp(",
            "static void init_hwp(", START, "start parser")
        files[PORT] = replace_function(
            files[PORT], "void fman_port_clear_lcv_split(",
            "EXPORT_SYMBOL_GPL(fman_port_clear_lcv_split);",
            CLEAR_LCV, "clear parser LCV")
        files[PORT] = replace_once(
            files[PORT], "EXPORT_SYMBOL_GPL(fman_port_clear_kg_direct_scheme);\n",
            "EXPORT_SYMBOL_GPL(fman_port_clear_kg_direct_scheme);\n" +
            VERIFY_DETACH, "port routing readback")
        files[PORT_H] = replace_once(
            files[PORT_H], "void fman_port_clear_lcv_split(struct fman_port *port);",
            "int fman_port_clear_lcv_split(struct fman_port *port);",
            "LCV declaration")
        files[PORT_H] = replace_once(
            files[PORT_H], "int fman_port_set_cc_base(struct fman_port *port, u32 cc_muram_off);\n",
            "int fman_port_set_cc_base(struct fman_port *port, u32 cc_muram_off);\n"
            "int fman_port_verify_fe_detached(struct fman_port *port);\n",
            "port readback declaration")
        files[KG] = replace_function(
            files[KG], "int fman_pcd_kg_port_detach_cc(",
            "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_detach_cc);",
            DETACH_CC, "RSS scheme detach")
        files[KG] = replace_function(
            files[KG], "void fman_pcd_kg_port_disarm_fe(",
            "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_disarm_fe);",
            DISARM_KG, "FE KG disarm")
        arm_signature = (
            "int fman_pcd_kg_port_arm_fe(struct fman_pcd *pcd, u8 hw_port_id,\n"
            "\t\t\t    u32 fe_enter_off, u8 *saved_engine,\n"
            "\t\t\t    u32 ekfc)"
        )
        files[KG] = replace_once(
            files[KG], arm_signature,
            arm_signature.replace("u32 ekfc)", "u32 ekfc, bool *programming_started)"),
            "KG arm programming state")
        files[KG] = replace_once(
            files[KG],
            "\tif (!pcd || !fe_enter_off || !saved_engine)\n\t\treturn -EINVAL;\n",
            "\tif (!programming_started)\n\t\treturn -EINVAL;\n"
            "\t*programming_started = false;\n"
            "\tif (!pcd || !fe_enter_off || !saved_engine)\n\t\treturn -EINVAL;\n",
            "KG arm initial state")
        files[KG] = replace_once(
            files[KG], "\tif (ekfc)\n\t\tslot->ekfc = ekfc;\n",
            "\t*programming_started = true;\n"
            "\tif (ekfc)\n\t\tslot->ekfc = ekfc;\n",
            "KG arm mutation boundary")
        files[HDR] = replace_once(
            files[HDR], arm_signature + ";",
            arm_signature.replace("u32 ekfc)", "u32 ekfc, bool *programming_started)") + ";",
            "KG arm declaration")
        files[HDR] = replace_once(
            files[HDR],
            " * Return: 0 on success; negative errno (scheme/port restored on failure).\n",
            " * @programming_started: out - KG state may have been modified\n"
            " *\n"
            " * Return: 0 on success; negative errno, with state uncertain if\n"
            " * programming started.\n",
            "KG arm failure contract")
        files[HDR] = replace_once(
            files[HDR], "void fman_pcd_kg_port_disarm_fe(struct fman_pcd *pcd, u8 hw_port_id,",
            "int fman_pcd_kg_port_disarm_fe(struct fman_pcd *pcd, u8 hw_port_id,",
            "KG disarm declaration")
        files[HDR] = replace_once(
            files[HDR],
            " * fman_pcd_kg_port_detach_cc().  Idempotent.\n",
            " * fman_pcd_kg_port_detach_cc().  Returns 0 or a negative error;\n"
            " * an error leaves hardware state uncertain and must not be retried.\n",
            "KG disarm contract")
        files[PCD] = replace_once(
            files[PCD],
            "\tDECLARE_BITMAP(fe_port_armed, 32);\t/* F-107: per-port engagement guard (gen_pool double-free prevention) */\n",
            "\tDECLARE_BITMAP(fe_port_armed, 0x28);\t/* F-107: per-port engagement guard */\n"
            "\t/* F-291: failed FE programming cannot be retried safely. */\n"
            "\tDECLARE_BITMAP(fe_port_faulted, 0x28);\n",
            "port fault bitmap")
        files[PCD] = replace_once(
            files[PCD],
            "\tbool fe_vm_chain_built;\t\t/* F-090/F-092: chain built flag */\n",
            "\tbool fe_vm_chain_built;\t\t/* F-090/F-092: chain built flag */\n"
            "\tbool fe_vm_chain_faulted;\t/* incomplete global chain retained */\n",
            "shared chain fault state")
        files[PCD] = replace_once(
            files[PCD],
            "\tstruct fman_pcd_fe_context_params p = {};\n"
            "\tvoid __iomem *fe;\n"
            "\tu32 fqid;\n",
            "\tvoid __iomem *fe;\n",
            "remove descriptor context builder state")
        files[PCD] = replace_once(
            files[PCD],
            "\t/* ENQ context: read FQID from the built ENQ FE descriptor. */\n"
            "\tenq = list_first_entry_or_null(&pcd->fe_enq,\n"
            "\t\t\t\t       struct fman_pcd_fe_obj, node);\n"
            "\tif (enq) {\n"
            "\t\tfe = (void *)(void *)fman_muram_offset_to_vbase(muram, enq->muram_off);\n"
            "\t\tfqid = ioread32be((u32 __iomem *)fe + 1) & 0x00FFFFFFu;\n"
            "\t\tif (fqid) {\n"
            "\t\t\tp.type = FMAN_FE_TYPE_ENQ;\n"
            "\t\t\tp.u.enq.fqid = fqid;\n"
            "\t\t\tfman_pcd_fe_context_build(fe,\n"
            "\t\t\t\tFMAN_FE_ENQ_CTX_OFF, &p);\n"
            "\t\t}\n"
            "\t}\n",
            ENQ_CONTEXT_DESCRIPTOR_GUARD +
            "\tenq = list_first_entry_or_null(&pcd->fe_enq,\n"
            "\t\t\t\t       struct fman_pcd_fe_obj, node);\n",
            "keep ENQ context out of descriptor")
        files[PCD] = replace_once(
            files[PCD],
            "\tstruct fman_pcd_ehash_table *fe_arming_v4_table;\n",
            "\tstruct fman_pcd_ehash_table *fe_arming_v4_table;\n"
            "\tu8 fe_arming_port_id;\t/* valid while the staging table exists */\n",
            "staging port identity")
        files[PCD] = replace_function(
            files[PCD], "static int fman_pcd_fe_port_del(",
            "static int fman_pcd_fe_port_drain(", PORT_DEL,
            "workspace ownership and detach readback")
        files[PCD] = replace_function(
            files[PCD], "static int fman_pcd_fe_port_set(",
            "static int fman_pcd_fe_port_del(", PORT_SET,
            "workspace ownership and publication readback")
        files[PCD] = replace_once(
            files[PCD], "static int __fman_pcd_fe_arm_engage(",
            FAULT_ARM + "static int __fman_pcd_fe_arm_engage(",
            "partial arm fault containment")
        files[PCD] = replace_once(
            files[PCD],
            "\tu8 saved_engine = 0;\n\tint err;\n\n"
            "\tif (port_id < 0x08 || port_id >= 0x28)\n",
            "\tu8 saved_engine = 0;\n\tbool kg_programming_started = false;\n"
            "\tint err;\n\n"
            "\tif (port_id < 0x08 || port_id >= 0x28)\n",
            "FE arm programming state")
        files[PCD] = replace_function(
            files[PCD], "static int __fman_pcd_fe_arm_disengage(",
            "/* Debugfs wrapper — parse string, delegate to __fman_pcd_fe_arm_disengage(). */",
            DISARM_FE, "FE disarm")
        files[PCD] = replace_once(
            files[PCD],
            "\tif (pcd->fe_arming_v4_table ||\n"
            "\t    (!test_bit(port_id, pcd->fe_port_armed) &&\n",
            "\tif (pcd->fe_arming_v4_table ||\n"
            "\t    test_bit(port_id, pcd->fe_port_faulted) ||\n"
            "\t    (!test_bit(port_id, pcd->fe_port_armed) &&\n",
            "retained state guard")
        files[PCD] = replace_once(
            files[PCD],
            "\tint err;\n\n"
            "\tif (port_id < 0x08 || port_id >= 0x28)\n"
            "\t\treturn -EINVAL;\n"
            "\t\t{\n"
            "\t\tstruct muram_info *muram = fman_get_muram(pcd->fman);\n",
            "\tint err;\n\n"
            "\tif (port_id < 0x08 || port_id >= 0x28)\n"
            "\t\treturn -EINVAL;\n"
            "\tif (pcd->fe_arming_v4_table || test_bit(port_id, pcd->fe_port_faulted))\n"
            "\t\treturn -ESHUTDOWN;\n"
            "\t{\n"
            "\t\tstruct muram_info *muram = fman_get_muram(pcd->fman);\n",
            "typed engage retained guard")
        files[PCD] = replace_once(
            files[PCD],
            "\t\tstruct muram_info *muram = fman_get_muram(pcd->fman);\n"
            "\t\tunsigned long gro, mto, ato;\n\n"
            "\t\tif (muram) {\n",
            "\t\tstruct muram_info *muram = fman_get_muram(pcd->fman);\n\n"
            "\t\tif (!muram) {\n"
            "\t\t\tpr_err(\"fman_pcd: FE arm has no MURAM; engage refused\\n\");\n"
            "\t\t\treturn -ENXIO;\n"
            "\t\t}\n"
            "\t\t{\n"
            "\t\t\tunsigned long gro, mto, ato;\n\n",
            "require MURAM before scaffold allocation")
        files[PCD] = replace_once(
            files[PCD],
            "\t\t\t\t\t * engage-serialized). Failure falls back to template table0.\n",
            "\t\t\t\t\t * engage-serialized). Failure aborts before dispatch.\n",
            "per-port allocation contract")
        files[PCD] = replace_function(
            files[PCD],
            "\t\t\t\t\tstruct fman_pcd_ehash_table *et =\n",
            "\t\t\t\t\tif (et && pcd->fe_int_buf_off) {\n",
            PORT_TABLE_ALLOC, "require per-port ehash table")
        files[PCD] = replace_once(
            files[PCD],
            "\t\tif (!rxport)\n"
            "\t\t\treturn -ENODEV;\n"
            "\t\terr = fman_pcd_port_ensure_params_page(pcd, rxport);\n"
            "\t\tif (err)\n"
            "\t\t\treturn err;\n",
            "\t\tif (!rxport) {\n"
            "\t\t\tfman_pcd_fe_fault_arm(pcd, (u8)port_id, false);\n"
            "\t\t\treturn -ENODEV;\n"
            "\t\t}\n"
            "\t\terr = fman_pcd_port_ensure_params_page(pcd, rxport);\n"
            "\t\tif (err) {\n"
            "\t\t\tfman_pcd_fe_fault_arm(pcd, (u8)port_id, false);\n"
            "\t\t\treturn err;\n"
            "\t\t}\n",
            "pre-dispatch params failure retention")
        files[PCD] = replace_once(
            files[PCD],
            "\t\t\treturn _verify_err;\n"
            "\t\t}\t}\n\n"
            "\t\terr = fman_pcd_kg_port_arm_fe(pcd, (u8)port_id,\n",
            "\t\t\tfman_pcd_fe_fault_arm(pcd, (u8)port_id, false);\n"
            "\t\t\treturn _verify_err;\n"
            "\t\t}\t}\n\n"
            "\t/* F-291: publish a verified per-port workspace before KG dispatch. */\n"
            "\terr = fman_pcd_fe_port_set(pcd, (u8)port_id);\n"
            "\tif (err) {\n"
            "\t\tfman_pcd_fe_fault_arm(pcd, (u8)port_id, false);\n"
            "\t\treturn err;\n"
            "\t}\n\n"
            "\t\terr = fman_pcd_kg_port_arm_fe(pcd, (u8)port_id,\n",
            "prepare workspace before KG dispatch")
        files[PCD] = replace_once(
            files[PCD],
            "\t\t\t\t      (u32)fe_enter_off, &saved_engine, ekfc);\n"
            "\tif (err) {\n",
            "\t\t\t\t      (u32)fe_enter_off, &saved_engine, ekfc,\n"
            "\t\t\t\t      &kg_programming_started);\n"
            "\tif (err) {\n"
            "\t\tfman_pcd_fe_fault_arm(pcd, (u8)port_id, kg_programming_started);\n",
            "KG arm failure fence")
        files[PCD] = replace_once(
            files[PCD],
            "\terr = fman_pcd_fe_port_set(pcd, hw_port_id);\n"
            "\tif (err) {\n"
            "\t\t__fman_pcd_fe_arm_disengage(pcd, hw_port_id);\n"
            "\t\tpcd->fe_port_profile[hw_port_id] = FMAN_PCD_FE_PROFILE_ROUTED;\n"
            "\t\treturn err;\n"
            "\t}\n\n"
            "\terr = fman_pcd_fe_adv_offload(pcd, hw_port_id, true);\t/* F-262 */\n"
            "\tif (err) {\n"
            "\t\t__fman_pcd_fe_arm_disengage(pcd, hw_port_id);\n"
            "\t\tpcd->fe_port_profile[hw_port_id] = FMAN_PCD_FE_PROFILE_ROUTED;\n"
            "\t\treturn err;\n"
            "\t}\n",
            "\terr = fman_pcd_fe_adv_offload(pcd, hw_port_id, true);\t/* F-262 */\n"
            "\tif (err) {\n"
            "\t\tfman_pcd_fe_fault_arm(pcd, hw_port_id, true);\n"
            "\t\tpr_err(\"fman_pcd: FE advanced offload setup failed (%d); port retained\\n\", err);\n"
            "\t\treturn err;\n"
            "\t}\n",
            "partial engage retention")
        files[PCD] = replace_function(
            files[PCD], "static int fman_pcd_fe_pool_get(",
            "static void fman_pcd_fe_pool_put(", DEBUG_CHAIN_HELPER + POOL_GET,
            "locked FE pool get")
        files[PCD] = replace_function(
            files[PCD], "static void fman_pcd_fe_pool_put(",
            "static int fman_pcd_fe_pool_show(", POOL_PUT,
            "guard debugfs FE pool put")
        files[PCD] = replace_once(
            files[PCD],
            '\t} else if (!strcmp(cmd, "put")) {\n'
            "\t\tfman_pcd_fe_pool_put(pcd);\n",
            '\t} else if (!strcmp(cmd, "put")) {\n'
            "\t\terr = fman_pcd_fe_pool_put(pcd);\n"
            "\t\tif (err)\n"
            "\t\t\treturn err;\n",
            "propagate debugfs FE pool put failure")
        for name in DEBUG_CHAIN_WRITERS:
            files[PCD] = guard_debugfs_writer(files[PCD], name)
        files[PCD] = replace_once(
            files[PCD], OBS_DISARM_PREFIX,
            OBS_DISARM_PREFIX + OBS_DISARM_OWNER_GUARD,
            "refuse observer disarm without pool or hash owner")
        files[PCD] = replace_once(
            files[PCD],
            "\terr = fman_pcd_fe_kg_ekfc_reconfig(pcd, (u8)scheme_id, (u32)ekfc);\n",
            "\tmutex_lock(&pcd->fe_lock);\n" + DEBUG_CHAIN_GUARD +
            DEBUG_OBSERVER_GUARD +
            "\terr = fman_pcd_fe_kg_ekfc_reconfig(pcd, (u8)scheme_id, (u32)ekfc);\n"
            "\tmutex_unlock(&pcd->fe_lock);\n",
            "serialize debugfs KeyGen reconfiguration")
        files[PCD] = replace_once(
            files[PCD],
            "\terr = fman_pcd_fe_pool_get(pcd);\n"
            "\tif (err)\n\t\treturn err;\n\n"
            "\terr = fman_pcd_fe_singletons_build(pcd);\n",
            "\terr = fman_pcd_fe_pool_get_locked(pcd);\n"
            "\tif (err)\n\t\treturn err;\n\n"
            "\terr = fman_pcd_fe_singletons_build(pcd);\n",
            "chain build pool lock")
        files[PCD] = replace_once(
            files[PCD],
            "\terr = fman_pcd_fe_retained_check(pcd, hw_port_id);\t/* F-289 */\n"
            "\tif (err)\n"
            "\t\treturn err;\n",
            "\terr = fman_pcd_fe_retained_check(pcd, hw_port_id);\t/* F-289 */\n"
            "\tif (err)\n"
            "\t\treturn err;\n" + VM_CHAIN_PRECHECK,
            "reject incomplete shared chain before engage")
        files[PCD] = replace_once(
            files[PCD],
            "\tif (!pcd->fe_vm_chain_built) {\n"
            "\t\tif (!list_empty(&pcd->fe_ehash_tables)) {\n"
            "\t\t\tpcd->fe_vm_chain_built = true;\n"
            "\t\t} else {\n"
            "\t\t\terr = __fman_pcd_fe_build_vm_chain(pcd);\n"
            "\t\t\tif (err) {\n"
            "\t\t\t\tpr_err(\"fman_pcd: FE engage: VM chain build failed: %d\\n\", err);\n"
            "\t\t\t\treturn err;\n"
            "\t\t\t}\n"
            "\t\t\tpcd->fe_vm_chain_built = true;\n"
            "\t\t}\n"
            "\t}\n",
            VM_CHAIN_BUILD, "retain failed shared chain")
        files[PCD] = replace_once(
            files[PCD],
            "/* F-092: Build FE-VM chain before arming (idempotent).\n"
            "\t * The ehash/fe_pool may already exist from FMan microcode pre-init\n"
            "\t * (U-Boot loads ucode into MURAM).  If so, just mark built and skip\n"
            "\t * the allocation to avoid double-allocating the arena.\n"
            "\t */\n",
            "\t/* Build the shared FE-VM chain before arming. */\n",
            "remove unowned chain adoption claim")
        engage_sig = (
            "int fman_pcd_fe_engage_profile(struct fman *fm, u8 hw_port_id,\n"
            "\t\t\t       u32 enq_fqid, enum fman_pcd_fe_profile profile)\n"
        )
        files[PCD] = replace_once(
            files[PCD], engage_sig,
            engage_sig.replace("int fman_pcd_fe_engage_profile(",
                               "static int __fman_pcd_fe_engage_profile_locked("),
            "locked FE engage implementation")
        files[PCD] = replace_once(
            files[PCD],
            "EXPORT_SYMBOL_GPL(fman_pcd_fe_engage_profile);\n",
            ENGAGE_WRAPPER + "EXPORT_SYMBOL_GPL(fman_pcd_fe_engage_profile);\n",
            "serialized FE engage")
        disengage_sig = "int fman_pcd_fe_disengage(struct fman *fm, u8 hw_port_id)\n"
        files[PCD] = replace_once(
            files[PCD], disengage_sig,
            disengage_sig.replace("int fman_pcd_fe_disengage(",
                                  "static int __fman_pcd_fe_disengage_locked("),
            "locked FE disengage implementation")
        files[PCD] = replace_once(
            files[PCD],
            "EXPORT_SYMBOL_GPL(fman_pcd_fe_disengage);\n",
            DISENGAGE_WRAPPER + "EXPORT_SYMBOL_GPL(fman_pcd_fe_disengage);\n" +
            DISENGAGE_PREPARED,
            "serialized FE disengage")
        files[HDR] = replace_once(
            files[HDR],
            "int fman_pcd_fe_disengage(struct fman *fm, u8 hw_port_id);\t/* F-289 */\n",
            "int fman_pcd_fe_disengage(struct fman *fm, u8 hw_port_id);\t/* F-289 */\n"
            "int fman_pcd_fe_disengage_prepared(struct fman *fm, u8 hw_port_id,\n"
            "\t\t\t\t    int (*prepare)(void *), void *data);\n",
            "prepared FE disengage declaration")
        files[PCD] = replace_function(
            files[PCD], "static ssize_t fman_pcd_fe_port_write(",
            "static const struct file_operations fman_pcd_fe_port_fops = {",
            DEBUG_PORT_DISABLED, "disable debugfs FE port mutation")
        files[PCD] = replace_function(
            files[PCD], "static ssize_t fman_pcd_fe_arm_write(",
            "\n\n/* ── fe_hash_probe:", DEBUG_ARM_DISABLED,
            "disable debugfs FE arm mutation")
        files[PCD] = replace_function(
            files[PCD], "static ssize_t fman_pcd_fe_extc_write(",
            "static const struct file_operations fman_pcd_fe_extc_fops = {",
            DEBUG_EXTC_DISABLED, "disable uncoordinated debugfs INV0 sync")
        files[PCD] = replace_once(
            files[PCD],
            " * existing fe_* code path. `cat fe_extc` reads the live register;\n"
            " * `echo sync > fe_extc` asserts INV0 and polls for hardware to clear\n"
            " * it, reporting the outcome to the kernel log. Uses fman_get_fpm_extc()/\n"
            " * fman_set_fpm_extc() (fman.c) rather than dereferencing fpm_regs\n"
            " * directly -- struct fman_fpm_regs is opaque outside fman.c.\n",
            " * existing fe_* code path. `cat fe_extc` reads the live register;\n"
            " * `echo sync > fe_extc` is disabled until global SYNC ownership\n"
            " * and pending-request handling are established.\n",
            "update debugfs INV0 description")
        files[PCD] = replace_once(
            files[PCD], "#define FMAN_FPM_EXTC_POLL_MAX\t100000U\n", "",
            "remove unused debugfs INV0 poll limit")
        files[PCD] = replace_once(
            files[PCD],
            "\tseq_printf(s, \"fmfp_extc: 0x%08x (INV0=%u)\\n\", val,\n"
            "\t\t   !!(val & FMAN_FPM_EXTC_INV0));\n",
            "\tseq_printf(s, \"fmfp_extc: 0x%08x (INV0=%u)\\n\", val,\n"
            "\t\t   !!(val & FMAN_FPM_EXTC_INV0));\n"
            "\tseq_puts(s, \"sync writes disabled: global INV0 ownership unresolved\\n\");\n",
            "report disabled debugfs INV0 sync")
        files[PCD] = replace_function(
            files[PCD], "static int fman_pcd_fe_arm_show(",
            "static int fman_pcd_fe_arm_open(", DEBUG_ARM_SHOW,
            "report full FE arm and fault state")
        files[PCD] = replace_once(
            files[PCD],
            "\tmutex_unlock(&pcd->fe_lock);\n\treturn 0;\n}\n\n"
            "static int fman_pcd_fe_port_open(",
            "\tmutex_unlock(&pcd->fe_lock);\n"
            "\tseq_puts(s, \"debugfs FE port changes disabled: ownership unresolved\\n\");\n"
            "\treturn 0;\n}\n\nstatic int fman_pcd_fe_port_open(",
            "report disabled debugfs FE port")
        files[PCD] = replace_once(
            files[PCD],
            "/* Debugfs wrapper — parse string, delegate to __fman_pcd_fe_arm_engage(). */\n",
            "", "remove stale debugfs arm comment")
        files[PCD] = replace_function(
            files[PCD], "static int fman_pcd_fe_arm_engage(",
            "\n\n/*\n * Typed disengage", "", "remove debugfs engage wrapper")
        files[PCD] = replace_function(
            files[PCD],
            "/* Debugfs wrapper — parse string, delegate to __fman_pcd_fe_arm_disengage(). */",
            "static int fman_pcd_fe_buffer_show(", "",
            "remove debugfs disengage wrapper")
        files[PCD] = replace_function(
            files[PCD],
            "/**\n * fman_pcd_port_recover() - Tier-1 de-wedge",
            "EXPORT_SYMBOL_GPL(fman_pcd_port_recover);",
            RECOVERY_DISABLED, "disable unverified FE recovery")
        files[PCD] = replace_function(
            files[PCD], "/* F-076: atomic fe_disengage_full",
            "static const struct file_operations fman_pcd_fe_disengage_full_fops = {",
            FULL_DISENGAGE_DISABLED, "disable full FE disengage")
        files[PCD] = replace_once(
            files[PCD],
            "int fman_pcd_fe_flow_add(struct fman *fm, u8 hw_port_id,\n",
            FLOW_READY +
            "int fman_pcd_fe_flow_add(struct fman *fm, u8 hw_port_id,\n",
            "FE flow publication precondition")
        files[PCD] = replace_once(
            files[PCD],
            "\tstruct fman_pcd_fe_obj *enq_obj;\n"
            "\tu32 target_fqid;\n\n"
            "\tif (!fm || !action || action->key_size == 0)\n",
            "\tstruct fman_pcd_fe_obj *enq_obj;\n"
            "\tu32 target_fqid;\n"
            "\tint err;\n\n"
            "\tif (!fm || !action || action->key_size == 0)\n",
            "FE flow admission result")
        files[PCD] = replace_once(
            files[PCD],
            "\tmutex_lock(&pcd->fe_lock);\n\n"
            "\t/* F-204: select the ehash table",
            "\tmutex_lock(&pcd->fe_lock);\n"
            "\terr = fman_pcd_fe_flow_ready_locked(pcd, hw_port_id);\n"
            "\tif (err) {\n"
            "\t\tmutex_unlock(&pcd->fe_lock);\n"
            "\t\treturn err;\n"
            "\t}\n\n"
            "\t/* F-204: select the ehash table",
            "FE flow admission under lock")
    except ValueError as exc:
        print(f"### F-291: FATAL: {exc}")
        return 1
    for path, text in files.items():
        with open(path, "w") as stream:
            stream.write(text)
    print("### F-291: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
