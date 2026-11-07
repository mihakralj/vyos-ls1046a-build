"""F-292: serialize RX/OH first-port accounting by actual port transition.

The existing first-port SYNC sequence and its timeout behavior are unchanged.
Other FMFP_EXTC writers, timeout ownership and hardware quiescence still need
separate protection.
"""

from pathlib import Path
import sys

ROOT = Path("drivers/net/ethernet/freescale/fman")
FMAN_H = ROOT / "fman.h"
FMAN_C = ROOT / "fman.c"
PORT_C = ROOT / "fman_port.c"
MARKER = "F-292: serialize RX/OH CC port transitions"


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise ValueError(f"{label}: found {count} anchors, expected 1")
    return text.replace(old, new, 1)


def update_port_function(source, name, port_type, has_rfpne):
    start = f"int {name}(struct fman_port *port, u32 cc_muram_off)\n"
    end = f"EXPORT_SYMBOL_GPL({name});"
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError(f"{name}: missing or duplicate function boundary")
    first = source.index(start)
    last = source.index(end, first)
    body = source[first:last]

    if has_rfpne:
        body = replace_once(body, "\tu32 rfpne;\n",
                            "\tstruct fman *fman;\n\tu32 rfpne;\n",
                            f"{name} declarations")
    else:
        body = replace_once(body, start + "{\n",
                            start + "{\n\tstruct fman *fman;\n\n",
                            f"{name} declarations")

    guard = (
        f"\tif (!port || port->port_type != {port_type})\n"
        "\t\treturn -EINVAL;\n"
    )
    body = replace_once(
        body, guard, guard +
        "\tfman = port->fm;\n"
        "\tif (!fman) {\n"
        "\t\tdev_err(port->dev, \"CC port has no FMan owner\\n\");\n"
        "\t\treturn -ENODEV;\n"
        "\t}\n"
        "\tmutex_lock(&fman->cc_port_lock);\n",
        f"{name} lock")

    accounting = (
        "\tif (cc_muram_off)\n"
        "\t\tfman_cc_engaged_inc(port->fm);\n"
        "\telse\n"
        "\t\tfman_cc_engaged_dec(port->fm);\n"
    )
    transitions = (
        "\tif (cc_muram_off && !port->cc_engaged) {\n"
        "\t\tfman_cc_engaged_inc(fman);\n"
        "\t\tport->cc_engaged = true;\n"
        "\t} else if (!cc_muram_off && port->cc_engaged) {\n"
        "\t\tfman_cc_engaged_dec(fman);\n"
        "\t\tport->cc_engaged = false;\n"
        "\t}\n"
    )
    body = replace_once(body, accounting, transitions, f"{name} accounting")
    body = replace_once(body, "\treturn 0;\n}\n",
                        "\tmutex_unlock(&fman->cc_port_lock);\n"
                        "\treturn 0;\n}\n", f"{name} unlock")
    return source[:first] + body + source[last:]


