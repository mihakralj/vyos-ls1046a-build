// SPDX-License-Identifier: GPL-2.0
/* Host harness for the actual per-CPU statistics helpers. */
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>

typedef uint64_t u64;
typedef struct {
	_Atomic uint64_t counter;
} atomic64_t;

#define __percpu
#define CPU_COUNT 4
#define ITERATIONS 10000
#define this_cpu_ptr(stat) cpu_counter((stat), current_cpu)
#define per_cpu_ptr(stat, cpu) cpu_counter((stat), (cpu))
#define for_each_possible_cpu(cpu) for ((cpu) = 0; (cpu) < CPU_COUNT; (cpu)++)
#define READ_ONCE(x) atomic_load_explicit(&(x), memory_order_relaxed)
#define WRITE_ONCE(x, v) atomic_store_explicit(&(x), (v), memory_order_relaxed)

static atomic64_t marker, slots[CPU_COUNT];
static _Thread_local int current_cpu;

static atomic64_t *cpu_counter(const atomic64_t *stat, int cpu)
{
	assert(stat == &marker && cpu >= 0 && cpu < CPU_COUNT);
	return &slots[cpu];
}

#include "wgfp_stats_body.h"

static void *increment(void *arg)
{
	current_cpu = (int)(intptr_t)arg;
	for (int i = 0; i < ITERATIONS; i++)
		dpaa_wgfp_stat_inc(&marker);
	return NULL;
}

int main(void)
{
	pthread_t threads[CPU_COUNT];

	for (int i = 0; i < CPU_COUNT; i++)
		assert(pthread_create(&threads[i], NULL, increment,
				      (void *)(intptr_t)i) == 0);
	for (int i = 0; i < CPU_COUNT; i++)
		assert(pthread_join(threads[i], NULL) == 0);
	assert(dpaa_wgfp_stat_sum(&marker) == CPU_COUNT * ITERATIONS);
	for (int i = 0; i < CPU_COUNT; i++)
		assert(atomic_load(&slots[i].counter) == ITERATIONS);
	return 0;
}
