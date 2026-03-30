#!/usr/bin/env python3
"""Patch image_installer.py to prefer mmcblk (eMMC) as default disk."""
import sys

p = 'src/op_mode/image_installer.py'
t = open(p).read()
if 'for _d in disks_available' in t:
    print('mmcblk default: already applied'); sys.exit(0)
old = 'default_disk: str = list(disks_available)'
if old not in t:
    print('ERROR: mmcblk default patch: expected context not found'); sys.exit(1)
ins = old + '\n    for _d in disks_available:\n        if "mmcblk" in _d:\n            default_disk=_d\n            break'
open(p, 'w').write(t.replace(old, ins, 1))
print('Applied mmcblk default fallback to image_installer.py')