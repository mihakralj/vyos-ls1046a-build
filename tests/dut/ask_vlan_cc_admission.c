/* Host fault tests for retained legacy VLAN CC owners. */
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define FMAN_CC_MAX_STATIC_KEYS 64
#define ask_pr_err(...) (++errors)

struct ask_vlan_cc_port {
	u32 hm_handles[FMAN_CC_MAX_STATIC_KEYS];
	u16 nkeys;
	bool installed;
};

static struct ask_vlan_cc_port ports[64];
static pthread_mutex_t ask_vlan_cc_lock = PTHREAD_MUTEX_INITIALIZER;
static int errors;

static void mutex_lock(pthread_mutex_t *lock) { assert(!pthread_mutex_lock(lock)); }
static void mutex_unlock(pthread_mutex_t *lock) { assert(!pthread_mutex_unlock(lock)); }

#include "vlan_cc_admission_body.h"

int main(void)
{
	ports[0x10].installed = true;
	ports[0x10].nkeys = 1;
	ports[0x10].hm_handles[0] = 0x1234;
	assert(ask_vlan_cc_teardown_port(0x10) == -EOPNOTSUPP);
	assert(ports[0x10].installed && ports[0x10].nkeys == 1);
	assert(ports[0x10].hm_handles[0] == 0x1234);

	memset(&ports[0x10], 0, sizeof(ports[0x10]));
	ports[0x10].installed = true;
	assert(ask_vlan_cc_teardown_port(0x10) == -EOPNOTSUPP);
	assert(ports[0x10].installed);
	ports[0x10].installed = false;
	ports[0x10].nkeys = 1;
	assert(ask_vlan_cc_teardown_port(0x10) == -EOPNOTSUPP);
	assert(ports[0x10].nkeys == 1);
	ports[0x10].nkeys = 0;
	ports[0x10].hm_handles[FMAN_CC_MAX_STATIC_KEYS - 1] = 0x1234;
	assert(ask_vlan_cc_teardown_port(0x10) == -EOPNOTSUPP);
	assert(ports[0x10].hm_handles[FMAN_CC_MAX_STATIC_KEYS - 1] == 0x1234);
	assert(errors == 4);

	memset(&ports[0x10], 0, sizeof(ports[0x10]));
	assert(ask_vlan_cc_teardown_port(0x10) == 0);
	assert(ask_vlan_cc_teardown_port(64) == -EINVAL);
	puts("VLAN CC teardown retains unresolved legacy owners");
	return 0;
}
