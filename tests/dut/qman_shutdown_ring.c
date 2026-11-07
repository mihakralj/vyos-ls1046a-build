// SPDX-License-Identifier: GPL-2.0
/* Characterize the extracted QMan shutdown path with simulated portal rings. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;

#define DPAA_ASSERT(x)
#define QM_MCC_VERB_QUERYFQ_NP 0x45
#define QM_MCC_VERB_QUERYFQ 0x44
#define QM_MCC_VERB_ALTER_RETIRE 0x4a
#define QM_MCC_VERB_ALTER_OOS 0x4b
#define QM_MCR_VERB_MASK 0x7f
#define QM_MCR_VERB_QUERYFQ_NP QM_MCC_VERB_QUERYFQ_NP
#define QM_MCR_VERB_QUERYFQ QM_MCC_VERB_QUERYFQ
#define QM_MCR_VERB_ALTER_RETIRE QM_MCC_VERB_ALTER_RETIRE
#define QM_MCR_VERB_ALTER_OOS QM_MCC_VERB_ALTER_OOS
#define QM_MCR_RESULT_OK 0xf0
#define QM_MCR_RESULT_ERR_FQID 0xf1
#define QM_MCR_RESULT_PENDING 0xf8
#define QM_MCR_RESULT_ERROR 0xf2
#define QM_MCR_NP_STATE_MASK 0x07
#define QM_MCR_NP_STATE_OOS 0
#define QM_MCR_NP_STATE_RETIRED 1
#define QM_MCR_NP_STATE_TEN_SCHED 2
#define QM_MCR_NP_STATE_TRU_SCHED 3
#define QM_MCR_NP_STATE_PARKED 4
#define QM_MCR_NP_STATE_ACTIVE 5
#define QM_MCR_FQS_NOTEMPTY 0x01
#define QM_MCR_FQS_ORLPRESENT 0x02
#define QM_MR_VERB_TYPE_MASK 0x27
#define QM_MR_VERB_FQRN 0x21
#define QM_MR_VERB_FQRL 0x23
#define QM_MR_VERB_ERN 0x01
#define QM_DQRR_STAT_FQ_EMPTY 0x01
#define QM_SDQCR_TYPE_ACTIVE 0x10
#define QM_SDQCR_CHANNELS_DEDICATED 0x20
#define QM_SDQCR_CHANNELS_POOL_CONV(channel) (channel)
#define QM_VDQCR_NUMFRAMES_SET(n) 0
#define TARGET_FQID 7
#define OTHER_FQID 23
#define DPAA_GENALLOC_OFF 0x80000000
#define qm_channel_pool1 0x100
#define qm_fqid_set(p, value) ((p)->fqid = (value))
#define qm_fqid_get(p) ((p)->fqid)

struct qm_fqd { u32 channel; };
struct qm_fqref { u32 fqid; };
union qm_mc_command { struct qm_fqref fq; };
union qm_mc_result {
	struct { u8 verb, result; };
	struct { u8 verb, result, reserved, state; } queryfq_np;
	struct { u8 verb, result; struct qm_fqd fqd; } queryfq;
	struct { u8 verb, result, fqs; } alterfq;
};
union qm_mr_entry {
	struct { u8 verb; };
	struct { u8 verb, fqs; u32 fqid; } fq;
};
struct qm_dqrr_entry { u32 fqid; u8 stat; bool has_frame; };
struct qm_portal { int unused; };
struct device { int unused; };
struct qm_portal_config { struct device *dev; };
struct qman_portal {
	struct qm_portal p;
	struct qm_portal_config *config;
	u32 sdqcr;
};

enum scenario {
	NORMAL,
	FOREIGN_FQRN,
	PENDING_ORL,
	FOREIGN_FQRL,
	FOREIGN_RING_ENTRIES,
	REUSED_RESULT,
	REUSED_NOTEMPTY,
	OVERWRITTEN_BEFORE_SNAPSHOT,
	ALREADY_OOS,
	ALREADY_RETIRED,
	BAD_NP_RESULT,
	BAD_RESERVED_NP_RESULT,
	WRONG_NP_VERB,
	BAD_QUERY_RESULT,
	WRONG_QUERY_VERB,
	WRONG_RETIRE_VERB,
	WRONG_OOS_VERB,
	WRONG_RETIRED_OOS_VERB,
};

static struct device device;
static struct qm_portal_config config = { .dev = &device };
static struct qman_portal portal = { .config = &config, .sdqcr = 0x55 };
static void *qm_fqalloc;
static union qm_mc_command command;
static union qm_mc_result result;
static union qm_mr_entry messages[8];
static struct qm_dqrr_entry dequeues[8];
static enum scenario current;
static unsigned int message_count, message_pos, dequeue_count, dequeue_pos;
static unsigned int foreign_messages, discarded_frames, retire_commands, oos_commands, errors;
static unsigned int released_fqids;
static bool affine_held, target_retired, target_orl_pending, target_nonempty;

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

static struct qman_portal *get_portal_for_channel(u32 channel)
{
	assert(channel < qm_channel_pool1);
	return &portal;
}

static void push_message(u8 verb, u32 fqid, u8 fqs)
{
	assert(message_count < sizeof(messages) / sizeof(messages[0]));
	messages[message_count++].fq = (typeof(messages[0].fq)) {
		.verb = verb, .fqs = fqs, .fqid = fqid,
	};
	if (fqid == TARGET_FQID && verb == QM_MR_VERB_FQRN) {
		target_retired = true;
		target_orl_pending = !!(fqs & QM_MCR_FQS_ORLPRESENT);
	}
	if (fqid == TARGET_FQID && verb == QM_MR_VERB_FQRL)
		target_orl_pending = false;
}

static void push_dequeue(u32 fqid, u8 stat, bool has_frame)
{
	assert(dequeue_count < sizeof(dequeues) / sizeof(dequeues[0]));
	dequeues[dequeue_count++] = (struct qm_dqrr_entry) {
		.fqid = fqid, .stat = stat, .has_frame = has_frame,
	};
}

static union qm_mc_command *qm_mc_start(struct qm_portal *p)
{
	assert(p == &portal.p && affine_held);
	memset(&command, 0, sizeof(command));
	return &command;
}

static void qm_mc_commit(struct qm_portal *p, u8 verb)
{
	assert(p == &portal.p && command.fq.fqid ==
	       (current == BAD_RESERVED_NP_RESULT ? 0 : TARGET_FQID));
	memset(&result, 0, sizeof(result));
	result.verb = verb;
	result.result = QM_MCR_RESULT_OK;
	switch (verb) {
	case QM_MCC_VERB_QUERYFQ_NP:
		result.queryfq_np.state = QM_MCR_NP_STATE_TRU_SCHED;
		if (current == ALREADY_OOS || current == BAD_NP_RESULT ||
		    current == BAD_RESERVED_NP_RESULT ||
		    current == WRONG_NP_VERB)
			result.queryfq_np.state = QM_MCR_NP_STATE_OOS;
		if (current == BAD_NP_RESULT || current == BAD_RESERVED_NP_RESULT)
			result.result = QM_MCR_RESULT_ERR_FQID;
		if (current == WRONG_NP_VERB)
			result.verb = QM_MCC_VERB_QUERYFQ;
		if (current == ALREADY_RETIRED || current == WRONG_RETIRED_OOS_VERB) {
			result.queryfq_np.state = QM_MCR_NP_STATE_RETIRED;
			target_retired = true;
		}
		break;
	case QM_MCC_VERB_QUERYFQ:
		result.queryfq.fqd.channel = qm_channel_pool1;
		if (current == BAD_QUERY_RESULT)
			result.result = QM_MCR_RESULT_ERROR;
		if (current == WRONG_QUERY_VERB)
			result.verb = QM_MCC_VERB_QUERYFQ_NP;
		break;
	case QM_MCC_VERB_ALTER_RETIRE:
		retire_commands++;
		if (current == WRONG_RETIRE_VERB) {
			result.verb = QM_MCC_VERB_QUERYFQ;
			break;
		}
		if (current == REUSED_RESULT || current == FOREIGN_FQRL ||
		    current == OVERWRITTEN_BEFORE_SNAPSHOT) {
			target_retired = target_orl_pending = true;
			result.alterfq.fqs = QM_MCR_FQS_ORLPRESENT;
			break;
		}
		if (current == REUSED_NOTEMPTY) {
			target_retired = target_nonempty = true;
			result.alterfq.fqs = QM_MCR_FQS_NOTEMPTY;
			break;
		}
		result.result = QM_MCR_RESULT_PENDING;
		if (current == FOREIGN_FQRN) {
			push_message(QM_MR_VERB_FQRN, OTHER_FQID, 0);
			break;
		}
		push_message(QM_MR_VERB_FQRN, TARGET_FQID,
			     current == PENDING_ORL ? QM_MCR_FQS_ORLPRESENT : 0);
		if (current == FOREIGN_RING_ENTRIES) {
			push_message(QM_MR_VERB_FQRN, OTHER_FQID, 0);
			push_message(QM_MR_VERB_ERN, OTHER_FQID, 0);
		}
		break;
	case QM_MCC_VERB_ALTER_OOS:
		oos_commands++;
		if (!target_retired || target_orl_pending || target_nonempty)
			result.result = QM_MCR_RESULT_ERROR;
		else if (current == WRONG_OOS_VERB || current == WRONG_RETIRED_OOS_VERB)
			result.verb = QM_MCC_VERB_QUERYFQ_NP;
		break;
	default:
		assert(false);
	}
}

static int qm_mc_result_timeout(struct qm_portal *p, union qm_mc_result **mcr)
{
	assert(p == &portal.p && affine_held);
	*mcr = &result;
	if (current == OVERWRITTEN_BEFORE_SNAPSHOT &&
	    result.verb == QM_MCC_VERB_ALTER_RETIRE)
		result.alterfq.fqs = 0;
	return 1;
}

static u32 qm_fqd_get_chan(const struct qm_fqd *fqd) { return fqd->channel; }
static u32 qm_fqd_get_wq(const struct qm_fqd *fqd) { return 0; }
static void dev_err(struct device *dev, const char *format, ...) { errors++; }
static void pr_debug(const char *format, ...) {}
static void gen_pool_free(void *pool, unsigned long address, size_t count)
{
	assert(pool == qm_fqalloc && address == (TARGET_FQID | DPAA_GENALLOC_OFF));
	assert(count == 1);
	released_fqids++;
}
static void cpu_relax(void) {}
static void qm_mr_pvb_update(struct qm_portal *p)
{
	if (current == FOREIGN_FQRL && !message_count)
		push_message(QM_MR_VERB_FQRL, OTHER_FQID, 0);
	if (current == REUSED_RESULT && !message_count)
		push_message(QM_MR_VERB_FQRL, TARGET_FQID, 0);
}
static const union qm_mr_entry *qm_mr_current(struct qm_portal *p)
{
	return message_pos < message_count ? &messages[message_pos] : NULL;
}
static void qm_mr_next(struct qm_portal *p) { message_pos++; }
static void qm_mr_cci_consume_to_current(struct qm_portal *p)
{
	const union qm_mr_entry *msg = &messages[message_pos - 1];

	if (msg->fq.fqid != TARGET_FQID)
		foreign_messages++;
}
static void qm_dqrr_pvb_update(struct qm_portal *p) {}
static const struct qm_dqrr_entry *qm_dqrr_current(struct qm_portal *p)
{
	return dequeue_pos < dequeue_count ? &dequeues[dequeue_pos] : NULL;
}
static void qm_dqrr_cdc_consume_1ptr(struct qm_portal *p,
				     const struct qm_dqrr_entry *dqrr, int dca)
{
	if (dqrr->has_frame)
		discarded_frames++;
}
static void qm_dqrr_next(struct qm_portal *p) { dequeue_pos++; }
static void qm_dqrr_sdqcr_set(struct qm_portal *p, u32 value) {}
static void qm_dqrr_vdqcr_set(struct qm_portal *p, u32 fqid)
{
	assert(fqid == TARGET_FQID);
	target_nonempty = false;
	push_dequeue(TARGET_FQID, QM_DQRR_STAT_FQ_EMPTY, false);
}
static int drain_mr_fqrni(struct qm_portal *p)
{
	/* Model two competing commands reusing the returned RR entry. */
	if (current == REUSED_RESULT || current == REUSED_NOTEMPTY)
		result.alterfq.fqs = 0;
	return 0;
}

