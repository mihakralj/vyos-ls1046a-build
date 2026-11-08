#!/usr/bin/env python3
"""Keep QMan CGR safe operations on each registration's creating CPU."""

from pathlib import Path
import sys


QMAN = Path("drivers/soc/fsl/qbman/qman.c")
HEADER = Path("include/soc/fsl/qman.h")
MARKER = "F-298: per-registration portal CPU"
OLD_FIELD = (
    "\tu16 chan; /* portal channel this object is created on */\n"
    "\tstruct list_head node;\n"
)
NEW_FIELD = (
    "\tu16 chan; /* portal channel this object is created on */\n"
    "\tint portal_cpu; /* F-298: per-registration portal CPU */\n"
    "\tstruct list_head node;\n"
)
OLD_ARRAY = "static u8 qman_cgr_cpus[CGR_NUM];\n"
OLD_ASSIGN = "\tqman_cgr_cpus[cgr->cgrid] = smp_processor_id();\n"
NEW_ASSIGN = "\tcgr->portal_cpu = smp_processor_id();\n"
OLD_LOOKUP = "qman_cgr_cpus[cgr->cgrid]"
NEW_LOOKUP = "cgr->portal_cpu"
CALLBACK_DISABLE_LOOKUP = (
    "\tstruct qman_portal *p = per_cpu_ptr(&qman_affine_portal,\n"
    "\t\t\t\t\t    cgr->portal_cpu);\n"
)


def main():
    try:
        qman = QMAN.read_text()
        header = HEADER.read_text()
        callback_lookups = qman.count(CALLBACK_DISABLE_LOOKUP)
        if callback_lookups not in (0, 1):
            raise ValueError("ambiguous CGR callback-disable portal lookup")
        if (header.count(NEW_FIELD) == 1 and header.count(MARKER) == 1 and
                header.count("portal_cpu;") == 1 and
                qman.count(NEW_ASSIGN) == 1 and
                qman.count("if (cgr->portal_cpu != smp_processor_id())") == 2 and
                qman.count("smp_call_function_single(cgr->portal_cpu,") == 2 and
                qman.count(NEW_LOOKUP) == 5 + callback_lookups and
                "qman_cgr_cpus" not in qman and
                "F-297: preserve callback result" in qman):
            print("### F-298: already applied")
            return 0
        if (header.count(OLD_FIELD) != 1 or "portal_cpu" in header or
                qman.count(OLD_ARRAY) != 1 or qman.count(OLD_ASSIGN) != 1 or
                qman.count(OLD_LOOKUP) != 5 or NEW_LOOKUP in qman or
                "F-297: preserve callback result" not in qman):
            raise ValueError("ambiguous, partial or changed CGR ownership source")
        qman = qman.replace(OLD_ARRAY, "", 1)
        qman = qman.replace(OLD_ASSIGN, NEW_ASSIGN, 1)
        qman = qman.replace(OLD_LOOKUP, NEW_LOOKUP)
        header = header.replace(OLD_FIELD, NEW_FIELD, 1)
        QMAN.write_text(qman)
        HEADER.write_text(header)
    except (OSError, ValueError) as error:
        print(f"### F-298: FATAL: {error}", file=sys.stderr)
        return 1
    print("### F-298: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
