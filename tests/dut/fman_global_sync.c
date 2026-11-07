/* Host fault and concurrency characterization of generated FMan INV0 writers. */
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

typedef uint32_t u32;
typedef uint64_t u64;
typedef long long loff_t;
#define __user
#define INV0 0x80000000U
#define FMAN_FPM_EXTC_INV0 INV0
#define FMAN_FPM_EXTC_POLL_MAX 100000U
#define FMAN_EHASH_SYNC_TRIES 3
#define FMAN_PORT_TYPE_RX 1
#define FMAN_PORT_TYPE_OH 2
#define NIA_ENG_BMI 0x00500000U
#define NIA_BMI_AC_ENQ_FRAME 0x00000002U
#define NIA_ENG_HWK 0x00480000U
#define NIA_KG_CC_EN 0x00000200U
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define dev_warn(dev, ...) ((void)(dev))
#define dev_info(dev, ...) ((void)(dev))
#define dev_dbg(dev, ...) ((void)(dev))
#define dev_err(dev, ...) ((void)(dev))
#define pr_warn(...) ((void)0)
#define pr_warn_ratelimited(...) ((void)0)

struct fman {
	pthread_mutex_t cc_port_lock;
	pthread_mutex_t inv0_lock;
	pthread_mutex_t hw_lock;
	pthread_cond_t changed;
	bool inv0_faulted, active, stuck, block_reads, first_read, release;
	bool first_finished, second_started, second_blocked, second_waiting;
	bool second_finished, block_idle_reads, release_idle;
	bool hold_first_write, first_write_ready, release_first_write;
	unsigned int writes, duplicates, pending_polls, engaged, idle_reads;
	unsigned int publication, accepted_publication, completed_publication;
	pthread_mutex_t *first_locks[16], *second_waiting_lock;
	unsigned int first_lock_count;
};
struct bmi_regs {
	struct { u32 fmbm_rccb, fmbm_rfpne; } rx;
	struct { u32 fmbm_occb, fmbm_ofpne; } oh;
};
struct fman_port {
	int port_type;
	void *dev;
	struct fman *fm;
	struct bmi_regs *bmi_regs;
	bool cc_engaged;
};
struct fman_pcd { struct fman *fman; pthread_mutex_t fe_lock; };
struct fman_pcd_ehash_table { struct fman_pcd *pcd; };
struct seq_file { void *private; };
struct file { void *private_data; };

static bool ehash_sync_diag;
static unsigned int ehash_sync_inject, ehash_sync_injected;
static unsigned int ehash_sync_timeouts, ehash_sync_wedged;
static unsigned int ehash_insert_sync_timeouts;
static _Thread_local struct fman *writer_fm;
static _Thread_local bool writer_is_second;

static void mutex_lock(pthread_mutex_t *lock)
{
	if (writer_fm && writer_is_second && lock != &writer_fm->hw_lock) {
		int rc = pthread_mutex_trylock(lock);

		if (!rc)
			return;
		assert(rc == EBUSY);
		assert(!pthread_mutex_lock(&writer_fm->hw_lock));
		writer_fm->second_blocked = true;
		writer_fm->second_waiting = true;
		writer_fm->second_waiting_lock = lock;
		assert(!pthread_cond_broadcast(&writer_fm->changed));
		assert(!pthread_mutex_unlock(&writer_fm->hw_lock));
		assert(!pthread_mutex_lock(lock));
		assert(!pthread_mutex_lock(&writer_fm->hw_lock));
		writer_fm->second_waiting = false;
		writer_fm->second_waiting_lock = NULL;
		assert(!pthread_cond_broadcast(&writer_fm->changed));
		assert(!pthread_mutex_unlock(&writer_fm->hw_lock));
		return;
	}
	assert(!pthread_mutex_lock(lock));
	if (writer_fm && !writer_is_second && lock != &writer_fm->hw_lock) {
		assert(!pthread_mutex_lock(&writer_fm->hw_lock));
		assert(writer_fm->first_lock_count < 16);
		writer_fm->first_locks[writer_fm->first_lock_count++] = lock;
		assert(!pthread_cond_broadcast(&writer_fm->changed));
		assert(!pthread_mutex_unlock(&writer_fm->hw_lock));
	}
}

