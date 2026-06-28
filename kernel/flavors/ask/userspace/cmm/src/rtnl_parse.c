/*
 * cmm_parse_rtattr - RTA walker shared between cmm proper and tests.
 *
 * Copyright (c) 2026 Mono Technologies Inc.
 *
 * SPDX-License-Identifier: GPL-2.0+
 */
#include <linux/rtnetlink.h>
#include <string.h>

#include "rtnl_parse.h"

int cmm_parse_rtattr(struct rtattr *tb[], int max, struct rtattr *rta, int len)
{
	memset(tb, 0, sizeof(struct rtattr *) * (max + 1));

	while (RTA_OK(rta, len)) {
		if (rta->rta_type <= max)
			tb[rta->rta_type] = rta;

		rta = RTA_NEXT(rta, len);
	}

	if (len)
		cmm_parse_rtattr_overflow(__func__, __LINE__, len);

	return 0;
}
