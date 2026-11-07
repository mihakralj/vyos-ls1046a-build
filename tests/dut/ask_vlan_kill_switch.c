// SPDX-License-Identifier: GPL-2.0
/* Host fault injection for the extracted VLAN and bridge parameter setters. */
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

typedef uint8_t u8;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define THIS_MODULE NULL
#define ask_pr_err(...) ((void)0)
#define pr_err(...) ((void)0)
#define pr_warn(...) ((void)0)

struct kernel_param { void *arg; };
static bool ask_vlan_offload = true;
static bool ask_bridge_offload = true;
static bool ask_hw_ready = true;
static bool ask_vlan_disable_incomplete;
static bool ask_bridge_disable_incomplete;
static bool allow_module_get = true;
static bool bridge_paused;
static int module_refs, routed_calls, bridge_calls, bridge_kicks;
static int legacy_teardowns, legacy_reengages;
static int routed_result, bridge_result;
static int bridge_all_calls, bridge_all_result;
static u8 ask_hw_port_family[256];
static pthread_mutex_t param_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t test_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t test_cond = PTHREAD_COND_INITIALIZER;
static bool block_parse, parse_entered, release_parse, ready_started, ready_done;

static void kernel_param_lock(void *module)
{
	assert(!pthread_mutex_lock(&param_lock));
}

static void kernel_param_unlock(void *module)
{
	assert(!pthread_mutex_unlock(&param_lock));
}

