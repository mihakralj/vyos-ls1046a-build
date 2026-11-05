// SPDX-License-Identifier: GPL-2.0+
/*
 * fman_pcd_hc.c - opt-in FMan Host-Command SYNC probe (F-284)
 *
 * Lab tool for the ehash walker-quiescence gate. The vendor ASK stack on the
 * same 210.10.1 microcode releases unlinked ehash nodes only after an SDK
 * FmHcPcdSync() Host Command completes; ASK2 currently uses FMFP_EXTC[INV0].
 * This file reproduces the SDK HC transport so both barriers can be measured
 * on the DUT. Nothing runs at boot: the port, FQs and DMA buffer exist only
 * after an explicit "init" written to
 *
 *   /sys/kernel/debug/fman_pcd/<N>/hc
 *
 * Verbs:
 *   init [hwport] [chan]  claim an O/H port as the HC port (default 0x02,
 *                         channel 0x809 as in the vendor DT), create the
 *                         tx/conf/err FQs and program BMI/QMI like the SDK
 *                         flib (init_bmi_oh/init_qmi for E_FMAN_PORT_TYPE_HC)
 *   sync [n]              enqueue n HC SYNC frames (HCOR = GBL | 0x2) one at
 *                         a time and wait up to 1 s for each confirmation
 *   fini                  disable the port and retire the FQs
 *   clear                 reset counters
 *
 * A confirmation timeout latches "failed": the frame stays owned by hardware,
 * the buffer is never reused or freed, and fini is refused until reboot,
 * matching the vendor's "board reset required" HC policy.
 *
 * Port resources are committed through fman_set_port_params(), which has no
 * release path, so they are claimed once per boot. Do not use the same port
 * with the fman_port O/H test tool.
 */

#include <linux/debugfs.h>
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/err.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/mutex.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <linux/types.h>
#include <linux/uaccess.h>
#include <soc/fsl/qman.h>

#include "fman.h"
#include "fman_pcd_internal.h"

#define HC_BMI_BASE		0x00080000u
#define HC_PORT_STRIDE		0x1000u
#define HC_QMI_OFFSET		0x400u

/* struct fman_port_oh_bmi_regs offsets */
#define OH_OCFG			0x000
#define OH_OST			0x004
#define OH_ODA			0x008
#define OH_OICP			0x00c
#define OH_OFDNE		0x010
#define OH_OFNE			0x014
#define OH_OFCA			0x018
#define OH_OIM			0x02c
#define OH_OFP			0x030
#define OH_OFQID		0x060
#define OH_OEFQID		0x064
#define OH_OFENE		0x070
#define OH_OSTC			0x200
#define OH_OFRC			0x204
#define OH_OFDC			0x208
#define OH_OFLEDC		0x20c
#define OH_OFUFDC		0x210
#define OH_OFLDEC		0x21c

/* struct fman_port_qmi_regs offsets */
#define QMI_PNC			0x00
#define QMI_PNS			0x04
#define QMI_PNTS		0x08
#define QMI_PNEN		0x1c
#define QMI_PNETFC		0x20
#define QMI_PNDN		0x2c
#define QMI_PNDC		0x30
#define QMI_PNDTFC		0x34
#define QMI_PNDFDC		0x38
#define QMI_PNDCC		0x3c

#define HC_BMI_CFG_EN		0x80000000u
#define HC_BMI_ST_BSY		0x80000000u
#define HC_BMI_COUNTERS_EN	0x80000000u
#define HC_BMI_DMA_WR_OPT	0x00100000u
#define HC_BMI_CMD_OP_MR_DEF	0x000c0000u	/* MR_DEAS | MR_MA */
#define HC_BMI_FIFO_DEPTH_SHIFT	12
#define HC_BMI_FIFO_DEPTH_MASK	0x0000f000u

#define HC_QMI_CFG_EN		0x80000000u
#define HC_QMI_CFG_EN_COUNTERS	0x10000000u
#define HC_QMI_ST_DEQ_FD_BSY	0x20000000u
#define HC_QMI_DEQ_TYPE1	0x10000000u
#define HC_QMI_DEQ_SP_SHIFT	20
#define HC_QMI_DEQ_SP_MASK	0xfu
#define HC_QMI_DEQ_BYTE_CNT	0x400u		/* DEFAULT_PORT_deqByteCnt_1G */

