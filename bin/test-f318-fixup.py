#!/usr/bin/env python3
"""Replay F-318 CAAM QI release retention and reject source drift."""

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXUP = ROOT / "bin/kernel-fixups/F_318.py"
QI_H = Path("drivers/crypto/caam/qi.h")
QI_C = Path("drivers/crypto/caam/qi.c")
ALG_C = Path("drivers/crypto/caam/caamalg_qi.c")


def fixture_sources():
    qi_h = "void caam_drv_ctx_rel(struct caam_drv_ctx *drv_ctx);\n"
    qi_c = """\
static int kill_fq(struct device *qidev, struct qman_fq *fq)
{
\tret = qman_oos_fq(fq);
\tif (ret)
\t\tdev_err(qidev, "OOS of FQID: %u failed\\n", fq->fqid);

\tqman_destroy_fq(fq);
\tkfree(fq);

\treturn ret;
}

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
    alg_c = """\
struct caam_ctx {
\tu8 key[CAAM_MAX_KEY_SIZE];
\tdma_addr_t key_dma;
};

static int caam_init_common(struct caam_ctx *ctx)
{
\tctx->key_dma = dma_map_single(dev, ctx->key, sizeof(ctx->key),
\t\t\t\t      ctx->dir);
\tif (dma_mapping_error(dev, ctx->key_dma)) {
\t\tdev_err(dev, "unable to map key\\n");
\t\tcaam_jr_free(ctx->jrdev);
\t\treturn -ENOMEM;
\t}
\treturn 0;
}

static void caam_exit_common(struct caam_ctx *ctx)
{
\tcaam_drv_ctx_rel(ctx->drv_ctx[ENCRYPT]);
\tcaam_drv_ctx_rel(ctx->drv_ctx[DECRYPT]);

\tdma_unmap_single(ctx->jrdev->parent, ctx->key_dma, sizeof(ctx->key),
\t\t\t ctx->dir);

\tcaam_jr_free(ctx->jrdev);
}
"""
    return qi_h, qi_c, alg_c


class F318FixupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.qi_h, self.qi_c, self.alg_c = fixture_sources()
        for rel, content in ((QI_H, self.qi_h), (QI_C, self.qi_c), (ALG_C, self.alg_c)):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

    def run_fixup(self):
        return subprocess.run(
            ["python3", str(FIXUP)], cwd=self.root, capture_output=True, text=True,
        )

    def test_apply_and_idempotence(self):
        res = self.run_fixup()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        qi_h = (self.root / QI_H).read_text()
        qi_c = (self.root / QI_C).read_text()
        alg_c = (self.root / ALG_C).read_text()
        self.assertIn("int caam_drv_ctx_rel_checked(", qi_h)
        self.assertIn("int caam_drv_ctx_rel_checked(", qi_c)
        self.assertIn("u8 *key;", alg_c)
        self.assertNotIn("sizeof(ctx->key)", alg_c)
        self.assertIn("kfree_sensitive(ctx->key);", alg_c)

        res = self.run_fixup()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("already applied", res.stdout)
        self.assertEqual((self.root / QI_H).read_text(), qi_h)
        self.assertEqual((self.root / QI_C).read_text(), qi_c)
        self.assertEqual((self.root / ALG_C).read_text(), alg_c)

    def test_drift_fails_atomically(self):
        cases = (
            ("extra sizeof(ctx->key)", self.qi_h, self.qi_c,
             self.alg_c + "size_t bad(struct caam_ctx *ctx) { return sizeof(ctx->key); }\n"),
            ("missing kill_fq OOS", self.qi_h,
             self.qi_c.replace("qman_oos_fq(fq)", "qman_oos_fq_other(fq)"), self.alg_c),
            ("missing rel decl", "", self.qi_c, self.alg_c),
        )
        for label, h, c, a in cases:
            with self.subTest(label=label):
                (self.root / QI_H).write_text(h)
                (self.root / QI_C).write_text(c)
                (self.root / ALG_C).write_text(a)
                res = self.run_fixup()
                self.assertNotEqual(res.returncode, 0, label)
                self.assertEqual((self.root / QI_H).read_text(), h)
                self.assertEqual((self.root / QI_C).read_text(), c)
                self.assertEqual((self.root / ALG_C).read_text(), a)


if __name__ == "__main__":
    unittest.main()
