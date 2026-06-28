/*
 * Standalone declaration of cmm_parse_rtattr, extracted from rtnl.c so
 * userspace tests can link the production parser without dragging in cmm.
 *
 * Copyright (c) 2026 Mono Technologies Inc.
 *
 * SPDX-License-Identifier: GPL-2.0+
 */
#ifndef CMM_RTNL_PARSE_H
#define CMM_RTNL_PARSE_H

#include <linux/rtnetlink.h>

int cmm_parse_rtattr(struct rtattr *tb[], int max, struct rtattr *rta, int len);
void cmm_parse_rtattr_overflow(const char *func, int line, int len_remaining);

#endif /* CMM_RTNL_PARSE_H */