#define HC_NIA_ENG_BMI		0x00500000u
#define HC_NIA_ENG_QMI_ENQ	0x00540000u
#define HC_NIA_ENG_QMI_DEQ	0x00580000u
#define HC_NIA_FM_CTL_AC_HC	0x0000000cu	/* NIA_ENG_FM_CTL is 0 */
#define HC_NIA_BMI_AC_RELEASE	0x000000c0u
#define HC_NIA_BMI_AC_FETCH	0x00000208u

#define HC_HCOR_GBL		0x20000000u
#define HC_HCOR_OPCODE_SYNC	0x2u
/* sizeof(t_HcFrame): 4 header words + the 31-word KG scheme union */
#define HC_FRAME_LEN		140u

#define HC_DEFAULT_HWPORT	0x02
#define HC_DEFAULT_CHANNEL	0x809
#define HC_TIMEOUT_NS		(1000ULL * NSEC_PER_MSEC)
#define HC_BUSY_POLL_NS		(1000ULL * NSEC_PER_USEC)
#define HC_DISABLE_POLLS	10000

enum hc_src {
	HC_SRC_NONE,
	HC_SRC_CONF,
	HC_SRC_ERR,
};

struct fman_pcd_hc_fq {
	struct qman_fq fq;
	struct fman_pcd_hc *hc;
	bool created;
	bool inited;
};

struct fman_pcd_hc {
	struct mutex lock;		/* serializes debugfs verbs */
	spinlock_t cb_lock;		/* in_flight/done vs DQRR callbacks */
	struct fman_pcd *pcd;
	struct fman *fman;
	void __iomem *bmi;
	void __iomem *qmi;
	u8 hwport;
	u16 chan;
	u16 conf_chan;
	bool claimed;
	bool enabled;
	bool failed;
	bool leaked_fqs;

	struct fman_pcd_hc_fq tx;
	struct fman_pcd_hc_fq conf;
	struct fman_pcd_hc_fq err;

	__be32 *buf;
	dma_addr_t dma;

	bool in_flight;
	bool done;
	u32 seq;
	u32 in_flight_seq;
	enum hc_src last_src;
	u32 last_status;
	u32 last_words[4];

	u64 sent;
	u64 confirmed;
	u64 err_frames;
	u64 late;
	u64 stray;
	u64 ern;
	u64 enq_err;
	u64 timeouts;
	u64 bad_seq;
	u64 lat_min_ns;
	u64 lat_max_ns;
	u64 lat_sum_ns;

	int np_rc;
	struct qm_mcr_queryfq_np np;
	bool np_valid;
};

static struct fman_pcd_hc *hc_state;

static u32 hc_bmi_rd(struct fman_pcd_hc *hc, u32 off)
{
	return ioread32be(hc->bmi + off);
}

static void hc_bmi_wr(struct fman_pcd_hc *hc, u32 off, u32 val)
{
	iowrite32be(val, hc->bmi + off);
}

static u32 hc_qmi_rd(struct fman_pcd_hc *hc, u32 off)
{
	return ioread32be(hc->qmi + off);
}

static void hc_qmi_wr(struct fman_pcd_hc *hc, u32 off, u32 val)
{
	iowrite32be(val, hc->qmi + off);
}

static enum qman_cb_dqrr_result hc_dqrr(struct fman_pcd_hc_fq *hfq,
					const struct qm_dqrr_entry *dq,
					enum hc_src src)
{
	struct fman_pcd_hc *hc = hfq->hc;
	unsigned long flags;
	u32 expected = 0, actual = 0;
	bool bad_seq = false;
	int i;

	spin_lock_irqsave(&hc->cb_lock, flags);
	if (qm_fd_addr(&dq->fd) != hc->dma) {
		hc->stray++;
	} else if (!hc->in_flight) {
		hc->late++;
	} else {
		dma_rmb();
		for (i = 0; i < ARRAY_SIZE(hc->last_words); i++)
			hc->last_words[i] = be32_to_cpu(hc->buf[i]);
		hc->last_status = be32_to_cpu(dq->fd.status);
		hc->last_src = src;
		if (hc->last_words[3] != hc->in_flight_seq) {
			expected = hc->in_flight_seq;
			actual = hc->last_words[3];
			hc->bad_seq++;
			WRITE_ONCE(hc->failed, true);
			bad_seq = true;
		} else if (src == HC_SRC_ERR || hc->last_status) {
			hc->err_frames++;
		} else {
			hc->confirmed++;
		}
		hc->in_flight = false;
		hc->done = true;
	}
	spin_unlock_irqrestore(&hc->cb_lock, flags);

