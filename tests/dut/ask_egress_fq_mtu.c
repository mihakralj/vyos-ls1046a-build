// SPDX-License-Identifier: GPL-2.0
/* Host ASan/UBSan test for MTU-aware per-flow egress FQ selection in ask_hw.c. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;

#define ETH_DATA_LEN 1500U
#define ASK_HW_NOCONF_SLOTS 16
#define ASK_HW_FLOW_FQS 16
#define READ_ONCE(x) (x)
#define smp_load_acquire(p) (*(p))
#define lockdep_assert_held(p) ((void)(p))
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
static int warn_hits;
#define WARN_ON_ONCE(cond) ({ bool __c = !!(cond); if (__c) warn_hits++; __c; })

struct net_device {
	unsigned int mtu;
};

struct ask_hw_pcd {
	int lock;
	struct {
		struct net_device *dev;
		u32 ifindex;
		u32 fqid;
		u32 legacy_users;
		u32 l2_users;
		u32 nflow;
		u32 flow_fqid[ASK_HW_FLOW_FQS];
		u32 users[ASK_HW_FLOW_FQS];
	} noconf_tx[ASK_HW_NOCONF_SLOTS];
};

static unsigned int ask_egress_fqs = ASK_HW_FLOW_FQS;
#ifdef HAVE_EGRESS_FQ_MIN_MTU
static unsigned int ask_egress_fq_min_mtu = ETH_DATA_LEN;
#endif

#include "ask_flow_fq_body.h"

int main(void)
{
	struct ask_hw_pcd h;
	struct net_device dev = { .mtu = 1500 };
	const int slot = 5;
	const u32 legacy = 0x200;
	u32 fqs[6];

	memset(&h, 0, sizeof(h));
	h.noconf_tx[slot].dev = &dev;
	h.noconf_tx[slot].ifindex = 5;
	h.noconf_tx[slot].fqid = legacy;
	h.noconf_tx[slot].nflow = ASK_HW_FLOW_FQS;
	for (unsigned int i = 0; i < ASK_HW_FLOW_FQS; i++)
		h.noconf_tx[slot].flow_fqid[i] = 0x300 + i;

	/* 1. Default 1500 MTU uses single legacy FQ (no 16-FQ DRR GRO split). */
	fqs[0] = ask_hw_flow_fq_get(&h, slot, legacy);
	fqs[1] = ask_hw_flow_fq_get(&h, slot, legacy);
	assert(fqs[0] == legacy);
	assert(fqs[1] == legacy);
	assert(h.noconf_tx[slot].legacy_users == 2);
	assert(h.noconf_tx[slot].users[0] == 0);

	/* 2. Raising port MTU to 9000 selects per-flow FQ set (least-used). */
	dev.mtu = 9000;
	fqs[2] = ask_hw_flow_fq_get(&h, slot, legacy);
	fqs[3] = ask_hw_flow_fq_get(&h, slot, legacy);
	assert(fqs[2] == 0x300);
	assert(fqs[3] == 0x301);
	assert(h.noconf_tx[slot].users[0] == 1);
	assert(h.noconf_tx[slot].users[1] == 1);

	/* 3. egress_fqs=0 forces legacy FQ even at jumbo MTU. */
	ask_egress_fqs = 0;
	fqs[4] = ask_hw_flow_fq_get(&h, slot, legacy);
	assert(fqs[4] == legacy);
	assert(h.noconf_tx[slot].legacy_users == 3);
	ask_egress_fqs = ASK_HW_FLOW_FQS;

#ifdef HAVE_EGRESS_FQ_MIN_MTU
	/* 4. egress_fq_min_mtu=0 forces per-flow FQ set even at 1500 MTU. */
	dev.mtu = 1500;
	ask_egress_fq_min_mtu = 0;
	fqs[5] = ask_hw_flow_fq_get(&h, slot, legacy);
	assert(fqs[5] == 0x302);
	assert(h.noconf_tx[slot].users[2] == 1);
	ask_egress_fq_min_mtu = ETH_DATA_LEN;
#else
	fqs[5] = ask_hw_flow_fq_get(&h, slot, legacy);
#endif

	/* 5. Mixed legacy + set FQ teardown leaves zero users and zero warnings. */
	for (int i = 0; i < 6; i++)
		ask_hw_flow_fq_put(&h, fqs[i]);
	assert(h.noconf_tx[slot].legacy_users == 0);
	for (unsigned int i = 0; i < ASK_HW_FLOW_FQS; i++)
		assert(h.noconf_tx[slot].users[i] == 0);
	assert(warn_hits == 0);

	puts("ask_egress_fq_mtu: ok");
	return 0;
}
