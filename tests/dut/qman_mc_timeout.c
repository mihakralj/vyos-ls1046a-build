// SPDX-License-Identifier: GPL-2.0
/* Model a late MC response using the extracted QMan portal implementation. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;

#define DPAA_ASSERT(x)
#define QM_MCC_VERB_VBIT 0x80
#define QM_MCC_VERB_INITFQ_SCHED 0x41
#define QM_MCC_VERB_QUERYFQ 0x44
#define QM_MCC_VERB_QUERYFQ_NP 0x45
#define QM_MCR_VERB_MASK 0x7f
#define QM_MCR_VERB_QUERYFQ QM_MCC_VERB_QUERYFQ
#define QM_MCR_VERB_QUERYFQ_NP QM_MCC_VERB_QUERYFQ_NP
#define QM_MCR_RESULT_OK 0xf0
#define QM_MCR_RESULT_ERR_FQID 0xf1
#define QM_MCR_TIMEOUT 4
#define pr_err_ratelimited(...) (unexpected_verb_logs++)

struct qm_fqd { u32 word; };
struct qm_mcr_queryfq_np { u8 verb, result, reserved[2]; u32 state; };
struct qm_mcc_fq { u8 verb, reserved[3]; u32 fqid; };
union qm_mc_command {
	struct { u8 _ncw_verb; u8 reserved[63]; };
	struct qm_mcc_fq fq;
};
union qm_mc_result {
	struct { u8 verb, result; u8 reserved[62]; };
	struct {
		u8 verb, result, reserved[8];
		struct qm_fqd fqd;
	} queryfq;
	struct qm_mcr_queryfq_np queryfq_np;
};
struct qm_mc {
	union qm_mc_command *cr;
	union qm_mc_result *rr;
	u8 rridx, vbit;
};
struct qm_portal { struct qm_mc mc; };
struct qman_portal { struct qm_portal p; };
struct qman_fq { u32 fqid; };

static struct qman_portal portal;
static union qm_mc_command command;
static union qm_mc_result responses[2];
static bool affine_held, init_pending, query_pending, command_reused;
static bool deliver_late, timeout_query, deliver_same_verb_late, invalid_fqid;
static unsigned int submissions, unexpected_verb_logs;

static struct qman_portal *get_affine_portal(void)
{
	assert(!affine_held);
	affine_held = true;
	return &portal;
}

static void put_affine_portal(void)
{
	assert(affine_held);
	affine_held = false;
}

static void dpaa_zero(void *ptr)
{
	assert(ptr == &command);
	if (init_pending || query_pending)
		command_reused = true;
	memset(ptr, 0, sizeof(command));
}

static void dpaa_flush(void *ptr)
{
	union qm_mc_result *rr = &responses[portal.p.mc.rridx];
	u8 verb = command._ncw_verb & QM_MCR_VERB_MASK;

	assert(ptr == &command && affine_held);
	submissions++;
	if (verb == QM_MCC_VERB_INITFQ_SCHED) {
		assert(!init_pending);
		init_pending = true;
	} else {
		assert(verb == QM_MCC_VERB_QUERYFQ ||
		       verb == QM_MCC_VERB_QUERYFQ_NP);
		if (timeout_query) {
			timeout_query = false;
			query_pending = true;
		} else {
			rr->verb = (deliver_late ? QM_MCC_VERB_INITFQ_SCHED : verb) |
				   portal.p.mc.vbit;
			rr->result = invalid_fqid ? QM_MCR_RESULT_ERR_FQID :
						    QM_MCR_RESULT_OK;
			if (verb == QM_MCC_VERB_QUERYFQ_NP)
				rr->queryfq_np.state = 0x87654321;
			else
				rr->queryfq.fqd.word = deliver_late ? 0 :
					deliver_same_verb_late ? 0xabcdef01 : 0x12345678;
		}
	}
}

static void dma_wmb(void) {}
static void dpaa_invalidate_touch_ro(void *ptr) {}
static void udelay(int usecs) {}
static void qm_fqid_set(struct qm_mcc_fq *fq, u32 fqid) { fq->fqid = fqid; }

#include "qman_mc_body.h"

static void reset(void)
{
	memset(&portal, 0, sizeof(portal));
	memset(&command, 0, sizeof(command));
	memset(responses, 0, sizeof(responses));
	portal.p.mc.cr = &command;
	portal.p.mc.rr = responses;
	portal.p.mc.rridx = 1;
	portal.p.mc.vbit = QM_MCC_VERB_VBIT;
	affine_held = init_pending = query_pending = command_reused = false;
	deliver_late = timeout_query = deliver_same_verb_late = invalid_fqid = false;
	submissions = unexpected_verb_logs = 0;
}

int main(int argc, char **argv)
{
	struct qman_fq fq = { .fqid = 7 };
	struct qm_fqd fqd = { .word = 0xdeadbeef };
	struct qm_mcr_queryfq_np np;
	struct qman_portal *p;
	union qm_mc_result *result;
	bool control = argc == 2 && !strcmp(argv[1], "--oracle-control");
	bool verb_check = argc == 2 && !strcmp(argv[1], "--check-verb");
	int rc, uncontained = 0;

	assert(argc == 1 || control || verb_check);
	reset();
	assert(qman_query_fq(&fq, &fqd) == 0);
	assert(fqd.word == 0x12345678 && submissions == 1 && !command_reused);

	if (verb_check) {
		reset();
		deliver_late = true;
		fqd.word = 0xdeadbeef;
		rc = qman_query_fq(&fq, &fqd);
		uncontained += rc != -EIO || fqd.word != 0xdeadbeef ||
			       submissions != 1 || affine_held ||
			       unexpected_verb_logs != 1;

		reset();
		memset(&np, 0, sizeof(np));
		rc = qman_query_fq_np(&fq, &np);
		assert(!rc && np.state == 0x87654321 && !affine_held);

		reset();
		invalid_fqid = true;
		np.state = 0xdeadbeef;
		rc = qman_query_fq_np(&fq, &np);
		assert(rc == -ERANGE && np.state == 0xdeadbeef && !affine_held);

		reset();
		deliver_late = true;
		np.state = 0xdeadbeef;
		rc = qman_query_fq_np(&fq, &np);
		uncontained += rc != -EIO || np.state != 0xdeadbeef ||
			       submissions != 1 || affine_held ||
			       unexpected_verb_logs != 1;

		reset();
		deliver_late = invalid_fqid = true;
		np.state = 0xdeadbeef;
		rc = qman_query_fq_np(&fq, &np);
		uncontained += rc != -EIO || np.state != 0xdeadbeef ||
			       submissions != 1 || affine_held ||
			       unexpected_verb_logs != 1;
		printf("QMan FQ query response verbs: %d uncontained case(s)\n",
		       uncontained);
		return uncontained ? 1 : 0;
	}

	reset();
	p = get_affine_portal();
	qm_mc_start(&p->p);
	qm_mc_commit(&p->p, QM_MCC_VERB_INITFQ_SCHED);
	assert(!qm_mc_result_timeout(&p->p, &result) && !result);
	put_affine_portal();
	deliver_late = true;
	fqd.word = 0xdeadbeef;
	rc = control ? -ESHUTDOWN : qman_query_fq(&fq, &fqd);
	if (command_reused)
		uncontained++;
	if (!rc && fqd.word == 0)
		uncontained++;
	if (control)
		assert(rc == -ESHUTDOWN && submissions == 1 && fqd.word == 0xdeadbeef);

	reset();
	fqd.word = 0xdeadbeef;
	timeout_query = true;
	assert(qman_query_fq(&fq, &fqd) == -ETIMEDOUT);
	assert(query_pending && submissions == 1 && fqd.word == 0xdeadbeef);
	fq.fqid++;
	deliver_same_verb_late = true;
	rc = control ? -ESHUTDOWN : qman_query_fq(&fq, &fqd);
	if (!rc && fqd.word == 0xabcdef01)
		uncontained++;
	if (control)
		assert(rc == -ESHUTDOWN && submissions == 1 && fqd.word == 0xdeadbeef);

	printf("QMan MC timeout: %d uncontained checks "
	       "(command reuse, different-verb and same-verb stale responses)\n",
	       uncontained);
	return uncontained ? 1 : 0;
}