	if (bad_seq)
		pr_err_ratelimited("fman_pcd_hc: sequence mismatch expected %08x got %08x; buffer retained until reboot\n",
				   expected, actual);

	return qman_cb_dqrr_consume;
}

static enum qman_cb_dqrr_result hc_conf_dqrr(struct qman_portal *qm,
					     struct qman_fq *fq,
					     const struct qm_dqrr_entry *dq,
					     bool sched_napi)
{
	return hc_dqrr(container_of(fq, struct fman_pcd_hc_fq, fq), dq,
		       HC_SRC_CONF);
}

static enum qman_cb_dqrr_result hc_err_dqrr(struct qman_portal *qm,
					    struct qman_fq *fq,
					    const struct qm_dqrr_entry *dq,
					    bool sched_napi)
{
	return hc_dqrr(container_of(fq, struct fman_pcd_hc_fq, fq), dq,
		       HC_SRC_ERR);
}

static void hc_ern(struct qman_portal *qm, struct qman_fq *fq,
		   const union qm_mr_entry *msg)
{
	struct fman_pcd_hc *hc = container_of(fq, struct fman_pcd_hc_fq, fq)->hc;
	unsigned long flags;

	spin_lock_irqsave(&hc->cb_lock, flags);
	hc->ern++;
	spin_unlock_irqrestore(&hc->cb_lock, flags);
}

static int hc_fq_setup(struct fman_pcd_hc *hc, struct fman_pcd_hc_fq *hfq,
		       u32 flags, u16 chan, int wq, qman_cb_dqrr dqrr)
{
	struct qm_mcc_initfq opts;
	int err;

	memset(&hfq->fq, 0, sizeof(hfq->fq));
	hfq->hc = hc;
	hfq->fq.cb.dqrr = dqrr;
	hfq->fq.cb.ern = hc_ern;
	err = qman_create_fq(0, flags | QMAN_FQ_FLAG_DYNAMIC_FQID, &hfq->fq);
	if (err)
		return err;
	hfq->created = true;

	memset(&opts, 0, sizeof(opts));
	opts.we_mask = cpu_to_be16(QM_INITFQ_WE_DESTWQ);
	qm_fqd_set_destwq(&opts.fqd, chan, wq);
	err = qman_init_fq(&hfq->fq, QMAN_INITFQ_FLAG_SCHED, &opts);
	if (err)
		return err;
	hfq->inited = true;
	return 0;
}

/* Returns false if the FQ could not be quiesced and was left allocated. */
static bool hc_fq_teardown(struct fman_pcd_hc_fq *hfq)
{
	int err = 0;

	if (!hfq->created)
		return true;
	if (hfq->inited) {
		err = qman_retire_fq(&hfq->fq, NULL);
		if (err >= 0)
			err = qman_oos_fq(&hfq->fq);
	}
	if (err < 0) {
		pr_warn("fman_pcd_hc: fqid 0x%x not quiesced (%d), leaking it\n",
			qman_fq_fqid(&hfq->fq), err);
		return false;
	}
	qman_destroy_fq(&hfq->fq);
	hfq->created = false;
	hfq->inited = false;
	return true;
}

static bool hc_fqs_teardown(struct fman_pcd_hc *hc)
{
	bool ok = true;

	ok &= hc_fq_teardown(&hc->tx);
	ok &= hc_fq_teardown(&hc->conf);
	ok &= hc_fq_teardown(&hc->err);
	if (!ok)
		hc->leaked_fqs = true;
	return ok;
}

static int hc_claim_port(struct fman_pcd_hc *hc)
{
	struct fman_port_init_params p = {};
	int err;

	if (hc->claimed)
		return 0;

	p.port_id = hc->hwport;
	p.port_type = FMAN_PORT_TYPE_OH;
	p.num_of_tasks = 1;			/* RM: one TNUM for atomic HC */
	p.num_of_open_dmas = 2;			/* SDK HC default */
	p.size_of_fifo = 10 * FMAN_BMI_FIFO_UNITS;	/* SDK HC default */
	p.deq_pipeline_depth = 2;		/* SDK HC on port != 0 */
	err = fman_set_port_params(hc->fman, &p);
	if (err)
		return err;
	hc->claimed = true;
	return 0;
}

