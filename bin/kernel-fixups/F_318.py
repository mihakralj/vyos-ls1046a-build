#!/usr/bin/env python3
"""F-318: retain CAAM QI FQ, descriptor, DMA key, and parent on teardown error."""

from pathlib import Path
import sys


QI_H = Path("drivers/crypto/caam/qi.h")
QI_C = Path("drivers/crypto/caam/qi.c")
ALG_C = Path("drivers/crypto/caam/caamalg_qi.c")
MARKER = "caam_drv_ctx_rel_checked"

QI_H_OLD = "void caam_drv_ctx_rel(struct caam_drv_ctx *drv_ctx);\n"
QI_H_NEW = (
    "int caam_drv_ctx_rel_checked(struct caam_drv_ctx *drv_ctx);\n"
    "void caam_drv_ctx_rel(struct caam_drv_ctx *drv_ctx);\n"
)

KILL_OLD = """\
\tret = qman_oos_fq(fq);
\tif (ret)
\t\tdev_err(qidev, "OOS of FQID: %u failed\\n", fq->fqid);

\tqman_destroy_fq(fq);
\tkfree(fq);

\treturn ret;
"""
KILL_NEW = """\
\tret = qman_oos_fq(fq);
\tif (ret) {
\t\tdev_err(qidev, "OOS of FQID: %u failed (%d); retaining FQ\\n",
\t\t\tfq->fqid, ret);
\t\treturn ret;
\t}

\tqman_destroy_fq(fq);
\tkfree(fq);

\treturn 0;
"""

REL_OLD = """\
void caam_drv_ctx_rel(struct caam_drv_ctx *drv_ctx)
{
\tif (IS_ERR_OR_NULL(drv_ctx))
\t\treturn;

\t/* Remove request FQ */
\tif (kill_fq(drv_ctx->qidev, drv_ctx->req_fq))
\t\tdev_err(drv_ctx->qidev, "Crypto session req FQ kill failed\\n");

\tdma_unmap_single(drv_ctx->qidev, drv_ctx->context_a,
\t\t\t sizeof(drv_ctx->sh_desc) + sizeof(drv_ctx->prehdr),
\t\t\t DMA_BIDIRECTIONAL);
\tkfree(drv_ctx);
}
EXPORT_SYMBOL(caam_drv_ctx_rel);
"""
REL_NEW = """\
int caam_drv_ctx_rel_checked(struct caam_drv_ctx *drv_ctx)
{
\tint ret;

\tif (IS_ERR_OR_NULL(drv_ctx))
\t\treturn 0;

\t/* Remove request FQ; retain descriptor and ctx if retirement/OOS fails. */
\tret = kill_fq(drv_ctx->qidev, drv_ctx->req_fq);
\tif (ret) {
\t\tdev_err(drv_ctx->qidev,
\t\t\t"Crypto session req FQ kill failed (%d); retaining ctx\\n",
\t\t\tret);
\t\treturn ret;
\t}

\tdma_unmap_single(drv_ctx->qidev, drv_ctx->context_a,
\t\t\t sizeof(drv_ctx->sh_desc) + sizeof(drv_ctx->prehdr),
\t\t\t DMA_BIDIRECTIONAL);
\tkfree(drv_ctx);
\treturn 0;
}
EXPORT_SYMBOL(caam_drv_ctx_rel_checked);

void caam_drv_ctx_rel(struct caam_drv_ctx *drv_ctx)
{
\t(void)caam_drv_ctx_rel_checked(drv_ctx);
}
EXPORT_SYMBOL(caam_drv_ctx_rel);
"""

SHUT_OLD = """\
static void caam_qi_shutdown(void *data)
{
\tint i;
\tstruct device *qidev = data;
\tstruct caam_qi_priv *priv = &qipriv;
\tconst cpumask_t *cpus = qman_affine_cpus();

\tfor_each_cpu(i, cpus) {
\t\tstruct napi_struct *irqtask;

\t\tirqtask = &per_cpu_ptr(&pcpu_qipriv.caam_napi, i)->irqtask;
\t\tnapi_disable(irqtask);
\t\tnetif_napi_del(irqtask);

\t\tif (kill_fq(qidev, per_cpu(pcpu_qipriv.rsp_fq, i)))
\t\t\tdev_err(qidev, "Rsp FQ kill failed, cpu: %d\\n", i);
\t\tfree_netdev(per_cpu(pcpu_qipriv.net_dev, i));
\t}

\tqman_delete_cgr_safe(&priv->cgr);
\tqman_release_cgrid(priv->cgr.cgrid);

\tkmem_cache_destroy(qi_cache);
}
"""
SHUT_NEW = """\
static void caam_qi_shutdown(void *data)
{
\tint i;
\tbool retained = false;
\tstruct device *qidev = data;
\tstruct caam_qi_priv *priv = &qipriv;
\tconst cpumask_t *cpus = qman_affine_cpus();

\tfor_each_cpu(i, cpus) {
\t\tstruct napi_struct *irqtask;

\t\tirqtask = &per_cpu_ptr(&pcpu_qipriv.caam_napi, i)->irqtask;
\t\tnapi_disable(irqtask);
\t\tnetif_napi_del(irqtask);

\t\tif (kill_fq(qidev, per_cpu(pcpu_qipriv.rsp_fq, i))) {
\t\t\tdev_err(qidev,
\t\t\t\t"Rsp FQ kill failed, cpu: %d; retaining netdev\\n",
\t\t\t\ti);
\t\t\tretained = true;
\t\t\tcontinue;
\t\t}
\t\tfree_netdev(per_cpu(pcpu_qipriv.net_dev, i));
\t}

\tif (retained) {
\t\tdev_err(qidev,
\t\t\t"CAAM QI shutdown incomplete; retaining CGR and cache\\n");
\t\treturn;
\t}

\tqman_delete_cgr_safe(&priv->cgr);
\tqman_release_cgrid(priv->cgr.cgrid);

\tkmem_cache_destroy(qi_cache);
}
"""

