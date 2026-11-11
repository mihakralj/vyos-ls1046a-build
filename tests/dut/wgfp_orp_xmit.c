// SPDX-License-Identifier: GPL-2.0
/* Host harness for the actual wgfp_orp_xmit(): the ORP frame is enqueued at
 * once when the ring has room, with no clock read on that path (the
 * download path sends every forwarded frame through it). */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef long long ktime_t;
typedef int atomic64_t;

#define likely(x) (x)
#define NSEC_PER_MSEC 1000000LL
#define WGFP_ORP_WAIT_NS (5 * NSEC_PER_MSEC)
#define DEFINE_PER_CPU(type, name) type name
#define this_cpu_ptr(p) (p)

struct qm_fd { u32 addr; };
struct qman_fq { int unused; };
struct wgfp_sink;
struct dpaa_wgfp_frame { const struct qm_fd *fd; };

static struct qman_fq wgfp_ern_fq;
static u32 wgfp_eg_fqid[4] = { 11, 12, 13, 14 };
static atomic64_t wgfp_xmit_ok, wgfp_xmit_fail, wgfp_orp_wait_fail;
static int clock_reads, busy_left, enqueues, releases;
static u32 last_seq, last_orp, last_fqid;
static long long now;

static ktime_t ktime_get(void) { clock_reads++; now += 1000000; return now; }
static ktime_t ktime_add_ns(ktime_t t, long long ns) { return t + ns; }
static int ktime_before(ktime_t a, ktime_t b) { return a < b; }
static void cpu_relax(void) { }
static void atomic64_inc(atomic64_t *v) { (*v)++; }
static void dpaa_wgfp_stat_inc(atomic64_t *v) { (*v)++; }
static void dpaa_wgfp_fd_release(const struct qm_fd *fd) { (void)fd; releases++; }
static int qman_enqueue_orp_fqid(struct qman_fq *tag, u32 fqid, u32 orp, u16 seq,
				 bool hole, const struct qm_fd *fd)
{
	(void)tag; (void)fd;
	assert(!hole);
	if (busy_left) {
		busy_left--;
		return -16;
	}
	enqueues++;
	last_fqid = fqid; last_orp = orp; last_seq = seq;
	return 0;
}

#include "wgfp_orp_xmit_body.h"

static void reset(bool owed, int busy)
{
	wgfp_orp_ctx.owed = owed;
	wgfp_orp_ctx.orp_fqid = 0x77;
	wgfp_orp_ctx.seq = 513;
	busy_left = busy;
	clock_reads = enqueues = releases = 0;
	wgfp_xmit_ok = wgfp_xmit_fail = wgfp_orp_wait_fail = 0;
	now = 0;
}

int main(void)
{
	struct qm_fd in = { 1 }, out = { 2 };
	struct dpaa_wgfp_frame f = { &in };

	/* nothing owed: the plain path sends it */
	reset(false, 0);
	assert(!wgfp_orp_xmit(&f, 1, &out));
	assert(enqueues == 0 && clock_reads == 0);

	/* ring has room: one enqueue, no clock read */
	reset(true, 0);
	assert(wgfp_orp_xmit(&f, 2, &out));
	assert(enqueues == 1 && last_fqid == 13 && last_orp == 0x77 && last_seq == 513);
	assert(!wgfp_orp_ctx.owed && wgfp_xmit_ok == 1 && releases == 0);
	assert(clock_reads == 0);

	/* ring briefly full: retried within the bound, then sent */
	reset(true, 2);
	assert(wgfp_orp_xmit(&f, 0, &out));
	assert(enqueues == 1 && !wgfp_orp_ctx.owed && wgfp_xmit_ok == 1);

	/* ring full past the bound: buffer released, the slot left for the settle */
	reset(true, 1 << 20);
	assert(wgfp_orp_xmit(&f, 0, &out));
	assert(enqueues == 0 && releases == 1 && wgfp_orp_ctx.owed);
	assert(wgfp_xmit_fail == 1 && wgfp_orp_wait_fail == 1);
	assert(clock_reads <= 8);

	puts("wgfp_orp_xmit: ok");
	return 0;
}