static int hc_port_disable(struct fman_pcd_hc *hc)
{
	int i;

	hc_bmi_wr(hc, OH_OCFG, hc_bmi_rd(hc, OH_OCFG) & ~HC_BMI_CFG_EN);
	for (i = 0; i < HC_DISABLE_POLLS; i++) {
		if (!(hc_bmi_rd(hc, OH_OST) & HC_BMI_ST_BSY))
			break;
		udelay(1);
	}
	if (i == HC_DISABLE_POLLS)
		return -EBUSY;

	hc_qmi_wr(hc, QMI_PNC, hc_qmi_rd(hc, QMI_PNC) & ~HC_QMI_CFG_EN);
	for (i = 0; i < HC_DISABLE_POLLS; i++) {
		if (!(hc_qmi_rd(hc, QMI_PNS) & HC_QMI_ST_DEQ_FD_BSY))
			break;
		udelay(1);
	}
	if (i == HC_DISABLE_POLLS)
		return -EBUSY;

	hc->enabled = false;
	return 0;
}

static void hc_port_program(struct fman_pcd_hc *hc)
{
	u32 ofp;

	/* BMI, SDK init_bmi_oh() for E_FMAN_PORT_TYPE_HC */
	hc_bmi_wr(hc, OH_OCFG, 0);
	hc_bmi_wr(hc, OH_ODA, HC_BMI_DMA_WR_OPT);
	ofp = hc_bmi_rd(hc, OH_OFP) & ~HC_BMI_FIFO_DEPTH_MASK;
	ofp |= (2 - 1) << HC_BMI_FIFO_DEPTH_SHIFT;
	hc_bmi_wr(hc, OH_OFP, ofp);
	hc_bmi_wr(hc, OH_OICP, 0);
	hc_bmi_wr(hc, OH_OFCA, HC_BMI_CMD_OP_MR_DEF);
	hc_bmi_wr(hc, OH_OIM, 0);
	hc_bmi_wr(hc, OH_OFDNE, HC_NIA_ENG_QMI_DEQ);
	hc_bmi_wr(hc, OH_OFNE, HC_NIA_FM_CTL_AC_HC);
	hc_bmi_wr(hc, OH_OFENE, HC_NIA_ENG_QMI_ENQ);
	hc_bmi_wr(hc, OH_OFQID, qman_fq_fqid(&hc->conf.fq) & 0x00ffffff);
	hc_bmi_wr(hc, OH_OEFQID, qman_fq_fqid(&hc->err.fq) & 0x00ffffff);
	hc_bmi_wr(hc, OH_OSTC, HC_BMI_COUNTERS_EN);

	/* QMI, SDK init_qmi(): non-RX enqueue NIA, then dequeue setup */
	hc_qmi_wr(hc, QMI_PNC, HC_QMI_CFG_EN_COUNTERS);
	hc_qmi_wr(hc, QMI_PNEN, HC_NIA_ENG_BMI | HC_NIA_BMI_AC_RELEASE);
	hc_qmi_wr(hc, QMI_PNDN, HC_NIA_ENG_BMI | HC_NIA_BMI_AC_FETCH);
	hc_qmi_wr(hc, QMI_PNDC, HC_QMI_DEQ_TYPE1 |
		  ((hc->chan & HC_QMI_DEQ_SP_MASK) << HC_QMI_DEQ_SP_SHIFT) |
		  HC_QMI_DEQ_BYTE_CNT);

	/* SDK fman_port_enable(): QMI before BMI for every non-RX port */
	hc_qmi_wr(hc, QMI_PNC, hc_qmi_rd(hc, QMI_PNC) | HC_QMI_CFG_EN);
	hc_bmi_wr(hc, OH_OCFG, hc_bmi_rd(hc, OH_OCFG) | HC_BMI_CFG_EN);
	hc->enabled = true;
}

