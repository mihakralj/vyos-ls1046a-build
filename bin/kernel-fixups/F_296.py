#!/usr/bin/env python3
"""Prevent sysfs unbind of the built-in QMan and BMan portal drivers."""

from pathlib import Path
import sys


ROOT = Path("drivers/soc/fsl/qbman")
MARKER = "F-296: no sysfs unbind"


def driver_block(name, guarded):
    guard = (
        f"\t\t/* {MARKER}; per-CPU portals retain devres. */\n"
        "\t\t.suppress_bind_attrs = true,\n"
        if guarded else ""
    )
    return (
        f"static struct platform_driver {name}_portal_driver = {{\n"
        "\t.driver = {\n"
        "\t\t.name = KBUILD_MODNAME,\n"
        f"\t\t.of_match_table = {name}_portal_ids,\n"
        f"{guard}"
        "\t},\n"
        f"\t.probe = {name}_portal_probe,\n"
        "};\n"
    )


def main():
    pending = []
    try:
        for name in ("qman", "bman"):
            path = ROOT / f"{name}_portal.c"
            source = path.read_text()
            old = driver_block(name, False)
            new = driver_block(name, True)
            if source.count(new) == 1 and source.count(MARKER) == 1:
                pending.append((path, source, False))
            elif (source.count(old) == 1 and MARKER not in source and
                  "pcfg = devm_kmalloc(dev, sizeof(*pcfg), GFP_KERNEL);" in source and
                  "init_pcfg(pcfg)" in source):
                pending.append((path, source.replace(old, new, 1), True))
            else:
                raise ValueError(f"{path}: ambiguous or changed portal driver")

        if any(changed for _, _, changed in pending) and not all(
            changed for _, _, changed in pending
        ):
            raise ValueError("partially applied portal guard")

        for path, source, changed in pending:
            if changed:
                path.write_text(source)
    except (OSError, ValueError) as error:
        print(f"### F-296: FATAL: {error}", file=sys.stderr)
        return 1

    print("### F-296: applied" if pending[0][2] else "### F-296: already applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
