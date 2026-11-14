/* Host harness for ask_flow_set_hw_stats() (kernel/ask/oot-modules/ask/ask_flow.c), included from
 * ask_hw_stats_body.h (extracted by tests/ask_hw_stats_baseline.py). With the F-315 stats cache a poll
 * can read a value up to ~2 s older than a previous direct read: such a read must report no change and
 * must not lower the baseline, or the next fresh read counts the gap twice. One record per token, so a
 * genuine counter reset under one flow cannot happen. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint64_t u64;
#define EXPORT_SYMBOL_GPL(x)
struct u64_stats_sync { int x; };
static void u64_stats_update_begin(struct u64_stats_sync *s) { (void)s; }
static void u64_stats_update_end(struct u64_stats_sync *s) { (void)s; }
static u64 now_ns = 1000;
static u64 ktime_get_ns(void) { return now_ns; }

struct ask_flow_stats { u64 packets, bytes, last_seen_ns; struct u64_stats_sync syncp; };
struct ask_flow { struct ask_flow_stats stats; u64 hw_reported_packets, hw_reported_bytes; };

#include "ask_hw_stats_body.h"

int main(void)
{
	struct ask_flow f;
	u64 dp, db;

	memset(&f, 0, sizeof(f));
	ask_flow_set_hw_stats(&f, 100, 14000, &dp, &db);
	assert(dp == 100 && db == 14000 && f.stats.last_seen_ns == 1000);
	now_ns = 2000;
	ask_flow_set_hw_stats(&f, 250, 35000, &dp, &db);
	assert(dp == 150 && db == 21000);

	/* stale cached read below the baseline: no change, baseline and totals kept */
	now_ns = 3000;
	ask_flow_set_hw_stats(&f, 200, 28000, &dp, &db);
	assert(dp == 0 && db == 0);
	assert(f.hw_reported_packets == 250 && f.hw_reported_bytes == 35000);
	assert(f.stats.packets == 250 && f.stats.bytes == 35000 && f.stats.last_seen_ns == 2000);

	/* the next fresh read counts only what is new since 250/35000 */
	ask_flow_set_hw_stats(&f, 300, 42000, &dp, &db);
	assert(dp == 50 && db == 7000 && f.stats.packets == 300);

	/* packets and bytes from different passes: each counter monotonic on its own */
	ask_flow_set_hw_stats(&f, 310, 41000, &dp, &db);
	assert(dp == 10 && db == 0 && f.stats.bytes == 42000);

	puts("ask_hw_stats_baseline: OK");
	return 0;
}