#include "qman_shutdown_body.h"

static void reset(enum scenario scenario)
{
	current = scenario;
	memset(&command, 0, sizeof(command));
	memset(&result, 0, sizeof(result));
	memset(messages, 0, sizeof(messages));
	memset(dequeues, 0, sizeof(dequeues));
	message_count = message_pos = dequeue_count = dequeue_pos = 0;
	foreign_messages = discarded_frames = retire_commands = oos_commands = errors = 0;
	released_fqids = 0;
	affine_held = target_retired = target_orl_pending = target_nonempty = false;
	if (scenario == FOREIGN_RING_ENTRIES) {
		push_dequeue(TARGET_FQID, 0, true);
		push_dequeue(OTHER_FQID, 0, true);
	}
}

static int check(enum scenario scenario, const char *name, bool control)
{
	bool unsafe;
	int rc;

	reset(scenario);
	/* A hypothetical caller refuses shutdown rather than touching a live portal. */
	rc = control ? -EINPROGRESS : qman_shutdown_fq(TARGET_FQID);
	switch (scenario) {
	case FOREIGN_FQRN:
		unsafe = oos_commands && !target_retired;
		break;
	case PENDING_ORL:
	case FOREIGN_FQRL:
	case REUSED_RESULT:
	case OVERWRITTEN_BEFORE_SNAPSHOT:
		unsafe = oos_commands && target_orl_pending;
		break;
	case REUSED_NOTEMPTY:
		unsafe = oos_commands && target_nonempty;
		break;
	case FOREIGN_RING_ENTRIES:
		unsafe = foreign_messages || discarded_frames;
		break;
	default:
		assert(false);
	}
	if (control) {
		assert(rc == -EINPROGRESS && !unsafe && !affine_held);
		printf("QMan shutdown %s: simulated refusal\n", name);
		return 0;
	}
	if (!unsafe) {
		printf("QMan shutdown %s: contained\n", name);
		return 0;
	}
	printf("QMan shutdown %s: NOT CONTAINED (rc=%d)\n", name, rc);
	return 1;
}

