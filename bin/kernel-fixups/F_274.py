"""F-274 (WGFP gate 3, 2026-11-04): fman_port_disable() reports a drain timeout.

The Linux fman_port_disable() polls the QMI/BMI busy bits, logs at dev_dbg and
returns 0 on timeout, so no caller can tell a quiesced port from one that
still has a task in flight. The NXP SDK routine returns -EBUSY there. Return
-EBUSY and warn (ratelimited).

Caller audit (2026-11-04): dpaa_stop (continues on every port, ndo_stop
result is ignored), the dpaa_eth mac_start_failed unwind (ignores), F-211
fe_arm quiesce (casts to void: the port is already forced down), af_xdp_pool
attach (skips the RX BPID reprogram and warns -- safer), af_xdp_pool detach
(continues, bumps xsk_pool_detach_timeout), 0163 port recover (aborts before
zeroing MURAM -- the intended contract). Success still means only "BMI/QMI
busy bits cleared", not that DDR ehash walkers have drained.

Idempotent; exits non-zero unless the anchor matches exactly once.
"""

import sys

MARK = "F-274"
PATH = "drivers/net/ethernet/freescale/fman/fman_port.c"

OLD = (
    '\tif (failure)\n'
    '\t\tdev_dbg(port->dev, "%s: FMan Port[%d]: BMI or QMI is Busy. Port forced down\\n",\n'
    '\t\t\t__func__,  port->port_id);\n'
    '\n'
    '\treturn 0;\n'
    '}\n'
    'EXPORT_SYMBOL(fman_port_disable);\n')
NEW = (
    '\t/* F-274: report the drain timeout (SDK returns -EBUSY); the port is\n'
    '\t * forced down but a task may still be in flight. */\n'
    '\tif (failure) {\n'
    '\t\tdev_warn_ratelimited(port->dev, "%s: FMan Port[%d]: BMI or QMI is Busy. Port forced down\\n",\n'
    '\t\t\t\t     __func__,  port->port_id);\n'
    '\t\treturn -EBUSY;\n'
    '\t}\n'
    '\n'
    '\treturn 0;\n'
    '}\n'
    'EXPORT_SYMBOL(fman_port_disable);\n')

try:
    with open(PATH) as f:
        s = f.read()
except FileNotFoundError:
    print(f"### F-274: FATAL: {PATH} not found")
    sys.exit(1)

if MARK in s:
    print("### F-274: already applied")
    sys.exit(0)

n = s.count(OLD)
if n != 1:
    print(f"### F-274: FATAL: anchor in {PATH} found {n} times (expected 1)")
    sys.exit(1)

with open(PATH, "w") as f:
    f.write(s.replace(OLD, NEW))
print("### F-274: fman_port_disable returns -EBUSY on drain timeout")
