"""F_102 v2: Add NULL guards for ALL tag_to_fq calls in __poll_portal_fast.

The ZC datapath (BPID reprogram) can produce QMan portal entries (MR, ERN,
DQRR) whose context_b doesn't map to a valid qman_fq. tag_to_fq() returns
NULL, and the code dereferences fq->cb.* without checking.

v1 only guarded the SDQCR path. v2 adds guards for the MR and ERN paths too,
which crash at __poll_portal_fast+0x40 (MR path, the first tag_to_fq call).

Temporary — remove once the ZC RX path properly initializes all FQ
context_b values.
"""

import os

changes = 0
QMAN = "drivers/soc/fsl/qbman/qman.c"

if os.path.exists(QMAN):
    with open(QMAN) as f:
        s = f.read()

    # Guard 1: MR path (line ~1521) — fq_state_change dereferences fq
    old1 = ('\t\t\tfq = tag_to_fq(be32_to_cpu(msg->fq.context_b));\n'
            '\t\t\tfq_state_change(p, fq, msg, verb);\n'
            '\t\t\tif (fq->cb.fqs)')
    new1 = ('\t\t\tfq = tag_to_fq(be32_to_cpu(msg->fq.context_b));\n'
            '\t\t\t/* F_102 v2: ZC datapath can produce entries with invalid context_b */\n'
            '\t\t\tif (unlikely(!fq)) {\n'
            '\t\t\t\tpr_err_once("qman: NULL fq from MR context_b=0x%x, skipping\\n",\n'
            '\t\t\t\t\t   be32_to_cpu(msg->fq.context_b));\n'
            '\t\t\t\tcontinue;\n'
            '\t\t\t}\n'
            '\t\t\tfq_state_change(p, fq, msg, verb);\n'
            '\t\t\tif (fq->cb.fqs)')
    if old1 in s:
        s = s.replace(old1, new1, 1)
        changes += 1
        print("### F_102 v2: NULL fq guard added in MR path")
    else:
        print("### F_102 v2: WARNING MR anchor not found in qman.c")

    # Guard 2: ERN path (line ~1535) — fq->cb.ern dereferences fq
    old2 = ('\t\t\tfq = tag_to_fq(be32_to_cpu(msg->ern.tag));\n'
            '\t\t\tfq->cb.ern(p, fq, msg);')
    new2 = ('\t\t\tfq = tag_to_fq(be32_to_cpu(msg->ern.tag));\n'
            '\t\t\t/* F_102 v2: ZC datapath can produce entries with invalid context_b */\n'
            '\t\t\tif (unlikely(!fq)) {\n'
            '\t\t\t\tpr_err_once("qman: NULL fq from ERN tag=0x%x, skipping\\n",\n'
            '\t\t\t\t\t   be32_to_cpu(msg->ern.tag));\n'
            '\t\t\t\tcontinue;\n'
            '\t\t\t}\n'
            '\t\t\tfq->cb.ern(p, fq, msg);')
    if old2 in s:
        s = s.replace(old2, new2, 1)
        changes += 1
        print("### F_102 v2: NULL fq guard added in ERN path")
    else:
        print("### F_102 v2: WARNING ERN anchor not found in qman.c")

    # Guard 3: SDQCR path (line ~1650) — fq->cb.dqrr dereferences fq (existing v1 guard)
    old3 = ('\t\t\t/* SDQCR: context_b points to the FQ */\n'
            '\t\t\tfq = tag_to_fq(be32_to_cpu(dq->context_b));\n'
            '\t\t\t/* Now let the callback do its stuff */\n'
            '\t\t\tres = fq->cb.dqrr(p, fq, dq, sched_napi);')
    new3 = ('\t\t\t/* SDQCR: context_b points to the FQ */\n'
            '\t\t\tfq = tag_to_fq(be32_to_cpu(dq->context_b));\n'
            '\t\t\t/* F_102 v2: ZC datapath can produce entries with invalid context_b */\n'
            '\t\t\tif (unlikely(!fq)) {\n'
            '\t\t\t\tpr_err_once("qman: NULL fq from DQRR context_b=0x%x, skipping\\n",\n'
            '\t\t\t\t\t   be32_to_cpu(dq->context_b));\n'
            '\t\t\t\tqm_dqrr_cdc_consume_1ptr(&p->p, dq, 0);\n'
            '\t\t\t\tqm_dqrr_next(&p->p);\n'
            '\t\t\t\tcontinue;\n'
            '\t\t\t}\n'
            '\t\t\t/* Now let the callback do its stuff */\n'
            '\t\t\tres = fq->cb.dqrr(p, fq, dq, sched_napi);')
    if old3 in s:
        s = s.replace(old3, new3, 1)
        changes += 1
        print("### F_102 v2: NULL fq guard added in SDQCR path")
    else:
        print("### F_102 v2: WARNING SDQCR anchor not found in qman.c")

    with open(QMAN, 'w') as f:
        f.write(s)

if changes:
    print("### F_102 v2: %d change(s) applied" % changes)
else:
    print("### F_102 v2: WARNING no changes applied")