static int hc_init(struct fman_pcd_hc *hc, unsigned int hwport,
		   unsigned int chan)
{
	void __iomem *bmi;
	int err;

	if (READ_ONCE(hc->failed))
		return -EIO;
	if (hc->enabled)
		return -EALREADY;
	if (hc->leaked_fqs)
		return -EBUSY;
	if (hwport < 0x02 || hwport > 0x07)
		return -EINVAL;
	if (chan < 0x800 || chan > 0x80f)
		return -EINVAL;
	if (hc->claimed && hwport != hc->hwport)
		return -EBUSY;

	bmi = hc->fman->base_addr + HC_BMI_BASE + hwport * HC_PORT_STRIDE;
	if ((ioread32be(bmi + OH_OCFG) & HC_BMI_CFG_EN) ||
	    (ioread32be(bmi + HC_QMI_OFFSET + QMI_PNC) & HC_QMI_CFG_EN))
		return -EBUSY;

	hc->hwport = hwport;
	hc->chan = chan;
	hc->bmi = bmi;
	hc->qmi = bmi + HC_QMI_OFFSET;
	hc->conf_chan = qman_affine_channel(cpumask_first(qman_affine_cpus()));

	if (!hc->buf) {
		hc->buf = dma_alloc_coherent(hc->fman->dev, PAGE_SIZE, &hc->dma,
					     GFP_KERNEL);
		if (!hc->buf)
			return -ENOMEM;
	}

	err = hc_fq_setup(hc, &hc->conf, QMAN_FQ_FLAG_NO_ENQUEUE,
			  hc->conf_chan, 1, hc_conf_dqrr);
	if (!err)
		err = hc_fq_setup(hc, &hc->err, QMAN_FQ_FLAG_NO_ENQUEUE,
				  hc->conf_chan, 2, hc_err_dqrr);
	if (!err)
		err = hc_fq_setup(hc, &hc->tx, QMAN_FQ_FLAG_TO_DCPORTAL,
				  hc->chan, 0, NULL);
	if (!err)
		err = hc_claim_port(hc);
	if (err) {
		hc_fqs_teardown(hc);
		return err;
	}

	hc_port_program(hc);
	return 0;
}

static int hc_sync_one(struct fman_pcd_hc *hc)
{
	struct qm_fd fd;
	unsigned long flags;
	u64 start, elapsed;
	bool done, failed;
	int err;

	memset(hc->buf, 0, HC_FRAME_LEN);
	hc->buf[0] = cpu_to_be32(HC_HCOR_GBL | HC_HCOR_OPCODE_SYNC);
	hc->buf[3] = cpu_to_be32(hc->seq);

	memset(&fd, 0, sizeof(fd));
	qm_fd_addr_set64(&fd, hc->dma);
	qm_fd_set_contig(&fd, 0, HC_FRAME_LEN);

	spin_lock_irqsave(&hc->cb_lock, flags);
	hc->in_flight_seq = hc->seq;
	hc->in_flight = true;
	hc->done = false;
	hc->last_src = HC_SRC_NONE;
	spin_unlock_irqrestore(&hc->cb_lock, flags);

	dma_wmb();
	start = ktime_get_ns();
	err = qman_enqueue(&hc->tx.fq, &fd);
	if (err) {
		/* Rejected before acceptance: never an HC timeout. */
		spin_lock_irqsave(&hc->cb_lock, flags);
		hc->in_flight = false;
		hc->enq_err++;
		spin_unlock_irqrestore(&hc->cb_lock, flags);
		return err;
	}
	hc->sent++;
	hc->seq++;

	for (;;) {
		spin_lock_irqsave(&hc->cb_lock, flags);
		done = hc->done;
		if (!done && ktime_get_ns() - start >= HC_TIMEOUT_NS) {
			hc->in_flight = false;
			WRITE_ONCE(hc->failed, true);
			hc->timeouts++;
		}
		failed = hc->failed;
		spin_unlock_irqrestore(&hc->cb_lock, flags);
		if (done || failed)
			break;
		if (ktime_get_ns() - start < HC_BUSY_POLL_NS)
			udelay(1);
		else
			usleep_range(50, 100);
	}

	if (!done) {
		/* Distinguish "never dequeued" from "dequeued, no answer". */
		hc->np_rc = qman_query_fq_np(&hc->tx.fq, &hc->np);
		hc->np_valid = !hc->np_rc;
		return -ETIMEDOUT;
	}

	elapsed = ktime_get_ns() - start;
	if (!hc->lat_min_ns || elapsed < hc->lat_min_ns)
		hc->lat_min_ns = elapsed;
	if (elapsed > hc->lat_max_ns)
		hc->lat_max_ns = elapsed;
	hc->lat_sum_ns += elapsed;
	return failed || hc->last_src == HC_SRC_ERR || hc->last_status ?
		-EIO : 0;
}

