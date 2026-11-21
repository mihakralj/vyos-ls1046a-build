// SPDX-License-Identifier: GPL-2.0
/* Host fault-injection tests for nat44_offload, nat66_offload, and ipv6_hw_frag kill switches. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;

#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define THIS_MODULE NULL
#define EXPORT_SYMBOL_GPL(x)
#define pr_info_once(...) ((void)0)
#define ask_pr_err(...) ((void)0)
#define pr_err(...) ((void)0)
#define pr_warn(...) ((void)0)
#define module_param_cb(name, ops, arg, perm)
#define MODULE_PARM_DESC(name, desc)

struct kernel_param { void *arg; };
struct kernel_param_ops {
	int (*set)(const char *val, const struct kernel_param *kp);
	int (*get)(char *buffer, const struct kernel_param *kp);
};
static int param_get_bool(char *buffer, const struct kernel_param *kp)
{
	(void)buffer; (void)kp; return 0;
}

static int kstrtobool(const char *val, bool *result)
{
	if (!strcmp(val, "Y") || !strcmp(val, "1"))
		*result = true;
	else if (!strcmp(val, "N") || !strcmp(val, "0"))
		*result = false;
	else
		return -EINVAL;
	return 0;
}

static bool allow_module_get = true;
static int module_refs;
static bool ask_hw_ready = true;
static int nat44_flush_calls, nat66_flush_calls, v6_frag_flush_calls;
static int nat44_flush_rc, nat66_flush_rc, v6_frag_flush_rc;
static bool flow_table_present = true;

struct mutex { bool held; };
static struct mutex ask_killswitch_mutex;
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = true; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = false; }

static bool try_module_get(void *m)
{
	(void)m;
	if (!allow_module_get)
		return false;
	module_refs++;
	return true;
}
static void module_put(void *m)
{
	(void)m;
	assert(module_refs == 1);
	module_refs--;
}
static void *ask_flow_default_table(void)
{
	return flow_table_present ? (void *)1 : NULL;
}
static bool ask_hw_is_ready(void) { return ask_hw_ready; }

static int ask_flow_nat44_flush(void)
{
	nat44_flush_calls++;
	return nat44_flush_rc;
}
static int ask_flow_nat66_flush(void)
{
	nat66_flush_calls++;
	return nat66_flush_rc;
}
static int ask_flow_v6_frag_flush_sync(void)
{
	v6_frag_flush_calls++;
	return v6_frag_flush_rc;
}

#include "nat_frag_switch_body.h"

static void check_switch(int (*setter)(const char *, const struct kernel_param *),
			 bool *state, bool *incomplete,
			 int *flush_calls, int *flush_rc, bool uses_table)
{
	struct kernel_param kp = { .arg = state };

	/* Normal disable -> flushes synchronously, re-enable succeeds. */
	*flush_rc = 0;
	assert(setter("0", &kp) == 0);
	assert(!*state && !*incomplete && *flush_calls == 1 && !module_refs);
	assert(setter("1", &kp) == 0 && *state && *flush_calls == 1);

	/* Failed withdrawal -> returns error, blocks re-enable with -EBUSY. */
	*flush_rc = -ETIMEDOUT;
	assert(setter("0", &kp) == -ETIMEDOUT);
	assert(!*state && *incomplete && *flush_calls == 2 && !module_refs);
	assert(setter("1", &kp) == -EBUSY && !*state && *flush_calls == 2);

	/* Retry disable succeeds -> clears incomplete, re-enable works again. */
	*flush_rc = 0;
	assert(setter("0", &kp) == 0);
	assert(!*state && !*incomplete && *flush_calls == 3);
	assert(setter("1", &kp) == 0 && *state);

	/* Invalid value -> -EINVAL, state unchanged, no flush. */
	assert(setter("bad", &kp) == -EINVAL && *state && *flush_calls == 3 && !module_refs);

	/* Module unloading -> -ENODEV. */
	allow_module_get = false;
	assert(setter("0", &kp) == -ENODEV && *state && *flush_calls == 3);
	allow_module_get = true;

	/* Before ready -> toggles without flushing (even if flow table exists). */
	(void)uses_table;
	ask_hw_ready = false;
	assert(setter("0", &kp) == 0 && !*state && *flush_calls == 3);
	assert(setter(NULL, &kp) == 0 && *state && *flush_calls == 3);
	ask_hw_ready = true;
}

int main(void)
{
	check_switch(ask_nat44_offload_set, &ask_nat44_offload,
		     &ask_nat44_disable_incomplete,
		     &nat44_flush_calls, &nat44_flush_rc, false);
	check_switch(ask_nat66_offload_set, &ask_nat66_offload,
		     &ask_nat66_disable_incomplete,
		     &nat66_flush_calls, &nat66_flush_rc, false);
	/* A1: ipv6_hw_frag defaults OFF (RFC 8200 safe); opt in before testing disable path. */
	assert(!ask_ipv6_hw_frag);
	{
		struct kernel_param kp = { .arg = &ask_ipv6_hw_frag };
		assert(ask_ipv6_hw_frag_set("1", &kp) == 0 && ask_ipv6_hw_frag);
	}
	check_switch(ask_ipv6_hw_frag_set, &ask_ipv6_hw_frag,
		     &ask_ipv6_hw_frag_disable_incomplete,
		     &v6_frag_flush_calls, &v6_frag_flush_rc, true);
	assert(!ask_killswitch_mutex.held);
	puts("ask_nat_frag_kill_switch: OK");
	return 0;
}
