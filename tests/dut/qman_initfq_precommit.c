// SPDX-License-Identifier: GPL-2.0
/* Host fault model for the extracted qman_init_fq() DMA path. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t dma_addr_t;

#define CONFIG_FSL_DPAA_CHECKING 1
#define DPAA_ASSERT(x) assert(x)
#define QMAN_FQ_FLAG_NO_MODIFY 0x02
#define QMAN_FQ_FLAG_TO_DCPORTAL 0x04
#define QMAN_FQ_STATE_CHANGING 0x100
#define QMAN_FQ_STATE_CGR_EN 0x200
#define QMAN_INITFQ_FLAG_SCHED 0x01
#define QMAN_INITFQ_FLAG_LOCAL 0x04
#define QM_INITFQ_WE_OAC 0x0100
#define QM_INITFQ_WE_CGID 0x0040
#define QM_INITFQ_WE_FQCTRL 0x0020
#define QM_INITFQ_WE_DESTWQ 0x0010
#define QM_INITFQ_WE_TDTHRESH 0x0004
#define QM_INITFQ_WE_CONTEXTB 0x0002
#define QM_INITFQ_WE_CONTEXTA 0x0001
#define QM_FQCTRL_CGE 0x0001
#define QM_MCC_VERB_INITFQ_PARKED 0x40
#define QM_MCC_VERB_INITFQ_SCHED 0x41
#define QM_MCR_VERB_MASK 0x7f
#define QM_MCR_RESULT_OK 0xf0
#define DMA_TO_DEVICE 0
#define cpu_to_be16(x) (x)
#define cpu_to_be32(x) (x)
#define be16_to_cpu(x) (x)
#define MAPPED_ADDR UINT64_C(0x123456789a)

enum qman_fq_state {
	qman_fq_state_oos,
	qman_fq_state_parked,
	qman_fq_state_sched,
};

struct qman_fq {
	u32 fqid, flags;
	enum qman_fq_state state;
	int cgr_groupid;
};
struct qm_fqd {
	u8 cgid;
	u16 fq_ctrl, dest_wq;
	u32 context_b;
	struct { dma_addr_t opaque; } context_a;
};
struct qm_mcc_initfq {
	u8 reserved[2];
	u16 we_mask;
	u32 fqid;
	u16 count;
	struct qm_fqd fqd;
};
struct qm_mcc_fq {
	u8 reserved[4];
	u32 fqid;
};
union qm_mc_command {
	struct qm_mcc_initfq initfq;
	struct qm_mcc_fq fq;
};
union qm_mc_result { struct { u8 verb, result; }; };
struct device { int unused; };
struct qm_portal { int unused; };
struct qm_portal_config { struct device *dev; u16 channel; };
struct qman_portal {
	struct qm_portal p;
	struct qm_portal_config *config;
};

static struct device device;
static struct qm_portal_config config = { .dev = &device, .channel = 9 };
static struct qman_portal portal = { .config = &config };
static struct qman_portal *qman_dma_portal = &portal;
static union qm_mc_command command;
static union qm_mc_result result;
static bool affine_held, mc_claimed, map_error;
static unsigned int maps, starts, commits, stashes, errors;

static struct qman_portal *get_affine_portal(void)
{
	assert(!affine_held);
	affine_held = true;
	return &portal;
}

static void put_affine_portal(void)
{
	assert(affine_held);
	affine_held = false;
}

static bool fq_isset(struct qman_fq *fq, u32 mask) { return fq->flags & mask; }
static bool fq_isclear(struct qman_fq *fq, u32 mask) { return !fq_isset(fq, mask); }
static void fq_set(struct qman_fq *fq, u32 mask) { fq->flags |= mask; }
static void fq_clear(struct qman_fq *fq, u32 mask) { fq->flags &= ~mask; }
static u32 fq_to_tag(struct qman_fq *fq) { return fq->fqid + 1; }

static dma_addr_t dma_map_single(struct device *dev, void *ptr, size_t size, int dir)
{
	assert(dev == &device && ptr && size == sizeof(struct qman_fq));
	assert(dir == DMA_TO_DEVICE && affine_held && !mc_claimed);
	maps++;
	return MAPPED_ADDR;
}

static bool dma_mapping_error(struct device *dev, dma_addr_t addr)
{
	assert(dev == &device && addr == MAPPED_ADDR);
	return map_error;
}

static void dev_err(struct device *dev, const char *format, ...)
{
	assert(dev == &device && format);
	errors++;
}

static union qm_mc_command *qm_mc_start(struct qm_portal *p)
{
	assert(p == &portal.p && affine_held && !mc_claimed);
	mc_claimed = true;
	starts++;
	memset(&command, 0, sizeof(command));
	return &command;
}

static void qm_fqid_set(struct qm_mcc_fq *fq, u32 fqid) { fq->fqid = fqid; }
static void qm_fqd_stashing_set64(struct qm_fqd *fqd, dma_addr_t addr)
{
	fqd->context_a.opaque = addr;
	stashes++;
}
static void qm_fqd_set_destwq(struct qm_fqd *fqd, int channel, int wq)
{
	fqd->dest_wq = (u16)(channel * 8 + wq);
}
static void qm_mc_commit(struct qm_portal *p, u8 verb)
{
	assert(p == &portal.p && mc_claimed);
	commits++;
	result.verb = verb;
	result.result = QM_MCR_RESULT_OK;
}
static int qm_mc_result_timeout(struct qm_portal *p, union qm_mc_result **mcr)
{
	assert(p == &portal.p && mc_claimed);
	*mcr = &result;
	mc_claimed = false;
	return 1;
}

#include "qman_initfq_body.h"

static void reset(void)
{
	memset(&command, 0, sizeof(command));
	memset(&result, 0, sizeof(result));
	affine_held = mc_claimed = map_error = false;
	maps = starts = commits = stashes = errors = 0;
}

int main(void)
{
	struct qman_fq fq = { .fqid = 7, .state = qman_fq_state_oos };
	struct qm_mcc_initfq opts = { .we_mask = QM_INITFQ_WE_CONTEXTA };

	reset();
	map_error = true;
	assert(qman_init_fq(&fq, QMAN_INITFQ_FLAG_SCHED, &opts) == -EIO);
	assert(maps == 1 && errors == 1 && !starts && !commits && !stashes);
	assert(!affine_held && !mc_claimed && fq.state == qman_fq_state_oos);
	map_error = false;
	assert(qman_init_fq(&fq, QMAN_INITFQ_FLAG_SCHED, &opts) == 0);
	assert(maps == 2 && starts == 1 && commits == 1 && stashes == 1);
	assert(command.initfq.fqd.context_a.opaque == MAPPED_ADDR);
	assert(command.initfq.fqd.context_b == fq.fqid + 1);
	assert(fq.state == qman_fq_state_sched && !affine_held && !mc_claimed);

	reset();
	fq.flags = QMAN_FQ_FLAG_TO_DCPORTAL;
	fq.state = qman_fq_state_oos;
	opts.fqd.context_a.opaque = 0x55;
	opts.fqd.context_b = 0x66;
	assert(qman_init_fq(&fq, 0, &opts) == 0);
	assert(!maps && !stashes && starts == 1 && commits == 1);
	assert(command.initfq.fqd.context_a.opaque == 0x55);
	assert(command.initfq.fqd.context_b == 0x66);

	reset();
	fq.flags = 0;
	fq.state = qman_fq_state_oos;
	opts.we_mask = 0;
	assert(qman_init_fq(&fq, 0, &opts) == 0);
	assert(!maps && !stashes && starts == 1 && commits == 1);
	assert(command.initfq.we_mask & QM_INITFQ_WE_CONTEXTA);
	assert(command.initfq.fqd.context_a.opaque == 0);

	reset();
	fq.state = qman_fq_state_oos;
	assert(qman_init_fq(&fq, 0, NULL) == 0);
	assert(!maps && !stashes && starts == 1 && commits == 1);
	assert(command.initfq.we_mask & QM_INITFQ_WE_CONTEXTA);
	assert(command.initfq.fqd.context_a.opaque == 0);

	reset();
	fq.flags = QMAN_FQ_STATE_CHANGING;
	fq.state = qman_fq_state_oos;
	opts.we_mask = QM_INITFQ_WE_CONTEXTA;
	assert(qman_init_fq(&fq, 0, &opts) == -EBUSY);
	assert(!maps && !starts && !commits && !affine_held);
	puts("QMan INITFQ DMA: failure, retry, mapped and unmapped paths pass");
	return 0;
}
