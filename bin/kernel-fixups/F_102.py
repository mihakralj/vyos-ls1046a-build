"""F_102 v4: Add NULL guards for ALL tag_to_fq calls AND portal entry in __poll_portal_fast.

The ZC datapath (BPID reprogram) can produce QMan portal entries (MR, ERN,
DQRR) whose context_b doesn't map to a valid qman_fq. tag_to_fq() returns
NULL, and the code dereferences fq->cb.* without checking.

v1 only guarded the SDQCR path. v2 added guards for MR and ERN paths.
v4 adds entry NULL portal guards for __poll_portal_fast and qman_p_poll_dqrr
to prevent NULL pointer dereferences when polling on isolated CPUs (isolcpus=3)
where p (qman_portal) is NULL.
"""

import os

changes = 0
QMAN = "drivers/soc/fsl/qbman/qman.c"

if os.path.exists(QMAN):
    with open(QMAN) as f:
        s = f.read()

    # Guard 0: __poll_portal_fast entry NULL portal guard
    old0 = 'static int __poll_portal_fast(struct qman_portal *p, unsigned int poll_limit)\n{'
    old0_alt = 'static int __poll_portal_fast(struct qman_portal *p, int poll_limit)\n{'
    new0 = ('static int __poll_portal_fast(struct qman_portal *p, unsigned int poll_limit)\n{\n'
            '\t/* F_102 v4: NULL portal guard for isolated CPUs (isolcpus) */\n'
            '\tif (unlikely(!p))\n'
            '\t\treturn 0;\n')
    new0_alt = ('static int __poll_portal_fast(struct qman_portal *p, int poll_limit)\n{\n'
                '\t/* F_102 v4: NULL portal guard for isolated CPUs (isolcpus) */\n'
                '\tif (unlikely(!p))\n'
                '\t\treturn 0;\n')
    if "NULL portal guard" not in s:
        if old0 in s:
            s = s.replace(old0, new0, 1)
            changes += 1
            print("### F_102 v4: NULL portal guard added in __poll_portal_fast entry")
        elif old0_alt in s:
            s = s.replace(old0_alt, new0_alt, 1)
            changes += 1
            print("### F_102 v4: NULL portal guard added in __poll_portal_fast entry (alt)")
        else:
            print("### F_102 v4: WARNING __poll_portal_fast entry anchor not found in qman.c")
    else:
        print("### F_102 v4: __poll_portal_fast entry guard already present")

    # Guard 0b: qman_p_poll_dqrr entry NULL portal guard
    old0b = 'int qman_p_poll_dqrr(struct qman_portal *p, unsigned int limit)\n{'
    new0b = ('int qman_p_poll_dqrr(struct qman_portal *p, unsigned int limit)\n{\n'
             '\t/* F_102 v4: NULL portal guard for isolated CPUs (isolcpus) */\n'
             '\tif (unlikely(!p))\n'
             '\t\treturn 0;\n')
    if old0b in s and "F_102 v4: NULL portal guard" not in s: # check if already guarded
        s = s.replace(old0b, new0b, 1)
        changes += 1
        print("### F_102 v4: NULL portal guard added in qman_p_poll_dqrr entry")

    # Guard 0c: qman_p_irqsource_add entry NULL portal guard
    old0c = 'void qman_p_irqsource_add(struct qman_portal *p, u32 bits)\n{'
    new0c = ('void qman_p_irqsource_add(struct qman_portal *p, u32 bits)\n{\n'
             '\t/* F_102 v5: NULL portal guard for isolated CPUs (isolcpus) */\n'
             '\tif (unlikely(!p))\n'
             '\t\treturn;\n')
    if old0c in s and "F_102 v5: NULL portal guard" not in s:
        s = s.replace(old0c, new0c, 1)
        changes += 1
        print("### F_102 v5: NULL portal guard added in qman_p_irqsource_add entry")

    # Guard 0d: qman_p_irqsource_remove entry NULL portal guard
    old0d = 'void qman_p_irqsource_remove(struct qman_portal *p, u32 bits)\n{'
    new0d = ('void qman_p_irqsource_remove(struct qman_portal *p, u32 bits)\n{\n'
             '\t/* F_102 v5: NULL portal guard for isolated CPUs (isolcpus) */\n'
             '\tif (unlikely(!p))\n'
             '\t\treturn;\n')
    if old0d in s and "F_102 v5: NULL portal guard" not in s:
        s = s.replace(old0d, new0d, 1)
        changes += 1
        print("### F_102 v5: NULL portal guard added in qman_p_irqsource_remove entry")

    # Guard 1: MR path (line ~1521) — fq_state_change dereferences fq
    # Uses 4 tabs (inside switch→case→for→if)
    old1 = ('\t\t\t\tfq = tag_to_fq(be32_to_cpu(msg->fq.context_b));\n'
            '\t\t\t\tfq_state_change(p, fq, msg, verb);\n'
            '\t\t\t\tif (fq->cb.fqs)')
    new1 = ('\t\t\t\tfq = tag_to_fq(be32_to_cpu(msg->fq.context_b));\n'
            '\t\t\t\t/* F_102 v3: ZC datapath can produce entries with invalid context_b */\n'
            '\t\t\t\tif (unlikely(!fq)) {\n'
            '\t\t\t\t\tpr_err_once("qman: NULL fq from MR context_b=0x%x, skipping\\n",\n'
            '\t\t\t\t\t\t   be32_to_cpu(msg->fq.context_b));\n'
            '\t\t\t\t\tcontinue;\n'
            '\t\t\t\t}\n'
            '\t\t\t\tfq_state_change(p, fq, msg, verb);\n'
            '\t\t\t\tif (fq->cb.fqs)')
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
    print("### F_102 v4: %d change(s) applied" % changes)
else:
    print("### F_102 v4: WARNING no changes applied")