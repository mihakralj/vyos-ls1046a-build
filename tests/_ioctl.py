"""Linux ioctl command-number helpers (mirror of asm-generic/ioctl.h).

Every `_IOR`/`_IOW`/`_IOWR` macro in the kernel packs (dir, type, nr,
size) into a u32 using these shifts. Reproducing in Python so tests can
craft the same 32-bit value the cdx driver expects.
"""

from __future__ import annotations

_IOC_NRBITS    = 8
_IOC_TYPEBITS  = 8
_IOC_SIZEBITS  = 14
_IOC_DIRBITS   = 2

_IOC_NRSHIFT   = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT   + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT  = _IOC_SIZESHIFT + _IOC_SIZEBITS

_IOC_NONE  = 0
_IOC_WRITE = 1
_IOC_READ  = 2


def _IOC(direction: int, type_: int, nr: int, size: int) -> int:
    return (
        (direction << _IOC_DIRSHIFT)
        | (type_    << _IOC_TYPESHIFT)
        | (nr       << _IOC_NRSHIFT)
        | (size     << _IOC_SIZESHIFT)
    )


def _IO (t, nr):          return _IOC(_IOC_NONE,              t, nr, 0)
def _IOR(t, nr, size):    return _IOC(_IOC_READ,              t, nr, size)
def _IOW(t, nr, size):    return _IOC(_IOC_WRITE,             t, nr, size)
def _IOWR(t, nr, size):   return _IOC(_IOC_READ | _IOC_WRITE, t, nr, size)


# --- cdx-specific: magic + known command numbers -------------------

CDX_IOC_MAGIC = 0xBE

# Retired classifier commands, retained for ABI snapshots and rejection tests.
# The old cdx_ctrl_set_dpa_params layout after IP-reassembly removal was:
#   void *fman_info          8 B
#   uint32_t num_fmans       4 B + 4 B tail padding to 8-B alignment
# = 16 B on 64-bit arm64.
SIZEOF_CDX_CTRL_SET_DPA_PARAMS = 16

CDX_CTRL_DPA_SET_PARAMS = _IOWR(CDX_IOC_MAGIC, 1, SIZEOF_CDX_CTRL_SET_DPA_PARAMS)
CDX_CTRL_DPA_INIT_CHECK = _IO(CDX_IOC_MAGIC, 5)

# Number 3 was CDX_CTRL_DPA_CONNADD pre-C9b. Post-removal the dispatcher
# table has no entry for it — any invocation must return -ENOTTY.
CDX_CTRL_DPA_CONNADD_LEGACY = _IOWR(CDX_IOC_MAGIC, 3, 0)

# Debug-only ioctl (cdx/cdx_ioctl.h, gated on DPAA_DEBUG_ENABLE). On
# production builds this nr has no dispatcher entry, so any invocation
# must return -ENOTTY. struct muram_data layout: void *buff (8 B) +
# uint32_t size (4 B + 4 B tail pad) = 16 B on arm64.
SIZEOF_CDX_CTRL_GET_MURAM_DATA = 16
CDX_CTRL_DPA_GET_MURAM_DATA = _IOWR(CDX_IOC_MAGIC, 4, SIZEOF_CDX_CTRL_GET_MURAM_DATA)

# An arbitrary never-used nr so we can probe the "unknown ioctl" path
# without accidentally matching something we don't remember.
CDX_CTRL_UNKNOWN_NR = _IOWR(CDX_IOC_MAGIC, 99, 0)
