#!/usr/bin/env python3
"""Correct CAAM QI queue-depth decoding and failed-enqueue DMA ownership."""

from pathlib import Path
import sys


PATH = Path("drivers/crypto/caam/qi.c")
OLD_ENQUEUE = """\
\tdev_err(qidev, "qman_enqueue failed: %d\\n", ret);

\treturn ret;
"""
NEW_ENQUEUE = """\
\tdev_err(qidev, "qman_enqueue failed: %d\\n", ret);
\tdma_unmap_single(qidev, addr, sizeof(req->fd_sgt), DMA_BIDIRECTIONAL);

\treturn ret;
"""
OLD_DEPTH = "\t\tif (!qm_mcr_np_get(&np, frm_cnt))\n"
NEW_DEPTH = "\t\tif (!(be32_to_cpu(np.frm_cnt) & qm_mcr_frm_cnt_mask))\n"
REPLACEMENTS = ((OLD_ENQUEUE, NEW_ENQUEUE), (OLD_DEPTH, NEW_DEPTH))


def main():
    try:
        source = PATH.read_text()
        if all(source.count(old) == 0 and source.count(new) == 1
               for old, new in REPLACEMENTS):
            print("### F-301: already applied")
            return 0
        if any(source.count(old) != 1 or source.count(new) != 0
               for old, new in REPLACEMENTS):
            raise ValueError("ambiguous or partially applied CAAM QI source")
        for old, new in REPLACEMENTS:
            source = source.replace(old, new, 1)
        PATH.write_text(source)
    except (OSError, ValueError) as error:
        print(f"### F-301: FATAL: {error}", file=sys.stderr)
        return 1
    print("### F-301: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
