#!/usr/bin/env python3
"""Avoid releasing CAAM QI resources twice after devres registration fails."""

from pathlib import Path
import sys


QI = Path("drivers/crypto/caam/qi.c")
OLD = """\
\terr = devm_add_action_or_reset(qidev, caam_qi_shutdown, qidev);
\tif (err)
\t\tgoto fail2;

\tdev_info(qidev, "Linux CAAM Queue I/F driver initialised\\n");
\tgoto free_cpumask;

fail2:
\tkmem_cache_destroy(qi_cache);
fail:
"""
NEW = """\
\terr = devm_add_action_or_reset(qidev, caam_qi_shutdown, qidev);
\t/* On error, the reset action already released the QI resources. */
\tif (err)
\t\tgoto free_cpumask;

\tdev_info(qidev, "Linux CAAM Queue I/F driver initialised\\n");
\tgoto free_cpumask;

fail:
"""


def main():
    try:
        source = QI.read_text()
        if source.count("int caam_qi_init(") != 1:
            raise ValueError("ambiguous CAAM QI initialization function")
        start = source.index("int caam_qi_init(")
        end = source.index("\n}\n", start) + 3
        init = source[start:end]
        if init.count(OLD) == 0 and init.count(NEW) == 1:
            print("### F-304: already applied")
            return 0
        if init.count(OLD) != 1 or init.count(NEW) != 0:
            raise ValueError("ambiguous or partially applied CAAM QI cleanup")
        init = init.replace(OLD, NEW, 1)
        QI.write_text(source[:start] + init + source[end:])
    except (OSError, ValueError) as error:
        print(f"### F-304: FATAL: {error}", file=sys.stderr)
        return 1
    print("### F-304: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
