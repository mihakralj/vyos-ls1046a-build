"""F-166: try the vendor's Internal Context Parameters (FMBM_RICP) layout
when AC_CC/FE_ENTER is armed (Task #26 follow-up experiment).

CONTEXT (2026-08-05, three cold-boot cycles on .185): arming AC_CC/
FE_ENTER dispatch via fe_arm engage wedges port 0x11 immediately and
100% reproducibly (3/3), before any test traffic is even sent. Zero
fault signature anywhere (no STL bit, all DCSR fault registers clean) --
matches this project's documented "silent WAIT, no fault latched"
corruption class.

Comparing live registers against the genuine vendor cdx.ko stack on
.106 (same physical port, hwport 0x11) found FMBM_RICP (Rx Internal
Context Parameters -- controls where/how much of the frame's Internal
Context, i.e. Parser Result + KeyGen hash + extracted key, gets copied)
configured completely differently:

    vendor (.106, source-confirmed hardcoded in fm_port.c,
            kernel/flavors/ask/sdk-sources/.../fman_port.c:104,
            "tmp = 0x00000007;" -- overrides the computed value
            unconditionally, no comment explaining why):
        0x00000007 -> ic_ext_offset=0B  ic_int_offset=0B  ic_size=112B

    ours (.185, mainline dpaa_eth's own fman_port_cfg_buf_prefix_content()
          mechanism, tuned for RSS-hash-in-skb / checksum offload --
          set once at port init, identical whether AC_CC is armed or not):
        0x000e0203 -> ic_ext_offset=224B ic_int_offset=32B ic_size=48B

These are not close -- completely different IC copy windows. Untested
hypothesis: the AC_CC/FE-VM engine's own internal IC access (separate
from what FMBM_RICP copies into the kernel-visible buffer) may depend
on this register being in the vendor's format; a mismatch could plausibly
put the classification microcode into an unbounded/invalid read that
silently WAITs rather than faulting, matching the observed wedge
signature exactly.

THE EXPERIMENT: override FMBM_RICP to the vendor's exact value
(0x00000007) when a real CC base is set (AC_CC being armed), and
restore mainline's own value (0x000e0203, confirmed via direct register
read on this exact board/build) on disengage -- so normal kernel RX
(RSS-hash-in-skb, checksum offload) is unaffected once disengaged.

CAVEAT: this is a diagnostic probe, not a proven fix. If the wedge
persists with this change, RICP is not the (sole) cause and this
should be reverted -- do not leave it in place "just in case" if it
doesn't change the outcome. If the wedge clears, that's strong evidence
FMBM_RICP is load-bearing for AC_CC dispatch and needs a real fix
(likely deriving it from the actual CC/ehash scheme's resource needs,
not a hardcoded constant -- vendor's own int_buf_start_margin/FMBM_RIM
is derived from getCcParams(), not fixed either, so a hardcoded RICP
override is very likely not the final correct form).

Disposition: experimental, debugfs-arm-path only (fman_port_set_cc_base()
is only called from the FE-VM arm/disarm path and cc_test's install/
detach, both explicitly test-only call sites -- never from mainline
port bring-up).
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
port_c = os.path.join(kroot, "fman_port.c")

if not os.path.exists(port_c):
    print("### F-166: fman_port.c not found")
    sys.exit(0)

with open(port_c) as f:
    src = f.read()

changes = 0

old = "\tiowrite32be(cc_muram_off, &port->bmi_regs->rx.fmbm_rccb);"
new = (
    "\tiowrite32be(cc_muram_off, &port->bmi_regs->rx.fmbm_rccb);\n"
    "\n"
    "\t/*\n"
    "\t * F-166 (2026-08-05, Task #26 experiment): try the vendor's\n"
    "\t * FMBM_RICP (Internal Context Parameters) layout while AC_CC is\n"
    "\t * armed. Vendor cdx.ko hardcodes 0x00000007 (ic_ext_offset=0,\n"
    "\t * ic_int_offset=0, ic_size=112B); mainline dpaa_eth's own\n"
    "\t * fman_port_cfg_buf_prefix_content() config (0x000e0203,\n"
    "\t * ic_ext_offset=224B, ic_int_offset=32B, ic_size=48B) is tuned\n"
    "\t * for RSS-hash-in-skb, not AC_CC/FE-VM dispatch. Restore\n"
    "\t * mainline's value on disengage so normal kernel RX (RSS hash,\n"
    "\t * checksum offload) is unaffected once AC_CC is torn down.\n"
    "\t * DIAGNOSTIC ONLY -- revert if this doesn't change the wedge.\n"
    "\t */\n"
    "\tiowrite32be(cc_muram_off ? 0x00000007 : 0x000e0203,\n"
    "\t\t    &port->bmi_regs->rx.fmbm_ricp);"
)

if new in src:
    print("### F-166: already applied")
elif old in src:
    src = src.replace(old, new, 1)
    changes += 1
    print("### F-166: FMBM_RICP override wired into fman_port_set_cc_base()")
else:
    print(
        "### F-166: FATAL: expected 'iowrite32be(cc_muram_off, "
        "&port->bmi_regs->rx.fmbm_rccb);' line not found verbatim in "
        "fman_port_set_cc_base() -- source has likely drifted. Refusing "
        "to guess; fix the anchor text in F_166.py against the current "
        "fman_port.c before retrying."
    )
    sys.exit(1)

if changes:
    with open(port_c, "w") as f:
        f.write(src)
    print(f"### F-166: {changes} change(s) applied")
else:
    print("### F-166: no changes applied")
    sys.exit(1)
