#!/usr/bin/env python3
"""Read a DPAA interface's BMan hardware free-buffer count."""

import argparse
import mmap
import os
from pathlib import Path
import struct
import sys

SYS_CLASS_NET = Path("/sys/class/net")
DEVMEM = "/dev/mem"
BMAN_BASE = 0x01890000
POOL_CONTENT = 0x600
POOL_COUNT = 64


def read_pool(interface):
    if not interface or interface in {".", ".."} or Path(interface).name != interface:
        raise ValueError(f"invalid interface name: {interface!r}")
    bpids = (SYS_CLASS_NET / interface / "bpids").read_text().split()
    if len(bpids) != 1:
        raise ValueError(f"{interface}: expected one BPID, got {bpids!r}")
    bpid = int(bpids[0], 10)
    if not 0 <= bpid < POOL_COUNT:
        raise ValueError(f"{interface}: invalid BPID {bpid}")

    fd = os.open(DEVMEM, os.O_RDONLY | os.O_SYNC)
    try:
        with mmap.mmap(fd, 0x1000, flags=mmap.MAP_SHARED,
                       prot=mmap.PROT_READ, offset=BMAN_BASE) as regs:
            # Use an aligned 32-bit MMIO load rather than copying register bytes.
            with memoryview(regs).cast("I") as words:
                raw = words[POOL_CONTENT // 4 + bpid]
            count = struct.unpack(">I", struct.pack("=I", raw))[0]
    finally:
        os.close(fd)
    return bpid, count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("interface")
    args = parser.parse_args()
    try:
        bpid, count = read_pool(args.interface)
    except (OSError, ValueError) as err:
        print(f"bman-pool-count: {err}", file=sys.stderr)
        return 1
    print(f"{bpid}:{count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
