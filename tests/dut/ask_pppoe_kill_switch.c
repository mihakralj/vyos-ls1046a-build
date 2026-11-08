// SPDX-License-Identifier: GPL-2.0
/* Host fault injection for the extracted PPPoE parameter setter. */
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define THIS_MODULE NULL
#define ask_pr_err(...) ((void)0)

struct kernel_param { void *arg; };
static bool ask_pppoe_offload = true, ask_hw_ready = true;
static bool ask_pppoe_disable_incomplete, allow_module_get = true;
static int module_refs, flush_calls, async_calls, flush_result;
static pthread_mutex_t param_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t event_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t event_cond = PTHREAD_COND_INITIALIZER;
static bool block_parse, parse_entered, release_parse;
static bool watch_ready_lock, ready_contended, ready_done;

static void kernel_param_lock(void *module)
{
	int rc;

	if (watch_ready_lock) {
		rc = pthread_mutex_trylock(&param_lock);
		if (rc == EBUSY) {
			assert(!pthread_mutex_lock(&event_lock));
			ready_contended = true;
			assert(!pthread_cond_broadcast(&event_cond));
			assert(!pthread_mutex_unlock(&event_lock));
			rc = pthread_mutex_lock(&param_lock);
		}
	} else {
		rc = pthread_mutex_lock(&param_lock);
	}
	assert(!rc);
}

static void kernel_param_unlock(void *module)
{
	assert(!pthread_mutex_unlock(&param_lock));
}

static int kstrtobool(const char *val, bool *result)
{
	assert(!pthread_mutex_lock(&event_lock));
	if (block_parse) {
		parse_entered = true;
		assert(!pthread_cond_broadcast(&event_cond));
		while (!release_parse)
			assert(!pthread_cond_wait(&event_cond, &event_lock));
	}
	assert(!pthread_mutex_unlock(&event_lock));
	if (!strcmp(val, "Y") || !strcmp(val, "1"))
		*result = true;
	else if (!strcmp(val, "N") || !strcmp(val, "0"))
		*result = false;
	else
		return -EINVAL;
	return 0;
}

static int param_set_bool(const char *val, const struct kernel_param *kp)
{
	bool value;
	int rc = kstrtobool(val ? val : "1", &value);

	if (!rc)
		*(bool *)kp->arg = value;
	return rc;
}

static bool try_module_get(void *module)
{
	if (!allow_module_get)
		return false;
	module_refs++;
	return true;
}

static void module_put(void *module)
{
	assert(module_refs == 1);
	module_refs--;
}

static void ask_flow_pppoe_flush(void)
{
	async_calls++;
}

static int ask_flow_pppoe_flush_sync(void)
{
	assert(!ask_pppoe_offload);
	flush_calls++;
	return flush_result;
}

#include "pppoe_switch_body.h"

static void *disable_before_ready(void *arg)
{
	struct kernel_param *kp = arg;

	kernel_param_lock(THIS_MODULE);
	assert(!ask_pppoe_offload_set("N", kp));
	kernel_param_unlock(THIS_MODULE);
	return NULL;
}

static void *publish_ready(void *arg)
{
	ask_hw_set_ready(true);
	assert(!pthread_mutex_lock(&event_lock));
	ready_done = true;
	assert(!pthread_cond_broadcast(&event_cond));
	assert(!pthread_mutex_unlock(&event_lock));
	return NULL;
}

int main(void)
{
	struct kernel_param kp = { .arg = &ask_pppoe_offload };
	pthread_t writer, initializer;
	int before;

	(void)ask_pppoe_disable_incomplete;
	assert(!ask_pppoe_offload_set("N", &kp));
	assert(!ask_pppoe_offload && flush_calls == 1 && !async_calls);
	assert(!module_refs);
	assert(!ask_pppoe_offload_set("Y", &kp) && ask_pppoe_offload);

	flush_result = -ENOMEM;
	assert(ask_pppoe_offload_set("N", &kp) == -ENOMEM);
	assert(!ask_pppoe_offload && !module_refs);
	assert(ask_pppoe_offload_set("Y", &kp) == -EBUSY);
	assert(!ask_pppoe_offload && flush_calls == 2);
	flush_result = 0;
	assert(!ask_pppoe_offload_set("N", &kp) && flush_calls == 3);
	assert(!ask_pppoe_offload_set("Y", &kp) && ask_pppoe_offload);

	flush_result = -ESTALE;
	assert(ask_pppoe_offload_set("N", &kp) == -ESTALE);
	assert(!ask_pppoe_offload);
	assert(ask_pppoe_offload_set("Y", &kp) == -EBUSY);
	flush_result = 0;
	assert(!ask_pppoe_offload_set("N", &kp));
	assert(!ask_pppoe_offload_set("Y", &kp) && ask_pppoe_offload);
	assert(ask_pppoe_offload_set("invalid", &kp) == -EINVAL);
	assert(ask_pppoe_offload && !module_refs);

	ask_hw_ready = false;
	before = flush_calls;
	assert(!ask_pppoe_offload_set("N", &kp) && !ask_pppoe_offload);
	assert(flush_calls == before);
	assert(!ask_pppoe_offload_set(NULL, &kp) && ask_pppoe_offload);
	ask_hw_ready = true;
	allow_module_get = false;
	assert(ask_pppoe_offload_set("N", &kp) == -ENODEV);
	assert(ask_pppoe_offload && !module_refs);

	allow_module_get = true;
	ask_hw_ready = false;
	block_parse = true;
	assert(!pthread_create(&writer, NULL, disable_before_ready, &kp));
	assert(!pthread_mutex_lock(&event_lock));
	while (!parse_entered)
		assert(!pthread_cond_wait(&event_cond, &event_lock));
	assert(!pthread_mutex_unlock(&event_lock));
	watch_ready_lock = true;
	assert(!pthread_create(&initializer, NULL, publish_ready, NULL));
	assert(!pthread_mutex_lock(&event_lock));
	while (!ready_contended)
		assert(!pthread_cond_wait(&event_cond, &event_lock));
	assert(!ready_done);
	release_parse = true;
	assert(!pthread_cond_broadcast(&event_cond));
	assert(!pthread_mutex_unlock(&event_lock));
	assert(!pthread_join(writer, NULL));
	assert(!pthread_join(initializer, NULL));
	assert(ask_hw_ready && !ask_pppoe_offload && !module_refs);
	assert(flush_calls == before && !async_calls);
	puts("PPPoE switch: withdrawal failures are visible and prevent re-enable");
	return 0;
}
