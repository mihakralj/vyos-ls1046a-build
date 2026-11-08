#!/usr/bin/env python3
"""Reject mismatched QMan FQ query responses before using their data."""

from pathlib import Path
import sys


QMAN = Path("drivers/soc/fsl/qbman/qman.c")
OLD = """\
\tDPAA_ASSERT((mcr->verb & QM_MCR_VERB_MASK) == QM_MCR_VERB_QUERYFQ);
\tif (mcr->result == QM_MCR_RESULT_OK)
"""
NEW = """\
\tif ((mcr->verb & QM_MCR_VERB_MASK) != QM_MCR_VERB_QUERYFQ) {
\t\tpr_err_ratelimited("qman: QUERYFQ response verb mismatch: 0x%02x\\n",
\t\t\t\t   mcr->verb);
\t\tret = -EIO;
\t\tgoto out;
\t}
\tif (mcr->result == QM_MCR_RESULT_OK)
"""
OLD_NP = """\
\tDPAA_ASSERT((mcr->verb & QM_MCR_VERB_MASK) == QM_MCR_VERB_QUERYFQ_NP);
\tif (mcr->result == QM_MCR_RESULT_OK)
"""
NEW_NP = """\
\tif ((mcr->verb & QM_MCR_VERB_MASK) != QM_MCR_VERB_QUERYFQ_NP) {
\t\tpr_err_ratelimited("qman: QUERYFQ_NP response verb mismatch: 0x%02x\\n",
\t\t\t\t   mcr->verb);
\t\tret = -EIO;
\t\tgoto out;
\t}
\tif (mcr->result == QM_MCR_RESULT_OK)
"""


def inspect(source, prefix, old, new):
    if source.count(prefix) != 1:
        raise ValueError(f"ambiguous {prefix}")
    start = source.index(prefix)
    end = source.index("\n}\n", start) + 3
    body = source[start:end]
    counts = body.count(old), body.count(new)
    if counts == (1, 0):
        return start, end, "old"
    if counts == (0, 1):
        return start, end, "new"
    raise ValueError(f"ambiguous or partially applied {prefix} response check")


def main():
    try:
        source = QMAN.read_text()
        sections = (
            inspect(source, "int qman_query_fq(", OLD, NEW),
            inspect(source, "int qman_query_fq_np(", OLD_NP, NEW_NP),
        )
        state = tuple(section[2] for section in sections)
        if state == ("new", "new"):
            print("### F-305: already applied")
            return 0
        if state not in (("old", "old"), ("new", "old")):
            raise ValueError("inconsistent FQ query response checks")
        for (start, end, status), old, new in sorted(
            zip(sections, (OLD, OLD_NP), (NEW, NEW_NP)), reverse=True,
        ):
            if status == "old":
                source = source[:start] + source[start:end].replace(old, new, 1) + source[end:]
        QMAN.write_text(source)
    except (OSError, ValueError) as error:
        print(f"### F-305: FATAL: {error}", file=sys.stderr)
        return 1
    print("### F-305: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
