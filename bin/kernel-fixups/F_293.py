"""Serialize FMan INV0 requests and fail closed on pending or timed-out SYNC.

Once INV0 ownership is uncertain, refuse new ehash publication; records
already published stay owned and retained until cold boot.
"""

from pathlib import Path
import sys


ROOT = Path("drivers/net/ethernet/freescale/fman")
FMAN_H = ROOT / "fman.h"
FMAN_C = ROOT / "fman.c"
PORT_C = ROOT / "fman_port.c"
PCD_C = ROOT / "fman_pcd.c"
MARKER = "F-293: serialize and poison global INV0"


PUBLISH_ANCHOR = (
    "\t\tpr_warn_ratelimited(\"fman_pcd: ehash insert has no FMan owner\\n\");\n"
    "\t\treturn -ENODEV;\n"
    "\t}\n"
)
PUBLISH_FENCE = (
    "\t/* F-293: no new publication after an unowned or failed INV0. */\n"
    "\tif (fman_fpm_inv0_faulted(fman_pcd_get_fman(t->pcd))) {\n"
    "\t\tpr_warn_ratelimited(\"fman_pcd: ehash insert refused: INV0 faulted until cold boot\\n\");\n"
    "\t\treturn -ESHUTDOWN;\n"
    "\t}\n"
)


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise ValueError(f"{label}: found {count} anchors, expected 1")
    return text.replace(old, new, 1)


def disable_debugfs_sync(source):
    start = "static ssize_t fman_pcd_fe_extc_write("
    if source.count(start) != 1:
        raise ValueError("fe_extc write function missing or duplicated")
    first = source.index(start)
    brace = source.index("{", first)
    end = source.index("\n}\n", brace) + 3
    signature = source[first:brace + 1]
    body = (
        "\n"
        "\t(void)file;\n"
        "\t(void)ubuf;\n"
        "\t(void)count;\n"
        "\t(void)ppos;\n"
        "\tpr_warn_ratelimited(\"fman_pcd: fe_extc SYNC writes disabled until INV0 ownership is proven\\n\");\n"
        "\treturn -EOPNOTSUPP;\n"
        "}\n"
    )
    return source[:first] + signature + body + source[end:]


def update_port(source, name, sync_label):
    start = f"int {name}(struct fman_port *port, u32 cc_muram_off)\n"
    end = f"EXPORT_SYMBOL_GPL({name});"
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError(f"{name}: function boundary missing or duplicated")
    first = source.index(start)
    last = source.index(end, first)
    body = source[first:last]
    body = replace_once(
        body,
        "\tstruct fman *fman;\n",
        "\tstruct fman *fman;\n\tu32 extc;\n\tunsigned int polls;\n\tint err;\n",
        f"{name} declarations",
    )
    old = (
        "\t\tconst u32 inv0 = 0x80000000U;\n"
        "\t\tconst unsigned int poll_max = 100000U;\n"
        "\t\tu32 extc;\n"
        "\t\tunsigned int i;\n\n"
        "\t\tfman_set_fpm_extc(port->fm, inv0);\n"
        "\t\tfor (i = 0; i < poll_max; i++) {\n"
        "\t\t\textc = fman_get_fpm_extc(port->fm);\n"
        "\t\t\tif (!(extc & inv0))\n"
        "\t\t\t\tbreak;\n"
        "\t\t\tudelay(1);\n"
        "\t\t}\n"
        "\t\tif (extc & inv0)\n"
        "\t\t\tdev_warn(port->dev,\n"
        f"\t\t\t\t \"{sync_label} timed out after %u polls (fmfp_extc=0x%08x)\\n\",\n"
        "\t\t\t\t poll_max, extc);\n"
        "\t\telse\n"
        "\t\t\tdev_info(port->dev,\n"
        f"\t\t\t\t \"{sync_label} cleared after %u poll(s)\\n\", i);\n"
    )
    new = (
        "\t\terr = fman_fpm_extc_sync(port->fm, &extc, &polls);\n"
        "\t\tif (err) {\n"
        "\t\t\tdev_warn(port->dev,\n"
        f"\t\t\t\t \"{sync_label} refused or failed (%d, polls=%u, fmfp_extc=0x%08x)\\n\",\n"
        "\t\t\t\t err, polls, extc);\n"
        "\t\t\tmutex_unlock(&fman->cc_port_lock);\n"
        "\t\t\treturn err;\n"
        "\t\t}\n"
        "\t\tdev_info(port->dev,\n"
        f"\t\t\t \"{sync_label} cleared after %u poll(s)\\n\", polls);\n"
    )
    body = replace_once(body, old, new, f"{name} INV0")
    return source[:first] + body + source[last:]


