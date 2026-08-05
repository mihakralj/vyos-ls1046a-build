#!/usr/bin/env python3
"""Dump live FMan MURAM contents via mmap() on /dev/mem.

Same technique as bin/ask-pcd-regdump.py: STRICT_DEVMEM is disabled on
both the dpaa1 and nxp-sdk kernel flavors (confirmed 2026-08-01 on `.106`
and `.185` via live /proc/config.gz), but a plain read()/`dd` against
/dev/mem still fails with EFAULT against MURAM's physical range because
read() on /dev/mem requires pfn_valid() (normal System RAM only). MURAM
is memory-mapped I/O/SRAM, reachable only via mmap() on /dev/mem — a
different code path that both boards allow.

The CDX_CTRL_DPA_GET_MURAM_DATA ioctl on `/dev/cdx_ctrl` was tried first
as an alternative on the nxp-sdk/.106 stack; it is present and correctly
wired in the nxp-sdk branch source (cdx_dev.c) but is NOT compiled into
the currently-deployed `.106` kernel module (confirmed via a kernel-log
"unsupported ioctl cmd" message with a hand-verified-correct command
number) — a real drift between this repo's CI config and what's flashed.
This mmap-based tool is the portable fallback and works on both boards.

Usage: sudo python3 bin/muram-mmap-dump.py [base_hex] [size_hex] > dump.bin

Default base/size are for LS1046A (confirmed via `sudo cat /proc/iomem |
grep fman-muram`): 0x01a00000, 0x60000 (393216 bytes).
"""
import mmap
import os
import sys

MURAM_PHYS_BASE = 0x01a00000
MURAM_SIZE = 0x60000  # 393216 bytes, from /proc/iomem


def dump(base=MURAM_PHYS_BASE, size=MURAM_SIZE):
    fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
    try:
        m = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ, offset=base)
        try:
            return m.read(size)
        finally:
            m.close()
    finally:
        os.close(fd)


if __name__ == "__main__":
    base = int(sys.argv[1], 0) if len(sys.argv) > 1 else MURAM_PHYS_BASE
    size = int(sys.argv[2], 0) if len(sys.argv) > 2 else MURAM_SIZE
    data = dump(base, size)
    sys.stderr.write(f"got {len(data)} bytes from phys 0x{base:x}\n")
    sys.stdout.buffer.write(data)