static int hc_sync(struct fman_pcd_hc *hc, unsigned int n)
{
	unsigned int i;
	int err = 0;

	if (READ_ONCE(hc->failed))
		return -EIO;
	if (!hc->enabled)
		return -ENODEV;
	if (!n || n > 100000)
		return -EINVAL;

	for (i = 0; i < n && !err; i++) {
		err = hc_sync_one(hc);
		cond_resched();
	}
	return err;
}

static int hc_fini(struct fman_pcd_hc *hc)
{
	int err;

	if (READ_ONCE(hc->failed))
		return -EIO;
	if (!hc->enabled)
		return 0;
	err = hc_port_disable(hc);
	if (err)
		return err;
	return hc_fqs_teardown(hc) ? 0 : -EBUSY;
}

static void hc_clear(struct fman_pcd_hc *hc)
{
	unsigned long flags;

	spin_lock_irqsave(&hc->cb_lock, flags);
	hc->sent = 0;
	hc->confirmed = 0;
	hc->err_frames = 0;
	hc->late = 0;
	hc->stray = 0;
	hc->ern = 0;
	hc->enq_err = 0;
	hc->lat_min_ns = 0;
	hc->lat_max_ns = 0;
	hc->lat_sum_ns = 0;
	spin_unlock_irqrestore(&hc->cb_lock, flags);
}

static int hc_show(struct seq_file *s, void *unused)
{
	struct fman_pcd_hc *hc = s->private;
	u64 completed;

	mutex_lock(&hc->lock);
	seq_printf(s, "state %s%s%s\n",
		   READ_ONCE(hc->failed) ? "failed" : hc->enabled ? "enabled" : "idle",
		   hc->claimed ? " claimed" : "",
		   hc->leaked_fqs ? " leaked_fqs" : "");
	seq_printf(s, "hwport 0x%02x chan 0x%03x conf_chan 0x%03x\n",
		   hc->hwport, hc->chan, hc->conf_chan);
	if (hc->tx.created)
		seq_printf(s, "fqid tx 0x%x conf 0x%x err 0x%x\n",
			   qman_fq_fqid(&hc->tx.fq),
			   qman_fq_fqid(&hc->conf.fq),
			   qman_fq_fqid(&hc->err.fq));
	seq_printf(s, "sent %llu confirmed %llu err %llu timeouts %llu bad_seq %llu\n",
		   hc->sent, hc->confirmed, hc->err_frames, hc->timeouts,
		   hc->bad_seq);
	seq_printf(s, "late %llu stray %llu ern %llu enq_err %llu\n",
		   hc->late, hc->stray, hc->ern, hc->enq_err);
	completed = hc->confirmed + hc->err_frames;
	seq_printf(s, "lat_ns min %llu max %llu avg %llu\n",
		   hc->lat_min_ns, hc->lat_max_ns,
		   completed ? div64_u64(hc->lat_sum_ns, completed) : 0);
	seq_printf(s, "last src %s fd_status 0x%08x words %08x %08x %08x %08x\n",
		   hc->last_src == HC_SRC_CONF ? "conf" :
		   hc->last_src == HC_SRC_ERR ? "err" : "none",
		   hc->last_status, hc->last_words[0], hc->last_words[1],
		   hc->last_words[2], hc->last_words[3]);
	if (hc->np_rc)
		seq_printf(s, "tx_np rc %d\n", hc->np_rc);
	else if (hc->np_valid)
		seq_printf(s, "tx_np state 0x%02x frm_cnt %u byte_cnt %u\n",
			   hc->np.state,
			   be32_to_cpu((__force __be32)hc->np.frm_cnt) &
			   0x00ffffff,
			   be32_to_cpu((__force __be32)hc->np.byte_cnt));
	if (hc->bmi) {
		seq_printf(s, "bmi ocfg 0x%08x ost 0x%08x oda 0x%08x ofca 0x%08x ofp 0x%08x\n",
			   hc_bmi_rd(hc, OH_OCFG), hc_bmi_rd(hc, OH_OST),
			   hc_bmi_rd(hc, OH_ODA), hc_bmi_rd(hc, OH_OFCA),
			   hc_bmi_rd(hc, OH_OFP));
		seq_printf(s, "bmi ofdne 0x%08x ofne 0x%08x ofene 0x%08x ofqid 0x%08x oefqid 0x%08x\n",
			   hc_bmi_rd(hc, OH_OFDNE), hc_bmi_rd(hc, OH_OFNE),
			   hc_bmi_rd(hc, OH_OFENE), hc_bmi_rd(hc, OH_OFQID),
			   hc_bmi_rd(hc, OH_OEFQID));
		seq_printf(s, "bmi ofrc %u ofdc %u ofledc %u ofufdc %u ofldec %u\n",
			   hc_bmi_rd(hc, OH_OFRC), hc_bmi_rd(hc, OH_OFDC),
			   hc_bmi_rd(hc, OH_OFLEDC), hc_bmi_rd(hc, OH_OFUFDC),
			   hc_bmi_rd(hc, OH_OFLDEC));
		seq_printf(s, "qmi pnc 0x%08x pns 0x%08x pnts 0x%08x pnen 0x%08x pndn 0x%08x pndc 0x%08x\n",
			   hc_qmi_rd(hc, QMI_PNC), hc_qmi_rd(hc, QMI_PNS),
			   hc_qmi_rd(hc, QMI_PNTS), hc_qmi_rd(hc, QMI_PNEN),
			   hc_qmi_rd(hc, QMI_PNDN), hc_qmi_rd(hc, QMI_PNDC));
		seq_printf(s, "qmi pnetfc %u pndtfc %u pndfdc %u pndcc %u\n",
			   hc_qmi_rd(hc, QMI_PNETFC), hc_qmi_rd(hc, QMI_PNDTFC),
			   hc_qmi_rd(hc, QMI_PNDFDC), hc_qmi_rd(hc, QMI_PNDCC));
	}
	mutex_unlock(&hc->lock);
	return 0;
}

