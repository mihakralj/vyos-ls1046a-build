// SPDX-License-Identifier: GPL-2.0
/* Host harness for the actual per-CPU statistics helpers. */
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>

typedef uint64_t u64;
typedef _Atomic uint64_t atomic64_t;

#define __percpu
#define CPU_COUNT 4
#define ITERATIONS 10000
#define get_cpu_ptr(stat) pin_local(stat)
#define put_cpu_ptr(stat) unpin_local(stat)
#define per_cpu_ptr(stat, cpu) cpu_counter(stat, cpu)
#define for_each_possible_cpu(cpu) for ((cpu) = 0; (cpu) < CPU_COUNT; (cpu)++)
#define atomic64_inc(stat) atomic_fetch_add(stat, 1)
#define atomic64_read(stat) atomic_load(stat)

static atomic64_t marker, slots[CPU_COUNT];
static _Thread_local int current_cpu, pin_depth;

static atomic64_t *cpu_counter(const atomic64_t *stat, int cpu)
{
	assert(stat == &marker && cpu >= 0 && cpu < CPU_COUNT);
	return &slots[cpu];
}

static atomic64_t *pin_local(const atomic64_t *stat)
{
	pin_depth++;
	return cpu_counter(stat, current_cpu);
}

static void unpin_local(const atomic64_t *stat)
{
	assert(stat == &marker && pin_depth == 1);
	pin_depth--;
}

#include "wgfp_stats_body.h"

static void *increment(void *arg)
{
	current_cpu = (int)(intptr_t)arg;
	for (int i = 0; i < ITERATIONS; i++)
		dpaa_wgfp_stat_inc(&marker);
	assert(pin_depth == 0);
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
		assert(atomic_load(&slots[i]) == ITERATIONS);
	return 0;
}
