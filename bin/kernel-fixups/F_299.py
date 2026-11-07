#!/usr/bin/env python3
"""Move fallible FQ DMA mapping before claiming the QMan MC slot."""

from pathlib import Path
import re
import sys


QMAN = Path("drivers/soc/fsl/qbman/qman.c")
MARKER = "F-299: map FQ context before MC start"
DECL_OLD = "\tint ret = 0;\n\n\tmyverb ="
DECL_NEW = "\tint ret = 0;\n\tdma_addr_t phys_fq = 0;\n\n\tmyverb ="
START = "\tmcc = qm_mc_start(&p->p);\n"
PREMAP = """\
\t/*
\t * F-299: map the FQ before reserving the MC command slot.
\t */
\tif (fq_isclear(fq, QMAN_FQ_FLAG_TO_DCPORTAL) &&
\t    opts &&
\t    (be16_to_cpu(opts->we_mask) & QM_INITFQ_WE_CONTEXTA)) {
\t\tstruct qman_portal *dma_portal = qman_dma_portal;

\t\tphys_fq = dma_map_single(dma_portal->config->dev, fq,
\t\t\t\t\t sizeof(*fq), DMA_TO_DEVICE);
\t\tif (dma_mapping_error(dma_portal->config->dev, phys_fq)) {
\t\t\tdev_err(dma_portal->config->dev, "dma_mapping failed\\n");
\t\t\tret = -EIO;
\t\t\tgoto out;
\t\t}
\t}
"""
OLD_MAP = """\
\t\t\tstruct qman_portal *p = qman_dma_portal;

\t\t\tphys_fq = dma_map_single(p->config->dev, fq,
\t\t\t\t\t\t sizeof(*fq), DMA_TO_DEVICE);
\t\t\tif (dma_mapping_error(p->config->dev, phys_fq)) {
\t\t\t\tdev_err(p->config->dev, "dma_mapping failed\\n");
\t\t\t\tret = -EIO;
\t\t\t\tgoto out;
\t\t\t}

\t\t\tqm_fqd_stashing_set64(&mcc->initfq.fqd, phys_fq);
"""
NEW_MAP = "\t\t\tqm_fqd_stashing_set64(&mcc->initfq.fqd, phys_fq);\n"
INNER_OLD = """\
\tif (fq_isclear(fq, QMAN_FQ_FLAG_TO_DCPORTAL)) {
\t\tdma_addr_t phys_fq;

\t\tmcc->initfq.we_mask |= cpu_to_be16(QM_INITFQ_WE_CONTEXTB);
"""
INNER_NEW = """\
\tif (fq_isclear(fq, QMAN_FQ_FLAG_TO_DCPORTAL)) {
\t\tmcc->initfq.we_mask |= cpu_to_be16(QM_INITFQ_WE_CONTEXTB);
"""


def init_function(source):
    start = source.index("int qman_init_fq(")
    end = source.index("EXPORT_SYMBOL(qman_init_fq);", start)
    return source[start:end]


def verify(source):
    init = init_function(source)
    start = init.find(START)
    mapping = init.find("dma_map_single(")
    error = init.find("dma_mapping_error(")
    commit = init.find("\tqm_mc_commit(&p->p, myverb);", start)
    return (
        source.count(MARKER) == 1
        and init.count(DECL_NEW) == 1
        and init.count(PREMAP) == 1
        and init.count(INNER_NEW) == 1
        and len(re.findall(r"\bdma_addr_t\s+phys_fq\b", init)) == 1
        and init.count("dma_map_single(") == 1
        and 0 <= mapping < error < start < commit
        and "goto out;" not in init[start:commit]
        and init.count("qm_fqd_stashing_set64(&mcc->initfq.fqd, phys_fq);") == 1
    )


def main():
    try:
        source = QMAN.read_text()
        if verify(source):
            print("### F-299: already applied")
            return 0

        init = init_function(source)
        if source.count(MARKER) == 0:
            if (
                init.count(DECL_OLD) != 1
                or init.count(START) != 1
                or init.count(OLD_MAP) != 1
                or init.count("dma_map_single(") != 1
            ):
                raise ValueError("ambiguous or changed INITFQ source")
            init = init.replace(DECL_OLD, DECL_NEW, 1)
            init = init.replace(START, PREMAP + START, 1)
            init = init.replace(OLD_MAP, NEW_MAP, 1)
            init = init.replace(
                "\t/* Issue an INITFQ_[PARKED|SCHED] management command */",
                f"\t/* {MARKER} */\n"
                "\t/* Issue an INITFQ_[PARKED|SCHED] management command */",
                1,
            )
        elif (
            source.count(MARKER) != 1
            or init.count(DECL_NEW) != 1
            or init.count(PREMAP) != 1
            or init.count(START) != 1
            or init.count(OLD_MAP) != 0
            or init.count(NEW_MAP) != 1
        ):
            raise ValueError("ambiguous or partially applied INITFQ source")
        if init.count(INNER_OLD) != 1:
            raise ValueError("ambiguous INITFQ DMA address declaration")
        init = init.replace(INNER_OLD, INNER_NEW, 1)
        start = source.index("int qman_init_fq(")
        end = source.index("EXPORT_SYMBOL(qman_init_fq);", start)
        source = source[:start] + init + source[end:]
        if not verify(source):
            raise ValueError("post-application invariant failed")
        QMAN.write_text(source)
    except (OSError, ValueError) as error:
        print(f"### F-299: FATAL: {error}", file=sys.stderr)
        return 1
    print("### F-299: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
