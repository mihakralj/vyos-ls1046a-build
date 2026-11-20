#!/usr/bin/env python3
"""Retire the IC probe's unowned TX buffer pointer and unsafe reader."""

import hashlib
from pathlib import Path
import re
import sys

ETH = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.c")
PCD = Path("drivers/net/ethernet/freescale/fman/fman_pcd.c")
CAPTURE = (
    "\t/* F-069a v9: capture frame-data vaddr (no bp var found) */\n"
    "\tfman_pcd_ic_vaddr = vaddr;\n\n"
)
GUARD = "\tif (unlikely(fd->bpid != FSL_DPAA_BPID_INV)) {\n"
# Exact preimage of the 0169 ic_probe_show; drift must be reviewed.
OLD_SHOW_SHA256 = "265e0dab5c2c74e1445410862614f21ef82fd25e12a9e1bfbc478d89103cc0b8"
NEW_SHOW = (
    "static int fman_pcd_ic_probe_show(struct seq_file *s, void *unused)\n"
    "{\n"
    '\tseq_puts(s, "disabled: TX buffer lifetime is not tracked\\n");\n'
    "\treturn 0;\n"
    "}\n"
)


def function(text, signature):
    matches = list(re.finditer(signature, text, re.MULTILINE))
    if len(matches) != 1:
        raise ValueError(f"expected one function, found {len(matches)}")
    start = matches[0].start()
    end = text.find("\n}\n", matches[0].end())
    if end < 0:
        raise ValueError("function end not found")
    return start, end + 3, text[start:end + 3]


def main():
    try:
        eth, pcd = ETH.read_text(), PCD.read_text()
        start, end, cleanup = function(
            eth, r"^(?:static )?struct sk_buff \*dpaa_cleanup_tx_fd\([^;]*?\n\{\n",
        )
        pstart, pend, show = function(
            pcd, r"^static int fman_pcd_ic_probe_show\([^;]*?\n\{\n",
        )
        if cleanup.count(GUARD) != 1 or "F-227" not in cleanup:
            raise ValueError("F-227 must run before F-317")
        if show == NEW_SHOW:
            if (eth.count("fman_pcd_ic_vaddr") != 1 or
                    pcd.count("fman_pcd_ic_vaddr") != 2 or
                    "fman_pcd_ic_vaddr" in cleanup):
                raise ValueError("unexpected IC pointer reference after retirement")
            print("### F-317: already applied")
            return 0
        if (cleanup.count(CAPTURE) != 1 or cleanup.count("fman_pcd_ic_vaddr") != 1
                or eth.count("fman_pcd_ic_vaddr") != 2
                or pcd.count("fman_pcd_ic_vaddr") != 3
                or cleanup.index(GUARD) > cleanup.index(CAPTURE)):
            raise ValueError("unexpected TX capture or additional IC pointer writer")
        if hashlib.sha256(show.encode()).hexdigest() != OLD_SHOW_SHA256:
            raise ValueError("unexpected ic_probe_show preimage")
        cleanup = cleanup.replace(CAPTURE, "", 1)
        eth = eth[:start] + cleanup + eth[end:]
        pcd = pcd[:pstart] + NEW_SHOW + pcd[pend:]
        ETH.write_text(eth)
        PCD.write_text(pcd)
    except (OSError, ValueError) as error:
        print(f"### F-317: FATAL: {error}", file=sys.stderr)
        return 1
    print("### F-317: retired TX pointer capture and disabled unsafe ic_probe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