static void mutex_unlock(pthread_mutex_t *lock)
{
	if (writer_fm && !writer_is_second && lock != &writer_fm->hw_lock) {
		unsigned int i;

		assert(!pthread_mutex_lock(&writer_fm->hw_lock));
		for (i = 0; i < writer_fm->first_lock_count; i++)
			if (writer_fm->first_locks[i] == lock)
				break;
		assert(i < writer_fm->first_lock_count);
		writer_fm->first_locks[i] =
			writer_fm->first_locks[--writer_fm->first_lock_count];
		assert(!pthread_mutex_unlock(lock));
		assert(!pthread_cond_broadcast(&writer_fm->changed));
		assert(!pthread_mutex_unlock(&writer_fm->hw_lock));
		return;
	}
	assert(!pthread_mutex_unlock(lock));
}
static void iowrite32be(u32 value, u32 *address) { *address = value; }
static u32 ioread32be(u32 *address) { return *address; }
static void udelay(unsigned int us) { }
static void dma_wmb(void) { }
static u64 ktime_get_ns(void) { return 0; }
static void fman_pcd_ehash_sync_diag_record(bool enabled, u64 started,
					     unsigned int polls, u32 extc) { }
static struct fman *fman_pcd_get_fman(struct fman_pcd *pcd) { return pcd->fman; }
static void *fman_get_dev(struct fman *fm) { return NULL; }
static int fman_pcd_hc_ehash_sync(struct fman_pcd *pcd) { return 0; }
static unsigned int fman_cc_engaged_get(struct fman *fm) { return fm->engaged; }
static void fman_cc_engaged_inc(struct fman *fm) { fm->engaged++; }
static void fman_cc_engaged_dec(struct fman *fm) { if (fm->engaged) fm->engaged--; }
static void fman_set_fpm_extc(struct fman *fm, u32 value)
{
	assert(value == INV0);
	mutex_lock(&fm->hw_lock);
	if (fm->hold_first_write && !fm->writes) {
		fm->first_write_ready = true;
		assert(!pthread_cond_broadcast(&fm->changed));
		while (!fm->release_first_write)
			assert(!pthread_cond_wait(&fm->changed, &fm->hw_lock));
	}
	fm->writes++;
	if (fm->active) {
		fm->duplicates++;
	} else {
		fm->accepted_publication = fm->publication;
		fm->active = true;
	}
	assert(!pthread_cond_broadcast(&fm->changed));
	mutex_unlock(&fm->hw_lock);
}

static u32 fman_get_fpm_extc(struct fman *fm)
{
	u32 result;

	mutex_lock(&fm->hw_lock);
	if (fm->block_idle_reads && !fm->active) {
		fm->idle_reads++;
		assert(!pthread_cond_broadcast(&fm->changed));
		while (!fm->release_idle)
			assert(!pthread_cond_wait(&fm->changed, &fm->hw_lock));
		mutex_unlock(&fm->hw_lock);
		return 0;
	}
	if (fm->active && fm->block_reads) {
		if (!fm->first_read) {
			fm->first_read = true;
			assert(!pthread_cond_broadcast(&fm->changed));
			while (!fm->release)
				assert(!pthread_cond_wait(&fm->changed, &fm->hw_lock));
		} else if (fm->writes > 1) {
			while (!fm->release)
				assert(!pthread_cond_wait(&fm->changed, &fm->hw_lock));
		}
		if (fm->block_reads) {
			mutex_unlock(&fm->hw_lock);
			return INV0;
		}
	}
	if (fm->active && !fm->stuck) {
		if (fm->pending_polls)
			fm->pending_polls--;
		else {
			fm->active = false;
			fm->completed_publication = fm->accepted_publication;
		}
	}
	result = fm->active ? INV0 : 0;
	mutex_unlock(&fm->hw_lock);
	return result;
}

#include "fman_global_sync_body.h"

enum writer { RX, OH, EHASH, F177_DEBUG, F177_PRODUCTION, EXTC };
static unsigned int unsafe_cases;
static bool diagnostic_disabled;

static void report(const char *scenario, bool safe)
{
	printf("%s: %s\n", scenario, safe ? "contained" : "NOT CONTAINED");
	if (!safe)
		unsafe_cases++;
}

static const char *writer_name(enum writer writer)
{
	static const char *const names[] = {
		"RX arm", "O/H arm", "ehash", "F-177 debug insert",
		"F-177 production insert", "fe_extc debugfs",
	};

	assert(writer <= EXTC);
	return names[writer];
}