static int kstrtobool(const char *val, bool *result)
{
	assert(!pthread_mutex_lock(&test_lock));
	if (block_parse) {
		parse_entered = true;
		pthread_cond_broadcast(&test_cond);
		while (!release_parse)
			assert(!pthread_cond_wait(&test_cond, &test_lock));
	}
	assert(!pthread_mutex_unlock(&test_lock));
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

static u8 ask_hw_port_family_get(u8 port) { return ask_hw_port_family[port]; }
static int ask_vlan_cc_teardown_port(u8 port) { legacy_teardowns++; return 0; }
static int ask_hw_fe_reengage(u8 port) { legacy_reengages++; return 0; }
static void ask_bridge_kick(void) { bridge_kicks++; }
static void ask_bridge_vlan_pause(void) { bridge_paused = true; }
static void ask_bridge_vlan_resume(void) { bridge_paused = false; bridge_kicks++; }

static int ask_flow_vlan_flush(void)
{
	assert(!ask_vlan_offload && bridge_paused);
	routed_calls++;
	return routed_result;
}

static int ask_bridge_vlan_flush(void)
{
	assert(!ask_vlan_offload && bridge_paused);
	bridge_calls++;
	return bridge_result;
}

static int ask_bridge_flush_all(void)
{
	assert(!ask_bridge_offload);
	bridge_all_calls++;
	return bridge_all_result;
}

#include "vlan_switch_body.h"

static void *disable_before_ready(void *arg)
{
	struct kernel_param *kp = arg;

	kernel_param_lock(THIS_MODULE);
	assert(!ask_vlan_offload_set("N", kp));
	kernel_param_unlock(THIS_MODULE);
	return NULL;
}

static void *publish_ready(void *arg)
{
	assert(!pthread_mutex_lock(&test_lock));
	ready_started = true;
	pthread_cond_broadcast(&test_cond);
	assert(!pthread_mutex_unlock(&test_lock));
	ask_hw_set_ready(true);
	assert(!pthread_mutex_lock(&test_lock));
	ready_done = true;
	pthread_cond_broadcast(&test_cond);
	assert(!pthread_mutex_unlock(&test_lock));
	return NULL;
}

int main(void)
{
	struct kernel_param kp = { .arg = &ask_vlan_offload };
	struct kernel_param bridge_kp = { .arg = &ask_bridge_offload };
	pthread_t writer, initializer;
	struct timespec deadline;
	int rc;

	/* A bridge kill-switch failure must be returned and block re-enable. */
	bridge_all_result = -ETIMEDOUT;
	assert(ask_bridge_offload_set("N", &bridge_kp) == -ETIMEDOUT);
	assert(!ask_bridge_offload && ask_bridge_disable_incomplete);
	assert(bridge_all_calls == 1 && !module_refs);
	assert(ask_bridge_offload_set("Y", &bridge_kp) == -EBUSY);
	assert(!ask_bridge_offload && bridge_all_calls == 1);
	bridge_all_result = 0;
	assert(ask_bridge_offload_set("N", &bridge_kp) == 0);
	assert(!ask_bridge_disable_incomplete && bridge_all_calls == 2);
	assert(ask_bridge_offload_set("Y", &bridge_kp) == 0 && ask_bridge_offload);
	assert(bridge_all_calls == 2 && !module_refs);
	assert(ask_bridge_offload_set("invalid", &bridge_kp) == -EINVAL);
	assert(ask_bridge_offload && bridge_all_calls == 2 && !module_refs);
	allow_module_get = false;
	assert(ask_bridge_offload_set("N", &bridge_kp) == -ENODEV);
	assert(ask_bridge_offload && bridge_all_calls == 2);
	allow_module_get = true;
	ask_hw_ready = false;
	assert(ask_bridge_offload_set("N", &bridge_kp) == 0);
	assert(!ask_bridge_offload && bridge_all_calls == 2);
	assert(ask_bridge_offload_set(NULL, &bridge_kp) == 0 && ask_bridge_offload);
	ask_hw_ready = true;

	ask_hw_port_family[0x10] = 1;
	rc = ask_vlan_offload_set("N", &kp);
	assert(rc == 0 && !ask_vlan_offload && !bridge_paused);
	assert(routed_calls == 1 && bridge_calls == 1);
	assert(!legacy_teardowns && !legacy_reengages && !module_refs);

	assert(ask_vlan_offload_set("Y", &kp) == 0 && ask_vlan_offload);
	routed_result = -ENOMEM;
	assert(ask_vlan_offload_set("N", &kp) == -ENOMEM);
	assert(!ask_vlan_offload && bridge_calls == 1 && !module_refs && bridge_paused);
	assert(ask_vlan_offload_set("Y", &kp) == -EBUSY && !ask_vlan_offload);
	routed_result = 0;
	assert(ask_vlan_offload_set("N", &kp) == 0);
	assert(!bridge_paused);
	assert(ask_vlan_offload_set("Y", &kp) == 0 && ask_vlan_offload);

	bridge_result = -ETIMEDOUT;
	assert(ask_vlan_offload_set("N", &kp) == -ETIMEDOUT);
	assert(!ask_vlan_offload && bridge_paused);
	assert(ask_vlan_offload_set("Y", &kp) == -EBUSY && !ask_vlan_offload);
	bridge_result = 0;
	assert(ask_vlan_offload_set("N", &kp) == 0);
	assert(ask_vlan_offload_set("Y", &kp) == 0 && ask_vlan_offload);

	assert(ask_vlan_offload_set("invalid", &kp) == -EINVAL);
	assert(ask_vlan_offload && !module_refs);
	ask_hw_ready = false;
	rc = routed_calls;
	assert(ask_vlan_offload_set("N", &kp) == 0 && !ask_vlan_offload);
	assert(routed_calls == rc);
	assert(ask_vlan_offload_set(NULL, &kp) == 0 && ask_vlan_offload);
	ask_hw_ready = true;
	allow_module_get = false;
	assert(ask_vlan_offload_set("N", &kp) == -ENODEV);
	assert(ask_vlan_offload && !module_refs);

	/* Readiness cannot overtake a parameter write that began during init. */
	allow_module_get = true;
	ask_hw_ready = false;
	block_parse = true;
	assert(!pthread_create(&writer, NULL, disable_before_ready, &kp));
	assert(!pthread_mutex_lock(&test_lock));
	while (!parse_entered)
		assert(!pthread_cond_wait(&test_cond, &test_lock));
	assert(!pthread_mutex_unlock(&test_lock));
	assert(!pthread_create(&initializer, NULL, publish_ready, NULL));
	assert(!pthread_mutex_lock(&test_lock));
	while (!ready_started)
		assert(!pthread_cond_wait(&test_cond, &test_lock));
	assert(!clock_gettime(CLOCK_REALTIME, &deadline));
	deadline.tv_sec++;
	while (!ready_done) {
		rc = pthread_cond_timedwait(&test_cond, &test_lock, &deadline);
		if (rc == ETIMEDOUT)
			break;
		assert(!rc);
	}
	assert(!ready_done);
	release_parse = true;
	pthread_cond_broadcast(&test_cond);
	assert(!pthread_mutex_unlock(&test_lock));
	assert(!pthread_join(writer, NULL));
	assert(!pthread_join(initializer, NULL));
	assert(ask_hw_ready && !ask_vlan_offload && !bridge_paused && !module_refs);
	puts("VLAN switch: synchronous withdrawal failures are visible and retryable");
	return 0;
}
