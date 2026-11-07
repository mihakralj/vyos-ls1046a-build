#!/usr/bin/env python3
"""Preserve QMan retirement flags across subsequent portal operations."""

from pathlib import Path
import sys


QMAN = Path("drivers/soc/fsl/qbman/qman.c")
DECL_OLD = "\tu8 state;\n"
DECL_NEW = "\tu8 state, retire_fqs;\n"
SAVE_OLD = "\t\tres = mcr->result; /* Make a copy as we reuse MCR below */\n"
SAVE_NEW = """\
\t\t/* Save both fields before our subsequent portal operations. */
\t\tres = mcr->result;
\t\tretire_fqs = mcr->alterfq.fqs;
"""
ORL_OLD = "if (!(mcr->alterfq.fqs & QM_MCR_FQS_ORLPRESENT))"
ORL_NEW = "if (!(retire_fqs & QM_MCR_FQS_ORLPRESENT))"
EMPTY_OLD = "if (drain || mcr->alterfq.fqs & QM_MCR_FQS_NOTEMPTY)"
EMPTY_NEW = "if (drain || retire_fqs & QM_MCR_FQS_NOTEMPTY)"
REPLACEMENTS = (
    (DECL_OLD, DECL_NEW),
    (SAVE_OLD, SAVE_NEW),
    (ORL_OLD, ORL_NEW),
    (EMPTY_OLD, EMPTY_NEW),
)


def main():
    try:
        source = QMAN.read_text()
        if source.count("int qman_shutdown_fq(") != 1:
            raise ValueError("ambiguous QMan shutdown function")
        start = source.index("int qman_shutdown_fq(")
        end = source.index("\n}\n", start) + 3
        shutdown = source[start:end]
        if all(shutdown.count(old) == 0 and shutdown.count(new) == 1
               for old, new in REPLACEMENTS):
            print("### F-303: already applied")
            return 0
        if any(shutdown.count(old) != 1 or shutdown.count(new) != 0
               for old, new in REPLACEMENTS):
            raise ValueError("ambiguous or partially applied QMan shutdown source")
        for old, new in REPLACEMENTS:
            shutdown = shutdown.replace(old, new, 1)
        QMAN.write_text(source[:start] + shutdown + source[end:])
    except (OSError, ValueError) as error:
        print(f"### F-303: FATAL: {error}", file=sys.stderr)
        return 1
    print("### F-303: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