struct context {
	struct fman fm;
	struct fman_pcd pcd;
	struct fman_pcd_ehash_table table;
	struct bmi_regs rx_regs, oh_regs;
	struct fman_port rx, oh;
	pthread_mutex_t control_lock;
};

static void init_context(struct context *ctx)
{
	memset(ctx, 0, sizeof(*ctx));
	assert(!pthread_mutex_init(&ctx->fm.cc_port_lock, NULL));
	assert(!pthread_mutex_init(&ctx->fm.inv0_lock, NULL));
	assert(!pthread_mutex_init(&ctx->fm.hw_lock, NULL));
	assert(!pthread_cond_init(&ctx->fm.changed, NULL));
	assert(!pthread_mutex_init(&ctx->pcd.fe_lock, NULL));
	assert(!pthread_mutex_init(&ctx->control_lock, NULL));
	ctx->pcd.fman = &ctx->fm;
	ctx->fm.publication = 1;
	ctx->table.pcd = &ctx->pcd;
	ctx->rx_regs.rx.fmbm_rfpne = NIA_ENG_HWK;
	ctx->rx = (struct fman_port) {
		.port_type = FMAN_PORT_TYPE_RX, .fm = &ctx->fm,
		.bmi_regs = &ctx->rx_regs,
	};
	ctx->oh = (struct fman_port) {
		.port_type = FMAN_PORT_TYPE_OH, .fm = &ctx->fm,
		.bmi_regs = &ctx->oh_regs,
	};
}

static void destroy_context(struct context *ctx)
{
	assert(!pthread_cond_destroy(&ctx->fm.changed));
	assert(!pthread_mutex_destroy(&ctx->fm.hw_lock));
	assert(!pthread_mutex_destroy(&ctx->fm.inv0_lock));
	assert(!pthread_mutex_destroy(&ctx->fm.cc_port_lock));
	assert(!pthread_mutex_destroy(&ctx->pcd.fe_lock));
	assert(!pthread_mutex_destroy(&ctx->control_lock));
}

static int invoke(struct context *ctx, enum writer writer)
{
	struct seq_file seq = { .private = &ctx->pcd };
	struct file file = { .private_data = &seq };
	ssize_t ret;
	u32 extc = 0;
	int err;

	switch (writer) {
	case RX: return fman_port_set_cc_base(&ctx->rx, 0x100);
	case OH: return fman_port_set_oh_cc_base(&ctx->oh, 0x100);
	case EHASH:
		mutex_lock(&ctx->pcd.fe_lock);
		err = fman_pcd_ehash_sync(&ctx->table, &extc);
		mutex_unlock(&ctx->pcd.fe_lock);
		return err;
	case F177_DEBUG:
		mutex_lock(&ctx->pcd.fe_lock);
		err = f177_debug(&ctx->pcd);
		mutex_unlock(&ctx->pcd.fe_lock);
		return err;
	case F177_PRODUCTION:
		mutex_lock(&ctx->pcd.fe_lock);
		err = f177_production(&ctx->pcd);
		mutex_unlock(&ctx->pcd.fe_lock);
		return err;
	case EXTC:
		ret = fman_pcd_fe_extc_write(&file, "sync", 4, NULL);
		return ret < 0 ? (int)ret : 0;
	}
	return -EINVAL;
}

struct call {
	struct context *ctx;
	enum writer writer;
	bool second, control;
	int result;
};

static void *run_writer(void *arg)
{
	struct call *call = arg;
	struct fman *fm = &call->ctx->fm;

	writer_fm = fm;
	writer_is_second = call->second;
	if (call->second) {
		mutex_lock(&fm->hw_lock);
		fm->publication++;
		fm->second_started = true;
		assert(!pthread_cond_broadcast(&fm->changed));
		mutex_unlock(&fm->hw_lock);
	}
	if (call->control)
		mutex_lock(&call->ctx->control_lock);
	call->result = invoke(call->ctx, call->writer);
	if (call->control)
		mutex_unlock(&call->ctx->control_lock);
	mutex_lock(&fm->hw_lock);
	if (call->second)
		fm->second_finished = true;
	else
		fm->first_finished = true;
	assert(!pthread_cond_broadcast(&fm->changed));
	mutex_unlock(&fm->hw_lock);
	writer_fm = NULL;
	return NULL;
}

