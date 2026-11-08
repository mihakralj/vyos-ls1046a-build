// SPDX-License-Identifier: GPL-2.0
/* Host mocks for the actual WGFP sink statistics callback. */
#include <assert.h>
#include <errno.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef unsigned long long u64;

#define READ_ONCE(x) (x)
#define rcu_access_pointer(x) (x)
#define QM_MCR_NP_STATE_MASK 0x07
#define qm_mcr_frm_cnt_mask 0x00ffffffU
#define be32_to_cpu(x) __builtin_bswap32(x)

struct qman_fq { unsigned int fqid; };
struct wgfp_sink {
	struct qman_fq fq;
	int cpu;
	bool dist;
	u64 frames, bytes, errs, sg, irqs, polls;
};
struct wgfp_port {
	int ifindex;
	unsigned int nsinks;
	bool dist;
	struct wgfp_sink dist_sink;
	struct wgfp_sink sink[4];
};
struct qm_mcr_queryfq_np {
	uint8_t state;
	uint32_t frm_cnt, byte_cnt;
};
struct seq_file {
	char buf[2048];
	size_t len;
};

static struct wgfp_sink wgfp_global[2];
static unsigned int wgfp_nglobal = 2;
static struct wgfp_port wgfp_ports[2];
static unsigned int wgfp_nports;
static unsigned int wgfp_touch = 2, wgfp_per_port = 2, wgfp_fanout = 32;
static bool wgfp_pool_failed;
static int wgfp_lock, wgfp_neg;
static void *wgfp_ops;
static u64 wgfp_xmit_ok, wgfp_xmit_fail, wgfp_xmit_ern;
static u64 wgfp_rx_enq_fail, wgfp_rx_ern;

static void mutex_lock(int *lock) { (void)lock; }
static void mutex_unlock(int *lock) { (void)lock; }
static u64 dpaa_wgfp_stat_sum(const u64 *value) { return *value; }
static u64 atomic64_read(const u64 *value) { return *value; }
static unsigned int qman_fq_fqid(struct qman_fq *fq) { return fq->fqid; }

static int qman_query_fq_np(struct qman_fq *fq, struct qm_mcr_queryfq_np *np)
{
	if (fq->fqid == 0x2bf)
		return -ETIMEDOUT;
	np->state = 0x15;
	np->frm_cnt = 0x01000000;
	np->byte_cnt = 0x40000000;
	return 0;
}

static void seq_printf(struct seq_file *m, const char *fmt, ...)
	__attribute__((format(printf, 2, 3)));
static void seq_printf(struct seq_file *m, const char *fmt, ...)
{
	va_list ap;
	int n;

	va_start(ap, fmt);
	n = vsnprintf(m->buf + m->len, sizeof(m->buf) - m->len, fmt, ap);
	va_end(ap);
	assert(n >= 0 && (size_t)n < sizeof(m->buf) - m->len);
	m->len += (size_t)n;
}

static void seq_putc(struct seq_file *m, char c)
{
	assert(m->len + 1 < sizeof(m->buf));
	m->buf[m->len++] = c;
	m->buf[m->len] = '\0';
}

#include "wgfp_stats_body.h"

int main(void)
{
	struct seq_file output = {};

	wgfp_global[0].fq.fqid = 0x2be;
	wgfp_global[1].fq.fqid = 0x2bf;
	wgfp_global[1].cpu = 1;
	assert(wgfp_stats_show(&output, NULL) == 0);
	assert(strstr(output.buf, "frm_cnt_encoding=host\n"));
	assert(strstr(output.buf,
		      "sink0 port=0 kind=global cpu=0 fqid=0x2be frames=0 bytes=0 errs=0 sg=0 irqs=0 polls=0"
		      " state=0x05 frm_cnt=1 byte_cnt=64 frm_cnt_raw=0x01000000 byte_cnt_raw=0x40000000\n"));
	assert(strstr(output.buf,
		      "sink1 port=0 kind=global cpu=1 fqid=0x2bf frames=0 bytes=0 errs=0 sg=0 irqs=0 polls=0"
		      " query_err=-110\n"));
	return 0;
}
