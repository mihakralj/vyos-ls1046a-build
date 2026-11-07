#!/usr/bin/env python3
"""Reject invalid management responses during QMan FQ shutdown."""

from pathlib import Path
import sys


QMAN = Path("drivers/soc/fsl/qbman/qman.c")
NP_OLD = """\
\tDPAA_ASSERT((mcr->verb & QM_MCR_VERB_MASK) == QM_MCR_VERB_QUERYFQ_NP);
"""
NP_NEW = """\
\tif ((mcr->verb & QM_MCR_VERB_MASK) != QM_MCR_VERB_QUERYFQ_NP ||
\t    mcr->result != QM_MCR_RESULT_OK) {
\t\tdev_err(dev, "QUERYFQ_NP failed: FQ 0x%x, verb 0x%02x, result 0x%02x\\n",
\t\t\tfqid, mcr->verb, mcr->result);
\t\tret = -EIO;
\t\tgoto out;
\t}
"""
QUERY_OLD = """\
\tDPAA_ASSERT((mcr->verb & QM_MCR_VERB_MASK) == QM_MCR_VERB_QUERYFQ);
"""
QUERY_NEW = """\
\tif ((mcr->verb & QM_MCR_VERB_MASK) != QM_MCR_VERB_QUERYFQ ||
\t    mcr->result != QM_MCR_RESULT_OK) {
\t\tdev_err(dev, "QUERYFQ failed: FQ 0x%x, verb 0x%02x, result 0x%02x\\n",
\t\t\tfqid, mcr->verb, mcr->result);
\t\tret = -EIO;
\t\tgoto out;
\t}
"""
RETIRE_OLD = """\
\t\tDPAA_ASSERT((mcr->verb & QM_MCR_VERB_MASK) ==
\t\t\t    QM_MCR_VERB_ALTER_RETIRE);
"""
RETIRE_NEW = """\
\t\tif ((mcr->verb & QM_MCR_VERB_MASK) != QM_MCR_VERB_ALTER_RETIRE) {
\t\t\tdev_err(dev, "ALTER_RETIRE verb mismatch: FQ 0x%x, verb 0x%02x\\n",
\t\t\t\tfqid, mcr->verb);
\t\t\tret = -EIO;
\t\t\tgoto out;
\t\t}
"""
OOS_OLD = """\
\t\tDPAA_ASSERT((mcr->verb & QM_MCR_VERB_MASK) ==
\t\t\t    QM_MCR_VERB_ALTER_OOS);
"""
OOS_NEW = """\
\t\tif ((mcr->verb & QM_MCR_VERB_MASK) != QM_MCR_VERB_ALTER_OOS) {
\t\t\tdev_err(dev, "ALTER_OOS verb mismatch: FQ 0x%x, verb 0x%02x\\n",
\t\t\t\tfqid, mcr->verb);
\t\t\tret = -EIO;
\t\t\tgoto out;
\t\t}
"""
REPLACEMENTS = (
    (NP_OLD, NP_NEW, 1),
    (QUERY_OLD, QUERY_NEW, 1),
    (RETIRE_OLD, RETIRE_NEW, 1),
    (OOS_OLD, OOS_NEW, 2),
)


def main():
    try:
        source = QMAN.read_text()
        if source.count("int qman_shutdown_fq(") != 1:
            raise ValueError("ambiguous QMan shutdown function")
        start = source.index("int qman_shutdown_fq(")
        end = source.index("\n}\n", start) + 3
        shutdown = source[start:end]
        if all(shutdown.count(old) == 0 and shutdown.count(new) == count
               for old, new, count in REPLACEMENTS):
            print("### F-306: already applied")
            return 0
        if any(shutdown.count(old) != count or shutdown.count(new) != 0
               for old, new, count in REPLACEMENTS):
            raise ValueError("ambiguous or partially applied shutdown response checks")
        for old, new, count in REPLACEMENTS:
            shutdown = shutdown.replace(old, new, count)
        QMAN.write_text(source[:start] + shutdown + source[end:])
    except (OSError, ValueError) as error:
        print(f"### F-306: FATAL: {error}", file=sys.stderr)
        return 1
    print("### F-306: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
