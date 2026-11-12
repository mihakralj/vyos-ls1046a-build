// SPDX-License-Identifier: GPL-2.0
/* Exercise the DPAA AF_XDP cookie map extracted from af_xdp_pool_main.c. */
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;

#define AFX_BUF_ALIGN	16
#define AFX_ADDR_LIMIT	(1ULL << 40)

#include "afx_map.h"

#define BITS	10
#define SLOTS	(1U << BITS)
#define FAIL(...) do { fprintf(stderr, __VA_ARGS__); exit(1); } while (0)

static void map_init(struct afx_map *m, u32 max_live)
{
	m->slot = calloc(SLOTS, sizeof(*m->slot));
	m->mask = SLOTS - 1;
	m->shift = 64 - BITS;
	m->live = 0;
	m->max_live = max_live;
}

/* Colliding keys: pick the ones whose home slot is the last one (wraparound). */
static u64 key_for_home(const struct afx_map *m, u32 home, unsigned int nth)
{
	u64 k;

	for (k = AFX_BUF_ALIGN; ; k += AFX_BUF_ALIGN)
		if (afx_home(m, k) == home && nth-- == 0)
			return k;
}

int main(void)
{
	struct afx_map m;
	static u64 keys[400];
	u32 i, round;

	/* Cookie validity. */
	if (afx_cookie_ok(0) || afx_cookie_ok(8) || afx_cookie_ok(AFX_ADDR_LIMIT) ||
	    !afx_cookie_ok(16) || !afx_cookie_ok(AFX_ADDR_LIMIT - 16))
		FAIL("afx_cookie_ok wrong\n");

	/* Basic insert, duplicate, erase, miss. */
	map_init(&m, 512);
	if (afx_map_insert(&m, 0x1000, (void *)1) ||
	    afx_map_insert(&m, 0x1000, (void *)2) != -EEXIST ||
	    m.live != 1)
		FAIL("insert/duplicate\n");
	if (afx_map_erase(&m, 0x2000) || afx_map_erase(&m, 0x1000) != (void *)1 ||
	    afx_map_erase(&m, 0x1000) || m.live)
		FAIL("erase/miss\n");

	/* Capacity. */
	free(m.slot);
	map_init(&m, 4);
	for (i = 0; i < 4; i++)
		if (afx_map_insert(&m, 0x100 + 0x10 * i, (void *)(uintptr_t)(i + 1)))
			FAIL("insert %u\n", i);
	if (afx_map_insert(&m, 0x900, (void *)9) != -ENOSPC)
		FAIL("full map accepted an insert\n");
	free(m.slot);

	/* Backshift deletion keeps every chain reachable, including a chain
	 * that wraps past the end of the table.  Erase in several orders.
	 */
	for (round = 0; round < 3; round++) {
		u32 n = 0, order[8];

		map_init(&m, 512);
		for (i = 0; i < 4; i++)
			keys[n++] = key_for_home(&m, SLOTS - 1, i);
		for (i = 0; i < 3; i++)
			keys[n++] = key_for_home(&m, 0, i);
		for (i = 0; i < n; i++)
			if (afx_map_insert(&m, keys[i], (void *)(uintptr_t)(i + 1)))
				FAIL("chain insert %u\n", i);
		for (i = 0; i < n; i++)
			order[i] = (round == 0) ? i : (round == 1) ? n - 1 - i
						 : (i * 3) % n;
		for (i = 0; i < n; i++) {
			u32 j, k = order[i];

			if (afx_map_erase(&m, keys[k]) != (void *)(uintptr_t)(k + 1))
				FAIL("round %u: erase of key %u lost\n", round, k);
			for (j = i + 1; j < n; j++) {
				u32 kk = order[j];

				if (!afx_map_erase(&m, keys[kk]))
					FAIL("round %u: key %u unreachable after erasing %u\n",
					     round, kk, k);
				if (afx_map_insert(&m, keys[kk], (void *)(uintptr_t)(kk + 1)))
					FAIL("reinsert\n");
			}
		}
		if (m.live)
			FAIL("round %u: live=%u\n", round, m.live);
		free(m.slot);
	}

	/* Random churn against a shadow array. */
	map_init(&m, 400);
	{
		static u8 present[400];
		srand(7);
		for (i = 0; i < 200000; i++) {
			u32 k = rand() % 400;
			u64 key = (u64)(k + 1) * 0x40;

			keys[k] = key;
			if (present[k]) {
				if (afx_map_erase(&m, key) != (void *)(uintptr_t)(k + 1))
					FAIL("churn: erase %u lost\n", k);
				present[k] = 0;
			} else {
				if (afx_map_insert(&m, key, (void *)(uintptr_t)(k + 1)))
					FAIL("churn: insert %u\n", k);
				present[k] = 1;
			}
		}
		for (i = 0; i < 400; i++)
			if (present[i] && !afx_map_erase(&m, keys[i]))
				FAIL("churn: final %u lost\n", i);
		if (m.live)
			FAIL("churn: live=%u\n", m.live);
	}
	free(m.slot);
	return 0;
}
