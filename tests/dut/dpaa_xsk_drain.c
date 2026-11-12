// SPDX-License-Identifier: GPL-2.0
/* Run the DPAA AF_XDP BMan drain against kernel bman_acquire() semantics. */
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>

typedef uint8_t u8;
typedef uint64_t u64;
struct bman_pool { int unused; };
struct bm_buffer { uint64_t data; };

static unsigned int pool_free;
static int force_timeout_after = -1;
static unsigned int acquires;
static unsigned int released;

static inline u64 bm_buffer_get64(const struct bm_buffer *buf)
{
	return buf->data;
}

static inline void count_release(void *ctx, u64 addr)
{
	(void)ctx; (void)addr;
	released++;
}

/* Same contract as drivers/soc/fsl/qbman/bman.c: exact count or -ENOMEM. */
static int bman_acquire(struct bman_pool *pool, struct bm_buffer *bufs, u8 num)
{
	(void)pool; (void)bufs;
	acquires++;
	if (force_timeout_after >= 0 && acquires > (unsigned int)force_timeout_after)
		return -ETIMEDOUT;
	if (pool_free < num)
		return -ENOMEM;
	pool_free -= num;
	return num;
}

#include "drain.h"

static int run(unsigned int free_bufs, int timeout_after)
{
	struct bman_pool pool;

	pool_free = free_bufs;
	force_timeout_after = timeout_after;
	acquires = 0;
	released = 0;
	return DRAIN_CALL(&pool, count_release, NULL);
}

int main(void)
{
	static const unsigned int sizes[] = { 0, 1, 7, 8, 9, 15, 16, 4097, 39139 };
	unsigned int i;

	for (i = 0; i < sizeof(sizes) / sizeof(sizes[0]); i++) {
		int ret = run(sizes[i], -1);

		if (ret || pool_free || released != sizes[i]) {
			fprintf(stderr, "pool of %u: ret=%d left=%u released=%u\n",
				sizes[i], ret, pool_free, released);
			return 1;
		}
	}

	/* A timeout is not proof of an empty pool. */
	if (run(100, 3) != -EAGAIN || !pool_free) {
		fprintf(stderr, "timeout treated as drained\n");
		return 1;
	}
	return 0;
}