static int check_response(enum scenario scenario, const char *name, bool control)
{
	unsigned int expected_retire, expected_oos;
	int rc;
	bool unsafe;

	reset(scenario);
	if (control)
		rc = -EINPROGRESS;
	else if (scenario == BAD_RESERVED_NP_RESULT)
		rc = qman_shutdown_fq(0);
	else
		rc = qman_release_fqid(TARGET_FQID);
	if (control) {
		assert(rc == -EINPROGRESS && !retire_commands && !oos_commands &&
		       !released_fqids && !affine_held);
		printf("QMan shutdown %s: simulated refusal\n", name);
		return 0;
	}
	expected_retire = scenario == WRONG_RETIRE_VERB || scenario == WRONG_OOS_VERB;
	expected_oos = scenario == WRONG_OOS_VERB || scenario == WRONG_RETIRED_OOS_VERB;
	unsafe = rc != -EIO || retire_commands != expected_retire ||
		 oos_commands != expected_oos || released_fqids || affine_held ||
		 errors != 1;
	printf("QMan shutdown %s: %s (rc=%d)\n", name,
	       unsafe ? "NOT CONTAINED" : "contained", rc);
	return unsafe;
}

int main(int argc, char **argv)
{
	bool control = argc == 2 && !strcmp(argv[1], "--oracle-control");
	bool snapshot = argc == 2 && !strcmp(argv[1], "--check-retire-snapshot");
	bool responses = argc == 2 && !strcmp(argv[1], "--check-shutdown-responses");
	int failures = 0;

	assert(argc == 1 || control || snapshot || responses);
	reset(NORMAL);
	assert(qman_shutdown_fq(TARGET_FQID) == 0);
	assert(oos_commands == 1 && !affine_held);
	reset(ALREADY_OOS);
	assert(qman_shutdown_fq(TARGET_FQID) == 0);
	assert(!retire_commands && !oos_commands && !affine_held);
	reset(ALREADY_OOS);
	assert(qman_release_fqid(TARGET_FQID) == 0 && released_fqids == 1);
	reset(ALREADY_RETIRED);
	assert(qman_shutdown_fq(TARGET_FQID) == 0);
	assert(!retire_commands && oos_commands == 1 && !affine_held);
	if (snapshot) {
		failures += check(REUSED_RESULT, "reused result entry", false);
		failures += check(REUSED_NOTEMPTY, "reused nonempty result", false);
		printf("QMan retirement snapshot: %d uncontained case(s)\n",
		       failures);
		return failures ? 1 : 0;
	}
	if (!responses) {
		failures += check(FOREIGN_FQRN, "foreign FQRN", control);
		failures += check(PENDING_ORL, "pending FQRN flags", control);
		failures += check(FOREIGN_FQRL, "foreign FQRL", control);
		failures += check(FOREIGN_RING_ENTRIES, "unrelated MR/DQRR entries", control);
		failures += check(REUSED_RESULT, "reused result entry", control);
		failures += check(REUSED_NOTEMPTY, "reused nonempty result", control);
		failures += check(OVERWRITTEN_BEFORE_SNAPSHOT,
				  "result overwritten before snapshot", control);
	}
	failures += check_response(BAD_NP_RESULT, "failed QUERYFQ_NP", control);
	failures += check_response(BAD_RESERVED_NP_RESULT,
				   "failed reserved FQID 0 QUERYFQ_NP", control);
	failures += check_response(WRONG_NP_VERB, "wrong QUERYFQ_NP verb", control);
	failures += check_response(BAD_QUERY_RESULT, "failed QUERYFQ", control);
	failures += check_response(WRONG_QUERY_VERB, "wrong QUERYFQ verb", control);
	failures += check_response(WRONG_RETIRE_VERB, "wrong ALTER_RETIRE verb", control);
	failures += check_response(WRONG_OOS_VERB, "wrong ALTER_OOS verb", control);
	failures += check_response(WRONG_RETIRED_OOS_VERB,
				   "wrong ALTER_OOS verb for retired FQ", control);
	printf("QMan shutdown characterization: %d uncontained case(s)\n",
	       failures);
	return failures ? 1 : 0;
}