static void wait_for_change(struct fman *fm, const struct timespec *deadline)
{
	int rc = pthread_cond_timedwait(&fm->changed, &fm->hw_lock, deadline);

	if (rc == ETIMEDOUT)
		fprintf(stderr, "writer made no observable progress\n");
	assert(!rc);
}

static bool first_holds_second_lock(struct fman *fm)
{
	for (unsigned int i = 0; i < fm->first_lock_count; i++)
		if (fm->first_locks[i] == fm->second_waiting_lock)
			return true;
	return false;
}

static void test_idle_check_race(void)
{
	struct context ctx;
	struct call a = { .ctx = &ctx, .writer = RX };
	struct call b = { .ctx = &ctx, .writer = EHASH, .second = true };
	struct timespec deadline;
	pthread_t one, two;
	bool skipped;

	init_context(&ctx);
	ctx.fm.block_idle_reads = true;
	ctx.fm.block_reads = true;
	ctx.fm.hold_first_write = true;
	assert(!clock_gettime(CLOCK_REALTIME, &deadline));
	deadline.tv_sec += 5;
	assert(!pthread_create(&one, NULL, run_writer, &a));
	mutex_lock(&ctx.fm.hw_lock);
	while (!ctx.fm.idle_reads && !ctx.fm.first_write_ready &&
	       !ctx.fm.first_finished)
		wait_for_change(&ctx.fm, &deadline);
	skipped = !ctx.fm.idle_reads;
	if (skipped) {
		ctx.fm.release_first_write = true;
		ctx.fm.release = true;
		ctx.fm.block_reads = false;
		assert(!pthread_cond_broadcast(&ctx.fm.changed));
		mutex_unlock(&ctx.fm.hw_lock);
		assert(!pthread_join(one, NULL));
		puts("  idle-check race: first writer does not inspect idle");
		destroy_context(&ctx);
		return;
	}
	mutex_unlock(&ctx.fm.hw_lock);
	assert(!pthread_create(&two, NULL, run_writer, &b));
	mutex_lock(&ctx.fm.hw_lock);
	while (ctx.fm.idle_reads < 2 && !ctx.fm.second_waiting &&
	       !ctx.fm.second_finished)
		wait_for_change(&ctx.fm, &deadline);
	ctx.fm.release_idle = true;
	assert(!pthread_cond_broadcast(&ctx.fm.changed));
	while (!ctx.fm.first_write_ready && !ctx.fm.first_finished)
		wait_for_change(&ctx.fm, &deadline);
	while (ctx.fm.first_write_ready && ctx.fm.idle_reads < 2 &&
	       !(ctx.fm.second_waiting && first_holds_second_lock(&ctx.fm)) &&
	       !ctx.fm.second_finished)
		wait_for_change(&ctx.fm, &deadline);
	ctx.fm.release_first_write = true;
	assert(!pthread_cond_broadcast(&ctx.fm.changed));
	while (!ctx.fm.first_read && !ctx.fm.first_finished)
		wait_for_change(&ctx.fm, &deadline);
	while (ctx.fm.first_read && ctx.fm.writes < 2 &&
	       !(ctx.fm.second_waiting && first_holds_second_lock(&ctx.fm)) &&
	       !ctx.fm.second_finished)
		wait_for_change(&ctx.fm, &deadline);
	ctx.fm.release = true;
	ctx.fm.block_reads = false;
	assert(!pthread_cond_broadcast(&ctx.fm.changed));
	mutex_unlock(&ctx.fm.hw_lock);
	assert(!pthread_join(one, NULL));
	assert(!pthread_join(two, NULL));
	printf("  idle-check race: idle reads=%u, writes=%u, reassertions=%u\n",
	       ctx.fm.idle_reads, ctx.fm.writes, ctx.fm.duplicates);
	report("idle-check race",
	       !ctx.fm.duplicates &&
	       (a.result < 0 || ctx.fm.completed_publication >= 1) &&
	       (b.result < 0 ||
		ctx.fm.completed_publication >= ctx.fm.publication));
	destroy_context(&ctx);
}

