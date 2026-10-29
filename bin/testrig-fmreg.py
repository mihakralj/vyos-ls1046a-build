#!/usr/bin/env python3
"""FMan CCSR register reader for bin/testrig-compare.sh (FMREG=bin/testrig-fmreg.py).

Run on the DUT with `sudo python3 fmreg.py r <offset>`; offsets are relative to the FMan base 0x1A00000
(eth3 mEMAC RDRP 0xf0158, eth4 0xf2158). Prints `<offset> = <value>` so field 3 is the 32-bit value.
"""
import mmap
import os
import struct
import sys

FMAN_BASE = 0x1A00000

if len(sys.argv) != 3 or sys.argv[1] != "r":
    sys.exit("usage: fmreg.py r <offset>")
off = int(sys.argv[2], 0)
page = (FMAN_BASE + off) & ~0xFFF
fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ, offset=page)
val = struct.unpack(">I", m[(FMAN_BASE + off) & 0xFFF:][:4])[0]
print(f"{off:#x} = {val}")
