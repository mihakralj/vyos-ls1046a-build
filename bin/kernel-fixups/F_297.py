#!/usr/bin/env python3
"""Propagate remote-CPU dispatch errors from QMan CGR updates."""

from pathlib import Path
import sys


PATH = Path("drivers/soc/fsl/qbman/qman.c")
MARKER = "F-297: preserve callback result"
OLD = """\
int qman_update_cgr_safe(struct qman_cgr *cgr, struct qm_mcc_initcgr *opts)
{
\tstruct update_cgr_params params = {
\t\t.cgr = cgr,
\t\t.opts = opts,
\t};

\tpreempt_disable();
\tif (qman_cgr_cpus[cgr->cgrid] != smp_processor_id())
\t\tsmp_call_function_single(qman_cgr_cpus[cgr->cgrid],
\t\t\t\t\t qman_update_cgr_smp_call, &params,
\t\t\t\t\t true);
\telse
\t\tparams.ret = qman_update_cgr(cgr, opts);
\tpreempt_enable();
\treturn params.ret;
}
"""
NEW = """\
int qman_update_cgr_safe(struct qman_cgr *cgr, struct qm_mcc_initcgr *opts)
{
\tstruct update_cgr_params params = {
\t\t.cgr = cgr,
\t\t.opts = opts,
\t};
\tint ret;

\tpreempt_disable();
\tif (qman_cgr_cpus[cgr->cgrid] != smp_processor_id()) {
\t\tret = smp_call_function_single(qman_cgr_cpus[cgr->cgrid],
\t\t\t\t\t       qman_update_cgr_smp_call, &params,
\t\t\t\t\t       true);
\t\t/* F-297: preserve callback result unless dispatch itself failed. */
\t\tif (ret)
\t\t\tparams.ret = ret;
\t} else {
\t\tparams.ret = qman_update_cgr(cgr, opts);
\t}
\tpreempt_enable();
\treturn params.ret;
}
"""
NEW_WITH_PORTAL_CPU = NEW.replace("qman_cgr_cpus[cgr->cgrid]", "cgr->portal_cpu")


def main():
    try:
        source = PATH.read_text()
        if (source.count(NEW) + source.count(NEW_WITH_PORTAL_CPU) == 1 and
                source.count(MARKER) == 1):
            print("### F-297: already applied")
            return 0
        if source.count(OLD) != 1 or MARKER in source:
            raise ValueError("ambiguous or changed CGR update function")
        PATH.write_text(source.replace(OLD, NEW, 1))
    except (OSError, ValueError) as error:
        print(f"### F-297: FATAL: {error}", file=sys.stderr)
        return 1
    print("### F-297: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