static void test_serialization(enum writer first, enum writer second, bool control)
{
	struct context ctx;
	struct call a = { .ctx = &ctx, .writer = first, .control = control };
	struct call b = {
		.ctx = &ctx, .writer = second, .second = true, .control = control,
	};
	struct timespec deadline;
	pthread_t one, two;
	unsigned int writes_while_pending;
	int rc;

	if (first == EXTC && diagnostic_disabled) {
		puts("  fe_extc cannot start a SYNC: diagnostic write disabled");
		return;
	}
	init_context(&ctx);
	ctx.fm.block_reads = true;
	assert(!pthread_create(&one, NULL, run_writer, &a));
	mutex_lock(&ctx.fm.hw_lock);
	while (!ctx.fm.first_read)
		assert(!pthread_cond_wait(&ctx.fm.changed, &ctx.fm.hw_lock));
	mutex_unlock(&ctx.fm.hw_lock);
	assert(!pthread_create(&two, NULL, run_writer, &b));
	mutex_lock(&ctx.fm.hw_lock);
	while (!ctx.fm.second_started)
		assert(!pthread_cond_wait(&ctx.fm.changed, &ctx.fm.hw_lock));
	assert(!clock_gettime(CLOCK_REALTIME, &deadline));
	deadline.tv_sec += 5;
	while (!ctx.fm.second_blocked && !ctx.fm.second_finished &&
	       ctx.fm.writes == 1) {
		rc = pthread_cond_timedwait(&ctx.fm.changed, &ctx.fm.hw_lock,
					    &deadline);
		if (rc == ETIMEDOUT) {
			fprintf(stderr, "second writer made no observable progress\n");
			assert(rc != ETIMEDOUT);
		}
		assert(!rc);
	}
	writes_while_pending = ctx.fm.writes;
	ctx.fm.release = true;
	ctx.fm.block_reads = false;
	assert(!pthread_cond_broadcast(&ctx.fm.changed));
	mutex_unlock(&ctx.fm.hw_lock);
	assert(!pthread_join(one, NULL));
	assert(!pthread_join(two, NULL));
	assert(a.result == 0);
	printf("  %s%s + %s: pending writes=%u, total=%u, reassertions=%u\n",
	       control ? "harness-only serialization control: " : "",
	       writer_name(first), writer_name(second), writes_while_pending,
	       ctx.fm.writes, ctx.fm.duplicates);
	if (second == EXTC && diagnostic_disabled) {
		report("disabled diagnostic refuses while INV0 pending",
		       b.result < 0 && writes_while_pending == 1 &&
		       ctx.fm.writes == 1 && !ctx.fm.duplicates);
	} else if (control) {
		assert(b.result == 0 && writes_while_pending == 1 &&
		       ctx.fm.writes == 2);
		assert(!ctx.fm.duplicates);
		assert(ctx.fm.completed_publication >= ctx.fm.publication);
	} else {
		report("concurrent writers",
		       writes_while_pending == 1 && !ctx.fm.duplicates &&
		       ((b.result == 0 && ctx.fm.writes == 2) ||
			(b.result < 0 && ctx.fm.writes <= 2)));
		if (second != EXTC)
			report("later update covered or refused",
			       b.result < 0 ||
			       ctx.fm.completed_publication >= ctx.fm.publication);
	}
	destroy_context(&ctx);
}