def main():
    try:
        header = FMAN_H.read_text()
        fman = FMAN_C.read_text()
        port = PORT_C.read_text()
        if MARKER in header:
            # F-293 adds one unlock on each RX/OH INV0 failure return.
            unlocks = 4 if "F-293: serialize and poison global INV0" in header else 2
            if (header.count(MARKER) != 1 or
                    fman.count("mutex_init(&fman->cc_port_lock);") != 1 or
                    fman.count("lockdep_assert_held(&fman->cc_port_lock);") != 3 or
                    port.count("bool cc_engaged;") != 1 or
                    port.count("mutex_lock(&fman->cc_port_lock);") != 2 or
                    port.count("mutex_unlock(&fman->cc_port_lock);") != unlocks or
                    port.count("port->cc_engaged = true;") != 2 or
                    port.count("port->cc_engaged = false;") != 2):
                raise ValueError("partially applied port transition guard")
            print("### F-292: already applied")
            return 0

        header = replace_once(
            header, "#include <linux/io.h>\n",
            "#include <linux/io.h>\n#include <linux/mutex.h>\n",
            "fman mutex include")
        header = replace_once(
            header,
            "\t/* F-215: count of RX ports currently engaged for AC_CC (non-zero\n"
            "\t * fmbm_rccb). The global FMFP_EXTC[INV0] SYNC is asserted only when\n"
            "\t * this is 0 (first port); a live sibling keeps the FMan controller\n"
            "\t * non-quiescent so INV0 could never clear -> multi-port arm wedge. */\n"
            "\tu8 cc_engaged_ports;\n",
            f"\t/* {MARKER}. */\n"
            "\tstruct mutex cc_port_lock;\n"
            "\t/* Number of managed RX and O/H ports with a CC base. */\n"
            "\tu8 cc_engaged_ports;\n",
            "fman CC lock")
        fman = replace_once(
            fman,
            "\tif (!fman)\n\t\treturn ERR_PTR(-ENOMEM);\n\n"
            "\tfm_node = of_node_get(of_dev->dev.of_node);\n",
            "\tif (!fman)\n\t\treturn ERR_PTR(-ENOMEM);\n"
            "\tmutex_init(&fman->cc_port_lock);\n\n"
            "\tfm_node = of_node_get(of_dev->dev.of_node);\n",
            "fman CC lock initialization")
        fman = replace_once(
            fman,
            "/* F-215: engaged-port counter. Tracks how many RX ports currently have\n"
            " * a non-zero fmbm_rccb (AC_CC engaged). Mutated only from the PCD\n"
            " * arm/disarm path (fman_port_set_cc_base), which the PCD layer\n"
            " * serializes; used to assert the global FMFP_EXTC[INV0] SYNC only for\n"
            " * the first engaged port. */\n",
            "/* F-215/F-292: RX and O/H callers hold cc_port_lock across the\n"
            " * first-port check, register programming and transition accounting. */\n",
            "counter ownership comment")
        for operation, original, guarded in (
            ("get",
             "\treturn fman ? fman->cc_engaged_ports : 0;\n",
             "\tif (!fman)\n\t\treturn 0;\n"
             "\tlockdep_assert_held(&fman->cc_port_lock);\n"
             "\treturn fman->cc_engaged_ports;\n"),
            ("increment",
             "\tif (fman)\n\t\tfman->cc_engaged_ports++;\n",
             "\tif (!fman)\n\t\treturn;\n"
             "\tlockdep_assert_held(&fman->cc_port_lock);\n"
             "\tfman->cc_engaged_ports++;\n"),
            ("decrement",
             "\tif (fman && fman->cc_engaged_ports)\n"
             "\t\tfman->cc_engaged_ports--;\n",
             "\tif (!fman)\n\t\treturn;\n"
             "\tlockdep_assert_held(&fman->cc_port_lock);\n"
             "\tif (fman->cc_engaged_ports)\n"
             "\t\tfman->cc_engaged_ports--;\n"),
        ):
            fman = replace_once(fman, original, guarded,
                                f"counter {operation} lock assertion")
        port = replace_once(
            port, "\tu32 ctrl_params_page;\t/* FM_CTL params page MURAM off (0116) */\n",
            "\tbool cc_engaged;\t/* protected by fman->cc_port_lock */\n"
            "\tu32 ctrl_params_page;\t/* FM_CTL params page MURAM off (0116) */\n",
            "port CC state")
        port = update_port_function(port, "fman_port_set_cc_base",
                                    "FMAN_PORT_TYPE_RX", True)
        port = update_port_function(port, "fman_port_set_oh_cc_base",
                                    "FMAN_PORT_TYPE_OH", False)
        port = replace_once(
            port,
            "\t/* F-215(sync-gate): maintain the engaged-port counter used above.\n"
            "\t * arm (cc_muram_off != 0) -> ++, teardown (== 0) -> -- (floored). */\n",
            "\t/* F-292: account each port only on an arm/disarm transition. */\n",
            "RX accounting comment")
    except (OSError, ValueError) as exc:
        print(f"### F-292: FATAL: {exc}", file=sys.stderr)
        return 1

    FMAN_H.write_text(header)
    FMAN_C.write_text(fman)
    PORT_C.write_text(port)
    print("### F-292: serialized RX/OH CC transition accounting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
