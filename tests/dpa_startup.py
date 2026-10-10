"""Nothing outside the kernel may change the hardware underneath a running classifier."""

import shlex

from ask_orch.uart import Console


async def test_no_fman_userspace_interface(splat_window):
    # cdx builds the classifier in the kernel. The SDK's /dev/fm*, /dev/fm*-pcd
    # and /dev/fm*-port-* character devices, and every ioctl behind them, are
    # gone: no FMan character device major is registered and no node exists.
    # Nor is USDPAA built, whose /dev/fsl-usdpaa* hand a process raw QMan/BMan
    # portals and DMA memory: nothing in ASK is a USDPAA application.
    script = """
import glob, re
nodes = glob.glob('/dev/fm[0-9]*') + glob.glob('/dev/fsl-usdpaa*')
assert not nodes, nodes
majors = [line for line in open('/proc/devices') if re.fullmatch(r'\\s*\\d+ fm\\d+\\n', line)]
assert not majors, majors
misc = [line for line in open('/proc/misc') if 'usdpaa' in line]
assert not misc, misc
print('no FMan userspace interface')
"""
    with Console.target() as con:
        con.login("root", None)
        result = con.run("python3 -c " + shlex.quote(script), timeout=15)
        assert result.rc == 0, result.stdout


async def test_dpa_exclusive_control_fd(splat_window):
    script = """
import errno, os
fd = os.open('/dev/cdx_ctrl', os.O_RDWR)
try:
    for _ in range(16):
        try:
            second = os.open('/dev/cdx_ctrl', os.O_RDWR)
        except OSError as error:
            assert error.errno == errno.EBUSY, error
        else:
            os.close(second)
            raise AssertionError('second opener admitted to the control device')
finally:
    os.close(fd)
os.close(os.open('/dev/cdx_ctrl', os.O_RDWR))
"""
    with Console.target() as con:
        con.login("root", None)
        result = con.run("python3 -c " + shlex.quote(script), timeout=15)
        assert result.rc == 0, result.stdout