CTX_OLD = "\tu8 key[CAAM_MAX_KEY_SIZE];\n"
CTX_NEW = "\tu8 *key;\n"

INIT_OLD = """\
\tctx->key_dma = dma_map_single(dev, ctx->key, sizeof(ctx->key),
\t\t\t\t      ctx->dir);
\tif (dma_mapping_error(dev, ctx->key_dma)) {
\t\tdev_err(dev, "unable to map key\\n");
\t\tcaam_jr_free(ctx->jrdev);
\t\treturn -ENOMEM;
\t}
"""
INIT_NEW = """\
\tctx->key = kzalloc(CAAM_MAX_KEY_SIZE, GFP_KERNEL);
\tif (!ctx->key) {
\t\tcaam_jr_free(ctx->jrdev);
\t\treturn -ENOMEM;
\t}

\tctx->key_dma = dma_map_single(dev, ctx->key, CAAM_MAX_KEY_SIZE,
\t\t\t\t      ctx->dir);
\tif (dma_mapping_error(dev, ctx->key_dma)) {
\t\tdev_err(dev, "unable to map key\\n");
\t\tkfree_sensitive(ctx->key);
\t\tctx->key = NULL;
\t\tcaam_jr_free(ctx->jrdev);
\t\treturn -ENOMEM;
\t}
"""

EXIT_OLD = """\
static void caam_exit_common(struct caam_ctx *ctx)
{
\tcaam_drv_ctx_rel(ctx->drv_ctx[ENCRYPT]);
\tcaam_drv_ctx_rel(ctx->drv_ctx[DECRYPT]);

\tdma_unmap_single(ctx->jrdev->parent, ctx->key_dma, sizeof(ctx->key),
\t\t\t ctx->dir);

\tcaam_jr_free(ctx->jrdev);
}
"""
EXIT_NEW = """\
static void caam_exit_common(struct caam_ctx *ctx)
{
\tbool retained = false;

\tif (caam_drv_ctx_rel_checked(ctx->drv_ctx[ENCRYPT]))
\t\tretained = true;
\tif (caam_drv_ctx_rel_checked(ctx->drv_ctx[DECRYPT]))
\t\tretained = true;

\tif (retained) {
\t\tdev_err(ctx->jrdev->parent,
\t\t\t"CAAM QI release incomplete; retaining DMA key and job ring\\n");
\t\treturn;
\t}

\tdma_unmap_single(ctx->jrdev->parent, ctx->key_dma, CAAM_MAX_KEY_SIZE,
\t\t\t ctx->dir);
\tkfree_sensitive(ctx->key);

\tcaam_jr_free(ctx->jrdev);
}
"""


def replace_once(text, old, new, label):
    if text.count(old) != 1 or text.count(new) != 0:
        raise ValueError(f"anchor drift in {label}")
    return text.replace(old, new, 1)


def already_applied(qi_h, qi_c, alg_c):
    checks = (
        qi_h.count(QI_H_NEW) == 1,
        qi_c.count(KILL_NEW) == 1,
        qi_c.count(REL_NEW) == 1,
        qi_c.count(SHUT_NEW) == 1,
        alg_c.count(CTX_NEW) == 1,
        alg_c.count(INIT_NEW) == 1,
        alg_c.count(EXIT_NEW) == 1,
    )
    if all(checks):
        return True
    if any(checks):
        raise ValueError("partially applied F-318 state")
    return False


def main():
    try:
        qi_h = QI_H.read_text()
        qi_c = QI_C.read_text()
        alg_c = ALG_C.read_text()

        if already_applied(qi_h, qi_c, alg_c):
            print("### F-318: already applied")
            return 0

        new_qi_h = replace_once(qi_h, QI_H_OLD, QI_H_NEW, "qi.h rel decl")
        new_qi_c = replace_once(qi_c, KILL_OLD, KILL_NEW, "qi.c kill_fq")
        new_qi_c = replace_once(new_qi_c, REL_OLD, REL_NEW, "qi.c caam_drv_ctx_rel")
        new_qi_c = replace_once(new_qi_c, SHUT_OLD, SHUT_NEW, "qi.c caam_qi_shutdown")

        new_alg_c = replace_once(alg_c, CTX_OLD, CTX_NEW, "caamalg_qi.c struct caam_ctx")
        new_alg_c = replace_once(new_alg_c, INIT_OLD, INIT_NEW, "caamalg_qi.c caam_init_common")
        new_alg_c = replace_once(new_alg_c, EXIT_OLD, EXIT_NEW, "caamalg_qi.c caam_exit_common")
        if "sizeof(ctx->key)" in new_alg_c:
            raise ValueError("remaining sizeof(ctx->key) after pointer conversion")

        QI_H.write_text(new_qi_h)
        QI_C.write_text(new_qi_c)
        ALG_C.write_text(new_alg_c)
    except (OSError, ValueError) as error:
        print(f"### F-318: FATAL: {error}", file=sys.stderr)
        return 1
    print("### F-318: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
