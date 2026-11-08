/* Host fault tests for F-291 parser and RX routing readback. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint32_t u32;
#define __iomem
#define HWP_HXS_COUNT 8
#define HWP_HXS_PCAC_PSTOP 2U
#define HWP_HXS_PCAC_PSTAT 1U
#define FMAN_PORT_TYPE_RX 1
#define NIA_ENG_HWK 0x00480000U
#define NIA_KG_CC_EN 0x00000200U
#define NIA_KG_DIRECT 0x00000100U
#define pr_err(...) ((void)0)
#define dev_err(...) ((void)0)

struct fman_port_hwp_regs {
	u32 fmpr_pcac;
	struct { u32 lcv; } pmda[HWP_HXS_COUNT];
};
struct bmi_regs { struct { u32 fmbm_rccb, fmbm_rfpne; } rx; };
struct fman_port {
	int port_type;
	void *dev;
	struct fman_port_hwp_regs *hwp_regs;
	struct bmi_regs *bmi_regs;
};

enum failure { NONE, STOP, LCV };
static enum failure failure;
static struct fman_port_hwp_regs hwp;
static unsigned int lcv_writes, delays;

static void iowrite32be(u32 value, u32 *address)
{
	if (address == &hwp.fmpr_pcac) {
		/* PSTAT means an active parse; this silicon never asserts it on restart. */
		*address = value == HWP_HXS_PCAC_PSTOP && failure == STOP ?
			   HWP_HXS_PCAC_PSTAT : 0;
	} else {
		lcv_writes++;
		*address = value;
	}
}
static u32 ioread32be(u32 *address)
{
	if (failure == LCV && address == &hwp.pmda[3].lcv)
		return 0;
	return *address;
}
static void udelay(unsigned int n) { delays++; }

#include "parser_body.h"

int main(void)
{
	struct bmi_regs bmi = { .rx.fmbm_rfpne = NIA_ENG_HWK };
	struct fman_port port = {
		.port_type = FMAN_PORT_TYPE_RX, .hwp_regs = &hwp, .bmi_regs = &bmi,
	};

	failure = STOP;
	assert(fman_port_clear_lcv_split(&port) == -ETIMEDOUT);
	assert(!lcv_writes && delays == 100);
	failure = LCV;
	delays = 0;
	assert(fman_port_clear_lcv_split(&port) == -EIO);
	assert(!hwp.fmpr_pcac && !delays);
	failure = NONE;
	lcv_writes = 0;
	assert(!fman_port_clear_lcv_split(&port));
	assert(lcv_writes == HWP_HXS_COUNT && !hwp.fmpr_pcac && !delays);

	assert(!fman_port_verify_fe_detached(&port));
	bmi.rx.fmbm_rccb = 0x100;
	assert(fman_port_verify_fe_detached(&port) == -EIO);
	bmi.rx.fmbm_rccb = 0;
	bmi.rx.fmbm_rfpne = NIA_ENG_HWK | NIA_KG_CC_EN;
	assert(fman_port_verify_fe_detached(&port) == -EIO);
	bmi.rx.fmbm_rfpne = NIA_ENG_HWK | NIA_KG_DIRECT;
	assert(fman_port_verify_fe_detached(&port) == -EIO);
	bmi.rx.fmbm_rfpne = 0x00500002U;
	assert(!fman_port_verify_fe_detached(&port));
	puts("RX disarm: parser stop timeout, restart and routing readback failures detected");
	return 0;
}
