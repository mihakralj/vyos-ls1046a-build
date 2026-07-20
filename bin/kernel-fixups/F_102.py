"""F_102: Add NULL guard for fq in __poll_portal_fast SDQCR path.

The ZC datapath can produce DQRR entries whose context_b doesn't map
to a valid qman_fq (tag_to_fq returns NULL). Without this guard, the
code dereferences fq->cb.dqrr and crashes. Skip the entry if fq is NULL.

Temporary — remove once the ZC RX path properly initializes all FQ
context_b values.
"""

import os

changes = 0
QMAN = "drivers/soc/fsl/qbman/qman.c"

if os.path.exists(QMAN):
    with open(QMAN) as f:
        s = f.read()

    # Add NULL guard after tag_to_fq in the SDQCR path
    old = ('\t\t\t/* SDQCR: context_b points to the FQ */\n'
           '\t\t\tfq = tag_to_fq(be32_to_cpu(dq->context_b));\n'
           '\t\t\t/* Now let the callback do its stuff */\n'
           '\t\t\tres = fq->cb.dqrr(p, fq, dq, sched_napi);')
    new = ('\t\t\t/* SDQCR: context_b points to the FQ */\n'
           '\t\t\tfq = tag_to_fq(be32_to_cpu(dq->context_b));\n'
           '\t\t\t/* F_102: ZC datapath can produce entries with invalid context_b */\n'
           '\t\t\tif (unlikely(!fq)) {\n'
           '\t\t\t\tpr_err_once("qman: NULL fq from context_b=0x%x, skipping DQRR entry\\n",\n'
           '\t\t\t\t\t   be32_to_cpu(dq->context_b));\n'
           '\t\t\t\tqm_dqrr_cdc_consume_1ptr(&p->p, dq, 0);\n'
           '\t\t\t\tqm_dqrr_next(&p->p);\n'
           '\t\t\t\tcontinue;\n'
           '\t\t\t}\n'
           '\t\t\t/* Now let the callback do its stuff */\n'
           '\t\t\tres = fq->cb.dqrr(p, fq, dq, sched_napi);')

    if old in s:
        s = s.replace(old, new, 1)
        changes += 1
        print("### F_102: NULL fq guard added in __poll_portal_fast SDQCR path")
    else:
        print("### F_102: WARNING anchor not found in qman.c")

    with open(QMAN, 'w') as f:
        f.write(s)

if changes:
    print("### F_102: %d change(s) applied" % changes)
else:
    print("### F_102: WARNING no changes applied")