static int hc_open(struct inode *inode, struct file *file)
{
	return single_open(file, hc_show, inode->i_private);
}

static ssize_t hc_write(struct file *file, const char __user *ubuf,
			size_t len, loff_t *ppos)
{
	struct fman_pcd_hc *hc = ((struct seq_file *)file->private_data)->private;
	unsigned int a, b;
	char buf[48];
	int err;

	if (len >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, len))
		return -EFAULT;
	buf[len] = '\0';
	strim(buf);

	mutex_lock(&hc->lock);
	if (!strncmp(buf, "init", 4) && (!buf[4] || buf[4] == ' ')) {
		a = HC_DEFAULT_HWPORT;
		b = HC_DEFAULT_CHANNEL;
		sscanf(buf + 4, "%i %i", &a, &b);
		err = hc_init(hc, a, b);
	} else if (!strncmp(buf, "sync", 4) && (!buf[4] || buf[4] == ' ')) {
		a = 1;
		sscanf(buf + 4, "%u", &a);
		err = hc_sync(hc, a);
	} else if (!strcmp(buf, "fini")) {
		err = hc_fini(hc);
	} else if (!strcmp(buf, "clear")) {
		hc_clear(hc);
		err = 0;
	} else {
		err = -EINVAL;
	}
	mutex_unlock(&hc->lock);

	return err ? err : len;
}

static const struct file_operations hc_fops = {
	.owner = THIS_MODULE,
	.open = hc_open,
	.read = seq_read,
	.write = hc_write,
	.llseek = seq_lseek,
	.release = single_release,
};

void fman_pcd_hc_debugfs_init(struct dentry *parent, struct fman_pcd *pcd)
{
	struct fman_pcd_hc *hc;

	if (IS_ERR_OR_NULL(parent) || READ_ONCE(hc_state))
		return;

	hc = kzalloc(sizeof(*hc), GFP_KERNEL);
	if (!hc)
		return;
	mutex_init(&hc->lock);
	spin_lock_init(&hc->cb_lock);
	hc->pcd = pcd;
	hc->fman = fman_pcd_get_fman(pcd);
	hc->hwport = HC_DEFAULT_HWPORT;
	hc->chan = HC_DEFAULT_CHANNEL;
	smp_store_release(&hc_state, hc);

	debugfs_create_file("hc", 0600, parent, hc, &hc_fops);
}
