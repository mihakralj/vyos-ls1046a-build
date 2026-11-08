#!/usr/bin/env python3
"""Report CAAM QI full-ring errors without changing other QMan callers."""

from pathlib import Path
import sys


PATH = Path("drivers/crypto/caam/qi.c")
OLD_DECL = """\
int caam_qi_enqueue(struct device *qidev, struct caam_drv_req *req)
{
\tstruct qm_fd fd;
"""
NEW_DECL = """\
int caam_qi_enqueue(struct device *qidev, struct caam_drv_req *req)
{
\tstruct qman_fq *fq;
\tstruct qm_fd fd;
"""
OLD_LOOP = """\
\tdo {
\t\trefcount_inc(&req->drv_ctx->refcnt);
\t\tret = qman_enqueue(req->drv_ctx->req_fq, &fd);
\t\tif (likely(!ret))
\t\t\treturn 0;

\t\trefcount_dec(&req->drv_ctx->refcnt);
\t\tif (ret != -EBUSY)
\t\t\tbreak;
\t\tnum_retries++;
\t} while (num_retries < CAAM_QI_ENQUEUE_RETRIES);

\tdev_err(qidev, "qman_enqueue failed: %d\\n", ret);
"""
PREVIOUS_LOOP = """\
\tdo {
\t\trefcount_inc(&req->drv_ctx->refcnt);
\t\tfq = READ_ONCE(req->drv_ctx->req_fq);
\t\tret = qman_enqueue_fqid(fq, fq->fqid, &fd);
\t\tif (likely(!ret))
\t\t\treturn 0;

\t\trefcount_dec(&req->drv_ctx->refcnt);
\t\tif (ret != -EBUSY)
\t\t\tbreak;
\t\tnum_retries++;
\t} while (num_retries < CAAM_QI_ENQUEUE_RETRIES);

\tif (ret == -EBUSY)
\t\tret = -EAGAIN;

\tdev_err(qidev, "qman_enqueue failed: %d\\n", ret);
"""
NEW_LOOP = """\
\trefcount_inc(&req->drv_ctx->refcnt);
\tdo {
\t\tfq = READ_ONCE(req->drv_ctx->req_fq);
\t\tret = qman_enqueue_fqid(fq, fq->fqid, &fd);
\t\tif (likely(!ret))
\t\t\treturn 0;

\t\tif (ret != -EBUSY)
\t\t\tbreak;
\t\tnum_retries++;
\t} while (num_retries < CAAM_QI_ENQUEUE_RETRIES);
\trefcount_dec(&req->drv_ctx->refcnt);

\tif (ret == -EBUSY)
\t\tret = -EAGAIN;

\tdev_err(qidev, "qman_enqueue failed: %d\\n", ret);
"""
DMA_GUARD = "\tdma_unmap_single(qidev, addr, sizeof(req->fd_sgt), DMA_BIDIRECTIONAL);\n"
REPLACEMENTS = (
    (OLD_DECL, NEW_DECL),
    (OLD_LOOP, NEW_LOOP),
)


def main():
    try:
        source = PATH.read_text()
        if source.count(DMA_GUARD) != 1:
            raise ValueError("F-301 enqueue DMA cleanup is missing or ambiguous")
        declarations = (source.count(OLD_DECL), source.count(NEW_DECL))
        loops = (source.count(OLD_LOOP), source.count(PREVIOUS_LOOP),
                 source.count(NEW_LOOP))
        if declarations == (0, 1) and loops == (0, 0, 1):
            print("### F-302: already applied")
            return 0
        if declarations == (1, 0) and loops == (1, 0, 0):
            source = source.replace(OLD_DECL, NEW_DECL, 1)
            source = source.replace(OLD_LOOP, NEW_LOOP, 1)
        elif declarations == (0, 1) and loops == (0, 1, 0):
            source = source.replace(PREVIOUS_LOOP, NEW_LOOP, 1)
        else:
            raise ValueError("ambiguous or partially applied CAAM enqueue source")
        PATH.write_text(source)
    except (OSError, ValueError) as error:
        print(f"### F-302: FATAL: {error}", file=sys.stderr)
        return 1
    print("### F-302: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