static void test_timeout_and_retry(void)
{
	struct context ctx;
	u32 extc = 0;
	unsigned int writes, duplicates, injected;
	int rc;

	init_context(&ctx);
	ctx.fm.stuck = true;
	writes = ctx.fm.writes;
	rc = fman_pcd_ehash_sync(&ctx.table, &extc);
	printf("  pending timeout: writes=%u, reassertions=%u\n",
	       ctx.fm.writes - writes, ctx.fm.duplicates);
	report("timeout retry does not reassert pending INV0",
	       rc < 0 && ctx.fm.active &&
	       ctx.fm.writes == writes + 1 && !ctx.fm.duplicates);
	writes = ctx.fm.writes;
	duplicates = ctx.fm.duplicates;
	rc = fman_pcd_ehash_sync(&ctx.table, &extc);
	printf("  subsequent operation: writes=%u, reassertions=%u\n",
	       ctx.fm.writes - writes, ctx.fm.duplicates - duplicates);
	report("subsequent operation leaves pending INV0 alone",
	       rc < 0 && ctx.fm.writes == writes &&
	       ctx.fm.duplicates == duplicates);
	ctx.fm.stuck = false;
	ctx.fm.publication++;
	duplicates = ctx.fm.duplicates;
	rc = fman_pcd_ehash_sync(&ctx.table, &extc);
	report("late recovery covers later publication or refuses",
	       ctx.fm.duplicates == duplicates &&
	       (rc < 0 || (rc == 0 &&
		ctx.fm.completed_publication >= ctx.fm.publication)));
	destroy_context(&ctx);

	init_context(&ctx);
	ctx.fm.pending_polls = 100000;
	writes = ctx.fm.writes;
	duplicates = ctx.fm.duplicates;
	rc = fman_pcd_ehash_sync(&ctx.table, &extc);
	printf("  late completion: writes=%u, reassertions=%u\n",
	       ctx.fm.writes - writes, ctx.fm.duplicates - duplicates);
	report("late completion does not reassert pending INV0",
	       ctx.fm.writes == writes + 1 &&
	       ctx.fm.duplicates == duplicates &&
	       (rc < 0 || (rc == 0 &&
		ctx.fm.completed_publication >= ctx.fm.publication)));
	destroy_context(&ctx);

	init_context(&ctx);
	ehash_sync_inject = 2;
	writes = ctx.fm.writes;
	duplicates = ctx.fm.duplicates;
	injected = ehash_sync_injected;
	rc = fman_pcd_ehash_sync(&ctx.table, &extc);
	report("injected completed handshakes do not reassert pending INV0",
	       rc < 0 && ctx.fm.inv0_faulted &&
	       ehash_sync_inject == 1 && ehash_sync_injected == injected + 1 &&
	       ctx.fm.writes == writes + 1 &&
	       ctx.fm.duplicates == duplicates);
	ehash_sync_inject = 0;
	destroy_context(&ctx);
}

static void test_preexisting_pending(void)
{
	struct context ctx;
	int rc;

	for (enum writer writer = RX; writer <= EXTC; writer++) {
		init_context(&ctx);
		ctx.fm.active = true;
		ctx.fm.stuck = true;
		rc = invoke(&ctx, writer);
		printf("  %s with INV0 already pending: rc=%d, writes=%u, reassertions=%u\n",
		       writer_name(writer), rc, ctx.fm.writes, ctx.fm.duplicates);
		report("pre-existing pending request",
		       (rc < 0 || ((writer == F177_DEBUG ||
				    writer == F177_PRODUCTION) &&
				   ctx.fm.inv0_faulted)) &&
		       !ctx.fm.writes && !ctx.fm.duplicates);
		if (writer == RX || writer == OH) {
			u32 nia = writer == RX ? ctx.rx_regs.rx.fmbm_rfpne :
						 ctx.oh_regs.oh.fmbm_ofpne;

			report("pending arm does not enable CC dispatch",
			       rc < 0 && !(nia & NIA_KG_CC_EN));
		}
		ehash_sync_inject = 0;
		destroy_context(&ctx);
	}
}

static void detect_diagnostic_state(void)
{
	struct context ctx;
	int rc;

	init_context(&ctx);
	rc = invoke(&ctx, EXTC);
	diagnostic_disabled = rc < 0 && ctx.fm.writes == 0;
	if (diagnostic_disabled)
		assert(!ctx.fm.writes && !ctx.fm.duplicates);
	else
		assert(rc == 0 && ctx.fm.writes == 1 && !ctx.fm.duplicates);
	destroy_context(&ctx);
}

int main(int argc, char **argv)
{
	bool characterize = argc == 2 && !strcmp(argv[1], "--characterize");

	assert(argc == 1 || characterize);
	detect_diagnostic_state();
	test_timeout_and_retry();
	test_preexisting_pending();
	test_idle_check_race();
	for (enum writer first = RX; first <= EXTC; first++) {
		if (first == EXTC && diagnostic_disabled)
			continue;
		for (enum writer second = RX; second <= EXTC; second++) {
			/* RX/OH first-port transitions are tested separately. */
			if (first <= OH && second <= OH)
				continue;
			test_serialization(first, second, false);
		}
	}
	test_serialization(RX, EHASH, true);
	printf("Global INV0 characterization: %u uncontained checks\n", unsafe_cases);
	return !characterize && unsafe_cases ? 1 : 0;
}
