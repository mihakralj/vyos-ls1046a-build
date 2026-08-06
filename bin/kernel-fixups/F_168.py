"""F-168: assert FMFP_EXTC SYNC after FMBM_RCCB, before AC_CC dispatch is
enabled (Task #26 follow-up to F-167).

CONTEXT (2026-08-06): F-167 added a standalone, inert-by-default debugfs
probe (`fe_extc`) for FMFP_EXTC (FPM External Requests Control, CCSR
offset 0x074) so the register could be tested without going anywhere
near the port-wedge. Board-tested on `.185`: the register is safe and
responsive in isolation -- `echo sync > fe_extc` cleared INV0 immediately
(0 polls) with zero adverse effect, ports/fault-registers unchanged, run
twice for repeatability. See arch/fman-microcode-210-programming-
reference.md §3 area and the qdrant entry for this board session.

That test proved the register itself is inert to touch standalone. It did
NOT test the actual hypothesis, because no port was armed. This fixup
does: it wires a real SYNC assertion into the one operation that has
reproducibly wedged the port on every attempt this session -- arming
AC_CC dispatch.

fman_port_set_cc_base() (fman_port.c) is where `fmbm_rccb` (the AC_CC
dispatch target BMI register) gets written, immediately followed by an
RMW of `fmbm_rfpne` that -- when the parser NIA already points at
KeyGen -- sets NIA_KG_CC_EN, i.e. the write that actually turns frame
dispatch into the CC tree on. The wedge is reproducible immediately on
arm, before any traffic and before any ehash flow is inserted -- so the
most direct test of "does SYNC prevent the wedge" is to assert it
between these two writes: after fmbm_rccb changes what a live-walked
structure points at, before fmbm_rfpne tells the parser it's safe to
dispatch into it. This is exactly the ordering RM §5.12.14.1 describes
for updating a live FMan-controller-walked structure.

SCOPE, deliberately narrow: only the arm path (`cc_muram_off != 0`) gets
the SYNC assertion. Teardown (`cc_muram_off == 0`) is untouched -- it has
never shown the wedge symptom this session, so there is no hypothesis to
test there, and touching a path that already works for no tested benefit
is exactly the kind of unforced risk this project's fixup history (see
F-164's incident writeup) has learned to avoid.

WHAT SUCCESS/FAILURE LOOKS LIKE ON BOARD: after this fixup, `fe_arm
engage <port> <off> ...` will print an extra dev_info/dev_warn line
("FMFP_EXTC SYNC cleared after N poll(s)" or "... timed out ...") before
the usual "RX coarse-classification base set" line. If the port still
wedges immediately, this hypothesis is refuted for the wedge (though it
may still matter for the ehash MISS specifically, once the wedge is
otherwise resolved -- these are two separable questions, do not conflate
a negative result here with FMFP_EXTC being irrelevant to ehash inserts).
If the port does NOT wedge, this is a major result and should be
board-confirmed carefully (repeat cold-boot cycles, check fault
registers, THEN proceed to test whether traffic actually produces a HIT)
before being treated as fixed.

Depends on F-167 (fman_get_fpm_extc()/fman_set_fpm_extc() in fman.c/
fman.h) already being applied -- ci-setup-kernel.sh runs F-167 before
F-168.
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
port_c = os.path.join(kroot, "fman_port.c")

if not os.path.exists(port_c):
    print("### F-168: fman_port.c not found")
    sys.exit(0)

with open(port_c) as f:
    src = f.read()

changes = 0

anchor = "\tiowrite32be(cc_muram_off, &port->bmi_regs->rx.fmbm_rccb);\n"

new = anchor + '''
\t/*
\t * F-168 (Task #26, 2026-08-06): RM §5.12.14.1 documents FMFP_EXTC[INV0]
\t * SYNC as required after changing a live FMan-controller-walked
\t * structure, before dispatch into it is safe. fmbm_rccb just repointed
\t * the AC_CC dispatch target; assert SYNC here, before fmbm_rfpne below
\t * enables dispatch into it -- testing whether this prevents the
\t * port-wedge that has reproducibly occurred on every prior arm attempt
\t * this session. Scoped to the arm path only (cc_muram_off != 0);
\t * teardown (cc_muram_off == 0) is untouched -- it has never shown the
\t * wedge symptom, so there is no hypothesis to test there. F-167
\t * board-confirmed this register is safe/responsive to touch standalone;
\t * this is the first test with an actual port arm in the picture.
\t */
\tif (cc_muram_off) {
\t\tconst u32 inv0 = 0x80000000U;
\t\tconst unsigned int poll_max = 100000U;
\t\tu32 extc;
\t\tunsigned int i;

\t\tfman_set_fpm_extc(port->fm, inv0);
\t\tfor (i = 0; i < poll_max; i++) {
\t\t\textc = fman_get_fpm_extc(port->fm);
\t\t\tif (!(extc & inv0))
\t\t\t\tbreak;
\t\t\tudelay(1);
\t\t}
\t\tif (extc & inv0)
\t\t\tdev_warn(port->dev,
\t\t\t\t \"fman_port: FMFP_EXTC SYNC timed out after %u polls (fmfp_extc=0x%08x)\\n\",
\t\t\t\t poll_max, extc);
\t\telse
\t\t\tdev_info(port->dev,
\t\t\t\t \"fman_port: FMFP_EXTC SYNC cleared after %u poll(s)\\n\", i);
\t}
'''

if new in src:
    print("### F-168: already applied")
elif anchor in src:
    src = src.replace(anchor, new, 1)
    changes += 1
    print("### F-168: FMFP_EXTC SYNC assertion inserted into fman_port_set_cc_base()")
else:
    print(
        "### F-168: FATAL: expected 'iowrite32be(cc_muram_off, "
        "&port->bmi_regs->rx.fmbm_rccb);' anchor not found verbatim in "
        "fman_port.c -- source has likely drifted since this fixup was "
        "written. Refusing to guess; fix the anchor text in F_168.py "
        "against the current fman_port.c before retrying."
    )
    sys.exit(1)

if changes:
    with open(port_c, "w") as f:
        f.write(src)
    print(f"### F-168: {changes} change(s) applied")
else:
    print("### F-168: no changes applied")
    sys.exit(1)