def main():
    try:
        header = FMAN_H.read_text()
        fman = FMAN_C.read_text()
        port = PORT_C.read_text()
        pcd = PCD_C.read_text()

        if MARKER in header:
            if (header.count(MARKER) != 1 or
                    fman.count("int fman_fpm_extc_sync(") != 1 or
                    fman.count("mutex_init(&fman->inv0_lock);") != 1 or
                    fman.count("bool fman_fpm_inv0_faulted(") != 1 or
                    header.count("bool fman_fpm_inv0_faulted(") != 1 or
                    pcd.count(PUBLISH_ANCHOR + PUBLISH_FENCE) != 1 or
                    port.count("fman_fpm_extc_sync(port->fm, &extc, &polls)") != 2 or
                    pcd.count("fman_fpm_extc_sync(") != 3 or
                    pcd.count("fe_extc SYNC writes disabled until INV0 ownership is proven") != 1):
                raise ValueError("partially applied INV0 serialization fix")
            print("### F-293: already applied")
            return 0

        if "F-292: serialize RX/OH CC port transitions" not in header:
            raise ValueError("F-292 transition lock is required")

        header = replace_once(
            header,
            "\t/* F-292: serialize RX/OH CC port transitions. */\n"
            "\tstruct mutex cc_port_lock;\n",
            "\t/* F-292: serialize RX/OH CC port transitions. */\n"
            "\tstruct mutex cc_port_lock;\n"
            f"\t/* {MARKER}; timeout requires cold-boot recovery. */\n"
            "\tstruct mutex inv0_lock;\n"
            "\tbool inv0_faulted;\n",
            "FMan INV0 state",
        )
        header = replace_once(
            header,
            "void fman_set_fpm_extc(struct fman *fman, u32 val);\n",
            "void fman_set_fpm_extc(struct fman *fman, u32 val);\n"
            "int fman_fpm_extc_sync(struct fman *fman, u32 *value,\n"
            "\t\t\t       unsigned int *polls);\n"
            "void fman_fpm_inv0_poison(struct fman *fman);\n"
            "bool fman_fpm_inv0_faulted(struct fman *fman);\n",
            "FMan INV0 API",
        )

        fman = replace_once(
            fman,
            "\tmutex_init(&fman->cc_port_lock);\n",
            "\tmutex_init(&fman->cc_port_lock);\n"
            "\tmutex_init(&fman->inv0_lock);\n",
            "INV0 mutex initialization",
        )
        anchor = "EXPORT_SYMBOL_GPL(fman_set_fpm_extc);\n"
        helper = (
            "\n"
            "/* Serialize the global INV0 request. A pending request has no "
            "provable\n"
            " * owner; never reassert it or claim its completion as this "
            "call's barrier.\n"
            " * Once ownership is uncertain, retain affected state until cold "
            "boot.\n"
            " */\n"
            "int fman_fpm_extc_sync(struct fman *fman, u32 *value,\n"
            "\t\t\t       unsigned int *polls)\n"
            "{\n"
            "\tconst u32 inv0 = 0x80000000U;\n"
            "\tconst unsigned int poll_max = 100000U;\n"
            "\tu32 extc;\n"
            "\tunsigned int i;\n"
            "\tint err = 0;\n"
            "\n"
            "\tif (!fman || !value || !polls)\n"
            "\t\treturn -EINVAL;\n"
            "\tmutex_lock(&fman->inv0_lock);\n"
            "\t*polls = 0;\n"
            "\textc = fman_get_fpm_extc(fman);\n"
            "\tif (READ_ONCE(fman->inv0_faulted)) {\n"
            "\t\terr = -ESHUTDOWN;\n"
            "\t\tgoto out;\n"
            "\t}\n"
            "\tif (extc & inv0) {\n"
            "\t\tWRITE_ONCE(fman->inv0_faulted, true);\n"
            "\t\terr = -EBUSY;\n"
            "\t\tgoto out;\n"
            "\t}\n"
            "\tfman_set_fpm_extc(fman, inv0);\n"
            "\tfor (i = 0; i < poll_max; i++) {\n"
            "\t\textc = fman_get_fpm_extc(fman);\n"
            "\t\tif (!(extc & inv0))\n"
            "\t\t\tbreak;\n"
            "\t\tudelay(1);\n"
            "\t}\n"
            "\t*polls = i == poll_max ? poll_max : i + 1;\n"
            "\tif (extc & inv0) {\n"
            "\t\tWRITE_ONCE(fman->inv0_faulted, true);\n"
            "\t\terr = -ETIMEDOUT;\n"
            "\t}\n"
            "out:\n"
            "\t*value = extc;\n"
            "\tmutex_unlock(&fman->inv0_lock);\n"
            "\treturn err;\n"
            "}\n"
            "EXPORT_SYMBOL_GPL(fman_fpm_extc_sync);\n"
            "\n"
            "void fman_fpm_inv0_poison(struct fman *fman)\n"
            "{\n"
            "\tif (fman)\n"
            "\t\tWRITE_ONCE(fman->inv0_faulted, true);\n"
            "}\n"
            "EXPORT_SYMBOL_GPL(fman_fpm_inv0_poison);\n"
            "\n"
            "bool fman_fpm_inv0_faulted(struct fman *fman)\n"
            "{\n"
            "\treturn !fman || READ_ONCE(fman->inv0_faulted);\n"
            "}\n"
            "EXPORT_SYMBOL_GPL(fman_fpm_inv0_faulted);\n"
        )
        fman = replace_once(fman, anchor, anchor + helper, "INV0 helper")

        port = update_port(port, "fman_port_set_cc_base",
                           "fman_port: FMFP_EXTC SYNC")
        port = update_port(port, "fman_port_set_oh_cc_base",
                           "fman_port: O/H FMFP_EXTC SYNC")

        old_sync = pcd[pcd.index("static int fman_pcd_ehash_sync("):
                       pcd.index("\nstatic int fman_pcd_ehash_recover(")]
        new_sync = (
            "static int fman_pcd_ehash_sync(struct fman_pcd_ehash_table *t, "
            "u32 *extc)\n"
            "{\n"
            "\tstruct fman *fman = t->pcd ? fman_pcd_get_fman(t->pcd) : NULL;\n"
            "\tunsigned int polls = 0;\n"
            "\tbool diag;\n"
            "\tu64 started_ns;\n"
            "\tint err;\n"
            "\n"
            "\t*extc = 0;\n"
            "\tif (!fman) {\n"
            "\t\tpr_warn_ratelimited(\"fman_pcd: ehash SYNC has no FMan owner; retaining state\\n\");\n"
            "\t\treturn -ENODEV;\n"
            "\t}\n"
            "\tdiag = READ_ONCE(ehash_sync_diag);\n"
            "\tstarted_ns = diag ? ktime_get_ns() : 0;\n"
            "\tdma_wmb();\n"
            "\terr = fman_fpm_extc_sync(fman, extc, &polls);\n"
            "\tif (diag)\n"
            "\t\tfman_pcd_ehash_sync_diag_record(true, started_ns, polls, *extc);\n"
            "\tif (err) {\n"
            "\t\tehash_sync_timeouts++;\n"
            "\t\tif (err == -ETIMEDOUT)\n"
            "\t\t\tehash_sync_wedged++;\n"
            "\t\treturn err;\n"
            "\t}\n"
            "\tif (READ_ONCE(ehash_sync_inject)) {\n"
            "\t\tWRITE_ONCE(ehash_sync_inject, ehash_sync_inject - 1);\n"
            "\t\tehash_sync_injected++;\n"
            "\t\tehash_sync_timeouts++;\n"
            "\t\tehash_sync_wedged++;\n"
            "\t\tfman_fpm_inv0_poison(fman);\n"
            "\t\tpr_warn(\"fman_pcd: F-282 injected ehash SYNC timeout; state retained\\n\");\n"
            "\t\treturn -ETIMEDOUT;\n"
            "\t}\n"
            "\treturn fman_pcd_hc_ehash_sync(t->pcd);\n"
            "}\n"
        )
        pcd = replace_once(pcd, old_sync, new_sync, "ehash SYNC")

        f177_new = (
            "\t\t\tu32 __f177_extc;\n"
            "\t\t\tunsigned int __f177_polls;\n"
            "\t\t\tint __f177_err;\n\n"
            "\t\t\t__f177_err = fman_fpm_extc_sync(__f177_fman,\n"
            "\t\t\t\t\t\t\t &__f177_extc,\n"
            "\t\t\t\t\t\t\t &__f177_polls);\n"
            "\t\t\tif (__f177_err) {\n"
            "\t\t\t\tehash_insert_sync_timeouts++;\n"
            "\t\t\t\tdev_warn(fman_get_dev(pcd->fman),\n"
            "\t\t\t\t\t \"fe_flow: F-177 FMFP_EXTC SYNC refused or failed (%d, polls=%u, fmfp_extc=0x%08x)\\n\",\n"
            "\t\t\t\t\t __f177_err, __f177_polls, __f177_extc);\n"
            "\t\t\t} else {\n"
            "\t\t\t\tdev_dbg(fman_get_dev(pcd->fman),\t/* F-265 */\n"
            "\t\t\t\t\t \"fe_flow: F-177 FMFP_EXTC SYNC cleared after %u poll(s)\\n\",\n"
            "\t\t\t\t\t __f177_polls);\n"
            "\t\t\t}"
        )
        starts = []
        cursor = 0
        while True:
            cursor = pcd.find("\t\t\tconst u32 __f177_inv0", cursor)
            if cursor < 0:
                break
            end = pcd.find("\t\t\t\t\t __f177_i);", cursor)
            if end < 0:
                raise ValueError("F-177 SYNC block is incomplete")
            starts.append((cursor, end + len("\t\t\t\t\t __f177_i);")))
            cursor = end + 1
        if len(starts) != 2:
            raise ValueError(f"F-177 blocks: found {len(starts)}, expected 2")
        for first, last in reversed(starts):
            pcd = pcd[:first] + f177_new + pcd[last:]

        pcd = disable_debugfs_sync(pcd)
        pcd = replace_once(pcd, PUBLISH_ANCHOR, PUBLISH_ANCHOR + PUBLISH_FENCE,
                           "ehash publication fence")

        FMAN_H.write_text(header)
        FMAN_C.write_text(fman)
        PORT_C.write_text(port)
        PCD_C.write_text(pcd)
    except (OSError, ValueError, IndexError) as exc:
        print(f"### F-293: FATAL: {exc}", file=sys.stderr)
        return 1

    print("### F-293: serialized INV0; pending or timed-out state is fenced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
