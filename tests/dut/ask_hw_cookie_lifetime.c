/* Host mocks for the production ASK cookie getter and immediate free. */
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>

typedef uint32_t u32;

struct ask_hw_flow_cookie { u32 sink_fqid; };
struct xarray {
	pthread_mutex_t lock;
	struct ask_hw_flow_cookie *entry;
};
struct ask_hw_pcd { struct xarray flow_cookies; };

static struct ask_hw_pcd hw = {
	.flow_cookies.lock = PTHREAD_MUTEX_INITIALIZER,
};
static struct ask_hw_pcd *hw_ptr = &hw;
static void *ask_hw_cookie_cache;
static pthread_mutex_t event_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t event_cond = PTHREAD_COND_INITIALIZER;
static bool pause_lookup, captured, probe_done;

#define EXPORT_SYMBOL_GPL(name)
#define READ_ONCE(value) (value)
#define rcu_read_lock() do { } while (0)
#define rcu_read_unlock() do { } while (0)
#define xa_lock_irqsave(xa, flags) do {                                    \
	(flags) = 0;                                                       \
	assert(!pthread_mutex_lock(&(xa)->lock));                          \
} while (0)
#define xa_unlock_irqrestore(xa, flags) do {                               \
	(void)(flags);                                                      \
	assert(!pthread_mutex_unlock(&(xa)->lock));                        \
} while (0)

static struct ask_hw_pcd *ask_hw_pcd_get(void)
{
	return hw_ptr;
}

static void *xa_load(struct xarray *xa, u32 index)
{
	struct ask_hw_flow_cookie *entry = index == 1 ? xa->entry : NULL;

	if (pause_lookup && entry) {
		assert(!pthread_mutex_lock(&event_lock));
		captured = true;
		assert(!pthread_cond_broadcast(&event_cond));
		while (!probe_done)
			assert(!pthread_cond_wait(&event_cond, &event_lock));
		assert(!pthread_mutex_unlock(&event_lock));
	}
	return entry;
}

static void *xa_erase(struct xarray *xa, u32 index)
{
	struct ask_hw_flow_cookie *entry;

	assert(!pthread_mutex_lock(&xa->lock));
	entry = index == 1 ? xa->entry : NULL;
	if (index == 1)
		xa->entry = NULL;
	assert(!pthread_mutex_unlock(&xa->lock));
	return entry;
}

static void kfree(void *p)
{
	if (p)
		((struct ask_hw_flow_cookie *)p)->sink_fqid = 0;
}

static void kmem_cache_free(void *cache, void *p)
{
	(void)cache;
	kfree(p);
}

#include "ask_hw_cookie_body.h"

struct lookup_result { u32 fqid; int rc; };

static void *lookup_thread(void *arg)
{
	struct lookup_result *result = arg;

	result->rc = ask_hw_flow_get_sink_fqid(1, &result->fqid);
	return NULL;
}

static void *remove_thread(void *arg)
{
	int rc;

	(void)arg;
	assert(!pthread_mutex_lock(&event_lock));
	while (!captured)
		assert(!pthread_cond_wait(&event_cond, &event_lock));
	assert(!pthread_mutex_unlock(&event_lock));

	rc = pthread_mutex_trylock(&hw.flow_cookies.lock);
	assert(rc == 0 || rc == EBUSY);
	if (!rc) {
		assert(!pthread_mutex_unlock(&hw.flow_cookies.lock));
		ask_hw_cookie_free(&hw, 1);
	}
	assert(!pthread_mutex_lock(&event_lock));
	probe_done = true;
	assert(!pthread_cond_broadcast(&event_cond));
	assert(!pthread_mutex_unlock(&event_lock));
	if (rc == EBUSY)
		ask_hw_cookie_free(&hw, 1);
	return NULL;
}

int main(void)
{
	struct ask_hw_flow_cookie entry = { .sink_fqid = 0x1234 };
	struct lookup_result result = { 0 };
	pthread_t reader, remover;
	u32 fqid = 99;

	assert(ask_hw_flow_get_sink_fqid(0, &fqid) == -EINVAL);
	assert(ask_hw_flow_get_sink_fqid(1, NULL) == -EINVAL);
	assert(ask_hw_flow_get_sink_fqid(1, &fqid) == -ENOENT && !fqid);
	hw_ptr = NULL;
	assert(ask_hw_flow_get_sink_fqid(1, &fqid) == -EINVAL);
	hw_ptr = &hw;

	hw.flow_cookies.entry = &entry;
	pause_lookup = true;
	assert(!pthread_create(&reader, NULL, lookup_thread, &result));
	assert(!pthread_create(&remover, NULL, remove_thread, NULL));
	assert(!pthread_join(reader, NULL));
	assert(!pthread_join(remover, NULL));
	assert(result.rc == 0 && result.fqid == 0x1234);
	assert(!entry.sink_fqid && !hw.flow_cookies.entry);
	return 0;
}
